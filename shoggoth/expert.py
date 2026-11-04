"""Expert Mode: one switch under Advanced that gates the engine-control features (authority, GPU assignment, routing flows).
Off means Shoggoth behaves as it always has. Every gate asks `on()`; nothing else reads the setting."""
import store


def on(settings=None):
    return bool((settings or store.load()).get("expert", {}).get("on"))


def set_on(value):
    """Turn it on or off. Saved flows and engine settings are kept either way; off only stops them applying."""
    value = bool(value)

    def put(s):
        s.setdefault("expert", {})["on"] = value
    store.update(put)
    return value
