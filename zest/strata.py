"""Strata: a second engine, for models too big for a graphics card (experts split between GPU, RAM and SSD).

Strata (github.com/Niko1221/Strata) installs its own `strata` command. Zest drives that command and never touches
Strata's files: `strata launch --status/--list-presets --output json` say what is saved and running, `strata launch
--preset NAME --yes --no-tui` starts one, `strata launch --stop` stops it. A running Strata is an OpenAI-style server
on localhost, so Pi talks to it directly. Zest never passes --restart: a Strata the person started themselves keeps
running. Standard library only.
"""
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

import hardware
import store

REPO = "Niko1221/Strata"
PREFIX = "strata/"                       # in front of a Strata model's name wherever Zest lists models next to Lemonade's
UPD = {"state": "idle", "error": None, "log": []}         # installing the Terminal
RUN = {"state": "idle", "preset": None, "error": None, "log": [], "since": 0}   # starting a preset
before_launch = []                       # callables run just before Strata starts loading a model (Lemonade's models are freed: shared memory)
on_change = []                           # callables run after Strata starts or stops (idle chats re-read the model list)
_lock = threading.Lock()


def _before_launch():
    for f in before_launch:
        try:
            f()
        except Exception:  # noqa: BLE001
            pass


def _changed():
    _cache["v"] = None
    for f in on_change:
        try:
            f()
        except Exception:  # noqa: BLE001
            pass


def binary():
    for cand in (os.environ.get("ZEST_STRATA"), shutil.which("strata"), os.path.expanduser("~/.local/bin/strata")):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def is_model(name):
    return str(name or "").startswith(PREFIX)


def bare(name):
    return str(name)[len(PREFIX):] if is_model(name) else str(name)


