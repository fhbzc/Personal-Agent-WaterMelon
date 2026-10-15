"""A page of the app in a WINDOW OF ITS OWN - no tabs, no address bar.

Delegate work, started from Explorer, the selection button or the avatar,
used to open the whole page in the user's browser, as a tab among their
others, with the box over it. What was wanted was the box: so it opens as a
window of its own, the size of the box, which closes when the box does.

It is the same page - drawn once, tested once - in a browser's "app" mode
(`--app=URL`): Edge, which every Windows has, or Chrome or Chromium; where
none is found, the user's own browser opens it as before.

A PROFILE OF ITS OWN (`profile`, in the application's internal folder):
none of the user's sign-ins, cookies or extensions are in it, and nothing
of it reaches theirs. It also makes the window a new browser process, the
only way the asked-for size and place are honoured when the user's browser
is already open.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

#: The window's own profile, in the application's internal folder.
PROFILE = "box_window"

#: The box's window: wide enough for the plan beside its labels, tall
#: enough for the plan and the records found under it.
SIZE = (780, 900)


def _windows_candidates() -> List[Path]:
    found = []
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 os.environ.get("LOCALAPPDATA")):
        if not base:
            continue
        found += [Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                  Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"]
    return found


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
            "--no-first-run", "--no-default-browser-check", "--disable-sync"]


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


def open_page(url: str, profile: Path, size: Tuple[int, int] = SIZE) -> None:
    """In a window of its own where one can be opened, else in the browser."""
    if not open_window(url, profile, size):
        import webbrowser
        webbrowser.open(url)


__all__ = ["browser", "command", "open_window", "open_page", "SIZE", "PROFILE"]
