"""Providers: the YAML is checked, folders that are wrong are skipped and explained, and an engine can be added with a YAML file alone.

Run from the repository root:  python3 -m unittest discover tests
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import textwrap
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "zest"))

import engine  # noqa: E402
import provbase  # noqa: E402
import provcheck  # noqa: E402
import registry  # noqa: E402
import strata  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "openai_server.py")
GOOD = {"id": "demo", "name": "Demo", "api_version": 1, "run": {"mode": "managed", "start": ["{bin}", "--port", "{port}"], "ready": {"path": "/health"}},
        "endpoint": {"base_url": "http://127.0.0.1:{port}/v1"}}


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("ZEST_DATA_DIR", "ZEST_CONFIG_DIR")}
        os.environ["ZEST_DATA_DIR"], os.environ["ZEST_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")
        self.user = os.path.join(self.tmp, "data", "providers")
        os.makedirs(self.user)

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, name, yaml_text, code=None):
        d = os.path.join(self.user, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "provider.yaml"), "w") as f:
            f.write(textwrap.dedent(yaml_text))
        if code:
            with open(os.path.join(d, "provider.py"), "w") as f:
                f.write(textwrap.dedent(code))
        return d

    def registry(self):
        return registry.Registry(bundled=os.path.join(HERE, "..", "zest", "providers"), user=self.user).load()


class Helpers(unittest.TestCase):
    def test_jpath(self):
        d = {"data": [{"id": "a", "n": 1}, {"id": "b", "n": 2}], "x": {"y": [10, 20]}}
        self.assertEqual(provbase.jpath(d, "$.data[*].id"), ["a", "b"])
        self.assertEqual(provbase.jpath(d, "$.x.y[1]"), [20])
        self.assertEqual(provbase.jpath(d, "$.missing"), [])

    def test_fill_rejects_unknown_names(self):
        self.assertEqual(provbase.fill(["{bin}", "--port", "{port}"], bin="/x", port=8080), ["/x", "--port", "8080"])
        with self.assertRaises(provbase.ProviderError):
            provbase.fill("{secret}")

    def test_port_range(self):
        self.assertEqual(provbase.port_range("8080-8082"), [8080, 8081, 8082])
        self.assertEqual(provbase.port_range([13305, "13307-13308"]), [13305, 13307, 13308])


class Validation(unittest.TestCase):
    def test_good(self):
        self.assertEqual(registry.validate(GOOD), [])

    def test_problems_say_where(self):
        cases = {
            "run.start": dict(GOOD, run={"mode": "managed", "start": "my-engine --port 1", "ready": {"path": "/health"}}),          # a shell string
            "id": dict(GOOD, id="Bad Id"),
            "api_version": dict(GOOD, api_version=7),
            "run.mode": dict(GOOD, run={"mode": "magic"}),
            "install": dict(GOOD, install={"url": "https://x.example/i.sh"}),                         # no checksum
            "install.url": dict(GOOD, install={"url": "http://x.example/i.sh", "unsafe_install": True}),
            "unknown variable": dict(GOOD, run={"mode": "managed", "start": ["{bin}", "{secret}"]}),
            "Unknown top-level": dict(GOOD, surprise=True),
            "endpoint.base_url": {k: v for k, v in GOOD.items() if k != "endpoint"},
        }
        for needle, spec in cases.items():
            with self.subTest(needle):
                self.assertTrue(any(needle in p for p in registry.validate(spec)), registry.validate(spec))


class Loading(Env):
    def test_bundled_providers_load(self):
        r = self.registry()
        self.assertEqual(sorted(p.id for p in r.all()), ["lemonade", "strata"])
        self.assertEqual(r.errors, [])
        # the template folder starts with _ and is never loaded
        self.assertNotIn("my-engine", r.providers)

    def test_architecture_rules_route_the_right_files(self):
        r = self.registry()
        big = {"format": "gguf", "architecture": "qwen4exp"}
        plain = {"format": "gguf", "architecture": "gemma4"}
        self.assertFalse(r.get("lemonade").can_run(big)[0])
        self.assertTrue(r.get("strata").can_run(big)[0])
        self.assertTrue(r.get("lemonade").can_run(plain)[0])
        self.assertFalse(r.get("strata").can_run(plain)[0])

    def test_wrong_folders_are_skipped_and_explained(self):
        self.add("broken-yaml", "id: [unclosed\n")
        self.add("bad-spec", "id: Bad Id\nname: X\napi_version: 1\n")
        self.add("fine", """
            id: fine
            name: Fine
            api_version: 1
            run: {mode: external}
            endpoint: {base_url: "http://10.0.0.5:9000/v1"}
            """)
        r = self.registry()
        self.assertIn("fine", r.providers)                  # a bad neighbour never takes the good one down
        self.assertEqual(len(r.errors), 2)
        self.assertTrue(all(e["folder"] and e["error"] for e in r.errors))
        self.assertIn("lemonade", r.providers)

    def test_duplicate_id_is_reported(self):
        self.add("lemonade-copy", "id: lemonade\nname: Copy\napi_version: 1\nrun: {mode: external}\nendpoint: {base_url: 'http://x/v1'}\n")
        r = self.registry()
        self.assertTrue(any("already uses the id" in e["error"] for e in r.errors))

    def test_provider_py_must_define_provider(self):
        self.add("nocls", "id: nocls\nname: N\napi_version: 1\nrun: {mode: external}\nendpoint: {base_url: 'http://x/v1'}\n", code="x = 1\n")
        r = self.registry()
        self.assertTrue(any("class Provider" in e["error"] for e in r.errors))


class YamlOnlyProvider(Env):
    """The promise of the design: drop a folder with one YAML file in, and Zest can start the engine and use its models."""
    PORTS = "18970-18979"

    def add_demo(self, **over):
        y = """
            id: demo-openai
            name: Demo OpenAI-style server
            summary: A test engine that is only a YAML file.
            api_version: 1
            detect:
              binaries: [%s]
              ports: %s
              health: {path: /health}
            run:
              mode: managed
              start: ["{bin}", "%s", "{port}"]
              ready: {path: /health, timeout: 20}
              stop_grace_secs: 5
              port_range: %s
            endpoint:
              base_url: "http://127.0.0.1:{port}/v1"
              models: {path: /v1/models, id: "$.data[*].id", context: "$.data[*].meta.n_ctx"}
            models: {formats: [gguf]}
            memory: {group: gpu, exclusive: true}
            """ % (sys.executable, self.PORTS, FIXTURE, self.PORTS)
        return self.add("demo-openai", y)

    def test_needs_approval_before_it_runs_a_program(self):
        self.add_demo()
        r = self.registry()
        p = r.get("demo-openai")
        self.assertFalse(p.trusted)
        self.assertFalse(p.approved)
        c = r.consent(p)
        self.assertIsNotNone(c)
        self.assertTrue(any("start a program" in w for w in c["will"]))
        with self.assertRaises(provbase.ProviderError):
            p.ensure("m1", lambda t: None)

    def test_start_use_and_stop(self):
        self.add_demo()
        r = self.registry()
        p = r.get("demo-openai")
        r.approve("demo-openai")
        self.assertTrue(p.approved)
        self.assertEqual(p.models(), [])                               # nothing running yet
        msgs = []
        ep = p.ensure("m1", msgs.append)
        try:
            self.assertEqual(ep["model"], "m1")
            self.assertEqual(ep["ctx"], 4096)
            self.assertTrue(ep["base_url"].startswith("http://127.0.0.1:1897"))
            self.assertEqual([m["owner"] for m in p.models()], ["zest"])
            self.assertTrue(p.status()["installed"])
            self.assertTrue(msgs)
            # asking again reuses what is running
            self.assertEqual(p.ensure("m1", lambda t: None)["base_url"], ep["base_url"])
        finally:
            ok, msg = p.stop()
        self.assertTrue(ok, msg)
        self.assertEqual(p.models(), [])

    def test_approval_is_tied_to_the_files(self):
        d = self.add_demo()
        r = self.registry()
        r.approve("demo-openai")
        self.assertTrue(self.registry().get("demo-openai").approved)       # remembered
        with open(os.path.join(d, "provider.yaml"), "a") as f:
            f.write("\n# changed\n")
        self.assertFalse(self.registry().get("demo-openai").approved)      # a changed file asks again

    def test_an_engine_that_started_elsewhere_is_never_stopped(self):
        import subprocess
        self.add_demo()
        r = self.registry()
        p = r.get("demo-openai")
        theirs = subprocess.Popen([sys.executable, FIXTURE, "18971"], stdin=subprocess.DEVNULL)
        try:
            for _ in range(40):
                if provbase.port_open(18971):
                    break
                time.sleep(0.1)
            self.assertEqual([m["owner"] for m in p.models()], ["yours"])
            ok, msg = p.stop()
            self.assertFalse(ok)
            self.assertTrue(provbase.port_open(18971))                    # still running
        finally:
            theirs.terminate()
            theirs.wait()

    def test_address_only_engine_needs_no_approval(self):
        self.add("remote", """
            id: remote
            name: Remote
            api_version: 1
            run: {mode: external}
            endpoint: {base_url: "http://10.0.0.5:9000/v1"}
            """)
        r = self.registry()
        self.assertIsNone(r.consent(r.get("remote")))


class EnginesPage(Env):
    """What the Advanced > Engines page is built from."""
    def real(self):
        return registry.Registry(bundled=os.path.join(HERE, "..", "zest", "providers"), user=self.user).load()

    def test_summary_carries_what_each_card_shows(self):
        d = self.real().summary()
        self.assertEqual(d["user_folder"], self.user)
        by = {p["id"]: p for p in d["providers"]}
        self.assertEqual(sorted(by), ["lemonade", "strata"])
        for p in by.values():
            self.assertTrue({"state", "text", "error"} <= set(p["update"]))
            self.assertIn(p["update"]["state"], ("idle", "working", "done", "error", "deferred"))
            self.assertTrue(p["trusted"] and p["consent"] is None)           # the built-in ones never ask

    def test_default_update_status_is_idle(self):
        gp = provbase.GenericProvider({"id": "x", "name": "X", "api_version": 1}, self.tmp, provbase.Context())
        self.assertEqual(gp.update_status(), {"state": "idle", "text": "", "error": None})

    def test_lemonade_reports_its_download_in_the_common_shape(self):
        old = dict(engine.UPD)
        try:
            engine.UPD.update(state="downloading", version="9.9", bytes=3, total=10, error=None)
            u = self.real().get("lemonade").update_status()
            self.assertEqual((u["state"], u["total"]), ("working", 10))
            self.assertIn("9.9", u["text"])
            engine.UPD.update(state="error", error="no network")
            self.assertEqual(self.real().get("lemonade").update_status()["state"], "error")
            engine.UPD.update(state="done", version="9.9", error=None)
            self.assertEqual(self.real().get("lemonade").update_status()["state"], "done")
        finally:
            engine.UPD.clear()
            engine.UPD.update(old)

    def test_strata_reports_its_update_in_the_common_shape(self):
        old = dict(strata.SUPD)
        try:
            strata.SUPD.update(state="deferred", text="Update after you stop it.", error=None)
            u = self.real().get("strata").update_status()
            self.assertEqual((u["state"], u["text"]), ("deferred", "Update after you stop it."))
        finally:
            strata.SUPD.clear()
            strata.SUPD.update(old)

    def test_consent_names_the_program_and_shows_the_real_command(self):
        d = os.path.join(self.user, "demo")
        os.makedirs(d)
        with open(os.path.join(d, "provider.yaml"), "w") as f:
            f.write(textwrap.dedent("""
                id: demo
                name: Demo
                api_version: 1
                detect: {binaries: [%s]}
                run: {mode: managed, start: ["{bin}", serve, "--port", "{port}"], ready: {path: /health}, port_range: 18970-18971}
                endpoint: {base_url: "http://127.0.0.1:{port}/v1"}
                """ % sys.executable))
        r = self.real()
        c = r.consent(r.get("demo"))
        self.assertEqual(c["will"], ["start a program on your computer (%s)" % os.path.basename(sys.executable)])
        self.assertEqual(c["commands"][0]["command"][0], sys.executable)        # the template word {bin} is replaced for the person
        self.assertIn("{port}", c["commands"][0]["command"])                    # values only known at start stay as they are


class Sdk(Env):
    """What someone writing an engine uses: the checker, the template and the llama-server example."""
    GOOD_YAML = """
        id: mine
        name: Mine
        tier: small-medium
        api_version: 1
        detect: {binaries: [%s], ports: 18980-18981, health: {path: /health}}
        run: {mode: managed, start: ["{bin}", serve, "--port", "{port}"], ready: {path: /health}, port_range: 18980-18981}
        endpoint: {base_url: "http://127.0.0.1:{port}/v1"}
        models: {formats: [gguf]}
        memory: {group: gpu, exclusive: true}
        """ % sys.executable

    def levels(self, rep):
        return [(i["level"], i["text"]) for i in rep["items"]]

    def test_a_good_folder_loads_and_says_what_zest_will_ask(self):
        rep = provcheck.check(self.add("mine", self.GOOD_YAML))
        self.assertTrue(rep["ok"], self.levels(rep))
        self.assertEqual((rep["id"], rep["name"]), ("mine", "Mine"))
        self.assertTrue(any("start a program" in t for _, t in self.levels(rep)))
        self.assertIn("Zest will load this engine", provcheck.render(rep))

    def test_a_typo_is_pointed_out_with_the_right_word(self):
        rep = provcheck.check(self.add("mine", self.GOOD_YAML.replace("port_range: 18980-18981}", "port_rnge: 18980-18981}")))
        text = " ".join(t for lv, t in self.levels(rep) if lv == "warn")
        self.assertIn("run.port_rnge", text)
        self.assertIn("did you mean run.port_range", text)

    def test_what_is_wrong_is_an_error_and_exits_nonzero(self):
        d = self.add("mine", self.GOOD_YAML.replace('start: ["{bin}", serve, "--port", "{port}"]', "start: my-engine --port 1"))
        rep = provcheck.check(d)
        self.assertFalse(rep["ok"])
        self.assertTrue(any(lv == "error" and "run.start" in t for lv, t in self.levels(rep)))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(provcheck.main(["check", d]), 1)

    def test_odd_shapes_are_reported_not_crashed_on(self):
        for bad in ("run: hello\n", "models: [gguf]\n", "detect: {binaries: my-engine}\n"):
            rep = provcheck.check(self.add("odd", "id: odd\nname: Odd\napi_version: 1\n" + bad))
            self.assertFalse(rep["ok"], bad)
            self.assertTrue(rep["items"], bad)
        self.assertFalse(provcheck.check(os.path.join(self.tmp, "nowhere"))["ok"])
        self.assertFalse(provcheck.check(self.user)["ok"])                          # a folder with no provider.yaml in it

    def test_an_engine_that_cannot_say_when_it_is_ready_is_refused(self):
        rep = provcheck.check(self.add("mine", self.GOOD_YAML.replace(", ready: {path: /health}", "").replace("health: {path: /health}", "")))
        self.assertTrue(any("run.ready" in t for lv, t in self.levels(rep) if lv == "error"), self.levels(rep))

    def test_serving_files_needs_the_file_word_in_the_command(self):
        yml = self.GOOD_YAML.replace("models: {formats: [gguf]}", "models: {formats: [gguf], serve: file}")
        self.assertTrue(any("model_file" in t for lv, t in self.levels(provcheck.check(self.add("a", yml))) if lv == "error"))
        yml2 = yml.replace('"--port", "{port}"]', '"--port", "{port}", "-m", "{model_file}"]')
        self.assertTrue(provcheck.check(self.add("b", yml2))["ok"])
        self.assertTrue(any("model_file" in t for lv, t in self.levels(provcheck.check(self.add("c", self.GOOD_YAML.replace('"--port", "{port}"]', '"--port", "{port}", "-m", "{model_file}"]')))) if lv == "error"))

    def test_new_makes_a_folder_that_passes_the_check(self):
        d = provcheck.new("my-thing", where=self.user)
        rep = provcheck.check(d)
        self.assertTrue(rep["ok"], self.levels(rep))
        self.assertEqual(rep["id"], "my-thing")
        with self.assertRaises(ValueError):
            provcheck.new("my-thing", where=self.user)                              # never overwrites
        with self.assertRaises(ValueError):
            provcheck.new("Bad Id", where=self.user)

    def test_the_bundled_example_is_a_working_engine_file(self):
        d = provcheck.new("llama-server", where=self.user, example="llama-server")
        rep = provcheck.check(d)
        self.assertTrue(rep["ok"], self.levels(rep))
        self.assertFalse([1 for lv, _ in self.levels(rep) if lv == "warn"], self.levels(rep))        # the example sets an example
        with self.assertRaises(ValueError):
            provcheck.new("x-y", where=self.user, example="nothing")

    def test_a_template_is_never_loaded_as_an_engine(self):
        r = self.registry()
        self.assertEqual(r.errors, [])
        self.assertNotIn("my-engine", [p.id for p in r.all()])

    def test_files_are_served_by_name_and_a_missing_one_is_explained(self):
        yml = self.GOOD_YAML.replace("models: {formats: [gguf]}", "models: {formats: [gguf], serve: file}").replace('"--port", "{port}"]', '"--port", "{port}", "-m", "{model_file}"]')
        self.add("mine", yml)
        p = self.registry().get("mine")
        self.assertIsNone(p.model_id({"stem": "x", "files": [], "complete": True}))
        self.assertIsNone(p.model_id({"stem": "x", "files": ["/m/x-00001-of-00002.gguf"], "complete": False}))
        self.assertEqual(p.model_id({"stem": "x", "files": ["/m/x-00001-of-00002.gguf", "/m/x-00002-of-00002.gguf"], "complete": True}), "x")
        self.assertEqual(p._files["x"], "/m/x-00001-of-00002.gguf")                # a split model starts from its first part
        self.registry().approve("mine")
        p2 = self.registry().get("mine")
        with self.assertRaises(provbase.ProviderError):
            p2.ensure("unknown", lambda t: None)

    def test_an_engine_that_cannot_report_its_work_says_so(self):
        p = self.registry().get("mine") or (self.add("mine", self.GOOD_YAML), self.registry().get("mine"))[1]
        self.assertEqual(p.activity(), (None, None))


if __name__ == "__main__":
    unittest.main()
