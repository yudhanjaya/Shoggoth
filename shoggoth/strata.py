"""Strata: a second engine, for models too big for a graphics card (experts split between GPU, RAM and SSD).

Strata (github.com/Niko1221/Strata) installs its own `strata` command. Shoggoth drives that command and never touches
Strata's files: `strata launch --status/--list-presets --output json` say what is saved and running, `strata launch
--preset NAME --yes --no-tui` starts one, `strata launch --stop` stops it. A running Strata is an OpenAI-style server
on localhost, so Pi talks to it directly. Shoggoth never passes --restart: a Strata the person started themselves keeps
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
import recs
import store

REPO = "Niko1221/Strata"
PREFIX = "strata/"                       # in front of a Strata model's name wherever Shoggoth lists models next to Lemonade's
UPD = {"state": "idle", "error": None, "log": []}         # installing the Terminal
RUN = {"state": "idle", "preset": None, "error": None, "log": [], "since": 0, "low_memory": False, "retry": None, "notice": None}   # starting a preset
before_launch = []                       # callables run just before Strata starts loading a model (Lemonade's models are freed: shared memory)
on_change = []                           # callables run after Strata starts or stops (idle chats re-read the model list)
_lock = threading.Lock()


def _before_launch():
    """Make room before a model loads. A hook that can't (something else is answering) raises, and the start stops with its reason."""
    for f in before_launch:
        f()


def _changed():
    _cache["v"] = None
    for f in on_change:
        try:
            f()
        except Exception:  # noqa: BLE001
            pass


def binary():
    for cand in (os.environ.get("SHOGGOTH_STRATA"), shutil.which("strata"), os.path.expanduser("~/.local/bin/strata")):
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
_dlock = threading.Lock()                # one discovery at a time; callers that arrive meanwhile get its answer


def version():
    code, out = _run(["--version"], 15)
    m = re.search(r"strata\s+(\d[\w.]*)", out) if code == 0 else None
    return m.group(1) if m else ""


PROBE_PORTS = range(8080, 8090)          # Strata's default is 8080; a second server takes the next free one


SHOGGOTH_PRESET = "Shoggoth "                    # presets Shoggoth makes are named "Shoggoth <model>"; only those start on demand
OWN_PRESETS = (SHOGGOTH_PRESET, "Zest ")         # ...and ones it made while it was called Zest


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


def _show_key():
    """The key of a server `strata launch` manages. Empty when there isn't one or the command doesn't say."""
    code, out = _run(["launch", "--show-key"], 10)
    words = re.findall(r"[A-Za-z0-9_\-]{16,}", out) if code == 0 else []
    return words[-1] if words else ""


def running():
    """Every Strata server Shoggoth can see, cached for a few seconds (the chat bar and every new message ask).

    [{url, port, name, state, source, owner, needs_key, key, models:[{id, ctx, vision}]}]
    source: launch (strata launch manages it), probe (found answering on a local port), saved (an address typed in).
    owner: "zest" if Shoggoth started it, otherwise "yours". Shoggoth only ever acts by itself on its own."""
    with _dlock:
        if _cache["v"] is None or time.time() - _cache["t"] > 3:
            _cache.update(t=time.time(), v=_discover())
        return _cache["v"]


