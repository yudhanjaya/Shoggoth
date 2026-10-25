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
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.parse
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)          # the AppImage runs Python in isolated mode, which doesn't add this folder itself

import agent       # noqa: E402
import engine      # noqa: E402
import folders     # noqa: E402
import gateway     # noqa: E402
import hardware    # noqa: E402
import hf          # noqa: E402
import lem         # noqa: E402
import llamaargs   # noqa: E402
import modelscan   # noqa: E402
import piup        # noqa: E402
import skills      # noqa: E402
import store       # noqa: E402
import util        # noqa: E402
import websearch   # noqa: E402
from util import cached, invalidate, run   # noqa: E402

VERSION = "3.3.1"
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
        "gpus_found": util.cached("gpus_found", 30, hardware.detect_gpus),
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

    def send_vendor(self, name, ctype="text/javascript; charset=utf-8"):
        """A bundled third-party script (Mermaid). Absent in a source checkout until packaging/fetch-vendor.sh has run."""
        try:
            with open(os.path.normpath(os.path.join(HERE, "vendor", name)), "rb") as f:
                body = f.read()
        except OSError:
            return self.send_json({"error": "not installed"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache" if name.endswith(".md") else "max-age=86400")
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
        elif p == "/ui/docs.md":
            self.send_vendor("../docs.md", "text/markdown; charset=utf-8")
        elif p == "/ui/vendor/mermaid.js":
            self.send_vendor("mermaid.min.js")
        elif p == "/ui/gpu":
            self.send_json({"gpus": hardware.all_gpus(), "t": time.time()})
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
        elif p == "/ui/hf":
            try:
                self.send_json(hf.lookup(parse_qs(u.query).get("q", [""])[0]))
            except ValueError as e:
                self.send_json({"error": str(e)}, 400)
        elif p == "/ui/server":
            ids = {int(x) for x in parse_qs(u.query).get("ids", [""])[0].split(",") if x.isdigit()}
            qids = {int(x) for x in parse_qs(u.query).get("q", [""])[0].split(",") if x.isdigit()}
            self.get_server(ids, qids)
        elif p == "/ui/activity":
            self.send_json({"items": gateway.REC.activity()})
        elif p == "/ui/cowork":
            self.send_json(agent.state())
        elif p == "/ui/pi":
            self.send_json(piup.summary())
        elif p == "/ui/skills":
            self.send_json({"sets": skills.scan()})
        elif p == "/ui/search":
            self.get_search(parse_qs(u.query))
        elif p == "/ui/model":
            self.send_json(agent.model_state())
        elif p == "/ui/cowork/models":
            self.send_json({"models": agent.chat_models()})
        elif p == "/ui/cowork/stream":
            self.cowork_stream(parse_qs(u.query))
        elif p == "/ui/cowork/live":
            self.cowork_live(parse_qs(u.query))
        elif p.startswith("/ui/cowork/job"):
            self.get_cowork_job(p, parse_qs(u.query))
        elif p == "/ui/cowork/folder/info":
            try:
                self.send_json(folders.info(parse_qs(u.query).get("path", [""])[0]))
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
            "/ui/gateway": self.post_gateway, "/ui/cowork/new": self.post_cowork_new,
            "/ui/cowork/cmd": self.post_cowork_cmd, "/ui/cowork/answer": self.post_cowork_answer,
            "/ui/cowork/mode": self.post_cowork_mode, "/ui/cowork/delete": self.post_cowork_delete,
            "/ui/cowork/rename": self.post_cowork_rename, "/ui/cowork/model": self.post_cowork_model,
            "/ui/search/settings": self.post_search_settings, "/ui/search/test": self.post_search_test, "/ui/model/load": self.post_model_load, "/ui/model/unload": self.post_model_unload, "/ui/model/ctx": self.post_model_ctx,
            "/ui/cowork/job/new": self.post_job_new, "/ui/cowork/job/ctl": self.post_job_ctl,
            "/ui/cowork/job/apply": self.post_job_apply, "/ui/cowork/job/discard": self.post_job_discard,
            "/ui/cowork/folders": self.post_cowork_folders, "/ui/cowork/seen": self.post_cowork_seen,
            "/ui/cowork/allow/remove": self.post_allow_remove, "/ui/cowork/folder": self.post_cowork_folder, "/ui/skills/install": self.post_skills_install, "/ui/pi/check": self.post_pi_check,
            "/ui/pi/update": self.post_pi_update, "/ui/pi/use": self.post_pi_use,
            "/ui/skills/remove": self.post_skills_remove, "/ui/skills/toggle": self.post_skills_toggle,
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
        path = gateway.norm(self.path)
        rec = None
        if self.command == "POST" and gateway.INFER.match(path):      # Zest's own chat shows up on the Server page too
            model, stream = gateway.describe(body)
            rec = gateway.REC.start("Zest", "zest", self.command, path, model, stream, "local")
        gateway.relay(self, self.command, self.path, body, rec, store.load()["gateway"]["capture"] if rec else False)

    # ---- the Server page
    def get_server(self, ids, qids=()):
        s = store.load()
        items, seq = gateway.REC.snapshot(60, ids if s["gateway"]["capture"] else ())
        if s["gateway"]["capture"] and qids:
            q = gateway.REC.quotes(set(qids))
            for it in items:
                if it["id"] in q:
                    it["quote"] = q[it["id"]]
        self.send_json({"gateway": gateway.public_state(s), "requests": items, "seq": seq, "now": time.time(),
                        "active": gateway.REC.active(), "today": gateway.REC.count_today()})

    def post_gateway(self, body):
        s = store.load()
        g = s["gateway"]
        for k in ("enabled", "lan", "capture"):
            if k in body:
                g[k] = bool(body[k])
        if body.get("new_key"):
            g["api_key"] = "zest-" + store.new_key()
        store.save(s)
        gateway.GATEWAY.apply(s)
        self.send_json({"gateway": gateway.public_state(s)})


    # ---- Cowork: the Pi agent
    def post_cowork_new(self, body):
        t = agent.create(body.get("cwd"), body.get("mode", "ask"), str(body.get("model", "")), body.get("folders"))
        self.send_json({"ok": True, "task": t})

    def post_cowork_cmd(self, body):
        self.send_json(agent.command(str(body.get("id", "")), body.get("cmd") or {}))

    def post_cowork_answer(self, body):
        s = agent.session(str(body.get("id", "")), start=False)
        a = body.get("answer") or {}
        if not s or a.get("id") not in s.pending:
            return self.send_json({"ok": False, "error": "That question was already answered."}, 409)
        rec = {"type": "extension_ui_response", "id": a["id"]}
        rec.update({k: a[k] for k in ("value", "confirmed", "cancelled") if k in a})
        asked = s.pending.get(a["id"]) or {}
        val = str(a.get("value", ""))
        if val.startswith("Always allow "):
            try:
                key = (json.loads(asked.get("title", "{}")) or {}).get("key") or ""
            except ValueError:
                key = ""
            if key and val == "Always allow " + key:
                agent.remember_allow(str(body.get("id", "")), key)
        s.answer(rec)
        self.send_json({"ok": True})

    def post_cowork_mode(self, body):
        agent.set_mode(str(body.get("id", "")), str(body.get("mode", "")))
        self.send_json({"ok": True})

    def post_cowork_delete(self, body):
        agent.delete(str(body.get("id", "")))
        self.send_json({"ok": True})

    def post_cowork_rename(self, body):
        agent.rename(str(body.get("id", "")), body.get("title", ""))
        self.send_json({"ok": True})

    # ---- web search for the chat (asked first by the approval gate in Pi; Zest only does the fetching)
    def get_search(self, q):
        if not agent.search_enabled():
            return self.send_json({"error": "Web search is switched off in Zest."}, 403)
        try:
            self.send_json({"results": websearch.search(q.get("q", [""])[0], int(q.get("n", ["6"])[0] or 6), agent.search_config())})
        except websearch.SearchError as e:
            # the model reads this text: when DuckDuckGo refuses, it is told to send the person to get a key
            self.send_json({"error": websearch.NEED_KEY if e.need_key else str(e), "need_key": e.need_key}, 502)
        except ValueError as e:
            self.send_json({"error": str(e)}, 400)

    def post_search_settings(self, body):
        agent.set_search(body.get("enabled"), body.get("provider"), body.get("brave_key"), body.get("tavily_key"))
        self.send_json(dict(agent.search_public(), ok=True))

    def post_search_test(self, body):
        """The Test button: one real search with the settings as they are, reported in plain words."""
        try:
            r = websearch.search("zest lemon", 3, agent.search_config())
            self.send_json({"ok": True, "message": "It works: %d results, the first from %s." % (len(r), urllib.parse.urlparse(r[0]["url"]).netloc)})
        except websearch.SearchError as e:
            self.send_json({"ok": False, "need_key": e.need_key, "message": str(e)})

    # ---- the model in memory (the chat bar)
    def post_model_load(self, body):
        """Load (or reload) a model at the chat context size. A size given here becomes the new chat setting."""
        model = str(body.get("model", "")).strip()
        ctx = None
        if body.get("ctx"):
            ctx = agent.set_chat_ctx(int(body["ctx"]))
        ok, msg, got = agent.load_model(model, ctx, bool(body.get("force")))
        invalidate("info")
        self.send_json({"ok": ok, "error": msg, "ctx": got}, 200 if ok else 502)

    def post_model_unload(self, body):
        ok, msg = agent.unload_model(str(body.get("model", "")).strip())
        invalidate("info")
        self.send_json({"ok": ok, "error": msg}, 200 if ok else 409)

    def post_model_ctx(self, body):
        """Only remember the context size, for when the model is loaded next."""
        self.send_json({"ok": True, "ctx": agent.set_chat_ctx(int(body.get("ctx") or agent.DEFAULT_CTX))})

    def post_pi_check(self, body):
        try:
            self.send_json(piup.check())
        except RuntimeError as e:
            self.send_json({"error": str(e)}, 502)

    def post_pi_update(self, body):
        self.send_json({"started": piup.start_update(), "update": dict(piup.UPD)})

    def post_pi_use(self, body):
        piup.use(str(body.get("version", "")))
        self.send_json(dict(piup.summary(), ok=True))

    def post_skills_install(self, body):
        r = skills.install(body.get("link", ""))
        agent.restart_idle()
        self.send_json(dict(r, ok=True, sets=skills.scan()))

    def post_skills_remove(self, body):
        skills.remove(str(body.get("id", "")))
        agent.restart_idle()
        self.send_json({"ok": True, "sets": skills.scan()})

    def post_skills_toggle(self, body):
        if body.get("set"):
            skills.toggle_set(str(body["set"]), bool(body.get("enabled")))
        else:
            skills.toggle(str(body.get("key", "")), bool(body.get("enabled")))
        agent.restart_idle()
        self.send_json({"ok": True, "sets": skills.scan()})

    def post_cowork_folder(self, body):
        agent.set_folder(str(body.get("id", "")), body.get("cwd", ""))
        self.send_json({"ok": True})

    def post_cowork_model(self, body):
        sid = str(body.get("id", ""))
        live = agent.session(sid, start=False)
        if live and live.running:
            raise ValueError("Wait for it to finish its current answer first.")
        agent.stop(sid)
        agent._update_meta(sid, model=str(body.get("model", "")))
        self.send_json({"ok": True})

    def get_cowork_job(self, p, q):
        jid = q.get("id", [""])[0]
        try:
            if p == "/ui/cowork/job":
                self.send_json(agent.job_detail(jid))
            elif p == "/ui/cowork/job/changes":
                self.send_json(agent.job_changes(jid))
            elif p == "/ui/cowork/job/diff":
                self.send_json(agent.job_diff(jid, q.get("path", [""])[0]))
            else:
                self.send_json({"error": "not found"}, 404)
        except ValueError as e:
            self.send_json({"error": str(e)}, 400)

    def post_job_new(self, body):
        t = agent.create_job(str(body.get("parent", "")), body.get("job") or {})
        self.send_json({"ok": True, "id": t["id"]})

    def post_job_ctl(self, body):
        self.send_json(agent.ctl_job(str(body.get("id", "")), str(body.get("action", ""))))

    def post_job_apply(self, body):
        only = body.get("only")
        self.send_json(dict(agent.job_apply(str(body.get("id", "")), only if isinstance(only, list) else None), ok=True))

    def post_job_discard(self, body):
        self.send_json(agent.job_discard(str(body.get("id", ""))))

    def post_cowork_folders(self, body):
        agent.set_folders(str(body.get("id", "")), body.get("folders") or [])
        self.send_json({"ok": True})

    def post_cowork_seen(self, body):
        agent.seen(str(body.get("id", "")))
        self.send_json({"ok": True})

    def post_allow_remove(self, body):
        folders.allow_remove(str(body.get("path", "")), str(body.get("key", "")))
        agent.restart_idle()      # idle chats restart on their next message and pick up the shorter list
        self.send_json({"ok": True})

    def cowork_live(self, q):
        """One stream for the whole sidebar: the task list, sent again whenever anything in it changes."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        ver = -1
        try:
            while True:
                cur = agent.wait_change(ver, 15)
                if cur == ver:
                    self.wfile.write(b": ping\n\n")
                else:
                    ver = cur
                    self.wfile.write(b"data: " + json.dumps(agent.state()).encode() + b"\n\n")
                self.wfile.flush()
        except OSError:
            pass

    def cowork_stream(self, q):
        """Server-sent events: everything the agent does, from the cursor the page last saw."""
        try:
            s = agent.session(q.get("id", [""])[0])
            cursor = int(q.get("since", ["-1"])[0])
        except (RuntimeError, ValueError) as e:
            return self.send_json({"error": str(e)}, 400)
        if not s:
            return self.send_json({"error": "no such task"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        s.watchers += 1
        try:
            while True:
                batch = s.since(cursor)
                out = b"".join(b"id: %d\ndata: %s\n\n" % (n, json.dumps(r).encode()) for n, r in batch)
                self.wfile.write(out or b": ping\n\n")
                self.wfile.flush()
                if batch:
                    cursor = batch[-1][0]
                if batch and batch[-1][1].get("type") == "zest_exit":
                    return
        except OSError:
            pass
        finally:
            s.watchers -= 1
            s.last = time.time()


def zest_running(host, port):
    """The version of the Zest answering on this port, or None when it isn't Zest (or nothing is there)."""
    try:
        c = http.client.HTTPConnection(host, port, timeout=1.5)
        c.request("GET", "/ui/ping")
        return json.loads(c.getresponse().read()).get("zest")
    except (OSError, ValueError, AttributeError):
        return None


def watchdog(srv, grace):
    """App mode: quit once the browser tab has been gone for `grace` seconds."""
    started = time.time()
    while True:
        time.sleep(5)
        if agent.busy():
            continue              # a job is running or waiting on you: stay open even with the tab closed
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

    there = zest_running(shown, a.port)
    if there == VERSION:  # second launch: just show the existing one
        print(f"Zest is already running at {url}")
        if not a.no_browser:
            webbrowser.open(url)
        return
    if there:
        # An older Zest is still running (its window may be hidden). Showing it would open the old interface, so start
        # this one beside it on the next free port; the old one quits by itself a few minutes after its tab closes.
        for p in range(a.port + 1, a.port + 11):
            if not zest_running(shown, p):
                try:
                    socket.create_connection((shown, p), timeout=0.3).close()
                except OSError:
                    print(f"Zest {there} is still running on port {a.port}; starting Zest {VERSION} on port {p} instead.", flush=True)
                    a.port, url = p, f"http://{shown}:{p}"
                    break
    try:
        srv = ThreadingHTTPServer((a.host, a.port), Handler)
    except OSError as e:
        sys.exit(f"Can't listen on {a.host}:{a.port}: {e}\nTry --port.")
    srv.daemon_threads = True

    agent.set_zest_port(a.port)
    agent.recover()
    piup.on_installed.append(agent.restart_idle)
    threading.Thread(target=agent.reaper, daemon=True).start()
    s = store.load()
    if a.lemonade:
        h, _, p = a.lemonade.rpartition(":")
        FORCED["addr"] = (h or "127.0.0.1", int(p or 13305))
        lem.configure(FORCED["addr"][0], FORCED["addr"][1], os.environ.get("LEMONADE_API_KEY", ""))
    else:
        apply_mode(s)
    print(f"Zest {VERSION} on {url}  ->  Lemonade {effective_mode(s)} ({'%s:%s' % lem.target()})", flush=True)

    gateway.ensure_key(s)
    gateway.GATEWAY.start(s["gateway"])
    if gateway.GATEWAY.error:
        print("Other apps can't connect yet: " + gateway.GATEWAY.error, flush=True)

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
        agent.stop_all()
        gateway.GATEWAY.stop()
        srv.server_close()        # free the port first; stopping Lemonade can take a few seconds
        engine.stop()


if __name__ == "__main__":
    main()
