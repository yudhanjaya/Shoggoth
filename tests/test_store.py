"""Zest must start where its home folder can't be written (a sandbox, a catalog test). Run: python3 -m unittest discover tests"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "zest"))

import store  # noqa: E402


class UnwritableHome(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env = {k: os.environ.get(k) for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "TMPDIR")}
        os.environ["TMPDIR"] = self.tmp
        tempfile.tempdir = None

    def tearDown(self):
        for k, v in self._env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        tempfile.tempdir = None
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
