"""A START THAT STANDS STILL, WRITTEN DOWN (the user, 2026-11-08: a start stood still 44 seconds with
nothing said; "只在debugger版加"): where every thread was, in a build with the Debugger only."""

from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401

from personalized_agent import __main__ as main_module
from personalized_agent.common.paths import AppPaths, resolve_root


class TheStartWatchTests(unittest.TestCase):

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="start-watch-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.file = AppPaths(resolve_root(self.root)).logs_dir / "startup_stuck.txt"

    def test_a_quick_start_leaves_nothing(self) -> None:
        with mock.patch("personalized_agent.build_flags.debugger_available", return_value=True):
            stuck = main_module._watch_the_start(self.root)
        self.assertIsNotNone(stuck)
        main_module._started(stuck)
        self.assertFalse(self.file.exists())

    def test_one_that_stands_still_has_every_threads_place_kept(self) -> None:
        with mock.patch("personalized_agent.build_flags.debugger_available", return_value=True), \
                mock.patch.object(main_module, "START_STUCK_SECONDS", 1):
            stuck = main_module._watch_the_start(self.root)
        time.sleep(1.6)                              # standing still
        with self.assertLogs(main_module.log, level="WARNING") as said:
            main_module._started(stuck)
        written = self.file.read_text(encoding="utf-8", errors="replace")
        self.assertIn("where every thread stood then", written)
        self.assertIn("test_start_watch.py", written, "the place it stood, by file and line")
        self.assertIn("startup_stuck.txt", said.output[0])

    def test_without_the_debugger_nothing_is_watched(self) -> None:
        with mock.patch("personalized_agent.build_flags.debugger_available", return_value=False):
            self.assertIsNone(main_module._watch_the_start(self.root))
        main_module._started(None)
        self.assertFalse(self.file.exists())


if __name__ == "__main__":
    unittest.main()
