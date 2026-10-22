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
            "nv_index": int(p[0]), "uuid": p[1], "name": p[2], "key": p[1], "vendor": "NVIDIA",
            "ref": f"GPU {int(p[0])} in nvidia-smi",
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


_VENDORS = {"0x10de": "NVIDIA", "0x1002": "AMD", "0x8086": "Intel"}


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def _pci_name(slot, vendor, dev_id):
    """A readable name for a PCI graphics device, from lspci when it is installed."""
    code, out = run(["lspci", "-mm", "-s", slot], 5) if shutil.which("lspci") else (1, "")
    if code == 0:
        f = re.findall(r'"([^"]*)"', out)
        if len(f) >= 3:
            name = f[2]
            inner = re.findall(r"\[([^\]]+)\]", name)
            core = (inner[-1] if inner else name).split("/")[0].strip()
            if core:
                return core if core.lower().startswith(vendor.lower()) else f"{vendor} {core}"
    return f"{vendor} graphics ({dev_id})"


def detect_gpus():
    """Graphics cards in this computer, found without llama.cpp, so the welcome steps can say what is here before
    any backend is installed. Names only: live usage still comes from nvidia-smi."""
    tool = {"NVIDIA": [g["name"] for g in nvidia_gpus()], "AMD": [], "Intel": []}
    try:
        devices = lem.system_info().get("devices", {})
    except Exception:  # noqa: BLE001
        devices = {}
    for key, vendor in (("nvidia_gpu", "NVIDIA"), ("amd_gpu", "AMD"), ("intel_gpu", "Intel")):
        names = [d["name"] for d in devices.get(key) or [] if isinstance(d, dict) and d.get("name") and d.get("available", True)]
        if len(names) > len(tool[vendor]):
            tool[vendor] = names
    pci = {"NVIDIA": [], "AMD": [], "Intel": []}
    base = "/sys/class/drm"
    try:
        cards = sorted(c for c in os.listdir(base) if re.fullmatch(r"card\d+", c))
    except OSError:
        cards = []
    for c in cards:
        dev = os.path.join(base, c, "device")
        vendor = _VENDORS.get(_read(os.path.join(dev, "vendor")).lower())
        if not vendor or not _read(os.path.join(dev, "class")).startswith("0x03"):
            continue
        slot = os.path.basename(os.path.realpath(dev))
        dev_id = f"{_read(os.path.join(dev, 'vendor'))[2:]}:{_read(os.path.join(dev, 'device'))[2:]}"
        pci[vendor].append(_pci_name(slot, vendor, dev_id))
    out = []
    for vendor in ("NVIDIA", "AMD", "Intel"):
        names = tool[vendor] if len(tool[vendor]) >= len(pci[vendor]) else pci[vendor]
        out += [{"vendor": vendor, "name": n} for n in names]
    return out



# ---------------------------------------------------------------- AMD GPUs (the Linux amdgpu driver exposes live numbers in sysfs)
DRM = "/sys/class/drm"


def _num(path, scale=1.0):
    t = _read(path)
    try:
        return float(t) / scale
    except ValueError:
        return None


def _first(paths):
    for p in paths:
        v = _num(p)
        if v is not None:
            return v
    return None


def _hwmon(dev):
    base = os.path.join(dev, "hwmon")
    try:
        return [os.path.join(base, d) for d in sorted(os.listdir(base))]
    except OSError:
        return []


def amd_gpus(base=DRM):
    """AMD cards with live memory, load, temperature and power, read straight from the amdgpu driver."""
    gpus = []
    try:
        cards = sorted(c for c in os.listdir(base) if re.fullmatch(r"card\d+", c))
    except OSError:
        return gpus
    for c in cards:
        dev = os.path.join(base, c, "device")
        if _read(os.path.join(dev, "vendor")).lower() != "0x1002" or not _read(os.path.join(dev, "class")).startswith("0x03"):
            continue
        total = _num(os.path.join(dev, "mem_info_vram_total"), 1048576)
        if total is None:           # not the amdgpu driver (or no VRAM figure): nothing live to show
            continue
        slot = os.path.basename(os.path.realpath(dev))
        dev_id = f"{_read(os.path.join(dev, 'vendor'))[2:]}:{_read(os.path.join(dev, 'device'))[2:]}"
        hw = _hwmon(dev)
        temp = _first(os.path.join(h, "temp1_input") for h in hw)
        power = _first(os.path.join(h, n) for h in hw for n in ("power1_average", "power1_input"))
        cap = _first(os.path.join(h, "power1_cap") for h in hw)
        fan = _first(os.path.join(h, "fan1_input") for h in hw)
        fan_max = _first(os.path.join(h, "fan1_max") for h in hw)
        pwm = _first(os.path.join(h, "pwm1") for h in hw)
        fan_pct = round(fan / fan_max * 100) if fan is not None and fan_max else (round(pwm / 255 * 100) if pwm is not None else None)
        gpus.append({
            "key": slot, "vendor": "AMD", "nv_index": None, "uuid": slot, "slot": slot, "drm": c,
            "name": cached(f"pci:{slot}", 3600, lambda: _pci_name(slot, "AMD", dev_id)),
            "ref": f"{c} ({slot}) in the amdgpu driver",
            "mem_total_mb": total, "mem_used_mb": _num(os.path.join(dev, "mem_info_vram_used"), 1048576),
            "util": _num(os.path.join(dev, "gpu_busy_percent")),
            "temp": round(temp / 1000) if temp is not None else None,
            "power": round(power / 1e6, 1) if power is not None else None,
            "power_limit": round(cap / 1e6) if cap else None,
            "fan": fan_pct, "procs": [],
        })
    gpus.sort(key=lambda g: g["slot"])
    by_slot = {g["slot"]: g for g in gpus}
    if by_slot:
        for p in drm_clients(by_slot):
            by_slot[p["slot"]]["procs"].append({"pid": p["pid"], "name": p["name"], "mem_mb": p["mem_mb"]})
    return gpus


