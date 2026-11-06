"""Turning the focus and presence events of a stretch of time into windows
(section 36.3.2).

Nothing is read from the screen. A window is made of which application was in
front and for how long, when the user was away, and which files were touched;
the label of a window is a rule over application names and titles, which the
user can edit, and not a guess made by a model.

From PR #6 (the rhythm of the day, branch junyi), taken on 2026-11-03 as
the Monitor's real-time input (monitor/signals.py).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .types import AWAY, OTHER, REST_LABELS, Window

log = logging.getLogger(__name__)

#: A fully idle window is "away"; this much of it must be idle to count.
AWAY_SHARE = 0.8
#: Away this long ends a break; away this long ends the session.
BREAK_GAP_MIN = 5
#: Around each thing a coding assistant says, how long it is taken to be at work.
AGENT_GRACE_SECONDS = 120
SESSION_GAP_MIN = 30
#: Windows of continuous absence kept. Eight hours asleep is one fact, not
#: thirty-two, and persistence would be right about every one of them.
MAX_AWAY_RUN = 8
#: Files named in a window's references.
MAX_REFERENCES = 5

#: Label -> words looked for in "application title", lowercased. The first
#: label with a match wins, so the order is part of the rule.
DEFAULT_RULES: Dict[str, Tuple[str, ...]] = {
    "meeting": ("zoom", "teams", "meet.google", "webex", "腾讯会议", "feishu meeting"),
    "communication": ("slack", "wechat", "微信", "discord", "outlook", "mail",
                      "telegram", "whatsapp", "qq", "lark", "飞书", "dingtalk"),
    "entertainment": ("youtube", "bilibili", "netflix", "steam", "spotify",
                      "twitch", "game", "douyin", "抖音", "知乎", "weibo"),
    "coding": ("code", "visual studio", "pycharm", "idea", "terminal", "powershell",
               "cmd.exe", "windowsterminal", "git", "jupyter", "vim", "cursor",
               "github.com", "stackoverflow"),
    "writing": ("word", "winword", "notion", "obsidian", "typora", "overleaf",
                "docs.google", "pages", "notepad", "latex"),
    "reading": ("acrobat", "pdf", "zotero", "arxiv", "kindle", "sumatra", "preview",
                "scholar", "paper"),
    "browsing": ("chrome", "firefox", "edge", "safari", "brave", "browser"),
}


def parse_rules(text: str) -> Dict[str, Tuple[str, ...]]:
    """The user's own rules: one ``label: word, word`` per line. Words they
    give are looked for before the built-in ones."""
    rules: Dict[str, Tuple[str, ...]] = {}
    for line in str(text or "").splitlines():
        label, _, words = line.partition(":")
        label = label.strip().lower()
        found = tuple(w.strip().lower() for w in words.split(",") if w.strip())
        if label and found:
            rules[label] = found
    return rules


def label_for(application: str, title: str,
              rules: Optional[Dict[str, Tuple[str, ...]]] = None) -> str:
    text = f"{application or ''} {title or ''}".lower()
    mine = rules or {}
    for source in (mine, DEFAULT_RULES):
        for label, words in source.items():
            if any(word in text for word in words):
                return label
    return OTHER


# -- events -> intervals ------------------------------------------------------
@dataclass
class Interval:
    start: datetime
    end: datetime
    label: str = OTHER
    application: str = ""
    title: str = ""                 # the window's title, for matching to a project

    def seconds_within(self, start: datetime, end: datetime) -> float:
        low, high = max(self.start, start), min(self.end, end)
        return max(0.0, (high - low).total_seconds())


def _when(text: Any) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def focus_intervals(events: Iterable[Dict[str, Any]],
                    rules: Optional[Dict[str, Tuple[str, ...]]] = None,
                    until: Optional[datetime] = None) -> List[Interval]:
    """Where the front window was, from the focus events.

    A period is only written when it ends. A session in one editor for two
    hours has no ``focus_ended`` until the user leaves it, so the last
    ``focused`` with nothing ended after it is an open period running to
    ``until`` - without that, the longest stretches of work read as absence.
    """
    events = list(events)
    out: List[Interval] = []
    focused = [(m, e) for e in events if e["event_type"] == "application.focused"
               for m in [_when(e["data"].get("activation_time")) or _when(e["timestamp"])]
               if m is not None]
    if focused and until is not None:
        began, last = max(focused, key=lambda pair: pair[0])
        closed = False
        for e in events:
            if e["event_type"] == "application.focus_ended":
                ended = _when(e["data"].get("deactivation_time")) or _when(e["timestamp"])
                # The period before this one ends a moment before this one
                # begins; a second of margin keeps that from closing it.
                closed = closed or (ended is not None
                                    and ended >= began + timedelta(seconds=1))
        if not closed and until > began:
            app = str(last.get("application") or "")
            out.append(Interval(began, until, label_for(
                app, last["data"].get("window_title") or "", rules), app,
                str(last["data"].get("window_title") or "")))
    for event in events:
        if event["event_type"] != "application.focus_ended":
            continue
        data = event["data"]
        end = _when(data.get("deactivation_time")) or _when(event["timestamp"])
        seconds = float(data.get("duration_seconds") or 0)
        if end is None or seconds <= 0:
            continue
        app = str(event.get("application") or "")
        out.append(Interval(end - timedelta(seconds=seconds), end,
                            label_for(app, data.get("window_title") or "", rules), app,
                            str(data.get("window_title") or "")))
    return sorted(out, key=lambda i: i.start)


def away_intervals(events: Iterable[Dict[str, Any]], until: datetime,
                   gone: str = "presence.away", back: str = "presence.back") -> List[Interval]:
    """Pair each ``presence.away`` with the ``presence.back`` after it; one
    with no back runs to ``until``."""
    out: List[Interval] = []
    began: Optional[datetime] = None
    for event in sorted(events, key=lambda e: e["timestamp"]):
        moment = _when(event["timestamp"])
        if moment is None:
            continue
        if event["event_type"] == gone and began is None:
            began = moment
        elif event["event_type"] == back and began is not None:
            out.append(Interval(began, moment, AWAY))
            began = None
    if began is not None:
        out.append(Interval(began, until, AWAY))
    return out


def referenced_files(events: Iterable[Dict[str, Any]], start: datetime,
                     end: datetime) -> List[str]:
    seen: Dict[str, None] = {}
    for event in events:
        if not event["event_type"].startswith("filesystem."):
            continue
        moment = _when(event["timestamp"])
        path = (event["data"] or {}).get("path")
        if moment is None or not path or not (start <= moment < end):
            continue
        seen.setdefault(os.path.basename(str(path)), None)
    return list(seen)[:MAX_REFERENCES]


# -- the running totals a window starts from -----------------------------------
@dataclass
class Carry:
    session: float = 0.0
    since_break: float = 0.0
    on_task: float = 0.0
    main: str = OTHER
    away_run: float = 0.0           # minutes of unbroken absence just before
    away_windows: int = 0
    own_work: float = 0.0

    @classmethod
    def after(cls, windows: List[Window]) -> "Carry":
        """What the totals were at the end of the last stored windows."""
        if not windows:
            return cls()
        last = windows[-1]
        run = 0
        for w in reversed(windows):
            if w.main != AWAY:
                break
            run += 1
        return cls(last.session_elapsed_min, last.since_break_min, last.on_task_min,
                   last.main, run * last.minutes, run, last.own_work_min)

    def within(self, watched_from: datetime, start: datetime) -> "Carry":
        """No longer than absences have been recorded, whatever was stored before they were."""
        most = max(0.0, (start - watched_from).total_seconds() / 60.0)
        self.session = min(self.session, most)
        self.since_break = min(self.since_break, most)
        self.own_work = min(self.own_work, most)
        return self


def agent_alone_seconds(quiet: List[Interval], busy: List[Tuple[datetime, datetime]],
                        start: datetime, end: datetime) -> float:
    """How long an agent was at work with the user's hands off the machine."""
    merged: List[List[datetime]] = []
    for low, high in sorted(busy):
        if merged and low <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], high)
        else:
            merged.append([low, high])
    total = 0.0
    for hands in quiet:
        for low, high in merged:
            total += max(0.0, (min(hands.end, high, end) - max(hands.start, low, start)).total_seconds())
    return total


