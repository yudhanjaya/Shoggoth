"""`shoggoth status` and `shoggoth connect`: what is running, and what another program needs to talk to it. Plain functions that read a
running Shoggoth through client.py; nothing here starts or changes anything."""
import argparse
import json
import sys

import client


def snapshot(c):
    """One read of everything a script or a person asks about: engines, models, the gateway, requests in flight."""
    srv, provs, rt = c.get("/ui/server"), c.get("/ui/providers"), c.get("/ui/router")
    gw = srv["gateway"]
    engines = [{"id": p["id"], "name": p["name"], "installed": bool(p.get("installed")),
                "running": [{"port": r.get("port"), "owner": r.get("owner")} for r in p.get("running") or []]} for p in provs["providers"]]
    return {"gateway": {"running": bool(gw["running"]), "url": gw["url"], "lan": bool(gw["lan"]), "error": gw.get("error")},
            "engines": engines, "models": [{"id": m["id"], "engine": m["engine"], "live": bool(m.get("live"))} for m in rt["models"]],
            "starting": rt["state"]["ref"], "in_flight": len(srv.get("active") or []), "requests_today": srv.get("today", 0)}


def status_text(s):
    g = s["gateway"]
    out = ["Gateway: %s%s" % (g["url"] if g["running"] else "not running", " (open to your network)" if g["lan"] and g["running"] else "")]
    if g["error"]:
        out.append("  " + g["error"])
    for e in s["engines"]:
        state = ", ".join("running on port %s%s" % (r["port"], " (started by Shoggoth)" if r["owner"] == "zest" else "") for r in e["running"]) or \
            ("installed, not running" if e["installed"] else "not installed")
        out.append("%s: %s" % (e["name"], state))
    live = [m["id"] for m in s["models"] if m["live"]]
    out.append("Loaded: %s" % (", ".join(live) or "nothing"))
    if s["starting"]:
        out.append("Starting: %s" % s["starting"])
    out.append("Requests: %d in flight, %d today" % (s["in_flight"], s["requests_today"]))
    return "\n".join(out)


def mask(key):
    return key[:9] + "…" + key[-4:] if len(key) > 16 else "…"


def connect_text(c, reveal=False):
    gw = c.get("/ui/server")["gateway"]
    rt = c.get("/ui/router")
    names = [m["id"] for m in rt["models"]]
    url, key = gw["url"], gw["key"]
    shown = key if reveal else mask(key)
    lines = ["Point another program at Shoggoth:", "",
             "  Base URL:  " + url, "  API key:   %s%s" % (shown, "" if reveal else "   (shoggoth connect --reveal to show it)"), "",
             "  Models:    " + (", ".join(names) or "none yet (add one in Shoggoth)"), "",
             "OpenAI-style programs:", "  export OPENAI_BASE_URL=%s" % url, "  export OPENAI_API_KEY=%s" % shown, "",
             "Try it:", "  curl %s/chat/completions -H 'Authorization: Bearer %s' -H 'Content-Type: application/json' \\" % (url, shown),
             "    -d '{\"model\": \"%s\", \"messages\": [{\"role\": \"user\", \"content\": \"Say hi\"}]}'" % (names[0] if names else "MODEL")]
    if not gw["running"]:
        lines.insert(1, "(The gateway isn't running: %s)" % (gw.get("error") or "it is switched off in Shoggoth."))
    if gw["lan"]:
        lines.append("\nThe gateway is open to your network. Anyone with the key can use it.")
    return "\n".join(lines)


def main(argv):
    ap = argparse.ArgumentParser(prog="shoggoth")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "connect"):
        p = sub.add_parser(name)
        p.add_argument("--host")
        p.add_argument("--port", type=int)
        if name == "status":
            p.add_argument("--json", action="store_true")
        else:
            p.add_argument("--reveal", action="store_true")
    a = ap.parse_args(argv)
    c = client.Client(a.host, a.port)
    try:
        if a.cmd == "status":
            s = snapshot(c)
            print(json.dumps(s, indent=2) if a.json else status_text(s))
            return 0 if s["gateway"]["running"] else 1
        print(connect_text(c, a.reveal))
        return 0
    except client.Unreachable as e:
        print(e, file=sys.stderr)
        return 2
