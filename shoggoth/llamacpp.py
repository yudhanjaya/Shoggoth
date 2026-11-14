"""The llama.cpp that Shoggoth sets up and runs for you.

Shoggoth downloads llama.cpp's official Linux builds from its GitHub releases (one build per kind of graphics card),
checks each file against the checksum GitHub publishes, and runs `llama-server` in router mode: one process that loads
and unloads models on request. Per-model settings live in a presets file Shoggoth writes.

Builds live in <data>/llamacpp/<tag>/<backend>/. The two newest tags are kept. The AppImage also carries a processor-only build
(usr/llamacpp/<tag>/cpu), so Shoggoth works offline on first run; it is used only until a downloaded build is there.
"""
import hashlib
import json
import os
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

import store
import util

REPO = "ggml-org/llama.cpp"
NEEDS_GLIBC = (2, 38)                 # what the official ubuntu builds link against (libggml-base needs GLIBC_2.38 on b12375)
HERE = os.path.dirname(os.path.abspath(__file__))
PORTS = range(13340, 13360)
_lock = threading.RLock()
_state = {"proc": None, "pid": None, "port": None, "key": None, "error": None}
UPD = {"state": "idle", "bytes": 0, "total": 0, "tag": None, "backend": None, "error": None}

# Backend → (main asset, runtime asset or None). {t} is the release tag.
ASSETS = {
    "cpu": ("llama-{t}-bin-ubuntu-x64.tar.gz", None),
    "vulkan": ("llama-{t}-bin-ubuntu-vulkan-x64.tar.gz", None),
    "cuda12": ("llama-{t}-bin-ubuntu-cuda-12.8-x64.tar.gz", "cudart-llama-{t}-bin-ubuntu-cuda-12.8-x64.tar.gz"),
    "cuda13": ("llama-{t}-bin-ubuntu-cuda-13.5-x64.tar.gz", "cudart-llama-{t}-bin-ubuntu-cuda-13.5-x64.tar.gz"),
    "rocm": ("llama-{t}-bin-ubuntu-rocm-10.0-x64.tar.gz", None),
}
NAMES = {"cpu": "Processor only", "vulkan": "Vulkan (AMD, Intel, NVIDIA)", "cuda12": "NVIDIA CUDA 12",
         "cuda13": "NVIDIA CUDA 13", "rocm": "AMD ROCm"}
CUDA13_DRIVER = 580                   # CUDA 13.x needs an NVIDIA driver at least this new


# ---------------------------------------------------------------- where things live
def root():
    return os.path.join(store.data_dir(), "llamacpp")


def presets_path():
    return os.path.join(root(), "presets.ini")


def log_path():
    return os.path.join(root(), "llama-server.log")


def _run_file():
    return os.path.join(root(), "run.json")


def models_dir():
    """The folder the router reads models from: the library's view for llama.cpp (links only; see library.sync_views and migrate5)."""
    return os.path.join(store.data_dir(), "views", "llamacpp")


def bundled_root():
    """Where a build that came with Shoggoth lives: SHOGGOTH_LLAMACPP_DIR, or usr/llamacpp inside the AppImage."""
    for c in (os.environ.get("SHOGGOTH_LLAMACPP_DIR"), os.path.join(HERE, "..", "..", "llamacpp")):
        if c and os.path.isdir(c):
            return os.path.realpath(c)
    return None


# ---------------------------------------------------------------- picking a release and a build
def _tag_num(tag):
    m = re.fullmatch(r"b(\d+)", str(tag or ""))
    return int(m.group(1)) if m else None


def pick_release(releases):
    """The newest real build among GitHub's releases. Only tags like b12375 count: llama.cpp also publishes tags such as
    v1.0.0 that hold no builds, and GitHub's "latest release" can point at one of those."""
    good = [r for r in releases if _tag_num(r.get("tag_name")) is not None and not r.get("draft") and r.get("assets")]
    return max(good, key=lambda r: _tag_num(r["tag_name"]), default=None)


def pick_backend(nvidia_driver=None, amd=False):
    """The build for this computer. nvidia_driver: the driver's major version, or None when there is no NVIDIA card."""
    if nvidia_driver:
        return "cuda13" if nvidia_driver >= CUDA13_DRIVER else "cuda12"
    if amd:
        return "vulkan"               # ROCm needs a matching ROCm install; Vulkan works with the stock driver
    return "cpu"


