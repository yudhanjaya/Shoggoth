"""Shoggoth's own llama.cpp as the default engine: which build runs, what the page is told, the devices, the load log, and the server
routes that replaced Lemonade's. Run: python3 -m unittest discover tests"""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "shoggoth"))

import hardware  # noqa: E402
import llamacpp  # noqa: E402
import servers  # noqa: E402
import store  # noqa: E402
import util  # noqa: E402

FAKE_SERVER = """#!/bin/sh
case "$1" in
  --version) echo "version: 1.0.0-dev (build %s, commit abc)" ;;
  --list-devices) echo "Available devices:"; echo "  CUDA0: NVIDIA GeForce RTX 3080 (9875 MiB, 8703 MiB free)"; echo "  CUDA1: NVIDIA GeForce RTX 4080 SUPER (15941 MiB, 3413 MiB free)" ;;
esac
"""


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR", "SHOGGOTH_LLAMACPP_DIR")}
        os.environ["SHOGGOTH_DATA_DIR"], os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")
        os.environ["SHOGGOTH_LLAMACPP_DIR"] = os.path.join(self.tmp, "bundled")
        util.invalidate()

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        util.invalidate()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, tag, backend, bundled=False):
        d = os.path.join(self.tmp, "bundled" if bundled else os.path.join("data", "llamacpp"), tag, backend)
        os.makedirs(d, exist_ok=True)
        exe = os.path.join(d, "llama-server")
        with open(exe, "w") as f:
            f.write(FAKE_SERVER % tag[1:])
        os.chmod(exe, 0o755)
        return exe


class Builds(Env):
    def test_the_bundled_build_is_found_and_a_download_comes_first(self):
        self.build("b12375", "cpu", bundled=True)
        self.assertEqual([(b["tag"], b["backend"], b["bundled"]) for b in llamacpp.builds()], [("b12375", "cpu", True)])
        self.assertTrue(llamacpp.choose({})["bundled"])
        self.build("b12000", "cuda13")                                 # older, but for the graphics card
        self.assertEqual(llamacpp.choose({})["backend"], "cuda13")
        self.assertEqual(llamacpp.choose({"llamacpp": {"backend": "cpu"}})["backend"], "cpu")
        self.assertEqual(llamacpp.choose({"llamacpp": {"tag": "b12375", "backend": "cpu"}})["tag"], "b12375")

    def test_pruning_never_touches_the_bundled_build(self):
        self.build("b1", "cpu", bundled=True)
        for t in ("b2", "b3", "b4"):
            self.build(t, "cuda13")
        llamacpp._prune()
        self.assertEqual(sorted(b["tag"] for b in llamacpp.builds()), ["b1", "b3", "b4"])

    def test_summary_says_what_the_page_needs(self):
        self.build("b12375", "cuda13")
        with mock.patch.object(llamacpp, "recommended", lambda: "cuda13"):
            s = llamacpp.summary({"llamacpp": {"max_loaded": 3}})
        self.assertEqual((s["installed"], s["tag"], s["backend"], s["max_loaded"], s["recommended"]), (True, "b12375", "cuda13", 3, "cuda13"))
        self.assertFalse(s["running"])
        self.assertEqual(s["builds"], [{"tag": "b12375", "backend": "cuda13", "name": "NVIDIA CUDA 13", "bundled": False}])

    def test_max_loaded_is_kept_sensible(self):
        self.assertEqual([llamacpp.max_loaded({"max_loaded": v}) for v in (None, 0, 2, 99, "x")], [1, 1, 2, 8, 1])

    def test_the_router_gets_the_device_and_the_models_folder(self):
        b = {"server": "/x/llama-server"}
        cmd = llamacpp.command(b, 13340, "k", llamacpp.models_dir(), 2, ["--device", "CUDA0"])
        self.assertEqual(cmd[cmd.index("--models-dir") + 1], os.path.join(self.tmp, "data", "views", "llamacpp"))
        self.assertEqual(cmd[cmd.index("--models-max") + 1], "2")
        self.assertEqual(cmd[-2:], ["--device", "CUDA0"])

    def test_the_official_builds_need_glibc_2_38(self):
        self.assertEqual(llamacpp.NEEDS_GLIBC, (2, 38))


