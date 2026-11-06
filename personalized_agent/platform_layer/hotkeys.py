"""KEYBOARD SHORTCUTS that work in every program (Settings > Shortcuts).

Two today, each on the keys the user chose, else its own:

* DELEGATE WORK, Ctrl+Alt+D - the Delegate work box, empty, on the screen,
  for the user to write what they want done;
* CHAT BUBBLE, Ctrl+Alt+C - the chat bubble opened beside the avatar, and the
  same keys minimise it to its bar, what is half written kept.

Nothing is read from the program in front.

Windows registers the keys with the system (RegisterHotKey): a combination
another program already holds is refused, and said so, rather than taken
from it. A Mac does the same (RegisterEventHotKey, which asks for no permission), on the main
thread, and calls the keys its own names (`names`): what is kept stays Ctrl, Alt, Shift and Win,
Win being Command. Elsewhere this says it is not available here, and why.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

#: Each shortcut: its setting, its default, what it does.
SHORTCUTS: List[Dict[str, str]] = [
    {"key": "delegate_work", "setting": "shortcut_delegate_work",
     "default": "Ctrl+Alt+D", "name": "Delegate work",
     "what": "Opens an empty Delegate work box, from any program, for you to say what "
             "you want done."},
    {"key": "open_chat", "setting": "shortcut_open_chat",
     "default": "Ctrl+Alt+C", "name": "Open or minimise the chat bubble",
     "what": "Opens the chat bubble beside the avatar, from any program; pressed again, "
             "minimises it, keeping what you have written."},
]
BY_KEY = {one["key"]: one for one in SHORTCUTS}

#: The modifiers, as written, and as Windows counts them.
MODIFIERS = {"ctrl": ("Ctrl", 0x0002), "control": ("Ctrl", 0x0002),
             "alt": ("Alt", 0x0001), "option": ("Alt", 0x0001),
             "shift": ("Shift", 0x0004),
             "win": ("Win", 0x0008), "meta": ("Win", 0x0008), "super": ("Win", 0x0008),
             "cmd": ("Win", 0x0008), "command": ("Win", 0x0008)}
MOD_ORDER = ("Ctrl", "Alt", "Shift", "Win")
#: WHAT A MAC CALLS THEM (the user, 2026-11-05: "苹果版本的command 什么的，要显示的是不一样").
MAC_NAMES = {"Ctrl": "\u2303 Control", "Alt": "\u2325 Option", "Shift": "\u21e7 Shift",
             "Win": "\u2318 Command"}
MOD_NOREPEAT = 0x4000
#: The keys a shortcut may end in, and Windows' code for each.
NAMED_KEYS = {"space": ("Space", 0x20), "insert": ("Insert", 0x2D),
              "delete": ("Delete", 0x2E), "home": ("Home", 0x24), "end": ("End", 0x23),
              "pageup": ("PageUp", 0x21), "pagedown": ("PageDown", 0x22),
              "left": ("Left", 0x25), "up": ("Up", 0x26), "right": ("Right", 0x27),
              "down": ("Down", 0x28)}


#: The modifiers as a Mac counts them, by Windows' count; and a Mac's code for each key, by Windows'.
MAC_MODIFIERS = {0x0001: 0x0800, 0x0002: 0x1000, 0x0004: 0x0200, 0x0008: 0x0100}
MAC_KEYS = {**{ord(one): code for one, code in zip(
    "ASDFHGZXCVBQWERYT123465978" "0OUIPLJKNM",
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 25, 26, 28,
     29, 31, 32, 34, 35, 37, 38, 40, 45, 46))},
            0x20: 49, 0x2E: 117, 0x24: 115, 0x23: 119, 0x21: 116, 0x22: 121,
            0x25: 123, 0x27: 124, 0x28: 125, 0x26: 126,
            **{0x6F + n: code for n, code in enumerate(
                (122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111, 105, 107, 113, 106, 64,
                 79, 80, 90), start=1)}}


def names() -> Dict[str, str]:
    """What the keys are shown as here, where that is not what is kept."""
    return dict(MAC_NAMES) if sys.platform == "darwin" else {}


def _said(windows: str, mac: str) -> str:
    return mac if sys.platform == "darwin" else windows


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
            raise ShortcutError("a shortcut has one key besides " + _said(
                "Ctrl, Alt, Shift and Win", "Control, Option, Shift and Command"))
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
        raise ShortcutError("add a key to " + _said("Ctrl, Alt, Shift or Win",
                                                    "Control, Option, Shift or Command"))
    if not ({"Ctrl", "Alt", "Win"} & set(mods)):
        raise ShortcutError("a shortcut needs " + _said("Ctrl, Alt or Win",
                                                        "Control, Option or Command")
                            + ", so that it is not typing in the program in front")
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
        return (True, "") if _carbon() is not None else (False, "not available on this Mac")
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


def _four(word: str) -> int:
    """A Mac's four-letter code."""
    return int.from_bytes(word.encode("ascii"), "big")


class _MacEventType(ctypes.Structure):
    _fields_ = [("kind_of", ctypes.c_uint32), ("kind", ctypes.c_uint32)]