def _discover():
    st = _settings()
    found = {}
    for r in _json(["launch", "--status", "--output", "json"], 15).get("running", []):
        if r.get("url"):
            found[r["url"].rstrip("/")] = {"name": r.get("name"), "profile": r.get("profile"), "port": r.get("port"), "state": r.get("state"), "source": "launch"}
    probe = [u for u in ("http://127.0.0.1:%d" % p for p in PROBE_PORTS) if u not in found]
    saved = [u for u in (str(r.get("url", "")).rstrip("/") for r in st["remote"]) if u and u not in found and u not in probe]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:          # an unreachable address costs its timeout once, not once each
        health = dict(zip(probe + saved, pool.map(_health, probe + saved)))
    for u in probe:
        h = health[u]
        if h:
            found[u] = {"name": h.get("model") or "Strata", "port": int(u.rsplit(":", 1)[1]), "state": "ready" if h.get("loaded") else "loading", "source": "probe"}
    for u in saved:
        h = health[u]
        found[u] = {"name": (h or {}).get("model") or u, "port": 0, "state": ("ready" if h.get("loaded") else "loading") if h else "unreachable", "source": "saved"}
    owned = st["owned"].get("port")
    mine = profile_of(st["owned"]["preset"]) if st["owned"].get("preset") else ""
    launched = [u for u, r in found.items() if r["source"] == "launch"]
    # `launch --show-key` names no server: its key is only known to be that server's when `strata launch` manages just one
    shown = _show_key() if len(launched) == 1 and not st["keys"].get(launched[0]) else ""
    out = []
    for u, r in sorted(found.items()):
        key = st["keys"].get(u, "") or (shown if r["source"] == "launch" else "")
        status, data = _http_json(u + "/v1/models", key) if r["state"] == "ready" else (0, {})
        models = []
        for m in data.get("data", []):
            mods = (m.get("architecture") or {}).get("input_modalities") or []
            if m.get("id"):
                models.append({"id": m["id"], "ctx": int((m.get("meta") or {}).get("n_ctx") or 0), "vision": "image" in mods})
        if not models and status == 200:
            # Strata frees its model's memory when idle and loads it again on the next request: it lists nothing then, but its health still names it
            h = _health(u)
            if h and h.get("model") and not h.get("loaded"):
                models.append({"id": h["model"], "ctx": int(h.get("max_context") or 0), "vision": bool(h.get("images")), "asleep": True})
        # Shoggoth's own is the one `strata launch` runs on the port Shoggoth started it on, from the preset Shoggoth started (when Strata says
        # which): a server of yours that later takes the same port is still yours.
        ours = bool(owned) and r["port"] == owned and r["source"] == "launch" and (not r.get("profile") or not mine or r["profile"] == mine)
        out.append(dict(r, url=u, key=key, needs_key=status in (401, 403), models=models, owner="zest" if ours else "yours"))
    if owned and not any(r["owner"] == "zest" for r in out):
        _save(lambda s: s["owned"].update(preset="", port=0))       # the one Shoggoth started has gone: forget it
    return out


def presets():
    return [{"name": p.get("name"), "family": p.get("family"), "model": p.get("model"), "context": p.get("context"),
             "vision": p.get("vision"), "port": p.get("port"), "last_used": p.get("last_used"),
             "tuned_preset": p.get("tuned_preset"), "tuned_at": p.get("tuned_at"), "extra": p.get("extra") or {}}
            for p in _json(["launch", "--list-presets", "--output", "json"], 15).get("presets", []) if p.get("name")]


