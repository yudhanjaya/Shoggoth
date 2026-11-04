"""Run three tasks on one model through Shoggoth's gateway and keep everything: prompts, replies, tool calls, speeds."""
import http.client, json, os, re, socket, sys, time, urllib.parse, urllib.request, html

HOST, PORT = "127.0.0.1", int(os.environ.get("TRIAL_PORT", "13398"))
PREFIX = os.environ.get("TRIAL_PREFIX", "/api/v1")
NOLOAD = bool(os.environ.get("TRIAL_NOLOAD"))
HERE = os.path.dirname(os.path.abspath(__file__))
SEED = 7


def api(method, path, body=None, timeout=900):
    c = http.client.HTTPConnection(HOST, PORT, timeout=timeout)
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers={"Content-Type": "application/json"})
    r = c.getresponse(); data = r.read(); c.close()
    try: return r.status, json.loads(data or b"{}")
    except ValueError: return r.status, {"raw": data[:300].decode("utf8", "replace")}


def chat(model, messages, tools=None, max_tokens=2000, temperature=0.7):
    """One streamed completion. Returns {content, reasoning, tool_calls, usage, decode_tps, ttft, seconds, finish}."""
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature, "seed": SEED, "stream": True,
            "stream_options": {"include_usage": True}}
    if tools: body["tools"] = tools
    c = http.client.HTTPConnection(HOST, PORT, timeout=1800)
    t0 = time.time()
    c.request("POST", PREFIX + "/chat/completions", body=json.dumps(body), headers={"Content-Type": "application/json"})
    r = c.getresponse()
    if r.status != 200:
        raise RuntimeError("HTTP %d: %s" % (r.status, r.read(400).decode("utf8", "replace")))
    content, reasoning, calls, usage, timings, finish = "", "", {}, {}, {}, None
    first = last = None
    for raw in r:
        line = raw.decode("utf8", "replace").strip()
        if not line.startswith("data:") or line.endswith("[DONE]"): continue
        try: o = json.loads(line[5:])
        except ValueError: continue
        if o.get("usage"): usage = o["usage"]
        if o.get("timings"): timings = o["timings"]
        for ch in o.get("choices") or []:
            d = ch.get("delta") or {}
            if ch.get("finish_reason"): finish = ch["finish_reason"]
            if d.get("content"): content += d["content"]; first = first or time.time(); last = time.time()
            rc = d.get("reasoning_content") or d.get("reasoning")
            if rc: reasoning += rc; first = first or time.time(); last = time.time()
            for tc in d.get("tool_calls") or []:
                k = tc.get("index", 0); cur = calls.setdefault(k, {"id": "", "name": "", "arguments": ""})
                if tc.get("id"): cur["id"] = tc["id"]
                f = tc.get("function") or {}
                if f.get("name"): cur["name"] += f["name"]
                if f.get("arguments"): cur["arguments"] += f["arguments"]
                first = first or time.time(); last = time.time()
    c.close()
    n = int(usage.get("completion_tokens") or 0)
    tps = timings.get("predicted_per_second") or ((max(n - 1, 1) / (last - first)) if first and last and last > first else None)
    return {"content": content, "reasoning": reasoning, "tool_calls": [calls[k] for k in sorted(calls)], "usage": usage,
            "decode_tps": round(tps, 1) if tps else None, "prefill_tps": round(timings.get("prompt_per_second"), 1) if timings.get("prompt_per_second") else None,
            "ttft": round(first - t0, 2) if first else None, "seconds": round(time.time() - t0, 1), "finish": finish}


