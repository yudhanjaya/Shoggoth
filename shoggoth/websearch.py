"""Web search for the chat: Brave Search or Tavily with the person's own key, or DuckDuckGo with none.

Every search leaves the computer, so the chat asks the person before each one (see pi/zest-gate.js) and Settings can
switch it off. DuckDuckGo's plain-HTML results page is read with the standard library: free and keyless, but it is a page,
not an API, and it refuses automated use after a few quick searches. Shoggoth therefore makes exactly one request per search
there, never retries, and after a refusal leaves DuckDuckGo alone for a while. The failure says what to do about it:
get a free key from Brave or Tavily and enter it in the chat's Details panel.
"""
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

PROVIDERS = ("duckduckgo", "brave", "tavily")
KEY_PAGES = {"brave": "https://brave.com/search/api/", "tavily": "https://app.tavily.com/"}
DDG_URL = "https://html.duckduckgo.com/html/"
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
TAVILY_URL = "https://api.tavily.com/search"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
COOLDOWN = 15 * 60                 # seconds to leave DuckDuckGo alone after it refuses
_ddg = {"until": 0}
_RESULT = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|\Z)', re.S)
_SNIPPET = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)

NEED_KEY = ("DuckDuckGo refused this search, because it limits automated searching. Don't try again. Tell the user that web search "
            "needs a free API key from Brave Search or Tavily, and that they can add one in Shoggoth: open the chat's Details panel, "
            "find Web search, choose the provider and paste the key.")


class SearchError(RuntimeError):
    """A failure with a sentence for the person; `need_key` marks the one that should send them to get a key."""
    def __init__(self, message, need_key=False):
        super().__init__(message)
        self.need_key = need_key


def _text(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", fragment or "")).split())


def _target(href):
    """DuckDuckGo wraps every link in a redirect (//duckduckgo.com/l/?uddg=<real url>); unwrap it."""
    href = html.unescape(href)
    if href.startswith("//"):
        href = "https:" + href
    q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
    return q["uddg"][0] if "uddg" in q else href


def _open(req, provider):
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.read(2_000_000).decode("utf8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 422):                # Brave answers 422 for a key it doesn't know
            raise SearchError("%s didn't accept the key. Check it was copied whole, and that it is for the search API." % provider)
        if e.code == 429:
            raise SearchError("%s says this key has used up its searches for now (or is searching too fast). Try again later." % provider)
        if e.code == 402:
            raise SearchError("%s says this key is out of credit." % provider)
        raise SearchError("%s answered %s. Try again in a little while." % (provider, e.code))
    except (urllib.error.URLError, OSError) as e:
        raise SearchError("Couldn't reach %s (%s). Check your internet connection." % (provider, getattr(e, "reason", e)))


def _duckduckgo(query, n):
    left = _ddg["until"] - time.time()
    if left > 0:
        raise SearchError("DuckDuckGo is refusing searches from this computer, so Shoggoth is leaving it alone for another %d minutes." % (left // 60 + 1), need_key=True)
    req = urllib.request.Request(DDG_URL, data=urllib.parse.urlencode({"q": query}).encode(),
                                 headers={"User-Agent": UA, "Accept-Language": "en", "Content-Type": "application/x-www-form-urlencoded"})
    try:
        page = _open(req, "DuckDuckGo")
    except SearchError as e:
        if "answered 202" in str(e) or "answered 403" in str(e) or "answered 429" in str(e):
            _ddg["until"] = time.time() + COOLDOWN
            raise SearchError("DuckDuckGo refused the search.", need_key=True)
        raise
    out = []
    for href, title, rest in _RESULT.findall(page):
        url = _target(href)
        if not url.startswith("http") or "duckduckgo.com/y.js" in url:      # skip ads
            continue
        m = _SNIPPET.search(rest)
        out.append({"title": _text(title), "url": url, "snippet": _text(m.group(1)) if m else ""})
    if out:
        return out[:n]
    if re.search(r"anomaly|challenge|captcha|unusual traffic", page, re.I):
        _ddg["until"] = time.time() + COOLDOWN
        raise SearchError("DuckDuckGo asked for a human check, so it refused the search.", need_key=True)
    raise SearchError("No results for that search.")


def _brave(query, n, key):
    req = urllib.request.Request(BRAVE_URL + "?" + urllib.parse.urlencode({"q": query, "count": n}),
                                 headers={"Accept": "application/json", "X-Subscription-Token": key, "User-Agent": "Shoggoth"})
    data = json.loads(_open(req, "Brave Search") or "{}")
    rows = ((data.get("web") or {}).get("results")) or []
    out = [{"title": _text(r.get("title")), "url": r.get("url", ""), "snippet": _text(r.get("description"))} for r in rows if r.get("url")]
    if not out:
        raise SearchError("No results for that search.")
    return out[:n]


def _tavily(query, n, key):
    body = json.dumps({"query": query, "max_results": n, "search_depth": "basic"}).encode()
    req = urllib.request.Request(TAVILY_URL, data=body, headers={"Content-Type": "application/json", "Authorization": "Bearer " + key, "User-Agent": "Shoggoth"})
    data = json.loads(_open(req, "Tavily") or "{}")
    out = [{"title": _text(r.get("title")), "url": r.get("url", ""), "snippet": _text(r.get("content"))[:500]} for r in data.get("results") or [] if r.get("url")]
    if not out:
        raise SearchError("No results for that search.")
    return out[:n]


def search(query, n=6, cfg=None):
    """[{title, url, snippet}, ...] from the provider in `cfg` ({"provider", "brave_key", "tavily_key"}).
    Raises SearchError with a sentence a person can read."""
    cfg = cfg or {}
    query = " ".join(str(query or "").split())[:300]
    if not query:
        raise SearchError("There was nothing to search for.")
    n = max(1, min(int(n or 6), 10))
    provider = cfg.get("provider") if cfg.get("provider") in PROVIDERS else "duckduckgo"
    if provider == "brave":
        key = (cfg.get("brave_key") or "").strip()
        if not key:
            raise SearchError("Brave Search is chosen but no key has been entered. Add it in the chat's Details panel, under Web search.")
        return _brave(query, n, key)
    if provider == "tavily":
        key = (cfg.get("tavily_key") or "").strip()
        if not key:
            raise SearchError("Tavily is chosen but no key has been entered. Add it in the chat's Details panel, under Web search.")
        return _tavily(query, n, key)
    return _duckduckgo(query, n)
