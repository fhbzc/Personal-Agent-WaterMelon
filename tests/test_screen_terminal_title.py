"""Section 36.3.2: a terminal is read the moment its title changes.

A coding assistant draws a banner with the folder it was started in, for a few seconds,
and the terminal's title is then the session's name. Read every fifteen seconds the
banner was caught for five sessions in twenty. Read when the title changes, it is on
screen when it is read.
"""

import unittest

from personalized_agent.listener.providers.screen import (
    TERMINAL_PROGRAMS, a_new_title_in_a_terminal)


class WhenToReadATerminalTests(unittest.TestCase):
    def test_the_first_look_only_notes_the_title(self) -> None:
        changed, kept = a_new_title_in_a_terminal(None, "WindowsTerminal.exe", "Windows PowerShell")
        self.assertFalse(changed)
        self.assertEqual(kept, ("windowsterminal.exe", "windows powershell"))

    def test_a_session_starting_is_a_new_title(self) -> None:
        _, before = a_new_title_in_a_terminal(None, "WindowsTerminal.exe", "me@cluster:~/tide_tables")
        changed, _ = a_new_title_in_a_terminal(before, "WindowsTerminal.exe", "✳ Claude Code")
        self.assertTrue(changed)

    def test_the_spinner_changing_while_the_assistant_works_is_not(self) -> None:
        """The assistant redraws a spinner in the title every second; reading at each
        would be reading continuously."""
        _, before = a_new_title_in_a_terminal(None, "WindowsTerminal.exe", "✳ agentpedia-20261011")
        for mark in ("◐", "◑", "◒", "◓", "⠂", "✳"):
            changed, before = a_new_title_in_a_terminal(before, "WindowsTerminal.exe",
                                                        f"{mark} agentpedia-20261011")
            self.assertFalse(changed, mark)

    def test_a_session_being_renamed_is_a_new_title(self) -> None:
        _, before = a_new_title_in_a_terminal(None, "WindowsTerminal.exe", "✳ Claude Code")
        changed, _ = a_new_title_in_a_terminal(before, "WindowsTerminal.exe", "✳ icc-2027-v13")
        self.assertTrue(changed)

    def test_a_browser_changing_its_title_is_left_to_the_usual_interval(self) -> None:
        _, before = a_new_title_in_a_terminal(None, "msedge.exe", "A page")
        changed, _ = a_new_title_in_a_terminal(before, "msedge.exe", "Another page")
        self.assertFalse(changed, "only terminals are read early: pages change title constantly")

    def test_the_same_title_in_the_same_terminal_is_not_new(self) -> None:
        _, before = a_new_title_in_a_terminal(None, "WindowsTerminal.exe", "Windows PowerShell")
        changed, _ = a_new_title_in_a_terminal(before, "WindowsTerminal.exe", "Windows PowerShell")
        self.assertFalse(changed)

    def test_the_programs_are_the_same_ones_the_rhythm_reads_banners_from(self) -> None:
        from personalized_agent.heartbeat.monitor.operations import TERMINAL_PROGRAMS as READ
        self.assertEqual(set(TERMINAL_PROGRAMS), set(READ),
                         "the screen must be read early in exactly the programs whose banner is used")


class TheProviderReadsEarlyTests(unittest.TestCase):
    """The loop's decision, with a window that is moved by the test."""

    class Window:
        def __init__(self) -> None:
            self.application, self.title = "WindowsTerminal.exe", "Windows PowerShell"
            self.pid = None

    def _provider(self, enabled: bool = True):
        from personalized_agent.listener.providers.screen import ScreenProvider
        window = self.Window()

        class Activity:
            def get_active_window(self_inner):
                return window

        class Platform:
            activity = Activity()
            name = "test"

        class Config:
            def get(self_inner, key, default=None):
                return enabled if key == "listener_screen_enabled" else default

            def lines(self_inner, key):
                return []

        return ScreenProvider(Config(), Platform()), window

    def test_a_title_that_changes_in_a_terminal_is_read_at_once(self) -> None:
        provider, window = self._provider()
        self.assertFalse(provider._a_terminal_just_changed(window, 0.0), "the first look only notes it")
        window.title = "✳ Claude Code"
        self.assertTrue(provider._a_terminal_just_changed(window, 1.0))
        self.assertFalse(provider._a_terminal_just_changed(window, 2.0), "and is not read again")

    def test_nothing_is_read_early_when_reading_the_screen_is_switched_off(self) -> None:
        provider, window = self._provider(enabled=False)
        provider._a_terminal_just_changed(window, 0.0)
        window.title = "✳ Claude Code"
        self.assertFalse(provider._a_terminal_just_changed(window, 1.0))

    def test_a_title_that_will_not_keep_still_is_read_early_only_so_often(self) -> None:
        from personalized_agent.listener.providers.screen import MOST_EXTRA_READINGS_A_MINUTE
        provider, window = self._provider()
        provider._a_terminal_just_changed(window, 0.0)
        reads = 0
        for n in range(40):
            window.title = f"session {n}"
            reads += provider._a_terminal_just_changed(window, 1.0 + n)
        self.assertEqual(reads, MOST_EXTRA_READINGS_A_MINUTE)

    def test_a_window_that_cannot_be_asked_about_is_not_an_error(self) -> None:
        provider, _ = self._provider()
        self.assertFalse(provider._a_terminal_just_changed(None, 0.0))

    def test_the_tick_reads_at_once_when_a_terminal_title_changes(self) -> None:
        provider, window = self._provider()
        looks = []
        provider._look = lambda: looks.append(1)
        provider._next_read_at = float("inf")
        provider._tick()
        window.title = "✳ Claude Code"
        provider._tick()
        self.assertEqual(len(looks), 1, "the new title is read before the ordinary interval")

if __name__ == "__main__":
    unittest.main()
