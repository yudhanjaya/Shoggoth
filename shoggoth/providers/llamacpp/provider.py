"""llama.cpp as a provider, answered by llamacpp.py: the engine Shoggoth runs itself for models on this computer.

The router reads <data>/views/llamacpp (llamacpp.models_dir()): links to every library file routed to llama.cpp, kept by
library.sync_views, plus what migrate5 linked from a 4.x Lemonade. A model's id is its name there (the link's name without .gguf,
or the folder's name for a split or vision model), which is also the name chats use.
"""
import os
import time

import llamacpp
import provbase
import store
import uiauth


def enabled():
    """On unless the person turned it off (settings llamacpp.enabled: false)."""
    return (store.load().get("llamacpp") or {}).get("enabled", True) is not False


def view_ids(view=None):
    """{real path of a model file: router id} for what the router's folder holds (links and folders of links)."""
    view = view or llamacpp.models_dir()
    out = {}
    try:
        names = sorted(os.listdir(view))
    except OSError:
        return out
    for name in names:
        p = os.path.join(view, name)
        if name.startswith("."):
            continue
        if os.path.isdir(p):
            for f in sorted(os.listdir(p)):
                if f.lower().endswith(".gguf") and not f.lower().startswith("mmproj"):
                    out.setdefault(os.path.realpath(os.path.join(p, f)), name)
        elif name.lower().endswith(".gguf") and not name.lower().startswith("mmproj"):
            out.setdefault(os.path.realpath(p), name[:-5])
    return out


