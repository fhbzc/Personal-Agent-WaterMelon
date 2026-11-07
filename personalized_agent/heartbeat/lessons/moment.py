"""WHAT THE USER WAS DOING WHEN THEY ANSWERED (the user, 2026-11-06: "这个可以在lesson里记录"). A blind
test on their own answers: every "later" was missed, for a later is about the moment, and no lesson
said what the moment was. So a lesson keeps it: what they were at, where, and what it was about, by
name - what a suggestion is anchored to counts for more than when it fires.

Read from the Monitor's signals. A word of the window, not what was in it.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

KEPT = ("mode", "depth", "activity", "application", "title", "project", "task", "hour", "weekday",
        "in_meeting", "presenting", "since_break_minutes", "worked_today_minutes")
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def moment_of(signals: Optional[Dict[str, Any]], recommended_today: Optional[int] = None
              ) -> Dict[str, Any]:
    """What is kept of the signals; {} when nothing was read yet."""
    signals = signals or {}
    said = {key: signals[key] for key in KEPT if signals.get(key) not in (None, "", [], {})}
    if "title" in said:
        said["title"] = str(said["title"])[:120]
    if said and recommended_today is not None:
        said["recommended_today"] = int(recommended_today)
    return said


def line_of(moment: Optional[Dict[str, Any]]) -> str:
    """The moment in words, for a model and for finding it again; "" for none."""
    if not moment:
        return ""
    parts = []
    if moment.get("mode"):
        parts.append(str(moment["mode"]) + (f" ({moment['depth']})" if moment.get("depth") else ""))
    if moment.get("in_meeting"):
        parts.append("in a meeting")
    if moment.get("presenting"):
        parts.append("presenting")
    if moment.get("application"):
        parts.append(f"in {moment['application']}")
    if moment.get("title"):
        parts.append(f'window "{moment["title"]}"')
    if moment.get("project"):
        parts.append(f'on the project "{moment["project"]}"')
    if moment.get("task"):
        parts.append(f'task "{moment["task"]}"')
    if moment.get("hour") is not None:
        hour = float(moment["hour"])
        day = DAYS[int(moment["weekday"]) % 7] + " " if moment.get("weekday") is not None else ""
        parts.append(f"{day}{int(hour):02d}:{int(hour % 1 * 60):02d}")
    if moment.get("since_break_minutes"):
        parts.append(f"{float(moment['since_break_minutes']):.0f} min since a break")
    if moment.get("recommended_today"):
        parts.append(f"{moment['recommended_today']} recommended today")
    return "At the time: " + ", ".join(parts) + "." if parts else ""


__all__ = ["moment_of", "line_of"]
