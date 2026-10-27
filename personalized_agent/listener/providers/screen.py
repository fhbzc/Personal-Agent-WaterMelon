"""What the window in front was showing (section 19.2).

Every other provider records that something happened: a file changed, a page
was visited, an application came to the front. None of them records *what was
in it*, and the difference is the difference between "you had Firefox open
for two hours" and "you spent two hours reading about X".

It also answers, for free, the question section 19 spends its length on.
Whether a file change was the user's doing or a program's is hard because the
filesystem does not say who. What is on the screen in front of somebody is
theirs by definition - an assistant reading a thousand files in the
background appears in no window at all.

It is on by default, which puts the weight on everything that narrows it.
Each of these is load-bearing:

- **Only the window in front**, and only while somebody is at the keyboard.
  A machine left alone still has a window in front of it
- **Never a password field.** The platform marks them and a marked element
  is skipped wherever it appears
- **Never a window on the excluded list**, matched against the application
  and the title both. That list matters more than any other in the Listener:
  a filename recorded by mistake is a filename, and a window recorded by
  mistake is everything that was in it
- **Only what changed.** The same page read twice is one record and then
  nothing, because nothing happened the second time
- **One switch turns it off**, and it is the first one to reach for.
"""

from __future__ import annotations

import collections
import hashlib
import copy
import logging
import os
import threading
import time
import types
from typing import Any, Dict, List, Optional, Tuple

from ...common.events.models import ListenerEvent, new_id
from ...platform_layer import get_platform
from ...platform_layer.window_text import is_wechat_application
from ...version import APP_NAME, FORMER_NAMES
from ..core.provider import ListenerProvider, NotOnThisMachine, PluginManifest
from ..core.manager import STOPPING_GRACE_SECONDS
from . import fast_reading

log = logging.getLogger(__name__)


