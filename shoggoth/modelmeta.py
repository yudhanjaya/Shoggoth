"""Shoggoth's own notes about a model, kept per model id: a measured speed, and how much graphics memory to leave free for the system.

Tuning flags are not kept here. Those stay where the engine keeps them (Strata's presets, Lemonade's saved options with the result in
tuning.json), so nothing is stored twice and the engine remains the source of truth.
"""
import json
import os
import threading

import store

_lock = threading.Lock()
KEYS = ("speed", "headroom_gb", "note")


def _path():
    return os.path.join(store.data_dir(), "model-meta.json")


def _all():
    try:
        with open(_path()) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def get(model):
    return dict(_all().get(model) or {})


def update(model, **fields):
    """Merge `fields` (only the known keys) into the model's record; a value of None removes that key. Returns the record."""
    bad = [k for k in fields if k not in KEYS]
    if bad:
        raise ValueError("unknown field: %s" % ", ".join(bad))
    with _lock:
        d = _all()
        rec = dict(d.get(model) or {})
        for k, v in fields.items():
            if v is None:
                rec.pop(k, None)
            else:
                rec[k] = v
        d[model] = rec
        os.makedirs(os.path.dirname(_path()), exist_ok=True)
        tmp = _path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, _path())
        return rec


DEFAULT_HEADROOM_GB = {"strata": 1.5}            # on unless the person turns it off: a big model that takes every last MiB makes the desktop crawl


def headroom_gb(model):
    """The graphics memory to leave free for the system on load: what the person set for the model (0 is "none"), else the engine's default."""
    v = get(model).get("headroom_gb")
    if v is None:
        return DEFAULT_HEADROOM_GB.get("strata" if str(model).startswith("strata/") else "lemonade", 0.0)
    try:
        return max(0.0, float(v))
    except (TypeError, ValueError):
        return 0.0


def headroom_info(model):
    """For the page: {gb, default, explicit}."""
    d = DEFAULT_HEADROOM_GB.get("strata" if str(model).startswith("strata/") else "lemonade", 0.0)
    return {"gb": headroom_gb(model), "default": d, "explicit": get(model).get("headroom_gb") is not None}


def headroom_table():
    """Everything the page needs to show the setting: the engines' defaults, and what the person set for particular models."""
    return {"default": dict(DEFAULT_HEADROOM_GB, lemonade=0.0), "set": {m: r["headroom_gb"] for m, r in _all().items() if isinstance(r, dict) and r.get("headroom_gb") is not None}}
