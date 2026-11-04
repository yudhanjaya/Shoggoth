"""An engine that holds the memory and was not started by Shoggoth (for tests of the router's memory arbiter)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "shoggoth"))

import provbase  # noqa: E402


class Fake(provbase.Base):
    """An engine that holds the memory and was not started by Shoggoth."""

    def __init__(self, pid, group="gpu", exclusive=True, ours=False, busy=False, can_release=True):
        self.id, self.name, self.ours, self._busy, self.can_release = pid, pid.title(), ours, busy, can_release
        self.spec, self.log, self.running = {}, [], True

    def memory(self):
        return {"group": "gpu", "exclusive": True}

    def is_running(self):
        return self.running

    def busy(self):
        return self._busy

    def owned_instance(self):
        return 1 if self.ours else None

    def make_way(self):
        if self.ours:
            self.log.append("stop")
            self.running = False
            return True, ""
        return False, "it is running something you started."

    minutes, found_busy = 5, (False, 1)

    def idle_minutes(self):
        return self.minutes

    def found_activity(self):
        return self.found_busy

    def activity(self):
        return None, None

    def release(self):
        self.log.append("release")
        self.running = False
        return (True, "") if self.can_release else (False, "it can't")
