"""What Shoggoth started, and may therefore stop: one record per engine, checked against the system before it is trusted.

Every engine keeps its record somewhere different (Lemonade a pid file, Strata Shoggoth's settings, a YAML engine its provider settings)
and proves it is still the same process in its own way, so those two steps are hooks and the rest lives here once."""


def start_time(pid):
    """When process `pid` began (clock ticks since boot, from /proc), or None when there is no such process. Tells a process from a later one
    that was given the same number."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            return int(f.read().rsplit(")", 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


class Claim:
    """The one thing an engine's Shoggoth started: `owned`, and its `pid`, `port`, start time `t` and anything else the engine records.

    load() returns the saved record (a dict) or None; save(rec) stores it, or forgets it when rec is None; same(rec, **ctx) says whether the
    record still names a live process of ours. Without a `same`, a saved record is trusted. A record that fails `same` is forgotten unless
    forget_stale is False (Lemonade's pid file may belong to a lemond on another port)."""

    def __init__(self, load, save, same=None, forget_stale=True):
        self._load, self._save, self._same, self._forget = load, save, same, forget_stale
        self.owned, self.pid, self.port, self.t, self.extra = False, None, None, None, {}

    def restore(self, **ctx):
        """Take the saved record back if the system still agrees with it; forget the record if it has gone. Returns whether we own something."""
        if self.owned:
            return True
        rec = self._load()
        if not rec:
            return False
        if self._same is None or self._same(rec, **ctx):
            self.owned = True
            self.pid, self.port, self.t = rec.get("pid"), rec.get("port"), rec.get("t")
            self.extra = {k: v for k, v in rec.items() if k not in ("pid", "port", "t")}
            return True
        if self._forget:
            self._save(None)
        return False

    def set(self, **rec):
        """Record that we started something (pid, port, t and whatever else the engine wants kept)."""
        self.owned, self.pid, self.port, self.t = True, rec.get("pid"), rec.get("port"), rec.get("t")
        self.extra = {k: v for k, v in rec.items() if k not in ("pid", "port", "t")}
        self._save(self.record())

    def record(self):
        return dict({"pid": self.pid, "port": self.port, "t": self.t}, **self.extra)

    def clear(self):
        """Forget it: it stopped, or it was never ours."""
        self.owned, self.pid, self.port, self.t, self.extra = False, None, None, None, {}
        self._save(None)
