"""One occurrence of a repeating task, changed without changing the rest.

A calendar shows a repeating task as many blocks, and the user expects to be
able to act on one of them - to delete Thursday's without deleting Thursday's
forever. That is the "this event / all events" choice every calendar asks, and
it needs somewhere to record the answer, because the blocks themselves are not
stored (section 52.3: a repeating task is one row).

So the row carries two things beyond its rule:

**A series anchor.** ``scheduled_at`` was doing two jobs - marking where the
next run happens *and* anchoring the grid every later occurrence is computed
from. Those agree until one occurrence is moved, at which point moving
Thursday to Friday drags every following week onto Fridays too. The anchor
separates them: the grid is ``anchor + index x interval`` and never moves,
while ``scheduled_at`` says when the next run actually happens.

**Overrides, by occurrence index.** One entry per occurrence the user has
touched:

    {"skipped": true}                  deleted, this one only
    {"scheduled_at": "...", ...}       moved, or retitled, this one only

An override is deliberately sparse - it holds what was changed and nothing
else, so a later edit to the series title still reaches an occurrence whose
time was moved. Overriding by copying the whole block would freeze it at the
moment it was touched, which is the bug that makes calendars show a stale
title on one week of a series.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ...common.events.models import iso, parse_iso

log = logging.getLogger(__name__)

#: Where the grid starts, and what has been changed one occurrence at a time.
SERIES_ANCHOR = "series_anchor"
OVERRIDES = "occurrence_overrides"

#: Which of the two things the user meant by acting on a block.
THIS_OCCURRENCE = "this"
WHOLE_SERIES = "all"
SCOPES = (THIS_OCCURRENCE, WHOLE_SERIES)

#: Fields an override may carry. Anything else belongs to the series: an
#: occurrence is a time, not a separate task, and letting it diverge further
#: would make "all events" unable to reach it.
OVERRIDABLE = ("scheduled_at", "title", "description")


def parse_block_id(raw: Any) -> Tuple[str, Optional[int]]:
    """Split ``task_123#7`` into the row that owns it and which run it is.

    A block with no index is the live row itself, which is why the index is
    ``None`` rather than zero - "no particular occurrence" and "occurrence
    zero" are different things.
    """
    text = str(raw or "")
    if "#" not in text:
        return text, None
    task_id, _, suffix = text.partition("#")
    try:
        return task_id, int(suffix)
    except ValueError:
        return task_id, None


def interval_of(task: Any) -> Optional[timedelta]:
    rule = task.recurrence or {}
    seconds = rule.get("every_seconds")
    if not seconds:
        return None
    return timedelta(seconds=int(seconds))


def anchor_of(task: Any) -> Optional[datetime]:
    """Where occurrence zero sits.

    Derived for a task made before anchors existed, or one whose anchor was
    lost: the grid it has been running on is ``scheduled_at`` minus the runs
    behind it. That reconstructs the original grid exactly as long as no
    occurrence has been moved, which is true of every task that predates this.
    """
    stored = parse_iso((task.metadata or {}).get(SERIES_ANCHOR))
    if stored is not None:
        return stored
    interval = interval_of(task)
    if interval is None or task.scheduled_at is None:
        return task.scheduled_at
    return task.scheduled_at - interval * int(task.runs or 0)


def grid_moment(task: Any, index: int) -> Optional[datetime]:
    """When occurrence ``index`` falls, before any override is applied."""
    anchor = anchor_of(task)
    interval = interval_of(task)
    if anchor is None:
        return None
    if interval is None:
        return anchor if index == 0 else None
    return anchor + interval * index


def overrides_of(task: Any) -> Dict[str, Dict[str, Any]]:
    raw = (task.metadata or {}).get(OVERRIDES)
    return dict(raw) if isinstance(raw, dict) else {}


def override_for(task: Any, index: int) -> Dict[str, Any]:
    entry = overrides_of(task).get(str(index))
    return dict(entry) if isinstance(entry, dict) else {}


def is_skipped(task: Any, index: int) -> bool:
    return bool(override_for(task, index).get("skipped"))


def with_override(task: Any, index: int, changes: Dict[str, Any]) -> Dict[str, Any]:
    """The task's metadata with one occurrence's override merged in.

    Returns metadata rather than writing it, so the caller decides whether this
    is a store update, and so nothing is half-applied if it refuses.
    """
    metadata = dict(task.metadata or {})
    # Record the anchor the moment anything is overridden. After this the grid
    # must not be re-derived from scheduled_at, because scheduled_at may be
    # about to move off it.
    if SERIES_ANCHOR not in metadata:
        anchor = anchor_of(task)
        if anchor is not None:
            metadata[SERIES_ANCHOR] = iso(anchor)
    overrides = dict(overrides_of(task))
    entry = dict(overrides.get(str(index)) or {})
    entry.update(changes)
    overrides[str(index)] = entry
    metadata[OVERRIDES] = overrides
    return metadata


def skip(task: Any, index: int) -> Dict[str, Any]:
    """Metadata with one occurrence removed - "delete this event"."""
    return with_override(task, index, {"skipped": True})


def apply_override(block: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Lay one occurrence's changes over the series' own fields."""
    for key in OVERRIDABLE:
        if key in override and override[key]:
            block[key] = override[key]
    if override:
        block["overridden"] = True
    return block


def next_unskipped(task: Any, index: int, limit: int = 500) -> Tuple[int, Optional[datetime]]:
    """The first occurrence at or after ``index`` the user has not deleted.

    Used when the series has to move past a skipped run: the row cannot simply
    advance by one, because the user may have deleted several in a row and the
    scheduler would then stop on one that is not supposed to happen.
    """
    interval = interval_of(task)
    if interval is None:
        return index, grid_moment(task, index)
    count = (task.recurrence or {}).get("count")
    until = parse_iso((task.recurrence or {}).get("until")) \
        if isinstance((task.recurrence or {}).get("until"), str) \
        else (task.recurrence or {}).get("until")
    for step in range(limit):
        candidate = index + step
        if count is not None and candidate >= int(count):
            return candidate, None
        moment = grid_moment(task, candidate)
        if moment is None:
            return candidate, None
        if until is not None and moment > until:
            return candidate, None
        if not is_skipped(task, candidate):
            entry = override_for(task, candidate)
            moved = parse_iso(entry.get("scheduled_at"))
            return candidate, moved or moment
    return index, None


__all__ = ["SERIES_ANCHOR", "OVERRIDES", "THIS_OCCURRENCE", "WHOLE_SERIES", "SCOPES",
           "OVERRIDABLE", "parse_block_id", "interval_of", "anchor_of", "grid_moment",
           "overrides_of", "override_for", "is_skipped", "with_override", "skip",
           "apply_override", "next_unskipped"]