class _MacHotkeyId(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


_MAC: Dict[str, Any] = {"loaded": False, "carbon": None, "pressed": {}, "next": 1}


def _carbon() -> Any:
    """The system's library for keys held everywhere - None where it cannot be had."""
    if not _MAC["loaded"]:
        _MAC["loaded"] = True
        try:
            carbon = ctypes.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
            carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
            carbon.InstallEventHandler.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                                                   ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
            carbon.RegisterEventHotKey.argtypes = [ctypes.c_uint32, ctypes.c_uint32, _MacHotkeyId,
                                                   ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
            carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
            carbon.GetEventParameter.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                                 ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                                 ctypes.c_void_p]
            _MAC["carbon"] = carbon
        except (OSError, AttributeError):
            log.info("no held shortcuts on this Mac", exc_info=True)
    return _MAC["carbon"]


def _mac_pressed(_call: Any, event: Any, _data: Any) -> int:
    said = _MacHotkeyId()
    # Its direct object ("----"), of the type "hkid": asked by that name it is not found (-9870),
    # and a press came and did nothing (2026-11-05).
    code = _MAC["carbon"].GetEventParameter(event, _four("----"), _four("hkid"), None,
                                            ctypes.sizeof(said), None, ctypes.byref(said))
    action = _MAC["pressed"].get(said.id) if code == 0 else None
    if action is not None:
        try:
            action()
        except Exception:
            log.exception("the shortcut's action failed")
    return 0


def _mac_listening() -> None:
    """Told of a press, from now on: once, on the main thread."""
    if "handler" in _MAC:
        return
    carbon = _carbon()
    handler = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.c_void_p)(_mac_pressed)
    kind = _MacEventType(_four("keyb"), 5)                # kEventHotKeyPressed
    made = ctypes.c_void_p()
    code = carbon.InstallEventHandler(carbon.GetApplicationEventTarget(),
                                      ctypes.cast(handler, ctypes.c_void_p), 1,
                                      ctypes.byref(kind), None, ctypes.byref(made))
    if code:
        raise OSError(f"the system said {code}")
    _MAC["handler"] = handler                             # kept: the system calls it


class _MacHotkey:
    """One combination, held with the system. All of it on the main thread (`on_main`)."""

    def __init__(self, flags: int, code: int, on_press: Callable[[], None],
                 on_main: Callable[[Callable[[], None]], None]) -> None:
        self.flags, self.code, self.on_press, self.on_main = flags, code, on_press, on_main
        self.ready = threading.Event()
        self.held = False
        self.why = ""
        self._ref = ctypes.c_void_p()
        self._id = 0

    def start(self) -> None:
        self.on_main(self._hold)

    def _hold(self) -> None:
        try:
            key = MAC_KEYS.get(self.code)
            if key is None:
                self.why = "a Mac has no such key"
                return
            _mac_listening()
            carbon = _carbon()
            modifiers = 0
            for windows, mac in MAC_MODIFIERS.items():
                if self.flags & windows:
                    modifiers |= mac
            self._id = _MAC["next"]
            _MAC["next"] += 1
            _MAC["pressed"][self._id] = self.on_press
            code = carbon.RegisterEventHotKey(key, modifiers, _MacHotkeyId(_four("WMln"), self._id),
                                              carbon.GetApplicationEventTarget(), 0,
                                              ctypes.byref(self._ref))
            self.held = code == 0
            if not self.held:
                _MAC["pressed"].pop(self._id, None)
        except Exception:
            log.exception("the shortcut could not be held")
            self.why = "the keys could not be held"
        finally:
            self.ready.set()

    def stop(self) -> None:
        self.on_main(self._let_go)

    def _let_go(self) -> None:
        _MAC["pressed"].pop(self._id, None)
        if self.held:
            _carbon().UnregisterEventHotKey(self._ref)
            self.held = False

    def join(self, timeout: Optional[float] = None) -> None:
        """Nothing to wait for: it has no thread."""


class Shortcuts:
    """The shortcuts the user has, held while the agent runs. `on_main`: how something is done
    on the main thread, which a Mac asks of keys held."""

    def __init__(self, config, actions: Dict[str, Callable[[], None]],
                 on_main: Optional[Callable[[Callable[[], None]], None]] = None) -> None:
        self.config = config
        self.actions = actions
        self.on_main = on_main or (lambda work: work())
        self._held: Dict[str, Any] = {}
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
            held = (_MacHotkey(flags, code, action, self.on_main) if sys.platform == "darwin"
                    else _WindowsHotkey(flags, code, action))
            held.start()
            held.ready.wait(3)
            if not held.held:
                self._said[key] = (getattr(held, "why", "")
                                   or "another program already uses these keys")
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
        return {"available": can, "why": why, "names": names(), "shortcuts": [
            {**one, "combo": self.combo_of(one["key"]),
             "held": one["key"] in self._held,
             "problem": self._said.get(one["key"], "") if one["key"] not in self._held
             and self.combo_of(one["key"]) else ""}
            for one in SHORTCUTS]}


def describe_without(config) -> Dict[str, object]:
    """What is set, where nothing holds the shortcuts (no desktop running)."""
    return {"available": False, "names": names(),
            "why": "shortcuts work while the agent runs with its avatar or its icon",
            "shortcuts": [{**one, "combo": (one["default"] if config.get(one["setting"]) is None
                                            else str(config.get(one["setting"]))),
                           "held": False, "problem": ""} for one in SHORTCUTS]}


__all__ = ["SHORTCUTS", "Shortcuts", "ShortcutError", "parse", "available", "names",
           "describe_without"]
