"""EVERY LESSON LEARNED FROM, ACROSS CONTEXTS (learning/from_lessons.py, reasons.py), found by every
decision (heartbeat/lessons/retrieve.py), with the moment it came at. Made-up answers."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.debugger import recommendation
from personalized_agent.heartbeat.lessons.moment import line_of, moment_of
from personalized_agent.learning import from_lessons as fl
from personalized_agent.learning import reasons as rs
from personalized_agent.learning.find import Finder
from personalized_agent.learning.reasons import Reasons
from personalized_agent.learning.store import LearningStore
from personalized_agent.learning.vocab import Vocabulary, term_id

SIGNALS = {"mode": "working", "depth": "focused", "application": "Editor", "title": "tides.py - Editor",
           "project": "Harbour survey", "task": "Tide tables", "hour": 14.5, "weekday": 3, "mood": "x",
           "since_break_minutes": 40.0}


class TheMomentTests(unittest.TestCase):
    def test_what_they_were_at_by_name(self) -> None:
        said = line_of(moment_of(SIGNALS, 2))
        for words in ("working (focused)", "in Editor", 'project "Harbour survey"', 'task "Tide tables"',
                      "Thursday 14:30", "40 min since a break", "2 recommended today"):
            self.assertIn(words, said)

    def test_nothing_read_says_nothing(self) -> None:
        self.assertEqual(moment_of({}, 3), {})
        self.assertEqual(line_of({}), "")

    def test_a_window_by_its_name_not_its_words(self) -> None:
        self.assertEqual(len(moment_of({"title": "x" * 500})["title"]), 120)
        self.assertNotIn("mood", moment_of(SIGNALS))


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.db = self.app.db
        self.store = LearningStore(self.db)
        self.app.monitor.latest = dict(SIGNALS)

    def _answered(self, action="bad_notification", title="The build should be done", said="",
                  kind="notifications", category="background job"):
        ident = self.app.lessons.record(kind, f"s{title}{action}",
                                        f'The agent said "{title}": words - the user answered '
                                        f'"{action}": what it means.', action=action, reason=said)
        self.db.execute("UPDATE lesson_evidence SET category = ? WHERE id = ?", (category, ident))
        return ident


class TheLessonKeepsItTests(_Base):
    def test_a_lesson_says_what_they_were_doing(self) -> None:
        ident = self._answered()
        row = self.db.query_one("SELECT summary, moment FROM lesson_evidence WHERE id = ?", (ident,))
        self.assertIn('project "Harbour survey"', row["summary"])
        self.assertEqual(json.loads(row["moment"])["project"], "Harbour survey")

    def test_from_another_time_it_says_nothing_of_now(self) -> None:
        ident = self.app.lessons.record("general", "s", "Something", at="2026-10-01T00:00:00+00:00")
        self.assertNotIn("At the time", self.db.query_one(
            "SELECT summary FROM lesson_evidence WHERE id = ?", (ident,))["summary"])

    def test_the_works_own_lessons_keep_none(self) -> None:
        ident = self.app.lessons.record("work", "s", "A stage")
        self.assertNotIn("At the time", self.db.query_one(
            "SELECT summary FROM lesson_evidence WHERE id = ?", (ident,))["summary"])

    def test_what_is_shown_keeps_the_moment_it_was_shown_at(self) -> None:
        said = self.app.notifications.notify("Title", "Words")
        self.assertEqual(said.metadata["moment"]["project"], "Harbour survey")

    def test_an_answer_says_the_moment_it_was_shown_at_not_now(self) -> None:
        shown = self.app.notifications.notify("Tide tables", "New ones", kind="proactive")
        self.app.monitor.latest = {"mode": "resting", "project": "Something else"}
        self.app.learn_answer(self.app.notifications.get(shown.id), "got_it")
        row = self.db.query_one("SELECT summary FROM lesson_evidence ORDER BY at DESC LIMIT 1")
        self.assertIn('project "Harbour survey"', row["summary"])


class TakenIntoTheLearningTests(_Base):
    def test_each_answer_is_a_judgment_of_its_own_case(self) -> None:
        self._answered(said="I can see it myself")
        self._answered("remind_later", "Talk to them")
        taken = fl.take(self.db)
        self.assertEqual(len(taken), 2)
        judgments = self.store.judgments("state = 'new' ORDER BY at")
        first, second = judgments
        self.assertEqual((first["aspect"], first["direction"], first["said"]),
                         ("attention", "not worth telling them", "I can see it myself"))
        self.assertEqual(second["aspect"], "timing")
        self.assertIn("At the time", first["why"], "the moment goes with it")
        self.assertNotIn("the user answered", first["why"], "what they did is the judgment")
        case = self.store.case(first["case_id"])
        self.assertEqual(case["unit_id"], fl.UNIT)
        self.assertEqual(case["facets"]["deliverable"], [term_id("deliverable", "notification")])
        self.assertEqual(first["case_id"], second["case_id"], "one kind of thing told")

    def test_taken_once(self) -> None:
        self._answered()
        fl.take(self.db)
        self.assertEqual(fl.take(self.db), [])

    def test_the_works_own_are_not_taken_one_by_one(self) -> None:
        self.app.lessons.record("work", "s", "A stage", action="stage")
        self.assertEqual(fl.take(self.db), [])

    def test_turned_off_nothing_is_taken(self) -> None:
        fl.take(self.db)
        unit = self.store.unit(fl.UNIT)
        unit["state"] = "inactive"
        self.store.save_unit(unit)
        self._answered()
        self.assertEqual(fl.take(self.db), [])

    def test_from_before_answers_were_kept_it_says_so(self) -> None:
        ident = self._answered()
        self.db.execute("UPDATE lesson_evidence SET at = '2026-10-24T17:40:00+00:00' WHERE id = ?",
                        (ident,))
        fl.take(self.db)
        self.assertIn("only deleting or calling it bad left a mark",
                      self.store.judgments("state = 'new'")[0]["why"])

    def test_before_the_answer_hides_it_and_keeps_the_moment(self) -> None:
        said = fl.before_the_answer({"summary": 'The agent said "T": w - the user answered "got_it": '
                                                'saw it. At the time: working.'})
        self.assertNotIn("got_it", said)
        self.assertIn("At the time: working.", said)


class _Asked:
    """A model that says what each step is told to say."""

    def __init__(self, **said):
        self.said, self.asked = said, []

    def __call__(self, _stage, system, text, _most, _what):
        self.asked.append((system, text))
        for name, prompt in (("predict", rs.PREDICT_PROMPT), ("across", rs.ACROSS_PROMPT),
                             ("abduce", rs.ABDUCE_PROMPT), ("explain", rs.EXPLAIN_PROMPT)):
            if system == prompt:
                answer = self.said.get(name, {})
                return answer(text) if callable(answer) else answer
        return {}


class TheReasonsTests(_Base):
    def _work_reason(self, statement="They trust what they can check", aspect="requirement",
                     source="inferred"):
        vocab = Vocabulary(self.db)
        vocab.ensure_fixed()
        self.store.save_unit({"id": "u1", "kind": "work", "key": "u1", "title": "Work", "state": "read"})
        case = self.store.save_case({"unit_id": "u1", "title": "A report", "facets": {
            "deliverable": [term_id("deliverable", "report")]}})
        judgment = self.store.save_judgment({
            "case_id": case, "aspect": aspect, "object": term_id("object", "correctness and checks"),
            "direction": "each number traced", "strength": "changed", "at": "2026-10-01",
            "state": "linked"})
        rid = self.store.save_reason({"statement": statement, "aspect": aspect, "source": source,
                                      "condition": {"text": "always"}, "objects": [], "predictions": [],
                                      "state": "active", "history": []})
        self.store.link(rid, judgment, "supports")
        return rid

    def _ids(self, text):
        return [line.split(" ")[0] for line in text.splitlines() if line.startswith("J-")]

    def test_a_reason_is_found_across_answers(self) -> None:
        self._answered(said="I can see it myself")
        self._answered(title="The tests should be done")

        def abduce(text):
            return {"reasons": [{"statement": "A guess at their own work adds nothing",
                                 "aspect": "attention", "condition": {"text": "their own runs"},
                                 "fails_when": "they take such a ping up", "predictions": [],
                                 "explains": self._ids(text.split("REASONS KEPT")[0])}]}
        Reasons(self.db, _Asked(abduce=abduce)).run(False)
        found = [one for one in self.store.reasons() if one["aspect"] == "attention"]
        self.assertEqual([one["statement"] for one in found], ["A guess at their own work adds nothing"])

    def test_guessed_before_it_is_learned_from_with_the_reasons_and_without(self) -> None:
        self._work_reason()
        ident = self._answered()
        asked = _Asked(predict=lambda text: {"answers": {ident: "negative" if "WHY THEY JUDGE" in text
                                                         else "neutral"}})
        Reasons(self.db, asked).run(False)
        went = fl.how_it_went(self.db)
        self.assertEqual((went["count"], went["with_reasons"], went["without"]), (1, 1, 0))
        shown = [text for system, text in asked.asked if system == rs.PREDICT_PROMPT]
        self.assertEqual(len(shown), 2)
        self.assertNotIn("the user answered", shown[0].split("TOLD THEM:")[1], "the answer stays hidden")
        self.assertIn("They trust what they can check", shown[0])
        self.assertNotIn("They trust what they can check", shown[1])

    def test_an_answer_of_no_group_is_not_guessed(self) -> None:
        self.app.lessons.record("general", "s", "Ticked a step", action="ticked_step")
        asked = _Asked()
        Reasons(self.db, asked).run(False)
        self.assertFalse([1 for system, _text in asked.asked if system == rs.PREDICT_PROMPT])

    def _answer_reason(self):
        ident = self._answered(said="not now")
        fl.take(self.db)
        judgment = self.store.judgments("state = 'new'")[0]
        rid = self.store.save_reason({"statement": "No interruption they did not choose",
                                      "aspect": "attention", "source": "inferred",
                                      "condition": {"text": "always"}, "objects": [], "predictions": [],
                                      "state": "active", "history": []})
        self.store.link(rid, judgment["id"], "supports")
        self.store.set_judgment_state(judgment["id"], "linked")
        return ident, rid

    def test_a_motive_stands_on_two_contexts_and_a_tension_is_kept(self) -> None:
        work = self._work_reason()
        _ident, answer = self._answer_reason()
        said = {"motives": [
            {"statement": "They want what they can check", "kind": "motive", "explains": [work, answer],
             "condition": {"text": "always"}, "fails_when": "they take a guess", "predictions": []},
            {"statement": "Upkeep taken off against no interruption", "kind": "tension",
             "explains": [work, answer], "condition": {"text": "always"},
             "fails_when": "either always wins", "predictions": []},
            {"statement": "Only the work", "kind": "motive", "explains": [work],
             "condition": {"text": "x"}, "fails_when": "y", "predictions": []}]}
        reasons = Reasons(self.db, _Asked(across=said))
        reasons.across()
        motives = [one["statement"] for one in self.store.reasons() if one["aspect"] == "motive"]
        self.assertEqual(sorted(motives), ["In tension: Upkeep taken off against no interruption",
                                           "They want what they can check"])

    def test_nothing_is_looked_for_from_the_work_alone(self) -> None:
        self._work_reason()
        asked = _Asked()
        Reasons(self.db, asked).across()
        self.assertEqual(asked.asked, [])

    def test_the_prompt_says_where_each_reason_comes_from_and_gives_the_principles(self) -> None:
        self._work_reason()
        self._answer_reason()
        self.db.execute("INSERT INTO experience_patterns (id, scope, layer, statement, members, support, "
                        "created_at, updated_at) VALUES ('p1', 'work', 'principle', 'They check things', "
                        "'[]', 3, 'x', 'x')")
        asked = _Asked(across={"motives": []})
        Reasons(self.db, asked).across()
        text = asked.asked[0][1]
        self.assertIn("[work]", text)
        self.assertIn("[notifications]", text)
        self.assertIn("They check things", text)

    def _an_answer_against_the_work(self, revision):
        """A reason of the work, an answer against it, and the model saying `revision`."""
        work = self._work_reason()
        reason = self.store.reason(work)
        reason["objects"] = [term_id("object", "interruptions")]
        self.store.save_reason(reason)
        self._answered(said="no")
        shown = {}

        def ask(_stage, system, text, _most, _what):
            if system == rs.EXPLAIN_PROMPT:
                return {"judgments": [{"id": one, "explained_by": [],
                                       "conflicts": [{"reason": work, "why": "against it"}]}
                                      for one in self._ids(text.split("NEW JUDGMENTS:")[1])]}
            if system == rs.VERIFY_PROMPT:
                return {"judgments": {one: "yes" for one in self._ids(text)}}
            if system.startswith("A REASON OF THE USER'S IS CONTRADICTED"):
                shown["system"], shown["text"] = system, text
                return revision
            return {}
        Reasons(self.db, ask).run(False)
        return work, shown

    REWRITTEN = {"choice": "c", "why": "they check what lasts, not what passes",
                 "rewrite": {"statement": "They trust what they can check, in what lasts",
                             "aspect": "requirement", "condition": {"text": "what is kept"},
                             "objects": [], "predictions": []}}

    def test_the_model_is_told_the_context_of_each_case(self) -> None:
        _work, shown = self._an_answer_against_the_work({"choice": "d", "why": "a one-off"})
        self.assertIn("context: their answer to what their agent told them", shown["text"])
        self.assertIn('context: their work, "Work"', shown["text"])
        self.assertIn("What they do in one context may say", shown["system"])

    def test_the_model_may_change_a_reason_of_the_work_on_an_answer(self) -> None:
        work, _shown = self._an_answer_against_the_work(self.REWRITTEN)
        reason = self.store.reason(work)
        self.assertEqual(reason["statement"], "They trust what they can check, in what lasts")
        self.assertEqual(reason["history"][-1]["on_answers"], ["notifications: background job"])

    def test_or_leave_it_as_it_is(self) -> None:
        work, _shown = self._an_answer_against_the_work({"choice": "d", "why": "a one-off"})
        reason = self.store.reason(work)
        self.assertEqual(reason["statement"], "They trust what they can check")
        self.assertEqual(reason["history"], [])

    def test_a_change_made_on_an_answer_can_be_read_in_the_debugger(self) -> None:
        self._an_answer_against_the_work(self.REWRITTEN)
        changed = recommendation._learned(self.app)["changed"]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["was"], "They trust what they can check")
        self.assertEqual(changed[0]["now"], "They trust what they can check, in what lasts")
        self.assertEqual(changed[0]["answers"], ["notifications: background job"])

    def test_a_part_of_the_debugger_that_fails_leaves_the_others(self) -> None:
        from unittest import mock
        with mock.patch.object(recommendation, "_learned", side_effect=ImportError("old code")):
            said = recommendation.report(self.app)
        self.assertEqual(said["learned"], {"error": "old code"})
        self.assertIn("switches", said)

    def test_a_change_made_on_the_work_is_not_listed_there(self) -> None:
        work = self._work_reason()
        reason = self.store.reason(work)
        news = self.store.judgments("state = 'linked'")
        self.assertEqual(Reasons(self.db, _Asked())._on_answers(reason, news), [])

    def test_answers_count_towards_a_reason_of_the_work_as_any_evidence(self) -> None:
        work = self._work_reason()
        reason = self.store.reason(work)
        reason["state"] = "candidate"
        self.store.save_reason(reason)
        for one in range(4):
            self._answered(title=f"Reminder {one}", category=f"class {one}")
        fl.take(self.db)
        for judgment in self.store.judgments("state = 'new'"):
            self.store.link(work, judgment["id"], "supports")
        said = Reasons(self.db, _Asked()).rescore(work)
        self.assertEqual(said["state"], "active", "whether they bear on it was the model's to say")
        self.assertGreater(said["support"], 2.0)

    def test_a_reason_of_the_answers_is_put_in_force_by_them(self) -> None:
        self._answered(title="One", category="class one")
        self._answered(title="Two", category="class two")
        fl.take(self.db)
        rid = self.store.save_reason({"statement": "A reason", "aspect": "attention",
                                      "source": "inferred", "condition": {}, "objects": [],
                                      "predictions": [], "state": "candidate", "history": []})
        for judgment in self.store.judgments("state = 'new'"):
            self.store.link(rid, judgment["id"], "supports")
        self.assertEqual(Reasons(self.db, _Asked()).rescore(rid)["state"], "active")

    def _reason_of_answers(self, statement="Not in the evening", condition=None):
        self._answered("remind_later", "Write to a colleague", said="not at this hour",
                       category="social follow-up")
        fl.take(self.db)
        judgment = self.store.judgments("state = 'new'")[0]
        rid = self.store.save_reason({"statement": statement, "aspect": "timing", "source": "stated",
                                      "condition": condition or {"text": "in the evening"},
                                      "objects": [], "predictions": [], "state": "active",
                                      "history": []})
        self.store.link(rid, judgment["id"], "supports")
        return rid

    def test_a_reason_says_which_answers_it_came_of(self) -> None:
        rid = self._reason_of_answers()
        said = fl.learned_from(self.store, rid)
        self.assertIn("how they answered their agent", said)
        self.assertIn("Write to a colleague", said)
        self.assertIn("wanted later", said)
        self.assertIn("not at this hour", said)
        self.assertIn("notifications: social follow-up", said)

    def test_a_reason_of_the_work_says_nothing_of_the_kind(self) -> None:
        self.assertEqual(fl.learned_from(self.store, self._work_reason()), "")

    def test_a_reason_of_both_says_how_much_is_answers(self) -> None:
        work = self._work_reason()
        self._answered()
        fl.take(self.db)
        self.store.link(work, self.store.judgments("state = 'new'")[0]["id"], "supports")
        self.assertIn("1 of 2", fl.learned_from(self.store, work))

    def test_a_work_run_is_given_it_with_where_it_came_from_and_judges(self) -> None:
        rid = self._reason_of_answers()
        finder = Finder(self.db)
        candidates = finder.candidates({})
        self.assertIn(rid, [one[0]["id"] for one in candidates], "no filter: it is for the model")
        asked = []

        def ask(prompt, text, _most):
            asked.append((prompt, text))
            return {"reasons": {rid: {"holds": "no", "why": "about reminders"}}}
        said = finder.holds("Write a report", {}, candidates, ask)
        self.assertIn("Write to a colleague", asked[0][1])
        self.assertIn("weigh what it meant there", asked[0][0])
        self.assertEqual(said[rid]["holds"], "no", "the model's to say")


class FoundByEveryDecisionTests(_Base):
    def test_a_reason_comes_with_the_lessons_said_to_be_one(self) -> None:
        reasons = TheReasonsTests._work_reason
        rid = reasons(self, "Harbour tables must be checked before anything is told", source="stated")
        retriever = self.app.calendar_nature.retriever
        retriever._reasons_at = 0.0
        said = retriever.text(("notifications", "recommendations", "general"), "harbour tables")
        self.assertIn("Harbour tables must be checked", said)
        self.assertIn("from why they judge so", said)
        self.assertIn("1 case", said)
        self.assertIn(rid, said)

    def test_a_reason_of_answers_comes_with_them(self) -> None:
        rid = TheReasonsTests._reason_of_answers(self, "Harbour tables wait for the morning")
        retriever = self.app.calendar_nature.retriever
        retriever._reasons_at = 0.0
        said = next(one for one in retriever._learned_reasons() if one["id"] == rid)
        self.assertIn("Write to a colleague", said["statement"])

    def test_turned_off_it_is_not_given(self) -> None:
        reasons = TheReasonsTests._work_reason
        reasons(self, "Harbour tables must be checked before anything is told", source="stated")
        unit = self.store.unit("u1")
        unit["state"] = "inactive"
        self.store.save_unit(unit)
        retriever = self.app.calendar_nature.retriever
        retriever._reasons_at = 0.0
        self.assertEqual(retriever._learned_reasons(), [])

    def test_recommendations_are_a_type_of_their_own_to_every_decision(self) -> None:
        from personalized_agent.heartbeat.lessons import retrieve
        self.assertIn("recommendations", retrieve.TYPES)


if __name__ == "__main__":
    unittest.main()
