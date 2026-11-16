"""The UI tests answer the page from tests/ui/api.json. This checks those recorded answers still look like what the real server sends:
it starts shoggoth/server.py on a throwaway profile (no llama.cpp, no network) and, for every /ui route in api.json, asks the real server and
compares the shape. A key the recording has and the real answer lacks means the page is being tested against an API that no longer
exists (how the Lemonade-era model list survived 5.0's tests while the real routes were gone).

Only /ui routes are compared: /api/* answers come from the engine, not Shoggoth. Values aren't compared, only which keys are there, at the
top level and one level down where both sides have something to look at. Run: python3 -m unittest tests.test_fixture_contract"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURE = os.path.join(HERE, "ui", "api.json")

# Recorded keys that depend on state the throwaway profile can't have (a model loaded, llama.cpp installed, a GPU): absent is not drift.
STATEFUL = {
    "/ui/info": {"llama_args"},
    "/ui/config": {"config.enabled"},                     # only sent once someone has turned llama.cpp off or on
    "/ui/setup": {"engine.update", "engine.tag", "engine.backend", "engine.backend_name", "engine.port"},
}
# Routes the recording keeps for a part of the page that is still there but needs something this profile won't have.
SKIP = {"/ui/engine/lemonade"}       # only answers on a profile that came from 4.x


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(port, path, timeout=30):
    with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def shape(rec, real, where, out, depth=0):
    """Record "where.key" for every key `rec` has that `real` doesn't."""
    if isinstance(rec, dict) and isinstance(real, dict):
        for k, v in rec.items():
            path = "%s.%s" % (where, k) if where else k
            if k not in real:
                out.append(path)
            elif depth < 1:
                shape(v, real[k], path, out, depth + 1)
    elif isinstance(rec, list) and isinstance(real, list) and rec and real and depth < 1:
        shape(rec[0], real[0], where + "[0]", out, depth + 1)


class Fixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.port = free_port()
        env = dict(os.environ, SHOGGOTH_AUTH="off", SHOGGOTH_DATA_DIR=os.path.join(cls.tmp, "data"), SHOGGOTH_CONFIG_DIR=os.path.join(cls.tmp, "config"),
                   XDG_DATA_HOME=os.path.join(cls.tmp, "xdg-data"), XDG_CONFIG_HOME=os.path.join(cls.tmp, "xdg-config"), SHOGGOTH_LLAMACPP_DIR="",
                   CUDA_VISIBLE_DEVICES="")
        cls.log = open(os.path.join(cls.tmp, "server.log"), "w")
        cls.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "shoggoth", "server.py"), "--no-browser", "--port", str(cls.port)],
                                    env=env, stdout=cls.log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        end = time.time() + 60
        while True:
            try:
                get(cls.port, "/ui/info", timeout=5)
                break
            except (OSError, ValueError):
                if cls.proc.poll() is not None or time.time() > end:
                    cls.tearDownClass()
                    raise RuntimeError("the server didn't start; see its log")
                time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        if cls.proc.poll() is None:
            cls.proc.terminate()
            try:
                cls.proc.wait(30)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
        cls.log.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_recorded_ui_answer_matches_the_real_one(self):
        with open(FIXTURE) as f:
            recorded = json.load(f)
        routes = [k for k in recorded if k.startswith("/ui/") and k.split("?")[0] not in SKIP]
        self.assertGreater(len(routes), 15)
        for route in routes:
            with self.subTest(route):
                try:
                    real = get(self.port, route)
                except urllib.error.HTTPError as e:
                    self.fail("%s answers %d on the real server, but the UI tests pretend it works" % (route, e.code))
                missing = []
                shape(recorded[route], real, "", missing)
                missing = [m for m in missing if m not in STATEFUL.get(route.split("?")[0], set())]
                self.assertEqual(missing, [], "%s: the recording has keys the real server doesn't send. Update tests/ui/api.json "
                                              "(node tests/ui/smoke.js --record URL TOKEN) or the page" % route)

    def test_every_route_the_page_calls_is_one_the_server_answers(self):
        """Each /ui path the page fetches with GET answers something other than 404 on the real server."""
        import re
        with open(os.path.join(ROOT, "shoggoth", "index.html"), encoding="utf8") as f:
            html = f.read()
        gets = set()
        for m in re.finditer(r'api\(\s*["`](/ui/[A-Za-z0-9/_\-.]+)["`?]\s*(\)|,\s*\{(?![^}]*method))', html):
            gets.add(m.group(1))
        self.assertGreater(len(gets), 15)
        for path in sorted(gets):
            with self.subTest(path):
                try:
                    get(self.port, path)
                except urllib.error.HTTPError as e:
                    self.assertNotEqual(e.code, 404, "the page calls %s, which the server doesn't have" % path)
                except (OSError, ValueError):
                    pass                                   # slow or not JSON: it exists, which is all this asks


class PageRoutes(unittest.TestCase):
    """Read from the page and the server's source, no server needed."""
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "shoggoth", "index.html"), encoding="utf8") as f:
            cls.html = f.read()
        with open(os.path.join(ROOT, "shoggoth", "server.py"), encoding="utf8") as f:
            cls.server = f.read()

    def calls(self, prefix):
        import re
        return sorted(set(re.findall(r'api\(\s*["`](%s[A-Za-z0-9/_\-.]*)' % re.escape(prefix), self.html)))

    def test_every_api_path_the_page_calls_is_one_the_gateway_forwards(self):
        """/api/* goes through the gateway, which forwards only the OpenAI-style door. A Lemonade-era call like /api/v1/pull would 404."""
        sys.path.insert(0, os.path.join(ROOT, "shoggoth"))
        import gateway
        known_unforwarded = {"/api/v1/stats"}         # the page asks and copes with no answer (llama.cpp has no /stats)
        for path in self.calls("/api/"):
            with self.subTest(path):
                if path in known_unforwarded:
                    continue
                self.assertTrue(gateway.ALLOW.match(gateway.norm(path)), "the page calls %s, which the gateway never forwards" % path)

    def test_every_ui_path_the_page_calls_is_named_in_the_server(self):
        """POSTs included, which the live check can't safely send."""
        for path in self.calls("/ui/"):
            with self.subTest(path):
                p = path.rstrip("/")
                self.assertTrue('"%s"' % p in self.server or '"%s/"' % p in self.server or any(
                    ('"%s' % p[:i]) in self.server for i in range(len(p), 4, -1) if p[i - 1] == "/" and '"%s"' % p[:i] in self.server),
                    "the page calls %s and server.py doesn't name it" % path)


if __name__ == "__main__":
    unittest.main()
