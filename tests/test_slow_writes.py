"""A slow database write is said, with what did it - in a build with the Debugger only (the
user, 2026-11-11: "对debugger版本可以")."""

from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401

from personalized_agent.data_manage.storage import database as storage


class SlowWritesTests(unittest.TestCase):

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="slow-writes-"))
        self.addCleanup(lambda: shutil.rmtree(self.folder, ignore_errors=True))
        self.db = storage.Database(self.folder / "agent.db")
        self.addCleanup(self.db.close)
        patcher = mock.patch.object(storage, "SLOW_WRITE_SECONDS", 0.05)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _hold_it(self) -> None:
        with self.db.write() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS held (n INTEGER)")
            with self.db.write() as inner:          # one write said once, not each within it
                inner.execute("INSERT INTO held VALUES (1)")
            time.sleep(0.1)

    def test_said_with_where_it_was_written_from(self) -> None:
        self.db._watch_writes = True
        with self.assertLogs(storage.log, "WARNING") as said:
            self._hold_it()
        self.assertEqual(len(said.records), 1)
        line = said.output[0]
        self.assertIn("a database write took", line)
        self.assertIn("the application", line)
        self.assertIn("_hold_it", line, "what was doing it")

    def test_not_said_without_the_debugger(self) -> None:
        self.db._watch_writes = False
        with mock.patch.object(storage.log, "warning") as warned:
            self._hold_it()
        warned.assert_not_called()

    def test_the_childs_mark_is_the_runners(self) -> None:
        from personalized_agent.agent.work.runner import CHILD_ENV
        self.assertEqual(storage.CHILD_ENV, CHILD_ENV)


if __name__ == "__main__":
    unittest.main()
