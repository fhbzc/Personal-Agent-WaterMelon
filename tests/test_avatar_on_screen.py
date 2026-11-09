"""The avatar is shown where it can be seen. Moved from an external display
back to the laptop's own, it stayed where the external one had been - shown,
as far as the window knew, and nowhere on screen - and Hide and Show only hid
and showed it there."""

from __future__ import annotations

import sys
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

    def test_half_past_the_side_of_the_screen_it_stays(self) -> None:
        # The user, 2026-11-04: left at the side, its middle past it.
        def on_the_screen(_window, x, y):
            return 0 <= x < 1920 and 0 <= y < 1080
        with mock.patch.object(desktop_module, "_on_a_screen", side_effect=on_the_screen):
            for x in (1865, 1886, -86):                  # beside it, in the right, in the left
                window = _Window(x, 910)
                _desktop(window)._keep_on_screen()
                self.assertEqual(window.placed, [], x)
            window = _Window(2100, 910)
            _desktop(window)._keep_on_screen()
            self.assertEqual(window.placed, ["home"], "none of it on a screen")

    def test_on_a_screen_it_stays(self) -> None:
        window = _Window(300, 300)
        agent = _desktop(window)
        with mock.patch.object(desktop_module, "_on_a_screen", return_value=True):
            agent._keep_on_screen()
        self.assertEqual(window.placed, [])

    def test_show_puts_it_back_and_the_menu_follows(self) -> None:
        window = _Window(5200, 900)
        agent = _desktop(window)
        with mock.patch.object(desktop_module.sys, "platform", "win32"), \
                mock.patch.object(desktop_module, "_on_a_screen", return_value=False):
            agent.toggle_character()            # hide
            self.assertTrue(agent.character_hidden)
            agent._keep_on_screen()             # hidden: left alone
            self.assertEqual(window.placed, [])
            agent.toggle_character()            # show
        self.assertFalse(agent.character_hidden)
        self.assertEqual(window.placed, ["home"])
        self.assertEqual(agent.tray.rebuilt, 2, "the menu's words built again each time")

    def test_on_a_mac_hidden_without_being_withdrawn(self) -> None:
        # A borderless window withdrawn on a Mac did not come back on Show.
        class _MacWindow(_Window):
            def __init__(self):
                super().__init__(300, 300)
                self.alpha, self.at, self.withdrawn = 1.0, "120x120+300+300", 0

            def withdraw(self):
                self.withdrawn += 1

            def attributes(self, *a):
                if a == ("-alpha",):
                    return self.alpha
                if a[0] == "-alpha":
                    self.alpha = a[1]

            def geometry(self, *a):
                if a:
                    self.at = a[0]
                return self.at

        window = _MacWindow()
        agent = _desktop(window)
        with mock.patch.object(desktop_module.sys, "platform", "darwin"), \
                mock.patch.object(desktop_module, "_on_a_screen", return_value=True):
            agent.toggle_character()            # hide
            self.assertEqual((window.alpha, window.withdrawn), (0.0, 0))
            self.assertTrue(window.at.startswith("+-"), "off every screen")
            agent.toggle_character()            # show
        self.assertEqual((window.alpha, window.at), (1.0, "120x120+300+300"))
        self.assertFalse(agent.character_hidden)

    def test_on_a_mac_the_system_says_which_screens_there_are(self) -> None:
        """Hidden on a large display, shown on a small one: Tk is not asked."""
        window = _Window(0, 0)
        small = [(0, 0, 1440, 900)]
        with mock.patch.object(desktop_module.sys, "platform", "darwin"),                 mock.patch.object(desktop_module, "_mac_screens", return_value=small):
            self.assertTrue(desktop_module._on_a_screen(window, 1400, 800))
            self.assertFalse(desktop_module._on_a_screen(window, 3000, 1400), "the large one has gone")
        two = small + [(1440, -200, 3360, 880)]
        with mock.patch.object(desktop_module.sys, "platform", "darwin"),                 mock.patch.object(desktop_module, "_mac_screens", return_value=two):
            self.assertTrue(desktop_module._on_a_screen(window, 3000, 100), "on the second one")

    def _where_it_stays(self, x, y, screens=((0, 0, 1920, 1080),)):
        window = _Window(x, y)
        agent = _desktop(window)
        agent.size = 128
        with mock.patch.object(desktop_module.sys, "platform", "darwin"), \
                mock.patch.object(desktop_module, "_mac_screens", return_value=list(screens)):
            agent._keep_on_screen()
        return window.placed

    def test_in_a_side_it_is_left_where_it_is(self) -> None:
        from personalized_agent.ui import avatar_tuck as tuck
        sides = tuck.Sides(0, 1920, True, True, 0, 1080, True, True)
        for side in ("left", "right", "top", "bottom"):
            x, y = tuck.tucked_at(side, 800, 500, 128, sides)
            self.assertEqual(self._where_it_stays(x, y), [], side)

    def test_in_a_corner_of_the_screen_with_as_much_showing_it_is_left(self) -> None:
        self.assertEqual(self._where_it_stays(1886, 1046), [], "a square as large as a side shows")
        self.assertEqual(self._where_it_stays(-94, -94), [])

    def test_a_few_pixels_showing_is_lost(self) -> None:
        for x, y in ((1916, 500), (-124, 500), (800, -124), (800, 1076), (1916, 1076)):
            self.assertEqual(self._where_it_stays(x, y), ["home"], (x, y))

    def test_across_two_screens_it_is_on_them(self) -> None:
        two = ((0, 0, 1440, 900), (1440, 0, 3360, 1080))
        self.assertEqual(self._where_it_stays(1400, 300, two), [])

    def test_the_dock_finds_one_that_is_not_hidden_too(self) -> None:
        window = _Window(5200, 900)
        agent = _desktop(window)
        agent.open_url = lambda _path: None
        with mock.patch.object(desktop_module.sys, "platform", "darwin"), \
                mock.patch.object(desktop_module, "_mac_screens", return_value=[(0, 0, 1440, 900)]):
            agent._dock_pressed()
        self.assertEqual(window.placed, ["home"])
        self.assertFalse(agent.character_hidden)

    def test_in_a_side_the_vine_stays_and_hidden_it_goes_and_comes_back(self) -> None:
        window = _Window(300, 300)
        agent = _desktop(window)
        told = []
        agent._chat_vine = mock.Mock(place=lambda: told.append(agent._vine_side()))
        agent._tucked = "right"
        self.assertEqual(agent._vine_side(), "right", "in a side: leaves, vine and button stay")
        with mock.patch.object(desktop_module.sys, "platform", "win32"), \
                mock.patch.object(desktop_module, "_on_a_screen", return_value=True):
            agent.toggle_character()
            agent.toggle_character()
        self.assertEqual(told, ["", "right"], "told at once, each way")

    def test_the_dock_brings_a_hidden_avatar_back(self) -> None:
        window = _Window(300, 300)
        agent = _desktop(window)
        opened = []
        agent.open_url = opened.append
        with mock.patch.object(desktop_module.sys, "platform", "win32"), \
                mock.patch.object(desktop_module, "_on_a_screen", return_value=True):
            agent.toggle_character()
            agent._dock_pressed()
            self.assertFalse(agent.character_hidden, "out it comes")
            agent._dock_pressed()
        self.assertFalse(agent.character_hidden, "shown, it is not hidden by it")
        self.assertEqual(opened, ["/#overview", "/#overview"])

    def test_on_a_mac_clear_only_on_the_tk_it_was_seen_on(self) -> None:
        # Without the native view (PR #5): on 8.6.16 a clear window showed
        # the character's text, not its picture.
        self.assertTrue(desktop_module._clear_window_shows_pictures("8.6.15"))
        self.assertFalse(desktop_module._clear_window_shows_pictures("8.6.16"))
        self.assertFalse(desktop_module._clear_window_shows_pictures("9.0.1"))

    def test_native_renderer_receives_the_unchanged_avatar_frame(self) -> None:
        from PIL import Image
        agent = _desktop(_Window(10, 10))
        agent._native_avatar = mock.Mock()
        agent.canvas = mock.Mock()
        agent._live = {}
        frame = Image.new("RGBA", (128, 128), (20, 80, 30, 255))
        before = frame.tobytes()
        agent._present_frame(frame)
        agent._native_avatar.show.assert_called_once_with(frame)
        self.assertIs(agent._live["image"], frame)
        self.assertEqual(frame.tobytes(), before)
        agent.canvas.create_image.assert_not_called()

    def test_native_draw_failure_keeps_the_avatar_visible(self) -> None:
        from PIL import Image
        agent = _desktop(_Window(10, 10))
        native = mock.Mock()
        native.show.side_effect = ValueError("cannot decode")
        agent._native_avatar = native
        agent.canvas = mock.Mock()
        agent._live = {}
        frame = Image.new("RGBA", (128, 128), (20, 80, 30, 255))
        with mock.patch.object(desktop_module.ImageTk, "PhotoImage") as photo:
            agent._present_frame(frame)
        native.close.assert_called_once_with()
        self.assertIsNone(agent._native_avatar)
        photo.assert_called_once_with(frame)
        self.assertIs(agent._live["image"], photo.return_value)
        agent.canvas.create_image.assert_called_once_with(60, 60, image=photo.return_value)

    def test_without_the_native_view_tk_s_own_clear_window_only_where_it_shows(self) -> None:
        agent = _desktop(_Window(10, 10))
        agent._rig, agent._frames, agent.canvas = object(), {}, mock.Mock()
        agent.root = mock.Mock()
        agent.agent_window = mock.Mock()
        failing = mock.Mock(side_effect=RuntimeError("no AppKit"))
        for patchlevel, clear in (("8.6.15", True), ("8.6.16", False)):
            with self.subTest(patchlevel=patchlevel):
                agent.agent_window.reset_mock()
                agent.root.tk.call.return_value = patchlevel
                with mock.patch.object(desktop_module.sys, "platform", "darwin"), \
                        mock.patch.dict(sys.modules, {"personalized_agent.ui.macos_avatar":
                                                      mock.Mock(NativeAvatar=failing)}):
                    agent._enable_native_avatar()
                self.assertIsNone(agent._native_avatar)
                self.assertEqual(mock.call("-transparent", True) in
                                 agent.agent_window.attributes.call_args_list, clear)

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
        self.assertEqual(labels, ["Chat", "To do", "Collections", "Waiting notifications", "Overview",
                                  "Hide avatar", "-", "Quit"])


