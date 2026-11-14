"""The save_skill Pi extension (shoggoth/pi/zest-skillsmith.js): validation, the always-ask dialog, and what it writes.
The checks are in tests/ui/skillsmith.test.mjs and run under node; skipped where node is missing."""
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class Skillsmith(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is needed")
    def test_save_skill(self):
        r = subprocess.run(["node", os.path.join(HERE, "ui", "skillsmith.test.mjs")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_the_bundled_skill_is_small_and_listed(self):
        d = os.path.join(HERE, "..", "shoggoth", "skills", "shoggoth", "skills", "writing-skills", "SKILL.md")
        with open(d, encoding="utf8") as f:
            text = f.read()
        self.assertLess(len(text.encode()), 3 * 1024)
        self.assertIn("save_skill", text)
        self.assertTrue(text.startswith("---\nname: writing-skills\n"))


if __name__ == "__main__":
    unittest.main()