class NewerLinux(Env):
    """A build that won't start on an older Linux says which Linux it needs."""

    GLIBC_ERROR = ("./llama-server: /lib/x86_64-linux-gnu/libc.so.6: version `GLIBC_2.38' not found "
                   "(required by ./libggml-base.so)\n")

    def test_a_glibc_not_found_error_names_2_38_and_the_distros(self):
        with mock.patch.object(llamacpp.util, "run", return_value=(1, self.GLIBC_ERROR)), \
                mock.patch.object(llamacpp, "_glibc", lambda: (2, 31)):
            with self.assertRaises(RuntimeError) as e:
                llamacpp.check_build("/x/llama-server", "b12375")
        msg = str(e.exception)
        for bit in ("needs a newer Linux", "glibc 2.38", "Ubuntu 24.04", "Debian 13", "Fedora 39",
                    "Linux Mint 22", "this computer has 2.31"):
            self.assertIn(bit, msg)

    def test_an_old_glibc_without_a_message_still_says_which_linux(self):
        with mock.patch.object(llamacpp.util, "run", return_value=(1, "Segmentation fault")), \
                mock.patch.object(llamacpp, "_glibc", lambda: (2, 35)):
            with self.assertRaises(RuntimeError) as e:
                llamacpp.check_build("/x/llama-server", "b12375")
        self.assertIn("glibc 2.38", str(e.exception))

    def test_a_build_that_runs_is_accepted(self):
        with mock.patch.object(llamacpp.util, "run", return_value=(0, "build 12375")):
            llamacpp.check_build("/x/llama-server", "b12375")

    def test_some_other_failure_is_not_called_an_old_linux(self):
        with mock.patch.object(llamacpp.util, "run", return_value=(1, "cannot execute: x")), \
                mock.patch.object(llamacpp, "_glibc", lambda: (2, 39)):
            with self.assertRaises(RuntimeError) as e:
                llamacpp.check_build("/x/llama-server", "b12375")
        self.assertIn("won't start", str(e.exception))


class LoadLog(Env):
    def test_what_a_load_took_is_read_from_the_load_log(self):
        with open(os.path.join(HERE, "fixtures", "kvlogs", "qwen3-0.6b-q4_0-q8-8192.log")) as f:
            text = f.read()
        # 83.46 + 319.96 model, 476 cache, 56.01 + 16.02 compute (MiB)
        self.assertAlmostEqual(llamacpp.measured_gb("m", text), round((83.46 + 319.96 + 476 + 56.01 + 16.02) / 1024, 2))

    def test_only_that_models_lines_count(self):
        os.makedirs(llamacpp.root(), exist_ok=True)
        with open(llamacpp.log_path(), "w") as f:
            f.write("[40001] load_tensors:        CUDA0 model buffer size =  1024.00 MiB\n"
                    "[40002] load_tensors:        CUDA0 model buffer size =  9999.00 MiB\n"
                    "[40001] llama_kv_cache: size = 1024.00 MiB (  8192 cells)\n")
        with mock.patch.object(llamacpp, "instance_port", lambda mid: 40001):
            self.assertEqual(llamacpp.measured_gb("m"), 2.0)
        with mock.patch.object(llamacpp, "instance_port", lambda mid: None):
            self.assertIsNone(llamacpp.measured_gb("m"))

    def test_own_ports_include_each_models_process(self):
        llamacpp._state.update(port=13340)
        try:
            with mock.patch.object(llamacpp, "running", lambda: True), \
                    mock.patch.object(llamacpp, "models", lambda: [{"id": "a", "args": ["x", "--port", "41234"], "status": "loaded"},
                                                                   {"id": "b", "args": ["x", "--port", "0"], "status": "unloaded"}]):
                self.assertEqual(llamacpp.own_ports(), {13340, 41234})
                self.assertEqual(llamacpp.loaded_ids(), ["a"])
        finally:
            llamacpp._state.update(port=None)


