"""Leaving graphics memory free for the system when a large model starts. Run: python3 -m unittest discover tests"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import modelmeta  # noqa: E402
import strata  # noqa: E402

QWEN = {"name": "IQ3_S dual", "family": "qwen", "model": "IQ3_S", "extra": {}}
DEEP = {"name": "Deep", "family": "deepseek41", "model": "IST-DAS-2.00bpw", "extra": {}}
INSTALLED = [{"id": "qwen3.8-flash-next-iq3_s"}, {"id": "deepseek-v4.1-flash-ist-das-2.00bpw"}]


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR")}
        os.environ["SHOGGOTH_DATA_DIR"], os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")
        self.p = mock.patch.object(strata, "installed_models", lambda: INSTALLED)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)


class Reserve(Env):
    def test_large_models_keep_one_and_a_half_gb_free_by_default(self):
        self.assertEqual(strata.reserve_setting(QWEN), ("vram-reserve-mib=1536", 1536))

    def test_the_deepseek_engine_names_it_differently(self):
        self.assertEqual(strata.reserve_setting(DEEP), ("reserve-mib=1536", 1536))

    def test_none_means_none(self):
        modelmeta.update("strata/qwen3.8-flash-next-iq3_s", headroom_gb=0)
        self.assertEqual(strata.reserve_setting(QWEN), (None, 0))

    def test_a_chosen_amount_is_used(self):
        modelmeta.update("strata/qwen3.8-flash-next-iq3_s", headroom_gb=3)
        self.assertEqual(strata.reserve_setting(QWEN)[1], 3072)

    def test_a_reserve_the_setup_already_carries_is_left_alone(self):
        self.assertEqual(strata.reserve_setting(dict(QWEN, extra={"--vram-reserve-mib": "900"})), (None, 0))
        self.assertEqual(strata.reserve_setting(dict(DEEP, extra={"reserve_mib": "900"})), (None, 0))

    def test_lemonade_models_are_not_changed_unless_asked(self):
        self.assertEqual(modelmeta.headroom_gb("Qwen3-0.6B-GGUF"), 0.0)
        modelmeta.update("Qwen3-0.6B-GGUF", headroom_gb=1.5)
        self.assertEqual(modelmeta.headroom_gb("Qwen3-0.6B-GGUF"), 1.5)


class StartsWithTheReserveThenWithout(Env):
    def run_start(self, outcomes):
        """outcomes: the exit codes of successive `strata launch` runs, and the log line each leaves."""
        calls = []

        class P:
            def __init__(s, args):
                calls.append(args)
                code, line = outcomes[len(calls) - 1]
                s.stdout, s.code = iter([line + "\n"]), code

            def wait(s):
                return s.code
        strata.RUN.update(state="starting", preset="IQ3_S dual", error=None, log=[], notice=None, low_memory=False)
        with mock.patch.object(strata.subprocess, "Popen", lambda args, **kw: P(args)), mock.patch.object(strata, "binary", lambda: "/bin/strata"), \
                mock.patch.object(strata, "presets", lambda: [QWEN]), mock.patch.object(strata, "running", lambda: []), \
                mock.patch.object(strata, "_before_launch", lambda: None), mock.patch.object(strata, "_changed", lambda: None):
            strata._do_start("IQ3_S dual", None, True, "IQ3_S")
        return calls

    def test_it_starts_with_the_reserve(self):
        calls = self.run_start([(0, "serving")])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-2:], ["--set", "vram-reserve-mib=1536"])
        self.assertIsNone(strata.RUN["notice"])

    def test_when_it_cannot_start_that_way_it_starts_without_and_says_so(self):
        calls = self.run_start([(1, "not enough VRAM: a smaller --vram-reserve-mib leaves more room"), (0, "serving")])
        self.assertEqual(len(calls), 2)
        self.assertNotIn("--set", calls[1])
        self.assertIn("without keeping 1.5 GB free", strata.RUN["notice"])
        self.assertEqual(strata.RUN["state"], "ready")

    def test_an_unrelated_failure_is_not_retried(self):
        calls = self.run_start([(1, "port 8080 is already in use")])
        self.assertEqual(len(calls), 1)
        self.assertEqual(strata.RUN["state"], "error")


if __name__ == "__main__":
    unittest.main()
