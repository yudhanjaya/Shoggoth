"""Work copies: a job can work on a copy of a folder and hand back one reviewable set of changes.

make() copies the folder (leaving out dependency and build folders), keeps a pristine copy of small files to diff
against, and records a signature of every file. changes() and diff() compare the copy with that record. apply() copies
the changes back into the real folder, refusing any file the person edited in the meantime. Standard library only.
"""
import difflib
import hashlib
import json
import os
import shutil

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "env", ".mypy_cache", ".pytest_cache", ".ruff_cache",
             "target", "dist", "build", ".cache", ".next", ".nuxt", ".gradle", ".idea", ".tox", ".eggs", ".svelte-kit"}
MAX_BYTES = 300 * 1024 * 1024
MAX_FILES = 30000
BASE_LIMIT = 1024 * 1024        # files up to this size are kept pristine so they can be diffed
HASH_LIMIT = 8 * 1024 * 1024    # bigger files are compared by size and modification time
GIT_LIMIT = 50 * 1024 * 1024    # the .git folder is copied only when it is small
DIFF_LINES = 1500


def _files(src):
    """(relative path, absolute path, stat) for every file a copy includes: no symlinks, no skipped folders, no .git."""
    for root, dirs, names in os.walk(src, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not os.path.islink(os.path.join(root, d)))
        for n in sorted(names):
            p = os.path.join(root, n)
            if os.path.islink(p):
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            yield os.path.relpath(p, src), p, st


def _sig(path, st):
    if st.st_size > HASH_LIMIT:
        return "%d:%d" % (st.st_size, st.st_mtime_ns)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def survey(src):
    """What a copy of `src` would contain and leave out, without copying anything."""
    files = size = 0
    for _, _, st in _files(src):
        files += 1
        size += st.st_size
        if files > MAX_FILES * 2:
            break
    left = sorted(d for d in SKIP_DIRS if d != ".git" and os.path.isdir(os.path.join(src, d)))
    return {"git": os.path.isdir(os.path.join(src, ".git")), "files": files, "bytes": size, "left_out": left,
            "too_big": files > MAX_FILES or size > MAX_BYTES}


