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

from personalized_agent.agent.work.delegate import runs_as_program
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
            {"draft": draft["id"], "version": 1, "state": "drafting"}]}})
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

    def test_handed_over_again_each_time_a_version(self) -> None:
        drafts = self.app.drafts
        names = []
        for n in range(3):
            draft = drafts.create(text="Repot the fern", source="todo",
                                  options={"todo": "todo_fern"}, collect=False)
            drafts.collect_and_plan(draft["id"])
            if n == 1:
                drafts.cancel(draft["id"])      # not a version: as never handed over
                continue
            unit = drafts.start(draft["id"], folders=[])["unit"]
            names.append(self.app.state_heartbeat.experience.unit(unit)["name"])
        # One more, its plan not started yet: the next version.
        draft = drafts.create(text="Repot the fern", source="todo",
                              options={"todo": "todo_fern"}, collect=False)
        versions = drafts.of_todos()["todo_fern"]["versions"]
        self.assertEqual([(one["version"], one["state"]) for one in versions],
                         [(1, "ongoing"), (2, "ongoing"), (3, "drafting")])
        self.assertFalse(names[0].endswith(")"))
        self.assertTrue(names[1].endswith("(V2)"), names)

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
