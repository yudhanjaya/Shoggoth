"""Hooking up a provider by hand: the Test button's chats and tool probes, saving the connection, and the definition that makes the
scan recognise it next time. Fake servers only. Run from the repository root: python3 -m unittest discover tests
"""
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

import custom  # noqa: E402
import engines  # noqa: E402
import servers  # noqa: E402
import store  # noqa: E402

REPLY = {"choices": [{"message": {"role": "assistant", "content": "ready"}}]}
TOOL_CALL = {"choices": [{"message": {"role": "assistant", "content": None,
                                      "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "shoggoth_time", "arguments": "{}"}}]}}]}
ANTHROPIC = {"content": [{"type": "text", "text": "ready"}], "role": "assistant"}
ANTHROPIC_TOOL = {"content": [{"type": "tool_use", "id": "t1", "name": "shoggoth_time", "input": {}}], "role": "assistant"}
RESPONSES = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "ready"}]}], "output_text": "ready"}
RESPONSES_TOOL = {"output": [{"type": "function_call", "name": "shoggoth_time", "arguments": "{}"}]}
GEMINI = {"candidates": [{"content": {"parts": [{"text": "ready"}]}}]}
GEMINI_TOOL = {"candidates": [{"content": {"parts": [{"functionCall": {"name": "shoggoth_time", "args": {}}}]}}]}
OLLAMA = {"message": {"role": "assistant", "content": "ready"}}
OLLAMA_TOOL = {"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "shoggoth_time", "arguments": {}}}]}}


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR")}
        os.environ["SHOGGOTH_DATA_DIR"], os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)


def fake(replies, models=("m1",), status=200, tools_status=200):
    """A server that answers /v1/models and every chat path with `replies` (first reply for a chat, the tool one when tools are offered)."""
    class H(BaseHTTPRequestHandler):
        seen = []

        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.rstrip("/").endswith("/models") or self.path == "/api/tags":
                if status >= 400:
                    return self._send(status, {"error": {"message": "no"}})
                if self.path == "/api/tags":
                    return self._send(200, {"models": [{"name": m} for m in models]})
                return self._send(200, {"data": [{"id": m} for m in models]})
            return self._send(404, {})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            H.seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
            offers = bool(body.get("tools") or body.get("tool_choice"))
            if offers and tools_status != 200:
                return self._send(tools_status, {"error": {"message": "tools are not supported"}})
            table = dict(replies)
            key = "tools" if offers else "chat"
            out = table.get(key, {})
            return self._send(out.get("status", 200), out.get("body", {}))
    H.seen = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d" % srv.server_address[1], (lambda: (srv.shutdown(), srv.server_close())), H


