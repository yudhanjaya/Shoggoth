"""The move from 4.x (Lemonade) to 5.x: links to Lemonade's models, the alias map, carried options, never deleting anything."""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shoggoth"))
import llamacpp  # noqa: E402
import migrate5  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "lemonade4")


def hub_model(hub, repo, files, rev="abc123"):
    """A Hugging Face hub cache entry: blobs/<hash> files with snapshots/<rev>/<name> links to them."""
    d = os.path.join(hub, "models--" + repo.replace("/", "--"))
    os.makedirs(os.path.join(d, "blobs"), exist_ok=True)
    snap = os.path.join(d, "snapshots", rev)
    os.makedirs(snap, exist_ok=True)
    for name, size in files.items():
        blob = os.path.join(d, "blobs", "sha-" + name)
        with open(blob, "wb") as f:
            f.write(b"G" * size)
        os.symlink(os.path.join("..", "..", "blobs", "sha-" + name), os.path.join(snap, name))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data = os.path.join(self.tmp, "data")
        self.lib = os.path.join(self.tmp, "Models")
        self.hub = os.path.join(self.tmp, "hf", "hub")
        shutil.copytree(os.path.join(FIXTURE, "lemonade-data"), os.path.join(self.data, "lemonade-data"))
        with open(os.path.join(self.data, "lemonade-data", "lemond-backend.bin"), "wb") as f:
            f.write(b"E" * 5000)                                                   # an engine file Lemonade downloaded
        hub_model(self.hub, "unsloth/MiMo-V2-Flash-GGUF", {"MiMo-V2-Flash-Q4_K_M.gguf": 4000, "MiMo-V2-Flash-Q8_0.gguf": 7000})
        lhub = os.path.join(self.lib, "lemonade")
        hub_model(lhub, "unsloth/Qwen3.6-35B-A3B-MTP-GGUF", {"Qwen3.6-35B-A3B-MTP-Q4_K_XL.gguf": 6000, "mmproj-F16.gguf": 500})
        hub_model(lhub, "bartowski/Big-GGUF", {"Big-Q4_K_M-00001-of-00002.gguf": 3000, "Big-Q4_K_M-00002-of-00002.gguf": 3000})
        env = mock.patch.dict(os.environ, {"HF_HUB_CACHE": self.hub})
        env.start()
        self.addCleanup(env.stop)
        self.logs = []

    def run_it(self):
        return migrate5.run(self.data, self.lib, log=self.logs.append)

    def sources(self):
        """Every real file Lemonade holds: {path: (inode, size)}."""
        out = {}
        for root in (self.hub, os.path.join(self.lib, "lemonade"), os.path.join(self.data, "lemonade-data")):
            for dp, _, fns in os.walk(root):
                for fn in fns:
                    p = os.path.join(dp, fn)
                    st = os.stat(p)
                    out[p] = (st.st_ino, st.st_size)
        return out

    def view(self, *p):
        return os.path.join(self.data, "views", "llamacpp", *p)


class SharedCache(Base):
    def test_fresh_profile_links_nothing_from_the_shared_cache(self):
        shutil.rmtree(os.path.join(self.data, "lemonade-data"))          # a profile where Lemonade never ran
        shutil.rmtree(os.path.join(self.lib, "lemonade"))
        hub_model(self.hub, "nvidia/parakeet-GGUF", {"parakeet-Q8_0.gguf": 100})
        self.run_it()
        self.assertFalse(os.path.exists(self.view("MiMo-V2-Flash-Q4_K_M.gguf")))
        self.assertFalse(os.path.exists(self.view("parakeet-Q8_0.gguf")))

    def test_shared_cache_keeps_only_repos_lemonade_knew(self):
        hub_model(self.hub, "nvidia/parakeet-GGUF", {"parakeet-Q8_0.gguf": 100})   # another app's download
        self.run_it()
        self.assertTrue(os.path.isfile(self.view("MiMo-V2-Flash-Q4_K_M.gguf")))     # named in user_models.json
        self.assertFalse(os.path.exists(self.view("parakeet-Q8_0.gguf")))


