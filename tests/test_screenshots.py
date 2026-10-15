"""Pictures of windows that hold more than their text - decided by measuring,
never by a model - and kept only while the user's work cites them."""

from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

from personalized_agent.listener.providers import screenshot as shots
from personalized_agent.listener.providers.screenshot import (
    ScreenshotProvider, is_visual_document, measures)
from personalized_agent.platform_layer.base import ActiveWindow


def slide() -> Image.Image:
    """A slide: a coloured title band, a chart, a few blocks."""
    image = Image.new("RGB", (960, 540), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 960, 90), fill=(30, 80, 200))
    for i, colour in enumerate([(220, 60, 50), (40, 170, 90), (240, 180, 30)]):
        draw.rectangle((120 + i * 200, 480 - 80 * (i + 2), 260 + i * 200, 480),
                       fill=colour)
    draw.ellipse((700, 150, 900, 350), fill=(150, 60, 190))
    return image


def page_of_text() -> Image.Image:
    """A page of black lines on white: text, which the reader has."""
    image = Image.new("RGB", (960, 540), "white")
    draw = ImageDraw.Draw(image)
    for row in range(30, 520, 18):
        draw.text((40, row), "The quick brown fox jumps over the lazy dog " * 2,
                  fill="black")
    return image


def noisy() -> Image.Image:
    """A busy grey canvas: a figure drawn in lines, with no colour."""
    image = Image.new("RGB", (960, 540), "white")
    draw = ImageDraw.Draw(image)
    rng = random.Random(7)
    for _ in range(400):
        x, y = rng.randrange(960), rng.randrange(540)
        draw.line((x, y, x + rng.randrange(-60, 60), y + rng.randrange(-60, 60)),
                  fill="black", width=2)
    return image


class _Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)

    def lines(self, key):
        return [line for line in str(self.get(key, "")).splitlines() if line]


class _Activity:
    def __init__(self):
        self.window = ActiveWindow(application="POWERPNT.EXE", title="x", pid=1)
        self.images = []

    def get_active_window(self):
        return self.window

    def front_window_image(self):
        return self.images.pop(0) if len(self.images) > 1 else self.images[0]

    def seconds_since_input(self):
        return 1.0


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.config = _Config(listener_screen_excluded_applications="KeePass\n1Password")
        self.activity = _Activity()
        self.provider = ScreenshotProvider(
            self.config, SimpleNamespace(activity=self.activity, name="test"),
            folder=self.folder)
        self.provider.DWELL = 0
        self.provider.PER_WINDOW = 0
        self.events = []
        self.provider.bind(self.events.append)

    def _show(self, application, title, *images, text=None):
        self.activity.window = ActiveWindow(application=application, title=title, pid=1)
        self.activity.images = list(images)
        if text is not None:
            shots.note_text(application, title, text)
        taken = []
        for _ in range(4):
            path = self.provider.look()
            if path:
                taken.append(path)
        return taken


class WhatIsWorthAPictureTests(_Base):

    def test_a_colourful_slide_in_a_browser_is_taken(self) -> None:
        # More text than a busy canvas has, less than an article: the colour
        # is what says there is more here than the text.
        taken = self._show("firefox.exe", "Some page — Mozilla Firefox", slide(),
                           text=600)
        self.assertEqual(len(taken), 1)
        self.assertTrue(Path(taken[0]).exists())
        self.assertTrue(str(taken[0]).startswith(str(self.folder)))
        event = self.events[0]
        self.assertEqual(event.event_type, "screen.shot")
        self.assertIn("colourful", event.data["why"])
        self.assertLessEqual(Image.open(taken[0]).width, 1280)

    def test_a_page_of_text_the_reader_has_is_not(self) -> None:
        self.assertEqual(self._show("firefox.exe", "An article — Mozilla Firefox",
                                    page_of_text(), text=4000), [])

    def test_a_busy_canvas_with_no_text_is(self) -> None:
        taken = self._show("firefox.exe", "Untitled canvas — Mozilla Firefox",
                           noisy(), text=0)
        self.assertEqual(len(taken), 1)
        self.assertIn("little of it is text", self.events[0].data["why"])

    def test_a_known_visual_document_is_taken_however_plain(self) -> None:
        taken = self._show("POWERPNT.EXE", "deck.pptx - PowerPoint", page_of_text(),
                           text=4000)
        self.assertEqual(len(taken), 1)
        self.assertEqual(self.events[0].data["why"], "a visual document")
        self.assertTrue(is_visual_document("firefox.exe",
                                           "Deck - Google Slides — Mozilla Firefox"))

    def test_a_moving_picture_is_never_taken(self) -> None:
        frames = []
        for i in range(8):
            image = slide()
            ImageDraw.Draw(image).rectangle((0, 100 + i * 50, 960, 160 + i * 50),
                                            fill=(i * 30, 0, 0))
            frames.append(image)
        frames.append(frames[-1])
        self.assertEqual(self._show("firefox.exe", "a video — Mozilla Firefox",
                                    *frames, text=50), [])

    def test_the_same_picture_is_taken_once(self) -> None:
        self._show("firefox.exe", "Slides — Mozilla Firefox", slide(), text=50)
        self.assertEqual(self._show("firefox.exe", "Slides — Mozilla Firefox",
                                    slide(), text=50), [])
        self.assertEqual(len(self.events), 1)


class WhatIsNeverTakenTests(_Base):

    def test_monitoring_off_and_excluded_windows(self) -> None:
        self.config["listener_enabled"] = False
        self.assertEqual(self._show("firefox.exe", "Slides — Mozilla Firefox",
                                    slide(), text=50), [])
        self.config["listener_enabled"] = True
        self.assertEqual(self._show("KeePass.exe", "Vault - KeePass", slide(),
                                    text=50), [])
        self.config["listener_screenshots_enabled"] = False
        self.assertEqual(self._show("firefox.exe", "Slides — Mozilla Firefox",
                                    slide(), text=50), [])

    def test_a_glance_is_not_looked_at(self) -> None:
        self.provider.DWELL = 3600
        self.assertEqual(self._show("POWERPNT.EXE", "deck.pptx - PowerPoint",
                                    slide()), [])

    def test_the_daily_cap(self) -> None:
        self.config["listener_screenshots_per_day"] = 1
        self._show("POWERPNT.EXE", "a.pptx - PowerPoint", slide())
        self.assertEqual(self._show("POWERPNT.EXE", "b.pptx - PowerPoint",
                                    noisy()), [])


class MeasuresTests(unittest.TestCase):

    def test_slide_text_and_canvas_measure_apart(self) -> None:
        self.assertGreater(measures(slide())["colourful"],
                           ScreenshotProvider.COLOURFUL)
        self.assertLess(measures(page_of_text())["colourful"],
                        ScreenshotProvider.COLOURFUL)
        self.assertGreater(measures(noisy())["busy"], ScreenshotProvider.BUSY)
        self.assertTrue(measures(Image.new("RGB", (400, 300), "white"))["blank"])