class Probing(Env):
    def test_an_openai_server_that_chats_and_calls_tools(self):
        url, stop, _ = fake({"chat": {"body": REPLY}, "tools": {"body": TOOL_CALL}})
        try:
            r = custom.probe(url)
        finally:
            stop()
        self.assertTrue(r["ok"])
        self.assertEqual((r["chat"], r["tools"]), (True, True))
        self.assertIn("Chat works", r["message"])
        self.assertIn("Tool calls work", r["message"])

    def test_a_model_that_answers_but_never_calls_the_tool(self):
        url, stop, _ = fake({"chat": {"body": REPLY}, "tools": {"body": REPLY}})
        try:
            r = custom.probe(url)
        finally:
            stop()
        self.assertTrue(r["ok"])
        self.assertEqual(r["tools"], False)
        self.assertIn("may not be able to work in a folder", r["message"])

    def test_a_server_that_refuses_the_tool_request_says_only_that_part_failed(self):
        url, stop, _ = fake({"chat": {"body": REPLY}}, tools_status=500)
        try:
            r = custom.probe(url)
        finally:
            stop()
        self.assertTrue(r["ok"])
        self.assertIsNone(r["tools"])
        self.assertIn("Chat works", r["message"])
        self.assertIn("Tool calls not tried", r["message"])

    def test_a_key_it_refuses_is_said_so(self):
        url, stop, _ = fake({}, status=401)
        try:
            r = custom.probe(url)
        finally:
            stop()
        self.assertFalse(r["ok"])
        self.assertIn("key was refused", r["message"])

    def test_a_server_with_no_models_says_so_and_asks_for_a_name(self):
        url, stop, _ = fake({}, models=())
        try:
            r = custom.probe(url)
        finally:
            stop()
        self.assertFalse(r["ok"])
        self.assertIn("doesn't list any models", r["message"])

    def test_nothing_answering_is_plain_words(self):
        url, stop, _ = fake({})
        stop()
        r = custom.probe(url)
        self.assertFalse(r["ok"])
        self.assertIn("Nothing answered", r["message"])

    def test_each_format_is_probed_in_its_own_shape(self):
        cases = [("anthropic-messages", ANTHROPIC, ANTHROPIC_TOOL, "/v1/messages"),
                 ("openai-responses", RESPONSES, RESPONSES_TOOL, "/v1/responses"),
                 ("ollama-native", OLLAMA, OLLAMA_TOOL, "/api/chat")]
        for api, chat, tool, path in cases:
            url, stop, H = fake({"chat": {"body": chat}, "tools": {"body": tool}}, models=())
            try:
                r = custom.probe(url, api=api, model="m1")
            finally:
                stop()
            self.assertTrue(r["ok"], api + ": " + r["message"])
            self.assertEqual((r["chat"], r["tools"]), (True, True), api)
            self.assertEqual(H.seen[0]["path"], path, api)

    def test_gemini_native_uses_its_own_header_and_path(self):
        url, stop, H = fake({"chat": {"body": GEMINI}, "tools": {"body": GEMINI_TOOL}}, models=())
        try:
            r = custom.probe(url, key="k-123", api="google-generative-ai", model="gemini-2.5-pro")
        finally:
            stop()
        self.assertTrue(r["ok"], r["message"])
        self.assertEqual(H.seen[0]["headers"].get("x-goog-api-key"), "k-123")
        self.assertIn(":generateContent", H.seen[0]["path"])

    def test_anthropic_sends_its_key_and_version_headers(self):
        url, stop, H = fake({"chat": {"body": ANTHROPIC}, "tools": {"body": ANTHROPIC_TOOL}}, models=())
        try:
            custom.probe(url, key="sk-abc", api="anthropic-messages", model="claude")
        finally:
            stop()
        h = H.seen[0]["headers"]
        self.assertEqual((h.get("x-api-key"), h.get("anthropic-version")), ("sk-abc", "2023-06-01"))

    def test_a_bad_address_is_refused_in_plain_words(self):
        r = custom.probe("file:///etc/passwd")
        self.assertFalse(r["ok"])
        self.assertIn("http://", r["message"])

    def test_it_says_whether_the_address_stays_here(self):
        self.assertTrue(custom.probe("http://127.0.0.1:1")["local"])
        self.assertFalse(custom.probe("https://openrouter.ai")["local"])


