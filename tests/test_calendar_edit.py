"""Editing an event on the calendar, and the edit kept as a lesson.

An event still to come can be edited - its title, the words its notification
shows, its time, how it repeats - from its card. A repeating one is edited
whole. Its accompanying state follows it, and the task says it was edited
without forgetting who made it or why.

Every edit is the user saying what they wanted INSTEAD, so it is recorded as
a notification lesson: the event whole as it was, whole as it is now, the
fields that changed, and why, if they said - for a model to compare and draw
the lesson from. Once read, only the title, the user's words and the changed
fields (before and after, in words) are kept.
"""

from __future__ import annotations

import json
import socket
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from support import TemporaryAgent, keyed

from personalized_agent.common.events.models import ScheduledTask, utcnow
from personalized_agent.models.base import ModelResponse


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _Answers:
    """A model that draws a lesson from whatever it is shown."""

    def __init__(self) -> None:
        self.asked = []

    def __call__(self, request, *args, **kwargs):
        self.asked.append(request)
        number = len(self.asked)
        return ModelResponse(text=json.dumps(
            {"why": f"Reason {number}: they wanted it otherwise.",
             "lesson": f"LESSON {number}: prefer the afternoon."}))


class CalendarEditTests(unittest.TestCase):

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

    # -- helpers ----------------------------------------------------------
    def _post(self, path: str, payload: dict):
        request = urllib.request.Request(
            f"{self.base}{path}", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(keyed(self.app, request), timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def _get(self, path: str):
        with urllib.request.urlopen(keyed(self.app, f"{self.base}{path}"), timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def _edit(self, task_id: str, **fields):
        return self._post("/api/tasks/action",
                          {"id": task_id, "action": "edit", **fields})

    def _refused(self, task_id: str, **fields) -> int:
        with self.assertRaises(urllib.error.HTTPError) as refused:
            self._edit(task_id, **fields)
        return refused.exception.code

    def _event(self, **overrides) -> ScheduledTask:
        fields = dict(
            title="Stretch", description="Stand up and stretch.",
            scheduled_at=(utcnow() + timedelta(days=1)).replace(
                hour=9, minute=0, second=0, microsecond=0),
            execution_type="notification",
            task_payload={"notify_user": True, "template": "reminder",
                          "message": "Stand up and stretch."},
            created_by="heartbeat", reason="sat for three hours straight")
        fields.update(overrides)
        return self.app.scheduler.create_task(ScheduledTask(**fields))

    def _lesson_rows(self):
        return self.app.db.query(
            "SELECT * FROM calendar_actions WHERE action = 'edit'")

    def _asked_about_edit(self) -> str:
        """The edit as the lessons engine's evidence: what it learns from."""
        return next(row["summary"] for row in self.app.db.query(
            "SELECT summary FROM lesson_evidence WHERE kind = 'notifications' "
            "AND action = 'edit'"))

    # -- each field -------------------------------------------------------
    def test_the_title_is_edited_and_the_state_follows(self) -> None:
        event = self._event()
        said = self._edit(event.id, title="Stretch your back")
        self.assertEqual(said["title"], "Stretch your back")
        self.assertEqual(said["changed"], ["title"])
        states = self.app.states.for_task(event.id)
        self.assertEqual([s.title for s in states], ["Scheduled: Stretch your back"])

    def test_the_message_is_what_the_notification_shows(self) -> None:
        event = self._event()
        said = self._edit(event.id, message="Roll your shoulders.")
        self.assertEqual(said["task_payload"]["message"], "Roll your shoulders.")
        self.assertEqual(said["description"], "Roll your shoulders.")
        # The rest of the payload is what the task needs to run.
        self.assertTrue(said["task_payload"]["notify_user"])
        self.assertEqual(said["task_payload"]["template"], "reminder")
        self.assertEqual(said["changed"], ["message"])

    def test_the_time_is_moved_and_the_state_with_it(self) -> None:
        event = self._event()
        later = event.scheduled_at + timedelta(hours=5)
        said = self._edit(event.id, at=later.isoformat())
        stored = self.app.scheduler.store.get(event.id)
        self.assertEqual(stored.scheduled_at, later)
        self.assertEqual(said["changed"], ["time"])
        state = self.app.states.for_task(event.id)[0]
        self.assertEqual(state.starts_at, later - timedelta(minutes=30))
        self.assertEqual(state.ends_at, later + timedelta(minutes=30))

    def test_a_time_with_an_offset_is_the_same_moment(self) -> None:
        event = self._event()
        wanted = (event.scheduled_at + timedelta(hours=2)).astimezone(
            timezone(timedelta(hours=8)))
        self._edit(event.id, at=wanted.isoformat())
        self.assertEqual(self.app.scheduler.store.get(event.id).scheduled_at, wanted)

    def test_a_repeat_is_added_changed_and_taken_away(self) -> None:
        event = self._event()
        said = self._edit(event.id, repeat={"every": 2, "unit": "days", "count": 3})
        self.assertEqual(said["recurrence"]["every_seconds"], 2 * 86400)
        self.assertEqual(said["recurrence"]["count"], 3)
        self.assertEqual(said["changed"], ["repeat"])
        until = (event.scheduled_at + timedelta(days=30)).isoformat()
        said = self._edit(event.id, repeat={"every": 1, "unit": "weeks",
                                            "until": until})
        self.assertEqual(said["recurrence"]["every_seconds"], 604800)
        self.assertNotIn("count", said["recurrence"])
        said = self._edit(event.id, repeat=None)
        self.assertEqual(said["recurrence"], {})

    def test_what_is_sent_back_unchanged_is_not_an_edit(self) -> None:
        event = self._event()
        said = self._edit(event.id, title="Stretch",
                          message="Stand up and stretch.",
                          at=event.scheduled_at.isoformat(), repeat=None)
        self.assertEqual(said["changed"], [])
        self.assertNotIn("edited_by_user", said["metadata"])
        self.assertEqual(self._lesson_rows(), [])

    def test_a_disabled_event_can_be_edited_and_stays_disabled(self) -> None:
        event = self._event()
        self.app.scheduler.disable(event.id)
        said = self._edit(event.id, title="Stretch later")
        self.assertEqual(said["status"], "disabled")
        self.assertEqual(said["title"], "Stretch later")

    def test_the_edit_is_marked_without_losing_who_made_it(self) -> None:
        event = self._event()
        self._edit(event.id, title="Stretch your back")
        said = self._edit(event.id, message="Now.")
        stored = self.app.scheduler.store.get(event.id)
        self.assertEqual(stored.created_by, "heartbeat")
        self.assertEqual(stored.reason, "sat for three hours straight")
        mark = said["metadata"]["edited_by_user"]
        self.assertEqual(mark["fields"], ["message"])
        self.assertEqual(mark["times"], 2)
        self.assertTrue(mark["at"])

    # -- what is refused --------------------------------------------------
    def test_only_an_event_still_to_come_can_be_edited(self) -> None:
        for status in ("running", "completed", "closed", "cancelled"):
            event = self._event()
            self.app.scheduler.store.update(event.id, status=status)
            self.assertEqual(self._refused(event.id, title="Other"), 409, status)
            self.assertEqual(self.app.scheduler.store.get(event.id).title, "Stretch")
        self.assertEqual(self._lesson_rows(), [])

    def test_a_time_in_the_past_is_refused(self) -> None:
        event = self._event()
        past = (utcnow() - timedelta(hours=3)).isoformat()
        self.assertEqual(self._refused(event.id, at=past), 400)
        self.assertEqual(self._refused(event.id, at="not a time"), 400)
        self.assertEqual(self.app.scheduler.store.get(event.id).scheduled_at,
                         event.scheduled_at)

    def test_an_unreadable_repeat_is_refused(self) -> None:
        event = self._event()
        for bad in ({"every": 2, "unit": "fortnights"},
                    {"every": 10, "unit": "seconds"},
                    {"every": 1, "unit": "days", "count": 0},
                    "daily"):
            self.assertEqual(self._refused(event.id, repeat=bad), 400, bad)
        self.assertEqual(self.app.scheduler.store.get(event.id).recurrence, {})

    def test_empty_words_are_refused(self) -> None:
        event = self._event()
        self.assertEqual(self._refused(event.id, title="   "), 400)
        self.assertEqual(self._refused(event.id, message=""), 400)

    def test_a_missing_event_is_not_found(self) -> None:
        self.assertEqual(self._refused("task_none", title="x"), 404)

    # -- a series ---------------------------------------------------------
    def test_a_series_is_edited_whole_from_any_block(self) -> None:
        event = self._event(recurrence={"every_seconds": 86400})
        # One occurrence was renamed on its own, and one was deleted.
        self.app.scheduler.override_occurrence(event.id, 2, {"title": "Odd one"})
        self.app.scheduler.skip_occurrence(event.id, 3)
        said = self._edit(f"{event.id}#4", title="Stretch, every day")
        self.assertEqual(said["id"], event.id)
        drawn = self._get(
            "/api/tasks?expand=1&start="
            + urllib.request.quote(utcnow().isoformat())
            + "&end=" + urllib.request.quote(
                (utcnow() + timedelta(days=8)).isoformat()))["tasks"]
        mine = [t for t in drawn if t.get("occurrence_of") == event.id]
        # Every block says the new title - the one that had its own too -
        # and the deleted one stays deleted.
        self.assertTrue(mine)
        self.assertEqual({t["title"] for t in mine}, {"Stretch, every day"})
        self.assertNotIn(3, [t["occurrence_index"] for t in mine])
        self.assertTrue(all(t["series_at"] == said["scheduled_at"] for t in mine))

    def test_a_series_moved_starts_again_from_its_next_run(self) -> None:
        event = self._event(recurrence={"every_seconds": 86400, "count": 5})
        self.app.scheduler.store.update(event.id, runs=2)
        self.app.scheduler.skip_occurrence(event.id, 3)
        later = event.scheduled_at + timedelta(hours=4)
        said = self._edit(event.id, at=later.isoformat())
        # Three runs were still to come; three are still to come.
        self.assertEqual(said["recurrence"]["count"], 3)
        self.assertEqual(said["runs"], 0)
        self.assertNotIn("occurrence_overrides", said["metadata"])
        self.assertEqual(said["metadata"]["runs_before_edit"], 2)
        # The repeat, sent back as the form showed it (the runs to come), is
        # not a change.
        again = self._edit(event.id, repeat={"every": 1, "unit": "days",
                                             "count": 3})
        self.assertEqual(again["changed"], [])

    # -- the lesson -------------------------------------------------------
    def test_an_edit_is_a_lesson_read_from_before_and_after(self) -> None:
        event = self._event()
        later = event.scheduled_at + timedelta(hours=5)
        self._edit(event.id, at=later.isoformat(), message="Walk around.",
                   repeat={"every": 1, "unit": "days"},
                   why="Mornings are for deep work")
        self.app.calendar_nature.wait()
        asked = self._asked_about_edit()
        # Both events, whole: before and after, side by side.
        self.assertIn("THE EVENT BEFORE THE EDIT", asked)
        self.assertIn("THE EVENT AFTER THE EDIT", asked)
        before, after = asked.split("THE EVENT AFTER THE EDIT", 1)
        before = before.split("THE EVENT BEFORE THE EDIT", 1)[1]
        for words in ("Title: Stretch", "Stand up and stretch.",
                      "does not repeat", "sat for three hours straight",
                      "the agent heartbeat", "Kind: notification (reminder)"):
            self.assertIn(words, before)
        for words in ("Title: Stretch", "Walk around.", "every day",
                      "sat for three hours straight", "the agent heartbeat"):
            self.assertIn(words, after)
        self.assertIn("WHAT CHANGED", asked)
        self.assertIn("Mornings are for deep work", asked)
        # The time, in the user's own time, both ways.
        local = lambda moment: moment.astimezone().strftime("%a %d %b %Y %H:%M")
        self.assertIn(f'time: "{local(event.scheduled_at)}" -> "{local(later)}"',
                      asked)
        self.assertIn('repeat: "does not repeat - runs once" -> "every day, '
                      'no end set"', asked)
    def test_an_edit_is_kept_whole_with_its_changes(self) -> None:
        event = self._event()
        self._edit(event.id, title="Stretch your back", why="clearer")
        self.app.calendar_nature.wait()
        row = self._lesson_rows()[0]
        kept = json.loads(row["event"])
        self.assertEqual(kept["task"]["title"], "Stretch")
        self.assertEqual(kept["after"]["title"], "Stretch your back")
        self.assertEqual(kept["user_said"], "clearer")
        self.assertEqual(kept["changes"], [{"field": "title", "before": "Stretch",
                                            "after": "Stretch your back"}])
        # The panel says what was done and what it changed.
        lessons = self._get("/api/lessons")["kinds"][0]["lessons"]
        mine = next(one for one in lessons if one["action"] == "edit")
        self.assertEqual(mine["did"], "Edited by user")
        self.assertEqual(mine["title"], "Stretch your back")
        self.assertEqual(mine["changes"],
                         ['title: "Stretch" -> "Stretch your back"'])
        # What the agent is given is what was learned, never the record.
        self.assertNotIn("sat for three hours", self.app.calendar_nature.text())

    def test_the_panel_shows_the_changes_before_it_is_read(self) -> None:
        self.app.models.invoke = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("away"))
        event = self._event()
        self._edit(event.id, title="Stretch your back")
        self.app.calendar_nature.wait()
        lessons = self._get("/api/lessons")["kinds"][0]["lessons"]
        mine = next(one for one in lessons if one["action"] == "edit")
        self.assertIsNone(mine["reasoned_at"])
        self.assertEqual(mine["changes"],
                         ['title: "Stretch" -> "Stretch your back"'])


if __name__ == "__main__":
    unittest.main()
