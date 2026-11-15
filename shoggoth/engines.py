"""Engine definitions: every inference engine and service Shoggoth knows by name, as data.

One file holds one definition or a list: `engines.d/*.yaml` or `*.json` next to this file (bundled), and the same folder in Shoggoth's
data folder (yours). A definition of yours with the same `id` replaces the bundled one; `enabled: false` hides it. Files are read again
when any of them changes. A definition can name and describe an engine, say where to find it and how to recognise it. It can't start,
install or run anything: any key outside the schema is refused, and so is a url that isn't http(s) or a probe path that isn't a plain
GET path.

A file with mistakes still loads what it can. Each bad definition is skipped and reported as {file, line?, error}, see `problems()`.
`python3 shoggoth/server.py engines check <file>` prints the same report for one file. Standard library only (YAML is read by the copy
in _vendor).
"""
import difflib
import json
import os
import re
import sys
import threading
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "_vendor"))
import yaml  # noqa: E402

import store  # noqa: E402

BUNDLED = os.path.join(HERE, "engines.d")
EXTS = (".yaml", ".yml", ".json")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]*$")                  # a plain GET path: no query, no fragment, no host
JSON_RE = re.compile(r"^\$[A-Za-z0-9_.\[\]*-]*$")
KEYS = ("id", "name", "category", "summary", "url", "ports", "match", "cwd", "identify", "formats", "context", "loads", "key", "key_url",
        "docs", "hint", "enabled")
CATEGORIES = ("local", "specialized", "hosted")
LOADS = ("router", "ollama", "fixed")
KEY_MODES = ("none", "optional", "required")
APIS = ("openai-completions", "openai-responses", "anthropic-messages", "google-generative-ai")
IDENTIFY_KEYS = ("path", "json", "equals", "header", "contains")
MAX_FILE = 500_000
_lock = threading.Lock()
_cache = {"sig": None, "val": None}


def user_dir():
    return os.path.join(store.data_dir(), "engines.d")


def _files(folder):
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    return [os.path.join(folder, n) for n in names if n.lower().endswith(EXTS) and os.path.isfile(os.path.join(folder, n))]


# ---------------------------------------------------------------- checking one definition
def _scalar(v):
    return isinstance(v, (str, int, float)) and not isinstance(v, bool)


def _url(v, errs, key, empty_ok=True):
    if v in (None, ""):
        if not empty_ok:
            errs.append((key, "`%s` is needed." % key))
        return ""
    if not isinstance(v, str):
        errs.append((key, "`%s` must be a web address." % key))
        return ""
    u = urllib.parse.urlsplit(v.strip())
    try:
        u.port
    except ValueError:
        errs.append((key, "`%s` has a port that isn't a number between 1 and 65535." % key))
        return ""
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
        errs.append((key, "`%s` must start with http:// or https:// and have no user name in it." % key))
        return ""
    return v.strip().rstrip("/")


def _text(d, key, errs, need=False, limit=600):
    v = d.get(key)
    if v is None:
        if need:
            errs.append((key, "needs `%s`." % key))
        return ""
    if _scalar(v):
        v = str(v)
    if not isinstance(v, str) or (need and not v.strip()):
        errs.append((key, "`%s` must be some text." % key))
        return ""
    if len(v) > limit:
        errs.append((key, "`%s` is longer than %d characters." % (key, limit)))
        return ""
    return v.strip()


def _choice(d, key, options, default, errs):
    v = d.get(key, default)
    if v not in options:
        errs.append((key, "`%s` must be one of: %s." % (key, ", ".join(options))))
        return default
    return v


def _words(d, key, errs):
    v = d.get(key)
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list) or not all(_scalar(x) and str(x).strip() for x in v):
        errs.append((key, "`%s` must be a list, like [a, b]." % key))
        return []
    return [str(x).strip() for x in v]


def _group(d, key, errs, allowed, label):
    """The list of groups under `key`, each with only the keys in `allowed`."""
    v = d.get(key)
    if v is None:
        return []
    if not isinstance(v, list):
        errs.append((key, "`%s` must be a list (lines starting with -)." % key))
        return []
    out = []
    for i, x in enumerate(v):
        if not isinstance(x, dict):
            errs.append((key, "%s %d must be a group of settings (key: value)." % (label, i + 1)))
            continue
        bad = [k for k in x if k not in allowed]
        for k in bad:
            errs.append((k, "unknown key `%s` in %s %d of `%s`." % (k, label, i + 1, key)))
        if not bad:
            out.append(x)
    return out


