"""THE REVIEWER'S CHECKS THAT DO NOT DEPEND ON ONE ANOTHER, SIDE BY SIDE (the
user, 2026-10-28) - only calls that were separate already: no call split,
none merged. A full review with few cards is still one call; past
FULL_REVIEW_CARDS_AT_ONCE its cards' own calls go at once, and the call
that concludes comes after them all, given their answers. The two final
checks at once. Each told where its own writing goes; one at a time while
the computer is busy."""

from __future__ import annotations

import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import test_final_checks as _final
from test_final_checks import _Checked, _Fixing
from test_work_runs import _TwoAgents
from test_work_versions import _RunsBase

from personalized_agent.agent.work import parallel
from personalized_agent.agent.work.runner import DONE, Run

#: Both sides must be in their call at once for either to go on.
MEET_SECONDS = 20


class _Meeting:
    """Calls marked by their words, each waiting for the other to begin."""

    def __init__(self, count=2):
        self.met = threading.Barrier(count, timeout=MEET_SECONDS)
        self.broken = False
        self.came = 0

    def meet(self):
        self.came += 1
        try:
            self.met.wait()
        except threading.BrokenBarrierError:
            self.broken = True


class TheAtOnceHelperTests(unittest.TestCase):

    def _stub(self):
        return SimpleNamespace(id="run_x")

    def test_side_by_side_in_order_and_a_failure_raised_once_all_are_back(self) -> None:
        meeting = _Meeting(3)
        done = []

        def one(n):
            def go():
                meeting.meet()
                done.append(n)
                return n
            return go
        with mock.patch.object(parallel, "machine_busy", return_value=False):
            self.assertEqual(Run._at_once(self._stub(), [one(1), one(2), one(3)]), [1, 2, 3])
        self.assertEqual(meeting.came, 3)
        self.assertFalse(meeting.broken, "all three were in their call at once")

        def bad():
            raise RuntimeError("the model went away")
        with mock.patch.object(parallel, "machine_busy", return_value=False):
            with self.assertRaises(RuntimeError):
                Run._at_once(self._stub(), [bad, lambda: done.append("after") or 1])
        self.assertIn("after", done, "the other was let finish")

    def test_one_at_a_time_while_the_computer_is_busy(self) -> None:
        going = {"now": 0, "most": 0}

        def one():
            going["now"] += 1
            going["most"] = max(going["most"], going["now"])
            time.sleep(0.05)
            going["now"] -= 1
            return 1
        with mock.patch.object(parallel, "machine_busy", return_value=True):
            Run._at_once(self._stub(), [one, one, one])
        self.assertEqual(going["most"], 1)


class _Reviews(_TwoAgents):
    """What each reviewer call was asked, as it was asked; and, `card_holds`
    no, a card that does not hold."""

    def __init__(self, card_holds="yes", **kwargs):
        kwargs.setdefault("full_review", "milestone")
        super().__init__(**kwargs)
        self.card_holds = card_holds
        self.reviews = []

    def invoke(self, request, role=""):
        last = request.messages[-1]["content"]
        said = last if isinstance(last, str) else json.dumps(last)
        if "You review a work agent" in (request.system or ""):
            self.reviews.append(said)
            if "Check this card, one of those" in said and self.card_holds != "yes":
                return self._reply(json.dumps({"card": "", "holds": self.card_holds,
                                               "why": "notes.md is empty", "findings": []}))
        return super().invoke(request, role)


class TheFullReviewTests(_RunsBase):

    def _at_once_counted(self):
        asked = []
        real = Run._at_once

        def counted(run, asks):
            asked.append(len(asks))
            return real(run, asks)
        patcher = mock.patch.object(Run, "_at_once", counted)
        patcher.start()
        self.addCleanup(patcher.stop)
        return asked

    def test_few_cards_still_one_call(self) -> None:
        asked = self._at_once_counted()
        models = _Reviews()
        run, _folder = self._run(models)
        self.assertEqual(run["status"], "done", run["log"][-6:])
        full = [one for one in models.reviews if "DO THE FULL REVIEW NOW" in one]
        self.assertEqual(len(full), 1, "one call, as before - not split")
        self.assertIn("Check every card those rounds touched", full[0])
        self.assertNotIn("OTHER CHECKS RUN BESIDE YOU", full[0])
        self.assertEqual(asked, [], "nothing side by side in it")

    def test_many_cards_each_at_once_then_the_conclusion_with_their_answers(self) -> None:
        from personalized_agent.agent.work import runner as runner_module
        asked = self._at_once_counted()
        models = _Reviews()
        with mock.patch.object(runner_module, "FULL_REVIEW_CARDS_AT_ONCE", 0):
            run, _folder = self._run(models)
        self.assertEqual(run["status"], "done", run["log"][-6:])
        self.assertEqual(asked, [1], "the cards' own calls, given to go at once")
        card = [n for n, one in enumerate(models.reviews) if "Check this card, one of those" in one]
        concluded = [n for n, one in enumerate(models.reviews)
                     if "Every card the rounds touched is checked" in one]
        self.assertEqual(len(card), 1)
        self.assertEqual(len(concluded), 1)
        self.assertLess(card[0], concluded[0], "the conclusion after the cards")
        self.assertIn("OTHER CHECKS RUN BESIDE YOU", models.reviews[card[0]])
        # It concludes with the cards' answers before it.
        self.assertIn("opened notes.md", models.reviews[concluded[0]].split("Their answers:")[1])
        self.assertEqual([one["passed"] for one in run["plan"]["full_reviews"]], [True])

    def test_a_card_that_does_not_hold_fails_it(self) -> None:
        from personalized_agent.agent.work import runner as runner_module
        models = _Reviews(card_holds="no", rounds_to_complete=1)
        with mock.patch.object(runner_module, "FULL_REVIEW_CARDS_AT_ONCE", 0):
            run, _folder = self._run(models)
        full = run["plan"]["full_reviews"]
        self.assertTrue(full, run["log"][-6:])
        self.assertFalse(full[0]["passed"], "the conclusion passed; the card did not hold")
        concluded = [one for one in models.reviews
                     if "Every card the rounds touched is checked" in one]
        self.assertIn('"holds": "no"', concluded[0], "and the conclusion was told so")


class TheFinalChecksSideBySideTests(unittest.TestCase):
    # The final checks' own setting up - borrowed, not their tests.
    setUp = _final.TheChecksOfARunTests.setUp
    tearDown = _final.TheChecksOfARunTests.tearDown
    _run = _final.TheChecksOfARunTests._run

    def test_quality_and_privacy_at_once(self) -> None:
        meeting = _Meeting()

        class _Meet(_Checked):
            def invoke(inner_self, request, role=""):
                if any(mark in (request.system or "") for mark in _Checked.MARKS.values()):
                    meeting.meet()
                    self.assertIn("OTHER CHECKS RUN BESIDE YOU", json.dumps(request.messages))
                return _Checked.invoke(inner_self, request, role)
        models = _Meet(_Fixing())
        with mock.patch.object(parallel, "machine_busy", return_value=False):
            run = self._run(models)
        self.assertEqual(run["status"], DONE, run["log"][-6:])
        self.assertEqual(meeting.came, 2)
        self.assertFalse(meeting.broken, "the two checks were asked at the same time")
        self.assertTrue(run["plan"]["final_checks"]["privacy"]["passed"])


if __name__ == "__main__":
    unittest.main()
