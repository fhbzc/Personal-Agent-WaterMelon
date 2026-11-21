"""A page of the app in a WINDOW OF ITS OWN - no tabs, no address bar.

Delegate work, started from Explorer, the selection button or the avatar,
used to open the whole page in the user's browser, as a tab among their
others, with the box over it. What was wanted was the box: so it opens as a
window of its own, the size of the box, which closes when the box does.

It is the same page - drawn once, tested once - in a browser's "app" mode
(`--app=URL`): Chrome where it is there, else Edge, which every Windows has, or
Chromium; where none is found, the user's own browser opens it as before.

A PROFILE OF ITS OWN (`profile`, in the application's internal folder):
none of the user's sign-ins, cookies or extensions are in it, and nothing
of it reaches theirs. EDGE WOULD SIGN IT IN BY ITSELF with the account the
computer is signed in to, sync off or not (2026-11-05, measured: one account
without the flag below, none with it), so Chrome comes first and Edge is told not to. It also makes the window a new browser process, the
only way the asked-for size and place are honoured when the user's browser
is already open.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

log = logging.getLogger(__name__)

#: The window's own profile, in the application's internal folder.
PROFILE = "box_window"

#: The box's window: wide enough for the plan beside its labels, tall
#: enough for the plan and the records found under it.
SIZE = (780, 900)


def _windows_candidates() -> List[Path]:
    bases = [base for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                               os.environ.get("LOCALAPPDATA")) if base]
    return [*(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe" for base in bases),
            *(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe" for base in bases)]


def _mac_candidates() -> List[Path]:
    apps = [Path("/Applications"), Path.home() / "Applications"]
    names = [("Google Chrome.app", "Google Chrome"), ("Microsoft Edge.app", "Microsoft Edge"),
             ("Chromium.app", "Chromium")]
    return [base / app / "Contents" / "MacOS" / exe for base in apps for app, exe in names]


def browser() -> Optional[str]:
    """A browser that opens a page as an app window, if there is one."""
    if sys.platform == "win32":
        candidates = _windows_candidates()
    elif sys.platform == "darwin":
        candidates = _mac_candidates()
    else:
        candidates = [Path(found) for found in (
            shutil.which(name) for name in ("google-chrome", "google-chrome-stable",
                                            "chromium", "chromium-browser",
                                            "microsoft-edge")) if found]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def command(program: str, url: str, profile: Path,
            size: Tuple[int, int] = SIZE, position: Optional[Tuple[int, int]] = None) -> List[str]:
    """How the window is asked for. Its size here is a first guess: the page
    sizes the window to the screen and puts it in the middle itself (app.js,
    DELEGATE_SOLO), because only the page measures the screen in the units
    the window is measured in - at 175% scaling, a 1920-pixel screen is 1097
    of them, and a place worked out here would be half off it."""
    width, height = size
    return [program, f"--app={url}", f"--user-data-dir={profile}",
            f"--window-size={width},{height}",
            *([f"--window-position={position[0]},{position[1]}"] if position else []),
            "--no-first-run", "--no-default-browser-check", "--disable-sync",
            "--disable-features=msImplicitSignin"]


def open_window(url: str, profile: Path, size: Tuple[int, int] = SIZE) -> bool:
    """`url` opened in a window of its own; False when no browser here can,
    for the caller to open it as it otherwise would."""
    program = browser()
    if program is None:
        return False
    try:
        Path(profile).mkdir(parents=True, exist_ok=True)
        flags = 0
        if sys.platform == "win32":
            flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                     | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        subprocess.Popen(command(program, url, Path(profile), size),
                         close_fds=True, creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return True
    except Exception:
        log.warning("could not open %s in a window of its own", program, exc_info=True)
        return False


#: THE WHOLE PAGE IN A WINDOW OF ITS OWN (the user, 2026-11-05): the program cannot reach a tab of
#: the user's browser, so each double click was a tab more. This one it can find, and bring back
#: as it was left. Its profile is its own too; its title is the page's, alone, which is how it is
#: told from a tab of the same page ("WaterMelon - Google Chrome").
PAGE_PROFILE = "page_window"
PAGE_SIZE = (1400, 900)
#: On a Mac this is Apple's WebKit instead (ui/mac_panel.py).
PAGE_TITLE = "WaterMelon"
#: The window class of Edge and of Chrome.
BROWSER_WINDOW = "Chrome_WidgetWin_1"
#: THE SIDE PANEL IN WEBVIEW2 (panel_host.py, 2026-11-14): its window a .NET form's, its class named so.
HOSTED_WINDOW = "WindowsForms10.Window"


def _ours(window_class: str) -> bool:
    """A window of the kinds the agent's own titled windows are: the browser's, or the panel host's."""
    return window_class == BROWSER_WINDOW or window_class.startswith(HOSTED_WINDOW)
#: ONE WINDOW, HOWEVER MANY DOUBLE CLICKS (the user, 2026-11-06: two in a row made two): a browser
#: takes seconds to start and the window is known by its title only once the page has loaded, so a
#: second double click found nothing and opened another. Held while one is coming; asked again
#: meanwhile, nothing more is done. How long it is waited for, where it can be seen and where not.
OPENING = threading.Lock()
OPENING_SECONDS, UNSEEN_SECONDS = 15.0, 3.0


def _no_windows_here() -> bool:
    """Under the tests nothing is opened on the screen of whoever runs them."""
    return bool(os.environ.get("PA_NO_DIALOGS"))


