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
            size: Tuple[int, int] = SIZE) -> List[str]:
    """How the window is asked for. Its size here is a first guess: the page
    sizes the window to the screen and puts it in the middle itself (app.js,
    DELEGATE_SOLO), because only the page measures the screen in the units
    the window is measured in - at 175% scaling, a 1920-pixel screen is 1097
    of them, and a place worked out here would be half off it."""
    width, height = size
    return [program, f"--app={url}", f"--user-data-dir={profile}",
            f"--window-size={width},{height}",
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


def _windows_page() -> Optional[int]:
    """The handle of the page's window, if it is open."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found: List[int] = []
    call = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def look(handle: int, _extra: int) -> bool:
        if user32.IsWindowVisible(handle):
            kind, title = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(handle, kind, 64)
            user32.GetWindowTextW(handle, title, 64)
            if is_the_page(kind.value, title.value):
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
           "PROFILE"]