def build_windows(start: datetime, end: datetime, minutes: int,
                  events: List[Dict[str, Any]], carry: Optional[Carry] = None,
                  rules: Optional[Dict[str, Tuple[str, ...]]] = None,
                  state_lookup: Optional[Callable[[datetime], Tuple[Optional[str], str]]] = None,
                  busy: Sequence[Tuple[datetime, datetime]] = (),
                  watched_from: Optional[datetime] = None,
                  ) -> List[Window]:
    """The windows from ``start`` to ``end``, each ``minutes`` long, in order.

    ``session_elapsed_min``, ``since_break_min`` and ``on_task_min`` are the
    totals at the END of the window: they describe the user at the moment
    the next window is about to begin, which is the moment a prediction is
    made.

    TWO COUNTS (the user, 2026-11-04: told they had worked 160 minutes, when much
    of it was their agent's; "连续看电脑多久，而不管工作还是娱乐，连续工作还有个更短的"):
    ``since_break_min`` is time at the screen, whatever is on it.
    ``own_work_min`` is the user's own work: not time resting - a window of it
    is a break from work - and not time an agent was at work (``busy``, and
    what a coding assistant said) while their hands were off the machine.
    Hands off alone is no sign: reading is work.

    ``watched_from``: since when absences have been recorded. Before it a window left in front
    overnight looks like work, so what the windows show stays but no stretch runs: the first
    build reaches eight days back, and on a machine with no absences in them that read as
    eight days without a break (the user, 2026-11-05: 11530 minutes).
    """
    carry = carry or Carry()
    focus = focus_intervals(events, rules, until=end)
    away = away_intervals((e for e in events if e["event_type"].startswith("presence.")), end)
    quiet = away_intervals((e for e in events if e["event_type"].startswith("presence.")), end,
                           "presence.hands_off", "presence.hands_on")
    agent = list(busy)
    for event in events:
        moment = _when(event["timestamp"]) if event["event_type"] == "assistant.message" else None
        if moment is not None:
            agent.append((moment - timedelta(seconds=AGENT_GRACE_SECONDS),
                          moment + timedelta(seconds=AGENT_GRACE_SECONDS)))
    out: List[Window] = []
    step = timedelta(minutes=minutes)
    seconds = minutes * 60.0
    here = start
    while here + step <= end:
        until = here + step
        by_label: Dict[str, float] = {}
        starts = 0
        for item in focus:
            if item.end <= here or item.start >= until:
                continue
            used = item.seconds_within(here, until)
            for gone in away:               # presence is not gated on focus
                used -= _overlap(item, gone, here, until)
            if used > 0:
                by_label[item.label] = by_label.get(item.label, 0.0) + used
            if here <= item.start < until:
                starts += 1
        active = sum(by_label.values())
        idle = max(0.0, 1.0 - active / seconds)
        if idle >= AWAY_SHARE or not by_label:
            main, shares = AWAY, {}
        else:
            shares = {k: round(v / active, 3) for k, v in sorted(
                by_label.items(), key=lambda kv: -kv[1])}
            main = next(iter(shares))
        # Sleeping through the night is one fact, not thirty-two.
        if main == AWAY and carry.away_windows >= MAX_AWAY_RUN:
            carry.away_windows += 1
            carry.away_run += minutes
            here = until
            continue
        if main == AWAY:
            carry.away_windows += 1
            carry.away_run += minutes
            carry.on_task = 0.0
        else:
            if carry.away_run >= SESSION_GAP_MIN:
                carry.session = 0.0
            if carry.away_run >= BREAK_GAP_MIN:
                carry.since_break = carry.own_work = 0.0
            if watched_from is not None and until <= watched_from:
                carry.session = carry.since_break = carry.own_work = 0.0
                idle_stretch = True
            else:
                idle_stretch = False
            worked = 0.0 if idle_stretch else minutes * (1.0 - idle)
            carry.session += worked
            carry.since_break += worked
            if main in REST_LABELS:
                carry.own_work = 0.0                 # resting is a break from work
            elif not idle_stretch:
                rest = sum(by_label.get(one, 0.0) for one in REST_LABELS)
                alone = agent_alone_seconds(quiet, agent, here, until)
                carry.own_work += minutes * max(0.0, active - rest - alone) / seconds
            carry.on_task = carry.on_task + minutes if main == carry.main else float(minutes)
            carry.away_run = 0.0
            carry.away_windows = 0
        carry.main = main
        state_id, task = (state_lookup(here) if state_lookup else (None, ""))
        out.append(Window(
            start=here, minutes=minutes, main=main, activities=shares, task=task or "",
            session_elapsed_min=carry.session, since_break_min=carry.since_break,
            own_work_min=carry.own_work,
            on_task_min=carry.on_task, switch_rate=starts * 3600.0 / seconds,
            idle_share=idle, references=referenced_files(events, here, until),
            state_id=state_id))
        here = until
    return out


