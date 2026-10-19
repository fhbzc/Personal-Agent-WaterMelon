"""A run whose folders are kept apart - each way and each try of the Work
debugger - is never given to a model that reads by itself where nothing
keeps it out. Codex runs its own commands in a sandbox that stops it writing,
not reading; such a call goes to the next model on the list, or is refused
saying why - unless the installed Codex is found confined to the agent's own
tools, as the agent starts it (its own list of what it can call, asked once
per program and model)."""

from __future__ import annotations

import unittest

from support import TemporaryAgent

from personalized_agent.models.base import ModelRequest
from personalized_agent.models.manager import READS_UNCONFINED, ModelUnavailable
from test_model_chain import _Scripted


class PassedOverTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.models = self.agent.app.models
        self.codex = _Scripted("codex", kind="client")
        self.claude = _Scripted("claude_code", kind="client")
        for one in (self.codex, self.claude):
            self.models.register(one)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _ask(self, sealed):
        return self.models.invoke(ModelRequest(messages=[{"role": "user", "content": "hi"}],
                                               sealed_reading=sealed))

    def test_codex_is_passed_over_for_the_next(self) -> None:
        self.assertIn("codex", READS_UNCONFINED)
        self.models.set_chain([{"provider": "codex", "model": "m1"},
                               {"provider": "claude_code", "model": "m1"}])
        self.assertEqual(self._ask(True).text, "from claude_code")
        self.assertEqual(self.codex.calls, 0)
        # Not sealed: the list as it is.
        self.assertEqual(self._ask(False).text, "from codex")

    def test_codex_alone_is_refused_saying_why(self) -> None:
        self.models.set_chain([{"provider": "codex", "model": "m1"}])
        with self.assertRaises(ModelUnavailable) as refused:
            self._ask(True)
        self.assertTrue(refused.exception.availability.requires_user_action())
        self.assertIn("kept apart", str(refused.exception))
        self.assertEqual(self.codex.calls, 0)

    def test_a_codex_found_confined_is_used_and_one_not_says_why(self) -> None:
        self.models.set_chain([{"provider": "codex", "model": "m1"}])
        self.codex.confined = lambda model: (True, "")
        self.assertEqual(self._ask(True).text, "from codex")
        self.codex.confined = lambda model: (False, "this Codex offers tools of its own the agent "
                                              "cannot hold to its permissions: shell")
        with self.assertRaises(ModelUnavailable) as refused:
            self._ask(True)
        self.assertIn("cannot hold to its permissions: shell", str(refused.exception))

    def test_a_debugger_runs_calls_are_sealed(self) -> None:
        source = (__import__("pathlib").Path(__file__).resolve().parents[1]
                  / "personalized_agent" / "agent" / "work" / "runner.py").read_text(
                      encoding="utf-8")
        self.assertIn('if isinstance(self.unit.get("debug"), dict) and not '
                      'request.sealed_reading:', source)


class CodexFoundConfinedTests(unittest.TestCase):
    """Whether this Codex reads only through the agent's tools: its code
    mode's own list of what it can call, asked once per program - only the
    tools measured to read nothing (OWN_TOOLS_ALLOWED) and the agent's own
    server may be there. A name a later Codex adds, or no list: not
    confined."""

    def _provider(self, names):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from personalized_agent.models.providers.codex_provider import CodexProvider
        provider = CodexProvider()
        program = Path(tempfile.mkdtemp(prefix="pa-codex-")) / "codex.exe"
        program.write_text("", encoding="utf-8")
        asked = []
        provider.executable = lambda: str(program)
        provider._probe_tools = lambda model="default": asked.append(model) or names
        return provider, asked, mock

    def test_only_what_reads_nothing(self) -> None:
        provider, asked, _mock = self._provider(
            ["apply_patch", "list_mcp_resources", "list_mcp_resource_templates",
             "read_mcp_resource", "mcp__pa__probe"])
        self.assertEqual(provider.confined("gpt-x"), (True, ""))
        provider.confined("gpt-x")
        self.assertEqual(asked, ["gpt-x"], "asked once for the same program and model")
        # Which tools a model is given differs: another model, asked again.
        provider.confined("gpt-y")
        self.assertEqual(asked, ["gpt-x", "gpt-y"])

    def test_a_tool_not_on_the_list_and_no_list(self) -> None:
        provider, _asked, _mock = self._provider(
            ["mcp__pa__probe", "clock__curr_time", "multi_agent_v1__spawn_agent", "shell"])
        ok, why = provider.confined()
        self.assertFalse(ok)
        self.assertIn("multi_agent_v1__spawn_agent, shell", why)
        provider, _asked, _mock = self._provider(None)
        self.assertEqual(provider.confined()[0], False)


if __name__ == "__main__":
    unittest.main()
