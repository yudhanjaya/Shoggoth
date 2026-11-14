"""Tuning for llama.cpp models, by trying settings one at a time and keeping only what is clearly faster.

Shoggoth measures with the `llama-bench` that ships in the same llama.cpp build as `llama-server`, run against the model's GGUF file.
It measures the model as it is (the baseline, repeated, to learn how noisy a measurement is), tries one change at a time, keeps only
changes that beat the noise, and checks the combination against the baseline before saving anything. Only changes that leave the answers
alone are tried: batch sizes, flash attention, threads, which GPUs, and how many MoE layers sit on the CPU. Nothing that changes
quantization or the KV cache type. The winning flags are written to the router's presets.ini as llama-server preset keys; the gain and
the way back are kept in tuning.json. Standard library only.
"""
import json
import os
import statistics
import subprocess
import threading
import time

import llamaargs
import llamacpp
import store

MAX_TRIALS = 12
BENCH_PROMPT = 1024                     # prompt tokens for the prefill measurement
BENCH_GEN = 64                          # tokens for the decode measurement
BENCH_TIMEOUT = 1200
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


# ---------------------------------------------------------------- the model's flags (llama-server preset keys)
BENCH_FLAGS = {"n-gpu-layers": "-ngl", "n-cpu-moe": "-ncmoe", "flash-attn": "-fa", "batch-size": "-b", "ubatch-size": "-ub", "threads": "-t",
               "cache-type-k": "-ctk", "cache-type-v": "-ctv", "main-gpu": "-mg", "split-mode": "-sm", "tensor-split": "-ts", "device": "-dev"}
ALIASES = {"ngl": "n-gpu-layers", "gpu-layers": "n-gpu-layers", "fa": "flash-attn", "b": "batch-size", "ub": "ubatch-size", "t": "threads",
           "ctk": "cache-type-k", "ctv": "cache-type-v", "mg": "main-gpu", "sm": "split-mode", "ts": "tensor-split", "dev": "device",
           "ncmoe": "n-cpu-moe"}
TUNED_KEYS = ("n-gpu-layers", "n-cpu-moe", "flash-attn", "batch-size", "ubatch-size", "threads", "device")


def _norm(flags):
    out = {}
    for k, v in (flags or {}).items():
        k = str(k).lstrip("-").replace("_", "-")
        out[ALIASES.get(k, k)] = v
    return out


def saved(model):
    """The model's own section of presets.ini."""
    return _norm(llamacpp.read_presets().get(model))


def effective(model):
    """The flags the router gives the model: Shoggoth's defaults, then the `*` section, then the model's own preset."""
    pres = llamacpp.read_presets()
    out = _norm(llamacpp.DEFAULTS)
    out.update(_norm(pres.get("*")))
    out.update(_norm(pres.get(model)))
    return out


def _apply_to_presets(model, changes):
    """Merge `changes` {key: value or None} into the model's section and write presets.ini back. None removes the key."""
    pres = llamacpp.read_presets()
    defaults = pres.pop("*", None)
    sec = pres.setdefault(model, {})
    for k, v in changes.items():
        if v is None:
            sec.pop(k, None)
        else:
            sec[k] = v
    llamacpp.write_presets(pres, defaults)
    try:
        if llamacpp.running():
            llamacpp.reload()
    except Exception:  # noqa: BLE001
        pass


def bench_args(flags):
    """llama-bench arguments for a set of preset flags. Flags llama-bench has no equivalent for (context size, jinja ...) are left out."""
    out = []
    for k, v in _norm(flags).items():
        flag = BENCH_FLAGS.get(k)
        if not flag or v is None or str(v) == "":
            continue
        v = str(v)
        if k == "flash-attn":
            v = {"true": "on", "1": "on", "false": "off", "0": "off"}.get(v.lower(), v.lower())
        out += [flag, v]
    return out


def model_path(model):
    """The GGUF file behind a model id: the preset's own `model` key, else what the router reports, else a file in the library's views."""
    p = saved(model).get("model")
    if p and os.path.isfile(p):
        return p
    try:
        for m in llamacpp.models():
            if m["id"] == model and m.get("path") and os.path.isfile(m["path"]):
                return m["path"]
    except Exception:  # noqa: BLE001
        pass
    for dirpath, _, files in os.walk(os.path.join(store.data_dir(), "views")):
        for f in files:
            if f.lower().endswith(".gguf") and os.path.splitext(f)[0] == model:
                return os.path.realpath(os.path.join(dirpath, f))
    return ""


