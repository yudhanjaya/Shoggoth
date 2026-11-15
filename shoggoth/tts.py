"""Read aloud, with Piper (text to speech).

Bark was tried first. bark.cpp builds and runs on the processor here, but it publishes no Linux build (its GitHub
releases carry no files) and its weights exist only as PyTorch checkpoints that have to be converted with a script
that needs torch. Shoggoth downloads official builds and doesn't run PyTorch, so Bark isn't available and Piper is used
instead. The page says so where the switch is.

Piper is a small program with its own Linux builds and voices:
  - the program comes from github.com/rhasspy/piper/releases (2023.11.14-2). GitHub published no checksums for those
    files, so their sha256 was checked by hand and is kept in TARBALLS below.
  - voices come from rhasspy/piper-voices on Hugging Face, where each .onnx file has a sha256, checked like a model.
    The small .json beside each voice has no published checksum, so its sha256 is kept in CONFIGS below.

Where the program comes from, first match wins:
  1. settings: {"tts": {"exe": "/path/to/piper"}}, or the SHOGGOTH_PIPER variable
  2. a build in <data>/piper/<tag>/ (install() puts it there)
  3. piper on PATH

One piper process serves every request: it reads a line of JSON per request and prints the path of the WAV it finished,
which is how a request is known to be done. It starts on the first thing read aloud and stops after 5 idle minutes.
Read aloud is off until someone turns it on: speak() refuses then, and nothing is downloaded or started.
"""
import hashlib
import json
import os
import platform
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request

import hf
import llamacpp
import store
import util

TAG = "2023.11.14-2"
REPO = "rhasspy/piper-voices"
IDLE_SECONDS = 300
MAX_TEXT = 20000
SPEAK_TIMEOUT = 300
TARBALLS = {
    "x86_64": ("piper_linux_x86_64.tar.gz", "a50cb45f355b7af1f6d758c1b360717877ba0a398cc8cbe6d2a7a3a26e225992"),
    "amd64": ("piper_linux_x86_64.tar.gz", "a50cb45f355b7af1f6d758c1b360717877ba0a398cc8cbe6d2a7a3a26e225992"),
    "aarch64": ("piper_linux_aarch64.tar.gz", "fea0fd2d87c54dbc7078d0f878289f404bd4d6eea6e7444a77835d1537ab88eb"),
    "arm64": ("piper_linux_aarch64.tar.gz", "fea0fd2d87c54dbc7078d0f878289f404bd4d6eea6e7444a77835d1537ab88eb"),
    "armv7l": ("piper_linux_armv7l.tar.gz", "c6946fcd57c705ed1d4666ea880f80ba0bbbd14de62ecbdd13460baf3bac8e37"),
}
VOICES = {                                   # the voices Shoggoth offers: name -> its place in the Hugging Face repo
    "en_US-lessac-medium": "en/en_US/lessac/medium/en_US-lessac-medium.onnx",
    "en_US-amy-medium": "en/en_US/amy/medium/en_US-amy-medium.onnx",
    "en_GB-alan-medium": "en/en_GB/alan/medium/en_GB-alan-medium.onnx",
    "en_US-ryan-high": "en/en_US/ryan/high/en_US-ryan-high.onnx",
}
CONFIGS = {                                  # sha256 of each voice's small .json, which Hugging Face gives no checksum for
    "en_US-lessac-medium.onnx.json": "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0",
    "en_US-amy-medium.onnx.json": "8c9b8aa3ee122c6a63678bd68e586a067c6775116d32e13b7ec919520d8948bf",
    "en_GB-alan-medium.onnx.json": "c0f0d124e5895c00e7c03b35dcc8287f319a6998a365b182deb5c8e752ee8c1e",
    "en_US-ryan-high.onnx.json": "c6d3b98f08315cb4bebf0d49d50fc4ff491b503c64b940cd3d5ca28543b48011",
}
DEFAULT_VOICE = "en_US-lessac-medium"
UPD = {"state": "idle", "bytes": 0, "total": 0, "what": None, "error": None}

_lock = threading.RLock()
_speaking = threading.Lock()
_state = {"proc": None, "voice": None, "exe": None, "last": 0.0, "watch": None, "lines": None, "busy": 0}


class TtsError(Exception):
    """str(e) is plain English, fit to show the user. `status` is the HTTP status a route should answer with."""
    def __init__(self, message, status=500):
        Exception.__init__(self, message)
        self.status = status


# ---------------------------------------------------------------- where things live
def root():
    return os.path.join(store.data_dir(), "piper")


def voices_dir():
    return os.path.join(root(), "voices")


