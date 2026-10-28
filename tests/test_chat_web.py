"""The web in Chat, and what it looked up kept (the user, 2026-10-27): Chat
on the checked route with the app's search and page reader held to the web's
checks; a client's own search seen by them (Codex's, from its JSON); Codex
started from a short path where its own is too deep for Windows; what was
looked up kept with the reply, for the heartbeat; a Chat turn kept whole as
material."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from support import TemporaryAgent

from personalized_agent.agent.core.agent_core import AgentCore, CHAT_WEB_TOOL_NAMES
from personalized_agent.common.events.models import utcnow
from personalized_agent.heartbeat.core.heartbeat import CHAT_APPLICATION
from personalized_agent.heartbeat.experience.materials import Materials
from personalized_agent.models.providers import codex_provider
from personalized_agent.models.providers.codex_provider import CodexProvider
from personalized_agent.runtime.web_guard import WebGuard

RUN = "personalized_agent.common.children.run"


class CodexsOwnSearchIsSeenTests(unittest.TestCase):

    def test_its_sites_and_words_seen_and_the_search_kept(self) -> None:
        guard = WebGuard()
        line = json.dumps({"type": "item.completed", "item": {
            "id": "exec-1", "type": "web_search", "query": "harbour town weather today",
            "action": {"type": "search", "query": "harbour town weather today"},
            "results": [{"type": "text_result", "domain": "www.weather-desk.example",
                         "ref_id": "turn0news1", "snippet": "Showers clearing by noon"}]}})
        CodexProvider._searched(line, guard)
        self.assertIn("www.weather-desk.example", guard.known_hosts)
        self.assertEqual(guard.why_not("https://www.weather-desk.example/")[0],
                         "the front page of a known site")
        self.assertEqual(guard.looked, [{"how": "search",
                                         "what": "harbour town weather today"}])
        # Begun, not ended; another item; not JSON: nothing.
        for other in (line.replace("item.completed", "item.started"),
                      json.dumps({"type": "item.completed",
                                  "item": {"type": "agent_message", "text": "web_search"}}),
                      "web_search, not JSON"):
            CodexProvider._searched(other, guard)
        self.assertEqual(len(guard.looked), 1)


class CodexFromAShortPathTests(unittest.TestCase):
    """npm's codex.cmd beside a package too deep: started from hard links in
    the agent's data folder (option 4)."""

    def _installed(self, top: Path, version: str) -> Path:
        npm = top / "npm"
        target = (npm / "node_modules" / "@openai" / "codex" / "node_modules" / "@openai"
                  / "codex-win32-x64" / "vendor" / "x86_64-pc-windows-msvc")
        (target / "bin").mkdir(parents=True, exist_ok=True)
        (target / "codex-resources").mkdir(exist_ok=True)
        (target / "bin" / "codex.exe").write_text("program " + version, encoding="utf-8")
        (target / "bin" / "codex-code-mode-host.exe").write_text("helper", encoding="utf-8")
        (target / "codex-resources" / "one.txt").write_text("r", encoding="utf-8")
        (target / "codex-package.json").write_text(json.dumps({"version": version}),
                                                   encoding="utf-8")
        (npm / "codex.cmd").write_text("@echo off", encoding="utf-8")
        return npm / "codex.cmd"

    def test_linked_the_same_files_and_again_for_a_new_version(self) -> None:
        with tempfile.TemporaryDirectory() as top:
            top = Path(top)
            found = self._installed(top, "1.2.3")
            program = codex_provider._codex_exe(found)
            self.assertEqual(program.name, "codex.exe")
            data = top / "data"
            provider = CodexProvider(data_dir=data)
            with patch.object(codex_provider, "PATH_HOLDS", 0):
                short = Path(provider._short(str(found)))
            self.assertEqual(short, data / "clients" / "codex" / "1.2.3" / "bin" / "codex.exe")
            self.assertTrue(os.path.samefile(short, program))
            self.assertTrue((short.parent / "codex-code-mode-host.exe").exists())
            self.assertTrue((short.parent.parent / "codex-resources" / "one.txt").exists())
            # A new version: linked anew, the old version's links let go.
            for one in program.parent.iterdir():
                one.unlink()
            self._installed(top, "1.2.4")
            provider._shortened.clear()
            with patch.object(codex_provider, "PATH_HOLDS", 0):
                again = Path(provider._short(str(found)))
            self.assertEqual(again.parent.parent.name, "1.2.4")
            self.assertFalse((data / "clients" / "codex" / "1.2.3").exists())
            self.assertEqual(again.read_text(encoding="utf-8"), "program 1.2.4")

    def test_short_enough_started_as_installed(self) -> None:
        with tempfile.TemporaryDirectory() as top:
            found = self._installed(Path(top), "1.2.3")
            provider = CodexProvider(data_dir=Path(top) / "data")
            self.assertEqual(provider._short(str(found)),
                             str(codex_provider._codex_exe(found)))
            self.assertFalse((Path(top) / "data").exists())


class TheWebInChatTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_held_to_the_checks(self) -> None:
        guard = WebGuard()
        guard.know("my budget code 48213", "user")
        with patch("personalized_agent.agent.work.tools.fetch") as fetch:
            said = AgentCore._web(guard, "fetch_url", {"url": "https://news.example/a?id=1"})
            self.assertIn("query", said["error"])
            said = AgentCore._web(guard, "fetch_url", {"url": "https://news.example/48213"})
            self.assertIn("error", said)
            fetch.assert_not_called()
        self.assertIn("say what to search for", AgentCore._web(guard, "web_search", {})["error"])
        self.assertEqual(guard.looked, [])

    def _converse(self, replies):
        provider = self.app.models.provider("codex")
        provider.executable = lambda: "codex"
        provider._auth_status = lambda force=False: {"logged_in": True}
        self.app.config.set("primary_provider", "codex")
        self.app.config.set("primary_model", "default")
        replies = iter(replies)

        def answer(command, **kwargs):
            if list(command[1:]) == ["exec", "--help"]:
                return subprocess.CompletedProcess(command, 0, "--ignore-user-config\n", "")
            out = json.dumps({"type": "item.completed",
                              "item": {"type": "agent_message", "text": next(replies)}})
            return subprocess.CompletedProcess(command, 0, out, "")

        with patch(RUN, side_effect=answer):
            return self.app.agent.handle_message("What is on the front page today?")

    def test_what_it_read_kept_with_the_reply_and_read_by_the_heartbeat(self) -> None:
        self.assertEqual(CHAT_WEB_TOOL_NAMES, {"web_search", "fetch_url"})
        call = {"tool": "fetch_url", "arguments": {"url": "https://news.example/weather"}}
        page = {"url": "https://news.example/weather", "text": "Rain later. " * 3000,
                "links": []}
        with patch("personalized_agent.agent.work.tools.fetch", return_value=page):
            result = self._converse([json.dumps(call), "Rain later, it says."])
        self.assertEqual(result["looked_up"],
                         [{"how": "read", "what": "https://news.example/weather"}])
        # The page as the model read it: cut to a length.
        self.assertIn("Rain later.", result["tool_calls"][0]["result"])
        reply = [one for one in self.app.db.query(
            "SELECT metadata FROM messages WHERE role = 'assistant'")][-1]
        meta = json.loads(reply["metadata"])
        self.assertEqual(meta["looked_up"], result["looked_up"])
        self.assertEqual(meta["tools_used"], ["fetch_url"])
        said = " ".join(str(one) for one in self.app.heartbeat._activity_since(10)
                        if one.get("application") == CHAT_APPLICATION)
        self.assertIn("having read https://news.example/weather", said)


class AChatTurnIsKeptWholeTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_users_message_and_the_reply_both(self) -> None:
        from personalized_agent.agent.states.store import State
        store = self.app.conversations
        conversation = store.create("Slides")
        asked = store.add_message(conversation, "user", "make the slides denser")
        replied = store.add_message(conversation, "assistant", "Done: two slides merged.")
        store.add_message(conversation, "user", "now the next thing")
        tested = store.add_message(conversation, "assistant", "a test",
                                   metadata={"origin": "chat_debugger"})
        now = utcnow()
        window = {"from": (now - timedelta(minutes=15)).isoformat(), "to": now.isoformat()}
        state = self.app.states.add(State(
            title="They revise the slides.", detail="They revise the slides.",
            starts_at=now - timedelta(minutes=14), ends_at=now,
            metadata={"evidence": [], "looked_at": window}))
        materials = Materials(self.app.db, Path(self.app.library.root) / "references")
        made = {one["event_id"] for one in materials.link(state, [replied["id"]], window)}
        self.assertEqual(made, {asked["id"], replied["id"]})
        self.assertNotIn(tested["id"], made)
        how = {row["event_id"]: row["linked_by"] for row in self.app.db.query(
            "SELECT event_id, linked_by FROM state_materials")}
        self.assertEqual(how, {replied["id"]: "model", asked["id"]: "chat"})


if __name__ == "__main__":
    unittest.main()
