"""The shipped text names llama.cpp. Lemonade appears only for the 4.x move or a server you run yourself."""
import os
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

# Everything a person who installs 5.0 reads, plus the code strings and the diagram.
SHIPPED = [
    "README.md",
    "docs/ENGINES.md",
    "docs/PRIVACY.md",
    "docs/SECURITY.md",
    "docs/DEVELOPMENT.md",
    "docs/AGENT-RUNTIME.md",
    "shoggoth/docs.md",
    "shoggoth/llamaargs.py",
    "shoggoth/recommendations/models.yaml",
    "shoggoth/skills/shoggoth/skills/diagrams/SKILL.md",
]

# Lemonade is still the right word here: moving a 4.x profile, and connecting a server you run yourself.
STILL_LEMONADE = [
    "shoggoth/migrate5.py",
    "shoggoth/engines.d/lemonade.yaml",
]


class Wording(unittest.TestCase):
    def test_shipped_text_says_llamacpp(self):
        offending = []
        for rel in SHIPPED:
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                text = f.read()
            if "lemonade" in text.lower():
                offending.append(rel)
        self.assertEqual(offending, [], "These files still mention Lemonade: %s" % ", ".join(offending))

    def test_lemonade_kept_where_it_belongs(self):
        for rel in STILL_LEMONADE:
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                text = f.read()
            self.assertIn("lemonade", text.lower(), "%s should still describe a 4.x Lemonade" % rel)


if __name__ == "__main__":
    unittest.main()
