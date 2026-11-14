"""What is Shoggoth's to stop, and what isn't: a process number given to another program; everything its own llama.cpp loaded.
Run: python3 -m unittest discover tests"""
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

import llamacpp  # noqa: E402
import provbase  # noqa: E402
import store  # noqa: E402


def _load(name):
    """A bundled provider.py, loaded the way the registry loads it (its folder shares a name with a module of Shoggoth's)."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth", "providers", name, "provider.py")
    spec = importlib.util.spec_from_file_location("zest_provider_test_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


llamacpp_provider = _load("llamacpp")


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

    def patch(self, mod, name, value):
        self._undo.append((mod, name, getattr(mod, name)))
        setattr(mod, name, value)


class LlamacppMakesWay(Patch):
    def setUp(self):
        super().setUp()
        self.p = llamacpp_provider.Provider({"id": "llamacpp", "name": "llama.cpp", "api_version": 1}, self.tmp, provbase.Context())
        self.loaded = ["a", "b"]
        self.unloaded = []
        self.patch(llamacpp, "loaded_ids", lambda: list(self.loaded))

        def unload(mid):
            self.unloaded.append(mid)
            self.loaded.remove(mid)
            return True, ""
        self.patch(llamacpp, "unload", unload)

    def test_its_own_router_is_emptied_and_keeps_running(self):
        stopped = []
        self.patch(llamacpp, "stop", lambda *a: stopped.append(1))
        self.assertEqual(self.p.make_way(), (True, ""))
        self.assertEqual(self.unloaded, ["a", "b"])
        self.assertEqual(self.p.stop(), (True, ""))                            # "free everything" frees the models, not the router
        self.assertEqual(stopped, [])

    def test_a_model_that_wont_unload_is_said_so(self):
        self.patch(llamacpp, "unload", lambda mid: (False, "llama.cpp couldn't free %s." % mid))
        ok, msg = self.p.make_way()
        self.assertFalse(ok)
        self.assertIn("a", msg)


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


class RouterReplies(unittest.TestCase):
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
        status, payload = llamacpp.request("GET", "/health", port=s.getsockname()[1], key="", timeout=5)
        s.close()
        self.assertEqual(status, 0)


if __name__ == "__main__":
    unittest.main()
