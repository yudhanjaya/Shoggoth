"""A tiny OpenAI-style server for the tests: /health and /v1/models on the port given as the first argument."""
import http.server
import json
import sys


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = {"ok": True} if self.path == "/health" else {"data": [{"id": "m1", "meta": {"n_ctx": 4096}}]}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


http.server.HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
