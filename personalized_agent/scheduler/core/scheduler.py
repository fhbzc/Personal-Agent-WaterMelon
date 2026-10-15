"""Scheduler loop (sections 52, 57, 59, 60).

The loop wakes roughly once per minute, claims the tasks that are due, and runs
them.  No model is invoked unless a task actually requires model reasoning.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from . import completion, editing, series
from .recurrence import next_occurrence
from ...common.events.models import ScheduledTask, parse_iso, utcnow
from ...common.reason import creator_of, reason_for
from ...common.observability.tracing import NullTracer, new_trace_id
from ..execution.executors import (DELAYED, DONE, TO_BE_CHECKED,
                                  ExecutionOutcome, TaskExecutor,
                                  verification_plan)
from ..storage.store import SchedulerStore

log = logging.getLogger(__name__)

#: What the scheduler runs: notifications, and nothing else. A task of any
#: other kind is settled without running (`run_task`).
RUNS = ("notification",)
NOT_RUN = ("not run: only notifications are run - nothing on the calendar "
           "executes anything")

#: How long a due task waits for the agent to finish filling it in, and how
#: many times. Short and few: this is the rare case where an event was made for
#: almost immediately, and a task that is never run is worse than a sparse one.
#: How long before and after a task its accompanying state claims the
#: calendar. Half an hour either side: enough to read as "this is coming"
#: without the calendar filling up with spans nothing is happening in.
STATE_LEAD_MINUTES = 30
STATE_TAIL_MINUTES = 30

COMPLETION_WAIT_SECONDS = 20
COMPLETION_WAITS = 6


class Scheduler:
    def __init__(
        self,
        *,
        store: SchedulerStore,
        executor: TaskExecutor,
        config: Any,
        bus: Any = None,
        observability: Any = None,
        recorder: Any = None,
        tracer: Any = None,
        questions: Any = None,
        notifications: Any = None,
        states: Any = None,
    ) -> None:
        self.store = store
        #: Section 34.1: every task gets the span during which it is true that
        #: the agent means to do it. Here rather than in the callers, because
        #: "every task" has to mean every task - a rule kept by three separate
        #: call sites is a rule that holds until someone adds a fourth.
        self.states = states
        #: Section 52.4: where a task that asks first puts its question.
        self.questions = questions
        self.notifications = notifications
        self.tracer = tracer or NullTracer()
        self.executor = executor
        self.config = config
        self.bus = bus
        self.observability = observability
        self.recorder = recorder
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_tick: Optional[str] = None

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        recovered = self.store.recover_orphans()
        if recovered:
            log.info("recovered %s scheduled tasks after restart", recovered)
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()
        log.info("scheduler started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("scheduler stopped")

    def _loop(self) -> None:
        while not self._stop.is_set():
            tick = max(5, int(self.config.get("scheduler_tick_seconds", 60)))
            if bool(self.config.get("scheduler_enabled", True)):
                try:
                    self.tick()
                except Exception:
                    log.exception("scheduler tick failed")
                    if self.observability is not None:
                        self.observability.record_error("scheduler", "tick failed")
            self._stop.wait(tick)

    # -- execution ----------------------------------------------------------
    def tick(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        now = now or utcnow()
        self._last_tick = now.isoformat()
        results = []
        for task in self.store.due(now):
            results.append(self.run_task(task))
        if self.observability is not None:
            self.observability.set_state(
                "scheduler.last_tick", {"at": self._last_tick, "executed": len(results)}
            )
        return results

    def run_task(self, task: ScheduledTask) -> Dict[str, Any]:
        token = self.store.claim(task)
        if token is None:
            return {"task_id": task.id, "skipped": "already claimed"}
        # ONLY NOTIFICATIONS RUN. Nothing on the calendar executes anything -
        # no script, no agent run, no model task, no check: a task of any
        # other kind, however it got there, is settled without running.
        if task.execution_type not in RUNS:
            self.cancel(task.id, reason=NOT_RUN, by="the scheduler")
            return {"task_id": task.id, "status": "cancelled", "result": NOT_RUN}
        # A task still being written out by the completer waits for it.
        waiting = self._hold_for_completion(task)
        if waiting is not None:
            return waiting
        if self.bus is not None:
            self.bus.publish("scheduler.task_started", task.to_dict())
        trace_id = new_trace_id()
        with self.tracer.span(
            "scheduler.execute_task", trace_id=trace_id, task_id=task.id,
            event_type="scheduler.task",
            connection_id="scheduler->runtime"
            if task.execution_type == "python_script" else None,
            inputs={"title": task.title, "type": task.execution_type},
            metadata={"kind": "scheduled_task", "execution_type": task.execution_type},
        ) as span:
            outcome: ExecutionOutcome = self.executor.execute(task)
            span.output_summary = outcome.result[:160]
            if not outcome.ok:
                span.status = "error"
                span.severity = "error"
                span.error = outcome.result[:200]
        retry_policy = task.retry_policy or {}
        max_retries = int(retry_policy.get("max_retries", 2))
        backoff = int(retry_policy.get("backoff_seconds", 60))

        if outcome.ok:
            status = self._settle(task, outcome)
        elif outcome.retryable and task.attempts < max_retries:
            retry_at = utcnow() + timedelta(seconds=backoff * max(1, task.attempts))
            self.store.fail(task.id, outcome.result, retry_at=retry_at)
            status = "retry_scheduled"
        else:
            self.store.fail(task.id, outcome.result)
            status = "failed"

        if self.recorder is not None:
            self.recorder.scheduler_event(
                task.id, f"scheduler.task_{status}",
                f"executed {task.execution_type} task '{task.title}'",
                outcome.result, metadata=outcome.to_dict(),
            )
        if self.observability is not None:
            self.observability.increment(f"scheduler.{status}")
        if self.bus is not None:
            self.bus.publish("scheduler.task_finished",
                             {"task": task.to_dict(), "status": status,
                              "outcome": outcome.to_dict()})
        return {"task_id": task.id, "status": status, "result": outcome.result,
                "trace_id": trace_id}

    # -- waiting to be filled in (section 60, as amended) -------------------
    def _hold_for_completion(self, task: ScheduledTask):
        """Do not run a task the agent is still writing.

        A calendar event is created the moment the user presses add, holding
        only what they typed, and the rest is filled in behind them. Almost
        always that lands long before the task is due. When it does not - an
        event made for the next minute, or a model that is slow - running
        anyway would run a task whose description, notification choice and
        verification plan are all still blank.

        So it waits, briefly and repeatedly, rather than running half-written
        or failing. If the filling-in never lands the task still runs: sparse
        is worse than complete, and far better than never.
        """
        if not completion.is_pending(task):
            return None
        waited = int((task.metadata or {}).get("completion_waits") or 0)
        if waited >= COMPLETION_WAITS:
            log.info("task %s runs without being filled in", task.id)
            return None
        metadata = {**(task.metadata or {}), "completion_waits": waited + 1}
        self.store.update(task.id, metadata=metadata)
        self.store.delay(task.id, utcnow() + timedelta(seconds=COMPLETION_WAIT_SECONDS),
                         "waiting to be filled in")
        return {"task_id": task.id, "status": "awaiting_completion",
                "result": "the agent is still filling this in"}

    # -- asking before acting (section 52.4, withdrawn) ----------------------
    def ask_permission(self, task: ScheduledTask):
        """Park a task and ask whether it may run.

        NOTHING ASKS FIRST ANY MORE. A task the agent was unsure of used to
        wait on the calendar for a "run now" - which the calendar no longer
        offers, so a task waiting for one waits for ever. Every task runs at
        its time; one the user does not want is disabled or deleted. This is
        kept for the answers machinery (agent/feedback), which still knows
        how to settle a permission question, and is called by nothing.
        """
        if self.questions is None:
            if self.notifications is not None:
                self.notifications.notify(
                    "Waiting on you", f"{task.title} - held until you say it "
                    "may run; on the calendar it can be disabled or deleted",
                    kind="approval", metadata={"task_id": task.id},
                    dedupe_key=f"approval.{task.id}")
            self.store.update(task.id, status="awaiting_approval", lease_token=None)
            if self.bus is not None:
                self.bus.publish("scheduler.task_awaiting_approval", task.to_dict())
            return {"task_id": task.id, "status": "awaiting_approval",
                    "result": "waiting for the user to say whether to run it"}

        from ...agent.feedback.store import KIND_PERMISSION, TaskQuestion
        from ...agent.feedback.templates import options_for

        existing = self.questions.pending_for(task.id, KIND_PERMISSION)
        if existing is None:
            question = self.questions.ask(TaskQuestion(
                task_id=task.id,
                kind=KIND_PERMISSION,
                question=f"Run '{task.title}' now?",
                task_detail=task.description,
                arrangement=str(task.metadata.get("rationale") or ""),
                # The options are recorded with the question, including the
                # interval the delay button offered. The user chose a button
                # that said a number; that number is what they agreed to, and
                # recomputing it later would answer a different question.
                metadata={"options": [o.to_dict() for o in options_for(
                    KIND_PERMISSION, self._delay_offer(task))]},
            ))
            if self.notifications is not None:
                self.notifications.notify(
                    "Waiting on you", f"{task.title} - shall I run it?",
                    kind="approval",
                    metadata={"task_id": task.id, "question_id": question.id},
                    dedupe_key=f"approval.{task.id}")
        self.store.update(task.id, status="awaiting_approval", lease_token=None)
        if self.bus is not None:
            self.bus.publish("scheduler.task_awaiting_approval", task.to_dict())
        return {"task_id": task.id, "status": "awaiting_approval",
                "result": "waiting for the user to say whether to run it"}

    def _delay_offer(self, task: ScheduledTask) -> int:
        """How long "later" should mean for this task.

        Urgency is a property of the work, so it comes from the plan written
        when the task was scheduled. Offering everything the same quarter of an
        hour would make the button meaningless on a task that matters and
        pushy on one that does not.
        """
        return int(verification_plan(task)["recheck_after_seconds"])

    def approve(self, task_id: str, run_now: bool = True):
        """The user said yes: spend the approval and let it run.

        The approval only releases the task; what becomes of it afterwards is
        decided the same way as any other run, by ``_settle``. Being waved
        through is not the same as being finished.
        """
        task = self.store.get(task_id)
        if task is None:
            return None
        payload = dict(task.task_payload)
        payload["approved"] = True
        fields = {"task_payload": payload, "status": "pending"}
        if run_now:
            fields["scheduled_at"] = utcnow()
        return self.store.update(task_id, **fields)

    def defer(self, task_id: str, seconds: Optional[int] = None,
              note: str = "") -> Optional[ScheduledTask]:
        """Not now: the same task, later, without asking again immediately.

        "Not now" is a delay, and has to move the clock to be one. Leaving the
        row pending at a time already past makes it due on the very next tick,
        which asks the question again within the minute - so declining once
        would produce a task that pesters rather than one that waits.
        """
        if seconds is None:
            seconds = int(self.config.get("scheduler_default_delay_seconds", 900))
        self.store.delay(task_id, utcnow() + timedelta(seconds=max(1, seconds)), note)
        updated = self.store.get(task_id)
        if updated is not None and self.bus is not None:
            self.bus.publish("scheduler.task_deferred", updated.to_dict())
        return updated

    def check_later(self, task_id: str, note: str = "") -> Optional[ScheduledTask]:
        """The user said they would do it. Find out later whether they did.

        Taking "got it" as done would make the agent's record of the week a
        record of what was agreed to rather than what happened - and the whole
        value of the record is that it is the second one. So the task takes the
        same road as any run that could not confirm itself: it parks, and a
        check goes on the calendar at the interval the task itself named.
        """
        task = self.store.get(task_id)
        if task is None:
            return None
        # No check is scheduled any more (only notifications run): what the
        # user said they would do is taken as said, and the task settled.
        self.store.complete(task_id, note)
        return self.store.get(task_id)

    # -- repeating tasks (section 52.3) -------------------------------------
    # -- what becomes of a task that ran (section 59, as amended) -----------
    def _settle(self, task: ScheduledTask, outcome: ExecutionOutcome) -> str:
        """Three ways a run that did not fail can end.

        Only one of them finishes the occurrence, and only that one may let a
        repeating task move on to its next. A delayed task is the *same*
        occurrence waiting for a better moment, and one still being verified
        may yet turn out not to have happened - advancing the recurrence for
        either would skip a run the user asked for and never notice.
        """
        disposition = outcome.disposition or DONE

        if disposition == DELAYED:
            until = outcome.next_attempt_at or (utcnow() + timedelta(
                seconds=int(self.config.get("scheduler_default_delay_seconds", 900))))
            self.store.delay(task.id, until, outcome.result)
            log.info("task %s delayed until %s", task.id, until.isoformat())
            return "delayed"

        if disposition == TO_BE_CHECKED:
            # NO CHECKS: a check is a task that runs a model, and only
            # notifications run. A notification that could not confirm the
            # thing was done has still been delivered, and that is its end.
            self.store.complete(task.id, outcome.result)
            return self._repeat_if_due(task, "completed")

        self.store.complete(task.id, outcome.result)
        return self._repeat_if_due(task, "completed")

    def _repeat_if_due(self, task: ScheduledTask, status: str) -> str:
        """Put a repeating task back on the calendar after it has run.

        A repeat is the same row moved forward, not a new task: the history,
        the notification choice and the reasoning all belong to the one thing
        the user scheduled, and a calendar that grew a fresh row per run would
        bury everything else.
        """
        if not task.recurrence:
            return status
        runs = task.runs + 1
        upcoming, why = next_occurrence(task.recurrence, task.scheduled_at, runs)
        if upcoming is None:
            self.store.update(task.id, runs=runs)
            log.info("task %s stopped repeating: %s", task.id, why)
            return "completed"
        self.store.update(task.id, runs=runs, scheduled_at=upcoming,
                          status="pending", attempts=0, lease_token=None)
        log.info("task %s repeats, next at %s", task.id, upcoming.isoformat())
        return "repeated"

    # -- one occurrence of a series (section 60, as amended) ----------------
    def skip_occurrence(self, task_id: str, index: Optional[int] = None):
        """Delete one run of a repeating task and leave the rest standing.

        The occurrence is recorded as skipped rather than removed, because
        there was never a row to remove - the calendar computes its blocks
        (section 52.3). Recording it is what makes the deletion survive the
        next expansion.

        If the run deleted is the one the scheduler is about to claim, the row
        has to move past it as well. Marking it skipped without moving the row
        would leave the task due, and it would run the occurrence the user had
        just deleted - the deletion would appear to work and then be undone by
        the next tick.
        """
        task = self.store.get(task_id)
        if task is None:
            return None
        if not task.recurrence:
            # Not a series: "this occurrence" and "the whole thing" are the
            # same claim, so this is an ordinary cancellation.
            return self.cancel(task_id, reason="this run was removed from "
                               "the calendar", by="you")

        index = task.runs if index is None else int(index)
        fields: Dict[str, Any] = {"metadata": series.skip(task, index)}
        if index == task.runs:
            # Look for the next run on the *updated* overrides, not the stored
            # ones: several occurrences in a row may be deleted, and advancing
            # by one would land the row on another the user has also removed.
            task.metadata = fields["metadata"]
            next_index, upcoming = series.next_unskipped(task, index + 1)
            if upcoming is None:
                fields.update(status="completed", lease_token=None)
                log.info("task %s has no runs left after the deletion", task_id)
            else:
                fields.update(runs=next_index, scheduled_at=upcoming,
                              attempts=0, lease_token=None, status="pending")
        updated = self.update_task(task_id, **fields)
        if updated is not None and self.bus is not None:
            self.bus.publish("scheduler.occurrence_skipped",
                             {"task_id": task_id, "occurrence_index": index})
        return updated

    def override_occurrence(self, task_id: str, index: Optional[int],
                            changes: Dict[str, Any]):
        """Change one run of a series without changing the others.

        Only the fields in ``series.OVERRIDABLE`` may differ per occurrence. An
        occurrence is a time, not a task of its own: letting it diverge further
        would put things in it that "all events" could no longer reach.
        """
        task = self.store.get(task_id)
        if task is None:
            return None
        wanted = {k: v for k, v in changes.items() if k in series.OVERRIDABLE and v}
        if not task.recurrence or index is None:
            return self.update_task(task_id, **wanted) if wanted else task
        if not wanted:
            return task

        index = int(index)
        fields: Dict[str, Any] = {"metadata": series.with_override(task, index, wanted)}
        if index == task.runs and "scheduled_at" in wanted:
            # The live run is the row, so moving it moves the row - but the
            # anchor recorded above keeps the grid where it was, so the rest of
            # the series does not follow it.
            moved = parse_iso(wanted["scheduled_at"])                 if isinstance(wanted["scheduled_at"], str) else wanted["scheduled_at"]
            if moved is not None:
                fields["scheduled_at"] = moved
        return self.update_task(task_id, **fields)

    # -- calendar facing API (section 60) ----------------------------------
    def create_task(self, task: ScheduledTask) -> ScheduledTask:
        # ONLY NOTIFICATIONS ARE SCHEDULED: nothing on the calendar executes
        # anything.
        if task.execution_type not in RUNS:
            raise ValueError(f"only notifications can be scheduled, not "
                             f"{task.execution_type!r}")
        # NOTHING ASKS FIRST: a task that says it should is a task that runs
        # at its time like any other (see `ask_permission`).
        if (task.task_payload or {}).get("approval"):
            task.task_payload = {key: value for key, value
                                 in task.task_payload.items()
                                 if key not in ("approval", "approved")}
        # Section 52.7, as amended: nothing reaches the calendar without
        # saying why it is there. Filled in here rather than trusted to each
        # caller, because a field that is optional is missing precisely where
        # it matters - on the task whose creator was not thinking about it.
        task.created_by = creator_of(task.created_by)
        task.reason = reason_for(task.reason or task.metadata.get("rationale"),
                                 task.created_by, f"task {task.title!r}")
        created = self.store.create(task)
        self._state_for_task(created)
        if self.bus is not None:
            self.bus.publish("scheduler.task_created", created.to_dict())
        return created

    def _state_for_task(self, task: ScheduledTask) -> str:
        """Scheduling something is itself a state (sections 34.1, 52.4).

        A task is a moment and is executed; a state is a span and is only
        shown. Something undertaken is both - there is the instant it runs,
        and there is the stretch of time during which it is true that it is
        going to happen. Writing only the task leaves that second half
        unsaid, and it is the half an answer needs: "what is on for today"
        is a question about spans.

        The span is deliberately short. This is not "this is being worked on
        all week"; it is "there is something queued", and it stops claiming
        the calendar shortly after the task has run.

        The relation is one-way. Every task has a state; most states have no
        task, because most of them are things the heartbeat noticed rather
        than things anybody scheduled.
        """
        if self.states is None or task.scheduled_at is None:
            return ""
        from ...agent.states.store import ABOUT_AGENT, SOURCE_TASK, State

        try:
            state = self.states.add(State(
                title=f"Scheduled: {task.title}"[:200],
                detail=(f"{task.description} "
                        f"Created because a task was scheduled to run at "
                        f"{task.scheduled_at.isoformat(timespec='minutes')}.").strip()[:1000],
                source=SOURCE_TASK,
                about=ABOUT_AGENT,
                task_id=task.id,
                starts_at=task.scheduled_at - timedelta(minutes=STATE_LEAD_MINUTES),
                ends_at=task.scheduled_at + timedelta(minutes=STATE_TAIL_MINUTES),
                # Section 52.7: it exists because the task does, so it says so
                # and carries the task's own reason with it. Anything else
                # would leave two records of one decision disagreeing about
                # why it was made.
                reason=f"accompanies a scheduled task - {task.reason}",
            ))
        except Exception:            # pragma: no cover - a state is context
            log.debug("could not write the state for task %s", task.id, exc_info=True)
            return ""
        return getattr(state, "id", "") or ""

    def edit_task(self, task_id: str, **asked: Any):
        """The user editing an event: (before, after, which fields changed).

        What may be edited, and what an edit to a series comes to, is
        `editing.plan`'s to say; it raises `editing.EditRefused` for what
        cannot be done. Written through `update_task`, so the accompanying
        state follows the event as it does any other change. An edit that
        changes nothing writes nothing.
        """
        before = self.store.get(task_id)
        fields, changed = editing.plan(before, **asked)
        if not changed:
            return before, before, []
        after = self.update_task(task_id, **fields)
        return before, after, changed

    def update_task(self, task_id: str, **fields: Any) -> Optional[ScheduledTask]:
        updated = self.store.update(task_id, **fields)
        if updated:
            self._follow_task_with_its_state(updated, fields)
            if self.bus is not None:
                self.bus.publish("scheduler.task_updated", updated.to_dict())
        return updated

    # -- keeping a task and its state saying the same thing -----------------
    def _follow_task_with_its_state(self, task: ScheduledTask,
                                    changed: Dict[str, Any]) -> None:
        """Move the accompanying state when the task it accompanies moves.

        A task and its state are two rows saying one thing. Left alone, a task
        moved to Friday keeps a state claiming Thursday - and the contradiction
        is invisible, because each row is fine on its own and nothing compares
        them. The state is also half of the context behind every answer, so a
        stale one is not a cosmetic problem: it is the agent being told
        something untrue about what is going on.

        Cancelled, disabled and completed all close the state rather than
        moving it. What it said was that the task was going to happen, and
        none of those is that.
        """
        if self.states is None:
            return
        accompanying = self._states_for(task.id)
        if not accompanying:
            return
        from ...agent.states.store import RETIRED

        status = str(changed.get("status") or task.status)
        if status in ("cancelled", "disabled", "completed", "failed", "closed"):
            why = f"the task is {status}"
            if status == "closed":
                said = str((task.metadata or {}).get("closed_reason") or "")
                why = f"closed as already done: {said}" if said else why
            for state in accompanying:
                self._close_state(state.id, RETIRED, why[:500])
            return
        if "scheduled_at" in changed and task.scheduled_at is not None:
            for state in accompanying:
                self._move_state(state, task.scheduled_at)
        if {"title", "description", "scheduled_at"} & set(changed):
            # Renamed or reworded - by the user's edit, or by the agent
            # filling it in - the state says what the task now says, or it
            # would go on naming an event the calendar no longer has.
            for state in accompanying:
                self._retell_state(state, task)

    def _states_for(self, task_id: str) -> List[Any]:
        try:
            return self.states.for_task(task_id)
        except Exception:            # pragma: no cover - a state is context
            log.debug("could not read the states for %s", task_id, exc_info=True)
            return []

    def _close_state(self, state_id: str, status: str, reason: str) -> None:
        try:
            self.states.close(state_id, status=status, reason=reason)
        except Exception:            # pragma: no cover
            log.debug("could not close state %s", state_id, exc_info=True)

    def _move_state(self, state: Any, when: datetime) -> None:
        """Put the span back around the moment it is about."""
        try:
            state.starts_at = when - timedelta(minutes=STATE_LEAD_MINUTES)
            state.ends_at = when + timedelta(minutes=STATE_TAIL_MINUTES)
            self.states._save(state)
        except Exception:            # pragma: no cover
            log.debug("could not move state %s", state.id, exc_info=True)

    def _retell_state(self, state: Any, task: ScheduledTask) -> None:
        """The state's title and detail, written again from the task - the
        same words `_state_for_task` first wrote them in."""
        try:
            state.title = f"Scheduled: {task.title}"[:200]
            if task.scheduled_at is not None:
                state.detail = (
                    f"{task.description} "
                    f"Created because a task was scheduled to run at "
                    f"{task.scheduled_at.isoformat(timespec='minutes')}."
                ).strip()[:1000]
            self.states._save(state)
        except Exception:            # pragma: no cover
            log.debug("could not retell state %s", state.id, exc_info=True)

    def reschedule(self, task_id: str, scheduled_at: datetime) -> Optional[ScheduledTask]:
        return self.update_task(task_id, scheduled_at=scheduled_at, status="pending",
                                lease_token=None)

    def cancel(self, task_id: str, reason: str = "",
               by: str = "") -> Optional[ScheduledTask]:
        """Never run it - and say who said so, when, and why.

        It stays on the calendar, marked cancelled. A notification that was
        set and then called off is part of what happened, and a calendar
        that simply loses it cannot be asked why the reminder never came.
        """
        task = self.store.get(task_id)
        if task is None:
            return None
        metadata = dict(task.metadata or {})
        metadata.update({"cancelled_by": by or "unknown",
                         "cancelled_reason": (reason or "no reason given")[:1000],
                         "cancelled_at": utcnow().isoformat()})
        return self.update_task(task_id, status="cancelled", lease_token=None,
                                metadata=metadata)

    def disable(self, task_id: str) -> Optional[ScheduledTask]:
        return self.update_task(task_id, status="disabled", lease_token=None)

    def enable(self, task_id: str) -> Optional[ScheduledTask]:
        return self.update_task(task_id, status="pending", lease_token=None)

    def delete(self, task_id: str) -> bool:
        # The state goes first: a state naming a task that no longer exists
        # would claim something is going to happen and give nothing to check
        # it against.
        if self.states is not None:
            from ...agent.states.store import RETIRED

            for state in self._states_for(task_id):
                self._close_state(state.id, RETIRED, "the task was deleted")
        deleted = self.store.delete(task_id)
        if deleted and self.bus is not None:
            self.bus.publish("scheduler.task_deleted", {"id": task_id})
        return deleted

    def status(self) -> Dict[str, Any]:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "last_tick": self._last_tick,
            "tick_seconds": int(self.config.get("scheduler_tick_seconds", 60)),
            **self.store.stats(),
        }
