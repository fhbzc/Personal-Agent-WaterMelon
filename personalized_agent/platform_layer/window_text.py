"""Reading the text out of the window in front, on Windows (section 19.2).

Every accessible application publishes what it is showing, so that a screen
reader can say it. The same tree answers a question the agent could not
answer any other way: not *that* a window was in front, but *what was in it*.

Two routes, and which one is used matters more than it sounds. Asking a
document-shaped element for its text is one call; walking every descendant
and asking each for its name is one call per element, and the cost follows
the count rather than the amount of text. Measured on one machine:

- a terminal showing 19,000 characters has **22 elements** and answers in
  **32 ms**
- a browser showing a busy page has **8,880 elements** and takes **1,827 ms**
  walked, or **294 ms** asked for its documents

So documents are asked first and the walk is the fallback, capped. A file
manager is the case that needs the walk: its file list is a hundred names and
no text at all.

A WINDOW HOLDING A LONG LIST OF THE OLD KIND IS READ WITHIN BOUNDS (the user,
2026-10-30). A Win32 list - a file list, a list box, a tree - has no
answers of its own: Windows asks it item by item, each question answered by
the application's own interface thread. Bitvise's file browser, a list of
794 files, was asked with FindAll and never answered: stopped after 45
seconds, its window stood still for a whole minute (measured). So before a
window is read its Win32 lists are counted, one message each
(`has_a_long_list`); one of more than LONG_LIST items and the window is read
by one walk, in the order it shows things, of at most WALK_AT_MOST elements
for at most READ_SECONDS, each with what is asked of it fetched in the same
call (a cache request), never going into a part not on the screen
(`_ask_bounded`). Every other window is read as before: that walk, tried on
all of them, found a fraction of what a page, Explorer or WeChat showed -
each step a question of its own, where FindAll is answered in one.

A PROGRAM SEEN STANDING STILL WHILE IT IS READ IS READ WITHIN BOUNDS FROM
THEN ON (the user, 2026-11-01: Bitvise still stood still now and then).
Bitvise's file browser with lists of 73 and 10 items - no long list - still
took 14 to 22 seconds to read, each pass over its 625 elements holding its
window still for two to nearly four seconds (measured). So while a window is
read the ordinary way it is watched (`_Watch`): its thread is asked to
answer (WM_NULL, which does nothing) every 50 ms from a thread of its own.
One that does not answer within STILL_SECONDS has the rest of that reading
done within bounds, and a program seen so in STILL_READINGS readings in a
row is read within bounds for STILL_DAYS from then on, remembered in the
agent's own folder (`remember_in`) - in a row, and for a while, so that a
browser busy with a heavy page now and then is never taken for one: one
ordinary reading between and the count starts again, and a program taken
wrongly is asked again within a week (the user, 2026-11-01). A window
already not answering before it is read is busy of its own accord: read
within bounds that once, and not counted. Every other program is read exactly as before.

Nothing here decides what is worth recording or keeps anything. It reads, and
hands back what it read.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

log = logging.getLogger(__name__)


def is_wechat_application(application: str) -> bool:
    return (application or '').strip().lower() in {
        'wechat', 'weixin', '微信', 'wechat.exe', 'weixin.exe'}




@dataclass
class WindowText:
    """What one window was showing, and what it cost to find out."""

    application: str = ""
    title: str = ""
    text: str = ""
    how: str = ""
    elements: int = 0
    milliseconds: float = 0.0
    handle: int = 0
    #: Which process owns the window. The one reliable way to recognise the
    #: agent's own window, whatever it has been titled.
    pid: int = 0
    capture_scope: str = ""
    wechat_capture: dict = field(default_factory=dict)
    #: WECHAT'S WORDS TO COME (listener/providers/deferred_ocr.py): set on a
    #: reading whose picture was taken and whose words are recognised later -
    #: `finish()` gives the reading with them, or None. And when the picture
    #: was taken, which is the reading's moment.
    finish: Any = None
    captured_at: str = ""

    def to_dict(self) -> dict:
        return {"application": self.application, "title": self.title,
                "text": self.text, "how": self.how, "elements": self.elements,
                "milliseconds": round(self.milliseconds, 1)}


#: Control types worth asking for text, cheapest and most likely first.
#: Numbers rather than names because they are stable parts of the API and
#: the constants module is generated at runtime.
_DOCUMENT = 50030
_EDIT = 50004
_TEXT = 50020

#: Scope for FindAll. Descendants is 4 - **not** 2, which is Children and
#: which silently returns a handful of shell elements and no content at all.
_DESCENDANTS = 4

_CHILDREN = 2
_LIST_ITEM = 50007

_CONTROL_TYPE_PROPERTY = 30003
_AUTOMATION_ID_PROPERTY = 30011
_IS_PASSWORD_PROPERTY = 30019

#: Whether the element is in the tree but not being shown.
#:
#: A browser keeps every open tab in the tree, so asking a window for its
#: documents returns all of them. Measured on one machine: twelve tabs and
#: 64,000 characters, of which the user was looking at one - the other eleven
#: included a mail inbox that happened to be open behind it. Reading those is
#: not what was asked for and not what anybody would expect.
_IS_OFFSCREEN_PROPERTY = 30022

#: What each element visited is asked, fetched with it in one call: its
#: name, whether it has text or a value to give, and the value.
_NAME_PROPERTY = 30005
_HAS_TEXT_PATTERN = 30040
_HAS_VALUE_PATTERN = 30043
_VALUE_PROPERTY = 30045
_CACHED = (_CONTROL_TYPE_PROPERTY, _NAME_PROPERTY, _IS_OFFSCREEN_PROPERTY,
           _IS_PASSWORD_PROPERTY, _HAS_TEXT_PATTERN, _HAS_VALUE_PATTERN, _VALUE_PROPERTY)

#: Stands in for an image or another embedded object. A busy page returns
#: these by the hundred and not one of them is text.
_PLACEHOLDER = "\ufffc"

#: How long a window may take to answer while it is read (module docstring);
#: in how many readings IN A ROW a program must have been seen standing still
#: before it is read within bounds - two, so that a page busy once with its
#: own work does not lose its reading - and for how many days after.
STILL_SECONDS = 0.3
STILL_READINGS = 2
STILL_DAYS = 7


class _Watch:
    """Whether a window stands still while it is read: its thread asked to
    answer (WM_NULL) every EVERY seconds, from a thread of its own, for as
    long as the reading goes on."""

    EVERY = 0.05

    def __init__(self, handle: int) -> None:
        self.handle = int(handle)
        self.still = threading.Event()
        self._done = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def answers(self) -> bool:
        """Whether the window answers within STILL_SECONDS now."""
        if sys.platform != "win32":
            return True
        result = ctypes.c_size_t()
        began = time.perf_counter()
        # SMTO_ABORTIFHUNG: a window Windows already takes as hung is not waited on.
        said = ctypes.windll.user32.SendMessageTimeoutW(
            ctypes.c_void_p(self.handle), 0, 0, 0, 0x0002, int(STILL_SECONDS * 1000),
            ctypes.byref(result))
        return bool(said) and time.perf_counter() - began <= STILL_SECONDS

    def __enter__(self) -> "_Watch":
        self._thread = threading.Thread(target=self._loop, name="window-watch", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._done.set()

    def _loop(self) -> None:
        while not self._done.is_set():
            if not self.answers():
                if not self._done.is_set():
                    self.still.set()
                return
            self._done.wait(self.EVERY)


class WindowTextReader:
    """A UI Automation client, kept alive between readings.

    Created on its own thread in the multi-threaded apartment. Both halves of
    that matter: COM decides a thread's apartment the first time anything
    initialises it, and a cross-process UI Automation call from a
    single-threaded apartment with no message pump returns an empty element
    rather than failing - which looks exactly like an application that
    exposes nothing.
    """

    #: How many elements a reading visits, whatever the window holds. A page
    #: with nine thousand is not going to be read this way whatever the
    #: limit is; the cap is there so that meeting one costs a fixed amount -
    #: and VISITED, not only read: a FindAll visited them all (module
    #: docstring).
    WALK_AT_MOST = 400

    #: And for how long, at most: an application slow to answer is asked
    #: no more once this has passed - what was found by then is the reading.
    READ_SECONDS = 0.5

    #: Enough text to have found the content, past which the cheaper route
    #: has clearly worked and the walk is not worth trying.
    ENOUGH = 200

    def __init__(self) -> None:
        self.last_error: str = ""
        self._automation: Any = None
        self._uia: Any = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._started = False
        self._cache: Any = None
        #: Programs seen standing still while read (module docstring): how
        #: many readings saw it, and those read within bounds from then on.
        self._seen_still: Dict[str, int] = {}
        #: Each with when it was seen so - over STILL_DAYS ago and it is
        #: asked again, the agent running all that time or not.
        self._still_since: Dict[str, datetime] = {}
        self._remembered_in: Optional[Path] = None

    @property
    def stands_still(self) -> Set[str]:
        """The programs read within bounds now: seen standing still in the
        last STILL_DAYS."""
        since = datetime.now(timezone.utc) - timedelta(days=STILL_DAYS)
        return {program for program, when in self._still_since.items() if when >= since}

    # -- programs that stand still while read ---------------------------------
    def remember_in(self, path: Path) -> None:
        """Keep the programs that stand still while read in `path` - the
        agent's own folder - and take back those kept there before."""
        import json

        self._remembered_in = Path(path)
        try:
            kept = json.loads(self._remembered_in.read_text(encoding="utf-8"))
            for program, when in dict(kept.get("programs") or {}).items():
                try:
                    seen = datetime.fromisoformat(str(when))
                except (TypeError, ValueError):
                    continue
                if seen.tzinfo is None:
                    seen = seen.replace(tzinfo=timezone.utc)
                self._still_since[str(program).lower()] = seen
        except (OSError, ValueError, AttributeError, TypeError):
            pass

    def _answered(self, program: str) -> None:
        """A reading of `program` in which its window answered throughout:
        the count of readings in a row that saw it stand still starts again."""
        self._seen_still.pop((program or "").lower(), None)

    def _stood_still(self, program: str) -> None:
        """A reading of `program` saw its window stand still."""
        program = (program or "").lower()
        if not program or program in self.stands_still:
            return
        self._seen_still[program] = self._seen_still.get(program, 0) + 1
        if self._seen_still[program] < STILL_READINGS:
            return
        now = datetime.now(timezone.utc)
        self._still_since[program] = now
        self._seen_still.pop(program, None)
        log.info("%s stands still while its windows are read: read within bounds for %d days",
                 program, STILL_DAYS)
        if self._remembered_in is None:
            return
        import json

        try:
            try:
                kept = json.loads(self._remembered_in.read_text(encoding="utf-8"))
                programs = dict(kept.get("programs") or {})
            except (OSError, ValueError, AttributeError):
                programs = {}
            programs[program] = now.isoformat(timespec="seconds")
            self._remembered_in.parent.mkdir(parents=True, exist_ok=True)
            self._remembered_in.write_text(json.dumps({"programs": programs}, indent=1),
                                           encoding="utf-8")
        except OSError:                              # pragma: no cover - kept for this run
            log.debug("could not keep the programs that stand still", exc_info=True)

    # -- availability -------------------------------------------------------
    @property
    def available(self) -> bool:
        if sys.platform != "win32":
            return False
        self._start()
        self._ready.wait(timeout=10)
        return self._automation is not None

    def _start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._be_a_client, name="uia-client",
                         daemon=True).start()

    def _be_a_client(self) -> None:
        """Hold the client open on this thread for as long as the agent runs.

        Creating one per reading would mean initialising COM, loading the
        type library and building a proxy every few seconds, and would lose
        the cache the automation object keeps about the applications it has
        already talked to.
        """
        try:
            # Chosen before comtypes is imported anywhere on this thread: it
            # initialises COM on import and reads this to pick the apartment.
            sys.coinit_flags = 0                  # COINIT_MULTITHREADED
            import comtypes.client

            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as uia

            self._uia = uia
            self._automation = comtypes.client.CreateObject(
                uia.CUIAutomation, interface=uia.IUIAutomation)
        except Exception as exc:
            self.last_error = f"UI Automation is unavailable: {exc}"[:200]
            log.info("window text reading is not available: %s", exc)
        finally:
            self._ready.set()
        # The object lives as long as this thread's apartment does.
        while True:
            time.sleep(3600)

    # -- reading ------------------------------------------------------------
    def read(self, handle: int, program: str = "") -> Optional[WindowText]:
        """Everything that window will say about what it is showing.
        `program` - its executable's name - is what is remembered when it
        stands still while read (module docstring)."""
        if not self.available or not handle:
            return None
        began = time.perf_counter()
        got = WindowText(handle=int(handle))
        try:
            element = self._automation.ElementFromHandle(handle)
        except Exception as exc:
            self.last_error = str(exc)[:200]
            return None
        # A window holding a long list of the old kind, or of a program seen
        # standing still while read, is walked within bounds; any other is
        # asked as it always was, watched (module docstring).
        if (program or "").lower() in self.stands_still or has_a_long_list(handle):
            body, how, elements = self._ask_bounded(element)
        else:
            watch = _Watch(handle)
            if not watch.answers():
                body, how, elements = self._ask_bounded(element)   # busy of its own accord
            else:
                with watch:
                    body, how, elements = self._ask_the_window(element, watch.still.is_set)
                if watch.still.is_set():
                    self._stood_still(program)
                else:
                    self._answered(program)
        got.text, got.how, got.elements = body, how, elements
        got.milliseconds = (time.perf_counter() - began) * 1000
        return got

    def wechat_frame(self, handle: int) -> Optional[tuple]:
        """WeChat's conversation in front, message by message: the window's
        picture, then its message list's items and the conversation's name
        from Automation (`wechat_items.py`). None when the window has no
        message list - an older WeChat, or a page that is not a chat - and
        the window is read as any other.

        Asked in physical pixels, the picture's: this thread is made DPI
        aware for the asking, and put back after (measured 2026-10-26: not
        aware, Automation answered in pixels scaled by 1.75).
        """
        if not self.available or not handle:
            return None
        user32 = ctypes.windll.user32
        try:
            before = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            before = None
        try:
            return self._wechat_frame(handle)
        except Exception as exc:
            self.last_error = f"WeChat's messages could not be read: {exc}"[:200]
            return None
        finally:
            if before:
                user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(before))

    def _wechat_frame(self, handle: int) -> Optional[tuple]:
        from ctypes import wintypes
        from PIL import ImageGrab

        from .wechat_items import parse_wechat_items

        frame = wintypes.RECT()
        if ctypes.windll.dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(handle), 9, ctypes.byref(frame), ctypes.sizeof(frame)) != 0:
            return None
        origin = (frame.left, frame.top)
        # The picture first: what Automation then says is checked against it.
        picture = ImageGrab.grab(bbox=(frame.left, frame.top, frame.right, frame.bottom),
                                 all_screens=True)
        element = self._automation.ElementFromHandle(handle)

        def box(found: Any) -> tuple:
            r = found.CurrentBoundingRectangle
            return (r.left - origin[0], r.top - origin[1], r.right - r.left, r.bottom - r.top)

        chat = element.FindFirst(_DESCENDANTS, self._automation.CreatePropertyCondition(
            _AUTOMATION_ID_PROPERTY, "chat_message_list"))
        if not chat:
            return None
        items = []
        listed = chat.FindAll(_CHILDREN, self._automation.CreatePropertyCondition(
            _CONTROL_TYPE_PROPERTY, _LIST_ITEM))
        for index in range(min(listed.Length, 200)):
            item = listed.GetElement(index)
            if self._is_hidden(item) or self._is_a_password(item):
                continue
            items.append({"text": item.CurrentName or "", "box": box(item)})
        headers = []
        texts = element.FindAll(_DESCENDANTS, self._automation.CreatePropertyCondition(
            _CONTROL_TYPE_PROPERTY, _TEXT))
        for index in range(min(texts.Length, 40)):
            label = texts.GetElement(index)
            # Its id is a path ending in this; the input box is never asked.
            if (label.CurrentAutomationId or "").endswith("current_chat_name_label"):
                headers.append({"text": label.CurrentName or "", "box": box(label)})
        capture = parse_wechat_items(picture, box(chat), items, headers)
        text = "\n".join(item["text"].strip() for item in items if item["text"].strip())
        return text, capture, len(items)

    def _ask_the_window(self, element: Any,
                        still: Callable[[], bool] = lambda: False) -> tuple:
        """The window asked the ordinary way; once `still` says it has stood
        still, the rest within bounds (module docstring)."""
        body = self._text_of(element)
        if len(body.strip()) >= self.ENOUGH:
            return body, "window", 1
        for control, label in ((_DOCUMENT, "document"), (_EDIT, "edit"),
                               (_TEXT, "text")):
            if still():
                return self._rest_bounded(element, body)
            found, count = self._of_type(element, control, still)
            if len(found.strip()) > len(body.strip()):
                body, how, elements = found, f"{label}", count
                if len(body.strip()) >= self.ENOUGH:
                    return body, how, elements
        if len(body.strip()) >= self.ENOUGH:
            return body, "pattern", 1
        if still():
            return self._rest_bounded(element, body)
        walked, count = self._walk(element, still)
        if len(walked.strip()) > len(body.strip()):
            return walked, "walk", count
        return body, "pattern" if body.strip() else "nothing", 1

    def _rest_bounded(self, element: Any, body: str) -> tuple:
        """The rest of a reading within bounds - what was found before, if it is more."""
        bounded = self._ask_bounded(element)
        if len(bounded[0].strip()) >= len(body.strip()):
            return bounded
        return body, "pattern" if body.strip() else "nothing", 1

    def _of_type(self, element: Any, control: int,
                 still: Callable[[], bool] = lambda: False) -> tuple:
        """The text of every document-shaped element inside this window."""
        try:
            condition = self._automation.CreatePropertyCondition(
                _CONTROL_TYPE_PROPERTY, control)
            found = element.FindAll(_DESCENDANTS, condition)
        except Exception:
            return "", 0
        pieces: List[str] = []
        for index in range(min(found.Length, 40)):
            if still():
                break
            try:
                child = found.GetElement(index)
                if self._is_a_password(child) or self._is_hidden(child):
                    continue
                pieces.append(self._text_of(child))
            except Exception:
                continue
        return self._tidied(pieces), found.Length

    def _walk(self, element: Any, still: Callable[[], bool] = lambda: False) -> tuple:
        """Every descendant's name, for windows that expose no text at all.

        A file manager is the case: its list of files is a hundred names and
        not one text pattern between them.
        """
        try:
            found = element.FindAll(_DESCENDANTS,
                                    self._automation.CreateTrueCondition())
        except Exception:
            return "", 0
        pieces: List[str] = []
        for index in range(min(found.Length, self.WALK_AT_MOST)):
            if still():
                break
            try:
                child = found.GetElement(index)
                if self._is_a_password(child) or self._is_hidden(child):
                    continue
                pieces.append(self._text_of(child) or (child.CurrentName or ""))
            except Exception:
                continue
        return self._tidied(pieces), found.Length

    def _ask_bounded(self, element: Any) -> tuple:
        """The window's own text; else, from one bounded walk (`_visit`),
        its documents, edits or texts - the first kind with enough - else
        every name the walk met. A file manager is the case for the names:
        its list of files is a hundred names and not one text pattern
        between them."""
        body = self._text_of(element)
        if len(body.strip()) >= self.ENOUGH:
            return body, "window", 1
        visited = self._visit(element)
        shown = [one for one in visited
                 if not self._cached(one, _IS_PASSWORD_PROPERTY)
                 and not self._cached(one, _IS_OFFSCREEN_PROPERTY)]
        for control, label in ((_DOCUMENT, "document"), (_EDIT, "edit"),
                               (_TEXT, "text")):
            of_type = [one for one in shown
                       if self._cached(one, _CONTROL_TYPE_PROPERTY) == control][:40]
            found = self._tidied([self._cached_text(one) for one in of_type])
            if len(found.strip()) > len(body.strip()):
                body = found
                if len(body.strip()) >= self.ENOUGH:
                    return body, label, len(of_type)
        walked = self._tidied([self._cached_text(one)
                               or str(self._cached(one, _NAME_PROPERTY) or "")
                               for one in shown])
        if len(walked.strip()) > len(body.strip()):
            return walked, "walk", len(visited)
        return body, "pattern" if body.strip() else "nothing", 1

    def _visit(self, root: Any) -> List[Any]:
        """The elements under `root`, in the order the window shows them -
        AT MOST WALK_AT_MOST, for at most READ_SECONDS - each fetched with
        what is asked of it (`_CACHED`). A part not on the screen is not
        gone into: a tab behind the one shown, rows scrolled away."""
        try:
            if self._cache is None:
                cache = self._automation.CreateCacheRequest()
                for one in _CACHED:
                    cache.AddProperty(one)
                self._cache = cache
            walker = self._automation.ControlViewWalker
            first = walker.GetFirstChildElementBuildCache(root, self._cache)
        except Exception:
            return []
        deadline = time.monotonic() + self.READ_SECONDS
        visited: List[Any] = []
        waiting = [first] if first else []
        while waiting and len(visited) < self.WALK_AT_MOST and time.monotonic() < deadline:
            element = waiting.pop()
            visited.append(element)
            try:
                after = walker.GetNextSiblingElementBuildCache(element, self._cache)
            except Exception:
                after = None
            if after:
                waiting.append(after)
            if self._cached(element, _IS_OFFSCREEN_PROPERTY):
                continue
            try:
                inside = walker.GetFirstChildElementBuildCache(element, self._cache)
            except Exception:
                inside = None
            if inside:
                waiting.append(inside)          # taken first: what is inside comes next
        return visited

    @staticmethod
    def _cached(element: Any, which: int) -> Any:
        """A property fetched with the element (`_CACHED`); None if not."""
        try:
            return element.GetCachedPropertyValue(which)
        except Exception:
            return None

    def _cached_text(self, element: Any) -> str:
        """Its text - asked only of an element that has a text to give - or
        its value, fetched with it."""
        if self._cached(element, _HAS_TEXT_PATTERN):
            said = self._text_of(element)
            if said:
                return said
        if self._cached(element, _HAS_VALUE_PATTERN):
            return str(self._cached(element, _VALUE_PROPERTY) or "")
        return ""

    def _text_of(self, element: Any) -> str:
        for pattern_id, interface in (
                (10014, "IUIAutomationTextPattern"),
                (10002, "IUIAutomationValuePattern")):
            try:
                pattern = element.GetCurrentPattern(pattern_id)
                if not pattern:
                    continue
                typed = pattern.QueryInterface(getattr(self._uia, interface))
                if pattern_id == 10014:
                    return typed.DocumentRange.GetText(-1) or ""
                return typed.CurrentValue or ""
            except Exception:
                continue
        return ""

    @staticmethod
    def _tidied(pieces: List[str]) -> str:
        """The same text without the parts that are not text.

        Placeholders standing in for images, and lines repeated exactly - a
        list control commonly reports one label once per column.
        """
        kept: List[str] = []
        seen = set()
        for piece in pieces:
            line = (piece or "").replace(_PLACEHOLDER, " ").strip()
            if not line or line in seen:
                continue
            seen.add(line)
            kept.append(line)
        return "\n".join(kept)

    @staticmethod
    def _is_hidden(element: Any) -> bool:
        """In the tree, but not on the screen.

        A browser tab open behind the one being read is the case this exists
        for, and it is the difference between recording what the user is
        looking at and recording everything they have open.
        """
        try:
            return bool(element.GetCurrentPropertyValue(_IS_OFFSCREEN_PROPERTY))
        except Exception:
            return False

    @staticmethod
    def _is_a_password(element: Any) -> bool:
        """A password box says so, and is never read.

        The one piece of the tree that must not be recorded even when
        everything else is, and the only one the platform labels for us.
        """
        try:
            return bool(element.GetCurrentPropertyValue(_IS_PASSWORD_PROPERTY))
        except Exception:
            return False


