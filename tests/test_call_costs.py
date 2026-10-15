"""WHAT EACH CALL COST, AND WHICH MODEL ANSWERED IT.

The cost is what the provider says (Claude Code: `total_cost_usd`, at list
price; OpenRouter: `usage.cost`, as billed), else the model's list price
(models/prices.py), else nothing. The model is the one that answered, by
its own name: Claude Code's "opus" answers as claude-opus-5-5. Calls from
before cost was counted are counted once at start.

And the Claude Code command line and the Claude desktop application are two
models to choose, never one standing in for the other."""

from __future__ import annotations

import json
import unittest

from support import TemporaryAgent

from personalized_agent.models import prices
from personalized_agent.models.base import (AuthMethod, AuthStatus, AvailabilityState,
                                            CredentialState, ModelAvailability,
                                            ModelDescriptor, ModelProvider, ModelRequest,
                                            ModelResponse, ModelUsage)
from personalized_agent.models.providers.claude_code_provider import (ClaudeCodeProvider,
                                                                     ClaudeDesktopProvider)
from personalized_agent.models.providers.codex_provider import CodexProvider

#: A real reply of `claude -p --output-format json --model haiku`, cut down.
CLAUDE_REPLY = {
    "type": "result", "result": "ok", "total_cost_usd": 0.0025274000000000004,
    "usage": {"input_tokens": 2, "cache_creation_input_tokens": 11805,
              "cache_read_input_tokens": 16420, "output_tokens": 4},
    "modelUsage": {"claude-haiku-5-5": {"inputTokens": 2, "outputTokens": 4,
                                        "cacheReadInputTokens": 16420,
                                        "cacheCreationInputTokens": 11805,
                                        "costUSD": 0.0025274000000000004}},
}


class PricesTests(unittest.TestCase):

    def test_a_list_price_matches_what_claude_code_said(self) -> None:
        # Claude Code keeps its cache for an hour: written at twice the input.
        cost = prices.estimate("haiku", 2, 4, 16420, 11805,
                               write_times=prices.cache_write_times("claude_code"))
        self.assertAlmostEqual(cost, CLAUDE_REPLY["total_cost_usd"], places=7)

    def test_names_are_known_in_their_forms(self) -> None:
        self.assertEqual(prices.known("opus"), "claude-opus-5-5")
        self.assertEqual(prices.known("anthropic/claude-sonnet-5.5"), "claude-sonnet-5-5")
        self.assertEqual(prices.known("claude-opus-4-8-20260101"), "claude-opus-4-8")
        self.assertEqual(prices.known("claude-opus-5"), "claude-opus-5")
        self.assertIsNone(prices.known("gpt-6-sol"))
        self.assertIsNone(prices.estimate("gpt-6-sol", 1000, 1000))

    def test_haiku_above_its_tier_costs_five_times(self) -> None:
        small = prices.estimate("claude-haiku-5-5", 100_000, 0)
        large = prices.estimate("claude-haiku-5-5", 100_001, 0)
        self.assertAlmostEqual(large / 100_001, 5 * small / 100_000, places=12)


