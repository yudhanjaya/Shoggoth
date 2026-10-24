"""Cowork: runs the Pi coding agent (https://pi.dev) against Zest's own Lemonade and relays it to the browser.

One `pi --mode rpc` child process per Cowork task, talking JSON lines over stdin/stdout. Zest keeps each task's
event stream in memory so the page can reconnect, stores the task list in its data folder, and leaves the transcript
itself to Pi (which writes it to a session file we point at a per-task folder). Standard library only.
"""
import json
import os
import shutil
import subprocess
import threading
import time
import uuid

import lem
import piup
import skills
import store

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "pi", "zest-gate.js")
SKILLS_EXT = os.path.join(HERE, "pi", "zest-skills.js")
MODES = ("ask", "edits", "auto", "plan")
FORWARD = {"prompt", "steer", "follow_up", "abort", "clear_queue", "get_state", "get_session_stats", "set_model",
           "set_thinking_level", "compact", "get_available_models", "get_commands", "new_session"}
IDLE_STOP = 20 * 60          # a quiet task's process is stopped after this long; reopening it resumes the transcript
CHAT_PROMPT = ("You are Zest, a friendly assistant running on the user's own computer. Answer clearly and concisely. "
               "You cannot see or change any files in this conversation; if the user wants that, tell them to choose a folder to work in.")
NOT_CHAT = ("sd-cpp", "whispercpp", "kokoro", "moonshine", "acestep", "thinksound", "trellis", "openmoss")

_lock = threading.RLock()
_sessions = {}               # id -> Session (running processes only)
_zest_port = {"v": 13306}


def set_zest_port(port):
    _zest_port["v"] = int(port)


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
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def _env(mode="ask", inject=()):
    e = dict(os.environ)
    e.update(PI_CODING_AGENT_DIR=agent_dir(), PI_OFFLINE="1", PI_TELEMETRY="0", PI_SKIP_VERSION_CHECK="1",
             ZEST_APPROVAL=mode, ZEST_INJECT=json.dumps(list(inject)), NO_COLOR="1")
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


def list_tasks():
    with _lock:
        tasks = _load_index()
    for t in tasks:
        s = _sessions.get(t["id"])
        t["live"] = bool(s and s.alive())
        t["running"] = bool(s and s.running)
        t["waiting"] = bool(s and s.pending)
    return sorted(tasks, key=lambda t: t.get("updated", 0), reverse=True)


def state():
    cmd, where = find_pi()
    return {"pi": {"found": bool(cmd), "where": where, "version": piup.summary()["active"] and piup.summary()["active"]["version"]}, "tasks": list_tasks(), "modes": list(MODES)}


def create(cwd, mode="ask", model=""):
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
        mode = "ask"
    task = {"id": sid, "title": "New chat", "cwd": cwd, "chat": chat, "mode": mode, "model": model,
            "created": time.time(), "updated": time.time()}
    with _lock:
        _save_index(_load_index() + [task])
    return task


def delete(sid):
    stop(sid)
    with _lock:
        _save_index([t for t in _load_index() if t["id"] != sid])
    shutil.rmtree(os.path.join(store.data_dir(), "cowork", "sessions", sid), ignore_errors=True)


def set_folder(sid, cwd):
    """Give a chat a folder to work in (or move a task to another one). Takes effect on the next start."""
    cwd = os.path.realpath(os.path.expanduser(str(cwd or "")))
    if not os.path.isdir(cwd):
        raise ValueError("That folder doesn't exist.")
    stop(sid)
    _update_meta(sid, cwd=cwd, chat=False)


def rename(sid, title):
    _update_meta(sid, title=str(title).strip()[:120] or "Untitled task")


