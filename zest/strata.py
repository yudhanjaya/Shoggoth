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


PROBE_PORTS = range(8080, 8090)          # Strata's default is 8080; a second server takes the next free one


def _settings():
    return store.load().setdefault("strata", {"remote": [], "keys": {}, "owned": {"preset": "", "port": 0}})


def _save(mutate):
    s = store.load()
    st = s.setdefault("strata", {"remote": [], "keys": {}, "owned": {"preset": "", "port": 0}})
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


def models():
    """Chat models Zest can offer from every ready Strata server: {id, url, key, ctx, vision, provider, ...}, id with Zest's prefix.
    Pi gets one provider per server (strata, strata2, ...)."""
    out = []
    for i, r in enumerate([r for r in running() if r["state"] == "ready" and not r["needs_key"]]):
        for m in r["models"]:
            out.append({"id": PREFIX + m["id"], "tools": True, "reasoning": True, "vision": m["vision"], "ctx": m["ctx"] or 32768,
                        "provider": "strata" if i == 0 else "strata%d" % (i + 1), "url": r["url"], "key": r["key"], "bare": m["id"]})
    return out


def find_model(name):
    return next((m for m in models() if m["id"] == name), None)


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
