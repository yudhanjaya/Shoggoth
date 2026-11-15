"""The door other apps use, and the log of what comes through it.

Shoggoth's own page is never exposed to other programs: it can change the engines' settings and has no login. Instead this
module opens a second, much smaller listener that serves only the OpenAI-style /v1 endpoints, always asks for the
key, and listens on this computer only unless the person turns on "other devices on my network".

Both that listener and Shoggoth's own chat go through relay(), which forwards the request to the engine that serves the model while
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
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import lan
import provbase
import recs
import router
import store

# What counts as "a request worth showing" (polling for health or the model list is not).
GEMINI_NATIVE = r"v1beta/models/[^?:]+:(?:stream)?[gG]enerateContent"       # Google's own format; the model is in the path
INFER = re.compile(r"^/(v1/(chat/completions|completions|embeddings|responses|messages|reranking|audio/.+|images/.+)|%s)$" % GEMINI_NATIVE)
# What the gateway will forward at all. Pulling, deleting, installing and changing settings are never reachable.
ALLOW = re.compile(r"^/(v1/(models(/.+)?|health|load|unload|chat/completions|completions|embeddings|responses|messages|"
                   r"reranking|audio/.+|images/.+)|%s)$" % GEMINI_NATIVE)
GEMINI_PATH = re.compile(r"^/v1beta/models/([^?:]+):((?:stream)?[gG]enerateContent)$")
GOOGLE_HOSTS = ("googleapis.com",)                                   # a 429 from these is Gemini's free limit
GEMINI_LIMIT = "Gemini's free limit is used up for now"
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
    forced = headers.get("X-Shoggoth-App") or headers.get("X-Zest-App") or headers.get("X-Title")
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

    # "Requests today" survives a restart, so it is kept in a tiny file beside Shoggoth's other data.
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
                 "error": "", "think": "", "answer": "", "tps": None, "pps": None}
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

    def last_speed(self, source="zest", model=None):
        """The newest finished request from `source`: {id, tps, pps, tokens, exact}. `tps` is llama.cpp's own figure when it sent one
        (`exact`), otherwise tokens over the time between the first token and the end, which still beats counting characters."""
        with self.lock:
            for r in reversed(self.items):
                if r["source"] != source or r["state"] != "done" or (model and r["model"] != model):
                    continue
                first = r["t_think"] or r["t_write"]
                est = None
                if r["tokens_out"] and first and r["t_end"] and r["t_end"] - first >= 0.5:
                    est = round(r["tokens_out"] / (r["t_end"] - first), 1)
                return {"id": r["id"], "tps": r["tps"] or est, "pps": r["pps"], "tokens": r["tokens_out"], "exact": bool(r["tps"])}
        return None


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
        tm = o.get("timings")                      # llama.cpp's own speed figures, on the last chunk: exact, where the character count is a guess
        if isinstance(tm, dict):
            if isinstance(tm.get("predicted_per_second"), (int, float)):
                self.rec["tps"] = round(tm["predicted_per_second"], 1)
            if isinstance(tm.get("prompt_per_second"), (int, float)):
                self.rec["pps"] = round(tm["prompt_per_second"], 1)
        u = o.get("usage")
        if isinstance(u, dict):
            self.rec["tokens_in"] = u.get("prompt_tokens", self.rec["tokens_in"])
            self.rec["tokens_out"] = u.get("completion_tokens", self.rec["tokens_out"])
        t = o.get("type")
        if isinstance(t, str):
            self._typed(t, o)
        if isinstance(o.get("candidates"), list):
            self._gemini(o)
        if o.get("object") == "response" or o.get("type") == "message":
            self._whole(o)                                     # a finished (not streamed) Responses or Anthropic reply
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

    def _typed(self, t, o):
        """Events with a `type`: Anthropic Messages (content_block_delta, message_start, message_delta) and OpenAI Responses (response.*)."""
        d = o.get("delta")
        if t == "content_block_delta" and isinstance(d, dict):
            if d.get("type") == "thinking_delta" and d.get("thinking"):
                self._think(d["thinking"])
            elif d.get("type") == "text_delta" and d.get("text"):
                self._content(d["text"])
        elif t == "content_block_start" and isinstance(o.get("content_block"), dict) and o["content_block"].get("type") in ("thinking", "redacted_thinking"):
            if self.rec["state"] == "waiting":
                self.rec["state"], self.rec["t_think"] = "thinking", time.time()
        elif t == "message_start" and isinstance(o.get("message"), dict):
            self._usage(o["message"].get("usage"))
        elif t == "message_delta":
            self._usage(o.get("usage"))
        elif t in ("response.reasoning_text.delta", "response.reasoning_summary_text.delta") and isinstance(d, str) and d:
            self._think(d)
        elif t == "response.output_text.delta" and isinstance(d, str) and d:
            self._content(d)
        elif t in ("response.completed", "response.incomplete") and isinstance(o.get("response"), dict):
            self._usage(o["response"].get("usage"))

    def _usage(self, u):
        """Token counts as Anthropic (input_tokens, output_tokens) and Responses (input_tokens, output_tokens) name them."""
        if isinstance(u, dict):
            if isinstance(u.get("input_tokens"), int):
                self.rec["tokens_in"] = u["input_tokens"]
            if isinstance(u.get("output_tokens"), int):
                self.rec["tokens_out"] = u["output_tokens"]

    def _gemini(self, o):
        """A Gemini chunk: parts with `thought: true` are thinking, any other text is the answer."""
        for c in o["candidates"]:
            parts = ((c or {}).get("content") or {}).get("parts") if isinstance(c, dict) else None
            for part in parts or []:
                if isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"]:
                    (self._think if part.get("thought") else self._content)(part["text"])
        u = o.get("usageMetadata")
        if isinstance(u, dict):
            if isinstance(u.get("promptTokenCount"), int):
                self.rec["tokens_in"] = u["promptTokenCount"]
            if isinstance(u.get("candidatesTokenCount"), int):
                self.rec["tokens_out"] = u["candidatesTokenCount"] + (u.get("thoughtsTokenCount") or 0)

    def _whole(self, o):
        """The same, from a reply that was not streamed."""
        self._usage(o.get("usage"))
        if o.get("type") == "message":
            for b in o.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "thinking" and b.get("thinking"):
                    self._think(b["thinking"])
                elif isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                    self._content(b["text"])
            return
        for item in o.get("output") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "reasoning":
                for part in (item.get("content") or []) + (item.get("summary") or []):
                    if isinstance(part, dict) and part.get("text"):
                        self._think(part["text"])
            elif item.get("type") == "message":
                for part in item.get("content") or []:
                    if isinstance(part, dict) and part.get("type") == "output_text" and part.get("text"):
                        self._content(part["text"])

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
def _split(url):
    u = urlparse(url)
    scheme = "https" if u.scheme == "https" else "http"
    return scheme, u.hostname, u.port or (443 if scheme == "https" else 80), u.path.rstrip("/")


def _plan(path, body):
    """Where a request should go: {scheme, host, port, key, path, body, error, who, kind}. Every model goes to the provider that serves it, found
    through the registry (starting it, and freeing what would fight it, first); a request with no model Shoggoth knows goes to the default
    provider. `who` is the name to use in an error; `kind` is the wire format: chat, messages, responses or gemini."""
    p, query = norm(path), urlparse(path).query
    gm = GEMINI_PATH.match(p)
    kind = "gemini" if gm else "messages" if p == "/v1/messages" else "responses" if p.startswith("/v1/responses") else "chat"
    model = describe(body, path)[0]
    out = {"key": "", "path": path, "body": body, "error": None, "who": "The model's engine", "kind": kind}
    provider, mid, known = None, model, False
    if model and INFER.match(p):
        provider, mid = router.resolve(model)
        known = provider is not None and router.known(model)
    if not known:
        provider, mid = router.default_provider(), model            # not a chat model Shoggoth knows: the default provider decides
    out["who"] = getattr(provider, "name", "") or out["who"]
    try:
        ep = router.route(model) if known else (provider.endpoint() if provider else None)
    except provbase.ProviderError as e:
        return dict(out, error=str(e))
    if not ep or not ep.get("base_url"):
        return dict(out, error="%s isn't running, so there is nothing to send that to." % (out["who"] if provider else "No engine"))
    out["scheme"], out["host"], out["port"], base = _split(ep["base_url"])
    out["key"] = ep.get("key") or ""
    out["pin"] = ep.get("pin") or ""                                   # another Shoggoth: its certificate's fingerprint (lan.py)
    if gm:                                                         # Google's own address: the model name goes in the path, the version is fixed
        name = ep.get("model") or mid
        out["path"] = "/v1beta/models/%s:%s" % (name, gm.group(2)) + (("?" + query) if query else "")
        return out
    out["path"] = base + p[len("/v1"):] + (("?" + query) if query else "")
    if known:
        try:
            o = json.loads(body)
            o["model"] = ep.get("model") or mid                    # the engine knows the model by its own name
            out["body"] = json.dumps(o).encode()
        except (ValueError, TypeError):
            pass
    return out


def _route(path, body):
    """(scheme, host, port, key, path, body, error) for a request: see _plan."""
    r = _plan(path, body)
    if r["error"]:
        return "http", "", 0, "", path, body, r["error"]
    return r["scheme"], r["host"], r["port"], r["key"], r["path"], r["body"], None


def _shoggoth_marks():
    """{model id: {"fit", "loaded", "title"}} from the engine picker, so an app can see whether a model fits this computer and is loaded now."""
    try:
        import library
        return {m["id"]: {"fit": m["fit"], "loaded": m["live"], "title": m.get("title") or m["id"]} for m in library.picker(30)["models"]}
    except Exception:  # noqa: BLE001 - a model list without these notes is still a useful answer
        return {}


def _mark_models(items, marks):
    """Name each model another app is told about, and add Shoggoth's own note to the ones the picker knows."""
    for m in items:
        if not isinstance(m, dict):
            continue
        mid = m.get("id")
        if not isinstance(mid, str):
            continue
        known = marks.get(mid) or {}
        m.setdefault("title", known.get("title") or mid)
        if known:
            m["shoggoth"] = {"fit": known["fit"], "loaded": known["loaded"]}
    return items


