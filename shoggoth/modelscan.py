"""Where models live: finding ones you already have, and telling Lemonade about them.

Lemonade has two relevant settings. `models_dir` is where it keeps (and downloads) models; "auto" means the
Hugging Face cache. `extra_models_dir` is a folder it searches for GGUF files to import. To bring in models from
several places (LM Studio, Ollama and so on) without copying gigabytes, Shoggoth builds one folder of symbolic links
and points `extra_models_dir` at it.
"""
import hashlib
import json
import os
import re
import shutil
import time

import lem
import store

_SKIP = re.compile(r"^(ggml-vocab-)", re.I)
_MAX_FILES = 6000
_MAX_DEPTH = 6


def _home(*p):
    return os.path.join(os.path.expanduser("~"), *p)


def _known_folders():
    ollama = os.environ.get("OLLAMA_MODELS") or _home(".ollama", "models")
    return [
        ("LM Studio", [_home(".lmstudio", "models"), _home(".cache", "lm-studio", "models")]),
        ("llama.cpp cache", [_home(".cache", "llama.cpp")]),
        ("GPT4All", [_home(".local", "share", "nomic.ai", "GPT4All")]),
        ("Jan", [_home("jan", "models"), _home(".local", "share", "Jan", "data", "models")]),
        ("Models folder", [_home("models"), _home("Models")]),
        ("Downloads", [_home("Downloads")]),
    ], ollama


def hf_cache_dir():
    """Where Lemonade's "auto" model folder points: the Hugging Face hub cache."""
    if os.environ.get("HF_HUB_CACHE"):
        return os.environ["HF_HUB_CACHE"]
    if os.environ.get("HF_HOME"):
        return os.path.join(os.environ["HF_HOME"], "hub")
    return _home(".cache", "huggingface", "hub")


def farm_dir():
    return os.path.join(store.data_dir(), "imported-models")


def _gguf_files(root):
    """GGUF files under root, as (path, size), without following links or wandering forever."""
    out, base_depth = [], root.rstrip(os.sep).count(os.sep)
    farm = os.path.realpath(farm_dir())
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        if os.path.realpath(dirpath).startswith(farm) or dirpath.count(os.sep) - base_depth >= _MAX_DEPTH:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if fn.lower().endswith(".gguf") and not _SKIP.match(fn):
                p = os.path.join(dirpath, fn)
                try:
                    out.append((p, os.path.getsize(p)))
                except OSError:
                    pass
                if len(out) >= _MAX_FILES:
                    return out
    return out


def _is_projector(path):
    return "mmproj" in os.path.basename(path).lower()


def _source_id(kind, path):
    return hashlib.sha1(f"{kind}:{path}".encode()).hexdigest()[:10]


def _folder_source(label, path):
    files = _gguf_files(path)
    models = [f for f in files if not _is_projector(f[0])]
    if not models:
        return None
    return {"id": _source_id("folder", path), "kind": "folder", "label": label, "path": path,
            "count": len(models), "bytes": sum(s for _, s in models),
            "samples": [os.path.basename(p) for p, _ in models[:3]]}


def _ollama_models(root):
    """Ollama stores models as hash-named blobs; the manifests say which blob is which model."""
    out, mroot = [], os.path.join(root, "manifests")
    if not os.path.isdir(mroot):
        return out
    for dirpath, _, filenames in os.walk(mroot):
        for fn in filenames:
            mp = os.path.join(dirpath, fn)
            try:
                with open(mp) as f:
                    manifest = json.load(f)
            except (OSError, ValueError):
                continue
            rel = os.path.relpath(mp, mroot).split(os.sep)       # registry/namespace/name/tag
            if len(rel) < 4:
                continue
            name = rel[-2] if rel[-3] == "library" else f"{rel[-3]}/{rel[-2]}"
            for layer in manifest.get("layers", []):
                if layer.get("mediaType") == "application/vnd.ollama.image.model":
                    blob = os.path.join(root, "blobs", str(layer.get("digest", "")).replace(":", "-"))
                    if os.path.isfile(blob):
                        out.append({"name": f"{name}-{rel[-1]}", "blob": blob, "size": os.path.getsize(blob)})
    return out


def scan():
    """Look in the usual places for models that aren't in the Hugging Face cache."""
    sources, seen = [], set()
    folders, ollama = _known_folders()
    for label, paths in folders:
        for p in paths:
            rp = os.path.realpath(p)
            if os.path.isdir(rp) and rp not in seen:
                seen.add(rp)
                s = _folder_source(label, rp)
                if s:
                    sources.append(s)
    for root in [ollama, "/usr/share/ollama/.ollama/models"]:
        if os.path.isdir(root):
            found = _ollama_models(root)
            if found:
                sources.append({"id": _source_id("ollama", root), "kind": "ollama", "label": "Ollama", "path": root,
                                "count": len(found), "bytes": sum(m["size"] for m in found),
                                "samples": [m["name"] for m in found[:3]]})
    return sources


