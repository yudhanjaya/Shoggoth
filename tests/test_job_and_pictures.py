"""Step 7: the agent can fill in the Start a job form (never start a job itself), and pictures reach Pi unchanged. Run: python3 -m unittest discover tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import agent  # noqa: E402
import skills  # noqa: E402


class JobTool(unittest.TestCase):
    def test_the_job_tool_is_only_offered_outside_a_job(self):
        self.assertEqual(agent._env(jobs=True)["SHOGGOTH_JOBS"], "1")
        self.assertEqual(agent._env()["SHOGGOTH_JOBS"], "0")
        self.assertTrue(os.path.isfile(agent.JOB_EXT))

    def test_the_skill_is_there_and_handed_to_plain_chats(self):
        path = os.path.join(os.path.dirname(agent.__file__), "skills", "zest", "skills", "start-a-job", "SKILL.md")
        text = open(path, encoding="utf8").read()
        self.assertRegex(text, r"(?m)^name: start-a-job$")
        self.assertIn("propose_job", text)
        self.assertIn("start-a-job", [s["name"] for s in skills.inline()])

    def test_the_approval_gate_lets_the_form_open_in_every_mode(self):
        gate = open(agent.GATE, encoding="utf8").read()
        self.assertRegex(gate, r'READ_ONLY = new Set\(\[[^\]]*"propose_job"')


class Pictures(unittest.TestCase):
    def test_a_prompt_with_pictures_goes_to_pi_untouched(self):
        sent = []

        class Fake:
            running = False
            loading = False

            def send(self, cmd, snapshot=False):
                sent.append(cmd)

        old = agent.session, agent.get_task, agent.ensure_loaded, agent._update_meta
        agent.session = lambda sid: Fake()
        agent.get_task = lambda sid: {"id": sid, "title": "x", "model": "m"}
        agent.ensure_loaded = lambda *a, **k: None
        agent._update_meta = lambda *a, **k: None
        try:
            img = {"type": "image", "data": "QUJD", "mimeType": "image/jpeg"}
            agent.command("t1", {"type": "prompt", "message": "What is this?", "images": [img]})
        finally:
            agent.session, agent.get_task, agent.ensure_loaded, agent._update_meta = old
        self.assertEqual(sent[0]["images"], [img])
        self.assertEqual(sent[0]["message"], "What is this?")


if __name__ == "__main__":
    unittest.main()
