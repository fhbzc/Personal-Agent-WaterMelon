"""The user editing an event on the calendar.

A calendar whose entries can only be disabled or deleted makes the user say
"not this" when what they mean is "this, but at two" - and the agent then
learns the wrong thing from it. So an event can be edited: its title, the
words its notification shows, its time, and how it repeats.

WHAT CAN BE EDITED. An event still to come: one waiting to run (pending) or
switched off by the user (disabled). One running now, already done, closed
as done or cancelled is a record of what happened, and rewriting it would
make the calendar say something happened that did not - so it is refused,
and refused as a conflict (409) rather than as a bad request: nothing is
wrong with what was asked, only with when.

A SERIES IS EDITED WHOLE. A repeating event is one row drawn many times
(section 52.3); an edit is to that row, so it reaches every occurrence. A
new title or message is taken out of any single occurrence that had its
own, or that occurrence would go on showing the old one. A new time or a
new repeat is a new grid: the occurrences moved or deleted one at a time
were places on the old grid, and there is nothing on the new one for them
to mean - so they are let go of, and the series starts again from its next
run. Its count of runs starts again too: "after 5 times", said in an edit,
means five more, not five counting the ones already behind it.

NOTHING ELSE CHANGES. Who made the event and why they did are what the
lessons read the edit against (heartbeat/core/calendar_nature.py), so they
are kept as they were; the edit is written beside them in the metadata -
when, which fields, and how many times it has been edited.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from . import series
from .recurrence import RecurrenceError, parse_recurrence
from ...common.events.models import iso, parse_iso, utcnow

#: Which statuses an event can be edited in.
EDITABLE = ("pending", "disabled")

#: Where an edit is written on the task.
EDITED = "edited_by_user"

#: A time "now" is not in the past: the minute the form was filled in has
#: usually begun by the time Save is pressed.
PAST_GRACE = timedelta(seconds=90)

#: The longest a title may be; the calendar's own, from the add form.
TITLE_CHARS = 200

#: Not given - distinct from given as empty, which for a repeat means "runs
#: once".
_UNSET = object()


class EditRefused(ValueError):
    """The edit cannot be made as asked. ``status`` is the HTTP status the
    route answers with."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def rule_from_now(task: Any) -> Dict[str, Any]:
    """The repeat as it stands from the next run on: a count of runs is the
    runs still to come, not the ones already behind it."""
    rule = dict(task.recurrence or {})
    if rule.get("count") is not None:
        rule["count"] = max(1, int(rule["count"]) - int(task.runs or 0))
    return rule


def _same_rule(one: Dict[str, Any], other: Dict[str, Any]) -> bool:
    if not one and not other:
        return True
    if bool(one) != bool(other):
        return False
    return (int(one.get("every_seconds") or 0) == int(other.get("every_seconds") or 0)
            and one.get("count") == other.get("count")
            and _moment(one.get("until")) == _moment(other.get("until")))


def _moment(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    return parse_iso(str(value))


def plan(task: Any, *, title: Any = _UNSET, message: Any = _UNSET,
         at: Any = _UNSET, repeat: Any = _UNSET,
         now: Optional[datetime] = None) -> Tuple[Dict[str, Any], List[str]]:
    """The store fields an edit comes to, and which of the user's fields it
    changed. Nothing is written: the caller writes it, or nothing is.

    A field given with the value it already has is not a change - the form
    may send back what it showed - and an edit that changes nothing comes to
    no fields at all.
    """
    if task is None:
        raise EditRefused("task not found", 404)
    if task.status not in EDITABLE:
        raise EditRefused(
            f"this event is {task.status} and can no longer be edited - only "
            f"one still to come (waiting to run, or disabled) can be", 409)
    now = now or utcnow()
    fields: Dict[str, Any] = {}
    changed: List[str] = []

    if title is not _UNSET:
        wanted = str(title or "").strip()
        if not wanted:
            raise EditRefused("the title cannot be empty")
        if len(wanted) > TITLE_CHARS:
            raise EditRefused(f"the title is longer than {TITLE_CHARS} characters")
        if wanted != task.title:
            fields["title"] = wanted
            changed.append("title")

    if message is not _UNSET:
        wanted = str(message or "").strip()
        if not wanted:
            raise EditRefused("the message cannot be empty")
        payload = dict(task.task_payload or {})
        shown = payload.get("message") or task.description
        if wanted != shown or (task.execution_type == "notification"
                               and payload.get("message") != wanted):
            # A notification shows its payload's message, and its detail
            # says the same; anything else has only the detail.
            if task.execution_type == "notification":
                payload["message"] = wanted
                fields["task_payload"] = payload
            fields["description"] = wanted
            if wanted != shown:
                changed.append("message")

    moved = False
    when = task.scheduled_at
    if at is not _UNSET and at not in (None, ""):
        wanted = at if isinstance(at, datetime) else parse_iso(str(at))
        if wanted is None:
            raise EditRefused("the time must be an ISO-8601 timestamp")
        if task.scheduled_at is None or abs(
                (wanted - task.scheduled_at).total_seconds()) >= 60:
            if wanted < now - PAST_GRACE:
                raise EditRefused("that time is in the past - an event can only "
                                  "be moved to a time still to come")
            when = wanted
            moved = True
            changed.append("time")

    rule = rule_from_now(task)
    if repeat is not _UNSET:
        try:
            wanted_rule = parse_recurrence(repeat)
        except RecurrenceError as exc:
            raise EditRefused(f"the repeat cannot be read: {exc}") from exc
        until = wanted_rule.get("until")
        if until is not None and when is not None and until < when:
            raise EditRefused("the repeat ends before the event's first run")
        if not _same_rule(wanted_rule, rule):
            rule = wanted_rule
            changed.append("repeat")
            moved = True

    if moved:
        # A new time or a new repeat is a new grid (see the module's note):
        # the series starts again from its next run, and what was moved or
        # deleted one occurrence at a time on the old grid is let go of.
        metadata = dict(task.metadata or {})
        metadata.pop(series.SERIES_ANCHOR, None)
        metadata.pop(series.OVERRIDES, None)
        if task.runs:
            metadata["runs_before_edit"] = int(
                metadata.get("runs_before_edit") or 0) + int(task.runs)
        fields.update(scheduled_at=when, recurrence=rule, runs=0, attempts=0,
                      lease_token=None, metadata=metadata)
    elif {"title", "message"} & set(changed) and series.overrides_of(task):
        # A series renamed or reworded: every occurrence says the new words,
        # including one that had been given its own.
        gone = {"title"} if "title" in changed else set()
        gone |= {"description"} if "message" in changed else set()
        metadata = dict(task.metadata or {})
        metadata[series.OVERRIDES] = {
            index: {key: value for key, value in entry.items() if key not in gone}
            for index, entry in series.overrides_of(task).items()
            if isinstance(entry, dict)}
        fields["metadata"] = metadata

    if not changed:
        return {}, []
    metadata = dict(fields.get("metadata") or task.metadata or {})
    earlier = metadata.get(EDITED) if isinstance(metadata.get(EDITED), dict) else {}
    metadata[EDITED] = {"at": iso(now), "fields": changed,
                        "times": int(earlier.get("times") or 0) + 1}
    fields["metadata"] = metadata
    return fields, changed


__all__ = ["EDITABLE", "EDITED", "EditRefused", "plan", "rule_from_now"]