def log_path():
    return os.path.join(root(), "piper.log")


def _settings():
    s = store.load().get("tts")
    return s if isinstance(s, dict) else {}


def enabled():
    """Read aloud is off until someone turns it on; nothing is downloaded or started before then."""
    return bool(_settings().get("enabled"))


def choose_voice():
    want = str(_settings().get("voice") or "")
    return want if want in VOICES else DEFAULT_VOICE


# ---------------------------------------------------------------- installed builds
def builds():
    """Installed builds: [{tag, dir, exe}], newest first."""
    out, r = [], root()
    if not os.path.isdir(r):
        return out
    for tag in sorted(os.listdir(r), reverse=True):
        d = os.path.join(r, tag)
        if tag == "voices" or not os.path.isdir(d):
            continue
        exe = os.path.join(d, "piper")
        if os.path.isfile(exe) and os.access(exe, os.X_OK):
            out.append({"tag": tag, "dir": d, "exe": exe})
    return out


def find_exe():
    """{'exe', 'source'} or None. A program the person pointed at is used as it is; a wrong path is an error to show, not paper over."""
    path = _settings().get("exe") or os.environ.get("SHOGGOTH_PIPER")
    if path:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return {"exe": path, "source": "settings"}
        return None
    have = builds()
    if have:
        return {"exe": have[0]["exe"], "source": "shoggoth"}
    found = shutil.which("piper")
    return {"exe": found, "source": "path"} if found else None


# ---------------------------------------------------------------- installing the program
def asset():
    """(name, url, sha256) for this computer's processor."""
    kind = platform.machine().lower()
    if kind not in TARBALLS:
        raise TtsError("There is no Piper build for this kind of computer (%s)." % (kind or "unknown"), 400)
    name, sha = TARBALLS[kind]
    return name, "https://github.com/rhasspy/piper/releases/download/%s/%s" % (TAG, name), sha


def _download(url, path, sha256, cancel=None):
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": "Shoggoth"})
    with urllib.request.urlopen(req, timeout=30) as r, open(path, "wb") as out:
        while True:
            if cancel is not None and cancel.is_set():
                raise RuntimeError("Download cancelled.")
            chunk = r.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)
            h.update(chunk)
            UPD["bytes"] += len(chunk)
    if h.hexdigest() != sha256:
        raise RuntimeError("The download didn't match the checksum Shoggoth keeps for it, so it was thrown away. Try again.")


def install(choice=None, cancel=None):
    """Download, check and unpack Piper. Returns its builds() entry."""
    name, url, sha = choice or asset()
    final = os.path.join(root(), TAG)
    tmp = final + ".part"
    UPD.update(state="downloading", bytes=0, total=0, what="read-aloud engine", error=None)
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        tgz = os.path.join(tmp, ".dl-" + name)
        _download(url, tgz, sha, cancel)
        UPD["state"] = "installing"
        llamacpp._safe_extract(tgz, tmp)
        os.remove(tgz)
        exe = os.path.join(tmp, "piper")
        if not os.path.isfile(exe):
            raise RuntimeError("The download doesn't contain piper.")
        os.chmod(exe, 0o755)
        code, out = util.run([exe, "--version"], 15)
        if code != 0:
            raise RuntimeError("The read-aloud engine won't start on this computer. %s" % out.strip()[-200:])
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


# ---------------------------------------------------------------- voices
def voice_entry(name):
    """The Hugging Face entry for a voice Shoggoth offers: {'name' (its place in the repo), 'size', 'sha256', 'config'}."""
    path = VOICES.get(name)
    if not path:
        raise TtsError("Shoggoth doesn't have a voice called %s." % (name or "that"), 400)
    tree = hf._get_json("%s/api/models/%s/tree/main/%s" % (hf.BASE, REPO, os.path.dirname(path)))
    item = next((i for i in (tree if isinstance(tree, list) else []) if i.get("path") == path), None)
    if not item:
        raise hf.HFError("Hugging Face doesn't have the voice %s any more." % name, final=True)
    sha = str((item.get("lfs") or {}).get("oid") or "").lower()
    if not sha:
        raise hf.HFError("Hugging Face published no checksum for %s, so Shoggoth won't use it." % name, final=True)
    return {"name": path, "size": int(item.get("size") or 0), "sha256": sha, "config": path + ".json"}


def voice_file(name):
    return os.path.join(voices_dir(), os.path.basename(VOICES[name]))


def voice_ready(name):
    """True when the voice's model file is already on disk."""
    return bool(name in VOICES and os.path.isfile(voice_file(name)) and os.path.getsize(voice_file(name)) > 0)


