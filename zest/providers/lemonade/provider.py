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
        if not lem.healthy():
            progress("Starting Lemonade…")
            if not engine.start(store.load()):
                raise provbase.ProviderError("Lemonade didn't start.")
        body = {"model_name": model_id}
        if opts and opts.get("ctx"):
            body["ctx_size"] = int(opts["ctx"])
        progress("Loading %s…" % model_id)
        status, payload = lem.request("POST", "/v1/load", body, timeout=900)
        if status != 200:
            raise provbase.ProviderError(lem.error_text(payload, "Lemonade couldn't load the model."))
        return self.endpoint(model_id)

    def stop(self):
        status, payload = lem.request("POST", "/v1/unload", {}, timeout=60)
        return status == 200, "" if status == 200 else lem.error_text(payload, "Lemonade couldn't free the model.")

    def update_check(self):
        try:
            c = engine.check(store.load())
        except RuntimeError as e:
            raise provbase.ProviderError(str(e))
        return {"supported": True, "available": bool(c.get("update_available")), "detail": c}

    def update(self, progress):
        return engine.start_update()
