"""Maya as a provider, against a fake Maya. Run: python3 -m unittest discover tests"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "shoggoth"))
sys.path.insert(0, os.path.join(HERE, "fixtures"))

import authority  # noqa: E402
import expert  # noqa: E402
import provbase  # noqa: E402
import registry  # noqa: E402
import router  # noqa: E402
import store  # noqa: E402
from fake_maya import FakeMaya  # noqa: E402


class MayaCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR")}
        os.environ["SHOGGOTH_DATA_DIR"], os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")
        self.fake = FakeMaya()
        self.addCleanup(self.fake.close)
        expert.set_on(True)
        s = store.load()
        s.setdefault("providers", {})["maya"] = {"port": self.fake.port}
        store.save(s)
        self.reg = registry.Registry().load()
        self.maya = self.reg.get("maya")

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)


class Offered(MayaCase):
    def test_it_loads_and_validates(self):
        self.assertEqual([e for e in self.reg.errors if "maya" in e["folder"]], [])
        self.assertIsNotNone(self.maya)

    def test_it_is_not_offered_while_expert_mode_is_off(self):
        expert.set_on(False)
        self.assertIsNone(self.reg.get("maya"))
        self.assertNotIn("maya", [p.id for p in self.reg.all()])
        self.assertIn("lemonade", [p.id for p in self.reg.all()])


class Finding(MayaCase):
    def test_a_running_maya_is_found_with_its_model(self):
        det = self.maya.detect()
        self.assertEqual([(r["port"], r["owner"], r["label"]) for r in det["running"]], [(self.fake.port, "yours", "glm-5.3-flash")])
        self.assertEqual([m["id"] for m in self.maya.models()], ["glm-5.3-flash"])
        self.assertEqual(self.maya.loaded("glm-5.3-flash"), (True, 131072))

    def test_a_strata_on_the_same_ports_is_not_taken_for_maya(self):
        self.fake.service = "strata"
        self.assertEqual(self.maya.detect()["running"], [])

    def test_a_maya_with_nothing_loaded_holds_no_memory(self):
        self.fake.model = None
        self.assertEqual(len(self.maya.detect()["running"]), 1)
        self.assertFalse(self.maya.is_running())


class Controlling(MayaCase):
    def test_release_unloads_and_keeps_it_running(self):
        self.assertEqual(self.maya.release(), (True, ""))
        self.assertEqual(self.fake.calls, [("/models/unload", {})])
        self.assertEqual(len(self.maya.detect()["running"]), 1)

    def test_release_is_refused_while_it_is_answering(self):
        self.fake.busy = True
        self.assertTrue(self.maya.busy())
        ok, msg = self.maya.release()
        self.assertFalse(ok)
        self.assertEqual(self.fake.model, "glm-5.3-flash")

    def test_with_model_management_off_it_says_so(self):
        self.fake.management = False
        ok, msg = self.maya.release()
        self.assertFalse(ok)
        self.assertIn("model management is off", msg)

    def test_ensure_asks_a_running_maya_to_swap_models(self):
        ep = self.maya.ensure("minimax-m3", lambda t: None)
        self.assertEqual(ep["model"], "minimax-m3")
        self.assertEqual(self.fake.calls, [("/models/load", {"name": "minimax-m3"})])

    def test_ensure_with_management_off_explains_instead_of_failing_blindly(self):
        self.fake.management = False
        with self.assertRaises(provbase.ProviderError) as c:
            self.maya.ensure("other", lambda t: None)
        self.assertIn("management is off", str(c.exception))

    def test_nothing_running_and_no_launch_script_is_a_plain_message(self):
        self.fake.close()
        s = store.load()
        s["providers"]["maya"] = {}
        store.save(s)
        with self.assertRaises(provbase.ProviderError) as c:
            self.reg.get("maya").ensure("x", lambda t: None)
        self.assertIn("launch script", str(c.exception))

    def test_a_found_maya_is_never_stopped(self):
        ok, msg = self.maya.stop()
        self.assertFalse(ok)
        self.assertEqual(self.fake.calls, [])


class LaunchScript(MayaCase):
    def test_a_launch_script_must_be_a_real_program(self):
        self.assertEqual(self.maya.ui("POST", "launch", {"command": "run-maya.sh"})[0], 400)
        self.assertEqual(self.maya.ui("POST", "launch", {"command": "/nonexistent/run.sh"})[0], 400)
        sh = os.path.join(self.tmp, "run.sh")
        with open(sh, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(sh, 0o755)
        code, d = self.maya.ui("POST", "launch", {"command": sh + " --gpu 1"})
        self.assertEqual((code, d["launch_approved"]), (200, True))
        self.assertEqual(self.maya.launch(), [sh, "--gpu", "1"])
        self.maya.ui("POST", "launch", {"command": ""})
        self.assertIsNone(self.maya.launch())

    def test_a_script_whose_saved_text_changed_is_not_approved(self):
        sh = os.path.join(self.tmp, "run.sh")
        with open(sh, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(sh, 0o755)
        self.maya.set_launch(sh)
        self.maya.ctx.remember("maya", launch=[sh, "--other"])
        self.assertFalse(self.maya.launch_approved())


class MakingRoom(MayaCase):
    def test_a_maya_on_ask_is_not_freed_until_the_person_says_yes(self):
        other = type("Other", (provbase.Base,), {"memory": lambda s: {"group": "gpu", "exclusive": True}})(
            {"id": "lemonade", "name": "Lemonade"}, "", None)
        real = router.providers
        router.providers = lambda: [self.maya, other]
        try:
            with self.assertRaises(provbase.NeedsAnswer):
                router.make_room(other, lambda t: None)
            self.assertEqual(self.fake.calls, [])
            authority.grant_once("maya")
            router.make_room(other, lambda t: None)
            self.assertEqual(self.fake.calls, [("/models/unload", {})])
        finally:
            router.providers = real


if __name__ == "__main__":
    unittest.main()
