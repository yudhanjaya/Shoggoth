"""Settings: saved by several threads at once (none may undo another's change), and usable where the home folder can't be written.
Run: python3 -m unittest discover tests"""
import os
import shutil
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "zest"))

import store  # noqa: E402


class Merging(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = os.environ.get("ZEST_CONFIG_DIR")
        os.environ["ZEST_CONFIG_DIR"] = self.tmp

    def tearDown(self):
        os.environ.pop("ZEST_CONFIG_DIR", None) if self._old is None else os.environ.__setitem__("ZEST_CONFIG_DIR", self._old)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_two_writers_that_changed_different_settings_both_keep_their_change(self):
        a, b = store.load(), store.load()                     # both read the file before either saved
        a["gateway"]["port"] = 14000
        b["chat"]["ctx"] = 65536
        store.save(a)
        store.save(b)
        now = store.load()
        self.assertEqual(now["gateway"]["port"], 14000)
        self.assertEqual(now["chat"]["ctx"], 65536)
        self.assertEqual(b["gateway"]["port"], 14000)         # and the one who saved last carries on with the merged result

    def test_the_same_setting_goes_to_the_one_who_saved_last(self):
        a, b = store.load(), store.load()
        a["chat"]["ctx"] = 32768
        b["chat"]["ctx"] = 65536
        store.save(a)
        store.save(b)
        self.assertEqual(store.load()["chat"]["ctx"], 65536)

    def test_a_removed_key_stays_removed_and_a_new_key_is_kept(self):
        s = store.load()
        s["strata"]["known"] = {"x": 1}
        store.save(s)
        a, b = store.load(), store.load()
        del a["strata"]["known"]["x"]
        b["providers"]["mine"] = {"approved": "abc"}
        store.save(a)
        store.save(b)
        now = store.load()
        self.assertEqual(now["strata"]["known"], {})
        self.assertEqual(now["providers"]["mine"], {"approved": "abc"})

    def test_many_threads_changing_their_own_setting_lose_nothing(self):
        def work(i):
            for _ in range(5):
                s = store.load()
                s["providers"].setdefault("p%d" % i, {})["n"] = s["providers"].get("p%d" % i, {}).get("n", 0) + 1
                store.save(s)
        ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual([store.load()["providers"]["p%d" % i]["n"] for i in range(8)], [5] * 8)

    def test_update_is_one_step(self):
        store.update(lambda s: s["chat"].update(ctx=1024))
        self.assertEqual(store.load()["chat"]["ctx"], 1024)

    def test_a_plain_dict_still_saves(self):
        store.save({"mode": "builtin"})
        self.assertEqual(store.load()["mode"], "builtin")


class UnwritableHome(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env = {k: os.environ.get(k) for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "TMPDIR")}
        os.environ["TMPDIR"] = self.tmp
        tempfile.tempdir = None
        self._zest = {k: os.environ.pop(k, None) for k in ("ZEST_CONFIG_DIR", "ZEST_DATA_DIR")}      # the test overrides would hide the fallback

    def tearDown(self):
        for k, v in self._env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        tempfile.tempdir = None
        for k, v in self._zest.items():
            if v is not None:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_home_falls_back_to_a_private_temp_folder(self):
        os.environ["XDG_CONFIG_HOME"] = "/proc/nonexistent"
        os.environ["XDG_DATA_HOME"] = "/proc/nonexistent"
        for d in (store.config_dir(), store.data_dir()):
            self.assertTrue(d.startswith(self.tmp), d)
            self.assertEqual(os.stat(d).st_mode & 0o077, 0)        # the settings hold an API key

    def test_saving_settings_works_after_the_fallback(self):
        os.environ["XDG_CONFIG_HOME"] = "/proc/nonexistent"
        s = store.load()
        s["builtin"]["api_key"] = "k"
        store.save(s)
        self.assertEqual(store.load()["builtin"]["api_key"], "k")

    def test_a_writable_home_is_used_as_is(self):
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.tmp, "cfg")
        self.assertEqual(store.config_dir(), os.path.join(self.tmp, "cfg", "zest"))


class Shipping(unittest.TestCase):
    def test_every_module_is_readable_by_other_users(self):
        # The catalog runs the AppImage as another user; a 0600 module crashed it on import.
        root = os.path.join(HERE, "..", "zest")
        bad = [f for f in os.listdir(root) if f.endswith(".py") and not os.stat(os.path.join(root, f)).st_mode & 0o004]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