def _run(args, timeout=20):
    exe = binary()
    if not exe:
        return 127, ""
    try:
        r = subprocess.run([exe] + args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout or ""
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def _json(args, timeout=20):
    code, out = _run(args, timeout)
    try:
        return json.JSONDecoder().raw_decode(out.lstrip())[0]      # some commands print a plain line after the JSON
    except ValueError:
        return {}


_cache = {"t": 0, "v": None}


def version():
    code, out = _run(["--version"], 15)
    m = re.search(r"strata\s+(\d[\w.]*)", out) if code == 0 else None
    return m.group(1) if m else ""


PROBE_PORTS = range(8080, 8090)          # Strata's default is 8080; a second server takes the next free one


ZEST_PRESET = "Zest "                    # presets Zest made are named "Zest <model>"; only those start on demand


def _settings():
    st = store.load().setdefault("strata", {})
    for k, v in (("remote", []), ("keys", {}), ("owned", {"preset": "", "port": 0}), ("known", {})):
        st.setdefault(k, v)
    return st


def _save(mutate):
    s = store.load()
    st = s.setdefault("strata", {})
    for k, v in (("remote", []), ("keys", {}), ("owned", {"preset": "", "port": 0}), ("known", {})):
        st.setdefault(k, v)
    mutate(st)
    store.save(s)


def _http_json(url, key="", timeout=3):
    h = {"Authorization": "Bearer " + key, "x-api-key": key} if key else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, {}
    except (OSError, ValueError):
        return 0, {}


def _health(url):
    """/health of a Strata server, or None when what answers isn't one."""
    status, h = _http_json(url + "/health", timeout=1.5)
    return h if status == 200 and h.get("service") == "strata" else None


def _show_key(url):
    """The key of a server `strata launch` manages. Empty when there isn't one or the command doesn't say."""
    code, out = _run(["launch", "--show-key"], 10)
    words = re.findall(r"[A-Za-z0-9_\-]{16,}", out) if code == 0 else []
    return words[-1] if words else ""


def running():
    """Every Strata server Zest can see, cached for a few seconds (the chat bar and every new message ask).

    [{url, port, name, state, source, owner, needs_key, key, models:[{id, ctx, vision}]}]
    source: launch (strata launch manages it), probe (found answering on a local port), saved (an address typed in).
    owner: "zest" if Zest started it, otherwise "yours". Zest only ever acts by itself on its own."""
    now = time.time()
    if _cache["v"] is None or now - _cache["t"] > 3:
        _cache.update(t=now, v=_discover())
    return _cache["v"]


def _discover():
    st = _settings()
    found = {}
    for r in _json(["launch", "--status", "--output", "json"], 15).get("running", []):
        if r.get("url"):
            found[r["url"].rstrip("/")] = {"name": r.get("name"), "port": r.get("port"), "state": r.get("state"), "source": "launch"}
    probe = [u for u in ("http://127.0.0.1:%d" % p for p in PROBE_PORTS) if u not in found]
    for u in probe:
        h = _health(u)
        if h:
            found[u] = {"name": h.get("model") or "Strata", "port": int(u.rsplit(":", 1)[1]), "state": "ready" if h.get("loaded") else "loading", "source": "probe"}
    for r in st["remote"]:
        u = str(r.get("url", "")).rstrip("/")
        if u and u not in found:
            h = _health(u)
            found[u] = {"name": (h or {}).get("model") or u, "port": 0, "state": ("ready" if h.get("loaded") else "loading") if h else "unreachable", "source": "saved"}
    owned = st["owned"].get("port")
    out = []
    for u, r in sorted(found.items()):
        key = st["keys"].get(u, "") or (_show_key(u) if r["source"] == "launch" else "")
        status, data = _http_json(u + "/v1/models", key) if r["state"] == "ready" else (0, {})
        models = []
        for m in data.get("data", []):
            mods = (m.get("architecture") or {}).get("input_modalities") or []
            if m.get("id"):
                models.append({"id": m["id"], "ctx": int((m.get("meta") or {}).get("n_ctx") or 0), "vision": "image" in mods})
        out.append(dict(r, url=u, key=key, needs_key=status in (401, 403), models=models,
                        owner="zest" if owned and r["port"] == owned else "yours"))
    if owned and not any(r["owner"] == "zest" for r in out):
        _save(lambda s: s["owned"].update(preset="", port=0))       # the one Zest started has gone: forget it
    return out


def presets():
    return [{"name": p.get("name"), "family": p.get("family"), "model": p.get("model"), "context": p.get("context"),
             "vision": p.get("vision"), "port": p.get("port"), "last_used": p.get("last_used")}
            for p in _json(["launch", "--list-presets", "--output", "json"], 15).get("presets", []) if p.get("name")]


def _provider(url):
    """Pi's name for the server at `url`: stable, so a model keeps its provider when other servers come and go."""
    m = re.search(r"://([^/]+)", url)
    return "strata-" + re.sub(r"[^\w]+", "-", m.group(1) if m else url)


_pc = {"t": 0, "v": []}


def _presets_cached():
    if time.time() - _pc["t"] > 10:
        _pc.update(t=time.time(), v=presets())
    return _pc["v"]


def models():
    """Chat models Zest can offer: {id, url, key, ctx, vision, provider, ...}, id with Zest's prefix. Pi gets one provider per server.

    Models of every ready Strata server, and `dormant` ones: a setup Zest made earlier that isn't running right now. A
    dormant model starts when a chat first needs it (see ensure)."""
    out = []
    live = [r for r in running() if r["state"] == "ready" and not r["needs_key"]]
    for r in live:
        for m in r["models"]:
            out.append({"id": PREFIX + m["id"], "tools": True, "reasoning": True, "vision": m["vision"], "ctx": m["ctx"] or 32768,
                        "provider": _provider(r["url"]), "url": r["url"], "key": r["key"], "bare": m["id"]})
    have = {m["id"] for m in out}
    ports = {r["port"] for r in running() if r["state"] in ("ready", "loading")}
    if binary():
        mine = {p["name"]: p for p in _presets_cached()}
        for name, k in _settings()["known"].items():
            p = mine.get(name)
            if not p or not name.startswith(ZEST_PRESET) or PREFIX + k["id"] in have or int(p.get("port") or 0) in ports:
                continue
            url = "http://127.0.0.1:%d" % int(p["port"])
            out.append({"id": PREFIX + k["id"], "tools": True, "reasoning": True, "vision": bool(k.get("vision")), "ctx": int(k.get("ctx") or 131072),
                        "provider": _provider(url), "url": url, "key": "", "bare": k["id"], "dormant": True, "preset": name})
    return out


def find_model(name):
    return next((m for m in models() if m["id"] == name), None)


def is_live(name):
    m = find_model(name)
    return bool(m) and not m.get("dormant")


def summary():
    exe = binary()
    s = {"installed": bool(exe), "version": version() if exe else "", "presets": [], "run": dict(RUN), "install": dict(UPD),
         "command": "strata launch", "servers": [{k: v for k, v in r.items() if k != "key"} for r in running()]}
    if exe:
        s["presets"] = presets()
    return s


def connect(url, key=""):
    """Remember a Strata server at `url` (and its key). Returns (ok, message)."""
    url = str(url or "").strip().rstrip("/")
    if not re.match(r"^https?://[\w.\-\[\]:]+(/.*)?$", url):
        return False, "Type an address like http://192.168.1.20:8080."
    h = _health(url)
    if not h:
        return False, "Nothing that looks like Strata answered at %s." % url

    def add(s):
        s["remote"] = [r for r in s["remote"] if r.get("url") != url] + [{"url": url}]
        if key:
            s["keys"][url] = key
    _save(add)
    _changed()
    return True, ""


def forget(url):
    url = str(url or "").rstrip("/")
    _save(lambda s: (s.update(remote=[r for r in s["remote"] if r.get("url") != url]), s["keys"].pop(url, None)))
    _changed()


def set_key(url, key):
    url = str(url or "").rstrip("/")
    _save(lambda s: s["keys"].__setitem__(url, str(key or "").strip()))
    _changed()


# ---------------------------------------------------------------- starting and stopping
def _active(state):
    return state in ("starting",)


def start(preset):
    """Start a saved preset in the background. Returns False when one is already starting. Never restarts a running model."""
    with _lock:
        if _active(RUN["state"]):
            return False
        if not binary():
            RUN.update(state="error", preset=preset, error="Strata isn't installed.", log=[])
            return False
        mine = next((p for p in presets() if p["name"] == preset), None)
        if not mine:
            RUN.update(state="error", preset=preset, error="Strata has no saved preset called “%s”." % preset, log=[])
            return False
        other = next((r for r in running() if r["state"] in ("ready", "loading") and r["owner"] == "yours" and r["source"] != "saved"), None)
        if other:
            RUN.update(state="error", preset=preset, log=[], error="Strata is already running %s on this computer. Zest won't start a second big model beside it: chat with that one, or stop it first." % (other["name"] or "a model"))
            return False
        RUN.update(state="starting", preset=preset, error=None, log=[], since=time.time())
    threading.Thread(target=_do_start, args=(preset, mine.get("port")), daemon=True).start()
    return True


def _do_start(preset, port):
    try:
        _before_launch()
        for r in running():                          # Zest's own earlier one gives way; one big model at a time
            if r["owner"] == "zest" and r["source"] == "launch":
                RUN["log"] = ["Stopping the Strata Zest started earlier…"]
                stop(r["url"])
        p = subprocess.Popen([binary(), "launch", "--preset", preset, "--yes", "--no-tui"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        for line in p.stdout:
            RUN["log"] = (RUN["log"] + [line.rstrip()])[-40:]
        code = p.wait()
        if code != 0:
            tail = [l for l in RUN["log"] if l.strip()][-2:]
            raise RuntimeError(" ".join(tail) or "Strata stopped with code %d." % code)
        if port:
            _save(lambda st: st["owned"].update(preset=preset, port=int(port)))
            _changed()
            for _ in range(20):                      # the server may need a moment to list its model
                r = next((r for r in running() if r["port"] == int(port) and r["models"]), None)
                if r:
                    m = r["models"][0]
                    _save(lambda st: st["known"].__setitem__(preset, {"id": m["id"], "ctx": m["ctx"], "vision": m["vision"]}))
                    break
                time.sleep(0.5)
                _cache["v"] = None
        RUN["state"] = "ready"
        _changed()
    except Exception as e:  # noqa: BLE001
        RUN.update(state="error", error=str(e) or e.__class__.__name__)


def stop(url=None):
    """Stop a Strata that `strata launch` manages (the only kind it can stop). Returns (ok, message)."""
    target = next((r for r in running() if url is None or r["url"] == str(url).rstrip("/")), None)
    if target and target["source"] != "launch":
        return False, "Zest can't stop that one, because it wasn't started with `strata launch`. Stop it where you started it."
    code, out = _run(["launch", "--stop"], 60)
    RUN.update(state="idle", error=None)
    _save(lambda s: s["owned"].update(preset="", port=0))
    _changed()
    return code == 0, "" if code == 0 else (out.strip().splitlines() or ["Strata didn't stop."])[-1]


# ---------------------------------------------------------------- installing the Terminal
def _get(url, limit=2_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": "Zest"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(limit + 1)


def install():
    """Download Strata's installer from its latest release, check it against the release's SHA256SUMS.txt, and run it."""
    with _lock:
        if UPD["state"] == "installing":
            return False
        UPD.update(state="installing", error=None, log=[])
    threading.Thread(target=_do_install, daemon=True).start()
    return True


def _install_blocking():
    """Download Strata's installer, check it against the release's SHA256SUMS.txt, run it. Raises RuntimeError on any problem."""
    base = "https://github.com/%s/releases/latest/download/" % REPO
    try:
        script = _get(base + "install.sh")
        sums = _get(base + "SHA256SUMS.txt").decode("utf8", "replace")
        want = next((l.split()[0] for l in sums.splitlines() if l.split()[-1:] == ["install.sh"]), "")
        if not want or hashlib.sha256(script).hexdigest() != want.lower():
            raise RuntimeError("The installer didn't match the checksum Strata publishes, so Zest didn't run it.")
        d = os.path.join(store.data_dir(), "strata-installer")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "install.sh")
        with open(path, "wb") as f:
            f.write(script)
        p = subprocess.Popen(["sh", path], cwd=d, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in p.stdout:
            UPD["log"] = (UPD["log"] + [line.rstrip()])[-40:]
        if p.wait() != 0:
            raise RuntimeError("Strata's installer failed: " + " ".join([l for l in UPD["log"] if l.strip()][-2:]))
        if not binary():
            raise RuntimeError("Strata installed, but Zest can't find the `strata` command. Open a new terminal, or add ~/.local/bin to your PATH.")
    except urllib.error.URLError as e:
        raise RuntimeError("Couldn't reach GitHub (%s)." % getattr(e, "reason", e))


def _do_install():
    try:
        _install_blocking()
        UPD["state"] = "done"
    except Exception as e:  # noqa: BLE001
        UPD.update(state="error", error=str(e) or e.__class__.__name__)


# ---------------------------------------------------------------- what fits this computer
# Qwen3.8-Flash-Next from ISTA-DASLab, Strata's recommended provider (docs/MODELS.md "Pick by memory"): the memory the weights
# need in RAM plus VRAM, best quality first. DeepSeek V4.1 is listed but never recommended: 257 GB of disk and a fixed 8K window.
CATALOG = [
    {"family": "qwen", "model": "IQ3_S", "title": "Qwen3.8 Flash-Next, best quality", "need_gb": 54.8, "ctx": 131072},
    {"family": "qwen", "model": "IQ3_XXS", "title": "Qwen3.8 Flash-Next, great quality", "need_gb": 47.0, "ctx": 131072},
    {"family": "qwen", "model": "IQ2_XS", "title": "Qwen3.8 Flash-Next, balanced", "need_gb": 39.2, "ctx": 131072},
    {"family": "qwen", "model": "Q2_0", "title": "Qwen3.8 Flash-Next, fastest", "need_gb": 37.6, "ctx": 131072},
    {"family": "deepseek41", "model": "IST-DAS-2.00bpw", "title": "DeepSeek V4.1 Flash (very large, 8K window)", "need_gb": 46.0, "ctx": 8192,
     "min_vram_gb": 12, "disk_gb": 270},
]


def _ram_gb():
    try:
        with open("/proc/meminfo") as f:
            return int(re.search(r"MemTotal:\s+(\d+)", f.read()).group(1)) / 1048576
    except (OSError, AttributeError, ValueError):
        return 0.0


def machine():
    """RAM, graphics memory and free disk, the way the fit rule counts them. Memory the GPU shares with the system counts once."""
    ram = _ram_gb()
    gpus = []
    for g in hardware.all_gpus():
        gb = (g.get("mem_total_mb") or 0) / 1024
        if gb:
            gpus.append({"name": g.get("name"), "gb": gb, "vendor": g.get("vendor")})
    shared = lambda g: g["vendor"] != "NVIDIA" and g["gb"] >= ram * 0.4          # an APU's "VRAM" is system memory
    vram = sum(g["gb"] for g in gpus if not shared(g))
    where = os.path.expanduser("~/.local/share")
    os.makedirs(where, exist_ok=True)
    return {"ram_gb": round(ram, 1), "vram_gb": round(vram, 1), "best_gpu_gb": round(max([g["gb"] for g in gpus if not shared(g)] or [0]), 1),
            "gpus": [g["name"] for g in gpus], "free_disk_gb": round(shutil.disk_usage(where).free / 1e9, 1)}


def _plan(entry, ctx):
    """strata launch --plan for one catalog entry: its download size and warnings, or {} before Strata is installed."""
    if not binary():
        return {}
    args = ["launch", "--plan", "--family", entry["family"], "--model", entry["model"], "--output", "json"]
    if entry["family"] == "qwen":
        args += ["--context", str(ctx)]
    p = _json(args, 20).get("plan") or {}
    return {"download_gb": (p.get("download") or {}).get("gb"), "warnings": p.get("warnings") or []}


def recommend(ctx=131072):
    """The catalog with a fit for each entry on this computer, and which one to suggest.

    fit: "fits" (room to spare), "tight" (should run; close other big programs), "no" (won't). The suggestion is the best
    quality that fits; when nothing does there is none, and the reason says why."""
    m = machine()
    budget = m["ram_gb"] + m["vram_gb"]
    # An install that already holds models keeps them wherever its owner chose, which Strata doesn't report; don't judge its disk.
    adopted = bool(binary() and _json(["models", "list", "--installed", "--output", "json"], 15).get("models"))
    out = []
    for e in CATALOG:
        why = ""
        if not m["gpus"]:
            fit, why = "no", "Strata needs a graphics card."
        elif e.get("min_vram_gb") and m["best_gpu_gb"] < e["min_vram_gb"]:
            fit, why = "no", "Needs a graphics card with %d GB or more." % e["min_vram_gb"]
        else:
            roomy, tight = budget * 0.85 - 6, budget * 0.95 - 2
            fit = "fits" if e["need_gb"] <= roomy else "tight" if e["need_gb"] <= tight else "no"
            if fit == "no":
                why = "Needs about %d GB of RAM plus graphics memory; this computer has %d GB." % (e["need_gb"], budget)
        plan = _plan(e, e["ctx"]) if fit != "no" else {}
        dl = plan.get("download_gb")
        if fit != "no" and not adopted and dl is not None and dl + 10 > m["free_disk_gb"]:
            fit, why = "no", "Needs about %d GB of free disk for the download; %d GB is free." % (dl, m["free_disk_gb"])
        out.append(dict(e, fit=fit, why=why, download_gb=dl, warnings=plan.get("warnings", [])))
    pick = next((o for o in out if o["fit"] == "fits" and o["family"] == "qwen"), None) or next((o for o in out if o["fit"] == "tight" and o["family"] == "qwen"), None)
    return {"machine": m, "adopted": adopted, "options": out, "pick": pick["model"] if pick else None}


# ---------------------------------------------------------------- one step: install, download, save, start
def _free_port():
    for p in PROBE_PORTS:
        try:
            socket.create_connection(("127.0.0.1", p), timeout=0.3).close()
        except OSError:
            return p                                                  # nothing there: free, and inside the range Zest looks in
    return None


def setup(family, model):
    """Install Strata if it is missing, then download `model`, save it as the preset "Zest <model>" with the context Zest
    uses (128k where the model allows) and start it. Runs in the background; progress is in RUN. Never touches a running Strata."""
    entry = next((e for e in CATALOG if e["family"] == family and e["model"] == model), None)
    if not entry:
        RUN.update(state="error", preset=None, error="Zest doesn't know that model.", log=[])
        return False
    with _lock:
        if _active(RUN["state"]) or UPD["state"] == "installing":
            return False
        other = next((r for r in running() if r["state"] in ("ready", "loading") and r["source"] != "saved"), None)
        if other:
            RUN.update(state="error", preset=None, log=[], error="Strata is already running %s on this computer. Zest won't start a second big model beside it: use that one, or stop it first." % (other["name"] or "a model"))
            return False
        RUN.update(state="starting", preset=ZEST_PRESET + model, error=None, log=[], since=time.time(), phase="")
    threading.Thread(target=_do_setup, args=(entry,), daemon=True).start()
    return True


def _do_setup(entry):
    name = ZEST_PRESET + entry["model"]
    try:
        if not binary():
            RUN["phase"] = "Installing Strata…"
            _install_blocking()
        port = _free_port()
        if not port:
            raise RuntimeError("Ports 8080 to 8089 are all in use, so Zest has nowhere to start Strata.")
        args = [binary(), "launch", "--family", entry["family"], "--model", entry["model"], "--access", "local", "--port", str(port),
                "--save-preset", name, "--yes", "--no-tui"]
        if entry["family"] == "qwen":
            args += ["--context", str(entry["ctx"])]
        _before_launch()
        RUN["phase"] = "Downloading and starting %s… the first time this can take a long while." % entry["model"]
        p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        for line in p.stdout:
            RUN["log"] = (RUN["log"] + [line.rstrip()])[-40:]
        if p.wait() != 0:
            tail = [l for l in RUN["log"] if l.strip()][-2:]
            raise RuntimeError(" ".join(tail) or "Strata stopped with code %d." % p.returncode)
        _pc["t"] = 0
        _save(lambda st: st["owned"].update(preset=name, port=port))
        _changed()
        for _ in range(40):
            r = next((r for r in running() if r["port"] == port and r["models"]), None)
            if r:
                m = r["models"][0]
                _save(lambda st: st["known"].__setitem__(name, {"id": m["id"], "ctx": m["ctx"], "vision": m["vision"]}))
                break
            time.sleep(0.5)
            _cache["v"] = None
        RUN["state"] = "ready"
        _changed()
    except Exception as e:  # noqa: BLE001
        RUN.update(state="error", error=str(e) or e.__class__.__name__)


def ensure(model, on_line=None):
    """Make sure the Strata model `model` is serving, starting the Zest-made setup behind a dormant one. Waits until it
    answers (the first start loads tens of GB). Raises ValueError with something a person can act on."""
    m = find_model(model)
    if m and not m.get("dormant"):
        return
    if not m:
        raise ValueError("Strata isn't running that model. Start it from the Models page.")
    deadline = time.time() + 1800
    if not start(m["preset"]):
        if RUN["state"] == "error":
            raise ValueError(RUN["error"])
    while time.time() < deadline:                  # started by us just now, or by another chat a moment ago
        if is_live(model):
            return
        if RUN["state"] == "error":
            raise ValueError(RUN["error"] or "Strata didn't start.")
        if on_line and RUN["log"]:
            on_line(RUN["log"][-1])
        time.sleep(1)
        _cache["v"] = None
    raise ValueError("Strata is taking too long to start. Look at the Models page.")
