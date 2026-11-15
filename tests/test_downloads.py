"""The page's model list, downloads, delete and per-model options (downloads.py), with library, hf and llama.cpp stood in for.
Run: python3 -m unittest discover tests"""
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import downloads  # noqa: E402
import hf  # noqa: E402
import llamacpp  # noqa: E402

REC = [{"id": "Tiny-4B", "also": [], "title": "Tiny 4B", "size_gb": 2.5, "vision": False,
        "download": {"checkpoint": "acme/Tiny-4B-GGUF:Q4_K_M", "mmproj": "", "recipe": "llamacpp"}},
       {"id": "Big-30B", "also": [], "title": "Big 30B", "size_gb": 18.0, "vision": True,
        "download": {"checkpoint": "acme/Big-30B-GGUF:Big-30B-Q4_K_M.gguf", "mmproj": "", "recipe": "llamacpp"}}]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"SHOGGOTH_DATA_DIR": os.path.join(self.tmp, "data"),
                                                "SHOGGOTH_CONFIG_DIR": os.path.join(self.tmp, "cfg")})
        self.env.start()
        self.patches = [mock.patch("downloads.recs.models", return_value={"recommended": REC}),
                        mock.patch("downloads.library.root", return_value=""),
                        mock.patch("downloads.library.reload_engine", return_value=(True, "")),
                        mock.patch("downloads.llamacpp.running", return_value=False),
                        mock.patch("downloads.llamacpp.loaded_ids", return_value=[])]
        for p in self.patches:
            p.start()
        downloads._jobs.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def file(self, *parts, n=1000):
        p = os.path.join(downloads.dest_dir(), *parts)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(b"x" * n)
        return p


class Listing(Base):
    def test_recommended_models_not_here_are_listed_with_their_size(self):
        got = {m["id"]: m for m in downloads.listing([])}
        self.assertEqual(got["Tiny-4B"]["recipe"], "llamacpp")
        self.assertEqual(got["Tiny-4B"]["size"], 2.5)
        self.assertFalse(got["Tiny-4B"]["downloaded"])
        self.assertIn("vision", got["Big-30B"]["labels"])

    def test_a_downloaded_recommended_model_takes_the_router_id_and_names_its_pick(self):
        f = self.file("acme", "Tiny-4B-GGUF", "Tiny-4B-Q4_K_M.gguf")
        e = {"provider": "llamacpp", "ref": "Tiny-4B-Q4_K_M", "stem": "Tiny-4B-Q4_K_M", "files": [f], "quant": "Q4_K_M", "size_bytes": 2_400_000_000}
        out = downloads.listing([e])
        self.assertEqual([m["id"] for m in out], ["Tiny-4B-Q4_K_M", "Big-30B"])
        self.assertEqual(out[0]["rec"], "Tiny-4B")
        self.assertTrue(out[0]["downloaded"] and out[0]["owned"])

    def test_a_model_matched_by_its_checkpoint_file(self):
        f = self.file("acme", "Big-30B-GGUF", "Big-30B-Q4_K_M.gguf")
        e = {"provider": "llamacpp", "ref": "Big-30B-Q4_K_M", "stem": "Big-30B-Q4_K_M", "files": [f], "size_bytes": 1}
        self.assertEqual(downloads.listing([e])[0]["rec"], "Big-30B")

    def test_a_model_read_from_another_folder_is_not_owned(self):
        e = {"provider": "llamacpp", "ref": "Mine", "stem": "Mine", "files": ["/somewhere/else/Mine.gguf"], "size_bytes": 1, "folder": "/somewhere/else"}
        m = downloads.listing([e])[0]
        self.assertFalse(m["owned"])
        self.assertEqual(m["folder"], "/somewhere/else")

    def test_models_other_engines_serve_are_left_out(self):
        e = {"provider": "ollama", "ref": "x", "files": [], "size_bytes": 1}
        self.assertNotIn("x", [m["id"] for m in downloads.listing([e])])


