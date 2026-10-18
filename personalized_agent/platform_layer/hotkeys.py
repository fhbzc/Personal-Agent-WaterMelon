"""KEYBOARD SHORTCUTS that work in every program (Settings > Shortcuts).

One today: DELEGATE WORK, Ctrl+Alt+D unless the user chose another - the
Delegate work box, empty, on the screen, for the user to write what they
want done. Nothing is read from the program in front.

Windows registers the keys with the system (RegisterHotKey): a combination
another program already holds is refused, and said so, rather than taken
from it. Elsewhere this says it is not available here, and why.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

#: Each shortcut: its setting, its default, what it does.
SHORTCUTS: List[Dict[str, str]] = [
    {"key": "delegate_work", "setting": "shortcut_delegate_work",
     "default": "Ctrl+Alt+D", "name": "Delegate work",
     "what": "Opens an empty Delegate work box, from any program, for you to say what "
             "you want done."},
]
BY_KEY = {one["key"]: one for one in SHORTCUTS}

#: The modifiers, as written, and as Windows counts them.
MODIFIERS = {"ctrl": ("Ctrl", 0x0002), "control": ("Ctrl", 0x0002),
             "alt": ("Alt", 0x0001), "option": ("Alt", 0x0001),
             "shift": ("Shift", 0x0004),
             "win": ("Win", 0x0008), "meta": ("Win", 0x0008), "super": ("Win", 0x0008),
             "cmd": ("Win", 0x0008)}
MOD_ORDER = ("Ctrl", "Alt", "Shift", "Win")
MOD_NOREPEAT = 0x4000
#: The keys a shortcut may end in, and Windows' code for each.
NAMED_KEYS = {"space": ("Space", 0x20), "insert": ("Insert", 0x2D),
              "delete": ("Delete", 0x2E), "home": ("Home", 0x24), "end": ("End", 0x23),
              "pageup": ("PageUp", 0x21), "pagedown": ("PageDown", 0x22),
              "left": ("Left", 0x25), "up": ("Up", 0x26), "right": ("Right", 0x27),
              "down": ("Down", 0x28)}


class ShortcutError(ValueError):
    """A combination that cannot be one - said in words the page shows."""


def parse(combo: str) -> Tuple[str, int, int]:
    """"ctrl + alt + d" -> ("Ctrl+Alt+D", modifiers, key code). A shortcut
    needs Ctrl, Alt or Win - Shift alone would take a capital letter from
    every program - and one key that is not a modifier."""
    parts = [one.strip() for one in str(combo or "").replace(" ", "").split("+") if one.strip()]
    if not parts:
        raise ShortcutError("press the keys of the shortcut")
    mods: Dict[str, int] = {}
    key: Optional[Tuple[str, int]] = None
    for part in parts:
        low = part.lower()
        if low in MODIFIERS:
            name, flag = MODIFIERS[low]
            mods[name] = flag
            continue
        if key is not None:
            raise ShortcutError("a shortcut has one key besides Ctrl, Alt, Shift and Win")
        if len(part) == 1 and part.isascii() and part.isalnum():
            key = (part.upper(), ord(part.upper()))
        elif low in NAMED_KEYS:
            key = NAMED_KEYS[low]
        elif low.startswith("f") and low[1:].isdigit() and 1 <= int(low[1:]) <= 24:
            key = (f"F{int(low[1:])}", 0x6F + int(low[1:]))
        else:
            raise ShortcutError(f"{part} cannot be the key of a shortcut: use a letter, a "
                                f"digit, F1-F24, Space or an arrow")
    if key is None:
        raise ShortcutError("add a key to Ctrl, Alt, Shift or Win")
    if not ({"Ctrl", "Alt", "Win"} & set(mods)):
        raise ShortcutError("a shortcut needs Ctrl, Alt or Win, so that it is not "
                            "typing in the program in front")
    written = "+".join([one for one in MOD_ORDER if one in mods] + [key[0]])
    flags = 0
    for flag in mods.values():
        flags |= flag
    return written, flags, key[1]


def available() -> Tuple[bool, str]:
    """Whether a shortcut can be held here - and, if not, why."""
    if sys.platform == "win32":
        return True, ""
    if sys.platform == "darwin":
        return False, "not available on a Mac yet"
    return False, "not available on Linux yet"


class _WindowsHotkey(threading.Thread):
    """One combination, held with the system for as long as this runs."""

    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012

    def __init__(self, flags: int, code: int, on_press: Callable[[], None]) -> None:
        super().__init__(name="shortcut", daemon=True)
        self.flags, self.code, self.on_press = flags, code, on_press
        self.ready = threading.Event()
        self.held = False
        self.thread_id = 0

    def run(self) -> None:
        import ctypes
        import ctypes.wintypes
        user32 = ctypes.windll.user32
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        self.held = bool(user32.RegisterHotKey(None, 1, self.flags | MOD_NOREPEAT, self.code))
        self.ready.set()
        if not self.held:
            return
        message = ctypes.wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == self.WM_HOTKEY:
                    try:
                        self.on_press()
                    except Exception:
                        log.exception("the shortcut's action failed")
        finally:
            user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if self.thread_id:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, self.WM_QUIT, 0, 0)


class Shortcuts:
    """The shortcuts the user has, held while the agent runs."""

    def __init__(self, config, actions: Dict[str, Callable[[], None]]) -> None:
        self.config = config
        self.actions = actions
        self._held: Dict[str, _WindowsHotkey] = {}
        self._said: Dict[str, str] = {}
        self._lock = threading.Lock()

    def combo_of(self, key: str) -> str:
        one = BY_KEY[key]
        value = self.config.get(one["setting"])
        return one["default"] if value is None else str(value)

    def start(self) -> None:
        for one in SHORTCUTS:
            self._hold(one["key"], self.combo_of(one["key"]))

    def stop(self) -> None:
        with self._lock:
            for held in self._held.values():
                held.stop()
            self._held.clear()

    def _hold(self, key: str, combo: str) -> bool:
        """`combo` held for `key` - in place of what it had. False, and why
        said, when it cannot be."""
        with self._lock:
            old = self._held.pop(key, None)
            if old is not None:
                old.stop()
                old.join(timeout=2)
            if not combo:
                self._said[key] = "off"
                return True
            can, why = available()
            if not can:
                self._said[key] = why
                return False
            try:
                _written, flags, code = parse(combo)
            except ShortcutError as exc:
                self._said[key] = str(exc)
                return False
            action = self.actions.get(key)
            if action is None:
                self._said[key] = "nothing to do it here"
                return False
            held = _WindowsHotkey(flags, code, action)
            held.start()
            held.ready.wait(3)
            if not held.held:
                self._said[key] = "another program already uses these keys"
                return False
            self._held[key] = held
            self._said[key] = ""
            return True

    def change(self, key: str, combo: str) -> Dict[str, object]:
        """The user's new keys - or "" for none. Held at once; kept only when
        they could be held, and the old ones held again when not."""
        if key not in BY_KEY:
            raise ShortcutError("no such shortcut")
        written = parse(combo)[0] if combo else ""
        before = self.combo_of(key)
        if not self._hold(key, written):
            why = self._said.get(key) or "the keys could not be held"
            self._hold(key, before)
            raise ShortcutError(f"{written}: {why}")
        self.config.set(BY_KEY[key]["setting"], written)
        return self.describe()

    def describe(self) -> Dict[str, object]:
        can, why = available()
        return {"available": can, "why": why, "shortcuts": [
            {**one, "combo": self.combo_of(one["key"]),
             "held": one["key"] in self._held,
             "problem": self._said.get(one["key"], "") if one["key"] not in self._held
             and self.combo_of(one["key"]) else ""}
            for one in SHORTCUTS]}


def describe_without(config) -> Dict[str, object]:
    """What is set, where nothing holds the shortcuts (no desktop running)."""
    return {"available": False,
            "why": "shortcuts work while the agent runs with its avatar or its icon",
            "shortcuts": [{**one, "combo": (one["default"] if config.get(one["setting"]) is None
                                            else str(config.get(one["setting"]))),
                           "held": False, "problem": ""} for one in SHORTCUTS]}


__all__ = ["SHORTCUTS", "Shortcuts", "ShortcutError", "parse", "available",
           "describe_without"]
