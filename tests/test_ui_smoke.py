"""The page opens every screen against recorded answers with no script errors (jsdom, via node). Skipped where node or its packages are missing.
Setup once: cd tests/ui && npm install.  Record the answers again after the API changes: node tests/ui/smoke.js --record URL TOKEN"""
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, "ui")


JSDOM = os.path.isdir(os.path.join(UI, "node_modules", "jsdom"))


def ui_tests():
    """Every tests/ui/*.test.mjs and *.test.cjs, so a new one runs without being listed here."""
    return sorted(n for n in os.listdir(UI) if n.endswith((".test.mjs", ".test.cjs")))


def needs_jsdom(name):
    with open(os.path.join(UI, name), encoding="utf8") as f:
        text = f.read()
    return "jsdom" in text or "page.cjs" in text


class EveryUITest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is needed")
    def test_every_ui_test_file_passes(self):
        self.assertGreaterEqual(len(ui_tests()), 13)
        for name in ui_tests():
            with self.subTest(name):
                if needs_jsdom(name) and not JSDOM:
                    self.skipTest("jsdom is needed: cd tests/ui && npm ci")
                r = subprocess.run(["node", os.path.join(UI, name)], capture_output=True, text=True, timeout=180)
                self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-3000:])


@unittest.skipUnless(shutil.which("node") and os.path.isdir(os.path.join(UI, "node_modules", "jsdom")), "node and `npm install` in tests/ui are needed")
class PageSmoke(unittest.TestCase):
    def test_every_page_opens_with_no_script_errors(self):
        r = subprocess.run(["node", os.path.join(UI, "smoke.js")], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("every page opened", r.stdout)


if __name__ == "__main__":
    unittest.main()
