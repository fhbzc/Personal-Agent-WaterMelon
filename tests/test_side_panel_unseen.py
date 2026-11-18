"""The side panel seen through when the agent takes a picture of the screen (the user, 2026-11-16: "要注意chat现在截图
的时候，忽视/穿透 sidebar"), and only then ("只是自己截图看不到，其它是可以的"): left out of the agent's own picture by
its own process a moment, in every other one; never taken for the window in front."""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

from support import TemporaryAgent

from personalized_agent.agent import front_screen


class NotTheWindowInFrontTests(unittest.TestCase):
    def test_the_window_in_front_is_the_one_behind_the_panel(self) -> None:
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        db = agent.app.db
        agent.app.config.set("listener_enabled", True)
        rows = [("2026-11-16T10:00:00+00:00", "Firefox", {"pid": 4242, "window_title": "Seed club - Mozilla Firefox"}),
                ("2026-11-16T10:00:05+00:00", "msedgewebview2",
                 {"pid": 5151, "window_title": "Waiting notifications - Personal Agent WaterMelon"})]
        for n, (at, application, data) in enumerate(rows):
            db.execute("INSERT INTO raw_events (id, event_type, source, timestamp, application, data) "
                       "VALUES (?, 'application.focused', 'test', ?, ?, ?)",
                       (f"e{n}", at, application, json.dumps(data)))
        said = front_screen.front_now(db, agent.app.config)
        self.assertEqual(said.get("title"), "Seed club - Mozilla Firefox")


@unittest.skipUnless(sys.platform == "win32", "Windows' own")
class LeftOutOnlyWhileTakenTests(unittest.TestCase):
    def test_left_out_when_said_and_back_in_every_picture_after(self) -> None:
        import ctypes
        import tkinter as tk
        from ctypes import wintypes

        from personalized_agent.platform_layer import panel_host
        title = f"Panel unseen test {os.getpid()}"
        root = tk.Tk()
        self.addCleanup(root.destroy)
        root.title(title)
        root.geometry("120x80+-3000+0")
        root.update()
        handle = ctypes.windll.user32.FindWindowW(None, title)

        def affinity() -> int:
            said = wintypes.DWORD()
            ctypes.windll.user32.GetWindowDisplayAffinity(handle, ctypes.byref(said))
            return said.value
        self.assertEqual(affinity(), 0, "seen in every picture until it is said")
        self.assertEqual(panel_host.leave_out_of_pictures(True, title), 1)
        self.assertEqual(affinity(), panel_host.WDA_EXCLUDEFROMCAPTURE)
        self.assertEqual(panel_host.leave_out_of_pictures(False, title), 1)
        self.assertEqual(affinity(), 0, "the user's own screenshots have it again")

    def test_none_of_that_title_none_left_out(self) -> None:
        from personalized_agent.platform_layer import panel_host
        self.assertEqual(panel_host.leave_out_of_pictures(True, "No window is called this"), 0)

    def test_the_agents_picture_asks_the_panels_process_and_puts_it_back(self) -> None:
        from PIL import Image

        from personalized_agent.platform_layer import windows as win
        provider = win.WindowsActivityProvider()
        panel = {"hwnd": 7, "pid": 5151, "box": (300, 0, 400, 300), "application": "msedgewebview2.exe",
                 "title": "Waiting notifications - Personal Agent WaterMelon", "class": "Chrome_WidgetWin_1",
                 "ours": True, "opaque": True}
        page = {"hwnd": 3, "pid": 4242, "box": (0, 0, 400, 300), "application": "firefox.exe",
                "title": "Tides", "class": "Mozilla", "ours": False, "opaque": True}
        said, grabs = [], []

        def grab(**_k):
            grabs.append(list(said))
            return Image.new("RGB", (400, 300), (255, 255, 255))
        with mock.patch.dict(win.OTHER_OWN_PROCESSES, {5151: lambda out: said.append(out) or True}, clear=True), \
                mock.patch.object(provider, "screen_windows", return_value=[panel, page]), \
                mock.patch.object(provider, "_monitor_of", return_value=(0, 0, 400, 300)), \
                mock.patch.object(provider, "_leave_out_of_pictures", return_value=[]), \
                mock.patch.object(win, "LEFT_OUT_SETTLE", 0), \
                mock.patch("PIL.ImageGrab.grab", side_effect=grab):
            image = provider.user_screen_image(title="")
        self.assertEqual(grabs, [[True]], "taken while the panel was left out")
        self.assertEqual(said, [True, False], "and back in every other picture after")
        self.assertEqual(image.getpixel((350, 150)), (255, 255, 255), "what is behind it, not grey")


if __name__ == "__main__":
    unittest.main()