def _probe_path(v, errs, key):
    if not isinstance(v, str) or not PATH_RE.match(v) or ".." in v or "//" in v:
        errs.append((key, "`%s` must be a plain path such as /props (no address, no ?query)." % key))
        return None
    return v


def _json_path(v, errs, key):
    if not isinstance(v, str) or not JSON_RE.match(v):
        errs.append((key, "`%s` must be a path such as $.data[*].owned_by." % key))
        return None
    return v


def _identify(d, errs):
    out = []
    for x in _group(d, "identify", errs, IDENTIFY_KEYS, "signature"):
        n = len(errs)
        item = {"path": _probe_path(x.get("path"), errs, "path")}
        if "json" in x:
            item["json"] = _json_path(x["json"], errs, "json")
        if "header" in x:
            if not isinstance(x["header"], str) or not re.fullmatch(r"[A-Za-z0-9-]{1,40}", x["header"]):
                errs.append(("header", "`header` must be a header name such as Server."))
            item["header"] = x["header"]
            if "equals" not in x and "contains" not in x:
                errs.append(("header", "a `header` signature needs `equals` or `contains`."))
        for k in ("equals", "contains"):
            if k in x:
                if not _scalar(x[k]) and not isinstance(x[k], bool):
                    errs.append((k, "`%s` must be a word or number." % k))
                item[k] = str(x[k])
        if len(errs) == n:
            out.append(item)
    return out


def _formats(d, errs):
    out = []
    for x in _group(d, "formats", errs, ("api", "match"), "format"):
        n = len(errs)
        api, pat = x.get("api"), x.get("match")
        if api not in APIS:
            errs.append(("api", "`api` must be one of: %s." % ", ".join(APIS)))
        if pat is not None:
            try:
                re.compile(str(pat))
            except re.error as e:
                errs.append(("match", "`match` isn't a usable pattern (%s)." % e))
        if len(errs) == n:
            out.append({"api": api, **({"match": str(pat)} if pat is not None else {})})
    return out


def _context(d, errs):
    v = d.get("context")
    if v in (None, {}):
        return {}
    if not isinstance(v, dict):
        errs.append(("context", "`context` must be a group with `path` and `json`."))
        return {}
    n = len(errs)
    for k in v:
        if k not in ("path", "json"):
            errs.append((k, "unknown key `%s` in `context`." % k))
    p, j = _probe_path(v.get("path"), errs, "path"), _json_path(v.get("json"), errs, "json")
    return {"path": p, "json": j} if len(errs) == n else {}


def _ports(d, errs):
    v = d.get("ports")
    if v is None:
        return []
    if isinstance(v, int) and not isinstance(v, bool):
        v = [v]
    if not isinstance(v, list) or not all(isinstance(p, int) and not isinstance(p, bool) and 0 < p < 65536 for p in v):
        errs.append(("ports", "`ports` must be a list of port numbers, like [8080]."))
        return []
    return list(v)


