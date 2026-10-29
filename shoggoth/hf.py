"""Looks up a model on Hugging Face so the Models page can offer "Get from Hugging Face".

Shoggoth only reads the public file list; the download itself is handed to Lemonade (POST /api/v1/pull), which already
knows how to fetch, verify and register a GGUF.
"""
import json
import re
import urllib.error
import urllib.request

API = "https://huggingface.co/api/models/"
_REPO = re.compile(r"^[A-Za-z0-9][\w.\-]{0,95}/[A-Za-z0-9][\w.\-]{0,95}$")
# Quantisation names as they appear in GGUF file names: Q4_K_M, IQ3_XXS, Q8_0, BF16, F16, MXFP4 ...
_QUANT = re.compile(r"(?<![A-Za-z0-9])((?:UD-)?(?:IQ\d(?:_[A-Z]+)*|Q\d(?:_[A-Z0-9]+)*|BF16|F16|F32|MXFP4(?:_MOE)?))(?![A-Za-z0-9])", re.I)
_SPLIT = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.I)


def parse(text):
    """'https://huggingface.co/org/repo/...', 'org/repo' or 'org/repo:Q4_K_M' -> (repo, quant or None)."""
    t = (text or "").strip()
    t = re.sub(r"^https?://(www\.)?huggingface\.co/", "", t, flags=re.I)
    t = re.sub(r"^hf\.co/", "", t, flags=re.I)
    quant = None
    if ":" in t:
        t, quant = t.split(":", 1)
        quant = quant.strip() or None
    parts = [x for x in t.split("/") if x]
    if len(parts) >= 2:
        t = "/".join(parts[:2])
    if not _REPO.match(t):
        raise ValueError("That doesn't look like a Hugging Face model. Paste a link such as huggingface.co/unsloth/Qwen3-4B-GGUF.")
    return t, quant


def quant_of(name):
    base = name.rsplit("/", 1)[-1]
    m = _QUANT.search(base[:-5] if base.lower().endswith(".gguf") else base)
    return m.group(1).upper() if m else None


def versions(siblings):
    """Group GGUF files by quantisation. Split files (-00001-of-00003) count as one version with their combined size."""
    groups, vision = {}, None
    for f in siblings or []:
        name = f.get("rfilename") or ""
        if not name.lower().endswith(".gguf"):
            continue
        if "mmproj" in name.lower():
            vision = vision or name
            continue
        q = quant_of(name)
        if not q:
            continue
        g = groups.setdefault(q, {"quant": q, "files": [], "bytes": 0})
        g["files"].append(name)
        g["bytes"] += int(f.get("size") or (f.get("lfs") or {}).get("size") or 0)
    out = sorted(groups.values(), key=lambda g: g["bytes"])
    for g in out:
        g["gb"] = round(g["bytes"] / 1e9, 2)
        g["split"] = any(_SPLIT.search(n) for n in g["files"])
    return out, vision


def lookup(text, timeout=12):
    repo, quant = parse(text)
    req = urllib.request.Request(API + repo + "?blobs=true", headers={"User-Agent": "Shoggoth", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            info = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ValueError("This model needs a Hugging Face login, which Shoggoth can't do yet.")
        if e.code == 404:
            raise ValueError("Hugging Face doesn't know a model called " + repo + ".")
        raise ValueError(f"Hugging Face answered with an error ({e.code}).")
    except (urllib.error.URLError, OSError, ValueError):
        raise ValueError("Couldn't reach Hugging Face. Check your internet connection.")
    if info.get("gated"):
        raise ValueError("This model is gated: you have to accept its licence on Hugging Face first, and Shoggoth can't log in for you yet.")
    vs, vision = versions(info.get("siblings"))
    return {"repo": repo, "wanted": quant, "versions": vs, "vision": bool(vision), "mmproj": vision or None, "likes": info.get("likes"),
            "downloads": info.get("downloads"), "tags": (info.get("tags") or [])[:12], "pipeline": info.get("pipeline_tag"),
            "name": repo.split("/", 1)[1]}


def local_name(repo):
    """The id Lemonade will know a downloaded model by. User models are namespaced 'user.'."""
    return "user." + re.sub(r"[^A-Za-z0-9._\-]", "-", repo.split("/", 1)[1])
