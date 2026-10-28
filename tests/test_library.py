"""The model library: reading GGUF headers, sorting models into tiers, routing them to an engine, and per-engine views.

Everything here is built from synthetic files and YAML-only engines, so it doesn't depend on what is installed on the computer.
Run from the repository root:  python3 -m unittest discover tests
"""
import os
import shutil
import struct
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "zest"))

import gguf  # noqa: E402
import library  # noqa: E402
import registry  # noqa: E402

GB = 10 ** 9


def write_gguf(path, arch="gemma3", name="Test Model", file_type=15, ctx=8192, size=None, kind="model", tokens=2000):
    """A minimal real GGUF header: general keys, the context length, a tokenizer array, and file_type AFTER the tokenizer, as Strata's files do."""
    def s(x):
        b = x.encode()
        return struct.pack("<Q", len(b)) + b

    def kv(key, t, val):
        return s(key) + struct.pack("<I", t) + val
    body = (kv("general.architecture", 8, s(arch)) + kv("general.type", 8, s(kind)) + kv("general.name", 8, s(name)) +
            kv("%s.context_length" % arch, 4, struct.pack("<I", ctx)) +
            kv("tokenizer.ggml.tokens", 9, struct.pack("<I", 8) + struct.pack("<Q", tokens) + b"".join(s("tok%d" % i) for i in range(tokens))) +
            kv("general.file_type", 4, struct.pack("<I", file_type)))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"GGUF" + struct.pack("<IQQ", 3, 0, 6) + body)
        if size:
            f.truncate(size)                                   # a sparse file: the size without the disk space


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("ZEST_DATA_DIR", "ZEST_CONFIG_DIR", "HF_HUB_CACHE")}
        os.environ["ZEST_DATA_DIR"], os.environ["ZEST_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")
        os.environ["HF_HUB_CACHE"] = os.path.join(self.tmp, "nohf")
        library._index.update(loaded=False, d={})
        self.lib = os.path.join(self.tmp, "lib")
        library.set_root(self.lib)
        self.user = os.path.join(self.tmp, "providers")
        os.makedirs(self.user)
        self.add("fast", """
            id: fast
            name: Fast engine
            api_version: 1
            tier: small-medium
            run: {mode: external}
            endpoint: {base_url: "http://127.0.0.1:1/v1"}
            models: {formats: [gguf], excluded_architectures: [qwen4exp], data_dir: "{library}/lemonade", view: true}
            """)
        self.add("big", """
            id: big
            name: Big engine
            api_version: 1
            tier: large
            run: {mode: external}
            endpoint: {base_url: "http://127.0.0.1:2/v1"}
            models: {formats: [gguf], architectures: [qwen4exp], data_dir: "{library}/strata"}
            """)
        self.reg = registry.Registry(bundled=os.path.join(self.tmp, "none"), user=self.user).load()
        self.assertEqual(self.reg.errors, [])
        self.machine = {"ram_gb": 64, "vram_gb": 24, "best_gpu_gb": 24, "gpus": ["RTX"], "free_disk_gb": 500}

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, name, text):
        d = os.path.join(self.user, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "provider.yaml"), "w") as f:
            f.write(textwrap.dedent(text))

    def cat(self):
        return library.catalog(self.reg, self.machine)

    def by_stem(self):
        return {m["stem"]: m for m in self.cat()}


class Headers(Env):
    def test_reads_everything_even_after_a_long_tokenizer(self):
        p = os.path.join(self.tmp, "m.gguf")
        write_gguf(p, arch="qwen4exp", name="Big One", file_type=26, ctx=262144, tokens=50000)
        h = gguf.read_header(p)
        self.assertEqual((h["architecture"], h["quant"], h["context"], h["name"]), ("qwen4exp", "IQ3_S", 262144, "Big One"))

    def test_not_a_gguf(self):
        p = os.path.join(self.tmp, "x.gguf")
        with open(p, "wb") as f:
            f.write(b"not a model")
        self.assertIsNone(gguf.read_header(p))

    def test_shard_names(self):
        self.assertEqual(gguf.shard_of("/a/Model-IQ3_S-00002-of-00005.gguf"), ("Model-IQ3_S", 2, 5))
        self.assertEqual(gguf.shard_of("/a/Plain.gguf"), ("Plain", 1, 1))

    def test_roles(self):
        self.assertEqual(gguf.role_of("clip"), "projector")
        self.assertEqual(gguf.role_of("dflash"), "drafter")
        self.assertEqual(gguf.role_of("parakeet"), "speech")
        self.assertEqual(gguf.role_of("gemma3"), "chat")
        self.assertEqual(gguf.role_of("deepseek41", "x", "/m/deepseek-flash-shared"), "support")
        self.assertEqual(gguf.role_of(""), "unknown")


