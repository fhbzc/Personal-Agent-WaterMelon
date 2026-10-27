"""A SMOOTH START (the user, 2026-10-26: the app felt stuck as it came up):
the listener's heavy first passes wait a minute, and where recording starts
from is fixed at once, so nothing said after the start is missed."""

import threading
import time
import unittest
from unittest import mock

from personalized_agent.listener.providers import assistant, filesystem


class QuietAtStartTests(unittest.TestCase):

    def test_the_assistant_fixes_where_it_starts_at_once_and_reads_later(self) -> None:
        provider = assistant.AssistantProvider.__new__(assistant.AssistantProvider)
        provider._stop = threading.Event()
        provider._primed = False
        provider.poll = mock.Mock(side_effect=lambda: provider._stop.set())
        provider.QUIET_AT_START, provider.poll_seconds = 0.3, 0.01
        with mock.patch.object(provider, "_skip_to_end", create=True) as skipped:
            began = time.monotonic()
            provider._loop()
        self.assertTrue(skipped.called and provider._primed, "where it starts from: now")
        # Windows' clock ticks every 15 ms or so (CI, 2026-10-27: 0.296 s).
        self.assertGreaterEqual(time.monotonic() - began, 0.25, "the heavy pass waited")
        self.assertEqual(provider.poll.call_count, 1)

    def test_stopped_while_quiet_reads_nothing(self) -> None:
        provider = assistant.AssistantProvider.__new__(assistant.AssistantProvider)
        provider._stop = threading.Event()
        provider._stop.set()
        provider.poll = mock.Mock()
        with mock.patch.object(provider, "_skip_to_end", create=True):
            provider._loop()
        provider.poll.assert_not_called()

    def test_what_was_opened_waits_and_reaches_back_past_the_wait(self) -> None:
        cls = filesystem.FilesystemProvider
        self.assertGreater(cls.WHAT_THEY_OPENED_SINCE, cls.QUIET_AT_START + cls.WHAT_THEY_OPENED_EVERY)
        provider = cls.__new__(cls)
        provider._stop = threading.Event()
        provider.QUIET_AT_START = 0.2
        provider._ask_the_desktop = mock.Mock(side_effect=lambda: provider._stop.set())
        began = time.monotonic()
        provider._watch_what_they_opened()
        self.assertGreaterEqual(time.monotonic() - began, 0.15, "it waited")
        provider._ask_the_desktop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
