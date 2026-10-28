"""Finds providers (engines), checks their YAML, and hands out the ones that work.

Folders are looked for in two places: `providers/` next to this file (bundled, trusted) and `providers/` in Zest's data folder
(added by the person, asked about once if they run a program). A folder that is wrong is skipped and reported with the exact
line to fix; it never stops Zest or the other providers. Standard library only (YAML is read by the copy in _vendor).
"""
import hashlib
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "_vendor"))       # Zest's Python has no site-packages
import yaml  # noqa: E402

import provbase  # noqa: E402
import store  # noqa: E402

ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
KNOWN = {"id", "name", "summary", "tier", "api_version", "detect", "install", "update", "run", "endpoint", "models", "memory",
         "capabilities", "options", "docs"}
# The settings Zest reads inside each group. Anything else is probably a typo, and is pointed out (never silently ignored).
SECTIONS = {
    "detect": {"binaries", "ports", "health"},
    "install": {"method", "url", "checksum", "unsafe_install"},
    "update": {"check", "apply", "series_guard"},
    "run": {"mode", "start", "stop", "ready", "stop_grace_secs", "port_range"},
    "endpoint": {"base_url", "models"},
    "models": {"formats", "architectures", "excluded_architectures", "prefer_architectures", "data_dir", "download", "setup", "view", "serve"},
    "memory": {"group", "exclusive", "idle_stop_minutes", "start_estimate", "max_loaded"},
    "capabilities": {"context", "multi_model", "tuning", "activity"},
}
LISTS = {("detect", "binaries"), ("models", "formats"), ("models", "architectures"), ("models", "excluded_architectures"), ("models", "prefer_architectures")}
MAX_YAML = 200_000


def _commands(spec):
    """Every (path, value) in the spec that is a command: a list of strings run without a shell."""
    out = []
    run = spec.get("run") or {}
    for k in ("start", "stop"):
        if k in run:
            out.append(("run." + k, run[k]))
    for k in ("check", "apply"):
        c = (spec.get("update") or {}).get(k)
        if isinstance(c, dict) and "command" in c:
            out.append(("update.%s.command" % k, c["command"]))
    m = spec.get("models") or {}
    for k in ("command", "plan"):
        if k in (m.get("setup") or {}):
            out.append(("models.setup." + k, m["setup"][k]))
    t = (spec.get("capabilities") or {}).get("tuning")
    if isinstance(t, dict):
        for k in ("plan", "run", "undo"):
            if k in t and isinstance(t[k], list):
                out.append(("capabilities.tuning." + k, t[k]))
    return out


def _walk_strings(o, path=""):
    if isinstance(o, str):
        yield path, o
    elif isinstance(o, dict):
        for k, v in o.items():
            yield from _walk_strings(v, "%s.%s" % (path, k) if path else str(k))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _walk_strings(v, "%s[%d]" % (path, i))