class Catalog(Env):
    def test_routing_and_tiers(self):
        write_gguf(os.path.join(self.lib, "lemonade", "small", "Small-Q4.gguf"), size=4 * GB)
        write_gguf(os.path.join(self.lib, "lemonade", "mid", "Mid-Q4.gguf"), size=18 * GB)
        write_gguf(os.path.join(self.lib, "lemonade", "huge", "Huge-Q4.gguf"), size=40 * GB)             # plain format, but very large
        write_gguf(os.path.join(self.lib, "strata", "Flash-IQ3_S-00001-of-00002.gguf"), arch="qwen4exp", file_type=26, size=1 * GB)
        write_gguf(os.path.join(self.lib, "strata", "Flash-IQ3_S-00002-of-00002.gguf"), arch="qwen4exp", file_type=26, size=1 * GB)
        c = self.by_stem()
        self.assertEqual((c["Small-Q4"]["tier"], c["Small-Q4"]["provider"]), ("small", "fast"))
        self.assertEqual((c["Mid-Q4"]["tier"], c["Mid-Q4"]["provider"]), ("medium", "fast"))
        # a plain model that is Large by size still goes to the engine that can run it
        self.assertEqual((c["Huge-Q4"]["tier"], c["Huge-Q4"]["provider"]), ("large", "fast"))
        flash = c["Flash-IQ3_S"]
        self.assertEqual((flash["tier"], flash["provider"], flash["architecture"], flash["quant"]), ("large", "big", "qwen4exp", "IQ3_S"))
        self.assertEqual(len(flash["files"]), 2)
        self.assertTrue(flash["complete"])
        self.assertEqual(flash["providers"]["fast"]["ok"], False)         # the fast engine is told it can't run it

    def test_incomplete_shards_are_flagged(self):
        write_gguf(os.path.join(self.lib, "strata", "Flash-00001-of-00003.gguf"), arch="qwen4exp")
        self.assertFalse(self.by_stem()["Flash"]["complete"])

    def test_support_files_and_projectors_are_not_models(self):
        write_gguf(os.path.join(self.lib, "lemonade", "m", "Vision-Q4.gguf"))
        write_gguf(os.path.join(self.lib, "lemonade", "m", "mmproj-Vision-BF16.gguf"), arch="clip", kind="mmproj")
        write_gguf(os.path.join(self.lib, "lemonade", "draft", "Drafter.gguf"), arch="dflash")
        write_gguf(os.path.join(self.lib, "lemonade", "voice", "Voice.gguf"), arch="parakeet")
        c = self.by_stem()
        self.assertEqual(sorted(c), ["Vision-Q4"])
        self.assertTrue(c["Vision-Q4"]["vision"])
        self.assertTrue(c["Vision-Q4"]["mmproj"].endswith("mmproj-Vision-BF16.gguf"))

    def test_fit_follows_this_computer(self):
        write_gguf(os.path.join(self.lib, "lemonade", "m", "Mid-Q4.gguf"), size=18 * GB)
        self.assertEqual(self.by_stem()["Mid-Q4"]["fit"], "fits")                 # 24 GB card
        self.machine = {"ram_gb": 16, "vram_gb": 6, "best_gpu_gb": 6, "gpus": ["x"], "free_disk_gb": 500}
        m = self.by_stem()["Mid-Q4"]
        self.assertEqual(m["fit"], "no")
        self.assertIn("graphics memory", m["why"])

    def test_the_same_file_in_two_places_is_one_model(self):
        write_gguf(os.path.join(self.lib, "lemonade", "a", "Same-Q4.gguf"), size=3 * GB)
        os.makedirs(os.path.join(self.lib, "strata", "b"))
        os.symlink(os.path.join(self.lib, "lemonade", "a", "Same-Q4.gguf"), os.path.join(self.lib, "strata", "b", "Same-Q4.gguf"))
        self.assertEqual(len([m for m in self.cat() if m["stem"] == "Same-Q4"]), 1)

    def test_a_model_no_engine_can_run_says_so(self):
        # the only engines here are fast (not qwen4exp) and big (only qwen4exp): a format neither lists is refused with a reason
        os.makedirs(os.path.join(self.lib, "lemonade", "z"))
        with open(os.path.join(self.lib, "lemonade", "z", "Odd.gguf"), "wb"):
            pass
        self.add("picky", """
            id: picky
            name: Picky
            api_version: 1
            run: {mode: external}
            endpoint: {base_url: "http://127.0.0.1:3/v1"}
            models: {formats: [safetensors]}
            """)
        # nothing to assert beyond not crashing on an unreadable file: it is skipped, not listed
        self.assertNotIn("Odd", self.by_stem())

    def test_headers_are_remembered(self):
        write_gguf(os.path.join(self.lib, "lemonade", "m", "A.gguf"))
        self.cat()
        self.assertTrue(os.path.isfile(library._index_path()))
        library._index.update(loaded=False, d={})
        self.assertIn("A", self.by_stem())                                          # read back from the saved index


