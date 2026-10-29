"""The door other apps use, and the log of what comes through it.

Zest's own page is never exposed to other programs: it can change Lemonade's settings and has no login. Instead this
module opens a second, much smaller listener that serves only the OpenAI-style /v1 endpoints, always asks for the
key, and listens on this computer only unless the person turns on "other devices on my network".

Both that listener and Zest's own chat go through relay(), which forwards the request to Lemonade unchanged while
watching the answer stream past, so the Server tab can show who asked, which model answered, and whether it was
thinking or already writing.
"""
import datetime
import hmac
import os
import http.client
import json
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import lem
import provbase
import recs
import router
import store

# What counts as "a request worth showing" (polling for health or the model list is not).
INFER = re.compile(r"^/v1/(chat/completions|completions|embeddings|responses|messages|reranking|audio/.+|images/.+)$")
# What the gateway will forward at all. Pulling, deleting, installing and changing settings are never reachable.
ALLOW = re.compile(r"^/v1/(models(/.+)?|health|load|unload|chat/completions|completions|embeddings|responses|messages|"
                   r"reranking|audio/.+|images/.+)$")
MAX_KEEP = 300
MAX_TEXT = 6000


def norm(path):
    """/api/v1/x and /v1/x are the same thing; query strings don't matter for matching."""
    p = urlparse(path).path
    return p[4:] if p.startswith("/api/") else p


# ---------------------------------------------------------------- who is asking
# Named apps come from the `detect` patterns in recommendations/tools.yaml; these say what kind of program it is when no app matched.
_GENERIC = [
    (r"openai[-/ ]?python|openai/py", "Python app"), (r"openai[-/ ]?(js|node)|openai/js", "Node app"),
    (r"python-requests|python-httpx|aiohttp|urllib", "Python script"), (r"^curl", "curl"), (r"node|undici|axios", "Node app"),
    (r"go-http-client", "Go app"), (r"mozilla", "A web page"),
]


def app_name(headers):
    forced = headers.get("X-Zest-App") or headers.get("X-Title")
    if forced:
        return re.sub(r"[^\w .+\-]", "", forced)[:32] or "Unknown app"
    ua = (headers.get("User-Agent") or "").strip()
    for pat, name in recs.app_patterns():
        if pat.search(ua):
            return name
    for pat, name in _GENERIC:
        if re.search(pat, ua, re.I):
            return name
    first = re.split(r"[/ ;(]", ua, 1)[0] if ua else ""
    first = re.sub(r"[^\w.+\-]", "", first)
    return first[:24] or "Unknown app"


