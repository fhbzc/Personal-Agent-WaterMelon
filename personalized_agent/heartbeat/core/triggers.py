"""AT ONCE, WHEN SOMETHING ON THE SCREEN CALLS FOR IT - not a whole heartbeat:
each rule, when what is on the screen matches it, runs the one thing it is
for, on that window alone.

  INVITATION   a meeting invitation is in front - Gmail's "Yes No Maybe",
               an .ics, "Invitation:", "Accept Decline", 会议邀请 - so its
               time and place are there to be read now.
  TIME AGREED  a time is being agreed - "the time works for me", "see you
               on", 时间可以 - though the message with the time may be folded
               away: what is agreed waits for its time (pending.py).

Both run the NOTIFICATION step (heartbeat step 3) - only it, one model call:
the window READ WHOLE (not the screen's new lines only - every line it
shows, and the words in its picture), the notifications already on the
calendar, what is waiting for a time and the lessons about notifications.
What it answers is applied as the heartbeat's own step 3 is.

Matching is a look at the words, no model: on every event, in the
listener's own thread, and quick - and ONLY AT WHAT IS NEW in that window:
a line it showed before (within `FRESH_FOR`) does not match again, so a
conversation that scrolls back into view, or a terminal where these very
words were discussed hours ago, is not a reason. A WINDOW is the window
itself - its handle, while it is open - not its title, which a terminal
changes as it works. The first look at a window after the application
starts only learns what it shows - after a restart the screen reads all of
it as new. A window is looked at once in `COOLDOWN`; a match
waits `SETTLE` seconds for the page to finish showing. NOT WHEN NOBODY IS
THERE: with no keyboard or mouse for `AWAY_AFTER` seconds, nothing runs.
Never the agent's own windows. The listener's exclusions already keep out
what it is not to read.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

#: Seconds a match waits before the window is read: the page settles.
SETTLE = 12.0
#: A window is looked at once in this many seconds, whatever matched.
COOLDOWN = 600.0
#: The most of a window read whole.
MOST_WINDOW = 20000
#: The window's own recent lines, when it is no longer in front.
RECENT_MINUTES = 5
#: A line a window showed is not new in it again for this long.
FRESH_FOR = 12 * 3600.0
#: Lines remembered for one window, at most.
MOST_SEEN = 5000
#: No keyboard or mouse for this long: nobody is there, and nothing runs.
AWAY_AFTER = 180.0

_OURS = ("personalizedagent", "personalized agent")


@dataclass
class Rule:
    name: str
    why: str
    title: Optional["re.Pattern[str]"]
    text: Optional["re.Pattern[str]"]
    action: str = "notify"

    def matches(self, title: str, text: str) -> bool:
        return bool((self.title and self.title.search(title or ""))
                    or (self.text and self.text.search(text or "")))


RULES = [
    Rule("invitation",
         "a meeting invitation is on the screen: its time and place can be read now",
         re.compile(r"(?i)(\binvitation\b|\binvited you\b|\binvite\b.*\bto\b|"
                    r"会议邀请|日程邀请|邀请您参加|邀请你参加)"),
         re.compile(r"(?i)(\bYes\s+No\s+Maybe\b|\binvite\.ics\b|\.ics\b|\bAccept\s+Decline\b|"
                    r"\bRSVP\b|\bAdd to (?:Google )?Calendar\b|Join (?:Zoom|Teams) Meeting|"
                    r"\bGoing\?\s|接受\s*拒绝|是否参加|日程邀请|会议邀请)")),
    Rule("time_agreed",
         "a time is being agreed: if the time itself is not in view, it waits for its time",
         None,
         re.compile(r"(?i)(\bthe time works\b|\bthat time works\b|\bworks for me\b|"
                    r"\bsee you (?:on|at|then|next)\b|\blet'?s (?:meet|talk) (?:on|at)\b|"
                    r"\bschedule (?:a|the) (?:call|meeting|talk)\b|\bwhen (?:are|would) you "
                    r"(?:be )?(?:free|available)\b|时间可以|这个时间|到时候见|约个时间|"
                    r"那就定在|定在周)")),
]


def _clean(title: str) -> str:
    from ..reading.log import clean_title
    return clean_title(title)


class Triggers:
    """Watches the listener's events; runs a rule's step at once."""

    def __init__(self, heartbeat: Any, bus: Any, raw_store: Any = None, config: Any = None,
                 rules: Optional[List[Rule]] = None,
                 front: Optional[Callable[[], Any]] = None,
                 idle: Optional[Callable[[], Optional[float]]] = None) -> None:
        self.heartbeat = heartbeat
        self.bus = bus
        self.raw_store = raw_store
        self.config = config
        self.rules = list(RULES if rules is None else rules)
        #: The window in front, read whole - the platform's own reader.
        self.front = front or _front_window
        self._lock = threading.Lock()
        self._due: Dict[str, Dict[str, Any]] = {}     # window -> what matched, when due
        self._looked: Dict[str, float] = {}            # window -> when last looked at
        #: Each window's lines, and when each was last shown: what is new.
        self._seen: Dict[str, Dict[str, float]] = {}
        #: The windows looked at since the application started.
        self._known: set = set()
        #: Seconds since the keyboard or mouse was last used - None, unknown.
        self.idle = idle or (lambda: None)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._unsubscribe: Optional[Callable[[], None]] = None
        #: What was done, the latest first: for the debugger.
        self.history: List[Dict[str, Any]] = []

    # -- watching -----------------------------------------------------------------------------------
    def start(self) -> None:
        if self.bus is not None and self._unsubscribe is None:
            self._unsubscribe = self.bus.subscribe("listener.*", self.heard)
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="triggers", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._thread is not None:
            self._thread.join(5)

    def heard(self, topic: str, event: Any) -> None:
        """One event - in the listener's thread: a look at its words, and,
        on a match, the window marked to be read when it has settled."""
        if not isinstance(event, dict) or not self.enabled():
            return
        data = event.get("data") or {}
        title = str(data.get("window_title") or data.get("title") or "")
        text = str(data.get("text") or "")
        application = str(event.get("application") or data.get("application") or "")
        if not (title or text) or any(mark in (title + " " + application).lower()
                                      for mark in _OURS):
            return
        if not self.present():
            return
        key = window_key(application, data.get("handle"), title)
        fresh_title, fresh_text = self.fresh(key, title, text)
        # THE FIRST LOOK AT A WINDOW SINCE THE APPLICATION STARTED only learns
        # what it shows: what the screen reads first after a restart is all
        # of it - an old conversation too - and none of it is new. An
        # invitation on the screen at that moment waits for the next
        # heartbeat instead.
        with self._lock:
            first = key not in self._known
            self._known.add(key)
        if first:
            return
        if not (fresh_title or fresh_text):
            return
        for rule in self.rules:
            if rule.matches(fresh_title, fresh_text):
                self.mark(rule, key, _clean(title) or title, application,
                          fresh=fresh_text or fresh_title, handle=int(data.get("handle") or 0))
                return

    def fresh(self, key: str, title: str, text: str,
              now: Optional[float] = None) -> "tuple[str, str]":
        """What of an event is NEW in its window: its title, if the window
        did not have it lately, and the lines it did not show lately."""
        now = time.time() if now is None else now
        with self._lock:
            seen = self._seen.setdefault(key, {})
            if len(seen) > MOST_SEEN:
                for line, at in sorted(seen.items(), key=lambda one: one[1])[:len(seen) // 2]:
                    seen.pop(line, None)

            def new(line: str) -> bool:
                said = " ".join(line.split())
                if len(said) < 3:
                    return False
                last = seen.get(said)
                seen[said] = now
                return last is None or now - last > FRESH_FOR

            fresh_title = title if title and new("title: " + title) else ""
            fresh_lines = [line for line in str(text or "").splitlines() if new(line)]
        return fresh_title, "\n".join(fresh_lines)

    def present(self) -> bool:
        """Whether somebody is at the computer: the keyboard or mouse used
        within `AWAY_AFTER` seconds - unknown counts as yes."""
        try:
            idle = self.idle()
        except Exception:
            return True
        return idle is None or float(idle) <= AWAY_AFTER

    def mark(self, rule: Rule, key: str, window: str = "", application: str = "",
             now: Optional[float] = None, fresh: str = "", handle: int = 0) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            if now - self._looked.get(key, -1e18) < COOLDOWN:
                return False
            if key in self._due:
                # More of it while it settles: what is new is all given.
                due = self._due[key]
                due["fresh"] = (due["fresh"] + "\n" + fresh).strip()[-6000:]
                return False
            self._due[key] = {"rule": rule, "key": key, "window": window or key,
                              "application": application, "handle": handle,
                              "fresh": fresh[-6000:], "at": now + SETTLE}
        self._wake.set()
        return True

    def enabled(self) -> bool:
        from ...common.modules import enabled as module_on
        return self.config is None or (bool(self.config.get("triggers_enabled", True))
                                       and module_on(self.config, "calendar"))

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(5.0)
            self._wake.clear()
            for one in self._ready():
                if self._stop.is_set():
                    return
                try:
                    if self.present():
                        self.run(one["rule"], one["window"], one["application"],
                                 fresh=one.get("fresh", ""), handle=one.get("handle", 0))
                except Exception:                  # pragma: no cover - the next match tries
                    log.warning("a trigger could not run", exc_info=True)

    def _ready(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        now = time.time() if now is None else now
        with self._lock:
            ready = [one for one in self._due.values() if one["at"] <= now]
            for one in ready:
                self._due.pop(one["key"], None)
                self._looked[one["key"]] = now
        return ready

    # -- running -------------------------------------------------------------------------------------
    def run(self, rule: Rule, window: str, application: str = "", fresh: str = "",
            handle: int = 0) -> Dict[str, Any]:
        """The rule's step: on what just appeared, with the window read
        whole for what it refers to."""
        said = self.read_window(window, handle)
        if not (said.strip() or fresh.strip()):
            return self._kept({"rule": rule.name, "window": window, "skipped": "nothing to read"})
        if rule.action == "notify":
            report = notify_now(self.heartbeat, rule, window, said, fresh)
        else:                                      # pragma: no cover - a rule of another kind
            report = {"skipped": f"no action {rule.action}"}
        return self._kept(dict(report, rule=rule.name, window=window))

    def _kept(self, report: Dict[str, Any]) -> Dict[str, Any]:
        report["at"] = datetime.now(timezone.utc).isoformat()
        self.history.insert(0, report)
        del self.history[50:]
        return report

    def read_window(self, window: str, handle: int = 0) -> str:
        """The window READ WHOLE: from the screen now, when it is still in
        front - every line it shows, not only the new ones; else what the
        screen showed of it lately. And the words in its latest picture."""
        parts: List[str] = []
        try:
            front = self.front()
        except Exception:
            front = None
        same = (int(getattr(front, "handle", 0) or 0) == handle) if handle else \
            (front is not None and _clean(getattr(front, "title", "")) == window)
        if front is not None and same and getattr(front, "text", ""):
            parts.append(str(front.text)[:MOST_WINDOW])
        else:
            parts.append(self._recent_text(window))
        words = self._picture_words(window)
        if words:
            parts.append("WORDS IN A PICTURE OF THE WINDOW (read on this computer):\n" + words)
        return "\n\n".join(part for part in parts if part.strip())[:MOST_WINDOW]

    def _recent_rows(self, window: str, kind: str) -> List[Dict[str, Any]]:
        if self.raw_store is None:
            return []
        since = (datetime.now(timezone.utc) - timedelta(minutes=RECENT_MINUTES)).isoformat()
        rows = self.raw_store.db.query(
            "SELECT data FROM raw_events WHERE event_type = ? AND timestamp >= ? "
            "ORDER BY timestamp", (kind, since))
        out = []
        for row in rows:
            try:
                data = json.loads(row["data"] or "{}")
            except ValueError:
                continue
            if _clean(str(data.get("window_title") or "")) == window:
                out.append(data)
        return out

    def _recent_text(self, window: str) -> str:
        seen: List[str] = []
        for data in self._recent_rows(window, "screen.text"):
            text = str(data.get("text") or "")
            if text and text not in seen:
                seen.append(text)
        return "\n".join(seen)[-MOST_WINDOW:]

    def _picture_words(self, window: str) -> str:
        shots = self._recent_rows(window, "screen.shot")
        if not shots:
            return ""
        from ...runtime import documents
        try:
            return str(documents.extract(shots[-1].get("path") or "").get("text") or "")[:6000]
        except Exception:
            return ""


def _front_window() -> Any:
    try:
        from ...platform_layer.window_text import text_in_front
        return text_in_front()
    except Exception:
        return None


TRIGGER_INTRO = """THIS WAS ASKED AT ONCE, NOT IN A REVIEW: {why}. You are given WHAT JUST
APPEARED in a window - the reason this was asked - and that window read
whole, for what it refers to. Not a stretch of activity: there are no event
or state ids to open, and nothing else to read.

