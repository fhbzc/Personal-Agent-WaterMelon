"""Scheduler reliability and calendar synchronisation (sections 59, 60)."""

from __future__ import annotations

import unittest
import inspect
import json
import socket
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from support import ROOT, TemporaryAgent, asking, keyed

from personalized_agent.common.events.models import (ScheduledTask, parse_iso,
                                                     utcnow)


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class SchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.scheduler = self.app.scheduler
        self.store = self.app.scheduler_store

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _task(self, **overrides) -> ScheduledTask:
        payload = {
            "title": "reminder",
            "scheduled_at": utcnow() - timedelta(seconds=1),
            "execution_type": "notification",
            # Section 52.2: a task says whether it interrupts. This one does,
            # which is what the tests below are about.
            "task_payload": {"message": "stand up", "notify_user": True},
        }
        payload.update(overrides)
        return self.scheduler.create_task(ScheduledTask(**payload))

    def test_due_task_runs_and_completes(self) -> None:
        task = self._task()
        results = self.scheduler.tick()
        self.assertEqual(results[0]["status"], "completed")
        self.assertEqual(self.store.get(task.id).status, "completed")
        self.assertTrue(self.app.notifications.recent())

    def test_future_task_is_not_due(self) -> None:
        self._task(scheduled_at=utcnow() + timedelta(hours=2))
        self.assertEqual(self.scheduler.tick(), [])

    def test_claim_prevents_duplicate_execution(self) -> None:
        task = self._task()
        self.assertIsNotNone(self.store.claim(task))
        self.assertIsNone(self.store.claim(task), "a claimed task must not be claimed twice")

    def _fails(self, why: str, retryable: bool) -> None:
        """Make delivering the notification go wrong.

        Only notifications run, so a notification is what fails: what the
        scheduler does with a failure is the same whatever the task was.
        """
        from personalized_agent.scheduler.execution.executors import ExecutionOutcome

        self.scheduler.executor.execute = lambda task: ExecutionOutcome(
            False, why, retryable=retryable)

    def test_failed_task_is_retried_then_marked_failed(self) -> None:
        task = self._task(retry_policy={"max_retries": 1, "backoff_seconds": 0})
        self._fails("the notifier does_not_exist", retryable=False)
        self.scheduler.tick()
        refreshed = self.store.get(task.id)
        # A failure that is not retryable fails immediately with the reason kept.
        self.assertEqual(refreshed.status, "failed")
        self.assertIn("does_not_exist", refreshed.last_error)

    def test_retryable_failure_reschedules(self) -> None:
        task = self._task(retry_policy={"max_retries": 2, "backoff_seconds": 0})
        self._fails("the notifier was busy", retryable=True)
        self.scheduler.tick()
        refreshed = self.store.get(task.id)
        self.assertEqual(refreshed.status, "pending", "it was not given another go")
        self.assertEqual(refreshed.attempts, 1)
        self.assertIn("busy", refreshed.last_error)

    def test_restart_recovery_requeues_running_tasks(self) -> None:
        task = self._task()
        self.store.claim(task)
        self.assertEqual(self.store.get(task.id).status, "running")
        recovered = self.store.recover_orphans()
        self.assertEqual(recovered, 1)
        self.assertEqual(self.store.get(task.id).status, "pending")

    def test_calendar_edits_change_scheduler_state(self) -> None:
        task = self._task(scheduled_at=utcnow() + timedelta(days=1))
        new_time = utcnow() + timedelta(days=2)
        self.scheduler.reschedule(task.id, new_time)
        self.assertEqual(
            self.store.get(task.id).scheduled_at.replace(microsecond=0),
            new_time.replace(microsecond=0),
        )
        self.scheduler.disable(task.id)
        self.assertEqual(self.store.get(task.id).status, "disabled")
        self.scheduler.enable(task.id)
        self.assertEqual(self.store.get(task.id).status, "pending")
        self.assertTrue(self.scheduler.delete(task.id))
        self.assertIsNone(self.store.get(task.id))

    def test_disabled_task_is_never_due(self) -> None:
        task = self._task()
        self.scheduler.disable(task.id)
        self.assertEqual(self.scheduler.tick(), [])

    def test_tasks_survive_a_restart(self) -> None:
        task = self._task(scheduled_at=utcnow() + timedelta(days=1))
        from personalized_agent.scheduler.storage.store import SchedulerStore

        reopened = SchedulerStore(self.app.db)
        self.assertIsNotNone(reopened.get(task.id))

    def test_scheduler_records_raw_edits(self) -> None:
        self._task()
        self.scheduler.tick()
        edits = self.app.raw_store.recent_edits()
        self.assertTrue(any(e.source == "scheduler" for e in edits))


class TaskNotificationChoiceTests(unittest.TestCase):
    """Section 52.2: scheduling and interrupting are separate decisions.

    Every scheduled task used to raise a notification when it ran - the flag
    defaulted to on and was passed force=True, which walks past the user's own
    notification setting. On real data all 26 scheduled tasks would have
    interrupted, including 24 the agent had set to check things for itself.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.raised = []
        self.app.bus.subscribe("notification.created",
                               lambda topic, payload: self.raised.append(payload))
        self.when = (utcnow() + timedelta(hours=2)).isoformat()

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _schedule(self, **overrides):
        args = {"title": "Check the build", "description": "Looks at whether it moved.",
                "scheduled_at": self.when, "notify": "silent"}
        args.update(overrides)
        return self.app.agent.tools.call("schedule_task", args)

    def test_a_task_must_say_whether_it_interrupts(self) -> None:
        outcome = self._schedule(notify=None)
        self.assertIn("error", outcome)
        self.assertIn("interrupt", outcome["error"])

    def test_silent_is_a_real_choice_and_is_recorded(self) -> None:
        outcome = self._schedule(notify="silent")
        task = self.app.scheduler_store.get(outcome["task_id"])
        self.assertFalse(task.task_payload["notify_user"])

    def test_notify_is_recorded_too(self) -> None:
        outcome = self._schedule(notify="notify")
        task = self.app.scheduler_store.get(outcome["task_id"])
        self.assertTrue(task.task_payload["notify_user"])

    def test_a_silent_task_does_not_interrupt_when_it_runs(self) -> None:
        outcome = self._schedule(notify="silent")
        self.app.scheduler.store.update(outcome["task_id"], scheduled_at=utcnow())
        self.app.scheduler.tick()
        self.assertEqual([n for n in self.raised if n.get("kind") == "proactive"], [])

    def test_a_notifying_task_does_interrupt(self) -> None:
        outcome = self._schedule(notify="notify")
        self.app.scheduler.store.update(outcome["task_id"], scheduled_at=utcnow())
        self.app.scheduler.tick()
        self.assertTrue([n for n in self.raised if n.get("kind") == "proactive"])

    def test_the_user_can_switch_it_off_afterwards(self) -> None:
        # A setting the user can see but not change is only half of one.
        outcome = self._schedule(notify="notify")
        self.app.scheduler.store.get(outcome["task_id"])
        updated = self.app.scheduler.store.get(outcome["task_id"])
        payload = dict(updated.task_payload)
        payload["notify_user"] = False
        self.app.scheduler.update_task(outcome["task_id"], task_payload=payload)
        self.assertFalse(
            self.app.scheduler.store.get(outcome["task_id"]).task_payload["notify_user"])


class TaskDetailIsForTheUserTests(unittest.TestCase):
    """Section 52.1, as amended: the detail is read by the user, not the agent.

    Asking one field for both the plan and the reasoning produced descriptions
    of 488 to 905 characters on real data - evidence, priors and what the
    answer would settle, written agent to agent.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.when = (utcnow() + timedelta(hours=2)).isoformat()

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _schedule(self, description, **extra):
        args = {"title": "Confirm the clock", "description": description,
                "scheduled_at": self.when, "notify": "silent"}
        args.update(extra)
        return self.app.agent.tools.call("schedule_task", args)

    def test_a_runaway_description_is_refused_and_told_where_it_belongs(self) -> None:
        # The ceiling is 2000 tokens, so this has to be genuinely enormous -
        # it is a stop against a runaway, not a style rule.
        outcome = self._schedule("Compares the activity envelope. " * 400)
        self.assertIn("error", outcome)
        self.assertIn("rationale", outcome["error"])

    def test_a_long_but_reasonable_description_is_allowed(self) -> None:
        # The descriptions that prompted this were 488 to 905 characters, and
        # the cap is not what rules those out - the guidance is.
        outcome = self._schedule("Runs tomorrow at 18:15Z, 45 minutes before the "
                                 "afternoon-gap test. " + ("Compares envelopes. " * 30))
        self.assertIn("task_id", outcome)

    def test_a_plain_sentence_is_accepted(self) -> None:
        outcome = self._schedule("Checks whether your working day looks like "
                                 "Lisbon or Madrid time.")
        self.assertIn("task_id", outcome)

    def test_the_reasoning_is_kept_where_it_can_be_asked_for(self) -> None:
        outcome = self._schedule("Checks which time zone your day fits.",
                                 rationale="A +0100 offset was seen on 3 Sep, which "
                                           "in September is WEST rather than CET.")
        task = self.app.scheduler_store.get(outcome["task_id"])
        self.assertIn("WEST", task.metadata["rationale"])

    def test_the_agent_is_told_the_difference(self) -> None:
        # The heartbeat's notifications: what the user reads, and apart from
        # it, the reasoning, shown only if they ask.
        from personalized_agent.heartbeat.core.heartbeat import SYSTEM_PROMPT

        self.assertIn("Write it for the user", SYSTEM_PROMPT)
        self.assertIn('Your evidence and reasoning go in "why"', SYSTEM_PROMPT)


