"""Hugging Face file list, download and search, against a local fake server (never the real network).
Run: python3 -m unittest discover tests"""
import hashlib
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import hf  # noqa: E402


def blob(n, seed):
    return bytes((i * 7 + seed) % 251 for i in range(n))


REPO = "acme/Tiny-GGUF"
DATA = {
    "Tiny-Q4_K_M.gguf": blob(300_000, 1),
    "Tiny-Q8_0-00001-of-00002.gguf": blob(120_000, 2),
    "Tiny-Q8_0-00002-of-00002.gguf": blob(90_000, 3),
    "mmproj-Tiny-F16.gguf": blob(50_000, 4),
    "README.md": b"hello",
}


class Fake(http.server.BaseHTTPRequestHandler):
    log = []
    drop_after = None          # cut the next CDN transfer after this many bytes (once)
    ignore_range = False
    search_hits = 0
    corrupt = set()
    branch = "main"            # the repo's default branch
    no_lfs = set()             # files listed without an lfs entry (no sha256)
    huge = False               # answer the tree request with a reply bigger than hf.MAX_JSON

    def log_message(self, *a):
        pass

    def reply(self, code, body, ctype="application/json", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        Fake.log.append((self.path, self.headers.get("Range"), self.headers.get("User-Agent")))
        path = self.path
        if path == "/api/models/" + REPO:
            return self.reply(200, json.dumps({"id": REPO, "default_branch": Fake.branch}).encode())
        if path.startswith("/api/models/%s/tree/%s" % (REPO, Fake.branch)):
            if Fake.huge:
                return self.reply(200, b"[" + b"0," * hf.MAX_JSON + b"0]")
            tree = [{"type": "directory", "path": "sub"}]
            for name, b in DATA.items():
                item = {"type": "file", "path": name, "size": len(b)}
                if name.endswith(".gguf") and name not in Fake.no_lfs:
                    item["lfs"] = {"oid": hashlib.sha256(b).hexdigest(), "size": len(b)}
                    if name in Fake.corrupt:
                        item["lfs"]["oid"] = "0" * 64
                tree.append(item)
            return self.reply(200, json.dumps(tree).encode())
        if path.startswith("/api/models/nobody/"):
            return self.reply(404, b"{}")
        if path.startswith("/api/models?"):
            Fake.search_hits += 1
            def m(i, dl, tags=("gguf",)):
                return {"id": i, "downloads": dl, "likes": 1, "tags": list(tags)}
            return self.reply(200, json.dumps([
                m("random/Big-GGUF", 9000), m("bartowski/Mid-GGUF", 50), m("unsloth/Low-GGUF", 10),
                m("nogguf/Thing", 99999, ("transformers",)), m("lmstudio-community/High-GGUF", 500)]).encode())
        if path.startswith("/%s/resolve/%s/" % (REPO, Fake.branch)):
            name = path.split("/resolve/%s/" % Fake.branch, 1)[1]
            if name not in DATA:
                return self.reply(404, b"no")
            return self.reply(302, b"", extra={"Location": "/cdn/" + name})
        if path.startswith("/cdn/"):
            name = path[5:]
            b = DATA[name]
            if name in Fake.corrupt:
                b = b[:-1] + bytes([b[-1] ^ 1])
            rng = self.headers.get("Range")
            start = 0
            if rng and not Fake.ignore_range:
                start = int(rng.split("=")[1].split("-")[0])
                if start >= len(b):
                    return self.reply(416, b"")
                code, extra = 206, {"Content-Range": "bytes %d-%d/%d" % (start, len(b) - 1, len(b))}
            else:
                code, extra = 200, {}
            body = b[start:]
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            for k, v in extra.items():
                self.send_header(k, v)
            self.end_headers()
            if Fake.drop_after is not None:
                cut, Fake.drop_after = Fake.drop_after, None
                self.wfile.write(body[:cut])
                self.wfile.flush()
                self.close_connection = True
                return
            self.wfile.write(body)
            return
        self.reply(404, b"")


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.old = hf.BASE
        hf.BASE = "http://127.0.0.1:%d" % cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        hf.BASE = cls.old
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        Fake.log, Fake.drop_after, Fake.ignore_range, Fake.corrupt = [], None, False, set()
        Fake.branch, Fake.no_lfs, Fake.huge = "main", set(), False
        hf._cache.clear()
        self.addCleanup(shutil.rmtree, self.tmp, True)


class Parsing(unittest.TestCase):
    def test_quants(self):
        for name, q in [("Qwen3-4B-Q4_K_M.gguf", "Q4_K_M"), ("m-IQ3_XXS.gguf", "IQ3_XXS"), ("m-UD-Q4_K_XL.gguf", "UD-Q4_K_XL"),
                        ("BF16/m-BF16-00001-of-00002.gguf", "BF16"), ("m-q8_0.gguf", "Q8_0"), ("m-MXFP4.gguf", "MXFP4"), ("plain.gguf", None)]:
            self.assertEqual(hf.quant_of(name), q, name)

    def test_model_id(self):
        self.assertEqual(hf.model_id("unsloth/Qwen3-4B-GGUF", "q4_k_m"), "Qwen3-4B-Q4_K_M")
        self.assertEqual(hf.model_id("a/Plain", "Q8_0"), "Plain-Q8_0")


class FileList(Base):
    def test_groups_shards_quants_and_mmproj(self):
        r = hf.files(REPO)
        self.assertEqual([v["quant"] for v in r["versions"]], ["Q8_0", "Q4_K_M"])      # smallest first: 210k < 300k
        q8 = r["versions"][0]
        self.assertTrue(q8["split"] and q8["complete"])
        self.assertEqual([f["name"] for f in q8["files"]], ["Tiny-Q8_0-00001-of-00002.gguf", "Tiny-Q8_0-00002-of-00002.gguf"])
        self.assertEqual(q8["bytes"], 210_000)
        self.assertEqual(q8["files"][0]["sha256"], hashlib.sha256(DATA["Tiny-Q8_0-00001-of-00002.gguf"]).hexdigest())
        self.assertFalse(r["versions"][1]["split"])
        self.assertEqual([f["name"] for f in r["mmproj"]], ["mmproj-Tiny-F16.gguf"])

    def test_missing_shard_is_flagged(self):
        tree = [{"path": "m-Q4_K_M-00001-of-00003.gguf", "size": 1}, {"path": "m-Q4_K_M-00003-of-00003.gguf", "size": 1}]
        self.assertFalse(hf.group_files(tree)[0]["Q4_K_M"]["complete"])

    def test_user_agent_and_errors(self):
        hf.files(REPO)
        self.assertRegex(Fake.log[0][2], r"^Shoggoth/")
        with self.assertRaises(hf.HFError) as c:
            hf.files("nobody/missing")
        self.assertIn("doesn't have", str(c.exception))
        with self.assertRaises(hf.HFError):
            hf.files("not a repo")

    def test_unreachable(self):
        old = hf.BASE
        hf.BASE = "http://127.0.0.1:1"
        try:
            with self.assertRaises(hf.HFError) as c:
                hf.files(REPO)
            self.assertIn("internet", str(c.exception))
        finally:
            hf.BASE = old


class Download(Base):
    def test_single_file_follows_redirect_and_lays_out(self):
        seen = []
        r = hf.download(REPO, "Q4_K_M", self.tmp, progress=lambda d, t: seen.append((d, t)))
        want = os.path.join(self.tmp, "acme", "Tiny-GGUF", "Tiny-Q4_K_M.gguf")
        self.assertEqual(r["files"], [want])
        self.assertEqual(r["id"], "Tiny-Q4_K_M")
        self.assertEqual(open(want, "rb").read(), DATA["Tiny-Q4_K_M.gguf"])
        self.assertFalse(os.path.exists(want + ".part"))
        self.assertEqual(seen[-1], (300_000, 300_000))
        self.assertTrue(any("/cdn/" in p for p, _, _ in Fake.log))

    def test_by_file_name(self):
        r = hf.download(REPO, "Tiny-Q4_K_M.gguf", self.tmp)
        self.assertEqual(r["quant"], "Q4_K_M")

    def test_shards_and_mmproj_progress_spans_all(self):
        seen = []
        r = hf.download(REPO, "Q8_0", self.tmp, progress=lambda d, t: seen.append((d, t)), mmproj=True)
        self.assertEqual(len(r["files"]), 2)
        self.assertTrue(r["mmproj"].endswith("mmproj-Tiny-F16.gguf"))
        self.assertEqual({t for _, t in seen}, {260_000})
        self.assertEqual(seen[-1][0], 260_000)
        self.assertEqual([d for d, _ in seen], sorted(d for d, _ in seen))
        for p in r["files"]:
            self.assertTrue(os.path.getsize(p) > 0)

    def test_no_mmproj_unless_asked(self):
        self.assertIsNone(hf.download(REPO, "Q4_K_M", self.tmp)["mmproj"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "acme", "Tiny-GGUF", "mmproj-Tiny-F16.gguf")))

    def test_resumes_from_part_file_with_range(self):
        dest = os.path.join(self.tmp, "acme", "Tiny-GGUF")
        os.makedirs(dest)
        with open(os.path.join(dest, "Tiny-Q4_K_M.gguf.part"), "wb") as f:
            f.write(DATA["Tiny-Q4_K_M.gguf"][:100_000])
        hf.download(REPO, "Q4_K_M", self.tmp)
        self.assertEqual(open(os.path.join(dest, "Tiny-Q4_K_M.gguf"), "rb").read(), DATA["Tiny-Q4_K_M.gguf"])
        cdn = [rng for p, rng, _ in Fake.log if p.startswith("/cdn/")]
        self.assertEqual(cdn, ["bytes=100000-"])          # Range survived the redirect

    def test_server_ignoring_range_restarts(self):
        dest = os.path.join(self.tmp, "acme", "Tiny-GGUF")
        os.makedirs(dest)
        with open(os.path.join(dest, "Tiny-Q4_K_M.gguf.part"), "wb") as f:
            f.write(b"junk" * 1000)
        Fake.ignore_range = True
        hf.download(REPO, "Q4_K_M", self.tmp)
        self.assertEqual(open(os.path.join(dest, "Tiny-Q4_K_M.gguf"), "rb").read(), DATA["Tiny-Q4_K_M.gguf"])

    def test_interrupted_transfer_resumes_by_itself(self):
        Fake.drop_after = 120_000
        hf.download(REPO, "Q4_K_M", self.tmp)
        got = open(os.path.join(self.tmp, "acme", "Tiny-GGUF", "Tiny-Q4_K_M.gguf"), "rb").read()
        self.assertEqual(got, DATA["Tiny-Q4_K_M.gguf"])
        ranges = [rng for p, rng, _ in Fake.log if p.startswith("/cdn/")]
        self.assertEqual(ranges[0], None)
        self.assertTrue(ranges[1].startswith("bytes=") and ranges[1] != "bytes=0-")

    def test_interrupted_without_retries_keeps_part_then_next_call_finishes(self):
        old, hf.RETRIES = hf.RETRIES, 0
        try:
            Fake.drop_after = 100_000
            with self.assertRaises(hf.HFError):
                hf.download(REPO, "Q4_K_M", self.tmp)
        finally:
            hf.RETRIES = old
        part = os.path.join(self.tmp, "acme", "Tiny-GGUF", "Tiny-Q4_K_M.gguf.part")
        self.assertTrue(0 < os.path.getsize(part) < 300_000)
        hf.download(REPO, "Q4_K_M", self.tmp)
        self.assertFalse(os.path.exists(part))

    def test_sha_mismatch_deletes_and_raises(self):
        Fake.corrupt = {"Tiny-Q4_K_M.gguf"}
        with self.assertRaises(hf.HFError) as c:
            hf.download(REPO, "Q4_K_M", self.tmp)
        self.assertIn("checksum", str(c.exception))
        d = os.path.join(self.tmp, "acme", "Tiny-GGUF")
        self.assertEqual([n for n in os.listdir(d) if "Q4_K_M" in n], [])

    def test_bad_resumed_bytes_are_caught_by_the_hash(self):
        d = os.path.join(self.tmp, "acme", "Tiny-GGUF")
        os.makedirs(d)
        with open(os.path.join(d, "Tiny-Q4_K_M.gguf.part"), "wb") as f:
            f.write(b"\x00" * 1000)                    # right size prefix, wrong content
        with self.assertRaises(hf.HFError):
            hf.download(REPO, "Q4_K_M", self.tmp)
        self.assertEqual(os.listdir(d), [])

    def test_cancel_stops_and_keeps_part(self):
        ev = threading.Event()

        def progress(done, total):
            if done > 50_000:
                ev.set()
        with self.assertRaises(hf.HFError) as c:
            hf.download(REPO, "Q4_K_M", self.tmp, progress=progress, cancel=ev)
        self.assertTrue(c.exception.cancelled)
        d = os.path.join(self.tmp, "acme", "Tiny-GGUF")
        self.assertEqual(os.listdir(d), ["Tiny-Q4_K_M.gguf.part"])

    def test_already_downloaded_is_not_fetched_again(self):
        hf.download(REPO, "Q4_K_M", self.tmp)
        Fake.log.clear()
        hf.download(REPO, "Q4_K_M", self.tmp)
        self.assertFalse([p for p, _, _ in Fake.log if "/cdn/" in p or "/resolve/" in p])

    def test_existing_file_is_hashed_and_replaced_when_wrong(self):       # H1
        d = os.path.join(self.tmp, "acme", "Tiny-GGUF")
        os.makedirs(d)
        dest = os.path.join(d, "Tiny-Q4_K_M.gguf")
        with open(dest, "wb") as f:
            f.write(b"\x01" * len(DATA["Tiny-Q4_K_M.gguf"]))            # right length, wrong bytes
        hf.download(REPO, "Q4_K_M", self.tmp)
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), DATA["Tiny-Q4_K_M.gguf"])

    def test_file_with_no_checksum_is_refused(self):                      # H1
        Fake.no_lfs = {"Tiny-Q4_K_M.gguf"}
        with self.assertRaises(hf.HFError) as c:
            hf.download(REPO, "Q4_K_M", self.tmp)
        self.assertIn("checksum", str(c.exception))
        self.assertFalse([p for p, _, _ in Fake.log if "/resolve/" in p])

    def test_uses_the_repos_default_branch(self):                         # H2
        Fake.branch = "master"
        r = hf.download(REPO, "Q4_K_M", self.tmp)
        self.assertTrue(os.path.isfile(r["files"][0]))
        self.assertTrue(any("/resolve/master/" in p for p, _, _ in Fake.log))

    def test_unknown_quant_and_missing_mmproj(self):
        with self.assertRaises(hf.HFError):
            hf.download(REPO, "Q2_K", self.tmp)
        with self.assertRaises(hf.HFError):
            hf.download(REPO, "Q4_K_M", self.tmp, mmproj="nope.gguf")


