"""THE APPLICATION CLOSING, A RUN IN A PROCESS OF ITS OWN (found 2026-11-08 in the user's log: "'ChildRun'
object has no attribute 'close_for'" - its place was not kept): told through its control file, the
child keeps where it stands, and says so. Everything here is made up."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401

from personalized_agent.agent.work import child


class TheCloseTests(unittest.TestCase):

    def setUp(self) -> None:
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.folder = Path(work.name)
        self.run = child.ChildRun(SimpleNamespace(), self.folder,
                                  SimpleNamespace(poll=lambda: None, pid=0),
                                  {"run": "run_tide"}, {"id": "u1"})

    def _orders(self):
        return [json.loads(line) for line in
                (self.folder / child.CONTROL).read_text(encoding="utf-8").splitlines()]

    def test_told_it_keeps_its_place_and_it_is_said_when_it_has(self) -> None:
        def the_child() -> None:                 # reads its order, and says it did it
            until = time.monotonic() + 5
            while time.monotonic() < until:
                if (self.folder / child.CONTROL).exists():
                    order = self._orders()[-1]
                    (self.folder / child.SNAPSHOT).write_text(
                        json.dumps({"obeyed": [order["id"]]}), encoding="utf-8")
                    return
                time.sleep(0.02)
        threading.Thread(target=the_child, daemon=True).start()
        self.assertTrue(self.run.close_for("quit"))
        self.assertEqual([(one["do"], one["why"]) for one in self._orders()], [("close", "quit")])

    def test_not_heard_in_time_it_is_said_not_kept(self) -> None:
        with mock.patch("personalized_agent.agent.work.runner.CLOSE_SECONDS", 3):
            self.assertFalse(self.run.close_for("quit"))

    def test_the_child_keeps_where_it_stands_when_told(self) -> None:
        kept = []
        child._obey(SimpleNamespace(close_for=kept.append), {"do": "close", "why": "an update"})
        self.assertEqual(kept, ["an update"])

    def test_gone_already_nothing_is_told(self) -> None:
        self.run.process = SimpleNamespace(poll=lambda: 0, pid=0)
        self.assertFalse(self.run.close_for("quit"))
        self.assertFalse((self.folder / child.CONTROL).exists())


if __name__ == "__main__":
    unittest.main()
