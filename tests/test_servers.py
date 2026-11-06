"""Servers the person connected under Advanced > Engines. Run: python3 -m unittest discover tests"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
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


class Discovering(Env):
    """The "Probe and connect" button: look at each preset's usual address, connect what answers."""
    def presets_at(self, **urls):
        from unittest import mock
        pre = [{"id": k, "name": k.title(), "url": u, "hint": ""} for k, u in urls.items()] + [{"id": "other", "name": "Another server", "url": "", "hint": ""}]
        return mock.patch.object(servers, "presets", lambda: pre)

    def test_it_connects_what_answers_and_skips_the_rest(self):
        (a, sa), (b, sb) = fake_server(models=("m1",)), fake_server(status=401)
        sc = fake_server()
        sc[1]()                                                   # a closed port
        try:
            with self.presets_at(ollama=a, lmstudio=b, unsloth=sc[0]):
                r = servers.connect_found()
            self.assertEqual((r["found"], [c["id"] for c in r["connected"]], r["needs_key"]), (2, ["ollama"], [b]))
            self.assertEqual([s["id"] for s in servers.all()], ["ollama"])
        finally:
            sa()
            sb()

    def test_a_second_press_connects_nothing_new(self):
        a, sa = fake_server()
        try:
            with self.presets_at(ollama=a):
                servers.connect_found()
                r = servers.connect_found()
            self.assertEqual((r["connected"], r["already"]), ([], [servers.normalise(a)]))
            self.assertEqual(len(servers.all()), 1)
        finally:
            sa()

    def test_an_address_several_servers_use_is_offered_as_another_server_named_by_address(self):
        a, sa = fake_server()
        try:
            with self.presets_at(strata=a, maya=a, llama_server=a):
                found = servers.discover()
            self.assertEqual([(f["preset"], f["name"]) for f in found], [("other", "Server at " + a.split("//")[1])])
        finally:
            sa()

    def test_nothing_answering_is_not_an_error(self):
        a, sa = fake_server()
        sa()
        with self.presets_at(ollama=a):
            self.assertEqual(servers.connect_found(), {"connected": [], "needs_key": [], "already": [], "unreachable": [], "found": 0})

    def test_it_finds_nothing_on_a_computer_with_no_servers_without_waiting(self):
        t = time.time()
        servers.discover()
        self.assertLess(time.time() - t, 4.0)


class TypedAddress(Env):
    """The one yellow button also looks at the address in the box."""
    def only_other(self):
        from unittest import mock
        return mock.patch.object(servers, "presets", lambda: [{"id": "other", "name": "Another server", "url": "", "hint": ""}])

    def test_the_typed_address_is_connected_with_its_key(self):
        url, stop = fake_server()
        try:
            with self.only_other():
                r = servers.connect_found(typed={"preset": "other", "url": url + "/v1/", "key": "k1"})
            self.assertEqual([c["url"] for c in r["connected"]], [servers.normalise(url)])
            self.assertEqual(servers.all()[0]["key"], "k1")
        finally:
            stop()

    def test_a_typed_address_that_wants_a_key_is_not_connected_without_one(self):
        url, stop = fake_server(status=401)
        try:
            with self.only_other():
                r = servers.connect_found(typed={"preset": "other", "url": url, "key": ""})
            self.assertEqual((r["connected"], r["needs_key"]), ([], [servers.normalise(url)]))
        finally:
            stop()

    def test_a_typed_address_nothing_answers_at_is_named(self):
        url, stop = fake_server()
        stop()
        with self.only_other():
            r = servers.connect_found(typed={"preset": "other", "url": url})
        self.assertEqual((r["connected"], r["unreachable"], r["found"]), ([], [servers.normalise(url)], 0))

    def test_an_empty_box_still_looks_at_the_usual_addresses(self):
        a, sa = fake_server()
        try:
            from unittest import mock
            with mock.patch.object(servers, "presets", lambda: [{"id": "ollama", "name": "Ollama", "url": a, "hint": ""}, {"id": "other", "name": "Another server", "url": "", "hint": ""}]):
                r = servers.connect_found(typed={"preset": "ollama", "url": ""})
            self.assertEqual([c["id"] for c in r["connected"]], ["ollama"])
        finally:
            sa()

    def test_the_typed_address_is_not_connected_twice_when_it_is_also_a_usual_one(self):
        a, sa = fake_server()
        try:
            from unittest import mock
            with mock.patch.object(servers, "presets", lambda: [{"id": "ollama", "name": "Ollama", "url": a, "hint": ""}, {"id": "other", "name": "Another server", "url": "", "hint": ""}]):
                r = servers.connect_found(typed={"preset": "ollama", "url": a})
            self.assertEqual(len(r["connected"]), 1)
            self.assertEqual(len(servers.all()), 1)
        finally:
            sa()


