"""THE REVIEWER IS TOLD A THING ONCE in one context (2026-10-24, from a real
run's calls): the draft its questions were asked of is not given again with
its review, the word list goes once the terms are written, a reason or a case
of what was learned already in its context is named and not given again,
how its questions are answered is said once, a pointer to the cases once, and
the user's description not when it is their instruction word for word. No
model is called; the work is made up."""

from __future__ import annotations

import json
import unittest

from support import TemporaryAgent
from test_learning import _Base
from test_lesson_cards_in_runs import _Asking, _reviewer

from personalized_agent.agent.work.runner import ANSWER_RULES, Review, Run, WORDS_USED
from personalized_agent.learning.find import Finder


class _Run:
    experience_given = Run.experience_given
    keep_experience_given = Run.keep_experience_given
    learned_given = Run.learned_given

    def __init__(self, app):
        self.owner = app.work
        self.id = "run_one"
        self._facets = {}


class AContextHoldsWhatItWasToldTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_once_forget_and_what_a_kept_package_brings(self) -> None:
        run = _Run(self.app)
        run.keep_experience_given("contract", {
            "text": "YOUR QUESTIONS: ...\n\nTHE PACKAGE\n\n" + ANSWER_RULES,
            "found": {"reasons": ["R-1"], "cases": ["C-1"], "index": ["C-2"]}})
        review = Review(run, {"text": "a card"}, "the work", same_as="criteria:card_1")
        # The contract's package came with it: its reasons, cases and rules.
        self.assertTrue(review.attached)
        self.assertEqual(review.given, {"reasons": {"R-1"}, "cases": {"C-1"}, "index": {"C-2"}})
        self.assertTrue(review.rules_given)
        self.assertEqual(review.once("THE DRAFT", "(above)"), "THE DRAFT")
        self.assertEqual(review.once("THE DRAFT", "(above)"), "(above)")
        self.assertEqual(review.once("A NEW DRAFT", "(above)"), "A NEW DRAFT")
        review.forget("the work", "the job")
        self.assertTrue(review.messages[0]["content"].startswith("the job"))


class _Seeing(_Asking):
    """Keeps all a contract review was told, as it was when asked."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.reviews = []

    def invoke(self, request, role=""):
        if _reviewer(request):
            last = request.messages[-1]["content"]
            if "REVIEW THIS CONTRACT" in (last if isinstance(last, str) else ""):
                self.reviews.append("\n".join(str(one["content"]) for one in request.messages))
        return super().invoke(request, role)


class TheContractsReviewTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_draft_once_and_no_word_list(self) -> None:
        import time
        unit = {"id": "task-notes1", "name": "Write notes", "kind": "task", "why": ""}
        models = _Seeing()
        self.app.work.models = models
        self.assertNotIn("error", self.app.work.start(unit, "write the notes"))
        for _ in range(600):
            runs = self.app.work.runs(unit["id"])
            if runs and not runs[0]["alive"] and runs[0]["status"] != "running":
                break
            time.sleep(0.1)
        self.assertTrue(models.reviews)
        told = models.reviews[0]
        self.assertEqual(told.count('"goal": '), 1, "the draft, once")
        self.assertIn("REVIEW THIS CONTRACT, drafted by the work agent before any work:\n"
                      "(as shown above, unchanged)", told)
        self.assertIn(WORDS_USED, told)
        self.assertNotIn("  - deliverable:", told, "the word list, gone")


class TheSecondPackageTests(_Base):

    def _reason(self, statement, supports):
        rid = self.store.save_reason({
            "statement": statement, "source": "stated", "aspect": "output", "state": "active",
            "condition": {"dims": {"deliverable": ["deliverable:visual material"]},
                          "text": "visual material"},
            "objects": ["object:amount of text"], "predictions": [], "history": [],
            "support": float(len(supports))})
        for jid in supports:
            self.store.link(rid, jid, "supports")
        return rid

    def test_what_its_context_holds_is_named_and_the_pointer_said_once(self) -> None:
        slides = self.case("Defence slides", deliverable=["deliverable:slides"])
        rid = self._reason("Listen, not read.", [self.judgment(slides)])
        questions = [{"q": "How much text a slide?", "kind": "output"},
                     {"q": "Which tool are the slides made in?", "kind": "fact"},
                     {"q": "Where are the slides kept?", "kind": "habit"}]
        verdicts = {"reasons": {rid: {"holds": "yes", "answers": {"1": 3}}}}
        finder = Finder(self.db)
        first = finder.answer("slides", {"deliverable": ["deliverable:slides"]}, questions,
                              ask=lambda system, content, most: verdicts)
        self.assertEqual(first["text"], first["whole"])
        self.assertEqual(first["text"].count("No reason learned answers this outright"), 2)
        self.assertEqual(first["text"].count("what each looks like and how it was made"), 1,
                         "the pointer in full once")
        self.assertIn("see under QUESTION 2", first["text"])
        self.assertIn("Listen, not read.", first["text"])
        self.assertIn("Reasons are why the user judges", first["text"])
        again = finder.answer("slides", {"deliverable": ["deliverable:slides"]}, questions,
                              ask=lambda system, content, most: verdicts, had=first["found"])
        self.assertIn(f"{rid} - given above", again["text"])
        self.assertNotIn("Listen, not read.", again["text"])
        self.assertIn(f"CASE {slides}: Defence slides - given above, in full", again["text"])
        self.assertNotIn("Reasons are why the user judges", again["text"])
        # Kept whole, to be given alone later.
        self.assertIn("Listen, not read.", again["whole"])
        self.assertEqual(again["found"]["reasons"], [rid])


class TheDescriptionTests(unittest.TestCase):

    def test_not_when_it_is_the_instruction(self) -> None:
        run = Run.__new__(Run)
        run.instruction = "write the notes\n"
        run._description = lambda: "write  the notes"
        self.assertEqual(run._description_text(), "")
        run._description = lambda: "Notes for the lab, kept weekly."
        self.assertEqual(run._description_text(),
                         "The user's description of this project: Notes for the lab, kept weekly.")


if __name__ == "__main__":
    unittest.main()
