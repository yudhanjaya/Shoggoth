"""Leaving graphics memory free for the system when a model loads. Run: python3 -m unittest discover tests"""
import glob
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import gguf  # noqa: E402
import modelmeta  # noqa: E402
import provbase  # noqa: E402


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


KVLOGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "kvlogs")
MIB = 1024 * 1024


def read_kvlog(path):
    """What a llama-server load log says: the model's shape, and the memory the load took in total (model, conversation and working
    buffers, on the card and in host memory), in GB."""
    with open(path) as f:
        text = f.read()
    num = lambda pat: float(re.search(pat, text).group(1))      # noqa: E731
    mib = sum(float(m) for m in re.findall(r"model buffer size\s*=\s*([\d.]+) MiB", text))
    mib += num(r"llama_kv_cache: size =\s*([\d.]+) MiB")
    mib += sum(float(m) for m in re.findall(r"compute buffer size\s*=\s*([\d.]+) MiB", text))
    return {"layers": int(num(r"n_layer\s*=\s*(\d+)")), "kv_heads": int(num(r"n_head_kv\s*=\s*(\d+)")), "dk": int(num(r"n_embd_head_k\s*=\s*(\d+)")),
            "dv": int(num(r"n_embd_head_v\s*=\s*(\d+)")), "ctx": int(num(r"\(\s*(\d+) cells")), "cache": re.search(r"K \((\w+)\)", text).group(1),
            "file_bytes": int(re.search(r"\((\d+) bytes\)", text.split("\n")[0]).group(1)) if "bytes)" in text.split("\n")[0] else 0,
            "total_gb": mib * MIB / 1e9}


class Estimate(unittest.TestCase):
    """The estimate (model x 1.1 plus the conversation's cache) against what llama-server really allocated, on the RTX 3080.
    Only the small test model could be loaded on the development machine; add logs for bigger models to fixtures/kvlogs/ and they are checked too."""

    def test_there_are_logs(self):
        self.assertTrue(glob.glob(os.path.join(KVLOGS, "*.log")))

    def test_every_logged_load_is_within_ten_percent(self):
        for path in sorted(glob.glob(os.path.join(KVLOGS, "*.log"))):
            with self.subTest(os.path.basename(path)):
                r = read_kvlog(path)
                entry = {"size_bytes": r["file_bytes"], "kv_gb": gguf.kv_cache_gb({k: r[k] for k in ("layers", "kv_heads", "dk", "dv")}, r["ctx"], cache_type=r["cache"])}
                est = provbase.Base.need_gb(None, entry)
                self.assertLess(abs(est - r["total_gb"]) / r["total_gb"], 0.10, "estimate %.2f GB, loaded %.2f GB" % (est, r["total_gb"]))

    def test_the_cache_line_matches_the_cache_formula(self):
        for path in sorted(glob.glob(os.path.join(KVLOGS, "*.log"))):
            with self.subTest(os.path.basename(path)):
                r = read_kvlog(path)
                with open(path) as f:
                    logged = float(re.search(r"llama_kv_cache: size =\s*([\d.]+) MiB", f.read()).group(1)) * MIB / 1e9
                est = gguf.kv_cache_gb({k: r[k] for k in ("layers", "kv_heads", "dk", "dv")}, r["ctx"], cache_type=r["cache"])
                self.assertLess(abs(est - logged) / logged, 0.02)


if __name__ == "__main__":
    unittest.main()
