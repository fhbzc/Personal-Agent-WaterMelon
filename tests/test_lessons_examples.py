"""THE LESSONS KEPT AS LEARNING KEEPS ITS WORK (the user, 2026-11-03): examples
marked with their class, their exact content kept, possible rules above them;
every answer to a notification or a question learned from; one way of finding
them when a notification is set up; and lessons from the chat in the same
form. Everything here is made up."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.heartbeat.lessons import engine as lessons_engine
from personalized_agent.heartbeat.lessons.retrieve import LessonRetriever, words_of


class _Model:
    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []

    def invoke(self, request, role="primary"):
        self.asked.append(request)
        answer = self.answers.pop(0) if self.answers else {}
        return SimpleNamespace(text=answer if isinstance(answer, str) else json.dumps(answer),
                               tool_calls=[], usage=None)


class Base(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.engine = self.app.lessons
        self.retriever = self.app.lesson_retriever
        self.retriever.embedder = None           # by words here: no model to load

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _examples(self, kind="notifications"):
        return [dict(row) for row in self.app.db.query(
            "SELECT * FROM lesson_evidence WHERE kind = ? ORDER BY at, rowid", (kind,))]

    def _notice(self, title="Water the tomatoes", **more):
        return self.app.notifications.notify(title, "Before the sun is high.", kind="proactive",
                                             metadata=more)


class EveryAnswerTests(Base):

    def test_each_of_a_notifications_own_buttons(self) -> None:
        self.app.remind_later(self._notice("Later one").id)
        self.app.notification_to_todo(self._notice("To do one").id)
        self.app.notification_got_it(self._notice("Seen one").id)
        said = {one["action"]: one for one in self._examples()}
        self.assertEqual(set(said), {"remind_later", "to_todo", "got_it"})
        self.assertIn("the moment was wrong", said["remind_later"]["summary"])
        self.assertLess(said["got_it"]["weight"], said["to_todo"]["weight"], "a weak sign")
        content = json.loads(said["to_todo"]["content"])
        self.assertEqual(content["notification"]["title"], "To do one", "kept exactly")

    def test_a_question_answered(self) -> None:
        from personalized_agent.agent.feedback.store import TaskQuestion
        asked = self.app.questions.ask(TaskQuestion(
            task_id="", kind="suggestion", question="Take a break?", task_detail="Two hours in.",
            metadata={"options": [{"key": "decline", "label": "Not now", "effect": "complete"}]}))
        self.app.feedback.answer(asked.id, "decline", "not during the review")
        one = self._examples()[-1]
        self.assertEqual((one["action"], one["reason"]), ("decline", "not during the review"))
        self.assertIn("Take a break?", one["summary"])

    def test_a_bad_notification_keeps_why_it_was_made(self) -> None:
        shown = self._notice(monitor={"why": "a stretch of two hours"})
        self.app.bad_notification(shown.id, "never in a meeting")
        one = self._examples()[-1]
        self.assertEqual(one["reason"], "never in a meeting")
        self.assertIn("a stretch of two hours", one["content"])


class TheCycleTests(Base):

    def test_marked_with_its_class_in_english_and_a_possible_rule_from_two(self) -> None:
        for n in range(2):
            self.engine.record("notifications", f"s{n}", f"Reminder {n} said during a meeting",
                               action="remind_later", content=f"exact {n}")
        rows = self._examples()
        features = {row["id"]: {"source": "calendar", "event_type": "reminder", "keywords": ["meeting"],
                                "category": "reminder during a meeting",
                                "english": "A reminder came during a meeting and was put off."}
                    for row in rows}
        self.engine.models = _Model(
            {"features": features},
            {"rule": {"statement": "When the user is in a meeting, do not remind them.",
                      "conditions": {}}, "explains": [row["id"] for row in rows]})
        self.engine.learn("notifications")
        kept = self._examples()
        self.assertEqual(kept[0]["category"], "reminder during a meeting")
        self.assertIn("put off", kept[0]["english"])
        self.assertEqual(kept[0]["content"], "exact 0", "the exact content stays")
        rules = self.engine.rules("notifications", ("candidate",))
        self.assertEqual([one["statement"] for one in rules],
                         ["When the user is in a meeting, do not remind them."])

    def test_made_again_once_what_the_user_decided_stays(self) -> None:
        kept = self.engine._new_rule("notifications", {"statement": "Pinned rule"}, "x")
        self.app.db.execute("UPDATE lesson_rules SET pinned = 1 WHERE id = ?", (kept,))
        self.engine._new_rule("notifications", {"statement": "Written in the old way"}, "x")
        one = self.engine.record("notifications", "s", "Something", action="delete")
        self.app.db.execute("UPDATE lesson_evidence SET features = '{}', processed_at = 'x' "
                            "WHERE id = ?", (one,))
        self.app.db.execute("DELETE FROM kv WHERE key = 'lessons.rebuilt.examples'")
        self.assertEqual(self.engine.rebuild_once(), 1)
        self.assertEqual([r["statement"] for r in self.engine.rules("notifications")], ["Pinned rule"])
        self.assertIsNone(self._examples()[-1]["processed_at"], "read afresh")
        self.assertEqual(self.engine.rebuild_once(), 0, "once")


class OneWayOfFindingThemTests(Base):

    def _many(self):
        for n in range(10):
            self.engine._new_rule("notifications", {"statement": f"Tea house rule on day {n}"}, "x")
        self.engine._new_rule("notifications",
                              {"statement": "Never recommend papers while reading a paper"}, "x")
        for n in range(6):
            self.engine.record("notifications", f"t{n}", f"Tea house note {n}", action="got_it")
        self.engine.record("notifications", "p", "A paper was recommended while reading a paper",
                           action="bad_notification", reason="not while I read")

    def test_the_like_ones_rules_and_examples(self) -> None:
        self._many()
        said = self.retriever.text(("notifications",), "reading a paper about soil", rules=2, examples=1)
        self.assertIn("Never recommend papers", said)
        self.assertIn("not while I read", said, "what the user did, in their words")
        self.assertNotIn("day 9", said.split("WHAT THE USER")[0].split("\n")[0])

    def test_said_again_in_english_only_when_there_is_a_choice(self) -> None:
        model = _Model("The user reads a paper.")
        self.retriever.models = model
        self.retriever.text(("notifications",), "正在读论文", rules=8, rewrite_task="monitor_lessons_query")
        self.assertEqual(model.asked, [], "nothing to choose from: not asked")
        self._many()
        self.retriever.text(("notifications",), "正在读论文", rules=2, rewrite_task="monitor_lessons_query")
        self.retriever.text(("notifications",), "正在读论文", rules=2, rewrite_task="monitor_lessons_query")
        self.assertEqual(len(model.asked), 1, "the same moment: remembered")
        self.assertEqual(model.asked[0].task, "monitor_lessons_query")

    def test_every_decision_goes_this_way(self) -> None:
        self.engine._new_rule("notifications", {"statement": "Say it in the morning"}, "x")
        self.engine._new_rule("connections", {"statement": "Keep colleagues apart"}, "x")
        self.engine._new_rule("general", {"statement": "Be brief"}, "x")
        # BY WHAT IT IS ABOUT, NO QUOTA (the user, 2026-11-07): a moment like the lessons finds
        # them; "anything" is like none of them, and is given none.
        notices = self.app.calendar_nature.relevant_text("say it in the morning, be brief")
        self.assertIn("Say it in the morning", notices)
        self.assertIn("Be brief", notices, "general lessons bear on every decision")
        people = self.app.connections.lessons_text("keep colleagues apart, be brief")
        self.assertIn("Keep colleagues apart", people)
        self.assertIn("Be brief", people)
        self.assertNotIn("Say it in the morning",
                         self.app.calendar_nature.relevant_text("anything"))

    def test_every_type_is_looked_at_its_own_first(self) -> None:
        # The user, 2026-11-04: what was learned elsewhere bears on it too.
        for n in range(6):
            self.engine._new_rule("notifications", {"statement": f"Morning rule {n} about tea"}, "x")
        self.engine._new_rule("connections",
                              {"statement": "A thesis advisor's messages about the thesis matter"}, "x")
        self.app.db.execute(
            "INSERT INTO lesson_cards (id, lineage, status, question, answer, created_at, updated_at) "
            "VALUES ('card_1', 'card_1', 'kept', 'When is the thesis draft sent?', "
            "'Only after the thesis figures are checked', 'x', 'x')")
        found = self.retriever.retrieve(("notifications", "general"), "about the thesis draft", rules=4)
        kinds = [one.get("kind") for one in found["rules"]]
        # NO HALF FOR ITS OWN (the user, 2026-11-07): the tea rules are nothing like the thesis.
        self.assertEqual(kinds.count("notifications"), 0, "its own only when like it")
        self.assertIn("connections", kinds)
        self.assertIn("work card", kinds)
        said = self.retriever.text(("notifications", "general"), "about the thesis draft", rules=4)
        self.assertIn("from connections: A thesis advisor", said, "said to be from where it is")
        self.assertIn("from work card:", said)
        self.assertNotIn("from notifications", said)

    def test_what_is_nothing_like_it_is_left_out(self) -> None:
        for n in range(6):
            self.engine._new_rule("notifications", {"statement": f"Morning rule {n} about tea"}, "x")
        self.engine._new_rule("connections", {"statement": "Keep colleagues apart"}, "x")
        found = self.retriever.retrieve(("notifications",), "tea in the morning", rules=4)
        self.assertNotIn("connections", [one.get("kind") for one in found["rules"]])

    def test_words_in_any_language(self) -> None:
        self.assertTrue({"读论", "论文"} <= words_of("读论文"))


class FromTheChatTests(Base):

    def test_what_the_user_says_kept_like_any_other_lesson(self) -> None:
        tools = self.app.agent.tools
        self.assertIn("error", tools.call("lesson_add", {"user_said": " ", "about": "general"}))
        said = tools.call("lesson_add", {
            "user_said": "以后开会时不要提醒我", "about": "notifications",
            "situation": "They were talking about a reminder during a meeting",
            "rule": "When the user is in a meeting, do not remind them."})
        one = self._examples()[-1]
        self.assertEqual((one["action"], one["reason"]), ("told_in_chat", "以后开会时不要提醒我"))
        rule = self.engine.rule(said["rule"])
        self.assertEqual(rule["state"], "active", "said by the user: in force at once")
        self.assertIn("in a meeting", tools.call("lesson_find", {"moment": "a meeting"})["lessons"])


if __name__ == "__main__":
    unittest.main()