def pick_assets(release, backend):
    """[(name, url, size, sha256)] for the build, or raise if any file is missing or has no published checksum."""
    if backend not in ASSETS:
        raise RuntimeError("Shoggoth doesn't know the %s build." % backend)
    tag = release["tag_name"]
    by_name = {a.get("name"): a for a in release.get("assets", [])}
    out = []
    for pattern in ASSETS[backend]:
        if not pattern:
            continue
        name = pattern.format(t=tag)
        a = by_name.get(name)
        if not a:
            raise RuntimeError("llama.cpp %s has no %s build (%s)." % (tag, NAMES[backend], name))
        digest = str(a.get("digest") or "")
        if not digest.startswith("sha256:"):
            raise RuntimeError("GitHub didn't publish a checksum for %s, so Shoggoth won't install it." % name)
        out.append((name, a["browser_download_url"], int(a.get("size") or 0), digest[7:]))
    return out


def nvidia_driver():
    code, out = util.run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"], 5)
    m = re.match(r"\s*(\d+)", out) if code == 0 else None
    return int(m.group(1)) if m else None


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Shoggoth", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError("GitHub answered %s. If this keeps happening, you may be rate-limited; try again later." % e.code)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise RuntimeError("Couldn't reach GitHub (%s). Check your internet connection." % getattr(e, "reason", e))


def latest():
    rel = pick_release(_get_json("https://api.github.com/repos/%s/releases?per_page=10" % REPO))
    if not rel:
        raise RuntimeError("Couldn't find a llama.cpp release with downloads.")
    return rel


# ---------------------------------------------------------------- installed builds
def builds():
    """Every installed build: [{tag, backend, dir, server, bundled}], newest first; a downloaded build before a bundled one of the same tag."""
    out = []
    for r, bundled in ((root(), False), (bundled_root(), True)):
        if not r or not os.path.isdir(r):
            continue
        for tag in os.listdir(r):
            if _tag_num(tag) is None or not os.path.isdir(os.path.join(r, tag)):
                continue
            for backend in os.listdir(os.path.join(r, tag)):
                d = os.path.join(r, tag, backend)
                exe = os.path.join(d, "llama-server")
                if backend in ASSETS and os.path.isfile(exe):
                    out.append({"tag": tag, "backend": backend, "dir": d, "server": exe, "bundled": bundled})
    return sorted(out, key=lambda b: (_tag_num(b["tag"]), not b["bundled"]), reverse=True)


def choose(settings=None):
    """The build to run: the pinned one if it is still here, else the newest build of the chosen backend, else the newest."""
    cfg = (settings or store.load()).get("llamacpp") or {}
    have = builds()
    pin = (cfg.get("tag"), cfg.get("backend"))
    for b in have:
        if (b["tag"], b["backend"]) == pin:
            return b
    want = cfg.get("backend")
    return (next((b for b in have if b["backend"] == want), None) or next((b for b in have if not b["bundled"]), None)
            or (have[0] if have else None))       # a build Shoggoth downloaded (for this computer's graphics card) before the bundled one


def _glibc():
    try:
        m = re.match(r"(\d+)\.(\d+)", os.confstr("CS_GNU_LIBC_VERSION").split()[-1])
        return (int(m.group(1)), int(m.group(2))) if m else None
    except (AttributeError, OSError, ValueError, IndexError):
        return None


def version_of(exe):
    """'b12375' from `llama-server --version`, or None when it won't run."""
    code, out = util.run([exe, "--version"], 15)
    m = re.search(r"build (\d+)", out)
    return "b" + m.group(1) if m else None


