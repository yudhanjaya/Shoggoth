"""Talks to Hugging Face: looks a model up, searches, lists its GGUF files and downloads them.

Downloads are done here (Range resume into <file>.part, sha256 checked as the bytes arrive), no other program involved.
Only public files; no login. Standard library only.
"""
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import gguf

BASE = "https://huggingface.co"
API = BASE + "/api/models/"
VERSION = "dev"          # the server sets this to its own version so the User-Agent says Shoggoth/<version>
TIMEOUT = 20             # seconds, for every request (also the longest a download may sit silent)
RETRIES = 3              # a dropped download connection is picked up again this many times before giving up
CACHE_SECONDS = 600
ALLOWED_UPLOADERS = ("unsloth", "bartowski", "ggml-org", "lmstudio-community")
_REPO = re.compile(r"^[A-Za-z0-9][\w.\-]{0,95}/[A-Za-z0-9][\w.\-]{0,95}$")
# Quantisation names as they appear in GGUF file names: Q4_K_M, IQ3_XXS, Q8_0, BF16, F16, MXFP4 ...
_QUANT = re.compile(r"(?<![A-Za-z0-9])((?:UD-)?(?:IQ\d(?:_[A-Z]+)*|Q\d(?:_[A-Z0-9]+)*|BF16|F16|F32|MXFP4(?:_MOE)?))(?![A-Za-z0-9])", re.I)
_SPLIT = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.I)


class HFError(Exception):
    """Something went wrong talking to Hugging Face. str(e) is plain English, fit to show the user."""
    def __init__(self, message, cancelled=False, final=False):
        Exception.__init__(self, message)
        self.cancelled = cancelled
        self.final = final          # true: retrying won't help


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
        raise ValueError("This model is gated: you have to accept its licence on Hugging Face first. Shoggoth can't log in for you yet.")
    vs, vision = versions(info.get("siblings"))
    return {"repo": repo, "wanted": quant, "versions": vs, "vision": bool(vision), "mmproj": vision or None, "likes": info.get("likes"),
            "downloads": info.get("downloads"), "tags": (info.get("tags") or [])[:12], "pipeline": info.get("pipeline_tag"),
            "name": repo.split("/", 1)[1]}


def local_name(repo):
    """The id Lemonade will know a downloaded model by. User models are namespaced 'user.'."""
    return "user." + re.sub(r"[^A-Za-z0-9._\-]", "-", repo.split("/", 1)[1])


# ---- Shoggoth 5: file list, download, search ----

def _open(url, headers=None):
    h = {"User-Agent": "Shoggoth/" + VERSION}     # urllib keeps these headers (Range too) across a redirect
    h.update(headers or {})
    try:
        return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        e.close()
        if e.code in (401, 403):
            raise HFError("This model needs a Hugging Face login, which Shoggoth can't do yet.", final=True)
        if e.code == 404:
            raise HFError("Hugging Face doesn't have that model or file.", final=True)
        if e.code == 416:
            raise HFError("Hugging Face refused to resume the download (416).")
        if e.code == 429:
            raise HFError("Hugging Face says we are asking too often. Try again in a minute.", final=True)
        raise HFError("Hugging Face answered with an error (%d)." % e.code, final=e.code < 500)
    except (urllib.error.URLError, OSError, ValueError):
        raise HFError("Couldn't reach Hugging Face. Check your internet connection.")


def _get_json(url):
    with _open(url, {"Accept": "application/json"}) as r:
        try:
            return json.loads(r.read())
        except (OSError, ValueError):
            raise HFError("Hugging Face sent something Shoggoth couldn't read.")


def _check_repo(repo):
    if not isinstance(repo, str) or not _REPO.match(repo):
        raise HFError("That doesn't look like a Hugging Face model name (expected owner/name).", final=True)
    return repo


def _safe_name(name):
    parts = name.split("/")
    if name.startswith("/") or any(p in ("", ".", "..") or "\\" in p for p in parts):
        raise HFError("Hugging Face listed a file name Shoggoth won't write to disk: " + name, final=True)
    return name


