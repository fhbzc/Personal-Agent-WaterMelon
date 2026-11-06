"""TWO FINGERS ON A TOUCH SCREEN ZOOM A WINDOW, AS CTRL + AND - DO (the user, 2026-11-05: every place
that zooms). Tk hears nothing of a pinch, so on Windows the window's own messages are listened to:
the system sends a window that has not asked for raw touch WM_GESTURE, and a child's goes up to the
window it is in. A Mac's pinch is in ui/desktop.py; elsewhere there is none to hear.

`listen` is told in steps. It is called from within the system's message: `on_step` must only hand
the step on, not touch Tk.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import Any, Callable, Dict

log = logging.getLogger(__name__)

WM_GESTURE, WM_NCDESTROY = 0x0119, 0x0082
GID_ZOOM, GF_BEGIN = 3, 0x0001
#: How much further apart, or closer, two fingers go for one step.
RATIO = 1.15


class Pinch:
    """Two fingers' distance, as steps of zoom."""

    def __init__(self) -> None:
        self.base = 0.0

    def moved(self, distance: float, begins: bool) -> int:
        if begins or self.base <= 0:
            self.base = float(distance)
            return 0
        if distance >= self.base * RATIO:
            self.base = float(distance)
            return 1
        if distance * RATIO <= self.base:
            self.base = float(distance)
            return -1
        return 0


class _Points(ctypes.Structure):
    _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short)]


class _GestureInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwFlags", ctypes.c_uint), ("dwID", ctypes.c_uint),
                ("hwndTarget", ctypes.c_void_p), ("ptsLocation", _Points),
                ("dwInstanceID", ctypes.c_uint), ("dwSequenceID", ctypes.c_uint),
                ("ullArguments", ctypes.c_ulonglong), ("cbExtraArgs", ctypes.c_uint)]


#: What the system is calling, kept for as long as it may: let go of, it would call nothing.
_HELD: Dict[Any, Any] = {}
_NEXT = [1]


def listen(window: Any, on_step: Callable[[int], None]) -> bool:
    """`on_step(+1 or -1)` as two fingers open or close on `window`. Whether it could be."""
    if not sys.platform.startswith("win"):
        return False
    try:
        from ctypes import wintypes
        user32, comctl = ctypes.windll.user32, ctypes.windll.comctl32
        result, wide, long_ = ctypes.c_ssize_t, ctypes.c_size_t, ctypes.c_ssize_t
        proc_type = ctypes.WINFUNCTYPE(result, wintypes.HWND, ctypes.c_uint, wide, long_, wide, wide)
        comctl.SetWindowSubclass.argtypes = [wintypes.HWND, proc_type, wide, wide]
        comctl.SetWindowSubclass.restype = wintypes.BOOL
        comctl.RemoveWindowSubclass.argtypes = [wintypes.HWND, proc_type, wide]
        comctl.DefSubclassProc.argtypes = [wintypes.HWND, ctypes.c_uint, wide, long_]
        comctl.DefSubclassProc.restype = result
        user32.GetGestureInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_GestureInfo)]
        user32.GetGestureInfo.restype = wintypes.BOOL
        user32.CloseGestureInfoHandle.argtypes = [ctypes.c_void_p]
        window.update_idletasks()
        handle = int(window.wm_frame(), 16)
        pinch = Pinch()
        number = _NEXT[0]
        _NEXT[0] += 1

        def heard(hwnd: Any, message: int, w_param: int, l_param: int, _id: int, _data: int) -> int:
            try:
                if message == WM_GESTURE:
                    info = _GestureInfo()
                    info.cbSize = ctypes.sizeof(_GestureInfo)
                    if user32.GetGestureInfo(ctypes.c_void_p(l_param), ctypes.byref(info)) \
                            and info.dwID == GID_ZOOM:
                        step = pinch.moved(info.ullArguments & 0xFFFFFFFF,
                                           bool(info.dwFlags & GF_BEGIN))
                        if step:
                            on_step(step)
                        user32.CloseGestureInfoHandle(ctypes.c_void_p(l_param))
                        return 0
                elif message == WM_NCDESTROY:
                    comctl.RemoveWindowSubclass(hwnd, _HELD[number], number)
                    # Not let go of here: this very call is running in it. The next one does.
                    _HELD.pop(_HELD.get("gone"), None)
                    _HELD["gone"] = number
            except Exception:                    # nothing may leave here into the system
                log.debug("a pinch could not be read", exc_info=True)
            return comctl.DefSubclassProc(hwnd, message, w_param, l_param)

        proc = proc_type(heard)
        _HELD[number] = proc
        if not comctl.SetWindowSubclass(handle, proc, number, 0):
            _HELD.pop(number, None)
            return False
        return True
    except Exception:
        log.debug("this window will not hear a pinch", exc_info=True)
        return False


__all__ = ["Pinch", "RATIO", "listen"]
