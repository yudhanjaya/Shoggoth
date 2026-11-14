"""One-time move from Shoggoth 4.x (Lemonade) to 5.x (llama.cpp).

Runs on the first start of 5.0 and can be run again safely. It never moves, changes or deletes a model file: it only makes
links to them, and writes three small files in the data folder: `model-aliases.json` (old model id -> new id), the model options
in llama.cpp's `presets.ini`, and its own marker `migrate5.json`, so each step is done once.

What it does, one log line each:
  1. link the GGUF files Lemonade downloaded into the view folder the llama.cpp router reads (hard links on the same disk,
     symbolic links otherwise);
  2. work out the new id of each model and save the alias map;
  3. carry Lemonade's saved per-model options (context size, extra llama-server options) into presets.ini;
  4. stop a lemond that Shoggoth started (one the person runs is left alone);
  5. count the space Lemonade's own files take, so the page can offer to delete them (this module never does).

Standard library only.
"""
import json
import os
import re
import signal
import time

import hf
import llamaargs
import llamacpp

MARKER = "migrate5.json"
ALIASES = "model-aliases.json"
STEPS = ("link", "aliases", "presets", "lemond")
_HUB = re.compile(r"^models--([^/]+?)--(.+)$")

# short llama-server flags -> the long names a presets file uses
SHORT = {"-ngl": "n-gpu-layers", "-c": "ctx-size", "-fa": "flash-attn", "-t": "threads", "-tb": "threads-batch", "-b": "batch-size",
         "-ub": "ubatch-size", "-ctk": "cache-type-k", "-ctv": "cache-type-v", "-sm": "split-mode", "-ts": "tensor-split",
         "-mg": "main-gpu", "-np": "parallel", "-ncmoe": "n-cpu-moe", "-cmoe": "cpu-moe", "-ot": "override-tensor",
         "-dev": "device", "-kvu": "kv-unified", "-nkvo": "no-kv-offload", "-rtr": "rope-scale", "-fit": "fit"}
# Options Lemonade accepted that Shoggoth sets itself or llama.cpp's router would reject in a preset.
NOT_IN_PRESET = {"-m", "--model", "--port", "--host", "--api-key", "--mmproj", "-mm", "--embeddings", "--embedding", "--reranking",
                 "--rerank", "--jinja", "--alias", "-a", "--models-dir", "--models-preset"}


# ---------------------------------------------------------------- small helpers
def _read_json(path):
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(path + ".tmp", path)


def _find_files(root, name, depth=4):
    """Files called `name` under root, to a limited depth, without following links."""
    out, base = [], root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        if dirpath.count(os.sep) - base >= depth:
            dirnames[:] = []
        if name in filenames:
            out.append(os.path.join(dirpath, name))
    return sorted(out)


def _lemonade_models_dirs(data_dir, library_dir):
    """Folders that may hold Lemonade's Hugging Face style model folders (models--org--repo), and whether each is the shared cache."""
    cfg = _read_json(os.path.join(data_dir, "lemonade-data", "config.json"))
    out = []
    md = cfg.get("models_dir")
    if md and md != "auto":
        out.append((os.path.expanduser(str(md)), False))
    elif os.path.isdir(os.path.join(data_dir, "lemonade-data")):
        # The shared Hugging Face cache counts only for a profile where Lemonade really ran: on a fresh 5.0 profile it holds
        # whatever other apps downloaded (speech models, other tools' files), none of it Lemonade's.
        import modelscan
        out.append((modelscan.hf_cache_dir(), True))
    if library_dir:
        out.append((os.path.join(library_dir, "lemonade"), False))
    out.append((os.path.join(data_dir, "lemonade-data"), False))
    seen, res = set(), []
    for p, shared in out:
        rp = os.path.realpath(p)
        if rp not in seen and os.path.isdir(rp):
            seen.add(rp)
            res.append((rp, shared))
    return res


def _hub_repos(root, depth=3):
    """[(repo 'org/name', snapshot folder)] for each models--org--name folder under root; the newest snapshot of each."""
    out, base = [], root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, _ in os.walk(root, followlinks=False):
        here = dirpath.count(os.sep) - base
        hubs = [d for d in dirnames if _HUB.match(d)]
        for d in hubs:
            m = _HUB.match(d)
            snaps = os.path.join(dirpath, d, "snapshots")
            if not os.path.isdir(snaps):
                continue
            revs = sorted((os.path.join(snaps, r) for r in os.listdir(snaps)), key=lambda p: os.path.getmtime(p), reverse=True)
            if revs:
                out.append(("%s/%s" % (m.group(1), m.group(2)), revs))
        dirnames[:] = [] if here >= depth else [d for d in dirnames if not _HUB.match(d) and not d.startswith(".")]
    return out