def model_id(repo, quant):
    """'unsloth/Qwen3-4B-GGUF' + 'Q4_K_M' -> 'Qwen3-4B-Q4_K_M'."""
    name = re.sub(r"[-_.]?gguf$", "", repo.split("/", 1)[1], flags=re.I)
    return name + "-" + str(quant).upper()


def group_files(tree):
    """Tree API items -> ({quant: version}, [mmproj files]). A split model is one version with its shards in order."""
    groups, mmproj = {}, []
    for item in tree or []:
        name = item.get("path") or ""
        if item.get("type") not in (None, "file") or not name.lower().endswith(".gguf"):
            continue
        lfs = item.get("lfs") or {}
        e = {"name": name, "size": int(item.get("size") or lfs.get("size") or 0), "sha256": (lfs.get("oid") or "").lower() or None}
        if "mmproj" in name.lower():
            mmproj.append(e)
            continue
        q = quant_of(name)
        if not q:
            continue
        g = groups.setdefault(q, {"quant": q, "files": [], "bytes": 0})
        g["files"].append(e)
        g["bytes"] += e["size"]
    for g in groups.values():
        g["files"].sort(key=lambda f: gguf.shard_of(f["name"])[:2])
        counts = {gguf.shard_of(f["name"])[2] for f in g["files"]}
        g["split"] = max(counts) > 1
        g["complete"] = len(counts) == 1 and len(g["files"]) == max(counts)     # every shard the names promise is there
        g["gb"] = round(g["bytes"] / 1e9, 2)
    mmproj.sort(key=lambda f: f["name"])
    return groups, mmproj


def files(repo):
    """GGUF files of a repo: {'repo', 'versions': [one per quant, smallest first], 'mmproj': [vision files]}."""
    _check_repo(repo)
    tree = _get_json("%s/api/models/%s/tree/main?recursive=1" % (BASE, repo))
    if not isinstance(tree, list):
        raise HFError("Hugging Face sent something Shoggoth couldn't read.")
    groups, mmproj = group_files(tree)
    return {"repo": repo, "versions": sorted(groups.values(), key=lambda g: g["bytes"]), "mmproj": mmproj}


def _pick_mmproj(listing, want):
    options = listing["mmproj"]
    if not options:
        raise HFError("This repo has no vision (mmproj) file.", final=True)
    if isinstance(want, str):
        for f in options:
            if want in (f["name"], os.path.basename(f["name"])):
                return f
        raise HFError("This repo has no vision file called " + want + ".", final=True)
    for tag in ("f16", "bf16", "f32"):        # the usual choice first, then whatever there is
        for f in options:
            if tag in f["name"].lower():
                return f
    return options[0]


def _select(listing, what):
    """The version asked for by quant ('q4_k_m') or by file name."""
    w = (what or "").strip()
    for v in listing["versions"]:
        if v["quant"].lower() == w.lower() or any(w in (f["name"], os.path.basename(f["name"])) for f in v["files"]):
            if not v["complete"]:
                raise HFError("The %s files in this repo are incomplete (a shard is missing)." % v["quant"], final=True)
            return v
    raise HFError("This repo has no %s version." % (w or "matching"), final=True)


def _hash_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h


def _stream(url, part, have, hs, tick, cancel):
    """Append the rest of the file to part, from byte `have`. hs is a one-item list holding the running hash (replaced on a restart).
    Returns the new size. tick(bytes_in_file) after every block."""
    r = _open(url, {"Range": "bytes=%d-" % have} if have else None)
    with r:
        mode = "ab"
        if have:
            m = re.match(r"bytes (\d+)-", r.headers.get("Content-Range") or "")
            if r.status == 206 and m and int(m.group(1)) == have:
                pass
            elif r.status == 200:            # the server ignored Range and sends everything: start over
                mode, have = "wb", 0
                hs[0] = hashlib.sha256()
            else:
                raise OSError("unexpected range answer")
        with open(part, mode) as f:
            while True:
                if cancel is not None and cancel.is_set():
                    raise HFError("Download cancelled.", cancelled=True, final=True)
                block = r.read(1 << 18)
                if not block:
                    return have
                f.write(block)
                hs[0].update(block)
                have += len(block)
                tick(have)


