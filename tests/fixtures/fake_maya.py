"""A stand-in for a running Maya (written from the plan's description of its API; no recording of the real one was available).

`FakeMaya(...)` serves /health, /v1/models, /slots, POST /models/load and /models/unload on a free port. `.busy` makes /slots report work and
/models/unload answer 409; `.management` False makes the model calls answer 403; `.service` adds the field that marks a Strata."""
import http.server
import json
import threading


class FakeMaya:
    def __init__(self, model="glm-5.3-flash", busy=False, management=True, service=None):
        self.model, self.busy, self.management, self.service, self.calls = model, busy, management, service, []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def _send(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.path == "/health":
                    h = {"model_label": outer.model or "", "version": "1.1.50"}
                    if outer.service:
                        h["service"] = outer.service
                    return self._send(200, h)
                if self.path == "/v1/models":
                    return self._send(200, {"data": [{"id": outer.model, "meta": {"n_ctx": 131072}}] if outer.model else []})
                if self.path == "/slots":
                    return self._send(200, [{"id": 0, "is_processing": outer.busy}])
                self._send(404, {})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                outer.calls.append((self.path, body))
                if not outer.management:
                    return self._send(403, {"error": "model management is off"})
                if self.path == "/models/unload":
                    if outer.busy:
                        return self._send(409, {"error": "busy"})
                    outer.model = None
                    return self._send(200, {"ok": True})
                if self.path == "/models/load":
                    outer.model = body.get("name")
                    return self._send(200, {"ok": True})
                self._send(404, {})

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