def _gguf_in(folder):
    out = []
    for dirpath, _, filenames in os.walk(folder, followlinks=False):
        for fn in filenames:
            if fn.lower().endswith(".gguf"):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


def discover(data_dir, library_dir):
    """The models Lemonade downloaded: [{repo, quant, id, files: [paths in the cache], mmproj: [paths], shared, root}].
    A repo with several quants gives one entry per quant. `files` are the cache's own paths (links into blobs/)."""
    models = {}
    for root, shared in _lemonade_models_dirs(data_dir, library_dir):
        for repo, revs in _hub_repos(root):
            seen_names = set()
            for rev in revs:                                  # newest first: an older snapshot only adds files the newer lacks
                for path in _gguf_in(rev):
                    name = os.path.basename(path)
                    if name in seen_names or not os.path.exists(path):
                        continue
                    seen_names.add(name)
                    if "mmproj" in name.lower():
                        models.setdefault((repo, "__mmproj__"), {"files": [], "root": root, "shared": shared})["files"].append(path)
                        continue
                    q = hf.quant_of(name) or os.path.splitext(name)[0]
                    m = models.setdefault((repo, q), {"files": [], "root": root, "shared": shared})
                    m["files"].append(path)
    # In the shared cache, keep only repos Lemonade knew about (its model lists name them), so other apps' downloads stay out.
    # When no list names anything (a list file is missing), fall back to every GGUF there, as 4.x Lemonade would have listed them.
    known = {cp.split(":", 1)[0].lower() for cp in _catalog_entries(data_dir).values()}
    out = []
    for (repo, q), m in sorted(models.items()):
        if q == "__mmproj__":
            continue
        if m["shared"] and known and repo.lower() not in known:
            continue
        proj = models.get((repo, "__mmproj__"), {}).get("files", [])
        quant = q if hf.quant_of(os.path.basename(m["files"][0])) else None
        out.append({"repo": repo, "quant": q, "id": hf.model_id(repo, q) if quant else q, "files": m["files"], "mmproj": proj,
                    "shared": m["shared"], "root": m["root"]})
    return out


# ---------------------------------------------------------------- step 1: links
def view_dir(data_dir):
    return os.path.join(data_dir, "views", "llamacpp")


def _link(src, dst):
    """Make dst a link to the file src: a hard link when that works, else a symbolic link. Never touches src.
    Returns 'hard', 'soft', 'present' or 'clash'."""
    real = os.path.realpath(src)
    if os.path.lexists(dst):
        try:
            if os.path.samefile(dst, real):
                return "present"
        except OSError:
            pass
        return "clash"
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        os.link(real, dst)
        return "hard"
    except OSError:
        os.symlink(real, dst)
        return "soft"


def link_models(data_dir, models, log=print):
    """Link each model into the view folder: <id>.gguf, or a folder <id>/ when it has several parts or a vision file (what the
    router expects). Returns {id: [link paths]}."""
    view = view_dir(data_dir)
    made, counts = {}, {"hard": 0, "soft": 0, "present": 0, "clash": 0}
    for m in models:
        files = sorted(m["files"], key=lambda p: os.path.basename(p))
        multi = len(files) > 1 or bool(m["mmproj"])
        paths = []
        if multi:
            d = os.path.join(view, m["id"])
            for p in files + list(m["mmproj"]):
                dst = os.path.join(d, os.path.basename(p))
                counts[_link(p, dst)] += 1
                paths.append(dst)
        else:
            dst = os.path.join(view, m["id"] + ".gguf")
            counts[_link(files[0], dst)] += 1
            paths.append(dst)
        made[m["id"]] = paths
    log("Linked %d Lemonade models into the library (%d hard links, %d symbolic links, %d already there%s)."
        % (len(models), counts["hard"], counts["soft"], counts["present"],
           ", %d left alone because a different file has that name" % counts["clash"] if counts["clash"] else ""))
    return made


