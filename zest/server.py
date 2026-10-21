#!/usr/bin/env python3
"""Zest: a local console for Lemonade Server.

Serves the single-page UI, proxies /api/* to lemond (so no CORS setup is
needed) and exposes a few /ui/* helpers: GPU telemetry from nvidia-smi, device
and backend configuration through the `lemonade` CLI, and the service log.

Standard library only. Binds to 127.0.0.1 unless told otherwise.
"""
import argparse
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

VERSION = "1.1.0"
HERE = os.path.dirname(os.path.abspath(__file__))

CFG = {
    "lemonade": (os.environ.get("LEMONADE_HOST", "127.0.0.1"), int(os.environ.get("LEMONADE_PORT", "13305"))),
    "api_key": os.environ.get("LEMONADE_API_KEY", ""),
    "cli": os.environ.get("LEMONADE_BIN") or shutil.which("lemonade") or "lemonade",
}
_cache = {}
_lock = threading.Lock()
_last_ping = {"t": None}


def cached(key, ttl, fn):
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (time.time(), val)
    return val


def invalidate(*keys):
    with _lock:
        for k in keys or list(_cache):
            _cache.pop(k, None)


def run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def lemonade_cli(*args, timeout=15):
    host, port = CFG["lemonade"]
    cmd = [CFG["cli"], "--host", host, "--port", str(port)]
    if CFG["api_key"]:
        cmd += ["--api-key", CFG["api_key"]]
    return run(cmd + list(args), timeout)


def lemonade_get(path):
    host, port = CFG["lemonade"]
    conn = http.client.HTTPConnection(host, port, timeout=8)
    try:
        h = {"Authorization": "Bearer " + CFG["api_key"]} if CFG["api_key"] else {}
        conn.request("GET", path, headers=h)
        r = conn.getresponse()
        return json.loads(r.read() or b"{}") if r.status == 200 else {}
    except (OSError, ValueError):
        return {}
    finally:
        conn.close()


# ---------------------------------------------------------------- hardware
def nvidia_gpus():
    q = "index,uuid,name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw,power.limit,fan.speed"
    code, out = run(["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits"], 5)
    gpus = []
    if code != 0:
        return gpus

    def num(s):
        try:
            return float(s)
        except ValueError:
            return None

    for line in out.strip().splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) < 10:
            continue
        gpus.append({
            "nv_index": int(p[0]), "uuid": p[1], "name": p[2],
            "mem_used_mb": num(p[3]), "mem_total_mb": num(p[4]),
            "util": num(p[5]), "temp": num(p[6]),
            "power": num(p[7]), "power_limit": num(p[8]), "fan": num(p[9]),
            "procs": [],
        })
    # Which processes hold VRAM on each card (compute clients only; names, not full paths).
    code, out = run(["nvidia-smi", "--query-compute-apps=pid,process_name,gpu_uuid,used_memory",
                     "--format=csv,noheader,nounits"], 5)
    by_uuid = {g["uuid"]: g for g in gpus}
    for line in out.strip().splitlines() if code == 0 else []:
        p = [x.strip() for x in line.split(",")]
        if len(p) < 4 or p[2] not in by_uuid:
            continue
        by_uuid[p[2]]["procs"].append({"pid": int(p[0]) if p[0].isdigit() else None,
                                       "name": os.path.basename(p[1]) or p[1],
                                       "mem_mb": num(p[3]) or 0})
    return gpus


def lemonade_config():
    code, out = lemonade_cli("config", timeout=10)
    cfg = {}
    for m in re.finditer(r"^\s{2}([a-z_0-9]+\.[a-z_0-9]+|[a-z_0-9]+)\s{2,}(.*)$", out, re.M):
        v = m.group(2).strip()
        cfg[m.group(1)] = "" if v == "(empty)" else v
    return cfg


def system_info():
    return cached("sysinfo", 10, lambda: lemonade_get("/api/v1/system-info"))


