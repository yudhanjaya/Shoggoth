"""A small client for a running Shoggoth's own routes, for `shoggoth status`, `shoggoth connect` and a terminal interface. Standard library only.

It is a client of the same routes the page uses, with the same secret (read from the file Shoggoth keeps it in, never sent anywhere but
the loopback address), so the page and the terminal cannot disagree."""
import http.client
import json
import os

import uiauth


class Unreachable(Exception):
    """No Shoggoth answered at that address."""


class Client:
    def __init__(self, host=None, port=None):
        self.host = host or os.environ.get("SHOGGOTH_HOST", "127.0.0.1")
        self.host = "127.0.0.1" if self.host in ("0.0.0.0", "::") else self.host
        self.port = int(port or os.environ.get("SHOGGOTH_PORT", "13306"))

    def call(self, method, path, body=None, timeout=10):
        c = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        headers = {"Authorization": "Bearer " + uiauth.token(), "Host": "%s:%d" % (self.host, self.port)}
        try:
            c.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
            r = c.getresponse()
            raw = r.read()
        except OSError as e:
            raise Unreachable("Shoggoth isn't answering at %s:%d (%s)." % (self.host, self.port, e))
        finally:
            c.close()
        try:
            return r.status, json.loads(raw or b"{}")
        except ValueError:
            return r.status, {"error": raw[:200].decode("utf8", "replace")}

    def get(self, path):
        status, d = self.call("GET", path)
        if status != 200:
            raise Unreachable("%s answered %s: %s" % (path, status, d.get("error", "")))
        return d

    def post(self, path, body):
        return self.call("POST", path, body or {})
