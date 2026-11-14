"""Shoggoth's own settings, kept in the user's config directory."""
import copy
import json
import os
import secrets
import tempfile
import threading

DEFAULTS = {
    "mode": None,            # "builtin" (Shoggoth runs its own llama.cpp) or "connected" (only servers you connected)
    "setup_done": False,
    # A person who chose another Shoggoth on the network and does not want llama.cpp downloaded on this computer yet.
    "client_only": False,
    # Shoggoth's own llama.cpp: backend (cpu, vulkan, cuda12, cuda13, rocm; "" = the newest build here), tag (a pinned build, "" = newest),
    # device (llama.cpp's name, like cuda0; "" = all), max_loaded (keep up to N models loaded), enabled (false turns it off)
    "llamacpp": {"backend": "", "tag": "", "device": "", "max_loaded": 1},
    "search": {"enabled": True, "provider": "duckduckgo", "brave_key": "", "tavily_key": ""},   # the model may search the web; every search still asks first
    "chat": {"ctx": "auto"},  # the context window chats ask for: "auto" (the most that fits, per model) or a number of tokens
    "pi": {"version": ""},   # which Pi (the agent) to run: "" = newest, "bundled" = the one that came with Shoggoth, or a version number
    "servers": [],                 # servers the person connected under Advanced > Engines: [{id, preset, name, url, key}]
    "library": {"root": ""},       # where every model lives (the person chooses it once)
    "providers": {},               # per engine: its own settings, and the fingerprint of a user-added one that was approved
    "models": {"storage": "hf", "custom_dir": "", "imports": []},
    # The door for other apps: its own port, always needs the key, this computer only unless `lan` is on.
    "gateway": {"enabled": True, "port": 13308, "api_key": "", "lan": False, "capture": False},
    # Each model family's creature on this computer: {lineage: [form, tone]}, given once and never changed.
    "creatures": {},
}
_lock = threading.RLock()


class Settings(dict):
    """What `load()` returns: a plain dict that also remembers what the file held when it was read, so `save()` can write only what the
    caller changed on top of whatever is on disk now (two threads changing different settings no longer undo each other)."""
    _base = None


def _legacy_env():
    """Shoggoth used to be called Zest. A ZEST_* variable still works: it stands in for its SHOGGOTH_* name when that is unset.
    Runs when this module is imported, before anything reads the environment, and the Pi extensions inherit the result."""
    for k, v in list(os.environ.items()):
        if k.startswith("ZEST_"):
            os.environ.setdefault("SHOGGOTH_" + k[5:], v)


_legacy_env()


def _xdg(var, fallback):
    """The folder under XDG `var`. A `zest` folder left from before the rename is moved to `shoggoth` the first time; if the move
    fails (another program has it open, a read-only disk), the old folder is used where it is."""
    base = os.environ.get(var) or os.path.expanduser(fallback)
    new, old = os.path.join(base, "shoggoth"), os.path.join(base, "zest")
    if not os.path.exists(new) and os.path.isdir(old):
        try:
            os.rename(old, new)
            print("Moved %s to %s (Zest is called Shoggoth now)." % (old, new), flush=True)
        except OSError:
            return old
    return new


def _usable(path, kind):
    """The folder if it can be written to; otherwise a private one in the temp folder, so a read-only or missing home
    (a sandbox, a kiosk) leaves Shoggoth running with settings that last until it quits, instead of crashing."""
    try:
        os.makedirs(path, exist_ok=True)
        if os.access(path, os.W_OK):
            return path
    except OSError:
        pass
    alt = os.path.join(tempfile.gettempdir(), "shoggoth-%d" % os.getuid(), kind)
    os.makedirs(alt, mode=0o700, exist_ok=True)
    return alt


def config_dir():
    return os.environ.get("SHOGGOTH_CONFIG_DIR") or _usable(_xdg("XDG_CONFIG_HOME", "~/.config"), "config")      # ZEST_*_DIR: for tests, so other programs keep their own folders


def data_dir():
    return os.environ.get("SHOGGOTH_DATA_DIR") or _usable(_xdg("XDG_DATA_HOME", "~/.local/share"), "data")


def _path():
    return os.path.join(config_dir(), "settings.json")


def _merge(base, extra):
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


OLD_DEFAULT_CTX = 131072     # what 4.x saved as the chat window without being asked; not a choice the person made


def _read():
    s = copy.deepcopy(DEFAULTS)
    try:
        with open(_path()) as f:
            _merge(s, json.load(f))
    except (OSError, ValueError):
        pass
    c = s.get("chat") if isinstance(s.get("chat"), dict) else {}
    if c.get("ctx") == OLD_DEFAULT_CTX and not c.get("ctx_set"):
        c["ctx"] = "auto"            # 4.x wrote 128k as the default; only a size picked in the chat bar (ctx_set) stays fixed
    return s


def load():
    s = Settings(_read())
    s._base = copy.deepcopy(dict(s))
    return s


_GONE = object()


def _apply(cur, base, new):
    """Put what changed between `base` and `new` onto `cur`. Dicts are followed key by key; anything else (lists, values) is replaced whole."""
    for k in set(base) | set(new):
        b, n = base.get(k, _GONE), new.get(k, _GONE)
        if b == n:
            continue
        if n is _GONE:
            cur.pop(k, None)
        elif isinstance(n, dict) and isinstance(b, dict) and isinstance(cur.get(k), dict):
            _apply(cur[k], b, n)
        else:
            cur[k] = copy.deepcopy(n)


def save(s):
    with _lock:
        if isinstance(s, Settings) and s._base is not None:
            cur = _read()
            _apply(cur, s._base, s)
            s.clear()
            s.update(cur)                                  # the caller carries on with the merged settings
            s._base = copy.deepcopy(cur)
        os.makedirs(config_dir(), mode=0o700, exist_ok=True)
        tmp = _path() + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # it holds an API key
        os.fchmod(fd, 0o600)                                               # even when an old .tmp was left with looser rights
        with os.fdopen(fd, "w") as f:
            json.dump(s, f, indent=2)
        os.replace(tmp, _path())


def update(fn):
    """Change settings in one step: `fn(settings)` runs on the current file and the result is saved, with no other writer in between."""
    with _lock:
        s = load()
        fn(s)
        save(s)
        return s


def new_key():
    return secrets.token_urlsafe(24)
