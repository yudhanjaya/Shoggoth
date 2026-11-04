"""`shoggoth status` and `shoggoth connect` against a real server. Run: python3 -m unittest discover tests"""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import cli  # noqa: E402
import client  # noqa: E402
import gateway  # noqa: E402
import server  # noqa: E402
import store  # noqa: E402


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR", "SHOGGOTH_AUTH", "SHOGGOTH_TOKEN")}
        os.environ["SHOGGOTH_DATA_DIR"], os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")
        os.environ.pop("SHOGGOTH_AUTH", None)
        os.environ.pop("SHOGGOTH_TOKEN", None)
        s = store.load()
        gateway.ensure_key(s)
        self.key = store.load()["gateway"]["api_key"]
        self.srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.c = client.Client("127.0.0.1", self.srv.server_address[1])

    def tearDown(self):
        if self.srv:
            self.srv.shutdown()
            self.srv.server_close()
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv) + ["--port", str(self.c.port)])
        return code, out.getvalue(), err.getvalue()

    def test_status_reads_engines_models_and_the_gateway(self):
        s = cli.snapshot(self.c)
        self.assertEqual(set(s), {"gateway", "engines", "models", "starting", "in_flight", "requests_today"})
        self.assertIn("lemonade", [e["id"] for e in s["engines"]])
        self.assertNotIn("maya", [e["id"] for e in s["engines"]])           # Expert Mode is off
        self.assertIn("Gateway:", cli.status_text(s))

    def test_status_json_is_valid_and_the_exit_code_follows_the_gateway(self):
        code, out, _ = self.run_cli("status", "--json")
        self.assertEqual(code, 0 if json.loads(out)["gateway"]["running"] else 1)

    def test_connect_masks_the_key_unless_asked(self):
        code, out, _ = self.run_cli("connect")
        self.assertEqual(code, 0)
        self.assertNotIn(self.key, out)
        self.assertIn("/v1", out)
        self.assertIn("curl", out)
        code, out, _ = self.run_cli("connect", "--reveal")
        self.assertIn(self.key, out)

    def test_nothing_answering_is_exit_2_with_a_plain_message(self):
        self.srv.shutdown()
        self.srv.server_close()
        code, out, err = self.run_cli("status")
        self.assertEqual(code, 2)
        self.assertIn("isn't answering", err)
        self.srv = None

    def test_the_mask_never_shows_a_whole_key(self):
        self.assertNotEqual(cli.mask("shoggoth-abcdefghijklmnop"), "shoggoth-abcdefghijklmnop")
        self.assertEqual(cli.mask("short"), "…")


if __name__ == "__main__":
    unittest.main()
