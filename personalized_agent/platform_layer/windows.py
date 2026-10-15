"""Windows specific implementations of the platform interfaces."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from ..common import children
from .base import (
    ActiveWindow,
    Platform,
    PlatformActivityProvider,
    PlatformCapabilities,
    PlatformNotificationProvider,
    PsutilProcessProvider,
    IsolatedSandbox,
)

log = logging.getLogger(__name__)

try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None


class WindowsActivityProvider(PlatformActivityProvider):
    def __init__(self) -> None:
        self._user32 = None
        self._kernel32 = None
        try:
            import ctypes

            self._ctypes = ctypes
            self._user32 = ctypes.windll.user32
            self._kernel32 = ctypes.windll.kernel32
        except Exception:  # pragma: no cover - non windows
            self._ctypes = None

    @property
    def available(self) -> bool:
        return self._user32 is not None

    def get_active_window(self) -> ActiveWindow:
        if not self.available:
            return ActiveWindow()
        ctypes = self._ctypes
        try:
            hwnd = self._user32.GetForegroundWindow()
            if not hwnd:
                return ActiveWindow()
            length = self._user32.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(length + 1)
            self._user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value or None

            pid = ctypes.c_ulong()
            self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pid_value = int(pid.value) or None

            app_name = None
            exe = None
            if pid_value and psutil is not None:
                try:
                    proc = psutil.Process(pid_value)
                    app_name = proc.name()
                    try:
                        exe = proc.exe()
                    except Exception:
                        exe = None
                except Exception:
                    app_name = None
            return ActiveWindow(application=app_name, title=title, pid=pid_value, executable=exe)
        except Exception:
            log.debug("failed to read foreground window", exc_info=True)
            return ActiveWindow()

    def get_active_application(self) -> Optional[str]:
        return self.get_active_window().application

    def front_window_image(self) -> Optional[Any]:
        """The window in front, cut from the screen at its frame.

        The frame is the compositor's (DWMWA_EXTENDED_FRAME_BOUNDS), in the
        physical pixels the grab is in; GetWindowRect would include the
        invisible resize border and, in a process that is not DPI aware,
        be scaled.
        """
        if not self.available:
            return None
        try:
            from PIL import ImageGrab
        except Exception:
            return None
        ctypes = self._ctypes
        try:
            from ctypes import wintypes
            hwnd = self._user32.GetForegroundWindow()
            if not hwnd or self._user32.IsIconic(hwnd):
                return None
            rect = wintypes.RECT()
            dwm = ctypes.windll.dwmapi
            if dwm.DwmGetWindowAttribute(wintypes.HWND(hwnd), 9, ctypes.byref(rect),
                                         ctypes.sizeof(rect)) != 0:
                if not self._user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    return None
            box = (rect.left, rect.top, rect.right, rect.bottom)
            if box[2] - box[0] < 120 or box[3] - box[1] < 90:
                return None
            return ImageGrab.grab(bbox=box, all_screens=True)
        except Exception:
            log.debug("could not take the window in front", exc_info=True)
            return None

    def seconds_since_input(self) -> Optional[float]:
        """From GetLastInputInfo, which is what Windows itself uses for idle."""
        if self._user32 is None or self._kernel32 is None:
            return None
        import ctypes

        class LastInput(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_ulong)]

        info = LastInput()
        info.cbSize = ctypes.sizeof(LastInput)
        try:
            if not self._user32.GetLastInputInfo(ctypes.byref(info)):
                return None
            now = self._kernel32.GetTickCount64()
        except Exception:                # pragma: no cover - defensive
            return None
        # Both are milliseconds since boot; GetLastInputInfo's field is 32
        # bits and wraps after 49 days, so a negative difference means it
        # wrapped and the honest answer is that we cannot say.
        elapsed = (now - info.dwTime) / 1000.0
        return elapsed if elapsed >= 0 else None

    def get_browser_history_paths(self) -> Dict[str, Path]:
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        roaming = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
        candidates = {
            "Chrome": local / "Google/Chrome/User Data/Default/History",
            "Edge": local / "Microsoft/Edge/User Data/Default/History",
            "Brave": local / "BraveSoftware/Brave-Browser/User Data/Default/History",
            "Vivaldi": local / "Vivaldi/User Data/Default/History",
        }
        firefox_root = roaming / "Mozilla/Firefox/Profiles"
        if firefox_root.exists():
            for profile in firefox_root.iterdir():
                db = profile / "places.sqlite"
                if db.exists():
                    candidates["Firefox"] = db
                    break
        return {name: path for name, path in candidates.items() if path.exists()}

    def text_in_front(self) -> Optional[Any]:
        """What the window in front is showing, through UI Automation.

        Measured on one machine: a terminal answers in 88 ms with 18,000
        characters, a browser in 334 ms with the one tab that is visible -
        the eleven open behind it are in the tree too and are left there.
        """
        from .window_text import text_in_front as read

        try:
            return read()
        except Exception as exc:                 # pragma: no cover - COM
            self._window_text_error = str(exc)[:200]
            return None

    @property
    def window_text_error(self) -> str:
        if getattr(self, "_window_text_error", ""):
            return self._window_text_error
        from .window_text import reader

        return reader().last_error

    def files_the_user_opened(self, since: float) -> Dict[str, float]:
        """Explorer writes a shortcut for every file the user opens.

        In one folder, named after the file and stamped with the time, with
        the path it points at inside it. Measured on one machine: reading
        three files with ``open()`` added nothing to it at all, while the
        files the user had opened that evening were each there to the second.

        It does not cover everything - an editor that does not register what
        it opens leaves no entry, and neither does a command line - which is
        why this is read as evidence *for* and never against.
        """
        from .recent_files import windows_recent

        return windows_recent(since)


class WindowsNotificationProvider(PlatformNotificationProvider):
    """Toast notification through the WinRT API exposed to PowerShell."""

    _SCRIPT = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
        " ContentType = WindowsRuntime] > $null;"
        "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        "$n = $t.GetElementsByTagName('text');"
        "$n.Item(0).AppendChild($t.CreateTextNode($env:PA_TITLE)) > $null;"
        "$n.Item(1).AppendChild($t.CreateTextNode($env:PA_BODY)) > $null;"
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($t);"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "'Personalized Agent').Show($toast);"
    )

    def __init__(self) -> None:
        self._powershell = None
        for candidate in ("powershell.exe", "pwsh.exe"):
            from .base import which

            found = which(candidate)
            if found:
                self._powershell = found
                break
        self._working = self._powershell is not None

    @property
    def available(self) -> bool:
        return self._working

    def send_notification(self, title: str, message: str) -> bool:
        if not self._powershell:
            return False
        env = dict(os.environ, PA_TITLE=title, PA_BODY=message)
        try:
            completed = children.run_short(
                [self._powershell, "-NoProfile", "-NonInteractive", "-Command", self._SCRIPT],
                env=env,
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=15,
            )
            if completed.returncode != 0:
                self._working = False
                log.debug("toast notification failed: %s", completed.stderr[:200])
                return False
            return True
        except Exception:
            self._working = False
            log.debug("toast notification raised", exc_info=True)
            return False


class WindowsSandbox(IsolatedSandbox):
    """An AppContainer: the child can open only what an ACL names it in -
    its own folders to write, the interpreter and the folders it reads to
    read - and has no network unless it is the package installer. See
    runtime/isolation.py; the in-process guard (runtime/guard.py) runs
    inside as well. Where an AppContainer cannot be started, the capability
    map says so instead of pretending.
    """

    isolation = "windows-subprocess"



def _can_read_windows() -> bool:
    """Whether this build can read the contents of a window at all."""
    try:
        from .window_text import reader

        return bool(reader().available)
    except Exception:              # pragma: no cover - optional dependency
        return False


class WindowsPlatform(Platform):
    name = "windows"

    def __init__(self) -> None:
        super().__init__()
        self.activity = WindowsActivityProvider()
        self.notifications = WindowsNotificationProvider()
        self.processes = PsutilProcessProvider()
        self.sandbox = WindowsSandbox()

    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            active_application=getattr(self.activity, "available", False),
            active_window_title=getattr(self.activity, "available", False),
            filesystem_events=True,
            browser_history=bool(self.activity.get_browser_history_paths()),
            process_isolation=self.sandbox.isolation_name == "appcontainer",
            native_notifications=getattr(self.notifications, "available", False),
            process_info=psutil is not None,
            # Asked rather than assumed: it needs comtypes, which is an
            # optional dependency, and a first call that has to build the
            # UI Automation client (section 19.2).
            window_text=_can_read_windows(),
        )