def validate(spec, has_code=False):
    """List of problems in a parsed provider.yaml, each saying where and what. Empty means usable."""
    if not isinstance(spec, dict):
        return ["The file must be a list of settings (key: value), not %s." % type(spec).__name__]
    bad = []
    for sec in list(SECTIONS) + ["options"]:
        if spec.get(sec) is not None and not isinstance(spec[sec], dict):
            bad.append("%s: must be a group of settings (key: value lines, indented under %s:)." % (sec, sec))
    for sec, key in sorted(LISTS):
        if isinstance((spec.get(sec) or {}).get(key), (str, int)) or isinstance((spec.get(sec) or {}).get(key), dict):
            bad.append("%s.%s: must be a list, like [a, b]." % (sec, key))
    if bad:
        return bad                      # the checks below assume the shape is right
    if not ID_RE.match(str(spec.get("id", ""))):
        bad.append("id: must be 2 to 32 characters, lowercase letters, digits, - or _, starting with a letter.")
    if not spec.get("name"):
        bad.append("name: is missing (the friendly name shown under Advanced > Engines).")
    if spec.get("api_version") != provbase.API_VERSION:
        bad.append("api_version: must be %d (this Zest reads version %d files)." % (provbase.API_VERSION, provbase.API_VERSION))
    run = spec.get("run") or {}
    mode = run.get("mode", "managed")
    if mode not in provbase.RUN_MODES:
        bad.append("run.mode: must be one of %s, not %r." % (", ".join(provbase.RUN_MODES), mode))
    if mode == "managed" and not has_code and not run.get("start"):
        bad.append("run.start: a managed engine needs the command that starts it.")
    for path, c in _commands(spec):
        if not (isinstance(c, list) and c and all(isinstance(x, (str, int)) for x in c)):
            bad.append("%s: a command is a list of words, like [\"{bin}\", serve, --port, \"{port}\"]; never one shell string." % path)
    if not has_code and mode != "service" and not (spec.get("endpoint") or {}).get("base_url"):
        bad.append("endpoint.base_url: the address other programs use, like http://127.0.0.1:{port}/v1.")
    inst = (spec.get("install") or {})
    if inst.get("url") and not str(inst["url"]).startswith("https://"):
        bad.append("install.url: must start with https://.")
    if inst.get("url") and not inst.get("checksum") and not inst.get("unsafe_install"):
        bad.append("install: a download needs a `checksum` (url and name), or `unsafe_install: true` to say it has none.")
    for k in ("ports",):
        try:
            provbase.port_range((spec.get("detect") or {}).get(k) or [])
            provbase.port_range(run.get("port_range") or [])
        except ValueError:
            bad.append("detect.ports / run.port_range: write ports as 8080 or 8080-8089.")
    for path, t in _walk_strings(spec):
        try:
            provbase.fill(t, **{})
        except provbase.ProviderError as e:
            bad.append("%s: %s" % (path, e))
        except (ValueError, IndexError, KeyError):
            bad.append("%s: curly braces must hold a single variable name, like {port}." % path)
    files = (spec.get("models") or {}).get("serve")
    if files not in (None, "file"):
        bad.append("models.serve: the only choice is `file` (each model is started from its file).")
    uses_file = any("{model_file}" in str(c) for _, c in _commands(spec) for c in [" ".join(map(str, c)) if isinstance(c, list) else ""])
    if mode == "managed" and not has_code:
        if not (spec.get("detect") or {}).get("health") and not (run.get("ready") or {}).get("path"):
            bad.append("run.ready: say how Zest can tell the engine is ready, like {path: /health} (or set detect.health).")
        if files == "file" and not uses_file:
            bad.append("models.serve is `file`, but run.start never uses {model_file}, so the engine would not be told which file to load.")
        if uses_file and files != "file":
            bad.append("run.start uses {model_file}, which only has a value when models.serve is `file`.")
    unknown = [k for k in spec if k not in KNOWN]
    if unknown:
        bad.append("Unknown top-level setting(s): %s." % ", ".join(sorted(unknown)))
    return bad


def fingerprint(folder):
    h = hashlib.sha256()
    for n in ("provider.yaml", "provider.py"):
        p = os.path.join(folder, n)
        if os.path.isfile(p):
            with open(p, "rb") as f:
                h.update(n.encode() + f.read())
    return h.hexdigest()


