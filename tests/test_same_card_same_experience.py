"""The same card is given the same learned package: what a run's reviewer was
given for its questions about a card the first time (`Run._what_to_know`,
kept under "criteria:<card>") is given again when the card is reviewed
again, opened again, or carried on after the application closed - even when
what was learned has changed meanwhile. A round of a card is given its
criteria's package; a card with none yet, the contract's. Kept in the
application's database, never in the run's plan, which goes with its
versions into the work's own folder."""

from __future__ import annotations

import unittest

from support import TemporaryAgent

from personalized_agent.agent.work.runner import Review, Run


class _Run:
    """What `Review` and `_what_to_know` ask of a run - its record kept by
    the real methods."""
    experience_given = Run.experience_given
    keep_experience_given = Run.keep_experience_given
    learned_given = Run.learned_given
    _what_to_know = Run._what_to_know

    def __init__(self, app, run_id="run_one"):
        self.owner = app.work
        self.id = run_id
        self._facets = {}

    def _keep(self, key, text, facets=None):
        self.keep_experience_given(key, {"text": text, "found": {},
                                         "facets": facets or {}})


class _NeverAsked:
    """A reviewer whose questions must not be asked again."""

    def __init__(self, attached=False, plain=False):
        self.attached = attached
        self.plain = plain

    def ask(self, *args, **kwargs):
        raise AssertionError("asked again for a card it was given a package for")


class TheSameCardTheSamePackageTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_given_again_the_same_never_the_newer(self) -> None:
        run = _Run(self.app)
        run._keep("criteria:card_1", "PACKAGE ONE", {"deliverable": ["deliverable:report"]})
        # Learning goes on beside the run: kept again, the first one stands.
        run._keep("criteria:card_1", "PACKAGE NEWER")
        review = Review(run, {"text": "the pledge form"}, "the work",
                        same_as="criteria:card_1")
        self.assertTrue(review.attached)
        self.assertIn("PACKAGE ONE", review.messages[0]["content"])
        self.assertNotIn("PACKAGE NEWER", review.messages[0]["content"])
        # The job's terms it was asked with come back with it.
        self.assertEqual(run._facets, {"deliverable": ["deliverable:report"]})

    def test_a_round_is_given_its_cards_package_else_the_contracts(self) -> None:
        run = _Run(self.app)
        run._keep("contract", "THE CONTRACT'S")
        run._keep("criteria:card_1", "CARD ONE'S")
        self.assertEqual(run.learned_given("round:card_1"), "CARD ONE'S")
        self.assertEqual(run.learned_given("criteria:card_1"), "CARD ONE'S")
        # Another card - asked nothing of its own yet - the contract's.
        self.assertEqual(run.learned_given("round:card_2"), "THE CONTRACT'S")
        self.assertEqual(run.learned_given("criteria:card_2"), "THE CONTRACT'S")
        self.assertEqual(run.learned_given(""), "THE CONTRACT'S")
        # Nothing asked at all: nothing given.
        self.assertEqual(_Run(self.app, "run_two").learned_given("round:card_1"), "")
        bare = Review(_Run(self.app, "run_two"), {"text": "a"}, "the work",
                      same_as="round:card_1")
        self.assertFalse(bare.attached)
        self.assertEqual(bare.messages[0]["content"], "the work")

    def test_and_after_the_application_closed(self) -> None:
        _Run(self.app)._keep("criteria:card_2", "LEDGER ROWS' PACKAGE")
        # The same run carried on in a new process: nothing in memory.
        later = _Run(self.app)
        review = Review(later, {"text": "ledger rows"}, "the work", same_as="round:card_2")
        self.assertIn("LEDGER ROWS' PACKAGE", review.messages[0]["content"])

    def test_the_questions_are_not_asked_again_for_the_same_card(self) -> None:
        run = _Run(self.app)
        run._keep("criteria:card_1", "CARD ONE'S", {"purpose": ["purpose:teach"]})
        card = {"id": "card_1", "title": "The pledge form"}
        # Already in its context (attached when its review began): nothing
        # more to put before the criteria.
        self.assertEqual(run._what_to_know(_NeverAsked(attached=True), "THIS CARD", "{}",
                                           card), "")
        # Not attached - a review begun for something else: the same text.
        self.assertEqual(run._what_to_know(_NeverAsked(), "THIS CARD", "{}", card),
                         "CARD ONE'S")
        self.assertEqual(run._facets, {"purpose": ["purpose:teach"]})

    def test_kept_in_the_database_not_in_the_plan(self) -> None:
        run = _Run(self.app)
        run._keep("criteria:card_1", "a")
        rows = self.app.db.query("SELECT key FROM work_run_experience WHERE run_id = ?",
                                 ("run_one",))
        self.assertEqual([row["key"] for row in rows], ["criteria:card_1"])
        self.assertFalse(hasattr(run, "plan"))


if __name__ == "__main__":
    unittest.main()
