"""A REMINDER THE AGENT WROTE, READ AGAIN AT ITS TIME (heartbeat/monitor/still_so.py; the user, 2026-11-15: "是不是在
通知发布之前要对这个再做一个检查，看看是否过时" - a reminder that a build waited for approval was told after the build
had been approved, run and passed), and A NOTIFICATION'S "GO TO IT" in the report ("应该能直接到他这个task item的
位置"). Fictional throughout."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from personalized_agent.agent.events import Events  # noqa: E402
from personalized_agent.common.events.models import ScheduledTask  # noqa: E402
from personalized_agent.data_manage.storage.database import Database  # noqa: E402
from personalized_agent.heartbeat.monitor import still_so  # noqa: E402
from personalized_agent.scheduler.execution.executors import TaskExecutor  # noqa: E402

WEB = ROOT / "personalized_agent" / "api" / "web"
NOW = datetime(2026, 11, 20, 15, 10, tzinfo=timezone.utc)
WRITTEN = NOW - timedelta(minutes=50)


class _Models:
    def __init__(self, answer: str = "{}", fails: bool = False) -> None:
        self.answer, self.fails, self.asked = answer, fails, []

    def invoke(self, request, role=""):
        self.asked.append((request, role))
        if self.fails:
            raise RuntimeError("no model")
        return SimpleNamespace(text=self.answer)


def _task(by: str = "heartbeat", **more) -> ScheduledTask:
    return ScheduledTask(title="The build waits for your approval", scheduled_at=NOW, created_by=by,
                         created_at=WRITTEN, description="Run 17 of the garden app waits for Approve.",
                         reason="The Actions page showed run 17 waiting.",
                         task_payload={"message": "Run 17 of the garden app waits for Approve.",
                                       "notify_user": True}, **more)


class TheCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._dir.cleanup)
        self.db = Database(Path(self._dir.name) / "agent.db")
        self.addCleanup(self.db.close)

    def _seen(self, minutes_after: int, text: str) -> None:
        at = (WRITTEN + timedelta(minutes=minutes_after)).isoformat()
        self.db.execute("INSERT INTO raw_events (id, event_type, source, timestamp, application, data, metadata, "
                        "processed) VALUES (?, 'screen.text', 'listener', ?, 'browser', ?, '{}', 0)",
                        (f"ev{minutes_after}", at, json.dumps({"text": text, "window_title": "Actions"})))

    def _check(self, models, task=None):
        task = task or _task()
        return still_so.check(self.db, models, task, task.title, task.task_payload["message"], now=NOW)

    def test_the_users_own_are_told_as_they_are_and_nothing_is_asked(self) -> None:
        self._seen(10, "Run 17 passed")
        models = _Models('{"verdict": "drop", "why": "it passed"}')
        self.assertEqual(self._check(models, _task(by="user"))["verdict"], still_so.SEND)
        self.assertEqual(models.asked, [])

    def test_the_chats_and_delegated_works_read_again_too_and_held_back_only_on_evidence(self) -> None:
        # The user, 2026-11-15: "我觉得都覆盖，但是要明确和agent说，必须有充足的证据说明是已经做好了才取消弹出".
        self._seen(10, "Run 17 passed")
        models = _Models('{"verdict": "drop", "why": "The Actions page showed run 17 passed at 14:30."}')
        self.assertEqual(self._check(models, _task(by="agent"))["verdict"], still_so.DROP)
        self.assertIn("ONLY on sufficient evidence", models.asked[0][0].system)
        self.assertIn('when in doubt, "send"', models.asked[0][0].system)

    def test_nothing_seen_since_it_was_written_no_call(self) -> None:
        self._seen(-5, "Run 17 waits for approval")              # before it was written
        models = _Models('{"verdict": "drop", "why": "x"}')
        self.assertEqual(self._check(models)["verdict"], still_so.SEND)
        self.assertEqual(models.asked, [])

    def test_over_by_what_was_seen_not_told(self) -> None:
        self._seen(20, "Run 17 - Build macos: success. All jobs passed.")
        models = _Models('{"verdict": "drop", "why": "The Actions page showed run 17 passed at 14:30."}')
        said = self._check(models)
        self.assertEqual(said["verdict"], still_so.DROP)
        self.assertIn("passed", said["why"])
        request, role = models.asked[0]
        self.assertEqual(role, "heartbeat", "the heartbeat's model, not the small one")
        self.assertIn("Run 17 - Build macos: success", request.messages[0]["content"])
        self.assertIn("Run 17 of the garden app waits for Approve.", request.messages[0]["content"])

    def test_still_to_tell_in_new_words(self) -> None:
        self._seen(20, "Run 17 approved, building")
        models = _Models('{"verdict": "rewrite", "title": "The build is running", '
                         '"message": "Run 17 was approved and is building now.", "why": "approved at 14:20"}')
        said = self._check(models)
        self.assertEqual((said["verdict"], said["title"], said["message"]),
                         (still_so.REWRITE, "The build is running", "Run 17 was approved and is building now."))

    def test_when_unsure_or_broken_told_as_written(self) -> None:
        self._seen(20, "something")
        task = _task()
        for models in (_Models("not json"), _Models('{"verdict": "drop"}'), _Models('{"verdict": "rewrite"}'),
                       _Models(fails=True)):
            said = self._check(models, task)
            self.assertEqual((said["verdict"], said["title"], said["message"]),
                             (still_so.SEND, task.title, task.task_payload["message"]))

    def test_written_again_later_counts_from_then(self) -> None:
        from personalized_agent.agent.reports import did
        task = _task()
        self._seen(10, "Run 17 waits for approval")
        did(self.db, "notifications_updated", "Changed on your calendar: the build", ref=task.id)
        self.db.execute("UPDATE agent_did SET at = ?", ((WRITTEN + timedelta(minutes=30)).isoformat(),))
        self.assertEqual(still_so.written_at(self.db, task), WRITTEN + timedelta(minutes=30))


class TheExecutorTests(unittest.TestCase):
    def _executor(self, verdict=None, fails=False):
        told = []
        executor = TaskExecutor(runtime=None, models=None, notifications=None, recorder=None)
        executor.relay = lambda builtin, title, message, **said: told.append((title, message, said))

        def still(task, title, message):
            if fails:
                raise RuntimeError("broken")
            return verdict
        executor.still_so = still if verdict is not None or fails else None
        return executor, told

    def test_dropped_not_told(self) -> None:
        executor, told = self._executor({"verdict": "drop", "why": "it passed"})
        outcome = executor.execute(_task())
        self.assertTrue(outcome.ok)
        self.assertEqual(told, [])
        self.assertIn("no longer so", outcome.result)

    def test_rewritten_told_anew_with_why(self) -> None:
        executor, told = self._executor({"verdict": "rewrite", "title": "Now running", "message": "Approved.",
                                         "why": "approved"})
        executor.execute(_task())
        self.assertEqual(told[0][:2], ("Now running", "Approved."))
        self.assertEqual(told[0][2]["metadata"]["rewritten"], "approved")

    def test_send_and_broken_told_as_written(self) -> None:
        for verdict, fails in (({"verdict": "send"}, False), (None, True), (None, False)):
            executor, told = self._executor(verdict, fails)
            executor.execute(_task())
            self.assertEqual(told[0][:2], ("The build waits for your approval",
                                           "Run 17 of the garden app waits for Approve."))


class GoToItTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._dir.cleanup)
        self.db = Database(Path(self._dir.name) / "agent.db")
        self.addCleanup(self.db.close)
        self.events = Events(SimpleNamespace(db=self.db, models=None))

    def _entry(self, key: str, event: str, thing: str = "", at: str = "2026-11-20T15:00:00+00:00") -> None:
        if not self.db.query_one("SELECT id FROM report_events WHERE id = ?", (event,)):
            self.db.execute("INSERT INTO report_events (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                            (event, "The garden app's release", at, at))
        self.db.execute("INSERT INTO report_event_items (key, event_id, title, added_at, thing) "
                        "VALUES (?, ?, ?, ?, ?)", (key, event, key, at, thing))

    def test_a_reminder_goes_to_its_plate_item_before_what_the_agent_did_with_it(self) -> None:
        self.db.execute("INSERT INTO agenda_items (id, source, ref_id, title, status, created_at, updated_at) "
                        "VALUES ('agenda_abc123', 'calendar', 'task_r1', 'The build should be done', 'open', "
                        "'x', 'x')")
        from personalized_agent.agent.reports import did
        did(self.db, "notifications_updated", "Changed on your calendar: the build", ref="task_r1")
        made = self.db.query_one("SELECT id FROM agent_did")["id"]
        self._entry("mine:agenda_abc123", "ev_1", "agenda:agenda_abc123", at="2026-11-20T14:00:00+00:00")
        self._entry(f"did:{made}", "ev_1", "", at="2026-11-20T15:00:00+00:00")
        self.assertEqual(self.events.place_of({"task_id": "task_r1"}),
                         {"event": "ev_1", "key": "mine:agenda_abc123"})

    def test_a_plate_item_named_in_it_and_a_merged_event_followed(self) -> None:
        self._entry("approve:agenda:agenda_def456", "ev_old", "agenda:agenda_def456")
        self.db.execute("INSERT INTO report_events (id, title, created_at, updated_at) VALUES "
                        "('ev_new', 'Together', 'x', 'x')")
        self.db.execute("UPDATE report_events SET merged_into = 'ev_new' WHERE id = 'ev_old'")
        self.assertEqual(self.events.place_of({"agenda": {"approve": ["agenda_def456"]}}),
                         {"event": "ev_new", "key": "approve:agenda:agenda_def456"})

    def test_in_no_event_nothing(self) -> None:
        self.assertEqual(self.events.place_of({"task_id": "task_none"}), {})
        self.assertEqual(self.events.place_of({}), {})

    def test_the_panel_offers_it_and_the_report_opens_there(self) -> None:
        notes = (WEB / "notes.js").read_text("utf-8")
        self.assertIn("data-notes-go", notes)
        self.assertIn('"/api/notifications/go_to"', notes)
        page = (WEB / "app.js").read_text("utf-8")
        self.assertIn("function goToReportPlace", page)
        self.assertIn('data-entry-key="${escapeHtml(key || "")}"', page)
        self.assertIn("if (said && said.report) goToReportPlace(said.report);", page)
        routes = (ROOT / "personalized_agent" / "api" / "routes.py").read_text("utf-8")
        self.assertIn('"/api/notifications/go_to"', routes)
        self.assertIn('report_wanted', routes)


if __name__ == "__main__":
    unittest.main()
