"""The side panel docked: the other windows make room for it (Windows).

THE OTHER WINDOWS MOVE OVER, AT ONCE (the user, 2026-11-16: "你右边弹出的时候，整个屏幕的其它控件能自动左缩放（有点像
overleaf的控件...) 这个缩放要快"; of the windows the user placed themselves: "只有重叠才缩"). Two ways:

* the strip down the right of the screen is taken from its work area, as the taskbar is (an application
  desktop toolbar, SHAppBarMessage): maximized and snapped windows are fitted to what is left by Windows
  itself;
* a window placed by hand that the panel would cover is made narrower, or moved left, to stand beside it -
  and put back as it was when the panel goes, unless it was moved in between (then it is the user's).

In one step, not slid: fitted again at every frame of a slide, every other program would draw itself twenty
times. Others are moved without waiting for them (SWP_ASYNCWINDOWPOS), so a program that does not answer
holds nothing up. A window full screen - a film, a game - is not pushed: it covers the panel till it is over.

Where the strip was taken is written down (`state_file`) and given back at the next start, should the
application end without giving it back itself.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

Rect = Tuple[int, int, int, int]                   # left, top, right, bottom

#: Narrower than this, a window is moved left instead of made narrower; and it is never made narrower than it.
NARROWEST = 360
#: The callback message the shell sends the panel's window (it is not read: the panel does not move).
CALLBACK = 0x8000 + 0x51                           # WM_APP + 0x51

ABM_NEW, ABM_REMOVE, ABM_QUERYPOS, ABM_SETPOS = 0, 1, 2, 3
ABE_RIGHT = 2
SWP_NOZORDER, SWP_NOACTIVATE, SWP_ASYNCWINDOWPOS = 0x0004, 0x0010, 0x4000


# -- what moves where: plain arithmetic, the same on every system ----------------------------------------------
def overlaps(window: Rect, strip: Rect) -> bool:
    left, top, right, bottom = window
    return left < strip[2] and right > strip[0] and top < strip[3] and bottom > strip[1]


def room_for(window: Rect, strip: Rect, screen: Rect, narrowest: int = NARROWEST) -> Optional[Rect]:
    """Where a window the panel's `strip` would cover goes, on a `screen` (its work area): as it is, when it
    does not overlap; else its right edge at the strip's left - narrower - or, too narrow then, moved left
    as wide as it was (as far as the screen lets it). None: left as it is."""
    if not overlaps(window, strip):
        return None
    left, top, right, bottom = window
    width = right - left
    edge = strip[0]
    if edge - left >= narrowest:                          # narrower, where it is
        return (left, top, edge, bottom)
    wanted = min(width, edge - screen[0])                 # moved left, as wide as it can stay
    if wanted < narrowest and wanted < width:
        wanted = max(edge - screen[0], 0)
    if wanted <= 0:
        return None
    return (edge - wanted, top, edge, bottom)


def full_screen(window: Rect, monitor: Rect) -> bool:
    """A window as large as its whole screen, the taskbar's place too: a film, a game, a presentation."""
    return window[0] <= monitor[0] and window[1] <= monitor[1] and window[2] >= monitor[2] \
        and window[3] >= monitor[3]


def put_back(moved: Dict[int, Tuple[Rect, Rect]], now: Dict[int, Rect]) -> Dict[int, Rect]:
    """The windows to put back as they were: each that is still where it was put (`moved`: handle ->
    (as it was, where it was put); `now`: where each is) - one moved since is the user's, and stays."""
    return {handle: was for handle, (was, put) in moved.items() if now.get(handle) == put}


#: How often a pinned panel looks whether a window in front fills its screen.
FULL_SCREEN_LOOK = 1.0


