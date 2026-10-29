"""What Shoggoth reads from Strata, checked on answers captured from a real install (tests/fixtures/real), so a fake can't drift from the real thing
unnoticed. When Strata changes its output, capture it again (see the top of each file) and these say what no longer matches.
Run: python3 -m unittest discover tests"""
import copy
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "shoggoth"))

import strata  # noqa: E402

REAL = os.path.join(HERE, "fixtures", "real")
FILES = {"what is running": "strata_status.json", "saved setups": "strata_presets.json", "installed models": "strata_models_installed.json",
         "the update check": "strata_update_check.json", "a download plan": "strata_plan.json"}


def real(name):
    with open(os.path.join(REAL, name)) as f:
        return json.load(f)


class Contracts(unittest.TestCase):
    def test_every_answer_Zest_reads_has_the_fields_Zest_reads(self):
        for name, args, check in strata.CONTRACTS:
            with self.subTest(name):
                self.assertEqual(check(real(FILES[name])), [])

    def test_a_missing_field_is_named(self):
        d = real("strata_update_check.json")
        del d["engine"]["status"]
        check = next(c for n, a, c in strata.CONTRACTS if n == "the update check")
        self.assertEqual(check(d), ["engine.status"])
        d = real("strata_plan.json")
        d["plan"]["download"]["gb"] = "lots"
        self.assertEqual(next(c for n, a, c in strata.CONTRACTS if n == "a download plan")(d), ["plan.download.gb (not a number)"])
        d = real("strata_presets.json")
        del d["presets"][0]["context"]
        self.assertEqual(next(c for n, a, c in strata.CONTRACTS if n == "saved setups")(d), ["presets[].context"])

    def test_a_running_server_without_its_address_is_caught(self):
        check = next(c for n, a, c in strata.CONTRACTS if n == "what is running")
        self.assertEqual(check({"running": [{"name": "x", "profile": "p", "port": 1, "state": "ready"}]}), ["running[].url"])
        self.assertEqual(check({"running": "none"}), ["running (not a list)"])


class ParsingTheRealAnswers(unittest.TestCase):
    """The functions that turn Strata's answers into what the page shows, fed the real thing instead of a hand-made fake."""
    def setUp(self):
        self._json, self._binary = strata._json, strata.binary
        strata.binary = lambda: "/usr/bin/strata"

    def tearDown(self):
        strata._json, strata.binary = self._json, self._binary

    def feed(self, by_command):
        def fake(args, timeout=20):
            for key, name in by_command.items():
                if key in " ".join(args):
                    return copy.deepcopy(real(name))
            return {}
        strata._json = fake

    def test_presets(self):
        self.feed({"--list-presets": "strata_presets.json"})
        got = strata.presets()
        self.assertEqual([p["name"] for p in got], ["IQ3_S dual", "IST-DAS-2.00bpw 128K"])
        self.assertEqual((got[0]["family"], got[0]["model"], got[0]["context"]), ("qwen", "IQ3_S", 131072))

    def test_the_plan_gives_the_download_size(self):
        self.feed({"--plan": "strata_plan.json"})
        self.assertEqual(strata._plan({"family": "qwen", "model": "IQ3_S"}, 131072), {"download_gb": 0, "warnings": []})

    def test_installed_models_and_where_they_live(self):
        self.feed({"models list": "strata_models_installed.json"})
        strata._im["v"] = None
        m = strata.installed_models()
        self.assertEqual(sorted(x["id"] for x in m), ["deepseek-v4.1-flash-ist-das-2.00bpw", "iq3_s"])
        self.assertTrue(all(x["config"] and x["status"] == "present" for x in m))


if __name__ == "__main__":
    unittest.main()