class Saving(Env):
    def test_a_saved_connection_keeps_its_format_and_context(self):
        url, stop, _ = fake({"chat": {"body": REPLY}})
        try:
            r = custom.save(url, key="k", api="anthropic-messages", name="My Claude", ctx=65536)
        finally:
            stop()
        self.assertTrue(r["ok"])
        entry = servers.get(r["id"])
        self.assertEqual((entry["api"], entry["ctx"], entry["local"]), ("anthropic-messages", 65536, True))
        self.assertEqual(r["server"], {"id": r["id"], "name": "My Claude", "url": entry["url"]})

    def test_the_key_never_leaves_the_server(self):
        url, stop, _ = fake({})
        try:
            r = custom.save(url, key="sk-abcd1234efgh5678", name="With a key")
        finally:
            stop()
        self.assertNotIn("sk-abcd1234efgh5678", json.dumps(r))
        self.assertNotIn("sk-abcd1234efgh5678", json.dumps(servers.public()))
        self.assertEqual(servers.public()[0]["key_hint"], "5678")

    def test_remember_writes_a_definition_the_scan_can_use(self):
        url, stop, _ = fake({})
        try:
            r = custom.save(url, key="", name="Ninfer at home", remember=True, match=["ninfer-server"])
        finally:
            stop()
        self.assertTrue(r["ok"], r["problems"])
        path = os.path.join(engines.user_dir(), r["file"] + ".yaml")
        self.assertTrue(os.path.isfile(path))
        defs, problems = engines.read_file(path)
        self.assertEqual(problems, [])
        self.assertEqual((defs[0]["match"], defs[0]["url"]), (["ninfer-server"], servers.normalise(url)))
        self.assertIn(defs[0]["id"], [d["id"] for d in engines.definitions()])

    def test_the_definition_goes_through_the_validator_and_carries_no_command(self):
        url, stop, _ = fake({})
        try:
            r = custom.save(url, name="Mine", remember=True)
        finally:
            stop()
        path = os.path.join(engines.user_dir(), r["file"] + ".yaml")
        text = open(path, encoding="utf8").read()
        for word in ("start:", "command", "run:", "install:"):
            self.assertNotIn(word, text)

    def test_a_definition_that_would_be_refused_is_not_saved_at_all(self):
        url, stop, _ = fake({})
        try:
            bad = custom.definition(url)
            bad["start"] = ["sh", "-c", "rm -rf /"]                    # not a key the schema knows
            problem = custom._write_definition(bad)
        finally:
            stop()
        self.assertIn("unknown key", problem)
        self.assertEqual(os.listdir(engines.user_dir()) if os.path.isdir(engines.user_dir()) else [], [])

    def test_two_definitions_never_take_each_others_id(self):
        a_url, stop_a, _ = fake({})
        b_url, stop_b, _ = fake({})
        try:
            a = custom.save(a_url, name="Same name", remember=True)
            b = custom.save(b_url, name="Same name", remember=True)
        finally:
            stop_a()
            stop_b()
        self.assertNotEqual(a["file"], b["file"])
        self.assertEqual(sorted(os.listdir(engines.user_dir())), sorted([a["file"] + ".yaml", b["file"] + ".yaml"]))


class Learning(unittest.TestCase):
    def test_a_local_name_is_inferred_from_the_address(self):
        for url, want in (("http://127.0.0.1:8080", True), ("http://192.168.1.4:8000", True), ("http://desk-pc.local:1", True),
                          ("https://openrouter.ai/api", False), ("https://api.commandcode.ai", False)):
            self.assertEqual(custom.is_local(url), want, url)

    def test_the_slug_is_lowercase_and_safe(self):
        self.assertEqual(custom._slug("Ninfer -- at home!"), "ninfer-at-home")
        self.assertEqual(custom._slug("!!!"), "")


class Routes(Env):
    """The two new routes, over a real loopback HTTP server."""

    def setUp(self):
        super().setUp()
        import server
        import uiauth
        os.environ.pop("SHOGGOTH_AUTH", None)
        os.environ.pop("SHOGGOTH_TOKEN", None)
        self.server = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.auth = {"Authorization": "Bearer " + uiauth.token(), "Host": "127.0.0.1:%d" % self.port}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def call(self, path, body):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        c.request("POST", path, body=json.dumps(body), headers=self.auth)
        r = c.getresponse()
        return r.status, json.loads(r.read())

    def test_the_test_button_says_what_works(self):
        url, stop, _ = fake({"chat": {"body": REPLY}, "tools": {"body": TOOL_CALL}})
        try:
            st, d = self.call("/ui/custom/probe", {"url": url, "key": "", "api": "openai-completions"})
        finally:
            stop()
        self.assertEqual((st, d["ok"], d["tools"]), (200, True, True))

    def test_saving_connects_it_and_it_appears_as_a_source(self):
        url, stop, _ = fake({"chat": {"body": REPLY}})
        try:
            st, d = self.call("/ui/custom/save", {"url": url, "key": "sk-abcd1234efgh5678", "name": "My server", "api": "openai-completions", "ctx": 32768})
        finally:
            stop()
        self.assertEqual((st, d["ok"]), (200, True))
        self.assertNotIn("sk-abcd1234efgh5678", json.dumps(d))
        self.assertEqual([s["name"] for s in d["servers"]], ["My server"])
        self.assertEqual(d["servers"][0]["key_hint"], "5678")
        src = next(s for s in store.load()["servers"] if s["id"] == d["id"])
        self.assertEqual((src["api"], src["ctx"]), ("openai-completions", 32768))

    def test_a_bad_address_is_a_400(self):
        st, d = self.call("/ui/custom/save", {"url": "file:///etc/passwd"})
        self.assertEqual(st, 400)
        self.assertIn("http://", d["error"])


if __name__ == "__main__":
    unittest.main()
