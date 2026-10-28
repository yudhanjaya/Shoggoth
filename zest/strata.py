"""Strata: a second engine, for models too big for a graphics card (experts split between GPU, RAM and SSD).

Strata (github.com/Niko1221/Strata) installs its own `strata` command. Zest drives that command and never touches
Strata's files: `strata launch --status/--list-presets --output json` say what is saved and running, `strata launch
--preset NAME --yes --no-tui` starts one, `strata launch --stop` stops it. A running Strata is an OpenAI-style server
on localhost, so Pi talks to it directly. Zest never passes --restart: a Strata the person started themselves keeps
running. Standard library only.
"""
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import signal
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
RUN = {"state": "idle", "preset": None, "error": None, "log": [], "since": 0, "low_memory": False, "retry": None}   # starting a preset
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
    for k, v in (("remote", []), ("keys", {}), ("owned", {"preset": "", "port": 0}), ("known", {}), ("idle_minutes", 20)):
        st.setdefault(k, v)
    return st


def _save(mutate):
    s = store.load()
    st = s.setdefault("strata", {})
    for k, v in (("remote", []), ("keys", {}), ("owned", {"preset": "", "port": 0}), ("known", {}), ("idle_minutes", 20)):
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
            found[r["url"].rstrip("/")] = {"name": r.get("name"), "profile": r.get("profile"), "port": r.get("port"), "state": r.get("state"), "source": "launch"}
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
         "command": "strata launch", "idle_minutes": _settings()["idle_minutes"], "servers": [{k: v for k, v in r.items() if k != "key"} for r in running()]}
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


def start(preset, force=False):
    """Start a saved preset in the background. Returns False when one is already starting. Never restarts a running model.
    `force` skips the free-memory check (the person pressed Start anyway)."""
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
        RUN.update(state="starting", preset=preset, error=None, log=[], since=time.time(), low_memory=False, retry={"kind": "start", "preset": preset})
    threading.Thread(target=_do_start, args=(preset, mine.get("port"), force, mine.get("model")), daemon=True).start()
    return True


# ---------------------------------------------------------------- memory
LOW_MEMORY = 0.6                        # refuse when free RAM + free graphics memory is under this share of what the model needs


def _meminfo_gb(key):
    try:
        with open("/proc/meminfo") as f:
            return int(re.search(key + r":\s+(\d+)", f.read()).group(1)) / 1048576
    except (OSError, AttributeError, ValueError):
        return 0.0


def _top_memory(n=3):
    """The programs using the most RAM right now, as "name (N GB)" strings, so a refusal can say what to close."""
    rows = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/status" % pid) as f:
                t = f.read()
            kb = int(re.search(r"VmRSS:\s+(\d+)", t).group(1))
            rows.append((kb, re.search(r"Name:\s+(\S+)", t).group(1)))
        except (OSError, AttributeError, ValueError):
            continue
    best = {}
    for kb, name in rows:
        best[name] = best.get(name, 0) + kb
    return ["%s (%d GB)" % (k, round(v / 1048576)) for k, v in sorted(best.items(), key=lambda kv: -kv[1])[:n] if v > 1048576 * 1.5]


def free_memory():
    """{ram_gb, vram_gb}: what is free right now. MemAvailable counts cache the system can give back."""
    vram = 0.0
    for g in hardware.nvidia_gpus():
        vram += max(0.0, (g.get("mem_total_mb") or 0) - (g.get("mem_used_mb") or 0)) / 1024
    return {"ram_gb": round(_meminfo_gb("MemAvailable"), 1), "vram_gb": round(vram, 1)}


def need_gb(model):
    """What a catalog model needs in RAM plus graphics memory; None for one Zest has no figure for (a setup of your own)."""
    return next((e["need_gb"] for e in CATALOG if e["model"].lower() == str(model or "").lower()), None)


def check_memory(model):
    """Raises LowMemory when starting `model` now would more than likely run the computer out of memory."""
    need = need_gb(model)
    if need is None:
        return
    f = free_memory()
    have = f["ram_gb"] + f["vram_gb"]
    if have < need * LOW_MEMORY:
        top = _top_memory()
        raise LowMemory("There isn't enough free memory to start %s: it needs about %d GB of RAM plus graphics memory, and %d GB is free (%d GB RAM, %d GB graphics).%s"
                        % (model, need, have, f["ram_gb"], f["vram_gb"], " Close some of these first: %s." % ", ".join(top) if top else ""))


class LowMemory(RuntimeError):
    pass


