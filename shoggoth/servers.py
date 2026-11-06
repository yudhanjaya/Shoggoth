"""Servers the person runs themselves and has connected under Advanced > Engines (Ollama, LM Studio, llama.cpp, Strata, Maya, Unsloth, any
OpenAI-style server). Shoggoth uses them and never starts, stops, updates or signals them. Lemonade is the only engine Shoggoth manages.

A saved server is {id, preset, name, url, key}. The registry turns each one into a ConnectedServer, an engine that is only an address.
Standard library only (YAML is read by the copy in _vendor)."""
import os
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "_vendor"))
import yaml  # noqa: E402

import provbase  # noqa: E402
import store  # noqa: E402

PRESET_FILE = os.path.join(HERE, "recommendations", "servers.yaml")
RESERVED = {"lemonade"}
_LOCAL = {"localhost", "::1"}


def presets():
    with open(PRESET_FILE) as f:
        return [dict(p) for p in yaml.safe_load(f)]


def normalise(url):
    """scheme://host[:port][/path] with no trailing slash and no /v1. ValueError, in plain words, for anything that is not a web address."""
    u = urllib.parse.urlsplit(str(url or "").strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("Type an address that starts with http:// or https://, for example http://127.0.0.1:11434.")
    if u.username or u.password:
        raise ValueError("Leave the user name and password out of the address. Put the key in the key box.")
    try:
        port = u.port
    except ValueError:
        raise ValueError("That port isn't a number between 1 and 65535.")
    host = u.hostname.lower()
    host = "[%s]" % host if ":" in host else host
    path = u.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    return "%s://%s%s%s" % (u.scheme, host, ":%d" % port if port else "", path)


def _canon(url):
    """The address with every way of saying 'this computer' made the same, so two spellings are not two servers."""
    u = urllib.parse.urlsplit(url)
    host = u.hostname
    if host in _LOCAL:
        host = "127.0.0.1"
    host = "[%s]" % host if ":" in host else host
    return "%s://%s:%s%s" % (u.scheme, host, u.port or (443 if u.scheme == "https" else 80), u.path)


def all():
    return [dict(s) for s in (store.load().get("servers") or []) if isinstance(s, dict) and s.get("id") and s.get("url")]


def public():
    """What the page may see: the key is replaced by its last four characters."""
    out = []
    for s in all():
        k = str(s.get("key") or "")
        out.append({"id": s["id"], "preset": s.get("preset", ""), "name": s.get("name") or s["id"], "url": s["url"],
                    "has_key": bool(k), "key_hint": k[-4:] if len(k) > 8 else ""})
    return out


def get(sid):
    return next((s for s in all() if s["id"] == sid), None)


def add(preset, url, key="", name=""):
    """Save a server. ValueError, in plain words, when the address is bad or already connected."""
    pre = next((p for p in presets() if p["id"] == preset), None)
    if not pre:
        raise ValueError("That isn't one of the servers Shoggoth knows by name. Choose 'Another server'.")
    url = normalise(url or pre.get("url"))
    taken = {s["id"] for s in all()} | RESERVED
    n, sid = 1, pre["id"]
    while sid in taken:
        n += 1
        sid = "%s-%d" % (pre["id"], n)
    entry = {"id": sid, "preset": pre["id"], "name": (name or "").strip()[:60] or pre["name"] + (" %d" % n if n > 1 else ""),
             "url": url, "key": str(key or "").strip()}

    def put(s):
        have = s.setdefault("servers", [])
        for h in have:
            if isinstance(h, dict) and h.get("url") and _canon(normalise(h["url"])) == _canon(url):
                raise ValueError("%s is already connected at that address." % (h.get("name") or h["id"]))
        have.append(entry)
    store.update(put)
    return dict(entry)


def remove(sid):
    def put(s):
        s["servers"] = [h for h in (s.get("servers") or []) if not (isinstance(h, dict) and h.get("id") == sid)]
    store.update(put)


def probe(url, key=""):
    """Ask a server what it has. Never raises, never takes longer than a couple of seconds. {reachable, needs_key, models}."""
    try:
        base = normalise(url)
    except ValueError:
        return {"reachable": False, "needs_key": False, "models": []}
    status, d = provbase.http_json(base + "/v1/models", key, timeout=1.5)
    models = provbase.jpath(d, "$.data[*].id") if status == 200 else []
    return {"reachable": status != 0, "needs_key": status in (401, 403), "models": [str(m) for m in models]}


class ConnectedServer(provbase.GenericProvider):
    """An engine that is only an address. It lists what the server has, hands out its address, and never starts or stops anything."""

    def __init__(self, entry, ctx):
        spec = {"id": entry["id"], "name": entry.get("name") or entry["id"], "api_version": 1, "tier": "", "run": {"mode": "external"},
                "endpoint": {"base_url": entry["url"] + "/v1"}, "memory": {"group": ""}}
        super().__init__(spec, "", ctx)
        self._seen = (0.0, [], 0)

    def _entry(self):
        return get(self.id) or {"url": self.spec["endpoint"]["base_url"][:-3], "key": ""}

    def _key(self):
        return self._entry().get("key", "")

    def _models(self):
        t, v, _ = self._seen
        if time.time() - t < 5:
            return v
        e = self._entry()
        status, d = provbase.http_json(e["url"] + "/v1/models", e.get("key", ""), timeout=1.5)
        out = []
        if status == 200:
            for m in (d.get("data") or []):
                if isinstance(m, dict) and m.get("id"):
                    ctx = ((m.get("meta") or {}).get("n_ctx") if isinstance(m.get("meta"), dict) else 0) or 0
                    out.append({"id": str(m["id"]), "ctx": int(ctx) if str(ctx).isdigit() else 0, "port": 0, "owner": "yours"})
        self._seen = (time.time(), out, status)
        return out

    def detect(self):
        return {"found": True, "bin": None, "running": [{"port": 0, "owner": "yours"}] if self._models() else [], "external": self._entry()["url"]}

    def status(self):
        det = self.detect()
        return dict(self.summary(), installed=True, running=det["running"], bin=None, problems=[], url=self._entry()["url"], connected=True,
                    needs_key=self._seen[2] in (401, 403), models=[m["id"] for m in self._models()])

    def models(self):
        return self._models()

    def endpoint(self, model_id=None):
        e = self._entry()
        for m in self._models():
            if model_id is None or m["id"] == model_id:
                return {"base_url": e["url"] + "/v1", "key": e.get("key", ""), "model": m["id"], "ctx": m["ctx"], "owner": "yours"}
        return None

    def ensure(self, model_id, progress, opts=None):
        self._seen = (0.0, [], 0)
        ep = self.endpoint(model_id)
        if not ep:
            raise provbase.ProviderError("%s isn't answering, or doesn't list %s. Shoggoth doesn't start it; start it yourself and try again." % (self.name, model_id))
        return ep

    def owned_instance(self):
        return None

    def stop(self, model_id=None):
        return False, "Shoggoth didn't start %s, so it leaves it running." % self.name

    def selftest(self):
        r = probe(self._entry()["url"], self._key())
        return [{"name": "its model list", "ok": r["reachable"] and not r["needs_key"], "detail": "needs a key" if r["needs_key"] else "" if r["reachable"] else "not answering"}]
