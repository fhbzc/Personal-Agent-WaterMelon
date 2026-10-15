"""What a review reads: from where the summaries got to, not "the last
interval" - so what happened between the last review and the agent being
closed is read by the next one, and not lost.

Closed briefly, the next review reads it together with what follows. Closed
a long while, it is read ON ITS OWN, soon after the agent starts - a late
look, step one only - and the ordinary reviews never reach back across the
gap. However long the gap, nothing is skipped and time never splits it; only
more than one review carries is read in turns, oldest first, until caught up."""

from __future__ import annotations

import json
import time
import unittest
from datetime import timedelta
from unittest import mock

from support import TemporaryAgent

from personalized_agent.common.events.models import ListenerEvent, utcnow
from personalized_agent.heartbeat.core import heartbeat as beat


class _Reply:
    def __init__(self, payload):
        self.text = json.dumps(payload)


def _models(payload, prompts):
    def invoke(_self, request, *a, **k):
        prompts.append(json.dumps([m.get("content") for m in request.messages],
                                  ensure_ascii=False, default=str))
        return _Reply(payload)

    return type("M", (), {
        "invoke": invoke,
        "resolve": lambda self, *a, **k: type("R", (), {"to_dict": lambda s: {}})(),
        "check_availability": lambda self, *a, **k: type("A", (), {
            "available": True, "to_dict": lambda s: {}, "state": None})()})()


