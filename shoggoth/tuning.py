"""One place to tune a model, whichever engine runs it.

A tune needs the machine's graphics cards to itself for minutes, so there is one job at a time, and nothing starts until the person
has seen the plan. llama.cpp models are tuned by llamatune.py (with llama-bench): a gain over the untuned baseline, what changed, and a way back.
"""
import os
import threading
import time

import agent
import hardware
import llamacpp
import llamatune
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


def _devices():
    try:
        import server
        info = hardware.info(server.current_mode(), server.VERSION)
    except Exception:  # noqa: BLE001
        return []
    return [{"id": d["id"], "name": d.get("name") or d["id"]} for d in info.get("devices") or [] if d.get("id")]


def llamacpp_env():
    """What a tune depends on: the llama.cpp build and its backend. A tune made under other values is stale."""
    try:
        return llamatune.env_info()
    except Exception:  # noqa: BLE001
        return {"llama": "", "backend": ""}


def _record(engine, model, state, **kw):
    import modelmeta
    meta = modelmeta.get(model)
    rec = {"engine": engine, "model": model, "state": state, "source": "llamatune", "gain_pct": None, "decode_tok_s": None,
           "baseline_tok_s": None, "when": None, "can_undo": False, "preset": None, "details": {}, "speed": meta.get("speed"), "headroom_gb": meta.get("headroom_gb")}
    rec.update(kw)
    return rec


def tuning_for(model):
    """What is known about tuning one model, whichever engine runs it, in one shape. States: tuned, default_best (the defaults were
    already the fastest), stale (made under other versions or settings), not_tuned. They come from llamatune's results."""
    r = llamatune.results().get(model)
    if not isinstance(r, dict) or r.get("outcome") is None:
        return _record("llamacpp", model, "not_tuned")
    now, then = llamacpp_env(), r.get("env") or {}
    changed = [k for k in ("backend", "llama") if then.get(k) and now.get(k) and then[k] != now[k]]
    state = "stale" if changed else "default_best" if r["outcome"] == "default_confirmed" else "tuned"
    lemonade = llamatune.written_by_lemonade(r)
    reasons = ["%s changed (%s to %s)" % (k, then[k], now[k]) for k in changed]
    if lemonade:
        reasons.insert(0, llamatune.LEMONADE_UNDO)          # first, because the page shows only the first reason
    return _record("llamacpp", model, state, gain_pct=r.get("gain_pct"), decode_tok_s=r.get("decode_tok_s"), baseline_tok_s=r.get("baseline_tok_s"), when=r.get("when"),
                   can_undo=bool(r.get("applied")) and not lemonade, details={"reasons": reasons, "outcome": r["outcome"], "change": r.get("change"), "noise": r.get("noise"), "lemonade": lemonade})


def _models():
    try:
        return llamacpp.models() if llamacpp.running() else []
    except Exception:  # noqa: BLE001
        return []


def targets():
    """Everything that can be tuned, with what is known about each: [{engine, id, title, tuned, tuning, ...}]. `tuned` is the short form the Tuning page reads."""
    out = []

    def short(t):
        return {"has": t["state"] != "not_tuned", "stale": t["state"] == "stale", "reasons": t["details"].get("reasons") or [], "gain_pct": t["gain_pct"],
                "decode": t["decode_tok_s"], "baseline": t["baseline_tok_s"], "outcome": t["details"].get("outcome"), "when": t["when"],
                "change": t["details"].get("change"), "can_undo": t["can_undo"], "state": t["state"]}
    for m in _models():
        if m.get("path"):
            t = tuning_for(m["id"])
            out.append({"engine": "llamacpp", "id": m["id"], "title": m["id"], "detail": "llama.cpp", "tuned": short(t), "tuning": t})
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
    stops = ["%s (llama.cpp)" % m["id"] for m in _models() if m["status"] == "loaded"]
    b = blockers(engine, target)
    if opts.get("kind") == "test":
        return {"ok": not b, "blockers": b, "stops": stops, "minutes": 3, "note": "Shoggoth runs llama.cpp's own speed test on the model file. Nothing is changed."}
    p = llamatune.plan(target, [], _devices(), os.cpu_count() or 8)
    if p.get("recipe") != "llamacpp":
        return {"ok": False, "blockers": ["Shoggoth can't find the file for that model."], "stops": stops}
    note = "Shoggoth runs llama.cpp's speed test on the model file with %d different settings, and keeps only what is clearly faster. Answers stay the same: it never changes the quantization or the KV cache." % p["trials"]
    return {"ok": not b, "blockers": b, "stops": stops, "minutes": p["minutes"], "note": note}


def start(engine, target, opts):
    with _lock:
        b = blockers(engine, target)
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
        for f in before_run:
            f()
        if opts.get("kind") == "test":
            res = llamatune.test_speed(target, job)
        else:
            res = llamatune.run(target, opts, job, [], _devices(), os.cpu_count() or 8, env=llamacpp_env())
        JOB.update(state="done", result=res, text="Done.", progress=1.0)
    except llamatune.Cancelled:
        JOB.update(state="cancelled", text="Cancelled. Nothing was changed.")
    except Exception as e:  # noqa: BLE001
        JOB.update(state="error", error=str(e) or e.__class__.__name__)


def cancel():
    _cancel.set()
    return JOB["state"] == "running"


def action(engine, target, act):
    """Undo for a tune (puts the model's presets.ini entries back). Returns (ok, message)."""
    if JOB["state"] == "running":
        return False, "A tune is running."
    if act == "undo":
        return llamatune.rollback(target)
    return False, "Not available for that engine."


def status():
    return dict(JOB)
