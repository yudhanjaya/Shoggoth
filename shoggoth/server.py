#!/usr/bin/env python3
"""Shoggoth: runs models on this computer with its own llama.cpp, and connects the servers you already run.

Serves the single-page UI, the OpenAI-style /api/v1/* door its own chat and Pi use (each model goes to the engine that serves it, see
gateway.py) and /ui/* helpers: GPU telemetry, Shoggoth's llama.cpp (build, backend, device, start, update), where models live, and logs.

Standard library only. Listens on 127.0.0.1 unless told otherwise.
"""
import argparse
import hashlib
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

import store       # noqa: E402  first: it maps old ZEST_* variables to their SHOGGOTH_* names
import agent       # noqa: E402
import folders     # noqa: E402
import gateway     # noqa: E402
import hardware    # noqa: E402
import hf          # noqa: E402
import library     # noqa: E402
import llamaargs   # noqa: E402
import llamacpp    # noqa: E402
import modelscan   # noqa: E402
import piup        # noqa: E402
import provbase    # noqa: E402
import recs        # noqa: E402
import scan        # noqa: E402
import registry    # noqa: E402
import router      # noqa: E402
import skills      # noqa: E402
import servers     # noqa: E402
import modelmeta  # noqa: E402
import tuning      # noqa: E402
import uiauth      # noqa: E402
import util        # noqa: E402
import websearch   # noqa: E402
import zestup      # noqa: E402
from util import cached, invalidate, run   # noqa: E402

VERSION = "4.3.0"
hf.VERSION = VERSION          # Hugging Face sees User-Agent: Shoggoth/<version>
_last_ping = {"t": None}
import speech      # noqa: E402  (speech to text with whisper.cpp)
import lan         # noqa: E402  (LAN sharing and pairing; its /ui/lan/* routes are lan.handle)
lan.VERSION = VERSION


class Tabs:
    """Which browser tabs are open, for app mode. A tab pings while it is open and says goodbye as it closes, so the last one closing
    quits Shoggoth after `leave` seconds (long enough for a reload to come back). A tab that vanished without a goodbye, like a crashed
    browser, times out after `grace` seconds."""

    def __init__(self, leave=2, grace=180):
        self.leave, self.grace, self.seen, self.left, self.lock = leave, grace, {}, None, threading.Lock()

    def ping(self, tab, now=None):
        with self.lock:
            self.seen[str(tab or "")], self.left = now or time.time(), None

    def bye(self, tab, now=None):
        with self.lock:
            self.seen.pop(str(tab or ""), None)
            if not self.seen:
                self.left = now or time.time()

    def should_quit(self, now=None, started=0):
        now = now or time.time()
        with self.lock:
            for t, at in [(t, at) for t, at in self.seen.items() if now - at > self.grace]:
                del self.seen[t]
                self.left = max(self.left or 0, at + self.grace)           # it was last heard from `grace` seconds after its last ping
            if self.seen:
                return False
            if self.left is not None:
                return now - self.left >= self.leave
            return now - started > 300


TABS = Tabs()
LOOPBACK_ONLY = {"on": True}


# ---------------------------------------------------------------- which engine
def effective_mode(s):
    """"builtin": Shoggoth runs its own llama.cpp. "connected": only the servers the person connected (they turned llama.cpp off, or chose
    so on the first-run screen)."""
    return "connected" if s.get("mode") in ("connected", "existing") else "builtin"


def _provider():
    return registry.default().get("llamacpp")


def _start_engine():
    """Start Shoggoth's llama.cpp (no model loaded) so the model list is there. Does nothing when it is off, not set up yet, or running."""
    p = _provider()
    if p is None or llamacpp.running() or not llamacpp.choose():
        return False
    try:
        return p.start()
    except Exception as e:  # noqa: BLE001 - never stop Shoggoth starting over the engine
        sys.stderr.write("Shoggoth: llama.cpp didn't start: %r\n" % e)
        return False


def apply_mode(s):
    """Start Shoggoth's llama.cpp in the background when it is the engine here; stop it when the person chose connected servers only."""
    if effective_mode(s) == "builtin":
        threading.Thread(target=_start_engine, daemon=True).start()
    else:
        llamacpp.stop()
    invalidate()


def current_mode():
    return effective_mode(store.load())


# ---------------------------------------------------------------- state for the setup screen
def setup_state():
    s = store.load()
    return {
        "setup_done": bool(s["setup_done"]),
        "mode": effective_mode(s), "mode_saved": s["mode"],
        "engine": llamacpp.summary(s),
        "connected": len(servers.all()),
        "models": {"storage": s["models"]["storage"], "custom_dir": s["models"]["custom_dir"],
                   "hf_dir": modelscan.hf_cache_dir(), "imports": s["models"]["imports"], "view": llamacpp.models_dir()},
        "gpus_found": util.cached("gpus_found", 30, hardware.detect_gpus),
    }


