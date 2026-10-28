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
import subprocess
import threading
import time
import urllib.error
import urllib.request

import store

REPO = "Niko1221/Strata"
PREFIX = "strata/"                       # in front of a Strata model's name wherever Zest lists models next to Lemonade's
UPD = {"state": "idle", "error": None, "log": []}         # installing the Terminal
RUN = {"state": "idle", "preset": None, "error": None, "log": [], "since": 0}   # starting a preset
on_change = []                           # callables run after Strata starts or stops (idle chats re-read the model list)
_lock = threading.Lock()


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


def _http_json(url, key="", timeout=3):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except (OSError, ValueError):
        return {}


def running():
    """Cached for a few seconds: the chat bar and every new message ask."""
    now = time.time()
    if _cache["v"] is None or now - _cache["t"] > 3:
        _cache.update(t=now, v=_running())
    return _cache["v"]


def _running():
    """[{name, url, port, state, models:[{id, ctx, vision}]}] of the Strata servers `strata launch` has started."""
    out = []
    for r in _json(["launch", "--status", "--output", "json"], 15).get("running", []):
        url = str(r.get("url") or "")
        if not url:
            continue
        models = []
        for m in _http_json(url + "/v1/models").get("data", []):
            mods = (m.get("architecture") or {}).get("input_modalities") or []
            models.append({"id": m.get("id"), "ctx": int((m.get("meta") or {}).get("n_ctx") or 0), "vision": "image" in mods})
        out.append({"name": r.get("name"), "url": url, "port": r.get("port"), "state": r.get("state"), "models": [m for m in models if m["id"]]})
    return out


def presets():
    return [{"name": p.get("name"), "family": p.get("family"), "model": p.get("model"), "context": p.get("context"),
             "vision": p.get("vision"), "last_used": p.get("last_used")}
            for p in _json(["launch", "--list-presets", "--output", "json"], 15).get("presets", []) if p.get("name")]


def models():
    """Chat models Zest can offer from every ready Strata server: {id, url, ctx, vision, ...} with Zest's prefix on id."""
    if not binary():
        return []
    out = []
    for r in running():
        if r["state"] != "ready":
            continue
        for m in r["models"]:
            out.append({"id": PREFIX + m["id"], "tools": True, "reasoning": True, "vision": m["vision"], "ctx": m["ctx"] or 32768,
                        "provider": "strata", "url": r["url"], "bare": m["id"]})
    return out


def find_model(name):
    return next((m for m in models() if m["id"] == name), None)


def summary():
    exe = binary()
    s = {"installed": bool(exe), "version": version() if exe else "", "presets": [], "running": [], "run": dict(RUN), "install": dict(UPD),
         "command": "strata launch"}
    if exe:
        s["presets"], s["running"] = presets(), running()
    return s


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
        if preset not in [p["name"] for p in presets()]:
            RUN.update(state="error", preset=preset, error="Strata has no saved preset called “%s”." % preset, log=[])
            return False
        RUN.update(state="starting", preset=preset, error=None, log=[], since=time.time())
    threading.Thread(target=_do_start, args=(preset,), daemon=True).start()
    return True


def _do_start(preset):
    try:
        p = subprocess.Popen([binary(), "launch", "--preset", preset, "--yes", "--no-tui"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        for line in p.stdout:
            RUN["log"] = (RUN["log"] + [line.rstrip()])[-40:]
        code = p.wait()
        if code != 0:
            tail = [l for l in RUN["log"] if l.strip()][-2:]
            raise RuntimeError(" ".join(tail) or "Strata stopped with code %d." % code)
        RUN["state"] = "ready"
        _changed()
    except Exception as e:  # noqa: BLE001
        RUN.update(state="error", error=str(e) or e.__class__.__name__)


def stop():
    """Stop the running Strata. Returns (ok, message)."""
    code, out = _run(["launch", "--stop"], 60)
    RUN.update(state="idle", error=None)
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


def _do_install():
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
        UPD["state"] = "done"
    except urllib.error.URLError as e:
        UPD.update(state="error", error="Couldn't reach GitHub (%s)." % getattr(e, "reason", e))
    except Exception as e:  # noqa: BLE001
        UPD.update(state="error", error=str(e) or e.__class__.__name__)
