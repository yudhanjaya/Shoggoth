"""Recommendations: the model cards and tools Shoggoth suggests, read from YAML so they change without touching code.

Two files in `recommendations/` next to this one: models.yaml (Library > Recommended, the first-run picks, Strata's big models) and
tools.yaml (apps, skills, engines and guides: where each shows, how to connect it, how to recognise it in the Server log). A file of
the same name in Shoggoth's data folder (`recommendations/`) is used instead of the bundled one. A file is read again when it changes.

A file with mistakes still loads what it can: a bad entry is skipped and reported with where it is and what is wrong, and a file that
isn't YAML at all falls back to the bundled one. `python3 shoggoth/server.py recommendations check` prints the report. Standard library
only (YAML is read by the copy in _vendor).
"""
import difflib
import os
import re
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "_vendor"))
import yaml  # noqa: E402

import store  # noqa: E402

BUNDLED = os.path.join(HERE, "recommendations")
FILES = ("models", "tools", "flavour")
FLAVOUR_SETS = ("loading", "reading", "thinking", "writing", "grasp", "quill", "seek", "eye", "sigil", "edit", "crypt", "tome", "orb",
                 "threshold", "maw", "severed", "idle")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
KINDS = ("app", "skills", "engine", "guide")
PLACES = ("library", "server", "engines", "guides")
FIELDS = ("base_url", "key", "model")
SIZES = ("small", "medium", "large")
MAX_YAML = 500_000
_lock = threading.Lock()
_cache = {}


def user_dir():
    return os.path.join(store.data_dir(), "recommendations")


def path(name):
    """The file Shoggoth reads for `name`: yours when there is one, else the bundled one."""
    mine = os.path.join(user_dir(), name + ".yaml")
    return mine if os.path.isfile(mine) else os.path.join(BUNDLED, name + ".yaml")


# ---------------------------------------------------------------- checking one value
class _Check:
    """Collects problems as "file: where: what" lines while values are cleaned."""
    def __init__(self, fname):
        self.fname, self.problems = fname, []

    def bad(self, where, what):
        self.problems.append("%s: %s: %s" % (self.fname, where, what) if where else "%s: %s" % (self.fname, what))

    def keys(self, d, where, known):
        for k in d:
            if k not in known:
                near = difflib.get_close_matches(str(k), known, 1)
                self.bad(where, "`%s` isn't a setting Shoggoth reads%s, so it is ignored." % (k, " (did you mean `%s`?)" % near[0] if near else ""))

    def text(self, d, key, where, need=False):
        v = d.get(key)
        if v is None:
            if need:
                self.bad(where, "needs `%s`." % key)
            return None
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            v = str(v)
        if not isinstance(v, str) or not v.strip():
            self.bad(where, "`%s` must be some text." % key)
            return None
        return v.strip()

    def number(self, d, key, where, need=False):
        v = d.get(key)
        if v is None:
            if need:
                self.bad(where, "needs `%s`, a number." % key)
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            self.bad(where, "`%s` must be a number above 0." % key)
            return None
        return v

    def flag(self, d, key, where, default=False):
        v = d.get(key, default)
        if not isinstance(v, bool):
            self.bad(where, "`%s` must be true or false." % key)
            return default
        return v

    def words(self, d, key, where):
        v = d.get(key)
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list) or not all(isinstance(x, (str, int, float)) and not isinstance(x, bool) for x in v):
            self.bad(where, "`%s` must be a list, like [a, b]." % key)
            return []
        return [str(x) for x in v]

    def items(self, d, key, where):
        """A list of groups of settings; anything else in it is reported and skipped."""
        v = d.get(key)
        if v is None:
            return []
        if not isinstance(v, list):
            self.bad(where, "`%s` must be a list (lines starting with -)." % key)
            return []
        out = []
        for i, x in enumerate(v):
            if isinstance(x, dict):
                out.append((i, x))
            else:
                self.bad("%s[%d]" % (key, i), "must be a group of settings (key: value), not %s." % type(x).__name__)
        return out


