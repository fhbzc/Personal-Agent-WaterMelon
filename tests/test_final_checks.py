"""THE TWO FINAL CHECKS of a run - its quality, and the user's privacy -
each by a context of its own that never saw how the work was built: both
must pass. What they find goes to a round of fixes, at most CHECK_FIXES for
each; one still failing after them ends the run saying which. A check that
could not open the work has not passed. Every way of the Work debugger is
checked so - the single model too - and every run of the user's own work;
each check is a kind of its own in the token charts."""

from __future__ import annotations

import json
import time
import unittest

from support import TemporaryAgent
from test_experience_debugger import _DebuggerBase, _Models
from test_work_runs import _TwoAgents

from personalized_agent.agent.work.runner import (CHECK_FAILED, CHECK_FIXES, CHECK_TASKS,
                                                  DONE)

PASS = {"passed": True, "checked": ["opened notes.md", "ran nothing: a note"],
        "problems": []}
FAIL = {"passed": False, "checked": ["opened notes.md"],
        "problems": [{"say": "the note names the user's street address",
                      "severity": "high", "file": "notes.md"}]}
# Says it passed, and opened nothing: not a pass.
BLIND = {"passed": True, "checked": [], "problems": []}


class _Fixing(_TwoAgents):
    """The two agents, fixing on the card they made: once accepted it is no
    longer listed open, and the rounds of fixes are on it."""

    def _summary(self, first):
        said = super()._summary(first)
        if said.get("card"):
            self.card = said["card"]
        else:
            said["card"] = getattr(self, "card", "")
        return said


class _Checked:
    """Any models of the tests' own, with the two checks answered as each
    test says: a list of answers per check, the last one kept."""

    MARKS = {"quality": "You CHECK THE QUALITY", "privacy": "FOR PRIVACY, independently"}

    def __init__(self, inner, **said):
        self.inner = inner
        self.said = {kind: list(said.get(kind) or [PASS]) for kind in self.MARKS}
        self.checks = {kind: [] for kind in self.MARKS}

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def invoke(self, request, role=""):
        for kind, mark in self.MARKS.items():
            if mark in (request.system or ""):
                self.checks[kind].append(request)
                answers = self.said[kind]
                answer = answers.pop(0) if len(answers) > 1 else answers[0]
                return _TwoAgents._reply(json.dumps(answer))
        return self.inner.invoke(request, role)


class TheChecksOfARunTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.app.work.final_checks = True
        self.unit = {"id": "task-chk123", "name": "Write notes", "kind": "task", "why": ""}

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

    def test_both_pass_and_each_is_a_kind_of_its_own(self) -> None:
        models = _Checked(_Fixing())
        run = self._run(models)
        self.assertEqual(run["status"], DONE, run["log"][-6:])
        checks = run["plan"]["final_checks"]
        self.assertTrue(checks["quality"]["passed"])
        self.assertTrue(checks["privacy"]["passed"])
        # Each asked once, in a context of its own: what the user asked and
        # where the work is - not what its builder claims of it.
        for kind in ("quality", "privacy"):
            self.assertEqual(len(models.checks[kind]), 1)
            request = models.checks[kind][0]
            # One question: what it answers is added after it.
            self.assertEqual([one["role"] for one in request.messages][:1], ["user"])
            told = request.messages[0]["content"]
            self.assertIn("write the notes", told)
            self.assertIn("notes.md", told)
            self.assertNotIn("wrote notes.md", told)
            self.assertEqual(request.task, CHECK_TASKS[kind])
            names = {spec.name for spec in request.tools or []}
            self.assertIn("run_python", names)
            self.assertNotIn("write_file", names)
        # The run's chart: who made each call, the checks by their names.
        whos = {one["who"] for one in run["calls"]}
        self.assertTrue({"quality check", "privacy check"} <= whos, whos)
        # The privacy check looks for what is sent away and left behind.
        system = models.checks["privacy"][0].system
        for words in ("SENT AWAY", "LEFT ON THE COMPUTER", "THE USER'S PRIVATE INFORMATION",
                      "documents, slides"):
            self.assertIn(words, system)

    def test_what_one_finds_is_fixed_and_both_look_again(self) -> None:
        models = _Checked(_Fixing(), privacy=[FAIL, PASS])
        run = self._run(models)
        self.assertEqual(run["status"], DONE, run["log"][-6:])
        self.assertEqual(run["plan"]["check_fixes"], {"privacy": 1})
        self.assertEqual(len(models.checks["privacy"]), 2)
        self.assertEqual(len(models.checks["quality"]), 2)
        # The builder was given what the privacy check found.
        builder = [json.dumps(r.messages, default=str) for r in models.inner.asked
                   if "You review a work agent" not in (r.system or "")]
        self.assertTrue(any("[privacy check] the note names the user's street address" in one
                            for one in builder))

    def test_one_still_failing_ends_the_run_saying_which(self) -> None:
        models = _Checked(_Fixing(), privacy=[FAIL])
        run = self._run(models)
        self.assertEqual(run["status"], CHECK_FAILED, run["log"][-6:])
        self.assertEqual(run["plan"]["check_fixes"]["privacy"], CHECK_FIXES)
        self.assertEqual(len(models.checks["privacy"]), CHECK_FIXES + 1)
        reason = run["final"]["reason"]
        self.assertIn(f"the privacy check did not pass after {CHECK_FIXES} rounds of fixes",
                      reason)
        self.assertIn("street address", reason)
        self.assertNotIn("quality check", reason)
        # It can be continued, as a run that stopped short can.
        self.assertTrue(run["continuable"])

    def test_a_check_that_opened_nothing_has_not_passed(self) -> None:
        models = _Checked(_Fixing(), quality=[BLIND])
        run = self._run(models)
        self.assertEqual(run["status"], CHECK_FAILED, run["log"][-6:])
        self.assertIn("the quality check did not pass", run["final"]["reason"])
        self.assertIn("could not open or try the work", run["final"]["reason"])
        self.assertFalse(run["plan"]["final_checks"]["quality"]["passed"])


class TheSingleModelIsCheckedTooTests(_DebuggerBase):

    def setUp(self) -> None:
        super().setUp()
        self.app.work.final_checks = True

    def test_not_done_until_both_pass(self) -> None:
        models = _Checked(_Models(alone_turns=1), privacy=[FAIL, PASS])
        self.app.work.models = models
        project = self._project()
        self._start(project, "single")
        run = self._finished(project, "single")
        self.assertEqual(run["status"], DONE, run["log"][-6:])
        self.assertEqual(len(models.checks["privacy"]), 2)
        # What the check found went back to the single model, to fix.
        told = [json.dumps(r.messages, default=str) for r in models.inner.asked]
        self.assertTrue(any("TWO INDEPENDENT CHECKS LOOKED AT YOUR FINISHED WORK" in one
                            for one in told))
        whos = {one["who"] for one in run["calls"]}
        self.assertTrue({"single", "quality check", "privacy check"} <= whos, whos)


if __name__ == "__main__":
    unittest.main()
