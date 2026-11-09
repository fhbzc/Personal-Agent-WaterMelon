"""A to-do handed over, and Work's list of projects.

WHAT CAME OF A TO-DO: Delegate work on a to-do keeps the to-do's id with
the work, so the to-do says what came of it - Ongoing while its plan is
drafted and while the agent works, then "Open the result" on what the work
made (a file that would run as a program is shown in its folder, never
opened). WORK'S PROJECTS: none of leisure, and the agent's own work on one
counts as activity - work just delegated is at the top, not the bottom."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from support import TemporaryAgent
from test_delegate import PLAN, _Models

from personalized_agent.agent.work.delegate import asked_of, runs_as_program
from personalized_agent.heartbeat.experience.service import is_leisure


def _run(app, run_id, unit_id, folder, status, when, deliverables=()):
    plan = {"deck": {"deliverables": [{"name": Path(one).name, "location": str(one)}
                                      for one in deliverables]}}
    app.db.execute(
        "INSERT INTO work_runs (id, unit_id, unit_name, folder, status, instruction, plan, "
        "log, final, started_at, ended_at, updated_at) VALUES (?, ?, 'x', ?, ?, '', ?, "
        "'[]', '{}', ?, ?, ?)",
        (run_id, unit_id, str(folder), status, json.dumps(plan), when, when, when))


class _Alive:
    status = "running"

    def alive(self):
        return True


class WhatCameOfATodoTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.app.models = _Models(PLAN)
        self.folder = Path(self.agent.dir) / "errand"
        self.folder.mkdir()
        self.app.work.start = lambda unit, instruction, **kw: {"run": "run_first"}

    def tearDown(self) -> None:
        self.app.work._running.clear()
        self.agent.cleanup()

    def _v(self, n=0):
        return self.app.drafts.of_todos()["todo_fern"]["versions"][n]

    def test_ongoing_then_its_result(self) -> None:
        drafts = self.app.drafts
        draft = drafts.create(text="Repot the fern", source="todo",
                              options={"todo": "todo_fern"}, collect=False)
        self.assertEqual(drafts.of_todos(), {"todo_fern": {"versions": [
            {"draft": draft["id"], "version": 1, "state": "drafting"}], "next": 1}})
        drafts.collect_and_plan(draft["id"])
        unit = drafts.start(draft["id"], folders=[str(self.folder)])["unit"]
        self.assertEqual(self._v()["state"], "ongoing")
        # At work: its run going this moment.
        _run(self.app, "run_1", unit, self.folder, "not finished", "2026-10-26T10:00:00+00:00")
        self.app.work._running["run_1"] = _Alive()
        self.assertEqual(self._v()["state"], "ongoing")
        self.app.work._running.clear()
        self.assertEqual(self._v()["state"], "stopped")
        # Stopped cleanly: Continue, from where it stopped.
        self.assertEqual((self._v()["continuable"], self._v()["goes_back"]), (True, False))
        # Failed: Resume - back to its last save point (the user, 2026-10-26).
        _run(self.app, "run_f", unit, self.folder, "failed", "2026-10-26T10:30:00+00:00")
        self.assertEqual((self._v()["state"], self._v()["continuable"], self._v()["goes_back"]),
                         ("stopped", True, True))
        # Finished: what it made, where it is.
        made = self.folder / "fern-plan.md"
        made.write_text("Water weekly.", encoding="utf-8")
        _run(self.app, "run_2", unit, self.folder, "done", "2026-10-26T11:00:00+00:00",
             deliverables=[self.folder / "gone.md", made])
        said = self._v()
        self.assertEqual((said["state"], said["run"], said["result"]),
                         ("finished", "run_2", str(made)))
        # What it is, before it is opened.
        self.assertEqual((said["result_kind"], said["result_name"]), ("file", "fern-plan.md"))

    def test_handed_over_again_each_time_a_try_of_the_same_work(self) -> None:
        drafts = self.app.drafts
        units, started = [], []
        self.app.work.start = lambda unit, instruction, **kw: (
            started.append(kw) or {"run": f"run_{len(started)}"})
        for n in range(3):
            draft = drafts.create(text="Repot the fern", source="todo",
                                  options={"todo": "todo_fern"}, collect=False)
            drafts.collect_and_plan(draft["id"])
            if n == 1:
                drafts.cancel(draft["id"])      # not a version: as never handed over
                continue
            units.append(drafts.start(draft["id"], folders=[])["unit"])
        # One more, its plan not started yet: the next version.
        draft = drafts.create(text="Repot the fern", source="todo",
                              options={"todo": "todo_fern"}, collect=False)
        versions = drafts.of_todos()["todo_fern"]["versions"]
        self.assertEqual([(one["version"], one["state"]) for one in versions],
                         [(1, "ongoing"), (2, "ongoing"), (3, "drafting")])
        # ONE PROJECT (the user, 2026-10-31): the second a try of the first's
        # work - its name unchanged - in a folder and a tree of its own.
        self.assertEqual(units[0], units[1])
        name = self.app.state_heartbeat.experience.unit(units[0])["name"]
        self.assertFalse(name.endswith(")"), name)
        self.assertEqual(started[0].get("attempt"), 1)
        self.assertNotIn("folder", started[0], "the first try: the work's own folder")
        self.assertEqual((started[1]["attempt"], started[1]["tree"]), (2, "v2"))
        self.assertEqual(Path(started[1]["folder"]).name, f"{name} V2")

    def test_a_try_opens_with_the_words_and_context_of_the_one_before(self) -> None:
        drafts = self.app.drafts
        first = drafts.create(text="Repot the fern", source="todo",
                              options={"todo": "todo_fern",
                                       "instruction": "Find a bigger pot"}, collect=False)
        drafts.collect_and_plan(first["id"])
        drafts.start(first["id"], folders=[str(self.folder)])
        kept = drafts.get(first["id"])["context"]
        second = drafts.create(text="Repot the fern", source="todo",
                               options={"todo": "todo_fern"}, collect=False, write_first=True)
        self.assertEqual(second["options"]["instruction"], "Find a bigger pot",
                         "the box opens with the try before's words")
        self.assertEqual(second["folders"], [str(self.folder)])
        # Drafted here, not on a thread of its own beside this test's own call.
        with mock.patch.object(drafts, "_collect_apart"):
            drafts.compose(second["id"], "Find a clay pot instead")
        # WHAT WAS HANDED OVER IS TAKEN AS IT WAS; THE USER'S RECORDS ARE
        # SEARCHED AGAIN (the user, 2026-10-31: to see what happened since),
        # by this try's own words.
        searched = []

        def found(draft, context):
            searched.append(asked_of(draft))
            return dict(context, query={"keywords": ["clay"]},
                        found=[{"kind": "state", "title": "The fern was repotted on Friday"}])
        with mock.patch.object(drafts, "collect", side_effect=AssertionError("collected again")), \
                mock.patch.object(drafts, "_found", side_effect=found):
            drafts.collect_and_plan(second["id"])
        again = drafts.get(second["id"])
        self.assertEqual(searched, ["Find a clay pot instead"])
        anew = ("query", "found", "summary", "tries", "people", "me")
        self.assertEqual({k: v for k, v in again["context"].items() if k not in anew},
                         {k: v for k, v in kept.items() if k not in anew},
                         "what was handed over, as it was collected")
        self.assertEqual([one["title"] for one in again["context"]["found"]],
                         ["The fern was repotted on Friday"], "found again")
        self.assertEqual(again["status"], "ready")

    def test_each_try_says_where_its_own_run_stands(self) -> None:
        drafts = self.app.drafts
        ids = iter(["run_v1", "run_v2"])
        self.app.work.start = lambda unit, instruction, **kw: {"run": next(ids)}
        for _ in range(2):
            draft = drafts.create(text="Repot the fern", source="todo",
                                  options={"todo": "todo_fern"}, collect=False)
            drafts.collect_and_plan(draft["id"])
            unit = drafts.start(draft["id"], folders=[str(self.folder)])["unit"]
        _run(self.app, "run_v1", unit, self.folder, "done", "2026-10-31T10:00:00+00:00")
        _run(self.app, "run_v2", unit, self.folder / "v2", "not finished",
             "2026-10-31T11:00:00+00:00")
        self.app.db.execute("UPDATE work_runs SET plan = ? WHERE id = 'run_v2'",
                            (json.dumps({"tree": "v2", "attempt": 2}),))
        self.app.work._running["run_v2"] = _Alive()
        self.assertEqual(self._v(0)["state"], "finished", "the first, finished")
        self.assertEqual(self._v(1)["state"], "ongoing", "the second, going")
        self.assertEqual((self._v(0)["run"], self._v(1)["run"]), ("run_v1", "run_v2"))

    def test_a_new_try_is_told_what_the_tries_before_made(self) -> None:
        drafts = self.app.drafts
        told, ids = [], iter(["run_v1", "run_v2", "run_v3"])
        self.app.work.start = lambda unit, instruction, **kw: (
            told.append(instruction) or {"run": next(ids)})
        first = drafts.create(text="Repot the fern", source="todo",
                              options={"todo": "todo_fern", "instruction": "Find a bigger pot"},
                              collect=False)
        drafts.collect_and_plan(first["id"])
        unit = drafts.start(first["id"], folders=[str(self.folder)])["unit"]
        _run(self.app, "run_v1", unit, self.folder, "done", "2026-10-31T10:00:00+00:00")
        self.app.db.execute("UPDATE work_runs SET final = ? WHERE id = 'run_v1'",
                            (json.dumps({"answer": "The fern needs a 20 cm clay pot."}),))
        self.assertNotIn("TRIED BEFORE", told[0], "the first try: none before it")
        second = drafts.create(text="Repot the fern", source="todo",
                               options={"todo": "todo_fern"}, collect=False, write_first=True)
        # Drafted here, not on a thread of its own beside this test's own call.
        with mock.patch.object(drafts, "_collect_apart"):
            drafts.compose(second["id"], "Order the pot")
        drafts.collect_and_plan(second["id"])
        # The plan starts from them too.
        self.assertEqual([one["version"] for one in drafts.get(second["id"])["context"]["tries"]],
                         [1])
        drafts.start(second["id"], folders=[str(self.folder)])
        self.assertIn("THIS WORK WAS TRIED BEFORE", told[1])
        self.assertIn("V1 (finished", told[1])
        self.assertIn("asked: Find a bigger pot", told[1])
        self.assertIn("it reported: The fern needs a 20 cm clay pot.", told[1])
        self.assertIn(f"its folder: {self.folder}", told[1])
        self.assertIn("this try follows its own instruction", told[1])
        # Unless the user said not: the tries apart.
        third = drafts.create(text="Repot the fern", source="todo",
                              options={"todo": "todo_fern"}, collect=False, write_first=True)
        # Drafted here, not on a thread of its own beside this test's own call.
        with mock.patch.object(drafts, "_collect_apart"):
            drafts.compose(third["id"], "Try a hanging basket")
        drafts.collect_and_plan(third["id"])
        drafts.start(third["id"], folders=[str(self.folder)], options={"see_tries": False})
        self.assertNotIn("TRIED BEFORE", told[2])

    def test_a_projects_step_and_its_whole_are_told_what_came_before(self) -> None:
        """The user, 2026-11-08: a step handed over again may build on its first try - not must -
        and is whole in its own folder; the whole project may use every step's and its own
        earlier versions."""
        drafts = self.app.drafts
        told, ids, kept = [], iter(["run_s1", "run_s2", "run_w1"]), []
        self.app.work.start = lambda unit, instruction, **kw: (
            told.append(instruction) or kept.append(kw) or {"run": next(ids)})
        workspace = Path(self.app.work.app_policy.workspace_root) / "agent_work"
        step = {"project": "p-harbour", "step": "3", "under": "Harbour survey"}
        first = drafts.create(text="Harbour survey: count the boats", source="project",
                              options=dict(step), collect=False)
        drafts.collect_and_plan(first["id"])
        unit = drafts.start(first["id"], folders=[])["unit"]
        _run(self.app, "run_s1", unit, workspace / "Harbour survey" / "x", "done",
             "2026-11-08T10:00:00+00:00")
        self.app.db.execute("UPDATE work_runs SET final = ? WHERE id = 'run_s1'",
                            (json.dumps({"answer": "Forty-one boats at the pier."}),))
        self.assertNotIn("TRIED BEFORE", told[0])
        second = drafts.create(text="Harbour survey: count the boats", source="project",
                               options=dict(step), collect=False)
        drafts.collect_and_plan(second["id"])
        self.assertEqual(len(drafts.get(second["id"])["context"]["tries"]), 1, "the plan too")
        said = drafts.start(second["id"], folders=[])
        self.assertIn("THIS WORK WAS TRIED BEFORE", told[1])
        self.assertIn("one step of the project", told[1])
        self.assertIn("it reported: Forty-one boats at the pier.", told[1])
        self.assertIn("MAY use or carry on from", told[1])
        self.assertIn("WHATEVER THIS TRY DELIVERS IS WHOLE IN ITS OWN FOLDER", told[1])
        folder = self.app.work.folder_of(self.app.state_heartbeat.experience.unit(said["unit"]))
        self.assertEqual(folder.parent, workspace / "Harbour survey")
        self.assertTrue(folder.name.endswith("(V2)"), folder.name)
        whole = drafts.create(text="Harbour survey: every step left", source="project",
                              options={"project": "p-harbour", "whole": True,
                                       "under": "Harbour survey",
                                       "read_from": [str(self.folder)]}, collect=False)
        drafts.collect_and_plan(whole["id"])
        drafts.start(whole["id"], folders=[])
        self.assertIn("one step of the project", told[2], "the whole is told its steps' tries")
        # The project's own folders, to read and never write; a file too big to copy, pointed to.
        self.assertIn("THE PROJECT'S OWN FOLDERS", told[2])
        self.assertIn(f"  - {self.folder}", told[2])
        self.assertIn("NEVER write, change or delete anything in", told[2])
        self.assertIn("too big to copy conveniently", told[2])
        self.assertIn(f"its folder: {workspace / 'Harbour survey' / 'x'}", told[2],
                      "and where each step's work is, to read and copy from")
        folder = self.app.work.folder_of(self.app.state_heartbeat.experience.unit(
            drafts.get(whole["id"])["unit_id"]))
        self.assertFalse(folder.name.endswith(")"), "its first version: no number")

    def test_a_try_removed_still_gives_the_next_its_words(self) -> None:
        drafts = self.app.drafts
        ids = iter(["run_v1", "run_v2", "run_v3", "run_v4"])
        started = []
        self.app.work.start = lambda unit, instruction, **kw: (
            started.append(unit["id"]) or {"run": next(ids)})
        made = []
        for words in ("Find a bigger pot", "Order the clay pot"):
            draft = drafts.create(text="Repot the fern", source="todo",
                                  options={"todo": "todo_fern", "instruction": words},
                                  collect=False)
            drafts.collect_and_plan(draft["id"])
            made.append(draft["id"])
            unit = drafts.start(draft["id"], folders=[])["unit"]
        _run(self.app, "run_v1", unit, self.folder, "done", "2026-10-31T10:00:00+00:00")
        _run(self.app, "run_v2", unit, self.folder, "done", "2026-10-31T11:00:00+00:00")
        # The second removed: the next still opens with ITS words.
        self.assertEqual(drafts.remove(made[1]), {"removed": made[1]})
        third = drafts.create(text="Repot the fern", source="todo",
                              options={"todo": "todo_fern"}, collect=False, write_first=True)
        self.assertEqual(third["options"]["instruction"], "Order the clay pot")
        # Every try removed, and with the last its project: the words still,
        # in a project of its own - never one the user no longer sees.
        self.assertEqual(drafts.remove(made[0]), {"removed": made[0]})
        fourth = drafts.create(text="Repot the fern", source="todo",
                               options={"todo": "todo_fern"}, collect=False, write_first=True)
        self.assertEqual(fourth["options"]["instruction"], "Order the clay pot")
        with mock.patch.object(drafts, "_collect_apart"):
            drafts.compose(fourth["id"], "Order the clay pot")
        drafts.collect_and_plan(fourth["id"])
        drafts.start(fourth["id"], folders=[])
        self.assertNotEqual(started[-1], unit, "a project of its own")

    def test_one_try_removed_its_work_stays_for_the_other(self) -> None:
        drafts = self.app.drafts
        ids = iter(["run_v1", "run_v2"])
        self.app.work.start = lambda unit, instruction, **kw: {"run": next(ids)}
        made = []
        for _ in range(2):
            draft = drafts.create(text="Repot the fern", source="todo",
                                  options={"todo": "todo_fern"}, collect=False)
            drafts.collect_and_plan(draft["id"])
            made.append(draft["id"])
            unit = drafts.start(draft["id"], folders=[str(self.folder)])["unit"]
        _run(self.app, "run_v1", unit, self.folder, "done", "2026-10-31T10:00:00+00:00")
        _run(self.app, "run_v2", unit, self.folder, "done", "2026-10-31T11:00:00+00:00")
        self.assertEqual(drafts.remove(made[0]), {"removed": made[0]})
        self.assertIn(unit, [one["id"] for one in self.app.state_heartbeat.experience.units()],
                      "the work stays for the other try")
        self.assertEqual([one["id"] for one in self.app.work.runs(unit)], ["run_v2"],
                         "the removed try's run is not listed")
        self.assertEqual([one["version"] for one in drafts.of_todos()["todo_fern"]["versions"]],
                         [2])

    def test_a_result_removed_is_not_shown_and_its_files_are_kept(self) -> None:
        drafts = self.app.drafts
        draft = drafts.create(text="Repot the fern", source="todo",
                              options={"todo": "todo_fern"}, collect=False)
        drafts.collect_and_plan(draft["id"])
        unit = drafts.start(draft["id"], folders=[str(self.folder)])["unit"]
        made = self.folder / "fern-plan.md"
        made.write_text("Water weekly.", encoding="utf-8")
        # Not while it goes.
        _run(self.app, "run_1", unit, self.folder, "not finished", "2026-10-26T10:00:00+00:00")
        self.app.work._running["run_1"] = _Alive()
        self.assertIn("stop it first", drafts.remove(draft["id"])["error"])
        self.app.work._running.clear()
        _run(self.app, "run_2", unit, self.folder, "done", "2026-10-26T11:00:00+00:00",
             deliverables=[made])
        self.assertIn(unit, [one["id"] for one in
                             self.app.state_heartbeat.experience.units()])
        self.assertEqual(drafts.remove(draft["id"]), {"removed": draft["id"]})
        self.assertEqual(drafts.of_todos(), {})
        self.assertNotIn(unit, [one["id"] for one in
                                self.app.state_heartbeat.experience.units()])
        self.assertEqual(made.read_text(encoding="utf-8"), "Water weekly.", "files kept")
        # A saved draft removed: not among the drafts.
        other = drafts.create(text="Buy soil", collect=False, write_first=True)
        drafts.save(other["id"])
        drafts.remove(other["id"])
        self.assertEqual(drafts.saved(), [])

    def test_cancelled_is_as_never_handed_over(self) -> None:
        draft = self.app.drafts.create(text="Repot the fern", source="todo",
                                       options={"todo": "todo_fern"}, collect=False)
        self.app.drafts.cancel(draft["id"])
        self.assertEqual(self.app.drafts.of_todos(), {})

    def test_the_route_says_it_with_each_todo(self) -> None:
        from personalized_agent.api.routes import build_routes
        todo = mock.MagicMock()
        todo.shown.return_value = [{"id": "todo_fern", "text": "Repot the fern"},
                                   {"id": "todo_tea", "text": "Buy tea"}]
        self.app.todos = todo
        self.app.drafts.create(text="Repot the fern", source="todo",
                               options={"todo": "todo_fern"}, collect=False)
        said = build_routes(self.app)[("GET", "/api/todos")](None)["todos"]
        self.assertEqual(said[0]["work"]["versions"][0]["state"], "drafting")
        self.assertNotIn("work", said[1])

    def test_what_would_run_is_never_opened(self) -> None:
        for name in ("setup.exe", "go.BAT", "run.cmd", "tool.ps1", "page.hta", "x.lnk"):
            self.assertTrue(runs_as_program(Path(name)), name)
        for name in ("plan.md", "slides.pdf", "table.xlsx", "page.html"):
            self.assertFalse(runs_as_program(Path(name)), name)


class WorksProjectsTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.experience = self.app.state_heartbeat.experience

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_leisure_is_no_work(self) -> None:
        def unit(*kinds):
            return {"tasks": [SimpleNamespace(described={"kind": kind} if kind else {})
                              for kind in kinds]}
        self.assertTrue(is_leisure(unit("leisure", "leisure")))
        self.assertFalse(is_leisure(unit("leisure", "work")))
        self.assertFalse(is_leisure(unit(None)))
        self.assertFalse(is_leisure({"tasks": []}), "a folder or a project made is work")

    def test_left_out_of_the_list_but_its_name_found(self) -> None:
        task = lambda ident, kind: SimpleNamespace(
            id=ident, described={"kind": kind, "title": ident},
            last_seen="2026-10-26T09:00:00+00:00")
        shown = [{"id": "garden", "kind": "task", "name": "Garden",
                  "tasks": [task("garden", "leisure")]},
                 {"id": "paper", "kind": "task", "name": "Paper",
                  "tasks": [task("paper", "work")]}]
        writer = SimpleNamespace(pending=lambda unit: [])
        with mock.patch.object(self.experience, "_shown_units", return_value=shown), \
                mock.patch.object(self.experience, "stages_writer", return_value=writer):
            self.assertEqual([one["id"] for one in self.experience.units()], ["paper"])
            self.assertEqual({one["id"] for one in self.experience.units(leisure=True)},
                             {"garden", "paper"})

    def test_work_just_delegated_is_at_the_top(self) -> None:
        store = self.experience.store
        older = store.create_work_project("Older")["unit_id"]
        handed = store.create_work_project("Handed over")["unit_id"]
        store.mark_delegated(handed, "todo")
        _run(self.app, "run_old", older, self.agent.dir, "done", "2026-10-20T10:00:00+00:00")
        _run(self.app, "run_new", handed, self.agent.dir, "not finished",
             "2026-10-26T10:00:00+00:00")
        units = self.experience.units()
        self.assertEqual([one["id"] for one in units][:2], [handed, older])
        self.assertTrue(units[0]["delegated"])
        self.assertFalse(units[1]["delegated"])


if __name__ == "__main__":
    unittest.main()
