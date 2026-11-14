"""Finding the inference servers that are already running. Run: python3 -m unittest discover tests"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "shoggoth"))

import engines  # noqa: E402
import scan  # noqa: E402
import servers  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "proc")


def defn(**kw):
    d, errs = engines.clean(dict({"id": "x", "name": "X"}, **kw))
    assert d, errs
    return d


def serve(routes):
    """A local server. routes: {path: (status, json, headers)}. Returns (port, stop)."""
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            status, body, head = routes.get(self.path.split("?")[0], (404, {}, {}))
            raw = json.dumps(body).encode()
            self.send_response(status)
            for k, v in head.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1], lambda: (srv.shutdown(), srv.server_close())


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR")}
        os.environ["SHOGGOTH_DATA_DIR"], os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")
        scan._last.update(at=0.0, found=[])

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def proc_for(self, port, comm="", cmd="", pid=777):
        """A fake /proc where one process (or none, when comm is empty) listens on `port` on loopback."""
        root = tempfile.mkdtemp(dir=self.tmp)
        os.makedirs(os.path.join(root, "net"))
        row = "   0: 0100007F:%04X 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 9090 1 0 100 0 0 10 0\n" % port
        with open(os.path.join(root, "net", "tcp"), "w") as f:
            f.write("  sl  local_address rem_address   st\n" + row)
        if comm:
            os.makedirs(os.path.join(root, str(pid), "fd"))
            os.symlink("socket:[9090]", os.path.join(root, str(pid), "fd", "3"))
            with open(os.path.join(root, str(pid), "comm"), "w") as f:
                f.write(comm + "\n")
            with open(os.path.join(root, str(pid), "cmdline"), "wb") as f:
                f.write(cmd.replace(" ", "\0").encode() + b"\0")
        return root


class ReadingProc(unittest.TestCase):
    def test_only_listen_rows_count_and_ports_are_hex(self):
        rows = scan.parse_tcp(open(os.path.join(FIXTURE, "net", "tcp")).read())
        self.assertEqual(sorted(r[1] for r in rows), [8080, 8081, 11434])         # the ESTABLISHED row (state 01) on 50000 is left out
        self.assertEqual(str(rows[0][0]), "127.0.0.1")

    def test_v6_rows(self):
        rows = scan.parse_tcp(open(os.path.join(FIXTURE, "net", "tcp6")).read())
        self.assertEqual([(str(r[0]), r[1]) for r in rows], [("::1", 5000), ("::", 8080)])

    def test_inodes_map_to_processes(self):
        got = scan.owners(FIXTURE, {11111, 22222, 44444})
        self.assertEqual(got[11111], (4242, "strata", "/home/u/.local/bin/strata serve"))
        self.assertEqual(got[22222][:2], (4343, "llama-server"))
        self.assertNotIn(44444, got)                                               # nobody we can read owns it

    def test_listening_joins_ports_and_processes(self):
        by = {r["port"]: r for r in scan.listening(FIXTURE)}
        self.assertEqual(sorted(by), [5000, 8080, 8081, 11434])
        self.assertEqual((by[8080]["process"], by[8080]["pid"]), ("strata", 4242))       # the v6 row for 8080 doesn't hide the process
        self.assertEqual(by[8081]["process"], "llama-server")
        self.assertEqual((by[11434]["process"], by[11434]["pid"]), ("", 0))              # unreadable: still listed, unnamed
        self.assertEqual(by[5000]["host"], "[::1]")

    def test_missing_proc_is_empty_not_an_error(self):
        self.assertEqual(scan.listening(os.path.join(FIXTURE, "nope")), [])


class Identifying(unittest.TestCase):
    """The order: process name, then a signature, then the category the candidates share, then 'OpenAI-style server'."""
    DEFS = [
        defn(id="vllm", name="vLLM", ports=[8000], match=["vllm"], identify=[{"path": "/version"}]),
        defn(id="llama-server", name="llama.cpp server", ports=[8080], match=["llama-server"], identify=[{"path": "/props", "json": "$.default_generation_settings"}]),
        defn(id="strata", name="Strata", category="specialized", ports=[8080], match=["strata"]),
        defn(id="maya", name="Maya", category="specialized", ports=[8080], match=["maya"]),
    ]

    @staticmethod
    def answers(**paths):
        return lambda p: paths.get(p.lstrip("/"), (404, {}, None))

    def test_process_name_beats_a_signature(self):
        everything = lambda p: (200, {}, {"default_generation_settings": {}})      # the reply says llama.cpp, the process says Strata
        r = scan.identify(self.DEFS, 8080, "strata", "/x/strata serve", everything)
        self.assertEqual((r["engine"], r["how"]), ("strata", "process"))

    def test_a_signature_beats_the_fallback(self):
        r = scan.identify(self.DEFS, 8080, "", "", self.answers(props=(200, {}, {"default_generation_settings": {"n_ctx": 1}})))
        self.assertEqual((r["engine"], r["how"]), ("llama-server", "signature"))

    def test_a_signature_names_an_engine_on_any_port(self):
        r = scan.identify(self.DEFS, 31337, "docker-proxy", "", self.answers(version=(200, {}, {"version": "1"})))
        self.assertEqual((r["engine"], r["how"]), ("vllm", "signature"))

    def test_two_engines_told_apart_by_process_name(self):
        a = scan.identify(self.DEFS, 8080, "strata", "", self.answers())
        b = scan.identify(self.DEFS, 8081, "llama-server", "", self.answers())
        self.assertEqual((a["engine"], b["engine"]), ("strata", "llama-server"))

    def test_the_command_counts_when_the_process_name_is_python(self):
        defs = [defn(id="sglang", name="SGLang", match=["sglang.launch_server"])]
        r = scan.identify(defs, 30000, "python3", "python3 -m sglang.launch_server", self.answers())
        self.assertEqual(r["engine"], "sglang")

    def test_unidentifiable_8080_is_the_specialized_category(self):
        r = scan.identify(self.DEFS, 8080, "", "", self.answers())                  # llama.cpp's signature failed, so it's ruled out
        self.assertEqual((r["engine"], r["how"], r["category"], r["name"]), (None, "category", "specialized", "Specialized inference engine"))
        self.assertEqual([c["id"] for c in r["candidates"]], ["strata", "maya"])

    def test_mixed_categories_fall_back_to_the_generic_name(self):
        defs = [defn(id="a", name="A", ports=[9000]), defn(id="b", name="B", category="specialized", ports=[9000])]
        r = scan.identify(defs, 9000, "", "", self.answers())
        self.assertEqual((r["engine"], r["how"], r["name"]), (None, "generic", "OpenAI-style server on port 9000"))

    def test_one_candidate_with_no_signature_is_named_by_its_port(self):
        defs = [defn(id="solo", name="Solo", ports=[9100])]
        self.assertEqual(scan.identify(defs, 9100, "", "", self.answers())["how"], "port")

    def test_a_header_signature(self):
        defs = [defn(id="h", name="H", identify=[{"path": "/", "header": "Server", "contains": "hserve"}])]
        r = scan.identify(defs, 9, "", "", lambda p: (200, {"server": "HServe/1.0"}, None))
        self.assertEqual(r["engine"], "h")

    def test_bundled_definitions_do_not_file_an_unknown_8080_as_specialized(self):
        """llamafile, RamaLama and TGI are local engines on 8080 with no signature, so they share it with Strata and Maya: no one category fits."""
        r = scan.identify(engines.definitions(), 8080, "", "", self.answers())
        self.assertEqual((r["category"], r["how"]), (None, "generic"))
        on_8080 = {d["id"] for d in engines.definitions() if 8080 in d["ports"]}
        self.assertTrue({"strata", "maya", "llamafile", "ramalama", "tgi"} <= on_8080)


class Scanning(Env):
    def test_finds_a_server_by_process_name_and_reads_its_models(self):
        port, stop = serve({"/v1/models": (200, {"data": [{"id": "m1", "meta": {"n_ctx": 4096}}, {"id": "m2"}]}, {})})
        try:
            defs = [defn(id="strata", name="Strata", category="specialized", match=["strata"])]
            found = scan.scan(proc=self.proc_for(port, "strata", "/b/strata serve"), defs=defs)
        finally:
            stop()
        self.assertEqual(len(found), 1)
        f = found[0]
        self.assertEqual((f["port"], f["engine"], f["how"], f["preset"], f["url"]), (port, "strata", "process", "strata", "http://127.0.0.1:%d" % port))
        self.assertEqual([(m["id"], m["ctx"]) for m in f["models"]], [("m1", 4096), ("m2", 0)])

    def test_two_servers_on_two_ports_are_each_named(self):
        pa, sa = serve({"/v1/models": (200, {"data": [{"id": "a"}]}, {})})
        pb, sb = serve({"/v1/models": (200, {"data": [{"id": "b"}]}, {})})
        root = os.path.join(self.tmp, "two")
        os.makedirs(os.path.join(root, "net"))
        with open(os.path.join(root, "net", "tcp"), "w") as f:
            f.write("h\n")
            for i, p in enumerate((pa, pb)):
                f.write("   %d: 0100007F:%04X 00000000:0000 0A 0:0 0:0 0 1000 0 %d 1\n" % (i, p, 500 + i))
        for pid, comm, inode in ((11, "strata", 500), (12, "llama-server", 501)):
            os.makedirs(os.path.join(root, str(pid), "fd"))
            os.symlink("socket:[%d]" % inode, os.path.join(root, str(pid), "fd", "3"))
            open(os.path.join(root, str(pid), "comm"), "w").write(comm)
            open(os.path.join(root, str(pid), "cmdline"), "wb").write(comm.encode() + b"\0")
        try:
            defs = [defn(id="strata", name="Strata", category="specialized", match=["strata"]), defn(id="llama-server", name="llama.cpp", match=["llama-server"])]
            found = scan.scan(proc=root, defs=defs)
        finally:
            sa(), sb()
        self.assertEqual({f["port"]: f["engine"] for f in found}, {pa: "strata", pb: "llama-server"})

    def test_an_unidentifiable_server_is_the_specialized_category(self):
        port, stop = serve({"/v1/models": (200, {"data": [{"id": "big"}]}, {})})
        try:
            defs = [defn(id="strata", name="Strata", category="specialized", ports=[port]), defn(id="maya", name="Maya", category="specialized", ports=[port]),
                    defn(id="llama-server", name="llama.cpp", ports=[port], identify=[{"path": "/props", "json": "$.default_generation_settings"}])]
            found = scan.scan(proc=self.proc_for(port), defs=defs)
        finally:
            stop()
        f = found[0]
        self.assertEqual((f["engine"], f["category"], f["name"], f["preset"]), (None, "specialized", "Specialized inference engine", "other"))
        self.assertEqual([c["id"] for c in f["candidates"]], ["strata", "maya"])
        self.assertIn("Strata", f["explain"])

    def test_a_server_nothing_names_is_an_openai_style_server(self):
        port, stop = serve({"/v1/models": (200, {"data": []}, {})})
        try:
            f = scan.scan(proc=self.proc_for(port), defs=[])[0]
        finally:
            stop()
        self.assertEqual(f["name"], "OpenAI-style server on port %d" % port)

    def test_context_comes_from_the_definitions_rule(self):
        port, stop = serve({"/v1/models": (200, {"data": [{"id": "m"}]}, {}), "/props": (200, {"default_generation_settings": {"n_ctx": 32768}}, {})})
        try:
            defs = [defn(id="llama-server", name="llama.cpp", match=["llama-server"], context={"path": "/props", "json": "$.default_generation_settings.n_ctx"})]
            f = scan.scan(proc=self.proc_for(port, "llama-server", "llama-server"), defs=defs)[0]
        finally:
            stop()
        self.assertEqual((f["context"], f["models"][0]["ctx"]), (32768, 32768))

    def test_a_server_that_wants_a_key_is_still_listed(self):
        port, stop = serve({"/v1/models": (401, {}, {})})
        try:
            f = scan.scan(proc=self.proc_for(port), defs=[])[0]
        finally:
            stop()
        self.assertTrue(f["needs_key"])

    def test_ports_that_are_not_inference_servers_are_left_out(self):
        web, s1 = serve({"/": (200, {}, {})})                                  # a web server: /v1/models is a 404
        try:
            self.assertEqual(scan.scan(proc=self.proc_for(web), defs=[]), [])
        finally:
            s1()
        self.assertEqual(scan.scan(proc=self.proc_for(1), defs=[], timeout=0.3), [])         # nothing answers at all

    def test_shoggoths_own_ports_and_process_are_skipped(self):
        port, stop = serve({"/v1/models": (200, {"data": []}, {})})
        try:
            self.assertEqual(scan.scan(proc=self.proc_for(port), defs=[], own_ports=[port]), [])
            self.assertEqual(scan.scan(proc=self.proc_for(port, "python3", "python3", pid=os.getpid()), defs=[]), [])
        finally:
            stop()


class Sources(Env):
    def test_connected_and_found_are_kept_apart(self):
        a, sa = serve({"/v1/models": (200, {"data": [{"id": "m1"}]}, {})})
        b, sb = serve({"/v1/models": (200, {"data": [{"id": "m2"}, {"id": "m3"}]}, {})})
        try:
            servers.add("other", "http://127.0.0.1:%d" % a)
            d = scan.sources(proc=self.proc_for(a), defs=[], fresh=True)
            self.assertEqual([(s["id"], s["state"], s["models"], s["kind"], s["leaves_computer"]) for s in d["sources"]], [("other", "ready", 1, "engine", False)])
            self.assertEqual(d["found"], [])                                   # already connected, so not offered again
            d = scan.sources(proc=self.proc_for(b), defs=[], fresh=True)
            self.assertEqual([f["port"] for f in d["found"]], [b])
        finally:
            sa(), sb()

    def test_an_offline_server_keeps_its_place(self):
        servers.add("other", "http://127.0.0.1:1")
        d = scan.sources(proc=self.proc_for(2), defs=[], fresh=True)
        self.assertEqual([(s["id"], s["state"]) for s in d["sources"]], [("other", "offline")])

    def test_a_scan_is_reused_until_asked_again(self):
        port, stop = serve({"/v1/models": (200, {"data": []}, {})})
        try:
            root = self.proc_for(port)
            self.assertEqual(len(scan.sources(proc=root, defs=[], fresh=True, include_other=True)["found"]), 1)
            self.assertEqual(len(scan.sources(proc=root, defs=[], fresh=True)["found"]), 0)     # unnamed and empty: not offered
        finally:
            stop()
        self.assertEqual(len(scan.sources(proc=root, defs=[], include_other=True)["found"]), 1)  # server gone, same scan within 20 s
        self.assertEqual(len(scan.sources(proc=root, defs=[], fresh=True, include_other=True)["found"]), 0)


class RealServers(Env):
    """Lists seen on the developer's machine."""
    def test_voicebox_is_speech(self):
        names = ["tts-1", "tts-1-hd", "gpt-4o-mini-tts", "qwen", "qwen_custom_voice", "luxtts", "chatterbox", "tada", "kokoro", "omnivoice"]
        items = [{"id": n, "object": "model", "owned_by": "voicebox", "description": d} for n, d in zip(names,
                 ["Alias", "Alias", "Alias", "Qwen TTS", "Qwen CustomVoice", "LuxTTS", "Chatterbox TTS", "TADA", "Kokoro", "OmniVoice"])]
        self.assertEqual(scan.classify(items), (0, "speech"))

    def test_a_bare_401_from_an_unnamed_app_is_not_offered(self):
        # hex (a speech app) answers 401 with an empty body; a real engine that wants a key says why
        for body, offered in (({}, False), ({"error": {"message": "Invalid API key", "type": "invalid_request_error"}}, True)):
            port, stop = serve({"/v1/models": (401, body, {})})
            try:
                got = scan.sources(proc=self.proc_for(port), defs=[], fresh=True)["found"]
            finally:
                stop()
            self.assertEqual(bool(got), offered)
            if offered:
                self.assertTrue(got[0]["needs_key"])

    def test_plain_chat_names_stay_chat(self):
        self.assertEqual(scan.classify([{"id": "Qwen3-0.6B-Q4_0"}, {"id": "gemma-4-12b-it"}]), (2, "chat"))


