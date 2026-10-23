"""Settings, model role resolution, fallback policy and credential isolation."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
import unittest.mock
from pathlib import Path

import sqlite3
from datetime import datetime, timedelta, timezone

from support import TemporaryAgent

#: Token-shaped fixtures, assembled at import rather than written out whole.
#:
#: These tests prove the application redacts anything of this shape, so the
#: shape has to be realistic - which is exactly what a secret scanner looks
#: for, and a repository host will block a push over one. The value at run
#: time is what the tests exercise; splitting the literal changes nothing
#: about the test and removes a false positive from the source.
_TOKEN_PREFIX = "sk-" + "ant-" + "oat01-"
FAKE_OAUTH_TOKEN = _TOKEN_PREFIX + "EXAMPLETOKENVALUE1234567890abcdef"
FAKE_OAUTH_NOISE = _TOKEN_PREFIX + "AAAABBBBCCCCDDDDEEEEFFFFGGGG"

from personalized_agent.common.config import schema
from personalized_agent.common.events.models import ScheduledTask
from personalized_agent.data_manage.storage.database import ADDED_COLUMNS, SCHEMA_SQL
from personalized_agent.common.logging.setup import SECRETS, RedactionFilter
from personalized_agent.models.providers.claude_code_provider import UNREACHABLE
from personalized_agent.models.base import (
    AuthMethod,
    AvailabilityState,
    FailureClass,
    ModelAvailability,
    ModelRequest,
)
from personalized_agent.models.manager import ModelUnavailable


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.config = self.agent.app.config

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_values_are_coerced_to_the_declared_type(self) -> None:
        self.assertEqual(self.config.set("font_size", "18"), 18)
        self.assertIs(self.config.set("heartbeat_enabled", "false"), False)
        self.assertEqual(self.config.set("listener_poll_interval_seconds", "3"), 3.0)

    def test_out_of_range_values_are_clamped(self) -> None:
        self.assertEqual(self.config.set("font_size", 900), 32)
        self.assertEqual(self.config.set("heartbeat_interval_seconds", 1), 60)

    def test_invalid_choice_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.config.set("log_level", "SHOUT")

    def test_settings_persist_across_instances(self) -> None:
        self.config.set("agent_name", "Nova")
        from personalized_agent.common.config.store import ConfigStore

        reopened = ConfigStore(self.agent.app.data_dir,
                               path=self.agent.app.paths.settings_file)
        self.assertEqual(reopened.get("agent_name"), "Nova")

    def test_changes_publish_an_event(self) -> None:
        seen = []
        self.agent.app.bus.subscribe("settings.changed", lambda t, p: seen.append(p))
        self.config.set("font_size", 20)
        self.assertEqual(seen[-1]["key"], "font_size")

    def test_schema_is_complete_for_every_default(self) -> None:
        defaults = schema.defaults()
        for field in schema.SETTINGS_SCHEMA:
            self.assertIn(field.key, defaults)
            self.assertTrue(field.label)
            self.assertTrue(field.section)

    def test_multiline_settings_are_read_as_lines(self) -> None:
        self.config.set("listener_application_denylist", "One\n\n Two \n")
        self.assertEqual(self.config.lines("listener_application_denylist"), ["One", "Two"])


class SettingExplanationTests(unittest.TestCase):
    """Section 12, as amended: no setting is presented without an explanation."""

    def setUp(self) -> None:
        self.schema = schema.SETTINGS_SCHEMA

    def test_every_setting_explains_itself(self) -> None:
        missing = [f.key for f in self.schema if not (f.description or "").strip()]
        self.assertEqual(missing, [], f"settings with no explanation: {missing}")

    def test_an_explanation_is_a_sentence_not_a_restated_label(self) -> None:
        for field in self.schema:
            with self.subTest(field.key):
                text = field.description.strip()
                self.assertGreaterEqual(len(text), 40,
                                        f"{field.key}: explanation is too short to say anything")
                self.assertNotEqual(text.rstrip(".").lower(), field.label.lower(),
                                    f"{field.key}: explanation only repeats the label")
                self.assertTrue(text.endswith((".", "!")),
                                f"{field.key}: explanation is not a complete sentence")

    def test_the_explanation_reaches_the_frontend(self) -> None:
        for entry in schema.schema_as_dicts():
            with self.subTest(entry["key"]):
                self.assertTrue(entry["description"].strip())

    def test_a_setting_that_cannot_be_edited_says_how_to_change_it(self) -> None:
        """Or says that it cannot be, which is the other honest answer.

        Two reasons a setting is not editable, and they need different words:
        one follows the agent root and is changed by moving that, the other is
        pinned in this version and is not changed at all. Telling somebody to
        relaunch with --root would be advice that does not work.
        """
        for field in self.schema:
            if not (field.readonly or field.fixed):
                continue
            with self.subTest(field.key):
                if field.fixed:
                    self.assertRegex(
                        field.description, r"cannot be changed|fixed",
                        f"{field.key}: pinned but never says so")
                else:
                    self.assertRegex(
                        field.description,
                        r"--root|PERSONALIZED_AGENT_HOME|agent root",
                        f"{field.key}: read-only but never says how it is changed")

    def test_the_settings_api_carries_every_explanation(self) -> None:
        agent = TemporaryAgent()
        try:
            payload = {f["key"]: f for f in agent.app.config.schema()}
        finally:
            agent.cleanup()
        self.assertEqual(len(payload), len(self.schema))
        blank = [k for k, f in payload.items() if not (f.get("description") or "").strip()]
        self.assertEqual(blank, [])

class ModelManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.models = self.app.models

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_roles_default_to_the_primary_model(self) -> None:
        for role in ("primary", "heartbeat", "scheduled_task"):
            resolved = self.models.resolve(role)
            self.assertEqual(resolved.label, "offline/offline-1")
            self.assertEqual(resolved.is_override, role == "primary" and False)

    def test_subsystem_override_is_used_when_configured(self) -> None:
        self.app.config.set("heartbeat_model_override", "offline/offline-1")
        resolved = self.models.resolve("heartbeat")
        self.assertTrue(resolved.is_override)

    def test_qualified_model_choice_selects_its_provider_too(self) -> None:
        # The Settings model picker stores provider/model values. Treating the
        # provider as unchanged turns a deliberate Codex selection into an
        # invalid offline/model pair, which reconciliation then resets.
        self.app.config.set("primary_model", "codex/default")
        corrected = self.models.reconcile_primary()
        self.assertEqual(self.app.config.get("primary_provider"), "codex")
        self.assertEqual(self.app.config.get("primary_model"), "default")
        self.assertEqual(corrected["primary_provider"], "codex")

    def test_a_codex_model_choice_keeps_its_explicit_model(self) -> None:
        self.app.config.set("primary_model", "codex/gpt-6-astra")
        corrected = self.models.reconcile_primary()
        self.assertEqual(self.app.config.get("primary_provider"), "codex")
        self.assertEqual(self.app.config.get("primary_model"), "gpt-6-astra")
        self.assertEqual(corrected["primary_model"], "gpt-6-astra")

    def test_malformed_override_falls_back_to_primary(self) -> None:
        self.app.config.set("heartbeat_model_override", "not-a-model")
        self.assertFalse(self.models.resolve("heartbeat").is_override)

    def test_unknown_provider_falls_back_to_offline(self) -> None:
        self.app.config.set("primary_provider", "does-not-exist")
        self.assertEqual(self.models.resolve("primary").provider_id, "offline")

    def test_no_silent_fallback_when_disabled(self) -> None:
        blocked = ModelAvailability(available=False,
                                    state=AvailabilityState.QUOTA_EXHAUSTED,
                                    reason="simulated",
                                    failure_class=FailureClass.USER_ACTION_REQUIRED)
        self.models.check_availability = lambda role="primary", force=False: blocked
        with self.assertRaises(ModelUnavailable):
            self.models.invoke(ModelRequest(messages=[{"role": "user", "content": "hi"}]))

    def test_fallback_is_used_only_when_explicitly_enabled(self) -> None:
        blocked = ModelAvailability(available=False,
                                    state=AvailabilityState.QUOTA_EXHAUSTED,
                                    reason="simulated",
                                    failure_class=FailureClass.USER_ACTION_REQUIRED)
        self.models.check_availability = lambda role="primary", force=False: blocked
        self.app.config.set("model_fallback_enabled", True)
        self.app.config.set("model_fallback_target", "offline/offline-1")
        response = self.models.invoke(ModelRequest(messages=[{"role": "user", "content": "hi"}]))
        self.assertTrue(response.text)

    def test_state_transitions_emit_events(self) -> None:
        topics = []
        self.app.bus.subscribe("model.*", lambda topic, payload: topics.append(topic))
        self.models.check_availability("primary", force=True)
        self.assertIn("model.available", topics)

    def test_invocation_records_observability_metrics(self) -> None:
        self.models.invoke(ModelRequest(messages=[{"role": "user", "content": "hi"}],
                                        task="unit_test"))
        metrics = self.app.observability.snapshot()["model_metrics"]
        self.assertGreater(metrics["totals"]["calls"], 0)
        self.assertIn("unit_test", metrics["by_task"])

    def test_describe_reports_availability_without_secrets(self) -> None:
        self.app.credentials.set("anthropic.api_key", "sk-ant-should-not-appear")
        payload = json.dumps(self.models.describe())
        self.assertNotIn("sk-ant-should-not-appear", payload)
        self.assertIn("providers", payload)


class CredentialIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_stored_secrets_are_redacted_from_logs(self) -> None:
        secret = "sk-ant-super-secret-token-value"
        self.app.credentials.set("anthropic.api_key", secret)
        record = logging.LogRecord("test", logging.INFO, __file__, 1,
                                   "using key %s", (secret,), None)
        RedactionFilter().filter(record)
        self.assertNotIn(secret, record.getMessage())

    def test_pattern_based_redaction_catches_unregistered_keys(self) -> None:
        text = SECRETS.scrub('{"api_key": "abcdef1234567890"}')
        self.assertNotIn("abcdef1234567890", text)

    def test_credentials_are_not_stored_in_the_memory_library(self) -> None:
        # Section 97: and now the library is files the user can open, which
        # makes a leaked key worse rather than better.
        self.app.credentials.set("anthropic.api_key", "sk-ant-graph-check")
        for record in self.app.library.records():
            self.assertNotIn("sk-ant-graph-check", record.body)
            self.assertNotIn("sk-ant-graph-check", record.summary)
        described = self.app.credentials.describe()
        self.assertNotIn("sk-ant-graph-check", json.dumps(described))

    def test_instances_do_not_share_a_keychain_namespace(self) -> None:
        """The OS keychain is global: two installations must stay separate."""
        from personalized_agent.models.credentials import SERVICE_NAME, service_name_for

        from personalized_agent.common.paths import default_data_dir

        default = service_name_for(Path(default_data_dir()) / "credentials")
        self.assertEqual(default, SERVICE_NAME)
        other = service_name_for(self.app.paths.credentials_dir)
        self.assertNotEqual(other, default)
        self.assertTrue(other.startswith(f"{SERVICE_NAME}:"))

    def test_keyring_can_be_disabled(self) -> None:
        from personalized_agent.models.credentials import CredentialManager

        manager = CredentialManager(self.app.paths.credentials_dir, use_keyring=False)
        self.assertIn(manager.backend, ("encrypted-file", "plaintext-file"))

    def test_tests_never_touch_the_real_keychain(self) -> None:
        self.assertNotEqual(self.app.credentials.backend, "keyring")

    def test_credential_storage_mode_is_configurable(self) -> None:
        """The user can choose a self-contained root (section 110.1)."""
        from personalized_agent.common.config import schema

        field = schema.SCHEMA_BY_KEY["credential_storage"]
        self.assertEqual(field.choices, ["keychain", "agent_root"])
        self.assertEqual(field.default, "keychain")
        self.assertIn("does NOT travel", field.description)

    def test_a_fresh_install_reports_no_anthropic_credential(self) -> None:
        provider = self.app.models.provider("anthropic")
        status = provider.get_auth_status()
        self.assertEqual(status.state.value, "not_configured")

    def test_account_login_is_not_offered_when_it_cannot_be_completed(self) -> None:
        """Sections 75.1 and 77: never advertise a method that always dead-ends."""
        from personalized_agent.models.providers import anthropic_provider

        provider = self.app.models.provider("anthropic")
        provider._ambient_credential = lambda: None
        provider._cli_available = staticmethod(lambda: None)
        # Whatever this machine has installed: no tool here can finish it.
        with unittest.mock.patch.object(anthropic_provider, "available_backend",
                                        lambda: None):
            methods = [m.value for m in provider.get_auth_methods()]
            self.assertEqual(methods, ["api_key"])
            self.assertFalse(provider.can_initiate_login())

    def test_account_login_is_not_offered_when_no_tool_can_finish_it(self) -> None:
        from personalized_agent.models.providers import anthropic_provider

        provider = self.app.models.provider("anthropic")
        provider._ambient_credential = lambda: None
        original = anthropic_provider.available_backend
        anthropic_provider.available_backend = lambda: None
        try:
            self.assertNotIn("account_login", [m.value for m in provider.get_auth_methods()])
        finally:
            anthropic_provider.available_backend = original

    def test_account_login_detects_an_ambient_credential(self) -> None:
        provider = self.app.models.provider("anthropic")
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-ambient-detection-only"
        try:
            self.assertEqual(provider._ambient_credential()["source"], "environment")
            self.assertEqual(provider.get_auth_status().state.value, "authenticated")
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    def test_discovery_never_touches_the_network_without_a_credential(self) -> None:
        """A Settings or Debugger refresh must not open a connection to find models."""
        provider = self.app.models.provider("anthropic")

        def forbidden():
            raise AssertionError("the provider opened a client without a credential")

        provider._get_client = forbidden
        provider._ui_client = forbidden
        provider._models = None
        self.assertTrue(provider.list_models())          # returns the catalogue
        self.assertTrue(self.app.models.describe())      # and so does the UI payload

    def test_a_discovery_failure_is_cached(self) -> None:
        """A broken connection costs the timeout once, not on every refresh."""
        provider = self.app.models.provider("anthropic")
        provider._models = None
        provider._discovery_failed_at = 0.0
        provider.get_auth_status = lambda: __import__(
            "personalized_agent.models.base", fromlist=["AuthStatus"]
        ).AuthStatus(
            __import__("personalized_agent.models.base",
                       fromlist=["CredentialState"]).CredentialState.AUTHENTICATED
        )
        calls = []

        def failing_client():
            calls.append(1)
            raise TimeoutError("simulated network failure")

        provider._ui_client = failing_client
        self.assertTrue(provider.list_models())
        self.assertTrue(provider.list_models())
        self.assertEqual(len(calls), 1, "the failed discovery was retried on every call")

    def test_ui_calls_are_bounded(self) -> None:
        from personalized_agent.models.providers.anthropic_provider import (
            DISCOVERY_RETRY_SECONDS,
            UI_TIMEOUT_SECONDS,
        )

        self.assertLessEqual(UI_TIMEOUT_SECONDS, 10)
        self.assertGreaterEqual(DISCOVERY_RETRY_SECONDS, 30)

        provider = self.app.models.provider("anthropic")
        captured = {}

        class FakeClient:
            def with_options(self, **kwargs):
                captured.update(kwargs)
                return self

        provider._get_client = lambda: FakeClient()
        provider._ui_client()
        self.assertEqual(captured.get("max_retries"), 0)
        self.assertEqual(captured.get("timeout"), UI_TIMEOUT_SECONDS)

    def test_credentials_survive_a_reopen(self) -> None:
        self.app.credentials.set("test.key", "value-123")
        from personalized_agent.models.credentials import CredentialManager

        reopened = CredentialManager(self.app.paths.credentials_dir, use_keyring=False)
        self.assertEqual(reopened.get("test.key"), "value-123")

    def test_credentials_can_live_inside_the_root(self) -> None:
        """Section 110.1: a copied root can carry its own credentials."""
        from personalized_agent.models.credentials import CredentialManager

        manager = CredentialManager(self.app.paths.credentials_dir, use_keyring=False)
        manager.set("portable.key", "value-in-root")
        stored = list(self.app.paths.credentials_dir.iterdir())
        self.assertTrue(stored, "nothing was written inside the root")
        self.assertTrue(
            all(f.is_relative_to(self.app.root) for f in stored)
        )


class ChoosingAProviderTests(unittest.TestCase):
    """Section 82, as amended: choosing is done where providers are shown.

    A provider listed as signed in with no way to use it is not a choice; the
    user concludes the thing they just set up does not work.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_panel_offers_an_action_for_each_provider(self) -> None:
        source = (Path(__file__).resolve().parents[1]
                  / "personalized_agent" / "api" / "web" / "app.js").read_text(encoding="utf-8")
        # The list in order: added to, moved up and down, removed from.
        for said in ("/api/models/chain", "model-add-open", "data-chain-up",
                     "data-chain-down", "data-chain-remove"):
            self.assertIn(said, source)

    def test_the_last_model_can_be_removed_and_stays_removed(self) -> None:
        from personalized_agent.models.base import ModelRequest
        from personalized_agent.models.manager import ModelUnavailable
        from personalized_agent.api.routes import build_routes

        class _Req:
            def __init__(self, body):
                self.body = body

            def get(self, key, default=None):
                return self.body.get(key, default)
        routes = build_routes(self.app)
        routes[("POST", "/api/models/chain")](
            _Req({"chain": [{"provider": "offline", "model": "offline-1"}]}))
        self.assertEqual(len(self.app.models.chain()), 1)
        routes[("POST", "/api/models/chain")](_Req({"chain": []}))
        # Empty - not the model from before the list, back as if never removed.
        self.assertEqual(self.app.models.chain(), [])
        self.assertEqual(self.app.models.describe()["chain"], [])
        with self.assertRaises(ModelUnavailable) as said:
            self.app.models.invoke(ModelRequest(messages=[{"role": "user",
                                                           "content": "hello"}]))
        self.assertIn("no model on your list", said.exception.availability.reason)
        # And one added again is used.
        routes[("POST", "/api/models/chain")](
            _Req({"chain": [{"provider": "offline", "model": "offline-1"}]}))
        self.assertEqual(len(self.app.models.chain()), 1)
        source = (Path(__file__).resolve().parents[1]
                  / "personalized_agent" / "api" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('chain.length === 1 ? " disabled"', source)

    def test_the_listing_says_which_provider_is_in_use(self) -> None:
        described = self.app.models.describe()
        in_use = described["roles"]["primary"]["provider"]
        self.assertIn(in_use, {p["provider"] for p in described["providers"]},
                      "the provider in use must be one of those listed, or the "
                      "interface cannot mark it")

    def test_choosing_a_provider_brings_its_model_with_it(self) -> None:
        # This is what the action does; the model must follow, or the user gets
        # a 404 naming a model they never chose.
        before = self.app.config.get("primary_model")
        self.app.config.set("primary_provider", "claude_code")
        corrected = self.app.models.reconcile_primary()
        self.assertIsNotNone(corrected, "changing the provider must move the model")
        after = self.app.config.get("primary_model")
        self.assertNotEqual(before, after)
        self.assertTrue(self.app.models.model_belongs_to_provider(
            "claude_code", after), f"{after} does not belong to claude_code")


class ChoosingTheLocalClientTests(unittest.TestCase):
    """Section 76.1: which client answers is chosen, and absence is reported."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.provider = self.app.models.provider("claude_code")

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_a_client_that_is_not_installed_is_still_offered(self) -> None:
        # Hiding it means the option the user expected is simply missing, and
        # they cannot tell whether the application knows about it at all.
        listed = {row["client"] for row in self.provider.clients()}
        self.assertEqual(listed, {"cli", "desktop"})

    def _provider_of(self, client: str):
        """Each client is a model of its own: the command line, the desktop app."""
        return self.app.models.provider({"cli": "claude_code", "desktop": "claude_desktop"}[client])

    def test_choosing_an_absent_client_says_so_and_says_where_to_get_it(self) -> None:
        absent = next((row["client"] for row in self.provider.clients()
                       if not row["installed"]), None)
        if absent is None:
            self.skipTest("every client is installed on this machine")
        provider = self._provider_of(absent)
        detail = provider.get_auth_status().detail
        self.assertIn("not installed", detail)
        self.assertIn("http", detail, "it must say where to get it")
        reason = provider.check_availability("sonnet").reason
        self.assertIn("not installed", reason)

    def test_an_absent_choice_is_never_replaced_by_another_client(self) -> None:
        # Quietly answering from a different client bills an account the user
        # did not choose, and the interface would still say "signed in".
        installed = [r for r in self.provider.clients() if r["installed"]]
        absent = [r for r in self.provider.clients() if not r["installed"]]
        if not installed or not absent:
            self.skipTest("this machine cannot show the distinction")
        self.assertIsNone(self._provider_of(absent[0]["client"]).executable())

    def test_the_report_names_the_client_that_was_asked(self) -> None:
        installed = [r for r in self.provider.clients() if r["installed"]]
        if not installed:
            self.skipTest("no Claude client is installed here")
        detail = self._provider_of(installed[0]["client"]).get_auth_status().detail or ""
        if "signed in" in detail:
            self.assertIn(installed[0]["display_name"], detail)

    def test_a_client_elsewhere_is_named_by_its_program_file(self) -> None:
        self.app.config.set("local_client_path", str(Path(self.agent.dir) / "nope.exe"))
        detail = self.provider.get_auth_status().detail
        self.assertIn("nope.exe", detail)
        # And a real file at that path is used by the command line's model.
        stand_in = Path(self.agent.dir) / "elsewhere.exe"
        stand_in.write_text("", encoding="utf-8")
        self.app.config.set("local_client_path", str(stand_in))
        self.assertEqual(Path(self.provider.executable() or ""), stand_in)
        # Never by the desktop application's.
        self.assertNotEqual(Path(self._provider_of("desktop").executable() or "."), stand_in)

    def test_a_program_named_otherwise_in_case_is_not_the_client(self) -> None:
        # A Mac's disk ignores case: Claude.app/Contents/MacOS/claude was the
        # desktop application `Claude`, asked again and again whether it was
        # signed in and never answering.
        from personalized_agent.models.providers import claude_code_provider as module
        bundle = Path(self.agent.dir) / "Claude.app" / "Contents" / "MacOS"
        bundle.mkdir(parents=True)
        (bundle / "Claude").write_text("", encoding="utf-8")
        spec = dict(module.CLIENTS["desktop"], candidates=[str(bundle / "claude")])
        with unittest.mock.patch.dict(module.CLIENTS, {"desktop": spec}):
            self.assertIsNone(module.ClaudeCodeProvider._locate("desktop"))
            spec["candidates"] = [str(bundle / "Claude")]
            self.assertEqual(module.ClaudeCodeProvider._locate("desktop"),
                             str(bundle / "Claude"))

    def test_the_settings_offer_the_choice(self) -> None:
        keys = {field["key"] for field in self.app.config.schema()}
        self.assertIn("local_client_path", keys)
        field = next(f for f in self.app.config.schema() if f["key"] == "local_client_path")
        # Section 12, as amended: a program is chosen, not typed.
        self.assertEqual(field["chooser"], "application")


class SignInProbeTests(unittest.TestCase):
    """Section 76.1: asking a client whether it is signed in has three answers.

    Signed in, not signed in, and *could not ask*. Reporting the third as the
    second sent the user looking for a sign-in problem that did not exist.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.provider = self.app.models.provider("claude_code")
        # The client is installed, whether or not this machine has it: what
        # is asked here is what its answer means, not whether it is there.
        self.provider._resolve = lambda: ("cli", "claude", None)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_one_clients_answer_is_never_reused_for_another(self) -> None:
        # The defect: the cached answer was not tied to the client it
        # described, so selecting an absent client and then a present one
        # reported the present one as signed out for a full minute.
        asked = []

        def fake(cli):
            asked.append(cli)
            return {"loggedIn": True} if cli else {}

        self.provider._ask_client = fake
        self.provider._resolve = lambda: ("desktop", None, "not installed")
        self.provider._auth_status()
        self.provider._resolve = lambda: ("cli", "claude", None)
        status = self.provider._auth_status()
        self.assertTrue(status.get("loggedIn"),
                        "the absent client's answer was reused for a present one")
        self.assertEqual(asked, [None, "claude"], "the second client was never asked")

    def test_the_same_client_is_not_asked_twice_in_a_minute(self) -> None:
        # The cache still has to do its job: this probe starts a process, and
        # the panels that read it refresh on a timer.
        calls = []
        self.provider._ask_client = lambda cli: calls.append(cli) or {"loggedIn": True}
        self.provider._resolve = lambda: ("cli", "claude", None)
        self.provider._auth_status()
        self.provider._auth_status()
        self.assertEqual(len(calls), 1)

    def test_a_probe_that_could_not_run_is_not_reported_as_signed_out(self) -> None:
        self.provider._ask_client = lambda cli: {UNREACHABLE: "it could not be run"}
        self.provider._status_cache = None
        detail = self.provider.get_auth_status().detail or ""
        self.assertIn("could not ask", detail)
        self.assertNotIn("not signed in", detail)

    def test_a_probe_that_could_not_run_does_not_block_an_answer(self) -> None:
        # The client itself is the authority. Failing to ask it is no reason
        # to refuse the user, and refusing was the visible symptom.
        self.provider._ask_client = lambda cli: {UNREACHABLE: "it could not be run"}
        self.provider._status_cache = None
        verdict = self.provider.check_availability("sonnet")
        self.assertTrue(verdict.available)
        self.assertFalse(verdict.requires_user_action())

    def test_a_client_that_says_it_is_signed_out_is_still_reported_that_way(self) -> None:
        # The distinction must not swallow the real, actionable case.
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="Not logged in. Run /login", stderr="")
        self.provider.executable = lambda: "claude"
        with unittest.mock.patch("personalized_agent.common.children.run", return_value=completed):
            status = self.provider._ask_client("claude")
        self.assertEqual(status, {})
        self.assertNotIn(UNREACHABLE, status)