def read_logs(n):
    try:
        with open(llamacpp.log_path(), errors="replace") as f:
            return [l.rstrip("\n") for l in f.readlines()[-n:]]
    except OSError:
        return ["llama.cpp hasn't written a log yet."]


def own_source(sources):
    """Shoggoth's own llama.cpp, as the first entry of /ui/sources (the same shape as a connected server)."""
    up = llamacpp.running()
    ms = llamacpp.models() if up else []
    b = llamacpp.choose()
    state = "ready" if up else "off" if current_mode() != "builtin" else "starting" if b else "not_installed"
    me = {"id": "llamacpp", "kind": "builtin", "name": "llama.cpp", "engine": "llamacpp",
          "url": "http://127.0.0.1:%d" % llamacpp._state["port"] if up else "", "state": state, "models": len(ms),
          "loaded": [m["id"] for m in ms if m["status"] == "loaded"], "loads": "router", "leaves_computer": False, "own": True,
          "version": (b or {}).get("tag", ""), "backend": (b or {}).get("backend", ""), "error": llamacpp._state.get("error")}
    return dict(sources, sources=[me] + list(sources.get("sources") or []))


def get_info():
    return hardware.info(current_mode(), VERSION)


# ---------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    server_version = "Shoggoth/" + VERSION

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

    def authed(self):
        auth = self.headers.get("Authorization")
        if not auth and urlparse(self.path).path.startswith("/api/"):
            # Pi's Anthropic and Google clients send the key in their own header
            k = self.headers.get("x-api-key") or self.headers.get("x-goog-api-key")
            auth = "Bearer " + k if k else None
        return uiauth.allowed(self.server.server_address[1], self.headers.get("Cookie"), auth)

    def _refuse(self, page=False):
        if page:
            self.send_response(401)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(uiauth.LOCKED_PAGE)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(uiauth.LOCKED_PAGE)
        else:
            self.send_json({"error": "not signed in: open Shoggoth from its own window"}, 401)

    def _let_in(self):
        """The window Shoggoth opened came with a one-time code: swap it for the cookie and show the page on a clean address."""
        self.send_response(302)
        self.send_header("Set-Cookie", uiauth.set_cookie(self.server.server_address[1]))
        self.send_header("Location", "/")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _info(self):
        return cached("info", 4, get_info)

    # ---- GET
    def do_GET(self):
        if not self.host_ok():
            return self.send_json({"error": "unexpected Host header"}, 403)
        u = urlparse(self.path)
        p = u.path
        if p == "/ui/ping":                                       # the version only: how a second launch finds this Shoggoth
            return self.send_json({"zest": VERSION, "profile": profile_id()})
        if p == "/" and uiauth.enabled() and uiauth.redeem(parse_qs(u.query).get("c", [""])[0]):
            return self._let_in()
        if not self.authed():
            return self._refuse(page=p in ("/", "/index.html"))
        if p.startswith("/ui/lan/"):                              # LAN sharing and pairing (lan.py)
            return self.send_json(*reversed(lan.handle("GET", p, {})))
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
            self.send_json({"gpus": library.models_on_cards(hardware.all_gpus()), "t": time.time()})
        elif p == "/ui/speech/status":
            self.send_json(speech.status())
        elif p == "/ui/info":
            self.send_json(self._info())
        elif p == "/ui/backends":
            self.send_json({"recipes": cached("backends", 4, hardware.backends)})
        elif p == "/ui/config":
            self.send_json({"config": (store.load().get("llamacpp") or {}), "engine": llamacpp.summary()})
        elif p == "/ui/args/schema":
            schema = llamaargs.args_schema(self._info().get("llama_bin"))
            self.send_json({"entries": schema or [], "verified": bool(schema)})
        elif p == "/ui/setup":
            self.send_json(setup_state())
        elif p == "/ui/engine/update":
            self.send_json(dict(llamacpp.UPD))
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
            self.send_json({"items": gateway.REC.activity(), "last": gateway.REC.last_speed()})
        elif p == "/ui/cowork":
            self.send_json(agent.state())
        elif p == "/ui/pi":
            self.send_json(piup.summary())
        elif p == "/ui/picker":
            self.send_json(library.picker(0 if parse_qs(u.query).get("fresh") else 8))
        elif p == "/ui/library":
            self.send_json(self.library_summary())
        elif p == "/ui/disk":
            self.send_json(library.models_disk())
        elif p == "/ui/library/places":
            self.send_json({"places": library.places(), "root": library.root()})
        elif p == "/ui/router":
            self.send_json({"state": router.state(), "models": router.chat_models()})
        elif p == "/ui/providers":
            self.send_json(registry.default().summary())
        elif p == "/ui/servers":
            self.send_json({"presets": servers.presets(), "servers": servers.public()})
        elif p == "/ui/sources":
            self.send_json(own_source(scan.sources(own_ports=self._own_ports())))
        elif p == "/ui/tuning":
            self.send_json({"targets": tuning.targets(), "job": tuning.status()})
        elif p == "/ui/tuning/job":
            self.send_json(tuning.status())
        elif p == "/ui/recommendations":
            self.send_json(recs.summary())
        elif p == "/ui/update":
            self.send_json(zestup.status())
        elif p == "/ui/skills":
            self.send_json({"sets": skills.scan()})
        elif p == "/ui/skills/own":
            self.send_json({"skills": skills.own_list()})
        elif p == "/ui/search":
            self.get_search(parse_qs(u.query))
        elif p == "/ui/model/headroom":
            self.send_json(modelmeta.headroom_table())
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
        if not self.authed():
            return self._refuse()
        if p == "/ui/ping":                                       # only the real page keeps Shoggoth open
            _last_ping["t"] = time.time()
            TABS.ping(self.headers.get("X-Tab"))
            return self.send_json({"zest": VERSION, "profile": profile_id()})
        if p == "/ui/bye":                                        # a tab says it is closing (sent by the page as it unloads)
            TABS.bye(self.headers.get("X-Tab") or urlparse(self.path).query.partition("tab=")[2])
            return self.send_json({"ok": True})
        if p == "/ui/code":                                       # a second launch asks the running Shoggoth for a way in (it holds the secret)
            return self.send_json({"code": uiauth.new_code()})
        if not self.same_origin():
            return self.send_json({"error": "cross-origin request refused"}, 403)
        if p.startswith("/api/"):
            return self.proxy()
        if p.startswith("/ui/lan/"):                              # LAN sharing and pairing (lan.py)
            return self.send_json(*reversed(lan.handle("POST", p, self.read_json())))
        if p == "/ui/speech/transcribe":                          # the mic button: the body is the audio itself (speech.py)
            n = int(self.headers.get("Content-Length") or 0)
            if n > 64_000_000:
                return self.send_json({"error": "That recording is too long."}, 413)
            status, obj = speech.ui_transcribe(self.rfile.read(n) if n else b"", self.headers.get("Content-Type", ""))
            return self.send_json(obj, status)
        handler = {
            "/ui/device": self.post_device, "/ui/config": self.post_config,
            "/ui/args/parse": self.post_args_parse, "/ui/args/check": self.post_args_check,
            "/ui/backend": self.post_backend, "/ui/backend/install": self.post_backend_install,
            "/ui/setup/mode": self.post_mode, "/ui/setup/done": self.post_done,
            "/ui/engine/check": self.post_engine_check, "/ui/engine/update": self.post_engine_update,
            "/ui/engine/use": self.post_engine_use, "/ui/engine/restart": self.post_engine_restart,
            "/ui/sources/scan": self.post_sources_scan,
            "/ui/gateway": self.post_gateway, "/ui/cowork/new": self.post_cowork_new,
            "/ui/cowork/cmd": self.post_cowork_cmd, "/ui/cowork/answer": self.post_cowork_answer,
            "/ui/cowork/mode": self.post_cowork_mode, "/ui/cowork/delete": self.post_cowork_delete,
            "/ui/cowork/rename": self.post_cowork_rename, "/ui/cowork/model": self.post_cowork_model,
            "/ui/search/settings": self.post_search_settings, "/ui/search/test": self.post_search_test, "/ui/model/load": self.post_model_load, "/ui/model/unload": self.post_model_unload, "/ui/model/ctx": self.post_model_ctx,
            "/ui/cowork/job/new": self.post_job_new, "/ui/cowork/job/ctl": self.post_job_ctl,
            "/ui/cowork/job/apply": self.post_job_apply, "/ui/cowork/job/discard": self.post_job_discard,
            "/ui/cowork/folders": self.post_cowork_folders, "/ui/cowork/seen": self.post_cowork_seen,
            "/ui/cowork/allow/remove": self.post_allow_remove, "/ui/cowork/folder": self.post_cowork_folder, "/ui/skills/install": self.post_skills_install, "/ui/pi/check": self.post_pi_check,
            "/ui/pi/update": self.post_pi_update, "/ui/library/root": self.post_library_root, "/ui/library/folder": self.post_library_folder, "/ui/providers/approve": self.post_provider_approve, "/ui/providers/check": self.post_provider_check, "/ui/providers/update": self.post_provider_update, "/ui/providers/stop": self.post_provider_stop, "/ui/providers/reload": self.post_provider_reload, "/ui/servers": self.post_servers, "/ui/tuning/plan": self.post_tuning_plan, "/ui/model/meta": self.post_model_meta, "/ui/tuning/run": self.post_tuning_run, "/ui/tuning/cancel": self.post_tuning_cancel, "/ui/tuning/action": self.post_tuning_action, "/ui/update/all": self.post_update_all, "/ui/update/restart": self.post_update_restart, "/ui/pi/use": self.post_pi_use,
            "/ui/skills/remove": self.post_skills_remove, "/ui/skills/toggle": self.post_skills_toggle,
            "/ui/skills/own/save": self.post_own_save, "/ui/skills/own/delete": self.post_own_delete,
            "/ui/chat/make-skill": self.post_make_skill,
            "/ui/models/storage": self.post_storage, "/ui/models/imports": self.post_imports,
        }.get(p)
        if not handler:
            return self.send_json({"error": "not found"}, 404)
        try:
            handler(self.read_json())
        except ValueError as e:
            self.send_json({"ok": False, "error": str(e)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            raise
        except Exception as e:  # noqa: BLE001 - a bad request must never drop the connection or leave the page waiting
            sys.stderr.write("Shoggoth: %s failed: %r\n" % (p, e))
            try:
                self.send_json({"ok": False, "error": "Something went wrong with that request. If it keeps happening, restart Shoggoth."}, 500)
            except OSError:
                pass

    do_DELETE = do_POST

    # ---- Shoggoth's llama.cpp: device, backend, build, how many models stay loaded
    @staticmethod
    def _set_llamacpp(**kw):
        store.update(lambda st: st.setdefault("llamacpp", {}).update(kw))
        invalidate()

    @staticmethod
    def _restart_engine():
        """Restart the router with the new settings (in the background; models load again on their next use)."""
        if current_mode() == "builtin" and llamacpp.running():
            threading.Thread(target=llamacpp.restart, daemon=True).start()
            time.sleep(0.3)
            return True
        return False

    def post_device(self, body):
        dev = body.get("device", "")
        if not isinstance(dev, str) or not re.fullmatch(r"([a-z]+\d+(,[a-z]+\d+)*)?", dev):
            return self.send_json({"error": "device must look like cuda1 or cuda0,cuda1 (empty = automatic)"}, 400)
        self._set_llamacpp(device=dev.upper())
        self.send_json({"ok": True, "restarted": self._restart_engine()})

    def post_config(self, body):
        """Change Shoggoth's llama.cpp settings: max_loaded (keep up to N models loaded), enabled."""
        kw = {}
        if "max_loaded" in body:
            try:
                kw["max_loaded"] = max(1, min(8, int(body["max_loaded"])))
            except (TypeError, ValueError):
                raise ValueError("Keep up to how many models loaded? Give a number from 1 to 8.")
        if "enabled" in body:
            kw["enabled"] = bool(body["enabled"])
        if not kw:
            raise ValueError("Nothing to change.")
        self._set_llamacpp(**kw)
        if kw.get("enabled") is False:
            llamacpp.stop()
            registry.reload()
            return self.send_json({"ok": True, "config": store.load()["llamacpp"]})
        registry.reload()
        self.send_json({"ok": True, "restarted": self._restart_engine(), "config": store.load()["llamacpp"]})

    def post_args_parse(self, body):
        self.send_json(llamaargs.parse_args(str(body.get("args", "")), self._info().get("llama_bin")))

    def post_args_check(self, body):
        self.send_json(llamaargs.check_args(str(body.get("args", "")), self._info().get("llama_bin")))

    def post_backend(self, body):
        """Use another installed build: backend (cpu, vulkan, cuda12, cuda13, rocm, or auto for the newest build here)."""
        backend = str(body.get("backend", ""))
        if backend == "auto":
            backend = ""
        elif not any(b["backend"] == backend for b in llamacpp.builds()):
            return self.send_json({"error": "That build isn't installed. Download it first."}, 400)
        self._set_llamacpp(backend=backend, tag="")
        self.send_json({"ok": True, "restarted": self._restart_engine(), "engine": llamacpp.summary()})

    def post_backend_install(self, body):
        """Download the newest build for a backend (in the background; progress at GET /ui/engine/update)."""
        backend = str(body.get("backend") or "") or llamacpp.recommended()
        if backend not in llamacpp.ASSETS:
            return self.send_json({"error": "Shoggoth doesn't know that build."}, 400)
        started = llamacpp.start_update(backend)
        self.send_json({"ok": True, "started": started, "update": dict(llamacpp.UPD)})

    # ---- setup: which engine
    def post_mode(self, body):
        mode = body.get("mode")
        if mode not in ("builtin", "connected"):
            raise ValueError("Unknown choice.")
        s = store.load()
        s["mode"] = mode
        store.save(s)
        apply_mode(s)
        self.send_json(setup_state())

    def post_done(self, body):
        s = store.load()
        s["setup_done"] = bool(body.get("done", True))
        store.save(s)
        self.send_json({"ok": True})

    def post_engine_check(self, body):
        try:
            rel = llamacpp.latest()
        except RuntimeError as e:
            return self.send_json({"ok": False, "error": str(e)}, 502)
        b = llamacpp.choose()
        newer = not b or b.get("bundled") or llamacpp._tag_num(rel["tag_name"]) > llamacpp._tag_num(b["tag"])
        self.send_json({"ok": True, "latest": rel["tag_name"], "current": (b or {}).get("tag", ""), "update_available": bool(newer)})

    def post_engine_update(self, body):
        self._set_llamacpp(tag="")              # asking for an update means "use the newest"
        started = llamacpp.start_update(str(body.get("backend") or "") or None)
        self.send_json({"started": started, **llamacpp.UPD})

    def post_engine_use(self, body):
        """Go back to (or forward to) another installed build: {tag, backend}."""
        tag, backend = str(body.get("version") or body.get("tag") or ""), str(body.get("backend") or "")
        have = llamacpp.builds()
        if tag and not any(b["tag"] == tag and (not backend or b["backend"] == backend) for b in have):
            raise ValueError("That version isn't installed.")
        backend = backend or next((b["backend"] for b in have if b["tag"] == tag), "")
        self._set_llamacpp(tag=tag, backend=backend)
        self.post_engine_restart({})

    def post_engine_restart(self, body):
        s = store.load()
        if effective_mode(s) == "builtin":
            threading.Thread(target=lambda: (llamacpp.stop(), _start_engine()), daemon=True).start()
            time.sleep(0.3)
        self.send_json({"ok": True, "engine": llamacpp.summary(s)})

    # ---- where models live
    def post_storage(self, body):
        s = store.load()
        modelscan.set_storage(s, body.get("kind"), body.get("path"))
        store.save(s)
        ok, msg = library.reload_engine()
        invalidate()
        self.send_json({"ok": ok, "message": msg, "models": setup_state()["models"]})

    def post_imports(self, body):
        s = store.load()
        chosen, seen = [], set()
        for src in body.get("sources") if isinstance(body.get("sources"), list) else []:
            if not isinstance(src, dict):
                continue
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
        before = len(llamacpp.models())
        s["models"]["imports"] = chosen
        store.save(s)
        library._pk["v"] = None
        ok, msg = library.reload_engine()                  # the view shows llama.cpp what it can run; the router reads it again
        invalidate()
        self.send_json({"ok": ok, "error": msg, "linked": len(chosen), "listed_before": before, "listed_after": len(llamacpp.models()),
                        "models": setup_state()["models"]})

    # ---- the OpenAI-style door under /api (Shoggoth's own chat and Pi): each model goes to the engine that serves it
    def proxy(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        path = gateway.norm(self.path)
        if not gateway.ALLOW.match(path):
            return self.send_json({"error": {"message": "Shoggoth doesn't forward %s." % path, "type": "not_found"}}, 404)
        rec = None
        if self.command == "POST" and gateway.INFER.match(path):      # Shoggoth's own chat shows up on the Server page too
            model, stream = gateway.describe(body, path)
            rec = gateway.REC.start("Shoggoth", "zest", self.command, path, model, stream, "local")
        gateway.relay(self, self.command, self.path, body, rec, store.load()["gateway"]["capture"] if rec else False, merge_models=True)

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
            g["api_key"] = "shoggoth-" + store.new_key()
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

    # ---- web search for the chat (asked first by the approval gate in Pi; Shoggoth only does the fetching)
    def get_search(self, q):
        if not agent.search_enabled():
            return self.send_json({"error": "Web search is switched off in Shoggoth."}, 403)
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
            r = websearch.search("shoggoth", 3, agent.search_config())
            self.send_json({"ok": True, "message": "It works: %d results, the first from %s." % (len(r), urllib.parse.urlparse(r[0]["url"]).netloc)})
        except websearch.SearchError as e:
            self.send_json({"ok": False, "need_key": e.need_key, "message": str(e)})

    # ---- the model in memory (the chat bar)
    def post_model_load(self, body):
        """Load (or reload) a model at the chat context size. A size given here becomes the new chat setting."""
        model = str(body.get("model", "")).strip()
        ctx = None
        if body.get("ctx"):
            ctx = agent.set_chat_ctx(body["ctx"] if body["ctx"] == "auto" else int(body["ctx"]))
        ok, msg, got = agent.load_model(model, ctx, bool(body.get("force")))
        invalidate("info")
        self.send_json({"ok": ok, "error": msg, "ctx": got}, 200 if ok else 502)

    def post_model_unload(self, body):
        ok, msg = agent.unload_model(str(body.get("model", "")).strip())
        invalidate("info")
        self.send_json({"ok": ok, "error": msg}, 200 if ok else 409)

    def post_model_ctx(self, body):
        """Only remember the context size, for when the model is loaded next."""
        v = body.get("ctx") or "auto"
        self.send_json({"ok": True, "ctx": agent.set_chat_ctx(v if v == "auto" else int(v))})

    def post_pi_check(self, body):
        try:
            self.send_json(piup.check())
        except RuntimeError as e:
            self.send_json({"error": str(e)}, 502)

    def post_pi_update(self, body):
        self.send_json({"started": piup.start_update(), "update": dict(piup.UPD)})

    @staticmethod
    def library_summary():
        d = library.summary()
        d["views"] = library.sync_views(d["models"]) if d["chosen"] else {}        # only links are ever written
        return d

    def post_library_folder(self, body):
        """Add (or, with `remove`, drop) one folder the library should read, keeping the others."""
        cur = [i for i in (store.load().get("models") or {}).get("imports", [])]
        if body.get("remove"):
            keep = [i for i in cur if i.get("id") != body["remove"]]
        elif body.get("kind") == "ollama":
            path = str(body.get("path", ""))
            keep = [i for i in cur if i.get("path") != path] + [{"id": modelscan._source_id("ollama", path), "kind": "ollama", "label": "Ollama", "path": path}]
        else:
            src = modelscan.source_from_path(str(body.get("path", "")), body.get("label"))
            keep = [i for i in cur if i.get("id") != src["id"]] + [{k: src[k] for k in ("id", "kind", "label", "path")}]
        self.post_imports({"sources": keep})

    def post_library_root(self, body):
        library.set_root(body.get("path", ""))
        threading.Thread(target=library.reload_engine, daemon=True).start()        # the router reads the new library through its view
        self.send_json(self.library_summary())

    @staticmethod
    def _provider(body):
        p = registry.default().get(str(body.get("id", "")))
        if not p:
            raise ValueError("No such engine.")
        return p

    def post_provider_check(self, body):
        """Is a newer version of this engine available? One answer shape for every engine."""
        p = self._provider(body)
        try:
            c = p.update_check()
        except NotImplementedError:
            return self.send_json({"supported": False, "available": False})
        except provbase.ProviderError as e:
            return self.send_json({"supported": True, "available": False, "error": str(e)})
        d = c.get("detail") or {}
        latest = d.get("latest") or (d.get("engine") or {}).get("offered") or ""
        self.send_json({"supported": c.get("supported", True), "available": bool(c.get("available")), "deferred": bool(c.get("deferred")), "major": bool(c.get("major")),
                        "latest": latest, "note": c.get("note", "")})

    def post_provider_update(self, body):
        p = self._provider(body)
        try:
            started = bool(p.update(lambda t: None))
        except NotImplementedError:
            raise ValueError("That engine can't be updated from here.")
        except provbase.ProviderError as e:
            raise ValueError(str(e))
        self.send_json({"started": started, "update": p.update_status()})

    def post_provider_stop(self, body):
        """Stop what Shoggoth started for this engine. Never stops one the person started."""
        p = self._provider(body)
        try:
            ok, msg = p.stop()
        except NotImplementedError:
            ok, msg = False, "That engine can't be stopped from here."
        invalidate("info")
        self.send_json({"ok": ok, "error": msg}, 200 if ok else 409)

    def post_provider_approve(self, body):
        registry.default().approve(str(body.get("id", "")))
        self.send_json(registry.default().summary())

    def post_provider_reload(self, body):
        self.send_json(registry.reload().summary())

    def post_sources_scan(self, body):
        """Look at what is listening on this computer again (the Look again button) and return the same shape as GET /ui/sources."""
        self.send_json(own_source(scan.sources(own_ports=self._own_ports(), fresh=True)))

    def _own_ports(self):
        """This Shoggoth's port and its llama.cpp's (the router's and each loaded model's), never offered as a server to connect."""
        return tuple({self.server.server_address[1]} | llamacpp.own_ports())

    def post_servers(self, body):
        """Probe and connect, or remove, a server the person runs themselves. Shoggoth only ever talks to these; it never starts or stops them."""
        act = body.get("action")
        if act == "probe":
            result = dict(servers.connect_found(typed={"preset": str(body.get("preset", "")), "url": str(body.get("url", "")), "key": str(body.get("key", "")).strip()}), ok=True)
        elif act == "remove":
            servers.remove(str(body.get("id", "")))
            result = {"ok": True}
        else:
            raise ValueError("Choose to probe and connect, or to remove a server.")
        registry.reload()
        router.invalidate()
        self.send_json(dict(result, servers=servers.public()))

    @staticmethod
    def _tune_args(body):
        engine, target = str(body.get("engine", "")), str(body.get("target", ""))
        if engine != "llamacpp" or not target:
            raise ValueError("Choose something to tune.")
        return engine, target, {k: body.get(k) for k in ("mode", "use", "goal", "kind")}

    def post_tuning_plan(self, body):
        self.send_json(tuning.plan(*self._tune_args(body)))

    def post_tuning_run(self, body):
        tuning.start(*self._tune_args(body))
        self.send_json({"ok": True, "job": tuning.status()})

    def post_model_meta(self, body):
        """Shoggoth's own notes for a model: today the GPU memory to leave free for the system."""
        model = str(body.get("model", ""))
        if not model:
            raise ValueError("Choose a model.")
        try:
            gb = None if body.get("headroom_gb") in (None, "") else max(0.0, min(64.0, float(body["headroom_gb"])))
        except (TypeError, ValueError):
            raise ValueError("Headroom is a number of gigabytes.")
        self.send_json({"ok": True, "meta": modelmeta.update(model, headroom_gb=gb)})

    def post_tuning_cancel(self, body):
        self.send_json({"ok": tuning.cancel()})

    def post_tuning_action(self, body):
        engine, target, _ = self._tune_args(body)
        ok, msg = tuning.action(engine, target, str(body.get("act", "")))
        self.send_json({"ok": ok, "error": msg, "targets": tuning.targets()}, 200 if ok else 409)

    def post_update_all(self, body):
        self.send_json({"started": zestup.start_all(VERSION, current_mode()), **zestup.status()})

    def post_update_restart(self, body):
        zestup.restart(zestup.UPD.get("path"))
        self.send_json({"ok": True})
        threading.Timer(0.5, os.kill, args=(os.getpid(), signal.SIGTERM)).start()

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

    def post_own_save(self, body):
        r = skills.own_save(body.get("name"), body.get("text"))
        self.send_json(dict(r, ok=True, skills=skills.own_list()))

    def post_own_delete(self, body):
        r = skills.own_delete(body.get("name"))
        self.send_json(dict(r, ok=True, skills=skills.own_list()))

    def post_make_skill(self, body):
        self.send_json(agent.make_skill(str(body.get("chat", ""))))

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


def open_window(host, port):
    """Open a browser on a Shoggoth that is already running, with a one-time way in. Returns the address that was opened."""
    url = "http://%s:%d/" % (host, port)
    try:
        c = http.client.HTTPConnection(host, port, timeout=3)
        c.request("POST", "/ui/code", body="{}", headers={"Authorization": "Bearer " + uiauth.token(), "Content-Type": "application/json"})
        code = json.loads(c.getresponse().read()).get("code")
        if code:
            url += "?c=" + code
    except (OSError, ValueError, AttributeError):
        pass                                                      # it will show the "start Shoggoth again" page, which says what to do
    webbrowser.open(url)
    return url


def profile_id():
    """Who this Shoggoth belongs to: an id made from its data folder, so two profiles on one computer can tell their Shoggoths apart."""
    return hashlib.sha256(os.path.abspath(store.data_dir()).encode()).hexdigest()[:12]


def zest_info(host, port):
    """What the Shoggoth answering on this port says about itself ({zest: version, profile: id}), or None when it isn't Shoggoth."""
    try:
        c = http.client.HTTPConnection(host, port, timeout=1.5)
        c.request("GET", "/ui/ping")
        info = json.loads(c.getresponse().read())
        return info if isinstance(info, dict) and info.get("zest") else None
    except (OSError, ValueError, AttributeError):
        return None


def choose_port(port, version, profile, probe, taken):
    """("reuse", port) when this profile's own Shoggoth of this version is already running on `port` or one of the next ten, else
    ("start", the port to start on). Another profile's Shoggoth, or an older one, is left alone and the next free port is used."""
    first = probe(port)
    if not first:
        return "start", port                                        # free, or something that isn't Shoggoth (starting will say so)
    mine = [p for p in range(port, port + 11) if (probe(p) or {}).get("zest") == version and (probe(p) or {}).get("profile") == profile]
    if mine:
        return "reuse", mine[0]
    for p in range(port + 1, port + 11):
        if not probe(p) and not taken(p):
            return "start", p
    return "start", port


def watchdog(srv, grace):
    """App mode: quit once the last browser tab has closed (a few seconds), or has been gone for `grace` seconds without saying goodbye."""
    TABS.grace = grace
    started = time.time()
    while True:
        time.sleep(1)
        if agent.busy():
            continue              # a job is running or waiting on you: stay open even with the tab closed
        if TABS.should_quit(started=started):
            srv.shutdown()
            return


def main():
    args = [a for a in sys.argv[1:] if a != "--app"]            # the AppImage adds --app; `Shoggoth.AppImage providers check <folder>` is the tool for engine authors
    if args[:1] == ["providers"]:
        import provcheck
        sys.exit(provcheck.main(args[1:]))
    if args[:1] == ["recommendations"]:
        sys.exit(recs.main(args[1:]))
    if args[:1] == ["engines"]:
        import engines
        sys.exit(engines.main(args[1:]))
    ap = argparse.ArgumentParser(description="Shoggoth: runs models on this computer with llama.cpp")
    ap.add_argument("--host", default=os.environ.get("SHOGGOTH_HOST", "127.0.0.1"), help="address to listen on (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("SHOGGOTH_PORT", "13306")), help="port to listen on (default 13306)")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    ap.add_argument("--app", action="store_true", help="desktop mode: quit automatically a few minutes after the browser tab is closed")
    ap.add_argument("--version", action="version", version="Shoggoth " + VERSION)
    a = ap.parse_args()

    LOOPBACK_ONLY["on"] = a.host in ("127.0.0.1", "localhost", "::1")
    shown = "127.0.0.1" if a.host in ("0.0.0.0", "::") else a.host
    url = f"http://{shown}:{a.port}"

    def taken(p):
        try:
            socket.create_connection((shown, p), timeout=0.3).close()
            return True
        except OSError:
            return False
    action, port = choose_port(a.port, VERSION, profile_id(), lambda p: zest_info(shown, p), taken)
    if action == "reuse":  # a second launch: just show the one that is already running
        print(f"Shoggoth is already running at http://{shown}:{port}")
        if not a.no_browser:
            open_window(shown, port)
        return
    if port != a.port:
        print(f"Another Shoggoth is already on port {a.port}, so Shoggoth {VERSION} is starting on port {port}.", flush=True)
        a.port, url = port, f"http://{shown}:{port}"
    try:
        srv = ThreadingHTTPServer((a.host, a.port), Handler)
    except OSError as e:
        sys.exit(f"Can't listen on {a.host}:{a.port}: {e}\nTry --port.")
    srv.daemon_threads = True

    try:
        servers.migrate_strata()          # a Strata typed in before Strata was removed becomes a connected server
    except Exception as e:  # noqa: BLE001 - never stop Shoggoth starting over old settings
        sys.stderr.write("Shoggoth: couldn't carry the saved Strata servers over: %r\n" % e)
    agent.set_zest_port(a.port)
    agent.recover()
    piup.on_installed.append(agent.restart_idle)
    threading.Thread(target=agent.reaper, daemon=True).start()
    threading.Thread(target=router.watch, daemon=True).start()
    try:
        import migrate5
        migrate5.run(store.data_dir(), library.root())       # a 4.x profile's Lemonade models, once (it can run again safely)
    except Exception as e:  # noqa: BLE001 - never stop Shoggoth starting over old settings
        sys.stderr.write("Shoggoth: couldn't carry the 4.x models over: %r\n" % e)
    s = store.load()
    apply_mode(s)
    b = llamacpp.choose(s)
    eng = ("llama.cpp %s (%s)" % (b["tag"], llamacpp.NAMES[b["backend"]]) if b else "llama.cpp, not set up yet") \
        if effective_mode(s) == "builtin" else "connected servers only"
    print(f"Shoggoth {VERSION} on {url}  ->  {eng}", flush=True)

    uiauth.token()                       # make the secret now, so a second launch can read it
    gateway.ensure_key(s)
    gateway.GATEWAY.start(s["gateway"])
    if gateway.GATEWAY.error:
        print("Other apps can't connect yet: " + gateway.GATEWAY.error, flush=True)

    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(0)))   # so Shoggoth's llama.cpp is stopped too
    if a.app:
        threading.Thread(target=watchdog, args=(srv, int(os.environ.get("SHOGGOTH_APP_GRACE", "180"))), daemon=True).start()
    if not a.no_browser:
        threading.Timer(0.6, webbrowser.open, args=(url + "/?c=" + uiauth.new_code() if uiauth.enabled() else url,)).start()
    try:
        srv.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        agent.stop_all()
        gateway.GATEWAY.stop()
        router.stop_started()     # what Shoggoth started goes with it; nothing keeps holding memory with no one left to stop it
        srv.server_close()        # free the port first; stopping llama.cpp can take a few seconds
        llamacpp.stop()


if __name__ == "__main__":
    main()
