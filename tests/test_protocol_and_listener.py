"""Application protocol, listener privacy controls and event routing."""

from __future__ import annotations

import datetime as dt
import json
import shutil
import tempfile
import threading
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from support import TemporaryAgent, keyed

from personalized_agent.common.events.models import ListenerEvent
from personalized_agent.listener.core.privacy import PrivacyFilter
from personalized_agent.protocol.authentication.service import AuthenticationError
from personalized_agent.protocol.identity.keys import CRYPTO_AVAILABLE, b64, unb64


@unittest.skipUnless(CRYPTO_AVAILABLE, "cryptography is required for the protocol")
class ProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization

        self.app_key = Ed25519PrivateKey.generate()
        public_raw = self.app_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )
        self.public_key = b64(public_raw)
        self.app.app_registry.register("vscode", self.public_key, ["ingest_events"],
                                       "VS Code integration")

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _authenticate(self) -> str:
        challenge = self.app.auth.begin("vscode")["challenge"]
        signature = b64(self.app_key.sign(unb64(challenge)))
        return self.app.auth.complete("vscode", challenge, signature)["token"]

    def test_agent_identity_never_exposes_the_private_key(self) -> None:
        identity = self.app.identity.public_identity()
        self.assertTrue(identity["agent_id"].startswith("agent_"))
        self.assertEqual(identity["private_key"], "never disclosed")
        self.assertNotIn("PRIVATE KEY", str(identity))

    def test_agent_proves_possession_without_transmitting_the_key(self) -> None:
        from personalized_agent.protocol.identity.keys import verify_signature

        challenge = b64(b"a" * 32)
        proof = self.app.identity.sign_challenge(challenge)
        self.assertTrue(
            verify_signature(self.app.identity.public_key, unb64(challenge), proof["signature"])
        )

    def test_valid_challenge_response_creates_a_session(self) -> None:
        token = self._authenticate()
        session = self.app.auth.authenticate(token, permission="ingest_events")
        self.assertEqual(session.application_id, "vscode")

    def test_challenge_cannot_be_replayed(self) -> None:
        challenge = self.app.auth.begin("vscode")["challenge"]
        signature = b64(self.app_key.sign(unb64(challenge)))
        self.app.auth.complete("vscode", challenge, signature)
        with self.assertRaises(AuthenticationError):
            self.app.auth.complete("vscode", challenge, signature)

    def test_wrong_signature_is_rejected(self) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        challenge = self.app.auth.begin("vscode")["challenge"]
        forged = b64(Ed25519PrivateKey.generate().sign(unb64(challenge)))
        with self.assertRaises(AuthenticationError):
            self.app.auth.complete("vscode", challenge, forged)

    def test_unregistered_application_cannot_start_a_handshake(self) -> None:
        with self.assertRaises(AuthenticationError):
            self.app.auth.begin("unknown-app")

    def test_revocation_invalidates_existing_sessions(self) -> None:
        token = self._authenticate()
        self.app.app_registry.revoke("vscode")
        with self.assertRaises(AuthenticationError):
            self.app.auth.authenticate(token)

    def test_permission_revocation_is_immediate(self) -> None:
        token = self._authenticate()
        self.app.app_registry.set_permissions("vscode", ["read"])
        with self.assertRaises(AuthenticationError):
            self.app.auth.authenticate(token, permission="ingest_events")

    def test_key_rotation_changes_the_agent_id(self) -> None:
        before = self.app.identity.agent_id
        self.app.identity.rotate()
        self.assertNotEqual(before, self.app.identity.agent_id)


class ListenerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.config = self.app.config
        self.config.set("listener_enabled", True)
        self.privacy = PrivacyFilter(self.config)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_categories_toggle_independently(self) -> None:
        self.config.set("listener_browser_enabled", False)
        self.config.set("listener_application_enabled", True)
        self.assertFalse(self.privacy.category_enabled("browser"))
        self.assertTrue(self.privacy.category_enabled("application"))

    def test_monitoring_off_suspends_everything(self) -> None:
        self.config.set("listener_enabled", False)
        self.assertFalse(self.privacy.category_enabled("application"))
        self.assertFalse(self.privacy.monitoring_enabled())

    def test_application_denylist_blocks_events(self) -> None:
        self.config.set("listener_application_denylist", "Password Manager")
        event = ListenerEvent(event_type="application.focused", source="application",
                              application="Password Manager")
        self.assertFalse(self.privacy.allows(event, "application"))

    def test_application_allowlist_restricts_to_listed_applications(self) -> None:
        self.config.set("listener_application_allowlist", "VS Code\nChrome")
        allowed = ListenerEvent(event_type="application.focused", source="application",
                                application="VS Code")
        blocked = ListenerEvent(event_type="application.focused", source="application",
                                application="Slack")
        self.assertTrue(self.privacy.allows(allowed, "application"))
        self.assertFalse(self.privacy.allows(blocked, "application"))

    def test_website_denylist_blocks_navigation(self) -> None:
        self.config.set("listener_browser_enabled", True)
        self.config.set("listener_website_denylist", "bank.example")
        event = ListenerEvent(event_type="browser.navigation", source="browser",
                              data={"url": "https://bank.example/account"})
        self.assertFalse(self.privacy.allows(event, "browser"))

    def test_directory_exclusions_block_filesystem_events(self) -> None:
        secret = str(self.agent.dir / "secret")
        self.config.set("listener_directory_exclusions", secret)
        event = ListenerEvent(event_type="filesystem.modified", source="filesystem",
                              data={"path": f"{secret}/passwords.txt"})
        self.assertFalse(self.privacy.allows(event, "filesystem"))

    def test_filtered_events_never_reach_storage(self) -> None:
        self.config.set("listener_enabled", False)
        self.app.listener._on_event(  # noqa: SLF001 - exercising the routing path
            ListenerEvent(event_type="application.focused", source="application",
                          application="VS Code")
        )
        self.assertEqual(self.app.raw_store.count_events(), 0)

    def test_accepted_events_are_stored_and_published(self) -> None:
        published = []
        self.app.bus.subscribe("listener.*", lambda topic, payload: published.append(topic))
        self.app.listener._on_event(  # noqa: SLF001
            ListenerEvent(event_type="application.focused", source="application",
                          application="VS Code")
        )
        self.assertEqual(self.app.raw_store.count_events(), 1)
        self.assertEqual(published, ["listener.application.focused"])

    def test_application_provider_exposes_a_platform_permission_failure(self) -> None:
        from personalized_agent.listener.providers.application import ApplicationProvider
        from personalized_agent.platform_layer.base import ActiveWindow, PlatformCapabilities

        class Activity:
            last_error = "Not authorized to send Apple events to System Events."

            def get_active_window(self):
                return ActiveWindow()

        class Platform:
            activity = Activity()

            @staticmethod
            def capabilities():
                return PlatformCapabilities(active_application=True)

        provider = ApplicationProvider(self.config, Platform())
        provider._poll()  # noqa: SLF001 - verifies the listener's status boundary
        self.assertIn("System Events", provider.get_status()["last_error"])

    def test_macos_foreground_application_script_uses_valid_try_syntax(self) -> None:
        from personalized_agent.platform_layer.macos import _FRONT_APP_SCRIPT

        self.assertIn("set frontProcess", _FRONT_APP_SCRIPT)
        self.assertIn("on error", _FRONT_APP_SCRIPT)
        self.assertNotIn("(try", _FRONT_APP_SCRIPT)

    def test_integration_ingest_requires_the_configured_prefix(self) -> None:
        from personalized_agent.listener.integrations.ingest import IngestProvider

        provider = IngestProvider(self.config, provider_id="app_integration",
                                  category="integration")
        provider.bind(lambda event: None)
        provider.start()
        with self.assertRaises(ValueError):
            provider.accept({"event_type": "filesystem.modified", "data": {}})
        event = provider.accept(
            {"event_type": "integration.vscode.file_saved", "data": {"path": "a.py"}},
            agent_id="vscode",
        )
        self.assertEqual(event.metadata["authenticated_agent"], "vscode")

    def test_disabled_ingest_provider_refuses_events(self) -> None:
        from personalized_agent.listener.integrations.ingest import IngestProvider

        provider = IngestProvider(self.config)
        with self.assertRaises(PermissionError):
            provider.accept({"event_type": "integration.x", "data": {}})


class AssistantContentScopeTests(unittest.TestCase):
    """Section 18, as amended: which conversations may be read.

    One switch for the whole machine is too blunt a control for this. A coding
    assistant is used for everything the user does - on one real installation
    it had produced sessions in 31 separate projects - and the reason to record
    what was said, so the agent can see the work it is helping with, does not
    apply to all of them equally.
    """

    def setUp(self) -> None:
        from personalized_agent.listener.providers.assistant import AssistantProvider

        self.provider = object.__new__(AssistantProvider)
        self.tool = _tool("claude_code", "Claude Code")

    def _options(self, **over):
        # The ticked list is the switch, so a test of anything else has to
        # tick something first.
        options = {"enabled": True, "thinking": False, "tools": False, "limit": 4000,
                   "only_tools": ["Claude Code"], "only_projects": [],
                   "never_projects": []}
        options.update(over)
        return options

    def _allowed(self, project, **over):
        return self.provider._content_allowed(self.tool, project, self._options(**over))

    def test_a_ticked_assistant_with_no_project_list_covers_its_projects(self) -> None:
        # An empty project list is no restriction. An empty assistant list is
        # not the same thing - that one is the switch.
        self.assertTrue(self._allowed(r"C:\Users\me\anything"))

    def test_nothing_ticked_records_nothing(self) -> None:
        self.assertFalse(self._allowed(r"C:\Users\me\anything", only_tools=[]))

    def test_a_named_project_is_recorded_and_others_are_not(self) -> None:
        only = [r"C:\Users\me\Research\release"]
        self.assertTrue(self._allowed(r"C:\Users\me\Research\release",
                                      only_projects=only))
        self.assertFalse(self._allowed(r"C:\Users\me\Desktop\taxes",
                                       only_projects=only))

    def test_a_directory_covers_the_projects_inside_it(self) -> None:
        only = [r"C:\Users\me\Research"]
        self.assertTrue(self._allowed(r"C:\Users\me\Research\one\deep",
                                      only_projects=only))
        self.assertFalse(self._allowed(r"C:\Users\me\Personal", only_projects=only))

    def test_a_glob_can_be_used(self) -> None:
        self.assertTrue(self._allowed(r"C:\Users\me\Research\desk_helper",
                                      only_projects=[r"C:\Users\me\*\desk_*"]))

    def test_slashes_do_not_have_to_match(self) -> None:
        # The user types a path; which separator they used is not a rule.
        self.assertTrue(self._allowed(r"C:\Users\me\Research\release",
                                      only_projects=["C:/Users/me/Research"]))

    def test_case_does_not_have_to_match(self) -> None:
        self.assertTrue(self._allowed(r"C:\Users\Me\Research\Release",
                                      only_projects=["c:/users/me/research"]))

    def test_an_exclusion_overrides_the_list_of_projects(self) -> None:
        # A directory can be recorded with one project inside it held back.
        self.assertFalse(self._allowed(
            r"C:\Users\me\Research\private",
            only_projects=[r"C:\Users\me\Research"],
            never_projects=[r"C:\Users\me\Research\private"]))

    def test_an_exclusion_works_on_its_own(self) -> None:
        self.assertFalse(self._allowed(r"C:\Users\me\Desktop\taxes",
                                       never_projects=[r"C:\Users\me\Desktop\taxes"]))
        self.assertTrue(self._allowed(r"C:\Users\me\Research",
                                      never_projects=[r"C:\Users\me\Desktop\taxes"]))

    def test_assistants_can_be_chosen_between(self) -> None:
        self.assertTrue(self._allowed("any", only_tools=["Claude Code"]))
        self.assertFalse(self._allowed("any", only_tools=["Some Other Assistant"]))

    def test_the_list_is_the_only_control(self) -> None:
        # There is no separate on/off any more: two controls for one decision
        # can disagree, and neither state reads as anything.
        from personalized_agent.common.config import schema as sc

        fields = [v for v in vars(sc).values()
                  if isinstance(v, list) and v and hasattr(v[0], "key")][0]
        self.assertNotIn("listener_assistant_content_enabled",
                         {f.key for f in fields})

    def test_star_is_every_assistant(self) -> None:
        # The default: every assistant found, one installed later too.
        self.assertTrue(self._allowed("any", only_tools=["*"]))

    def test_an_assistant_can_be_named_by_its_key(self) -> None:
        self.assertTrue(self._allowed("any", only_tools=["claude_code"]))

    def test_the_settings_exist_and_are_editable(self) -> None:
        from personalized_agent.common.config import schema as sc

        fields = [v for v in vars(sc).values()
                  if isinstance(v, list) and v and hasattr(v[0], "key")][0]
        by_key = {f.key: f for f in fields}
        for key in ("listener_assistant_content_tools",
                    "listener_assistant_content_projects",
                    "listener_assistant_content_exclusions"):
            self.assertIn(key, by_key)
            self.assertEqual(by_key[key].section, "Listener")
            self.assertFalse(by_key[key].hidden, "the user has to be able to set it")
        # Every listening on by default (Settings > Privacy), this too.
        # every assistant found, one installed later too.
        self.assertEqual(by_key["listener_assistant_content_tools"].default, "*")

        # The projects are typed or picked; the assistants are ticked from
        # what was found installed, because their names are the application's
        # to know rather than the user's to remember.
        for key in ("listener_assistant_content_projects",
                    "listener_assistant_content_exclusions"):
            self.assertEqual(by_key[key].type, "multiline")
            self.assertEqual(by_key[key].chooser, "directory")
        tools = by_key["listener_assistant_content_tools"]
        self.assertEqual(tools.type, "multi_choice")
        self.assertEqual(tools.choices_source, "assistant_tools")


def _tool(key, name):
    from personalized_agent.listener.providers.assistant import AssistantTool
    from pathlib import Path as _Path

    return AssistantTool(key, name, _Path("."))


class AssistantContentIsActuallyWithheldTests(unittest.TestCase):
    """Section 18, as amended: the filter withholds the text, not just a verdict.

    Testing the decision alone would pass just as well if the transcript were
    read anyway and the result thrown away afterwards. These write real
    transcripts and check what came out.
    """

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.research = os.sep.join(["C:", "Users", "me", "Research", "release"])
        self.taxes = os.sep.join(["C:", "Users", "me", "Desktop", "taxes"])
        self._transcript(self.research, "keep")
        self._transcript(self.taxes, "hold")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _transcript(self, project: str, name: str) -> None:
        folder = self.root / name
        folder.mkdir(parents=True, exist_ok=True)
        lines = [
            {"type": "user", "timestamp": "2026-09-06T10:00:00Z", "sessionId": name,
             "cwd": project, "message": {"content": [
                 {"type": "text", "text": "SECRET PROMPT in " + name}]}},
            {"type": "assistant", "timestamp": "2026-09-06T10:00:05Z",
             "sessionId": name, "cwd": project, "message": {"model": "m", "content": [
                 {"type": "text", "text": "SECRET REPLY in " + name}]}},
        ]
        (folder / "session.jsonl").write_text(
            "\n".join(json.dumps(entry) for entry in lines), encoding="utf-8")

    def _recorded(self, **settings) -> list:
        from personalized_agent.listener.providers.assistant import (
            AssistantProvider, AssistantTool)

        values = {"listener_assistant_content_tools": "Claude Code"}
        values.update(settings)

        class Config:
            def get(self, key, default=None):
                return values.get(key, default)

        provider = object.__new__(AssistantProvider)
        provider.config = Config()
        provider.provider_id = "assistant"
        provider._offsets = {}
        provider._emitted = 0
        captured = []
        provider.emit = captured.append

        options = provider._content_settings()
        tool = AssistantTool("claude_code", "Claude Code", self.root)
        for name, project in (("keep", self.research), ("hold", self.taxes)):
            path = self.root / name / "session.jsonl"
            if options["enabled"] and provider._content_allowed(tool, project, options):
                provider._turns(path, tool, options)
        return sorted({event.data.get("text", "").split(" in ")[-1]
                       for event in captured if event.data.get("text")})

    def test_a_ticked_assistant_has_both_its_projects_read(self) -> None:
        self.assertEqual(self._recorded(), ["hold", "keep"])

    def test_nothing_ticked_reads_nothing(self) -> None:
        self.assertEqual(self._recorded(listener_assistant_content_tools=""), [])

    def test_naming_one_directory_withholds_the_other(self) -> None:
        self.assertEqual(
            self._recorded(listener_assistant_content_projects="C:/Users/me/Research"),
            ["keep"])

    def test_an_exclusion_withholds_that_one(self) -> None:
        self.assertEqual(
            self._recorded(
                listener_assistant_content_exclusions="C:/Users/me/Desktop/taxes"),
            ["keep"])

    def test_ticking_only_another_assistant_withholds_everything(self) -> None:
        self.assertEqual(self._recorded(listener_assistant_content_tools="Cursor"), [])

    def test_ticking_this_assistant_reads_it(self) -> None:
        self.assertEqual(
            self._recorded(listener_assistant_content_tools="Claude Code"),
            ["hold", "keep"])

if __name__ == "__main__":
    unittest.main()


