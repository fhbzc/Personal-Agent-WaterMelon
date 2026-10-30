"""Three things seen on a real machine, 2026-10-16.

A program installed after this application started - Codex, here - is
found: this process's PATH is the one it started with. A local client that is
not installed is not shown "ready" beside the model (it needs no sign-in,
which is not the same as being there). And a notification is said once: in
the bubble beside the avatar where there is one, not again as a card on the
page.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_connections_panel import _render
from test_panels import _javascript_engine

from personalized_agent.platform_layer import programs


class InstalledAfterItStartedTests(unittest.TestCase):

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        name = "tool-installed-later" + (".cmd" if sys.platform.startswith("win") else "")
        self.program = self.dir / name
        self.program.write_text("@echo off\n" if name.endswith(".cmd") else "#!/bin/sh\n",
                                encoding="utf-8")
        self.program.chmod(0o755)

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_found_on_the_path_as_it_is_now(self) -> None:
        with mock.patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")}), \
                mock.patch.object(programs, "current_path", lambda: str(self.dir)):
            self.assertIsNone(shutil.which("tool-installed-later"),
                              "this process's own PATH does not have it")
            found = programs.find_program("tool-installed-later")
        self.assertEqual(Path(found).resolve(), self.program.resolve())

    def test_and_what_it_starts_is_found_too(self) -> None:
        """A Codex installed with npm runs Node: installed together after the
        agent started, the Node must be on the PATH Codex is started with."""
        with mock.patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")}), \
                mock.patch.object(programs, "current_path", lambda: str(self.dir)):
            programs.find_program("tool-installed-later")
            self.assertIn(str(self.dir), os.environ["PATH"].split(os.pathsep))
            before = os.environ["PATH"]
            programs.find_program("tool-installed-later")
            self.assertEqual(os.environ["PATH"], before, "added once, not again")

    def test_or_where_it_is_put(self) -> None:
        with mock.patch.object(programs, "current_path", lambda: ""):
            self.assertEqual(programs.find_program("nowhere-at-all", [str(self.program)]),
                             str(self.program))
            self.assertIsNone(programs.find_program("nowhere-at-all"))


class CodexIsFoundWhenInstalledLaterTests(unittest.TestCase):

    def test_the_provider_finds_it(self) -> None:
        from personalized_agent.models.providers import codex_provider
        provider = codex_provider.CodexProvider.__new__(codex_provider.CodexProvider)
        with mock.patch.object(programs, "find_program_where",
                               lambda name, extra=(), fresh=False: (
                                   ("D:/later/codex.cmd", "the PATH as the system has it now")
                                   if name == "codex" else (None, ""))):
            self.assertEqual(provider.executable(), "D:/later/codex.cmd")


CHAIN = {"chain": [{"provider": "codex", "model": "gpt-5", "label": "codex/gpt-5",
                    "auth_status": {"state": "not_configured",
                                    "detail": "Codex is not installed on this machine"},
                    "availability": None}],
         "providers": [{"provider": "codex", "authentication_methods": ["none"], "models": []}],
         "answered": ""}


class SaidOnceAndNotReadyTests(unittest.TestCase):

    def setUp(self) -> None:
        kind, self.engine = _javascript_engine()
        if kind != "chrome":
            self.skipTest("no browser available to render the page")

    def test_a_client_that_is_not_installed_is_not_ready(self) -> None:
        import json
        result = _render(self.engine, {},
                         "document.body.insertAdjacentHTML('beforeend','<div id=\"model-chain\"></div>');"
                         f"renderModelChain({json.dumps(CHAIN)});await wait();"
                         "out.badge=text(document.querySelector('#model-chain .badge'));")
        self.assertEqual(result["err"], "")
        self.assertNotEqual(result["out"]["badge"], "ready")

    def test_with_the_bubble_the_page_shows_no_card(self) -> None:
        stub = {"/api/notifications": {"notifications": [
                    {"id": "n1", "title": "A reminder", "message": "Send the draft",
                     "kind": "proactive", "metadata": {}}]}}
        steps = ("speaksInBubble=true;await pollNotifications();await wait();"
                 "out.cards=document.querySelectorAll('.toast-notification').length;"
                 "out.read=window.__posts.filter(p=>p.p==='/api/notifications/read').length;"
                 "speaksInBubble=false;await pollNotifications();await wait();"
                 "out.cardsWithout=document.querySelectorAll('.toast-notification').length;")
        result = _render(self.engine, stub, steps)
        self.assertEqual(result["err"], "")
        self.assertEqual(result["out"]["cards"], 0, "said twice")
        self.assertEqual(result["out"]["read"], 0, "marked read under the bubble")
        self.assertEqual(result["out"]["cardsWithout"], 1, "with no bubble the page says it")


class TheStatusSaysSoTests(unittest.TestCase):

    def test_the_status_says_whether_the_bubble_speaks(self) -> None:
        from support import TemporaryAgent
        agent = TemporaryAgent()
        try:
            self.assertFalse(agent.app.status()["speaks_in_bubble"])
            agent.app.speaks_in_bubble = True
            self.assertTrue(agent.app.status()["speaks_in_bubble"])
        finally:
            agent.cleanup()


if __name__ == "__main__":
    unittest.main()
