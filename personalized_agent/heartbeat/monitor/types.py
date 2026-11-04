"""One window of the user's time.

From PR #6 (the rhythm of the day, branch junyi), taken on 2026-11-03 as
the Monitor's real-time input (monitor/signals.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

#: The coarse labels a window's main activity is one of (section 36.3.2).
AWAY = "away"
OTHER = "other"
LABELS = ("coding", "reading", "writing", "meeting", "communication",
          "browsing", "entertainment", AWAY, OTHER)


def time_bucket(moment: datetime) -> int:
    """Half-hour of the local day, 0-47."""
    return moment.hour * 2 + (1 if moment.minute >= 30 else 0)


def day_type(weekday: int) -> str:
    return "weekend" if weekday >= 5 else "workday"


@dataclass
class Window:
    """What the user did over one window, as numbers and two labels: the kind
    of work, and - when the agent has grouped the work into one - the project."""

    start: datetime                        # local, timezone-aware
    minutes: int = 15
    main: str = OTHER                      # the label that held most of the window
    activities: Dict[str, float] = field(default_factory=dict)   # label -> share
    task: str = ""                         # what a covering state calls it
    session_elapsed_min: float = 0.0       # since the computer was opened
    since_break_min: float = 0.0           # at the screen since the last short absence
    own_work_min: float = 0.0              # of the user's own work, within that
    on_task_min: float = 0.0               # consecutive minutes with the same main
    switch_rate: float = 0.0               # focus changes per hour
    idle_share: float = 0.0
    references: List[str] = field(default_factory=list)
    state_id: Optional[str] = None
    project_id: str = ""                   # the project the work was in, if known
    project: str = ""                      # its name when this was written
    on_project_min: float = 0.0            # consecutive minutes on the same unit
    project_today_min: float = 0.0         # minutes on the same unit since midnight

    @property
    def weekday(self) -> int:
        return self.start.weekday()

    @property
    def bucket(self) -> int:
        return time_bucket(self.start)

    @property
    def day_type(self) -> str:
        return day_type(self.weekday)

    @property
    def unit(self) -> str:
        """What the window was about: the project when the work is in one,
        otherwise the kind of work. Being away is always away."""
        if self.main == AWAY:
            return AWAY
        return self.project_id or self.main

    @property
    def unit_name(self) -> str:
        if self.main == AWAY:
            return AWAY
        return self.project or self.main

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start": self.start.isoformat(), "minutes": self.minutes,
            "main": self.main, "activities": self.activities, "task": self.task,
            "project": self.project, "project_id": self.project_id,
            "weekday": self.weekday, "bucket": self.bucket,
            "day_type": self.day_type,
            "session_elapsed_min": round(self.session_elapsed_min, 1),
            "since_break_min": round(self.since_break_min, 1),
            "own_work_min": round(self.own_work_min, 1),
            "on_task_min": round(self.on_task_min, 1),
            "on_project_min": round(self.on_project_min, 1),
            "project_today_min": round(self.project_today_min, 1),
            "switch_rate": round(self.switch_rate, 2),
            "idle_share": round(self.idle_share, 2),
            "references": self.references, "state_id": self.state_id,
        }


#: Labels of time the user is resting, not working.
REST_LABELS = ("entertainment",)