class AssistantConversationCaptureTests(unittest.TestCase):
    """Section 18, as amended: recording what was said, when asked for.

    A coding assistant is where a large part of the user's thinking now
    happens, and an agent that may not read it is guessing at the work it
    exists to help with. It is also the most personal thing this application
    can hold, so every part of it is a deliberate choice.
    """

    PROJECT = "C:/proj/demo"

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.root = Path(tempfile.mkdtemp(prefix="pa-transcripts-"))
        self.project = self.root / "C--proj-demo"
        self.project.mkdir(parents=True)
        self.transcript = self.project / "session.jsonl"
        self.transcript.write_text("", encoding="utf-8")
        # Section 18, as amended: ticking the assistant is what turns it on.
        self.app.config.set("listener_assistant_content_tools", "Claude Code")
        self.captured = []

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        self.agent.cleanup()

    def _provider(self):
        from personalized_agent.listener.providers.assistant import (
            AssistantProvider, AssistantTool)

        provider = AssistantProvider(self.app.config)
        provider._tools = [AssistantTool("claude_code", "Claude Code", self.root)]
        provider.bind(self.captured.append)
        provider.poll()          # the first poll starts at the end of the file
        return provider

    def _write(self, role, blocks, **extra) -> None:
        entry = {"type": role, "sessionId": "s1", "cwd": self.PROJECT,
                 "timestamp": "2026-09-01T10:00:00.000Z",
                 "message": {"role": role, "content": blocks}}
        entry.update(extra)
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + chr(10))

    def _messages(self):
        return [e for e in self.captured if e.event_type == "assistant.message"]

    def test_both_sides_of_the_conversation_are_recorded(self) -> None:
        provider = self._provider()
        self._write("user", [{"type": "text", "text": "how do I fix the build"}])
        self._write("assistant", [{"type": "text", "text": "run the tests first"}])
        provider.poll()
        said = [(m.data["role"], m.data["text"]) for m in self._messages()]
        self.assertEqual(said, [("user", "how do I fix the build"),
                                ("assistant", "run the tests first")])

    def test_a_turn_says_where_it_happened(self) -> None:
        provider = self._provider()
        self._write("user", [{"type": "text", "text": "a question"}])
        provider.poll()
        data = self._messages()[0].data
        self.assertEqual(data["project"], self.PROJECT)
        self.assertEqual(data["session_id"], "s1")
        self.assertEqual(data["tool"], "Claude Code")

    def test_recording_is_off_until_an_assistant_is_ticked(self) -> None:
        self.app.config.set("listener_assistant_content_tools", "")
        provider = self._provider()
        self._write("user", [{"type": "text", "text": "not recorded"}])
        provider.poll()
        self.assertEqual(self._messages(), [])

    def test_switching_it_on_does_not_reach_backwards(self) -> None:
        # What was said before the user asked for any of it to be recorded was
        # said before they asked.
        self.app.config.set("listener_assistant_content_tools", "")
        provider = self._provider()
        self._write("user", [{"type": "text", "text": "said while off"}])
        provider.poll()
        self.app.config.set("listener_assistant_content_tools", "Claude Code")
        provider.poll()
        self.assertEqual(self._messages(), [])
        self._write("user", [{"type": "text", "text": "said while on"}])
        provider.poll()
        self.assertEqual([m.data["text"] for m in self._messages()], ["said while on"])

    def test_a_pause_in_collection_is_not_filled_in_afterwards(self) -> None:
        # Monitoring turned off stops the provider while the session keeps
        # being written. Resuming from the old position would read back exactly the
        # stretch the user turned collection off for.
        provider = self._provider()
        self._write("user", [{"type": "text", "text": "before"}])
        provider.poll()
        provider.stop()
        self._write("user", [{"type": "text", "text": "said while private"}])
        provider._stop = threading.Event()
        provider._primed = False              # what start() does
        provider.poll()
        self._write("user", [{"type": "text", "text": "after"}])
        provider.poll()
        texts = [m.data["text"] for m in self._messages()]
        self.assertEqual(texts, ["before", "after"])
        self.assertNotIn("said while private", texts)

    def test_reasoning_and_tool_traffic_stay_out_unless_asked_for(self) -> None:
        provider = self._provider()
        self._write("assistant", [
            {"type": "thinking", "thinking": "a long internal monologue"},
            {"type": "text", "text": "the actual answer"},
            {"type": "tool_use", "name": "Read", "input": {"path": "x.py"}},
        ])
        provider.poll()
        text = self._messages()[0].data["text"]
        self.assertEqual(text, "the actual answer")
        self.assertNotIn("internal monologue", text)

    def test_reasoning_is_included_when_it_is_asked_for(self) -> None:
        self.app.config.set("listener_assistant_include_thinking", True)
        provider = self._provider()
        self._write("assistant", [{"type": "thinking", "thinking": "the monologue"},
                                  {"type": "text", "text": "the answer"}])
        provider.poll()
        self.assertIn("the monologue", self._messages()[0].data["text"])

    def test_tool_calls_are_included_when_they_are_asked_for(self) -> None:
        self.app.config.set("listener_assistant_include_tool_calls", True)
        provider = self._provider()
        self._write("assistant", [{"type": "text", "text": "reading it"},
                                  {"type": "tool_use", "name": "Read",
                                   "input": {"path": "x.py"}}])
        provider.poll()
        self.assertIn("Read", self._messages()[0].data["text"])

    def test_a_long_turn_is_cut_and_says_so(self) -> None:
        self.app.config.set("listener_assistant_max_chars", 200)
        provider = self._provider()
        self._write("user", [{"type": "text", "text": "x" * 5000}])
        provider.poll()
        data = self._messages()[0].data
        self.assertEqual(len(data["text"]), 200)
        self.assertTrue(data["truncated"], "a cut turn must say it was cut")

    def test_a_transcript_is_read_once_as_it_grows(self) -> None:
        # The cursor is a byte offset: a session running for hours is not
        # re-read from the beginning on every poll.
        provider = self._provider()
        self._write("user", [{"type": "text", "text": "one"}])
        provider.poll()
        provider.poll()
        provider.poll()
        self.assertEqual([m.data["text"] for m in self._messages()], ["one"])

    def test_a_half_written_line_is_ignored_not_fatal(self) -> None:
        provider = self._provider()
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.write('{"type": "user", "message": {"content": [{"type": "te')
        provider.poll()
        self.assertEqual(self._messages(), [])

    def test_a_turn_that_is_only_tool_traffic_is_not_an_empty_message(self) -> None:
        provider = self._provider()
        self._write("assistant", [{"type": "tool_use", "name": "Read", "input": {}}])
        provider.poll()
        self.assertEqual(self._messages(), [])


class AnApplicationConnectsItselfTests(unittest.TestCase):
    """Sections 61-65: an integrated application, end to end.

    Register, sign a challenge, get a session, push events - the whole road
    an application integration takes, with nothing stubbed but the request.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_whole_handshake_works(self) -> None:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        from personalized_agent.api.routes import build_routes
        from personalized_agent.protocol.identity.keys import b64, unb64

        self.app.config.set("listener_enabled", True)
        self.app.config.set("listener_integrations_enabled", True)
        self.app.listener.discover()
        self.app.listener._providers["app_integration"].start()
        routes = build_routes(self.app)

        class Request:
            def __init__(self, body=None, token=None):
                self.body = body or {}
                self.token = token

            def get(self, key, default=None):
                return self.body.get(key, default)

            def require(self, key):
                return self.body[key]

        call = lambda method, path, **kw: routes[(method, path)](Request(**kw))

        key = Ed25519PrivateKey.generate()
        public = key.public_key().public_bytes(serialization.Encoding.Raw,
                                               serialization.PublicFormat.Raw)
        call("POST", "/api/protocol/register", body={
            "application_id": "editor-plugin-test", "public_key": b64(public),
            "display_name": "Editor plugin", "permissions": ["ingest_events"]})
        challenge = call("POST", "/api/protocol/challenge",
                         body={"application_id": "editor-plugin-test"})
        session = call("POST", "/api/protocol/authenticate", body={
            "application_id": "editor-plugin-test",
            "challenge": challenge["challenge"],
            "signature": b64(key.sign(unb64(challenge["challenge"])))})
        self.assertIn("ingest_events", session["permissions"])

        accepted = call("POST", "/api/listener/ingest", token=session["token"], body={
            "provider": "app_integration",
            "events": [{"event_type": "integration.editor.file_saved",
                        "application": "editor",
                        "data": {"path": "notes.txt", "text": "hello"}}]})
        self.assertEqual(len(accepted["accepted"]), 1)
        stored = self.app.raw_store.recent_events(limit=5, event_type="integration.")
        self.assertEqual([e.data["text"] for e in stored], ["hello"])


class TheAgentDoesNotListenToItselfTests(unittest.TestCase):
    """Section 19, as amended: bookkeeping is not activity.

    The agent keeps its memory in the workspace, and the workspace is
    watched - rightly, because the user's documents and downloads are there
    too. So every record it wrote, and every rebuild of the index, came back
    as a filesystem event and cost a model call to interpret. Measured on one
    day: 12,022 of 14,892 events were the agent hearing its own writing.
    """

    def setUp(self) -> None:
        from personalized_agent.common.agent_writes import AGENT_WRITES

        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.registry = AGENT_WRITES
        self.registry.clear()

    def tearDown(self) -> None:
        self.registry.clear()
        self.agent.cleanup()

    def _heard(self, path) -> bool:
        from personalized_agent.common.events.models import ListenerEvent

        return self.app.listener.privacy.allows(
            ListenerEvent(event_type="filesystem.modified", source="filesystem",
                          data={"path": str(path)}), "filesystem")

    def test_a_record_it_wrote_is_not_activity(self) -> None:
        from personalized_agent.data_manage.library import USER_FEEDBACK, Record

        written = self.app.library.write(Record(folder=USER_FEEDBACK, title="A memory",
                                                summary="s", body="b"))
        self.assertFalse(self._heard(written.path))


    def test_but_work_done_for_the_user_is(self) -> None:
        """The distinction is bookkeeping, not authorship.

        When the agent writes a file the user asked for - code in a
        conversation, a document a task produced - that is something that
        happened, and belongs in the record of what happened as much as if
        the user had typed it.
        """
        from personalized_agent.common.agent_writes import was_written_by_agent

        made = self.app.runtime.write_text("report.md", "# for the user")
        self.assertFalse(was_written_by_agent(made),
                         "work done for the user was suppressed as bookkeeping")

    def test_a_claim_does_not_last(self) -> None:
        # The same file the agent wrote this morning may be edited by the
        # user this afternoon, and that edit is theirs.
        import time

        from personalized_agent.common.agent_writes import AgentWrites

        registry = AgentWrites(ttl=0.01)
        registry.mark("C:\\p\\a.md")
        self.assertTrue(registry.claims("C:\\p\\a.md"))
        time.sleep(0.05)
        self.assertFalse(registry.claims("C:\\p\\a.md"))

    def test_it_is_the_same_file_however_it_is_spelled(self) -> None:
        from personalized_agent.common.agent_writes import (mark_written,
                                                            was_written_by_agent)

        mark_written("C:\\Users\\x\\A.MD")
        self.assertTrue(was_written_by_agent("c:/users/x/a.md"))

    def test_it_does_not_grow_without_limit(self) -> None:
        from personalized_agent.common.agent_writes import MAX_CLAIMS, AgentWrites

        registry = AgentWrites()
        for index in range(MAX_CLAIMS + 500):
            registry.mark("C:\\p\\f%d.md" % index)
        self.assertLessEqual(registry.pending(), MAX_CLAIMS)


class TheAgentDoesNotRecordItselfTests(unittest.TestCase):
    """Section 18, as amended: the agent's own model calls are not activity.

    The Claude Code provider answers by running the client on this machine,
    and the client writes a transcript of every run into the same session
    store this listener reads. So each model call came back a moment later as
    something that had happened at the user's desk. Expanding a stretch of
    Firefox showed the relabeller asking for keywords and the data heartbeat
    asking what some raw activity meant, filed under whichever window
    happened to be in front while the agent worked in the background.

    It is the loop ``agent_writes`` closes, arriving by another road - there
    the agent heard its own files, here it hears its own voice - and worse in
    one way, because interpreting that activity takes a model call, whose
    transcript is more activity. Measured on this machine: 888 of the 951
    assistant events in recent history were the agent's own.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.root = Path(tempfile.mkdtemp(prefix="pa-own-sessions-"))
        self.app.config.set("listener_assistant_content_tools", "Claude Code")
        self.captured = []

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        self.agent.cleanup()

    def _session(self, session_id: str, project: str) -> None:
        folder = self.root / session_id
        folder.mkdir(parents=True, exist_ok=True)
        entry = {"type": "user", "sessionId": session_id, "cwd": project,
                 "timestamp": "2026-09-01T10:00:00.000Z",
                 "message": {"role": "user",
                             "content": [{"type": "text", "text": "a turn"}]}}
        (folder / (session_id + ".jsonl")).write_text(
            json.dumps(entry) + chr(10), encoding="utf-8")

    def _poll(self):
        from personalized_agent.listener.providers.assistant import (
            AssistantProvider, AssistantTool)

        provider = AssistantProvider(self.app.config)
        provider._tools = [AssistantTool("claude_code", "Claude Code", self.root)]
        provider.bind(self.captured.append)
        provider.poll()
        return self.captured

    def test_a_session_this_application_started_is_not_recorded(self) -> None:
        from personalized_agent.common.agent_sessions import new_session_id

        self._session(new_session_id(), "C:/somewhere/else")
        self.assertEqual(self._poll(), [])

    def test_nor_one_run_inside_the_agents_own_storage(self) -> None:
        # The second way of telling, and the one that reads sessions recorded
        # before there was a mark to read.
        self._session("11111111-2222-4333-8444-555555555555",
                      str(self.app.root / "workspace"))
        self.assertEqual(self._poll(), [])

    def test_the_users_own_session_is_still_recorded(self) -> None:
        self._session("99999999-2222-4333-8444-555555555555", "C:/proj/demo")
        kinds = {e.event_type for e in self._poll()}
        self.assertIn("assistant.session", kinds)

    def test_the_mark_survives_being_written_down_and_read_back(self) -> None:
        # It has to: the transcripts outlive the process that made them, and
        # after a restart nothing in memory remembers which were ours.
        from personalized_agent.common.agent_sessions import (
            is_agent_session, new_session_id)

        written = json.loads(json.dumps({"sessionId": new_session_id()}))
        self.assertTrue(is_agent_session(written["sessionId"]))
        self.assertFalse(is_agent_session("99999999-2222-4333-8444-555555555555"))

    def test_the_id_it_chooses_is_a_well_formed_uuid(self) -> None:
        # The client validates it, and refuses the run if it is not one.
        import uuid

        from personalized_agent.common.agent_sessions import new_session_id

        chosen = new_session_id()
        self.assertEqual(str(uuid.UUID(chosen)), chosen)

    def test_two_calls_never_share_a_session(self) -> None:
        from personalized_agent.common.agent_sessions import new_session_id

        made = {new_session_id() for _ in range(200)}
        self.assertEqual(len(made), 200)


