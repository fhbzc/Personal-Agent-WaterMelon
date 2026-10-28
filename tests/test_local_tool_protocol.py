"""The agent's tools through a local client's transcript (models/providers/local_tools.py).

Ported in part from PR #4 (by daniela-miaut): the full argument schema in the
list of tools, Codex labelling a tool's result as one, and Codex keeping a
call that the model kept typing after. Each client keeps its own protocol
wording; only the mechanics are shared - and running a tool is still the
agent's registry's job, under its own policy.
"""

import json
import re
import subprocess
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from support import TemporaryAgent
from personalized_agent.models.base import ModelRequest, ToolSpec
from personalized_agent.models.providers import claude_code_provider, codex_provider
from personalized_agent.models.providers.claude_code_provider import ClaudeCodeProvider
from personalized_agent.models.providers.codex_provider import CodexProvider

RUN = "personalized_agent.common.children.run"


def _offered(prompt: str) -> dict:
    """The tools a prompt lists, by name, with the schema it gave each."""
    listed = prompt.split("Available tools:\n", 1)[1]
    found = {}
    # A description may run over several lines; the schema is one line.
    for name, schema in re.findall(
            r"^- ([a-z_]+): .*?^  arguments \(JSON schema\): (\{[^\n]*\})$", listed,
            re.M | re.S):
        found[name] = json.loads(schema)
    return found


class TheToolListTests(unittest.TestCase):
    def setUp(self):
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.request = ModelRequest(messages=[], tools=self.app.agent.tools.specs)

    def tearDown(self):
        self.agent.cleanup()

    def test_both_clients_are_given_every_tools_whole_argument_schema(self):
        for provider in (CodexProvider(), ClaudeCodeProvider()):
            with self.subTest(provider=provider.provider_id):
                offered = _offered(provider._system(self.request))
                self.assertEqual(set(offered), {spec.name for spec in self.request.tools})
                for spec in self.request.tools:
                    self.assertEqual(offered[spec.name], spec.input_schema)
                schedule = offered["schedule_task"]
                self.assertEqual(schedule["required"],
                                 ["title", "description", "notify", "at_local", "zone"])
                self.assertEqual(schedule["properties"]["notify"]["enum"],
                                 ["silent", "notify"])

    def test_both_clients_are_told_the_same_words(self):
        """The same of the agent's tools for Claude Code and Codex (the user,
        2026-10-27, PR #3): the client chooses the model, not what the
        agent's tools mean."""
        claude = ClaudeCodeProvider()._system(self.request)
        codex = CodexProvider()._system(self.request)
        self.assertEqual(claude, codex)
        self.assertIs(claude_code_provider.TOOL_PROTOCOL, codex_provider.TOOL_PROTOCOL)
        self.assertIn("they are how you look things up", codex)


class CodexTranscriptTests(unittest.TestCase):
    TOOLS = [ToolSpec("list_scheduled_tasks", "List scheduled tasks.", {"type": "object"}),
             ToolSpec("current_time", "The time.", {"type": "object"})]

    def test_a_tool_result_is_labelled_as_one(self):
        prompt = CodexProvider()._prompt(ModelRequest(messages=[{
            "role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1",
                                         "content": '{"task_id":"task_123"}'}]}]))
        self.assertIn('Tool result: {"task_id":"task_123"}', prompt)
        self.assertNotIn("User:", prompt)
        self.assertNotIn("tool_use_id", prompt)

    def test_a_call_the_model_kept_typing_after_is_still_a_call(self):
        request = ModelRequest(messages=[], tools=self.TOOLS)
        reply = ('{"tool": "list_scheduled_tasks", "arguments": {"limit": 3}}\n'
                 'Tool result: [{"id": "invented"}]')
        calls = CodexProvider._tool_calls(reply, request)
        self.assertEqual([(c.name, c.arguments) for c in calls],
                         [("list_scheduled_tasks", {"limit": 3})])

    def test_and_one_with_its_reason_in_front(self):
        request = ModelRequest(messages=[], tools=self.TOOLS)
        calls = CodexProvider._tool_calls(
            'Let me check the time first.\n{"tool": "current_time", "arguments": {}}', request)
        self.assertEqual([c.name for c in calls], ["current_time"])

    def test_a_tool_not_offered_is_not_a_call(self):
        """Read here or not, nothing runs a tool but the registry - and a
        name this request did not offer is not even read as a call."""
        request = ModelRequest(messages=[], tools=self.TOOLS)
        self.assertEqual(CodexProvider._tool_calls(
            '{"tool": "write_file", "arguments": {"path": "x"}}', request), [])
        self.assertEqual(CodexProvider._tool_calls('{"answer": 42}', request), [])
        self.assertEqual(CodexProvider._tool_calls(
            '{"tool": "current_time", "arguments": {}}', ModelRequest(messages=[])), [])

    def test_the_call_is_remembered_without_what_followed_it(self):
        provider = CodexProvider()
        provider.executable = lambda: "codex"
        reply = '{"tool": "current_time", "arguments": {}}\nTool result: 3 o\'clock'
        event = json.dumps({"type": "item.completed",
                            "item": {"type": "agent_message", "text": reply}})
        with patch(RUN, return_value=subprocess.CompletedProcess([], 0, event, "")):
            response = provider.invoke(ModelRequest(messages=[], tools=self.TOOLS), "default")
        self.assertEqual(response.text, "")
        self.assertEqual([c.name for c in response.tool_calls], ["current_time"])
        self.assertEqual(json.loads(response.raw_content),
                         {"tool": "current_time", "arguments": {}})