# -- Windows -----------------------------------------------------------------------------------------------
class Dock:
    """The strip taken for the panel and given back; the windows moved for it and put back."""

    def __init__(self, state_file: Optional[Path] = None) -> None:
        self.state_file = Path(state_file) if state_file else None
        self._lock = threading.Lock()
        self.handle: Optional[int] = None           # the panel's window, while the strip is its
        self.strip: Optional[Rect] = None
        self.moved: Dict[int, Tuple[Rect, Rect]] = {}
        self._watch_stop: Optional[threading.Event] = None
        #: OUT OF THE WAY OF A FILM: hidden by `_watch` while a window fills its screen - not put away.
        self.stepped_aside = False
        #: PINNED AND OPENED BY THE USER OVER A FILM (the user, 2026-11-16: "全屏会放在sidebar的下面（没有缩放）尽管sidebar是pin
        #: 的"): the strip taken all the same, and the film made room for as every window is - put back whole after.
        self.over_film = False
        #: THE FILM THE USER OPENED IT OVER: above that one, not behind it, till it is over (`_watch`).
        self.kept_over = 0

    @staticmethod
    def usable() -> bool:
        return sys.platform == "win32"

    # .. given back after the application ended without it ..................................................
    def give_back_left_over(self) -> None:
        """At the start: a strip written down and not given back - the application ended first - given back,
        and the windows moved for it put back where they still stand where they were put."""
        if not self.usable() or self.state_file is None:
            return
        with self._lock:                               # not while this one takes a strip of its own
            if self.handle is not None or not self.state_file.exists():
                return
            try:
                said = json.loads(self.state_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                said = {}
            try:
                if said.get("handle"):
                    self._remove(int(said["handle"]))
                moved = {int(k): (tuple(v[0]), tuple(v[1])) for k, v in (said.get("moved") or {}).items()}
                self._put_back(moved)
                log.info("the side panel's strip of the screen, left taken, given back")
            except Exception:
                log.info("the side panel's strip left taken could not be given back", exc_info=True)
            finally:
                self._forget()

    # .. the strip ..........................................................................................
    def dock(self, handle: int, width: int) -> Optional[Rect]:
        """The strip `width` pixels wide down the right of the screen the pointer is on taken for the window
        `handle`, and the windows it would cover moved; where the panel goes (left, top, right, bottom) - or
        None, nothing taken: a window full screen there, or the system said no."""
        if not self.usable():
            return None
        with self._lock:
            try:
                return self._dock(handle, width)
            except Exception:
                log.warning("the side panel could not take its strip of the screen", exc_info=True)
                return None

    def undock(self) -> None:
        """The strip given back, the windows moved for it put back - at once."""
        if not self.usable():
            return
        with self._lock:
            handle, moved = self.handle, dict(self.moved)
            self.handle, self.strip, self.moved = None, None, {}
            if self._watch_stop is not None:
                self._watch_stop.set()
                self._watch_stop = None
            try:
                if handle:
                    _on_top(handle, False)
                    self._remove(handle)
                self._put_back(moved)
            except Exception:
                log.warning("the side panel's strip could not be given back", exc_info=True)
            finally:
                self._forget()

    @property
    def docked(self) -> bool:
        return self.handle is not None

    def _dock(self, handle: int, width: int) -> Optional[Rect]:
        import ctypes
        from .app_window import _per_monitor
        _per_monitor()                                   # in pixels, as the panel is placed
        monitor, work = _pointer_screen()
        film = 0
        if self.over_film:
            import os
            from . import windows
            film = _front_of_others({os.getpid(), *windows.OTHER_OWN_PROCESSES})
            if film and not full_screen(_rect_of(film), monitor):
                film = 0
        if not film and _something_full_screen(monitor, but=handle):
            # ITS STRIP HELD ALREADY: put back into it, never beside it (the user, 2026-11-16: "sidebar没有出现在最右边，
            # 而是在偏左的位置" - refused, it was placed by the work area the strip had narrowed). A film keeps
            # only a strip from being taken anew.
            if self.handle == handle and self.strip:
                return self.strip
            return None
        data = _bar(handle)
        shell32 = ctypes.windll.shell32
        if self.handle != handle:
            if self.handle:
                self._remove(self.handle)
            if not shell32.SHAppBarMessage(ABM_NEW, ctypes.byref(data)):
                log.info("the side panel's strip: the system did not take the panel as a bar")
                return None
            log.info("the side panel took its strip of the screen")
            self.handle = handle
        data.uEdge = ABE_RIGHT
        data.rc.left, data.rc.top = monitor[2] - width, monitor[1]
        data.rc.right, data.rc.bottom = monitor[2], monitor[3]
        shell32.SHAppBarMessage(ABM_QUERYPOS, ctypes.byref(data))   # the taskbar on the right: beside it
        data.rc.left = data.rc.right - width
        shell32.SHAppBarMessage(ABM_SETPOS, ctypes.byref(data))
        strip = (data.rc.left, max(data.rc.top, work[1]), data.rc.right, min(data.rc.bottom, work[3]))
        self.strip = strip
        self.width, self.monitor = width, monitor            # its screen as it was: a change of it seen (_watch)
        # ABOVE THE OTHERS WHILE PINNED, AS THE TASKBAR IS (the user, 2026-11-16: "windows上显示桌面，即使右边pin了也会被
        # 最小化，不应该这样"): Show desktop leaves the windows always on top - the strip is its own, nothing is
        # under it to be covered.
        _on_top(handle, True)
        self._watch(handle, monitor)
        self._remember()
        self._make_room(handle, strip, (work[0], work[1], strip[0], work[3]))
        if film:
            self.kept_over = film
        if film and film not in self.moved:
            # The film beside the strip, as tall as the screen - full screen again when it is given back.
            rect = _rect_of(film)
            where = (monitor[0], monitor[1], strip[0], monitor[3])
            ctypes.windll.user32.SetWindowPos(film, 0, where[0], where[1], where[2] - where[0], where[3] - where[1],
                                              SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS)
            self.moved[film] = (rect, where)
            self._remember()
        return strip

    def _watch(self, handle: int, monitor: Rect) -> None:
        """A FULL SCREEN COVERS THE PINNED PANEL (the user, 2026-11-17: "pin的时候如果点了全屏，没有浏览器extension加持下，就
        自动覆盖Pin的内容，这样取消全屏后又能看到pin"): while it is pinned, a window in front as large as its screen has the
        panel behind it - its strip kept, nothing moved - and above the others again once it is not. A browser with
        the extension never fills the screen: its page's full screen is beside the panel (fullscreen.js). One film
        the user opened the panel over themselves (`kept_over`) has it stay above, till that film is over."""
        if self._watch_stop is not None:
            return
        stop = self._watch_stop = threading.Event()

        def watch() -> None:
            # IN PIXELS, AS THE SCREEN IT WAS GIVEN IS (the user, 2026-11-17: "全屏不能全屏了，sidebar还是挡住" - this thread,
            # not told, saw every window by the scaled screen, 1463 across for 2560: a film never filled it).
            from .app_window import _per_monitor
            _per_monitor()
            behind = 0
            while not stop.wait(FULL_SCREEN_LOOK):
                # ANOTHER SCREEN, OR THIS ONE OTHERWISE (the user, 2026-11-19: "如果换显示屏之后，他的sidebar的位置会变" -
                # a small screen, then the large one again: its strip kept the small one's width, the panel beside it,
                # neither at the edge): its strip taken again at the right of the screen it is on now.
                try:
                    now = _window_screen(handle)[0]
                    if now != getattr(self, "monitor", now) and self.handle == handle:
                        self._again(handle)
                except Exception:
                    log.debug("the side panel's screen could not be looked at", exc_info=True)
                screen = getattr(self, "monitor", None) or monitor     # the screen as it is now (_again)
                try:
                    film = _film_front(screen, but=handle)
                except Exception:
                    continue
                if stop.is_set():
                    break
                if film and film == self.kept_over:
                    film = 0                          # the user wants it seen over this one
                elif not film:
                    self.kept_over = 0
                if film and film != behind:
                    _on_top(handle, False)
                    _behind(handle, film)
                    behind = film
                    self.stepped_aside = True
                    log.info("a window full screen covers the pinned side panel till it is over")
                elif not film and behind:
                    _on_top(handle, True)
                    behind = 0
                    self.stepped_aside = False
            if behind and self.handle is None:
                self.stepped_aside = False
        threading.Thread(target=watch, name="side-panel-full-screen", daemon=True).start()

    def _again(self, handle: int) -> None:
        """ITS STRIP TAKEN AGAIN on the screen the panel is on now (its size or place changed): at its right edge, as
        wide as before, the panel put into it and the windows it would cover moved again."""
        import ctypes
        with self._lock:
            if self.handle != handle:
                return
            monitor, work = _window_screen(handle)
            width = int(getattr(self, "width", 0) or (self.strip[2] - self.strip[0] if self.strip else 0))
            if width <= 0:
                return
            data = _bar(handle)
            data.uEdge = ABE_RIGHT
            data.rc.left, data.rc.top = monitor[2] - width, monitor[1]
            data.rc.right, data.rc.bottom = monitor[2], monitor[3]
            shell32 = ctypes.windll.shell32
            shell32.SHAppBarMessage(ABM_QUERYPOS, ctypes.byref(data))
            data.rc.left = data.rc.right - width
            shell32.SHAppBarMessage(ABM_SETPOS, ctypes.byref(data))
            monitor, work = _window_screen(handle)           # its work area as the strip now leaves it
            strip = (data.rc.left, max(data.rc.top, work[1]), data.rc.right, min(data.rc.bottom, work[3]))
            self.strip, self.monitor = strip, monitor
            ctypes.windll.user32.SetWindowPos(handle, 0, strip[0], strip[1], strip[2] - strip[0], strip[3] - strip[1],
                                              SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS)
            self._remember()
            self._make_room(handle, strip, (work[0], work[1], strip[0], work[3]))
            log.info("the side panel took its strip again: its screen changed (%s)", monitor)

    def _make_room(self, panel: int, strip: Rect, left_over: Rect) -> None:
        """The windows a hand placed that the strip covers, beside it - each without waiting for it."""
        import ctypes
        user32 = ctypes.windll.user32
        for window, rect in _placed_windows(but=panel):
            if window in self.moved:
                continue
            # By what is seen of it, not its invisible border (some 7 px round a window on Windows 10 and
            # 11): one beside the strip already is not moved; one moved lands with what is seen at its edge.
            seen = _seen_rect(window) or rect
            to = room_for(seen, strip, left_over)
            if to is None or to == seen:
                continue
            where = (to[0] - (seen[0] - rect[0]), to[1] - (seen[1] - rect[1]),
                     to[2] + (rect[2] - seen[2]), to[3] + (rect[3] - seen[3]))
            user32.SetWindowPos(window, 0, where[0], where[1], where[2] - where[0], where[3] - where[1],
                                SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS)
            self.moved[window] = (rect, where)
        self._remember()

    def _put_back(self, moved: Dict[int, Tuple[Rect, Rect]]) -> None:
        import ctypes
        from .app_window import _per_monitor
        _per_monitor()
        user32 = ctypes.windll.user32
        now = {window: _rect_of(window) for window in moved if user32.IsWindow(window)}
        for window, was in put_back(moved, now).items():
            user32.SetWindowPos(window, 0, was[0], was[1], was[2] - was[0], was[3] - was[1],
                                SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS)

    @staticmethod
    def _remove(handle: int) -> None:
        import ctypes
        data = _bar(handle)
        ctypes.windll.shell32.SHAppBarMessage(ABM_REMOVE, ctypes.byref(data))

    def _remember(self) -> None:
        if self.state_file is None:
            return
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps({
                "handle": self.handle, "strip": self.strip,
                "moved": {str(k): [list(v[0]), list(v[1])] for k, v in self.moved.items()}}), encoding="utf-8")
        except OSError:
            log.debug("where the side panel's strip is could not be written down", exc_info=True)

    def _forget(self) -> None:
        if self.state_file is not None:
            try:
                self.state_file.unlink()
            except OSError:
                pass