# ---------------------------------------------------------------- models.yaml
def clean_models(doc, c):
    """The usable part of a parsed models.yaml: {recommended, first_run, strata}."""
    out = {"recommended": [], "first_run": [], "strata": []}
    if not isinstance(doc, dict):
        c.bad("", "the file must be groups of settings (key: value), not %s." % type(doc).__name__)
        return out
    c.keys(doc, "", ["version", "recommended", "first_run", "strata"])
    if doc.get("version", 1) != 1:
        c.bad("version", "Shoggoth reads version 1.")
    ids = set()
    for i, m in c.items(doc, "recommended", ""):
        w = "recommended[%d]" % i
        c.keys(m, w, ["id", "also", "label", "take"])
        mid, label = c.text(m, "id", w, need=True), c.text(m, "label", w, need=True)
        if mid in ids:
            c.bad(w, "%s is listed twice." % mid)
        elif mid and label:
            ids.add(mid)
            out["recommended"].append({"id": mid, "also": c.words(m, "also", w), "label": label, "take": c.text(m, "take", w) or ""})
    for i, f in c.items(doc, "first_run", ""):
        w = "first_run[%d]" % i
        c.keys(f, w, ["size", "models", "engine", "blurb", "start", "recommended"])
        size = c.text(f, "size", w, need=True)
        if size and size not in SIZES:
            c.bad(w, "`size` must be one of %s." % ", ".join(SIZES))
            continue
        models, engine = c.words(f, "models", w), c.text(f, "engine", w)
        if size and not models and engine != "strata":
            c.bad(w, "needs `models` (the ones to try, best first), or `engine: strata` for Strata's own suggestion.")
            continue
        if size:
            out["first_run"].append({"size": size, "models": models, "engine": engine or "", "blurb": c.text(f, "blurb", w) or "",
                                     "start": c.text(f, "start", w) or "", "recommended": c.flag(f, "recommended", w)})
    seen = set()
    for i, e in c.items(doc, "strata", ""):
        w = "strata[%d]" % i
        c.keys(e, w, ["family", "model", "title", "need_gb", "ctx", "min_vram_gb", "disk_gb", "suggest"])
        ent = {"family": c.text(e, "family", w, need=True), "model": c.text(e, "model", w, need=True), "title": c.text(e, "title", w, need=True),
               "need_gb": c.number(e, "need_gb", w, need=True), "ctx": c.number(e, "ctx", w, need=True)}
        if not all(ent.values()):
            continue
        if (ent["family"], ent["model"]) in seen:
            c.bad(w, "%s %s is listed twice." % (ent["family"], ent["model"]))
            continue
        seen.add((ent["family"], ent["model"]))
        ent["ctx"] = int(ent["ctx"])
        for k in ("min_vram_gb", "disk_gb"):
            v = c.number(e, k, w)
            if v is not None:
                ent[k] = v
        ent["suggest"] = c.flag(e, "suggest", w, default=True)
        out["strata"].append(ent)
    return out


