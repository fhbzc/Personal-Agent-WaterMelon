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

    def test_never_faulthandlers_dump_which_brought_the_process_down(self) -> None:
        # 2026-11-20 20:15: python312.dll 0xc0000005 while faulthandler read the threads without the lock
        page = (support.ROOT / "personalized_agent" / "__main__.py").read_text(encoding="utf-8")
        self.assertNotIn("dump_traceback_later(", page)
        self.assertIn("threading.Timer(START_STUCK_SECONDS, _write_every_thread", page)

    def test_a_crash_is_recorded_and_the_last_ones_kept(self) -> None:
        import faulthandler
        logs = self.file.parent
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "crash.txt").write_text("started\nFatal Python error: made up\n", encoding="utf-8")
        self.addCleanup(faulthandler.disable)
        with mock.patch("personalized_agent.build_flags.debugger_available", return_value=True), \
                self.assertLogs(main_module.log, level="WARNING") as said:
            main_module._record_crashes(self.root)
        self.addCleanup(lambda: main_module._CRASH_FILE.close())
        self.assertTrue(faulthandler.is_enabled())
        kept = list(logs.glob("crash-*.txt"))
        self.assertEqual(len(kept), 1, "the last run's crash kept")
        self.assertIn("Fatal Python error: made up", kept[0].read_text(encoding="utf-8"))
        self.assertIn("ended in a crash", said.output[0])
        faulthandler.disable()
        main_module._CRASH_FILE.flush()
        self.assertEqual((logs / "crash.txt").read_text(encoding="utf-8").count("\n"), 1, "only its first line")

    def test_without_the_debugger_nothing_is_watched(self) -> None:
        with mock.patch("personalized_agent.build_flags.debugger_available", return_value=False):
            self.assertIsNone(main_module._watch_the_start(self.root))
        main_module._started(None)
        self.assertFalse(self.file.exists())


if __name__ == "__main__":
    unittest.main()