def _a_film(handle: int) -> bool:
    """NOT A SCREENSHOT'S VEIL (the user, 2026-11-16: "我刚刚用微信截屏之后，你短时间左边的sidebar消失了"): a tool taking a
    picture of the screen lays a window over all of it, above every other and seen through - a film, a game or a slide
    show is neither."""
    import ctypes
    ex = ctypes.windll.user32.GetWindowLongW(handle, -20)
    return not ex & 0x00000008 and not ex & 0x00080000          # WS_EX_TOPMOST, WS_EX_LAYERED


def _shown(handle: int, on: bool) -> None:
    """The window shown again (not made the one typed into) or hidden - not waited for."""
    import ctypes
    try:
        ctypes.windll.user32.ShowWindowAsync(ctypes.c_void_p(handle), 4 if on else 0)   # SW_SHOWNOACTIVATE / SW_HIDE
    except Exception:
        log.debug("the side panel could not be %s", "shown" if on else "hidden", exc_info=True)


def _front_of_others(skip: set) -> int:
    """The topmost window shown of another program's - this application's own (`skip`, by process) passed over: what
    is in front of the user behind them."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found: List[int] = []
    call = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def look(handle: int, _extra: int) -> bool:
        if not user32.IsWindowVisible(handle) or user32.IsIconic(handle) or _cloaked(handle):
            return True
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if owner.value in skip or user32.GetWindowLongW(handle, -20) & 0x80:       # WS_EX_TOOLWINDOW
            return True
        rect = _rect_of(handle)
        if rect[2] - rect[0] < 50 or rect[3] - rect[1] < 50:
            return True
        found.append(handle)
        return False                                   # the first, from the top
    user32.EnumWindows(call(look), 0)
    return found[0] if found else 0


def full_screen_now(but: int = 0, skip: Optional[set] = None) -> bool:
    """Whether the window in front fills the whole of its screen - a film, a game, a slide show. `skip`: processes
    whose windows are passed over (this application's own: the side panel pressed on is not the end of the film)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        front = _front_of_others(skip) if skip else user32.GetForegroundWindow()
        if not front or front == but or front == user32.GetShellWindow():
            return False
        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(front, name, 64)
        if name.value in ("WorkerW", "Progman", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return False

        class _Info(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD)]
        info = _Info()
        info.cbSize = ctypes.sizeof(_Info)
        if not user32.GetMonitorInfoW(user32.MonitorFromWindow(front, 2), ctypes.byref(info)):
            return False
        box = info.rcMonitor
        return _a_film(front) and full_screen(_rect_of(front), (box.left, box.top, box.right, box.bottom))
    except Exception:
        return False


def _behind(handle: int, front: int) -> None:
    """The window put just behind `front` - not moved, not sized, not made the one typed into."""
    import ctypes
    try:
        ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(handle), ctypes.c_void_p(front), 0, 0, 0, 0,
                                          0x0001 | 0x0002 | 0x0010)
    except Exception:
        log.debug("the side panel could not be put behind the window in front", exc_info=True)


