"""Sharing models with other Shoggoths on the local network, and using theirs.

Server side: when "Share my models with other Shoggoths on this network" is on, the gateway listens on every address and serves the
network over TLS with a self-signed certificate made once (this computer keeps it, mode 600). A beacon says every 2 s, by UDP broadcast
to port 13309, that this Shoggoth is here: its name, profile, version, port, the sha256 of its certificate (`fp`) and how many models
it has. No model names and nothing about the person.

Client side: a listener collects those beacons. Connecting to a found Shoggoth pairs with it:

  1. The client opens TLS to the address the beacon gave and checks the certificate hashes to the beacon's `fp` (trust on first use;
     every later connection is pinned to that same certificate). It sends its name and a random client nonce.
  2. The server answers with a random server nonce and shows a 6-digit code and the client's name on its own screen.
  3. The person types the code on the client, which sends HMAC-SHA256(code, client_nonce || server_nonce || fp).
  4. The server checks it (constant time) and returns a key made for that client alone. The client saves the server as a connected
     server (preset `shoggoth`, https URL, the key and the pinned fp).

Codes last 2 minutes and work once. Five wrong codes, from anyone, lock pairing for 60 s and throw away every waiting code. The
server keeps only the sha256 of each client's key (mode 600); removing a client deletes it, and the gateway stops accepting it at once.

Everything in the pairing exchange, the nonces and the HMAC included, goes over the pinned TLS connection, never in the clear: a
6-digit code is easy to recover by trying all million values against a captured HMAC, so the HMAC must never be visible to anyone on
the network. Standard library only.
"""
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import struct
import subprocess
import threading
import time
import urllib.parse

import provbase
import store

BEACON_PORT = 13309
INTERVAL = 2.0
MAX_PACKET = 1024
PEER_TTL = 10.0                      # a Shoggoth not heard from for this long is no longer listed
MAX_PEERS = 64
CODE_SECONDS = 120
MAX_TRIES = 5
LOCK_SECONDS = 60
MAX_PENDING = 3
KEY_PREFIX = "shoggoth-peer-"
VERSION = "dev"                      # server.py sets this to its own version
_FP_RE = re.compile(r"^[0-9a-f]{64}$")
_HEX32 = re.compile(r"^[0-9a-f]{64}$")      # a 32-byte nonce, as hex


class LanError(Exception):
    """Something a person can act on, in plain words."""


# ---------------------------------------------------------------- where things live
def _dir():
    d = os.path.join(store.data_dir(), "lan")
    os.makedirs(d, mode=0o700, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def _write_private(path, text):
    """Write a file only this user can read, replacing it in one step."""
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)                       # in case an old .tmp was left with looser rights
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def profile_id():
    """The same id server.profile_id() gives: made from the data folder, so two profiles on one computer are two Shoggoths."""
    return hashlib.sha256(os.path.abspath(store.data_dir()).encode()).hexdigest()[:12]


def my_name():
    g = store.load().get("gateway") or {}
    return clean_name(g.get("lan_name") or "") or clean_name(socket.gethostname()) or "Shoggoth"


def clean_name(v, limit=48):
    v = re.sub(r"[\x00-\x1f\x7f]", "", str(v or "")).strip()
    return v[:limit]


def short_fp(fp):
    """What people compare by eye on both screens: the first 8 hex characters, as two groups."""
    fp = str(fp or "").upper()
    return "%s %s" % (fp[:4], fp[4:8]) if len(fp) >= 8 else ""


# ---------------------------------------------------------------- the certificate
_cert_lock = threading.Lock()


def cert_paths():
    d = _dir()
    return os.path.join(d, "cert.pem"), os.path.join(d, "key.pem")


def _fp_of(cert_path):
    with open(cert_path) as f:
        return provbase.cert_fp(ssl.PEM_cert_to_DER_cert(f.read()))


