"""S5: installing and removing curated Pi packages from the page, with progress while it runs. The fake npm registry is the one
tests/test_pipkgs.py uses. Run: python3 -W ignore -m unittest discover tests"""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "shoggoth"))

import agent  # noqa: E402
import pipkgs  # noqa: E402
import piup  # noqa: E402
import server  # noqa: E402
import uiauth  # noqa: E402
from test_pipkgs import Registry, sri  # noqa: E402

IDLE = {"state": "idle", "name": None, "done": 0, "total": 0, "error": None}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR", "SHOGGOTH_AUTH", "SHOGGOTH_TOKEN")}
        os.environ["SHOGGOTH_DATA_DIR"] = os.path.join(self.tmp, "data")
        os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "config")
        os.environ.pop("SHOGGOTH_AUTH", None)
        os.environ.pop("SHOGGOTH_TOKEN", None)
        self.reg = Registry()
        self._reg = piup.REGISTRY
        piup.REGISTRY = self.reg.base + "/"
        pipkgs.UPD.update(IDLE)
        self.entries = []

    def tearDown(self):
        pipkgs.UPD.update(IDLE)
        piup.REGISTRY = self._reg
        self.reg.close()
        for k, v in self._env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, name, version, deps=None, **kw):
        integ = self.reg.add(name, version, deps=deps, **kw)
        if not any(e["name"] == name for e in self.entries):
            self.entries.append({"name": name, "version": version, "integrity": integ, "enabled": True, "notes": "Adds things."})
        return integ

    def entry(self, name, version, enabled=True, notes="Adds things.", integrity=None):
        if integrity is None:
            _, data, over = self.reg.pkgs[name][version]
            integrity = over or sri(data)
        return {"name": name, "version": version, "integrity": integrity, "enabled": enabled, "notes": notes}

    def wait(self, pred, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return True
            time.sleep(0.02)
        return False


class Installing(Base):
    def test_a_package_and_its_dependencies_are_installed_in_a_thread_with_progress(self):
        self.add("app", "1.0.0", deps={"left": "^1.0.0", "right": "^2.0.0"})
        self.add("left", "1.2.0")
        self.add("right", "2.0.0")
        gate, done = threading.Event(), threading.Event()

        def slow_meta(name, version):
            gate.wait(10)
            return real_meta(name, version)
        real_meta = pipkgs._meta
        with mock.patch.object(pipkgs, "_meta", slow_meta):
            self.assertTrue(pipkgs.start_install("app", self.entries, on_done=done.set))
            self.assertEqual(pipkgs.UPD["state"], "installing")
            self.assertEqual((pipkgs.UPD["name"], pipkgs.UPD["done"], pipkgs.UPD["total"]), ("app", 0, 1))
            self.assertEqual(pipkgs.installed(), [])
            gate.set()
            self.assertTrue(done.wait(15), "the install never finished")
        self.assertTrue(self.wait(lambda: pipkgs.UPD["state"] == "done"))
        self.assertEqual(pipkgs.UPD["done"], pipkgs.UPD["total"])
        self.assertEqual(pipkgs.UPD["total"], 3)                       # app, left, right
        self.assertEqual([(p["name"], p["version"]) for p in pipkgs.installed()], [("app", "1.0.0")])

    def test_a_name_that_is_not_pinned_is_refused_at_once(self):
        self.reg.add("loose", "1.0.0")
        with self.assertRaises(RuntimeError) as c:
            pipkgs.start_install("loose", self.entries)
        self.assertIn("list", str(c.exception))
        self.assertEqual(pipkgs.UPD["state"], "idle")
        self.assertEqual(pipkgs.installed(), [])

    def test_a_disabled_entry_is_refused_at_once(self):
        self.add("app", "1.0.0")
        entries = [self.entry("app", "1.0.0", enabled=False)]
        with self.assertRaises(RuntimeError) as c:
            pipkgs.start_install("app", entries)
        self.assertIn("tested", str(c.exception))
        self.assertEqual(pipkgs.UPD["state"], "idle")
        self.assertEqual(pipkgs.installed(), [])

    def test_a_failed_install_reports_its_reason(self):
        self.add("evil", "1.0.0", integrity=sri(b"not it"))
        self.assertTrue(pipkgs.start_install("evil", self.entries))
        self.assertTrue(self.wait(lambda: pipkgs.UPD["state"] == "error"))
        self.assertIn("checksum", pipkgs.UPD["error"])
        self.assertEqual(pipkgs.installed(), [])

    def test_remove_deletes_the_files(self):
        self.add("app", "1.0.0")
        pipkgs.install("app", self.entries)
        self.assertTrue(pipkgs.remove("app"))
        self.assertEqual(pipkgs.installed(), [])
        self.assertFalse(pipkgs.remove("app"))


class Listing(Base):
    def test_each_entry_says_what_it_is_and_what_it_adds(self):
        self.add("app", "1.0.0")
        self.reg.add("other", "3.2.1")
        self.entries.append(self.entry("other", "3.2.1", enabled=False, notes="Not for 5.0."))
        pipkgs.install("app", self.entries)
        with mock.patch.object(pipkgs, "curated", lambda: self.entries):
            got = pipkgs.summary()
        self.assertEqual([p["name"] for p in got["packages"]], ["app", "other"])
        app, other = got["packages"]
        self.assertEqual((app["installed"], app["installed_version"], app["enabled"]), (True, "1.0.0", True))
        self.assertEqual((other["installed"], other["enabled"], other["note"]), (False, False, "Not for 5.0."))
        self.assertEqual(app["runs_code"], "Runs code on your computer")
        self.assertEqual(got["install"]["state"], "done")


class Routes(Base):
    def setUp(self):
        super().setUp()
        self._curated = pipkgs.curated
        pipkgs.curated = lambda: self.entries
        self.srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.auth = {"Authorization": "Bearer " + uiauth.token(), "Host": "127.0.0.1:%d" % self.port}

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        pipkgs.curated = self._curated
        super().tearDown()

    def call(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=self.auth)
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")

    def test_the_page_lists_the_pinned_packages(self):
        self.add("app", "1.0.0")
        st, d = self.call("GET", "/ui/packages")
        self.assertEqual(st, 200)
        self.assertEqual([p["name"] for p in d["packages"]], ["app"])
        self.assertEqual((d["packages"][0]["installed"], d["packages"][0]["enabled"]), (False, True))
        self.assertEqual(d["packages"][0]["runs_code"], "Runs code on your computer")
        self.assertEqual(d["packages"][0]["note"], "Adds things.")
        self.assertEqual(d["install"]["state"], "idle")

    def test_install_runs_in_a_thread_and_restarts_idle_chats_when_done(self):
        self.add("app", "1.0.0")
        restarted = threading.Event()
        gate = threading.Event()
        real = pipkgs._meta

        def slow_meta(name, version):
            gate.wait(10)
            return real(name, version)
        with mock.patch.object(pipkgs, "_meta", slow_meta), \
             mock.patch.object(agent, "restart_idle", restarted.set):
            st, d = self.call("POST", "/ui/packages/install", {"name": "app"})
            self.assertEqual(st, 200)
            self.assertTrue(d["started"])
            self.assertEqual(d["packages"]["install"]["state"], "installing")
            self.assertEqual(pipkgs.installed(), [])                  # nothing waits for the download
            gate.set()
            self.assertTrue(restarted.wait(15), "idle chats were not restarted")
        self.assertTrue(self.wait(lambda: pipkgs.UPD["state"] == "done"))
        self.assertEqual([(p["name"], p["version"]) for p in pipkgs.installed()], [("app", "1.0.0")])

    def test_install_refuses_anything_not_on_the_pinned_list(self):
        self.reg.add("loose", "1.0.0")
        st, d = self.call("POST", "/ui/packages/install", {"name": "loose"})
        self.assertEqual(st, 400)
        self.assertIn("list", d["error"])
        self.assertEqual(pipkgs.installed(), [])

    def test_install_refuses_an_entry_that_is_not_switched_on(self):
        self.add("app", "1.0.0")
        self.entries[0]["enabled"] = False
        st, d = self.call("POST", "/ui/packages/install", {"name": "app"})
        self.assertEqual(st, 400)
        self.assertIn("tested", d["error"])
        self.assertEqual(pipkgs.installed(), [])

    def test_remove_deletes_it_and_restarts_idle_chats(self):
        self.add("app", "1.0.0")
        pipkgs.install("app", self.entries)
        with mock.patch.object(agent, "restart_idle", lambda: self.restarted.append(1)):
            self.restarted = []
            st, d = self.call("POST", "/ui/packages/remove", {"name": "app"})
        self.assertEqual((st, d["removed"]), (200, True))
        self.assertEqual(pipkgs.installed(), [])
        self.assertEqual(self.restarted, [1])
        st, d = self.call("GET", "/ui/packages")
        self.assertFalse(d["packages"][0]["installed"])

    def test_remove_refuses_a_strange_name(self):
        for name in ("../escape", "", None, "Upper", "with space"):
            st, d = self.call("POST", "/ui/packages/remove", {"name": name})
            self.assertEqual(st, 400, repr(name))
            self.assertTrue(d.get("error"))


if __name__ == "__main__":
    unittest.main()
