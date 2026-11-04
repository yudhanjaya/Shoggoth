"""Routing flows. Run: python3 -m unittest discover tests"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import expert  # noqa: E402
import flows  # noqa: E402
import gateway  # noqa: E402
import router  # noqa: E402

FLOW = """
version: 1
targets:
  quick: {engine: lemonade, model: small, can: [tools]}
  glm:   {engine: maya, model: glm-5.3-flash, can: [tools, vision]}
flow:
  start: n_compact
  nodes:
    n_compact: {type: match, when: {kind: [compaction, title]}, yes: quick, no: n_tools}
    n_tools:
      type: match
      when: {any: [{tool_results_in_turn: {gte: 3}}, {has_code: true}]}
      yes: glm
      no: quick
  default: glm
policy: {swap: ask, ram_guard: strict}
"""


def chat(text, **extra):
    return dict({"model": "auto", "messages": [{"role": "user", "content": text}]}, **extra)


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR")}
        os.environ["SHOGGOTH_DATA_DIR"], os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)


class Flows(Env):
    def target(self, body, loaded=()):
        return flows.decide(flows.parse(FLOW), flows.features(body), loaded)

    def test_a_good_flow_has_no_problems(self):
        self.assertEqual(flows.validate(flows.parse(FLOW)), ([], []))

    def test_each_mistake_is_named_where_it_is(self):
        bad = FLOW.replace("default: glm", "default: nope").replace("kind: [compaction, title]", "colour: red").replace("version: 1", "version: 2")
        problems, _ = flows.validate(flows.parse(bad))
        text = " ".join(problems)
        for part in ("version", "flow.default", "colour"):
            self.assertIn(part, text)

    def test_a_loop_and_an_unreachable_node_are_caught(self):
        loop = FLOW.replace("no: quick\n  default", "no: n_compact\n  default")
        self.assertTrue(any("loops" in p for p in flows.validate(flows.parse(loop))[0]))
        extra = FLOW.replace("  default: glm", "    lonely: {type: match, when: {has_tools: true}, yes: glm, no: quick}\n  default: glm")
        self.assertEqual(flows.validate(flows.parse(extra))[1], ["flow.nodes.lonely: nothing leads to it."])

    def test_a_short_chat_goes_to_the_small_model_and_the_path_says_why(self):
        t, path = self.target(chat("hi there"))
        self.assertEqual((t, path), ("quick", "n_compact:no → n_tools:no → quick"))

    def test_code_and_long_tool_chains_go_to_the_big_model(self):
        self.assertEqual(self.target(chat("fix this:\n```py\nx=1\n```"))[0], "glm")
        turn = chat("run it") ["messages"] + [{"role": "tool", "content": "ok"}] * 3
        self.assertEqual(self.target({"model": "auto", "messages": turn})[0], "glm")

    def test_a_compaction_request_is_the_small_models(self):
        body = {"model": "auto", "messages": [{"role": "system", "content": "Summarize the conversation so far."}, {"role": "user", "content": "..."}]}
        self.assertEqual(self.target(body)[0], "quick")

    def test_a_target_that_cannot_see_images_is_never_chosen_for_one(self):
        img = {"model": "auto", "messages": [{"role": "user", "content": [{"type": "text", "text": "what is this"},
                                                                          {"type": "image_url", "image_url": {"url": "data:..."}}]}]}
        t, path = self.target(img)
        self.assertEqual(t, "glm")
        self.assertIn("quick can't handle it", path)

    def test_loaded_lets_a_flow_prefer_what_is_resident(self):
        d = flows.parse(FLOW.replace("when: {kind: [compaction, title]}", "when: {loaded: glm}"))
        self.assertEqual(flows.decide(d, flows.features(chat("hi")), {"glm"})[0], "quick")
        self.assertEqual(flows.decide(d, flows.features(chat("hi")), set())[0], "quick")

    def test_features_read_the_request(self):
        f = flows.features(chat("one? two?", max_tokens=32, tools=[{}]))
        self.assertEqual((f["question_count"], f["max_tokens"], f["has_tools"], f["kind"]), (2, 32, True, "chat"))
        self.assertEqual(flows.features({"model": "e", "input": "x"}, "/v1/embeddings")["kind"], "embedding")
        self.assertEqual(flows.features(b"not json")["kind"], "chat")

    def test_a_dry_run_counts_where_a_set_of_requests_would_go(self):
        r = flows.dry_run(FLOW, [chat("hi"), chat("```x```"), chat("yo")])
        self.assertEqual((r["requests"], r["per_target"]), (3, {"quick": 2, "glm": 1}))

    def test_nothing_applies_with_expert_mode_off_or_no_active_flow(self):
        flows.save("mine", FLOW)
        flows.activate("mine")
        self.assertIsNone(flows.active())                      # Expert Mode is off
        expert.set_on(True)
        self.assertEqual(flows.active()["flow"]["default"], "glm")
        flows.activate(None)
        self.assertIsNone(flows.active())

    def test_saving_refuses_a_flow_with_problems_and_keeps_warnings(self):
        with self.assertRaises(ValueError):
            flows.save("bad", "version: 1")
        with self.assertRaises(ValueError):
            flows.save("", FLOW)
        self.assertEqual(flows.saved(), {})

    def test_deleting_the_active_flow_deactivates_it(self):
        expert.set_on(True)
        flows.save("mine", FLOW)
        flows.activate("mine")
        flows.delete("mine")
        self.assertIsNone(flows.active())


class Prov:
    def __init__(self, pid):
        self.id = pid

    def ref(self, m):
        return "%s/%s" % (self.id, m)


class Auto(Env):
    def setUp(self):
        super().setUp()
        expert.set_on(True)
        flows.save("mine", FLOW)
        flows.activate("mine")
        self.resident = set()
        self._real = (router.get, router.loaded)
        router.get = lambda pid: Prov(pid)
        router.loaded = lambda ref: (ref in self.resident, 0)

    def tearDown(self):
        router.get, router.loaded = self._real
        super().tearDown()

    def ask(self, text):
        return gateway._auto("/v1/chat/completions", json.dumps(chat(text)).encode())

    def test_auto_becomes_the_flows_choice_in_the_request(self):
        model, body = self.ask("hi")
        self.assertEqual((model, json.loads(body)["model"]), ("lemonade/small", "lemonade/small"))
        self.assertTrue(flows.RECENT[-1]["path"].endswith("quick"))

    def test_auto_is_never_moved_to_an_engine_that_is_not_loaded_when_another_is(self):
        self.resident = {"lemonade/small"}
        model, _ = self.ask("fix:\n```x```")                       # the flow says glm, but only the small one is resident
        self.assertEqual(model, "lemonade/small")
        self.assertIn("staying on quick", flows.RECENT[-1]["path"])

    def test_with_nothing_loaded_the_flows_own_choice_is_used(self):
        self.assertEqual(self.ask("fix:\n```x```")[0], "maya/glm-5.3-flash")

    def test_no_active_flow_means_auto_is_left_to_the_old_path(self):
        flows.activate(None)
        self.assertIsNone(self.ask("hi"))


if __name__ == "__main__":
    unittest.main()
