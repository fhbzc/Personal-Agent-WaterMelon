"""WHEN AN ISSUE OF CUSTOMIZED RECOMMENDATION IS MADE: at the times of day the
user chose (`recommend_times`, "08:00" by default - several may be given), in
this computer's time zone. Its own clock, apart from the heartbeats.

Looked at once a minute. An issue is due when the latest of those times has
passed and no issue was made since. Times missed while the computer was off
make ONE issue when it is back, covering what happened since the last
(recommend.since_of) - not one for each. A time whose issue failed is not
tried again every minute: the next time is waited for, or the user's button.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta
from typing import Any, List, Optional

log = logging.getLogger(__name__)

TIMES = "recommend_times"
DEFAULT_TIMES = "08:00"
#: How often the clock looks.
LOOKS_EVERY = 60


def parse_times(text: Any) -> List[str]:
    """The times of day in a setting: "08:00, 18:30" - each HH:MM, in order,
    once; what is not a time left out."""
    out = set()
    for hours, minutes in re.findall(r"(\d{1,2})[:.h](\d{2})", str(text or "")):
        if int(hours) < 24 and int(minutes) < 60:
            out.add(f"{int(hours):02d}:{int(minutes):02d}")
    return sorted(out)


def latest_time(times: List[str], now: datetime) -> Optional[datetime]:
    """The latest of the times that has passed, today or yesterday - in the
    local time zone of `now`."""
    if not times:
        return None
    passed = []
    for day in (0, 1):
        base = (now - timedelta(days=day)).replace(second=0, microsecond=0)
        for one in times:
            hours, minutes = (int(part) for part in one.split(":"))
            at = base.replace(hour=hours, minute=minutes)
            if at <= now:
                passed.append(at)
    return max(passed) if passed else None


def next_time(times: List[str], now: datetime) -> Optional[datetime]:
    upcoming = []
    for day in (0, 1):
        base = (now + timedelta(days=day)).replace(second=0, microsecond=0)
        for one in times:
            hours, minutes = (int(part) for part in one.split(":"))
            at = base.replace(hour=hours, minute=minutes)
            if at > now:
                upcoming.append(at)
    return min(upcoming) if upcoming else None


class IssueClock:
    """Makes an issue when one is due (module docstring)."""

    def __init__(self, config: Any, round_: Any, store: Any) -> None:
        self.config = config
        self.round = round_
        self.store = store
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        #: The time whose issue was started - not started twice.
        self._started_for: Optional[datetime] = None

    def times(self) -> List[str]:
        """The times of day - none at all when the user took every one off:
        then no issue is made by itself, only with the button."""
        return parse_times(self.config.get(TIMES, DEFAULT_TIMES))

    def due(self, now: Optional[datetime] = None) -> bool:
        from ...common.modules import enabled as module_on
        if not self.config.get("connections_enabled", True) or \
                not module_on(self.config, "recommend"):
            return False
        now = now or datetime.now().astimezone()
        latest = latest_time(self.times(), now)
        if latest is None or latest == self._started_for:
            return False
        last = (self.store.briefing() or {}).get("at")
        try:
            made = datetime.fromisoformat(str(last).replace("Z", "+00:00")) if last else None
        except ValueError:
            made = None
        return made is None or made < latest

    def look(self, now: Optional[datetime] = None) -> bool:
        """One look: an issue started when one is due."""
        now = now or datetime.now().astimezone()
        if self.round.running() or not self.due(now):
            return False
        self._started_for = latest_time(self.times(), now)
        return bool(self.round.start(going=lambda: not self._stop.is_set()))

    def status(self) -> dict:
        now = datetime.now().astimezone()
        upcoming = next_time(self.times(), now)
        return {"times": self.times(), "time_zone": now.strftime("%Z (UTC%z)"),
                "next": upcoming.isoformat() if upcoming else ""}

    # -- the loop ------------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="issue-clock", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(5)

    def _loop(self) -> None:
        # A minute after start-up first: not while the window appears.
        while not self._stop.wait(LOOKS_EVERY):
            try:
                self.look()
            except Exception:                      # pragma: no cover - looked at again
                log.warning("the issue clock could not look", exc_info=True)


__all__ = ["IssueClock", "parse_times", "latest_time", "next_time", "TIMES", "DEFAULT_TIMES"]