# ---------------------------------------------------------------- one running agent
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
        self.ready = threading.Event()
        self.ready_rec = None

    # -- process
    def start(self):
        cmd, _ = find_pi()
        if not cmd:
            raise RuntimeError("Pi isn't installed.")
        sdir = os.path.join(store.data_dir(), "cowork", "sessions", self.id)
        os.makedirs(sdir, exist_ok=True)
        args = cmd + ["--mode", "rpc", "--session-dir", sdir, "--no-approve", "-e", GATE, "-e", SKILLS_EXT]
        chat = bool(self.task.get("chat"))
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
        if self.task.get("model"):
            args += ["--provider", "lemonade", "--model", self.task["model"]]
        self.proc = subprocess.Popen(args, cwd=self.task["cwd"], env=_env(self.task.get("mode", "ask"), inject),
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
        if self.alive():
            try:
                self.proc.stdin.close()
                self.proc.wait(5)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()
        self.running = False

    # -- events
    def push(self, rec):
        with self.cond:
            self.seq += 1
            self.events.append((self.seq, rec))
            self.cond.notify_all()
            return self.seq

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
        self.push({"type": "zest_exit", "code": self.proc.poll()})

    def _read_err(self):
        tail = []
        for raw in self.proc.stderr:
            tail = (tail + [raw.decode("utf8", "replace").rstrip()])[-12:]
        if tail and self.proc.poll():
            self.push({"type": "zest_error", "message": "\n".join(tail)})

    def _handle(self, rec):
        t = rec.get("type")
        self.last = time.time()
        if t == "response" and rec.get("id") == self.snapshot_id:
            self.snapshot_id = None
            if rec.get("success"):
                self._compact({"type": "zest_history", "messages": (rec.get("data") or {}).get("messages", [])})
            self.ready.set()
            return
        if t == "agent_start":
            self.running = True
        elif t == "agent_settled":
            self.running = False
            _update_meta(self.id, updated=time.time())
            self.settled_seq = self.push(rec)
            self.send({"type": "get_messages"}, snapshot=True)
            return
        elif t == "extension_ui_request":
            if rec.get("method") in ("select", "confirm", "input", "editor"):
                self.pending[rec["id"]] = rec
        elif t == "session_info_changed" and rec.get("name"):
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
        self.push({"type": "zest_answered", "id": rec.get("id")})

    def since(self, cursor, wait=15):
        with self.cond:
            if not [e for e in self.events if e[0] > cursor]:
                self.cond.wait(wait)
            return [e for e in self.events if e[0] > cursor]


# ---------------------------------------------------------------- manager
def get_task(sid):
    for t in _load_index():
        if t["id"] == sid:
            return t
    return None


def session(sid, start=True):
    with _lock:
        s = _sessions.get(sid)
        if s and s.alive():
            return s
        task = get_task(sid)
        if not task or not start:
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
                       "cwd": task["cwd"], "chat": bool(task.get("chat")), "models": [m["id"] for m in models]}
        s.push(s.ready_rec)
        _sessions[sid] = s
        return s


def stop(sid):
    with _lock:
        s = _sessions.pop(sid, None)
    if s:
        s.stop()


def stop_all():
    for sid in list(_sessions):
        stop(sid)


def command(sid, cmd):
    """Forward one browser command to Pi. A prompt on a task that is already busy becomes steering."""
    kind = cmd.get("type")
    if kind not in FORWARD:
        raise ValueError("unknown command")
    s = session(sid)
    if s is None:
        raise ValueError("no such task")
    if kind == "prompt":
        t = get_task(sid)
        if t and t["title"] in ("New task", "New chat"):
            _update_meta(sid, title=" ".join(str(cmd.get("message", "")).split())[:60] or "New chat")
        if s.running:
            cmd = dict(cmd, streamingBehavior="steer")
    s.send(cmd)
    return {"ok": True}


def set_mode(sid, mode):
    if mode not in MODES:
        raise ValueError("unknown mode")
    _update_meta(sid, mode=mode)
    s = session(sid)
    if s:
        s.task["mode"] = mode
        s.send({"type": "prompt", "message": "/zest-mode " + mode})


def restart_idle():
    """After the skills change, idle tasks restart on their next message; a task that is working keeps what it has."""
    for sid, s in list(_sessions.items()):
        if not s.running and not s.pending:
            stop(sid)


def reaper():
    while True:
        time.sleep(60)
        now = time.time()
        for sid, s in list(_sessions.items()):
            if not s.alive():
                _sessions.pop(sid, None)
            elif not s.running and not s.pending and not s.watchers and now - s.last > IDLE_STOP:
                stop(sid)
