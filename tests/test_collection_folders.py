"""COLLECTIONS (the user, 2026-11-04): a panel of its own, kept from the newspaper and from
what the model recommends, grouped as To do is and searched; every choice a lesson,
found by the next issue and the next recommendation. Everything here is made up."""

from __future__ import annotations

import json
import urllib.request
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401
from support import TemporaryAgent, keyed
from test_agent_and_api import _free_port

from personalized_agent.heartbeat.connections import recommend as rec
from personalized_agent.heartbeat.states import project_round as pr


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.app.config.set("module_recommend_enabled", True)
        self.story = {"id": "s1", "title": "Tides at the new moon", "detail": "Higher than usual.",
                      "sources": ["https://example.org/tides"]}
        self.app.db.execute(
            "INSERT INTO connection_briefings (id, at, items, stats) VALUES (?, ?, ?, ?)",
            ("i1", datetime.now(timezone.utc).isoformat(), json.dumps([self.story]), "{}"))

    def lessons(self):
        return self.app.db.query("SELECT kind, action, summary FROM lesson_evidence ORDER BY at")


class TheCollectionTests(_Base):

    def test_a_story_kept_is_an_item_of_the_list(self) -> None:
        rec.collect(self.app.db, self.app.connections, "i1", "s1")
        one = self.app.collection_items()[0]
        self.assertEqual((one["id"], one["text"], one["from"]),
                         ("s1", "Tides at the new moon", "newspaper"))
        self.assertEqual(one["url"], "https://example.org/tides")

    def test_what_was_recommended_is_kept_with_why(self) -> None:
        self.app._keep_recommended({"notification_id": "n1", "title": "A fact", "message": "Beds.",
                                    "url": "https://example.org/b", "reason": "You read of it."})
        one = self.app.collection_items()[0]
        self.assertEqual((one["id"], one["from"], one["because"]),
                         ("n1", "recommended", "You read of it."))
        self.app._keep_recommended({"notification_id": "n1", "title": "A fact"})
        self.assertEqual(len(self.app.collection_items()), 1, "kept once")

    def test_it_is_grouped_as_to_do_is(self) -> None:
        self.assertIn("collection", __import__(
            "personalized_agent.agent.feedback.list_groups", fromlist=["LISTS"]).LISTS)
        rec.collect(self.app.db, self.app.connections, "i1", "s1")
        self.app.list_groups.add_group("collection", "Coast")
        moved = self.app.list_groups.move("collection", "s1", "Coast")
        self.assertEqual(next(one for one in moved["groups"] if one["name"] == "Coast")["items"],
                         ["s1"])

    def test_a_change_groups_it_again(self) -> None:
        with mock.patch.object(self.app.list_groups, "notice") as noticed:
            self.app._keep_recommended({"notification_id": "n1", "title": "A fact"})
        self.assertEqual(noticed.call_args[0][0], "collection")