class TheListenerDoesNotRecordOurOwnPanelTests(unittest.TestCase):
    """Section 9, as amended: not recorded, rather than filtered on the way out.

    The address is in the browser's own history, which this provider reads.
    Dropping it here is what stops it becoming raw activity, being handed to
    a model to interpret, and turning into a memory about the user having
    looked at the Overview.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.app.config.set("api_port", 8756)
        self.captured = []

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _poll(self, pages):
        from datetime import datetime, timezone

        from personalized_agent.listener.providers.browser import BrowserProvider

        when = datetime.now(timezone.utc)

        class OnePlatform:
            class activity:
                @staticmethod
                def get_browser_history_paths():
                    return {"Firefox": "places.sqlite"}

        provider = BrowserProvider(self.app.config, platform=OnePlatform())
        provider._read = lambda browser, path: [(url, url, when) for url in pages]
        provider.bind(self.captured.append)
        provider.poll()
        return [e.data["url"] for e in self.captured]

    def test_our_own_panel_never_becomes_an_event(self) -> None:
        seen = self._poll(["http://127.0.0.1:8756/#overview",
                           "https://www.youtube.com/watch?v=Ex4m1a0002A"])
        self.assertEqual(seen, ["https://www.youtube.com/watch?v=Ex4m1a0002A"])

    def test_the_cursor_still_moves_past_it(self) -> None:
        # Skipped without advancing, it would be re-read and re-skipped on
        # every poll for as long as it stayed the newest thing in history.
        from personalized_agent.listener.providers.browser import BrowserProvider

        self._poll(["http://127.0.0.1:8756/#overview"])
        self.assertEqual(self.captured, [])

    def test_the_port_is_the_configured_one(self) -> None:
        self.app.config.set("api_port", 9001)
        seen = self._poll(["http://127.0.0.1:9001/", "http://127.0.0.1:8756/"])
        self.assertEqual(seen, ["http://127.0.0.1:8756/"])


class AClientThatWentAwayIsNotAFailureTests(unittest.TestCase):
    """Section 60: the panel reloads, and a request in flight is lost.

    The panel refreshes on a timer and reloads whenever the user navigates,
    so a request still being answered when the tab closes is ordinary and
    happens several times an hour. Writing the answer then fails with
    ``ConnectionAbortedError``.

    What made that look like a fault was what happened next. The write was
    treated as a failed request, so the handler tried to send a 500 - down
    the same closed socket, which failed the same way, with nothing left to
    catch it. What reached the user was two stack traces, one inside the
    other, naming neither the route nor the reason.
    """

    def setUp(self) -> None:
        import socket
        import threading

        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.socket = socket
        self.threading = threading
        # A port nobody else is on, chosen by the system.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.app.config.set("api_port", self.port)
        self.app.config.set("api_host", "127.0.0.1")
        from personalized_agent.api.server import ApiServer

        self.api = ApiServer(self.app)
        self.api.start()

    def tearDown(self) -> None:
        self.api.stop()
        self.agent.cleanup()

    def _hang_up_mid_request(self, path: str = "/api/status") -> None:
        """Ask, then close without reading the answer."""
        connection = self.socket.socket()
        connection.settimeout(5)
        connection.connect(("127.0.0.1", self.port))
        connection.sendall(
            ("GET " + path + f" HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
             f"X-PA-Key: {self.app.api_access.key}\r\n"
             "Connection: close\r\n\r\n").encode("ascii"))
        # Discarded rather than read: this is the tab being closed.
        connection.close()

    def test_it_does_not_print_a_stack_trace(self) -> None:
        import io
        import sys

        captured = io.StringIO()
        held, sys.stderr = sys.stderr, captured
        try:
            for _ in range(5):
                self._hang_up_mid_request()
            time.sleep(0.6)
        finally:
            sys.stderr = held
        printed = captured.getvalue()
        self.assertNotIn("Traceback", printed)
        self.assertNotIn("ConnectionAbortedError", printed)

    def test_and_the_server_goes_on_answering(self) -> None:
        import urllib.request

        for _ in range(5):
            self._hang_up_mid_request()
        time.sleep(0.3)
        with urllib.request.urlopen(keyed(
                self.app, f"http://127.0.0.1:{self.port}/api/status"), timeout=10) as answer:
            self.assertEqual(answer.status, 200)

    def test_a_real_failure_is_still_reported(self) -> None:
        # The quiet is for one named family of errors, not for everything.
        import urllib.error
        import urllib.request

        def explode(request):
            raise RuntimeError("something actually went wrong")

        self.api.routes[("GET", "/api/status")] = explode
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(keyed(
                self.app, f"http://127.0.0.1:{self.port}/api/status"), timeout=10)
        self.assertEqual(caught.exception.code, 500)
        self.assertIn("something actually went wrong",
                      caught.exception.read().decode("utf-8"))


class BeingReadIsNotBeingWrittenTests(unittest.TestCase):
    """Section 19, as amended: one notification, two different things.

    Windows reports a directory change for eight different reasons - the
    content was written, the file was **read**, an attribute changed - and
    watchdog collapses all of them into one "modified". Recorded as written,
    a file the user only opened is a file they are told they edited.

    Measured on one machine: of 399 notifications, **4** were about a file
    written in the seconds before. The rest were the memory library being
    read by the indexer, reported an hour later when Windows got round to
    flushing the access time.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.root = Path(tempfile.mkdtemp(prefix="pa-fs-"))
        self.captured = []

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        self.agent.cleanup()

    def _provider(self):
        from personalized_agent.listener.providers.filesystem import FilesystemProvider

        provider = FilesystemProvider(self.app.config)
        provider.bind(self.captured.append)
        return provider

    class _Raw:
        is_directory = False
        event_type = "modified"

        def __init__(self, path):
            self.src_path = str(path)

    def _notify(self, provider, path):
        provider.handle_raw(self._Raw(path))
        # Events wait a second to be seen in company (a sweep is not a
        # person); these tests are about which of the two a notification is,
        # not about waiting, so the held batch is judged at once.
        provider.flush_held_events()
        return self.captured[-1].event_type if self.captured else None

    def test_a_file_written_a_moment_ago_is_a_write(self) -> None:
        path = self.root / "report.md"
        path.write_text("one", encoding="utf-8")
        self.assertEqual(self._notify(self._provider(), path), "filesystem.modified")

    def test_a_file_nobody_wrote_is_a_read(self) -> None:
        # The notification is real - Windows sent it - but the content did
        # not move, so calling it an edit says something that did not happen.
        path = self.root / "reference.pdf"
        path.write_text("one", encoding="utf-8")
        old = time.time() - 7200
        os.utime(path, (old, old))
        self.assertEqual(self._notify(self._provider(), path), "filesystem.opened")

    def test_the_second_notification_about_an_unchanged_file_is_a_read(self) -> None:
        path = self.root / "notes.md"
        path.write_text("one", encoding="utf-8")
        provider = self._provider()
        self.assertEqual(self._notify(provider, path), "filesystem.modified")
        self.assertEqual(self._notify(provider, path), "filesystem.opened")
        self.assertEqual(self._notify(provider, path), "filesystem.opened")

    def test_but_a_real_edit_after_a_read_is_an_edit_again(self) -> None:
        path = self.root / "notes.md"
        path.write_text("one", encoding="utf-8")
        provider = self._provider()
        self._notify(provider, path)
        self._notify(provider, path)
        path.write_text("one and a half", encoding="utf-8")
        self.assertEqual(self._notify(provider, path), "filesystem.modified")

    def test_a_file_that_is_already_gone_is_not_guessed_at(self) -> None:
        provider = self._provider()
        self.assertEqual(self._notify(provider, self.root / "never.txt"),
                         "filesystem.modified")

    def test_what_it_remembers_is_bounded(self) -> None:
        from personalized_agent.listener.providers.filesystem import FilesystemProvider

        provider = self._provider()
        provider.REMEMBERED = 10
        for index in range(30):
            path = self.root / f"f{index}.txt"
            path.write_text("x", encoding="utf-8")
            self._notify(provider, path)
        self.assertLessEqual(len(provider._seen), 10)
        self.assertGreater(FilesystemProvider.REMEMBERED, 1000,
                           "the real ceiling is for a machine, not a test")

    def test_the_new_kind_is_one_the_provider_declares(self) -> None:
        # A type nothing admits to producing is one no reader is written for.
        self.assertIn("filesystem.opened", self._provider().supported_event_types())


class OnlyWhatAPersonDidTests(unittest.TestCase):
    """Section 19, as amended: a machine left alone is not a user working.

    Watching the whole disk, the record filled with what the machine does on
    its own. Measured over twenty-five minutes on one installation: **3,499
    file events, of which 2,950 were one Windows registry hive log** - 84% of
    everything recorded, none of it anybody's work. The rest was a chat
    client's cache and a coding assistant's own session files.

    The file the user had actually opened in that window was not there at all.

    Two things separate the two, and they are needed together: a person
    touching the keyboard, which says *somebody* did something; and the names
    of the files that churn regardless, which a directory exclusion cannot
    reach because they sit among things worth watching.
    """

    class _Here:
        class activity:
            @staticmethod
            def seconds_since_input():
                return 3.0

    class _Away:
        class activity:
            @staticmethod
            def seconds_since_input():
                return 9999.0

    class _CannotTell:
        class activity:
            @staticmethod
            def seconds_since_input():
                return None

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.app.config.set("listener_enabled", True)
        self.app.config.set("listener_filesystem_enabled", True)
        self.app.config.set("listener_browser_enabled", True)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _filter(self, platform):
        from personalized_agent.listener.core.privacy import PrivacyFilter

        return PrivacyFilter(self.app.config, platform=platform)

    @staticmethod
    def _file(path):
        return ListenerEvent(event_type="filesystem.modified", source="filesystem",
                             data={"path": path})

    def test_a_file_changed_while_the_user_is_here_is_theirs(self) -> None:
        self.assertTrue(self._filter(self._Here).allows(
            self._file("C:/work/report.md"), "filesystem"))

    def test_a_file_changed_while_nobody_is_here_is_not(self) -> None:
        self.assertFalse(self._filter(self._Away).allows(
            self._file("C:/work/report.md"), "filesystem"))

    def test_a_platform_that_cannot_tell_records_rather_than_stops(self) -> None:
        """Silence is read as yes.

        Refusing to record because we cannot tell would turn an unsupported
        platform into one that quietly monitors nothing - the failure this
        section exists to prevent.
        """
        self.assertTrue(self._filter(self._CannotTell).allows(
            self._file("C:/work/report.md"), "filesystem"))

    def test_and_so_does_no_platform_at_all(self) -> None:
        self.assertTrue(self._filter(None).allows(
            self._file("C:/work/report.md"), "filesystem"))

    def test_the_gate_can_be_switched_off(self) -> None:
        self.app.config.set("listener_only_while_present", False)
        self.assertTrue(self._filter(self._Away).allows(
            self._file("C:/work/report.md"), "filesystem"))

    def test_how_long_counts_as_here_is_a_setting(self) -> None:
        class Recently:
            class activity:
                @staticmethod
                def seconds_since_input():
                    return 600.0

        self.assertFalse(self._filter(Recently).allows(
            self._file("C:/work/report.md"), "filesystem"))
        self.app.config.set("listener_presence_seconds", 1200)
        self.assertTrue(self._filter(Recently).allows(
            self._file("C:/work/report.md"), "filesystem"))

    def test_a_page_visit_is_not_gated_on_presence(self) -> None:
        # Somebody visited it. The browser does not read pages on its own,
        # and history is read long after the fact by design.
        page = ListenerEvent(event_type="browser.navigation", source="browser",
                             data={"url": "https://example.com/"})
        self.assertTrue(self._filter(self._Away).allows(page, "browser"))

    # -- the files that churn whatever the user is doing -------------------
    def test_the_registry_hive_log_is_never_recorded(self) -> None:
        here = self._filter(self._Here)
        self.assertFalse(here.allows(
            self._file("C:/Users/someone/ntuser.dat.LOG2"), "filesystem"))
        self.assertFalse(here.allows(
            self._file("C:/Users/someone/NTUSER.DAT.LOG1"), "filesystem"))

    def test_nor_a_lock_file_or_a_half_written_download(self) -> None:
        here = self._filter(self._Here)
        for name in ("C:/work/.claude.json.lock", "C:/work/film.mp4.crdownload",
                     "C:/work/notes.md.tmp", "C:/work/notes.md~"):
            self.assertFalse(here.allows(self._file(name), "filesystem"), name)

    def test_nor_a_coding_assistants_own_session_files(self) -> None:
        # Its transcripts are already read as conversation by the assistant
        # listener; reading them again as files records the same thing twice.
        self.assertFalse(self._filter(self._Here).allows(
            self._file("C:/Users/someone/.claude/sessions/a.jsonl"), "filesystem"))

    def test_but_the_users_own_file_is_untouched_by_any_of_it(self) -> None:
        here = self._filter(self._Here)
        for name in ("C:/Users/someone/Desktop/project/build.py",
                     "C:/work/report.md", "C:/work/data.sqlite"):
            self.assertTrue(here.allows(self._file(name), "filesystem"), name)

    def test_the_patterns_are_a_setting_not_a_rule(self) -> None:
        self.app.config.set("listener_excluded_file_patterns", "*.md")
        here = self._filter(self._Here)
        self.assertFalse(here.allows(self._file("C:/work/report.md"), "filesystem"))
        self.assertTrue(here.allows(
            self._file("C:/Users/someone/ntuser.dat.LOG2"), "filesystem"))

    def test_a_file_event_says_what_was_on_screen(self) -> None:
        """Being at the keyboard does not make every write yours.

        A background program writes while the user works - a coding
        assistant rewriting its configuration every few seconds, a sync
        client, a build. Presence says *somebody* was there; what was on
        screen is the evidence for which of them did this, and it is
        recorded rather than judged, because no rule here could tell an
        editor saving a file from a compiler writing one the user asked for.
        """
        from personalized_agent.listener.providers.filesystem import FilesystemProvider

        class Screen:
            class activity:
                @staticmethod
                def get_active_window():
                    class Window:
                        application = "sublime_text.exe"
                        title = "build.py - personalized_agent_release"
                    return Window()

        captured = []
        provider = FilesystemProvider(self.app.config, platform=Screen)
        provider.bind(captured.append)
        provider._emit_event("filesystem.modified", {"path": "C:/work/build.py"})
        provider.flush_held_events()
        self.assertEqual(captured[0].data["while_using"], "sublime_text.exe")
        self.assertIn("build.py", captured[0].data["while_showing"])

    def test_it_is_asked_once_a_second_rather_than_once_an_event(self) -> None:
        # A burst of ten thousand file events must not be ten thousand calls
        # into the window manager.
        from personalized_agent.listener.providers.filesystem import FilesystemProvider

        asked = []

        class Screen:
            class activity:
                @staticmethod
                def get_active_window():
                    asked.append(1)
                    class Window:
                        application = "x.exe"
                        title = "t"
                    return Window()

        provider = FilesystemProvider(self.app.config, platform=Screen)
        provider.bind(lambda event: None)
        for _ in range(500):
            provider._emit_event("filesystem.modified", {"path": "C:/work/a.txt"})
        self.assertLessEqual(len(asked), 3)

    def test_without_a_platform_it_simply_says_nothing(self) -> None:
        from personalized_agent.listener.providers.filesystem import FilesystemProvider

        captured = []
        provider = FilesystemProvider(self.app.config)
        provider.bind(captured.append)
        provider._emit_event("filesystem.modified", {"path": "C:/work/a.txt"})
        provider.flush_held_events()
        self.assertNotIn("while_using", captured[0].data)

    def test_the_assistants_own_configuration_is_never_recorded(self) -> None:
        # Rewritten every few seconds while it runs, in the home directory,
        # where a directory exclusion cannot reach it. Measured in one
        # twenty-minute window: 44 changes to it and 64 to its lock file.
        here = self._filter(self._Here)
        for name in ("C:/Users/someone/.claude.json",
                     "C:/Users/someone/.claude.json.lock",
                     "C:/Users/someone/somewhere/.in_use"):
            self.assertFalse(here.allows(self._file(name), "filesystem"), name)

    # -- and whose doing it was -------------------------------------------
    def test_only_what_the_window_in_front_is_about(self) -> None:
        """The only test that speaks about *this* write.

        Presence says somebody was working; what was in front says which of
        them wrote this. A chat client's message index never appears in any
        title, and never will.
        """
        here = self._filter(self._Here)
        wechat = ListenerEvent(
            event_type="filesystem.modified", source="filesystem",
            data={"path": "C:/Users/me/xwechat_files/db_storage/message_fts.db",
                  "while_showing": "build.py - personalized_agent_release"})
        mine = ListenerEvent(
            event_type="filesystem.modified", source="filesystem",
            data={"path": "C:/Users/me/Desktop/personalized_agent_release/build.py",
                  "while_showing": "build.py - personalized_agent_release"})
        self.assertFalse(here.allows(wechat, "filesystem"))
        self.assertTrue(here.allows(mine, "filesystem"))

    def test_a_folder_above_it_counts_too(self) -> None:
        # The case that saves real work: a build started from a terminal
        # whose title names the project writes a hundred files the title
        # never mentions, and they are all under it.
        built = ListenerEvent(
            event_type="filesystem.modified", source="filesystem",
            # Not under "dist" or "build": those are excluded by name before
            # this test is reached, which is right and is a different rule.
            data={"path": "C:/Users/me/Desktop/personalized_agent_release/docs/reader.html",
                  "while_showing": "personalized_agent_release - python build.py"})
        self.assertTrue(self._filter(self._Here).allows(built, "filesystem"))

    def test_a_name_too_short_to_mean_anything_does_not_count(self) -> None:
        # "src" and "bin" are inside half the titles on a machine.
        self.assertFalse(self._filter(self._Here).on_screen(
            "C:/a/src/thing.o", "Inbox - src of the problem"))

    def test_with_nothing_on_screen_it_says_no(self) -> None:
        self.assertFalse(self._filter(self._Here).on_screen("C:/a/b.txt", ""))

    def test_it_is_on_unless_turned_off(self) -> None:
        """The default changed when the question became answerable.

        It used to ask only whether the window in front *now* named the file,
        which is evidence about nothing for a read reported an hour late. Now
        it can ask about the moment the change happened, and read the
        desktop's own record of what the user opened - so it is worth having
        on, and it is what the user asked the agent to record.
        """
        from personalized_agent.common.config.schema import SCHEMA_BY_KEY

        self.assertTrue(SCHEMA_BY_KEY["listener_only_what_the_user_did"].default)
        showing_something_else = ListenerEvent(
            event_type="filesystem.modified", source="filesystem",
            data={"path": "C:/work/report.md", "while_showing": "Inbox"})
        self.assertFalse(
            self._filter(self._Here).allows(showing_something_else, "filesystem"))

    def test_but_what_cannot_be_judged_is_kept(self) -> None:
        """Absence of evidence is not evidence.

        Neither test can say a change was *not* the user's: a file opened
        from a command line enters no Recent list, and a moment before the
        agent started has no trail behind it. An agent that read silence as a
        refusal would quietly record nothing, which is the failure this
        section opens by warning about.
        """
        nothing_known = ListenerEvent(
            event_type="filesystem.modified", source="filesystem",
            data={"path": "C:/work/report.md"})
        self.assertTrue(self._filter(self._Here).allows(nothing_known, "filesystem"))

    def test_the_old_setting_is_dropped_rather_than_carried(self) -> None:
        # Someone who left the old one off was declining a test that could
        # not see either of these things; carrying that "no" across would
        # make it a refusal of something they were never offered.
        from personalized_agent.common.config.store import ConfigStore

        self.assertIn("listener_only_what_is_on_screen", ConfigStore.RETIRED)

    def test_this_machine_can_answer_the_question_at_all(self) -> None:
        # The gate is worth nothing on a platform that cannot say, and this
        # is the platform the measurements above were taken on.
        from personalized_agent.platform_layer import get_platform

        idle = get_platform().activity.seconds_since_input()
        if os.name != "nt":
            self.skipTest("only Windows implements this so far")
        self.assertIsNotNone(idle)
        self.assertGreaterEqual(idle, 0.0)


