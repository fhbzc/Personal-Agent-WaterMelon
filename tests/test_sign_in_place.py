"""A place to sign in to the Claude client, and the world's growth when no model answers or its answer is a little off
(the user, 2026-11-21: "你那个mac, claude cli 没有登录，你要给一个登录的位置"; "如果你解析不成功，是不是说明有的design会失效")."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401

from personalized_agent.platform_layer import terminal

ROOT = Path(__file__).resolve().parents[1] / "personalized_agent"


class ATerminalForTheSignInTests(unittest.TestCase):
    def test_on_a_mac_a_command_file_opened_by_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(terminal.subprocess, "Popen") as started:
            self.assertTrue(terminal.open_in_terminal(["/usr/local/bin/claude", "auth", "login"], "Sign in",
                                                      Path(folder), "Done? Go back.", platform="darwin"))
            script = Path(folder) / "sign_in.command"
            words = script.read_text(encoding="utf-8")
            self.assertTrue(words.startswith("#!/bin/zsh\n"))
            self.assertIn("/usr/local/bin/claude auth login", words)
            self.assertIn("Done? Go back.", words)
            self.assertEqual(started.call_args.args[0], ["open", "-a", "Terminal", str(script)], "no AppleScript")

    def test_on_windows_a_command_window(self) -> None:
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(terminal.subprocess, "Popen") as started:
            self.assertTrue(terminal.open_in_terminal([r"C:\Program Files\claude.exe", "auth", "login"], "Sign in",
                                                      Path(folder), platform="win32"))
            line = started.call_args.args[0]
            self.assertIn('start "Sign in" cmd.exe /k', line)
            self.assertIn('"C:\\Program Files\\claude.exe" auth login', line)

    def test_the_settings_and_the_panel_offer_it(self) -> None:
        routes = (ROOT / "api" / "routes.py").read_text(encoding="utf-8")
        self.assertIn('@route("POST", "/api/models/claude/sign_in")', routes)
        self.assertIn('open_in_terminal([path, "auth", "login"]', routes)
        self.assertIn('@route("GET", "/api/models/attention")', routes)
        app = (ROOT / "api" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('one.provider === "claude_code" && !ready ? `<button type="button" class="small"', app)
        notes = (ROOT / "api" / "web" / "notes.js").read_text(encoding="utf-8")
        self.assertIn('said = await api.get("/api/models/attention")', notes)
        self.assertIn('await api.post("/api/models/claude/sign_in", { provider: "claude_code" })', notes)


class ASignInWhereverItIsSaidTests(unittest.TestCase):
    def test_every_page_puts_a_button_beside_the_words(self) -> None:
        # 2026-11-21: "你提示的Other message里也可以加上登录链接，而不是让我去claude auth login"
        web = ROOT / "api" / "web"
        for page in ("index.html", "notes.html", "chat.html"):
            self.assertIn('<script src="/sign-in-link.js"></script>', (web / page).read_text(encoding="utf-8"), page)
        js = (web / "sign-in-link.js").read_text(encoding="utf-8")
        self.assertIn(r"const SAID = /Claude Code(?: \([^)]{1,20}\))? is not signed in|Claude Code 没有登录|Claude Code CLI sign-in|claude auth login/i;", js)
        self.assertIn("const PHRASE = /Sign in to Claude Code|登录 Claude Code|`?claude auth login`?/i;", js)
        self.assertIn('a.href = "#";', js, "a link in the words, not a button beside them")
        self.assertIn('post("/api/models/claude/sign_in", { provider: "claude_code" })', js)
        from personalized_agent.models.providers.claude_code_provider import ClaudeCodeProvider
        self.assertNotIn("Run `", ClaudeCodeProvider.SIGN_IN, "no command to type")


class TheWorldsGrowthReadTests(unittest.TestCase):
    def test_no_model_no_growth_and_a_little_off_read_all_the_same(self) -> None:
        world = (ROOT / "agent" / "playworld.py").read_text(encoding="utf-8")
        self.assertIn('if str(getattr(response, "provider", "") or "") == "offline":', world)
        self.assertIn('return {"error": "no language model is configured"}', world)
        self.assertIn("said = max(found, key=lambda one: len(json.dumps(one, ensure_ascii=False)))", world)


if __name__ == "__main__":
    unittest.main()
