"""The model library: one list of every model on this computer, whichever engine runs it.

Shoggoth reads each model file's own header (gguf.py), asks every provider whether it can run the file and how much room it needs,
sorts the model into a tier (Small, Medium, Large) and picks the provider that serves it. Where files live is one folder the
person chooses (the Models folder); each engine keeps what it downloads in its own subfolder (lemonade/, strata/), and Shoggoth keeps small per-engine "views" (folders of
links) so an engine is only ever shown files it can run. Nothing is copied or moved. Standard library only.
"""
import json
import os
import re
import shutil
import time

import gguf
import hardware
import modelscan
import registry
import store

TIERS = ("small", "medium", "large")
TIER_LABEL = {"small": "Small", "medium": "Medium", "large": "Large"}
SMALL_GB, MEDIUM_GB = 10, 24             # graphics memory a model needs: under 10 is Small, up to 24 Medium, beyond that Large
SUBDIRS = ("lemonade", "strata")         # one folder per engine under the person's Models folder; nothing else lives there
_index = {"loaded": False, "d": {}}


# ---------------------------------------------------------------- where the library lives
def root():
    return (store.load().get("library") or {}).get("root", "") or ""


def places():
    """Disks a person could keep models on, most free space first: [{path, folder, free_gb, total_gb, suggested}]; `folder` is the
    Models folder Shoggoth would create there.

    A place is a real, writable disk or the home folder. The suggestion is the one with the most room."""
    skip_fs = {"proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2", "squashfs", "overlay", "efivarfs", "securityfs", "debugfs",
               "tracefs", "pstore", "bpf", "configfs", "fusectl", "mqueue", "hugetlbfs", "autofs", "binfmt_misc", "ramfs", "fuse.portal", "nsfs"}
    skip_prefix = ("/boot", "/snap", "/var/lib", "/sys", "/proc", "/dev", "/run", "/usr", "/etc", "/opt")
    found = {}
    try:
        with open("/proc/mounts") as f:
            mounts = [l.split() for l in f]
    except OSError:
        mounts = []
    home = os.path.expanduser("~")
    for m in mounts:
        if len(m) < 3 or m[2] in skip_fs or m[2].startswith("fuse.gvfs"):
            continue
        mp = m[1].replace("\\040", " ")
        if mp != "/" and mp.startswith(skip_prefix) or not os.path.isdir(mp):
            continue
        base = home if mp == "/" else mp                       # the system disk: offer the home folder, not /
        if not os.access(base, os.W_OK):
            continue
        try:
            u = shutil.disk_usage(mp)
        except OSError:
            continue
        key = os.path.realpath(base)
        if key not in found or u.free > found[key]["free"]:
            found[key] = {"path": base, "free": u.free, "total": u.total}
    rows = sorted(found.values(), key=lambda r: -r["free"])
    return [{"path": r["path"], "folder": os.path.join(r["path"], "Models"), "free_gb": round(r["free"] / 1e9, 1), "total_gb": round(r["total"] / 1e9, 1),
             "suggested": i == 0 and r["free"] > 20e9} for i, r in enumerate(rows)]


def views_base():
    """Where the per-engine views (folders of links) live: Shoggoth's own data folder, so the Models folder holds only models and the
    links work whatever kind of disk the models are on."""
    return os.path.join(store.data_dir(), "views")


def set_root(path):
    """Make `path` the library root: create it (and its engine folders), prove it is writable, remember it."""
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("Choose a folder for your models.")
    path = os.path.abspath(os.path.expanduser(raw))
    if path == "/":
        raise ValueError("Choose a folder for your models, not the whole disk.")
    try:
        for sub in SUBDIRS:
            os.makedirs(os.path.join(path, sub), exist_ok=True)
        probe = os.path.join(path, ".zest-write-test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as e:
        raise ValueError("Shoggoth can't write to that folder (%s). Choose another." % (e.strerror or e))
    s = store.load()
    s.setdefault("library", {})["root"] = path
    store.save(s)
    registry.reload()                                         # providers read the library folder through their context
    return path


# ---------------------------------------------------------------- reading files
INDEX_VERSION = 2          # 2: headers also carry the model's attention shape (for the memory the context costs)


def _index_path():
    return os.path.join(store.data_dir(), "library-index.json")


def _header(path):
    """The header of one file, remembered by size and modified time so a rescan of a big library is instant."""
    if not _index["loaded"]:
        try:
            with open(_index_path()) as f:
                _index["d"] = json.load(f)
        except (OSError, ValueError):
            _index["d"] = {}
        _index["loaded"] = True
    rp = os.path.realpath(path)
    try:
        st = os.stat(rp)
    except OSError:
        return None
    hit = _index["d"].get(rp)
    if hit and hit.get("size") == st.st_size and hit.get("mtime") == int(st.st_mtime) and hit.get("v") == INDEX_VERSION:
        return hit.get("h")
    h = gguf.read_header(rp)
    _index["d"][rp] = {"size": st.st_size, "mtime": int(st.st_mtime), "h": h, "v": INDEX_VERSION}
    return h


def _save_index():
    try:
        os.makedirs(os.path.dirname(_index_path()), exist_ok=True)
        tmp = _index_path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_index["d"], f)
        os.replace(tmp, _index_path())
    except OSError:
        pass


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _pretty(stem, header):
    name = (header or {}).get("name") or ""
    base = name if name and len(name) > 3 else re.sub(r"[-_]+", " ", stem)
    return re.sub(r"\s+", " ", base).strip()


