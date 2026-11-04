"""Which graphics cards each engine may use (Expert Mode). The choice is "all" (the default), "none" (processor, RAM and disk only) or a
list of cards by UUID. It is applied when an engine is started, as CUDA_VISIBLE_DEVICES, and a UUID means the same card whatever order
the tools number them in. NVIDIA only so far: AMD and Intel cards are listed but not assignable.

With Expert Mode off every engine reads as "all", so nothing changes for anyone who has not turned it on."""
import expert
import store

ALL, NONE = "all", "none"


def _gpus():
    import hardware                       # here, not at the top: hardware imports engine, and engine starts engines with these settings
    return hardware.nvidia_gpus()


def choice(pid, settings=None):
    s = settings or store.load()
    if not expert.on(s):
        return ALL
    v = (s.get("devices") or {}).get(pid)
    return v if v in (ALL, NONE) or (isinstance(v, list) and v and all(isinstance(x, str) for x in v)) else ALL


def set_(pid, value):
    """Save the choice for one engine; it applies the next time the engine starts."""
    if isinstance(value, list):
        value = [str(x) for x in value]
    if not (value in (ALL, NONE) or (isinstance(value, list) and value)):
        raise ValueError("Choose all cards, none, or at least one card.")
    known = {g["uuid"] for g in _gpus()}
    if isinstance(value, list) and not set(value) <= known:
        raise ValueError("That card isn't one Shoggoth can see.")

    def put(s):
        s.setdefault("devices", {})[str(pid)] = value
    store.update(put)
    return value


def env(pid):
    """The environment an engine is started with: nothing for "all", the chosen cards otherwise."""
    c = choice(pid)
    if c == ALL:
        return {}
    return {"CUDA_DEVICE_ORDER": "PCI_BUS_ID", "CUDA_VISIBLE_DEVICES": "" if c == NONE else ",".join(c)}


def visible(pid, gpus=None):
    """The card UUIDs this engine will see."""
    gpus = _gpus() if gpus is None else gpus
    allk = [g["uuid"] for g in gpus]
    c = choice(pid)
    return set(allk) if c == ALL else set() if c == NONE else {u for u in c if u in allk}


def disjoint(a, b):
    """True when both engines have their own cards chosen and share none, so neither needs the other's memory freed."""
    if choice(a) == ALL or choice(b) == ALL:
        return False
    return not (visible(a) & visible(b))


def state(pids):
    return {"gpus": [{"uuid": g["uuid"], "name": g["name"], "gb": round((g["mem_total_mb"] or 0) / 1024), "index": g["nv_index"]}
                     for g in _gpus()],
            "engines": [{"id": p, "devices": choice(p)} for p in pids]}
