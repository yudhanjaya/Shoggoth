"""llama.cpp as a provider, answered by llamacpp.py. Off until the person (or 5.0's first run) turns it on, so 4.x
behaviour with Lemonade is unchanged while both exist."""
import os

import llamacpp
import provbase
import store


def enabled():
    return bool((store.load().get("llamacpp") or {}).get("enabled")) or os.environ.get("SHOGGOTH_ENGINE") == "llamacpp"


class Provider(provbase.GenericProvider):
    def _models_dir(self):
        return os.path.join(self.ctx.library or store.data_dir(), "llamacpp")

    def can_run(self, model):
        return enabled() and super().can_run(model)

    def detect(self):
        b = llamacpp.choose()
        up = llamacpp.running()
        return {"found": bool(b), "bin": b and b["server"],
                "running": [{"port": llamacpp._state["port"], "owner": "zest"}] if up else []}

    def status(self):
        b = llamacpp.choose()
        return dict(self.summary(), installed=bool(b), running=self.detect()["running"], bin=b and b["server"], problems=[],
                    version=(b or {}).get("tag", ""), backend=(b or {}).get("backend", ""), enabled=enabled())

    def models(self):
        if not llamacpp.running():
            return []
        return [{"id": m["id"], "ctx": m["ctx"], "port": llamacpp._state["port"], "owner": "zest", "status": m["status"]}
                for m in llamacpp.models()]

    def loaded(self, model_id):
        m = next((m for m in llamacpp.models() if m["id"] == model_id), None) if llamacpp.running() else None
        return (True, m["ctx"]) if m and m["status"] == "loaded" else (False, 0)

    def is_running(self):
        return llamacpp.running() and any(m["status"] == "loaded" for m in llamacpp.models())

    def endpoint(self, model_id=None):
        ep = llamacpp.endpoint()
        return dict(ep, model=model_id, ctx=self.loaded(model_id)[1] if model_id else 0, owner="zest") if ep else None

    def ensure(self, model_id, progress, opts=None):
        """Have `model_id` loaded with at least the context asked for (exactly, with opts.exact)."""
        opts = opts or {}
        if not llamacpp.running():
            progress("Starting llama.cpp…")
            if not llamacpp.start(self._models_dir()):
                raise provbase.ProviderError(llamacpp._state.get("error") or "llama.cpp didn't start.")
        ctx = int(opts.get("ctx") or 0)
        isl, cur = self.loaded(model_id)
        if isl and not opts.get("force") and (not ctx or (cur == ctx if opts.get("exact") else cur >= ctx)):
            return self.endpoint(model_id)
        if ctx:
            presets = {k: v for k, v in llamacpp.read_presets().items() if k != "*"}
            presets.setdefault(model_id, {})["ctx-size"] = ctx
            llamacpp.write_presets(presets, llamacpp.DEFAULTS)
            if isl:
                llamacpp.unload(model_id)
            llamacpp.reload()
        progress("Loading %s…" % model_id)
        ok, msg = llamacpp.load(model_id)
        if not ok:
            raise provbase.ProviderError(msg)
        return self.endpoint(model_id)

    def stop(self, model_id=None):
        if model_id:
            return llamacpp.unload(model_id)
        llamacpp.stop()
        return True, ""

    def make_way(self):
        for m in llamacpp.models() if llamacpp.running() else []:
            if m["status"] == "loaded":
                ok, msg = llamacpp.unload(m["id"])
                if not ok:
                    return False, msg
        return True, ""

    def owned_instance(self):
        return llamacpp._state["port"] if llamacpp.running() else None

    def update_check(self):
        try:
            rel = llamacpp.latest()
        except RuntimeError as e:
            raise provbase.ProviderError(str(e))
        b = llamacpp.choose()
        newer = not b or llamacpp._tag_num(rel["tag_name"]) > llamacpp._tag_num(b["tag"])
        return {"supported": True, "available": newer, "detail": {"latest": rel["tag_name"], "current": (b or {}).get("tag")}}

    def selftest(self):
        if not llamacpp.running():
            return []
        status, d = llamacpp.request("GET", "/models")
        ok = status == 200 and isinstance(d.get("data"), list) and all("status" in m for m in d["data"])
        return [{"name": "its model list", "ok": ok, "detail": "" if ok else "GET /models answered %s or changed shape" % status}]