# ---------------------------------------------------------------- installing
def _safe_extract(tar_path, dest, cap=2 * 1024 ** 3):
    """Unpack into `dest`, dropping the archive's top folder. Regular files, folders, and links that stay inside `dest`."""
    real = os.path.realpath(dest)
    inside = lambda p: p == real or p.startswith(real + os.sep)          # noqa: E731
    total, links = 0, []
    with tarfile.open(tar_path, "r:gz") as tf:
        for m in tf.getmembers():
            parts = m.name.split("/")
            if m.name.startswith("/") or ".." in parts:
                raise RuntimeError("The download contains an unsafe path, so it was rejected.")
            rel = "/".join(parts[1:]) if len(parts) > 1 else ""
            if not rel:
                continue
            target = os.path.join(real, rel)
            if not inside(os.path.realpath(os.path.dirname(target))) and os.path.dirname(target) != real:
                raise RuntimeError("The download contains an unsafe path, so it was rejected.")
            if m.isdir():
                os.makedirs(target, exist_ok=True)
            elif m.issym():
                if os.path.isabs(m.linkname) or not inside(os.path.realpath(os.path.join(os.path.dirname(target), m.linkname))):
                    raise RuntimeError("The download contains a link that points outside it, so it was rejected.")
                links.append((m.linkname, target))
            elif m.isfile():
                total += m.size
                if total > cap:
                    raise RuntimeError("The download is far larger than expected, so it was rejected.")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with tf.extractfile(m) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                os.chmod(target, 0o755 if m.mode & 0o111 else 0o644)
    for link, target in links:                       # after the files, so a link never stands where a file goes
        if os.path.lexists(target):
            os.remove(target)
        os.symlink(link, target)


def _download(url, path, sha256, cancel=None):
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": "Shoggoth"})
    with urllib.request.urlopen(req, timeout=30) as r, open(path, "wb") as out:
        while True:
            if cancel is not None and cancel.is_set():
                raise RuntimeError("Cancelled.")
            chunk = r.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)
            h.update(chunk)
            UPD["bytes"] += len(chunk)
    if h.hexdigest() != sha256:
        raise RuntimeError("The download didn't match GitHub's checksum, so it was thrown away. Try again.")


def install(backend, release=None, cancel=None):
    """Download, verify and unpack one build. Returns its builds() entry. Progress is in UPD."""
    rel = release or latest()
    files = pick_assets(rel, backend)
    tag = rel["tag_name"]
    final = os.path.join(root(), tag, backend)
    tmp = final + ".part"
    UPD.update(state="downloading", bytes=0, total=sum(f[2] for f in files), tag=tag, backend=backend, error=None)
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        for name, url, _size, sha in files:
            tgz = os.path.join(tmp, ".dl-" + name)
            _download(url, tgz, sha, cancel)
            UPD["state"] = "installing"
            _safe_extract(tgz, tmp)
            os.remove(tgz)
            UPD["state"] = "downloading"
        exe = os.path.join(tmp, "llama-server")
        if not os.path.isfile(exe):
            raise RuntimeError("The download doesn't contain llama-server.")
        if version_of(exe) != tag:
            g = _glibc()
            if g and g < NEEDS_GLIBC:
                raise RuntimeError("This llama.cpp build needs a newer Linux (glibc %d.%d or later; this computer has %d.%d)."
                                   % (NEEDS_GLIBC + g))
            raise RuntimeError("The new llama.cpp won't start on this computer.")
        shutil.rmtree(final, ignore_errors=True)
        os.replace(tmp, final)
        _prune()
        util.invalidate()
        UPD["state"] = "done"
        return next(b for b in builds() if b["dir"] == final)
    except Exception as e:  # noqa: BLE001
        UPD.update(state="error", error=str(e) or e.__class__.__name__)
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _prune(keep=2):
    """Keep the newest `keep` tags (and never the one running)."""
    running = (_state.get("dir") or "")
    tags = sorted({b["tag"] for b in builds() if not b["bundled"]}, key=_tag_num, reverse=True)
    for tag in tags[keep:]:
        d = os.path.join(root(), tag)
        if not running.startswith(d + os.sep):
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- the presets file
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_SECTION = re.compile(r"^[^\[\]\r\n;#]+$")


def write_presets(models, defaults=None, path=None):
    """Write the router's presets file. models: {model_id: {option: value}}, options as llama-server long names without
    dashes (ctx-size, n-gpu-layers, flash-attn, cache-type-k, mmproj, model ...). True/False become true/false."""
    def val(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        s = str(v)
        if "\n" in s or "\r" in s:
            raise ValueError("A preset value can't span lines.")
        return s

    def block(name, opts):
        if not _SECTION.match(name):
            raise ValueError("Not a usable model name for the presets file: %r" % name)
        lines = ["[%s]" % name]
        for k, v in opts.items():
            if v is None:
                continue
            if not _KEY.match(k):
                raise ValueError("Not a llama-server option name: %r" % k)
            lines.append("%s = %s" % (k, val(v)))
        return lines

    out = ["; Written by Shoggoth. Changes here are replaced; change settings in Shoggoth instead.", "version = 1", ""]
    if defaults:
        out += block("*", defaults) + [""]
    for mid in sorted(models):
        out += block(mid, models[mid]) + [""]
    p = path or presets_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "w") as f:
        f.write("\n".join(out))
    os.replace(p + ".tmp", p)
    return p


