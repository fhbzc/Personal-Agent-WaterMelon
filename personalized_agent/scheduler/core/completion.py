"""Filling in a task the user only half-wrote (section 60, as amended).

A task made from the calendar and a task made by the agent are the same kind
of thing, and the scheduler needs the same fields from both: what will happen
when it runs, why it exists, whether it interrupts, and how it will be settled
if its run cannot say whether the work is done.

A person dragging a box across a calendar supplies one or two of those. The
rest are not optional - a task with no description shows the user a blank when
they come back to it a week later, and one with no verification plan gets the
defaults whether or not they suit it. So the agent fills them in, the same way
it would if the user had asked in conversation: from the task itself, it
writes what is missing.

**The event exists first, and is completed afterwards.** This is the part that
differs from a conversation. The row is written the moment the user presses
add, holding exactly what they typed, and the filling-in happens behind them.
Waiting for a model call before the event appears would make the calendar feel
broken for the second or two it takes - and if the model were unavailable, the
event the user just created would not exist at all. Written first, the worst
case is an event that stays sparse.

Nothing here invents a time. Whatever the user put in the box is the one thing
they certainly meant, and a filled-in field never overwrites a given one.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...common.languages import in_display
from ...models.base import ModelRequest

log = logging.getLogger(__name__)

#: Where a task records that it is still being filled in.
COMPLETION = "completion"
PENDING = "pending"
DONE = "done"
FAILED = "failed"

#: What the agent may fill in. Deliberately not the time, and not the title:
#: those are what the user actually said, and the rest is what they left to
#: the agent by not saying it.
FILLABLE = ("description", "rationale", "notify_user", "verification")

SYSTEM_PROMPT = """You complete a task the user started on their calendar.

They gave you a title and a time. Work out the rest from what you know of them,
and answer only for the fields you were asked for. Do not restate the title,
and do not invent a different time.

Return ONLY a JSON object with these keys, omitting any you were not asked for:
{
  "description": "what will happen when this runs, in a sentence or two the
                  user can read at a glance",
  "rationale": "why this is worth doing, and on what evidence; not shown
                unless they ask",
  "notify_user": true|false,
  "verification": {"completed_when": "the rule that counts as finished",
                   "check_after_seconds": 600,
                   "recheck_after_seconds": 1800}
}

notify_user is whether running this interrupts them. A reminder they wrote for
themselves usually should; a check the agent does on its own should not.