# ---------------------------------------------------------------- step 2: aliases
def _catalog_entries(data_dir):
    """{lemonade model name: checkpoint} from user_models.json (ids there are user.<name>) and any server_models.json that is around."""
    out = {}
    roots = [os.path.join(data_dir, "lemonade-data"), os.path.join(data_dir, "lemonade")]
    for r in roots:
        if not os.path.isdir(r):
            continue
        for fname, prefix in (("user_models.json", "user."), ("server_models.json", "")):
            for p in _find_files(r, fname):
                for name, info in _read_json(p).items():
                    cp = info.get("checkpoint") if isinstance(info, dict) else None
                    if not cp and isinstance(info, dict):
                        cp = (info.get("checkpoints") or {}).get("main")
                    if cp:
                        out.setdefault(prefix + name, str(cp))
                        if prefix:
                            out.setdefault(name, str(cp))
    return out


def _gguf_stem(name):
    return re.sub(r"[-_.]?gguf$", "", name, flags=re.I)


def build_aliases(data_dir, models):
    """{old id: new id}. Old ids are what 4.x chats and settings hold: user.<name>[-<quant>], a Lemonade catalog name, or the repo name."""
    by_repo, by_id = {}, {m["id"]: m for m in models}
    for m in models:
        by_repo.setdefault(m["repo"], []).append(m)
    alias = {}

    def add(old, new):
        if old and old != new and old not in alias:
            alias[old] = new

    for old, cp in _catalog_entries(data_dir).items():
        repo, _, variant = cp.partition(":")
        mine = by_repo.get(repo) or []
        pick = None
        if variant:
            q = hf.quant_of(variant) or variant
            pick = next((m for m in mine if m["quant"].lower() == q.lower()), None)
        elif len(mine) == 1:
            pick = mine[0]
        if pick:
            add(old, pick["id"])
    for repo, mine in by_repo.items():
        name = repo.split("/", 1)[1]
        for m in mine:
            q = m["quant"]
            for base in {name, _gguf_stem(name)}:
                add("user.%s-%s" % (base, q), m["id"])
                add("%s-%s" % (base, q), m["id"])
        if len(mine) == 1:                                    # the bare repo name only when it is unambiguous
            for base in {name, _gguf_stem(name)}:
                add(base, mine[0]["id"])
                add("user." + base, mine[0]["id"])
    return {k: v for k, v in alias.items() if v in by_id}


def resolve(model_id, data_dir):
    """The current id for an old model id, or the id unchanged when there is no alias. A provider prefix ('llamacpp/old') is kept."""
    if not model_id or not isinstance(model_id, str):
        return model_id
    al = _read_json(os.path.join(data_dir, ALIASES))
    if model_id in al:
        return al[model_id]
    if "/" in model_id:
        head, tail = model_id.rsplit("/", 1)
        if tail in al:
            return head + "/" + al[tail]
    return model_id


# ---------------------------------------------------------------- step 3: options -> presets.ini
def _recipe_options(data_dir):
    """{lemonade model id: {ctx_size, llamacpp_args, ...}} from recipe_options.json (where 4.x keeps what you saved per model)."""
    out = {}
    for r in (os.path.join(data_dir, "lemonade-data"),):
        if os.path.isdir(r):
            for p in _find_files(r, "recipe_options.json"):
                for k, v in _read_json(p).items():
                    if isinstance(v, dict):
                        out.setdefault(k, v)
    return out


def args_to_preset(text, bin_path=None):
    """Lemonade's llamacpp_args string -> ({preset key: value}, [reasons for what was dropped])."""
    toks, open_quote = llamaargs.lem_split(text or "")
    if open_quote:
        return {}, ["a quote is never closed in %r" % text]
    known = llamaargs.known_flags(bin_path) if bin_path else None
    isflag = lambda t: t.startswith("-") and not re.fullmatch(r"-\d+(\.\d+)?", t)       # noqa: E731
    out, dropped, i = {}, [], 0
    while i < len(toks):
        flag, eq, inline = toks[i].partition("=")
        i += 1
        if not isflag(flag):
            dropped.append("%s (not an option)" % toks[i - 1])
            continue
        val = inline if eq else None
        if val is None and i < len(toks) and not isflag(toks[i]):
            val, i = toks[i], i + 1
        reason = None
        if flag in NOT_IN_PRESET:
            reason = "Shoggoth sets this itself"
        elif flag in llamaargs.FOREIGN_HINTS:
            reason = "it is not a llama.cpp option"
        elif known is not None and flag not in known:
            reason = "this llama-server doesn't list it"
        key = SHORT.get(flag) or flag.lstrip("-")
        if reason is None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", key):
            reason = "not a usable option name"
        if reason is None and val is not None and ("\n" in val or "\r" in val):
            reason = "the value spans lines"
        if reason:
            dropped.append("%s (%s)" % (flag, reason))
            continue
        out[key] = True if val is None else val
    return out, dropped


