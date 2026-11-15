"""Per-model API formats for hosted services: the `formats` rules, what Pi's models.json gets, and how the Server page reads each format's stream.

Run from the repository root:  python3 -m unittest discover tests
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "shoggoth"))
sys.path.insert(0, HERE)

import agent  # noqa: E402
import engines  # noqa: E402
import gateway  # noqa: E402
import online_services  # noqa: E402


class Rules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = os.environ.get("SHOGGOTH_DATA_DIR")
        os.environ["SHOGGOTH_DATA_DIR"] = self.tmp
        online_services.install()

    def tearDown(self):
        os.environ.pop("SHOGGOTH_DATA_DIR", None) if self._old is None else os.environ.__setitem__("SHOGGOTH_DATA_DIR", self._old)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_zen_and_command_code_pick_a_format_per_model(self):
        for eid in ("opencode-zen", "command-code"):
            self.assertEqual(engines.api_for(eid, "claude-sonnet-4-5"), "anthropic-messages")
            self.assertEqual(engines.api_for(eid, "anthropic/claude-opus-4"), "anthropic-messages")
            self.assertEqual(engines.api_for(eid, "gpt-5"), "openai-responses")
            self.assertEqual(engines.api_for(eid, "openai/gpt-5-codex"), "openai-responses")
            self.assertEqual(engines.api_for(eid, "glm-4.6"), "openai-completions")
            self.assertEqual(engines.api_for(eid, "kimi-k2"), "openai-completions")

    def test_plain_services_are_chat_completions_for_every_model(self):
        for eid in ("openrouter", "nous-portal", "opencode-go"):
            self.assertEqual(engines.api_for(eid, "anthropic/claude-3.5"), "openai-completions")
            self.assertEqual(engines.api_for(eid, "gpt-5"), "openai-completions")

    def test_gemini_uses_its_own_format_because_pi_drops_the_thought_signature(self):
        self.assertEqual(engines.api_for("gemini", "gemini-2.5-pro"), "google-generative-ai")

    def test_unknown_engines_and_missing_rules_fall_back(self):
        self.assertEqual(engines.api_for("", "x"), "openai-completions")
        self.assertEqual(engines.api_for("no-such-engine", "x"), "openai-completions")
        self.assertEqual(engines.api_for({"formats": [{"api": "anthropic-messages", "match": "^a"}]}, "b"), "openai-completions")

    def test_every_bundled_hosted_rule_is_a_known_api(self):
        for d in engines.definitions():
            for f in d.get("formats") or []:
                self.assertIn(f["api"], engines.APIS)

    def test_each_client_gets_the_address_shape_it_expects(self):
        self.assertEqual(engines.pi_base_url("openai-completions", "https://x/zen/v1"), "https://x/zen/v1")
        self.assertEqual(engines.pi_base_url("openai-responses", "https://x/zen/v1"), "https://x/zen/v1")
        self.assertEqual(engines.pi_base_url("anthropic-messages", "https://x/zen/v1"), "https://x/zen")      # the client adds /v1/messages
        self.assertEqual(engines.pi_base_url("google-generative-ai", "http://127.0.0.1:1/api/v1"), "http://127.0.0.1:1/api/v1beta")
        self.assertEqual(engines.pi_base_url("google-generative-ai", "https://g.example/v1beta/openai/v1"), "https://g.example/v1beta")

    def test_only_models_that_can_chat_are_kept_from_a_gemini_listing(self):
        listing = {"models": [
            {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent", "countTokens"]},
            {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
            {"name": "models/imagen-4", "supportedGenerationMethods": ["predict"]},
            {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/broken"}]}
        self.assertEqual(engines.gemini_chat_models(listing), ["gemini-2.5-flash", "gemini-2.5-pro"])
        self.assertEqual(engines.gemini_chat_models({}), [])


class PiModels(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = os.environ.get("SHOGGOTH_DATA_DIR"), os.environ.get("SHOGGOTH_CONFIG_DIR")
        os.environ["SHOGGOTH_DATA_DIR"], os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "d"), os.path.join(self.tmp, "c")
        online_services.install()

    def tearDown(self):
        for k, v in zip(("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR"), self._old):
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_models_json_carries_an_api_per_model(self):
        def m(name, **kw):
            return dict({"id": "zen/" + name, "pi_model": name, "engine": "zen", "pi_provider": "zen", "pi_url": "https://opencode.ai/zen/v1", "pi_key": "k",
                         "reasoning": False, "vision": False, "ctx": 32768}, **kw)
        models = [m("claude-sonnet-4-5"), m("gpt-5"), m("glm-4.6"), m("gemini-2.5-pro", api="google-generative-ai")]
        import servers
        orig = servers.get
        servers.get = lambda sid: {"preset": "opencode-zen"} if sid == "zen" else None
        try:
            agent.write_config(models, "zen/glm-4.6")
        finally:
            servers.get = orig
        with open(os.path.join(agent.agent_dir(), "models.json")) as f:
            prov = json.load(f)["providers"]["zen"]
        by = {x["id"]: x for x in prov["models"]}
        self.assertEqual(by["claude-sonnet-4-5"]["api"], "anthropic-messages")
        self.assertEqual(by["claude-sonnet-4-5"]["baseUrl"], "https://opencode.ai/zen")
        self.assertEqual(by["gpt-5"]["api"], "openai-responses")
        self.assertEqual(by["gpt-5"]["baseUrl"], "https://opencode.ai/zen/v1")
        self.assertEqual(by["glm-4.6"]["api"], "openai-completions")
        self.assertNotIn("baseUrl", by["glm-4.6"])                          # inherits the provider's
        self.assertEqual(by["gemini-2.5-pro"]["api"], "google-generative-ai")
        self.assertEqual(by["gemini-2.5-pro"]["baseUrl"], "https://opencode.ai/zen/v1beta")
        self.assertTrue(prov["authHeader"])

    def test_a_local_engine_stays_on_chat_completions(self):
        m = {"id": "fast/q", "pi_model": "q", "engine": "fast", "pi_provider": "fast", "pi_url": "http://127.0.0.1:1/v1", "reasoning": False,
             "vision": False, "ctx": 8192}
        agent.write_config([m], "fast/q")
        with open(os.path.join(agent.agent_dir(), "models.json")) as f:
            prov = json.load(f)["providers"]["fast"]
        self.assertEqual(prov["models"][0]["api"], "openai-completions")
        self.assertNotIn("authHeader", prov)


def new_tap(stream=True):
    rec = {"state": "waiting", "stream": stream, "t_think": None, "t_write": None, "think_chars": 0, "out_chars": 0, "tokens_in": None,
           "tokens_out": None, "think": "", "answer": "", "tps": None, "pps": None, "error": "", "status": None, "t_end": None}
    return gateway.Tap(rec, True), rec


def sse(*events):
    return b"".join(("event: x\n" if isinstance(e, dict) and "type" in e else "") .encode() + b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)


class Classifier(unittest.TestCase):
    def test_anthropic_stream_thinking_then_writing(self):
        tap, r = new_tap()
        tap.feed(sse({"type": "message_start", "message": {"usage": {"input_tokens": 11, "output_tokens": 1}}},
                     {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
                     {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Let me see. "}}))
        self.assertEqual((r["state"], r["think"]), ("thinking", "Let me see. "))
        tap.feed(sse({"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "abc"}},
                     {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Hello"}},
                     {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 7}}))
        tap.finish(200)
        self.assertEqual((r["state"], r["answer"], r["think"]), ("done", "Hello", "Let me see. "))
        self.assertEqual((r["tokens_in"], r["tokens_out"]), (11, 7))

    def test_anthropic_reply_that_was_not_streamed(self):
        tap, r = new_tap(stream=False)
        tap.feed(json.dumps({"type": "message", "content": [{"type": "thinking", "thinking": "hm"}, {"type": "text", "text": "Hi"}],
                             "usage": {"input_tokens": 3, "output_tokens": 4}}).encode())
        tap.finish(200)
        self.assertEqual((r["think"], r["answer"], r["tokens_in"], r["tokens_out"]), ("hm", "Hi", 3, 4))

    def test_responses_events(self):
        tap, r = new_tap()
        tap.feed(sse({"type": "response.reasoning_text.delta", "delta": "Thinking "},
                     {"type": "response.output_text.delta", "delta": "Answer"},
                     {"type": "response.completed", "response": {"usage": {"input_tokens": 5, "output_tokens": 9}}}))
        tap.finish(200)
        self.assertEqual((r["think"], r["answer"], r["state"]), ("Thinking ", "Answer", "done"))
        self.assertEqual((r["tokens_in"], r["tokens_out"]), (5, 9))

    def test_responses_reply_that_was_not_streamed(self):
        tap, r = new_tap(stream=False)
        tap.feed(json.dumps({"object": "response", "output": [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "why"}]},
            {"type": "message", "content": [{"type": "output_text", "text": "because"}]}], "usage": {"input_tokens": 1, "output_tokens": 2}}).encode())
        tap.finish(200)
        self.assertEqual((r["think"], r["answer"], r["tokens_out"]), ("why", "because", 2))

    def test_gemini_chunks(self):
        tap, r = new_tap()
        tap.feed(sse({"candidates": [{"content": {"parts": [{"text": "pondering", "thought": True}]}}]}))
        self.assertEqual((r["state"], r["think"]), ("thinking", "pondering"))
        tap.feed(sse({"candidates": [{"content": {"parts": [{"text": "The answer"}]}}]},
                     {"candidates": [{"content": {"parts": [{"functionCall": {"name": "f", "args": {}}, "thoughtSignature": "s"}]}}],
                      "usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 3, "thoughtsTokenCount": 2}}))
        tap.finish(200)
        self.assertEqual((r["answer"], r["think"], r["tokens_in"], r["tokens_out"]), ("The answer", "pondering", 8, 5))

    def test_openai_chunks_still_work(self):
        tap, r = new_tap()
        tap.feed(sse({"choices": [{"delta": {"reasoning_content": "t"}}]}, {"choices": [{"delta": {"content": "a"}}]}))
        self.assertEqual((r["think"], r["answer"]), ("t", "a"))

    def test_gemini_paths_are_recognised(self):
        p = "/api/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse"
        self.assertTrue(gateway.INFER.match(gateway.norm(p)))
        self.assertTrue(gateway.ALLOW.match(gateway.norm(p)))
        self.assertEqual(gateway.describe(b"{}", p), ("gemini-2.5-flash", True))
        self.assertEqual(gateway.describe(b"{}", "/v1beta/models/models/x:generateContent"), ("x", False))
        self.assertFalse(gateway.ALLOW.match("/v1beta/models/x:delete"))
        self.assertTrue(gateway.ALLOW.match("/v1/messages"))
        self.assertTrue(gateway.INFER.match("/v1/responses"))


if __name__ == "__main__":
    unittest.main()
