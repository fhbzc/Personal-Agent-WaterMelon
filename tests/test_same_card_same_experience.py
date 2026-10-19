"""The same card is given the same past work: what a run's reviewer was given
for a card the first time is kept, and given again when the card is opened
again, reviewed again, or carried on after the application closed - even
when the record has changed meanwhile. Kept in the application's database,
never in the run's plan, which goes with its versions into the work's own
folder. And a stage handed over for one card is not held against another."""

from __future__ import annotations

import unittest
from unittest import mock

from support import TemporaryAgent

from personalized_agent.agent.work import runner
from personalized_agent.agent.work.runner import Review, Run


class _Retriever:
    def __init__(self):
        self.asked = []
        self.answer = {"matches": [{"id": "stage_first"}]}

    def find(self, profile):
        self.asked.append(profile)
        return self.answer


class _Run:
    """What `Review` asks of a run - its record kept by the real methods."""
    experience_given = Run.experience_given
    keep_experience_given = Run.keep_experience_given
    lessons_for = Run.lessons_for
    lessons = None                      # no lesson cards: these are of the stages

    def __init__(self, app, run_id="run_one"):
        self.owner = app.work
        self.id = run_id
        self.retriever = _Retriever()
        self.lessons_shown = {}

    def _in_english(self, profile):
        return dict(profile)


class TheSameCardTheSamePastWorkTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        patcher = mock.patch.object(runner, "compact_experience", lambda said: dict(said))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_asked_once_and_given_again(self) -> None:
        run = _Run(self.app)
        Review(run, {"text": "the pledge form"}, "the work", same_as="criteria:card_1")
        # The record changes meanwhile - learning goes on beside the run.
        run.retriever.answer = {"matches": [{"id": "stage_new"}]}
        again = Review(run, {"text": "the pledge form"}, "the work", same_as="criteria:card_1")
        self.assertEqual(len(run.retriever.asked), 1)
        self.assertIn("stage_first", again.messages[0]["content"])
        self.assertNotIn("stage_new", again.messages[0]["content"])

    def test_and_after_the_application_closed(self) -> None:
        Review(_Run(self.app), {"text": "ledger rows"}, "the work", same_as="round:card_2")
        # The same run carried on in a new process: nothing in memory.
        later = _Run(self.app)
        later.retriever.answer = {"matches": [{"id": "stage_new"}]}
        Review(later, {"text": "ledger rows"}, "the work", same_as="round:card_2")
        self.assertEqual(later.retriever.asked, [])

    def test_another_card_another_question(self) -> None:
        run = _Run(self.app)
        Review(run, {"text": "a"}, "the work", same_as="criteria:card_1")
        Review(run, {"text": "b"}, "the work", same_as="criteria:card_2")
        Review(run, {"text": "c"}, "the work")          # no card: asked each time
        Review(run, {"text": "c"}, "the work")
        self.assertEqual(len(run.retriever.asked), 4)
        # Nothing handed over before is held against the next question.
        self.assertTrue(all("seen" not in one for one in run.retriever.asked))

    def test_kept_in_the_database_not_in_the_plan(self) -> None:
        run = _Run(self.app)
        Review(run, {"text": "a"}, "the work", same_as="criteria:card_1")
        rows = self.app.db.query("SELECT key FROM work_run_experience WHERE run_id = ?",
                                 ("run_one",))
        self.assertEqual([row["key"] for row in rows], ["criteria:card_1"])

    def test_no_down_weighting_left(self) -> None:
        from personalized_agent.heartbeat.experience import retrieve
        self.assertFalse(hasattr(retrieve, "SEEN_ALREADY"))


if __name__ == "__main__":
    unittest.main()
