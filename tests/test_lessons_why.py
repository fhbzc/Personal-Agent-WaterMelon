"""WHY THE USER DID IT, LOOKED FOR (the user, 2026-11-09: "能不能让模型学习的时候和你一样做reasoning";
"总结经验的模型需要有搜索的能力，比如可以自己去搜索邮件"). Reading what each record means, the lessons'
model has searches of the user's own records - activity, mail, chat, contacts - and says the reason
it found, sure or not; judging and inducing go by that reason. Everything here is made up."""

from __future__ import annotations

import json
import re
import unittest
from datetime import timedelta
from types import SimpleNamespace

from support import TemporaryAgent

from personalized_agent.common.events.models import new_id, utcnow
from personalized_agent.heartbeat.lessons.engine import LessonEngine
from personalized_agent.heartbeat.lessons.looking import Looking, TOOLS, local_time
from personalized_agent.models.base import ModelResponse, ModelUsage


class _Model:
    """Reading the features: first it looks at what the user was doing at the record's time, then
    it answers with the reason it found. Judging: it keeps what it was shown."""

    def __init__(self):
        self.asked, self.judged, self.looked = [], [], []

    def invoke(self, request, role=""):
        first = request.messages[0]["content"]
        self.asked.append(request)
        if "give each one the features" in first:
            if len(request.messages) == 1:
                at = re.search(r"at (\d{4}-\d\d-\d\d \d\d:\d\d)", first).group(1)
                self.offered = [one.name for one in request.tools or []]
                return SimpleNamespace(text="", stop_reason="tool_use", raw_content=[],
                                       tool_calls=[SimpleNamespace(
                                           id="t1", name="search_activity",
                                           arguments={"from_local": at, "query": ""})])
            self.looked.append(request.messages[-1]["content"][0]["content"])
            ids = re.findall(r"\[(evi_[0-9a-f]+)\]", first)
            return self._said({"features": {one: {
                "source": "screen", "event_type": "recommendation", "keywords": ["tips"],
                "english": "A tip was recommended while the user rested.",
                "why": "They were resting with a video; it was not about what was on their screen.",
                "sure": True} for one in ids}})
        if "judge:" in first:
            self.judged.append(first)
            return self._said({"judgements": []})
        return self._said({})

    @staticmethod
    def _said(value):
        return ModelResponse(text=json.dumps(value), usage=ModelUsage(10, 5))


class LookingForWhyTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.db = self.app.db
        at = (utcnow() - timedelta(minutes=5)).isoformat()
        self.db.execute("INSERT INTO states (id, title, detail, source, about, starts_at, ends_at, "
                        "status, created_at, updated_at) VALUES (?, ?, '', 'agent', 'user', ?, ?, "
                        "'active', ?, ?)", (new_id("state"), "Watching a film about lighthouses",
                                            at, utcnow().isoformat(), at, at))

    def test_it_looks_and_the_reason_it_found_is_kept_and_judged_by(self) -> None:
        model = _Model()
        engine = LessonEngine(self.db, model, self.app.config)
        rule = engine.rule_from_the_user("recommendations", "Do not send unverified tips",
                                         "the user said so")
        self.assertTrue(rule)
        engine.record("recommendations", "bad:n1", 'The agent recommended "A shell tip": use it - '
                      "the user called it a bad notification.", action="bad_notification")
        engine.learn("recommendations")
        self.assertEqual(model.offered, [one.name for one in TOOLS], "its searches offered")
        self.assertIn("Watching a film about lighthouses", model.looked[0],
                      "what they were doing then, found on their own records")
        row = self.db.query_one("SELECT features FROM lesson_evidence WHERE kind = 'recommendations'")
        features = json.loads(row["features"])
        self.assertIn("resting", features["why"])
        self.assertTrue(features["sure"])
        if model.judged:
            self.assertIn("why, as read: They were resting", model.judged[0])

    def test_the_prompts_ask_for_the_reason_and_go_by_it(self) -> None:
        from personalized_agent.heartbeat.lessons import engine
        self.assertIn("{why_rules}", engine.FEATURES_PROMPT)
        self.assertIn("BY THE USER'S REASON", engine.JUDGE_PROMPT)
        self.assertIn("REASON the user had", engine.INDUCE_PROMPT)

    def test_the_searches_read_their_records(self) -> None:
        looking = Looking(self.db)
        said = looking.run("search_activity", {"query": "lighthouses"})
        self.assertIn("Watching a film about lighthouses", json.dumps(said, ensure_ascii=False))
        self.assertIn("threads", looking.run("search_mail", {"query": "lighthouse keeper"}))
        self.assertIn("found", looking.run("search_people", {"query": "Wren Halloway"}))
        self.assertIn("error", looking.run("delete_everything", {}))
        self.assertRegex(local_time(utcnow().isoformat()), r"^\d{4}-\d\d-\d\d \d\d:\d\d$")


if __name__ == "__main__":
    unittest.main()
