"""The side panel docked on a Mac: the other windows make room for it.

(The user, 2026-11-16: the other windows move left when the panel opens, quickly; on a Mac by the Accessibility
API, the permission asked for - "这个要提醒自动设置".) A Mac has no strip of the screen to take, as Windows has: each
window the panel would cover - a large one too, which is what a Mac's "maximized" is - is made narrower or moved
left by the Accessibility API, and put back when the panel goes, unless it was moved in between. Without the
permission nothing is moved: the panel opens over the others, and the system's own request for it is shown once.

The arithmetic is dock.py's (`room_for`, `put_back`); here only where windows are and moving them. Done off the
main thread, each window not waited on by the next.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Any, Dict, List, Optional, Tuple

from .dock import Rect, put_back, room_for

log = logging.getLogger(__name__)


def usable() -> bool:
    return sys.platform == "darwin"


def trusted(ask: bool = False) -> bool:
    """Whether this program may move other windows (Accessibility); `ask`: the system's request shown when not."""
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: bool(ask)}))
    except Exception:                                   # pragma: no cover - no framework
        log.debug("could not ask about Accessibility", exc_info=True)
        return False


def _windows(but_pid: int) -> List[Tuple[int, Rect]]:
    """(owner, where - left, top, right, bottom, top-left origin) of the ordinary windows on the screens."""
    import Quartz
    listed = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements, Quartz.kCGNullWindowID)
    found = []
    for one in listed or []:
        if int(one.get("kCGWindowLayer", 1)) != 0 or int(one.get("kCGWindowOwnerPID", 0)) == but_pid:
            continue
        box = one.get("kCGWindowBounds") or {}
        x, y, w, h = (float(box.get(k, 0)) for k in ("X", "Y", "Width", "Height"))
        if w < 50 or h < 50:
            continue
        found.append((int(one["kCGWindowOwnerPID"]), (int(x), int(y), int(x + w), int(y + h))))
    return found


def _ax_windows(pid: int) -> List[Any]:
    from ApplicationServices import AXUIElementCopyAttributeValue, AXUIElementCreateApplication
    err, windows = AXUIElementCopyAttributeValue(AXUIElementCreateApplication(pid), "AXWindows", None)
    return list(windows or []) if err == 0 else []


def _ax_rect(window: Any) -> Optional[Rect]:
    from ApplicationServices import (AXUIElementCopyAttributeValue, AXValueGetValue, kAXValueCGPointType,
                                     kAXValueCGSizeType)
    err_p, pos = AXUIElementCopyAttributeValue(window, "AXPosition", None)
    err_s, size = AXUIElementCopyAttributeValue(window, "AXSize", None)
    if err_p or err_s:
        return None
    _ok, point = AXValueGetValue(pos, kAXValueCGPointType, None)
    _ok, extent = AXValueGetValue(size, kAXValueCGSizeType, None)
    return (int(point.x), int(point.y), int(point.x + extent.width), int(point.y + extent.height))


def _ax_put(window: Any, where: Rect) -> None:
    import Quartz
    from ApplicationServices import AXUIElementSetAttributeValue, AXValueCreate, kAXValueCGPointType, kAXValueCGSizeType
    AXUIElementSetAttributeValue(window, "AXPosition",
                                 AXValueCreate(kAXValueCGPointType, Quartz.CGPoint(where[0], where[1])))
    AXUIElementSetAttributeValue(window, "AXSize",
                                 AXValueCreate(kAXValueCGSizeType, Quartz.CGSize(where[2] - where[0], where[3] - where[1])))


class MacDock:
    """The windows moved for the panel's strip, and put back."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.moved: Dict[int, Tuple[Any, Rect, Rect]] = {}      # id -> (its AX window, as it was, where put)
        self.asked = False

    @property
    def docked(self) -> bool:
        return bool(self.moved)

    def dock(self, strip: Rect, screen: Rect) -> int:
        """The windows the `strip` (left, top, right, bottom; top-left origin) covers on `screen` moved beside it,
        off this thread. How many are being moved - 0 when none, or no permission (then asked for, once)."""
        if not usable():
            return 0
        if not trusted():
            if not self.asked:
                self.asked = True
                trusted(ask=True)                         # the system's own request, once
                log.info("the side panel: Accessibility not allowed - the other windows are not moved")
            return 0
        listed = [one for one in _windows(os.getpid()) if room_for(one[1], strip, screen) is not None]
        if listed:
            threading.Thread(target=self._move, args=(listed, strip, screen), name="side-panel-room",
                             daemon=True).start()
        return len(listed)

    def _move(self, listed: List[Tuple[int, Rect]], strip: Rect, screen: Rect) -> None:
        with self._lock:
            for pid, seen in listed:
                try:
                    for window in _ax_windows(pid):
                        rect = _ax_rect(window)
                        if rect is None or any(abs(a - b) > 2 for a, b in zip(rect, seen)):
                            continue
                        where = room_for(rect, strip, screen)
                        if where is not None and where != rect:
                            _ax_put(window, where)
                            # Moved again (the panel made wider): put back, it goes where it was first.
                            before = next((key for key, entry in self.moved.items() if entry[0] == window), None)
                            was = self.moved.pop(before)[1] if before is not None else rect
                            self.moved[id(window)] = (window, was, where)
                        break
                except Exception:                         # one program's windows: not the others'
                    log.debug("a window could not be moved for the side panel", exc_info=True)

    def undock(self) -> None:
        """Each window moved put back - unless it was moved since - off this thread."""
        with self._lock:
            moved, self.moved = dict(self.moved), {}
        if moved:
            threading.Thread(target=self._put_back, args=(moved,), name="side-panel-room-back", daemon=True).start()

    @staticmethod
    def _put_back(moved: Dict[int, Tuple[Any, Rect, Rect]]) -> None:
        now = {key: _ax_rect(window) for key, (window, _was, _put) in moved.items()}
        back = put_back({key: (was, put) for key, (_window, was, put) in moved.items()}, now)
        for key, was in back.items():
            try:
                _ax_put(moved[key][0], was)
            except Exception:
                log.debug("a window could not be put back", exc_info=True)
