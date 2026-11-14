"""llama.cpp set up by Shoggoth: picking the release and build, the presets file, unpacking safely."""
import io
import os
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shoggoth"))
import llamacpp  # noqa: E402


def _asset(name, digest="sha256:" + "a" * 64):
    return {"name": name, "browser_download_url": "https://example.invalid/" + name, "size": 10, "digest": digest}


def _release(tag, names):
    return {"tag_name": tag, "assets": [_asset(n) for n in names]}


class PickRelease(unittest.TestCase):
    def test_skips_tags_without_builds(self):
        # llama.cpp's v1.0.0 tag holds only nightly-tag.txt; GitHub's "latest" pointed at it on 13 Nov 2026
        rels = [_release("v1.0.0", ["nightly-tag.txt"]), _release("b12375", ["x"]), _release("b12374", ["x"])]
        self.assertEqual(llamacpp.pick_release(rels)["tag_name"], "b12375")

    def test_numeric_not_text_order(self):
        rels = [_release("b9999", ["x"]), _release("b12000", ["x"])]
        self.assertEqual(llamacpp.pick_release(rels)["tag_name"], "b12000")

    def test_drafts_and_empty_ignored(self):
        rels = [dict(_release("b2", ["x"]), draft=True), {"tag_name": "b3", "assets": []}, _release("b1", ["x"])]
        self.assertEqual(llamacpp.pick_release(rels)["tag_name"], "b1")

    def test_none(self):
        self.assertIsNone(llamacpp.pick_release([_release("v1.0.0", ["nightly-tag.txt"])]))


class PickBackend(unittest.TestCase):
    def test_cuda13_needs_new_driver(self):
        self.assertEqual(llamacpp.pick_backend(580), "cuda13")
        self.assertEqual(llamacpp.pick_backend(575), "cuda12")

    def test_amd_and_cpu(self):
        self.assertEqual(llamacpp.pick_backend(None, amd=True), "vulkan")
        self.assertEqual(llamacpp.pick_backend(None), "cpu")


class PickAssets(unittest.TestCase):
    def test_cuda_needs_runtime_too(self):
        rel = _release("b5", ["llama-b5-bin-ubuntu-cuda-13.5-x64.tar.gz", "cudart-llama-b5-bin-ubuntu-cuda-13.5-x64.tar.gz"])
        names = [a[0] for a in llamacpp.pick_assets(rel, "cuda13")]
        self.assertEqual(names, ["llama-b5-bin-ubuntu-cuda-13.5-x64.tar.gz", "cudart-llama-b5-bin-ubuntu-cuda-13.5-x64.tar.gz"])
        with self.assertRaises(RuntimeError):
            llamacpp.pick_assets(_release("b5", ["llama-b5-bin-ubuntu-cuda-13.5-x64.tar.gz"]), "cuda13")

    def test_refuses_without_checksum(self):
        rel = {"tag_name": "b5", "assets": [_asset("llama-b5-bin-ubuntu-x64.tar.gz", digest=None)]}
        with self.assertRaises(RuntimeError) as e:
            llamacpp.pick_assets(rel, "cpu")
        self.assertIn("checksum", str(e.exception))

    def test_sha_is_taken_from_digest(self):
        rel = _release("b5", ["llama-b5-bin-ubuntu-vulkan-x64.tar.gz"])
        self.assertEqual(llamacpp.pick_assets(rel, "vulkan")[0][3], "a" * 64)


class Presets(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "presets.ini")

    def test_round_trip(self):
        llamacpp.write_presets({"Qwen3-0.6B-Q4_0": {"ctx-size": 8192, "jinja": True, "mmproj": None}},
                               llamacpp.DEFAULTS, path=self.p)
        got = llamacpp.read_presets(self.p)
        self.assertEqual(got["Qwen3-0.6B-Q4_0"], {"ctx-size": "8192", "jinja": "true"})
        self.assertEqual(got["*"]["cache-type-k"], "q8_0")

    def test_rejects_bad_keys_values_and_names(self):
        for models in ({"m": {"--ctx-size": 1}}, {"m": {"model": "a\nb"}}, {"bad]name": {"c": 1}}, {"m": {"x y": 1}}):
            with self.assertRaises(ValueError):
                llamacpp.write_presets(models, path=self.p)


