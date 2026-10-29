"""Tuning for Lemonade models (llama.cpp ones), the way `strata tune auto` does it for Strata.

Lemonade has no tuner of its own. This one measures the model as it is (the baseline, repeated, to learn how noisy a measurement is),
tries one change at a time through Lemonade's own load options, keeps only changes that beat the noise, and checks the combination
against the baseline before saving anything. Only changes that leave the answers alone are tried: batch sizes, flash attention,
threads, which backend and which GPUs. Nothing that changes quantization or the KV cache type. Standard library only.
"""
import http.client
import json
import os
import statistics
import time

import lem
import llamaargs
import store

MAX_TRIALS = 12
SHORT = [{"role": "user", "content": "Write one plain sentence about the sea."}]
SHORT_TOKENS = 64
LONG_TOKENS = 3000                      # about this many prompt tokens for the prefill measurement
MIN_GAIN = 0.03                         # never keep a change that wins by less than 3%, however quiet the measurements were


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------- results kept between runs
def _path():
    return os.path.join(store.data_dir(), "tuning.json")


def results():
    try:
        with open(_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_results(d):
    os.makedirs(os.path.dirname(_path()), exist_ok=True)
    tmp = _path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, _path())


# ---------------------------------------------------------------- editing llama-server arguments
def set_flag(tokens, names, value):
    """Remove every spelling of a flag from the token list (with its value), then add `names[0] value` unless value is None."""
    out, i = [], 0
    while i < len(tokens):
        if tokens[i] in names:
            i += 2 if i + 1 < len(tokens) and not tokens[i + 1].startswith("-") else 1
            continue
        out.append(tokens[i])
        i += 1
    if value is not None:
        out += [names[0], str(value)]
    return out


def get_flag(tokens, names):
    for i, t in enumerate(tokens):
        if t in names and i + 1 < len(tokens):
            return tokens[i + 1]
    return None


def join_args(tokens):
    return " ".join(llamaargs.lem_quote(t) or t for t in tokens)


FLAGS = {"flash": ("--flash-attn", "-fa"), "batch": ("--batch-size", "-b"), "ubatch": ("--ubatch-size", "-ub"), "threads": ("--threads", "-t")}