# ---------------------------------------------------------------- the log
class Recorder:
    def __init__(self):
        self.lock = threading.Lock()
        self.items = []
        self.seq = 0
        self.day, self.today = self._load_day()

    # "Requests today" survives a restart, so it is kept in a tiny file beside Zest's other data.
    @staticmethod
    def _day_path():
        return os.path.join(store.data_dir(), "requests-today.json")

    def _load_day(self):
        now = datetime.date.today().isoformat()
        try:
            with open(self._day_path()) as f:
                d = json.load(f)
            if d.get("day") == now:
                return now, int(d.get("n", 0))
        except (OSError, ValueError):
            pass
        return now, 0

    def _count(self):
        now = datetime.date.today().isoformat()
        if now != self.day:
            self.day, self.today = now, 0
        self.today += 1
        try:
            os.makedirs(store.data_dir(), exist_ok=True)
            with open(self._day_path(), "w") as f:
                json.dump({"day": self.day, "n": self.today}, f)
        except OSError:
            pass

    def count_today(self):
        with self.lock:
            return self.today if self.day == datetime.date.today().isoformat() else 0

    def start(self, app, source, method, path, model, stream, ip):
        with self.lock:
            self.seq += 1
            self._count()
            r = {"id": self.seq, "app": app, "source": source, "method": method, "path": path, "model": model or "",
                 "stream": bool(stream), "ip": ip, "state": "waiting", "t0": time.time(), "t_think": None, "t_write": None,
                 "t_end": None, "status": None, "think_chars": 0, "out_chars": 0, "tokens_in": None, "tokens_out": None,
                 "error": "", "think": "", "answer": ""}
            self.items.append(r)
            del self.items[:-MAX_KEEP]
            return r

    def quotes(self, ids):
        """The last few words of what each running request is thinking or writing, for the live cards."""
        out = {}
        with self.lock:
            for r in self.items:
                if r["id"] in ids and r["state"] in ("thinking", "answering"):
                    txt = (r["think"] if r["state"] == "thinking" else r["answer"]).strip()
                    if txt:
                        tail = txt[-260:]
                        cut = max(tail.rfind(". "), tail.rfind("? "), tail.rfind("! "), tail.rfind("\n"))
                        out[r["id"]] = (tail[cut + 1:] if 0 <= cut < len(tail) - 25 else tail).strip()[-200:]
        return out

    def snapshot(self, limit=60, text_ids=()):
        """The newest requests, newest last. Question and answer text only goes out for the ids asked about."""
        with self.lock:
            out = []
            for r in self.items[-limit:]:
                c = dict(r)
                if r["id"] not in text_ids:
                    c["think"] = c["answer"] = ""
                out.append(c)
            return out, self.seq

    def active(self):
        with self.lock:
            return sum(1 for r in self.items if r["state"] in ("waiting", "thinking", "answering"))

    def models_in_flight(self):
        """The models of requests an engine is working on now (sent on, and not finished)."""
        with self.lock:
            return {r["model"] for r in self.items if r.get("sent") and r["state"] in ("waiting", "thinking", "answering")}

    def activity(self):
        """Requests being written right now, with a rough speed. Characters stand in for tokens (about four to one),
        so the figure is marked as approximate wherever it is shown."""
        now = time.time()
        out = []
        with self.lock:
            for r in self.items:
                if r["state"] not in ("thinking", "answering"):
                    continue
                start = r["t_think"] or r["t_write"]
                dt = now - start if start else 0
                chars = r["think_chars"] + r["out_chars"]
                out.append({"id": r["id"], "app": r["app"], "source": r["source"], "model": r["model"], "state": r["state"],
                            "tps": round(chars / 4 / dt, 1) if dt >= 1 and chars >= 20 else None})
        return out


REC = Recorder()


def _provider_busy(pid):
    """Is an app's request being answered by provider `pid`? The router asks before it stops an engine to make room."""
    for model in REC.models_in_flight():
        p, _ = router.resolve(model)
        if p is not None and p.id == pid:
            return True
    return False


router.busy_checks.append(_provider_busy)


