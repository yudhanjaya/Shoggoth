"""Keeps Pi (the agent) up to date, the way engine.py does for Lemonade.

Looks on the npm registry, downloads the new release, checks it against the checksum npm publishes, unpacks it into
Zest's own data folder next to the copy that came with the AppImage (which is never touched), runs it once to make sure
it starts and loads Zest's extensions, and only then offers it. Standard library only.
"""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import threading
import time
import urllib.error
import urllib.request

import store
import util

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = "@earendil-works/pi-coding-agent"
REGISTRY = "https://registry.npmjs.org/"
RELEASES = "https://github.com/earendil-works/pi/releases"
UPD = {"state": "idle", "bytes": 0, "total": 0, "version": None, "error": None}
on_installed = []            # callables run after a new version is installed (the server restarts idle tasks)
MAX_UNPACKED = 200 * 1024 * 1024


def root():
    return os.path.join(store.data_dir(), "pi")


def _appimage_root():
    return os.path.abspath(os.path.join(HERE, "..", ".."))


def bundled_dir():
    d = os.path.join(_appimage_root(), "pi")
    return d if os.path.isfile(os.path.join(d, "dist", "bundle", "cli.js")) else None


def node_bin():
    """The Node that runs Pi: the one in the AppImage, else a Node on the PATH."""
    n = os.path.join(_appimage_root(), "node", "bin", "node")
    return n if os.access(n, os.X_OK) else shutil.which("node")


def vkey(v):
    return tuple(int(x) if x.isdigit() else 0 for x in re.findall(r"\d+", str(v))[:3]) or (0,)


def _version_of(directory):
    try:
        with open(os.path.join(directory, "package.json")) as f:
            return str(json.load(f).get("version", ""))
    except (OSError, ValueError):
        return ""


def programs():
    """Every Pi Zest can run: the bundled one and the downloaded ones, newest first."""
    out = []
    b = bundled_dir()
    if b:
        out.append({"version": _version_of(b), "dir": b, "source": "bundled"})
    try:
        for d in os.listdir(root()):
            p = os.path.join(root(), d)
            if os.path.isfile(os.path.join(p, "dist", "bundle", "cli.js")):
                out.append({"version": _version_of(p) or d, "dir": p, "source": "downloaded"})
    except OSError:
        pass
    return sorted(out, key=lambda p: vkey(p["version"]), reverse=True)


def choose(settings=None):
    """The Pi to run: the version pinned in settings ("bundled" or a number), else the newest."""
    progs = programs()
    if not progs:
        return None
    want = (settings or store.load()).get("pi", {}).get("version", "")
    if want == "bundled":
        return next((p for p in progs if p["source"] == "bundled"), progs[0])
    return next((p for p in progs if p["version"] == want), progs[0])


def command(settings=None):
    """([node, cli.js], source) for the Pi to run, or (None, "") when there isn't one Zest manages."""
    p, node = choose(settings), node_bin()
    if p and node:
        return [node, os.path.join(p["dir"], "dist", "bundle", "cli.js")], p["source"]
    return None, ""


def summary(settings=None):
    settings = settings or store.load()
    act = choose(settings)
    return {"active": {"version": act["version"], "source": act["source"]} if act else None,
            "node": bool(node_bin()),
            "programs": [{"version": p["version"], "source": p["source"], "active": bool(act and p["dir"] == act["dir"])} for p in programs()],
            "update": dict(UPD)}


# ---------------------------------------------------------------- looking for a release
def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Zest", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError("The npm registry answered %s. Try again later." % e.code)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise RuntimeError("Couldn't reach the npm registry to look for updates (%s). Check your internet connection." % getattr(e, "reason", e))


def _node_version():
    node = node_bin()
    code, out = util.run([node, "--version"], 10) if node else (1, "")
    return out.strip().lstrip("v") if code == 0 else ""


