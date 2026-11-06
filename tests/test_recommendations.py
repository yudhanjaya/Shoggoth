"""The model cards and tools Shoggoth recommends come from shoggoth/recommendations/*.yaml. These tests check the files as shipped (so an edit
with a mistake fails here before it reaches anyone), that a copy in the data folder wins, that a mistake is reported with where it is,
and that the Server page reads what the files say. Run: python3 -m unittest discover tests"""
import os
import re
import contextlib
import io
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import gateway  # noqa: E402
import recs  # noqa: E402


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = os.environ.get("SHOGGOTH_DATA_DIR")
        os.environ["SHOGGOTH_DATA_DIR"] = self.tmp
        recs._cache.clear()

    def tearDown(self):
        os.environ.pop("SHOGGOTH_DATA_DIR", None) if self._old is None else os.environ.__setitem__("SHOGGOTH_DATA_DIR", self._old)
        recs._cache.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def mine(self, name, text):
        os.makedirs(recs.user_dir(), exist_ok=True)
        with open(os.path.join(recs.user_dir(), name + ".yaml"), "w") as f:
            f.write(text)


class Shipped(Env):
    def test_the_bundled_files_have_no_mistakes(self):
        for name in recs.FILES:
            r = recs.load(name, os.path.join(recs.BUNDLED, name + ".yaml"))
            self.assertEqual(r["problems"], [], name)

    def test_they_say_what_the_app_used_to_hard_code(self):
        m, t = recs.models(), recs.tools()
        self.assertEqual([r["id"] for r in m["recommended"]][:3], ["LFM2.5-8B-A1B", "user.MiMo-V2.6-Distill-Qwen-9B-Q4_K_M", "Gemma-4-12B-it-MTP-GGUF"])
        self.assertEqual([f["size"] for f in m["first_run"]], ["small", "medium"])
        self.assertNotIn("strata", m)
        ids = {x["id"]: x for x in t["tools"]}
        self.assertEqual([x["id"] for x in t["tools"] if "server" in x["show"]], ["hermes"])
        self.assertEqual(ids["hermes"]["connect"]["fields"], ["base_url", "key", "model"])
        self.assertEqual(len([x for x in t["tools"] if "guides" in x["show"]]), 11)


class ThePicks(Env):
    """The picks as the owner gave them: names, where each comes from, and the things that must never come back."""

    def test_small_and_medium_lists_are_the_owners(self):
        rec = {r["id"]: r for r in recs.models()["recommended"]}
        self.assertEqual(list(rec), ["LFM2.5-8B-A1B", "user.MiMo-V2.6-Distill-Qwen-9B-Q4_K_M", "Gemma-4-12B-it-MTP-GGUF", "Qwen3.6-35B-A3B-MTP-GGUF",
                                     "Gemma-4-26B-A4B-it-MTP-GGUF", "GLM-4.7-Flash-GGUF", "Gemma-4-31B-it-MTP-GGUF", "Qwen3.8-27B-GGUF"])
        for r in rec.values():
            self.assertTrue(r["title"] and r["take"] and r["size_gb"], r["id"])
            self.assertEqual(r["provider"], "Lemonade")
        self.assertNotIn("Muse-Glimmer-30B-GGUF", rec)

    def test_a_model_lemonade_doesnt_list_says_where_to_fetch_it(self):
        mimo = next(r for r in recs.models()["recommended"] if r["id"].startswith("user.MiMo"))
        self.assertEqual(mimo["download"]["checkpoint"], "bartowski/MiMo-V2.6-Distill-Qwen-9B-GGUF:MiMo-V2.6-Distill-Qwen-9B-Q4_K_M.gguf")
        self.assertEqual(mimo["download"]["recipe"], "llamacpp")
        self.assertEqual([r["id"] for r in recs.models()["recommended"] if "download" in r], [mimo["id"]])

    def test_the_two_models_that_are_not_ready_show_as_coming_soon(self):
        self.assertEqual([c["title"] for c in recs.models()["coming_soon"]], ["LFM3", "GLM 5.3 Flash"])
        self.assertTrue(all(c["take"] for c in recs.models()["coming_soon"]))

    def test_recommendations_name_no_engine_but_lemonade(self):
        for r in recs.models()["recommended"]:
            self.assertEqual(r["provider"], "Lemonade", r["id"])
        for f in recs.models()["first_run"]:
            self.assertNotIn("engine", f)

    def test_no_speed_figures_on_a_card(self):
        for r in recs.models()["recommended"]:
            self.assertNotRegex(r["take"] + r["title"], r"tok/s|tokens/s|tokens per second")

    def test_a_bad_download_or_coming_soon_entry_is_reported(self):
        self.mine("models", "recommended:\n  - {id: A, label: a, download: {mmproj: x}}\ncoming_soon:\n  - {take: no title}\n")
        s = recs.summary()
        joined = "\n".join(s["problems"])
        self.assertIn("recommended[0].download: needs `checkpoint`", joined)
        self.assertIn("coming_soon[0]: needs `title`", joined)
        self.assertEqual(s["models"]["coming_soon"], [])
        self.assertNotIn("download", s["models"]["recommended"][0])


