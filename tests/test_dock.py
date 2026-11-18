"""The side panel docked: the other windows make room (the user, 2026-11-16: "其它控件能自动左缩放 ... 这个缩放要快";
of the windows placed by hand, "只有重叠才缩")."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import ROOT  # noqa: F401 - puts the repository on the path

from personalized_agent.platform_layer import dock as dk

SCREEN = (0, 0, 1920, 1040)               # a work area above the taskbar
STRIP = (1460, 0, 1920, 1040)              # the panel's, 460 wide


class WhereTheyGoTests(unittest.TestCase):
    def test_one_clear_of_the_strip_is_left_as_it_is(self) -> None:
        self.assertIsNone(dk.room_for((100, 100, 1400, 900), STRIP, (0, 0, 1460, 1040)))
        self.assertIsNone(dk.room_for((100, 100, 1460, 900), STRIP, (0, 0, 1460, 1040)), "touching is not over")

    def test_one_it_would_cover_is_made_narrower_to_stand_beside_it(self) -> None:
        self.assertEqual(dk.room_for((200, 100, 1700, 900), STRIP, (0, 0, 1460, 1040)), (200, 100, 1460, 900))

    def test_one_that_would_be_too_narrow_is_moved_left_as_wide_as_it_was(self) -> None:
        self.assertEqual(dk.room_for((1300, 100, 1900, 900), STRIP, (0, 0, 1460, 1040)), (860, 100, 1460, 900))

    def test_one_wider_than_the_room_left_takes_all_of_it(self) -> None:
        self.assertEqual(dk.room_for((1200, 0, 1900 + 1500, 600), STRIP, (0, 0, 1460, 1040)), (0, 0, 1460, 600))

    def test_nothing_above_or_below_the_strip_is_moved(self) -> None:
        self.assertIsNone(dk.room_for((1500, 1100, 1800, 1300), STRIP, (0, 0, 1460, 1040)))

    def test_full_screen_is_left_alone(self) -> None:
        monitor = (0, 0, 1920, 1080)
        self.assertTrue(dk.full_screen((0, 0, 1920, 1080), monitor))
        self.assertTrue(dk.full_screen((-8, -8, 1928, 1088), monitor), "its border off the screen")
        self.assertFalse(dk.full_screen((0, 0, 1920, 1040), monitor), "maximized above the taskbar")


class PictureBesideTheStripTests(unittest.TestCase):
    def test_pinned_the_picture_is_of_the_screen_left_of_its_strip(self) -> None:
        """2026-11-17: "pin的时候，左边就是整个屏幕，如果是unpin的，那就是完整屏幕截屏"."""
        from personalized_agent.platform_layer import windows as wl
        monitor = (0, 0, 1920, 1080)
        strip = [None]
        with mock.patch.object(wl, "SIDE_STRIP", [lambda: strip[0]]):
            self.assertEqual(wl.without_the_strip(monitor), monitor, "not pinned: the whole screen")
            strip[0] = (1286, 0, 1920, 1032)
            self.assertEqual(wl.without_the_strip(monitor), (0, 0, 1286, 1080))
            self.assertEqual(wl.without_the_strip((-1920, 0, 0, 1080)), (-1920, 0, 0, 1080), "another screen: whole")


class PointerElsewhereTests(unittest.TestCase):
    def test_on_the_agents_own_windows_the_last_place_off_them(self) -> None:
        """2026-11-17: "你之前的鼠标位置现在chat里没有了" - sent from the panel, the pointer was on the panel."""
        from collections import deque

        from personalized_agent.platform_layer import windows as wl
        panel = (1286, 0, 1920, 1032)
        with mock.patch.object(wl, "POINTER_TRAIL", deque([(300, 400), (900, 500), (1500, 600)], maxlen=10)):
            self.assertEqual(wl.pointer_elsewhere((700, 200), [panel]), (700, 200), "off them: where it is")
            self.assertEqual(wl.pointer_elsewhere((1600, 900), [panel]), (900, 500), "on the panel: where it was")
            self.assertIsNone(wl.pointer_elsewhere((1600, 900), [panel, (0, 0, 1286, 1032)]), "never off them")
            self.assertEqual(wl.pointer_elsewhere(None, [panel]), (900, 500))


class PutBackTests(unittest.TestCase):
    def test_put_back_only_where_it_still_stands_where_it_was_put(self) -> None:
        moved = {1: ((200, 100, 1700, 900), (200, 100, 1460, 900)),
                 2: ((1300, 100, 1900, 900), (860, 100, 1460, 900)),
                 3: ((10, 10, 1600, 500), (10, 10, 1460, 500))}
        now = {1: (200, 100, 1460, 900), 2: (50, 50, 650, 850)}      # 2 moved by the user since; 3 closed
        self.assertEqual(dk.put_back(moved, now), {1: (200, 100, 1700, 900)})


class OnlyOnWindowsTests(unittest.TestCase):
    def test_elsewhere_nothing_is_taken_and_nothing_breaks(self) -> None:
        with mock.patch.object(dk.Dock, "usable", staticmethod(lambda: False)):
            dock = dk.Dock()
            self.assertIsNone(dock.dock(123, 460))
            dock.undock()
            self.assertFalse(dock.docked)

    def test_a_strip_left_taken_is_given_back_and_forgotten(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            kept = Path(folder) / "side_panel_dock.json"
            kept.write_text(json.dumps({"handle": 4242, "strip": list(STRIP),
                                        "moved": {"7": [[200, 100, 1700, 900], [200, 100, 1460, 900]]}}),
                            encoding="utf-8")
            removed, put = [], []
            with mock.patch.object(dk.Dock, "usable", staticmethod(lambda: True)), \
                    mock.patch.object(dk.Dock, "_remove", staticmethod(removed.append)), \
                    mock.patch.object(dk.Dock, "_put_back", lambda self, moved: put.append(moved)):
                dk.Dock(kept).give_back_left_over()
            self.assertEqual(removed, [4242])
            self.assertEqual(put, [{7: ((200, 100, 1700, 900), (200, 100, 1460, 900))}])
            self.assertFalse(kept.exists())


@unittest.skipUnless(sys.platform == "win32", "Windows' own")
class TheDesktopUsesItTests(unittest.TestCase):
    def test_the_panel_takes_its_room_and_gives_it_back(self) -> None:
        import inspect

        from personalized_agent.ui.desktop import DesktopAgent
        opened = inspect.getsource(DesktopAgent.open_notifications)
        self.assertIn("dock=dock", opened)
        hidden = inspect.getsource(DesktopAgent.hide_notes_panel)
        self.assertIn('dock = getattr(self, "_dock", None)', hidden)
        self.assertIn("slide_out_titled(self._notes_titles(), dock=dock)", hidden, "hidden, its room given back")
        self.assertIn("_show_side_tab", hidden, "and its tab alone left at the screen's edge")
        self.assertIn("dock.undock", inspect.getsource(DesktopAgent._watch_notes))
        self.assertIn("_give_the_room_back", inspect.getsource(DesktopAgent.quit))
        self.assertIn("give_back_left_over", inspect.getsource(DesktopAgent.run))

    def test_pinned_and_shown_without_its_strip_it_is_taken_again(self) -> None:
        """2026-11-17: "他是pined, 但是莫名其妙又不是置顶的了" - shown, pinned, its strip given back unseen."""
        from types import SimpleNamespace

        from personalized_agent.platform_layer import app_window
        from personalized_agent.ui.desktop import DesktopAgent
        dock = SimpleNamespace(docked=False, stepped_aside=False)
        ran = []
        desk = SimpleNamespace(REDOCK_SECONDS=DesktopAgent.REDOCK_SECONDS, _quiet_opened_at=None, _over_film=False,
                               _notes_titles=lambda: {"Side"}, _off_this_thread=lambda what, name: ran.append(what))
        with mock.patch.object(DesktopAgent, "_side_dock", lambda self: dock), \
                mock.patch.object(DesktopAgent, "_film_in_front", lambda self: False), \
                mock.patch.object(DesktopAgent, "_notes_width", lambda self: 400), \
                mock.patch.object(app_window, "dock_titled", return_value=True) as docked:
            DesktopAgent._keep_docked(desk)
            self.assertEqual(len(ran), 1)
            ran[0]()
            docked.assert_called_once_with({"Side"}, 400, dock)
            DesktopAgent._keep_docked(desk)
            self.assertEqual(len(ran), 1, "not tried again at once")
            desk._redocked_at = 0.0
            desk._quiet_opened_at = 123.0
            DesktopAgent._keep_docked(desk)
            self.assertEqual(len(ran), 1, "a notification's opening stays over the windows")
            desk._quiet_opened_at = None
            dock.docked = True
            DesktopAgent._keep_docked(desk)
            self.assertEqual(len(ran), 1, "in its strip already")

    def test_a_notifications_panel_pressed_on_is_pinned_in_its_strip(self) -> None:
        """2026-11-17: "在Pin的情况下，居然进入了pin的位置" - opened by a notification over the windows, it stayed so for
        good once pressed on, and the windows came over its strip."""
        from types import SimpleNamespace

        from personalized_agent.platform_layer import app_window
        from personalized_agent.ui.desktop import DesktopAgent
        dock = SimpleNamespace(docked=False, stepped_aside=False)
        ran = []
        desk = SimpleNamespace(REDOCK_SECONDS=DesktopAgent.REDOCK_SECONDS, _quiet_opened_at=100.0, _over_film=False,
                               app=SimpleNamespace(side_touched_at=50.0),
                               _notes_titles=lambda: {"Side"}, _off_this_thread=lambda what, name: ran.append(what))
        with mock.patch.object(DesktopAgent, "_side_dock", lambda self: dock),                 mock.patch.object(DesktopAgent, "_film_in_front", lambda self: False),                 mock.patch.object(DesktopAgent, "_notes_width", lambda self: 400),                 mock.patch.object(app_window, "dock_titled", return_value=True):
            DesktopAgent._keep_docked(desk)
            self.assertEqual(ran, [], "untouched since: over the windows still")
            desk.app.side_touched_at = 150.0
            DesktopAgent._keep_docked(desk)
            self.assertEqual(len(ran), 1, "pressed on: in its strip")
            self.assertIsNone(desk._quiet_opened_at)

    def test_not_pinned_its_tab_while_a_window_is_over_it(self) -> None:
        """2026-11-17: "取消pin之后，expand按钮缩放后也消失了，这个按钮不应该消失"."""
        from types import SimpleNamespace

        from personalized_agent.platform_layer import app_window
        from personalized_agent.ui.desktop import DesktopAgent
        said = []
        pinned = {"side_panel_pinned": False}
        desk = SimpleNamespace(config=SimpleNamespace(get=lambda key, default=None: pinned.get(key, default)),
                               _notes_titles=lambda: {"Side"}, _side_tab_on=False)
        with mock.patch.object(DesktopAgent, "_show_side_tab", lambda self: said.append("shown")),                 mock.patch.object(DesktopAgent, "_hide_side_tab", lambda self: said.append("away")),                 mock.patch.object(app_window, "covered_titled", return_value=True) as covered:
            DesktopAgent._tab_when_covered(desk)
            DesktopAgent._tab_when_covered(desk)
            self.assertEqual(said, ["shown"], "covered: its tab, once")
            covered.return_value = False
            DesktopAgent._tab_when_covered(desk)
            self.assertEqual(said, ["shown", "away"], "in front again: the tab away")
            pinned["side_panel_pinned"] = True
            covered.return_value = True
            DesktopAgent._tab_when_covered(desk)
            self.assertEqual(said, ["shown", "away"], "pinned: above them all, no tab")

    def test_untouched_it_is_put_away_again_pinned_too(self) -> None:
        from types import SimpleNamespace

        from personalized_agent.ui.desktop import DesktopAgent
        hidden = []
        desk = SimpleNamespace(_quiet_opened_at=100.0, app=SimpleNamespace(side_touched_at=0.0),
                               config=SimpleNamespace(get=lambda key, default=None: True if key == "side_panel_pinned" else default),
                               hide_notes_panel=lambda: hidden.append(1))
        DesktopAgent._put_away_untouched(desk, 100.0)
        self.assertEqual(hidden, [1], "pinned or not - nothing took its strip for it")
        desk._quiet_opened_at, desk.app.side_touched_at = 200.0, 250.0
        DesktopAgent._put_away_untouched(desk, 200.0)
        self.assertEqual(hidden, [1], "pressed on: the user's, left")

    def test_its_tab_as_large_as_the_panels_own(self) -> None:
        """2026-11-17: "collapse和expand还是不一样大小" - WebView2 drawn by Windows' text size too."""
        import winreg
        from types import SimpleNamespace

        from personalized_agent.ui.desktop import DesktopAgent

        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False
        for said, scale in ((179, 1.79), (100, 1.0), (500, 2.25)):
            with mock.patch.object(winreg, "OpenKey", lambda *a: Key()), \
                    mock.patch.object(winreg, "QueryValueEx", lambda key, name: (said, 4)):
                self.assertAlmostEqual(DesktopAgent._text_scale(), scale)
        with mock.patch.object(winreg, "OpenKey", side_effect=OSError):
            self.assertEqual(DesktopAgent._text_scale(), 1.0, "not set")
        desk = SimpleNamespace()
        with mock.patch.object(DesktopAgent, "_screen_scale", lambda self: 1.5), \
                mock.patch.object(DesktopAgent, "_text_scale", staticmethod(lambda: 1.2)):
            self.assertAlmostEqual(DesktopAgent._side_tab_scale(desk), 1.8)

    def test_pinned_its_keys_go_to_its_chat(self) -> None:
        """2026-11-17: "当我sidebar pin的时候，打开sidebar的快捷键的功能变成焦点切换到sidebar的chat上" - "包括chat的快捷键也是"."""
        from types import SimpleNamespace

        from personalized_agent.ui.desktop import DesktopAgent
        did, told = [], []
        config = {"side_panel_pinned": True}
        shown = [True]
        desk = SimpleNamespace(config=SimpleNamespace(get=lambda key, default=None: config.get(key, default)),
                               _notes_open_now=lambda: shown[0], hide_notes_panel=lambda: did.append("hidden"),
                               open_notifications=lambda: did.append("opened"),
                               _off_this_thread=lambda what, name="": what(), KEYS_AFTER_OPEN_MS=0,
                               root=SimpleNamespace(after=lambda ms, what: what()))
        with mock.patch.object(DesktopAgent, "_keys_to_the_side_chat", lambda self: did.append("keys")), \
                mock.patch.object(DesktopAgent, "_tell_the_panel",
                                  lambda self, peeking, by_user=False, focus=False: told.append(focus)):
            DesktopAgent._side_panel_in_or_out(desk)
            self.assertEqual(did, ["keys"], "pinned and shown: the keys to its chat, not put away")
            shown[0] = False
            DesktopAgent._side_panel_in_or_out(desk)
            self.assertEqual(did[-2:], ["opened", "keys"], "put away: back, the keys in its box")
            config["side_panel_pinned"], shown[0] = False, True
            DesktopAgent._side_panel_in_or_out(desk)
            self.assertEqual(did[-1], "hidden", "not pinned: put away, as before")

    def test_no_line_beside_the_avatar_where_the_panel_is(self) -> None:
        """2026-11-17: "logo旁边的1 recommendation这种提示直接取消"."""
        from types import SimpleNamespace

        from personalized_agent.ui.desktop import DesktopAgent
        hidden = []
        desk = SimpleNamespace(
            _answering={}, _blocked=lambda: False, _bubble_queue=[], _quick_panels={}, _waiting_line=None,
            app=SimpleNamespace(notifications=SimpleNamespace(waiting_by_category=lambda: {"Recommended content": 1})),
            _hide_waiting=lambda: hidden.append(True))
        with mock.patch.object(DesktopAgent, "_side_panel_here", lambda self: True):
            DesktopAgent._show_waiting(desk)
        self.assertEqual(hidden, [True])
        self.assertIsNone(desk._waiting_line, "nothing drawn")

    def test_its_tab_is_there_from_the_start(self) -> None:
        """2026-11-17: "agent启动之后就要有凸起的按钮让我可以expand, 而不是我第一次展开之后才有"."""
        import inspect
        import os
        from types import SimpleNamespace

        from personalized_agent.ui.desktop import DesktopAgent
        self.assertIn("_side_tab_at_start", inspect.getsource(DesktopAgent.run))
        shown = []
        desk = SimpleNamespace(_notes_open_now=lambda: False)
        with mock.patch.dict(os.environ, {"PA_NO_DIALOGS": ""}), \
                mock.patch.object(DesktopAgent, "_show_side_tab", lambda self: shown.append(self._side_tab_on)):
            DesktopAgent._side_tab_at_start(desk)
            self.assertEqual(shown, [True])
            desk._notes_open_now = lambda: True
            DesktopAgent._side_tab_at_start(desk)
            self.assertEqual(shown, [True], "the panel open already: no tab")

    def test_it_takes_room_only_when_the_user_pinned_it(self) -> None:
        """2026-11-16: "所有notification弹出现在都是不缩放，只有user主动选择固定在右边，才是缩放的view"."""
        from types import SimpleNamespace

        from personalized_agent.ui.desktop import DesktopAgent
        self.assertIsNone(DesktopAgent._side_dock(SimpleNamespace(config={})), "by default over the others")
        self.assertIsNone(DesktopAgent._side_dock(SimpleNamespace(config={"side_panel_pinned": False})))
        pinned = SimpleNamespace(config={"side_panel_pinned": True},
                                 app=SimpleNamespace(paths=SimpleNamespace(data_dir=Path("."))))
        self.assertIsNotNone(DesktopAgent._side_dock(pinned))