class TaskNotificationBackfillTests(unittest.TestCase):
    """Section 52.2: tasks made before there was a choice still need one.

    Falling to the new silent default would quietly switch off the tasks that
    exist in order to interrupt - a recurring break reminder among them.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _legacy(self, title, execution_type, payload):
        """A row as an older version wrote it - straight into the store.

        The scheduler no longer makes anything but notifications, but the
        rows an older version made are still in the database, and the
        backfill has to decide for them all.
        """
        task = ScheduledTask(title=title, description="made before the choice existed",
                             scheduled_at=utcnow() + timedelta(hours=5),
                             execution_type=execution_type, task_payload=payload)
        return self.app.scheduler_store.create(task).id

    def test_a_reminder_keeps_interrupting(self) -> None:
        task_id = self._legacy("Break reminder", "notification", {})
        self.app.db.backfill_notification_choice()
        task = self.app.scheduler_store.get(task_id)
        self.assertTrue(task.task_payload["notify_user"])

    def test_a_check_the_agent_set_for_itself_goes_quiet(self) -> None:
        task_id = self._legacy("Did the build move?", "model_task", {"surface": "tasks"})
        self.app.db.backfill_notification_choice()
        task = self.app.scheduler_store.get(task_id)
        self.assertFalse(task.task_payload["notify_user"])

    def test_an_explicit_choice_is_never_overwritten(self) -> None:
        task_id = self._legacy("Already decided", "model_task", {"notify_user": True})
        self.app.db.backfill_notification_choice()
        task = self.app.scheduler_store.get(task_id)
        self.assertTrue(task.task_payload["notify_user"])


class RecurrenceRuleTests(unittest.TestCase):
    """Section 52.3: a repeat is a rule, checked before it is accepted."""

    def _parse(self, raw):
        from personalized_agent.scheduler.core.recurrence import parse_recurrence

        return parse_recurrence(raw)

    def test_nothing_means_it_runs_once(self) -> None:
        self.assertEqual(self._parse(None), {})
        self.assertEqual(self._parse({}), {})

    def test_an_interval_can_be_said_in_words(self) -> None:
        self.assertEqual(self._parse({"every": 45, "unit": "minutes"}),
                         {"every_seconds": 2700})
        self.assertEqual(self._parse({"every": 2, "unit": "weeks"}),
                         {"every_seconds": 1209600})

    def test_a_count_limits_it(self) -> None:
        rule = self._parse({"every": 1, "unit": "days", "count": 8})
        self.assertEqual(rule["count"], 8)

    def test_an_end_date_limits_it(self) -> None:
        rule = self._parse({"every": 1, "unit": "days",
                            "until": "2026-12-01T00:00:00Z"})
        self.assertEqual(rule["until"].year, 2026)

    def test_a_repeat_needs_an_interval(self) -> None:
        from personalized_agent.scheduler.core.recurrence import RecurrenceError

        with self.assertRaises(RecurrenceError):
            self._parse({"count": 3})

    def test_a_runaway_interval_is_refused(self) -> None:
        # Repeating every few seconds is a loop, not a schedule, and it would
        # spend the scheduler's whole tick on one task.
        from personalized_agent.scheduler.core.recurrence import RecurrenceError

        with self.assertRaises(RecurrenceError):
            self._parse({"every_seconds": 5})

    def test_a_count_of_zero_is_not_a_schedule(self) -> None:
        from personalized_agent.scheduler.core.recurrence import RecurrenceError

        with self.assertRaises(RecurrenceError):
            self._parse({"every": 1, "unit": "days", "count": 0})

    def test_it_describes_itself_in_words(self) -> None:
        from personalized_agent.scheduler.core.recurrence import describe

        self.assertEqual(describe({}), "runs once")
        self.assertEqual(describe(self._parse({"every": 45, "unit": "minutes"})),
                         "every 45 minutes, no end set")
        self.assertEqual(describe(self._parse({"every": 1, "unit": "days",
                                               "count": 8})),
                         "every day, 8 times")


class ARepeatsEndDateIsReadBackAsADateTests(unittest.TestCase):
    """A repeat that ends on a date, saved and read back, still compares as
    a date: the end date was read back as text, and the first comparison
    with a time raised TypeError (ported from PR #4)."""

    def _row(self, until):
        from personalized_agent.common.events.models import json_dump
        from personalized_agent.scheduler.core.recurrence import parse_recurrence
        rule = parse_recurrence({"every_seconds": 86400, "until": until})
        return {"id": "t1", "title": "Every day", "scheduled_at":
                "2026-10-01T09:00:00+00:00", "recurrence": json_dump(rule)}

    def test_the_next_run_and_the_calendar_work_after_a_reload(self) -> None:
        from personalized_agent.scheduler.core.recurrence import next_occurrence
        task = ScheduledTask.from_row(self._row("2026-10-05T00:00:00+00:00"))
        self.assertIsInstance(task.recurrence["until"], datetime)
        upcoming, why = next_occurrence(task.recurrence, task.scheduled_at, 0,
                                        now=parse_iso("2026-10-02T10:00:00+00:00"))
        self.assertEqual(upcoming, parse_iso("2026-10-03T09:00:00+00:00"))
        ended, why = next_occurrence(task.recurrence, task.scheduled_at, 0,
                                     now=parse_iso("2026-10-06T10:00:00+00:00"))
        self.assertIsNone(ended)
        self.assertIn("end of its repeat window", why)

    def test_an_end_date_that_cannot_be_read_ends_the_repeat(self) -> None:
        from personalized_agent.common.events.models import json_dump
        row = self._row("2026-10-05T00:00:00+00:00")
        row["recurrence"] = json_dump({"every_seconds": 86400, "until": "not a date"})
        task = ScheduledTask.from_row(row)
        self.assertEqual(task.recurrence["until"], task.scheduled_at)


class RecurrenceTimingTests(unittest.TestCase):
    """Section 52.3: when the next run lands."""

    def _next(self, raw, previous, runs, now):
        from personalized_agent.scheduler.core.recurrence import (
            next_occurrence, parse_recurrence)

        return next_occurrence(parse_recurrence(raw), previous, runs, now)

    def setUp(self) -> None:
        self.start = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)

    def test_the_next_run_follows_the_interval(self) -> None:
        upcoming, _ = self._next({"every": 1, "unit": "hours"}, self.start, 1,
                                 self.start + timedelta(minutes=1))
        self.assertEqual(upcoming, self.start + timedelta(hours=1))

    def test_it_advances_from_the_schedule_not_from_now(self) -> None:
        # Otherwise a task that runs three minutes late is three minutes later
        # every cycle, and a daily task walks around the clock.
        late = self.start + timedelta(minutes=3)
        upcoming, _ = self._next({"every": 1, "unit": "hours"}, self.start, 1, late)
        self.assertEqual(upcoming, self.start + timedelta(hours=1))

    def test_a_long_gap_produces_one_run_not_a_backlog(self) -> None:
        # The application was closed for three days. Coming back to ninety-six
        # missed reminders would be worse than missing them.
        now = self.start + timedelta(days=3)
        upcoming, _ = self._next({"every": 45, "unit": "minutes"}, self.start, 1, now)
        self.assertGreater(upcoming, now)
        self.assertLessEqual((upcoming - now).total_seconds(), 45 * 60)

    def test_it_stops_after_its_count(self) -> None:
        upcoming, why = self._next({"every": 1, "unit": "hours", "count": 3},
                                   self.start, 3, self.start)
        self.assertIsNone(upcoming)
        self.assertIn("3 runs", why)

    def test_it_stops_at_its_end_date(self) -> None:
        upcoming, why = self._next(
            {"every": 1, "unit": "days", "until": "2026-09-02T00:00:00Z"},
            self.start, 1, self.start)
        self.assertIsNone(upcoming)
        self.assertIn("window", why)


