"""A FOLDER OR A FILE OPENED IN FRONT (the user, 2026-11-08: "打开folder的时候，folder都不是直接跳出来的
而是打开，你可以让他直接跳出来").

Windows lets only the program in front put a window in front: the application, behind the page the
user pressed in, opened Explorer behind everything - seen only as a blink in the taskbar. Opened as
before, its window is then looked for (by its title: the folder's name, or the file's) for a few
seconds, and brought forward as WeChat's is (wechat_open._front: this thread's input joined to the
window in front for the moment it takes). A Mac's `open` and Linux's file managers come forward
themselves.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

#: How long its window is looked for, and how often.
LOOK_SECONDS, LOOK_EVERY = 4.0, 0.15


def opened_in_front(path: Any, select: bool = False) -> None:
    """`path` opened as the system opens it - a folder in its file manager, a file in its program;
    `select`: its folder shown with it chosen - and, on Windows, its window brought in front."""
    path = Path(str(path))
    if sys.platform.startswith("win"):
        if select:
            subprocess.Popen(["explorer", f"/select,{path}"])
        else:
            os.startfile(str(path))                       # type: ignore[attr-defined]
        named = path.parent.name if select else path.name
        if os.environ.get("PA_NO_DIALOGS"):
            return                               # the tests': nothing on the screen moved
        threading.Thread(target=_bring_forward, args=(named, time.monotonic()),
                         name="open-in-front", daemon=True).start()
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if select else ["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent if select else path)])


def window_named(named: str, windows: Any) -> Optional[int]:
    """Of the windows (handle, title) from the front: the newest whose title is that name or
    begins with it - "Report.docx - Word", "harbour" (a folder)."""
    named = named.strip().lower()
    if not named:
        return None
    for handle, title in windows:
        title = str(title or "").strip().lower()
        if title == named or title.startswith(named + " ") or title.startswith(named + " -"):
            return handle
    return None


def _windows() -> list:
    """The windows on the screen, from the front: (handle, title)."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(handle, _):
        if user32.IsWindowVisible(handle):
            length = user32.GetWindowTextLengthW(handle)
            if length:
                title = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(handle, title, length + 1)
                out.append((handle, title.value))
        return True
    user32.EnumWindows(each, 0)
    return out


def _bring_forward(named: str, since: float,
                   find: Optional[Callable[[], list]] = None) -> bool:
    """Its window looked for until it is there, and brought in front - True when it was."""
    find = find or _windows
    while time.monotonic() - since < LOOK_SECONDS:
        try:
            handle = window_named(named, find())
            if handle:
                from .wechat_open import _front
                _front(handle)
                return True
        except Exception:
            log.debug("the window opened could not be brought forward", exc_info=True)
            return False
        time.sleep(LOOK_EVERY)
    return False


__all__ = ["opened_in_front", "window_named", "LOOK_SECONDS"]