class Registry:
    def __init__(self, bundled=None, user=None):
        self.bundled = bundled if bundled is not None else os.path.join(HERE, "providers")
        self.user = user if user is not None else os.path.join(store.data_dir(), "providers")
        self.providers, self.errors = {}, []

    # -- what providers are told about Zest
    def context(self):
        s = store.load()
        return provbase.Context(library=(s.get("library") or {}).get("root", ""), data=store.data_dir(),
                                settings=lambda pid: (store.load().get("providers") or {}).get(pid, {}))

    def load(self):
        self.providers, self.errors = {}, []
        for root, trusted in ((self.bundled, True), (self.user, False)):
            if not os.path.isdir(root):
                continue
            for name in sorted(os.listdir(root)):
                folder = os.path.join(root, name)
                if os.path.isfile(os.path.join(folder, "provider.yaml")) and not name.startswith("_"):
                    self._load_one(folder, trusted)
        return self

    def _load_one(self, folder, trusted):
        try:
            with open(os.path.join(folder, "provider.yaml")) as f:
                text = f.read(MAX_YAML + 1)
            if len(text) > MAX_YAML:
                raise ValueError("it is far larger than any engine file should be")
            spec = yaml.safe_load(text)
        except (OSError, ValueError, yaml.YAMLError) as e:
            line = getattr(getattr(e, "problem_mark", None), "line", None)
            self.errors.append({"folder": folder, "error": "The file couldn't be read%s: %s" % (" (line %d)" % (line + 1) if line is not None else "", getattr(e, "problem", None) or e)})
            return
        code = os.path.join(folder, "provider.py")
        try:
            problems = validate(spec, has_code=os.path.isfile(code))
        except Exception as e:  # noqa: BLE001
            problems = ["Couldn't check the file (%s). It probably has a setting of the wrong kind." % e]
        if problems:
            self.errors.append({"folder": folder, "error": " ".join(problems)})
            return
        if spec["id"] in self.providers:
            self.errors.append({"folder": folder, "error": "Another engine already uses the id %r. Change id: in this file." % spec["id"]})
            return
        cls = provbase.GenericProvider
        if os.path.isfile(code):
            try:
                cls = self._import(code, spec["id"])
            except Exception as e:  # noqa: BLE001
                self.errors.append({"folder": folder, "error": "provider.py couldn't be loaded: %s" % e})
                return
        try:
            p = cls(spec, folder, self.context())
        except Exception as e:  # noqa: BLE001
            self.errors.append({"folder": folder, "error": "The engine couldn't be set up: %s" % e})
            return
        p.trusted = trusted
        p.approved = trusted or self.is_approved(p)
        self.providers[p.id] = p

    @staticmethod
    def _import(path, pid):
        spec = importlib.util.spec_from_file_location("zest_provider_" + pid.replace("-", "_"), path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        cls = getattr(mod, "Provider", None)
        if not (isinstance(cls, type) and issubclass(cls, provbase.Base)):
            raise ValueError("it must define `class Provider(provbase.Base)`")
        return cls

    # -- approval of providers the person added
    def is_approved(self, p):
        return ((store.load().get("providers") or {}).get(p.id) or {}).get("approved") == fingerprint(p.folder)

    def consent(self, p):
        """What a not-yet-approved provider would do, in words, or None when it needs no approval."""
        if p.trusted:
            return None
        spec, runs = p.spec, []
        run = spec.get("run") or {}
        if run.get("mode", "managed") == "managed" and run.get("start"):
            exe = os.path.basename(p.binary() or "") if hasattr(p, "binary") and p.binary() else os.path.basename(str(run["start"][0]).replace("{bin}", "its program"))
            runs.append("start a program on your computer (%s)" % (exe or "its program"))
        inst = spec.get("install") or {}
        if inst.get("url"):
            runs.append("download and run an installer from %s" % inst["url"])
        code = os.path.isfile(os.path.join(p.folder, "provider.py"))
        if code:
            runs.append("run its own Python code inside Zest")
        if not runs:
            return None                                    # it only points at an address that is already running
        return {"id": p.id, "name": p.name, "will": runs, "code": code, "unsafe_install": bool(inst.get("unsafe_install")),
                "commands": [{"where": w, "command": [(p.binary() or "its program") if str(x) == "{bin}" else x for x in c]} for w, c in _commands(spec)]}

    def approve(self, pid):
        p = self.providers.get(pid)
        if not p:
            raise provbase.ProviderError("No such engine.")
        s = store.load()
        s.setdefault("providers", {}).setdefault(pid, {})["approved"] = fingerprint(p.folder)
        store.save(s)
        p.approved = True

    # -- what the rest of Zest uses
    def get(self, pid):
        return self.providers.get(pid)

    def all(self):
        return list(self.providers.values())

    def summary(self):
        out = []
        for p in self.all():
            try:
                st = p.status()
            except NotImplementedError:
                st = p.summary()
            except Exception as e:  # noqa: BLE001
                st = dict(p.summary(), problems=[str(e)])
            try:
                us = p.update_status()
            except Exception:  # noqa: BLE001
                us = {"state": "idle", "text": "", "error": None}
            out.append(dict(st, consent=self.consent(p) if not p.approved else None, update=us))
        return {"providers": out, "errors": list(self.errors), "user_folder": self.user}


_default = {"r": None}


def default():
    if _default["r"] is None:
        _default["r"] = Registry().load()
    return _default["r"]


def reload():
    _default["r"] = Registry().load()
    return _default["r"]
