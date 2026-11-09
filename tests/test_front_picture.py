"""A PICTURE OF THE SCREEN FOR THE MODEL (the user, 2026-11-04): taken on the press, with
its words; the same screen reused; shown to nobody. Everything here is made up."""

from __future__ import annotations

import tempfile
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401
from PIL import Image, ImageDraw
from support import TemporaryAgent

from personalized_agent.agent.front_picture import KEEP, FrontPictures, block


def _screen(text="Tide tables", clock="10:00", shade=255):
    image = Image.new("RGB", (1280, 720), (shade, shade, shade))
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 100, 900, 500), outline="black", width=4)
    for n in range(10):
        draw.text((120, 120 + n * 30), f"{text} {n}" * 8, fill="black")
    draw.text((1220, 700), clock, fill="black")
    return image


class _Config(dict):
    def lines(self, key):
        return self.get(key, [])


class PicturesTests(unittest.TestCase):

    def setUp(self) -> None:
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.folder = Path(work.name) / "front"
        self.config = _Config()
        self.image = _screen()
        self.taken, self.read = [], []
        self.front = {"application": "chrome", "title": "Tides"}

        def grab():
            self.taken.append(1)
            return self.image

        def words(_image):
            self.read.append(1)
            return "High water at noon"
        self.pictures = FrontPictures(self.config, SimpleNamespace(user_window_image=grab), self.folder,
                                      lambda: dict(self.front), read_words=words)

    def _press(self):
        self.pictures.take()
        return self.pictures.latest()

    def test_a_picture_with_its_words(self) -> None:
        shot = self._press()
        self.assertTrue(Path(shot["path"]).is_file())
        self.assertEqual(shot["words"], "High water at noon")
        self.assertEqual(self.pictures.blocks(shot)[0]["media_type"], "image/jpeg")

    def test_every_message_has_the_screen_the_second_too(self) -> None:
        """The user, 2026-11-08: "在chat里问第二句话的时候，就没有input screen了，应该也是要有的"."""
        self.pictures.take()                                 # the press that opens the chat
        first = self.pictures.for_a_message(title="Tides")
        self.assertTrue(first.get("path"))
        self.assertEqual(len(self.taken), 1, "the first: the picture of the press")
        self.front = {"application": "word", "title": "Notes"}     # looked at another window
        self.image = _screen("Meeting notes", shade=230)
        second = self.pictures.for_a_message(title="Notes")
        self.assertEqual(len(self.taken), 2, "taken again for the second")
        self.assertTrue(second.get("path"))
        self.assertNotEqual(second["path"], first["path"])
        third = self.pictures.for_a_message(title="Notes")
        self.assertEqual(len(self.taken), 3)
        self.assertEqual(third["path"], second["path"], "the same screen: the same picture")

    def test_the_same_screen_is_reused_a_clock_is_not_a_change(self) -> None:
        first = self._press()
        self.image = _screen(clock="10:01")
        second = self._press()
        self.assertEqual(second["path"], first["path"])
        self.assertEqual(len(self.read), 1, "not read again")
        self.assertEqual(len(list(self.folder.glob("*.jpg"))), 1, "not written again")

    def test_another_screen_is_another_picture(self) -> None:
        first = self._press()
        self.image = _screen(text="Budget lines for the grant")
        self.assertNotEqual(self._press()["path"], first["path"])
        self.assertEqual(len(self.read), 2)

    def test_a_password_on_the_screen_drops_it_whole(self) -> None:
        with mock.patch("personalized_agent.common.credentials_guard.hide_credentials",
                        return_value="[hidden]"):
            self.assertEqual(self._press(), {})
        self.assertEqual(list(self.folder.glob("*.jpg")) if self.folder.exists() else [], [])

    def test_switched_off_nothing_is_taken(self) -> None:
        for setting in ("listener_enabled", "listener_screenshots_enabled"):
            with self.subTest(setting):
                self.config[setting] = False
                self.assertEqual(self._press(), {})
                self.assertEqual(self.taken, [])
                del self.config[setting]

    def test_a_window_excluded_is_not_kept(self) -> None:
        self.config["listener_screen_excluded_applications"] = ["chrome"]
        self.assertEqual(self._press(), {})
        self.assertFalse(self.folder.exists())

    def test_moved_on_it_no_longer_goes(self) -> None:
        self._press()
        self.assertTrue(self.pictures.latest(title="Tides"))
        self.assertEqual(self.pictures.latest(title="Notes"), {})

    def test_a_message_does_not_wait_for_ever(self) -> None:
        import threading
        hold = threading.Event()
        self.pictures.read_words = lambda _image: hold.wait(5) and ""
        self.pictures.take()
        self.assertEqual(self.pictures.latest(wait=0.05), {}, "not checked yet: it does not go")
        hold.set()
        self.pictures.latest(wait=10)

    def test_only_the_newest_are_kept(self) -> None:
        for n in range(KEEP + 3):
            self.image = _screen(text=f"Page {n} of the report {'x' * n}", shade=255 - n * 6)
            self._press()
        self.assertEqual(len(list(self.folder.glob("*.jpg"))), KEEP)

    def test_a_picture_that_cannot_be_read_is_none(self) -> None:
        self.assertIsNone(block(self.folder / "gone.jpg"))