class Extract(unittest.TestCase):
    def _tar(self, members):
        path = os.path.join(tempfile.mkdtemp(), "t.tar.gz")
        with tarfile.open(path, "w:gz") as tf:
            for kind, name, extra in members:
                ti = tarfile.TarInfo(name)
                if kind == "file":
                    ti.size, ti.mode = len(extra), 0o755
                    tf.addfile(ti, io.BytesIO(extra))
                elif kind == "link":
                    ti.type, ti.linkname = tarfile.SYMTYPE, extra
                    tf.addfile(ti)
        return path

    def test_drops_top_folder_and_keeps_inner_links(self):
        t = self._tar([("file", "llama-b1/libx.so.1.0", b"x"), ("link", "llama-b1/libx.so.1", "libx.so.1.0"),
                       ("file", "llama-b1/llama-server", b"#!")])
        dest = tempfile.mkdtemp()
        llamacpp._safe_extract(t, dest)
        self.assertTrue(os.path.islink(os.path.join(dest, "libx.so.1")))
        self.assertTrue(os.access(os.path.join(dest, "llama-server"), os.X_OK))

    def test_rejects_escapes(self):
        for members in ([("file", "top/../../evil", b"x")], [("link", "top/x", "/etc/passwd")], [("link", "top/x", "../../etc")]):
            with self.assertRaises(RuntimeError):
                llamacpp._safe_extract(self._tar(members), tempfile.mkdtemp())


class Args(unittest.TestCase):
    def test_ctx_from_args(self):
        self.assertEqual(llamacpp.ctx_from_args(["llama-server", "--ctx-size", "32768"]), 32768)
        self.assertEqual(llamacpp.ctx_from_args(["-c", "4096"]), 4096)
        self.assertEqual(llamacpp.ctx_from_args(["--ctx-size=2048"]), 2048)
        self.assertEqual(llamacpp.ctx_from_args(["--port", "1"]), 0)

    def test_router_command(self):
        b = {"server": "/x/llama-server"}
        cmd = llamacpp.command(b, 13340, "k", "/models", 2)
        self.assertEqual(cmd[0], "/x/llama-server")
        self.assertIn("--models-preset", cmd)
        self.assertEqual(cmd[cmd.index("--models-max") + 1], "2")
        self.assertEqual(cmd[cmd.index("--host") + 1], "127.0.0.1")
        self.assertNotIn("-m", cmd)                          # no model: router mode


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- Wave 0 review fixes (L1-L9)
import hashlib  # noqa: E402
import http.server  # noqa: E402
import shutil  # noqa: E402
import threading  # noqa: E402
from unittest import mock  # noqa: E402


class Profile(unittest.TestCase):
    """A scratch data folder for the functions that use root()."""
    def setUp(self):
        self.d = tempfile.mkdtemp()
        p = mock.patch.object(llamacpp, "root", lambda: self.d)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.d, True)


class KeyNotOnCommandLine(Profile):                      # L1
    def test_key_goes_in_a_private_file(self):
        cmd = llamacpp.command({"server": "/x/llama-server"}, 13340, "s3cretkey", "/models")
        self.assertNotIn("--api-key", cmd)
        self.assertNotIn("s3cretkey", " ".join(cmd))
        self.assertEqual(cmd[cmd.index("--api-key-file") + 1], llamacpp._key_file())
        llamacpp._write_private(llamacpp._key_file(), "s3cretkey\n")
        self.assertEqual(os.stat(llamacpp._key_file()).st_mode & 0o777, 0o600)


class SmokeTestEnv(unittest.TestCase):                   # L2
    def test_version_check_runs_with_the_build_folder_on_the_library_path(self):
        with mock.patch.object(llamacpp.util, "run", return_value=(0, "version: 1 (x)\nbuilt: build 12375")) as run:
            self.assertEqual(llamacpp.version_of("/opt/b/llama-server"), "b12375")
        self.assertTrue(run.call_args.kwargs["env"]["LD_LIBRARY_PATH"].startswith("/opt/b"))