class Search(Base):
    def test_ranks_trusted_first_then_downloads_and_drops_non_gguf(self):
        r = hf.search("tiny")
        self.assertEqual([x["repo"] for x in r], ["lmstudio-community/High-GGUF", "bartowski/Mid-GGUF", "unsloth/Low-GGUF", "random/Big-GGUF"])
        self.assertEqual([x["trusted"] for x in r], [True, True, True, False])
        url = [p for p, _, _ in Fake.log if p.startswith("/api/models?")][0]
        for part in ("search=tiny", "filter=gguf", "sort=downloads", "limit=40"):
            self.assertIn(part, url)

    def test_cached_for_ten_minutes(self):
        clock = [1000.0]
        old, hf._now = hf._now, lambda: clock[0]
        try:
            before = Fake.search_hits
            hf.search("Tiny")
            hf.search("tiny ")
            self.assertEqual(Fake.search_hits, before + 1)
            clock[0] += 599
            hf.search("tiny")
            self.assertEqual(Fake.search_hits, before + 1)
            clock[0] += 2
            hf.search("tiny")
            self.assertEqual(Fake.search_hits, before + 2)
        finally:
            hf._now = old

    def test_cache_keeps_only_the_most_recent_queries(self):              # H3
        old, hf.CACHE_MAX = hf.CACHE_MAX, 3
        try:
            for q in ("a", "b", "c", "d"):
                hf.search(q)
            self.assertEqual(list(hf._cache), ["b", "c", "d"])
        finally:
            hf.CACHE_MAX = old

    def test_oversize_reply_is_refused(self):                             # H3
        Fake.huge = True
        old, hf.MAX_JSON = hf.MAX_JSON, 1000
        try:
            with self.assertRaises(hf.HFError) as c:
                hf.files(REPO)
        finally:
            hf.MAX_JSON = old
        self.assertIn("too large", str(c.exception))

    def test_empty_query(self):
        self.assertEqual(hf.search("  "), [])


if __name__ == "__main__":
    unittest.main()