def _entries_in(folder, label, owner):
    """Model entries (one per file set) for the GGUF files under `folder`."""
    files = modelscan._gguf_files(folder)
    by_dir, projectors = {}, {}
    for path, size in files:
        if modelscan._is_projector(path):
            projectors.setdefault(os.path.dirname(path), []).append(path)
            continue
        stem, i, n = gguf.shard_of(path)
        by_dir.setdefault((os.path.dirname(path), stem), {"n": n, "shards": {}})["shards"][i] = (path, size)
    out = []
    # a projector may live in the model's folder or one level up (Strata keeps one beside its model folders)
    for (d, stem), g in by_dir.items():
        shards = [g["shards"][i] for i in sorted(g["shards"])]
        first = shards[0][0]
        h = _header(first)
        if h is None:
            continue
        near = projectors.get(d) or projectors.get(os.path.dirname(d)) or []
        role = gguf.role_of(h.get("architecture", ""), stem, d)
        if role != "chat":
            continue
        out.append({"stem": stem, "files": [p for p, _ in shards], "size_bytes": sum(s for _, s in shards), "complete": len(shards) == g["n"],
                    "format": "gguf", "architecture": h.get("architecture", ""), "quant": h.get("quant", ""), "context": h.get("context", 0),
                    "size_label": h.get("size_label", ""), "title": _pretty(stem, h), "vision": bool(near), "mmproj": near[0] if near else "",
                    "mtp": "mtp" in stem.lower(), "folder": d, "source": label, "owner": owner,
                    "attn": dict(h.get("attn") or {}, arch=h.get("architecture", ""))})
    return out


# ---------------------------------------------------------------- the catalog
def sources(reg=None):
    """[(folder, label, owner provider id or None)] the catalog reads: each provider's own folders, the library root's, and folders
    the person has brought in (Settings > models). Nothing else is scanned: Shoggoth does not go through Downloads uninvited."""
    reg = reg or registry.default()
    out, seen = [], set()

    def add(path, label, owner):
        rp = os.path.realpath(path)
        if os.path.isdir(rp) and rp not in seen:
            seen.add(rp)
            out.append((rp, label, owner))
    for p in reg.all():
        try:
            for path, label in p.model_folders():
                add(path, label, p.id)
        except Exception:  # noqa: BLE001
            continue
    r = root()
    if r:
        for sub in SUBDIRS:
            add(os.path.join(r, sub), "Shoggoth library", None)
    m = store.load().get("models") or {}
    for imp in m.get("imports") or []:
        if imp.get("kind") == "folder":
            add(imp.get("path", ""), imp.get("label") or "Your folder", None)
    if r:                                                      # models Shoggoth itself downloaded before there was a library folder stay in the library
        add(modelscan.hf_cache_dir(), "Hugging Face cache", None)
        if m.get("storage") == "custom" and m.get("custom_dir"):
            add(m["custom_dir"], "Your earlier models folder", None)
    return out


def _tier(size_gb, only_large):
    """By the size of the model itself: a 22 GB model is Medium even though it needs a little more than that to run."""
    if only_large or size_gb > MEDIUM_GB:
        return "large"
    return "small" if size_gb < SMALL_GB else "medium"


