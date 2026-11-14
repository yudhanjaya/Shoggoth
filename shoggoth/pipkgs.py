"""Curated Pi packages: npm packages that add extensions, skills or prompts to Pi. They run code on this computer, so Shoggoth
installs only the pinned list in recommendations/pi-packages.yaml, checks every download against the sha512 npm publishes, and
never runs a lifecycle script (nothing here calls npm or an install hook: the files are only unpacked). Standard library only.

Each package is unpacked to <data>/pi-packages/<name>@<version>/ with its dependencies in node_modules beside it.
"""
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "_vendor"))
import yaml  # noqa: E402

import piup  # noqa: E402
import store  # noqa: E402

LIST_FILE = os.path.join(HERE, "recommendations", "pi-packages.yaml")
MARK = ".shoggoth-package.json"
MAX_TARBALL = 60 * 1024 * 1024
MAX_UNPACKED = 150 * 1024 * 1024          # per package, its dependencies included
MAX_PACKAGES = 400                         # how many packages one install may pull in
NAME_RE = re.compile(r"^(@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$")
# Pi supplies these to its packages; a second copy in node_modules would split Pi's registries.
HOST_PROVIDED = {"@earendil-works/pi-ai", "@earendil-works/pi-agent-core", "@earendil-works/pi-coding-agent", "@earendil-works/pi-tui", "typebox"}
_lock = threading.RLock()


def root():
    return os.path.join(store.data_dir(), "pi-packages")


def _dirname(name, version):
    return "%s@%s" % (name.replace("/", "__"), version)


def curated():
    """The pinned list: [{name, version, integrity, enabled, notes}]. Entries with a bad name or version are left out."""
    try:
        with open(LIST_FILE, encoding="utf8") as f:
            doc = yaml.safe_load(f.read(200000))
    except (OSError, yaml.YAMLError):
        return []
    out = []
    for e in doc if isinstance(doc, list) else []:
        if not isinstance(e, dict):
            continue
        name, ver, integ = str(e.get("name") or ""), str(e.get("version") or ""), str(e.get("integrity") or "")
        if not NAME_RE.match(name) or (ver and not VERSION_RE.match(ver)) or (integ and not integ.startswith("sha512-")):
            continue
        # An enabled entry must be pinned by a sha512 as well as a version; without one it stays "not tested yet".
        out.append({"name": name, "version": ver, "integrity": integ, "enabled": e.get("enabled") is True and integ.startswith("sha512-"), "notes": str(e.get("notes") or "")})
    return out


# ---------------------------------------------------------------- versions
def _parse(v):
    """([major, minor, patch], prerelease); a missing or x part is None."""
    m = re.match(r"^v?(\d+|[xX*])(?:\.(\d+|[xX*]))?(?:\.(\d+|[xX*]))?(?:-([0-9A-Za-z.-]+))?(?:\+.*)?$", v.strip())
    if not m:
        raise RuntimeError("Can't read the version range '%s'." % v)
    return [None if (g is None or g in "xX*") else int(g) for g in m.groups()[:3]], m.group(4)


def _cmp(a, b):
    """Compare two exact versions; a prerelease sorts before its release."""
    (an, ap), (bn, bp) = _parse(a), _parse(b)
    an, bn = tuple(an), tuple(bn)
    if an != bn:
        return -1 if an < bn else 1
    if ap == bp:
        return 0
    if ap is None or bp is None:
        return 1 if ap is None else -1
    xs, ys = ap.split("."), bp.split(".")
    for x, y in zip(xs, ys):
        if x == y:
            continue
        if x.isdigit() and y.isdigit():
            return -1 if int(x) < int(y) else 1
        if x.isdigit() != y.isdigit():
            return -1 if x.isdigit() else 1
        return -1 if x < y else 1
    return (len(xs) > len(ys)) - (len(xs) < len(ys))


def _comparators(token):
    """One range token (^1.2.3, ~1.2, 1.x, >=1.0.0, 1.2.3) as a list of (op, version) pairs, all of which must hold."""
    m = re.match(r"^(\^|~>?|>=|<=|>|<|=)?(.+)$", token)
    op, (ma, mi, pa), pre = m.group(1) or "", _parse(m.group(2))[0], _parse(m.group(2))[1]
    full = None not in (ma, mi, pa)
    lo = "%d.%d.%d" % (ma or 0, mi or 0, pa or 0) + ("-" + pre if pre else "")
    if ma is None:
        return [] if op in ("", "=", "^", "~", "~>") else ([(">=", "0.0.0")] if op in (">=", "<=") else [("<", "0.0.0")])
    nxt_minor = "%d.%d.0" % (ma, (mi or 0) + 1)
    nxt_major = "%d.0.0" % (ma + 1)
    if op in ("", "="):
        return [("=", lo)] if full else [(">=", lo), ("<", nxt_major if mi is None else nxt_minor)]
    if op == "^":
        if ma > 0 or mi is None:
            hi = nxt_major
        elif mi > 0 or pa is None:
            hi = nxt_minor
        else:
            hi = "0.0.%d" % (pa + 1)
        return [(">=", lo), ("<", hi)]
    if op in ("~", "~>"):
        return [(">=", lo), ("<", nxt_major if mi is None else nxt_minor)]
    if op == ">=":
        return [(">=", lo)]
    if op == "<":
        return [("<", lo)]
    if op == ">":
        return [(">", lo)] if full else [(">=", nxt_major if mi is None else nxt_minor)]
    return [("<=", lo)] if full else [("<", nxt_major if mi is None else nxt_minor)]       # <=


