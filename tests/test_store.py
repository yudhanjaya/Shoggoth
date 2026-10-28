"""Settings are saved by several threads at once; none of them may undo another's change. Run: python3 -m unittest discover tests"""
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "zest"))

import store  # noqa: E402


class Merging(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = os.environ.get("ZEST_CONFIG_DIR")
        os.environ["ZEST_CONFIG_DIR"] = self.tmp

    def tearDown(self):
        os.environ.pop("ZEST_CONFIG_DIR", None) if self._old is None else os.environ.__setitem__("ZEST_CONFIG_DIR", self._old)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_two_writers_that_changed_different_settings_both_keep_their_change(self):
        a, b = store.load(), store.load()                     # both read the file before either saved
        a["gateway"]["port"] = 14000
        b["chat"]["ctx"] = 65536
        store.save(a)
        store.save(b)
        now = store.load()
        self.assertEqual(now["gateway"]["port"], 14000)
        self.assertEqual(now["chat"]["ctx"], 65536)
        self.assertEqual(b["gateway"]["port"], 14000)         # and the one who saved last carries on with the merged result

    def test_the_same_setting_goes_to_the_one_who_saved_last(self):
        a, b = store.load(), store.load()
        a["chat"]["ctx"] = 32768
        b["chat"]["ctx"] = 65536
        store.save(a)
        store.save(b)
        self.assertEqual(store.load()["chat"]["ctx"], 65536)

    def test_a_removed_key_stays_removed_and_a_new_key_is_kept(self):
        s = store.load()
        s["strata"]["known"] = {"x": 1}
        store.save(s)
        a, b = store.load(), store.load()
        del a["strata"]["known"]["x"]
        b["providers"]["mine"] = {"approved": "abc"}
        store.save(a)
        store.save(b)
        now = store.load()
        self.assertEqual(now["strata"]["known"], {})
        self.assertEqual(now["providers"]["mine"], {"approved": "abc"})

    def test_many_threads_changing_their_own_setting_lose_nothing(self):
        def work(i):
            for _ in range(5):
                s = store.load()
                s["providers"].setdefault("p%d" % i, {})["n"] = s["providers"].get("p%d" % i, {}).get("n", 0) + 1
                store.save(s)
        ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual([store.load()["providers"]["p%d" % i]["n"] for i in range(8)], [5] * 8)

    def test_update_is_one_step(self):
        store.update(lambda s: s["chat"].update(ctx=1024))
        self.assertEqual(store.load()["chat"]["ctx"], 1024)

    def test_a_plain_dict_still_saves(self):
        store.save({"mode": "builtin"})
        self.assertEqual(store.load()["mode"], "builtin")


if __name__ == "__main__":
    unittest.main()