# ---- the wallpaper task's tools: a search, a page reader and a downloader. Nothing else: no shell, no files outside the folder.
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
TOOLS = [
    {"type": "function", "function": {"name": "web_search", "description": "Search the web. Returns titles, links and short snippets.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "fetch_page", "description": "Read a web page: returns its text and the image links found on it.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "download_file", "description": "Download an image from a URL into the downloads folder.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}, "filename": {"type": "string"}}, "required": ["url", "filename"]}}},
]


def _get(url, limit=3_000_000, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.headers.get("Content-Type", ""), r.read(limit)


def run_tool(name, args, folder):
    try:
        if name == "web_search":
            q = str(args.get("query", ""))
            _, b = _get("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(q))
            t = b.decode("utf8", "replace")
            out = []
            for m in re.finditer(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>', t, re.S):
                u = m.group(1)
                if "uddg=" in u: u = urllib.parse.unquote(u.split("uddg=")[1].split("&")[0])
                out.append("%s\n%s\n%s" % (html.unescape(re.sub("<[^>]+>", "", m.group(2))).strip(), u, html.unescape(re.sub("<[^>]+>", "", m.group(3))).strip()))
                if len(out) >= 6: break
            return "\n\n".join(out) or "No results."
        if name == "fetch_page":
            u = str(args.get("url", ""))
            if not u.startswith("http"): return "Error: give a full https:// address."
            ct, b = _get(u)
            t = b.decode("utf8", "replace")
            imgs = []
            for m in re.finditer(r'(?:src|href|data-src|content)="([^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', t, re.I):
                iu = urllib.parse.urljoin(u, html.unescape(m.group(1)))
                if iu not in imgs: imgs.append(iu)
            text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", " ", t, flags=re.S | re.I))).strip()
            return "TEXT: %s\n\nIMAGE LINKS:\n%s" % (text[:2500], "\n".join(imgs[:25]) or "none found")
        if name == "download_file":
            u, fn = str(args.get("url", "")), re.sub(r"[^A-Za-z0-9._-]", "_", str(args.get("filename", "image.jpg")))[:80] or "image.jpg"
            if not u.startswith("https://"): return "Error: only https:// addresses can be downloaded."
            ct, b = _get(u, limit=40_000_000, timeout=60)
            if not ct.lower().startswith("image/"): return "Error: that address is not an image (it is %s)." % ct
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, fn), "wb") as f: f.write(b)
            return "Saved %s (%d bytes)." % (fn, len(b))
    except Exception as e:  # noqa: BLE001
        return "Error: %s" % e
    return "Error: unknown tool."


def describe_images(folder):
    from PIL import Image
    out = []
    for fn in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        p = os.path.join(folder, fn)
        try:
            with Image.open(p) as im: out.append({"file": fn, "bytes": os.path.getsize(p), "width": im.width, "height": im.height})
        except Exception as e:  # noqa: BLE001
            out.append({"file": fn, "bytes": os.path.getsize(p), "error": "not a readable image"})
    return out


STORY = "Write a short story about a knight."
WALL = "Download 3 high-quality 4K wallpapers of nature (at least 3840 pixels wide) into the downloads folder, then tell me what you saved."
CODE_Q = "Here is a Python file and what it is supposed to do (in its docstring). It has bugs. Find every bug: for each one, say which line or function it is in, what is wrong, and how to fix it.\n\n```python\n%s\n```"


def main(model, label):
    out = {"model": model, "label": label, "tasks": {}}
    st, d = (200, {"note": "standalone llama-server"}) if NOLOAD else api("POST", "/api/v1/load", {"model_name": model, "ctx_size": 32768})
    out["load"] = {"status": st, "reply": d}
    if st != 200:
        return out
    # task 1: the story
    if os.environ.get("SKIP_STORY"):
        out["tasks"]["story"] = json.load(open(os.path.join(HERE, "result_%s.json" % label)))["tasks"]["story"]
    else:
        r = chat(model, [{"role": "user", "content": STORY}], max_tokens=int(os.environ.get("STORY_MAX", "2500")))
        out["tasks"]["story"] = {"input": STORY, **r}
    skip_wall = bool(os.environ.get("SKIP_WALL"))
    # task 2: wallpapers (tool loop)
    folder = os.path.join(HERE, "downloads", label)
    os.makedirs(folder, exist_ok=True)
    msgs = [{"role": "system", "content": "You are a helpful assistant with tools to search the web, read pages and download images. Use them to do what the user asks."},
            {"role": "user", "content": WALL}]
    steps, speeds = [], []
    try:
        for turn in range(0 if skip_wall else 14):
            r = chat(model, msgs, tools=TOOLS, max_tokens=1500, temperature=0.4)
            speeds.append(r["decode_tps"])
            step = {"turn": turn + 1, "reasoning": r["reasoning"], "content": r["content"], "tool_calls": [], "decode_tps": r["decode_tps"], "tokens": r["usage"].get("completion_tokens")}
            if not r["tool_calls"]:
                steps.append(step); break
            msgs.append({"role": "assistant", "content": r["content"] or None, "tool_calls": [{"id": c["id"] or "call_%d_%d" % (turn, i), "type": "function", "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}} for i, c in enumerate(r["tool_calls"])]})
            for i, c in enumerate(r["tool_calls"]):
                try: args = json.loads(c["arguments"] or "{}")
                except ValueError: args = {}
                res = run_tool(c["name"], args, folder)
                step["tool_calls"].append({"name": c["name"], "arguments": args, "result": res[:700]})
                msgs.append({"role": "tool", "tool_call_id": c["id"] or "call_%d_%d" % (turn, i), "content": res[:3500]})
            steps.append(step)
    except Exception as e:  # noqa: BLE001
        steps.append({"error": str(e)})
    good = [s for s in speeds if s]
    if skip_wall:
        out["tasks"]["wallpapers"] = json.load(open(os.path.join(HERE, "result_%s.json" % label)))["tasks"]["wallpapers"]
    else: out["tasks"]["wallpapers"] = {"input": WALL, "steps": steps, "files": describe_images(folder), "decode_tps_avg": round(sum(good) / len(good), 1) if good else None}
    # task 3: find the bugs
    code = open(os.path.join(HERE, "buggy_inventory.py")).read()
    q = CODE_Q % code
    r = chat(model, [{"role": "user", "content": q}], max_tokens=int(os.environ.get("BUGS_MAX", "3000")), temperature=0.2)
    text = (r["content"] or "").lower()
    bugs = json.load(open(os.path.join(HERE, "bugs.json")))
    out["tasks"]["bugs"] = {"input": q, **r, "found": {b["id"]: any(k.lower() in text for k in b["keywords"]) for b in bugs}}
    if not NOLOAD: api("POST", "/api/v1/unload", {"model_name": model})
    return out


if __name__ == "__main__":
    model, label = sys.argv[1], sys.argv[2]
    res = main(model, label)
    import shutil
    if os.path.exists(os.path.join(HERE, "result_%s.json" % label)) and not os.environ.get("SKIP_WALL"): shutil.copy(os.path.join(HERE, "result_%s.json" % label), os.path.join(HERE, "result_%s.prev.json" % label))
    with open(os.path.join(HERE, "result_%s.json" % label), "w") as f: json.dump(res, f, indent=2)
    print("done", label, {k: (v.get("decode_tps") or v.get("decode_tps_avg")) for k, v in res.get("tasks", {}).items()}, res.get("load", {}).get("status"))