def _models_for_apps(h):
    """GET /v1/models for other apps: the default engine's list plus the models other engines can serve. Returns True when answered."""
    dflt = router.default_provider()
    try:
        ep = dflt.endpoint() if dflt else None
    except Exception:  # noqa: BLE001 - an engine that can't say where it is has no list to give
        ep = None
    if not ep or not ep.get("base_url"):
        return False
    status, d = provbase.http_json(ep["base_url"].rstrip("/") + "/models", ep.get("key") or "", timeout=15)
    if status != 200 or not isinstance(d, dict) or not isinstance(d.get("data"), list):
        return False
    d["data"] += [{"id": m["id"], "object": "model", "created": 0, "owned_by": m["engine"]} for m in router.chat_models() if m["engine"] != getattr(dflt, "id", "")]
    _mark_models(d["data"], _shoggoth_marks())
    h.send_json(d)
    return True


def _auth_headers(kind, key, inbound):
    """Headers for the upstream request. The caller's own key (for Shoggoth's door) is never passed on; the provider's key is added in the
    form its format uses. Anthropic's version and beta headers are passed on as the caller sent them."""
    h = {k: v for k, v in inbound.items() if k in ("content-type", "accept")}
    if kind == "gemini":
        if key:
            h["x-goog-api-key"] = key
        return h
    if kind == "messages":
        h["anthropic-version"] = inbound.get("anthropic-version") or "2023-06-01"
        if inbound.get("anthropic-beta"):
            h["anthropic-beta"] = inbound["anthropic-beta"]
        if key:
            h["x-api-key"] = key
    if key:
        h["Authorization"] = "Bearer " + key
    return h


