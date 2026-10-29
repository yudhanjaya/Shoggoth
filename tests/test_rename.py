"""Shoggoth used to be called Zest. These check the pieces that keep an old install working after the rename (the settings
folder and the ZEST_* variables are in test_store.py; Strata presets and Lemonade's list are in test_ownership.py).
Run: python3 -m unittest discover tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import engine  # noqa: E402
import zestup  # noqa: E402


class Updater(unittest.TestCase):
    def release(self, name):
        return {"tag_name": "v4.0.1", "html_url": "page", "assets": [
            {"name": name, "browser_download_url": "url", "size": 5, "digest": "sha256:" + "a" * 64}]}

    def setUp(self):
        self._get = engine._get_json

    def tearDown(self):
        engine._get_json = self._get

    def test_a_release_named_shoggoth_is_found(self):
        engine._get_json = lambda url: self.release("Shoggoth-4.0.1-x86_64.AppImage")
        self.assertEqual(zestup.latest_release("4.0.0")["name"], "Shoggoth-4.0.1-x86_64.AppImage")

    def test_a_release_still_named_zest_is_found(self):
        engine._get_json = lambda url: self.release("Zest-4.0.1-x86_64.AppImage")
        self.assertEqual(zestup.latest_release("4.0.0")["name"], "Zest-4.0.1-x86_64.AppImage")


if __name__ == "__main__":
    unittest.main()