class Pulling(Base):
    def test_a_recommended_model_needs_only_its_name(self):
        seen = {}
        def run(j, repo, what, mm):
            seen.update(repo=repo, what=what)
            downloads._end(j, "completed", "")
        j = downloads.pull({"model_name": "Tiny-4B"}, run=run)
        time.sleep(0.05)
        self.assertEqual(j["id"], "model:Tiny-4B")
        self.assertEqual(seen, {"repo": "acme/Tiny-4B-GGUF", "what": "Q4_K_M"})
        self.assertEqual(downloads.jobs()[0]["status"], "completed")

    def test_a_vision_file_written_as_repo_and_file_is_fetched_by_its_file_name(self):
        seen = {}
        def run(j, repo, what, mm):
            seen.update(repo=repo, mm=mm)
            downloads._end(j, "completed", "")
        downloads.pull({"model_name": "V", "checkpoint": "acme/V-GGUF:Q8_0", "mmproj": "acme/V-GGUF:mmproj-V-Q8_0.gguf"}, run=run)
        time.sleep(0.05)
        self.assertEqual(seen, {"repo": "acme/V-GGUF", "mm": "mmproj-V-Q8_0.gguf"})
        with self.assertRaisesRegex(ValueError, "same repository"):
            downloads.pull({"model_name": "W", "checkpoint": "acme/V-GGUF:Q8_0", "mmproj": "other/X-GGUF:mmproj.gguf"})

    def test_a_model_with_no_checkpoint_is_refused_in_words(self):
        with self.assertRaisesRegex(ValueError, "doesn't know where to download"):
            downloads.pull({"model_name": "Nope"})

    def test_a_bad_repo_name_is_refused(self):
        with self.assertRaisesRegex(ValueError, "isn't a Hugging Face model name"):
            downloads.pull({"model_name": "x", "checkpoint": "../etc:Q4"})

    def test_the_download_lands_in_shoggoths_models_folder_then_llamacpp_reads_it(self):
        calls = {}
        def fake(repo, what, dest, progress=None, cancel=None, mmproj=False):
            calls.update(repo=repo, what=what, dest=dest)
            progress(50, 100)
            progress(100, 100)
        with mock.patch("downloads.hf.download", side_effect=fake):
            downloads.pull({"model_name": "Tiny-4B-Q8_0", "checkpoint": "acme/Tiny-4B-GGUF:Q8_0"})
            for _ in range(50):
                if downloads.jobs()[0]["status"] != "downloading":
                    break
                time.sleep(0.02)
        self.assertEqual(calls["dest"], os.path.join(os.environ["SHOGGOTH_DATA_DIR"], "models"))
        j = downloads.jobs()[0]
        self.assertEqual((j["status"], j["percent"]), ("completed", 100))
        downloads.library.reload_engine.assert_called()

    def test_a_failed_download_says_why(self):
        with mock.patch("downloads.hf.download", side_effect=hf.HFError("Hugging Face said no.")):
            downloads.pull({"model_name": "Tiny-4B"})
            time.sleep(0.1)
        j = downloads.jobs()[0]
        self.assertEqual((j["status"], j["error"]), ("error", "Hugging Face said no."))

    def test_cancel_stops_it_and_remove_forgets_it_and_the_part_files(self):
        go = threading.Event()
        def slow(repo, what, dest, progress=None, cancel=None, mmproj=False):
            os.makedirs(os.path.join(dest, "acme", "Tiny-4B-GGUF"), exist_ok=True)
            open(os.path.join(dest, "acme", "Tiny-4B-GGUF", "Tiny-4B-Q4_K_M.gguf.part"), "w").close()
            go.set()
            cancel.wait(2)
            raise hf.HFError("cancelled", cancelled=True)
        with mock.patch("downloads.hf.download", side_effect=slow):
            downloads.pull({"model_name": "Tiny-4B"})
            go.wait(2)
            downloads.control({"id": "model:Tiny-4B", "action": "cancel"})
            self.assertEqual(downloads.jobs()[0]["status"], "cancelled")
            downloads.control({"id": "model:Tiny-4B", "action": "remove"})
        self.assertEqual(downloads.jobs(), [])
        self.assertFalse(os.path.exists(os.path.join(downloads.dest_dir(), "acme", "Tiny-4B-GGUF", "Tiny-4B-Q4_K_M.gguf.part")))