def bench_exe():
    """(llama-bench path, its folder) from the llama.cpp build in use, or raises."""
    b = llamacpp.choose()
    exe = os.path.join(b["dir"], "llama-bench") if b else ""
    if not exe or not os.path.isfile(exe):
        raise RuntimeError("Shoggoth can't find llama-bench in the llama.cpp it runs. Install or update llama.cpp, then try again.")
    return exe, b["dir"]


def env_info():
    """What a tune depends on: the llama.cpp build and its backend. A tune made under other values is stale."""
    b = llamacpp.choose()
    return {"llama": b["tag"] if b else "", "backend": b["backend"] if b else ""}


# ---------------------------------------------------------------- one-change trials
def candidates(flags, devices, cpus, moe=False):
    """One-change trials, most promising first: [{label, flags:{preset key: value}}]. `flags` are the model's effective flags."""
    flags = _norm(flags)
    out = []
    cur_dev = [d for d in str(flags.get("device") or "").split(",") if d]
    if len(devices) > 1:
        for d in devices:
            if [d["id"]] != cur_dev:
                out.append({"label": "only %s" % d["name"], "flags": {"device": d["id"]}})
        if cur_dev and len(cur_dev) < len(devices):
            out.append({"label": "all GPUs", "flags": {"device": ",".join(d["id"] for d in devices)}})
    if moe:
        cur = int(flags.get("n-cpu-moe") or 0)
        out += [{"label": "%d expert layers on the CPU" % v, "flags": {"n-cpu-moe": v}} for v in (0, 4, 8, 16) if v != cur]
    cur = str(flags.get("flash-attn") or "auto").lower()
    cur = {"true": "on", "false": "off"}.get(cur, cur)
    for v in ("on", "off"):
        if cur != v:
            out.append({"label": "flash attention %s" % v, "flags": {"flash-attn": v}})
    for k, vals, default, name in (("ubatch-size", (256, 1024, 2048), "512", "micro-batch"), ("batch-size", (1024, 4096), "2048", "batch size")):
        cur = str(flags.get(k) or default)
        out += [{"label": "%s %d" % (name, v), "flags": {k: v}} for v in vals if str(v) != cur]
    cur = str(flags.get("threads") or "")
    for v in sorted({max(1, cpus // 2), cpus}):
        if str(v) != cur:
            out.append({"label": "%d threads" % v, "flags": {"threads": v}})
    return out[:MAX_TRIALS]


def apply_change(flags, ch):
    return dict(_norm(flags), **(ch.get("flags") or {}))


def merge_changes(chs):
    out = {"flags": {}}
    for c in chs:
        out["flags"].update(c.get("flags") or {})
    out["label"] = " + ".join(c["label"] for c in chs)
    return out


def _is_moe(path):
    try:
        import gguf
        h = gguf.read_header(path) or {}
        return any(str(k).endswith(".expert_count") and v for k, v in (h.get("kv") or {}).items())
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- measuring
def parse_bench(text):
    """llama-bench's `-o json` output -> {decode, prefill, noise} in tokens/s. Rows with only a prompt are prefill (pp), rows with only
    generation are decode (tg); each is the median of its samples. `noise` is how far the samples spread, as a fraction of the speed."""
    try:
        rows = json.loads(text)
    except ValueError:
        i, j = text.find("["), text.rfind("]")
        try:
            rows = json.loads(text[i:j + 1]) if 0 <= i < j else []
        except ValueError:
            rows = []
    got, spread = {}, []
    for r in rows if isinstance(rows, list) else []:
        n_p, n_g = int(r.get("n_prompt") or 0), int(r.get("n_gen") or 0)
        key = "prefill" if n_p and not n_g else "decode" if n_g and not n_p else None
        samples = [float(x) for x in r.get("samples_ts") or [] if x]
        speed = statistics.median(samples) if samples else float(r.get("avg_ts") or 0)
        if not key or speed <= 0:
            continue
        got[key] = speed
        if len(samples) > 1:
            spread.append((max(samples) - min(samples)) / statistics.mean(samples))
        elif r.get("stddev_ts") and r.get("avg_ts"):
            spread.append(float(r["stddev_ts"]) / float(r["avg_ts"]) * 2)
    if "decode" not in got or "prefill" not in got:
        raise RuntimeError("llama-bench didn't report both prompt and generation speed.")
    return {"decode": got["decode"], "prefill": got["prefill"], "noise": max(spread) if spread else 0.0}


def bench(path, flags, repeats, cancelled=None):
    """Run llama-bench once on the model file with these preset flags. Returns {decode, prefill, noise} in tokens/s."""
    exe, d = bench_exe()
    cmd = [exe, "-m", path, "-p", str(BENCH_PROMPT), "-n", str(BENCH_GEN), "-r", str(repeats), "-o", "json"] + bench_args(flags)
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = d + (os.pathsep + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    out, err = [], []
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=d)
    readers = [threading.Thread(target=lambda s, b: b.append(s.read()), args=(proc.stdout, out), daemon=True),
               threading.Thread(target=lambda s, b: b.append(s.read()), args=(proc.stderr, err), daemon=True)]
    for t in readers:
        t.start()
    t0 = time.time()
    try:
        while proc.poll() is None:
            if cancelled and cancelled():
                raise Cancelled()
            if time.time() - t0 > BENCH_TIMEOUT:
                raise RuntimeError("llama-bench took too long.")
            time.sleep(0.1)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    for t in readers:
        t.join(5)
    proc.stdout.close()
    proc.stderr.close()
    if proc.returncode != 0:
        tail = (err[0] if err else b"").decode("utf8", "replace").strip().splitlines()[-1:] or ["no message"]
        raise RuntimeError("llama-bench stopped: %s" % tail[0][:200])
    return parse_bench((out[0] if out else b"").decode("utf8", "replace"))


def score(m, base, use):
    """Weighted geometric gain over the baseline: chat leans on decode speed, coding on prefill as well."""
    w = 0.5 if use == "coding" else 0.3
    return (m["decode"] / base["decode"]) ** (1 - w) * (m["prefill"] / base["prefill"]) ** w - 1


def _free_gpus():
    """llama-bench needs the cards: unload whatever the router has loaded."""
    try:
        if llamacpp.running():
            for m in llamacpp.models():
                if m["status"] == "loaded":
                    llamacpp.unload(m["id"])
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- the run
def plan(model, backends, devices, cpus):
    """{trials, minutes, recipe}; recipe is "llamacpp" when the model's file is found. `backends` is unused (kept for the old signature)."""
    path = model_path(model)
    if not path:
        return {"trials": 0, "minutes": 0, "recipe": None}
    n = len(candidates(effective(model), devices, cpus, _is_moe(path)))
    return {"trials": n, "minutes": max(3, round((n + 4) * 0.5)), "recipe": "llamacpp"}


def run(model, opts, job, backends, devices, cpus, env=None):
    """Tune one model. Returns the result dict that is also saved. Raises RuntimeError / Cancelled."""
    use = "coding" if opts.get("use") == "coding" else "chat"
    path = model_path(model)
    if not path:
        raise RuntimeError("Shoggoth can't find the model file for %s." % model)
    eff, before = effective(model), saved(model)
    cands = candidates(eff, devices, cpus, _is_moe(path))
    total = len(cands) + 3
    done = [0]

    def step(text):
        done[0] += 1
        job.update(text=text, progress=min(0.99, done[0] / total))

    def trial(flags, label, repeats):
        if job.cancelled():
            raise Cancelled()
        job.update(text=label)
        return bench(path, flags, repeats, job.cancelled)

    _free_gpus()
    base = trial(eff, "Measuring the model as it is…", 5)
    step("Baseline: %.1f tokens/s, prefill %.0f tokens/s" % (base["decode"], base["prefill"]))
    thr = max(MIN_GAIN, 2 * base["noise"])
    kept, tried, measured = [], [], {}
    for c in cands:
        try:
            m = trial(apply_change(eff, c), "Trying %s…" % c["label"], 3)
        except Cancelled:
            raise
        except RuntimeError as e:
            tried.append({"label": c["label"], "error": str(e)[:200]})
            step("Skipped %s: llama.cpp wouldn't run it." % c["label"])
            continue
        g = score(m, base, use)
        measured[c["label"]] = m
        tried.append({"label": c["label"], "gain": round(g, 4), "decode": round(m["decode"], 1), "prefill": round(m["prefill"], 1)})
        if g > thr:
            kept.append((g, c))
        step("%s: %+.1f%%" % (c["label"], g * 100))
    best_change, best_gain, best_m = None, 0.0, base
    if kept:
        kept.sort(key=lambda x: -x[0])
        best_change, best_gain = kept[0][1], kept[0][0]
        best_m = measured[best_change["label"]]
        if len(kept) > 1:
            combo = merge_changes([c for _, c in kept])
            try:
                m = trial(apply_change(eff, combo), "Checking the best changes together…", 3)
                g = score(m, base, use)
                if g > best_gain:
                    best_change, best_gain, best_m = combo, g, m
            except Cancelled:
                raise
            except RuntimeError:
                pass
        if best_gain <= thr:
            best_change = None
    step("Done measuring.")
    res = {"engine": "llamacpp", "model": model, "when": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "use": use, "noise": round(base["noise"], 4),
           "baseline": {"decode": round(base["decode"], 1), "prefill": round(base["prefill"], 1)}, "tried": tried,
           "baseline_tok_s": round(base["decode"], 1), "decode_tok_s": round(best_m["decode"], 1) if best_change else round(base["decode"], 1),
           "outcome": "improved" if best_change else "default_confirmed", "gain_pct": round(best_gain * 100, 1) if best_change else 0.0,
           "best": {"decode": round(best_m["decode"], 1), "prefill": round(best_m["prefill"], 1)} if best_change else None,
           "change": best_change["label"] if best_change else "", "env": env or {}, "applied": None}
    if best_change:
        new = apply_change(eff, best_change)
        body = {k: new[k] for k in TUNED_KEYS if k in new and new[k] != eff.get(k)}
        res["flags"] = body                                                  # the llama-server preset keys now in presets.ini
        res["applied"] = body
        res["saved_before"] = {k: before.get(k) for k in body}              # None: the key was not in the model's own section
        _apply_to_presets(model, {k: str(v) for k, v in body.items()})
    r = results()
    r[model] = res
    _save_results(r)
    return res


def rollback(model):
    """Put the model's flags back as they were before the tune. Returns (ok, message)."""
    r = results()
    res = r.get(model)
    if not res or not res.get("applied"):
        return False, "There is no tuning to undo for that model."
    before = res.get("saved_before") or {}
    try:
        _apply_to_presets(model, {k: before.get(k) for k in res["applied"]})
    except (OSError, ValueError) as e:
        return False, "Shoggoth couldn't restore the settings: %s" % e
    res["applied"], res["rolled_back"] = None, time.strftime("%Y-%m-%dT%H:%M:%S%z")
    _save_results(dict(r, **{model: res}))
    return True, ""


def test_speed(model, job, repeats=3):
    """Just the baseline: measure decode and prefill speed with the model's flags as they are, and keep the answer in Shoggoth's own record
    for the model (not in the tuning results, which are only ever changes that were measured). Returns {decode, prefill, noise, when}."""
    import modelmeta
    path = model_path(model)
    if not path:
        raise RuntimeError("Shoggoth can't find the model file for %s." % model)
    if job.cancelled():
        raise Cancelled()
    _free_gpus()
    job.update(text="Measuring how fast it answers…")
    m = bench(path, effective(model), max(repeats, 2), job.cancelled)
    res = {"decode": round(m["decode"], 1), "prefill": round(m["prefill"], 1), "noise": round(m["noise"], 4), "when": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    modelmeta.update(model, speed=res)
    return res


def headroom_args(model):
    """The model's own preset flags as a llama-server argument string, plus `--fit on --fit-target <MiB>` so `gb` of every card stays free for
    the system; None when no headroom is set for the model. llama.cpp only fits layers itself when -ngl is not pinned: a pinned
    n-gpu-layers (say, a tuned one) stays as `-ngl` and wins over the fit. Used on load; a tune measures the model as it is."""
    import modelmeta
    gb = modelmeta.headroom_gb(model)
    if not gb:
        return None
    tokens = []
    for k, v in saved(model).items():
        if k in ("model", "mmproj") or v is None:
            continue
        tokens += ["--" + k, str(v)]
    tokens = set_flag(set_flag(tokens, ("--fit", "-fit"), "on"), ("--fit-target", "-fitt"), int(gb * 1024))
    return join_args(tokens)