def _on_top(handle: int, on: bool) -> None:
    """The window above every other (HWND_TOPMOST) or among them again (HWND_NOTOPMOST) - not moved, not sized,
    not made the one typed into."""
    import ctypes
    try:
        # HWND_TOPMOST (-1) / HWND_NOTOPMOST (-2) as handles, all 64 bits of them: passed as plain numbers they went
        # as 32, which Windows took for some other window - and it was never above the others.
        ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(handle), ctypes.c_void_p(-1 if on else -2), 0, 0, 0, 0,
                                          0x0001 | 0x0002 | 0x0010)
    except Exception:
        log.debug("the side panel could not be put %s the others", "above" if on else "among", exc_info=True)


def _bar(handle: int) -> Any:
    import ctypes
    from ctypes import wintypes

    class APPBARDATA(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uCallbackMessage", wintypes.UINT),
                    ("uEdge", wintypes.UINT), ("rc", wintypes.RECT), ("lParam", wintypes.LPARAM)]
    ctypes.windll.shell32.SHAppBarMessage.restype = ctypes.c_size_t
    ctypes.windll.shell32.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.c_void_p]
    data = APPBARDATA()
    data.cbSize = ctypes.sizeof(APPBARDATA)
    data.hWnd = handle
    data.uCallbackMessage = CALLBACK
    return data


