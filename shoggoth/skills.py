"""Skills: instructions a model loads when a job calls for them (the open Agent Skills format, a folder with a SKILL.md).

Shoggoth ships a few starter sets inside the app, and installs more from a link to a GitHub repository. Pi (the agent) is
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

import pipkgs
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
    s.setdefault("enabled", [])             # skills a set ships switched off ("off" in its set.json) that the person turned on
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


MINE = "mine"
OWN = "own"                                   # skills Pi wrote with save_skill, after the person said yes
on_changed = []                               # called with no arguments after a skill is saved by a model (agent.py adds restart_idle)


def mine_dir():
    """Skills you wrote or copied in yourself: any folder holding a SKILL.md, no set.json needed."""
    return os.path.join(root(), MINE)


def _adopt_strays():
    """A skill dropped straight into the folder Pi reads from (skills-live) used to be deleted on the next task.
    Move it into My skills instead, where it is kept, listed and can be switched off."""
    live = os.path.join(store.data_dir(), "cowork", "skills-live")
    if not os.path.isdir(live):
        return
    for entry in os.listdir(live):
        src = os.path.join(live, entry)
        if os.path.islink(src) or not os.path.isfile(os.path.join(src, "SKILL.md")):
            continue
        dst = os.path.join(mine_dir(), entry)
        if os.path.exists(dst):
            continue
        try:
            os.makedirs(mine_dir(), exist_ok=True)
            shutil.move(src, dst)
        except OSError:
            pass


def own_dir():
    """The set of skills your models wrote. Pi's save_skill tool writes one folder per skill into own_skills_dir()."""
    return os.path.join(root(), "sets", OWN)


def own_skills_dir():
    return os.path.join(own_dir(), "skills")


def _package_sets():
    """[(set id, skills folder, package name)] for each Pi package that is switched on: the skills it ships, so they are listed
    and handed to Pi like any other. A package can say where its skills are in package.json ("pi": {"skills": [...]}); otherwise
    it is a skills/ folder."""
    out = []
    for p in pipkgs.active():
        base = "pkg-" + re.sub(r"[^a-z0-9.-]+", "-", p["name"].lower()).strip("-")
        for i, d in enumerate(pipkgs.skill_dirs(p["dir"])):
            out.append((base if i == 0 else "%s-%d" % (base, i + 1), d, p["name"]))
    return out


def _set_dirs():
    """[(set id, folder, bundled?)]"""
    out = []
    if os.path.isdir(BUNDLED):
        out += [(d, os.path.join(BUNDLED, d), True) for d in sorted(os.listdir(BUNDLED)) if os.path.isdir(os.path.join(BUNDLED, d))]
    inst = os.path.join(root(), "sets")
    if os.path.isdir(inst):
        out += [(d, os.path.join(inst, d), False) for d in sorted(os.listdir(inst)) if d not in (MINE, OWN) and os.path.isdir(os.path.join(inst, d))]
    out += [(sid, d, False) for sid, d, _ in _package_sets()]
    os.makedirs(mine_dir(), exist_ok=True)
    os.makedirs(own_skills_dir(), exist_ok=True)
    out.append((OWN, own_dir(), False))
    out.append((MINE, mine_dir(), False))
    return out


def _off(st):
    """Keys that are switched off: the ones the person switched off, and the ones a bundled set ships off until they turn them on."""
    out = set(st["disabled"])
    for sid, folder, bundled in _set_dirs():
        if bundled:
            out |= {"%s/%s" % (sid, n) for n in (_meta(folder).get("off") or [])} - set(st["enabled"])
    return out


def _placeholder(skill_md):
    """A slot waiting for its text (its SKILL.md says so). It is listed but never handed to a model."""
    try:
        with open(skill_md, encoding="utf8", errors="replace") as f:
            return "placeholder: the user writes this" in f.read(3000).lower()
    except OSError:
        return False


