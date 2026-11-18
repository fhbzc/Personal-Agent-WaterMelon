"""LEARNING QUESTIONS (heartbeat/lessons/questions.py; the user, 2026-11-17: "加一个monitor, 是learning question, 也就是agent
在遇到相关的learning 不确定的时候，也可以主动ask, 比如有一些conflict rule, learning的question 要求必须给option (让用户可以点，
同时有一个chat bar让用户回复)"). Everything here is made up."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from support import TemporaryAgent

from personalized_agent.agent.information_queue import InformationQueue
from personalized_agent.api.routes import build_routes
from personalized_agent.heartbeat.lessons import questions as lq


def _asked(**said):
    return SimpleNamespace(get=lambda name, default=None: said.get(name, default), body=said, query={},
                           require=lambda name: said[name])


class LearningQuestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.lessons = self.app.lessons
        self.unsure = self.rule("When a bean article comes in the evening, tell it at once.", 2.6, 2.2)
        self.mixed = self.rule("Tell garden news after lunch.", 3.0, 1.2)        # one side ahead: not asked
        self.sure = self.rule("Never tell seed club news on weekends.", 4.0, 0.0)
        self.question = {"kind": "notifications", "rule_ids": [self.unsure], "matters": 0.9,
                       "question": "Bean articles in the evening: now, or the next morning?",
                       "why": "You opened some at once and put others off.",
                       "options": [{"label": "Tell me at once", "rule": "When a bean article comes in the evening, "
                                                                      "tell it at once."},
                                   {"label": "Next morning", "rule": "When a bean article comes in the evening, "
                                                                     "tell it the next morning."}]}
        self.answer = {"questions": [self.question]}
        self.calls = []
        models = SimpleNamespace(invoke=lambda request, role=None: self.calls.append((request, role)))
        self.asking = lq.LearningQuestions(
            self.lessons, models, self.app.notifications, self.app.db, self.app.config,
            tell=lambda t, m, **k: self.app.monitor.relay("learning_question", t, m, **k))

    def rule(self, statement: str, support: float, against: float) -> str:
        made = self.lessons._new_rule("notifications", {"statement": statement}, "test", state="active")
        self.app.db.execute("UPDATE lesson_rules SET support = ?, contradict = ? WHERE id = ?",
                            (support, against, made))
        return made

    def look(self, said=None, now=None):
        with mock.patch("personalized_agent.models.answers.read_text",
                        return_value=(json.dumps(said or self.answer), False)):
            return self.asking.look(now)

    def test_asked_about_what_it_is_unsure_of_with_options(self) -> None:
        self.assertEqual([one["id"] for one in self.asking.unsure()], [self.unsure], "near even only")
        [made] = self.look()
        request, role = self.calls[0]
        self.assertEqual((request.task, role), (lq.TASK, "primary"))
        self.assertIn(self.unsure, request.messages[0]["content"])
        meta = self.app.notifications.get(made)["metadata"]
        self.assertEqual([one["label"] for one in meta["learning_question"]["options"]],
                         ["Tell me at once", "Next morning"])
        self.assertTrue(InformationQueue.own(meta), "its moment judged, never dropped")
        # One at a time; a while apart; a rule asked about once.
        self.assertIsNone(self.look(), "too soon: a round every two hours")
        later = datetime.now(timezone.utc) + timedelta(hours=lq.ASK_AT_MOST_EVERY_HOURS + 1)
        self.assertIsNone(self.look(now=later), "asked about already: nothing unsure is left")

    def test_asked_only_when_truly_undecided_and_it_matters(self) -> None:
        """2026-11-17: "只有在确实已有东西分不出来的情况下才问，且这个learning rule毕竟重要的情况下才问，让agent尽量少问"."""
        self.assertNotIn(self.mixed, [one["id"] for one in self.asking.unsure()], "one side ahead: settled enough")
        self.assertIsNone(self.look({"questions": [dict(self.question, matters=0.4)]}), "a small matter: not asked")
        self.assertIn("ASK AS LITTLE AS YOU CAN", lq.SYSTEM)

    def test_options_are_required(self) -> None:
        self.assertIsNone(self.look({"questions": [dict(self.question, options=self.question["options"][:1])]}),
                          "one option: not asked")
        self.assertIsNone(self.look({"questions": []}))

    def test_several_at_once_each_its_own_message(self) -> None:
        """2026-11-17: "每2小时问一次就行了（可以一次问多个问题)"."""
        other = dict(self.question, question="Seed club news: on weekends too?")
        made = self.look({"questions": [self.question, other]})
        self.assertEqual(len(made), 2)
        self.assertEqual(lq.ASK_AT_MOST_EVERY_HOURS, 2.0)

    def test_an_option_pressed_is_the_users_own_rule(self) -> None:
        [made] = self.look()
        said = build_routes(self.app)[("POST", "/api/learning/answer")](_asked(id=made, option=1))
        self.assertEqual(said["answered"], "Next morning")
        rule = self.lessons.rule(said["rule"])
        self.assertEqual((rule["state"], rule["statement"]),
                         ("active", "When a bean article comes in the evening, tell it the next morning."))
        self.assertTrue(self.lessons._the_users(rule), "the user's own: never rewritten")
        self.assertEqual(self.lessons.rule(self.unsure)["state"], "rejected", "what it was about, let go of")
        self.assertEqual(self.lessons.rule(self.sure)["state"], "active")
        self.assertTrue(self.app.notifications.get(made)["read"])
        self.assertIn("answered", self.asking.answer(made, 0), "answered once")
        row = [one for part in self.app._waiting_sections(side=True) for one in part["rows"] if one["id"] == made]
        self.assertFalse(row and row[0]["learning"]["options"] and not row[0]["learning"]["answered"])

    def test_a_question_naming_no_lesson_lets_none_go(self) -> None:
        """2026-11-17: an answer once let go of every lesson of its round - only those a question names, now."""
        [made] = self.look({"questions": [dict(self.question, rule_ids=["not-one-of-them"])]})
        self.asking.answer(made, 0)
        self.assertEqual(self.lessons.rule(self.unsure)["state"], "active", "not named: kept")
        self.assertNotIn(self.unsure, self.asking._kept().get("rules") or [], "not asked about: may be asked later")

    def test_answered_in_words_from_the_chat_bar(self) -> None:
        [made] = self.look()
        with mock.patch.object(self.lessons, "record", wraps=self.lessons.record) as kept:
            self.assertTrue(self.app.notification_chat(made, "only the long ones, the short ones next morning"))
        actions = [one.kwargs.get("action") for one in kept.call_args_list]
        self.assertIn("learning_answer", actions)
        said = [one for one in kept.call_args_list if one.kwargs.get("action") == "learning_answer"][0]
        self.assertIn("only the long ones", said.kwargs["reason"])

    def test_shown_with_its_options_in_the_side_panel(self) -> None:
        [made] = self.look()
        [row] = [one for part in self.app._waiting_sections(side=True) for one in part["rows"] if one["id"] == made]
        self.assertEqual(row["learning"], {"options": ["Tell me at once", "Next morning"], "answered": ""})
        self.assertTrue(any(one.get("writes") for one in row["actions"]), "an answer in words, by the chat's box")


if __name__ == "__main__":
    unittest.main()
