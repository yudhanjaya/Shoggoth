"""Shoggoth's own page only opens for the window Shoggoth started. Run: python3 -m unittest discover tests"""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import server  # noqa: E402
import uiauth  # noqa: E402


class Door(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR", "SHOGGOTH_AUTH", "SHOGGOTH_TOKEN")}
        os.environ["SHOGGOTH_DATA_DIR"], os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")
        os.environ.pop("SHOGGOTH_AUTH", None)
        os.environ.pop("SHOGGOTH_TOKEN", None)
        self.srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, method, path, headers=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=body, headers=dict({"Host": "127.0.0.1:%d" % self.port}, **(headers or {})))
        r = c.getresponse()
        data = r.read()
        return r.status, dict(r.getheaders()), data

    def test_a_stranger_gets_the_locked_page_and_nothing_else(self):
        status, _, body = self.call("GET", "/")
        self.assertEqual(status, 401)
        self.assertIn(b"Start Shoggoth again", body)
        for method, path in (("GET", "/ui/info"), ("GET", "/ui/library"), ("GET", "/ui/providers"), ("POST", "/ui/model/ctx"), ("POST", "/ui/providers/reload"),
                             ("POST", "/ui/ping"), ("POST", "/ui/code"), ("GET", "/api/v1/models"), ("DELETE", "/ui/cowork/delete")):
            self.assertEqual(self.call(method, path, body="{}")[0], 401, (method, path))

    def test_the_version_is_public_so_a_second_launch_can_find_this_zest(self):
        status, _, body = self.call("GET", "/ui/ping")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["zest"], server.VERSION)

    def test_the_secret_works_as_a_bearer_token(self):
        auth = {"Authorization": "Bearer " + uiauth.token()}
        self.assertEqual(self.call("GET", "/ui/library/places", auth)[0], 200)
        self.assertEqual(self.call("GET", "/ui/library/places", {"Authorization": "Bearer nope"})[0], 401)
        self.assertEqual(self.call("GET", "/ui/library/places", {"Authorization": uiauth.token()})[0], 401)      # must say Bearer

    def test_a_one_time_code_becomes_a_cookie_and_cannot_be_used_twice(self):
        auth = {"Authorization": "Bearer " + uiauth.token()}
        status, _, body = self.call("POST", "/ui/code", auth, "{}")
        code = json.loads(body)["code"]
        status, headers, _ = self.call("GET", "/?c=" + code)
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "/")
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("zest_ui_%d=" % self.port, cookie)
        mine = {"Cookie": cookie.split(";")[0]}
        self.assertEqual(self.call("GET", "/", mine)[0], 200)                                                 # the page itself
        self.assertEqual(self.call("POST", "/ui/ping", mine)[0], 200)
        self.assertEqual(self.call("GET", "/?c=" + code)[0], 401)                                             # the code is spent

    def test_a_cookie_made_for_another_zest_on_this_computer_is_not_accepted(self):
        other = {"Cookie": "zest_ui_%d=%s" % (self.port + 1, uiauth.token())}
        self.assertEqual(self.call("GET", "/ui/info", other)[0], 401)

    def test_an_old_code_is_worth_nothing(self):
        code = uiauth.new_code()
        uiauth._codes[code] = 1                                                                               # long ago
        self.assertEqual(self.call("GET", "/?c=" + code)[0], 401)

    def test_it_can_be_switched_off(self):
        os.environ["SHOGGOTH_AUTH"] = "off"
        self.assertEqual(self.call("GET", "/ui/library/places")[0], 200)

    def test_the_secret_is_kept_private_and_stable(self):
        t = uiauth.token()
        path = os.path.join(self.tmp, "config", "ui-token")
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        self.assertEqual(uiauth.token(), t)
        self.assertGreaterEqual(len(t), 32)


if __name__ == "__main__":
    unittest.main()