ACT ONLY ON WHAT JUST APPEARED, and on what it refers to. The rest of the
window is history: a conversation, a terminal, a document may hold old
messages, examples or a discussion of exactly this kind of thing - none of
it is a reason to add anything. Nothing new to act on is a good answer.

Add, update or close notifications, and write, resolve or drop what is
waiting for a time. In "evidence", say "the window" and quote the line that
shows it."""


def window_key(application: str, handle: Any, title: str) -> str:
    """A window as itself: its application and handle while it is open -
    or, where there is no handle (a page's own event), its title."""
    try:
        handle = int(handle or 0)
    except (TypeError, ValueError):
        handle = 0
    if handle:
        return f"{str(application or '').lower()}#{handle}"
    return "title:" + (_clean(title) or title)


def notify_now(heartbeat: Any, rule: Rule, window: str, text: str,
               fresh: str = "") -> Dict[str, Any]:
    """The heartbeat's step 3, on one window: one call, its answer applied
    as the step's own."""
    from ...common.languages import in_display
    from ...common.events.models import new_id
    from ...models.base import ModelRequest
    from .heartbeat import PREAMBLE, STEP_PROMPTS, _extract_json, _now_said
    step = STEP_PROMPTS["step_notify"]
    scheduled = heartbeat._notifications_ahead(with_checks=True)
    waiting = getattr(heartbeat, "pending", None)
    keeper = getattr(heartbeat, "calendar_nature", None)
    waiting_lines = waiting.lines() if waiting is not None else []
    lessons = ""
    if keeper is not None:
        try:
            lessons = keeper.text()
        except Exception:
            lessons = ""
    sections = [
        _now_said(),
        TRIGGER_INTRO.format(why=rule.why),
        f"1. WHAT JUST APPEARED in {window} - act on this:\n\n"
        + (fresh.strip() or "(only the window's title)"),
        f"2. THE WINDOW, READ WHOLE - for what that refers to; the rest is history:\n\n"
        + (text or "(not readable now)"),
        f"3. NOTIFICATIONS ALREADY SCHEDULED, {len(scheduled)}\nOn the user's calendar now, "
        "each to pop up at its time. One about the same thing at a nearby time is a "
        "duplicate.\n\n" + ("\n".join(scheduled) or "None."),
        f"4. WAITING FOR A TIME, {len(waiting_lines)}\nAgreed, its time not known yet - "
        "each with its id.\n\n" + ("\n".join(waiting_lines) or "None."),
        "5. LESSONS ABOUT THE USER'S NOTIFICATIONS\n\n" + (lessons or "None learned yet."),
        step["instruction"],
        "Return ONLY this JSON object:\n" + step["returns"],
    ]
    try:
        response = heartbeat.models.invoke(ModelRequest(
            messages=[{"role": "user", "content": "\n\n".join(sections)}],
            system=PREAMBLE, max_tokens=4000, task="heartbeat_trigger",
            language=in_display()), role="heartbeat")
    except Exception as exc:
        log.info("the trigger could not ask the model: %s", exc)
        return {"skipped": f"the model could not be asked: {exc}"[:300]}
    answered = _extract_json(getattr(response, "text", "") or "") or {}
    task_id = new_id("trigger")
    report: Dict[str, Any] = {"task_id": task_id}
    heartbeat._notify_writer = {"model": getattr(response, "model", ""),
                                "provider": getattr(response, "provider", "")}
    if waiting is not None:
        report.update(waiting.apply(answered))
    added = heartbeat._add_notifications(task_id, answered.get("notifications"), report)
    if added:
        report["notifications_scheduled"] = added
    closed = heartbeat._close_notifications(task_id, answered.get("closed"))
    if closed:
        report["notifications_closed"] = closed
    updated = heartbeat._update_notifications(task_id, answered.get("updated"))
    if updated:
        report["notifications_updated"] = updated
    return report


__all__ = ["Triggers", "Rule", "RULES", "notify_now", "SETTLE", "COOLDOWN"]
