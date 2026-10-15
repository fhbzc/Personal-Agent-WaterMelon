"""Notification lessons: each add, edit, disable and delete the user makes on
the calendar, and each notification they mark as a bad one, is written down
whole - and is a piece of evidence for the lessons engine, which learns the
lessons from many of them (test_lessons_engine). The step that schedules
notifications is given only what it learned, never the records. It is not
experience, and nothing groups it."""

from __future__ import annotations

import json
import socket
import unittest
import urllib.error
import urllib.request
from datetime import timedelta

from support import TemporaryAgent, keyed

from personalized_agent.common.events.models import ScheduledTask, iso, utcnow
from personalized_agent.models.base import ModelResponse


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _Answers:
    """A model that says why and what the lesson is, and summarises when
    asked to."""

    def __init__(self, nothing_new=()) -> None:
        self.asked = []
        self.nothing_new = set(nothing_new)

    def __call__(self, request, *args, **kwargs):
        self.asked.append(request)
        if "grown too long" in (request.system or ""):
            return ModelResponse(text="SUMMARY: do not remind about markets.")
        number = sum(1 for one in self.asked
                     if "grown too long" not in (one.system or ""))
        lesson = ("" if number in self.nothing_new else
                  f"LESSON {number}: do not schedule this kind. " + "y" * 200)
        return ModelResponse(text=json.dumps(
            {"why": f"Reason {number}: they did not want it.", "lesson": lesson}))


class NotificationLessonTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.answers = _Answers()
        self.app.models.invoke = self.answers
        self.port = _free_port()
        self.app.config.set("api_port", self.port)
        from personalized_agent.api.server import ApiServer

        self.server = ApiServer(self.app)
        self.server.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.stop()
        self.agent.cleanup()

    def _post(self, path: str, payload: dict):
        request = urllib.request.Request(
            f"{self.base}{path}", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(keyed(self.app, request), timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def _get(self, path: str):
        with urllib.request.urlopen(keyed(self.app, f"{self.base}{path}"), timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def _lessons(self):
        kinds = self._get("/api/lessons")["kinds"]
        self.assertEqual(kinds[0]["kind"], "notifications")
        return kinds[0]

    def _asked(self):
        """The first question about a lesson (adding an event also asks a
        model to fill in what was left blank; that is not this question)."""
        return next(one for one in self.answers.asked
                    if one.task == "notification_lessons").messages[0]["content"]

    def _agents_event(self, title: str) -> ScheduledTask:
        return self.app.scheduler.create_task(ScheduledTask(
            title=title, description="Your sign-up bonus ends Friday.",
            scheduled_at=utcnow() + timedelta(hours=2),
            execution_type="notification",
            task_payload={"notify_user": True, "template": "reminder"},
            created_by="heartbeat", reason="seen on a brokerage page"))

    def test_only_these_actions_are_recorded(self) -> None:
        kept = self._agents_event("A reminder")
        self.app.calendar_nature.record("enable", kept)
        self.app.calendar_nature.record("run_now", kept)
        self.assertEqual(self._lessons()["actions"], 0)

    def test_a_bad_notification_without_an_event_is_still_kept(self) -> None:
        shown = self.app.notifications.notify("A note", "Something to know.",
                                              kind="info")
        self._post("/api/notifications/bad", {"id": shown.id})
        self.app.calendar_nature.wait()
        lesson = self._lessons()["lessons"][0]
        self.assertIsNone(lesson["event"]["task"])
        self.assertEqual(lesson["title"], "A note")

    def test_the_bubble_offers_it(self) -> None:
        """The desktop bubble carries the notification's id, and its Bad
        notification button keeps it as a lesson."""
        from personalized_agent.ui import desktop

        queued = []

        class _Queue:
            def put(self, item):
                queued.append(item)

        ui = desktop.DesktopAgent.__new__(desktop.DesktopAgent)
        ui.app = self.app
        ui.queue = _Queue()
        shown = self.app.notifications.notify("A note", "Something to know.",
                                              kind="info")
        ui._on_notification("notification.created", shown.to_dict())
        self.assertEqual(queued[0][1]["notification_id"], shown.id)
        ui._mark_bad(shown.id)                  # what the button runs
        self.app.calendar_nature.wait()
        self.assertEqual(self._lessons()["lessons"][0]["action"], "bad_notification")

    def test_the_agent_adds_nothing_without_a_reason(self) -> None:
        when = (utcnow() + timedelta(hours=3)).isoformat()
        added = self.app.heartbeat._add_notifications("task_1", [
            {"type": "reminder", "title": "Stretch", "message": "Time to stretch.",
             "at": when},
            {"type": "reminder", "title": "Send the draft",
             "message": "You said Friday.", "at": when,
             "why": "The promise in this morning's reply."}])
        self.assertFalse(added[0]["ok"])
        self.assertIn("reason", added[0]["error"])
        self.assertTrue(added[1]["ok"])
        made = self.app.scheduler.store.get(added[1]["task_id"])
        self.assertEqual(made.reason, "The promise in this morning's reply.")

    # -- the actions as the lessons engine's evidence ------------------------------
    def _learning(self):
        from test_lessons_engine import _Model
        self.app.lessons.models = _Model()

    def _evidence(self):
        return self.app.db.query("SELECT * FROM lesson_evidence WHERE kind = 'notifications' "
                                 "ORDER BY at, rowid")

    def test_each_action_is_evidence_read_whole(self) -> None:
        self._learning()
        self._post("/api/tasks", {
            "title": "Call the dentist",
            "scheduled_at": (utcnow() + timedelta(days=1)).isoformat()})
        disabled = self._agents_event("Northbank Invest bonus ends Friday")
        self._post("/api/tasks/action", {"id": disabled.id, "action": "disable"})
        deleted = self._agents_event("Check the market")
        self._post("/api/tasks/action", {"id": deleted.id, "action": "delete"})
        self.app.calendar_nature.wait()
        said = self._lessons()
        self.assertEqual(said["actions"], 3)
        actions = {row["action"]: row for row in said["lessons"]}
        self.assertEqual(set(actions), {"add", "disable", "delete"})
        self.assertEqual(actions["delete"]["did"], "Deleted by user")
        evidence = {row["action"]: row for row in self._evidence()}
        self.assertEqual(set(evidence), {"add", "disable", "delete"})
        # Each is the whole event as it read, with why the agent made it.
        self.assertIn("The user deleted this event.", evidence["delete"]["summary"])
        self.assertIn("Your sign-up bonus ends Friday.", evidence["delete"]["summary"])
        self.assertIn("seen on a brokerage page", evidence["delete"]["summary"])
        self.assertNotIn("THE COMPLETE RECORD", evidence["delete"]["summary"])
        # And each says how it was read, once read.
        self.assertTrue(all("read" in row for row in said["lessons"]))
        self.assertIn("engine", said)

    def test_a_reason_is_kept_and_weighs_more(self) -> None:
        self._learning()
        made = self._agents_event("Check the market")
        self._post("/api/tasks/action", {
            "id": made.id, "action": "delete",
            "why": "I only check it on weekends, not during work."})
        self._post("/api/tasks", {
            "title": "Water the plants",
            "scheduled_at": (utcnow() + timedelta(days=1)).isoformat(),
            "reason": "The agent never reminds me of chores."})
        self.app.calendar_nature.wait()
        rows = {row["action"]: row for row in self._evidence()}
        self.assertEqual(rows["delete"]["reason"], "I only check it on weekends, not during work.")
        self.assertEqual(rows["delete"]["weight"], 2.0)
        self.assertIn("The user said: I only check it on weekends", rows["delete"]["summary"])
        self.assertEqual(rows["add"]["reason"], "The agent never reminds me of chores.")

    def test_a_bad_notification_is_evidence_with_both_records(self) -> None:
        self._learning()
        event = self._agents_event("Northbank Invest bonus ends Friday")
        shown = self.app.notifications.notify(
            "Northbank Invest bonus ends Friday", "Your sign-up bonus ends Friday.",
            kind="info", metadata={"task_id": event.id})
        said = self._post("/api/notifications/bad", {"id": shown.id,
                                                     "why": "Too vague to act on."})
        self.assertTrue(said["ok"])
        self.app.calendar_nature.wait()
        self.assertTrue(self.app.notifications.get(shown.id)["read"])
        lesson = self._lessons()["lessons"][0]
        self.assertEqual(lesson["action"], "bad_notification")
        self.assertEqual(lesson["did"], "Marked bad by user")
        [evidence] = self._evidence()
        self.assertIn("marked this as a bad notification", evidence["summary"])
        self.assertIn("THE NOTIFICATION AS IT WAS SHOWN", evidence["summary"])
        self.assertEqual(evidence["reason"], "Too vague to act on.")

    def test_the_notification_step_is_given_only_what_was_learned(self) -> None:
        from personalized_agent.heartbeat.core.heartbeat import StateFloor
        self._learning()
        keeper = self.app.calendar_nature
        for _ in range(3):
            keeper.record("delete", self._agents_event("Check the market"))
        keeper.wait()
        # Learned with the state heartbeat's round: asked for here.
        self.app.lessons.learn("notifications")
        floor = StateFloor(15, [], [])
        floor.calendar_nature = keeper.text()
        context = floor.context_text()
        self.assertIn("LESSONS ABOUT THE USER'S NOTIFICATIONS", context)
        self.assertIn("[les_", context)
        self.assertIn("Name, in", context)
        self.assertNotIn("Your sign-up bonus ends Friday.", context)
        self.assertNotIn("Check the market", context)

    def test_deleting_an_action_or_a_lesson_from_the_panel(self) -> None:
        self._learning()
        keeper = self.app.calendar_nature
        for _ in range(3):
            keeper.record("delete", self._agents_event("Check the market"))
        keeper.wait()
        # Learned with the state heartbeat's round: asked for here.
        self.app.lessons.learn("notifications")
        rule = self.app.lessons.rules("notifications", ("active",))[0]
        action = self._lessons()["lessons"][0]
        self.assertTrue(self._post("/api/lessons/delete",
                                   {"kind": "notifications", "id": action["id"]})["deleted"])
        self.assertEqual(len(self._evidence()), 2, "its evidence went with it")
        self.assertTrue(self._post("/api/lessons/delete",
                                   {"kind": "notifications", "id": rule["id"]})["deleted"])
        self.assertEqual(keeper.text(), "", "rejected: not given, not learned again")
        self.assertEqual(self.app.lessons.rule(rule["id"])["state"], "rejected")
        said = self._post("/api/lessons/rule", {"id": rule["id"], "op": "pin"})
        self.assertTrue(said["pinned"])

    def test_the_agent_corrected_counts_against_what_it_followed(self) -> None:
        self._learning()
        keeper = self.app.calendar_nature
        for _ in range(3):
            keeper.record("delete", self._agents_event("Check the market"))
        keeper.wait()
        # Learned with the state heartbeat's round: asked for here.
        self.app.lessons.learn("notifications")
        rule = self.app.lessons.rules("notifications", ("active",))[0]
        when = (utcnow() + timedelta(hours=3)).isoformat()
        added = self.app.heartbeat._add_notifications("task_1", [
            {"type": "reminder", "title": "Send the draft", "message": "You said Friday.",
             "at": when, "why": "The promise in this morning's reply.",
             "lessons": [rule["id"]]}])
        made = self.app.scheduler.store.get(added[0]["task_id"])
        self.assertEqual(made.metadata["lessons_used"], [rule["id"]])
        before = self.app.lessons.rule(rule["id"])["confidence"]
        self._post("/api/tasks/action", {"id": made.id, "action": "delete"})
        keeper.wait()
        self.assertLess(self.app.lessons.rule(rule["id"])["confidence"], before)


    def test_deleting_what_is_not_there_says_so(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as refused:
            self._post("/api/lessons/delete", {"id": "calact_none"})
        self.assertEqual(refused.exception.code, 404)

    def test_remind_me_later_is_the_same_reminder_in_half_an_hour(self) -> None:
        event = self._agents_event("Send the revised draft")
        shown = self.app.notifications.notify(
            "Send the revised draft", "You told the editor Friday.",
            kind="proactive", template="reminder",
            metadata={"task_id": event.id})
        said = self._post("/api/notifications/later", {"id": shown.id})
        again = self.app.scheduler.store.get(said["task"]["id"])
        self.assertEqual(again.title, "Send the revised draft")
        self.assertEqual(again.execution_type, "notification")
        self.assertTrue(again.task_payload["notify_user"])
        minutes = (again.scheduled_at - utcnow()).total_seconds() / 60
        self.assertTrue(29 <= minutes <= 30.5, minutes)
        # THE EVENT MOVED, not another one made - and the one shown dismissed.
        self.assertEqual(again.id, event.id)
        self.assertEqual(again.status, "pending")
        self.assertTrue(self.app.notifications.get(shown.id)["read"])
        # Pressed again: moved again, still one.
        shown_again = self.app.notifications.notify(
            "Send the revised draft", "You told the editor Friday.",
            kind="proactive", template="reminder", metadata={"task_id": event.id})
        self._post("/api/notifications/later", {"id": shown_again.id})
        same = [task for task in self.app.scheduler.store.list(limit=100)
                if task.title == "Send the revised draft"]
        self.assertEqual(len(same), 1)
        # EVERY MOVE WRITTEN DOWN on it: its first time, then each new one.
        log = same[0].metadata["postponed"]
        self.assertEqual(len(log), 2)
        self.assertEqual(log[0]["from"], iso(event.scheduled_at))
        self.assertEqual(log[1]["from"], log[0]["to"])
        self.assertEqual(log[1]["to"], iso(same[0].scheduled_at))
        # Not a lesson: nothing was said against it.
        self.assertEqual(self._lessons()["actions"], 0)

    def test_a_repeating_reminder_keeps_its_series_and_one_postponed(self) -> None:
        from datetime import timedelta
        from personalized_agent.common.events.models import ScheduledTask
        series = self.app.scheduler.create_task(ScheduledTask(
            title="Stand up and stretch", description="Every hour.",
            scheduled_at=utcnow() + timedelta(hours=1), execution_type="notification",
            task_payload={"message": "Stretch", "notify_user": True},
            recurrence={"every_seconds": 3600}, created_by="user"))
        held_at = self.app.scheduler.store.get(series.id).scheduled_at
        for _ in range(3):
            shown = self.app.notifications.notify(
                "Stand up and stretch", "Every hour.", kind="proactive",
                template="reminder", metadata={"task_id": series.id})
            self._post("/api/notifications/later", {"id": shown.id})
        self.assertEqual(self.app.scheduler.store.get(series.id).scheduled_at, held_at,
                         "the series stays where it was")
        postponed = [task for task in self.app.scheduler.store.list(limit=100)
                     if task.title == "Stand up and stretch" and task.id != series.id]
        self.assertEqual(len(postponed), 1, "one postponed reminder, moved each time")
        # And it keeps every move: made once, then moved twice.
        log = postponed[0].metadata["postponed"]
        self.assertEqual(len(log), 3)
        self.assertEqual([one["from"] for one in log[1:]], [one["to"] for one in log[:-1]])

    def test_the_bubble_offers_it_on_a_reminder_not_a_notice(self) -> None:
        from personalized_agent.ui import desktop

        queued = []

        class _Queue:
            def put(self, item):
                queued.append(item)

        ui = desktop.DesktopAgent.__new__(desktop.DesktopAgent)
        ui.app = self.app
        ui.queue = _Queue()
        reminder = self.app.notifications.notify("Stretch", "Time to move.",
                                                 kind="proactive",
                                                 template="reminder")
        ui._on_notification("notification.created", reminder.to_dict())
        self.assertEqual(queued[0][1]["template"], "reminder")
        ui._remind_later(reminder.id)              # what the button runs
        later = [task for task in self.app.scheduler.store.list(limit=50)
                 if task.title == "Stretch"]
        self.assertEqual(len(later), 1)

    def test_it_is_not_experience(self) -> None:
        """Nothing that groups natures can see it: it is not in the library."""
        keeper = self.app.calendar_nature
        keeper.record("delete", self._agents_event("Check the market"))
        keeper.wait()
        for record in self.app.library.records("user_nature"):
            self.assertNotIn("Check the market", str(record.to_dict()))
        tables = {row["name"] for row in self.app.db.query(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("stage_natures", "nature_links"):
            if table in tables:
                rows = self.app.db.query(f"SELECT * FROM {table}")
                self.assertNotIn("Check the market", json.dumps(rows, default=str))



if __name__ == "__main__":
    unittest.main()
