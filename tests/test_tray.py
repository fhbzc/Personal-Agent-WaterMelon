"""The agent's icon beside the clock: its face, its menu, and that nothing
in it touches a window from the tray's own thread."""

from __future__ import annotations

import glob
import queue
import unittest
import unittest.mock
from pathlib import Path

from support import ROOT

from personalized_agent.ui import tray as tray_module
from personalized_agent.ui.tray import Tray, icon_image

DEFAULT = Path(ROOT) / "personalized_agent" / "resources" / "characters" / "watermelon"


class _Characters:
    def get(self, name):
        if name != "watermelon":
            return None
        return {"frames": {"idle": sorted(glob.glob(str(DEFAULT / "idle" / "*.png")))}}


class _Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class _Desktop:
    def __init__(self, **config):
        self.queue = queue.Queue()
        self.config = _Config(config)
        self.app = type("App", (), {"characters": _Characters()})()
        self.ran = []
        self.agent_window = type("W", (), {"state": lambda s: "normal"})()

    def open_url(self, fragment="/"):
        self.ran.append(("open", fragment))

    def open_chat(self):
        self.ran.append(("chat",))

    def toggle_character(self):
        self.ran.append(("character",))

    def _open_workspace(self):
        self.ran.append(("workspace",))

    def quit(self):
        self.ran.append(("quit",))


@unittest.skipUnless(tray_module.TRAY_AVAILABLE, "pystray is not installed")
class TheTrayIconTests(unittest.TestCase):

    def test_its_face_is_the_characters(self) -> None:
        from PIL import Image

        # A character that has gone falls back to the default one's face.
        image = icon_image(_Characters(), "someone-gone")
        self.assertEqual(image.size, (64, 64))
        face = Image.open(sorted(glob.glob(str(DEFAULT / "idle" / "*.png")))[0])
        # Cut to the figure, as large as other programs' icons.
        from personalized_agent.ui.tray import fitted
        expected = fitted(face.convert("RGBA"), 64)
        self.assertEqual(image.tobytes(), expected.tobytes())
        self.assertGreater(image.getbbox()[2] - image.getbbox()[0], 60,
                           "the figure fills the icon")

    def test_with_no_character_it_is_a_disc(self) -> None:
        class Nothing:
            def get(self, name):
                return None
        image = icon_image(Nothing(), "default")
        self.assertEqual(image.getpixel((32, 32)), (61, 107, 229, 255))
        self.assertEqual(image.getpixel((1, 1))[3], 0)

    def test_every_item_is_handed_to_the_window_thread(self) -> None:
        desktop = _Desktop()
        menu = Tray(desktop)._menu()
        import pystray
        items = [item for item in menu.items
                 if item is not pystray.Menu.SEPARATOR]
        # The same four as the avatar's menu.
        self.assertEqual([item.text for item in items],
                         ["Chat", "Overview", "Hide avatar", "Quit"])
        self.assertTrue(items[1].default)       # what a click on the icon does
        for item in items:
            item(None)
        # Nothing ran on the tray's thread: all of it is queued.
        self.assertEqual(desktop.ran, [])
        while not desktop.queue.empty():
            kind, work = desktop.queue.get()
            self.assertEqual(kind, "call")
            work()
        self.assertEqual(desktop.ran, [("chat",), ("open", "/#overview"), ("character",),
                                       ("quit",)])
        # Hidden, it offers to show it again.
        desktop.agent_window = type("W", (), {"state": lambda s: "withdrawn"})()
        self.assertEqual(items[2].text, "Show avatar")

    def test_the_checks_read_the_settings(self) -> None:
        tray = Tray(_Desktop())
        self.assertTrue(tray._character_shown())

    def test_turned_off_it_is_not_shown(self) -> None:
        self.assertFalse(Tray(_Desktop(tray_icon_enabled=False)).start())


class WithoutPystrayTests(unittest.TestCase):

    def test_the_agent_runs_on_without_it(self) -> None:
        with unittest.mock.patch.object(tray_module, "TRAY_AVAILABLE", False):
            tray = Tray(_Desktop())
            self.assertFalse(tray.start())
            tray.refresh()
            tray.stop()
        self.assertIsNone(tray.icon)