class AboveTheOthersWhilePinnedTests(unittest.TestCase):
    """The user, 2026-11-16: "windows上显示桌面，即使右边pin了也会被最小化，不应该这样" - above the others, as the
    taskbar is; and (2026-11-17: "pin的时候如果点了全屏，没有浏览器extension加持下，就自动覆盖Pin的内容，这样取消全屏后又能看到
    pin") covered by a window filling the screen till it is over."""

    def _watched(self, fronts, kept_over=0):
        import threading
        import time
        fronts = iter(list(fronts) + [0] * 400)
        said, done = [], threading.Event()

        def on_top(handle, on):
            said.append(("top", handle, on))
            if on:
                done.set()

        def behind(handle, front):
            said.append(("behind", handle, front))
        place = dk.Dock()
        place.kept_over = kept_over
        with mock.patch.object(dk, "FULL_SCREEN_LOOK", 0.01),                 mock.patch.object(dk, "_film_front", side_effect=lambda *_a, **_k: next(fronts)),                 mock.patch.object(dk, "_on_top", side_effect=on_top), mock.patch.object(dk, "_behind", side_effect=behind):
            place._watch(7, (0, 0, 1920, 1080))
            done.wait(1)
            time.sleep(0.1)
            place._watch_stop.set()
            time.sleep(0.05)
        return said, place

    def test_a_full_screen_covers_it_and_it_is_above_again_after(self) -> None:
        said, place = self._watched([0, 99, 99, 99, 0])
        self.assertEqual(said, [("top", 7, False), ("behind", 7, 99), ("top", 7, True)],
                         "behind the film once, nothing moved; above again when it is over")
        self.assertFalse(place.stepped_aside)

    def test_the_film_it_was_opened_over_by_the_user_has_it_stay_above(self) -> None:
        said, place = self._watched([99, 99, 0, 99, 0], kept_over=99)
        self.assertEqual(said[:2], [("top", 7, False), ("behind", 7, 99)],
                         "above that one till it is over - the next full screen covers it again")
        self.assertEqual(place.kept_over, 0)

    def test_nothing_is_pushed_to_make_room_any_more(self) -> None:
        self.assertFalse(hasattr(dk.Dock, "_squeeze"))


@unittest.skipUnless(sys.platform == "win32", "Windows' own")
class ItsStripKeptTests(unittest.TestCase):
    """The user, 2026-11-16: "sidebar没有出现在最右边，而是在偏左的位置" - a window taken for full screen kept it out of
    the strip it held, and it was placed by the work area the strip had narrowed."""

    def _docked(self, holds: bool):
        from personalized_agent.platform_layer import app_window
        place = dk.Dock()
        if holds:
            place.handle, place.strip = 7, (1600, 0, 1920, 1032)
        with mock.patch.object(app_window, "_per_monitor"),                 mock.patch.object(dk, "_pointer_screen", return_value=((0, 0, 1920, 1080), (0, 0, 1600, 1032))),                 mock.patch.object(dk, "_something_full_screen", return_value=True):
            return place._dock(7, 320)

    def test_held_it_goes_back_into_its_strip(self) -> None:
        self.assertEqual(self._docked(True), (1600, 0, 1920, 1032))

    def test_not_held_none_is_taken_while_a_film_plays(self) -> None:
        self.assertIsNone(self._docked(False))


if __name__ == "__main__":
    unittest.main()
