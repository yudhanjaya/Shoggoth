"""The Lemonade that Zest can run for you.

A copy of Lemonade's official "embeddable" build ships inside the AppImage. Zest starts it privately (its own
port, API key and data folder) so it never touches a Lemonade you run yourself. Updates are downloaded from
Lemonade's GitHub releases, checked against the checksum GitHub publishes, and kept in the user's data folder,
because the AppImage itself is read-only.
"""
import atexit
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import tarfile
import threading
import time
import urllib.error
import urllib.request

import lem
import store
import util

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "lemonade-sdk/lemonade"
_ARCH = {"x86_64": "ubuntu-x64", "aarch64": "ubuntu-arm64"}
_run_lock = threading.RLock()
_state = {"state": "stopped", "proc": None, "pid": None, "program": None, "port": None, "error": None, "adopted": False}
UPD = {"state": "idle", "bytes": 0, "total": 0, "version": None, "error": None}


# ---------------------------------------------------------------- where Lemonade lives
def data_dir():
    """Lemonade's working folder: config.json and the backends it downloads. Survives engine updates."""
    return os.path.join(store.data_dir(), "lemonade-data")


def log_path():
    return os.path.join(store.data_dir(), "lemond.log")


def _downloaded_root():
    return os.path.join(store.data_dir(), "lemonade")


def _bundled_dirs():
    cands = [os.environ.get("ZEST_LEMONADE_DIR"), os.path.join(HERE, "..", "..", "lemonade")]  # the second: inside the AppImage
    out = []
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "lemond")):
            out.append(os.path.realpath(c))
    return out