def carry_options(data_dir, aliases, models, log=print, bin_path=None):
    """Write each model's saved Lemonade options into presets.ini under its new id. A section Shoggoth already has is not overwritten."""
    path = os.path.join(data_dir, "llamacpp", "presets.ini")
    opts = _recipe_options(data_dir)
    ids = {m["id"] for m in models}
    existing = read = llamacpp.read_presets(path)
    new, carried = {}, 0
    for old, o in opts.items():
        mid = aliases.get(old) or (old if old in ids else None)
        if not mid:
            log("  Options saved for %s were not carried over: that model isn't on this computer." % old)
            continue
        sect = {}
        ctx = o.get("ctx_size")
        if isinstance(ctx, (int, float)) and ctx > 0:
            sect["ctx-size"] = int(ctx)
        extra, dropped = args_to_preset(o.get("llamacpp_args") or "", bin_path)
        sect.update(extra)
        for d in dropped:
            log("  Dropped %s from %s's options." % (d, old))
        for k in ("llamacpp_backend", "llamacpp_device"):
            if o.get(k):
                log("  Dropped %s=%s from %s's options (the graphics backend is chosen in Settings now)." % (k, o[k], old))
        if sect:
            new[mid] = sect
            carried += 1
    if new:
        merged = {k: v for k, v in read.items() if k != "*"}
        for mid, sect in new.items():
            merged.setdefault(mid, {})
            for k, v in sect.items():
                merged[mid].setdefault(k, v)
        llamacpp.write_presets(merged, existing.get("*") or llamacpp.DEFAULTS, path=path)
    log("Carried per-model options for %d model%s into presets.ini." % (carried, "" if carried == 1 else "s"))
    return carried


# ---------------------------------------------------------------- step 4: lemond
def _alive(pid):
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def _is_our_lemond(pid, data_dir):
    """Really a lemond running on Shoggoth's own Lemonade folder, so a pid that was recycled is never mistaken for it."""
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            argv = [a.decode("utf8", "replace") for a in f.read().split(b"\0") if a]
    except OSError:
        return False
    return any(os.path.basename(a) == "lemond" for a in argv[:4]) and os.path.join(data_dir, "lemonade-data") in argv


def stop_lemond(data_dir, log=print, wait=10):
    """Stop the lemond Shoggoth 4.x started (it records the pid in lemond.pid). One the person runs has no such record and stays."""
    pf = os.path.join(data_dir, "lemond.pid")
    try:
        with open(pf) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        log("No Lemonade started by Shoggoth is running (a Lemonade you run yourself is left alone).")
        return False
    if not _is_our_lemond(pid, data_dir):
        log("The recorded Lemonade (pid %d) isn't running any more; nothing to stop." % pid)
        try:
            os.remove(pf)
        except OSError:
            pass
        return False
    for sig, grace in ((signal.SIGINT, wait), (signal.SIGTERM, 5), (signal.SIGKILL, 3)):
        try:
            os.kill(pid, sig)
        except OSError:
            break
        end = time.time() + grace
        while time.time() < end and _alive(pid):
            time.sleep(0.1)
        if not _alive(pid):
            break
    try:
        os.remove(pf)
    except OSError:
        pass
    log("Stopped the Lemonade Shoggoth started (pid %d)." % pid)
    return True