class ThroughTheAgentTests(unittest.TestCase):
    """End to end: a Codex call becomes a real task through the registry, and
    the result the model invented never reaches the next turn."""

    def setUp(self):
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self):
        self.agent.cleanup()

    def _converse(self, provider_id, replies):
        provider = self.app.models.provider(provider_id)
        provider.executable = lambda: provider_id
        if hasattr(provider, "_resolve"):
            # Installed, whether or not this machine has it.
            provider._resolve = lambda: ("cli", provider_id, None)
        provider._auth_status = lambda force=False: (
            {"logged_in": True} if provider_id == "codex" else {"loggedIn": True})
        self.app.config.set("primary_provider", provider_id)
        self.app.config.set("primary_model", "default" if provider_id == "codex" else "sonnet")
        replies = iter(replies)
        prompts = []

        def answer(command, **kwargs):
            if list(command[1:]) == ["exec", "--help"]:
                # What the installed Codex knows (codex_provider._takes).
                return subprocess.CompletedProcess(command, 0, "--ignore-user-config\n", "")
            prompts.append(kwargs["input"])
            text = next(replies)
            if "--mcp-config" in command:
                # ON THE CHECKED ROUTE (Chat, 2026-10-27): the client calls the
                # tool through the call's own server, as its own - not in words.
                url = next(iter(json.loads(command[command.index("--mcp-config") + 1])
                                ["mcpServers"].values()))["url"]
                call = json.loads(text.splitlines()[0])
                body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                   "params": {"name": call["tool"],
                                              "arguments": call["arguments"]}}).encode()
                urllib.request.urlopen(urllib.request.Request(
                    url, data=body, method="POST",
                    headers={"Content-Type": "application/json"}), timeout=30).read()
                text = next(replies)
            if provider_id == "codex":
                out = json.dumps({"type": "item.completed",
                                  "item": {"type": "agent_message", "text": text}})
            else:
                out = json.dumps({"result": text})
            return subprocess.CompletedProcess(command, 0, out, "")

        with patch(RUN, side_effect=answer):
            result = self.app.agent.handle_message("Remind me to stretch in two days.")
        return result, prompts

    def test_both_clients_schedule_through_the_registry(self):
        when = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0)
        call = {"tool": "schedule_task", "arguments": {
            "title": "Stretch", "description": "Time to stretch.",
            "rationale": "The user asked.", "notify": "notify",
            "scheduled_at": when.isoformat()}}
        for provider_id in ("codex", "claude_code"):
            with self.subTest(provider_id):
                before = len(self.app.scheduler_store.list())
                result, prompts = self._converse(provider_id, [
                    json.dumps(call) + '\nTool result: {"task_id": "invented"}',
                    "Scheduled: Stretch."])
                tasks = self.app.scheduler_store.list()
                self.assertEqual(len(tasks), before + 1)
                made = [t for t in tasks if t.title == "Stretch"][-1]
                self.assertEqual(result["classification"], "delayed")
                self.assertEqual(result["answer"], "Scheduled: Stretch.")
                self.assertEqual(result["tool_calls"][0]["tool"], "schedule_task")
                if len(prompts) == 1:
                    continue                  # run in the call, through its server
                # The next turn reads the real result, labelled as one, and
                # not the one the model made up.
                self.assertIn("Tool result:", prompts[1])
                self.assertIn(made.id, prompts[1])
                self.assertNotIn("invented", prompts[1])

    def test_a_call_outside_the_policy_is_refused_by_the_runtime(self):
        """Read from a local client's reply, a call still meets the sandbox."""
        outside = self.agent.dir / "outside.txt"
        call = {"tool": "write_file", "arguments": {"path": str(outside), "content": "x"}}
        result, prompts = self._converse("codex", [json.dumps(call), "I could not."])
        self.assertFalse(outside.exists())
        self.assertIn("security", result["tool_calls"][0]["result"].lower())


if __name__ == "__main__":
    unittest.main()