class AWatcherThatStoppedSaysSoTests(unittest.TestCase):
    """Section 19, as amended: still an object is not still listening.

    Watching a whole drive is the case that showed it. The notification
    buffer for a busy volume overflows, the emitter thread ends - and the
    observer goes on being an object with a root attached, so the provider
    reported ``running``, ``watching`` and no error while its count sat
    frozen and nothing was recorded at all.

    A monitor that is off and says it is on is worse than one that is off.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _provider(self, observer):
        from personalized_agent.listener.providers.filesystem import FilesystemProvider

        provider = FilesystemProvider(self.app.config)
        provider._observer = observer
        provider._roots = {"C:\\": "watching"}
        return provider

    class _Observer:
        def __init__(self, alive, emitters):
            self._alive = alive
            self.emitters = emitters

        def is_alive(self):
            return self._alive

    class _Emitter:
        def __init__(self, alive):
            self._alive = alive

        def is_alive(self):
            return self._alive

    def test_a_live_watcher_is_reported_as_watching(self) -> None:
        status = self._provider(
            self._Observer(True, [self._Emitter(True)])).get_status()
        self.assertTrue(status["running"])
        self.assertEqual(status["roots"], {"C:\\": "watching"})
        self.assertEqual(status["last_error"], "")

    def test_an_emitter_that_died_is_not_watching(self) -> None:
        status = self._provider(
            self._Observer(True, [self._Emitter(False)])).get_status()
        self.assertFalse(status["running"])
        self.assertEqual(status["roots"], {"C:\\": "stopped"})
        self.assertIn("stopped delivering", status["last_error"])

    def test_nor_is_an_observer_with_no_emitters_at_all(self) -> None:
        self.assertFalse(self._provider(self._Observer(True, [])).get_status()["running"])

    def test_nor_one_whose_thread_has_ended(self) -> None:
        self.assertFalse(self._provider(
            self._Observer(False, [self._Emitter(True)])).get_status()["running"])

    def test_the_reason_says_what_to_do_about_it(self) -> None:
        # The fix is not obvious from "stopped": the cause is almost always a
        # directory too busy to watch, and the answer is to narrow it.
        status = self._provider(self._Observer(False, [])).get_status()
        self.assertIn("whole drive", status["last_error"])
        self.assertIn("Narrow", status["last_error"])


class ASweepIsAProgramNotAPersonTests(unittest.TestCase):
    """Section 19, as amended: a thousand files in one second.

    With the machine's own churn excluded and somebody checked to be at the
    keyboard, what was left was still mostly not the user: a commit reading
    every file in a repository, a build, a search. Measured over two hours on
    one machine, of **16,721 file events only 40 arrived alone** in their
    second, and 4,874 arrived in seconds holding more than a hundred. The
    busiest second held 1,076.

    A person opens one file at a time. Counting a tree walk as a thousand
    acts of reading is not a small error - it is the whole record, and it
    buries the one file they actually opened.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.seen = []
        self.provider = self._provider()

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _provider(self):
        from personalized_agent.listener.providers.filesystem import FilesystemProvider

        provider = FilesystemProvider(self.agent.app.config)
        provider.bind(self.seen.append)
        return provider

    def _touch(self, count, prefix="C:/repo/file"):
        for index in range(count):
            self.provider._emit_event("filesystem.opened",
                                      {"path": f"{prefix}{index}.py"})

    def test_one_file_at_a_time_is_a_person(self) -> None:
        self.provider._emit_event("filesystem.modified", {"path": "C:/work/report.md"})
        self.provider.flush_held_events()
        self.assertEqual(len(self.seen), 1)

    def test_a_handful_together_is_still_a_person(self) -> None:
        # Opening a file touches it and perhaps a lock or a backup beside it;
        # saving touches two or three. The threshold has to sit above that.
        for name in ("report.md", ".report.md.swp", "report.md~"):
            self.provider._emit_event("filesystem.modified", {"path": f"C:/work/{name}"})
        self.provider.flush_held_events()
        self.assertEqual(len(self.seen), 3)

    def test_a_tree_walk_is_not(self) -> None:
        self._touch(400)
        self.provider.flush_held_events()
        self.assertEqual(self.seen, [])

    def test_and_it_says_a_sweep_happened(self) -> None:
        """That it happened is worth counting; the four hundred files are not.

        Dropping them without a trace would leave the record looking as
        though nothing at all occurred in a minute when a great deal did.
        """
        self._touch(400)
        self.provider.flush_held_events()
        status = self.provider.get_status()
        self.assertEqual(status["sweeps"], 1)
        self.assertEqual(status["swept_past"], 400)
        self.assertEqual(status["events"], 400, "it still counts what it saw")

    def test_a_person_working_after_a_sweep_is_recorded_again(self) -> None:
        # The window is judged on its own; a sweep does not silence what
        # comes after it.
        self._touch(400)
        self.provider.flush_held_events()
        self.provider._emit_event("filesystem.modified", {"path": "C:/work/report.md"})
        self.provider.flush_held_events()
        self.assertEqual([event.data["path"] for event in self.seen],
                         ["C:/work/report.md"])

    def test_a_sweep_across_a_second_boundary_is_still_one_sweep(self) -> None:
        """Together means close to each other, not inside the same second.

        Found by a test that failed about one run in ten: cutting the clock
        into seconds and judging each one separately splits a tree walk that
        starts at 11:59.998, and the tail of three files left on the far side
        looks exactly like somebody opening three files. Measured from each
        event to the one before it instead, the walk is one walk.
        """
        start = time.time() - 60
        for index in range(40):
            # Forty files over four hundredths of a second, arranged to
            # straddle the boundary.
            self.provider._emit_event(
                "filesystem.opened", {"path": f"C:/repo/file{index}.py"},
                start - 0.02 + index * 0.001)
        self.provider.flush_held_events()
        self.assertEqual(self.seen, [])
        self.assertEqual(self.provider.get_status()["sweeps"], 1)

    def test_and_work_spread_out_in_time_is_not_one_group(self) -> None:
        # Twenty files, but a minute apart: nothing about that is a sweep.
        start = time.time() - 3600
        for index in range(20):
            self.provider._emit_event(
                "filesystem.opened", {"path": f"C:/work/note{index}.md"},
                start + index * 60)
        self.provider.flush_held_events()
        self.assertEqual(len(self.seen), 20)
        self.assertEqual(self.provider.get_status()["sweeps"], 0)

    def test_the_threshold_is_what_a_person_could_plausibly_cause(self) -> None:
        from personalized_agent.listener.providers.filesystem import FilesystemProvider

        self.assertGreaterEqual(FilesystemProvider.BURST_IS_A_PROGRAM, 4)
        self.assertLessEqual(FilesystemProvider.BURST_IS_A_PROGRAM, 20)

    def test_the_window_closes_on_its_own(self) -> None:
        # Nothing here is read in real time, so a second's wait costs
        # nothing - but it has to end without being asked.
        self.provider._emit_event("filesystem.modified", {"path": "C:/work/report.md"})
        self.assertEqual(self.seen, [], "not yet: it has not been seen in company")
        deadline = time.time() + self.provider.BURST_WINDOW_SECONDS + 4
        while not self.seen and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(len(self.seen), 1)

    def test_what_is_held_when_the_listener_stops_is_not_lost(self) -> None:
        # Otherwise the last second of the user's day disappears every time
        # the agent is closed.
        self.provider._emit_event("filesystem.modified", {"path": "C:/work/report.md"})
        self.provider.stop()
        self.assertEqual(len(self.seen), 1)


class ADirectoryReportsEverythingInsideItTests(unittest.TestCase):
    """Section 19, as amended: one write, reported once per directory above it.

    A directory reports itself modified whenever anything inside it changes,
    so writing one file produced an event for the file and one for every
    directory containing it. Nothing read them - the Overview skips them, the
    templater discards them - and they were a fifth of what was stored.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.seen = []
        from personalized_agent.listener.providers.filesystem import FilesystemProvider

        self.provider = FilesystemProvider(self.agent.app.config)
        self.provider.bind(self.seen.append)

    def tearDown(self) -> None:
        self.agent.cleanup()

    class _Raw:
        def __init__(self, path, kind="modified", is_directory=True):
            self.src_path = path
            self.event_type = kind
            self.is_directory = is_directory

    def _notify(self, *raw):
        for event in raw:
            self.provider.handle_raw(event)
        self.provider.flush_held_events()
        return [event.event_type for event in self.seen]

    def test_a_directory_reporting_itself_modified_is_dropped(self) -> None:
        self.assertEqual(self._notify(self._Raw("C:/work")), [])

    def test_but_a_directory_created_or_deleted_still_counts(self) -> None:
        # Making a folder is something the user did; a folder saying its
        # contents changed is the contents, reported a second time.
        self.assertEqual(
            self._notify(self._Raw("C:/work/new", "created"),
                         self._Raw("C:/work/old", "deleted")),
            ["filesystem.directory_created", "filesystem.directory_deleted"])

    def test_and_a_file_underneath_it_is_untouched_by_this(self) -> None:
        self.assertEqual(
            self._notify(self._Raw("C:/work/report.md", "created", False)),
            ["filesystem.created"])

    def test_it_is_not_declared_as_something_this_produces(self) -> None:
        # A type nothing emits but every reader filters is a type everybody
        # downstream has to know about for no reason.
        self.assertNotIn("filesystem.directory_modified",
                         self.provider.supported_event_types())


class AReadIsReportedLongAfterItHappenedTests(unittest.TestCase):
    """Section 19, as amended: the notification is not the moment.

    Windows keeps a file's last access time in memory and writes it to disk
    when it gets round to it; the notification comes with that write, not
    with the read. Measured on one machine: three files read at 22:12:07
    produced no notification at all in the next thirty-five seconds, while a
    single second - 03:02:35 - carried 47 files read across the preceding
    hour.

    Dated by arrival all of them are "read at 03:02:35", which is both wrong
    and useless: nobody opens forty-seven files in a second, and the hour in
    which they were actually opened, one at a time, is gone. The file itself
    knows when it was read, so it is asked.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.root = Path(tempfile.mkdtemp(prefix="pa-atime-"))
        self.seen = []
        from personalized_agent.listener.providers.filesystem import FilesystemProvider

        self.provider = FilesystemProvider(self.agent.app.config)
        self.provider.bind(self.seen.append)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        self.agent.cleanup()

    class _Raw:
        is_directory = False
        event_type = "modified"

        def __init__(self, path):
            self.src_path = str(path)

    def _a_file_read(self, name, minutes_ago):
        """A file last written long ago and last read `minutes_ago`."""
        path = self.root / name
        path.write_text("x", encoding="utf-8")
        written = time.time() - 86400
        os.utime(path, (time.time() - minutes_ago * 60, written))
        return path

    def _notify(self, *paths):
        for path in paths:
            self.provider.handle_raw(self._Raw(path))
        self.provider.flush_held_events()
        return self.seen

    def test_a_read_is_dated_by_when_it_was_read(self) -> None:
        path = self._a_file_read("report.md", minutes_ago=40)
        [event] = self._notify(path)
        self.assertEqual(event.event_type, "filesystem.opened")
        late = (dt.datetime.now(dt.timezone.utc) - event.timestamp).total_seconds()
        self.assertGreater(late, 30 * 60, "not dated to the moment we were told")
        self.assertLess(late, 50 * 60)

    def test_a_write_is_dated_now_because_that_is_when_it_arrived(self) -> None:
        path = self.root / "fresh.md"
        path.write_text("x", encoding="utf-8")
        [event] = self._notify(path)
        self.assertEqual(event.event_type, "filesystem.modified")
        self.assertLess(
            abs((dt.datetime.now(dt.timezone.utc) - event.timestamp).total_seconds()), 5)

    def test_an_hour_old_read_is_not_labelled_with_the_window_in_front(self) -> None:
        """What is on screen now is no evidence about what happened an hour ago.

        Labelling the flush with the current window filed forty-seven files
        opened across a morning under whatever the user happened to be
        looking at when Windows got round to writing.
        """
        from personalized_agent.listener.providers.filesystem import FilesystemProvider

        class Screen:
            class activity:
                @staticmethod
                def get_active_window():
                    class Window:
                        application = "firefox.exe"
                        title = "something else entirely"
                    return Window()

        seen = []
        provider = FilesystemProvider(self.agent.app.config, platform=Screen)
        provider.bind(seen.append)
        provider.handle_raw(self._Raw(self._a_file_read("old.md", minutes_ago=40)))
        provider.handle_raw(self._Raw(self._a_file_read("justnow.md", minutes_ago=0)))
        provider.flush_held_events()
        by_name = {os.path.basename(e.data["path"]): e for e in seen}
        self.assertNotIn("while_using", by_name["old.md"].data)
        self.assertEqual(by_name["justnow.md"].data.get("while_using"), "firefox.exe")

    def test_one_flush_holding_a_sweep_and_a_person_keeps_the_person(self) -> None:
        """The lump is Windows writing, not anybody doing anything.

        Everything Windows had been holding arrives in the same second, so
        judged as one batch every read there is would be thrown away. Judged
        by the second each file was actually touched in, the tree walk is
        still a tree walk and the four files the user opened are still four
        files the user opened.
        """
        swept = [self._a_file_read(f"scan{i}.py", minutes_ago=50) for i in range(60)]
        opened = [self._a_file_read(f"mine{i}.md", minutes_ago=10 + i * 2)
                  for i in range(4)]
        self._notify(*(swept + opened))
        kept = sorted(os.path.basename(e.data["path"]) for e in self.seen)
        self.assertEqual(kept, ["mine0.md", "mine1.md", "mine2.md", "mine3.md"])
        self.assertEqual(self.provider.get_status()["swept_past"], 60)

    def test_an_access_time_from_last_week_is_not_an_opening(self) -> None:
        # Neither written nor read lately: the notification was about its
        # permissions or attributes - a grant across the home raised one for
        # every file - and recording it as an opening said the user had
        # opened hundreds of files. Nothing is recorded.
        path = self._a_file_read("ancient.md", minutes_ago=60 * 24 * 7)
        self.assertEqual(self._notify(path), [])


class WhatWasInFrontAnHourAgoTests(unittest.TestCase):
    """Section 19, as amended: the window in front, for a moment that passed.

    The Listener has always known what was in front, and only ever for
    *now*. That is enough for a write, reported as it happens, and evidence
    about nothing for a read, reported when Windows flushes the access time
    up to an hour later.

    Nothing is sampled for the trail: the Application provider already emits
    a focus event whenever the window or its title changes, and those pass
    through the manager on their way to storage.
    """

    def setUp(self) -> None:
        from personalized_agent.listener.core.focus_trail import FocusTrail

        self.trail = FocusTrail()
        self.now = time.time()

    def test_it_answers_for_the_moment_asked_about(self) -> None:
        self.trail.remember("sublime_text.exe", "build.py - project", self.now - 3600)
        self.trail.remember("firefox.exe", "Inbox", self.now - 60)
        self.assertEqual(self.trail.at(self.now - 3000)[1], "build.py - project")
        self.assertEqual(self.trail.at(self.now - 30)[1], "Inbox")

    def test_a_moment_before_the_trail_begins_is_not_known(self) -> None:
        """Unknown, which is not the same as "nothing was in front".

        The agent was not running then, or had just started. Read as a
        refusal it would discard everything from the first hour of every run.
        """
        self.trail.remember("firefox.exe", "Inbox", self.now)
        self.assertEqual(self.trail.at(self.now - 7200), (None, None))

    def test_an_empty_trail_knows_nothing_rather_than_nothing_happened(self) -> None:
        self.assertEqual(self.trail.at(self.now), (None, None))

    def test_the_same_window_twice_is_one_entry(self) -> None:
        # A title that is only being animated is the same title, and the
        # Application provider already collapses those; this is the second
        # line of defence, and it keeps the trail small.
        for _ in range(50):
            self.trail.remember("firefox.exe", "Inbox", self.now)
        self.assertEqual(len(self.trail), 1)

    def test_it_forgets_what_nothing_can_still_ask_about(self) -> None:
        from personalized_agent.listener.core.focus_trail import FocusTrail

        self.trail.remember("old.exe", "yesterday", self.now - FocusTrail.REMEMBER_FOR * 2)
        self.trail.remember("mid.exe", "earlier", self.now - FocusTrail.REMEMBER_FOR - 10)
        self.trail.remember("new.exe", "now", self.now)
        # The one that was in front *at* the cutoff is kept: dropping it
        # would lose every moment between it and the next change.
        self.assertLessEqual(len(self.trail), 2)
        self.assertEqual(self.trail.at(self.now)[0], "new.exe")

    def test_a_clock_that_steps_backwards_does_not_break_the_search(self) -> None:
        self.trail.remember("a.exe", "first", self.now)
        self.trail.remember("b.exe", "second", self.now - 600)
        self.assertEqual(self.trail.at(self.now + 1)[0], "b.exe")

    def test_the_manager_feeds_it_from_the_events_that_pass_through(self) -> None:
        agent = TemporaryAgent()
        try:
            privacy = agent.app.listener.privacy
            before = len(privacy.focus_trail)
            privacy.remember_what_was_in_front(ListenerEvent(
                event_type="application.focused", source="application",
                application="sublime_text.exe",
                data={"window_title": "notes.md - work"}))
            self.assertEqual(len(privacy.focus_trail), before + 1)
        finally:
            agent.cleanup()


