"""The tool for people who write an engine folder: `zest providers check <folder>` says what is wrong, in the same words Zest
itself would use, and what Zest would do with it. `new <id>` makes a starting folder, `list` shows what Zest has loaded.
Nothing here starts a program. The only thing it touches is a health check on the engine's own ports, on this computer."""
import difflib
import json
import os
import re
import shutil
import sys

import provbase
import registry
import store

TEMPLATE = os.path.join(registry.HERE, "providers", "_template")
SHELL_WORDS = {"|", "||", "&&", ";", ">", ">>", "<", "2>&1", "&"}


def _lint(spec, folder):
    """[(level, text)] for a spec that already passed `registry.validate`: things that load fine but are probably not what was meant."""
    out = []
    for sec, known in registry.SECTIONS.items():
        for k in (spec.get(sec) or {}):
            if k not in known:
                near = difflib.get_close_matches(str(k), sorted(known), n=1)
                out.append(("warn", "%s.%s: Zest doesn't read this setting%s." % (sec, k, " (did you mean %s.%s?)" % (sec, near[0]) if near else "")))
    for k in spec:
        if k not in registry.KNOWN:
            near = difflib.get_close_matches(str(k), sorted(registry.KNOWN), n=1)
            out.append(("warn", "%s: Zest doesn't read this setting%s." % (k, " (did you mean %s?)" % near[0] if near else "")))
    run = spec.get("run") or {}
    mode = run.get("mode", "managed")
    has_code = os.path.isfile(os.path.join(folder, "provider.py"))
    if os.path.basename(os.path.normpath(folder)) != spec.get("id"):
        out.append(("info", "The folder is called %r but the id is %r. That works; matching them makes the folder easier to find." % (os.path.basename(os.path.normpath(folder)), spec.get("id"))))
    tier = str(spec.get("tier", ""))
    if not tier:
        out.append(("warn", "tier: not set. Say `small-medium` (the usual) or `large` (needs lots of RAM) so Zest knows which models to send here."))
    elif tier not in ("small-medium", "large", "small", "medium"):
        out.append(("warn", "tier: %r isn't one Zest knows. Use `small-medium` or `large`." % tier))
    if mode == "managed":
        start = run.get("start") or []
        if start and str(start[0]) != "{bin}":
            out.append(("warn", "run.start: the first word is usually {bin}, the program found through detect.binaries."))
        if start and "{port}" not in " ".join(map(str, start)) and not has_code:
            out.append(("warn", "run.start: never mentions {port}, so the engine may listen somewhere Zest isn't looking."))
        if not (spec.get("detect") or {}).get("binaries"):
            out.append(("warn", "detect.binaries: not set, so Zest can't tell whether the program is installed."))
        if not run.get("port_range"):
            out.append(("warn", "run.port_range: not set, so Zest has no port to start it on."))
        if not (spec.get("memory") or {}).get("group"):
            out.append(("warn", "memory.group: not set. Without it Zest won't free memory for this engine, or from it, when you switch models."))
        if (spec.get("memory") or {}).get("idle_stop_minutes") and not (spec.get("capabilities") or {}).get("activity") and not has_code:
            out.append(("info", "memory.idle_stop_minutes is set, but Zest can't see whether the engine is working, so it will never stop it by the clock. Add capabilities.activity to allow that."))
    m = spec.get("models") or {}
    if not m.get("formats"):
        out.append(("warn", "models.formats: not set. Say what it can run, like [gguf]; otherwise it is offered every model."))
    elif not m.get("data_dir") and not has_code:
        out.append(("info", "models.data_dir: not set, so Zest keeps nothing for this engine in the Models folder (fine if it finds its own models)."))
    for path, c in registry._commands(spec):  # noqa: SLF001
        if isinstance(c, list) and any(str(w) in SHELL_WORDS for w in c):
            out.append(("warn", "%s: has a word that is shell syntax. Commands run without a shell, so pipes, && and redirects don't work." % path))
    return out


