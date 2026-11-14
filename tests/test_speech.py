"""Speech to text: picking whisper.cpp's build, the audio rules, and a fake whisper-server behind the real code."""
import hashlib
import io
import json
import os
import stat
import sys
import tarfile
import tempfile
import unittest
import wave
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shoggoth"))
import speech  # noqa: E402

FAKE = '''#!/usr/bin/env python3
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
a = sys.argv
port = int(a[a.index("--port") + 1])
open(sys.argv[0] + ".args", "w").write(" ".join(a[1:]))
class H(BaseHTTPRequestHandler):
    def log_message(self, *x): pass
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def do_POST(self):
        n = int(self.headers["Content-Length"]); body = self.rfile.read(n)
        ok = self.path == "/inference" and b"RIFF" in body and b'name="response_format"' in body
        out = json.dumps({"text": " hello  world \\n"} if ok else {"error": "bad request"}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(out)
HTTPServer(("127.0.0.1", port), H).serve_forever()
'''


def wav(rate=16000, ch=1, seconds=0.2):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * int(rate * seconds) * ch)
    return b.getvalue()


def multipart(fields, filename, data, ctype="audio/wav"):
    b = "XBOUNDARYX"
    out = b""
    for k, v in fields.items():
        out += ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (b, k, v)).encode()
    out += ('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\nContent-Type: %s\r\n\r\n' % (b, filename, ctype)).encode()
    out += data + ("\r\n--%s--\r\n" % b).encode()
    return out, {"Content-Type": "multipart/form-data; boundary=" + b}


class Release(unittest.TestCase):
    def test_pick_release_goes_by_files_and_date(self):
        name = speech._asset_name()
        a = {"name": name, "browser_download_url": "https://x/" + name, "size": 5, "digest": "sha256:" + "a" * 64}
        rels = [{"tag_name": "v1.9.5", "assets": [], "published_at": "2026-10-06T17:00:00Z"},
                {"tag_name": "b5454", "assets": [a], "published_at": "2026-10-06T16:00:00Z"},
                {"tag_name": "b5130", "assets": [a], "published_at": "2026-09-11T05:00:00Z"},
                {"tag_name": "b9999", "assets": [a], "draft": True, "published_at": "2027-01-01T00:00:00Z"}]
        self.assertEqual(speech.pick_release(rels)["tag_name"], "b5454")
        self.assertEqual(speech.pick_asset(rels[1])[3], "a" * 64)

    def test_no_checksum_no_install(self):
        a = {"name": speech._asset_name(), "browser_download_url": "u", "size": 1}
        with self.assertRaisesRegex(RuntimeError, "checksum"):
            speech.pick_asset({"tag_name": "b1", "assets": [a]})

    def test_model_choice(self):
        with mock.patch.object(speech, "_settings", return_value={}):
            self.assertEqual(speech.choose_model("cpu"), "ggml-base.en.bin")
            self.assertEqual(speech.choose_model("cuda13"), "ggml-large-v3-turbo-q5_0.bin")
            with mock.patch.object(speech.llamacpp, "nvidia_driver", return_value=None):
                self.assertEqual(speech.choose_model("unknown"), "ggml-base.en.bin")
            with mock.patch.object(speech.llamacpp, "nvidia_driver", return_value=580):
                self.assertEqual(speech.choose_model("unknown"), "ggml-large-v3-turbo-q5_0.bin")


class Audio(unittest.TestCase):
    def test_16k_wav_passes_straight_through(self):
        w = wav()
        self.assertIs(speech.to_wav(w, "audio/wav"), w)

    def test_webm_without_ffmpeg_says_send_wav(self):
        with mock.patch.object(speech.shutil, "which", return_value=None):
            with self.assertRaises(speech.SpeechError) as c:
                speech.to_wav(b"\x1a\x45\xdf\xa3 webm", "audio/webm;codecs=opus")
        self.assertEqual(c.exception.status, 415)
        self.assertIn("send 16 kHz WAV", str(c.exception))
        self.assertIn("audio/webm", str(c.exception))

    def test_other_rate_wav_without_ffmpeg_is_an_error(self):
        with mock.patch.object(speech.shutil, "which", return_value=None):
            with self.assertRaises(speech.SpeechError):
                speech.to_wav(wav(rate=44100), "audio/wav")

    @unittest.skipUnless(speech.shutil.which("ffmpeg"), "needs ffmpeg")
    def test_ffmpeg_converts_other_rates(self):
        out = speech.to_wav(wav(rate=44100, ch=2), "audio/wav")
        self.assertTrue(speech._wav_ok(out))

    def test_empty(self):
        with self.assertRaises(speech.SpeechError) as c:
            speech.to_wav(b"", "audio/wav")
        self.assertEqual(c.exception.status, 400)