def _meta(folder):
    try:
        with open(os.path.join(folder, "set.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def scan():
    """Every set and its skills, with whether each is switched on."""
    st = _state()
    off = _off(st)
    sets = []
    _adopt_strays()
    pkg_names = {sid: name for sid, _, name in _package_sets()}
    for sid, folder, bundled in _set_dirs():
        meta = _meta(folder) if bundled else dict(st["sets"].get(sid, {}))
        if sid == MINE:
            meta = {"name": "My skills", "blurb": "Skills you made or copied in yourself. Put a folder with a SKILL.md file in %s and it appears here." % folder}
        elif sid == OWN:
            meta = {"name": "Written by your models", "blurb": "Skills a model proposed and you said yes to. Each one is only saved after you read it."}
        elif sid in pkg_names:
            meta = {"name": pkg_names[sid], "blurb": "Comes with the %s tool package. Turn the package off and these go with it." % pkg_names[sid],
                    "note": "Runs code on your computer."}
        skills = []
        for name, path in _skills_in(folder):
            fm = frontmatter(os.path.join(path, "SKILL.md"))
            key = "%s/%s" % (sid, name)
            item = {"key": key, "name": fm.get("name") or name, "dir": name, "description": fm.get("description", ""),
                    "enabled": key not in off, "bytes": _du(path), "placeholder": _placeholder(os.path.join(path, "SKILL.md")),
                    "auto": "disable-model-invocation" not in fm or str(fm.get("disable-model-invocation")).lower() != "true"}
            if sid == OWN:
                item["from"] = _source(path)
            skills.append(item)
        sets.append({"id": sid, "name": meta.get("name") or sid, "blurb": meta.get("blurb", ""), "source": meta.get("source", ""),
                     "license": meta.get("license", ""), "author": meta.get("author", ""), "note": meta.get("note", ""),
                     "bundled": bundled, "mine": sid == MINE, "own": sid == OWN, "package": pkg_names.get(sid, ""), "skills": skills})
    return sets


def _source(path):
    """Who wrote a skill in the own set: {chat, model, date} from its source.json (empty when it has none)."""
    try:
        with open(os.path.join(path, "source.json")) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    return {k: str(d.get(k) or "") for k in ("chat", "model", "date")} if isinstance(d, dict) else {}


def saved(name):
    """Called when Pi reports that save_skill finished. The folder on disk decides, not what Pi's message says: if a skill by that
    name is there with its source.json, switch it on or off as the person chose (turn_on in source.json) and tell the listeners
    (idle tasks restart so they pick it up). Returns True when a skill was found."""
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
        return False
    path = os.path.join(own_skills_dir(), name)
    try:
        with open(os.path.join(path, "source.json")) as f:
            turn_on = json.load(f).get("turn_on") is True
    except (OSError, ValueError, AttributeError):
        return False
    if not os.path.isfile(os.path.join(path, "SKILL.md")):
        return False
    toggle("%s/%s" % (OWN, name), turn_on)
    for cb in list(on_changed):
        try:
            cb()
        except Exception:
            pass
    return True


def toggle(key, enabled):
    with _lock:
        st = _state()
        off, on = set(st["disabled"]), set(st["enabled"])
        (off.discard if enabled else off.add)(key)
        (on.add if enabled else on.discard)(key)
        st["disabled"], st["enabled"] = sorted(off), sorted(on)
        _save(st)


def toggle_set(sid, enabled):
    for s in scan():
        if s["id"] == sid:
            for k in s["skills"]:
                toggle(k["key"], enabled)


# ---------------------------------------------------------------- what Pi gets
def live_dir():
    """A folder of links to the enabled skills; Pi loads skills from here and nowhere else.

    Updated in place, never emptied and rebuilt: other tasks (a job running in the background, say) may be reading
    skills from this folder while a new task starts."""
    d = os.path.join(store.data_dir(), "cowork", "skills-live")
    os.makedirs(d, exist_ok=True)
    _adopt_strays()
    off = _off(_state())
    want = {}
    for sid, folder, _ in _set_dirs():
        for name, path in _skills_in(folder):
            if "%s/%s" % (sid, name) not in off and not _placeholder(os.path.join(path, "SKILL.md")):
                want["%s__%s" % (sid, name)] = path
    for entry in os.listdir(d):
        link = os.path.join(d, entry)
        if os.path.isdir(link) and not os.path.islink(link):
            continue                                      # never delete a real folder; strays holding a skill were adopted above
        if entry not in want or not os.path.islink(link) or os.readlink(link) != want[entry]:
            try:
                os.remove(link)
            except OSError:
                pass
    for entry, path in want.items():
        link = os.path.join(d, entry)
        if not os.path.lexists(link):
            os.symlink(path, link)
    return d, len(want)


def bootstrap():
    """[{name, path}] of skills a bundled set wants loaded into every conversation (Superpowers' entry skill)."""
    out = []
    off = _off(_state())
    for sid, folder, bundled in _set_dirs():
        want = _meta(folder).get("bootstrap") if bundled else None
        if want and "%s/%s" % (sid, want) not in off:
            p = os.path.join(folder, "skills", want, "SKILL.md")
            if os.path.isfile(p) and not _placeholder(p):
                out.append({"name": want, "path": p})
    return out


def inline():
    """Skills small enough to hand to a plain chat (no tools, so it cannot open skill files itself): the bundled ones that ask for it."""
    out = []
    off = _off(_state())
    for sid, folder, bundled in _set_dirs():
        for want in (_meta(folder).get("inline") or []) if bundled else []:
            p = os.path.join(folder, "skills", want, "SKILL.md")
            if "%s/%s" % (sid, want) not in off and os.path.isfile(p) and not _placeholder(p):
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
    req = urllib.request.Request(url, headers={"User-Agent": "Shoggoth"})
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
    if sid == MINE:
        raise ValueError("That is your own skills folder. Delete the skill folders you don't want from it.")
    if sid == OWN:
        raise ValueError("Those are the skills your models wrote. Delete the ones you don't want from the list.")
    folder = os.path.join(root(), "sets", sid)
    if not re.fullmatch(r"[\w.-]+", sid) or sid.startswith(".") or not os.path.isdir(folder):
        raise ValueError("That set isn't installed.")
    with _lock:
        shutil.rmtree(folder, ignore_errors=True)
        st = _state()
        st["sets"].pop(sid, None)
        st["disabled"] = [k for k in st["disabled"] if not k.startswith(sid + "/")]
        _save(st)