def _window_screen(handle: int) -> Tuple[Rect, Rect]:
    """(the whole screen, its work area) of the screen the window is on (the nearest), in pixels."""
    import ctypes
    from ctypes import wintypes

    class _MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
    user32 = ctypes.windll.user32
    user32.MonitorFromWindow.restype = ctypes.c_void_p
    user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    monitor = user32.MonitorFromWindow(handle, 2)                 # MONITOR_DEFAULTTONEAREST
    info = _MonitorInfo()
    info.cbSize = ctypes.sizeof(_MonitorInfo)
    user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    whole, work = info.rcMonitor, info.rcWork
    return (whole.left, whole.top, whole.right, whole.bottom), (work.left, work.top, work.right, work.bottom)


def _pointer_screen() -> Tuple[Rect, Rect]:
    """(the whole screen, its work area) of the screen the pointer is on, in pixels."""
    import ctypes
    from ctypes import wintypes

    class _MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
    user32 = ctypes.windll.user32
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    user32.MonitorFromPoint.restype = ctypes.c_void_p
    monitor = user32.MonitorFromPoint(point, 2)                   # MONITOR_DEFAULTTONEAREST
    info = _MonitorInfo()
    info.cbSize = ctypes.sizeof(_MonitorInfo)
    user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    whole, work = info.rcMonitor, info.rcWork
    return (whole.left, whole.top, whole.right, whole.bottom), (work.left, work.top, work.right, work.bottom)


