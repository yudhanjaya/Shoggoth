"""One place to tune a model, whichever engine runs it.

A tune needs the machine's graphics cards to itself for minutes, so there is one job at a time, and nothing starts until the person
has seen the plan. Lemonade's llama.cpp models are tuned by lemtune.py: a gain over the untuned baseline, what changed, and a way back.
"""
import os
import threading
import time

import agent
import hardware
import lem
import lemtune
import router

_lock = threading.Lock()
JOB = {"id": 0, "engine": None, "target": None, "title": "", "state": "idle", "text": "", "progress": None, "elapsed": 0,
       "error": None, "result": None, "started": 0}
_cancel = threading.Event()
before_run = []                           # callables run just before a job takes the GPUs (e.g. free what Shoggoth itself is running)


def _hold():
    """While a tune runs it has the graphics cards: no chat or app may start or use a model (that would spoil the measurements, or run out of memory)."""
    return "Shoggoth is tuning %s right now, which needs the graphics cards to itself. Wait for it to finish, or cancel it on Advanced > Tuning." % (JOB["title"] or "a model") \
        if JOB["state"] == "running" else ""


router.holds.append(_hold)


class _Job:
    def update(self, **kw):
        JOB.update(kw)

    def cancelled(self):
        return _cancel.is_set()


def _lemonade_backends():
    for r in hardware.backends():
        if r.get("recipe") == "llamacpp":
            return [b["name"] for b in r["backends"] if b.get("state") == "installed"]
    return []


def _lemonade_devices():
    try:
        import server
        info = hardware.info(server.current_mode(), server.VERSION)
    except Exception:  # noqa: BLE001
        return []
    return [{"id": d["id"], "name": d.get("name") or d["id"]} for d in info.get("devices") or [] if d.get("id")]


def lemonade_env():
    """What a Lemonade tune depends on: the llama.cpp backend and its build, and the Lemonade version. A tune made under other values is stale."""
    env = {"backend": "", "llama": "", "lemonade": ""}
    try:
        for r in hardware.backends():
            if r.get("recipe") == "llamacpp":
                cur = r.get("current") if r.get("current") not in (None, "", "auto") else r.get("default")
                b = next((x for x in r["backends"] if x["name"] == cur), None)
                env["backend"], env["llama"] = cur or "", (b or {}).get("version") or ""
        import engine
        import store
        a = engine.summary(store.load()).get("active")
        env["lemonade"] = (a or {}).get("version") or ""
    except Exception:  # noqa: BLE001
        pass
    return env


def _record(engine, model, state, **kw):
    import modelmeta
    meta = modelmeta.get(model)
    rec = {"engine": engine, "model": model, "state": state, "source": "lemtune", "gain_pct": None, "decode_tok_s": None,
           "baseline_tok_s": None, "when": None, "can_undo": False, "preset": None, "details": {}, "speed": meta.get("speed"), "headroom_gb": meta.get("headroom_gb")}
    rec.update(kw)
    return rec


def tuning_for(model):
    """What is known about tuning one model, whichever engine runs it, in one shape. States: tuned, default_best (the defaults were
    already the fastest), stale (made under other versions or settings), not_tuned. Lemonade's come from lemtune's results."""
    r = lemtune.results().get(model)
    if not r or r.get("outcome") is None:
        return _record("lemonade", model, "not_tuned")
    now, then = lemonade_env(), r.get("env") or {}
    changed = [k for k in ("backend", "llama", "lemonade") if then.get(k) and now.get(k) and then[k] != now[k]]
    state = "stale" if changed else "default_best" if r["outcome"] == "default_confirmed" else "tuned"
    return _record("lemonade", model, state, gain_pct=r.get("gain_pct"), decode_tok_s=r.get("decode_tok_s"), baseline_tok_s=r.get("baseline_tok_s"), when=r.get("when"),
                   can_undo=bool(r.get("applied")), details={"reasons": ["%s changed (%s to %s)" % (k, then[k], now[k]) for k in changed], "outcome": r["outcome"], "change": r.get("change"), "noise": r.get("noise")})


def _lemonade_models():
    try:
        return lem.get("/v1/models").get("data", []) if lem.healthy() else []
    except Exception:  # noqa: BLE001
        return []


