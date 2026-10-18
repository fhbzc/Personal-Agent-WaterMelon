"""The agent's calls to Codex get none of the user's own Codex: not its
MCP servers, plugins, hooks or rules, and not Codex's built-in shell, code
mode, web search, computer or browser - the agent's tools, run under its own
guard, are the only ones. The sign-in, the model and the effort the user
configured stay theirs."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from personalized_agent.models.base import ModelRequest
from personalized_agent.models.providers import codex_provider
from personalized_agent.models.providers.codex_provider import OWN_TOOLS_OFF, CodexProvider

HELP_NOW = ("Usage: codex exec [OPTIONS] [PROMPT]\n      --ignore-user-config\n"
            "      --ignore-rules\n")
HELP_BEFORE = "Usage: codex exec [OPTIONS] [PROMPT]\n      --ephemeral\n"


class _Codex:
    """A Codex installed here, whose `exec --help` says what it knows."""

    def __init__(self, help_text: str) -> None:
        self.help_text = help_text
        self.asked = 0
        self.folder = Path(tempfile.mkdtemp(prefix="pa-codex-home-"))
        self.program = self.folder / "codex.exe"
        self.program.write_text("", encoding="utf-8")

    def run(self, command, **_kw):
        if command[1:] == ["exec", "--help"]:
            self.asked += 1
            return subprocess.CompletedProcess(command, 0, self.help_text, "")
        raise AssertionError(f"only the help is asked here: {command}")


class OwnToolsOffTests(unittest.TestCase):

    def _command(self, codex: _Codex, request=None, model="gpt-6.1-sol", home=None):
        provider = CodexProvider()
        request = request or ModelRequest(messages=[{"role": "user", "content": "hi"}])
        with mock.patch.object(provider, "executable", lambda: str(codex.program)), \
                mock.patch.object(codex_provider.children, "run", codex.run), \
                mock.patch.dict(os.environ, {"CODEX_HOME": str(home or codex.folder)}):
            return provider, provider._command(request, model)

    def test_the_users_configuration_is_not_loaded_and_every_own_tool_is_off(self) -> None:
        codex = _Codex(HELP_NOW)
        _provider, command = self._command(codex)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        said = " ".join(command)
        for name in ("shell_tool", "unified_exec", "code_mode_host", "computer_use",
                     "browser_use", "plugins", "apps", "hooks"):
            self.assertIn(name, OWN_TOOLS_OFF)
            self.assertIn(f"features.{name}=false", said)
        self.assertIn('web_search="disabled"', said)
        # No agent of its own: the multi-agent tools stayed in code mode's
        # list with the feature off, and a helper could be started.
        self.assertIn("features.multi_agent=false", said)
        self.assertIn("agents.max_depth=0", said)
        # Still read-only, and the prompt still comes on stdin.
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertEqual(command[-1], "-")

    def test_a_codex_too_old_to_skip_it_has_its_mcp_servers_emptied(self) -> None:
        codex = _Codex(HELP_BEFORE)
        _provider, command = self._command(codex)
        self.assertNotIn("--ignore-user-config", command)
        self.assertNotIn("--ignore-rules", command)
        self.assertIn("mcp_servers={}", command)
        self.assertIn("features.shell_tool=false", command)

    def test_its_help_is_asked_once_for_the_same_program(self) -> None:
        codex = _Codex(HELP_NOW)
        provider, _command = self._command(codex)
        with mock.patch.object(provider, "executable", lambda: str(codex.program)), \
                mock.patch.object(codex_provider.children, "run", codex.run):
            provider._command(ModelRequest(messages=[]), "gpt-6.1-sol")
            provider._command(ModelRequest(messages=[]), "gpt-6.1-sol")
        self.assertEqual(codex.asked, 1)

    def test_default_is_still_the_model_and_effort_the_user_configured(self) -> None:
        codex = _Codex(HELP_NOW)
        (codex.folder / "config.toml").write_text(
            'model = "gpt-6-luna"\nmodel_reasoning_effort = "high"\n'
            '[mcp_servers.someone]\ncommand = "node"\n', encoding="utf-8")
        _provider, command = self._command(codex, model="default")
        self.assertEqual(command[command.index("--model") + 1], "gpt-6-luna")
        self.assertIn('model_reasoning_effort="high"', command)
        self.assertNotIn("someone", " ".join(command))

    def test_an_effort_chosen_in_settings_comes_before_the_configured_one(self) -> None:
        codex = _Codex(HELP_NOW)
        (codex.folder / "config.toml").write_text(
            'model_reasoning_effort = "high"\n', encoding="utf-8")
        request = ModelRequest(messages=[], reasoning_effort="low")
        _provider, command = self._command(codex, request=request)
        self.assertIn('model_reasoning_effort="low"', command)
        self.assertNotIn('model_reasoning_effort="high"', command)

    def test_no_configuration_leaves_default_to_codex(self) -> None:
        codex = _Codex(HELP_NOW)
        _provider, command = self._command(codex, model="default")
        self.assertNotIn("--model", command)
        self.assertFalse(any(part.startswith("model_reasoning_effort") for part in command))


class TheAgentsToolsServedNativelyTests(unittest.TestCase):
    """With a tool runner - Work's calls - the agent's tools are served to
    Codex over the agent's own MCP server, as they are to Claude Code: called
    as tools, run by the agent. Codex's code mode, through which it calls
    every tool, stays on; its shell stays off."""

    def _tools(self):
        from personalized_agent.models.base import ToolSpec
        return [ToolSpec("read_file", "Read a file.",
                         {"type": "object", "properties": {"path": {"type": "string"}}}),
                ToolSpec("web_search", "Search the web.",
                         {"type": "object", "properties": {"query": {"type": "string"}}})]

    def test_a_call_with_a_runner_is_given_the_bridge_and_its_tools_run_by_the_agent(self) -> None:
        import json as _json
        import urllib.request
        codex = _Codex(HELP_NOW)
        ran, seen = [], {}

        def runner(name, arguments):
            ran.append((name, arguments))
            return {"text": "three tips"}

        def run(command, **kw):
            if command[1:] == ["exec", "--help"]:
                return subprocess.CompletedProcess(command, 0, HELP_NOW, "")
            seen["command"], seen["input"], seen["kw"] = command, kw.get("input"), kw
            url = next(part.split("=", 1)[1] for part in command
                       if part.startswith("mcp_servers.pa.url="))
            # Codex calls the tool over MCP, as the real one does.
            body = _json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                "params": {"name": "read_file",
                                           "arguments": {"path": "notes.md"}}}).encode()
            urllib.request.urlopen(urllib.request.Request(
                _json.loads(url), data=body, headers={"Content-Type": "application/json"}),
                timeout=10).read()
            stream = ('{"type":"item.completed","item":{"type":"agent_message",'
                      '"text":"{\\"done\\": true}"}}\n{"type":"turn.completed","usage":{}}')
            return subprocess.CompletedProcess(command, 0, stream, "")
        provider = CodexProvider()
        request = ModelRequest(messages=[{"role": "user", "content": "check the notes"}],
                               tools=self._tools()[:1], tool_runner=runner)
        with mock.patch.object(provider, "executable", lambda: str(codex.program)), \
                mock.patch.object(codex_provider.children, "run", run), \
                mock.patch.dict(os.environ, {"CODEX_HOME": str(codex.folder)}):
            response = provider.invoke(request, "gpt-6.1-sol")
        self.assertEqual(ran, [("read_file", {"path": "notes.md"})])
        self.assertEqual([one["name"] for one in response.tool_exchanges], ["read_file"])
        self.assertEqual(response.tool_calls, [])
        command = " ".join(seen["command"])
        self.assertNotIn("features.code_mode_host=false", command)
        self.assertIn("features.shell_tool=false", command)
        self.assertIn("features.computer_use=false", command)
        # Told the tools are served; not the words-only convention.
        self.assertIn("MCP server named `pa`", seen["input"])
        self.assertNotIn("reply with a single JSON object", seen["input"])
        # Ended when silent or stopped, not at a fixed time.
        self.assertIn("watch", seen["kw"])
        self.assertNotIn("timeout", seen["kw"])

    def test_without_a_runner_the_words_convention_and_code_mode_off(self) -> None:
        codex = _Codex(HELP_NOW)
        request = ModelRequest(messages=[], tools=self._tools()[:1])
        provider, command = self._command(codex, request)
        self.assertIn("features.code_mode_host=false", command)
        self.assertFalse(any(part.startswith("mcp_servers.pa") for part in command))
        self.assertIn("reply with a single JSON object", provider._prompt(request))

    def _command(self, codex, request, bridge=None):
        provider = CodexProvider()
        with mock.patch.object(provider, "executable", lambda: str(codex.program)), \
                mock.patch.object(codex_provider.children, "run", codex.run), \
                mock.patch.dict(os.environ, {"CODEX_HOME": str(codex.folder)}):
            return provider, provider._command(request, "gpt-6.1-sol", bridge=bridge)

    def test_the_models_own_web_search_in_place_of_the_agents(self) -> None:
        codex = _Codex(HELP_NOW)
        provider = CodexProvider()
        said = {}

        def run(command, **kw):
            if command[1:] == ["exec", "--help"]:
                return subprocess.CompletedProcess(command, 0, HELP_NOW, "")
            said["command"], said["input"] = command, kw.get("input")
            stream = ('{"type":"item.completed","item":{"type":"agent_message",'
                      '"text":"found"}}\n{"type":"turn.completed","usage":{}}')
            return subprocess.CompletedProcess(command, 0, stream, "")
        request = ModelRequest(messages=[{"role": "user", "content": "look it up"}],
                               tools=self._tools(), builtin_web=True)
        with mock.patch.object(provider, "executable", lambda: str(codex.program)), \
                mock.patch.object(codex_provider.children, "run", run), \
                mock.patch.dict(os.environ, {"CODEX_HOME": str(codex.folder)}):
            provider.invoke(request, "gpt-6.1-sol")
        self.assertIn('web_search="live"', said["command"])
        self.assertNotIn("* web_search", said["input"])
        self.assertIn("SEARCH THE WEB WITH YOUR OWN WEB SEARCH", said["input"])
        # The app's own search chosen (Settings > Web search): Codex's is off.
        plain = ModelRequest(messages=[], tools=self._tools())
        _provider, command = self._command(codex, plain)
        self.assertIn('web_search="disabled"', command)

    def test_a_stopped_run_ends_the_call(self) -> None:
        from personalized_agent.common import children
        from personalized_agent.models.base import ModelInvocationError
        codex = _Codex(HELP_NOW)
        provider = CodexProvider()

        def run(command, **kw):
            if command[1:] == ["exec", "--help"]:
                return subprocess.CompletedProcess(command, 0, HELP_NOW, "")
            raise children.Ended("cancelled")
        request = ModelRequest(messages=[{"role": "user", "content": "go"}],
                               tools=self._tools()[:1], tool_runner=lambda n, a: {})
        with mock.patch.object(provider, "executable", lambda: str(codex.program)), \
                mock.patch.object(codex_provider.children, "run", run), \
                mock.patch.dict(os.environ, {"CODEX_HOME": str(codex.folder)}):
            with self.assertRaises(ModelInvocationError) as said:
                provider.invoke(request, "gpt-6.1-sol")
        self.assertIn("the run was stopped", str(said.exception))


if __name__ == "__main__":
    unittest.main()