class TheWindowStartsWhereTheSummariesGotToTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.heartbeat
        self.app.config.set("heartbeat_interval_seconds", 900)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _mark(self, minutes_ago):
        self.app.config.set(beat.SUMMARISED_UNTIL,
                            (utcnow() - timedelta(minutes=minutes_ago)).isoformat(),
                            internal=True)

    def _event(self, minutes_ago, title):
        self.app.raw_store.add_event(ListenerEvent(
            event_type="screen.text", source="screen",
            timestamp=utcnow() - timedelta(minutes=minutes_ago),
            application="Code.exe", data={"text": title}))

    def _span(self):
        since, until = self.beat._window_bounds()
        return (until - since).total_seconds() / 60

    def test_with_nothing_kept_it_is_the_interval(self) -> None:
        self.beat._began_at = utcnow()
        self.assertAlmostEqual(self._span(), 15, delta=0.1)

    def test_closed_briefly_the_minutes_before_are_read_with_what_follows(self) -> None:
        # Summarised up to 23+5 minutes ago; eight minutes of work, closed
        # for five, started again fifteen minutes ago.
        self._mark(28)
        self.beat._began_at = utcnow() - timedelta(minutes=15)
        self.assertAlmostEqual(self._span(), 28, delta=0.1)
        self.assertIsNone(self.beat._late_stretch())

    def test_closed_long_the_reviews_do_not_reach_back_across_it(self) -> None:
        self._mark(180)
        self.beat._began_at = utcnow() - timedelta(minutes=15)
        self.assertAlmostEqual(self._span(), 15, delta=0.1)
        since, until = self.beat._late_stretch()
        self.assertAlmostEqual((until - since).total_seconds() / 60, 165, delta=0.1)

    def test_a_long_gap_with_nothing_recorded_is_one_quick_read(self) -> None:
        """Closed for three months: nothing was recorded, and the whole gap
        is passed over at once - no model call, no turns."""
        mark = utcnow() - timedelta(days=90)
        self.app.config.set(beat.SUMMARISED_UNTIL, mark.isoformat(), internal=True)
        self.beat._began_at = utcnow() - timedelta(minutes=1)
        self.assertEqual(self.beat._late_stretch(), (mark, self.beat._began_at))
        prompts = []
        self.beat.models = _models({"states": []}, prompts)
        self.beat.run_once(trigger="manual_debug")
        self.assertEqual(prompts, [])
        self.assertEqual(self.beat._summarised_until(), self.beat._began_at)

    def test_behind_while_running_the_whole_gap_is_one_review_when_it_fits(self) -> None:
        """The model away for six hours while the listener went on: the next
        review reads all six hours - time alone never splits it."""
        self._mark(6 * 60)
        self.beat._began_at = utcnow() - timedelta(hours=10)
        self._event(5 * 60, "five hours ago: the budget")
        self._event(90, "an hour and a half ago: the slides")
        self._event(3, "now: the email")
        self.assertIsNone(self.beat._late_stretch())
        prompts = []
        self.beat.models = _models({"states": [], "notifications": []}, prompts)
        self.beat.run_once(trigger="manual_debug")
        said = " ".join(prompts)
        for one in ("the budget", "the slides", "the email"):
            self.assertIn(one, said)
        self.assertNotIn("A LATE LOOK", said)
        self.assertLess(abs((utcnow() - self.beat._summarised_until()).total_seconds()), 30)

    def test_more_than_one_review_carries_is_read_in_turns_none_dropped(self) -> None:
        self._mark(6 * 60)
        self.beat._began_at = utcnow() - timedelta(hours=10)
        most = self.beat.STATE_FLOOR_EVENTS
        for index in range(most + 5):
            self._event(5 * 60 - index * 0.1, f"busy {index}")
        since, until = self.beat._late_stretch()
        # The first `most` events are this turn; the next turn starts there.
        self.assertAlmostEqual((until - since).total_seconds() / 60,
                               60 + most * 0.1, delta=0.05)
        prompts = []
        self.beat.models = _models({"states": [], "notifications": []}, prompts)
        self.beat.run_once(trigger="manual_debug")
        self.assertIn("A LATE LOOK", prompts[0])
        self.assertEqual(self.beat._summarised_until(), until)
        # The rest fits one review: an ordinary one, up to now.
        self.assertIsNone(self.beat._late_stretch())

    def test_catching_up_the_next_turn_is_due_at_once(self) -> None:
        self._mark(6 * 60)
        self.beat._began_at = utcnow() - timedelta(hours=10)
        for index in range(self.beat.STATE_FLOOR_EVENTS + 5):
            self._event(5 * 60 - index * 0.1, f"busy {index}")
        self.beat._loop_began = time.time() - 3600
        self.app.config.set(beat.RUN_TRIED_AT, utcnow().isoformat(), internal=True)
        self.beat._caught_some = True
        self.assertLess(self.beat._due_at() - time.time(), beat.CATCH_UP_TURN_SECONDS + 2)
        # A turn that got nowhere waits an interval, as any review.
        self.beat._caught_some = False
        self.assertGreater(self.beat._due_at() - time.time(), 60)

    def test_a_review_that_answered_moves_the_mark_and_one_blocked_does_not(self) -> None:
        self._mark(28)
        self.beat._began_at = utcnow() - timedelta(minutes=15)
        self._event(25, "before the agent was closed: the draft")
        self._event(5, "after it started again: the figures")
        prompts = []
        self.beat.models = _models({"states": []}, prompts)
        self.beat.run_once(trigger="manual_debug")
        # Both stretches were read.
        said = " ".join(prompts)
        self.assertIn("the draft", said)
        self.assertIn("the figures", said)
        mark = self.beat._summarised_until()
        self.assertLess(abs((utcnow() - mark).total_seconds()), 30)

        # The model away: the mark stays where it was.
        from personalized_agent.models.manager import ModelUnavailable
        self._mark(40)
        before = self.app.config.get(beat.SUMMARISED_UNTIL)

        def away(*_a, **_k):
            raise ModelUnavailable(mock.Mock(label="the model"),
                                   mock.Mock(requires_user_action=lambda: False,
                                             reason="offline", state=mock.Mock(value="x")))

        self.beat.models = type("M", (), {
            "invoke": lambda self, *a, **k: away(),
            "resolve": lambda self, *a, **k: type("R", (), {"to_dict": lambda s: {}})(),
            "check_availability": lambda self, *a, **k: type("A", (), {
                "available": True, "to_dict": lambda s: {}, "state": None})()})()
        self.beat.run_once(trigger="manual_debug")
        self.assertEqual(self.app.config.get(beat.SUMMARISED_UNTIL), before)

    def test_the_late_look_reads_only_the_stretch_before_and_writes_only_states(self) -> None:
        self._mark(200)
        self.beat._began_at = utcnow() - timedelta(minutes=2)
        self._event(150, "before the agent was closed: the draft")
        self._event(1, "after it started again: the figures")
        prompts = []
        self.beat.models = _models({"states": [], "notifications": []}, prompts)
        with mock.patch.object(self.beat, "run_once", wraps=self.beat.run_once) as ran:
            self.beat._catch_up()
        self.assertEqual(ran.call_count, 1)
        said = " ".join(prompts)
        self.assertIn("the draft", said)
        self.assertNotIn("the figures", said)
        self.assertIn("A LATE LOOK", said)
        # One call: step one alone, no notifications step.
        self.assertEqual(len(prompts), 1)
        # Read up to where the agent started; the next review goes on from there.
        self.assertEqual(self.beat._summarised_until(), self.beat._began_at)
        self.assertIsNone(self.beat._window_fixed)

    def test_a_late_look_with_nothing_to_read_just_moves_the_mark(self) -> None:
        self._mark(200)
        self.beat._began_at = utcnow() - timedelta(minutes=2)
        prompts = []
        self.beat.models = _models({"states": []}, prompts)
        self.beat._catch_up()
        self.assertEqual(prompts, [])
        self.assertEqual(self.beat._summarised_until(), self.beat._began_at)