class TheDesktopKnowsWhatItsUserOpenedTests(unittest.TestCase):
    """Section 19, as amended: a record that only a person can get into.

    Every desktop keeps a list of the files its user opened, for its own
    purposes. What makes it worth reading here is what never goes in it: a
    program calling ``open()`` does not appear. Measured on one machine -
    reading three files from Python added nothing to the Windows Recent
    folder at all, while the files the user had opened in Explorer that
    evening were each there to the second.
    """

    def setUp(self) -> None:
        from personalized_agent.listener.core.user_opened import WhatTheUserOpened

        self.now = time.time()
        self.opened = {os.path.normcase(r"C:/work/report.md"): self.now - 120,
                       os.path.normcase("slides.key"): self.now - 60}

        class Desktop:
            class activity:
                @staticmethod
                def files_the_user_opened(since):
                    return dict(self.opened)

        self.asking = WhatTheUserOpened(Desktop)

    def test_a_file_the_user_opened_is_recognised_by_its_path(self) -> None:
        self.assertTrue(self.asking.opened_by_the_user(r"C:/work/report.md", self.now))

    def test_and_by_its_name_where_that_is_all_the_record_holds(self) -> None:
        # Windows names the shortcut after the file and does not always give
        # up the path inside it. Two files of the same name in different
        # folders is a wrong answer in the generous direction, which is the
        # direction this evidence is allowed to be wrong in.
        self.assertTrue(self.asking.opened_by_the_user(r"C:/decks/slides.key", self.now))

    def test_a_file_nobody_opened_is_not(self) -> None:
        self.assertFalse(self.asking.opened_by_the_user(r"C:/work/other.md", self.now))

    def test_the_same_file_opened_yesterday_is_not_this_morning(self) -> None:
        self.assertFalse(
            self.asking.opened_by_the_user(r"C:/work/report.md", self.now - 86400))

    def test_a_desktop_that_keeps_no_such_record_says_nothing(self) -> None:
        from personalized_agent.listener.core.user_opened import WhatTheUserOpened

        self.assertFalse(WhatTheUserOpened(None).opened_by_the_user("C:/a.md", self.now))

    def test_a_desktop_that_fails_to_answer_is_not_fatal(self) -> None:
        from personalized_agent.listener.core.user_opened import WhatTheUserOpened

        class Broken:
            class activity:
                @staticmethod
                def files_the_user_opened(since):
                    raise OSError("spotlight is not running")

        asking = WhatTheUserOpened(Broken)
        self.assertFalse(asking.opened_by_the_user("C:/a.md", self.now))
        self.assertTrue(asking.describe()["last_error"])


class WhoseDoingItWasTests(unittest.TestCase):
    """Section 19, as amended: being at the keyboard is not doing this.

    An assistant, a build, a test run and a language server all read and
    write while the user sits there doing something else. The notification
    cannot say which of them it was - on Windows it carries no process at
    all - so two pieces of positive evidence are used, and neither knows or
    cares which assistant is installed.
    """

    class _Here:
        class activity:
            @staticmethod
            def seconds_since_input():
                return 3.0

    def setUp(self) -> None:
        from personalized_agent.listener.core.privacy import PrivacyFilter

        self.agent = TemporaryAgent()
        self.now = time.time()
        self.agent.app.config.set("listener_enabled", True)
        self.agent.app.config.set("listener_filesystem_enabled", True)
        self.agent.app.config.set("listener_browser_enabled", True)
        self.privacy = PrivacyFilter(self.agent.app.config, platform=self._Here)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _file(self, path, **data):
        return ListenerEvent(
            event_type="filesystem.opened", source="filesystem",
            timestamp=dt.datetime.fromtimestamp(self.now, dt.timezone.utc),
            data={"path": path, **data})

    def test_a_file_the_window_in_front_was_about_is_the_users(self) -> None:
        self.assertTrue(self.privacy.the_user_did_this(
            "C:/work/report.md", {"while_showing": "report.md - work"}, self.now))

    def test_a_file_nothing_on_screen_mentioned_is_not(self) -> None:
        # The case this exists for: an assistant reading a source file while
        # the user reads their mail.
        self.assertFalse(self.privacy.the_user_did_this(
            "C:/work/store.py", {"while_showing": "Inbox - Outlook"}, self.now))

    def test_an_hour_old_read_is_judged_by_what_was_in_front_then(self) -> None:
        """The whole point of keeping a trail.

        The event carries no window of its own, because labelling it with
        the one that is in front an hour later would be worse than nothing.
        """
        an_hour_ago = self.now - 3600
        self.privacy.focus_trail.remember(
            "sublime_text.exe", "templates.py", an_hour_ago - 30)
        self.assertTrue(self.privacy.the_user_did_this(
            "C:/project/templates.py", {}, an_hour_ago))
        self.assertFalse(self.privacy.the_user_did_this(
            "C:/project/store.py", {}, an_hour_ago))

    def test_and_by_what_the_desktop_says_they_opened(self) -> None:
        opened = {os.path.normcase("C:/project/store.py"): self.now}

        class Desktop:
            class activity:
                @staticmethod
                def files_the_user_opened(since):
                    return dict(opened)

        self.privacy.user_opened.platform = Desktop
        self.privacy.user_opened._asked_at = 0.0
        # Nothing on screen named it, and it is still theirs: they opened it
        # from the file manager and then looked at something else.
        self.assertTrue(self.privacy.the_user_did_this(
            "C:/project/store.py", {"while_showing": "Inbox - Outlook"}, self.now))

    def test_a_moment_the_trail_does_not_reach_is_kept(self) -> None:
        self.assertTrue(self.privacy.the_user_did_this(
            "C:/project/store.py", {}, self.now - 86400))

    def test_in_front_with_nothing_in_the_title_is_known_and_is_not_about_a_file(self) -> None:
        # A window that is in front and has no title cannot be about any
        # file, which is a different answer from "we do not know".
        self.assertFalse(self.privacy.the_user_did_this(
            "C:/project/store.py", {"while_using": "python.exe"}, self.now))

    def test_the_filter_applies_it_to_file_events(self) -> None:
        self.assertFalse(self.privacy.allows(
            self._file("C:/project/store.py", while_showing="Inbox"), "filesystem"))
        self.assertTrue(self.privacy.allows(
            self._file("C:/project/store.py", while_showing="store.py - project"),
            "filesystem"))

    def test_and_leaves_everything_else_alone(self) -> None:
        # A page visited or a window brought to the front is already
        # somebody doing something; there is no file to ask about.
        page = ListenerEvent(event_type="browser.visit", source="browser",
                             data={"url": "https://example.com"})
        self.assertTrue(self.privacy.allows(page, "browser"))

    def test_the_titles_this_was_measured_against(self) -> None:
        """Taken from one machine's focus events over eighteen hours.

        The worry this answers: an assistant runs in a terminal, and if the
        terminal's title named the project then every source file it read
        would match a folder above it and be recorded as the user's. It does
        not. A terminal running an assistant shows the **conversation's**
        title, which is about the task and not about the tree - so the files
        it reads on the user's behalf match nothing, which is the answer
        wanted. A terminal running a command the user typed shows that
        command, and the file named in it matches, which is also right: the
        user did type it.
        """
        cases = [
            # (title actually observed, path, whether it is the user's)
            ("\u2733 Upload instruction wc \u547d\u4ee4\u62a5\u9519",
             "C:/Users/me/Desktop/personalized_agent_release/store.py", False),
            ("C:\\WINDOWS\\system32\\cmd.exe - python  run.py",
             "C:/Users/me/Desktop/personalized_agent_release/store.py", False),
            ("C:\\WINDOWS\\system32\\cmd.exe - python  run.py",
             "C:/Users/me/Desktop/personalized_agent_release/run.py", True),
            ("personalized_agent_release - File Explorer",
             "C:/Users/me/Desktop/personalized_agent_release/notes.md", True),
            ("Inbox (27,462) - someone@example.com - Gmail \u2014 Mozilla Firefox",
             "C:/Users/me/Desktop/personalized_agent_release/store.py", False),
        ]
        for title, path, theirs in cases:
            self.assertEqual(
                self.privacy.the_user_did_this(path, {"while_showing": title},
                                               self.now),
                theirs, title)

    def test_turning_it_off_records_everything_again(self) -> None:
        self.agent.app.config.set("listener_only_what_the_user_did", False)
        self.assertTrue(self.privacy.allows(
            self._file("C:/project/store.py", while_showing="Inbox"), "filesystem"))


class ReadingEachDesktopsRecordTests(unittest.TestCase):
    """Section 19, as amended: three systems, one question.

    Explorer writes a shortcut, LaunchServices stamps an attribute, GTK
    appends to a list. None of it needs a privilege, an entitlement or a
    daemon - it is reading what the desktop writes anyway.
    """

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="pa-recent-"))
        self.now = time.time()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    @staticmethod
    def _as_this_machine_writes_it(path: str) -> str:
        # These two readers are for desktops this suite may not be running
        # on, and a POSIX path made absolute on Windows acquires a drive
        # letter. Case is deliberately *not* normalised: the readers return
        # paths as the filesystem spells them, because normalising at the
        # source once put "readme.md" in front of somebody who had opened
        # "README.md".
        return os.path.abspath(path)

    # -- Windows ----------------------------------------------------------
    def test_a_shortcut_gives_up_the_path_inside_it(self) -> None:
        """Parsed rather than resolved.

        Resolving a shortcut properly means loading the shell into this
        process, and for one pointing at a share it means touching the
        network. Two files called notes.md in different projects are
        different files, so the path is worth digging out.
        """
        from personalized_agent.platform_layer.recent_files import (
            _what_the_shortcut_points_at)

        target = r"C:\work\notes.md"
        lnk = self.root / "notes.md.lnk"
        lnk.write_bytes(self._shortcut(target))
        self.assertEqual(_what_the_shortcut_points_at(lnk), target)

    def test_something_that_is_not_a_shortcut_yields_nothing(self) -> None:
        from personalized_agent.platform_layer.recent_files import (
            _what_the_shortcut_points_at)

        broken = self.root / "truncated.lnk"
        broken.write_bytes(b"not a shell link at all")
        self.assertIsNone(_what_the_shortcut_points_at(broken))

    def test_the_recent_folder_is_narrowed_by_time_before_it_is_read(self) -> None:
        # A folder filling up for months holds a thousand shortcuts and the
        # window asked about holds two. Measured: 220 ms to sort and parse
        # them all, 13 ms to narrow first.
        from personalized_agent.platform_layer import recent_files

        old = self.root / "ancient.lnk"
        old.write_bytes(self._shortcut(r"C:\old\ancient.md"))
        os.utime(old, (self.now - 86400, self.now - 86400))
        fresh = self.root / "today.lnk"
        fresh.write_bytes(self._shortcut(r"C:\work\today.md"))
        original = recent_files._windows_recent_folder
        recent_files._windows_recent_folder = lambda: self.root
        try:
            found = recent_files.windows_recent(self.now - 3600)
        finally:
            recent_files._windows_recent_folder = original
        self.assertIn(os.path.abspath(r"C:\work\today.md"), found)
        self.assertNotIn(os.path.abspath(r"C:\old\ancient.md"), found)

    @staticmethod
    def _shortcut(target: str) -> bytes:
        """The smallest .lnk that says where it points (MS-SHLLINK)."""
        header = bytearray(0x4C)
        header[0:4] = (0x4C).to_bytes(4, "little")
        header[20:24] = (0x02).to_bytes(4, "little")      # HasLinkInfo only
        path = target.encode("mbcs" if os.name == "nt" else "utf-8") + b"\x00"
        link_info = bytearray()
        link_info += (0x1C + len(path)).to_bytes(4, "little")   # LinkInfoSize
        link_info += (0x1C).to_bytes(4, "little")               # HeaderSize
        link_info += (0x01).to_bytes(4, "little")               # flags
        link_info += (0).to_bytes(4, "little")                  # VolumeIDOffset
        link_info += (0x1C).to_bytes(4, "little")               # LocalBasePathOffset
        link_info += (0).to_bytes(4, "little")                  # network offset
        link_info += (0).to_bytes(4, "little")                  # CommonPathSuffix
        link_info += path
        return bytes(header) + bytes(link_info)

    # -- macOS ------------------------------------------------------------
    def test_spotlight_is_asked_which_files_then_when(self) -> None:
        from personalized_agent.platform_layer.recent_files import macos_recent

        asked = []

        def run(command):
            asked.append(command[0])
            if command[0] == "mdfind":
                return "/Users/me/report.md\0/Users/me/slides.key\0"
            return "2026-09-19 03:11:34 +0000\n2026-09-19 03:12:00 +0000"

        found = macos_recent(self.now - 3600, run=run)
        self.assertEqual(asked, ["mdfind", "mdls"])
        self.assertEqual(len(found), 2)
        self.assertIn(self._as_this_machine_writes_it("/Users/me/report.md"), found)

    def test_values_that_do_not_line_up_are_not_trusted(self) -> None:
        """A wrong time on a real path is worse than no answer.

        ``mdls -raw`` gives one value per file with nothing between them, so
        a count that does not match means they cannot be paired.
        """
        from personalized_agent.platform_layer.recent_files import macos_recent

        def run(command):
            if command[0] == "mdfind":
                return "/Users/me/a.md\0/Users/me/b.md\0"
            return "2026-09-19 03:11:34 +0000"

        self.assertEqual(macos_recent(self.now - 3600, run=run), {})

    # -- Linux ------------------------------------------------------------
    def test_the_freedesktop_list_is_read(self) -> None:
        from personalized_agent.platform_layer.recent_files import linux_recent

        share = self.root / ".local" / "share"
        share.mkdir(parents=True)
        recent = dt.datetime.now(dt.timezone.utc).isoformat()
        (share / "recently-used.xbel").write_text(
            '<?xml version="1.0"?>\n<xbel>'
            f'<bookmark href="file:///home/me/report.md" visited="{recent}"/>'
            '<bookmark href="file:///home/me/old.md" visited="2020-01-01T00:00:00Z"/>'
            '</xbel>', encoding="utf-8")
        found = linux_recent(self.now - 3600, home=self.root)
        self.assertIn(self._as_this_machine_writes_it("/home/me/report.md"), found)
        self.assertNotIn(self._as_this_machine_writes_it("/home/me/old.md"), found)

    def test_a_list_that_is_not_there_is_not_an_error(self) -> None:
        from personalized_agent.platform_layer.recent_files import linux_recent

        self.assertEqual(linux_recent(self.now - 3600, home=self.root), {})

    # -- every platform ---------------------------------------------------
    def test_a_platform_that_keeps_no_such_record_says_so(self) -> None:
        from personalized_agent.platform_layer.base import PlatformActivityProvider

        self.assertEqual(
            PlatformActivityProvider.files_the_user_opened(None, self.now), {})

    def test_each_platform_answers_the_question(self) -> None:
        from personalized_agent.platform_layer.linux import LinuxActivityProvider
        from personalized_agent.platform_layer.macos import MacActivityProvider
        from personalized_agent.platform_layer.windows import WindowsActivityProvider

        for provider in (WindowsActivityProvider, MacActivityProvider,
                         LinuxActivityProvider):
            self.assertIn("files_the_user_opened", vars(provider), provider.__name__)


class _Element:
    """A made-up UI Automation element: what a cache request fetched."""

    def __init__(self, tree, name, control=50007, offscreen=False, children=()):
        self.tree, self.name, self.control, self.offscreen = tree, name, control, offscreen
        self.children = list(children)
        self.parent = None
        for one in self.children:
            one.parent = self

    def GetCachedPropertyValue(self, which):
        from personalized_agent.platform_layer import window_text as wt
        return {wt._CONTROL_TYPE_PROPERTY: self.control, wt._NAME_PROPERTY: self.name,
                wt._IS_OFFSCREEN_PROPERTY: self.offscreen}.get(which, False)

    def GetCurrentPattern(self, _which):
        return None

    def FindAll(self, *_args):
        self.tree.found_all += 1
        raise AssertionError("FindAll over the descendants")


class _Tree:
    """A window holding a list of `items` files - and, behind the shown tab,
    one of `hidden_tab` - answering as UI Automation's walker does."""

    def __init__(self, items, each_call=0.0, hidden_tab=0):
        self.calls, self.found_all, self.each_call = 0, 0, each_call
        files = [_Element(self, f"file {index:04d}.txt") for index in range(items)]
        parts = [_Element(self, "Files", 50008, children=files)]
        if hidden_tab:
            parts.append(_Element(self, "Tab", 50033, offscreen=True, children=[
                _Element(self, f"behind {index}") for index in range(hidden_tab)]))
        self.root = _Element(self, "Window", 50032, children=parts)
        self.ControlViewWalker = self

    def CreateCacheRequest(self):
        return SimpleNamespace(AddProperty=lambda _which: None)

    def _ask(self):
        self.calls += 1
        if self.each_call:
            time.sleep(self.each_call)

    def GetFirstChildElementBuildCache(self, element, _cache):
        self._ask()
        return element.children[0] if element.children else None

    def GetNextSiblingElementBuildCache(self, element, _cache):
        self._ask()
        siblings = element.parent.children if element.parent else []
        at = siblings.index(element) + 1 if element in siblings else len(siblings)
        return siblings[at] if at < len(siblings) else None


class _Showing:
    """A platform that has a window in front and will say what is in it."""

    def __init__(self, text="", application="firefox.exe", title="a page",
                 idle=3.0, how="document", pid=999999):
        self.reading = _Reading(application, title, text, how, pid)
        self.idle = idle
        self.asked = 0
        outer = self

        class activity:
            @staticmethod
            def seconds_since_input():
                return outer.idle

            @staticmethod
            def text_in_front():
                outer.asked += 1
                return outer.reading

        self.activity = activity
        self.name = "test"

    def capabilities(self):
        from personalized_agent.platform_layer.base import PlatformCapabilities

        return PlatformCapabilities(window_text=True)


class _Reading:
    def __init__(self, application, title, text, how, pid=999999):
        self.application = application
        self.title = title
        self.text = text
        self.how = how
        self.elements = 3
        self.milliseconds = 12.0
        self.handle = 4321
        self.pid = pid


