"""Task execution strategies (sections 55-58).

Each execution type is a small isolated strategy. Model based tasks are
answered from the task itself - nothing is searched for - and script based
tasks run through the sandbox runtime, never through a bare subprocess call.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

from ...common.events.models import ScheduledTask
from ...models.base import ModelRequest
from ...common.languages import in_display
from ...models.manager import ModelUnavailable
from ...agent.feedback.templates import PASSIVE, REMINDER
from ..storage.store import AWAITING_VERIFICATION

log = logging.getLogger(__name__)

def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        candidate = text[start:end + 1] if start != -1 and end > start else None
    if not candidate:
        return None
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except ValueError:
        return None


# Section 52.2: a scheduled task interrupts only if it was made to.
#
# The flag used to default to True, so every task that ran raised a
# notification, and it was passed force=True, which walks past both the user's
# own "notifications enabled" setting and the duplicate suppression. A calendar
# full of checks the agent set for itself therefore interrupted for all of
# them. Silence is the default now, and a notification that does go out is an
# ordinary one: the user's settings still apply to it.
def _wants_notification(task) -> bool:
    return bool(task.task_payload.get("notify_user", False))


#: What becomes of a task once its execution has returned without error.
#:
#: "It ran" and "it is finished" are different claims, and only some kinds of
#: work can make the second one. A notification is delivered or it is not. A
#: script that kicks off a build, or an agent task told to get something done,
#: returns as soon as it has done its part - which is not the same as the
#: thing being done.
#:
#: The task is finished. Nothing more will happen to it unless it repeats.
DONE = "completed"
#: Not finished, and not wrong either - it is simply not time. The task goes
#: back on the calendar at a new time and will be run again as it stands.
DELAYED = "delayed"
#: It may be finished; the execution cannot tell. A check of its own is put on
#: the calendar, and that check settles it. Better a task that says it is
#: unsure than one that reports success and leaves the user to find out.
TO_BE_CHECKED = "to_be_checked"

DISPOSITIONS = (DONE, DELAYED, TO_BE_CHECKED)

#: The execution type of a check. It is an ordinary scheduled task - it has a
#: time, it can be moved, the calendar shows it - which is the point: being
#: unsure produces more scheduled work, not a second queue somewhere else with
#: its own rules for draining it.
CHECK_STATUS = "check_status"

#: The payload key naming the task a check was made for.
CHECKS_TASK_ID = "checks_task_id"

#: The payload key holding a task's plan for being settled.
#:
#: Decided when the task is created, not when the check runs. Whoever schedules
#: the work is the one who knows what finishing looks like and how soon it is
#: worth asking - a check written at check time has to guess both from a title
#: and a stale result string. It also means the plan is visible on the task
#: from the moment it exists, rather than being invented later by something the
#: user never saw scheduled.
VERIFICATION = "verification"

#: How long after an unsure run its check falls due, when the task named no
#: interval of its own. Ten minutes: long enough for something started to have
#: got somewhere, short enough that a finished task is not left hanging.
DEFAULT_CHECK_AFTER_SECONDS = 600

#: How long an unfinished task waits before running again, absent a plan.
DEFAULT_RECHECK_AFTER_SECONDS = 1800

#: Bounds on both, so that a plan cannot take a task off the calendar in all
#: but name, nor spin the scheduler by asking to be checked every second.
MIN_INTERVAL_SECONDS = 0
MAX_INTERVAL_SECONDS = 60 * 60 * 24 * 7


def _interval(value: Any, fallback: int) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS, seconds))


def verification_plan(task: ScheduledTask) -> Dict[str, Any]:
    """How this task is to be settled, as decided when it was created.

    Always returns a usable plan. A task scheduled before this existed, or by
    something that did not think about it, gets the defaults rather than an
    error - being unsure is already the awkward case, and refusing to check
    would leave the task unsettled forever.
    """
    raw = task.task_payload.get(VERIFICATION)
    raw = raw if isinstance(raw, dict) else {}
    return {
        # The rule the check judges against. Free text on purpose: what counts
        # as finished is particular to the task, and the one who scheduled it
        # is the one who can say.
        "completed_when": str(raw.get("completed_when") or "").strip(),
        # 0 means check as soon as the scheduler next comes round.
        "check_after_seconds": _interval(raw.get("check_after_seconds"),
                                         DEFAULT_CHECK_AFTER_SECONDS),
        "recheck_after_seconds": _interval(raw.get("recheck_after_seconds"),
                                           DEFAULT_RECHECK_AFTER_SECONDS),
    }


@dataclass
class ExecutionOutcome:
    """What an execution returned, and what should become of the task.

    ``ok`` says whether the execution itself went wrong. ``disposition`` says
    something different: whether the *work* is finished. The two are not the
    same question, and collapsing them is what made every successful run mean
    "done" - a run that returned cleanly having only started something had no
    way to say so, and the task was closed underneath it.
    """

    ok: bool
    result: str
    retryable: bool = False
    metadata: Optional[Dict[str, Any]] = None
    #: One of DONE, DELAYED, TO_BE_CHECKED. Read only when ``ok``; a failure
    #: is governed by the retry policy, which is a separate decision.
    disposition: str = DONE
    #: When DELAYED, the time to try again. Ignored otherwise.
    next_attempt_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "result": self.result, "retryable": self.retryable,
                "disposition": self.disposition,
                "next_attempt_at": self.next_attempt_at.isoformat()
                if self.next_attempt_at else None,
                "metadata": self.metadata or {}}


class TaskExecutor:
    """Dispatches a scheduled task to the strategy for its execution type."""

    def __init__(
        self,
        *,
        runtime: Any,
        models: Any,
        notifications: Any,
        store: Any = None,
        questions: Any = None,
        recorder: Any,
        config: Any = None,
        bus: Any = None,
        agent_runner: Optional[Callable[[str, Dict[str, Any]], str]] = None,
    ) -> None:
        self.runtime = runtime
        #: The schedule itself. A check_status task settles the task it was
        #: made for, which is a row, not a running thing - so the store is all
        #: it needs and the scheduler loop is none of its business.
        self.store = store
        self.models = models
        self.notifications = notifications
        #: Section 52.4: where "was that worth interrupting you for?" goes.
        self.questions = questions
        self.recorder = recorder
        self.config = config
        self.bus = bus
        self.agent_runner = agent_runner
        self.system_actions: Dict[str, Callable[[Dict[str, Any]], str]] = {
            "clear_workspace_tmp": self._action_clear_tmp,
        }

    # -- dispatch -----------------------------------------------------------
    def execute(self, task: ScheduledTask) -> ExecutionOutcome:
        handler = {
            "notification": self._notification,
            "python_script": self._python_script,
            "agent_task": self._agent_task,
            "model_task": self._model_task,
            "system_action": self._system_action,
            "integration_action": self._integration_action,
            CHECK_STATUS: self._check_status,
        }.get(task.execution_type)
        if handler is None:
            return ExecutionOutcome(False, f"unsupported execution type {task.execution_type}")
        try:
            return handler(task)
        except ModelUnavailable as exc:
            # Never silently substitute a provider (section 94).
            return ExecutionOutcome(
                False,
                f"model unavailable: {exc}",
                retryable=not exc.availability.requires_user_action(),
                metadata={"availability": exc.availability.to_dict()},
            )
        except Exception as exc:
            log.exception("scheduled task %s failed", task.id)
            return ExecutionOutcome(False, f"{type(exc).__name__}: {exc}", retryable=True)

    # -- strategies ---------------------------------------------------------
    def _notification(self, task: ScheduledTask) -> ExecutionOutcome:
        """Deliver a message - as an interruption, or onto the calendar.

        Section 52.2 makes the choice per task, and this kind of task has to
        honour it like the others. A notification task set to silent is not a
        task that does nothing: its message becomes the result, which is what
        the calendar shows. What changes is whether the user is taken away
        from what they are doing to read it.
        """
        message = task.task_payload.get("message") or task.description or task.title
        if not _wants_notification(task):
            return ExecutionOutcome(True, message)
        # WHICH KIND, as the task says. Every scheduled notification
        # arrived as a reminder, asking "shall I remind you again?" about a
        # sentence that wanted only to be read; a notice is now a notice.
        template = task.task_payload.get("template")
        if template not in (REMINDER, PASSIVE):
            template = REMINDER
        self.notifications.notify(
            task.title, message, kind="proactive",
            metadata={"task_id": task.id, "source": "scheduler"},
            template=template, question=self._reply_for(task),
        )
        self._ask_whether_it_was_worth_it(task)
        return ExecutionOutcome(True, "notification delivered")

    def _check_status(self, task: ScheduledTask) -> ExecutionOutcome:
        """Settle the task this check was made for.

        A check has exactly two things it can conclude, and it concludes them
        about the *other* task, never about itself:

          finished     the original is completed
          not yet      the original is delayed, and will run again

        The delay lands on the original for a reason. Delaying the check would
        leave the original parked as unsettled while the thing that was meant
        to settle it drifts further away - the work would sit undone and the
        calendar would show nothing due. Delaying the original puts it back
        where it can actually make progress.

        The check itself always ends completed. It asked its question and got
        an answer; that is the whole of its job.
        """
        if self.store is None:
            return ExecutionOutcome(False, "no schedule to check against")
        original_id = str(task.task_payload.get(CHECKS_TASK_ID) or "")
        if not original_id:
            return ExecutionOutcome(False, f"a {CHECK_STATUS} task names no task to check")
        original = self.store.get(original_id)
        if original is None:
            return ExecutionOutcome(True, "the task it was checking is gone")
        if original.status != AWAITING_VERIFICATION:
            # Answered some other way - cancelled, or settled by hand. The
            # check has nothing left to decide and says so rather than
            # overwriting whatever settled it.
            return ExecutionOutcome(
                True, f"already settled as {original.status}; nothing to check")

        plan = verification_plan(original)
        verdict = self._ask_whether_it_finished(original, plan)
        if verdict is None:
            # Undecidable this time. The check is not finished either, so it
            # delays *itself* - the one case where that is right, because the
            # original has not been looked at yet.
            return ExecutionOutcome(
                True, "could not tell yet; will look again", disposition=DELAYED)

        if verdict.get("finished"):
            self.store.complete(original_id,
                                str(verdict.get("reason") or "verified as finished"))
            return ExecutionOutcome(True, f"{original.title}: finished")

        # The plan wins over the model's suggestion. Whoever scheduled the work
        # knew how urgent it was; the check only knows whether it is done.
        seconds = plan["recheck_after_seconds"]
        self.store.delay(original_id,
                         datetime.now(timezone.utc) + timedelta(seconds=seconds),
                         str(verdict.get("reason") or "not finished yet"))
        return ExecutionOutcome(
            True, f"{original.title}: not finished; runs again in {seconds // 60} min")

    def _ask_whether_it_finished(self, original: ScheduledTask,
                                 plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Ask the model whether one task's work actually got done.

        The rule it judges against comes from the task, written when the task
        was created. Without one the check has to infer what finishing means
        from a title and a result string, which is how a check comes to accept
        "the script ran" as "the work is done".
        """
        last_result = str((original.metadata or {}).get("last_result") or "")
        rule = plan["completed_when"] or (
            "no rule was recorded - judge whether the work described above is "
            "actually done")
        prompt = (
            "A scheduled task ran and could not tell whether the work it was "
            "meant to do is now finished. Decide.\n\n"
            f"The task: {original.title}\n"
            f"What it was meant to do: {original.description or '(no detail given)'}\n"
            f"Counts as finished when: {rule}\n"
            f"What the run reported: {last_result[:1500] or '(nothing)'}\n"
            f"When it ran: {original.last_run_at or '(unknown)'}\n\n"
            "Return ONLY JSON:\n"
            '{"finished": true|false, "reason": "what makes you say so"}\n\n'
            "Judge against the rule above, not against whether the run ended. "
            "A run that returned cleanly having only started the work is not "
            "finished."
        )
        try:
            response = self.models.invoke(
                ModelRequest(messages=[{"role": "user", "content": prompt}],
                             max_tokens=500, task="verification",
                             language=in_display("the reason")),
                role="scheduler")
        except Exception as exc:
            log.info("could not check task %s this run (%s)", original.id, exc)
            return None
        payload = _extract_json(response.text)
        if not isinstance(payload, dict) or "finished" not in payload:
            return None
        return payload

    def _reply_for(self, task: ScheduledTask) -> Dict[str, Any]:
        """What the question raised alongside this interruption should say.

        The same three things the standalone ask uses - what the task was,
        why the agent arranged it, and how long "remind me later" should be
        for this particular task - so that whichever of the two raises the
        question first, the user sees the same one.
        """
        try:
            delay = int(verification_plan(task)["recheck_after_seconds"])
        except Exception:            # pragma: no cover - the template default
            delay = 0
        return {"task_id": task.id, "question": task.title,
                "task_detail": task.description,
                "arrangement": str(task.metadata.get("rationale") or ""),
                "delay_seconds": delay}

    def _ask_whether_it_was_worth_it(self, task: ScheduledTask) -> None:
        """Section 52.4: an interruption asks whether it should have happened.

        The agent decides on its own judgement what is worth interrupting for,
        and until now nothing recorded whether it judged well. One question,
        three answers, asked only of the notifications that actually reached
        the user - which is why it is asked here rather than at scheduling
        time, when nobody has been interrupted yet.
        """
        if self.questions is None:
            return
        from ...agent.feedback.store import TaskQuestion
        from ...agent.feedback.templates import options_for

        try:
            if self.questions.pending_for(task.id, REMINDER) is not None:
                return
            plan = verification_plan(task)
            self.questions.ask(TaskQuestion(
                task_id=task.id,
                kind=REMINDER,
                question=task.title,
                task_detail=task.description,
                arrangement=str(task.metadata.get("rationale") or ""),
                metadata={"options": [o.to_dict() for o in options_for(
                    REMINDER, int(plan["recheck_after_seconds"]))]},
            ))
        except Exception:            # a question must never break the task
            log.exception("could not ask for feedback on task %s", task.id)

    def _python_script(self, task: ScheduledTask) -> ExecutionOutcome:
        timeout = float((self.config or {}).get("script_timeout_seconds", 120)
                        if self.config else 120)
        if task.script_path:
            result = self.runtime.run_python_script(task.script_path, timeout=timeout)
        else:
            code = task.task_payload.get("code")
            if not code:
                return ExecutionOutcome(False, "no script_path and no inline code")
            result = self.runtime.run_python_code(code, timeout=timeout)
        summary = (result.stdout or result.stderr or "").strip()[:2000]
        return ExecutionOutcome(
            result.ok, summary or f"exit code {result.returncode}",
            retryable=result.timed_out, metadata=result.to_dict(),
        )

    def _agent_task(self, task: ScheduledTask) -> ExecutionOutcome:
        if self.agent_runner is None:
            return ExecutionOutcome(False, "no agent core is attached to the scheduler")
        instruction = task.task_payload.get("instruction") or task.description or task.title
        answer = self.agent_runner(instruction, {"task_id": task.id, "origin": "scheduler"})
        if _wants_notification(task):
            self.notifications.notify(
                task.title, answer[:500], kind="proactive",
                metadata={"task_id": task.id},
                template=REMINDER, question=self._reply_for(task),
            )
            self._ask_whether_it_was_worth_it(task)
        return ExecutionOutcome(True, answer[:2000])

    def _model_task(self, task: ScheduledTask) -> ExecutionOutcome:
        prompt = task.task_payload.get("prompt") or task.description or task.title
        response = self.models.invoke(
            ModelRequest(
                messages=[{"role": "user", "content": f"Task:\n{prompt}"}],
                system="You are a personal assistant executing a scheduled task.",
                max_tokens=int((self.config.get("max_output_tokens", 4096)) if self.config else 4096),
                task="scheduled_task",
                language=in_display(),
            ),
            role="scheduled_task",
        )
        if _wants_notification(task):
            self.notifications.notify(
                task.title, response.text[:500], kind="proactive",
                template=REMINDER, question=self._reply_for(task),
                metadata={"task_id": task.id},
            )
            self._ask_whether_it_was_worth_it(task)
        return ExecutionOutcome(True, response.text[:2000],
                                metadata={"tokens": response.usage.total_tokens})

    def _system_action(self, task: ScheduledTask) -> ExecutionOutcome:
        action = task.task_payload.get("action")
        handler = self.system_actions.get(str(action))
        if handler is None:
            return ExecutionOutcome(
                False, f"unknown system action {action!r}; known: {sorted(self.system_actions)}"
            )
        return ExecutionOutcome(True, handler(task.task_payload))

    def _integration_action(self, task: ScheduledTask) -> ExecutionOutcome:
        """Hand the action to an external integration over the event bus."""
        payload = {"task_id": task.id, "title": task.title, **task.task_payload}
        if self.bus is None:
            return ExecutionOutcome(False, "no event bus available for integration actions")
        self.bus.publish("integration.action_requested", payload)
        return ExecutionOutcome(True, "integration action published to listening integrations")

    # -- system actions -----------------------------------------------------
    def _action_clear_tmp(self, payload: Dict[str, Any]) -> str:
        tmp = self.runtime.policy.subdir("tmp")
        removed = 0
        for child in tmp.iterdir():
            try:
                self.runtime.delete(child)
                removed += 1
            except Exception:
                continue
        return f"removed {removed} entries from the workspace temporary directory"