def latest_release():
    target = os.environ.get("ZEST_PI_TARGET") or "latest"          # a version number here is for testing
    meta = _get_json(REGISTRY + PKG + "/" + target)
    dist = meta.get("dist") or {}
    integrity = str(dist.get("integrity") or "")
    if not integrity.startswith("sha512-") or not dist.get("tarball"):
        raise RuntimeError("npm didn't publish a checksum for this release, so Zest won't install it.")
    jiti = str((meta.get("dependencies") or {}).get("jiti", ""))
    return {"version": str(meta["version"]), "url": dist["tarball"], "integrity": integrity, "jiti": jiti,
            "node": str((meta.get("engines") or {}).get("node", "")), "page": RELEASES}


def check(settings=None):
    rel = latest_release()
    cur = choose(settings)
    current = cur["version"] if cur else None
    newest_local = max((vkey(p["version"]) for p in programs()), default=(0,))
    newer_major = bool(current) and vkey(rel["version"])[0] > vkey(current)[0]
    return {"latest": rel["version"], "current": current, "page": rel["page"],
            "update_available": vkey(rel["version"]) > newest_local and not newer_major,
            "newer_major": newer_major,
            "have_latest": any(p["version"] == rel["version"] for p in programs())}


# ---------------------------------------------------------------- installing one
def _sri(path):
    h = hashlib.sha512()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha512-" + base64.b64encode(h.digest()).decode()


def _download(url, dest, integrity, progress=False):
    req = urllib.request.Request(url, headers={"User-Agent": "Zest"})
    with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as out:
        if progress:
            UPD["total"] = int(r.headers.get("Content-Length") or 0)
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            out.write(chunk)
            if progress:
                UPD["bytes"] += len(chunk)
    if _sri(dest) != integrity:
        raise RuntimeError("The download didn't match npm's checksum, so it was thrown away. Try again.")


