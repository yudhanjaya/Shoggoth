"""An engine change (Strata starting a model) must not stop the chat that is waiting for that model. Run: python3 -m unittest discover tests"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import agent  # noqa: E402


class FakeSession:
    def __init__(self, loading):
        self.running, self.loading, self.pending = False, loading, {}


class RestartIdle(unittest.TestCase):
    def test_a_chat_waiting_for_its_model_keeps_running(self):
        waiting, idle = FakeSession(True), FakeSession(False)
        with mock.patch.dict(agent._sessions, {"waiting": waiting, "idle": idle}, clear=True), mock.patch.object(agent, "stop") as stop:
            agent.restart_idle()
        stop.assert_called_once_with("idle")


if __name__ == "__main__":
    unittest.main()