def _error_text(raw):
    """The message inside an error body, in any of the shapes the formats use, or ''."""
    try:
        o = json.loads(raw)
    except ValueError:
        return ""
    if isinstance(o, list) and o:
        o = o[0]
    e = o.get("error") if isinstance(o, dict) else None
    return str((e.get("message") if isinstance(e, dict) else e) or "")[:500]


def relay(h, method, path, body, rec=None, capture=False, merge_models=False):
    """Send the request to whichever provider serves the model and stream the answer back through handler `h`."""
    if merge_models and method == "GET" and re.match(r"^/v1/models/?$", norm(path)) and _models_for_apps(h):
        return
    r = _plan(path, body)
    if rec and not rec["model"] and r["kind"] == "gemini":          # the model is in a Gemini path, not in the body
        rec["model"], rec["stream"] = describe(body, path)
    if r["error"]:
        if rec:
            rec["error"] = r["error"]
            Tap(rec, False).finish(503)
        return h.send_json({"error": {"message": r["error"], "type": "model_unavailable"}}, 503)
    headers = _auth_headers(r["kind"], r["key"], {k.lower(): v for k, v in h.headers.items()})
    if r.get("pin") and r["scheme"] == "https":                       # pinned: a different certificate fails before anything is sent
        conn = provbase.PinnedHTTPSConnection(r["host"], r["port"], pin=r["pin"], timeout=900)
    else:
        conn = (http.client.HTTPSConnection if r["scheme"] == "https" else http.client.HTTPConnection)(r["host"], r["port"], timeout=900)
    tap = Tap(rec, capture) if rec else None
    if rec:
        rec["sent"] = True                     # from here the engine is working on it (the router won't stop it to make room)
    try:
        conn.request(method, r["path"], body=r["body"], headers=headers)
        resp = conn.getresponse()
    except (OSError, http.client.HTTPException) as e:
        if rec:
            rec["error"] = "%s is unreachable" % r["who"]
            tap.finish(502)
        return h.send_json({"error": {"message": f"{r['who']} is unreachable: {e}"}}, 502)
    if resp.status == 429 and r["host"].endswith(GOOGLE_HOSTS):
        resp.read()
        conn.close()
        if rec:
            rec["error"] = GEMINI_LIMIT
            tap.finish(429)
        return h.send_json({"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "type": "rate_limit_exceeded", "message": GEMINI_LIMIT}}, 429)
    h.send_response(resp.status)
    for name in ("Content-Type", "Cache-Control"):
        if resp.getheader(name):
            h.send_header(name, resp.getheader(name))
    h.send_header("X-Accel-Buffering", "no")
    if hasattr(h, "cors"):
        h.cors()
    h.end_headers()          # HTTP/1.0: the body ends when we close, so streaming just works
    raw = b""
    try:
        while True:
            chunk = resp.read1(8192)
            if not chunk:
                break
            h.wfile.write(chunk)
            h.wfile.flush()
            if resp.status >= 400 and len(raw) < 20000:
                raw += chunk
            if tap:
                tap.feed(chunk)
    except (BrokenPipeError, ConnectionResetError):
        if rec:
            rec["error"] = "The app hung up before the answer finished"
    finally:
        conn.close()
        if tap:
            tap.finish(resp.status)
            if resp.status >= 400 and not rec["error"]:
                rec["error"] = _error_text(raw) or "%s answered with an error (%d)" % (r["who"], resp.status)


def describe(body, path=None):
    """(model, stream) from a JSON request body, or from the path for Google's own format (where the model is in the address)."""
    m = GEMINI_PATH.match(norm(path)) if path else None
    if m:
        name = m.group(1)
        return (name[7:] if name.startswith("models/") else name), "stream" in m.group(2).lower()
    try:
        o = json.loads(body or b"{}")
        return str(o.get("model") or ""), bool(o.get("stream"))
    except (ValueError, AttributeError):
        return "", False


# ---------------------------------------------------------------- the listener
# From the network the gateway speaks HTTPS only (5.0 dropped plain HTTP there). Plain HTTP is still served to this computer.
CONFIG = {"key": "", "capture": False}
# What a plain-HTTP request from another device gets instead of a dropped connection, so the person sees what to change.
PLAIN_REFUSED = (b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: %d\r\n\r\n%s")
PLAIN_REFUSED_BODY = (b'{"error": {"message": "Shoggoth only accepts encrypted connections from the network. Change the address in your app '
                      b'from http:// to https:// and trust the certificate Shoggoth shows on its Server page."}}')


class GWHandler(BaseHTTPRequestHandler):
    server_version = "ShoggothGateway"

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
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Api-Key, X-Goog-Api-Key, Anthropic-Version, Anthropic-Beta, X-Title, X-Shoggoth-App, X-Zest-App")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.end_headers()

    def key_ok(self):
        """The gateway's own key, or (over TLS, or from this computer) the key of a paired Shoggoth, whose name is kept in self.peer."""
        self.peer = None
        sent = self.headers.get("Authorization", "")
        sent = sent[7:].strip() if sent.lower().startswith("bearer ") else (self.headers.get("X-Api-Key") or self.headers.get("X-Goog-Api-Key") or "").strip()
        if CONFIG["key"] and hmac.compare_digest(sent.encode(), CONFIG["key"].encode()):
            return True
        if isinstance(self.connection, ssl.SSLSocket) or lan.is_loopback(self.client_address[0]):
            self.peer = lan.PAIRING.client_for_key(sent, self.client_address[0])
        return self.peer is not None

    def handle_any(self):
        p = norm(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        if n > 64_000_000:
            return self.send_json({"error": {"message": "That request is too large."}}, 413)
        body = self.rfile.read(n) if n else None
        if p in ("/", "/v1", "/v1/"):
            return self.send_json({"zest_gateway": True, "message": "Point your app's base URL at /v1 and use your Shoggoth key."})
        if p.startswith("/shoggoth/"):                                  # pairing (lan.py): TLS only, no key yet
            code, obj = lan.serve_peer(self.command, p, body, self.client_address[0], isinstance(self.connection, ssl.SSLSocket))
            return self.send_json(obj, code)
        app = app_name(self.headers)
        if not self.key_ok():
            r = REC.start(app, "app", self.command, p, "", False, self.client_address[0])
            r["error"] = "Wrong key" if (self.headers.get("Authorization") or self.headers.get("x-api-key") or self.headers.get("x-goog-api-key")) else "No key sent"
            Tap(r, False).finish(401)
            return self.send_json({"error": {"message": "Missing or wrong API key. Copy it from Shoggoth's Server page.",
                                             "type": "invalid_api_key"}}, 401, {"WWW-Authenticate": "Bearer"})
        if not ALLOW.match(p):
            return self.send_json({"error": {"message": "This door only serves the OpenAI-style /v1 endpoints for chatting."}}, 404)
        if self.peer:
            app = self.peer["name"]
        if p == "/v1/audio/transcriptions" and self.command == "POST":    # speech to text runs in Shoggoth's own whisper.cpp (speech.py)
            import speech
            rec = REC.start(app, "app", self.command, p, "whisper", False, self.client_address[0])
            code, ctype, payload = speech.openai_transcriptions(body or b"", self.headers)
            Tap(rec, False).finish(code)
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if p == "/v1/audio/speech" and self.command == "POST":            # read aloud runs in Piper (tts.py)
            import tts
            rec = REC.start(app, "app", self.command, p, "piper", False, self.client_address[0])
            code, ctype, payload = tts.openai_speech(body or b"", self.headers)
            Tap(rec, False).finish(code)
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        rec = None
        if INFER.match(p) and self.command == "POST":
            model, stream = describe(body, p)
            rec = REC.start(app, "app", self.command, p, model, stream, self.client_address[0])
        q = urlparse(self.path).query
        relay(self, self.command, p + ("?" + q if q else ""), body, rec, CONFIG["capture"], merge_models=True)

    do_GET = do_POST = handle_any


class _Server(ThreadingHTTPServer):
    """One port for both sides. With `tls` set (sharing on the network), each connection's first byte says which it is: a TLS handshake
    is answered with TLS; plain HTTP is served to this computer only; from the network it gets a short reply saying to use https://."""
    tls = None
    daemon_threads = True

    def finish_request(self, request, client_address):
        if self.tls is not None:
            try:
                request.settimeout(10)
                first = request.recv(1, socket.MSG_PEEK)
                if first == b"\x16":
                    request = self.tls.wrap_socket(request, server_side=True)
                elif not lan.is_loopback(client_address[0]):
                    try:
                        request.sendall(PLAIN_REFUSED % (len(PLAIN_REFUSED_BODY), PLAIN_REFUSED_BODY))
                    except OSError:
                        pass
                    return
                request.settimeout(None)
            except (OSError, ssl.SSLError):
                return
        try:
            self.RequestHandlerClass(request, client_address, self)
        finally:
            if isinstance(request, ssl.SSLSocket):
                try:
                    request.close()
                except OSError:
                    pass


class Gateway:
    def __init__(self):
        self.srv = None
        self.error = ""
        self.host = ""

    def start(self, g):
        CONFIG.update(key=g["api_key"], capture=bool(g["capture"]))
        if not g["enabled"]:
            return
        tls, lan_error = None, ""
        if g["lan"]:
            try:
                tls = lan.server_context()
            except (lan.LanError, OSError, ssl.SSLError) as e:      # no certificate, no network side: stay on this computer
                lan_error = "Not shared on the network: %s" % e
        self.host = "0.0.0.0" if tls else "127.0.0.1"
        try:
            self.srv = _Server((self.host, int(g["port"])), GWHandler)
        except OSError as e:
            self.srv, self.error = None, f"Port {g['port']} is busy ({e.strerror or e})"
            return
        self.srv.tls = tls
        self.error = lan_error
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        if tls:
            lan.SHARING["on"] = True
            lan.BEACON.start(self.srv.server_address[1])

    def stop(self):
        lan.SHARING["on"] = False
        lan.BEACON.stop()
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
        g["api_key"] = "shoggoth-" + store.new_key()
        store.save(s)
    return s


def public_state(s):
    g = s["gateway"]
    ip = lan_ip() if g["lan"] and lan.SHARING["on"] else None
    fp = lan.own_fp() if ip else ""
    return {"enabled": g["enabled"], "running": GATEWAY.running, "error": GATEWAY.error, "port": g["port"],
            "lan": g["lan"], "capture": g["capture"], "key": g["api_key"],
            "url": f"http://127.0.0.1:{g['port']}/v1", "lan_url": f"https://{ip}:{g['port']}/v1" if ip else None,
            "fp": fp, "fp_short": lan.short_fp(fp)}