class ScreenProvider(ListenerProvider):
    provider_id = "screen"
    category = "screen"

    #: How many windows' contents are remembered for comparison. Enough for
    #: everything somebody has open; the oldest goes first.
    REMEMBERED = 40

    #: A line has to be this long to count as something that was read. Single
    #: words are buttons and labels, and a window full of them says nothing
    #: about what the user was doing.
    WORTH_A_LINE = 3

    #: How long a window keeps its place after the user leaves it. Coming
    #: back to a page after an hour and reading further is a new sitting;
    #: switching to another window and straight back is not.
    SAME_SITTING_SECONDS = 1800.0

    #: How much new text is worth an event of its own.
    #:
    #: Every other provider records something that happened once - a file
    #: changed, a window came to the front - and one event is the natural
    #: unit. Reading is not like that: it is continuous, and sampling it
    #: every few seconds turns one person reading one page into a hundred
    #: events, each a few lines, each needing a model to say what it was.
    #:
    #: Measured on one machine: 133 readings over eleven minutes produced 98
    #: events, about 535 an hour, against a data heartbeat that reads forty
    #: model-needing events a round. Gathered into sittings the same reading
    #: is a handful of events with the same words in them.
    ENOUGH_TO_REPORT = 1200

    #: How long a sitting is held before it is reported anyway.
    #:
    #: Somebody reading slowly, or watching something, would otherwise never
    #: reach the size above, and their afternoon would arrive in one lump
    #: when they finally switched windows.
    HOLD_AT_MOST_SECONDS = 180.0

    #: How often the window in front is asked after - its application and
    #: title alone, a fraction of a millisecond - so a window read often
    #: (fast_reading.py) is known within a second of coming to the front.
    FOCUS_CHECK_SECONDS = 1.0

    def __init__(self, config: Any, platform: Any = None, bus: Any = None) -> None:
        super().__init__()
        self.config = config
        self.platform = platform or get_platform()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.bus = bus
        self._wake = threading.Event()
        self._unsubscribe = None
        self._front_application = ''
        self._front_title = ''
        self._was_fast = False
        self._typing = False
        self._next_read_at = 0.0
        self._last_read_started_at = None
        #: FAST SAMPLING (fast_reading.py): how often a window read often is
        #: looked at, and whether it is read.
        self._pace = fast_reading.Pace(config)
        self._wechat_visit = None
        self._seen: "collections.OrderedDict[int, Tuple[float, set]]" = \
            collections.OrderedDict()
        #: What has been read in the window in front and not yet reported:
        #: (handle, application, title, [lines], first seen at).
        self._gathering: Optional[dict] = None
        self._readings = 0
        self._recorded = 0
        self._gathered = 0
        self._unchanged = 0
        self._skipped = 0
        self._ourselves = 0
        self._milliseconds = 0.0
        #: Per application: each line it has shown, and the different pages
        #: (window and title) it was shown on - see `_furniture`.
        self._shown_on: Dict[str, "collections.OrderedDict[str, set]"] = {}
        self._furniture_dropped = 0
        #: Per page (application and title): fingerprints of the lines already
        #: recorded there - see `_without_the_furniture`.
        self._kept_on: "collections.OrderedDict[str, set]" = collections.OrderedDict()
        self._repeats_dropped = 0

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        if not self._can_read():
            raise NotOnThisMachine(
                "this platform cannot read the contents of a window: "
                + (self._why_not() or "no accessibility interface"))
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._next_read_at = 0.0
        self._last_read_started_at = None
        self._was_fast = False
        self._pace.forget()
        if self.bus is not None and self._unsubscribe is None:
            self._unsubscribe = self.bus.subscribe('listener.application.focused', self._focus_changed)
        self._thread = threading.Thread(target=self._loop, name="listener-screen",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._thread:
            self._thread.join(timeout=STOPPING_GRACE_SECONDS)
            self._thread = None
        # Whatever was being gathered is what the user was reading when the
        # agent was closed, which is the part of their day most likely to
        # matter to them.
        self.flush_what_is_gathered()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                delay = self._tick()
            except Exception as exc:       # one unreadable window is not fatal
                self.record_error(str(exc)[:200])
                delay = self._interval()
            self._wake.wait(delay)

    def _front(self) -> Any:
        """The window in front - its application, title and process - asked
        cheaply: no reading, no picture."""
        ask = getattr(self.platform.activity, 'get_active_window', None)
        if callable(ask):
            try:
                return ask()
            except Exception:
                return None
        return None

    def _tick(self) -> float:
        """One turn of the loop: which window is in front, and - a window
        read often - a look at it, read when it changed; any other window
        read at the ordinary interval."""
        requested = self._wake.is_set()
        self._wake.clear()
        now = time.monotonic()
        front = self._front()
        if front is not None:
            self._front_application = front.application or ''
            self._front_title = front.title or ''
        peek = getattr(self.platform.activity, 'peek_foreground_application', None)
        if callable(peek):
            self._front_application = peek() or self._front_application
            if front is None and self._front_application:
                # The application alone, where that is all that is asked.
                front = types.SimpleNamespace(application=self._front_application,
                                              title=self._front_title, pid=None)
        fast = (front is not None and self._allowed_front(front)
                and fast_reading.wanted(self.config, self._front_application, self._front_title))
        if not fast:
            if self._was_fast:
                self._pace.forget()
                self._wechat_visit = None
                # Back from a window read often: the ordinary interval from now.
                self._next_read_at = now + self._interval()
            self._was_fast = False
            if requested and self._last_read_started_at is None or now >= self._next_read_at:
                self._last_read_started_at = now
                try:
                    self._look()
                finally:
                    self._next_read_at = now + self._interval()
            return max(0.1, min(self._next_read_at - time.monotonic(), self.FOCUS_CHECK_SECONDS))
        if not self._was_fast:
            self._pace.forget()
        self._was_fast = True
        # Only how long since a key was pressed - never which key.
        self._typing = self._recent_keyboard_input()
        picture = None
        image_of = getattr(self.platform.activity, 'front_window_image', None)
        if callable(image_of):
            try:
                picture = fast_reading.fingerprint(image_of())
            except Exception:
                picture = None
        changed = self._pace.looked(
            (self._front_application, self._front_title, getattr(front, 'pid', None)),
            picture, now)
        if self._pace.read_now(changed or requested, self._typing, now):
            self._last_read_started_at = now
            began = time.monotonic()
            try:
                self._look()
            finally:
                # Never overlapped: the next look is counted from the end.
                self._pace.took(now, time.monotonic() - began)
        return self._pace.interval(self._typing, time.monotonic())

    def _focus_changed(self, _topic, payload) -> None:
        self._front_application = payload.get('application') or ''
        if not is_wechat_application(self._front_application):
            self._wechat_visit = None
        # A window come to the front is looked at now - and, read often,
        # read if it changed.
        self._wake.set()

    def settings_changed(self) -> None:
        self._wake.set()

    def _interval(self) -> float:
        if self._was_fast:
            return self._pace.interval(self._typing, time.monotonic())
        try:
            asked = float(self.config.get("listener_screen_interval_seconds", 5.0))
        except (TypeError, ValueError):
            asked = 5.0
        # Other windows keep their configured interval and their sittings.
        return max(2.0, asked)

    def _recent_keyboard_input(self) -> bool:
        if (not self.config.get('listener_enabled', True)
                or not self.config.get('listener_screen_enabled', True)):
            return False
        ask = getattr(self.platform.activity, 'seconds_since_keyboard_input', None)
        if not callable(ask):
            return False
        try:
            age = ask()
            return age is not None and 0 <= float(age) <= fast_reading.TYPING_SECONDS
        except Exception:
            return False

    # -- the reading --------------------------------------------------------
    def _look(self) -> None:
        if (not bool(self.config.get("listener_screen_enabled", True))
                or not bool(self.config.get("listener_enabled", True))):
            self._gathering = None
            self._pace.forget()
            return
        if not self._somebody_is_here():
            # Not read while away: what changed meanwhile is read on return.
            self._pace.forget()
            return
        reading = self._read_the_front()
        if reading is None:
            # Failed/blocked/skipped reads are not proof of a quiet window.
            self._pace.forget()
            return
        self._front_application = reading.application or ''
        self._readings += 1
        self._milliseconds += reading.milliseconds
        if self._is_the_agent_itself(reading):
            self._ourselves += 1
            return
        if self._excluded(reading.application, reading.title):
            self._skipped += 1
            return
        structured = getattr(reading, 'wechat_capture', None)
        if is_wechat_application(reading.application) and structured:
            peer_name = str(structured.get('peer_name') or '')
            if self._excluded(reading.application, peer_name):
                self._skipped += 1
                return
            self.flush_what_is_gathered()
            if self._wechat_visit is None:
                self._wechat_visit = new_id('wechat_visit')
            capture = copy.deepcopy(structured)
            remaining = len(self._trimmed('x' * 16000))
            kept = []
            for message in reversed(capture.get('messages') or []):
                text = str(message.get('text') or '')
                if remaining <= 0:
                    capture['content_truncated'] = True
                    break
                if len(text) > remaining:
                    message['text'] = text[-remaining:]
                    message['text_truncated'] = True
                    capture['content_truncated'] = True
                kept.append(message)
                remaining -= min(len(text),remaining)
            capture['messages'] = list(reversed(kept))
            unresolved = str(capture.get('unresolved_text') or '')
            if len(unresolved) > remaining:
                capture['content_truncated'] = True
            capture['unresolved_text'] = unresolved[-remaining:] if remaining else ''
            capture['scope_id'] = f'{self._wechat_visit}:{reading.pid}:{reading.handle}'
            self._recorded += 1
            self.emit(ListenerEvent(event_type='screen.text', source=self.provider_id,
                application=reading.application, data={
                    'application': reading.application, 'window_title': reading.title,
                    'handle': int(reading.handle or 0), 'text': reading.text,
                    'characters': len(reading.text), 'readings': 1, 'over_seconds': 0,
                    'how': reading.how, 'milliseconds': round(reading.milliseconds, 1),
                    'capture_scope': reading.capture_scope, 'wechat_capture': capture},
                metadata={'platform': getattr(self.platform, 'name', '')}))
            return
        # How much of this window was text - the screenshot provider's
        # measure of what reading it missed.
        from .screenshot import note_text
        note_text(reading.application or "", reading.title or "", sum(
            len(line.strip()) for line in (reading.text or "").splitlines()
            if len(line.strip()) >= self.WORTH_A_LINE))
        fresh = self._without_the_furniture(reading, self._what_is_new(reading))
        if not fresh:
            self._unchanged += 1
            # Still the same window: a sitting that has gone quiet but has
            # not ended is reported once it has been held long enough.
            self._report_if_it_is_time(reading)
            return
        self._gathered += 1
        self._gather(reading, fresh)
        if is_wechat_application(reading.application):
            # Short chat visits cannot wait for the ordinary three-minute
            # buffer. Deduplication still happens before immediate emission.
            self.flush_what_is_gathered()
        else:
            self._report_if_it_is_time(reading)

    # -- gathering ---------------------------------------------------------
    def _gather(self, reading: Any, fresh: str) -> None:
        """Add what was just read to the sitting in progress.

        A different window ends the sitting before it starts a new one:
        switching away is the clearest signal that a stretch of reading is
        over, and the record reads as one stretch per window rather than as
        an interleaving of two.
        """
        held = self._gathering
        chat_changed = (held is not None
                        and (is_wechat_application(held["application"])
                             or is_wechat_application(reading.application))
                        and (held["title"], held["how"]) != (reading.title, reading.how))
        if held is not None and (held["handle"] != reading.handle or chat_changed):
            self._report(held)
            held = None
        if held is None:
            held = {"handle": reading.handle,
                    "application": reading.application,
                    "title": reading.title,
                    "lines": [],
                    "since": time.time(),
                    "readings": 0,
                    "milliseconds": 0.0,
                    "how": reading.how}
            held['capture_scope'] = getattr(reading, 'capture_scope', '')
            self._gathering = held
        held["lines"].append(fresh)
        held["readings"] += 1
        held["milliseconds"] += reading.milliseconds
        # Chat title changes separate conversations. Other applications keep
        # upstream's sitting behavior and retain the newest window title.
        held["title"] = reading.title

    def _report_if_it_is_time(self, reading: Any) -> None:
        held = self._gathering
        if held is None:
            return
        gathered = sum(len(line) for line in held["lines"])
        if (gathered >= self.ENOUGH_TO_REPORT
                or time.time() - held["since"] >= self.HOLD_AT_MOST_SECONDS):
            self._report(held)

    def flush_what_is_gathered(self) -> None:
        """End the sitting in progress now, rather than when it would end.

        Reading is continuous, so a sitting normally ends when the user
        switches windows, when enough has been gathered, or when it has been
        held too long. Shutting down is a fourth reason, and a test asking
        what one reading produced is a fifth.
        """
        held = self._gathering
        if held is not None:
            self._report(held)

    def _report(self, held: dict) -> None:
        """Send one sitting, and forget it."""
        if self._gathering is held:
            self._gathering = None
        body = self._trimmed("\n".join(held["lines"]))
        if not body.strip():
            return
        self._recorded += 1
        self.emit(ListenerEvent(
            event_type="screen.text",
            source=self.provider_id,
            application=held["application"] or None,
            data={
                "application": held["application"],
                "window_title": held["title"],
                # Which window it was: the same while it is open, whatever
                # its title says (a terminal's changes with its work).
                "handle": int(held.get("handle") or 0),
                "text": body,
                "characters": len(body),
                "readings": held["readings"],
                "over_seconds": round(max(0.0, time.time() - held["since"]), 1),
                "how": held["how"],
                "milliseconds": round(held["milliseconds"], 1),
                "capture_scope": held.get('capture_scope', ''),
            },
            metadata={"platform": getattr(self.platform, "name", "")},
        ))

    def _read_the_front(self) -> Any:
        activity = getattr(self.platform, "activity", None)
        guarded = getattr(activity, "text_in_front_allowed", None)
        if guarded is not None:
            return guarded(self._allowed_front)
        read = getattr(activity, "text_in_front", None)
        if read is None:
            return None
        return read()

    def _allowed_front(self, front: Any) -> bool:
        from ..core.privacy import PrivacyFilter
        privacy = PrivacyFilter(self.config)
        return (privacy.category_enabled("screen")
                and privacy.application_allowed(front.application)
                and not self._excluded(front.application, front.title)
                and not self._is_the_agent_itself(front))

    def _somebody_is_here(self) -> bool:
        """The same question the filesystem provider asks, for the same reason.

        A machine left alone still has a window in front of it, and reading
        the same idle screen every five seconds all night is not a record of
        anybody's day.
        """
        activity = getattr(self.platform, "activity", None)
        ask = getattr(activity, "seconds_since_input", None)
        if ask is None:
            return True
        try:
            idle = ask()
        except Exception:
            return True
        if idle is None:
            return True
        try:
            window = float(self.config.get("listener_presence_seconds", 300))
        except (TypeError, ValueError):
            window = 300.0
        return idle <= window

    # -- what is worth keeping ---------------------------------------------
    def _is_the_agent_itself(self, reading: Any) -> bool:
        """The agent's own window, which is the agent looking at itself.

        Section 19 already says the agent does not hear its own writing, for
        a reason that applies here twice over: a record of the user's day
        that is mostly the agent's own interface is a record of nothing, and
        an agent reading back its own summaries would compound them.

        Two faces to recognise. Its own desktop window is the certain case -
        the process is this one, whatever the window has been titled, and a
        character with a name the user chose cannot be matched any other way.
        Its interface in a browser is a tab like any other, and the name in
        the title bar is what there is to go on.
        """
        if reading.pid and reading.pid == os.getpid():
            return True
        said = f"{reading.application} {reading.title}".lower()
        return any(name.lower() in said for name in (APP_NAME, *FORMER_NAMES))

    def _excluded(self, application: str, title: str) -> bool:
        """Windows the user has said not to read.

        The list matters more here than anywhere else in the Listener: a
        password manager, a banking page or a private conversation is exactly
        the thing that must never be written down, and unlike a filename its
        contents cannot be un-seen once they are in the record.
        """
        haystack = f"{application} {title}".lower()
        for pattern in self.config.lines("listener_screen_excluded_applications"):
            wanted = pattern.strip().lower()
            if wanted and wanted in haystack:
                return True
        return False

    def _what_is_new(self, reading: Any) -> str:
        """The lines that were not there last time this window was read.

        Without this the same page read every five seconds is the same
        thousand words written down every five seconds. Measured on one
        machine, a browser showing one tab returns 1,617 characters and
        almost all of it is identical from one reading to the next; what
        changed is what happened.
        """
        lines = [line.strip() for line in (reading.text or "").splitlines()]
        lines = [line for line in lines if len(line) >= self.WORTH_A_LINE]
        if not lines:
            return ""
        now = time.time()
        known = self._seen.get(reading.handle)
        if known is not None and now - known[0] > self.SAME_SITTING_SECONDS:
            known = None
        already = known[1] if known else set()
        fresh = [line for line in lines if line not in already]
        remembered = (already | set(lines)) if known else set(lines)
        self._seen[reading.handle] = (now, remembered)
        self._seen.move_to_end(reading.handle)
        while len(self._seen) > self.REMEMBERED:
            self._seen.popitem(last=False)
        return "\n".join(fresh)

    #: On how many different pages of one application a line has to appear
    #: before it is taken for part of the application rather than the page.
    FURNITURE_AFTER = 3
    #: How many lines each application's memory holds.
    FURNITURE_REMEMBERED = 4000
    #: How many pages' recorded lines are remembered, most recent kept.
    PAGES_REMEMBERED = 600

    def _without_the_furniture(self, reading: Any, fresh: str) -> str:
        """What was new on the page, without what is on every page.

        The 4,000 characters kept from a sitting were partly spent on the
        application rather than the page: measured on one machine, a fifth
        of everything read in a browser was lines repeated from page to page
        - the browser's own right-click menu ("Inspect", "Select All", "Take
        Screenshot"), its toolbar ("Back", "Forward", "Reload"), the
        extensions, a site's navigation.

        The rule needs no list of such words. Within one window only lines
        not seen there before are kept already, so a conversation's earlier
        messages are never written twice; what survives that and still
        repeats is what turns up on DIFFERENT pages. A paragraph, a message,
        an email lives on one page. A line found on three different pages of
        the same application belongs to the application, and from then on
        it is not spent on.
        """
        lines = [line.strip() for line in (reading.text or "").splitlines()
                 if len(line.strip()) >= self.WORTH_A_LINE]
        application = str(reading.application or "")
        page = f"{reading.handle}:{reading.title or ''}"
        shown = self._shown_on.setdefault(application, collections.OrderedDict())
        for line in set(lines):
            pages = shown.get(line)
            if pages is None:
                pages = set()
                shown[line] = pages
            else:
                shown.move_to_end(line)
            if len(pages) < self.FURNITURE_AFTER:
                pages.add(page)
        while len(shown) > self.FURNITURE_REMEMBERED:
            shown.popitem(last=False)
        if not fresh:
            return fresh
        # AND NOT WHAT THIS PAGE HAS ALREADY HAD RECORDED. Lines seen in a
        # window are remembered only for one sitting, so coming back to a
        # page later wrote down everything still on it a second time:
        # measured, fourteen per cent of all that was read, and half of a
        # WeChat chat - every message still on screen, again, each time the
        # chat was opened. Remembered by page rather than by window, so a
        # browser tab's text is not confused with the next tab's.
        where = f"{application}\n{reading.title or ''}"
        recorded = self._kept_on.get(where)
        if recorded is None:
            recorded = set()
            self._kept_on[where] = recorded
        else:
            self._kept_on.move_to_end(where)
        while len(self._kept_on) > self.PAGES_REMEMBERED:
            self._kept_on.popitem(last=False)
        kept = []
        for line in fresh.split("\n"):
            bare = line.strip()
            if len(shown.get(bare, ())) >= self.FURNITURE_AFTER:
                self._furniture_dropped += 1
                continue
            mark = hash(bare)
            if bare and mark in recorded:
                self._repeats_dropped += 1
                continue
            if bare:
                recorded.add(mark)
            kept.append(line)
        return "\n".join(kept)

    def _trimmed(self, body: str) -> str:
        try:
            most = int(self.config.get("listener_screen_max_chars", 4000))
        except (TypeError, ValueError):
            most = 4000
        most = max(200, most)
        if len(body) <= most:
            return body
        # The end rather than the beginning: what a window shows last is what
        # the user has just got to.
        return body[-most:]

    # -- interface ----------------------------------------------------------
    def _can_read(self) -> bool:
        capabilities = self.platform.capabilities()
        return bool(getattr(capabilities, "extra", {}).get("window_text")
                    or getattr(capabilities, "window_text", False))

    def _why_not(self) -> str:
        activity = getattr(self.platform, "activity", None)
        return str(getattr(activity, "window_text_error", "") or "")

    def get_status(self) -> Dict[str, Any]:
        running = bool(self._thread and self._thread.is_alive())
        average = (self._milliseconds / self._readings) if self._readings else 0.0
        return {
            "running": running,
            "enabled": bool(self.config.get("listener_screen_enabled", True)),
            "readings": self._readings,
            "recorded": self._recorded,
            "unchanged": self._unchanged,
            "gathered": self._gathered,
            "gathering_now": bool(self._gathering),
            "skipped": self._skipped,
            "our_own_windows": self._ourselves,
            "windows_remembered": len(self._seen),
            "furniture_lines_dropped": self._furniture_dropped,
            "repeated_lines_dropped": self._repeats_dropped,
            "average_milliseconds": round(average, 1),
            "effective_interval_seconds": round(self._interval(), 1),
            # FAST SAMPLING: whether the window in front is one read often,
            # and how it is paced.
            "fast": dict(self._pace.status(), in_front=self._was_fast,
                         typing=self._typing),
            "wechat_immediate_write": True,
            "focus_check_seconds": self.FOCUS_CHECK_SECONDS,
            "last_error": self._why_not() or self.last_error,
            "reader": (self.platform.activity.window_text_status()
                       if hasattr(self.platform.activity, "window_text_status") else {}),
        }

    def supported_event_types(self) -> List[str]:
        return ["screen.text"]

    def get_capabilities(self) -> Dict[str, Any]:
        return {"realtime": False, "reads_window_contents": True,
                "skips_password_fields": True}

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            plugin_id=self.provider_id,
            version="1.0.0",
            supported_platforms=["windows", "macos"],
            supported_event_types=self.supported_event_types(),
            required_permissions=["accessibility"],
            configuration_schema={
                "listener_screen_enabled": "bool",
                "listener_screen_interval_seconds": "float",
                "listener_screen_max_chars": "int",
                "listener_screen_excluded_applications": "multiline",
            },
            description="Reads the text of the window in front, when asked to.",
        )
