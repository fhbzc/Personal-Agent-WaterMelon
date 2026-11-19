"""THE AGENT'S HANDS ON THE SCREEN (the user, 2026-11-18: "加一个模式，是允许agent操控你的screen(包括鼠标键盘)"):
the screen pictured, the pointer moved - slowly, as a person moves it - clicked, dragged and scrolled, words typed and
keys pressed; and the user's own hand noticed: their mouse moved (past a hand's resting nudge) or pressed, or a key
pressed - never one of ours - ends it (the user, 2026-11-18: "还是移动鼠标或者键盘都算取消screen 操作（这样键盘权限没有也
可以)").

Windows: SendInput, each event marked as ours (dwExtraInfo = MARK); low-level mouse and keyboard hooks tell ours by the
mark. The Mac: Quartz events posted with the mark in their user data; listening event taps - the mouse's and the keys'
apart - tell ours by it; with no tap at all (no permission), the pointer watched: moved where we did not put it.

Positions are fractions of the screen (0..1 across, 0..1 down) - of the whole desktop on Windows, of the main display
on the Mac - so the picture taken and the pointer moved agree whatever their scales.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Any, Callable, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

#: Every event we make carries this, to be told from the user's.
MARK = 0x50414741
#: How long a move of the pointer takes, and how many steps it is made in; the pause between two typed letters.
MOVE_SECONDS, MOVE_STEPS, TYPE_EVERY = 0.6, 30, 0.06


def picture() -> Optional[Any]:
    """The screen now, as a PIL image: the whole desktop on Windows, the main display on the Mac. None if it cannot be."""
    try:
        from PIL import ImageGrab
        if sys.platform == "win32":
            return ImageGrab.grab(all_screens=True)
        return ImageGrab.grab()
    except Exception:
        log.info("the screen could not be pictured", exc_info=True)
        return None


class Hands:
    """The pointer and the keys, moved by the agent - and its own end when the user touches either."""

    def __init__(self) -> None:
        self.at: Optional[Tuple[float, float]] = None   # where we last put the pointer (fractions)
        self.taken = threading.Event()                   # the user's hand was seen
        self._on_taken: Optional[Callable[[], None]] = None
        self._watching = None

    # -- the user's hand -----------------------------------------------------------------------------------
    def watch(self, on_taken: Callable[[], None]) -> bool:
        """Watch for the user's own mouse and keys; `on_taken` once, at the first. Whether it watches - where it does
        not, the screen is not taken (agent/screen_control.py)."""
        self.taken.clear()
        self._on_taken = on_taken
        try:
            self._watching = _WindowsWatch(self._seen) if sys.platform == "win32" else _MacWatch(self._seen, self)
            return self._watching.start()
        except Exception:
            log.info("the user's hand cannot be watched", exc_info=True)
            return False

    def unwatch(self) -> None:
        if self._watching is not None:
            try:
                self._watching.stop()
            except Exception:
                log.debug("the watch did not stop cleanly", exc_info=True)
        self._watching = None

    def _seen(self) -> None:
        if self.taken.is_set():
            return
        self.taken.set()
        if self._on_taken is not None:
            threading.Thread(target=self._on_taken, name="screen-taken-back", daemon=True).start()

    def _stopped(self) -> bool:
        return self.taken.is_set()

    # -- acting ---------------------------------------------------------------------------------------------
    def move(self, x: float, y: float) -> bool:
        """The pointer to (x, y) - fractions - drawn there over MOVE_SECONDS, as a hand moves it. False when the user
        took over on the way."""
        start = self.at or _driver().where()
        for k in range(1, MOVE_STEPS + 1):
            if self._stopped():
                return False
            t = k / MOVE_STEPS
            ease = t * t * (3 - 2 * t)
            _driver().move(start[0] + (x - start[0]) * ease, start[1] + (y - start[1]) * ease)
            self.at = (start[0] + (x - start[0]) * ease, start[1] + (y - start[1]) * ease)
            time.sleep(MOVE_SECONDS / MOVE_STEPS)
        return True

    def click(self, x: float, y: float, button: str = "left", times: int = 1) -> bool:
        if not self.move(x, y) or self._stopped():
            return False
        for _ in range(max(1, min(3, times))):
            _driver().button(button, True, times)
            time.sleep(0.05)
            _driver().button(button, False, times)
            time.sleep(0.12)
        return True

    def drag(self, x: float, y: float, to_x: float, to_y: float) -> bool:
        if not self.move(x, y) or self._stopped():
            return False
        _driver().button("left", True, 1)
        time.sleep(0.15)
        done = self.move(to_x, to_y)
        _driver().button("left", False, 1)
        return done

    def scroll(self, x: float, y: float, down: int) -> bool:
        if not self.move(x, y):
            return False
        for _ in range(min(20, abs(int(down)))):
            if self._stopped():
                return False
            _driver().scroll(-1 if down > 0 else 1)
            time.sleep(0.08)
        return True

    def type(self, text: str) -> bool:
        for ch in text:
            if self._stopped():
                return False
            _driver().letter(ch)
            time.sleep(TYPE_EVERY)
        return True

    def keys(self, combo: Sequence[str]) -> bool:
        """Keys pressed together - "ctrl", "shift", "alt", "cmd"/"win", then one key ("enter", "a", "f5", "down")."""
        if self._stopped():
            return False
        return _driver().combo([str(one).lower() for one in combo if str(one).strip()])


# -- Windows ---------------------------------------------------------------------------------------------------------
_VK = {"ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "cmd": 0x5B, "meta": 0x5B,
       "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20, "backspace": 0x08,
       "delete": 0x2E, "del": 0x2E, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "left": 0x25,
       "up": 0x26, "right": 0x27, "down": 0x28, "insert": 0x2D, **{f"f{n}": 0x6F + n for n in range(1, 13)}}
_EXTENDED = {0x2E, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x5B}


class _Windows:
    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.user32 = ctypes, ctypes.windll.user32
        ULONG_PTR = ctypes.c_size_t

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

        class _U(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]
        self.MOUSEINPUT, self.KEYBDINPUT, self.INPUT = MOUSEINPUT, KEYBDINPUT, INPUT

    def _send(self, *inputs: Any) -> None:
        array = (self.INPUT * len(inputs))(*inputs)
        self.user32.SendInput(len(inputs), array, self.ctypes.sizeof(self.INPUT))

    def _mouse(self, flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> Any:
        one = self.INPUT(type=0)
        one.u.mi = self.MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, MARK)
        return one

    def _key(self, vk: int = 0, scan: int = 0, flags: int = 0) -> Any:
        one = self.INPUT(type=1)
        one.u.ki = self.KEYBDINPUT(vk, scan, flags, 0, MARK)
        return one

    def where(self) -> Tuple[float, float]:
        from ctypes import wintypes
        point = wintypes.POINT()
        self.user32.GetCursorPos(self.ctypes.byref(point))
        vx, vy, vw, vh = (self.user32.GetSystemMetrics(n) for n in (76, 77, 78, 79))
        return (point.x - vx) / max(1, vw), (point.y - vy) / max(1, vh)

    def move(self, x: float, y: float) -> None:
        nx, ny = int(round(min(1, max(0, x)) * 65535)), int(round(min(1, max(0, y)) * 65535))
        self._send(self._mouse(0x0001 | 0x8000 | 0x4000, nx, ny))      # MOVE | ABSOLUTE | VIRTUALDESK

    def button(self, which: str, down: bool, _times: int) -> None:
        flags = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}.get(which, (0x0002, 0x0004))
        self._send(self._mouse(flags[0] if down else flags[1]))

    def scroll(self, notches: int) -> None:
        self._send(self._mouse(0x0800, data=120 * notches))

    def letter(self, ch: str) -> None:
        if ch == "\n":
            self.combo(["enter"])
            return
        data = ch.encode("utf-16-le")
        for i in range(0, len(data), 2):                       # each UTF-16 unit (a surrogate pair is two)
            code = data[i] | (data[i + 1] << 8)
            self._send(self._key(0, code, 0x0004), self._key(0, code, 0x0004 | 0x0002))

    def combo(self, keys: List[str]) -> bool:
        codes = []
        for one in keys:
            if one in _VK:
                codes.append(_VK[one])
            elif len(one) == 1:
                vk = self.user32.VkKeyScanW(ord(one)) & 0xFF
                if vk == 0xFF:
                    return False
                codes.append(vk)
            else:
                return False
        downs = [self._key(vk, 0, 0x0001 if vk in _EXTENDED else 0) for vk in codes]
        ups = [self._key(vk, 0, 0x0002 | (0x0001 if vk in _EXTENDED else 0)) for vk in reversed(codes)]
        self._send(*downs)
        time.sleep(0.05)
        self._send(*ups)
        return True


#: A key pressed - WM_KEYDOWN, WM_SYSKEYDOWN. A key let go never counts: it may have been pressed before the agent took
#: the screen (the Enter that sent the message).
_WIN_KEY_DOWNS = (0x0100, 0x0104)
#: The mouse's buttons pressed, its wheels turned; a move, by POINTER_MOST.
_WIN_MOUSE_PRESSES = (0x0201, 0x0204, 0x0207, 0x020B, 0x020A, 0x020E)
_WIN_MOUSE_MOVE = 0x0200
#: HOW FAR THE USER'S POINTER MUST GO TO COUNT (2026-11-18: the screen given back 0.26 s after it was taken - a hand
#: resting on the mouse nudges it a pixel or two): screen pixels, across and down together.
POINTER_MOST = 6


class _Pointer:
    """Where the pointer last was - put there by us or by the user - and whether the user's move went far enough."""

    def __init__(self, most: float = POINTER_MOST) -> None:
        self.most, self.at = most, None

    def moved(self, x: float, y: float, ours: bool) -> bool:
        if ours or self.at is None:
            self.at = (x, y)
            return False
        return abs(x - self.at[0]) + abs(y - self.at[1]) > self.most


