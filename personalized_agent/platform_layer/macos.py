"""macOS specific implementations of the platform interfaces."""

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

_FRONT_APP_SCRIPT = """
tell application "System Events"
    set frontProcess to first process whose frontmost is true
    set applicationName to name of frontProcess
    set processID to unix id of frontProcess
    try
        set windowTitle to name of front window of frontProcess
    on error
        set windowTitle to ""
    end try
    return applicationName & "|" & processID & "|" & windowTitle
end tell
"""


class MacActivityProvider(PlatformActivityProvider):
    def __init__(self) -> None:
        self._osascript = which("osascript")
        self.last_error: Optional[str] = None

    @property
    def available(self) -> bool:
        return self._osascript is not None

    def _run(self, script: str) -> Optional[str]:
        if not self._osascript:
            self.last_error = "macOS activity tracking requires the osascript command"
            return None
        try:
            completed = children.run_short(
                [self._osascript, "-e", script], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=5
            )
            if completed.returncode != 0:
                self.last_error = (completed.stderr.strip() or
                                   "macOS denied access to System Events")[:300]
                return None
            self.last_error = None
            return completed.stdout.strip()
        except Exception as exc:
            self.last_error = f"could not query macOS System Events: {exc}"
            log.debug("osascript failed", exc_info=True)
            return None

    def get_active_window(self) -> ActiveWindow:
        raw = self._run(_FRONT_APP_SCRIPT)
        if not raw:
            return ActiveWindow()
        parts = raw.split("|")
        name = parts[0].strip() or None
        pid = None
        if len(parts) > 1:
            try:
                pid = int(parts[1].strip())
            except ValueError:
                pid = None
        title = parts[2].strip() if len(parts) > 2 else None
        return ActiveWindow(application=name, title=title or None, pid=pid)

    def get_active_application(self) -> Optional[str]:
        return self.get_active_window().application

    def peek_foreground_application(self) -> Optional[str]:
        """Cheap focus hint for chat sampling; never reads window contents."""
        import objc
        from AppKit import NSWorkspace
        with objc.autorelease_pool():
            front = NSWorkspace.sharedWorkspace().frontmostApplication()
            return str(front.localizedName() or '') if front is not None else None

    def _text_reader(self):
        from .macos_window_text import MacWindowTextReader
        if not hasattr(self, "_mac_text_reader"):
            self._mac_text_reader = MacWindowTextReader()
        return self._mac_text_reader

    def text_in_front_allowed(self, allowed):
        # Section 19.2: the listener's policy is checked before AX or OCR,
        # then again before a result leaves the platform boundary.
        try:
            import objc
        except ImportError:
            return self._text_reader().read(allowed)

        # The listener is a Python worker thread, not AppKit's event loop.
        # Release native AX objects and OCR images after each reading.
        with objc.autorelease_pool():
            return self._text_reader().read(allowed)


    def text_in_front(self):
        return self.text_in_front_allowed(lambda front: True)

    @property
    def window_text_error(self) -> str:
        return self._text_reader().last_error

    def window_text_status(self):
        return self._text_reader().status()

    def seconds_since_input(self) -> Optional[float]:
        try:
            import Quartz
            return float(Quartz.CGEventSourceSecondsSinceLastEventType(
                Quartz.kCGEventSourceStateCombinedSessionState,
                Quartz.kCGAnyInputEventType))
        except (ImportError, AttributeError):
            return None

    def seconds_since_keyboard_input(self) -> Optional[float]:
        """Age only, no event monitor, key codes, characters or stored input."""
        try:
            import Quartz
            return float(Quartz.CGEventSourceSecondsSinceLastEventType(
                Quartz.kCGEventSourceStateCombinedSessionState, Quartz.kCGEventKeyDown))
        except (ImportError, AttributeError):
            return None

    def front_window_image(self) -> Optional[Any]:
        """The front window, by its position and size, with the system's own
        screencapture. Needs the Screen Recording permission; without it the
        picture is of the desktop only, and the provider's measures see an
        empty window and take nothing."""
        script = ('tell application "System Events" to tell (first process '
                  'whose frontmost is true) to get {position, size} of window 1')
        raw = self._run(script)
        if not raw:
            return None
        try:
            x, y, w, h = [int(float(part)) for part in raw.replace(" ", "").split(",")[:4]]
        except ValueError:
            return None
        if w < 120 or h < 90:
            return None
        import tempfile
        try:
            from PIL import Image
            with tempfile.TemporaryDirectory() as folder:
                out = f"{folder}/front.png"
                children.run_short(["screencapture", "-x", "-R", f"{x},{y},{w},{h}", out],
                                   capture_output=True, timeout=10)
                image = Image.open(out)
                image.load()
                return image
        except Exception:
            log.debug("could not take the window in front", exc_info=True)
            return None

    def get_browser_history_paths(self) -> Dict[str, Path]:
        home = Path.home()
        candidates = {
            "Chrome": home / "Library/Application Support/Google/Chrome/Default/History",
            "Edge": home / "Library/Application Support/Microsoft Edge/Default/History",
            "Brave": home / "Library/Application Support/BraveSoftware/Brave-Browser/Default/History",
            "Safari": home / "Library/Safari/History.db",
        }
        firefox_root = home / "Library/Application Support/Firefox/Profiles"
        try:
            if firefox_root.exists():
                for profile in firefox_root.iterdir():
                    db = profile / "places.sqlite"
                    if db.exists():
                        candidates["Firefox"] = db
                        break
        except OSError:
            # Capability discovery runs during startup. A denied or removed
            # profile is an unavailable source, not a failed application.
            log.debug("Firefox history directory is unavailable", exc_info=True)
        available = {}
        for name, path in candidates.items():
            try:
                if path.exists():
                    available[name] = path
            except OSError:
                log.debug("%s history path is unavailable", name, exc_info=True)
        return available

    def files_the_user_opened(self, since: float) -> Dict[str, float]:
        """Spotlight keeps ``kMDItemLastUsedDate``; LaunchServices sets it.

        The cleanest of the three: the attribute moves when the desktop hands
        a document to an application on the user's behalf, and a program
        reading the file does not move it. Spotlight is asked which files
        have one inside the window, and then what it says.
        """
        from .recent_files import macos_recent

        return macos_recent(since)


