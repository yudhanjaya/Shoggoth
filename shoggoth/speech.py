"""Speech to text with whisper.cpp.

Shoggoth runs `whisper-server` on 127.0.0.1 when someone talks to it and stops it after 5 idle minutes.

Where the program comes from, first match wins:
  1. settings: {"speech": {"server": "/path/to/whisper-server"}}, or the SHOGGOTH_WHISPER_SERVER variable
  2. a build in <data>/whisper/<tag>/<backend>/ (install_cpu() puts the official processor-only build there;
     GPU builds come from packaging/build-whisper.sh, because whisper.cpp publishes no Linux GPU build)
  3. whisper-server on PATH

Models come from the ggerganov/whisper.cpp repo on Hugging Face, sha256-checked: large-v3-turbo (q5_0) when the build
can use a graphics card, base.en otherwise.
"""
import email
import email.policy
import io
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave
import hashlib

import hf
import llamacpp
import store
import util

REPO = "ggml-org/whisper.cpp"
MODEL_REPO = "ggerganov/whisper.cpp"
MODELS = {"gpu": "ggml-large-v3-turbo-q5_0.bin", "cpu": "ggml-base.en.bin"}
IDLE_SECONDS = 300
MAX_AUDIO = 100 * 1024 * 1024
GPU_BACKENDS = ("cuda12", "cuda13", "cuda", "vulkan", "rocm")
UPD = {"state": "idle", "bytes": 0, "total": 0, "tag": None, "what": None, "error": None}

_lock = threading.RLock()
_state = {"proc": None, "port": None, "last": 0.0, "model": None, "exe": None, "watch": None, "error": None}


class SpeechError(Exception):
    """str(e) is plain English, fit to show the user. `status` is the HTTP status a route should answer with."""
    def __init__(self, message, status=500):
        Exception.__init__(self, message)
        self.status = status


# ---------------------------------------------------------------- where things live
def root():
    return os.path.join(store.data_dir(), "whisper")


def models_dir():
    return os.path.join(root(), "models")


def log_path():
    return os.path.join(root(), "whisper-server.log")


def _settings():
    s = store.load().get("speech")
    return s if isinstance(s, dict) else {}


# ---------------------------------------------------------------- picking a release
def _asset_name():
    import platform
    return "whisper-bin-ubuntu-arm64.tar.gz" if platform.machine().lower() in ("aarch64", "arm64") else "whisper-bin-ubuntu-x64.tar.gz"


def pick_release(releases):
    """The newest release (by date) that has the Linux processor build. whisper.cpp mixes tags like b5454 and v1.9.2, and
    some tags hold no files, so go by the files."""
    name = _asset_name()
    good = [r for r in releases if not r.get("draft") and any(a.get("name") == name for a in r.get("assets", []))]
    return max(good, key=lambda r: r.get("published_at") or "", default=None)


def pick_asset(release):
    """(name, url, size, sha256). Refuses a file GitHub published no checksum for."""
    name = _asset_name()
    a = next((a for a in release.get("assets", []) if a.get("name") == name), None)
    if not a:
        raise RuntimeError("whisper.cpp %s has no Linux build (%s)." % (release.get("tag_name"), name))
    digest = str(a.get("digest") or "")
    if not digest.startswith("sha256:"):
        raise RuntimeError("GitHub didn't publish a checksum for %s, so Shoggoth won't install it." % name)
    return name, a["browser_download_url"], int(a.get("size") or 0), digest[7:]


def latest():
    rel = pick_release(llamacpp._get_json("https://api.github.com/repos/%s/releases?per_page=10" % REPO))
    if not rel:
        raise RuntimeError("Couldn't find a whisper.cpp release with a Linux download.")
    return rel


