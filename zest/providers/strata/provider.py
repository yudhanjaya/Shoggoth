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
            strata.ensure(strata.PREFIX + model_id, on_line=progress)
        except ValueError as e:
            raise provbase.ProviderError(str(e))
        return self.endpoint(model_id)

    def stop(self):
        r = strata.zest_server()
        if not r:
            return False, "Zest didn't start Strata, so it leaves it running."
        return strata.stop(r["url"])

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
            n = 46.0
        return n if n is not None else round(entry.get("size_bytes", 0) / 1e9 * 0.7, 1)

    def fit(self, entry, machine):
        need = self.need_gb(entry)
        if not machine.get("gpus"):
            return "no", "Strata needs a graphics card."
        budget = machine.get("ram_gb", 0) + machine.get("vram_gb", 0)
        if entry.get("architecture") == "deepseek41" and machine.get("best_gpu_gb", 0) < 12:
            return "no", "Needs a graphics card with 12 GB or more."
        if need <= budget * 0.85 - 6:
            return "fits", ""
        if need <= budget * 0.95 - 2:
            return "tight", "Close other big programs first."
        return "no", "Needs about %d GB of RAM plus graphics memory; this computer has %d GB." % (need, budget)
