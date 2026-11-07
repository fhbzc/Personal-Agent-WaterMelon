"""Which project the user is in right now, from what they are doing (section 36.3.2).

The agent groups states into projects hours after the time they cover, so the
work in progress belongs to no project until then. Waiting for that is waiting
for exactly the moment a prediction is wanted. This reads the operations
themselves - the window or document in front, the folders and files touched, the
sites visited, and the paths that appear in the text of the window the user is
working in - and sets them against the operations of the windows the agent has
already placed.

It is plain on purpose. An operation is a whole thing, not words from it: a
window's title, a folder, a file's path, a site, a folder named in a terminal.
For each, count how long it was in front, and in how many separate stretches, in
the windows the agent placed - in which project, or which other task. An operation
that was only ever in one place says where the window is; one that was in many -
the mailbox, the browser - says nothing.

**How strong the evidence is depends on how much of it there is, in time and in
recurrence, and neither alone is the measure.** One coding session open for three
hours is strong evidence for the project it was in; so is a project opened ten
times in a day for ten minutes each. A glance of a few seconds is weak, and so is
a single short visit. Evidence is one minus the chance of seeing that much by
accident: it grows with the minutes and with the number of separate stretches, and
either can carry it.

Counting words was tried first and was worse: words from titles are mostly the
user's ordinary day, and many of them each a little more common in one project's
windows added up to a false certainty (a trip's flight searches went into a
research project). Other tasks the agent knows are classes too: a window like the
ones where the user was booking flights is that task, and "not this project" is
as good an answer as a project.

From PR #6 (the rhythm of the day, branch junyi), taken on 2026-11-03 as
the Monitor's real-time input (monitor/signals.py).
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Set, Tuple, Union

from .types import Window

#: An operation speaks for a class only if this share of the time it was in front
#: was in that class.
SPECIFICITY = 0.7
#: The class must lead the next best by this much (of a score that runs from 0
#: up, a fully certain operation adding 1).
LEAD = 0.2
#: And must reach this: about one operation seen for a good while, or two
#: well-supported ones seen briefly.
MIN_SCORE = 0.7
#: Windows this far apart or less are one stretch of the same operation.
STRETCH_GAP = timedelta(minutes=30)
#: Minutes of an operation in a class that make its evidence about two thirds as
#: strong as it can be, and the number of separate stretches that do.
MINUTES_FOR_EVIDENCE = 10.0
STRETCHES_FOR_EVIDENCE = 3.0
#: Seconds in front, in the window being read, that make that operation count
#: about two thirds as much as it can: a two-second prompt counts a little, a
#: few minutes counts fully.
SECONDS_FOR_PRESENCE = 45.0

#: An operation in a class that was in front within half an hour of this window -
#: the same session still going - is about this certain, however little of it there
#: is in total. The agent's grouping lags the work by hours, so for the work in
#: progress the only thing already known is the stretch just before it.
CONTINUITY_EVIDENCE = 0.85

#: Seconds an event with no duration of its own stands for.
EVENT_SECONDS = 30.0

#: Fields of an event that name a place the user was working.
PATH_FIELDS = ("path", "destination_path", "source_path", "cwd", "project")

#: What a browser adds to a title that says nothing about the page: how many
#: other tabs, whose profile, which browser.
_TAB_COUNT = re.compile(r"\s*和另外\s*\d+\s*个页面.*$|\s+and\s+\d+\s+more\s+pages?.*$")
_BROWSER_TAIL = re.compile(r"\s+-\s+(microsoft.{0,3}edge|google chrome|mozilla firefox|brave)\s*$")
#: A program drawing a spinner or a star before a title is saying it is busy.
_LEADING_MARK = re.compile(r"^[^\w一-鿿]+")
_SCHEME = re.compile(r"^[a-z]+://")
#: A path written in text: ``~/dir/sub``, ``/project/x/y``, ``C:\\dir\\sub``.
_UNIX_PATH = re.compile(r"(?:~|/)[\w.\-@]+(?:/[\w.\-@]+)+")
_WINDOWS_PATH = re.compile(r"[A-Za-z]:[\\/](?:[\w.\-]+[\\/])*[\w.\-]+")
#: The most distinct folder names taken from one reading of the screen.
MOST_NAMES_PER_READING = 40

Operations = Dict[str, float]

#: The banner a coding assistant draws when it starts: its name and version, the model,
#: and - a couple of lines down - the folder it was started in. The one line of a
#: session's text that is the folder and not a file the conversation happens to mention.
_BANNER = re.compile(
    r"Claude Code v[\d.]+[^\n]*\n(?:[^\n]*\n){0,3}?[^\n]*?"
    r"((?:[A-Za-z]:\\|~/|/home/|/project/|/scratch/)[^\s]*)")
#: What a session is called before it has a name of its own: not one session's title.
GENERIC_SESSION_TITLES = ("claude code", "new session")
TERMINAL_PROGRAMS = ("terminal", "powershell", "pwsh", "cmd", "conhost", "wezterm",
                     "alacritty", "iterm", "konsole", "mintty")


def session_folders(events: Sequence[Dict[str, Any]]) -> Dict[str, Set[str]]:
    """Session title -> the folder names of where it was started, from its banner.

    The terminal's title for a coding session is the session's name; the folder it
    was started from is on screen only for the first moments, in the banner. Where a
    reading of the screen caught the banner, the title and the folder are tied
    together, and every later window that session is in front can say where it is.
    Banners caught while the title was still the default are not used: that title is
    every new session's.
    """
    out: Dict[str, Set[str]] = {}
    for event in events:
        if event["event_type"] != "screen.text":
            continue
        # Only a terminal: a web page about the assistant quotes the same banner.
        program = str(event.get("application") or "").lower()
        if not any(name in program for name in TERMINAL_PROGRAMS):
            continue
        data = event.get("data") or {}
        found = _BANNER.search(str(data.get("text") or ""))
        if not found:
            continue
        title = clean_title(data.get("window_title") or "")
        if not title or title in GENERIC_SESSION_TITLES:
            continue
        # The banner's path is the folder itself, often one level under the home
        # (`~/Agentpedia`), which the general path reader - written for paths with
        # several parts - does not take.
        names = {part.lower() for part in re.split(r"[\\/]", found.group(1))
                 if len(part) >= 5 and not part.startswith("~") and ":" not in part}
        if names:
            out.setdefault(title, set()).update(names)
    return out


def clean_title(title: str) -> str:
    text = _TAB_COUNT.sub("", str(title or "").lower()).strip()
    text = _BROWSER_TAIL.sub("", text)
    text = _LEADING_MARK.sub("", text).strip()
    return text if len(text) >= 4 else ""


#: What a title is cut to when it is said back as what a window was about.
MOST_TITLE_CHARS = 60


def about(title: str) -> str:
    """What a window's title says it is about: the page, the document, the folder, the
    session - without the program's name, a count of tabs or a leading mark."""
    text = clean_title(title)[:MOST_TITLE_CHARS].strip()
    return "" if text in GENERIC_SESSION_TITLES else text