def is_the_page(window_class: str, title: str) -> bool:
    return window_class == BROWSER_WINDOW and title == PAGE_TITLE


#: THE WAITING NOTIFICATIONS DOWN THE SIDE OF THE SCREEN (the user, 2026-11-13): a window of its own.
NOTES_PROFILE = "notes_window"
NOTES_SIZE = (460, 900)


def _windows_page(titles: Optional[set] = None, hidden: bool = False) -> Optional[int]:
    """The handle of the page's window - or of one of the agent's own titled `titles` - if open (and
    shown, unless `hidden`: one put away by `slide_out_titled` too)."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found: List[int] = []
    call = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def look(handle: int, _extra: int) -> bool:
        if hidden or user32.IsWindowVisible(handle):
            kind, title = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(handle, kind, 64)
            user32.GetWindowTextW(handle, title, 64)
            if (_ours(kind.value) and title.value in titles) if titles \
                    else is_the_page(kind.value, title.value):
                found.append(handle)
                return False
        return True
    user32.EnumWindows(call(look), 0)
    return found[0] if found else None


def bring_back_the_page() -> bool:
    """The page's window to the front, as it was left; False when it is not open."""
    if _no_windows_here() or sys.platform != "win32":
        return False
    try:
        import ctypes
        handle = _windows_page()
        if handle is None:
            return False
        user32 = ctypes.windll.user32
        if user32.IsIconic(handle):
            user32.ShowWindow(handle, 9)         # SW_RESTORE
        user32.SetForegroundWindow(handle)
        return True
    except Exception:
        log.debug("the page's window could not be brought back", exc_info=True)
    return False


def bring_back_titled(titles: set) -> bool:
    """One of the agent's own windows, titled one of `titles`, to the front; False when none is open."""
    if _no_windows_here() or sys.platform != "win32":
        return False
    try:
        import ctypes
        handle = _windows_page(set(titles))
        if handle is None:
            return False
        user32 = ctypes.windll.user32
        if user32.IsIconic(handle):
            user32.ShowWindow(handle, 9)         # SW_RESTORE
        user32.SetForegroundWindow(handle)
        return True
    except Exception:
        log.debug("the window could not be brought back", exc_info=True)
    return False


#: SLID IN FROM THE SIDE (the user, 2026-11-13: "现在打开好像太慢了，有没有更natural的方式从侧边
#: 弹出"): a window put away is kept, hidden, and slid back in - no browser started again. So long, BY
#: THE CLOCK (2026-11-16: ten steps of a sleep each took 250 ms, not 160 - a sleep here is never less than
#: the system's 15.6 ms tick; the user: "弹出，关闭，显示都要快"), a step about this often.
SLIDE_SECONDS, SLIDE_STEP_SECONDS = 0.12, 0.008
#: PUT AWAY AT ONCE, not slid out (the user, 2026-11-16: closing must be quick - sliding out was 235 ms).
SLIDE_OUT = False


def _work_area(handle: int) -> Tuple[int, int, int, int]:
    """(left, top, right, bottom) of the work area of the screen `handle` is on."""
    import ctypes
    from ctypes import wintypes

    class _MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
    user32 = ctypes.windll.user32
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    monitor = user32.MonitorFromWindow(handle, 2)          # MONITOR_DEFAULTTONEAREST
    info = _MonitorInfo()
    info.cbSize = ctypes.sizeof(_MonitorInfo)
    user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    work = info.rcWork
    return work.left, work.top, work.right, work.bottom


def eased(elapsed: float, seconds: float = SLIDE_SECONDS) -> float:
    """How far along a slide is, 0 to 1, `elapsed` seconds into it: quick, then easing into place."""
    if seconds <= 0 or elapsed >= seconds:
        return 1.0
    return 1 - (1 - max(0.0, elapsed) / seconds) ** 3


def _slide(handle: int, start: int, end: int, top: int, width: int, height: int,
           seconds: float = SLIDE_SECONDS) -> None:
    """From `start` to `end` in `seconds` by the clock, the last step in place."""
    import ctypes
    user32 = ctypes.windll.user32
    flags = 0x0004 | 0x0010                                # SWP_NOZORDER | SWP_NOACTIVATE
    winmm = getattr(ctypes.windll, "winmm", None)
    fine = False
    try:
        fine = winmm is not None and winmm.timeBeginPeriod(1) == 0   # a sleep of 1 ms is 1 ms, for now
    except Exception:                                      # pragma: no cover - the coarse clock, then
        fine = False
    try:
        began = time.perf_counter()
        while True:
            done = eased(time.perf_counter() - began, seconds)
            user32.SetWindowPos(handle, 0, int(round(start + (end - start) * done)), top, width, height, flags)
            if done >= 1.0:
                return
            time.sleep(SLIDE_STEP_SECONDS)
    finally:
        if fine:
            winmm.timeEndPeriod(1)


def _per_monitor() -> None:
    """This thread in pixels, whatever the screen's scaling - what is measured and set agrees."""
    try:
        import ctypes
        ctypes.windll.user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
    except Exception:                                    # pragma: no cover - an older Windows
        pass


