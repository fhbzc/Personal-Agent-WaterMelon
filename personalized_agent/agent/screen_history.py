"""WHAT WAS ON THE SCREEN, LOOKED UP BY TIME (the user, 2026-11-06: "允许chat按照时间搜索，但是不直接给").

The chat is told what is in front of the user now and nothing of before (agent/front_screen.py).
"What am I doing" can need more: the calendar page read two minutes before says who a call is with.
So the full agent may ask for a stretch of time, and nothing of it goes with a message unasked.

Read from what the listener recorded, so what it may not see is not here either, and held to what
holds today: a window on the excluded list since, this application's own, a text showing a password
are left out. With the listener off nothing is given.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..common.events.models import parse_iso
from ..models.base import ToolSpec
from ..runtime.documents import shows_credentials
from ..scheduler.core import zones
from ..scheduler.core.given import time_given
from ..version import names_the_agent

#: With no time given, this much before now.
LAST_MINUTES = 10
#: The windows named, what is read of each text, all that is read, and the rows looked through.
MOST_WINDOWS, MOST_EACH, MOST_TEXT, MOST_ROWS = 25, 1200, 5000, 5000


def _moment(written: Any) -> Tuple[Optional[datetime], str]:
    moment, _note, error = time_given({"at_local": str(written), "zone": "user"})
    return moment, error


def _span(args: Dict[str, Any], now: datetime) -> Tuple[datetime, datetime, str]:
    until, since = now, now - timedelta(minutes=LAST_MINUTES)
    if args.get("until_local"):
        until, error = _moment(args["until_local"])
        if error:
            return now, now, error
        since = until - timedelta(minutes=LAST_MINUTES)
    if args.get("from_local"):
        since, error = _moment(args["from_local"])
        if error:
            return now, now, error
    if since >= until:
        return now, now, "from_local must be before until_local"
    return since, until, ""


def _clock(stamp: str) -> str:
    return zones.wall_of(parse_iso(stamp), zones.here()).strftime("%Y-%m-%d %H:%M:%S")


def _left_out(config: Any, application: str, data: Dict[str, Any]) -> bool:
    title = str(data.get("window_title") or "")
    if str(data.get("pid") or "") == str(os.getpid()) or names_the_agent(application, title):
        return True
    haystack = f"{application} {title}".lower()
    return any(one.strip() and one.strip().lower() in haystack
               for one in config.lines("listener_screen_excluded_applications"))


def _rows(db: Any, config: Any, since: datetime, until: datetime) -> List[Dict[str, Any]]:
    out = []
    for row in db.query(
            "SELECT timestamp, event_type, application, data FROM raw_events WHERE event_type IN "
            "('application.focused', 'screen.text') AND timestamp >= ? AND timestamp <= ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (since.astimezone(timezone.utc).isoformat(), until.astimezone(timezone.utc).isoformat(),
             MOST_ROWS)):
        try:
            data = json.loads(row["data"] or "{}")
        except ValueError:
            continue
        application = str(row["application"] or "")
        if not _left_out(config, application, data):
            out.append({"at": row["timestamp"], "kind": row["event_type"], "application": application,
                        "title": str(data.get("window_title") or ""),
                        "text": str(data.get("text") or "").strip()})
    out.reverse()
    return out


def look(db: Any, config: Any, args: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """The windows of a stretch of time, oldest first, and what was read in them, the latest kept."""
    if not config.get("listener_enabled", True):
        return {"error": "activity is not being recorded: the user turned it off"}
    since, until, error = _span(args, now or datetime.now(timezone.utc))
    if error:
        return {"error": error}
    words = str(args.get("words") or "").strip().lower()
    rows = [one for one in _rows(db, config, since, until)
            if not words or words in f"{one['title']} {one['text']}".lower()]
    windows: List[Dict[str, str]] = []
    for one in rows:
        if one["kind"] == "application.focused" and (not windows or windows[-1]["title"] != one["title"]):
            windows.append({"at": _clock(one["at"]), "application": one["application"],
                            "title": one["title"]})
    readings: List[Dict[str, str]] = []
    room, seen = MOST_TEXT, set()
    for one in reversed(rows):
        if one["kind"] != "screen.text" or not one["text"] or room <= 0:
            continue
        if (one["title"], one["text"]) in seen or shows_credentials(one["text"]):
            continue
        seen.add((one["title"], one["text"]))
        text = one["text"][:min(MOST_EACH, room)]
        room -= len(text)
        readings.insert(0, {"at": _clock(one["at"]), "application": one["application"],
                            "title": one["title"], "text": text})
    said: Dict[str, Any] = {"from": _clock(since.isoformat()), "until": _clock(until.isoformat()),
                            "windows": windows[-MOST_WINDOWS:], "read": readings}
    if len(windows) > MOST_WINDOWS:
        said["earlier_windows_left_out"] = len(windows) - MOST_WINDOWS
    if not rows:
        said["said"] = "nothing recorded for this time"
    return said


def add_tools(registry: Any, db: Any, config: Any) -> None:
    registry.add(ToolSpec(
        "screen_history",
        "What was on the user's screen in a stretch of time, from what this application recorded: "
        "the windows they were in and the text read in them. Nothing of it reaches you unless you "
        "ask. Use it when what is in front of them now does not answer: what they have been doing, "
        "or who a call is with, which a calendar or mail page read shortly before may say. Records "
        f"go back about a week. With no time given, the last {LAST_MINUTES} minutes.",
        {"type": "object", "properties": {
            "from_local": {"type": "string", "description": "on the user's clock: YYYY-MM-DD HH:MM"},
            "until_local": {"type": "string", "description": "on the user's clock: YYYY-MM-DD HH:MM"},
            "words": {"type": "string", "description": "only windows and text holding these words, "
                                                       "in the language they were on the screen"}},
         "required": []}, read_only=True),
        lambda args: look(db, config, args))


__all__ = ["look", "add_tools", "LAST_MINUTES"]