def reserve_setting(preset):
    """How to leave graphics memory free for the system when this saved setup starts: (the `--set` text, MiB), or (None, 0) when the person
    set no headroom, the setup already carries a reserve of its own (theirs wins), or the family has no such setting. The two Strata
    engines name it differently: the Qwen engine's `vram-reserve-mib`, the DeepSeek engine's `reserve-mib`. Both are MiB left free on a card."""
    import modelmeta
    extra = {str(k).lstrip("-").replace("_", "-") for k in (preset.get("extra") or {})}
    if extra & {"vram-reserve-mib", "reserve-mib"}:
        return None, 0
    mid = str(preset.get("model") or "").lower()
    inst = next((m for m in installed_models() if mid and (str(m.get("id", "")).lower() == mid or str(m.get("id", "")).lower().endswith(mid))), None)
    mib = int(round(modelmeta.headroom_gb(PREFIX + (inst["id"] if inst else mid)) * 1024))
    if mib <= 0:
        return None, 0
    key = "reserve-mib" if str(preset.get("family") or "").lower().startswith("deepseek") else "vram-reserve-mib"
    return "%s=%d" % (key, mib), mib


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
    """Chat models Shoggoth can offer: {id, url, key, ctx, vision, provider, ...}, id with Shoggoth's prefix. Pi gets one provider per server.

    Models of every ready Strata server, and `dormant` ones: a setup Shoggoth made earlier that isn't running right now. A
    dormant model starts when a chat first needs it (see ensure)."""
    out = []
    live = [r for r in running() if r["state"] == "ready" and not r["needs_key"]]
    for r in live:
        for m in r["models"]:
            out.append({"id": PREFIX + m["id"], "tools": True, "reasoning": True, "vision": m["vision"], "ctx": m["ctx"] or 32768,
                        "provider": _provider(r["url"]), "url": r["url"], "key": r["key"], "bare": m["id"], "asleep": bool(m.get("asleep"))})
    have = {m["id"] for m in out}
    ports = {r["port"] for r in running() if r["state"] in ("ready", "loading")}
    if binary():
        mine = _presets_cached()
        for im in installed_models():
            if im["status"] != "present":
                continue
            p = _preset_for(im, mine)
            if not p or not p.get("port") or int(p["port"]) in ports:
                continue
            k = _settings()["known"].get(p["name"]) or {}
            name = k.get("id") or im["name"]                  # what its server called it last time it ran: the name it will have again
            if PREFIX + name in have:
                continue
            url = "http://127.0.0.1:%d" % int(p["port"])
            out.append({"id": PREFIX + name, "tools": True, "reasoning": True, "vision": bool(k.get("vision", p.get("vision") not in (None, "off"))),
                        "ctx": int(k.get("ctx") or p.get("context") or 131072), "provider": _provider(url), "url": url, "key": "", "bare": name,
                        "dormant": True, "preset": p["name"]})
    return out


def _preset_for(installed, presets):
    """The saved setup that starts an installed model: a preset whose model is that model (a Shoggoth-made one first, then the most recently used)."""
    mid = str(installed.get("id") or "").lower()
    ok = [p for p in presets if p.get("model") and (mid == str(p["model"]).lower() or mid.endswith(str(p["model"]).lower()))]
    ok.sort(key=lambda p: str(p.get("last_used") or ""), reverse=True)            # most recently used first...
    ok.sort(key=lambda p: not p["name"].startswith(OWN_PRESETS))                  # ...then Shoggoth-made ones ahead (the sort is stable)
    return ok[0] if ok else None


def find_model(name):
    return next((m for m in models() if m["id"] == name), None)


