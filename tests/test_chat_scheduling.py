"""CHAT AND THE CALENDAR, WHICHEVER CLIENT (the user, 2026-10-27, from PR #3,
on top of what is here): Claude Code and Codex told the same of the agent's
tools; Codex told it as its own developer instructions; Chat told what the
application has, and how to put something on the calendar - a time not said
taken from the conversation and said back, the time and zone given on every
call."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.models.base import ModelRequest, ModelResponse, ToolCall, ToolSpec
from personalized_agent.models.providers import local_tools
from personalized_agent.models.providers.claude_code_provider import ClaudeCodeProvider
from personalized_agent.models.providers.codex_provider import CodexProvider

ROOT = Path(__file__).resolve().parents[1]


class ChatPutsItOnTheCalendarTests(unittest.TestCase):

    def setUp(self) -> None:
        self.fixture = TemporaryAgent()
        self.addCleanup(self.fixture.cleanup)
        self.app = self.fixture.app

    def test_exactly_one_entry_and_the_time_and_zone_on_every_call(self) -> None:
        responses = [ModelResponse(tool_calls=[ToolCall("s1", "schedule_task", {
            "title": "Planning meeting", "description": "The quarter's planning.",
            "notify": "silent", "at_local": "2030-10-11 01:00", "zone": "Asia/Shanghai"})]),
            ModelResponse(text="On your calendar: 11 October 2030, 01:00 Beijing time.")]
        with mock.patch.object(self.app.models, "invoke", side_effect=responses) as invoke:
            result = self.app.agent.handle_message(
                "Add a meeting on October 11, 2030 at 1AM Beijing time.")
        tasks = self.app.scheduler.store.list()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].title, "Planning meeting")
        self.assertEqual(tasks[0].scheduled_at.isoformat(), "2030-10-10T17:00:00+00:00")
        self.assertEqual(tasks[0].execution_type, "notification", "it only notifies")
        self.assertEqual(result["classification"], "delayed")
        # The clock and the zone, on each of the two calls of this turn.
        systems = [call.args[0].system for call in invoke.call_args_list]
        self.assertEqual(len(systems), 2)
        for system in systems:
            self.assertIn("on the user's clock", system)
            self.assertIn("What this application has", system)

    def test_an_answer_with_no_call_puts_nothing_on(self) -> None:
        with mock.patch.object(self.app.models, "invoke",
                               return_value=ModelResponse(text="Which day?")):
            self.app.agent.handle_message("Add a meeting.")
        self.assertEqual(self.app.scheduler.store.list(), [])

    def test_the_prompt_says_what_is_what_and_how_to_schedule(self) -> None:
        system = self.app.agent._system_prompt()
        for one in ("Calendar:", "Tasks:", "To-do:", "Work:", "Connections:",
                    "Notifications:", "never that you did it",
                    "Do not stop to ask: put it on, then say what you took",
                    'notify="silent"', "task_id"):
            self.assertIn(one, system)
        schedule = next(s for s in self.app.agent.tools.specs if s.name == "schedule_task")
        self.assertIn("this application's own calendar", schedule.description)


def _request() -> ModelRequest:
    return ModelRequest(messages=[{"role": "user", "content": "hello"}],
                        system="Application instructions", tools=[ToolSpec(
                            "schedule_task", "Put it on the calendar.",
                            {"type": "object", "properties": {"title": {"type": "string"}},
                             "required": ["title"]})])


def _codex(program: str) -> CodexProvider:
    provider = CodexProvider()
    provider.executable = lambda: program
    provider._takes = lambda flag: True
    return provider


class BothClientsAreToldTheSameTests(unittest.TestCase):

    def test_the_same_words_and_the_whole_schema(self) -> None:
        codex = _codex("/usr/local/bin/codex")._system(_request())
        claude = ClaudeCodeProvider()._system(_request())
        self.assertEqual(codex, claude)
        self.assertIn('"required": ["title"]', codex)
        self.assertIn("this application's own", codex)
        self.assertIn("not a browser", codex)

    def test_claude_code_has_them_as_its_system_prompt(self) -> None:
        source = (ROOT / "personalized_agent" / "models" / "providers"
                  / "claude_code_provider.py").read_text(encoding="utf-8")
        self.assertIn('command += ["--append-system-prompt", system]', source)


class CodexAsItsDeveloperInstructionsTests(unittest.TestCase):

    def test_given_as_developer_instructions_not_in_the_prompt(self) -> None:
        provider = _codex("C:/Program Files/codex/codex.exe")
        command = provider._command(_request(), "gpt-5.5")
        value = next(one for one in command if one.startswith("developer_instructions="))
        said = json.loads(value.split("=", 1)[1])
        self.assertIn("Application instructions", said)
        self.assertIn("schedule_task", said)
        self.assertNotIn("System instructions", provider._prompt(_request()))
        self.assertIn("--ephemeral", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")

    def test_through_a_cmd_they_stay_in_the_prompt(self) -> None:
        provider = _codex("C:/Users/x/AppData/Roaming/npm/codex.cmd")
        command = provider._command(_request(), "gpt-5.5")
        self.assertFalse(any(one.startswith("developer_instructions=") for one in command))
        self.assertIn("System instructions", provider._prompt(_request()))

    def test_a_codex_that_refuses_it_is_told_in_the_prompt(self) -> None:
        provider = _codex("/usr/local/bin/codex")
        made = []
        answer = "\n".join(json.dumps(one) for one in (
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Hi."}},
            {"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 1}}))

        def run(command, input=None, **kwargs):
            made.append((list(command), input))
            if len(made) == 1:
                return subprocess.CompletedProcess(command, 2, "",
                                                   "unknown field `developer_instructions`")
            return subprocess.CompletedProcess(command, 0, answer, "")
        request = ModelRequest(messages=[{"role": "user", "content": "hello"}],
                               system="Application instructions")
        with mock.patch("personalized_agent.models.providers.codex_provider.children.run",
                        side_effect=run):
            said = provider.invoke(request, "gpt-5.5")
        self.assertEqual(said.text, "Hi.")
        self.assertEqual(len(made), 2)
        self.assertTrue(any(one.startswith("developer_instructions=") for one in made[0][0]))
        self.assertFalse(any(one.startswith("developer_instructions=") for one in made[1][0]))
        self.assertIn("System instructions", made[1][1])
        self.assertTrue(provider._developer_refused, "and so from then on")


if __name__ == "__main__":
    unittest.main()
