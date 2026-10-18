"""What a piece of work may read of the assistants' folders, and what a way
of the Work debugger may not read of the agent itself.

Every run may read the record of the user's Claude Code and Codex sessions
- a lesson can come from one - and none may read their sign-ins and
settings. A way of the debugger, which is measured, may not read the
agent's own code (the checkout, or an installed copy's whole folder), its
data folder's workspace, the sessions of work in either, nor the sessions
of the other tries of its project."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TheAssistantsFoldersTests(unittest.TestCase):

    def test_sessions_open_sign_ins_closed(self) -> None:
        from personalized_agent.runtime.isolation import credential_stores
        with tempfile.TemporaryDirectory() as work:
            home = Path(work)
            for one in (".claude/projects/C--garden", ".claude/plugins", ".codex/sessions/2026",
                        ".ssh"):
                (home / one).mkdir(parents=True)
            for one in (".claude/.credentials.json", ".claude/settings.json", ".codex/auth.json",
                        ".claude.json"):
                (home / one).write_text("x")
            hidden = {str(p.relative_to(home)).replace("\\", "/") for p in credential_stores(home)}
        self.assertIn(".claude/.credentials.json", hidden)
        self.assertIn(".claude/settings.json", hidden)
        self.assertIn(".claude/plugins", hidden)
        self.assertIn(".codex/auth.json", hidden)
        self.assertIn(".claude.json", hidden)
        self.assertIn(".ssh", hidden)
        self.assertNotIn(".claude", hidden)
        self.assertNotIn(".claude/projects", hidden)
        self.assertNotIn(".codex/sessions", hidden)


class AWaysReadsTests(unittest.TestCase):

    def test_sealed_from_the_agent_itself(self) -> None:
        from personalized_agent.agent.work import debug_projects as dp
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            home = root / "home"
            code = root / "checkout"
            agent_ws = root / "agent" / "workspace"
            project = root / "garden-project"
            for one in (code, agent_ws / "data", agent_ws / "agent_work", project / "try_1" / "A",
                        project / "try_2"):
                one.mkdir(parents=True)
            projects = home / ".claude" / "projects"
            for one in (dp._claude_name(code), dp._claude_name(project / "try_2"),
                        "C--somewhere-else"):
                (projects / one).mkdir(parents=True)
            owner = dp.DebugProjects.__new__(dp.DebugProjects)
            owner.work = mock.Mock(app_policy=mock.Mock(workspace_root=agent_ws))
            with mock.patch.object(dp, "_code_root", return_value=code), \
                    mock.patch("pathlib.Path.home", return_value=home):
                sealed = owner.sealed_for({"folder": str(project)}, 1, "single")
        sealed = {Path(one).resolve() for one in sealed}
        self.assertIn(agent_ws.resolve(), sealed)
        self.assertIn((projects / dp._claude_name(code)).resolve(), sealed)
        self.assertIn((projects / dp._claude_name(project / "try_2")).resolve(), sealed)
        self.assertNotIn((projects / "C--somewhere-else").resolve(), sealed,
                         "other sessions stay readable")

    def test_the_users_notes_on_the_tries_are_sealed(self) -> None:
        """What the user wrote at the top of a try - this one too - and the
        sessions of an evaluation run there; the top of the project's folder
        is the user's input, and stays readable."""
        from personalized_agent.agent.work import debug_projects as dp
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            home = root / "home"
            project = root / "garden-project"
            own = project / "try_2" / "A"
            for one in (own, project / "try_2" / "B", project / "try_1" / "A"):
                one.mkdir(parents=True)
            (project / "comparison.md").write_text("B was better")
            (project / "try_2" / "eval.md").write_text("A misses the point")
            projects = home / ".claude" / "projects"
            names = {"top": dp._claude_name(project), "try": dp._claude_name(project / "try_2"),
                     "own": dp._claude_name(own)}
            for one in names.values():
                (projects / one).mkdir(parents=True)
            owner = dp.DebugProjects.__new__(dp.DebugProjects)
            owner.work = mock.Mock(app_policy=mock.Mock(workspace_root=root / "nowhere"))
            with mock.patch.object(dp, "_code_root", return_value=root / "checkout"), \
                    mock.patch("pathlib.Path.home", return_value=home), \
                    mock.patch.dict("os.environ", {"CODEX_HOME": str(home / ".codex")}):
                sealed = {Path(one).resolve() for one in
                          owner.sealed_for({"folder": str(project)}, 2, "single")}
            for path in (project / "try_2" / "eval.md", project / "try_1",
                         project / "try_2" / "B", projects / names["try"]):
                self.assertIn(path.resolve(), sealed, path)
            # The top of the project's folder is the user's input: readable,
            # and so are sessions run there.
            for path in (own, project / "try_2", project, project / "comparison.md",
                         projects / names["own"], projects / names["top"]):
                self.assertNotIn(path.resolve(), sealed, path)

    def test_a_way_inside_the_workspace_keeps_its_own_folder(self) -> None:
        from personalized_agent.agent.work.debug_projects import _around
        with tempfile.TemporaryDirectory() as work:
            ws = Path(work) / "workspace"
            own = ws / "projects" / "garden" / "try_1" / "A"
            for one in (own, ws / "data", ws / "projects" / "other"):
                one.mkdir(parents=True)
            out = {p.resolve() for p in _around(ws, own)}
        self.assertIn((ws / "data").resolve(), out)
        self.assertIn((ws / "projects" / "other").resolve(), out)
        self.assertNotIn(ws.resolve(), out)
        self.assertFalse(any(own.resolve() == p or p in own.resolve().parents for p in out))


class AnInstalledCopysFolderTests(unittest.TestCase):

    def test_the_whole_folder_it_was_installed_in(self) -> None:
        from personalized_agent.common import paths
        with tempfile.TemporaryDirectory() as work:
            installed = Path(work) / "PersonalAgentWaterMelon"
            (installed / "_internal").mkdir(parents=True)
            exe = installed / "PersonalAgentWaterMelon.exe"
            exe.write_text("x")
            with mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(sys, "_MEIPASS", str(installed / "_internal"), create=True), \
                    mock.patch.object(sys, "executable", str(exe)):
                self.assertEqual(paths.app_code_root(), installed.resolve())


if __name__ == "__main__":
    unittest.main()