class Linking(Base):
    def test_models_linked_with_new_ids(self):
        self.run_it()
        self.assertTrue(os.path.isfile(self.view("MiMo-V2-Flash-Q4_K_M.gguf")))
        self.assertTrue(os.path.isfile(self.view("MiMo-V2-Flash-Q8_0.gguf")))
        # a vision file and a split model each go in a folder named by the id, as the router wants
        self.assertTrue(os.path.isfile(self.view("Qwen3.6-35B-A3B-MTP-Q4_K_XL", "Qwen3.6-35B-A3B-MTP-Q4_K_XL.gguf")))
        self.assertTrue(os.path.isfile(self.view("Qwen3.6-35B-A3B-MTP-Q4_K_XL", "mmproj-F16.gguf")))
        self.assertTrue(os.path.isfile(self.view("Big-Q4_K_M", "Big-Q4_K_M-00002-of-00002.gguf")))

    def test_hard_links_on_same_disk(self):
        self.run_it()
        link = self.view("MiMo-V2-Flash-Q4_K_M.gguf")
        self.assertFalse(os.path.islink(link))
        self.assertEqual(os.stat(link).st_nlink, 2)

    def test_symlinks_when_hard_links_fail(self):
        with mock.patch("os.link", side_effect=OSError(18, "cross-device")):
            self.run_it()
        link = self.view("MiMo-V2-Flash-Q4_K_M.gguf")
        self.assertTrue(os.path.islink(link))
        self.assertTrue(os.path.isfile(link))

    def test_never_deletes_or_changes_sources(self):
        before = self.sources()
        self.run_it()
        self.run_it()
        after = self.sources()
        for p, meta in before.items():
            self.assertIn(p, after, "source file gone: " + p)
            self.assertEqual(after[p], meta)
        for p in before:
            self.assertTrue(os.path.exists(p))

    def test_never_deletes_sources_even_if_views_removed(self):
        before = self.sources()
        self.run_it()
        shutil.rmtree(os.path.join(self.data, "views"))
        self.assertEqual(before, self.sources())

    def test_a_different_file_with_the_same_name_is_left_alone(self):
        os.makedirs(self.view())
        with open(self.view("MiMo-V2-Flash-Q4_K_M.gguf"), "wb") as f:
            f.write(b"mine")
        self.run_it()
        with open(self.view("MiMo-V2-Flash-Q4_K_M.gguf"), "rb") as f:
            self.assertEqual(f.read(), b"mine")


class Aliases(Base):
    def aliases(self):
        self.run_it()
        with open(os.path.join(self.data, "model-aliases.json")) as f:
            return json.load(f)

    def test_user_model_name(self):
        self.assertEqual(self.aliases()["user.MiMo-V2-Flash-Q4_K_M"], "MiMo-V2-Flash-Q4_K_M")

    def test_catalog_name_maps_to_repo_and_quant(self):
        self.assertEqual(self.aliases()["Qwen3.6-35B-A3B-MTP-GGUF"], "Qwen3.6-35B-A3B-MTP-Q4_K_XL")

    def test_same_id_format_as_hf(self):
        import hf
        self.assertEqual(hf.model_id("unsloth/Qwen3.6-35B-A3B-MTP-GGUF", "Q4_K_XL"), "Qwen3.6-35B-A3B-MTP-Q4_K_XL")
        self.assertEqual(self.aliases()["user.Big-GGUF-Q4_K_M"], "Big-Q4_K_M")

    def test_ambiguous_bare_repo_name_is_not_guessed(self):
        self.assertNotIn("MiMo-V2-Flash-GGUF", self.aliases())          # two quants of that repo are here

    def test_model_that_is_not_here_gets_no_alias(self):
        self.assertNotIn("user.Gone-Model", self.aliases())
        self.assertNotIn("Gone-Model", self.aliases())

    def test_resolve(self):
        self.run_it()
        self.assertEqual(migrate5.resolve("user.MiMo-V2-Flash-Q4_K_M", self.data), "MiMo-V2-Flash-Q4_K_M")
        self.assertEqual(migrate5.resolve("llamacpp/user.MiMo-V2-Flash-Q4_K_M", self.data), "llamacpp/MiMo-V2-Flash-Q4_K_M")
        self.assertEqual(migrate5.resolve("Something-Else", self.data), "Something-Else")
        self.assertEqual(migrate5.resolve("MiMo-V2-Flash-Q4_K_M", self.data), "MiMo-V2-Flash-Q4_K_M")
        self.assertEqual(migrate5.resolve("", self.data), "")

    def test_resolve_without_a_file(self):
        self.assertEqual(migrate5.resolve("user.x", os.path.join(self.tmp, "nowhere")), "user.x")