def clean(d):
    """(definition, errors) for one parsed entry. Errors are [(key, message)]; the definition is None when there are any."""
    errs = []
    if not isinstance(d, dict):
        return None, [("", "a definition must be a group of settings (key: value).")]
    for k in d:
        if k not in KEYS:
            near = difflib.get_close_matches(str(k), KEYS, 1)
            errs.append((str(k), "unknown key `%s`%s. A definition can't carry commands." % (k, " (did you mean `%s`?)" % near[0] if near else "")))
    eid = d.get("id")
    if not isinstance(eid, str) or not ID_RE.match(eid):
        errs.append(("id", "`id` must be lowercase letters, digits, - or _ (up to 48)."))
        eid = None
    enabled = d.get("enabled", True)
    if not isinstance(enabled, bool):
        errs.append(("enabled", "`enabled` must be true or false."))
        enabled = True
    out = {"id": eid, "enabled": enabled, "name": _text(d, "name", errs, need=enabled) or (eid or "")}
    out["category"] = _choice(d, "category", CATEGORIES, "local", errs)
    out["summary"] = _text(d, "summary", errs)
    out["url"] = _url(d.get("url"), errs, "url")
    out["ports"] = _ports(d, errs)
    out["match"] = _words(d, "match", errs)
    out["cwd"] = _words(d, "cwd", errs)
    out["identify"] = _identify(d, errs)
    out["formats"] = _formats(d, errs)
    out["context"] = _context(d, errs)
    out["loads"] = _choice(d, "loads", LOADS, "fixed", errs)
    out["key"] = _choice(d, "key", KEY_MODES, "none", errs)
    out["key_url"] = _url(d.get("key_url"), errs, "key_url")
    out["docs"] = _url(d.get("docs"), errs, "docs")
    out["hint"] = _text(d, "hint", errs)
    if out["url"] and not out["ports"]:
        p = urllib.parse.urlsplit(out["url"]).port
        out["ports"] = [p] if p and urllib.parse.urlsplit(out["url"]).hostname in ("127.0.0.1", "localhost", "::1") else []
    return (None if errs else out), errs


# ---------------------------------------------------------------- reading files
def _line(text, key):
    """The first line that holds `key` as a key, else None."""
    if not key:
        return None
    pat = re.compile(r"""^\s*(?:-\s*)?["']?%s["']?\s*:""" % re.escape(key))
    for i, ln in enumerate(text.splitlines(), 1):
        if pat.match(ln):
            return i
    return None


def _problem(label, error, line=None):
    p = {"file": label, "error": error}
    if line:
        p["line"] = line
    return p


MAX_YAML_NODES = 20000       # nodes after aliases are expanded
MAX_YAML_DEPTH = 40


class YamlTooBig(yaml.YAMLError):
    problem = "it uses too many anchors/aliases or is nested too deeply"


def _yaml_load(text):
    """yaml.safe_load, but the node graph is measured first: aliases make a few lines expand into a huge value ("billion laughs"),
    so a file whose expanded size or depth is over the limit is refused before anything is built."""
    loader = yaml.SafeLoader(text)
    try:
        node = loader.get_single_node()
        if node is not None:
            sizes, active = {}, set()

            def size(n, depth):
                if depth > MAX_YAML_DEPTH or id(n) in active:        # too deep, or an anchor that contains itself
                    raise YamlTooBig()
                if id(n) in sizes:
                    return sizes[id(n)]
                active.add(id(n))
                total = 1
                kids = n.value if isinstance(n, yaml.SequenceNode) else [x for kv in n.value for x in kv] if isinstance(n, yaml.MappingNode) else []
                for k in kids:
                    total += size(k, depth + 1)
                    if total > MAX_YAML_NODES:
                        raise YamlTooBig()
                active.discard(id(n))
                sizes[id(n)] = total
                return total
            size(node, 0)
        return loader.construct_document(node) if node is not None else None
    finally:
        loader.dispose()


