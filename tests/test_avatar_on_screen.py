"""The avatar is shown where it can be seen. Moved from an external display
back to the laptop's own, it stayed where the external one had been - shown,
as far as the window knew, and nowhere on screen - and Hide and Show only hid
and showed it there."""

from __future__ import annotations

import unittest
from unittest import mock

import support  # noqa: F401

from personalized_agent.ui import desktop as desktop_module


class _Window:
    def __init__(self, x, y):
        self.x, self.y = x, y
        self.placed = []
        self.shown = 0

    def update_idletasks(self):
        pass

    def winfo_x(self):
        return self.x

    def winfo_y(self):
        return self.y

    def deiconify(self):
        self.shown += 1

    def withdraw(self):
        pass

    def lift(self):
        pass

    def attributes(self, *a):
        pass


class _Tray:
    def __init__(self):
        self.rebuilt = 0

    def refresh_menu(self):
        self.rebuilt += 1


def _desktop(window):
    agent = desktop_module.DesktopAgent.__new__(desktop_module.DesktopAgent)
    agent.agent_window = window
    agent.size = 120
    agent.character_hidden = False
    agent.tray = _Tray()
    agent._place_bottom_right = lambda: window.placed.append("home")
    return agent


class PutBackOnAScreenTests(unittest.TestCase):

    def test_off_every_screen_it_goes_home(self) -> None:
        window = _Window(5200, 900)
        agent = _desktop(window)
        with mock.patch.object(desktop_module, "_on_a_screen", return_value=False):
            agent._keep_on_screen()
        self.assertEqual(window.placed, ["home"])

    def test_on_a_screen_it_stays(self) -> None:
        window = _Window(300, 300)
        agent = _desktop(window)
        with mock.patch.object(desktop_module, "_on_a_screen", return_value=True):
            agent._keep_on_screen()
        self.assertEqual(window.placed, [])

    def test_show_puts_it_back_and_the_menu_follows(self) -> None:
        window = _Window(5200, 900)
        agent = _desktop(window)
        with mock.patch.object(desktop_module, "_on_a_screen", return_value=False):
            agent.toggle_character()            # hide
            self.assertTrue(agent.character_hidden)
            agent._keep_on_screen()             # hidden: left alone
            self.assertEqual(window.placed, [])
            agent.toggle_character()            # show
        self.assertFalse(agent.character_hidden)
        self.assertEqual(window.placed, ["home"])
        self.assertEqual(agent.tray.rebuilt, 2, "the menu's words built again each time")

    @unittest.skipUnless(desktop_module.sys.platform.startswith("win"), "asked of Windows")
    def test_windows_says_which_points_are_on_a_screen(self) -> None:
        self.assertTrue(desktop_module._on_a_screen(None, 10, 10))
        self.assertFalse(desktop_module._on_a_screen(None, -100000, -100000))


class TheRightClickMenuTests(unittest.TestCase):

    def test_chat_overview_hide_and_quit_not_delegate_work(self) -> None:
        labels = []

        class _Menu:
            def __init__(self, *a, **k):
                pass

            def add_command(self, label="", **_k):
                labels.append(label)

            def add_separator(self):
                labels.append("-")

            def tk_popup(self, *a):
                pass

            def grab_release(self):
                pass
        agent = _desktop(_Window(10, 10))
        agent.root = None
        agent._words = lambda text: text
        agent.tray = type("T", (), {"icon": object()})()
        with mock.patch.object(desktop_module.tk, "Menu", _Menu):
            agent._on_right_click(type("E", (), {"x_root": 1, "y_root": 1})())
        self.assertEqual(labels, ["Chat", "Overview", "Hide avatar", "-", "Quit"])


if __name__ == "__main__":
    unittest.main()