def is_live(name):
    m = find_model(name)
    return bool(m) and not m.get("dormant") and not m.get("asleep")


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
            RUN.update(state="error", preset=preset, log=[], error="Strata is already running %s on this computer. Shoggoth won't start a second big model beside it: chat with that one, or stop it first." % (other["name"] or "a model"))
            return False
        RUN.update(state="starting", preset=preset, error=None, log=[], since=time.time(), low_memory=False, retry={"kind": "start", "preset": preset}, notice=None)
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
    """What a catalog model needs in RAM plus graphics memory; None for one Shoggoth has no figure for (a setup of your own)."""
    return next((e["need_gb"] for e in catalog() if e["model"].lower() == str(model or "").lower()), None)


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
        for r in running():                          # Shoggoth's own earlier one gives way; one big model at a time
            if r["owner"] == "zest" and r["source"] == "launch":
                RUN["log"] = ["Stopping the Strata Shoggoth started earlier…"]
                stop(r["url"])
                time.sleep(3)                        # let the memory come back before measuring it
        if not force:
            check_memory(model)
        saved = next((x for x in presets() if x["name"] == preset), None)
        keep, mib = reserve_setting(saved) if saved else (None, 0)

        def launch(extra):
            p = subprocess.Popen([binary(), "launch", "--preset", preset, "--yes", "--no-tui"] + extra, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
            for line in p.stdout:
                RUN["log"] = (RUN["log"] + [line.rstrip()])[-40:]
            return p.wait()
        code = launch(["--set", keep] if keep else [])
        if code != 0 and keep and re.search(r"memory|vram|room|reserve|fit", " ".join(RUN["log"][-12:]), re.I):
            # Keeping memory free is a wish, not a rule: when the model cannot start with it, start it without
            RUN["log"] = RUN["log"] + ["It didn't start with %.1f GB kept free, so Shoggoth is starting it without that." % (mib / 1024)]
            RUN["notice"] = "This model needs the whole graphics card, so it started without keeping %.1f GB free. Your desktop may slow down while it runs." % (mib / 1024)
            code = launch([])
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
        return False, "Shoggoth can't stop that one, because it wasn't started with `strata launch`. Stop it where you started it."
    code, out = _run(["launch", "--stop"], 60)
    RUN.update(state="idle", error=None)
    _save(lambda s: s["owned"].update(preset="", port=0))
    _changed()
    return code == 0, "" if code == 0 else (out.strip().splitlines() or ["Strata didn't stop."])[-1]


# ---------------------------------------------------------------- installing the Terminal
def _get(url, limit=2_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": "Shoggoth"})
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
            raise RuntimeError("The installer didn't match the checksum Strata publishes, so Shoggoth didn't run it.")
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
            raise RuntimeError("Strata installed, but Shoggoth can't find the `strata` command. Open a new terminal, or add ~/.local/bin to your PATH.")
    except urllib.error.URLError as e:
        raise RuntimeError("Couldn't reach GitHub (%s)." % getattr(e, "reason", e))


def _do_install():
    try:
        _install_blocking()
        UPD["state"] = "done"
    except Exception as e:  # noqa: BLE001
        UPD.update(state="error", error=str(e) or e.__class__.__name__)


# ---------------------------------------------------------------- what fits this computer
def catalog():
    """The big models Shoggoth offers to set up, best quality first, with the memory each needs: the `strata` list in
    recommendations/models.yaml (edit that file to change them)."""
    return recs.strata_catalog()


def machine():
    return hardware.machine()


QWEN_FAMILIES = ("qwen", "coder", "swift")           # the families built on Qwen3.8 Flash-Next: they take a context size


def preset_name(entry):
    """The saved setup Shoggoth makes for a catalog entry: "Shoggoth <model>", or "Shoggoth <family> <model>" for a family that
    shares model names with another (Swift's IQ2_XS is not Qwen's)."""
    return SHOGGOTH_PRESET + (entry["model"] if entry["family"] in ("qwen", "deepseek41") else "%s %s" % (entry["family"], entry["model"]))


def _plan(entry, ctx):
    """strata launch --plan for one catalog entry: its download size and warnings, or {} before Strata is installed."""
    if not binary():
        return {}
    args = ["launch", "--plan", "--family", entry["family"], "--model", entry["model"], "--output", "json"]
    if entry["family"] in QWEN_FAMILIES:
        args += ["--context", str(ctx)]
    p = _json(args, 20).get("plan") or {}
    return {"download_gb": (p.get("download") or {}).get("gb"), "warnings": p.get("warnings") or []}


def models_root():
    """The folder Strata keeps (and will download) its models in, learned from a model it already has, or None when it has none.
    It is often another disk, so that is the disk a download has to fit on."""
    for m in installed_models():
        for cand in [m.get("data_root")] + [f.split("/models/")[0] for f in m.get("files", []) if "/models/" in f]:
            if cand and os.path.isdir(cand):
                return cand
    return None


def disk_problem(download_gb, root=None):
    """A plain sentence when `download_gb` won't fit where Strata keeps models (with 10 GB to spare), else None."""
    if not download_gb:
        return None
    root = root or models_root()
    where = root or os.path.expanduser("~/.local/share")
    free = shutil.disk_usage(where).free / 1e9
    if download_gb + 10 > free:
        return "Needs about %d GB of free disk for the download; %d GB is free%s." % (download_gb, free, " on " + root if root else "")
    return None


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
    # An install that already holds models keeps them wherever its owner chose, and that disk is the one a download must fit on.
    root = models_root() if binary() else None
    adopted = bool(binary() and (root or _json(["models", "list", "--installed", "--output", "json"], 30).get("models")))
    cat = catalog()
    fits = []
    for e in cat:
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
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(cat))) as pool:
        futs = {(e["family"], e["model"]): pool.submit(_plan, e, e["ctx"]) for e, (fit, _) in zip(cat, fits) if fit != "no"}
        for k, f in futs.items():
            try:
                plans[k] = f.result(timeout=90)
            except Exception:  # noqa: BLE001
                plans[k] = {}
    out = []
    for e, (fit, why) in zip(cat, fits):
        plan = plans.get((e["family"], e["model"]), {})
        dl = plan.get("download_gb")
        bad = disk_problem(dl, root) if fit != "no" and (root or not adopted) else None
        if bad:
            fit, why = "no", bad
        out.append(dict(e, fit=fit, why=why, download_gb=dl, warnings=plan.get("warnings", []), preset=preset_name(e)))
    pick = next((o for o in out if o["fit"] == "fits" and o["suggest"]), None) or next((o for o in out if o["fit"] == "tight" and o["suggest"]), None)
    res = {"machine": m, "adopted": adopted, "options": out, "pick": pick["model"] if pick else None}
    _rc.update(t=time.time(), v=res)
    return res