class InvocationConstraints(unittest.TestCase):
    """Section 76.1: how a local client is actually invoked.

    Each of these was a real failure that presented as the agent simply not
    answering, so each is asserted rather than left to review.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.provider = self.agent.app.models.provider("claude_code")

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _command(self, prompt: str = "hello"):
        request = ModelRequest(messages=[{"role": "user", "content": prompt}])
        return self.provider._command(request, "sonnet")

    def test_the_prompt_never_travels_on_the_command_line(self) -> None:
        # A command line has a length limit; a conversation does not. Passing
        # the prompt as an argument fails for whoever has talked longest.
        long_prompt = "x" * 5000
        command = self._command(long_prompt)
        self.assertNotIn(long_prompt, command)
        self.assertLess(len(" ".join(command)), 2000)
        self.assertIn("-p", command)
        # -p must not be given a value: the prompt arrives on stdin.
        following = command[command.index("-p") + 1]
        self.assertTrue(following.startswith("--"), f"-p was given {following!r}")

    def test_the_denied_tools_are_named_and_recognised(self) -> None:
        from personalized_agent.models.providers.claude_code_provider import DENIED_TOOLS
        command = self._command()
        self.assertIn("--disallowed-tools", command)
        for tool in ("Bash", "Write", "Edit", "Read", "Task"):
            self.assertIn(tool, DENIED_TOOLS)
        # An empty name is exactly what makes the client discard the flag.
        self.assertTrue(all(t and not t.startswith("-") for t in DENIED_TOOLS))

    def test_nothing_bypasses_the_clients_own_permission_check(self) -> None:
        # The real guarantee, ahead of any list: no mode in which a tool can
        # be approved.
        self.assertNotIn("bypassPermissions", self._command())
        self.assertNotIn("--permission-mode", self._command())

    def test_the_run_is_named_so_the_listener_can_tell_it_is_ours(self) -> None:
        """Section 18, as amended: the agent must not hear its own voice.

        The client writes a transcript of every run into its own session
        store, and that store is what the assistant listener reads. Left
        unnamed, each model call arrived back a moment later as activity at
        the user's desk - the relabeller asking for keywords, the data
        heartbeat asking what some raw activity meant - attributed to
        whichever window was in front while the agent worked.

        The name travels on the session id, because the id is the one thing
        that passes from the run to the transcript and is still there when a
        later process reads it.
        """
        from personalized_agent.common.agent_sessions import (
            is_agent_session, new_session_id)

        chosen = new_session_id()
        command = self.provider._command(
            ModelRequest(messages=[{"role": "user", "content": "hi"}]),
            "sonnet", chosen)
        self.assertEqual(command[command.index("--session-id") + 1], chosen)
        self.assertTrue(is_agent_session(chosen))

    def test_every_run_invoked_gets_its_own_marked_session(self) -> None:
        # Reusing one id would make the client refuse the second run, and
        # leaving it off would put the run back in the user's activity.
        from personalized_agent.common.agent_sessions import is_agent_session

        seen = []

        def record(command, **kwargs):
            seen.append(command)
            return subprocess.CompletedProcess(
                args=command, returncode=0,
                stdout=json.dumps({"result": "ok", "session_id": "ignored"}),
                stderr="")

        with unittest.mock.patch("personalized_agent.common.children.run", record),                 unittest.mock.patch.object(self.provider, "executable",
                                           return_value="claude"):
            self.provider.invoke(ModelRequest(messages=[
                {"role": "user", "content": "one"}]), "sonnet")
            self.provider.invoke(ModelRequest(messages=[
                {"role": "user", "content": "two"}]), "sonnet")
        ids = [c[c.index("--session-id") + 1] for c in seen]
        self.assertEqual(len(set(ids)), 2, "a run may not reuse another's session")
        self.assertTrue(all(is_agent_session(i) for i in ids))

    def test_a_failure_on_the_output_stream_is_still_reported(self) -> None:
        # Auth failures arrive on stdout with a non-zero exit; reading only
        # stderr reports them as an empty failure that tells nobody anything.
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="Invalid API key - please run /login", stderr="")
        verdict = self.provider._translate(completed)
        self.assertIn("login", (verdict.reason or "").lower())
        self.assertEqual(verdict.state, AvailabilityState.AUTHENTICATION_REQUIRED)

    def test_output_that_is_valid_json_but_not_the_envelope(self) -> None:
        # A bare JSON string parses without complaint and then fails on the
        # first field read; it must be reported, not raise AttributeError.
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout='"just a string"', stderr="")
        self.assertIsNotNone(self.provider._translate(completed).reason)

if __name__ == "__main__":
    unittest.main()


FAKE_CLI = '''import sys
if sys.argv[1:3] != ["auth", "login"]:
    raise SystemExit(2)
print("Open the following URL to authorize:")
print("https://example.invalid/oauth/authorize?state=test")
sys.stdout.flush()
code = sys.stdin.readline().strip()
if code == "good-code":
    print("Login successful.")
    raise SystemExit(0)
print("Invalid code")
raise SystemExit(1)
'''


class AccountLoginTests(unittest.TestCase):
    """Sections 75.1 and 75.2: the sign-in happens in this application."""


    def test_the_api_takes_a_key_and_no_account_sign_in(self) -> None:
        """A Claude subscription goes through Claude Code; the Anthropic API
        takes a key - no sign-in is offered, one asked for is refused, and
        a token stored by an earlier sign-in is not used."""
        from personalized_agent.models.base import AuthMethod, CredentialState
        from personalized_agent.models.providers import anthropic_provider as ap
        # Even with a sign-in tool on the machine.
        provider = self._provider(True)
        self.assertEqual(provider.get_auth_methods(), [AuthMethod.API_KEY])
        self.assertEqual([one.method for one in provider.describe_auth_methods()],
                         [AuthMethod.API_KEY])
        refused = provider.authenticate({"method": "account_login"})
        self.assertEqual(refused.state, CredentialState.ERROR)
        self.assertIn("Claude Code", refused.detail)
        provider.credentials.set(ap.OAUTH_TOKEN_CREDENTIAL, "a-subscription-token")
        self.assertIsNone(provider._account_token())
        self.assertNotEqual(provider.get_auth_status().state, CredentialState.AUTHENTICATED)

    def setUp(self) -> None:
        from personalized_agent.models.credentials import CredentialManager

        self.dir = Path(tempfile.mkdtemp(prefix="pa-login-"))
        (self.dir / "fake_cli.py").write_text(FAKE_CLI, encoding="utf-8")
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        if os.name == "nt":
            (self.bin / "ant.bat").write_text(
                f'@echo off\r\n"{sys.executable}" "{self.dir / "fake_cli.py"}" %*\r\n',
                encoding="utf-8")
        else:
            script = self.bin / "ant"
            script.write_text(f'#!/bin/sh\nexec "{sys.executable}" '
                              f'"{self.dir / "fake_cli.py"}" "$@"\n', encoding="utf-8")
            script.chmod(0o755)
        self._path = os.environ["PATH"]
        self._env = {k: os.environ.pop(k, None)
                     for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        self.credentials = CredentialManager(self.dir / "creds", use_keyring=False)

    def tearDown(self) -> None:
        os.environ["PATH"] = self._path
        for key, value in self._env.items():
            if value is not None:
                os.environ[key] = value
        shutil.rmtree(self.dir, ignore_errors=True)

    def _provider(self, with_cli: bool):
        from personalized_agent.models.providers.anthropic_provider import AnthropicProvider

        # Exclusive, never prepended: a real provider tool on the machine's own
        # PATH would otherwise be picked ahead of the stand-in and start a real
        # sign-in.
        os.environ["PATH"] = str(self.bin if with_cli else self.dir / "empty")
        return AnthropicProvider(self.credentials)

    # -- disclosure (section 77, as amended) --------------------------------
    def test_signing_in_returns_a_browser_url_and_asks_for_the_code(self) -> None:
        provider = self._provider(with_cli=True)
        started = provider.start_account_login(open_browser=False)
        self.assertEqual(started["state"], "awaiting_authorization")
        self.assertTrue(started["authorization_url"].startswith("https://"))
        self.assertTrue(started["awaiting_code"])
        self.assertFalse(started["finished"])
        provider.cancel_account_login()

    def test_the_code_completes_the_sign_in(self) -> None:
        provider = self._provider(with_cli=True)
        provider.start_account_login(open_browser=False)
        result = provider.submit_account_login_code("good-code")
        self.assertEqual(result["state"], "completed")
        self.assertTrue(result["finished"])

    def test_a_rejected_code_fails_with_the_reason(self) -> None:
        provider = self._provider(with_cli=True)
        provider.start_account_login(open_browser=False)
        result = provider.submit_account_login_code("wrong")
        self.assertEqual(result["state"], "failed")
        self.assertTrue(result["detail"])
        self.assertTrue(result["finished"])

    def test_the_sign_in_can_be_cancelled(self) -> None:
        provider = self._provider(with_cli=True)
        provider.start_account_login(open_browser=False)
        self.assertEqual(provider.cancel_account_login()["state"], "cancelled")

    def test_the_flow_never_sends_the_user_to_a_terminal(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "personalized_agent" / "models" /
                  "providers" / "account_login.py").read_text(encoding="utf-8")
        for terminal in ("gnome-terminal", "xterm", "konsole", "osascript",
                         "x-terminal-emulator", 'cmd", "/k'):
            self.assertNotIn(terminal, source,
                             "section 75.2: the sign-in is hosted in the application")

    def test_no_credential_material_is_returned_to_the_caller(self) -> None:
        provider = self._provider(with_cli=True)
        provider.start_account_login(open_browser=False)
        payload = json.dumps(provider.submit_account_login_code("good-code"))
        self.assertNotIn("token", payload.lower())
        self.assertNotIn("secret", payload.lower())

CRLF = chr(13) + chr(10)
LF = chr(10)


class AccountLoginBackendTests(unittest.TestCase):
    """Section 75.2: whichever provider tool is present performs the sign-in."""

    def setUp(self) -> None:
        from personalized_agent.models.providers import account_login

        self.module = account_login
        self._path = os.environ["PATH"]
        self.dir = Path(tempfile.mkdtemp(prefix="pa-backend-"))
        (self.dir / "empty").mkdir()

    def tearDown(self) -> None:
        os.environ["PATH"] = self._path
        shutil.rmtree(self.dir, ignore_errors=True)

    def _stub(self, name: str) -> Path:
        binaries = self.dir / name
        binaries.mkdir(exist_ok=True)
        target = binaries / (f"{name}.bat" if os.name == "nt" else name)
        if os.name == "nt":
            target.write_text("@echo off" + CRLF + "exit /b 0" + CRLF, encoding="utf-8")
        else:
            target.write_text("#!/bin/sh" + LF + "exit 0" + LF, encoding="utf-8")
            target.chmod(0o755)
        return binaries

    def test_more_than_one_tool_can_perform_the_sign_in(self) -> None:
        keys = {backend.key for backend in self.module.BACKENDS}
        self.assertIn("claude_code", keys)
        self.assertIn("anthropic_cli", keys)

    def test_claude_code_is_recognised(self) -> None:
        os.environ["PATH"] = str(self._stub("claude"))
        described = {t["key"]: t for t in self.module.describe_backends()}
        self.assertTrue(described["claude_code"]["installed"])
        self.assertFalse(described["anthropic_cli"]["installed"])

    def test_each_tool_states_what_the_credential_draws_on(self) -> None:
        for tool in self.module.describe_backends():
            with self.subTest(tool["key"]):
                self.assertTrue(tool["account_note"],
                                "the interface must say which account and billing is used")
                self.assertTrue(tool["install_url"])

    def test_no_tool_at_all_is_explained_and_remedied(self) -> None:
        os.environ["PATH"] = str(self.dir / "empty")
        self.assertIsNone(self.module.available_backend())
        reason = self.module.account_login_unavailable_reason()
        self.assertIn("Claude Code", reason)
        remedy, url = self.module.missing_tool_remedy()
        self.assertTrue(remedy)
        self.assertTrue(url.startswith("https://"))

    def test_a_terminal_only_tool_needs_a_hosted_terminal(self) -> None:
        """Section 75.2: the application hosts the terminal, or says it cannot."""
        cli = next(b for b in self.module.BACKENDS if b.key == "anthropic_cli")
        claude = next(b for b in self.module.BACKENDS if b.key == "claude_code")
        self.assertTrue(claude.needs_terminal)
        self.assertFalse(cli.needs_terminal)

    def test_a_tool_whose_credential_is_refused_is_not_offered(self) -> None:
        """Section 76.1: a credential the API turns away is not a way in."""
        claude = next(b for b in self.module.BACKENDS if b.key == "claude_code")
        self.assertFalse(claude.yields_api_credential)
        os.environ["PATH"] = str(self._stub("claude"))
        self.assertFalse(claude.usable(), "a refused credential was offered as a sign-in")
        reason = self.module.account_login_unavailable_reason()
        self.assertIn("refused", reason)

    def test_a_following_hint_is_never_glued_onto_the_url(self) -> None:
        """The real failure: the tool prints a hint under the URL."""
        url = ("https://claude.com/cai/oauth/authorize?code=true&client_id=abc"
               "&response_type=code&code_challenge=xyz&state=d2lVtrTdGUUUfM")
        noisy = ("Open this:" + LF + url + LF + LF
                 + "HoldShiftwhileselectingtouseyourterminal" + LF)
        self.assertEqual(self.module._first_url(noisy), url)

    def test_a_url_that_filled_the_line_is_not_handed_over_truncated(self) -> None:
        """A wrapped URL is incomplete, and guessing the rest corrupts it."""
        exact = "https://example.invalid/a?" + ("x" * (40 - len("https://example.invalid/a?")))
        self.assertEqual(len(exact), 40)
        self.assertIsNone(self.module._first_url(exact + LF + "more", columns=40))

    def test_terminal_control_sequences_are_stripped(self) -> None:
        esc = chr(27)
        noisy = (esc + "[2J" + esc + "[36mVisit" + esc + "[0m "
                 "https://example.invalid/authorize?x=1" + esc + "[0m")
        self.assertEqual(self.module._first_url(noisy),
                         "https://example.invalid/authorize?x=1")

    def test_a_minted_token_never_appears_in_reported_output(self) -> None:
        """Sections 83, 84: the token is a secret, including on failure."""
        noisy = f"minted {FAKE_OAUTH_NOISE} then failed"
        self.assertNotIn(_TOKEN_PREFIX.rstrip("-"), self.module._tail(noisy))
        self.assertIn("<token>", self.module._tail(noisy))


class AccountTokenStorageTests(unittest.TestCase):
    """A token the tool hands back is stored, used, and never returned."""

    def setUp(self) -> None:
        from personalized_agent.models.credentials import CredentialManager
        from personalized_agent.models.providers import anthropic_provider

        self.module = anthropic_provider
        self.dir = Path(tempfile.mkdtemp(prefix="pa-token-"))
        self.credentials = CredentialManager(self.dir, use_keyring=False)
        self.provider = anthropic_provider.AnthropicProvider(self.credentials)
        self._env = {k: os.environ.pop(k, None)
                     for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}

    def tearDown(self) -> None:
        for key, value in self._env.items():
            if value is not None:
                os.environ[key] = value
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_an_api_key_wins_over_an_account_token(self) -> None:
        """Two mechanisms must not silently blend into one another."""
        self.credentials.set(self.module.OAUTH_TOKEN_CREDENTIAL, "sk-ant-oat01-example")
        self.credentials.set(self.module.API_KEY_CREDENTIAL, "sk-ant-api-example")
        self.assertEqual(self.provider._api_key(), "sk-ant-api-example")

    def test_logging_out_removes_the_token_too(self) -> None:
        self.credentials.set(self.module.OAUTH_TOKEN_CREDENTIAL, "sk-ant-oat01-example")
        self.provider.logout()
        self.assertFalse(self.credentials.has(self.module.OAUTH_TOKEN_CREDENTIAL))

    def test_the_token_is_never_in_the_reported_status(self) -> None:
        self.credentials.set(self.module.OAUTH_TOKEN_CREDENTIAL, "sk-ant-oat01-secret-value")
        payload = json.dumps(self.provider.get_auth_status().to_dict())
        self.assertNotIn("secret-value", payload)
        self.assertNotIn("sk-ant", payload)

class _StubTransport:
    """Shaped like `claude setup-token`: prints a URL, takes a code, returns a
    token, and then keeps running."""

    name = "pty"

    def __init__(self, command, *, exits_after_token=False, token=True,
                 completes_by_itself=False):
        self.command = command
        self.sent = []
        self.stopped = False
        self._sink = None
        self._exits = exits_after_token
        self._token = token
        self._self_completes = completes_by_itself
        self._alive = True

    def read_stream(self, sink):
        self._sink = sink
        sink.put("Open the following URL:\n")
        sink.put("https://claude.com/cai/oauth/authorize?state=abc123\n")
        sink.put("Paste code here if prompted >\n")
        if self._self_completes:
            # The browser handed the result straight back, so no code is ever
            # asked for: the tool prints its token and exits.
            time.sleep(0.3)
            sink.put("Login successful.\n")
            sink.put(FAKE_OAUTH_TOKEN + "\n")
            self._alive = False
            sink.put(None)

    def write(self, text):
        self.sent.append(text)
        if self._token:
            self._sink.put("Login successful.\n")
            self._sink.put(FAKE_OAUTH_TOKEN + "\n")
        if self._exits:
            self._alive = False
            self._sink.put(None)

    def alive(self):
        return self._alive

    def wait(self, timeout):
        return 0 if not self._alive else None

    def stop(self):
        self.stopped = True
        self._alive = False


class AccountLoginCompletionTests(unittest.TestCase):
    """Section 75.2: the sign-in finishes, and finishes once."""

    def setUp(self) -> None:
        from personalized_agent.models.providers import account_login

        self.module = account_login
        self.made = []
        self.opened = []
        self._pty = account_login.PtyTransport
        self._browser = account_login.webbrowser
        self._available = account_login.available_backend
        account_login.webbrowser = types.SimpleNamespace(
            open=lambda url: self.opened.append(url))

    def tearDown(self) -> None:
        self.module.PtyTransport = self._pty
        self.module.webbrowser = self._browser
        self.module.available_backend = self._available

    def _use(self, *, opens_browser: bool, returns_token: bool, **stub) -> None:
        backend = types.SimpleNamespace(
            key="stub", executable="stub", display_name="Claude Code", arguments=(),
            needs_terminal=True, returns_token=returns_token,
            opens_browser=opens_browser, install_url="https://example.invalid",
            account_note="note", command=lambda: ["stub"], path=lambda: "stub",
            usable=lambda: True)
        self.module.available_backend = lambda: backend

        def make(command):
            transport = _StubTransport(command, **stub)
            self.made.append(transport)
            return transport

        self.module.PtyTransport = make

    def test_a_tool_that_opens_its_own_browser_is_not_opened_again(self) -> None:
        """Two authorization pages for one sign-in: only one carries the verifier."""
        self._use(opens_browser=True, returns_token=True)
        session = self.module.AccountLoginSession()
        session.start(open_browser=True)
        self.assertEqual(self.opened, [])

    def test_a_tool_that_does_not_open_a_browser_still_gets_one(self) -> None:
        self._use(opens_browser=False, returns_token=True)
        session = self.module.AccountLoginSession()
        session.start(open_browser=True)
        self.assertEqual(len(self.opened), 1)

    def test_the_sign_in_completes_when_the_token_arrives(self) -> None:
        """The tool stays alive afterwards, so waiting for exit would hang."""
        self._use(opens_browser=True, returns_token=True)
        session = self.module.AccountLoginSession()
        session.start(open_browser=False)
        began = time.monotonic()
        result = session.submit_code("code")
        self.assertEqual(result["state"], "completed")
        self.assertLess(time.monotonic() - began, 30,
                        "completion waited for an exit that never comes")
        self.assertTrue(session.take_token())
        self.assertTrue(self.made[0].stopped, "the tool was left running")

    def test_the_token_is_not_in_the_result(self) -> None:
        self._use(opens_browser=True, returns_token=True)
        session = self.module.AccountLoginSession()
        session.start(open_browser=False)
        payload = json.dumps(session.submit_code("code"))
        self.assertNotIn("sk-ant", payload)

    def test_a_tool_that_returns_no_token_is_a_failure_not_a_hang(self) -> None:
        self._use(opens_browser=True, returns_token=True, token=False,
                  exits_after_token=True)
        session = self.module.AccountLoginSession()
        session.start(open_browser=False)
        result = session.submit_code("code")
        self.assertEqual(result["state"], "failed")
        self.assertIn("without returning a credential", result["detail"])

    def test_a_store_writing_tool_completes_on_exit(self) -> None:
        """The other backend's signal is the process finishing, not a token."""
        self._use(opens_browser=False, returns_token=False, token=False,
                  exits_after_token=True)
        session = self.module.AccountLoginSession()
        session.start(open_browser=False)
        self.assertEqual(session.submit_code("code")["state"], "completed")

    def test_a_sign_in_the_browser_completes_needs_no_code(self) -> None:
        """The real case: the page says "you're all set up" and never shows a code."""
        self._use(opens_browser=True, returns_token=True, completes_by_itself=True)
        stored = []
        session = self.module.AccountLoginSession(on_token=stored.append)
        session.start(open_browser=False)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not session.describe()["finished"]:
            time.sleep(0.1)
        status = session.describe()
        self.assertEqual(status["state"], "completed",
                         "a sign-in the browser finished must not wait for a code")
        self.assertEqual(len(stored), 1, "the token was never collected")
        self.assertFalse(status["awaiting_code"])

    def test_pasting_into_an_already_completed_sign_in_is_not_an_error(self) -> None:
        """The user may paste something after it already finished."""
        self._use(opens_browser=True, returns_token=True, completes_by_itself=True)
        session = self.module.AccountLoginSession(on_token=lambda token: None)
        session.start(open_browser=False)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not session.describe()["finished"]:
            time.sleep(0.1)
        result = session.submit_code("something-the-user-pasted")
        self.assertEqual(result["state"], "completed")
        self.assertNotIn("no longer running", result["detail"])


