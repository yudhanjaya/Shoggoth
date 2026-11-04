"""The steps every updater shares: checksum, safe unpacking, keeping the newest copies. Run: python3 -m unittest discover tests"""
import base64
import hashlib
import io
import os
import shutil
import sys
import tarfile
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import updater  # noqa: E402


def tarball(path, files):
    with tarfile.open(path, "w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


class Shared(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def p(self, *a):
        return os.path.join(self.tmp, *a)

    def test_a_download_is_kept_only_when_its_checksum_matches(self):
        src = self.p("src.bin")
        open(src, "wb").write(b"hello" * 1000)
        url = "file://" + src
        upd = {"bytes": 0, "total": 0, "state": "downloading"}
        sha256 = hashlib.sha256(b"hello" * 1000).hexdigest()
        updater.download(url, self.p("a"), sha256, "GitHub's", upd, verifying=True)
        self.assertEqual((upd["bytes"], upd["state"]), (5000, "verifying"))
        sri = "sha512-" + base64.b64encode(hashlib.sha512(b"hello" * 1000).digest()).decode()
        updater.download(url, self.p("b"), sri, "npm's", None)                       # npm's form
        with self.assertRaisesRegex(RuntimeError, "npm's checksum"):
            updater.download(url, self.p("c"), "sha512-AAAA", "npm's", None)
        with self.assertRaisesRegex(RuntimeError, "GitHub's checksum"):
            updater.download(url, self.p("d"), "0" * 64, "GitHub's", None)

    def test_unpacking_strips_the_top_folder_and_refuses_paths_that_escape(self):
        tarball(self.p("ok.tgz"), {"pkg/lemond": b"x", "pkg/lib/a.so": b"y"})
        updater.safe_extract(self.p("ok.tgz"), self.p("out"), 1000, chmod=lambda rel: 0o755 if rel == "lemond" else 0o644)
        self.assertEqual(open(self.p("out", "lib", "a.so"), "rb").read(), b"y")
        self.assertTrue(os.access(self.p("out", "lemond"), os.X_OK))
        tarball(self.p("bad.tgz"), {"pkg/../../evil": b"x"})
        with self.assertRaisesRegex(RuntimeError, "unsafe path"):
            updater.safe_extract(self.p("bad.tgz"), self.p("out2"), 1000)
        self.assertFalse(os.path.exists(self.p("evil")))

    def test_an_npm_package_unpacks_only_what_was_asked_for_and_only_under_package(self):
        tarball(self.p("n.tgz"), {"package/package.json": b"{}", "package/dist/bundle/cli.js": b"js", "package/docs/x.md": b"d", "other/f": b"o"})
        updater.safe_extract(self.p("n.tgz"), self.p("pi"), 1000, top="package", only=["package.json", "dist/bundle"], dirs=False)
        self.assertEqual(sorted(os.listdir(self.p("pi"))), ["dist", "package.json"])
        self.assertTrue(os.path.isfile(self.p("pi", "dist", "bundle", "cli.js")))

    def test_a_download_that_unpacks_to_far_more_than_expected_is_refused(self):
        tarball(self.p("big.tgz"), {"pkg/f": b"x" * 500})
        with self.assertRaisesRegex(RuntimeError, "larger than expected"):
            updater.safe_extract(self.p("big.tgz"), self.p("o"), 100)

    def test_only_the_newest_copies_are_kept(self):
        for v in ("1.0.0", "1.2.0", "1.10.0", "0.9.0"):
            os.makedirs(self.p(v))
            open(self.p(v, "ok"), "w").close()
        os.makedirs(self.p("half"))                                                   # not a finished copy: left alone
        updater.keep_newest(self.tmp, lambda d: os.path.isfile(os.path.join(d, "ok")),
                            lambda v: tuple(int(x) for x in v.split(".")))
        self.assertEqual(sorted(d for d in os.listdir(self.tmp) if d != "half"), ["1.10.0", "1.2.0"])

    def test_an_update_starts_once_and_a_failed_lookup_is_the_error(self):
        upd = {"state": "idle", "bytes": 9, "total": 0, "version": None, "error": None}
        done = []
        self.assertTrue(updater.begin(upd, ("downloading",), lambda: {"version": "2", "size": 7}, lambda rel: done.append(rel)))
        self.assertEqual((upd["state"], upd["bytes"], upd["total"], upd["version"]), ("downloading", 0, 7, "2"))
        self.assertFalse(updater.begin(upd, ("downloading",), lambda: 1 / 0, None))           # running: not even looked up
        for _ in range(50):
            if done:
                break
            time.sleep(0.02)
        self.assertEqual(done, [{"version": "2", "size": 7}])

        def fail():
            raise RuntimeError("no network")
        upd["state"] = "idle"
        self.assertFalse(updater.begin(upd, ("downloading",), fail, None))
        self.assertEqual((upd["state"], upd["error"]), ("error", "no network"))


if __name__ == "__main__":
    unittest.main()
