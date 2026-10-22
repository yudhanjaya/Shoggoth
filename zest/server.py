#!/usr/bin/env python3
"""Zest: a console for Lemonade Server.

Serves the single-page UI, proxies /api/* to Lemonade (so no CORS setup is needed) and exposes /ui/* helpers:
GPU telemetry, device and backend configuration, the built-in Lemonade (start, update), where models live, and
logs. Zest runs either its own bundled Lemonade ("built-in") or talks to one you already run ("existing").

Standard library only. Listens on 127.0.0.1 unless told otherwise.
"""
import argparse
import http.client
import json
import os
import re
import signal
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)          # the AppImage runs Python in isolated mode, which doesn't add this folder itself

import engine      # noqa: E402
import hardware    # noqa: E402
import lem         # noqa: E402
import llamaargs   # noqa: E402
import modelscan   # noqa: E402
import store       # noqa: E402
import util        # noqa: E402
from util import cached, invalidate, run   # noqa: E402

VERSION = "2.0.0"
_last_ping = {"t": None}
FORCED = {"addr": None}           # set by --lemonade: use exactly that server and skip the setup screen
LOOPBACK_ONLY = {"on": True}


# ---------------------------------------------------------------- which Lemonade
def effective_mode(s):
    if FORCED["addr"]:
        return "existing"
    if s["mode"] in ("builtin", "existing"):
        return s["mode"]
    return "builtin" if engine.programs() else "existing"      # a fresh install uses the bundled one if there is one


def _existing_target(s):
    e = s["existing"]
    return e["host"], int(e["port"]), e.get("api_key") or os.environ.get("LEMONADE_API_KEY", "")


def apply_mode(s):
    """Point Zest at the right Lemonade, starting the built-in one in the background when needed."""
    mode = effective_mode(s)
    if mode == "builtin":
        b = s["builtin"]
        if not b.get("api_key"):
            b["api_key"] = store.new_key()
            store.save(s)
        # Until it is up, aim at its (not yet listening) port, never at some other Lemonade.
        lem.configure("127.0.0.1", b["port"], b["api_key"])
        if not engine.running():
            threading.Thread(target=engine.start, args=(s,), daemon=True).start()
    else:
        if not FORCED["addr"]:
            engine.stop()
            lem.configure(*_existing_target(s))
    invalidate()


def current_mode():
    return effective_mode(store.load())


# ---------------------------------------------------------------- state for the setup screen
def setup_state():
    s = store.load()
    mode = effective_mode(s)
    h, p, k = _existing_target(s)
    reachable = lem.healthy()
    live = {}
    if reachable:
        cfg = lem.flat_config()
        live = {"models_dir": cfg.get("models_dir", ""), "extra_models_dir": cfg.get("extra_models_dir", "")}
    host, port = lem.target()
    return {
        "setup_done": bool(s["setup_done"] or FORCED["addr"]),
        "mode": mode, "mode_saved": s["mode"], "forced": bool(FORCED["addr"]),
        "engine": engine.summary(s),
        "existing": {"host": h, "port": p, "reachable": lem.healthy(h, p, k)},
        "lemonade": {"reachable": reachable, "target": f"{host}:{port}"},
        "models": {"storage": s["models"]["storage"], "custom_dir": s["models"]["custom_dir"],
                   "hf_dir": modelscan.hf_cache_dir(), "imports": s["models"]["imports"], "live": live},
        "arch_supported": engine.summary(s)["arch_supported"],
    }


def read_logs(n):
    if current_mode() == "builtin":
        try:
            with open(engine.log_path(), errors="replace") as f:
                return [l.rstrip("\n") for l in f.readlines()[-n:]]
        except OSError:
            return ["The built-in Lemonade hasn't written a log yet."]
    code, out = run(["journalctl", "-u", "lemond", "-n", str(n), "--no-pager", "-o", "cat"], 10)
    if code != 0 and not out.strip().count("\n"):
        out = "Couldn't read the lemond service log (%s).\nZest reads it with `journalctl -u lemond`." % out.strip()[:200]
    return out.splitlines()


def get_info():
    return hardware.info(current_mode(), VERSION)


