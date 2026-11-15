"""Finding the inference servers already running on this computer, on any port, and saying what each one is.

1. List what is listening: LISTEN sockets from /proc/net/tcp and tcp6, matched to a process through /proc/<pid>/fd for the processes
   Shoggoth may read (the others' ports are still probed, just unnamed).
2. Ask each port what it is: `GET /v1/models`, then the engine definitions (engines.py) decide, first match wins: the process name, then
   a signature in a reply, then the category the candidates share ("Specialized inference engine"), else "OpenAI-style server on port N".
3. Read the context length the definition says where to find.

It only reads and sends GETs. It never starts or stops anything, never looks at other computers, and runs only when asked (start-up,
opening the Models page, Look again), never on a timer. The /proc folder can be passed in, so tests use a fake one. Standard library only.
"""
import concurrent.futures
import http.client
import ipaddress
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import engines
import hf
import llamacpp
import provbase
import recs
import servers
import store

PROC = "/proc"
TIMEOUT = 1.5
WORKERS = 8
FRESH = 20                                  # seconds a scan is reused when the page opens the picker again
TEXT_LIMIT = 64 * 1024                     # bytes of a plain-text reply a signature may look through
LABELS = {"specialized": "Specialized inference engine", "local": "Local inference engine", "hosted": "Hosted service",
          "shared": "Inference engine"}
_lock = threading.Lock()
_last = {"at": 0.0, "found": []}


# ---------------------------------------------------------------- what is listening
def _ip(hexaddr):
    raw = bytes.fromhex(hexaddr)
    if len(raw) == 4:
        return ipaddress.IPv4Address(raw[::-1])
    return ipaddress.IPv6Address(b"".join(raw[i:i + 4][::-1] for i in range(0, 16, 4)))     # four words, each little-endian


def parse_tcp(text):
    """[(ip, port, inode)] for the LISTEN rows (state 0A) of one /proc/net/tcp or tcp6 file."""
    out = []
    for ln in text.splitlines():
        f = ln.split()
        if len(f) < 10 or f[3] != "0A":
            continue
        try:
            addr, _, port = f[1].rpartition(":")
            out.append((_ip(addr), int(port, 16), int(f[9])))
        except ValueError:
            continue
    return out


def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read(65536)
    except OSError:
        return b""


def owners(proc, inodes):
    """{inode: (pid, process name, first two words of the command, working folder)} for the sockets in `inodes` that belong to a process we may read."""
    want, out = {"socket:[%d]" % i: i for i in inodes}, {}
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return out
    for pid in pids:
        try:
            fds = os.listdir(os.path.join(proc, pid, "fd"))
        except OSError:
            continue                                       # someone else's process, or gone
        mine = []
        for fd in fds:
            try:
                t = os.readlink(os.path.join(proc, pid, "fd", fd))
            except OSError:
                continue
            if t in want and want[t] not in out:
                mine.append(want[t])
        if not mine:
            continue
        comm = _read(os.path.join(proc, pid, "comm")).decode("utf8", "replace").strip()
        words = [w for w in _read(os.path.join(proc, pid, "cmdline")).decode("utf8", "replace").split("\0") if w][:2]
        try:
            cwd = os.readlink(os.path.join(proc, pid, "cwd"))
        except OSError:
            cwd = ""
        for i in mine:
            out[i] = (int(pid), comm, " ".join(words), cwd)
    return out


def listening(proc=PROC):
    """Every port in LISTEN state, once: [{port, host, pid, process, cmd, cwd}] with `host` the address to probe. pid is 0 when unreadable."""
    rows = []
    for name in ("tcp", "tcp6"):
        rows += parse_tcp(_read(os.path.join(proc, "net", name)).decode("utf8", "replace"))
    own = owners(proc, {r[2] for r in rows})
    by_port = {}
    for ip, port, inode in rows:
        # `::` may be IPv6-only, so probe it at ::1 (a dual-stack socket answers there too); only 0.0.0.0 maps to 127.0.0.1.
        host = "127.0.0.1" if (ip.version == 4 and (ip.is_unspecified or ip.is_loopback)) else "[::1]" if (ip.is_unspecified or ip.is_loopback) else \
            "[%s]" % ip if ip.version == 6 else str(ip)
        pid, comm, cmd, cwd = own.get(inode, (0, "", "", ""))
        cur = by_port.get(port)
        if cur is None or (not cur["pid"] and pid) or (host == "127.0.0.1" and cur["host"] != "127.0.0.1"):
            by_port[port] = {"port": port, "host": host, "pid": pid or (cur or {}).get("pid", 0),
                             "process": comm or (cur or {}).get("process", ""), "cmd": cmd or (cur or {}).get("cmd", ""),
                             "cwd": cwd or (cur or {}).get("cwd", "")}
    return [by_port[p] for p in sorted(by_port)]


