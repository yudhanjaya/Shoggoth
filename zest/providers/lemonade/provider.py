"""Lemonade as a provider, answered by the code that already talks to it (lem.py, engine.py). Nothing here changes behaviour."""
import engine
import lem
import provbase
import store


class Provider(provbase.GenericProvider):
    def detect(self):
        host, port = lem.target()
        up = lem.healthy()
        return {"found": up or bool(engine.programs()), "bin": (engine.choose(store.load()) or {}).get("dir"),
                "running": [{"port": port, "owner": "zest" if engine.running() else "yours"}] if up else []}

    def status(self):
        det = self.detect()
        return dict(self.summary(), installed=det["found"], running=det["running"], bin=det["bin"], problems=[],
                    version=((engine.choose(store.load()) or {}).get("version") or ""))

    def models(self):
        out = []
        for m in lem.get("/v1/models").get("data", []):
            if m.get("downloaded"):
                out.append({"id": m["id"], "ctx": int(m.get("context_length") or 0), "vision": "vision" in (m.get("labels") or []),
                            "recipe": m.get("recipe"), "port": lem.target()[1], "owner": "zest" if engine.running() else "yours"})
        return out

    def endpoint(self, model_id=None):
        if not lem.healthy():
            return None
        host, port = lem.target()
        return {"base_url": "http://%s:%d/api/v1" % (host, port), "key": lem.key(), "model": model_id, "ctx": 0}

    def ensure(self, model_id, progress, opts=None):
        """Have `model_id` in memory. opts: ctx (the context window wanted), exact (reload unless it is exactly that size, as an explicit
        Load does), force (reload regardless). Without exact, a model already loaded with at least `ctx` is left alone."""
        opts = opts or {}
        ctx = int(opts.get("ctx") or 0)
        if not lem.healthy():
            progress("Starting Lemonade…")
            if not engine.start(store.load()):
                raise provbase.ProviderError("Lemonade didn't start.")
        isl, cur = self.loaded(model_id)
        if isl and not opts.get("force") and (not ctx or (cur == ctx if opts.get("exact") else (not cur or cur >= ctx))):
            return self.endpoint(model_id)
        body = {"model_name": model_id}
        if ctx:
            body["ctx_size"] = ctx
        progress("Loading %s…" % model_id)
        if isl:
            lem.request("POST", "/v1/unload", {"model_name": model_id}, timeout=60)
        status, payload = lem.request("POST", "/v1/load", body, timeout=900)
        if status != 200:
            raise provbase.ProviderError(lem.error_text(payload, "Lemonade couldn't load the model."))
        return self.endpoint(model_id)

    def stop(self, model_id=None):
        """Free one model's memory, or every model's when none is named. Lemonade's models are loaded by Zest on request, so Zest frees them."""
        status, payload = lem.request("POST", "/v1/unload", {"model_name": model_id} if model_id else {}, timeout=60)
        return status == 200, "" if status == 200 else lem.error_text(payload, "Lemonade couldn't free the model.")

    def ref(self, model_id):
        return model_id                                  # Lemonade's names are the ones chats have always used

    def is_running(self):
        return bool(lem.get("/v1/health").get("all_models_loaded"))

    def loaded(self, model_id):
        """(in memory?, its context window in tokens or 0 when Lemonade doesn't say)."""
        for m in lem.get("/v1/health").get("all_models_loaded", []):
            if m.get("model_name") != model_id:
                continue
            cmd = m.get("launch_command") or []
            if isinstance(cmd, str):
                cmd = cmd.split()
            if "--ctx-size" in cmd:
                try:
                    return True, int(cmd[cmd.index("--ctx-size") + 1])
                except (ValueError, IndexError):
                    pass
            return True, 0
        return False, 0

    def max_ctx(self, model_id):
        """The longest context the model was trained for, when Lemonade knows it (0 when it doesn't)."""
        for m in lem.get("/v1/models").get("data", []):
            if m.get("id") == model_id:
                try:
                    n = int(m.get("context_length") or 0)
                except (TypeError, ValueError):
                    return 0
                return n if n >= 2048 else 0
        return 0

    def chat_models(self):
        """Downloaded models that can drive an agent: chat models, preferring ones Lemonade says can call tools."""
        not_chat = ("sd-cpp", "whispercpp", "kokoro", "moonshine", "acestep", "thinksound", "trellis", "openmoss")
        out = []
        in_memory = {m.get("model_name") for m in lem.get("/v1/health").get("all_models_loaded", [])}
        for m in lem.get("/v1/models").get("data", []):
            labels = m.get("labels") or []
            if not m.get("downloaded") or m.get("recipe") in not_chat or "embeddings" in labels or "chat" not in labels and "tool-calling" not in labels:
                continue
            out.append({"id": m["id"], "tools": "tool-calling" in labels, "reasoning": "reasoning" in labels, "vision": "vision" in labels,
                        "ctx": int(m.get("context_length") or 131072), "live": m["id"] in in_memory})
        out.sort(key=lambda m: (not m["tools"], m["id"].lower()))
        return out

    def pi_endpoint(self, zest_port):
        # through Zest, so the request shows on the Server page
        return {"base_url": "http://127.0.0.1:%d/api/v1" % zest_port, "key": "zest"}

    def update_check(self):
        if lem.healthy() and not engine.running():
            # a Lemonade the person runs (for example a system service): Zest can only fetch a separate copy of its own, which wouldn't update it
            return {"supported": False, "available": False, "note": "You run this one yourself, so update it where you installed it."}
        try:
            c = engine.check(store.load())
        except RuntimeError as e:
            raise provbase.ProviderError(str(e))
        return {"supported": True, "available": bool(c.get("update_available")), "detail": c}

    def update(self, progress):
        return engine.start_update()

    def model_folders(self):
        import modelscan
        out = [(modelscan.hf_cache_dir(), "Lemonade")]
        if lem.healthy():
            cfg = lem.flat_config()
            md = cfg.get("models_dir")
            if md and md != "auto" and md != modelscan.hf_cache_dir():
                out.append((md, "Lemonade"))
        return [(p, l) for p, l in out if p and __import__("os").path.isdir(p)]

    def model_id(self, entry):
        """Lemonade's name for a file: a registered model whose checkpoint is the file's Hugging Face repo and quant, else the name
        it gives a file imported from a folder (the file name without .gguf)."""
        import os
        import re
        models = self._downloaded()
        if (entry.get("ids") or {}).get("lemonade"):
            return entry["ids"]["lemonade"]
        path = (entry.get("files") or [""])[0]
        hf = re.search(r"models--([^/]+)--([^/]+)", path)
        if hf:
            repo = "%s/%s" % (hf.group(1), hf.group(2))
            quant = (entry.get("quant") or "").lower()
            same = [m for m in models if str(m.get("checkpoint", "")).lower().startswith(repo.lower())]
            exact = [m for m in same if quant and quant in str(m.get("checkpoint", "")).lower()]
            pick = (exact or same or [None])[0]
            if pick:
                return pick["id"]
        stem = re.sub(r"\.gguf$", "", os.path.basename(path), flags=re.I)
        return next((m["id"] for m in models if m["id"] == stem), None)

    def library_models(self):
        """Lemonade's own list: this is the only view of its models when it runs as a system service whose folder Zest can't read."""
        import re
        not_chat = ("sd-cpp", "whispercpp", "kokoro", "moonshine", "acestep", "thinksound", "trellis", "openmoss")
        out = []
        for m in lem.get("/v1/models").get("data", []):
            labels = m.get("labels") or []
            if not m.get("downloaded") or m.get("recipe") in not_chat or "embeddings" in labels or "chat" not in labels:
                continue
            quant = re.search(r":(?:.*?)((?:UD-)?(?:IQ\d\w*|Q\d\w*|BF16|F16|MXFP4))", str(m.get("checkpoint") or ""))
            out.append({"stem": m["id"], "title": m["id"].replace("-GGUF", "").replace("-", " "), "files": [], "size_bytes": int(float(m.get("size") or 0) * 1e9),
                        "complete": True, "format": "gguf", "architecture": "", "quant": quant.group(1) if quant else "", "context": 0, "size_label": "",
                        "vision": "vision" in labels, "mmproj": "", "mtp": "mtp" in labels, "reasoning": "reasoning" in labels, "tools": "tool-calling" in labels,
                        "folder": "", "source": "Lemonade", "owner": "lemonade", "listed_by": "lemonade", "ids": {"lemonade": m["id"]}})
        return out

    def _downloaded(self):
        """Lemonade's downloaded models, kept for ten seconds: the library asks once per file."""
        import time
        c = getattr(self, "_dl", None)
        if c is None or time.time() - c[0] > 10:
            c = self._dl = (time.time(), [m for m in lem.get("/v1/models").get("data", []) if m.get("downloaded")])
        return c[1]

    def update_status(self):
        u = dict(engine.UPD)
        working = u.get("state") in ("downloading", "verifying", "installing")
        text = {"downloading": "Downloading Lemonade %s…", "verifying": "Checking the download…", "installing": "Installing…"}.get(u.get("state"), "") % ((u.get("version"),) if u.get("state") == "downloading" else ())
        return {"state": "working" if working else "done" if u.get("state") == "done" else "error" if u.get("state") == "error" else "idle",
                "text": text or ("Lemonade %s is installed. It starts the next time Lemonade restarts." % u.get("version") if u.get("state") == "done" else ""),
                "error": u.get("error"), "bytes": u.get("bytes"), "total": u.get("total")}
