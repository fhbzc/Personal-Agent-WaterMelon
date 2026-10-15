"""An English copy beside what the user reads - only when they do not read
English: what is compared and measured is read in English (the embedding
model reads English only). And the user's say on stories - more like this,
not interested - carried into the next issue's search and score."""

from __future__ import annotations

import json
import unittest

from support import TemporaryAgent

from personalized_agent.common.languages import needs_english_copy
from personalized_agent.heartbeat.connections import recommend
from personalized_agent.models.base import ModelResponse


class _Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class OnlyWhenNotEnglishTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_needed_only_when_the_user_reads_another_language(self) -> None:
        self.assertFalse(needs_english_copy(_Config(display_language="en")))
        self.assertTrue(needs_english_copy(_Config(display_language="zh")))

    def test_a_task_summary_asks_for_it_only_then(self) -> None:
        beat = self.app.state_heartbeat
        self.app.config.set("display_language", "en")
        english = beat._instruction()
        self.assertNotIn('"in_english"', english, "nothing asked when the user reads English")
        self.assertNotIn("{in_english}", english)
        self.app.config.set("display_language", "zh")
        self.assertIn('"in_english"', beat._instruction())

    def test_the_model_is_told_not_to_repeat_what_is_english_already(self) -> None:
        from personalized_agent.heartbeat.connections.recommend import STORY_ENGLISH
        from personalized_agent.heartbeat.states import heartbeat as h
        for said in (h.SUMMARY_ENGLISH, h.STATUS_ENGLISH, h.PROJECT_ENGLISH, STORY_ENGLISH):
            self.assertIn("each word you write is paid for", " ".join(said.split()))
            self.assertRegex(" ".join(said.split()).lower(), r"(in english already|already in english)")

    def test_a_story_asks_for_it_only_then(self) -> None:
        round_ = self.app.connections_round
        self.app.config.set("display_language", "en")
        self.assertEqual(round_._english_field(), "")
        self.app.config.set("display_language", "zh")
        self.assertIn('"english"', round_._english_field())


class KeptAndUsedTests(unittest.TestCase):

    def test_an_amendment_keeps_the_copy_it_does_not_send(self) -> None:
        from personalized_agent.heartbeat.states.heartbeat import _amended
        said = _amended({"status": "新的", "in_english": {"status": "the new one"}},
                        {"title": "标题", "in_english": {"title": "The title",
                                                        "status": "the old one"}})
        self.assertEqual(said["in_english"], {"title": "The title", "status": "the new one"})

    def test_a_story_is_measured_by_its_english(self) -> None:
        self.assertEqual(recommend.story_text({"title": "钢琴", "detail": "踏板",
                                               "english": "Piano pedalling"}),
                         "Piano pedalling")
        self.assertEqual(recommend.story_text({"title": "Piano", "detail": "Pedals"}),
                         "Piano. Pedals")

    def test_a_task_is_measured_by_its_english_copy_and_states(self) -> None:
        from test_recommend import _task
        agent = TemporaryAgent()
        try:
            db = agent.app.db
            _task(db, "t1", "整理论文", [("Revising the replication scripts",
                                          "The fetch step failed twice.")])
            row = db.query_one("SELECT described FROM state_tasks WHERE id = 't1'")
            described = json.loads(row["described"])
            described["in_english"] = {"title": "Revise the paper",
                                       "summary": "Revise the replication package."}
            db.execute("UPDATE state_tasks SET described = ? WHERE id = 't1'",
                       (json.dumps(described, ensure_ascii=False),))
            found = recommend.subjects(db)[0]
            self.assertTrue(found["measured"][0].startswith("Revise the paper."))
            self.assertIn("Revising the replication scripts: The fetch step failed twice.",
                          found["measured"])
            self.assertTrue(all("整理" not in line for line in found["measured"]))
        finally:
            agent.cleanup()


class NaturesSearchedInEnglishTests(unittest.TestCase):

    def test_a_stage_by_its_english_keywords(self) -> None:
        from personalized_agent.heartbeat.experience.stage_natures import _stage_text
        self.assertEqual(_stage_text({"summary": "整理了脚本", "work": {
            "keywords": ["replication script", "fetch retry"]}}),
            "replication script fetch retry")
        self.assertIn("整理了脚本", _stage_text({"summary": "整理了脚本", "work": {}}),
                      "a stage from before keywords: as it was")

    def test_a_task_unit_by_its_english_copy(self) -> None:
        from types import SimpleNamespace
        from personalized_agent.heartbeat.experience.stages import StageWriter
        task = SimpleNamespace(id="t1", title="整理论文", described={
            "title": "整理论文", "goal": "投稿",
            "in_english": {"title": "Revise the paper", "goal": "Submit it"}})
        unit = StageWriter.units(SimpleNamespace(), [task], [])[0]
        self.assertEqual(unit["english"], "Revise the paper Submit it")


class _Embed:
    def embed(self, texts, while_ok=None):
        return [[1.0, 0.0] if "piano" in text.lower() else [0.0, 1.0] for text in texts]