class Adopt(Profile):                                    # L3
    def _info(self, **kw):
        info = {"pid": os.getpid(), "port": self.port, "key": "k", "t": llamacpp._start_time(os.getpid()), "dir": "/d"}
        info.update(kw)
        llamacpp._save_run(info)

    def setUp(self):
        super().setUp()
        for k in ("pid", "port", "key", "dir", "t"):
            llamacpp._state[k] = None
        outer = self

        class H(http.server.BaseHTTPRequestHandler):     # /health open; everything else needs the key when protected
            def log_message(self, *a):
                pass

            def do_GET(self):
                ok = self.path == "/health" or not outer.protected or self.headers.get("Authorization") == "Bearer k"
                self.send_response(200 if ok else 401)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
        self.protected = True
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.port = self.srv.server_address[1]

    def test_adopts_a_verified_router(self):
        self._info()
        self.assertTrue(llamacpp._adopt())
        self.assertEqual(llamacpp._state["t"], llamacpp._start_time(os.getpid()))

    def test_missing_start_time_is_refused(self):
        self._info(t=None)
        self.assertFalse(llamacpp._adopt())

    def test_something_that_ignores_the_key_is_refused(self):
        self.protected = False                           # answers 200 to everyone, so it isn't a router with our key
        self._info()
        self.assertFalse(llamacpp._adopt())

    def test_wrong_key_is_refused(self):
        self._info(key="other")
        self.assertFalse(llamacpp._adopt())


class RunFile(Profile):                                  # L4
    def test_private_from_the_start_and_stale_temp_removed(self):
        stale = llamacpp._run_file() + ".tmp"
        with open(stale, "w") as f:
            f.write("old key")
        os.chmod(stale, 0o644)
        llamacpp._save_run({"key": "k"})
        self.assertEqual(os.stat(llamacpp._run_file()).st_mode & 0o777, 0o600)
        self.assertFalse(os.path.exists(stale))
        with open(stale, "w") as f:
            f.write("left behind")
        llamacpp._state.update(pid=os.getpid(), t=None)
        with mock.patch.object(llamacpp.os, "killpg"), mock.patch.object(llamacpp, "_start_time", return_value=None):
            llamacpp.stop(grace=0)
        self.assertEqual(os.listdir(self.d), [])

    def test_stop_leaves_a_pid_that_is_no_longer_ours(self):
        llamacpp._state.update(pid=os.getpid(), t="1")
        with mock.patch.object(llamacpp.os, "killpg") as kill:
            llamacpp.stop(grace=0)
        kill.assert_not_called()
        llamacpp._state["t"] = None


class _Serve(http.server.BaseHTTPRequestHandler):
    data = b""
    ranges = True
    log = []
    drop = None

    def log_message(self, *a):
        pass

    def do_GET(self):
        rng = self.headers.get("Range")
        _Serve.log.append(rng)
        b, start = _Serve.data, 0
        if rng and _Serve.ranges:
            start = int(rng.split("=")[1].split("-")[0])
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, len(b) - 1, len(b)))
        else:
            self.send_response(200)
        body = b[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if _Serve.drop is not None:
            cut, _Serve.drop = _Serve.drop, None
            self.wfile.write(body[:cut])
            self.wfile.flush()
            self.close_connection = True
            return
        self.wfile.write(body)


class Download(Profile):                                 # L5
    def setUp(self):
        super().setUp()
        _Serve.data, _Serve.ranges, _Serve.log, _Serve.drop = bytes(range(251)) * 2000, True, [], None
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Serve)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.url = "http://127.0.0.1:%d/a" % self.srv.server_address[1]
        self.sha = hashlib.sha256(_Serve.data).hexdigest()
        self.out = os.path.join(self.d, "out")

    def test_resumes_after_a_cut_with_range(self):
        _Serve.drop = 100_000
        with self.assertRaises(Exception):
            llamacpp._download(self.url, self.out, self.sha, size=len(_Serve.data))
        self.assertFalse(os.path.exists(self.out))
        llamacpp._download(self.url, self.out, self.sha, size=len(_Serve.data))
        self.assertEqual(open(self.out, "rb").read(), _Serve.data)
        self.assertEqual(_Serve.log[0], None)
        self.assertTrue(_Serve.log[1].startswith("bytes=") and _Serve.log[1] != "bytes=0-")

    def test_server_ignoring_range_starts_over(self):
        _Serve.drop, _Serve.ranges = 50_000, False
        with self.assertRaises(Exception):
            llamacpp._download(self.url, self.out, self.sha, size=len(_Serve.data))
        llamacpp._download(self.url, self.out, self.sha, size=len(_Serve.data))
        self.assertEqual(open(self.out, "rb").read(), _Serve.data)

    def test_wrong_hash_is_thrown_away(self):
        with self.assertRaises(RuntimeError) as c:
            llamacpp._download(self.url, self.out, "0" * 64, size=len(_Serve.data))
        self.assertIn("checksum", str(c.exception))
        self.assertFalse(os.path.exists(self.out))
        self.assertEqual(os.listdir(os.path.join(self.d, ".partial")), [])

    def test_wrong_size_is_refused_before_the_hash(self):
        with self.assertRaises(RuntimeError) as c:
            llamacpp._download(self.url, self.out, self.sha, size=len(_Serve.data) + 5)
        self.assertIn("cut short", str(c.exception))