_OPS = {"=": lambda c: c == 0, ">=": lambda c: c >= 0, "<=": lambda c: c <= 0, ">": lambda c: c > 0, "<": lambda c: c < 0}


def satisfies(version, rng):
    """Whether an exact version meets an npm range (^ ~ x-ranges, comparators, hyphen ranges, ||). A prerelease only matches exactly."""
    rng = (rng or "*").strip()
    pre = _parse(version)[1]
    if rng in ("", "*", "latest", "x"):
        return not pre
    for alt in rng.split("||"):
        alt = re.sub(r"^\s*(\S+)\s+-\s+(\S+)\s*$", r">=\1 <=\2", alt.strip())
        alt = re.sub(r"(>=|<=|>|<|=|\^|~>?)\s+", r"\1", alt)
        pairs = [p for tok in alt.split() for p in _comparators(tok)]
        if pre and not any(o == "=" and v == version for o, v in pairs):
            continue
        if all(_OPS[o](_cmp(version, v)) for o, v in pairs):
            return True
    return False


def _pick(versions, rng):
    best = None
    for v in versions:
        if VERSION_RE.match(v) and satisfies(v, rng) and (best is None or _cmp(v, best) > 0):
            best = v
    if best is None:
        raise RuntimeError("No published version fits '%s'." % rng)
    return best


# ---------------------------------------------------------------- registry
def _url(name, *rest):
    return piup.REGISTRY.rstrip("/") + "/" + "/".join([name] + list(rest))


def _meta(name, version):
    meta = piup._get_json(_url(name, version))
    dist = meta.get("dist") or {}
    integ = str(dist.get("integrity") or "")
    if not integ.startswith("sha512-") or not dist.get("tarball"):
        raise RuntimeError("npm didn't publish a checksum for %s, so Shoggoth won't install it." % name)
    if str(meta.get("name")) != name or str(meta.get("version")) != version:
        raise RuntimeError("npm answered with a different package than %s@%s." % (name, version))
    return meta


def _resolve(name, rng):
    """The exact version of `name` that fits `rng`, from the registry."""
    if VERSION_RE.match(rng):
        return rng
    doc = piup._get_json(_url(name))
    return _pick(list((doc.get("versions") or {}).keys()), rng)


def _read_version(d):
    try:
        with open(os.path.join(d, "package.json")) as f:
            return str(json.load(f).get("version") or "")
    except (OSError, ValueError):
        return ""


class _Job:
    """One install: fetches and unpacks packages, counting what it has taken."""

    def __init__(self, work):
        self.count, self.size = 0, 0
        self.tmp = os.path.join(work, ".dl")
        os.makedirs(self.tmp, exist_ok=True)

    def fetch(self, name, version, dest, expect=None):
        if not NAME_RE.match(name):
            raise RuntimeError("'%s' isn't a valid package name." % name)
        self.count += 1
        if self.count > MAX_PACKAGES:
            raise RuntimeError("This package pulls in too many others, so it was not installed.")
        meta = _meta(name, version)
        integ = meta["dist"]["integrity"]
        if expect and integ != expect:
            raise RuntimeError("npm's checksum for %s@%s differs from the one Shoggoth was given, so it was not installed." % (name, version))
        tar = urllib.parse.urlparse(meta["dist"]["tarball"])
        reg = urllib.parse.urlparse(piup.REGISTRY)
        if tar.scheme not in ("http", "https") or tar.scheme != reg.scheme or tar.netloc != reg.netloc:
            raise RuntimeError("%s is hosted outside the npm registry, so it was not installed." % name)
        path = os.path.join(self.tmp, "%d.tgz" % self.count)
        piup._download(meta["dist"]["tarball"], path, integ, max_bytes=MAX_TARBALL)
        piup._extract(path, dest, limit=MAX_UNPACKED - self.size)
        os.remove(path)
        for d, dirs, files in os.walk(dest):
            if d == dest and "node_modules" in dirs:
                dirs.remove("node_modules")                       # already counted when it was unpacked
            self.size += sum(os.path.getsize(os.path.join(d, f)) for f in files)
        if not os.path.isfile(os.path.join(dest, "package.json")):
            raise RuntimeError("%s has no package.json, so it was not installed." % name)
        return meta

    def deps(self, meta, where, top):
        """Install meta's dependencies. They go in <top>/node_modules, or <where>/node_modules when a different version is already there.
        All of a package's own dependencies are placed before any of theirs, so what a package asks for directly gets the shared spot."""
        placed = []
        for dep, rng in sorted((meta.get("dependencies") or {}).items()):
            if dep in HOST_PROVIDED:
                continue
            if not NAME_RE.match(dep) or not isinstance(rng, str) or re.match(r"^(npm:|file:|git|https?:|link:|workspace:|[\w.-]+/[\w.-]+$)", rng.strip()):
                raise RuntimeError("%s depends on '%s' from somewhere other than the npm registry, so it was not installed." % (meta.get("name"), dep))
            rng = rng.strip() or "*"
            nested = os.path.join(where, "node_modules", dep)
            if any((_read_version(p) and satisfies(_read_version(p), rng)) for p in (nested, os.path.join(top, "node_modules", dep))):
                continue
            ver = _resolve(dep, rng)
            dest = os.path.join(top, "node_modules", dep)
            if os.path.exists(dest):
                dest = nested
            placed.append((self.fetch(dep, ver, dest), dest))
        for sub, dest in placed:
            self.deps(sub, dest, top)