class Devices(Env):
    def test_devices_come_from_the_build_shoggoth_runs(self):
        self.build("b12375", "cuda13")
        with mock.patch.object(hardware, "all_gpus", lambda: []):
            info = hardware.info("builtin", "5.0")
        self.assertEqual([(d["id"], d["mib"]) for d in info["devices"]], [("cuda0", 9875), ("cuda1", 15941)])
        self.assertEqual((info["llama_tag"], info["needs_backend"], info["backend"]["resolved"]), ("b12375", False, "cuda13"))
        self.assertIn("build 12375", info["llama_version"])

    def test_no_build_means_no_devices_and_a_backend_is_needed(self):
        with mock.patch.object(hardware, "all_gpus", lambda: []):
            info = hardware.info("builtin", "5.0")
        self.assertEqual((info["devices"], info["needs_backend"]), ([], True))

    def test_backends_list_installed_and_downloadable_builds(self):
        self.build("b12375", "vulkan")
        with mock.patch.object(llamacpp, "recommended", lambda: "vulkan"):
            r = hardware.backends()[0]
        self.assertEqual((r["recipe"], r["default"], r["current"]), ("llamacpp", "vulkan", "auto"))
        st = {b["name"]: (b["state"], b["version"]) for b in r["backends"]}
        self.assertEqual(st["vulkan"], ("installed", "b12375"))
        self.assertEqual(st["cuda13"], ("installable", None))
        self.assertEqual([x["recipe"] for x in hardware.backends() if x["recipe"] == "llamacpp"], ["llamacpp"])   # one recipe, no Lemonade
        self.assertEqual(sorted(b["name"] for b in r["backends"]), ["cpu", "cuda12", "cuda13", "rocm", "vulkan"])


class ChatContext(Env):
    def write(self, chat):
        os.makedirs(store.config_dir(), exist_ok=True)
        with open(os.path.join(store.config_dir(), "settings.json"), "w") as f:
            json.dump({"chat": chat}, f)

    def test_the_default_is_auto(self):
        self.assertEqual(store.load()["chat"]["ctx"], "auto")

    def test_the_old_default_becomes_auto_but_a_choice_stays(self):
        self.write({"ctx": 131072})
        self.assertEqual(store.load()["chat"]["ctx"], "auto")
        self.write({"ctx": 131072, "ctx_set": True})
        self.assertEqual(store.load()["chat"]["ctx"], 131072)
        self.write({"ctx": 32768})
        self.assertEqual(store.load()["chat"]["ctx"], 32768)

    def test_set_chat_ctx_takes_auto_or_a_number(self):
        import agent
        with mock.patch.object(agent, "restart_idle", lambda: None):
            self.assertEqual(agent.set_chat_ctx(16384), 16384)
            self.assertEqual(agent.chat_ctx_setting(), 16384)
            self.assertTrue(store.load()["chat"]["ctx_set"])
            self.assertEqual(agent.set_chat_ctx("auto"), "auto")
            self.assertEqual(agent.chat_ctx(), agent.DEFAULT_CTX)
        with mock.patch.object(agent, "_auto_ctx", lambda m: 65536), mock.patch.object(agent.router, "fixed_ctx", lambda m: False), \
                mock.patch.object(agent.router, "max_ctx", lambda m: 262144):
            self.assertEqual(agent.want_ctx("m"), 65536)         # auto: the window the library picked for this model


class Connected(Env):
    def test_gemini_uses_its_own_base_and_model_list(self):
        self.assertEqual(servers.api_base("https://generativelanguage.googleapis.com/v1beta/openai"),
                         "https://generativelanguage.googleapis.com/v1beta/openai")
        self.assertEqual(servers.api_base("http://127.0.0.1:11434"), "http://127.0.0.1:11434/v1")
        listing = {"models": [{"name": "models/gemini-3-flash", "supportedGenerationMethods": ["generateContent"], "inputTokenLimit": 1048576},
                              {"name": "models/text-embedding-005", "supportedGenerationMethods": ["embedContent"]}]}

        class R:
            status = 200

            def read(self):
                return json.dumps(listing).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        seen = {}

        def opener(req, timeout=0):
            seen["url"], seen["key"] = req.full_url, req.get_header("X-goog-api-key")
            return R()
        with mock.patch.object(servers.urllib.request, "urlopen", opener):
            st, ms = servers.list_models("https://generativelanguage.googleapis.com/v1beta/openai", "gk")
        self.assertEqual((st, ms), (200, [{"id": "gemini-3-flash", "ctx": 1048576}]))
        self.assertTrue(seen["url"].startswith("https://generativelanguage.googleapis.com/v1beta/models"))
        self.assertEqual(seen["key"], "gk")
        p = servers.ConnectedServer({"id": "gemini", "url": "https://generativelanguage.googleapis.com/v1beta/openai", "preset": "gemini"},
                                    __import__("provbase").Context(settings=lambda pid: {}))
        self.assertEqual(p.spec["endpoint"]["base_url"], "https://generativelanguage.googleapis.com/v1beta/openai")

    def test_discover_never_contacts_hosted_services(self):
        asked = []
        pre = [{"id": "openrouter", "name": "OpenRouter", "url": "https://openrouter.ai/api", "category": "hosted"},
               {"id": "ollama", "name": "Ollama", "url": "http://127.0.0.1:9", "category": "local"},
               {"id": "other", "name": "Another server", "url": ""}]
        with mock.patch.object(servers, "presets", lambda: pre), \
                mock.patch.object(servers, "probe", lambda u, k="", pin=None: asked.append(u) or {"reachable": False, "needs_key": False, "models": []}):
            servers.discover([])
        self.assertEqual(asked, ["http://127.0.0.1:9"])

    def test_only_a_local_server_with_models_is_connected_unasked(self):
        found = [{"url": "http://127.0.0.1:1234", "preset": "other", "name": "A", "reachable": True, "needs_key": False, "models": [], "already": False},
                 {"url": "http://8.8.8.8:80", "preset": "other", "name": "B", "reachable": True, "needs_key": False, "models": ["m"], "already": False}]
        with mock.patch.object(servers, "presets", lambda: [{"id": "other", "name": "Another server", "url": ""}]):
            res = servers.connect_found(found)
        self.assertEqual((res["connected"], servers.all()), ([], []))
        self.assertTrue(servers.is_local("http://192.168.1.4:8080") and servers.is_local("http://localhost:1"))
        self.assertFalse(servers.is_local("https://openrouter.ai"))