def check(folder):
    """Everything Zest would say about an engine folder: {folder, id, name, ok, items:[{level, text}], consent}. levels: error, warn, info, ok."""
    folder = os.path.abspath(os.path.expanduser(folder))
    items = []
    rep = {"folder": folder, "id": None, "name": None, "ok": False, "items": items, "consent": None}
    yml = os.path.join(folder, "provider.yaml")
    if not os.path.isdir(folder):
        items.append({"level": "error", "text": "%s isn't a folder." % folder})
        return rep
    if not os.path.isfile(yml):
        items.append({"level": "error", "text": "There is no provider.yaml in %s. That file is the whole engine." % folder})
        return rep
    probe = registry.Registry(bundled="", user="")
    probe._load_one(folder, trusted=False)  # noqa: SLF001
    if probe.errors:
        for e in probe.errors:
            for line in re.split(r"(?<=\.) (?=[a-z_]+[.:\[])", e["error"]):
                items.append({"level": "error", "text": line.strip()})
        return rep
    p = next(iter(probe.providers.values()))
    rep.update(id=p.id, name=p.name)
    items.append({"level": "ok", "text": "provider.yaml reads, and every setting has the right shape (id %s, \"%s\")." % (p.id, p.name)})
    if os.path.isfile(os.path.join(folder, "provider.py")):
        items.append({"level": "ok", "text": "provider.py loads and defines Provider."})
    for lv, text in _lint(p.spec, folder):
        items.append({"level": lv, "text": text})
    try:
        taken = registry.Registry().load()
        other = taken.get(p.id)
        if other and os.path.realpath(other.folder) != os.path.realpath(folder):
            items.append({"level": "error", "text": "The id %r is already used by the engine in %s. Change id: in this file." % (p.id, other.folder)})
    except Exception:  # noqa: BLE001
        pass
    # what is on this computer: never starts anything
    try:
        det = p.detect()
        if det.get("bin"):
            items.append({"level": "info", "text": "Found its program at %s." % det["bin"]})
        elif (p.spec.get("run") or {}).get("mode", "managed") == "managed":
            items.append({"level": "info", "text": "Its program isn't on this computer (detect.binaries found nothing). Zest would show the engine as not installed."})
        for r in det.get("running") or []:
            items.append({"level": "info", "text": "Something answering its health check is running on port %s." % r["port"]})
            try:
                ms = p.models()
                if ms:
                    items.append({"level": "info", "text": "It lists these models: %s." % ", ".join(m["id"] for m in ms[:6])})
            except Exception as e:  # noqa: BLE001
                items.append({"level": "warn", "text": "It answered, but its model list couldn't be read: %s" % e})
    except Exception as e:  # noqa: BLE001
        items.append({"level": "warn", "text": "Looking for it on this computer failed: %s" % e})
    c = probe.consent(p)
    rep["consent"] = c
    if c:
        items.append({"level": "info", "text": "Before it runs, Zest will ask the person to allow it to: %s." % "; ".join(c["will"])})
        for cmd in c["commands"]:
            items.append({"level": "info", "text": "  %s: %s" % (cmd["where"], " ".join(map(str, cmd["command"])))})
    rep["ok"] = not any(i["level"] == "error" for i in items)
    return rep


MARK = {"ok": "✓", "info": "·", "warn": "!", "error": "✗"}


def render(rep):
    lines = ["Checking %s" % rep["folder"]]
    lines += ["  %s %s" % (MARK[i["level"]], i["text"]) for i in rep["items"]]
    errs = sum(i["level"] == "error" for i in rep["items"])
    warns = sum(i["level"] == "warn" for i in rep["items"])
    if errs:
        lines.append("Zest would not load this engine: %d problem%s to fix%s." % (errs, "" if errs == 1 else "s", ", and %d thing%s to look at" % (warns, "" if warns == 1 else "s") if warns else ""))
    elif warns:
        lines.append("Zest will load this engine. %d thing%s worth a look." % (warns, "" if warns == 1 else "s"))
    else:
        lines.append("Zest will load this engine.")
    return "\n".join(lines)


def new(pid, where=None, example=None):
    """Make <your engines folder>/<pid>/provider.yaml from the template (or from a bundled example). Returns the new folder."""
    if not registry.ID_RE.match(pid):
        raise ValueError("The id must be 2 to 32 lowercase letters, digits, - or _, starting with a letter.")
    root = where or os.path.join(store.data_dir(), "providers")
    dest = os.path.join(root, pid)
    if os.path.exists(dest):
        raise ValueError("%s already exists." % dest)
    os.makedirs(dest)
    src = os.path.join(registry.HERE, "providers", "_examples", example) if example else TEMPLATE
    if example and not os.path.isfile(os.path.join(src, "provider.yaml")):
        os.rmdir(dest)
        raise ValueError("There is no example called %r. Examples: %s." % (example, ", ".join(sorted(os.listdir(os.path.join(registry.HERE, "providers", "_examples"))))))
    with open(os.path.join(src, "provider.yaml")) as f:
        text = f.read()
    if not example:
        text = re.sub(r"(?m)^id: my-engine\b", "id: " + pid, text, count=1)
        text = re.sub(r"(?m)^name: My engine\b", "name: " + pid.replace("-", " ").replace("_", " ").capitalize(), text, count=1)
    else:
        text = re.sub(r"(?m)^id: .*$", "id: " + pid, text, count=1)
    with open(os.path.join(dest, "provider.yaml"), "w") as f:
        f.write(text)
    return dest


def main(argv):
    """`providers check <folder> [--json]`, `providers new <id>`, `providers list`. Returns the exit code."""
    usage = "usage: providers check <folder> [--json] | providers new <id> [--from <example>] | providers list"
    if not argv:
        print(usage)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "check" and rest:
        rep = check([a for a in rest if not a.startswith("--")][0])
        print(json.dumps(rep, indent=2) if "--json" in rest else render(rep))
        return 0 if rep["ok"] else 1
    if cmd == "new" and rest and not rest[0].startswith("--"):
        try:
            d = new(rest[0], example=rest[rest.index("--from") + 1] if "--from" in rest and rest.index("--from") + 1 < len(rest) else None)
        except ValueError as e:
            print(e)
            return 1
        print("Made %s\nEdit provider.yaml, run `providers check %s`, then open Advanced > Engines in Zest and press Reload." % (os.path.join(d, "provider.yaml"), d))
        return 0
    if cmd == "list":
        r = registry.Registry().load()
        for p in r.all():
            print("%-14s %-24s %s" % (p.id, p.name, "built in" if p.trusted else ("added by you" + ("" if p.approved else " (waiting for your OK)"))))
        for e in r.errors:
            print("not loaded: %s\n    %s" % (e["folder"], e["error"]))
        return 0
    print(usage)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
