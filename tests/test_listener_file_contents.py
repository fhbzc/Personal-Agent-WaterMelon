"""What a filesystem event carries beyond the path.

A path and a timestamp say somebody touched `fetch.py`. They do not say
whether they were fixing the rate limiting or reading it to remember what
it did, and that difference is the whole of what a state is for.
"""

import os
import tempfile
import unittest
from pathlib import Path

from support import TemporaryAgent


class _Raw:
    """One notification, the shape watchdog hands over."""

    def __init__(self, path, event_type="modified", is_directory=False):
        self.src_path = str(path)
        self.event_type = event_type
        self.is_directory = is_directory


class WhatAFileEventCarriesTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        from personalized_agent.listener.providers.filesystem import (
            FilesystemProvider)
        self.provider = FilesystemProvider(self.app.config)
        self.sent = []
        self.provider.emit = lambda event: self.sent.append(event)
        self.work = tempfile.TemporaryDirectory()
        self.here = Path(self.work.name)

    def tearDown(self) -> None:
        self.work.cleanup()
        self.agent.cleanup()

    def _touch(self, name, text):
        path = self.here / name
        path.write_text(text, encoding="utf-8")
        return path

    def _event_for(self, path):
        self.sent.clear()
        self.provider.handle_raw(_Raw(path))
        self.provider.flush_held_events()
        return self.sent[-1] if self.sent else None

    def test_the_text_of_the_file_is_on_the_event(self) -> None:
        path = self._touch("fetch.py", "def fetch():\n    return 1\n")
        event = self._event_for(path)
        self.assertIsNotNone(event, "nothing was emitted")
        self.assertIn("def fetch()", event.data.get("content", ""))
        self.assertFalse(event.data.get("content_cut"))
        self.assertTrue(event.data.get("content_of"))

    def test_and_it_is_cut_at_the_cap_and_says_so(self) -> None:
        self.app.config.set("listener_file_excerpt_chars", 50)
        path = self._touch("long.md", "x" * 500)
        event = self._event_for(path)
        self.assertEqual(len(event.data["content"]), 50)
        self.assertTrue(event.data["content_cut"],
                        "a cut excerpt did not say it was cut")

    def test_and_the_setting_can_turn_it_off(self) -> None:
        self.app.config.set("listener_file_excerpt_chars", 0)
        path = self._touch("notes.md", "something private")
        event = self._event_for(path)
        self.assertNotIn("content", event.data)
        # The event itself is still recorded: that the file was touched is
        # a fact about the afternoon either way.
        self.assertEqual(event.data["path"], str(path))

    def test_and_the_cap_cannot_be_raised_past_the_one_in_the_code(self) -> None:
        """An unbounded excerpt is not an excerpt."""
        self.app.config.set("listener_file_excerpt_chars", 10_000_000)
        self.assertEqual(self.provider._how_much_of_a_file(),
                         self.provider.MOST_OF_A_FILE)

    def test_and_a_file_that_holds_secrets_is_not_read_inside(self) -> None:
        """It is a text file by every test this code can apply, and it is a
        list of the user's keys."""
        path = self._touch(".env", "OPENAI_API_KEY=sk-live-1234")
        event = self._event_for(path)
        self.assertNotIn("content", event.data)
        self.assertIn("credentials", event.data.get("content_withheld", ""))
        # And the fact that it was touched is still there.
        self.assertEqual(event.data["path"], str(path))

    def test_and_a_binary_is_left_alone(self) -> None:
        path = self.here / "thing.dll"
        path.write_bytes(bytes(range(256)) * 4)
        event = self._event_for(path)
        self.assertNotIn("content", event.data)

    def test_and_something_enormous_is_not_opened(self) -> None:
        self.app.config.set("listener_file_excerpt_chars", 100)
        path = self._touch("huge.log", "y" * 3_000_000)
        event = self._event_for(path)
        self.assertNotIn("content", event.data)
        self.assertIn("too big", event.data.get("content_withheld", ""))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