class Routes(Env):
    """The routes that used to reach into Lemonade, now about Shoggoth's own llama.cpp."""
    def setUp(self):
        super().setUp()
        import server
        import uiauth
        self.server = server
        os.environ.pop("SHOGGOTH_AUTH", None)
        self.srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.token = uiauth.token()
        self.host = "127.0.0.1:%d" % self.port

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def call(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": self.host, "Authorization": "Bearer " + self.token} if headers is None else dict(headers, Host=self.host)
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = c.getresponse()
        return r.status, r.read()

    def test_sources_start_with_shoggoths_own_llamacpp(self):
        self.build("b12375", "cuda13")
        st, raw = self.call("GET", "/ui/sources")
        me = json.loads(raw)["sources"][0]
        self.assertEqual((st, me["id"], me["kind"], me["own"], me["version"], me["state"]), (200, "llamacpp", "builtin", True, "b12375", "starting"))

    def test_config_routes_are_llamacpps_settings(self):
        st, raw = self.call("POST", "/ui/config", {"max_loaded": 3})
        self.assertEqual((st, json.loads(raw)["config"]["max_loaded"]), (200, 3))
        st, raw = self.call("GET", "/ui/config")
        self.assertEqual(json.loads(raw)["engine"]["max_loaded"], 3)
        self.assertEqual(self.call("POST", "/ui/config", {"max_loaded": "many"})[0], 400)
        st, raw = self.call("POST", "/ui/device", {"device": "cuda1"})
        self.assertEqual((st, store.load()["llamacpp"]["device"]), (200, "CUDA1"))
        self.assertEqual(self.call("POST", "/ui/backend", {"backend": "rocm"})[0], 400)          # not installed

    def test_setup_state_names_the_engine(self):
        st, raw = self.call("GET", "/ui/setup")
        d = json.loads(raw)
        self.assertEqual((st, d["mode"], d["engine"]["installed"]), (200, "builtin", False))
        self.assertEqual(json.loads(self.call("POST", "/ui/setup/mode", {"mode": "connected"})[1])["mode"], "connected")
        self.assertEqual(self.call("POST", "/ui/setup/mode", {"mode": "existing-lemonade"})[0], 400)

    def test_choosing_a_paired_shoggoth_skips_the_engine_download(self):
        store.update(lambda s: s["servers"].append({"id": "desk", "preset": "shoggoth", "name": "desk-pc", "url": "https://192.168.1.5:13308",
                                                    "key": "shoggoth-peer-x", "fp": "ab" * 32}))
        st, raw = self.call("POST", "/ui/setup/mode", {"mode": "connected"})
        self.assertEqual((st, json.loads(raw)["client_only"]), (200, True))
        self.assertTrue(store.load()["client_only"])
        self.assertTrue(json.loads(self.call("GET", "/ui/setup")[1])["client_only"])
        st, raw = self.call("POST", "/ui/setup/mode", {"mode": "builtin"})            # "also run models on this computer"
        self.assertEqual((st, json.loads(raw)["client_only"]), (200, False))

    def test_another_kind_of_server_does_not_skip_the_engine_download(self):
        store.update(lambda s: s["servers"].append({"id": "box", "preset": "ollama", "name": "box", "url": "http://192.168.1.6:11434",
                                                    "key": "shoggoth-peer-x"}))
        st, raw = self.call("POST", "/ui/setup/mode", {"mode": "connected"})
        self.assertEqual((st, json.loads(raw)["client_only"]), (200, False))

    def test_api_forwards_only_the_openai_style_paths(self):
        st, raw = self.call("GET", "/api/v1/system-info")
        self.assertEqual(st, 404)
        st, raw = self.call("POST", "/api/internal/set", {"x": 1})
        self.assertEqual(st, 404)

    def test_api_takes_the_key_in_anthropic_and_google_headers(self):
        for h in ({"x-api-key": self.token}, {"x-goog-api-key": self.token}):
            self.assertEqual(self.call("GET", "/api/v1/system-info", headers=h)[0], 404)     # let in (then not found), not 401
        self.assertEqual(self.call("GET", "/api/v1/models", headers={"x-api-key": "wrong"})[0], 401)
        self.assertEqual(self.call("GET", "/ui/setup", headers={"x-api-key": self.token})[0], 401)   # only the /api door reads them

    def test_tuning_takes_llamacpp(self):
        self.assertEqual(self.server.Handler._tune_args({"engine": "llamacpp", "target": "m"})[:2], ("llamacpp", "m"))
        with self.assertRaises(ValueError):
            self.server.Handler._tune_args({"engine": "lemonade", "target": "m"})


class ChildProcessMemory(Env):
    """S2: what a load really took, measured on the model's own process (nvidia-smi and /proc), not the router's quiet log."""

    def setUp(self):
        super().setUp()
        self._old = {k: llamacpp._state.get(k) for k in ("pid", "port")}
        self.addCleanup(lambda: llamacpp._state.update(self._old))

    def smi(self, text=None):
        """A fake nvidia-smi on PATH printing `text`; None leaves it out (an AMD card or a processor-only computer)."""
        d = os.path.join(self.tmp, "bin")
        os.makedirs(d, exist_ok=True)
        if text is not None:
            with open(os.path.join(d, "nvidia-smi"), "w") as f:
                f.write("#!/bin/sh\n" + "".join("printf '%%s\\n' %r\n" % line for line in text.splitlines()))
            os.chmod(os.path.join(d, "nvidia-smi"), 0o755)
        elif os.path.exists(os.path.join(d, "nvidia-smi")):
            os.remove(os.path.join(d, "nvidia-smi"))
        p = mock.patch.dict(os.environ, {"PATH": d})
        p.start()
        self.addCleanup(p.stop)

    def proc(self, router=1000, kids=None, ports=None, rss_kb=1048576, children_file=True):
        """A fake /proc: the router's children list and each child's command line and VmRSS."""
        kids = list(kids or [4242])
        ports = ports or {p: 41234 for p in kids}
        root = os.path.join(self.tmp, "proc")
        if children_file:
            task = os.path.join(root, str(router), "task", str(router))
            os.makedirs(task, exist_ok=True)
            with open(os.path.join(task, "children"), "w") as f:
                f.write(" ".join(str(p) for p in kids))
        for pid, port in ports.items():
            d = os.path.join(root, str(pid))
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "cmdline"), "wb") as f:
                f.write(("llama-server\0--port\0%d\0" % port).encode())
            if rss_kb is not None:
                with open(os.path.join(d, "status"), "w") as f:
                    f.write("Name:\tllama-server\nVmRSS:\t%d kB\n" % rss_kb)
            elif os.path.exists(os.path.join(d, "status")):
                os.remove(os.path.join(d, "status"))
        llamacpp._state["pid"] = router
        return root

    def model(self, args):
        p = mock.patch.object(llamacpp, "models", lambda: [{"id": "m", "status": "loaded", "args": args, "ctx": 8192}])
        p.start()
        self.addCleanup(p.stop)

    def test_the_models_own_gpu_memory_is_summed_over_its_cards(self):
        self.smi("4242, 3072\n4242, 512\n9999, 1024")
        root = self.proc()
        self.model(["llama-server", "--port", "41234"])
        self.assertEqual(llamacpp.measured_process_gb("m", proc_root=root), 3.5)

    def test_processor_memory_is_added_when_experts_stay_there(self):
        self.smi("4242, 3072")
        root = self.proc(rss_kb=1048576)                     # 1 GB of processor memory
        self.model(["llama-server", "--port", "41234", "--n-cpu-moe", "24"])
        self.assertEqual(llamacpp.measured_process_gb("m", proc_root=root), 4.0)

    def test_a_model_on_the_processor_is_its_memory(self):
        self.smi("9999, 1024")                               # nvidia-smi is here, but this process holds no card
        root = self.proc(rss_kb=2097152)
        self.model(["llama-server", "--port", "41234", "--device", "none"])
        self.assertEqual(llamacpp.measured_process_gb("m", proc_root=root), 2.0)

    def test_no_nvidia_smi_uses_processor_memory(self):
        self.smi(None)
        root = self.proc(rss_kb=3145728)
        self.model(["llama-server", "--port", "41234"])
        self.assertEqual(llamacpp.measured_process_gb("m", proc_root=root), 3.0)

    def test_a_child_on_another_port_is_not_this_model(self):
        self.smi("4243, 1024")
        root = self.proc(kids=[4242, 4243], ports={4242: 40001, 4243: 41234})
        self.model(["llama-server", "--port", "41234"])
        self.assertEqual(llamacpp.measured_process_gb("m", proc_root=root), 1.0)

    def test_children_are_found_by_parent_when_the_kernel_lists_are_missing(self):
        self.smi("4242, 2048")
        root = self.proc(children_file=False)
        for pid, ppid in ((1000, 1), (4242, 1000)):
            os.makedirs(os.path.join(root, str(pid)), exist_ok=True)
            with open(os.path.join(root, str(pid), "stat"), "w") as f:
                f.write("%d (llama-server) S %d 0 0 0\n" % (pid, ppid))
        self.model(["llama-server", "--port", "41234"])
        self.assertEqual(llamacpp.measured_process_gb("m", proc_root=root), 2.0)

    def test_unknown_records_nothing(self):
        self.smi("4242, 1024")
        root = self.proc()
        with mock.patch.object(llamacpp, "_state", dict(llamacpp._state, pid=None)):
            self.model(["llama-server", "--port", "41234"])
            self.assertIsNone(llamacpp.measured_process_gb("m", proc_root=root))
        self.smi(None)
        root = self.proc(rss_kb=None)                        # no VmRSS and no nvidia-smi
        self.model(["llama-server", "--port", "41234"])
        self.assertIsNone(llamacpp.measured_process_gb("m", proc_root=root))


