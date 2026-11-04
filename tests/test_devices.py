"""Which cards each engine may use. Run: python3 -m unittest discover tests"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures"))

import devices  # noqa: E402
import expert  # noqa: E402
import provbase  # noqa: E402
import router  # noqa: E402
from fake_engine import Fake  # noqa: E402

GPUS = [{"uuid": "GPU-aaa", "name": "RTX 4080", "mem_total_mb": 16376, "nv_index": 0}, {"uuid": "GPU-bbb", "name": "RTX 3080", "mem_total_mb": 10240, "nv_index": 1}]


class Devices(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR")}
        os.environ["SHOGGOTH_DATA_DIR"], os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")
        p = mock.patch.object(devices, "_gpus", lambda: GPUS)
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_off_means_every_engine_sees_every_card_whatever_was_saved(self):
        devices.set_("maya", ["GPU-aaa"])
        self.assertEqual(devices.choice("maya"), "all")
        self.assertEqual(devices.env("maya"), {})

    def test_on_a_chosen_card_becomes_the_environment_the_engine_starts_with(self):
        expert.set_on(True)
        devices.set_("maya", ["GPU-aaa"])
        self.assertEqual(devices.env("maya"), {"CUDA_DEVICE_ORDER": "PCI_BUS_ID", "CUDA_VISIBLE_DEVICES": "GPU-aaa"})
        devices.set_("maya", "none")
        self.assertEqual(devices.env("maya")["CUDA_VISIBLE_DEVICES"], "")
        devices.set_("maya", "all")
        self.assertEqual(devices.env("maya"), {})

    def test_a_card_that_does_not_exist_or_an_empty_choice_is_refused(self):
        for bad in (["GPU-zzz"], [], "some", None):
            with self.assertRaises(ValueError):
                devices.set_("maya", bad)

    def test_engines_on_different_cards_do_not_conflict_and_sharing_one_does(self):
        expert.set_on(True)
        devices.set_("maya", ["GPU-aaa"])
        devices.set_("lemonade", ["GPU-bbb"])
        self.assertTrue(devices.disjoint("maya", "lemonade"))
        devices.set_("lemonade", ["GPU-aaa", "GPU-bbb"])
        self.assertFalse(devices.disjoint("maya", "lemonade"))
        devices.set_("lemonade", "all")
        self.assertFalse(devices.disjoint("maya", "lemonade"))

    def test_an_engine_on_no_cards_conflicts_with_nobody(self):
        expert.set_on(True)
        devices.set_("strata", "none")
        devices.set_("maya", ["GPU-aaa"])
        self.assertTrue(devices.disjoint("strata", "maya"))

    def test_make_room_leaves_an_engine_on_other_cards_alone(self):
        expert.set_on(True)
        a, b = Fake("maya"), Fake("lemonade")
        real = router.providers
        router.providers = lambda: [a, b]
        try:
            with self.assertRaises(provbase.ProviderError):
                router.make_room(b, lambda t: None)                    # sharing every card: maya is in the way, and is on Ask
            devices.set_("maya", ["GPU-aaa"])
            devices.set_("lemonade", ["GPU-bbb"])
            router.make_room(b, lambda t: None)                        # their own cards: nothing to free
            self.assertEqual(a.log, [])
        finally:
            router.providers = real


if __name__ == "__main__":
    unittest.main()
