"""MISSED CHANCES (agent/missed_chances.py; the user, 2026-11-19: "把'错过的时机'当作学习信号：它没出声而用户自己来求助，记一次
反例；空闲时回看时间线，找重复操作、卡住的地方 ... 加到我们的learning里，类似于之前基于chat的学习"). Everything here is made up."""

from __future__ import annotations

import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from support import TemporaryAgent

from personalized_agent.agent import missed_chances as mc
from personalized_agent.heartbeat.monitor import heads_up as hu

NOW = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)


class MissedChancesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.asked = []
        self.asks = [{"at": (NOW - timedelta(minutes=20)).isoformat(), "text": "can you rename all these bean photos?"}]
        models = SimpleNamespace(invoke=lambda request, role=None: self.asked.append((request, role)))
        self.look = mc.MissedChances(self.app.db, models, self.app.lessons, self.app.config,
                                     chat=lambda after, before: [one for one in self.asks if after < one["at"] <= before],
                                     busy=lambda: False)

    def event(self, kind: str, at: datetime, **data) -> None:
        self.app.db.execute("INSERT INTO raw_events (id, event_type, source, timestamp, application, data, metadata, "
                            "processed) VALUES (?, ?, ?, ?, ?, ?, '{}', 0)",
                            (str(uuid.uuid4()), kind, kind.split(".")[0], at.isoformat(),
                             str(data.get("application") or ""), json.dumps(data)))

    def a_morning(self) -> None:
        for n in range(40):                     # the same rename, by hand, again and again
            at = NOW - timedelta(hours=2) + timedelta(minutes=2 * n)
            self.event("application.focus_ended", at, executable="explorer.exe", window_title=f"bean_{n}.jpg - Rename",
                       duration_seconds=45)
        self.event("browser.navigation", NOW - timedelta(minutes=50), browser="chrome", title="How to rename files",
                   url="https://example.org/rename?q=private")
        self.event("presence.away", NOW - timedelta(minutes=5))

    def run_look(self, answer, now=NOW):
        with mock.patch("personalized_agent.models.answers.read_text", return_value=(json.dumps(answer), False)):
            return self.look.tick(now)

    def test_a_time_it_was_silent_and_help_was_wanted_is_kept_as_a_lesson(self) -> None:
        self.a_morning()
        answer = {"missed": [
            {"category": "repetitive_work", "evidence_ids": ["e1", "e2", "e3"], "user_did": "renamed 40 photos by hand",
             "situation": "Renaming many files one by one in the file manager.",
             "when_to_offer": "When they rename several files by hand, offer to rename them all at once.",
             "why_worth_it": "It saves many minutes."},
            {"category": "stuck", "evidence_ids": ["e1"], "user_did": "x", "when_to_offer": "y"},          # one line: no
            {"category": "magic", "evidence_ids": ["e1", "e2"], "user_did": "x", "when_to_offer": "y"}]}   # no such: no
        self.assertEqual(self.run_look(answer), {"missed": 1})
        request, role = self.asked[-1]
        self.assertEqual((request.task, role), ("missed_chances", "primary"), "the main model, as the chat's learning")
        timeline = json.loads(request.messages[0]["content"])["timeline"]
        self.assertIn("bean_3.jpg - Rename", timeline)
        self.assertIn("asked YOU (the agent) in chat: can you rename all these bean photos?", timeline)
        self.assertIn("(example.org)", timeline)
        self.assertNotIn("q=private", timeline, "a page's host only, never its address")
        row = self.app.db.query_one("SELECT kind, action, reason, content FROM lesson_evidence WHERE action = 'missed_chance'")
        self.assertEqual((row["kind"], row["reason"]), ("notifications", "renamed 40 photos by hand"))
        self.assertEqual(json.loads(row["content"])["category"], "repetitive_work")
        self.assertIn("offer to rename them all at once", mc.recent(self.app.db))
        # looked over once: not again till more has happened and time has gone by
        self.assertIsNone(self.run_look(answer, NOW + timedelta(minutes=30)))

    def test_never_while_they_talk_in_private_mode_or_soon_after_the_last(self) -> None:
        self.a_morning()
        self.look.busy = lambda: True
        self.assertIsNone(self.run_look({"missed": []}))
        self.look.busy = lambda: False
        with mock.patch("personalized_agent.common.privacy_folders.why_no_picture", return_value="private mode"):
            self.assertIsNone(self.run_look({"missed": []}))
        self.assertEqual(self.asked, [], "nothing asked")

    def test_the_heads_up_is_told_to_offer_that_help_in_a_moment_like_one(self) -> None:
        self.assertIn("WHEN THEY NEEDED HELP AND THE AGENT WAS SILENT", hu.SYSTEM)
        self.app.lessons.record("notifications", "", "Renaming many files by hand.", action="missed_chance",
                                reason="renamed 40 photos by hand",
                                content=json.dumps({"category": "repetitive_work",
                                                    "when_to_offer": "When they rename files by hand, offer a batch rename."}))
        self.assertIn("offer a batch rename", self.app._heads_up_context())
        self.assertIn("WHEN THEY NEEDED HELP AND THE AGENT WAS SILENT", self.app._heads_up_context())


if __name__ == "__main__":
    unittest.main()