# ---------------------------------------------------------------- one step: install, download, save, start
def _free_port():
    for p in PROBE_PORTS:
        try:
            socket.create_connection(("127.0.0.1", p), timeout=0.3).close()
        except OSError:
            return p                                                  # nothing there: free, and inside the range Shoggoth looks in
    return None


def setup(family, model, force=False):
    """Install Strata if it is missing, then download `model`, save it as the preset "Shoggoth <model>" with the context Shoggoth
    uses (128k where the model allows) and start it. Runs in the background; progress is in RUN. Never touches a running Strata."""
    entry = next((e for e in catalog() if e["family"] == family and e["model"] == model), None)
    if not entry:
        RUN.update(state="error", preset=None, error="Shoggoth doesn't know that model.", log=[])
        return False
    with _lock:
        if _active(RUN["state"]) or UPD["state"] == "installing":
            return False
        other = next((r for r in running() if r["state"] in ("ready", "loading") and r["source"] != "saved"), None)
        if other:
            RUN.update(state="error", preset=None, log=[], error="Strata is already running %s on this computer. Shoggoth won't start a second big model beside it: use that one, or stop it first." % (other["name"] or "a model"))
            return False
        RUN.update(state="starting", preset=preset_name(entry), error=None, log=[], since=time.time(), phase="", low_memory=False, retry={"kind": "setup", "family": family, "model": model})
    threading.Thread(target=_do_setup, args=(entry, force), daemon=True).start()
    return True


