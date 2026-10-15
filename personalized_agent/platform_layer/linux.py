"""Linux specific implementations of the platform interfaces."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

from ..common import children
from .base import (
    ActiveWindow,
    Platform,
    PlatformActivityProvider,
    PlatformCapabilities,
    PlatformNotificationProvider,
    PsutilProcessProvider,
    IsolatedSandbox,
    which,
)

log = logging.getLogger(__name__)

try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None


class LinuxActivityProvider(PlatformActivityProvider):
    """Active window through xdotool / xprop when an X session is present."""

    def __init__(self) -> None:
        self._xdotool = which("xdotool")
        self._xprop = which("xprop")

    @property
    def available(self) -> bool:
        return bool(self._xdotool or self._xprop)

    def _xdotool_window(self) -> ActiveWindow:
        try:
            pid_out = children.run_short(
                [self._xdotool, "getactivewindow", "getwindowpid"],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=5,
            )
            name_out = children.run_short(
                [self._xdotool, "getactivewindow", "getwindowname"],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=5,
            )
            pid = int(pid_out.stdout.strip()) if pid_out.stdout.strip().isdigit() else None
            title = name_out.stdout.strip() or None
            app = None
            exe = None
            if pid and psutil is not None:
                try:
                    proc = psutil.Process(pid)
                    app = proc.name()
                    exe = proc.exe()
                except Exception:
                    pass
            return ActiveWindow(application=app, title=title, pid=pid, executable=exe)
        except Exception:
            log.debug("xdotool query failed", exc_info=True)
            return ActiveWindow()

    def get_active_window(self) -> ActiveWindow:
        if self._xdotool:
            return self._xdotool_window()
        return ActiveWindow()

    def front_window_image(self) -> Optional[Any]:
        """The active window by xdotool's geometry, grabbed from the X
        display. Wayland does not allow it, and nothing is taken there."""
        if not self._xdotool:
            return None
        try:
            from PIL import ImageGrab
            done = children.run_short(
                [self._xdotool, "getactivewindow", "getwindowgeometry", "--shell"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=5)
            said = dict(line.split("=", 1) for line in done.stdout.splitlines()
                        if "=" in line)
            x, y = int(said["X"]), int(said["Y"])
            w, h = int(said["WIDTH"]), int(said["HEIGHT"])
            if w < 120 or h < 90:
                return None
            return ImageGrab.grab(bbox=(x, y, x + w, y + h))
        except Exception:
            log.debug("could not take the window in front", exc_info=True)
            return None

    def get_active_application(self) -> Optional[str]:
        return self.get_active_window().application

    def get_browser_history_paths(self) -> Dict[str, Path]:
        home = Path.home()
        candidates = {
            "Chrome": home / ".config/google-chrome/Default/History",
            "Chromium": home / ".config/chromium/Default/History",
            "Brave": home / ".config/BraveSoftware/Brave-Browser/Default/History",
        }
        firefox_root = home / ".mozilla/firefox"
        if firefox_root.exists():
            for profile in firefox_root.iterdir():
                db = profile / "places.sqlite"
                if db.exists():
                    candidates["Firefox"] = db
                    break
        return {name: path for name, path in candidates.items() if path.exists()}

    def files_the_user_opened(self, since: float) -> Dict[str, float]:
        """The freedesktop recent list, and KDE's own beside it.

        ``recently-used.xbel`` is written when a file goes through an open
        dialog or a file manager; KDE keeps a directory of small desktop
        files for the same purpose. Both record the user's opening and
        neither records a program's reading.
        """
        from .recent_files import linux_recent

        return linux_recent(since)


class LinuxNotificationProvider(PlatformNotificationProvider):
    def __init__(self) -> None:
        self._notify_send = which("notify-send")

    @property
    def available(self) -> bool:
        return self._notify_send is not None

    def send_notification(self, title: str, message: str) -> bool:
        if not self._notify_send:
            return False
        try:
            completed = children.run_short(
                [self._notify_send, title, message], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=10
            )
            return completed.returncode == 0
        except Exception:
            return False


class LinuxSandbox(IsolatedSandbox):
    """bubblewrap where it can start a sandbox here, else Landlock (kernel
    5.13+), else the in-process guard alone - see runtime/isolation.py."""


class LinuxPlatform(Platform):
    name = "linux"

    def __init__(self) -> None:
        super().__init__()
        self.activity = LinuxActivityProvider()
        self.notifications = LinuxNotificationProvider()
        self.processes = PsutilProcessProvider()
        self.sandbox = LinuxSandbox()

    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            active_application=self.activity.available,
            active_window_title=self.activity.available,
            filesystem_events=True,
            browser_history=bool(self.activity.get_browser_history_paths()),
            process_isolation=self.sandbox.isolation_name in ("bubblewrap", "landlock"),
            native_notifications=self.notifications.available,
            process_info=psutil is not None,
        )