class Options(Base):
    def presets(self):
        self.run_it()
        return llamacpp.read_presets(os.path.join(self.data, "llamacpp", "presets.ini"))

    def test_context_and_args_carried_under_new_id(self):
        p = self.presets()["MiMo-V2-Flash-Q4_K_M"]
        self.assertEqual(p["ctx-size"], "65536")
        self.assertEqual(p["n-gpu-layers"], "99")
        self.assertEqual(p["threads"], "6")
        self.assertEqual(p["no-mmap"], "true")

    def test_invalid_args_dropped_and_logged(self):
        p = self.presets()["MiMo-V2-Flash-Q4_K_M"]
        self.assertNotIn("port", p)
        self.assertNotIn("tool-call-parser", p)
        text = "\n".join(self.logs)
        self.assertIn("--port", text)
        self.assertIn("--tool-call-parser", text)
        self.assertIn("vulkan", text)

    def test_default_context_is_not_carried_but_args_are(self):
        p = self.presets()["Qwen3.6-35B-A3B-MTP-Q4_K_XL"]
        self.assertNotIn("ctx-size", p)
        self.assertEqual(p["cache-type-k"], "q8_0")

    def test_options_of_a_missing_model_skipped(self):
        self.assertNotIn("Gone-Model", self.presets())
        self.assertIn("Gone-Model", "\n".join(self.logs))

    def test_existing_preset_values_win(self):
        path = os.path.join(self.data, "llamacpp", "presets.ini")
        llamacpp.write_presets({"MiMo-V2-Flash-Q4_K_M": {"ctx-size": 8192}}, {"flash-attn": "off"}, path=path)
        p = self.presets()
        self.assertEqual(p["MiMo-V2-Flash-Q4_K_M"]["ctx-size"], "8192")
        self.assertEqual(p["MiMo-V2-Flash-Q4_K_M"]["threads"], "6")
        self.assertEqual(p["*"]["flash-attn"], "off")

    def test_args_to_preset(self):
        got, dropped = migrate5.args_to_preset("-c 4096 -fa on --override-tensor 'a=b' --model x.gguf")
        self.assertEqual(got, {"ctx-size": "4096", "flash-attn": "on", "override-tensor": "a=b"})
        self.assertEqual(len(dropped), 1)
        self.assertEqual(migrate5.args_to_preset("--oops 'unclosed")[0], {})

    def test_known_flags_check_when_a_binary_is_given(self):
        with mock.patch("llamaargs.known_flags", return_value={"--threads", "-ngl"}):
            got, dropped = migrate5.args_to_preset("--threads 4 --nonsense 1", "/some/llama-server")
        self.assertEqual(got, {"threads": "4"})
        self.assertIn("--nonsense", dropped[0])


