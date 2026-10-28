"""Which saved Strata setup starts which installed model, so the picker can offer it. Run: python3 -m unittest discover tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "zest"))

import strata  # noqa: E402

PRESETS = [
    {"name": "Mine old", "model": "IQ3_S", "last_used": "2026-10-01 10:00"},
    {"name": "Mine recent", "model": "IQ3_S", "last_used": "2026-10-20 10:00"},
    {"name": "Zest IQ3_S", "model": "IQ3_S", "last_used": None},
    {"name": "Deep", "model": "IST-DAS-2.00bpw", "last_used": "2026-10-05 10:00"},
]


class PresetFor(unittest.TestCase):
    def test_a_zest_made_setup_comes_first_then_the_most_recently_used(self):
        self.assertEqual(strata._preset_for({"id": "iq3_s"}, PRESETS)["name"], "Zest IQ3_S")
        self.assertEqual(strata._preset_for({"id": "iq3_s"}, PRESETS[:2])["name"], "Mine recent")

    def test_the_model_name_may_be_a_suffix_of_the_installed_id(self):
        self.assertEqual(strata._preset_for({"id": "deepseek-v4.1-flash-ist-das-2.00bpw"}, PRESETS)["name"], "Deep")

    def test_no_setup_no_match(self):
        self.assertIsNone(strata._preset_for({"id": "something-else"}, PRESETS))


if __name__ == "__main__":
    unittest.main()
