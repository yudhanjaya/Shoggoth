"""Servers the person runs themselves and has connected under Advanced > Engines (Ollama, LM Studio, llama.cpp, Strata, Maya, Unsloth, any
OpenAI-style server). Shoggoth uses them and never starts, stops, updates or signals them. llama.cpp is the only engine Shoggoth manages.

A saved server is {id, preset, name, url, key}. The registry turns each one into a ConnectedServer, an engine that is only an address.
Standard library only."""
import ipaddress
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import engines
import provbase
import store

RESERVED = {"llamacpp"}          # the id of Shoggoth's own engine
_LOCAL = {"localhost", "::1"}


def presets():
    """The engines Shoggoth knows by name: the definitions in engines.d (see engines.py)."""
    return engines.presets()


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
                    "has_key": bool(k), "key_hint": k[-4:] if len(k) > 8 else "", "api": s.get("api", ""),
                    "ctx": s.get("ctx") or 0, "local": s.get("local")})
    return out


def get(sid):
    return next((s for s in all() if s["id"] == sid), None)


def _append(s, pre, url, key, name, extra=None):
    """Add a server to the settings `s` (not saved here). ValueError when the address is already connected."""
    have = s.setdefault("servers", [])
    for h in have:
        if isinstance(h, dict) and h.get("url") and _canon(normalise(h["url"])) == _canon(url):
            raise ValueError("%s is already connected at that address." % (h.get("name") or h["id"]))
    taken = {h["id"] for h in have if isinstance(h, dict) and h.get("id")} | RESERVED
    n, sid = 1, pre["id"]
    while sid in taken:
        n += 1
        sid = "%s-%d" % (pre["id"], n)
    entry = {"id": sid, "preset": pre["id"], "name": (name or "").strip()[:60] or pre["name"] + (" %d" % n if n > 1 else ""),
             "url": url, "key": str(key or "").strip()}
    for k, v in (extra or {}).items():
        if v not in (None, "", {}):
            entry[k] = v
    have.append(entry)
    return entry


def add(preset, url, key="", name="", extra=None):
    """Save a server. ValueError, in plain words, when the address is bad or already connected. `extra` carries what a hand-made
    connection needs beyond an address: `api` (the wire format), `ctx` (the context size to assume) and `local`."""
    pre = next((p for p in presets() if p["id"] == preset), None)
    if not pre:
        raise ValueError("That isn't one of the servers Shoggoth knows by name. Choose 'Another server'.")
    url, out = normalise(url or pre.get("url")), []
    store.update(lambda s: out.append(_append(s, pre, url, key, name, extra)))
    return dict(out[0])


def migrate_strata():
    """Earlier versions kept the Strata servers a person typed in (and their keys) in the `strata` settings. Carry them over as connected
    servers, once, so a Strata they use keeps working (the first is still called `strata`, so chats that name strata/<model> still open).
    The old settings are left where they are. Returns how many servers were added."""
    added = []

    def put(s):
        done = s.setdefault("migrated", [])
        if "strata" in done:
            return
        old = s.get("strata") if isinstance(s.get("strata"), dict) else {}
        keys = old.get("keys") if isinstance(old.get("keys"), dict) else {}
        pre = next(p for p in presets() if p["id"] == "strata")
        for r in old.get("remote") if isinstance(old.get("remote"), list) else []:
            raw = str((r or {}).get("url", "")).strip() if isinstance(r, dict) else ""
            try:
                url = normalise(raw)
                added.append(_append(s, pre, url, keys.get(raw) or keys.get(raw.rstrip("/")) or keys.get(url) or ""  , ""))
            except ValueError:
                continue
        done.append("strata")
    store.update(put)
    return len(added)


def _forget_caches():
    """After the server list changes, let every cached model list go so a removed server's models disappear at once."""
    try:
        import registry
        registry.reload()
    except Exception:  # noqa: BLE001
        pass
    try:
        import router
        router.invalidate()
    except Exception:  # noqa: BLE001
        pass
    try:
        import library
        library._pk["v"] = None
    except Exception:  # noqa: BLE001
        pass
    try:
        import agent
        agent.sync_models()
    except Exception:  # noqa: BLE001
        pass


def remove(sid):
    def put(s):
        s["servers"] = [h for h in (s.get("servers") or []) if not (isinstance(h, dict) and h.get("id") == sid)]
    store.update(put)
    _forget_caches()


