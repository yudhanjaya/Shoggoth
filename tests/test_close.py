"""Closing the last Shoggoth tab quits at once, but a reload or a second tab keeps it open. Run: python3 -m unittest discover tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import server  # noqa: E402


class Tabs(unittest.TestCase):
    def setUp(self):
        self.t = server.Tabs(leave=2, grace=180)

    def test_an_open_tab_keeps_it_running(self):
        self.t.ping("a", now=100)
        self.assertFalse(self.t.should_quit(now=101, started=0))

    def test_the_last_tab_closing_quits_after_a_moment(self):
        self.t.ping("a", now=100)
        self.t.bye("a", now=110)
        self.assertFalse(self.t.should_quit(now=111, started=0))
        self.assertTrue(self.t.should_quit(now=112.5, started=0))

    def test_a_reload_does_not_quit(self):
        self.t.ping("a", now=100)
        self.t.bye("a", now=110)
        self.t.ping("b", now=110.5)
        self.assertFalse(self.t.should_quit(now=120, started=0))

    def test_a_second_tab_keeps_it_open_when_the_first_closes(self):
        self.t.ping("a", now=100)
        self.t.ping("b", now=101)
        self.t.bye("a", now=102)
        self.assertFalse(self.t.should_quit(now=110, started=0))
        self.t.bye("b", now=111)
        self.assertTrue(self.t.should_quit(now=114, started=0))

    def test_a_tab_that_vanished_without_saying_so_still_times_out(self):
        self.t.ping("a", now=100)
        self.assertFalse(self.t.should_quit(now=200, started=0))
        self.assertTrue(self.t.should_quit(now=283, started=0))

    def test_nobody_ever_opening_a_tab_times_out(self):
        self.assertFalse(self.t.should_quit(now=100, started=0))
        self.assertTrue(self.t.should_quit(now=301, started=0))


if __name__ == "__main__":
    unittest.main()