def _close_others(titles: set, keep: int) -> None:
    """The agent's other windows of these titles - one left hidden from before, say - asked to close."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    others: List[int] = []
    call = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def look(handle: int, _extra: int) -> bool:
        kind, title = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(handle, kind, 64)
        user32.GetWindowTextW(handle, title, 64)
        if _ours(kind.value) and title.value in titles and handle != keep:
            others.append(handle)
        return True
    user32.EnumWindows(call(look), 0)
    for handle in others:
        user32.PostMessageW(handle, 0x0010, 0, 0)                       # WM_CLOSE


#: HOW MUCH OF THE SIDE PANEL STAYS ON THE SCREEN PUT AWAY (the user, 2026-11-16: "collapse的时候这个凸起还是在边框外（这样我
#: 可以点击展开)" - "不是一个bar, 你就露出一个button expand就行"): ITS TAB ALONE, as wide and as tall as it, in the middle of
#: the screen's right edge - in the screen's own units at 96 dpi.
PEEK, PEEK_TALL = 24, 64          # the tab's own size (styles.css .side-collapse-tab): the tab alone, a little more of it


def _unpeek(handle: int) -> None:
    """All of it seen again - the tab's region taken off."""
    import ctypes
    try:
        ctypes.windll.user32.SetWindowRgn(ctypes.c_void_p(handle), None, True)
    except Exception:
        log.debug("the side panel's region could not be taken off", exc_info=True)


def _monitor_box(handle: int) -> Tuple[int, int, int, int]:
    """The whole of the screen a window is on - its edges, taskbar and all."""
    import ctypes
    from ctypes import wintypes

    class _Info(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]
    user32 = ctypes.windll.user32
    user32.MonitorFromWindow.restype = ctypes.c_void_p
    info = _Info()
    info.cbSize = ctypes.sizeof(_Info)
    user32.GetMonitorInfoW(ctypes.c_void_p(user32.MonitorFromWindow(handle, 2)), ctypes.byref(info))
    box = info.rcMonitor
    return box.left, box.top, box.right, box.bottom


def _peeking(handle: int) -> bool:
    """Whether a window shown is put away to its tab at the right edge of its screen (`peek_titled`)."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    if not user32.IsWindowVisible(handle) or user32.IsIconic(handle):
        return False
    rect = wintypes.RECT()
    user32.GetWindowRect(handle, ctypes.byref(rect))
    try:
        dpi = int(user32.GetDpiForWindow(handle)) or 96
    except Exception:                                      # pragma: no cover - an older Windows
        dpi = 96
    return rect.left >= _monitor_box(handle)[2] - round(PEEK * dpi / 96) - 2


def peek_titled(titles: set, dock: Any = None) -> bool:
    """THE SIDE PANEL PUT AWAY TO ITS TAB (the user, 2026-11-16): slid out to the right edge of its screen but for its
    left PEEK, which holds the tab that brings it back - above every other window, so it is always there to press.
    `dock`: its strip given back first, the windows moved for it put back. False when none is shown here."""
    if _no_windows_here() or sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        _per_monitor()
        user32 = ctypes.windll.user32
        handle = _windows_page(set(titles))
        if handle is None:
            return False
        rect = wintypes.RECT()
        user32.GetWindowRect(handle, ctypes.byref(rect))
        try:
            dpi = int(user32.GetDpiForWindow(handle)) or 96
        except Exception:                                  # pragma: no cover - an older Windows
            dpi = 96
        box = _monitor_box(handle)
        right = box[2]
        width, height = rect.right - rect.left, rect.bottom - rect.top
        wide, tall = round(PEEK * dpi / 96), round(PEEK_TALL * dpi / 96)
        # ITS TAB ALONE SEEN AT ONCE, then moved to the edge in one step - the window kept as large as it was (a window
        # region shows the tab's piece of it), never laid out anew; and only then the strip given back and the other
        # windows moved back (the user, 2026-11-16: "collapse和expand的弹出变卡了" - slid whole while every window under
        # it was being fitted again, it went in jerks).
        gdi32 = ctypes.windll.gdi32
        gdi32.CreateRectRgn.restype = ctypes.c_void_p
        region = gdi32.CreateRectRgn(0, height // 2 - tall // 2, wide, height // 2 + tall // 2)
        user32.SetWindowRgn(ctypes.c_void_p(handle), ctypes.c_void_p(region), True)
        user32.SetWindowPos(ctypes.c_void_p(handle), ctypes.c_void_p(-1), right - wide, rect.top, width, height,
                            0x0010)                        # above the others: its tab always there to press
        if dock is not None and getattr(dock, "docked", False):
            dock.undock()                                  # the others back as they were - after
            # ABOVE THEM AGAIN (the user, 2026-11-16: "sidebar消失了，那个bar和button都没有了"): giving the strip back
            # put it among the others, under the window it had made room for.
            user32.SetWindowPos(ctypes.c_void_p(handle), ctypes.c_void_p(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        return True
    except Exception:
        log.warning("the window could not be put away to its tab", exc_info=True)
    return False


def show_titled(titles: set, on: bool) -> bool:
    """One of the agent's own windows shown again (not made the one typed into) or hidden - its place kept."""
    if _no_windows_here() or sys.platform != "win32":
        return False
    try:
        import ctypes
        handle = _windows_page(set(titles), hidden=True)
        if handle is None:
            return False
        ctypes.windll.user32.ShowWindowAsync(ctypes.c_void_p(handle), 4 if on else 0)
        return True
    except Exception:
        return False