class _WindowsWatch:
    """Low-level hooks on a thread of their own - THE MOUSE OR THE KEYBOARD (the user, 2026-11-18: "还是移动鼠标或者键盘都
    算取消screen 操作"): a key pressed, a button pressed, the wheel turned, or the pointer moved past POINTER_MOST,
    that is not marked as ours, is the user's."""

    def __init__(self, seen: Callable[[], None]) -> None:
        self.seen, self.thread, self.tid = seen, None, 0
        self.ready = threading.Event()
        self.ok = False

    def start(self) -> bool:
        self.thread = threading.Thread(target=self._run, name="screen-watch", daemon=True)
        self.thread.start()
        self.ready.wait(3)
        return self.ok

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        ULONG_PTR = ctypes.c_size_t

        class MSLL(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

        class KBDLL(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]
        PROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, PROC, ctypes.c_void_p, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p

        pointer = _Pointer()

        def on_mouse(code: int, wparam: int, lparam: int) -> int:
            if code >= 0:
                event = ctypes.cast(lparam, ctypes.POINTER(MSLL)).contents
                ours = event.dwExtraInfo == MARK
                if wparam == _WIN_MOUSE_MOVE:
                    if pointer.moved(event.x, event.y, ours):
                        self.seen()
                elif not ours and wparam in _WIN_MOUSE_PRESSES:
                    self.seen()
            return user32.CallNextHookEx(None, code, wparam, lparam)

        def on_key(code: int, wparam: int, lparam: int) -> int:
            if (code >= 0 and wparam in _WIN_KEY_DOWNS
                    and ctypes.cast(lparam, ctypes.POINTER(KBDLL)).contents.dwExtraInfo != MARK):
                self.seen()
            return user32.CallNextHookEx(None, code, wparam, lparam)
        self._procs = (PROC(on_mouse), PROC(on_key))          # kept: a callback let go of crashes the hook
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p    # a 64-bit handle, not cut to an int
        user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
        module = kernel32.GetModuleHandleW(None)
        hooks = [user32.SetWindowsHookExW(14, self._procs[0], module, 0),           # WH_MOUSE_LL
                 user32.SetWindowsHookExW(13, self._procs[1], module, 0)]           # WH_KEYBOARD_LL
        self.tid = kernel32.GetCurrentThreadId()
        self.ok = any(hooks)                                  # either the mouse or the keys: the user can stop it
        if not self.ok:
            log.info("the user's hand cannot be watched: hooks %s, error %s", hooks, kernel32.GetLastError())
        self.ready.set()
        message = wintypes.MSG()
        while self.ok and user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        for hook in hooks:
            if hook:
                user32.UnhookWindowsHookEx(ctypes.c_void_p(hook))

    def stop(self) -> None:
        import ctypes
        if self.tid:
            ctypes.windll.user32.PostThreadMessageW(self.tid, 0x0012, 0, 0)     # WM_QUIT


# -- the Mac ---------------------------------------------------------------------------------------------------------
_MAC_KEYS = {"a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13,
             "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "9": 25, "7": 26,
             "8": 28, "0": 29, "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
             "enter": 36, "return": 36, "tab": 48, "space": 49, "backspace": 51, "esc": 53, "escape": 53,
             "delete": 117, "del": 117, "home": 115, "end": 119, "pageup": 116, "pagedown": 121, "left": 123,
             "right": 124, "down": 125, "up": 126, "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
             "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111}


class _Mac:
    def __init__(self) -> None:
        import Quartz
        self.Q = Quartz
        self.bounds = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
        self.down: Optional[str] = None

    def _point(self, x: float, y: float) -> Any:
        b = self.bounds
        return (b.origin.x + min(1, max(0, x)) * b.size.width, b.origin.y + min(1, max(0, y)) * b.size.height)

    def _post(self, event: Any, flags: int = 0) -> None:
        # NO KEY HELD BUT THOSE MEANT (2026-11-18: a typed letter carried Command, taken from the system's state as an
        # event is made - and 2026-11-02 a stuck Command hid the app): each event's modifiers set, none by default.
        self.Q.CGEventSetFlags(event, flags)
        self.Q.CGEventSetIntegerValueField(event, self.Q.kCGEventSourceUserData, MARK)
        self.Q.CGEventPost(self.Q.kCGHIDEventTap, event)

    def where(self) -> Tuple[float, float]:
        at = self.Q.CGEventGetLocation(self.Q.CGEventCreate(None))
        b = self.bounds
        return (at.x - b.origin.x) / max(1, b.size.width), (at.y - b.origin.y) / max(1, b.size.height)

    def move(self, x: float, y: float) -> None:
        Q = self.Q
        kind = {"left": Q.kCGEventLeftMouseDragged, "right": Q.kCGEventRightMouseDragged}.get(self.down or "", Q.kCGEventMouseMoved)
        self._post(Q.CGEventCreateMouseEvent(None, kind, self._point(x, y), Q.kCGMouseButtonLeft))
        self.last = (x, y)

    def button(self, which: str, down: bool, times: int) -> None:
        Q = self.Q
        kinds = {"left": (Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp, Q.kCGMouseButtonLeft),
                 "right": (Q.kCGEventRightMouseDown, Q.kCGEventRightMouseUp, Q.kCGMouseButtonRight)}
        down_kind, up_kind, button = kinds.get(which, kinds["left"])
        x, y = getattr(self, "last", None) or self.where()
        event = Q.CGEventCreateMouseEvent(None, down_kind if down else up_kind, self._point(x, y), button)
        Q.CGEventSetIntegerValueField(event, Q.kCGMouseEventClickState, max(1, times))
        self.down = which if down else None
        self._post(event)

    def scroll(self, notches: int) -> None:
        self._post(self.Q.CGEventCreateScrollWheelEvent(None, self.Q.kCGScrollEventUnitLine, 1, notches * 3))

    def letter(self, ch: str) -> None:
        if ch == "\n":
            self.combo(["enter"])
            return
        for down in (True, False):
            event = self.Q.CGEventCreateKeyboardEvent(None, 0, down)
            self.Q.CGEventKeyboardSetUnicodeString(event, len(ch.encode("utf-16-le")) // 2, ch)
            self._post(event)

    def combo(self, keys: List[str]) -> bool:
        Q = self.Q
        masks = {"cmd": Q.kCGEventFlagMaskCommand, "meta": Q.kCGEventFlagMaskCommand, "win": Q.kCGEventFlagMaskCommand,
                 "ctrl": Q.kCGEventFlagMaskControl, "control": Q.kCGEventFlagMaskControl,
                 "shift": Q.kCGEventFlagMaskShift, "alt": Q.kCGEventFlagMaskAlternate, "option": Q.kCGEventFlagMaskAlternate}
        flags = 0
        main = None
        for one in keys:
            if one in masks:
                flags |= masks[one]
            elif one in _MAC_KEYS:
                main = _MAC_KEYS[one]
            else:
                return False
        if main is None:
            return False
        for down in (True, False):
            self._post(Q.CGEventCreateKeyboardEvent(None, main, down), flags)
            time.sleep(0.04)
        return True


class _MacWatch:
    """THE MOUSE OR THE KEYBOARD (the user, 2026-11-18: "还是移动鼠标或者键盘都算取消screen 操作（这样键盘权限没有也可以)"):
    listening event taps - one of the mouse, one of the keys, apart, so the mouse's stands where the keys' needs a
    permission not given (Input Monitoring) - an event without our mark is the user's (a move past POINTER_MOST); with
    no tap at all, the pointer watched: moved where we did not put it, the user moved it."""

    def __init__(self, seen: Callable[[], None], hands: Hands) -> None:
        self.seen, self.hands = seen, hands
        self.loop, self.thread, self.polling = None, None, False
        self.ready = threading.Event()
        self.ok = False

    def start(self) -> bool:
        self.thread = threading.Thread(target=self._run, name="screen-watch", daemon=True)
        self.thread.start()
        self.ready.wait(3)
        return self.ok

    def _run(self) -> None:
        import Quartz as Q
        pointer = _Pointer()
        moves = (Q.kCGEventMouseMoved, Q.kCGEventLeftMouseDragged, Q.kCGEventRightMouseDragged)

        def tapped(_proxy: Any, kind: int, event: Any, _refcon: Any) -> Any:
            try:
                ours = Q.CGEventGetIntegerValueField(event, Q.kCGEventSourceUserData) == MARK
                if kind in moves:
                    at = Q.CGEventGetLocation(event)
                    if pointer.moved(at.x, at.y, ours):
                        self.seen()
                elif not ours:
                    self.seen()
            except Exception:
                pass
            return event

        def tap_of(kinds: Sequence[int]) -> Any:
            mask = 0
            for kind in kinds:
                mask |= Q.CGEventMaskBit(kind)
            return Q.CGEventTapCreate(Q.kCGSessionEventTap, Q.kCGHeadInsertEventTap, Q.kCGEventTapOptionListenOnly,
                                      mask, tapped, None)
        taps = [tap for tap in (tap_of([*moves, Q.kCGEventLeftMouseDown, Q.kCGEventRightMouseDown, Q.kCGEventScrollWheel]),
                                tap_of([Q.kCGEventKeyDown])) if tap is not None]
        if not taps:
            # No tap at all (no permission): the pointer watched - moved where we did not put it, the user moved it.
            log.info("no event tap on this Mac: the user's pointer is watched instead")
            self.ok, self.polling = True, True
            self.ready.set()
            driver = _driver()
            first = driver.where()                         # where it stood before the agent moved it
            while self.polling:
                at, put = driver.where(), self.hands.at
                put = put if put is not None else first
                if put is not None and at is not None and abs(at[0] - put[0]) + abs(at[1] - put[1]) > 0.01:
                    self.seen()
                    return
                time.sleep(0.1)
            return
        self.loop = Q.CFRunLoopGetCurrent()
        for tap in taps:
            Q.CFRunLoopAddSource(self.loop, Q.CFMachPortCreateRunLoopSource(None, tap, 0), Q.kCFRunLoopCommonModes)
            Q.CGEventTapEnable(tap, True)
        self.ok = True
        self.ready.set()
        Q.CFRunLoopRun()

    def stop(self) -> None:
        self.polling = False
        if self.loop is not None:
            import Quartz as Q
            Q.CFRunLoopStop(self.loop)


_DRIVER: Any = None


def _driver() -> Any:
    global _DRIVER
    if _DRIVER is None:
        _DRIVER = _Windows() if sys.platform == "win32" else _Mac()
    return _DRIVER


def supported() -> bool:
    """Whether the agent can have the screen here: Windows and the Mac."""
    if sys.platform == "win32":
        return True
    if sys.platform == "darwin":
        try:
            import Quartz  # noqa: F401
            return True
        except Exception:
            return False
    return False


__all__ = ["Hands", "picture", "supported", "MARK"]