def catalog(reg=None, machine=None, refresh=False):
    """Every model, with who can run it, what it needs, its tier, the provider chosen to serve it and whether it fits here.

    [{id, title, files, size_bytes, architecture, quant, context, vision, mtp, folder, source, tier, provider, providers, need_gb, fit, why, ids}]"""
    reg = reg or registry.default()
    machine = machine or hardware.machine()
    found, seen_real, errors = {}, set(), []
    for folder, label, owner in sources(reg):
        for e in _entries_in(folder, label, owner):
            key = os.path.realpath(e["files"][0])
            if key in seen_real:
                continue
            seen_real.add(key)
            base = _slug("%s-%s" % (e["stem"], e["size_bytes"]))
            found[base] = e
    _save_index()
    have_ids = {(pid, mid) for e in found.values() for pid, mid in
                [(p.id, p.model_id(e)) for p in reg.all() if (p.can_run(e)[0])] if mid}
    for p in reg.all():
        try:
            listed = p.library_models()
        except Exception:  # noqa: BLE001
            listed = []
        for e in listed:
            if (p.id, e["ids"].get(p.id)) in have_ids:
                continue                                          # the same model, already read from its files
            found[_slug("%s-%s-%s" % (p.id, e["stem"], e["size_bytes"]))] = e
    big = [p for p in reg.all() if "large" in p.tier_hint()]
    rest = [p for p in reg.all() if "large" not in p.tier_hint()]
    out = []
    for key, e in found.items():
        listed_by = e.get("listed_by")
        cap = {p.id: ((True, "") if p.id == listed_by else p.can_run(e) if not listed_by else (False, "%s can't use a model it manages itself." % p.name)) for p in reg.all()}
        runnable = [p for p in reg.all() if cap[p.id][0]]
        if not runnable:
            e = dict(e, tier="", provider=None, providers={k: {"ok": ok, "reason": r} for k, (ok, r) in cap.items()}, need_gb=None, fit="no",
                     why="No engine here can run this kind of model (%s)." % (e["architecture"] or "unknown"), ids={})
            e["id"] = key
            e["ref"], e["engine_name"] = "", ""
            out.append(e)
            continue
        only_large = all(p in big for p in runnable)
        tier = _tier(e["size_bytes"] / 1e9, only_large)
        # a Large model goes to the big-model engine when one can run it; everything else goes to the fast engine
        chosen = next((p for p in runnable if p.prefers(e)), None)          # an engine that asked for this kind of model gets it
        chosen = chosen or (next((p for p in big if cap[p.id][0]), None) if tier == "large" else None)
        chosen = chosen or next((p for p in rest if cap[p.id][0]), None) or runnable[0]
        want = int((store.load().get("chat") or {}).get("ctx") or 131072)            # the context window chats ask for
        used = min(want, e.get("context") or want)
        e = dict(e, kv_ctx=used, kv_gb=gguf.kv_cache_gb(e.get("attn"), used))        # what remembering that much conversation costs in memory
        fit, why = chosen.fit(e, machine)
        ids = dict(e["ids"]) if e.get("listed_by") else {p.id: p.model_id(e) for p in runnable}
        out.append(dict(e, id=key, tier=tier, provider=chosen.id, need_gb=chosen.need_gb(e), fit=fit, why=why,
                        providers={k: {"ok": ok, "reason": r} for k, (ok, r) in cap.items()}, ids=ids,
                        ref=chosen.ref(ids[chosen.id]) if ids.get(chosen.id) else "", engine_name=chosen.name))
    order = {t: i for i, t in enumerate(TIERS)}
    out.sort(key=lambda m: (order.get(m["tier"], 9), (m["need_gb"] or 0), m["title"].lower()))
    return out


# ---------------------------------------------------------------- per-engine views
def sync_views(entries=None, reg=None):
    """Keep <Shoggoth data folder>/views/<engine>/ holding links to exactly the files that engine should be shown: those routed to it that it doesn't
    already keep itself. Only links are ever created or removed here, never real files. Returns {engine: view folder}."""
    reg = reg or registry.default()
    entries = entries if entries is not None else catalog(reg)
    out = {}
    for p in reg.all():
        if not (p.spec.get("models") or {}).get("view"):
            continue
        view = os.path.join(views_base(), p.id)
        os.makedirs(view, exist_ok=True)
        own = [os.path.realpath(path) for path, _ in p.model_folders()]
        want = {}
        for e in entries:
            if e.get("provider") != p.id or not e.get("complete", True):
                continue
            if any(os.path.realpath(f).startswith(o + os.sep) for f in e["files"] for o in own):
                continue                                       # the engine keeps this one itself
            for f in e["files"] + ([e["mmproj"]] if e.get("mmproj") else []):
                want[os.path.basename(f)] = os.path.realpath(f)
        for name in os.listdir(view):
            link = os.path.join(view, name)
            if os.path.islink(link) and (name not in want or os.readlink(link) != want[name]):
                os.remove(link)
        for name, target in want.items():
            link = os.path.join(view, name)
            if not os.path.lexists(link):
                os.symlink(target, link)
        out[p.id] = view
    return out


def lemonade_config():
    """The folder settings Lemonade should have so it reads the library: its own downloads under lemonade/, everything else it can run
    through the view. {} until a root is chosen. Applying this is only ever done to a Lemonade Shoggoth runs itself."""
    r = root()
    return {"models_dir": os.path.join(r, "lemonade"), "extra_models_dir": os.path.join(views_base(), "lemonade")} if r else {}


def _nearest_existing(path):
    p = os.path.abspath(path or "/")
    while p != "/" and not os.path.isdir(p):
        p = os.path.dirname(p)
    return p


