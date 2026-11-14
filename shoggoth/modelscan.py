"""Where models live: finding the ones you already have (LM Studio, Ollama, the Hugging Face cache and so on).

Folders the person brings in are read by the library (library.py), which shows llama.cpp the files it can run through its view
folder of links, so nothing is copied or moved.
"""
import hashlib
import json
import os
import re
import time

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
    """The Hugging Face hub cache (where 4.x downloaded models by default)."""
    if os.environ.get("HF_HUB_CACHE"):
        return os.environ["HF_HUB_CACHE"]
    if os.environ.get("HF_HOME"):
        return os.path.join(os.environ["HF_HOME"], "hub")
    return _home(".cache", "huggingface", "hub")


def farm_dir():
    """The folder of links 4.x made for Lemonade. Never written now; skipped when scanning so its links aren't counted twice."""
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
