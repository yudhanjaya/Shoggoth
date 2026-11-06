"""Servers the person connected under Advanced > Engines. Run: python3 -m unittest discover tests"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import provbase  # noqa: E402
import servers  # noqa: E402
import store  # noqa: E402


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR")}
        os.environ["SHOGGOTH_DATA_DIR"], os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)


def fake_server(status=200, models=("m1",), meta=None):
    """A server that answers /v1/models like an OpenAI-style one. Returns (base url, stop)."""
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"data": [dict({"id": m}, **({"meta": meta} if meta else {})) for m in models]}).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d" % srv.server_address[1], lambda: (srv.shutdown(), srv.server_close())


class Saving(Env):
    def test_normalise_spellings_are_one_address(self):
        for u in ("http://localhost:11434/v1/", "http://localhost:11434/", "HTTP://LocalHost:11434"):
            self.assertEqual(servers.normalise(u), "http://localhost:11434")

    def test_normalise_refuses(self):
        for u in ("file:///etc/passwd", "ftp://x", "http://u:p@host", "", "http://", "http://h:99999"):
            with self.assertRaises(ValueError, msg=u):
                servers.normalise(u)

    def test_add_twice_gives_distinct_ids(self):
        a = servers.add("ollama", "http://127.0.0.1:11434")
        b = servers.add("ollama", "http://192.168.1.5:11434")
        self.assertEqual((a["id"], b["id"]), ("ollama", "ollama-2"))

    def test_the_same_address_in_two_spellings_is_refused(self):
        servers.add("ollama", "http://localhost:11434")
        with self.assertRaises(ValueError):
            servers.add("other", "http://127.0.0.1:11434/v1")

    def test_public_never_returns_the_key(self):
        servers.add("strata", "http://h:1", key="sk-abcd1234efgh5678")
        self.assertNotIn("sk-abcd1234efgh5678", json.dumps(servers.public()))
        self.assertEqual(servers.public()[0]["key_hint"], "5678")

    def test_a_short_key_shows_no_hint(self):
        servers.add("strata", "http://h:1", key="abc")
        self.assertEqual((servers.public()[0]["has_key"], servers.public()[0]["key_hint"]), (True, ""))

    def test_remove(self):
        s = servers.add("ollama", "http://h:1")
        servers.remove(s["id"])
        self.assertEqual(servers.all(), [])

    def test_unknown_preset_is_refused(self):
        with self.assertRaises(ValueError):
            servers.add("nonsense", "http://h:1")

    def test_a_preset_with_no_address_uses_its_default(self):
        self.assertEqual(servers.add("ollama", "")["url"], "http://127.0.0.1:11434")

    def test_presets_have_no_launch_fields(self):
        names = {p["id"] for p in servers.presets()}
        self.assertTrue({"ollama", "lmstudio", "llama-server", "strata", "maya", "unsloth", "other"} <= names)
        for p in servers.presets():
            self.assertLessEqual(set(p), {"id", "name", "url", "hint"})


class Probing(Env):
    def test_probe_reads_models(self):
        url, stop = fake_server(models=("m1", "m2"))
        try:
            self.assertEqual(servers.probe(url), {"reachable": True, "needs_key": False, "models": ["m1", "m2"]})
            self.assertEqual(servers.probe(url + "/v1/")["models"], ["m1", "m2"])
        finally:
            stop()

    def test_probe_says_when_a_key_is_needed(self):
        url, stop = fake_server(status=401)
        try:
            self.assertEqual(servers.probe(url), {"reachable": True, "needs_key": True, "models": []})
        finally:
            stop()

    def test_probe_of_a_closed_port_or_a_bad_address_does_not_raise(self):
        url, stop = fake_server()
        stop()
        self.assertFalse(servers.probe(url)["reachable"])
        self.assertFalse(servers.probe("nonsense")["reachable"])

    def test_a_server_with_no_models_is_reachable_not_an_error(self):
        url, stop = fake_server(models=())
        try:
            self.assertEqual(servers.probe(url), {"reachable": True, "needs_key": False, "models": []})
        finally:
            stop()


class AsAnEngine(Env):
    def provider(self, entry):
        return servers.ConnectedServer(entry, provbase.Context(settings=lambda pid: {}))

    def test_it_lists_models_hands_out_its_address_and_never_stops(self):
        url, stop = fake_server(models=("m1",), meta={"n_ctx": 4096})
        try:
            e = servers.add("ollama", url, key="k")
            p = self.provider(e)
            self.assertEqual([m["id"] for m in p.models()], ["m1"])
            ep = p.endpoint("m1")
            self.assertEqual((ep["base_url"], ep["key"], ep["ctx"]), (url + "/v1", "k", 4096))
            self.assertEqual(p.ref("m1"), "ollama/m1")
            ok, _ = p.stop()
            self.assertFalse(ok)
            self.assertIsNone(p.owned_instance())
            self.assertEqual(p.memory()["group"], "")
            self.assertEqual(p.ensure("m1", lambda *_: None)["model"], "m1")
        finally:
            stop()

    def test_ensure_on_a_server_that_is_down_says_it_does_not_start_it(self):
        url, stop = fake_server()
        stop()
        p = self.provider(servers.add("ollama", url))
        with self.assertRaises(provbase.ProviderError) as cm:
            p.ensure("m1", lambda *_: None)
        self.assertIn("doesn't start it", str(cm.exception))

    def test_a_changed_key_is_used_without_a_restart(self):
        url, stop = fake_server()
        try:
            e = servers.add("ollama", url, key="old")
            p = self.provider(e)
            store.update(lambda s: s["servers"][0].__setitem__("key", "new"))
            self.assertEqual(p.endpoint("m1")["key"], "new")
        finally:
            stop()


class Routes(Env):
    def setUp(self):
        super().setUp()
        import http.client  # noqa: F401
        import server
        import uiauth
        os.environ.pop("SHOGGOTH_AUTH", None)
        os.environ.pop("SHOGGOTH_TOKEN", None)
        self.srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.auth = {"Authorization": "Bearer " + uiauth.token(), "Host": "127.0.0.1:%d" % self.port}

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def call(self, method, path, body=None, auth=True):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=self.auth if auth else {"Host": self.auth["Host"]})
        r = c.getresponse()
        raw = r.read()
        return r.status, raw

    def test_add_then_list_then_remove(self):
        url, stop = fake_server(models=("m1",))
        try:
            st, raw = self.call("POST", "/ui/servers", {"action": "add", "preset": "ollama", "url": url, "key": "sk-secret-0000"})
            self.assertEqual(st, 200)
            sid = json.loads(raw)["server"]["id"]
            st, raw = self.call("GET", "/ui/servers")
            d = json.loads(raw)
            self.assertEqual([s["id"] for s in d["servers"]], [sid])
            self.assertIn("ollama", [p["id"] for p in d["presets"]])
            st, raw = self.call("GET", "/ui/providers")
            ours = next(p for p in json.loads(raw)["providers"] if p["id"] == sid)
            self.assertEqual((ours["connected"], ours["models"]), (True, ["m1"]))
            self.assertEqual(self.call("POST", "/ui/servers", {"action": "remove", "id": sid})[0], 200)
            self.assertEqual(json.loads(self.call("GET", "/ui/servers")[1])["servers"], [])
            self.assertNotIn(sid, [p["id"] for p in json.loads(self.call("GET", "/ui/providers")[1])["providers"]])
        finally:
            stop()

    def test_a_bad_address_is_a_400_in_plain_words(self):
        st, raw = self.call("POST", "/ui/servers", {"action": "add", "preset": "other", "url": "file:///etc/passwd"})
        self.assertEqual(st, 400)
        self.assertIn("http://", json.loads(raw)["error"])
        self.assertNotIn("Traceback", raw.decode())

    def test_the_key_is_in_no_response(self):
        url, stop = fake_server()
        try:
            out = [self.call("POST", "/ui/servers", {"action": "add", "preset": "ollama", "url": url, "key": "sk-abcd1234efgh5678"})[1],
                   self.call("GET", "/ui/servers")[1], self.call("GET", "/ui/providers")[1],
                   self.call("POST", "/ui/servers", {"action": "test", "url": url, "key": "sk-abcd1234efgh5678"})[1]]
            for raw in out:
                self.assertNotIn(b"sk-abcd1234efgh5678", raw)
        finally:
            stop()

    def test_test_does_not_save_anything(self):
        url, stop = fake_server(models=("a", "b"))
        try:
            st, raw = self.call("POST", "/ui/servers", {"action": "test", "url": url})
            self.assertEqual(json.loads(raw)["models"], ["a", "b"])
            self.assertEqual(servers.all(), [])
        finally:
            stop()

    def test_it_needs_the_page_key(self):
        for m, path in (("GET", "/ui/servers"), ("POST", "/ui/servers")):
            self.assertEqual(self.call(m, path, {}, auth=False)[0], 401)


if __name__ == "__main__":
    unittest.main()