def read_presets(path=None):
    """{section: {key: value}} from a presets file (values as strings)."""
    out, cur = {}, None
    try:
        with open(path or presets_path()) as f:
            for line in f:
                line = line.strip()
                if not line or line[0] in ";#":
                    continue
                if line.startswith("[") and line.endswith("]"):
                    cur = out.setdefault(line[1:-1], {})
                elif "=" in line and cur is not None:
                    k, v = line.split("=", 1)
                    cur[k.strip()] = v.strip()
    except OSError:
        pass
    return out


# Defaults every model gets unless its own preset says otherwise: flash attention and an 8-bit cache, which halves the
# memory the conversation takes compared with 16 bits at no noticeable cost in quality.
DEFAULTS = {"flash-attn": "on", "cache-type-k": "q8_0", "cache-type-v": "q8_0", "jinja": True, "ctx-size": 32768}


# ---------------------------------------------------------------- running the router
def _port_free(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
        return False
    except OSError:
        return True


def _start_time(pid):
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _save_run(info):
    os.makedirs(root(), exist_ok=True)
    with open(_run_file() + ".tmp", "w") as f:
        json.dump(info, f)
    os.replace(_run_file() + ".tmp", _run_file())
    os.chmod(_run_file(), 0o600)                     # it holds the server's key


def _load_run():
    try:
        with open(_run_file()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def request(method, path, body=None, timeout=10, port=None, key=None):
    """(status, json) from the router. status 0 means it didn't answer."""
    port, key = port or _state.get("port"), key if key is not None else _state.get("key")
    if not port:
        return 0, {}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=data, method=method,
                                 headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + key} if key else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, OSError, ValueError):
        return 0, {}


def healthy(port=None, key=None):
    return request("GET", "/health", timeout=2, port=port, key=key)[0] == 200


def _adopt():
    """A router a previous Shoggoth started that is still running and answers to its key is reused."""
    info = _load_run()
    pid, port = info.get("pid"), info.get("port")
    if pid and port and _start_time(pid) == info.get("t") and healthy(port, info.get("key")):
        _state.update(proc=None, pid=pid, port=port, key=info.get("key"), dir=info.get("dir"), error=None)
        return True
    return False


def running():
    with _lock:
        if _state["pid"] and (_state["proc"] is None or _state["proc"].poll() is None) and _start_time(_state["pid"]):
            return True
        return _adopt()


def command(build, port, key, models_dir, max_loaded=1, extra=None):
    """The command line that starts the router."""
    cmd = [build["server"], "--host", "127.0.0.1", "--port", str(port), "--api-key", key,
           "--models-dir", models_dir, "--models-preset", presets_path(), "--models-max", str(max_loaded),
           "--no-webui", "--metrics", "--slots"]
    return cmd + list(extra or [])


def start(models_dir, settings=None, ready_timeout=60):
    """Start the router (or adopt the one already running). Returns True when it answers."""
    with _lock:
        if running():
            return True
        b = choose(settings)
        if not b:
            _state["error"] = "llama.cpp isn't set up yet."
            return False
        cfg = (settings or store.load()).get("llamacpp") or {}
        if not os.path.exists(presets_path()):
            write_presets({}, DEFAULTS)
        port = next((p for p in PORTS if _port_free(p)), None)
        if port is None:
            _state["error"] = "No free port for llama.cpp."
            return False
        key = store.new_key()
        os.makedirs(models_dir, exist_ok=True)
        log = open(log_path(), "ab")
        env = dict(os.environ)
        env["LD_LIBRARY_PATH"] = b["dir"] + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
        env["LLAMA_CACHE"] = os.path.join(root(), "cache")      # its own cache, so models other tools fetched into ~/.cache/llama.cpp stay theirs
        env.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")          # number NVIDIA cards the way nvidia-smi does
        extra = ["--device", cfg["device"]] if cfg.get("device") else []
        proc = subprocess.Popen(command(b, port, key, models_dir, max_loaded(cfg), extra), stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, cwd=b["dir"], env=env, start_new_session=True)
        log.close()
        _state.update(proc=proc, pid=proc.pid, port=port, key=key, dir=b["dir"], error=None)
        end = time.time() + ready_timeout
        while time.time() < end:
            if proc.poll() is not None:
                _state.update(proc=None, pid=None, error="llama.cpp stopped right after starting. " + _log_tail())
                return False
            if healthy(port, key):
                _save_run({"pid": proc.pid, "port": port, "key": key, "t": _start_time(proc.pid), "dir": b["dir"], "tag": b["tag"]})
                return True
            time.sleep(0.3)
        stop()
        _state["error"] = "llama.cpp didn't become ready in time."
        return False