def own_fp():
    """This Shoggoth's certificate fingerprint, or "" when it has none yet (sharing was never turned on)."""
    c, k = cert_paths()
    try:
        return _fp_of(c) if os.path.exists(k) else ""
    except (OSError, ValueError):
        return ""


def ensure_cert():
    """(cert_path, key_path, fp), making the certificate the first time: P-256, self-signed, ten years. The key file is created 600
    before openssl writes to it, so it is never readable by anyone else, even for a moment."""
    with _cert_lock:
        c, k = cert_paths()
        if os.path.exists(c) and os.path.exists(k):
            try:
                os.chmod(k, 0o600)
                return c, k, _fp_of(c)
            except (OSError, ValueError):
                pass                                   # unreadable or damaged: make a new one
        for p in (c, k):
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass
        os.close(os.open(k, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        cmd = ["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-sha256", "-nodes",
               "-days", "3650", "-subj", "/CN=Shoggoth", "-keyout", k, "-out", c]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise LanError("Couldn't run openssl to make this computer's certificate (%s)." % e)
        if r.returncode != 0 or not os.path.getsize(k):
            for p in (c, k):
                try:
                    os.unlink(p)
                except FileNotFoundError:
                    pass
            raise LanError("openssl couldn't make this computer's certificate: " + (r.stderr.strip().splitlines() or ["no reason given"])[-1])
        os.chmod(k, 0o600)
        return c, k, _fp_of(c)


def server_context():
    """The TLS settings for the network side of the gateway."""
    c, k, _ = ensure_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(c, k)
    return ctx


def is_loopback(ip):
    try:
        a = ipaddress.ip_address(str(ip).split("%")[0])
        return a.is_loopback or (a.version == 6 and a.ipv4_mapped is not None and a.ipv4_mapped.is_loopback)
    except ValueError:
        return False


# ---------------------------------------------------------------- the beacon
def encode(info):
    """A beacon packet from {name, profile, version, port, fp, models}. Only these fields, never anything else."""
    pkt = {"shoggoth": 1, "name": clean_name(info.get("name"), 64), "profile": str(info.get("profile") or "")[:32],
           "version": str(info.get("version") or "")[:32], "port": int(info["port"]), "fp": str(info["fp"]).lower(),
           "models": max(0, int(info.get("models") or 0))}
    data = json.dumps(pkt, separators=(",", ":")).encode()
    if len(data) > MAX_PACKET:
        raise ValueError("beacon too large")
    return data


def decode(data, ip=""):
    """The beacon in a packet, or None for anything that isn't a well-formed Shoggoth beacon (too big, not JSON, wrong fields)."""
    if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_PACKET:
        return None
    try:
        o = json.loads(bytes(data).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(o, dict) or o.get("shoggoth") != 1:
        return None
    name, profile, version, port, fp, models = (o.get(k) for k in ("name", "profile", "version", "port", "fp", "models"))
    if not (isinstance(name, str) and isinstance(profile, str) and isinstance(version, str) and isinstance(fp, str)):
        return None
    if type(port) is not int or not 0 < port < 65536 or type(models) is not int or not 0 <= models <= 100000:
        return None
    name, fp = clean_name(name, 64), fp.lower()
    if not name or not _FP_RE.match(fp) or not re.fullmatch(r"[A-Za-z0-9._-]{0,32}", profile) or not re.fullmatch(r"[A-Za-z0-9.+_-]{0,32}", version):
        return None
    return {"name": name, "profile": profile, "version": version, "port": port, "fp": fp, "models": models, "ip": str(ip)}


def broadcast_addresses():
    """255.255.255.255 and each network interface's own broadcast address (Linux; others get the first only)."""
    out = ["255.255.255.255"]
    try:
        import fcntl
        names = [n for _, n in socket.if_nameindex()]
    except (ImportError, OSError):
        return out
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
        for n in names:
            try:
                r = fcntl.ioctl(sk.fileno(), 0x8919, struct.pack("256s", n.encode()[:15]))      # SIOCGIFBRDADDR
            except OSError:
                continue
            ip = socket.inet_ntoa(r[20:24])
            if ip not in out and ip != "0.0.0.0" and not ip.startswith("127."):
                out.append(ip)
    return out


def _model_count():
    try:
        import router
        return len(router.chat_models())
    except Exception:  # noqa: BLE001 - the beacon must keep going even if listing models fails
        return 0


class Beacon:
    """Says "Shoggoth is here" every 2 s while sharing is on."""

    def __init__(self):
        self._stop = None
        self.port = 0
        self._models = (0.0, 0)

    @property
    def running(self):
        return self._stop is not None

    def info(self):
        t, n = self._models
        if time.time() - t > 15:
            n = _model_count()
            self._models = (time.time(), n)
        return {"name": my_name(), "profile": profile_id(), "version": VERSION, "port": self.port, "fp": ensure_cert()[2], "models": n}

    def start(self, port):
        self.stop()
        self.port = int(port)
        ev = self._stop = threading.Event()

        def run():
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
                sk.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                while not ev.is_set():
                    try:
                        data = encode(self.info())
                        for dst in broadcast_addresses():
                            try:
                                sk.sendto(data, (dst, BEACON_PORT))
                            except OSError:
                                pass
                    except Exception:  # noqa: BLE001 - try again next time
                        pass
                    ev.wait(INTERVAL)
        threading.Thread(target=run, daemon=True, name="lan-beacon").start()

    def stop(self):
        if self._stop:
            self._stop.set()
            self._stop = None


class Listener:
    """Collects beacons from other Shoggoths, keyed by certificate fingerprint (so an address change finds the same one again)."""

    def __init__(self, port=BEACON_PORT):
        self.port = port
        self.peers = {}
        self.lock = threading.Lock()
        self.sock = None
        self.error = ""
        self._checked = {}

    def start(self):
        if self.sock:
            return
        try:
            sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)        # every Shoggoth on this computer hears every beacon
            sk.bind(("", self.port))
        except OSError as e:
            self.error = "Can't listen for other Shoggoths on port %d (%s)." % (self.port, e.strerror or e)
            return
        self.sock, self.error = sk, ""
        threading.Thread(target=self._run, daemon=True, name="lan-listen").start()

    def stop(self):
        sk, self.sock = self.sock, None
        if sk:
            try:
                sk.close()
            except OSError:
                pass

    def _run(self):
        sk = self.sock
        while self.sock is sk:
            try:
                data, addr = sk.recvfrom(MAX_PACKET + 1)       # one byte more than allowed, so an oversized packet is seen as such
            except OSError:
                return
            self.feed(data, addr[0])

    def feed(self, data, ip):
        """Take one packet. Returns the beacon it held, or None when it was ignored."""
        d = decode(data, ip)
        if not d or d["fp"] == own_fp():
            return None
        with self.lock:
            d["seen"] = time.time()
            self.peers[d["fp"]] = d
            if len(self.peers) > MAX_PEERS:
                for fp in sorted(self.peers, key=lambda f: self.peers[f]["seen"])[:len(self.peers) - MAX_PEERS]:
                    del self.peers[fp]
        self._follow(d)
        return d

    def found(self):
        now = time.time()
        with self.lock:
            for fp in [f for f, p in self.peers.items() if now - p["seen"] > PEER_TTL]:
                del self.peers[fp]
            return [dict(p) for p in sorted(self.peers.values(), key=lambda p: p["name"].lower())]

    def get(self, fp):
        return next((p for p in self.found() if p["fp"] == str(fp or "").lower()), None)

    def _follow(self, d):
        """A paired Shoggoth announcing itself at a new address: check the address really has its certificate, then update the saved URL.
        A beacon alone can't move it (anyone can send one); the TLS handshake against the pinned certificate decides."""
        last = self._checked.get(d["fp"], 0)
        if time.time() - last < 30:
            return
        url = "https://%s:%d" % (d["ip"], d["port"])
        mine = [s for s in (store.load().get("servers") or []) if isinstance(s, dict) and s.get("preset") == "shoggoth" and s.get("fp") == d["fp"]]
        if not mine or all(s.get("url") == url for s in mine):
            return
        self._checked[d["fp"]] = time.time()

        def check():
            if not handshake_ok(d["ip"], d["port"], d["fp"]):
                return

            def put(s):
                for h in s.get("servers") or []:
                    if isinstance(h, dict) and h.get("preset") == "shoggoth" and h.get("fp") == d["fp"]:
                        h["url"] = url
            store.update(put)
        threading.Thread(target=check, daemon=True).start()


def handshake_ok(host, port, fp, timeout=3):
    """True when host:port completes TLS with the certificate `fp`. Sends nothing beyond the handshake."""
    try:
        c = provbase.PinnedHTTPSConnection(host, port, pin=fp, timeout=timeout)
        c.connect()
        c.close()
        return True
    except (OSError, ValueError, ssl.SSLError):
        return False


BEACON = Beacon()
LISTENER = Listener()


# ---------------------------------------------------------------- pairing, server side
def proof(code, client_nonce, server_nonce, fp):
    """HMAC-SHA256 keyed by the 6-digit code over client_nonce || server_nonce || fp (all as bytes). Including the fingerprint the client
    saw ties the proof to the certificate the client pinned."""
    msg = bytes.fromhex(client_nonce) + bytes.fromhex(server_nonce) + bytes.fromhex(fp)
    return hmac.new(str(code).encode(), msg, hashlib.sha256).hexdigest()


class Pairing:
    """Pairing requests waiting for their code, the wrong-code count, and the paired clients (only the sha256 of each key is kept)."""

    def __init__(self, path=None, clock=time.time):
        self._path = path
        self.clock = clock
        self.lock = threading.Lock()
        self.pending = {}
        self.fails = 0
        self.locked_until = 0.0
        self._clients = None

    # -- paired clients
    def path(self):
        return self._path or os.path.join(_dir(), "clients.json")

    def _load(self):
        if self._clients is None:
            try:
                with open(self.path()) as f:
                    d = json.load(f)
                self._clients = [c for c in d if isinstance(c, dict) and c.get("id") and _FP_RE.match(str(c.get("key_sha256", "")))]
            except (OSError, ValueError, TypeError):
                self._clients = []
        return self._clients

    def _save(self):
        _write_private(self.path(), json.dumps(self._clients, indent=1))

    def clients(self):
        with self.lock:
            return [{k: c.get(k) for k in ("id", "name", "created", "last_seen", "ip")} for c in self._load()]

    def revoke(self, cid):
        with self.lock:
            before = len(self._load())
            self._clients = [c for c in self._clients if c["id"] != cid]
            if len(self._clients) == before:
                return False
            self._save()
            return True

    def client_for_key(self, key, ip=""):
        """The paired client this key belongs to, or None. Every stored hash is compared, in constant time."""
        key = str(key or "")
        if not key.startswith(KEY_PREFIX):
            return None
        h = hashlib.sha256(key.encode()).hexdigest()
        with self.lock:
            hit = None
            for c in self._load():
                if hmac.compare_digest(h, c["key_sha256"]):
                    hit = c
            if hit:
                hit["last_seen"], hit["ip"] = int(self.clock()), str(ip)
                return {"id": hit["id"], "name": hit["name"]}
        return None

    # -- waiting requests
    def locked_for(self):
        return max(0, int(self.locked_until - self.clock() + 0.999))

    def waiting(self):
        now = self.clock()
        with self.lock:
            self._expire(now)
            return [{"id": i, "name": p["name"], "ip": p["ip"], "code": p["code"], "expires_in": int(p["expires"] - now)}
                    for i, p in sorted(self.pending.items(), key=lambda kv: kv[1]["expires"])]

    def deny(self, rid):
        with self.lock:
            return self.pending.pop(rid, None) is not None

    def _expire(self, now):
        for i in [i for i, p in self.pending.items() if p["expires"] <= now]:
            del self.pending[i]

    def start(self, name, client_nonce, ip=""):
        """A client asks to pair. Returns (status, reply)."""
        name = clean_name(name)
        cn = str(client_nonce or "").lower()
        if not name or not _HEX32.match(cn):
            return 400, {"error": "A pairing request needs a name and a 32-byte nonce."}
        now = self.clock()
        with self.lock:
            if now < self.locked_until:
                return 429, {"error": "Too many wrong codes. Try again in %d s." % self.locked_for(), "retry_in": self.locked_for()}
            self._expire(now)
            while len(self.pending) >= MAX_PENDING:
                del self.pending[min(self.pending, key=lambda i: self.pending[i]["expires"])]
            rid, sn = secrets.token_hex(16), secrets.token_hex(32)
            self.pending[rid] = {"name": name, "ip": str(ip), "client_nonce": cn, "server_nonce": sn,
                                 "code": "%06d" % secrets.randbelow(1_000_000), "expires": now + CODE_SECONDS}
        return 200, {"id": rid, "server_nonce": sn, "expires_in": CODE_SECONDS}

    def _fail(self, now):
        self.fails += 1
        if self.fails >= MAX_TRIES:
            self.fails, self.locked_until = 0, now + LOCK_SECONDS
            self.pending.clear()                       # every code shown so far is spent; a new request gets a new one

    def finish(self, rid, given, fp):
        """The client sends its proof. Returns (status, reply); on success the reply holds the client's new key, shown this once."""
        now = self.clock()
        given = str(given or "").lower()
        with self.lock:
            if now < self.locked_until:
                return 429, {"error": "Too many wrong codes. Try again in %d s." % self.locked_for(), "retry_in": self.locked_for()}
            self._expire(now)
            p = self.pending.get(str(rid or ""))
            want = proof(p["code"], p["client_nonce"], p["server_nonce"], fp) if p else "0" * 64
            if not p or not hmac.compare_digest(want, given):
                self._fail(now)
                if now < self.locked_until:
                    return 429, {"error": "Too many wrong codes. Try again in %d s." % LOCK_SECONDS, "retry_in": LOCK_SECONDS}
                if not p:
                    return 410, {"error": "That pairing request has run out or was turned down. Start again."}
                return 403, {"error": "That code isn't right.", "tries_left": MAX_TRIES - self.fails}
            del self.pending[rid]                       # single use
            self.fails = 0
            key = KEY_PREFIX + secrets.token_urlsafe(32)
            c = {"id": secrets.token_hex(6), "name": p["name"], "key_sha256": hashlib.sha256(key.encode()).hexdigest(),
                 "created": int(now), "last_seen": None, "ip": p["ip"]}
            self._load().append(c)
            self._save()
        return 200, {"key": key, "client_id": c["id"]}


PAIRING = Pairing()
SHARING = {"on": False}           # set by the gateway when its network side is listening


def serve_peer(method, path, body, ip, tls):
    """The pairing endpoints the gateway serves to the network. Only over TLS, and only while sharing is on. Returns (status, obj)."""
    if not SHARING["on"]:
        return 404, {"error": "This Shoggoth isn't sharing its models."}
    if not tls:
        return 403, {"error": "Pairing only works over the encrypted connection (https)."}
    if method != "POST":
        return 405, {"error": "Use POST."}
    try:
        o = json.loads(body or b"{}")
    except ValueError:
        o = None
    if not isinstance(o, dict):
        return 400, {"error": "Send a JSON object."}
    if path == "/shoggoth/pair":
        return PAIRING.start(o.get("name"), o.get("client_nonce"), ip)
    if path == "/shoggoth/pair/finish":
        return PAIRING.finish(o.get("id"), o.get("proof"), own_fp())
    return 404, {"error": "not found"}


# ---------------------------------------------------------------- pairing, client side
SESSIONS = {}
_slock = threading.Lock()


def _peer_post(host, port, fp, path, obj, timeout=10):
    """POST JSON to another Shoggoth over TLS pinned to `fp`. Returns (status, json); raises LanError when it can't be reached or
    shows a different certificate (in which case nothing was sent)."""
    try:
        c = provbase.PinnedHTTPSConnection(host, port, pin=fp, timeout=timeout)
        try:
            c.request("POST", path, body=json.dumps(obj).encode(), headers={"Content-Type": "application/json"})
            r = c.getresponse()
            raw = r.read(65536)
        finally:
            c.close()
    except provbase.PinMismatch:
        raise LanError("That address answered with a different certificate from the one its beacon announced, so Shoggoth sent nothing.")
    except (OSError, ssl.SSLError, http.client.HTTPException) as e:
        raise LanError("Couldn't reach that Shoggoth (%s)." % e)
    try:
        d = json.loads(raw or b"{}")
    except ValueError:
        d = {}
    return r.status, d if isinstance(d, dict) else {}


def pair_start(fp):
    """Ask a found Shoggoth to pair. Its screen then shows a code. Returns {session, server, fp_short, expires_in}."""
    peer = LISTENER.get(fp)
    if not peer:
        raise ValueError("That Shoggoth isn't announcing itself any more. Check it's running and sharing.")
    cn = secrets.token_hex(32)
    status, d = _peer_post(peer["ip"], peer["port"], peer["fp"], "/shoggoth/pair", {"name": my_name(), "client_nonce": cn})
    if status != 200 or not isinstance(d.get("id"), str) or not _HEX32.match(str(d.get("server_nonce", ""))):
        raise ValueError(d.get("error") or "%s didn't accept the pairing request." % peer["name"])
    sid = secrets.token_hex(8)
    with _slock:
        now = time.time()
        for k in [k for k, v in SESSIONS.items() if v["expires"] < now]:
            del SESSIONS[k]
        SESSIONS[sid] = {"peer": peer, "client_nonce": cn, "server_nonce": d["server_nonce"], "id": d["id"],
                         "expires": now + min(int(d.get("expires_in") or CODE_SECONDS), CODE_SECONDS)}
    return {"session": sid, "server": peer["name"], "fp_short": short_fp(peer["fp"]), "expires_in": CODE_SECONDS}


def pair_finish(sid, code):
    """Send the code the person typed. On success the server is saved as a connected server. Returns {ok, server: {id, name, url}}."""
    code = re.sub(r"\s", "", str(code or ""))
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("The code is 6 digits.")
    with _slock:
        s = SESSIONS.get(str(sid or ""))
    if not s or s["expires"] < time.time():
        raise ValueError("That pairing has run out. Start again.")
    peer = s["peer"]
    status, d = _peer_post(peer["ip"], peer["port"], peer["fp"], "/shoggoth/pair/finish",
                           {"id": s["id"], "proof": proof(code, s["client_nonce"], s["server_nonce"], peer["fp"])})
    if status != 200 or not str(d.get("key", "")).startswith(KEY_PREFIX):
        if status != 403:                                   # a wrong code may be typed again; anything else ends this pairing
            with _slock:
                SESSIONS.pop(sid, None)
        raise ValueError(d.get("error") or "%s didn't accept the code." % peer["name"])
    with _slock:
        SESSIONS.pop(sid, None)
    return {"ok": True, "server": save_server(peer, d["key"])}


def save_server(peer, key):
    """Save a paired Shoggoth with the other connected servers (settings.json, mode 600): https URL, its key, its pinned fingerprint.
    Pairing again with the same Shoggoth updates the saved one instead of adding a second."""
    import servers
    pre = next((p for p in servers.presets() if p["id"] == "shoggoth"), None)
    if not pre:
        raise ValueError("The `shoggoth` engine definition is missing or switched off.")
    url = servers.normalise("https://%s:%d" % (peer["ip"], peer["port"]))
    out = []

    def put(s):
        have = s.setdefault("servers", [])
        for h in have:
            if isinstance(h, dict) and h.get("preset") == "shoggoth" and h.get("fp") == peer["fp"]:
                h.update(url=url, key=key)
                out.append(h)
                return
        s["servers"] = [h for h in have if not (isinstance(h, dict) and h.get("url")
                                                and servers._canon(servers.normalise(h["url"])) == servers._canon(url))]
        e = servers._append(s, pre, url, key, peer["name"])
        e["fp"] = peer["fp"]
        out.append(e)
    store.update(put)
    return {"id": out[0]["id"], "name": out[0]["name"], "url": out[0]["url"]}


# ---------------------------------------------------------------- the page's /ui/lan/* routes
def status():
    import gateway
    g = store.load().get("gateway") or {}
    fp = own_fp()
    port = gateway.GATEWAY.srv.server_address[1] if gateway.GATEWAY.srv else g.get("port")
    ip = gateway.lan_ip()
    return {"sharing": bool(g.get("lan")), "running": SHARING["on"] and BEACON.running, "error": gateway.GATEWAY.error,
            "name": my_name(), "fp": fp, "fp_short": short_fp(fp), "port": port,
            "url": "https://%s:%s/v1" % (ip, port) if ip and SHARING["on"] else None,
            "waiting": PAIRING.waiting(), "clients": PAIRING.clients(), "locked_for": PAIRING.locked_for()}


def set_sharing(on, name=None):
    import gateway
    s = store.load()
    s["gateway"]["lan"] = bool(on)
    if name is not None:
        s["gateway"]["lan_name"] = clean_name(name)
    store.save(s)
    gateway.GATEWAY.apply(s)


def found():
    import servers
    LISTENER.start()
    paired = {s.get("fp"): s["id"] for s in servers.all() if s.get("preset") == "shoggoth" and s.get("fp")}
    return [{"fp": p["fp"], "fp_short": short_fp(p["fp"]), "name": p["name"], "profile": p["profile"], "version": p["version"],
             "ip": p["ip"], "port": p["port"], "models": p["models"], "seen_ago": int(time.time() - p["seen"]),
             "connected": paired.get(p["fp"])} for p in LISTENER.found()]


def handle(method, path, body):
    """Dispatch /ui/lan/*. Returns (status, obj)."""
    body = body if isinstance(body, dict) else {}
    try:
        if method == "GET" and path == "/ui/lan/status":
            return 200, status()
        if method == "GET" and path == "/ui/lan/found":
            return 200, {"found": found(), "error": LISTENER.error}
        if method == "GET" and path == "/ui/lan/clients":
            return 200, {"clients": PAIRING.clients()}
        if method == "POST" and path == "/ui/lan/share":
            set_sharing(body.get("on"), body.get("name"))
            return 200, status()
        if method == "POST" and path == "/ui/lan/pair":
            LISTENER.start()
            return 200, pair_start(body.get("fp"))
        if method == "POST" and path == "/ui/lan/code":
            return 200, pair_finish(body.get("session"), body.get("code"))
        if method == "POST" and path == "/ui/lan/deny":
            return 200, {"ok": PAIRING.deny(str(body.get("id") or ""))}
        if method == "POST" and path == "/ui/lan/revoke":
            ok = PAIRING.revoke(str(body.get("id") or ""))
            return (200 if ok else 404), {"ok": ok, "clients": PAIRING.clients()}
        return 404, {"error": "not found"}
    except (ValueError, LanError) as e:
        return 400, {"ok": False, "error": str(e)}