def targets():
    """Everything that can be tuned, with what is known about each: [{engine, id, title, tuned, tuning, ...}]. `tuned` is the short form the Tuning page reads."""
    out = []

    def short(t):
        return {"has": t["state"] != "not_tuned", "stale": t["state"] == "stale", "reasons": t["details"].get("reasons") or [], "gain_pct": t["gain_pct"],
                "decode": t["decode_tok_s"], "baseline": t["baseline_tok_s"], "outcome": t["details"].get("outcome"), "when": t["when"],
                "change": t["details"].get("change"), "can_undo": t["can_undo"], "state": t["state"]}
    for m in _lemonade_models():
        if m.get("downloaded") and m.get("recipe") == "llamacpp" and "embeddings" not in (m.get("labels") or []) and "reranking" not in (m.get("labels") or []):
            t = tuning_for(m["id"])
            out.append({"engine": "lemonade", "id": m["id"], "title": m["id"], "detail": "Lemonade · llama.cpp", "tuned": short(t), "tuning": t})
    return out


def blockers(engine, target):
    """Reasons a tune can't start now, in words a person can act on."""
    why = []
    if JOB["state"] == "running":
        why.append("Another tune is running.")
    if any(s.running for s in list(agent._sessions.values())):
        why.append("A chat is answering right now. Wait for it to finish.")
    elif router.anything_busy():
        why.append("Another app is getting an answer from a model right now. Wait for it to finish.")
    return why


def plan(engine, target, opts):
    """What a tune would do, for the person to read before agreeing: time, what stops, and anything in the way."""
    stops = []
    loaded = lem.get("/v1/health").get("all_models_loaded", []) if lem.healthy() else []
    stops += ["%s (Lemonade)" % m.get("model_name") for m in loaded]
    if opts.get("kind") == "test":
        if engine != "lemonade":
            return {"ok": False, "blockers": ["Testing speed works for Lemonade's models."], "stops": stops}
        b = blockers(engine, target)
        return {"ok": not b, "blockers": b, "stops": stops, "minutes": 3, "note": "Shoggoth loads the model once and measures how fast it answers. Nothing is changed."}
    p = lemtune.plan(target, _lemonade_backends(), _lemonade_devices(), os.cpu_count() or 8)
    if p.get("recipe") != "llamacpp":
        return {"ok": False, "blockers": ["Shoggoth can tune llama.cpp models. This one runs on a different engine."], "stops": stops}
    minutes, note = p["minutes"], "Shoggoth loads the model with %d different settings, measures each, and keeps only what is clearly faster. Answers stay the same: it never changes the quantization or the KV cache." % p["trials"]
    b = blockers(engine, target)
    return {"ok": not b, "blockers": b, "stops": stops, "minutes": minutes, "note": note}


def start(engine, target, opts):
    with _lock:
        b = blockers(engine, target)
        if opts.get("kind") == "test" and engine != "lemonade":
            b.append("Testing speed works for Lemonade's models.")
        if b:
            raise ValueError(" ".join(b))
        _cancel.clear()
        JOB.update(id=JOB["id"] + 1, engine=engine, target=target, title=target, state="running", text="Getting the graphics cards ready…",
                   progress=None, elapsed=0, error=None, result=None, started=time.time())
    threading.Thread(target=_run, args=(engine, target, dict(opts)), daemon=True).start()
    return True


def _run(engine, target, opts):
    job = _Job()
    try:
        if lem.healthy():
            lem.request("POST", "/v1/unload", {}, timeout=60)
        for f in before_run:
            f()
        if opts.get("kind") == "test":
            res = lemtune.test_speed(target, job)
        else:
            res = lemtune.run(target, opts, job, _lemonade_backends(), _lemonade_devices(), os.cpu_count() or 8, env=lemonade_env())
        JOB.update(state="done", result=res, text="Done.", progress=1.0)
    except lemtune.Cancelled:
        JOB.update(state="cancelled", text="Cancelled. Nothing was changed.")
    except Exception as e:  # noqa: BLE001
        JOB.update(state="error", error=str(e) or e.__class__.__name__)


def cancel():
    _cancel.set()
    return JOB["state"] == "running"


def action(engine, target, act):
    """Undo for Lemonade's tuning. Returns (ok, message)."""
    if JOB["state"] == "running":
        return False, "A tune is running."
    if act == "undo":
        return lemtune.rollback(target)
    return False, "Not available for that engine."


def status():
    return dict(JOB)