def _extract(tar_path, dest, only=None):
    """Unpack an npm tarball (its files sit under `package/`): regular files only, nothing that escapes `dest`."""
    real, total = os.path.realpath(dest), 0
    with tarfile.open(tar_path, "r:gz") as tf:
        for m in tf:
            parts = m.name.split("/")
            if m.name.startswith("/") or ".." in parts:
                raise RuntimeError("The download contains an unsafe path, so it was rejected.")
            if not m.isfile() or parts[0] != "package":
                continue
            rel = "/".join(parts[1:])
            if only and not any(rel == o or rel.startswith(o + "/") for o in only):
                continue
            target = os.path.realpath(os.path.join(dest, rel))
            if not target.startswith(real + os.sep):
                raise RuntimeError("The download contains an unsafe path, so it was rejected.")
            total += m.size
            if total > MAX_UNPACKED:
                raise RuntimeError("The download is far larger than expected, so it was rejected.")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with tf.extractfile(m) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def _smoke_test(directory, version):
    """Run the new Pi once: it must report its version, then start in RPC mode with Zest's extensions loaded."""
    node, cli = node_bin(), os.path.join(directory, "dist", "bundle", "cli.js")
    code, out = util.run([node, cli, "--version"], 30, env=dict(os.environ, PI_OFFLINE="1"))
    if code != 0 or version not in out:
        raise RuntimeError("The new Pi won't start on this computer. " + out.strip().splitlines()[-1][:200] if out.strip() else "The new Pi won't start on this computer.")
    import agent                                         # late: agent imports this module
    tmp = os.path.join(root(), ".smoke-agent")
    os.makedirs(tmp, exist_ok=True)
    env = dict(os.environ, PI_CODING_AGENT_DIR=tmp, PI_OFFLINE="1", PI_TELEMETRY="0", PI_SKIP_VERSION_CHECK="1", ZEST_INJECT="[]")
    p = subprocess.Popen([node, cli, "--mode", "rpc", "--no-session", "--no-approve", "--no-skills", "-e", agent.GATE, "-e", agent.SKILLS_EXT],
                         cwd=tmp, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        p.stdin.write(b'{"id":"t","type":"get_commands"}\n')
        p.stdin.flush()
        ok, deadline = False, time.time() + 25
        while time.time() < deadline:
            line = p.stdout.readline()
            if not line:
                break
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("type") == "response" and rec.get("id") == "t":
                ok = rec.get("success") and any(c.get("name") == "zest-mode" for c in rec["data"]["commands"])
                break
        if not ok:
            err = p.stderr.read1(400).decode("utf8", "replace").strip() if p.poll() is not None else ""
            raise RuntimeError("The new Pi started but doesn't work with this version of Zest." + (" " + err.splitlines()[-1][:200] if err else ""))
    finally:
        try:
            p.stdin.close()
            p.wait(5)
        except (OSError, subprocess.TimeoutExpired):
            p.kill()
        shutil.rmtree(tmp, ignore_errors=True)


def _do_update(rel):
    os.makedirs(root(), exist_ok=True)
    ver = rel["version"]
    tgz, jtgz = (os.path.join(root(), ".download-%s.tgz" % n) for n in ("pi", "jiti"))
    tmp = os.path.join(root(), ".unpack-" + ver)
    try:
        need = rel["node"].lstrip(">=") if rel["node"].startswith(">=") else ""
        have = _node_version()
        if need and have and vkey(have) < vkey(need):
            raise RuntimeError("Pi %s needs Node %s or newer, and this Zest has Node %s. A newer Zest will bring a newer Node." % (ver, need, have))
        if not re.fullmatch(r"\d+\.\d+\.\d+", rel["jiti"]):
            raise RuntimeError("Pi %s needs a different version of one of its parts than this Zest knows how to fetch. A newer Zest will handle it." % ver)
        UPD.update(state="downloading", bytes=0, total=0, version=ver, error=None)
        _download(rel["url"], tgz, rel["integrity"], progress=True)
        jmeta = _get_json(REGISTRY + "jiti/" + rel["jiti"])
        jint = str((jmeta.get("dist") or {}).get("integrity") or "")
        if not jint.startswith("sha512-"):
            raise RuntimeError("npm didn't publish a checksum for one of Pi's parts, so Zest won't install it.")
        _download(jmeta["dist"]["tarball"], jtgz, jint)
        UPD["state"] = "installing"
        shutil.rmtree(tmp, ignore_errors=True)
        _extract(tgz, tmp, only=["package.json", "dist/bundle"])
        _extract(jtgz, os.path.join(tmp, "node_modules", "jiti"))
        _smoke_test(tmp, ver)
        final = os.path.join(root(), ver)
        shutil.rmtree(final, ignore_errors=True)
        os.replace(tmp, final)
        have_dirs = sorted((d for d in os.listdir(root()) if os.path.isfile(os.path.join(root(), d, "dist", "bundle", "cli.js"))), key=vkey, reverse=True)
        for old in have_dirs[2:]:
            shutil.rmtree(os.path.join(root(), old), ignore_errors=True)
        util.invalidate()
        UPD["state"] = "done"
        for fn in on_installed:
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        UPD.update(state="error", error=str(e) or e.__class__.__name__)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for f in (tgz, jtgz):
            try:
                os.remove(f)
            except OSError:
                pass


def start_update():
    if UPD["state"] in ("downloading", "installing"):
        return False
    if not node_bin():
        UPD.update(state="error", error="Pi needs Node.js to run, and none was found. The Zest AppImage includes it.")
        return False
    try:
        rel = latest_release()
    except RuntimeError as e:
        UPD.update(state="error", error=str(e))
        return False
    UPD.update(state="downloading", bytes=0, total=0, version=rel["version"], error=None)
    threading.Thread(target=_do_update, args=(rel,), daemon=True).start()
    return True


def use(version):
    """Pin the Pi to run: "bundled", a downloaded version number, or "" for the newest."""
    if version and version != "bundled" and not any(p["version"] == version for p in programs()):
        raise ValueError("That version of Pi isn't installed.")
    s = store.load()
    s.setdefault("pi", {})["version"] = version or ""
    store.save(s)
    for fn in on_installed:
        fn()
