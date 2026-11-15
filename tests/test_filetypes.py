"""S8: file types and attachments, server side (master plan 6.2).
Run: python3 -W ignore -m unittest discover tests"""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.parse
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "shoggoth"))

import agent  # noqa: E402
import filetypes  # noqa: E402
import server  # noqa: E402
import store  # noqa: E402
import uiauth  # noqa: E402


class Handlers(unittest.TestCase):
    """The extension decides the handler, and a handler that needs a tool says whether the tool is there."""

    def setUp(self):
        self._installed = filetypes.installed
        filetypes.installed = lambda tool: False

    def tearDown(self):
        filetypes.installed = self._installed

    def test_text_and_code_are_read_directly(self):
        for name in ("notes.txt", "README", ".env", "README.md", "rows.csv", "data.json", "page.html", "app.py",
                     "code.js", "config.yaml"):
            self.assertEqual(filetypes.handler_for(name), "text", name)
            self.assertEqual(filetypes.describe(name)["label"], "Read as text")

    def test_images_go_to_a_vision_model(self):
        for name in ("photo.PNG", "a.jpg", "b.jpeg", "c.webp", "d.gif", "e.bmp", "f.tiff"):
            self.assertEqual(filetypes.handler_for(name), "image", name)
        self.assertTrue(filetypes.describe("photo.png")["available"])

    def test_a_pdf_is_read_as_text_when_pdftotext_is_there(self):
        filetypes.installed = lambda tool: tool == "pdftotext"
        d = filetypes.describe("paper.pdf")
        self.assertEqual((d["handler"], d["available"], d["needs"]), ("pdf-text", True, "pdftotext"))
        self.assertEqual(d["label"], "PDF, read as text")

    def test_a_pdf_with_only_pdftoppm_is_rendered_to_images(self):
        filetypes.installed = lambda tool: tool == "pdftoppm"
        d = filetypes.describe("scan.pdf")
        self.assertEqual((d["handler"], d["available"], d["needs"]), ("pdf-scan", True, "pdftoppm"))

    def test_a_missing_pdftotext_is_reported(self):
        d = filetypes.describe("paper.pdf")
        self.assertEqual((d["handler"], d["available"], d["needs"]), ("pdf-text", False, "pdftotext"))
        self.assertIn("pdftotext", d["label"])

    def test_a_pdf_prefers_text_when_both_tools_are_there(self):
        filetypes.installed = lambda tool: True
        self.assertEqual(filetypes.handler_for("paper.pdf"), "pdf-text")

    def test_sheets_and_word_need_a_tool_that_is_not_installed(self):
        for name, handler in (("budget.xlsx", "sheet"), ("old.xls", "sheet"), ("table.ods", "sheet"),
                              ("letter.docx", "docx"), ("old.doc", "docx")):
            d = filetypes.describe(name)
            self.assertEqual(d["handler"], handler, name)
            self.assertFalse(d["available"], name)
            self.assertIn("needs a tool that isn't installed", d["label"], name)

    def test_an_unknown_type_is_not_claimed_as_text(self):
        d = filetypes.describe("thing.exe")
        self.assertEqual(d["handler"], "unsupported")
        self.assertFalse(d["available"])

    def test_labels_are_plain(self):
        for name in ("a.txt", "a.png", "a.pdf", "a.xlsx", "a.docx", "a.exe"):
            label = filetypes.describe(name)["label"]
            self.assertTrue(label and "!" not in label and label == label.strip(), label)


class Names(unittest.TestCase):
    def test_a_plain_name_is_kept(self):
        self.assertEqual(filetypes.clean_name("report final.pdf"), "report final.pdf")

    def test_a_path_is_refused(self):
        for name in ("../escape.txt", "../../etc/passwd", "sub/file.txt", "sub\\file.txt", "/etc/passwd",
                     "C:\\Windows\\x.txt", ".", ".."):
            with self.assertRaises(ValueError, msg=repr(name)):
                filetypes.clean_name(name)

    def test_controls_and_bidi_are_refused(self):
        for name in ("a\x00b.txt", "bell\x07.txt", "\u202eevil.txt", " new\nline.txt", "", None, "x" * 200, "   "):
            with self.assertRaises(ValueError, msg=repr(name)):
                filetypes.clean_name(name)

    def test_ordinary_unicode_and_dots_are_fine(self):
        for name in ("résumé.pdf", ".env", "v1.2 notes.md", "photo (1).png"):
            self.assertEqual(filetypes.clean_name(name), name)


