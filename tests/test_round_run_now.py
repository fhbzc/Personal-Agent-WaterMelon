"""Run now, from the Overview: a state heartbeat round begun at once, and the
next regular one counted from when it finishes."""

from __future__ import annotations

import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from support import TemporaryAgent

ROOT = Path(__file__).resolve().parents[1]


class RunNowTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.beat = self.agent.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_next_regular_round_is_counted_from_the_last_finish(self) -> None:
        finished = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
        self.agent.app.config.set("state_round_completed_at", finished.isoformat(),
                                  internal=True)
        said = self.beat.when_next()
        self.assertEqual(said["next_at"],
                         (finished + timedelta(hours=said["every_hours"])).isoformat())
        # And a round run now that finishes puts it off by as long - the
        # regular rounds are measured from it.
        self.beat._round_is_done(type("W", (), {"scope": "live"})())
        later = self.beat.when_next()
        self.assertGreater(later["next_at"], said["next_at"])
        verdict = self.beat.due_now()
        self.assertFalse(verdict["run"] and not verdict.get("resuming"), verdict.get("why"))

    def test_begun_at_once_and_answered_straight_away(self) -> None:
        began = threading.Event()
        release = threading.Event()

        def run_once(trigger="timer", scope="live"):
            self.beat._visits_in_flight["live"] = 1
            began.set()
            release.wait(5)
            self.beat._visits_in_flight["live"] = 0
            return {}
        self.beat.run_once = run_once
        started = time.monotonic()
        said = self.beat.start_now()
        self.assertLess(time.monotonic() - started, 1.0, "answered without waiting")
        self.assertTrue(said["started"])
        self.assertTrue(began.wait(5))
        again = self.beat.start_now()
        self.assertFalse(again["started"], "one round at a time")
        self.assertTrue(again["running"])
        release.set()

    def test_not_while_the_regular_round_is_going(self) -> None:
        ran = []
        self.beat.run_once = lambda trigger="timer", scope="live": ran.append(trigger)
        # A regular round begun and not finished: between its visits.
        self.agent.app.config.set("state_round_started_at",
                                  datetime.now(timezone.utc).isoformat(), internal=True)
        self.agent.app.config.set("state_round_done", False, internal=True)
        self.assertTrue(self.beat._round_in_progress())
        said = self.beat.start_now()
        self.assertFalse(said["started"])
        self.assertIn("already running", said["why"])
        time.sleep(0.3)
        self.assertEqual(ran, [], "run now did nothing")

    def test_and_the_timer_begins_none_while_one_runs(self) -> None:
        # A visit holds the round's lock; another visit - the timer's - is
        # skipped, not run beside it.
        running = self.beat._running_in("live")
        self.assertTrue(running.acquire(blocking=False))
        try:
            said = self.beat._run_visit(trigger="timer", scope="live")
        finally:
            running.release()
        self.assertIn("already running", said.get("skipped", ""))

    def test_the_button_is_on_the_overview(self) -> None:
        page = (ROOT / "personalized_agent" / "api" / "web" / "index.html").read_text(encoding="utf-8")
        card = page[page.index('id="ov-tasks-card"'):page.index('id="ov-tasks">')]
        self.assertIn('id="ov-round-run"', card)
        script = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('api.post("/api/state_heartbeat/start"', script)
        from personalized_agent.api.routes import build_routes
        routes = build_routes(self.agent.app)
        self.assertIn(("POST", "/api/state_heartbeat/start"), routes)
        self.assertIn(("GET", "/api/state_heartbeat/next"), routes)


if __name__ == "__main__":
    unittest.main()
