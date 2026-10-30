"""THE PLAN IS WHAT REMAINS; EACH ROUND'S REVIEW IS ITS CARD'S; THE WHOLE IS
TRIED ONCE, AT THE END (the user, 2026-10-29).

A replication handed over almost done took 19 rounds and 14 full reviews:
every round's review ran every core promise again, and every full review
re-checked every card since the last one that passed. Now the contract
lists what is already there and plans only what remains; a round's review
checks its card; a card done is a version, and the only place the reviewer
may ask for a full review; and at the end a tester runs the work from
scratch once - the final full review and the quality check use its run."""

from __future__ import annotations

import json
import time
import unittest

from support import TemporaryAgent
from test_final_checks import _Checked
from test_work_runs import _Promised, _TwoAgents

from personalized_agent.agent.work import runner


class _There(_TwoAgents):
    """A contract that finds a part already there."""

    def invoke(self, request, role=""):
        first = request.messages[0]["content"]
        first = first if isinstance(first, str) else json.dumps(first)
        if "You review a work agent" not in (request.system or "") \
                and "DRAFT THE CONTRACT" in first:
            self.asked.append(request)
            return self._reply(json.dumps({
                "goal": "notes.md says hello", "scope": ["the notes"],
                "out_of_scope": ["anything else"],
                "already_there": [{"what": "the heading of notes.md",
                                   "where": "notes.md, line 1",
                                   "seen": "opened notes.md"}],
                "deliverables": [{"ref": "d1", "title": "The notes", "type": "document",
                                  "statement": "notes.md says hello",
                                  "why": "it is what was asked"}]}))
        return super().invoke(request, role)


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.unit = {"id": "task-once42", "name": "Write notes", "kind": "task", "why": ""}

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _run(self, models):
        self.app.work.models = models
        started = self.app.work.start(self.unit, "write the notes")
        self.assertNotIn("error", started, started)
        for _ in range(900):
            runs = self.app.work.runs(self.unit["id"])
            if runs and not runs[0]["alive"] and runs[0]["status"] != "running":
                return runs[0]
            time.sleep(0.1)
        self.fail("the run did not end")

    @staticmethod
    def _said(request) -> str:
        return json.dumps(request.messages, default=str)

    def _kinds(self, run):
        return [one["kind"] for one in self.app.work.versions(run["id"])["versions"]]


class OnlyWhatRemainsIsPlannedTests(_Base):

    def test_the_contract_lists_what_is_there_and_both_agents_are_told(self) -> None:
        self.assertIn("PLAN ONLY WHAT REMAINS", runner.DRAFT_CONTRACT)
        self.assertIn('"already_there"', runner.DRAFT_CONTRACT)
        models = _There()
        run = self._run(models)
        self.assertEqual(run["status"], "done", run["log"][-6:])
        there = run["plan"]["contract"]["already_there"]
        self.assertEqual(there, [{"what": "the heading of notes.md",
                                  "where": "notes.md, line 1", "seen": "opened notes.md"}])
        # The reviewer checks the plan makes nothing again.
        contract_reviews = [one for one in models.asked
                            if "REVIEW THIS CONTRACT" in self._said(one)]
        self.assertTrue(any("DOES IT PLAN ONLY WHAT REMAINS" in self._said(one)
                            for one in contract_reviews))
        # The work agent is told what is there, to use as it is.
        rounds = [one for one in models.asked
                  if "You review a work agent" not in (one.system or "")
                  and "WORK ONE ROUND" in self._said(one)]
        self.assertTrue(rounds)
        self.assertIn("ALREADY THERE - used as it is", self._said(rounds[0]))
        self.assertIn("the heading of notes.md (notes.md, line 1)", self._said(rounds[0]))

    def test_kept_from_a_draft_as_said(self) -> None:
        kept = runner.already_there_of([{"what": "  the  model ", "where": "m.py"},
                                        {"where": "no what"}, "not a dict"])
        self.assertEqual(kept, [{"what": "the model", "where": "m.py", "seen": ""}])
        self.assertIn("ALREADY THERE: the model",
                      runner.contract_lines({"already_there": kept}))


class EachRoundReviewsItsCardTests(_Base):

    def test_the_round_review_tries_no_promise(self) -> None:
        self.assertIn("THIS CARD ONLY", runner.REVIEW_ROUND)
        self.assertNotIn("FIRST, THE CORE PROMISES", runner.REVIEW_ROUND)
        self.assertNotIn('"promises"', runner.REVIEW_ROUND)
        models = _Promised()
        run = self._run(models)
        self.assertEqual(run["status"], "done", run["log"][-6:])
        # The promise's state is the tester's, at the end - not a round's.
        self.assertEqual(run["plan"]["promise_status"]["P1"]["tried"], "read notes.md")

    def test_a_card_done_is_a_version(self) -> None:
        run = self._run(_TwoAgents(rounds_to_complete=2, full_review="never",
                                   card_done_each=True))
        self.assertEqual(run["status"], "done", run["log"][-6:])
        self.assertEqual(self._kinds(run), ["contract", "card", "card", "finished"])
        versions = self.app.work.versions(run["id"])["versions"]
        self.assertEqual([one.get("round") for one in versions[1:3]], [1, 2])

    def test_a_full_review_only_at_a_card_done(self) -> None:
        models = _TwoAgents(rounds_to_complete=2, full_review="always")
        run = self._run(models)
        self.assertEqual(run["status"], "done", run["log"][-6:])
        # Asked in round 1, with no card done: not made.
        self.assertTrue(any(entry.get("what") == "full review not made"
                            and entry.get("round") == 1 for entry in run["log"]))
        # Made at round 2's card done - its version a full review's - and
        # the final one at the end.
        self.assertEqual(models.full_reviews, 2)
        self.assertEqual(self._kinds(run), ["contract", "full review", "finished"])
        self.assertEqual([one["round"] for one in run["plan"]["full_reviews"]], [2, 2])


