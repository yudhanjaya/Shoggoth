"""The page's model list, model downloads, deleting a downloaded model, and one model's llama.cpp options.

The page's Models and Getting ready screens read one list (`listing()`): the models on this computer that llama.cpp serves, and the
models recommendations/models.yaml suggests that aren't here yet. Getting one starts a download (`pull()`) that runs here in a thread
with hf.download (resume, sha256 check) into the folder new models go to (`dest_dir()`), then has llama.cpp read its folder again.

Deleting only ever removes files inside the folders Shoggoth downloads into. A model in a folder the person brought in, or one another
engine keeps, is only read, so Shoggoth says where it is instead.
"""
import os
import re
import shlex
import threading
import time

import hf
import library
import llamacpp
import recs
import store

KEEP_ENDED = 120                  # seconds a finished or failed download stays in the list, so the page can say what happened
MANAGED = ("model", "mmproj", "ctx-size")      # options Shoggoth writes itself; the rest of a model's presets section is "Extra options"
_lock = threading.Lock()
_jobs = {}                        # model name -> {id, model_name, status, percent, done, total, error, ended, cancel, repo, files}


# ---------------------------------------------------------------- where new models go
def dest_dir():
    """The folder a download goes to: the library's llama.cpp folder when there is a library, else Shoggoth's own models folder."""
    r = library.root()
    return os.path.join(r, library.SUBDIRS[0]) if r else os.path.join(store.data_dir(), "models")


def own_dirs():
    """Folders Shoggoth downloads into, so it may delete what is in them."""
    out = [os.path.join(store.data_dir(), "models")]
    if library.root():
        out.append(os.path.join(library.root(), library.SUBDIRS[0]))
    return [os.path.realpath(d) for d in out]


def _inside(path, folders):
    rp = os.path.realpath(path)
    return any(rp.startswith(d + os.sep) for d in folders)


# ---------------------------------------------------------------- the list the page reads
def _labels(e):
    out = ["chat", "llamacpp"]
    if e.get("vision"):
        out.append("vision")
    return out


def _rec_for(e, wanted):
    """The recommended entry a model on disk is, matched by the file its checkpoint names, or by its id or another name."""
    names = {str(e.get("stem", "")).lower(), str(e.get("ref", "")).lower()}
    base = {os.path.basename(f).lower() for f in e.get("files") or []}
    for it in wanted:
        ck = ((it.get("download") or {}).get("checkpoint") or "")
        repo, _, what = ck.partition(":")
        if what and (what.lower() in base or (repo and _matches_quant(e, repo, what))):
            return it
        if names & {x.lower() for x in [it["id"]] + list(it.get("also") or [])}:
            return it
    return None


def _matches_quant(e, repo, quant):
    """A file downloaded into <dest>/<owner>/<repo>/ whose quant is `quant`."""
    folder = os.path.join(*repo.split("/")) if "/" in repo else repo
    return any(os.sep + folder + os.sep in os.path.realpath(f) for f in e.get("files") or []) and \
        str(e.get("quant", "")).lower() == quant.lower()


def listing(entries=None):
    """[{id, title, recipe, size, downloaded, labels, checkpoint, suggested, rec, path_owned}] for the page."""
    entries = entries if entries is not None else library.catalog()
    wanted = recs.models()["recommended"]
    owned = own_dirs()
    out, got = [], set()
    for e in entries:
        if e.get("provider") != "llamacpp" or not e.get("ref"):
            continue
        it = _rec_for(e, wanted)
        if it:
            got.add(it["id"])
        out.append({"id": e["ref"], "title": e.get("title") or e["ref"], "recipe": "llamacpp", "size": round((e.get("size_bytes") or 0) / 1e9, 2),
                    "downloaded": True, "labels": _labels(e), "checkpoint": ((it or {}).get("download") or {}).get("checkpoint", ""),
                    "suggested": bool(it), "rec": it["id"] if it else "", "owned": bool(e.get("files")) and _inside(e["files"][0], owned),
                    "folder": e.get("folder", "")})
    for it in wanted:
        if it["id"] in got:
            continue
        out.append({"id": it["id"], "title": it.get("title") or it["id"], "recipe": "llamacpp", "size": float(it.get("size_gb") or 0),
                    "downloaded": False, "labels": ["chat", "llamacpp"] + (["vision"] if it.get("vision") else []),
                    "checkpoint": (it.get("download") or {}).get("checkpoint", ""), "suggested": True, "rec": it["id"], "owned": False, "folder": ""})
    return out