def covered_titled(titles: set) -> bool:
    """Whether one of the agent's own windows, titled one of `titles`, is shown but has the window in front over it -
    another program's, its rectangle crossing this one's (the side panel not pinned, a browser pressed on)."""
    if _no_windows_here() or sys.platform != "win32":
        return False
    try:
        import ctypes
        import os
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        handle = _windows_page(set(titles))
        if handle is None or not user32.IsWindowVisible(handle):
            return False
        front = user32.GetForegroundWindow()
        if not front or int(front) == int(handle):
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(front, ctypes.byref(pid))
        from . import windows as _windows
        if pid.value in {os.getpid(), *_windows.OTHER_OWN_PROCESSES}:
            return False                                  # the avatar, its tab, a window of the agent's: not over it
        mine, theirs = wintypes.RECT(), wintypes.RECT()
        user32.GetWindowRect(handle, ctypes.byref(mine))
        user32.GetWindowRect(front, ctypes.byref(theirs))
        return theirs.left < mine.right and theirs.right > mine.left and theirs.top < mine.bottom and theirs.bottom > mine.top
    except Exception:
        log.debug("whether the window is covered could not be told", exc_info=True)
        return False


def on_top_titled(titles: set, on: bool) -> bool:
    """One of the agent's own windows above every other, or among them again."""
    if _no_windows_here() or sys.platform != "win32":
        return False
    try:
        import ctypes
        handle = _windows_page(set(titles))
        if handle is None:
            return False
        ctypes.windll.user32.SetWindowPos(ctypes.c_void_p(handle), ctypes.c_void_p(-1 if on else -2), 0, 0, 0, 0,
                                          0x0001 | 0x0002 | 0x0010)
        return True
    except Exception:
        return False


def peeking_titled(titles: set) -> bool:
    if _no_windows_here() or sys.platform != "win32":
        return False
    try:
        handle = _windows_page(set(titles))
        return handle is not None and _peeking(handle)
    except Exception:
        return False


def _off_every_screen(handle: int) -> bool:
    """Whether a window stands on no screen at all (one made off them, never brought in)."""
    import ctypes
    ctypes.windll.user32.MonitorFromWindow.restype = ctypes.c_void_p
    return not ctypes.windll.user32.MonitorFromWindow(handle, 0)        # MONITOR_DEFAULTTONULL


def slide_in_titled(titles: set, width: Optional[int] = None, dock: Any = None, activate: bool = True) -> bool:
    """One of the agent's own windows, titled one of `titles`, slid in from the right edge of its
    screen, as tall as it is - from where `slide_out_titled` put it away, or from off every screen
    where it was made and never brought in (2026-11-13: "1 recommendation 点击open好像打不开了"); one shown
    already, to the front. `width`: as wide as this at the screen's scaling. `dock` (dock.py): the strip
    it goes into taken from the screen first, the other windows moved over for it. False when none is
    open, for the caller to open one."""
    if _no_windows_here() or sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        _per_monitor()
        user32 = ctypes.windll.user32
        handle = _windows_page(set(titles), hidden=True)
        if handle is None:
            return False
        _close_others(set(titles), handle)       # ONE PANEL: another left from before, closed
        off = _off_every_screen(handle)
        peeking = _peeking(handle)               # put away to its tab: slid in from where it is
        if user32.IsWindowVisible(handle) and not user32.IsIconic(handle) and not off and not peeking:
            if activate:
                user32.SetForegroundWindow(handle)
            return True
        if user32.IsIconic(handle):
            user32.ShowWindow(handle, 9)                   # SW_RESTORE
        rect = wintypes.RECT()
        user32.GetWindowRect(handle, ctypes.byref(rect))
        if off or width:
            (left, top, right, bottom), dpi = _cursor_work_area()
            wide = round(width * dpi / 96) if width else rect.right - rect.left
        else:
            left, top, right, bottom = _work_area(handle)
            wide = rect.right - rect.left
        width, height = max(wide, 200), bottom - top
        if dock is not None:
            # SEEN FIRST, AT THE EDGE, the strip taken after (the user, 2026-11-16: "collapse和expand的弹出变卡了"): taking
            # it fits every maximised window again and moves the others - waited for, the panel came after all that.
            box = _monitor_box(handle)
            user32.SetWindowPos(handle, 0, box[2] - width, top, width, height, 0x0004 | 0x0010 | 0x0100)
            if peeking:
                _unpeek(handle)
            user32.ShowWindowAsync(handle, 4)              # SW_SHOWNOACTIVATE
        strip = dock.dock(handle, width) if dock is not None else None
        held = getattr(dock, "strip", None) if dock is not None else None
        if not strip and held and getattr(dock, "docked", False):
            # ITS STRIP HELD, IT GOES THERE (the user, 2026-11-16: "为什么刚刚ctrl + c变成这样了" - the strip not
            # given back to it a moment, it was put by the work area its own strip had narrowed, left of it).
            strip = held
        elif not strip and held and abs(held[0] - right) <= 2:
            right = held[2]                            # over the windows, at the screen's edge, not its strip's
        if strip:
            # INTO THE STRIP TAKEN FOR IT, IN ONE STEP, as the others move over (the user, 2026-11-16: "这个缩放
            # 要快"): slid while every other window was being fitted again, the slide took 260 ms, not 120.
            # Not waited for (SWP_ASYNCWINDOWPOS, ShowWindowAsync): the panel's own process lays itself out at
            # its new size - 170 ms waited for, measured - while this goes on.
            left, top, right, bottom = strip
            user32.SetWindowPos(handle, 0, left, top, right - left, bottom - top, 0x0004 | 0x0010 | 0x4000)
            user32.ShowWindowAsync(handle, 4)              # SW_SHOWNOACTIVATE
            if activate:                                   # a notification's: where the user types stays
                user32.SetForegroundWindow(handle)
            return True
        start = rect.left if peeking else right
        # Among the others again, over them as it opens - above every one only while it is put away to its tab.
        user32.SetWindowPos(ctypes.c_void_p(handle), ctypes.c_void_p(-2), start, top, width, height, 0x0010)
        if peeking:
            _unpeek(handle)                                # all of it, from where its tab was
        user32.ShowWindow(handle, 4)                       # SW_SHOWNOACTIVATE
        _slide(handle, start, right - width, top, width, height)
        if activate:
            user32.SetForegroundWindow(handle)
        return True
    except Exception:
        log.warning("the window could not be slid in", exc_info=True)
    return False