class Migrating(Env):
    def old(self, remote, keys):
        store.update(lambda s: s.__setitem__("strata", {"remote": remote, "keys": keys, "owned": {"preset": "", "port": 0}, "known": {}, "idle_minutes": 20}))

    def test_remote_addresses_and_keys_carry_over(self):
        self.old([{"url": "http://10.0.0.5:8080/"}, {"url": "http://10.0.0.6:8080"}], {"http://10.0.0.5:8080": "k1", "http://10.0.0.6:8080": "k2"})
        self.assertEqual(servers.migrate_strata(), 2)
        got = {s["id"]: (s["url"], s["key"]) for s in servers.all()}
        self.assertEqual(got, {"strata": ("http://10.0.0.5:8080", "k1"), "strata-2": ("http://10.0.0.6:8080", "k2")})

    def test_a_second_run_changes_nothing(self):
        self.old([{"url": "http://10.0.0.5:8080"}], {})
        servers.migrate_strata()
        before = Path(os.path.join(store.config_dir(), "settings.json")).read_text()
        self.assertEqual(servers.migrate_strata(), 0)
        self.assertEqual(Path(os.path.join(store.config_dir(), "settings.json")).read_text(), before)

    def test_a_server_removed_afterwards_does_not_come_back(self):
        self.old([{"url": "http://10.0.0.5:8080"}], {})
        servers.migrate_strata()
        servers.remove("strata")
        servers.migrate_strata()
        self.assertEqual(servers.all(), [])

    def test_a_half_written_block_is_skipped_not_fatal(self):
        store.update(lambda s: s.__setitem__("strata", {"remote": [{}, "x", {"url": "file:///x"}, {"url": "http://10.0.0.5:8080"}], "keys": None}))
        self.assertEqual(servers.migrate_strata(), 1)
        store.update(lambda s: (s.__setitem__("migrated", []), s.__setitem__("strata", "nonsense"), s.__setitem__("servers", [])))
        self.assertEqual(servers.migrate_strata(), 0)

    def test_a_fresh_install_has_nothing_to_migrate(self):
        self.assertEqual(servers.migrate_strata(), 0)


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

    def probe(self, **body):
        from unittest import mock
        only_typed = [{"id": "other", "name": "Another server", "url": "", "hint": ""}]          # nothing usual to scan: only what was typed
        with mock.patch.object(servers, "presets", lambda: only_typed):
            return self.call("POST", "/ui/servers", dict(action="probe", preset="other", **body))

    def test_probe_connects_the_typed_address_then_lists_and_removes_it(self):
        url, stop = fake_server(models=("m1",))
        try:
            st, raw = self.probe(url=url, key="sk-secret-0000")
            d = json.loads(raw)
            self.assertEqual((st, [c["url"] for c in d["connected"]]), (200, [servers.normalise(url)]))
            sid = d["connected"][0]["id"]
            d = json.loads(self.call("GET", "/ui/servers")[1])
            self.assertEqual([s["id"] for s in d["servers"]], [sid])
            ours = next(p for p in json.loads(self.call("GET", "/ui/providers")[1])["providers"] if p["id"] == sid)
            self.assertEqual((ours["connected"], ours["models"]), (True, ["m1"]))
            self.assertEqual(self.call("POST", "/ui/servers", {"action": "remove", "id": sid})[0], 200)
            self.assertEqual(json.loads(self.call("GET", "/ui/servers")[1])["servers"], [])
            self.assertNotIn(sid, [p["id"] for p in json.loads(self.call("GET", "/ui/providers")[1])["providers"]])
        finally:
            stop()

    def test_a_bad_address_is_a_400_in_plain_words(self):
        st, raw = self.probe(url="file:///etc/passwd")
        self.assertEqual(st, 400)
        self.assertIn("http://", json.loads(raw)["error"])
        self.assertNotIn("Traceback", raw.decode())

    def test_the_key_is_in_no_response(self):
        url, stop = fake_server()
        try:
            out = [self.probe(url=url, key="sk-abcd1234efgh5678")[1], self.call("GET", "/ui/servers")[1], self.call("GET", "/ui/providers")[1]]
            for raw in out:
                self.assertNotIn(b"sk-abcd1234efgh5678", raw)
        finally:
            stop()

    def test_an_address_nothing_answers_at_is_reported_and_not_saved(self):
        url, stop = fake_server()
        stop()
        st, raw = self.probe(url=url)
        d = json.loads(raw)
        self.assertEqual((st, d["connected"], d["unreachable"]), (200, [], [servers.normalise(url)]))
        self.assertEqual(servers.all(), [])

    def test_the_old_add_and_test_actions_are_gone(self):
        for act in ("add", "test"):
            self.assertEqual(self.call("POST", "/ui/servers", {"action": act, "url": "http://h:1"})[0], 400)

    def test_probe_connects_what_answers(self):
        from unittest import mock
        a, sa = fake_server(models=("m1",))
        try:
            with mock.patch.object(servers, "presets", lambda: [{"id": "ollama", "name": "Ollama", "url": a, "hint": ""}, {"id": "other", "name": "Another server", "url": "", "hint": ""}]):
                st, raw = self.call("POST", "/ui/servers", {"action": "probe"})
            d = json.loads(raw)
            self.assertEqual((st, [c["id"] for c in d["connected"]], [s["id"] for s in d["servers"]]), (200, ["ollama"], ["ollama"]))
            self.assertEqual([m["id"] for p in json.loads(self.call("GET", "/ui/providers")[1])["providers"] if p["id"] == "ollama" for m in [{"id": x} for x in p["models"]]], ["m1"])
        finally:
            sa()

    def test_it_needs_the_page_key(self):
        for m, path in (("GET", "/ui/servers"), ("POST", "/ui/servers")):
            self.assertEqual(self.call(m, path, {}, auth=False)[0], 401)


