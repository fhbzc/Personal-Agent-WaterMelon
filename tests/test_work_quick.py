"""THE QUICK WAY of the user's own work (the user, 2026-10-26): one agent on
its own judgement, as a session told everything at the start would do it -
no contract, no cards, no reviewer - but with the user's context: what was
learned of how they work, for the job, given at the start, and their files,
records and past cases to search. At its end only the privacy check, and a
README only when it made more than one file. Too big for it, it says so,
and the work is carried on the thorough way from what it made. Every box
that starts work offers the way, as the plan suggests it, with why."""

from __future__ import annotations

import json
import time
import unittest

from support import TemporaryAgent
from test_delegate import PLAN, _Models as _Planner
from test_final_checks import PASS, _Checked
from test_work_runs import _TwoAgents

from personalized_agent.agent.work.delegate import plan_of
from personalized_agent.agent.work.runner import DONE, FULL, NEEDS_THOROUGH, QUICK


class _Quick(_TwoAgents):
    """The quick agent: its questions before it starts, then a turn that
    writes email.md, then done - or, `too_big`, that it needs the thorough
    way. Anything else is the two agents of a thorough run."""

    def __init__(self, too_big=False, files=("email.md",), **kwargs):
        super().__init__(**kwargs)
        self.too_big = too_big
        self.files = files
        self.quick = []

    def invoke(self, request, role=""):
        from personalized_agent.models.base import ToolCall
        last = request.messages[-1]["content"]
        said = last if isinstance(last, str) else json.dumps(last)
        if "BEFORE YOU START THIS WORK" in said:
            self.asked.append(request)
            return self._reply(json.dumps({
                "terms": {}, "questions": [{"q": "How long should the email be?",
                                            "kind": "output"}]}))
        if "DOES EACH OF THESE REASONS" in (request.system or ""):
            self.asked.append(request)
            return self._reply("{}")
        if "on your own" not in (request.system or ""):
            return super().invoke(request, role)
        if "WRITE THE README" in said:
            return self._reply(json.dumps({"readme": "# What was made\nOpen them."}))
        self.asked.append(request)
        self.quick.append(request)
        if not said.startswith("GO ON") and len(self.quick) == 1:
            return self._reply("", [ToolCall(id=f"w{n}", name="write_file",
                                             arguments={"path": name, "content": "Dear Ada,"})
                                    for n, name in enumerate(self.files)])
        if self.too_big:
            return self._reply(json.dumps({
                "done": False, "report": "began the email",
                "thorough": "it needs a mail merge built and tested"}))
        return self._reply(json.dumps({"done": True, "report": "wrote email.md",
                                       "commit": {"title": "Write the email", "body": []}}))


class TheQuickWayTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.unit = {"id": "task-quick1", "name": "Email to Ada", "kind": "task", "why": ""}
        # WHAT WAS LEARNED of the user's work: a reason of theirs for any job.
        from personalized_agent.learning.store import LearningStore
        LearningStore(self.app.db).save_reason({
            "statement": "Emails are signed GIRAFFEPLUM", "source": "stated",
            "aspect": "output", "state": "active", "condition": {},
            "first_seen": "2026-09-01", "last_seen": "2026-10-01"})

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _wait(self, count=1):
        for _ in range(900):
            runs = self.app.work.runs(self.unit["id"])
            if len(runs) >= count and not runs[0]["alive"] and runs[0]["status"] != "running":
                return runs[0]
            time.sleep(0.1)
        self.fail("the run did not end")

    def _start(self, models, **kw):
        self.app.work.models = models
        started = self.app.work.start(self.unit, "write Ada an email", **kw)
        self.assertNotIn("error", started, started)
        return started

    def test_one_agent_with_the_users_context_and_the_privacy_check_alone(self) -> None:
        self.app.work.final_checks = True
        models = _Checked(_Quick())
        started = self._start(models, way="quick")
        self.assertEqual(started["way"], QUICK)
        run = self._wait()
        self.assertEqual(run["status"], DONE, run["log"][-6:])
        self.assertEqual(run["plan"]["way"], QUICK)
        # No contract, no reviewer.
        self.assertFalse(run["plan"].get("contract"))
        self.assertFalse(any("You review a work agent" in (r.system or "")
                             for r in models.inner.asked))
        # What was learned of the user's work, for its questions: given at the start.
        brief = models.inner.quick[0].messages[0]["content"]
        self.assertIn("HOW THE USER WORKS ON THINGS LIKE THIS", brief)
        self.assertIn("How long should the email be?", brief)
        self.assertIn("GIRAFFEPLUM", brief)
        self.assertIn("THIS IS THE USER'S OWN WORK, done the quick way",
                      models.inner.quick[0].system)
        # Their files, records and past cases to search; no cards.
        tools = {spec.name for spec in models.inner.quick[0].tools or []}
        self.assertTrue({"open_case", "open_reason", "write_file"} <= tools, tools)
        self.assertNotIn("open_card", tools)
        # The privacy check alone; one file made, so no README.
        self.assertEqual(len(models.checks["privacy"]), 1)
        self.assertEqual(models.checks["quality"], [])
        folder = self.app.work.folder_of(self.unit)
        self.assertTrue((folder / "email.md").exists())
        self.assertFalse((folder / "README.md").exists())

    def test_a_readme_only_for_more_than_one_file(self) -> None:
        self._start(_Quick(files=("email.md", "list.csv")), way="quick")
        run = self._wait()
        self.assertEqual(run["status"], DONE, run["log"][-6:])
        self.assertTrue((self.app.work.folder_of(self.unit) / "README.md").exists())

    def test_too_big_it_is_carried_on_thoroughly(self) -> None:
        models = _Quick(too_big=True)
        self._start(models, way="quick")
        quick = self._wait()
        self.assertEqual(quick["status"], NEEDS_THOROUGH, quick["log"][-6:])
        self.assertEqual(quick["final"]["thorough"], "it needs a mail merge built and tested")
        said = self.app.work.thorough(quick["id"], self.unit)
        self.assertNotIn("error", said, said)
        self.assertEqual(said["way"], FULL)
        run = self._wait(count=2)
        self.assertEqual(run["plan"]["way"], FULL)
        self.assertIn("BEGUN THE QUICK WAY", run["instruction"])
        self.assertIn("mail merge", run["instruction"])
        self.assertIn("began the email", run["instruction"])
        self.assertTrue(any("You review a work agent" in (r.system or "")
                            for r in models.asked))
        # Only a quick run is carried on so.
        self.assertIn("error", self.app.work.thorough(run["id"], self.unit))

    def test_thorough_unless_told(self) -> None:
        self._start(_Quick(), way="nonsense")
        run = self._wait()
        self.assertEqual(run["plan"]["way"], FULL)
        self.assertTrue(run["plan"].get("contract"))

    def test_continued_the_same_way(self) -> None:
        models = _Quick()
        self._start(models, way="quick")
        run = self._wait()
        self.assertEqual(run["status"], DONE, run["log"][-6:])
        said = self.app.work.continue_run(run["id"], self.unit, instruction="add a PS")
        self.assertNotIn("error", said, said)
        time.sleep(0.5)
        run = self._wait()
        self.assertEqual(run["plan"]["way"], QUICK)
        self.assertGreater(len(models.quick), 2)
        self.assertIn("add a PS", json.dumps(models.quick[-1].messages))


