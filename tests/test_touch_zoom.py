"""Two fingers on a touch screen zoom a window, as Ctrl + and Ctrl - do."""

from __future__ import annotations

import ctypes
import gc
import sys
import tkinter as tk
import unittest

import support  # noqa: F401

from personalized_agent.ui import touch_zoom as tz


class ThePinchTests(unittest.TestCase):

    def test_apart_is_larger_together_smaller_a_tremble_nothing(self) -> None:
        pinch = tz.Pinch()
        self.assertEqual(pinch.moved(200, True), 0, "where it begins")
        self.assertEqual(pinch.moved(205, False), 0, "a tremble")
        self.assertEqual(pinch.moved(240, False), 1)
        self.assertEqual(pinch.moved(280, False), 1, "from where the last step was")
        self.assertEqual(pinch.moved(240, False), -1)
        self.assertEqual(pinch.moved(100, True), 0, "another pinch begins afresh")

    def test_what_the_system_hands_over_is_laid_out_as_the_system_has_it(self) -> None:
        wanted = 56 if ctypes.sizeof(ctypes.c_void_p) == 8 else 48
        self.assertEqual(ctypes.sizeof(tz._GestureInfo), wanted)


@unittest.skipUnless(sys.platform.startswith("win"), "Windows' messages")
class OnAWindowTests(unittest.TestCase):

    def test_listened_to_and_let_go_with_its_window(self) -> None:
        root = tk.Tk()
        root.withdraw()
        window = tk.Toplevel(root)
        window.geometry("120x80+-3000+200")
        root.update()
        self.assertTrue(tz.listen(window, lambda _step: None))
        root.update()
        handle = int(window.wm_frame(), 16)
        # Any other message goes through as it did.
        send = ctypes.WinDLL("user32").SendMessageW
        send.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        send(handle, 0x0000, 0, 0)
        window.destroy()
        root.update()
        root.destroy()
        del window, root
        gc.collect()


class ThePageInOneWindowTests(unittest.TestCase):
    """Each double click was one more tab: its window is found and brought back."""

    def test_told_from_a_tab_of_the_same_page(self) -> None:
        from personalized_agent.platform_layer import app_window as aw
        self.assertTrue(aw.is_the_page("Chrome_WidgetWin_1", "WaterMelon"))
        self.assertFalse(aw.is_the_page("Chrome_WidgetWin_1", "WaterMelon - Google Chrome"))
        self.assertFalse(aw.is_the_page("TkTopLevel", "WaterMelon"), "the avatar's own")

    def test_open_it_is_brought_back_shut_it_is_opened_with_no_browser_a_tab(self) -> None:
        from types import SimpleNamespace
        from unittest import mock
        from pathlib import Path
        from personalized_agent.platform_layer import app_window as aw
        from personalized_agent.ui import desktop as dm
        desk = SimpleNamespace(app=SimpleNamespace(sign_in_url=lambda path: "http://here" + path,
                                                   paths=SimpleNamespace(data_dir=Path("data"))))
        with mock.patch.object(aw, "bring_back_the_page", return_value=True), \
                mock.patch.object(aw, "open_the_page") as opened, \
                mock.patch.object(dm.webbrowser, "open") as tab:
            dm.DesktopAgent._open_the_page(desk)
        opened.assert_not_called()
        tab.assert_not_called()
        with mock.patch.object(aw, "bring_back_the_page", return_value=False), \
                mock.patch.object(aw, "open_the_page", return_value=True) as opened, \
                mock.patch.object(dm.webbrowser, "open") as tab:
            dm.DesktopAgent._open_the_page(desk)
        opened.assert_called_once()
        tab.assert_not_called()
        with mock.patch.object(aw, "bring_back_the_page", return_value=False), \
                mock.patch.object(aw, "open_the_page", return_value=False), \
                mock.patch.object(dm.webbrowser, "open") as tab:
            dm.DesktopAgent._open_the_page(desk)
        tab.assert_called_once_with("http://here/#overview")

    def test_two_double_clicks_make_one_window_while_it_is_coming(self) -> None:
        import threading
        from pathlib import Path
        from types import SimpleNamespace
        from unittest import mock
        from personalized_agent.platform_layer import app_window as aw
        from personalized_agent.ui import desktop as dm
        desk = SimpleNamespace(app=SimpleNamespace(sign_in_url=lambda path: "http://here" + path,
                                                   paths=SimpleNamespace(data_dir=Path("data"))))
        coming, go_on = threading.Event(), threading.Event()

        def still_coming() -> None:
            coming.set()
            go_on.wait(5)
        with mock.patch.object(aw, "bring_back_the_page", return_value=False), \
                mock.patch.object(aw, "open_the_page", return_value=True) as opened, \
                mock.patch.object(aw, "wait_for_the_page", side_effect=still_coming), \
                mock.patch.object(dm.webbrowser, "open") as tab:
            first = threading.Thread(target=dm.DesktopAgent._open_the_page, args=(desk,))
            first.start()
            self.assertTrue(coming.wait(5))
            dm.DesktopAgent._open_the_page(desk)
            self.assertEqual(opened.call_count, 1, "the second finds it on its way")
            go_on.set()
            first.join(5)
            dm.DesktopAgent._open_the_page(desk)
            self.assertEqual(opened.call_count, 2, "shut again later, it is opened again")
        tab.assert_not_called()
        self.assertFalse(aw.OPENING.locked())

    def test_it_is_waited_for_until_it_is_there_and_no_longer(self) -> None:
        from unittest import mock
        from personalized_agent.platform_layer import app_window as aw
        with mock.patch.dict(aw.os.environ, {"PA_NO_DIALOGS": ""}), \
                mock.patch.object(aw.sys, "platform", "win32"), \
                mock.patch.object(aw.time, "sleep"), \
                mock.patch.object(aw, "_windows_page", side_effect=[None, None, 7]) as looked:
            aw.wait_for_the_page()
        self.assertEqual(looked.call_count, 3)
        ticks = iter([0.0, 1.0, aw.OPENING_SECONDS + 1])
        with mock.patch.dict(aw.os.environ, {"PA_NO_DIALOGS": ""}), \
                mock.patch.object(aw.sys, "platform", "win32"), \
                mock.patch.object(aw.time, "sleep"), \
                mock.patch.object(aw.time, "monotonic", side_effect=lambda: next(ticks)), \
                mock.patch.object(aw, "_windows_page", return_value=None) as looked:
            aw.wait_for_the_page()
        self.assertEqual(looked.call_count, 1, "a window that never comes does not hold the next")

    def test_chrome_first_and_never_signed_in_or_synced(self) -> None:
        from pathlib import Path
        from unittest import mock
        from personalized_agent.platform_layer import app_window as aw
        with mock.patch.dict(aw.os.environ, {"ProgramFiles(x86)": "A", "ProgramFiles": "B",
                                             "LOCALAPPDATA": "C"}):
            names = [one.name for one in aw._windows_candidates()]
        self.assertEqual(names, ["chrome.exe"] * 3 + ["msedge.exe"] * 3)
        said = aw.command("browser", "http://here/", Path("p"))
        self.assertIn("--disable-sync", said)
        self.assertIn("--disable-features=msImplicitSignin", said)

    def test_nothing_is_opened_under_the_tests(self) -> None:
        from personalized_agent.platform_layer import app_window as aw
        self.assertFalse(aw.bring_back_the_page())
        self.assertFalse(aw.open_the_page("http://here/", "nowhere"))


