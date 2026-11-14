"""Compatibility shim: what gateway.py still asks of "Lemonade", answered by Shoggoth's own llama.cpp router (llamacpp.py).

Lemonade is gone. gateway._route still calls target(), key() and request() for the default engine; those now point at the router,
so a chat to the default engine reaches llama.cpp. Remove this file once gateway.py routes every model through the registry.
"""
import llamacpp


def target():
    return "127.0.0.1", (llamacpp._state.get("port") if llamacpp.running() else 0) or 0


def key():
    return llamacpp._state.get("key") or ""


def _path(path):
    """Lemonade's /api/v1/x is the router's /v1/x; its /v1/health is the router's /health."""
    if path.startswith("/api/"):
        path = path[4:]
    return "/health" if path.split("?")[0] == "/v1/health" else path


def request(method, path, body=None, timeout=10, **_):
    """(status, parsed JSON) from the router; status 0 when it isn't running."""
    if not llamacpp.running():
        return 0, {"error": "llama.cpp isn't running"}
    return llamacpp.request(method, _path(path), body, timeout=timeout)


def get(path, timeout=8):
    status, payload = request("GET", path, timeout=timeout)
    return payload if status == 200 else {}


def healthy(*_a, **_k):
    return llamacpp.running()


def error_text(payload, fallback):
    err = payload.get("error", "") if isinstance(payload, dict) else ""
    if isinstance(err, dict):
        err = err.get("message", "")
    return str(err) or fallback