class WhatTheWindowInFrontWasShowingTests(unittest.TestCase):
    """Section 19.2: not that a window was there, but what was in it.

    Every other provider records that something happened. This one records
    what it was, which is the difference between "Firefox was open for two
    hours" and what those two hours were about - and it is a different kind
    of thing to keep, which is why it is off until it is turned on.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.config = self.agent.app.config
        # The provider now checks the master switch before native reads.
        self.config.set("listener_enabled", True)
        self.config.set("listener_screen_enabled", True)
        self.seen = []

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _provider(self, platform):
        from personalized_agent.listener.providers.screen import ScreenProvider

        provider = ScreenProvider(self.config, platform)
        provider.bind(self.seen.append)
        return provider

    def test_it_records_what_the_window_was_showing(self) -> None:
        provider = self._provider(_Showing("A paragraph about something.\n"
                                           "And a second line of it."))
        provider._look()
        provider.flush_what_is_gathered()
        [event] = self.seen
        self.assertEqual(event.event_type, "screen.text")
        self.assertIn("paragraph", event.data["text"])
        self.assertEqual(event.data["application"], "firefox.exe")
        self.assertEqual(event.data["window_title"], "a page")

    def test_the_same_window_read_again_is_not_a_second_record(self) -> None:
        """Nothing happened the second time.

        A page left open is read every few seconds and is the same page every
        time. Measured: five readings of one tab produced one record and four
        findings of no change.
        """
        platform = _Showing("The same thing, still on the screen.")
        provider = self._provider(platform)
        for _ in range(5):
            provider._look()
        provider.flush_what_is_gathered()
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(provider.get_status()["unchanged"], 4)

    def test_but_what_was_added_to_it_is(self) -> None:
        platform = _Showing("The first thing that was there.")
        provider = self._provider(platform)
        provider._look()
        provider.flush_what_is_gathered()
        platform.reading.text = ("The first thing that was there.\n"
                                 "And then something new appeared.")
        provider._look()
        provider.flush_what_is_gathered()
        self.assertEqual(len(self.seen), 2)
        self.assertIn("something new", self.seen[1].data["text"])
        self.assertNotIn("first thing", self.seen[1].data["text"],
                         "only what changed")

    def test_nothing_is_read_while_nobody_is_there(self) -> None:
        # A machine left alone still has a window in front of it, and
        # reading the same idle screen all night is not anybody's day.
        provider = self._provider(_Showing("Something on an idle screen.",
                                           idle=9999.0))
        provider._look()
        self.assertEqual(self.seen, [])

    def test_and_nothing_at_all_unless_it_is_turned_on(self) -> None:
        self.config.set("listener_screen_enabled", False)
        platform = _Showing("Something private.")
        provider = self._provider(platform)
        provider._look()
        self.assertEqual(self.seen, [])
        self.assertEqual(platform.asked, 0, "not even read")

    def test_it_is_on_and_one_switch_turns_it_off(self) -> None:
        """On by default, which puts the weight on what narrows it.

        A product decision, and the consequence is worth stating: somebody
        upgrading starts recording the contents of their windows without
        doing anything. What makes that defensible is everything else in
        this class - the window in front only, presence, password fields,
        the excluded list, and only what changed - together with a single
        switch that stops it.
        """
        from personalized_agent.common.config.schema import SCHEMA_BY_KEY

        self.assertTrue(SCHEMA_BY_KEY["listener_screen_enabled"].default)
        self.assertTrue(SCHEMA_BY_KEY["listener_screen_enabled"].restart_required)
        platform = _Showing("Something on the screen.")
        provider = self._provider(platform)
        self.config.set("listener_screen_enabled", False)
        provider._look()
        self.assertEqual(platform.asked, 0, "not read at all, not read and dropped")

    def test_the_agents_own_window_is_never_read(self) -> None:
        """The agent looking at itself.

        Its own desktop window is the certain case: the process is this one,
        whatever the window has been titled - and the user names the
        character, so there is no title to match on.
        """
        import os

        provider = self._provider(_Showing("Whatever the agent is showing",
                                           application="python.exe",
                                           title="Aria", pid=os.getpid()))
        provider._look()
        self.assertEqual(self.seen, [])
        self.assertEqual(provider.get_status()["our_own_windows"], 1)

    def test_and_neither_is_its_own_interface_in_a_browser(self) -> None:
        """A tab like any other, and the name in the title is what there is.

        Section 19 already says the agent does not hear its own writing. A
        record of somebody's day that is mostly the agent's own summaries
        read back to it is a record of nothing.
        """
        from personalized_agent.version import APP_NAME

        provider = self._provider(_Showing(
            "Overview Calendar Memory Chat Settings Debugger",
            application="firefox.exe",
            title=f"{APP_NAME} \u2014 Mozilla Firefox"))
        provider._look()
        self.assertEqual(self.seen, [])

    def test_but_a_terminal_running_an_assistant_is_still_recorded(self) -> None:
        """Left in on purpose, and worth saying why.

        An assistant runs in a terminal and the terminal is in front, so
        what it writes looks like what the user is reading - which it also
        is. Nor can it be told from the user's own terminal work: a process
        tree does not reach it, because Windows Terminal hosts its shells
        through ConPTY and they are not its children. Measured on one
        machine: claude.exe's parent is cmd.exe and its grandparent is
        explorer.exe, while the terminal window's own process has no
        descendants at all.

        So it is kept, and the overlap with the assistant provider - which
        reads the same conversation with the speakers marked - is left for a
        reader to reconcile rather than guessed at here.
        """
        provider = self._provider(_Showing(
            "A conversation with an assistant, several lines of it.",
            application="WindowsTerminal.exe",
            title="\u2733 Upload instruction wc \u547d\u4ee4\u62a5\u9519"))
        provider._look()
        provider.flush_what_is_gathered()
        self.assertEqual(len(self.seen), 1)

    def test_a_window_on_the_never_read_list_is_not_read(self) -> None:
        """The list that matters more than any other here.

        A filename recorded by mistake is a filename. A window recorded by
        mistake is everything that was in it, and a password manager shows
        credentials in plain text by design.
        """
        provider = self._provider(_Showing("Master password: hunter2",
                                           application="1Password.exe",
                                           title="1Password"))
        provider._look()
        self.assertEqual(self.seen, [])
        self.assertEqual(provider.get_status()["skipped"], 1)

    def test_the_list_matches_the_title_as_well_as_the_application(self) -> None:
        # A bank is a page in a browser, not an application of its own.
        provider = self._provider(_Showing("Balance: a number",
                                           application="firefox.exe",
                                           title="Sign in - Harbour Savings Bank"))
        provider._look()
        self.assertEqual(self.seen, [])

    def test_what_is_on_that_list_to_begin_with(self) -> None:
        from personalized_agent.common.config.schema import DEFAULT_UNREADABLE_WINDOWS

        for expected in ("1password", "bitwarden", "private browsing",
                         "incognito", "authenticator"):
            self.assertIn(expected, DEFAULT_UNREADABLE_WINDOWS)

    def test_a_long_page_is_cut_to_what_is_worth_keeping(self) -> None:
        self.config.set("listener_screen_max_chars", 500)
        provider = self._provider(_Showing("\n".join(
            f"line number {index} with enough on it to count" for index in range(400))))
        provider._look()
        provider.flush_what_is_gathered()
        [event] = self.seen
        self.assertLessEqual(len(event.data["text"]), 500)
        self.assertIn("line number 399", event.data["text"],
                      "the end is kept: it is where the reader has got to")

    def test_single_words_are_not_what_somebody_was_reading(self) -> None:
        # A window full of button labels says nothing about the work.
        provider = self._provider(_Showing("OK\nNo\na\nb\nGo"))
        provider._look()
        self.assertEqual(self.seen, [])

    def test_coming_back_hours_later_records_only_what_is_new(self) -> None:
        """Coming back to a page used to write down everything still on it
        a second time - fourteen per cent of all that was read, and half of
        a WeChat chat, every message still on screen recorded again each
        time the chat was reopened. That somebody came back is in the focus
        events; what the page says is kept once, and only what has been
        added to it since is kept again."""
        from personalized_agent.listener.providers.screen import ScreenProvider

        platform = _Showing("A page somebody read this morning.")
        provider = self._provider(platform)
        provider._look()
        provider._look()
        provider.flush_what_is_gathered()
        self.assertEqual(len(self.seen), 1)

        def hours_later() -> None:
            when, lines = provider._seen[platform.reading.handle]
            provider._seen[platform.reading.handle] = (
                when - ScreenProvider.SAME_SITTING_SECONDS - 60, lines)

        # Back to the same page, nothing on it changed: nothing new to keep.
        hours_later()
        provider._look()
        provider.flush_what_is_gathered()
        self.assertEqual(len(self.seen), 1,
                         "the same text was recorded a second time")
        # Back again, and something was added: only that is kept.
        hours_later()
        platform.reading.text = ("A page somebody read this morning.\n"
                                 "A reply that arrived this afternoon.")
        provider._look()
        provider.flush_what_is_gathered()
        self.assertEqual(len(self.seen), 2)
        kept = self.seen[-1].data["text"]
        self.assertIn("A reply that arrived this afternoon.", kept)
        self.assertNotIn("read this morning", kept)

    def test_a_platform_that_cannot_read_a_window_says_so(self) -> None:
        from personalized_agent.listener.providers.screen import ScreenProvider

        class Blind:
            name = "test"

            class activity:
                @staticmethod
                def seconds_since_input():
                    return 1.0

            @staticmethod
            def capabilities():
                from personalized_agent.platform_layer.base import PlatformCapabilities

                return PlatformCapabilities(window_text=False)

        provider = ScreenProvider(self.config, Blind)
        with self.assertRaises(RuntimeError):
            provider.start()

    def test_the_category_can_be_switched_off_like_any_other(self) -> None:
        from personalized_agent.listener.core.privacy import CATEGORY_SETTINGS

        self.assertEqual(CATEGORY_SETTINGS["screen"], "listener_screen_enabled")

    def test_it_is_registered_with_the_listener(self) -> None:
        # Constructed like every other provider, and registered whether or
        # not this machine can actually read a window: a provider that says
        # it cannot is more use than one that is not there.
        listener = self.agent.app.listener
        listener.discover()
        self.assertIn("screen", listener.provider_ids())

    def test_looking_much_more_often_than_a_second_is_refused(self) -> None:
        # Reading a busy page costs about a third of a second, and nothing
        # downstream reads any of this as it happens.
        self.config.set("listener_screen_interval_seconds", 0.1)
        provider = self._provider(_Showing("x"))
        self.assertGreaterEqual(provider._interval(), 2.0)


class ReadingAWindowsAccessibilityTreeTests(unittest.TestCase):
    """Section 19.2: the parts of the reading that are not COM.

    The client itself needs a desktop and a running application to talk to,
    and is exercised by hand. What can be tested is what it does with what it
    gets back, and those are the parts that were wrong.
    """

    def _reader(self):
        from personalized_agent.platform_layer.window_text import WindowTextReader

        return WindowTextReader()

    def test_an_image_placeholder_is_not_text(self) -> None:
        # A busy page returns these by the hundred; one measurement came back
        # as 64,000 characters of which a great many were this.
        tidied = self._reader()._tidied(["\ufffc\ufffc Real words here \ufffc"])
        self.assertEqual(tidied, "Real words here")

    def test_the_same_line_twice_is_one_line(self) -> None:
        # A list control commonly reports one label once per column.
        tidied = self._reader()._tidied(["Name", "Name", "Date modified"])
        self.assertEqual(tidied.splitlines(), ["Name", "Date modified"])

    def test_descendants_is_four(self) -> None:
        """The number that made the whole approach look impossible.

        Passing 2 - which is Children - returns a handful of shell elements
        and no content, from every application, with no error. Measured
        before: a text editor with a file open exposed 5 elements and 26
        characters. After: 61 elements, 951 characters, and the file's text.
        """
        from personalized_agent.platform_layer import window_text

        self.assertEqual(window_text._DESCENDANTS, 4)

    def test_a_hidden_element_is_left_where_it_is(self) -> None:
        """A browser keeps every open tab in the tree.

        Measured on one machine: asking a browser window for its documents
        returned twelve tabs and 64,371 characters, of which the user was
        looking at 1,617. One of the other eleven was a mail inbox.
        """
        reader = self._reader()

        class Hidden:
            @staticmethod
            def GetCurrentPropertyValue(which):
                return True

        class Showing:
            @staticmethod
            def GetCurrentPropertyValue(which):
                return False

        self.assertTrue(reader._is_hidden(Hidden))
        self.assertFalse(reader._is_hidden(Showing))

    def test_a_password_field_is_never_read(self) -> None:
        reader = self._reader()

        class Secret:
            @staticmethod
            def GetCurrentPropertyValue(which):
                from personalized_agent.platform_layer import window_text

                return which == window_text._IS_PASSWORD_PROPERTY

        self.assertTrue(reader._is_a_password(Secret))

    def test_an_element_that_refuses_to_answer_is_not_assumed_secret(self) -> None:
        # Nor assumed safe: refusing to answer about a password means the
        # element is read, so the question is asked of the platform and the
        # failure mode is the ordinary one.
        reader = self._reader()

        class Silent:
            @staticmethod
            def GetCurrentPropertyValue(which):
                raise OSError("no")

        self.assertFalse(reader._is_a_password(Silent))
        self.assertFalse(reader._is_hidden(Silent))

    def test_a_window_of_thousands_is_read_at_a_bounded_cost(self) -> None:
        """The user, 2026-10-30: Bitvise's file browser, a list of
        thousands, stood still for half a minute at a time while it was
        read - every element was visited, however few were read. Now at
        most WALK_AT_MOST are, each asked once, and the names are read."""
        from personalized_agent.platform_layer import window_text as wt

        tree = _Tree(5000)
        reader = self._reader()
        reader._automation = tree
        text, how, visited = reader._ask_bounded(tree.root)
        self.assertEqual(how, "walk")
        self.assertLessEqual(visited, reader.WALK_AT_MOST)
        self.assertIn("file 0001.txt", text)
        # Each element visited cost at most its two walker calls.
        self.assertLessEqual(tree.calls, 2 * reader.WALK_AT_MOST + 2)
        self.assertEqual(tree.found_all, 0, "nothing asked of every descendant")
        # An application slow to answer is asked no more after READ_SECONDS.
        slow = _Tree(5000, each_call=0.01)
        reader = self._reader()
        reader._automation = slow
        began = time.monotonic()
        reader._ask_bounded(slow.root)
        self.assertLess(time.monotonic() - began, reader.READ_SECONDS + 0.5)
        self.assertLess(slow.calls, 2 * reader.WALK_AT_MOST)
        self.assertEqual(wt._HAS_TEXT_PATTERN, 30040)

    def test_a_part_not_on_the_screen_is_not_gone_into(self) -> None:
        tree = _Tree(3, hidden_tab=200)
        reader = self._reader()
        reader._automation = tree
        text, _how, visited = reader._ask_bounded(tree.root)
        self.assertNotIn("behind", text)
        self.assertLess(visited, 10)

    def test_only_a_window_holding_a_long_list_is_read_within_bounds(self) -> None:
        """Every other window is asked as it always was: the bounded walk,
        tried on all, found a fraction of a page, Explorer or WeChat."""
        from personalized_agent.platform_layer import window_text as wt
        reader = self._reader()
        reader._started = True
        reader._ready.set()
        reader._automation = SimpleNamespace(ElementFromHandle=lambda handle: "window")
        asked = []
        reader._ask_bounded = lambda element: asked.append("bounded") or ("", "walk", 1)
        reader._ask_the_window = lambda element: asked.append("whole") or ("", "walk", 1)
        with mock.patch.object(wt.sys, "platform", "win32"):
            with mock.patch.object(wt, "has_a_long_list", return_value=True):
                reader.read(7)
            with mock.patch.object(wt, "has_a_long_list", return_value=False):
                reader.read(7)
        self.assertEqual(asked, ["bounded", "whole"])

    @unittest.skipUnless(sys.platform == "win32", "Win32 lists: Windows")
    def test_a_long_win32_list_is_counted_with_one_message(self) -> None:
        import ctypes
        from personalized_agent.platform_layer import window_text as wt
        user32 = ctypes.windll.user32
        user32.CreateWindowExW.restype = ctypes.c_void_p
        user32.CreateWindowExW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                           ctypes.c_uint, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t,
                                        ctypes.c_wchar_p]
        user32.DestroyWindow.argtypes = [ctypes.c_void_p]
        # Shown, but far off the screen: a list in a hidden window is not counted.
        parent = user32.CreateWindowExW(0x80, "STATIC", "pa-test", 0x90000000, -32000, -32000,
                                        200, 200,
                                        None, None, None, None)
        self.addCleanup(user32.DestroyWindow, parent)
        listed = user32.CreateWindowExW(0, "LISTBOX", "", 0x50000000, 0, 0, 100, 100,
                                        parent, None, None, None)
        for index in range(wt.LONG_LIST):
            user32.SendMessageW(listed, 0x0180, 0, f"file {index}")    # LB_ADDSTRING
        self.assertFalse(wt.has_a_long_list(parent))
        user32.SendMessageW(listed, 0x0180, 0, "one more")
        self.assertTrue(wt.has_a_long_list(parent))

    def test_nothing_is_read_on_a_platform_without_it(self) -> None:
        from personalized_agent.platform_layer.base import PlatformActivityProvider

        self.assertIsNone(PlatformActivityProvider.text_in_front(None))

    def test_and_it_is_reported_as_a_capability(self) -> None:
        from personalized_agent.platform_layer.base import PlatformCapabilities

        self.assertIn("window_text", PlatformCapabilities().to_dict())
        self.assertFalse(PlatformCapabilities().window_text)


class OneSetOfRulesAskedInThreePlacesTests(unittest.TestCase):
    """Section 19, as amended: a rule added on Tuesday and Monday's records.

    Excluding a file when it arrives does nothing about the thirty days
    already written down, and the user meets those in the Overview. Measured
    on one machine the day after the exclusions went in: of 24,610 file
    events in the last day, **10,653 were one registry hive log**, all
    recorded in the ten minutes before the rule existed - and all of them
    shown under Firefox, because Firefox was in front while Windows flushed
    its registry.

    So the same question is asked in three places, by one function: before
    recording, before showing, and before deleting.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.config = self.agent.app.config

    def tearDown(self) -> None:
        self.agent.cleanup()

    # -- the rules themselves ---------------------------------------------
    def test_a_name_that_churns_wherever_it_sits(self) -> None:
        from personalized_agent.common.file_noise import churns_regardless

        # A directory exclusion cannot reach this one: it is in the home
        # directory itself, beside everything worth watching.
        self.assertTrue(churns_regardless(
            r"C:/Users/me/ntuser.dat.LOG2", patterns=["*.log2"]))
        self.assertFalse(churns_regardless(
            r"C:/Users/me/notes.md", patterns=["*.log2"]))

    def test_a_directory_name_anywhere_along_the_way(self) -> None:
        from personalized_agent.common.file_noise import churns_regardless

        self.assertTrue(churns_regardless(
            r"C:/work/project/node_modules/x/y.js", names=["node_modules"]))
        self.assertFalse(churns_regardless(
            r"C:/work/project/src/y.js", names=["node_modules"]))

    def test_and_a_whole_tree_by_where_it_is(self) -> None:
        from personalized_agent.common.file_noise import churns_regardless

        tree = os.path.abspath("C:/agent/workspace/data")
        self.assertTrue(churns_regardless(
            os.path.join(tree, "others", "a.md"), folders=[tree]))
        self.assertFalse(churns_regardless(
            os.path.abspath("C:/agent/workspace/notes.md"), folders=[tree]))

    def test_nothing_matches_nothing(self) -> None:
        from personalized_agent.common.file_noise import churns_regardless

        self.assertFalse(churns_regardless(None, patterns=["*.log2"]))
        self.assertFalse(churns_regardless("C:/a/b.txt"))

    def test_the_three_lists_come_from_the_settings(self) -> None:
        from personalized_agent.common.file_noise import rules_from

        rules = rules_from(self.config)
        self.assertEqual(set(rules), {"patterns", "names", "folders"})
        self.assertIn("*.log2", rules["patterns"])

    # -- the filter still uses them ---------------------------------------
    def test_the_listener_still_refuses_what_it_always_did(self) -> None:
        from personalized_agent.listener.core.privacy import PrivacyFilter

        privacy = PrivacyFilter(self.config)
        self.assertFalse(privacy.file_name_allowed(r"C:/Users/me/ntuser.dat.LOG2"))
        self.assertTrue(privacy.file_name_allowed(r"C:/Users/me/report.md"))
        self.assertFalse(privacy.path_allowed(r"C:/work/node_modules/x.js"))

    # -- the prefilter -----------------------------------------------------
    def test_the_database_narrows_before_python_decides(self) -> None:
        """Wider than the rule, never narrower.

        SQLite will not do fnmatch on a basename, and reading every stored
        path into Python to ask is affordable once and wasteful every time.
        """
        from personalized_agent.common.file_noise import sql_prefilter

        sql, params = sql_prefilter(patterns=["*.log2"], names=["node_modules"])
        self.assertIn("LIKE", sql)
        self.assertIn("%.log2", params)
        self.assertIn("%node_modules%", params)

    def test_a_tree_is_matched_by_where_it_starts(self) -> None:
        """Left out once, and the sweep then found a third of what it should.

        14,763 rows examined out of the 50,784 its own rules would catch:
        everything excluded by its folder rather than by its name was
        invisible to the query and so never asked about.
        """
        from personalized_agent.common.file_noise import sql_prefilter

        _sql, params = sql_prefilter(folders=["C:/agent/workspace/data"])
        self.assertEqual(params, ["C:/agent/workspace/data%"])

    def test_no_rules_means_no_condition_rather_than_everything(self) -> None:
        from personalized_agent.common.file_noise import sql_prefilter

        self.assertEqual(sql_prefilter(), ("", []))


