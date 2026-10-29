"""Folders Shoggoth has worked in, and what it has learned to trust there.

Trust belongs to the folder, not the chat: how careful to be (the approval mode a new chat there starts with) and the
commands the person has said to always allow. Kept in Shoggoth's data folder next to the Cowork task list.
"""
import json
import os
import threading
import time

import store

_lock = threading.RLock()
MODES = ("ask", "edits", "auto", "plan")

# Commands that are never remembered, however often they are approved: each one can run anything, delete things
# or reach other machines, so allowing it once says nothing about the next time.
NEVER = {"rm", "rmdir", "sudo", "su", "doas", "dd", "mkfs", "chmod", "chown", "sh", "bash", "zsh", "fish", "dash", "eval", "exec",
         "ssh", "scp", "sftp", "rsync", "curl", "wget", "nc", "ncat", "xargs", "find", "env", "nohup", "timeout", "watch",
         "docker", "podman", "kubectl", "systemctl", "kill", "pkill", "killall", "crontab", "mv", "cp", "ln", "shred"}


def _path():
    return os.path.join(store.data_dir(), "cowork", "folders.json")


def _load():
    try:
        with open(_path()) as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d):
    p = _path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, p)


def _key(path):
    return os.path.realpath(os.path.expanduser(str(path or "")))


def get(path):
    with _lock:
        rec = _load().get(_key(path)) or {}
    return {"mode": rec.get("mode") if rec.get("mode") in MODES else "", "allow": list(rec.get("allow") or []),
            "last": rec.get("last", 0)}


def touch(path):
    """Remember that this folder was used just now."""
    k = _key(path)
    if not os.path.isdir(k):
        return
    with _lock:
        d = _load()
        rec = d.setdefault(k, {})
        rec["last"] = time.time()
        _save(d)


def remember_mode(path, mode):
    if mode not in MODES:
        return
    k = _key(path)
    with _lock:
        d = _load()
        d.setdefault(k, {})["mode"] = mode
        _save(d)


def valid_key(key):
    key = str(key or "").strip()
    if not key or len(key) > 80 or "\n" in key or "\r" in key:
        return ""
    if key.split()[0].rsplit("/", 1)[-1] in NEVER:
        return ""
    return key


def allow_add(path, key):
    key = valid_key(key)
    if not key:
        return False
    k = _key(path)
    with _lock:
        d = _load()
        rec = d.setdefault(k, {})
        keys = rec.setdefault("allow", [])
        if key not in keys:
            keys.append(key)
        _save(d)
    return True


def allow_remove(path, key):
    k = _key(path)
    with _lock:
        d = _load()
        rec = d.get(k) or {}
        rec["allow"] = [x for x in rec.get("allow", []) if x != key]
        d[k] = rec
        _save(d)


def recent(n=8):
    """Folders used lately that still exist, newest first."""
    with _lock:
        d = _load()
    rows = [(rec.get("last", 0), k, rec) for k, rec in d.items() if os.path.isdir(k) and rec.get("last")]
    rows.sort(reverse=True)
    return [{"path": k, "name": os.path.basename(k.rstrip("/")) or k, "mode": rec.get("mode", ""),
             "allow": rec.get("allow", [])} for _, k, rec in rows[:n]]


def info(path):
    """A quick look at a folder for the job form: is it a git repo, and what would a copy leave out."""
    import workcopy
    k = _key(path)
    if not os.path.isdir(k):
        raise ValueError("That folder doesn't exist.")
    return workcopy.survey(k)