class ProviderModelPairingTests(unittest.TestCase):
    """Section 82, as amended: a model belongs to a provider."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _store_mismatch(self) -> None:
        """The state a user is left in after switching provider before the fix."""
        self.app.config.set("primary_provider", "anthropic")
        self.app.config._values["primary_model"] = "offline-1"

    def test_changing_the_provider_carries_the_model_with_it(self) -> None:
        self.app.config.set("primary_provider", "anthropic")
        self.app.models.reconcile_primary()
        resolved = self.app.models.resolve("primary")
        self.assertEqual(resolved.provider_id, "anthropic")
        self.assertNotEqual(resolved.model_id, "offline-1")
        self.assertTrue(self.app.models.model_belongs_to_provider(
            resolved.provider_id, resolved.model_id))

    def test_a_stored_mismatch_is_repaired_rather_than_sent(self) -> None:
        """It outlives the edit that made it, so a settings hook is not enough."""
        self._store_mismatch()
        corrected = self.app.models.reconcile_primary()
        self.assertEqual(corrected["was"], "offline-1")
        self.assertEqual(self.app.models.resolve("primary").model_id,
                         corrected["primary_model"])

    def test_every_role_follows_the_repair(self) -> None:
        self._store_mismatch()
        self.app.models.reconcile_primary()
        for role in ("primary", "heartbeat", "scheduled_task"):
            with self.subTest(role):
                resolved = self.app.models.resolve(role)
                self.assertNotEqual(resolved.model_id, "offline-1")

    def test_a_mismatch_is_reported_as_a_setting_not_as_not_checked(self) -> None:
        self._store_mismatch()
        roles = self.app.models.describe()["roles"]
        for role, info in roles.items():
            with self.subTest(role):
                availability = info["availability"]
                self.assertIsNotNone(availability,
                                     "a knowable fault must not read as unchecked")
                self.assertEqual(availability["state"], "model_unavailable")
                self.assertIn("not a model offered by", availability["reason"])
                self.assertTrue(availability["requires_user_action"])

    def test_the_reason_names_models_the_provider_does_offer(self) -> None:
        self._store_mismatch()
        reason = self.app.models.check_availability("primary", force=True).reason
        self.assertIn("claude-opus-5", reason)

    def test_a_provider_qualified_model_is_not_sent_as_a_model_name(self) -> None:
        """The list offers "provider/model"; storing that must still resolve."""
        self.app.config.set("primary_provider", "anthropic")
        self.app.config.set("primary_model", "anthropic/claude-sonnet-5")
        resolved = self.app.models.resolve("primary")
        self.assertEqual(resolved.provider_id, "anthropic")
        self.assertEqual(resolved.model_id, "claude-sonnet-5")

    def test_a_working_pair_is_left_alone(self) -> None:
        self.app.config.set("primary_provider", "offline")
        self.app.config.set("primary_model", "offline-1")
        self.assertEqual(self.app.models.reconcile_primary(), {})


class TemporaryFailureTests(unittest.TestCase):
    """Section 89, as amended: the classification decides what happens."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.models = self.agent.app.models
        self.request = ModelRequest(messages=[{"role": "user", "content": "hi"}])

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _verdict(self, state, failure):
        return ModelAvailability(available=False, state=state, reason="simulated",
                                 failure_class=failure)

    def _count_attempts(self):
        provider = self.models.provider("offline")
        attempts = []
        original = provider.invoke

        def spy(request, model_id):
            attempts.append(model_id)
            return original(request, model_id)

        provider.invoke = spy
        return attempts

    def test_a_rate_limit_does_not_refuse_the_users_request(self) -> None:
        attempts = self._count_attempts()
        self.models.check_availability = lambda role="primary", force=False: self._verdict(
            AvailabilityState.RATE_LIMITED, FailureClass.TEMPORARY)
        response = self.models.invoke(self.request)
        self.assertTrue(attempts, "a temporary condition refused the request outright")
        self.assertTrue(response.text)

    def test_a_network_problem_does_not_refuse_it_either(self) -> None:
        attempts = self._count_attempts()
        self.models.check_availability = lambda role="primary", force=False: self._verdict(
            AvailabilityState.NETWORK_UNAVAILABLE, FailureClass.TEMPORARY)
        self.models.invoke(self.request)
        self.assertTrue(attempts)

    def test_a_condition_the_user_must_act_on_is_reported_not_attempted(self) -> None:
        attempts = self._count_attempts()
        self.models.check_availability = lambda role="primary", force=False: self._verdict(
            AvailabilityState.AUTHENTICATION_REQUIRED, FailureClass.USER_ACTION_REQUIRED)
        with self.assertRaises(ModelUnavailable):
            self.models.invoke(self.request)
        self.assertEqual(attempts, [], "a request that cannot work was still sent")

    def test_a_successful_attempt_clears_the_stale_verdict(self) -> None:
        """What is known is what last happened, not what a probe once saw."""
        resolved = self.models.resolve("primary")
        self.models.invoke(self.request)
        cached = self.models.cached_availability(resolved)
        self.assertIsNotNone(cached)
        self.assertTrue(cached.available)


    @staticmethod
    def _rate_limit_error(headers, message):
        anthropic = _anthropic()
        if anthropic is None:
            return None
        import httpx2 as httpx

        response = httpx.Response(429, headers=headers,
                                  request=httpx.Request("POST", "https://api.anthropic.com"))
        return anthropic.RateLimitError(message, response=response, body=None)

    def test_a_refusal_wearing_a_429_is_not_reported_as_a_rate_limit(self) -> None:
        """A rate limit says what is left and when to return; this says nothing."""
        from personalized_agent.models.providers.anthropic_provider import AnthropicProvider

        error = self._rate_limit_error({}, "Error code: 429 - {'type': 'error', 'error': "
                                           "{'type': 'rate_limit_error', 'message': 'Error'}}")
        if error is None:
            self.skipTest("the anthropic SDK is not installed")
        verdict = AnthropicProvider(self.agent.app.credentials)._translate(error)
        self.assertEqual(verdict.state.value, "credential_invalid")
        self.assertTrue(verdict.requires_user_action(),
                        "a refusal that will not clear must not promise a retry")
        self.assertIn("API key", verdict.reason)

    def test_an_actual_rate_limit_is_still_temporary(self) -> None:
        from personalized_agent.models.providers.anthropic_provider import AnthropicProvider

        error = self._rate_limit_error(
            {"retry-after": "30", "anthropic-ratelimit-requests-remaining": "0"},
            "rate limit exceeded")
        if error is None:
            self.skipTest("the anthropic SDK is not installed")
        verdict = AnthropicProvider(self.agent.app.credentials)._translate(error)
        self.assertEqual(verdict.state.value, "rate_limited")
        self.assertFalse(verdict.requires_user_action())

    def test_the_marks_of_a_rate_limit_are_what_decide(self) -> None:
        from personalized_agent.models.providers.anthropic_provider import (
            _looks_like_a_rate_limit)

        class Fake(Exception):
            def __init__(self, headers, text):
                super().__init__(text)
                self.response = type("R", (), {"headers": headers})()

        self.assertFalse(_looks_like_a_rate_limit(Fake({}, "Error")))
        self.assertTrue(_looks_like_a_rate_limit(Fake({"retry-after": "5"}, "Error")))
        self.assertTrue(_looks_like_a_rate_limit(
            Fake({"anthropic-ratelimit-tokens-remaining": "0"}, "Error")))
        self.assertTrue(_looks_like_a_rate_limit(Fake({}, "too many requests, try again in 5s")))