class Tap:
    """Reads the bytes going past and keeps the record up to date: thinking, then answering, then done."""

    def __init__(self, rec, capture):
        self.rec, self.capture, self.buf, self.in_tag = rec, capture, b"", False

    def _think(self, t):
        r = self.rec
        if r["state"] == "waiting":
            r["state"], r["t_think"] = "thinking", time.time()
        r["think_chars"] += len(t)
        if self.capture and len(r["think"]) < MAX_TEXT:
            r["think"] += t

    def _write(self, t):
        r = self.rec
        if r["state"] in ("waiting", "thinking"):
            r["state"], r["t_write"] = "answering", time.time()
        r["out_chars"] += len(t)
        if self.capture and len(r["answer"]) < MAX_TEXT:
            r["answer"] += t

    def _content(self, t):
        # Some models put their thinking inline between <think> tags instead of in a separate field.
        while t:
            if self.in_tag:
                i = t.find("</think>")
                self._think(t if i < 0 else t[:i])
                if i < 0:
                    return
                self.in_tag, t = False, t[i + 8:]
            else:
                i = t.find("<think>")
                if i < 0:
                    if t.strip() or self.rec["state"] == "answering":
                        self._write(t)
                    return
                if t[:i].strip():
                    self._write(t[:i])
                self.in_tag, t = True, t[i + 7:]
                if self.rec["state"] == "waiting":
                    self.rec["state"], self.rec["t_think"] = "thinking", time.time()

    def _obj(self, o):
        u = o.get("usage")
        if isinstance(u, dict):
            self.rec["tokens_in"] = u.get("prompt_tokens", self.rec["tokens_in"])
            self.rec["tokens_out"] = u.get("completion_tokens", self.rec["tokens_out"])
        for ch in o.get("choices") or []:
            d = ch.get("delta") if isinstance(ch.get("delta"), dict) else ch.get("message")
            if isinstance(d, dict):
                th = d.get("reasoning_content") or d.get("reasoning")
                if th:
                    self._think(th)
                if d.get("content"):
                    self._content(d["content"])
            elif isinstance(ch.get("text"), str) and ch["text"]:
                self._content(ch["text"])

    def feed(self, chunk):
        self.buf += chunk
        if len(self.buf) > 4_000_000:        # not a stream we understand; stop buffering
            self.buf = b""
            return
        if not self.rec["stream"]:
            return
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if not data or data == b"[DONE]":
                continue
            try:
                self._obj(json.loads(data))
            except ValueError:
                pass

    def finish(self, status):
        r = self.rec
        if not r["stream"] and self.buf:
            try:
                o = json.loads(self.buf)
                if isinstance(o, dict):
                    self._obj(o)
                    if status >= 400:
                        e = o.get("error")
                        r["error"] = (e.get("message") if isinstance(e, dict) else e) or ""
            except ValueError:
                pass
        r["status"], r["t_end"] = status, time.time()
        if r["tokens_out"] is None and (r["out_chars"] or r["think_chars"]):
            r["tokens_out"] = max(1, (r["out_chars"] + r["think_chars"]) // 4)
        r["state"] = "error" if status >= 400 else "done"


# ---------------------------------------------------------------- forwarding
def _route(path, body):
    """Where a request should go: (scheme, host, port, key, path, body, error). Requests for a model another engine serves are sent there
    (starting it, and freeing what would fight it, first); everything else goes to the default engine as it always did."""
    host, port = lem.target()
    key = lem.key()
    model, _ = describe(body)
    if not model or not INFER.match(norm(path)):
        return "http", host, port, key, path, body, None
    provider, mid = router.resolve(model)
    if provider is None or not router.known(model):
        return "http", host, port, key, path, body, None              # not a chat model Zest knows: the default engine decides
    try:
        ep = router.route(model)
    except provbase.ProviderError as e:
        return "http", host, port, key, path, body, str(e)
    if provider is router.default_provider() or not ep.get("base_url"):
        return "http", host, port, key, path, body, None
    u = urlparse(ep["base_url"])
    p = norm(path)
    out_path = u.path.rstrip("/") + p[len("/v1"):] + (("?" + urlparse(path).query) if urlparse(path).query else "")
    try:
        o = json.loads(body)
        o["model"] = ep.get("model") or mid                           # the engine knows the model by its own name
        body = json.dumps(o).encode()
    except (ValueError, TypeError):
        pass
    scheme = "https" if u.scheme == "https" else "http"
    return scheme, u.hostname, u.port or (443 if scheme == "https" else 80), ep.get("key") or "", out_path, body, None


def _models_for_apps(h):
    """GET /v1/models for other apps: the default engine's list plus the models other engines can serve. Returns True when answered."""
    status, d = lem.request("GET", "/api/v1/models", timeout=15)
    if status != 200 or not isinstance(d, dict) or not isinstance(d.get("data"), list):
        return False
    dflt = router.default_provider()
    d["data"] += [{"id": m["id"], "object": "model", "created": 0, "owned_by": m["engine"]} for m in router.chat_models() if m["engine"] != getattr(dflt, "id", "")]
    h.send_json(d)
    return True


def relay(h, method, path, body, rec=None, capture=False, merge_models=False):
    """Send the request to whichever engine serves the model and stream the answer back through handler `h`."""
    if merge_models and method == "GET" and re.match(r"^/v1/models/?$", norm(path)) and _models_for_apps(h):
        return
    scheme, host, port, key, path, body, error = _route(path, body)
    if error:
        if rec:
            rec["error"] = error
            Tap(rec, False).finish(503)
        return h.send_json({"error": {"message": error, "type": "model_unavailable"}}, 503)
    headers = {k: v for k, v in h.headers.items() if k.lower() in ("content-type", "accept")}
    if key:
        headers["Authorization"] = "Bearer " + key
    conn = (http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection)(host, port, timeout=900)
    tap = Tap(rec, capture) if rec else None
    if rec:
        rec["sent"] = True                     # from here the engine is working on it (the router won't stop it to make room)
    try:
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
    except (OSError, http.client.HTTPException) as e:
        who = "Lemonade" if (host, port) == lem.target() else "The model's engine"
        if rec:
            rec["error"] = "%s is unreachable" % who
            tap.finish(502)
        return h.send_json({"error": {"message": f"{who} is unreachable: {e}"}}, 502)
    h.send_response(resp.status)
    for name in ("Content-Type", "Cache-Control"):
        if resp.getheader(name):
            h.send_header(name, resp.getheader(name))
    h.send_header("X-Accel-Buffering", "no")
    if hasattr(h, "cors"):
        h.cors()
    h.end_headers()          # HTTP/1.0: the body ends when we close, so streaming just works
    try:
        while True:
            chunk = resp.read1(8192)
            if not chunk:
                break
            h.wfile.write(chunk)
            h.wfile.flush()
            if tap:
                tap.feed(chunk)
    except (BrokenPipeError, ConnectionResetError):
        if rec:
            rec["error"] = "The app hung up before the answer finished"
    finally:
        conn.close()
        if tap:
            tap.finish(resp.status)


def describe(body):
    """(model, stream) from a JSON request body."""
    try:
        o = json.loads(body or b"{}")
        return str(o.get("model") or ""), bool(o.get("stream"))
    except (ValueError, AttributeError):
        return "", False


# ---------------------------------------------------------------- the listener
CONFIG = {"key": "", "capture": False}


class GWHandler(BaseHTTPRequestHandler):
    server_version = "ZestGateway"

    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200, extra=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.cors()
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def cors(self):
        # A web page can only call this if it also knows the key, so open CORS is safe and lets browser-based UIs work.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Api-Key, X-Title, X-Zest-App")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.end_headers()

    def key_ok(self):
        sent = self.headers.get("Authorization", "")
        sent = sent[7:].strip() if sent.lower().startswith("bearer ") else (self.headers.get("X-Api-Key") or "").strip()
        return bool(CONFIG["key"]) and hmac.compare_digest(sent.encode(), CONFIG["key"].encode())

    def handle_any(self):
        p = norm(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        if n > 64_000_000:
            return self.send_json({"error": {"message": "That request is too large."}}, 413)
        body = self.rfile.read(n) if n else None
        if p in ("/", "/v1", "/v1/"):
            return self.send_json({"zest_gateway": True, "message": "Point your app's base URL at /v1 and use your Zest key."})
        app = app_name(self.headers)
        if not self.key_ok():
            r = REC.start(app, "app", self.command, p, "", False, self.client_address[0])
            r["error"] = "Wrong key" if (self.headers.get("Authorization") or self.headers.get("x-api-key")) else "No key sent"
            Tap(r, False).finish(401)
            return self.send_json({"error": {"message": "Missing or wrong API key. Copy it from Zest's Server page.",
                                             "type": "invalid_api_key"}}, 401, {"WWW-Authenticate": "Bearer"})
        if not ALLOW.match(p):
            return self.send_json({"error": {"message": "This door only serves the OpenAI-style /v1 endpoints for chatting."}}, 404)
        rec = None
        if INFER.match(p) and self.command == "POST":
            model, stream = describe(body)
            rec = REC.start(app, "app", self.command, p, model, stream, self.client_address[0])
        q = urlparse(self.path).query
        relay(self, self.command, p + ("?" + q if q else ""), body, rec, CONFIG["capture"], merge_models=True)

    do_GET = do_POST = handle_any


class Gateway:
    def __init__(self):
        self.srv = None
        self.error = ""
        self.host = ""

    def start(self, g):
        CONFIG.update(key=g["api_key"], capture=bool(g["capture"]))
        if not g["enabled"]:
            return
        self.host = "0.0.0.0" if g["lan"] else "127.0.0.1"
        try:
            self.srv = ThreadingHTTPServer((self.host, int(g["port"])), GWHandler)
        except OSError as e:
            self.srv, self.error = None, f"Port {g['port']} is busy ({e.strerror or e})"
            return
        self.error = ""
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def stop(self):
        if self.srv:
            self.srv.shutdown()
            self.srv.server_close()
            self.srv = None

    def apply(self, s):
        self.stop()
        self.start(s["gateway"])

    @property
    def running(self):
        return self.srv is not None


GATEWAY = Gateway()


def lan_ip():
    """This computer's address on the local network (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("10.255.255.255", 1))
            ip = sk.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


def ensure_key(s):
    g = s["gateway"]
    if not g.get("api_key"):
        g["api_key"] = "zest-" + store.new_key()
        store.save(s)
    return s


def public_state(s):
    g = s["gateway"]
    ip = lan_ip() if g["lan"] else None
    return {"enabled": g["enabled"], "running": GATEWAY.running, "error": GATEWAY.error, "port": g["port"],
            "lan": g["lan"], "capture": g["capture"], "key": g["api_key"],
            "url": f"http://127.0.0.1:{g['port']}/v1", "lan_url": f"http://{ip}:{g['port']}/v1" if ip else None}