def folder_names(text: str) -> Set[str]:
    """The folder and file names of the paths written in a stretch of text.

    A terminal's text says where the work is - the path of a file a coding
    assistant is reading, a listing, a prompt - even when its title no longer does.
    Short names are left out: they are everywhere.
    """
    names: Set[str] = set()
    for found in _UNIX_PATH.findall(text) + _WINDOWS_PATH.findall(text):
        for part in re.split(r"[\\/]", found.lower()):
            if len(part) >= 5 and not part.startswith("~") and ":" not in part:
                names.add(part)
                if len(names) >= MOST_NAMES_PER_READING:
                    return names
    return names


def window_items(events: Sequence[Dict[str, Any]], intervals: Sequence[Any],
                 window: Window,
                 session_cwd: Optional[Mapping[str, Set[str]]] = None) -> Operations:
    """What the user did in one window, as whole things and for how long.

    ``t:`` a title that was in front, ``d:`` the folder of a file touched (its last
    two parts), ``f:`` the file, ``h:`` a site, ``p:`` a folder or file name written
    in the text of what was on screen - or, for a coding session whose banner was
    caught, the folder it was started in. The value is the seconds it was in front, or
    stood for: a title for as long as it was in front, a screen reading for as long
    as the text stayed, any other event for half a minute. Never more than the
    window itself.
    """
    end = window.start + timedelta(minutes=window.minutes)
    cap = window.minutes * 60.0
    found: Dict[str, float] = defaultdict(float)
    for item in intervals:
        if item.end <= window.start or item.start >= end:
            continue
        seconds = item.seconds_within(window.start, end)
        title = clean_title(getattr(item, "title", ""))
        if title and seconds >= 1:
            found["t:" + title] += seconds
            # A session whose banner was caught says where it was started, for as
            # long as it was in front: the folder of the conversation.
            for name in (session_cwd or {}).get(title, ()):
                found["p:" + name] += seconds
    for event in events:
        kind = event["event_type"]
        if kind.startswith("application."):
            continue                              # the intervals carry the titles
        try:
            when = datetime.fromisoformat(str(event["timestamp"]))
        except (TypeError, ValueError):
            continue
        if not (window.start <= when < end):
            continue
        data = event.get("data") or {}
        if kind == "screen.text":
            seconds = min(cap, float(data.get("over_seconds") or EVENT_SECONDS))
            for name in folder_names(str(data.get("text") or "")):
                found["p:" + name] += seconds
            continue
        for field in PATH_FIELDS:
            value = data.get(field)
            if not value:
                continue
            path = str(value).lower().replace("\\", "/")
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 2:
                found["d:" + "/".join(parts[:-1][-2:])] += EVENT_SECONDS
            found["f:" + path] += EVENT_SECONDS
        url = data.get("url")
        if url:
            host = _SCHEME.sub("", str(url).lower()).split("/")[0]
            if host:
                found["h:" + host] += EVENT_SECONDS
    return {item: min(cap, seconds) for item, seconds in found.items()}