def list_devices(bin_path):
    """Ask llama.cpp how it numbers devices; this differs from nvidia-smi."""
    if not bin_path or not os.path.isfile(bin_path):
        return None
    code, out = run([bin_path, "--list-devices"], 30)
    devs = []
    for m in re.finditer(r"^\s*([A-Za-z]+\d+):\s*(.+?)\s*\((\d+) MiB", out, re.M):
        devs.append({"id": m.group(1).lower(), "name": m.group(2), "mib": int(m.group(3))})
    return devs


def llama_version(bin_path):
    if not bin_path or not os.path.isfile(bin_path):
        return None
    code, out = run([bin_path, "--version"], 15)
    m = re.search(r"version:\s*(.+)", out)
    return m.group(1).strip() if m else None


def find_llama_bin(cfg, backend):
    path = cfg.get(f"llamacpp.{backend}_bin", "")
    if path and path != "builtin" and os.path.isfile(path):
        return path
    if cfg.get("llamacpp.prefer_system") == "true":
        w = shutil.which("llama-server")
        if w:
            return w
    return None


def info():
    cfg = lemonade_config()
    recipe = system_info().get("recipes", {}).get("llamacpp", {})
    current = cfg.get("llamacpp.backend", "auto") or "auto"
    resolved = recipe.get("default_backend") if current == "auto" else current
    bin_path = find_llama_bin(cfg, resolved) if resolved else None
    devs = cached(f"devs:{bin_path}", 600, lambda: list_devices(bin_path))
    assumed = False
    gpus = nvidia_gpus()
    if devs is None:
        # No binary we can interrogate (e.g. Lemonade's bundled build). For CUDA,
        # fall back to PCI order and say so; other backends need a manual value.
        devs = [{"id": f"cuda{g['nv_index']}", "name": g["name"], "mib": int((g["mem_total_mb"] or 0))}
                for g in gpus] if resolved == "cuda" else []
        assumed = bool(devs)
    used = set()
    for d in devs:  # bind each llama.cpp device to its nvidia-smi entry for live telemetry
        d["nv_index"] = None
        for g in gpus:
            if g["nv_index"] in used:
                continue
            if g["name"] == d["name"] or g["name"] in d["name"] or d["name"] in g["name"]:
                d["nv_index"] = g["nv_index"]
                used.add(g["nv_index"])
                break
    return {
        "device": cfg.get("llamacpp.device", ""),
        "backend": {"current": current, "resolved": resolved},
        "llama_bin": bin_path,
        "llama_version": cached(f"ver:{bin_path}", 600, lambda: llama_version(bin_path)),
        "llama_args": cfg.get("llamacpp.args", ""),
        "devices": devs,
        "devices_assumed": assumed,
        "lemonade": "%s:%s" % CFG["lemonade"],
        "zest": VERSION,
    }


