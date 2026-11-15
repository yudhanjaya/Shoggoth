"""Engine definitions: bundled and yours, merged, checked, and reloaded when a file changes. Run: python3 -m unittest discover tests"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "shoggoth"))

import engines  # noqa: E402

GOOD = {"id": "ninfer", "name": "Ninfer", "category": "specialized", "summary": "Big models.", "url": "http://127.0.0.1:9100", "ports": [9100],
        "match": ["ninfer"], "cwd": [], "identify": [{"path": "/v1/models", "json": "$.data[*].owned_by", "equals": "ninfer"}],
        "formats": [{"api": "openai-completions"}], "context": {"path": "/props", "json": "$.n_ctx"}, "loads": "fixed", "key": "optional",
        "key_url": "", "docs": "https://example.com/ninfer", "hint": "Use the address it prints.", "enabled": True}


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = os.environ.get("SHOGGOTH_DATA_DIR")
        os.environ["SHOGGOTH_DATA_DIR"] = os.path.join(self.tmp, "data")
        self.mine = os.path.join(self.tmp, "data", "engines.d")
        os.makedirs(self.mine)

    def tearDown(self):
        os.environ.pop("SHOGGOTH_DATA_DIR", None) if self._old is None else os.environ.__setitem__("SHOGGOTH_DATA_DIR", self._old)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, name, text):
        p = os.path.join(self.mine, name)
        with open(p, "w") as f:
            f.write(text)
        return p

    def ids(self):
        return [e["id"] for e in engines.definitions()]


class BundledFiles(unittest.TestCase):
    """Every shipped definition passes the schema, and carries nothing that could run."""
    def test_every_bundled_file_is_valid(self):
        files = [f for f in os.listdir(engines.BUNDLED) if f.endswith(engines.EXTS)]
        self.assertTrue(files)
        for f in files:
            defs, probs = engines.read_file(os.path.join(engines.BUNDLED, f))
            self.assertEqual(probs, [], f)
            self.assertTrue(defs, f)
            for d in defs:
                self.assertEqual(set(d), set(engines.KEYS), f)

    def test_ids_are_unique_and_the_catch_all_is_last(self):
        os.environ.pop("SHOGGOTH_DATA_DIR", None)
        d = tempfile.mkdtemp()
        os.environ["SHOGGOTH_DATA_DIR"] = d
        try:
            ids = [e["id"] for e in engines.definitions()]
        finally:
            shutil.rmtree(d, ignore_errors=True)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids[-1], "other")
        self.assertTrue({"ollama", "lmstudio", "llama-server", "strata", "maya", "unsloth", "other"} <= set(ids))

    def test_the_specialized_engines_are_in_their_category(self):
        by = {e["id"]: e for e in engines.definitions()}
        self.assertEqual({by[i]["category"] for i in ("strata", "maya")}, {"specialized"})
        self.assertEqual(by["strata"]["match"], ["strata"])

    def test_engines_that_only_say_python_are_matched_by_their_folder(self):
        by = {e["id"]: e for e in engines.definitions()}
        self.assertEqual(by["tabbyapi"]["cwd"], ["tabbyAPI"])
        self.assertEqual(by["textgen"]["cwd"], ["text-generation-webui", "textgen"])

    def test_ninfer_is_recognised_by_its_metrics(self):
        by = {e["id"]: e for e in engines.definitions()}
        self.assertIn({"path": "/metrics", "contains": "ninfer_"}, by["ninfer"]["identify"])


class Schema(unittest.TestCase):
    def bad(self, **kw):
        d, errs = engines.clean(dict(GOOD, **kw))
        self.assertIsNone(d)
        return " ".join(m for _, m in errs)

    def test_a_good_definition_comes_back_whole(self):
        d, errs = engines.clean(GOOD)
        self.assertEqual((errs, d), ([], GOOD))

    def test_unknown_keys_are_refused(self):
        for k in ("start", "command", "install", "run"):
            self.assertIn("unknown key `%s`" % k, self.bad(**{k: "rm -rf /"}))

    def test_a_near_miss_is_suggested(self):
        self.assertIn("did you mean `summary`", self.bad(sumary="x"))

    def test_urls_must_be_http_or_https(self):
        for key in ("url", "docs", "key_url"):
            for u in ("file:///etc/passwd", "ftp://x/y", "javascript:alert(1)", "http://u:p@h", "//h/x", "http://h:99999"):
                self.assertIn("`%s`" % key, self.bad(**{key: u}), u)

    def test_identify_paths_must_be_plain(self):
        for p in ("props", "http://evil/props", "/props?x=1", "/a/../b", "//evil", "/p q", "/p#frag", ""):
            self.assertNotEqual(self.bad(identify=[{"path": p}]), "", p)
        self.assertIsNotNone(engines.clean(dict(GOOD, identify=[{"path": "/api/v0/models"}]))[0])

    def test_identify_and_context_take_only_their_own_keys(self):
        self.assertIn("unknown key `method`", self.bad(identify=[{"path": "/x", "method": "POST"}]))
        self.assertIn("unknown key `body`", self.bad(context={"path": "/x", "json": "$.a", "body": "{}"}))

    def test_a_header_signature_needs_something_to_compare(self):
        self.assertIn("equals", self.bad(identify=[{"path": "/", "header": "Server"}]))

    def test_choices(self):
        for k, v in (("category", "cloud"), ("loads", "sometimes"), ("key", "maybe"), ("enabled", "no")):
            self.assertNotEqual(self.bad(**{k: v}), "", k)
        self.assertIn("api", self.bad(formats=[{"api": "carrier-pigeon"}]))

    def test_ports_and_ids(self):
        self.assertNotEqual(self.bad(ports=[0]), "")
        self.assertNotEqual(self.bad(ports=["8080"]), "")
        self.assertNotEqual(self.bad(id="Has Space"), "")

    def test_cwd_takes_working_folder_names(self):
        d, errs = engines.clean(dict(GOOD, cwd="TabbyAPI"))
        self.assertEqual((errs, d["cwd"]), ([], ["TabbyAPI"]))
        self.assertIn("`cwd` must be a list", self.bad(cwd=5))

    def test_a_plain_text_signature_needs_only_a_path_and_contains(self):
        d, errs = engines.clean(dict(GOOD, identify=[{"path": "/metrics", "contains": "ninfer_"}]))
        self.assertEqual((errs, d["identify"]), ([], [{"path": "/metrics", "contains": "ninfer_"}]))

    def test_a_hidden_entry_needs_only_an_id(self):
        d, errs = engines.clean({"id": "strata", "enabled": False})
        self.assertEqual((errs, d["enabled"]), ([], False))

    def test_the_port_is_taken_from_a_local_url(self):
        d, _ = engines.clean({"id": "x", "name": "X", "url": "http://localhost:7000"})
        self.assertEqual(d["ports"], [7000])


class Merging(Env):
    def test_yours_adds_to_the_bundled_ones(self):
        self.put("ninfer.yaml", "id: ninfer\nname: Ninfer\ncategory: specialized\nurl: http://127.0.0.1:9100\n")
        self.assertIn("ninfer", self.ids())
        self.assertIn("ollama", self.ids())

    def test_the_same_id_replaces_the_bundled_entry(self):
        self.put("ollama.yaml", "id: ollama\nname: My Ollama\nurl: http://127.0.0.1:11999\n")
        by = {e["id"]: e for e in engines.definitions()}
        self.assertEqual((by["ollama"]["name"], by["ollama"]["ports"]), ("My Ollama", [11999]))
        self.assertEqual(self.ids().count("ollama"), 1)

    def test_a_user_definition_cannot_move_a_hosted_service(self):
        self.put("or.yaml", "id: openrouter\nname: OpenRouter fix\ncategory: hosted\nurl: https://evil.example/api/v1\nkey: required\n")
        by = {e["id"]: e for e in engines.definitions()}
        self.assertEqual(by["openrouter"]["url"], "https://openrouter.ai/api/v1")
        self.assertEqual(by["openrouter"]["name"], "OpenRouter")
        msg = [p for p in engines.problems() if p["file"].endswith("or.yaml")]
        self.assertEqual(len(msg), 1)
        self.assertIn("openrouter.ai", msg[0]["error"])
        self.assertIn("new `id`", msg[0]["error"])

    def test_a_user_definition_may_restate_a_hosted_service_or_use_a_new_id(self):
        self.put("or.yaml", "id: openrouter\nname: My OpenRouter\ncategory: hosted\nurl: https://openrouter.ai/api/v1\nkey: required\n")
        self.put("mine.yaml", "id: myhost\nname: Mine\ncategory: hosted\nurl: https://llm.example.com/v1\nkey: required\n")
        by = {e["id"]: e for e in engines.definitions()}
        self.assertEqual(by["openrouter"]["name"], "My OpenRouter")
        self.assertIn("myhost", by)
        self.assertEqual(engines.problems(), [])

    def test_hosted_definitions_carry_their_host(self):
        by = {e["id"]: e for e in engines.definitions()}
        self.assertEqual(by["openrouter"]["host"], "openrouter.ai")
        self.assertNotIn("host", by["ollama"])

    def test_alias_bombs_are_refused(self):
        bomb = "a: &a [x, x, x, x, x, x, x, x, x, x]\n" + "".join(
            "%s: &%s [%s]\n" % (c, c, ", ".join(["*" + p] * 10)) for p, c in zip("abcdefgh", "bcdefghi"))
        self.put("bomb.yaml", bomb)
        loop = self.put("loop.yaml", "id: &x [*x]\n")
        probs = {p["file"]: p["error"] for p in engines.problems()}
        self.assertIn("too many anchors", probs["engines.d/bomb.yaml"])
        self.assertIn("too many anchors", probs["engines.d/loop.yaml"])
        self.assertIn("ollama", self.ids())

    def test_small_aliases_still_work(self):
        self.put("al.yaml", "id: al\nname: &n Al\nsummary: *n\n")
        self.assertEqual([e["summary"] for e in engines.definitions() if e["id"] == "al"], ["Al"])

    def test_enabled_false_hides_one(self):
        self.put("hide.yaml", "- {id: maya, enabled: false}\n- {id: unsloth, enabled: false}\n")
        self.assertNotIn("maya", self.ids())
        self.assertNotIn("unsloth", self.ids())
        self.assertIn("strata", self.ids())

    def test_json_and_yaml_say_the_same_thing(self):
        self.put("a.yaml", "id: same\nname: Same\ncategory: specialized\nports: [9200]\nmatch: [same]\nidentify:\n  - {path: /x, json: '$.a', equals: b}\n")
        a = [e for e in engines.definitions() if e["id"] == "same"][0]
        os.remove(os.path.join(self.mine, "a.yaml"))
        self.put("b.json", json.dumps({"id": "same", "name": "Same", "category": "specialized", "ports": [9200], "match": ["same"],
                                       "identify": [{"path": "/x", "json": "$.a", "equals": "b"}]}))
        b = [e for e in engines.definitions() if e["id"] == "same"][0]
        self.assertEqual(a, b)

    def test_a_file_can_hold_a_list(self):
        self.put("many.json", json.dumps([{"id": "one", "name": "One"}, {"id": "two", "name": "Two"}]))
        self.assertTrue({"one", "two"} <= set(self.ids()))

    def test_changing_a_file_is_noticed(self):
        p = self.put("n.yaml", "id: n\nname: First\n")
        self.assertEqual([e["name"] for e in engines.definitions() if e["id"] == "n"], ["First"])
        self.put("n.yaml", "id: n\nname: Second, longer\n")
        os.utime(p, ns=(1, os.stat(p).st_mtime_ns + 5_000_000_000))
        self.assertEqual([e["name"] for e in engines.definitions() if e["id"] == "n"], ["Second, longer"])
        os.remove(p)
        self.assertNotIn("n", self.ids())


class BadFiles(Env):
    def test_a_bad_file_is_reported_and_the_rest_still_load(self):
        self.put("bad.yaml", "id: broken\nname: Broken\nstart: ./run.sh\n")
        self.put("worse.yaml", "id: [unclosed\n")
        self.put("good.yaml", "id: fine\nname: Fine\n")
        self.assertIn("fine", self.ids())
        self.assertNotIn("broken", self.ids())
        self.assertIn("ollama", self.ids())
        probs = {p["file"]: p for p in engines.problems()}
        self.assertEqual(probs["engines.d/bad.yaml"]["line"], 3)
        self.assertIn("unknown key `start`", probs["engines.d/bad.yaml"]["error"])
        self.assertIn("isn't valid YAML", probs["engines.d/worse.yaml"]["error"])
        self.assertNotIn("engines.d/good.yaml", probs)

    def test_bad_json_says_where(self):
        self.put("bad.json", '{"id": "x",\n "name": }')
        p = [p for p in engines.problems() if p["file"].endswith("bad.json")][0]
        self.assertEqual(p["line"], 2)

    def test_one_bad_entry_in_a_list_does_not_take_the_others(self):
        self.put("list.yaml", "- {id: ok, name: Ok}\n- {id: bad, name: Bad, url: 'file:///x'}\n")
        self.assertIn("ok", self.ids())
        self.assertNotIn("bad", self.ids())

    def test_an_empty_file_is_reported(self):
        self.put("empty.yaml", "")
        self.assertTrue([p for p in engines.problems() if p["file"].endswith("empty.yaml")])


class CheckCommand(Env):
    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = engines.main(list(argv))
        return code, out.getvalue()

    def test_a_good_file_passes(self):
        code, out = self.run_main("check", self.put("ok.yaml", "id: ok\nname: Ok\n"))
        self.assertEqual(code, 0)
        self.assertIn("1 definition", out)

    def test_a_bad_file_fails_with_its_line(self):
        code, out = self.run_main("check", self.put("no.yaml", "id: no\nname: No\ninstall: curl x | sh\n"))
        self.assertEqual(code, 1)
        self.assertIn("line 3", out)
        self.assertIn("unknown key `install`", out)

    def test_usage(self):
        self.assertEqual(self.run_main()[0], 2)


if __name__ == "__main__":
    unittest.main()