def fill_screen_titled(titles: set) -> Optional[Tuple[int, int, int, int]]:
    """THE SIDE PANEL OVER THE WHOLE SCREEN (the user, 2026-11-20: "加一个全屏图标，是可以让整个sidebar最大化，再点一下恢复"):
    the agent's window titled one of `titles` made as large as the screen it is on, taskbar and all - for a game.
    Where it was before ((left, top, right, bottom)), to put it back (`put_titled`); None when none is shown."""
    if _no_windows_here() or sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        _per_monitor()
        user32 = ctypes.windll.user32
        handle = _windows_page(set(titles))
        if handle is None:
            return None
        rect = wintypes.RECT()
        user32.GetWindowRect(handle, ctypes.byref(rect))
        before = (rect.left, rect.top, rect.right, rect.bottom)
        left, top, right, bottom = _monitor_box(handle)
        user32.SetWindowPos(handle, 0, left, top, right - left, bottom - top, 0x0004 | 0x0010 | 0x0040)
        return before
    except Exception:
        log.info("the side panel could not fill the screen", exc_info=True)
        return None


def put_titled(titles: set, box: Tuple[int, int, int, int]) -> bool:
    """The agent's window titled one of `titles` put back where it was (`box`: left, top, right, bottom)."""
    if _no_windows_here() or sys.platform != "win32":
        return False
    try:
        import ctypes
        _per_monitor()
        handle = _windows_page(set(titles))
        if handle is None:
            return False
        left, top, right, bottom = box
        ctypes.windll.user32.SetWindowPos(handle, 0, left, top, right - left, bottom - top, 0x0004 | 0x0010 | 0x0040)
        return True
    except Exception:
        log.info("the side panel could not be put back", exc_info=True)
        return False


def resize_titled(titles: set, width: int, dock: Any = None) -> bool:
    """THE SIDE PANEL WIDER, OR BACK (2026-11-16: its picture's column opens beside the chat only when there
    is a picture): the agent's window titled one of `titles`, shown, made `width` wide at the screen's
    scaling, its right edge where it is - into a strip taken again at that width when `dock` holds one, the
    windows it now covers moved over. False when none is shown."""
    if _no_windows_here() or sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        _per_monitor()
        user32 = ctypes.windll.user32
        handle = _windows_page(set(titles))
        if handle is None:
            return False
        rect = wintypes.RECT()
        user32.GetWindowRect(handle, ctypes.byref(rect))
        try:
            dpi = int(user32.GetDpiForWindow(handle)) or 96
        except Exception:                                  # pragma: no cover - an older Windows
            dpi = 96
        # ITS FRAME COUNTED IN (the user, 2026-11-16: "点击sidebar左边框想拖动的时候，他会先跳到右边"): `width` is its
        # page's; a window with a frame round the page - the browser's, where the panel opened there - that much
        # wider, not narrowed by it as the drag begins.
        inside = wintypes.RECT()
        frame = 0
        if user32.GetClientRect(handle, ctypes.byref(inside)):
            frame = max(0, (rect.right - rect.left) - (inside.right - inside.left))
        wide = max(200, round(width * dpi / 96) + frame)
        strip = dock.dock(handle, wide) if dock is not None and getattr(dock, "docked", False) else None
        if strip:
            left, top, right, bottom = strip
        else:
            right, top, bottom = rect.right, rect.top, rect.bottom
            left = right - wide
        user32.SetWindowPos(handle, 0, left, top, right - left, bottom - top, 0x0004 | 0x0010 | 0x0100)   # NOCOPYBITS
        return True
    except Exception:
        log.warning("the side panel could not be made wider or narrower", exc_info=True)
    return False


#: How often the pointer is looked at while the side panel's edge is dragged; how long a drag may last at most.
EDGE_STEP_SECONDS, EDGE_LONGEST_SECONDS = 0.012, 120.0


