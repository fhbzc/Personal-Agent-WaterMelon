"""What the user is doing now, as a state (section 36.3.9).

A recommendation is not a guess at the next step. It is a thing to say, and it needs to
be said at a moment when the user is there, is on something that can be recommended
about, and is not in the middle of something that should not be broken. This reads that
off the window being lived in: whether they are working, in the browser or among
messages (a good place to speak), resting, in a meeting or away; what they are on and
for how long; and whether it has only just begun, is going steadily, is scattered, or
has gone on a long time.

Rules over numbers the counting already has; no model is asked.

From PR #6 (the rhythm of the day, branch junyi), taken on 2026-11-03 as
the Monitor's real-time input (monitor/signals.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .types import AWAY, OTHER, Window

WORKING = "working"
SHALLOW = "shallow"        # in the browser or among messages: easy to speak to
RESTING = "resting"
BUSY = "busy"              # in a meeting
GONE = "away"

#: Labels of the work that takes the whole of the attention.
WORK_LABELS = ("coding", "reading", "writing")
#: Labels of work that is already shallow, scattered or on the way to something else.
SHALLOW_LABELS = ("browsing", "communication", OTHER)
from .types import REST_LABELS  # noqa: E402 - with the labels, used by the windows too
BUSY_LABELS = ("meeting",)
#: More than this much of a window idle is a pause, though not yet being away.
PAUSED_IDLE_SHARE = 0.5

ARRIVING = "arriving"
FOCUSED = "focused"
SCATTERED = "scattered"
WINDING_DOWN = "winding_down"

#: Focus changes an hour past which the work is scattered - for someone whose own
#: work is not already that busy: on a real week the middle window was 56 an hour and
#: seven in ten were past 12, so the line is the user's own (``scattered_at``).
SCATTERED_SWITCHES_PER_HOUR = 12
#: The share of the user's own working windows that are not scattered.
SCATTERED_PERCENTILE = 0.75
#: Fewer working windows than this say nothing of what is usual for them.
LEAST_WINDOWS_FOR_THEIR_OWN_LINE = 40
#: Just begun: this long on the unit in a row, or this much of it today.
ARRIVING_ON_UNIT_MIN = 15
ARRIVING_TODAY_MIN = 20
#: Gone on this long without a break.
WINDING_DOWN_MIN = 90
#: The computer was "just opened" for this long.
JUST_OPENED_MIN = 20


@dataclass
class UserState:
    mode: str
    depth: str
    unit: str                       # the project or task, "" when there is none
    unit_name: str
    label: str                      # what kind of work the window is
    on_unit_min: float              # in a row, on that unit
    today_min: float                # on that unit since midnight
    since_break_min: float
    switch_rate: float
    session_min: float = 0.0        # since the computer was opened (or came back after hours)
    reasons: List[str] = field(default_factory=list)

    @property
    def just_opened(self) -> bool:
        """The computer was opened only a little while ago."""
        return self.mode != GONE and self.session_min <= JUST_OPENED_MIN

    @property
    def can_be_spoken_to(self) -> bool:
        return self.mode in (WORKING, SHALLOW)

    def says(self) -> str:
        if self.mode == GONE:
            return "away from the computer"
        what = f"{self.unit_name or self.label}"
        if self.mode == RESTING:
            return f"resting ({what})"
        if self.mode == BUSY:
            return f"in a meeting ({what})"
        how = {"working": "working on", "shallow": "browsing or in messages, on"}[self.mode]
        said = (f"{how} {what}, {self.on_unit_min:.0f} min in a row "
                f"({self.depth.replace('_', ' ')})")
        if self.just_opened:
            return f"just opened the computer ({self.session_min:.0f} min ago); {said}"
        return f"{said}; {self.session_min / 60:.1f} h since the computer was opened"

    def to_dict(self) -> Dict[str, Any]:
        return {"mode": self.mode, "depth": self.depth, "unit": self.unit,
                "unit_name": self.unit_name, "label": self.label,
                "on_unit_min": round(self.on_unit_min, 1),
                "today_min": round(self.today_min, 1),
                "since_break_min": round(self.since_break_min, 1),
                "switch_rate": round(self.switch_rate, 1),
                "session_min": round(self.session_min, 1),
                "just_opened": self.just_opened, "reasons": self.reasons,
                "says": self.says()}


def scattered_line(windows: Sequence[Window]) -> float:
    """How many focus changes an hour is scattered for this user: the top quarter of
    their own working windows, and never less than the default."""
    rates = sorted(w.switch_rate for w in windows if w.main != AWAY)
    if len(rates) < LEAST_WINDOWS_FOR_THEIR_OWN_LINE:
        return SCATTERED_SWITCHES_PER_HOUR
    return max(float(SCATTERED_SWITCHES_PER_HOUR),
               rates[int(SCATTERED_PERCENTILE * (len(rates) - 1))])


def infer_state(now: Window, scattered_at: float = SCATTERED_SWITCHES_PER_HOUR
                ) -> UserState:
    """The state the window being lived in says the user is in."""
    reasons: List[str] = []
    if now.main == AWAY:
        mode = GONE
        reasons.append("no input in most of the window")
    elif now.main in BUSY_LABELS:
        mode = BUSY
        reasons.append("a meeting is in front")
    elif now.main in REST_LABELS or now.idle_share >= PAUSED_IDLE_SHARE:
        mode = RESTING
        reasons.append("entertainment is in front" if now.main in REST_LABELS
                       else f"{now.idle_share:.0%} of the window idle")
    elif now.main in WORK_LABELS:
        mode = WORKING
        reasons.append(f"{now.main} is in front")
    else:
        mode = SHALLOW
        reasons.append(f"{now.main} is in front")

    unit = now.project_id or ""
    on_unit = now.on_project_min if unit else 0.0
    today = now.project_today_min if unit else 0.0
    if now.switch_rate >= scattered_at:
        depth = SCATTERED
        reasons.append(f"{now.switch_rate:.0f} focus changes an hour")
    elif unit and (on_unit <= ARRIVING_ON_UNIT_MIN or today <= ARRIVING_TODAY_MIN):
        depth = ARRIVING
        reasons.append("only just begun on it")
    elif now.since_break_min >= WINDING_DOWN_MIN:
        depth = WINDING_DOWN
        reasons.append(f"{now.since_break_min:.0f} min without a break")
    else:
        depth = FOCUSED
    return UserState(mode=mode, depth=depth, unit=unit, unit_name=now.project or "",
                     label=now.main, on_unit_min=on_unit, today_min=today,
                     since_break_min=now.since_break_min, switch_rate=now.switch_rate,
                     session_min=now.session_elapsed_min, reasons=reasons)


__all__ = ["UserState", "infer_state", "scattered_line", "WORKING", "SHALLOW", "RESTING", "BUSY", "GONE",
           "ARRIVING", "FOCUSED", "SCATTERED", "WINDING_DOWN"]
