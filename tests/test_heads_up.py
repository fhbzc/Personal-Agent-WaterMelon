"""HEADS UP - THE SIDE WATCH (heartbeat/monitor/heads_up.py; the user, 2026-11-17: "类似claude的side agent monitor, 观察你
在screen上干什么，然后给你notification那种（比如你漏掉的东西)" - "monitor的token usage要不同的trigger单独监控"). Everything
here is made up."""

from __future__ import annotations

import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from support import TemporaryAgent

from personalized_agent.common.notifications import category_of
from personalized_agent.heartbeat.monitor import heads_up as hu

NOW = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)


class HeadsUpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.asked = []
        self.answer = {"heads_up": True, "title": "The test you asked for is not there",
                       "say": "You asked for a test of the bean sorter.\n- Its turn added no test\n- Ask it to add one",
                       "evidence": "please add a test", "sure": 0.9, "urgent": False}
        models = SimpleNamespace(invoke=lambda request, role=None: self.asked.append((request, role)))
        self.watch = hu.HeadsUp(self.app.db, models, self.app.notifications, self.app.config,
                                context=lambda: "OPEN TO-DOS:\n- ship the bean sorter",
                                tell=lambda t, m, **k: self.app.monitor.relay("heads_up", t, m, **k))

    def event(self, kind: str, at: datetime, **data) -> None:
        self.app.db.execute("INSERT INTO raw_events (id, event_type, source, timestamp, application, data, metadata, "
                            "processed) VALUES (?, ?, ?, ?, ?, ?, '{}', 0)",
                            (str(uuid.uuid4()), kind, kind.split(".")[0], at.isoformat(),
                             str(data.get("application") or ""), json.dumps(data)))

    def tick(self, now=NOW, answer=None):
        with mock.patch("personalized_agent.models.answers.read_text",
                        return_value=(json.dumps(answer or self.answer), False)):
            return self.watch.tick(now)

    def test_an_assistants_turn_done_is_looked_at_its_tokens_apart(self) -> None:
        self.event("assistant.message", NOW - timedelta(minutes=3), role="user", session_id="s1", tool="claude-code",
                   project="bean-sorter", text="please add a test for the sorter")
        self.event("assistant.message", NOW - timedelta(seconds=30), role="assistant", session_id="s1",
                   tool="claude-code", project="bean-sorter", text="Done - the sorter is fixed.")
        self.assertIsNone(self.tick(), "still going: quiet less than a minute")
        made = self.tick(NOW + timedelta(seconds=45))
        self.assertTrue(made)
        request, role = self.asked[-1]
        self.assertEqual((request.task, role), ("heads_up_assistant", "fast_reply"), "its own task, the small model")
        given = json.loads(request.messages[0]["content"])
        self.assertIn("please add a test", given["what_they_did"])
        self.assertIn("ship the bean sorter", given["what_they_mean_to_do"])
        shown = self.app.notifications.get(made)
        self.assertEqual(category_of(shown), "Heads up")
        self.assertEqual(shown["metadata"]["monitor"]["activator"], "Heads up", "said through the Monitor")
        self.assertEqual(shown["metadata"]["heads_up"]["kind"], "assistant")
        # Told the queue's main model where it came from and the content it is about (2026-11-17).
        self.assertEqual(shown["metadata"]["created_by"], "heads_up")
        self.assertIn("bean-sorter", shown["metadata"]["related_to"])
        self.assertIsNone(self.tick(NOW + timedelta(minutes=30)), "that turn looked at once")

    def test_a_message_paused_over_is_looked_at(self) -> None:
        self.event("screen.text", NOW - timedelta(seconds=20), application="OUTLOOK", window_title="Seed club - Message",
                   handle=7, text="Hi Ana, the seed list is attached. See you at the club on Tuesday at 3:30. " * 2)
        self.assertTrue(self.tick(answer=dict(self.answer, title="No attachment")))
        self.assertEqual(self.asked[-1][0].task, "heads_up_message")
        self.assertIsNone(self.tick(NOW + timedelta(minutes=10)), "the same draft: not again")

    def test_a_document_left_after_a_while_is_looked_at(self) -> None:
        began = NOW - timedelta(minutes=12)
        self.event("screen.text", began + timedelta(minutes=1), application="WINWORD", window_title="beans.docx - Word",
                   text="Bean report\nIntro")
        self.event("screen.text", began + timedelta(minutes=9), application="WINWORD", window_title="beans.docx - Word",
                   text="Bean report\nIntro\nResults: 40 beans sorted")
        self.event("application.focus_ended", NOW - timedelta(minutes=2), executable="WINWORD.EXE",
                   window_title="beans.docx - Word", duration_seconds=600)
        self.assertTrue(self.tick(answer=dict(self.answer, title="Results without a method")))
        request = self.asked[-1][0]
        self.assertEqual(request.task, "heads_up_document")
        self.assertIn("Results: 40 beans sorted", json.loads(request.messages[0]["content"])["what_they_did"])

    def test_kept_within_bounds_and_only_when_sure(self) -> None:
        self.event("screen.text", NOW - timedelta(seconds=20), application="WeChat", window_title="WeChat",
                   handle=9, text="Sure, I will send the seed list to you by Friday, and the club dates too. " * 2)
        self.assertIsNone(self.tick(answer=dict(self.answer, sure=0.4)), "not sure enough: nothing said")
        self.assertIsNone(self.tick(NOW + timedelta(seconds=60)), "too soon after the last look")
        self.app.config.set("module_heads_up_enabled", False, internal=True)
        self.assertIsNone(self.tick(NOW + timedelta(hours=1)), "off, by its own switch under the Monitor")
        self.app.config.set("module_heads_up_enabled", True, internal=True)
        self.app.config.set("module_monitor_enabled", False, internal=True)
        self.assertIsNone(self.tick(NOW + timedelta(hours=1)), "off with the Monitor")
        self.app.config.set("module_monitor_enabled", True, internal=True)
        self.app.config.set("listener_private_mode", True, internal=True)
        self.assertIsNone(self.tick(NOW + timedelta(hours=2)), "never in private mode")

    def test_its_tasks_are_known(self) -> None:
        from personalized_agent.common.modules import TASK_MODULE
        for task in hu.TASKS.values():
            self.assertEqual(TASK_MODULE.get(task), "heads_up", "its tokens counted apart, under the Monitor")
        self.assertIn("OPEN TO-DOS", self.app._heads_up_context() or "OPEN TO-DOS")


if __name__ == "__main__":
    unittest.main()
