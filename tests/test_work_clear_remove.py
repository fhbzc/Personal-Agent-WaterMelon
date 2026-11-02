"""CLEAR AND REMOVE (the user, 2026-10-31). Clear: what a project's work made,
in its own work folders - and only those, inside agent_work - moved to the
Recycle Bin; the project and every record stay. Remove: a project hidden
from Work (asked whether to clear too), and one the activity makes again of
mostly its tasks hidden as well; a try removed from its project, and from its
to-do. Nothing in the user's own folders is touched."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from support import TemporaryAgent
from test_work_panel import _PanelBase, _render

from personalized_agent.agent.work import runner as rn
from personalized_agent.platform_layer import trash


def _row(app, run_id, unit_id, folder, when="2026-10-31T10:00:00+00:00"):
    app.db.execute(
        "INSERT INTO work_runs (id, unit_id, unit_name, folder, status, instruction, plan, "
        "log, final, started_at, ended_at, updated_at) VALUES (?, ?, 'x', ?, 'done', 'do it', "
        "'{}', '[]', '{}', ?, ?, ?)", (run_id, unit_id, str(folder), when, when, when))


class ClearTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.unit = {"id": "work_fern", "name": "Repot the fern", "kind": "new project"}
        root = Path(self.app.work.app_policy.workspace_root) / rn.WORK_DIR
        self.own = self.app.work.folder_of(self.unit)
        self.v2 = root / "Repot the fern V2"
        self.mine = Path(self.agent.dir) / "my own folder"       # the user's, given it
        for folder in (self.own, self.v2, self.mine):
            (folder / "sub").mkdir(parents=True)
            (folder / "made.md").write_text("made", encoding="utf-8")
            (folder / "sub" / "more.txt").write_text("more", encoding="utf-8")
        _row(self.app, "run_v1", "work_fern", self.own)
        _row(self.app, "run_v2", "work_fern", self.v2, "2026-10-31T11:00:00+00:00")
        _row(self.app, "run_mine", "work_fern", self.mine, "2026-10-31T12:00:00+00:00")
        self.bin = Path(self.agent.dir) / "bin"
        self.bin.mkdir()

    def tearDown(self) -> None:
        self.app.work._running.clear()
        self.agent.cleanup()

    def _to_bin(self, path):
        shutil.move(str(path), str(self.bin / f"{len(list(self.bin.iterdir()))}-{path.name}"))

    def test_only_its_own_work_folders_inside_agent_work(self) -> None:
        folders = self.app.work.work_folders(self.unit)
        self.assertEqual(set(folders), {self.own.resolve(), self.v2.resolve()})
        self.assertNotIn(self.mine.resolve(), folders, "the user's own folder: never")
        said = self.app.work.clear_preview(self.unit)
        self.assertEqual(sorted((Path(one["path"]).name, one["files"]) for one in said["folders"]),
                         sorted([(self.own.name, 2), (self.v2.name, 2)]))
        self.assertEqual(said["busy"], "")

    def test_cleared_to_the_bin_and_every_record_kept(self) -> None:
        with mock.patch.object(trash, "move_to_trash", side_effect=self._to_bin):
            said = self.app.work.clear(self.unit)
        self.assertEqual(set(said["cleared"]), {str(self.own.resolve()), str(self.v2.resolve())})
        for folder in (self.own, self.v2):
            self.assertTrue(folder.is_dir(), "the folder stays, empty")
            self.assertEqual(list(folder.iterdir()), [])
        self.assertTrue((self.mine / "made.md").exists(), "the user's own folder untouched")
        self.assertEqual(len(list(self.bin.iterdir())), 4, "all of it in the bin")
        self.assertEqual({one["id"] for one in self.app.work.runs("work_fern")},
                         {"run_v1", "run_v2", "run_mine"}, "every record kept")

    def test_not_while_its_work_goes(self) -> None:
        self.app.work._running["run_v2"] = SimpleNamespace(alive=lambda: True)
        with mock.patch.object(trash, "move_to_trash", side_effect=AssertionError("moved")):
            self.assertIn("stop it first", self.app.work.clear(self.unit)["error"])
        self.assertTrue((self.v2 / "made.md").exists())

    def test_the_freedesktop_trash_keeps_where_it_was(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            gone = Path(home) / "gone.md"
            gone.write_text("x", encoding="utf-8")
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": home}):
                trash._freedesktop(gone)
            self.assertFalse(gone.exists())
            self.assertTrue((Path(home) / "Trash" / "files" / "gone.md").exists())
            info = (Path(home) / "Trash" / "info" / "gone.md.trashinfo").read_text(encoding="utf-8")
            self.assertIn("[Trash Info]", info)
            self.assertIn("gone.md", info)
            self.assertIn("DeletionDate=", info)


class RemovedStaysRemovedTests(unittest.TestCase):

    def test_a_project_made_again_of_mostly_its_tasks_is_hidden(self) -> None:
        from personalized_agent.heartbeat.experience.service import _made_again
        removed = {"project_old": {"tasks": ["t1", "t2", "t3"]}}
        again = {"id": "project_new", "tasks": [SimpleNamespace(id=one) for one in ("t1", "t2", "t9")]}
        other = {"id": "project_other", "tasks": [SimpleNamespace(id=one) for one in ("t1", "t8", "t9")]}
        self.assertTrue(_made_again(again, removed), "two of its three tasks: the one removed")
        self.assertFalse(_made_again(other, removed), "one of three: work of its own")
        self.assertFalse(_made_again({"id": "folder_1", "tasks": []}, removed))


class ATryRemovedTests(unittest.TestCase):

    def test_from_its_project_and_its_to_do_never_the_project(self) -> None:
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        app = agent.app
        from test_delegate import PLAN, _Models
        app.models = _Models(PLAN)
        ids = iter(["run_v1", "run_v2"])
        app.work.start = lambda unit, instruction, **kw: {"run": next(ids)}
        for _ in range(2):
            draft = app.drafts.create(text="Repot the fern", source="todo",
                                      options={"todo": "todo_fern"}, collect=False)
            app.drafts.collect_and_plan(draft["id"])
            unit = app.drafts.start(draft["id"], folders=[])["unit"]
        _row(app, "run_v1", unit, Path(agent.dir) / "v1")
        _row(app, "run_v2", unit, Path(agent.dir) / "v2", "2026-10-31T11:00:00+00:00")
        self.assertTrue(app.work.hide_run("run_v2"))
        app.drafts.forget_run("run_v2")
        self.assertEqual([one["id"] for one in app.work.runs(unit)], ["run_v1"])
        self.assertEqual([one["version"] for one in app.drafts.of_todos()["todo_fern"]["versions"]],
                         [1])
        self.assertIn(unit, [one["id"] for one in app.state_heartbeat.experience.units()],
                      "the project stays")


class ThePageAsksFirstTests(_PanelBase):

    def test_clear_lists_what_goes_and_remove_may_clear_too(self) -> None:
        run = {"id": "run_1", "folder": "C:/w/agent_work/fern", "alive": False, "status": "done",
               "continuable": False, "paused": False, "started_at": "2026-10-31T10:00:00",
               "instruction": "repot it", "final": {}, "log": [], "versions": {"count": 0},
               "plan": {"packages": {"mode": "ask", "requests": []}}}
        stub = {"/api/work/runs": {"runs": [run]},
                "/api/work/folders": {"folders": [], "warning": ""},
                "/api/work/limits": {"defaults": {}},
                "/api/work/tree": {"cards": [], "stages": [], "edges": []},
                "/api/work/clear": {"folders": [{"path": "C:/w/agent_work/fern", "files": 3,
                                                 "bytes": 2048}], "busy": ""},
                "/api/experience": {"units": [{"id": "work_fern", "name": "Repot the fern",
                                               "kind": "new project", "stages": 0}],
                                    "learning": 1},
                "/api/experience/graph": {"unit": {"id": "work_fern", "name": "Repot the fern",
                                                   "kind": "new project"}, "stages": []}}
        steps = (
            "experienceState.unit='work_fern';await loadExperience(false);await wait(1200);"
            "click(document.querySelector('[data-xp-clear]'));await wait(600);"
            "out.asked=text(document.querySelector('.xp-unit-ask'));"
            "click(document.querySelector('[data-xp-clear-yes]'));await wait(800);"
            "out.cleared=window.__posts.map(p=>p.p);window.__posts.length=0;"
            "click(document.querySelector('[data-xp-remove]'));await wait(400);"
            "document.querySelector('[data-xp-remove-clear]').checked=true;"
            "click(document.querySelector('[data-xp-remove-yes]'));await wait(800);"
            "out.removed=window.__posts.map(p=>p.p);window.__posts.length=0;"
            "experienceState.unit='work_fern';await xpLoadRuns(true);await wait(400);"
            "click(document.querySelector('[data-xp-run-remove]'));await wait(600);"
            "click(document.querySelector('[data-xp-run-remove-yes]'));await wait(800);"
            "out.tryRemoved=window.__posts.map(p=>[p.p,p.body.run]);")
        result = _render(self.engine, stub, steps)
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        self.assertIn("moved to the Recycle Bin", out["asked"])
        self.assertIn("C:/w/agent_work/fern", out["asked"])
        self.assertIn("3 files, 2 KB", out["asked"])
        self.assertIn("Your own folders are not touched", out["asked"])
        self.assertEqual(out["cleared"], ["/api/work/clear"])
        self.assertEqual(out["removed"], ["/api/work/clear", "/api/experience/remove"],
                         "cleared first, then removed")
        self.assertIn(["/api/work/runs/remove", "run_1"], out["tryRemoved"])


if __name__ == "__main__":
    unittest.main()
