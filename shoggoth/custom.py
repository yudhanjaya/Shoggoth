"""Hooking up a provider by hand: an address, a key, an API format, and, if the person wants it, a definition so Shoggoth recognises it
next time.

Two halves:

- `probe()` sends one short chat request and one request that offers a tool, and says in plain words what happened ("Chat works · Tool
  calls work", or which part failed). It only reads.
- `save()` writes the connection (a server the person runs, see servers.py) and, when asked, a definition file in engines.d so the
  scan names the engine on any port. The definition goes through `engines.clean`, so it can't carry a command, an address that isn't
  http(s), or a probe path that isn't a plain GET path.

Standard library only.
"""
import ipaddress
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "_vendor"))
import yaml  # noqa: E402

import engines  # noqa: E402
import servers  # noqa: E402

TIMEOUT = 20
CHAT = "Reply with the single word: ready."
WITH_TOOL = "Call the tool shoggoth_time with no arguments, then stop."
TOOL = {"name": "shoggoth_time", "description": "Tells the time on the computer that runs the model.",
        "schema": {"type": "object", "properties": {}, "required": []}}
FORMATS = ("openai-completions", "anthropic-messages", "openai-responses", "ollama-native", "google-generative-ai")
DEFAULT_CTX = 32768
MAX_NAME = 60
NAME_RE = re.compile(r"[^a-z0-9]+")


def is_local(url):
    """Does this address stay on this computer or the local network? (loopback or a private range)."""
    host = (urllib.parse.urlsplit(str(url or "")).hostname or "").lower()
    if host in ("localhost", "::1"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host.endswith(".local") or "." not in host
    return ip.is_loopback or ip.is_private or ip.is_link_local


def _headers(api, key):
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if not key:
        return h
    if api == "anthropic-messages":
        h["x-api-key"] = key
        h["anthropic-version"] = "2023-06-01"
    elif api == "google-generative-ai":
        h["x-goog-api-key"] = key
    else:
        h["Authorization"] = "Bearer " + key
    return h


def _tool_for(api):
    fn = {"name": TOOL["name"], "description": TOOL["description"], "parameters": TOOL["schema"]}
    if api == "anthropic-messages":
        return {"name": TOOL["name"], "description": TOOL["description"], "input_schema": TOOL["schema"]}
    if api == "openai-responses":
        return dict(fn, type="function")
    if api == "google-generative-ai":
        return {"functionDeclarations": [{"name": TOOL["name"], "description": TOOL["description"], "parameters": TOOL["schema"]}]}
    return {"type": "function", "function": fn}


def request(api, url, model, key="", tools=False):
    """(full url, headers, body) for one chat request in `api`'s own shape. Raises ValueError for a format Shoggoth can't speak."""
    base = servers.normalise(url)
    h = _headers(api, key)
    text = WITH_TOOL if tools else CHAT
    tool = [_tool_for(api)] if tools else None
    if api == "openai-completions":
        body = {"model": model, "messages": [{"role": "user", "content": text}], "max_tokens": 32, "stream": False}
        if tool:
            body.update(tools=tool, tool_choice="auto")
        return base + "/v1/chat/completions", h, body
    if api == "ollama-native":
        body = {"model": model, "messages": [{"role": "user", "content": text}], "stream": False, "options": {"num_predict": 32}}
        if tool:
            body["tools"] = tool
        return base + "/api/chat", h, body
    if api == "anthropic-messages":
        body = {"model": model, "max_tokens": 32, "messages": [{"role": "user", "content": text}]}
        if tool:
            body["tools"] = tool
        return base + "/v1/messages", h, body
    if api == "openai-responses":
        body = {"model": model, "input": text, "max_output_tokens": 32}
        if tool:
            body["tools"] = tool
        return base + "/v1/responses", h, body
    if api == "google-generative-ai":
        body = {"contents": [{"parts": [{"text": text}]}]}
        if tool:
            body["tools"] = tool
        return "%s/models/%s:generateContent" % (engines.pi_base_url(api, base), urllib.parse.quote(str(model), safe="")), h, body
    raise ValueError("Shoggoth doesn't know the API format %r." % api)


def _wants_a_tool(api, d):
    """Did the reply ask for the tool? One shape per format."""
    try:
        if api == "openai-completions":
            m = ((d.get("choices") or [{}])[0] or {}).get("message") or {}
            return bool(m.get("tool_calls") or m.get("function_call"))
        if api == "ollama-native":
            return bool(((d.get("message") or {}).get("tool_calls")))
        if api == "anthropic-messages":
            return any(isinstance(c, dict) and c.get("type") == "tool_use" for c in d.get("content") or [])
        if api == "openai-responses":
            return any(isinstance(o, dict) and o.get("type") == "function_call" for o in d.get("output") or [])
        if api == "google-generative-ai":
            parts = (((d.get("candidates") or [{}])[0] or {}).get("content") or {}).get("parts") or []
            return any(isinstance(p, dict) and p.get("functionCall") for p in parts)
    except (IndexError, AttributeError, TypeError):
        return False
    return False


def _says_something(api, d):
    try:
        if api == "openai-completions":
            return bool((((d.get("choices") or [{}])[0] or {}).get("message") or {}).get("content"))
        if api == "ollama-native":
            return bool((d.get("message") or {}).get("content"))
        if api == "anthropic-messages":
            return any(isinstance(c, dict) and c.get("type") == "text" and c.get("text") for c in d.get("content") or [])
        if api == "openai-responses":
            return bool(d.get("output_text")) or any(isinstance(o, dict) and o.get("type") == "message" for o in d.get("output") or [])
        if api == "google-generative-ai":
            parts = (((d.get("candidates") or [{}])[0] or {}).get("content") or {}).get("parts") or []
            return any(isinstance(p, dict) and p.get("text") for p in parts)
    except (IndexError, AttributeError, TypeError):
        return False
    return False


def _post(url, headers, body, timeout):
    """(status, json, plain reason). Status 0 when nothing HTTP answered."""
    data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers, method="POST"), timeout=timeout) as r:
            raw = r.read(2_000_000)
            status = r.status
    except urllib.error.HTTPError as e:
        try:
            raw, status = e.read(200_000), e.code
        except OSError:
            raw, status = b"", e.code
    except (OSError, ValueError) as e:
        return 0, {}, str(e) or "it didn't answer"
    try:
        return status, json.loads(raw or b"{}"), ""
    except ValueError:
        return status, {}, ""


