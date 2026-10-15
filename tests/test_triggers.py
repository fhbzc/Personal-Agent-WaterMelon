"""At once, when the screen calls for it (heartbeat/core/triggers.py), and what
waits for a time (pending.py): a meeting invitation, or a time agreed, runs
the notification step alone on that window read whole; what is agreed
without a time waits, is given to every notification step, and is put on
the calendar when its time is seen."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from support import TemporaryAgent

from personalized_agent.common.events.models import ListenerEvent
from personalized_agent.heartbeat.core import triggers
from personalized_agent.heartbeat.core.pending import PendingStore
from personalized_agent.models.base import ModelResponse

INVITE = ("Alex Kim - Guest Speaker Inbox Mon, Oct 19 • 2:00 PM – 3:00 PM 100 Example Ave "
          "Sam Rivera - Organizer Yes No Maybe invite.ics")
AGREED = "Hi Prof. Tan: Thanks for the message. The time works for me."


class RulesTests(unittest.TestCase):

    def _rule(self, title="", text=""):
        for rule in triggers.RULES:
            if rule.matches(title, text):
                return rule.name
        return None

    def test_what_calls_for_it(self) -> None:
        self.assertEqual(self._rule("Guest Speaker - Gmail", INVITE), "invitation")
        self.assertEqual(self._rule("Invitation: Lab meeting @ Mon Oct 19", ""), "invitation")
        self.assertEqual(self._rule("会议邀请：周一组会", ""), "invitation")
        self.assertEqual(self._rule("Request to Present - Gmail", AGREED), "time_agreed")
        self.assertEqual(self._rule("微信", "好的，这个时间可以"), "time_agreed")
        self.assertIsNone(self._rule("A winter soup - Google Chrome", "Slow-cooked onions."))


class _Front:
    def __init__(self, title, text):
        self.title, self.text = title, text


class WatchingTests(unittest.TestCase):

    def _event(self, title, text, handle=0, application="firefox.exe"):
        return {"application": application,
                "data": {"window_title": title, "text": text, "handle": handle}}

    def _opened(self, watch, title, handle=0, application="firefox.exe"):
        """The window's first look since the start: learned, not a reason."""
        watch.heard("listener.screen.text", self._event(title, "opened", handle, application))

    def test_the_first_look_after_a_start_only_learns(self) -> None:
        # After a restart the screen reads everything in a window as new: an
        # old conversation is not a reason - and is known from then on.
        watch = triggers.Triggers(heartbeat=None, bus=None)
        old = self._event("Terminal", "the time works for me", 9, "WindowsTerminal.exe")
        watch.heard("listener.screen.text", old)
        self.assertEqual(watch._due, {})
        watch.heard("listener.screen.text", old)
        self.assertEqual(watch._due, {}, "known now")

    def test_a_window_once_after_it_settles_and_never_ours(self) -> None:
        watch = triggers.Triggers(heartbeat=None, bus=None)
        self._opened(watch, "Guest Speaker - Gmail — Mozilla Firefox")
        watch.heard("listener.screen.text",
                    self._event("Guest Speaker - Gmail — Mozilla Firefox", INVITE))
        key = "title:Guest Speaker"
        self.assertEqual(list(watch._due), [key])
        # Not yet: the page settles first.
        self.assertEqual(watch._ready(now=0), [])
        ready = watch._ready(now=watch._due[key]["at"])
        self.assertEqual([one["rule"].name for one in ready], ["invitation"])
        self.assertIn("Yes No Maybe", ready[0]["fresh"])
        # The agent's own windows are not read.
        watch.heard("listener.screen.text", self._event("Personalized Agent", INVITE + " x",
                                                        application="PersonalizedAgent.exe"))
        self.assertEqual(watch._due, {})

    def test_one_window_whatever_its_title_says(self) -> None:
        # A terminal's title changes as it works: the window is its handle.
        watch = triggers.Triggers(heartbeat=None, bus=None)
        self._opened(watch, "\u25d0 Upload instruction", 4242, "WindowsTerminal.exe")
        watch.heard("listener.screen.text", self._event(
            "\u25d0 Upload instruction", "and the time works for me", 4242, "WindowsTerminal.exe"))
        key = "windowsterminal.exe#4242"
        watch._ready(now=watch._due[key]["at"])
        watch.heard("listener.screen.text", self._event(
            "\u2733 Fix the tests", "see you on Monday then", 4242, "WindowsTerminal.exe"))
        self.assertEqual(watch._due, {}, "the same window, within its ten minutes")

    def test_only_what_is_new_in_the_window(self) -> None:
        # What the window showed before - a conversation scrolled back, words
        # discussed hours ago - is not a reason again.
        watch = triggers.Triggers(heartbeat=None, bus=None)
        self._opened(watch, "Terminal", 7, "WindowsTerminal.exe")
        old = self._event("Terminal", "the time works for me", 7, "WindowsTerminal.exe")
        watch.heard("listener.screen.text", old)
        key = "windowsterminal.exe#7"
        watch._ready(now=watch._due[key]["at"])
        watch._looked[key] = 0                     # its ten minutes long past
        watch.heard("listener.screen.text", old)
        self.assertEqual(watch._due, {}, "seen before in this window")
        watch.heard("listener.screen.text", self._event(
            "Terminal", "the time works for me\nlet's meet on Friday at 3", 7,
            "WindowsTerminal.exe"))
        self.assertEqual(watch._due[key]["fresh"], "let's meet on Friday at 3")

    def test_nobody_there_nothing_runs(self) -> None:
        watch = triggers.Triggers(heartbeat=None, bus=None, idle=lambda: 3600)
        watch.heard("listener.screen.text", self._event("Guest Speaker - Gmail", INVITE))
        self.assertEqual(watch._due, {})
        self.assertTrue(triggers.Triggers(heartbeat=None, bus=None, idle=lambda: 5).present())
        self.assertTrue(triggers.Triggers(heartbeat=None, bus=None, idle=lambda: None).present())

    def test_off_when_turned_off(self) -> None:
        watch = triggers.Triggers(heartbeat=None, bus=None,
                                  config={"triggers_enabled": False})
        watch.heard("listener.screen.text", self._event("x", INVITE))
        self.assertEqual(watch._due, {})


