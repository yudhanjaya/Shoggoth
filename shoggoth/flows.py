"""Routing flows (Expert Mode): a small YAML description of which model should answer which kind of request.

A flow is data: `targets` (engine, model, what it can do) and a tree of `match` nodes whose conditions come from a closed list, no code.
It is only ever used when a request names the model `auto`, and only while Expert Mode is on and a flow is active; any other request goes
where it always went. Wrong routing is silent, so every decision returns its path and `explain()` shows it without calling a model.
The request fingerprints for compaction and title are guesses until Pi's real requests have been recorded."""
import fnmatch
import json
import re
import sys
import os
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "_vendor"))
import yaml  # noqa: E402

import expert  # noqa: E402
import store  # noqa: E402

CONDITIONS = {"kind", "has_tools", "tool_results_in_turn", "has_code", "has_images", "question_count", "prompt_tokens", "max_tokens",
              "requested_model", "client", "loaded", "all", "any", "not"}
NUMBERS = {"gte", "lte", "gt", "lt", "eq"}
KINDS = {"compaction", "title", "chat", "embedding"}
RECENT = []                                  # the last decisions, newest last: [{t, path, target}]
_lock = threading.Lock()


# ---------------------------------------------------------------- reading a flow
def _words(o):
    """YAML reads the keys `yes` and `no` as true and false; in a flow they are the two edges of a node."""
    if isinstance(o, dict):
        return {("yes" if k is True else "no" if k is False else k): _words(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_words(v) for v in o]
    return o


def parse(text):
    """The flow described by YAML text, or raises ValueError with a plain reason."""
    try:
        d = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError("The flow isn't valid YAML: %s" % getattr(e, "problem", e))
    if not isinstance(d, dict):
        raise ValueError("A flow is a list of settings (version, targets, flow, policy).")
    return _words(d)


def _conds(c, where, problems):
    if not isinstance(c, dict) or not c:
        problems.append("%s: a condition is a group like {kind: [title]}." % where)
        return
    for k, v in c.items():
        if k not in CONDITIONS:
            problems.append("%s: %r isn't a condition Shoggoth knows (%s)." % (where, k, ", ".join(sorted(CONDITIONS))))
        elif k in ("all", "any"):
            if not isinstance(v, list) or not v:
                problems.append("%s.%s: needs a list of conditions." % (where, k))
            else:
                for i, sub in enumerate(v):
                    _conds(sub, "%s.%s[%d]" % (where, k, i), problems)
        elif k == "not":
            _conds(v, where + ".not", problems)
        elif k == "kind" and not (set(v if isinstance(v, list) else [v]) <= KINDS):
            problems.append("%s.kind: choose from %s." % (where, ", ".join(sorted(KINDS))))
        elif isinstance(v, dict) and not set(v) <= NUMBERS:
            problems.append("%s.%s: use gte, lte, gt, lt or eq." % (where, k))


def validate(d):
    """(problems, warnings) for a parsed flow. A flow with problems cannot be activated; warnings are the person's call."""
    problems, warnings = [], []
    if d.get("version") != 1:
        problems.append("version: must be 1.")
    targets = d.get("targets")
    if not isinstance(targets, dict) or not targets:
        problems.append("targets: name at least one (engine and model).")
        targets = {}
    for name, t in targets.items():
        if not isinstance(t, dict) or not t.get("engine") or not t.get("model"):
            problems.append("targets.%s: needs an engine and a model." % name)
        else:
            bad = set(t.get("can") or []) - {"tools", "vision"}
            if bad:
                problems.append("targets.%s.can: only tools and vision, not %s." % (name, ", ".join(sorted(map(str, bad)))))
    flow = d.get("flow") or {}
    nodes = flow.get("nodes") or {}
    if flow.get("default") not in targets:
        problems.append("flow.default: must name a target (it catches whatever the rules don't).")
    if nodes and flow.get("start") not in nodes:
        problems.append("flow.start: must name one of the nodes.")
    for nid, n in nodes.items():
        if not isinstance(n, dict) or n.get("type") != "match":
            problems.append("flow.nodes.%s: type must be match." % nid)
            continue
        _conds(n.get("when"), "flow.nodes.%s.when" % nid, problems)
        for edge in ("yes", "no"):
            if n.get(edge) not in nodes and n.get(edge) not in targets:
                problems.append("flow.nodes.%s.%s: must name a node or a target." % (nid, edge))
    if not problems and nodes:                                     # a tree: walk it, refuse a loop, and note what nothing reaches
        seen = set()

        def walk(nid, above):
            if nid not in nodes:
                return
            if nid in above:
                problems.append("flow.nodes.%s: the flow loops back to it." % nid)
                return
            seen.add(nid)
            for edge in ("yes", "no"):
                walk(nodes[nid].get(edge), above | {nid})
        walk(flow["start"], frozenset())
        for nid in sorted(set(nodes) - seen):
            warnings.append("flow.nodes.%s: nothing leads to it." % nid)
    pol = d.get("policy") or {}
    if pol.get("swap", "ask") not in ("ask", "never"):
        problems.append("policy.swap: ask or never.")
    if pol.get("ram_guard", "strict") not in ("strict", "warn", "off"):
        problems.append("policy.ram_guard: strict, warn or off.")
    return problems, warnings


# ---------------------------------------------------------------- what a request looks like
_CODE = re.compile(r"```|^\s*[-+]{3} |^@@ |(?:^|\s)(?:[\w.-]+/)+[\w.-]+\.\w{1,5}\b|\$ \w+|\bdef \w+\(|\bfunction \w+\(|\bclass \w+", re.M)


def _text(content):
    if isinstance(content, list):
        return " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return str(content or "")


def features(body, path="/v1/chat/completions", client="gateway"):
    """The facts a condition can ask about, read from an OpenAI-style request body (a dict or JSON text/bytes)."""
    if isinstance(body, (bytes, str)):
        try:
            body = json.loads(body)
        except ValueError:
            body = {}
    body = body if isinstance(body, dict) else {}
    msgs = [m for m in body.get("messages") or [] if isinstance(m, dict)]
    last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=-1)
    turn = msgs[last_user + 1:] if last_user >= 0 else msgs
    texts = [_text(m.get("content")) for m in msgs]
    user_text = _text(msgs[last_user].get("content")) if last_user >= 0 else ""
    system = " ".join(_text(m.get("content")) for m in msgs if m.get("role") == "system").lower()
    max_tokens = int(body.get("max_tokens") or body.get("max_completion_tokens") or 0)
    if "embeddings" in path or ("input" in body and not msgs):
        kind = "embedding"
    elif re.search(r"summari[sz]e (the )?(conversation|session|chat)|compact", system + " " + user_text[:300].lower()):
        kind = "compaction"
    elif re.search(r"\b(generate|write|create)\b.{0,30}\btitle\b|\btitle\b.{0,30}\b(for|of) (this|the) (conversation|chat)", system + " " + user_text[:300].lower()) \
            and (max_tokens and max_tokens <= 64 or len(msgs) <= 3):
        kind = "title"
    else:
        kind = "chat"
    parts = [p for m in msgs if isinstance(m.get("content"), list) for p in m["content"] if isinstance(p, dict)]
    return {"kind": kind, "has_tools": bool(body.get("tools")), "tool_results_in_turn": sum(1 for m in turn if m.get("role") == "tool"),
            "has_code": bool(_CODE.search("\n".join(texts))), "has_images": any(p.get("type") in ("image_url", "input_image") for p in parts),
            "question_count": user_text.count("?"), "prompt_tokens": sum(len(t) for t in texts) // 4, "max_tokens": max_tokens,
            "requested_model": str(body.get("model") or ""), "client": client}