def vkey(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")) or (0,)


def _version_of(directory):
    exe = os.path.join(directory, "lemond")
    try:
        key = f"lemver:{exe}:{os.path.getmtime(exe)}"
    except OSError:
        return None

    def read():
        code, out = util.run([exe, "--version"], 10)
        m = re.search(r"(\d+(?:\.\d+)+)", out)
        return m.group(1) if code == 0 and m else None
    return util.cached(key, 3600, read)


def programs():
    """Every Lemonade this machine can run: the one shipped with Zest plus any downloaded updates."""
    found = []
    for d in _bundled_dirs():
        v = _version_of(d)
        # A bundled copy that can't even report its version is still *there*; starting it will explain why it won't run.
        found.append({"version": v or "unknown", "dir": d, "source": "bundled"})
    root = _downloaded_root()
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            d = os.path.join(root, name)
            if os.path.isfile(os.path.join(d, "lemond")) and not name.startswith("."):
                v = _version_of(d)
                if v:
                    found.append({"version": v, "dir": d, "source": "downloaded"})
    return found


def choose(settings):
    """Which copy to run: a pinned choice if there is one, otherwise the newest (the bundled copy wins ties)."""
    progs = programs()
    if not progs:
        return None
    pin = settings["builtin"].get("version", "")
    if pin == "bundled":
        b = [p for p in progs if p["source"] == "bundled"]
        if b:
            return b[0]
    elif pin:
        m = [p for p in progs if p["version"] == pin]
        if m:
            return m[0]
    return sorted(progs, key=lambda p: (vkey(p["version"]), p["source"] == "bundled"))[-1]


# ---------------------------------------------------------------- running it
def _port_free(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.4).close()
        return False
    except OSError:
        return True


def _seed_config(directory):
    """Lemonade only accepts changes to settings that already exist in config.json, and `llamacpp.device`
    isn't there by default. Adding it empty means Zest's device picker can use it."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "config.json")
    try:
        with open(path) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    if not isinstance(cfg.get("llamacpp"), dict):
        cfg["llamacpp"] = {}
    cfg["llamacpp"].setdefault("device", "")
    # 128k is a good threshold for usefulness in long chats and agentic work. Set it once, so a later change sticks.
    marker = os.path.join(directory, ".zest-ctx-default")
    if not os.path.exists(marker):
        if cfg.get("ctx_size", -1) == -1:
            cfg["ctx_size"] = 131072
        open(marker, "w").close()
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)


def _pidfile():
    return os.path.join(store.data_dir(), "lemond.pid")


def _is_our_lemond(pid, port):
    """True only if `pid` is really a lemond serving `port`, so a recycled pid is never mistaken for it."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv = f.read().split(b"\0")
    except OSError:
        return False
    return bool(argv) and os.path.basename(argv[0]) == b"lemond" and str(port).encode() in argv


def _recorded_pid(port):
    try:
        with open(_pidfile()) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    return pid if _is_our_lemond(pid, port) else None


def _log_tail(n=14):
    try:
        with open(log_path(), errors="replace") as f:
            return [l.rstrip() for l in f.readlines()[-n:]]
    except OSError:
        return []


def diagnose(directory, tail):
    """Turn a start-up failure into something a person can act on."""
    hints = []
    text = "\n".join(tail)
    if re.search(r"GLIBC(XX)?_[\d.]+['`]? not found|version `GLIBC", text):
        hints.append("This version of Linux is too old for the built-in Lemonade, which needs glibc 2.38 or newer "
                     "(for example Ubuntu 24.04, Fedora 39 or Debian 13). You can still connect Zest to a Lemonade you install yourself.")
    code, out = util.run(["ldd", os.path.join(directory, "lemond")], 10)
    missing = re.findall(r"^\s*(\S+) => not found", out, re.M)
    if missing:
        hints.append("Lemonade needs system libraries that aren't installed: " + ", ".join(missing) +
                     ". Install them with your package manager (for example libssl3, libzstd1, libdrm2 and libdrm-amdgpu1 on Ubuntu).")
    return " ".join(hints)


def running():
    p = _state["proc"]
    if p is not None and p.poll() is None:
        return True
    return bool(_state["adopted"] and _state["port"] and lem.healthy("127.0.0.1", _state["port"], _state.get("key")))


def start(settings):
    """Start (or adopt) the private Lemonade. Returns True once it answers requests."""
    with _run_lock:
        if running():
            return True
        _state.update(state="starting", error=None, proc=None, adopted=False)
        prog = choose(settings)
        if not prog:
            return _fail("This copy of Zest doesn't include Lemonade. Use “Download Lemonade” on the Setup page, or connect to a Lemonade you already run.")
        b = settings["builtin"]
        if not b.get("api_key"):
            b["api_key"] = store.new_key()
            store.save(settings)
        key = b["api_key"]
        port = None
        for cand in range(int(b.get("port") or 13307), int(b.get("port") or 13307) + 20):
            if lem.healthy("127.0.0.1", cand, key):          # left over from an earlier Zest: reuse it
                _state.update(state="running", port=cand, key=key, program=prog, adopted=True, pid=_recorded_pid(cand))
                return _connected(settings, cand, key)
            if _port_free(cand):
                port = cand
                break
        if port is None:
            return _fail("Couldn't find a free port for the built-in Lemonade (tried 13307 to 13326).")
        exe = os.path.join(prog["dir"], "lemond")
        data = data_dir()
        _seed_config(data)
        os.makedirs(os.path.dirname(log_path()), exist_ok=True)
        logf = open(log_path(), "wb")
        env = dict(os.environ, LEMONADE_API_KEY=key)
        try:
            proc = subprocess.Popen([exe, data, "--port", str(port), "--no-broadcast"], cwd=data, env=env,
                                    stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as e:
            return _fail(f"Couldn't start Lemonade: {e}")
        finally:
            logf.close()
        _state.update(proc=proc, pid=proc.pid, port=port, key=key, program=prog)
        try:
            with open(_pidfile(), "w") as f:
                f.write(str(proc.pid))
        except OSError:
            pass
        for _ in range(80):
            if proc.poll() is not None:
                tail = _log_tail()
                why = diagnose(prog["dir"], tail)
                return _fail((why + " " if why else "") + "Lemonade stopped right after starting.", tail)
            if lem.healthy("127.0.0.1", port, key):
                b["port"] = port
                return _connected(settings, port, key)
            time.sleep(0.5)
        stop()
        return _fail("Lemonade didn't become ready within 40 seconds.", _log_tail())


def _connected(settings, port, key):
    lem.configure("127.0.0.1", port, key)
    _state.update(state="running", error=None)
    settings["builtin"]["port"] = port
    store.save(settings)
    util.invalidate()
    try:
        import modelscan
        modelscan.apply_to_lemonade(settings)       # models folder and imports are Zest's to manage here
    except Exception:  # noqa: BLE001 - a models-folder problem must not stop the engine from being usable
        pass
    return True


def _fail(message, tail=None):
    _state.update(state="error", error=message, tail=tail or [])
    return False


def _alive(pid, proc):
    if proc is not None:
        return proc.poll() is None
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def stop():
    """Stop the private Lemonade, whether this Zest started it or inherited it from one that crashed."""
    with _run_lock:
        pid, proc = _state.get("pid"), _state["proc"]
        if pid and _alive(pid, proc):
            try:
                os.kill(pid, signal.SIGINT)          # Lemonade's own service unit stops it with SIGINT
                for _ in range(80):
                    if not _alive(pid, proc):
                        break
                    time.sleep(0.1)
                else:
                    try:
                        os.killpg(os.getpgid(pid), signal.SIGKILL)
                    except OSError:
                        os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        if proc is not None:
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        try:
            os.remove(_pidfile())
        except OSError:
            pass
        _state.update(state="stopped", proc=None, pid=None, adopted=False)


atexit.register(stop)


def restart(settings):
    stop()
    util.invalidate()
    return start(settings)


def summary(settings):
    progs = programs()
    active = choose(settings)
    return {
        "available": bool(progs), "state": _state["state"], "running": running(),
        "port": _state["port"], "error": _state["error"], "log_tail": _state.get("tail", []) if _state["state"] == "error" else [],
        "programs": [{"version": p["version"], "source": p["source"],
                      "active": bool(active) and p["version"] == active["version"] and p["source"] == active["source"]} for p in
                     sorted(progs, key=lambda p: vkey(p["version"]), reverse=True)],
        "active": {"version": active["version"], "source": active["source"]} if active else None,
        "pinned": settings["builtin"].get("version", ""),
        "bundled": any(p["source"] == "bundled" for p in progs),
        "arch_supported": platform.machine() in _ARCH,
        "update": dict(UPD),
    }


# ---------------------------------------------------------------- updates
def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Zest", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub answered {e.code}. If this keeps happening, you may be rate-limited; try again later.")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise RuntimeError(f"Couldn't reach GitHub to look for updates ({getattr(e, 'reason', e)}). Check your internet connection.")


def latest_release():
    suffix = _ARCH.get(platform.machine())
    if not suffix:
        raise RuntimeError(f"A built-in Lemonade isn't available for this type of CPU ({platform.machine()}).")
    data = _get_json(f"https://api.github.com/repos/{REPO}/releases/latest")   # stable releases only
    tag = str(data.get("tag_name", ""))
    ver = tag.lstrip("v")
    want = f"lemonade-embeddable-{ver}-{suffix}.tar.gz"
    asset = next((a for a in data.get("assets", []) if a.get("name") == want), None)
    if not asset:
        raise RuntimeError(f"Lemonade {tag} has no built-in download for this computer ({want}).")
    digest = str(asset.get("digest") or "")
    if not digest.startswith("sha256:"):
        raise RuntimeError("GitHub didn't publish a checksum for this release, so Zest won't install it.")
    return {"version": ver, "tag": tag, "url": asset["browser_download_url"], "size": int(asset.get("size", 0)),
            "sha256": digest[7:], "page": data.get("html_url", ""), "published": data.get("published_at", "")}


def check(settings):
    rel = latest_release()
    cur = choose(settings)
    newest_local = max((vkey(p["version"]) for p in programs()), default=(0,))
    return {"latest": rel["version"], "size": rel["size"], "page": rel["page"], "published": rel["published"],
            "current": cur["version"] if cur else None,
            "update_available": vkey(rel["version"]) > newest_local,
            "have_latest": any(p["version"] == rel["version"] for p in programs())}


def _safe_extract(tar_path, dest):
    """Unpack a Lemonade tarball: regular files and folders only, nothing that escapes `dest`."""
    real_dest = os.path.realpath(dest)
    total = 0
    with tarfile.open(tar_path, "r:gz") as tf:
        members = tf.getmembers()
        top = members[0].name.split("/")[0] if members else ""
        for m in members:
            parts = m.name.split("/")
            if m.name.startswith("/") or ".." in parts:
                raise RuntimeError("The download contains an unsafe path, so it was rejected.")
            if not (m.isfile() or m.isdir()):
                continue                           # skip links and device files
            rel = "/".join(parts[1:]) if parts[0] == top else m.name
            if not rel:
                continue
            target = os.path.realpath(os.path.join(dest, rel))
            if not (target == real_dest or target.startswith(real_dest + os.sep)):
                raise RuntimeError("The download contains an unsafe path, so it was rejected.")
            if m.isdir():
                os.makedirs(target, exist_ok=True)
                continue
            total += m.size
            if total > 400 * 1024 * 1024:
                raise RuntimeError("The download is far larger than expected, so it was rejected.")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with tf.extractfile(m) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            os.chmod(target, 0o755 if os.path.basename(rel) in ("lemond", "lemonade") else 0o644)


def _do_update(rel):
    root = _downloaded_root()
    os.makedirs(root, exist_ok=True)
    tmp_tar = os.path.join(root, f".download-{rel['version']}.tar.gz")
    tmp_dir = os.path.join(root, f".unpack-{rel['version']}")
    try:
        UPD.update(state="downloading", bytes=0, total=rel["size"], version=rel["version"], error=None)
        req = urllib.request.Request(rel["url"], headers={"User-Agent": "Zest"})
        sha = hashlib.sha256()
        with urllib.request.urlopen(req, timeout=30) as r, open(tmp_tar, "wb") as out:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                out.write(chunk)
                sha.update(chunk)
                UPD["bytes"] += len(chunk)
        UPD["state"] = "verifying"
        if sha.hexdigest() != rel["sha256"]:
            raise RuntimeError("The download didn't match GitHub's checksum, so it was thrown away. Try again.")
        UPD["state"] = "installing"
        shutil.rmtree(tmp_dir, ignore_errors=True)
        _safe_extract(tmp_tar, tmp_dir)
        exe = os.path.join(tmp_dir, "lemond")
        if not os.path.isfile(exe):
            raise RuntimeError("The download doesn't contain lemond.")
        code, out = util.run([exe, "--version"], 10)
        if code != 0 or rel["version"] not in out:
            why = diagnose(tmp_dir, out.splitlines())
            raise RuntimeError("The new Lemonade won't run on this computer." + (" " + why if why else ""))
        final = os.path.join(root, rel["version"])
        shutil.rmtree(final, ignore_errors=True)
        os.replace(tmp_dir, final)
        # keep the two newest downloaded copies, drop older ones
        have = sorted((d for d in os.listdir(root) if os.path.isfile(os.path.join(root, d, "lemond"))), key=vkey, reverse=True)
        for old in have[2:]:
            shutil.rmtree(os.path.join(root, old), ignore_errors=True)
        util.invalidate()
        UPD["state"] = "done"
    except Exception as e:  # noqa: BLE001
        UPD.update(state="error", error=str(e) or e.__class__.__name__)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        try:
            os.remove(tmp_tar)
        except OSError:
            pass


def start_update():
    if UPD["state"] in ("downloading", "verifying", "installing"):
        return False
    try:
        rel = latest_release()
    except RuntimeError as e:
        UPD.update(state="error", error=str(e))
        return False
    UPD.update(state="downloading", bytes=0, total=rel["size"], version=rel["version"], error=None)
    threading.Thread(target=_do_update, args=(rel,), daemon=True).start()
    return True
