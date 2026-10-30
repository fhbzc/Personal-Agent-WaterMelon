"""CODEX FOUND AGAIN WITHOUT A RESTART, AND SAID IN THE LOG (the user,
2026-10-29): after "Refresh models" Codex still could not be reached, and
quitting the agent brought it back - while the log said nothing of Codex at
all. Now Refresh models lets go of everything kept of it, as a restart
would, and looks for it on the PATH as the system has it now first; and
where it was found, whether it is signed in and in how long, whether its
options could be read and whether its calls answer are said in the log,
each once, when it changes."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401

from personalized_agent.models.providers import codex_provider
from personalized_agent.models.providers.codex_provider import CodexProvider
from personalized_agent.platform_layer import programs

NAME = "codex.exe" if os.name == "nt" else "codex"


class WhereAProgramIsFoundTests(unittest.TestCase):

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="pa-found-"))
        self.started, self.now = self.dir / "started", self.dir / "now"
        for folder in (self.started, self.now):
            folder.mkdir()
            program = folder / NAME
            program.write_bytes(b"")
            program.chmod(0o755)

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def _where(self, process_path, now_path, usual=(), **kwargs):
        with mock.patch.dict(os.environ, {"PATH": str(process_path)}), \
                mock.patch.object(programs, "current_path", return_value=str(now_path)), \
                mock.patch.object(programs, "usual_places", return_value=list(usual)):
            return programs.find_program_where("codex", **kwargs)

    def test_this_process_s_path_first_and_the_path_now_after(self) -> None:
        found, how = self._where(self.started, self.now)
        self.assertEqual((Path(found).parent, how), (self.started, "this process's PATH"))
        found, how = self._where(self.dir / "nowhere", self.now)
        self.assertEqual((Path(found).parent, how),
                         (self.now, "the PATH as the system has it now"))
        found, how = self._where(self.dir / "nowhere", self.now, usual=[str(self.now)])
        self.assertEqual(how, "where such programs are put")
        self.assertEqual(self._where(self.dir / "nowhere", self.dir / "nor here"), (None, ""))

    def test_looked_for_again_the_path_now_comes_first(self) -> None:
        found, how = self._where(self.started, self.now, fresh=True)
        self.assertEqual((Path(found).parent, how),
                         (self.now, "the PATH as the system has it now"))


class RefreshModelsIsARestartForCodexTests(unittest.TestCase):

    def _provider(self) -> CodexProvider:
        return CodexProvider()

    def test_everything_kept_is_let_go_and_it_is_looked_for_afresh(self) -> None:
        provider = self._provider()
        provider._status_cache = {"logged_in": False}
        provider._status_checked_at = 1e12
        provider._shortened = {"old": (1, "short")}
        provider._confined = {("old", 0, "m"): (False, "")}
        provider._help = (("old", 0), "")
        asked = []

        def where(name, extra=(), fresh=False):
            asked.append(fresh)
            return None, ""
        with mock.patch("personalized_agent.platform_layer.programs.find_program_where",
                        side_effect=where):
            with self.assertRaises(RuntimeError):
                provider.refresh_models()
            provider.executable()
        self.assertEqual(asked, [True, False], "afresh once, then as before")
        self.assertIsNone(provider._status_cache)
        self.assertEqual((provider._shortened, provider._confined, provider._help),
                         ({}, {}, None))

    def test_the_route_asks_settings_again_when_the_refresh_fails(self) -> None:
        source = Path(codex_provider.__file__).parents[2] / "api" / "routes.py"
        text = source.read_text(encoding="utf-8")
        start = text.index("def refresh_codex_models")
        body = text[start:text.index("\n    @route", start)]
        self.assertEqual(body.count("app.models.invalidate()"), 2)


class SaidInTheLogOnceWhenItChangesTests(unittest.TestCase):

    def test_where_it_was_found(self) -> None:
        provider = CodexProvider()
        name = "personalized_agent.models.providers.codex_provider"
        with mock.patch("personalized_agent.platform_layer.programs.find_program_where",
                        return_value=(None, "")):
            with self.assertLogs(name, "INFO") as said:
                provider.executable()
                provider.executable()
        self.assertEqual(len(said.records), 1)
        self.assertIn("Codex not found", said.output[0])
        with mock.patch("personalized_agent.platform_layer.programs.find_program_where",
                        return_value=("/opt/codex/codex", "the PATH as the system has it now")), \
                mock.patch.object(provider, "_short", return_value=None):
            with self.assertLogs(name, "INFO") as said:
                provider.executable()
                provider.executable()
        self.assertEqual(len(said.records), 1)
        self.assertIn("Codex found: /opt/codex/codex (on the PATH as the system has it now)",
                      said.output[0])

    def test_its_sign_in_and_how_long_it_took(self) -> None:
        provider = CodexProvider()
        name = "personalized_agent.models.providers.codex_provider"
        answers = iter([{"logged_in": True, "detail": "Logged in using ChatGPT"},
                        {"logged_in": True, "detail": "Logged in using ChatGPT"},
                        {codex_provider.UNREACHABLE: "codex did not answer within 20s"},
                        {"logged_in": False, "detail": "Not logged in"}])
        with mock.patch.object(provider, "_login_status", side_effect=lambda cli: next(answers)):
            with self.assertLogs(name, "INFO") as said:
                for _ in range(4):
                    provider._ask_client("codex")
        lines = said.output
        self.assertEqual(len(lines), 3, lines)
        self.assertIn("Codex sign-in: signed in (", lines[0])
        self.assertIn("could not be checked after", lines[1])
        self.assertIn("did not answer within 20s", lines[1])
        self.assertIn("Codex sign-in: not signed in", lines[2])


if __name__ == "__main__":
    unittest.main()