# ---------------------------------------------------------------- asking a port
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None                                  # a 3xx is just an answer; a local service can't send Shoggoth elsewhere


_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def _get(base, path, key="", timeout=TIMEOUT):
    """(status, lower-case headers, parsed JSON or None, the first 64 KB as text) for one GET. Status 0 when nothing HTTP answers."""
    h = {"User-Agent": "Shoggoth/" + hf.VERSION}
    if key:
        h["Authorization"] = "Bearer " + key
    try:
        with _opener.open(urllib.request.Request(base + path, headers=h), timeout=timeout) as r:
            status, head, raw = r.status, r.headers, r.read(1_000_000)
    except urllib.error.HTTPError as e:
        try:
            raw = e.read(64_000)                     # an error's body says whether it's an API asking for a key
        except OSError:
            raw = b""
        status, head = e.code, e.headers
    except (OSError, ValueError, http.client.HTTPException):      # not HTTP at all
        return 0, {}, None, ""
    try:
        d = json.loads(raw) if raw else None
    except ValueError:
        d = None
    return status, {k.lower(): v for k, v in head.items()}, d, raw[:TEXT_LIMIT].decode("utf8", "replace")


def _process_hit(d, comm, cmd, cwd=""):
    """True when a definition names this process: its `match` list holds its name or a word of its command (a path counts by its last
    part), or its `cwd` list holds part of the folder the command runs in. `cwd` is for engines that only say `python main.py`."""
    words = [comm] + cmd.split()
    toks = {w.lower() for w in words if w} | {os.path.basename(w).lower() for w in words if w}
    joined = (comm + " " + cmd).lower()
    for m in d["match"]:
        m = m.lower()
        if m in toks or ((" " in m or "." in m) and m in joined):
            return True
    if cwd:
        parts = [p.lower() for p in re.split(r"[\\/]+", cwd) if p]
        return any(m.lower() in p for m in d.get("cwd") or [] for p in parts)
    return False


def _signature_hit(sig, fetch):
    status, head, d, text = fetch(sig["path"])
    if status != 200:
        return False
    if "header" in sig:
        v = head.get(sig["header"].lower(), "")
        return (sig["equals"].lower() == v.lower() if "equals" in sig else sig["contains"].lower() in v.lower()) if v else False
    if "json" in sig:
        got = [x for x in provbase.jpath(d, sig["json"]) if x is not None] if d is not None else []
        if "equals" in sig:
            return any(str(x) == sig["equals"] for x in got)
        if "contains" in sig:
            return any(sig["contains"] in str(x) for x in got)
        return bool(got)
    if "contains" in sig:
        return sig["contains"].lower() in (text or "").lower()
    return True


def identify(defs, port, comm, cmd, fetch, cwd=""):
    """What is on this port. `fetch(path)` -> (status, headers, json, text). Returns {engine, name, category, how, candidates} where how
    is process | signature | port | category | generic, and candidates is [{id, name}] (more than one only when how is category)."""
    def named(d, how):
        return {"engine": d["id"], "name": d["name"], "category": d["category"], "how": how, "candidates": [{"id": d["id"], "name": d["name"]}]}
    for d in defs:
        if (d["match"] or d.get("cwd")) and _process_hit(d, comm, cmd, cwd):
            return named(d, "process")
    cache = {}

    def once(path):
        if path not in cache:
            cache[path] = fetch(path)
        return cache[path]
    withsig = sorted((d for d in defs if d["identify"]), key=lambda d: port not in d["ports"])        # the ones that usually live here first
    for d in withsig:
        if any(_signature_hit(s, once) for s in d["identify"]):
            return named(d, "signature")
    left = [d for d in defs if port in d["ports"] and not d["identify"]]       # a definition with a signature that failed is ruled out
    if len(left) == 1:
        return named(left[0], "port")
    if len(left) > 1:
        # Several engines use this port and none says which it is. One category when they share one; otherwise "shared",
        # specialized engines listed first, since those are the ones that most often sit on the common ports.
        cats = {d["category"] for d in left}
        cat = left[0]["category"] if len(cats) == 1 else "shared"
        left = sorted(left, key=lambda d: d["category"] != "specialized")          # stable: otherwise the definitions' own order
        return {"engine": None, "name": LABELS[cat] + ("" if len(cats) == 1 else " on port %d" % port), "category": cat, "how": "category",
                "candidates": [{"id": d["id"], "name": d["name"], "category": d["category"]} for d in left]}
    return {"engine": None, "name": "OpenAI-style server on port %d" % port, "category": None, "how": "generic", "candidates": []}


def _ctx_of(m):
    meta = m.get("meta") if isinstance(m.get("meta"), dict) else {}
    for v in (meta.get("n_ctx"), m.get("max_model_len"), m.get("context_length"), m.get("context_window")):
        if isinstance(v, int) and not isinstance(v, bool) and v > 0:
            return v
    return 0


