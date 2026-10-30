"""One model-level answer to "is this tuned?", the same shape from both engines. Run: python3 -m unittest discover tests"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import lemtune  # noqa: E402
import modelmeta  # noqa: E402
import strata  # noqa: E402
import tuning  # noqa: E402

KEYS = {"engine", "model", "state", "source", "gain_pct", "decode_tok_s", "baseline_tok_s", "when", "can_undo", "preset", "details", "speed", "headroom_gb"}
PRESETS = [
    {"name": "Mine old", "model": "IQ3_S", "last_used": "2026-10-01 10:00", "tuned_preset": None, "tuned_at": "2026-10-02"},      # tuned_at set, nothing tuned
    {"name": "Mine tuned", "model": "IQ3_S", "last_used": "2026-09-01 10:00", "tuned_preset": "abc", "tuned_at": "2026-09-02"},
    {"name": "Deep", "model": "IST-DAS-2.00bpw", "last_used": None, "tuned_preset": None, "tuned_at": "2026-10-25"},
]


def status(**kw):
    base = {"has": True, "state": "fresh", "reasons": [], "gain_pct": 12.5, "decode_tok_s": 90.0, "baseline_tok_s": 80.0, "outcome": "improved", "mode": "quick", "when": "2026-10-24", "id": "abc", "name": "x", "presets": []}
    base.update(kw)
    return base


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR")}
        os.environ["SHOGGOTH_DATA_DIR"], os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")
        self.patches = [mock.patch.object(strata, "_presets_cached", lambda: PRESETS), mock.patch.object(strata, "installed_models", lambda: []),
                        mock.patch.object(strata, "binary", lambda: "/bin/strata")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)


class StrataSide(Env):
    def test_no_saved_setup_is_not_tuned(self):
        t = tuning.tuning_for("strata/something-else")
        self.assertEqual(t["state"], "not_tuned")
        self.assertIsNone(t["preset"])

    def test_tuned_at_alone_does_not_count_as_tuned(self):
        t = tuning.tuning_for("strata/IST-DAS-2.00bpw")
        self.assertEqual(t["state"], "not_tuned")
        self.assertEqual(t["preset"], "Deep")

    def test_with_several_setups_the_tuned_one_is_read(self):
        with mock.patch.object(strata, "tuning_status", lambda name: status()) as m:
            t = tuning.tuning_for("strata/IQ3_S")
        self.assertEqual((t["state"], t["preset"], t["gain_pct"]), ("tuned", "Mine tuned", 12.5))
        self.assertTrue(t["can_undo"])

    def test_defaults_confirmed_is_a_result_not_a_zero(self):
        with mock.patch.object(strata, "tuning_status", lambda name: status(gain_pct=0.0, outcome="default_confirmed", decode_tok_s=79.1, baseline_tok_s=79.1)):
            t = tuning.tuning_for("strata/IQ3_S")
        self.assertEqual(t["state"], "default_best")
        self.assertEqual(t["decode_tok_s"], 79.1)

    def test_a_stale_tune_is_stale(self):
        with mock.patch.object(strata, "tuning_status", lambda name: status(state="stale", reasons=["engine changed"])):
            t = tuning.tuning_for("strata/IQ3_S")
        self.assertEqual(t["state"], "stale")
        self.assertEqual(t["details"]["reasons"], ["engine changed"])


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
    def test_both_engines_answer_with_the_same_keys(self):
        with mock.patch.object(strata, "tuning_status", lambda name: status()), mock.patch.object(tuning, "lemonade_env", lambda: {}):
            lemtune._save_results({"m": {"outcome": "improved", "gain_pct": 3.0}})
            keys = [set(tuning.tuning_for(x)) for x in ("strata/IQ3_S", "strata/nope", "m", "unknown")]
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