class MacNotificationProvider(PlatformNotificationProvider):
    def __init__(self) -> None:
        self._osascript = which("osascript")

    @property
    def available(self) -> bool:
        return self._osascript is not None

    def send_notification(self, title: str, message: str) -> bool:
        if not self._osascript:
            return False
        safe_title = title.replace('"', "'")
        safe_message = message.replace('"', "'")
        script = f'display notification "{safe_message}" with title "{safe_title}"'
        try:
            completed = children.run_short(
                [self._osascript, "-e", script], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=10
            )
            return completed.returncode == 0
        except Exception:
            return False


class MacSandbox(IsolatedSandbox):
    """Seatbelt (``sandbox-exec``): writes only in the folders given, the
    secrets unreadable, the network closed - see runtime/isolation.py."""


class MacPlatform(Platform):
    name = "macos"

    def __init__(self) -> None:
        super().__init__()
        self.activity = MacActivityProvider()
        self.notifications = MacNotificationProvider()
        self.processes = PsutilProcessProvider()
        self.sandbox = MacSandbox()

    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            active_application=self.activity.available,
            active_window_title=self.activity.available,
            filesystem_events=True,
            browser_history=bool(self.activity.get_browser_history_paths()),
            process_isolation=self.sandbox.isolation_name == "seatbelt",
            native_notifications=self.notifications.available,
            process_info=psutil is not None,
            window_text=self.activity._text_reader().available(),
        )