def _do_start(preset, port, force=False, model=None):
    try:
        _before_launch()
        for r in running():                          # Zest's own earlier one gives way; one big model at a time
            if r["owner"] == "zest" and r["source"] == "launch":
                RUN["log"] = ["Stopping the Strata Zest started earlier…"]
                stop(r["url"])
                time.sleep(3)                        # let the memory come back before measuring it
        if not force:
            check_memory(model)
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
    except LowMemory as e:
        RUN.update(state="error", error=str(e), low_memory=True)
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


def machine():
    return hardware.machine()


def _plan(entry, ctx):
    """strata launch --plan for one catalog entry: its download size and warnings, or {} before Strata is installed."""
    if not binary():
        return {}
    args = ["launch", "--plan", "--family", entry["family"], "--model", entry["model"], "--output", "json"]
    if entry["family"] == "qwen":
        args += ["--context", str(ctx)]
    p = _json(args, 20).get("plan") or {}
    return {"download_gb": (p.get("download") or {}).get("gb"), "warnings": p.get("warnings") or []}


_rc = {"t": 0, "v": None}


def recommend(ctx=131072, max_age=300):
    """The catalog with a fit for each entry on this computer, and which one to suggest.

    fit: "fits" (room to spare), "tight" (should run; close other big programs), "no" (won't). The suggestion is the best
    quality that fits; when nothing does there is none, and the reason says why. The plans are asked for in parallel
    (each is a slow call while a model is running) and the answer is kept for `max_age` seconds."""
    if max_age and _rc["v"] is not None and time.time() - _rc["t"] < max_age:
        return _rc["v"]
    m = machine()
    budget = m["ram_gb"] + m["vram_gb"]
    # An install that already holds models keeps them wherever its owner chose, which Strata doesn't report; don't judge its disk.
    adopted = bool(binary() and _json(["models", "list", "--installed", "--output", "json"], 30).get("models"))
    fits = []
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
        fits.append((fit, why))
    plans = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(CATALOG)) as pool:
        futs = {e["model"]: pool.submit(_plan, e, e["ctx"]) for e, (fit, _) in zip(CATALOG, fits) if fit != "no"}
        for k, f in futs.items():
            try:
                plans[k] = f.result(timeout=90)
            except Exception:  # noqa: BLE001
                plans[k] = {}
    out = []
    for e, (fit, why) in zip(CATALOG, fits):
        plan = plans.get(e["model"], {})
        dl = plan.get("download_gb")
        if fit != "no" and not adopted and dl is not None and dl + 10 > m["free_disk_gb"]:
            fit, why = "no", "Needs about %d GB of free disk for the download; %d GB is free." % (dl, m["free_disk_gb"])
        out.append(dict(e, fit=fit, why=why, download_gb=dl, warnings=plan.get("warnings", [])))
    pick = next((o for o in out if o["fit"] == "fits" and o["family"] == "qwen"), None) or next((o for o in out if o["fit"] == "tight" and o["family"] == "qwen"), None)
    res = {"machine": m, "adopted": adopted, "options": out, "pick": pick["model"] if pick else None}
    _rc.update(t=time.time(), v=res)
    return res


# ---------------------------------------------------------------- one step: install, download, save, start
def _free_port():
    for p in PROBE_PORTS:
        try:
            socket.create_connection(("127.0.0.1", p), timeout=0.3).close()
        except OSError:
            return p                                                  # nothing there: free, and inside the range Zest looks in
    return None


def setup(family, model, force=False):
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
        RUN.update(state="starting", preset=ZEST_PRESET + model, error=None, log=[], since=time.time(), phase="", low_memory=False, retry={"kind": "setup", "family": family, "model": model})
    threading.Thread(target=_do_setup, args=(entry, force), daemon=True).start()
    return True


def _do_setup(entry, force=False):
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
        if not force:
            time.sleep(2)
            check_memory(entry["model"])
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
    except LowMemory as e:
        RUN.update(state="error", error=str(e), low_memory=True)
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


# ---------------------------------------------------------------- idle shutdown, and making room for Lemonade
def activity(url):
    """(busy now?, tokens the server has produced so far) from `strata status`, or (None, None) when it can't be read."""
    code, out = _run(["status", "--url", url, "--output", "json"], 10)
    try:
        snap = json.JSONDecoder().raw_decode(out.lstrip())[0].get("snapshot") or {}
    except ValueError:
        return None, None
    work = snap.get("work") or {}
    return bool((work.get("active") or 0) or (work.get("queued") or 0)), (snap.get("rates") or {}).get("output_tokens_server_counter")


