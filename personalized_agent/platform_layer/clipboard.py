"""WORDS ON THE CLIPBOARD, PUT THERE BY THE APPLICATION ITSELF (the user,
2026-11-01).

A reply opened from the Overview's to-do opened Gmail first and asked the
page to copy after - and a browser lets only the page in front write the
clipboard: Gmail was in front, the copy was refused, and the note on the
desktop said it was there. So what is to be pasted is put on the system's
clipboard by the application, before anything is opened, whatever is in
front - and said to be there only when it is:

  - WINDOWS: the clipboard's own calls (OpenClipboard, SetClipboardData,
    Unicode text), tried again for a moment while another program holds it;
  - MACOS: pbcopy;
  - LINUX: wl-copy, xclip or xsel - whichever is there.

It writes nothing but the clipboard, and reads nothing.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time

log = logging.getLogger(__name__)

#: How often, and how long apart, the clipboard is asked for while another
#: program holds it open.
TRIES, BETWEEN = 10, 0.05


def copy(text: str) -> bool:
    """`text` on the clipboard - whether it is."""
    text = str(text or "")
    try:
        if sys.platform.startswith("win"):
            return _windows(text)
        if sys.platform == "darwin":
            return _run(["pbcopy"], text, {"LANG": "en_US.UTF-8"})
        for command in (["wl-copy"], ["xclip", "-selection", "clipboard"],
                        ["xsel", "--clipboard", "--input"]):
            if shutil.which(command[0]):
                return _run(command, text)
    except Exception:                                    # pragma: no cover - not copied
        log.debug("the clipboard could not be written", exc_info=True)
    return False


def _run(command: list, text: str, env: dict = None) -> bool:
    import os
    done = subprocess.run(command, input=text.encode("utf-8"), timeout=5,
                          env=dict(os.environ, **(env or {})),
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return done.returncode == 0


def _windows(text: str) -> bool:
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
    data = text.encode("utf-16-le") + b"\0\0"
    for _ in range(TRIES):
        if user32.OpenClipboard(None):
            break
        time.sleep(BETWEEN)
    else:
        return False
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        at = kernel32.GlobalLock(handle)
        if not at:
            kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(at, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)              # still ours when it was refused
            return False
        return True                                  # the clipboard's now
    finally:
        user32.CloseClipboard()


__all__ = ["copy"]
