"""Version order for updates: a beta sorts below its release, so a beta install is still offered the release.
Run: python3 -m unittest discover tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import engine  # noqa: E402


class VersionOrder(unittest.TestCase):
    def test_pre_releases_sort_below_their_release(self):
        order = ["4.0.0", "4.1.0-beta.1", "4.1.0-beta.2", "4.1.0-rc.1", "4.1.0", "4.1.1", "10.0.0"]
        self.assertEqual(sorted(reversed(order), key=engine.vkey), order)

    def test_a_beta_install_is_offered_the_release(self):
        self.assertGreater(engine.vkey("4.1.0"), engine.vkey("4.1.0-beta.1"))
        self.assertGreater(engine.vkey("4.1.0-beta.1"), engine.vkey("4.0.0"))      # and not the older stable one

    def test_missing_parts_are_zero_and_suffixes_are_not_pre_releases(self):
        self.assertEqual(engine.vkey("4.1"), engine.vkey("4.1.0"))
        self.assertEqual(engine.vkey("v9.1.4"), engine.vkey("9.1.4"))
        self.assertEqual(engine.vkey("0.1.52-linux-x86_64-cuda-cuda13"), engine.vkey("0.1.52"))
        self.assertEqual(engine.vkey(""), (0,))


if __name__ == "__main__":
    unittest.main()
