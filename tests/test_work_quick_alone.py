"""THE QUICK WAY ALONE, WITH ITS MONITOR (the user, 2026-10-31).

The thorough way - a builder and a reviewer - is switched off for now: the
user's own work is done the quick way, with no way to choose, no word in
the plan's one call of which way, nothing said of the work being too big,
and no "Continue thoroughly". And the quick way has a MONITOR: when the
agent writes, runs or ends a turn, it looks up what bears on it and, where
the agent differs from the user, reminds it - with a tool's result or the
next message - never stopping it."""

from __future__ import annotations

import json
import time
import unittest

from support import TemporaryAgent
from test_delegate import PLAN, _Models as _Planner
from test_work_quick import _Quick

from personalized_agent.agent.work import runner as rn
from personalized_agent.agent.work.delegate import PLAN_PROMPT, PLAN_PROMPT_QUICK, plan_of

REMIND = "Sign the email GIRAFFEPLUM, as the user always does"


class _Watched(_Quick):
    """The quick agent, and a monitor that finds one difference the first
    time it looks, and none after. `slow`: the monitor takes that long to
    look - the agent goes on meanwhile; `patient`: the agent's second step
    comes once the monitor has looked (an agent slower than its monitor)."""

    def __init__(self, slow=0.0, patient=True, **kwargs):
        super().__init__(**kwargs)
        self.monitor = []
        self.slow, self.patient = slow, patient

    def invoke(self, request, role=""):
        if "You watch a work agent" in (request.system or ""):
            self.monitor.append(request)
            time.sleep(self.slow)
            if len(self.monitor) == 1:
                return self._reply(json.dumps({
                    "differs": True, "about": "the email's signature", "remind": REMIND,
                    "evidence": ["reason: Emails are signed GIRAFFEPLUM"]}))
            return self._reply(json.dumps({"differs": False}))
        if self.patient and "on your own" in (request.system or "") and len(self.quick) == 1:
            for _ in range(100):
                if self.monitor:
                    break
                time.sleep(0.05)
            time.sleep(0.3)
        return super().invoke(request, role)


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.app.work.thorough_open = False        # as the application has it
        self.unit = {"id": "task-quick2", "name": "Email to Ada", "kind": "task", "why": ""}

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _wait(self):
        for _ in range(900):
            runs = self.app.work.runs(self.unit["id"])
            if runs and not runs[0]["alive"] and runs[0]["status"] != "running":
                return runs[0]
            time.sleep(0.1)
        self.fail("the run did not end")


class TheThoroughWaySwitchedOffTests(_Base):

    def test_off_in_the_application(self) -> None:
        self.assertFalse(rn.THOROUGH_OPEN)

    def test_every_piece_of_work_the_quick_way(self) -> None:
        models = _Watched()
        self.app.work.models = models
        said = self.app.work.start(self.unit, "write Ada an email", way="full")
        self.assertEqual(said["way"], rn.QUICK)
        run = self._wait()
        self.assertEqual(run["plan"]["way"], rn.QUICK)
        # Nothing said of a way it is too big for.
        self.assertNotIn("TOO BIG FOR THE QUICK WAY", models.quick[0].system)
        self.assertNotIn('"thorough"', models.quick[0].system)
        self.assertIn("A MONITOR WATCHES YOUR WORK", models.quick[0].system)
        self.assertIn("switched off", self.app.work.thorough(run["id"], self.unit)["error"])

    def test_the_plans_one_call_says_nothing_of_a_way(self) -> None:
        self.assertNotIn('"way"', PLAN_PROMPT_QUICK)
        self.assertNotIn("THE WAY", PLAN_PROMPT_QUICK)
        self.assertNotIn('With "full"', PLAN_PROMPT_QUICK)
        self.assertIn('"questions"', PLAN_PROMPT_QUICK)
        self.assertIn('"way"', PLAN_PROMPT, "kept, for the thorough way open again")
        self.app.models = _Planner(dict(PLAN, way="full", way_why="code to build"))
        seen = []
        invoke = self.app.models.invoke
        self.app.models.invoke = lambda request, role="": seen.append(request) or invoke(
            request, role)
        draft = self.app.drafts.create(text="Write Ada a note", collect=False)
        self.app.drafts.collect_and_plan(draft["id"])
        plans = [one for one in seen if one.system in (PLAN_PROMPT, PLAN_PROMPT_QUICK)]
        self.assertEqual(len(plans), 1, "one call, as before")
        self.assertEqual(plans[0].system, PLAN_PROMPT_QUICK)
        plan = self.app.drafts.get(draft["id"])["plan"]
        self.assertEqual((plan["way"], plan["way_why"]), ("quick", ""))
        self.assertEqual(plan_of(dict(PLAN, way="full"), thorough=False)["way"], "quick")