def make(src, root):
    """Copy `src` into root/work, with root/base holding pristine small files and root/manifest.json the signatures."""
    s = survey(src)
    if s["too_big"]:
        raise ValueError("That folder is too big to copy (over %d files or %d MB). Let the job work in the folder itself instead."
                         % (MAX_FILES, MAX_BYTES // (1024 * 1024)))
    work, base = os.path.join(root, "work"), os.path.join(root, "base")
    os.makedirs(work, exist_ok=True)
    os.makedirs(base, exist_ok=True)
    manifest = {}
    for rel, p, st in _files(src):
        for dst in ([work, base] if st.st_size <= BASE_LIMIT else [work]):
            d = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(p, d)
        manifest[rel] = _sig(p, st)
    git = os.path.join(src, ".git")
    if os.path.isdir(git):
        size = sum(os.path.getsize(os.path.join(r, n)) for r, _, ns in os.walk(git) for n in ns if os.path.exists(os.path.join(r, n)))
        if size <= GIT_LIMIT:
            shutil.copytree(git, os.path.join(work, ".git"), symlinks=True, dirs_exist_ok=True)
    _save_manifest(root, manifest)
    return work


def snapshot(src, root):
    """Record `src` as it is now (pristine small files plus signatures) without copying it, so a job working in the
    folder itself can still show what it changed. Returns False when the folder is too big to record."""
    if survey(src)["too_big"]:
        return False
    base = os.path.join(root, "base")
    os.makedirs(base, exist_ok=True)
    manifest = {}
    for rel, p, st in _files(src):
        if st.st_size <= BASE_LIMIT:
            d = os.path.join(base, rel)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(p, d)
        manifest[rel] = _sig(p, st)
    _save_manifest(root, manifest)
    return True


def _save_manifest(root, m):
    tmp = os.path.join(root, "manifest.json.tmp")
    with open(tmp, "w") as f:
        json.dump(m, f)
    os.replace(tmp, os.path.join(root, "manifest.json"))


def _manifest(root):
    with open(os.path.join(root, "manifest.json")) as f:
        return json.load(f)


def _read_text(path):
    try:
        with open(path, "rb") as f:
            raw = f.read(BASE_LIMIT + 1)
    except OSError:
        return None
    if len(raw) > BASE_LIMIT or b"\0" in raw[:8192]:
        return None
    return raw.decode("utf8", "replace")


def _counts(old, new):
    add = dele = 0
    for line in difflib.unified_diff((old or "").splitlines(), (new or "").splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            add += 1
        elif line.startswith("-") and not line.startswith("---"):
            dele += 1
    return add, dele


def changes(root, work=None):
    """What differs between the copy and the folder as it was when copied: [{path, status A|M|D, add, del}]."""
    work, base = work or os.path.join(root, "work"), os.path.join(root, "base")
    m = _manifest(root)
    now = {rel: (p, st) for rel, p, st in _files(work)}
    out = []
    for rel, (p, st) in now.items():
        if rel not in m:
            new = _read_text(p)
            a, d = _counts("", new) if new is not None else (0, 0)
            out.append({"path": rel, "status": "A", "add": a, "del": d, "binary": new is None})
        elif _sig(p, st) != m[rel]:
            new, old = _read_text(p), _read_text(os.path.join(base, rel))
            a, d = _counts(old, new) if (new is not None and old is not None) else (0, 0)
            out.append({"path": rel, "status": "M", "add": a, "del": d, "binary": new is None or old is None})
    for rel in m:
        if rel not in now:
            old = _read_text(os.path.join(base, rel))
            out.append({"path": rel, "status": "D", "add": 0, "del": len((old or "").splitlines()), "binary": old is None})
    out.sort(key=lambda c: c["path"])
    return out


def diff(root, rel, work=None):
    """Unified diff text for one changed file (without the ---/+++ header lines)."""
    work, base = work or os.path.join(root, "work"), os.path.join(root, "base")
    if not rel or os.path.isabs(rel) or ".." in rel.replace("\\", "/").split("/"):
        raise ValueError("bad path")
    wp, bp = os.path.join(work, rel), os.path.join(base, rel)
    if os.path.realpath(wp) != os.path.join(os.path.realpath(work), rel):
        raise ValueError("bad path")
    new = _read_text(wp) if os.path.exists(wp) else ""
    old = _read_text(bp) if os.path.exists(bp) else ""
    if new is None or old is None:
        return {"text": "This file is binary or too large to show a diff.", "truncated": False}
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=3))[2:]
    cut = len(lines) > DIFF_LINES
    return {"text": "\n".join(lines[:DIFF_LINES]), "truncated": cut}


def apply(root, src, only=None):
    """Copy the copy's changes into `src`. A file whose original changed since the copy was made is skipped and reported."""
    work = os.path.join(root, "work")
    m = _manifest(root)
    applied, conflicts = [], []
    for c in changes(root):
        rel = c["path"]
        if only is not None and rel not in only:
            continue
        target = os.path.join(src, rel)
        exists = os.path.exists(target) and not os.path.islink(target)
        if c["status"] == "A":
            if exists and _sig(target, os.stat(target)) != _sig(os.path.join(work, rel), os.stat(os.path.join(work, rel))):
                conflicts.append({"path": rel, "reason": "A different file with this name is already in your folder."})
                continue
        else:
            if not exists or _sig(target, os.stat(target)) != m.get(rel):
                conflicts.append({"path": rel, "reason": "You changed this file in your folder after the copy was made."})
                continue
        if not os.path.realpath(os.path.dirname(target)).startswith(os.path.realpath(src)):
            conflicts.append({"path": rel, "reason": "That path leaves your folder."})
            continue
        if c["status"] == "D":
            os.remove(target)
            m.pop(rel, None)
        else:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(os.path.join(work, rel), target)
            m[rel] = _sig(os.path.join(work, rel), os.stat(os.path.join(work, rel)))
            bd = os.path.join(root, "base", rel)
            if os.path.getsize(os.path.join(work, rel)) <= BASE_LIMIT:
                os.makedirs(os.path.dirname(bd), exist_ok=True)
                shutil.copy2(os.path.join(work, rel), bd)
        applied.append(rel)
    _save_manifest(root, m)
    return {"applied": applied, "conflicts": conflicts}


def remove(root):
    shutil.rmtree(root, ignore_errors=True)
