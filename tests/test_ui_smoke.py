"""The page opens every screen against recorded answers with no script errors (jsdom, via node). Skipped where node or its packages are missing.
Setup once: cd tests/ui && npm install.  Record the answers again after the API changes: node tests/ui/smoke.js --record URL TOKEN"""
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, "ui")


class Gate(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is needed")
    def test_the_approval_card_says_what_a_command_could_do(self):
        r = subprocess.run(["node", os.path.join(UI, "gate.test.mjs")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


    @unittest.skipUnless(shutil.which("node"), "node is needed")
    def test_a_model_that_wont_load_is_explained_in_plain_words(self):
        r = subprocess.run(["node", os.path.join(UI, "problems.test.mjs")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


    @unittest.skipUnless(shutil.which("node"), "node is needed")
    def test_the_job_tool_only_fills_a_form_in(self):
        r = subprocess.run(["node", os.path.join(UI, "job.test.mjs")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


@unittest.skipUnless(shutil.which("node") and os.path.isdir(os.path.join(UI, "node_modules", "jsdom")), "node and `npm install` in tests/ui are needed")
class PageSmoke(unittest.TestCase):
    def test_every_page_opens_with_no_script_errors(self):
        r = subprocess.run(["node", os.path.join(UI, "smoke.js")], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("every page opened", r.stdout)


if __name__ == "__main__":
    unittest.main()