def backends():
    cfg = lemonade_config()
    out = []
    for key, r in system_info().get("recipes", {}).items():
        bs = [{"name": n, "state": b.get("state"), "message": b.get("message"),
               "version": b.get("version"), "release_url": b.get("release_url"),
               "file": b.get("download_filename")}
              for n, b in r.get("backends", {}).items() if b.get("state") != "unsupported"]
        if not bs:
            continue
        prefix = key.replace("-", "")
        out.append({
            "recipe": key, "name": r.get("display_name", key), "modality": r.get("modality", ""),
            "experimental": bool(r.get("experimental")), "order": r.get("order", 99),
            "selectable": bool(r.get("selectable_backend")) and f"{prefix}.backend" in cfg,
            "default": r.get("default_backend"), "current": cfg.get(f"{prefix}.backend") or "auto",
            "backends": bs,
        })
    out.sort(key=lambda r: r["order"])
    return out


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
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def same_origin(self):
        origin = self.headers.get("Origin")
        return not origin or urlparse(origin).netloc == self.headers.get("Host")

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        elif u.path == "/ui/ping":
            self.send_json({"zest": VERSION})
        elif u.path == "/ui/gpu":
            self.send_json({"gpus": nvidia_gpus(), "t": time.time()})
        elif u.path == "/ui/info":
            self.send_json(cached("info", 4, info))
        elif u.path == "/ui/backends":
            self.send_json({"recipes": cached("backends", 4, backends)})
        elif u.path == "/ui/logs":
            n = min(max(int(parse_qs(u.query).get("n", ["200"])[0]), 10), 1000)
            code, out = run(["journalctl", "-u", "lemond", "-n", str(n), "--no-pager", "-o", "cat"], 10)
            if code != 0 and not out.strip().count("\n"):
                out = "Couldn't read the lemond service log (%s).\nZest reads it with `journalctl -u lemond`." % out.strip()[:200]
            self.send_json({"lines": out.splitlines()})
        elif u.path.startswith("/api/"):
            self.proxy()
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/ui/ping":
            _last_ping["t"] = time.time()
            return self.send_json({"zest": VERSION})
        if not self.same_origin():
            return self.send_json({"error": "cross-origin request refused"}, 403)
        if u.path == "/ui/device":
            dev = self.read_json().get("device", "")
            if not isinstance(dev, str) or not re.fullmatch(r"([a-z]+\d+(,[a-z]+\d+)*)?", dev):
                return self.send_json({"error": "device must look like cuda1 or cuda0,cuda1 (empty = automatic)"}, 400)
            code, out = lemonade_cli("config", "set", f"llamacpp.device={dev}")
            invalidate("info")
            return self.send_json({"ok": code == 0, "output": out.strip()}, 200 if code == 0 else 500)
        if u.path in ("/ui/backend", "/ui/backend/install"):
            return self.backend_action(u.path.endswith("/install"))
        if u.path.startswith("/api/"):
            return self.proxy()
        self.send_json({"error": "not found"}, 404)

    do_DELETE = do_POST

    def backend_action(self, install):
        body = self.read_json()
        recipe, backend = body.get("recipe"), body.get("backend")
        rec = next((r for r in cached("backends", 4, backends) if r["recipe"] == recipe), None)
        if not rec:
            return self.send_json({"error": "unknown recipe"}, 400)
        if install:
            b = next((x for x in rec["backends"] if x["name"] == backend), None)
            if not b or b["state"] not in ("installable", "update_required"):
                return self.send_json({"error": "that backend can't be installed"}, 400)
            code, out = lemonade_cli("backends", "install", f"{recipe}:{backend}", timeout=3600)
        else:
            if not rec["selectable"]:
                return self.send_json({"error": "this engine doesn't support choosing a backend"}, 400)
            if backend != "auto" and not any(x["name"] == backend for x in rec["backends"]):
                return self.send_json({"error": "unknown backend"}, 400)
            code, out = lemonade_cli("config", "set", f"{recipe.replace('-', '')}.backend={backend}")
        invalidate()
        self.send_json({"ok": code == 0, "output": out.strip()[-600:]}, 200 if code == 0 else 500)

    def proxy(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        headers = {k: v for k, v in self.headers.items() if k.lower() in ("content-type", "accept")}
        if CFG["api_key"]:
            headers["Authorization"] = "Bearer " + CFG["api_key"]
        conn = http.client.HTTPConnection(*CFG["lemonade"], timeout=900)
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
    ap.add_argument("--lemonade", default="%s:%s" % CFG["lemonade"], metavar="HOST:PORT", help="Lemonade Server address (default 127.0.0.1:13305)")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    ap.add_argument("--app", action="store_true", help="desktop mode: quit automatically a few minutes after the browser tab is closed")
    ap.add_argument("--version", action="version", version="Zest " + VERSION)
    a = ap.parse_args()

    h, _, p = a.lemonade.rpartition(":")
    CFG["lemonade"] = (h or "127.0.0.1", int(p or 13305))
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
    print(f"Zest {VERSION} on {url}  ->  Lemonade {CFG['lemonade'][0]}:{CFG['lemonade'][1]}", flush=True)
    if a.app:
        threading.Thread(target=watchdog, args=(srv, 180), daemon=True).start()
    if not a.no_browser:
        threading.Timer(0.6, webbrowser.open, args=(url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
