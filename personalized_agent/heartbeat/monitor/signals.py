"""THE REAL-TIME INPUT (the user, 2026-11-03: "我的listener和其他我说的（比如用户
当前busy status, 什么时候开始工作的等等信息），是一个real-time的input machine。
这个东西作为一个接口提供给模型").

Code alone, no model: what the listener records, turned every few seconds into
named SIGNALS - the time, whether the user is there, how busy, since when they
work today, how long this stretch and since the last break, the project of the
window in front, and the HABITS of the last weeks - and into EVENTS, the
moments something changed (the day began, back after an absence, a new piece
of work, a rest).

The Activators (activators.py) are written against these names, and the model
is shown the list (`describe`). Nothing here reads what is on the screen but
the titles, folders and sites the window placement needs; the windows kept
(`activity_windows`, store.py) hold what a window WAS, never its words.

The windows and their placement are PR #6's (types, windows, store, state,
projects, operations); the absence written over a time nobody was watching
and the window under way are its service's, taken here. The last way to
place a window is by its words, not by a model of meanings - the events are
in every language.
"""

from __future__ import annotations

import json
import logging
import math
import re
import statistics
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import screen_now
from . import state as state_module
from .operations import ProjectMatcher, session_folders, window_items
from .projects import ProjectIndex
from .store import WindowStore
from .types import AWAY, Window, day_type, time_bucket
from .windows import (Carry, _when, assign_units, build_windows, floor_to, focus_intervals,
                      label_for, parse_rules)

log = logging.getLogger(__name__)

#: The windows are this long; the window under way is read once it has this
#: many minutes in it.
WINDOW_MINUTES = 15
LIVE_LEAST_MIN = 2
#: Never reach back further than what the raw record holds.
BACKFILL_DAYS = 8
#: The events read for the window being lived in.
EVENT_PREFIXES = ("application.", "presence.", "filesystem.", "browser.", "assistant.",
                  "screen.text")
GAP_PREFIXES = ("application.", "presence.", "screen.", "filesystem.", "browser.")
#: A look that finds the last one longer ago than this, with nothing recorded
#: between, writes the gap as an absence (the agent closed, the computer off).
GAP_LEAST_S = 180
ALIVE_KEY = "monitor.alive_at"
#: Nobody at the keyboard or mouse for this long is away (listener_presence_seconds).
AWAY_AFTER_S = 300
#: Away this long and back is the day beginning, when little was worked today.
DAY_GAP_MIN = 240
DAY_BEGAN_LEAST_WORK_MIN = 30
#: Back from an absence: said for this long after it.
BACK_FOR_MIN = 10
#: Placement by paths and by meaning, held this long; meaning asked at most so often.
PATH_MAP_SECONDS = 600
#: By words: the score the best piece of work must reach, and how many times
#: the next's. Measured on a real week against the grouping's answers
#: (2026-11-04): 12% of windows answered, 79% rightly; half this lead
#: answered twice as many, 65% rightly - and a wrong project is worse than
#: none, the grouping coming later.
WORDS_AT, WORDS_LEAD = 0.15, 2.0
#: The most of a window's screen text read for its words.
MOST_SCREEN = 20000
#: Words too common in titles to say which work it is.
COMMON_WORDS = {"the", "and", "for", "with", "from", "new", "tab", "page", "home", "untitled",
                "google", "chrome", "edge", "firefox", "microsoft", "search", "inbox", "mail",
                "http", "https", "www", "com", "org", "html", "file", "files", "folder",
                "window", "windows", "document", "documents", "project", "work", "task"}
PATH_NAME_LEAST = 6
#: WHILE A VIDEO HAS THE FOCUS THIS LONG, the paths say nothing of what the user is at (the user,
#: 2026-11-06, "看哪个窗口在上"): a coding assistant working in a folder is not the user working
#: there. A glance at one, shorter than this, leaves the project as it was.
WATCHING_SECONDS = 30.0
#: How far back the focus is read for that: titles change as videos follow one another.
WATCHING_LOOKS_BACK = 20
_NAME_SPLIT = re.compile(r"\s+[-–—|]\s+|[\\/]")
#: The windows are placed again at most so often (the grouping moves slowly).
RELABEL_EVERY_S = 300
#: HABITS are read over this many weeks, and asked again at most so often.
HABIT_DAYS = 28
HABIT_EVERY_S = 600
#: How far back what is scattered for this user is read from.
SCATTERED_DAYS = 14
HABIT_LEAST_DAYS = 3

#: The programs running are read at most so often.
RUNNING_EVERY_S = 30
#: A browser's addresses this recent are read for a meeting it may be in.
MEETING_PAGE_HOURS = 3