def source_from_path(path, label=None):
    rp = os.path.realpath(os.path.expanduser(path))
    if not os.path.isdir(rp):
        raise ValueError("That folder doesn't exist.")
    s = _folder_source(label or os.path.basename(rp) or rp, rp)
    if not s:
        raise ValueError("No GGUF model files were found in that folder (looked up to six levels deep).")
    return s


def _slug(text):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")[:80] or "models"


def sync_farm(sources, accept=None):
    """Rebuild the folder of symbolic links for the chosen sources. Returns how many models were linked.

    `accept(path)` may refuse a file (one the engine reading this folder can't run), so it is never shown to it."""
    farm = farm_dir()
    shutil.rmtree(farm, ignore_errors=True)           # removes only the links themselves, never their targets
    if not sources:
        return 0
    os.makedirs(farm, exist_ok=True)
    linked, used = 0, set()

    def group_dir(name):
        base, n = _slug(name), 1
        cand = base
        while cand in used:
            n += 1
            cand = f"{base}-{n}"
        used.add(cand)
        d = os.path.join(farm, cand)
        os.makedirs(d, exist_ok=True)
        return d

    for src in sources:
        path = os.path.realpath(src["path"])
        if src["kind"] == "ollama":
            for m in _ollama_models(path):
                d = group_dir(f"ollama-{m['name']}")
                os.symlink(m["blob"], os.path.join(d, _slug(m["name"]) + ".gguf"))
                linked += 1
        else:
            groups = {}
            for p, _ in _gguf_files(path):
                groups.setdefault(os.path.dirname(p), []).append(p)
            for folder, files in groups.items():
                d = group_dir(f"{src['label']}--{os.path.relpath(folder, path)}")
                models = [p for p in files if not _is_projector(p)]
                if accept and models and not any(accept(p) for p in models):
                    continue                          # nothing in this folder that the engine can run
                for p in files:                       # projector files travel with their model
                    if accept and not _is_projector(p) and not accept(p):
                        continue
                    os.symlink(p, os.path.join(d, os.path.basename(p)))
                    linked += 0 if _is_projector(p) else 1
    return linked


def apply_to_lemonade(settings):
    """Push Shoggoth's model-folder choices into Lemonade. Returns (ok, message).

    Once a library folder is chosen it decides everything (see library.apply_to_lemonade); before that, the older choices below apply."""
    import engine
    import library
    if library.root():
        if not engine.running():                               # a Lemonade you run yourself is never re-pointed
            return True, "Your own Lemonade keeps its own folders."
        return library.apply_to_lemonade()
    m = settings["models"]
    storage = "auto" if m.get("storage") != "custom" or not m.get("custom_dir") else m["custom_dir"]
    ok, msg = lem.set_config({"models_dir": storage})
    if not ok:
        return False, msg
    n = sync_farm(m.get("imports") or [])
    ok, msg = lem.set_config({"extra_models_dir": farm_dir() if n else ""})
    return ok, msg


def set_storage(settings, kind, path):
    if kind == "hf":
        settings["models"].update(storage="hf", custom_dir="")
    elif kind == "custom":
        raw = os.path.expanduser(path or "")
        if not raw or not os.path.isabs(raw):
            raise ValueError("Give the full path of a folder, starting with /")
        p = os.path.normpath(raw)
        os.makedirs(p, exist_ok=True)
        probe = os.path.join(p, f".zest-write-test-{int(time.time())}")
        try:
            open(probe, "w").close()
            os.remove(probe)
        except OSError:
            raise ValueError("Shoggoth can't write to that folder.")
        settings["models"].update(storage="custom", custom_dir=p)
    else:
        raise ValueError("Unknown storage choice.")


def list_dir(path):
    p = os.path.realpath(os.path.expanduser(path or "~"))
    if not os.path.isdir(p):
        raise ValueError("That isn't a folder.")
    dirs, gguf = [], 0
    try:
        with os.scandir(p) as it:
            for e in sorted(it, key=lambda x: x.name.lower()):
                if e.is_dir(follow_symlinks=False) and not e.name.startswith("."):
                    dirs.append(e.name)
                elif e.is_file() and e.name.lower().endswith(".gguf"):
                    gguf += 1
    except PermissionError:
        raise ValueError("Shoggoth isn't allowed to read that folder.")
    return {"path": p, "parent": os.path.dirname(p) if p != "/" else None, "dirs": dirs[:600], "gguf": gguf,
            "home": os.path.expanduser("~")}