SPEECH_WORDS = {"whisper", "parakeet", "kokoro", "tts", "asr", "stt", "piper", "bark", "xtts", "canary", "moonshine", "voxtral", "sensevoice",
                "transcribe", "transcription", "speech", "wav2vec", "vits", "voice", "voices", "chatterbox", "omnivoice"}
EMBED_WORDS = {"bge", "e5", "embed", "embedding", "embeddings", "minilm", "gte", "rerank", "reranker", "colbert"}
CHAT_CAPS = {"chat", "completion", "completions", "chat_completion", "chat-completion", "text", "text-generation", "generate", "tools", "vision"}


def _words(*vals):
    return {w for v in vals if isinstance(v, str) for w in re.split(r"[^a-z0-9]+", v.lower()) if w}


def _kind_of_words(ws):
    if ws & SPEECH_WORDS or any(w.endswith("tts") for w in ws):         # LuxTTS, OpenTTS: one word, a speech engine
        return "speech"
    if ws & EMBED_WORDS:
        return "embeddings"
    return ""


def model_kind(m):
    """chat | speech | embeddings | other | unknown for one /v1/models item. "chat" only on evidence; "unknown" when nothing says."""
    arch = m.get("architecture") if isinstance(m.get("architecture"), dict) else {}
    out = arch.get("output_modalities", m.get("output_modalities"))
    if isinstance(out, list) and out:
        o = {str(x).lower() for x in out}
        if "text" not in o:
            return "speech" if o & {"audio", "speech"} else "embeddings" if o & {"embedding", "embeddings"} else "other"
    caps = m.get("capabilities")
    if isinstance(caps, dict):
        caps = [k for k, v in caps.items() if v]
    if isinstance(caps, list) and caps:
        c = {str(x).lower() for x in caps}
        if c & CHAT_CAPS:
            return "chat"
        return _kind_of_words(_words(*c)) or "other"
    for k in ("type", "task", "object"):
        v = m.get(k)
        if isinstance(v, str):
            w = _words(v)
            if k == "object" and w <= {"model"}:
                continue
            if _kind_of_words(w):
                return _kind_of_words(w)
    return _kind_of_words(_words(str(m.get("id", "")), m.get("owned_by"), m.get("description"), m.get("name"))) or "unknown"


def classify(items):
    """(chat_models, role) for a list of model items. With nothing listed the server stays "chat": it may just have nothing loaded."""
    if not items:
        return 0, "chat"
    kinds = [model_kind(m) for m in items]
    sure, unknown = kinds.count("chat"), kinds.count("unknown")
    other = len(kinds) - sure - unknown
    # A list that is mostly speech or embedding models, with nothing that says chat, is that kind of server: Voicebox lists voice
    # names (tada, kokoro) beside its TTS aliases, and the names alone don't give the rest away.
    if sure == 0 and other > unknown:
        return 0, "speech" if "speech" in kinds else "embeddings" if "embeddings" in kinds else "other"
    return sure + unknown, "chat"


def _explain(r):
    if r["how"] == "category":
        names = [c["name"] for c in r["candidates"]]
        if r["category"] == "specialized":
            return ("One of the engines built for running very large models on home hardware, such as %s. They all serve an OpenAI-style "
                    "API, so Shoggoth can use this one without knowing which it is." % ", ".join(names))
        if r["category"] == "shared":
            spec = [c["name"] for c in r["candidates"] if c.get("category") == "specialized"]
            rest = [c["name"] for c in r["candidates"] if c.get("category") != "specialized"]
            parts = (["engines built for very large models on home hardware (%s)" % ", ".join(spec)] if spec else []) + \
                    (["other local engines (%s)" % ", ".join(rest)] if rest else [])
            return ("Several engines use port %d by default: %s. It serves an OpenAI-style API, so Shoggoth can use it without "
                    "knowing which one it is. Tell Shoggoth which it is, and it will remember." % (r["port"], " and ".join(parts)))
        return "Could be any of %s. Shoggoth can use it without knowing which." % ", ".join(names)
    if r["how"] == "generic":
        return "It speaks the OpenAI API, but Shoggoth can't tell which program it is."
    return ""


