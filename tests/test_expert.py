"""Expert Mode is off until the person turns it on, and turning it off keeps what they set. Run: python3 -m unittest discover tests"""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import expert  # noqa: E402
import server  # noqa: E402
import store  # noqa: E402
import uiauth  # noqa: E402


class Switch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR", "SHOGGOTH_AUTH", "SHOGGOTH_TOKEN")}
        os.environ["SHOGGOTH_DATA_DIR"], os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")
        os.environ.pop("SHOGGOTH_AUTH", None)
        os.environ.pop("SHOGGOTH_TOKEN", None)

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_it_is_off_by_default_and_a_fresh_settings_file_says_so(self):
        self.assertFalse(expert.on())
        self.assertEqual(store.load()["expert"], {"on": False})

    def test_an_old_settings_file_without_the_key_reads_as_off(self):
        os.makedirs(store.config_dir(), exist_ok=True)
        with open(os.path.join(store.config_dir(), "settings.json"), "w") as f:
            json.dump({"mode": "builtin", "setup_done": True}, f)
        self.assertFalse(expert.on())

    def test_turning_it_on_and_off_keeps_every_other_setting(self):
        s = store.load()
        s["strata"]["idle_minutes"] = 5
        s["providers"]["maya"] = {"authority": "ask"}               # stands in for settings the later features keep
        store.save(s)
        expert.set_on(True)
        self.assertTrue(expert.on())
        expert.set_on(False)
        self.assertFalse(expert.on())
        s = store.load()
        self.assertEqual((s["strata"]["idle_minutes"], s["providers"]["maya"]), (5, {"authority": "ask"}))

    def test_on_reads_the_settings_it_is_given(self):
        self.assertTrue(expert.on({"expert": {"on": True}}))
        self.assertFalse(expert.on({"expert": {}}))
        self.assertFalse(expert.on({}))

    def test_the_page_reads_and_sets_it_over_the_ui_routes(self):
        srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]
        auth = {"Authorization": "Bearer " + uiauth.token(), "Host": "127.0.0.1:%d" % port}

        def call(method, body=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request(method, "/ui/expert", body=body, headers=auth)
            r = c.getresponse()
            return r.status, json.loads(r.read())
        try:
            self.assertEqual(call("GET"), (200, {"on": False}))
            self.assertEqual(call("POST", '{"on": true}'), (200, {"on": True}))
            self.assertEqual(call("GET"), (200, {"on": True}))
            self.assertTrue(expert.on())
            self.assertEqual(call("POST", '{"on": false}'), (200, {"on": False}))
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request("GET", "/ui/expert", headers={"Host": "127.0.0.1:%d" % port})
            self.assertEqual(c.getresponse().status, 401)                       # not without the key
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
