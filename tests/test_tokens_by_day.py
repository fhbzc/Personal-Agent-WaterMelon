"""The Debugger's token trend over days: what each kind of work spent on
each of the last N days, cut at the reader's own midnight."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from personalized_agent.common.observability import model_calls as calls_module
from personalized_agent.common.observability.model_calls import ModelCalls
from personalized_agent.data_manage.storage.database import Database


class TokensByDayTests(unittest.TestCase):

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="pa-tokens-"))
        self.db = Database(self.dir / "agent.db")
        self.calls = ModelCalls(self.db)

    def tearDown(self) -> None:
        close = getattr(self.db, "close", None)
        if close:
            close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _call(self, at: datetime, task: str, tokens: int) -> None:
        with mock.patch.object(calls_module, "utcnow", return_value=at):
            self.calls.record(provider="p", model="m", task=task,
                              input_tokens=tokens, output_tokens=0, latency_ms=1)

    def test_each_day_and_kind_is_summed(self) -> None:
        now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
        self._call(now, "state_heartbeat", 100)
        self._call(now - timedelta(hours=1), "state_heartbeat", 50)
        self._call(now - timedelta(hours=2), "chat", 7)
        self._call(now - timedelta(days=2), "state_heartbeat", 30)
        self._call(now - timedelta(days=40), "state_heartbeat", 999)   # too old
        with mock.patch.object(calls_module, "utcnow", return_value=now):
            said = self.calls.by_day(days=7)
        self.assertEqual(len(said["days"]), 7)
        self.assertEqual(said["days"][-1], "2026-10-08")
        rows = {(r["day"], r["task"]): r for r in said["rows"]}
        self.assertEqual(rows[("2026-10-08", "state_heartbeat")]["total_tokens"], 150)
        self.assertEqual(rows[("2026-10-08", "state_heartbeat")]["calls"], 2)
        self.assertEqual(rows[("2026-10-08", "chat")]["total_tokens"], 7)
        self.assertEqual(rows[("2026-10-06", "state_heartbeat")]["total_tokens"], 30)
        self.assertNotIn(999, [r["total_tokens"] for r in said["rows"]])

    def test_input_is_what_went_in_whole_cached_too(self) -> None:
        """A cached provider's call reads a handful "fresh" and the rest from
        its cache: input is all of it, so "input", "output" and "total" are
        three different lines - not nothing, total and total again."""
        now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
        with mock.patch.object(calls_module, "utcnow", return_value=now):
            self.calls.record(provider="claude_code", model="opus", task="work_run",
                              input_tokens=10, output_tokens=4000, latency_ms=1,
                              cached_input_tokens=300000, cache_written_tokens=20000)
            day = self.calls.by_day(days=1)["rows"][0]
            point = self.calls.over_time()[0]
            totals = self.calls.totals()
            kinds = self.calls.by_task()["work_run"]
        self.assertEqual((day["input_tokens"], day["output_tokens"], day["total_tokens"]),
                         (320010, 4000, 324010))
        self.assertEqual((point["input_tokens"], point["output_tokens"], point["total_tokens"]),
                         (320010, 4000, 324010))
        self.assertEqual((totals["input"], totals["total"]), (320010, 324010))
        self.assertEqual((kinds["input"], kinds["total"]), (320010, 324010))

    def test_what_was_read_from_the_cache_comes_with_each_day(self) -> None:
        now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
        with mock.patch.object(calls_module, "utcnow", return_value=now):
            self.calls.record(provider="claude_code", model="opus", task="work_run",
                              input_tokens=10, output_tokens=4000, latency_ms=1,
                              cached_input_tokens=300000, cache_written_tokens=20000)
            day = self.calls.by_day(days=1)["rows"][0]
        self.assertEqual(day["cached_tokens"], 300000)
        self.assertEqual(day["total_tokens"], 324010)

    def test_days_are_cut_at_the_readers_midnight(self) -> None:
        # 23:30 UTC on the 7th is already the 8th eight hours east of UTC.
        late = datetime(2026, 10, 7, 23, 30, tzinfo=timezone.utc)
        self._call(late, "chat", 5)
        now = datetime(2026, 10, 8, 2, 0, tzinfo=timezone.utc)
        with mock.patch.object(calls_module, "utcnow", return_value=now):
            utc = self.calls.by_day(days=3)
            east = self.calls.by_day(days=3, minutes_east=480)
        self.assertEqual([r["day"] for r in utc["rows"]], ["2026-10-07"])
        self.assertEqual([r["day"] for r in east["rows"]], ["2026-10-08"])


class OldCallsNamedTests(unittest.TestCase):
    """Calls made before learning and each kind of lessons had a name of
    their own are named - only where it can be told for certain - when the
    database opens, and once."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="pa-names-"))
        self.path = self.dir / "agent.db"
        self.db = Database(self.path)

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _call(self, ident, at, task, read, out=100):
        self.db.execute(
            "INSERT INTO model_calls (id, created_at, provider, model, task, input_tokens, "
            "output_tokens, total_tokens, latency_ms, ok, error, cached_input_tokens) VALUES "
            "(?, ?, 'p', 'm', ?, 10, ?, ?, 1, 1, '', ?)", (ident, at, task, out, read + out, read - 10))

    def _learning(self, unit, since, until, calls, tokens_in):
        self.db.execute(
            "INSERT INTO experience_learning (unit_id, until, done, learned_at, calls, tokens_in, "
            "tokens_out, counted_since) VALUES (?, ?, 1, ?, ?, ?, 0, ?)",
            (unit, until, until, calls, tokens_in, since))

    def _tasks(self):
        return {row["id"]: row["task"] for row in self.db.query("SELECT id, task FROM model_calls")}

    def _reopen(self):
        self.db.close()
        self.db = Database(self.path)

    def test_a_learning_whose_calls_are_all_its_own(self) -> None:
        self._learning("unit_a", "2026-10-01T10:00:00", "2026-10-01T11:00:00", 2, 3000)
        self._call("a1", "2026-10-01T10:10:00", "experience", 1000)
        self._call("a2", "2026-10-01T10:20:00", "experience", 2000)
        self._call("g1", "2026-10-01T12:00:00", "experience", 500)          # after it
        # Calls among another's, in the same hours: not told apart, left.
        self._learning("unit_b", "2026-10-02T10:00:00", "2026-10-02T11:00:00", 1, 1000)
        self._call("b1", "2026-10-02T10:10:00", "experience", 1000)
        self._call("b2", "2026-10-02T10:30:00", "experience", 900)
        self._reopen()
        tasks = self._tasks()
        self.assertEqual((tasks["a1"], tasks["a2"]), ("experience_stages", "experience_stages"))
        self.assertEqual((tasks["g1"], tasks["b1"], tasks["b2"]),
                         ("experience", "experience", "experience"))

    def test_lessons_by_what_was_learned_that_minute(self) -> None:
        for ident, kind, minute in (("e1", "work", "2026-10-03T13:18:05"),
                                    ("e2", "notifications", "2026-10-03T16:40:09"),
                                    ("e3", "work", "2026-10-03T13:08:01"),
                                    ("e4", "notifications", "2026-10-03T13:08:02")):
            self.db.execute("INSERT INTO lesson_evidence (id, kind, at, processed_at) "
                            "VALUES (?, ?, ?, ?)", (ident, kind, minute, minute))
        self._call("l1", "2026-10-03T13:18:40", "lessons", 900)
        self._call("l2", "2026-10-03T16:40:50", "lessons", 900)
        self._call("l3", "2026-10-03T13:08:30", "lessons", 900)              # both kinds
        self._reopen()
        self._reopen()                                                      # and again
        tasks = self._tasks()
        self.assertEqual((tasks["l1"], tasks["l2"], tasks["l3"]),
                         ("lessons_work", "lessons_notifications", "lessons"))


if __name__ == "__main__":
    unittest.main()