def zest_server():
    """The Strata Zest started and can stop, or None."""
    return next((r for r in running() if r["owner"] == "zest" and r["source"] == "launch" and r["state"] in ("ready", "loading")), None)


def set_idle_minutes(n):
    n = max(0, min(int(n), 24 * 60))
    _save(lambda s: s.__setitem__("idle_minutes", n))
    return n


_idle = {"url": None, "since": 0, "tokens": None}


def idle_tick(now=None):
    """One look at the Strata Zest started: restart the idle clock if it is working, stop it once it has been idle for the chosen
    time. Only Zest's own is ever stopped this way. Returns "stopped", "busy", "idle" or None (nothing to watch)."""
    now = now or time.time()
    r = zest_server()
    minutes = _settings()["idle_minutes"]
    if not r or r["state"] != "ready" or not minutes:
        _idle.update(url=None)
        return None
    if _idle["url"] != r["url"]:
        _idle.update(url=r["url"], since=now, tokens=None)       # a new server: its clock starts now
    busy, tokens = activity(r["url"])
    if busy is None or busy or tokens != _idle["tokens"]:
        _idle.update(since=now, tokens=tokens)
        return "busy"
    if now - _idle["since"] >= minutes * 60:
        stop(r["url"])
        _idle.update(url=None)
        return "stopped"
    return "idle"


def watch(every=30):
    """Run idle_tick forever (a daemon thread)."""
    while True:
        try:
            idle_tick()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(every)


def free_for_lemonade():
    """Before a Lemonade model loads: stop the Strata Zest started, so the two don't fight for memory. Returns "" when done or
    there was nothing to do, otherwise why it didn't (it is answering). A Strata you started is never touched."""
    r = zest_server()
    if not r:
        return ""
    busy, _ = activity(r["url"])
    if busy:
        return "Strata is answering right now. Wait for it to finish, or stop it from the Models page, then try again."
    stop(r["url"])
    return ""


# ---------------------------------------------------------------- updates
TESTED_SERIES = "0.2"                   # the Terminal series Zest has been tried with (strata --version: 0.2.x)
SUPD = {"state": "idle", "text": "", "error": None, "log": [], "stopped": ""}      # state: idle, working, done, deferred, error
_uc = {"t": 0, "v": None}


def _vkey(v):
    return tuple(int(x) if x.isdigit() else 0 for x in re.findall(r"\d+", str(v))[:3]) or (0,)


def update_check(max_age=0):
    """What `strata update --check` says, boiled down: {available, engine, control, bytes, serving, deferred, major, tested}.

    Strata checks its own signed feed and only offers an engine that fits this computer. `serving` are the profiles running now:
    an update can't replace an engine that is serving, so Zest stops its own and waits for the person to stop theirs.
    `major`: the offered Control is a different series than the installed one; Zest leaves that for the person to take."""
    if max_age and _uc["v"] is not None and time.time() - _uc["t"] < max_age:
        return _uc["v"]
    if not binary():
        return {"installed": False}
    d = _json(["update", "--check", "--output", "json"], 60)
    if not d.get("ok"):
        raise RuntimeError(((d.get("error") or {}).get("message") if isinstance(d.get("error"), dict) else d.get("error")) or "Couldn't read Strata's update feed. Check your internet connection.")
    eng, ctl = d.get("engine") or {}, d.get("control") or {}
    e_new = eng.get("status") == "available"
    c_new = ctl.get("status") == "available"
    major = bool(c_new and ctl.get("installed") and _vkey(ctl.get("offered"))[:2] != _vkey(ctl.get("installed"))[:2])
    serving = [x.get("profile") for x in d.get("serving") or []]
    zest = zest_server()
    theirs = [p for p in serving if not zest or p != zest.get("profile")]       # serving, and not the one Zest started
    v = version()
    out = {"installed": True, "version": v, "available": bool((e_new or c_new) and not major), "major": major,
           "engine": {"installed": (eng.get("installed") or {}).get("version"), "offered": (eng.get("offered") or {}).get("version"), "new": e_new},
           "control": {"installed": ctl.get("installed"), "offered": ctl.get("offered"), "new": c_new},
           "bytes": int((eng.get("download_bytes") or 0) if e_new else 0) + int((ctl.get("download_bytes") or 0) if c_new else 0),
           "serving": serving, "deferred": bool(theirs),
           "tested": _vkey(v)[:2] == _vkey(TESTED_SERIES)[:2] if v else True, "problem": eng.get("problem")}
    _uc.update(t=time.time(), v=out)
    return out