# ---------------------------------------------------------------- step 5: the space Lemonade's own files take
def lemonade_bytes(data_dir, library_dir, linked=None):
    """(bytes that deleting Lemonade's own files would free, number of models that still depend on those files).
    Lemonade's engines, its copies and its logs count. A model file counts for nothing when the library has a hard link to it (deleting
    one name frees nothing); a model the library reaches by symbolic link depends on Lemonade's folder, so the page must say so."""
    total, seen = 0, set()
    roots = [os.path.join(data_dir, "lemonade-data"), os.path.join(data_dir, "lemonade"), os.path.join(data_dir, "glibc-run")]
    if library_dir:
        roots.append(os.path.join(library_dir, "lemonade"))
    for r in roots:
        for dirpath, dirnames, filenames in os.walk(r, followlinks=False):
            if _HUB.match(os.path.basename(dirpath)):                     # model storage: counted below
                dirnames[:] = []
                continue
            for fn in filenames:
                p = os.path.join(dirpath, fn)
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                if (st.st_dev, st.st_ino) not in seen and not os.path.islink(p):
                    seen.add((st.st_dev, st.st_ino))
                    total += st.st_size
    try:
        total += os.path.getsize(os.path.join(data_dir, "lemond.log"))
    except OSError:
        pass
    depend = 0
    own = [os.path.realpath(r) for r in roots]
    for paths in (linked or {}).values():
        if any(os.path.islink(p) and any(os.path.realpath(p).startswith(o + os.sep) for o in own) for p in paths):
            depend += 1
    return total, depend


# ---------------------------------------------------------------- the whole thing
def run(data_dir, library_dir, log=print, bin_path=None):
    """Do the migration (the steps not done yet) and return a summary:
    {done, models, linked, aliases, presets, lemond_stopped, lemonade_gb, depend_on_lemonade, steps}.
    `lemonade_gb` is what "Delete Lemonade's own files" would free. `bin_path` is a llama-server to check options against (optional)."""
    marker_path = os.path.join(data_dir, MARKER)
    marker = _read_json(marker_path)
    steps = dict(marker.get("steps") or {})
    summary = dict(marker.get("summary") or {})
    log("Moving from Lemonade to llama.cpp." if not all(steps.get(s) for s in STEPS) else "Lemonade to llama.cpp move was done before; checking.")
    os.makedirs(data_dir, exist_ok=True)
    models = discover(data_dir, library_dir)
    log("Found %d model%s that Lemonade downloaded." % (len(models), "" if len(models) == 1 else "s"))
    linked = {}

    def finish(name):
        steps[name] = True
        _write_json(marker_path, {"version": 1, "steps": steps, "summary": summary, "at": int(time.time())})

    # Every step is cheap and safe to repeat, but a step recorded as done is skipped, so a person who deleted a link is not surprised.
    if not steps.get("link"):
        linked = link_models(data_dir, models, log)
        summary["linked"] = len(linked)
        finish("link")
    else:
        log("Links: done before.")
    if not steps.get("aliases"):
        alias = build_aliases(data_dir, models)
        merged = _read_json(os.path.join(data_dir, ALIASES))
        for k, v in alias.items():
            merged.setdefault(k, v)
        _write_json(os.path.join(data_dir, ALIASES), merged)
        summary["aliases"] = len(merged)
        log("Saved %d old-to-new model name%s in %s." % (len(merged), "" if len(merged) == 1 else "s", ALIASES))
        finish("aliases")
    else:
        log("Model names: done before.")
    aliases = _read_json(os.path.join(data_dir, ALIASES))
    if not steps.get("presets"):
        summary["presets"] = carry_options(data_dir, aliases, models, log, bin_path)
        finish("presets")
    else:
        log("Model options: done before.")
    if not steps.get("lemond"):
        summary["lemond_stopped"] = stop_lemond(data_dir, log)
        finish("lemond")
    else:
        log("Lemonade process: done before.")
    if not linked:                                                    # for the dependency count on a repeat run
        view = view_dir(data_dir)
        linked = {m["id"]: [p for p in ([os.path.join(view, m["id"] + ".gguf")] + [os.path.join(view, m["id"], os.path.basename(f)) for f in m["files"]])
                            if os.path.lexists(p)] for m in models}
    nbytes, depend = lemonade_bytes(data_dir, library_dir, linked)
    summary.update(models=len(models), lemonade_bytes=nbytes, lemonade_gb=round(nbytes / 1e9, 2), depend_on_lemonade=depend)
    log("Lemonade's own files take %.2f GB%s." % (nbytes / 1e9, "; %d model%s still reach their files through Lemonade's folder"
                                                  % (depend, "" if depend == 1 else "s") if depend else ""))
    summary["done"] = all(steps.get(s) for s in STEPS)
    summary["steps"] = dict(steps)
    _write_json(marker_path, {"version": 1, "steps": steps, "summary": summary, "at": int(time.time())})
    return summary
