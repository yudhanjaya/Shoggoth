"""What hardware and llama.cpp backends Lemonade can see, and how llama.cpp numbers the devices."""
import os
import re
import shutil

import engine
import lem
import store
from util import cached, run

# ---------------------------------------------------------------- GPUs
def nvidia_gpus():
    q = "index,uuid,name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw,power.limit,fan.speed"
    code, out = run(["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits"], 5)
    gpus = []
    if code != 0:
        return gpus

    def num(s):
        try:
            return float(s)
        except ValueError:
            return None

    for line in out.strip().splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) < 10:
            continue
        gpus.append({
            "nv_index": int(p[0]), "uuid": p[1], "name": p[2],
            "mem_used_mb": num(p[3]), "mem_total_mb": num(p[4]),
            "util": num(p[5]), "temp": num(p[6]),
            "power": num(p[7]), "power_limit": num(p[8]), "fan": num(p[9]),
            "procs": [],
        })
    # Which processes hold VRAM on each card (compute clients only; names, not full paths).
    code, out = run(["nvidia-smi", "--query-compute-apps=pid,process_name,gpu_uuid,used_memory",
                     "--format=csv,noheader,nounits"], 5)
    by_uuid = {g["uuid"]: g for g in gpus}
    for line in out.strip().splitlines() if code == 0 else []:
        p = [x.strip() for x in line.split(",")]
        if len(p) < 4 or p[2] not in by_uuid:
            continue
        by_uuid[p[2]]["procs"].append({"pid": int(p[0]) if p[0].isdigit() else None,
                                       "name": os.path.basename(p[1]) or p[1],
                                       "mem_mb": num(p[3]) or 0})
    return gpus


def list_devices(bin_path):
    """Ask llama.cpp how it numbers devices; this differs from nvidia-smi."""
    if not bin_path or not os.path.isfile(bin_path):
        return None
    code, out = run([bin_path, "--list-devices"], 30)
    devs = []
    for m in re.finditer(r"^\s*([A-Za-z]+\d+):\s*(.+?)\s*\((\d+) MiB", out, re.M):
        devs.append({"id": m.group(1).lower(), "name": m.group(2), "mib": int(m.group(3))})
    return devs


def llama_version(bin_path):
    if not bin_path or not os.path.isfile(bin_path):
        return None
    code, out = run([bin_path, "--version"], 15)
    m = re.search(r"version:\s*(.+)", out)
    return m.group(1).strip() if m else None


def find_llama_bin(cfg, backend, mode):
    """A llama-server binary Zest is allowed to run, so it can ask it about devices and options."""
    path = cfg.get(f"llamacpp.{backend}_bin", "")
    if path and path != "builtin" and os.path.isfile(path):
        return path
    if mode == "builtin":     # Lemonade keeps backends it installs under its data folder
        own = os.path.join(engine.data_dir(), "bin", "llamacpp", backend, "llama-server")
        if os.path.isfile(own):
            return own
    if cfg.get("llamacpp.prefer_system") in (True, "true"):
        w = shutil.which("llama-server")
        if w:
            return w
    return None


def info(mode, version):
    cfg = lem.flat_config()
    recipe = lem.system_info().get("recipes", {}).get("llamacpp", {})
    current = cfg.get("llamacpp.backend", "auto") or "auto"
    resolved = recipe.get("default_backend") if current == "auto" else current
    bin_path = find_llama_bin(cfg, resolved, mode) if resolved else None
    devs = cached(f"devs:{bin_path}", 600, lambda: list_devices(bin_path))
    assumed = False
    gpus = nvidia_gpus()
    if devs is None:
        # No binary we can ask (for example a backend that isn't installed yet). For CUDA, fall back to PCI
        # order and say so; other backends need a manual value.
        if mode == "builtin":
            devs = []           # the built-in Lemonade has no llama.cpp yet; a guess could point at the wrong card
        else:
            devs = [{"id": f"cuda{g['nv_index']}", "name": g["name"], "mib": int((g["mem_total_mb"] or 0))}
                    for g in gpus] if resolved == "cuda" else []
            assumed = bool(devs)
    used = set()
    for d in devs:  # bind each llama.cpp device to its nvidia-smi entry for live telemetry
        d["nv_index"] = None
        for g in gpus:
            if g["nv_index"] in used:
                continue
            if g["name"] == d["name"] or g["name"] in d["name"] or d["name"] in g["name"]:
                d["nv_index"] = g["nv_index"]
                used.add(g["nv_index"])
                break
    host, port = lem.target()
    return {
        "device": cfg.get("llamacpp.device", ""),
        "device_supported": "llamacpp.device" in cfg,
        "backend": {"current": current, "resolved": resolved},
        "llama_bin": bin_path,
        "llama_version": cached(f"ver:{bin_path}", 600, lambda: llama_version(bin_path)),
        "llama_args": cfg.get("llamacpp.args", ""),
        "devices": devs,
        "devices_assumed": assumed,
        "needs_backend": mode == "builtin" and bin_path is None,
        "lemonade": f"{host}:{port}",
        "mode": mode,
        "zest": version,
    }


def backends():
    cfg = lem.flat_config()
    out = []
    for key, r in lem.system_info().get("recipes", {}).items():
        bs = [{"name": n, "state": b.get("state"), "message": b.get("message"),
               "version": b.get("version"), "release_url": b.get("release_url"),
               "file": b.get("download_filename")}
              for n, b in r.get("backends", {}).items() if b.get("state") != "unsupported"]
        if not bs:
            continue
        prefix = key.replace("-", "")
        out.append({
            "recipe": key, "name": r.get("display_name", key), "modality": r.get("modality", ""),
            "experimental": bool(r.get("experimental")), "order": r.get("order", 99),
            "selectable": bool(r.get("selectable_backend")) and f"{prefix}.backend" in cfg,
            "default": r.get("default_backend"), "current": cfg.get(f"{prefix}.backend") or "auto",
            "backends": bs,
        })
    out.sort(key=lambda r: r["order"])
    return out
