"""What is Shoggoth's to stop, and what isn't: a Strata of yours on Shoggoth's old port, a process number given to another program, models
another program loaded into your Lemonade. Also: a Strata model keeps its name when it starts. Run: python3 -m unittest discover tests"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import engine  # noqa: E402
import lem  # noqa: E402
import provbase  # noqa: E402
import store  # noqa: E402
import strata  # noqa: E402



def _load(name):
    """A bundled provider.py, loaded the way the registry loads it (its folder shares a name with a module of Shoggoth's)."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth", "providers", name, "provider.py")
    spec = importlib.util.spec_from_file_location("zest_provider_test_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lemonade_provider, strata_provider = _load("lemonade"), _load("strata")


class Patch(unittest.TestCase):
    """Swap module attributes for the length of one test."""
    def setUp(self):
        self._undo = []
        self.tmp = tempfile.mkdtemp()
        self._env = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR")}
        os.environ["SHOGGOTH_DATA_DIR"], os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")

    def tearDown(self):
        for mod, name, old in reversed(self._undo):
            setattr(mod, name, old)
        for k, v in self._env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)
        strata._cache["v"] = None

    def patch(self, mod, name, value):
        self._undo.append((mod, name, getattr(mod, name)))
        setattr(mod, name, value)


class StrataOwnership(Patch):
    def discover(self, running, owned):
        strata._save(lambda s: s["owned"].update(owned))
        self.patch(strata, "_json", lambda args, timeout=20: {"running": running} if "--status" in args else {})
        self.patch(strata, "_health", lambda u: None)
        self.patch(strata, "_show_key", lambda: "")
        self.patch(strata, "_http_json", lambda u, key="", timeout=3: (200, {"data": []}))
        return strata._discover()

    def test_a_strata_of_yours_on_zests_old_port_stays_yours(self):
        out = self.discover([{"url": "http://127.0.0.1:8080", "name": "Mine", "profile": "launch-my-own", "port": 8080, "state": "ready"}],
                            {"preset": "Shoggoth IQ3_S", "port": 8080})
        self.assertEqual(out[0]["owner"], "yours")
        self.assertEqual(strata._settings()["owned"]["port"], 0)                    # and Shoggoth forgets the one it started, which has gone

    def test_the_one_zest_started_is_zests(self):
        out = self.discover([{"url": "http://127.0.0.1:8080", "name": "Q", "profile": "launch-shoggoth-iq3_s", "port": 8080, "state": "loading"}],
                            {"preset": "Shoggoth IQ3_S", "port": 8080})
        self.assertEqual(out[0]["owner"], "zest")
        self.assertIsNotNone(strata.zest_server())
        self.patch(strata, "running", lambda: out)
        self.assertEqual(strata_provider.Provider.owned_instance(None), "http://127.0.0.1:8080")    # still loading: stopped on quit too


class StrataNames(Patch):
    LIVE = {"id": "strata/served-name", "url": "http://127.0.0.1:8080", "key": "", "ctx": 131072, "bare": "served-name", "vision": False}
    DORMANT = {"id": "strata/qwen3.8-flash-next-iq3_s", "url": "http://127.0.0.1:8080", "key": "", "ctx": 131072, "bare": "qwen3.8-flash-next-iq3_s",
               "vision": False, "dormant": True, "preset": "Shoggoth IQ3_S"}

    def test_a_dormant_model_is_listed_under_the_name_its_server_gave_it(self):
        strata._save(lambda s: s["known"].update({"Shoggoth IQ3_S": {"id": "served-name", "ctx": 131072, "vision": False}}))
        self.patch(strata, "running", lambda: [])
        self.patch(strata, "binary", lambda: "/bin/true")
        self.patch(strata, "_presets_cached", lambda: [{"name": "Shoggoth IQ3_S", "model": "IQ3_S", "port": 8080}])
        self.patch(strata, "installed_models", lambda: [{"id": "iq3_s", "name": "qwen3.8-flash-next-iq3_s", "status": "present"}])
        self.assertEqual([m["id"] for m in strata.models()], ["strata/served-name"])

    def test_a_model_that_starts_under_another_name_is_still_found(self):
        state = {"started": False}
        self.patch(strata, "models", lambda: [self.LIVE] if state["started"] else [self.DORMANT])
        self.patch(strata, "start", lambda preset: state.update(started=True) or strata.RUN.update(state="ready") or True)
        self.patch(strata.time, "sleep", lambda s: None)
        m = strata.ensure("strata/qwen3.8-flash-next-iq3_s")
        self.assertEqual(m["bare"], "served-name")

    def test_loaded_is_safe_when_the_model_has_just_gone(self):
        self.patch(strata, "find_model", lambda name: None)
        self.assertEqual(strata_provider.Provider.loaded(None, "x"), (False, 0))

    def test_a_strata_on_another_computer_holds_no_memory_here(self):
        self.patch(strata, "running", lambda: [{"state": "ready", "source": "saved"}])
        self.assertFalse(strata_provider.Provider.is_running(None))


class StrataFromBeforeTheRename(Patch):
    def test_a_server_started_while_it_was_zest_is_still_its_own(self):
        out = StrataOwnership.discover(self, [{"url": "http://127.0.0.1:8080", "name": "Q", "profile": "launch-zest-iq3_s", "port": 8080, "state": "ready"}],
                                       {"preset": "Zest IQ3_S", "port": 8080})
        self.assertEqual(out[0]["owner"], "zest")

    def test_a_preset_it_made_as_zest_is_still_preferred(self):
        presets = [{"name": "Mine", "model": "IQ3_S", "last_used": "2026-10-29"}, {"name": "Zest IQ3_S", "model": "IQ3_S", "last_used": None}]
        self.assertEqual(strata._preset_for({"id": "iq3_s"}, presets)["name"], "Zest IQ3_S")


class LemonadeMakesWay(Patch):
    def setUp(self):
        super().setUp()
        saved = {}
        ctx = provbase.Context(settings=lambda pid: saved.get(pid, {}), save=lambda pid, **kv: saved.setdefault(pid, {}).update(kv))
        self.p = lemonade_provider.Provider({"id": "lemonade", "name": "Lemonade", "api_version": 1}, self.tmp, ctx)
        self.loaded = ["zest-loaded", "other-app"]
        self.unloaded = []
        self.patch(lem, "get", lambda path, timeout=8: {"all_models_loaded": [{"model_name": m} for m in self.loaded]})

        def request(method, path, body=None, timeout=10, **k):
            if path == "/v1/unload":
                self.unloaded.append((body or {}).get("model_name"))
                self.loaded = [m for m in self.loaded if body and m != body.get("model_name")]
            return 200, {}
        self.patch(lem, "request", request)
        self.p._mine(add="zest-loaded")

    def test_in_a_lemonade_you_run_only_zests_models_are_unloaded(self):
        self.patch(engine, "running", lambda: False)
        ok, msg = self.p.make_way()
        self.assertFalse(ok)
        self.assertIn("other-app", msg)
        self.assertEqual(self.unloaded, ["zest-loaded"])

    def test_models_it_loaded_as_zest_are_still_its_own(self):
        saved = {"lemonade": {"loaded_by_zest": ["old-one"]}}
        ctx = provbase.Context(settings=lambda pid: saved.get(pid, {}), save=lambda pid, **kv: saved.setdefault(pid, {}).update(kv))
        p = lemonade_provider.Provider({"id": "lemonade", "name": "Lemonade", "api_version": 1}, self.tmp, ctx)
        self.assertEqual(p._mine(add="new-one"), ["old-one", "new-one"])
        self.assertEqual(saved["lemonade"]["loaded_by_shoggoth"], ["old-one", "new-one"])

    def test_a_lemonade_zest_runs_is_emptied(self):
        self.patch(engine, "running", lambda: True)
        self.assertEqual(self.p.make_way(), (True, ""))
        self.assertEqual(self.unloaded, [None])


class ProcessNumbers(unittest.TestCase):
    def test_a_number_given_to_another_program_is_never_signalled(self):
        other = subprocess.Popen(["sleep", "30"], start_new_session=True)
        try:
            tmp = tempfile.mkdtemp()
            p = provbase.GenericProvider({"id": "x", "name": "X", "api_version": 1}, tmp, provbase.Context())
            p._owned, p._pid, p._port, p._t = True, other.pid, 1, -1         # Shoggoth's process was this number, started at another time
            self.assertIsNone(p.owned_instance())
            p.stop()
            time.sleep(0.2)
            self.assertIsNone(other.poll())                                    # the program now using that number is left alone
            shutil.rmtree(tmp, ignore_errors=True)
        finally:
            other.kill()
            other.wait()


class LemonadeReplies(unittest.TestCase):
    def test_a_garbled_reply_is_status_0_not_an_exception(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)

        def serve():
            c, _ = s.accept()
            c.recv(4096)
            c.sendall(b"not http at all\r\n\r\n")
            c.close()
        threading.Thread(target=serve, daemon=True).start()
        status, payload = lem.request("GET", "/v1/health", host="127.0.0.1", port=s.getsockname()[1], timeout=5)
        s.close()
        self.assertEqual(status, 0)
        self.assertTrue(payload["error"])


if __name__ == "__main__":
    unittest.main()
