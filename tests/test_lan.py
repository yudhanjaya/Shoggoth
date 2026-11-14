"""Sharing with other Shoggoths on the network (lan.py): the beacon, pairing, pinning, and the gateway's TLS side.
Run: python3 -m pytest tests/test_lan.py -q"""
import json
import os
import secrets
import shutil
import socket
import ssl
import stat
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import gateway  # noqa: E402
import lan  # noqa: E402
import provbase  # noqa: E402
import servers  # noqa: E402
import store  # noqa: E402

FP = "ab" * 32
INFO = {"name": "desk-pc", "profile": "0123456789ab", "version": "5.0.0", "port": 13308, "fp": FP, "models": 3}


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR")}
        os.environ["SHOGGOTH_DATA_DIR"], os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


# ---------------------------------------------------------------- beacon
class Beacon(unittest.TestCase):
    def test_round_trip(self):
        d = lan.decode(lan.encode(INFO), "192.168.1.20")
        self.assertEqual(d, dict(INFO, ip="192.168.1.20"))

    def test_only_the_beacon_fields_go_out(self):
        pkt = json.loads(lan.encode(dict(INFO, models_list=["secret-model"], user="asha")))
        self.assertEqual(set(pkt), {"shoggoth", "name", "profile", "version", "port", "fp", "models"})

    def test_malformed_packets_are_ignored(self):
        good = json.loads(lan.encode(INFO))
        bad = [b"", b"hello", b"\xff\xfe", b"[1,2]", b"null", json.dumps(dict(good, shoggoth=2)).encode(),
               json.dumps(dict(good, fp="xyz")).encode(), json.dumps(dict(good, fp="ab" * 31)).encode(),
               json.dumps(dict(good, port=0)).encode(), json.dumps(dict(good, port=70000)).encode(),
               json.dumps(dict(good, port="13308")).encode(), json.dumps(dict(good, port=True)).encode(),
               json.dumps(dict(good, models=-1)).encode(), json.dumps(dict(good, name="")).encode(),
               json.dumps(dict(good, name=["x"])).encode(), json.dumps(dict(good, profile="../../etc")).encode(),
               json.dumps({k: v for k, v in good.items() if k != "fp"}).encode()]
        for b in bad:
            self.assertIsNone(lan.decode(b, "10.0.0.2"), b)

    def test_oversized_packets_are_ignored(self):
        big = json.dumps(dict(json.loads(lan.encode(INFO)), pad="x" * 1100)).encode()
        self.assertGreater(len(big), lan.MAX_PACKET)
        self.assertIsNone(lan.decode(big, "10.0.0.2"))
        self.assertLessEqual(len(lan.encode(dict(INFO, name="x" * 500))), lan.MAX_PACKET)          # long names are cut, not refused

    def test_control_characters_are_taken_out_of_names(self):
        d = lan.decode(lan.encode(dict(INFO, name="desk\x1b[31m-pc\n")), "10.0.0.2")
        self.assertEqual(d["name"], "desk[31m-pc")