# ---------------------------------------------------------------- downloads
def _public(j):
    return {k: j[k] for k in ("id", "model_name", "status", "percent", "done", "total", "error")}


def jobs():
    """Downloads running, paused by an error, or ended in the last two minutes."""
    now = time.time()
    with _lock:
        for k in [k for k, j in _jobs.items() if j["ended"] and now - j["ended"] > KEEP_ENDED]:
            _jobs.pop(k)
        return [_public(j) for j in _jobs.values()]


def _checkpoint_for(name):
    it = next((x for x in recs.models()["recommended"] if name in [x["id"]] + list(x.get("also") or [])), None)
    d = (it or {}).get("download") or {}
    return d.get("checkpoint", ""), d.get("mmproj", "")


def pull(body, run=None):
    """Start downloading a model. body: {model_name, checkpoint ("owner/repo:Q4_K_M" or "owner/repo:file.gguf"), mmproj}. A recommended
    model needs only its name. Returns the job; raises ValueError with a plain sentence when it can't start."""
    name = str(body.get("model_name") or "").strip()
    ck, mm = str(body.get("checkpoint") or "").strip(), body.get("mmproj")
    if not ck:
        ck, mm = _checkpoint_for(name)
    repo, _, what = ck.partition(":")
    if not name or not repo or not what:
        raise ValueError("Shoggoth doesn't know where to download %s from." % (name or "that model"))
    if not hf._REPO.match(repo):
        raise ValueError("%s isn't a Hugging Face model name." % repo)
    with _lock:
        cur = _jobs.get(name)
        if cur and cur["status"] == "downloading":
            return _public(cur)
        j = {"id": "model:" + name, "model_name": name, "status": "downloading", "percent": 0, "done": 0, "total": 0, "error": "", "ended": 0,
             "cancel": threading.Event(), "repo": repo, "dest": dest_dir()}
        _jobs[name] = j
    t = threading.Thread(target=run or _run, args=(j, repo, what, mm or False), daemon=True)
    t.start()
    return _public(j)


def _run(j, repo, what, mmproj):
    def progress(done, total):
        j["done"], j["total"] = done, total
        j["percent"] = int(done * 100 / total) if total else 0
    try:
        os.makedirs(j["dest"], exist_ok=True)
        hf.download(repo, what, j["dest"], progress=progress, cancel=j["cancel"], mmproj=mmproj)
    except hf.HFError as e:
        _end(j, "cancelled" if e.cancelled or j["cancel"].is_set() else "error", "" if j["cancel"].is_set() else str(e))
        return
    except OSError as e:
        _end(j, "error", "Shoggoth couldn't save the download: %s." % (e.strerror or e))
        return
    if j["cancel"].is_set():
        return _end(j, "cancelled", "")
    ok, msg = library.reload_engine()
    j["percent"] = 100
    _end(j, "completed", "" if ok else msg)


def _end(j, status, error):
    j["status"], j["error"], j["ended"] = status, error, time.time()


def control(body):
    """{id: "model:<name>", action: cancel|remove}. Cancel stops it (what came down is kept, so getting it again carries on); remove
    forgets it and throws away the unfinished part."""
    name = str(body.get("id") or "").partition("model:")[2] or str(body.get("model_name") or "")
    act = body.get("action")
    with _lock:
        j = _jobs.get(name)
    if not j:
        return {"ok": True}
    if act == "cancel":
        j["cancel"].set()
        if j["status"] == "downloading":
            _end(j, "cancelled", "")
    elif act == "remove":
        j["cancel"].set()
        with _lock:
            _jobs.pop(name, None)
        folder = os.path.join(j["dest"], *j["repo"].split("/"))
        for dirpath, _, names in os.walk(folder):
            for n in names:
                if n.endswith(".part"):
                    try:
                        os.remove(os.path.join(dirpath, n))
                    except OSError:
                        pass
    else:
        raise ValueError("Unknown action.")
    return {"ok": True}