def _get_file(url, dest):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Shoggoth"}), timeout=30) as r:
        with open(dest, "wb") as out:
            shutil.copyfileobj(r, out)


def ensure_voice(name, cancel=None):
    """The voice's model path, downloading it and its config the first time. Both are checked against their sha256."""
    entry = voice_entry(name)
    dest = voice_file(name)
    if voice_ready(name):
        return dest
    UPD.update(state="downloading", bytes=0, total=entry["size"], what="voice", error=None)

    def tick(n):
        UPD["bytes"] = n
    try:
        hf._fetch_one(REPO, entry, dest, tick, cancel)
        cfg = dest + ".json"
        if not os.path.isfile(cfg):
            _get_file("%s/%s/resolve/main/%s" % (hf.BASE, REPO, entry["config"]), cfg)
            want = CONFIGS.get(os.path.basename(cfg))
            if want and hf._hash_of(cfg).hexdigest() != want:
                os.remove(cfg)
                raise RuntimeError("The voice's settings file didn't match its checksum, so it was thrown away. Try again.")
    except Exception as e:  # noqa: BLE001
        UPD.update(state="error", error=str(e))
        raise
    UPD["state"] = "done"
    return dest


# ---------------------------------------------------------------- running piper
def running():
    p = _state["proc"]
    return bool(p and p.poll() is None)


def _log_tail(n=4):
    try:
        with open(log_path(), errors="replace") as f:
            lines = [l.strip() for l in f.read().splitlines() if l.strip()]
        return " ".join(lines[-n:])[-300:]
    except OSError:
        return ""


def command(exe, model):
    return [exe, "-m", model, "--json-input", "-q"]


def _reader(proc, lines):
    """Piper prints the path of each WAV it has finished, so a printed line means that request is done."""
    try:
        for line in proc.stdout:
            line = line.strip()
            if line:
                lines.put(line)
    except (OSError, ValueError):
        pass


def start(voice=None):
    """Start piper if it isn't up, with this voice. Raises TtsError with something the person can act on."""
    with _lock:
        voice = voice or choose_voice()
        if running():
            if _state["voice"] == voice:
                _state["last"] = time.time()
                return _state["exe"]
            stop()                                    # one process serves one voice, so a new voice means a new process
        found = find_exe()
        if not found:
            raise TtsError("Read aloud needs Piper, which isn't installed.", 503)
        model = ensure_voice(voice)
        os.makedirs(root(), exist_ok=True)
        log = open(log_path(), "ab")
        try:
            proc = subprocess.Popen(command(found["exe"], model), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=log, cwd=os.path.dirname(found["exe"]) or None, text=True, bufsize=1,
                                    start_new_session=True)
        except OSError as e:
            raise TtsError("Piper won't start: %s" % e, 500)
        finally:
            log.close()
        lines = queue.Queue()
        threading.Thread(target=_reader, args=(proc, lines), daemon=True).start()
        _state.update(proc=proc, voice=voice, exe=found["exe"], last=time.time(), lines=lines)
        time.sleep(0.05)
        if proc.poll() is not None:                   # it died at once: a wrong build, or no voice file it can read
            _state["proc"] = None
            raise TtsError("Piper stopped right after starting. %s" % _log_tail(), 500)
        if not _state["watch"] or not _state["watch"].is_alive():
            _state["watch"] = threading.Thread(target=_idle_watch, daemon=True)
            _state["watch"].start()
        return found["exe"]


def stop():
    with _lock:
        p, _state["proc"] = _state["proc"], None
        _state["voice"], _state["lines"] = None, None
    if p and p.poll() is None:
        try:
            if p.stdin:
                p.stdin.close()                       # piper exits when its input ends
        except (OSError, ValueError):
            pass
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(5)


def _idle_watch():
    while True:
        time.sleep(min(5, max(0.05, IDLE_SECONDS / 10.0)))
        with _lock:
            if not running():
                return
            if time.time() - _state["last"] >= IDLE_SECONDS and not _state["busy"]:
                stop()
                return


# ---------------------------------------------------------------- speaking
def clean_text(text):
    """What Piper should read: one line, no control characters, and not more than MAX_TEXT characters."""
    t = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or ""))
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        raise TtsError("There's nothing to read aloud.", 400)
    if len(t) > MAX_TEXT:
        raise TtsError("That's too long to read aloud in one go.", 413)
    return t


def _is_wav(b):
    return len(b) > 44 and b[:4] == b"RIFF" and b[8:12] == b"WAVE"