def _look(s, defs, timeout):
    """Everything one port has to say, or None when it isn't an inference server."""
    base = "http://%s:%d" % (s["host"], s["port"])
    status, _, d, _t = _get(base, "/v1/models", timeout=timeout)
    d = d if isinstance(d, dict) else {}
    if status == 0:
        return None                                            # not HTTP, or not answering
    who = identify(defs, s["port"], s["process"], s["cmd"], lambda p: _get(base, p, timeout=timeout), s.get("cwd", ""))
    items = [m for m in (d.get("data") if isinstance(d, dict) else None) or [] if isinstance(m, dict) and m.get("id")]
    speaks = status in (401, 403) or (status == 200 and isinstance(d, dict) and isinstance(d.get("data"), list))
    if not speaks and who["how"] not in ("process", "signature"):
        return None
    ctx = 0
    dd = next((x for x in defs if x["id"] == who["engine"]), None)
    if dd and dd["context"]:
        st, _, cd, _t = _get(base, dd["context"]["path"], timeout=timeout)
        got = [x for x in provbase.jpath(cd, dd["context"]["json"]) if isinstance(x, int) and not isinstance(x, bool) and x > 0] if st == 200 and cd is not None else []
        ctx = got[0] if got else 0
    models = [{"id": str(m["id"]), "title": recs.title_for(str(m["id"])) or str(m["id"]), "ctx": _ctx_of(m) or ctx} for m in items]
    chat, role = classify(items)
    if not items and who["how"] not in ("process", "signature") and (status == 200 or not d):
        role = "other"          # unnamed with nothing to offer, or a bare 401 with no body (OpenAI-style servers explain themselves): not a card
    r = dict(s, url=base, chat_models=chat, role=role, preset=who["engine"] or "other", needs_key=status in (401, 403), models=models, context=ctx, **who)
    r["explain"] = _explain(r)
    return r


def scan(proc=PROC, defs=None, own_ports=(), timeout=TIMEOUT):
    """The inference servers listening here, not counting Shoggoth's own: [{port, host, url, pid, process, cmd, engine, name, category, how,
    candidates, preset, needs_key, models: [{id, ctx}], chat_models, role, context, explain}], by port."""
    defs = engines.definitions() if defs is None else defs
    mine = set(own_ports)
    socks = [s for s in listening(proc) if s["port"] not in mine and s["pid"] != os.getpid()]
    if not socks:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        got = list(pool.map(lambda s: _safe(_look, s, defs, timeout), socks))
    return [g for g in got if g]


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001 - one odd port must not stop the rest being listed
        return None


# ---------------------------------------------------------------- what the page shows
def _own(extra):
    s = store.load()
    ports = set(extra)
    for k in ("gateway", "builtin"):
        p = (s.get(k) or {}).get("port")
        if isinstance(p, int):
            ports.add(p)
    try:
        if llamacpp.running():                       # Shoggoth's own llama.cpp router
            ports.add(llamacpp._state["port"])
    except Exception:  # noqa: BLE001 - never let this stop a scan
        pass
    return ports


def latest(own_ports=(), fresh=False, **kw):
    """(found, when) from the last scan, scanning again when asked or when it is older than FRESH seconds."""
    with _lock:
        if not fresh and time.time() - _last["at"] <= FRESH:
            return _last["found"], _last["at"]
    found = scan(own_ports=_own(own_ports), **kw)          # the slow part runs outside the lock
    with _lock:
        _last["found"], _last["at"] = found, time.time()
        return _last["found"], _last["at"]


def _state(entry, defs):
    r = servers.probe(entry["url"], entry.get("key", ""), pin=entry.get("fp"))
    d = next((x for x in defs if x["id"] == entry.get("preset")), None) or {}
    host = (urllib.parse.urlsplit(entry["url"]).hostname or "").lower()
    cloud = d.get("category") == "hosted"
    return {"id": entry["id"], "kind": "cloud" if cloud else "shoggoth" if entry.get("preset") == "shoggoth" else "engine", "name": entry.get("name") or entry["id"], "engine": entry.get("preset", ""),
            "url": entry["url"], "state": "needs_key" if r["needs_key"] else "ready" if r["reachable"] else "offline", "models": len(r["models"]),
            "loads": d.get("loads", "fixed"), "leaves_computer": cloud or host not in ("127.0.0.1", "localhost", "::1")}


def sources(own_ports=(), fresh=False, include_other=False, **kw):
    """The /ui/sources reply: {sources: [...connected...], found: [...answering but not connected...], scanned_at}.
    Servers whose models are all speech/embeddings/other (role != chat) are left out of `found` unless include_other.
    Each source is {id, kind, name, engine, url, state, models, loads, leaves_computer}; each found server is a scan() entry."""
    found, when = latest(own_ports, fresh, **kw)
    defs = engines.definitions()
    have = []
    for h in servers.all():
        try:
            servers.normalise(h["url"])
        except ValueError:
            continue                                  # a malformed stored address is skipped, not fatal
        have.append(h)
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        srcs = list(pool.map(lambda e: _state(e, defs), have))
    canon = {servers._canon(servers.normalise(h["url"])) for h in have}
    return {"sources": srcs, "found": [f for f in found if servers._canon(f["url"]) not in canon and (include_other or f.get("role", "chat") == "chat")], "scanned_at": when,
            "problems": engines.problems()}