def _do_setup(entry, force=False):
    name = preset_name(entry)
    try:
        if not binary():
            RUN["phase"] = "Installing Strata…"
            _install_blocking()
        port = _free_port()
        if not port:
            raise RuntimeError("Ports 8080 to 8089 are all in use, so Shoggoth has nowhere to start Strata.")
        args = [binary(), "launch", "--family", entry["family"], "--model", entry["model"], "--access", "local", "--port", str(port),
                "--save-preset", name, "--yes", "--no-tui"]
        if entry["family"] in QWEN_FAMILIES:
            args += ["--context", str(entry["ctx"])]
        if not force:
            bad = disk_problem(_plan(entry, entry["ctx"]).get("download_gb"))
            if bad:
                raise RuntimeError(bad + " Free some space, or move Strata's models to a bigger disk (strata models move).")
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
    """Make sure the Strata model `model` is serving, starting the Shoggoth-made setup behind a dormant one. Waits until it
    answers (the first start loads tens of GB). Returns the live model ({id, url, key, ctx, bare, ...}): normally `model`
    itself, but the first time a setup runs its server may call it something else. Raises ValueError with something a
    person can act on."""
    m = find_model(model)
    if m and not m.get("dormant"):
        return m
    if not m:
        raise ValueError("Strata isn't running that model. Start it from the Models page.")
    deadline = time.time() + 1800
    if not start(m["preset"]):
        if RUN["state"] == "error":
            raise ValueError(RUN["error"])
    ready_at = None
    while time.time() < deadline:                  # started by us just now, or by another chat a moment ago
        live = find_model(model)
        if live and not live.get("dormant"):
            return live
        if RUN["state"] == "error":
            raise ValueError(RUN["error"] or "Strata didn't start.")
        if RUN["state"] == "ready":
            # It started, but not under that name: whatever the server on the setup's port serves is this setup's model.
            other = next((x for x in models() if not x.get("dormant") and x["url"] == m["url"]), None)
            if other:
                return other
            ready_at = ready_at or time.time()
            if time.time() - ready_at > 60:
                raise ValueError("Strata started, but nothing is serving %s on %s. Look at the Models page." % (bare(model), m["url"]))
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
    """The Strata Shoggoth started and can stop, or None."""
    return next((r for r in running() if r["owner"] == "zest" and r["source"] == "launch" and r["state"] in ("ready", "loading")), None)


def set_idle_minutes(n):
    n = max(0, min(int(n), 24 * 60))
    _save(lambda s: s.__setitem__("idle_minutes", n))
    return n


_idle = {"url": None, "since": 0, "tokens": None}