class RepeatingTaskRunsAgainTests(unittest.TestCase):
    """Section 52.3: the whole path, through the real scheduler."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _make(self, recurrence):
        outcome = self.app.agent.tools.call("schedule_task", {
            "title": "Stand up and stretch", "description": "A short break.",
            "notify": "notify", "execution_type": "notification",
            "scheduled_at": utcnow().isoformat(), "recurrence": recurrence})
        self.assertIn("task_id", outcome, outcome)
        return outcome["task_id"]

    def test_a_repeating_task_goes_back_on_the_calendar(self) -> None:
        task_id = self._make({"every": 45, "unit": "minutes"})
        self.app.scheduler.tick()
        task = self.app.scheduler_store.get(task_id)
        self.assertEqual(task.status, "pending", "it did not come back")
        self.assertEqual(task.runs, 1)
        self.assertGreater(task.scheduled_at, utcnow())

    def test_it_is_the_same_task_not_a_new_one(self) -> None:
        # A fresh row per run would bury the rest of the calendar, and lose
        # the notification choice and the reasoning with each copy.
        before = len(self.app.scheduler_store.list(limit=100))
        task_id = self._make({"every": 45, "unit": "minutes"})
        self.app.scheduler.tick()
        after = self.app.scheduler_store.list(limit=100)
        self.assertEqual(len(after), before + 1)
        self.assertTrue(any(t.id == task_id for t in after))

    def test_it_keeps_its_notification_choice_across_repeats(self) -> None:
        task_id = self._make({"every": 45, "unit": "minutes"})
        self.app.scheduler.tick()
        task = self.app.scheduler_store.get(task_id)
        self.assertTrue(task.task_payload["notify_user"])

    def test_a_counted_repeat_finishes(self) -> None:
        task_id = self._make({"every": 1, "unit": "minutes", "count": 2})
        for _ in range(2):
            self.app.scheduler.store.update(task_id, scheduled_at=utcnow())
            self.app.scheduler.tick()
        task = self.app.scheduler_store.get(task_id)
        self.assertEqual(task.runs, 2)
        self.assertEqual(task.status, "completed")

    def test_a_task_with_no_rule_still_runs_once(self) -> None:
        task_id = self._make(None)
        self.app.scheduler.tick()
        task = self.app.scheduler_store.get(task_id)
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.runs, 0)

    def test_a_bad_rule_is_refused_with_a_reason(self) -> None:
        outcome = self.app.agent.tools.call("schedule_task", {
            "title": "Too eager", "description": "Every four seconds.",
            "notify": "silent", "scheduled_at": utcnow().isoformat(),
            "recurrence": {"every_seconds": 4}})
        self.assertIn("error", outcome)
        self.assertIn("minimum", outcome["error"])

    def test_the_rule_survives_a_round_trip_through_storage(self) -> None:
        task_id = self._make({"every": 1, "unit": "days", "count": 5})
        reloaded = self.app.scheduler_store.get(task_id)
        self.assertEqual(reloaded.recurrence["every_seconds"], 86400)
        self.assertEqual(reloaded.recurrence["count"], 5)


class OccurrenceExpansionTests(unittest.TestCase):
    """Section 52.3: a repeating task is drawn as every run it will have.

    One stored row is the right shape for the data - the history, the notify
    choice and the reasoning belong to one thing - but it is the wrong shape
    for a calendar. A reminder every 45 minutes has to appear every 45
    minutes, or the calendar is not showing the week the user will have.
    """

    def _occ(self, raw, first, runs=0, **kw):
        from personalized_agent.scheduler.core.recurrence import (
            occurrences, parse_recurrence)

        return occurrences(parse_recurrence(raw), first, runs, **kw)

    def setUp(self) -> None:
        self.start = datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc)

    def test_a_repeat_with_no_end_is_drawn_to_the_horizon(self) -> None:
        from personalized_agent.scheduler.core.recurrence import HORIZON

        drawn = self._occ({"every": 45, "unit": "minutes"}, self.start)
        self.assertEqual(len(drawn), HORIZON)
        self.assertEqual(HORIZON, 30)

    def test_the_blocks_are_one_interval_apart(self) -> None:
        drawn = self._occ({"every": 45, "unit": "minutes"}, self.start)
        gaps = {(b - a).total_seconds()
                for (_, a), (_, b) in zip(drawn, drawn[1:])}
        self.assertEqual(gaps, {45 * 60})

    def test_a_window_gets_only_the_runs_inside_it(self) -> None:
        drawn = self._occ({"every": 45, "unit": "minutes"}, self.start,
                          window_end=self.start + timedelta(hours=3))
        self.assertEqual(len(drawn), 5)          # 9:00 to 12:00 inclusive

    def test_a_counted_repeat_is_drawn_exactly_that_many_times(self) -> None:
        drawn = self._occ({"every": 45, "unit": "minutes", "count": 8}, self.start)
        self.assertEqual(len(drawn), 8)

    def test_runs_already_done_are_not_drawn_again(self) -> None:
        drawn = self._occ({"every": 45, "unit": "minutes", "count": 8},
                          self.start, runs=6)
        self.assertEqual(len(drawn), 2)
        self.assertEqual(drawn[0][0], 6, "the index is its number in the series")

    def test_an_end_date_stops_the_drawing(self) -> None:
        drawn = self._occ({"every": 1, "unit": "days",
                           "until": "2026-09-10T00:00:00Z"}, self.start)
        self.assertEqual(len(drawn), 5)

    def test_a_far_window_does_not_walk_every_step(self) -> None:
        # A daily task started a year ago must not cost 365 iterations to draw
        # today. The window is reached in one jump.
        far = self.start + timedelta(days=365)
        drawn = self._occ({"every": 1, "unit": "days"}, self.start,
                          window_start=far, window_end=far + timedelta(days=2))
        self.assertEqual(len(drawn), 3)
        self.assertEqual(drawn[0][0], 365)

    def test_a_task_that_does_not_repeat_is_drawn_once(self) -> None:
        from personalized_agent.scheduler.core.recurrence import occurrences

        self.assertEqual(len(occurrences({}, self.start)), 1)


class CalendarDrawsEveryRunTests(unittest.TestCase):
    """Section 52.3, through the API the calendar actually calls."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.port = _free_port()
        self.app.config.set("api_port", self.port)
        from personalized_agent.api.server import ApiServer

        self.server = ApiServer(self.app)
        self.server.start()
        self.base = f"http://127.0.0.1:{self.port}"
        self.start = utcnow().replace(second=0, microsecond=0) + timedelta(hours=1)

    def tearDown(self) -> None:
        self.server.stop()
        self.agent.cleanup()

    def _get(self, path):
        with urllib.request.urlopen(keyed(self.app, f"{self.base}{path}"), timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    def _repeating(self):
        return self.app.agent.tools.call("schedule_task", {
            "title": "Stand up and stretch", "description": "A short break.",
            "notify": "notify", "execution_type": "notification",
            "scheduled_at": self.start.isoformat(),
            "recurrence": {"every": 45, "unit": "minutes"}})["task_id"]

    def _window(self, hours=8):
        end = self.start + timedelta(hours=hours)
        return (f"?expand=1&limit=500&start={urllib.parse.quote(self.start.isoformat())}"
                f"&end={urllib.parse.quote(end.isoformat())}")

    def test_the_calendar_gets_a_block_for_every_run(self) -> None:
        self._repeating()
        blocks = self._get("/api/tasks" + self._window())["tasks"]
        # Eight hours at 45 minute spacing.
        self.assertEqual(len(blocks), 11)

    def test_only_one_of_them_is_the_stored_row(self) -> None:
        task_id = self._repeating()
        blocks = self._get("/api/tasks" + self._window())["tasks"]
        live = [b for b in blocks if b["id"] == task_id]
        self.assertEqual(len(live), 1)
        self.assertTrue(all(b["id"].startswith(task_id) for b in blocks))

    def test_the_drawn_blocks_say_they_have_not_run(self) -> None:
        self._repeating()
        blocks = self._get("/api/tasks" + self._window())["tasks"]
        drawn = [b for b in blocks if "#" in b["id"]]
        self.assertTrue(drawn)
        self.assertTrue(all(b["status"] == "scheduled" for b in drawn))

    def test_nothing_extra_is_stored(self) -> None:
        # The blocks are drawn from the rule. Storing thirty rows would be the
        # copying section 52.3 refuses.
        self._repeating()
        self._get("/api/tasks" + self._window())
        self.assertEqual(len(self.app.scheduler_store.list(limit=500)), 1)

    def test_without_expand_the_series_is_one_row(self) -> None:
        self._repeating()
        plain = self._get("/api/tasks?limit=500")["tasks"]
        self.assertEqual(len(plain), 1)

    def test_a_one_off_task_still_appears(self) -> None:
        self.app.agent.tools.call("schedule_task", {
            "title": "One-off check", "description": "Runs a single time.",
            "notify": "silent",
            "scheduled_at": (self.start + timedelta(hours=2)).isoformat()})
        blocks = self._get("/api/tasks" + self._window())["tasks"]
        self.assertTrue(any(b["title"] == "One-off check" for b in blocks))

    def _act(self, block_id: str, action: str, **extra) -> dict:
        request = urllib.request.Request(
            f"{self.base}/api/tasks/action",
            data=json.dumps({"id": block_id, "action": action, **extra}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(keyed(self.app, request), timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_cancelling_one_drawn_block_removes_only_that_run(self) -> None:
        # The "this event" half of the choice every calendar offers. Acting on
        # a block used to resolve to the series, so deleting Thursday's
        # reminder deleted Thursday's reminder forever.
        from personalized_agent.scheduler.core import series

        task_id = self._repeating()
        self._act(f"{task_id}#3", "cancel")

        task = self.app.scheduler_store.get(task_id)
        self.assertEqual(task.status, "pending", "the series was cancelled")
        self.assertTrue(series.is_skipped(task, 3), "that run is still drawn")

    def test_cancelling_with_scope_all_cancels_the_series(self) -> None:
        task_id = self._repeating()
        self._act(f"{task_id}#3", "cancel", scope="all")
        self.assertEqual(self.app.scheduler_store.get(task_id).status, "cancelled")

    def test_a_one_off_block_has_only_one_meaning(self) -> None:
        # Asking "this event or all events" about a task that happens once is
        # noise, so it is not asked.
        once = self.app.agent.tools.call("schedule_task", {
            "title": "One and done", "description": "Runs a single time.",
            "notify": "silent", "execution_type": "notification",
            "scheduled_at": (self.start + timedelta(hours=3)).isoformat()})["task_id"]

        self._act(once, "cancel")

        self.assertEqual(self.app.scheduler_store.get(once).status, "cancelled")


class ThreeMemoryKindsTests(unittest.TestCase):
    """Section 52.4, as amended: feedback is its own folder.

    The graph had three retrieval categories and four walks over them. The
    folders replaced the categories and the walks went with the graph, but the
    thing those tests were protecting did not: what the user thought of the
    agent still has to be kept apart from what the user is like, or an agent
    that cannot reach its own past misjudgements repeats them.
    """

    def test_feedback_is_its_own_folder(self) -> None:
        from personalized_agent.data_manage.library import (FOLDERS,
                                                            STATE_TASKS,
                                                            STATES,
                                                            USER_FEEDBACK,
                                                            USER_NATURE)

        # Section 34.3, as amended: what a run of states adds up to is its
        # own folder too. A state is a claim about a stretch of time; a
        # task is the claim that several of them are one thing, and it can
        # be wrong in a way no individual state is.
        # `others` and `agent-lesson` are retired: nothing writes or reads
        # either any more.
        self.assertEqual(FOLDERS, (USER_NATURE, USER_FEEDBACK, STATES,
                                   STATE_TASKS))


class NothingAsksFirstTests(unittest.TestCase):
    """Section 52.4, withdrawn: no task waits to be allowed. A task that asked
    first waited for a "run now" the calendar no longer offers - for ever.
    Every task runs at its time; one the user does not want is disabled or
    deleted."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = asking(self.agent.app)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _guarded(self, **over):
        fields = dict(
            title="Tidy the exports folder",
            description="Deletes old export files.",
            scheduled_at=utcnow(), execution_type="notification",
            task_payload={"approval": "ask_first", "message": "tidy up",
                          "notify_user": False},
            metadata={"rationale": "There are 40 stale exports."})
        fields.update(over)
        return self.app.scheduler.create_task(ScheduledTask(**fields))

    def test_a_task_that_says_ask_first_runs_at_its_time(self) -> None:
        task = self._guarded()
        self.assertNotIn("approval", task.task_payload)
        self.app.scheduler.tick()
        self.assertEqual(self.app.scheduler_store.get(task.id).status, "completed")
        self.assertIsNone(self.app.questions.pending_for(task.id, "approval"))

    def test_the_agent_is_not_offered_it(self) -> None:
        spec = next(one for one in self.app.agent.tools.specs
                    if one.name == "schedule_task")
        self.assertNotIn("approval", json.dumps(spec.input_schema))
        self.assertNotIn("ask_first", json.dumps(spec.input_schema))

    def test_nothing_fills_it_in(self) -> None:
        from personalized_agent.scheduler.core import completion

        self.assertNotIn("approval", completion.FILLABLE)
        self.assertNotIn("ask_first", inspect.getsource(completion))

    # What still knows how to settle a permission question, should one be
    # asked (`ask_permission` - called by nothing now).
    def _asked(self):
        task = self._guarded()
        self.app.scheduler.ask_permission(self.app.scheduler_store.get(task.id))
        return task, self.app.questions.pending_for(task.id, "approval")

    def test_the_question_carries_what_the_user_is_judging(self) -> None:
        _task, question = self._asked()
        self.assertIn("Tidy the exports folder", question.question)
        self.assertEqual(question.task_detail, "Deletes old export files.")
        self.assertIn("40 stale exports", question.arrangement)

    def test_approving_lets_it_run(self) -> None:
        task, question = self._asked()
        self.app.feedback.answer(question.id, "allow")
        self.app.scheduler.tick()
        self.assertEqual(self.app.scheduler_store.get(task.id).status, "completed")

    def test_deny_settles_it_rather_than_cancelling_it(self) -> None:
        task, question = self._asked()
        self.app.feedback.answer(question.id, "deny")
        self.assertEqual(self.app.scheduler_store.get(task.id).status, "completed")

    def test_delay_keeps_it_rather_than_deleting_it(self) -> None:
        task, question = self._asked()
        self.app.feedback.answer(question.id, "delay")
        self.assertEqual(self.app.scheduler_store.get(task.id).status, "pending")


class InterruptionAsksIfItWasWorthItTests(unittest.TestCase):
    """Section 52.4: an interruption asks whether it should have happened."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = asking(self.agent.app)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _notifying(self, notify=True):
        return self.app.scheduler.create_task(ScheduledTask(
            title="Stand up and stretch", description="A short break.",
            scheduled_at=utcnow(), execution_type="notification",
            task_payload={"notify_user": notify},
            metadata={"rationale": "Two hours at the desk."}))

    def test_a_notification_asks_afterwards(self) -> None:
        task = self._notifying()
        self.app.scheduler.tick()
        question = self.app.questions.pending_for(task.id, "review")
        self.assertIsNotNone(question)
        self.assertEqual(question.answers, ("acknowledged", "delay", "to_todo"))

    def test_wrong_moment_is_an_answer_and_never_again_is_the_calendars(self) -> None:
        # Most of what an agent gets wrong is timing, and collecting that as
        # plain disapproval teaches the wrong lesson - so "later" is offered.
        # "Stop reminding me" is not: "Got it" dismisses a reminder, and one
        # the user does not want again is disabled or deleted on the calendar.
        from personalized_agent.agent.feedback.templates import REMINDER, answers_for
        REVIEW_ANSWERS = answers_for(REMINDER)

        self.assertIn("delay", REVIEW_ANSWERS)
        self.assertNotIn("stop_asking", REVIEW_ANSWERS)

    def test_answering_writes_a_feedback_record(self) -> None:
        from personalized_agent.data_manage.library import USER_FEEDBACK

        task = self._notifying()
        self.app.scheduler.tick()
        question = self.app.questions.pending_for(task.id, "review")
        answered = self.app.feedback.answer(question.id, "delay",
                                            "I was mid-sentence.")
        record = self.app.library.get(answered.node_id)
        self.assertIsNotNone(record, "the answer was not written anywhere")
        self.assertEqual(record.folder, USER_FEEDBACK)

    def test_the_record_keeps_all_three_parts(self) -> None:
        # The answer without the reasoning says the user disliked something,
        # with no way to tell what about it.
        task = self._notifying()
        self.app.scheduler.tick()
        question = self.app.questions.pending_for(task.id, "review")
        answered = self.app.feedback.answer(question.id, "delay",
                                            "I was mid-sentence.")
        content = self.app.library.get(answered.node_id).body
        self.assertIn("A short break.", content)            # what it would do
        self.assertIn("Two hours at the desk.", content)    # why the agent did it
        self.assertIn("delay", content)                     # what the user said
        self.assertIn("I was mid-sentence.", content)       # what they added

    def test_a_refused_answer_is_not_accepted(self) -> None:
        task = self._notifying()
        self.app.scheduler.tick()
        question = self.app.questions.pending_for(task.id, "review")
        with self.assertRaises(ValueError):
            self.app.feedback.answer(question.id, "sort_of")

    def test_a_silent_task_does_not_ask(self) -> None:
        # Nobody was interrupted, so there is nothing to judge.
        task = self._notifying(notify=False)
        self.app.scheduler.tick()
        self.assertIsNone(self.app.questions.pending_for(task.id, "review"))


class TheAgentHeartbeatCanRepeatANotificationTests(unittest.TestCase):
    """Step 3 of the agent heartbeat may make a notification that repeats -
    the same rule the chat's schedule_task reads - and one whose repeat
    cannot be read is refused, not made to run once."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _add(self, **extra):
        return self.app.heartbeat._add_notifications("heartbeat_test", [dict({
            "type": "notice", "title": "Team meeting",
            "message": "The weekly team meeting starts in ten minutes.",
            "why": "The invitation read on screen says every Monday at 10.",
            "at": (utcnow() + timedelta(days=2)).isoformat()}, **extra)])

    def test_a_weekly_notice_until_a_date(self) -> None:
        until = (utcnow() + timedelta(days=60)).replace(microsecond=0)
        done = self._add(repeat={"every": 1, "unit": "weeks",
                                 "until": until.isoformat()})
        self.assertTrue(done[0]["ok"], done)
        self.assertIn("every week", done[0]["repeats"])
        task = self.app.scheduler_store.get(done[0]["task_id"])
        self.assertEqual(task.recurrence["every_seconds"], 7 * 86400)
        # Read back from storage, its end is still a date.
        self.assertEqual(task.recurrence["until"], until)

    def test_one_that_happens_once_has_no_repeat(self) -> None:
        done = self._add()
        self.assertTrue(done[0]["ok"], done)
        self.assertNotIn("repeats", done[0])

    def test_a_repeat_that_cannot_be_read_is_refused(self) -> None:
        done = self._add(repeat={"every": 1, "unit": "fortnights"})
        self.assertFalse(done[0]["ok"])
        self.assertIn("repeat could not be read", done[0]["error"])


class ScheduledTaskGetsAStateTests(unittest.TestCase):
    """Section 52.4: scheduling something is also a state on the calendar."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _schedule(self):
        # The heartbeat schedules through its third step, which adds
        # notifications.
        return self.app.heartbeat._add_notifications("heartbeat_test", [{
            "type": "reminder", "title": "Check the supplier quote",
            "message": "Look at whether they replied.",
            "why": "The quote was asked for on Monday.",
            "at": (utcnow() + timedelta(hours=3)).isoformat()}])

    def _state_for(self, task_id: str):
        """Found by the task it names, not by who happened to write it."""
        found = [s for s in self.app.states.between(
            utcnow() - timedelta(days=2), utcnow() + timedelta(days=2), limit=200)
            if s.task_id == task_id]
        self.assertEqual(len(found), 1, f"expected one state for {task_id}")
        return found[0]

    def test_a_state_accompanies_the_task(self) -> None:
        done = self._schedule()
        self.assertTrue(done[0]["ok"], done)
        self.assertIsNotNone(self._state_for(done[0]["task_id"]))

    def test_the_state_names_the_task_it_accompanies(self) -> None:
        # State.task_id was specified in section 34.1 and had never been set
        # by anything, so a state could claim a task while naming none.
        done = self._schedule()
        state = self._state_for(done[0]["task_id"])
        self.assertEqual(state.task_id, done[0]["task_id"])
        self.assertEqual(state.source, "task")

    def test_the_state_says_why_it_exists(self) -> None:
        done = self._schedule()
        self.assertIn("a task was scheduled",
                      self._state_for(done[0]["task_id"]).detail)

    def test_the_span_is_short(self) -> None:
        # "Something is queued", not "the agent is busy with this all week".
        done = self._schedule()
        self.assertLessEqual(self._state_for(done[0]["task_id"]).span_hours, 2)

    def test_every_way_of_making_a_task_writes_one(self) -> None:
        """The rule is "every task", so it lives in create_task.

        It used to live in the heartbeat, which meant a task made through the
        schedule tool or from the calendar had no state at all - and so was
        invisible to the half of every context that says what is true now.
        """
        made = {
            "the heartbeat": self._schedule()[0]["task_id"],
            "the schedule tool": self.app.agent.tools.call("schedule_task", {
                "title": "From the tool", "description": "d", "notify": "silent",
                "execution_type": "notification",
                "scheduled_at": (utcnow() + timedelta(hours=2)).isoformat()})["task_id"],
            "the calendar": self.app.scheduler.create_task(ScheduledTask(
                title="From the calendar", description="d",
                scheduled_at=utcnow() + timedelta(hours=2),
                execution_type="notification",
                metadata={"created_by": "calendar_ui"})).id,
        }
        for where, task_id in made.items():
            with self.subTest(made_by=where):
                self.assertIsNotNone(self._state_for(task_id))

    def test_a_state_does_not_imply_a_task(self) -> None:
        """The relation is one-way, and mostly empty in the other direction.

        Most states are things the heartbeat noticed, not things anybody
        scheduled - so a state with no task is the ordinary case rather than a
        missing link.
        """
        from personalized_agent.agent.states.store import State

        self.app.states.add(State(
            title="Quiet desk since 11:53", detail="Meeting, or lunch.",
            starts_at=utcnow() - timedelta(hours=1),
            ends_at=utcnow() + timedelta(hours=1)))
        loose = [s for s in self.app.states.covering(limit=100) if not s.task_id]
        self.assertTrue(loose, "a state must be able to stand on its own")

    def test_the_heartbeat_path_sets_what_a_notification_needs(self) -> None:
        # What the heartbeat schedules is a notification: it interrupts, it
        # is the kind it was asked to be, and it keeps the reason it gave.
        done = self.app.heartbeat._add_notifications("heartbeat_test", [{
            "type": "notice", "title": "Stretch", "message": "A break.",
            "why": "They have been sitting for hours.",
            "at": (utcnow() + timedelta(hours=1)).isoformat()}])
        task = self.app.scheduler_store.get(done[0]["task_id"])
        self.assertEqual(task.execution_type, "notification")
        self.assertTrue(task.task_payload["notify_user"])
        self.assertEqual(task.task_payload["template"], "passive")
        self.assertIn("sitting for hours", task.reason)

if __name__ == "__main__":
    unittest.main()


class TaskMustSayWhatItIsTests(unittest.TestCase):
    """Section 52.1: a task carries a title and a detail, or is not created.

    Substituting a placeholder for a missing title does not rescue the call.
    It produces a task nobody can identify, in a list where every such task
    looks the same, and the mistake is invisible from then on.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_agent_cannot_schedule_a_task_with_no_title(self) -> None:
        answer = self.app.agent.tools.call("schedule_task", {
            "description": "something", "scheduled_at": "2026-10-01T11:00:00Z"})
        self.assertIn("title", answer.get("error", ""))
        self.assertEqual(self.app.scheduler.store.list(limit=10), [])

    def test_the_agent_cannot_schedule_a_task_with_no_detail(self) -> None:
        answer = self.app.agent.tools.call("schedule_task", {
            "title": "Review the numbers", "scheduled_at": "2026-10-01T11:00:00Z"})
        self.assertIn("description", answer.get("error", ""))
        self.assertEqual(self.app.scheduler.store.list(limit=10), [])

    def test_the_refusal_says_what_is_wanted(self) -> None:
        # The model can fix a call it is told how to fix.
        answer = self.app.agent.tools.call("schedule_task", {
            "scheduled_at": "2026-10-01T11:00:00Z"})
        self.assertIn("tell it from every other task", answer.get("error", ""))

    def test_a_task_with_both_is_created(self) -> None:
        answer = self.app.agent.tools.call("schedule_task", {
            "title": "Review the quarterly numbers",
            "description": "Read the figures and flag anything odd before Friday.",
            "notify": "silent",           # section 52.2: and which it is
            "scheduled_at": "2026-10-01T11:00:00Z"})
        self.assertIn("task_id", answer)
        task = self.app.scheduler.store.get(answer["task_id"])
        self.assertEqual(task.title, "Review the quarterly numbers")
        self.assertTrue(task.description)

    def test_a_blank_title_is_not_a_title(self) -> None:
        answer = self.app.agent.tools.call("schedule_task", {
            "title": "   ", "description": "   ",
            "scheduled_at": "2026-10-01T11:00:00Z"})
        self.assertIn("error", answer)

    def test_both_are_required_by_the_tool_definition(self) -> None:
        spec = next(s for s in self.app.agent.tools.specs if s.name == "schedule_task")
        required = spec.input_schema["required"]
        self.assertIn("title", required)
        self.assertIn("description", required)

    def test_the_heartbeat_refuses_a_nameless_task(self) -> None:
        performed = self.app.heartbeat._add_notifications("task_1", [
            {"type": "reminder", "at": "2026-10-01T11:00:00Z"}])
        self.assertFalse(performed[0]["ok"])
        self.assertIn("title", performed[0]["error"])
        self.assertEqual(self.app.scheduler.store.list(limit=10), [])

    def test_the_heartbeat_schedules_one_that_says_what_it_is(self) -> None:
        performed = self.app.heartbeat._add_notifications("task_1", [{
            "type": "reminder", "title": "Remind about the report",
            "message": "The quarterly report is due and has not been opened.",
            "at": (utcnow() + timedelta(hours=1)).isoformat(),
            "why": "The report is due today and was not opened."}])
        self.assertTrue(performed[0]["ok"])
        task = self.app.scheduler.store.get(performed[0]["task_id"])
        self.assertEqual(task.title, "Remind about the report")
        self.assertTrue(task.description)

    def test_nothing_invents_a_placeholder_title(self) -> None:
        # The two strings that used to stand in for a name.
        for path in ("personalized_agent/agent/core/tools.py",
                     "personalized_agent/heartbeat/core/heartbeat.py"):
            source = (ROOT / path).read_text(encoding="utf-8")
            self.assertNotIn('or "Scheduled task"', source)
            self.assertNotIn('or "Heartbeat task"', source)

    def test_the_detail_is_shown_even_when_it_is_missing(self) -> None:
        # An empty detail rendering as empty space is invisible, which is
        # exactly how a task with nothing to say went unnoticed.
        script = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text(
            encoding="utf-8")
        self.assertIn("No detail was written for this task", script)


class ThreeWaysARunCanEndTests(unittest.TestCase):
    """Section 59, as amended: a run that did not fail has three endings.

    "It ran" and "it is finished" are different claims. Before this, every
    clean return meant done, so a task that had only started something was
    closed underneath it.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.scheduler = self.app.scheduler
        self.store = self.app.scheduler_store

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _task(self, **overrides) -> ScheduledTask:
        fields = {
            "title": "build the thing",
            "description": "kick off the build and see it through",
            "scheduled_at": utcnow() - timedelta(seconds=1),
            "execution_type": "notification",
            "task_payload": {"message": "go", "notify_user": False},
        }
        fields.update(overrides)
        return self.scheduler.create_task(ScheduledTask(**fields))

    def _returns(self, outcome) -> None:
        """Make the executor hand back one fixed outcome."""
        self.app.scheduler.executor.execute = lambda task: outcome

    def _status(self, task_id: str) -> str:
        return self.store.get(task_id).status

    # -- completed ----------------------------------------------------------
    def test_a_finished_task_is_completed(self) -> None:
        from personalized_agent.scheduler.execution.executors import DONE, ExecutionOutcome

        task = self._task()
        self._returns(ExecutionOutcome(True, "sent", disposition=DONE))
        result = self.scheduler.tick()[0]

        self.assertEqual(result["status"], "completed")
        self.assertEqual(self._status(task.id), "completed")

    def test_completed_is_still_the_default(self) -> None:
        # Every existing executor returns ExecutionOutcome(True, ...) with no
        # disposition, and must go on meaning "done".
        from personalized_agent.scheduler.execution.executors import ExecutionOutcome

        task = self._task()
        self._returns(ExecutionOutcome(True, "sent"))
        self.scheduler.tick()
        self.assertEqual(self._status(task.id), "completed")

    # -- delayed ------------------------------------------------------------
    def test_a_delayed_task_goes_back_on_the_calendar(self) -> None:
        from personalized_agent.scheduler.execution.executors import DELAYED, ExecutionOutcome

        task = self._task()
        later = utcnow() + timedelta(hours=2)
        self._returns(ExecutionOutcome(True, "not yet", disposition=DELAYED,
                                       next_attempt_at=later))
        result = self.scheduler.tick()[0]

        self.assertEqual(result["status"], "delayed")
        stored = self.store.get(task.id)
        self.assertEqual(stored.status, "pending", "it is queued again, not finished")
        self.assertAlmostEqual(stored.scheduled_at.timestamp(), later.timestamp(), delta=2)

    def test_a_delay_is_not_a_failure(self) -> None:
        # fail(retry_at=...) moves the row the same way and means the opposite.
        # A task that is repeatedly not-yet must not be retired by the retry
        # policy as though it were repeatedly broken.
        from personalized_agent.scheduler.execution.executors import DELAYED, ExecutionOutcome

        task = self._task()
        self._returns(ExecutionOutcome(True, "not yet", disposition=DELAYED,
                                       next_attempt_at=utcnow() + timedelta(hours=1)))
        self.scheduler.tick()
        stored = self.store.get(task.id)

        self.assertEqual(stored.attempts, 0, "a delay does not count as an attempt")
        self.assertFalse(stored.last_error, "a delay records no error")

    def test_a_delayed_task_is_not_due_again_immediately(self) -> None:
        from personalized_agent.scheduler.execution.executors import DELAYED, ExecutionOutcome

        self._task()
        self._returns(ExecutionOutcome(True, "not yet", disposition=DELAYED,
                                       next_attempt_at=utcnow() + timedelta(hours=1)))
        self.scheduler.tick()
        self.assertEqual(self.scheduler.tick(), [], "it must not run again straight away")

    # -- to be checked: there are no checks any more ------------------------
    def test_an_unsure_task_is_never_run_again(self) -> None:
        # Running it twice is the exact harm that being unsure threatens. It
        # is not parked to be checked (nothing checks now) - it is settled.
        from personalized_agent.scheduler.execution.executors import (
            TO_BE_CHECKED, ExecutionOutcome)

        self._task()
        self._returns(ExecutionOutcome(True, "started", disposition=TO_BE_CHECKED))
        self.scheduler.tick()
        self.assertEqual(self.scheduler.tick(), [], "it must not be picked up again")

    # -- the invariant that ties them together ------------------------------
    def test_only_a_delay_holds_back_a_repeating_task(self) -> None:
        """A delayed run is the same occurrence, still owed.

        Advancing the recurrence for it would skip a run the user asked for,
        and nothing would ever say so. A run that could not confirm itself
        used to be held back too, waiting on a check; with no checks, a
        delivered notification is the end of its occurrence, and the series
        moves on exactly as it does for one that finished.
        """
        from personalized_agent.scheduler.execution.executors import (
            DELAYED, DONE, TO_BE_CHECKED, ExecutionOutcome)

        from personalized_agent.scheduler.core.recurrence import parse_recurrence

        daily = parse_recurrence({"every": 1, "unit": "days"})
        task = self._task(recurrence=daily)
        self._returns(ExecutionOutcome(True, "...", disposition=DELAYED,
                                       next_attempt_at=utcnow() + timedelta(hours=1)))
        self.scheduler.tick()
        self.assertEqual(self.store.get(task.id).runs, 0,
                         "the occurrence is not over, so it has not run")

        for disposition in (DONE, TO_BE_CHECKED):
            with self.subTest(disposition=disposition):
                task = self._task(recurrence=daily)
                self._returns(ExecutionOutcome(True, "done", disposition=disposition))
                self.scheduler.tick()
                stored = self.store.get(task.id)
                self.assertEqual(stored.runs, 1, "finishing is what advances it")
                self.assertEqual(stored.status, "pending",
                                 "the next occurrence is on the calendar")


class AnsweringIsNotAnEndingTests(unittest.TestCase):
    """A held task is paused, not finished.

    Waiting for approval means the task stopped and someone else decides;
    releasing it hands it back to end the way any run ends. (Nothing asks
    first any more - `ask_permission` is called by nothing - but the answers
    to such a question are still honoured, and must still mean this.)
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = asking(self.agent.app)
        self.store = self.app.scheduler_store

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _held(self) -> ScheduledTask:
        task = self.app.scheduler.create_task(ScheduledTask(
            title="tidy the exports",
            description="delete the files nobody opened",
            scheduled_at=utcnow() - timedelta(seconds=1),
            execution_type="notification",
            task_payload={"message": "go", "notify_user": False}))
        self.app.scheduler.ask_permission(task)
        self.assertEqual(self.store.get(task.id).status, "awaiting_approval")
        return task

    def _question(self, task_id: str):
        from personalized_agent.agent.feedback.store import KIND_PERMISSION
        question = self.app.questions.pending_for(task_id, KIND_PERMISSION)
        self.assertIsNotNone(question, "the task should have asked")
        return question

    def test_allow_releases_the_task_rather_than_finishing_it(self) -> None:
        from personalized_agent.scheduler.execution.executors import (
            DELAYED, ExecutionOutcome)

        task = self._held()
        self.app.feedback.answer(self._question(task.id).id, "allow")
        self.assertEqual(self.store.get(task.id).status, "pending",
                         "approval releases it; it has not run yet")

        # It runs, and what it returns is what decides - exactly as for a task
        # nobody was ever asked about. Here the run says "not yet", so the
        # task is back on the calendar rather than finished by the "allow".
        later = utcnow() + timedelta(hours=1)
        self.app.scheduler.executor.execute = lambda t: ExecutionOutcome(
            True, "not yet", disposition=DELAYED, next_attempt_at=later)
        self.app.scheduler.tick()
        stored = self.store.get(task.id)
        self.assertEqual(stored.status, "pending")
        self.assertAlmostEqual(stored.scheduled_at.timestamp(), later.timestamp(),
                               delta=2)

    def test_delay_moves_the_clock(self) -> None:
        task = self._held()
        self.app.feedback.answer(self._question(task.id).id, "delay")

        stored = self.store.get(task.id)
        self.assertEqual(stored.status, "pending")
        self.assertGreater(stored.scheduled_at, utcnow(),
                           "'not now' has to mean later, or it means nothing")

    def test_delay_does_not_ask_again_on_the_next_tick(self) -> None:
        # The whole point: declining once must not produce a task that pesters.
        task = self._held()
        self.app.feedback.answer(self._question(task.id).id, "delay")

        self.app.scheduler.tick()

        self.assertIsNone(self._pending_question(task.id),
                          "it asked again within the minute")

    def _pending_question(self, task_id: str):
        from personalized_agent.agent.feedback.store import KIND_PERMISSION
        return self.app.questions.pending_for(task_id, KIND_PERMISSION)

    def test_deny_ends_the_task(self) -> None:
        task = self._held()
        self.app.feedback.answer(self._question(task.id).id, "deny")
        self.assertEqual(self.store.get(task.id).status, "completed",
                         "settled, not cancelled - the user decided")


class TheVerificationPlanIsMadeWhenTheTaskIsTests(unittest.TestCase):
    """Section 59, as amended twice: the plan is written with the task.

    It used to decide when a check of an unsure task fell due, and how long an
    unfinished one waited. There are no checks now - only notifications run,
    and an unsure one is settled - so what is left of the plan is the one
    thing that still reads it: how long "later" means for this task, which the
    delay button offers (see `test_the_interval_comes_from_the_tasks_own_plan`).
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.store = self.app.scheduler_store

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_nonsense_in_a_plan_falls_back_to_the_defaults(self) -> None:
        from personalized_agent.scheduler.execution.executors import (
            DEFAULT_CHECK_AFTER_SECONDS, DEFAULT_RECHECK_AFTER_SECONDS,
            VERIFICATION, verification_plan)

        task = self.app.scheduler.create_task(ScheduledTask(
            title="build the thing", description="kick off the build",
            scheduled_at=utcnow() + timedelta(hours=1),
            execution_type="notification",
            task_payload={"notify_user": False,
                          VERIFICATION: {"check_after_seconds": "soon-ish",
                                         "recheck_after_seconds": None}}))
        plan = verification_plan(self.store.get(task.id))
        self.assertEqual(plan["check_after_seconds"], DEFAULT_CHECK_AFTER_SECONDS)
        self.assertEqual(plan["recheck_after_seconds"], DEFAULT_RECHECK_AFTER_SECONDS)

    # -- it reaches the task through the tool the agent actually uses -------
    def test_the_schedule_tool_records_a_plan(self) -> None:
        from personalized_agent.scheduler.execution.executors import VERIFICATION

        outcome = self.app.agent.tools.call("schedule_task", {
            "title": "Kick off the nightly build",
            "description": "Start it and confirm it finished.",
            "notify": "silent",
            "scheduled_at": utcnow().isoformat(),
            "verification": {"completed_when": "the build log ends with SUCCESS",
                             "check_after_seconds": 900,
                             "recheck_after_seconds": 3600}})
        self.assertIn("task_id", outcome, outcome)

        stored = self.store.get(outcome["task_id"])
        plan = stored.task_payload[VERIFICATION]
        self.assertEqual(plan["completed_when"], "the build log ends with SUCCESS")
        self.assertEqual(plan["check_after_seconds"], 900)
        self.assertEqual(plan["recheck_after_seconds"], 3600)


class AnInterruptionCanBeAnsweredTests(unittest.TestCase):
    """Section 52.6, as amended: a notification the agent raises is a template.

    A notification used to be a sentence and nothing else. The user could
    read it and that was all - there was no way to say "not now", and no way
    at all to say "stop". Saying nothing is indistinguishable from not having
    seen it, so the agent went on raising it.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = asking(self.agent.app)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _raise(self, **kwargs):
        return self.app.notifications.notify(
            kwargs.pop("title", "Time to stand up"),
            kwargs.pop("message", "You have been sitting for 45 minutes."),
            kind="proactive", **kwargs)

    def _questions(self):
        return self.app.questions.pending(limit=50)

    def test_a_proactive_notification_arrives_with_a_way_to_answer(self) -> None:
        from personalized_agent.agent.feedback.templates import REMINDER

        self._raise(template=REMINDER, question={"task_id": "task_rest"})
        pending = self._questions()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].kind, REMINDER)
        self.assertEqual([o["key"] for o in pending[0].options],
                         ["acknowledged", "delay", "to_todo"])

    def test_a_passive_notice_offers_only_ok(self) -> None:
        from personalized_agent.agent.feedback.templates import PASSIVE

        self._raise(template=PASSIVE, question={"task_id": "task_note"})
        pending = self._questions()
        # OK, or onto the to-do list - each saying what happens.
        self.assertEqual([o["key"] for o in pending[0].options], ["ok", "to_todo"])
        self.assertIn("no more notifications", pending[0].options[0]["label"])

    def test_the_notification_records_which_template_it_is(self) -> None:
        from personalized_agent.agent.feedback.templates import PASSIVE

        notification = self._raise(template=PASSIVE,
                                   question={"task_id": "task_note"})
        self.assertEqual(notification.metadata.get("template"), PASSIVE)

    def test_a_template_nobody_named_is_a_reminder(self) -> None:
        # Not refused: losing the interruption is worse than choosing for it.
        from personalized_agent.agent.feedback.templates import REMINDER

        notification = self._raise(question={"task_id": "task_x"})
        self.assertEqual(notification.metadata.get("template"), REMINDER)
        self.assertEqual(self._questions()[0].kind, REMINDER)

    def test_a_template_that_is_not_one_of_them_is_a_reminder(self) -> None:
        from personalized_agent.agent.feedback.templates import REMINDER

        notification = self._raise(template="urgent!",
                                   question={"task_id": "task_y"})
        self.assertEqual(notification.metadata.get("template"), REMINDER)

    def test_the_old_names_still_resolve(self) -> None:
        # A caller still passing "approval" means a permission.
        from personalized_agent.agent.feedback.templates import PERMISSION

        notification = self._raise(template="approval",
                                   question={"task_id": "task_z"})
        self.assertEqual(notification.metadata.get("template"), PERMISSION)

    def test_one_interruption_asks_one_question(self) -> None:
        # A repeating reminder that fires again while the last is unanswered
        # must not stack a second identical question.
        from personalized_agent.agent.feedback.templates import REMINDER

        for _ in range(3):
            self._raise(template=REMINDER, question={"task_id": "task_rest"},
                        force=True)
        self.assertEqual(len(self._questions()), 1)

    def test_the_notification_points_at_the_question(self) -> None:
        """Without this the popup has buttons that do nothing.

        The bubble beside the character already knew how to read a question id
        off a notification and offer that question's own options. Nothing had
        ever written one, so every interruption fell through to a single
        "Got it" with no key behind it - a button that closed the bubble,
        recorded nothing, and left the task exactly where it was.
        """
        from personalized_agent.agent.feedback.templates import PASSIVE

        notification = self._raise(template=PASSIVE,
                                   question={"task_id": "task_note"})
        question_id = notification.metadata.get("question_id")
        self.assertTrue(question_id, "the popup has no question to answer")
        question = self.app.questions.get(question_id)
        self.assertIsNotNone(question)
        # what the bubble puts on its buttons
        self.assertEqual([o["key"] for o in question.options], ["ok", "to_todo"])

    def test_a_second_notification_points_at_the_standing_question(self) -> None:
        # One interruption asks one question, but the later notification is
        # still answerable - it points at the one already waiting rather than
        # at nothing.
        from personalized_agent.agent.feedback.templates import REMINDER

        first = self._raise(template=REMINDER, question={"task_id": "task_rest"},
                            force=True)
        second = self._raise(template=REMINDER, question={"task_id": "task_rest"},
                             force=True)
        self.assertTrue(second.metadata.get("question_id"))
        self.assertEqual(second.metadata["question_id"],
                         first.metadata["question_id"])

    def test_a_system_warning_carries_no_question(self) -> None:
        notification = self.app.notifications.notify(
            "Model unavailable", "Open Settings > Models.", kind="warning")
        self.assertNotIn("question_id", notification.metadata)

    def test_a_system_warning_is_not_dressed_as_a_question(self) -> None:
        # Only what the agent chose to raise is judged. A model being
        # unavailable is not a decision the user is asked to weigh in on.
        self.app.notifications.notify(
            "Model unavailable", "Open Settings > Models.", kind="warning")
        self.assertEqual(self._questions(), [])

    def test_the_notice_still_arrives_when_nothing_can_ask(self) -> None:
        # Degraded, and deliberately so: an interruption the user cannot
        # answer beats one they never receive.
        from personalized_agent.agent.feedback.templates import PASSIVE

        self.app.notifications.questions = None
        self.assertIsNotNone(self._raise(template=PASSIVE))

    def test_answering_ok_closes_the_task(self) -> None:
        """"Selecting it closes it" - the one thing the passive option does."""
        from personalized_agent.agent.feedback.templates import PASSIVE

        task = self.app.scheduler_store.create(ScheduledTask(
            title="A thing worth saying once",
            description="nothing to decide",
            scheduled_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            execution_type="notification"))
        self._raise(template=PASSIVE, question={"task_id": task.id})
        question = self._questions()[0]
        self.app.feedback.answer(question.id, "ok", "")
        self.assertEqual(self.app.scheduler_store.get(task.id).status, "completed")


class EveryQuestionComesFromATemplateTests(unittest.TestCase):
    """Section 52.4, as amended: three templates, and no fourth.

    An option is a label *and* a consequence, declared together. When the two
    lived apart, a button's label could quietly stop being true - which is a
    worse failure than a wrong button, because the user cannot see it.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = asking(self.agent.app)
        self.store = self.app.scheduler_store

    def tearDown(self) -> None:
        self.agent.cleanup()

    # -- the two templates, and what each option does -----------------------
    def test_a_reminder_offers_two_things(self) -> None:
        # No "Stop reminding me": "Got it" dismisses a reminder, and one the
        # user does not want again is disabled or deleted on the calendar.
        from personalized_agent.agent.feedback.templates import (
            DELAY, REMINDER, TO_CHECK, answers_for, effect_of)

        # And onto the to-do list: no more notifications, kept to be done.
        self.assertEqual(answers_for(REMINDER), ("acknowledged", "delay", "to_todo"))
        self.assertEqual(effect_of(REMINDER, "acknowledged"), TO_CHECK)
        self.assertEqual(effect_of(REMINDER, "delay"), DELAY)
        self.assertEqual(effect_of(REMINDER, "to_todo"), TO_CHECK)
        self.assertIsNone(effect_of(REMINDER, "stop_asking"))

    def test_a_permission_offers_four(self) -> None:
        from personalized_agent.agent.feedback.templates import (
            COMPLETE, DELAY, PERMISSION, RUN, answers_for, effect_of)

        self.assertEqual(answers_for(PERMISSION),
                         ("allow", "deny", "delay", "stop_asking", "to_todo"))
        self.assertEqual(effect_of(PERMISSION, "allow"), RUN)
        self.assertEqual(effect_of(PERMISSION, "deny"), COMPLETE)
        self.assertEqual(effect_of(PERMISSION, "delay"), DELAY)
        self.assertEqual(effect_of(PERMISSION, "stop_asking"), COMPLETE)

    def test_a_passive_notice_offers_one(self) -> None:
        """Something said, with nothing asked - but still something to dismiss.

        Without it a note the agent only wanted to say had to borrow a
        reminder's buttons and ask "shall I remind you again?" about a
        sentence that needed no answer.
        """
        from personalized_agent.agent.feedback.templates import (
            COMPLETE, PASSIVE, answers_for, effect_of, options_for)

        self.assertEqual(answers_for(PASSIVE), ("ok", "to_todo"))
        self.assertEqual(effect_of(PASSIVE, "ok"), COMPLETE)
        self.assertEqual([o.label for o in options_for(PASSIVE)][0],
                         "OK (no more notifications)")

    def test_there_is_no_fourth_template(self) -> None:
        from personalized_agent.agent.feedback.templates import (
            ALL_TEMPLATES, PASSIVE, PERMISSION, REMINDER, TEMPLATES)

        self.assertEqual(set(TEMPLATES), {REMINDER, PERMISSION, PASSIVE})
        self.assertEqual(set(ALL_TEMPLATES), set(TEMPLATES),
                         "the list a notification chooses from is the list "
                         "of templates, not a copy that can drift from it")

    def test_every_option_declares_an_effect(self) -> None:
        from personalized_agent.agent.feedback.templates import (
            COMPLETE, DELAY, RUN, TEMPLATES, TO_CHECK)

        for template, options in TEMPLATES.items():
            for option in options:
                with self.subTest(template=template, option=option.key):
                    self.assertIn(option.effect, (TO_CHECK, DELAY, COMPLETE, RUN))
                    self.assertTrue(option.label, "a button with no label")
                    self.assertTrue(option.meaning, "nothing to read back later")

    # -- the delay option carries the agent's own interval ------------------
    def test_the_delay_option_says_how_long(self) -> None:
        from personalized_agent.agent.feedback.templates import REMINDER, options_for

        soon = {o.key: o for o in options_for(REMINDER, 600)}["delay"]
        later = {o.key: o for o in options_for(REMINDER, 3 * 86400)}["delay"]

        self.assertEqual(soon.delay_seconds, 600)
        self.assertIn("10 minute", soon.label)
        self.assertIn("3 day", later.label)
        self.assertNotEqual(soon.label, later.label,
                            "the same button must not say the same thing for "
                            "ten minutes and three days")

    def test_the_interval_comes_from_the_tasks_own_plan(self) -> None:
        from personalized_agent.agent.feedback.store import KIND_PERMISSION
        from personalized_agent.scheduler.execution.executors import VERIFICATION

        task = self.app.scheduler.create_task(ScheduledTask(
            title="Tidy the exports", description="Deletes old files.",
            scheduled_at=utcnow() - timedelta(seconds=1),
            execution_type="notification",
            task_payload={"notify_user": False,
                          VERIFICATION: {"recheck_after_seconds": 7200}}))
        self.app.scheduler.ask_permission(task)

        question = self.app.questions.pending_for(task.id, KIND_PERMISSION)
        delay = [o for o in question.options if o["key"] == "delay"][0]
        self.assertEqual(delay["delay_seconds"], 7200)
        self.assertIn("2 hour", delay["label"])

    def test_the_interval_the_user_pressed_is_the_one_applied(self) -> None:
        # Recomputing it at answer time would apply a number the user never saw.
        from personalized_agent.agent.feedback.store import KIND_PERMISSION
        from personalized_agent.scheduler.execution.executors import VERIFICATION

        task = self.app.scheduler.create_task(ScheduledTask(
            title="Tidy the exports", description="Deletes old files.",
            scheduled_at=utcnow() - timedelta(seconds=1),
            execution_type="notification",
            task_payload={"notify_user": False,
                          VERIFICATION: {"recheck_after_seconds": 7200}}))
        self.app.scheduler.ask_permission(task)
        question = self.app.questions.pending_for(task.id, KIND_PERMISSION)

        self.app.feedback.answer(question.id, "delay")

        waited = (self.store.get(task.id).scheduled_at - utcnow()).total_seconds()
        self.assertAlmostEqual(waited, 7200, delta=120)

    # -- every answer is remembered -----------------------------------------
    def test_every_answer_becomes_a_feedback_node(self) -> None:
        from personalized_agent.agent.feedback.store import KIND_PERMISSION
        from personalized_agent.data_manage.library import USER_FEEDBACK

        for answer in ("allow", "deny", "delay", "stop_asking"):
            with self.subTest(answer=answer):
                task = self.app.scheduler.create_task(ScheduledTask(
                    title=f"Task for {answer}", description="Does something.",
                    scheduled_at=utcnow() - timedelta(seconds=1),
                    execution_type="notification",
                    task_payload={"notify_user": False}))
                self.app.scheduler.ask_permission(task)
                question = self.app.questions.pending_for(task.id, KIND_PERMISSION)

                answered = self.app.feedback.answer(question.id, answer)

                self.assertTrue(answered.node_id, "no feedback node was written")
                node = self.app.library.get(answered.node_id)
                self.assertEqual(node.type, USER_FEEDBACK)
                self.assertEqual(node.metadata.get("answer"), answer)
                self.assertTrue(node.metadata.get("effect"),
                                "the node should record what the answer did")

    # -- acknowledging a reminder settles it ---------------------------------
    def test_acknowledging_a_reminder_settles_it(self) -> None:
        """"Got it" is taken as said, and nothing is scheduled to check it.

        It used to park the task and put a check on the calendar - a task
        that runs a model. Only notifications run now, so what the user said
        they would do is taken as said and the reminder is done with.
        """
        from personalized_agent.agent.feedback.store import KIND_REMINDER
        from personalized_agent.scheduler.execution.executors import CHECK_STATUS

        task = self.app.scheduler.create_task(ScheduledTask(
            title="Send the invoice", description="It is due today.",
            scheduled_at=utcnow() - timedelta(seconds=1),
            execution_type="notification",
            task_payload={"message": "Send the invoice", "notify_user": True}))
        self.app.scheduler.tick()
        question = self.app.questions.pending_for(task.id, KIND_REMINDER)
        self.assertIsNotNone(question, "an interruption should have asked")

        self.app.feedback.answer(question.id, "acknowledged")

        self.assertEqual(self.store.get(task.id).status, "completed")
        self.assertEqual([t for t in self.store.list(limit=100)
                          if t.execution_type == CHECK_STATUS], [],
                         "a check was put on the calendar")

    def test_stop_asking_a_permission_ends_the_task(self) -> None:
        # "Stop asking" left the reminder; on a permission question it still
        # finishes the task - settled, not cancelled.
        from personalized_agent.agent.feedback.store import KIND_PERMISSION

        task = self.app.scheduler.create_task(ScheduledTask(
            title="Stretch", description="Stand up for a minute.",
            scheduled_at=utcnow() - timedelta(seconds=1),
            execution_type="notification",
            task_payload={"message": "Stretch", "notify_user": False}))
        self.app.scheduler.ask_permission(task)
        question = self.app.questions.pending_for(task.id, KIND_PERMISSION)

        self.app.feedback.answer(question.id, "stop_asking")

        self.assertEqual(self.store.get(task.id).status, "completed",
                         "settled, not cancelled - the user decided")

    # -- questions written before the templates ------------------------------
    def test_a_question_stored_under_the_old_name_is_still_answerable(self) -> None:
        # Upgrading must not strand a queue of questions with no buttons.
        from personalized_agent.agent.feedback.store import KIND_PERMISSION

        task = self.app.scheduler.create_task(ScheduledTask(
            title="An old one", description="Scheduled before the templates.",
            scheduled_at=utcnow() - timedelta(seconds=1),
            execution_type="notification",
            task_payload={"notify_user": False}))
        self.app.scheduler.ask_permission(task)
        question = self.app.questions.pending_for(task.id, KIND_PERMISSION)
        self.app.db.execute(
            "UPDATE task_questions SET kind = 'approval', metadata = '{}' WHERE id = ?",
            (question.id,))

        revived = self.app.questions.get(question.id)
        self.assertEqual(revived.answers,
                         ("allow", "deny", "delay", "stop_asking", "to_todo"))
        self.assertIsNotNone(self.app.questions.pending_for(task.id, KIND_PERMISSION),
                             "the old row is invisible to the new name")

    def test_an_answer_outside_the_template_is_refused(self) -> None:
        from personalized_agent.agent.feedback.store import KIND_PERMISSION

        task = self.app.scheduler.create_task(ScheduledTask(
            title="Tidy up", description="Deletes old files.",
            scheduled_at=utcnow() - timedelta(seconds=1),
            execution_type="notification",
            task_payload={"notify_user": False}))
        self.app.scheduler.ask_permission(task)
        question = self.app.questions.pending_for(task.id, KIND_PERMISSION)

        with self.assertRaises(ValueError):
            self.app.feedback.answer(question.id, "maybe")


class ThisEventOrAllEventsTests(unittest.TestCase):
    """Section 60, as amended: a calendar block can be acted on by itself.

    Every calendar asks "this event or all events", and until now there was
    only the second: every block resolved to the series, so deleting Thursday
    deleted Thursday forever.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.store = self.app.scheduler_store

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _daily(self, **over) -> ScheduledTask:
        from personalized_agent.scheduler.core.recurrence import parse_recurrence
        fields = dict(
            title="Stand up and stretch", description="A short break.",
            scheduled_at=utcnow() + timedelta(hours=1),
            execution_type="notification",
            task_payload={"message": "stretch", "notify_user": False},
            recurrence=parse_recurrence({"every": 1, "unit": "days"}))
        fields.update(over)
        return self.app.scheduler.create_task(ScheduledTask(**fields))

    def _blocks(self, task, days=7):
        """What the calendar would draw for the next few days."""
        from personalized_agent.scheduler.core import series
        from personalized_agent.scheduler.core.recurrence import occurrences
        anchor = series.anchor_of(task)
        end = utcnow() + timedelta(days=days)
        out = []
        for index, moment in occurrences(task.recurrence, anchor, 0, None, None):
            if index < task.runs or moment > end:
                continue
            if series.is_skipped(task, index):
                continue
            entry = series.override_for(task, index)
            out.append((index, parse_iso(entry.get("scheduled_at")) or moment))
        return out

    # -- deleting one -------------------------------------------------------
    def test_deleting_one_block_leaves_the_rest(self) -> None:
        task = self._daily()
        before = self._blocks(task)
        self.assertGreater(len(before), 3)
        victim = before[2][0]

        self.app.scheduler.skip_occurrence(task.id, victim)

        after = self._blocks(self.store.get(task.id))
        self.assertNotIn(victim, [i for i, _ in after])
        self.assertEqual(len(after), len(before) - 1)

    def test_the_series_itself_survives(self) -> None:
        task = self._daily()
        self.app.scheduler.skip_occurrence(task.id, task.runs + 2)
        self.assertEqual(self.store.get(task.id).status, "pending")

    def test_deleting_the_next_run_moves_the_row_past_it(self) -> None:
        """Otherwise the deletion is undone by the very next tick.

        Marking the live occurrence skipped without advancing the row leaves
        the task due, and the scheduler runs the thing just deleted.
        """
        task = self._daily(scheduled_at=utcnow() - timedelta(seconds=1))
        first = task.runs

        self.app.scheduler.skip_occurrence(task.id, first)

        moved = self.store.get(task.id)
        self.assertEqual(moved.runs, first + 1, "the row still points at the deleted run")
        self.assertGreater(moved.scheduled_at, utcnow())
        self.assertEqual(self.app.scheduler.tick(), [],
                         "the deleted occurrence ran anyway")

    def test_several_deletions_in_a_row_are_all_honoured(self) -> None:
        # Advancing by one would land on another the user also deleted.
        task = self._daily(scheduled_at=utcnow() - timedelta(seconds=1))
        first = task.runs
        for index in (first, first + 1, first + 2):
            self.app.scheduler.skip_occurrence(task.id, index)

        moved = self.store.get(task.id)
        self.assertEqual(moved.runs, first + 3)
        self.assertEqual(self.app.scheduler.tick(), [])

    def test_deleting_one_of_a_one_off_task_cancels_it(self) -> None:
        # "This occurrence" and "the whole thing" are the same claim.
        task = self.app.scheduler.create_task(ScheduledTask(
            title="Once", description="Just the once.",
            scheduled_at=utcnow() + timedelta(hours=1),
            execution_type="notification",
            task_payload={"message": "hi", "notify_user": False}))
        self.app.scheduler.skip_occurrence(task.id, None)
        self.assertEqual(self.store.get(task.id).status, "cancelled")

    # -- moving one ---------------------------------------------------------
    def test_moving_one_block_does_not_drag_the_others(self) -> None:
        """The bug the series anchor exists to prevent.

        scheduled_at used to anchor the grid as well as mark the next run, so
        moving Thursday to Friday put every following week on a Friday.
        """
        task = self._daily()
        before = dict(self._blocks(task))
        target = sorted(before)[2]
        moved_to = before[target] + timedelta(hours=5)

        self.app.scheduler.override_occurrence(
            task.id, target, {"scheduled_at": moved_to.isoformat()})

        after = dict(self._blocks(self.store.get(task.id)))
        self.assertEqual(after[target], moved_to, "the block did not move")
        for index, when in before.items():
            if index != target:
                self.assertEqual(after[index], when,
                                 f"occurrence {index} moved with it")

    def test_moving_the_next_run_moves_the_row_but_not_the_grid(self) -> None:
        task = self._daily()
        before = dict(self._blocks(task))
        live = task.runs
        moved_to = before[live] + timedelta(hours=3)

        self.app.scheduler.override_occurrence(
            task.id, live, {"scheduled_at": moved_to.isoformat()})

        updated = self.store.get(task.id)
        self.assertEqual(updated.scheduled_at, moved_to, "the row did not move")
        after = dict(self._blocks(updated))
        self.assertEqual(after[live + 1], before[live + 1],
                         "the rest of the series followed the moved run")

    def test_retitling_one_block_leaves_the_series_title(self) -> None:
        task = self._daily()
        target = task.runs + 2

        self.app.scheduler.override_occurrence(
            task.id, target, {"title": "Stretch properly this time"})

        from personalized_agent.scheduler.core import series
        updated = self.store.get(task.id)
        self.assertEqual(updated.title, "Stand up and stretch")
        self.assertEqual(series.override_for(updated, target)["title"],
                         "Stretch properly this time")

    def test_an_override_holds_only_what_changed(self) -> None:
        """So a later edit to the series still reaches an overridden block.

        Copying the whole block into the override would freeze it, which is
        what makes a calendar show a stale title on one week of a series.
        """
        from personalized_agent.scheduler.core import series

        task = self._daily()
        target = task.runs + 2
        self.app.scheduler.override_occurrence(task.id, target, {"title": "Just this one"})
        self.app.scheduler.update_task(task.id, description="A longer break.")

        updated = self.store.get(task.id)
        self.assertEqual(set(series.override_for(updated, target)), {"title"})
        self.assertEqual(updated.description, "A longer break.",
                         "the series edit should still reach every block")

    # -- what may differ per occurrence -------------------------------------
    def test_only_a_time_a_title_and_a_description_may_differ(self) -> None:
        # An occurrence is a time, not a task of its own. Letting it diverge
        # further would put things in it that "all events" cannot reach.
        from personalized_agent.scheduler.core import series

        task = self._daily()
        target = task.runs + 1
        self.app.scheduler.override_occurrence(
            task.id, target, {"execution_type": "python_script", "title": "kept"})

        updated = self.store.get(task.id)
        self.assertEqual(set(series.override_for(updated, target)), {"title"})
        self.assertEqual(updated.execution_type, "notification")

    # -- the ids the calendar hands back ------------------------------------
    def test_a_block_id_says_which_occurrence_it_is(self) -> None:
        from personalized_agent.scheduler.core.series import parse_block_id

        self.assertEqual(parse_block_id("task_123#7"), ("task_123", 7))
        self.assertEqual(parse_block_id("task_123"), ("task_123", None),
                         "the live row is not occurrence zero")
        self.assertEqual(parse_block_id("task_123#nonsense"), ("task_123", None))

    # -- tasks made before any of this existed ------------------------------
    def test_a_task_with_no_anchor_keeps_the_grid_it_has_been_running_on(self) -> None:
        from personalized_agent.scheduler.core import series

        task = self._daily()
        self.store.update(task.id, runs=5,
                          scheduled_at=task.scheduled_at + timedelta(days=5),
                          metadata={})            # as though anchors never existed
        revived = self.store.get(task.id)

        self.assertEqual(series.anchor_of(revived), task.scheduled_at)
        self.assertEqual(series.grid_moment(revived, 5), revived.scheduled_at)


class TheCalendarAndTheSchedulerAreTheSameThingTests(unittest.TestCase):
    """Section 60, as amended: an event made on the calendar is a task.

    It needs the same fields as one the agent scheduled, so what the user left
    blank is filled in from context - the way it would be if they had asked in
    conversation. The difference is the order: the event exists first and is
    completed behind them.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.store = self.app.scheduler_store
        self.completer = self.app.task_completer

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _model_says(self, payload: dict) -> None:
        class _Reply:
            text = json.dumps(payload)
        self.completer.models.invoke = lambda request, role=None: _Reply()

    def _sparse(self, **over) -> ScheduledTask:
        """What pressing "add" on a calendar actually supplies."""
        fields = dict(title="Call the accountant",
                      scheduled_at=utcnow() + timedelta(days=1),
                      execution_type="notification",
                      metadata={"created_by": "calendar_ui"})
        fields.update(over)
        return self.app.scheduler.create_task(ScheduledTask(**fields))

    # -- what is missing ----------------------------------------------------
    def test_a_calendar_event_knows_what_it_is_missing(self) -> None:
        from personalized_agent.scheduler.core import completion

        task = self._sparse()
        self.assertEqual(
            completion.missing_fields(task),
            ["description", "rationale", "notify_user", "verification"])

    def test_what_the_user_said_does_not_count_as_missing(self) -> None:
        from personalized_agent.scheduler.core import completion

        task = self._sparse(description="Ask about the VAT return.",
                            task_payload={"notify_user": False})
        missing = completion.missing_fields(task)
        self.assertNotIn("description", missing)
        self.assertNotIn("notify_user", missing,
                         "an explicit 'do not interrupt me' is a choice, not a gap")

    # -- the event exists first ---------------------------------------------
    def test_the_event_exists_before_it_is_completed(self) -> None:
        from personalized_agent.scheduler.core import completion

        # The model is slow; the row must be there regardless.
        def _slow(request, role=None):
            raise AssertionError("the request thread waited for the model")
        self.completer.models.invoke = _slow

        task = self._sparse()
        marked = self.completer.request(task)

        self.assertIsNotNone(self.store.get(task.id), "the event was not written")
        self.assertEqual(completion.state_of(marked), completion.PENDING)

    def test_an_unavailable_model_does_not_lose_the_event(self) -> None:
        from personalized_agent.scheduler.core import completion

        task = self._sparse()
        def _unavailable(request, role=None):
            raise RuntimeError("model unavailable")
        self.completer.models.invoke = _unavailable

        self.completer.complete(task.id)

        stored = self.store.get(task.id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.title, "Call the accountant")
        self.assertEqual(completion.state_of(stored), completion.FAILED)

    # -- and is completed afterwards ----------------------------------------
    def test_completion_fills_the_blanks(self) -> None:
        from personalized_agent.scheduler.core import completion
        from personalized_agent.scheduler.execution.executors import VERIFICATION

        task = self._sparse()
        self.completer.request(task)
        self._model_says({
            "description": "Ring them about the VAT return.",
            "rationale": "The filing deadline is in a week.",
            "notify_user": True,
            "approval": "auto",
            "verification": {"completed_when": "the call shows in the log",
                             "check_after_seconds": 900,
                             "recheck_after_seconds": 3600}})

        self.completer.complete(task.id)

        done = self.store.get(task.id)
        self.assertEqual(done.description, "Ring them about the VAT return.")
        self.assertEqual(done.metadata["rationale"], "The filing deadline is in a week.")
        self.assertTrue(done.task_payload["notify_user"])
        # What it said about asking first is not kept: nothing asks first.
        self.assertNotIn("approval", done.task_payload)
        self.assertEqual(done.task_payload[VERIFICATION]["check_after_seconds"], 900)
        self.assertEqual(completion.state_of(done), completion.DONE)

    def test_it_never_overwrites_what_the_user_wrote(self) -> None:
        task = self._sparse(description="Ask about the VAT return.")
        self.completer.request(task)
        self._model_says({"description": "Something the agent made up",
                          "rationale": "because"})

        self.completer.complete(task.id)

        self.assertEqual(self.store.get(task.id).description,
                         "Ask about the VAT return.")

    def test_what_the_user_types_while_it_thinks_still_wins(self) -> None:
        """It runs behind them, and they may be editing the very thing."""
        task = self._sparse()
        self.completer.request(task)

        def _slow_then_answer(request, role=None):
            # The user finishes typing while the model is thinking.
            self.app.scheduler.update_task(task.id, description="Mine, actually.")
            class _Reply:
                text = json.dumps({"description": "The agent's guess",
                                   "rationale": "some reason"})
            return _Reply()
        self.completer.models.invoke = _slow_then_answer

        self.completer.complete(task.id)

        self.assertEqual(self.store.get(task.id).description, "Mine, actually.")

    def test_it_does_not_invent_a_different_time(self) -> None:
        when = utcnow() + timedelta(days=1)
        task = self._sparse(scheduled_at=when)
        self.completer.request(task)
        self._model_says({"description": "d", "rationale": "r",
                          "scheduled_at": (utcnow() + timedelta(days=30)).isoformat()})

        self.completer.complete(task.id)

        self.assertEqual(self.store.get(task.id).scheduled_at, when,
                         "the time is the one thing the user certainly meant")

    def test_a_task_needing_nothing_is_left_alone(self) -> None:
        task = self._sparse(description="Complete already.",
                            task_payload={"notify_user": False, "approval": "auto",
                                          "verification": {"completed_when": "x"}},
                            metadata={"created_by": "calendar_ui", "rationale": "given"})
        called = []
        self.completer.models.invoke = lambda *a, **k: called.append(1)

        self.completer.request(task)

        self.assertEqual(called, [], "it asked the model for nothing")

    # -- a half-written task does not run -----------------------------------
    def test_a_task_still_being_filled_in_waits(self) -> None:
        from personalized_agent.scheduler.core import completion

        task = self._sparse(scheduled_at=utcnow() - timedelta(seconds=1))
        self.app.scheduler.update_task(
            task.id, metadata=completion.mark(task.metadata, completion.PENDING, ["description"]))

        results = self.app.scheduler.tick()

        self.assertEqual(results[0]["status"], "awaiting_completion")
        self.assertGreater(self.store.get(task.id).scheduled_at, utcnow())

    def test_it_does_not_wait_for_ever(self) -> None:
        # Sparse is worse than complete, and far better than never.
        from personalized_agent.scheduler.core import completion
        from personalized_agent.scheduler.core.scheduler import COMPLETION_WAITS

        task = self._sparse(scheduled_at=utcnow() - timedelta(seconds=1))
        metadata = completion.mark(task.metadata, completion.PENDING, ["description"])
        metadata["completion_waits"] = COMPLETION_WAITS
        self.app.scheduler.update_task(task.id, metadata=metadata)

        results = self.app.scheduler.tick()

        self.assertNotEqual(results[0].get("status"), "awaiting_completion")

    # -- the bug this path had ----------------------------------------------
    def test_a_repeating_calendar_event_can_be_created(self) -> None:
        """_creation_payload referred to a name that did not exist.

        Any create carrying a recurrence raised NameError before it reached
        the scheduler, so the event was simply lost.
        """
        from personalized_agent.api import routes

        source = routes.__file__
        with open(source, encoding="utf-8") as handle:
            body = handle.read()
        start = body.index("def _creation_payload")
        end = body.index("def _creation_recurrence")
        self.assertNotIn("fields[", body[start:end],
                         "_creation_payload still uses a name it does not define")


class StatesAreInternalTests(unittest.TestCase):
    """Sections 34.1 and 14.33, as amended.

    A state is half of the context behind every answer, which makes it worth
    being able to look at, and does not make it an entry in the user's diary.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _routes(self, debugger: bool = True):
        import personalized_agent.api.routes as routes_mod

        original = routes_mod.debugger_available
        routes_mod.debugger_available = lambda: debugger
        try:
            return routes_mod.build_routes(self.app)
        finally:
            routes_mod.debugger_available = original

    def _task(self, **over):
        fields = dict(title="Call the accountant", description="About the VAT.",
                      scheduled_at=utcnow() + timedelta(hours=2),
                      execution_type="notification",
                      metadata={"created_by": "calendar_ui"})
        fields.update(over)
        return self.app.scheduler.create_task(ScheduledTask(**fields))

    def _state_of(self, task):
        found = self.app.states.for_task(task.id)
        return found[0] if found else None

    # -- states are not on the user's calendar ------------------------------
    def test_states_are_served_only_under_the_debugger(self) -> None:
        routes = self._routes()
        self.assertIn(("GET", "/api/debugger/states"), routes)
        self.assertNotIn(("GET", "/api/states"), routes,
                         "states must not be on the ordinary surface")

    def test_the_calendar_page_does_not_draw_them(self) -> None:
        from personalized_agent.common.paths import resource_path

        app_js = resource_path("api", "web", "app.js").read_text(encoding="utf-8")
        surface = app_js[app_js.index("async function loadCalendar"):
                         app_js.index("function openPopover")]
        self.assertNotIn("/api/states", surface)
        self.assertNotIn("statesOn(", surface,
                         "the calendar still has state-drawing code in it")

    # -- a build without the debugger has none of it ------------------------
    def test_a_build_without_the_debugger_serves_none_of_it(self) -> None:
        routes = self._routes(debugger=False)
        debugger_routes = [p for (_m, p) in routes if p.startswith("/api/debugger")]
        self.assertEqual(debugger_routes, [],
                         "a build made without the Debugger still answers for it")

    def test_the_rest_of_the_interface_is_unaffected(self) -> None:
        routes = self._routes(debugger=False)
        self.assertIn(("GET", "/api/tasks"), routes)
        self.assertIn(("GET", "/api/status"), routes)

    def test_the_build_can_exclude_the_package(self) -> None:
        import os

        from build.common import NO_DEBUGGER_ENV, exclude_arguments

        before = {NO_DEBUGGER_ENV: os.environ.get(NO_DEBUGGER_ENV)}
        try:
            os.environ.pop(NO_DEBUGGER_ENV, None)
            # By default a build carries it: a developer's own build.
            self.assertNotIn("personalized_agent.debugger", exclude_arguments("windows"))
            # --no-debugger - as every released download is built - leaves it out.
            os.environ[NO_DEBUGGER_ENV] = "1"
            self.assertIn("personalized_agent.debugger", exclude_arguments("windows"))
        finally:
            for name, value in before.items():
                os.environ.pop(name, None)
                if value is not None:
                    os.environ[name] = value

    def test_the_interface_is_told_which_build_this_is(self) -> None:
        self.assertIn("debugger_built_in", self.app.status())

    # -- a change on the calendar reaches the state -------------------------
    def test_moving_a_task_moves_its_state(self) -> None:
        """Two rows saying one thing must not come to disagree.

        A task moved to Friday whose state still claims Thursday is a record
        that contradicts itself invisibly - each row is fine on its own - and
        that state is in the context behind every answer meanwhile.
        """
        task = self._task()
        before = self._state_of(task).starts_at

        self.app.scheduler.reschedule(task.id, utcnow() + timedelta(days=3))

        after = self._state_of(task)
        self.assertGreater(after.starts_at, before)
        moved = self.app.scheduler_store.get(task.id).scheduled_at
        self.assertLess(abs((after.starts_at - moved).total_seconds()), 3600)

    def test_cancelling_a_task_closes_its_state(self) -> None:
        # What the state said was that the task was going to happen.
        task = self._task()
        self.app.scheduler.cancel(task.id)
        self.assertIsNone(self._state_of(task))

    def test_deleting_a_task_closes_its_state(self) -> None:
        task = self._task()
        self.app.scheduler.delete(task.id)
        self.assertIsNone(self._state_of(task),
                          "a state naming a task that no longer exists")

    def test_disabling_a_task_closes_its_state(self) -> None:
        task = self._task()
        self.app.scheduler.disable(task.id)
        self.assertIsNone(self._state_of(task))

    def test_a_closed_state_leaves_the_context(self) -> None:
        # The point of closing it: it stops being told to the model.
        task = self._task(scheduled_at=utcnow() + timedelta(minutes=5))
        self.assertTrue([s for s in self.app.states.covering(limit=100)
                         if s.task_id == task.id])

        self.app.scheduler.cancel(task.id)

        self.assertFalse([s for s in self.app.states.covering(limit=100)
                          if s.task_id == task.id],
                         "a cancelled task is still being described as upcoming")

    def test_an_ordinary_edit_leaves_the_state_alone(self) -> None:
        task = self._task()
        before = self._state_of(task).starts_at
        self.app.scheduler.update_task(task.id, title="A better title")
        self.assertEqual(self._state_of(task).starts_at, before)


class EverythingSaysWhyItExistsTests(unittest.TestCase):
    """Section 52.7, as amended: a task, a state or a calendar entry says why.

    An agent that acts on its own judgement and leaves no account of that
    judgement gives the user two choices: accept everything, or switch it off.
    A reminder that turns out to be unwelcome is a different conversation when
    the user can read why the agent thought it was worth it - and it is the
    only way the agent's own decisions can be reviewed at all.

    The user is the exception, in the other direction. They put it there; that
    is the reason, and asking them to justify their own calendar entry would be
    the system demanding an explanation from the one participant who does not
    owe one.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.now = utcnow()

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _task(self, **kwargs):
        from personalized_agent.common.events.models import ScheduledTask

        kwargs.setdefault("title", "Check the form")
        kwargs.setdefault("scheduled_at", self.now + timedelta(hours=2))
        return self.app.scheduler.create_task(ScheduledTask(**kwargs))

    def test_the_reason_the_creator_gave_is_kept(self) -> None:
        task = self._task(created_by="heartbeat",
                          reason="they said on Tuesday they would submit it")
        self.assertEqual(task.created_by, "heartbeat")
        self.assertIn("Tuesday", task.reason)
        self.assertIn("Tuesday", self.app.scheduler_store.get(task.id).reason)

    def test_a_user_entry_with_nothing_written_says_added_by_user(self) -> None:
        self.assertEqual(self._task(created_by="user").reason, "added by user")

    def test_a_user_entry_keeps_what_they_did_write(self) -> None:
        task = self._task(created_by="user", reason="dentist, rescheduled")
        self.assertEqual(task.reason, "dentist, rescheduled")

    def test_an_agent_entry_with_nothing_written_says_so(self) -> None:
        """Not blank.

        A thing with no reason and a thing whose reason was not recorded read
        identically unless they are written differently, and the second is a
        fault in whatever created it.
        """
        task = self._task()
        self.assertIn("no reason recorded", task.reason)
        self.assertEqual(task.created_by, "agent")

    def test_an_existing_rationale_is_used_as_the_reason(self) -> None:
        # The heartbeat and the schedule_task tool were already being asked for
        # a rationale, and it was going into metadata where nothing required it
        # and nothing showed it.
        task = self._task(metadata={"rationale": "a +0100 offset was seen"})
        self.assertIn("+0100", task.reason)

    def test_the_state_a_task_writes_carries_the_task_reason(self) -> None:
        # Two records of one decision must not disagree about why it was made.
        task = self._task(created_by="heartbeat", reason="the deadline is Friday")
        state = self.app.states.for_task(task.id)[0]
        self.assertIn("the deadline is Friday", state.reason)
        self.assertIn("accompanies a scheduled task", state.reason)

    def test_a_state_of_the_users_own_says_added_by_user(self) -> None:
        from personalized_agent.agent.states.store import SOURCE_USER, State

        state = self.app.states.add(State(
            title="On holiday", source=SOURCE_USER,
            starts_at=self.now, ends_at=self.now + timedelta(days=3)))
        self.assertEqual(state.reason, "added by user")

    def test_a_state_the_agent_inferred_says_so_when_nothing_was_given(self) -> None:
        from personalized_agent.agent.states.store import State

        state = self.app.states.add(State(
            title="Writing the report", starts_at=self.now,
            ends_at=self.now + timedelta(hours=3)))
        self.assertIn("no reason recorded", state.reason)


    def test_a_calendar_entry_the_user_adds_is_theirs(self) -> None:
        from personalized_agent.api.routes import build_routes

        class Request:
            def __init__(self, body):
                self.body = body

            def require(self, key):
                return self.body[key]

            def get(self, key, default=None):
                return self.body.get(key, default)

        created = build_routes(self.app)[("POST", "/api/tasks")](Request({
            "title": "Dentist",
            "scheduled_at": (self.now + timedelta(days=1)).isoformat()}))
        self.assertEqual(created["created_by"], "user")
        self.assertEqual(created["reason"], "added by user")


class ACalledOffNotificationStaysOnTheCalendarTests(unittest.TestCase):
    """Set and then cancelled, it stays - marked, with who and why - and
    never interrupts."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _notification(self):
        return self.app.scheduler.create_task(ScheduledTask(
            title="Reply to Prof. Okafor", scheduled_at=utcnow() + timedelta(hours=2),
            execution_type="notification", task_payload={"message": "m"}))

    def test_cancelling_records_who_and_why(self) -> None:
        task = self._notification()
        self.app.scheduler.cancel(task.id, reason="already replied", by="you")
        kept = self.app.scheduler.store.get(task.id)
        self.assertEqual(kept.status, "cancelled")
        self.assertEqual(kept.metadata["cancelled_reason"], "already replied")
        self.assertEqual(kept.metadata["cancelled_by"], "you")
        self.assertTrue(kept.metadata["cancelled_at"])
        # Still on the calendar, and not due to run.
        self.assertIn(task.id, [one.id for one in self.app.scheduler.store.list(
            start=utcnow(), end=utcnow() + timedelta(days=1))])
        self.assertNotIn(task.id, [one.id for one in self.app.scheduler.store.due(
            utcnow() + timedelta(days=1))])

    def test_the_agent_must_say_why(self) -> None:
        tools = self.app.agent.tools if hasattr(self.app.agent, "tools") else None
        registry = getattr(tools, "registry", tools)
        run = getattr(registry, "call", None) or getattr(registry, "run", None)
        if run is None:
            self.skipTest("no tool registry to call")
        task = self._notification()
        said = run("cancel_scheduled_task", {"task_id": task.id})
        self.assertFalse((said if isinstance(said, dict) else {}).get("cancelled"))
        self.assertEqual(self.app.scheduler.store.get(task.id).status, "pending")
        run("cancel_scheduled_task", {"task_id": task.id,
                                      "reason": "the user replied already"})
        kept = self.app.scheduler.store.get(task.id)
        self.assertEqual(kept.status, "cancelled")
        self.assertEqual(kept.metadata["cancelled_by"], "the agent")


class OnlyNotificationsAreScheduledTests(unittest.TestCase):
    """Nothing on the calendar executes anything: only notifications run.

    A scheduled script, agent run or model task did something on the user's
    machine at a moment nobody was watching, and a check of an unsure task was
    itself a model run the user never asked for. So the scheduler makes
    notifications and nothing else, refuses every other kind at the door, and
    settles - without running - any other kind that is already in the store.
    """

    OTHER_KINDS = ("python_script", "agent_task", "model_task", "system_action",
                   "integration_action", "check_status")

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.scheduler = self.app.scheduler
        self.store = self.app.scheduler_store

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _fields(self, execution_type, **over):
        fields = dict(title="Tidy the exports", description="Deletes old files.",
                      scheduled_at=utcnow() - timedelta(seconds=1),
                      execution_type=execution_type,
                      task_payload={"message": "tidy", "notify_user": False})
        fields.update(over)
        return fields

    def test_every_other_kind_is_refused(self) -> None:
        for kind in self.OTHER_KINDS:
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError) as said:
                    self.scheduler.create_task(ScheduledTask(**self._fields(kind)))
                self.assertIn("only notifications", str(said.exception))
                self.assertIn(kind, str(said.exception))
        self.assertEqual(self.store.list(limit=50), [],
                         "a refused task was written anyway")

    def test_a_notification_is_still_made(self) -> None:
        task = self.scheduler.create_task(ScheduledTask(**self._fields("notification")))
        self.assertEqual(self.store.get(task.id).execution_type, "notification")

    def test_one_already_in_the_store_is_cancelled_and_never_run(self) -> None:
        """A row an older version wrote is settled, not executed.

        The script would leave a file behind if it ran; the file must not
        exist, and the executor must never have been handed the task. The
        marker is somewhere a scheduled script was allowed to write (its own
        directory, section 32), so its absence means it did not run rather
        than that it ran and was refused.
        """
        from personalized_agent.scheduler.core.scheduler import NOT_RUN

        marker = self.app.policy.write_root / "generated" / "ran.txt"
        code = ("from pathlib import Path\n"
                "Path('generated').mkdir(exist_ok=True)\n"
                "Path('generated/ran.txt').write_text('ran')\n")
        task = self.store.create(ScheduledTask(**self._fields(
            "python_script", task_payload={"code": code, "notify_user": False})))
        handed = []
        real = self.scheduler.executor.execute
        self.scheduler.executor.execute = lambda t: (handed.append(t.id), real(t))[1]

        results = self.scheduler.tick()

        self.assertEqual(results[0]["status"], "cancelled")
        self.assertEqual(results[0]["result"], NOT_RUN)
        self.assertEqual(handed, [], "the executor was handed it")
        self.assertFalse(marker.exists(), "the script ran")
        kept = self.store.get(task.id)
        self.assertEqual(kept.status, "cancelled")
        self.assertEqual(kept.metadata["cancelled_reason"], NOT_RUN)
        self.assertEqual(kept.metadata["cancelled_by"], "the scheduler")
        self.assertEqual(self.scheduler.tick(), [], "it came round again")

    def test_every_other_kind_in_the_store_is_cancelled(self) -> None:
        from personalized_agent.scheduler.core.scheduler import NOT_RUN

        ids = [self.store.create(ScheduledTask(**self._fields(kind))).id
               for kind in self.OTHER_KINDS]
        handed = []
        self.scheduler.executor.execute = lambda t: handed.append(t.id)

        self.scheduler.tick()

        self.assertEqual(handed, [])
        for task_id in ids:
            kept = self.store.get(task_id)
            with self.subTest(kind=kept.execution_type):
                self.assertEqual(kept.status, "cancelled")
                self.assertEqual(kept.metadata["cancelled_reason"], NOT_RUN)

    def test_an_unsure_notification_completes_and_schedules_no_check(self) -> None:
        """Being unsure used to park the task and put a check on the calendar.

        A check is a model run, and only notifications run: a notification
        that could not confirm the thing was done has still been delivered,
        and that is its end.
        """
        from personalized_agent.scheduler.execution.executors import (
            CHECK_STATUS, TO_BE_CHECKED, ExecutionOutcome)

        task = self.scheduler.create_task(ScheduledTask(**self._fields("notification")))
        self.scheduler.executor.execute = lambda t: ExecutionOutcome(
            True, "started the build", disposition=TO_BE_CHECKED)

        result = self.scheduler.tick()[0]

        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.store.get(task.id).status, "completed")
        self.assertEqual([t for t in self.store.list(limit=50)
                          if t.execution_type == CHECK_STATUS], [])
        self.assertEqual(self.store.awaiting_verification(), [])

    def test_check_later_completes_the_task(self) -> None:
        from personalized_agent.scheduler.execution.executors import CHECK_STATUS

        task = self.scheduler.create_task(ScheduledTask(**self._fields(
            "notification", scheduled_at=utcnow() + timedelta(hours=1))))

        settled = self.scheduler.check_later(task.id, "the user said they would")

        self.assertEqual(settled.status, "completed")
        self.assertEqual(self.store.get(task.id).status, "completed")
        self.assertEqual([t for t in self.store.list(limit=50)
                          if t.execution_type == CHECK_STATUS], [])
        self.assertIsNone(self.scheduler.check_later("task_gone", "x"))

    def test_the_schedule_tool_offers_no_other_kind(self) -> None:
        spec = next(one for one in self.app.agent.tools.specs
                    if one.name == "schedule_task")
        self.assertNotIn("execution_type", spec.input_schema["properties"])
        self.assertNotIn("script_path", spec.input_schema["properties"])

    def test_what_the_schedule_tool_makes_is_a_notification(self) -> None:
        # Even when asked for something else: the argument is not read.
        outcome = self.app.agent.tools.call("schedule_task", {
            "title": "Run the cleanup", "description": "Deletes old files.",
            "notify": "silent", "execution_type": "python_script",
            "scheduled_at": (utcnow() + timedelta(hours=1)).isoformat()})
        self.assertIn("task_id", outcome, outcome)
        self.assertEqual(outcome["execution_type"], "notification")
        self.assertEqual(self.store.get(outcome["task_id"]).execution_type,
                         "notification")

    def test_the_calendar_route_refuses_another_kind(self) -> None:
        from personalized_agent.api.errors import ApiError
        from personalized_agent.api.routes import build_routes

        class Request:
            def __init__(self, body):
                self.body = body

            def require(self, key):
                return self.body[key]

            def get(self, key, default=None):
                return self.body.get(key, default)

        with self.assertRaises(ApiError) as said:
            build_routes(self.app)[("POST", "/api/tasks")](Request({
                "title": "Run the cleanup", "execution_type": "model_task",
                "scheduled_at": (utcnow() + timedelta(days=1)).isoformat()}))
        self.assertEqual(said.exception.status, 400)
        self.assertEqual(self.store.list(limit=50), [])