class TheWayInThePlanTests(unittest.TestCase):

    def test_suggested_with_why_and_thorough_when_unsure(self) -> None:
        quick = plan_of(dict(PLAN, way="quick", way_why="one email, read by the user"))
        self.assertEqual((quick["way"], quick["way_why"]),
                         ("quick", "one email, read by the user"))
        self.assertFalse(any("README" in one for one in quick["deliverables"]),
                         "an email needs no README")
        for said in (PLAN, dict(PLAN, way="maybe")):
            full = plan_of(said)
            self.assertEqual(full["way"], "full")
            self.assertTrue(any("README" in one for one in full["deliverables"]))

    def test_started_the_way_and_with_the_settings_the_box_shows(self) -> None:
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        app = agent.app
        app.models = _Planner(dict(PLAN, way="quick", way_why="a short note"))
        started = []
        app.work.start = lambda unit, instruction, **kw: (
            started.append(kw) or {"run": "run_q"})
        draft = app.drafts.create(text="Write Ada a note", collect=False,
                                  options={"limits": {"minutes": 30}, "packages": "ask"})
        app.drafts.collect_and_plan(draft["id"])
        self.assertEqual(app.drafts.get(draft["id"])["plan"]["way"], "quick")
        app.drafts.start(draft["id"], folders=[],
                         options={"limits": {"minutes": 5}, "sound": True, "instruction": "x"})
        kw = started[0]
        self.assertEqual(kw["way"], "quick")
        self.assertEqual((kw["limits"], kw["packages"], kw["sound"]),
                         ({"minutes": 5}, "ask", True))
        # The user's instruction is not a setting the box sends.
        self.assertNotEqual(app.drafts.get(draft["id"])["options"].get("instruction"), "x")
        # The user chose the other way: theirs.
        draft = app.drafts.create(text="Write Ada a note", collect=False)
        app.drafts.collect_and_plan(draft["id"])
        app.drafts.start(draft["id"], folders=[], plan=dict(PLAN, way="full"))
        self.assertEqual(started[1]["way"], "full")
        self.assertEqual(app.drafts.get(draft["id"])["plan_by"], "user")


class WorksListSaysTheWayTests(unittest.TestCase):

    def test_its_latest_runs_way(self) -> None:
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        app = agent.app
        experience = app.state_heartbeat.experience
        unit = experience.store.create_work_project("Email to Ada")["unit_id"]
        for n, way in enumerate(("full", "quick")):
            app.db.execute(
                "INSERT INTO work_runs (id, unit_id, unit_name, folder, status, instruction, "
                "plan, log, final, started_at, ended_at, updated_at) VALUES (?, ?, 'x', '', "
                "'done', '', ?, '[]', '{}', ?, ?, ?)",
                (f"run_{n}", unit, json.dumps({"way": way}), f"2026-10-2{n}T10:00:00+00:00",
                 f"2026-10-2{n}T10:00:00+00:00", f"2026-10-2{n}T10:00:00+00:00"))
        row = next(one for one in experience.units() if one["id"] == unit)
        self.assertEqual(row["way"], "quick")


if __name__ == "__main__":
    unittest.main()
