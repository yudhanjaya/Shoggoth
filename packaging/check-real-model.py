#!/usr/bin/env python3
"""End to end with a real model, no GPU: start a built AppImage on a throwaway profile, download a model from Hugging Face through the
page's own route (resume, checksum), load it with the bundled processor-only llama.cpp, and get an answer through the chat door.

  packaging/check-real-model.py dist/Shoggoth-<v>-x86_64.AppImage                      SmolLM2-135M (105 MB), a quick check
  packaging/check-real-model.py dist/Shoggoth-<v>-x86_64.AppImage --pick LFM3-Omni-2B   a recommended model by its name in models.yaml

It goes online (Hugging Face) and takes a few minutes. Your own profile is never touched: everything is in a temporary folder that is
removed afterwards, and Shoggoth is stopped at the end whatever happens."""
import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

QUICK = {"model_name": "SmolLM2-135M-Instruct-Q4_K_M", "checkpoint": "bartowski/SmolLM2-135M-Instruct-GGUF:Q4_K_M"}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class App:
    def __init__(self, path):
        self.tmp = tempfile.mkdtemp(prefix="shoggoth-e2e-")
        self.port = free_port()
        env = dict(os.environ, APPIMAGE_EXTRACT_AND_RUN="1", SHOGGOTH_AUTH="off", XDG_DATA_HOME=os.path.join(self.tmp, "data"),
                   XDG_CONFIG_HOME=os.path.join(self.tmp, "config"), HOME=os.path.join(self.tmp, "home"), CUDA_VISIBLE_DEVICES="")
        os.makedirs(env["HOME"])
        self.log = open(os.path.join(self.tmp, "run.log"), "w")
        self.proc = subprocess.Popen([os.path.realpath(path), "--no-browser", "--port", str(self.port)], cwd=self.tmp, env=env,
                                     stdout=self.log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)

    def call(self, path, body=None, timeout=60):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=data, method="POST" if data else "GET",
                                     headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:%d" % self.port})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            raise RuntimeError("%s answered %d: %s" % (path, e.code, e.read()[:500].decode("utf8", "replace")))

    def wait_up(self, seconds=120):
        end = time.time() + seconds
        while time.time() < end:
            if self.proc.poll() is not None:
                raise RuntimeError("the AppImage exited while starting")
            try:
                return self.call("/ui/info", timeout=5)
            except (OSError, RuntimeError, ValueError):
                time.sleep(1)
        raise RuntimeError("no answer after %d s" % seconds)

    def stop(self):
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(40)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self.log.close()

    def tail(self, n=40):
        try:
            with open(os.path.join(self.tmp, "run.log")) as f:
                return "".join(f.readlines()[-n:])
        except OSError:
            return ""


def step(msg):
    print("..", msg, flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("appimage")
    ap.add_argument("--pick", help="a recommended model's id in models.yaml (downloads what its card would)")
    ap.add_argument("--timeout", type=int, default=1800, help="seconds allowed for the download")
    a = ap.parse_args()
    app = App(a.appimage)
    ok = False
    try:
        step("starting %s" % os.path.basename(a.appimage))
        info = app.wait_up()
        print("ok: up, llama.cpp %s" % (info.get("llama_tag") or "?"))

        body = {"model_name": a.pick} if a.pick else dict(QUICK)
        step("downloading %s" % (a.pick or QUICK["checkpoint"]))
        app.call("/ui/models/pull", body)
        end, last = time.time() + a.timeout, -1
        while True:
            jobs = {j["model_name"]: j for j in app.call("/ui/models/downloads")}
            j = jobs.get(body["model_name"])
            if j and j["status"] == "completed":
                break
            if j and j["status"] in ("error", "cancelled"):
                raise RuntimeError("the download ended: %s %s" % (j["status"], j.get("error")))
            if j and j["percent"] // 10 != last:
                last = j["percent"] // 10
                print("   %d%%" % j["percent"], flush=True)
            if time.time() > end:
                raise RuntimeError("the download took longer than %d s" % a.timeout)
            time.sleep(2)
        print("ok: downloaded and checked")

        models = app.call("/ui/models/list")["data"]
        got = [m for m in models if m["downloaded"] and (m.get("rec") == a.pick if a.pick else m["id"].lower().startswith("smollm2-135m"))]
        if not got:
            raise RuntimeError("the download isn't in the model list: %s" % [m["id"] for m in models if m["downloaded"]])
        mid = got[0]["id"]
        print("ok: listed as %s" % mid)

        step("loading %s" % mid)
        r = app.call("/ui/model/load", {"model": mid, "ctx": 4096}, timeout=900)
        if not r.get("ok"):
            raise RuntimeError("it didn't load: %s" % r.get("error"))
        print("ok: loaded at %s tokens" % r.get("ctx"))

        step("asking it something")
        r = app.call("/api/v1/chat/completions", {"model": mid, "max_tokens": 24, "temperature": 0,
                                                  "messages": [{"role": "user", "content": "Say hello in one short sentence."}]}, timeout=300)
        text = ((r.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        if not text.strip():
            raise RuntimeError("an empty answer: %s" % json.dumps(r)[:400])
        print("ok: it answered: %r" % text.strip()[:120])
        ok = True
    except Exception as e:  # noqa: BLE001 - report anything, then clean up
        print("FAIL: %s" % e, file=sys.stderr)
        print("--- log\n" + app.tail(), file=sys.stderr)
    finally:
        app.stop()
        shutil.rmtree(app.tmp, ignore_errors=True)
    print("Real-model check %s" % ("passed" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
