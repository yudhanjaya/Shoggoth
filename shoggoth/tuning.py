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


def _strata_presets_for(model):
    """The saved Strata setups that start `model` (picker id, with or without the strata/ prefix), best first: tuned ones, then Shoggoth-made, then most recently used."""
    bare = strata.bare(model) if hasattr(strata, "bare") else str(model)
    inst = next((m for m in strata.installed_models() if str(m.get("id", "")).lower() == str(bare).lower()), None) or {"id": bare}
    mid = str(inst.get("id") or "").lower()
    ok = [p for p in strata._presets_cached() if p.get("model") and (mid == str(p["model"]).lower() or mid.endswith(str(p["model"]).lower()))]
    ok.sort(key=lambda p: str(p.get("last_used") or ""), reverse=True)
    ok.sort(key=lambda p: not p["name"].startswith(strata.OWN_PRESETS))
    ok.sort(key=lambda p: not p.get("tuned_preset"))                  # tuned_preset is the reliable signal; tuned_at is set even when nothing was tuned
    return ok


def _record(engine, model, state, **kw):
    import modelmeta
    meta = modelmeta.get(model)
    rec = {"engine": engine, "model": model, "state": state, "source": "strata presets" if engine == "strata" else "lemtune", "gain_pct": None, "decode_tok_s": None,
           "baseline_tok_s": None, "when": None, "can_undo": False, "preset": None, "details": {}, "speed": meta.get("speed"), "headroom_gb": meta.get("headroom_gb")}
    rec.update(kw)
    return rec


def tuning_for(model):
    """What is known about tuning one model, whichever engine runs it, in one shape. States: tuned, default_best (the defaults were
    already the fastest), stale (made under other versions or settings), not_tuned. The page never branches on the engine.
    Strata stays the source of truth for its own tunings (read only here); Lemonade's come from lemtune's results."""
    if str(model).startswith("strata/"):
        ps = _strata_presets_for(model) if strata.binary() else []
        if not ps:
            return _record("strata", model, "not_tuned", details={"why": "It has no saved Strata setup yet."})
        p = ps[0]
        if not p.get("tuned_preset"):
            return _record("strata", model, "not_tuned", preset=p["name"])
        t = strata.tuning_status(p["name"])
        state = "stale" if t["state"] == "stale" else "default_best" if t["outcome"] == "default_confirmed" else "tuned"
        return _record("strata", model, state, preset=p["name"], gain_pct=t["gain_pct"], decode_tok_s=t["decode_tok_s"], baseline_tok_s=t["baseline_tok_s"],
                       when=t["when"], can_undo=True, details={"reasons": t["reasons"], "outcome": t["outcome"], "mode": t["mode"], "tuned_at": p.get("tuned_at")})
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
    if strata.binary():
        for p in strata._presets_cached():
            t = tuning_for("strata/" + str(p.get("model")))
            out.append({"engine": "strata", "id": p["name"], "title": p["name"], "detail": " · ".join(x for x in (p.get("model"), "%dk context" % (p["context"] // 1024) if p.get("context") else "") if x),
                        "tuned": short(dict(t, state=t["state"] if t["preset"] == p["name"] else "not_tuned", details=t["details"] if t["preset"] == p["name"] else {})), "tuning": t if t["preset"] == p["name"] else None})
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
    if opts.get("kind") == "test":
        if engine != "lemonade":
            return {"ok": False, "blockers": ["Testing speed on its own works for Lemonade's models. For Strata's, use Tune."], "stops": stops}
        b = blockers(engine, target)
        return {"ok": not b, "blockers": b, "stops": stops, "minutes": 3, "note": "Shoggoth loads the model once and measures how fast it answers. Nothing is changed."}
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
        if opts.get("kind") == "test" and engine != "lemonade":
            b.append("Testing speed on its own works for Lemonade's models. For Strata's, use Tune.")
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
            if opts.get("kind") == "test":
                res = lemtune.test_speed(target, job)
            else:
                res = lemtune.run(target, opts, job, _lemonade_backends(), _lemonade_devices(), os.cpu_count() or 8, env=lemonade_env())
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
