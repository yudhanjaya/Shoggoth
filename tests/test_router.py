"""The router: one model name in, the right engine running out, with memory made room for and nothing of yours ever stopped.

Real processes (a tiny OpenAI-style server) started through YAML-only providers. Run from the repository root:
python3 -m unittest discover tests
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "zest"))

import gateway  # noqa: E402
import library  # noqa: E402
import lem  # noqa: E402
import provbase  # noqa: E402
import registry  # noqa: E402
import router  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "openai_server.py")
YAML = """
    id: %(id)s
    name: %(name)s
    api_version: 1
    tier: %(tier)s
    detect: {binaries: [%(py)s], ports: %(ports)s, health: {path: /health}}
    run:
      mode: managed
      start: ["{bin}", "%(fixture)s", "{port}"]
      ready: {path: /health, timeout: 20}
      stop_grace_secs: 5
      port_range: %(ports)s
    endpoint:
      base_url: "http://127.0.0.1:{port}/v1"
      models: {path: /v1/models, id: "$.data[*].id", context: "$.data[*].meta.n_ctx"}
    models: {formats: [gguf]}
    memory: {group: gpu, exclusive: %(exclusive)s, idle_stop_minutes: %(idle)d}
    """


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("ZEST_DATA_DIR", "ZEST_CONFIG_DIR")}
        os.environ["ZEST_DATA_DIR"], os.environ["ZEST_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")
        self.user = os.path.join(self.tmp, "providers")
        os.makedirs(self.user)
        self.add("fast", id="fast", name="Fast", tier="small-medium", ports="18980-18984", exclusive="false", idle=0)
        self.add("big", id="big", name="Big", tier="large", ports="18985-18989", exclusive="true", idle=1)
        self.reg = registry.Registry(bundled=os.path.join(self.tmp, "none"), user=self.user).load()
        self.assertEqual(self.reg.errors, [])
        for pid in ("fast", "big"):
            self.reg.approve(pid)
        registry._default["r"] = self.reg
        router._idx["v"] = None
        router._idle.clear()
        router.busy_checks[:] = []
        self.fast, self.big = self.reg.get("fast"), self.reg.get("big")
        self.stray = []

    def tearDown(self):
        for p in (self.fast, self.big):
            p.stop()
        for s in self.stray:
            s.terminate()
            s.wait()
        registry._default["r"] = None
        router.busy_checks[:] = []
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, folder, code=None, **v):
        d = os.path.join(self.user, folder)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "provider.yaml"), "w") as f:
            f.write(textwrap.dedent(YAML % dict(v, py=sys.executable, fixture=FIXTURE)))
        if code:
            with open(os.path.join(d, "provider.py"), "w") as f:
                f.write(textwrap.dedent(code))

    def start_stray(self, port):
        """An engine somebody else started, listening where a provider looks."""
        p = subprocess.Popen([sys.executable, FIXTURE, str(port)], stdin=subprocess.DEVNULL)
        self.stray.append(p)
        for _ in range(50):
            if provbase.port_open(port):
                return
            time.sleep(0.1)
        self.fail("stray server didn't start")


class Resolving(Env):
    def test_names_go_to_the_provider_that_serves_them(self):
        self.fast.ensure("m1", lambda t: None)
        self.assertEqual(router.resolve("fast/m1")[0].id, "fast")
        self.assertEqual(router.resolve("fast/m1")[1], "m1")
        # a provider's name for a model that is not the default's also works for an app that uses it
        self.big.ensure("m1", lambda t: None)
        router._idx["v"] = None
        self.assertEqual(router.resolve("big/m1")[0].id, "big")
        # anything unknown goes to the default (the first provider that isn't for large models), unchanged
        p, mid = router.resolve("some-embedding-model")
        self.assertEqual((p.id, mid), ("fast", "some-embedding-model"))
        self.assertFalse(router.known("some-embedding-model"))

    def test_chat_models_carry_what_pi_and_the_page_need(self):
        self.fast.ensure("m1", lambda t: None)
        ms = router.chat_models()
        m = next(m for m in ms if m["engine"] == "fast")
        self.assertEqual((m["id"], m["pi_model"], m["pi_provider"], m["live"], m["ctx"]), ("fast/m1", "m1", "fast", True, 4096))


class Progress(Env):
    def test_the_page_can_read_progress_while_an_engine_is_still_starting(self):
        import threading
        seen, release = {}, threading.Event()
        real = self.big.ensure

        def slow(model_id, progress, opts=None):
            progress("Loading the model…")
            release.wait(10)                                   # a real big model takes minutes
            return real(model_id, progress, opts)
        self.big.ensure = slow
        t = threading.Thread(target=lambda: router.activate("big/m1", lambda x: None), daemon=True)
        t.start()
        time.sleep(0.5)
        t0 = time.time()
        seen.update(router.state())
        self.assertLess(time.time() - t0, 1.0)                  # it must not wait for the start to finish
        self.assertEqual(seen["step"], "Loading the model…")
        self.assertEqual(seen["provider"], "big")
        release.set()
        t.join(30)
        self.assertEqual(router.state()["step"], "")


class Arbiter(Env):
    def test_a_big_model_frees_the_fast_engine_and_back(self):
        log = []
        router.activate("fast/m1", log.append)
        self.assertTrue(self.fast.is_running())
        ep = router.activate("big/m1", log.append)                 # exclusive: Fast shares its memory group, so it is stopped first
        self.assertFalse(self.fast.is_running())
        self.assertTrue(self.big.is_running())
        self.assertEqual(ep["provider"], "big")
        self.assertTrue(any("stopping Fast" in t for t in log), log)
        router.activate("fast/m1", log.append)                     # and Big is stopped for Fast
        self.assertTrue(self.fast.is_running())
        self.assertFalse(self.big.is_running())

    def test_reusing_what_is_running_stops_nothing(self):
        router.activate("fast/m1")
        port = self.fast.endpoint("m1")["base_url"]
        log = []
        router.activate("fast/m1", log.append)
        self.assertEqual(self.fast.endpoint("m1")["base_url"], port)
        self.assertFalse(any("Freeing" in t for t in log))

    def test_an_engine_that_is_answering_is_not_stopped(self):
        router.activate("fast/m1")
        router.busy_checks.append(lambda pid: pid == "fast")
        with self.assertRaises(provbase.ProviderError) as cm:
            router.activate("big/m1")
        self.assertIn("answering right now", str(cm.exception))
        self.assertTrue(self.fast.is_running())
        self.assertFalse(self.big.is_running())

    def test_an_engine_you_started_only_earns_a_warning_when_the_new_one_can_share(self):
        self.start_stray(18986)                                    # a Big (exclusive) that somebody else started
        log = []
        router.activate("fast/m1", log.append)                     # Fast is not exclusive, so it goes ahead beside it
        self.assertTrue(provbase.port_open(18986))                 # never stopped
        self.assertTrue(self.fast.is_running())
        self.assertTrue(any("something you started" in t for t in log), log)

    def test_an_exclusive_engine_will_not_stop_one_you_started(self):
        self.start_stray(18981)                                    # a Fast that somebody else started
        with self.assertRaises(provbase.ProviderError) as cm:
            router.activate("big/m1")                              # Big needs the memory to itself: refused, with the reason
        self.assertIn("you started", str(cm.exception))
        self.assertTrue(provbase.port_open(18981))                 # still running
        self.assertFalse(self.big.is_running())

    def test_an_engine_that_is_not_approved_is_refused_in_words(self):
        self.big.approved = False
        with self.assertRaises(provbase.ProviderError) as cm:
            router.activate("big/m1")
        self.assertIn("approved", str(cm.exception))
        self.assertEqual(router.state()["ref"], None)              # nothing is left looking like it is starting


class Idle(Env):
    CODE = """
        import provbase

        class Provider(provbase.GenericProvider):
            def activity(self):
                return False, 7            # idle, and the output counter never moves
        """

    def setUp(self):
        super().setUp()
        self.add("quiet", code=self.CODE, id="quiet", name="Quiet", tier="large", ports="18990-18994", exclusive="true", idle=1)
        self.reg = registry.Registry(bundled=os.path.join(self.tmp, "none"), user=self.user).load()
        self.assertEqual(self.reg.errors, [])
        for pid in ("fast", "big", "quiet"):
            self.reg.approve(pid)
        registry._default["r"] = self.reg
        self.reg = registry.Registry(bundled=os.path.join(self.tmp, "none"), user=self.user).load()
        registry._default["r"] = self.reg
        self.quiet = self.reg.get("quiet")
        self.fast, self.big = self.reg.get("fast"), self.reg.get("big")

    def tearDown(self):
        self.quiet.stop()
        super().tearDown()

    def test_what_zest_started_is_stopped_once_idle_and_only_then(self):
        router.activate("quiet/m1")
        self.assertTrue(self.quiet.is_running())
        t0 = time.time()
        self.assertEqual(router.idle_tick(t0)["quiet"], "busy")        # first look: the counter is new, the clock starts
        self.assertEqual(router.idle_tick(t0 + 30)["quiet"], "idle")
        self.assertEqual(router.idle_tick(t0 + 61)["quiet"], "stopped")
        self.assertFalse(self.quiet.is_running())

    def test_a_chat_answering_resets_the_clock(self):
        router.activate("quiet/m1")
        t0 = time.time()
        router.idle_tick(t0)
        router.busy_checks.append(lambda pid: pid == "quiet")
        self.assertEqual(router.idle_tick(t0 + 500)["quiet"], "busy")
        self.assertTrue(self.quiet.is_running())

    def test_an_engine_that_cannot_report_activity_is_never_stopped_by_the_clock(self):
        router.activate("big/m1")                                        # the plain provider can't say whether it is busy
        t0 = time.time()
        router.idle_tick(t0)
        self.assertEqual(router.idle_tick(t0 + 9999)["big"], "busy")
        self.assertTrue(self.big.is_running())

    def test_nothing_of_yours_is_ever_idle_stopped(self):
        self.start_stray(18991)
        self.assertEqual(router.idle_tick(time.time() + 9999), {})


class Picker(Env):
    def test_picker_lists_what_is_running_with_what_helps_choose(self):
        self.fast.ensure("m1", lambda t: None)
        library._pk["v"] = None
        d = library.picker(max_age=0)
        m = next(m for m in d["models"] if m["engine"] == "fast")
        self.assertEqual((m["id"], m["title"], m["live"], m["start"], m["engine_name"], m["fit"]), ("fast/m1", "m1", True, "Ready now", "Fast", "fits"))
        self.assertFalse(m["ctx_fixed"])
        self.assertEqual([t["id"] for t in d["tiers"]], ["small", "medium", "large"])
        self.assertIn("ram_gb", d["machine"])

    def test_start_time_hints_are_plain(self):
        self.assertEqual(library._start_hint(0), "Ready now")
        self.assertEqual(library._start_hint(20), "Ready in seconds")
        self.assertEqual(library._start_hint(60), "About 1 minute to start")
        self.assertEqual(library._start_hint(120), "About 2 minutes to start")

    def test_the_answer_is_kept_briefly(self):
        library._pk["v"] = None
        a = library.picker(max_age=0)
        self.assertIs(library.picker(max_age=60), a)


class GatewayRouting(Env):
    def test_a_request_for_another_engines_model_goes_there(self):
        lem.configure("127.0.0.1", 1, "")                                  # the default engine is elsewhere and must not be touched
        body = json.dumps({"model": "big/m1", "messages": []}).encode()
        host, port, key, path, new_body, err = gateway._route("/v1/chat/completions", body)
        self.assertIsNone(err)
        self.assertEqual((host, port // 5), ("127.0.0.1", 18985 // 5))
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(json.loads(new_body)["model"], "m1")             # rewritten to the engine's own name
        self.assertTrue(self.big.is_running())

    def test_unknown_names_and_non_inference_paths_are_left_alone(self):
        lem.configure("127.0.0.1", 4242, "k")
        body = json.dumps({"model": "an-embedding-model"}).encode()
        self.assertEqual(gateway._route("/v1/embeddings", body), ("127.0.0.1", 4242, "k", "/v1/embeddings", body, None))
        self.assertEqual(gateway._route("/v1/health", None)[:2], ("127.0.0.1", 4242))

    def test_a_model_that_cannot_start_is_a_clean_error(self):
        self.big.approved = False
        body = json.dumps({"model": "big/m1"}).encode()
        # not running and not approved: activation fails with words, and the request is refused instead of hanging
        self.big.stop()
        h, p, k, path, b, err = gateway._route("/v1/chat/completions", body)
        self.assertTrue(err)
        self.assertIn("approved", err)

    def test_models_list_for_apps_adds_the_other_engines(self):
        self.big.ensure("m1", lambda t: None)
        router._idx["v"] = None
        sent = {}

        class H:
            def send_json(self, obj, code=200, extra=None):
                sent["obj"] = obj
        orig = lem.request
        lem.request = lambda *a, **k: (200, {"data": [{"id": "Lemonade-Model", "object": "model"}]})
        try:
            self.assertTrue(gateway._models_for_apps(H()))
        finally:
            lem.request = orig
        ids = [m["id"] for m in sent["obj"]["data"]]
        self.assertIn("Lemonade-Model", ids)
        self.assertIn("big/m1", ids)
        self.assertNotIn("fast/m1", ids)                                   # the default engine lists its own models itself


if __name__ == "__main__":
    unittest.main()