def _why(status, detail, path):
    if status == 0:
        return "Nothing answered at that address. Check that the server is running and the address is right."
    if status in (401, 403):
        return "It answered, but the key was refused."
    if status == 404:
        return "That address answered, but not at %s. Check the address, and that the API format is the right one." % path
    if status == 429:
        return "It answered, but it is busy or its limit is used up. Try again in a little while."
    return "It answered with %d instead of a reply.%s" % (status, (" " + detail) if detail else "")


def probe(url, key="", api="openai-completions", model="", timeout=TIMEOUT):
    """One chat request and one request that offers a tool. {ok, chat, tools, message, models, ctx, api, local}.

    `models` is the server's own list when it has one; `ctx` is what the server says about the model, else 0 (the page then keeps the
    number the person typed)."""
    api = api if api in FORMATS else "openai-completions"
    out = {"ok": False, "chat": False, "tools": None, "message": "", "models": [], "ctx": 0, "api": api, "local": False, "model": ""}
    try:
        base = servers.normalise(url)
    except ValueError as e:
        out["message"] = str(e)
        return out
    out["local"] = is_local(base)
    status, ms = servers.list_models(base, key, None, timeout, api=api)
    out["models"] = [m["id"] for m in ms]
    model = str(model or "").strip() or (out["models"][0] if out["models"] else "")
    out["model"] = model
    if model:
        out["ctx"] = next((m["ctx"] for m in ms if m["id"] == model and m["ctx"]), 0)
    if not model:
        out["message"] = ("It answered, but it doesn't list any models. Type the model's name, and try again." if status == 200
                          else _why(status, "", "/models"))
        return out
    chat_url, h, body = request(api, base, model, key)
    st, d, detail = _post(chat_url, h, body, timeout)
    if st != 200:
        out["message"] = _why(st, detail, urllib.parse.urlsplit(chat_url).path.rsplit("/models/", 1)[0])
        return out
    out["chat"] = bool(_says_something(api, d) or _wants_a_tool(api, d))
    if not out["chat"]:
        out["message"] = "It answered, but not with a reply. Check the model name and the API format."
        return out
    out["ok"] = True
    tool_url, h2, body2 = request(api, base, model, key, tools=True)
    st2, d2, _ = _post(tool_url, h2, body2, timeout)
    if st2 != 200:
        out["message"] = "Chat works · Tool calls not tried: the second request answered %d." % st2
        return out
    out["tools"] = bool(_wants_a_tool(api, d2))
    out["message"] = ("Chat works · Tool calls work" if out["tools"] else
                      "Chat works · It answered without calling the tool, so it may not be able to work in a folder.")
    return out