class TheUsersSayTests(unittest.TestCase):

    def setUp(self) -> None:
        self.page = {"key": "p", "title": "Piano", "topic_ids": [],
                     "anchors": [[1.0, 0.0], [0.0, 1.0]]}

    def test_like_what_they_asked_for_more_of_scores_higher(self) -> None:
        stories = [{"id": "a", "title": "A piano method", "gain": 4, "page": "p",
                    "sources": ["https://x.example/a"]},
                   {"id": "b", "title": "A recipe", "gain": 4, "page": "p",
                    "sources": ["https://x.example/b"]}]
        plain = {one["id"]: one["score"] for one in recommend.lay_out(
            stories, [self.page], _Embed(), [], [], set())[0]}
        said = {one["id"]: one["score"] for one in recommend.lay_out(
            stories, [self.page], _Embed(), [], [], set(), liked=[[1.0, 0.0]],
            disliked=[[0.0, 1.0]])[0]}
        self.assertGreater(said["a"], plain["a"])
        self.assertLess(said["b"], plain["b"])

    def test_the_search_is_given_the_examples_near_its_page(self) -> None:
        agent = TemporaryAgent()
        try:
            app = agent.app
            ident = app.connections.save_briefing([], [
                {"id": "know_1", "title": "钢琴踏板", "english": "Piano pedalling explained",
                 "sources": ["https://x.example/1"]},
                {"id": "know_2", "title": "菜谱", "english": "A recipe for soup",
                 "sources": ["https://x.example/2"]}],
                {"paper": {"front": [], "brief": [], "pages": []}})
            recommend.record_feedback(app.db, app.connections, ident, "know_1", "more")
            recommend.record_feedback(app.db, app.connections, ident, "know_2", "less")
            round_ = app.connections_round
            round_.embedder = _Embed()
            examples = round_._examples()
            self.assertEqual(sorted(one["action"] for one in examples), ["less", "more"])
            piano = {"anchors": [[1.0, 0.0]]}
            self.assertIn("Piano pedalling explained", round_._near(examples, piano, "more"))
            self.assertEqual(round_._near(examples, piano, "less"), "None yet.",
                             "not near this page")

            asked = []

            class _Models:
                config = app.config

                def invoke(self, request, role=""):
                    asked.append(request.messages[0]["content"])
                    return ModelResponse(text=json.dumps({"items": []}))

            round_.models = _Models()
            from datetime import datetime, timedelta, timezone
            round_._page_branch({"calls": 0, "web": 0, "tokens_in": 0, "tokens_out": 0},
                                lambda: True, [dict(piano, key="p", title="Piano",
                                                    subjects=[{"kind": "interest",
                                                               "title": "Piano"}],
                                                    detail=["READ: a piano video"],
                                                    topic_ids=[])], "today",
                                datetime.now(timezone.utc) - timedelta(days=1))
            self.assertIn("THEY ASKED FOR MORE LIKE THESE", asked[0])
            self.assertIn("Piano pedalling explained", asked[0])
        finally:
            agent.cleanup()


if __name__ == "__main__":
    unittest.main()


class FilledWhereMissingTests(unittest.TestCase):
    """A copy missing, or of words since rewritten, translated - and only
    those; nothing when the user reads English."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        from test_recommend import _task
        _task(self.app.db, "t1", "整理论文", [("Revising the scripts", "Two failures.")])
        self.asked = []
        outer = self

        class _Models:
            def invoke(self, request, role=""):
                said = json.loads(request.messages[0]["content"])
                outer.asked.append(said)
                return ModelResponse(text=json.dumps({"items": [
                    {"id": one["id"], "fields": {k: f"EN {k}" for k in one["fields"]}}
                    for one in said["items"]]}))

        self.models = _Models()

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _described(self):
        row = self.app.db.query_one("SELECT described FROM state_tasks WHERE id = 't1'")
        return json.loads(row["described"])

    def test_missing_then_stale_then_nothing(self) -> None:
        from personalized_agent.heartbeat.states import english_copies
        self.app.config.set("display_language", "zh")
        done = english_copies.fill(self.app.db, self.models, self.app.config)
        self.assertEqual(done["filled"], 2)
        english = self._described()["in_english"]
        self.assertEqual((english["title"], english["summary"]), ("EN title", "EN summary"))
        # Nothing new: nothing asked.
        english_copies.fill(self.app.db, self.models, self.app.config)
        self.assertEqual(len(self.asked), 1)
        # The summary rewritten without its copy: that one, again.
        said = self._described()
        said["summary"] = "新的摘要"
        self.app.db.execute("UPDATE state_tasks SET described = ? WHERE id = 't1'",
                            (json.dumps(said, ensure_ascii=False),))
        english_copies.fill(self.app.db, self.models, self.app.config)
        self.assertEqual(list(self.asked[-1]["items"][0]["fields"]), ["summary"])

    def test_nothing_when_the_user_reads_english(self) -> None:
        from personalized_agent.heartbeat.states import english_copies
        self.app.config.set("display_language", "en")
        english_copies.fill(self.app.db, self.models, self.app.config)
        self.assertEqual(self.asked, [])

    def test_a_copy_written_with_the_summary_is_not_asked_again(self) -> None:
        from personalized_agent.heartbeat.states import english_copies
        self.app.config.set("display_language", "zh")
        said = self._described()
        said["in_english"] = english_copies.stamped(
            {"title": "Revise the paper", "summary": "Revise it."}, said, ["title", "summary"])
        self.app.db.execute("UPDATE state_tasks SET described = ? WHERE id = 't1'",
                            (json.dumps(said, ensure_ascii=False),))
        english_copies.fill(self.app.db, self.models, self.app.config)
        self.assertEqual(self.asked, [])