def _anthropic():
    try:
        import anthropic
    except ImportError:
        return None
    return anthropic


class LocalClientProviderTests(unittest.TestCase):
    """Section 76.1: a licensed client on the machine can be the provider."""

    def setUp(self) -> None:
        from personalized_agent.models.providers.claude_code_provider import (
            ClaudeCodeProvider)

        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.provider = ClaudeCodeProvider(self.app.credentials)
        # Installed, whether or not this machine has it (a test that asks
        # about an absent client says so itself).
        self.provider._resolve = lambda: ("cli", "claude", None)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_it_is_registered_as_a_provider(self) -> None:
        self.assertIn("claude_code", [p.provider_id for p in self.app.models.providers()])

    def test_this_application_stores_no_credential_for_it(self) -> None:
        self.provider._auth_status = lambda force=False: {"loggedIn": True}
        self.assertEqual(self.provider.get_auth_methods(), [AuthMethod.NONE])
        self.assertIn("stores no credential",
                      self.provider.capability_model()["note"].lower()
                      + str(self.provider.get_auth_status().detail or "").lower())

    def test_it_is_offered_only_when_installed_and_signed_in(self) -> None:
        self.provider._resolve = lambda: (
            "cli", None, "Claude Code (command line) is not installed on this machine")
        verdict = self.provider.check_availability("opus")
        self.assertFalse(verdict.available)
        self.assertTrue(verdict.requires_user_action())
        self.assertIn("not installed", verdict.reason)

        self.provider._resolve = lambda: ("cli", "claude", None)
        self.provider._auth_status = lambda: {"loggedIn": False}
        verdict = self.provider.check_availability("opus")
        self.assertEqual(verdict.state.value, "authentication_required")
        self.assertIn("auth login", verdict.reason)

    def test_the_client_is_asked_to_answer_not_to_act(self) -> None:
        """Its own tools stay off: the agent's sandbox decides what may happen."""
        self.provider._resolve = lambda: ("cli", "claude", None)
        command = self.provider._command(
            ModelRequest(messages=[{"role": "user", "content": "hi"}]), "opus")
        # Named denials, not an empty allow list: an empty or unknown tool
        # name makes the client discard the flag and grant everything.
        self.assertIn("--disallowed-tools", command)
        self.assertIn("Bash", command)
        self.assertNotIn("", command)
        self.assertIn("-p", command)
        self.assertIn("--output-format", command)

    def test_it_cannot_arrange_anything_that_outlives_the_call(self) -> None:
        """The reported bug, as a property of the command.

        The client can schedule, remind and notify for itself, and those look
        to the model like perfectly good ways to do what the user asked. They
        are not: they live and die with one invocation, while the agent has a
        scheduler that survives restarts. A user who asked to be reminded at
        two o'clock was told, accurately and uselessly, that the reminder was
        "session-only" - it had been made inside a subprocess that had
        already exited.
        """
        from personalized_agent.models.providers.claude_code_provider import (
            DENIED_TOOLS)

        for tool in ("CronCreate", "ScheduleWakeup", "PushNotification",
                     "RemoteTrigger", "SendMessage"):
            with self.subTest(tool=tool):
                self.assertIn(tool, DENIED_TOOLS)

    def test_it_cannot_delegate_or_load_more_tools(self) -> None:
        from personalized_agent.models.providers.claude_code_provider import (
            DENIED_TOOLS)

        for tool in ("Agent", "Task", "ToolSearch", "Skill", "Workflow"):
            with self.subTest(tool=tool):
                self.assertIn(tool, DENIED_TOOLS)

    def test_it_does_not_inherit_the_users_own_connectors(self) -> None:
        """What the agent may do is decided by the agent, not by another
        program's configuration.

        Without this the client loads whatever MCP servers the user has set up
        in their own Claude Code - document connectors, issue trackers,
        anything - and reaches for them. Asked to set a reminder it answered
        in terms of the connectors it had found, having tried to call the
        agent's own scheduler as though it were one of them.
        """
        self.provider._resolve = lambda: ("cli", "claude", None)
        command = self.provider._command(
            ModelRequest(messages=[{"role": "user", "content": "hi"}]), "opus")
        self.assertIn("--strict-mcp-config", command)
        # and nothing is passed with it, which is what makes it "none"
        self.assertNotIn("--mcp-config", command)
        # Nor is anything of the call kept in the user's own ~/.claude.
        self.assertIn("--no-session-persistence", command)

    def test_it_is_told_that_its_hosts_timers_do_not_persist(self) -> None:
        from personalized_agent.models.base import ToolSpec

        system = self.provider._system(ModelRequest(
            messages=[], system="You are the agent.",
            tools=[ToolSpec(name="schedule_task", description="Schedule work.",
                            input_schema={})]))
        lowered = system.lower()
        self.assertIn("outlive this reply", lowered)
        self.assertIn("will not happen", lowered)

    def test_it_runs_inside_the_workspace(self) -> None:
        local = self.app.models.provider("claude_code")
        self.assertEqual(Path(str(local.workspace)), self.app.paths.workspace_dir)

    def test_the_conversation_keeps_its_roles(self) -> None:
        prompt = self.provider._prompt(ModelRequest(messages=[
            {"role": "user", "content": "what did I say?"},
            {"role": "assistant", "content": "you said hello"},
        ]))
        self.assertIn("User: what did I say?", prompt)
        self.assertIn("Assistant: you said hello", prompt)

    def test_a_tool_result_is_not_the_user_speaking(self) -> None:
        """The client answers from a transcript, so a result must read as one.

        The Messages API carries a tool result on a user turn, which is its
        convention and invisible there. Rendered into "User: ..." it reads as
        the user pasting a block of JSON, and the answer built on it gets
        disowned - the agent told the user its calendar items were "from my
        own records, not from reading the calendar just now" when it had in
        fact just read it.
        """
        prompt = self.provider._prompt(ModelRequest(messages=[
            {"role": "user", "content": "what is on my calendar?"},
            {"role": "assistant",
             "content": '{"tool": "list_scheduled_tasks", "arguments": {}}'},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "c1",
                 "content": '[{"title": "Dentist"}]'}]},
        ]))
        self.assertIn('Tool result: [{"title": "Dentist"}]', prompt)
        self.assertNotIn("User: [{", prompt)
        self.assertNotIn("tool_use_id", prompt,
                         "the envelope is noise; only what it returned matters")

    def test_an_ordinary_block_list_is_still_carried(self) -> None:
        # Only tool results get the special rendering. Anything else is
        # content, and dropping it would lose part of the conversation.
        prompt = self.provider._prompt(ModelRequest(messages=[
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        ]))
        self.assertIn("User:", prompt)
        self.assertIn("hello", prompt)

    def test_the_tools_are_offered_as_the_agents_own(self) -> None:
        """Otherwise the client describes the tools of whatever is running it.

        Its own tools are all disallowed, so a model asked what it can do
        answered in terms of that empty set - "this session's tool set doesn't
        include the scheduled-task list" - while holding a working
        list_scheduled_tasks the whole time.
        """
        from personalized_agent.models.base import ToolSpec

        system = self.provider._system(ModelRequest(
            messages=[], system="You are the agent.",
            tools=[ToolSpec(name="list_scheduled_tasks",
                            description="List scheduled tasks.",
                            input_schema={})]))
        self.assertIn("You are the agent.", system)
        self.assertIn("list_scheduled_tasks", system)
        lowered = system.lower()
        self.assertIn("yours for this conversation", lowered)
        self.assertIn("really run", lowered)
        # and it is told not to answer as the program running it
        self.assertIn("the program that is running you", lowered)

    def test_no_tools_means_no_protocol(self) -> None:
        system = self.provider._system(ModelRequest(
            messages=[], system="You are the agent."))
        self.assertEqual(system, "You are the agent.")

    def test_a_tool_call_is_read_strictly(self) -> None:
        from personalized_agent.models.base import ToolSpec

        request = ModelRequest(messages=[], tools=[
            ToolSpec(name="remember", description="store a note", input_schema={})])
        calls = self.provider._tool_calls(
            '{"tool": "remember", "arguments": {"title": "x"}}', request)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "remember")
        # anything else is an answer, not a call
        for text in ('{"tool": "not_a_tool"}', "I will remember that", '{"nope": 1}'):
            with self.subTest(text):
                self.assertEqual(self.provider._tool_calls(text, request), [])

    def test_a_call_the_model_kept_typing_past_is_still_a_call(self) -> None:
        """The reply that reached the user as invented records.

        The client has no tool protocol of its own, so ours is a convention in
        the prompt - and the transcript it reads shows tool results, so a model
        will sometimes write the call and then continue the transcript by
        writing the result too. Requiring the reply to be exactly one JSON
        object made that "not a call", so it became the answer: the user was
        shown raw JSON followed by records that did not exist. Four of the six
        ids in one such reply were not in the library at all.
        """
        from personalized_agent.models.base import ToolSpec

        request = ModelRequest(messages=[], tools=[
            ToolSpec(name="search_memory", description="search", input_schema={})])
        reply = (
            '{"tool": "search_memory", "arguments": {"queries": ["a"]}}'
            + "\n\n"
            + 'Tool result: [{"id": "node_1f0c2a77bb4e4d21", "title": "invented"}]')
        calls = self.provider._tool_calls(reply, request)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "search_memory")
        self.assertEqual(calls[0].arguments, {"queries": ["a"]})

    def test_what_the_model_wrote_after_its_call_is_not_a_result(self) -> None:
        # Tool results do not come from the model. Whatever it wrote after the
        # call is discarded, and the agent supplies the real one.
        from personalized_agent.models.base import ToolSpec

        request = ModelRequest(messages=[], tools=[
            ToolSpec(name="search_memory", description="search", input_schema={})])
        reply = ('{"tool": "search_memory", "arguments": {}}'
                 + "\n" + "Tool result: [1,2,3]")
        calls = self.provider._tool_calls(reply, request)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].arguments, {})

    def test_prose_is_still_an_answer(self) -> None:
        from personalized_agent.models.base import ToolSpec

        request = ModelRequest(messages=[], tools=[
            ToolSpec(name="search_memory", description="search", input_schema={})])
        for text in ("Here is what I found.", "{not json at all",
                     '{"tool": "not_a_tool"}', '{"nope": 1}'):
            with self.subTest(text=text):
                self.assertEqual(self.provider._tool_calls(text, request), [])

    def test_the_transcript_remembers_the_call_not_the_invention(self) -> None:
        """The other half of reading a call from the front.

        The parser discards whatever the model wrote after its call, but what
        goes back into the transcript as "what you said last turn" was still
        the whole reply. So a result the model invented was read back to it a
        turn later as its own words, immediately above the real result -
        teaching it, turn by turn, that writing results is what it does here.
        """
        from personalized_agent.models.base import ToolCall

        call = ToolCall(id="x", name="search_memory", arguments={"queries": ["a"]})
        whole = ('{"tool": "search_memory", "arguments": {"queries": ["a"]}}'
                 + "\n\n" + 'Tool result: [{"id": "node_invented"}]')
        remembered = self.provider._as_written(whole, [call])
        self.assertNotIn("node_invented", remembered)
        self.assertNotIn("Tool result", remembered)
        self.assertEqual(json.loads(remembered),
                         {"tool": "search_memory", "arguments": {"queries": ["a"]}})

    def test_an_answer_is_remembered_whole(self) -> None:
        self.assertEqual(
            self.provider._as_written("Here is the answer.", []),
            "Here is the answer.")

    def test_it_is_told_what_no_such_tool_means(self) -> None:
        """The failure the user kept meeting, explained where it happens.

        The client can also invoke tools of its own, and none of ours are
        among them, so reaching for one that way is answered with "no such
        tool". Left unexplained the model reports that to the user as a
        missing tool - which sends them looking for a fault that is not
        there, while the tool sits in the list it was given.
        """
        from personalized_agent.models.base import ToolSpec

        system = self.provider._system(ModelRequest(
            messages=[], system="You are the agent.",
            tools=[ToolSpec(name="search_memory", description="search",
                            input_schema={})]))
        lowered = system.lower()
        self.assertIn("no such tool", lowered)
        self.assertIn("wrong mechanism", lowered)
        self.assertIn("never tell the user a tool does not exist", lowered)

    def test_it_is_told_not_to_write_the_result_itself(self) -> None:
        from personalized_agent.models.base import ToolSpec

        system = self.provider._system(ModelRequest(
            messages=[], system="You are the agent.",
            tools=[ToolSpec(name="search_memory", description="search",
                            input_schema={})]))
        lowered = system.lower()
        self.assertIn("ends at the closing brace", lowered)
        self.assertIn("do not write what you think the tool returned", lowered)

    def test_reading_provider_state_does_not_launch_a_process_each_time(self) -> None:
        """The panels refresh on a timer; asking the tool costs a process launch."""
        launches = []
        self.provider.executable = staticmethod(lambda: "claude")

        def counted(force=False):
            launches.append(1)
            return {"loggedIn": True}

        real = self.provider._auth_status
        self.provider._auth_status = counted
        try:
            for _ in range(5):
                self.provider.get_auth_status()
        finally:
            self.provider._auth_status = real
        self.assertEqual(len(launches), 5, "the provider should ask through one place")
        # and that one place caches
        source = (Path(__file__).resolve().parents[1] / "personalized_agent" / "models" /
                  "providers" / "claude_code_provider.py").read_text(encoding="utf-8")
        self.assertIn("STATUS_CACHE_SECONDS", source)
        self.assertIn("_status_cache", source)

    def test_a_subscription_refusal_points_at_the_local_client(self) -> None:
        from personalized_agent.models.providers.anthropic_provider import (
            _subscription_refused_reason)

        reason = _subscription_refused_reason()
        self.assertIn("Claude Code", reason)
        self.assertIn("Settings > General > Models", reason)


