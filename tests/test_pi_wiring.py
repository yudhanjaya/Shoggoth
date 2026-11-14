"""Pi's start-up: curated packages in settings.json, the save_skill environment, the "Written by your models" set, and the restart
after a save. Run: python3 -m unittest discover tests"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "shoggoth"))

import agent  # noqa: E402
import pipkgs  # noqa: E402
import skills  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._env = os.environ.get("SHOGGOTH_DATA_DIR")
        os.environ["SHOGGOTH_DATA_DIR"] = self.tmp
        self._curated = pipkgs.curated
        self.curated = []
        pipkgs.curated = lambda: self.curated
        self._cb = list(skills.on_changed)

    def tearDown(self):
        pipkgs.curated = self._curated
        skills.on_changed[:] = self._cb
        if self._env is None:
            os.environ.pop("SHOGGOTH_DATA_DIR", None)
        else:
            os.environ["SHOGGOTH_DATA_DIR"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def install_fake(self, name="@acme/pi-tools", version="1.0.0", skill="tidy"):
        d = os.path.join(pipkgs.root(), pipkgs._dirname(name, version))
        os.makedirs(os.path.join(d, "skills", skill))
        with open(os.path.join(d, "package.json"), "w") as f:
            json.dump({"name": name, "version": version}, f)
        with open(os.path.join(d, "skills", skill, "SKILL.md"), "w") as f:
            f.write("---\nname: %s\ndescription: Tidies.\n---\n\n1. Tidy.\n" % skill)
        with open(os.path.join(d, pipkgs.MARK), "w") as f:
            json.dump({"name": name, "version": version}, f)
        return d

    def settings(self):
        with open(os.path.join(agent.agent_dir(), "settings.json")) as f:
            return json.load(f)

    def own(self, name, turn_on, chat="chat-1"):
        d = os.path.join(skills.own_skills_dir(), name)
        os.makedirs(d)
        with open(os.path.join(d, "SKILL.md"), "w") as f:
            f.write('---\nname: %s\ndescription: "Use when testing."\n---\n\n1. Test.\n' % name)
        with open(os.path.join(d, "source.json"), "w") as f:
            json.dump({"chat": chat, "model": "m", "date": "2026-11-14T00:00:00Z", "turn_on": turn_on}, f)


class Packages(Base):
    def test_settings_have_packages_only_when_one_is_enabled(self):
        agent.write_config([], "")
        self.assertNotIn("packages", self.settings())                       # nothing installed: no key at all
        d = self.install_fake()
        self.curated = [{"name": "@acme/pi-tools", "version": "1.0.0", "integrity": "", "enabled": False, "notes": ""}]
        agent.write_config([], "")
        self.assertNotIn("packages", self.settings())                       # installed, but disabled in the list
        self.curated[0]["enabled"] = True
        agent.write_config([], "")
        self.assertEqual(self.settings()["packages"], [d])                  # enabled: the unpacked folder

    def test_a_task_starting_syncs_the_list_without_a_new_write_config(self):
        agent.write_config([], "")
        d = self.install_fake()
        self.curated = [{"name": "@acme/pi-tools", "version": "1.0.0", "integrity": "", "enabled": True, "notes": ""}]
        agent._sync_packages()
        self.assertEqual(self.settings()["packages"], [d])
        self.curated[0]["enabled"] = False
        agent._sync_packages()
        self.assertNotIn("packages", self.settings())
        self.assertEqual(self.settings()["defaultProjectTrust"], "never")   # the rest of the file is untouched

    def test_package_skills_are_listed_and_handed_to_pi(self):
        self.install_fake()
        self.assertFalse([s for s in skills.scan() if s["package"]])
        self.curated = [{"name": "@acme/pi-tools", "version": "1.0.0", "integrity": "", "enabled": True, "notes": ""}]
        sets = [s for s in skills.scan() if s["package"]]
        self.assertEqual([s["package"] for s in sets], ["@acme/pi-tools"])
        self.assertEqual([k["name"] for k in sets[0]["skills"]], ["tidy"])
        live, n = skills.live_dir()
        self.assertTrue(os.path.isfile(os.path.join(live, "pkg-acme-pi-tools__tidy", "SKILL.md")))
        skills.toggle(sets[0]["skills"][0]["key"], False)                   # it can be switched off like any skill
        skills.live_dir()
        self.assertFalse(os.path.lexists(os.path.join(live, "pkg-acme-pi-tools__tidy")))

    def test_pi_still_runs_without_its_own_skill_discovery(self):
        src = open(agent.__file__, encoding="utf8").read()
        self.assertEqual(src.count('"--no-skills"'), 2)
        self.assertIn('"-e", SKILLSMITH_EXT', src)
        self.assertTrue(os.path.isfile(agent.SKILLSMITH_EXT))


class SkillsmithEnv(Base):
    def test_the_environment_carries_what_the_extension_reads(self):
        e = agent._env(own_skills=skills.own_skills_dir(), task_id="t1", model="tiny")
        self.assertEqual(e["SHOGGOTH_OWN_SKILLS"], skills.own_skills_dir())
        self.assertEqual((e["SHOGGOTH_TASK_ID"], e["SHOGGOTH_MODEL"]), ("t1", "tiny"))

    def test_without_it_the_tool_is_not_offered_even_if_the_parent_has_the_variable(self):
        os.environ["SHOGGOTH_OWN_SKILLS"] = "/elsewhere"
        try:
            self.assertNotIn("SHOGGOTH_OWN_SKILLS", agent._env())
        finally:
            del os.environ["SHOGGOTH_OWN_SKILLS"]

    def test_the_gate_runner_passes_the_gate_test(self):
        if not shutil.which("node"):
            self.skipTest("node is needed")
        r = subprocess.run(["node", os.path.join(HERE, "ui", "gate-skillsmith.test.mjs")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class OwnSet(Base):
    def test_the_own_set_is_listed_with_its_skills_and_where_they_came_from(self):
        own = [s for s in skills.scan() if s["id"] == "own"]
        self.assertEqual(own[0]["name"], "Written by your models")
        self.assertEqual(own[0]["skills"], [])
        self.own("release-notes", True, chat="chat-9")
        own = [s for s in skills.scan() if s["id"] == "own"][0]
        self.assertEqual([k["key"] for k in own["skills"]], ["own/release-notes"])
        self.assertEqual(own["skills"][0]["from"]["chat"], "chat-9")
        self.assertTrue(own["own"])

    def test_it_cannot_be_removed_like_an_installed_set(self):
        with self.assertRaises(ValueError):
            skills.remove("own")

    def test_a_save_switches_the_skill_as_chosen_and_restarts_idle_tasks(self):
        calls = []
        skills.on_changed[:] = [lambda: calls.append(1)]
        self.own("kept-off", False)
        self.own("kept-on", True)
        self.assertTrue(skills.saved("kept-off"))
        self.assertTrue(skills.saved("kept-on"))
        self.assertEqual(len(calls), 2)
        on = {k["key"]: k["enabled"] for s in skills.scan() if s["id"] == "own" for k in s["skills"]}
        self.assertEqual(on, {"own/kept-off": False, "own/kept-on": True})
        live, _ = skills.live_dir()
        self.assertTrue(os.path.lexists(os.path.join(live, "own__kept-on")))
        self.assertFalse(os.path.lexists(os.path.join(live, "own__kept-off")))

    def test_a_save_that_left_nothing_on_disk_does_nothing(self):
        calls = []
        skills.on_changed[:] = [lambda: calls.append(1)]
        for name in ("missing", "../x", "", None):
            self.assertFalse(skills.saved(name))
        self.assertEqual(calls, [])

    def test_a_fake_save_reported_by_pi_restarts_idle_chats_but_not_busy_ones(self):
        stopped = []
        real_stop, real_sessions = agent.stop, dict(agent._sessions)

        class Fake:
            def __init__(self, running):
                self.running, self.loading, self.pending = running, False, {}

        agent.stop = stopped.append
        agent._sessions.clear()
        agent._sessions.update({"idle": Fake(False), "busy": Fake(True)})
        try:
            self.assertIn(agent.restart_idle, skills.on_changed)            # agent.py wired itself in
            self.own("fresh", True)
            s = agent.Session({"id": "busy"})
            s.running = True
            s._handle({"type": "tool_execution_end", "toolName": "save_skill", "isError": False,
                       "result": {"content": [], "details": {"saved": True, "name": "fresh"}}})
            for t in __import__("threading").enumerate():
                if t is not __import__("threading").current_thread() and t.daemon:
                    t.join(2)
            self.assertEqual(stopped, ["idle"])
            # a declined or failed save changes nothing
            stopped.clear()
            s._handle({"type": "tool_execution_end", "toolName": "save_skill", "isError": False,
                       "result": {"content": [], "details": {"saved": False, "name": "fresh"}}})
            s._handle({"type": "tool_execution_end", "toolName": "save_skill", "isError": True, "result": {}})
            self.assertEqual(stopped, [])
        finally:
            agent.stop = real_stop
            agent._sessions.clear()
            agent._sessions.update(real_sessions)


class Bundled(Base):
    def test_good_code_is_short_and_listed(self):
        p = os.path.join(skills.BUNDLED, "zest", "skills", "good-code", "SKILL.md")
        self.assertLessEqual(os.path.getsize(p), 2048)
        names = [k["dir"] for s in skills.scan() if s["id"] == "zest" for k in s["skills"]]
        self.assertIn("good-code", names)
        self.assertNotIn("good-code", [x["name"] for x in skills.inline()])

    def test_unslop_is_a_placeholder_that_is_off_and_never_reaches_a_model(self):
        item = next(k for s in skills.scan() if s["id"] == "zest" for k in s["skills"] if k["dir"] == "unslop")
        self.assertTrue(item["placeholder"])
        self.assertFalse(item["enabled"])
        self.assertNotIn("unslop", [x["name"] for x in skills.inline()])
        skills.toggle("zest/unslop", True)                                 # switched on, still nothing to hand over
        self.assertNotIn("unslop", [x["name"] for x in skills.inline()])
        live, _ = skills.live_dir()
        self.assertFalse(os.path.lexists(os.path.join(live, "zest__unslop")))
        with open(os.path.join(skills.BUNDLED, "zest", "set.json")) as f:
            self.assertIn("unslop", json.load(f)["inline"])


if __name__ == "__main__":
    unittest.main()