def speak(text, voice=None):
    """Text in, WAV bytes out (22050 Hz, 16-bit, mono). Raises TtsError."""
    if not enabled():
        raise TtsError("Read aloud is turned off.", 403)
    text = clean_text(text)
    voice = voice or choose_voice()
    if voice not in VOICES:
        raise TtsError("Shoggoth doesn't have a voice called %s." % voice, 400)
    with _speaking:                                   # one piper process, so one request at a time
        with _lock:
            _state["busy"] += 1
        try:
            for attempt in (0, 1):
                start(voice)
                d = tempfile.mkdtemp(prefix="shoggoth-tts-")
                out = os.path.join(d, "speech.wav")
                try:
                    proc, lines = _state["proc"], _state["lines"]
                    try:
                        proc.stdin.write(json.dumps({"text": text, "output_file": out}) + "\n")
                        proc.stdin.flush()
                    except (OSError, ValueError):
                        if attempt:
                            raise TtsError("Piper stopped answering. %s" % _log_tail(), 502)
                        stop()
                        continue
                    done, deadline = False, time.time() + SPEAK_TIMEOUT
                    while time.time() < deadline:
                        if proc.poll() is not None:
                            break
                        try:
                            if lines.get(timeout=0.2) == out:
                                done = True
                                break
                        except queue.Empty:
                            continue
                    if not done:
                        if attempt or proc.poll() is not None:
                            raise TtsError("Piper stopped answering. %s" % _log_tail(), 502)
                        stop()                    # it hung: one fresh start, then give up
                        continue
                    with open(out, "rb") as f:
                        wav = f.read()
                finally:
                    shutil.rmtree(d, ignore_errors=True)
                if not _is_wav(wav):
                    raise TtsError("Piper sent something that isn't a WAV file.", 502)
                return wav
        finally:
            with _lock:
                _state["busy"] -= 1
                _state["last"] = time.time()


def start_setup(voice=None):
    """Download Piper and the voice it reads with, in the background: progress is in UPD, which status() returns as
    "download". False when a download is already running."""
    with _lock:
        if UPD["state"] in ("downloading", "installing"):
            return False
        UPD.update(state="downloading", bytes=0, total=0, what="read-aloud engine", error=None)

    def run():
        try:
            if not find_exe():
                install()
            ensure_voice(voice or choose_voice())
        except Exception as e:  # noqa: BLE001 - install() and ensure_voice() put the reason in UPD when they can
            UPD.update(state="error", error=UPD.get("error") or str(e) or e.__class__.__name__)

    threading.Thread(target=run, daemon=True).start()
    return True


NOTE = "Bark isn't available, so read aloud uses Piper."


def status():
    """What the page reads to say whether Read aloud can be offered, and how a download is going."""
    found = find_exe()
    voice = choose_voice()
    return {"enabled": enabled(), "installed": bool(found), "exe": found and found["exe"],
            "voice": voice, "voices": sorted(VOICES), "voice_ready": voice_ready(voice),
            "running": running(), "runs_on": "Runs on the processor" if found else None,
            "bark": False, "note": NOTE, "download": dict(UPD)}


# ---------------------------------------------------------------- handlers for the routes
def ui_speak(body):
    """For POST /ui/speech/speak: {text, voice}. Returns (status, content_type, bytes); the WAV is the body."""
    try:
        return 200, "audio/wav", speak(body.get("text"), str(body.get("voice") or "") or None)
    except TtsError as e:
        return e.status, "application/json", json.dumps({"error": str(e)}).encode()
    except (hf.HFError, RuntimeError) as e:
        return 503, "application/json", json.dumps({"error": str(e)}).encode()


def openai_speech(body, headers=None):
    """For POST /v1/audio/speech, shaped like OpenAI's. body: the raw request bytes. Returns (status, content_type, bytes).
    `model` is ignored and only WAV is made, which is what Piper writes."""
    def err(status, msg, kind="invalid_request_error"):
        return status, "application/json", json.dumps({"error": {"message": msg, "type": kind}}).encode()
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return err(400, "That request isn't JSON.")
    if not isinstance(data, dict):
        return err(400, "That request isn't JSON.")
    fmt = str(data.get("response_format") or "wav")
    if fmt != "wav":
        return err(400, "Shoggoth makes WAV audio only; ask for response_format wav.")
    # Other apps ask for OpenAI's voice names (alloy, nova), which Shoggoth doesn't have: those get the voice set here.
    voice = str(data.get("voice") or "")
    try:
        return 200, "audio/wav", speak(data.get("input"), voice if voice in VOICES else None)
    except TtsError as e:
        return err(e.status, str(e), "invalid_request_error" if e.status < 500 else "server_error")
    except (hf.HFError, RuntimeError) as e:
        return err(503, str(e), "server_error")
