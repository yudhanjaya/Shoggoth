"""The gateway with hosted services: every model goes to its own provider, the three other wire formats pass through unchanged,
Gemini's thought signatures survive, and a Gemini 429 is put in plain words. Fake local servers only.

Run from the repository root:  python3 -m unittest discover tests
"""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "shoggoth"))

import gateway  # noqa: E402
import registry  # noqa: E402
import router  # noqa: E402
import servers  # noqa: E402

SIGNED_TOOL_CALL = {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\": \"a.txt\"}"},
                    "extra_content": {"google": {"thought_signature": "c2lnbmF0dXJl/+=="}}}
SSE = b"event: message_start\ndata: {\"type\": \"message_start\", \"message\": {\"usage\": {\"input_tokens\": 4}}}\n\n" \
      b"event: content_block_delta\ndata: {\"type\": \"content_block_delta\", \"delta\": {\"type\": \"thinking_delta\", \"thinking\": \"hmm\"}}\n\n" \
      b"event: content_block_delta\ndata: {\"type\": \"content_block_delta\", \"delta\": {\"type\": \"text_delta\", \"text\": \"hi\"}}\n\n"


class Fake(ThreadingHTTPServer):
    """A pretend hosted service: answers by path, remembers what it was sent."""
    daemon_threads = True

    def __init__(self, models, replies):
        self.models, self.replies, self.seen = models, replies, []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                srv = self.server
                srv.seen.append({"method": self.command, "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                if self.path == "/v1/models":
                    out, status, ctype = json.dumps({"data": [{"id": m} for m in srv.models]}).encode(), 200, "application/json"
                else:
                    status, ctype, out = srv.replies.get(self.path.split("?")[0], (404, "application/json", b"{}"))
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
            do_GET = do_POST = do

        super().__init__(("127.0.0.1", 0), H)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.server_address[1]


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR")}
        os.environ["SHOGGOTH_DATA_DIR"], os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")
        self.fakes = []
        self.gate = None

    def tearDown(self):
        for f in self.fakes:
            f.shutdown()
            f.server_close()
        if self.gate:
            self.gate.shutdown()
            self.gate.server_close()
        registry._default["r"] = None
        router._idx["v"] = None
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake(self, models, replies):
        f = Fake(models, replies)
        self.fakes.append(f)
        return f

    def connect(self, *pairs):
        """Connect each (preset, fake, key) as a server, then open the gateway on a free port."""
        for preset, fake, key in pairs:
            servers.add(preset, fake.url, key)
        registry._default["r"] = registry.Registry(bundled=os.path.join(self.tmp, "none"), user=os.path.join(self.tmp, "none")).load()
        router._idx["v"] = None
        gateway.CONFIG.update(key="gk", capture=True)
        self.gate = ThreadingHTTPServer(("127.0.0.1", 0), gateway.GWHandler)
        self.gate.daemon_threads = True
        threading.Thread(target=self.gate.serve_forever, daemon=True).start()

    def call(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.gate.server_address[1], timeout=20)
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data


class Routing(Env):
    def test_each_model_goes_to_its_own_provider_with_its_own_key(self):
        zen = self.fake(["claude-sonnet-4-5", "gpt-5", "glm-4.6"], {
            "/v1/chat/completions": (200, "application/json", b'{"choices": [{"message": {"content": "chat"}}]}'),
            "/v1/messages": (200, "text/event-stream", SSE),
            "/v1/responses": (200, "application/json", b'{"object": "response", "output": []}')})
        orr = self.fake(["meta/llama"], {"/v1/chat/completions": (200, "application/json", b'{"choices": []}')})
        self.connect(("opencode-zen", zen, "zen-key"), ("openrouter", orr, "or-key"))
        auth = {"Authorization": "Bearer gk"}

        st, data = self.call("POST", "/v1/chat/completions", {"model": "opencode-zen/glm-4.6", "messages": []}, auth)
        self.assertEqual(st, 200)
        sent = zen.seen[-1]
        self.assertEqual(sent["headers"]["authorization"], "Bearer zen-key")        # the provider's key, never the caller's
        self.assertEqual(json.loads(sent["body"])["model"], "glm-4.6")

        self.call("POST", "/v1/chat/completions", {"model": "openrouter/meta/llama"}, auth)
        self.assertEqual(orr.seen[-1]["headers"]["authorization"], "Bearer or-key")
        self.assertEqual(len([s for s in zen.seen if s["path"] == "/v1/chat/completions"]), 1)

    def test_anthropic_messages_pass_through_with_their_headers_and_the_stream_is_read(self):
        zen = self.fake(["claude-sonnet-4-5"], {"/v1/messages": (200, "text/event-stream", SSE)})
        self.connect(("opencode-zen", zen, "zen-key"))
        body = {"model": "opencode-zen/claude-sonnet-4-5", "max_tokens": 10, "stream": True, "messages": [{"role": "user", "content": "x"}]}
        st, data = self.call("POST", "/v1/messages", body, {"x-api-key": "gk", "anthropic-version": "2023-06-01", "anthropic-beta": "tools-2024"})
        self.assertEqual((st, data), (200, SSE))                                      # forwarded bytes are untouched
        sent = zen.seen[-1]
        self.assertEqual(sent["headers"]["x-api-key"], "zen-key")
        self.assertEqual(sent["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(sent["headers"]["anthropic-beta"], "tools-2024")
        self.assertEqual(json.loads(sent["body"])["model"], "claude-sonnet-4-5")
        rec = gateway.REC.snapshot(5, {gateway.REC.seq})[0][-1]
        self.assertEqual((rec["state"], rec["think"], rec["answer"], rec["tokens_in"]), ("done", "hmm", "hi", 4))

    def test_responses_pass_through(self):
        zen = self.fake(["gpt-5"], {"/v1/responses": (200, "application/json", b'{"object": "response", "output": [], "usage": {"input_tokens": 2, "output_tokens": 3}}')})
        self.connect(("opencode-zen", zen, "zen-key"))
        st, _ = self.call("POST", "/v1/responses", {"model": "opencode-zen/gpt-5", "input": "x"}, {"Authorization": "Bearer gk"})
        self.assertEqual(st, 200)
        self.assertEqual(zen.seen[-1]["path"], "/v1/responses")
        rec = gateway.REC.snapshot(5)[0][-1]
        self.assertEqual((rec["tokens_in"], rec["tokens_out"]), (2, 3))

    def test_the_error_names_the_provider_not_lemonade(self):
        zen = self.fake(["glm-4.6"], {})
        self.connect(("opencode-zen", zen, "k"))
        zen.shutdown()
        zen.server_close()
        self.fakes.remove(zen)
        st, data = self.call("POST", "/v1/chat/completions", {"model": "opencode-zen/glm-4.6"}, {"Authorization": "Bearer gk"})
        self.assertEqual(st, 503)                       # not running: the router says so before any request is made
        self.assertNotIn("Lemonade", data.decode())

    def test_a_wrong_key_is_refused(self):
        zen = self.fake(["glm-4.6"], {})
        self.connect(("opencode-zen", zen, "k"))
        self.assertEqual(self.call("POST", "/v1/chat/completions", {"model": "opencode-zen/glm-4.6"}, {"Authorization": "Bearer nope"})[0], 401)
        self.assertEqual([x for x in zen.seen if x["method"] == "POST"], [])


class Gemini(Env):
    def native(self, model="gemini-2.5-flash"):
        return "/v1beta/models/%s:streamGenerateContent?alt=sse" % model

    def test_native_requests_go_to_the_provider_with_the_google_key_header(self):
        chunk = b"data: " + json.dumps({"candidates": [{"content": {"parts": [{"text": "think", "thought": True}, {"text": "done"}]}}],
                                        "usageMetadata": {"promptTokenCount": 6, "candidatesTokenCount": 2}}).encode() + b"\n\n"
        g = self.fake(["gemini-2.5-flash"], {"/v1beta/models/gemini-2.5-flash:streamGenerateContent": (200, "text/event-stream", chunk)})
        self.connect(("gemini", g, "g-key"))
        st, data = self.call("POST", self.native("gemini/gemini-2.5-flash"), {"contents": []},
                             {"x-goog-api-key": "gk"})
        self.assertEqual((st, data), (200, chunk))
        sent = [s for s in g.seen if s["method"] == "POST"][-1]
        self.assertEqual(sent["path"], self.native())                     # the provider's own name for the model, query kept
        self.assertEqual(sent["headers"]["x-goog-api-key"], "g-key")
        self.assertNotIn("authorization", sent["headers"])
        self.assertEqual(sent["body"], b'{"contents": []}')
        rec = gateway.REC.snapshot(5, {gateway.REC.seq})[0][-1]
        self.assertEqual((rec["model"], rec["think"], rec["answer"], rec["tokens_in"]), ("gemini/gemini-2.5-flash", "think", "done", 6))

    def test_thought_signature_on_a_tool_call_survives_the_proxy(self):
        reply = {"choices": [{"index": 0, "message": {"role": "assistant", "tool_calls": [SIGNED_TOOL_CALL]}, "finish_reason": "tool_calls"}]}
        stream = b"data: " + json.dumps({"choices": [{"index": 0, "delta": {"tool_calls": [dict(SIGNED_TOOL_CALL, index=0)]}}]}).encode() + b"\n\ndata: [DONE]\n\n"
        g = self.fake(["gemini-2.5-pro"], {"/v1/chat/completions": (200, "application/json", json.dumps(reply).encode())})
        self.connect(("gemini", g, "g-key"))
        st, data = self.call("POST", "/v1/chat/completions", {"model": "gemini/gemini-2.5-pro", "messages": []}, {"Authorization": "Bearer gk"})
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(data)["choices"][0]["message"]["tool_calls"][0], SIGNED_TOOL_CALL)       # byte for byte
        self.assertEqual(data, json.dumps(reply).encode())
        g.replies["/v1/chat/completions"] = (200, "text/event-stream", stream)
        self.assertEqual(self.call("POST", "/v1/chat/completions", {"model": "gemini/gemini-2.5-pro", "stream": True}, {"Authorization": "Bearer gk"})[1], stream)
        # and the signature the app sends back with the tool result reaches Google unchanged
        history = {"model": "gemini/gemini-2.5-pro", "messages": [{"role": "assistant", "tool_calls": [SIGNED_TOOL_CALL]},
                                                                   {"role": "tool", "tool_call_id": "call_1", "content": "text"}]}
        self.call("POST", "/v1/chat/completions", history, {"Authorization": "Bearer gk"})
        back = json.loads([s for s in g.seen if s["method"] == "POST"][-1]["body"])
        self.assertEqual(back["messages"][0]["tool_calls"][0], SIGNED_TOOL_CALL)

    def test_pi_openai_completions_does_not_keep_the_signature_so_gemini_uses_its_own_format(self):
        import engines
        self.assertEqual(engines.api_for("gemini", "gemini-2.5-pro"), "google-generative-ai")

    def test_a_429_is_put_in_plain_words(self):
        busy = json.dumps({"error": {"code": 429, "message": "Quota exceeded for metric", "status": "RESOURCE_EXHAUSTED"}}).encode()
        g = self.fake(["gemini-2.5-flash"], {"/v1/chat/completions": (429, "application/json", busy)})
        self.connect(("gemini", g, "g-key"))
        auth = {"Authorization": "Bearer gk"}
        st, data = self.call("POST", "/v1/chat/completions", {"model": "gemini/gemini-2.5-flash"}, auth)
        self.assertEqual((st, data), (429, busy))          # a server that isn't Google's passes the error as sent
        self.assertIn("Quota exceeded", gateway.REC.snapshot(5)[0][-1]["error"])
        old = gateway.GOOGLE_HOSTS
        gateway.GOOGLE_HOSTS = ("127.0.0.1",)               # the fake stands in for Google
        try:
            st, data = self.call("POST", "/v1/chat/completions", {"model": "gemini/gemini-2.5-flash"}, auth)
        finally:
            gateway.GOOGLE_HOSTS = old
        self.assertEqual(st, 429)
        self.assertEqual(json.loads(data)["error"]["message"], "Gemini's free limit is used up for now")
        self.assertEqual(gateway.REC.snapshot(5)[0][-1]["error"], "Gemini's free limit is used up for now")

    def test_the_gemini_wording(self):
        self.assertEqual(gateway.GEMINI_LIMIT, "Gemini's free limit is used up for now")


if __name__ == "__main__":
    unittest.main()
