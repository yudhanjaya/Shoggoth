"""The router: turns a model name into a running engine, making room first.

Chats, Pi and other apps all ask for a model by name. The router works out which provider serves it (resolve), frees whatever
would fight it for memory (the arbiter in make_room), starts what is needed and returns the address to use (activate), and stops
what Zest started once it has been idle (idle_tick). Nothing here names an engine: providers answer through provbase.Base.
Standard library only.
"""
import threading
import time

import provbase
import registry

_lock = threading.RLock()
STATE = {"ref": None, "provider": None, "step": "", "since": 0}      # what is being started right now, for the page's progress card
busy_checks = []          # callables(provider id) -> bool; True when something of Zest's (a chat answering) is using that provider
on_change = []            # callables run after an activation or a stop, so cached model lists are re-read
_idx = {"t": 0, "v": None}
_idle = {}


def providers():
    return registry.default().all()


def get(pid):
    return registry.default().get(pid)


def default_provider():
    """The provider a name nobody recognises goes to: the fast one (the first that isn't for large models)."""
    ps = providers()
    return next((p for p in ps if "large" not in p.tier_hint()), ps[0] if ps else None)


def invalidate():
    _idx["v"] = None
    for f in on_change:
        try:
            f()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- what can be asked for
def chat_models():
    """Every model to offer in chat, in one list: [{id (the name chats use), engine, pi_model, pi_provider, tools, reasoning, vision, ctx, live}].

    The first engine's models come first, then the others', as before."""
    out = []
    for p in providers():
        try:
            ms = p.chat_models()
        except NotImplementedError:
            continue
        except Exception:  # noqa: BLE001
            continue
        for m in ms:
            e = dict(m)
            e.update(engine=p.id, pi_model=m["id"], id=p.ref(m["id"]))
            e.setdefault("pi_provider", p.id)
            e.setdefault("live", False)
            out.append(e)
    return out


def _index():
    if _idx["v"] is None or time.time() - _idx["t"] > 15:
        ms = chat_models()
        v = {m["id"]: (m["engine"], m["pi_model"]) for m in ms}
        for m in ms:                                      # a provider's own name also works for an app that uses it (not for the default's)
            if m["engine"] != getattr(default_provider(), "id", None):
                v.setdefault(m["pi_model"], (m["engine"], m["pi_model"]))
        _idx.update(t=time.time(), v=v)
    return _idx["v"]


def resolve(ref):
    """(provider, the provider's own name for the model) for a name a chat or an app used.

    Known names go where they were found; `<provider>/<name>` goes to that provider; anything else (an embedding or speech model, a name
    Zest hasn't listed) goes to the default provider unchanged."""
    ref = str(ref or "")
    hit = _index().get(ref)
    if hit and get(hit[0]):
        return get(hit[0]), hit[1]
    for p in providers():
        if p is not default_provider() and ref.startswith(p.id + "/"):
            return p, ref[len(p.id) + 1:]
    return default_provider(), ref


def known(ref):
    """Is this a model Zest lists (from the cached index)? Names it doesn't list, like embedding or speech models, pass through untouched."""
    ref = str(ref or "")
    return ref in _index() or any(p is not default_provider() and ref.startswith(p.id + "/") for p in providers())


def info(ref):
    """The chat-list entry for a name, or None."""
    return next((m for m in chat_models() if m["id"] == str(ref)), None)


def loaded(ref):
    """(in memory?, context window) for a model, wherever it runs."""
    p, mid = resolve(ref)
    return p.loaded(mid) if p else (False, 0)


def max_ctx(ref):
    p, mid = resolve(ref)
    return p.max_ctx(mid) if p else 0


def fixed_ctx(ref):
    """True when the provider decides the context window when it starts it (so a chat can't ask for another)."""
    p, _ = resolve(ref)
    return bool(p) and (p.spec.get("capabilities") or {}).get("context") == "fixed"


def state():
    with _lock:
        return dict(STATE)


# ---------------------------------------------------------------- the memory arbiter
def _in_use(p):
    return p.busy() or any(chk(p.id) for chk in busy_checks)


