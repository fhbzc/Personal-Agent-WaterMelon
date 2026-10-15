"""What a debugger is shown of a model call is what was actually sent.

Ported from PR #4 (by daniela-miaut). The local clients are started through
the children wrapper here, so that is where the call is caught.
"""

from __future__ import annotations

import subprocess
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from support import TemporaryAgent
from personalized_agent.models.base import (AvailabilityState, ModelAvailability,
                                            ModelRequest, ToolSpec)
from personalized_agent.models.manager import ModelUnavailable


class ProviderDebugTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_claude_code_capture_matches_the_child_payload_and_is_detached(self) -> None:
        from personalized_agent.models.providers.claude_code_provider import ClaudeCodeProvider

        provider = ClaudeCodeProvider(workspace=self.agent.workspace)
        provider.executable = lambda: "/mock/claude"
        request = ModelRequest(
            messages=[{"role": "user", "content": "actual question"}],
            system="system rule", tools=[ToolSpec("lookup", "look it up", {"type": "object"})])
        captured = []

        def debug(kind, data):
            captured.append((kind, data))
            if kind == "provider_request":
                data["input"] = "callback mutation"

        done = subprocess.CompletedProcess([], 0, '{"result":"ok"}', "")
        # The system prompt goes in a file of its own (a long one would pass
        # Windows' command-line limit): read while the call runs.
        system = []

        def run_and_read(command, **kwargs):
            flag = command.index("--append-system-prompt-file")
            from pathlib import Path
            system.append(Path(command[flag + 1]).read_text(encoding="utf-8"))
            return done

        with patch("personalized_agent.common.children.run",
                   side_effect=run_and_read) as run:
            provider.invoke(replace(request, debug=debug), "sonnet")

        kind, detail = captured[0]
        self.assertEqual(kind, "provider_request")
        kwargs = run.call_args.kwargs
        self.assertEqual(detail["command"], run.call_args.args[0])
        self.assertEqual(kwargs["input"], provider._prompt(request))
        self.assertNotEqual(detail["input"], kwargs["input"])
        self.assertEqual(detail["cwd"], kwargs["cwd"])
        self.assertIn("Available tools:", system[0])

    def test_codex_capture_matches_the_child_payload_even_if_callback_fails(self) -> None:
        from personalized_agent.models.providers.codex_provider import CodexProvider

        provider = CodexProvider(workspace=self.agent.workspace)
        provider.executable = lambda: "/mock/codex"
        request = ModelRequest(messages=[{"role": "assistant", "content": "prior"},
                                         {"role": "user", "content": "question"}],
                               system="system rule")
        seen = []

        def debug(kind, data):
            seen.append((kind, data))
            raise RuntimeError("debug sink failed")

        stream = ('{"type":"item.completed","item":{"type":"agent_message",'
                  '"text":"ok"}}\n{"type":"turn.completed","usage":{}}')
        done = subprocess.CompletedProcess([], 0, stream, "")
        with patch("personalized_agent.common.children.run", return_value=done) as run:
            response = provider.invoke(replace(request, debug=debug), "default")

        self.assertEqual(response.text, "ok")
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["input"], provider._prompt(request))
        self.assertEqual(kwargs["cwd"], str(self.agent.workspace))
        self.assertIn("--sandbox", run.call_args.args[0])
        self.assertEqual(run.call_args.args[0][-1], "-")
        self.assertEqual(seen[0][1]["command"], run.call_args.args[0])

    def test_anthropic_capture_is_the_exact_api_kwargs(self) -> None:
        from personalized_agent.models.providers.anthropic_provider import AnthropicProvider

        class FakeMessages:
            def create(_self, **kwargs):
                self.actual = kwargs
                return SimpleNamespace(
                    content=[SimpleNamespace(type="text", text="ok")],
                    usage=SimpleNamespace(input_tokens=2, output_tokens=1))

        provider = AnthropicProvider(self.app.credentials)
        provider._get_client = lambda: SimpleNamespace(messages=FakeMessages())
        request = ModelRequest(
            messages=[{"role": "user", "content": "question"}],
            system="system rule", tools=[ToolSpec("lookup", "look it up", {"type": "object"})])
        events = []
        provider.invoke(replace(request, debug=lambda kind, data: events.append((kind, data))),
                        "claude-sonnet")
        self.assertEqual(events[0][0], "provider_request")
        self.assertEqual(events[0][1]["kwargs"], self.actual)
        self.assertEqual(events[0][1]["kwargs"]["model"], "claude-sonnet")

    def test_nobody_watching_changes_nothing(self) -> None:
        """An ordinary call carries no watcher, and is the same request."""
        request = ModelRequest(messages=[{"role": "user", "content": "x"}])
        self.assertIsNone(request.debug)
        self.assertEqual(request, replace(request, debug=lambda *_: None))
        request.emit_debug("anything", {"a": 1})        # nothing to call, nothing raised

    def test_manager_reports_resolution_before_invocation(self) -> None:
        events = []
        self.app.models.invoke(
            ModelRequest(messages=[{"role": "user", "content": "hello"}],
                         debug=lambda kind, data: events.append((kind, data))))
        resolution = next(data for kind, data in events if kind == "model_resolution")
        self.assertEqual(resolution["decision"], "attempt")
        self.assertEqual(resolution["requested"], resolution["chosen"])
        self.assertIn("available", resolution["availability"])

    def test_manager_reports_refused_model_before_chat_error(self) -> None:
        events = []
        self.app.models.check_availability = lambda *args, **kwargs: ModelAvailability(
            False, AvailabilityState.AUTHENTICATION_REQUIRED, reason="sign in required")
        with self.assertRaises(ModelUnavailable):
            self.app.models.invoke(ModelRequest(
                messages=[{"role": "user", "content": "hello"}],
                debug=lambda kind, data: events.append((kind, data))))
        self.assertEqual(events[0][0], "model_resolution")
        self.assertEqual(events[0][1]["decision"], "denied")
        self.assertIsNone(events[0][1]["chosen"])
        self.assertEqual(events[0][1]["availability"]["reason"], "sign in required")

    def test_manager_reports_explicit_fallback_selection(self) -> None:
        events = []
        self.app.config.set("model_fallback_enabled", True)
        self.app.config.set("model_fallback_target", "offline/offline-1")
        self.app.models.check_availability = lambda *args, **kwargs: ModelAvailability(
            False, AvailabilityState.AUTHENTICATION_REQUIRED, reason="sign in required")
        response = self.app.models.invoke(ModelRequest(
            messages=[{"role": "user", "content": "hello"}],
            debug=lambda kind, data: events.append((kind, data))))
        self.assertTrue(response.text)
        # The first refused, said; then the call passed down the list, said.
        decisions = [data["decision"] for kind, data in events if kind == "model_resolution"]
        self.assertEqual(decisions[0], "denied")
        passed = next(data for kind, data in events
                      if kind == "model_resolution" and data.get("fallback_used"))
        self.assertEqual(passed["chosen"]["label"], "offline/offline-1")


if __name__ == "__main__":
    unittest.main()