class Yours(Env):
    def test_a_copy_in_the_data_folder_is_used_instead(self):
        self.mine("models", "version: 1\nrecommended:\n  - {id: My-Model, label: Mine}\n")
        self.assertEqual([m["id"] for m in recs.models()["recommended"]], ["My-Model"])
        self.assertTrue(recs.summary()["files"]["models"].startswith(self.tmp))

    def test_a_change_is_read_without_a_restart(self):
        self.mine("models", "recommended:\n  - {id: A, label: a}\n")
        self.assertEqual(recs.models()["recommended"][0]["id"], "A")
        path = os.path.join(recs.user_dir(), "models.yaml")
        self.mine("models", "recommended:\n  - {id: B, label: b}\n")
        os.utime(path, ns=(os.stat(path).st_atime_ns, os.stat(path).st_mtime_ns + 10**9))
        self.assertEqual(recs.models()["recommended"][0]["id"], "B")

    def test_a_mistake_skips_that_entry_and_says_where(self):
        self.mine("models", "recommended:\n  - {id: A, size_gb: lots}\n  - {id: B, label: fine}\nfirst_run:\n  - {size: tiny, models: [X]}\n")
        s = recs.summary()
        self.assertEqual([m["id"] for m in s["models"]["recommended"]], ["A", "B"])
        self.assertIsNone(s["models"]["recommended"][0]["size_gb"])
        self.assertEqual(s["models"]["first_run"], [])
        joined = "\n".join(s["problems"])
        self.assertIn("recommended[0]: `size_gb` must be a number above 0", joined)
        self.assertIn("first_run[0]: `size` must be one of", joined)

    def test_a_file_that_isnt_yaml_falls_back_to_zests_own(self):
        self.mine("tools", "tools: [\n")
        s = recs.summary()
        self.assertIn("hermes", [t["id"] for t in s["tools"]["tools"]])
        self.assertTrue(any("isn't valid YAML" in p for p in s["problems"]))
        self.assertTrue(any("using its own tools.yaml" in p for p in s["problems"]))

    def test_a_tool_has_to_say_how_to_connect_to_show_on_the_server_page(self):
        self.mine("tools", "tools:\n  - {id: x, name: X, kind: app, show: [server, nowhere], detect: ['(']}\n")
        s = recs.summary()
        self.assertEqual(s["tools"]["tools"][0]["show"], [])
        joined = "\n".join(s["problems"])
        self.assertIn("isn't a place", joined)
        self.assertIn("isn't a valid regular expression", joined)
        self.assertIn("no `connect`", joined)

    def test_the_check_command_exits_1_on_a_mistake(self):
        self.mine("models", "recommended:\n  - {id: A, lable: x}\n")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(recs.main(["check", os.path.join(recs.user_dir(), "models.yaml")]), 1)
            self.assertEqual(recs.main(["check", os.path.join(recs.BUNDLED, "models.yaml")]), 0)
        self.assertIn("`lable` isn't a setting Shoggoth reads (did you mean `label`?)", out.getvalue())


class Flavour(Env):
    def test_the_lines_are_there_and_name_only_the_model(self):
        lines = recs.summary()["flavour"]["lines"]
        self.assertGreaterEqual(len(lines), 17)
        for ls in lines.values():
            for l in ls:
                self.assertNotRegex(l, r"Cthulhu|Nyarlathotep|Dagon|Azathoth|Yog")
                self.assertEqual(set(re.findall(r"\{(\w+)\}", l)) - {"model", "target"}, set())

    def test_a_line_with_an_unknown_blank_is_skipped_and_reported(self):
        self.mine("flavour", "lines:\n  thinking:\n    - \"{model} thinks.\"\n    - \"{whom} waits.\"\n  pondering: [x]\n")
        s = recs.summary()
        self.assertEqual(s["flavour"]["lines"]["thinking"], ["{model} thinks."])
        joined = "\n".join(s["problems"])
        self.assertIn("{whom} isn't something Shoggoth fills in", joined)
        self.assertIn("`pondering` isn't a setting", joined)


class NoEmojiInTheUI(unittest.TestCase):
    def test_glyphs_stand_in_for_every_emoji(self):
        html = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth", "index.html"), encoding="utf8").read()
        text = "\n".join(l for l in html.split("\n") if "base64," not in l)
        self.assertEqual(re.findall(r"[\U0001F300-\U0001FAFF\u26A0\u26A1\u2705\u2702\u270D\u270F]", text), [])


class ReadByTheApp(Env):
    def test_the_server_page_names_apps_from_the_file(self):
        self.assertEqual(gateway.app_name({"User-Agent": "opencode/0.9 ai-sdk"}), "opencode")
        self.assertEqual(gateway.app_name({"User-Agent": "python-requests/2.31"}), "Python script")
        self.mine("tools", "tools:\n  - {id: newapp, name: New App, kind: app, detect: ['newapp/']}\n")
        self.assertEqual(gateway.app_name({"User-Agent": "NewApp/1.0 (node)"}), "New App")


if __name__ == "__main__":
    unittest.main()