# ---------------------------------------------------------------- installed builds
def builds():
    """Installed builds: [{tag, backend, dir, server}], newest first."""
    out, r = [], root()
    if not os.path.isdir(r):
        return out
    for tag in sorted(os.listdir(r), reverse=True):
        for backend in sorted(os.listdir(os.path.join(r, tag))) if os.path.isdir(os.path.join(r, tag)) and tag != "models" else []:
            d = os.path.join(r, tag, backend)
            exe = os.path.join(d, "whisper-server")
            if os.path.isfile(exe) and os.access(exe, os.X_OK):
                out.append({"tag": tag, "backend": backend, "dir": d, "server": exe})
    return out


def find_server():
    """{'server', 'backend', 'source'} or None. backend is 'cpu' or a GPU kind, or 'unknown' for a program Shoggoth didn't unpack."""
    path = _settings().get("server") or os.environ.get("SHOGGOTH_WHISPER_SERVER")
    if path:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return {"server": path, "backend": _settings().get("backend") or "unknown", "source": "settings"}
        return None                                  # a path that is wrong is an error to show, not to paper over
    have = builds()
    gpu = next((b for b in have if b["backend"] in GPU_BACKENDS), None)
    pick = gpu or (have[0] if have else None)
    if pick:
        return {"server": pick["server"], "backend": pick["backend"], "source": "shoggoth"}
    found = shutil.which("whisper-server")
    return {"server": found, "backend": "unknown", "source": "path"} if found else None


def choose_model(backend):
    """Model file name for this build: the big one when it can use a card, base.en otherwise."""
    want = _settings().get("model")
    if want:
        return want
    if backend in GPU_BACKENDS or (backend == "unknown" and llamacpp.nvidia_driver()):
        return MODELS["gpu"]
    return MODELS["cpu"]


# ---------------------------------------------------------------- installing the processor build and the model
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


def install_cpu(release=None, cancel=None):
    """Download, verify and unpack whisper.cpp's official processor-only build. Returns its builds() entry."""
    rel = release or latest()
    name, url, size, sha = pick_asset(rel)
    tag = rel["tag_name"]
    final = os.path.join(root(), tag, "cpu")
    tmp = final + ".part"
    UPD.update(state="downloading", bytes=0, total=size, tag=tag, what="speech engine", error=None)
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        tgz = os.path.join(tmp, ".dl-" + name)
        _download(url, tgz, sha, cancel)
        UPD["state"] = "installing"
        llamacpp._safe_extract(tgz, tmp)
        os.remove(tgz)
        exe = os.path.join(tmp, "whisper-server")
        if not os.path.isfile(exe):
            raise RuntimeError("The download doesn't contain whisper-server.")
        code, out = util.run([exe, "--help"], 15)
        if "usage" not in out.lower():
            g = llamacpp._glibc()
            raise RuntimeError("The speech engine won't start on this computer%s." %
                               (" (it needs a newer Linux than glibc %d.%d)" % g if g and g < llamacpp.NEEDS_GLIBC else ""))
        shutil.rmtree(final, ignore_errors=True)
        os.replace(tmp, final)
        util.invalidate()
        UPD["state"] = "done"
        return next(b for b in builds() if b["dir"] == final)
    except Exception as e:  # noqa: BLE001
        UPD.update(state="error", error=str(e) or e.__class__.__name__)
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def model_entry(filename):
    """{'name', 'size', 'sha256'} for a file in the Hugging Face repo."""
    tree = hf._get_json("%s/api/models/%s/tree/main" % (hf.BASE, MODEL_REPO))
    for item in tree if isinstance(tree, list) else []:
        if item.get("path") == filename:
            lfs = item.get("lfs") or {}
            sha = (lfs.get("oid") or "").lower()
            if not sha:
                raise hf.HFError("Hugging Face published no checksum for %s, so Shoggoth won't use it." % filename, final=True)
            return {"name": filename, "size": int(item.get("size") or lfs.get("size") or 0), "sha256": sha}
    raise hf.HFError("Hugging Face has no speech model called %s." % filename, final=True)


