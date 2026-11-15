"""What the chat bar's picker groups by, and the "Auto (32k)" context line. Run: python3 -m unittest discover tests"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "shoggoth"))

import agent  # noqa: E402
import server  # noqa: E402
import store  # noqa: E402


def src(sid, kind, name, state="ready", items=(), leaves=False, label=None):
    return {"id": sid, "kind": kind, "name": name, "label": label, "state": state, "items": list(items),
            "leaves_computer": leaves, "loads": "fixed", "url": ""}


class Groups(unittest.TestCase):
    """The picker's order: this computer, then each connected engine, then other Shoggoths, then paid services."""

    def test_the_order_is_this_computer_engines_shoggoths_then_paid(self):
        got = server._source_groups({"sources": [
            src("openrouter", "cloud", "OpenRouter", leaves=True),
            src("desk", "shoggoth", "desk-pc"),
            src("ollama", "engine", "Ollama"),
            src("llamacpp", "builtin", "llama.cpp", label="llama.cpp (on this computer)"),
        ]})
        self.assertEqual([g["id"] for g in got], ["llamacpp", "ollama", "desk", "openrouter"])
        self.assertEqual(got[0]["name"], "llama.cpp (on this computer)")
        self.assertTrue(got[-1]["leaves_computer"])

    def test_two_engines_are_one_group_each_by_name(self):
        got = server._source_groups({"sources": [src("strata-2", "engine", "Strata"), src("lmstudio", "engine", "LM Studio")]})
        self.assertEqual([g["name"] for g in got], ["LM Studio", "Strata"])

    def test_an_offline_source_keeps_its_place_with_no_rows_to_pick(self):
        got = server._source_groups({"sources": [src("ollama", "engine", "Ollama", state="offline", items=[{"id": "ollama/m1", "state": "offline"}])]})
        self.assertEqual((got[0]["state"], got[0]["models"]), ("offline", []))

    def test_rows_carry_the_names_chats_use(self):
        items = [{"id": "ollama/m1", "state": "available"}, {"id": "ollama/m2", "state": "offline"}]
        self.assertEqual(server._source_groups({"sources": [src("ollama", "engine", "Ollama", items=items)]})[0]["models"], ["ollama/m1"])


class AutoContext(unittest.TestCase):
    """The chat bar says "Auto (32k)": the server works out the size, the page only prints it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR")}
        os.environ["SHOGGOTH_DATA_DIR"], os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "d"), os.path.join(self.tmp, "c")

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_auto_reports_the_window_a_load_would_ask_for(self):
        from unittest import mock
        store.update(lambda s: s.setdefault("chat", {}).update(ctx="auto"))
        with mock.patch.object(agent.router, "chat_models", lambda: [{"id": "m1", "engine": "e", "pi_model": "m1", "live": False}]), \
                mock.patch.object(agent, "_auto_ctx", lambda m: 32768):
            st = agent.model_state()
        self.assertEqual((st["chat_ctx"], st["auto_ctx"]), ("auto", 32768))

    def test_a_chosen_size_has_no_auto_figure_to_show(self):
        from unittest import mock
        store.update(lambda s: s.setdefault("chat", {}).update(ctx=8192))
        with mock.patch.object(agent.router, "chat_models", lambda: []):
            st = agent.model_state()
        self.assertEqual((st["chat_ctx"], st["auto_ctx"]), (8192, 0))

    def test_auto_can_be_chosen_again_after_a_number(self):
        self.assertEqual(agent.set_chat_ctx(8192), 8192)
        self.assertEqual(agent.set_chat_ctx("auto"), "auto")
        self.assertEqual(agent.chat_ctx_setting(), "auto")


if __name__ == "__main__":
    unittest.main()