def stop(grace=20):
    """Stop the router Shoggoth started, and the model processes it started."""
    with _lock:
        pid, proc = _state.get("pid"), _state.get("proc")
        if not pid:
            return
        t = _start_time(pid)
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            pass
        end = time.time() + grace
        while time.time() < end and _start_time(pid) == t and t is not None:
            if proc is not None and proc.poll() is not None:
                break
            time.sleep(0.1)
        if _start_time(pid) == t and t is not None:
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass
        _state.update(proc=None, pid=None, port=None, key=None)
        try:
            os.remove(_run_file())
        except OSError:
            pass


def _log_tail(n=6):
    try:
        with open(log_path(), errors="replace") as f:
            return " ".join(f.readlines()[-n:]).strip()[-600:]
    except OSError:
        return ""


# ---------------------------------------------------------------- models
def models():
    """[{id, status, ctx, args, failed}] from the router's own list."""
    status, d = request("GET", "/models", timeout=10)
    out = []
    for m in (d.get("data") or []) if status == 200 else []:
        st = m.get("status") or {}
        out.append({"id": m.get("id"), "status": st.get("value", "unloaded"), "args": st.get("args") or [],
                    "ctx": ctx_from_args(st.get("args") or []), "failed": bool(st.get("failed")), "path": m.get("path", ""),
                    "inputs": ((m.get("architecture") or {}).get("input_modalities") or ["text"])})
    return out


def ctx_from_args(args):
    for i, a in enumerate(args):
        if a in ("-c", "--ctx-size", "-ctx") and i + 1 < len(args):
            try:
                return int(args[i + 1])
            except ValueError:
                return 0
        if a.startswith("--ctx-size="):
            try:
                return int(a.split("=", 1)[1])
            except ValueError:
                return 0
    return 0


def reload():
    """Ask the router to read its models folder and presets again."""
    return request("GET", "/models?reload=1", timeout=30)[0] == 200


def load(model_id, timeout=900):
    """Load a model and wait until it is ready. Returns (ok, message)."""
    status, d = request("POST", "/models/load", {"model": model_id}, timeout=30)
    if status != 200:
        return False, _err(d, "llama.cpp couldn't load %s." % model_id)
    end = time.time() + timeout
    while time.time() < end:
        m = next((m for m in models() if m["id"] == model_id), None)
        if m and m["status"] == "loaded":
            return True, ""
        if m and m["failed"] and m["status"] == "unloaded":
            return False, "llama.cpp couldn't load %s. %s" % (model_id, _log_tail())
        time.sleep(0.5)
    return False, "%s took too long to load." % model_id


def unload(model_id):
    status, d = request("POST", "/models/unload", {"model": model_id}, timeout=60)
    return (True, "") if status == 200 else (False, _err(d, "llama.cpp couldn't free %s." % model_id))


def _err(d, fallback):
    e = d.get("error") if isinstance(d, dict) else None
    if isinstance(e, dict):
        return e.get("message") or fallback
    return str(e) if e else fallback


def endpoint():
    """Where chat requests go, or None when the router isn't running."""
    if not running():
        return None
    return {"base_url": "http://127.0.0.1:%d/v1" % _state["port"], "key": _state["key"]}


# ---------------------------------------------------------------- what the rest of Shoggoth asks
def max_loaded(cfg=None):
    """The "keep up to N models loaded" setting (default 1, at most 8)."""
    cfg = cfg if cfg is not None else (store.load().get("llamacpp") or {})
    try:
        return max(1, min(8, int(cfg.get("max_loaded") or 1)))
    except (TypeError, ValueError):
        return 1