def ensure_model(filename, cancel=None):
    """The path of the model file, downloading it (checked against its sha256) when it isn't there. A model named
    by a full path in settings is used as it is."""
    if os.path.isabs(filename):
        if not os.path.isfile(filename):
            raise SpeechError("The speech model %s isn't there." % filename, 500)
        return filename
    hf._safe_name(filename)
    dest = os.path.join(models_dir(), filename)
    if os.path.isfile(dest) and os.path.getsize(dest) > 0:
        return dest
    entry = model_entry(filename)
    UPD.update(state="downloading", bytes=0, total=entry["size"], tag=None, what="speech model", error=None)

    def tick(n):
        UPD["bytes"] = n
    try:
        hf._fetch_one(MODEL_REPO, entry, dest, tick, cancel)
    except Exception as e:  # noqa: BLE001
        UPD.update(state="error", error=str(e))
        raise
    UPD["state"] = "done"
    return dest


def start_setup(model=None):
    """Install whisper.cpp's processor build and the model it uses, in the background: progress is in UPD, which
    status() returns as "download". False when a download is already running."""
    with _lock:
        if UPD["state"] in ("downloading", "installing"):
            return False
        UPD.update(state="downloading", bytes=0, total=0, tag=None, what="speech engine", error=None)

    def run():
        try:
            build = install_cpu()
            ensure_model(model or choose_model(build["backend"]))
        except Exception as e:  # noqa: BLE001 - install_cpu() and ensure_model() put the reason in UPD when they can
            UPD.update(state="error", error=UPD.get("error") or str(e) or e.__class__.__name__)

    threading.Thread(target=run, daemon=True).start()
    return True


# ---------------------------------------------------------------- running whisper-server
def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def running():
    p = _state["proc"]
    return bool(p and p.poll() is None)


def _http(method, path, data=None, headers=None, timeout=10):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (_state["port"], path), data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def _log_tail(n=4):
    try:
        with open(log_path(), errors="replace") as f:
            lines = [l.strip() for l in f.read().splitlines() if l.strip()]
        return " ".join(lines[-n:])[-300:]
    except OSError:
        return ""