def read_file(fname, label=None):
    """([definitions], [problems]) for one file. A bad definition is left out and reported; the rest still load."""
    label = label or os.path.basename(fname)
    try:
        with open(fname, encoding="utf8") as f:
            text = f.read(MAX_FILE + 1)
    except (OSError, UnicodeDecodeError) as e:
        return [], [_problem(label, "couldn't read it (%s)." % (getattr(e, "strerror", None) or "not text"))]
    if len(text) > MAX_FILE:
        return [], [_problem(label, "the file is bigger than %d KB." % (MAX_FILE // 1000))]
    try:
        doc = json.loads(text) if fname.lower().endswith(".json") else _yaml_load(text)
    except json.JSONDecodeError as e:
        return [], [_problem(label, "isn't valid JSON: %s." % e.msg, e.lineno)]
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        return [], [_problem(label, "isn't valid YAML: %s." % (getattr(e, "problem", None) or e), mark.line + 1 if mark else None)]
    items = doc if isinstance(doc, list) else [doc]
    if doc is None or items == []:
        return [], [_problem(label, "holds no definitions.")]
    defs, probs = [], []
    for item in items:
        d, errs = clean(item)
        for key, msg in errs:
            probs.append(_problem(label, msg, _line(text, key)))
        if d is not None:
            defs.append(d)
    return defs, probs


def load():
    """{engines, problems}: bundled definitions first, then yours (same id replaces, enabled false hides)."""
    byid, order, probs = {}, [], []
    for folder, tag in ((BUNDLED, "engines.d"), (user_dir(), "engines.d")):
        seen_files = _files(folder)
        for fname in seen_files:
            defs, p = read_file(fname, "%s/%s" % (tag, os.path.basename(fname)))
            probs += p
            for d in defs:
                if d["id"] not in byid:
                    order.append(d["id"])
                byid[d["id"]] = d
    engines = [byid[i] for i in order if byid[i]["enabled"]]
    for e in engines:
        if e["category"] == "hosted":
            e["host"] = urllib.parse.urlsplit(e["url"]).hostname or ""      # where the key goes, for the UI to show beside the key field
    engines.sort(key=lambda e: e["id"] == "other")           # the catch-all is always last
    return {"engines": engines, "problems": probs}


def _sig():
    out = []
    for folder in (BUNDLED, user_dir()):
        out.append(folder)
        for f in _files(folder):
            try:
                out.append((f, os.stat(f).st_mtime_ns))
            except OSError:
                out.append((f, None))
    return tuple(out)


def get():
    """load(), kept until a file is added, removed or changed."""
    sig = _sig()
    with _lock:
        if _cache["sig"] != sig:
            _cache["val"], _cache["sig"] = load(), sig
        return _cache["val"]


def definitions():
    return [dict(e) for e in get()["engines"]]


def presets():
    return definitions()


def by_id(eid):
    return next((e for e in definitions() if e["id"] == eid), None)


DEFAULT_API = "openai-completions"


def api_for(engine, model):
    """The API format a hosted service wants for one model: the first `formats` rule whose `match` fits the model name (a rule with no
    `match` fits everything), else OpenAI chat completions. `engine` is a definition id or a definition."""
    d = by_id(engine) if isinstance(engine, str) else engine
    for rule in (d or {}).get("formats") or []:
        pat = rule.get("match")
        if pat is None or re.search(pat, str(model or ""), re.I):
            return rule["api"]
    return DEFAULT_API


def pi_base_url(api, url):
    """The base address Pi's client for `api` wants, given a service's OpenAI-style address (".../v1"). Pi's Anthropic client adds /v1/messages
    itself, the Responses client adds /responses, and Google's adds /models/<name>:streamGenerateContent after the version."""
    u = str(url or "").rstrip("/")
    if api == "anthropic-messages":
        return u[:-3] if u.endswith("/v1") else u
    if api == "google-generative-ai":
        i = u.find("/v1beta")
        if i >= 0:
            return u[:i + 7]
        return (u[:-3] if u.endswith("/v1") else u) + "/v1beta"
    return u


def gemini_chat_models(listing):
    """Names of the models in a Gemini `GET /v1beta/models` reply that can chat (`supportedGenerationMethods` has generateContent), without
    the `models/` prefix. Embedding, image-only and other models stay out of the picker."""
    out = []
    for m in (listing.get("models") if isinstance(listing, dict) else None) or []:
        if isinstance(m, dict) and "generateContent" in (m.get("supportedGenerationMethods") or []) and m.get("name"):
            n = str(m["name"])
            out.append(n[7:] if n.startswith("models/") else n)
    return out


def problems():
    """[{file, line?, error}] for every definition that was left out, bundled or yours."""
    return [dict(p) for p in get()["problems"]]


# ---------------------------------------------------------------- the command line
def main(argv):
    """`engines check <file>...`. Returns the exit code."""
    if len(argv) < 2 or argv[0] != "check":
        print("usage: engines check <file.yaml|file.json> ...")
        return 2
    bad = 0
    for f in argv[1:]:
        defs, probs = read_file(os.path.abspath(f), f)
        print("%s %s: %d definition%s" % ("✗" if probs else "✓", f, len(defs), "" if len(defs) == 1 else "s"))
        for p in probs:
            print("  ✗ %s%s" % ("line %d: " % p["line"] if p.get("line") else "", p["error"]))
        bad += len(probs)
    return 1 if bad else 0