class AnExistingDatabaseKeepsUpTests(unittest.TestCase):
    """A user's data outlives any one version of this application.

    CREATE TABLE IF NOT EXISTS does nothing to a table that already exists, so
    every column added after the first release has to be listed in
    ADDED_COLUMNS as well. Three were not: scheduled_tasks.created_by,
    scheduled_tasks.reason and states.reason all went into the schema for
    section 52.7 and nowhere else.

    The shape of the failure is what makes it worth a test. Reading tolerated
    the gap - a missing column reads as absent and the row loads - so
    everything looked fine until the user next asked for something to be
    scheduled, which is a long way from the change that broke it, and the only
    symptom was "no column named created_by" from inside a tool call.
    """

    SNAPSHOT = Path(__file__).resolve().parent / "schema_snapshot.json"

    def _fresh(self):
        connection = sqlite3.connect(":memory:")
        connection.executescript(SCHEMA_SQL)
        return connection

    def _columns(self, connection, table):
        return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}

    def test_every_column_added_since_the_snapshot_is_migrated(self) -> None:
        """The guard. Add a column to the schema and nothing else, and this fails.

        The snapshot is a record of what the columns were, and it is not
        updated when one is added - a new column belongs in ADDED_COLUMNS,
        which is what actually reaches a database that already exists.
        """
        recorded = json.loads(self.SNAPSHOT.read_text(encoding="utf-8"))
        migrated = {(table, column) for table, column, _ in ADDED_COLUMNS}
        connection = self._fresh()
        try:
            missing = []
            for table in recorded:
                for column in self._columns(connection, table):
                    if column in recorded[table]:
                        continue
                    if (table, column) not in migrated:
                        missing.append(f"{table}.{column}")
        finally:
            connection.close()
        self.assertEqual(missing, [],
                         "these columns are in CREATE TABLE but would never "
                         "reach a database that already exists - add them to "
                         "ADDED_COLUMNS")

    def test_a_database_without_them_gets_them(self) -> None:
        # The three that were missing, dropped from a fresh database to stand
        # for the one the user actually had.
        from personalized_agent.data_manage.storage.database import Database

        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "old.db"
            connection = sqlite3.connect(path)
            connection.executescript(SCHEMA_SQL)
            for table, column in (("scheduled_tasks", "created_by"),
                                  ("scheduled_tasks", "reason"),
                                  ("states", "reason")):
                connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
            connection.commit()
            connection.close()

            database = Database(path)           # opening it is what migrates it
            database.close()

            check = sqlite3.connect(path)
            try:
                self.assertIn("created_by", self._columns(check, "scheduled_tasks"))
                self.assertIn("reason", self._columns(check, "scheduled_tasks"))
                self.assertIn("reason", self._columns(check, "states"))
            finally:
                check.close()

    def test_a_task_can_be_scheduled_afterwards(self) -> None:
        """The thing the user was actually trying to do.

        Two attempts at schedule_task failed with OperationalError; listing
        worked throughout, which is why the database looked healthy.
        """
        from personalized_agent.data_manage.storage.database import Database
        from personalized_agent.scheduler.storage.store import SchedulerStore

        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "old.db"
            connection = sqlite3.connect(path)
            connection.executescript(SCHEMA_SQL)
            connection.execute(
                "ALTER TABLE scheduled_tasks DROP COLUMN created_by")
            connection.execute("ALTER TABLE scheduled_tasks DROP COLUMN reason")
            connection.commit()
            connection.close()

            database = Database(path)
            try:
                store = SchedulerStore(database)
                task = store.create(ScheduledTask(
                    title="Stand up", description="every 45 minutes",
                    scheduled_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                    execution_type="notification", created_by="user",
                    reason="added by user"))
                self.assertEqual(store.get(task.id).created_by, "user")
                self.assertEqual(store.get(task.id).reason, "added by user")
            finally:
                database.close()

    def test_migrating_twice_changes_nothing(self) -> None:
        from personalized_agent.data_manage.storage.database import Database

        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "db.sqlite"
            Database(path).close()
            first = json.dumps(self._shape(path), sort_keys=True)
            Database(path).close()
            self.assertEqual(json.dumps(self._shape(path), sort_keys=True), first)

    @staticmethod
    def _shape(path):
        connection = sqlite3.connect(path)
        try:
            return {row[0]: sorted(
                r[1] for r in connection.execute(f"PRAGMA table_info({row[0]})"))
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()


class TheDisplayLanguageTests(unittest.TestCase):
    """Section 12.2, as amended: what the user is shown, and what it is found by.

    One setting was answering two questions - the language the agent replies
    in and the language its memory is indexed under - and they are not the
    same question. Someone who keeps their machine in English and thinks in
    Chinese wants to be spoken to in Chinese; the words their memory is found
    by are a separate choice, and changing one should not silently change the
    other.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _instruction(self) -> str:
        # As it is sent: the chat's language put at the top of its prompt.
        from personalized_agent.common.languages import in_display
        sent = in_display().compose(self.app.agent._system_prompt(), self.app.config)
        lines = [line for line in sent.splitlines()
                 if "Write everything the user will read" in line]
        return lines[0] if lines else ""

    def test_it_defaults_to_the_system_language(self) -> None:
        from personalized_agent.common.languages import system_language
        from personalized_agent.common.config.schema import SETTINGS_SCHEMA

        field = next(f for f in SETTINGS_SCHEMA if f.key == "display_language")
        self.assertEqual(field.default, system_language())

    def test_the_agent_is_told_what_to_answer_in(self) -> None:
        self.app.config.set("display_language", "zh")
        self.assertIn("Chinese", self._instruction())

    def test_changing_it_changes_what_the_agent_is_told(self) -> None:
        self.app.config.set("display_language", "zh")
        self.assertIn("Chinese", self._instruction())
        self.app.config.set("display_language", "es")
        instruction = self._instruction()
        self.assertIn("Spanish", instruction)
        self.assertNotIn("Chinese", instruction)
        self.app.config.set("display_language", "ja")
        self.assertIn("Japanese", self._instruction())
        self.app.config.set("display_language", "ko")
        self.assertIn("Korean", self._instruction())
        for code, name in (("ru", "Russian"), ("fr", "French"), ("de", "German"),
                           ("it", "Italian")):
            self.app.config.set("display_language", code)
            self.assertIn(name, self._instruction())
        # The nine, and no other.
        with self.assertRaises(ValueError):
            self.app.config.set("display_language", "pt")

    def test_it_is_not_the_memory_language(self) -> None:
        # The whole reason for two settings. Being spoken to in one language
        # and searching in another is a real thing to want.
        self.app.config.set("display_language", "zh")
        self.assertIn("Chinese", self._instruction())
        # Memory is kept in English, whatever you are spoken to in.
        self.assertEqual(self.app.config.get("language"), "en")

    def test_what_is_quoted_keeps_its_own_words(self) -> None:
        # Otherwise the agent translates a file path or an error message,
        # which makes it useless for the thing it was quoted for.
        self.app.config.set("display_language", "zh")
        instruction = self._instruction()
        for kept in ("Quoted material", "file paths", "code"):
            self.assertIn(kept, instruction)

    def test_everything_the_user_reads_carries_it(self) -> None:
        """Not only replies: a note raised on its own is read too.

        A model writes in whatever language the material suggests, so a note
        composed from English source material comes out in English however
        this is set - and the user reads half their agent in a language they
        did not choose.
        """
        from personalized_agent.common import output_languages as langs
        from personalized_agent.common.languages import display_instruction

        self.app.config.set("display_language", "zh")
        expected = display_instruction(self.app.config)
        self.assertIn("Chinese", expected)
        # Said where each call is made (language=), and read off the source.
        rules = {}
        for one in langs.read_code()["said"]:
            rules.setdefault(one["task"], set()).update(r["rule"] for r in one["rules"])
        for task in ("heartbeat:step_notify", "task_completion", "scheduled_task",
                     "agent_instruction"):
            with self.subTest(task=task):
                self.assertIn("display", rules.get(task, set()),
                              f"{task} composes text the user reads and does not "
                              f"carry the display language")

    def test_an_existing_choice_is_not_taken_away_on_upgrade(self) -> None:
        """Splitting one setting in two must not undo what was already chosen.

        Someone who had set the language was choosing, among other things, to
        be spoken to in it. Defaulting the new setting to the system's
        language would have reverted that on upgrade - silently, and in the
        place they would notice least until the next reply arrived.
        """
        from personalized_agent.common.config.store import ConfigStore

        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "settings.json"
            # What an installation from before the split looks like.
            path.write_text(json.dumps({"language": "zh"}), encoding="utf-8")
            config = ConfigStore(data_dir=Path(work))
            self.assertEqual(config.get("display_language"), "zh")
            self.assertEqual(config.get("language"), "zh")

    def test_an_explicit_choice_survives_the_upgrade_path(self) -> None:
        from personalized_agent.common.config.store import ConfigStore

        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "settings.json"
            path.write_text(json.dumps({"language": "zh",
                                        "display_language": "en"}),
                            encoding="utf-8")
            config = ConfigStore(data_dir=Path(work))
            self.assertEqual(config.get("display_language"), "en",
                             "the upgrade must not overwrite a real choice")

    def test_a_fresh_installation_follows_the_system(self) -> None:
        from personalized_agent.common.config.store import ConfigStore
        from personalized_agent.common.languages import system_language

        with tempfile.TemporaryDirectory() as work:
            config = ConfigStore(data_dir=Path(work))
            self.assertEqual(config.get("display_language"), system_language())

    def test_memory_is_kept_in_english(self) -> None:
        """Every keyword written and searched in one language - English - so a
        word is found as it is written; what the user reads follows the
        display language."""
        from personalized_agent.common.languages import keyword_languages

        self.assertEqual(keyword_languages(self.app.config.get("language")), ["en"])

    def test_the_language_it_was_written_in_is_always_covered(self) -> None:
        # What keeps a record findable by quoting it.
        from personalized_agent.common.languages import BASE, keyword_languages

        self.assertIn(BASE, keyword_languages("zh,ja,ko"))

    def test_it_is_english_whatever_is_asked_for(self) -> None:
        from personalized_agent.common.languages import keyword_languages

        self.assertEqual(keyword_languages("zh,ja,ko,fr,de,es,pt,ru,it,nl"), ["en"])

    def test_a_single_language_from_before_still_reads(self) -> None:
        # Installations have one bare code stored, from when it could only be
        # one. It must not be read as a broken value.
        from personalized_agent.common.languages import keyword_languages

        self.assertEqual(keyword_languages("zh"), ["en"])
        self.assertEqual(keyword_languages("zh,en"), ["en"])

    def test_what_is_stored_is_tidied(self) -> None:
        from personalized_agent.common.config import schema

        self.assertEqual(schema.coerce("language", " zh , en ,zh "), "zh,en")
        self.assertEqual(schema.coerce("language", ["ja", "zh"]), "ja,zh")

    def test_the_memory_language_is_english_and_not_a_setting(self) -> None:
        # Fixed at English, and not shown: there is nothing to choose.
        from personalized_agent.common.config.schema import SETTINGS_SCHEMA

        field = next(f for f in SETTINGS_SCHEMA if f.key == "language")
        self.assertTrue(field.fixed)
        self.assertTrue(field.hidden)
        self.assertEqual(field.default, "en")
        self.assertEqual(field.choices, ["en"])
        # One language setting is shown: the display language.
        shown = [f.key for f in SETTINGS_SCHEMA if "language" in f.key and not f.hidden]
        self.assertEqual(shown, ["display_language"])


    def test_an_unset_value_falls_back_rather_than_failing(self) -> None:
        from personalized_agent.common.languages import (display_language,
                                                         system_language)

        self.assertEqual(display_language(None), system_language())


class WhatTheAgentSpendsIsWrittenDownTests(unittest.TestCase):
    """Section 14.35, as amended: the cost history outlives the process.

    The calls were a deque of four hundred and a few running totals, so
    everything began again at zero whenever the application started - which
    makes "is this getting more expensive" unanswerable, and the agent
    restarts whenever anything is fixed.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _call(self, task="heartbeat", model="opus", tokens=1000):
        self.app.observability.record_model_call(
            provider="anthropic", model=model, task=task,
            input_tokens=tokens, output_tokens=100, latency_ms=900,
            estimated_cost=0.01)

    def test_a_call_is_recorded(self) -> None:
        self._call()
        totals = self.app.observability.snapshot()["model_metrics"]["totals"]
        self.assertEqual(totals["calls"], 1)
        self.assertEqual(totals["total"], 1100)

    def test_it_says_which_kind_of_work_spent_it(self) -> None:
        # "How much" without "on what" is not actionable: a heartbeat costing
        # more than every conversation put together is a setting to change.
        self._call(task="heartbeat")
        self._call(task="agent_instruction")
        by_task = self.app.observability.snapshot()["model_metrics"]["by_task"]
        self.assertEqual(set(by_task), {"heartbeat", "agent_instruction"})

    def test_and_which_model(self) -> None:
        self._call(model="opus")
        self._call(model="haiku")
        by_model = self.app.observability.snapshot()["model_metrics"]["by_model"]
        self.assertEqual(set(by_model), {"opus", "haiku"})

    def test_it_says_what_window_it_covers(self) -> None:
        self._call()
        metrics = self.app.observability.snapshot()["model_metrics"]
        self.assertTrue(metrics["measured_since"],
                        "a chart that cannot say what it covers is a chart "
                        "that sounds like it covers everything")

    def test_nothing_recorded_is_not_an_error(self) -> None:
        metrics = self.app.observability.snapshot()["model_metrics"]
        self.assertEqual(metrics["totals"]["calls"], 0)
        self.assertEqual(metrics["tokens_over_time"], [])

    def test_a_broken_store_does_not_break_the_answer(self) -> None:
        """Instrumentation must never be the reason a turn fails."""
        class Broken:
            def record(self, **kwargs):
                raise RuntimeError("disk gone")

            def snapshot(self, since=None):
                raise RuntimeError("disk gone")

        self.app.observability.store = Broken()
        self._call()                      # must not raise
        metrics = self.app.observability.snapshot()["model_metrics"]
        self.assertIn("totals", metrics, "it fell back to what is in memory")

    def test_old_calls_are_dropped_eventually(self) -> None:
        from personalized_agent.common.events.models import iso, utcnow

        self._call()
        self.app.db.execute("UPDATE model_calls SET created_at = ?",
                            (iso(utcnow() - timedelta(days=500)),))
        self.assertEqual(self.app.model_calls.prune(400), 1)
        self.assertEqual(
            self.app.observability.snapshot()["model_metrics"]["totals"]["calls"], 0)

    def test_what_is_about_this_run_is_not_kept(self) -> None:
        # What is executing now means nothing from a process that has ended.
        snapshot = self.app.observability.snapshot()
        self.assertIn("agent_metrics", snapshot)
        self.assertIn("active_task", snapshot["agent_metrics"])


class TheCostHistorySurvivesARestartTests(unittest.TestCase):
    """The whole point, exercised the only way that proves it."""

    def test_the_numbers_are_there_when_it_starts_again(self) -> None:
        from personalized_agent.app import AgentApplication

        # Not TemporaryDirectory: the log file stays open on Windows, and
        # this test is about the database rather than about cleanup.
        work = tempfile.mkdtemp()
        try:
            root = Path(work) / "root"
            first = AgentApplication(root=root)
            try:
                for _ in range(3):
                    first.observability.record_model_call(
                        provider="anthropic", model="opus", task="heartbeat",
                        input_tokens=500, output_tokens=50, latency_ms=800,
                        estimated_cost=0.005)
                before = first.observability.snapshot()["model_metrics"]["totals"]
            finally:
                first.db.close()

            again = AgentApplication(root=root)
            try:
                after = again.observability.snapshot()["model_metrics"]["totals"]
            finally:
                again.db.close()
        finally:
            shutil.rmtree(work, ignore_errors=True)

        self.assertEqual(before["calls"], 3)
        self.assertEqual(after, before,
                         "the cost history began again at zero")


class CodexProviderTests(unittest.TestCase):
    """Section 76.1: Codex can answer through its existing local sign-in."""

    def setUp(self) -> None:
        from personalized_agent.models.providers.codex_provider import CodexProvider

        self.agent = TemporaryAgent()
        self.provider = CodexProvider(self.agent.app.credentials,
                                      workspace=self.agent.app.paths.workspace_dir)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_it_is_registered_as_a_provider(self) -> None:
        self.assertIn("codex", [p.provider_id for p in self.agent.app.models.providers()])

    def test_it_offers_default_and_explicit_codex_models_for_selection(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"CODEX_HOME": str(self.agent.dir)}):
            models = [model.id for model in self.provider.list_models()]
        self.assertEqual(models[0], "default")
        self.assertIn("gpt-6-astra", models)
        self.assertIn("gpt-5.6-luna", models)

    def test_catalog_tracks_client_updates_and_excludes_hidden_models(self) -> None:
        payload = {"models": [
            {"slug": "future-model", "display_name": "Future model", "visibility": "list"},
            {"slug": "internal-review", "visibility": "hide"},
            {"slug": "future-model", "visibility": "list"},
            {"slug": None, "visibility": "list"},
            None,
        ]}
        with unittest.mock.patch.object(Path, "read_text", return_value=json.dumps(payload)):
            models = self.provider.list_models()
        self.assertEqual([m.id for m in models], ["default", "future-model"])
        self.assertEqual(models[1].display_name, "Future model")
        # Codex rewrites its cache in place. A read caught halfway keeps the
        # last catalog that could be read whole (ported from PR #4).
        with unittest.mock.patch.object(Path, "read_text", return_value='{"models": ['):
            self.assertEqual(self.provider.list_models(), models,
                             "a partially written cache discarded the last usable catalog")

    def test_the_cli_is_asked_to_answer_read_only_and_ephemerally(self) -> None:
        prompt = "do not put this in argv " * 500
        command = self.provider._command(
            ModelRequest(messages=[{"role": "user", "content": prompt}]), "default")
        self.assertNotIn(prompt, command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertNotIn("--ask-for-approval", command)
        self.assertIn("--ephemeral", command)
        self.assertIn("--json", command)
        self.assertEqual(command[-1], "-")

        default = self.provider._command(
            ModelRequest(messages=[{"role": "user", "content": "hello"}]), "default")
        self.assertNotIn("--model", default)

    def test_tool_instruction_template_preserves_its_json_example(self) -> None:
        from personalized_agent.models.base import ToolSpec

        prompt = self.provider._prompt(ModelRequest(messages=[], tools=[
            ToolSpec(name="remember", description="store a note", input_schema={}),
        ]))
        self.assertIn('{"tool": "<name>", "arguments": {...}}', prompt)

    def test_jsonl_response_becomes_the_model_response(self) -> None:
        output = '\n'.join((
            '{"type":"thread.started","thread_id":"thread_1"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"Hello"}}',
            '{"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":3}}',
        ))
        text, usage, stop = self.provider._parse_events(output)
        self.assertEqual(text, "Hello")
        self.assertEqual((usage.input_tokens, usage.output_tokens), (12, 3))
        self.assertEqual(stop, "end_turn")

    def test_invocation_reads_the_final_agent_message_from_jsonl(self) -> None:
        # Installed-ness and sign-in are stubbed the way the sibling test
        # below stubs them. Without this the test asserts something about the
        # machine it runs on - it passes where Codex happens to be installed
        # and fails everywhere else, which is not what it is trying to check.
        self.provider.executable = lambda: "codex"
        self.provider._auth_status = lambda: {"logged_in": True}
        completed = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout='{"type":"item.completed","item":{"type":"agent_message",'
                   '"text":"Hello from Codex"}}\n'
                   '{"type":"turn.completed","usage":{"input_tokens":8,'
                   '"output_tokens":4}}\n',
            stderr="",
        )
        with unittest.mock.patch("personalized_agent.common.children.run", return_value=completed) as run:
            response = self.provider.invoke(
                ModelRequest(messages=[{"role": "user", "content": "hello"}]),
                "gpt-5.3-codex",
            )
        self.assertEqual(response.text, "Hello from Codex")
        self.assertEqual((response.usage.input_tokens, response.usage.output_tokens), (8, 4))
        self.assertEqual(run.call_args.kwargs["input"], "User: hello\n\nAssistant:")

    def test_login_failure_is_actionable(self) -> None:
        self.provider.executable = lambda: "codex"
        self.provider._auth_status = lambda: {"logged_in": False}
        verdict = self.provider.check_availability("gpt-5.3-codex")
        self.assertEqual(verdict.state, AvailabilityState.AUTHENTICATION_REQUIRED)
        self.assertTrue(verdict.requires_user_action())


