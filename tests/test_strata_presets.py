"""Which saved Strata setup starts which installed model, so the picker can offer it. Run: python3 -m unittest discover tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import strata  # noqa: E402

PRESETS = [
    {"name": "Mine old", "model": "IQ3_S", "last_used": "2026-10-01 10:00"},
    {"name": "Mine recent", "model": "IQ3_S", "last_used": "2026-10-20 10:00"},
    {"name": "Shoggoth IQ3_S", "model": "IQ3_S", "last_used": None},
    {"name": "Deep", "model": "IST-DAS-2.00bpw", "last_used": "2026-10-05 10:00"},
]


class PresetFor(unittest.TestCase):
    def test_a_zest_made_setup_comes_first_then_the_most_recently_used(self):
        self.assertEqual(strata._preset_for({"id": "iq3_s"}, PRESETS)["name"], "Shoggoth IQ3_S")
        self.assertEqual(strata._preset_for({"id": "iq3_s"}, PRESETS[:2])["name"], "Mine recent")

    def test_the_model_name_may_be_a_suffix_of_the_installed_id(self):
        self.assertEqual(strata._preset_for({"id": "deepseek-v4.1-flash-ist-das-2.00bpw"}, PRESETS)["name"], "Deep")

    def test_no_setup_no_match(self):
        self.assertIsNone(strata._preset_for({"id": "something-else"}, PRESETS))


class DiskWhereModelsLive(unittest.TestCase):
    """Strata keeps models on the disk its owner chose, and a download has to fit there, not on the home disk."""
    def setUp(self):
        import collections
        import shutil
        import tempfile
        self.shutil_real = strata.shutil.disk_usage
        self.im_real = strata.installed_models
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "Strata")
        os.makedirs(os.path.join(self.root, "models", "IQ3_S"))
        self.free = {}
        U = collections.namedtuple("U", "total used free")
        strata.shutil.disk_usage = lambda p: U(500e9, 0, self.free.get(os.path.realpath(p).split("/Strata")[0] + ("/Strata" if "/Strata" in p else ""), 500e9))
        self.shutil_cleanup = shutil.rmtree

    def tearDown(self):
        strata.shutil.disk_usage = self.shutil_real
        strata.installed_models = self.im_real
        self.shutil_cleanup(self.tmp, ignore_errors=True)

    def test_the_root_comes_from_an_installed_models_files(self):
        f = os.path.join(self.root, "models", "IQ3_S", "x-00001-of-00002.gguf")
        strata.installed_models = lambda: [{"id": "iq3_s", "files": [f], "data_root": ""}]
        self.assertEqual(strata.models_root(), self.root)
        strata.installed_models = lambda: []
        self.assertIsNone(strata.models_root())

    def test_a_download_that_does_not_fit_is_explained_with_the_disk(self):
        strata.installed_models = lambda: [{"id": "iq3_s", "files": [], "data_root": self.root}]
        self.free[self.root] = 28e9
        msg = strata.disk_problem(75.8)
        self.assertIn("75 GB", msg)
        self.assertIn("28 GB is free", msg)
        self.assertIn(self.root, msg)
        self.assertIsNone(strata.disk_problem(0))                                   # nothing to download
        self.assertIsNone(strata.disk_problem(10))                                  # fits, with room to spare


class UpdateFailure(unittest.TestCase):
    """Taken from a real `strata update` on this computer: the engine failed its test start, the Strata app updated."""
    REAL = {"ok": False, "results": [
        {"component": "engine", "status": "failed", "changed": False,
         "error": "The new engine 0.1.52 did not start correctly on this computer, so it was not installed. Your current engine is unchanged. Run `strata update --force` to try again."},
        {"component": "control", "status": "updated", "changed": True, "installed_before": "0.1.51", "installed_after": "0.1.53"}]}

    def test_says_what_failed_what_updated_and_that_nothing_was_broken(self):
        msg = strata.update_failure(self.REAL)
        self.assertIn("couldn't update the engine", msg)
        self.assertIn("Your current engine is unchanged", msg)
        self.assertIn("the Strata app to 0.1.53", msg)
        self.assertNotIn("{", msg)                                                 # never the raw JSON

    def test_no_per_part_answer_means_the_caller_uses_its_own_words(self):
        self.assertIsNone(strata.update_failure({"ok": False}))
        self.assertIsNone(strata.update_failure({"ok": False, "results": [{"component": "engine", "status": "current"}]}))


class PresetNames(unittest.TestCase):
    def test_a_family_that_shares_a_model_name_gets_its_own_saved_setup(self):
        q = strata.preset_name({"family": "qwen", "model": "IQ2_XS"})
        w = strata.preset_name({"family": "swift", "model": "IQ2_XS"})
        self.assertEqual(q, "Shoggoth IQ2_XS")
        self.assertNotEqual(q, w)
        self.assertEqual(strata.preset_name({"family": "deepseek41", "model": "IST-DAS-2.00bpw"}), "Shoggoth IST-DAS-2.00bpw")
        self.assertEqual(strata.preset_name({"family": "coder", "model": "IQ1_M"}), "Shoggoth coder IQ1_M")


if __name__ == "__main__":
    unittest.main()