def _fetch_one(repo, entry, dest, tick, cancel):
    """Download one file to dest through dest.part. tick(bytes so far in this file)."""
    size, want = entry["size"], entry["sha256"]
    url = "%s/%s/resolve/main/%s" % (BASE, repo, urllib.parse.quote(entry["name"], safe="/"))
    part = dest + ".part"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isfile(dest) and (not size or os.path.getsize(dest) == size):
        tick(os.path.getsize(dest))                 # already there from an earlier download
        return
    failures = 0
    while True:
        have = os.path.getsize(part) if os.path.isfile(part) else 0
        if size and have > size:
            os.remove(part)
            have = 0
        hs = [_hash_of(part) if have else hashlib.sha256()]
        tick(have)
        try:
            if not size or have < size:
                have = _stream(url, part, have, hs, tick, cancel)
            if size and have != size:
                raise OSError("cut short")
            break
        except (OSError, HFError) as e:
            if isinstance(e, HFError) and e.final:
                raise
            if isinstance(e, HFError) and "(416)" in str(e) and os.path.isfile(part):
                os.remove(part)                     # the part file doesn't fit the file any more
            failures += 1
            if failures > RETRIES:
                raise HFError("The download keeps getting cut off. Try again; it will carry on from where it stopped.")
    if want and hs[0].hexdigest() != want:
        os.remove(part)
        raise HFError("%s didn't match its checksum, so Shoggoth threw it away. Try again." % os.path.basename(entry["name"]), final=True)
    os.replace(part, dest)


def download(repo, filename_or_quant, dest_dir, progress=None, cancel=None, mmproj=False):
    """Download a model's files into dest_dir/<owner>/<name>/. Resumes, checks sha256, renames on success.

    filename_or_quant: 'Q4_K_M' or a file name. mmproj: True for the repo's best vision file, or a file name.
    progress(done, total) counts bytes over all files. cancel is a threading.Event; the .part files stay for next time.
    Returns {'id', 'repo', 'quant', 'files': [paths in shard order], 'mmproj': path or None}."""
    listing = files(repo)
    ver = _select(listing, filename_or_quant)
    todo = list(ver["files"])
    vision = _pick_mmproj(listing, mmproj) if mmproj else None
    if vision:
        todo.append(vision)
    for e in todo:
        _safe_name(e["name"])
    total = sum(e["size"] for e in todo)
    finished, paths = 0, []
    for e in todo:
        def tick(n, base=finished):
            if progress:
                progress(base + n, total)
        dest = os.path.join(dest_dir, *repo.split("/"), *e["name"].split("/"))
        _fetch_one(repo, e, dest, tick, cancel)
        finished += e["size"]
        paths.append(dest)
    if progress:
        progress(total, total)
    return {"id": model_id(repo, ver["quant"]), "repo": repo, "quant": ver["quant"],
            "files": paths[:len(ver["files"])], "mmproj": paths[-1] if vision else None}


_cache = {}
_cache_lock = threading.Lock()
_now = time.time


def search(q):
    """Models with GGUF files matching q: [{'repo', 'downloads', 'likes', 'trusted'}], trusted uploaders first, then by downloads."""
    q = (q or "").strip()
    if not q:
        return []
    key = q.lower()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and _now() - hit[0] < CACHE_SECONDS:
            return [dict(x) for x in hit[1]]
    data = _get_json(BASE + "/api/models?" + urllib.parse.urlencode({"search": q, "filter": "gguf", "sort": "downloads", "limit": 40}))
    out = []
    for m in data if isinstance(data, list) else []:
        repo = m.get("id") or m.get("modelId") or ""
        if not _REPO.match(repo) or not ("gguf" in (m.get("tags") or []) or m.get("gguf")):
            continue
        out.append({"repo": repo, "downloads": int(m.get("downloads") or 0), "likes": int(m.get("likes") or 0),
                    "trusted": repo.split("/", 1)[0].lower() in ALLOWED_UPLOADERS})
    out.sort(key=lambda x: (not x["trusted"], -x["downloads"]))
    with _cache_lock:
        _cache[key] = (_now(), out)
    return [dict(x) for x in out]
