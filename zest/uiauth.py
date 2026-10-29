"""Who may use Zest's own page. The browser Zest opens is let in once; everything else on this computer that can reach the port is not.

The web page can change settings, start engines and run commands in a folder, so "it is on 127.0.0.1" is not enough: another user on the
same machine, or any program, can reach it. Zest keeps a secret in its config folder (readable only by you). The window Zest opens
carries a one-time code in its address; the server swaps it for a cookie (HttpOnly, SameSite=Strict, kept for a year so a bookmark keeps
working) and every request after that must carry the cookie. A script can send the secret as `Authorization: Bearer <secret>` instead.
Set ZEST_AUTH=off to switch this off (tests, a machine only you use and trust). Standard library only.
"""
import hmac
import http.cookies
import os
import secrets
import threading
import time

import store

CODE_SECONDS = 120
_codes = {}
_lock = threading.Lock()


def enabled():
    return os.environ.get("ZEST_AUTH", "").lower() not in ("off", "0", "false", "no")


def _path():
    return os.path.join(store.config_dir(), "ui-token")


def token():
    """The secret, made on first use and kept (mode 600) so a restart or a second launch still works."""
    env = os.environ.get("ZEST_TOKEN")
    if env:
        return env
    with _lock:
        try:
            with open(_path()) as f:
                t = f.read().strip()
            if len(t) >= 20:
                return t
        except OSError:
            pass
        t = secrets.token_urlsafe(32)
        os.makedirs(store.config_dir(), mode=0o700, exist_ok=True)
        fd = os.open(_path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(t)
        return t


def cookie_name(port):
    return "zest_ui_%d" % int(port)           # cookies ignore the port, so two Zests on one computer must not share a name


def new_code():
    """A single-use code for the address of the window Zest opens. It is worth nothing after one use or two minutes."""
    now = time.time()
    code = secrets.token_urlsafe(18)
    with _lock:
        for k in [k for k, t in _codes.items() if t < now]:
            del _codes[k]
        _codes[code] = now + CODE_SECONDS
    return code


def redeem(code):
    with _lock:
        t = _codes.pop(str(code or ""), 0)
    return t > time.time()


def allowed(port, cookie_header, authorization):
    """Does this request carry the secret, as the cookie or as a bearer token?"""
    if not enabled():
        return True
    want = token()
    auth = str(authorization or "")
    if auth.lower().startswith("bearer ") and hmac.compare_digest(auth[7:].strip(), want):
        return True
    try:
        jar = http.cookies.SimpleCookie(cookie_header or "")
    except http.cookies.CookieError:
        return False
    got = jar.get(cookie_name(port))
    return bool(got) and hmac.compare_digest(got.value, want)


def set_cookie(port):
    return "%s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000" % (cookie_name(port), token())


LOCKED_PAGE = b"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zest</title><body style="font:16px/1.5 system-ui,sans-serif;max-width:34em;margin:12vh auto;padding:0 20px;color:#333">
<h1 style="font-weight:600">Zest is running</h1>
<p>This browser hasn't been let in yet. To keep other programs and people on this computer from using it, Zest only opens for the window it starts itself.</p>
<p><b>Start Zest again</b> (open the app, or run the AppImage again). It opens a window that is let in, and this address works in that browser afterwards.</p>
</body>"""