class Install(Profile):                                  # L6
    def test_each_install_gets_its_own_folder(self):
        seen = []

        def fake_download(url, path, sha, cancel=None, size=0):
            seen.append(os.path.dirname(path))
            raise RuntimeError("stop here")
        rel = _release("b5", ["llama-b5-bin-ubuntu-x64.tar.gz"])
        with mock.patch.object(llamacpp, "_download", fake_download):
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    llamacpp.install("cpu", rel)
        self.assertNotEqual(seen[0], seen[1])
        self.assertEqual([n for n in os.listdir(self.d) if n.startswith(".install-")], [])


class Prune(Profile):                                    # L7
    def test_never_removes_the_build_a_previous_shoggoth_left_running(self):
        for tag in ("b1", "b2", "b3"):
            os.makedirs(os.path.join(self.d, tag, "cpu"))
            open(os.path.join(self.d, tag, "cpu", "llama-server"), "w").close()
        llamacpp._state["dir"] = None
        llamacpp._save_run({"pid": os.getpid(), "t": llamacpp._start_time(os.getpid()), "dir": os.path.join(self.d, "b1", "cpu")})
        llamacpp._prune(keep=2)
        self.assertTrue(os.path.isdir(os.path.join(self.d, "b1")))
        os.remove(llamacpp._run_file())
        llamacpp._prune(keep=2)
        self.assertFalse(os.path.isdir(os.path.join(self.d, "b1")))


class Hardlinks(unittest.TestCase):                      # L8
    def _tar(self, members):
        path = os.path.join(tempfile.mkdtemp(), "t.tar.gz")
        with tarfile.open(path, "w:gz") as tf:
            for kind, name, extra in members:
                ti = tarfile.TarInfo(name)
                if kind == "file":
                    ti.size = len(extra)
                    tf.addfile(ti, io.BytesIO(extra))
                elif kind == "hard":
                    ti.type, ti.linkname = tarfile.LNKTYPE, extra
                    tf.addfile(ti)
                elif kind == "fifo":
                    ti.type = tarfile.FIFOTYPE
                    tf.addfile(ti)
        return path

    def test_hard_link_inside_is_kept(self):
        dest = tempfile.mkdtemp()
        llamacpp._safe_extract(self._tar([("file", "b/libx.so.1.0", b"x"), ("hard", "b/libx.so.1", "b/libx.so.1.0")]), dest)
        self.assertEqual(open(os.path.join(dest, "libx.so.1"), "rb").read(), b"x")
        self.assertTrue(os.path.samefile(os.path.join(dest, "libx.so.1"), os.path.join(dest, "libx.so.1.0")))

    def test_hard_link_outside_and_special_files_are_refused(self):
        for members in ([("hard", "b/x", "../../etc/passwd")], [("hard", "b/x", "b/missing")], [("fifo", "b/pipe", None)]):
            with self.assertRaises(RuntimeError):
                llamacpp._safe_extract(self._tar(members), tempfile.mkdtemp())


class PresetsParserSafe(unittest.TestCase):              # L9
    def test_rejects_what_llama_cpp_would_misread(self):
        p = os.path.join(tempfile.mkdtemp(), "p.ini")
        for models in ({"m": {"model": "/models/Old #2/x.gguf"}}, {"m": {"model": "/m/a ;b"}}, {"m": {"1x": 1}},
                       {"default": {"c": 1}}):
            with self.assertRaises(ValueError):
                llamacpp.write_presets(models, path=p)
        llamacpp.write_presets({"m": {"model": "/models/a#b/x.gguf"}}, path=p)       # no space before #: fine

    def test_a_model_called_star_cannot_replace_the_defaults(self):
        p = os.path.join(tempfile.mkdtemp(), "p.ini")
        llamacpp.write_presets({"*": {"ctx-size": 1}, "m": {"c": 2}}, {"flash-attn": "on"}, path=p)
        got = llamacpp.read_presets(p)
        self.assertEqual(got["*"], {"flash-attn": "on"})
        self.assertEqual(open(p).read().count("[*]"), 1)
