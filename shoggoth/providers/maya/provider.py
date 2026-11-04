"""Maya as a provider. The YAML says what it is; this module knows how to tell a Maya from a Strata or a bare llama-server on the same
ports, how to ask it to load, swap or unload (its own HTTP calls), and how to start it from a launch script the person approved.

A Maya that is already running is never signalled: `release()` asks it to unload, and `stop()` works only on a process Shoggoth started."""
import os
import shlex
import subprocess
import time

import provbase
from provbase import ProviderError, http_json, port_open, port_range


class Provider(provbase.GenericProvider):
    # -- finding it
    def _ports(self):
        d = self.spec.get("detect") or {}
        ports = port_range(d.get("ports") or [])
        extra = self.ctx.settings(self.id).get("port")
        return ports + ([int(extra)] if extra and int(extra) not in ports else [])

    @staticmethod
    def _is_maya(h):
        """Maya's /health names its model (`model_label`) and its `version`; Strata says `service: strata`, and a bare llama-server says neither."""
        return isinstance(h, dict) and "service" not in h and ("model_label" in h or "version" in h)

    def detect(self):
        self._adopt_own()
        running = []
        for port in self._ports():
            if not port_open(port):
                continue
            h = self._health(port)
            if self._is_maya(h):
                running.append({"port": port, "owner": "zest" if (self._owned and port == self._port) else "yours",
                                "label": h.get("model_label") or "", "version": h.get("version") or ""})
        return {"found": bool(running) or bool(self.launch()), "bin": None, "running": running}

    def status(self):
        det = self.detect()
        return dict(self.summary(), installed=det["found"], running=det["running"], bin=None, problems=[],
                    launch=" ".join(shlex.quote(x) for x in self.launch() or []), launch_approved=self.launch_approved())

    # -- the launch script: something the person sets once, so nothing is learned or run silently
    def launch(self):
        cmd = self.ctx.settings(self.id).get("launch")
        return [str(x) for x in cmd] if isinstance(cmd, list) and cmd else None

    def launch_approved(self):
        s = self.ctx.settings(self.id)
        return bool(self.launch()) and s.get("launch_ok") == s.get("launch")

    def set_launch(self, text):
        words = shlex.split(str(text or ""))
        if not words:
            self.ctx.remember(self.id, launch=None, launch_ok=None)
            return None
        if not os.path.isabs(words[0]) or not os.access(words[0], os.X_OK):
            raise ProviderError("That isn't a program Shoggoth can run. Give the full path to Maya's launch script.")
        self.ctx.remember(self.id, launch=words, launch_ok=words)          # saving it here is the person's approval of it
        return words

    def ui(self, method, action, arg):
        if (method, action) == ("GET", "summary"):
            return 200, self.status()
        if (method, action) == ("POST", "launch"):
            try:
                self.set_launch((arg or {}).get("command"))
            except ProviderError as e:
                return 400, {"error": str(e)}
            return 200, self.status()
        return None

    # -- what it is doing
    def found_activity(self):
        """(answering now?, tokens so far) from /slots on a running Maya, whoever started it; (None, None) when it can't say."""
        for r in self.detect()["running"]:
            status, d = http_json(self._root(r["port"]) + "/slots", self._key())
            if status == 200 and isinstance(d, list):
                return any(bool(s.get("is_processing")) for s in d if isinstance(s, dict)), None
        return None, None

    def busy(self):
        return bool(self.found_activity()[0])

    def activity(self):
        return self.found_activity()

    # -- serving
    def ensure(self, model_id, progress, opts=None):
        ep = self.endpoint(model_id)
        if ep:
            return ep
        up = self.detect()["running"]
        if up:                                                              # running, but with another model or none: ask it
            progress("Asking Maya to load %s…" % model_id)
            port = up[0]["port"]
            status, d = http_json(self._root(port) + "/models/load", self._key(), timeout=30, method="POST", body={"name": model_id})
            if status in (401, 403, 404):
                raise ProviderError("Maya's model management is off, so Shoggoth can't load %s. Turn it on in Maya, or load the model there yourself." % model_id)
            if status == 409:
                raise ProviderError("Maya is answering right now. Wait for it to finish, then try again.")
            if status != 200:
                raise ProviderError("Maya couldn't load %s (it answered %s)." % (model_id, status))
            return self._wait_for(model_id, progress, 600)
        cmd = self.launch()
        if not cmd:
            raise ProviderError("Maya isn't running. Start it yourself, or give Shoggoth its launch script under Advanced > Engines.")
        if not self.launch_approved():
            raise ProviderError("Maya's launch script hasn't been approved yet.")
        with self._lock:
            progress("Starting Maya…")
            self._proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, start_new_session=True)
            self._claim.set(pid=self._proc.pid, port=None, t=provbase._start_time(self._proc.pid))
        return self._wait_for(model_id, progress, float((self.spec.get("run") or {}).get("ready", {}).get("timeout", 900)), launched=True)

    def _wait_for(self, model_id, progress, secs, launched=False):
        end = time.time() + secs
        while time.time() < end:
            if launched and self._proc is not None and self._proc.poll() is not None:
                self._claim.clear()
                raise ProviderError("Maya stopped right after starting.")
            if launched and self._owned:
                mine = next((r for r in self.detect()["running"] if r["port"] != 0), None)
                if mine and self._claim.port is None:
                    self._claim.set(pid=self._claim.pid, port=mine["port"], t=self._claim.t)
                    self._port = mine["port"]
            ep = self.endpoint(model_id) or (self.endpoint(None) if launched else None)
            if ep:
                return ep
            time.sleep(1)
        raise ProviderError("Maya didn't finish loading in time.")

    def release(self):
        """Ask Maya to unload its model and keep running (POST /models/unload). 409 means it is answering. Nothing is signalled."""
        up = self.detect()["running"]
        if not up:
            return True, ""
        status, d = http_json(self._root(up[0]["port"]) + "/models/unload", self._key(), timeout=60, method="POST", body={})
        if status == 200:
            return True, ""
        if status == 409:
            return False, "Maya is answering right now."
        if status in (401, 403, 404):
            return False, "Maya's model management is off. Turn it on in Maya, or stop Maya yourself."
        return False, "Maya couldn't unload (it answered %s)." % status

    def make_way(self):
        if self.owned_instance() is not None:
            return self.stop()
        return False, "Maya is running something you started."
