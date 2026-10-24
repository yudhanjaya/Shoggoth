"""Skills: instructions a model loads when a job calls for them (the open Agent Skills format, a folder with a SKILL.md).

Zest ships a few starter sets inside the app, and installs more from a link to a GitHub repository. Pi (the agent) is
pointed at one folder of the skills you have switched on, rebuilt every time a task starts. Standard library only.
"""
import io
import json
import os
import re
import shutil
import tarfile
import threading
import urllib.error
import urllib.request

import store

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLED = os.path.join(HERE, "skills")
MAX_DOWNLOAD = 40 * 1024 * 1024
MAX_FILES = 3000
MAX_SKILL_BYTES = 6 * 1024 * 1024
SKIP_DIRS = {".git", ".github", "node_modules", "tests", "test", "__pycache__", "evals", "corpus"}
_lock = threading.RLock()


def root():
    return os.path.join(store.data_dir(), "skills")


def _state_path():
    return os.path.join(root(), "state.json")


def _state():
    try:
        with open(_state_path()) as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    s.setdefault("disabled", [])
    s.setdefault("sets", {})
    return s


def _save(s):
    os.makedirs(root(), exist_ok=True)
    tmp = _state_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=2)
    os.replace(tmp, _state_path())


# ---------------------------------------------------------------- reading skill folders
def frontmatter(path):
    """name and description from a SKILL.md, tolerant of quotes and folded (`>`/`|`) values."""
    try:
        with open(path, encoding="utf8", errors="replace") as f:
            text = f.read(20000)
    except OSError:
        return {}
    m = re.match(r"---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    out, key, buf = {}, None, []
    for line in m.group(1).split("\n"):
        k = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", line)
        if k and not line.startswith(" "):
            if key:
                out[key] = " ".join(buf).strip()
            key, val = k.group(1), k.group(2).strip()
            buf = [] if val in (">", "|", ">-", "|-") else [val]
        elif key and line.startswith((" ", "\t")):
            buf.append(line.strip())
    if key:
        out[key] = " ".join(buf).strip()
    for k, v in out.items():
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            out[k] = v[1:-1]
    return out


def _skills_in(base):
    """[(skill folder name, absolute folder)] for every folder under `base` that holds a SKILL.md."""
    found = []
    for dirpath, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        if "SKILL.md" in files:
            found.append((os.path.basename(dirpath), dirpath))
            dirs[:] = []                                  # a skill's own subfolders are not skills
    return sorted(found)


def _du(path):
    total = 0
    for dp, _, fs in os.walk(path):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


def _set_dirs():
    """[(set id, folder, bundled?)]"""
    out = []
    if os.path.isdir(BUNDLED):
        out += [(d, os.path.join(BUNDLED, d), True) for d in sorted(os.listdir(BUNDLED)) if os.path.isdir(os.path.join(BUNDLED, d))]
    inst = os.path.join(root(), "sets")
    if os.path.isdir(inst):
        out += [(d, os.path.join(inst, d), False) for d in sorted(os.listdir(inst)) if os.path.isdir(os.path.join(inst, d))]
    return out


def _meta(folder):
    try:
        with open(os.path.join(folder, "set.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def scan():
    """Every set and its skills, with whether each is switched on."""
    st = _state()
    off = set(st["disabled"])
    sets = []
    for sid, folder, bundled in _set_dirs():
        meta = _meta(folder) if bundled else dict(st["sets"].get(sid, {}))
        skills = []
        for name, path in _skills_in(folder):
            fm = frontmatter(os.path.join(path, "SKILL.md"))
            key = "%s/%s" % (sid, name)
            skills.append({"key": key, "name": fm.get("name") or name, "dir": name, "description": fm.get("description", ""),
                           "enabled": key not in off, "bytes": _du(path),
                           "auto": "disable-model-invocation" not in fm or str(fm.get("disable-model-invocation")).lower() != "true"})
        sets.append({"id": sid, "name": meta.get("name") or sid, "blurb": meta.get("blurb", ""), "source": meta.get("source", ""),
                     "license": meta.get("license", ""), "author": meta.get("author", ""), "note": meta.get("note", ""),
                     "bundled": bundled, "skills": skills})
    return sets


def toggle(key, enabled):
    with _lock:
        st = _state()
        off = set(st["disabled"])
        (off.discard if enabled else off.add)(key)
        st["disabled"] = sorted(off)
        _save(st)


def toggle_set(sid, enabled):
    for s in scan():
        if s["id"] == sid:
            for k in s["skills"]:
                toggle(k["key"], enabled)


# ---------------------------------------------------------------- what Pi gets
def live_dir():
    """A folder of links to the enabled skills; Pi loads skills from here and nowhere else."""
    d = os.path.join(store.data_dir(), "cowork", "skills-live")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    count = 0
    for sid, folder, _ in _set_dirs():
        off = set(_state()["disabled"])
        for name, path in _skills_in(folder):
            if "%s/%s" % (sid, name) in off:
                continue
            os.symlink(path, os.path.join(d, "%s__%s" % (sid, name)))
            count += 1
    return d, count


def bootstrap():
    """[{name, path}] of skills a bundled set wants loaded into every conversation (Superpowers' entry skill)."""
    out = []
    off = set(_state()["disabled"])
    for sid, folder, bundled in _set_dirs():
        want = _meta(folder).get("bootstrap") if bundled else None
        if want and "%s/%s" % (sid, want) not in off:
            p = os.path.join(folder, "skills", want, "SKILL.md")
            if os.path.isfile(p):
                out.append({"name": want, "path": p})
    return out


def inline():
    """Skills small enough to hand to a plain chat (no tools, so it cannot open skill files itself): the bundled ones that ask for it."""
    out = []
    off = set(_state()["disabled"])
    for sid, folder, bundled in _set_dirs():
        for want in (_meta(folder).get("inline") or []) if bundled else []:
            p = os.path.join(folder, "skills", want, "SKILL.md")
            if "%s/%s" % (sid, want) not in off and os.path.isfile(p):
                out.append({"name": want, "path": p})
    return out


# ---------------------------------------------------------------- installing from a link
def parse_link(link):
    """Accepts github.com URLs (repo, /tree/<ref>/<folder>), git:github.com/o/r, git@github.com:o/r.git and owner/repo.
    Returns (owner, repo, ref or None, subfolder)."""
    s = str(link or "").strip()
    s = re.sub(r"^git:", "", s)
    s = re.sub(r"^git@github\.com:", "https://github.com/", s)
    s = re.sub(r"^(https?://)?(www\.)?github\.com/", "", s)
    s = re.sub(r"^//", "", s)
    if re.match(r"^[\w.-]+/[\w.-]+(/.*)?$", s) is None or "://" in s or s.startswith(("http", "npm:")):
        raise ValueError("Paste a link to a GitHub repository, like https://github.com/obra/superpowers")
    s = re.sub(r"\.git(?=$|[/#?])", "", s.split("#")[0].split("?")[0])
    parts = [p for p in s.split("/") if p]
    owner, repo, rest = parts[0], parts[1], parts[2:]
    ref, sub = None, ""
    if len(rest) >= 2 and rest[0] in ("tree", "blob"):
        ref, sub = rest[1], "/".join(rest[2:])
        if sub.endswith("SKILL.md"):
            sub = sub[: -len("SKILL.md")].rstrip("/")
    if not re.fullmatch(r"[\w.-]+", owner) or not re.fullmatch(r"[\w.-]+", repo):
        raise ValueError("That doesn't look like a GitHub repository link.")
    return owner, repo, ref, sub


def _download(owner, repo, ref):
    url = "https://codeload.github.com/%s/%s/tar.gz/%s" % (owner, repo, ref or "HEAD")
    req = urllib.request.Request(url, headers={"User-Agent": "Zest"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            data = r.read(MAX_DOWNLOAD + 1)
    except urllib.error.HTTPError as e:
        raise ValueError("GitHub says that repository or branch wasn't found." if e.code == 404 else "GitHub refused the download (%s)." % e.code)
    except OSError as e:
        raise ValueError("Couldn't reach GitHub: %s" % e)
    if len(data) > MAX_DOWNLOAD:
        raise ValueError("That repository is too large to install as skills (over 40 MB).")
    return data


def _extract(data, dest):
    """Unpack a tarball safely: plain files and folders only, nothing outside `dest`, and a limit on how many."""
    n = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for m in tf:
            parts = [p for p in m.name.split("/")[1:] if p]       # drop the top folder GitHub adds
            if not parts or any(p in ("..", ".") for p in parts) or os.path.isabs(m.name):
                continue
            if any(p in SKIP_DIRS or p.startswith(".git") for p in parts[:-1]):
                continue
            target = os.path.join(dest, *parts)
            if m.isdir():
                os.makedirs(target, exist_ok=True)
            elif m.isfile() and m.size <= MAX_SKILL_BYTES:
                n += 1
                if n > MAX_FILES:
                    raise ValueError("That repository has too many files to be a skills set.")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with tf.extractfile(m) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                if m.mode & 0o111:
                    os.chmod(target, 0o755)


def install(link):
    owner, repo, ref, sub = parse_link(link)
    sid = re.sub(r"[^\w.-]", "-", "%s-%s" % (owner, repo)).lower()
    with _lock:
        tmp = os.path.join(root(), "tmp-" + sid)
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp, exist_ok=True)
        try:
            _extract(_download(owner, repo, ref), tmp)
            base = os.path.join(tmp, *[p for p in sub.split("/") if p]) if sub else tmp
            if not os.path.realpath(base).startswith(os.path.realpath(tmp)) or not os.path.isdir(base):
                raise ValueError("That folder isn't in the repository.")
            found = _skills_in(base)
            if not found:
                raise ValueError("No skills found there. A skill is a folder with a SKILL.md file in it.")
            final = os.path.join(root(), "sets", sid)
            shutil.rmtree(final, ignore_errors=True)
            os.makedirs(final, exist_ok=True)
            names = []
            for name, path in found:
                if _du(path) > MAX_SKILL_BYTES:
                    continue
                dst = os.path.join(final, name)
                if os.path.exists(dst):
                    dst = os.path.join(final, "%s-%d" % (name, len(names)))
                shutil.copytree(path, dst, symlinks=False)
                names.append(name)
            lic = next((f for f in os.listdir(base) if f.upper().startswith("LICEN")), None)
            if lic and os.path.isfile(os.path.join(base, lic)):
                shutil.copy(os.path.join(base, lic), os.path.join(final, "LICENSE"))
            st = _state()
            st["sets"][sid] = {"name": "%s/%s" % (owner, repo), "source": "https://github.com/%s/%s" % (owner, repo) + ("/tree/%s/%s" % (ref or "HEAD", sub) if sub else ""),
                               "blurb": "Installed from a link."}
            _save(st)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return {"set": sid, "count": len(names), "names": names}


def remove(sid):
    folder = os.path.join(root(), "sets", sid)
    if not re.fullmatch(r"[\w.-]+", sid) or not os.path.isdir(folder):
        raise ValueError("That set isn't installed.")
    with _lock:
        shutil.rmtree(folder, ignore_errors=True)
        st = _state()
        st["sets"].pop(sid, None)
        st["disabled"] = [k for k in st["disabled"] if not k.startswith(sid + "/")]
        _save(st)