# ---------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    server_version = "Zest/" + VERSION

    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    def same_origin(self):
        origin = self.headers.get("Origin")
        return not origin or urlparse(origin).netloc == self.headers.get("Host")

    def host_ok(self):
        """Refuse requests whose Host isn't this machine, which blocks DNS-rebinding attacks on a local tool."""
        if not LOOPBACK_ONLY["on"]:
            return True
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in ("localhost", "127.0.0.1", "::1")

    def _info(self):
        return cached("info", 4, get_info)

    # ---- GET
    def do_GET(self):
        if not self.host_ok():
            return self.send_json({"error": "unexpected Host header"}, 403)
        u = urlparse(self.path)
        p = u.path
        if p in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        elif p == "/ui/ping":
            self.send_json({"zest": VERSION})
        elif p == "/ui/gpu":
            self.send_json({"gpus": hardware.nvidia_gpus(), "t": time.time()})
        elif p == "/ui/info":
            self.send_json(self._info())
        elif p == "/ui/backends":
            self.send_json({"recipes": cached("backends", 4, hardware.backends)})
        elif p == "/ui/config":
            self.send_json({"config": lem.get("/internal/config"), "defaults": lem.get("/internal/config/defaults")})
        elif p == "/ui/args/schema":
            schema = llamaargs.args_schema(self._info().get("llama_bin"))
            self.send_json({"entries": schema or [], "verified": bool(schema)})
        elif p == "/ui/setup":
            self.send_json(setup_state())
        elif p == "/ui/engine/update":
            self.send_json(dict(engine.UPD))
        elif p == "/ui/models/scan":
            self.send_json({"sources": modelscan.scan()})
        elif p == "/ui/models/source":
            try:
                self.send_json({"source": modelscan.source_from_path(parse_qs(u.query).get("path", [""])[0])})
            except ValueError as e:
                self.send_json({"error": str(e)}, 400)
        elif p == "/ui/fs":
            try:
                self.send_json(modelscan.list_dir(parse_qs(u.query).get("path", [""])[0]))
            except ValueError as e:
                self.send_json({"error": str(e)}, 400)
        elif p == "/ui/logs":
            n = min(max(int(parse_qs(u.query).get("n", ["200"])[0]), 10), 1000)
            self.send_json({"lines": read_logs(n)})
        elif p.startswith("/api/"):
            self.proxy()
        else:
            self.send_json({"error": "not found"}, 404)

    # ---- POST
    def do_POST(self):
        if not self.host_ok():
            return self.send_json({"error": "unexpected Host header"}, 403)
        p = urlparse(self.path).path
        if p == "/ui/ping":
            _last_ping["t"] = time.time()
            return self.send_json({"zest": VERSION})
        if not self.same_origin():
            return self.send_json({"error": "cross-origin request refused"}, 403)
        if p.startswith("/api/"):
            return self.proxy()
        handler = {
            "/ui/device": self.post_device, "/ui/config": self.post_config,
            "/ui/args/parse": self.post_args_parse, "/ui/args/check": self.post_args_check,
            "/ui/backend": self.post_backend, "/ui/backend/install": self.post_backend_install,
            "/ui/setup/mode": self.post_mode, "/ui/setup/done": self.post_done,
            "/ui/engine/check": self.post_engine_check, "/ui/engine/update": self.post_engine_update,
            "/ui/engine/use": self.post_engine_use, "/ui/engine/restart": self.post_engine_restart,
            "/ui/models/storage": self.post_storage, "/ui/models/imports": self.post_imports,
        }.get(p)
        if not handler:
            return self.send_json({"error": "not found"}, 404)
        try:
            handler(self.read_json())
        except ValueError as e:
            self.send_json({"ok": False, "error": str(e)}, 400)

    do_DELETE = do_POST

    # ---- settings the UI edits in Lemonade
    def post_device(self, body):
        dev = body.get("device", "")
        if not isinstance(dev, str) or not re.fullmatch(r"([a-z]+\d+(,[a-z]+\d+)*)?", dev):
            return self.send_json({"error": "device must look like cuda1 or cuda0,cuda1 (empty = automatic)"}, 400)
        ok, msg = lem.set_config({"llamacpp": {"device": dev}})
        invalidate("info")
        if not ok and "nknown" in msg:
            msg = ("This Lemonade doesn't have the llamacpp.device setting yet, so devices can't be chosen from here. "
                   "The built-in Lemonade has it.")
        self.send_json({"ok": ok, "error": msg, "output": msg}, 200 if ok else 400)

    def post_config(self, body):
        if not body:
            return self.send_json({"error": "expected a JSON object"}, 400)
        status, payload = lem.request("POST", "/internal/set", body, timeout=15)
        invalidate()
        if status == 200:
            return self.send_json({"ok": True, "updated": payload.get("updated")})
        self.send_json({"ok": False, "error": lem.error_text(payload, "Lemonade rejected the change")}, 400 if status else 502)

    def post_args_parse(self, body):
        self.send_json(llamaargs.parse_args(str(body.get("args", "")), self._info().get("llama_bin")))

    def post_args_check(self, body):
        self.send_json(llamaargs.check_args(str(body.get("args", "")), self._info().get("llama_bin")))

    def _recipe(self, body):
        return next((r for r in cached("backends", 4, hardware.backends) if r["recipe"] == body.get("recipe")), None)

    def post_backend(self, body):
        rec, backend = self._recipe(body), body.get("backend")
        if not rec:
            return self.send_json({"error": "unknown recipe"}, 400)
        if not rec["selectable"]:
            return self.send_json({"error": "this engine doesn't support choosing a backend"}, 400)
        if backend != "auto" and not any(x["name"] == backend for x in rec["backends"]):
            return self.send_json({"error": "unknown backend"}, 400)
        ok, msg = lem.set_config({rec["recipe"].replace("-", ""): {"backend": backend}})
        invalidate()
        self.send_json({"ok": ok, "error": msg}, 200 if ok else 400)

    def post_backend_install(self, body):
        rec, backend = self._recipe(body), body.get("backend")
        if not rec:
            return self.send_json({"error": "unknown recipe"}, 400)
        b = next((x for x in rec["backends"] if x["name"] == backend), None)
        if not b or b["state"] not in ("installable", "update_required"):
            return self.send_json({"error": "that backend can't be installed"}, 400)
        ok, msg = lem.install_backend(rec["recipe"], backend)
        invalidate()
        switched = None
        if ok and rec["selectable"] and rec["current"] == "auto":
            # "auto" prefers the engine's default backend. If that one isn't installed, point Lemonade at the one
            # that was just installed so loading a model actually works.
            fresh = next((r for r in hardware.backends() if r["recipe"] == rec["recipe"]), None)
            if fresh and not any(b["name"] == fresh["default"] and b["state"] == "installed" for b in fresh["backends"]):
                if lem.set_config({rec["recipe"].replace("-", ""): {"backend": backend}})[0]:
                    switched = backend
                    invalidate()
        self.send_json({"ok": ok, "error": msg, "switched": switched}, 200 if ok else 500)

    # ---- setup: which Lemonade, and the built-in engine
    def post_mode(self, body):
        mode = body.get("mode")
        s = store.load()
        if mode == "builtin":
            if not engine.programs():
                raise ValueError("This copy of Zest has no built-in Lemonade yet. Download it first.")
        elif mode == "existing":
            host = str(body.get("host") or s["existing"]["host"])
            port = int(body.get("port") or s["existing"]["port"])
            if not re.fullmatch(r"[A-Za-z0-9.\-]+|\[[0-9a-fA-F:]+\]", host) or not 0 < port < 65536:
                raise ValueError("That address doesn't look right.")
            s["existing"].update(host=host, port=port)
            if "api_key" in body:
                s["existing"]["api_key"] = str(body["api_key"])
        else:
            raise ValueError("Unknown choice.")
        s["mode"] = mode
        store.save(s)
        if mode == "existing":
            lem.configure(*_existing_target(s))
        apply_mode(s)
        self.send_json(setup_state())

    def post_done(self, body):
        s = store.load()
        s["setup_done"] = bool(body.get("done", True))
        store.save(s)
        self.send_json({"ok": True})

    def post_engine_check(self, body):
        try:
            self.send_json({"ok": True, **engine.check(store.load())})
        except RuntimeError as e:
            self.send_json({"ok": False, "error": str(e)}, 502)

    def post_engine_update(self, body):
        s = store.load()
        s["builtin"]["version"] = ""          # asking for an update means "use the newest"
        store.save(s)
        started = engine.start_update()
        self.send_json({"started": started, **engine.UPD})

    def post_engine_use(self, body):
        version = str(body.get("version", ""))
        s = store.load()
        if version not in ("", "bundled") and not any(p["version"] == version for p in engine.programs()):
            raise ValueError("That version isn't installed.")
        s["builtin"]["version"] = version
        store.save(s)
        self.post_engine_restart({})

    def post_engine_restart(self, body):
        s = store.load()
        if effective_mode(s) == "builtin":
            threading.Thread(target=engine.restart, args=(s,), daemon=True).start()
            time.sleep(0.3)
        self.send_json({"ok": True, **{"engine": engine.summary(s)}})

    # ---- where models live
    def post_storage(self, body):
        s = store.load()
        modelscan.set_storage(s, body.get("kind"), body.get("path"))
        ok, msg = modelscan.apply_to_lemonade(s) if lem.healthy() else (False, "Lemonade isn't running yet; it will be applied when it starts.")
        store.save(s)
        invalidate()
        self.send_json({"ok": ok or not lem.healthy(), "message": msg, "models": setup_state()["models"]})

    def post_imports(self, body):
        s = store.load()
        chosen, seen = [], set()
        for src in body.get("sources", []):
            kind, path = src.get("kind"), str(src.get("path", ""))
            if kind == "ollama":
                if not os.path.isdir(path):
                    continue
                item = {"id": modelscan._source_id("ollama", path), "kind": "ollama", "label": "Ollama", "path": path}
            else:
                item = modelscan.source_from_path(path, src.get("label"))
                item = {k: item[k] for k in ("id", "kind", "label", "path")}
            if item["id"] not in seen:
                seen.add(item["id"])
                chosen.append(item)
        before = lem.get("/v1/models?show_all=true").get("data", [])
        s["models"]["imports"] = chosen
        linked = modelscan.sync_farm(chosen)
        ok, msg = (lem.set_config({"extra_models_dir": modelscan.farm_dir() if linked else ""})
                   if lem.healthy() else (True, ""))
        store.save(s)
        time.sleep(1.5)                       # let Lemonade rescan
        after = lem.get("/v1/models?show_all=true").get("data", [])
        invalidate()
        self.send_json({"ok": ok, "error": msg, "linked": linked, "listed_before": len(before), "listed_after": len(after),
                        "models": setup_state()["models"]})

    # ---- everything under /api goes to Lemonade untouched
    def proxy(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        headers = {k: v for k, v in self.headers.items() if k.lower() in ("content-type", "accept")}
        if lem.key():
            headers["Authorization"] = "Bearer " + lem.key()
        conn = http.client.HTTPConnection(*lem.target(), timeout=900)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
        except OSError as e:
            return self.send_json({"error": {"message": f"Lemonade is unreachable: {e}"}}, 502)
        self.send_response(resp.status)
        for h in ("Content-Type", "Cache-Control"):
            if resp.getheader(h):
                self.send_header(h, resp.getheader(h))
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()  # HTTP/1.0: the body ends when we close, so streaming just works
        try:
            while True:
                chunk = resp.read1(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            conn.close()


def zest_running(host, port):
    try:
        c = http.client.HTTPConnection(host, port, timeout=1.5)
        c.request("GET", "/ui/ping")
        return "zest" in json.loads(c.getresponse().read())
    except (OSError, ValueError):
        return False


def watchdog(srv, grace):
    """App mode: quit once the browser tab has been gone for `grace` seconds."""
    started = time.time()
    while True:
        time.sleep(5)
        last = _last_ping["t"]
        if (last and time.time() - last > grace) or (not last and time.time() - started > 300):
            srv.shutdown()
            return


def main():
    ap = argparse.ArgumentParser(description="Zest: a console for Lemonade Server")
    ap.add_argument("--host", default=os.environ.get("ZEST_HOST", "127.0.0.1"), help="address to listen on (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("ZEST_PORT", "13306")), help="port to listen on (default 13306)")
    ap.add_argument("--lemonade", default=None, metavar="HOST:PORT",
                    help="use this Lemonade Server and skip the setup screen (default: the choice saved in Zest's settings)")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    ap.add_argument("--app", action="store_true", help="desktop mode: quit automatically a few minutes after the browser tab is closed")
    ap.add_argument("--version", action="version", version="Zest " + VERSION)
    a = ap.parse_args()

    LOOPBACK_ONLY["on"] = a.host in ("127.0.0.1", "localhost", "::1")
    shown = "127.0.0.1" if a.host in ("0.0.0.0", "::") else a.host
    url = f"http://{shown}:{a.port}"

    if zest_running(shown, a.port):  # second launch: just show the existing one
        print(f"Zest is already running at {url}")
        if not a.no_browser:
            webbrowser.open(url)
        return
    try:
        srv = ThreadingHTTPServer((a.host, a.port), Handler)
    except OSError as e:
        sys.exit(f"Can't listen on {a.host}:{a.port}: {e}\nTry --port.")
    srv.daemon_threads = True

    s = store.load()
    if a.lemonade:
        h, _, p = a.lemonade.rpartition(":")
        FORCED["addr"] = (h or "127.0.0.1", int(p or 13305))
        lem.configure(FORCED["addr"][0], FORCED["addr"][1], os.environ.get("LEMONADE_API_KEY", ""))
    else:
        apply_mode(s)
    print(f"Zest {VERSION} on {url}  ->  Lemonade {effective_mode(s)} ({'%s:%s' % lem.target()})", flush=True)

    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(0)))   # so the built-in Lemonade is stopped too
    if a.app:
        threading.Thread(target=watchdog, args=(srv, int(os.environ.get("ZEST_APP_GRACE", "180"))), daemon=True).start()
    if not a.no_browser:
        threading.Timer(0.6, webbrowser.open, args=(url,)).start()
    try:
        srv.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        srv.server_close()        # free the port first; stopping Lemonade can take a few seconds
        engine.stop()


if __name__ == "__main__":
    main()