def _overlap(item: Interval, gone: Interval, start: datetime, end: datetime) -> float:
    low = max(item.start, gone.start, start)
    high = min(item.end, gone.end, end)
    return max(0.0, (high - low).total_seconds())


def floor_to(moment: datetime, minutes: int) -> datetime:
    """The start of the window ``moment`` is in, on the clock the moment is on."""
    stamp = moment.replace(second=0, microsecond=0)
    return stamp - timedelta(minutes=stamp.minute % minutes)


#: A break this short, in windows, does not end a stretch on a project.
SHORT_BREAK_WINDOWS = 2


def assign_units(windows: List[Window],
                 lookup: Callable[[Window], Tuple[str, str]]) -> int:
    """Say which project each window's work was in, and for how long.

    ``lookup`` gives (project id, project name) for a window, or empty strings
    when the work is in no project the agent has grouped. This is a separate
    pass from building the windows because it cannot be done then: the agent
    groups states into tasks and tasks into projects hours later, so a window
    is recorded first and learns its project afterwards - and this is run again
    whenever the grouping may have moved.

    The totals are as at the END of the window, like the others. A stretch on
    one project is not ended by a break of half an hour or less, and a break
    is not counted as time on it. ``windows`` must be in order and must begin
    at the start of a local day, because ``project_today_min`` counts from it.

    Returns how many windows changed.
    """
    changed = 0
    today: Dict[str, float] = {}
    day = None
    current, minutes, away_run = "", 0.0, 0
    previous: Optional[Window] = None
    for w in windows:
        if day != w.start.date():
            day, today = w.start.date(), {}
        before = (w.project_id, w.project, w.on_project_min, w.project_today_min)
        if w.main == AWAY:
            w.project_id = w.project = ""
            w.on_project_min = 0.0
            w.project_today_min = 0.0
            away_run += 1
        else:
            w.project_id, w.project = lookup(w)
            joined = previous is not None and (
                w.start - previous.start == timedelta(minutes=previous.minutes))
            same = w.unit == current and (
                (away_run == 0 and joined)
                or (0 < away_run <= SHORT_BREAK_WINDOWS))
            minutes = minutes + w.minutes if same else float(w.minutes)
            current, away_run = w.unit, 0
            today[current] = today.get(current, 0.0) + w.minutes
            w.on_project_min = minutes
            w.project_today_min = today[current]
        if (w.project_id, w.project, w.on_project_min, w.project_today_min) != before:
            changed += 1
        previous = w
    return changed
