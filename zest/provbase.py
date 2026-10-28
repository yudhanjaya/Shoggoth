"""What a provider is, and the one that needs nothing but a YAML file.

A provider is an engine that can serve models (Lemonade, Strata, a llama-server wrapper, a remote address). It lives in a folder
with a `provider.yaml` and, only when YAML can't say it, a `provider.py` defining `class Provider(provbase.Base)`.
Zest talks to every provider through `Base`, so the rest of the program never needs to know which engine it is. Standard library
only (YAML is read by the copy in _vendor).
"""
import json
import os
import re
import shutil
import socket
import string
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API_VERSION = 1
# The only names a command or URL in a provider.yaml may use. Anything else is a mistake, not a feature.
VARIABLES = {"bin", "host", "port", "model", "preset", "family", "name", "context", "library", "data", "profile", "key"}
RUN_MODES = ("managed", "service", "external")


class ProviderError(Exception):
    """Something a person can act on, in plain words."""


# ---------------------------------------------------------------- small helpers shared by every provider
def fill(template, **values):
    """Fill {name} placeholders in a string or list of strings. Unknown names are an error; missing values become empty."""
    def one(t):
        t = str(t)
        bad = [n for _, n, _, _ in string.Formatter().parse(t) if n and n not in VARIABLES]
        if bad:
            raise ProviderError("unknown variable {%s} (allowed: %s)" % (bad[0], ", ".join(sorted(VARIABLES))))
        return t.format_map({k: ("" if values.get(k) is None else values[k]) for k in VARIABLES})
    return [one(t) for t in template] if isinstance(template, list) else one(template)


def jpath(obj, path):
    """A small JSON path: `$.a.b`, `$.data[*].id`, `$.list[0]`. Returns a list of matches (possibly empty)."""
    if not path or path == "$":
        return [obj]
    cur = [obj]
    for part in re.findall(r"[^.\[\]]+|\[[^\]]*\]", path.lstrip("$").lstrip(".")):
        nxt = []
        for o in cur:
            if part == "[*]" and isinstance(o, list):
                nxt += o
            elif re.fullmatch(r"\[\d+\]", part) and isinstance(o, list):
                i = int(part[1:-1])
                nxt += o[i:i + 1]
            elif isinstance(o, dict) and part in o:
                nxt.append(o[part])
        cur = nxt
    return cur


def http_json(url, key="", timeout=3, method="GET", body=None):
    h = {"Authorization": "Bearer " + key} if key else {}
    data = None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h, method=method), timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}
    except (OSError, ValueError):
        return 0, {}


def port_open(port, host="127.0.0.1"):
    try:
        socket.create_connection((host, port), timeout=0.3).close()
        return True
    except OSError:
        return False


def port_range(spec):
    """'8080-8089' -> range(8080, 8090); '13305' -> range(13305, 13306); a list of either."""
    out = []
    for item in spec if isinstance(spec, list) else [spec]:
        a, _, b = str(item).partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


class Context:
    """What a provider may ask Zest for: where things live. Providers never reach into Zest's modules."""
    def __init__(self, library="", data="", settings=None):
        self.library, self.data = library, data
        self._settings = settings or (lambda pid: {})

    def settings(self, pid):
        return self._settings(pid)