class SweepingUpWhatWasNeverActivityTests(unittest.TestCase):
    """Section 19, as amended: deleting what should not have been recorded.

    Different from pruning material that has already become a record, which
    is a question of storage. These rows were never anybody's activity:
    keeping them costs tokens to summarise and puts noise in front of the
    user. Measured on one machine, 50,784 of 54,309 stored file events -
    **93%** - were caught by rules that already existed.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.store = self.agent.app.raw_store
        self.config = self.agent.app.config

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _file(self, path, when=None):
        self.store.add_event(ListenerEvent(
            event_type="filesystem.modified", source="filesystem",
            data={"path": path}))

    def _count(self):
        return len(self.store.recent_events(limit=500, event_type="filesystem."))

    def test_a_hive_log_is_swept_and_the_users_file_is_not(self) -> None:
        self._file(r"C:/Users/me/ntuser.dat.LOG2")
        self._file(r"C:/Users/me/Desktop/report.md")
        self.assertEqual(self._count(), 2)
        swept = self.store.prune_noise(patterns=["*.log2"])
        self.assertEqual(swept["removed"], 1)
        left = self.store.recent_events(limit=50, event_type="filesystem.")
        self.assertEqual([e.data["path"] for e in left],
                         [r"C:/Users/me/Desktop/report.md"])

    def test_a_dry_run_says_what_it_would_do_and_does_nothing(self) -> None:
        self._file(r"C:/Users/me/ntuser.dat.LOG2")
        swept = self.store.prune_noise(patterns=["*.log2"], dry_run=True)
        self.assertEqual(swept["removed"], 1)
        self.assertEqual(self._count(), 1, "still there")

    def test_the_agents_own_folder_goes_too(self) -> None:
        tree = os.path.abspath("C:/agent/workspace/data")
        self._file(os.path.join(tree, "others", "a.md"))
        self._file(r"C:/Users/me/Desktop/report.md")
        swept = self.store.prune_noise(folders=[tree])
        self.assertEqual(swept["removed"], 1)

    def test_with_no_rules_it_deletes_nothing(self) -> None:
        # A missing settings file must not be read as "everything is noise".
        self._file(r"C:/Users/me/ntuser.dat.LOG2")
        self.assertEqual(self.store.prune_noise()["removed"], 0)
        self.assertEqual(self._count(), 1)

    def test_it_leaves_everything_that_is_not_a_file_event_alone(self) -> None:
        self.store.add_event(ListenerEvent(
            event_type="browser.visit", source="browser",
            data={"url": "https://example.com/ntuser.dat.LOG2"}))
        self.store.prune_noise(patterns=["*.log2"])
        self.assertEqual(
            len(self.store.recent_events(limit=50, event_type="browser.")), 1)

    def test_one_pass_is_bounded(self) -> None:
        """A year of history is cleared over several rounds, not one long one.

        On the machine these numbers come from it took four: 19,876 rows,
        then 17,622, then 13,286, then nothing left to find.
        """
        from personalized_agent.data_manage.storage.raw_store import RawStore

        self.assertLessEqual(RawStore.NOISE_AT_MOST, 50000)
        self.assertGreaterEqual(RawStore.NOISE_AT_MOST, 1000)


class ReadingIsGatheredIntoSittingsTests(unittest.TestCase):
    """Section 19.2, as amended: reading is continuous, events are not.

    Every other provider records something that happened once - a file
    changed, a window came to the front - and one event is the natural unit.
    Reading is not like that. Sampled every few seconds it turns one person
    reading one page into a hundred events, each a few lines, **each needing
    a model** to say what it was: window contents cannot be templated the way
    a path or a duration can, because the content is the whole of what they
    are worth.

    Measured on one machine: 133 readings over eleven minutes produced 98
    events, about 535 an hour, against a data heartbeat that reads eighty
    model-needing observations a round. Gathered into sittings, the same
    reading is a handful of events containing the same words.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.config = self.agent.app.config
        # The provider now checks the master switch before native reads.
        self.config.set("listener_enabled", True)
        self.config.set("listener_screen_enabled", True)
        self.seen = []

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _provider(self, platform):
        from personalized_agent.listener.providers.screen import ScreenProvider

        provider = ScreenProvider(self.config, platform)
        provider.bind(self.seen.append)
        return provider

    def test_a_page_read_in_pieces_is_one_record(self) -> None:
        platform = _Showing("The opening lines of something.")
        provider = self._provider(platform)
        for index in range(6):
            platform.reading.text = f"Line number {index}, with words on it."
            provider._look()
        self.assertEqual(self.seen, [], "still being gathered")
        provider.flush_what_is_gathered()
        [event] = self.seen
        for index in range(6):
            self.assertIn(f"Line number {index}", event.data["text"])
        self.assertEqual(event.data["readings"], 6)

    def test_enough_gathered_is_reported_without_being_asked(self) -> None:
        from personalized_agent.listener.providers.screen import ScreenProvider

        platform = _Showing("")
        provider = self._provider(platform)
        for index in range(40):
            platform.reading.text = f"Paragraph {index}. " + ("word " * 40)
            provider._look()
        self.assertTrue(self.seen, "a long read is not held for ever")
        gathered = sum(len(e.data["text"]) for e in self.seen)
        self.assertGreaterEqual(gathered, ScreenProvider.ENOUGH_TO_REPORT)

    def test_switching_windows_ends_the_sitting(self) -> None:
        """The clearest signal that a stretch of reading is over.

        And it keeps the record one stretch per window rather than two
        interleaved.
        """
        platform = _Showing("What was on the first page.")
        provider = self._provider(platform)
        provider._look()
        platform.reading.handle = 777
        platform.reading.application = "sublime_text.exe"
        platform.reading.title = "notes.md"
        platform.reading.text = "What was in the editor."
        provider._look()
        self.assertEqual(len(self.seen), 1, "the first window was reported")
        self.assertIn("first page", self.seen[0].data["text"])
        self.assertEqual(self.seen[0].data["application"], "firefox.exe")
        provider.flush_what_is_gathered()
        self.assertIn("in the editor", self.seen[1].data["text"])
        self.assertEqual(self.seen[1].data["application"], "sublime_text.exe")

    def test_a_slow_reader_is_reported_anyway(self) -> None:
        """Held too long is a reason to report, not to keep waiting.

        Otherwise somebody reading slowly, or watching something, has their
        whole afternoon arrive in one lump when they finally switch windows.
        """
        from personalized_agent.listener.providers.screen import ScreenProvider

        platform = _Showing("A few words.")
        provider = self._provider(platform)
        provider._look()
        self.assertEqual(self.seen, [])
        # Wind the sitting back past the holding time.
        provider._gathering["since"] -= ScreenProvider.HOLD_AT_MOST_SECONDS + 1
        platform.reading.text = "A few more words."
        provider._look()
        self.assertEqual(len(self.seen), 1)
        self.assertIn("A few words", self.seen[0].data["text"])

    def test_a_quiet_window_still_reports_what_it_gathered(self) -> None:
        # Nothing new for a while does not mean nothing was read: what was
        # gathered before it went quiet still has to come out.
        from personalized_agent.listener.providers.screen import ScreenProvider

        platform = _Showing("Something worth keeping.")
        provider = self._provider(platform)
        provider._look()
        provider._gathering["since"] -= ScreenProvider.HOLD_AT_MOST_SECONDS + 1
        provider._look()             # unchanged, but the hold has expired
        self.assertEqual(len(self.seen), 1)

    def test_the_newest_title_is_the_one_kept(self) -> None:
        # A browser tab that has been navigated is the same sitting, and
        # where it ended up is the useful half of where it was.
        platform = _Showing("First page.", title="An article")
        provider = self._provider(platform)
        provider._look()
        platform.reading.title = "A different article"
        platform.reading.text = "Second page."
        provider._look()
        provider.flush_what_is_gathered()
        self.assertEqual(self.seen[0].data["window_title"], "A different article")

    def test_closing_the_agent_does_not_lose_the_sitting(self) -> None:
        platform = _Showing("What they were reading when it closed.")
        provider = self._provider(platform)
        provider._look()
        provider.stop()
        self.assertEqual(len(self.seen), 1)
        self.assertIn("when it closed", self.seen[0].data["text"])

    def test_a_record_says_how_long_the_reading_took(self) -> None:
        platform = _Showing("Some words to read.")
        provider = self._provider(platform)
        provider._look()
        provider.flush_what_is_gathered()
        [event] = self.seen
        self.assertIn("over_seconds", event.data)
        self.assertIn("readings", event.data)

    def test_gathering_nothing_reports_nothing(self) -> None:
        provider = self._provider(_Showing(""))
        provider._look()
        provider.flush_what_is_gathered()
        self.assertEqual(self.seen, [])


    def test_looking_every_fifteen_seconds_rather_than_five(self) -> None:
        from personalized_agent.common.config.schema import SCHEMA_BY_KEY

        self.assertEqual(
            SCHEMA_BY_KEY["listener_screen_interval_seconds"].default, 15.0)