class ItsOwnWindowTests(unittest.TestCase):

    def test_the_users_window_is_asked_of_the_system_never_the_screen(self) -> None:
        import inspect

        from personalized_agent.agent import front_picture
        self.assertNotIn("ImageGrab", inspect.getsource(front_picture))

    def test_the_window_the_user_was_in_not_the_bar_over_it(self) -> None:
        from personalized_agent.platform_layer.windows import WindowsActivityProvider as Windows
        bar, call = (1, "a", "Firefox \u2014 Sharing Indicator"), (2, "b", "Meet - Harbour survey")
        self.assertEqual(Windows.the_window_meant([bar, call], "Meet - Harbour survey"), (2, "b"))
        self.assertEqual(Windows.the_window_meant([bar, call], "Gone by now"), (1, "a"))
        self.assertEqual(Windows.the_window_meant([bar, call], ""), (1, "a"))
        self.assertIsNone(Windows.the_window_meant([], "Meet"))

    def test_elsewhere_it_is_the_window_in_front(self) -> None:
        from personalized_agent.platform_layer.base import PlatformActivityProvider
        one = SimpleNamespace(front_window_image=lambda: "the window")
        self.assertEqual(PlatformActivityProvider.user_window_image(one), "the window")

    @unittest.skipUnless(__import__("sys").platform.startswith("win"), "Windows")
    def test_a_real_window_is_a_window_not_the_whole_of_every_screen(self) -> None:
        from personalized_agent.platform_layer import get_platform
        image = get_platform().activity.user_window_image()
        if image is not None:                # none when nothing is open
            self.assertGreaterEqual(min(image.size), 90)


class WhatTheModelIsGivenTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app

    def test_a_run_may_read_where_they_are_kept(self) -> None:
        self.assertIn("references", str(self.app.front_pictures.folder))
        self.assertTrue(str(self.app.front_pictures.folder).startswith(
            str(self.app.config.get("workspace_root"))))

    def test_the_plan_sees_the_screen_and_the_pictures_handed_over(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            one, shot = Path(work) / "chart.png", Path(work) / "screen.jpg"
            _screen().save(one)
            _screen(text="Other").save(shot)
            note = Path(work) / "notes.txt"
            note.write_text("words", encoding="utf-8")
            draft = {"files": [str(one), str(note)], "options": {"front": {"picture": str(shot)}}}
            self.assertEqual(len(self.app.drafts._pictures(draft)), 2)

    def test_the_full_agent_is_given_it_and_nothing_written_down(self) -> None:
        picture = {"media_type": "image/jpeg", "data": "aGk="}
        asked = {}

        class Seen(Exception):
            pass

        def run(text, context, debug=None):
            asked.update(context, text=text)
            raise Seen()
        written = []
        keep = self.app.agent.conversations.add_message

        def add(conversation, role, text, metadata=None):
            written.append((text, metadata))
            return keep(conversation, role, text, metadata=metadata)
        with mock.patch.object(self.app.agent, "run_instruction", side_effect=run), \
                mock.patch.object(self.app.agent.conversations, "add_message", side_effect=add):
            with self.assertRaises(Seen):
                self.app.agent.handle_message("What is this?", front="\n\n[Tides]", seen=[picture])
        self.assertEqual(asked["pictures"], [picture])
        self.assertIn("[Tides]", asked["text"])
        self.assertEqual([one[0] for one in written], ["What is this?"],
                         "what is kept is what they said")
        self.assertNotIn("aGk=", str(written))


class TakenBehindThePressTests(unittest.TestCase):
    """Where a window can be asked what it draws, nothing waits for the picture."""

    def _pictures(self, folder, activity):
        config = SimpleNamespace(get=lambda _key, default=None: default, lines=lambda _key: [])
        return FrontPictures(config, activity, Path(folder), lambda: {"title": "Tides"},
                             read_words=lambda _image: "tide table")

    def test_the_press_does_not_wait_and_no_screen_is_read(self) -> None:
        import threading
        release, asked = threading.Event(), []

        def drawn(itself_only=False, title=""):
            asked.append((itself_only, title))
            release.wait(5)
            return Image.new("RGB", (64, 64), "#336699")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            pictures = self._pictures(folder, SimpleNamespace(draws_windows=True, user_window_image=drawn))
            began = time.monotonic()
            pictures.take()
            self.assertLess(time.monotonic() - began, 0.5, "the press goes on")
            release.set()
            self.assertEqual(pictures.latest(title="Tides")["words"], "tide table")
            self.assertEqual(asked, [(True, "Tides")], "what it draws, of the window the user was in")

    def test_a_window_that_draws_nothing_leaves_no_picture(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            pictures = self._pictures(folder, SimpleNamespace(
                draws_windows=True, user_window_image=lambda itself_only=False, title="": None))
            pictures.take()
            self.assertEqual(pictures.latest(), {})


@unittest.skipUnless(sys.platform.startswith("win"), "Windows")
class WhatTheWindowDrawsTests(unittest.TestCase):
    """Nothing over a window is in its picture: not the chat, not the Delegate work box."""

    def test_a_window_over_it_is_not_in_the_picture(self) -> None:
        import ctypes
        import tkinter as tk
        from personalized_agent.platform_layer.windows import WindowsActivityProvider
        root = tk.Tk()
        root.overrideredirect(True)
        root.geometry("300x200+200+200")
        root.configure(bg="#c01020")
        over = tk.Toplevel(root)
        over.overrideredirect(True)
        over.attributes("-topmost", True)
        over.geometry("300x200+200+200")
        over.configure(bg="#10c020")
        root.update()
        over.lift()
        root.update()
        end = time.monotonic() + 0.5         # the system draws a new window in a moment
        while time.monotonic() < end:
            root.update()
            time.sleep(0.02)
        try:
            provider = WindowsActivityProvider.__new__(WindowsActivityProvider)
            provider._ctypes = ctypes
            hwnd = int(root.wm_frame(), 16)
            image = provider._window_itself(hwnd, (200, 200, 500, 400))
            self.assertIsNotNone(image)
            self.assertEqual(image.size, (300, 200))
            red, green, _blue = image.convert("RGB").getpixel((150, 100))
            self.assertGreater(red, green, "the window's own colour, not what stands over it")
        finally:
            over.destroy()
            root.destroy()
            del over, root
            import gc
            gc.collect()

    def test_a_window_that_gives_nothing_falls_back(self) -> None:
        import ctypes
        from personalized_agent.platform_layer.windows import WindowsActivityProvider
        provider = WindowsActivityProvider.__new__(WindowsActivityProvider)
        provider._ctypes = ctypes
        self.assertIsNone(provider._window_itself(0, (0, 0, 10, 10)))


if __name__ == "__main__":
    unittest.main()


class TheWholeScreenTests(unittest.TestCase):
    """The user, 2026-11-08: "能给整个屏幕吗" - the whole screen, this application's windows and
    the windows never read painted grey where they show."""

    def test_its_own_windows_and_those_never_read_are_grey_where_they_show(self) -> None:
        from personalized_agent.platform_layer.windows import BLANKED, blank_windows
        image = Image.new("RGB", (400, 300), (255, 255, 255))
        windows = [  # from the top down
            {"box": (10, 10, 60, 60), "application": "python.exe", "title": "Fern", "ours": True},
            {"box": (0, 0, 400, 300), "application": "overlay.exe", "title": "", "opaque": False},
            {"box": (150, 50, 250, 150), "application": "notes.exe", "title": "Notes",
             "opaque": True},
            {"box": (100, 0, 300, 200), "application": "KeePass.exe", "title": "Passwords",
             "opaque": True},
            {"box": (0, 0, 400, 300), "application": "chrome.exe", "title": "Tides",
             "opaque": True}]
        out = blank_windows(image, (0, 0, 400, 300), windows,
                            hidden=lambda application, title: "keepass" in application.lower())
        self.assertEqual(out.getpixel((30, 30)), BLANKED, "the avatar, this application's own")
        self.assertEqual(out.getpixel((120, 20)), BLANKED, "never read, under a see-through one")
        self.assertEqual(out.getpixel((200, 100)), (255, 255, 255), "a window over it, as it is")
        self.assertEqual(out.getpixel((350, 250)), (255, 255, 255), "the window in front, as it is")

    def test_on_another_screen_its_place_is_counted_from_that_screen(self) -> None:
        from personalized_agent.platform_layer.windows import BLANKED, blank_windows
        image = Image.new("RGB", (200, 100), (255, 255, 255))
        windows = [{"box": (1950, 10, 2000, 60), "application": "python.exe", "title": "",
                    "ours": True}]
        out = blank_windows(image, (1920, 0, 2120, 100), windows)
        self.assertEqual(out.getpixel((40, 30)), BLANKED)
        self.assertEqual(out.getpixel((150, 30)), (255, 255, 255))

    def test_taken_as_the_whole_screen_where_it_can_be_and_the_window_when_switched_off(self) -> None:
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        asked = []

        def screen(title="", hidden=None):
            asked.append(("screen", title, hidden("KeePass.exe", "Vault"), hidden("chrome", "x")))
            return _screen()

        def window(*_a, **_k):
            asked.append(("window",))
            return _screen()
        config = _Config(listener_screen_excluded_applications=["keepass"])
        pictures = FrontPictures(config, SimpleNamespace(user_screen_image=screen,
                                                         user_window_image=window),
                                 Path(work.name) / "front", lambda: {"title": "Tides"},
                                 read_words=lambda _image: "")
        pictures.take()
        self.assertTrue(pictures.latest().get("path"))
        self.assertEqual(asked, [("screen", "Tides", True, False)])
        config["front_picture_whole_screen"] = False
        asked.clear()
        pictures.take()
        pictures.latest()
        self.assertEqual(asked, [("window",)])


class ShownAtOnceTests(unittest.TestCase):
    """The user, 2026-11-08: "你等识别，你可以先把图片截图发上来" - a small copy, before its words."""

    def test_a_small_copy_before_its_words_are_read(self) -> None:
        import threading as _threading
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        reading = _threading.Event()

        def words(_image):
            reading.wait(5)                      # reading is slow: the copy is there before
            return "High water"
        pictures = FrontPictures(_Config(), SimpleNamespace(user_window_image=lambda: _screen()),
                                 Path(work.name) / "front", lambda: {"title": "Tides"},
                                 read_words=words)
        pictures.take()
        small = pictures.preview(wait=2)
        self.assertIsNotNone(small, "there before its words")
        from personalized_agent.agent.front_picture import PREVIEW_SIDE
        self.assertLessEqual(max(small.size), PREVIEW_SIDE)
        self.assertFalse(pictures._done.is_set(), "its words still being read")
        reading.set()
        self.assertTrue(pictures.latest(wait=5).get("path"))

    def test_none_of_a_window_never_read_nor_a_picture_with_a_password(self) -> None:
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        pictures = FrontPictures(_Config(listener_screen_excluded_applications=["keepass"]),
                                 SimpleNamespace(user_window_image=lambda: _screen()),
                                 Path(work.name) / "front",
                                 lambda: {"application": "KeePass", "title": "Vault"},
                                 read_words=lambda _image: "")
        pictures.take()
        self.assertIsNone(pictures.preview(wait=2))
        pictures.latest(wait=5)
        self.assertEqual(pictures.why, "the window in front is one never read")


class TheChatsIsNotReadTests(unittest.TestCase):
    """The user, 2026-11-08: "chat的这个可以跳过。不需要识别" - the chat's picture goes as taken."""

    def test_no_words_read_nor_a_password_looked_for_and_the_next_message_the_same(self) -> None:
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        read = []
        shots = iter([_screen("Tides"), _screen("Notes", shade=230)])
        pictures = FrontPictures(_Config(), SimpleNamespace(user_window_image=lambda: next(shots)),
                                 Path(work.name) / "front", lambda: {"title": "Tides"},
                                 read_words=lambda image: read.append(1) or "password: hunter2")
        pictures.take(words=False)
        first = pictures.for_a_message(title="Tides")
        self.assertTrue(first.get("path"), "sent, though what would be read holds a password")
        self.assertEqual(first.get("words"), "")
        second = pictures.for_a_message(title="Tides")
        self.assertTrue(second.get("path"))
        self.assertNotEqual(second["path"], first["path"], "taken again, as the chat's")
        self.assertEqual(read, [], "nothing read")

    def test_delegate_works_is_still_read(self) -> None:
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        pictures = FrontPictures(_Config(), SimpleNamespace(user_window_image=lambda: _screen()),
                                 Path(work.name) / "front", lambda: {"title": "Tides"},
                                 read_words=lambda image: "password: hunter2")
        pictures.take()
        self.assertEqual(pictures.latest(wait=5), {}, "a password on it: dropped")


@unittest.skipUnless(sys.platform.startswith("win"), "Windows' own way of leaving a window out")
class ThroughItsOwnWindowsTests(unittest.TestCase):
    """The user, 2026-11-08: "有一团灰色是我的chat窗口，能不能越过chat窗口截屏"."""

    def _taken(self, leaves_out):
        from personalized_agent.platform_layer import windows as win
        provider = win.WindowsActivityProvider()
        windows = [{"hwnd": 1, "box": (10, 10, 60, 60), "application": "python.exe",
                    "title": "Chat", "class": "TkTopLevel", "ours": True, "opaque": True},
                   {"hwnd": 2, "box": (100, 0, 200, 100), "application": "KeePass.exe",
                    "title": "Vault", "class": "x", "ours": False, "opaque": True},
                   {"hwnd": 3, "box": (0, 0, 400, 300), "application": "chrome.exe",
                    "title": "Tides", "class": "Chrome", "ours": False, "opaque": True}]
        asked = []

        def leave(hwnds, out):
            asked.append((list(hwnds), out))
            return list(hwnds) if leaves_out else []
        with mock.patch.object(provider, "screen_windows", return_value=windows), \
                mock.patch.object(provider, "_monitor_of", return_value=(0, 0, 400, 300)), \
                mock.patch.object(provider, "_leave_out_of_pictures", side_effect=leave), \
                mock.patch.object(win, "LEFT_OUT_SETTLE", 0), \
                mock.patch("PIL.ImageGrab.grab", return_value=Image.new("RGB", (400, 300), (255, 255, 255))):
            image = provider.user_screen_image(title="Tides",
                                               hidden=lambda app, title: "keepass" in app.lower())
        return image, asked, win.BLANKED

    def test_its_own_left_out_what_is_behind_them_taken_and_put_back(self) -> None:
        image, asked, grey = self._taken(True)
        self.assertEqual(asked, [([1], True), ([1], False)], "left out for the grab, then put back")
        self.assertEqual(image.getpixel((30, 30)), (255, 255, 255), "behind the chat, not grey")
        self.assertEqual(image.getpixel((150, 50)), grey, "a window never read: grey still")

    def test_where_the_system_will_not_its_own_are_grey_as_before(self) -> None:
        image, _asked, grey = self._taken(False)
        self.assertEqual(image.getpixel((30, 30)), grey)


@unittest.skipUnless(sys.platform.startswith("win"), "Windows' own way of leaving a window out")
class OneComeUpMeanwhileTests(unittest.TestCase):
    """The user, 2026-11-08: the first picture had the chat in it - opened as it was taken."""

    def test_the_chat_opening_while_it_is_taken_is_left_out_too(self) -> None:
        from personalized_agent.platform_layer import windows as win
        provider = win.WindowsActivityProvider()
        page = {"hwnd": 3, "box": (0, 0, 400, 300), "application": "firefox.exe",
                "title": "Tides", "class": "Mozilla", "ours": False, "opaque": True}
        avatar = {"hwnd": 1, "box": (300, 200, 340, 240), "application": "python.exe",
                  "title": "Fern", "class": "TkTopLevel", "ours": True, "opaque": True}
        chat = {"hwnd": 4, "box": (10, 10, 60, 60), "application": "python.exe",
                "title": "Chat", "class": "TkTopLevel", "ours": True, "opaque": True}
        lists = iter([[avatar, page]] + [[chat, avatar, page]] * 10)
        left, grabs = [], []

        def leave(hwnds, out):
            if out:
                left.extend(hwnds)
            return list(hwnds)

        def grab(**_k):
            grabs.append(sorted(left))
            return Image.new("RGB", (400, 300), (255, 255, 255))
        with mock.patch.object(provider, "screen_windows", side_effect=lambda: next(lists)), \
                mock.patch.object(provider, "_monitor_of", return_value=(0, 0, 400, 300)), \
                mock.patch.object(provider, "_leave_out_of_pictures", side_effect=leave), \
                mock.patch.object(win, "LEFT_OUT_SETTLE", 0), \
                mock.patch("PIL.ImageGrab.grab", side_effect=grab):
            image = provider.user_screen_image(title="Tides")
        self.assertEqual(grabs[-1], [1, 4], "taken with the chat left out as well")
        self.assertEqual(image.getpixel((30, 30)), (255, 255, 255), "not grey: behind it")
