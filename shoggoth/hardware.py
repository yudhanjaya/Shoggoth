"""What hardware this computer has, which llama.cpp builds Shoggoth has for it, and how llama.cpp numbers the devices.
Device lists come from the llama.cpp binary itself (`llama-server --list-devices`)."""
import os
import re
import shutil

import llamacpp
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
    llama.cpp Shoggoth runs is the same user as Shoggoth; a system service's is not)."""
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
    """Ask llama.cpp how it numbers devices. NVIDIA cards are numbered in PCI order (as nvidia-smi does), the same order Shoggoth's
    llama.cpp runs with."""
    if not bin_path or not os.path.isfile(bin_path):
        return None
    env = dict(os.environ)
    env.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
    code, out = run([bin_path, "--list-devices"], 30, env=env)
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


def info(mode, version):
    """The devices of the llama.cpp build Shoggoth runs, each tied to the card whose live numbers describe it, and the device chosen."""
    s = store.load()
    cfg = s.get("llamacpp") or {}
    b = llamacpp.choose(s)
    bin_path = (b or {}).get("server")
    devs = cached(f"devs:{bin_path}", 600, lambda: list_devices(bin_path)) or []
    devs = [dict(d) for d in devs]
    bind_devices(devs, all_gpus())   # live telemetry for each llama.cpp device
    return {
        "device": cfg.get("device", ""),
        "device_supported": bool(b),
        "backend": {"current": cfg.get("backend") or "auto", "resolved": (b or {}).get("backend", "")},
        "llama_bin": bin_path,
        "llama_version": cached(f"ver:{bin_path}", 600, lambda: llama_version(bin_path)),
        "llama_tag": (b or {}).get("tag", ""),
        "devices": devs,
        "devices_assumed": False,
        "needs_backend": b is None,
        "mode": mode,
        "zest": version,
    }


def backends():
    """The llama.cpp builds, in the shape the Engines page and tuning read: [{recipe, name, selectable, default, current, backends:
    [{name, state, version, message}]}]. A build is installed (with its tag as the version) or can be downloaded."""
    s = store.load()
    cfg = s.get("llamacpp") or {}
    have = {}
    for b in llamacpp.builds():
        have.setdefault(b["backend"], b)
    bs = [{"name": k, "state": "installed" if k in have else "installable", "message": llamacpp.NAMES[k],
           "version": have[k]["tag"] if k in have else None, "release_url": None, "file": None} for k in llamacpp.ASSETS]
    return [{"recipe": "llamacpp", "name": "llama.cpp", "modality": "llm", "experimental": False, "order": 0, "selectable": True,
             "default": cached("llamacpp-rec", 300, llamacpp.recommended), "current": cfg.get("backend") or "auto", "backends": bs}]


# ---------------------------------------------------------------- how big this computer is
def _ram_gb():
    try:
        with open("/proc/meminfo") as f:
            return int(re.search(r"MemTotal:\s+(\d+)", f.read()).group(1)) / 1048576
    except (OSError, AttributeError, ValueError):
        return 0.0


def machine():
    """RAM, graphics memory and free disk, the way the fit rule counts them. Memory the GPU shares with the system counts once."""
    ram = _ram_gb()
    gpus = []
    for g in all_gpus():
        gb = (g.get("mem_total_mb") or 0) / 1024
        if gb:
            gpus.append({"name": g.get("name"), "gb": gb, "vendor": g.get("vendor")})
    shared = lambda g: g["vendor"] != "NVIDIA" and g["gb"] >= ram * 0.4          # an APU's "VRAM" is system memory
    vram = sum(g["gb"] for g in gpus if not shared(g))
    where = os.path.expanduser("~/.local/share")
    os.makedirs(where, exist_ok=True)
    return {"ram_gb": round(ram, 1), "vram_gb": round(vram, 1), "best_gpu_gb": round(max([g["gb"] for g in gpus if not shared(g)] or [0]), 1),
            "gpus": [g["name"] for g in gpus], "free_disk_gb": round(shutil.disk_usage(where).free / 1e9, 1)}
