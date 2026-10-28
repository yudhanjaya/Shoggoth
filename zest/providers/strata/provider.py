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