class TheMonitorTests(_Base):

    def test_it_looks_up_and_reminds_never_stopping_the_work(self) -> None:
        models = _Watched()
        self.app.work.models = models
        self.app.work.start(self.unit, "write Ada an email", way="quick")
        run = self._wait()
        self.assertEqual(run["status"], rn.DONE, run["log"][-6:])
        self.assertGreaterEqual(len(models.monitor), 1, "it looked")
        # Its tools: looking things up and reading - nothing that writes or runs.
        tools = {spec.name for spec in models.monitor[0].tools or []}
        self.assertTrue(tools <= set(rn.MONITOR_TOOLS), tools)
        self.assertNotIn("write_file", tools)
        # The user's contacts, to look people up in - the monitor and the agent alike.
        self.assertTrue({"search_people", "open_person"} <= tools, tools)
        self.assertTrue({"search_people", "open_person"}
                        <= {spec.name for spec in models.quick[0].tools or []})
        # What it was shown: the work, and what the agent did.
        shown = models.monitor[0].messages[0]["content"]
        self.assertIn("WHAT THE USER ASKED: write Ada an email", shown)
        self.assertIn("write_file", shown)
        # The reminder reached the agent - with a tool's result or a message.
        later = json.dumps([one.messages for one in models.quick[1:]], ensure_ascii=False)
        self.assertIn(REMIND, later)
        # Given once (each request holds the whole exchange: the last one says it all).
        self.assertEqual(json.dumps(models.quick[-1].messages, ensure_ascii=False)
                         .count(REMIND), 1)
        # In the live session, said as the monitor's.
        events = rn.read_session(self.app.work.log_dir_of(run))
        said = [one for one in events if one.get("who") == "monitor"]
        self.assertTrue(any("reminds the agent" in str(one.get("text")) for one in said), said)
        # Its calls are the run's, under "monitor", and a kind of their own.
        self.assertTrue(any(one.get("who") == "monitor" for one in run["calls"]), run["calls"])

    def test_it_reads_what_the_agent_wrote_and_compares_the_tone(self) -> None:
        models = _Watched()
        self.app.work.models = models
        self.app.work.start(self.unit, "write Ada an email", way="quick")
        self._wait()
        shown = models.monitor[0].messages[0]["content"]
        self.assertIn("WHAT IT WROTE:", shown, "the draft, whole")
        self.assertIn("Dear Ada,", shown)
        self.assertIn("WRITTEN AS THE USER WRITES TO THAT PERSON", models.monitor[0].system)

    def test_the_plan_the_user_approved_is_followed(self) -> None:
        said = " ".join(rn.ALONE_SYSTEM.split())
        self.assertIn("A PLAN THE USER APPROVED, where the task has one, is theirs: follow it",
                      said)
        self.assertIn("say in your report what you changed and why", said)
        self.assertNotIn("no plan you must follow", rn.ALONE_SYSTEM)

    def test_the_agent_never_waits_for_it(self) -> None:
        # A monitor that takes six seconds to look: the agent writes, says
        # done, and the run ends without it.
        models = _Watched(slow=6.0, patient=False)
        self.app.work.models = models
        began = time.monotonic()
        self.app.work.start(self.unit, "write Ada an email", way="quick")
        run = self._wait()
        self.assertEqual(run["status"], rn.DONE, run["log"][-6:])
        self.assertLess(time.monotonic() - began, 5.0, "not waited for")
        later = json.dumps([one.messages for one in models.quick[1:]], ensure_ascii=False)
        self.assertNotIn(REMIND, later, "a look not done is not waited for")

    def test_not_for_the_thorough_way(self) -> None:
        self.app.work.thorough_open = True
        models = _Watched()
        self.app.work.models = models
        self.app.work.start(self.unit, "write Ada an email", way="full")
        self._wait()
        self.assertEqual(models.monitor, [])

    def test_the_monitors_calls_are_the_users_own_work(self) -> None:
        from personalized_agent.common import modules
        self.assertEqual(modules.TASK_MODULE["work_monitor"], "manual_work")
        self.assertEqual(rn.CHECK_TASKS["monitor"], "work_monitor")


if __name__ == "__main__":
    unittest.main()