#: THE SIGNALS, as the model is told of them: name -> (type, what it is).
SIGNALS: Dict[str, Tuple[str, str]] = {
    "hour": ("number", "the local time as hours, 14.5 = 14:30"),
    "weekday": ("number", "0 Monday ... 6 Sunday"),
    "is_weekend": ("bool", "Saturday or Sunday"),
    "date": ("text", "today, YYYY-MM-DD"),
    "present": ("bool", "someone used the keyboard or mouse in the last minutes"),
    "idle_seconds": ("number", "seconds since the keyboard or mouse was last used"),
    "away_minutes": ("number", "how long the user has been away now; 0 when present"),
    "back_after_minutes": ("number", "the absence that ended in the last 10 minutes; 0 if none"),
    "mode": ("text", "working | shallow (browser, messages) | resting | busy (meeting) | away"),
    "depth": ("text", "arriving | focused | scattered | winding_down"),
    "activity": ("text", "the kind of thing in front: coding, reading, writing, meeting, "
                         "communication, browsing, entertainment, other, away"),
    "switch_rate": ("number", "focus changes an hour in the window under way"),
    "application": ("text", "the program in front"),
    "title": ("text", "the title of the window in front"),
    "screen_locked": ("bool", "the lock screen, sign-in screen or screensaver is up"),
    "running": ("text", "the programs running now, by name, separated by commas - use contains"),
    "full_screen": ("bool", "something fills the whole screen: slides, a film, a game "
                            "(Windows only so far)"),
    "presenting": ("bool", "slides are being shown"),
    "in_meeting": ("bool", "in a meeting or a call: its window, a call program using the "
                           "microphone, or a browser using it on a meeting's own address"),
    "microphone_in_use": ("text", "the programs using the microphone now, separated by commas "
                                  "(Windows only so far)"),
    "work_started_today": ("text", "when the first work today was, HH:MM; empty before"),
    "work_started_hour": ("number", "the same, as hours; -1 before any work today"),
    "stretch_minutes": ("number", "minutes worked since the last absence of 30 min or more"),
    "since_break_minutes": ("number", "minutes at the screen since the last break, work or not"),
    "work_minutes": ("number", "minutes of the user's own work since the last break: not resting, "
                               "and not their agent working while their hands are off"),
    "worked_today_minutes": ("number", "minutes worked since midnight"),
    "project_id": ("text", "the project (or task in no project) of the window in front"),
    "project": ("text", "its name"),
    "project_how": ("text", "how that was told: grouping (the heartbeat placed it), history "
                            "(like windows placed before), paths (its folders and files), "
                            "words (the words of its titles and screen - about one in five "
                            "wrong); empty when no project is known"),
    "task_id": ("text", "the task within it the window most likely belongs to"),
    "task": ("text", "that task's title"),
    "project_minutes": ("number", "minutes on that project in a row"),
    "project_today_minutes": ("number", "minutes on it today"),
    "project_days_since": ("number", "days since it was last worked on before today; "
                                     "-1 never before"),
    "habit_days": ("number", "days of work the habits below are read from (last 4 weeks)"),
    "usual_start_hour": ("number", "when work usually begins on a day like today; -1 unknown"),
    "usual_stretch_minutes": ("number", "how long the user usually works before a break; "
                                        "-1 unknown"),
    "usual_worked_by_now_minutes": ("number", "minutes usually worked by this time on a "
                                              "day like today; -1 unknown"),
    "worked_vs_usual_minutes": ("number", "worked today minus the usual by now"),
    "usual_activity_now": ("text", "what the user usually does at this half hour on a day "
                                   "like today; empty unknown"),
}

#: WINDOWS BEING CLOSED (PR #6's): within this many seconds, at least this many fewer than the
#: most there were, and at least this share of them.
CLOSING_WITHIN_SECONDS, CLOSING_LEAST_FEWER, CLOSING_SHARE = 180, 4, 0.4

#: THE EVENTS, each said on the look it happened: name -> what it is.
EVENTS: Dict[str, str] = {
    "day_began": "the first work of the day, after hours away (the computer just opened)",
    "back": "back at the computer after an absence (back_after_minutes says how long)",
    "project_changed": "the window in front is now another project",
    "new_work": "a piece of work never worked on before today has begun",
    "leisure": "turned to something to watch or play, after working",
    "text": "new words appeared in a window (for keyword conditions)",
    "closing_up": "many windows closed in a few minutes: tidying up, perhaps to stop for the day",
}


def words_of(text: str) -> set:
    """A text's words in any language: Latin words of three letters or more,
    and pairs of Chinese, Japanese or Korean characters."""
    text = str(text or "").lower()
    found = {word for word in re.findall(r"[a-z0-9\u00c0-\u024f\u0400-\u04ff]{3,}", text)
             if word not in COMMON_WORDS and not word.isdigit()}
    for run in re.findall(r"[\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]+", text):
        found.update(run[i:i + 2] for i in range(max(1, len(run) - 1)))
    return found


def screen_words(events: List[Dict[str, Any]]) -> set:
    """The words on the screen in these events."""
    text = "\n".join(str((event.get("data") or {}).get("text") or "") for event in events
                     if event.get("event_type") == "screen.text")
    return words_of(text[-MOST_SCREEN:])


def _texts_of(said: Any) -> str:
    """Every text in a task's or project's description - both languages."""
    out: List[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, dict):
            for key, one in value.items():
                if key not in ("minutes", "named_minutes", "urgency", "importance", "kind"):
                    walk(one)
        elif isinstance(value, list):
            for one in value[:40]:
                walk(one)
    walk(said)
    return " ".join(out)[:6000]


def _aware(text: Any) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _median(values: List[float]) -> float:
    return float(statistics.median(values)) if values else -1.0