# ---------------------------------------------------------------- tools.yaml
def clean_tools(doc, c):
    """The usable part of a parsed tools.yaml: {places, tools}."""
    out = {"places": {}, "tools": []}
    if not isinstance(doc, dict):
        c.bad("", "the file must be groups of settings (key: value), not %s." % type(doc).__name__)
        return out
    c.keys(doc, "", ["version", "places", "tools"])
    if doc.get("version", 1) != 1:
        c.bad("version", "Shoggoth reads version 1.")
    places = doc.get("places") or {}
    if not isinstance(places, dict):
        c.bad("places", "must be groups of settings, one per place.")
        places = {}
    for k, v in places.items():
        if k not in PLACES:
            c.bad("places", "`%s` isn't a place Shoggoth shows tools (%s)." % (k, ", ".join(PLACES)))
            continue
        v = v if isinstance(v, dict) else {}
        c.keys(v, "places.%s" % k, ["title", "blurb"])
        out["places"][k] = {"title": c.text(v, "title", "places." + k) or "", "blurb": c.text(v, "blurb", "places." + k) or ""}
    ids = set()
    for i, t in c.items(doc, "tools", ""):
        w = "tools[%d]" % i
        c.keys(t, w, ["id", "name", "kind", "badge", "take", "links", "show", "detect", "connect", "provider"])
        tid, name, kind = c.text(t, "id", w, need=True), c.text(t, "name", w, need=True), c.text(t, "kind", w, need=True)
        if not (tid and name and kind):
            continue
        w = "tools[%d] (%s)" % (i, tid)
        if not ID_RE.match(tid):
            c.bad(w, "`id` may only have lowercase letters, digits, - and _.")
            continue
        if tid in ids:
            c.bad(w, "another tool already has this id.")
            continue
        if kind not in KINDS:
            c.bad(w, "`kind` must be one of %s." % ", ".join(KINDS))
            continue
        ids.add(tid)
        tool = {"id": tid, "name": name, "kind": kind, "badge": c.text(t, "badge", w) or "", "take": c.text(t, "take", w) or "",
                "links": [], "show": [], "detect": [], "provider": c.text(t, "provider", w) or ""}
        for j, ln in c.items(t, "links", w):
            lw = "%s.links[%d]" % (w, j)
            c.keys(ln, lw, ["label", "url"])
            label, url = c.text(ln, "label", lw, need=True), c.text(ln, "url", lw, need=True)
            if url and not re.match(r"^(https?://|#[a-z])", url):
                c.bad(lw, "`url` must start with https:// (or # for a page in Shoggoth).")
            elif label and url:
                tool["links"].append({"label": label, "url": url})
        for s in c.words(t, "show", w):
            if s in PLACES:
                tool["show"].append(s)
            else:
                c.bad(w, "`show` has `%s`, which isn't a place (%s)." % (s, ", ".join(PLACES)))
        for pat in c.words(t, "detect", w):
            try:
                re.compile(pat)
                tool["detect"].append(pat)
            except re.error as e:
                c.bad(w, "`detect` pattern `%s` isn't a valid regular expression (%s)." % (pat, e))
        conn = t.get("connect")
        if conn is not None:
            if not isinstance(conn, dict):
                c.bad(w, "`connect` must be a group of settings (help, and fields or json).")
            else:
                cw = w + ".connect"
                c.keys(conn, cw, ["help", "fields", "json"])
                fields = c.words(conn, "fields", cw)
                for f in fields:
                    if f not in FIELDS:
                        c.bad(cw, "`fields` has `%s`; it may have %s." % (f, ", ".join(FIELDS)))
                fields = [f for f in fields if f in FIELDS]
                js = conn.get("json")
                if js is not None and not isinstance(js, dict):
                    c.bad(cw, "`json` must be the config file's settings (key: value).")
                    js = None
                if fields and js:
                    c.bad(cw, "has both `fields` and `json`; Shoggoth shows the json.")
                if fields or js:
                    tool["connect"] = {"help": c.text(conn, "help", cw) or "", "fields": [] if js else fields, "json": js}
                else:
                    c.bad(cw, "needs `fields` or `json`.")
        if "server" in tool["show"] and "connect" not in tool:
            c.bad(w, "shows on the Server page but has no `connect`, so there would be nothing to show.")
            tool["show"].remove("server")
        out["tools"].append(tool)
    return out


# ---------------------------------------------------------------- reading
# ---------------------------------------------------------------- flavour.yaml
def clean_flavour(doc, c):
    """The usable part of a parsed flavour.yaml: {lines: {set: [line, ...]}}. Only {model} and {target} may be filled in."""
    out = {"lines": {}}
    if not isinstance(doc, dict):
        c.bad("", "the file must be groups of settings (key: value), not %s." % type(doc).__name__)
        return out
    c.keys(doc, "", ["version", "lines"])
    if doc.get("version", 1) != 1:
        c.bad("version", "Shoggoth reads version 1.")
    lines = doc.get("lines") or {}
    if not isinstance(lines, dict):
        c.bad("lines", "must be groups, one per set (thinking:, writing: ...).")
        return out
    c.keys(lines, "lines", list(FLAVOUR_SETS))
    for k in FLAVOUR_SETS:
        v = lines.get(k)
        if v is None:
            continue
        v = v if isinstance(v, list) else [v]
        keep = []
        for i, x in enumerate(v):
            w = "lines.%s[%d]" % (k, i)
            if not isinstance(x, str) or not x.strip():
                c.bad(w, "must be a line of text.")
                continue
            odd = [p for p in re.findall(r"\{([^}]*)\}", x) if p not in ("model", "target")]
            if odd:
                c.bad(w, "{%s} isn't something Shoggoth fills in. Use {model} or {target}." % odd[0])
                continue
            keep.append(x.strip())
        if keep:
            out["lines"][k] = keep
    return out


CLEAN = {"models": clean_models, "tools": clean_tools, "flavour": clean_flavour}