class TheNextReviewIsDueFromTheLastTests(unittest.TestCase):
    """As the state heartbeat's rounds: unfinished last time, again at once;
    finished, an interval after where it got to; refused, an interval after
    the try."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.heartbeat
        self.app.config.set("heartbeat_interval_seconds", 900)
        self.beat._loop_began = __import__("time").time()

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _set(self, key, minutes_ago):
        self.app.config.set(key, (utcnow() - timedelta(minutes=minutes_ago)).isoformat(),
                            internal=True)

    def _in_minutes(self):
        return (self.beat._due_at() - __import__("time").time()) / 60

    def test_nothing_known_is_an_interval_from_the_start(self) -> None:
        self.assertAlmostEqual(self._in_minutes(), 15, delta=0.1)

    def test_finished_five_minutes_ago_is_due_in_ten(self) -> None:
        self._set(beat.SUMMARISED_UNTIL, 5)
        self._set(beat.RUN_TRIED_AT, 5)
        self.assertAlmostEqual(self._in_minutes(), 10, delta=0.1)

    def test_overdue_is_due_once_the_agent_has_started(self) -> None:
        self._set(beat.SUMMARISED_UNTIL, 25)
        self._set(beat.RUN_TRIED_AT, 25)
        self.assertAlmostEqual(self._in_minutes(), beat.CATCH_UP_AFTER_SECONDS / 60, delta=0.05)

    def test_unfinished_last_time_is_done_again_at_once(self) -> None:
        self._set(beat.SUMMARISED_UNTIL, 2)
        self._set(beat.RUN_STARTED_AT, 1)
        self.assertAlmostEqual(self._in_minutes(), beat.CATCH_UP_AFTER_SECONDS / 60, delta=0.05)

    def test_refused_is_tried_again_an_interval_later(self) -> None:
        self._set(beat.SUMMARISED_UNTIL, 40)
        self._set(beat.RUN_TRIED_AT, 1)
        self.assertAlmostEqual(self._in_minutes(), 14, delta=0.1)

    def _going(self):
        # The loop's thread, as status() sees it: alive.
        self.beat._thread = mock.Mock(is_alive=lambda: True)

    def test_the_readout_knows_the_last_and_next_after_a_restart(self) -> None:
        # Nothing ran in this process: the readout reads what the schedule
        # reads - "5m ago, next in 10m" - not "not yet, next unknown".
        self._going()
        self._set(beat.SUMMARISED_UNTIL, 5)
        self._set(beat.RUN_TRIED_AT, 5)
        said = self.beat.status()
        self.assertAlmostEqual(said["last_run_seconds_ago"], 300, delta=5)
        self.assertAlmostEqual(said["next_run_in_seconds"], 600, delta=5)

    def test_the_readout_while_a_review_is_under_way(self) -> None:
        self._going()
        self._set(beat.RUN_TRIED_AT, 20)
        self._set(beat.RUN_STARTED_AT, 0)
        self.assertEqual(self.beat.status()["next_run_in_seconds"], 0.0)

    def test_the_readout_of_one_that_is_off(self) -> None:
        self._going()
        self.app.config.set("heartbeat_enabled", False)
        self.assertIsNone(self.beat.status()["next_run_in_seconds"])

    def test_a_review_is_marked_under_way_and_tried_even_when_it_fails(self) -> None:
        seen = []

        def run_once(trigger):
            seen.append(self.app.config.get(beat.RUN_STARTED_AT))
            raise RuntimeError("the model fell over")

        with mock.patch.object(self.beat, "run_once", side_effect=run_once):
            with self.assertRaises(RuntimeError):
                self.beat._run_marked()
        self.assertTrue(seen[0])
        self.assertEqual(self.app.config.get(beat.RUN_STARTED_AT), "")
        self.assertIsNotNone(beat._as_utc(self.app.config.get(beat.RUN_TRIED_AT)))


if __name__ == "__main__":
    unittest.main()