# ---------------------------------------------------------------- remembering it
def _slug(name):
    s = NAME_RE.sub("-", str(name or "").lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)[:48].strip("-")
    return s if s and re.match(r"^[a-z0-9]", s) else ""


def _unique(slug, defs):
    taken = {d["id"] for d in defs}
    if slug not in taken:
        return slug
    n = 1
    while "%s-%d" % (slug, n + 1) in taken:
        n += 1
    return "%s-%d" % (slug, n + 1)


def definition(url, name="", api="openai-completions", local=True, match=None, identify=None, key="optional", hint=""):
    """The definition file's settings for a server the person set up. Only what engines.py's schema allows."""
    api = api if api in engines.APIS else "openai-completions"
    out = {"id": "", "name": (name or "").strip()[:MAX_NAME] or "My server", "category": "local" if local else "hosted",
           "summary": "A server you set up yourself.", "url": servers.normalise(url),
           "formats": [{"api": api}], "loads": "fixed", "key": key, "hint": (hint or "").strip()[:300]}
    if match:
        out["match"] = [str(m) for m in match][:8]
    if identify:
        out["identify"] = identify
    return out


LEARN_PATHS = ("/props", "/api/version", "/v1/models")


def listen_notes(url, found=None):
    """{match: [process name], identify: [a signature the server gave]}: what the scan saw on this address, for "Recognise this next
    time?". `found` is a scan result list (tests pass their own); without one the port is looked at."""
    u = urllib.parse.urlsplit(servers.normalise(url))
    port = u.port or (443 if u.scheme == "https" else 80)
    match = []
    if found is None:
        try:
            import scan
            found = scan.scan()
        except Exception:  # noqa: BLE001 - the scan is a convenience; a failure just means nothing to learn
            found = []
    hit = next((f for f in (found or []) if f.get("port") == port), None)
    if hit and hit.get("process") and hit["process"] not in ("python", "python3"):
        match = [hit["process"]]
    return {"match": match, "identify": [], "port": port}


def save(url, key="", api="openai-completions", name="", model="", models=None, ctx=0, local=None, remember=False,
         match=None, identify=None, timeout=TIMEOUT):
    """Connect the server, and (with `remember`) write a definition so the scan knows it next time.

    Returns {ok, id, server, file, problems}. `problems` is what engines.py said about the definition; when it isn't empty nothing was
    written at all. Raises ValueError, in plain words, for an address that isn't one."""
    base = servers.normalise(url)
    api = api if api in FORMATS else "openai-completions"
    local = is_local(base) if local is None else bool(local)
    extra = {"api": api, "ctx": int(ctx or 0), "local": local}
    d = definition(base, name=name, api=api, local=local, match=match, identify=identify,
                   key="required" if key else "none")
    if remember:
        problem = _write_definition(d)
        if problem:
            return {"ok": False, "id": "", "server": None, "file": "", "problems": [problem]}
        preset = d["id"]
    else:
        preset = "other"
    server = servers.add(preset, base, key, name or d["name"], extra)
    shown = {"id": server["id"], "name": server["name"], "url": server["url"]}
    return {"ok": True, "id": server["id"], "server": shown, "file": d["id"] if remember else "",
            "problems": [], "definition": d if remember else None}


def _write_definition(d):
    """Write one definition under engines.d, after engines.py has approved every key in it. Returns a problem in plain words, or "".
    The id it was saved under comes back in `d["id"]`."""
    wanted = dict(d, id=_unique(_slug(d.get("name")) or "my-server", engines.definitions()))
    clean, errs = engines.clean(wanted)
    if errs:
        return " ".join(msg for _key, msg in errs)
    folder = engines.user_dir()
    path = os.path.join(folder, clean["id"] + ".yaml")
    try:
        os.makedirs(folder, exist_ok=True)
        text = "# Written by Shoggoth from the Hook up something else form.\n" + yaml.safe_dump(clean, sort_keys=False, allow_unicode=True)
        with open(path, "w", encoding="utf8") as f:
            f.write(text)
    except OSError as e:
        return "That setting couldn't be saved (%s)." % (getattr(e, "strerror", None) or "the folder isn't writable")
    # Read it back the way Shoggoth will, so a file that only looked right is still refused before it is kept.
    _defs, problems = engines.read_file(path)
    if problems:
        try:
            os.unlink(path)
        except OSError:
            pass
        return problems[0]["error"]
    d["id"] = clean["id"]
    return ""