class Signals:
    """The real-time input: windows kept, the window under way, the signals
    and the events of each look."""

    def __init__(self, db: Any, config: Any, states: Any = None,
                 idle: Optional[Callable[[], Optional[float]]] = None) -> None:
        self.db = db
        self.config = config
        self.states = states
        self.idle = idle or (lambda: None)
        from ...platform_layer import get_platform
        self.platform = getattr(get_platform(), "activity", None)
        self.programs = screen_now.running_programs
        self._running: List[str] = []
        self._running_due = 0.0
        self.store = WindowStore(db)
        self._lock = threading.RLock()
        self._previous: Dict[str, Any] = {}
        self._away_since: Optional[datetime] = None
        self._back: Optional[Tuple[datetime, float]] = None
        self._labelling: Optional[Dict[str, Any]] = None
        self._relabelled_at = 0.0
        self._habits: Tuple[float, str, Dict[str, Any]] = (0.0, "", {})
        self._scattered: Tuple[float, Optional[float]] = (0.0, None)
        self._born = datetime.now(timezone.utc)
        self._window_counts: List[Tuple[float, int]] = []
        self._watched_at: Optional[datetime] = None
        self.last_error = ""

    # -- the record ---------------------------------------------------------------------
    def _events(self, start: datetime, end: datetime,
                prefixes: Tuple[str, ...] = EVENT_PREFIXES) -> List[Dict[str, Any]]:
        """The events of the stretch, with the focus still open at its start and
        the last word on presence before it."""
        like = " OR ".join("event_type LIKE ?" for _ in prefixes)
        low = start.astimezone(timezone.utc).isoformat()
        high = end.astimezone(timezone.utc).isoformat()
        rows = self.db.query(
            f"SELECT event_type, timestamp, application, data FROM raw_events "
            f"WHERE ({like}) AND timestamp >= ? AND timestamp < ? ORDER BY timestamp",
            (*[p + "%" for p in prefixes], low, high))
        # EACH PAIR ON ITS OWN. Presence was one kind here: hands_off is stamped later than the
        # away it comes with, so it was the last word, the absence under way was not seen,
        # and days away read as days at the screen (the user, 2026-11-05: 11530 minutes).
        for kinds, opened in ((("application.focus%",), "application.focused"),
                              (("presence.away", "presence.back"), "presence.away"),
                              (("presence.hands_off", "presence.hands_on"), "presence.hands_off")):
            which = " OR ".join("event_type LIKE ?" for _ in kinds)
            before = self.db.query_one(
                "SELECT event_type, timestamp, application, data FROM raw_events "
                f"WHERE ({which}) AND timestamp < ? ORDER BY timestamp DESC LIMIT 1",
                (*kinds, low))
            if before and before["event_type"] == opened:
                rows.insert(0, before)
        out = []
        for row in rows:
            row = dict(row)
            try:
                row["data"] = json.loads(row["data"] or "{}")
            except ValueError:
                row["data"] = {}
            out.append(row)
        return out

    def _watched_from(self, until: datetime) -> datetime:
        """Since when the user's absences have been recorded: before it no break could be
        seen, so no stretch can be told. `until` where they never have been. The monitor's
        own gaps do not count: they say the agent was not looking, not that it can tell."""
        if self._watched_at is None:
            row = self.db.query_one(
                "SELECT MIN(timestamp) AS t FROM raw_events WHERE event_type IN "
                "('presence.away', 'presence.back') AND source != 'monitor'")
            self._watched_at = _aware(row["t"]) if row and row["t"] else None
        return self._watched_at or until

    def _kv(self, key: str) -> str:
        row = self.db.query_one("SELECT value FROM kv WHERE key = ?", (key,))
        return str(row["value"]) if row and row["value"] is not None else ""

    def _set_kv(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO "
                        "UPDATE SET value = excluded.value", (key, value))

    def _recorded(self, sql_order: str, op: str, moment: datetime,
                  until: Optional[datetime] = None) -> Optional[datetime]:
        like = " OR ".join("event_type LIKE ?" for _ in GAP_PREFIXES)
        params: List[Any] = [p + "%" for p in GAP_PREFIXES]
        sql = (f"SELECT {sql_order}(timestamp) AS t FROM raw_events WHERE ({like}) "
               f"AND timestamp {op} ? AND source != 'monitor'")
        params.append(moment.astimezone(timezone.utc).isoformat())
        if until is not None:
            sql += " AND timestamp < ?"
            params.append(until.astimezone(timezone.utc).isoformat())
        row = self.db.query_one(sql, tuple(params))
        return _aware(row["t"]) if row and row["t"] else None

    def _write_absence(self, start: datetime, end: datetime, why: str) -> None:
        from ...common.events.models import ListenerEvent
        from ...data_manage.storage.raw_store import RawStore
        data = {"idle_seconds": round((end - start).total_seconds(), 1), "why": why}
        RawStore(self.db).add_events([
            ListenerEvent(event_type="presence.away", source="monitor", timestamp=start,
                          data=data),
            ListenerEvent(event_type="presence.back", source="monitor", timestamp=end,
                          data=data)])

    def mark_gap(self, now: datetime) -> Optional[Tuple[datetime, datetime]]:
        """NOT WATCHING IS NOT WORKING (PR #6): the time since the last look,
        with nothing recorded, written as an absence - the agent closed, the
        computer off or asleep - so that a window left in front overnight is
        not a night of work."""
        when = now.astimezone(timezone.utc)
        last = _aware(self._kv(ALIVE_KEY)) if self._kv(ALIVE_KEY) else None
        born, self._born = self._born, None
        if born is not None:
            before = self._recorded("MAX", "<", born - timedelta(seconds=5))
            if before is not None and (last is None or before < last):
                last = before
        written = None
        if last is not None and (when - last).total_seconds() > GAP_LEAST_S:
            again = self._recorded("MIN", ">", last + timedelta(seconds=5), when) or when
            if (again - last).total_seconds() > GAP_LEAST_S:
                self._write_absence(last, again, "nobody was watching: the agent was not "
                                                 "running or the computer was off or asleep")
                written = (last, again)
                self.store.delete_between(floor_to(last.astimezone(), WINDOW_MINUTES),
                                          when.astimezone() + timedelta(days=1))
        self._set_kv(ALIVE_KEY, when.isoformat())
        return written

    def record_if_due(self, now: Optional[datetime] = None) -> int:
        """The windows ended since the last one, written; their projects given."""
        with self._lock:
            when = now or datetime.now(timezone.utc)
            try:
                self.mark_gap(when)
                added = self._record(when)
                day = when.astimezone().date().isoformat()
                if self._kv("monitor.pruned") != day:
                    # The record of the user's time kept as long as they said.
                    self.store.prune(int(self.config.get("monitor_windows_keep_days", 180)
                                         if self.config else 180))
                    self._set_kv("monitor.pruned", day)
                if added or time.monotonic() - self._relabelled_at > RELABEL_EVERY_S:
                    self.relabel()
                return added
            except Exception as exc:
                self.last_error = str(exc)[:200]
                log.warning("the windows could not be recorded", exc_info=True)
                return 0

    def _record(self, now: datetime) -> int:
        local = now.astimezone()
        boundary = floor_to(local, WINDOW_MINUTES)
        tail = self.store.last(12)
        if tail:
            start = tail[-1].start + timedelta(minutes=WINDOW_MINUTES)
        else:
            first = self.db.query_one("SELECT MIN(timestamp) AS t FROM raw_events "
                                      "WHERE event_type LIKE 'application.%'")
            if not first or not first["t"]:
                return 0
            start = floor_to(_aware(first["t"]).astimezone(), WINDOW_MINUTES)
        start = max(start, boundary - timedelta(days=BACKFILL_DAYS))
        if start >= boundary:
            return 0
        watched = self._watched_from(boundary)
        windows = build_windows(start, boundary, WINDOW_MINUTES, self._events(start, boundary),
                                Carry.after(tail).within(watched, start), self._rules(),
                                self._state_lookup(), busy=self._agent_at_work(start),
                                watched_from=watched)
        return self.store.add(windows)

    def _agent_at_work(self, since: datetime) -> List[Tuple[datetime, datetime]]:
        """When the agent's models were at work for the user: its chat and its
        runs. The background rounds are the agent's own business."""
        try:
            rows = self.db.query(
                "SELECT created_at, latency_ms FROM model_calls WHERE created_at >= ? "
                "AND task NOT LIKE 'heartbeat%' AND task NOT LIKE 'monitor%'",
                ((since - timedelta(hours=1)).astimezone(timezone.utc).isoformat(),))
        except Exception:
            log.debug("the model calls could not be read", exc_info=True)
            return []
        out = []
        for row in rows:
            ended = _when(row["created_at"])
            if ended is not None:
                out.append((ended - timedelta(milliseconds=float(row["latency_ms"] or 0)), ended))
        return out

    def _rules(self) -> Dict[str, Tuple[str, ...]]:
        return parse_rules(self.config.get("monitor_label_rules", "") if self.config else "")

    def _state_lookup(self) -> Optional[Callable[[datetime], Tuple[Optional[str], str]]]:
        if self.states is None:
            return None

        def lookup(moment: datetime) -> Tuple[Optional[str], str]:
            try:
                found = self.states.covering(moment, limit=1)
            except Exception:
                return None, ""
            return (found[0].id, found[0].title) if found else (None, "")
        return lookup

    def relabel(self, days: int = BACKFILL_DAYS) -> int:
        """Each window of the last days given its project: the agent's grouping
        first, then what the windows it placed teach (PR #6)."""
        self._relabelled_at = time.monotonic()
        midnight = (datetime.now(timezone.utc).astimezone() - timedelta(days=days)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        windows = self.store.since(midnight)
        if not windows:
            return 0
        stored = {w.start: (w.project_id, w.project, w.on_project_min, w.project_today_min)
                  for w in windows}
        end = windows[-1].start + timedelta(minutes=windows[-1].minutes)
        index = ProjectIndex(self.db, windows[0].start, end)
        work = [w for w in windows if w.main != AWAY]
        items, started_in = self._items_of(work, end)
        assign_units(windows, index.of)
        by_grouping = {w.start: (w.project_id, w.project) for w in windows if w.project_id}
        classes = {w.start: index.class_of(w) for w in work}
        matcher = None
        out: Dict[datetime, Tuple[str, str]] = {}
        told: Dict[datetime, str] = {}
        if work and any(classes.values()):
            matcher = ProjectMatcher()
            matcher.learn((classes[w.start], index.taught(w, classes[w.start], items[w.start]),
                           w.start) for w in work if classes[w.start])
            for w in work:
                if classes[w.start]:
                    continue
                found = matcher.match(items[w.start], w.start)
                if found:
                    matcher.anchor(found[0], items[w.start], w.start)
                    if found[0] in index.units:
                        out[w.start] = (found[0], index.names.get(found[0], ""))
                        told[w.start] = "history"
        # A window the grouping has not reached goes by its paths, then its
        # words (the user, 2026-11-03); the grouping corrects it when it comes.
        words = self.__dict__.get("_seen_words", {})
        for w in work:
            if classes.get(w.start) or w.start in out:
                continue
            for said, how in (("paths", lambda: self._place_by_paths(items[w.start])),
                              ("words", lambda: self._place_by_words(items[w.start],
                                                                     words.get(w.start)))):
                try:
                    unit, name = how()
                except Exception:
                    log.debug("the window could not be placed", exc_info=True)
                    unit, name = "", ""
                if unit:
                    out[w.start] = (unit, name)
                    told[w.start] = said
                    break
        assign_units(windows, lambda w: by_grouping.get(w.start) or out.get(w.start, ("", "")))
        self._told = {w.start: "grouping" if w.start in by_grouping else told.get(w.start, "")
                      for w in work}
        self._labelling = {"index": index, "started_in": started_in, "matcher": matcher}
        changed = sum(1 for w in windows if (w.project_id, w.project, w.on_project_min,
                                             w.project_today_min) != stored[w.start])
        if changed:
            self.store.add(windows)
        return changed

    def _items_of(self, work: List[Window], end: datetime) -> Tuple[Dict[datetime, Any], Dict[str, set]]:
        """WHAT WAS DONE IN EACH WINDOW - read once (the user, 2026-11-04: the
        agent stuttered; every window went through eight days of events, every
        five minutes: 75 s on one real week, in the process that draws the
        avatar). A window that has ended does not change: each is read once,
        from its own events, and only the new ones' events are read."""
        kept: Dict[datetime, Any] = self.__dict__.setdefault("_items_kept", {})
        started_in: Dict[str, set] = self.__dict__.setdefault("_started_in", {})
        seen: Dict[datetime, set] = self.__dict__.setdefault("_seen_words", {})
        wanted = [w for w in work if w.start not in kept]
        if wanted:
            first = min(w.start for w in wanted)
            events = self._events(first, end)
            for title, folders in session_folders(events).items():
                started_in.setdefault(title, set()).update(folders)
            intervals = focus_intervals(events, None, until=end)
            step = timedelta(minutes=WINDOW_MINUTES)
            by_window: Dict[datetime, List[Dict[str, Any]]] = {}
            for event in events:
                when = _aware(event["timestamp"])
                if when is not None:
                    by_window.setdefault(floor_to(when.astimezone(), WINDOW_MINUTES), []).append(event)
            in_window: Dict[datetime, List[Any]] = {}
            for interval in intervals:
                here = floor_to(interval.start.astimezone(), WINDOW_MINUTES)
                while here < interval.end:
                    in_window.setdefault(here, []).append(interval)
                    here += step
            for w in wanted:
                mine = by_window.get(w.start, [])
                kept[w.start] = window_items(mine, in_window.get(w.start, []), w, started_in)
                seen[w.start] = screen_words(mine)
        here = {w.start for w in work}
        for start in [one for one in kept if one not in here]:
            del kept[start]                      # past the days looked at
            seen.pop(start, None)
        return {w.start: kept[w.start] for w in work}, started_in

    # -- the window under way -----------------------------------------------------------
    def live_window(self, now: datetime) -> Optional[Window]:
        """The window under way, built as a stored one is, cut where it has got to
        and given its project now - not when the grouping reaches it."""
        local = now.astimezone()
        boundary = floor_to(local, WINDOW_MINUTES)
        elapsed = (local - boundary).total_seconds() / 60.0
        tail = self.store.last(12)
        if elapsed < LIVE_LEAST_MIN:
            return tail[-1] if tail and local - tail[-1].start <= timedelta(
                minutes=2 * WINDOW_MINUTES) else None
        cut = max(1, int(math.ceil(elapsed)))
        events = self._events(boundary, local)
        # To the end of what is built, not to now: that end is up to a minute past it, and that
        # minute would count as watched where absences never were.
        watched = self._watched_from(boundary + timedelta(minutes=cut))
        built = build_windows(boundary, boundary + timedelta(minutes=cut), cut, events,
                              Carry.after(tail).within(watched, boundary), self._rules(), None,
                              busy=self._agent_at_work(boundary), watched_from=watched)
        if not built:
            return None
        live = built[0]
        if live.main != AWAY:
            self._place_live(live, events, boundary + timedelta(minutes=cut), local)
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        assign_units(self.store.since(midnight) + [live], lambda w: (w.project_id, w.project))
        return live

    def _watching(self, now: datetime) -> bool:
        """Whether a video has had the focus, one after another, for WATCHING_SECONDS."""
        began = None
        for row in self.db.query(
                "SELECT timestamp, application, data FROM raw_events WHERE event_type = "
                "'application.focused' AND timestamp <= ? ORDER BY timestamp DESC LIMIT ?",
                (now.astimezone(timezone.utc).isoformat(), WATCHING_LOOKS_BACK)):
            try:
                title = str(json.loads(row["data"] or "{}").get("window_title") or "")
            except ValueError:
                title = ""
            if label_for(str(row["application"] or ""), title, self._rules()) != "entertainment":
                break
            began = _aware(row["timestamp"])
        return began is not None and (now - began).total_seconds() >= WATCHING_SECONDS

    def _place_live(self, live: Window, events: List[Dict[str, Any]], end: datetime,
                    now: Optional[datetime] = None) -> None:
        """Its project: what the placed windows teach, then the same working path as work already
        found - not while a video has the focus. NOT WHAT THE WORDS ON THE SCREEN MEAN (the user,
        2026-11-06): a calendar lists every matter of the week, and none of them is what is being
        done. Replayed, the words placed 5 windows of 277 here, 4 of them wrongly: none is better
        than the wrong one."""
        if self._labelling is None:
            self.relabel()
        state = self._labelling or {"started_in": {}, "matcher": None, "index": None}
        intervals = focus_intervals(events, None, until=end)
        items = window_items(events, intervals, live,
                             {**state["started_in"], **session_folders(events)})
        seen = screen_words(events)
        self._live = {"how": "", "items": items, "seen": seen}
        matcher, index = state.get("matcher"), state.get("index")
        if matcher is not None and index is not None:
            found = matcher.match(items, live.start)
            if found and found[0] in index.units:
                live.project_id, live.project = found[0], index.names.get(found[0], "")
                self._live = {"how": "history", "items": items, "seen": seen}
                return
        if self._watching(now or end):
            return
        try:
            unit, name = self._place_by_paths(items)
        except Exception:
            log.debug("the window could not be placed", exc_info=True)
            unit, name = "", ""
        if unit:
            live.project_id, live.project = unit, name
            self._live = {"how": "paths", "items": items, "seen": seen}

    @staticmethod
    def _name_key(text: str) -> str:
        return re.sub(r"[-\s]+", "_", str(text or "").strip().lower())

    def _path_map(self) -> Dict[str, set]:
        held = getattr(self, "_path_map_held", None)
        if held and time.monotonic() - held[0] < PATH_MAP_SECONDS:
            return held[1]
        from ..states.tasks import Kept
        project_of: Dict[str, str] = {}
        for row in self.db.query("SELECT id, tasks FROM state_projects WHERE scope = 'live'"):
            for one in json.loads(row["tasks"] or "[]"):
                project_of[one] = row["id"]
        out: Dict[str, set] = {}
        try:
            tasks = Kept(self.db).live()
        except Exception:
            tasks = []
        for task in tasks:
            unit = project_of.get(task.id, task.id)
            texts = list(task.places()) + [str((task.described or {}).get("where") or "")]
            for text in texts:
                for part in re.split(r"[\\/\s,，;；:：()（）\"'“”「」、]+", text):
                    key = self._name_key(part)
                    if len(key) >= PATH_NAME_LEAST and re.fullmatch(r"[a-z0-9_.]+", key) and (
                            "_" in key or "." in key or any(c.isdigit() for c in key)
                            or len(key) >= 10):
                        out.setdefault(key, set()).add(unit)
        self._path_map_held = (time.monotonic(), out)
        return out

    def _place_by_paths(self, items: Dict[str, float]) -> Tuple[str, str]:
        names: Dict[str, float] = {}
        for op, seconds in items.items():
            for part in _NAME_SPLIT.split(str(op).split(":", 1)[-1]):
                key = self._name_key(part)
                if len(key) >= PATH_NAME_LEAST:
                    names[key] = names.get(key, 0.0) + float(seconds or 0)
        mapping = self._path_map()
        votes: Dict[str, float] = {}
        for key, seconds in names.items():
            units = mapping.get(key)
            if units and len(units) == 1:
                unit = next(iter(units))
                votes[unit] = votes.get(unit, 0.0) + max(1.0, seconds)
        if not votes:
            return "", ""
        best = max(votes, key=votes.get)
        if len(votes) > 1 and sorted(votes.values())[-2] >= votes[best] * 0.5:
            return "", ""
        return best, self.unit_name(best)

    def _place_by_words(self, items: Dict[str, float],
                        seen: Optional[set] = None) -> Tuple[str, str]:
        """BY THE WORDS IT SHARES WITH EACH PIECE OF WORK - the last way (the
        user, 2026-11-03: the raw events are in every language and the model
        of meanings reads English; "基于token-based similarity"). The titles in
        front and the words on the screen (`seen`: a class's name in the code,
        a document's terms - "根据代码内容"), cut into words (Latin words, pairs
        of Chinese, Japanese and Korean characters), against each task's words
        in the user's language and in English, and its places. The best must
        lead the next clearly: none is better than the wrong one."""
        if not bool(self.config.get("monitor_place_by_words", True) if self.config else True):
            return "", ""
        scores = self._word_scores(items, seen or set())
        if not scores or scores[0][0] < WORDS_AT or (
                len(scores) > 1 and scores[0][0] < WORDS_LEAD * scores[1][0]):
            return "", ""
        return scores[0][1], self.unit_name(scores[0][1])

    def _word_scores(self, items: Dict[str, float], seen: set) -> List[Tuple[float, str]]:
        """Each piece of work's score, the best first: the cosine of the
        window's words and the work's, each word worth how rare it is among
        the pieces of work - a big project is not ahead for having many words."""
        asked = set(seen)
        for op in items:
            if str(op).startswith("t:"):
                asked |= words_of(str(op)[2:])
        units = self._unit_words()
        if not units or not asked:
            return []
        many: Dict[str, int] = {}
        for found in units.values():
            for word in found:
                many[word] = many.get(word, 0) + 1
        rare = {word: math.log(1 + len(units) / count) for word, count in many.items()}
        known = [word for word in asked if word in rare]
        size = math.sqrt(sum(rare[word] ** 2 for word in known)) or 1.0
        scores = []
        for unit, found in units.items():
            dot = sum(rare[word] ** 2 for word in known if word in found)
            if dot:
                length = math.sqrt(sum(rare[word] ** 2 for word in found)) or 1.0
                scores.append((dot / size / length, unit))
        scores.sort(reverse=True)
        return scores

    def _unit_words(self) -> Dict[str, set]:
        """Each project's (else each task's) words, held a few minutes."""
        held = getattr(self, "_unit_words_held", None)
        if held and time.monotonic() - held[0] < PATH_MAP_SECONDS:
            return held[1]
        from ..states.tasks import Kept
        project_of: Dict[str, str] = {}
        out: Dict[str, set] = {}
        for row in self.db.query("SELECT id, name, tasks, described FROM state_projects "
                                 "WHERE scope = 'live' AND COALESCE(archived_at, '') = ''"):
            for one in json.loads(row["tasks"] or "[]"):
                project_of[one] = row["id"]
            out.setdefault(row["id"], set()).update(words_of(str(row["name"] or "")))
            try:
                said = json.loads(row["described"] or "{}")
            except ValueError:
                said = {}
            out[row["id"]].update(words_of(_texts_of(said)))
        try:
            tasks = Kept(self.db).live()
        except Exception:
            tasks = []
        inside: Dict[str, List[Tuple[str, str, set]]] = {}
        for task in tasks:
            said = task.described or {}
            if said.get("deleted_by_user"):
                continue
            unit = project_of.get(task.id, task.id)
            mine = words_of(_texts_of(said))
            for place in task.places():
                mine.update(words_of(str(place)))
            out.setdefault(unit, set()).update(mine)
            inside.setdefault(unit, []).append((task.id, str(said.get("title") or ""), mine))
        self._unit_words_held = (time.monotonic(), out)
        self._unit_tasks = inside
        return out

    def task_in(self, unit: str, items: Dict[str, float], seen: set) -> Tuple[str, str]:
        """(id, title) of the task of `unit` the window most likely belongs to:
        the one it shares the most rare words with; the only one, when there
        is one; none when nothing says which."""
        if not unit:
            return "", ""
        self._unit_words()
        tasks = self.__dict__.get("_unit_tasks", {}).get(unit) or []
        if len(tasks) == 1:
            return tasks[0][0], tasks[0][1]
        asked = set(seen)
        for op in items:
            if str(op).startswith("t:"):
                asked |= words_of(str(op)[2:])
        many: Dict[str, int] = {}
        for _ident, _title, mine in tasks:
            for word in mine:
                many[word] = many.get(word, 0) + 1
        best, score = None, 0.0
        for one in tasks:
            weight = sum(1.0 / many[word] for word in asked & one[2]) / max(1.0, len(one[2])) ** 0.5
            if weight > score:
                best, score = one, weight
        return (best[0], best[1]) if best else ("", "")

    def unit_name(self, unit: str) -> str:
        if unit.startswith("project"):
            row = self.db.query_one("SELECT name FROM state_projects WHERE id = ?", (unit,))
            return str(row["name"]) if row and row["name"] else ""
        row = self.db.query_one("SELECT described FROM state_tasks WHERE id = ?", (unit,))
        try:
            return str(json.loads(row["described"] or "{}").get("title") or "") if row else ""
        except ValueError:
            return ""

    # -- habits ---------------------------------------------------------------------------
    def scattered_at(self, now: datetime) -> float:
        """How many focus changes an hour is scattered for this user, from their own weeks. The
        line was written and never used: a fixed 12, past which 65 in 100 windows of real work were
        (measured 2026-11-06), said nothing."""
        held_at, line = self._scattered
        if line is None or time.monotonic() - held_at >= HABIT_EVERY_S:
            line = state_module.scattered_line(self.store.since(now - timedelta(days=SCATTERED_DAYS)))
            self._scattered = (time.monotonic(), line)
        return line

    def habits(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """WEEKS OF HABITS (the user, 2026-11-03): over the last four weeks, on
        days like today - when work begins, how long a stretch runs before a
        break, how much is usually done by now, what this half hour usually is."""
        local = (now or datetime.now(timezone.utc)).astimezone()
        key = f"{local.date()}:{time_bucket(local)}"
        held_at, held_key, held = self._habits
        if held_key == key and time.monotonic() - held_at < HABIT_EVERY_S:
            return held
        since = (local - timedelta(days=HABIT_DAYS)).replace(hour=0, minute=0, second=0,
                                                             microsecond=0)
        today = local.replace(hour=0, minute=0, second=0, microsecond=0)
        kind = day_type(local.weekday())
        days: Dict[Any, List[Window]] = {}
        for w in self.store.between(since, today):
            days.setdefault(w.start.date(), []).append(w)
        alike = {day: ws for day, ws in days.items()
                 if day_type(ws[0].start.weekday()) == kind
                 and any(w.main != AWAY for w in ws)}
        starts, by_now, stretches, at_this = [], [], [], {}
        clock = local.hour * 60 + local.minute
        for ws in alike.values():
            work = [w for w in ws if w.main != AWAY]
            first = work[0].start
            starts.append(first.hour + first.minute / 60.0)
            by_now.append(sum(w.minutes * (1 - w.idle_share) for w in work
                              if w.start.hour * 60 + w.start.minute < clock))
            for one, nxt in zip(ws, ws[1:]):
                if one.main != AWAY and nxt.since_break_min < one.since_break_min:
                    stretches.append(one.since_break_min)
            for w in work:
                if w.bucket == time_bucket(local):
                    at_this[w.main] = at_this.get(w.main, 0) + 1
        enough = len(alike) >= HABIT_LEAST_DAYS
        usual_now = ""
        if enough and at_this:
            label, count = max(at_this.items(), key=lambda kv: kv[1])
            usual_now = label if count >= max(2, len(alike) // 3) else ""
        out = {"habit_days": len(alike),
               "usual_start_hour": round(_median(starts), 2) if enough else -1.0,
               "usual_stretch_minutes": round(_median(stretches), 0) if enough and stretches
               else -1.0,
               "usual_worked_by_now_minutes": round(_median(by_now), 0) if enough else -1.0,
               "usual_activity_now": usual_now,
               "projects_this_week": self._projects_by_week(local)}
        self._habits = (time.monotonic(), key, out)
        return out

    def _projects_by_week(self, local: datetime) -> List[Dict[str, Any]]:
        """The projects of the last seven days, with the hours of each against
        its weekly usual over the three weeks before."""
        week = local - timedelta(days=7)
        before = local - timedelta(days=HABIT_DAYS)
        now_h: Dict[str, float] = {}
        then_h: Dict[str, float] = {}
        names: Dict[str, str] = {}
        for w in self.store.between(before, local):
            if w.main == AWAY or not w.project_id:
                continue
            names[w.project_id] = w.project or names.get(w.project_id, "")
            hours = w.minutes * (1 - w.idle_share) / 60.0
            target = now_h if w.start >= week else then_h
            target[w.project_id] = target.get(w.project_id, 0.0) + hours
        rows = [{"project": names.get(unit) or unit, "hours_this_week": round(hours, 1),
                 "usual_hours_a_week": round(then_h.get(unit, 0.0) / 3.0, 1)}
                for unit, hours in now_h.items()]
        return sorted(rows, key=lambda one: -one["hours_this_week"])[:8]

    def habits_text(self, now: Optional[datetime] = None) -> str:
        """The habits in words, for the model's context."""
        said = self.habits(now)
        if said["habit_days"] < HABIT_LEAST_DAYS:
            return f"Too few days yet to say what is usual ({said['habit_days']} like today)."

        def clock(hours: float) -> str:
            return "unknown" if hours < 0 else f"{int(hours):02d}:{int(round(hours % 1 * 60)) % 60:02d}"
        lines = [f"Read from {said['habit_days']} days like today in the last four weeks.",
                 f"- work usually begins at {clock(said['usual_start_hour'])}",
                 f"- a stretch usually runs {said['usual_stretch_minutes']:.0f} min before a break"
                 if said["usual_stretch_minutes"] >= 0 else "- no usual stretch yet",
                 f"- by now, {said['usual_worked_by_now_minutes']:.0f} min are usually worked",
                 f"- at this half hour the user is usually: {said['usual_activity_now'] or 'nothing usual'}"]
        for one in said["projects_this_week"]:
            lines.append(f"- {one['project']}: {one['hours_this_week']} h this week, "
                         f"{one['usual_hours_a_week']} h a usual week")
        return "\n".join(lines)

    # -- the look -------------------------------------------------------------------------
    def snapshot(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """THE SIGNALS NOW, and the EVENTS since the last look ("events")."""
        with self._lock:
            when = now or datetime.now(timezone.utc)
            local = when.astimezone()
            live = self.live_window(when)
            mode_state = (state_module.infer_state(live, self.scattered_at(when))
                          if live is not None else None)
            try:
                idle = self.idle()
            except Exception:
                idle = None
            idle = float(idle) if idle is not None else 0.0
            away_after = float(self.config.get("listener_presence_seconds", AWAY_AFTER_S)
                               if self.config else AWAY_AFTER_S)
            front = self._front(local)
            # A key pressed at the lock screen wakes it: nobody is back yet. The
            # model was asked about two such returns, and rightly said nothing.
            from ...data_manage.storage.raw_store import _away as a_locked_screen
            locked = a_locked_screen(front.get("application", ""))
            present = idle < away_after and not locked
            if not present and self._away_since is None:
                self._away_since = when - timedelta(seconds=idle)
            back_after = 0.0
            if present and self._away_since is not None:
                self._back = (when, (when - self._away_since).total_seconds() / 60.0)
                self._away_since = None
            if self._back is not None:
                if (when - self._back[0]).total_seconds() <= BACK_FOR_MIN * 60:
                    back_after = self._back[1]
                else:
                    self._back = None
            midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
            today = [w for w in self.store.since(midnight) if w.main != AWAY]
            if live is not None and live.main != AWAY and (not today or live.start > today[-1].start):
                today = today + [live]
            first = today[0].start if today else None
            worked = sum(w.minutes * (1 - w.idle_share) for w in today)
            project_id = (live.project_id if live is not None else "") or ""
            held = self.__dict__.get("_live") or {}
            how = (held.get("how") or self.__dict__.get("_told", {}).get(
                live.start if live is not None else None, "")) if project_id else ""
            task_id, task = self.task_in(project_id, held.get("items") or {},
                                         held.get("seen") or set())
            habits = self.habits(when)
            usual_by_now = habits["usual_worked_by_now_minutes"]
            said: Dict[str, Any] = {
                "hour": round(local.hour + local.minute / 60.0, 2),
                "weekday": local.weekday(), "is_weekend": local.weekday() >= 5,
                "date": local.date().isoformat(),
                "present": present, "idle_seconds": round(idle, 0),
                "away_minutes": round((when - self._away_since).total_seconds() / 60.0, 1)
                if self._away_since is not None else 0.0,
                "back_after_minutes": round(back_after, 1),
                "mode": mode_state.mode if mode_state else state_module.GONE,
                "depth": mode_state.depth if mode_state else "",
                "activity": live.main if live is not None else AWAY,
                "switch_rate": round(live.switch_rate, 1) if live is not None else 0.0,
                "application": front.get("application", ""),
                "title": front.get("title", ""),
                "screen_locked": locked, **self._on_now(front, local),
                "work_started_today": first.strftime("%H:%M") if first else "",
                "work_started_hour": round(first.hour + first.minute / 60.0, 2) if first else -1.0,
                "stretch_minutes": round(live.session_elapsed_min, 0) if live is not None else 0.0,
                "since_break_minutes": round(live.since_break_min, 0) if live is not None else 0.0,
                "work_minutes": round(live.own_work_min, 0) if live is not None else 0.0,
                "worked_today_minutes": round(worked, 0),
                "project_id": project_id,
                "project": (live.project if live is not None else "") or "",
                "project_how": how, "task_id": task_id, "task": task,
                "project_minutes": round(live.on_project_min, 0) if live is not None else 0.0,
                "project_today_minutes": round(live.project_today_min, 0) if live is not None
                else 0.0,
                "project_days_since": self._days_since(project_id, midnight),
                "habit_days": habits["habit_days"],
                "usual_start_hour": habits["usual_start_hour"],
                "usual_stretch_minutes": habits["usual_stretch_minutes"],
                "usual_worked_by_now_minutes": usual_by_now,
                "worked_vs_usual_minutes": round(worked - usual_by_now, 0) if usual_by_now >= 0
                else 0.0,
                "usual_activity_now": habits["usual_activity_now"],
                "at": when.isoformat(),
            }
            said["events"] = self._events_of(said)
            self._previous = said
            return said

    def _days_since(self, project_id: str, midnight: datetime) -> float:
        if not project_id:
            return -1.0
        row = self.db.query_one(
            "SELECT MAX(start_at) AS t FROM activity_windows WHERE project_id = ? AND start_at < ?",
            (project_id, midnight.astimezone(timezone.utc).isoformat()))
        last = _aware(row["t"]) if row and row["t"] else None
        if last is None:
            return -1.0
        return float((midnight.date() - last.astimezone().date()).days)

    def _pages_lately(self, local: datetime) -> List[str]:
        """The addresses a browser was on lately, the newest first."""
        since = (local - timedelta(hours=MEETING_PAGE_HOURS)).astimezone(timezone.utc).isoformat()
        out = []
        for row in self.db.query("SELECT data FROM raw_events WHERE event_type = "
                                 "'browser.navigation' AND timestamp >= ? "
                                 "ORDER BY timestamp DESC LIMIT 40", (since,)):
            try:
                out.append(str(json.loads(row["data"] or "{}").get("url") or ""))
            except ValueError:
                continue
        return out

    def _on_now(self, front: Dict[str, str], local: datetime) -> Dict[str, Any]:
        """What is running and what holds the screen (screen_now.py)."""
        if time.monotonic() >= self._running_due:
            self._running = self.programs()
            self._running_due = time.monotonic() + RUNNING_EVERY_S
        held, microphone = "", []
        try:
            held = self.platform.what_holds_the_screen()
            microphone = self.platform.microphone_users()
        except Exception:
            log.debug("the system could not say what holds the screen", exc_info=True)
        application, title = front.get("application", ""), front.get("title", "")
        return {"running": ", ".join(self._running), "full_screen": bool(held),
                "presenting": screen_now.presenting(application, title, held),
                "in_meeting": screen_now.in_meeting(
                    application, title, self._running, microphone,
                    self._pages_lately(local) if microphone else ()),
                "microphone_in_use": ", ".join(microphone)}

    def _front(self, local: datetime) -> Dict[str, str]:
        row = self.db.query_one(
            "SELECT application, data FROM raw_events WHERE event_type = 'application.focused' "
            "AND timestamp <= ? ORDER BY timestamp DESC LIMIT 1",
            (local.astimezone(timezone.utc).isoformat(),))
        if not row:
            return {}
        try:
            data = json.loads(row["data"] or "{}")
        except ValueError:
            data = {}
        return {"application": str(row["application"] or ""),
                "title": str(data.get("window_title") or "")[:200]}

    def _events_of(self, now: Dict[str, Any]) -> List[str]:
        """What changed since the last look - each said once."""
        before = self._previous
        out: List[str] = []
        if not before:
            return out
        if now["present"] and now["back_after_minutes"] > 0 and not before.get(
                "back_after_minutes"):
            out.append("back")
        day = now["date"]
        if (now["present"] and now["worked_today_minutes"] <= DAY_BEGAN_LEAST_WORK_MIN
                and now["mode"] != state_module.GONE and self._kv("monitor.day_began") != day
                and (now["back_after_minutes"] >= DAY_GAP_MIN or self._first_look_today(day))):
            self._set_kv("monitor.day_began", day)
            out.append("day_began")
        if now["project_id"] and now["project_id"] != before.get("project_id"):
            out.append("project_changed")
            if now["project_days_since"] < 0:
                out.append("new_work")
        if (now["activity"] == "entertainment" and before.get("activity") != "entertainment"
                and before.get("mode") in (state_module.WORKING, state_module.SHALLOW)):
            out.append("leisure")
        if self._closing_up():
            out.append("closing_up")
        return out

    def _closing_up(self) -> bool:
        """Many windows closed in the last few minutes, said once for them."""
        try:
            count = self.platform.count_windows()
        except Exception:                                # no platform, or it cannot say
            count = None
        if count is None:
            return False
        moment = time.monotonic()
        seen = [one for one in self._window_counts if moment - one[0] <= CLOSING_WITHIN_SECONDS]
        seen.append((moment, count))
        most = max(one[1] for one in seen)
        closed = most - count
        closing = closed >= CLOSING_LEAST_FEWER and closed >= CLOSING_SHARE * most
        self._window_counts = [seen[-1]] if closing else seen
        return closing

    def _first_look_today(self, day: str) -> bool:
        """The application started today and nothing was worked before it."""
        return not self._kv("monitor.day_began") or self._kv("monitor.day_began") < day

    @staticmethod
    def describe() -> Dict[str, Any]:
        """The interface, as the model is shown it (monitor_signals)."""
        return {"signals": [{"name": name, "type": kind, "means": means}
                            for name, (kind, means) in SIGNALS.items()],
                "events": [{"name": name, "means": means} for name, means in EVENTS.items()]}


__all__ = ["Signals", "SIGNALS", "EVENTS", "WINDOW_MINUTES"]
