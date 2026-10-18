"""THE MACOS PERMISSIONS, on one page.

macOS asks for each kind of access on its own, and nothing - short of a
managed Mac's profile - grants them together. What can be done is to ask
for them in one sitting rather than one at a time over the first day,
and to ask for Full Disk Access first: it covers the Desktop, Documents
and Downloads folders and Safari's history, so the three questions macOS
would otherwise put about those folders never come.

Each one is LOOKED AT without asking (`status`); asking, and opening the
very pane of System Settings where it is switched on, is the user's click
(`open_pane`). Nothing here writes anywhere: System Settings is opened,
and macOS keeps its own record of what was allowed.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..common import children

log = logging.getLogger(__name__)

#: In the order they are best given: Full Disk Access first, since it
#: spares the folder questions.
ORDER = ("full_disk", "accessibility", "automation", "screen")

#: Each one's pane in System Settings > Privacy & Security.
PANES = {"full_disk": "Privacy_AllFiles",
         "accessibility": "Privacy_Accessibility",
         "automation": "Privacy_Automation",
         "screen": "Privacy_ScreenCapture"}

_SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?"


def applies() -> bool:
    return sys.platform == "darwin"


def _framework(name: str) -> Any:
    return ctypes.cdll.LoadLibrary(f"/System/Library/Frameworks/{name}.framework/{name}")


def _accessibility() -> Optional[bool]:
    """Reading other applications' windows. Asked of this process: a child
    such as osascript is answered for by the application that started it."""
    try:
        trusted = _framework("ApplicationServices").AXIsProcessTrusted
        trusted.restype = ctypes.c_bool
        return bool(trusted())
    except Exception:  # pragma: no cover - only on a Mac
        log.debug("could not ask whether accessibility is allowed", exc_info=True)
        return None


def _screen() -> Optional[bool]:
    try:
        preflight = _framework("CoreGraphics").CGPreflightScreenCaptureAccess
        preflight.restype = ctypes.c_bool
        return bool(preflight())
    except Exception:  # pragma: no cover - only on a Mac (10.15 and later)
        log.debug("could not ask whether screen recording is allowed", exc_info=True)
        return None


def _full_disk() -> Optional[bool]:
    """The privacy database itself is readable only with Full Disk Access -
    which is the usual way of telling, since macOS offers no question for
    this one."""
    probe = Path.home() / "Library" / "Application Support" / "com.apple.TCC" / "TCC.db"
    try:
        with open(probe, "rb") as handle:
            handle.read(1)
        return True
    except PermissionError:
        return False
    except OSError:
        return None


_SYSTEM_EVENTS = ('tell application "System Events" to return name of '
                  'first process whose frontmost is true')


def _automation() -> Optional[bool]:
    """Sending System Events a question. The first time, this is what makes
    macOS ask - which is the point of asking it from here."""
    try:
        done = children.run_short(["osascript", "-e", _SYSTEM_EVENTS],
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=10)
    except Exception:  # pragma: no cover - osascript is part of macOS
        return None
    if done.returncode == 0:
        return True
    said = (done.stderr or "").lower()
    if "-1743" in said or "not authorized" in said or "not allowed" in said:
        return False
    return None


_CHECKS = {"full_disk": _full_disk, "accessibility": _accessibility,
           "automation": _automation, "screen": _screen}


def needed(config: Any) -> Dict[str, bool]:
    """Which of them the switches that are on need."""
    def on(key: str) -> bool:
        return bool(config.get(key, True))
    front = on("listener_application_enabled")
    return {"full_disk": on("listener_filesystem_enabled") or on("listener_browser_enabled"),
            "accessibility": front,
            "automation": front,
            "screen": front and on("listener_screenshots_enabled")}


def status(config: Any) -> Dict[str, Any]:
    """Each permission: whether it is needed and whether macOS allows it
    (None where that cannot be told)."""
    if not applies():
        return {"applies": False, "items": []}
    wanted = needed(config)
    items: List[Dict[str, Any]] = []
    for key in ORDER:
        items.append({"key": key, "needed": wanted[key], "granted": _CHECKS[key]()})
    missing = [one["key"] for one in items if one["needed"] and one["granted"] is False]
    return {"applies": True, "items": items, "missing": missing}


def _put_on_the_list(key: str) -> None:
    """Where macOS lets a program ask, it is asked: that also puts the
    program on the pane's list, so the user only has to switch it on.
    Full Disk Access has no such question - the user adds it with +."""
    try:
        if key == "screen":
            _framework("CoreGraphics").CGRequestScreenCaptureAccess()
        elif key == "accessibility":
            core = _framework("CoreFoundation")
            services = _framework("ApplicationServices")
            prompt = ctypes.c_void_p.in_dll(services, "kAXTrustedCheckOptionPrompt")
            true = ctypes.c_void_p.in_dll(core, "kCFBooleanTrue")
            core.CFDictionaryCreate.restype = ctypes.c_void_p
            core.CFDictionaryCreate.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(ctypes.c_void_p), ctypes.c_long,
                ctypes.c_void_p, ctypes.c_void_p]
            keys = (ctypes.c_void_p * 1)(prompt.value)
            values = (ctypes.c_void_p * 1)(true.value)
            options = core.CFDictionaryCreate(None, keys, values, 1, None, None)
            ask = services.AXIsProcessTrustedWithOptions
            ask.argtypes = [ctypes.c_void_p]
            ask.restype = ctypes.c_bool
            ask(options)
            core.CFRelease.argtypes = [ctypes.c_void_p]
            core.CFRelease(options)
        elif key == "automation":
            _automation()
    except Exception:  # pragma: no cover - only on a Mac
        log.debug("could not ask macOS for %s", key, exc_info=True)


def open_pane(key: str) -> bool:
    """Ask for one, where macOS lets a program ask, and open its pane."""
    if not applies() or key not in PANES:
        return False
    _put_on_the_list(key)
    try:
        done = children.run_short(["open", _SETTINGS_URL + PANES[key]],
                                  capture_output=True, text=True, timeout=15)
        return done.returncode == 0
    except Exception:  # pragma: no cover - `open` is part of macOS
        log.debug("could not open System Settings", exc_info=True)
        return False
