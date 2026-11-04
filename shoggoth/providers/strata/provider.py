"""Strata as a provider: the YAML says what it is; this module answers the questions the YAML can't, by asking the code
that already knows (strata.py). Nothing here changes how Strata behaves."""
import provbase
import strata


class Provider(provbase.GenericProvider):
    def detect(self):
        running = [{"port": r["port"], "owner": r["owner"], "url": r["url"], "state": r["state"], "source": r["source"]} for r in strata.running()]
        return {"found": bool(strata.binary()) or bool(running), "bin": strata.binary(), "running": running}

    def status(self):
        det = self.detect()
        d = dict(self.summary(), installed=bool(det["bin"]), version=strata.version() if det["bin"] else "", running=det["running"],
                 bin=det["bin"], problems=[])
        if det["bin"]:
            d["presets"] = strata.presets()
        return d

    def models(self):
        out = []
        for m in strata.models():
            out.append({"id": m["bare"], "ctx": m["ctx"], "vision": m["vision"], "dormant": bool(m.get("dormant")), "preset": m.get("preset"),
                        "port": int(m["url"].rsplit(":", 1)[1]), "owner": "zest" if m.get("dormant") or (strata.zest_server() or {}).get("url") == m["url"] else "yours"})
        return out

    def endpoint(self, model_id=None):
        m = next((m for m in strata.models() if not m.get("dormant") and (model_id is None or m["bare"] == model_id)), None)
        return {"base_url": m["url"].rstrip("/") + "/v1", "key": m.get("key", ""), "model": m["bare"], "ctx": m["ctx"]} if m else None

    def ensure(self, model_id, progress, opts=None):
        try:
            m = strata.ensure(strata.PREFIX + model_id, on_line=progress)
        except ValueError as e:
            raise provbase.ProviderError(str(e))
        return {"base_url": m["url"].rstrip("/") + "/v1", "key": m.get("key", ""), "model": m["bare"], "ctx": m["ctx"]}

    def stop(self, model_id=None):
        r = strata.zest_server()
        if not r:
            return False, "Shoggoth didn't start Strata, so it leaves it running."
        return strata.stop(r["url"])

    def release(self):
        """`strata launch --stop`, which works on any instance `strata launch` manages, ours or not. One it didn't launch is refused with the reason."""
        return strata.stop()

    def is_running(self):
        # only what holds this computer's memory: a Strata on another computer you saved the address of doesn't
        return any(r["state"] in ("ready", "loading") and r["source"] != "saved" for r in strata.running())

    def loaded(self, model_id):
        m = strata.find_model(strata.PREFIX + model_id)
        return (True, m["ctx"]) if m and not m.get("dormant") and not m.get("asleep") else (False, 0)

    def max_ctx(self, model_id):
        return self.loaded(model_id)[1] or (strata.find_model(strata.PREFIX + model_id) or {}).get("ctx", 0)

    def chat_models(self):
        """Every Strata model that is running now (an asleep one wakes on the next message), and the setups Shoggoth made that aren't
        (they start when a chat needs them)."""
        return [{"id": m["bare"], "tools": True, "reasoning": True, "vision": m["vision"], "ctx": m["ctx"], "live": not m.get("dormant") and not m.get("asleep"),
                 "pi_provider": m["provider"], "pi_url": m["url"].rstrip("/") + "/v1", "pi_key": m.get("key") or ""} for m in strata.models()]

    def busy(self):
        r = strata.zest_server()
        return bool(r and strata.activity(r["url"])[0])

    def activity(self):
        r = strata.zest_server()
        return strata.activity(r["url"]) if r else (None, None)

    def selftest(self):
        return strata.selftest()

    def owned_instance(self):
        r = strata.zest_server()
        return r["url"] if r else None                     # loading counts: one still loading when Shoggoth quits is stopped too

    def idle_minutes(self):
        return int(strata._settings().get("idle_minutes") or 0)

    def pi_endpoint(self, zest_port):
        return None                                        # each Strata is its own server; models carry their own address

    def memory(self):
        d = super().memory()
        d["free"] = strata.free_memory()
        return d

    def update_check(self):
        c = strata.update_check()
        return {"supported": True, "available": bool(c.get("available")), "deferred": bool(c.get("deferred")), "major": bool(c.get("major")), "detail": c}

    def update(self, progress):
        return strata.start_update()

    def tune_plan(self, model_id, opts):
        return {"minutes": strata.tune_minutes(opts.get("mode") or "quick")}

    def tune_run(self, model_id, opts, job):
        return strata.tune_run(model_id, opts, job)

    def model_folders(self):
        import os
        roots = set()
        for m in strata.installed_models():
            for f in m["files"]:
                roots.add(os.path.dirname(os.path.dirname(f)))        # .../models/<name>/file.gguf -> .../models
            if m.get("data_root"):
                roots.add(os.path.join(m["data_root"], "models"))
        return [(r, "Strata") for r in sorted(roots) if os.path.isdir(r)]

    def model_id(self, entry):
        import os
        names = {os.path.basename(f) for f in entry.get("files", [])}
        deep = [m for m in strata.installed_models() if str(m.get("name", "")).startswith("deepseek-v4.1")]
        if entry.get("architecture") == "deepseek41" and len(deep) == 1:
            return deep[0]["name"]                       # Strata's DeepSeek run config doesn't list its files; there is one
        for m in strata.installed_models():
            if names & {os.path.basename(f) for f in m["files"]} or (entry.get("quant") and entry["quant"].lower() in str(m.get("id", "")).lower() and entry.get("architecture") in ("qwen4exp", "deepseek41")):
                return m["name"]
        return None

    def need_gb(self, entry):
        # Strata splits a model across graphics card, RAM and SSD: its need is the figure for that size, not the file size.
        n = strata.need_gb(entry.get("quant") or "") if entry.get("architecture") == "qwen4exp" else None
        if n is None and entry.get("architecture") == "deepseek41":
            n = (self._deepseek() or {}).get("need_gb")
        return n if n is not None else round(entry.get("size_bytes", 0) / 1e9 * 0.7, 1)

    @staticmethod
    def _deepseek():
        """DeepSeek V4.1's entry in Shoggoth's list of big models (recommendations/models.yaml), or None."""
        return next((e for e in strata.catalog() if e["family"] == "deepseek41"), None)

    def fit(self, entry, machine):
        need = self.need_gb(entry)
        if not machine.get("gpus"):
            return "no", "Strata needs a graphics card."
        budget = machine.get("ram_gb", 0) + machine.get("vram_gb", 0)
        vram = (self._deepseek() or {}).get("min_vram_gb", 0) if entry.get("architecture") == "deepseek41" else 0
        if vram and machine.get("best_gpu_gb", 0) < vram:
            return "no", "Needs a graphics card with %d GB or more." % vram
        if need <= budget * 0.85 - 6:
            return "fits", ""
        if need <= budget * 0.95 - 2:
            return "tight", "Close other big programs first."
        return "no", "Needs about %d GB of RAM plus graphics memory; this computer has %d GB." % (need, budget)

    def update_status(self):
        u = dict(strata.SUPD)
        return {"state": u.get("state") or "idle", "text": u.get("text") or "", "error": u.get("error")}

    # -- the page's calls (/ui/providers/strata/<action>)
    def ui(self, method, action, arg):
        fn = getattr(self, "_ui_%s_%s" % (method.lower(), action), None)
        return fn(arg) if fn else None

    def _ui_get_summary(self, q):
        return 200, strata.summary()

    def _ui_get_recommend(self, q):
        return 200, strata.recommend()

    def _ui_get_update(self, q):
        """The last look at Strata's feed (kept six hours; ?fresh=1 looks again), and where an update stands."""
        try:
            check, err = strata.update_check(0 if q.get("fresh") else 6 * 3600), ""
        except RuntimeError as e:
            check, err = {"installed": bool(strata.binary())}, str(e)
        return 200, {"check": check, "error": err, "update": dict(strata.SUPD)}

    def _ui_post_update(self, body):
        return 200, {"started": strata.start_update(), "update": dict(strata.SUPD)}

    def _ui_post_start(self, body):
        """Start a saved Strata preset. A big model and Lemonade's models compete for memory, so Lemonade's are freed first
        when the page says so (`free_lemonade`)."""
        preset = str(body.get("preset", "")).strip()
        return 200, {"started": strata.start(preset, bool(body.get("force"))), **strata.summary()}

    def _ui_post_setup(self, body):
        """Get a big model in one step: install Strata if needed, download, save as a Shoggoth preset, start. Lemonade's models are
        freed first because they share the memory."""
        family, model = str(body.get("family", "")), str(body.get("model", ""))
        if not any(e["family"] == family and e["model"] == model for e in strata.catalog()):
            raise ValueError("Shoggoth doesn't know that model.")
        return 200, {"started": strata.setup(family, model, bool(body.get("force"))), **strata.summary()}

    def _ui_post_install(self, body):
        return 200, {"started": strata.install(), **strata.summary()}

    def _ui_post_idle(self, body):
        strata.set_idle_minutes(body.get("minutes", 20))
        return 200, {"ok": True, **strata.summary()}

    def _ui_post_connect(self, body):
        ok, msg = strata.connect(body.get("url", ""), str(body.get("key", "")).strip())
        return (200 if ok else 400), {"ok": ok, "error": msg, "_restart": True, **strata.summary()}

    def _ui_post_forget(self, body):
        strata.forget(body.get("url", ""))
        return 200, {"ok": True, "_restart": True, **strata.summary()}

    def _ui_post_key(self, body):
        strata.set_key(body.get("url", ""), body.get("key", ""))
        return 200, {"ok": True, "_restart": True, **strata.summary()}

    def _ui_post_stop(self, body):
        ok, msg = strata.stop(body.get("url") or None)
        return (200 if ok else 409), {"ok": ok, "error": msg, "_restart": True}