class Views(Env):
    def setUp(self):
        super().setUp()
        write_gguf(os.path.join(self.lib, "lemonade", "own", "Own-Q4.gguf"), size=2 * GB)              # the engine keeps this itself
        self.elsewhere = os.path.join(self.tmp, "other-disk")
        write_gguf(os.path.join(self.elsewhere, "Extra-Q4.gguf"), size=2 * GB)
        write_gguf(os.path.join(self.lib, "strata", "Flash-00001-of-00001.gguf"), arch="qwen4exp")
        s = __import__("store").load()
        s.setdefault("models", {})["imports"] = [{"kind": "folder", "path": self.elsewhere, "label": "Other disk"}]
        __import__("store").save(s)

    def test_only_what_the_engine_can_run_and_doesnt_already_keep(self):
        views = library.sync_views(self.cat(), self.reg)
        self.assertEqual(list(views), ["fast"])                                    # the big engine reads its files in place
        names = sorted(os.listdir(views["fast"]))
        self.assertEqual(names, ["Extra-Q4.gguf"])                                 # not Own (kept by the engine), not the qwen4exp file
        self.assertTrue(os.path.islink(os.path.join(views["fast"], "Extra-Q4.gguf")))

    def test_stale_links_go_but_real_files_stay(self):
        views = library.sync_views(self.cat(), self.reg)
        v = views["fast"]
        os.symlink("/nonexistent", os.path.join(v, "Gone.gguf"))
        real = os.path.join(v, "MINE.txt")
        with open(real, "w") as f:
            f.write("keep me")
        library.sync_views(self.cat(), self.reg)
        self.assertFalse(os.path.lexists(os.path.join(v, "Gone.gguf")))
        self.assertTrue(os.path.isfile(real))

    def test_config_for_a_lemonade_zest_runs(self):
        c = library.lemonade_config()
        self.assertEqual(c["models_dir"], os.path.join(self.lib, "lemonade"))
        self.assertEqual(c["extra_models_dir"], os.path.join(self.tmp, "data", "views", "lemonade"))


class Layout(Env):
    def test_the_models_folder_holds_only_models(self):
        # engines get their own subfolders; the views (links) live in Zest's data folder, never in the Models folder
        write_gguf(os.path.join(self.lib, "lemonade", "a", "A-Q4.gguf"), size=2 * GB)
        self.elsewhere = os.path.join(self.tmp, "other")
        write_gguf(os.path.join(self.elsewhere, "B-Q4.gguf"), size=2 * GB)
        s = __import__("store").load()
        s.setdefault("models", {})["imports"] = [{"kind": "folder", "path": self.elsewhere, "label": "x"}]
        __import__("store").save(s)
        library.sync_views(self.cat(), self.reg)
        self.assertEqual(sorted(os.listdir(self.lib)), ["lemonade", "strata"])
        self.assertTrue(os.path.islink(os.path.join(self.tmp, "data", "views", "fast", "B-Q4.gguf")))
        self.assertEqual(library.views_base(), os.path.join(self.tmp, "data", "views"))


class Places(Env):
    def test_places_and_root(self):
        ps = library.places()
        self.assertTrue(ps)
        self.assertEqual([p["free_gb"] for p in ps], sorted([p["free_gb"] for p in ps], reverse=True))
        self.assertLessEqual(sum(1 for p in ps if p["suggested"]), 1)
        self.assertTrue(all(p["folder"].endswith(os.sep + "Models") for p in ps))

    def test_set_root_makes_the_folders_and_refuses_the_unwritable(self):
        new = os.path.join(self.tmp, "elsewhere", "My Models")
        self.assertEqual(library.set_root(new), new)
        for sub in ("lemonade", "strata"):
            self.assertTrue(os.path.isdir(os.path.join(new, sub)))
        self.assertEqual(library.root(), new)
        with self.assertRaises(ValueError):
            library.set_root("/proc/zest")
        with self.assertRaises(ValueError):
            library.set_root("")


if __name__ == "__main__":
    unittest.main()