def _rect_of(handle: int) -> Rect:
    import ctypes
    from ctypes import wintypes
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(handle, ctypes.byref(rect))
    return (rect.left, rect.top, rect.right, rect.bottom)


def _seen_rect(handle: int) -> Optional[Rect]:
    """What is seen of a window - without the invisible border the compositor draws round it."""
    import ctypes
    from ctypes import wintypes
    rect = wintypes.RECT()
    try:
        if ctypes.windll.dwmapi.DwmGetWindowAttribute(handle, 9, ctypes.byref(rect), ctypes.sizeof(rect)) == 0:
            return (rect.left, rect.top, rect.right, rect.bottom)   # DWMWA_EXTENDED_FRAME_BOUNDS
    except Exception:                                              # pragma: no cover - no compositor
        pass
    return None


def _cloaked(handle: int) -> bool:
    """A window Windows keeps out of sight (another desktop's, a store app suspended) though "visible"."""
    import ctypes
    from ctypes import wintypes
    value = wintypes.DWORD(0)
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(handle, 14, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:                                              # pragma: no cover - no compositor
        return False
    return bool(value.value)


def _placed_windows(but: int) -> List[Tuple[int, Rect]]:
    """The windows on the screen a hand placed: shown, framed, not minimized or maximized (Windows fits
    those), not tool windows or the shell's own, not seen-through, not cloaked - with where each is."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found: List[Tuple[int, Rect]] = []
    shell = {user32.GetShellWindow(), user32.FindWindowW("Shell_TrayWnd", None)}
    call = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def look(handle: int, _extra: int) -> bool:
        if handle == but or handle in shell or not user32.IsWindowVisible(handle):
            return True
        if user32.IsIconic(handle) or user32.IsZoomed(handle) or user32.GetParent(handle):
            return True
        style, ex = user32.GetWindowLongW(handle, -16), user32.GetWindowLongW(handle, -20)
        if not style & 0x00C00000 or ex & 0x00000080 or ex & 0x08000000:   # WS_CAPTION; TOOLWINDOW; NOACTIVATE
            return True
        if _cloaked(handle):
            return True
        found.append((handle, _rect_of(handle)))
        return True
    user32.EnumWindows(call(look), 0)
    return found


def _film_front(monitor: Rect, but: int) -> int:
    """The window in front on that screen when it is as large as all of it (a film, a game) - else 0."""
    import ctypes
    user32 = ctypes.windll.user32
    front = user32.GetForegroundWindow()
    if not front or front == but or front in (user32.GetShellWindow(),):
        return 0
    # THE DESKTOP IS NOT A FILM: pressed on, the window behind its icons is in front, as large as the screen.
    name = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(front, name, 64)
    if name.value in ("WorkerW", "Progman", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        return 0
    return front if _a_film(front) and full_screen(_rect_of(front), monitor) else 0


def _something_full_screen(monitor: Rect, but: int) -> bool:
    """Whether the window in front on that screen is as large as all of it (a film, a game)."""
    return bool(_film_front(monitor, but))


__all__: Sequence[str] = ("Dock", "room_for", "overlaps", "full_screen", "put_back", "NARROWEST")