def start_update():
    """Download and install the newest engine and Control from Strata's signed feed, in the background. Strata tries the new engine
    with a test start before switching to it. Returns False when nothing was started (see SUPD)."""
    with _lock:
        if SUPD["state"] == "working":
            return False
        SUPD.update(state="working", text="Looking for a Strata update…", error=None, log=[], stopped="")
    try:
        c = update_check()
    except RuntimeError as e:
        SUPD.update(state="error", error=str(e))
        return False
    if not c.get("installed"):
        SUPD.update(state="idle", text="")
        return False
    if c["major"]:
        SUPD.update(state="deferred", text="Strata has a bigger update (Control %s). Zest leaves that one for you: run `strata update` in a terminal when you are ready." % c["control"]["offered"])
        return False
    if not c["available"]:
        SUPD.update(state="idle", text="Strata is up to date (engine %s, control %s)." % (c["engine"]["installed"], c["control"]["installed"]))
        return False
    if c["deferred"]:
        SUPD.update(state="deferred", text="Strata is running a model you started, and an update can't replace an engine that is serving. Update after you stop it.")
        return False
    threading.Thread(target=_do_update, args=(c,), daemon=True).start()
    return True


def _do_update(c):
    try:
        r = zest_server()
        if r:                                                  # Zest's own: stop it, update, and it starts again when a chat needs it
            SUPD.update(text="Stopping the Strata Zest started, so it can be updated…", stopped=r["name"] or "")
            stop(r["url"])
            time.sleep(3)
        SUPD["text"] = "Updating Strata (about %.1f GB to download)…" % (c["bytes"] / 1e9)
        p = subprocess.Popen([binary(), "update", "--output", "json"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        out = []
        for line in p.stdout:
            out.append(line)
            SUPD["log"] = (SUPD["log"] + [line.rstrip()])[-20:]
        code = p.wait()
        try:
            res = json.JSONDecoder().raw_decode("".join(out).lstrip())[0]
        except ValueError:
            res = {}
        if code != 0 or res.get("ok") is False:
            err = res.get("error")
            msg = (err.get("message") if isinstance(err, dict) else err) or " ".join([l for l in SUPD["log"] if l.strip()][-2:]) or "Strata's update failed."
            raise RuntimeError(msg)
        _uc["v"] = None
        n = update_check()
        SUPD.update(state="done", text="Strata is updated (engine %s, control %s)." % (n["engine"]["installed"], n["control"]["installed"]))
        _changed()
    except Exception as e:  # noqa: BLE001
        SUPD.update(state="error", error=str(e) or e.__class__.__name__)


# ---------------------------------------------------------------- tuning (strata tune auto / strata presets)
def profile_of(preset):
    """`strata launch` keeps a preset's run configuration under the profile `launch-<name, lowercased, with runs of other characters as "-">`."""
    return "launch-" + re.sub(r"[^a-z0-9_]+", "-", str(preset).lower()).strip("-")


def tuning_status(preset):
    """Boiled down `strata presets status`: {has, state (fresh/stale/none), reasons, name, id, gain_pct, decode_tok_s, baseline_tok_s, mode, outcome, seconds, when, presets}."""
    d = _json(["presets", "status", profile_of(preset), "--output", "json"], 20)
    a = d.get("active") or {}
    t = a.get("tuner") or {}
    return {"has": bool(a), "state": d.get("state") or ("none" if not a else "fresh"), "reasons": d.get("reasons") or [],
            "name": a.get("name"), "id": a.get("id"), "gain_pct": a.get("gain_pct"), "decode_tok_s": a.get("decode_tok_s"),
            "baseline_tok_s": a.get("baseline_decode_tok_s"), "mode": t.get("mode"), "outcome": t.get("outcome"),
            "seconds": t.get("seconds"), "when": a.get("created"),
            "presets": [{"id": p.get("id"), "name": p.get("name"), "active": bool(p.get("active"))} for p in d.get("presets") or []]}


def tune_minutes(mode):
    return 20 if mode == "quick" else 120


def _read_status_file(path):
    """Progress Strata writes at every step. The format isn't documented, so take the common spellings of a phase, a message and a
    fraction when they are there and show nothing otherwise."""
    try:
        with open(path) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for k in ("phase", "stage", "step", "message", "detail", "text", "status"):
        if isinstance(d.get(k), str) and d[k]:
            out["text"] = d[k]
            break
    for k in ("progress", "fraction", "percent"):
        v = d.get(k)
        if isinstance(v, (int, float)):
            out["progress"] = v / 100 if v > 1 else v
            break
    if "progress" not in out and isinstance(d.get("done"), (int, float)) and d.get("total"):
        out["progress"] = d["done"] / d["total"]
    return out


def tune_run(preset, opts, job):
    """Run `strata tune auto` on a saved preset (the server must not be serving: Strata needs the GPU to itself) and wait for it.
    `job` has update(**kw) and cancelled(). Returns a dict of what to show; raises RuntimeError with a plain message on failure."""
    mode = "thorough" if opts.get("mode") == "thorough" else "quick"
    args = [binary(), "tune", "auto", profile_of(preset), "--mode", mode, "--use", "coding" if opts.get("use") == "coding" else "chat",
            "--goal", "room" if opts.get("goal") == "room" else "speed", "--output", "json"]
    sf = os.path.join(store.data_dir(), "strata-tune-status.json")
    os.makedirs(os.path.dirname(sf), exist_ok=True)
    try:
        os.remove(sf)
    except OSError:
        pass
    args += ["--status-file", sf]
    before = tuning_status(preset)
    p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    out = []
    threading.Thread(target=lambda: [out.append(l) for l in p.stdout], daemon=True).start()
    t0, last = time.time(), 0.0
    while p.poll() is None:
        if job.cancelled():
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except OSError:
                pass
            p.wait(timeout=60)
            raise Cancelled()
        if time.time() - last >= 2:                       # cancel is checked every half second, progress read every two
            prog = _read_status_file(sf)
            job.update(text=prog.get("text") or "Strata is measuring settings…", progress=prog.get("progress"), elapsed=int(time.time() - t0))
            last = time.time()
        time.sleep(0.5)
    text = "".join(out)
    try:
        res = json.JSONDecoder().raw_decode(text.lstrip())[0]
    except ValueError:
        res = {}
    if p.returncode != 0 or res.get("ok") is False:
        err = res.get("error")
        msg = (err.get("message") if isinstance(err, dict) else err) or " ".join([l.strip() for l in text.splitlines() if l.strip()][-2:]) or "Strata's tuning stopped."
        raise RuntimeError(msg)
    after = tuning_status(preset)
    _pc["t"] = 0
    return {"before": before, "after": after, "gain_pct": after.get("gain_pct"), "decode_tok_s": after.get("decode_tok_s"),
            "baseline_tok_s": after.get("baseline_tok_s"), "outcome": after.get("outcome")}


class Cancelled(Exception):
    pass


def tune_action(preset, act):
    """off (switch tuning off, it is kept), on (switch the active saved tuning back on) or undo (back to the one before). Returns (ok, message)."""
    st = tuning_status(preset)
    prof = profile_of(preset)
    if act == "off":
        code, out = _run(["presets", "off", prof, "--output", "json"], 30)
    elif act in ("on", "undo") and st.get("id"):
        args = ["presets", "apply", st["id"]] if act == "on" else ["presets", "undo", st["id"], "--config", prof]
        code, out = _run(args + ["--output", "json"], 30)
    else:
        return False, "Strata has no saved tuning for that setup."
    ok = code == 0
    _pc["t"] = 0
    return ok, "" if ok else (out.strip().splitlines() or ["Strata couldn't do that."])[-1]


# ---------------------------------------------------------------- the model files Strata already has
_im = {"t": 0, "v": None}


def installed_models():
    """Cached for ten seconds: the library asks once per file."""
    if _im["v"] is None or time.time() - _im["t"] > 10:
        _im.update(t=time.time(), v=_installed_models())
    return _im["v"]


def _installed_models():
    """[{id, name, config, status, files}] for every model Strata has set up, with the GGUF files its run config points at.

    Strata keeps its own files where it was told to (often another disk); this finds them without scanning drives."""
    out = []
    for m in _json(["models", "list", "--installed", "--output", "json"], 30).get("models", []):
        files = []
        try:
            with open(m.get("source_config") or "") as f:
                args = json.load(f).get("args") or []
        except (OSError, ValueError):
            args = []
        for i, a in enumerate(args[:-1]):
            if a in ("--native", "--ple-gguf", "--gguf") and str(args[i + 1]).lower().endswith(".gguf") and os.path.isfile(args[i + 1]):
                files.append(args[i + 1])
        pack = next((args[i + 1] for i, a in enumerate(args[:-1]) if a == "--pack"), "")
        out.append({"id": m.get("id"), "name": m.get("name"), "config": m.get("source_config"), "status": m.get("component_status"),
                    "files": sorted(set(files)), "data_root": os.path.dirname(os.path.dirname(pack)) if pack else ""})
    return out
