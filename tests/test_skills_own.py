"""S4: the "Written by your models" skills — list, edit in a plain textarea, delete, and "Make a skill from this chat".
Run: python3 -W ignore -m unittest discover tests"""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "shoggoth"))

import agent  # noqa: E402
import pipkgs  # noqa: E402
import server  # noqa: E402
import skills  # noqa: E402
import uiauth  # noqa: E402

GOOD = '---\nname: %s\ndescription: "Use when tidying a folder."\n---\n\n1. Open the folder.\n2. Sort the files.\n'


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR", "SHOGGOTH_AUTH", "SHOGGOTH_TOKEN")}
        os.environ["SHOGGOTH_DATA_DIR"] = os.path.join(self.tmp, "data")
        os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "config")
        os.environ.pop("SHOGGOTH_AUTH", None)
        os.environ.pop("SHOGGOTH_TOKEN", None)
        self._curated = pipkgs.curated
        pipkgs.curated = lambda: []
        self._cb = list(skills.on_changed)
        self.changed = []
        skills.on_changed[:] = [lambda: self.changed.append(1)]

    def tearDown(self):
        pipkgs.curated = self._curated
        skills.on_changed[:] = self._cb
        for k, v in self._env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def own(self, name, text=None, turn_on=True):
        d = os.path.join(skills.own_skills_dir(), name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "SKILL.md"), "w", encoding="utf8") as f:
            f.write(GOOD % name if text is None else text)
        with open(os.path.join(d, "source.json"), "w") as f:
            json.dump({"chat": "chat-7", "model": "tiny", "date": "2026-11-14T10:00:00Z", "turn_on": turn_on}, f)
        return d

    def text_of(self, name):
        with open(os.path.join(skills.own_skills_dir(), name, "SKILL.md"), encoding="utf8") as f:
            return f.read()


class Listing(Base):
    def test_it_lists_nothing_at_first(self):
        self.assertEqual(skills.own_list(), [])

    def test_each_skill_comes_with_its_source_json_and_whole_text(self):
        self.own("tidy-desk")
        item = skills.own_list()[0]
        self.assertEqual((item["name"], item["key"], item["title"]), ("tidy-desk", "own/tidy-desk", "tidy-desk"))
        self.assertEqual(item["description"], "Use when tidying a folder.")
        self.assertEqual(item["source"], {"chat": "chat-7", "model": "tiny", "date": "2026-11-14T10:00:00Z"})
        self.assertIn("1. Open the folder.", item["text"])
        self.assertTrue(item["enabled"])

    def test_a_skill_switched_off_is_listed_as_off(self):
        self.own("tidy-desk")
        skills.toggle("own/tidy-desk", False)
        self.assertFalse(skills.own_list()[0]["enabled"])


class Editing(Base):
    def test_an_edit_rewrites_the_skill_and_keeps_its_source_json(self):
        self.own("tidy-desk")
        new = (GOOD % "tidy-desk") + "3. Close the drawer.\n"
        self.assertEqual(skills.own_save("tidy-desk", new), {"name": "tidy-desk", "key": "own/tidy-desk"})
        self.assertIn("Close the drawer", self.text_of("tidy-desk"))
        self.assertEqual(skills.own_list()[0]["source"]["chat"], "chat-7")
        self.assertEqual(self.changed, [1])

    def test_it_edits_and_never_creates_a_skill(self):
        with self.assertRaises(ValueError):
            skills.own_save("brand-new", GOOD % "brand-new")
        self.assertFalse(os.path.exists(os.path.join(skills.own_skills_dir(), "brand-new")))
        self.assertEqual(self.changed, [])

    def test_a_skill_that_is_not_there_is_refused(self):
        with self.assertRaises(ValueError):
            skills.own_save("tidy-desk", GOOD % "tidy-desk")

    def test_the_name_must_be_a_slug(self):
        self.own("tidy-desk")
        for name in ("../escape", ".hidden", "Upper", "two--hyphens", "", None, "x" * 41, "with/slash", "with space"):
            with self.assertRaises(ValueError, msg=repr(name)):
                skills.own_save(name, GOOD % "tidy-desk")
        self.assertIn("1. Open the folder.", self.text_of("tidy-desk"))
        self.assertEqual(self.changed, [])

    def test_the_limits_are_save_skills_limits(self):
        self.own("tidy-desk")
        bad = [
            (GOOD % "tidy-desk") + "y" * (17 * 1024),
            (GOOD % "tidy-desk") + "x" * (9 * 1024),
            '---\nname: tidy-desk\ndescription: "%s"\n---\n\n1. A step.\n' % ("d" * 301),
            (GOOD % "tidy-desk") + "\u202e reversed\n",
            (GOOD % "tidy-desk") + "bell\x07\n",
            "Just some steps, no header.\n",
            "---\nname: other-skill\ndescription: x\n---\n\n1. A step.\n",
            "---\nname: tidy-desk\n---\n\n1. A step.\n",
            "---\nname: tidy-desk\ndescription: x\n---\n\n   \n",
            "",
            None,
        ]
        for text in bad:
            with self.assertRaises(ValueError, msg=repr(text)[:60]):
                skills.own_save("tidy-desk", text)
        self.assertIn("1. Open the folder.", self.text_of("tidy-desk"))
        self.assertEqual(self.changed, [])

    def test_crlf_from_a_textarea_is_stored_as_lf(self):
        self.own("tidy-desk")
        skills.own_save("tidy-desk", (GOOD % "tidy-desk").replace("\n", "\r\n"))
        self.assertNotIn("\r", self.text_of("tidy-desk"))
        self.assertTrue(self.text_of("tidy-desk").endswith("\n"))