def _num(v, want):
    if isinstance(want, dict):
        return all(v >= want[k] if k == "gte" else v <= want[k] if k == "lte" else v > want[k] if k == "gt" else v < want[k] if k == "lt" else v == want[k]
                   for k in want)
    return v == want


def holds(cond, f, loaded=()):
    """Does condition `cond` hold for features `f`? `loaded` is the set of target names resident now."""
    for k, want in cond.items():
        if k == "all":
            ok = all(holds(c, f, loaded) for c in want)
        elif k == "any":
            ok = any(holds(c, f, loaded) for c in want)
        elif k == "not":
            ok = not holds(want, f, loaded)
        elif k == "kind":
            ok = f["kind"] in (want if isinstance(want, list) else [want])
        elif k == "requested_model":
            ok = any(fnmatch.fnmatch(f["requested_model"], g) for g in (want if isinstance(want, list) else [want]))
        elif k == "client":
            ok = f["client"] in (want if isinstance(want, list) else [want])
        elif k == "loaded":
            ok = bool(set(want if isinstance(want, list) else [want]) & set(loaded))
        elif isinstance(want, bool):
            ok = bool(f.get(k)) == want
        else:
            ok = _num(f.get(k, 0), want)
        if not ok:
            return False
    return True


def decide(d, f, loaded=()):
    """(target name, path) for a request with features `f`. Targets that cannot handle it (images, tools) are never chosen: the walk
    goes on to the default, then to any target that can."""
    targets, flow = d["targets"], d["flow"]
    nodes, path, here = flow.get("nodes") or {}, [], flow.get("start")

    def can(name):
        c = set(targets[name].get("can") or [])
        return not ((f["has_images"] and "vision" not in c) or (f["has_tools"] and "tools" not in c and c))
    while here in nodes:
        n = nodes[here]
        yes = holds(n["when"], f, loaded)
        path.append("%s:%s" % (here, "yes" if yes else "no"))
        here = n["yes"] if yes else n["no"]
    if here not in targets or not can(here):
        if here in targets:
            path.append("%s can't handle it" % here)
        here = flow["default"] if can(flow["default"]) else next((t for t in targets if can(t)), flow["default"])
        path.append("default")
    path.append(here)
    return here, " → ".join(path)