GEMINI_HOST = "generativelanguage.googleapis.com"


def is_gemini(url):
    return (urllib.parse.urlsplit(str(url or "")).hostname or "").lower() == GEMINI_HOST


def api_base(url):
    """The OpenAI-style base for a saved address: <url>/v1, except Gemini, whose OpenAI-style door is .../v1beta/openai."""
    if is_gemini(url):
        return "https://%s/v1beta/openai" % GEMINI_HOST
    return url + "/v1"


def _gemini_models(key, timeout=1.5):
    """(status, [{id, ctx}]) from Gemini's own list (GET /v1beta/models), the chat models only (engines.gemini_chat_models)."""
    req = urllib.request.Request("https://%s/v1beta/models?pageSize=1000" % GEMINI_HOST, headers={"x-goog-api-key": key} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read() or b"{}")
            status = r.status
    except urllib.error.HTTPError as e:
        return (401 if e.code in (400, 401, 403) and not key else e.code), []
    except (OSError, ValueError):
        return 0, []
    ctx = {str(m.get("name", ""))[7:]: int(m.get("inputTokenLimit") or 0) for m in (d.get("models") or []) if isinstance(m, dict)}
    return status, [{"id": n, "ctx": ctx.get(n, 0)} for n in engines.gemini_chat_models(d)]


def list_models(url, key="", pin=None, timeout=1.5, api=""):
    """(status, [{id, ctx}]) from a saved address. status 0 means it didn't answer. `api` is the wire format its models speak
    (see engines.APIS plus "ollama-native"); empty guesses from the address. Every format Shoggoth speaks lists its models at
    `<address>/models`; Gemini has its own listing, and Ollama's native one is /api/tags."""
    if api == "google-generative-ai" or (not api and is_gemini(url)):
        return _gemini_models(key, timeout)
    if api == "ollama-native":
        status, d = provbase.http_json(url + "/api/tags", key, timeout=timeout, pin=pin)
        out = [{"id": str(m["name"]), "ctx": 0} for m in (d.get("models") or [])
               if isinstance(m, dict) and m.get("name")] if status == 200 else []
        return status, out
    status, d = provbase.http_json(api_base(url) + "/models", key, timeout=timeout, pin=pin)
    out = []
    if status == 200:
        for m in ((d or {}).get("data") or []):
            if isinstance(m, dict) and m.get("id"):
                ctx = ((m.get("meta") or {}).get("n_ctx") if isinstance(m.get("meta"), dict) else 0) or 0
                out.append({"id": str(m["id"]), "ctx": int(ctx) if str(ctx).isdigit() else 0})
    return status, out


def probe(url, key="", pin=None, api=""):
    """Ask a server what it has. Never raises, never takes longer than a couple of seconds. {reachable, needs_key, models}."""
    try:
        base = normalise(url)
    except ValueError:
        return {"reachable": False, "needs_key": False, "models": []}
    status, ms = list_models(base, key, pin, api=api)
    return {"reachable": status != 0, "needs_key": status in (401, 403), "models": [m["id"] for m in ms] if status == 200 else []}


def is_local(url):
    """Is the address this computer or the local network (loopback or a private address)?"""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host in ("localhost",):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host.endswith(".local")
    return ip.is_loopback or ip.is_private or ip.is_link_local


def discover(connected=None):
    """Look for servers on this computer at the usual address of each preset, all at once. Only asks `/v1/models`; changes nothing.
    Returns [{url, preset, name, reachable, needs_key, models, already}] for every address that answered. An address that more than one
    preset uses (several servers default to 8080) can't be told apart, so it is offered as 'Another server' named by its address."""
    pres = presets()
    by_url = {}
    for p in pres:
        if p.get("url") and p.get("category") != "hosted" and is_local(normalise(p["url"])):     # hosted services are never contacted uninvited
            by_url.setdefault(normalise(p["url"]), []).append(p)
    have = {_canon(normalise(h["url"])) for h in (connected if connected is not None else all())}
    import concurrent.futures
    urls = sorted(by_url)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        got = dict(zip(urls, pool.map(probe, urls)))
    out = []
    for u in urls:
        r = got[u]
        if not r["reachable"]:
            continue
        owners = by_url[u]
        pre = owners[0] if len(owners) == 1 else next(p for p in pres if p["id"] == "other")
        name = pre["name"] if len(owners) == 1 else "Server at " + urllib.parse.urlsplit(u).netloc
        out.append(dict(r, url=u, preset=pre["id"], name=name, already=_canon(u) in have))
    return out


