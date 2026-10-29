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
import signal
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
VARIABLES = {"bin", "host", "port", "model", "model_file", "preset", "family", "name", "context", "library", "data", "profile", "key"}
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
    def __init__(self, library="", data="", settings=None, save=None):
        self.library, self.data = library, data
        self._settings = settings or (lambda pid: {})
        self._save = save or (lambda pid, **kv: None)

    def settings(self, pid):
        return self._settings(pid)

    def remember(self, pid, **kv):
        """Keep a few facts about a provider across restarts of Zest (None removes one)."""
        self._save(pid, **kv)


def _start_time(pid):
    """When process `pid` began (clock ticks since boot, from /proc), or None when there is no such process. Tells a process from a later one
    that was given the same number."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            return int(f.read().rsplit(")", 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


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

    def prefers(self, model):
        """Should this provider be the one that gets the model, even when another could run it too? (`models.prefer_architectures`)"""
        return bool(model.get("architecture")) and model["architecture"] in ((self.spec.get("models") or {}).get("prefer_architectures") or [])

    # -- what the router asks: is it running anything, what is loaded, is it busy, what may be stopped when idle
    def ref(self, model_id):
        """The name Zest's chat and apps use for one of this provider's models. Default: `<provider id>/<model id>`."""
        return "%s/%s" % (self.id, model_id)

    def chat_models(self):
        """Models to offer in chat: [{id (the provider's own name), tools, reasoning, vision, ctx, live, ...}]. Default: what is running."""
        return [{"id": m["id"], "tools": True, "reasoning": False, "vision": False, "ctx": m.get("ctx") or 0, "live": True} for m in self.models()]

    def is_running(self):
        """Is anything of this provider's holding memory right now?"""
        return bool(self.models())

    def loaded(self, model_id):
        """(in memory?, its context window in tokens or 0 when unknown)."""
        m = next((m for m in self.models() if m["id"] == model_id), None)
        return (True, m.get("ctx") or 0) if m else (False, 0)

    def max_ctx(self, model_id):
        """The longest context the model supports, 0 when unknown."""
        return self.loaded(model_id)[1]

    def busy(self):
        """Is it answering a request right now?"""
        return False

    def activity(self):
        """(busy?, a counter that moves when it produces output) for idle detection, or (None, None) when it can't tell."""
        return None, None

    def owned_instance(self):
        """An identifier of the running thing Zest itself started (so it may stop it when idle), or None."""
        return None

    def idle_minutes(self):
        """Stop what Zest started after this many idle minutes; 0 never. Default from the YAML."""
        return int((self.spec.get("memory") or {}).get("idle_stop_minutes") or 0)

    def pi_endpoint(self, zest_port):
        """Where Pi should send requests for this provider: {base_url, key}. Default: the provider's own endpoint."""
        ep = self.endpoint()
        return {"base_url": ep["base_url"], "key": ep.get("key") or self.id} if ep else None

    # -- its models: where they live, what it calls them, how much room they need
    def model_folders(self):
        """[(path, label)] this provider keeps or finds model files in, for the library to read. Default: `models.data_dir`."""
        d = (self.spec.get("models") or {}).get("data_dir")
        if not d or not self.ctx.library:
            return []
        path = fill(d, **self._vals()) if hasattr(self, "_vals") else d
        return [(path, self.name)] if os.path.isdir(path) else []

    def model_id(self, entry):
        """The name this provider knows a library entry by, or None when it hasn't got it set up."""
        return None

    def library_models(self):
        """Models this provider keeps where Zest can't read the files (a service running as another user, a remote address), taken
        from its own list instead: [{stem, title, size_bytes, quant, vision, mtp, ids:{<id>: name}, listed_by: <id>, ...}]. Default: none."""
        return []

    def need_gb(self, entry):
        """Memory (graphics, plus RAM for the part that spills over) the entry needs to run on this provider, in GB: the model, a little
        working space, and what the context window costs (entry["kv_gb"], worked out from the model's own shape)."""
        return round(entry.get("size_bytes", 0) / 1e9 * 1.1 + (entry.get("kv_gb") or 0), 1)

    def fit(self, entry, machine):
        """("fits" | "tight" | "no", reason) for this entry on this computer."""
        need = self.need_gb(entry)
        gpu, vram, ram = machine.get("best_gpu_gb", 0), machine.get("vram_gb", 0), machine.get("ram_gb", 0)
        room = vram * 0.88 if len(machine.get("gpus") or []) > 1 else gpu * 0.9          # layers spread over several cards
        kv = entry.get("kv_gb") or 0
        part = " (%d GB of that is the memory for a %dk conversation)" % (round(kv), (entry.get("kv_ctx") or 0) // 1024) if kv >= 1.5 else ""
        if gpu and need <= room:
            return "fits", ""
        if need <= vram * 0.9 + ram * 0.5:
            return "tight", "Needs about %d GB%s; it will run partly on the processor, so it will be slower." % (need, part)
        return "no", "Needs about %d GB%s; this computer has %d GB of graphics memory and %d GB of RAM." % (need, part, vram, ram)

    def tier_hint(self):
        return str(self.spec.get("tier", ""))

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

    def update_status(self):
        """Where an update stands: {state: idle | working | done | error | deferred, text, error}. Default: idle."""
        return {"state": "idle", "text": "", "error": None}

    # -- serving
    def models(self):
        raise NotImplementedError

    def ensure(self, model_id, progress, opts=None):
        raise NotImplementedError

    def stop(self, model_id=None):
        raise NotImplementedError

    def endpoint(self, model_id=None):
        raise NotImplementedError

    def selftest(self):
        """Ask the real engine the questions Zest asks and say whether the answers still look as Zest expects: [{name, ok, detail}].
        Starts nothing. Empty when there is nothing to ask (not installed, not running)."""
        return []

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
        self._pid = None                # its process number (kept across restarts of Zest, so what Zest started is still Zest's)
        self._files = {}                # model name -> its file, learned as the library asks for names (models.serve: file)

    # -- helpers
    def _adopt_own(self):
        """After a restart of Zest (or a reload of the engines) find the process Zest started last time, if it is still there."""
        if self._owned:
            return
        rec = self.ctx.settings(self.id).get("started") or {}
        pid, port = rec.get("pid"), rec.get("port")
        if pid and port and _start_time(int(pid)) == rec.get("t") and port_open(int(port)):
            self._pid, self._port, self._owned = int(pid), int(port), True
        elif rec:
            self.ctx.remember(self.id, started=None)             # it has gone

    def _alive(self):
        if self._proc is not None:
            return self._proc.poll() is None
        return bool(self._pid) and _start_time(self._pid) is not None

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
        self._adopt_own()
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

    def model_id(self, entry):
        """With `models.serve: file` every library model this engine can run is served from its file: its name is the file's stem."""
        if (self.spec.get("models") or {}).get("serve") != "file" or not entry.get("files") or entry.get("complete") is False:
            return None
        if entry["stem"].startswith("-"):
            return None                                           # a program could take the name for an option; such a file is not offered
        self._files[entry["stem"]] = entry["files"][0]            # a split model starts from its first part
        return entry["stem"]

    def activity(self):
        a = (self.spec.get("capabilities") or {}).get("activity") or {}
        if a.get("path") and self._port:
            status, d = http_json(self._root(self._port) + a["path"], self._key())
            if status == 200:
                flags = jpath(d, a.get("busy", "")) if a.get("busy") else []
                return any(bool(f) for f in flags), (sum(x for x in jpath(d, a["tokens"]) if isinstance(x, (int, float))) if a.get("tokens") else None)
        return None, None                # it can't say, so the clock never stops it (declare capabilities.activity to allow that)

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
        if str(model_id).startswith("-"):
            raise ProviderError("The name of that model starts with a dash, which %s could mistake for an option. Rename the file." % self.name)
        files = (self.spec.get("models") or {}).get("serve") == "file"
        if files and not self._files.get(model_id):
            raise ProviderError("%s doesn't know where the file for %s is. Open the Library and try again." % (self.name, model_id))
        with self._lock:
            if self.owned_instance() and not (self.spec.get("capabilities") or {}).get("multi_model"):
                self.stop()                                          # one model at a time: make room for this one
            used = {p for p in port_range(run.get("port_range") or [])}
            free = next((p for p in sorted(used) if not port_open(p)), None)
            if free is None:
                raise ProviderError("No free port for %s." % self.name)
            self._port = free
            ctx = int((opts or {}).get("ctx") or self.ctx.settings(self.id).get("context") or 131072)
            cmd = fill(run["start"], **self._vals(model=model_id, model_file=self._files.get(model_id, ""), port=free, context=ctx))
            progress("Starting %s…" % self.name)
            self._proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, start_new_session=True)
            self._owned, self._pid = True, self._proc.pid
            self.ctx.remember(self.id, started={"pid": self._pid, "port": free, "t": _start_time(self._pid)})
            deadline = time.time() + float((run.get("ready") or {}).get("timeout", 120))
            while time.time() < deadline:
                if self._proc.poll() is not None:
                    self._owned, self._pid = False, None
                    self.ctx.remember(self.id, started=None)
                    raise ProviderError("%s stopped right after starting." % self.name)
                if self._health(free) is not None:
                    ep = self.endpoint(model_id) or self.endpoint(None)
                    if ep:
                        return ep
                time.sleep(0.5)
            self.stop()
            raise ProviderError("%s didn't become ready in time." % self.name)

    def selftest(self):
        out = []
        for m in self.detect().get("running", []):
            status, d = http_json(self._root(m["port"]) + ((self.spec.get("endpoint") or {}).get("models") or {}).get("path", "/v1/models"), self._key())
            out.append({"name": "its model list (port %s)" % m["port"], "ok": status == 200,
                        "detail": "" if status == 200 else "answered %s instead of 200" % status})
        return out

    def owned_instance(self):
        self._adopt_own()
        return self._port if (self._owned and self._alive()) else None

    def stop(self, model_id=None):
        """Stop what Zest started. Returns (ok, message). Never stops an engine somebody else started."""
        with self._lock:
            self._adopt_own()
            if not self._owned or not self._pid:
                return False, "Zest didn't start %s, so it leaves it running." % self.name
            run = self.spec.get("run") or {}
            grace = float(run.get("stop_grace_secs", 20))
            try:
                if run.get("stop"):
                    subprocess.run(fill(run["stop"], **self._vals()), capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
                self._end(signal.SIGTERM)
                if not self._wait_gone(grace):
                    self._end(signal.SIGKILL)
                    self._wait_gone(5)
            except (OSError, subprocess.SubprocessError):
                pass
            self._proc, self._owned, self._pid = None, False, None
            self.ctx.remember(self.id, started=None)
            return True, ""

    def _end(self, sig):
        """Signal what Zest started, and what that started (it was made the leader of its own group)."""
        try:
            os.killpg(self._pid, sig)
        except (OSError, ProcessLookupError):
            try:
                os.kill(self._pid, sig)
            except OSError:
                pass

    def _wait_gone(self, secs):
        end = time.time() + secs
        while time.time() < end:
            if self._proc is not None:
                if self._proc.poll() is not None:
                    return True
            elif _start_time(self._pid) is None:
                return True
            time.sleep(0.1)
        return False

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