#: Win32 lists, and the message that asks each how many items it holds.
_LIST_COUNTS = (("SysListView32", 0x1004),      # LVM_GETITEMCOUNT
                ("SysTreeView32", 0x1105),      # TVM_GETCOUNT
                ("ListBox", 0x018B),            # LB_GETCOUNT
                ("ComboLBox", 0x018B))
#: More items than this in one of them, and the window is read within bounds.
LONG_LIST = 300


def has_a_long_list(handle: int) -> bool:
    """Whether the window holds a Win32 list of more than LONG_LIST items
    (module docstring) - each list asked once, and one that does not answer
    at once taken as long."""
    if sys.platform != "win32" or not handle:
        return False
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = [False]

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(child, _param):
        name = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(child, name, 128)
        for kind, message in _LIST_COUNTS:
            if kind in name.value and user32.IsWindowVisible(child):
                count = ctypes.c_size_t()
                # SMTO_ABORTIFHUNG | SMTO_BLOCK, a fifth of a second at most.
                # A list that does not answer one question in that time is
                # taken as long: asked item by item it would be worse
                # (measured on Bitvise's file browser, busy).
                if not user32.SendMessageTimeoutW(child, message, 0, 0, 0x0003, 200,
                                                  ctypes.byref(count)) \
                        or int(count.value) > LONG_LIST:
                    found[0] = True
                    return False
        return True
    try:
        user32.EnumChildWindows(wintypes.HWND(handle), each, 0)
    except Exception:                                # pragma: no cover
        return False
    return found[0]


