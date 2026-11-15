"""llamatune: measuring with llama-bench (a fake one that prints fixture JSON), and keeping the winning flags in presets.ini."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "shoggoth"))

import llamacpp  # noqa: E402
import llamatune  # noqa: E402
import tuning  # noqa: E402

FIXTURE = [
    {"n_prompt": 512, "n_gen": 0, "avg_ts": 42206.3, "stddev_ts": 7689.5, "samples_ts": [36768.9, 47643.6]},
    {"n_prompt": 0, "n_gen": 64, "avg_ts": 663.4, "stddev_ts": 16.0, "samples_ts": [652.1, 674.8]},
]


class Job:
    def __init__(self):
        self.log = []
        self.stop = False

    def update(self, **kw):
        self.log.append(kw)

    def cancelled(self):
        return self.stop


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR", "FAKE_BENCH_LOG", "FAKE_BENCH_FAIL")}
        os.environ["SHOGGOTH_DATA_DIR"], os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")
        os.environ["FAKE_BENCH_LOG"] = os.path.join(self.tmp, "calls.log")
        os.environ.pop("FAKE_BENCH_FAIL", None)
        d = os.path.join(llamacpp.root(), "b100", "cpu")
        os.makedirs(d)
        open(os.path.join(d, "llama-server"), "w").close()
        shutil.copy(os.path.join(HERE, "fixtures", "fake-llama-bench"), os.path.join(d, "llama-bench"))
        os.chmod(os.path.join(d, "llama-bench"), 0o755)
        self.gguf = os.path.join(self.tmp, "m.gguf")
        open(self.gguf, "w").close()
        llamacpp.write_presets({"m": {"model": self.gguf, "ctx-size": 8192}}, llamacpp.DEFAULTS)

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def calls(self):
        try:
            return open(os.environ["FAKE_BENCH_LOG"]).read().splitlines()
        except OSError:
            return []


class Parse(unittest.TestCase):
    def test_prefill_and_decode_come_from_the_rows_with_only_a_prompt_or_only_generation(self):
        m = llamatune.parse_bench(json.dumps(FIXTURE))
        self.assertAlmostEqual(m["prefill"], 42206.25, delta=1)       # median of two samples
        self.assertAlmostEqual(m["decode"], 663.45, delta=0.1)
        self.assertGreater(m["noise"], 0.2)                           # the prefill samples are far apart

    def test_text_around_the_json_is_ignored(self):
        m = llamatune.parse_bench("ggml_cuda_init: found 1 CUDA devices\n" + json.dumps(FIXTURE) + "\n")
        self.assertGreater(m["decode"], 600)

    def test_a_missing_half_is_an_error(self):
        with self.assertRaises(RuntimeError):
            llamatune.parse_bench(json.dumps(FIXTURE[:1]))
        with self.assertRaises(RuntimeError):
            llamatune.parse_bench("not json")


class Flags(unittest.TestCase):
    def test_preset_keys_become_llama_bench_flags_and_unknown_ones_are_dropped(self):
        a = llamatune.bench_args({"n-gpu-layers": 20, "n-cpu-moe": 4, "flash-attn": "true", "ctx-size": 8192, "jinja": "true", "ubatch-size": 256})
        self.assertEqual(a, ["-ngl", "20", "-ncmoe", "4", "-fa", "on", "-ub", "256"])

    def test_candidates_skip_what_is_already_set(self):
        labels = [c["label"] for c in llamatune.candidates({"flash-attn": "on", "ubatch-size": "256"}, [], 8)]
        self.assertNotIn("flash attention on", labels)
        self.assertIn("flash attention off", labels)
        self.assertNotIn("micro-batch 256", labels)
        self.assertFalse(any("expert" in x for x in labels))
        self.assertTrue(any("expert" in c["label"] for c in llamatune.candidates({}, [], 8, moe=True)))


class Bench(Env):
    def test_runs_the_bench_in_the_builds_folder_against_the_model_file(self):
        m = llamatune.bench(self.gguf, {"flash-attn": "on", "ctx-size": 8192}, 3)
        self.assertAlmostEqual(m["decode"], 100.0, delta=1)
        self.assertAlmostEqual(m["prefill"], 2000.0, delta=20)
        line = self.calls()[0]
        self.assertIn("-m " + self.gguf, line)
        self.assertIn("-o json", line)
        self.assertIn("-fa on", line)
        self.assertNotIn("8192", line)

    def test_a_failing_bench_is_a_plain_error(self):
        os.environ["FAKE_BENCH_FAIL"] = "-ub"
        with self.assertRaises(RuntimeError) as cm:
            llamatune.bench(self.gguf, {"ubatch-size": 256}, 2)
        self.assertIn("cannot run that", str(cm.exception))

    def test_cancelling_stops_before_the_next_run(self):
        with self.assertRaises(llamatune.Cancelled):
            llamatune.bench(self.gguf, {}, 2, cancelled=lambda: True)

    def test_no_build_means_a_clear_message(self):
        shutil.rmtree(llamacpp.root())
        with self.assertRaises(RuntimeError) as cm:
            llamatune.bench(self.gguf, {}, 2)
        self.assertIn("llama-bench", str(cm.exception))

    def test_the_model_file_is_found_from_the_preset(self):
        self.assertEqual(llamatune.model_path("m"), self.gguf)
        self.assertEqual(llamatune.model_path("nope"), "")


class Run(Env):
    def test_a_win_is_written_to_presets_and_can_be_undone(self):
        before = llamacpp.read_presets()
        job = Job()
        res = llamatune.run("m", {"use": "chat"}, job, [], [], 8, env={"llama": "b100"})
        self.assertEqual(res["outcome"], "improved")
        self.assertEqual(res["engine"], "llamacpp")
        self.assertEqual(res["change"], "micro-batch 1024")
        self.assertEqual(res["flags"], {"ubatch-size": 1024})
        self.assertGreater(res["gain_pct"], 3)
        pres = llamacpp.read_presets()
        self.assertEqual(pres["m"]["ubatch-size"], "1024")
        self.assertEqual(pres["m"]["model"], self.gguf)               # the rest of the section is kept
        self.assertEqual(pres["*"], before["*"])                      # and so are the defaults
        self.assertEqual(llamatune.results()["m"]["env"], {"llama": "b100"})
        self.assertEqual(tuning.tuning_for("m")["can_undo"], True)

        ok, msg = llamatune.rollback("m")
        self.assertTrue(ok, msg)
        self.assertEqual(llamacpp.read_presets(), before)
        self.assertFalse(llamatune.results()["m"]["applied"])
        self.assertFalse(llamatune.rollback("m")[0])

    def test_nothing_clearly_faster_leaves_presets_alone(self):
        before = llamacpp.read_presets()
        with mock.patch.object(llamatune, "score", lambda m, b, u: 0.01):
            res = llamatune.run("m", {}, Job(), [], [], 8)
        self.assertEqual((res["outcome"], res["gain_pct"], res["applied"]), ("default_confirmed", 0.0, None))
        self.assertEqual(llamacpp.read_presets(), before)

    def test_a_trial_that_will_not_run_is_skipped_not_fatal(self):
        os.environ["FAKE_BENCH_FAIL"] = "256"
        res = llamatune.run("m", {}, Job(), [], [], 8)
        self.assertTrue(any(t.get("error") for t in res["tried"]))
        self.assertEqual(res["outcome"], "improved")

    def test_cancelled_changes_nothing(self):
        job = Job()
        job.stop = True
        before = llamacpp.read_presets()
        with self.assertRaises(llamatune.Cancelled):
            llamatune.run("m", {}, job, [], [], 8)
        self.assertEqual(llamacpp.read_presets(), before)
        self.assertEqual(llamatune.results(), {})

    def test_test_speed_keeps_the_numbers_in_the_models_record(self):
        import modelmeta
        res = llamatune.test_speed("m", Job())
        self.assertAlmostEqual(res["decode"], 100.0, delta=1)
        self.assertEqual(modelmeta.get("m")["speed"], res)
        self.assertEqual(llamatune.results(), {})


class LemonadeRecords(Env):
    """tuning.json entries the 4.x Lemonade tuner wrote: `applied` holds Lemonade's option names, not llama-server preset keys."""

    def legacy(self, **kw):
        res = {"engine": "lemonade", "model": "m", "when": "2026-09-01T10:00:00", "use": "chat", "outcome": "improved", "gain_pct": 7.0,
               "decode_tok_s": 50.0, "baseline_tok_s": 47.0, "change": "8 threads",
               "applied": {"llamacpp_args": "-t 8 -fa on", "llamacpp_backend": "rocm", "llamacpp_device": "gpu"},
               "saved_before": {"llamacpp_args": "-t 4", "llamacpp_backend": "rocm", "llamacpp_device": "gpu"}}
        res.update(kw)
        llamatune._save_results({"m": res})
        return res

    def test_it_is_recognised(self):
        self.assertTrue(llamatune.written_by_lemonade(self.legacy()))
        self.assertTrue(llamatune.written_by_lemonade({"outcome": "improved", "applied": "-t 8 -fa on"}))
        self.assertTrue(llamatune.written_by_lemonade({"outcome": "improved", "applied": {"llamacpp_args": "-t 8"}}))

    def test_a_5x_record_is_not(self):
        self.assertFalse(llamatune.written_by_lemonade({"engine": "llamacpp", "applied": {"ubatch-size": 1024}, "saved_before": {"ubatch-size": None}}))
        self.assertFalse(llamatune.written_by_lemonade({"engine": "llamacpp", "applied": None}))
        self.assertFalse(llamatune.written_by_lemonade(None))

    def test_undo_refuses_and_leaves_presets_alone(self):
        self.legacy()
        before = llamacpp.read_presets()
        ok, msg = llamatune.rollback("m")
        self.assertFalse(ok)
        self.assertIn("Lemonade", msg)
        self.assertEqual(llamacpp.read_presets(), before)
        self.assertEqual(llamatune.results()["m"]["applied"], {"llamacpp_args": "-t 8 -fa on", "llamacpp_backend": "rocm", "llamacpp_device": "gpu"})

    def test_an_unexpected_string_is_refused_not_iterated(self):
        self.legacy(applied="-t 8 -fa on")
        before = llamacpp.read_presets()
        ok, msg = llamatune.rollback("m")
        self.assertFalse(ok)
        self.assertIn("Lemonade", msg)
        self.assertEqual(llamacpp.read_presets(), before)


if __name__ == "__main__":
    unittest.main()
