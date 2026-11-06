"""One model-level answer to "is this tuned?". Run: python3 -m unittest discover tests"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import lemtune  # noqa: E402
import modelmeta  # noqa: E402
import tuning  # noqa: E402

KEYS = {"engine", "model", "state", "source", "gain_pct", "decode_tok_s", "baseline_tok_s", "when", "can_undo", "preset", "details", "speed", "headroom_gb"}
class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR")}
        os.environ["SHOGGOTH_DATA_DIR"], os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)


class LemonadeSide(Env):
    ENV = {"backend": "cuda", "llama": "b11395", "lemonade": "2026.44.1"}

    def save(self, **kw):
        res = {"engine": "lemonade", "model": "m", "outcome": "improved", "gain_pct": 8.0, "decode_tok_s": 54.0, "baseline_tok_s": 50.0, "when": "2026-10-30T10:00:00", "applied": {"llamacpp_args": "-fa on"}, "env": dict(self.ENV)}
        res.update(kw)
        lemtune._save_results({"m": res})

    def test_nothing_saved_is_not_tuned(self):
        self.assertEqual(tuning.tuning_for("m")["state"], "not_tuned")

    def test_a_saved_gain_is_tuned(self):
        self.save()
        with mock.patch.object(tuning, "lemonade_env", lambda: dict(self.ENV)):
            t = tuning.tuning_for("m")
        self.assertEqual((t["state"], t["gain_pct"], t["can_undo"]), ("tuned", 8.0, True))

    def test_defaults_already_best(self):
        self.save(outcome="default_confirmed", gain_pct=0.0, applied=None)
        with mock.patch.object(tuning, "lemonade_env", lambda: dict(self.ENV)):
            self.assertEqual(tuning.tuning_for("m")["state"], "default_best")

    def test_a_new_llama_cpp_build_makes_it_stale(self):
        self.save()
        with mock.patch.object(tuning, "lemonade_env", lambda: dict(self.ENV, llama="b12000")):
            t = tuning.tuning_for("m")
        self.assertEqual(t["state"], "stale")
        self.assertIn("llama changed", t["details"]["reasons"][0])

    def test_an_old_result_with_no_versions_is_not_called_stale(self):
        self.save(env={})
        with mock.patch.object(tuning, "lemonade_env", lambda: dict(self.ENV)):
            self.assertEqual(tuning.tuning_for("m")["state"], "tuned")


class SameShape(Env):
    def test_a_tuned_and_an_untuned_model_answer_with_the_same_keys(self):
        with mock.patch.object(tuning, "lemonade_env", lambda: {}):
            lemtune._save_results({"m": {"outcome": "improved", "gain_pct": 3.0}})
            keys = [set(tuning.tuning_for(x)) for x in ("m", "unknown")]
        for k in keys:
            self.assertEqual(k, KEYS)

    def test_measured_speed_and_headroom_ride_along(self):
        modelmeta.update("m", speed={"decode": 40.0}, headroom_gb=2)
        t = tuning.tuning_for("m")
        self.assertEqual((t["speed"], t["headroom_gb"]), ({"decode": 40.0}, 2))


class Headroom(Env):
    def test_it_becomes_a_fit_target_in_mib_and_keeps_other_arguments(self):
        modelmeta.update("m", headroom_gb=2.5)
        with mock.patch.object(lemtune, "options_of", lambda m: {"effective": {"llamacpp_args": "--threads 8"}}):
            args = lemtune.headroom_args("m")
        self.assertIn("--threads 8", args)
        self.assertIn("--fit-target 2560", args)

    def test_no_headroom_leaves_the_load_alone(self):
        self.assertIsNone(lemtune.headroom_args("m"))


if __name__ == "__main__":
    unittest.main()