class Deleting(Base):
    def test_a_model_shoggoth_downloaded_is_deleted_and_its_empty_folder_goes(self):
        f = self.file("acme", "Tiny-4B-GGUF", "Tiny-4B-Q4_K_M.gguf")
        r = downloads.delete("Tiny-4B-Q4_K_M", [{"ref": "Tiny-4B-Q4_K_M", "files": [f]}])
        self.assertTrue(r["ok"])
        self.assertFalse(os.path.exists(os.path.dirname(f)))
        self.assertTrue(os.path.isdir(downloads.dest_dir()))

    def test_a_model_in_someone_elses_folder_is_never_deleted(self):
        other = os.path.join(self.tmp, "mine", "Mine.gguf")
        os.makedirs(os.path.dirname(other))
        open(other, "w").close()
        with self.assertRaisesRegex(ValueError, "only reads"):
            downloads.delete("Mine", [{"ref": "Mine", "files": [other], "folder": os.path.dirname(other)}])
        self.assertTrue(os.path.exists(other))

    def test_a_link_into_shoggoths_folder_does_not_count_as_shoggoths(self):
        real = os.path.join(self.tmp, "mine", "Real.gguf")
        os.makedirs(os.path.dirname(real))
        open(real, "w").close()
        link = os.path.join(downloads.dest_dir(), "Real.gguf")
        os.makedirs(os.path.dirname(link), exist_ok=True)
        os.symlink(real, link)
        with self.assertRaises(ValueError):
            downloads.delete("Real", [{"ref": "Real", "files": [link]}])
        self.assertTrue(os.path.exists(real))

    def test_a_loaded_model_is_unloaded_first(self):
        f = self.file("Tiny.gguf")
        with mock.patch("downloads.llamacpp.loaded_ids", return_value=["Tiny"]), mock.patch("downloads.llamacpp.unload", return_value=(True, "")) as u:
            downloads.delete("Tiny", [{"ref": "Tiny", "files": [f]}])
        u.assert_called_once_with("Tiny")


class Options(Base):
    def test_options_round_trip_through_presets_ini(self):
        downloads.set_options("Tiny", {"ctx_size": 16384, "llamacpp_args": "--n-gpu-layers 30 --no-mmap"})
        sec = llamacpp.read_presets()["Tiny"]
        self.assertEqual(sec, {"ctx-size": "16384", "n-gpu-layers": "30", "no-mmap": "true"})
        o = downloads.options("Tiny")
        self.assertEqual(o["effective"]["ctx_size"], 16384)
        self.assertEqual(o["saved"]["llamacpp_args"], "--n-gpu-layers 30 --no-mmap")
        self.assertIn("*", llamacpp.read_presets(), "the defaults section stays")

    def test_null_goes_back_to_the_default(self):
        downloads.set_options("Tiny", {"ctx_size": 16384})
        o = downloads.set_options("Tiny", {"ctx_size": None})
        self.assertEqual(o["effective"]["ctx_size"], llamacpp.DEFAULTS["ctx-size"])
        self.assertNotIn("Tiny", llamacpp.read_presets())

    def test_options_shoggoth_sets_itself_are_refused(self):
        with self.assertRaisesRegex(ValueError, "Context size"):
            downloads.set_options("Tiny", {"llamacpp_args": "--ctx-size 4096"})
        with self.assertRaisesRegex(ValueError, "itself"):
            downloads.set_options("Tiny", {"llamacpp_args": "--model /etc/passwd"})

    def test_extra_options_keep_the_automatic_context(self):
        downloads.set_options("Tiny", {"ctx_size": 8192})
        downloads.set_options("Tiny", {"llamacpp_args": "--threads 4"})
        self.assertEqual(llamacpp.read_presets()["Tiny"], {"ctx-size": "8192", "threads": "4"})


if __name__ == "__main__":
    unittest.main()