def explain(text, body, loaded=()):
    """One request through one flow, with no model call: {target, path, features}."""
    d = parse(text)
    problems, _ = validate(d)
    if problems:
        raise ValueError(problems[0])
    f = features(body)
    target, path = decide(d, f, loaded)
    return {"target": target, "path": path, "features": f}


def dry_run(text, bodies):
    """A set of requests through the flow with no model calls: how they would be spread over targets, and what fell to the default."""
    d = parse(text)
    problems, _ = validate(d)
    if problems:
        raise ValueError(problems[0])
    per, fell = {}, 0
    for b in bodies:
        t, path = decide(d, features(b))
        per[t] = per.get(t, 0) + 1
        fell += path.endswith("default → " + t)
    return {"requests": len(bodies), "per_target": per, "fell_to_default": fell}


# ---------------------------------------------------------------- saved flows
def saved(settings=None):
    return dict(((settings or store.load()).get("flows") or {}).get("saved") or {})


def active_name(settings=None):
    s = settings or store.load()
    return ((s.get("flows") or {}).get("active")) if expert.on(s) else None


def save(name, text):
    """Keep a flow under a name. It is saved with its warnings but never with problems."""
    name = str(name or "").strip()[:60]
    if not name:
        raise ValueError("Give the flow a name.")
    problems, warnings = validate(parse(text))
    if problems:
        raise ValueError(problems[0])

    def put(s):
        s.setdefault("flows", {}).setdefault("saved", {})[name] = text
    store.update(put)
    return warnings


def activate(name):
    if name is not None and name not in saved():
        raise ValueError("No flow with that name.")

    def put(s):
        s.setdefault("flows", {})["active"] = name
    store.update(put)


def delete(name):
    def put(s):
        f = s.setdefault("flows", {})
        f.get("saved", {}).pop(name, None)
        if f.get("active") == name:
            f["active"] = None
    store.update(put)


def active():
    """The parsed active flow, or None (also when Expert Mode is off, or the saved text no longer reads)."""
    s = store.load()
    name = active_name(s)
    text = saved(s).get(name) if name else None
    if not text:
        return None
    try:
        d = parse(text)
        return d if not validate(d)[0] else None
    except ValueError:
        return None


def note(target, path):
    with _lock:
        RECENT.append({"t": time.time(), "target": target, "path": path})
        del RECENT[:-50]