class TheWholeIsRunOnceTests(_Base):

    def test_the_final_full_review_and_the_quality_check_use_the_testers_run(self) -> None:
        self.app.work.final_checks = True
        models = _Checked(_Promised())
        run = self._run(models)
        self.assertEqual(run["status"], "done", run["log"][-6:])
        inner = models.inner
        testers = [one for one in inner.asked if "You TEST a piece of work" in one.system]
        self.assertEqual(len(testers), 1)
        finals = [one for one in inner.asked
                  if "You review a work agent" in (one.system or "")
                  and "DO THE FULL REVIEW NOW (the final full review" in self._said(one)]
        self.assertTrue(finals)
        self.assertIn("THE CORE PROMISES WERE JUST TRIED FOR REAL", self._said(finals[0]))
        self.assertIn("do NOT run the whole work again", self._said(finals[0]))
        quality = models.checks["quality"][0].messages[0]["content"]
        self.assertIn("A TESTER RAN THE WORK FROM SCRATCH JUST NOW", quality)
        self.assertIn("read notes.md", quality)
        privacy = models.checks["privacy"][0].messages[0]["content"]
        self.assertNotIn("A TESTER RAN THE WORK", privacy)

    def test_after_fixes_the_tester_tries_only_what_it_found_broken(self) -> None:

        class _Two(_Promised):
            """Two promises: the tester finds P2 broken the first time."""

            def __init__(self):
                super().__init__()
                self.tested = []

            def invoke(self, request, role=""):
                first = request.messages[0]["content"]
                first = first if isinstance(first, str) else json.dumps(first)
                if "You TEST a piece of work" in request.system:
                    self.asked.append(request)
                    told = [one for one in ("P1", "P2") if f"  {one}:" in first]
                    self.tested.append(told)
                    once = len(self.tested) == 1
                    return self._reply(json.dumps({
                        "promises": [{"id": one, "tried": f"ran {one}",
                                      "holds": "no" if (one == "P2" and once) else "yes"}
                                     for one in told],
                        "findings": []}))
                if "You review a work agent" not in (request.system or "") \
                        and "DRAFT THE CONTRACT" in first:
                    reply = super().invoke(request, role)
                    said = json.loads(reply.text)
                    said["core_promises"].append(dict(said["core_promises"][0], id="P2",
                                                      promise="notes.md has a title"))
                    reply.text = reply.raw_content = json.dumps(said)
                    return reply
                return super().invoke(request, role)

        models = _Two()
        run = self._run(models)
        self.assertEqual(run["status"], "done", run["log"][-6:])
        self.assertEqual(models.tested, [["P1", "P2"], ["P2"]])
        # What it found of P1 the first time is kept beside P2's second.
        kept = {one["id"]: one["holds"] for one in run["plan"]["black_box"]["promises"]}
        self.assertEqual(kept, {"P1": "yes", "P2": "yes"})


class _Found(_TwoAgents):
    """The final review finds as many problems each time as `counts` says,
    the last count for every review after."""

    def __init__(self, counts):
        super().__init__()
        self.counts = list(counts)

    def _summary(self, first):
        # Fixes are on the card done: it is no longer listed open.
        said = super()._summary(first)
        said["card"] = said.get("card") or getattr(self, "card", "")
        self.card = said["card"]
        return said

    def invoke(self, request, role=""):
        last = request.messages[-1]["content"]
        said = last if isinstance(last, str) else json.dumps(last)
        if "You review a work agent" in (request.system or "")                 and "THE GOAL IS MET BY YOUR LAST REVIEW" in said:
            self.final_reviews += 1
            count = self.counts[min(self.final_reviews, len(self.counts)) - 1]
            return self._reply(json.dumps({"findings": [
                {"say": f"problem {n}", "severity": "high", "file": "notes.md"}
                for n in range(count)]}))
        return super().invoke(request, role)


class FixesCountedByWhatTheyMovedTests(_Base):

    def test_fixes_that_move_it_go_on_past_three(self) -> None:
        run = self._run(_Found([5, 4, 3, 2, 1, 0]))
        self.assertEqual(run["status"], "done", run["log"][-6:])
        self.assertEqual(run["plan"]["fix_rounds"], 5)
        self.assertEqual(run["plan"].get("fix_stalls"), 0)

    def test_three_in_a_row_that_move_nothing_end_them(self) -> None:
        models = _Found([2])
        run = self._run(models)
        self.assertEqual(run["plan"]["fix_rounds"], runner.FIX_ROUNDS)
        self.assertEqual(run["plan"]["fix_stalls"], runner.FIX_ROUNDS)
        self.assertEqual(models.final_reviews, runner.FIX_ROUNDS + 1)

    def test_and_never_more_than_the_most_in_all(self) -> None:
        models = _Found(list(range(runner.FIX_ROUNDS_MOST + 2, -1, -1)))
        run = self._run(models)
        self.assertEqual(run["plan"]["fix_rounds"], runner.FIX_ROUNDS_MOST)
        self.assertEqual(models.final_reviews, runner.FIX_ROUNDS_MOST + 1)


if __name__ == "__main__":
    unittest.main()
