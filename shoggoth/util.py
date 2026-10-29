"""Small helpers shared by the Shoggoth server modules."""
import subprocess
import threading
import time

_cache = {}
_lock = threading.Lock()


def cached(key, ttl, fn):
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (time.time(), val)
    return val


def invalidate(*keys):
    with _lock:
        for k in keys or list(_cache):
            _cache.pop(k, None)


def run(cmd, timeout=15, env=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, str(e)