class Roles(Env):
    def found(self, models, comm="", cmd="", defs=None, **kw):
        port, stop = serve({"/v1/models": (200, {"data": models}, {})})
        try:
            return port, scan.sources(proc=self.proc_for(port, comm, cmd), defs=[] if defs is None else defs, fresh=True, **kw)["found"]
        finally:
            stop()

    def test_a_tts_only_server_is_left_out_unless_asked_for(self):
        tts = [{"id": "kokoro-82m", "object": "model"}, {"id": "voice-1", "architecture": {"output_modalities": ["audio"]}}]
        self.assertEqual(self.found(tts)[1], [])
        self.scan_again()
        port, got = self.found(tts, include_other=True)
        self.assertEqual([(f["port"], f["role"], f["chat_models"]) for f in got], [(port, "speech", 0)])

    def scan_again(self):
        scan._last.update(at=0.0, found=[])

    def test_embedding_and_speech_to_text_roles(self):
        self.assertEqual(scan.classify([{"id": "bge-m3"}, {"id": "nomic-embed-text"}]), (0, "embeddings"))
        self.assertEqual(scan.classify([{"id": "parakeet-tdt-0.6b"}]), (0, "speech"))
        self.assertEqual(scan.classify([{"id": "x", "capabilities": ["transcription"]}]), (0, "speech"))
        self.assertEqual(scan.classify([{"id": "x", "capabilities": {"embedding": True, "chat": False}}]), (0, "embeddings"))
        self.assertEqual(scan.classify([{"id": "x", "architecture": {"output_modalities": ["embedding"]}}]), (0, "embeddings"))
        self.assertEqual(scan.classify([{"id": "whisper-chat", "capabilities": ["chat"]}]), (1, "chat"))       # explicit evidence wins

    def test_a_mixed_list_counts_only_the_chat_models(self):
        port, got = self.found([{"id": "qwen3-8b"}, {"id": "whisper-large-v3"}, {"id": "bge-small-en"}, {"id": "llama-3.1-8b"}])
        self.assertEqual([(f["role"], f["chat_models"], len(f["models"])) for f in got], [("chat", 2, 4)])

    def test_a_named_server_with_no_models_stays(self):
        d = defn(id="llamacpp", name="llama.cpp", match=["llama-server"])
        port, got = self.found([], comm="llama-server", cmd="llama-server --port 1", defs=[d])
        self.assertEqual([(f["engine"], f["role"], f["chat_models"]) for f in got], [("llamacpp", "chat", 0)])

    def test_unknown_model_names_count_as_chat(self):
        self.assertEqual(scan.classify([{"id": "zorblax-9"}, {"id": "mystery", "object": "model", "owned_by": "someone"}]), (2, "chat"))
        port, got = self.found([{"id": "zorblax-9"}])
        self.assertEqual([(f["role"], f["chat_models"]) for f in got], [("chat", 1)])


if __name__ == "__main__":
    unittest.main()