class NothingShippedForOtherEngines(unittest.TestCase):
    """Shoggoth ships Lemonade only. Other servers are connected by address, and nothing here may install, start, update or tune them."""
    # names and default addresses only; models.yaml has the owner's own "coming soon" text, which says "Project Maya"
    ALLOWED = {"servers.py", os.path.join("recommendations", "servers.yaml"), os.path.join("recommendations", "models.yaml"), "index.html", "docs.md"}

    def test_shipped_code_has_no_strata_or_maya_setup(self):
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth")
        bad = []
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in ("_vendor", "vendor", "__pycache__", "node_modules", "pi", "skills")]
            for f in files:
                if not f.endswith((".py", ".yaml", ".html", ".md", ".js")):
                    continue
                path = os.path.join(base, f)
                text = open(path, encoding="utf8").read().lower() if "base64," not in f else ""
                rel = os.path.relpath(path, root)
                for marker in ("strata launch", "strata tune", "strata update", "install.sh", "import strata"):
                    if marker in text:
                        bad.append((rel, marker))
                if "maya" in text and rel not in self.ALLOWED:
                    bad.append((rel, "maya"))
        self.assertEqual(bad, [])

    def test_the_only_bundled_engine_is_lemonade(self):
        folders = [d for d in os.listdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth", "providers")) if not d.startswith("_")]
        self.assertEqual(folders, ["lemonade"])


if __name__ == "__main__":
    unittest.main()
