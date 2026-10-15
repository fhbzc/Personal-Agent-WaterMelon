"""How hard the primary Codex model thinks: chosen, applied, reset.

Ported from PR #4 (by daniela-miaut). Codex is started through the children
wrapper here, so that is where its command is caught.
"""

import json
import os
import subprocess
import unittest
from unittest.mock import patch

from support import TemporaryAgent
from personalized_agent.api.errors import ApiError
from personalized_agent.api.routes import build_routes
from personalized_agent.api.server import Request
from personalized_agent.common.config.store import ConfigStore
from personalized_agent.models.base import (
    AvailabilityState, FailureClass, ModelAvailability, ModelInvocationError,
    ModelRequest, ModelResponse,
)
from personalized_agent.models.manager import ROLES


class PrimaryReasoningEffortTests(unittest.TestCase):
    def setUp(self):
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.provider = self.app.models.provider("codex")
        self.env = patch.dict(os.environ, {"CODEX_HOME": str(self.agent.dir)})
        self.env.start()
        (self.agent.dir / "models_cache.json").write_text(json.dumps({"models": [
            {"slug": "deep-model", "visibility": "list", "default_reasoning_level": "medium",
             "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"},
                                            {"effort": "high"}, {"effort": "xhigh"}]},
            {"slug": "fast-model", "visibility": "list", "default_reasoning_level": "low",
             "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}]},
        ]}), encoding="utf-8")
        self.app.config.set("primary_provider", "codex")
        self.app.config.set("primary_model", "deep-model")
        self.app.models.check_availability = lambda role="primary", force=False: ModelAvailability(
            True, AvailabilityState.AVAILABLE)
        self.routes = build_routes(self.app)

    def tearDown(self):
        self.env.stop()
        self.agent.cleanup()

    def _settings(self, values=None):
        method = "GET" if values is None else "POST"
        req = Request(method, "/api/settings", {}, {"values": values}, {})
        return self.routes[(method, req.path)](req)

    def test_default_preserves_codex_configuration(self):
        self.assertEqual(self.app.config.get("primary_reasoning_effort"), "default")
        for effort in (None, "default"):
            command = self.provider._command(ModelRequest([], reasoning_effort=effort), "deep-model")
            self.assertFalse(any(arg.startswith("model_reasoning_effort=") for arg in command))

    def test_catalog_metadata_filters_choices_and_reports_model_default(self):
        data = self._settings()
        field = next(f for f in data["schema"] if f["key"] == "primary_reasoning_effort")
        self.assertEqual(field["choices"], ["default", "low", "medium", "high", "xhigh"])
        self.assertFalse(field["readonly"])
        self.assertIn("medium", data["choice_notes"][field["key"]]["default"])
        self.assertIn("your Codex configuration", data["choice_notes"][field["key"]]["default"])

    def test_a_model_change_to_another_provider_resets_it_too(self):
        self._settings({"primary_reasoning_effort": "high"})
        result = self._settings({"primary_model": "offline/offline-1"})
        self.assertEqual(result["values"]["primary_reasoning_effort"], "default")
        self.assertEqual(result["corrected"]["primary_reasoning_effort"], "default")

    def test_an_effort_is_refused_for_a_provider_that_takes_none(self):
        self.app.config.set("primary_provider", "offline")
        self.app.config.set("primary_model", "offline-1")
        with self.assertRaises(ApiError):
            self._settings({"primary_reasoning_effort": "high"})
        self.assertEqual(self.app.config.get("primary_reasoning_effort"), "default")

    def test_effort_is_saved_and_applied_to_the_actual_codex_command(self):
        self._settings({"primary_reasoning_effort": "high"})
        reopened = ConfigStore(path=self.app.config.path)
        self.assertEqual(reopened.get("primary_reasoning_effort"), "high")
        completed = subprocess.CompletedProcess([], 0,
            '{"type":"item.completed","item":{"type":"agent_message","text":"Answer"}}', "")
        original = ModelRequest([{"role": "user", "content": "question"}])
        with patch.object(self.provider, "executable", return_value="codex"), \
                patch("personalized_agent.common.children.run", return_value=completed) as run:
            response = self.app.models.invoke(original)
        self.assertEqual(response.text, "Answer")
        command = run.call_args.args[0]
        self.assertIn('model_reasoning_effort="high"', command)
        self.assertEqual(command[-1], "-")
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertIsNone(original.reasoning_effort, "the caller's request was mutated")

    def test_inherited_roles_apply_effort_but_explicit_overrides_do_not(self):
        self.app.config.set("primary_reasoning_effort", "high")
        with patch.object(self.provider, "invoke", return_value=ModelResponse()) as invoke:
            for role in ROLES:
                self.app.models.invoke(ModelRequest([]), role)
                self.assertEqual(invoke.call_args.args[0].reasoning_effort, "high")
            self.app.config.set("heartbeat_model_override", "codex/fast-model")
            self.app.models.invoke(ModelRequest([]), "heartbeat")
            self.assertIsNone(invoke.call_args.args[0].reasoning_effort)

    def test_fallback_does_not_inherit_effort_and_other_providers_ignore_it(self):
        self.app.config.set("primary_reasoning_effort", "high")
        self.app.config.set("model_fallback_enabled", True)
        self.app.config.set("model_fallback_target", "codex/fast-model")
        self.app.models.check_availability = lambda role="primary": ModelAvailability(
            False, AvailabilityState.QUOTA_EXHAUSTED, failure_class=FailureClass.USER_ACTION_REQUIRED)
        # The fallback is checked for itself: as on a machine with Codex,
        # whether or not this one has it.
        real_check = self.app.models._check
        self.app.models._check = lambda resolved, force=False: ModelAvailability(True)             if resolved.model_id == "fast-model" else real_check(resolved, force)
        with patch.object(self.provider, "invoke", return_value=ModelResponse()) as invoke:
            self.app.models.invoke(ModelRequest([]))
            self.assertIsNone(invoke.call_args.args[0].reasoning_effort)
        self.app.config.set("primary_provider", "offline")
        self.app.config.set("primary_model", "offline-1")
        self.app.models.check_availability = lambda role="primary": ModelAvailability(True)
        offline = self.app.models.provider("offline")
        with patch.object(offline, "invoke", return_value=ModelResponse()) as invoke:
            self.app.models.invoke(ModelRequest([]))
            self.assertIsNone(invoke.call_args.args[0].reasoning_effort)
        field = next(f for f in self._settings()["schema"] if f["key"] == "primary_reasoning_effort")
        self.assertTrue(field["readonly"])

    def test_unsupported_effort_is_rejected_before_any_settings_are_saved(self):
        before = self.app.config.all()
        with self.assertRaises(ApiError):
            self._settings({"primary_reasoning_effort": "max", "agent_name": "changed"})
        self.assertEqual(before, self.app.config.all())
        with patch.object(self.provider, "executable", return_value="codex"), \
                patch("personalized_agent.common.children.run") as run:
            with self.assertRaises(ModelInvocationError):
                self.provider.invoke(ModelRequest([], reasoning_effort="max"), "deep-model")
            run.assert_not_called()

    def test_model_change_resets_an_incompatible_effort_to_default(self):
        self._settings({"primary_reasoning_effort": "high"})
        result = self._settings({"primary_model": "codex/fast-model"})
        self.assertEqual(result["values"]["primary_model"], "fast-model")
        self.assertEqual(result["values"]["primary_reasoning_effort"], "default")
        self.assertEqual(result["corrected"]["primary_reasoning_effort"], "default")
        field = next(f for f in self._settings()["schema"] if f["key"] == "primary_reasoning_effort")
        self.assertEqual(field["choices"], ["default", "low", "medium"])

    def test_refresh_metadata_uses_the_same_reasoning_fields_as_the_cache(self):
        descriptor = self.provider._descriptor("refreshed", "Refreshed", {
            "defaultReasoningEffort": "low", "supportedReasoningEfforts": [
                {"reasoningEffort": "low"}, {"reasoningEffort": "max"},
                {"reasoningEffort": "max"}, None, {"reasoningEffort": "invalid"}],
        })
        self.assertEqual(descriptor.reasoning_efforts, ["low", "max"])
        self.assertEqual(descriptor.to_dict()["default_reasoning_effort"], "low")


if __name__ == "__main__":
    unittest.main()