class TheAgentKnowsItsOwnPageTests(unittest.TestCase):
    """Its title became WaterMelon, and its page would have been read as the user's day."""

    def test_by_its_name_alone_or_with_its_browsers(self) -> None:
        from personalized_agent.version import APP_NAME, names_the_agent
        for title in ("WaterMelon", "WaterMelon - Google Chrome", "WaterMelon \u2014 Mozilla Firefox",
                      f"Conversation - {APP_NAME}"):
            self.assertTrue(names_the_agent("Browser", title), title)

    def test_its_pages_on_this_machine_with_their_address(self) -> None:
        from personalized_agent.version import APP_NAME, names_the_agent
        self.assertTrue(names_the_agent(
            "firefox.exe", f"http://127.0.0.1:8756 - Delegate work - {APP_NAME} \u2014 Mozilla Firefox"))
        self.assertTrue(names_the_agent(f"{APP_NAME}.exe", "Anything"))

    def test_never_what_only_holds_its_name(self) -> None:
        from personalized_agent.version import APP_NAME, names_the_agent
        for title in ("Meet - Personalized Agent \u2014 Mozilla Firefox",
                      "Personalized Agent - Google Docs", f"Notes on {APP_NAME} - Google Docs",
                      f"{APP_NAME} - Google Search - Google Chrome",
                      "fhbzc/Personalized_agent \u2014 Mozilla Firefox"):
            self.assertFalse(names_the_agent("firefox.exe", title), title)

    def test_never_a_page_about_the_fruit(self) -> None:
        from personalized_agent.version import names_the_agent
        for title in ("Watermelon - Wikipedia - Google Chrome", "watermelon recipes",
                      "How to grow WaterMelon at home", "Watermelon - Google Chrome"):
            self.assertFalse(names_the_agent("Browser", title), title)


class OnAMacTests(unittest.TestCase):

    def test_the_page_goes_to_webkit_whatever_the_browser_on_the_main_thread(self) -> None:
        import queue
        from types import SimpleNamespace
        from unittest import mock
        from personalized_agent.ui import desktop as dm, mac_panel
        desk = SimpleNamespace(queue=queue.Queue(), _open_the_page_on_a_mac="on the main thread")
        with mock.patch.object(dm.sys, "platform", "darwin"), \
                mock.patch.object(mac_panel, "available", return_value=True):
            dm.DesktopAgent._open_the_page(desk)
        self.assertEqual(desk.queue.get_nowait(), ("call", "on the main thread"))

    def test_open_it_comes_back_shut_it_opens_and_failing_a_browser(self) -> None:
        from types import SimpleNamespace
        from unittest import mock
        from personalized_agent.ui import desktop as dm, mac_panel
        desk = SimpleNamespace(app=SimpleNamespace(sign_in_url=lambda path: "http://here" + path))
        with mock.patch.object(mac_panel, "bring_back_the_page", return_value=True), \
                mock.patch.object(mac_panel, "open_the_page") as opened:
            dm.DesktopAgent._open_the_page_on_a_mac(desk)
        opened.assert_not_called()
        with mock.patch.object(mac_panel, "bring_back_the_page", return_value=False), \
                mock.patch.object(mac_panel, "open_the_page", return_value=False), \
                mock.patch.object(dm.webbrowser, "open") as tab:
            dm.DesktopAgent._open_the_page_on_a_mac(desk)
        tab.assert_called_once_with("http://here/#overview")

    def test_its_zoom_keeps_within_reason(self) -> None:
        from personalized_agent.ui import mac_panel
        self.assertEqual(mac_panel.zoomed(1.0, "="), 1.1)
        self.assertEqual(mac_panel.zoomed(1.0, "-"), 0.9)
        self.assertEqual(mac_panel.zoomed(1.7, "0"), 1.0)
        self.assertEqual(mac_panel.zoomed(0.5, "-"), 0.5)
        self.assertEqual(mac_panel.zoomed(3.0, "+"), 3.0)


if __name__ == "__main__":
    unittest.main()
