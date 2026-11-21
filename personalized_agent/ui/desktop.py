"""Desktop agent window (sections 5-7).

A frameless always-on-top character window:

* single click  -> conversation interface (section 7.1)
* double click  -> Overview interface (section 7.2)
* right click   -> States menu: Settings, Graph, Debugger (section 7.3)
* the agent may raise something itself when it is worth saying (section 7.4).
  It does so in a bubble beside the character, in a warmer colour than an
  ordinary one, and never by opening a window over what the user is doing.
  The user can switch the whole thing off in Settings.

Bubbles arrive one at a time and stay until they are answered. Each carries
the buttons of the question it belongs to, or a single acknowledgement when
there is no question behind it.

Tk must own the main thread, so all backend work happens in worker threads and
comes back through a queue drained by the Tk event loop.
"""

from __future__ import annotations

import collections
import json
import logging
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
import urllib.parse
import webbrowser
from typing import Any, Dict, List, Optional, Tuple

from ..common import chooser, interface_words
from . import sizes
from .on_top import on_release

log = logging.getLogger(__name__)

try:
    from PIL import Image, ImageTk

    PIL_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    PIL_AVAILABLE = False

#: The tallest a notification's picture is drawn in the bubble, in pixels.
BUBBLE_PICTURE_HEIGHT = 220

TRANSPARENT = "#ff00ff"

#: The Tk versions on which a Mac's clear window shows the character. On
#: 8.6.15 it was seen whole and pressed; on 8.6.16 (the Python 3.12.10
#: installer's) a clear window drew its text and not its pictures - the
#: character was not there at all, on every way of making it clear that
#: was tried (2026-10-23). Any other Tk: the plain tile, which is seen.
#: SINCE PR #5 (2026-10-28) only where the native image view
#: (ui/macos_avatar.py) cannot be had: that is seen whole, clear round it,
#: on 8.6.16 too (measured on a Mac Studio, Tk 8.6.16: pressed, hidden and
#: shown, its moods; 10.9% of a core at thirty frames a second, Tk's own
#: drawing 19.8%).
CLEAR_ON_TK = ("8.6.15",)


def _clear_window_shows_pictures(patchlevel: str) -> bool:
    """Whether, on a Mac, a clear window on this Tk shows the character."""
    return str(patchlevel) in CLEAR_ON_TK
STATE_COLORS = {
    "idle": "#5c8ce6",
    "talking": "#5ebe96",
    "thinking": "#a08ce6",
    "notification": "#eba55a",
}


def _mac_screens() -> List[Tuple[int, int, int, int]]:
    """A Mac's screens as they are now, the main one first: (left, top, right, bottom), counted as
    Tk counts. ASKED OF THE SYSTEM, not of Tk (a classmate's Mac, 2026-11-05: hidden on a large
    display, shown on a small one, it was nowhere): Tk knows one screen, and second-hand. Empty
    where it cannot be asked."""
    try:
        import AppKit
        screens = AppKit.NSScreen.screens()
        height = screens[0].frame().size.height
        frames = [one.frame() for one in screens]
        return [(int(one.origin.x), int(height - one.origin.y - one.size.height),
                 int(one.origin.x + one.size.width), int(height - one.origin.y)) for one in frames]
    except Exception:            # pragma: no cover - asked of a Mac
        return []


def _on_a_screen(window: Any, x: int, y: int) -> bool:
    """Whether a point is on a screen that is there now - any of them."""
    if sys.platform == "darwin":
        screens = _mac_screens()
        if screens:
            return any(left <= x < right and top <= y < bottom
                       for left, top, right, bottom in screens)
    if sys.platform.startswith("win"):
        try:
            import ctypes
            from ctypes import wintypes
            point = wintypes.POINT(int(x), int(y))
            # MONITOR_DEFAULTTONULL: no monitor holds the point.
            return bool(ctypes.windll.user32.MonitorFromPoint(point, 0))
        except Exception:        # pragma: no cover - asked of Windows
            pass
    try:
        left, top = window.winfo_vrootx(), window.winfo_vrooty()
        width, height = window.winfo_vrootwidth(), window.winfo_vrootheight()
        if width <= 1 or height <= 1:
            width, height = window.winfo_screenwidth(), window.winfo_screenheight()
        return left <= x < left + width and top <= y < top + height
    except Exception:            # pragma: no cover - no screen to ask
        return True


#: An ordinary bubble: something happened, and the user may read it or not.
#:
#: ``accent`` is the surface a primary button sits on, and ``on_accent`` the
#: colour its text is written in. They are named separately from ``edge``
#: because the quiet bubble deliberately has no visible edge - its edge *is*
#: its background - and a button drawn as "edge behind, background in front"
#: therefore wrote its label in its own background colour. The OK on a passive
#: notice is always the primary button, so it was always the invisible one.
QUIET_BUBBLE = {"bg": "#181819", "fg": "#ffffff", "edge": "#313133",
                "accent": "#313133", "on_accent": "#ffffff"}

#: The agent raising something of its own accord (section 7.4). Warmer, with a
#: visible edge, and it borrows the amber already used for the notification
#: state so the character and the bubble are saying the same thing.
#:
#: This is deliberately the *only* difference between the two. Emphasis is a
#: colour and a few extra seconds - never a window that takes focus, because
#: the agent cannot know that what it has to say is worth the user's place in
#: whatever they were reading.
EMPHATIC_BUBBLE = {"bg": "#2b2519", "fg": "#f4e6cf", "edge": "#eba55a",
                   "accent": "#eba55a", "on_accent": "#2b2519"}

#: HOW A BUBBLE LOOKS (the user, 2026-11-01: "nicer, today's look"): a dark
#: card with a thin outline, rounded where Windows rounds windows; a line at
#: its top saying what it is, and how many wait behind it; its title in bold
#: over its words; its buttons flat and rounded - the one to press filled -
#: and "a bad one" a quiet link. The agent's own word keeps its warm amber.
BUBBLE_LOOK = {
    False: {"outline": "#313133", "bg": "#181819", "ink": "#ffffff", "muted": "#94979d",
            "dot": "#4b91ff", "button": "#313133", "button_hover": "#3d3d40",
            "primary": "#4b91ff", "primary_hover": "#6aa5ff", "on_primary": "#ffffff"},
    True: {"outline": "#eba55a", "bg": "#2a2418", "ink": "#f6ead6", "muted": "#c9b391",
           "dot": "#eba55a", "button": "#3a3122", "button_hover": "#4a3f2c",
           "primary": "#eba55a", "primary_hover": "#f3b874", "on_primary": "#2a2418"},
}

#: EACH KIND IN ITS OWN COLOUR, on the line of what waits and on its bubble's dot (the user,
#: 2026-11-07: "推荐用黄色，总结用绿色"): a notification blue, a summary green, a recommendation
#: yellow - a clear yellow, not the amber of a notification said loud.
#: How large the picture that went with a side panel message is kept, its longer side in pixels.
SIDE_PICTURE_SIDE = 1280
WAITING_INK = {"notifications": "#6aa5ff", "summaries": "#5fd1b3", "recommendations": "#f2d14b",
               "headsup": "#ff9a5c"}                     # as the side panel's (styles.css)
SUMMARY_DOT = WAITING_INK["summaries"]

#: WHAT THE MODEL RECOMMENDS LOOKS ITS OWN (the user, 2026-11-04): no notification.
RECOMMENDED_LOOK = {"outline": "#2f6f62", "bg": "#14302b", "ink": "#e6f3ef", "muted": "#9cc4b9",
                    "dot": "#5fd1b3", "button": "#1f4640", "button_hover": "#2a5a52",
                    "primary": "#2e9e83", "primary_hover": "#3bb697", "on_primary": "#ffffff"}

#: A word, or one character of a language written without spaces.
_WRAPS_AT = re.compile(r"[\u2e80-\u9fff\uac00-\ud7af\uff00-\uffef]|[^\s\u2e80-\u9fff\uac00-\ud7af\uff00-\uffef]+\s*|\s+")


def lines_of(text: str, measure: Any, wide: int) -> int:
    """How many lines words wrap to, `wide` pixels across."""
    lines = 0
    for paragraph in str(text).split("\n"):
        lines += 1
        used = 0
        for piece in _WRAPS_AT.findall(paragraph):
            size = measure(piece.rstrip()) if piece.strip() else 0
            if used and used + size > wide:
                lines += 1
                used = 0
            if size > wide:                  # one word wider than the line
                lines += size // max(1, wide)
                size %= max(1, wide)
            used += measure(piece) if used + size <= wide and piece.strip() else size
    return max(1, lines)


#: There is no timeout, and that is the point. A bubble stays until it is
#: answered. One that erased itself after a few seconds was a notification the
#: user had to happen to be looking at - and when it carried a question, the
#: question went with it.


#: How long after the window appears it is watched for not answering.
WATCHED_AT_START_SECONDS = 90


def _watch_the_window_at_start(ui: Any) -> None:
    """WHETHER THE AGENT'S WINDOW ANSWERS while the agent starts - asked of
    Windows the way it asks before showing the busy cursor over a window -
    and, when it does not, what its thread and the busy ones were doing,
    in the log. For the first minute and a half only; Windows only."""
    if sys.platform != "win32":
        return
    import ctypes
    import traceback
    from ctypes import wintypes

    try:
        hwnd = int(ui.agent_window.wm_frame(), 16)
    except Exception:                               # pragma: no cover
        return
    window_thread = threading.get_ident()
    user32 = ctypes.windll.user32
    user32.SendMessageTimeoutW.argtypes = (
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t))

    def where(frame: Any) -> str:
        from .stall_watch import stack_of
        stack = stack_of(frame)                     # no source lines read (stall_watch.stack_of)
        ours = [one for one in stack if "personalized_agent" in one.filename]
        shown = (ours[-3:] if ours else []) + [stack[-1]]
        return " > ".join(f"{one.filename.rsplit(chr(92), 1)[-1].rsplit('/', 1)[-1]}"
                          f":{one.lineno} {one.name}" for one in shown)

    def watch() -> None:
        began = time.monotonic()
        told = 0
        while time.monotonic() - began < WATCHED_AT_START_SECONDS and told < 12:
            out = ctypes.c_size_t()
            asked = time.monotonic()
            answered = user32.SendMessageTimeoutW(hwnd, 0, 0, 0, 0x2, 1000,
                                                  ctypes.byref(out))
            took = time.monotonic() - asked
            if not answered or took > 0.5:
                told += 1
                frames = sys._current_frames()
                busy = []
                for one in threading.enumerate():
                    frame = frames.get(one.ident)
                    if frame is None or one.ident in (window_thread,
                                                      threading.get_ident()):
                        continue
                    from .stall_watch import stack_of
                    last = stack_of(frame)[-1].name
                    if last in ("wait", "sleep", "select", "_wait_for_tstate_lock",
                                "read_directory_changes", "accept", "get"):
                        continue
                    busy.append(f"{one.name}: {where(frame)}")
                log.info("the agent window did not answer for %.1fs, %.0fs after "
                         "it appeared. Its thread: %s. Busy: %s", took,
                         asked - began,
                         where(frames[window_thread]) if window_thread in frames
                         else "?", " | ".join(busy[:8]) or "nothing")
            time.sleep(0.25)

    threading.Thread(target=watch, name="window-watch", daemon=True).start()


def _extension_browsers(app: Any) -> set:
    """The browsers the agent's extension said it is in (their programs)."""
    try:
        from ..platform_layer.browser_extension import seen_processes
        return seen_processes(app.data_dir)
    except Exception:
        return set()


#: How much of the screen a notification's words take before they scroll.
WORDS_SHARE_OF_SCREEN = 0.45
#: The bar beside them: the strip that takes the mouse and the thumb drawn in it, as on sizes.REFERENCE.
BAR_WIDE, BAR_THUMB = 16, 8
#: How far two fingers open or close for one step of zoom.
PINCH_STEP = 0.12


def zoom_popups(desk: Any, step: int) -> None:
    """ONE ZOOM FOR EVERY WINDOW OF THE AGENT'S (the user, 2026-11-05: "不要每个panel独立，而是和Muse一样的
    缩放逻辑"): what one is zoomed to, all are - and the next to open. A hidden setting."""
    from .chat_bubble import ZOOM_START
    step = sizes.within_zoom(step)
    if step == sizes.popup_zoom(desk.config):
        return
    try:
        desk.config.set("popup_zoom", step, internal=True)
    except Exception:                        # pragma: no cover - the windows still follow
        log.debug("the windows' zoom could not be kept", exc_info=True)
    chat, note = getattr(desk, "_chat_bubble", None), getattr(desk, "_handed_note", None)
    told = [getattr(desk, "_redraw_bubble", None), getattr(desk, "_redraw_waiting", None)]
    if chat is not None:
        told.append(lambda: chat.zoom_to(ZOOM_START + step))
    if note is not None and note.zooms:
        told.append(lambda: note.zoom_to(next(iter(note.zooms)), ZOOM_START + step))
    told += [lambda one=one: one.zoom_to(step)
             for one in (getattr(desk, "_quick_panels", None) or {}).values()]
    quick = getattr(desk, "_quick", None)
    if quick is not None:
        told.append(lambda: quick.show_zoom(step))
    for one in told:
        try:
            if one is not None:
                one()
        except Exception:                    # one window's trouble is not the others'
            log.debug("a window could not be zoomed", exc_info=True)


def window_writes(desk: Any) -> Any:
    """The window's writes, handed over (later_writes.py) - made the first time they are asked for."""
    later = getattr(desk, "_later", None)
    if later is None:
        from .later_writes import LaterWrites
        later = LaterWrites(lambda key, value: DesktopAgent._write_kv(desk, key, value))
        desk._later = later
    return later


#: Ms after the avatar's start before its two panels are made ahead (2026-11-20: not while the start loads).
PANELS_MADE_AFTER_MS = 10000