def idle_tick(now=None):
    """One look at the Strata Shoggoth started: restart the idle clock if it is working, stop it once it has been idle for the chosen
    time. Only Shoggoth's own is ever stopped this way. Returns "stopped", "busy", "idle" or None (nothing to watch)."""
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
    """Before a Lemonade model loads: stop the Strata Shoggoth started, so the two don't fight for memory. Returns "" when done or
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
TESTED_SERIES = "0.2"                   # the Terminal series Shoggoth has been tried with (strata --version: 0.2.x)
SUPD = {"state": "idle", "text": "", "error": None, "log": [], "stopped": ""}      # state: idle, working, done, deferred, error
_uc = {"t": 0, "v": None}


def _vkey(v):
    return tuple(int(x) if x.isdigit() else 0 for x in re.findall(r"\d+", str(v))[:3]) or (0,)


def update_check(max_age=0):
    """What `strata update --check` says, boiled down: {available, engine, control, bytes, serving, deferred, major, tested}.

    Strata checks its own signed feed and only offers an engine that fits this computer. `serving` are the profiles running now:
    an update can't replace an engine that is serving, so Shoggoth stops its own and waits for the person to stop theirs.
    `major`: the offered Control is a different series than the installed one; Shoggoth leaves that for the person to take."""
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
    theirs = [p for p in serving if not zest or p != zest.get("profile")]       # serving, and not the one Shoggoth started
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
        SUPD.update(state="deferred", text="Strata has a bigger update (Control %s). Shoggoth leaves that one for you: run `strata update` in a terminal when you are ready." % c["control"]["offered"])
        return False
    if not c["available"]:
        SUPD.update(state="idle", text="Strata is up to date (engine %s, control %s)." % (c["engine"]["installed"], c["control"]["installed"]))
        return False
    if c["deferred"]:
        SUPD.update(state="deferred", text="Strata is running a model you started, and an update can't replace an engine that is serving. Update after you stop it.")
        return False
    threading.Thread(target=_do_update, args=(c,), daemon=True).start()
    return True


def update_failure(res):
    """Strata's `update --output json` answers per part ("engine", "control"). In words: what failed and why, what did get updated, and
    that a part that failed was left as it was. None when the answer has no per-part results."""
    results = [r for r in (res.get("results") or []) if isinstance(r, dict)]
    failed = [r for r in results if r.get("status") == "failed"]
    if not failed:
        return None
    names = {"engine": "the engine", "control": "the Strata app"}
    said = []
    for r in failed:
        why = str(r.get("error") or "it didn't work").replace("`strata update --force`", "strata update --force")
        said.append("Strata couldn't update %s. %s" % (names.get(r.get("component"), r.get("component") or "a part"), why))
    done = [r for r in results if r.get("changed") and r.get("status") != "failed"]
    if done:
        said.append("The rest did update: %s." % ", ".join("%s to %s" % (names.get(r.get("component"), r.get("component")), r.get("installed_after") or "the new version") for r in done))
    return " ".join(said)


def _do_update(c):
    try:
        r = zest_server()
        if r:                                                  # Shoggoth's own: stop it, update, and it starts again when a chat needs it
            SUPD.update(text="Stopping the Strata Shoggoth started, so it can be updated…", stopped=r["name"] or "")
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
            msg = update_failure(res) or (err.get("message") if isinstance(err, dict) else err) or " ".join([l for l in SUPD["log"] if l.strip()][-2:]) or "Strata's update failed."
            _uc["v"] = None
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


# ---------------------------------------------------------------- does Strata still answer the way Shoggoth reads it?
def _lacks(d, *paths):
    """The dotted paths that are missing from a parsed answer."""
    out = []
    for path in paths:
        cur = d
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                out.append(path)
                break
    return out


def _each(items, *keys):
    """Keys that some item lacks (an empty list has nothing to check)."""
    return sorted({k for it in items if isinstance(it, dict) for k in keys if k not in it})


# (what Shoggoth reads, the call, a check on its parsed answer that returns the problems). These are the only places Shoggoth depends on the shape
# of Strata's output, so a change upstream shows up here first. The tests run the checks on captured real answers (tests/fixtures/real).
CONTRACTS = [
    ("what is running", ["launch", "--status", "--output", "json"],
     lambda d: _lacks(d, "running") + (["running (not a list)"] if not isinstance(d.get("running", []), list) else [])
     + ["running[].%s" % k for k in _each(d.get("running") or [], "url", "name", "profile", "port", "state")]),
    ("saved setups", ["launch", "--list-presets", "--output", "json"],
     lambda d: _lacks(d, "presets") + ["presets[].%s" % k for k in _each(d.get("presets") or [], "name", "family", "model", "context", "port")]),
    ("installed models", ["models", "list", "--installed", "--output", "json"],
     lambda d: _lacks(d, "models") + ["models[].%s" % k for k in _each(d.get("models") or [], "id", "name", "source_config", "component_status")]),
    ("the update check", ["update", "--check", "--output", "json"],
     lambda d: _lacks(d, "ok", "engine.status", "engine.installed.version", "control.status", "control.installed") + (["serving (not a list)"] if not isinstance(d.get("serving", []), list) else [])),
    ("a download plan", ["launch", "--plan", "--family", "qwen", "--model", "IQ3_S", "--context", "131072", "--output", "json"],
     lambda d: _lacks(d, "plan.download", "plan.warnings", "plan.profile") + (["plan.download.gb (not a number)"] if not isinstance((((d.get("plan") or {}).get("download") or {}).get("gb")), (int, float)) else [])),
]


def selftest():
    """Ask the installed Strata the questions Shoggoth asks and check the answers still have the fields Shoggoth reads. Starts nothing, changes nothing.
    [{name, ok, detail}]; empty when Strata isn't installed."""
    if not binary():
        return []
    out = []
    v = version()
    out.append({"name": "its version", "ok": bool(v), "detail": ("Strata %s" % v) if v else "`strata --version` didn't answer as expected."})
    for name, args, check in CONTRACTS:
        try:
            code, text = _run(args, 60)
            d = json.JSONDecoder().raw_decode(text.lstrip())[0]
            problems = check(d)
        except ValueError:
            problems = ["the answer isn't JSON any more"]
        except Exception as e:  # noqa: BLE001
            problems = [str(e)]
        out.append({"name": name, "ok": not problems, "detail": "" if not problems else "missing or changed: " + ", ".join(problems[:6])})
    return out