Give "verification" when the task starts something rather than doing it - the
run would return before the work is over. Set the intervals by how urgent it
is: minutes for something pressing, hours for background work.
"""


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


def missing_fields(task: Any) -> List[str]:
    """What this task still needs, in the order the prompt asks for it.

    Absent rather than empty is the test throughout: a user who typed a
    description and deleted it back to nothing has said something, and an
    explicit "do not interrupt me" is a choice rather than a gap.
    """
    payload = task.task_payload or {}
    metadata = task.metadata or {}
    missing = []
    if not (task.description or "").strip():
        missing.append("description")
    if not str(metadata.get("rationale") or "").strip():
        missing.append("rationale")
    if "notify_user" not in payload:
        missing.append("notify_user")
    if not isinstance(payload.get("verification"), dict):
        missing.append("verification")
    return missing


def state_of(task: Any) -> str:
    entry = (task.metadata or {}).get(COMPLETION)
    return str(entry.get("state") or "") if isinstance(entry, dict) else ""


def is_pending(task: Any) -> bool:
    return state_of(task) == PENDING


def mark(metadata: Dict[str, Any], state: str,
         missing: Optional[List[str]] = None, note: str = "") -> Dict[str, Any]:
    entry: Dict[str, Any] = {"state": state,
                             "at": datetime.now(timezone.utc).isoformat()}
    if missing is not None:
        entry["missing"] = list(missing)
    if note:
        entry["note"] = note[:500]
    out = dict(metadata or {})
    out[COMPLETION] = entry
    return out


class TaskCompleter:
    """Fills in the blanks of a calendar-made task, off the request thread."""

    def __init__(self, *, scheduler: Any, models: Any,
                 config: Any = None, bus: Any = None, recorder: Any = None) -> None:
        self.scheduler = scheduler
        self.models = models
        self.config = config
        self.bus = bus
        self.recorder = recorder

    # -- the part the request thread calls ----------------------------------
    def request(self, task: Any) -> Any:
        """Mark what is missing and start filling it in. Returns at once.

        The marking is a write of its own rather than something the worker
        does when it gets there: the calendar redraws immediately, and a task
        that is visibly being worked on is better than one that looks finished
        and changes under the user a moment later.
        """
        missing = missing_fields(task)
        if not missing:
            return task
        updated = self.scheduler.update_task(
            task.id, metadata=mark(task.metadata, PENDING, missing))
        self._start(task.id)
        return updated or task

    def _start(self, task_id: str) -> None:
        threading.Thread(target=self.complete, args=(task_id,),
                         name=f"complete-{task_id[:8]}", daemon=True).start()

    # -- the part the worker runs -------------------------------------------
    def complete(self, task_id: str) -> Optional[Any]:
        """Ask the model for the missing fields and write them in.

        Anything the user has since typed themselves is left alone: this runs
        behind them, and they may well be editing the thing it is completing.
        """
        task = self.scheduler.store.get(task_id)
        if task is None:
            return None
        missing = missing_fields(task)
        if not missing:
            return self._settle(task, DONE, note="nothing was missing")

        try:
            filled = self._ask(task, missing)
        except Exception as exc:               # pragma: no cover - reported
            log.info("could not complete task %s (%s)", task_id, exc)
            return self._settle(task, FAILED, note=str(exc))
        if filled is None:
            return self._settle(task, FAILED, note="the model gave nothing usable")

        # Re-read: the user may have filled something in themselves while this
        # was thinking, and what they typed wins over what was guessed.
        task = self.scheduler.store.get(task_id) or task
        fields = self._fields_from(task, filled, missing_fields(task))
        metadata = mark(fields.pop("metadata", dict(task.metadata or {})), DONE,
                        note="filled in by the agent")
        fields["metadata"] = metadata
        updated = self.scheduler.update_task(task_id, **fields)
        if self.bus is not None:
            self.bus.publish("scheduler.task_completed_by_agent",
                             {"task_id": task_id, "filled": sorted(filled)})
        return updated

    # -- internals ----------------------------------------------------------
    def _ask(self, task: Any, missing: List[str]) -> Optional[Dict[str, Any]]:
        when = task.scheduled_at.isoformat() if task.scheduled_at else "unspecified"
        prompt = (
            f"The task they started:\n"
            f"  title: {task.title}\n"
            f"  when: {when}\n"
            f"  repeats: {'yes' if task.recurrence else 'no'}\n"
            f"  kind: {task.execution_type}\n"
            f"  what they wrote: {task.description or '(nothing)'}\n\n"
            f"Fill in only these: {', '.join(missing)}"
        )
        response = self.models.invoke(
            ModelRequest(messages=[{"role": "user", "content": prompt}],
                         system=SYSTEM_PROMPT,
                         max_tokens=800,
                         task="task_completion",
                         language=in_display()),
            role="task_completion")
        return _extract_json(response.text)

    @staticmethod
    def _fields_from(task: Any, filled: Dict[str, Any],
                     still_missing: List[str]) -> Dict[str, Any]:
        """Turn the model's answer into store fields, for the gaps only."""
        from ..execution.executors import (DEFAULT_CHECK_AFTER_SECONDS,
                                           DEFAULT_RECHECK_AFTER_SECONDS,
                                           VERIFICATION, _interval)

        fields: Dict[str, Any] = {}
        payload = dict(task.task_payload or {})
        metadata = dict(task.metadata or {})
        wanted = set(still_missing)

        if "description" in wanted and str(filled.get("description") or "").strip():
            fields["description"] = str(filled["description"]).strip()
        if "rationale" in wanted and str(filled.get("rationale") or "").strip():
            metadata["rationale"] = str(filled["rationale"]).strip()
        if "notify_user" in wanted and isinstance(filled.get("notify_user"), bool):
            payload["notify_user"] = filled["notify_user"]
        if "verification" in wanted and isinstance(filled.get("verification"), dict):
            plan = filled["verification"]
            payload[VERIFICATION] = {
                "completed_when": str(plan.get("completed_when") or "")[:500],
                "check_after_seconds": _interval(plan.get("check_after_seconds"),
                                                 DEFAULT_CHECK_AFTER_SECONDS),
                "recheck_after_seconds": _interval(plan.get("recheck_after_seconds"),
                                                   DEFAULT_RECHECK_AFTER_SECONDS),
            }
        fields["task_payload"] = payload
        fields["metadata"] = metadata
        return fields

    def _settle(self, task: Any, state: str, note: str = "") -> Optional[Any]:
        return self.scheduler.update_task(
            task.id, metadata=mark(task.metadata, state, note=note))


__all__ = ["TaskCompleter", "COMPLETION", "PENDING", "DONE", "FAILED",
           "FILLABLE", "missing_fields", "state_of", "is_pending", "mark"]
