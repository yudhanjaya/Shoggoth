"""Leaving graphics memory free for the system when a model loads. Run: python3 -m unittest discover tests"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import modelmeta  # noqa: E402


class Reserve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR")}
        os.environ["SHOGGOTH_DATA_DIR"], os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_models_are_not_changed_unless_asked(self):
        self.assertEqual(modelmeta.headroom_gb("Qwen3-0.6B-GGUF"), 0.0)
        self.assertEqual(modelmeta.headroom_info("Qwen3-0.6B-GGUF"), {"gb": 0.0, "default": 0.0, "explicit": False})

    def test_a_chosen_amount_is_used(self):
        modelmeta.update("Qwen3-0.6B-GGUF", headroom_gb=1.5)
        self.assertEqual(modelmeta.headroom_gb("Qwen3-0.6B-GGUF"), 1.5)
        self.assertTrue(modelmeta.headroom_info("Qwen3-0.6B-GGUF")["explicit"])

    def test_none_means_none(self):
        modelmeta.update("Qwen3-0.6B-GGUF", headroom_gb=0)
        self.assertEqual(modelmeta.headroom_gb("Qwen3-0.6B-GGUF"), 0.0)

    def test_the_page_gets_the_defaults_and_what_was_set(self):
        modelmeta.update("m", headroom_gb=2)
        self.assertEqual(modelmeta.headroom_table(), {"default": {"lemonade": 0.0}, "set": {"m": 2}})


if __name__ == "__main__":
    unittest.main()