if __name__ == "__main__":
    unittest.main()


class BackOnTopTests(unittest.TestCase):
    """The user, 2026-11-08: "为什么avatar没有放在front" - found under a browser and two terminals,
    its "on top" still set. Put back when that is so; never when it is where it should be."""

    def test_out_of_place_only_under_another_programs_window_not_kept_on_top(self) -> None:
        from personalized_agent.ui.on_top import out_of_place
        self.assertTrue(out_of_place([{"ours": False, "topmost": True},      # the taskbar
                                      {"ours": False, "topmost": False}]))   # a browser
        self.assertFalse(out_of_place([{"ours": False, "topmost": True},
                                       {"ours": True, "topmost": False}]), "its own windows")
        self.assertFalse(out_of_place([]))

    def test_looked_at_with_the_screens_and_not_when_hidden(self) -> None:
        from personalized_agent.ui import on_top
        asked = []
        desk = mock.MagicMock(character_hidden=False, agent_window="avatar")
        with mock.patch.object(on_top, "back_on_top", lambda window: asked.append(window)):
            desktop_module.DesktopAgent._watch_the_screens(desk)
            desk.character_hidden = True
            desktop_module.DesktopAgent._watch_the_screens(desk)
        self.assertEqual(asked, ["avatar"])

    @unittest.skipUnless(sys.platform.startswith("win"), "Windows' order of windows")
    def test_a_window_on_top_where_it_should_be_is_left_as_it_is(self) -> None:
        import tkinter as tk
        from personalized_agent.ui.on_top import back_on_top
        root = tk.Tk()
        try:
            root.geometry("40x40+-2000+-2000")           # nowhere anyone looks
            root.attributes("-topmost", True)
            root.update()
            self.assertFalse(back_on_top(root))
        finally:
            root.destroy()