def candidates(opts, backends, devices, cpus):
    """One-change trials, most promising first: [{label, args:{key: value}, backend, device}]. `opts` are the model's effective options."""
    tokens, _ = llamaargs.lem_split(opts.get("llamacpp_args") or "")
    out = []
    cur_dev = [d for d in str(opts.get("llamacpp_device") or "").split(",") if d]
    if len(devices) > 1:
        for d in devices:
            if [d["id"]] != cur_dev:
                out.append({"label": "only %s" % d["name"], "device": d["id"]})
        if cur_dev and len(cur_dev) < len(devices):
            out.append({"label": "all GPUs", "device": ",".join(d["id"] for d in devices)})
    for b in backends:
        if b != opts.get("llamacpp_backend"):
            out.append({"label": "the %s backend" % b, "backend": b})
    cur = get_flag(tokens, FLAGS["flash"])
    for v in ("on", "off"):
        if cur != v:
            out.append({"label": "flash attention %s" % v, "args": {"flash": v}})
    for k, vals, default in (("ubatch", (256, 1024, 2048), "512"), ("batch", (1024, 4096), "2048")):
        cur = get_flag(tokens, FLAGS[k]) or default
        out += [{"label": "%s %d" % ("micro-batch" if k == "ubatch" else "batch size", v), "args": {k: v}} for v in vals if str(v) != cur]
    cur = get_flag(tokens, FLAGS["threads"])
    for v in sorted({max(1, cpus // 2), cpus}):
        if str(v) != cur:
            out.append({"label": "%d threads" % v, "args": {"threads": v}})
    return out[:MAX_TRIALS]


def apply_change(opts, ch):
    """The options with change `ch` applied: {llamacpp_args, llamacpp_backend, llamacpp_device}."""
    tokens, _ = llamaargs.lem_split(opts.get("llamacpp_args") or "")
    for k, v in (ch.get("args") or {}).items():
        tokens = set_flag(tokens, FLAGS[k], v)
    return {"llamacpp_args": join_args(tokens), "llamacpp_backend": ch.get("backend") or opts.get("llamacpp_backend"),
            "llamacpp_device": ch.get("device") or opts.get("llamacpp_device")}


def merge_changes(chs):
    out = {"args": {}}
    for c in chs:
        out["args"].update(c.get("args") or {})
        for k in ("backend", "device"):
            if c.get(k):
                out[k] = c[k]
    out["label"] = " + ".join(c["label"] for c in chs)
    return out


# ---------------------------------------------------------------- measuring
def _long_prompt():
    para = "The harbour was quiet that morning, and the boats rocked gently against their ropes while gulls wheeled overhead. "
    return [{"role": "user", "content": para * (LONG_TOKENS // 22) + "\nSay only the word: done."}]


def measure_once(model, messages, max_tokens, timeout=600):
    """One streamed request: (time to first token in s, tokens per second after it, prompt tokens). Reasoning text counts as output."""
    h, p = lem.target()
    conn = http.client.HTTPConnection(h, p, timeout=timeout)
    headers = {"Content-Type": "application/json"}
    if lem.key():
        headers["Authorization"] = "Bearer " + lem.key()
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0, "stream": True,
            "stream_options": {"include_usage": True}}
    t0, first, last, chunks, usage = time.time(), None, None, 0, {}
    try:
        conn.request("POST", "/v1/chat/completions", body=json.dumps(body), headers=headers)
        r = conn.getresponse()
        if r.status != 200:
            raise RuntimeError("Lemonade answered %d: %s" % (r.status, r.read(200).decode("utf8", "replace")))
        for raw in r:
            line = raw.decode("utf8", "replace").strip()
            if not line.startswith("data:") or line.endswith("[DONE]"):
                continue
            try:
                o = json.loads(line[5:])
            except ValueError:
                continue
            if o.get("usage"):
                usage = o["usage"]
            for c in o.get("choices") or []:
                d = c.get("delta") or {}
                if d.get("content") or d.get("reasoning_content"):
                    now = time.time()
                    first = first or now
                    last = now
                    chunks += 1
    finally:
        conn.close()
    if first is None:
        raise RuntimeError("The model produced no output.")
    n = int(usage.get("completion_tokens") or chunks)
    span = (last - first) or 1e-6
    return first - t0, (max(n - 1, 1) / span), int(usage.get("prompt_tokens") or LONG_TOKENS)


def measure(model, repeats, cancelled):
    """Decode speed and prefill speed, each the median of `repeats` runs after one untimed warm-up, plus how far the runs spread."""
    measure_once(model, SHORT, 8)                                   # warm-up: first request after a load is slower
    dec, pre = [], []
    for _ in range(repeats):
        if cancelled():
            raise Cancelled()
        _, tps, _ = measure_once(model, SHORT, SHORT_TOKENS)
        dec.append(tps)
        ttft, _, ptoks = measure_once(model, _long_prompt(), 4)
        pre.append(ptoks / max(ttft, 1e-6))
    spread = lambda xs: (max(xs) - min(xs)) / statistics.mean(xs) if len(xs) > 1 else 0.0
    return {"decode": statistics.median(dec), "prefill": statistics.median(pre), "noise": max(spread(dec), spread(pre))}


def score(m, base, use):
    """Weighted geometric gain over the baseline: chat leans on decode speed, coding on prefill as well."""
    w = 0.5 if use == "coding" else 0.3
    return (m["decode"] / base["decode"]) ** (1 - w) * (m["prefill"] / base["prefill"]) ** w - 1


# ---------------------------------------------------------------- the run
def options_of(model):
    st, d = lem.request("GET", "/api/v1/models/%s/options" % model, timeout=15)
    return d if st == 200 else {}


def _load(model, ctx, opts):
    lem.request("POST", "/v1/unload", {"model_name": model}, timeout=60)
    body = {"model_name": model, "ctx_size": ctx}
    for k in ("llamacpp_args", "llamacpp_backend", "llamacpp_device"):
        if opts.get(k):
            body[k] = opts[k]
    st, p = lem.request("POST", "/v1/load", body, timeout=900)
    if st != 200:
        raise RuntimeError(lem.error_text(p, "Lemonade couldn't load the model with those options."))


def plan(model, backends, devices, cpus):
    d = options_of(model)
    eff = d.get("effective") or {}
    n = len(candidates(eff, backends, devices, cpus))
    return {"trials": n, "minutes": max(5, round((n + 4) * 1.5)), "recipe": d.get("recipe")}


def run(model, opts, job, backends, devices, cpus):
    """Tune one llama.cpp model. Returns the result dict that is also saved. Raises RuntimeError / Cancelled."""
    use = "coding" if opts.get("use") == "coding" else "chat"
    d = options_of(model)
    if d.get("recipe") != "llamacpp":
        raise RuntimeError("Shoggoth can tune llama.cpp models. This one runs on a different engine.")
    eff, saved = d.get("effective") or {}, d.get("saved") or {}
    ctx = max(4096, min(int(d.get("resolved_ctx_size") or 8192), 16384))
    cands = candidates(eff, backends, devices, cpus)
    total = len(cands) + 3
    done = [0]

    def step(text):
        done[0] += 1
        job.update(text=text, progress=min(0.99, done[0] / total))

    def trial(options, label, repeats):
        if job.cancelled():
            raise Cancelled()
        job.update(text=label)
        _load(model, ctx, options)
        return measure(model, repeats, job.cancelled)

    base_opts = {k: eff.get(k) for k in ("llamacpp_args", "llamacpp_backend", "llamacpp_device")}
    try:
        base = trial(base_opts, "Measuring the model as it is…", 3)
        step("Baseline: %.1f tokens/s, prefill %.0f tokens/s" % (base["decode"], base["prefill"]))
        thr = max(MIN_GAIN, 2 * base["noise"])
        kept, tried = [], []
        for c in cands:
            try:
                m = trial(apply_change(eff, c), "Trying %s…" % c["label"], 2)
            except Cancelled:
                raise
            except RuntimeError as e:
                tried.append({"label": c["label"], "error": str(e)[:200]})
                step("Skipped %s: Lemonade wouldn't run it." % c["label"])
                continue
            g = score(m, base, use)
            tried.append({"label": c["label"], "gain": round(g, 4), "decode": round(m["decode"], 1), "prefill": round(m["prefill"], 1)})
            if g > thr:
                kept.append((g, c))
            step("%s: %+.1f%%" % (c["label"], g * 100))
        best_change, best_gain, best_m = None, 0.0, base
        if kept:
            kept.sort(key=lambda x: -x[0])
            top = kept[0]
            best_change, best_gain = top[1], top[0]
            if len(kept) > 1:
                combo = merge_changes([c for _, c in kept])
                job.update(text="Checking the best changes together…")
                try:
                    m = trial(apply_change(eff, combo), "Checking the best changes together…", 3)
                    g = score(m, base, use)
                    if g > best_gain:
                        best_change, best_gain, best_m = combo, g, m
                except RuntimeError:
                    pass
            if best_gain <= thr:
                best_change = None
        step("Done measuring.")
        res = {"engine": "lemonade", "model": model, "when": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "use": use, "noise": round(base["noise"], 4),
               "baseline": {"decode": round(base["decode"], 1), "prefill": round(base["prefill"], 1)}, "tried": tried,
               "baseline_tok_s": round(base["decode"], 1), "decode_tok_s": round(best_m["decode"], 1) if best_change else round(base["decode"], 1),
               "outcome": "improved" if best_change else "default_confirmed", "gain_pct": round(best_gain * 100, 1) if best_change else 0.0,
               "best": {"decode": round(best_m["decode"], 1), "prefill": round(best_m["prefill"], 1)} if best_change else None,
               "change": best_change["label"] if best_change else "", "saved_before": {k: saved.get(k) for k in ("llamacpp_args", "llamacpp_backend", "llamacpp_device")}}
        if best_change:
            new = apply_change(eff, best_change)
            body = {k: new[k] for k in new if new[k] != eff.get(k)}
            st, p = lem.request("POST", "/api/v1/models/%s/options" % model, body, timeout=30)
            if st != 200:
                raise RuntimeError(lem.error_text(p, "Lemonade wouldn't save the new options."))
            res["applied"] = body
        r = results()
        r[model] = res
        _save_results(r)
        return res
    finally:
        lem.request("POST", "/v1/unload", {"model_name": model}, timeout=60)       # the next load uses what is saved


def rollback(model):
    """Put the model's options back as they were before the tune. Returns (ok, message)."""
    r = results()
    res = r.get(model)
    if not res or not res.get("applied"):
        return False, "There is no tuning to undo for that model."
    before = res.get("saved_before") or {}
    body = {k: before.get(k) for k in res["applied"]}          # null clears a saved value, as the options page does
    st, p = lem.request("POST", "/api/v1/models/%s/options" % model, body, timeout=30)
    if st != 200:
        return False, lem.error_text(p, "Lemonade wouldn't restore the options.")
    res["applied"], res["rolled_back"] = None, time.strftime("%Y-%m-%dT%H:%M:%S%z")
    _save_results(dict(r, **{model: res}))
    return True, ""