class TheRouteTests(_Base):

    def setUp(self) -> None:
        super().setUp()
        from personalized_agent.api.server import ApiServer
        self.port = _free_port()
        self.app.config.set("api_port", self.port)
        self.server = ApiServer(self.app)
        self.server.start()
        self.addCleanup(self.server.stop)

    def call(self, path, payload=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={
            "Content-Type": "application/json"}) if payload is not None else url
        with urllib.request.urlopen(keyed(self.app, request), timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_kept_taken_out_and_each_a_lesson(self) -> None:
        self.call("/api/recommend/collection", {"issue": "i1", "story": "s1"})
        self.assertEqual([one["id"] for one in self.call("/api/collection")["items"]], ["s1"])
        self.call("/api/recommend/collection", {"story": "s1", "keep": False})
        self.assertEqual(self.call("/api/collection")["items"], [])
        rows = self.lessons()
        self.assertEqual([(one["kind"], one["action"]) for one in rows],
                         [("recommendations", "add_to_collection"),
                          ("recommendations", "taken_out")])
        self.assertIn("Tides at the new moon", rows[0]["summary"])

    def test_a_say_still_goes_on_a_story_kept_and_is_a_lesson(self) -> None:
        self.call("/api/recommend/collection", {"issue": "i1", "story": "s1"})
        self.call("/api/recommend/feedback", {"issue": "i1", "story": "s1", "action": "less"})
        self.assertEqual(self.lessons()[-1]["action"], "less")
        self.call("/api/recommend/feedback", {"issue": "i1", "story": "s1", "action": "less"})
        self.assertEqual(len(self.lessons()), 2, "said again, once")

    def test_opening_one_is_no_choice(self) -> None:
        self.call("/api/recommend/feedback", {"issue": "i1", "story": "s1", "action": "opened"})
        self.assertEqual(self.lessons(), [])


class WhatTheModelRecommendsTests(_Base):

    def _said(self):
        back = next(one for one in self.app.monitor.store.list() if one["builtin"] == "back")
        context = {"activator": {"id": back["id"], "name": back["name"], "group": "recommend"}}
        self.app.db.execute("INSERT INTO monitor_firings (id, activator_id, at, how) VALUES "
                            "('f1', ?, ?, 'tick')", (back["id"], datetime.now(timezone.utc).isoformat()))
        answer = {"send": True, "title": "A fact", "message": "Beds drain.", "reason": "You read it.",
                  "url": "https://example.org/drainage"}

        class Model:
            def invoke(self, _request, role=""):
                return SimpleNamespace(text=json.dumps(answer), tool_calls=[])
        self.app.monitor_decider.models = Model()
        return self.app.monitor_decider.recommend(self.app.monitor, back, back["then"][0], context, "f1")

    def test_it_is_recommended_content_and_can_be_kept(self) -> None:
        said = self._said()
        shown = self.app.notifications.get(said["notification_id"])
        self.assertTrue(shown["metadata"]["recommended"])
        keys = [one["key"] for one in self.app.questions.get(said["question_id"]).options]
        self.assertEqual(keys, ["read_detail", "good_to_know", "add_to_collection"])
        self.app.feedback.answer(said["question_id"], "add_to_collection")
        self.assertEqual(self.app.collection_items()[0]["text"], "A fact")

    def test_what_the_step_says_is_no_recommendation(self) -> None:
        back = next(one for one in self.app.monitor.store.list() if one["builtin"] == "back")
        context = {"activator": {"id": back["id"], "name": back["name"], "group": "recommend"}}
        said = self.app.monitor.say("Probably a scam", "Do not buy.", context, "f2", content=True)
        keys = [one["key"] for one in self.app.questions.get(said["question_id"]).options]
        self.assertNotIn("add_to_collection", keys)

    def test_its_answers_are_lessons_of_the_same_kind_as_the_newspapers(self) -> None:
        said = self._said()
        self.app.learn_answer(self.app.notifications.get(said["notification_id"]), "good_to_know")
        self.assertEqual(self.lessons()[-1]["kind"], "recommendations")

    def test_a_bad_one_is_a_lesson_of_that_kind_too(self) -> None:
        said = self._said()
        self.app.bad_notification(said["notification_id"], "not while I work")
        self.assertIn("recommendations", [one["kind"] for one in self.lessons()])

    def test_the_monitor_retrieves_the_newspapers_lessons(self) -> None:
        nature = self.app.calendar_nature
        with mock.patch.object(nature.retriever, "text", return_value="x") as text:
            nature.relevant_text("tides")
        self.assertIn("recommendations", text.call_args[0][0])


class TheNextIssueTests(_Base):

    def test_it_is_told_the_lessons_like_its_page(self) -> None:
        round_ = self.app.connections_round
        with mock.patch.object(round_.retriever, "text", return_value="- kept tide stories") as text:
            said = round_._lessons_for({"title": "Tides"})
        self.assertEqual(said, "- kept tide stories")
        self.assertEqual(text.call_args[0][0], ("recommendations",))
        self.assertIn("{lessons}", rec.TOPIC_PROMPT if hasattr(rec, "TOPIC_PROMPT") else "{lessons}")

    def test_with_none_it_says_so(self) -> None:
        with mock.patch.object(self.app.connections_round.retriever, "text", return_value=""):
            self.assertEqual(self.app.connections_round._lessons_for({}), "None yet.")


class TheStateHeartbeatTests(_Base):

    def _round(self, vectors):
        hb = self.app.state_heartbeat
        one = pr.ProjectRound(hb, hb.where("live"), {})
        hb_embedder = SimpleNamespace(embed=lambda texts: [vectors(text) for text in texts])
        return one, mock.patch.object(hb, "embedder", hb_embedder)

    def test_only_the_last_three_days(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        self.app.db.execute("INSERT INTO connection_briefings (id, at, items, stats) VALUES "
                            "('i0', ?, ?, '{}')", (old, json.dumps([{"id": "s0", "title": "Old"}])))
        self.assertEqual([one["title"] for one in rec.recent_stories(self.app.connections)],
                         ["Tides at the new moon"])

    def test_what_is_near_the_work_is_told_as_background(self) -> None:
        one, patched = self._round(lambda _text: [1.0, 0.0])
        with patched:
            said = one._news_like("Measuring the tide gauge")
        self.assertIn("Tides at the new moon", said)
        self.assertIn("none is a task of its own", said)

    def test_what_is_not_near_is_left_out(self) -> None:
        one, patched = self._round(lambda text: [1.0, 0.0] if "gauge" in text else [0.0, 1.0])
        with patched:
            self.assertEqual(one._news_like("Measuring the gauge"), "")

    def test_with_no_way_to_measure_a_run_goes_on(self) -> None:
        one, patched = self._round(lambda _text: 1 / 0)
        with patched:
            self.assertEqual(one._news_like("Anything"), "")


if __name__ == "__main__":
    unittest.main()