class Saving(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.folder = os.path.join(self.tmp, "attachments")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_file_lands_in_attachments(self):
        p = filetypes.save(self.folder, "notes.txt", b"hello")
        self.assertEqual(p, os.path.join(self.folder, "notes.txt"))
        with open(p, "rb") as f:
            self.assertEqual(f.read(), b"hello")

    def test_a_collision_gets_a_suffix(self):
        a = filetypes.save(self.folder, "notes.txt", b"one")
        b = filetypes.save(self.folder, "notes.txt", b"two")
        c = filetypes.save(self.folder, "notes.txt", b"three")
        self.assertEqual([os.path.basename(x) for x in (a, b, c)], ["notes.txt", "notes-1.txt", "notes-2.txt"])
        with open(b, "rb") as f:
            self.assertEqual(f.read(), b"two")

    def test_the_size_limit_is_fifty_megabytes(self):
        self.assertEqual(filetypes.MAX_BYTES, 50 * 1024 * 1024)
        with mock.patch.object(filetypes, "MAX_BYTES", 8):
            filetypes.save(self.folder, "small.txt", b"12345678")
            with self.assertRaises(ValueError):
                filetypes.save(self.folder, "big.txt", b"123456789")
        self.assertFalse(os.path.exists(os.path.join(self.folder, "big.txt")))

    def test_a_bad_name_writes_nothing(self):
        with self.assertRaises(ValueError):
            filetypes.save(self.folder, "../escape.txt", b"x")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escape.txt")))


class Placement(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR")}
        os.environ["SHOGGOTH_DATA_DIR"] = os.path.join(self.tmp, "data")
        os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "config")
        self.project = os.path.join(self.tmp, "project")
        os.makedirs(self.project)

    def tearDown(self):
        for k, v in self._env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_chat_with_a_folder_gets_an_attachments_folder_in_it(self):
        t = agent.create(self.project)
        out = filetypes.save_for_chat(t, "notes.txt", b"hi")
        self.assertEqual(out["path"], os.path.join(self.project, "attachments", "notes.txt"))
        self.assertEqual((out["handler"], out["label"]), ("text", "Read as text"))

    def test_a_chat_without_a_folder_uses_shoggoths_scratch_folder(self):
        t = agent.create("")
        out = filetypes.save_for_chat(t, "notes.txt", b"hi")
        self.assertTrue(out["path"].startswith(os.path.join(store.data_dir(), "cowork", "scratch", t["id"], "attachments")))
        self.assertTrue(os.path.isfile(out["path"]))

    def test_a_missing_chat_is_refused(self):
        with self.assertRaises(ValueError):
            filetypes.save_for_chat(None, "notes.txt", b"hi")

    def test_a_job_is_refused(self):
        with self.assertRaises(ValueError):
            filetypes.save_for_chat({"id": "job1", "kind": "job", "cwd": self.project, "job": {}}, "notes.txt", b"hi")


class Routes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR", "SHOGGOTH_AUTH", "SHOGGOTH_TOKEN")}
        os.environ["SHOGGOTH_DATA_DIR"] = os.path.join(self.tmp, "data")
        os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "config")
        os.environ.pop("SHOGGOTH_AUTH", None)
        os.environ.pop("SHOGGOTH_TOKEN", None)
        self.project = os.path.join(self.tmp, "project")
        os.makedirs(self.project)
        self.srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.auth = {"Authorization": "Bearer " + uiauth.token(), "Host": "127.0.0.1:%d" % self.port}

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for k, v in self._env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, path, body=b""):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", path, body=body, headers=dict(self.auth, **{"Content-Type": "application/octet-stream"}))
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")

    def attach(self, chat, name, data):
        return self.call("/ui/attach?chat=%s&name=%s" % (chat, urllib.parse.quote(name)), data)

    def test_the_route_saves_the_file_and_says_the_handler(self):
        t = agent.create(self.project)
        st, d = self.attach(t["id"], "notes.txt", b"hello")
        self.assertEqual(st, 200)
        self.assertEqual((d["handler"], d["label"]), ("text", "Read as text"))
        self.assertEqual(d["path"], os.path.join(self.project, "attachments", "notes.txt"))
        with open(d["path"], "rb") as f:
            self.assertEqual(f.read(), b"hello")

    def test_a_folderless_chat_saves_to_the_scratch_folder(self):
        t = agent.create("")
        st, d = self.attach(t["id"], "notes.txt", b"hi")
        self.assertEqual(st, 200)
        self.assertTrue(d["path"].startswith(os.path.join(store.data_dir(), "cowork", "scratch", t["id"])))

    def test_traversal_is_refused_and_nothing_is_written(self):
        t = agent.create(self.project)
        st, d = self.attach(t["id"], "../escape.txt", b"x")
        self.assertEqual(st, 400)
        self.assertTrue(d.get("error"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escape.txt")))

    def test_a_file_over_the_limit_answers_413(self):
        t = agent.create(self.project)
        with mock.patch.object(filetypes, "MAX_BYTES", 4):
            st, d = self.attach(t["id"], "notes.txt", b"12345")
        self.assertEqual(st, 413)
        self.assertFalse(os.path.exists(os.path.join(self.project, "attachments", "notes.txt")))

    def test_a_pdf_with_no_pdftotext_says_so(self):
        t = agent.create(self.project)
        with mock.patch.object(filetypes, "installed", lambda tool: False):
            st, d = self.attach(t["id"], "paper.pdf", b"%PDF-1.4")
        self.assertEqual(st, 200)
        self.assertEqual(d["handler"], "pdf-text")
        self.assertIn("pdftotext", d["label"])

    def test_a_chat_that_is_not_there_is_refused(self):
        st, d = self.attach("nope", "notes.txt", b"x")
        self.assertEqual(st, 400)
        self.assertTrue(d.get("error"))


if __name__ == "__main__":
    unittest.main()
