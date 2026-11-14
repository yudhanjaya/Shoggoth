"""Old 4.x model names (user.X, Lemonade catalog names) still find their model in 5.x through model-aliases.json."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shoggoth"))
import router  # noqa: E402
import store  # noqa: E402


class OldNames(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        with open(os.path.join(self.d, "model-aliases.json"), "w") as f:
            json.dump({"user.MiMo-V2-Flash-Q4_K_M": "MiMo-V2-Flash-Q4_K_M"}, f)
        for p in (mock.patch.object(store, "data_dir", return_value=self.d),
                  mock.patch.dict(router._idx, {"t": time.time() + 3600, "v": {"MiMo-V2-Flash-Q4_K_M": ("llamacpp", "MiMo-V2-Flash-Q4_K_M")}})):
            p.start()
            self.addCleanup(p.stop)

    def test_old_name_maps_to_new(self):
        self.assertEqual(router.canon("user.MiMo-V2-Flash-Q4_K_M"), "MiMo-V2-Flash-Q4_K_M")
        self.assertTrue(router.known("user.MiMo-V2-Flash-Q4_K_M"))

    def test_listed_and_unknown_names_unchanged(self):
        self.assertEqual(router.canon("MiMo-V2-Flash-Q4_K_M"), "MiMo-V2-Flash-Q4_K_M")
        self.assertEqual(router.canon("nomic-embed"), "nomic-embed")
        self.assertEqual(router.canon(""), "")


if __name__ == "__main__":
    unittest.main()
