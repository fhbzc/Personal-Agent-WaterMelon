"""CHECK AGAIN, FOR A CLAUDE CLIENT (the user, 2026-10-29): what is kept of
it let go, as a restart would; where it was found, its sign-in and its
calls said in the log when they change; and the newest model of each
family asked of it by its aliases - only when the user checks again, and
only when its version is not the one last asked of: each asking is four
small calls of the user's own. One it names that the list does not have
is added to the list, first, and kept. Never asked by itself."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401

from personalized_agent.models.providers import claude_code_provider as claude
from personalized_agent.models.providers.claude_code_provider import ClaudeCodeProvider


class _Settings:
    def __init__(self):
        self.values = {}

    def get(self, key, default=""):
        # As the settings do: "" for one never set.
        return self.values.get(key, default)

    def set(self, key, value, internal=False):
        self.values[key] = value


class _Provider(ClaudeCodeProvider):
    """The client stood in for: its version as set, and each alias naming
    the model `means` says."""

    def __init__(self, version="2.1.300", means=None):
        super().__init__(settings=_Settings())
        self.version = version
        self.means = dict(means if means is not None else {
            "opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5",
            "haiku": "claude-haiku-5-5", "fable": "claude-fable-5-1"})
        self.calls = []

    def executable(self):
        return "/usr/local/bin/claude"

    def cli_version(self, cli):
        return self.version

    def invoke(self, request, model):
        self.calls.append(model)
        if model not in self.means:
            raise RuntimeError(f"no model {model}")
        return SimpleNamespace(model=self.means[model], text="OK")


class TheNewestOfEachFamilyTests(unittest.TestCase):

    def test_asked_once_a_version_and_a_new_one_kept_first(self) -> None:
        provider = _Provider(means={"opus": "claude-opus-5-6", "sonnet": "claude-sonnet-5-5",
                                    "haiku": "claude-haiku-5-5", "fable": "claude-fable-5-1"})
        said = provider.models_by_alias(force=True)
        self.assertTrue(said["checked"])
        self.assertEqual(provider.calls, list(claude.ALIASES))
        self.assertEqual(said["new"], ["claude-opus-5-6"])
        ids = [one.id for one in provider.list_models()]
        self.assertEqual(ids[0], "claude-opus-5-6")
        self.assertEqual(provider.list_models()[0].display_name, "Claude Opus 5.6")
        self.assertTrue(provider.accepts_model_id("claude-opus-5-6"))
        self.assertEqual(provider.settings.values["claude_code_models_checked"], "2.1.300")
        # Checked again on the same version: nothing asked, nothing spent.
        said = provider.models_by_alias(force=True)
        self.assertEqual((said["checked"], said["why"]), (False, "the same version"))
        self.assertEqual(len(provider.calls), 4)
        # A new version: asked again.
        provider.version = "2.1.301"
        self.assertTrue(provider.models_by_alias(force=True)["checked"])
        self.assertEqual(len(provider.calls), 8)

    def test_nothing_named_nothing_kept_and_asked_again_next_time(self) -> None:
        provider = _Provider(means={})
        said = provider.models_by_alias(force=True)
        self.assertEqual((said["checked"], said["why"]), (False, "no alias answered"))
        self.assertNotIn("claude_code_models_checked", provider.settings.values)
        self.assertEqual(provider.models_by_alias(force=True)["why"], "no alias answered")

    def test_only_a_claude_model_is_added(self) -> None:
        provider = _Provider(means={"opus": "not-a-claude-model"})
        self.assertFalse(provider.models_by_alias(force=True)["checked"])
        self.assertEqual([one.id for one in provider.list_models()],
                         [one.id for one in claude.MODELS])

    def test_never_asked_by_itself(self) -> None:
        from support import TemporaryAgent
        agent = TemporaryAgent()
        try:
            for name in ("claude_code", "claude_desktop"):
                provider = agent.app.models.provider(name)
                with mock.patch.object(type(provider), "models_by_alias",
                                       side_effect=AssertionError("asked by itself")):
                    agent.app.models.describe()
        finally:
            agent.cleanup()


class CheckAgainLetsGoTests(unittest.TestCase):

    def test_what_is_kept_is_let_go_and_it_is_looked_for_afresh(self) -> None:
        provider = ClaudeCodeProvider(settings=_Settings())
        provider._status_cache = {"loggedIn": False}
        provider._status_checked_at = 1e12
        provider._version_seen = (1e12, "/old/claude", "2.0.0")
        provider.forget()
        self.assertIsNone(provider._status_cache)
        self.assertEqual(provider._version_seen, (0.0, None, ""))
        asked = []

        def locate(client, fresh=False):
            asked.append(fresh)
            return None
        with mock.patch.object(ClaudeCodeProvider, "_locate", side_effect=locate):
            provider.executable()
            first = list(asked)
            provider.executable()
        # Every kind of client looked for afresh once, then as before.
        self.assertTrue(first and all(first))
        self.assertFalse(any(asked[len(first):]))

    def test_the_route(self) -> None:
        from pathlib import Path
        text = (Path(claude.__file__).parents[2] / "api" / "routes.py").read_text(
            encoding="utf-8")
        start = text.index('@route("POST", "/api/models/claude/recheck")')
        body = text[start:text.index("\n    @route", start + 10)]
        for step in ("provider.forget()", "models_by_alias(force=True)",
                     "app.models.invalidate()"):
            self.assertIn(step, body)


class SaidInTheLogOnceTests(unittest.TestCase):

    def test_where_it_was_found_and_its_sign_in(self) -> None:
        provider = ClaudeCodeProvider(settings=_Settings())
        name = "personalized_agent.models.providers.claude_code_provider"
        with mock.patch.object(ClaudeCodeProvider, "_resolve",
                               return_value=("cli", "/usr/local/bin/claude", None)), \
                mock.patch.object(provider, "_ask_client", return_value={"loggedIn": True}):
            with self.assertLogs(name, "INFO") as said:
                provider.executable()
                provider.executable()
                provider._auth_status(force=True)
                provider._auth_status(force=True)
        lines = said.output
        self.assertEqual(len(lines), 2, lines)
        self.assertIn("found: /usr/local/bin/claude (cli)", lines[0])
        self.assertIn("sign-in: signed in (", lines[1])


if __name__ == "__main__":
    unittest.main()