class Lemond(Base):
    def spawn(self):
        script = os.path.join(self.tmp, "lemond")
        with open(script, "w") as f:
            f.write("#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n")
        os.chmod(script, 0o755)
        proc = subprocess.Popen([script, os.path.join(self.data, "lemonade-data"), "--port", "13307"], start_new_session=True)
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.wait()))
        time.sleep(0.3)
        return proc

    def test_stops_the_one_shoggoth_started(self):
        proc = self.spawn()
        with open(os.path.join(self.data, "lemond.pid"), "w") as f:
            f.write(str(proc.pid))
        s = self.run_it()
        proc.wait(timeout=10)
        self.assertTrue(s["lemond_stopped"])
        self.assertFalse(os.path.exists(os.path.join(self.data, "lemond.pid")))

    def test_leaves_one_the_person_runs(self):
        proc = self.spawn()                                            # no pid file: Shoggoth did not start it
        s = self.run_it()
        self.assertFalse(s["lemond_stopped"])
        self.assertIsNone(proc.poll())

    def test_does_not_kill_an_unrelated_process_with_a_recycled_pid(self):
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(lambda: (other.kill(), other.wait()))
        with open(os.path.join(self.data, "lemond.pid"), "w") as f:
            f.write(str(other.pid))
        s = self.run_it()
        self.assertFalse(s["lemond_stopped"])
        self.assertIsNone(other.poll())


class Summary(Base):
    def test_lemonade_gb_counts_engine_files_not_linked_models(self):
        s = self.run_it()
        self.assertTrue(5000 <= s["lemonade_bytes"] < 6000, s["lemonade_bytes"])                                   # only the engine file (models have hard links)
        self.assertEqual(s["lemonade_gb"], 0.0)
        self.assertEqual(s["depend_on_lemonade"], 0)

    def test_symlinked_models_are_reported_as_depending_on_lemonade(self):
        with mock.patch("os.link", side_effect=OSError(18, "cross-device")):
            s = self.run_it()
        self.assertGreater(s["depend_on_lemonade"], 0)

    def test_summary_fields(self):
        s = self.run_it()
        self.assertEqual(s["models"], 4)
        self.assertTrue(s["done"])
        self.assertTrue(os.path.isfile(os.path.join(self.data, "migrate5.json")))

    def test_one_log_line_per_step(self):
        self.run_it()
        text = "\n".join(self.logs)
        for word in ("Linked", "old-to-new", "per-model options", "Lemonade", "own files take"):
            self.assertIn(word, text)


class Idempotent(Base):
    def test_second_run_changes_nothing(self):
        first = self.run_it()
        snap = {}
        for root in (self.data,):
            for dp, _, fns in os.walk(root):
                for fn in fns:
                    p = os.path.join(dp, fn)
                    if fn != "migrate5.json":
                        snap[p] = (os.stat(p).st_ino, os.stat(p).st_mtime_ns)
        self.logs.clear()
        second = self.run_it()
        for p, meta in snap.items():
            st = os.stat(p)
            self.assertEqual((st.st_ino, st.st_mtime_ns), meta, p)
        for k in ("models", "linked", "aliases", "presets", "lemonade_gb"):
            self.assertEqual(first[k], second[k])
        self.assertIn("done before", "\n".join(self.logs))

    def test_marker_records_steps(self):
        self.run_it()
        with open(os.path.join(self.data, "migrate5.json")) as f:
            m = json.load(f)
        self.assertEqual(sorted(m["steps"]), sorted(migrate5.STEPS))

    def test_no_lemonade_at_all(self):
        empty = os.path.join(self.tmp, "fresh")
        with mock.patch.dict(os.environ, {"HF_HUB_CACHE": os.path.join(self.tmp, "none")}):
            s = migrate5.run(empty, "", log=self.logs.append)
        self.assertEqual(s["models"], 0)
        self.assertEqual(s["lemonade_gb"], 0)
        self.assertTrue(s["done"])


if __name__ == "__main__":
    unittest.main()
