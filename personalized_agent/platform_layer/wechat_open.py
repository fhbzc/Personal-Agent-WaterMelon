"""WECHAT BROUGHT FORWARD FOR THE USER - NOTHING DONE IN IT (the user,
2026-11-01: "open the program; clicking and the like, the user does, by
the instructions").

WeChat has no link to a chat, and the application does not drive it: no
key is pressed, nothing is clicked, nothing is written in it. Its window is
brought in front of the others - shown again if it was minimised - and that
is all; the message is on the clipboard (the caller's), and the note on
the desktop tells the user to find the chat, paste and send.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any, Dict, List

log = logging.getLogger(__name__)

#: WeChat's programs.
PROGRAMS = ("weixin.exe", "wechat.exe")


def bring_forward() -> Dict[str, Any]:
    """WeChat's main window in front - {"opened": True} - or {"opened":
    False, "why": ...}: not open, or not on Windows."""
    if not sys.platform.startswith("win"):
        return {"opened": False, "why": "bringing WeChat forward is done on Windows only"}
    try:
        handle = _main_window()
        if not handle:
            return {"opened": False, "why": "WeChat is not open"}
        _front(handle)
        return {"opened": True}
    except Exception as exc:                             # pragma: no cover - said, not raised
        log.debug("WeChat could not be brought forward", exc_info=True)
        return {"opened": False, "why": f"WeChat could not be brought forward: {exc}"[:200]}


def _main_window() -> int:
    """WeChat's largest visible window - its main one."""
    import ctypes
    import ctypes.wintypes as wt
    import psutil
    user32 = ctypes.windll.user32
    found: List[tuple] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def each(handle, _):
        if not user32.IsWindowVisible(handle) and not user32.IsIconic(handle):
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        try:
            if psutil.Process(pid.value).name().lower() in PROGRAMS:
                box = wt.RECT()
                user32.GetWindowRect(handle, ctypes.byref(box))
                found.append(((box.right - box.left) * (box.bottom - box.top), handle))
        except Exception:
            pass
        return True
    user32.EnumWindows(each, 0)
    return max(found)[1] if found else 0


def _front(handle: int) -> None:
    """Shown, and in front - no key pressed, nothing clicked: the system
    lets a program bring a window forward while its input is joined to the
    one in front."""
    import ctypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    if user32.IsIconic(handle):
        user32.ShowWindow(handle, 9)                     # SW_RESTORE
    front = user32.GetForegroundWindow()
    theirs = user32.GetWindowThreadProcessId(front, None)
    mine = kernel32.GetCurrentThreadId()
    joined = theirs and theirs != mine and user32.AttachThreadInput(mine, theirs, True)
    try:
        user32.SetForegroundWindow(handle)
        user32.BringWindowToTop(handle)
    finally:
        if joined:
            user32.AttachThreadInput(mine, theirs, False)
    time.sleep(0.1)


__all__ = ["bring_forward", "PROGRAMS"]