def models_disk():
    """Where the next download goes and how much room is there: {where, free_gb, missing}. `missing` is true when a library folder was chosen
    but can't be found now (a disk that isn't connected)."""
    r = root()
    if r:
        where = r
    else:
        import lem
        md = lem.flat_config().get("models_dir") if lem.healthy() else None
        where = md if md and md != "auto" else modelscan.hf_cache_dir()
    here = _nearest_existing(where)
    return {"where": where, "free_gb": round(shutil.disk_usage(here).free / 1e9, 1), "missing": bool(r) and not os.path.isdir(r)}


def apply_to_lemonade():
    """Point a Lemonade Shoggoth runs itself at the library: downloads go to <root>/lemonade, and every other file it can run is shown to it
    through its view of links. Returns (ok, message). Does nothing (ok) until a library folder is chosen."""
    if not root():
        return True, ""
    if not os.path.isdir(root()):
        return False, "The models folder %s can't be found (is its disk connected?), so Lemonade was left as it was." % root()
    import lem
    cfg = lemonade_config()
    ok, msg = lem.set_config({"models_dir": cfg["models_dir"]})        # first, so what it kept before (the Hugging Face cache) is no longer "its own"
    if not ok:
        return ok, msg
    registry.reload()
    sync_views()
    return lem.set_config({"extra_models_dir": cfg["extra_models_dir"]})     # and again, so it looks in the view now that the links exist


def summary():
    """What the Library page and the first-run screen need."""
    r = root()
    cat = catalog()
    return {"root": r, "chosen": bool(r), "places": places(), "machine": hardware.machine(),
            "tiers": [{"id": t, "label": TIER_LABEL[t]} for t in TIERS], "models": cat,
            "bytes": sum(m["size_bytes"] for m in cat),
            "disk": {"free_gb": round(shutil.disk_usage(r).free / 1e9, 1)} if r and os.path.isdir(r) else None,
            "missing": bool(r) and not os.path.isdir(r)}


# ---------------------------------------------------------------- what the model picker shows
def _start_hint(seconds):
    if seconds <= 0:
        return "Ready now"
    if seconds < 45:
        return "Ready in seconds"
    return "About %d minute%s to start" % (round(seconds / 60), "" if round(seconds / 60) == 1 else "s")


_pk = {"t": 0, "v": None}


def picker(max_age=8):
    """The models a person can pick for a chat, from every engine, with what helps them choose:

    [{id, title, tier, tier_label, fit, why, size_gb, need_gb, live, start_s, start, engine, engine_name, vision, tools, reasoning, ctx, ctx_fixed}].
    Engine names are carried for a discreet line; the interface never leads with them."""
    import router
    if max_age and _pk["v"] is not None and time.time() - _pk["t"] < max_age:
        return _pk["v"]
    reg = registry.default()
    machine = hardware.machine()
    by = {}
    for e in catalog(reg, machine):
        for pid, mid in (e.get("ids") or {}).items():
            if mid:
                by[(pid, mid)] = e
    out = []
    for m in router.chat_models():
        e = by.get((m["engine"], m["pi_model"])) or {}
        p = reg.get(m["engine"])
        start = 0 if m["live"] else (p.memory().get("start_estimate_s") or 0)
        out.append({"id": m["id"], "title": e.get("title") or m["pi_model"], "tier": e.get("tier") or "", "tier_label": TIER_LABEL.get(e.get("tier"), "Other"),
                    "fit": e.get("fit") or "fits", "why": e.get("why") or "", "size_gb": round(e.get("size_bytes", 0) / 1e9, 1), "need_gb": e.get("need_gb"),
                    "live": bool(m["live"]), "start_s": start, "start": _start_hint(start), "engine": m["engine"], "engine_name": p.name if p else m["engine"],
                    "vision": bool(m.get("vision")), "tools": bool(m.get("tools")), "reasoning": bool(m.get("reasoning")), "ctx": m.get("ctx") or 0,
                    "ctx_fixed": router.fixed_ctx(m["id"])})
    order = {t: i for i, t in enumerate(TIERS)}
    out.sort(key=lambda x: (order.get(x["tier"], 9), x["need_gb"] or x["size_gb"] or 0, x["title"].lower()))
    res = {"models": out, "machine": machine, "tiers": [{"id": t, "label": TIER_LABEL[t]} for t in TIERS]}
    _pk.update(t=time.time(), v=res)
    return res


def lemonade_accepts(path):
    """Can Lemonade run this file? Used so an imported folder never shows it a model it would list but fail to load."""
    h = gguf.read_header(path)
    prov = registry.default().get("lemonade")
    if not h or not prov:
        return True
    return prov.can_run({"format": "gguf", "architecture": h.get("architecture", "")})[0]
