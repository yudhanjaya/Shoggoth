"""One place to tune a model, whichever engine runs it.

A tune needs the machine's graphics cards to itself for minutes to hours, so there is one job at a time across both engines, and
nothing starts until the person has seen the plan. Strata tunes with its own `strata tune auto`; Lemonade's llama.cpp models are
tuned by lemtune.py. Both report the same shape: a gain over the untuned baseline, what changed, and a way back.
"""
import os
import threading
import time

import agent
import hardware
import lem
import lemtune
import router
import strata

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


def targets():
    """Everything that can be tuned, with what is known about each: [{engine, id, title, tuned, ...}]."""
    out = []
    if strata.binary():
        for p in strata.presets():
            t = strata.tuning_status(p["name"])
            out.append({"engine": "strata", "id": p["name"], "title": p["name"], "detail": " · ".join(x for x in (p.get("model"), "%dk context" % (p["context"] // 1024) if p.get("context") else "") if x),
                        "tuned": {"has": t["has"], "stale": t["state"] == "stale", "reasons": t["reasons"], "gain_pct": t["gain_pct"], "decode": t["decode_tok_s"],
                                  "baseline": t["baseline_tok_s"], "outcome": t["outcome"], "when": t["when"], "can_undo": t["has"]}})
    saved = lemtune.results()
    for m in lem.get("/v1/models").get("data", []):
        if m.get("downloaded") and m.get("recipe") == "llamacpp" and "embeddings" not in (m.get("labels") or []) and "reranking" not in (m.get("labels") or []):
            r = saved.get(m["id"]) or {}
            out.append({"engine": "lemonade", "id": m["id"], "title": m["id"], "detail": "Lemonade · llama.cpp",
                        "tuned": {"has": bool(r), "stale": False, "gain_pct": r.get("gain_pct"), "decode": (r.get("best") or r.get("baseline") or {}).get("decode"),
                                  "baseline": (r.get("baseline") or {}).get("decode"), "outcome": r.get("outcome"), "when": r.get("when"),
                                  "change": r.get("change"), "can_undo": bool(r.get("applied"))}})
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
    theirs = [r for r in strata.running() if r["owner"] == "yours" and r["source"] != "saved" and r["state"] in ("ready", "loading")]
    if theirs:
        why.append("A Strata you started is running (%s). Tuning needs the graphics cards to itself, so stop it first." % (theirs[0]["name"] or "a model"))
    if engine == "strata" and not strata.binary():
        why.append("Strata isn't installed.")
    return why


def plan(engine, target, opts):
    """What a tune would do, for the person to read before agreeing: time, what stops, and anything in the way."""
    stops = []
    z = strata.zest_server()
    if z:
        stops.append("the Strata Shoggoth started (%s)" % (z["name"] or "a model"))
    loaded = lem.get("/v1/health").get("all_models_loaded", []) if lem.healthy() else []
    stops += ["%s (Lemonade)" % m.get("model_name") for m in loaded]
    if engine == "strata":
        mode = "thorough" if opts.get("mode") == "thorough" else "quick"
        minutes, note = strata.tune_minutes(mode), "Strata measures its own settings and keeps only what is faster than the default by more than the noise."
    else:
        p = lemtune.plan(target, _lemonade_backends(), _lemonade_devices(), os.cpu_count() or 8)
        if p.get("recipe") != "llamacpp":
            return {"ok": False, "blockers": ["Shoggoth can tune llama.cpp models. This one runs on a different engine."], "stops": stops}
        minutes, note = p["minutes"], "Shoggoth loads the model with %d different settings, measures each, and keeps only what is clearly faster. Answers stay the same: it never changes the quantization or the KV cache." % p["trials"]
    b = blockers(engine, target)
    return {"ok": not b, "blockers": b, "stops": stops, "minutes": minutes, "note": note}


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
        z = strata.zest_server()
        if z:                                                   # Shoggoth's own Strata gives up the cards
            JOB["text"] = "Stopping the Strata Shoggoth started…"
            strata.stop(z["url"])
            time.sleep(3)
        if lem.healthy():
            lem.request("POST", "/v1/unload", {}, timeout=60)
        for f in before_run:
            f()
        if engine == "strata":
            res = strata.tune_run(target, opts, job)
        else:
            res = lemtune.run(target, opts, job, _lemonade_backends(), _lemonade_devices(), os.cpu_count() or 8)
        JOB.update(state="done", result=res, text="Done.", progress=1.0)
    except (strata.Cancelled, lemtune.Cancelled):
        JOB.update(state="cancelled", text="Cancelled. Nothing was changed.")
    except Exception as e:  # noqa: BLE001
        JOB.update(state="error", error=str(e) or e.__class__.__name__)


def cancel():
    _cancel.set()
    return JOB["state"] == "running"


def action(engine, target, act):
    """off / on / undo for Strata's saved tuning; undo for Lemonade's. Returns (ok, message)."""
    if JOB["state"] == "running":
        return False, "A tune is running."
    if engine == "strata":
        return strata.tune_action(target, act)
    if act == "undo":
        return lemtune.rollback(target)
    return False, "Not available for that engine."


def status():
    return dict(JOB)
