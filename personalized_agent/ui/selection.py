"""DELEGATE WORK BESIDE SELECTED TEXT (Windows).

Select text in any program and a small "Delegate work" button - the words,
then the avatar's face - appears beside it for a few seconds. Clicked, the
text goes to the agent as a piece of work, with the window it came from:
the Delegate work box opens on the context collected and a plan to check.

How the selection is read matters more than anything else here:

* ONLY WHEN A SELECTION WAS JUST MADE: a drag with the left button, or a
  double-click, ended. Nothing is read otherwise.
* THROUGH UI AUTOMATION - the accessibility interface screen readers use:
  the focused control is asked what it has selected. The clipboard is never
  touched and no key is pressed (Ctrl+C would overwrite what the user
  copied, and stop a program running in a terminal).
* NEVER A PASSWORD FIELD, and never one of the agent's own windows.
* KEPT ONLY IF CLICKED: the text is held in memory while the button shows,
  and is gone when it does - unless the user clicks it.

Always there - Delegate work is not a setting.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import math
import os
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional

log = logging.getLogger(__name__)

VK_LBUTTON = 0x01
#: A drag at least this long, in pixels, or two clicks this close in time.
DRAG_PIXELS = 6
DOUBLE_SECONDS = 0.45
#: How often the button is looked at.
POLL_SECONDS = 0.05
MOST_CHARS = 20000


def available() -> bool:
    return sys.platform == "win32"


class SelectionWatch:
    """Watches the left button; when a selection may just have been made,
    asks what is selected - on a thread of its own, with UI Automation."""

    def __init__(self, on_selection: Callable[[Dict[str, Any]], None],
                 on_press: Optional[Callable[[int, int], None]] = None,
                 wanted: Callable[[], bool] = lambda: True) -> None:
        self.on_selection = on_selection
        self.on_press = on_press
        self.wanted = wanted
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        if not available():
            return False
        self._thread = threading.Thread(target=self._loop, name="selection-watch", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    # -- the loop -----------------------------------------------------------------------------
    def _loop(self) -> None:
        try:
            sys.coinit_flags = 0                  # COINIT_MULTITHREADED, as window_text
            import comtypes.client
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as uia
            automation = comtypes.client.CreateObject(uia.CUIAutomation,
                                                      interface=uia.IUIAutomation)
        except Exception as exc:
            log.info("selected text cannot be read here: %s", exc)
            return
        user32 = ctypes.windll.user32
        pressed, down_at, down_xy, last_click = False, 0.0, (0, 0), 0.0
        while not self._stop.wait(POLL_SECONDS):
            now_pressed = bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
            if now_pressed and not pressed:
                down_xy, down_at = _cursor(), time.monotonic()
                if self.on_press is not None:
                    self.on_press(*down_xy)
            elif pressed and not now_pressed:
                up_xy = _cursor()
                moved = math.dist(down_xy, up_xy) >= DRAG_PIXELS
                double = down_at - last_click < DOUBLE_SECONDS
                last_click = time.monotonic()
                if (moved or double) and self.wanted():
                    time.sleep(0.12)              # let the program settle its selection
                    said = self._selected(automation, uia)
                    if said and said["text"].strip():
                        said.update(x=up_xy[0], y=up_xy[1])
                        self.on_selection(said)
            pressed = now_pressed

    @staticmethod
    def _selected(automation: Any, uia: Any) -> Optional[Dict[str, Any]]:
        """What the focused control has selected - never in a password field,
        never in one of the agent's own windows."""
        try:
            element = automation.GetFocusedElement()
            if element is None or element.CurrentIsPassword:
                return None
            if int(element.CurrentProcessId or 0) == os.getpid():
                return None
            pattern = element.GetCurrentPattern(10014)            # TextPattern
            if not pattern:
                return None
            text_pattern = pattern.QueryInterface(uia.IUIAutomationTextPattern)
            ranges = text_pattern.GetSelection()
            pieces = []
            for index in range(ranges.Length if ranges else 0):
                pieces.append(ranges.GetElement(index).GetText(MOST_CHARS) or "")
            text = "\n".join(piece for piece in pieces if piece).strip()
            if not text:
                return None
            return {"text": text[:MOST_CHARS], **_window_in_front()}
        except Exception:
            return None


def _cursor() -> tuple:
    point = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return (point.x, point.y)


def _window_in_front() -> Dict[str, str]:
    """The title of the window in front, and its program's name."""
    user32 = ctypes.windll.user32
    handle = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(handle)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(handle, buffer, length + 1)
    app = ""
    try:
        import ctypes.wintypes
        import psutil
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        app = psutil.Process(pid.value).name()
    except Exception:
        pass
    return {"title": buffer.value, "app": app}


def no_activate(window: Any) -> None:
    """A window that is clicked without taking the focus from the program
    the user was in (WS_EX_NOACTIVATE), and kept off the taskbar."""
    if not available():
        return
    try:
        user32 = ctypes.windll.user32
        handle = int(window.wm_frame(), 16)
        style = user32.GetWindowLongW(handle, -20)                    # GWL_EXSTYLE
        user32.SetWindowLongW(handle, -20, style | 0x08000000 | 0x00000080)
    except Exception:
        log.debug("could not keep the button from taking the focus", exc_info=True)


__all__ = ["SelectionWatch", "available", "no_activate"]