class AStatedLimitIsReadAsALimitTests(unittest.TestCase):
    """Section 80: the one failure certain to end by itself.

    The client stops serving for a while and says so in its own words:
    "You've hit your session limit · resets 11:50pm (Europe/Lisbon)". It
    does not say *rate*, so nothing matched, and a wait-and-retry was filed
    as ``unknown_error`` - the class for failures nobody knows what to do
    about.

    And the exit code was not zero, so the envelope on stdout was not parsed
    and the reason became the raw JSON. Cut off by the length limits between
    there and the log, what reached the user was:

        data heartbeat deferred: unknown_error ({"duration_api_ms":0,
        "stop_reason":"stop_sequence","session_id":"a9e77f01-…

    Zeroed telemetry, mid-field, saying nothing - not even that there was no
    answer.
    """

    LIMIT = "You've hit your session limit \u00b7 resets 11:50pm (Europe/Lisbon)"

    def _verdict(self, result="", returncode=1, stderr=""):
        from personalized_agent.models.providers.claude_code_provider import (
            ClaudeCodeProvider)

        envelope = json.dumps({
            "duration_api_ms": 0, "stop_reason": "stop_sequence",
            "session_id": "a9e77f01-72dd", "total_cost_usd": 0,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "is_error": True, "result": result})
        done = subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=envelope, stderr=stderr)
        return ClaudeCodeProvider._translate(
            done, ClaudeCodeProvider._envelope(envelope))

    def test_the_client_own_sentence_is_the_reason(self) -> None:
        self.assertEqual(self._verdict(self.LIMIT).reason, self.LIMIT)

    def test_the_reason_is_never_the_envelope(self) -> None:
        for reason in (self._verdict(self.LIMIT).reason, self._verdict("").reason):
            self.assertNotIn("duration_api_ms", reason)
            self.assertNotIn("stop_reason", reason)

    def test_it_is_temporary_rather_than_unknown(self) -> None:
        from personalized_agent.models.base import AvailabilityState, FailureClass

        verdict = self._verdict(self.LIMIT)
        self.assertEqual(verdict.state, AvailabilityState.RATE_LIMITED)
        self.assertEqual(verdict.failure_class, FailureClass.TEMPORARY)

    def test_it_does_not_ask_the_user_to_do_anything(self) -> None:
        # There is nothing for them to do. It comes back on its own.
        self.assertFalse(self._verdict(self.LIMIT).requires_user_action())

    def test_when_it_comes_back_is_kept_where_a_caller_can_read_it(self) -> None:
        self.assertEqual(self._verdict(self.LIMIT).rate_limit_status,
                         "11:50pm (Europe/Lisbon)")

    def test_other_spellings_of_the_same_thing(self) -> None:
        from personalized_agent.models.base import AvailabilityState

        for said in ("You've hit your weekly limit, resets Monday",
                     "Limit reached. Try again later.",
                     "5-hour limit reached \u00b7 resets 3pm"):
            self.assertEqual(self._verdict(said).state,
                             AvailabilityState.RATE_LIMITED, said)

    def test_a_failure_with_nothing_to_say_says_that(self) -> None:
        verdict = self._verdict("", returncode=2)
        self.assertIn("exited with code 2", verdict.reason)
        self.assertIn("no message", verdict.reason)

    def test_an_expired_sign_in_is_still_told_apart(self) -> None:
        from personalized_agent.models.base import AvailabilityState

        self.assertEqual(self._verdict("Invalid API key - please run /login").state,
                         AvailabilityState.AUTHENTICATION_REQUIRED)

    def test_a_quota_that_really_is_exhausted_is_not_made_temporary(self) -> None:
        from personalized_agent.models.base import AvailabilityState

        self.assertEqual(self._verdict("Your usage limit has been reached").state,
                         AvailabilityState.QUOTA_EXHAUSTED)

    def test_output_that_is_not_json_does_not_break_the_reading(self) -> None:
        from personalized_agent.models.providers.claude_code_provider import (
            ClaudeCodeProvider)

        self.assertEqual(ClaudeCodeProvider._envelope("not json at all"), {})
        self.assertEqual(ClaudeCodeProvider._envelope(None), {})
        self.assertEqual(ClaudeCodeProvider._envelope('["a list"]'), {})


