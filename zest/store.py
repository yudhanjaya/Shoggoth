"""Zest's own settings, kept in the user's config directory."""
import copy
import json
import os
import secrets
import threading

DEFAULTS = {
    "mode": None,            # "builtin" (Zest runs its own Lemonade) or "existing" (one you already run)
    "setup_done": False,
    "existing": {"host": "127.0.0.1", "port": 13305, "api_key": ""},
    "builtin": {"port": 13307, "api_key": "", "version": ""},   # version: "" = newest, or a pinned version, or "bundled"
    "chat": {"ctx": 131072},  # the context window every chat load asks for (128k): long chats and agent work need the room
    "pi": {"version": ""},   # which Pi (the agent) to run: "" = newest, "bundled" = the one that came with Zest, or a version number
    "models": {"storage": "hf", "custom_dir": "", "imports": []},
    # The door for other apps: its own port, always needs the key, this computer only unless `lan` is on.
    "gateway": {"enabled": True, "port": 13308, "api_key": "", "lan": False, "capture": False},
}
_lock = threading.Lock()


def _xdg(var, fallback):
    return os.path.join(os.environ.get(var) or os.path.expanduser(fallback), "zest")


def config_dir():
    return _xdg("XDG_CONFIG_HOME", "~/.config")


def data_dir():
    return _xdg("XDG_DATA_HOME", "~/.local/share")


def _path():
    return os.path.join(config_dir(), "settings.json")


def _merge(base, extra):
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load():
    s = copy.deepcopy(DEFAULTS)
    try:
        with open(_path()) as f:
            _merge(s, json.load(f))
    except (OSError, ValueError):
        pass
    return s


def save(s):
    with _lock:
        os.makedirs(config_dir(), exist_ok=True)
        tmp = _path() + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # it holds an API key
        with os.fdopen(fd, "w") as f:
            json.dump(s, f, indent=2)
        os.replace(tmp, _path())


def new_key():
    return secrets.token_urlsafe(24)
