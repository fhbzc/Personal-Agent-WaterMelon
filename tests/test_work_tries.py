"""A to-do handed over again is a TRY of the same work (the user,
2026-10-31): another run of the first try's project, with a tree and a
folder of its own; the Work page folds every run, says which try each is,
and shows the tree of the try chosen."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from support import TemporaryAgent
from test_work_panel import _PanelBase, _render

from personalized_agent.agent.work import runner as rn


def _row(app, run_id, unit_id, folder, when, plan=None, status="done"):
    app.db.execute(
        "INSERT INTO work_runs (id, unit_id, unit_name, folder, status, instruction, plan, "
        "log, final, started_at, ended_at, updated_at) VALUES (?, ?, 'x', ?, ?, 'do it', ?, "
        "'[]', '{}', ?, ?, ?)",
        (run_id, unit_id, str(folder), status, json.dumps(plan or {}), when, when, when))


class EachTryItsOwnTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.folder = Path(self.agent.dir) / "fern"
        (self.folder / "v2").mkdir(parents=True)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_a_tries_tree_is_kept_beside_the_works(self) -> None:
        self.assertEqual(rn.work_scope("work_1"), "work:work_1")
        self.assertEqual(rn.work_scope("work_1", "v2"), "work:work_1:v2")
        self.assertEqual(rn.tree_of({"plan": {"tree": "v2"}}), "v2")
        self.assertEqual(rn.tree_of({"plan": {}}), "")

    def test_each_run_says_its_try_and_is_the_latest_of_its_own_tree(self) -> None:
        _row(self.app, "run_v1", "work_1", self.folder, "2026-10-31T10:00:00+00:00",
             {"attempt": 1})
        _row(self.app, "run_v2", "work_1", self.folder / "v2", "2026-10-31T11:00:00+00:00",
             {"attempt": 2, "tree": "v2"})
        latest = {}
        real = self.app.work.contract_in_force

        def seen(row, index, is_latest):
            latest[row["id"]] = is_latest
            return real(row, index, is_latest)
        with mock.patch.object(self.app.work, "contract_in_force", side_effect=seen):
            runs = self.app.work.runs("work_1")
        self.assertEqual([(one["id"], one["attempt"], one["tree"]) for one in runs],
                         [("run_v2", 2, "v2"), ("run_v1", 1, "")])
        self.assertEqual(latest, {"run_v2": True, "run_v1": True},
                         "each the latest of its own tree")

    def test_a_try_removed_is_not_listed_and_not_while_it_goes(self) -> None:
        _row(self.app, "run_v1", "work_1", self.folder, "2026-10-31T10:00:00+00:00")
        _row(self.app, "run_v2", "work_1", self.folder, "2026-10-31T11:00:00+00:00")

        class _Alive:
            def alive(self):
                return True
        self.app.work._running["run_v2"] = _Alive()
        try:
            self.assertFalse(self.app.work.hide_run("run_v2"), "not while it goes")
        finally:
            self.app.work._running.clear()
        self.assertTrue(self.app.work.hide_run("run_v1"))
        self.assertEqual([one["id"] for one in self.app.work.runs("work_1")], ["run_v2"])

    def test_a_later_tries_result_is_its_own_folder(self) -> None:
        made = self.folder / "v2" / "plan.md"
        made.write_text("x", encoding="utf-8")
        row = {"id": "run_v2", "unit_id": "work_1", "folder": str(self.folder / "v2"),
               "plan": {"attempt": 2, "tree": "v2"}}
        with mock.patch.object(self.app.work, "readable_by",
                               side_effect=lambda run, place: Path(place)):
            said = self.app.work.results_of(row)
        self.assertEqual([one["path"] for one in said], [str(self.folder / "v2")])

    def test_carried_on_thoroughly_a_try_stays_that_try(self) -> None:
        _row(self.app, "run_q", "work_1", self.folder / "v2", "2026-10-31T11:00:00+00:00",
             {"attempt": 2, "tree": "v2", "way": "quick"})
        asked = {}
        with mock.patch.object(self.app.work, "start",
                               side_effect=lambda unit, instruction, **kw: asked.update(kw)
                               or {"run": "run_t"}):
            self.app.work.thorough("run_q", {"id": "work_1", "name": "x"})
        self.assertEqual((asked["folder"], asked["tree"], asked["attempt"]),
                         (str(self.folder / "v2"), "v2", 2))


class TheWorkPageFoldsEachTryTests(_PanelBase):

    def _run(self, run_id, attempt, tree, when):
        return {"id": run_id, "folder": f"C:/w/agent_work/fern{attempt}", "alive": False,
                "status": "done", "continuable": False, "paused": False,
                "started_at": when, "final": {}, "log": [],
                # As work handed over is told it: the user's words, and much more.
                "instruction": (f"THE USER'S INSTRUCTION:\nwrite the note, try {attempt}\n\n"
                                "WHAT THE USER HANDED OVER:\nRepot the fern\n\n"
                                "REFERENCES FROM THE USER'S OWN RECORDS - " + "x" * 400)
                if attempt == 2 else "plant " * 40 + "and water it",
                "versions": {"count": 0}, "attempt": attempt, "tree": tree,
                "plan": {"attempt": attempt, "tree": tree,
                         "packages": {"mode": "ask", "requests": []}}}

    def test_folded_said_which_try_and_the_tree_chosen(self) -> None:
        stub = {"/api/work/runs": {"runs": [self._run("run_v2", 2, "v2", "2026-10-31T11:00:00"),
                                            self._run("run_v1", 1, "", "2026-10-31T10:00:00")]},
                "/api/work/folders": {"folders": [], "warning": ""},
                "/api/work/limits": {"defaults": {}},
                "/api/work/tree": {"cards": [], "stages": [], "edges": []},
                "/api/experience": {"units": [{"id": "work_fern", "name": "Repot the fern",
                                               "kind": "new project", "stages": 0}],
                                    "learning": 1},
                "/api/experience/graph": {"unit": {"id": "work_fern", "name": "Repot the fern",
                                                   "kind": "new project"}, "stages": []}}
        steps = (
            "experienceState.unit='work_fern';await loadExperience(false);await wait(1500);"
            "const boxes=[...document.querySelectorAll('.xp-run')];"
            "out.open=boxes.map(b=>b.open);"
            "out.summaries=boxes.map(b=>text(b.querySelector('summary')));"
            "const pick=document.querySelector('#xp-tree-try');"
            "out.trees=pick?[...pick.options].map(o=>o.textContent):[];"
            "out.firstTree=window.__gets.filter(u=>u.includes('/api/work/tree')).pop();"
            "pick.value='';pick.dispatchEvent(new Event('change',{bubbles:true}));await wait(1500);"
            "out.chosenTree=window.__gets.filter(u=>u.includes('/api/work/tree')).pop();"
            # The one a to-do's "See it in Work" asks for: opened.
            "experienceState.openRun='run_v1';await xpLoadRuns(true);await wait(600);"
            "out.asked=document.querySelector('[data-xp-run-box=\"run_v1\"]').open;"
            "out.other=document.querySelector('[data-xp-run-box=\"run_v2\"]').open;")
        result = _render(self.engine, stub, steps)
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        self.assertEqual(out["open"], [False, False], "every run folded by default")
        self.assertTrue(out["summaries"][0].startswith("V2"), out["summaries"])
        self.assertTrue(out["summaries"][1].startswith("V1"), out["summaries"])
        # THE LINE SAYS WHAT THE USER ASKED, IN SHORT (the user, 2026-10-31).
        self.assertIn("write the note, try 2", out["summaries"][0])
        self.assertNotIn("HANDED OVER", out["summaries"][0])
        self.assertNotIn("REFERENCES", out["summaries"][0])
        self.assertIn(" … ", out["summaries"][1], "a long one by its beginning and its end")
        self.assertTrue(out["summaries"][1].endswith("and water it"), out["summaries"][1])
        self.assertEqual(out["trees"], ["V2", "V1"])
        self.assertIn("tree=v2", out["firstTree"], "the newest try's tree first")
        self.assertNotIn("tree=", out["chosenTree"], "the first try's, chosen")
        self.assertTrue(out["asked"])
        self.assertFalse(out["other"])


if __name__ == "__main__":
    unittest.main()
