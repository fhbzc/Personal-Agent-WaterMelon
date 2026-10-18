"""TRIES: the same instruction tried again, each try in its own subfolder of
the project's folder with its three ways inside, and each try blind to every
other - its ways may not read another try's folder or anything under it, nor
what Claude Code or Codex sessions started there kept of themselves."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from support import TemporaryAgent

from personalized_agent.agent.work import debug_projects as dp


class _Request:
    def __init__(self, body):
        self.body = body

    def get(self, key, default=None):
        return self.body.get(key, default)

    def require(self, key):
        return self.body[key]


class TriesTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        from personalized_agent.api.routes import build_routes
        self.routes = build_routes(self.app)
        self.chosen = Path(self.agent.dir) / "bench"
        self.chosen.mkdir()

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _post(self, body):
        return self.routes[("POST", "/api/debugger/experience/projects")](_Request(body))

    def _project(self):
        return self._post({"action": "create", "name": "Notes", "description": "plain",
                           "folder": str(self.chosen)})["project"]

    def test_a_new_try_is_its_own_folder_and_is_shown(self) -> None:
        first = self._project()
        self.assertEqual((first["try"], first["tries"]), (1, [1]))
        for way in ("A", "B", "C"):
            self.assertTrue((self.chosen / "try_1" / way).is_dir())
        second = self._post({"action": "new_try", "id": first["id"]})["project"]
        self.assertEqual((second["try"], second["tries"]), (2, [1, 2]))
        self.assertEqual(second["id"], f"{first['id']}~2")
        self.assertEqual(Path(second["try_folder"]), self.chosen / "try_2")
        self.assertEqual({Path(v["folder"]) for v in second["variants"]},
                         {self.chosen / "try_2" / way for way in ("A", "B", "C")})
        # The project list shows the try chosen; another can be chosen back.
        listed = self.routes[("GET", "/api/debugger/experience/projects")](_Request({}))
        self.assertEqual(listed["projects"][0]["id"], second["id"])
        back = self._post({"action": "use_try", "id": second["id"], "try": 1})["project"]
        self.assertEqual((back["id"], back["try"]), (first["id"], 1))

    def test_a_try_reads_no_other_try(self) -> None:
        from personalized_agent.agent.work.tools import WorkTools
        first = self._project()
        (self.chosen / "try_1" / "A" / "made.txt").write_text("try one's work")
        (self.chosen / "try_1" / "evaluation.md").write_text("try one is weak at X")
        (self.chosen / "input.csv").write_text("the user's own")
        second = self._post({"action": "new_try", "id": first["id"]})["project"]
        unit = dp.DebugProjects(self.app.work).unit(second["id"], "single")
        self.assertEqual(unit["id"], f"{second['id']}:single")
        runtime = self.app.work._runtime(unit, self.app.work.folder_of(unit))
        self.assertEqual(Path(runtime.policy.write_root).resolve(),
                         (self.chosen / "try_2" / "A").resolve())
        tools = WorkTools(runtime)
        for blocked in (self.chosen / "try_1" / "A" / "made.txt",
                        self.chosen / "try_1" / "evaluation.md"):
            said = tools.call("read_file", {"path": str(blocked)})
            self.assertEqual(said.get("error"), "security_violation", (blocked, said))
        said = tools.call("list_dir", {"path": str(self.chosen / "try_1")})
        self.assertEqual(said.get("error"), "security_violation", said)
        # The user's own beside the tries is still theirs to give.
        self.assertEqual(tools.call("read_file", {"path": str(self.chosen / "input.csv")})
                         ["text"], "the user's own")
        # And the first try is as blind to the second.
        (self.chosen / "try_2" / "B" / "later.txt").write_text("try two's")
        unit = dp.DebugProjects(self.app.work).unit(first["id"], "noctx")
        tools = WorkTools(self.app.work._runtime(unit, self.app.work.folder_of(unit)))
        said = tools.call("read_file", {"path": str(self.chosen / "try_2" / "B" / "later.txt")})
        self.assertEqual(said.get("error"), "security_violation", said)

    def test_and_the_operating_system_keeps_them_apart(self) -> None:
        from personalized_agent.runtime.isolation import isolation_status
        if isolation_status().get("backend") != "appcontainer":
            self.skipTest("no AppContainer isolation here")
        from personalized_agent.agent.work.tools import WorkTools
        first = self._project()
        (self.chosen / "try_1" / "evaluation.md").write_text("try one is weak at X")
        second = self._post({"action": "new_try", "id": first["id"]})["project"]
        unit = dp.DebugProjects(self.app.work).unit(second["id"], "single")
        tools = WorkTools(self.app.work._runtime(unit, self.app.work.folder_of(unit)))
        said = tools.call("run_python", {"code": (
            "import _io\n"
            f"path = r'{self.chosen / 'try_1' / 'evaluation.md'}'\n"
            "try:\n"
            "    print('READ', _io.FileIO(path).read().decode())\n"
            "except Exception as exc:\n"
            "    print('refused:', type(exc).__name__)\n")})
        out = str(said.get("stdout"))
        self.assertNotIn("READ", out, said)
        self.assertIn("refused", out, said)


class RemoveATryTests(TriesTests):
    """Remove this try: its folder and all in it, and the sessions of work
    in it, deleted; the other tries and the project's own folder as they
    are. Not the only try."""

    def test_its_folder_and_sessions_go_and_the_rest_stay(self) -> None:
        first = self._project()
        second = self._post({"action": "new_try", "id": first["id"]})["project"]
        third = self._post({"action": "new_try", "id": first["id"]})["project"]
        (self.chosen / "try_2" / "A" / "made.txt").write_text("try two's work")
        (self.chosen / "try_2" / "notes.md").write_text("what I thought of try two")
        (self.chosen / "input.csv").write_text("the user's own")
        session = Path(self.agent.dir) / "home" / ".claude" / "projects" / "bench-try-2"
        session.mkdir(parents=True)
        (session / "one.jsonl").write_text("{}")
        real = dp.sessions_under

        def sessions(roots, home=None):
            if [Path(one).name for one in roots] == ["try_2"]:
                return [str(session)]
            return real(roots, home)
        self._post({"action": "use_try", "id": second["id"], "try": 2})
        with mock.patch.object(dp, "sessions_under", side_effect=sessions):
            said = self._post({"action": "remove_try", "id": second["id"]})
        dp.wait_deleted()
        self.assertEqual(said["removed_try"], 2)
        self.assertEqual(said["sessions"], 1)
        self.assertFalse((self.chosen / "try_2").exists())
        self.assertFalse(session.exists())
        self.assertEqual([one.name for one in self.chosen.iterdir()
                          if one.name.startswith(".try_")], [])
        self.assertTrue((self.chosen / "try_1" / "A").is_dir())
        self.assertTrue((self.chosen / "try_3" / "A").is_dir())
        self.assertEqual((self.chosen / "input.csv").read_text(), "the user's own")
        # The one shown moves to a try that is still there.
        self.assertEqual((said["project"]["try"], said["project"]["tries"]), (3, [1, 3]))
        # The first can go too; the last one left cannot.
        said = self._post({"action": "remove_try", "id": first["id"]})
        dp.wait_deleted()
        self.assertEqual(said["project"]["tries"], [3])
        from personalized_agent.api.server import ApiError
        with self.assertRaises(ApiError):
            self._post({"action": "remove_try", "id": third["id"]})
        self.assertTrue((self.chosen / "try_3" / "A").is_dir())
        # A new try is numbered after the ones left.
        made = self._post({"action": "new_try", "id": first["id"]})["project"]
        self.assertEqual(made["tries"], [3, 4])

    def test_the_page_asks_first(self) -> None:
        script = (Path(__file__).resolve().parents[1] / "personalized_agent" / "api"
                  / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("data-xd-remove-try-yes", script)
        self.assertLess(script.index("xdState.removeTryAsk = asking"),
                        script.index('action: "remove_try"'))


class AssistantSessionsTests(unittest.TestCase):
    """What Claude Code and Codex kept of sessions started in another try."""

    def test_claude_code_and_codex_sessions_in_another_try_are_sealed(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as work:
            home = Path(work) / "home"
            bench = Path(work) / "bench"
            one, ten = bench / "try_1", bench / "try_10"
            projects = home / ".claude" / "projects"
            names = {"one": dp._claude_name(one), "one_sub": dp._claude_name(one / "A"),
                     "ten": dp._claude_name(ten)}
            for name in names.values():
                (projects / name).mkdir(parents=True)
            sessions = home / ".codex" / "sessions" / "2026" / "10" / "16"
            sessions.mkdir(parents=True)
            ran_in_one = sessions / "rollout-a.jsonl"
            ran_in_one.write_text(json.dumps({"type": "session_meta", "payload": {
                "cwd": str(one / "notes")}}) + "\n")
            elsewhere = sessions / "rollout-b.jsonl"
            elsewhere.write_text(json.dumps({"type": "session_meta", "payload": {
                "cwd": str(ten)}}) + "\n")
            with mock.patch.dict(os.environ, {"CODEX_HOME": ""}):
                sealed = dp.sessions_under([one], home=home)
            self.assertIn(str(projects / names["one"]), sealed)
            self.assertIn(str(projects / names["one_sub"]), sealed)
            self.assertNotIn(str(projects / names["ten"]), sealed, "try_10 is not try_1")
            self.assertIn(str(ran_in_one), sealed)
            self.assertNotIn(str(elsewhere), sealed)

    def test_they_are_in_what_a_way_may_not_read(self) -> None:
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        bench = Path(agent.dir) / "bench"
        bench.mkdir()
        projects = dp.DebugProjects(agent.app.work)
        made = projects.create("Notes", "plain", "", str(bench))["project"]
        projects.new_try(made["id"])
        held = Path(agent.dir) / "claude-session"
        with mock.patch.object(dp, "sessions_under", return_value=[str(held)]) as asked:
            unit = projects.unit(f"{made['id']}~2", "full")
        self.assertIn(str(held), unit["debug"]["sealed"])
        self.assertIn(str((bench / "try_1").resolve()),
                      [str(Path(one).resolve()) for one in unit["debug"]["sealed"]])
        # The other tries' sessions - and those of the agent's own data and
        # code (test_debugger_reads).
        asked_about = [[Path(one) for one in call[0][0]] for call in asked.call_args_list]
        self.assertIn(["try_1"], [[one.name for one in roots] for roots in asked_about])
        self.assertTrue(any(Path(agent.app.work.app_policy.workspace_root) in roots
                            for roots in asked_about))


class AProjectFromBeforeTriesTests(unittest.TestCase):

    def test_its_ways_move_into_the_first_try_with_their_runs(self) -> None:
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        bench = Path(agent.dir) / "bench"
        for way in ("A", "B", "C"):
            (bench / way).mkdir(parents=True)
        (bench / "C" / "made.txt").write_text("the full way's work")
        (bench / "input.csv").write_text("the user's own")
        db = agent.app.db
        db.execute("INSERT INTO debug_projects (id, name, description, instruction, folder, "
                   "created_at, updated_at) VALUES ('dbg_old', 'Old', '', 'do it', ?, "
                   "'2026-10-09', '2026-10-09')", (str(bench),))
        db.execute("INSERT INTO work_runs (id, unit_id, unit_name, folder, status, instruction, "
                   "plan, log, final, started_at, ended_at, updated_at) VALUES ('run_old', "
                   "'dbg_old:full', 'Old', ?, 'done', 'do it', '', '', ?, '2026-10-11', "
                   "'2026-10-12', '2026-10-12')",
                   (str(bench / "C"), f"wrote {bench / 'C' / 'made.txt'}"))
        shown = dp.DebugProjects(agent.app.work).project("dbg_old")
        self.assertEqual((shown["try"], shown["tries"]), (1, [1]))
        self.assertTrue((bench / "try_1" / "C" / "made.txt").is_file())
        self.assertFalse((bench / "C").exists())
        self.assertTrue((bench / "input.csv").is_file(), "the user's own stays where it was")
        row = db.query("SELECT folder, final FROM work_runs WHERE id = 'run_old'")[0]
        self.assertEqual(Path(row["folder"]), bench / "try_1" / "C")
        self.assertIn(str(bench / "try_1" / "C" / "made.txt"), row["final"])
        self.assertEqual(Path(dp.variant_folder_of(db, "dbg_old:full")), bench / "try_1" / "C")


class ThePanelTests(unittest.TestCase):

    def test_the_tries_row(self) -> None:
        script = (Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
                  / "app.js").read_text(encoding="utf-8")
        self.assertIn("function xdTries(project)", script)
        self.assertIn('{ action: "new_try", id: xdState.project.id }', script)
        self.assertIn('action: "use_try"', script)


if __name__ == "__main__":
    unittest.main()