def drag_left_edge_titled(titles: set, narrowest: int, widest: int, let_go: Any) -> bool:
    """THE SIDE PANEL'S LEFT EDGE FOLLOWING THE POINTER while the button that pressed it is held (the user,
    2026-11-16: "还是没法移动sidebar"): the agent's window titled one of `titles` made as wide as from where the
    pointer is to its right edge, `narrowest` to `widest` at the screen's scaling - read here, from where the pointer
    really is: the page's own idea of where it is goes stale once another process moves its window, and the width it
    worked out went back to where it began. Let go, `let_go(width)` with the width in the page's pixels. Not waited
    for. False when none is shown."""
    if _no_windows_here() or sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    handle = _windows_page(set(titles))
    if handle is None:
        return False

    def follow() -> None:
        _per_monitor()
        rect = wintypes.RECT()
        user32.GetWindowRect(handle, ctypes.byref(rect))
        try:
            dpi = int(user32.GetDpiForWindow(handle)) or 96
        except Exception:                                  # pragma: no cover - an older Windows
            dpi = 96
        right, top, bottom, last = rect.right, rect.top, rect.bottom, rect.left
        least, most = round(narrowest * dpi / 96), round(widest * dpi / 96)
        try:                                               # as wide as its screen at most: never past its left edge
            most = min(most, right - _monitor_box(handle)[0])
        except Exception:                                  # pragma: no cover - its screen not known
            pass
        # The button pressed, as the hand presses it: the right one where the buttons are swapped.
        button = 0x02 if user32.GetSystemMetrics(23) else 0x01
        point = wintypes.POINT()
        # Where on the edge it was pressed, kept: the edge stays under the pointer as it was - not put under it.
        held = point.x - last if user32.GetCursorPos(ctypes.byref(point)) else 0
        end = time.monotonic() + EDGE_LONGEST_SECONDS
        try:
            while time.monotonic() < end and user32.GetAsyncKeyState(button) & 0x8000:
                if user32.GetCursorPos(ctypes.byref(point)):
                    left = min(right - least, max(right - most, point.x - held))
                    if left != last:
                        # ITS OLD PICTURE NOT CARRIED ALONG (the user, 2026-11-16: dragged by its left edge, "他先往左移动
                        # 再回归位置"): Windows moved what was drawn with the window's left edge, then the page drew itself
                        # again where it belongs - SWP_NOCOPYBITS, and it is only drawn where it belongs.
                        user32.SetWindowPos(handle, 0, left, top, right - left, bottom - top,
                                            0x0004 | 0x0010 | 0x0100)
                        last = left
                time.sleep(EDGE_STEP_SECONDS)
        finally:
            try:
                let_go(round((right - last) * 96 / dpi))
            except Exception:
                log.warning("the side panel's width could not be kept", exc_info=True)
    threading.Thread(target=follow, name="side-panel-edge", daemon=True).start()
    return True


def edge_strip_titled(titles: set, strip: int, outside: int) -> bool:
    """THE EDGE TAKEN ON BOTH SIDES OF ITS LINE (the user, 2026-11-20: "我把鼠标放在白色线的中间，就应该变成缩放的形状"): the
    window `strip` (a handle) set just left of the side panel's left edge, `outside` pixels wide at 96 dpi and as tall
    as it, straight above it in the order of windows - so it covers no other window the panel is under. Measured and
    set in the screen's own pixels, this thread put back as it was after. False when the panel is not shown (put
    away, to its tab, or none): the strip is then hidden by its owner."""
    if _no_windows_here() or sys.platform != "win32" or not strip:
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    handle = _windows_page(set(titles))
    if handle is None or not user32.IsWindowVisible(handle) or user32.IsIconic(handle) or _peeking(handle):
        return False
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    before = None
    try:
        before = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))     # PER_MONITOR_AWARE_V2, for now
        rect = wintypes.RECT()
        user32.GetWindowRect(handle, ctypes.byref(rect))
        try:
            dpi = int(user32.GetDpiForWindow(handle)) or 96
        except Exception:                                  # pragma: no cover - an older Windows
            dpi = 96
        wide = max(2, round(outside * dpi / 96))
        if rect.left - wide < _monitor_box(handle)[0] - 1:
            return False                                   # at its screen's left edge: nothing outside it to take
        user32.GetWindow.restype = wintypes.HWND
        above = user32.GetWindow(handle, 3)                # GW_HWNDPREV: what is just over the panel
        if above == strip:
            after = None                                   # already right over it
        elif above:
            after = above
        else:
            topmost = user32.GetWindowLongW(handle, -20) & 0x8                  # WS_EX_TOPMOST
            after = wintypes.HWND(-1) if topmost else wintypes.HWND(0)
        flags = 0x0010 | (0x0004 if after is None else 0)  # SWP_NOACTIVATE, SWP_NOZORDER when it is in place
        user32.SetWindowPos(wintypes.HWND(strip), after, rect.left - wide, rect.top, wide, rect.bottom - rect.top, flags)
        return True
    finally:
        if before:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(before))


def dock_titled(titles: set, width: int, dock: Any) -> bool:
    """THE SIDE PANEL PINNED WHILE IT IS SHOWN (2026-11-16): the agent's window titled one of `titles` put into a
    strip `width` wide (at the screen's scaling) taken for it now, the other windows moved over. False when none is
    shown, or no strip could be taken."""
    if _no_windows_here() or sys.platform != "win32" or dock is None:
        return False
    try:
        import ctypes
        _per_monitor()
        user32 = ctypes.windll.user32
        handle = _windows_page(set(titles))
        if handle is None:
            return False
        try:
            dpi = int(user32.GetDpiForWindow(handle)) or 96
        except Exception:                                  # pragma: no cover - an older Windows
            dpi = 96
        strip = dock.dock(handle, max(200, round(width * dpi / 96)))
        if not strip:
            return False
        left, top, right, bottom = strip
        user32.SetWindowPos(handle, 0, left, top, right - left, bottom - top, 0x0004 | 0x0010 | 0x4000)
        return True
    except Exception:
        log.warning("the side panel could not be pinned", exc_info=True)
    return False