#: A source as a state gives a window: `firefox.exe window 'Its title'`.
_SOURCE_WINDOW = re.compile(r"""^\S+ window ['"](.*)['"]$""")


def named_in(source: str) -> Set[str]:
    """THE OPERATIONS A STATE'S SOURCE NAMES, written as `window_items` writes them. A page's address
    names none: one page of a site is not the site, and taken so, one video made the whole of it
    a single project's."""
    text = str(source or "").strip()
    found = _SOURCE_WINDOW.match(text)
    if found:
        title = clean_title(found.group(1))
        return {"t:" + title} if title else set()
    if _SCHEME.match(text.lower()):
        return set()
    path = text.lower().replace("\\", "/")
    parts = [part for part in path.split("/") if part]
    out = {"f:" + path} | {"p:" + part for part in parts}
    if len(parts) >= 2:
        out.add("d:" + "/".join(parts[:-1][-2:]))
    title = clean_title(text)
    if title:
        out.add("t:" + title)
    return out


def _as_operations(items: Union[Mapping[str, float], Iterable[str]]) -> Operations:
    """Operations as seconds; a bare set of names stands for a minute each."""
    if isinstance(items, Mapping):
        return dict(items)
    return {item: 60.0 for item in items}


class ProjectMatcher:
    """Operations per class, learned from windows the agent has already placed.

    A class is a project, or a task that is in none.
    """

    def __init__(self) -> None:
        self._minutes: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self._stretches: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._last: Dict[Tuple[str, str], datetime] = {}
        #: When each operation was in front in each class, for telling whether a
        #: window continues a session that is already known.
        self._starts: Dict[Tuple[str, str], list] = defaultdict(list)
        #: How many windows each class has been seen in. An operation is held
        #: against how often it shows up in a class's windows, not against how
        #: many minutes in total: the biggest class has the most of everything,
        #: and by raw share every operation in the user's day looks like theirs.
        self._windows_in: Dict[str, int] = defaultdict(int)

    def learn(self, placed: Iterable[Tuple[Any, ...]]) -> None:
        """``placed``: (class, operations, start) for each window the agent
        placed, in time order; ``start`` may be left out.

        Each operation is credited with the minutes it was in front in that
        class, and - separately - with one more stretch whenever it turns up
        again after more than half an hour. Both are evidence, and neither is
        capped by the other: a session of three hours is one stretch and a great
        many minutes, ten visits of ten minutes are ten stretches and a hundred.
        """
        for entry in placed:
            cls, items = entry[0], _as_operations(entry[1])
            start = entry[2] if len(entry) > 2 else None
            self._windows_in[cls] += 1
            for item, seconds in items.items():
                self._minutes[item][cls] += seconds / 60.0
                last = self._last.get((item, cls))
                continuing = (start is not None and last is not None
                              and start - last <= STRETCH_GAP)
                if start is not None:
                    self._last[(item, cls)] = start
                    self._starts[(item, cls)].append(start)
                if not continuing:
                    self._stretches[item][cls] += 1

    def anchor(self, cls: str, items: Union[Mapping[str, float], Iterable[str]],
               start: datetime) -> None:
        """Note that a window the matcher placed was in this class, so that the
        window after it can be seen to continue it. Only that: what is guessed is
        not counted as seen, or a mistake would vouch for itself."""
        for item in _as_operations(items):
            self._starts[(item, cls)].append(start)

    def _continues(self, item: str, cls: str, start: Optional[datetime]) -> bool:
        if start is None:
            return False
        return any(abs(start - seen) <= STRETCH_GAP for seen in self._starts.get((item, cls), ()))

    def evidence(self, item: str, cls: str) -> float:
        """0 to 1: how much has been seen of this operation in this class."""
        minutes = self._minutes.get(item, {}).get(cls, 0.0)
        stretches = self._stretches.get(item, {}).get(cls, 0)
        return 1.0 - math.exp(-(minutes / MINUTES_FOR_EVIDENCE
                                + stretches / STRETCHES_FOR_EVIDENCE))

    def scores(self, items: Union[Mapping[str, float], Iterable[str]],
               start: Optional[datetime] = None) -> Dict[str, float]:
        out: Dict[str, float] = defaultdict(float)
        for item, seconds in _as_operations(items).items():
            per = self._minutes.get(item)
            if not per:
                continue
            # How much of each class's time this operation takes up, so that a
            # class that is simply bigger does not win by being bigger.
            rate = {cls: minutes / max(1, self._windows_in[cls])
                    for cls, minutes in per.items()}
            total = sum(rate.values())
            present = 1.0 - math.exp(-seconds / SECONDS_FOR_PRESENCE)
            for cls, one in rate.items():
                share = one / total if total else 0.0
                if share >= SPECIFICITY:
                    seen = self.evidence(item, cls)
                    if self._continues(item, cls, start):
                        seen = max(seen, CONTINUITY_EVIDENCE)
                    out[cls] += share * seen * present
        return dict(out)

    def match(self, items: Union[Mapping[str, float], Iterable[str]],
              start: Optional[datetime] = None) -> Optional[Tuple[str, float]]:
        """(class, score) - or None when no class speaks clearly. ``start`` is
        when the window began, for seeing which session it continues."""
        ranked = sorted(self.scores(items, start).items(), key=lambda kv: (-kv[1], kv[0]))
        if not ranked or ranked[0][1] < MIN_SCORE:
            return None
        if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < LEAD:
            return None
        return ranked[0]