def own_ports():
    """Ports Shoggoth's llama.cpp listens on: the router's and each loaded model's (the router starts one process per model), so a scan
    of this computer never offers them as somebody else's server."""
    if not running():
        return set()
    out = {_state["port"]}
    for m in models():
        a = m.get("args") or []
        for i, x in enumerate(a[:-1]):
            if x == "--port" and str(a[i + 1]).isdigit() and int(a[i + 1]):
                out.add(int(a[i + 1]))
    return out


def loaded_ids():
    """The models in memory right now."""
    return [m["id"] for m in models() if m["status"] == "loaded"] if running() else []


def restart(settings=None):
    """Stop the router and start it again (a new build, backend or device). Models load again on their next use."""
    stop()
    return start(models_dir(), settings)


def start_update(backend=None, release=None):
    """Download a build in the background (the newest, for `backend` or the one in use). False when one is already downloading."""
    if UPD["state"] in ("downloading", "installing"):
        return False
    s = store.load()
    cur = choose(s)
    want = backend or (s.get("llamacpp") or {}).get("backend") or (cur or {}).get("backend") or recommended()
    UPD.update(state="downloading", bytes=0, total=0, tag=None, backend=want, error=None)

    def run():
        try:
            b = install(want, release)
            store.update(lambda st: st.setdefault("llamacpp", {}).update(backend=b["backend"], tag=""))
        except Exception as e:  # noqa: BLE001 - install() already put the reason in UPD
            UPD.update(state="error", error=UPD.get("error") or str(e) or e.__class__.__name__)
    threading.Thread(target=run, daemon=True).start()
    return True


def recommended():
    """The build for this computer's graphics card."""
    amd = False
    try:
        import hardware
        amd = any(g.get("vendor") == "AMD" for g in hardware.detect_gpus())
    except Exception:  # noqa: BLE001
        pass
    return pick_backend(nvidia_driver(), amd)


def summary(settings=None):
    """What the page shows about Shoggoth's llama.cpp: {installed, running, port, tag, backend, backend_name, bundled, pinned, builds,
    backends, recommended, max_loaded, device, error, update, log}."""
    s = settings or store.load()
    cfg = s.get("llamacpp") or {}
    b = choose(s)
    up = running()
    return {"installed": bool(b), "running": up, "port": _state["port"] if up else None,
            "tag": (b or {}).get("tag", ""), "backend": (b or {}).get("backend", ""), "backend_name": NAMES.get((b or {}).get("backend"), ""),
            "bundled": bool((b or {}).get("bundled")), "pinned": cfg.get("tag") or "",
            "builds": [{"tag": x["tag"], "backend": x["backend"], "name": NAMES[x["backend"]], "bundled": x["bundled"]} for x in builds()],
            "backends": [{"id": k, "name": NAMES[k]} for k in ASSETS], "recommended": util.cached("llamacpp-rec", 300, recommended),
            "max_loaded": max_loaded(cfg), "device": cfg.get("device", ""), "error": _state.get("error"),
            "update": dict(UPD), "log": log_path()}


_BUF = re.compile(r"(?:model buffer size|compute buffer size)\s*=\s*([\d.]+) MiB|llama_kv_cache: size\s*=\s*([\d.]+) MiB")


def instance_port(model_id):
    """The port the router gave the process serving `model_id`, or None."""
    m = next((m for m in models() if m["id"] == model_id), None)
    a = (m or {}).get("args") or []
    for i, x in enumerate(a[:-1]):
        if x == "--port" and str(a[i + 1]).isdigit():
            return int(a[i + 1])
    return None


def measured_gb(model_id, text=None):
    """What loading `model_id` really took, in GB: its model buffers, the conversation cache and the compute buffers, read from the lines
    the router copies from that model's process into its log (each starts with "[port]"). None when the log doesn't say."""
    port = instance_port(model_id) if text is None else None
    if text is None:
        if not port:
            return None
        try:
            with open(log_path(), "rb") as f:
                f.seek(max(0, os.path.getsize(log_path()) - 2_000_000))
                text = f.read().decode("utf8", "replace")
        except OSError:
            return None
    mib = 0.0
    tag = "[%d]" % port if port else ""
    for line in text.splitlines():
        if tag and not line.lstrip().startswith(tag):
            continue
        m = _BUF.search(line)
        if m:
            mib += float(m.group(1) or m.group(2))
    return round(mib / 1024, 2) if mib else None
