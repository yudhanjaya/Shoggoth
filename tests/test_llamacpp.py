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
