"""LESSON CARDS ARE NO LONGER A RUN'S (the learning started again,
2026-10-21 - learning/): the reviewer asks its questions of the user in an
answer of its own, with no tools at all; the full way is then given what was
learned of the user's work for them (learning/find.py), the way with no
context nothing - it answers them itself. The questions and their answers
are logged with the run (work_run_questions), never kept as a card; a card
kept from before is shown to no reviewer, cited by none, and no use of it is
recorded. No model is called; the work is made up."""

from __future__ import annotations

import json
import unittest

from support import TemporaryAgent
from test_experience_debugger import _DebuggerBase, _Models
from test_work_runs import _TwoAgents

from personalized_agent.heartbeat.experience.lesson_cards import LessonCards
from personalized_agent.learning.store import LearningStore

QUESTION = "How long should the notes be?"
OLD_QUESTION = "Which notes does the user keep?"


def _reviewer(request):
    return "You review a work agent" in (request.system or "")


def _as_asked(request):
    """What a call was asked, as it was asked: its last message and its
    tools - the messages go on growing after it."""
    last = request.messages[-1]["content"]
    return (last if isinstance(last, str) else json.dumps(last),
            {one.name for one in request.tools or []})


def _contract_review(request):
    """THE QUESTIONS STEP of the contract: the job's terms and one question.
    Then the contract approved, the question answered by its number. None
    for anything else."""
    if not _reviewer(request):
        return None
    said, _tools = _as_asked(request)
    if "BEFORE YOU WRITE THE CRITERIA OF THIS CONTRACT" in said:
        return json.dumps({"terms": {}, "questions": [{"q": QUESTION, "kind": "output"}]})
    if "REVIEW THIS CONTRACT" not in said:
        return None
    return json.dumps({
        "verdict": "approve", "comments": [],
        "deliverables": [{"ref": "d1", "criteria": [
            {"text": "notes.md says hello", "because": ["own judgement"],
             "backed_by": "check"}], "approach": "write it plainly"}],
        "answers": [{"n": 1, "answer": "One line.", "tier": "assumption",
                     "grounds": ["own judgement"], "searched": []}]})


class _Asking(_TwoAgents):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.seen = []

    def invoke(self, request, role=""):
        if _reviewer(request):
            self.seen.append(_as_asked(request))
        answer = _contract_review(request)
        if answer is not None:
            self.asked.append(request)
            return self._reply(answer)
        return super().invoke(request, role)


class _DebugAsking(_Models):

    def invoke(self, request, role=""):
        answer = _contract_review(request)
        if answer is not None:
            self.asked.append(request)
            return self._reply(answer)
        return super().invoke(request, role)


def _everything_said(request):
    return (request.system or "") + json.dumps(request.messages, default=str)


class TheUsersOwnWorkKeepsNoCardTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.unit = {"id": "task-notes1", "name": "Write notes", "kind": "task", "why": ""}
        self.cards = LessonCards(self.app.db)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _run(self, models):
        import time
        self.app.work.models = models
        started = self.app.work.start(self.unit, "write the notes")
        self.assertNotIn("error", started, started)
        for _ in range(600):
            runs = self.app.work.runs(self.unit["id"])
            if runs and not runs[0]["alive"] and runs[0]["status"] != "running":
                break
            time.sleep(0.1)
        run = self.app.work.runs(self.unit["id"])[0]
        self.assertEqual(run["status"], "done", run["log"][-6:])
        return run

    def test_asked_with_no_tools_answered_logged_and_no_card(self) -> None:
        old = self.cards.write(OLD_QUESTION, "Short plain notes.",
                               [{"type": "user_code", "ref": "notes/today.md"}],
                               context={"project": "elsewhere"})["card"]
        models = _Asking()
        run = self._run(models)
        # Asked in an answer of its own, with no tools at all.
        step_one = [tools for said, tools in models.seen
                    if "BEFORE YOU WRITE THE CRITERIA OF THIS CONTRACT" in said]
        self.assertTrue(step_one)
        self.assertEqual(step_one[0], set())
        # Its question put before the contract's review, with what was
        # learned for it and how it is to be answered - the web there too.
        review = [(said, tools) for said, tools in models.seen if "REVIEW THIS CONTRACT" in said]
        self.assertTrue(review)
        self.assertIn(QUESTION, review[0][0])
        self.assertIn("ANSWER YOUR QUESTIONS AS YOU WRITE", review[0][0])
        self.assertIn("web_search", review[0][1])
        # Logged with the run, the answer beside it - never a card.
        asked = LearningStore(self.app.db).questions(run["id"])
        self.assertEqual([(one["question"], one["kind"], one["asked_by"]) for one in asked],
                         [(QUESTION, "output", "reviewer")])
        self.assertEqual((asked[0]["answer"].get("answer"), asked[0]["answer"].get("tier")),
                         ("One line.", "assumption"))
        self.assertEqual([one["id"] for one in self.cards.cards()], [old["id"]])
        # The card from before: shown to no reviewer, no use of it recorded.
        for request in models.asked:
            said = _everything_said(request)
            self.assertNotIn(old["id"], said)
            self.assertNotIn(OLD_QUESTION, said)
            if _reviewer(request):
                self.assertNotIn("LESSON CARD", said)
        self.assertEqual(self.cards.uses(old["id"]), [])

    def test_two_runs_still_no_card(self) -> None:
        self._run(_Asking())
        self._run(_Asking())
        self.assertEqual(self.cards.cards(), [])


class ADebuggerWayIsGivenNoCardTests(_DebuggerBase):

    def test_the_full_way_is_given_what_was_learned_and_b_answers_itself(self) -> None:
        cards = LessonCards(self.app.db)
        mine = cards.write(OLD_QUESTION, "Short plain notes.",
                           [{"type": "user_code", "ref": "notes/today.md"}],
                           context={"project": "elsewhere"})["card"]
        project = self._project()
        for variant in ("full", "noctx"):
            models = _DebugAsking()
            self.app.work.models = models
            self._start(project, variant)
            run = self._finished(project, variant)
            self.assertEqual(run["status"], "done", run["log"][-6:])
            reviewer = [r for r in models.asked if _reviewer(r)]
            self.assertTrue(reviewer)
            for request in reviewer:
                said = _everything_said(request)
                self.assertNotIn(mine["id"], said, variant)
                self.assertNotIn("LESSON CARD", said, variant)
                self.assertNotIn("replaces", said, variant)
            reviews = [_as_asked(r)[0] for r in reviewer
                       if "REVIEW THIS CONTRACT" in _as_asked(r)[0]]
            self.assertTrue(reviews, variant)
            if variant == "full":
                self.assertIn("ANSWER YOUR QUESTIONS AS YOU WRITE", reviews[0])
            else:
                self.assertIn("NO RECORD OF THE USER'S WORK is given for your questions",
                              reviews[0])
                self.assertNotIn("ANSWER YOUR QUESTIONS AS YOU WRITE", reviews[0])
            # Logged with its run; nothing kept, no use recorded.
            asked = LearningStore(self.app.db).questions(run["id"])
            self.assertEqual([one["question"] for one in asked], [QUESTION], variant)
            if variant == "noctx":
                self.assertEqual(asked[0]["found"], {"given": "nothing"})
            self.assertEqual([one["id"] for one in cards.cards()], [mine["id"]])
            self.assertEqual(cards.uses(mine["id"]), [])


if __name__ == "__main__":
    unittest.main()