class Listening(Env):
    def test_peers_are_keyed_by_fingerprint(self):
        li = lan.Listener()
        li.feed(lan.encode(INFO), "192.168.1.20")
        li.feed(lan.encode(dict(INFO, name="desk-pc")), "192.168.1.77")         # same Shoggoth, new address
        li.feed(lan.encode(dict(INFO, fp="cd" * 32, name="laptop")), "192.168.1.30")
        got = {p["fp"]: p for p in li.found()}
        self.assertEqual(len(got), 2)
        self.assertEqual(got[FP]["ip"], "192.168.1.77")

    def test_stale_peers_drop_out(self):
        li = lan.Listener()
        li.feed(lan.encode(INFO), "192.168.1.20")
        li.peers[FP]["seen"] -= lan.PEER_TTL + 1
        self.assertEqual(li.found(), [])

    def test_its_own_beacon_is_not_listed(self):
        _, _, fp = lan.ensure_cert()
        li = lan.Listener()
        self.assertIsNone(li.feed(lan.encode(dict(INFO, fp=fp)), "192.168.1.20"))
        self.assertEqual(li.found(), [])

    def test_a_paired_shoggoth_at_a_new_address_is_found_again(self):
        fp = "cd" * 32
        store.update(lambda s: s["servers"].append({"id": "desk", "preset": "shoggoth", "name": "desk-pc", "url": "https://192.168.1.20:13308",
                                                    "key": "shoggoth-peer-x", "fp": fp}))
        li = lan.Listener()
        orig, lan.handshake_ok = lan.handshake_ok, lambda host, port, pin, timeout=3: True
        try:
            li.feed(lan.encode(dict(INFO, fp=fp)), "192.168.1.77")
            for _ in range(100):                                        # the handshake check runs in its own thread
                if servers.get("desk")["url"] == "https://192.168.1.77:13308":
                    break
                time.sleep(0.02)
        finally:
            lan.handshake_ok = orig
        self.assertEqual(servers.get("desk")["url"], "https://192.168.1.77:13308")     # found by fingerprint, not by address

    def test_real_udp(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        li = lan.Listener(port)
        li.start()
        self.assertEqual(li.error, "")
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tx:
                tx.sendto(b"x" * 1500, ("127.0.0.1", port))
                tx.sendto(b"{not json", ("127.0.0.1", port))
                big = json.dumps(dict(json.loads(lan.encode(INFO)), pad="x" * 1000)).encode()
                tx.sendto(big, ("127.0.0.1", port))
                tx.sendto(lan.encode(INFO), ("127.0.0.1", port))
            for _ in range(50):
                if li.found():
                    break
                time.sleep(0.05)
            found = li.found()
            self.assertEqual([p["fp"] for p in found], [FP])
            self.assertEqual(found[0]["ip"], "127.0.0.1")
        finally:
            li.stop()


# ---------------------------------------------------------------- pairing
class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class Pairing(Env):
    def setUp(self):
        super().setUp()
        self.clock = Clock()
        self.p = lan.Pairing(os.path.join(self.tmp, "clients.json"), clock=self.clock)
        self.fp = "ef" * 32

    def ask(self, name="Asha's laptop"):
        cn = secrets.token_hex(32)
        status, d = self.p.start(name, cn, "192.168.1.30")
        self.assertEqual(status, 200, d)
        code = next(w["code"] for w in self.p.waiting() if w["id"] == d["id"])
        return d["id"], cn, d["server_nonce"], code

    def test_round_trip(self):
        rid, cn, sn, code = self.ask()
        self.assertEqual(self.p.waiting()[0]["name"], "Asha's laptop")
        self.assertRegex(code, r"^\d{6}$")
        status, d = self.p.finish(rid, lan.proof(code, cn, sn, self.fp), self.fp)
        self.assertEqual(status, 200, d)
        self.assertTrue(d["key"].startswith(lan.KEY_PREFIX))
        self.assertEqual(self.p.client_for_key(d["key"])["name"], "Asha's laptop")
        self.assertIsNone(self.p.client_for_key(d["key"] + "x"))
        self.assertIsNone(self.p.client_for_key("shoggoth-not-a-peer-key"))
        # The key itself is never written down, only its hash, in a file only this user can read.
        with open(self.p.path()) as f:
            text = f.read()
        self.assertNotIn(d["key"], text)
        self.assertEqual(mode(self.p.path()), 0o600)
        # A new Pairing reading the same file still knows the client.
        self.assertIsNotNone(lan.Pairing(self.p.path()).client_for_key(d["key"]))

    def test_codes_work_once(self):
        rid, cn, sn, code = self.ask()
        pr = lan.proof(code, cn, sn, self.fp)
        self.assertEqual(self.p.finish(rid, pr, self.fp)[0], 200)
        self.assertEqual(self.p.finish(rid, pr, self.fp)[0], 410)

    def test_codes_run_out(self):
        rid, cn, sn, code = self.ask()
        self.clock.t += lan.CODE_SECONDS + 1
        self.assertEqual(self.p.finish(rid, lan.proof(code, cn, sn, self.fp), self.fp)[0], 410)
        self.assertEqual(self.p.waiting(), [])

    def test_the_proof_is_tied_to_the_certificate(self):
        rid, cn, sn, code = self.ask()
        self.assertEqual(self.p.finish(rid, lan.proof(code, cn, sn, "00" * 32), self.fp)[0], 403)

    def test_wrong_code_fails_and_locks_out(self):
        rid, cn, sn, code = self.ask()
        wrong = "%06d" % ((int(code) + 1) % 1_000_000)
        for left in (4, 3, 2, 1):
            status, d = self.p.finish(rid, lan.proof(wrong, cn, sn, self.fp), self.fp)
            self.assertEqual((status, d["tries_left"]), (403, left))
        status, d = self.p.finish(rid, lan.proof(wrong, cn, sn, self.fp), self.fp)
        self.assertEqual(status, 429)
        self.assertEqual(self.p.locked_for(), lan.LOCK_SECONDS)
        # Locked: even the right code is refused, new requests too, and the codes already shown are gone.
        self.assertEqual(self.p.finish(rid, lan.proof(code, cn, sn, self.fp), self.fp)[0], 429)
        self.assertEqual(self.p.start("someone", secrets.token_hex(32))[0], 429)
        self.assertEqual(self.p.waiting(), [])
        self.clock.t += lan.LOCK_SECONDS
        rid, cn, sn, code = self.ask()
        self.assertEqual(self.p.finish(rid, lan.proof(code, cn, sn, self.fp), self.fp)[0], 200)

    def test_the_lockout_is_for_the_whole_server(self):
        """Changing name or request doesn't buy more guesses."""
        for i in range(lan.MAX_TRIES):
            rid, cn, sn, code = self.ask("attacker %d" % i)
            status, _ = self.p.finish(rid, "00" * 32, self.fp)
        self.assertEqual(status, 429)
        self.assertEqual(self.p.start("attacker 99", secrets.token_hex(32))[0], 429)

    def test_unknown_requests_count_as_wrong(self):
        for _ in range(lan.MAX_TRIES - 1):
            self.assertEqual(self.p.finish("nope", "00" * 32, self.fp)[0], 410)
        self.assertEqual(self.p.finish("nope", "00" * 32, self.fp)[0], 429)

    def test_a_right_code_resets_the_count(self):
        rid, cn, sn, code = self.ask()
        for _ in range(lan.MAX_TRIES - 1):
            self.p.finish(rid, "00" * 32, self.fp)
        self.assertEqual(self.p.finish(rid, lan.proof(code, cn, sn, self.fp), self.fp)[0], 200)
        self.assertEqual(self.p.fails, 0)

    def test_bad_requests(self):
        self.assertEqual(self.p.start("", secrets.token_hex(32))[0], 400)
        self.assertEqual(self.p.start("x", "short")[0], 400)
        self.assertEqual(self.p.start("x", "zz" * 32)[0], 400)

    def test_only_a_few_wait_at_once(self):
        for i in range(lan.MAX_PENDING + 2):
            self.p.start("c%d" % i, secrets.token_hex(32))
        self.assertEqual(len(self.p.waiting()), lan.MAX_PENDING)

    def test_deny_and_revoke(self):
        rid, cn, sn, code = self.ask()
        self.assertTrue(self.p.deny(rid))
        self.assertEqual(self.p.finish(rid, lan.proof(code, cn, sn, self.fp), self.fp)[0], 410)
        rid, cn, sn, code = self.ask()
        key = self.p.finish(rid, lan.proof(code, cn, sn, self.fp), self.fp)[1]["key"]
        cid = self.p.clients()[0]["id"]
        self.assertTrue(self.p.revoke(cid))
        self.assertIsNone(self.p.client_for_key(key))
        self.assertFalse(self.p.revoke(cid))
        self.assertEqual(self.p.clients(), [])


# ---------------------------------------------------------------- certificates and pinning
class Certs(Env):
    def test_made_once_and_private(self):
        c, k, fp = lan.ensure_cert()
        self.assertRegex(fp, r"^[0-9a-f]{64}$")
        self.assertEqual(mode(k), 0o600)
        self.assertEqual(mode(os.path.dirname(k)), 0o700)
        self.assertEqual(lan.ensure_cert()[2], fp)
        self.assertEqual(lan.own_fp(), fp)
        with open(c) as f:
            self.assertIn("BEGIN CERTIFICATE", f.read())

    def test_settings_file_is_private(self):
        store.update(lambda s: s.__setitem__("x", 1))
        self.assertEqual(mode(os.path.join(store.config_dir(), "settings.json")), 0o600)


def _second_cert(where):
    """Another certificate, from another data folder (another Shoggoth)."""
    old = os.environ["SHOGGOTH_DATA_DIR"]
    os.environ["SHOGGOTH_DATA_DIR"] = where
    try:
        return lan.ensure_cert()
    finally:
        os.environ["SHOGGOTH_DATA_DIR"] = old


class LoopbackTLS(Env):
    """The gateway's network side for real, on 127.0.0.1 with TLS."""

    def setUp(self):
        super().setUp()
        self.fp = lan.ensure_cert()[2]
        self.other = _second_cert(os.path.join(self.tmp, "other"))
        self.assertNotEqual(self.fp, self.other[2])
        self.saved = dict(gateway.CONFIG)
        gateway.CONFIG.update(key="shoggoth-apikey-xyz", capture=False)
        self.srv = gateway._Server(("127.0.0.1", 0), gateway.GWHandler)
        self.srv.tls = lan.server_context()
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        lan.SHARING["on"] = True
        lan.PAIRING = lan.Pairing()
        self.base = "https://127.0.0.1:%d" % self.port

    def tearDown(self):
        lan.SHARING["on"] = False
        self.srv.shutdown()
        self.srv.server_close()
        gateway.CONFIG.clear()
        gateway.CONFIG.update(self.saved)
        lan.LISTENER.peers.clear()
        super().tearDown()

    def test_pin_accepts_the_right_cert_and_rejects_another(self):
        status, d = provbase.http_json(self.base + "/v1", pin=self.fp)
        self.assertEqual(status, 200)
        self.assertTrue(d.get("zest_gateway"))
        self.assertEqual(provbase.http_json(self.base + "/v1", pin=self.other[2]), (0, {}))
        c = provbase.PinnedHTTPSConnection("127.0.0.1", self.port, pin=self.other[2])
        self.assertRaises(provbase.PinMismatch, c.connect)
        self.assertEqual(provbase.http_json("http://127.0.0.1:%d/v1" % self.port, pin=self.fp), (0, {}))   # a pin needs https

    def test_a_server_with_the_other_cert_is_rejected(self):
        """Two real servers, two certificates: each pin opens only its own."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.other[0], self.other[1])
        srv2 = gateway._Server(("127.0.0.1", 0), gateway.GWHandler)
        srv2.tls = ctx
        threading.Thread(target=srv2.serve_forever, daemon=True).start()
        try:
            b2 = "https://127.0.0.1:%d/v1" % srv2.server_address[1]
            self.assertEqual(provbase.http_json(b2, pin=self.other[2])[0], 200)
            self.assertEqual(provbase.http_json(b2, pin=self.fp), (0, {}))
        finally:
            srv2.shutdown()
            srv2.server_close()

    def test_loopback_stays_plain_http(self):
        with urllib.request.urlopen("http://127.0.0.1:%d/v1" % self.port, timeout=5) as r:
            self.assertTrue(json.loads(r.read())["zest_gateway"])

    def test_network_plain_http_is_refused_with_a_reason(self):
        from unittest import mock
        with mock.patch.object(lan, "is_loopback", return_value=False):          # as if from another device
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen("http://127.0.0.1:%d/v1/models" % self.port, timeout=5)
            self.assertEqual(e.exception.code, 400)
            self.assertIn("https://", json.loads(e.exception.read())["error"]["message"])
            # and the encrypted side still answers
            status, d = provbase.http_json(self.base + "/v1", pin=self.fp)
            self.assertEqual(status, 200)

    def test_pairing_is_refused_without_tls(self):
        req = urllib.request.Request("http://127.0.0.1:%d/shoggoth/pair" % self.port, method="POST",
                                     data=json.dumps({"name": "x", "client_nonce": secrets.token_hex(32)}).encode())
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(e.exception.code, 403)
        self.assertEqual(lan.PAIRING.waiting(), [])

    def test_pair_end_to_end(self):
        peer = {"name": "desk-pc", "profile": "p", "version": "5.0.0", "port": self.port, "fp": self.fp, "models": 1,
                "ip": "127.0.0.1", "seen": time.time()}
        lan.LISTENER.peers[self.fp] = peer                     # as if its beacon had arrived (our own would be filtered out)
        code, d = lan.handle("POST", "/ui/lan/pair", {"fp": self.fp})
        self.assertEqual(code, 200, d)
        waiting = lan.PAIRING.waiting()
        self.assertEqual(len(waiting), 1)
        right = waiting[0]["code"]
        wrong = "%06d" % ((int(right) + 1) % 1_000_000)
        code, e = lan.handle("POST", "/ui/lan/code", {"session": d["session"], "code": wrong})
        self.assertEqual(code, 400)
        self.assertIn("isn't right", e["error"])
        code, e = lan.handle("POST", "/ui/lan/code", {"session": d["session"], "code": right})
        self.assertEqual(code, 200, e)
        saved = servers.get(e["server"]["id"])
        self.assertEqual(saved["preset"], "shoggoth")
        self.assertEqual(saved["url"], "https://127.0.0.1:%d" % self.port)
        self.assertEqual(saved["fp"], self.fp)
        self.assertTrue(saved["key"].startswith(lan.KEY_PREFIX))
        self.assertEqual(mode(os.path.join(store.config_dir(), "settings.json")), 0o600)
        self.assertEqual(lan.handle("GET", "/ui/lan/found", {})[1]["found"][0]["connected"], saved["id"])

        # The gateway takes the paired key over TLS (the request then goes on to an engine, which isn't running here: not a 401) ...
        def get(key, base=self.base, pin=self.fp):
            if pin:
                c = provbase.PinnedHTTPSConnection("127.0.0.1", self.port, pin=pin, timeout=10)
            else:
                import http.client
                c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            c.request("GET", "/v1/health", headers={"Authorization": "Bearer " + key})
            st = c.getresponse().status
            c.close()
            return st
        self.assertNotEqual(get(saved["key"]), 401)
        self.assertEqual(get("shoggoth-peer-made-up"), 401)
        self.assertNotEqual(get("shoggoth-apikey-xyz"), 401)              # the gateway's own key still works over TLS
        # ... and stops taking it the moment it is removed.
        cid = lan.PAIRING.clients()[0]["id"]
        self.assertEqual(lan.handle("POST", "/ui/lan/revoke", {"id": cid})[0], 200)
        self.assertEqual(get(saved["key"]), 401)

    def test_pairing_with_a_lying_beacon_sends_nothing(self):
        """A beacon that names our address but another certificate: the handshake fails the pin before the request goes out."""
        lan.LISTENER.peers[self.other[2]] = {"name": "fake", "profile": "", "version": "", "port": self.port, "fp": self.other[2],
                                             "models": 0, "ip": "127.0.0.1", "seen": time.time()}
        code, d = lan.handle("POST", "/ui/lan/pair", {"fp": self.other[2]})
        self.assertEqual(code, 400)
        self.assertIn("different certificate", d["error"])
        self.assertEqual(lan.PAIRING.waiting(), [])

    def test_lockout_over_the_wire(self):
        for _ in range(lan.MAX_TRIES):
            c = provbase.PinnedHTTPSConnection("127.0.0.1", self.port, pin=self.fp)
            c.request("POST", "/shoggoth/pair/finish", body=json.dumps({"id": "x", "proof": "00" * 32}),
                      headers={"Content-Type": "application/json"})
            st = c.getresponse().status
            c.close()
        self.assertEqual(st, 429)


class Definition(unittest.TestCase):
    def test_the_shoggoth_preset_exists(self):
        import engines
        d = engines.by_id("shoggoth")
        self.assertEqual((d["category"], d["key"], d["ports"], d["match"], d["url"]), ("local", "required", [], [], ""))


if __name__ == "__main__":
    unittest.main()