class ProviderRecordsMeasured(Env):
    """S2: the provider records the measurement once the load succeeds, at the context it was loaded with."""

    def provider(self):
        import importlib.util
        import provbase
        path = os.path.join(HERE, "..", "shoggoth", "providers", "llamacpp", "provider.py")
        spec = importlib.util.spec_from_file_location("s2_provider_llamacpp", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.Provider({"id": "llamacpp", "name": "llama.cpp", "api_version": 1}, self.tmp, provbase.Context())

    def load(self, log_gb, process_gb, seen):
        import library
        with mock.patch.object(llamacpp, "running", lambda: True), \
                mock.patch.object(llamacpp, "endpoint", lambda: {"base_url": "http://127.0.0.1:1/v1", "key": "k"}), \
                mock.patch.object(llamacpp, "models", lambda: [{"id": "m", "status": "loaded", "args": ["--port", "41234"], "ctx": 8192}]), \
                mock.patch.object(llamacpp, "measured_gb", lambda mid: log_gb), \
                mock.patch.object(llamacpp, "measured_process_gb", lambda mid: process_gb), \
                mock.patch.object(llamacpp, "reload", lambda: True), \
                mock.patch.object(llamacpp, "unload", lambda mid: (True, "")), \
                mock.patch.object(llamacpp, "load", lambda mid: (True, "")), \
                mock.patch.object(library, "record_measured", lambda mid, gb, ctx=None: seen.append((mid, gb, ctx))):
            return self.provider().ensure("m", lambda t: None, {"force": True})

    def test_the_process_measurement_is_recorded(self):
        seen = []
        self.load(None, 4.25, seen)
        self.assertEqual(seen, [("m", 4.25, 8192)])

    def test_the_load_log_wins_when_it_has_the_buffers(self):
        seen = []
        self.load(2.0, 9.9, seen)
        self.assertEqual(seen, [("m", 2.0, 8192)])

    def test_nothing_is_recorded_when_it_cannot_be_measured(self):
        seen = []
        self.assertIsNotNone(self.load(None, None, seen))
        self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()