def command(exe, model, port, backend="unknown"):
    cmd = [exe, "--host", "127.0.0.1", "--port", str(port), "-m", model, "-t", str(max(2, min(8, (os.cpu_count() or 4) // 2))),
           "-l", "en" if ".en" in os.path.basename(model) else "auto", "-nt"]
    if backend == "cpu":
        cmd.append("-ng")
    return cmd


def start(ready_timeout=120):
    """Start whisper-server if it isn't up. Raises SpeechError with something the person can act on."""
    with _lock:
        if running():
            _state["last"] = time.time()
            return _state["port"]
        found = find_server()
        if not found:
            if (_settings().get("server") or os.environ.get("SHOGGOTH_WHISPER_SERVER")):
                raise SpeechError("The whisper-server set in Shoggoth's settings isn't there or can't be run.", 500)
            raise SpeechError("Speech to text isn't set up yet: whisper-server is not installed.", 503)
        model = ensure_model(choose_model(found["backend"]))
        port = _free_port()
        os.makedirs(root(), exist_ok=True)
        log = open(log_path(), "ab")
        try:
            proc = subprocess.Popen(command(found["server"], model, port, found["backend"]), stdout=log, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, cwd=os.path.dirname(found["server"]), start_new_session=True)
        except OSError as e:
            raise SpeechError("whisper-server won't start: %s" % e, 500)
        finally:
            log.close()
        _state.update(proc=proc, port=port, last=time.time(), model=os.path.basename(model), exe=found["server"], error=None)
        deadline = time.time() + ready_timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                _state["proc"] = None
                raise SpeechError("whisper-server stopped right after starting. %s" % _log_tail(), 500)
            try:
                _http("GET", "/", timeout=2)
                break
            except urllib.error.HTTPError:
                break                                # it answered, so it is up
            except (urllib.error.URLError, OSError):
                time.sleep(0.2)
        else:
            stop()
            raise SpeechError("whisper-server didn't become ready in time.", 504)
        if not _state["watch"] or not _state["watch"].is_alive():
            _state["watch"] = threading.Thread(target=_idle_watch, daemon=True)
            _state["watch"].start()
        return port


def stop():
    with _lock:
        p, _state["proc"] = _state["proc"], None
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(5)


def _idle_watch():
    while True:
        time.sleep(min(5, max(0.05, IDLE_SECONDS / 10.0)))
        with _lock:
            if not running():
                return
            if time.time() - _state["last"] >= IDLE_SECONDS and not _state.get("busy"):
                stop()
                return


def status():
    found = find_server()
    runs_on = None
    if found:
        runs_on = "Runs on the graphics card" if found["backend"] in GPU_BACKENDS else "Runs on the processor"
    return {"installed": bool(found), "server": found and found["server"], "backend": found and found["backend"],
            "model": choose_model(found["backend"]) if found else None, "running": running(), "port": _state["port"] if running() else None,
            "runs_on": runs_on, "download": dict(UPD)}


# ---------------------------------------------------------------- audio
def _is_wav(b):
    return b[:4] == b"RIFF" and b[8:12] == b"WAVE"


def _wav_ok(b):
    """True for 16 kHz, 16-bit, mono PCM: what whisper-server reads without help."""
    try:
        with wave.open(io.BytesIO(b)) as w:
            return w.getframerate() == 16000 and w.getsampwidth() == 2 and w.getnchannels() == 1
    except (wave.Error, EOFError):
        return False


def to_wav(audio, content_type=""):
    """16 kHz mono WAV bytes from whatever came in. 16 kHz WAV passes through; anything else needs ffmpeg."""
    if not audio:
        raise SpeechError("No audio was sent.", 400)
    if len(audio) > MAX_AUDIO:
        raise SpeechError("That recording is too large.", 413)
    if _is_wav(audio) and _wav_ok(audio):
        return audio
    ff = shutil.which("ffmpeg")
    if not ff:
        kind = "WAV at a different sample rate" if _is_wav(audio) else (content_type.split(";")[0].strip() or "this audio format")
        raise SpeechError("Shoggoth can't read %s without ffmpeg, which isn't installed. The page should send 16 kHz WAV, "
                          "or install ffmpeg." % kind, 415)
    with tempfile.TemporaryDirectory(prefix="shoggoth-speech-") as d:
        src, dst = os.path.join(d, "in"), os.path.join(d, "out.wav")
        with open(src, "wb") as f:
            f.write(audio)
        code, out = util.run([ff, "-nostdin", "-v", "error", "-i", src, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav", dst], 120)
        if code != 0 or not os.path.isfile(dst):
            raise SpeechError("ffmpeg couldn't read that audio. %s" % out.strip()[-200:], 415)
        with open(dst, "rb") as f:
            return f.read()


def _multipart(fields, filename, data):
    b = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (b, k, v)).encode())
    parts.append(('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\nContent-Type: audio/wav\r\n\r\n'
                  % (b, filename)).encode() + data + b"\r\n")
    parts.append(("--%s--\r\n" % b).encode())
    return b"".join(parts), "multipart/form-data; boundary=" + b


def transcribe(audio_bytes, content_type="audio/wav", language=None, prompt=None):
    """Audio in, {"text": ...} out. Raises SpeechError."""
    wav = to_wav(audio_bytes, content_type)
    with _lock:
        _state["busy"] = _state.get("busy", 0) + 1
    try:
        for attempt in (0, 1):
            port = start()
            fields = {"response_format": "json", "temperature": "0.0"}
            if language:
                fields["language"] = language
            if prompt:
                fields["prompt"] = prompt
            body, ctype = _multipart(fields, "audio.wav", wav)
            try:
                _, raw = _http("POST", "/inference", body, {"Content-Type": ctype}, timeout=300)
                break
            except urllib.error.HTTPError as e:
                detail = e.read()[:200].decode("utf-8", "replace")
                raise SpeechError("The speech engine refused the audio (%s). %s" % (e.code, detail), 502)
            except (urllib.error.URLError, OSError):
                if attempt:                                  # the server died or was stopped: one fresh start, then give up
                    raise SpeechError("The speech engine stopped answering. %s" % _log_tail(), 502)
                stop()
        try:
            data = json.loads(raw)
        except ValueError:
            raise SpeechError("The speech engine sent something Shoggoth couldn't read.", 502)
        if not isinstance(data, dict) or data.get("error"):
            raise SpeechError("The speech engine said: %s" % (data.get("error") if isinstance(data, dict) else "no answer"), 502)
        return {"text": re.sub(r"\s+", " ", str(data.get("text") or "")).strip()}
    finally:
        with _lock:
            _state["busy"] -= 1
            _state["last"] = time.time()


# ---------------------------------------------------------------- handlers for the routes
def ui_transcribe(body, content_type=""):
    """For POST /ui/speech/transcribe: the request body is the audio itself, Content-Type says what it is.
    Returns (status, dict)."""
    try:
        return 200, transcribe(body, content_type)
    except SpeechError as e:
        return e.status, {"error": str(e)}
    except (hf.HFError, RuntimeError) as e:
        return 503, {"error": str(e)}


def parse_multipart(body, content_type):
    """{field: str} and the file part: ({fields}, filename, bytes, part content-type). Raises SpeechError."""
    if not content_type.lower().startswith("multipart/form-data"):
        raise SpeechError("Send the audio as multipart/form-data with a 'file' field.", 400)
    msg = email.message_from_bytes(b"Content-Type: " + content_type.encode("latin-1", "replace") + b"\r\n\r\n" + body,
                                   policy=email.policy.HTTP)
    if not msg.is_multipart():
        raise SpeechError("That multipart body couldn't be read.", 400)
    fields, audio, fname, ctype = {}, None, None, ""
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        if name == "file":
            audio, fname, ctype = payload, part.get_filename() or "", part.get_content_type()
        else:
            fields[name] = payload.decode("utf-8", "replace").strip()
    if audio is None:
        raise SpeechError("The request has no 'file' field.", 400)
    return fields, fname, audio, ctype


def openai_transcriptions(body, headers):
    """For POST /v1/audio/transcriptions, shaped like OpenAI's. body: the raw request bytes; headers: a dict or
    http.server headers (Content-Type is read case-insensitively). Returns (status, content_type, response_bytes).
    Supports response_format json (default), text and verbose_json (text only, no segments); `model` is ignored."""
    def err(status, msg):
        return status, "application/json", json.dumps({"error": {"message": msg, "type": "invalid_request_error" if status < 500 else "server_error"}}).encode()
    ctype = ""
    for k in (headers or {}):
        if str(k).lower() == "content-type":
            ctype = headers[k]
    try:
        fields, fname, audio, part_type = parse_multipart(body, ctype)
        fmt = fields.get("response_format") or "json"
        if fmt not in ("json", "text", "verbose_json"):
            return err(400, "response_format %s isn't supported here; use json, text or verbose_json." % fmt)
        guess = part_type if part_type and part_type != "application/octet-stream" else ("audio/wav" if fname.lower().endswith(".wav") else part_type)
        out = transcribe(audio, guess, language=fields.get("language") or None, prompt=fields.get("prompt") or None)
    except SpeechError as e:
        return err(e.status, str(e))
    except (hf.HFError, RuntimeError) as e:
        return err(503, str(e))
    if fmt == "text":
        return 200, "text/plain; charset=utf-8", (out["text"] + "\n").encode()
    if fmt == "verbose_json":
        out = {"task": "transcribe", "text": out["text"]}
    return 200, "application/json", json.dumps(out).encode()