class DesktopAgent:
    def __init__(self, application: Any) -> None:
        self.app = application
        # The agent speaks in the bubble beside it: the page, told so through
        # /api/status, does not say the same thing a second time.
        application.speaks_in_bubble = True
        # An update is installed once this program has quit: its own Quit,
        # on the window's thread.
        updater = getattr(application, "updater", None)
        if updater is not None:
            updater.quit = lambda: self.on_main_thread(self.quit)
        self.config = application.config
        self.queue: "queue.Queue[Any]" = queue.Queue()
        # THE WINDOW NEVER WAITS FOR THE DATABASE, AND WHEN IT STANDS STILL IT IS SAID (the user, 2026-11-16:
        # the chat bubble slow to type in and to send): later_writes.py, stall_watch.py.
        from .stall_watch import StallWatch
        window_writes(self)
        self._stalls = StallWatch()
        self.state = "idle"
        #: Whether the avatar is hidden (Hide avatar): kept here, where the
        #: window's own thread changes it, for the tray's menu to read.
        self.character_hidden = False
        self._frames: Dict[str, List[Any]] = {}
        #: On a Mac, the native view the avatar is drawn in (PR #5), or None.
        self._native_avatar: Any = None
        self._frame_index = 0
        #: An avatar whose face is drawn live (agent/character/watermelon.py),
        #: and what it is doing: where it looks, when it next blinks, whether
        #: the pointer is on it, when it was clicked.
        self._rig: Any = None
        self._live: Dict[str, Any] = {"look": [0.0, 0.0], "blink_at": 0.0, "hover": False,
                                      "poked": -10.0, "start": time.monotonic(),
                                      "pointer": (0, 0), "moved": 0.0, "image": None}
        #: The conversation this agent window is having (section 7.1).
        self._conversation_id: Optional[str] = None
        #: The hover readout and its refresh job (section 7.5).
        self._hover: Optional[tk.Toplevel] = None
        self._hover_job: Optional[str] = None
        self._click_job = None
        #: Bubbles waiting their turn, and the one on screen. One at a time:
        #: overlapping bubbles are unreadable, and a user who cannot tell how
        #: many arrived cannot tell what they missed.
        self._bubble_queue: "collections.deque" = collections.deque()
        self._bubble: Optional[tk.Toplevel] = None
        self._bubble_count: Optional[tk.Label] = None
        #: What the bubble on screen is saying, so that the same interruption
        #: raised again merges into it rather than queueing behind it.
        self._showing: Optional[Dict[str, Any]] = None
        #: Reminders held by a blocker, opened by hand; the line saying they wait.
        self._bubbles_opened = False
        self._waiting_line: Optional[tk.Toplevel] = None

        # The character is a borderless, always-on-top window - but it must not
        # be the application's root. A root with no border and no manager frame
        # is not a window the system ever activates, and on Windows nothing
        # owned by it can take the keyboard: the conversation window opens and
        # then cannot be typed into (section 7.1). So the root stays a normal,
        # hidden window, and the character is a child of it.
        self.root = tk.Tk()
        # What Python lets go of cleaned up by Tk's thread in its own time - never from within a window
        # procedure, where a Tkinter call aborts Python (2026-11-17, ui/tk_finalizers.py).
        from . import tk_finalizers
        tk_finalizers.install(self.root)
        self.root.title(self.app.avatar_name())
        self.root.withdraw()
        if sys.platform == "darwin":
            self._be_in_the_dock()

        self.agent_window = tk.Toplevel(self.root)
        self.agent_window.title(self.app.avatar_name())
        self.agent_window.overrideredirect(True)
        self.agent_window.attributes("-topmost", True)
        self.size = 128
        self._place_bottom_right()
        try:
            self.agent_window.attributes("-transparentcolor", TRANSPARENT)
            background = TRANSPARENT
        except tk.TclError:  # macOS / Linux
            # A plain tile. On a Mac only to begin with, seen whatever
            # happens: made clear once the native view is in it, or by Tk's
            # own means where those show the character (`_enable_native_avatar`).
            background = "#101216"
            self.agent_window.attributes("-alpha", 0.96)
        self.canvas = tk.Canvas(self.agent_window, width=self.size, height=self.size,
                                highlightthickness=0, bg=background)
        self.canvas.pack()
        if sys.platform == "darwin":
            # Above the other windows: on a Mac, asked before the window was
            # mapped, it was not (it sat among the ordinary windows).
            self.root.after(500, lambda: self.agent_window.attributes("-topmost", True))
        self.agent_window.protocol("WM_DELETE_WINDOW", self.quit)
        from .on_top import stays_through_peek
        stays_through_peek(self.agent_window)

        self._load_character()
        self._enable_native_avatar()
        self._bind_events()
        self._own_panel_for_the_box()
        self.app.bus.subscribe("notification.created", self._on_notification)
        # READ ANYWHERE - the page, the report, a button: the line beside the avatar drawn again now.
        self.app.bus.subscribe("notification.read", lambda _topic, _said: self.queue.put(("waiting", None)))
        self.app.bus.subscribe("agent.proactive_message", self._on_proactive)
        # The side panel's × puts it away through the desktop (api/routes.py), off this thread.
        self.app.hide_notes_panel = self.hide_notes_panel
        # Its picture's column opened or put away: the panel wider or back (api/routes.py, off this thread).
        self.app.widen_notes_panel = self.widen_notes_panel
        self.app.size_notes_panel = self.size_notes_panel
        self.app.pin_notes_panel = self.pin_notes_panel
        self.app.full_notes_panel = self.full_notes_panel
        self.app.grab_notes_panel = lambda wide=False: DesktopAgent.grab_notes_panel(self, wide)
        self.app.toggle_notes_panel = lambda: DesktopAgent.toggle_notes_panel(self)
        # WHAT IS IN FRONT OF THE USER, WITH A MESSAGE FROM THE SIDE PANEL as with one from the bubble before it
        # (api/routes.py chat_turn): the window behind the panel - the panel is never in the picture.
        self.app.front_for_chat = lambda told=None: DesktopAgent._front_for_chat(self, told)
        # A NOTIFICATION'S "GO TO IT": the app's window, at the report (api/routes.py, off this thread).
        self.app.open_report_window = self.open_report_window
        # THE AVATAR IN THE WORLD, NOT ON THE SCREEN, while the Play view is open (api/web/play.js).
        # The small screen's own screenshot button: the shortcut's cut-out (api/web/side-board.js sideBoardSnip).
        self.app.snip_into_board = lambda: DesktopAgent._snip_by_shortcut(self)
        # The side panel brought in while the agent has the screen (agent/screen_control.py) - quietly: the keys stay
        # where the agent is typing.
        self.app.side_panel_in = lambda: self.queue.put(("call", lambda: DesktopAgent.open_notifications(self, quietly=True)))
        self.app.side_panel_hold = lambda on: self.queue.put(("call", lambda: DesktopAgent._hold_side_panel(self, on)))
        # Where the side panel is, and whether it is in front - never the agent's to touch.
        self.app.side_panel_box = lambda: DesktopAgent._side_panel_box(self)
        self.app.side_panel_front = lambda: DesktopAgent._side_panel_front(self)
        self.app.avatar_in_play = lambda on: self.queue.put(("call", lambda: DesktopAgent.avatar_in_play(self, on)))
        self.app.bus.subscribe("agent.speaking", self._on_speaking)
        self.app.bus.subscribe("character.changed", self._on_character_changed)
        self.app.bus.subscribe("work.handed_opened", self._on_handed_opened)
        self.root.after(120, self._drain_queue)
        self.root.after(400, self._animate)
        self.root.after(1500, self._log_avatar_window)
        self.root.after(self.BLOCKERS_EVERY_MS, self._watch_the_blockers)
        self.root.after(self.ON_SCREEN_EVERY_MS, self._watch_the_screens)
        self._start_selection_watch()
        # The folder and application dialogs open on this thread, the one
        # that owns the windows (common/chooser.py).
        chooser.dialogs_on(self.root, lambda fn: self.queue.put(("call", fn)))
        _watch_the_window_at_start(self)
        # And its icon beside the clock, while it runs.
        from .tray import Tray
        self.tray = Tray(self)
        self.tray.start()

    # -- window placement ---------------------------------------------------
    def _log_avatar_window(self) -> None:
        """Said once it is up: the window as mapped, not only that it began."""
        log.info("avatar window: Tk %s, geometry %s, mapped %s, drawn natively %s",
                 self.root.tk.call("info", "patchlevel"), self.agent_window.geometry(),
                 self.agent_window.winfo_viewable(), self._native_avatar is not None)
        if sys.platform != "darwin":
            return
        try:
            from AppKit import NSApplication, NSWindowOcclusionStateVisible
            for window in NSApplication.sharedApplication().windows():
                if (str(window.title()) == self.app.avatar_name()
                        and int(window.contentView().bounds().size.width) == self.size):
                    log.info("avatar native window: visible %s, active desktop %s, "
                             "unoccluded %s, level %s", window.isVisible(),
                             window.isOnActiveSpace(),
                             bool(window.occlusionState() & NSWindowOcclusionStateVisible),
                             window.level())
        except Exception:
            log.debug("could not inspect the native avatar window", exc_info=True)

    def _be_in_the_dock(self) -> None:
        """On a Mac: an icon in the Dock, and a click on it - or opening the
        application again - opens the Overview. Not left to the menu-bar
        icon alone: with many icons there, a Mac with a notch hides the ones
        that do not fit, and the agent had no way in that could be seen."""
        try:
            self.root.createcommand("::tk::mac::ReopenApplication", self._dock_pressed)
        except Exception:      # pragma: no cover - asked of a Mac's Tk
            log.info("no Dock reopen on this Tk", exc_info=True)
        try:
            from AppKit import NSApplication, NSApplicationActivationPolicyRegular
            NSApplication.sharedApplication().setActivationPolicy_(
                NSApplicationActivationPolicyRegular)
        except Exception:      # pragma: no cover - asked of a Mac
            log.info("no Dock icon on this Mac", exc_info=True)
        self._dock_menu()
        self._pinch_zooms()

    def _pinch_zooms(self) -> None:
        """TWO FINGERS ON A MAC'S TRACKPAD zoom every window, as Command + and - do (the user,
        2026-11-05). Tk has no such event: the system's is listened to, in this program only."""
        try:
            import AppKit
            pinched = {"by": 0.0}

            def heard(event: Any) -> Any:
                pinched["by"] += float(event.magnification())
                if abs(pinched["by"]) >= PINCH_STEP:
                    step = 1 if pinched["by"] > 0 else -1
                    pinched["by"] = 0.0
                    self.queue.put(("call", lambda: zoom_popups(
                        self, sizes.popup_zoom(self.config) + step)))
                return event
            self._pinch_watch = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                AppKit.NSEventMaskMagnify, heard)
        except Exception:      # pragma: no cover - asked of a Mac
            log.info("no pinch to zoom here", exc_info=True)

    def _dock_menu(self) -> None:
        """What a right click on it offers: the avatar's menu, each done on the window's thread."""
        if sys.platform != "darwin":
            return
        from . import macos_dock
        say = self._words

        def later(work: Any) -> Any:
            return lambda: self.queue.put(("call", work))
        macos_dock.show([
            (say("Chat"), later(self.open_chat_bubble)),
            (say("To do"), later(lambda: self._quick_panel("todo"))),
            (say("Collections"), later(lambda: self._quick_panel("collection"))),
            (say("Waiting notifications"), later(self.open_notifications)),
            (say("Report"), later(self.open_report)),
            (say("Overview"), later(lambda: self.open_url("/#overview"))),
            (say("Show avatar" if self.character_hidden else "Hide avatar"),
             later(self.toggle_character))])

    def _dock_pressed(self, *_args: Any) -> None:
        """THE WAY BACK FOR A HIDDEN AVATAR that does not need the menu bar (a classmate's Mac,
        2026-11-05: hidden, it could not be brought back; a Mac with a notch hides the icons that
        do not fit, and that icon was the only way)."""
        if self.character_hidden:
            self.toggle_character()
        else:
            # Not hidden and still not to be seen: the press finds it all the same.
            self._keep_on_screen()
            self.agent_window.lift()
        self.open_url("/#overview")

    def _place_bottom_right(self) -> None:
        screen_width = self.agent_window.winfo_screenwidth()
        screen_height = self.agent_window.winfo_screenheight()
        screens = _mac_screens() if sys.platform == "darwin" else []
        if screens:
            screen_width, screen_height = screens[0][2], screens[0][3]
        x = screen_width - self.size - 60
        y = screen_height - self.size - 120
        self.agent_window.geometry(f"{self.size}x{self.size}+{x}+{y}")
        self._tucked = self._beside = ""
        live = getattr(self, "_live", None)
        if live:
            live.pop("slide", None)
            live.pop("sneak", None)
            live["tuck"] = live["grip"] = 0.0

    # -- character ----------------------------------------------------------
    def _own_panel_for_the_box(self) -> None:
        """ON A MAC, THE DELEGATE WORK BOX IN A PANEL OF THE AGENT'S OWN (the
        user, 2026-11-02; ui/mac_panel.py) - made on this, the main, thread
        (through the queue: asked from the API's thread too), and opened in
        the browser should it fail."""
        if sys.platform != "darwin":
            return
        try:
            from ..platform_layer import app_window
            from .mac_panel import available, open_panel
            if not available():
                return

            def opener(url: str, size: tuple) -> bool:
                def made() -> None:
                    if not open_panel(url, size):
                        import webbrowser
                        webbrowser.open(url)
                self.queue.put(("call", made))
                return True
            app_window.set_native_opener(opener)
        except Exception:                          # pragma: no cover - the browser, then
            log.debug("no panel of its own for the box", exc_info=True)

    def _enable_native_avatar(self) -> None:
        """ON A MAC, the avatar drawn by a native image view over a clear
        window (PR #5, ui/macos_avatar.py). Where it cannot be: Tk's own
        clear window on a Tk it shows the character on, else the plain tile."""
        if sys.platform != "darwin" or not (self._rig or self._frames):
            return
        try:
            from .macos_avatar import NativeAvatar
            self._native_avatar = NativeAvatar(self.agent_window, self.canvas, self.size)
            log.info("the avatar is drawn by a native image view on a clear window")
            return
        except Exception:
            log.warning("no native avatar view; Tk's drawing instead", exc_info=True)
            self._native_avatar = None
        patchlevel = str(self.root.tk.call("info", "patchlevel"))
        if not _clear_window_shows_pictures(patchlevel):
            log.info("Tk %s: the character on a plain tile, not a clear window", patchlevel)
            return
        # Tk's own: ``-transparent`` with the system's clear colour on the
        # window and the canvas. Tried on a Mac (Tk 8.6.15, Aqua) beside a
        # plain tile and an NSWindow made clear by hand: seen whole, and
        # pressed, with nothing round it (2026-10-23).
        try:
            self.agent_window.configure(bg="systemTransparent")
            self.canvas.configure(bg="systemTransparent")
            self.agent_window.attributes("-transparent", True)
            self.agent_window.attributes("-alpha", 1.0)
        except tk.TclError:
            log.info("no clear window on this Tk: a plain tile", exc_info=True)

    def _present_frame(self, frame: Any) -> None:
        """One frame of the avatar: to the native view where there is one,
        else drawn by Tk - kept, or Tk draws nothing."""
        if self._private_mode():
            frame = self._in_private_colours(frame)
        if self._native_avatar is not None:
            try:
                self._native_avatar.show(frame)
                self._live["image"] = frame
                return
            except Exception:
                log.warning("the native avatar view could not draw; Tk's drawing instead",
                            exc_info=True)
                self._native_avatar.close()
                self._native_avatar = None
        image = ImageTk.PhotoImage(frame)
        self.canvas.delete("all")
        self.canvas.create_image(self.size // 2, self.size // 2, image=image)
        self._live["image"] = image

    def _load_character(self) -> None:
        if self._native_avatar is not None:
            self._native_avatar.close()
            self._native_avatar = None
        name = str(self.config.get("character", "watermelon"))
        package = self.app.characters.get(name) or self.app.characters.get("watermelon")
        self._rig = None
        # Another character, or the same one drawn afresh: none of the kept frames is its own.
        self._frame_cache = None
        if isinstance(getattr(self, "_live", None), dict):
            self._live["shown_key"] = None
        if not package or not PIL_AVAILABLE:
            return
        self._wear_on_taskbar(str(package.get("name") or name))
        # A FACE DRAWN LIVE: its eyes on the pointer, its moods drawn as they
        # happen - rather than frames played in turn.
        from ..agent.character.manager import drawn_live
        art = drawn_live(package)
        if art is not None:
            try:
                self._rig = art.Watermelon(scale=2, package=package.get("path"))
                self._rig_art = art
                return
            except Exception:
                log.warning("the avatar could not be drawn live; its pictures instead",
                            exc_info=True)
                self._rig = None
        for state, paths in (package.get("frames") or {}).items():
            images = []
            for path in paths:
                try:
                    # Kept as pictures: drawn by Tk or the native view
                    # (`_present_frame`).
                    images.append(Image.open(path).convert("RGBA")
                                  .resize((self.size, self.size)))
                except Exception:
                    log.debug("could not load character frame %s", path, exc_info=True)
            if images:
                self._frames[state] = images

    def _wear_on_taskbar(self, name: str) -> None:
        """The avatar's face as the icon of every window of the agent - on
        the taskbar and in each title bar - cut to the figure, so it is as
        large as other programs' icons."""
        try:
            from .tray import icon_image
            faces = [ImageTk.PhotoImage(icon_image(self.app.characters, name, size))
                     for size in (256, 64, 32, 16)]
            self.root.iconphoto(True, *faces)
            self._taskbar_faces = faces            # kept, or Tk forgets them
        except Exception:                          # pragma: no cover - cosmetic
            log.debug("could not set the window icon", exc_info=True)

    def _animate(self) -> None:
        # HIDDEN, NOT DRAWN (the user, 2026-11-20: "我现在不是已经不画西瓜头像了吗" - hidden for Play or by the user, its
        # face was still drawn several times a second, up to a second a frame, the window's thread held for it): looked
        # at again in a second, drawn once it is shown.
        if getattr(self, "character_hidden", False):
            self._animate_job = self.root.after(1000, self._animate)
            return
        if self._rig is not None:
            self._animate_live()
            return
        frames = self._frames.get(self.state) or self._frames.get("idle")
        if frames:
            self._frame_index = (self._frame_index + 1) % len(frames)
            self._present_frame(frames[self._frame_index])
        else:
            self.canvas.delete("all")
            # Vector fallback so the agent is visible without image assets.
            colour = STATE_COLORS.get(self.state, STATE_COLORS["idle"])
            bob = 3 if self._frame_index % 2 else 0
            self._frame_index += 1
            self.canvas.create_oval(14, 20 + bob, 114, 116 + bob, fill=colour, outline="")
            self.canvas.create_oval(44, 46 + bob, 56, 58 + bob, fill="white", outline="")
            self.canvas.create_oval(72, 46 + bob, 84, 58 + bob, fill="white", outline="")
            self.canvas.create_arc(50, 68 + bob, 80, 92 + bob, start=200, extent=140,
                                   style=tk.ARC, outline="#22293a", width=3)
            if self.state == "notification":
                self.canvas.create_oval(92, 12, 118, 38, fill="#e64646", outline="white", width=2)
        # The readout put away by where the pointer is (see `_animate_live`).
        if not getattr(self, "_carried", False) and (
                getattr(self, "_hover", None) is not None
                or getattr(self, "_hover_wait", None) is not None) \
                and not self._pointer_on_avatar():
            self._on_hover_leave()
        self.root.after(650, self._animate)

    #: How long the avatar takes to turn round.
    TURN_SECONDS = 0.3

    def _animate_live(self) -> None:
        """One frame of a live avatar, and the next one asked for: often
        while something moves - the pointer, a blink, a hop - and seldom
        while all it does is breathe."""
        import math
        import random
        live = self._live
        now = time.monotonic()
        t = now - live["start"]
        busy = False
        try:
            px, py = self.root.winfo_pointerxy()
            left, top = self.agent_window.winfo_rootx(), self.agent_window.winfo_rooty()
        except tk.TclError:                        # pragma: no cover - window gone
            return
        if (px, py) != live["pointer"]:
            live["pointer"], live["moved"] = (px, py), now
            if sys.platform == "win32":
                # Where it went, for the picture sent with a message (windows.pointer_elsewhere).
                from ..platform_layer import windows as windows_layer
                windows_layer.POINTER_TRAIL.append((int(px), int(py)))
        # From between its eyes, toward the pointer: all the way at 150 px.
        from ..agent.character import watermelon
        art = getattr(self, "_rig_art", None) or watermelon      # the flat one: its own middle
        RIG, facing_for = art.RIG, art.facing_for
        # Toward the middle of the screen: on the right half it faces left.
        # Turning round, it TURNS - narrows to its edge and opens the other
        # way (TURN_SECONDS) - rather than flipping in one frame.
        facing = facing_for(left, self.size, self.agent_window.winfo_screenwidth(),
                            live.get("facing", "left"))
        tucked = getattr(self, "_tucked", "")
        if tucked in ("left", "right"):            # the body goes away from its face
            facing = "left" if tucked == "right" else "right"
        if facing != live.get("facing", "left"):
            live["turn"] = (now, live.get("facing", "left"))
        live["facing"] = facing
        fx, fy = RIG.get("looks_from", (94, 56))
        if live["facing"] == "right":
            fx = 128 - fx
        cx, cy = left + self.size * fx / 128, top + self.size * fy / 128
        aim = (max(-1.0, min(1.0, (px - cx) / 150)), max(-1.0, min(1.0, (py - cy) / 150)))
        if now - live["moved"] > 12:               # nothing moved: it looks ahead
            aim = (0.0, 0.0)
        sneaking = self._sneak_on(live, now, left <= px < left + self.size
                                  and top <= py < top + self.size)
        if sneaking:
            aim = {"right": (sneaking.look, 0.0), "left": (-sneaking.look, 0.0),
                   "top": (0.0, -sneaking.look), "bottom": (0.0, sneaking.look)}[self._beside]
        look = [v + (a - v) * 0.35 for v, a in zip(live["look"], aim)]
        if max(abs(a - b) for a, b in zip(look, live["look"])) > 0.01:
            busy = True
        live["look"] = look
        if not live["blink_at"]:
            live["blink_at"] = t + random.uniform(2.0, 5.0)
        blink = 0.0
        if t >= live["blink_at"]:
            into = (t - live["blink_at"]) / 0.16
            if into < 1:
                blink, busy = 1 - abs(2 * into - 1), True
            else:
                live["blink_at"] = t + random.uniform(2.5, 6.0)
        # POINTED AT: from where the pointer is, not from the window's enter
        # and leave - those come and go while the pointer rests on it (the
        # see-through pixels round the figure pass the pointer through, and
        # the readout opening beside it takes it for a moment), which made
        # the smile and the cheeks flicker. On it, it smiles; off it a third
        # of a second, it stops.
        on_it = left <= px < left + self.size and top <= py < top + self.size
        if on_it:
            live["hover"], live["off_since"] = True, None
        elif live["hover"]:
            # Off it a moment - quickly looked at (the user, 2026-11-03: going
            # off it felt like a jolt - the next look came at the slow pace).
            live["off_since"] = live.get("off_since") or now
            busy = True
            if now - live["off_since"] > self.OFF_SECONDS:
                live["hover"], live["off_since"] = False, None
        # THE READOUT PUT AWAY BY WHERE THE POINTER IS, too: a leave over
        # the see-through pixels round the figure, the pointer still in its
        # square, is rightly not taken for the pointer going - and when it
        # then went, no other leave came, and the readout stayed until the
        # pointer came back and went again.
        if not live["hover"] and not getattr(self, "_carried", False) and (
                getattr(self, "_hover", None) is not None
                or getattr(self, "_hover_wait", None) is not None):
            self._on_hover_leave()
        # IN THE SIDE, HALF OF IT OUT UNDER THE POINTER (the user, 2026-11-09: "鼠标移过去会弹出半个
        # 身子，单击半个身子弹出chat, 双击overview, 拖动是把他拉出来"), back in when it goes; a hand
        # on it, for it is pressed.
        self._hand_when_tucked()
        self._peek_on(live)
        mood = self.state if self.state != "idle" else (
            "hover" if live["hover"] and not tucked else "idle")
        # CARRIED: held, whatever it was doing (the user, 2026-11-04).
        carried = bool(getattr(self, "_carried", False))
        caught = now < float(live.get("caught_until") or 0.0)
        if carried or caught:
            mood = "grabbed"
        elif sneaking:
            mood = "sneaky"
        leaning = self._lean_on(live, now, carried, left)
        # INTO THE SMILE AND OUT OF IT, over a moment - not at once (the user,
        # 2026-11-03: the smile going felt like a jolt): drawn at thirty
        # frames a second while it changes (watermelon.draw_face's `smile`).
        wanted = 1.0 if mood == "hover" else 0.0
        smile = 0.0 if carried or caught else float(live.get("smile", wanted))
        if smile != wanted:
            step = (now - float(live.get("smiled_at", now) or now)) / self.SMILE_SECONDS
            step = min(1.0, max(step, 0.2))
            smile = min(wanted, smile + step) if wanted > smile else max(wanted, smile - step)
            busy, live["smiling"] = True, True
        else:
            live["smiling"] = False
        live["smile"], live["smiled_at"] = smile, now
        since = t - live["poked"]
        # The breath on one of its poses: a still avatar's frames come round again (avatar_frames.py).
        from . import avatar_frames
        squash, hop = avatar_frames.breathing(t), 0.0
        if since < 0.7:                            # clicked: a hop and a squash
            squash = 1 - 0.18 * math.exp(-since * 7) * math.cos(since * 22)
            hop = 7 * math.sin(math.pi * since / 0.35) if since < 0.35 else 0.0
            busy = True
        elif mood == "notification":
            beat = t % 1.6
            hop = 4 * math.sin(math.pi * beat / 0.3) if beat < 0.3 else 0.0
            busy = True
        elif mood in ("talking", "thinking"):
            busy = True
        elif carried:                              # hanging from the hand
            squash, hop, busy = 1.05, 3.0, False   # and no frames to hold the drag up
        elif sneaking:
            squash, hop = sneaking.squash, sneaking.hop
        if sneaking and sneaking.done:
            self._sneaked_in(live)
        self._slide_on(live, now)
        if (live.get("slide") or {}).get("leap"):
            squash, hop = live["pose"]
        shown, narrow = live.get("facing", "left"), 1.0
        turn = live.get("turn")
        if turn:
            into = (now - turn[0]) / self.TURN_SECONDS
            if into >= 1:
                live["turn"] = None
            else:
                # The way it faced until half way, then the new one; its
                # width as a coin's turning edge-on.
                shown = turn[1] if into < 0.5 else shown
                narrow, busy = max(0.06, abs(math.cos(math.pi * into))), True
        try:
            more = {"tuck": live["tuck"]} if live.get("tuck") else {}
            if leaning:
                more["lean"] = live["lean"]
            if live.get("tuck") or live.get("grip"):
                side = getattr(self, "_tucked", "") or getattr(self, "_beside", "")
                more["edge"] = side if side in ("top", "bottom") else "side"
            if getattr(self._rig, "TUCKS", False):
                from . import avatar_tuck
                part = self._part_on(live, now)
                if part:
                    more["part"] = part
                if carried or caught:
                    more["flail"] = avatar_tuck.flail_at(now)
            if live.get("grip"):
                more.update(grip=live["grip"], reach=self._reach(live))
                # Jumping out, it turns upright in the air.
                if (getattr(self, "_tucked", "") or (live.get("slide") or {}).get("leap")) in (
                        "left", "right", "top"):
                    more["turn"] = live["grip"] * (1 - float(live.get("tuck") or 0.0))
            quick = bool(live.get("slide"))
            rig = self._rig.quick() if quick else self._rig
            # DRAWN ONCE, SHOWN AFTER (avatar_frames.py): what it is drawn from rounded past what shows,
            # a frame that comes round again taken from the kept ones, one the same as the one on the
            # screen not shown again.
            look, blink, squash, hop, smile = avatar_frames.rounded(tuple(look), blink, squash, hop, smile)
            cache = getattr(self, "_frame_cache", None)
            if cache is None:
                cache = self._frame_cache = avatar_frames.FrameCache()
            key = None if quick or narrow < 1.0 else cache.key(
                mood, look, blink, squash, hop, self.size, shown, smile, more,
                self._native_avatar is not None)
            shown_now = None if key is None else (key, self._private_mode())
            if shown_now is None or live.get("shown_key") != shown_now:   # else on the screen already
                frame = cache.get(key)
                if frame is None:
                    frame = rig.frame(mood, look, blink, t, squash, hop, size=self.size,
                                      facing=shown, smile=smile, **more)
                    if narrow < 1.0:
                        from PIL import Image
                        width = max(1, int(self.size * narrow))
                        thin = frame.resize((width, self.size), Image.BILINEAR)
                        frame = Image.new("RGBA", (self.size, self.size), (0, 0, 0, 0))
                        frame.paste(thin, ((self.size - width) // 2, 0), thin)
                    # Whole pixels only: an edge half see-through takes on the colour
                    # the window keys out, which shows as a pink fringe. Not in the
                    # Mac's native view, whose edges are drawn as they are.
                    if self._native_avatar is None:
                        alpha = frame.getchannel("A").point(lambda a: 255 if a >= 110 else 0)
                        frame.putalpha(alpha)
                    cache.put(key, frame)
                self._present_frame(frame)
                live["shown_key"] = shown_now
        except Exception:                          # pragma: no cover - drawn next time
            log.debug("could not draw the avatar", exc_info=True)
        # Turning, thirty frames a second, so the turn is seen as a turn; and
        # the smile coming or going.
        self._animate_job = self.root.after(
            max(1, self.SLIDE_FRAME_MS - int((time.monotonic() - now) * 1000))
            if live.get("slide")
            else 33 if live.get("turn") or live.get("smiling") or live.get("sneak")
            or live.get("parting") else 80 if carried    # its frames must not hold the drag up
            else 50 if busy or caught or leaning else 160, self._animate)

    #: Going in or coming out, a frame this often, in milliseconds, the drawing included.
    SLIDE_FRAME_MS = 16

    #: How long the smile takes to come, or to go; and how long the pointer
    #: is off it before it goes (the see-through pixels round the figure).
    SMILE_SECONDS = 0.16
    OFF_SECONDS = 0.2

    # -- tucked into the side of the screen (ui/avatar_tuck.py) --------------------------
    #: How long it takes to go in, or to come out.
    TUCK_SECONDS = 0.24
    #: How long it looks caught, seen sneaking.
    CAUGHT_SECONDS = 1.0

    def _slide_on(self, live: Dict[str, Any], now: float) -> None:
        """One step of going in or coming out: the window, and the body off
        the leaves with it."""
        slide = live.get("slide")
        if not slide:
            return
        from . import avatar_tuck
        side = slide.get("leap", "")
        seconds = (avatar_tuck.JUMP_IN_SECONDS if slide.get("into_side")
                   else avatar_tuck.leap_seconds(side) if side else self.TUCK_SECONDS)
        into = min(1.0, (now - slide["at"]) / seconds)
        jump = avatar_tuck.leap(into) if side else None
        eased = jump.out if jump else 1 - (1 - into) ** 3
        x = int(round(slide["x"] + (slide["to"][0] - slide["x"]) * eased))
        y = int(round(slide["y"] + (slide["to"][1] - slide["y"]) * eased))
        if jump:
            live["pose"] = (jump.squash, 0.0)
            if side in ("left", "right"):          # out of a top or bottom it flies straight
                y -= int(round(jump.lift * avatar_tuck.LEAP_LIFT * self.size))
        along = max(0.0, min(1.0, eased))
        live["tuck"] = slide["tuck"] + (slide["to_tuck"] - slide["tuck"]) * along
        live["grip"] = slide["grip"] + (slide["to_grip"] - slide["grip"]) * along
        live["at"] = (x, y)
        self.agent_window.geometry(f"+{x}+{y}")
        if into >= 1:
            live.pop("slide", None)
            if slide.get("into_side") and getattr(self, "_beside", ""):
                self._sneaked_in(live)               # in, as if it had sneaked there

    #: Pulled along, how far it leans back at most, and for how fast a pull.
    LEAN_MOST, LEAN_PER_PIXEL = 11.0, 0.02

    def _lean_on(self, live: Dict[str, Any], now: float, carried: bool, left: int) -> float:
        """How far it leans now (the user's earlier companion, yanked: it leans
        back against the pull) - after the hand, and upright when let go."""
        wanted = 0.0
        last = live.get("carried_at")
        if carried and last:
            speed = (left - last[1]) / max(0.01, now - last[0])
            wanted = max(-self.LEAN_MOST, min(self.LEAN_MOST, speed * self.LEAN_PER_PIXEL))
        live["carried_at"] = (now, left) if carried else None
        lean = float(live.get("lean") or 0.0)
        lean += (wanted - lean) * 0.4
        live["lean"] = 0.0 if abs(lean) < 0.2 and not wanted else lean
        return live["lean"]

    def _part_on(self, live: Dict[str, Any], now: float) -> float:
        """How far apart the leaves are: its head out, pushed aside; back in, closing behind it -
        whatever brought it out or in."""
        from . import avatar_tuck
        wanted = 1.0 if live.get("peeking") else 0.0
        if wanted != live.get("part_to", 0.0):
            live.update(part_to=wanted, part_from=float(live.get("part") or 0.0), part_at=now,
                        parting=True)
        if live.get("parting"):
            live["part"] = avatar_tuck.parted(now - live["part_at"], live["part_from"], wanted)
            if now - live["part_at"] >= avatar_tuck.PART_SECONDS:
                live["parting"] = False
        return float(live.get("part") or 0.0)

    def _placed(self, place: Any, side: str) -> tuple:
        """Where `place` (ui/avatar_tuck.py) puts it, in the side it is in."""
        from . import avatar_tuck
        window = self.agent_window
        x, y = window.winfo_x(), window.winfo_y()
        sides = avatar_tuck.sides_of(window, *avatar_tuck.seen_at(side, x, y, self.size))
        return place(side, x, y, self.size, sides)

    def _reach(self, live: Dict[str, Any]) -> float:
        """How far in from the side of the screen its window is out, in the
        128 units it is drawn in: the leaves stay on the side as it moves."""
        side = getattr(self, "_tucked", "") or getattr(self, "_beside", "")
        x, y = live.get("at") or (self.agent_window.winfo_x(), self.agent_window.winfo_y())
        edge = live.get("edge", 0)
        out = {"right": edge - x, "left": x + self.size - edge,
               "top": y + self.size - edge, "bottom": edge - y}[side]
        return out * 128 / self.size

    def _slide_from_here(self, to: tuple, tuck: float, grip: float, leap: str = "") -> None:
        """`leap`: the side it jumps out of, rather than sliding."""
        window, live = self.agent_window, self._live
        live["slide"] = {"at": time.monotonic(), "x": window.winfo_x(), "y": window.winfo_y(),
                         "to": to, "tuck": float(live.get("tuck") or 0.0), "to_tuck": tuck,
                         "grip": float(live.get("grip") or 0.0), "to_grip": grip, "leap": leap}

    def _note_the_side(self) -> None:
        """Let go: beside the side of a screen, it will sneak in, left alone."""
        self._beside = ""
        if not getattr(getattr(self, "_rig", None), "TUCKS", False):
            return
        from . import avatar_tuck
        window = self.agent_window
        x, y = window.winfo_x(), window.winfo_y()
        sides = avatar_tuck.sides_of(window, x + self.size // 2, y + self.size // 2)
        self._beside = avatar_tuck.side_to_tuck(x, y, self.size, sides)
        self._beside_since = time.monotonic()
        if avatar_tuck.jumps_in(self._beside, x, y, self.size, sides):
            # MORE THAN HALF OF IT PAST THE SIDE: in it jumps, now (the user, 2026-11-08).
            self._live["edge"] = avatar_tuck.edge_of(self._beside, sides)
            self._slide_from_here(avatar_tuck.tucked_at(self._beside, x, y, self.size, sides),
                                  avatar_tuck.TUCK_REST, 1.0, leap=self._beside)
            self._live["slide"]["into_side"] = True
            self._look_now()

    def _watched(self, pointer_on_it: bool) -> bool:
        """Whether anything is going on with it: it does not sneak off then."""
        bubble = getattr(self, "_chat_bubble", None)
        return bool(pointer_on_it or getattr(self, "_carried", False)
                    or getattr(self, "state", "idle") != "idle"
                    or getattr(self, "character_hidden", False)
                    or (bubble is not None and bubble.is_open)
                    or getattr(self, "_bubble", None) is not None
                    or getattr(self, "_hover", None) is not None)

    def _sneak_on(self, live: Dict[str, Any], now: float, pointer_on_it: bool) -> Any:
        """A moment of sneaking in, if it is at it (ui/avatar_tuck.py). Seen
        at it, it stops and goes back."""
        side = getattr(self, "_beside", "")
        if not side or getattr(self, "_tucked", "") or live.get("slide"):
            return None
        from . import avatar_tuck
        sneak = live.get("sneak")
        if self._watched(pointer_on_it):
            self._beside_since = now
            if sneak:
                del live["sneak"]
                live["poked"] = now - live["start"]      # caught: a hop, and that face
                live["caught_until"] = now + self.CAUGHT_SECONDS
                self._slide_from_here((sneak["x"], sneak["y"]), 0.0, 0.0)
            return None
        window = self.agent_window
        if not sneak:
            if now - getattr(self, "_beside_since", now) < avatar_tuck.SNEAK_AFTER:
                return None
            x, y = window.winfo_x(), window.winfo_y()
            sides = avatar_tuck.sides_of(window, x + self.size // 2, y + self.size // 2)
            sneak = live["sneak"] = {"at": now, "x": x, "y": y,
                                     "to": avatar_tuck.tucked_at(side, x, y, self.size, sides)}
            live["edge"] = avatar_tuck.edge_of(side, sides)
        step = avatar_tuck.sneak_at(now - sneak["at"])
        x = int(round(sneak["x"] + (sneak["to"][0] - sneak["x"]) * step.walk))
        y = int(round(sneak["y"] + (sneak["to"][1] - sneak["y"]) * step.walk))
        window.geometry(f"+{x}+{y}")
        live["at"] = (x, y)
        # The body only partly off its leaves at the last: a little of the melon shows.
        from . import avatar_tuck as _tuck
        live["tuck"], live["grip"] = step.tuck * _tuck.TUCK_REST, step.tuck
        return step

    def _sneaked_in(self, live: Dict[str, Any]) -> None:
        from . import avatar_tuck
        self._tucked, self._beside = self._beside, ""
        live.pop("sneak", None)
        live["tuck"], live["grip"] = avatar_tuck.TUCK_REST, 1.0
        vine = getattr(self, "_chat_vine", None)
        if vine is not None:
            vine.place()                         # hidden: out it comes

    def _hand_when_tucked(self) -> None:
        """The pointer a hand over it while it is in a side; as it was when it is out."""
        wanted = "hand2" if getattr(self, "_tucked", "") else ""
        if wanted == getattr(self, "_cursor_shown", ""):
            return
        try:
            self.canvas.configure(cursor=wanted)
            self._cursor_shown = wanted
        except (tk.TclError, AttributeError):    # pragma: no cover - closing
            pass

    def _peek_on(self, live: Dict[str, Any]) -> None:
        """In the side, the pointer on it: half of it out; the pointer gone, back in - a little of
        the melon left out."""
        side = getattr(self, "_tucked", "")
        if not side or live.get("slide"):
            return
        looking = bool(live.get("hover"))
        if looking == bool(live.get("peeking")):
            return
        from . import avatar_tuck
        live["peeking"] = looking
        place = avatar_tuck.peek_at if looking else avatar_tuck.tucked_at
        self._slide_from_here(self._placed(place, side),
                              0.0 if looking else avatar_tuck.TUCK_REST, 1.0)

    def _untuck(self) -> None:
        """The leaf clicked: it jumps back out - beside the side, and goes in
        again in a while."""
        from . import avatar_tuck
        side, self._tucked = self._tucked, ""
        self._live["peeking"] = False
        self._beside, self._beside_since = side, time.monotonic()
        self._slide_from_here(self._placed(avatar_tuck.back_at, side), 0.0, 0.0, leap=side)
        self._look_now()

    def _look_now(self) -> None:
        """The next frame at once - the pointer just came on or went: the
        smile begins now, not at the next slow frame (up to 160 ms on)."""
        job = getattr(self, "_animate_job", None)
        if job is None:
            return
        try:
            self.root.after_cancel(job)
        except Exception:
            pass
        self._animate_job = self.root.after(1, self._animate)

    def set_state(self, state: str) -> None:
        self.state = state if state in STATE_COLORS else "idle"

    # -- interaction --------------------------------------------------------
    def _bind_events(self) -> None:
        self.canvas.bind("<Enter>", self._on_hover_enter)
        self.canvas.bind("<Leave>", self._on_hover_leave)
        # A CLICK IS LET GO WHERE IT WAS PRESSED (the user, 2026-11-04: it hopped
        # at the press and opened the chat whatever came next): the press does
        # nothing to be seen, a drag is held at once, and `_end_drag` counts
        # one click or two in `_on_click` - not Tk's own double click, which
        # any event between the presses broke.
        self.canvas.bind("<Button-3>", self._on_right_click)
        if sys.platform == "darwin":
            # A Mac's Tk calls the right button 2; and a Mac without one
            # clicks with Control held.
            self.canvas.bind("<Button-2>", self._on_right_click)
            self.canvas.bind("<Control-Button-1>", self._on_right_click)
            # And while another program is in front - which Tk drops there.
            try:
                from types import SimpleNamespace
                from .macos_avatar import right_clicks_while_away
                # Told through the queue, never to Tk from there: AppKit
                # calls it inside Tk's own wait for events, and a call into
                # Tk from it ended the process (measured on the lab's Mac).
                self._right_monitor = right_clicks_while_away(
                    self.agent_window, self.size,
                    lambda x, y: self.queue.put(("call", lambda: self._on_right_click(
                        SimpleNamespace(x_root=x, y_root=y)))))
            except Exception:                      # pragma: no cover - Tk's alone, then
                log.debug("the avatar's right click is Tk's alone", exc_info=True)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonPress-1>", self._start_drag, add="+")
        self.canvas.bind("<ButtonRelease-1>", self._end_drag, add="+")
        # WHAT STANDS BESIDE IT MOVES WITH IT, AT ONCE (the user, 2026-11-09: "移动西瓜logo, 1
        # recommendation 这个框很慢才会跟着西瓜一起走" - it was placed again only when the count was
        # read again, seconds on): every move of its window - dragged, sliding, jumping out.
        self.agent_window.bind("<Configure>", self._avatar_moved, add="+")
        self.agent_window.bind("<Escape>", lambda _event: self.quit())

    #: How far the pointer moves, pressed, before it is a drag and not a
    #: click: a hand's tremble on a click is not a drag.
    DRAG_FROM = 4

    def _start_drag(self, event: tk.Event) -> None:
        # Where on the window it was taken, on the screen: the window follows
        # the pointer's place on the screen. (From the place within the
        # window, which moves with every step, it trailed and shook.)
        self._drag_press = (event.x_root, event.y_root)
        self._drag_offset = (event.x_root - self.agent_window.winfo_x(),
                             event.y_root - self.agent_window.winfo_y())
        self._dragged = False
        # IN THE AIR IT IS NOT STOPPED (the user, 2026-11-04: pressed as it came out of the top,
        # it stopped half way, its head out, and the press went on to open the chat).
        self._pressed = not (self._live.get("slide") or {}).get("leap")
        if not self._pressed:
            return
        self._live.pop("slide", None)
        if self._live.pop("sneak", None):
            self._live["tuck"] = self._live["grip"] = 0.0

    def _on_drag(self, event: tk.Event) -> None:
        if not getattr(self, "_pressed", True):
            return                               # pressed in the air
        press = getattr(self, "_drag_press", (event.x_root, event.y_root))
        if not self._dragged:
            if max(abs(event.x_root - press[0]), abs(event.y_root - press[1])) < self.DRAG_FROM:
                return
            self._dragged = True
            # Carried, not clicked: no hop - its frames, fast, held the
            # first steps of the drag up.
            self._live["poked"] = -10.0
            # NO READOUT WHILE IT IS CARRIED (the user's choice): put away,
            # and not opened again until it is let go.
            self._carried = True
            if getattr(self, "_hover", None) is not None:
                self._on_hover_leave()
            # Pulled out by its leaf: all of it, in the hand.
            if getattr(self, "_tucked", ""):
                self._tucked = ""
                self._drag_offset = (self.size // 2, self.size // 2)
            self._live["tuck"] = self._live["grip"] = 0.0
            self._live["peeking"] = False
            self._look_now()                     # held, at once
        offset = getattr(self, "_drag_offset", (0, 0))
        self.agent_window.geometry(f"+{event.x_root - offset[0]}+{event.y_root - offset[1]}")

    def _end_drag(self, event: Any = None) -> None:
        """Let go: the readout again, the pointer being still on it - and,
        beside the side of a screen, it will sneak in. Let go where it was
        pressed: a click - its leaf's brings it out. (A Mac's Control-click is
        a right click: its press never came here, and nor does its let go.)"""
        pressed, self._pressed = getattr(self, "_pressed", False), False
        if getattr(self, "_carried", False):
            self._carried = False
            self._on_hover_enter()
            self._note_the_side()
            self._look_now()
        elif not pressed:
            return
        elif event is not None:
            # In the side as anywhere: once the chat, twice the Overview (the user, 2026-11-09);
            # pulled, it comes out (`_on_drag`).
            self._on_click(event)

    #: How far apart, on the screen, two presses may be and still be one
    #: double click (the system's own is a few pixels; a hand at 175%
    #: scaling moves more).
    DOUBLE_CLICK_PIXELS = 8

    @staticmethod
    def double_click_seconds() -> float:
        """The system's own double-click time - Windows' 500 ms unless the
        user set another - so a double click here is one wherever else."""
        if sys.platform.startswith("win"):
            try:
                import ctypes
                return max(0.2, min(1.5, ctypes.windll.user32.GetDoubleClickTime() / 1000))
            except Exception:                      # pragma: no cover - read below
                pass
        return 0.4

    def _on_click(self, event: tk.Event) -> None:
        """ONE CLICK OR TWO, COUNTED HERE, EACH WHEN IT IS LET GO (the user,
        2026-10-26, 2026-11-04): a double
        click, sometimes, opened two conversations. Tk's own double click is
        broken by any event between the two presses - and a click makes the
        avatar hop, so the pixel under a pointer at its edge turns
        see-through and back, and Windows says the pointer left and came
        back between them: the second press was a single click again. And a
        single click waited 240 ms, half the system's double-click time:
        presses 300 ms apart opened the chat AND the Overview. Now the
        presses are counted: a second within the system's double-click time,
        and near the first, is a double click; a single waits that long."""
        # A hop for being clicked.
        self._live["poked"] = time.monotonic() - self._live["start"]
        now = time.monotonic()
        at = (getattr(event, "x_root", 0), getattr(event, "y_root", 0))
        last = getattr(self, "_last_press", None)
        self._last_press = (now, *at)
        if self._click_job is not None:
            self.root.after_cancel(self._click_job)
            self._click_job = None
            if last is not None and now - last[0] <= self.double_click_seconds() \
                    and abs(at[0] - last[1]) <= self.DOUBLE_CLICK_PIXELS \
                    and abs(at[1] - last[2]) <= self.DOUBLE_CLICK_PIXELS:
                self._last_press = None
                self._on_double_click(event)
                return
        self._click_job = self.root.after(int(self.double_click_seconds() * 1000),
                                          self._single_click)

    def _single_click(self) -> None:
        self._click_job = None
        if getattr(self, "_dragged", False):
            self._dragged = False
            return
        self.open_chat_bubble()

    def _on_double_click(self, _event: Any = None) -> None:
        if self._click_job is not None:
            self.root.after_cancel(self._click_job)
            self._click_job = None
        self.open_url("/#overview")

    #: THE AVATAR'S MENU, and the tray's (the user's choice): the chat, To do and Collections
    #: (the user, 2026-11-04: "to do 和collection改成右键弹出来"), the Overview (as a double
    #: click), hiding the avatar, and quitting.
    def _on_right_click(self, event: tk.Event) -> None:
        menu = tk.Menu(self.root, tearoff=0)
        # Not Delegate work (the user's choice): work is handed over from the
        # page, Explorer's menu or the selection's button, not from here.
        say = self._words
        menu.add_command(label=say("Chat"), command=self.open_chat_bubble)
        menu.add_command(label=say("To do"), command=lambda: self._quick_panel("todo"))
        menu.add_command(label=say("Collections"), command=lambda: self._quick_panel("collection"))
        menu.add_command(label=say("Waiting notifications"),
                         command=self.open_notifications)
        # THE REPORT, IN A WINDOW OF ITS OWN (the user, 2026-11-12: what waits for approval, what
        # the agent did, what is theirs to do - agent/reports.py).
        menu.add_command(label=say("Report"), command=self.open_report)
        for panel in getattr(self, "_quick_panels", {}).values():
            panel.prepare()                  # what they will show, read while the menu is open
        menu.add_command(label=say("Overview"), command=lambda: self.open_url("/#overview"))
        # PRIVATE MODE, on and off here (the user, 2026-11-12): nothing recorded meanwhile, and
        # the watermelon red.
        menu.add_separator()
        menu.add_command(label=say("End private mode") if self._private_mode()
                         else say("Private mode"), command=self.toggle_private_mode)
        # Hidden, it comes back from the tray's menu: offered only with one.
        if getattr(getattr(self, "tray", None), "icon", None) is not None:
            menu.add_command(label=say("Hide avatar"), command=self.toggle_character)
        menu.add_separator()
        menu.add_command(label=say("Quit"), command=self.quit)
        x, y = int(event.x_root), int(event.y_root)
        if sys.platform == "darwin":
            # ALL OF IT ABOVE THE DOCK (on the lab's Mac, 2026-11-02: opened
            # down from the avatar low on the right, a Mac cut it at the Dock
            # and Quit was behind its scroll arrow): opened up where down it
            # does not fit.
            try:
                menu.update_idletasks()
                high = max(menu.winfo_reqheight(), 24 * menu.index("end") + 24)
                from .chat_bubble import work_area
                bottom = (work_area(menu) or (0, 0, 0, menu.winfo_screenheight() - 90))[3]
                if y + high > bottom:
                    y = max(0, y - high)
            except Exception:                      # where it was asked, then
                pass
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    # -- private mode (common/privacy_folders.py) ---------------------------------------------
    def _private_mode(self) -> bool:
        from ..common.privacy_folders import private_mode
        config = getattr(self, "config", None) or getattr(getattr(self, "app", None),
                                                          "config", None)
        return config is not None and private_mode(config)

    def toggle_private_mode(self) -> None:
        """On: nothing of the user's activity recorded - no screen read or pictured, no file -
        until turned off; off: as before. Kept through a restart; the listener stops and starts
        its sources on the change (app._on_settings_changed)."""
        on = not self._private_mode()
        try:
            (getattr(self, "config", None) or self.app.config).set("listener_private_mode", on)
        except Exception:
            log.warning("private mode could not be %s", "turned on" if on else "turned off",
                        exc_info=True)
            return
        log.info("private mode %s", "on" if on else "off")   # red from its next frame

    def _in_private_colours(self, frame: Any) -> Any:
        """THE WATERMELON RED IN PRIVATE MODE (the user, 2026-11-12): its green - the rind, the
        leaves - turned red, the rest as it is. BY ITS HUE, GREEN TO TEAL (the user, 2026-11-12:
        "logo的叶子也要变成红色" - the flat watermelon's leaves are teal, their darker half and veins
        bluer than green): each such colour, clear enough to be one, red at its own lightness; a
        grey-green (its eyes), white, pink and a tear's blue as they are."""
        try:
            import numpy
            from PIL import Image
            rgba = frame.convert("RGBA")
            pixels = numpy.array(rgba)
            hsv = numpy.array(rgba.convert("RGB").convert("HSV")).astype(numpy.int16)
            hue, sat = hsv[..., 0], hsv[..., 1]
            # PIL's hue is 0-255: 40 is about 56 degrees, 136 about 192 - yellow-green to teal.
            leafy = (hue >= 40) & (hue <= 136) & (sat >= 50) & (pixels[..., 3] > 0)
            hsv[..., 0] = numpy.where(leafy, 0, hue)
            hsv[..., 1] = numpy.where(leafy, numpy.maximum(sat, 150), sat)
            red = numpy.array(Image.fromarray(hsv.astype(numpy.uint8), "HSV").convert("RGB"))
            out = pixels.copy()
            out[..., :3] = numpy.where(leafy[..., None], red, pixels[..., :3])
            return Image.fromarray(out, "RGBA")
        except Exception:                          # pragma: no cover - drawn as it is
            log.debug("the avatar could not be drawn red", exc_info=True)
            return frame

    # -- delegate work -----------------------------------------------------------------------
    def _delegate_face(self, size: int = 16) -> Any:
        """The avatar's face, small - beside each Delegate work, and the icon
        beside selected text."""
        faces = getattr(self, "_faces_small", None)
        if faces is None or getattr(self, "_face_for", "") != self.config.get("character"):
            faces = self._faces_small = {}
            self._face_for = self.config.get("character")
        if size in faces:
            return faces[size]
        try:
            from .tray import icon_image
            image = icon_image(self.app.characters, str(self.config.get("character",
                                                                         "watermelon")), size)
            faces[size] = ImageTk.PhotoImage(image) if image is not None else None
        except Exception:
            faces[size] = None
        return faces[size]

    def _start_selection_watch(self) -> None:
        """Delegate work beside selected text (ui/selection.py) - Windows. The shortcuts and the
        panels first: after that line they were Windows' too, and a Mac had neither (2026-11-05)."""
        from . import selection
        self._start_shortcuts()
        self._start_avatar_buttons()
        # Two fingers on a touch screen: handed on, as it is heard within the system's message.
        from . import on_top
        on_top.PINCH[:] = [lambda step: self.queue.put(("call", lambda: zoom_popups(
            self, sizes.popup_zoom(self.config) + step)))]
        if not selection.available():
            return
        self._selection = selection.SelectionWatch(
            on_selection=lambda said: self.queue.put(("selection", said)),
            on_press=lambda x, y: self.queue.put(("selection_press", (x, y))),
            # Settings > Shortcuts: the button can be turned off.
            wanted=lambda: bool(self.app.config.get("delegate_selection_button", True)))
        self._selection.start()

    def _start_avatar_buttons(self) -> None:
        """The panels of the avatar's menu made ahead. NO VINE, NO BUTTONS OUT OF THE SIDE (the user,
        2026-11-09: "不需要出来藤蔓(那两个logo也不要了)"): in a side, half of it comes out under the
        pointer, and a click on it is the chat, two the Overview (`_peek_on`, `_on_click`)."""
        self._quick_panels: Dict[str, Any] = {}
        self._chat_vine = None
        try:
            # AFTER THE START, NOT IN IT (the user's log, 2026-11-20 20:15: "did not answer for 1.0s, 2s after it
            # appeared ... _prepare_panels"): at 1.5 s the start's threads were still loading and the window waited
            # on them; made once it is all up, one panel at a time, each when the window is idle.
            self.root.after(PANELS_MADE_AFTER_MS, self._prepare_panels)
        except Exception:                    # the avatar goes on without them
            log.exception("the avatar's panels could not be made ahead")

    def _vine_side(self) -> str:
        """The side the vine comes out of. In a side the leaves, the vine and its button stay on
        the screen; HIDDEN, ALL OF IT GOES (the user, 2026-11-05), and it comes back as it was."""
        return "" if self.character_hidden else getattr(self, "_tucked", "")

    def _prepare_panels(self, which: str = "todo") -> None:
        """Both panels made out of sight, once the start is over - one, then the other when idle."""
        try:
            self._panel(which).prepare()
        except Exception:                    # made at the press, then
            log.warning("a panel could not be made ahead", exc_info=True)
        if which == "todo":
            try:
                self.root.after(400, lambda: self.root.after_idle(lambda: self._prepare_panels("collection")))
            except Exception:                # made at the press, then
                pass

    def _quick_panel(self, which: str) -> None:
        """A list, opened or closed: drawn here at once, never the page. ONE AT A TIME
        (the user, 2026-11-04): what was open is closed, and does not come back."""
        for other, one in self._quick_panels.items():
            if other != which:
                one.close()
        chat = getattr(self, "_chat_bubble", None)
        if chat is not None and chat.is_open:
            chat.close()
        self._panel(which).toggle(self._beside_box())

    def _panel(self, which: str) -> Any:
        from .avatar_buttons import QuickPanel
        panel = self._quick_panels.get(which)
        if panel is None:
            todo, waiting = which == "todo", which == "waiting"
            approving = which == "approvals"
            group = {"label": self._words("+ Add group"), "keys": ["name"],
                     "fields": [self._words("The new group's name")],
                     "path": "/api/lists/groups/add", "fixed": {"list": which}}
            adds = [] if waiting or approving else [group]
            if todo:
                adds.insert(0, {"label": self._words("+ Add a to-do"), "keys": ["text", "detail"],
                                "fields": [self._words("What to do"),
                                           self._words("Details (optional)")],
                                "path": "/api/todos/add"})
            panel = self._quick_panels[which] = QuickPanel(
                self.root, self._words("To do" if todo else "Waiting notifications" if waiting
                                       else "For your approval" if approving else "Collections"),
                lambda: self.app.quick_sections(which),
                lambda fn: self.queue.put(("call", fn)), self._words, self._popup_font(),
                self._words("Nothing is waiting." if waiting else
                            "Nothing is waiting for your approval." if approving else "Nothing yet."),
                searches=True, act=self._panel_act,
                zoom=sizes.popup_zoom(self.config), on_zoom=lambda zoom: zoom_popups(self, zoom),
                adds=adds, closes_emptied=waiting or approving,
                on_open=self._waiting_list_open if waiting else None,
                size=self.config.get(f"quick_panel_size_{which}", None) or None,
                on_size=lambda size: self.config.set(f"quick_panel_size_{which}",
                                                     [int(size[0]), int(size[1])], internal=True))
        return panel

    def _panel_act(self, action: Dict[str, Any], done: Any) -> None:
        """What a panel's row offers, as the Overview does it - off Tk's thread. To
        delegate opens the Delegate work box; what came of it, its own program. A notification
        answered in the waiting list: not counted beside the avatar from the press, and the line
        drawn again the moment its answer is kept."""
        notification = str((action.get("body") or {}).get("id") or "") \
            if str(action.get("path") or "").startswith("/api/notifications/") else ""
        if notification:
            if not hasattr(self, "_answering"):
                self._answering = {}
            self._answering.setdefault(notification, "list")

        def work() -> None:
            try:
                if action["key"] == "delegate":
                    draft = self.app.drafts.create(text=action["text"], source="todo",
                                                   options={"todo": action["todo"]}, write_first=True)
                    self.queue.put(("call", lambda: self.open_box(
                        f"/?solo=delegate#delegate={draft['id']}")))
                elif action["key"] == "read":
                    webbrowser.open(action["url"])
                else:
                    self.app.local_post(action["path"], action["body"])
            except Exception as exc:
                log.warning("what a panel asked could not be done", exc_info=True)
                said = str(exc)[:300]           # `exc` is gone once the except ends
                self.queue.put(("call", lambda: self.notify_bubble(said)))
            finally:
                if notification:
                    # Kept (or not): counted as the database now says, at once.
                    getattr(self, "_answering", {}).pop(notification, None)
                    self.on_main_thread(self._show_waiting)
                done()
        threading.Thread(target=work, name="panel-act", daemon=True).start()

    def _beside_box(self) -> tuple:
        """What a window opened beside the avatar keeps clear of: the avatar and the button
        on its vine."""
        box = self._avatar_box()
        vine = getattr(self, "_chat_vine", None)
        return vine.around(box) if vine is not None else box

    def _start_shortcuts(self) -> None:
        """The keyboard shortcuts (Settings > Shortcuts) - held while the
        agent runs; the page changes them through `app.shortcuts`."""
        from ..platform_layer.hotkeys import Shortcuts
        try:
            self.app.shortcuts = Shortcuts(self.app.config,
                                           {"delegate_work": self._delegate_by_shortcut,
                                            "open_chat": self._chat_by_shortcut,
                                            "side_panel": self._side_panel_by_shortcut,
                                            "board_snip": self._snip_by_shortcut},
                                           on_main=self._on_the_main_thread)
            self.app.shortcuts.start()
        except Exception:
            log.exception("the keyboard shortcuts could not be held")

    def _on_the_main_thread(self, work: Any) -> None:
        if threading.current_thread() is threading.main_thread():
            work()
        else:
            self.queue.put(("call", work))

    def _delegate_by_shortcut(self) -> None:
        """The shortcut pressed: the quick Delegate work box, empty, on the
        screen at once - a title and a description to write (ui/quick_delegate.py);
        pressed again while it is open, it closes. Nothing is read from the program in front."""
        self.queue.put(("call", self._show_quick_delegate))

    def _chat_by_shortcut(self) -> None:
        """The shortcut pressed: the chat bubble beside the avatar; THE SAME KEYS CLOSE IT, as its
        x does - not minimise it (the user, 2026-11-08: "打开chat的快捷键再嗯一下是关掉（而不是最小化)"):
        what was written and not sent kept, the next one a new conversation."""
        self.queue.put(("call", self._chat_open_or_closed))

    def _snip_by_shortcut(self) -> None:
        """A PART OF THE SCREEN INTO THE SMALL SCREEN (the user, 2026-11-17: "能不能我点一个快捷键，我能把某个矩形框的内容拖到
        image里面"): the screen frozen and a part of it cut out (ui/snip.py, a process of its own), then the side panel
        opened with it in its small screen. Off Tk's thread - the cut-out waits for the user."""
        self._off_this_thread(lambda: DesktopAgent._snip_into_the_board(self), "board-snip")

    #: How long a cut-out is waited for.
    SNIP_SECONDS = 300
    #: Cut-outs kept for the panel to take, at most.
    SNIPS_KEPT = 5

    def _snip_into_the_board(self) -> None:
        import subprocess
        from pathlib import Path
        from ..common.privacy_folders import why_no_picture
        from ..common.events.models import new_id
        from . import snip
        side = getattr(self.app, "side_events", None)
        held = why_no_picture(self.config)
        if held:                                   # NOTHING OF THE SCREEN while private (privacy_folders.py)
            if side is not None:
                side.tell("board", error=f"No picture of the screen now: {held}")
            return
        if getattr(self, "_snipping", False):
            return
        self._snipping = True
        out = Path(self.app.paths.data_dir) / "snips" / f"{new_id('snip')}.png"
        data = b""
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            child = subprocess.Popen(snip.command(out), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:                                   # the keys pressed: this process may hand the front to it
                import ctypes
                ctypes.windll.user32.AllowSetForegroundWindow(child.pid)
            except Exception:
                pass
            child.wait(timeout=self.SNIP_SECONDS)
            if out.exists():
                data = out.read_bytes()
        except Exception:
            log.info("the screen could not be cut", exc_info=True)
        finally:
            self._snipping = False
            try:
                out.unlink()
            except OSError:
                pass
        if not data:
            return                                 # Esc: nothing
        inbox = getattr(self.app, "board_inbox", None)
        if inbox is None:
            inbox = self.app.board_inbox = {}
        ident = new_id("cut")
        inbox[ident] = data
        while len(inbox) > self.SNIPS_KEPT:
            inbox.pop(next(iter(inbox)))
        # The panel in front - opened if it was not - and told to take it into its small screen.
        if not self._notes_open_now():
            self.queue.put(("call", self.open_notifications))
        if side is not None:
            side.tell("board", cut=ident)

    def _side_panel_by_shortcut(self) -> None:
        """THE SIDE PANEL AT A KEY (the user, 2026-11-16): open, it is put away; put away or never opened,
        it comes in - at once, on the window's thread."""
        self.queue.put(("call", self._side_panel_in_or_out))

    def _side_panel_in_or_out(self) -> None:
        # PINNED, ITS KEYS GO TO ITS CHAT (the user, 2026-11-17: "当我sidebar pin的时候，打开sidebar的快捷键的功能变成焦点
        # 切换到sidebar的chat上（而不是展开/收起sidebar)" - "包括chat的快捷键也是"): shown, the keys to its box; put
        # away, back with the keys in its box. Not pinned: in, or away, as before.
        pinned = bool(getattr(self, "config", None) is not None and self.config.get("side_panel_pinned", False))
        if self._notes_open_now():
            if pinned and sys.platform == "win32":
                self._off_this_thread(lambda: DesktopAgent._keys_to_the_side_chat(self), "side-panel-keys")
                return
            self._off_this_thread(self.hide_notes_panel, "side-panel-away")
        else:
            self.open_notifications()
            if pinned and getattr(self, "root", None) is not None:
                # Once it is in: the keys to its box.
                self.root.after(self.KEYS_AFTER_OPEN_MS, lambda: self._off_this_thread(
                    lambda: DesktopAgent._keys_to_the_side_chat(self), "side-panel-keys"))

    #: How long after it is opened by its keys the keys are put in its box.
    KEYS_AFTER_OPEN_MS = 350

    def _keys_to_the_side_chat(self) -> None:
        """The side panel in front, where the user types: its window the active one and its page given the keys (by
        its host, where the window is - panel_host.focus_panel), and the page told to put them in its box."""
        from ..platform_layer import app_window
        host = getattr(self, "panel_host", None)
        focused = False
        try:
            focused = bool(host is not None and host.focus(self._notes_titles()))
        except Exception:
            log.debug("the side panel's host could not give it the keys", exc_info=True)
        if not focused:
            app_window.bring_back_titled(self._notes_titles())
        DesktopAgent._tell_the_panel(self, peeking=False, by_user=True, focus=True)

    def _side_panel_here(self) -> bool:
        """Whether the side panel - the chat now - opens here: Windows, or a Mac with its own panels."""
        if os.environ.get("PA_NO_DIALOGS"):
            return False
        return sys.platform == "win32" or self._notes_on_a_mac()

    def _chat_open_or_closed(self) -> None:
        if DesktopAgent._side_panel_here(self):          # the chat is the side panel: in, or away
            self._side_panel_in_or_out()
            return
        bubble = getattr(self, "_chat_bubble", None)
        if bubble is not None and bubble.is_open:
            bubble.close()
            vine = getattr(self, "_chat_vine", None)
            if vine is not None:
                vine.place()
        else:
            self.open_chat_bubble()

    def toggle_chat_bubble(self) -> None:
        bubble = getattr(self, "_chat_bubble", None)
        if bubble is not None and bubble.is_open and not bubble.minimized:
            bubble.minimize()
        else:
            self.open_chat_bubble()

    def _show_quick_delegate(self) -> None:
        """Opened by the shortcut - and closed by it, pressed again (QuickDelegate.toggle)."""
        if getattr(self, "_quick", None) is None:
            from .quick_delegate import QuickDelegate
            self._quick = QuickDelegate(self.root, self.app, self.open_box, self._words,
                                        lambda fn: self.queue.put(("call", fn)))
        self._quick.toggle()

    #: THE ICON BESIDE SELECTED TEXT (the user, 2026-11-01: "像搜狗输入法, 选择
    #: 一段文字弹出来...用一个西瓜图标，然后点击弹出delegate work菜单"): the
    #: avatar's face alone, this big, for this long - clicked, its menu, for
    #: longer.
    ICON_SIZE, ICON_SECONDS, MENU_SECONDS = 24, 6, 12

    def _show_delegate_button(self, said: Dict[str, Any]) -> None:
        """The avatar's face, small, beside the selection, for a few seconds
        - it does not take the focus from the program the user is in, nor
        cover what they read. Clicked, it opens into its menu: Delegate work,
        Learn (`_open_selection_menu`)."""
        self._hide_delegate_button()
        # A BROWSER WITH THE AGENT'S EXTENSION shows its own icon in its pages
        # (resources/browser_extension/content.js) - one icon, not two.
        app = getattr(self, "app", None)
        if app is not None and str(said.get("app") or "").lower() in _extension_browsers(app):
            return
        button_window = tk.Toplevel(self.root)
        button_window.overrideredirect(True)
        button_window.attributes("-topmost", True)
        face = self._delegate_face(self.ICON_SIZE)
        font = self._popup_font()
        icon = tk.Button(button_window, relief="flat", bd=0, padx=3, pady=3, bg="#1c2028",
                         activebackground="#3d6be5", cursor="hand2",
                         command=lambda: self._open_selection_menu(said),
                         **({"image": face} if face else
                            {"text": self._words("Delegate work"), "fg": "#e6e9f0",
                             "font": (font[0], max(8, font[1] - 1))}))
        icon.pack()
        self._delegate_button = button_window
        self._delegate_held = said
        self._place_beside_selection(button_window, said)
        from .selection import no_activate
        no_activate(button_window)
        self._delegate_job = self.root.after(self.ICON_SECONDS * 1000, self._hide_delegate_button)

    def _place_beside_selection(self, window: Any, said: Dict[str, Any]) -> None:
        """Below and right of where the selection ended - or, RIGHT-CLICKED,
        left of the program's own menu, which opens to the right and below."""
        window.update_idletasks()
        x, y = int(said.get("x", 0)), int(said.get("y", 0))
        if said.get("right"):
            window.geometry(f"+{max(0, x - window.winfo_reqwidth() - 12)}+{max(0, y - 4)}")
        else:
            window.geometry(f"+{x + 14}+{y + 18}")
        from .on_top import stay_on_top
        stay_on_top(window)                  # on a Mac, asked before it was laid out: lost

    def _open_selection_menu(self, said: Dict[str, Any]) -> None:
        """The icon clicked: its menu, in its place - still not taking the
        focus from the program the user is in."""
        window = getattr(self, "_delegate_button", None)
        if window is None:
            return
        for child in list(window.winfo_children()):
            child.destroy()
        font = self._popup_font()
        face = self._delegate_face()
        style = {"font": (font[0], max(8, font[1] - 1)), "relief": "flat", "anchor": "w",
                 "padx": 10, "pady": 4, "bg": "#1c2028", "fg": "#e6e9f0",
                 "activebackground": "#3d6be5", "activeforeground": "#ffffff", "bd": 0}
        tk.Button(window, text=self._words("Delegate work"),
                  command=lambda: self._delegate_selection(said),
                  **({"image": face, "compound": "right"} if face else {}),
                  **style).pack(fill="x")
        # LEARN (learning/plan.py): the text - and the page it was selected
        # on - put in the learning list.
        tk.Button(window, text=self._words("Learn"),
                  command=lambda: self._learn_selection(said), **style).pack(fill="x")
        self._place_beside_selection(window, said)
        job = getattr(self, "_delegate_job", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._delegate_job = self.root.after(self.MENU_SECONDS * 1000,
                                             self._hide_delegate_button)

    def _hide_delegate_button(self) -> None:
        window = getattr(self, "_delegate_button", None)
        self._delegate_button = None
        self._delegate_held = None                 # the text goes with it
        job = getattr(self, "_delegate_job", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
            self._delegate_job = None
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:
                pass

    def _pressed_elsewhere(self, x: int, y: int) -> None:
        window = getattr(self, "_delegate_button", None)
        if window is None:
            return
        try:
            left, top = window.winfo_rootx(), window.winfo_rooty()
            inside = left <= x <= left + window.winfo_width() and \
                top <= y <= top + window.winfo_height()
        except tk.TclError:
            inside = False
        if not inside:
            self._hide_delegate_button()

    def _delegate_selection(self, said: Dict[str, Any]) -> None:
        """Clicked: the text handed over, with the window it came from - and
        its plan opened, in a window of its own."""
        self._hide_delegate_button()

        def hand_over() -> None:
            try:
                draft = self.app.drafts.create(
                    text=str(said.get("text") or ""), source="selection",
                    window={"title": said.get("title") or "", "app": said.get("app") or ""},
                    write_first=True)
                self.queue.put(("call", lambda: self.open_box(
                    f"/?solo=delegate#delegate={draft['id']}")))
            except Exception:
                log.exception("could not hand the selected text over")
        threading.Thread(target=hand_over, name="delegate-selection", daemon=True).start()

    def _learn_selection(self, said: Dict[str, Any]) -> None:
        """Clicked: the text handed to the learning, with the window it came
        from - and its learning plan opened, in a window of its own."""
        self._hide_delegate_button()

        def hand_over() -> None:
            try:
                draft = self.app.learning.planner.start({
                    "kind": "text", "text": str(said.get("text") or ""),
                    "window": {"title": said.get("title") or "", "app": said.get("app") or ""},
                    # Learned at once, no plan to check (the user, 2026-10-29).
                    "auto": True})
                self.queue.put(("call", lambda: self.open_box(
                    f"/?solo=learn#learn={draft['id']}")))
            except Exception:
                log.exception("could not hand the selected text to the learning")
        threading.Thread(target=hand_over, name="learn-selection", daemon=True).start()

    def avatar_in_play(self, on: bool) -> None:
        """THE AVATAR WALKS IN THE WORLD INSTEAD (the user, 2026-11-17: "logo就不在显示在屏幕上，但是会显示在游戏视图里（主角的
        avatar, 有行走动画什么的)"): the Play view open, the character off the screen - back when it shuts, unless the user
        hid it themselves meanwhile (then it stays as they left it). On Tk's thread."""
        if on and not self.character_hidden:
            self.toggle_character(remember=False)
            self._hidden_for_play = True
        elif not on and getattr(self, "_hidden_for_play", False):
            self._hidden_for_play = False
            if self.character_hidden:
                self.toggle_character(remember=False)

    def toggle_character(self, remember: bool = True) -> None:
        """Show or hide the character on screen; the agent keeps running,
        and its icon beside the clock stays.

        WHICH IT IS, KEPT HERE, not read back from the window: the tray's
        menu asked Tk from the tray's own thread, which Tk does not answer
        reliably, and Windows builds the menu's words once - so the menu
        said "Hide" while the avatar was hidden and "Show" while it was
        there. The menu is built again after every change."""
        try:
            if sys.platform == "darwin":
                self._toggle_on_a_mac()
            elif self.character_hidden:
                self.agent_window.deiconify()
                self.agent_window.attributes("-topmost", True)
                self.character_hidden = False
                # Shown again where it can be seen: a screen it was on may
                # have gone.
                self._keep_on_screen()
                self.agent_window.lift()
            else:
                self.agent_window.withdraw()
                self.character_hidden = True
        except Exception:        # pragma: no cover - the window has gone
            log.debug("could not show or hide the character", exc_info=True)
        if remember:                               # the user's own choice, kept for the next start
            try:
                self.config.set("avatar_on_screen", not self.character_hidden, internal=True)
            except Exception:
                log.debug("whether the avatar is on the screen could not be kept", exc_info=True)
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.refresh_menu()
        vine = getattr(self, "_chat_vine", None)
        if vine is not None:
            vine.place()                         # at once, not at its next look
        try:
            self._dock_menu()
        except Exception:        # its words a moment out of date: nothing else is the worse
            log.debug("the Dock's menu could not be told", exc_info=True)

    def _toggle_on_a_mac(self) -> None:
        """Hidden and shown on a Mac without withdrawing it: a borderless
        window withdrawn there did not come back when shown again - Show
        showed nothing. It is made clear and moved off every screen, and
        shown again where it was."""
        window = self.agent_window
        if self.character_hidden:
            window.geometry(getattr(self, "_shown_at", "") or window.geometry())
            window.attributes("-alpha", getattr(self, "_shown_alpha", 1.0))
            window.attributes("-topmost", True)
            self.character_hidden = False
            self._keep_on_screen()
            window.lift()
        else:
            self._shown_at = window.geometry()
            self._shown_alpha = float(window.attributes("-alpha"))
            window.attributes("-alpha", 0.0)
            window.geometry(f"+{-20 * self.size}+{-20 * self.size}")
            self.character_hidden = True

    #: How often the avatar is looked at to see that it is on a screen.
    ON_SCREEN_EVERY_MS = 3000
    BLOCKERS_EVERY_MS = 3000

    def _keep_on_screen(self) -> None:
        """Back where it can be seen when the screen it was on has gone.

        Moved from an external display to the laptop's own, the avatar
        stayed where the external one had been: shown, as far as the
        window knew, and nowhere anyone could see - so Hide and Show only
        hid and showed it there. It goes back to the bottom right of the
        main screen."""
        if self.character_hidden:
            return
        try:
            self.agent_window.update_idletasks()
            x, y = self.agent_window.winfo_x(), self.agent_window.winfo_y()
            # AS MUCH OF IT AS SHOWS IN A SIDE is on a screen: beside the side, its middle may
            # well be past it (the user, 2026-11-04: left at the side, it was taken for lost and
            # sent home), and in the side it is meant to be. Less is lost (2026-11-05: a corner
            # of a few pixels counted, after a change of screen): a square that large at one of
            # its corners, asked by the square's inner corner.
            from .avatar_tuck import PEEK
            near = max(1, int(self.size * PEEK) - 3)
            far = self.size - 1 - near
            if not any(_on_a_screen(self.agent_window, x + across, y + down)
                       for across in (near, far) for down in (near, far)):
                log.info("the avatar was off every screen (at %s,%s): put back", x, y)
                self._place_bottom_right()
                self.agent_window.deiconify()
                self.agent_window.lift()
        except Exception:        # pragma: no cover - the window has gone
            log.debug("could not keep the avatar on a screen", exc_info=True)

    def _watch_the_screens(self) -> None:
        self._keep_on_screen()
        if not self.character_hidden:
            from .on_top import back_on_top
            try:
                if back_on_top(self.agent_window):
                    log.info("the avatar had fallen under another window: put back on top")
            except Exception:    # pragma: no cover - the window has gone
                log.debug("could not keep the avatar on top", exc_info=True)
        self.root.after(self.ON_SCREEN_EVERY_MS, self._watch_the_screens)

    @staticmethod
    def _off_this_thread(what, name: str = "open-page") -> None:
        """OPENED OFF THIS WINDOW'S THREAD (measured 2026-10-26): the first
        page opened looks for every browser on the PATH - seconds with the
        disk busy - and this window did not answer meanwhile, so a double
        click seemed not to have been taken, and was made again."""
        def run() -> None:
            try:
                what()
            except Exception:                       # pragma: no cover - logged
                log.warning("could not open the page", exc_info=True)
        threading.Thread(target=run, name=name, daemon=True).start()

    def _warm_up_when_idle(self, tries: int = 20) -> None:
        """The folder dialog's warm-up, once nothing has been touched for a
        while - asked again each half minute, and given up after ten."""
        ask = getattr(getattr(self.app.platform, "activity", None), "seconds_since_input", None)
        try:
            idle = float(ask()) if callable(ask) else 0.0
        except Exception:
            idle = 0.0
        if idle >= 20:
            chooser.warm_up(self.root)
        elif tries > 0:
            self.root.after(30000, lambda: self._warm_up_when_idle(tries - 1))

    def _open_workspace(self) -> None:
        self._off_this_thread(lambda: webbrowser.open(self.app.policy.workspace_root.as_uri()))

    def open_url(self, fragment: str = "/") -> None:
        # Through a sign-in link, every time: the page's requests need the
        # browser signed in (api/access.py), and a link made now is good for
        # this one opening and for two minutes - so the tray, the menu, the
        # chat and every "#tab" open the page signed in, and none of them
        # leaves a link behind that would sign anyone else in later.
        # A TAB OF THE PAGE, IN THE APP'S OWN WINDOW (the user, 2026-11-13: "现在open the report打开的
        # 是网站，而不是那个app，应该改成在app端打开") - the report as the overview: brought back where
        # it is open, and turned to that tab; opened at it where it is not.
        if re.fullmatch(r"/#[a-z_]+", fragment):
            self._off_this_thread(lambda: self._open_the_page(fragment))
            return
        self._off_this_thread(lambda: webbrowser.open(self.app.sign_in_url(fragment)))

    def _open_the_page(self, fragment: str = "/#overview") -> None:
        """THE PAGE IN ONE WINDOW, BROUGHT BACK AS IT WAS LEFT (the user, 2026-11-05: each double
        click was one more tab) - turned to `fragment`'s tab when it is not the overview (the page
        asks for it as it comes to the front: `page_wants`). No browser to make that window: a tab,
        as before."""
        from . import mac_panel
        from ..platform_layer import app_window
        tab = fragment[2:] if fragment.startswith("/#") else ""
        # Asked for BEFORE the window comes to the front: the page asks as it does.
        self.app.page_wants = {"tab": tab, "at": time.time()} if tab and tab != "overview" else None
        if sys.platform == "darwin" and mac_panel.available():
            # Apple's WebKit, whatever browser the Mac has: on the main thread.
            self.queue.put(("call", lambda: self._open_the_page_on_a_mac(fragment)))
            return
        if not app_window.OPENING.acquire(blocking=False):
            return                                   # it is on its way
        try:
            if app_window.bring_back_the_page():
                return
            url = self.app.sign_in_url(fragment)
            if app_window.open_the_page(url, self.app.paths.data_dir / app_window.PAGE_PROFILE):
                app_window.wait_for_the_page()
            else:
                webbrowser.open(url)
        finally:
            app_window.OPENING.release()

    def open_box(self, fragment: str) -> None:
        """The Delegate work box alone, in a window of its own - not the
        whole page in a browser tab (platform_layer/app_window.py)."""
        from ..platform_layer import app_window
        self._off_this_thread(lambda: app_window.open_page(
            self.app.sign_in_url(fragment), self.app.paths.data_dir / app_window.PROFILE))

    def _open_the_page_on_a_mac(self, fragment: str = "/#overview") -> None:
        from . import mac_panel
        if mac_panel.bring_back_the_page():
            return
        url = self.app.sign_in_url(fragment)
        if not mac_panel.open_the_page(url):
            webbrowser.open(url)

    # -- conversation -------------------------------------------------------
    def open_chat_bubble(self) -> None:
        """A CONVERSATION IN A BUBBLE BESIDE IT (the user, 2026-11-01: a single
        click is not the chat page - a bubble to type in; ui/chat_bubble.py),
        on the same conversation the page has. Open already: written in again."""
        for panel in getattr(self, "_quick_panels", {}).values():
            panel.close()                    # one at a time, however it was opened
        pictures = getattr(self.app, "front_pictures", None)
        if pictures is not None:
            # Before anything of this covers it - and, the chat's, its words not read (the user,
            # 2026-11-08: "chat的这个可以跳过。不需要识别"): the picture goes as it was taken.
            pictures.take(words=False)
        # THE SIDE PANEL IS THE CHAT (the user, 2026-11-16: "你现在的chat框取消，之后sidebar就是一个聊天框（当然可以有
        # 那个image panel), 所以不需要单独的chat"): where it can be opened - Windows, a Mac - it opens instead of the
        # bubble; the bubble only where there is none.
        if DesktopAgent._side_panel_here(self):
            self.open_notifications()
            return
        from ..agent.front_screen import front_now
        from .chat_bubble import ChatBubble
        bubble = getattr(self, "_chat_bubble", None)
        if bubble is not None and bubble.is_open:
            bubble.show()
            return
        font = self._popup_font()

        def full(conversation: Optional[str]) -> None:
            if conversation:
                self._conversation_id = conversation
            self.open_chat()
        # ITS SIZE as the user left it last (the user, 2026-11-01).
        kept = sizes.kept(self.root, self.config.get("chat_bubble_size", []) or [])

        def sized(size: tuple) -> None:
            try:
                self.config.set("chat_bubble_size", sizes.share(self.root, size), internal=True)
            except Exception:                    # pragma: no cover - this one keeps it
                log.debug("the chat bubble's size could not be kept", exc_info=True)
        # ITS ZOOM (Ctrl + / Ctrl -): every window's, a hidden setting (`zoom_popups`).
        from .chat_bubble import ZOOM_START
        zoom = ZOOM_START + sizes.popup_zoom(self.config)

        def zoomed(step: int) -> None:
            zoom_popups(self, step - ZOOM_START)
        self._chat_bubble = ChatBubble(
            self.root, name=self.app.avatar_name(), family=font[0], size=font[1],
            send=lambda *said, **more: self._said_in_the_bubble(*said, **more),
            front=lambda: front_now(self.app.db, self.app.config),
            on_late=lambda *said: self.app.chat_reply_notice(*said),
            draft=self._chat_draft(), on_draft=self._keep_chat_draft,
            streams=True, turn=getattr(self, "_chat_turn", None),
            files=getattr(self.app, "chat_files", None),
            post=lambda fn: self.queue.put(("call", fn)), open_full=full, words=self._words,
            # A NEW CONVERSATION each time one is opened: the last was closed
            # (the user, 2026-11-03: "点击关闭之后再打开就是一个新的session").
            conversation_id=None,
            on_conversation=lambda conversation: setattr(self, "_conversation_id", conversation),
            kept_size=kept or None, on_size=sized, zoom=zoom, on_zoom=zoomed)
        self._chat_bubble.show(near=self._beside_box())

    def _chat_turn(self, text: Any, conversation: Optional[str] = None, files: Any = None,
                   mentions: Any = None, on_done: Any = None) -> Any:
        """A bubble's message at two speeds (agent/two_speed.py); asked with
        None, whether they are on."""
        two = self.app.two_speed
        if text is None:
            return two.on
        note, seen = self._front_for_chat()
        return two.send(text, conversation, files, mentions, on_done, front=note, seen=seen)

    def _chat_draft(self) -> str:
        """What was written in the bubble and not sent, through a restart too - in the
        database, not among the settings; one handed over and not yet written, from there."""
        waiting = window_writes(self).waiting("chat.draft", None)
        if waiting is not None:
            return str(waiting)
        row = self.app.db.query_one("SELECT value FROM kv WHERE key = 'chat.draft'")
        return str(row["value"]) if row and row["value"] else ""

    def _keep_chat_draft(self, text: str) -> None:
        """Handed over, never written here: the bubble goes on while it waits its turn (later_writes.py)."""
        window_writes(self).put("chat.draft", text)

    def _write_kv(self, key: str, value: Any) -> None:
        """One of the window's writes, on the thread they are written on."""
        self.app.db.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) "
                            "DO UPDATE SET value = excluded.value", (key, value))

    def _said_in_the_bubble(self, text: str, conversation: Optional[str], files: Any = None,
                            mentions: Any = None, on_text: Any = None) -> Any:
        note, seen = self._front_for_chat()
        return self.app.agent.handle_message(text, conversation, attachments=files or None,
                                             mentions=mentions or None, on_text=on_text,
                                             front=note, seen=seen, chat=True)

    def _front_for_chat(self, told: Optional[Dict[str, Any]] = None) -> tuple:
        """What goes with a message: what the bubble says, and the picture of the
        screen with the words read in it - unless taken off there, or the user
        has moved on from what the picture shows. (note, pictures). `told` (the side panel's, which is
        in another process): filled with what the bubble was shown - "picture", the picture as PNG bytes,
        "how" it went ("sent", "words", "not") and "why", or "withheld" and why none was taken."""
        from ..agent.front_screen import MOST_TEXT, front_now, note_of
        try:
            said = front_now(self.app.db, self.app.config)
        except Exception:                # a message goes, whatever this could not read
            log.info("what is in front of the user could not be read", exc_info=True)
            return "", []
        bubble = getattr(self, "_chat_bubble", None) if told is None else None
        off = getattr(bubble, "front_off", "")
        if off and said.get("title") == off:
            return "", []
        pictures = getattr(self.app, "front_pictures", None)
        if pictures is None:
            return note_of(said), []
        # WHAT GOES WITH IT, SHOWN UNDER THE MESSAGE AT ONCE (the user, 2026-11-08: "每次发出去的时候，
        # 显示当前发给模型的屏幕截屏长什么样"; "你等识别，你可以先把图片截图发上来") - small, before its
        # words are read; then said whether it went, and how.
        again = getattr(pictures, "again_for_a_message", None)
        if callable(again):
            again()
        showing = bubble is not None and getattr(self, "queue", None) is not None
        preview = pictures.preview() if showing and callable(
            getattr(pictures, "preview", None)) else None
        if preview is not None:
            self.queue.put(("call", lambda: bubble.picture_sent(preview)))
        taking = getattr(pictures, "for_a_message", None)
        shot = (taking if callable(taking) else pictures.latest)(title=said.get("title", ""))
        if shot.get("words"):
            said = dict(said, picture_words=shot["words"][:MOST_TEXT])
        if shot.get("pointer"):
            said = dict(said, pointer=shot["pointer"])    # where the mouse was (2026-11-13)
        seen = pictures.blocks(shot)
        if told is not None:
            DesktopAgent._tell_the_side_panel(self, told, pictures, shot, seen)
        if showing:
            sees = getattr(self, "_chat_model_sees", lambda: True)()
            if preview is None and seen and shot.get("path"):
                path = str(shot["path"])
                self.queue.put(("call", lambda: bubble.picture_sent(path)))
            if preview is not None or seen:
                how = ("sent" if sees else "words") if seen else "not"
                why = str(getattr(pictures, "why", "") or "")
                if how == "words" and not shot.get("words"):
                    # Nothing read of it (the chat's): a model that sees no pictures has none.
                    how, why = "not", "this model sees no pictures"
                self.queue.put(("call", lambda: bubble.picture_settled(how, why)))
            else:
                # NONE TAKEN, AND WHY (the user, 2026-11-12: "加个注释，这个时候没图是因为private mode,
                # 或者privacy directory保护").
                from ..common.privacy_folders import withheld
                held = str(getattr(pictures, "why", "") or "")
                if withheld(held) and callable(getattr(bubble, "picture_withheld", None)):
                    self.queue.put(("call", lambda: bubble.picture_withheld(held)))
        return note_of(said), seen

    def _tell_the_side_panel(self, told: Dict[str, Any], pictures: Any, shot: Dict[str, Any], seen: Any) -> None:
        """THE PICTURE THAT WENT WITH A MESSAGE FROM THE SIDE PANEL, as the bubble showed it (the user, 2026-11-16:
        "你现在那些之前的feature, 包括当前屏幕，context什么的都要实现"): the picture small, as PNG bytes - kept in
        memory only, never in a file - and whether it went, and why not."""
        if not seen:
            from ..common.privacy_folders import withheld
            held = str(getattr(pictures, "why", "") or "")
            if withheld(held):
                told["withheld"] = held
            return
        sees = DesktopAgent._chat_model_sees(self)
        how = "sent" if sees else "words"
        why = str(getattr(pictures, "why", "") or "")
        if how == "words" and not shot.get("words"):
            how, why = "not", "this model sees no pictures"
        told["how"], told["why"] = how, why
        try:
            import io

            from PIL import Image
            path = shot.get("path")
            if path:
                with Image.open(str(path)) as found:
                    view = found.convert("RGB")
                view.thumbnail((SIDE_PICTURE_SIDE, SIDE_PICTURE_SIDE))
                kept = io.BytesIO()
                view.save(kept, "PNG", optimize=True)
                told["picture"] = kept.getvalue()
        except Exception:
            log.debug("the picture sent could not be kept for the side panel", exc_info=True)

    def _chat_model_sees(self) -> bool:
        """Whether the chat's first model is given pictures, or their words only."""
        try:
            from ..models.vision import sees_images
            first = next(iter(self.app.models.chain("primary")), None)
            return first is None or sees_images(first.provider_id, first.model_id,
                                                self.app.config)
        except Exception:                        # said as seen: the picture went
            return True

    def _avatar_box(self) -> tuple:
        """Where the avatar is on the screen: (x, y, width, height)."""
        window = self.agent_window
        try:
            return (window.winfo_x(), window.winfo_y(),
                    max(1, window.winfo_width()), max(1, window.winfo_height()))
        except Exception:                        # pragma: no cover - a sketch of one
            return (window.winfo_x(), window.winfo_y(), 1, 1)

    def open_chat(self, initial_message: Optional[str] = None) -> None:
        """Open the conversation interface (section 7.1, as amended).

        The application already has a conversation interface; this opens it in
        a window of its own rather than building a second one in the desktop
        toolkit, which would drift from it and inherit this window's inability
        to take the keyboard.
        """
        query = []
        if self._conversation_id:
            query.append(f"conversation={urllib.parse.quote(self._conversation_id)}")
        if initial_message:
            query.append(f"message={urllib.parse.quote(initial_message)}")
        self.open_url("/chat.html" + ("?" + "&".join(query) if query else ""))

    # -- the small windows the agent puts on screen -------------------------
    def _popup_font(self) -> tuple:
        """The text size for what the agent says and for the hover readout.

        Read each time rather than kept: the user changes it to be able to
        read the thing in front of them, so the next popup should already
        honour it without restarting the application.
        """
        try:
            size = int(self.config.get("popup_font_size", 9))
        except (TypeError, ValueError):
            size = 9
        # A window sized for text cannot survive an absurd value, and the
        # setting is the sort of thing that gets a stray keystroke.
        return ("Segoe UI", sizes.px(self.root, max(7, min(24, size))))

    # -- hover readout (section 7.5) ----------------------------------------
    #: The readout opens once the pointer has rested on the avatar this long
    #: (the user's choice: not at every pass of the pointer).
    HOVER_AFTER_MS = 2000

    def _pointer_on_avatar(self) -> bool:
        """Whether the pointer is over the avatar's window - by where it is,
        not by enter and leave, which come and go while it rests there (the
        see-through pixels round the figure pass the pointer through)."""
        try:
            px, py = self.root.winfo_pointerxy()
            left, top = self.agent_window.winfo_rootx(), self.agent_window.winfo_rooty()
        except Exception:                          # pragma: no cover - window gone
            return False
        return left <= px < left + self.size and top <= py < top + self.size

    def _on_hover_enter(self, _event: Any = None) -> None:
        """The pointer came onto it: its smile begins at once (`_look_now`);
        the readout after `HOVER_AFTER_MS`, if it is still there then and the
        avatar is not being carried."""
        self._look_now()
        if self._hover is not None or getattr(self, "_carried", False) \
                or getattr(self, "_hover_wait", None) is not None:
            return
        self._hover_wait = self.root.after(self.HOVER_AFTER_MS, self._hover_if_resting)

    def _hover_if_resting(self) -> None:
        self._hover_wait = None
        if self._hover is not None or getattr(self, "_carried", False) \
                or not self._pointer_on_avatar():
            return
        self._open_hover()

    def _open_hover(self) -> None:
        window = tk.Toplevel(self.root)
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        frame = tk.Frame(window, bg="#11151f", padx=10, pady=8,
                         highlightbackground="#2b3350", highlightthickness=1)
        frame.pack()
        label = tk.Label(frame, text="", justify="left", anchor="w", bg="#11151f",
                         fg="#e7ebf3", font=self._popup_font())
        label.pack()
        self._hover = window
        self._hover_label = label
        self._refresh_hover()
        from .on_top import stay_on_top
        stay_on_top(window)                  # on a Mac, asked before it was laid out: lost

    def _on_hover_leave(self, _event: Any = None) -> None:
        # A leave while the pointer is still on it is the see-through
        # pixels, not the pointer going: nothing to put away.
        if _event is not None and not getattr(self, "_carried", False) \
                and self._pointer_on_avatar():
            return
        if _event is not None:
            self._look_now()                     # the smile begins to go at once
        wait = getattr(self, "_hover_wait", None)
        if wait is not None:
            try:
                self.root.after_cancel(wait)
            except Exception:                      # pragma: no cover - already gone
                pass
            self._hover_wait = None
        if self._hover_job is not None:
            try:
                self.root.after_cancel(self._hover_job)
            except Exception:                      # pragma: no cover - already gone
                pass
            self._hover_job = None
        window, self._hover = self._hover, None
        if window is not None:
            window.destroy()

    def _refresh_hover(self) -> None:
        """Redraw while the pointer rests - from what was READ ELSEWHERE.

        NEVER ON THIS THREAD. Working out what the agent is doing reads
        more than memory - the state heartbeat's status counts its records
        on disk - and while the agent starts, the starting thread is reading
        those same files. Done here, every second of hovering held up the
        window that owns the pointer, and Windows turned it into the busy
        cursor (the start-up watchdog caught it in the act). So it is read
        on a thread of its own, one reading at a time, and this only shows
        the latest - or that the agent is still starting."""
        if self._hover is None:
            return
        self._kept_worker("hover-reader", self._read_hover)       # one thread kept for it, told
        self._show_hover(getattr(self, "_hover_said", None))
        self._hover_job = self.root.after(1000, self._refresh_hover)

    def _read_hover(self) -> None:
        try:
            said = self._hover_text()
        except Exception:                          # pragma: no cover - mid start
            log.debug("could not read what the agent is doing", exc_info=True)
            said = None
        self.queue.put(("hover", said))

    def _show_hover(self, said: Optional[str]) -> None:
        if self._hover is None:
            return
        name = self.app.avatar_name()
        say = _sayer(self.app)
        if not getattr(self, "_up", False):
            # Up at once, saying so: the window is here before the rest.
            text = (f"{name}\n" + say("Starting\u2026 the heartbeats show here once it is up")
                    if not said else said + "\n" + say("(still starting\u2026)"))
        else:
            text = said or f"{name}\n" + say("Reading what it is doing\u2026")
        try:
            if self._hover_label.cget("text") != text:
                self._hover_label.configure(text=text)
                self._place_hover()
        except Exception:                          # pragma: no cover - window closed
            pass

    def _place_hover(self) -> None:
        """Put the readout where it fits.

        The agent usually sits at a corner, so a fixed offset puts the readout
        off the edge of the screen. Place it beside the agent on whichever side
        has room, and keep it fully on screen.
        """
        window = self._hover
        if window is None:
            return
        window.update_idletasks()
        width = window.winfo_reqwidth()
        height = window.winfo_reqheight()
        screen_width = self.agent_window.winfo_screenwidth()
        screen_height = self.agent_window.winfo_screenheight()
        agent_x = self.agent_window.winfo_rootx()
        agent_y = self.agent_window.winfo_rooty()
        margin = 8

        # Beside the agent, on the side with room for it.
        x = agent_x + self.size + margin
        if x + width > screen_width - margin:
            x = agent_x - width - margin
        x = max(margin, min(x, screen_width - width - margin))

        # Aligned with the agent, moved up only as far as it must be.
        y = agent_y
        if y + height > screen_height - margin:
            y = screen_height - height - margin
        y = max(margin, y)
        window.geometry(f"+{int(x)}+{int(y)}")

    def _words(self, text: str) -> str:
        """One of the application's own texts, in the display language."""
        return _sayer(self.app)(text)

    def _fill(self, key: str, *values: Any) -> str:
        """Words with numbered places, in the display language, filled in."""
        return _filler(self.app)(key, *values)

    def _hover_text(self) -> str:
        state = self.app.agent_state()
        fill = _filler(self.app)
        lines = [str(state.get("agent_name") or fill("Agent"))]
        model = state.get("model") or {}
        verdict = ("" if model.get("available") is None
                   else ("" if model["available"] else f" — {model.get('state')}"))
        lines.append(fill("Model: {0}", f"{model.get('label') or fill('unknown')}{verdict}"))
        # The two of them, the state heartbeat first: it is the one
        # that answers what have I been doing, which is the question
        # somebody hovering over the agent is asking.
        states = state.get("state_heartbeat") or {}
        grouping = _schedule_line(fill("State heartbeat"), states, fill)
        if states.get("running") and states.get("tasks"):
            grouping += "  " + fill("({0} tasks)", states["tasks"])
        lines.append(grouping)
        lines.append(_schedule_line(fill("Heartbeat"), state.get("heartbeat") or {}, fill))
        task = state.get("next_task")
        lines.append(fill("Next task: {0} {1}", task["title"], _in_words(task["in_seconds"], fill))
                     if task else fill("Next task: none scheduled"))
        lines += _work_lines(state.get("work") or [], fill)
        privacy = state.get("privacy") or {}
        if not privacy.get("monitoring_enabled"):
            lines.append(fill("Activity monitoring: off"))
        attention = state.get("needs_attention")
        if attention:
            lines.append(fill("Needs you: sign in to {0} again", attention.get("provider")))
        return chr(10).join(lines)

    def _drain_queue(self) -> None:
        # Asked for 150ms after the last time: anything more is the thread
        # held up - which a folder dialog open on it feels as a stutter.
        now = time.monotonic()
        due = getattr(self, "_drain_due", now)
        chooser.late(now - due)
        self._drain_due = now + 0.15
        # ONE THING THAT FAILS IS SKIPPED, NOT EVERYTHING AFTER IT: a handler
        # that raised used to end this loop - it was never asked again, and
        # every bubble and note after it was silently lost until a restart.
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                try:
                    self._handle(kind, payload)
                except Exception:
                    log.warning("the desktop could not show a %s; going on with the rest",
                                kind, exc_info=True)
        except queue.Empty:
            pass
        finally:
            self.root.after(150, self._drain_queue)

    def _handle(self, kind: str, payload: Any) -> None:
        """One thing from the queue, on the thread that owns the windows."""
        if kind == "call":
            # Work from another thread - the tray's menu - run here,
            # on the thread that owns the windows.
            payload()
        elif kind == "selection":
            self._show_delegate_button(payload)
        elif kind == "selection_press":
            self._pressed_elsewhere(*payload)
        elif kind == "character":
            # The HTTP API runs off Tk's main thread. Queue the reload
            # so PhotoImage objects are still created by Tk itself.
            self._frames.clear()
            self._frame_index = 0
            self._load_character()
            self._enable_native_avatar()
            self._look_now()                     # worn at once, not at the next slow frame
            # Its name, which the avatar's may have changed with it.
            for window in (self.root, self.agent_window):
                window.title(self.app.avatar_name())
            tray = getattr(self, "tray", None)
            if tray is not None:
                tray.refresh()
        elif kind == "hover":
            self._hover_reading = False
            if payload:
                self._hover_said = payload
            self._show_hover(getattr(self, "_hover_said", None))
        elif kind == "handed_note":
            self._show_handed_note(payload)
        elif kind == "answer":
            self.set_state("talking")
            self.root.after(2500, lambda: self.set_state("idle"))
        elif kind == "speaking":
            self._speak_for(float(payload))
        elif kind == "waiting":
            self._show_waiting()
        elif kind == "panels":
            for panel in getattr(self, "_quick_panels", {}).values():
                if panel.is_open:
                    panel._read_it(quiet=True)
        elif kind == "notification":
            self.set_state("notification")
            if isinstance(payload, dict):
                self.notify_bubble(payload["text"],
                                   question_id=payload["question_id"],
                                   notification_id=payload.get(
                                       "notification_id", ""),
                                   template=payload.get("template", ""),
                                   content=bool(payload.get("content")),
                                   picture=payload.get("picture", ""),
                                   conversation=payload.get("conversation", ""),
                                   reason=payload.get("reason", ""),
                                   trigger=payload.get("trigger", ""),
                                   recommended=bool(payload.get("recommended")),
                                   through=bool(payload.get("through")),
                                   summary=bool(payload.get("summary")))
            else:                       # a plain string, from elsewhere
                self.notify_bubble(str(payload))
            self.root.after(4000, lambda: self.set_state("idle"))
        elif kind == "proactive":
            # Section 7.4: the agent may raise something on its own.
            # It may not take the screen to do it - see notify_bubble.
            #
            # Switched off, the message is not lost: it was recorded as
            # a notification before it reached here, and is in the list
            # with the others. What the setting buys is silence now.
            if bool(self.config.get("proactive_conversation_enabled", True)):
                self.set_state("talking")
                self.notify_bubble(payload, emphasis=True)
                self.root.after(3000, lambda: self.set_state("idle"))

    # -- bus subscriptions --------------------------------------------------
    def _on_notification(self, _topic: str, payload: Any) -> None:
        from ..common.notifications import told_from, what_set_it_off
        text = f"{payload.get('title')}: {payload.get('message')}"
        # A notification raised alongside a question carries its id, so the
        # bubble can offer that question's own buttons rather than a bare
        # acknowledgement that settles nothing.
        metadata = payload.get("metadata") or {}
        if payload.get("kind") == "approval":        # an open list has its answers to show now
            self.queue.put(("panels", None))
        if metadata.get("level") == "collapse":      # no bubble: one more in the waiting list
            self.queue.put(("waiting", None))
            return
        question_id = str(metadata.get("question_id") or "")
        self.queue.put(("notification", {
            "text": text, "question_id": question_id,
            "notification_id": str(payload.get("id") or ""),
            "template": str(metadata.get("template") or ""),
            "content": bool(metadata.get("content")),
            "picture": str(metadata.get("picture") or ""),
            "conversation": str(metadata.get("chat_conversation") or ""),
            "reason": str(metadata.get("reason") or ""), "trigger": told_from(metadata),
            "set_off": what_set_it_off(metadata),
            "recommended": bool(metadata.get("recommended")),
            "summary": bool(metadata.get("summary")),
            "report": bool(metadata.get("report")),       # the report (common/notifications.py category_of)
            "through": bool(metadata.get("through"))}))

    def _on_speaking(self, _topic: str, payload: Any) -> None:
        self.queue.put(("speaking", float((payload or {}).get("seconds") or 1.0)))

    def _speak_for(self, seconds: float) -> None:
        """Its mouth goes while a reply comes out, and stops when no more does."""
        due = getattr(self, "_speech_due", None)
        if due is not None:
            self.root.after_cancel(due)
        self.set_state("talking")
        self._speech_due = self.root.after(int(seconds * 1000), self._spoken)

    def _spoken(self) -> None:
        self._speech_due = None
        if self.state == "talking":
            self.set_state("idle")

    def _on_proactive(self, _topic: str, payload: Any) -> None:
        self.queue.put(("proactive", str(payload.get("message", ""))))

    def _on_character_changed(self, _topic: str, _payload: Any) -> None:
        self.queue.put(("character", None))

    def _on_handed_opened(self, _topic: str, payload: Any) -> None:
        if isinstance(payload, dict):
            self.queue.put(("handed_note", payload))

    def notify_bubble(self, text: str, emphasis: bool = False,
                      question_id: str = "", notification_id: str = "",
                      template: str = "", content: bool = False,
                      picture: str = "", conversation: str = "", reason: str = "",
                      recommended: bool = False, through: bool = False,
                      trigger: str = "", summary: bool = False) -> None:
        """Queue something to say beside the character.

        Everything the agent raises of its own accord arrives this way. It used
        to be that a "proactive" message opened the conversation window - which
        takes focus, covers whatever the user was reading, and makes the agent
        deciding it has something to say cost the user their place. A bubble
        can be ignored; a window that steals focus cannot, and the agent is not
        in a position to know that what it has is worth that.

        **One at a time, and it waits for an answer.** Two things follow from
        the same observation: a notification the user did not happen to be
        looking at was a notification that never happened.

        Bubbles used to be drawn at the same spot and timed out on their own,
        so a busy minute stacked three of them into an unreadable pile and then
        erased all three - and the user could not tell afterwards that there
        had been anything at all. Now one is shown, it stays until it is
        answered, and the next follows. Nothing the agent says is lost to
        having looked away.

        ``emphasis`` is the whole of the difference between the loud kind and
        the quiet kind: a warmer colour and a visible edge, never a different
        mechanism.
        """
        item = {"text": text, "emphasis": emphasis, "question_id": question_id,
                "notification_id": notification_id, "template": template,
                "content": content, "picture": picture, "conversation": conversation,
                "reason": reason, "recommended": recommended, "through": through,
                "trigger": trigger, "summary": summary}
        if not self._merge_bubble(item):
            self._bubble_queue.append(item)
        self._show_next_bubble()
        # The one on screen was drawn before this arrived, so its count is now
        # wrong. Notifications turn up over time rather than in a batch, which
        # makes this the ordinary case and not the edge one.
        self._refresh_bubble_count()

    def _merge_bubble(self, item: Dict[str, Any]) -> bool:
        """Whether this is one already waiting, rather than another one.

        A reminder that repeats raises a notification every time it fires and
        each one points at the *same standing question* - so two bubbles are
        two copies of one interruption, and answering either answers both.
        Queueing both means the user dismisses the same words twice and the
        second one tells them nothing.

        Matched on the question where there is one, and on the words where
        there is not.
        """
        waiting = list(self._bubble_queue)
        if self._bubble is not None and self._showing is not None:
            waiting.append(self._showing)
        for other in waiting:
            same_question = (item["question_id"]
                             and item["question_id"] == other.get("question_id"))
            same_words = (not item["question_id"]
                          and not other.get("question_id")
                          and item["text"] == other.get("text"))
            if same_question or same_words:
                # The later raising is the current one, so its wording wins -
                # a repeating reminder may say something new each time.
                other["text"] = item["text"]
                other["emphasis"] = other.get("emphasis") or item["emphasis"]
                other["notification_id"] = (item.get("notification_id")
                                            or other.get("notification_id", ""))
                return True
        return False

    def _show_handed_note(self, note: Dict[str, Any]) -> None:
        """WHAT WAS HANDED OVER, HOW IT IS USED AND WHAT TO CHECK (the user,
        2026-11-01; agent/work/handed.py) - beside what was just opened, a
        card of its own on top until it is closed (ui/handed_note.py). A
        newer one takes its place; its zoom is kept for the next."""
        from .handed_note import PARTS, PROJECT_PALETTE, ZOOM_START, HandedNote
        # ONE OF EACH KIND (the user, 2026-11-08: "per to do 的内容，如果打开下一个per to do的to do panel,
        # 上一个自动关掉，但是per project的to do panel 只会被下一个per project的panel关掉"): a newer
        # note takes the place of the one of its own kind only.
        scope = "project" if note.get("scope") == "project" else \
            "approval" if note.get("scope") == "approval" else "todo"
        notes = self.__dict__.setdefault("_handed_notes", {})

        def current() -> Dict[str, Any]:
            """THE APPROVALS PANEL'S ITEM AS IT IS NOW - each of its rows is another work's."""
            panel = notes.get(scope)
            return panel.note if scope == "approval" and panel is not None else note
        old = notes.get(scope)
        if old is not None:
            old.close()
        font = self._popup_font()
        # EACH PART'S OWN ZOOM, AS IT WAS LAST LEFT (the user, 2026-11-08: "四个小panel是支持单独的 缩放
        # （并且记住历史选择)"): a part never zoomed opens at the popups' zoom.
        left = self.config.get("handed_note_zooms", {}) or {}
        left = left if isinstance(left, dict) else {}
        zooms = {part: int(left.get(part, ZOOM_START + sizes.popup_zoom(self.config)))
                 for part in PARTS}

        def kept(side: str, step: int) -> None:
            try:
                now = dict(self.config.get("handed_note_zooms", {}) or {})
                now[side] = int(step)
                self.config.set("handed_note_zooms", now, internal=True)
            except Exception:                    # this note keeps it
                log.debug("a part's zoom could not be kept", exc_info=True)
        def save(words: str) -> Any:
            """Saved where the work keeps it (WorkRuns.save_handed) - True,
            else why not, in words."""
            try:
                said = self.app.work.save_handed(str(current().get("run") or ""),
                                                 int(current().get("n") or 0), words)
            except Exception:                    # pragma: no cover - said not saved
                log.warning("what was handed over could not be saved", exc_info=True)
                return False
            if isinstance(said, dict) and said.get("error") == "going":
                return "It is working on it again - save once it is done; copy it so it is not lost"
            return said is not None
        # TICKED, KEPT (the user, 2026-11-08): by the run and the item, in the database.
        def tick_key(one: Dict[str, Any]) -> str:
            # An item's by its place; a whole project's own checks, "project".
            n = one.get("n")
            return f"handed.ticked.{one.get('run') or ''}.{n if n == 'project' else int(n or 0)}"

        def kept_ticks(one: Dict[str, Any]) -> list:
            try:
                waiting = window_writes(self).waiting(tick_key(one), None)
                if waiting is not None:          # handed over, not yet written
                    return json.loads(waiting)
                row = self.app.db.query_one("SELECT value FROM kv WHERE key = ?", (tick_key(one),))
                return json.loads(row["value"]) if row and row["value"] else []
            except Exception:                    # none ticked, then
                log.debug("what was ticked could not be read", exc_info=True)
                return []

        def ticked_of(one: Dict[str, Any]) -> Dict[str, Any]:
            return dict(one, ticked=kept_ticks(one)) if one.get("run") else one
        note = ticked_of(note)
        if note.get("run") and isinstance(note.get("project"), dict):
            note["project"] = dict(note["project"], ticked=kept_ticks({"run": note["run"],
                                                                       "n": "project"}))

        def ticked(words: list, shown: Optional[Dict[str, Any]] = None) -> None:
            # Handed over: the window never waits for the database (later_writes.py).
            window_writes(self).put(tick_key(shown or note), json.dumps(list(words), ensure_ascii=False))

        def approval_item(k: int, opening: bool = False) -> None:
            """A row of the approvals panel: what the agent made for it, shown below the rows."""
            rows = (note.get("approval") or {}).get("rows") or []
            if not 0 <= k < len(rows):
                return
            row = rows[k]

            def ask() -> None:
                try:
                    said = self.app.approval_item(row)
                    said = ticked_of(said) if said.get("run") else said
                except Exception:
                    log.info("a thing to approve could not be shown", exc_info=True)
                    return
                panel = self.__dict__.get("_handed_notes", {}).get("approval")
                if panel is not None:
                    self.queue.put(("call", lambda: panel.show_item(said)))
            threading.Thread(target=ask, name="approval-item", daemon=True).start()

        def approve(k: int, how: str) -> None:
            """An answer to a row - kept off Tk's thread, then said on its row."""
            rows = (note.get("approval") or {}).get("rows") or []
            ident = str(rows[k].get("id") or "") if 0 <= k < len(rows) else ""

            def ask() -> None:
                ok = False
                try:
                    ok = self.app.agenda_approved(ident, how) is not None
                except Exception:
                    log.warning("an approval could not be kept", exc_info=True)
                panel = self.__dict__.get("_handed_notes", {}).get("approval")
                if panel is not None:
                    self.queue.put(("call", lambda: panel.approved(k, how, ok)))
            threading.Thread(target=ask, name="approval-answer", daemon=True).start()

        def item(n: int, opening: bool = False) -> None:
            """One of a whole project's items, shown below the project's part of its panel -
            `opening`: opened where it opens, too. Asked off Tk's thread: what it is is worked out
            as when it is opened."""
            told = getattr(self.app, "handed_told", None)
            opens = getattr(self.app, "open_handed", None)
            if told is None:
                return
            run = str(note.get("run") or "")

            def ask() -> None:
                if opening and opens is not None:
                    try:
                        opens(run, int(n))
                    except Exception:            # said in its note's own words, when shown
                        log.info("an item could not be opened", exc_info=True)
                try:
                    said = ticked_of(told(run, int(n)))
                except Exception:
                    log.info("an item's note could not be made", exc_info=True)
                    return
                panel = self.__dict__.get("_handed_notes", {}).get("project")
                if panel is not None:
                    self.queue.put(("call", lambda: panel.show_item(said)))
            threading.Thread(target=ask, name="handed-item", daemon=True).start()

        def folder() -> None:
            """Its work's folder, in front - asked off Tk's thread."""
            opening = getattr(self.app, "open_run_folder", None)
            if opening is None:
                return

            def ask() -> None:
                try:
                    opening(str(note.get("run") or ""))
                except Exception:
                    log.info("a work's folder could not be opened", exc_info=True)
            threading.Thread(target=ask, name="open-work-folder", daemon=True).start()

        def again() -> str:
            """Opened again where it opens (the API's own act, no note put up over this one): ""
            when it opened, else why not."""
            opening = getattr(self.app, "open_handed", None)
            if opening is None:
                return "It cannot be opened from here - open it from the Overview"
            try:
                opening(str(current().get("run") or ""), int(current().get("n") or 0))
            except Exception as exc:             # the API's own refusal, in its words
                return str(getattr(exc, "message", "") or exc) or "It could not be opened"
            return ""
        # ITS SIZE AND ITS SIDES' SHARE as the user left the last note's.
        size = sizes.kept(self.root, self.config.get("handed_note_size", []) or []) or []
        try:
            split = self.config.get("handed_note_split", None)
            split = float(split) if split not in (None, "") else None
        except (TypeError, ValueError):
            split = None

        def laid_out(size: tuple, split: Any) -> None:
            try:
                self.config.set("handed_note_size", sizes.share(self.root, size), internal=True)
                if split is not None:
                    self.config.set("handed_note_split", round(float(split), 4), internal=True)
            except Exception:                    # pragma: no cover - this note keeps it
                log.debug("the note's size could not be kept", exc_info=True)
        approving = scope == "approval"
        self._handed_note = HandedNote(self.root, note, self._words, font[0], font[1],
                                       zoom=zooms, on_zoom=kept,
                                       on_save=save if note.get("run") or approving else None,
                                       on_open=again if note.get("run") or approving else None,
                                       on_item=approval_item if approving else
                                       item if note.get("run") and note.get("items") else None,
                                       on_tick=ticked if note.get("run") or approving else None,
                                       on_folder=folder if note.get("run") and not approving
                                       else None,
                                       kept_size=size or None, split=split, on_layout=laid_out,
                                       palette=PROJECT_PALETTE if scope == "project" else None,
                                       on_approve=approve if approving else None)
        notes[scope] = self._handed_note
        self._handed_note.show()

    def _blocker(self) -> bool:
        """Whether a blocker of the Monitor's holds every reminder now."""
        return bool(getattr(getattr(self.app, "monitor", None), "blocker", ""))

    def _blocked(self) -> bool:
        return self._blocker() and not self._bubbles_opened

    def _watch_the_blockers(self) -> None:
        """A BLOCKER IS FOR EVERY REMINDER (the user, 2026-11-04). What waits is
        collapsed into a small line saying how many, to be opened by hand, and
        comes by itself when it is over. One on the screen when it begins, as
        slides go up, goes back to the head of the line, unanswered."""
        self._look_for_work_waiting()
        if getattr(self, "_side_tab_on", False):
            DesktopAgent._side_tab_unread(self)      # its dot kept true
        if not self._blocker():
            self._bubbles_opened = False
            self._show_next_bubble()
        elif self._bubbles_opened:
            if self._bubble is None and not self._bubble_queue:
                self._bubbles_opened = False     # all read: the next ones wait again
        elif self._bubble is not None and self._showing is not None \
                and not self._showing.get("through"):
            self._bubble_queue.appendleft(self._showing)
            self._close_bubble()
        self._show_waiting()
        monitor = getattr(self.app, "monitor", None)
        if monitor is not None:
            monitor.reminders_waiting = len(self._bubble_queue) if self._blocker() else 0
        self.root.after(self.BLOCKERS_EVERY_MS, self._watch_the_blockers)

    def _look_for_work_waiting(self) -> None:
        """Off Tk's thread: a run is asked, in its own process, and one look at a time."""
        self._kept_worker("work-waiting", self.app.tell_work_waiting)

    def _kept_worker(self, name: str, work: Any) -> None:
        """`work` done on ONE thread kept for it, told by an Event - Tk's thread never waits on it (2026-11-16: the
        avatar froze, black, deaf to the mouse - Tk's thread stood in Thread.start() waiting for a new thread to say
        it had begun, as it started one every few seconds; that one never did). Started the first time; asked while
        it works, it works once more after; asked many times meanwhile, once."""
        workers = self.__dict__.setdefault("_kept_workers", {})
        asked = workers.get(name)
        if asked is None:
            asked = workers[name] = threading.Event()

            def working() -> None:
                while True:
                    asked.wait()
                    asked.clear()
                    try:
                        work()
                    except Exception:                # the next time catches up
                        log.debug("%s could not be done", name, exc_info=True)
            threading.Thread(target=working, name=name, daemon=True).start()
        asked.set()

    def _open_waiting(self) -> None:
        """Pressed: what was kept, the list - the bubbles held while it is open, and coming again
        when it closes; nothing kept, what a blocker held, one at a time, as any bubble."""
        self._bubbles_opened = True
        self._hide_waiting()
        if self.app.notifications.waiting_count():
            self.open_notifications()
        else:
            self._show_next_bubble()

    #: Its window's title, as the page sets it (app.js, NOTES_SOLO) - in English and as shown.
    NOTES_TITLE = "Waiting notifications - Personal Agent WaterMelon"

    def open_notifications(self, quietly: bool = False) -> None:
        """THE WAITING NOTIFICATIONS DOWN THE WHOLE SIDE OF THE SCREEN (the user, 2026-11-13: "你现在这个
        notifications/recommendation的channel太不现代了，能不能直接占满整个侧边框的那种（类似reports每个事项点击之后
        弹出来的内容)"): a page in a window of its own, docked to the right edge as tall as the screen -
        brought back when it is open already. The line beside the avatar goes as each is answered. quietly: a
        notification's - shown, where the user is typing left in front (as its bubble was)."""
        from ..platform_layer import app_window
        titles = self._notes_titles()
        on_a_mac = self._notes_on_a_mac()
        # NOT BESIDE THE OPEN PANEL (the user, 2026-11-13: "打开1 recommendation 之后, 1 recommendation的通知还
        # 存在"): the line and the bubbles held while it is shown - drawn again, those still unread, once
        # it is put away (`_watch_notes`). Where the panel cannot be seen from here, as before.
        if (sys.platform == "win32" or on_a_mac) and not os.environ.get("PA_NO_DIALOGS"):
            self._waiting_list_open(True)
            self._notes_since = time.monotonic()
            self._notes_seen = False
            if not getattr(self, "_notes_watching", False):
                self._notes_watching = True
                self.root.after(self.NOTES_WATCH_MS, self._watch_notes)
        if on_a_mac:
            # ON A MAC THE SAME (the user, 2026-11-13: "Mac上的面板为什么不改呢"): a panel of the agent's own,
            # Apple's WebKit, down the right side - made on the main thread, at once (no browser).
            self.queue.put(("call", lambda: DesktopAgent._open_notes_on_a_mac(self, quietly)))
            return

        # OPENED BY A NOTIFICATION, OVER THE WINDOWS ONLY (2026-11-16: pinned and put away, a notification took the
        # strip again and moved the user's windows as they typed) - and put away again untouched (`_put_away_untouched`).
        if quietly and not getattr(self, "_held_for_screen", False) and DesktopAgent._film_in_front(self):
            # NOT OVER A FILM (the user, 2026-11-16: "全屏的时候我把他collapse, 他自动又会出现"): a notification while
            # something fills the screen waits - told in the panel once it is over.
            if not getattr(self, "_notes_after_film", False):
                self._notes_after_film = True
                self.root.after(self.FILM_LOOK_MS, lambda: DesktopAgent._open_after_film(self))
            return
        self._side_tab_on = self._tab_for_cover = False      # opened: no longer its tab alone
        if getattr(self, "queue", None) is not None:
            self.queue.put(("call", lambda: DesktopAgent._hide_side_tab(self)))   # its tab away
        if quietly is False and not DesktopAgent._film_in_front(self):
            self._over_film = False            # opened again with no film: as usual
        # OVER A FILM WHEN THE USER OPENS IT (the user, 2026-11-16: "全屏时候，点ctrl+c 打开sidebar没用"): above it,
        # over the windows - its strip is no use while a film fills the screen.
        film = not quietly and DesktopAgent._film_in_front(self)
        if quietly:
            self._quiet_opened_at = time.monotonic()
            self.root.after(self.UNTOUCHED_MS, lambda at=self._quiet_opened_at: DesktopAgent._put_away_untouched(self, at))
        else:
            self._quiet_opened_at = None

        def go() -> None:
            # THE OTHER WINDOWS MAKE ROOM (dock.py): its strip of the screen taken as it slides in - when the user
            # opened it.
            pinned = DesktopAgent._side_dock(self)
            # PINNED, OVER A FILM TOO: its strip taken and the film made room for (dock.py `over_film`); not pinned,
            # over the film - its strip, if one was left, given back first.
            over = film and pinned is None
            dock = None if quietly or over else pinned
            if over:
                held = getattr(self, "_dock", None)
                if held is not None and held.docked:
                    held.undock()
            if film and dock is not None:
                dock.over_film = True
            # PUT AWAY, NOT CLOSED: slid back in at once - a browser started only the first time.
            wide = DesktopAgent._notes_width(self)               # as the user left it
            size = (wide, app_window.NOTES_SIZE[1])
            try:
                opened = app_window.slide_in_titled(titles, width=wide, dock=dock, activate=not quietly)
            finally:
                if dock is not None:
                    dock.over_film = False
            if opened:
                if over:
                    app_window.on_top_titled(titles, True)
                    self.queue.put(("call", lambda: DesktopAgent._over_the_film(self)))
                # Opened by the user: the keys in its box, as by its keys (2026-11-17: "第一次按钮expand, 焦点会在search上").
                DesktopAgent._tell_the_panel(self, peeking=False, by_user=not quietly, focus=not quietly)
                return
            url = self.app.sign_in_url(self.NOTES_PATH)
            # IN WEBVIEW2, STARTED BEFORE IT WAS WANTED (panel_host.py; the user, 2026-11-14: "接进") - else, as
            # before, the browser's app mode. The first time too: put down the side before it is shown, then
            # slid in.
            if app_window.open_hosted_side_window(getattr(self, "panel_host", None), url,
                                                  size, self.NOTES_TITLE, titles, dock=dock):
                return
            if not app_window.open_side_window(url, self.app.paths.data_dir / app_window.NOTES_PROFILE,
                                               size, titles=titles):
                webbrowser.open(url)
        self._off_this_thread(go, "open-notifications")

    #: The side panel's page - placed by the desktop, not moving itself.
    NOTES_PATH = "/notes.html?placed=1"          # its own small page (2026-11-14), not the whole application
    #: How often the open side panel is looked for; how long one being started is waited for.
    NOTES_WATCH_MS, NOTES_COMING_SECONDS = 700, 15.0

    def _side_dock(self) -> Any:
        """THE SIDE PANEL MAKES ROOM ONLY WHEN THE USER PINNED IT (the user, 2026-11-16: "所有notification弹出现在都是
        不缩放，只有user主动选择固定在右边，才是缩放的view(相当于右边固定被sidebar占据)"; dock.py) - on Windows; else
        None, and the panel opens over the other windows."""
        config = getattr(self, "config", None)
        if sys.platform != "win32" or config is None or not config.get("side_panel_pinned", False):
            return None
        dock = getattr(self, "_dock", None)
        if dock is None:
            from ..platform_layer.dock import Dock
            dock = self._dock = Dock(self.app.paths.data_dir / "side_panel_dock.json")
        return dock

    def _give_the_room_back(self) -> None:
        """Its strip of the screen given back and the windows moved for it put back - whatever the setting
        is now (one switched off while the panel was open)."""
        dock = getattr(self, "_dock", None)
        if dock is not None and dock.docked:
            dock.undock()
        on_a_mac = getattr(self, "_mac_dock", None)
        if on_a_mac is not None and on_a_mac.docked:
            on_a_mac.undock()

    def _notes_on_a_mac(self) -> bool:
        if sys.platform != "darwin":
            return False
        from . import mac_panel
        return mac_panel.available()

    #: How often a notification held back by a film looks again whether the film is over.
    FILM_LOOK_MS = 5000

    def _film_in_front(self) -> bool:
        """Whether a window fills its screen in front - a film, a game, a slide show (Windows) - this application's own
        windows passed over: the side panel or the avatar pressed on is not the end of the film."""
        if sys.platform != "win32":
            return False
        from ..platform_layer.dock import full_screen_now
        from ..platform_layer import windows
        return full_screen_now(skip={os.getpid(), *windows.OTHER_OWN_PROCESSES})

    def _over_the_film(self) -> None:
        """OPENED BY THE USER OVER A FILM (the user, 2026-11-16: "全屏的时候ctrl C, 显示出来了sidebar, 我又点了一下全屏的视频，
        sidebar好像被最小化了 ... 再ctrl + C, sidebar 的位置就没有靠着最右边"): its strip given back - the film is
        over all of it - so that it is not hidden for the film, nor put by the room the strip had left; above the
        film till the user puts it away. The film over and the panel still shown: pinned, its strip taken again;
        else among the windows again."""
        self._over_film = True
        if not getattr(self, "_film_watching", False):
            self._film_watching = True
            self.root.after(self.FILM_LOOK_MS, lambda: DesktopAgent._film_over_yet(self))

    def _film_over_yet(self) -> None:
        if getattr(self, "_over_film", False) and DesktopAgent._film_in_front(self):
            self.root.after(self.FILM_LOOK_MS, lambda: DesktopAgent._film_over_yet(self))
            return
        self._film_watching = False
        if not getattr(self, "_over_film", False):
            return
        self._over_film = False
        if not self._notes_open_now():
            return
        from ..platform_layer import app_window
        titles, width = self._notes_titles(), DesktopAgent._notes_width(self)
        dock = DesktopAgent._side_dock(self)

        def back() -> None:
            if dock is None or not app_window.dock_titled(titles, width, dock):
                app_window.on_top_titled(titles, False)
        threading.Thread(target=back, name="side-panel-after-film", daemon=True).start()

    def _open_after_film(self) -> None:
        if DesktopAgent._film_in_front(self):
            self.root.after(self.FILM_LOOK_MS, lambda: DesktopAgent._open_after_film(self))
            return
        self._notes_after_film = False
        if self.app.notifications.for_the_side():
            self.open_notifications(quietly=True)

    #: How long a side panel a notification opened stays, never touched, before it is put away again.
    UNTOUCHED_MS = 45000
    HELD_LOOK_MS = 1500

    def _hold_side_panel(self, on: bool) -> None:
        """HELD OUT WHILE THE AGENT HAS THE SCREEN (the user, 2026-11-18: "操控屏幕的时候claude 不能collapose sidebar"): brought
        in - quietly, the keys left where the agent types - over a film too, and never put away by itself (as one a
        notification opened is, untouched); looked at every HELD_LOOK_MS and brought in again whenever it is not shown.
        Let go when the screen is given back - the user's hand takes the screen first, so their own putting away is
        theirs."""
        self._held_for_screen = bool(on)
        if not on:
            return
        DesktopAgent.open_notifications(self, quietly=True)
        self._quiet_opened_at = None                     # not put away untouched

        def look() -> None:
            if not getattr(self, "_held_for_screen", False):
                return
            try:
                shown = DesktopAgent._side_panel_shown(self)
            except Exception:
                shown = True
            if not shown:
                DesktopAgent.open_notifications(self, quietly=True)
                self._quiet_opened_at = None
            self.root.after(self.HELD_LOOK_MS, look)
        self.root.after(self.HELD_LOOK_MS, look)

    def _put_away_untouched(self, opened_at: float) -> None:
        """A side panel a notification opened, untouched since (pressed on, typed in - api/routes.py side_touched),
        put away again - not when the user opened it since; pinned or not. Never while the agent has the screen."""
        if getattr(self, "_held_for_screen", False):
            return
        if getattr(self, "_quiet_opened_at", None) != opened_at:
            return
        if float(getattr(self.app, "side_touched_at", 0.0) or 0.0) >= opened_at:
            return
        # PINNED TOO: it was put away before the notification opened it, and nothing took its strip for it (it stayed
        # over the windows for good - 2026-11-17).
        self._quiet_opened_at = None
        self.hide_notes_panel()

    def _open_notes_on_a_mac(self, quietly: bool = False) -> None:
        from . import mac_panel
        from ..platform_layer import app_window
        url = self.app.sign_in_url(self.NOTES_PATH)
        if not mac_panel.side_panel(url, DesktopAgent._notes_width(self), self.NOTES_TITLE, activate=not quietly):
            webbrowser.open(url)
            return
        DesktopAgent._make_room_on_a_mac(self)

    def _make_room_on_a_mac(self) -> None:
        """THE OTHER WINDOWS MOVE OVER ON A MAC TOO (the user, 2026-11-16; platform_layer/dock_mac.py): those the
        shown panel covers, by the Accessibility API - asked for once, by the system, when it is not allowed. On
        the main thread (where the panel is read); the moving off it."""
        config = getattr(self, "config", None)
        if config is None or not config.get("side_panel_pinned", False):
            return
        try:
            from . import mac_panel
            from ..platform_layer.dock_mac import MacDock
            placed = mac_panel.side_panel_strip(self.NOTES_TITLE)
            if placed is None:
                return
            dock = getattr(self, "_mac_dock", None)
            if dock is None:
                dock = self._mac_dock = MacDock()
            dock.dock(*placed)
        except Exception:                        # the panel open over the others, then
            log.info("the other windows could not be moved for the side panel", exc_info=True)

    def _side_panel_box(self) -> Optional[Tuple[float, float, float, float]]:
        """The side panel on the screen as shares of it - left, top, right, bottom - as the agent's hands count them
        (platform_layer/input_control.py: of the whole desktop on Windows, of the main display on the Mac); None when it
        is not shown. Asked from the agent's thread."""
        titles = self._notes_titles()
        try:
            if sys.platform == "win32":
                import ctypes
                from ctypes import wintypes
                from ..platform_layer import app_window
                handle = app_window._windows_page(titles)
                if not handle:
                    return None
                user32 = ctypes.windll.user32
                rect = wintypes.RECT()
                user32.GetWindowRect(handle, ctypes.byref(rect))
                vx, vy, vw, vh = (user32.GetSystemMetrics(n) for n in (76, 77, 78, 79))
                return ((rect.left - vx) / max(1, vw), (rect.top - vy) / max(1, vh),
                        (rect.right - vx) / max(1, vw), (rect.bottom - vy) / max(1, vh))
            if self._notes_on_a_mac():
                import Quartz
                from . import mac_panel
                bounds = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
                for title in titles:
                    got = mac_panel.side_panel_strip(title)
                    if got:
                        left, top, right, bottom = got[0]
                        w, h = max(1.0, bounds.size.width), max(1.0, bounds.size.height)
                        return left / w, top / h, right / w, bottom / h
        except Exception:
            log.debug("the side panel's place could not be read", exc_info=True)
        return None

    def _side_panel_shown(self) -> bool:
        """Whether the side panel is on the screen now - not put away, not hidden, not minimised, not slid off it."""
        box = DesktopAgent._side_panel_box(self)
        if box is None or box[0] >= 0.995 or box[2] <= 0.005:
            return False
        if sys.platform == "win32":
            import ctypes
            from ..platform_layer import app_window
            handle = app_window._windows_page(self._notes_titles())
            user32 = ctypes.windll.user32
            return bool(handle) and bool(user32.IsWindowVisible(handle)) and not user32.IsIconic(handle)
        return True

    def _side_panel_front(self) -> bool:
        """Whether the side panel has the keys now - typing would go into it."""
        titles = self._notes_titles()
        try:
            if sys.platform == "win32":
                import ctypes
                from ..platform_layer import app_window
                handle = app_window._windows_page(titles)
                return bool(handle) and ctypes.windll.user32.GetForegroundWindow() == handle
            if self._notes_on_a_mac():
                from . import mac_panel
                return any(found is not None and bool(found[0].isKeyWindow())
                           for found in (mac_panel._side.get(title) for title in titles))
        except Exception:
            log.debug("whether the side panel is in front could not be read", exc_info=True)
        return False

    def _notes_titles(self) -> set:
        from ..common.interface_words import shown
        return {self.NOTES_TITLE, shown(self.NOTES_TITLE, getattr(self.app, "config", None))}

    def _notes_open_now(self) -> bool:
        """Whether the side panel is on the screen now - however it was opened."""
        try:
            if self._notes_on_a_mac():
                from . import mac_panel
                return mac_panel.side_panel_shown(self.NOTES_TITLE)
            if sys.platform != "win32":
                return False
            from ..platform_layer import app_window
            return app_window.shown_titled(self._notes_titles())
        except Exception:
            return False

    def _watch_notes(self) -> None:
        """While the side panel is shown, nothing beside the avatar; put away or closed - by its ×, by
        the window's own, or empty - what still waits is drawn again."""
        shown = self._notes_open_now()
        dock = getattr(self, "_dock", None)
        if not shown and dock is not None and getattr(dock, "stepped_aside", False):
            # Out of the way of a film (dock.py), not put away: its strip kept, shown again when it is over.
            self.root.after(self.NOTES_WATCH_MS, self._watch_notes)
            return
        if shown:
            self._notes_seen = True
            self._notes_gone_looks = 0
            DesktopAgent._keep_docked(self)
            DesktopAgent._tab_when_covered(self)
        elif self._notes_seen or time.monotonic() - getattr(self, "_notes_since", 0) > self.NOTES_COMING_SECONDS:
            # GONE ONLY WHEN NOT SEEN A FEW LOOKS IN A ROW (the user, 2026-11-17: "他是pined, 但是莫名其妙又不是置顶的了" -
            # its strip was given back, unlogged, while it stayed shown: one look that missed it was taken for its
            # end, and the watch stopped - nothing took the strip again).
            gone = self._notes_gone_looks = int(getattr(self, "_notes_gone_looks", 0) or 0) + 1
            if gone < self.NOTES_GONE_LOOKS:
                self.root.after(self.NOTES_WATCH_MS, self._watch_notes)
                return
            self._notes_gone_looks = 0
            self._notes_watching = False
            self._waiting_list_open(False)
            # GONE HOWEVER IT WENT - its window's own ×, say: the room it took given back.
            dock = getattr(self, "_dock", None)
            if dock is not None and dock.docked:
                log.info("the side panel is gone; its strip of the screen given back")
                threading.Thread(target=dock.undock, name="side-panel-room", daemon=True).start()
            on_a_mac = getattr(self, "_mac_dock", None)
            if on_a_mac is not None and on_a_mac.docked:
                on_a_mac.undock()                        # moves back off this thread itself
            return
        self.root.after(self.NOTES_WATCH_MS, self._watch_notes)

    def _tab_when_covered(self) -> None:
        """NOT PINNED, ITS TAB WHILE ANOTHER WINDOW IS OVER IT (the user, 2026-11-17: "取消pin之后，expand按钮缩放后也消失了，
        这个按钮不应该消失"): not pinned, the panel is a window among the others - a browser pressed on comes over it, and
        it was gone with no tab to bring it back. While one is over it, its tab at the screen's edge; pressed, the panel
        brought to the front (open_notifications); in front again, the tab away. Pinned, it stands above them all."""
        if sys.platform != "win32":
            return
        from ..platform_layer import app_window
        pinned = bool(getattr(self, "config", None) is not None and self.config.get("side_panel_pinned", False))
        covered = not pinned and app_window.covered_titled(self._notes_titles())
        if covered and not getattr(self, "_side_tab_on", False):
            self._side_tab_on = self._tab_for_cover = True
            DesktopAgent._show_side_tab(self)
        elif not covered and getattr(self, "_tab_for_cover", False):
            self._side_tab_on = self._tab_for_cover = False
            DesktopAgent._hide_side_tab(self)

    #: How many looks in a row the side panel must be missing to be taken as gone.
    NOTES_GONE_LOOKS = 3
    #: How long after one try the strip of a pinned panel shown without one is taken again, at the most often.
    REDOCK_SECONDS = 10.0

    def _keep_docked(self) -> None:
        """PINNED AND SHOWN, IN ITS STRIP: the panel the user opened, pinned, shown without its strip (given back by
        something that did not put it away) - the strip taken again, above the others as pinned. Not one a
        notification opened (over the windows only), nor over a film, nor one stepped aside for a film."""
        # A NOTIFICATION'S PANEL TOUCHED IS THE USER'S (the user, 2026-11-17: "在Pin的情况下，居然进入了pin的位置" - opened by a
        # notification over the windows, it stayed so for good once pressed on, and the windows came over its strip):
        # pressed on or typed in since, it is pinned as the user's own is - in its strip.
        quiet = getattr(self, "_quiet_opened_at", None)
        app = getattr(self, "app", None)
        if quiet and float(getattr(app, "side_touched_at", 0.0) or 0.0) >= quiet:
            self._quiet_opened_at = None
        if sys.platform != "win32" or getattr(self, "_quiet_opened_at", None) or getattr(self, "_over_film", False) \
                or getattr(self, "_notes_full", None):     # over the whole screen: never docked meanwhile
            return
        dock = DesktopAgent._side_dock(self)
        if dock is None or dock.docked or getattr(dock, "stepped_aside", False):
            return
        now = time.monotonic()
        if now - float(getattr(self, "_redocked_at", 0.0) or 0.0) < self.REDOCK_SECONDS:
            return
        self._redocked_at = now
        if DesktopAgent._film_in_front(self):
            return
        from ..platform_layer import app_window
        titles, width = self._notes_titles(), DesktopAgent._notes_width(self)
        log.info("the side panel is pinned and shown without its strip of the screen; taken again")
        self._off_this_thread(lambda: app_window.dock_titled(titles, width, dock), "side-panel-redock")

    #: THE SIDE PANEL'S WIDTH, AS THE USER LEFT IT (the user, 2026-11-16: "那个side bar 要允许我鼠标拖动size"), in the
    #: screen's own units - never narrower than this; as wide as the screen at most (the user, 2026-11-19: "sidebar整体
    #: 是不是不能拉的很右，这个允许他甚至占满整个屏幕" - it stopped at 900), the screen's own edge holding it. With its
    #: picture beside the chat ("有图的时候才展开") it is as wide again.
    NOTES_NARROWEST, NOTES_WIDEST = 320, 20000

    def _notes_width(self) -> int:
        from ..platform_layer import app_window
        config = getattr(self, "config", None)
        try:
            kept = int(config.get("side_panel_width", 0) or 0) if config is not None else 0
        except (TypeError, ValueError, AttributeError):
            kept = 0
        return max(DesktopAgent.NOTES_NARROWEST, min(DesktopAgent.NOTES_WIDEST, kept or app_window.NOTES_SIZE[0]))

    def pin_notes_panel(self, pinned: Optional[bool]) -> bool:
        """THE SIDE PANEL PINNED TO THE RIGHT, OR NOT (its pin): pinned, the strip down the right is its own and
        the other windows stand beside it - at once while it is shown, and each time it opens after; not, it
        opens over them. `pinned` None: only asked. Whether it is pinned."""
        config = self.config
        if pinned is None:
            return bool(config.get("side_panel_pinned", False))
        config.set("side_panel_pinned", bool(pinned), internal=True)
        if self._notes_on_a_mac():
            if pinned:
                self.queue.put(("call", lambda: DesktopAgent._make_room_on_a_mac(self)))
            else:
                DesktopAgent._give_the_room_back(self)
            return bool(pinned)
        if sys.platform != "win32":
            return bool(pinned)
        from ..platform_layer import app_window

        def change() -> None:
            if pinned:
                dock = DesktopAgent._side_dock(self)
                if dock is not None:
                    app_window.dock_titled(self._notes_titles(), DesktopAgent._notes_width(self), dock)
            else:
                DesktopAgent._give_the_room_back(self)
        self._off_this_thread(change, "side-panel-pin")
        return bool(pinned)

    def full_notes_panel(self, full: Optional[bool]) -> bool:
        """THE SIDE PANEL OVER THE WHOLE SCREEN, OR BACK (the user, 2026-11-20: "加一个全屏图标，是可以让整个sidebar最大化，
        再点一下恢复（全屏不作为attach的位置记录，只是单纯的全屏，很多时候是玩游戏用的）"): its strip of the screen given
        back while it is full - nothing docks it again meanwhile, and no width of it is kept; back, where it was, and
        pinned again as it was. `full` None: only asked. Whether it is full now."""
        if full is None:
            return bool(getattr(self, "_notes_full", None))
        if sys.platform != "win32":
            return False
        from ..platform_layer import app_window
        titles = self._notes_titles()
        if full:
            if getattr(self, "_notes_full", None):
                return True
            self._notes_full = ("pending",)          # the re-dock watch keeps off from now
            DesktopAgent._give_the_room_back(self)
            before = app_window.fill_screen_titled(titles)
            self._notes_full = before
            if before is None:
                return False
            log.info("the side panel fills the screen")
            return True
        before = getattr(self, "_notes_full", None)
        self._notes_full = None
        if before and before != ("pending",):
            app_window.put_titled(titles, before)
        if self.config.get("side_panel_pinned", False):
            dock = DesktopAgent._side_dock(self)
            if dock is not None:
                self._off_this_thread(lambda: app_window.dock_titled(titles, DesktopAgent._notes_width(self), dock),
                                      "side-panel-unfull")
        log.info("the side panel is back from the whole screen")
        return False

    def grab_notes_panel(self, wide: bool = False) -> bool:
        """THE SIDE PANEL'S LEFT EDGE PRESSED, ON WINDOWS: followed from here, where the pointer really is, till it
        is let go (app_window.drag_left_edge_titled) - then its width kept, and the other windows moved for it when
        it is pinned (`size_notes_panel`). False where the page follows it itself (a Mac)."""
        if sys.platform != "win32" or os.environ.get("PA_NO_DIALOGS") or self._notes_on_a_mac():
            return False
        from ..platform_layer import app_window
        times = 2 if wide else 1                  # its picture's column beside the chat: twice as wide

        def let_go(width: int) -> None:
            DesktopAgent.size_notes_panel(self, round(width / times), True, wide)
        return app_window.drag_left_edge_titled(self._notes_titles(), self.NOTES_NARROWEST * times,
                                                self.NOTES_WIDEST * times, let_go)

    #: The side panel's edge taken this far outside its line (pixels at 96 dpi) - the page takes as far inside it.
    SIDE_EDGE_OUTSIDE = 3                      # 2026-11-20: "6像素太宽了，要不改成3像素吧"
    #: How often the strip outside the edge follows the panel (ms).
    SIDE_EDGE_EVERY_MS = 300

    def _side_edge_follow(self) -> None:
        """THE PANEL'S EDGE TAKEN ON BOTH SIDES OF ITS LINE (the user, 2026-11-20: "我把鼠标放在白色线的中间，就应该变成缩放的
        形状，但是现在是放在白色线右边才有"): the page's grip is inside the panel; outside it, a strip of this window's own -
        all but unseen, the resizing pointer over it, pressed it drags the edge as the grip does - kept beside the
        panel's edge while the panel is shown, hidden when it is not (put away, over the whole screen, closed)."""
        strip = getattr(self, "_side_edge", None)
        try:
            from ..platform_layer import app_window
            if strip is None:
                strip = self._side_edge = tk.Toplevel(self.root)
                strip.withdraw()
                strip.overrideredirect(True)
                strip.configure(bg="#000000", cursor="sb_h_double_arrow")
                strip.attributes("-alpha", 0.01)           # unseen, but there for the pointer
                strip.bind("<ButtonPress-1>", lambda _event: DesktopAgent._side_edge_pressed(self))
                strip.update_idletasks()
                try:                                       # pressed, it takes no focus from what is in front
                    import ctypes
                    handle = ctypes.windll.user32.GetParent(strip.winfo_id()) or strip.winfo_id()
                    style = ctypes.windll.user32.GetWindowLongW(handle, -20)
                    ctypes.windll.user32.SetWindowLongW(handle, -20, style | 0x08000000 | 0x80)   # NOACTIVATE, TOOLWINDOW
                except Exception:
                    pass
            import ctypes
            handle = ctypes.windll.user32.GetParent(strip.winfo_id()) or strip.winfo_id()
            shown = not getattr(self, "_notes_full", None) and \
                app_window.edge_strip_titled(self._notes_titles(), handle, self.SIDE_EDGE_OUTSIDE)
            if shown and strip.state() == "withdrawn":
                strip.deiconify()
                app_window.edge_strip_titled(self._notes_titles(), handle, self.SIDE_EDGE_OUTSIDE)
            elif not shown and strip.state() != "withdrawn":
                strip.withdraw()
        except Exception:
            log.debug("the side panel's edge outside could not be kept", exc_info=True)
        self.root.after(self.SIDE_EDGE_EVERY_MS, lambda: DesktopAgent._side_edge_follow(self))

    def _side_edge_pressed(self) -> None:
        """Its edge pressed outside the line: dragged as from the page's grip - twice as wide when its picture's column
        is open (its width now against the chat's)."""
        try:
            import ctypes
            from ctypes import wintypes
            from ..platform_layer import app_window
            handle = app_window._windows_page(set(self._notes_titles()))
            wide = False
            if handle:
                rect = wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(handle, ctypes.byref(rect))
                dpi = int(ctypes.windll.user32.GetDpiForWindow(handle) or 96)
                wide = (rect.right - rect.left) * 96 / dpi > 1.5 * self._notes_width()
            DesktopAgent.grab_notes_panel(self, wide)
        except Exception:
            log.debug("the side panel's edge could not be taken", exc_info=True)

    def size_notes_panel(self, width: int, final: bool, wide: bool = False) -> bool:
        """THE SIDE PANEL MADE AS WIDE AS IT IS DRAGGED (its left edge): `width` the chat's - twice it with its
        picture beside it (`wide`); while dragged, only itself - the other windows moved once, when it is let
        go - and that width kept for the next time. False where it cannot be."""
        from ..platform_layer import app_window
        if getattr(self, "_notes_full", None):            # over the whole screen: no width of it kept
            return False
        chat = max(self.NOTES_NARROWEST, min(self.NOTES_WIDEST, int(width)))
        if final:
            self.config.set("side_panel_width", chat, internal=True)
        width = chat * 2 if wide else chat
        if self._notes_on_a_mac():
            from . import mac_panel
            if not mac_panel.has_side_panel(self.NOTES_TITLE):
                return False

            def sized() -> None:
                if mac_panel.resize_side_panel(self.NOTES_TITLE, width) and final:
                    self._make_room_on_a_mac()
            self.queue.put(("call", sized))
            return True
        if sys.platform != "win32":
            return False
        dock = getattr(self, "_dock", None)
        done = app_window.resize_titled(self._notes_titles(), width, dock=dock if final else None)
        # SAID WHEN IT IS LET GO (2026-11-16: "我现在都没法拖动size了") - what was asked and what came of it.
        if final or not done:
            log.info("the side panel dragged to %s (%s): %s%s", width, "let go" if final else "while dragged",
                     "done" if done else "NOT done", ", pinned" if dock is not None and dock.docked else "")
        return done

    def widen_notes_panel(self, wide: bool) -> bool:
        """The side panel made wide for its picture's column, or as narrow as the chat again - the room it
        takes on the screen with it. False where it cannot be here: the page shows the picture above the
        chat instead."""
        from ..platform_layer import app_window
        width = self._notes_width() * 2 if wide else self._notes_width()
        if self._notes_on_a_mac():
            from . import mac_panel
            if not mac_panel.has_side_panel(self.NOTES_TITLE):
                return False
            # On the main thread, as every window of a Mac's is changed (`hide_notes_panel`) - and the windows
            # it now covers moved over too.
            def wider() -> None:
                if mac_panel.resize_side_panel(self.NOTES_TITLE, width):
                    self._make_room_on_a_mac()
            self.queue.put(("call", wider))
            return True
        if sys.platform != "win32":
            return False
        return app_window.resize_titled(self._notes_titles(), width, dock=getattr(self, "_dock", None))

    def hide_notes_panel(self) -> bool:
        """THE SIDE PANEL PUT AWAY - slid out and kept, hidden, to be slid back in (its × and Escape,
        and when the last in it is answered). False where it cannot be: the page closes itself."""
        from ..platform_layer import app_window
        if self._notes_on_a_mac():
            from . import mac_panel
            if not mac_panel.has_side_panel(self.NOTES_TITLE):
                return False
            self.queue.put(("call", lambda: mac_panel.hide_side_panel(self.NOTES_TITLE)))   # main thread
            on_a_mac = getattr(self, "_mac_dock", None)
            if on_a_mac is not None:
                on_a_mac.undock()                        # the windows moved for it, back
            return True
        # PUT AWAY, ITS TAB ALONE LEFT AT THE SCREEN'S EDGE (the user, 2026-11-16: "collapse的时候这个凸起还是在边框外（这样我
        # 可以点击展开)" - "还是一整个bar, 不能就留一个按钮吗"): the panel hidden at once, kept as large as it is (never
        # laid out anew - it comes back at once); a small window of the avatar's own, the tab, in the middle of the right
        # edge, above every window - over a film too ("全屏的时候，哪怕是collapse状态，那个expand button还是要能显示").
        dock = getattr(self, "_dock", None)
        hidden = app_window.slide_out_titled(self._notes_titles(), dock=dock)
        if not hidden:
            DesktopAgent._give_the_room_back(self)
        DesktopAgent._tell_the_panel(self, peeking=hidden)
        self._side_tab_on, self._peek_hidden = bool(hidden), False
        if hidden:
            self.queue.put(("call", lambda: DesktopAgent._show_side_tab(self)))
        return hidden

    #: The tab's size, at 96 dpi; its colours (the panel's dark, the accent of its edge and arrow).
    SIDE_TAB_SIZE = (16, 64)          # (the user, 2026-11-17: "高度不变，宽度小一点" - 24 wide was)
    SIDE_TAB_LOOK = {"bg": "#232325", "edge": "#4b91ff", "dot": "#4b91ff", "key": "#010203"}

    def _show_side_tab(self) -> None:
        """The tab, shown at the middle of the right edge of the screen the panel was on - on Tk's thread."""
        if not getattr(self, "_side_tab_on", False):
            return
        look = self.SIDE_TAB_LOOK
        tab = getattr(self, "_side_tab", None)
        # THE SAME SIZE AS THE PANEL'S OWN TAB (the user, 2026-11-17: "这个button在collapse和expand状态下的size大小要一
        # 致"): 24 x 64 at 96 dpi, by the dpi of the screen it goes on - the panel's page is drawn by that screen's dpi
        # too (and its tab kept at that size whatever the page's zoom); Tk's own dpi is the first screen's only.
        scale = DesktopAgent._side_tab_scale(self)
        if tab is not None and getattr(self, "_side_tab_scale_built", None) != scale:
            try:
                tab.destroy()
            except tk.TclError:
                pass
            tab = self._side_tab = None
        if tab is None:
            tab = self._side_tab = tk.Toplevel(self.root)
            tab.withdraw()
            tab.overrideredirect(True)                 # no frame, no taskbar button
            tab.attributes("-topmost", True)
            tab.configure(bg=look["key"])
            try:
                tab.attributes("-transparentcolor", look["key"])   # its rounded edge, nothing round it
            except tk.TclError:
                pass
            self._side_tab_scale_built = scale
            wide, tall = (round(one * scale) for one in self.SIDE_TAB_SIZE)
            canvas = tk.Canvas(tab, width=wide, height=tall, bg=look["key"], highlightthickness=0, bd=0,
                               cursor="hand2")
            canvas.pack()
            r, e = round(10 * scale), max(1, round(1.5 * scale))
            # Rounded on the left, flat against the screen's edge on the right.
            points = [r, e, wide + r, e, wide + r, tall - e, r, tall - e, e, tall - e, e, tall - r, e, r, e, e]
            canvas.create_polygon(points, smooth=True, fill=look["bg"], outline=look["edge"], width=e)
            canvas.create_text(wide // 2 + round(1 * scale), tall // 2, text="«", fill=look["edge"],
                               font=("Segoe UI", -max(12, round(15 * scale)), "bold"))   # pixels, as the page's
            self._side_tab_dot = canvas.create_oval(wide - round(9 * scale), round(7 * scale), wide - round(3 * scale),
                                                    round(13 * scale), fill=look["dot"], outline="", state="hidden")
            self._side_tab_canvas = canvas
            canvas.bind("<ButtonRelease-1>", lambda _event: self.open_notifications())
            self._side_tab_size = (wide, tall)
        wide, tall = self._side_tab_size
        right, top, bottom = DesktopAgent._side_tab_edge(self)
        tab.geometry(f"{wide}x{tall}+{right - wide}+{(top + bottom) // 2 - tall // 2}")
        tab.deiconify()
        tab.attributes("-topmost", True)
        tab.lift()
        DesktopAgent._side_tab_unread(self)

    def _side_tab_scale(self) -> float:
        """The dpi of the screen the pointer is on, over 96 - the screen the tab goes on - times Windows' text size
        (Settings > Accessibility > Text size): the panel's page, WebView2, is drawn that much larger too (2026-11-17:
        at 179 % its tab was 43 x 115 pixels, this one 24 x 64 - "collapse和expand还是不一样大小")."""
        return DesktopAgent._screen_scale(self) * DesktopAgent._text_scale()

    @staticmethod
    def _text_scale() -> float:
        """Windows' text size, 1.0 to 2.25 (HKCU Accessibility TextScaleFactor, in per cent); 1.0 where not set."""
        if sys.platform != "win32":
            return 1.0
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Accessibility") as key:
                said, _kind = winreg.QueryValueEx(key, "TextScaleFactor")
            return max(1.0, min(2.25, float(said) / 100.0))
        except (OSError, ValueError, TypeError):
            return 1.0

    def _screen_scale(self) -> float:
        """The dpi of the screen the pointer is on, over 96."""
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            user32.MonitorFromPoint.restype = ctypes.c_void_p
            monitor = user32.MonitorFromPoint(point, 2)
            dpi_x, dpi_y = ctypes.c_uint(), ctypes.c_uint()
            if ctypes.windll.shcore.GetDpiForMonitor(ctypes.c_void_p(monitor), 0, ctypes.byref(dpi_x),
                                                      ctypes.byref(dpi_y)) == 0 and dpi_x.value:
                return max(1.0, dpi_x.value / 96.0)
        except Exception:
            log.debug("the screen's dpi could not be read", exc_info=True)
        try:
            return max(1.0, float(self.root.winfo_fpixels("1i")) / 96.0)
        except Exception:
            return 1.0

    def _side_tab_edge(self) -> tuple:
        """The right edge of the screen the panel was on - its top and bottom (the pointer's screen, else the first)."""
        # Read in this thread's own units - Tk's - never changing how this thread is told the screen's size.
        try:
            import ctypes
            from ctypes import wintypes

            class _Info(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                            ("dwFlags", wintypes.DWORD)]
            user32 = ctypes.windll.user32
            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            user32.MonitorFromPoint.restype = ctypes.c_void_p
            info = _Info()
            info.cbSize = ctypes.sizeof(_Info)
            if user32.GetMonitorInfoW(ctypes.c_void_p(user32.MonitorFromPoint(point, 2)), ctypes.byref(info)):
                return info.rcMonitor.right, info.rcWork.top, info.rcWork.bottom
        except Exception:
            log.debug("the screen the tab goes on could not be read", exc_info=True)
        return self.root.winfo_screenwidth(), 0, self.root.winfo_screenheight()

    def _side_tab_unread(self) -> None:
        """Its dot: something told and not yet seen in the panel."""
        canvas, dot = getattr(self, "_side_tab_canvas", None), getattr(self, "_side_tab_dot", None)
        if canvas is None or dot is None:
            return
        try:
            unread = bool(self.app.notifications.for_the_side(1))
        except Exception:
            unread = False
        try:
            canvas.itemconfigure(dot, state="normal" if unread else "hidden")
        except tk.TclError:
            pass

    def _hide_side_tab(self) -> None:
        tab = getattr(self, "_side_tab", None)
        if tab is not None:
            try:
                tab.withdraw()
            except tk.TclError:
                pass

    #: How often the tab looks whether a film fills the screen.
    PEEK_LOOK_MS = 2000

    def _watch_peek(self) -> None:
        """WHILE PUT AWAY TO ITS TAB: hidden while a film, a game or a slide show fills the screen, shown again after
        (the user, 2026-11-16: full screen, the taskbar was not covered while the panel stood above)."""
        if not getattr(self, "_side_tab_on", False) or getattr(self, "_peek_watching", False):
            return
        self._peek_watching = True

        def look() -> None:
            if not getattr(self, "_side_tab_on", False):
                self._peek_watching = False
                return
            from ..platform_layer import app_window
            film = DesktopAgent._film_in_front(self)
            if film != getattr(self, "_peek_hidden", False):
                app_window.show_titled(self._notes_titles(), not film)
                self._peek_hidden = film
            self.root.after(self.PEEK_LOOK_MS, look)
        look()

    def toggle_notes_panel(self) -> bool:
        """ITS TAB PRESSED: put away, or back - whether it is put away now."""
        if not self._notes_open_now():
            self.open_notifications()
            return False
        return self.hide_notes_panel()

    def _tell_the_panel(self, peeking: bool, by_user: bool = False, focus: bool = False) -> None:
        """The page told whether it is put away to its tab - its arrow pointing the way it will go - and whether the
        user opened it themselves: what it shows is seen then (notes.js sideLooked); `focus`: the keys to its box."""
        side = getattr(self.app, "side_events", None)
        if side is not None:
            try:
                side.tell("panel", peeking=bool(peeking), by_user=bool(by_user), focus=bool(focus))
            except Exception:
                log.debug("the side panel could not be told", exc_info=True)

    def _waiting_list_open(self, opened: bool) -> None:
        """THE WAITING LIST OPEN, NO BUBBLE BESIDE IT (the user, 2026-11-11: "我打开waiting
        notifications时...那些通知先隐藏，关闭之后如果还有再显示"): the one shown back to the head of the
        line, unanswered, and none drawn while it is open; closed, they come again - those still
        unread (one answered in the list meanwhile is not shown again)."""
        self._list_holds = opened
        if opened:
            # The line of what waits, too: the list is what it says (`_show_waiting` draws none
            # while the list is open).
            self._hide_waiting()
            if self._bubble is not None and self._showing is not None:
                self._bubble_queue.appendleft(self._showing)
                try:
                    self._bubble.destroy()
                except tk.TclError:          # pragma: no cover - already gone
                    pass
                self._bubble = None
                self._showing = None
            return
        # ANSWERED IN THE LIST, NOT YET KEPT (the user, 2026-11-13: "这个1 summary还是要很久才关掉。你已经
        # 修这个好多次了，好好修一下"): the list closes on the press of its last answer, BEFORE the
        # answer is written - and the line was drawn from the database then, "1 summary" again, and
        # stayed until the next look. Those answered are not counted while they are kept
        # (`_panel_act` lets go of each once kept, and draws the line again at once).
        if not hasattr(self, "_answering"):
            self._answering = {}
        panel = getattr(self, "_quick_panels", {}).get("waiting")
        for ident in getattr(panel, "gone", ()) or ():
            self._answering.setdefault(str(ident), "list")
        answering = self._answering
        unread = self._still_unread
        # PUT AWAY, IT STAYS AWAY (the user, 2026-11-16: "全屏的时候我把他collapse ... sidebar又会出现"): where the side
        # panel is where they are told, those that came while it was open are in it - told again, the panel opened
        # again at once, every time it was put away. Kept there, for when the user opens it.
        side = DesktopAgent._side_panel_here(self)
        kept = [one for one in self._bubble_queue
                if not one.get("notification_id") or (not side and one["notification_id"] not in answering
                                                      and unread(one["notification_id"]))]
        self._bubble_queue.clear()
        self._bubble_queue.extend(kept)
        self.root.after(300, self._show_next_bubble)
        self._show_waiting()

    def _still_unread(self, notification_id: str) -> bool:
        try:
            shown = self.app.notifications.get(notification_id)
        except Exception:                    # pragma: no cover - shown, then
            return True
        return shown is None or not shown.get("read")

    def _hide_waiting(self) -> None:
        line, self._waiting_line = getattr(self, "_waiting_line", None), None
        self._waiting_at = None
        if line is not None:
            try:
                line.destroy()
            except tk.TclError:          # pragma: no cover - already gone
                pass

    def _show_waiting(self) -> None:
        """The small line beside the avatar: how many wait, a row a kind in its colour (the user,
        2026-11-07: "要写清楚category", "分行", "每行不同的颜色"). It takes no focus."""
        from ..common.notifications import category_of, waiting_lines
        # ONE JUST ANSWERED IS NOT COUNTED, whether or not its answer is kept yet (the user,
        # 2026-11-12: after Good to know "1 recommendation" still stayed two seconds).
        answering = getattr(self, "_answering", None) or {}
        if not answering:
            counts = self.app.notifications.waiting_by_category()
        else:
            counts = {}
            for row in self.app.notifications.waiting(1000):
                if str(row.get("id") or "") in answering:
                    continue
                name = category_of(row)
                counts[name] = counts.get(name, 0) + 1
        kept = sum(counts.values())
        if self._blocked():
            for item in self._bubble_queue:
                name = ("Report" if item.get("report") else "Summaries" if item.get("summary") else
                        "Recommended content" if item.get("recommended") else "Reminders")
                counts[name] = counts.get(name, 0) + 1
        count = sum(counts.values())
        self._waiting_counts = dict(counts)
        panel = getattr(self, "_quick_panels", {}).get("waiting")
        if panel is not None and panel.is_open and kept != getattr(self, "_waiting_kept", kept):
            panel._read_it(quiet=True)
        self._waiting_kept = kept
        # NO LINE BESIDE THE AVATAR WHERE THE SIDE PANEL IS (the user, 2026-11-17: "logo旁边的1 recommendation这种提示直接
        # 取消"): what waits is told in the panel - a dot on its tab while it is put away.
        if DesktopAgent._side_panel_here(self):
            self._hide_waiting()
            return
        # NOT BESIDE THE SIDE PANEL, HOWEVER IT CAME TO BE OPEN (the user, 2026-11-13: the panel open, "1
        # recommendation" still beside the avatar - it was open from before the agent started again): seen
        # open, held - and watched, drawn again once it is put away.
        open_now = getattr(self, "_notes_open_now", None)
        if count and not getattr(self, "_list_holds", False) and callable(open_now) and open_now():
            self._waiting_list_open(True)
            self._notes_since, self._notes_seen = time.monotonic(), True
            if not getattr(self, "_notes_watching", False):
                self._notes_watching = True
                self.root.after(self.NOTES_WATCH_MS, self._watch_notes)
            return
        # NOT BESIDE THE OPEN LIST (the user, 2026-11-11: the list open, "1 recommendation" still
        # there - the blockers' watch drew it again every few seconds). Drawn when it closes.
        if not count or getattr(self, "_list_holds", False):
            self._hide_waiting()
            return
        rows = waiting_lines(counts, self._words)
        if self._waiting_line is not None:
            self._draw_waiting(rows)
            return
        from .on_top import stay_on_top
        look = BUBBLE_LOOK[False]
        line = tk.Toplevel(self.root)
        line.overrideredirect(True)
        line.attributes("-topmost", True)
        line.configure(bg=look["outline"])
        self._waiting_box = tk.Frame(line, bg=look["bg"], padx=8, pady=3, cursor="hand2")
        self._waiting_box.pack(padx=1, pady=1)
        self._waiting_line = line
        self._draw_waiting(rows)
        stay_on_top(line)
        # ZOOMED AS EVERY OTHER WINDOW OF THE AGENT'S (the user, 2026-11-09: "1 recommendation的字体也
        # 太大了，应该这个也是鼠标放上面可以滚轮调整"): the pointer on it, Ctrl and the wheel, or Ctrl
        # + and -; one zoom for them all (zoom_popups).
        self._zoom_keys(line)

    def _redraw_waiting(self) -> None:
        """The line drawn again at the zoom just chosen."""
        if self._waiting_line is not None:
            self._show_waiting()

    def _draw_waiting(self, rows: List[Tuple[str, str]]) -> None:
        """Its rows, drawn again, and the line placed for its new size."""
        from .chat_bubble import beside, work_area
        from .on_top import on_release
        line, box = self._waiting_line, self._waiting_box
        look = BUBBLE_LOOK[False]
        font = self._popup_font()
        try:
            for old in box.winfo_children():
                old.destroy()
            for kind, said in rows:
                row = tk.Label(box, text=said, bg=look["bg"], fg=WAITING_INK.get(kind, look["ink"]),
                               # SMALLER THAN A BUBBLE'S WORDS (the user, 2026-11-09: "字体也太大了"):
                               # it is a hint beside the avatar, not something to read.
                               font=(font[0], sizes.zoomed(max(6, font[1] - 2), self._bubble_zoom())),
                               anchor="w", justify="left", cursor="hand2")
                row.pack(fill="x")
                on_release(row, self._open_waiting)
            on_release(box, self._open_waiting)
            line.update_idletasks()
        except tk.TclError:                  # pragma: no cover - the line went
            return
        self._place_waiting()

    def _avatar_moved(self, event: Any = None) -> None:
        """The avatar's window moved (or its child's changed - not that): the line beside it, too."""
        if event is not None and getattr(event, "widget", None) is not self.agent_window:
            return
        self._place_waiting()

    def _place_waiting(self) -> None:
        """The line beside the avatar where it is now - as large as it is drawn."""
        from .chat_bubble import beside, work_area
        line = getattr(self, "_waiting_line", None)
        if line is None:
            return
        try:
            size = (line.winfo_reqwidth(), line.winfo_reqheight())
            x, y = beside(self._beside_box(), size,
                          (line.winfo_screenwidth(), line.winfo_screenheight()), work_area(line))
            if (x, y) != getattr(self, "_waiting_at", None):
                line.geometry(f"+{x}+{y}")
                self._waiting_at = (x, y)
        except tk.TclError:                  # pragma: no cover - the line went
            pass

    def _copyable(self, parent: Any, text: str, font: tuple, ink: str, look: Dict[str, str],
                  wide: int, tallest: int = 0) -> Any:
        """Words that look like a label and can be selected and copied: Ctrl+C,
        Ctrl+A, or the right button for all of them. `tallest`, in pixels: past it they scroll,
        with a bar. An address in them is a link (the user, 2026-11-06: a call's, to be joined)."""
        import tkinter.font as tkfont
        from .chat_bubble import links_in, write_links
        written, text = text, "".join(words for words, _address in links_in(text))
        measured = tkfont.Font(font=font)
        chars = max(1, wide // max(1, measured.measure("0")))
        # Its height by the font: a Text not yet on the screen counts its lines at
        # a width of nothing, and a bubble came out taller than the screen.
        lines = lines_of(text, measured.measure, chars * measured.measure("0"))
        shown = max(3, tallest // max(1, measured.metrics("linespace"))) if tallest else lines
        holder = tk.Frame(parent, bg=look["bg"]) if lines > shown else None
        box = tk.Text(holder or parent, wrap="word", font=font, bg=look["bg"], fg=ink, relief="flat",
                      bd=0, highlightthickness=0, padx=0, pady=0, cursor="xterm", takefocus=0,
                      height=min(lines, shown), width=chars,
                      selectbackground=look["button_hover"], selectforeground=look["ink"],
                      inactiveselectbackground=look["button_hover"])
        write_links(box, "1.0", written, (), look["primary_hover"])
        box.configure(state="disabled")

        def everything(_event: Any = None) -> str:
            box.tag_add("sel", "1.0", "end-1c")
            return "break"
        from .on_top import with_copy
        box.bind("<Button-1>", lambda _e: box.focus_force(), add="+")
        box.bind("<Control-a>", everything)
        with_copy(box, self._words("Copy"), whole=text)
        if holder is None:
            return box
        from .chat_bubble import ScrollBar
        bar = ScrollBar(holder, wide=sizes.px(parent, BAR_WIDE), thumb=sizes.px(parent, BAR_THUMB),
                        bg=look["bg"], ink=look["muted"], hot=look["ink"])
        box.configure(yscrollcommand=bar.shown)
        bar.scrolls(box)
        box.pack(side="left", fill="both", expand=True)
        return holder

    def _bubble_chat(self, parent: Any, item: Dict[str, Any], look: Dict[str, str],
                     font: tuple) -> None:
        """A BOX UNDER EVERY NOTIFICATION: what is written is a lesson, and the
        notification closes. Nothing answers it."""
        from .on_top import on_release
        row = tk.Frame(parent, bg=look["button"], padx=8, pady=5)
        row.pack(fill="x", pady=(8, 0))
        box = tk.Entry(row, bg=look["button"], fg=look["ink"], insertbackground=look["ink"],
                       relief="flat", bd=0, highlightthickness=0, cursor="xterm",
                       font=(font[0], max(sizes.SMALLEST_WORDS, font[1] - 1)))
        box.pack(side="left", fill="x", expand=True)
        hint = self._words("Tell the agent for next time...")
        box.insert(0, hint)
        box.configure(fg=look["muted"])

        def begin(_event: Any = None) -> None:
            if box.get() == hint:
                box.delete(0, "end")
                box.configure(fg=look["ink"])
            box.focus_force()

        def send(_event: Any = None) -> str:
            said = box.get().strip()
            if said and said != hint:
                self._bubble_said(item, said)
            return "break"
        box.bind("<Button-1>", begin, add="+")
        box.bind("<Return>", send)
        go = tk.Label(row, text=self._words("Send"), bg=look["button"], fg=look["ink"],
                      font=(font[0], max(sizes.SMALLEST_WORDS, font[1] - 1), "bold"), cursor="hand2",
                      padx=4)
        go.pack(side="right")
        on_release(go, send)

    def _bubble_said(self, item: Dict[str, Any], said: str) -> None:
        self._close_bubble()

        def tell() -> None:
            try:
                self.app.notification_chat(item["notification_id"], said)
            except Exception:
                log.exception("what was written on a notification could not be taken")
        threading.Thread(target=tell, name="bubble-chat", daemon=True).start()

    def _bubble_read_reply(self, item: Dict[str, Any]) -> None:
        """The whole reply, in the chat page, on its conversation."""
        self._conversation_id = item["conversation"]
        self._bubble_answered("", "", item["emphasis"], item.get("notification_id", ""))
        self.open_chat()

    def _bubble_approvals(self, notification_id: str) -> Optional[tuple]:
        """(the things, until when) when this notification is the plan's call to approve them."""
        if not notification_id or getattr(self.app, "notifications", None) is None:
            return None
        try:
            said = (self.app.notifications.get(notification_id) or {}).get("metadata") or {}
        except Exception:
            return None
        agenda = said.get("agenda") or {}
        if not isinstance(agenda, dict) or not agenda.get("approve"):
            return None
        return [str(one) for one in agenda["approve"]], str(agenda.get("until") or "")

    def _open_summary(self, item: Dict[str, Any]) -> None:
        """The report opened from its notification: the notification read and closed."""
        self._bubble_answered("", "", item.get("emphasis"), item.get("notification_id", ""))
        self.open_report()

    def open_report_window(self) -> bool:
        """The app's window turned to the report, for a place in it the page then asks for (`report_wanted`) -
        the side panel put away, not to stand in front of it; PINNED, LEFT WHERE IT IS (the user, 2026-11-17: "点了你的
        report, 打开窗口之后，pin的sidebar直接collapse了") - its strip is its own, the report's window beside it."""
        self.open_url("/#report")
        config = getattr(self, "config", None)
        if config is not None and config.get("side_panel_pinned", False):
            return True
        try:
            self.hide_notes_panel()
        except Exception:                        # pragma: no cover - left where it is
            log.debug("the side panel could not be put away", exc_info=True)
        return True

    def open_report(self) -> None:
        """THE REPORT, IN ITS OWN TAB OF THE PAGE (the user, 2026-11-12: "summary要和newspaper一样单独有一个
        panel") - its latest issue first; not a panel beside the avatar ("现在汇报的那个窗口很卡"). Opened,
        its summaries are read: "1 summary" goes from beside the avatar at once."""
        def read() -> None:
            try:
                self.app.notifications.read_report_summaries()
            except Exception:                    # pragma: no cover - read at the page's opening
                log.debug("the report's summaries could not be read", exc_info=True)
        threading.Thread(target=read, name="report-read", daemon=True).start()
        self.open_url("/#report")

    def _bubble_is_summary(self, notification_id: str) -> bool:
        """Whether this notification is a summary of what waits for approval (agent/approvals.py)."""
        if not notification_id or getattr(self.app, "notifications", None) is None:
            return False
        try:
            said = (self.app.notifications.get(notification_id) or {}).get("metadata") or {}
        except Exception:
            return False
        return bool(said.get("approvals"))

    def _open_approvals(self, ids: List[str], until: str) -> None:
        """The approvals panel, made off Tk's thread (it reads what each work made)."""
        opening = getattr(self.app, "open_agenda_approvals", None)
        if opening is None:
            return

        def ask() -> None:
            try:
                opening(ids, until)
            except Exception:
                log.warning("the approvals panel could not be opened", exc_info=True)
        threading.Thread(target=ask, name="open-approvals", daemon=True).start()

    def _bubble_part_of(self, notification_id: str) -> str:
        """The work a notification is part of, read as it is drawn: the link is
        made by the state heartbeat, after the notification was."""
        try:
            names = [one["title"] for one in self.app.item_links.of("notification", notification_id)]
        except Exception:
            return ""
        return self._words("Part of: {0}").replace("{0}", ", ".join(names)) if names else ""

    def _bubble_picture(self, name: str, width: int) -> Any:
        """A notification's picture, as wide as the card at most - or None, and
        the notification is shown without it."""
        if not name or not PIL_AVAILABLE:
            return None
        try:
            path = self.app.monitor.picture_path(name)
            if path is None:
                return None
            with Image.open(path) as found:
                picture = found.convert("RGBA")
            picture.thumbnail((width, BUBBLE_PICTURE_HEIGHT))
            return ImageTk.PhotoImage(picture)
        except Exception:
            log.debug("a notification's picture could not be drawn", exc_info=True)
            return None

    def _next_bubble(self) -> Optional[Dict[str, Any]]:
        """The next in line. While a blocker holds, only what comes through it (the user,
        2026-11-06: a scam warning is not held for a meeting)."""
        if not self._blocked():
            return self._bubble_queue.popleft() if self._bubble_queue else None
        for one in self._bubble_queue:
            if one.get("through"):
                self._bubble_queue.remove(one)
                return one
        return None

    def _show_next_bubble(self) -> None:
        """Draw the next bubble, if the last one has been dealt with - not while the waiting list
        is open (`_waiting_list_open`)."""
        if getattr(self, "_list_holds", False):
            return
        item = None if self._bubble is not None else self._next_bubble()
        if item is None:
            return
        # THE AGENT SPEAKS IN THE SIDE PANEL (the user, 2026-11-16: "这种Notification和chat以后都是在sidebar里
        # (notification相当于是agent主动说话)"): where it opens, a notification is its message there - the panel
        # shown over the other windows, never taking the keys from where the user types; the bubbles waiting
        # with it are in it too.
        if item.get("notification_id") and DesktopAgent._side_panel_here(self):
            self._bubble_queue = collections.deque(
                one for one in self._bubble_queue if not one.get("notification_id"))
            self.open_notifications(quietly=True)
            return
        self._showing = item
        look = RECOMMENDED_LOOK if item.get("recommended") else BUBBLE_LOOK[bool(item["emphasis"])]
        options = self._options_for(item["question_id"])

        bubble = tk.Toplevel(self.root)
        bubble.overrideredirect(True)
        bubble.attributes("-topmost", True)
        bubble.configure(bg=look["outline"])
        font = self._popup_font()
        # ITS OWN SIZE OF WORDS, as every panel has (the user, 2026-11-04: "鼠标放上去ctrl +-缩放就行"):
        # Ctrl with + and - or the wheel, kept, shown in no Settings.
        family, points = font[0], sizes.zoomed(font[1], self._bubble_zoom())
        font = (family, points) + tuple(font[2:])
        self._zoom_keys(bubble)
        # The bubble is sized from the text, not fixed: a larger font in a
        # fixed window just cuts the message off, which defeats setting it.
        width = 340 + (points - 9) * 20
        # The outline is the window behind the card: tk draws no outline on an
        # overrideredirect window, so the edge has to be something with a size.
        frame = tk.Frame(bubble, bg=look["bg"], padx=20, pady=16)
        frame.pack(fill="both", expand=True, padx=1, pady=1)

        # What it is, and how many wait behind it.
        head = tk.Frame(frame, bg=look["bg"])
        head.pack(fill="x")
        dot = tk.Canvas(head, width=10, height=10, bg=look["bg"], highlightthickness=0, bd=0)
        dot.create_oval(1, 1, 9, 9, fill=SUMMARY_DOT if item.get("summary") else look["dot"],
                        outline="")
        dot.pack(side="left", padx=(0, 7))
        # What the model sends asks nothing, though it has answers of its own.
        kind = "Work summary" if item.get("summary") else \
            "Recommended content" if item.get("recommended") else \
            "Notification" if item.get("content") else "Question" if item["question_id"] else \
            "Notification" if item.get("notification_id") else "From your agent"
        tk.Label(head, text=self._words(kind), bg=look["bg"], fg=look["muted"],
                 font=(family, max(sizes.SMALLEST_WORDS, points - 2), "bold")).pack(side="left")
        # How many are behind this one. Without it, answering reads as having
        # done nothing when the next bubble appears in the same place. The
        # label is made even when the count is zero, because more may arrive
        # while this one is still waiting to be answered.
        self._bubble_count = tk.Label(head, text="", bg=look["bg"], fg=look["muted"],
                                      font=(family, max(sizes.SMALLEST_WORDS, points - 2)))
        self._bubble_count.pack(side="right")
        self._refresh_bubble_count()

        # Its title in bold over its words - a notification is "title: words".
        # WHOLE (the user, 2026-11-05: the day's report stopped in mid-word at 400 letters, with
        # nowhere to read the rest): past a part of the screen, its words scroll.
        text = item["text"]
        title, words = "", text
        if item.get("notification_id") and ": " in text:
            title, words = text.split(": ", 1)
        # TO BE COPIED, ITS TITLE AND ITS WORDS (the user, 2026-11-04).
        if title:
            self._copyable(frame, title, (family, points + 1, "bold"), look["ink"], look,
                           width - 34).pack(fill="x", pady=(8, 0))
        self._copyable(frame, words, (family, points), look["ink"] if not title else look["muted"],
                       look, width - 34,
                       tallest=int(bubble.winfo_screenheight() * WORDS_SHARE_OF_SCREEN)
                       ).pack(fill="x", pady=(4 if title else 8, 0))
        # WHY IT IS TOLD (the user, 2026-11-04).
        if item.get("reason"):
            self._copyable(frame, self._words("Why: {0}").replace("{0}", item["reason"]),
                           (family, max(sizes.SMALLEST_WORDS, points - 1), "italic"), look["muted"], look,
                           width - 34).pack(fill="x", pady=(6, 0))
        # WHAT SET IT OFF (the user, 2026-11-06).
        if item.get("trigger"):
            self._copyable(frame, self._words("Trigger: {0}").replace(
                "{0}", self._words(item["trigger"])
                + (f" - {item['set_off']}" if item.get("set_off") else "")),
                (family, max(sizes.SMALLEST_WORDS, points - 1)),
                look["muted"], look, width - 34).pack(fill="x", pady=(4, 0))
        part_of = self._bubble_part_of(item.get("notification_id", ""))
        if part_of:
            tk.Label(frame, text=part_of, bg=look["bg"], fg=look["muted"],
                     font=(family, max(sizes.SMALLEST_WORDS, points - 2)), wraplength=width - 34, justify="left",
                     anchor="w").pack(fill="x", pady=(6, 0))
        picture = self._bubble_picture(item.get("picture", ""), width - 34)
        if picture is not None:
            self._bubble_image = picture         # Tk keeps no hold on it itself
            tk.Label(frame, image=picture, bg=look["bg"], bd=0).pack(anchor="w", pady=(8, 0))

        buttons = tk.Frame(frame, bg=look["bg"])
        buttons.pack(fill="x", pady=(12, 0))
        # A NOTIFICATION'S BUTTONS EACH SAY WHAT HAPPENS - and are long for
        # it, so one to a line, each as wide as the card.
        stacked = bool(item.get("notification_id"))
        if item.get("notification_id") and not item.get("question_id"):
            from ..agent.feedback.templates import GOT_IT
            options = [dict(one, label=GOT_IT) if not one.get("key") else one
                       for one in options]

        def button(words: str, act: Any, primary: bool = False) -> None:
            fill, hover = ((look["primary"], look["primary_hover"]) if primary
                           else (look["button"], look["button_hover"]))
            face = tk.Label(buttons, text=words, bg=fill,
                            fg=look["on_primary"] if primary else look["ink"],
                            font=(family, max(sizes.SMALLEST_WORDS, points - 1),
                                  "bold" if primary else "normal"),
                            padx=12, pady=5, cursor="hand2",
                            anchor="w" if stacked else "center",
                            wraplength=width - 60 if stacked else 0, justify="left")
            face.bind("<Enter>", lambda _e: face.configure(bg=hover))
            face.bind("<Leave>", lambda _e: face.configure(bg=fill))
            on_release(face, act)                # done when let go over it
            if stacked:
                face.pack(side="top", fill="x", pady=(0, 6))
            else:
                face.pack(side="right", padx=(6, 0))
        if item.get("conversation"):
            button(self._words("Read the whole reply"), lambda: self._bubble_read_reply(item),
                   primary=True)
        # THE PLAN'S APPROVALS, OPENED FROM ITS REMINDER (the user, 2026-11-09: "点提醒就打开"): in
        # the approvals panel - the rows to answer, what the agent made below.
        approving = self._bubble_approvals(item.get("notification_id", ""))
        if approving is not None:
            button(self._words("Open the approvals"), lambda: self._open_approvals(*approving),
                   primary=True)
        elif self._bubble_is_summary(item.get("notification_id", "")):
            # THE SUMMARY OF WHAT WAITS FOR APPROVAL (2026-11-12): all of it, in one panel - and the
            # summary closed as it opens (the user: "我点open approval之后，这个notification应该关掉").
            button(self._words("Open the report"), lambda: self._open_summary(item),
                   primary=True)
        for option in options:
            # A button with no words on it is not a button. Whatever the
            # option carries, something legible goes on the face of it.
            button(self._words(str(option.get("label") or option.get("key") or "OK")),
                   lambda key=option.get("key", ""): self._bubble_answered(
                       item["question_id"], key, item["emphasis"],
                       item.get("notification_id", "")),
                   primary=(bool(option.get("primary")) or len(options) == 1)
                   and not item.get("conversation"))
        # REMIND ME LATER, on a reminder: the same one again in half an hour.
        if item.get("notification_id") and item.get("template") != "passive" \
                and not item.get("question_id"):
            from ..agent.feedback.templates import delay_label
            button(self._words(delay_label(self.app.REMIND_LATER_MINUTES * 60)),
                   lambda: self._bubble_later(item["notification_id"]))
        # ADD TO TO-DO, on every notification: no more notifications about
        # it, and listed in To do on the Overview. A question offers it
        # among its own options.
        if item.get("notification_id") and not item.get("question_id"):
            from ..agent.feedback.templates import TO_TODO_LABEL
            button(self._words(TO_TODO_LABEL), lambda: self._bubble_todo(item["notification_id"]))
        # EVERY NOTIFICATION CAN BE CALLED A BAD ONE - a quiet link under the
        # rest. Saying so dismisses it and is kept as a lesson - the
        # notification and the event it came from, whole - which the agent
        # heartbeat reads before it schedules the next
        # (heartbeat/core/calendar_nature.py).
        if item.get("notification_id"):
            from ..agent.feedback.templates import BAD_LABEL
            bad = tk.Label(frame, text=self._words(BAD_LABEL), bg=look["bg"], fg=look["muted"],
                           font=(family, max(sizes.SMALLEST_WORDS, points - 2), "underline"), cursor="hand2",
                           wraplength=width - 34, justify="left", anchor="w")
            bad.bind("<Enter>", lambda _e: bad.configure(fg=look["ink"]))
            bad.bind("<Leave>", lambda _e: bad.configure(fg=look["muted"]))
            on_release(bad, lambda: self._bubble_bad(item["notification_id"]))
            bad.pack(fill="x", pady=(4, 0))
            self._bubble_chat(frame, item, look, (family, points))
        bubble.update_idletasks()
        # Rounded where Windows rounds windows (11 and later).
        if sys.platform.startswith("win"):
            try:
                import ctypes
                handle = ctypes.windll.user32.GetParent(bubble.winfo_id()) or bubble.winfo_id()
                prefer = ctypes.c_int(2)                 # DWMWCP_ROUND
                ctypes.windll.dwmapi.DwmSetWindowAttribute(handle, 33, ctypes.byref(prefer),
                                                           ctypes.sizeof(prefer))
            except Exception:                            # pragma: no cover - square, then
                pass
        bubble.configure(width=width)

        bubble.update_idletasks()
        # BESIDE THE AVATAR (the user, 2026-11-01): at its left, level with
        # its middle - at its right where the left has no room.
        # ALL OF IT ON THE SCREEN, ITS BUTTONS ABOVE THE TASKBAR (the user,
        # 2026-11-02: "notification是不是太靠下了，我都不能点击button了"): its
        # size is its card's - the window's own, asked before it is shown,
        # was 1 pixel tall, and a bubble put level with the avatar's middle
        # ran past the bottom of the screen (measured: to 1204 of 1080) -
        # and it is placed again by its size once shown.
        from .chat_bubble import beside, work_area
        area = work_area(bubble, self._avatar_box())
        screen = (bubble.winfo_screenwidth(), bubble.winfo_screenheight())
        size = (max(bubble.winfo_reqwidth(), frame.winfo_reqwidth() + 2),
                max(bubble.winfo_reqheight(), frame.winfo_reqheight() + 2))
        x, y = beside(self._beside_box(), size, screen, area)
        bubble.geometry(f"+{x}+{y}")
        bubble.update_idletasks()
        shown = (bubble.winfo_width(), bubble.winfo_height())
        if shown[1] > size[1] or shown[0] > size[0]:
            x, y = beside(self._beside_box(), shown, screen, area)
            bubble.geometry(f"+{x}+{y}")
        # MOVED BY HAND (the user: "notification无法移动"): dragged by any of
        # it but its buttons - each press of those stays a press.
        self._draggable_bubble(bubble, frame)
        from .on_top import stay_on_top
        stay_on_top(bubble)                  # on a Mac, asked before it was laid out: lost
        self._bubble = bubble
        # Counted now it is the one shown: before, it was not yet, and the
        # count stayed empty until another arrived.
        self._refresh_bubble_count()
        # Deliberately no timer. A bubble that erases itself is one the user
        # can miss entirely, and if it carries a question, the question's
        # moment goes with it.

    @staticmethod
    def _draggable_bubble(bubble: Any, frame: Any) -> None:
        """The bubble dragged by its card and its words - never by a button
        (a label that answers a press, by its hand cursor)."""
        grabbed = {"at": None}

        def grab(event: Any) -> None:
            grabbed["at"] = (event.x_root - bubble.winfo_x(), event.y_root - bubble.winfo_y())

        def move(event: Any) -> None:
            if grabbed["at"] is None:
                return
            dx, dy = grabbed["at"]
            try:
                bubble.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")
            except tk.TclError:                          # pragma: no cover - answered meanwhile
                pass

        def every(widget: Any) -> None:
            try:
                if str(widget.cget("cursor")) in ("hand2", "xterm"):
                    return                               # a press, or words to select
            except tk.TclError:
                pass
            widget.bind("<ButtonPress-1>", grab, add="+")
            widget.bind("<B1-Motion>", move, add="+")
            for child in widget.winfo_children():
                every(child)
        every(frame)

    def _refresh_bubble_count(self) -> None:
        """Keep "N more waiting" true while the bubble is on screen."""
        label = getattr(self, "_bubble_count", None)
        if label is None or self._bubble is None:
            return
        waiting = len(self._bubble_queue)
        try:
            label.configure(text=self._fill("{0} more waiting", waiting) if waiting else "")
        except tk.TclError:                # pragma: no cover - window gone
            pass

    def _options_for(self, question_id: str) -> List[Dict[str, Any]]:
        """The buttons this bubble offers.

        A question brings its own, from the template it was built with, so the
        bubble and the conversation view offer the same words and the same
        consequences. Anything else gets one button, because "it waits until
        you answer" needs something to answer with.
        """
        if question_id:
            try:
                question = self.app.questions.get(question_id)
            except Exception:              # pragma: no cover - a bubble is an aid
                log.debug("could not read question %s", question_id, exc_info=True)
                question = None
            if question is not None and question.status == "pending":
                offered = list(question.options)
                if offered:
                    offered[0] = {**offered[0], "primary": True}
                    return offered
        return [{"key": "", "label": "Got it", "primary": True}]

    def _bubble_answered(self, question_id: str, key: str, emphasis: bool,
                         notification_id: str = "") -> None:
        """Close this bubble, record the answer, and show the next.

        The bubble closes first. Answering touches the database and the
        scheduler, and a button that stays down while that happens reads as a
        button that did not work.
        """
        self._answered_now()
        self._close_bubble()
        if question_id and key:
            threading.Thread(
                target=self._then_waiting(self._answer_question),
                args=(question_id, key, notification_id),
                name="bubble-answer", daemon=True).start()
        elif notification_id:
            # "Got it (no more notifications)": read, and that is the end of it.
            threading.Thread(target=self._then_waiting(self.app.notification_got_it, read=True),
                             args=(notification_id,), name="bubble-read",
                             daemon=True).start()
        # Dismissing a proactive message completes the interaction. Do not
        # open the browser here: webbrowser.open activates its macOS Space and
        # pulls the user away from the desktop they are using. The character
        # click remains the explicit way to open the conversation.

    def _bubble_later(self, notification_id: str) -> None:
        """Close this bubble; the same reminder comes back in half an hour."""
        self._answered_now()
        self._close_bubble()
        threading.Thread(target=self._then_waiting(self._remind_later, read=True),
                         args=(notification_id,),
                         name="bubble-later", daemon=True).start()

    def _then_waiting(self, work: Any, read: bool = False) -> Any:
        """`work`, then the line of what waits drawn again at once (the user, 2026-11-09: closed,
        "1 recommendation" stayed a second or more) - not at the next look, seconds away. `read`:
        its notification (the first of `args`) read FIRST and the line drawn then (the user,
        2026-11-11: "X summary/notification 还是要等几秒才关闭" - the learning from the answer took
        seconds, and the line waited for it)."""
        def done(*args: Any) -> None:
            try:
                if read and args and args[0]:
                    try:
                        self.app.notifications.mark_read(str(args[0]))
                    finally:
                        self.on_main_thread(self._show_waiting)
                work(*args)
            finally:
                # Kept now - read or not, as it was answered: counted as the database says.
                for one in args:
                    getattr(self, "_answering", {}).pop(str(one), None)
                self.on_main_thread(self._show_waiting)
        return done

    def _answered_now(self) -> None:
        """THE ANSWERED ONE OFF THE LINE BESIDE THE AVATAR AT ONCE - on the press, before anything
        is kept (the user, 2026-11-12: after Good to know "1 recommendation" still stayed two
        seconds): the line drawn from what it last showed, less this one, touching no database;
        and not counted again until its answer is kept (`_show_waiting`)."""
        item = self._showing or {}
        notification_id = str(item.get("notification_id") or "")
        if not notification_id:
            return
        if not hasattr(self, "_answering"):
            self._answering = {}
        if notification_id in self._answering:
            return
        name = ("Report" if item.get("report") else "Summaries" if item.get("summary") else
                "Recommended content" if item.get("recommended") else "Reminders")
        counts = dict(getattr(self, "_waiting_counts", None) or {})
        if name == "Reminders" and not counts.get(name) and counts.get("Other"):
            name = "Other"                   # one not a reminder: counted with the rest (category_of)
        self._answering[notification_id] = name
        if counts.get(name):
            counts[name] -= 1
        counts = {kind: n for kind, n in counts.items() if n > 0}
        self._waiting_counts = counts
        try:
            if not counts:
                self._hide_waiting()
            elif getattr(self, "_waiting_line", None) is not None:
                from ..common.notifications import waiting_lines
                self._draw_waiting(waiting_lines(counts, self._words))
        except tk.TclError:                  # pragma: no cover - drawn at the next look
            pass

    def _remind_later(self, notification_id: str) -> None:
        try:
            self.app.remind_later(notification_id)
        except Exception:                  # pragma: no cover - reported, not fatal
            log.exception("could not remind again about %s", notification_id)

    def _bubble_todo(self, notification_id: str) -> None:
        """Close this bubble; no more notifications about it, and it is on
        the to-do list."""
        self._answered_now()
        self._close_bubble()
        threading.Thread(target=self._then_waiting(self._to_todo, read=True), args=(notification_id,),
                         name="bubble-todo", daemon=True).start()

    def _to_todo(self, notification_id: str) -> None:
        try:
            self.app.notification_to_todo(notification_id)
        except Exception:                  # pragma: no cover - reported, not fatal
            log.exception("could not put %s on the to-do list", notification_id)

    def _bubble_bad(self, notification_id: str) -> None:
        """Close this bubble, keep it as a bad notification, show the next."""
        self._answered_now()
        self._close_bubble()
        threading.Thread(target=self._then_waiting(self._mark_bad, read=True), args=(notification_id,),
                         name="bubble-bad", daemon=True).start()

    def _mark_bad(self, notification_id: str) -> None:
        try:
            self.app.bad_notification(notification_id)
        except Exception:                  # pragma: no cover - reported, not fatal
            log.exception("could not keep %s as a bad notification",
                          notification_id)

    def _answer_question(self, question_id: str, key: str,
                         notification_id: str = "") -> None:
        """READ FIRST, THEN ANSWERED (the user, 2026-11-10: after "Add to collection" "N
        recommendation" still took two seconds to go): the notification is read and the line beside
        the avatar drawn again at once; then the answer, which may keep a page in the collection."""
        try:
            if notification_id:
                self.app.notifications.mark_read(notification_id)
                self.on_main_thread(self._show_waiting)
            self.app.feedback.answer(question_id, key, notification_id=notification_id)
        except Exception:                  # pragma: no cover - reported, not fatal
            log.exception("could not record the answer to %s", question_id)

    def _zoom_keys(self, bubble: Any) -> None:
        """The keys reach what has the keyboard: the pointer on it, it has it - unless something
        in it already does, the box being written in."""
        from .on_top import keys_on_hover

        def zoomed(step: int) -> str:
            self._zoom_bubble(step)
            return "break"
        modifier = "Command" if sys.platform == "darwin" else "Control"
        keys_on_hover(bubble)
        for key, step in (("plus", 1), ("equal", 1), ("KP_Add", 1), ("minus", -1), ("KP_Subtract", -1)):
            bubble.bind(f"<{modifier}-{key}>", lambda _e, one=step: zoomed(one))
        bubble.bind(f"<{modifier}-MouseWheel>", lambda event: zoomed(1 if event.delta > 0 else -1))

    def _bubble_zoom(self) -> int:
        return sizes.popup_zoom(getattr(self, "config", None))

    def _zoom_bubble(self, step: int) -> None:
        zoom_popups(self, self._bubble_zoom() + step)

    def _redraw_bubble(self) -> None:
        """The notification shown drawn again at the new size."""
        shown = self._showing
        if shown is None or self._bubble is None:
            return
        self._bubble.destroy()
        self._bubble = None
        self._bubble_queue.appendleft(shown)
        self._show_next_bubble()

    def _close_bubble(self) -> None:
        if self._bubble is not None:
            try:
                self._bubble.destroy()
            except tk.TclError:            # pragma: no cover - already gone
                pass
            self._bubble = None
        self._showing = None
        # A beat between bubbles, so that the next one registers as a new thing
        # rather than as the first one having failed to close.
        self.root.after(300, self._show_next_bubble)

    # -- lifecycle ----------------------------------------------------------
    def on_main_thread(self, work: Any) -> None:
        """Run this on the thread that owns the windows.

        Tk may only be touched from the thread that created it. Anything
        the start-up thread wants to show has to come back here, and
        `after(0, ...)` is the queue for that.
        """
        try:
            self.root.after(0, work)
        except Exception:        # pragma: no cover - the window has gone
            log.debug("could not reach the window thread", exc_info=True)

    def say_hello(self) -> None:
        """A word when the agent is actually up.

        NOT WHEN THE WINDOW APPEARS - the window now appears first and the
        machinery follows, so a greeting posted with the window would be
        claiming something that is not true yet. This runs when the last
        of it has started, which makes it the one honest signal that the
        application is ready.
        """
        self._up = True
        # The browsers looked for now, off this thread: the first page
        # opened - a double click - then opens at once.
        self._off_this_thread(webbrowser.get, name="find-the-browser")
        # The first folder dialog is slow to appear, so one is opened unseen
        # (chooser.warm_up) - which holds this window's thread for a few
        # seconds (6 s measured, 2026-10-26). Not on Windows, where adding a
        # work directory has the system's own chooser, made ready by nothing
        # here; elsewhere, only once the user has left the computer alone.
        if sys.platform != "win32":
            self.root.after(30000, self._warm_up_when_idle)
        if not bool(self.config.get("greet_on_start", True)):
            return
        # What it says is counted from the states - off this thread, as the
        # hover readout is: nothing in it touches a window.
        threading.Thread(target=self._greet, name="greeting", daemon=True).start()

    def _greet(self) -> None:
        name = self.app.avatar_name()
        held = {}
        try:
            held = self.app.state_heartbeat.how_many_wait()
        except Exception:        # pragma: no cover - a readout, not the work
            held = {}
        waiting = int(held.get("waiting") or 0)
        tasks = int(held.get("tasks") or 0)
        # What it is going to do, where there is anything to say about it:
        # a greeting that also answers "what have you got" is worth
        # reading twice, and one that says only hello is worth reading
        # once.
        if tasks and waiting:
            said = (f"I am watching again. {tasks} task"
                    f"{'' if tasks == 1 else 's'} so far, and {waiting} "
                    f"state{'' if waiting == 1 else 's'} not in one yet.")
        elif tasks:
            said = (f"I am watching again. {tasks} task"
                    f"{'' if tasks == 1 else 's'}, all of them grouped.")
        else:
            said = "I am watching again. Nothing grouped yet."
        # NO GREETING AS A NOTIFICATION (the user, 2026-11-13: "其它都取消"): said on the avatar.
        self._hover_said = f"{name}\n{said}"

    def say_it_could_not_start(self) -> None:
        """And when it did not start, which matters more than a greeting.

        The window is already up by then, so without this it would sit
        there looking like a working application with nothing behind it.
        """
        self._up = True
        self._hover_said = (f"{self.app.avatar_name()}\n"
                            "Could not start - the log in internal/logs says why")
        try:
            self.app.notifications.system(
                self.app.avatar_name(),
                "I could not start. The log in internal/logs has the "
                "reason; nothing has been recorded in the meantime.",
                kind="error", dedupe_key="start-failed", startup=True)
        except Exception:        # pragma: no cover
            log.debug("could not report the failure", exc_info=True)

    def run(self) -> None:
        self._start_panel_host()
        try:
            self._stalls.start(self.root)       # the window's thread standing still: said (stall_watch.py)
        except Exception:        # pragma: no cover - watched or not, the window runs
            log.debug("the window's thread could not be watched", exc_info=True)
        # A strip of the screen the side panel took and the application ended without giving back: given
        # back now (dock.py) - the setting off or not.
        if sys.platform == "win32":
            from ..platform_layer.dock import Dock
            self._dock = Dock(self.app.paths.data_dir / "side_panel_dock.json")
            threading.Thread(target=self._dock.give_back_left_over, name="side-panel-room-back",
                             daemon=True).start()
            # The picture of the screen taken beside the pinned panel's strip, not under it (windows.py).
            from ..platform_layer import windows as windows_layer
            windows_layer.SIDE_STRIP.append(
                lambda dock=self._dock: dock.strip if dock.docked and not dock.stepped_aside else None)
            self.root.after(self.SIDE_TAB_AT_START_MS, lambda: DesktopAgent._side_tab_at_start(self))
            if not os.environ.get("PA_NO_DIALOGS"):
                self.root.after(self.SIDE_TAB_AT_START_MS + 500, lambda: DesktopAgent._side_edge_follow(self))
        # NOT ON THE SCREEN BY DEFAULT (the user, 2026-11-17: "已经不需要logo在屏幕上了"): the side panel's tab, its
        # shortcuts and the tray reach everything; the watermelon walks in the Play view. Shown again from the tray's
        # menu - and kept so.
        if not self.config.get("avatar_on_screen", False):
            self.root.after(0, lambda: DesktopAgent.toggle_character(self, remember=False) if not self.character_hidden else None)
        self.root.mainloop()

    #: How long after the start the side panel's tab is put at the screen's edge.
    SIDE_TAB_AT_START_MS = 1500

    def _side_tab_at_start(self) -> None:
        """ITS TAB FROM THE START (the user, 2026-11-17: "agent启动之后就要有凸起的按钮让我可以expand, 而不是我第一次展开
        之后才有"): at the screen's edge, pressed to open the panel - unless it is open already."""
        if sys.platform != "win32" or os.environ.get("PA_NO_DIALOGS"):
            return
        try:
            if self._notes_open_now():
                return
            self._side_tab_on = True
            DesktopAgent._show_side_tab(self)
        except Exception:
            log.debug("the side panel's tab could not be shown at the start", exc_info=True)

    def _start_panel_host(self) -> None:
        """THE SIDE PANEL'S OWN PROCESS, WEBVIEW2 STARTED BEFORE IT IS WANTED (panel_host.py; the user,
        2026-11-14: "这个做") - on Windows, a while after the agent starts. Where it cannot be (no pywebview, no
        WebView2 runtime), nothing: the panel opens in the browser as before."""
        if sys.platform != "win32" or os.environ.get("PA_NO_DIALOGS"):
            return
        try:
            from ..platform_layer.panel_host import PanelHost
            self.panel_host = PanelHost(self.app.paths.data_dir / "notes_webview")
            self.panel_host.start_later(then=self._prepare_notes)
        except Exception:        # pragma: no cover - the panel opens as before
            log.info("the side panel's host could not be set up", exc_info=True)

    def _prepare_notes(self) -> None:
        """THE PANEL MADE BEFORE IT IS WANTED, HIDDEN: the first open only shows it (slide_in_titled finds one put
        away) - unless one is there already."""
        from ..platform_layer import app_window
        if app_window._windows_page(self._notes_titles(), hidden=True) is not None:
            return
        self.panel_host.open(self.app.sign_in_url(self.NOTES_PATH), self.NOTES_TITLE, DesktopAgent._notes_width(self),
                             app_window.NOTES_SIZE[1], app_window.OFF_SCREEN[0], app_window.OFF_SCREEN[1],
                             hidden=True)

    def quit(self) -> None:
        """Stop the agent. Quit is an explicit choice, so it is not queried.

        The user reached this from the menu item that says Quit; asking again
        adds a step without adding information, and nothing is lost - state is
        already persisted, and starting the agent again restores it.
        """
        # OFF THE SCREEN FIRST, THEN THE WORK. Stopping the agent takes a
        # second or two in the ordinary case - heartbeats finishing the
        # phase they are in, listeners being let go, the api closing - and
        # all of that used to happen with the window still up. Tk is
        # single-threaded, so the window was not merely visible, it was
        # frozen: the title bar stopped responding, the mouse became a
        # spinner, and quitting looked like hanging.
        #
        # Nothing about the work changes. What changes is that the window
        # is withdrawn and the screen repainted BEFORE it starts, so the
        # last thing the user sees is the application going away at the
        # moment they asked for it. The seconds are still spent; they are
        # just not spent in front of them.
        # EVERY WINDOW, FOUND RATHER THAN LISTED. Withdrawing the root does
        # not hide a Toplevel - each is a window in its own right - and a
        # list of them here goes stale the moment another is added, which
        # is exactly what happened: the hover readout stayed on screen
        # after the rest of the interface had gone, because it was a
        # window this did not know about. Asking Tk for its children
        # cannot fall behind in that way.
        #
        # The pending `after` jobs go too. One of them refreshes the hover
        # readout from the application, and leaving it queued means a
        # callback reaching into a stopped agent a second from now.
        for job in (getattr(self, "_hover_job", None), getattr(self, "_hover_wait", None)):
            if not job:
                continue
            try:
                self.root.after_cancel(job)
            except Exception:    # pragma: no cover - already fired
                pass
        windows = []
        try:
            # Anything that can be withdrawn is a window for this purpose.
            # Asking `isinstance(kid, tk.Toplevel)` would be narrower and
            # also untestable without a display, which is how the hover
            # readout came to be missed in the first place.
            windows = [kid for kid in self.root.winfo_children()
                       if hasattr(kid, "withdraw")]
        except Exception:        # pragma: no cover - already tearing down
            windows = [one for one in (getattr(self, "_hover", None),
                                       getattr(self, "_bubble", None),
                                       self.agent_window) if one is not None]
        for window in [*windows, self.root]:
            if window is None:
                continue
            try:
                window.withdraw()
            except Exception:    # pragma: no cover - already gone
                pass
        try:
            # Tk only repaints when asked, and this is the one repaint that
            # has to happen before the blocking call rather than after it.
            self.root.update_idletasks()
            self.root.update()
        except Exception:        # pragma: no cover - a repaint, not the work
            pass
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.stop()
        # THE OVERVIEW'S WINDOW GOES WITH IT (the user, 2026-11-09: "agent关闭之后，自动把overview panel
        # 也关了"): a page with no agent behind it only says it cannot reach it.
        # The side panel's own process FIRST, its windows with it: a WebView2 form does not close on WM_CLOSE
        # (measured 2026-11-14), and close_titled would wait two seconds on each.
        host = getattr(self, "panel_host", None)
        if host is not None:
            try:
                host.stop()
            except Exception:    # pragma: no cover
                log.debug("the side panel's host could not be stopped", exc_info=True)
        try:
            from ..platform_layer.app_window import close_the_page, close_titled
            close_the_page()
            close_titled(self._notes_titles())          # the side panel too - put away or not
        except Exception:        # pragma: no cover - the agent stops whatever
            log.debug("the page's window could not be closed", exc_info=True)
        # The side panel's strip of the screen given back, the windows moved for it put back.
        try:
            self._give_the_room_back()
        except Exception:        # pragma: no cover - the agent stops whatever
            log.debug("the side panel's room could not be given back", exc_info=True)
        # What the window handed over to be written (a draft, a tick) written before the database closes.
        stalls = getattr(self, "_stalls", None)
        if stalls is not None:
            stalls.stop()
        if not window_writes(self).flush(5.0):
            log.warning("what the window handed over was not all written before it closed")
        try:
            self.app.stop()
        finally:
            self.root.destroy()


def _sayer(app: Any) -> Any:
    """The application's own texts in `app`'s display language
    (common/interface_words.py)."""
    return lambda text: interface_words.shown(text, getattr(app, "config", None))


def _filler(app: Any) -> Any:
    """Words with numbered places, in `app`'s display language, filled in."""
    from ..common.languages import display_language
    code = display_language(getattr(app, "config", None))
    return lambda key, *values: interface_words.fill(key, code, *values)


def _as_written(key: str, *values: Any) -> str:
    """Words with numbered places, filled in English (the hover's words, when
    no display language is given: interface_words.fill gives one)."""
    return key.format(*values)


def _in_words(seconds: Optional[float], fill: Any = _as_written) -> str:
    """A duration from now, which is the question being asked (section 7.5)."""
    if seconds is None:
        return fill("unknown")
    seconds = max(0, int(seconds))
    if seconds < 60:
        return fill("in {0}s", seconds)
    if seconds < 3600:
        return fill("in {0}m", seconds // 60)
    return fill("in {0}h {1}m", seconds // 3600, f"{(seconds % 3600) // 60:02d}")


def _ago_in_words(seconds: Optional[float], fill: Any = _as_written) -> str:
    if seconds is None:
        return fill("not yet")
    seconds = max(0, int(seconds))
    if seconds < 60:
        return fill("{0}s ago", seconds)
    if seconds < 3600:
        return fill("{0}m ago", seconds // 60)
    return fill("{0}h ago", seconds // 3600)


def _tokens(n: int) -> str:
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.0f}k"
    return str(n)


def _work_lines(going: List[Dict[str, Any]], fill: Any = _as_written) -> List[str]:
    """Each work run going - the Experience debugger's too, which go on with
    their panels closed: its name (and way), where it is, for how long, and
    what it has spent. Nothing when none is going."""
    if not going:
        return []
    lines = [fill("Work going: {0}", len(going))]
    for one in going:
        name = one.get("name") or "work"
        if one.get("way"):
            name = f"{name} \u00b7 {one['way']}"
        where = []
        if one.get("round"):
            where.append(fill("round {0}", one["round"]))
        elif one.get("phase"):
            where.append(str(one["phase"]))
        status = str(one.get("status") or "")
        if status and status != "running":
            # Waiting for you: on what - the model's sign-in, its usage limit.
            where.append(f"{fill(status)}: {one['waiting_for']}" if one.get("waiting_for")
                         and status == "waiting for you" else fill(status))
        minutes = float(one.get("minutes") or 0)
        where.append(f"{int(minutes)}m" if minutes >= 1 else fill("just started"))
        where.append(fill("{0} in / {1} out", _tokens(one.get("tokens_in")),
                          _tokens(one.get("tokens_out"))))
        lines.append(f"  \u2022 {name} \u2014 {', '.join(where)}")
    return lines


def _schedule_line(label: str, schedule: Dict[str, Any], fill: Any = _as_written) -> str:
    """A component that is off says so, rather than showing an empty countdown."""
    if not schedule.get("enabled", True):
        return fill("{0}: off", label)
    if not schedule.get("running", False):
        return fill("{0}: not running", label)
    return fill("{0}: {1}, next {2}", label,
                _ago_in_words(schedule.get("last_run_seconds_ago"), fill),
                _in_words(schedule.get("next_run_in_seconds"), fill))
