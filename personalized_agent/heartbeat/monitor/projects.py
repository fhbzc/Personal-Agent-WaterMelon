"""Which project a stretch of time belongs to (section 36.3.2).

The agent already groups what the user did into states, states into tasks and
tasks into projects (sections 34 and 36). The rhythm does not group anything of
its own: a window takes the unit of the state that covers most of it, and the unit
is the project - or, for a concrete task that is in no project, the task. Where the
grouping has not reached a state yet, the window has none and is read by the kind of
work instead.

From PR #6 (the rhythm of the day, branch junyi), taken on 2026-11-03 as
the Monitor's real-time input (monitor/signals.py).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .operations import named_in
from .types import Window

log = logging.getLogger(__name__)

#: A project's name can run to a sentence; the panels get the start of it.
NAME_LENGTH = 70


def _when(text: Any) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


#: Spans are kept by the quarter hours they touch: a window looks only at those
#: near it (every window went through every span: 760,000 comparisons a pass).
SLOT_SECONDS = 900


def _slots_of(start: datetime, end: datetime) -> range:
    return range(int(start.timestamp() // SLOT_SECONDS),
                 int(end.timestamp() // SLOT_SECONDS) + 1)


def _by_slot(spans: List[Tuple[datetime, datetime, str]]) -> Dict[int, List[int]]:
    out: Dict[int, List[int]] = {}
    for n, (a, b, _what) in enumerate(spans):
        for slot in _slots_of(a, b):
            out.setdefault(slot, []).append(n)
    return out


def _near(spans: List[Tuple[datetime, datetime, str]], slots: Dict[int, List[int]],
          start: datetime, end: datetime) -> List[Tuple[datetime, datetime, str]]:
    """The spans that may overlap from `start` to `end`, each once."""
    found: set = set()
    for slot in _slots_of(start, end):
        found.update(slots.get(slot, ()))
    return [spans[n] for n in sorted(found)]


class ProjectIndex:
    """A snapshot of who belongs to which project, for one pass over windows."""

    def __init__(self, db: Any, start: datetime, end: datetime,
                 scope: str = "live") -> None:
        self.names: Dict[str, str] = {}
        #: Which of the classes are projects. The rest are tasks that are in no
        #: project: work the agent knows, which is not any project's.
        self.projects: set = set()
        #: What a window can be placed in: the projects, and the concrete tasks that
        #: are in none. A task is a unit as much as a project is: "the evening
        #: letters" is one piece of work whether or not it was grouped with another.
        self.units: set = set()
        self._spans: List[Tuple[datetime, datetime, str]] = []
        #: The same spans by the task they are in, whatever project that is in.
        self._task_spans: List[Tuple[datetime, datetime, str]] = []
        #: What every state in the range says, whether or not the grouping has
        #: reached it: the user's own project words are looked for in it.
        self._texts: List[Tuple[datetime, datetime, str]] = []
        #: What each state says it rests on, with the unit it is in ("" for none).
        self._named: List[Tuple[datetime, datetime, Any]] = []
        try:
            self._load(db, start, end, scope)
        except Exception:                  # no grouping yet is not an error
            log.debug("the projects could not be read", exc_info=True)
        self._span_slots = _by_slot(self._spans)
        self._task_slots = _by_slot(self._task_spans)
        self._named_slots = _by_slot(self._named)

    def _load(self, db: Any, start: datetime, end: datetime, scope: str) -> None:
        project_of_task: Dict[str, str] = {}
        for row in db.query("SELECT id, name, tasks FROM state_projects WHERE scope = ?",
                            (scope,)):
            self.names[row["id"]] = str(row["name"] or "")[:NAME_LENGTH]
            self.projects.add(row["id"])
            self.units.add(row["id"])
            for task in json.loads(row["tasks"] or "[]"):
                project_of_task[task] = row["id"]
        project_of_state: Dict[str, str] = {}
        task_of_state: Dict[str, str] = {}
        # Put-away rows first, so that a state also held by a row still in use is in
        # that one: the tasks of the scheme before tasks and projects were made per
        # run (section 34.5) are kept as history, and their states are in the
        # projects that scheme writes now.
        for row in db.query("SELECT id, members, described, archived_at FROM state_tasks "
                            "WHERE scope = ? AND retired_at IS NULL "
                            "ORDER BY (archived_at IS NULL OR archived_at = '')", (scope,)):
            # A state is in its project, or - the task being in none - in its task.
            kind = project_of_task.get(row["id"]) or row["id"]
            if kind == row["id"]:
                try:
                    described = json.loads(row["described"] or "{}")
                except ValueError:
                    described = {}
                # A task the agent does not call concrete is not a piece of work, and
                # one the user put away is not one they want predicted.
                if (described.get("concrete") and not row["archived_at"]
                        and not described.get("deleted_by_user")
                        and str(described.get("title") or "").strip()):
                    self.units.add(row["id"])
                    self.names[row["id"]] = str(described["title"]).strip()[:NAME_LENGTH]
            for state in json.loads(row["members"] or "[]"):
                project_of_state[state] = kind
                task_of_state[state] = row["id"]
        # A day either side of the range, compared as text: the stamps are ISO,
        # so a date prefix sorts correctly whatever offset they carry.
        margin = (start - timedelta(days=1)).date().isoformat()
        for row in db.query("SELECT id, title, detail, starts_at, ends_at, metadata FROM states "
                            "WHERE ends_at >= ? ORDER BY starts_at", (margin,)):
            a, b = _when(row["starts_at"]), _when(row["ends_at"])
            if not (a and b and b > start and a < end):
                continue
            self._texts.append((a, b, f"{row['title'] or ''} {row['detail'] or ''}"))
            project = project_of_state.get(row["id"])
            try:
                sources = json.loads(row["metadata"] or "{}").get("sources") or []
            except (TypeError, ValueError, AttributeError):
                sources = []
            names = {item for source in sources for item in named_in(source)}
            if names:
                self._named.append((a, b, (project or "", names)))
            if project:
                self._spans.append((a, b, project))
                self._task_spans.append((a, b, task_of_state[row["id"]]))

    def taught(self, window: Window, kind: str, items: Dict[str, float]) -> Dict[str, float]:
        """WHAT A WINDOW TEACHES OF ITS UNIT (the user, 2026-11-06: "让他判断这个grouping里哪些是不作数的").
        A window has one unit, and what else went on in it is not that unit's: the model says as much
        in the states, each naming what it rests on. What only the states of something else name
        teaches nothing of this one; what none name is as it was."""
        end = window.start + timedelta(minutes=window.minutes)
        named: Dict[str, set] = {}
        for a, b, (unit, names) in _near(self._named, self._named_slots, window.start, end):
            if a < end and b > window.start:
                for item in names:
                    named.setdefault(item, set()).add(unit)
        return {item: seconds for item, seconds in items.items()
                if item not in named or kind in named[item]}

    def text_of(self, window: Window) -> str:
        """What the states that overlap the window say, joined."""
        end = window.start + timedelta(minutes=window.minutes)
        return " ".join(text for a, b, text in self._texts if a < end and b > window.start)

    def task_of(self, window: Window) -> str:
        """The task whose states cover most of the window, in a project or not."""
        end = window.start + timedelta(minutes=window.minutes)
        best: Dict[str, float] = {}
        for a, b, task in _near(self._task_spans, self._task_slots, window.start, end):
            overlap = (min(b, end) - max(a, window.start)).total_seconds()
            if overlap > 0:
                best[task] = best.get(task, 0.0) + overlap
        if not best:
            return ""
        return max(best.items(), key=lambda item: (item[1], item[0]))[0]

    def class_of(self, window: Window) -> str:
        """The project - or, for a task in none, the task - whose states cover
        most of the window; "" when the grouping has not reached it."""
        end = window.start + timedelta(minutes=window.minutes)
        best: Dict[str, float] = {}
        for a, b, kind in _near(self._spans, self._span_slots, window.start, end):
            overlap = (min(b, end) - max(a, window.start)).total_seconds()
            if overlap > 0:
                best[kind] = best.get(kind, 0.0) + overlap
        if not best:
            return ""
        return max(best.items(), key=lambda item: (item[1], item[0]))[0]

    def of(self, window: Window) -> Tuple[str, str]:
        """(unit id, unit name) of the project or task whose states cover most of the
        window; the time decides. A window that was a few minutes on one project and
        the rest on a task in none is that task's - it used to be the project's, which
        put an evening of letters into the project that happened to be open beside them."""
        end = window.start + timedelta(minutes=window.minutes)
        best: Dict[str, float] = {}
        for a, b, kind in _near(self._spans, self._span_slots, window.start, end):
            if kind not in self.units:
                continue
            overlap = (min(b, end) - max(a, window.start)).total_seconds()
            if overlap > 0:
                best[kind] = best.get(kind, 0.0) + overlap
        if not best:
            return "", ""
        winner = max(best.items(), key=lambda item: (item[1], item[0]))[0]
        return winner, self.names.get(winner, "")