# ---------------------------------------------------------------- the interface
class Base:
    """Every provider answers these. A step a provider can't do raises NotImplementedError and is reported as unavailable."""
    def __init__(self, spec, folder, ctx):
        self.spec, self.folder, self.ctx = spec, folder, ctx
        self.id, self.name = spec["id"], spec.get("name") or spec["id"]
        self.trusted = True            # set False by the registry for providers from the user's folder
        self.approved = True

    # -- identity and what it is for
    def summary(self):
        return {"id": self.id, "name": self.name, "summary": self.spec.get("summary", ""), "tier": self.spec.get("tier", ""),
                "trusted": self.trusted, "approved": self.approved, "mode": (self.spec.get("run") or {}).get("mode", "managed"),
                "capabilities": sorted((self.spec.get("capabilities") or {}).keys())}

    def memory(self):
        m = self.spec.get("memory") or {}
        return {"group": m.get("group", ""), "exclusive": bool(m.get("exclusive")), "idle_stop_minutes": m.get("idle_stop_minutes", 0),
                "start_estimate_s": (m.get("start_estimate") or {}).get("seconds", 0), "max_loaded": m.get("max_loaded")}

    def formats(self):
        m = self.spec.get("models") or {}
        return {"formats": m.get("formats") or [], "architectures": m.get("architectures") or []}

    def can_run(self, model):
        """(ok, reason) for a catalog entry {format, architecture, ...} from the declared formats and architectures."""
        f = self.formats()
        if f["formats"] and model.get("format") and model["format"] not in f["formats"]:
            return False, "%s can't run %s files." % (self.name, model["format"])
        arch = model.get("architecture")
        if arch and arch in ((self.spec.get("models") or {}).get("excluded_architectures") or []):
            return False, "%s can't run the %s architecture." % (self.name, arch)
        if f["architectures"] and arch and arch not in f["architectures"]:
            return False, "%s doesn't support the %s architecture." % (self.name, arch)
        return True, ""

    # -- finding and keeping it current
    def detect(self):
        raise NotImplementedError

    def status(self):
        raise NotImplementedError

    def install(self, progress):
        raise NotImplementedError

    def update_check(self):
        raise NotImplementedError

    def update(self, progress):
        raise NotImplementedError

    # -- serving
    def models(self):
        raise NotImplementedError

    def ensure(self, model_id, progress, opts=None):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def endpoint(self, model_id=None):
        raise NotImplementedError

    # -- making it faster
    def tune_plan(self, model_id, opts):
        raise NotImplementedError

    def tune_run(self, model_id, opts, job):
        raise NotImplementedError


# ---------------------------------------------------------------- the provider that is only a YAML file
class GenericProvider(Base):
    """Implements the interface from `provider.yaml`: find it, start and stop a process, wait until it answers, list its models."""

    def __init__(self, spec, folder, ctx):
        super().__init__(spec, folder, ctx)
        self._proc = None
        self._lock = threading.RLock()
        self._port = None
        self._owned = False             # did Zest start the process now serving?

    # -- helpers
    def _vals(self, **extra):
        v = {"bin": self.binary() or "", "host": "127.0.0.1", "port": self._port, "library": self.ctx.library, "data": self.ctx.data}
        v.update(extra)
        return v

    def binary(self):
        for cand in (self.spec.get("detect") or {}).get("binaries") or []:
            cand = os.path.expanduser(os.path.expandvars(str(cand)))
            if cand.startswith("$"):
                continue                # an unset environment variable
            found = cand if os.path.isabs(cand) and os.access(cand, os.X_OK) else shutil.which(cand)
            if found:
                return found
        return None

    def _base(self, port=None):
        ep = self.spec.get("endpoint") or {}
        return fill(ep.get("base_url", ""), **self._vals(port=port or self._port)).rstrip("/")

    def _key(self):
        return self.ctx.settings(self.id).get("key", "")

    def _root(self, port):
        """scheme://host:port of the server. Paths in `detect.health`, `run.ready` and `endpoint.models` are relative to this."""
        u = urllib.parse.urlparse(self._base(port))
        return "%s://%s" % (u.scheme, u.netloc)

    def _health(self, port):
        h = (self.spec.get("detect") or {}).get("health") or (self.spec.get("run") or {}).get("ready") or {}
        path = h.get("path")
        if not path:
            return None
        status, d = http_json(self._root(port) + path, self._key(), timeout=1.5)
        if status != 200:
            return None
        for k, want in (h.get("expect") or {}).items():
            if d.get(k) != want:
                return None
        return d

    # -- finding it
    def detect(self):
        """What is already there: {found, bin, running:[{port}], owner}. Never starts anything."""
        d = self.spec.get("detect") or {}
        running = []
        for port in port_range(d.get("ports") or ((self.spec.get("run") or {}).get("port_range") or [])):
            if port_open(port) and self._health(port) is not None:
                running.append({"port": port, "owner": "zest" if (self._owned and port == self._port) else "yours"})
        mode = (self.spec.get("run") or {}).get("mode", "managed")
        if mode == "external":
            url = (self.ctx.settings(self.id) or {}).get("url") or (self.spec.get("endpoint") or {}).get("base_url", "")
            return {"found": bool(url), "bin": None, "running": [], "external": url}
        return {"found": bool(self.binary()) or bool(running), "bin": self.binary(), "running": running}

    def status(self):
        det = self.detect()
        installed = bool(det.get("bin")) or bool(det.get("running")) or (self.spec.get("run") or {}).get("mode") in ("service", "external")
        return dict(self.summary(), installed=installed, running=det.get("running", []), bin=det.get("bin"), problems=[])

    # -- models
    def models(self):
        """The models the running servers offer: [{id, ctx, port, owner}]."""
        spec = (self.spec.get("endpoint") or {}).get("models") or {}
        out = []
        for r in self.detect().get("running", []):
            status, d = http_json(self._root(r["port"]) + spec.get("path", "/v1/models"), self._key())
            if status != 200:
                continue
            ids = jpath(d, spec.get("id", "$.data[*].id"))
            ctxs = jpath(d, spec["context"]) if spec.get("context") else []
            for i, mid in enumerate(ids):
                out.append({"id": mid, "ctx": int(ctxs[i]) if i < len(ctxs) and ctxs[i] else 0, "port": r["port"], "owner": r["owner"]})
        return out

    def endpoint(self, model_id=None):
        raise NotImplementedError

    # -- making it faster
    def tune_plan(self, model_id, opts):
        raise NotImplementedError

    def tune_run(self, model_id, opts, job):
        raise NotImplementedError


