"""WHAT THE USER MAY HAVE MISSED (heartbeat/monitor/missed_things.py; the user, 2026-11-20: "agent的heads up 不仅有agent是否
做了相应的事情，还要提醒user可能漏掉的事情" - every kind, no cap, the main model). Everything here is made up."""

from __future__ import annotations

import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from support import TemporaryAgent

from personalized_agent.common import modules
from personalized_agent.heartbeat.monitor import heads_up as hu
from personalized_agent.heartbeat.monitor import missed_things as mt

NOW = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)


class MissedThingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.db = self.app.db
        self.asked = []
        models = SimpleNamespace(invoke=lambda request, role=None: self.asked.append((request, role)))
        self.watch = hu.HeadsUp(self.db, models, self.app.notifications, self.app.config,
                                context=lambda: "OPEN TO-DOS:\n- sort the seed packets",
                                tell=lambda t, m, **k: self.app.monitor.relay("heads_up", t, m, **k))

    def event(self, kind: str, at: datetime, **data) -> None:
        self.db.execute("INSERT INTO raw_events (id, event_type, source, timestamp, application, data, metadata, "
                        "processed) VALUES (?, ?, ?, ?, ?, ?, '{}', 0)",
                        (str(uuid.uuid4()), kind, kind.split(".")[0], at.isoformat(),
                         str(data.get("application") or ""), json.dumps(data)))

    def chat(self, conversation: str, role: str, text: str, at: datetime) -> str:
        ident = str(uuid.uuid4())
        if not self.db.query_one("SELECT id FROM listener_wechat_conversations WHERE id = ?", (conversation,)):
            self.db.execute("INSERT INTO listener_wechat_conversations (id, account_id, person_id, peer_name, header_key, "
                            "peer_avatar, kind, identity_status, identity_basis, created_at, last_seen_at) VALUES "
                            "(?, 'a1', '', 'Bea of the allotment', ?, '', 'unknown', '', '', ?, ?)",
                            (conversation, f"h-{conversation}", at.isoformat(), at.isoformat()))
        self.db.execute("INSERT INTO listener_wechat_messages (id, conversation_id, sender_role, text, content_status, "
                        "first_seen_at, last_seen_at, payload) VALUES (?, ?, ?, ?, 'text', ?, ?, '{}')",
                        (ident, conversation, role, text, at.isoformat(), at.isoformat()))
        return ident

    def test_each_kind_is_gathered_by_code(self) -> None:
        self.chat("c1", "self", "I'll send you the seed list tomorrow", NOW - timedelta(days=2))
        asked = self.chat("c1", "other", "Can you water the beans on Saturday?", NOW - timedelta(hours=6))
        self.chat("c2", "other", "thanks!", NOW - timedelta(hours=1))           # too fresh to be unanswered
        self.db.execute("INSERT INTO agenda_items (id, source, ref_id, title, detail, fixed_at, place, project, status, "
                        "fingerprint, triage, created_at, updated_at) VALUES ('e1', 'calendar', 'r1', 'Seed swap', '', ?, "
                        "'', '', 'open', 'f1', '', ?, ?)",
                        ((NOW + timedelta(hours=5)).isoformat(), NOW.isoformat(), NOW.isoformat()))
        self.event("screen.text", NOW - timedelta(hours=2), application="firefox", window_title="Allotment news",
                   text="Plot renewals: the deadline is 30 November, forms at the shed.")
        found = mt.candidates(self.db, NOW)
        kinds = {one["kind"] for one in found}
        self.assertIn("message not answered", kinds)
        self.assertIn("promise in their words", kinds)
        self.assertTrue(any(k.startswith("calendar ahead") for k in kinds))
        self.assertIn("due soon", kinds)
        self.assertIn("deadline read", kinds)
        self.assertTrue(any(one["key"] == f"wechat:c1:{asked}" for one in found))
        self.assertFalse(any(one["key"].startswith("wechat:c2") for one in found), "a fresh one waits")

    def test_at_a_stopped_moment_the_main_model_judges_and_each_missed_is_told_once(self) -> None:
        asked = self.chat("c1", "other", "Can you water the beans on Saturday?", NOW - timedelta(hours=6))
        answer = {"missed": [{"key": f"wechat:c1:{asked}", "title": "Bea's question is waiting",
                              "say": "Bea asked you something six hours ago.\n- \"water the beans on Saturday\"",
                              "evidence": "Can you water the beans on Saturday?", "sure": 0.9, "urgent": False},
                             {"key": "made-up", "title": "Not a candidate", "say": "x", "sure": 1}]}
        with mock.patch("personalized_agent.models.answers.read_text", return_value=(json.dumps(answer), False)):
            self.assertIsNone(self.watch.tick(NOW), "not a stopped moment: nothing asked")
            self.event("presence.hands_off", NOW - timedelta(minutes=1))
            made = self.watch.tick(NOW)
        self.assertTrue(made)
        request, role = self.asked[-1]
        self.assertEqual((request.task, role), ("heads_up_missed", "fast_reply"),
                         "its own task, the small model (2026-11-20: \"这个还是都用小模型判断\")")
        given = json.loads(request.messages[0]["content"])
        self.assertTrue(given["candidates"][0]["new"])
        self.assertIn("sort the seed packets", given["what_they_mean_to_do"])
        shown = self.app.notifications.get(made)
        self.assertEqual(shown["title"], "Bea's question is waiting")
        self.assertEqual(shown["metadata"]["heads_up"]["kind"], "missed")
        self.assertIsNone(self.db.query_one("SELECT id FROM notifications WHERE title = 'Not a candidate'"),
                          "only what the code found is told")
        calls = len(self.asked)
        with mock.patch("personalized_agent.models.answers.read_text", return_value=(json.dumps(answer), False)):
            self.assertIsNone(self.watch.tick(NOW + timedelta(minutes=10)), "nothing new: not asked again")
        self.assertEqual(len(self.asked), calls)

    def test_its_tokens_are_counted_apart(self) -> None:
        self.assertEqual(modules.module_of("heads_up_missed"), "heads_up")
        self.assertEqual(hu.TASKS["missed"], mt.TASK)


if __name__ == "__main__":
    unittest.main()
