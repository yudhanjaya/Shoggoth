"""Read aloud: picking Piper's build, its voice checksums, and a fake piper behind the real code.

Bark was tried and dropped: bark.cpp builds, but it publishes no Linux build and its weights need PyTorch to convert,
so Shoggoth can't install it. Piper's own Linux builds are used instead.
"""
import io
import json
import os
import stat
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shoggoth"))
import tts  # noqa: E402

# A piper that reads one JSON line per request and writes a small WAV to the path it was given, like the real one.
FAKE = '''#!/usr/bin/env python3
import json, struct, sys
a = sys.argv
out = a[a.index("-m") + 1] if "-m" in a else "?"
open(sys.argv[0] + ".args", "w").write(" ".join(a[1:]))
if "--version" in a:
    print("1.2.0")
    sys.exit(0)
if "--json-input" not in a:
    sys.exit(2)
open(sys.argv[0] + ".model", "w").write(out)
def wav(path, n=800):
    data = b"\\x01\\x00" * n
    hdr = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 22050, 44100, 2, 16)
    hdr += b"data" + struct.pack("<I", len(data))
    open(path, "wb").write(hdr + data)
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    dest = req["output_file"]
    open(sys.argv[0] + ".said", "a").write(req["text"] + "\\n")
    wav(dest)
    print(dest, flush=True)
'''


class Build(unittest.TestCase):
    def test_the_tarball_for_this_processor(self):
        with mock.patch.object(tts.platform, "machine", return_value="x86_64"):
            name, url, sha = tts.asset()
        self.assertEqual(name, "piper_linux_x86_64.tar.gz")
        self.assertTrue(url.endswith("/2023.11.14-2/" + name))
        self.assertEqual(len(sha), 64)
        with mock.patch.object(tts.platform, "machine", return_value="aarch64"):
            self.assertEqual(tts.asset()[0], "piper_linux_aarch64.tar.gz")

    def test_a_computer_with_no_build_is_told_so(self):
        with mock.patch.object(tts.platform, "machine", return_value="riscv64"):
            with self.assertRaises(tts.TtsError) as c:
                tts.asset()
        self.assertEqual(c.exception.status, 400)
        self.assertIn("riscv64", str(c.exception))

    def test_no_voice_file_no_use(self):
        self.assertEqual(len(tts.CONFIGS), len(tts.VOICES))

    def test_bark_is_not_offered(self):
        self.assertFalse(tts.status()["bark"])