class Fake(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.exe = os.path.join(d, "whisper-server")
        with open(self.exe, "w") as f:
            f.write(FAKE)
        os.chmod(self.exe, os.stat(self.exe).st_mode | stat.S_IXUSR)
        self.model = os.path.join(d, "m.bin")
        with open(self.model, "wb") as f:
            f.write(b"x")
        self.env = mock.patch.dict(os.environ, {"SHOGGOTH_DATA_DIR": d})
        self.env.start()
        self.sett = mock.patch.object(speech, "_settings", return_value={"server": self.exe, "model": self.model, "backend": "cpu"})
        self.sett.start()

    def tearDown(self):
        speech.stop()
        self.sett.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_transcribe_starts_the_server_and_cleans_the_text(self):
        self.assertFalse(speech.running())
        self.assertEqual(speech.transcribe(wav(), "audio/wav"), {"text": "hello world"})
        self.assertTrue(speech.running())
        with open(self.exe + ".args") as f:
            args = f.read()
        self.assertIn("--host 127.0.0.1", args)
        self.assertIn("-ng", args)                      # a cpu build never reaches for a card
        self.assertEqual(speech.transcribe(wav(), "audio/wav")["text"], "hello world")   # reuses the running server

    def test_idle_server_is_stopped(self):
        with mock.patch.object(speech, "IDLE_SECONDS", 0.3):
            speech.transcribe(wav(), "audio/wav")
            self.assertTrue(speech.running())
            for _ in range(60):
                if not speech.running():
                    break
                speech.time.sleep(0.1)
        self.assertFalse(speech.running())

    def test_restarts_if_the_server_died(self):
        speech.transcribe(wav(), "audio/wav")
        speech._state["proc"].kill()
        speech._state["proc"].wait()
        self.assertEqual(speech.transcribe(wav(), "audio/wav")["text"], "hello world")

    def test_ui_handler(self):
        status, out = speech.ui_transcribe(wav(), "audio/wav")
        self.assertEqual((status, out), (200, {"text": "hello world"}))
        status, out = speech.ui_transcribe(b"", "audio/wav")
        self.assertEqual(status, 400)
        self.assertIn("error", out)

    def test_openai_shape(self):
        body, h = multipart({"model": "whisper-1", "response_format": "json"}, "a.wav", wav())
        status, ctype, raw = speech.openai_transcriptions(body, h)
        self.assertEqual((status, ctype, json.loads(raw)), (200, "application/json", {"text": "hello world"}))
        body, h = multipart({"response_format": "text"}, "a.wav", wav())
        status, ctype, raw = speech.openai_transcriptions(body, {k.lower(): v for k, v in h.items()})
        self.assertEqual((status, raw), (200, b"hello world\n"))
        body, h = multipart({}, "a.webm", b"\x1a\x45\xdf\xa3", "audio/webm")
        with mock.patch.object(speech.shutil, "which", return_value=None):
            status, ctype, raw = speech.openai_transcriptions(body, h)
        self.assertEqual(status, 415)
        self.assertIn("WAV", json.loads(raw)["error"]["message"])

    def test_openai_bad_requests(self):
        self.assertEqual(speech.openai_transcriptions(b"{}", {"Content-Type": "application/json"})[0], 400)
        body, h = multipart({"response_format": "srt"}, "a.wav", wav())
        self.assertEqual(speech.openai_transcriptions(body, h)[0], 400)

    def test_missing_binary(self):
        with mock.patch.object(speech, "_settings", return_value={"server": "/nope/whisper-server"}):
            status, out = speech.ui_transcribe(wav(), "audio/wav")
        self.assertEqual(status, 500)
        self.assertIn("settings", out["error"])


class Install(unittest.TestCase):
    def test_install_cpu_verifies_and_unpacks(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as src:
            tgz = os.path.join(src, "w.tar.gz")
            with tarfile.open(tgz, "w:gz") as tf:
                data = b"#!/bin/sh\necho usage: whisper-server\n"
                ti = tarfile.TarInfo("whisper-bin-ubuntu-x64/whisper-server")
                ti.size, ti.mode = len(data), 0o755
                tf.addfile(ti, io.BytesIO(data))
            with open(tgz, "rb") as f:
                raw = f.read()
            rel = {"tag_name": "b1", "assets": [{"name": speech._asset_name(), "browser_download_url": "file://" + tgz,
                                                  "size": len(raw), "digest": "sha256:" + hashlib.sha256(raw).hexdigest()}]}
            with mock.patch.dict(os.environ, {"SHOGGOTH_DATA_DIR": d}):
                b = speech.install_cpu(rel)
                self.assertEqual((b["tag"], b["backend"]), ("b1", "cpu"))
                self.assertTrue(os.access(b["server"], os.X_OK))
                rel["assets"][0]["digest"] = "sha256:" + "0" * 64
                with self.assertRaisesRegex(RuntimeError, "checksum"):
                    speech.install_cpu(rel)


if __name__ == "__main__":
    unittest.main()


class GatewayRoute(unittest.TestCase):
    """POST /v1/audio/transcriptions on the gateway goes to speech.py, not to a chat engine."""

    def test_transcriptions_answered_by_speech(self):
        import threading
        import urllib.request
        import gateway
        saved = dict(gateway.CONFIG)
        self.addCleanup(lambda: (gateway.CONFIG.clear(), gateway.CONFIG.update(saved)))
        gateway.CONFIG.update(key="k-test", capture=False)
        srv = gateway._Server(("127.0.0.1", 0), gateway.GWHandler)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        seen = {}

        def fake(body, headers):
            seen["body"] = body
            return 200, "application/json", b'{"text": "hello"}'
        with mock.patch.object(speech, "openai_transcriptions", fake):
            req = urllib.request.Request("http://127.0.0.1:%d/v1/audio/transcriptions" % srv.server_address[1], data=b"AUDIO",
                                         headers={"Authorization": "Bearer k-test", "Content-Type": "multipart/form-data; boundary=x"})
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertEqual(json.loads(r.read()), {"text": "hello"})
        self.assertEqual(seen["body"], b"AUDIO")