# ---------------------------------------------------------------- deleting one
def delete(name, entries=None):
    """Remove the files of a model Shoggoth downloaded. Returns {ok, freed_gb}; raises ValueError when it can't."""
    entries = entries if entries is not None else library.catalog()
    e = next((x for x in entries if x.get("ref") == name and x.get("files")), None)
    if not e:
        raise ValueError("%s isn't on this computer." % name)
    files = list(e["files"]) + ([e["mmproj"]] if e.get("mmproj") else [])
    owned = own_dirs()
    if not all(_inside(f, owned) for f in e["files"]):
        raise ValueError("This model is in %s, which Shoggoth only reads. Delete it there if you no longer want it." % e.get("folder", "another folder"))
    if name in llamacpp.loaded_ids():
        llamacpp.unload(name)
    freed = 0
    for f in files:
        if not _inside(f, owned) or not os.path.isfile(f):
            continue                                   # a vision file shared from somewhere else stays
        if e.get("mmproj") == f and _shared_projector(f, entries, e):
            continue
        freed += os.path.getsize(f)
        os.remove(f)
    folder = os.path.dirname(e["files"][0])
    while _inside(folder, owned) and os.path.isdir(folder) and not os.listdir(folder):
        os.rmdir(folder)
        folder = os.path.dirname(folder)
    library.reload_engine()
    return {"ok": True, "freed_gb": round(freed / 1e9, 2)}


def _shared_projector(path, entries, me):
    return any(x is not me and x.get("mmproj") == path for x in entries)


# ---------------------------------------------------------------- one model's options (its section of presets.ini)
def _args_text(sec):
    parts = []
    for k, v in sec.items():
        if k in MANAGED:
            continue
        if v in (True, "true"):
            parts.append("--" + k)
        else:
            parts += ["--" + k, shlex.quote(str(v))]
    return " ".join(parts)


def _args_dict(text):
    toks = shlex.split(text or "")
    out, i = {}, 0
    while i < len(toks):
        t = toks[i]
        if not t.startswith("-"):
            raise ValueError("%s isn't an option: options start with --." % t)
        k, eq, v = t.lstrip("-").partition("=")
        if not re.match(r"^[a-z0-9][a-z0-9-]*$", k):
            raise ValueError("%s isn't a llama-server option name." % t)
        if k in MANAGED:
            raise ValueError("Shoggoth sets --%s itself%s." % (k, "; use Context size above" if k == "ctx-size" else ""))
        if eq:
            out[k] = v
        elif i + 1 < len(toks) and not re.match(r"^--?[a-zA-Z]", toks[i + 1]):
            out[k] = toks[i + 1]
            i += 1
        else:
            out[k] = True
        i += 1
    return out


def options(name):
    """{recipe, effective, saved, resolved_ctx_size} for the options dialog."""
    sec = llamacpp.read_presets().get(name) or {}
    star = llamacpp.read_presets().get("*") or {}
    try:
        ctx = int(sec.get("ctx-size") or 0)
    except ValueError:
        ctx = 0
    try:
        dflt = int(star.get("ctx-size") or llamacpp.DEFAULTS["ctx-size"])
    except ValueError:
        dflt = llamacpp.DEFAULTS["ctx-size"]
    args = _args_text(sec)
    saved = {k: v for k, v in (("ctx_size", ctx or None), ("llamacpp_args", args or None)) if v}
    return {"recipe": "llamacpp", "model_name": name, "saved": saved, "effective": {"ctx_size": ctx or dflt, "llamacpp_args": args},
            "resolved_ctx_size": ctx or dflt}


def set_options(name, body):
    """Change a model's options. A key set to null goes back to the default."""
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$", name or ""):
        raise ValueError("Not a model name Shoggoth can save options for.")
    presets = llamacpp.read_presets()
    sec = dict(presets.get(name) or {})
    if "ctx_size" in body:
        v = body["ctx_size"]
        if v in (None, "", 0):
            sec.pop("ctx-size", None)
        else:
            try:
                n = int(v)
            except (TypeError, ValueError):
                raise ValueError("The context size must be a whole number.")
            if not 512 <= n <= 4_194_304:
                raise ValueError("The context size must be between 512 and 4194304.")
            sec["ctx-size"] = n
    if "llamacpp_args" in body:
        new = _args_dict(body["llamacpp_args"] or "")
        sec = {k: v for k, v in sec.items() if k in MANAGED}
        sec.update(new)
    presets = {k: v for k, v in presets.items() if k != "*"}
    if sec:
        presets[name] = sec
    else:
        presets.pop(name, None)
    llamacpp.write_presets(presets, llamacpp.read_presets().get("*") or llamacpp.DEFAULTS)
    if llamacpp.running():
        llamacpp.reload()
    return options(name)