def slide_out_titled(titles: set, dock: Any = None) -> bool:
    """One of the agent's own windows, titled one of `titles`, slid out to the right edge and put
    away - kept, hidden, for `slide_in_titled`; `dock`: its strip of the screen given back after, the
    windows moved for it put back. False when none is shown here."""
    if _no_windows_here() or sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        handle = _windows_page(set(titles))
        if handle is None:
            return False
        if SLIDE_OUT:
            rect = wintypes.RECT()
            user32.GetWindowRect(handle, ctypes.byref(rect))
            _left, _top, right, _bottom = _work_area(handle)
            width, height = rect.right - rect.left, rect.bottom - rect.top
            _slide(handle, rect.left, max(right, rect.left), rect.top, width, height)
        user32.ShowWindow(handle, 0)                       # SW_HIDE - at once
        if dock is not None:
            dock.undock()                                  # then the others back as they were
        return True
    except Exception:
        log.warning("the window could not be slid out", exc_info=True)
    return False


def _windows_of(pid: int) -> List[int]:
    """The browser windows - framed, not its small hidden ones - of the process `pid`, shown or not."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found: List[int] = []
    call = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def look(handle: int, _extra: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if owner.value == pid:
            kind = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(handle, kind, 64)
            style = user32.GetWindowLongW(handle, -16)                 # GWL_STYLE
            if kind.value == BROWSER_WINDOW and style & 0x00C00000:   # WS_CAPTION
                found.append(handle)
        return True
    user32.EnumWindows(call(look), 0)
    return found


#: Where the side window is made: off every screen - Chrome keeps it there (measured 2026-11-13: at
#: -32768), so nothing of it is seen until it is slid in.
OFF_SCREEN = (-32000, 0)
#: A browser on a new profile can take this long before its window is titled (measured 2026-11-13: over
#: 15 s): waited for, to be slid in - pressed again after, it is slid in still (`slide_in_titled`).
SIDE_OPENING_SECONDS = 45.0


def _cursor_work_area() -> Tuple[Tuple[int, int, int, int], int]:
    """The work area of the screen the pointer is on - where the avatar was just pressed - and its
    dots per inch, in pixels (this thread made per-monitor aware first)."""
    import ctypes
    from ctypes import wintypes

    class _MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
    user32 = ctypes.windll.user32
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    user32.MonitorFromPoint.restype = wintypes.HMONITOR
    monitor = user32.MonitorFromPoint(point, 2)              # MONITOR_DEFAULTTONEAREST
    info = _MonitorInfo()
    info.cbSize = ctypes.sizeof(_MonitorInfo)
    user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    dpi_x, dpi_y = wintypes.UINT(96), wintypes.UINT(96)
    try:
        ctypes.windll.shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
    except Exception:                                        # pragma: no cover - an older Windows
        pass
    work = info.rcWork
    return (work.left, work.top, work.right, work.bottom), int(dpi_x.value or 96)


def open_side_window(url: str, profile: Path, size: Tuple[int, int], titles: Optional[set] = None) -> bool:
    """`url` in a window of its own DOWN THE RIGHT SIDE, slid in - made off every screen and only then
    put there (the user, 2026-11-13: "你现在windows上要先在左边跳一下，才会在右边": the browser drew it
    where it likes, and the page moved it only once it had loaded). `size[0]` wide at the screen's
    scaling, as tall as its work area. False where none can be opened here."""
    if sys.platform != "win32" or _no_windows_here():
        return open_window(url, profile, size)
    program = browser()
    if program is None:
        return False
    try:
        import ctypes
        Path(profile).mkdir(parents=True, exist_ok=True)
        started = subprocess.Popen(
            command(program, url, Path(profile), size, OFF_SCREEN), close_fds=True,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        log.warning("could not open %s in a window of its own", program, exc_info=True)
        return False

    place_side_window(size, titles, started.pid)
    return True


def place_side_window(size: Tuple[int, int], titles: Optional[set], pid: int = 0, dock: Any = None) -> None:
    """The side window, once it is there off every screen - the process's own (`pid`), or one by its title
    - put at the right edge as tall as the work area and slid in; `dock`: into the strip taken for it, the
    other windows moved over (dock.py). In its own thread."""
    import ctypes

    def place() -> None:
        try:
            placing()
        except Exception:                                     # said: the window would stay off every screen
            log.warning("the side window could not be put on the screen", exc_info=True)

    def placing() -> None:
        user32 = ctypes.windll.user32
        try:                                                  # in pixels, whatever the scaling
            user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
        except Exception:                                     # pragma: no cover - an older Windows
            pass
        (left, top, right, bottom), dpi = _cursor_work_area()
        width, height = round(size[0] * dpi / 96), bottom - top
        end = time.monotonic() + SIDE_OPENING_SECONDS
        while time.monotonic() < end:
            # ITS OWN, OR - HANDED TO THE BROWSER ALREADY RUNNING ON THAT PROFILE, OR OPENED BY THE PANEL'S HOST -
            # ONE BY ITS TITLE STILL OFF EVERY SCREEN (2026-11-13: the window made off the screens and never
            # brought in).
            found = list(_windows_of(pid)) if pid else []
            titled = _windows_page(set(titles), hidden=False) if titles else None
            if titled is not None and _off_every_screen(titled) and titled not in found:
                found.append(titled)
            for handle in found:
                if not user32.IsWindowVisible(handle):
                    continue
                strip = dock.dock(handle, width) if dock is not None else None
                if strip:                                     # in one step, as the others move over
                    _left, top, right, bottom = strip
                    user32.SetWindowPos(handle, 0, _left, top, right - _left, bottom - top, 0x0004 | 0x0010)
                else:
                    user32.SetWindowPos(handle, 0, right, top, width, height, 0x0004 | 0x0010)
                    _slide(handle, right, right - width, top, width, height)
                user32.SetForegroundWindow(handle)
                return
            time.sleep(0.005)
    threading.Thread(target=place, name="side-window", daemon=True).start()


def open_hosted_side_window(host: object, url: str, size: Tuple[int, int], title: str,
                            titles: Optional[set] = None, dock: Any = None) -> bool:
    """THE SIDE PANEL IN WEBVIEW2 (panel_host.py): opened by the host off every screen, then slid in as the
    browser's was (`dock`: into its strip of the screen). False when the host cannot - for the browser's
    way."""
    if sys.platform != "win32" or _no_windows_here() or host is None:
        return False
    try:
        if not host.open(url, title, size[0], size[1], OFF_SCREEN[0], OFF_SCREEN[1]):  # type: ignore[attr-defined]
            return False
    except Exception:
        log.info("the side panel's host could not open it", exc_info=True)
        return False
    place_side_window(size, titles or {title}, dock=dock)
    return True


def close_titled(titles: set) -> int:
    """Each of the agent's own windows titled one of `titles` - shown or put away - asked to close."""
    if _no_windows_here() or sys.platform != "win32":
        return 0
    closed = 0
    try:
        import ctypes
        user32 = ctypes.windll.user32
        for _ in range(3):
            handle = _windows_page(set(titles), hidden=True)
            if handle is None:
                break
            user32.PostMessageW(handle, 0x0010, 0, 0)     # WM_CLOSE
            closed += 1
            end = time.monotonic() + 2.0
            while time.monotonic() < end and _windows_page(set(titles), hidden=True) == handle:
                time.sleep(0.05)
    except Exception:
        log.debug("the window could not be closed", exc_info=True)
    return closed


