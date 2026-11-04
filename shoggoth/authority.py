"""How far Shoggoth may go with an engine it did not start: Ask, Allowed, or Hands off, per engine.

With Expert Mode off every engine reads as Hands off, which is how Shoggoth has always behaved. With it on, an engine nobody set is on Ask.
Only the setting lives here; the router decides what to do with the answer."""
import threading

import expert
import store

ASK, ALLOWED, HANDS_OFF = "ask", "allowed", "hands_off"
CHOICES = (ASK, ALLOWED, HANDS_OFF)
_once = set()                 # engines the person said "free it" to just now; spent by the next time it is needed
_lock = threading.Lock()


def get(pid, settings=None):
    """What Shoggoth may do with engine `pid` that it did not start."""
    s = settings or store.load()
    if not expert.on(s):
        return HANDS_OFF
    v = (s.get("authority") or {}).get(pid)
    return v if v in CHOICES else ASK


def all_for(pids):
    s = store.load()
    return {p: get(p, s) for p in pids}


def set_(pid, value):
    """Save the choice for one engine. Settings for engines are kept when Expert Mode is off; they just stop applying."""
    if value not in CHOICES:
        raise ValueError("Authority is one of: ask, allowed, hands_off.")

    def put(s):
        s.setdefault("authority", {})[str(pid)] = value
    store.update(put)
    return value


def grant_once(pid):
    """The person answered an Ask with "Free it": the next time the engine's memory is needed it may be released."""
    with _lock:
        _once.add(pid)


def spend(pid):
    """True once for each grant_once."""
    with _lock:
        if pid in _once:
            _once.discard(pid)
            return True
        return False


def may(pid):
    """Yes if the engine is Allowed, or the person just said yes to an Ask. Spends a one-time yes."""
    return get(pid) == ALLOWED or (get(pid) == ASK and spend(pid))
