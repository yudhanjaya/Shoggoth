"""Names that arrive from a web page must never become paths outside the folder they belong in. Run: python3 -m unittest discover tests"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "zest"))

import agent  # noqa: E402
import skills  # noqa: E402


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("ZEST_DATA_DIR", "ZEST_CONFIG_DIR")}
        os.environ["ZEST_DATA_DIR"], os.environ["ZEST_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)


class Deleting(Env):
    def test_a_chat_id_that_is_not_one_of_ours_is_never_a_path(self):
        keep = os.path.join(self.tmp, "data", "cowork", "keep")
        os.makedirs(os.path.join(keep, "inner"))
        os.makedirs(os.path.join(self.tmp, "data", "cowork", "sessions"))
        for bad in ("../keep", "..", "../../cowork", "", "abc/../../keep", "a" * 12 + "/.."):
            agent.delete(bad)
        self.assertTrue(os.path.isdir(os.path.join(keep, "inner")))
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "data", "cowork", "sessions")))

    def test_a_skill_set_name_cannot_climb_out_of_the_sets_folder(self):
        root = skills.root()
        os.makedirs(os.path.join(root, "sets", "real-set"))
        os.makedirs(os.path.join(root, "mine", "my-skill"))
        for bad in ("..", ".", "../mine", ".hidden"):
            with self.assertRaises(ValueError):
                skills.remove(bad)
        self.assertTrue(os.path.isdir(os.path.join(root, "mine", "my-skill")))
        skills.remove("real-set")                                          # a real one still goes
        self.assertFalse(os.path.isdir(os.path.join(root, "sets", "real-set")))


if __name__ == "__main__":
    unittest.main()