class AFolderNamedAfterAnApplicationTests(unittest.TestCase):
    """Section 19, as amended: the title bar always says the application.

    "A folder above the file appears in the window title" is the weak half
    of the evidence, and it has a failure that is not rare: a folder named
    after an application, seen while that application is in front. Every
    window carries its application's name, so the match is guaranteed and
    means nothing.

    Found in the record rather than by reading the code:
    ``extensions/browser/firefox/manifest.json`` was recorded as the user's
    reading three times, with the title "Mozilla Firefox". Firefox was
    reading it - as a program checking its installed extensions, which is
    the exact thing this test exists to exclude.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        from personalized_agent.listener.core.privacy import PrivacyFilter

        self.privacy = PrivacyFilter(self.agent.app.config)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_case_that_was_found_in_the_record(self) -> None:
        self.assertFalse(self.privacy.on_screen(
            r"C:/p/extensions/browser/firefox/manifest.json",
            "Personalized Agent \u2014 Mozilla Firefox", "firefox.exe"))

    def test_and_with_nothing_but_the_application_in_the_title(self) -> None:
        self.assertFalse(self.privacy.on_screen(
            r"C:/p/extensions/browser/firefox/manifest.json",
            "Mozilla Firefox", "firefox.exe"))

    def test_but_a_file_actually_called_that_still_counts(self) -> None:
        """The filename is the strong half and keeps its own evidence.

        A file called firefox.html, open in Firefox, belongs in the record:
        its name appearing in full is not a coincidence of folder naming.
        """
        self.assertTrue(self.privacy.on_screen(
            r"C:/notes/firefox.html", "firefox.html \u2014 Mozilla Firefox",
            "firefox.exe"))

    def test_a_folder_the_application_is_not_named_after_still_counts(self) -> None:
        # The case the folder rule exists for, unaffected.
        self.assertTrue(self.privacy.on_screen(
            r"C:/work/personalized_agent_release/build.py",
            "build.py - personalized_agent_release", "sublime_text.exe"))
        self.assertTrue(self.privacy.on_screen(
            r"C:/work/personalized_agent_release/docs/x.md",
            "personalized_agent_release - File Explorer", "explorer.exe"))

    def test_an_application_too_short_to_strip_is_left_alone(self) -> None:
        """Removing one or two letters from a title would cut it to pieces.

        A two-letter name occurs inside ordinary words, so stripping it
        would take "golang" out of a title along with everything else
        containing "go" - and the folder rule would then fail on a folder
        that has nothing to do with the application.
        """
        self.assertTrue(self.privacy.on_screen(
            r"C:/work/golang/notes.md", "golang - notes", "go"))

    def test_not_knowing_the_application_changes_nothing_else(self) -> None:
        self.assertTrue(self.privacy.on_screen(
            r"C:/work/personalized_agent_release/build.py",
            "build.py - personalized_agent_release"))

    def test_the_filter_passes_the_application_through(self) -> None:
        """The fix is worth nothing if the caller does not hand it over."""
        from personalized_agent.common.events.models import ListenerEvent
        import datetime as _dt

        event = ListenerEvent(
            event_type="filesystem.opened", source="filesystem",
            timestamp=_dt.datetime.now(_dt.timezone.utc),
            data={"path": r"C:/p/extensions/browser/firefox/manifest.json",
                  "while_using": "firefox.exe",
                  "while_showing": "Personalized Agent \u2014 Mozilla Firefox"})
        self.agent.app.config.set("listener_enabled", True)
        self.agent.app.config.set("listener_filesystem_enabled", True)

        class Here:
            class activity:
                @staticmethod
                def seconds_since_input():
                    return 2.0

        from personalized_agent.listener.core.privacy import PrivacyFilter

        privacy = PrivacyFilter(self.agent.app.config, platform=Here)
        self.assertFalse(privacy.allows(event, "filesystem"))


class TheDesktopSaysSoAtOnceTests(unittest.TestCase):
    """Section 19, as amended: the only prompt account of a file being opened.

    The filesystem's account of a read arrives when Windows flushes the
    access time, which is up to an hour later. The desktop's account is
    immediate - Explorer writes its shortcut as the file opens.

    Measured on one machine: a file opened at 17:10:35 was in the Recent
    folder at 17:10:35, its access time said 17:11:17, and the filesystem
    had still reported nothing three minutes later. The same record already
    read as *evidence* about who opened a file is therefore read as a
    *source* as well.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.root = Path(tempfile.mkdtemp(prefix="pa-desk-"))
        self.seen = []
        self.now = time.time()
        self.opened = {}

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        self.agent.cleanup()

    def _provider(self):
        from personalized_agent.listener.providers.filesystem import FilesystemProvider

        outer = self

        class Desktop:
            name = "test"

            class activity:
                @staticmethod
                def files_the_user_opened(since):
                    return dict(outer.opened)

        provider = FilesystemProvider(self.agent.app.config, Desktop)
        provider.bind(self.seen.append)
        return provider

    def _a_file(self, name="report.md"):
        path = self.root / name
        path.write_text("something", encoding="utf-8")
        return str(path)

    def test_a_file_the_desktop_says_was_opened_is_reported(self) -> None:
        path = self._a_file()
        self.opened = {path: self.now - 30}
        provider = self._provider()
        provider._ask_the_desktop()
        provider.flush_held_events()
        [event] = self.seen
        self.assertEqual(event.event_type, "filesystem.opened")
        self.assertEqual(event.data["path"], path)
        self.assertEqual(event.data["said_by"], "the desktop")

    def test_it_is_dated_when_the_desktop_says_rather_than_now(self) -> None:
        path = self._a_file()
        self.opened = {path: self.now - 600}
        provider = self._provider()
        provider._ask_the_desktop()
        provider.flush_held_events()
        [event] = self.seen
        late = (dt.datetime.now(dt.timezone.utc) - event.timestamp).total_seconds()
        self.assertGreater(late, 300)

    def test_the_same_opening_is_not_reported_twice(self) -> None:
        path = self._a_file()
        self.opened = {path: self.now - 30}
        provider = self._provider()
        for _ in range(4):
            provider._ask_the_desktop()
        provider.flush_held_events()
        self.assertEqual(len(self.seen), 1)

    def test_nor_when_the_filesystem_gets_round_to_it(self) -> None:
        """One opening is one event however many ways we hear about it.

        The two timestamps for a single opening are close but not equal -
        measured, 17:10:35 from the desktop and 17:11:17 from the access
        time - so they are matched by nearness rather than by equality.
        """
        from personalized_agent.listener.providers.filesystem import FilesystemProvider

        path = self._a_file()
        self.opened = {path: self.now - 60}
        provider = self._provider()
        provider._ask_the_desktop()
        provider.flush_held_events()
        self.assertEqual(len(self.seen), 1)

        class Raw:
            src_path = path
            event_type = "modified"
            is_directory = False

        # Make it look like a read: written long ago, accessed just now.
        os.utime(path, (self.now - 40, self.now - 86400))
        provider.handle_raw(Raw)
        provider.flush_held_events()
        self.assertEqual(len(self.seen), 1, "the desktop already said so")

    def test_but_opening_it_again_much_later_is(self) -> None:
        from personalized_agent.listener.providers.filesystem import FilesystemProvider

        path = self._a_file()
        provider = self._provider()
        self.opened = {path: self.now - 7200}
        provider._ask_the_desktop()
        self.opened = {path: self.now - 30}
        provider._ask_the_desktop()
        provider.flush_held_events()
        self.assertEqual(len(self.seen), 2)
        self.assertLessEqual(FilesystemProvider.SAME_OPENING_SECONDS, 900)

    def test_a_bare_filename_is_recognised_but_not_reported(self) -> None:
        """Enough to know somebody else's event, not enough to make one.

        Windows names its shortcut after the file and cannot always give up
        the path inside it. A name with no directory is not something a
        record can point at.
        """
        self.opened = {"report.md": self.now - 30}
        provider = self._provider()
        provider._ask_the_desktop()
        provider.flush_held_events()
        self.assertEqual(self.seen, [])

    def test_a_file_that_is_gone_is_not_reported(self) -> None:
        self.opened = {str(self.root / "deleted.md"): self.now - 30}
        provider = self._provider()
        provider._ask_the_desktop()
        provider.flush_held_events()
        self.assertEqual(self.seen, [])

    def test_a_platform_with_no_such_record_is_not_an_error(self) -> None:
        from personalized_agent.listener.providers.filesystem import FilesystemProvider

        class Bare:
            name = "test"

            class activity:
                pass

        provider = FilesystemProvider(self.agent.app.config, Bare)
        provider.bind(self.seen.append)
        provider._ask_the_desktop()
        self.assertEqual(self.seen, [])

    def test_the_path_keeps_the_spelling_the_filesystem_uses(self) -> None:
        # Normalising case is for comparing. Doing it at the source once put
        # "readme.md" in front of somebody who had opened "README.md".
        path = self._a_file("README.md")
        self.opened = {path: self.now - 30}
        provider = self._provider()
        provider._ask_the_desktop()
        provider.flush_held_events()
        self.assertTrue(self.seen[0].data["path"].endswith("README.md"))

    def test_it_is_read_often_enough_to_be_worth_calling_prompt(self) -> None:
        from personalized_agent.listener.providers.filesystem import FilesystemProvider

        self.assertLessEqual(FilesystemProvider.WHAT_THEY_OPENED_EVERY, 30.0)
        self.assertGreater(FilesystemProvider.WHAT_THEY_OPENED_SINCE,
                           FilesystemProvider.WHAT_THEY_OPENED_EVERY,
                           "the window has to overlap the interval")


class ATitleThatIsItselfAPathTests(unittest.TestCase):
    """Section 19, as amended: the folder rule vouching for a whole tree.

    Some editors put the whole path in the title bar. That is the most
    precise thing a window can say about what it is showing - and the folder
    rule exists for the opposite case, a title that names only the project
    or only the command and leaves the file unsaid.

    Applied to a title that is already a path, the folder rule stops
    narrowing anything and starts vouching for everything. Found in the
    record: with Sublime showing
    ``C:\\...\\personalized_agent_release\\HANDOFF.md``, a source file an
    assistant was editing and a file in an unrelated folder were both
    recorded as the user's reading, because the project's name appeared in
    the title of a different file.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        from personalized_agent.listener.core.privacy import PrivacyFilter

        self.privacy = PrivacyFilter(self.agent.app.config)
        self.sublime = (r"C:\Users\me\Desktop\personalized_agent_release"
                        r"\HANDOFF.md - Sublime Text")

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_another_file_in_the_same_project_is_not_vouched_for(self) -> None:
        self.assertFalse(self.privacy.on_screen(
            r"C:/Users/me/Desktop/personalized_agent_release/runtime/guard.py",
            self.sublime, "sublime_text.exe"))

    def test_but_the_file_the_title_names_is(self) -> None:
        self.assertTrue(self.privacy.on_screen(
            r"C:/Users/me/Desktop/personalized_agent_release/HANDOFF.md",
            self.sublime, "sublime_text.exe"))

    def test_a_command_line_in_a_title_names_its_file_and_no_other(self) -> None:
        title = r"C:\WINDOWS\system32\cmd.exe - python  run.py"
        self.assertTrue(self.privacy.on_screen(
            r"C:/work/project/run.py", title, "WindowsTerminal.exe"))
        self.assertFalse(self.privacy.on_screen(
            r"C:/work/project/store.py", title, "WindowsTerminal.exe"))

    def test_a_title_that_names_a_folder_still_uses_the_folder_rule(self) -> None:
        """The case the folder rule is for, and it is untouched.

        A file manager showing a folder, or a terminal whose title is the
        project, says nothing about which file - so the folders are all
        there is to go on.
        """
        self.assertTrue(self.privacy.on_screen(
            r"C:/work/personalized_agent_release/docs/x.md",
            "personalized_agent_release - File Explorer", "explorer.exe"))
        self.assertTrue(self.privacy.on_screen(
            r"C:/work/personalized_agent_release/build.py",
            "build.py - personalized_agent_release", "sublime_text.exe"))

    def test_a_title_with_a_slash_but_no_drive_is_not_a_path(self) -> None:
        # "Inbox - me/you - Chat" is a title, not a path. Requiring both a
        # separator and a colon keeps prose out of it.
        self.assertTrue(self.privacy.on_screen(
            r"C:/work/personalized_agent_release/notes.md",
            "personalized_agent_release / notes", "sublime_text.exe"))


class FiveWaysATitleSaidYesWronglyTests(unittest.TestCase):
    """Section 19, as amended: the whole list, in one place.

    The folder rule - "a folder above the file appears in the window title" -
    is the weak half of the evidence, and every one of its failures was found
    in a real record rather than by reading the code. Gathered here because
    the shape repeats: a string that is guaranteed to be in the title tells
    you nothing, and each fix revealed the next guaranteed string.
    """

    def _about(self, path, title, application=None):
        from personalized_agent.common.titles import about_this_file

        return about_this_file(path, title, application)

    def test_one_a_folder_named_after_the_application(self) -> None:
        # extensions/browser/firefox/manifest.json, recorded three times,
        # because the title said "Mozilla Firefox".
        self.assertFalse(self._about(
            r"C:/p/extensions/browser/firefox/manifest.json",
            "Personalized Agent \u2014 Mozilla Firefox", "firefox.exe"))

    def test_two_the_file_itself_named_after_the_application(self) -> None:
        """The same clash coming through the *stronger* test.

        Fixing the folder rule alone left this: the directory
        ``.../browser/firefox`` has `firefox` as its own name, so it matched
        as a filename and never reached the folder rule at all.
        """
        self.assertFalse(self._about(
            r"C:/p/extensions/browser/firefox", "Mozilla Firefox",
            "firefox.exe"))

    def test_three_a_title_that_is_itself_a_path(self) -> None:
        # An editor showing one file vouched for every file in the project.
        title = (r"C:\Users\me\personalized_agent_release\HANDOFF.md"
                 " - Sublime Text")
        self.assertFalse(self._about(
            r"C:/Users/me/personalized_agent_release/runtime/guard.py",
            title, "sublime_text.exe"))
        self.assertTrue(self._about(
            r"C:/Users/me/personalized_agent_release/HANDOFF.md",
            title, "sublime_text.exe"))

    def test_four_a_folder_whose_name_names_nothing(self) -> None:
        """`agent`, five letters, inside "Personalized Agent".

        Long enough to pass the length test and common enough to appear in
        any title. A source file an assistant was reading was recorded as
        the user's because its path contained a folder called `agent`.
        """
        self.assertFalse(self._about(
            r"C:/p/personalized_agent/agent/overview/service.py",
            "Personalized Agent \u2014 Mozilla Firefox", "firefox.exe"))

    def test_five_a_name_too_short_to_mean_anything(self) -> None:
        self.assertFalse(self._about("C:/a/src/thing.o",
                                     "Inbox - src of the problem"))

    # -- and what still has to work ---------------------------------------
    def test_a_real_file_named_after_an_application_still_counts(self) -> None:
        # firefox.html, open in Firefox, is a real file: its name appearing
        # in full is evidence of its own.
        self.assertTrue(self._about(r"C:/notes/firefox.html",
                                    "firefox.html \u2014 Mozilla Firefox",
                                    "firefox.exe"))

    def test_a_filename_in_a_plain_title_counts(self) -> None:
        self.assertTrue(self._about(
            r"C:/work/personalized_agent_release/build.py",
            "build.py - personalized_agent_release", "sublime_text.exe"))

    def test_a_folder_title_from_a_file_manager_counts(self) -> None:
        self.assertTrue(self._about(
            r"C:/work/personalized_agent_release/docs/x.md",
            "personalized_agent_release - File Explorer", "explorer.exe"))

    def test_a_command_in_a_terminal_names_its_own_file(self) -> None:
        title = r"C:\WINDOWS\system32\cmd.exe - python  run.py"
        self.assertTrue(self._about(r"C:/work/project/run.py", title,
                                    "WindowsTerminal.exe"))
        self.assertFalse(self._about(r"C:/work/project/store.py", title,
                                     "WindowsTerminal.exe"))

    def test_the_names_that_say_nothing_are_ordinary_folder_names(self) -> None:
        # They are too common to be evidence and too useful to forbid, so
        # they simply do not count towards this one test.
        from personalized_agent.common.titles import SAYS_NOTHING

        for common in ("agent", "src", "docs", "api", "build", "tests"):
            self.assertIn(common, SAYS_NOTHING)

    def test_the_listener_and_the_panel_ask_the_same_function(self) -> None:
        """A rule improved in one place has to improve both.

        Five fixes, and each one left a panel still displaying what the
        Listener had by then learned not to record.
        """
        import inspect

        from personalized_agent.agent.overview import service
        from personalized_agent.listener.core import privacy

        self.assertIn("about_this_file", inspect.getsource(privacy))
        self.assertIn("about_this_file", inspect.getsource(service))


class WhatIsShownIsJudgedAgainTests(unittest.TestCase):
    """Section 19, as amended: the records were judged by the older rules.

    Excluding something when it arrives does nothing about what is already
    stored, and the panel is where the user meets it. Re-judging on the way
    out costs a comparison and is reversible, which matters while the rules
    are still gaining a fix a day.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.overview = self.agent.app.overview

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _event(self, path, **data):
        return ListenerEvent(event_type="filesystem.opened", source="filesystem",
                             data={"path": path, **data})

    def test_a_record_the_older_rules_let_through_is_not_shown(self) -> None:
        kept = self.overview._worth_showing([self._event(
            r"C:/p/extensions/browser/firefox/manifest.json",
            while_showing="Personalized Agent - Mozilla Firefox",
            while_using="firefox.exe")])
        self.assertEqual(kept, [])

    def test_one_that_still_looks_like_theirs_is(self) -> None:
        kept = self.overview._worth_showing([self._event(
            r"C:/work/notes/report.md",
            while_showing="report.md - notes", while_using="sublime_text.exe")])
        self.assertEqual(len(kept), 1)

    def test_an_older_record_with_no_window_is_kept(self) -> None:
        """It cannot be re-judged, and what cannot be judged is kept.

        The same answer the Listener gives, for the same reason - and most
        of what is stored is this: of 3,542 file events kept on one machine,
        only 172 carried a window title at all.
        """
        kept = self.overview._worth_showing([self._event(r"C:/work/old.md")])
        self.assertEqual(len(kept), 1)

    def test_and_nothing_is_deleted_to_achieve_it(self) -> None:
        # Re-judging on the way out is reversible; deleting is not, and the
        # rules behind the judgement were still gaining a fix a day.
        import inspect

        from personalized_agent.agent.overview import service

        source = inspect.getsource(service.OverviewService._worth_showing)
        self.assertNotIn("raw_store", source, "it filters a list, it does not "
                                              "reach back into storage")
        self.assertNotIn("prune", source)

    def test_turning_the_judgement_off_shows_everything_again(self) -> None:
        self.agent.app.config.set("listener_only_what_the_user_did", False)
        kept = self.overview._worth_showing([self._event(
            r"C:/p/extensions/browser/firefox/manifest.json",
            while_showing="Personalized Agent - Mozilla Firefox",
            while_using="firefox.exe")])
        self.assertEqual(len(kept), 1)


class ATranscriptIsReadOncePerChangeTests(unittest.TestCase):
    """Section 24, as amended: 557 MB re-read every two minutes.

    Summarising a session means parsing every line of it as JSON, and a poll
    re-read every transcript from the last thirty days to find out whether
    anything had been added. Measured on one machine: **2,277 files, 557 MB,
    4.4 seconds of a core, every 120 seconds** - and almost nothing had
    changed between one pass and the next. Reading only what moved brought
    the same pass to 267 ms.

    A transcript only ever grows, so its size and modification time together
    say whether the copy already read is still the whole of it.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.root = Path(tempfile.mkdtemp(prefix="pa-sess-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        self.agent.cleanup()

    def _provider(self):
        from personalized_agent.listener.providers.assistant import AssistantProvider

        return AssistantProvider(self.agent.app.config)

    def _transcript(self, name="one.jsonl", lines=3):
        path = self.root / name
        path.write_text("\n".join(
            json.dumps({"timestamp": f"2026-09-20T09:0{index}:00Z",
                        "sessionId": "s1", "type": "user"})
            for index in range(lines)), encoding="utf-8")
        return path

    def test_a_transcript_that_has_not_changed_is_not_read_again(self) -> None:
        provider = self._provider()
        path = self._transcript()
        reads = []
        original = provider._summarise

        def counted(target):
            reads.append(target)
            return original(target)

        provider._summarise = counted
        first = provider._summary_of(path)
        again = provider._summary_of(path)
        self.assertEqual(len(reads), 1, "read once")
        self.assertEqual(first, again)

    def test_one_that_has_grown_is(self) -> None:
        provider = self._provider()
        path = self._transcript(lines=2)
        first = provider._summary_of(path)
        self._transcript(lines=6)
        again = provider._summary_of(path)
        self.assertNotEqual(first.get("records"), again.get("records"))

    def test_what_the_caller_does_with_it_cannot_corrupt_the_memory(self) -> None:
        # The caller adds the project and the tool to what it gets back.
        provider = self._provider()
        path = self._transcript()
        first = provider._summary_of(path)
        first["records"] = 999
        self.assertNotEqual(provider._summary_of(path)["records"], 999)

    def test_a_file_that_is_gone_is_not_a_failure(self) -> None:
        provider = self._provider()
        self.assertIsNone(provider._summary_of(self.root / "missing.jsonl"))

    def test_it_does_not_remember_without_end(self) -> None:
        from personalized_agent.listener.providers.assistant import AssistantProvider

        self.assertLessEqual(AssistantProvider.REMEMBERED, 20000)
        self.assertGreaterEqual(AssistantProvider.REMEMBERED, 500)

    def test_one_stat_serves_both_questions(self) -> None:
        """Old enough to skip, and changed since it was last read.

        At two thousand files a second stat each is half the cost of the
        whole pass, once the reading itself is no longer repeated.
        """
        import inspect

        from personalized_agent.listener.providers import assistant

        source = inspect.getsource(assistant.AssistantProvider._sessions)
        self.assertIn("_summary_of(path, info)", source)