class Voices(unittest.TestCase):
    def test_the_voice_shoggoth_offers(self):
        self.assertIn(tts.DEFAULT_VOICE, tts.VOICES)
        for name, path in tts.VOICES.items():
            self.assertTrue(path.endswith(name + ".onnx"))
            self.assertIn(name + ".onnx.json", tts.CONFIGS)

    def test_the_hand_checked_checksums(self):
        """Piper's GitHub release has no digests and Hugging Face gives no checksum for a voice's .json, so these
        are pinned here. They were checked against the real files; a wrong one would make every download fail."""
        for kind, (name, sha) in tts.TARBALLS.items():
            self.assertTrue(name.endswith(".tar.gz"), kind)
            self.assertEqual(len(sha), 64, kind)
            self.assertTrue(all(c in "0123456789abcdef" for c in sha), kind)
        for name, sha in tts.CONFIGS.items():
            self.assertTrue(name.endswith(".onnx.json"), name)
            self.assertEqual(len(sha), 64, name)
        self.assertEqual(len({v[0] for v in tts.TARBALLS.values()}), 3)    # the three processor kinds
        self.assertIn("x86_64", tts.TARBALLS)
        self.assertIn("aarch64", tts.TARBALLS)
        self.assertIn("armv7l", tts.TARBALLS)

    def test_a_voice_it_does_not_have(self):
        with mock.patch.object(tts, "_settings", return_value={}):
            with self.assertRaises(tts.TtsError) as c:
                tts.voice_entry("en_US-nobody-medium")
        self.assertEqual(c.exception.status, 400)

    def test_an_unknown_voice_setting_falls_back_to_the_default(self):
        with mock.patch.object(tts, "_settings", return_value={"voice": "en_US-nobody-medium"}):
            self.assertEqual(tts.choose_voice(), tts.DEFAULT_VOICE)

    def test_ready_needs_a_file_with_bytes_in_it(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(tts, "voices_dir", return_value=d):
            self.assertFalse(tts.voice_ready(tts.DEFAULT_VOICE))
            open(os.path.join(d, tts.DEFAULT_VOICE + ".onnx"), "wb").close()
            self.assertFalse(tts.voice_ready(tts.DEFAULT_VOICE))
            with open(os.path.join(d, tts.DEFAULT_VOICE + ".onnx"), "wb") as f:
                f.write(b"x")
            self.assertTrue(tts.voice_ready(tts.DEFAULT_VOICE))

    def test_a_voice_entry_comes_from_hugging_face(self):
        tree = [{"path": tts.VOICES[tts.DEFAULT_VOICE], "size": 63201294,
                 "lfs": {"oid": "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f"}},
                {"path": tts.VOICES[tts.DEFAULT_VOICE] + ".json", "size": 4885}]
        with mock.patch.object(tts.hf, "_get_json", return_value=tree):
            e = tts.voice_entry(tts.DEFAULT_VOICE)
        self.assertEqual(e["sha256"], "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f")
        self.assertEqual(e["name"], tts.VOICES[tts.DEFAULT_VOICE])
        self.assertEqual(e["config"], tts.VOICES[tts.DEFAULT_VOICE] + ".json")

    def test_a_voice_with_no_published_checksum_is_refused(self):
        tree = [{"path": tts.VOICES[tts.DEFAULT_VOICE], "size": 1}]
        with mock.patch.object(tts.hf, "_get_json", return_value=tree):
            with self.assertRaises(tts.hf.HFError) as c:
                tts.voice_entry(tts.DEFAULT_VOICE)
        self.assertIn("checksum", str(c.exception))


class Text(unittest.TestCase):
    def test_control_characters_go_and_spaces_become_one(self):
        self.assertEqual(tts.clean_text("  Hello\x00\n\tworld  "), "Hello world")

    def test_nothing_to_read(self):
        for bad in ("", "   ", "\x00\x1f", None):
            with self.assertRaises(tts.TtsError) as c:
                tts.clean_text(bad)
            self.assertEqual(c.exception.status, 400)

    def test_too_much_to_read_in_one_go(self):
        with self.assertRaises(tts.TtsError) as c:
            tts.clean_text("a" * (tts.MAX_TEXT + 1))
        self.assertEqual(c.exception.status, 413)


class Refused(unittest.TestCase):
    """Off by default: nothing is downloaded, nothing starts."""

    def test_off_by_default(self):
        with mock.patch.object(tts.store, "load", return_value={}):
            self.assertFalse(tts.enabled())

    def test_speak_when_it_is_off_starts_nothing(self):
        with mock.patch.object(tts, "_settings", return_value={}), \
                mock.patch.object(tts, "start") as start:
            with self.assertRaises(tts.TtsError) as c:
                tts.speak("Hello.")
        self.assertEqual(c.exception.status, 403)
        self.assertIn("turned off", str(c.exception))
        start.assert_not_called()

    def test_the_route_says_the_same(self):
        with mock.patch.object(tts, "_settings", return_value={}):
            status, ctype, body = tts.ui_speak({"text": "Hello."})
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"], "Read aloud is turned off.")


class Fake(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.exe = os.path.join(d, "piper-bin")
        with open(self.exe, "w") as f:
            f.write(FAKE)
        os.chmod(self.exe, os.stat(self.exe).st_mode | stat.S_IXUSR)
        self.env = mock.patch.dict(os.environ, {"SHOGGOTH_DATA_DIR": d})
        self.env.start()
        self.sett = mock.patch.object(tts, "_settings", return_value={"enabled": True, "exe": self.exe})
        self.sett.start()
        self.voice = mock.patch.object(tts, "ensure_voice", return_value=os.path.join(d, "v.onnx"))
        self.voice.start()

    def tearDown(self):
        tts.stop()
        self.voice.stop()
        self.sett.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_speaking_starts_piper_and_returns_a_wav(self):
        self.assertFalse(tts.running())
        out = tts.speak("Read this out.")
        self.assertTrue(tts.running())
        self.assertEqual(out[:4], b"RIFF")
        self.assertEqual(out[8:12], b"WAVE")
        self.assertIn(b"data", out[:44])
        with open(self.exe + ".args") as f:
            args = f.read()
        self.assertIn("--json-input", args)
        self.assertIn("-q", args)
        with open(self.exe + ".said") as f:
            self.assertEqual(f.read(), "Read this out.\n")
        self.assertEqual(tts.speak("And this.")[:4], b"RIFF")       # reuses the running process

    def test_the_voice_it_started_with(self):
        tts.speak("One.")
        with open(self.exe + ".model") as f:
            self.assertEqual(f.read(), os.path.join(self.tmp.name, "v.onnx"))

    def test_idle_piper_is_stopped(self):
        with mock.patch.object(tts, "IDLE_SECONDS", 0.3):
            tts.speak("Hello.")
            self.assertTrue(tts.running())
            for _ in range(60):
                if not tts.running():
                    break
                time.sleep(0.1)
        self.assertFalse(tts.running())

    def test_restarts_if_piper_died(self):
        tts.speak("First.")
        tts._state["proc"].kill()
        tts._state["proc"].wait()
        self.assertEqual(tts.speak("Second.")[:4], b"RIFF")

    def test_a_new_voice_restarts_the_process(self):
        tts.speak("One.")
        first = tts._state["proc"]
        with mock.patch.object(tts, "_settings", return_value={"enabled": True, "exe": self.exe, "voice": "en_US-amy-medium"}):
            tts.speak("Two.")
        self.assertNotEqual(tts._state["proc"], first)
        self.assertFalse(first.poll() is None)

    def test_no_piper_installed(self):
        with mock.patch.object(tts, "find_exe", return_value=None):
            with self.assertRaises(tts.TtsError) as c:
                tts.speak("Hello.")
        self.assertEqual(c.exception.status, 503)
        self.assertIn("isn't installed", str(c.exception))

    def test_the_ui_route_answers_a_wav(self):
        status, ctype, body = tts.ui_speak({"text": "Hello."})
        self.assertEqual((status, ctype), (200, "audio/wav"))
        self.assertEqual(body[:4], b"RIFF")

    def test_the_ui_route_answers_an_error_as_json(self):
        with mock.patch.object(tts, "speak", side_effect=tts.TtsError("Piper is cross.", 502)):
            status, ctype, body = tts.ui_speak({"text": "Hello."})
        self.assertEqual((status, ctype), (502, "application/json"))
        self.assertEqual(json.loads(body)["error"], "Piper is cross.")

    def test_the_openai_shape(self):
        status, ctype, body = tts.openai_speech(json.dumps({"input": "Hello.", "model": "tts-1"}).encode())
        self.assertEqual((status, ctype), (200, "audio/wav"))
        self.assertEqual(body[:4], b"RIFF")

    def test_openai_voice_names_fall_back_to_the_voice_here(self):
        """Other apps send OpenAI's own voice names (alloy, nova); those read with the voice Shoggoth is set to."""
        seen = {}

        def fake(text, voice=None):
            seen["voice"] = voice
            return b"RIFFxxxxWAVE"
        with mock.patch.object(tts, "speak", fake):
            self.assertEqual(tts.openai_speech(json.dumps({"input": "x", "voice": "alloy"}).encode())[0], 200)
            self.assertIsNone(seen["voice"])
            tts.openai_speech(json.dumps({"input": "x", "voice": tts.DEFAULT_VOICE}).encode())
            self.assertEqual(seen["voice"], tts.DEFAULT_VOICE)

    def test_the_openai_shape_refuses_other_formats(self):
        for fmt in ("mp3", "opus", "flac"):
            status, ctype, body = tts.openai_speech(json.dumps({"input": "Hello.", "response_format": fmt}).encode())
            self.assertEqual(status, 400)
            self.assertIn("WAV audio only", json.loads(body)["error"]["message"])
        self.assertEqual(tts.openai_speech(b"not json")[0], 400)
        self.assertEqual(tts.openai_speech(b"[]")[0], 400)

    def test_the_openai_shape_says_when_it_is_off(self):
        with mock.patch.object(tts, "_settings", return_value={}):
            status, ctype, body = tts.openai_speech(json.dumps({"input": "Hello."}).encode())
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"]["type"], "invalid_request_error")


class Install(unittest.TestCase):
    def test_install_checks_the_checksum_and_unpacks(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as src:
            tgz = os.path.join(src, "p.tar.gz")
            with tarfile.open(tgz, "w:gz") as tf:
                data = b"#!/bin/sh\necho 1.2.0\n"
                ti = tarfile.TarInfo("piper/piper")
                ti.size, ti.mode = len(data), 0o755
                tf.addfile(ti, io.BytesIO(data))
            with open(tgz, "rb") as f:
                raw = f.read()
            choice = ("piper_linux_x86_64.tar.gz", "file://" + tgz, tts.hf._hash_of(tgz).hexdigest())
            with mock.patch.dict(os.environ, {"SHOGGOTH_DATA_DIR": d}):
                b = tts.install(choice)
                self.assertEqual(b["tag"], tts.TAG)
                self.assertTrue(os.access(b["exe"], os.X_OK))
                bad = (choice[0], choice[1], "0" * 64)
                with self.assertRaisesRegex(RuntimeError, "checksum"):
                    tts.install(bad)


class Setup(unittest.TestCase):
    """POST /ui/speech/read/setup downloads the program and the voice in the background."""

    def setUp(self):
        tts.UPD.update(state="idle", bytes=0, total=0, what=None, error=None)

    def tearDown(self):
        tts.UPD.update(state="idle", bytes=0, total=0, what=None, error=None)

    @staticmethod
    def _wait(done, seconds=5):
        deadline = time.time() + seconds
        while time.time() < deadline and not done():
            time.sleep(0.01)

    def test_installs_piper_then_its_voice(self):
        calls = []

        def install(choice=None, cancel=None):
            calls.append("piper")
            return {"tag": tts.TAG, "dir": "/p", "exe": "/p/piper"}

        def voice(name, cancel=None):
            calls.append(name)
            tts.UPD.update(state="done")
            return "/p/voices/" + name

        with mock.patch.object(tts, "install", install), mock.patch.object(tts, "ensure_voice", voice), \
                mock.patch.object(tts, "_settings", return_value={}), mock.patch.object(tts, "find_exe", return_value=None):
            self.assertTrue(tts.start_setup())
            self._wait(lambda: len(calls) == 2 and tts.UPD["state"] == "done")
        self.assertEqual(calls, ["piper", tts.DEFAULT_VOICE])

    def test_a_piper_that_is_there_is_not_downloaded_again(self):
        with mock.patch.object(tts, "install") as install, \
                mock.patch.object(tts, "ensure_voice", return_value="/w/v") as voice, \
                mock.patch.object(tts, "_settings", return_value={}), \
                mock.patch.object(tts, "find_exe", return_value={"exe": "/w/piper", "source": "path"}):
            self.assertTrue(tts.start_setup())
            self._wait(lambda: voice.called)
        install.assert_not_called()
        voice.assert_called_once_with(tts.DEFAULT_VOICE)

    def test_does_not_start_twice(self):
        gate = threading.Event()
        calls = []

        def install(choice=None, cancel=None):
            calls.append("piper")
            gate.wait(5)
            return {"tag": tts.TAG, "dir": "/p", "exe": "/p/piper"}

        def voice(name, cancel=None):
            calls.append("voice")
            tts.UPD.update(state="done")
            return "/p/" + name

        with mock.patch.object(tts, "install", install), mock.patch.object(tts, "ensure_voice", voice), \
                mock.patch.object(tts, "_settings", return_value={}), mock.patch.object(tts, "find_exe", return_value=None):
            self.assertTrue(tts.start_setup())
            self.assertFalse(tts.start_setup())
            gate.set()
            self._wait(lambda: len(calls) == 2)
        self.assertEqual(calls, ["piper", "voice"])

    def test_a_failure_is_reported_in_the_progress(self):
        with mock.patch.object(tts, "install", side_effect=RuntimeError("GitHub is down")), \
                mock.patch.object(tts, "_settings", return_value={}), mock.patch.object(tts, "find_exe", return_value=None):
            self.assertTrue(tts.start_setup())
            self._wait(lambda: tts.UPD["state"] == "error")
        self.assertIn("GitHub is down", tts.UPD["error"])


class StatusText(unittest.TestCase):
    def test_it_says_whether_it_is_on_and_where_it_runs(self):
        with mock.patch.object(tts, "_settings", return_value={"enabled": True}), \
                mock.patch.object(tts, "find_exe", return_value={"exe": "/p/piper", "source": "shoggoth"}):
            st = tts.status()
        self.assertEqual((st["enabled"], st["installed"], st["runs_on"]), (True, True, "Runs on the processor"))
        self.assertEqual((st["voice"], st["installed"]), (tts.DEFAULT_VOICE, True))
        with mock.patch.object(tts, "_settings", return_value={}), \
                mock.patch.object(tts, "find_exe", return_value=None):
            st = tts.status()
        self.assertEqual((st["enabled"], st["installed"], st["runs_on"]), (False, False, None))


class Routes(unittest.TestCase):
    """The server routes: the switch, the voice, and the read-aloud reply, over a real server. Its own settings folder, so
    nothing here reads or writes the profile of whoever runs the tests."""

    TOKEN = "t" * 32

    def setUp(self):
        import http.server
        import server
        import threading
        self.server = server
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"SHOGGOTH_TOKEN": self.TOKEN,
                                                "SHOGGOTH_CONFIG_DIR": os.path.join(self.tmp.name, "config"),
                                                "SHOGGOTH_DATA_DIR": os.path.join(self.tmp.name, "data")})
        self.env.start()
        self.settings = mock.patch.object(tts, "_settings", return_value={"enabled": True})
        self.settings.start()
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        tts.stop()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.settings.stop()
        self.env.stop()
        self.tmp.cleanup()

    def _url(self, path):
        return "http://127.0.0.1:%d%s" % (self.httpd.server_address[1], path)

    def _get(self, path):
        import urllib.request
        req = urllib.request.Request(self._url(path), headers={"Authorization": "Bearer " + self.TOKEN})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()

    def _post(self, path, payload):
        import urllib.error
        import urllib.request
        req = urllib.request.Request(self._url(path), data=json.dumps(payload).encode(),
                                     headers={"Authorization": "Bearer " + self.TOKEN, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, r.headers.get("Content-Type"), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type"), e.read()

    def test_the_status_route(self):
        """GET /ui/speech/read is what the page reads before it offers Read aloud."""
        with mock.patch.object(tts, "status", return_value={"enabled": False, "bark": False}):
            status, body = self._get("/ui/speech/read")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"enabled": False, "bark": False})

    def test_the_speak_route_answers_a_wav(self):
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "piper-bin")
            with open(exe, "w") as f:
                f.write(FAKE)
            os.chmod(exe, os.stat(exe).st_mode | stat.S_IXUSR)
            voice = os.path.join(d, "v.onnx")
            with open(voice, "wb") as f:
                f.write(b"x")
            with mock.patch.object(tts, "_settings", return_value={"enabled": True, "exe": exe}), \
                    mock.patch.object(tts, "ensure_voice", return_value=voice):
                status, ctype, body = self._post("/ui/speech/speak", {"text": "Say this."})
        self.assertEqual((status, ctype), (200, "audio/wav"))
        self.assertEqual(body[:4], b"RIFF")

    def test_the_speak_route_refuses_nothing_to_read(self):
        status, ctype, body = self._post("/ui/speech/speak", {"text": "  "})
        self.assertEqual((status, ctype), (400, "application/json"))
        self.assertEqual(json.loads(body)["error"], "There's nothing to read aloud.")

    def test_the_speak_route_needs_the_secret(self):
        import urllib.error
        import urllib.request
        req = urllib.request.Request(self._url("/ui/speech/speak"), data=b'{"text": "x"}',
                                     headers={"Authorization": "Bearer wrong", "Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as c:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(c.exception.code, 401)

    def test_turning_it_on_and_off(self):
        seen, saved = {}, {}

        def update(fn):
            st = {}
            fn(st)
            saved.update(st)
            return st

        with mock.patch.object(tts, "status", return_value={"enabled": True}), \
                mock.patch.object(tts, "stop") as stop, \
                mock.patch.object(tts.store, "update", side_effect=update):

            class H:
                def send_json(self, obj, code=200):
                    seen.update(obj=obj, code=code)

            self.server.Handler.post_speech_read_config(H(), {"enabled": True})
        self.assertEqual(saved["tts"]["enabled"], True)
        self.assertTrue(seen["obj"]["ok"])
        stop.assert_not_called()

    def test_an_unknown_voice_is_refused(self):
        seen = {}

        class H:
            def send_json(self, obj, code=200):
                seen.update(obj=obj, code=code)

        self.server.Handler.post_speech_read_config(H(), {"voice": "en_US-nobody-medium"})
        self.assertEqual(seen["code"], 400)
        self.assertIn("doesn't have a voice", seen["obj"]["error"])


class GatewayRoute(unittest.TestCase):
    """POST /v1/audio/speech on the gateway goes to tts.py, not to a chat engine. Its own data folder, so the request
    counter beside the real profile isn't touched."""

    def setUp(self):
        import gateway
        self.gateway = gateway
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"SHOGGOTH_DATA_DIR": os.path.join(self.tmp.name, "data"),
                                                "SHOGGOTH_CONFIG_DIR": os.path.join(self.tmp.name, "config")})
        self.env.start()
        self.saved = dict(gateway.CONFIG)
        gateway.CONFIG.update(key="k-tts", capture=False)
        self.srv = gateway._Server(("127.0.0.1", 0), gateway.GWHandler)
        import threading
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.gateway.CONFIG.clear()
        self.gateway.CONFIG.update(self.saved)
        self.env.stop()
        self.tmp.cleanup()

    def test_speech_answered_by_tts(self):
        import urllib.request
        seen = {}

        def fake(body, headers):
            seen["body"] = body
            return 200, "audio/wav", b"RIFFxxxxWAVE"
        with mock.patch.object(tts, "openai_speech", fake):
            req = urllib.request.Request("http://127.0.0.1:%d/v1/audio/speech" % self.srv.server_address[1],
                                         data=b'{"input": "Hello."}',
                                         headers={"Authorization": "Bearer k-tts", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertEqual(r.status, 200)
                self.assertEqual(r.headers.get("Content-Type"), "audio/wav")
                self.assertEqual(r.read(), b"RIFFxxxxWAVE")
        self.assertEqual(seen["body"], b'{"input": "Hello."}')

    def test_the_gateway_needs_the_key(self):
        import urllib.error
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1:%d/v1/audio/speech" % self.srv.server_address[1], data=b'{"input": "x"}',
                                     headers={"Authorization": "Bearer wrong", "Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as c:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(c.exception.code, 401)
