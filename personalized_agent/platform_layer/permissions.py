"""WHAT THE OPERATING SYSTEM MUST ALLOW, beside each switch that needs it -
on every platform.

A Privacy switch that is on does nothing if the system does not let the
agent do what it asks: on a Mac each kind of access is granted on its own,
in System Settings; on Windows a program's notifications can be turned off
for it, or for every program; on Linux a Wayland session lets no program
see which window is in front, nor take a picture of it.

Each permission is LOOKED AT without asking (`status`) and said beside the
switches that need it - allowed, not allowed, or that it cannot be told -
with a link to the very place it is switched on, where the system has one
(`open_place`). Nothing here changes a setting: the system's own settings
are opened, and the user decides there.

macOS: Full Disk Access, Accessibility, Automation (System Events) and
Screen Recording, as macos_permissions.py has always looked at them; and
Notifications, which macOS lets no program read - only open.
Windows: Notifications - every program's (Settings > System > Notifications)
and this one's, read from the user's own registry, never written.
Linux: the session - X11 lets the window in front be read and pictured,
Wayland does not, and that is chosen at sign-in, not in a setting.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Callable, Dict, List, Optional

from ..common import children

log = logging.getLogger(__name__)

#: Each permission: the switches it serves (a switch is on unless set off),
#: and where it is switched on. Per platform.
MAC = {
    "full_disk": {"switches": ["listener_filesystem_enabled", "listener_browser_enabled"]},
    "accessibility": {"switches": ["listener_application_enabled",
                                   "listener_screen_enabled"]},
    "automation": {"switches": ["listener_application_enabled"]},
    "screen": {"switches": ["listener_screenshots_enabled"]},
    "notifications": {"switches": ["notifications_enabled"],
                      "url": "x-apple.systempreferences:com.apple.preference.notifications"},
}
WINDOWS = {
    "notifications": {"switches": ["notifications_enabled"], "url": "ms-settings:notifications"},
}
LINUX = {
    "x11_session": {"switches": ["listener_application_enabled", "listener_screen_enabled",
                                 "listener_screenshots_enabled"]},
}


def platform() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def catalog() -> Dict[str, Dict[str, Any]]:
    return {"macos": MAC, "windows": WINDOWS, "linux": LINUX}[platform()]


# -- what each platform says -------------------------------------------------------
def _windows_notifications() -> Optional[bool]:
    """Off for every program (the toasts switch) or for this one (its own
    key, under the name its notifications are shown with). Read only."""
    try:
        import winreg
        from ..version import APP_NAME
    except Exception:                         # pragma: no cover - Windows only
        return None

    def read(path: str, name: str) -> Optional[int]:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                return int(winreg.QueryValueEx(key, name)[0])
        except OSError:
            return None
    base = r"Software\Microsoft\Windows\CurrentVersion"
    if read(base + r"\PushNotifications", "ToastEnabled") == 0:
        return False
    if read(base + r"\Notifications\Settings" + "\\" + APP_NAME, "Enabled") == 0:
        return False
    return True


def _linux_session() -> Optional[bool]:
    kind = (os.environ.get("XDG_SESSION_TYPE") or "").lower()
    if kind == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
        return False
    if kind == "x11" or os.environ.get("DISPLAY"):
        return True
    return None


def _checks() -> Dict[str, Callable[[], Optional[bool]]]:
    if platform() == "macos":
        from . import macos_permissions as mac
        return {"full_disk": mac._full_disk, "accessibility": mac._accessibility,
                "automation": mac._automation, "screen": mac._screen,
                "notifications": lambda: None}
    if platform() == "windows":
        return {"notifications": _windows_notifications}
    return {"x11_session": _linux_session}


def _on(config: Any, key: str) -> bool:
    return bool(config.get(key, True))


def status(config: Any) -> Dict[str, Any]:
    """Every permission this platform has: the switches it serves, whether
    one that is on needs it, whether the system allows it (None: cannot be
    told), and whether its place can be opened; and, by switch, which
    permissions stand beside it."""
    checks = _checks()
    items: List[Dict[str, Any]] = []
    by_switch: Dict[str, List[str]] = {}
    for key, said in catalog().items():
        switches = list(said["switches"])
        needed = any(_on(config, one) for one in switches)
        if key == "screen":
            # A picture of the window in front: only while that is watched.
            needed = _on(config, "listener_screenshots_enabled") and _on(
                config, "listener_application_enabled")
        try:
            granted = checks[key]() if needed else None
        except Exception:                      # pragma: no cover - said as unknown
            log.debug("could not look at %s", key, exc_info=True)
            granted = None
        items.append({"key": key, "switches": switches, "needed": needed,
                      "granted": granted, "can_open": can_open(key)})
        for one in switches:
            by_switch.setdefault(one, []).append(key)
    missing = [one["key"] for one in items if one["needed"] and one["granted"] is False]
    return {"platform": platform(), "items": items, "by_switch": by_switch,
            "missing": missing}


def can_open(key: str) -> bool:
    if platform() == "macos":
        from . import macos_permissions as mac
        return key in mac.PANES or bool(MAC.get(key, {}).get("url"))
    return bool(catalog().get(key, {}).get("url"))


def open_place(key: str) -> bool:
    """The place in the system's settings where it is switched on, opened -
    and, on a Mac, asked for first where macOS lets a program ask, so the
    agent is already on the pane's list."""
    if key not in catalog() or not can_open(key):
        return False
    if platform() == "macos":
        from . import macos_permissions as mac
        if key in mac.PANES:
            return mac.open_pane(key)
        command = ["open", MAC[key]["url"]]
    elif platform() == "windows":
        command = ["cmd", "/c", "start", "", catalog()[key]["url"]]
    else:                                      # pragma: no cover - nothing to open
        return False
    try:
        done = children.run_short(command, capture_output=True, text=True, timeout=15)
        return done.returncode == 0
    except Exception:                          # pragma: no cover - the system's own
        log.debug("could not open the system's settings for %s", key, exc_info=True)
        return False


__all__ = ["status", "open_place", "catalog", "platform"]