def _read(fname):
    """(parsed YAML, problem) for one file."""
    try:
        with open(fname, encoding="utf8") as f:
            text = f.read(MAX_YAML + 1)
        if len(text) > MAX_YAML:
            return None, "the file is bigger than %d KB." % (MAX_YAML // 1000)
        return yaml.safe_load(text), None
    except OSError as e:
        return None, "couldn't read it (%s)." % e.strerror
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = " (line %d, column %d)" % (mark.line + 1, mark.column + 1) if mark else ""
        what = str(getattr(e, "problem", None) or e)
        hint = " Text with a colon and a space in it needs quotes around it." if "mapping values" in what else ""
        return None, "isn't valid YAML%s: %s.%s" % (where, what, hint)


def load(name, fname=None):
    """{data, problems, file} for models or tools, from `fname` (default: yours, else the bundled one)."""
    fname = fname or path(name)
    doc, err = _read(fname)
    c = _Check(os.path.basename(fname))
    if err:
        c.bad("", err)
        bundled = os.path.join(BUNDLED, name + ".yaml")
        if fname != bundled:                                   # yours is unreadable: the bundled one stands in, and that is said
            c.bad("", "Shoggoth is using its own %s.yaml until this is fixed." % name)
            doc, err = _read(bundled)
            fname_used = bundled
        else:
            fname_used = fname
        if err:                                                # nothing usable at all: empty lists, and the page says why
            return {"data": CLEAN[name]({}, _Check("")), "problems": c.problems, "file": fname_used}
        return {"data": CLEAN[name](doc, _Check("")), "problems": c.problems, "file": fname_used}
    return {"data": CLEAN[name](doc, c), "problems": c.problems, "file": fname}


def get(name):
    """load(name), kept until the file changes."""
    fname = path(name)
    try:
        sig = (fname, os.stat(fname).st_mtime_ns)
    except OSError:
        sig = (fname, None)
    with _lock:
        hit = _cache.get(name)
        if hit and hit[0] == sig:
            return hit[1]
        v = load(name, fname)
        _cache[name] = (sig, v)
        return v


def models():
    return get("models")["data"]


def tools():
    return get("tools")["data"]


def summary():
    """Everything the page needs: {models, tools, flavour, problems, files}."""
    m, t, f = get("models"), get("tools"), get("flavour")
    return {"models": m["data"], "tools": t["data"], "flavour": f["data"], "problems": m["problems"] + t["problems"] + f["problems"],
            "files": {"models": m["file"], "tools": t["file"], "flavour": f["file"]}}


def strata_catalog():
    """Strata's big models, best quality first: [{family, model, title, need_gb, ctx, min_vram_gb?, disk_gb?, suggest}]."""
    return models()["strata"]


def app_patterns():
    """[(compiled pattern, app name)] from every tool's `detect`, in file order: the Server page names apps with these."""
    out = []
    for t in tools()["tools"]:
        for p in t["detect"]:
            out.append((re.compile(p, re.I), t["name"]))
    return out


# ---------------------------------------------------------------- the command line
def main(argv):
    """`recommendations check [file...]` and `recommendations where`. Returns the exit code."""
    usage = "usage: recommendations check [models.yaml|tools.yaml|flavour.yaml ...] | recommendations where"
    if not argv or argv[0] not in ("check", "where"):
        print(usage)
        return 2
    if argv[0] == "where":
        for n in FILES:
            print("%s: %s%s" % (n, path(n), "  (yours)" if path(n).startswith(user_dir()) else ""))
        print("Put a file in %s to use it instead of the bundled one." % user_dir())
        return 0
    targets = argv[1:] or [path(n) for n in FILES]
    bad = 0
    for f in targets:
        name = next((n for n in FILES if os.path.basename(f).startswith(n)), None)
        if not name:
            print("%s: Shoggoth reads models.yaml, tools.yaml and flavour.yaml; it can't tell which this is." % f)
            bad += 1
            continue
        r = load(name, os.path.abspath(f))
        d = r["data"]
        counts = ("%d recommended, %d first-run sizes, %d Strata models" % (len(d["recommended"]), len(d["first_run"]), len(d["strata"])) if name == "models"
                  else "%d tools, %d places" % (len(d["tools"]), len(d["places"])) if name == "tools"
                  else "%d lines in %d sets" % (sum(map(len, d["lines"].values())), len(d["lines"])))
        print("%s %s: %s" % ("✗" if r["problems"] else "✓", f, counts))
        for p in r["problems"]:
            print("  ✗ " + p)
        bad += len(r["problems"])
    return 1 if bad else 0
