"""Each model grows a creature: these check what the server works out for it (family, quant, size) and that the form and tone
a family is given on this computer never change. The generator itself is tested in tests/ui/creature.test.mjs.
Run: python3 -m unittest discover tests"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import library  # noqa: E402
import store  # noqa: E402


class Card(unittest.TestCase):
    def test_family_quant_and_size_come_from_the_name(self):
        cases = [
            ({"folder": "/hf/models--unsloth--Qwen3.6-35B-A3B-MTP-GGUF/snapshots/a", "quant": "Q4_K_M"}, "q", ("qwen3.6-35b-a3b-mtp", "Q4_K_M", 35)),
            ({"stem": "LFM2.5-8B-A1B-Q8_0"}, "LFM2.5-8B-A1B", ("lfm2.5-8b-a1b", "Q8_0", 8)),
            ({"stem": "Qwen3-0.6B-UD-IQ2_M"}, "x", ("qwen3-0.6b", "IQ2_M", 0.6)),
            ({"size_label": "270M", "stem": "gemma-3-270m-it-Q4_0"}, "x", ("gemma-3-270m-it", "Q4_0", 0.27)),
            ({}, "Mixtral-8x7B-Instruct", ("mixtral-8x7b-instruct", "Q4_K_M", 56)),
        ]
        for e, fb, (fam, q, b) in cases:
            quant = library.quant_of(e, fb)
            self.assertEqual((library.lineage(e, fb), quant, library.params_of(e, fb, 20, quant)), (fam, q, b), fb)

    def test_q4_and_q8_of_one_model_are_one_family(self):
        a = library.lineage({"stem": "Gemma-4-31B-it-Q4_K_M"}, "")
        b = library.lineage({"stem": "Gemma-4-31B-it-Q8_0"}, "")
        self.assertEqual(a, b)

    def test_an_expert_count_label_is_not_taken_as_the_size(self):
        self.assertEqual(library.params_of({"size_label": "512x56B"}, "Qwen3.8-Flash-Next", 54.8, "IQ3_S"), 120)

    def test_the_hash_matches_the_page(self):
        self.assertEqual(library.fnv(""), 2166136261)
        self.assertEqual(library.fnv("qwen"), 919884438)                                   # the same number as the page's hashStr("qwen")


class ModelsOnCards(unittest.TestCase):
    def setUp(self):
        self._cmd, self._map = library._cmdline, library._file_map
        library._file_map = lambda: {"/m/qwen/Q-IQ3_S-00001-of-00002.gguf": {"title": "Qwen3.8 Flash-Next", "ids": {"strata": "q"}},
                                     "Gemma-Q4.gguf": {"title": "Gemma 4", "ids": {"lemonade": "gemma"}}}

    def tearDown(self):
        library._cmdline, library._file_map = self._cmd, self._map

    def test_a_process_is_named_by_the_model_file_it_runs(self):
        library._cmdline = lambda pid: {1: ["strata", "--serve", "--native", "/m/qwen/Q-IQ3_S-00001-of-00002.gguf"],
                                        2: ["llama-server", "-m", "/elsewhere/Gemma-Q4.gguf"], 3: ["python3"]}.get(pid, [])
        gpus = [{"procs": [{"pid": 1, "name": "strata", "mem_mb": 9000}, {"pid": 2, "name": "llama-server", "mem_mb": 4000},
                           {"pid": 3, "name": "python3", "mem_mb": 300}, {"pid": None, "name": "[no data]", "mem_mb": 200}]}]
        got = library.models_on_cards(gpus)[0]["models"]
        self.assertEqual([(m["model"], m["engine"], m["mem_mb"]) for m in got],
                         [("Qwen3.8 Flash-Next", "strata", 9000), ("Gemma 4", "lemonade", 4000), (None, "python3", 300)])


class Creatures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = os.environ.get("SHOGGOTH_CONFIG_DIR")
        os.environ["SHOGGOTH_CONFIG_DIR"] = self.tmp

    def tearDown(self):
        os.environ.pop("SHOGGOTH_CONFIG_DIR", None) if self._old is None else os.environ.__setitem__("SHOGGOTH_CONFIG_DIR", self._old)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_five_families_get_five_forms(self):
        fams = ["lfm2.5-8b-a1b", "qwen3.6-35b-a3b", "gemma-4-31b-it", "glm-4.7-flash", "qwen3.8-27b"]
        got = library.creatures(fams)
        self.assertEqual(len({f for f, t in got.values()}), 5)

    def test_a_family_keeps_its_creature(self):
        first = library.creatures(["a-model", "b-model", "c-model"])
        store.update(lambda s: s["creatures"].pop("b-model"))                    # b was deleted, and its record with it
        again = library.creatures(["a-model", "c-model", "d-model"])
        self.assertEqual(again["a-model"], first["a-model"])
        self.assertEqual(again["c-model"], first["c-model"])
        self.assertEqual(library.creatures(["a-model"])["a-model"], first["a-model"])


if __name__ == "__main__":
    unittest.main()
