"""The 5.0 documentation names what shipped, and its links resolve.

These are reading tests on purpose: the docs are the deliverable, so a missing section or a link to a
file that no longer exists is a regression worth failing on. They use the standard library only.
"""
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DOCS = ["README.md", "docs/ARCHITECTURE.md", "docs/AGENT-RUNTIME.md", "docs/ENGINES.md",
        "docs/PRIVACY.md", "docs/SECURITY.md", "docs/DEVELOPMENT.md", "docs/README.md",
        "docs/RELEASE-5.0.md", "shoggoth/docs.md"]
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
BANNED = ("seamless", "effortless", "powerful")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class Docs(unittest.TestCase):
    def test_readme_covers_the_5_0_features(self):
        text = read("README.md")
        for phrase in ("llama.cpp", "Moving from 4.x", "Other servers", "Other computers", "whisper.cpp",
                       "Tools for the agent", "glibc 2.38"):
            self.assertIn(phrase, text, "README.md should mention %r" % phrase)

    def test_engine_definition_files_are_documented(self):
        text = read("docs/ENGINES.md")
        self.assertIn("Engine definition files", text)
        self.assertIn("engines check", text)
        for key in ("ports", "match", "cwd", "identify", "formats", "key_url", "enabled"):
            self.assertIn("`%s`" % key, text, "the definition table should describe %s" % key)
        self.assertTrue("cannot carry a command" in text or "can't carry a command" in text,
                        "the docs must say a definition cannot run anything")
        self.assertNotIn("recommendations/servers.yaml", text, "that file was replaced by engines.d")

    def test_in_app_guide_covers_the_5_0_features(self):
        text = read("shoggoth/docs.md")
        for phrase in ("Pictures and files", "Speech", "Written by your models", "Tools",
                       "Models online", "Other computers", "Coming from 4.x", "Attach a file"):
            self.assertIn(phrase, text, "the user guide should mention %r" % phrase)

    def test_release_notes_cover_the_release(self):
        text = read("docs/RELEASE-5.0.md")
        for phrase in ("What's new", "What's removed", "Moving from 4.x", "Known gaps",
                       "llama.cpp", "Lemonade", "Ubuntu 22.04", "glibc 2.38", "HTTPS",
                       "model-aliases.json", "Piper", "Written by your models"):
            self.assertIn(phrase, text, "RELEASE-5.0.md should mention %r" % phrase)

    def test_agent_runtime_covers_packages_skills_and_attachments(self):
        text = read("docs/AGENT-RUNTIME.md")
        for phrase in ("Pi packages", "save_skill", "/ui/attach", "make-skill"):
            self.assertIn(phrase, text, "AGENT-RUNTIME.md should mention %r" % phrase)

    def test_privacy_lists_the_new_storage_and_destinations(self):
        text = read("docs/PRIVACY.md")
        for phrase in ("whisper", "piper", "pi-packages", "piper-voices", "lan/", "attachments/"):
            self.assertIn(phrase, text, "PRIVACY.md should mention %r" % phrase)

    def test_security_covers_packages_and_attachments(self):
        text = read("docs/SECURITY.md")
        for phrase in ("Pi packages", "Runs code on your computer", "Attachments", "never executed"):
            self.assertIn(phrase, text, "SECURITY.md should mention %r" % phrase)

    def test_relative_links_resolve(self):
        missing = []
        for rel in DOCS:
            base = os.path.dirname(os.path.join(ROOT, rel))
            for target in LINK.findall(read(rel)):
                target = target.split("#", 1)[0].strip()
                if not target or "://" in target or target.startswith("mailto:"):
                    continue
                path = os.path.normpath(os.path.join(base, target))
                if not path.startswith(os.path.normpath(ROOT)):
                    continue                      # a link outside the repository, such as ../../releases/latest
                if not os.path.exists(path):
                    missing.append("%s -> %s" % (rel, target))
        self.assertEqual(missing, [], "These documentation links do not resolve: %s" % ", ".join(missing))

    def test_copy_rules(self):
        offending = []
        for rel in DOCS:
            text = read(rel)
            for word in BANNED:
                if word in text.lower():
                    offending.append("%s: %s" % (rel, word))
            prose = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)          # markdown images use "!["
            if re.search(r"\w!", prose):
                offending.append("%s: exclamation mark" % rel)
        self.assertEqual(offending, [], "Copy rules broken: %s" % ", ".join(offending))


if __name__ == "__main__":
    unittest.main()