class ACallAfterASentenceIsStillACallTests(unittest.TestCase):
    """The reply had to begin with the call.

    A model that says why it is asking - which is what a caller should do,
    and what the heartbeat's own instruction asks for in as many words -
    wrote a sentence, then a perfectly good call, and was read as having
    written no call at all. The whole reply became an answer: a step that
    had gone looking was recorded as one that decided it had enough, and
    the panel said so.
    """

    def _parse(self, text, names=("search_memory", "open_memory")):
        from personalized_agent.models.base import ModelRequest, ToolSpec
        from personalized_agent.models.providers.claude_code_provider import (
            ClaudeCodeProvider)

        request = ModelRequest(messages=[],
                               tools=[ToolSpec(n, "", {}) for n in names])
        provider = ClaudeCodeProvider.__new__(ClaudeCodeProvider)
        return provider._tool_calls(text, request)

    CALL = '{"tool": "search_memory", "arguments": {"queries": ["a", "b"]}}'

    def test_a_reason_before_the_call_does_not_hide_it(self) -> None:
        calls = self._parse("I want to check the record first.\n\n" + self.CALL)
        self.assertEqual([c.name for c in calls], ["search_memory"])
        self.assertEqual(calls[0].arguments, {"queries": ["a", "b"]})

    def test_a_call_on_its_own_still_works(self) -> None:
        self.assertEqual([c.name for c in self._parse(self.CALL)],
                         ["search_memory"])

    def test_and_one_in_a_fenced_block(self) -> None:
        fenced = "```json\n" + self.CALL + "\n```"
        self.assertEqual([c.name for c in self._parse(fenced)],
                         ["search_memory"])

    def test_an_answer_is_not_a_call(self) -> None:
        """Which is what the position used to be standing in for.

        An answer is a JSON object too. What tells them apart is whether
        the object names a tool this request offered.
        """
        self.assertEqual(
            self._parse('{"state_analysis": "working", "states": []}'), [])

    def test_nor_is_an_answer_with_something_said_before_it(self) -> None:
        self.assertEqual(
            self._parse('Here is what I think.\n\n{"state_analysis": "x"}'), [])

    def test_a_tool_that_was_not_offered_is_not_a_call(self) -> None:
        self.assertEqual(self._parse('{"tool": "rm_rf", "arguments": {}}'), [])

    def test_what_follows_the_call_is_still_discarded(self) -> None:
        """The reason the front of the reply used to decide.

        A model wrote a call and then invented its result. Nothing after a
        call can be a tool result, because results do not come from models.
        """
        calls = self._parse(self.CALL + '\nTool result: [{"id": "made up"}]')
        self.assertEqual([c.name for c in calls], ["search_memory"])

    def test_prose_with_no_call_in_it_is_an_answer(self) -> None:
        self.assertEqual(self._parse("I have enough already."), [])

    def test_a_reply_with_no_tools_offered_is_never_a_call(self) -> None:
        self.assertEqual(self._parse(self.CALL, names=()), [])