class ProvidersSayTests(unittest.TestCase):

    def test_claude_code_says_its_cost_and_its_model(self) -> None:
        usage = ClaudeCodeProvider._usage_of(CLAUDE_REPLY)
        self.assertAlmostEqual(usage.cost_usd, 0.0025274, places=7)
        self.assertEqual(usage.cached_input_tokens, 16420)
        self.assertEqual(ClaudeCodeProvider._answered_by(CLAUDE_REPLY), "claude-haiku-5-5")
        self.assertEqual(ClaudeCodeProvider._answered_by({}), "")

    def test_codex_input_read_from_the_cache_is_told_apart(self) -> None:
        line = json.dumps({"type": "turn.completed", "usage": {
            "input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 50}})
        _, usage, _ = CodexProvider._parse_events(line)
        self.assertEqual((usage.input_tokens, usage.cached_input_tokens, usage.output_tokens),
                         (200, 800, 50))


class _Answering(ModelProvider):
    provider_id = "answering"
    display_name = "Answering"
    kind = "api"

    def __init__(self):
        self.said = None
        self.model = "claude-sonnet-5-5"

    def list_models(self):
        return [ModelDescriptor("m1", "M1")]

    def get_auth_methods(self):
        return [AuthMethod.API_KEY]

    def get_auth_status(self):
        return AuthStatus(CredentialState.AUTHENTICATED, AuthMethod.API_KEY)

    def authenticate(self, config):
        return self.get_auth_status()

    def logout(self):
        return self.get_auth_status()

    def check_availability(self, model):
        return ModelAvailability(True, AvailabilityState.AVAILABLE)

    def invoke(self, request, model):
        return ModelResponse(text="hi", model=self.model, provider=self.provider_id,
                             usage=ModelUsage(1000, 100, cost_usd=self.said))


class RecordedTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.provider = _Answering()
        self.app.models.register(self.provider)
        self.app.models.set_chain([{"provider": "answering", "model": "m1"}])

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _last(self):
        self.app.models.invoke(ModelRequest(messages=[{"role": "user", "content": "hi"}]))
        return self.app.model_calls.recent(1)[-1]

    def test_the_model_that_answered_at_its_price(self) -> None:
        row = self._last()
        self.assertEqual(row["model"], "claude-sonnet-5-5")
        self.assertAlmostEqual(row["estimated_cost"], (1000 * 2 + 100 * 10) / 1e6, places=9)

    def test_what_the_provider_said_it_cost_comes_first(self) -> None:
        self.provider.said = 0.5
        self.assertEqual(self._last()["estimated_cost"], 0.5)

    def test_a_model_with_no_price_costs_nothing_said(self) -> None:
        self.provider.model = "gpt-6-sol"
        self.assertIsNone(self._last()["estimated_cost"])

    def test_calls_from_before_are_counted(self) -> None:
        calls = self.app.model_calls
        calls.record(provider="claude_code", model="opus", task="t", input_tokens=10,
                     output_tokens=1000, latency_ms=1, cached_input_tokens=100_000,
                     cache_written_tokens=1000)
        calls.record(provider="codex", model="gpt-6-sol", task="t", input_tokens=10,
                     output_tokens=10, latency_ms=1)
        self.assertEqual(calls.fill_costs(), 1)
        rows = {r["model"]: r["estimated_cost"] for r in calls.recent(10)}
        self.assertAlmostEqual(rows["opus"],
                               (10 * 4 + 1000 * 4 * 2 + 100_000 * 0.2 + 1000 * 20) / 1e6)
        self.assertIsNone(rows["gpt-6-sol"])


class TwoClaudeClientsTests(unittest.TestCase):

    def test_the_command_line_and_the_desktop_application_are_two(self) -> None:
        agent = TemporaryAgent()
        try:
            models = agent.app.models
            cli, desktop = models.provider("claude_code"), models.provider("claude_desktop")
            self.assertIsInstance(cli, ClaudeCodeProvider)
            self.assertIsInstance(desktop, ClaudeDesktopProvider)
            self.assertEqual((cli.display_name, cli._choice()), ("Claude Code CLI", "cli"))
            self.assertEqual((desktop.display_name, desktop._choice()),
                             ("Claude desktop app", "desktop"))
            # The setting that chose between them is no longer one to show.
            from personalized_agent.common.config.schema import SCHEMA_BY_KEY
            self.assertTrue(SCHEMA_BY_KEY["local_client"].hidden)
        finally:
            agent.cleanup()

    def test_models_by_their_names_the_latest_opus_first(self) -> None:
        agent = TemporaryAgent()
        try:
            models = agent.app.models
            for provider in ("claude_code", "claude_desktop"):
                listed = [one.id for one in models.provider(provider).list_models()]
                self.assertEqual(listed[0], "claude-opus-5-5")
                self.assertFalse({"opus", "sonnet", "haiku"} & set(listed), listed)
                # An alias a setting still holds is still taken.
                self.assertTrue(models.model_belongs_to_provider(provider, "opus"))
        finally:
            agent.cleanup()

    def test_an_alias_kept_before_is_kept_by_its_name(self) -> None:
        agent = TemporaryAgent()
        try:
            config = agent.app.config
            config.set("primary_provider", "claude_code")
            config.set("primary_model", "opus")
            config.set("model_chain", [{"provider": "claude_code", "model": "opus"},
                                       {"provider": "claude_desktop", "model": "haiku"},
                                       {"provider": "offline", "model": "offline-1"}])
            config.set("heartbeat_model_override", "claude_code/sonnet")
            agent.app.models._claude_models_by_name()
            self.assertEqual(config.get("primary_model"), "claude-opus-5-5")
            self.assertEqual(config.get("model_chain"), [
                {"provider": "claude_code", "model": "claude-opus-5-5"},
                {"provider": "claude_desktop", "model": "claude-haiku-5-5"},
                {"provider": "offline", "model": "offline-1"}])
            self.assertEqual(config.get("heartbeat_model_override"),
                             "claude_code/claude-sonnet-5-5")
        finally:
            agent.cleanup()

    def test_claude_code_is_asked_with_its_effort(self) -> None:
        provider = ClaudeCodeProvider()
        request = ModelRequest(messages=[{"role": "user", "content": "hi"}],
                               reasoning_effort="xhigh")
        command = provider._command(request, "claude-opus-5-5")
        self.assertEqual(command[command.index("--effort") + 1], "xhigh")
        plain = provider._command(ModelRequest(messages=[{"role": "user", "content": "hi"}]),
                                  "claude-opus-5-5")
        self.assertNotIn("--effort", plain)
        self.assertEqual(provider.list_models()[0].reasoning_efforts,
                         ["low", "medium", "high", "xhigh", "max"])

    def test_the_anthropic_api_takes_effort_where_the_model_does(self) -> None:
        from personalized_agent.models.providers.anthropic_provider import efforts_of
        self.assertIn("xhigh", efforts_of("claude-opus-5-5"))
        self.assertNotIn("xhigh", efforts_of("claude-sonnet-4-6"))
        self.assertEqual(efforts_of("claude-haiku-4-5"), [])

    def test_the_desktop_chosen_before_is_the_desktop_model_now(self) -> None:
        agent = TemporaryAgent()
        try:
            config = agent.app.config
            config.set("local_client", "desktop")
            config.set("primary_provider", "claude_code")
            config.set("model_chain", [{"provider": "claude_code", "model": "opus"}])
            agent.app.models._desktop_chosen_before()
            self.assertEqual(config.get("primary_provider"), "claude_desktop")
            self.assertEqual(config.get("model_chain"),
                             [{"provider": "claude_desktop", "model": "opus"}])
            self.assertEqual(config.get("local_client"), "auto")
        finally:
            agent.cleanup()


if __name__ == "__main__":
    unittest.main()