def parse_fdinfo(text):
    """One open file of a process: which AMD card it is on and how much VRAM that client holds (None if not a DRM file)."""
    kv = dict(l.split(":", 1) for l in text.splitlines() if ":" in l)
    if kv.get("drm-driver", "").strip() != "amdgpu" or "drm-pdev" not in kv:
        return None
    mem = 0.0
    m = re.match(r"\s*(\d+)\s*(\w+)?", kv.get("drm-memory-vram", "0"))
    if m:
        mem = int(m.group(1)) / {"KiB": 1024, "MiB": 1, "GiB": 1/1024, "B": 1048576, None: 1048576}.get(m.group(2), 1024)
    return {"slot": kv["drm-pdev"].strip(), "client": kv.get("drm-client-id", "").strip(), "mem_mb": mem}


def drm_clients(by_slot, proc="/proc"):
    """Which of our processes hold VRAM on which AMD card. Only processes we are allowed to inspect appear (the
    built-in Lemonade runs as the same user as Zest; a system service's does not)."""
    def scan():
        out = []
        try:
            pids = [p for p in os.listdir(proc) if p.isdigit()]
        except OSError:
            return out
        for pid in pids:
            fdinfo = os.path.join(proc, pid, "fdinfo")
            try:
                fds = os.listdir(fdinfo)
            except OSError:
                continue
            seen = {}
            for fd in fds:
                try:
                    with open(os.path.join(fdinfo, fd)) as f:
                        text = f.read(4096)
                except OSError:
                    continue
                if "drm-driver" not in text:
                    continue
                d = parse_fdinfo(text)
                if d and d["slot"] in by_slot:
                    seen[(d["slot"], d["client"] or fd)] = d["mem_mb"]
            per = {}
            for (slot, _), mem in seen.items():
                per[slot] = per.get(slot, 0) + mem
            if per:
                name = _read(os.path.join(proc, pid, "comm")) or pid
                for slot, mem in per.items():
                    if mem > 0:
                        out.append({"slot": slot, "pid": int(pid), "name": name, "mem_mb": round(mem)})
        return out
    return cached("drm-clients", 4, scan)


def all_gpus():
    return nvidia_gpus() + amd_gpus()


def _norm(n):
    n = re.sub(r"\(.*?\)", " ", str(n).lower())
    n = re.sub(r"\b(nvidia|amd|advanced micro devices|ati|geforce|radeon|graphics|series|corporation|inc)\b", " ", n)
    return re.sub(r"[^a-z0-9]+", " ", n).strip()


def _vendor_of(name):
    n = str(name).lower()
    if re.search(r"nvidia|geforce|rtx|gtx|quadro|tesla", n):
        return "NVIDIA"
    if re.search(r"amd|radeon|\bradv\b|vega|\brx\b|instinct|\bati\b", n):
        return "AMD"
    if re.search(r"intel|\barc\b|iris", n):
        return "Intel"
    return None


def bind_devices(devs, gpus):
    """Tie each llama.cpp device to the card whose live numbers describe it. Same name first (closest memory breaks
    ties between two cards of one model); if names don't line up, fall back to the same maker and closest memory."""
    used = set()

    def take(d, g):
        used.add(g["key"])
        d["gpu_key"], d["nv_index"], d["gpu_ref"] = g["key"], g["nv_index"], g.get("ref", "")

    for d in devs:
        d["gpu_key"] = None
        d["nv_index"] = None
    for d in devs:
        dn, best, score = _norm(d["name"]), None, None
        for g in gpus:
            gn = _norm(g["name"])
            if g["key"] in used or not (dn and gn and (dn == gn or dn in gn or gn in dn)):
                continue
            gap = abs((g["mem_total_mb"] or 0) - d["mib"])
            if best is None or gap < score:
                best, score = g, gap
        if best is not None:
            take(d, best)
    for d in devs:
        if d["gpu_key"]:
            continue
        v, best, score = _vendor_of(d["name"]), None, None
        for g in gpus:
            if g["key"] in used or not v or g["vendor"] != v:
                continue
            gap = abs((g["mem_total_mb"] or 0) - d["mib"])
            if best is None or gap < score:
                best, score = g, gap
        if best is not None:
            take(d, best)
    return devs


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
    gpus = all_gpus()
    if devs is None:
        # No binary we can ask (for example a backend that isn't installed yet). For CUDA, fall back to PCI
        # order and say so; other backends need a manual value.
        if mode == "builtin":
            devs = []           # the built-in Lemonade has no llama.cpp yet; a guess could point at the wrong card
        else:
            devs = [{"id": f"cuda{g['nv_index']}", "name": g["name"], "mib": int((g["mem_total_mb"] or 0))}
                    for g in gpus if g["vendor"] == "NVIDIA"] if resolved == "cuda" else []
            assumed = bool(devs)
    bind_devices(devs, gpus)   # live telemetry for each llama.cpp device
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