class Deleting(Base):
    def test_delete_removes_the_folder_and_its_switches(self):
        d = self.own("tidy-desk")
        self.own("keep-me")
        skills.toggle("own/tidy-desk", False)
        self.assertEqual(skills.own_delete("tidy-desk"), {"name": "tidy-desk"})
        self.assertFalse(os.path.isdir(d))
        with open(os.path.join(skills.root(), "state.json")) as f:
            st = json.load(f)
        self.assertNotIn("own/tidy-desk", st["disabled"] + st["enabled"])
        self.assertEqual([s["name"] for s in skills.own_list()], ["keep-me"])
        self.assertEqual(self.changed, [1])

    def test_a_missing_or_strange_name_is_refused(self):
        for name in ("tidy-desk", "../mine", "", None, ".hidden"):
            with self.assertRaises(ValueError, msg=repr(name)):
                skills.own_delete(name)
        self.assertEqual(self.changed, [])


class Routes(Base):
    def setUp(self):
        super().setUp()
        self.srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.auth = {"Authorization": "Bearer " + uiauth.token(), "Host": "127.0.0.1:%d" % self.port}

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def call(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=self.auth)
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")

    def test_the_page_lists_edits_and_deletes(self):
        self.own("tidy-desk")
        st, d = self.call("GET", "/ui/skills/own")
        self.assertEqual((st, [s["name"] for s in d["skills"]]), (200, ["tidy-desk"]))
        self.assertEqual(d["skills"][0]["source"]["model"], "tiny")
        new = (GOOD % "tidy-desk") + "3. Close the drawer.\n"
        st, d = self.call("POST", "/ui/skills/own/save", {"name": "tidy-desk", "text": new})
        self.assertEqual(st, 200)
        self.assertIn("Close the drawer", self.text_of("tidy-desk"))
        self.assertEqual(d["skills"][0]["source"]["chat"], "chat-7")
        self.assertTrue(self.changed)
        st, d = self.call("POST", "/ui/skills/own/delete", {"name": "tidy-desk"})
        self.assertEqual((st, d["skills"]), (200, []))
        self.assertFalse(os.path.isdir(os.path.join(skills.own_skills_dir(), "tidy-desk")))

    def test_a_bad_edit_answers_400_with_the_reason(self):
        self.own("tidy-desk")
        st, d = self.call("POST", "/ui/skills/own/save", {"name": "../escape", "text": GOOD % "x"})
        self.assertEqual(st, 400)
        self.assertTrue(d.get("error"))
        st, d = self.call("POST", "/ui/skills/own/save", {"name": "tidy-desk", "text": (GOOD % "tidy-desk") + "x\x07\n"})
        self.assertEqual(st, 400)
        self.assertIn("plain", d["error"])
        self.assertIn("1. Open the folder.", self.text_of("tidy-desk"))

    def test_make_skill_sends_the_fixed_prompt(self):
        t = agent.create(self.tmp)
        sent = []
        with mock.patch.object(agent, "command", lambda sid, cmd: sent.append((sid, cmd))):
            st, d = self.call("POST", "/ui/chat/make-skill", {"chat": t["id"]})
        self.assertEqual(st, 200)
        self.assertEqual(sent, [(t["id"], {"type": "prompt", "message": agent.MAKE_SKILL_PROMPT})])
        self.assertIn("save_skill", d["prompt"])

    def test_make_skill_ignores_any_text_from_the_page(self):
        t = agent.create(self.tmp)
        sent = []
        with mock.patch.object(agent, "command", lambda sid, cmd: sent.append((sid, cmd))):
            self.call("POST", "/ui/chat/make-skill", {"chat": t["id"], "message": "write me a poem"})
        self.assertEqual(sent[0][1]["message"], agent.MAKE_SKILL_PROMPT)

    def test_make_skill_refuses_a_background_job(self):
        sent = []
        with mock.patch.object(agent, "get_task", lambda sid: {"id": sid, "kind": "job", "job": {}}), \
             mock.patch.object(agent, "command", lambda sid, cmd: sent.append(cmd)):
            st, d = self.call("POST", "/ui/chat/make-skill", {"chat": "j1"})
        self.assertEqual(st, 400)
        self.assertIn("background job", d["error"])
        self.assertEqual(sent, [])

    def test_make_skill_for_a_chat_that_is_not_there(self):
        st, d = self.call("POST", "/ui/chat/make-skill", {"chat": "nope"})
        self.assertEqual(st, 400)
        self.assertTrue(d.get("error"))


class Prompt(unittest.TestCase):
    def test_it_is_one_fixed_prompt_that_asks_for_save_skill(self):
        p = agent.MAKE_SKILL_PROMPT
        self.assertEqual(p, agent.MAKE_SKILL_PROMPT)
        self.assertIn("save_skill", p)
        self.assertIn("distil", p)
        self.assertGreater(len(p), 80)
        self.assertNotIn("!", p)

    def test_a_change_asks_the_idle_tasks_to_restart(self):
        self.assertIn(agent.restart_idle, skills.on_changed)


if __name__ == "__main__":
    unittest.main()