# ---------------------------------------------------------------------------
# The window in front
# ---------------------------------------------------------------------------
def front_window() -> tuple:
    """Handle, title and process id of whatever is in front."""
    if sys.platform != "win32":
        return 0, "", 0
    user32 = ctypes.windll.user32
    handle = user32.GetForegroundWindow()
    if not handle:
        return 0, "", 0
    length = user32.GetWindowTextLengthW(handle)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(handle, buffer, length + 1)
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    return int(handle), buffer.value, int(pid.value)


_reader: Optional[WindowTextReader] = None
_reader_lock = threading.Lock()


def reader() -> WindowTextReader:
    """The one client, shared. Building one per reading loses its cache."""
    global _reader
    with _reader_lock:
        if _reader is None:
            _reader = WindowTextReader()
    return _reader


def text_in_front() -> Optional[WindowText]:
    """What the window in front is showing, or nothing if it will not say."""
    handle, title, pid = front_window()
    if not handle:
        return None
    try:
        import psutil

        application = psutil.Process(pid).name()
    except Exception:
        application = ""
    got = None
    if is_wechat_application(application):
        # WeChat: its messages one by one, whose each is (wechat_items.py).
        began = time.perf_counter()
        found = reader().wechat_frame(handle)
        if found is not None:
            text, capture, elements = found
            got = WindowText(handle=int(handle), text=text, how="windows_wechat_messages",
                             elements=elements, capture_scope="visible_text_only",
                             wechat_capture=capture,
                             milliseconds=(time.perf_counter() - began) * 1000)
    if got is None:
        got = reader().read(handle, application)
    if got is None:
        return None
    got.title = title
    got.pid = pid
    got.application = application
    return got


__all__ = ["WindowText", "WindowTextReader", "front_window", "text_in_front",
           "reader"]
