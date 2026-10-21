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
import shlex
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

VERSION = "1.2.0"
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


def lemonade_request(method, path, body=None, timeout=15):
    """Returns (status, parsed JSON); status 0 means Lemonade couldn't be reached."""
    host, port = CFG["lemonade"]
    headers = {"Authorization": "Bearer " + CFG["api_key"]} if CFG["api_key"] else {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request(method, path, body=data, headers=headers)
        r = conn.getresponse()
        raw = r.read()
        return r.status, json.loads(raw or b"{}")
    except OSError as e:
        return 0, {"error": str(e)}
    except ValueError:
        return r.status, {}
    finally:
        conn.close()


def lemonade_get(path):
    status, payload = lemonade_request("GET", path, timeout=8)
    return payload if status == 200 else {}


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


# ---------------------------------------------------------------- llama-server arguments
def lem_split(text):
    """Split an argument string exactly the way Lemonade does: on spaces, with '...' or "..." grouping and no escapes."""
    out, cur, quote, started = [], "", None, False
    for c in text or "":
        if quote is None and c in "\"'":
            quote, started = c, True
        elif quote and c == quote:
            quote = None
        elif quote is None and c == " ":
            if cur or started:
                out.append(cur)
            cur, started = "", False
        else:
            cur += c
    if cur or started:
        out.append(cur)
    return out, quote is not None   # tokens, and whether a quote was left open


def lem_quote(tok):
    """Quote one token so Lemonade reads it back unchanged (None if it contains both kinds of quote)."""
    if tok and not re.search(r"[\s'\"]", tok):
        return tok
    if "'" not in tok:
        return "'" + tok + "'"
    if '"' not in tok:
        return '"' + tok + '"'
    return None


# Lemonade refuses these because it sets them itself.
MANAGED_FLAGS = {
    "-m": "the model path", "--model": "the model path", "--port": "the port",
    "--ctx-size": "the context window", "-c": "the context window",
    "-ngl": "GPU layers", "--gpu-layers": "GPU layers", "--n-gpu-layers": "GPU layers",
    "--jinja": "the chat template engine", "--mmproj": "the multimodal projector", "-mm": "the multimodal projector",
    "--embeddings": "embedding mode", "--embedding": "embedding mode",
    "--reranking": "reranking mode", "--rerank": "reranking mode",
}
# Options people know from vLLM / SGLang that llama.cpp spells differently.
FOREIGN_HINTS = {
    "--tool-call-parser": "That is a vLLM/SGLang option. llama.cpp reads tool calls through the model's chat template, and --jinja is already on. To use a different template, try --chat-template-file.",
    "--enable-auto-tool-choice": "That is a vLLM option. llama.cpp enables tool calling through the chat template (--jinja is already on).",
    "--reasoning-parser": "That is a vLLM/SGLang option. llama.cpp has --reasoning-format and --reasoning for thinking models.",
    "--max-model-len": "That is a vLLM option. Set the Context window field instead.",
    "--tensor-parallel-size": "That is a vLLM option. llama.cpp splits across GPUs with --split-mode and --tensor-split.",
}


def known_flags(bin_path):
    """Every flag the llama-server binary documents in --help."""
    def build():
        if not bin_path or not os.path.isfile(bin_path):
            return None
        code, out = run([bin_path, "--help"], 20)
        flags = set()
        for line in out.splitlines():
            if not line.startswith("-"):
                continue
            head = re.split(r"\s{2,}", re.sub(r",\s+", ", ", line.strip()), maxsplit=1)[0]
            for tok in re.findall(r"--?\[?[A-Za-z0-9][\w\-\[\]]*", head):
                m = re.fullmatch(r"(--)\[no-\](.+)", tok)
                if m:
                    flags.update({m.group(1) + m.group(2), m.group(1) + "no-" + m.group(2)})
                else:
                    flags.add(tok)
        return flags or None
    return cached(f"flags:{bin_path}:{os.path.getmtime(bin_path) if bin_path and os.path.isfile(bin_path) else 0}", 3600, build)


# ---------------------------------------------------------------- argument schema
# Flags that are not model settings (or that would break Lemonade's own connection to llama-server).
_HIDDEN = {"-h", "--help", "--usage", "--version", "--license", "--completion-bash", "--list-devices",
           "--cache-list", "--host", "--port", "--api-key", "--api-key-file", "--path", "--alias", "-a",
           "--tags", "--reuse-port", "--timeout", "-to", "--threads-http"}
_HIDDEN_PREFIXES = ("--ssl", "--api-", "--webui", "--no-webui", "--models-", "--hf-", "-hf", "--model-url",
                    "-mu", "--mmproj", "-mm", "--docker", "--tools", "--offline")


def _hidden(flag):
    return flag in _HIDDEN or flag in MANAGED_FLAGS or flag.startswith(_HIDDEN_PREFIXES)


def _clean_desc(lines):
    text = " ".join(lines)
    text = re.sub(r"\(env:[^)]*\)", "", text)
    text = re.sub(r"\[[^\]]*\]\([^)]*\)", "", text)       # markdown links
    return re.sub(r"\s+", " ", text).strip()


def _make_entry(flagpart, desc_lines, section):
    pieces = [x.strip() for x in flagpart.split(", ") if x.strip()]
    flags, metavar = [], ""
    for piece in pieces:
        parts = piece.split(None, 1)
        flags.append(parts[0])
        if len(parts) > 1 and not metavar:
            metavar = parts[1].strip()
    # split "-kvo, --kv-offload, -nkvo, --no-kv-offload" into its positive and negative halves
    neg_at = next((i for i, f in enumerate(flags) if f.startswith(("--no-", "-no-"))), None)
    pos, neg = flags, []
    if neg_at is not None:
        k0 = neg_at - 1 if neg_at > 0 and not flags[neg_at - 1].startswith("--") else neg_at
        pos, neg = flags[:k0], flags[k0:]
    if not pos:
        return None
    if any(_hidden(f) for f in pos + neg):
        return None
    ident = next((f for f in pos if f.startswith("--")), pos[0])
    desc = _clean_desc(desc_lines)
    choices, optional = [], False
    m = re.fullmatch(r"[\[{]([^\]}]+)[\]}]", metavar)
    if m:
        choices = [c for c in re.split(r"[|,]", m.group(1)) if c]
        optional = metavar.startswith("[")
    elif "," in metavar and "." not in metavar and re.fullmatch(r"[\w+\-,]+", metavar):
        choices = metavar.split(",")
    if not choices:
        mv = re.search(r"allowed values:\s*([\w+\-, ]+?)(?:\s*\(|$)", desc)
        if mv:
            choices = [c.strip() for c in mv.group(1).split(",") if c.strip()]
    if not choices:
        choices = re.findall(r"(?:^| )- ([\w+\-]+):", " " + " ".join(desc_lines))
    dm = re.search(r"default:\s*([^)]+?)\)", desc)
    default = dm.group(1).strip() if dm else ""
    if neg:
        kind = "pair"
    elif not metavar:
        kind = "bool"
    elif "," in metavar and not m and choices:
        kind = "multi"
    elif choices:
        kind = "choice"
    elif metavar in ("N", "SEED", "INDEX") or re.fullmatch(r"<[\d.]+>", metavar):
        kind = "number"
    else:
        kind = "text"
    return {"id": ident, "flags": pos, "neg": neg, "kind": kind, "metavar": metavar, "choices": choices,
            "optional": optional, "default": default[:40], "desc": desc, "section": section}


def args_schema(bin_path):
    """Every llama-server option, parsed from its own --help, as structured data."""
    def build():
        if not bin_path or not os.path.isfile(bin_path):
            return None
        code, out = run([bin_path, "--help"], 20)
        lines, entries, section, i = out.splitlines(), [], "common", 0
        while i < len(lines):
            line = lines[i]
            m = re.match(r"^-{3,}\s*(.+?)\s*-{3,}$", line)
            if m:
                section = m.group(1).replace(" params", "").strip()
                i += 1
                continue
            if not line.startswith("-"):
                i += 1
                continue
            norm = re.sub(r",\s+", ", ", line.strip())
            parts = re.split(r"\s{2,}", norm, maxsplit=1)
            desc = [parts[1]] if len(parts) > 1 else []
            i += 1
            while i < len(lines) and lines[i].startswith(" " * 10):
                desc.append(lines[i].strip())
                i += 1
            e = _make_entry(parts[0], desc, section)
            if e:
                entries.append(e)
        return entries or None
    key = f"schema:{bin_path}:{os.path.getmtime(bin_path) if bin_path and os.path.isfile(bin_path) else 0}"
    return cached(key, 3600, build)


def parse_args(text, bin_path):
    """Split an argument string into values for known options plus whatever is left over."""
    schema = args_schema(bin_path)
    toks, open_quote = lem_split(text)
    if open_quote or not schema:
        return {"values": {}, "custom": text or "", "known": bool(schema) and not open_quote}
    if not schema:
        return {"values": {}, "custom": text or "", "known": False}
    alias = {}
    for e in schema:
        for f in e["flags"]:
            alias[f] = (e, False)
        for f in e["neg"]:
            alias[f] = (e, True)
    values, custom, i = {}, [], 0
    isflag = lambda t: t.startswith("-") and not re.fullmatch(r"-\d+(\.\d+)?", t)
    while i < len(toks):
        t = toks[i]
        flag, eq, inline = t.partition("=")
        hit = alias.get(flag) if isflag(t) else None
        if not hit:
            custom.append(t)
            if isflag(t) and not eq and i + 1 < len(toks) and not isflag(toks[i + 1]):
                custom.append(toks[i + 1])
                i += 1
            i += 1
            continue
        e, is_neg = hit
        if e["kind"] in ("bool", "pair"):
            values[e["id"]] = ("off" if is_neg else "on") if e["kind"] == "pair" else True
            i += 1
            continue
        val = inline if eq else None
        if val is None and i + 1 < len(toks) and (not isflag(toks[i + 1])):
            val = toks[i + 1]
            i += 1
        if val is None:  # a bare optional-value flag such as "--flash-attn" means "on"
            val = "on" if "on" in e["choices"] else ""
        values[e["id"]] = val.split(",") if e["kind"] == "multi" else val
        i += 1
    return {"values": values, "custom": " ".join(q or t for t in custom for q in [lem_quote(t)]), "known": True}


def check_args(text, bin_path):
    toks, open_quote = lem_split(text)
    if open_quote:
        return {"error": "A quote is never closed.", "problems": [], "verified": False}
    flags = known_flags(bin_path)
    problems, seen = [], set()
    for t in toks:
        if not t.startswith("-") or re.fullmatch(r"-\d+(\.\d+)?", t):
            continue
        flag = t.split("=", 1)[0]
        if flag in seen:
            continue
        seen.add(flag)
        if flag in MANAGED_FLAGS:
            problems.append({"flag": flag, "level": "error",
                             "message": f"Lemonade sets {MANAGED_FLAGS[flag]} itself and rejects this option."})
        elif flags is not None and flag not in flags:
            problems.append({"flag": flag, "level": "warn",
                             "message": FOREIGN_HINTS.get(flag, "This llama-server build doesn't list this option, so loading would probably fail.")})
    return {"problems": problems, "verified": flags is not None}


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
        elif u.path == "/ui/config":
            self.send_json({"config": lemonade_get("/internal/config"), "defaults": lemonade_get("/internal/config/defaults")})
        elif u.path == "/ui/args/schema":
            schema = args_schema(cached("info", 4, info).get("llama_bin"))
            self.send_json({"entries": schema or [], "verified": bool(schema)})
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
        if u.path == "/ui/config":
            nested = self.read_json()
            if not isinstance(nested, dict) or not nested:
                return self.send_json({"error": "expected a JSON object"}, 400)
            status, payload = lemonade_request("POST", "/internal/set", nested)
            invalidate()
            if status == 200:
                return self.send_json({"ok": True, "updated": payload.get("updated")})
            err = payload.get("error", "Lemonade rejected the change") if isinstance(payload, dict) else "Lemonade rejected the change"
            return self.send_json({"ok": False, "error": err if isinstance(err, str) else str(err)}, 400 if status else 502)
        if u.path == "/ui/args/parse":
            body = self.read_json()
            return self.send_json(parse_args(str(body.get("args", "")), cached("info", 4, info).get("llama_bin")))
        if u.path == "/ui/args/check":
            body = self.read_json()
            return self.send_json(check_args(str(body.get("args", "")), cached("info", 4, info).get("llama_bin")))
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