def make_room(p, progress):
    """Free the memory `p` needs: stop whatever shares its memory group and can't sit beside it.

    Providers in the same group conflict when either is exclusive. What Zest started, or loaded, is stopped (unless it is answering,
    which is reported). What the person started themselves is never stopped: if `p` needs the memory to itself that is an error with
    the plain reason; otherwise it is a warning and the load goes ahead."""
    mem = p.memory()
    if not mem["group"]:
        return
    for q in providers():
        mq = q.memory()
        if q is p or mq["group"] != mem["group"] or not (mem["exclusive"] or mq["exclusive"]):
            continue
        try:
            running = q.is_running()
        except NotImplementedError:
            continue
        if not running:
            continue
        if _in_use(q):
            raise provbase.ProviderError("%s is answering right now. Wait for it to finish, or stop it, then try again." % q.name)
        progress("Freeing memory: stopping %s…" % q.name)
        ok, msg = q.stop()
        if not ok:
            if mem["exclusive"]:
                raise provbase.ProviderError("%s is running something you started, and %s needs the memory to itself. %s" % (q.name, p.name, msg))
            progress("%s is still running something you started; memory may be tight." % q.name)
        else:
            time.sleep(1)                                                  # let the memory come back before the next step measures it


def activate(ref, progress=None, opts=None):
    """Make sure the model is running and return where to talk to it: {base_url, key, model, ctx, provider}.

    Raises provbase.ProviderError with a message a person can act on. Only one activation runs at a time."""
    progress = progress or (lambda t: None)
    p, mid = resolve(ref)
    if p is None:
        raise provbase.ProviderError("No engine is set up to run models.")

    def step(text):
        with _lock:
            STATE["step"] = text
        progress(text)
    with _lock:
        STATE.update(ref=str(ref), provider=p.id, step="Getting ready…", since=time.time())
        try:
            opts = opts or {}
            live, cur = p.loaded(mid)
            ctx = int(opts.get("ctx") or 0)
            if not (live and not opts.get("force") and (not ctx or not cur or cur >= ctx or fixed_ctx(ref)) and not opts.get("exact")):
                make_room(p, step)
            ep = p.ensure(mid, step, opts)
        finally:
            STATE.update(ref=None, provider=None, step="", since=0)
    invalidate()
    return dict(ep or {}, provider=p.id, model=mid)


def deactivate(ref=None):
    """Free a model's memory (or, with no name, everything of the default provider's). Returns (ok, message)."""
    if ref:
        p, mid = resolve(ref)
        if _in_use(p):
            return False, "It is answering right now. Wait for it to finish, or stop it, then try again."
    else:
        p, mid = default_provider(), None
    ok, msg = p.stop(mid)
    invalidate()
    return ok, msg


# ---------------------------------------------------------------- stopping what Zest started once it has been idle
def idle_tick(now=None):
    """One look at every provider: restart its idle clock if it is working, stop what Zest started once it has been idle for the chosen
    time. Only what Zest itself started is ever stopped this way. Returns {provider id: "stopped" | "busy" | "idle"}."""
    now = now or time.time()
    out = {}
    for p in providers():
        try:
            minutes, inst = p.idle_minutes(), p.owned_instance()
        except NotImplementedError:
            continue
        st = _idle.get(p.id)
        if not minutes or inst is None:
            _idle.pop(p.id, None)
            continue
        if not st or st["inst"] != inst:
            st = _idle[p.id] = {"inst": inst, "since": now, "tokens": None}     # a new instance: its clock starts now
        busy, tokens = p.activity()
        if busy is None or busy or tokens != st["tokens"] or any(chk(p.id) for chk in busy_checks):
            st.update(since=now, tokens=tokens)
            out[p.id] = "busy"
        elif now - st["since"] >= minutes * 60:
            p.stop()
            _idle.pop(p.id, None)
            invalidate()
            out[p.id] = "stopped"
        else:
            out[p.id] = "idle"
    return out


def watch(every=30):
    while True:
        try:
            idle_tick()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(every)