def shown_titled(titles: set) -> bool:
    """Whether one of the agent's own windows titled one of `titles` is shown (not put away - to its tab neither)."""
    if _no_windows_here() or sys.platform != "win32":
        return False
    try:
        import ctypes
        handle = _windows_page(set(titles))
        return handle is not None and not ctypes.windll.user32.IsIconic(handle) and not _peeking(handle)
    except Exception:
        return False


def close_the_page() -> int:
    """THE PAGE'S WINDOW CLOSED WITH THE AGENT (the user, 2026-11-09: "agent关闭之后，自动把overview
    panel也关了"): every window of it - the app window titled the page's own name alone, never a tab
    of the user's browser - asked to close as its own close button would. How many."""
    if _no_windows_here() or sys.platform != "win32":
        return 0
    closed = 0
    try:
        import ctypes
        user32 = ctypes.windll.user32
        for _ in range(5):                       # one at a time: each found, then the next
            handle = _windows_page()
            if handle is None:
                break
            user32.PostMessageW(handle, 0x0010, 0, 0)   # WM_CLOSE
            closed += 1
            end = time.monotonic() + 2.0
            while time.monotonic() < end and _windows_page() == handle:
                time.sleep(0.05)
    except Exception:
        log.debug("the page's window could not be closed", exc_info=True)
    return closed


def wait_for_the_page() -> None:
    """Until the window just asked for is there, or it is not coming."""
    if _no_windows_here():
        return
    seen = sys.platform == "win32"
    end = time.monotonic() + (OPENING_SECONDS if seen else UNSEEN_SECONDS)
    while time.monotonic() < end:
        try:
            if seen and _windows_page() is not None:
                return
        except Exception:
            log.debug("the page's window could not be looked for", exc_info=True)
            return
        time.sleep(0.2)


def open_the_page(url: str, profile: Path) -> bool:
    """`url` in the page's window; False where no browser here can make one."""
    if _no_windows_here():
        return False
    program = browser()
    if program is None:
        return False
    try:
        Path(profile).mkdir(parents=True, exist_ok=True)
        flags = 0
        if sys.platform == "win32":
            flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                     | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        subprocess.Popen(command(program, url, Path(profile), PAGE_SIZE), close_fds=True,
                         creationflags=flags, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        log.warning("could not open the page in a window of its own", exc_info=True)
        return False


#: A PANEL OF THE AGENT'S OWN (the user, 2026-11-02): where the process that
#: opens the box has one - on a Mac, the desktop's (ui/mac_panel.py, made on
#: its main thread) - `opener(url, size)` opens it and says True.
_native_opener: Optional[Callable[[str, Tuple[int, int]], bool]] = None


def set_native_opener(opener: Optional[Callable[[str, Tuple[int, int]], bool]]) -> None:
    global _native_opener
    _native_opener = opener


def open_page(url: str, profile: Path, size: Tuple[int, int] = SIZE) -> None:
    """In a window of its own where one can be opened - a panel of the
    agent's own on a Mac, a browser's app window on Windows - else in the
    browser."""
    opener = _native_opener
    if opener is not None:
        try:
            if opener(url, size):
                return
        except Exception:
            log.warning("no panel of its own for %s", url, exc_info=True)
    if not open_window(url, profile, size):
        import webbrowser
        webbrowser.open(url)


__all__ = ["browser", "command", "open_window", "open_page", "set_native_opener", "SIZE",
           "PROFILE", "close_the_page"]