class Provider(provbase.GenericProvider):
    _ids = (0.0, {})

    def _models_dir(self):
        return llamacpp.models_dir()

    def can_run(self, model):
        if not enabled():
            return False, "llama.cpp is turned off in Shoggoth's settings."
        return super().can_run(model)

    def ref(self, model_id):
        return model_id                       # the default engine: its names are the ones chats use

    def model_id(self, entry):
        """The router's name for a library entry: what the view already calls its file (a migrated 4.x model keeps its new id), else the
        name library.sync_views gives it (its stem)."""
        if not entry.get("files") or entry.get("complete") is False or str(entry.get("stem", "")).startswith("-"):
            return None
        t, ids = self._ids
        if time.time() - t > 5:
            ids = view_ids()
            self._ids = (time.time(), ids)
        return ids.get(os.path.realpath(entry["files"][0])) or entry["stem"]

    def detect(self):
        b = llamacpp.choose()
        up = llamacpp.running()
        return {"found": bool(b), "bin": b and b["server"],
                "running": [{"port": llamacpp._state["port"], "owner": "zest"}] if up else []}

    def status(self):
        b = llamacpp.choose()
        return dict(self.summary(), installed=bool(b), running=self.detect()["running"], bin=b and b["server"],
                    problems=[llamacpp._state["error"]] if llamacpp._state.get("error") else [],
                    version=(b or {}).get("tag", ""), backend=(b or {}).get("backend", ""), enabled=enabled())

    def models(self):
        if not llamacpp.running():
            return []
        return [{"id": m["id"], "ctx": m["ctx"], "port": llamacpp._state["port"], "owner": "zest", "status": m["status"]}
                for m in llamacpp.models()]

    @staticmethod
    def _is_chat(m):
        """Is this router entry a language model to talk to? Speech, embedding and other files in the folder are not (read from the
        file's own header; a file that can't be read counts as chat, so nothing is hidden by mistake)."""
        a = m.get("args") or []
        path = next((a[i + 1] for i, x in enumerate(a[:-1]) if x in ("--model", "-m")), "")
        if not path:
            return True
        try:
            import gguf
            import library
            h = library._header(path)
            if not h:
                return True
            return gguf.role_of(h.get("architecture", ""), m["id"], os.path.dirname(path)) in ("chat", "unknown")
        except Exception:  # noqa: BLE001
            return True

    def chat_models(self):
        """Every chat model the router can load, the loaded ones marked live."""
        if not llamacpp.running():
            return []
        return [{"id": m["id"], "tools": True, "reasoning": False, "vision": "image" in (m.get("inputs") or []), "ctx": m["ctx"] or 0,
                 "live": m["status"] == "loaded"} for m in llamacpp.models() if not m["id"].lower().startswith("mmproj") and self._is_chat(m)]

    def loaded(self, model_id):
        m = next((m for m in llamacpp.models() if m["id"] == model_id), None) if llamacpp.running() else None
        return (True, m["ctx"]) if m and m["status"] == "loaded" else (False, 0)

    def is_running(self):
        return bool(llamacpp.loaded_ids())

    def endpoint(self, model_id=None):
        ep = llamacpp.endpoint()
        return dict(ep, model=model_id, ctx=self.loaded(model_id)[1] if model_id else 0, owner="zest") if ep else None

    def pi_endpoint(self, zest_port):
        # through Shoggoth, so the request shows on the Server page; Shoggoth's own door asks for its secret
        return {"base_url": "http://127.0.0.1:%d/api/v1" % zest_port, "key": uiauth.token()}

    def start(self):
        """Start the router (no model loaded). Returns True when it answers."""
        try:
            import library
            library.sync_views()                 # the router lists what is in its folder when it starts
        except Exception:  # noqa: BLE001 - a library problem must not keep the engine from starting
            pass
        return llamacpp.start(self._models_dir())

    def ensure(self, model_id, progress, opts=None):
        """Have `model_id` loaded with at least the context asked for (exactly, with opts.exact)."""
        opts = opts or {}
        if not enabled():
            raise provbase.ProviderError("llama.cpp is turned off in Shoggoth's settings.")
        if not llamacpp.running():
            progress("Starting llama.cpp…")
            if not self.start():
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
        elif isl:
            llamacpp.unload(model_id)                # a forced reload
        llamacpp.reload()                            # the router reads the presets again (the library may have chosen a new window)
        progress("Loading %s…" % model_id)
        ok, msg = llamacpp.load(model_id)
        if not ok:
            raise provbase.ProviderError(msg)
        try:
            gb = llamacpp.measured_gb(model_id)      # what the load really took, from the load log
            if not gb:
                gb = llamacpp.measured_process_gb(model_id)   # ... or what the model's own process holds
            if gb:
                import library
                library.record_measured(model_id, gb, self.loaded(model_id)[1] or None)
        except Exception:  # noqa: BLE001 - a measurement is a bonus; the model is loaded either way
            pass
        return self.endpoint(model_id)

    def stop(self, model_id=None):
        """Free one model, or (with no name) every model; the router itself keeps running so the model list stays."""
        if model_id:
            return llamacpp.unload(model_id)
        return self.make_way()

    def make_way(self):
        for mid in llamacpp.loaded_ids():
            ok, msg = llamacpp.unload(mid)
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
        newer = not b or b.get("bundled") or llamacpp._tag_num(rel["tag_name"]) > llamacpp._tag_num(b["tag"])
        return {"supported": True, "available": bool(newer), "detail": {"latest": rel["tag_name"], "current": (b or {}).get("tag")}}

    def update(self, progress):
        return llamacpp.start_update()

    def update_status(self):
        u = dict(llamacpp.UPD)
        st = u.get("state")
        name = llamacpp.NAMES.get(u.get("backend"), "llama.cpp")
        text = {"downloading": "Downloading llama.cpp %s (%s)…" % (u.get("tag") or "", name), "installing": "Installing llama.cpp…",
                "done": "llama.cpp %s is installed. It is used the next time llama.cpp starts." % (u.get("tag") or "")}.get(st, "")
        return {"state": "working" if st in ("downloading", "installing") else st if st in ("done", "error") else "idle",
                "text": text, "error": u.get("error"), "bytes": u.get("bytes"), "total": u.get("total")}

    def selftest(self):
        if not llamacpp.running():
            return []
        status, d = llamacpp.request("GET", "/models")
        ok = status == 200 and isinstance(d.get("data"), list) and all("status" in m for m in d["data"])
        return [{"name": "its model list", "ok": ok, "detail": "" if ok else "GET /models answered %s or changed shape" % status}]