# ---------------------------------------------------------------- what is installed
def installed():
    """[{name, version, dir}] for every package that was installed completely, sorted by name."""
    out = []
    try:
        names = os.listdir(root())
    except OSError:
        return out
    for d in names:
        try:
            with open(os.path.join(root(), d, MARK)) as f:
                m = json.load(f)
            out.append({"name": str(m["name"]), "version": str(m["version"]), "dir": os.path.join(root(), d)})
        except (OSError, ValueError, KeyError):
            continue
    return sorted(out, key=lambda p: (p["name"], piup.vkey(p["version"])))


def install(name, entries=None):
    """Install the curated package `name` (exactly its pinned version). Raises RuntimeError with a message fit to show."""
    entry = next((e for e in (curated() if entries is None else entries) if e["name"] == name), None)
    if not entry:
        raise RuntimeError("%s isn't on Shoggoth's list of packages, so it can't be installed." % name)
    if not entry.get("enabled"):
        raise RuntimeError("%s hasn't been tested with Shoggoth yet, so it can't be installed." % name)
    if not str(entry.get("integrity") or "").startswith("sha512-"):
        raise RuntimeError("%s hasn't been tested with Shoggoth yet (no checksum is pinned), so it can't be installed." % name)
    ver = entry.get("version") or ""
    if not VERSION_RE.match(ver):
        raise RuntimeError("%s has no pinned version yet, so it can't be installed." % name)
    with _lock:
        os.makedirs(root(), exist_ok=True)
        final = os.path.join(root(), _dirname(name, ver))
        work = tempfile.mkdtemp(prefix=".work-", dir=root())
        try:
            job = _Job(work)
            dest = os.path.join(work, "pkg")
            meta = job.fetch(name, ver, dest, expect=entry["integrity"])
            job.deps(meta, dest, dest)
            with open(os.path.join(dest, MARK), "w") as f:
                json.dump({"name": name, "version": ver, "integrity": meta["dist"]["integrity"], "packages": job.count}, f)
            shutil.rmtree(final, ignore_errors=True)
            os.replace(dest, final)
        except OSError as e:
            raise RuntimeError("Couldn't install %s (%s)." % (name, getattr(e, "reason", None) or e))
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return {"name": name, "version": ver, "dir": final}


def remove(name):
    """Delete every installed version of `name`. True when something was removed."""
    gone = False
    with _lock:
        for p in installed():
            if p["name"] == name:
                shutil.rmtree(p["dir"], ignore_errors=True)
                gone = True
    return gone


def settings_entries(enabled=None):
    """The `packages` list for Pi's settings.json: the absolute folder of each installed package that is turned on.

    Pi loads a path as a local package, as is, without copying or installing it. `enabled` is a collection of package names
    (default: every installed one). Pi's docs also allow {"source": path, "extensions": [...], ...} to filter what loads."""
    return [p["dir"] for p in installed() if enabled is None or p["name"] in enabled]


def active():
    """The installed packages that load into Pi: installed completely, and still turned on in the pinned list. A package whose entry
    was switched off (or dropped) in a later release stops loading even though its files are still on disk."""
    on = {e["name"] for e in curated() if e["enabled"]}
    return [p for p in installed() if p["name"] in on]


def skill_dirs(pkg_dir):
    """The folders inside an unpacked package that hold its skills: package.json's pi.skills paths, else skills/. Never outside the package."""
    try:
        with open(os.path.join(pkg_dir, "package.json")) as f:
            declared = ((json.load(f).get("pi") or {}).get("skills"))
    except (OSError, ValueError, AttributeError):
        declared = None
    paths = [x for x in declared if isinstance(x, str)] if isinstance(declared, list) else ["skills"]
    base = os.path.realpath(pkg_dir)
    out = []
    for rel in paths:
        d = os.path.realpath(os.path.join(base, rel))
        if d.startswith(base + os.sep) and os.path.isdir(d) and d not in out:
            out.append(d)
    return out
