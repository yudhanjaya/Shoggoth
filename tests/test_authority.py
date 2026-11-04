"""Authority: how far Shoggoth may go with an engine it did not start. Run: python3 -m unittest discover tests"""
import http.client
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shoggoth"))

import authority  # noqa: E402
import expert  # noqa: E402
import provbase  # noqa: E402
import router  # noqa: E402
import server  # noqa: E402
import threading
import uiauth  # noqa: E402


class Fake(provbase.Base):
    """An engine that holds the memory and was not started by Shoggoth."""

    def __init__(self, pid, group="gpu", exclusive=True, ours=False, busy=False, can_release=True):
        self.id, self.name, self.ours, self._busy, self.can_release = pid, pid.title(), ours, busy, can_release
        self.spec, self.log, self.running = {}, [], True

    def memory(self):
        return {"group": "gpu", "exclusive": True}

    def is_running(self):
        return self.running

    def busy(self):
        return self._busy

    def owned_instance(self):
        return 1 if self.ours else None

    def make_way(self):
        if self.ours:
            self.log.append("stop")
            self.running = False
            return True, ""
        return False, "it is running something you started."

    minutes, found_busy = 5, (False, 1)

    def idle_minutes(self):
        return self.minutes

    def found_activity(self):
        return self.found_busy

    def activity(self):
        return None, None

    def release(self):
        self.log.append("release")
        self.running = False
        return (True, "") if self.can_release else (False, "it can't")


class Authority(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = {k: os.environ.get(k) for k in ("SHOGGOTH_DATA_DIR", "SHOGGOTH_CONFIG_DIR")}
        os.environ["SHOGGOTH_DATA_DIR"], os.environ["SHOGGOTH_CONFIG_DIR"] = os.path.join(self.tmp, "data"), os.path.join(self.tmp, "config")
        self.other, self.mine = Fake("maya"), Fake("lemonade", ours=False)
        self._providers = router.providers
        router.providers = lambda: [self.other, self.mine]
        authority._once.clear()

    def tearDown(self):
        router.providers = self._providers
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def room(self):
        router.make_room(self.mine, lambda t: None)

    def test_with_expert_mode_off_every_engine_is_hands_off_whatever_was_saved(self):
        authority.set_("maya", "allowed")
        self.assertEqual(authority.get("maya"), "hands_off")
        with self.assertRaises(provbase.ProviderError):
            self.room()
        self.assertEqual(self.other.log, [])

    def test_with_expert_mode_on_an_engine_nobody_set_is_on_ask(self):
        expert.set_on(True)
        self.assertEqual(authority.get("maya"), "ask")

    def test_ask_stops_and_names_the_engine_without_touching_it(self):
        expert.set_on(True)
        with self.assertRaises(provbase.NeedsAnswer) as c:
            self.room()
        self.assertEqual(c.exception.engine, "maya")
        self.assertEqual(self.other.log, [])

    def test_a_yes_frees_it_once_and_the_next_time_asks_again(self):
        expert.set_on(True)
        authority.grant_once("maya")
        self.room()
        self.assertEqual(self.other.log, ["release"])
        self.other.running = True
        with self.assertRaises(provbase.NeedsAnswer):
            self.room()

    def test_allowed_frees_it_by_itself(self):
        expert.set_on(True)
        authority.set_("maya", "allowed")
        self.room()
        self.assertEqual(self.other.log, ["release"])

    def test_hands_off_touches_nothing_even_with_expert_mode_on(self):
        expert.set_on(True)
        authority.set_("maya", "hands_off")
        with self.assertRaises(provbase.ProviderError) as c:
            self.room()
        self.assertNotIsInstance(c.exception, provbase.NeedsAnswer)
        self.assertEqual(self.other.log, [])

    def test_an_engine_that_is_answering_is_never_interrupted_whatever_its_authority(self):
        expert.set_on(True)
        authority.set_("maya", "allowed")
        self.other._busy = True
        with self.assertRaises(provbase.ProviderError):
            self.room()
        self.assertEqual(self.other.log, [])

    def test_what_shoggoth_started_is_stopped_as_before_without_any_asking(self):
        self.other.ours = True
        self.room()
        self.assertEqual(self.other.log, ["stop"])

    def test_an_engine_that_cannot_let_go_is_reported_not_forced(self):
        expert.set_on(True)
        authority.set_("maya", "allowed")
        self.other.can_release = False
        with self.assertRaises(provbase.ProviderError):
            self.room()

    def test_an_ask_waits_for_the_person_until_it_is_answered(self):
        expert.set_on(True)
        router.ASKS.clear()
        with self.assertRaises(provbase.NeedsAnswer):
            self.room()
        self.assertEqual(list(router.ASKS), ["maya"])

    def idle(self, t):
        router.idle_tick(now=t)
        return self.other.log

    def test_an_allowed_engine_that_reports_idle_is_released_after_its_minutes_never_stopped(self):
        expert.set_on(True)
        authority.set_("maya", "allowed")
        router._idle.clear()
        self.idle(1000)
        self.assertEqual(self.idle(1000 + 4 * 60), [])
        self.assertEqual(self.idle(1000 + 5 * 60), ["release"])

    def test_found_engines_on_ask_or_hands_off_or_unable_to_report_are_never_released_when_idle(self):
        expert.set_on(True)
        for setting, report in (("ask", (False, 1)), ("hands_off", (False, 1)), ("allowed", (None, None)), ("allowed", (True, 1))):
            authority.set_("maya", setting)
            self.other.found_busy = report
            router._idle.clear()
            self.idle(0)
            self.idle(100000)
            self.assertEqual(self.other.log, [], (setting, report))

    def test_a_bad_choice_is_refused(self):
        with self.assertRaises(ValueError):
            authority.set_("maya", "whatever")

    def test_the_page_lists_sets_and_answers_over_ui_authority(self):
        srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]
        hdr = {"Authorization": "Bearer " + uiauth.token(), "Host": "127.0.0.1:%d" % port}

        def call(method, body=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request(method, "/ui/authority", body=body, headers=hdr)
            r = c.getresponse()
            return r.status, json.loads(r.read())
        real_get = router.get
        try:
            st, d = call("GET")
            self.assertEqual((st, d["expert"]), (200, False))
            expert.set_on(True)
            router.get = lambda pid: next((p for p in (self.other, self.mine) if p.id == pid), None)
            st, d = call("POST", '{"engine": "maya", "authority": "allowed"}')
            self.assertEqual((st, {e["id"]: e["authority"] for e in d["engines"]}), (200, {"maya": "allowed", "lemonade": "ask"}))
            self.assertEqual(call("POST", '{"engine": "maya", "answer": "free"}')[0], 400)       # only an engine on Ask can be answered
            self.assertEqual(call("POST", '{"engine": "nobody", "authority": "ask"}')[0], 400)
            self.assertEqual(call("POST", '{"engine": "maya", "authority": "bogus"}')[0], 400)
            router.ASKS["lemonade"] = {"message": "Free it?", "since": 1}
            self.assertEqual([a["engine"] for a in call("GET")[1]["asks"]], ["lemonade"])
            self.assertEqual(call("POST", '{"engine": "lemonade", "answer": "free"}')[0], 200)
            self.assertEqual(router.ASKS, {})
            self.assertTrue(authority.spend("lemonade"))
        finally:
            router.get = real_get
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
