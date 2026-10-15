"""Repeating tasks (section 52.3).

A task repeats on an interval and stops on a limit: after a number of runs, at
a moment, or - if the caller says so explicitly - not at all.

The rule is a closed shape, validated here on the way in. The lesson of
``task_payload`` is that an open dict collects keys nobody reads; the lesson of
``retry_policy`` is that a small dict with fixed keys is fine. This is the
second kind, and the reason it is one field rather than four columns.

    {"every_seconds": 2700}                      every 45 minutes, forever
    {"every_seconds": 2700, "count": 8}          eight times, then done
    {"every_seconds": 86400, "until": "..."}     daily until a date
    {}                                           runs once (the default)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ...common.events.models import parse_iso, utcnow

#: Below this an interval is a mistake rather than a schedule - a task that
#: repeats every few seconds is a loop, and the scheduler would spend its tick
#: budget on it.
MINIMUM_INTERVAL_SECONDS = 60

#: Named intervals, so a caller can say "daily" without arithmetic.
UNITS = {
    "minutes": 60,
    "hours": 3600,
    "days": 86400,
    "weeks": 604800,
}


class RecurrenceError(ValueError):
    """The recurrence rule cannot be honoured as written."""


def parse_recurrence(raw: Any) -> Dict[str, Any]:
    """Validate a rule, or raise saying exactly what is wrong with it.

    Returns ``{}`` for a task that does not repeat, which is the default and
    the shape every existing task already has.
    """
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise RecurrenceError("recurrence must be an object")

    every = raw.get("every_seconds")
    if every is None:
        # The friendlier spelling: {"every": 45, "unit": "minutes"}.
        amount, unit = raw.get("every"), str(raw.get("unit") or "").lower()
        if amount is not None and unit in UNITS:
            every = float(amount) * UNITS[unit]
    if every is None:
        raise RecurrenceError(
            "a repeating task needs an interval: every_seconds, or every plus "
            f"unit (one of {', '.join(sorted(UNITS))})")
    try:
        every = int(float(every))
    except (TypeError, ValueError):
        raise RecurrenceError("the interval must be a number of seconds") from None
    if every < MINIMUM_INTERVAL_SECONDS:
        raise RecurrenceError(
            f"an interval of {every}s repeats faster than the scheduler is meant "
            f"to work; the minimum is {MINIMUM_INTERVAL_SECONDS}s")

    rule: Dict[str, Any] = {"every_seconds": every}

    count = raw.get("count")
    if count is not None:
        try:
            count = int(count)
        except (TypeError, ValueError):
            raise RecurrenceError("count must be a whole number of runs") from None
        if count < 1:
            raise RecurrenceError("a count of less than one run is not a schedule")
        rule["count"] = count

    until = raw.get("until")
    if until:
        moment = parse_iso(until) if isinstance(until, str) else until
        if not isinstance(moment, datetime):
            raise RecurrenceError("until must be an ISO-8601 timestamp")
        rule["until"] = moment

    return rule


def describe(rule: Dict[str, Any]) -> str:
    """One line a person can read, for the calendar and for tool replies."""
    if not rule:
        return "runs once"
    every = int(rule.get("every_seconds") or 0)
    for name in ("weeks", "days", "hours", "minutes"):
        size = UNITS[name]
        if every >= size and every % size == 0:
            amount = every // size
            unit = name[:-1] if amount == 1 else name
            interval = f"every {amount} {unit}" if amount != 1 else f"every {unit}"
            break
    else:
        interval = f"every {every} seconds"
    if rule.get("count"):
        return f"{interval}, {rule['count']} times"
    if rule.get("until"):
        return f"{interval} until {rule['until'].strftime('%d %b %Y %H:%M')} UTC"
    return f"{interval}, no end set"


def next_occurrence(
    rule: Dict[str, Any],
    previous: datetime,
    runs: int,
    now: Optional[datetime] = None,
) -> Tuple[Optional[datetime], str]:
    """When this should run next, and why it is stopping if it is.

    The next time is advanced from the *previous scheduled time* rather than
    from now, so a task that runs a little late does not drift later every
    cycle. It is advanced by whole intervals until it is in the future, so an
    application that was closed for a week comes back to one pending run
    rather than a week of catch-ups nobody wants.
    """
    if not rule:
        return None, "does not repeat"
    now = now or utcnow()
    every = int(rule["every_seconds"])

    count = rule.get("count")
    if count is not None and runs >= count:
        return None, f"finished its {count} runs"

    upcoming = previous + timedelta(seconds=every)
    if upcoming <= now:
        missed = int((now - upcoming).total_seconds() // every) + 1
        upcoming = upcoming + timedelta(seconds=every * missed)

    until = rule.get("until")
    if until is not None and upcoming > until:
        return None, "reached the end of its repeat window"

    return upcoming, "scheduled"


#: How far ahead a repeat with no end is drawn on the calendar (section 52.3).
#:
#: A repeat that never stops has no last occurrence to draw, so something has
#: to bound it. Thirty is enough to see the shape of it - a 45 minute repeat
#: fills the day, a daily one fills the month - and the window moves with the
#: calendar, so reaching the end of one batch simply reveals the next. Nothing
#: is stored for these: they are the one rule, drawn out.
HORIZON = 30


def occurrences(
    rule: Dict[str, Any],
    first: datetime,
    runs: int = 0,
    window_start: Optional[datetime] = None,
    window_end: Optional[datetime] = None,
    limit: int = HORIZON,
) -> List[Tuple[int, datetime]]:
    """Every time this task is still going to run, as (index, moment) pairs.

    ``first`` is the task's current scheduled time - the next run that has not
    happened yet - and ``runs`` is how many are already behind it, so the
    index of each occurrence is its number in the whole series rather than in
    this window.

    Occurrences are computed from the rule, never stored. A repeating task is
    one row (section 52.3); drawing it as thirty rows would be the copying
    that section refuses, with the calendar carrying the consequences.
    """
    if first is None:
        return []
    if not rule:
        if _within(first, window_start, window_end):
            return [(runs, first)]
        return []

    every = timedelta(seconds=int(rule["every_seconds"]))
    count = rule.get("count")
    until = rule.get("until")

    moment, index = first, runs
    # A window far in the future is reached in one step rather than by walking
    # every intervening occurrence.
    if window_start is not None and moment < window_start:
        skipped = int((window_start - moment).total_seconds() // every.total_seconds())
        if skipped > 0:
            moment += every * skipped
            index += skipped

    found: List[Tuple[int, datetime]] = []
    while len(found) < limit:
        if count is not None and index >= count:
            break
        if until is not None and moment > until:
            break
        if window_end is not None and moment > window_end:
            break
        if window_start is None or moment >= window_start:
            found.append((index, moment))
        moment += every
        index += 1
    return found


def _within(moment: datetime,
            start: Optional[datetime], end: Optional[datetime]) -> bool:
    if start is not None and moment < start:
        return False
    if end is not None and moment > end:
        return False
    return True