class _Model:
    """Answers the notification step: the talk, and the waiting one resolved."""

    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    def invoke(self, request, role=""):
        self.asked.append(request)
        return ModelResponse(text=json.dumps(self.answer))


class TheStepAtOnceTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_an_invitation_puts_the_talk_on_the_calendar_and_settles_what_waited(self) -> None:
        waiting = self.app.pending.add("Talk in Prof. Tan's group - time to be set",
                                       "The time works for them; the date is not on screen.",
                                       ["Prof. Tan"])
        at = (datetime.now(timezone.utc) + timedelta(days=6)).astimezone().isoformat()
        model = _Model({
            "notifications": [{"type": "reminder", "title": "Guest talk at 2:00 PM",
                               "message": "Your talk at 100 Example Ave.", "at": at,
                               "why": "the invitation in the window"}],
            "pending_resolved": [{"id": waiting, "why": "the invitation gives Mon 2:00 PM"}]})
        self.app.heartbeat.models = model
        watch = self.app.triggers
        watch.front = lambda: _Front("Guest Speaker - Gmail — Mozilla Firefox", INVITE)
        report = watch.run(triggers.RULES[0], "Guest Speaker", fresh="Yes No Maybe invite.ics")
        # One call: the window whole, what is scheduled, what waits.
        self.assertEqual(len(model.asked), 1)
        prompt = model.asked[0].messages[0]["content"]
        self.assertIn("WHAT JUST APPEARED", prompt)
        self.assertIn("Yes No Maybe invite.ics", prompt)
        self.assertIn("ACT ONLY ON WHAT JUST APPEARED", prompt)
        self.assertIn("THE WINDOW, READ WHOLE", prompt)
        self.assertIn("100 Example Ave", prompt)
        self.assertIn(waiting, prompt)
        self.assertIn("NOTIFICATIONS ALREADY SCHEDULED", prompt)
        self.assertEqual(model.asked[0].task, "heartbeat_trigger")
        # Applied as step 3's own: on the calendar - and what waited, settled.
        self.assertTrue(report.get("notifications_scheduled"), report)
        titles = [row["title"] for row in self.app.db.query("SELECT title FROM scheduled_tasks")]
        self.assertIn("Guest talk at 2:00 PM", titles)
        self.assertEqual(self.app.pending.open(), [])
        self.assertEqual(report["pending_resolved"], [waiting])
        self.assertEqual(watch.history[0]["rule"], "invitation")

    def test_a_time_agreed_without_its_time_waits(self) -> None:
        model = _Model({"pending": [{"title": "Talk in Prof. Tan's group - time to be set",
                                     "detail": "They said the time works; it is not shown.",
                                     "people": ["Prof. Tan"],
                                     "evidence": ["the window: 'The time works for me.'"]}]})
        self.app.heartbeat.models = model
        watch = self.app.triggers
        watch.front = lambda: None
        # No longer in front: what the screen showed of it lately.
        self.app.raw_store.add_event(ListenerEvent(
            event_type="screen.text", source="test", application="firefox.exe",
            data={"window_title": "Request to Present - Gmail — Mozilla Firefox",
                  "text": AGREED}))
        watch.run(triggers.RULES[1], "Request to Present")
        self.assertIn(AGREED, model.asked[0].messages[0]["content"])
        waiting = self.app.pending.open()
        self.assertEqual([one["title"] for one in waiting],
                         ["Talk in Prof. Tan's group - time to be set"])
        # Given to the next notification step, every time.
        self.assertIn("Talk in Prof. Tan's group", "\n".join(self.app.pending.lines()))

    def test_nothing_to_read_nothing_asked(self) -> None:
        model = _Model({})
        self.app.heartbeat.models = model
        watch = self.app.triggers
        watch.front = lambda: None
        report = watch.run(triggers.RULES[0], "A window never seen")
        self.assertEqual(report["skipped"], "nothing to read")
        self.assertEqual(model.asked, [])


class WaitingTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_once_settled_once_and_the_users_say(self) -> None:
        from personalized_agent.api.routes import build_routes
        store = self.app.pending
        first = store.add("Call with Ana - time to be set")
        self.assertIsNone(store.add("call with ana - time to be set"), "not twice")
        self.assertTrue(store.close(first, "dropped", "off"))
        self.assertFalse(store.close(first, "resolved"), "settled once")

        class _Req:
            def __init__(self, body):
                self.body = body

            def require(self, key):
                return self.body[key]

            def get(self, key, default=None):
                return self.body.get(key, default)

        routes = build_routes(self.app)
        said = routes[("POST", "/api/pending")](_Req({"action": "add", "title": "Dentist"}))
        ident = said["open"][0]["id"]
        self.assertEqual(said["open"][0]["by"], "user")
        said = routes[("POST", "/api/pending")](_Req({"action": "drop", "id": ident}))
        self.assertEqual(said["open"], [])
        listed = routes[("GET", "/api/pending")](_Req({}))
        self.assertEqual({one["status"] for one in listed["recent"]}, {"dropped"})

    def test_the_heartbeats_notification_step_is_told_what_waits(self) -> None:
        from personalized_agent.heartbeat.core.heartbeat import STEP_PROMPTS
        step = STEP_PROMPTS["step_notify"]
        self.assertIn("WHAT IS WAITING FOR A TIME", step["instruction"])
        self.assertIn('"pending_resolved"', step["returns"])


if __name__ == "__main__":
    unittest.main()
