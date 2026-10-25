"""Cowork: runs the Pi coding agent (https://pi.dev) against Zest's own Lemonade and relays it to the browser.

One `pi --mode rpc` child process per Cowork task, talking JSON lines over stdin/stdout. Zest keeps each task's
event stream in memory so the page can reconnect, stores the task list in its data folder, and leaves the transcript
itself to Pi (which writes it to a session file we point at a per-task folder). Standard library only.

A task is either a chat (kind "chat": a conversation, optionally in folders) or a job (kind "job": a goal a chat
hands off to run in the background, in rounds, checked after each one). A job is a task of its own with its own Pi
process and transcript, parented to the chat that started it; a small orchestrator thread per job runs the rounds.
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid

import folders as folderstore
import lem
import piup
import skills
import store
import workcopy

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "pi", "zest-gate.js")
SKILLS_EXT = os.path.join(HERE, "pi", "zest-skills.js")
MODES = ("ask", "edits", "auto", "plan")
FORWARD = {"prompt", "steer", "follow_up", "abort", "clear_queue", "get_state", "get_session_stats", "set_model",
           "set_thinking_level", "compact", "get_available_models", "get_commands", "new_session"}
IDLE_STOP = 20 * 60          # a quiet task's process is stopped after this long; reopening it resumes the transcript
CHAT_PROMPT = ("You are Zest, a friendly assistant running on the user's own computer. Answer clearly and concisely. "
               "You cannot see or change any files in this conversation; if the user wants that, tell them to choose a folder to work in.")
JOB_PROMPT = ("This is a background job. Nobody is watching each step, so work on your own toward the goal and only stop to ask "
              "the user something when you are truly blocked. Work in rounds: in each one make the most useful progress you can, "
              "then stop and say in one short sentence what you changed.")
NOT_CHAT = ("sd-cpp", "whispercpp", "kokoro", "moonshine", "acestep", "thinksound", "trellis", "openmoss")
JOB_ACTIVE = ("queued", "working", "paused")
JOB_FINAL = ("done", "stopped", "failed")
DEFAULT_ROUNDS = 10
DEFAULT_SECONDS = 2 * 3600

_lock = threading.RLock()
_sessions = {}               # id -> Session (running processes only)
_start_locks = {}            # id -> Lock, so starting one task never blocks reading the list
_zest_port = {"v": 13306}
_ctl = {}                    # job id -> Ctl for jobs whose orchestrator thread is alive
_owner = {}                  # folder path -> job id of the live (not copy) job working there
_down = {"on": False}        # Zest is shutting down: jobs keep their state so the next start can offer to continue them
_changed = threading.Condition()
_ver = {"n": 0}


def set_zest_port(port):
    _zest_port["v"] = int(port)


def touch():
    """Something the sidebar shows has changed; wakes everyone waiting on the live stream."""
    with _changed:
        _ver["n"] += 1
        _changed.notify_all()


def wait_change(since, timeout=15):
    with _changed:
        if _ver["n"] == since:
            _changed.wait(timeout)
        return _ver["n"]


# ---------------------------------------------------------------- finding Pi
def find_pi():
    """Returns ([command...], where) for the Pi we run: the one in the AppImage, ZEST_PI, or `pi` on the PATH."""
    if os.environ.get("ZEST_PI"):
        return [os.environ["ZEST_PI"]], "ZEST_PI"
    cmd, source = piup.command()                 # the bundled Pi or one Zest downloaded, whichever is chosen
    if cmd:
        return cmd, source
    found = shutil.which("pi")
    return ([found], "PATH") if found else (None, "")


def version():
    cmd, _ = find_pi()
    if not cmd:
        return ""
    try:
        r = subprocess.run(cmd + ["--version"], capture_output=True, text=True, timeout=20, env=_env())
        return (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr).strip() else ""
    except (OSError, subprocess.SubprocessError):
        return ""


# ---------------------------------------------------------------- Pi's own configuration (rewritten every start)
def agent_dir():
    return os.path.join(store.data_dir(), "cowork", "agent")


def chat_models():
    """Downloaded models that can drive an agent: chat models, preferring ones Lemonade says can call tools."""
    out = []
    for m in lem.get("/v1/models").get("data", []):
        labels = m.get("labels") or []
        if not m.get("downloaded") or m.get("recipe") in NOT_CHAT or "embeddings" in labels or "chat" not in labels and "tool-calling" not in labels:
            continue
        out.append({"id": m["id"], "tools": "tool-calling" in labels, "reasoning": "reasoning" in labels,
                    "vision": "vision" in labels, "ctx": int(m.get("context_length") or 131072)})
    out.sort(key=lambda m: (not m["tools"], m["id"].lower()))
    return out


def model_ctx(model):
    """The context window this model has (loaded) or would get (not loaded yet), in tokens; 0 when unknown."""
    for m in lem.get("/v1/health").get("all_models_loaded", []):
        if m.get("model_name") == model:
            cmd = m.get("launch_command") or []
            if "--ctx-size" in cmd:
                try:
                    return int(cmd[cmd.index("--ctx-size") + 1])
                except (ValueError, IndexError):
                    pass
    try:
        return int(lem.get("/v1/models/%s/options" % model).get("resolved_ctx_size") or 0)
    except (TypeError, ValueError):
        return 0


def set_context(model, tokens):
    """Reload `model` with a bigger window. Returns (ok, message)."""
    status, payload = lem.request("POST", "/v1/load", {"model_name": model, "ctx_size": int(tokens)}, timeout=600)
    return status == 200, "" if status == 200 else lem.error_text(payload, "Lemonade couldn't load the model with that size")


def write_config(models, default, ctx=0):
    d = agent_dir()
    os.makedirs(d, exist_ok=True)
    provider = {
        "baseUrl": "http://127.0.0.1:%d/api/v1" % _zest_port["v"],      # through Zest, so it shows on the Server page
        "api": "openai-completions",
        "apiKey": "zest",
        "compat": {"supportsDeveloperRole": False, "supportsUsageInStreaming": True},
        "models": [{"id": m["id"], "name": m["id"], "reasoning": m["reasoning"],
                    "input": ["text", "image"] if m["vision"] else ["text"],
                    "contextWindow": ctx if (ctx and m["id"] == default) else min(m["ctx"], 131072),
                    "maxTokens": 16384} for m in models],
    }
    _write(os.path.join(d, "models.json"), {"providers": {"lemonade": provider}})
    settings = {"defaultProvider": "lemonade", "defaultProjectTrust": "never", "quietStartup": True,
                "enableInstallTelemetry": False, "autoCompaction": True}
    if default:
        settings["defaultModel"] = default
    _write(os.path.join(d, "settings.json"), settings)


def _write(path, obj):
    tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def _env(mode="ask", inject=(), extra_folders=(), allow=()):
    e = dict(os.environ)
    e.update(PI_CODING_AGENT_DIR=agent_dir(), PI_OFFLINE="1", PI_TELEMETRY="0", PI_SKIP_VERSION_CHECK="1",
             ZEST_APPROVAL=mode, ZEST_INJECT=json.dumps(list(inject)), NO_COLOR="1",
             ZEST_FOLDERS=json.dumps(list(extra_folders)), ZEST_ALLOW=json.dumps(list(allow)))
    return e


# ---------------------------------------------------------------- the task list
def _index_path():
    return os.path.join(store.data_dir(), "cowork", "tasks.json")


def _load_index():
    try:
        with open(_index_path()) as f:
            return json.load(f).get("tasks", [])
    except (OSError, ValueError):
        return []


def _save_index(tasks):
    os.makedirs(os.path.dirname(_index_path()), exist_ok=True)
    _write(_index_path(), {"tasks": tasks})


def _update_meta(sid, **kw):
    with _lock:
        tasks = _load_index()
        for t in tasks:
            if t["id"] == sid:
                t.update(kw)
        _save_index(tasks)
    touch()


def _update_job(sid, **kw):
    with _lock:
        tasks = _load_index()
        for t in tasks:
            if t["id"] == sid and t.get("job"):
                t["job"].update(kw)
                t["updated"] = time.time()
        _save_index(tasks)
    touch()


def get_task(sid):
    for t in _load_index():
        if t["id"] == sid:
            return t
    return None


def _is_job(t):
    return bool(t) and t.get("kind") == "job"


def _spent(j):
    """Seconds a job has actually been working (not counting time paused or stopped)."""
    s = j.get("spent_before", 0)
    if j.get("resumed_at"):
        s += time.time() - j["resumed_at"]
    return s


def _doing(sid):
    s = _sessions.get(sid)
    return s.doing if s and s.alive() else ""


def _job_summary(t, parent_seen=0):
    j = t["job"]
    s = _sessions.get(t["id"])
    need = (len(s.pending) if s else 0) + (1 if j.get("wait_reason") == "review" and j["state"] == "paused" else 0)
    results = j.get("results", [])
    series = [r.get("metric") for r in results]
    doing = ""
    if j["state"] == "queued":
        doing = "Waiting for another job in this folder"
    elif j["state"] == "paused":
        doing = "Waiting for you to continue" if j.get("wait_reason") == "review" else "Paused"
    elif j["state"] == "working":
        doing = ("Waiting for you" if need else (s.doing if s and s.alive() and s.doing else "Thinking…"))
    return {"id": t["id"], "title": t["title"], "goal": j["goal"], "state": j["state"], "round": j.get("round", 0),
            "rounds_max": j.get("rounds_max", DEFAULT_ROUNDS), "seconds_max": j.get("seconds_max", 0), "spent": _spent(j),
            "series": series, "check": j.get("check", ""), "check_kind": j.get("check_kind", "words"), "copy": bool(j.get("copy")), "watched": bool(j.get("watched")),
            "copy_state": j.get("copy_state", ""), "doing": doing, "need": need, "started": j.get("started", 0),
            "finished": j.get("finished", 0), "stop_reason": j.get("stop_reason", ""), "after_msgs": j.get("after_msgs", 0),
            "unseen": bool(j.get("finished") and j["finished"] > parent_seen), "review": bool(j.get("review")),
            "wait_reason": j.get("wait_reason", ""), "updated": t.get("updated", 0), "last_note": j.get("last_note", "")}


def list_tasks():
    """The chats for the sidebar, each with its jobs nested inside, and a state the list shows as a glyph."""
    with _lock:
        tasks = _load_index()
    kids = {}
    for t in tasks:
        if _is_job(t):
            kids.setdefault(t.get("parent"), []).append(t)
    out = []
    for t in tasks:
        if _is_job(t):
            continue
        s = _sessions.get(t["id"])
        seen = t.get("seen", 0)
        jobs = [_job_summary(k, seen) for k in sorted(kids.get(t["id"], []), key=lambda k: k["job"].get("started") or k.get("created", 0))]
        row = dict(t)
        row["live"] = bool(s and s.alive())
        row["running"] = bool(s and s.running)
        own_need = len(s.pending) if s else 0
        row["waiting"] = bool(own_need)
        row["jobs"] = jobs
        need = own_need + sum(j["need"] for j in jobs)
        working = row["running"] or any(j["state"] in ("queued", "working") and not j["need"] for j in jobs)
        if need:
            state = "need"
        elif working:
            state = "work"
        elif any(j["state"] == "paused" for j in jobs):
            state = "paused"
        elif any(j["state"] == "interrupted" for j in jobs):
            state = "interrupted"
        elif any(j["unseen"] and j["state"] == "done" for j in jobs):
            state = "done"
        elif any(j["unseen"] and j["state"] in ("stopped", "failed") for j in jobs):
            state = "stopped"
        else:
            state = ""
        row["state"] = state
        row["need"] = need
        live_jobs = [j for j in jobs if j["state"] == "working"]
        if state == "work" and live_jobs:
            j = live_jobs[0]
            row["doing"] = ("Round %d · %s" % (max(j["round"], 1) if j["state"] == "working" else j["round"], _lower(j["doing"]))) if j["doing"] else "Round %d" % max(j["round"], 1)
        elif state == "need":
            nj = [j for j in jobs if j["need"]]
            row["doing"] = ("Wants your answer · " + nj[0]["title"]) if nj and not own_need else "Wants your answer"
        elif state == "paused":
            row["doing"] = "Paused · " + [j for j in jobs if j["state"] == "paused"][0]["title"]
        elif row["running"]:
            row["doing"] = s.doing or "Working…"
        else:
            row["doing"] = ""
        row["sort"] = max([t.get("updated", 0)] + [j["updated"] for j in jobs])
        out.append(row)
    return sorted(out, key=lambda t: t["sort"], reverse=True)


def _lower(s):
    return s[:1].lower() + s[1:] if s else s


def state():
    cmd, where = find_pi()
    return {"pi": {"found": bool(cmd), "where": where, "version": piup.summary()["active"] and piup.summary()["active"]["version"]},
            "tasks": list_tasks(), "modes": list(MODES), "folders": folderstore.recent(8), "busy": busy()}


def _clean_folders(extra, cwd):
    out, seen = [], {os.path.realpath(cwd)} if cwd else set()
    for f in extra or []:
        if not isinstance(f, dict):
            continue
        p = os.path.realpath(os.path.expanduser(str(f.get("path", ""))))
        if not p or p in seen:
            continue
        if not os.path.isdir(p):
            raise ValueError("The folder %s doesn't exist." % p)
        seen.add(p)
        out.append({"path": p, "access": "read" if f.get("access") == "read" else "write"})
    return out


def create(cwd, mode=None, model="", extra_folders=None):
    """A task with a folder is an agent working there; without one it is a plain chat (Pi with its tools off)."""
    sid = uuid.uuid4().hex[:12]
    chat = not str(cwd or "").strip()
    if chat:
        cwd = os.path.join(store.data_dir(), "cowork", "scratch", sid)
        os.makedirs(cwd, exist_ok=True)
    cwd = os.path.realpath(os.path.expanduser(str(cwd)))
    if not os.path.isdir(cwd):
        raise ValueError("That folder doesn't exist.")
    if mode not in MODES:
        mode = (folderstore.get(cwd)["mode"] if not chat else "") or "ask"
    task = {"id": sid, "kind": "chat", "title": "New chat", "cwd": cwd, "chat": chat, "mode": mode, "model": model,
            "folders": [] if chat else _clean_folders(extra_folders, cwd), "created": time.time(), "updated": time.time(),
            "seen": time.time()}
    if not chat:
        folderstore.touch(cwd)
    with _lock:
        _save_index(_load_index() + [task])
    touch()
    return task


def delete(sid):
    t = get_task(sid)
    stop(sid)
    ids = {sid}
    if t and not _is_job(t):
        ids |= {k["id"] for k in _load_index() if _is_job(k) and k.get("parent") == sid}
    for jid in ids:
        c = _ctl.get(jid)
        if c:
            c.stop = True
            c.cont.set()
        stop(jid)
        shutil.rmtree(os.path.join(store.data_dir(), "cowork", "sessions", jid), ignore_errors=True)
        shutil.rmtree(_copy_root(jid), ignore_errors=True)
        shutil.rmtree(os.path.join(store.data_dir(), "cowork", "jobs", jid), ignore_errors=True)
    with _lock:
        _save_index([k for k in _load_index() if k["id"] not in ids])
    touch()


def set_folder(sid, cwd):
    """Give a chat a folder to work in (or move a task to another one). Takes effect on the next start."""
    t = get_task(sid)
    if _is_job(t):
        raise ValueError("A job's folder can't be changed.")
    cwd = os.path.realpath(os.path.expanduser(str(cwd or "")))
    if not os.path.isdir(cwd):
        raise ValueError("That folder doesn't exist.")
    s = _sessions.get(sid)
    if s and s.running:
        raise ValueError("Wait for it to finish its current answer first.")
    stop(sid)
    folderstore.touch(cwd)
    extra = [f for f in (t or {}).get("folders", []) if f["path"] != cwd]
    _update_meta(sid, cwd=cwd, chat=False, folders=extra)


def set_folders(sid, extra):
    """Replace the extra folders (beyond the working folder) a chat can see, each Look only or Can change."""
    t = get_task(sid)
    if not t or _is_job(t):
        raise ValueError("no such chat")
    s = _sessions.get(sid)
    if s and s.running:
        raise ValueError("Wait for it to finish its current answer first.")
    clean = _clean_folders(extra, t["cwd"])
    stop(sid)
    for f in clean:
        folderstore.touch(f["path"])
    _update_meta(sid, folders=clean)


def rename(sid, title):
    _update_meta(sid, title=str(title).strip()[:120] or "Untitled task")


def seen(sid):
    """The person has looked at this chat: finished jobs under it stop showing as new."""
    _update_meta(sid, seen=time.time())


# ---------------------------------------------------------------- one running agent
KINDS = ("think", "write", "cmd", "wait")


class Session:
    def __init__(self, task):
        self.task = task
        self.id = task["id"]
        self.cond = threading.Condition()
        self.events = []            # [(seq, record)]
        self.seq = 0
        self.running = False
        self.pending = {}           # extension_ui_request id -> record, until answered
        self.last = time.time()
        self.watchers = 0
        self.proc = None
        self.cmd_id = 0
        self.snapshot_id = None
        self.settled_seq = 0
        self.settled_n = 0
        self.ready = threading.Event()
        self.ready_rec = None
        self.doing = ""
        self.last_text = ""
        self.msg_count = 0
        self.stopping = False
        self.tl = []                # [[start, end, kind]] what the agent has been doing, for the ribbon
        self._seg = None
        if _is_job(task):
            self.tl = _load_tl(task["id"])

    # -- the activity ribbon
    def mark(self, kind):
        now = time.time()
        if self._seg and self._seg[1] == kind:
            return
        if self._seg:
            t0, k = self._seg
            if self.tl and self.tl[-1][2] == k and t0 - self.tl[-1][1] < 2:
                self.tl[-1][1] = now
            else:
                self.tl.append([t0, now, k])
            self._seg = None
        if kind:
            self._seg = (now, kind)
        if len(self.tl) > 800:
            self.tl = self.tl[-600:]

    def timeline(self):
        out = [list(x) for x in self.tl]
        if self._seg:
            out.append([self._seg[0], time.time(), self._seg[1]])
        return out

    def save_tl(self):
        if _is_job(self.task):
            _save_tl(self.id, self.timeline() if not self._seg else self.tl)

    # -- process
    def start(self):
        cmd, _ = find_pi()
        if not cmd:
            raise RuntimeError("Pi isn't installed.")
        t = self.task
        sdir = os.path.join(store.data_dir(), "cowork", "sessions", self.id)
        os.makedirs(sdir, exist_ok=True)
        args = cmd + ["--mode", "rpc", "--session-dir", sdir, "--no-approve", "-e", GATE, "-e", SKILLS_EXT]
        chat = bool(t.get("chat"))
        if chat:
            inject = skills.inline()                   # no tools in a plain chat, so the short skills are put in the prompt
            args += ["--no-skills"]
        else:
            live, _ = skills.live_dir()
            inject = skills.bootstrap()
            args += ["--no-skills", "--skill", live]
        if any(f.endswith(".jsonl") for f in os.listdir(sdir)):
            args.append("--continue")
        if chat:
            args += ["--no-tools", "--system-prompt", CHAT_PROMPT]
        else:
            note = _folders_note(t)
            if _is_job(t):
                note = (JOB_PROMPT + " " + note).strip()
            if note:
                args += ["--append-system-prompt", note]
        if t.get("model"):
            args += ["--provider", "lemonade", "--model", t["model"]]
        extra = [f for f in t.get("folders", [])]
        allow = list(folderstore.get(_origin(t))["allow"]) + list((t.get("job") or {}).get("allow", []))
        self.proc = subprocess.Popen(args, cwd=t["cwd"], env=_env(t.get("mode", "ask"), inject, extra, allow),
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()
        self.send({"type": "get_messages"}, snapshot=True)
        self.send({"type": "get_state"})

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def send(self, cmd, snapshot=False):
        self.cmd_id += 1
        cmd = dict(cmd)
        cmd.setdefault("id", "z%d" % self.cmd_id)
        if snapshot:
            self.snapshot_id = cmd["id"]
        try:
            self.proc.stdin.write((json.dumps(cmd) + "\n").encode())
            self.proc.stdin.flush()
        except (OSError, ValueError):
            self.push({"type": "zest_error", "message": "The agent stopped. Send a message to start it again."})
        return cmd["id"]

    def stop(self):
        self.stopping = True
        if self.alive():
            try:
                self.proc.stdin.close()
                self.proc.wait(5)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()
        self.running = False
        self.mark(None)
        self.save_tl()

    # -- events
    def push(self, rec):
        with self.cond:
            self.seq += 1
            self.events.append((self.seq, rec))
            self.cond.notify_all()
            seq = self.seq
        if rec.get("type") in ("agent_start", "agent_settled", "extension_ui_request", "zest_answered", "zest_exit", "zest_error",
                               "tool_execution_start", "tool_execution_end", "zest_ready", "queue_update"):
            touch()
        return seq

    def _read(self):
        for raw in self.proc.stdout:
            line = raw.rstrip(b"\r\n")
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            self._handle(rec)
        self.running = False
        self.mark(None)
        self.save_tl()
        self.push({"type": "zest_exit", "code": self.proc.poll()})

    def _read_err(self):
        tail = []
        for raw in self.proc.stderr:
            tail = (tail + [raw.decode("utf8", "replace").rstrip()])[-12:]
        if tail and self.proc.poll():
            self.err = "\n".join(tail)
            self.push({"type": "zest_error", "message": self.err})

    def _handle(self, rec):
        t = rec.get("type")
        self.last = time.time()
        if t == "response" and rec.get("id") == self.snapshot_id:
            self.snapshot_id = None
            if rec.get("success"):
                msgs = (rec.get("data") or {}).get("messages", [])
                self.msg_count = len(msgs)
                self._compact({"type": "zest_history", "messages": msgs})
            self.ready.set()
            return
        if t == "agent_start":
            self.running = True
            self.mark("think")
        elif t == "agent_settled":
            self.running = False
            self.doing = ""
            self.mark(None)
            self.save_tl()
            self.settled_n += 1
            if not _is_job(self.task):
                _update_meta(self.id, updated=time.time())
            self.settled_seq = self.push(rec)
            self.send({"type": "get_messages"}, snapshot=True)
            return
        elif t == "message_update":
            e = rec.get("assistantMessageEvent") or {}
            k = e.get("type", "")
            if k.startswith("thinking"):
                if self._seg is None or self._seg[1] != "think":
                    self.doing = "Thinking…"
                    self.mark("think")
                    touch()
            elif k.startswith("text"):
                if self._seg is None or self._seg[1] != "write":
                    self.doing = "Writing a reply…"
                    self.mark("write")
                    touch()
        elif t == "message_end":
            msg = rec.get("message") or {}
            self.msg_count += 1
            if msg.get("role") == "assistant":
                text = "\n".join(c.get("text", "") for c in (msg.get("content") or []) if c.get("type") == "text").strip()
                if text:
                    self.last_text = text
        elif t == "tool_execution_start":
            self.doing = _phrase(rec.get("toolName"), rec.get("args"))
            self.mark("write" if rec.get("toolName") in ("edit", "write") else "cmd")
        elif t == "tool_execution_end":
            self.doing = "Thinking…"
            self.mark("think")
        elif t == "extension_ui_request":
            if rec.get("method") in ("select", "confirm", "input", "editor"):
                self.pending[rec["id"]] = rec
                self.doing = "Waiting for you"
                self.mark("wait")
        elif t == "session_info_changed" and rec.get("name") and not _is_job(self.task):
            _update_meta(self.id, title=str(rec["name"])[:120])
        self.push(rec)

    def _compact(self, snap):
        """Replace everything up to now with one history record, so a reconnecting page replays a short log."""
        with self.cond:
            keep = [(self.settled_seq, self.ready_rec)] if self.ready_rec else []
            self.events = [(self.settled_seq, snap)] + keep + [e for e in self.events if e[0] > self.settled_seq]
            self.cond.notify_all()

    def answer(self, rec):
        self.pending.pop(rec.get("id"), None)
        self.proc.stdin.write((json.dumps(rec) + "\n").encode())
        self.proc.stdin.flush()
        self.doing = "Thinking…"
        self.mark("think")
        self.push({"type": "zest_answered", "id": rec.get("id")})

    def since(self, cursor, wait=15):
        with self.cond:
            if not [e for e in self.events if e[0] > cursor]:
                self.cond.wait(wait)
            return [e for e in self.events if e[0] > cursor]


def _phrase(tool, args):
    a = args or {}
    name = os.path.basename(str(a.get("path") or a.get("file_path") or "")) or "a file"
    if tool == "read":
        return "Reading " + name
    if tool == "edit":
        return "Editing " + name
    if tool == "write":
        return "Writing " + name
    if tool == "bash":
        c = " ".join(str(a.get("command", "")).split())
        return "Running " + (c[:38] + "…" if len(c) > 38 else c or "a command")
    if tool in ("grep", "find", "ls"):
        return "Searching the folder"
    return "Using " + str(tool)


def _folders_note(t):
    extra = t.get("folders") or []
    if not extra:
        return ""
    rows = ["%s (%s)" % (f["path"], "look only, do not change anything there" if f["access"] == "read" else "you may change files there")
            for f in extra]
    return "Besides your working folder you can also use these folders by absolute path: " + "; ".join(rows) + "."


def _origin(t):
    """The real folder a task belongs to: for a job working on a copy, the folder it was copied from."""
    return (t.get("job") or {}).get("origin") or t["cwd"]


# ---------------------------------------------------------------- activity ribbon on disk
def _tl_path(jid):
    return os.path.join(store.data_dir(), "cowork", "jobs", jid, "timeline.json")


def _load_tl(jid):
    try:
        with open(_tl_path(jid)) as f:
            return [list(x) for x in json.load(f)]
    except (OSError, ValueError):
        return []


def _save_tl(jid, tl):
    try:
        os.makedirs(os.path.dirname(_tl_path(jid)), exist_ok=True)
        _write(_tl_path(jid), tl[-800:])
    except OSError:
        pass


# ---------------------------------------------------------------- manager
def session(sid, start=True):
    with _lock:
        s = _sessions.get(sid)
        if s and s.alive():
            return s
    task = get_task(sid)
    if not task or not start:
        return None
    with _lock:
        gate = _start_locks.setdefault(sid, threading.Lock())
    with gate:
        with _lock:
            s = _sessions.get(sid)
            if s and s.alive():
                return s
        task = get_task(sid)
        if not task:
            return None
        models = chat_models()
        if not task.get("model") and models:
            loaded = [m.get("model_name") for m in lem.get("/v1/health").get("all_models_loaded", [])]
            task["model"] = next((m["id"] for m in models if m["id"] in loaded), models[0]["id"])
            _update_meta(sid, model=task["model"])
        ctx = model_ctx(task.get("model", "")) if task.get("model") else 0
        write_config(models, task.get("model", ""), ctx)
        s = Session(task)
        s.start()
        s.ready.wait(20)          # the transcript so far goes out before anything new happens
        s.ready_rec = {"type": "zest_ready", "model": task.get("model", ""), "ctx": ctx, "mode": task.get("mode", "ask"),
                       "cwd": task["cwd"], "chat": bool(task.get("chat")), "models": [m["id"] for m in models],
                       "kind": task.get("kind", "chat"), "folders": task.get("folders", []), "parent": task.get("parent", "")}
        s.push(s.ready_rec)
        with _lock:
            _sessions[sid] = s
        return s


def stop(sid):
    with _lock:
        s = _sessions.pop(sid, None)
    if s:
        s.stop()
    touch()


def stop_all():
    _down["on"] = True
    for sid in list(_sessions):
        stop(sid)


def command(sid, cmd):
    """Forward one browser command to Pi. A prompt on a task that is already busy becomes steering."""
    kind = cmd.get("type")
    if kind not in FORWARD:
        raise ValueError("unknown command")
    t = get_task(sid)
    if kind == "prompt" and _is_job(t):
        s = _sessions.get(sid)
        if not (s and s.running):
            # between rounds there is nothing to steer: keep the note for the next round
            j = t["job"]
            _update_job(sid, notes=list(j.get("notes", [])) + [" ".join(str(cmd.get("message", "")).split())])
            return {"ok": True, "queued": True}
    s = session(sid)
    if s is None:
        raise ValueError("no such task")
    if kind == "prompt":
        t = get_task(sid)
        if t and t["title"] in ("New task", "New chat"):
            _update_meta(sid, title=" ".join(str(cmd.get("message", "")).split())[:60] or "New chat")
        notes = (t or {}).get("pending_notes") or []
        if notes:
            cmd = dict(cmd, message="<zest-note>%s</zest-note>\n%s" % (" ".join(notes), cmd.get("message", "")))
            _update_meta(sid, pending_notes=[])
        if s.running:
            cmd = dict(cmd, streamingBehavior="steer")
    s.send(cmd)
    return {"ok": True}


def set_mode(sid, mode):
    if mode not in MODES:
        raise ValueError("unknown mode")
    t = get_task(sid)
    _update_meta(sid, mode=mode)
    if t and not t.get("chat") and not _is_job(t):
        folderstore.remember_mode(t["cwd"], mode)
    s = session(sid, start=False)
    if s:
        s.task["mode"] = mode
        s.send({"type": "prompt", "message": "/zest-mode " + mode})


def remember_allow(sid, key):
    """The person chose 'Always allow <key>' on an approval: keep it for the folder, so it holds next time."""
    t = get_task(sid)
    if t and folderstore.allow_add(_origin(t), key):
        return True
    return False


def restart_idle():
    """After the skills change, idle tasks restart on their next message; a task that is working keeps what it has."""
    for sid, s in list(_sessions.items()):
        if not s.running and not s.pending and sid not in _ctl:
            stop(sid)


def reaper():
    while True:
        time.sleep(30)
        now = time.time()
        for sid, s in list(_sessions.items()):
            if not s.alive():
                _sessions.pop(sid, None)
            elif not s.running and not s.pending and not s.watchers and sid not in _ctl and now - s.last > IDLE_STOP:
                stop(sid)
            elif s.alive():
                s.save_tl()


# ---------------------------------------------------------------- jobs
class Ctl:
    def __init__(self):
        self.stop = False
        self.pause = False
        self.cont = threading.Event()
        self.thread = None


def _copy_root(jid):
    return os.path.join(store.data_dir(), "cowork", "copies", jid)


def busy():
    """Is a job working, or a question waiting for an answer? Zest stays open while this is true, even with no browser tab.
    A job paused for you is not counted: if Zest closes meanwhile, it comes back as interrupted and can be continued."""
    for jid, c in list(_ctl.items()):
        if c.thread and c.thread.is_alive() and ((get_task(jid) or {}).get("job") or {}).get("state") in ("working", "queued"):
            return True
    return any(s.alive() and (s.running or s.pending) for s in list(_sessions.values()))


def last_activity():
    return time.time() if busy() else max([s.last for s in list(_sessions.values())] or [0])


def _short(text, n=60):
    t = " ".join(str(text or "").split())
    return t[:n].rstrip() + ("…" if len(t) > n else "")


def create_job(parent, spec):
    """Start a background job from a chat. spec: goal, check, check_kind, rounds, seconds, review, copy, allow."""
    p = get_task(parent)
    if not p or _is_job(p):
        raise ValueError("no such chat")
    goal = " ".join(str(spec.get("goal", "")).split()) if "\n" not in str(spec.get("goal", "")) else str(spec.get("goal", "")).strip()
    if not goal:
        raise ValueError("Say what the job should achieve.")
    if p.get("chat"):
        raise ValueError("Choose a folder for this chat first. A job needs somewhere to work.")
    check = str(spec.get("check", "")).strip()
    kind = spec.get("check_kind") if spec.get("check_kind") in ("command", "words") else ("command" if re.match(r"^[\w./-]+( |$)", check) and check else "words")
    if not check:
        kind = "words"
    rounds = max(1, min(int(spec.get("rounds") or DEFAULT_ROUNDS), 100))
    seconds = max(0, min(int(spec.get("seconds") if spec.get("seconds") is not None else DEFAULT_SECONDS), 48 * 3600))
    copy = bool(spec.get("copy", True))
    jid = uuid.uuid4().hex[:12]
    origin = p["cwd"]
    cwd = origin
    watched = False
    if not copy:
        os.makedirs(_copy_root(jid), exist_ok=True)
        watched = workcopy.snapshot(origin, _copy_root(jid))      # lets Changes show what a live job did
        if not watched:
            shutil.rmtree(_copy_root(jid), ignore_errors=True)
    if copy:
        os.makedirs(_copy_root(jid), exist_ok=True)
        try:
            cwd = workcopy.make(origin, _copy_root(jid))
        except (ValueError, OSError) as e:
            shutil.rmtree(_copy_root(jid), ignore_errors=True)
            raise ValueError(str(e))
    s = _sessions.get(parent)
    after = s.msg_count if s else 0
    extra = [dict(f, access="read") if copy else f for f in p.get("folders", [])]
    allow = [k for k in (folderstore.valid_key(x) for x in spec.get("allow", []) or []) if k]
    for k in allow:
        folderstore.allow_add(origin, k)
    job = {"goal": goal, "check": check, "check_kind": kind, "rounds_max": rounds, "seconds_max": seconds,
           "review": bool(spec.get("review")), "copy": copy, "watched": watched, "origin": origin, "allow": allow, "state": "queued", "round": 0,
           "results": [], "notes": [], "after_msgs": after, "spent_before": 0, "created": time.time()}
    task = {"id": jid, "kind": "job", "parent": parent, "title": _short(spec.get("title") or next((l for l in goal.splitlines() if l.strip() and not l.strip().endswith(":")), goal), 60), "cwd": cwd,
            "chat": False, "mode": "edits", "model": spec.get("model") or p.get("model", ""), "folders": extra,
            "created": time.time(), "updated": time.time(), "job": job}
    with _lock:
        _save_index(_load_index() + [task])
    _update_meta(parent, updated=time.time())
    _start_thread(jid)
    return task


def _start_thread(jid):
    c = Ctl()
    c.thread = threading.Thread(target=_job_main, args=(jid, c), daemon=True)
    _ctl[jid] = c
    c.thread.start()
    touch()


def recover():
    """Called once at startup: any job that was working when Zest closed is now interrupted, and can be continued."""
    with _lock:
        tasks = _load_index()
        changed = False
        for t in tasks:
            j = t.get("job")
            if j and j["state"] in JOB_ACTIVE:
                j["state"] = "interrupted"
                j["spent_before"] = _spent(j)
                j["resumed_at"] = None
                j["wait_reason"] = ""
                changed = True
        if changed:
            _save_index(tasks)


def _set_state(jid, state, **kw):
    j = (get_task(jid) or {}).get("job") or {}
    extra = dict(kw)
    now = time.time()
    if state == "working" and not j.get("resumed_at"):
        extra["resumed_at"] = now
    if state != "working" and j.get("resumed_at"):
        extra["spent_before"] = _spent(j)
        extra["resumed_at"] = None
    _update_job(jid, state=state, **extra)


def _claim(cwd, jid):
    with _lock:
        owner = _owner.get(cwd)
        if owner and owner != jid and owner in _ctl and _ctl[owner].thread.is_alive():
            return False
        _owner[cwd] = jid
        return True


def _release(cwd, jid):
    with _lock:
        if _owner.get(cwd) == jid:
            _owner.pop(cwd, None)


def _job_main(jid, c):
    t = get_task(jid)
    cwd = t["cwd"]
    live = not t["job"].get("copy")
    try:
        _job_loop(jid, c, live)
    except Exception as e:                                   # a bug or a crash must end the job visibly, never hang it
        if not _down["on"]:
            _finish(jid, "failed", "Something went wrong: %s" % e)
    finally:
        if live:
            _release(cwd, jid)
        stop(jid)
        _ctl.pop(jid, None)
        touch()


def _job_loop(jid, c, live):
    t = get_task(jid)
    j = t["job"]
    cwd = t["cwd"]
    if live:
        while not _claim(cwd, jid):
            _set_state(jid, "queued")
            if c.stop:
                return _finish(jid, "stopped", "Stopped before it started.")
            time.sleep(1)
    _set_state(jid, "working", started=j.get("started") or time.time(), wait_reason="")
    if j["check_kind"] == "command" and not j["results"]:
        res = _run_check(jid, j["check"], cwd)
        base = dict(res, n=0, t1=time.time(), note="Before the job started")
        _update_job(jid, results=[base])
        if res["ok"]:
            return _finish(jid, "done", "The check already passes, so there was nothing to do.")
    while True:
        j = get_task(jid)["job"]
        if c.stop:
            return _finish(jid, "stopped", "Stopped by you.")
        if c.pause:
            c.cont.clear()
            _set_state(jid, "paused", wait_reason=j.get("wait_reason") or "pause")
            while not c.cont.wait(1):
                if c.stop:
                    break
            c.pause = False
            if c.stop:
                continue
            _set_state(jid, "working", wait_reason="")
            continue
        n = j["round"] + 1
        if n > j["rounds_max"]:
            return _finish(jid, "stopped", "Reached the limit of %d rounds." % j["rounds_max"])
        if j["seconds_max"] and _spent(j) >= j["seconds_max"]:
            return _finish(jid, "stopped", "Reached the time limit.")
        outcome = _round(jid, c, n)
        if _down["on"]:
            return
        if outcome == "stop":
            return _finish(jid, "stopped", "Stopped by you.")
        if outcome == "pause":
            continue
        if outcome == "time":
            return _finish(jid, "stopped", "Reached the time limit.")
        if outcome == "died":
            if _down["on"]:
                return
            s = _sessions.get(jid)
            return _finish(jid, "failed", "The agent stopped unexpectedly. " + (getattr(s, "err", "") or "")[-300:])
        j = get_task(jid)["job"]
        last = j["results"][-1]
        if last.get("ok"):
            return _finish(jid, "done", last.get("done_reason") or "The check passes.")
        series = [r.get("metric") for r in j["results"] if r.get("metric") is not None]
        if len(series) >= 3 and series[-1] >= series[-3] and len(series) == len(j["results"]):
            return _finish(jid, "stopped", "The failure count stopped falling, so it stopped to avoid going in circles.")
        if j.get("review"):
            _update_job(jid, wait_reason="review")
            c.pause = True


def _round(jid, c, n):
    """Run one round: prompt Pi, wait for it to settle, then check. Returns settled | pause | stop | time | died."""
    j = get_task(jid)["job"]
    s = session(jid)
    before = s.settled_n
    s.last_text = ""
    notes = j.get("notes", [])
    if notes:
        _update_job(jid, notes=[])
    prompt = _round_prompt(j, n, notes)
    s.send({"type": "prompt", "message": prompt})
    t0 = time.time()
    while True:
        with s.cond:
            s.cond.wait(1.0)
        if s.settled_n > before:
            break
        if not s.alive():
            return "died"
        cur = get_task(jid)["job"]
        over = cur["seconds_max"] and _spent(cur) >= cur["seconds_max"]
        if c.stop or c.pause or over:
            s.send({"type": "abort"})
            for _ in range(40):
                with s.cond:
                    s.cond.wait(0.5)
                if s.settled_n > before or not s.alive():
                    break
            else:
                s.proc.kill()
            return "stop" if c.stop else "time" if over else "pause"
    text = s.last_text
    res = {"n": n, "t0": t0, "t1": time.time(), "ok": False, "metric": None, "summary": "", "tail": "", "note": _note(text)}
    j = get_task(jid)["job"]
    if j["check_kind"] == "command":
        s.mark("cmd")
        chk = _run_check(jid, j["check"], get_task(jid)["cwd"])
        s.mark(None)
        res.update(ok=chk["ok"], metric=chk["metric"], summary=chk["summary"], tail=chk["tail"])
        if chk["ok"]:
            res["done_reason"] = "The check passes."
    else:
        done = bool(re.search(r"^\s*STATUS:\s*DONE\b", text, re.I | re.M))
        res.update(ok=done, summary="The model says it is done." if done else "Not done yet.")
        if done:
            res["done_reason"] = "The model reports the goal is met."
    _update_job(jid, results=list(j["results"]) + [res], round=n, last_note=res["note"])
    return "settled"


def _note(text):
    t = re.sub(r"^\s*STATUS:.*$", "", str(text or ""), flags=re.I | re.M).strip()
    first = re.split(r"(?<=[.!?])\s|\n", t, 1)[0] if t else ""
    return _short(first, 140)


def _round_prompt(j, n, notes):
    goal = j["goal"]
    check = j.get("check", "")
    lines = []
    if n == 1:
        lines.append("Goal: " + goal)
        if j["check_kind"] == "command":
            base = (j.get("results") or [{}])[0]
            lines.append("It is done when this command passes: " + check)
            if base.get("summary"):
                lines.append("Right now it gives: %s.%s" % (base["summary"], ("\n```\n%s\n```" % base["tail"]) if base.get("tail") else ""))
        elif check:
            lines.append("It is done when: " + check)
        lines.append("Work on it now. Stop when you have made a round of progress and say in one sentence what you changed.")
    else:
        prev = j["results"][-1] if j.get("results") else {}
        lines.append("Round %d." % n)
        if prev.get("summary"):
            lines.append("After the last round the check says: %s.%s" % (prev["summary"], ("\n```\n%s\n```" % prev["tail"]) if prev.get("tail") and not prev.get("ok") else ""))
        lines.append("Keep working toward the goal: " + goal)
    if notes:
        lines.append("The user added a note while you worked: " + " ".join(notes))
    if j["check_kind"] != "command":
        lines.append("When the goal is fully met, end your message with a line that says exactly: STATUS: DONE")
    return "\n\n".join(lines)


_COUNT = re.compile(r"(\d+)\s+(failed|failing|failures?|errors?|problems?)\b", re.I)


def _run_check(jid, cmd, cwd):
    """Run the person's check command in the job's folder (the copy, when there is one)."""
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return {"ok": False, "metric": None, "summary": "The check took too long", "tail": ""}
    except OSError as e:
        return {"ok": False, "metric": None, "summary": "The check couldn't run: %s" % e, "tail": ""}
    out = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
    lines = out.splitlines()
    metric = None
    found = _COUNT.findall("\n".join(lines[-25:]))
    if found:
        metric = sum(int(n) for n, _ in found)
    ok = r.returncode == 0
    if ok:
        metric = 0
    tail = "\n".join(lines[-14:])[-900:]
    summary = "It passes" if ok else ("%d failing" % metric if metric is not None else "It fails (exit %d)" % r.returncode)
    return {"ok": ok, "metric": metric, "summary": summary, "tail": "" if ok else tail}


def _finish(jid, state, reason):
    t = get_task(jid)
    if not t:
        return
    j = t["job"]
    _set_state(jid, state, finished=time.time(), stop_reason=reason, wait_reason="")
    parent = t.get("parent")
    if parent and get_task(parent):
        p = get_task(parent)
        where = ""
        if j.get("copy") and state == "done":
            where = " Its changes are in a copy of the folder and have not been applied yet."
        line = "The job \"%s\" %s after %d round%s: %s%s" % (t["title"], {"done": "finished", "stopped": "stopped", "failed": "failed"}[state],
                                                              j.get("round", 0), "" if j.get("round", 0) == 1 else "s", reason, where)
        _update_meta(parent, pending_notes=list(p.get("pending_notes") or []) + [line], updated=time.time())


def job_detail(jid):
    t = get_task(jid)
    if not _is_job(t):
        raise ValueError("no such job")
    parent = get_task(t.get("parent")) or {}
    d = _job_summary(t, parent.get("seen", 0))
    j = t["job"]
    s = _sessions.get(jid)
    tl = s.timeline() if s else _load_tl(jid)
    now = time.time()
    d.update(results=[{k: r.get(k) for k in ("n", "ok", "metric", "summary", "note", "t1")} for r in j.get("results", [])],
             timeline=[x for x in tl if x[1] > now - 3600], now=now, origin=j.get("origin", ""), cwd=t["cwd"],
             parent=t.get("parent", ""), mode=t.get("mode", "edits"), allow=j.get("allow", []), notes=j.get("notes", []))
    return d


def ctl_job(jid, action):
    t = get_task(jid)
    if not _is_job(t):
        raise ValueError("no such job")
    j = t["job"]
    c = _ctl.get(jid)
    alive = bool(c and c.thread and c.thread.is_alive())
    if action == "pause":
        if alive and j["state"] == "working":
            c.pause = True
            touch()
    elif action in ("resume", "continue"):
        if alive and j["state"] == "paused":
            _update_job(jid, wait_reason="")
            c.pause = False
            c.cont.set()
        elif not alive and j["state"] == "interrupted":
            _update_job(jid, wait_reason="")
            _start_thread(jid)
    elif action == "stop":
        if alive:
            c.stop = True
            c.cont.set()
        elif j["state"] in ("interrupted", "paused", "queued"):
            _finish(jid, "stopped", "Stopped by you.")
    elif action == "remove":
        if alive:
            raise ValueError("Stop the job first.")
        delete(jid)
    else:
        raise ValueError("unknown action")
    touch()
    return {"ok": True}


def job_changes(jid):
    t = get_task(jid)
    if _is_job(t) and t["job"].get("watched") and os.path.isdir(_copy_root(jid)):
        return {"mode": "live", "files": workcopy.changes(_copy_root(jid), t["job"]["origin"]), "state": "", "origin": t["job"]["origin"]}
    if not _is_job(t) or not t["job"].get("copy") or t["job"].get("copy_state") in ("applied", "discarded"):
        return {"mode": "none", "files": [], "state": (t or {}).get("job", {}).get("copy_state", "")}
    if not os.path.isdir(_copy_root(jid)):
        return {"mode": "none", "files": [], "state": "gone"}
    return {"mode": "copy", "files": workcopy.changes(_copy_root(jid)), "state": "", "origin": t["job"]["origin"]}


def job_diff(jid, rel):
    t = get_task(jid)
    if _is_job(t) and t["job"].get("watched"):
        return workcopy.diff(_copy_root(jid), rel, t["job"]["origin"])
    if not _is_job(t) or not t["job"].get("copy"):
        raise ValueError("no copy to compare")
    return workcopy.diff(_copy_root(jid), rel)


def job_apply(jid, only=None):
    t = get_task(jid)
    if not _is_job(t) or not t["job"].get("copy"):
        raise ValueError("This job has no copy to apply.")
    if t["job"]["state"] in JOB_ACTIVE:
        raise ValueError("Wait for the job to finish, or stop it, before applying its changes.")
    if t["job"].get("copy_state") in ("applied", "discarded"):
        raise ValueError("Its changes were already %s." % t["job"]["copy_state"])
    res = workcopy.apply(_copy_root(jid), t["job"]["origin"], set(only) if only else None)
    left = workcopy.changes(_copy_root(jid))
    if not left:
        _update_job(jid, copy_state="applied")
    return dict(res, remaining=len(left))


def job_discard(jid):
    t = get_task(jid)
    if not _is_job(t) or not t["job"].get("copy"):
        raise ValueError("This job has no copy.")
    if t["job"]["state"] in JOB_ACTIVE:
        raise ValueError("Stop the job first.")
    workcopy.remove(_copy_root(jid))
    _update_job(jid, copy_state="discarded")
    return {"ok": True}
