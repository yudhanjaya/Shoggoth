"""Two profiles on one computer each get their own Shoggoth, even though they both ask for the same port. Run: python3 -m unittest discover tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import server  # noqa: E402

ME = {"zest": "1.0", "profile": "me"}
THEM = {"zest": "1.0", "profile": "alberto"}


def pick(running, port=13306, version="1.0", profile="me"):
    """running: {port: ping info} for what is listening; anything else is free."""
    return server.choose_port(port, version, profile, probe=lambda p: running.get(p), taken=lambda p: p in running)


class Choose(unittest.TestCase):
    def test_a_free_port_is_used(self):
        self.assertEqual(pick({}), ("start", 13306))

    def test_my_own_running_shoggoth_is_reused(self):
        self.assertEqual(pick({13306: ME}), ("reuse", 13306))

    def test_another_profiles_shoggoth_is_left_alone_and_the_next_port_used(self):
        self.assertEqual(pick({13306: THEM}), ("start", 13307))

    def test_my_second_launch_finds_the_one_i_started_beside_theirs(self):
        self.assertEqual(pick({13306: THEM, 13307: ME}), ("reuse", 13307))

    def test_ports_taken_by_other_profiles_are_skipped(self):
        self.assertEqual(pick({13306: THEM, 13307: THEM}), ("start", 13308))

    def test_an_older_shoggoth_that_does_not_say_its_profile_is_not_mine(self):
        self.assertEqual(pick({13306: {"zest": "0.9"}}), ("start", 13307))

    def test_another_version_of_mine_is_not_reused(self):
        self.assertEqual(pick({13306: {"zest": "0.9", "profile": "me"}}), ("start", 13307))

    def test_something_else_on_the_port_is_not_shoggoth(self):
        self.assertEqual(pick({13306: None}, ), ("start", 13306))


class Who(unittest.TestCase):
    def test_a_profile_is_told_apart_by_its_data_folder(self):
        old = os.environ.get("SHOGGOTH_DATA_DIR")
        try:
            os.environ["SHOGGOTH_DATA_DIR"] = "/tmp/a"
            a = server.profile_id()
            os.environ["SHOGGOTH_DATA_DIR"] = "/tmp/b"
            b = server.profile_id()
        finally:
            os.environ.pop("SHOGGOTH_DATA_DIR", None) if old is None else os.environ.__setitem__("SHOGGOTH_DATA_DIR", old)
        self.assertNotEqual(a, b)
        self.assertNotIn("/tmp", a)                 # an id, not the folder name


if __name__ == "__main__":
    unittest.main()
