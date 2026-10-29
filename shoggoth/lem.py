"""Talks to a Lemonade Server over its HTTP API (the same one the `lemonade` CLI uses)."""
import http.client
import json

_t = {"host": "127.0.0.1", "port": 13305, "key": ""}


def configure(host, port, key=""):
    _t.update(host=host, port=int(port), key=key or "")


def target():
    return _t["host"], _t["port"]


def key():
    return _t["key"]


def request(method, path, body=None, timeout=10, host=None, port=None, api_key=None):
    """Returns (status, parsed JSON). status 0 means the server couldn't be reached."""
    h = host or _t["host"]
    p = port or _t["port"]
    k = _t["key"] if api_key is None else api_key
    headers = {"Authorization": "Bearer " + k} if k else {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    conn = http.client.HTTPConnection(h, p, timeout=timeout)
    try:
        conn.request(method, path, body=data, headers=headers)
        r = conn.getresponse()
        raw = r.read()
    except (OSError, http.client.HTTPException) as e:          # HTTPException: a reply cut off or garbled (say, mid-load)
        return 0, {"error": str(e) or e.__class__.__name__}
    finally:
        conn.close()
    try:
        return r.status, json.loads(raw or b"{}")
    except ValueError:
        return r.status, {"raw": raw[:300].decode("utf8", "replace")}


def get(path, timeout=8):
    status, payload = request("GET", path, timeout=timeout)
    return payload if status == 200 else {}


def healthy(host=None, port=None, api_key=None):
    status, _ = request("GET", "/v1/health", timeout=2, host=host, port=port, api_key=api_key)
    return status == 200


def error_text(payload, fallback):
    err = payload.get("error", "") if isinstance(payload, dict) else ""
    if isinstance(err, dict):
        err = err.get("message", "")
    return str(err) or fallback


def flat_config():
    """The server's config.json as dotted keys, e.g. 'llamacpp.device'."""
    out = {}

    def walk(prefix, d):
        for k, v in d.items():
            if isinstance(v, dict):
                walk(prefix + k + ".", v)
            else:
                out[prefix + k] = "" if v is None else v
    walk("", get("/internal/config"))
    return out


def set_config(nested):
    """nested like {"llamacpp": {"device": "cuda1"}} or {"models_dir": "auto"}. Returns (ok, message)."""
    status, payload = request("POST", "/internal/set", nested, timeout=15)
    if status == 200:
        return True, ""
    return False, error_text(payload, "Lemonade rejected the change")


def system_info():
    return get("/v1/system-info")


def install_backend(recipe, backend):
    status, payload = request("POST", "/v1/install", {"recipe": recipe, "backend": backend}, timeout=3600)
    if status == 200:
        return True, ""
    return False, error_text(payload, "install failed")
