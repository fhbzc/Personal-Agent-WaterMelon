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