def connect_found(found=None, typed=None):
    """Connect what answers: the address the person typed (with its key), then every usual address `discover` finds. Skips servers that
    are already connected, and ones that want a key Shoggoth wasn't given. Returns
    {connected: [{id, name, url}], needs_key: [url], already: [url], unreachable: [url], found: n}."""
    res = {"connected": [], "needs_key": [], "already": [], "unreachable": [], "found": 0}
    if typed and str(typed.get("url") or "").strip():
        url = normalise(typed["url"])
        r = probe(url, typed.get("key", ""))
        if not r["reachable"]:
            res["unreachable"].append(url)
        elif r["needs_key"]:
            res["needs_key"].append(url)
            res["found"] += 1
        else:
            res["found"] += 1
            try:
                e = add(typed.get("preset") or "other", url, typed.get("key", ""), "")
                res["connected"].append({"id": e["id"], "name": e["name"], "url": e["url"]})
            except ValueError:
                res["already"].append(url)
    found = discover() if found is None else found
    res["found"] += len(found)
    for f in found:
        if f["already"] or f["url"] in res["already"]:
            res["already"].append(f["url"])
        elif f["needs_key"]:
            res["needs_key"].append(f["url"])
        elif not is_local(f["url"]) or not f.get("models"):
            continue                            # only a server on this computer or network that lists models is connected without asking
        else:
            try:
                e = add(f["preset"], f["url"], "", f["name"])
                res["connected"].append({"id": e["id"], "name": e["name"], "url": e["url"]})
            except ValueError:
                res["already"].append(f["url"])
    return res


class ConnectedServer(provbase.GenericProvider):
    """An engine that is only an address. It lists what the server has, hands out its address, and never starts or stops anything."""

    def __init__(self, entry, ctx):
        spec = {"id": entry["id"], "name": entry.get("name") or entry["id"], "api_version": 1, "tier": "", "run": {"mode": "external"},
                "endpoint": {"base_url": api_base(entry["url"])}, "memory": {"group": ""}}
        super().__init__(spec, "", ctx)
        self._url = entry["url"]
        self._seen = (0.0, [], 0)

    def _entry(self):
        return get(self.id) or {"url": self._url, "key": ""}

    def _api(self):
        """The wire format this server's models speak: its own `api` when the person chose one, else the preset's rules."""
        e = self._entry()
        return e.get("api") or engines.api_for(e.get("preset") or "", "")

    def _key(self):
        return self._entry().get("key", "")

    def _models(self):
        t, v, _ = self._seen
        if time.time() - t < 5:
            return v
        e = self._entry()
        status, ms = list_models(e["url"], e.get("key", ""), e.get("fp"), api=e.get("api", ""))
        out = [dict(m, port=0, owner="yours", ctx=m.get("ctx") or e.get("ctx") or 0) for m in ms] if status == 200 else []
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
                return {"base_url": api_base(e["url"]), "key": e.get("key", ""), "model": m["id"], "ctx": m["ctx"], "owner": "yours",
                        "pin": e.get("fp") or "", "api": e.get("api", "")}
        return None

    def pi_endpoint(self, zest_port):
        """Where Pi talks to this server, for the format its models speak: an OpenAI-compatible base for all of them, except Gemini's
        native door, which Pi's own Gemini client reaches at the version root."""
        e = self._entry()
        api = e.get("api") or engines.api_for(e.get("preset") or "", "")
        if api == "google-generative-ai":
            base = engines.pi_base_url(api, e["url"])
        elif api == "ollama-native":
            base = e["url"] + "/v1"                       # Ollama answers on both doors; /v1 gives the model list and chat Pi knows
        else:
            base = api_base(e["url"])
        return {"base_url": base, "key": e.get("key", "") or self.id}

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
        r = probe(self._entry()["url"], self._key(), self._entry().get("fp"))
        return [{"name": "its model list", "ok": r["reachable"] and not r["needs_key"], "detail": "needs a key" if r["needs_key"] else "" if r["reachable"] else "not answering"}]
