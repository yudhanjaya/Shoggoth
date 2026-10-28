"""Updates Zest itself, and runs "update everything" (Zest, Lemonade and Pi) from one button.

Zest ships as an AppImage, so an update is a new AppImage: download it from the project's latest GitHub release, check it
against the checksum GitHub publishes, and save it next to the running one (which is never touched). Restarting hands
over to the new file. Standard library only.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import engine
import piup
import store

REPO = "yudhanjaya/Zest"
UPD = {"state": "idle", "bytes": 0, "total": 0, "version": None, "error": None, "path": None}
ALL = {"state": "idle", "items": {}, "error": None}
_lock = threading.Lock()


def appimage_path():
    """The AppImage file we are running from, or None (running from source, or an unpacked image)."""
    p = os.environ.get("APPIMAGE")
    return p if p and os.path.isfile(p) else None


def vkey(v):
    return engine.vkey(v)


def latest_release(version):
    data = engine._get_json(f"https://api.github.com/repos/{REPO}/releases/latest")
    tag = str(data.get("tag_name", ""))
    ver = tag.lstrip("v")
    want = f"Zest-{ver}-x86_64.AppImage"
    assets = data.get("assets", [])
    a = next((x for x in assets if x.get("name") == want), None)
    if not a:
        raise RuntimeError(f"Zest {ver} has no AppImage attached to its release yet. Try again a little later.")
    digest = str(a.get("digest") or "")
    sha = digest[7:] if digest.startswith("sha256:") else ""
    if not sha:
        side = next((x for x in assets if x.get("name") == want + ".sha256"), None)
        if side:
            req = urllib.request.Request(side["browser_download_url"], headers={"User-Agent": "Zest"})
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    m = re.match(r"[0-9a-fA-F]{64}", r.read(500).decode("utf8", "replace").strip())
                    sha = m.group(0).lower() if m else ""
            except OSError:
                sha = ""
    if not sha:
        raise RuntimeError("GitHub didn't publish a checksum for this release, so Zest won't install it.")
    return {"version": ver, "name": want, "url": a["browser_download_url"], "size": int(a.get("size", 0)), "sha256": sha,
            "page": data.get("html_url", "")}


def check(version):
    rel = latest_release(version)
    dest = _dest(rel)
    return {"latest": rel["version"], "current": version, "page": rel["page"], "size": rel["size"],
            "update_available": vkey(rel["version"]) > vkey(version),
            "can_install": bool(appimage_path()),
            "have_latest": bool(dest and os.path.isfile(dest))}


def _dest(rel):
    cur = appimage_path()
    return os.path.join(os.path.dirname(cur), rel["name"]) if cur else None


def _do_update(rel):
    dest = _dest(rel)
    tmp = dest + ".part"
    try:
        UPD.update(state="downloading", bytes=0, total=rel["size"], version=rel["version"], error=None, path=None)
        sha = hashlib.sha256()
        req = urllib.request.Request(rel["url"], headers={"User-Agent": "Zest"})
        with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as out:
            while True:
                chunk = r.read(262144)
                if not chunk:
                    break
                out.write(chunk)
                sha.update(chunk)
                UPD["bytes"] += len(chunk)
        UPD["state"] = "verifying"
        if sha.hexdigest() != rel["sha256"]:
            raise RuntimeError("The download didn't match GitHub's checksum, so it was thrown away. Try again.")
        os.chmod(tmp, 0o755)
        os.replace(tmp, dest)
        UPD.update(state="done", path=dest)
    except PermissionError:
        UPD.update(state="error", error="Zest can't save the new version next to the current one (that folder isn't writable). Download it from the releases page instead.")
    except Exception as e:  # noqa: BLE001
        UPD.update(state="error", error=str(e) or e.__class__.__name__)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def start_update(version):
    if UPD["state"] in ("downloading", "verifying"):
        return False
    if not appimage_path():
        UPD.update(state="error", error="This copy of Zest isn't running from an AppImage, so it can't replace itself. Update it the way you installed it.")
        return False
    try:
        rel = latest_release(version)
    except RuntimeError as e:
        UPD.update(state="error", error=str(e))
        return False
    if vkey(rel["version"]) <= vkey(version):
        UPD.update(state="idle", error=None)
        return False
    dest = _dest(rel)
    if os.path.isfile(dest):
        UPD.update(state="done", version=rel["version"], path=dest, error=None)
        return False
    UPD.update(state="downloading", bytes=0, total=rel["size"], version=rel["version"], error=None, path=None)
    threading.Thread(target=_do_update, args=(rel,), daemon=True).start()
    return True


def restart(path):
    """Start the new AppImage once this process has gone, then let the caller shut this one down."""
    if not path or not os.path.isfile(path) or os.path.dirname(os.path.abspath(path)) != os.path.dirname(appimage_path() or ""):
        raise ValueError("There's no downloaded update to restart into.")
    env = {k: v for k, v in os.environ.items() if k not in ("APPIMAGE", "APPDIR", "ARGV0", "OWD")}
    script = 'while kill -0 "$1" 2>/dev/null; do sleep 0.5; done; exec "$2"'
    subprocess.Popen(["/bin/sh", "-c", script, "zest-restart", str(os.getpid()), os.path.abspath(path)], env=env,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


# ---------------------------------------------------------------- everything at once
def _item(name, **kw):
    ALL["items"][name] = dict(ALL["items"].get(name, {}), **kw)


def _wait(upd, active):
    while upd["state"] in active:
        time.sleep(0.5)


def _run_all(version, mode):
    settings = store.load()
    ALL.update(state="working", items={}, error=None)
    for n in ("zest", "lemonade", "pi"):
        _item(n, state="checking", text="Looking for updates…")
    jobs = []
    # Zest
    try:
        c = check(version)
        if not c["update_available"]:
            _item("zest", state="current", text="Zest %s is the latest." % version)
        elif not c["can_install"]:
            _item("zest", state="manual", text="Zest %s is out. This copy can't replace itself; get it from the releases page." % c["latest"], page=c["page"])
        else:
            _item("zest", state="working", text="Downloading Zest %s…" % c["latest"])
            jobs.append(("zest", lambda: start_update(version), UPD, ("downloading", "verifying")))
    except RuntimeError as e:
        _item("zest", state="error", text=str(e))
    # Lemonade (only the copy Zest manages)
    if mode != "builtin" or engine.choose(settings) is None:
        _item("lemonade", state="skipped", text="You use your own Lemonade; Zest leaves it alone.")
    else:
        try:
            c = engine.check(settings)
            if not c["update_available"]:
                _item("lemonade", state="current", text="Lemonade %s is the latest." % (c["current"] or c["latest"]))
            else:
                def go():
                    s = store.load()
                    s["builtin"]["version"] = ""
                    store.save(s)
                    return engine.start_update()
                _item("lemonade", state="working", text="Downloading Lemonade %s…" % c["latest"])
                jobs.append(("lemonade", go, engine.UPD, ("downloading", "verifying", "installing")))
        except RuntimeError as e:
            _item("lemonade", state="error", text=str(e))
    # Pi
    try:
        c = piup.check(settings)
        if not c["update_available"]:
            _item("pi", state="current", text="Pi %s is the latest." % (c["current"] or c["latest"]))
        else:
            _item("pi", state="working", text="Downloading Pi %s…" % c["latest"])
            jobs.append(("pi", piup.start_update, piup.UPD, ("downloading", "installing")))
    except RuntimeError as e:
        _item("pi", state="error", text=str(e))
    for name, start, upd, active in jobs:
        start()
    for name, start, upd, active in jobs:
        _wait(upd, active)
        if upd["state"] == "error":
            _item(name, state="error", text=upd.get("error") or "The update failed.")
        else:
            _item(name, state="updated", text={"zest": "Zest %s is downloaded. Restart to start using it.",
                                              "lemonade": "Lemonade %s is installed. It starts the next time Lemonade restarts.",
                                              "pi": "Pi %s is installed. Your next message uses it."}[name] % upd.get("version"),
                  **({"path": upd.get("path")} if name == "zest" else {}))
    ALL["state"] = "done"


def start_all(version, mode):
    with _lock:
        if ALL["state"] == "working":
            return False
        ALL.update(state="working", items={}, error=None)
    def run():
        try:
            _run_all(version, mode)
        except Exception as e:  # noqa: BLE001
            ALL.update(state="done", error=str(e) or e.__class__.__name__)
    threading.Thread(target=run, daemon=True).start()
    return True


def status():
    return {"state": ALL["state"], "error": ALL["error"], "items": json.loads(json.dumps(ALL["items"])),
            "restart": bool(UPD["state"] == "done" and UPD.get("path") and os.path.isfile(UPD["path"]))}
