"""The steps every updater shares: look up a release, download it with progress and check its checksum, unpack it safely, keep the newest
copies, and start the work in a thread once. Shoggoth, Lemonade and Pi each say what is theirs (where the release is, what to run to
test it); Strata updates through its own signed feed and only shares the state dictionary shape. Standard library only."""
import base64
import hashlib
import json
import os
import shutil
import tarfile
import threading
import urllib.error
import urllib.request


def get_json(url, accept, http_error, net_error):
    """GET a JSON document. `http_error` and `net_error` are the messages for a refusal (%s: the code) and for no connection (%s: why)."""
    req = urllib.request.Request(url, headers={"User-Agent": "Shoggoth", "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(http_error % e.code)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise RuntimeError(net_error % getattr(e, "reason", e))


def _digest(expect):
    """A hash object for the checksum `expect`: "sha512-<base64>" (what npm publishes) or a hex sha256 (what GitHub publishes)."""
    return hashlib.sha512() if expect.startswith("sha512-") else hashlib.sha256()


def _matches(h, expect):
    return ("sha512-" + base64.b64encode(h.digest()).decode()) == expect if expect.startswith("sha512-") else h.hexdigest() == expect


def download(url, dest, expect, source, upd=None, chunk=65536, total_from_header=False, verifying=False):
    """Save `url` to `dest` and check it against `expect`. With `upd` (an UPD dict) the byte count is kept in it as it grows; `verifying` also
    sets its state to "verifying" while the checksum is compared. A file that doesn't match is an error (the caller deletes it)."""
    h = _digest(expect)
    req = urllib.request.Request(url, headers={"User-Agent": "Shoggoth"})
    with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as out:
        if upd is not None and total_from_header:
            upd["total"] = int(r.headers.get("Content-Length") or 0)
        while True:
            block = r.read(chunk)
            if not block:
                break
            out.write(block)
            h.update(block)
            if upd is not None:
                upd["bytes"] += len(block)
    if upd is not None and verifying:
        upd["state"] = "verifying"
    if not _matches(h, expect):
        raise RuntimeError("The download didn't match %s checksum, so it was thrown away. Try again." % source)


def safe_extract(tar_path, dest, limit, top=None, only=None, dirs=True, chmod=None):
    """Unpack a gzip tarball: regular files (and folders when `dirs`) only, nothing that escapes `dest`, nothing past `limit` bytes.
    The first folder is stripped: `top` names the one expected (others are skipped, as in an npm package), else it is whatever the first
    entry is. `only` limits it to those paths below the top; `chmod(rel)` gives each file's mode."""
    real, total = os.path.realpath(dest), 0
    unsafe = RuntimeError("The download contains an unsafe path, so it was rejected.")
    with tarfile.open(tar_path, "r:gz") as tf:
        members = tf.getmembers()
        first = top or (members[0].name.split("/")[0] if members else "")
        for m in members:
            parts = m.name.split("/")
            if m.name.startswith("/") or ".." in parts:
                raise unsafe
            if not (m.isfile() or (dirs and m.isdir())):
                continue                                   # links and device files are never unpacked
            if top and parts[0] != top:
                continue
            rel = "/".join(parts[1:]) if parts[0] == first else m.name
            if not rel or (only and not any(rel == o or rel.startswith(o + "/") for o in only)):
                continue
            target = os.path.realpath(os.path.join(dest, rel))
            if not (target == real or target.startswith(real + os.sep)):
                raise unsafe
            if m.isdir():
                os.makedirs(target, exist_ok=True)
                continue
            total += m.size
            if total > limit:
                raise RuntimeError("The download is far larger than expected, so it was rejected.")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with tf.extractfile(m) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            if chmod:
                os.chmod(target, chmod(rel))


def keep_newest(root, valid, key, keep=2):
    """Delete all but the `keep` newest folders of `root` that `valid(path)` accepts, newest by `key(name)`."""
    have = sorted((d for d in os.listdir(root) if valid(os.path.join(root, d))), key=key, reverse=True)
    for old in have[keep:]:
        shutil.rmtree(os.path.join(root, old), ignore_errors=True)


def begin(upd, active, fetch, work, total_of=lambda rel: rel.get("size", 0)):
    """Start an update in a thread unless one is running. `fetch()` finds the release (RuntimeError: it can't), `work(rel)` does the rest.
    Returns whether a thread was started; a failed lookup is left in `upd` as the error."""
    if upd["state"] in active:
        return False
    try:
        rel = fetch()
    except RuntimeError as e:
        upd.update(state="error", error=str(e))
        return False
    upd.update(state="downloading", bytes=0, total=total_of(rel), version=rel["version"], error=None)
    threading.Thread(target=work, args=(rel,), daemon=True).start()
    return True
