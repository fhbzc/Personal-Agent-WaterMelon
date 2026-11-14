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
from typing import Callable, List, Optional, Tuple

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
            if (kind.value == BROWSER_WINDOW and title.value in titles) if titles \
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


#: SLID IN FROM THE SIDE, SLID OUT TO IT (the user, 2026-11-13: "现在打开好像太慢了，有没有更natural的方式从侧边
#: 弹出"): a window put away is kept, hidden, and slid back in - no browser started again. So long, in
#: so many steps.
SLIDE_SECONDS, SLIDE_STEPS = 0.16, 10


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


def _slide(handle: int, start: int, end: int, top: int, width: int, height: int) -> None:
    import ctypes
    user32 = ctypes.windll.user32
    flags = 0x0004 | 0x0010                                # SWP_NOZORDER | SWP_NOACTIVATE
    for step in range(1, SLIDE_STEPS + 1):
        done = 1 - (1 - step / SLIDE_STEPS) ** 3           # quick, then easing into place
        user32.SetWindowPos(handle, 0, int(start + (end - start) * done), top, width, height, flags)
        time.sleep(SLIDE_SECONDS / SLIDE_STEPS)


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
        if kind.value == BROWSER_WINDOW and title.value in titles and handle != keep:
            others.append(handle)
        return True
    user32.EnumWindows(call(look), 0)
    for handle in others:
        user32.PostMessageW(handle, 0x0010, 0, 0)                       # WM_CLOSE


def _off_every_screen(handle: int) -> bool:
    """Whether a window stands on no screen at all (one made off them, never brought in)."""
    import ctypes
    ctypes.windll.user32.MonitorFromWindow.restype = ctypes.c_void_p
    return not ctypes.windll.user32.MonitorFromWindow(handle, 0)        # MONITOR_DEFAULTTONULL


def slide_in_titled(titles: set, width: Optional[int] = None) -> bool:
    """One of the agent's own windows, titled one of `titles`, slid in from the right edge of its
    screen, as tall as it is - from where `slide_out_titled` put it away, or from off every screen
    where it was made and never brought in (2026-11-13: "1 recommendation 点击open好像打不开了"); one shown
    already, to the front. `width`: as wide as this at the screen's scaling. False when none is open,
    for the caller to open one."""
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
        if user32.IsWindowVisible(handle) and not user32.IsIconic(handle) and not off:
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
        user32.SetWindowPos(handle, 0, right, top, width, height, 0x0004 | 0x0010)
        user32.ShowWindow(handle, 4)                       # SW_SHOWNOACTIVATE
        _slide(handle, right, right - width, top, width, height)
        user32.SetForegroundWindow(handle)
        return True
    except Exception:
        log.debug("the window could not be slid in", exc_info=True)
    return False


def slide_out_titled(titles: set) -> bool:
    """One of the agent's own windows, titled one of `titles`, slid out to the right edge and put
    away - kept, hidden, for `slide_in_titled`. False when none is shown here."""
    if _no_windows_here() or sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        handle = _windows_page(set(titles))
        if handle is None:
            return False
        rect = wintypes.RECT()
        user32.GetWindowRect(handle, ctypes.byref(rect))
        _left, _top, right, _bottom = _work_area(handle)
        width, height = rect.right - rect.left, rect.bottom - rect.top
        _slide(handle, rect.left, max(right, rect.left), rect.top, width, height)
        user32.ShowWindow(handle, 0)                       # SW_HIDE
        return True
    except Exception:
        log.debug("the window could not be slid out", exc_info=True)
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

    def place() -> None:
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
            # ITS OWN, OR - HANDED TO THE BROWSER ALREADY RUNNING ON THAT PROFILE - ONE BY ITS TITLE STILL OFF
            # EVERY SCREEN (2026-11-13: the window made off the screens and never brought in).
            found = list(_windows_of(started.pid))
            titled = _windows_page(set(titles), hidden=False) if titles else None
            if titled is not None and _off_every_screen(titled) and titled not in found:
                found.append(titled)
            for handle in found:
                if not user32.IsWindowVisible(handle):
                    continue
                user32.SetWindowPos(handle, 0, right, top, width, height, 0x0004 | 0x0010)
                _slide(handle, right, right - width, top, width, height)
                return
            time.sleep(0.005)
    threading.Thread(target=place, name="side-window", daemon=True).start()
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
    """Whether one of the agent's own windows titled one of `titles` is shown (not put away)."""
    if _no_windows_here() or sys.platform != "win32":
        return False
    try:
        import ctypes
        handle = _windows_page(set(titles))
        return handle is not None and not ctypes.windll.user32.IsIconic(handle)
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