# ---------------------------------------------------------------- the provider that is only a YAML file
class GenericProvider(Base):
    """Implements the interface from `provider.yaml`: find it, start and stop a process, wait until it answers, list its models."""

    def __init__(self, spec, folder, ctx):
        super().__init__(spec, folder, ctx)
        self._proc = None
        self._lock = threading.RLock()
        self._port = None
        self._owned = False             # did Zest start the process now serving?

    # -- helpers
    def _vals(self, **extra):
        v = {"bin": self.binary() or "", "host": "127.0.0.1", "port": self._port, "library": self.ctx.library, "data": self.ctx.data}
        v.update(extra)
        return v

    def binary(self):
        for cand in (self.spec.get("detect") or {}).get("binaries") or []:
            cand = os.path.expanduser(os.path.expandvars(str(cand)))
            if cand.startswith("$"):
                continue                # an unset environment variable
            found = cand if os.path.isabs(cand) and os.access(cand, os.X_OK) else shutil.which(cand)
            if found:
                return found
        return None

    def _base(self, port=None):
        ep = self.spec.get("endpoint") or {}
        return fill(ep.get("base_url", ""), **self._vals(port=port or self._port)).rstrip("/")

    def _key(self):
        return self.ctx.settings(self.id).get("key", "")

    def _root(self, port):
        """scheme://host:port of the server. Paths in `detect.health`, `run.ready` and `endpoint.models` are relative to this."""
        u = urllib.parse.urlparse(self._base(port))
        return "%s://%s" % (u.scheme, u.netloc)

    def _health(self, port):
        h = (self.spec.get("detect") or {}).get("health") or (self.spec.get("run") or {}).get("ready") or {}
        path = h.get("path")
        if not path:
            return None
        status, d = http_json(self._root(port) + path, self._key(), timeout=1.5)
        if status != 200:
            return None
        for k, want in (h.get("expect") or {}).items():
            if d.get(k) != want:
                return None
        return d

    # -- finding it
    def detect(self):
        """What is already there: {found, bin, running:[{port}], owner}. Never starts anything."""
        d = self.spec.get("detect") or {}
        running = []
        for port in port_range(d.get("ports") or ((self.spec.get("run") or {}).get("port_range") or [])):
            if port_open(port) and self._health(port) is not None:
                running.append({"port": port, "owner": "zest" if (self._owned and port == self._port) else "yours"})
        mode = (self.spec.get("run") or {}).get("mode", "managed")
        if mode == "external":
            url = (self.ctx.settings(self.id) or {}).get("url") or (self.spec.get("endpoint") or {}).get("base_url", "")
            return {"found": bool(url), "bin": None, "running": [], "external": url}
        return {"found": bool(self.binary()) or bool(running), "bin": self.binary(), "running": running}

    def status(self):
        det = self.detect()
        return dict(self.summary(), installed=bool(det.get("bin")) or mode_ok(self), running=det.get("running", []), bin=det.get("bin"),
                    problems=[])

    # -- models
    def models(self):
        ep = self.spec.get("endpoint") or {}
        spec = ep.get("models") or {}
        out = []
        for r in self.detect().get("running", []):
            status, d = http_json(self._base(r["port"]) + spec.get("path", "/models").replace("/v1", "", 1) if self._base(r["port"]).endswith("/v1") and spec.get("path", "").startswith("/v1") else self._base(r["port"]) + spec.get("path", "/models"), self._key())
            if status != 200:
                continue
            ids = jpath(d, spec.get("id", "$.data[*].id"))
            ctxs = jpath(d, spec["context"]) if spec.get("context") else []
            for i, mid in enumerate(ids):
                out.append({"id": mid, "ctx": int(ctxs[i]) if i < len(ctxs) and ctxs[i] else 0, "port": r["port"], "owner": r["owner"]})
        return out

    def endpoint(self, model_id=None):
        for m in self.models():
            if model_id is None or m["id"] == model_id:
                return {"base_url": self._base(m["port"]), "key": self._key(), "model": m["id"], "ctx": m["ctx"], "owner": m["owner"]}
        return None

    # -- serving
    def ensure(self, model_id, progress, opts=None):
        """Return the endpoint serving `model_id`, starting the engine first if it isn't (managed mode only)."""
        ep = self.endpoint(model_id)
        if ep:
            return ep
        run = self.spec.get("run") or {}
        if run.get("mode", "managed") != "managed":
            raise ProviderError("%s isn't running, and Zest doesn't start it." % self.name)
        if not self.approved:
            raise ProviderError("%s hasn't been approved to run yet." % self.name)
        if not self.binary():
            raise ProviderError("%s isn't installed." % self.name)
        with self._lock:
            used = {p for p in port_range(run.get("port_range") or [])}
            free = next((p for p in sorted(used) if not port_open(p)), None)
            if free is None:
                raise ProviderError("No free port for %s." % self.name)
            self._port = free
            cmd = fill(run["start"], **self._vals(model=model_id, port=free))
            progress("Starting %s…" % self.name)
            self._proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, start_new_session=True)
            self._owned = True
            deadline = time.time() + float((run.get("ready") or {}).get("timeout", 120))
            while time.time() < deadline:
                if self._proc.poll() is not None:
                    self._owned = False
                    raise ProviderError("%s stopped right after starting." % self.name)
                if self._health(free) is not None:
                    ep = self.endpoint(model_id) or self.endpoint(None)
                    if ep:
                        return ep
                time.sleep(0.5)
            self.stop()
            raise ProviderError("%s didn't become ready in time." % self.name)

    def stop(self):
        """Stop what Zest started. Returns (ok, message). Never stops an engine somebody else started."""
        with self._lock:
            if not self._owned or self._proc is None:
                return False, "Zest didn't start %s, so it leaves it running." % self.name
            run = self.spec.get("run") or {}
            try:
                if run.get("stop"):
                    subprocess.run(fill(run["stop"], **self._vals()), capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
                self._proc.terminate()
                self._proc.wait(timeout=float(run.get("stop_grace_secs", 20)))
            except subprocess.TimeoutExpired:
                self._proc.kill()
            except OSError:
                pass
            self._proc, self._owned = None, False
            return True, ""

    # -- updates (by command; the engine's own tool does the real work)
    def update_check(self):
        u = (self.spec.get("update") or {}).get("check")
        if not u or not self.binary():
            return {"available": False, "supported": False}
        try:
            r = subprocess.run(fill(u["command"], **self._vals()), capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
            d = json.JSONDecoder().raw_decode(r.stdout.lstrip())[0]
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            raise ProviderError("Couldn't check for updates: %s" % e)
        parse = u.get("parse") or {}
        flag = jpath(d, parse.get("available", "")) if parse.get("available") else []
        return {"supported": True, "available": bool(flag and flag[0] in (True, "available")), "serving": jpath(d, parse["serving"]) if parse.get("serving") else []}

