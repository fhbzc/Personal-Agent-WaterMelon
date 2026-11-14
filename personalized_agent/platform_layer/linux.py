"""Linux specific implementations of the platform interfaces."""

from __future__ import annotations

import logging
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

    def user_screen_image(self, title: str = "", hidden: Optional[Any] = None,
                          **_ignored: Any) -> Optional[Any]:
        """THE WHOLE SCREEN THE USER'S WINDOW IS ON (the user, 2026-11-08: "linux也要做"), as X shows
        it - this application's windows in it as they are ("linux上实在做不到就带窗口截吧": X has no
        way to leave a window out of a picture), every window `hidden(application, title)` says is
        never read painted over where it shows. Not on Wayland, which allows no picture; without
        xprop and xdotool, None."""
        import os
        if not (self._xdotool and self._xprop) or not os.environ.get("DISPLAY"):
            return None
        try:
            from PIL import ImageGrab

            from .screen_picture import blank_windows, display_of, the_window_meant
            stacked = children.run_short([self._xprop, "-root", "_NET_CLIENT_LIST_STACKING"],
                                         capture_output=True, text=True, encoding="utf-8",
                                         errors="replace", timeout=5).stdout
            windows = []
            for ident in reversed(stacked_ids(stacked)):          # from the top down
                one = self._x_window(ident)
                if one is not None:
                    windows.append(one)
            displays = []
            if which("xrandr"):
                said = children.run_short(["xrandr", "--listactivemonitors"], capture_output=True,
                                          text=True, encoding="utf-8", errors="replace",
                                          timeout=5).stdout
                displays = monitors_of(said)
            meant = the_window_meant(windows, title)
            screen = display_of(meant["box"] if meant else None, displays)
            image = ImageGrab.grab(bbox=screen) if screen else ImageGrab.grab()
            if screen is None:
                screen = (0, 0, image.size[0], image.size[1])
            shown = [dict(one, ours=False) for one in windows]      # in it, as they are
            image = blank_windows(image, screen, shown, hidden)
            # THE MOUSE POINTER, DRAWN WHERE IT WAS (2026-11-13: "你要能截取我的鼠标位置").
            try:
                from .screen_picture import draw_pointer
                said = children.run_short([self._xdotool, "getmouselocation", "--shell"], capture_output=True,
                                          text=True, encoding="utf-8", errors="replace", timeout=5).stdout
                found = dict(line.split("=", 1) for line in said.splitlines() if "=" in line)
                image = draw_pointer(image, screen, (int(found["X"]), int(found["Y"])))
            except Exception:
                log.debug("the mouse pointer could not be drawn", exc_info=True)
            return image
        except Exception:
            log.debug("could not take the screen", exc_info=True)
            return None

    def _x_window(self, ident: str) -> Optional[Dict[str, Any]]:
        """One window X lists: its frame, program, title and whether it is this application's."""
        import os
        try:
            geometry = children.run_short([self._xdotool, "getwindowgeometry", "--shell", ident],
                                          capture_output=True, text=True, encoding="utf-8",
                                          errors="replace", timeout=5).stdout
            said = dict(line.split("=", 1) for line in geometry.splitlines() if "=" in line)
            x, y, w, h = (int(said[key]) for key in ("X", "Y", "WIDTH", "HEIGHT"))
            props = children.run_short([self._xprop, "-id", ident, "_NET_WM_PID", "_NET_WM_NAME"],
                                       capture_output=True, text=True, encoding="utf-8",
                                       errors="replace", timeout=5).stdout
        except Exception:
            return None
        pid, title = props_of(props)
        application = ""
        if pid:
            try:
                import psutil
                application = psutil.Process(pid).name()
            except Exception:
                application = ""
        return {"id": ident, "box": (x, y, x + w, y + h), "application": application,
                "title": title, "ours": pid == os.getpid(), "opaque": True, "layer": 0}

    def get_active_application(self) -> Optional[str]:
        return self.get_active_window().application

    # -- WeChat's messages: the one window read on Linux (linux_wechat.py) --
    def text_in_front_allowed(self, allowed) -> Optional[Any]:
        from .linux_wechat import wechat_in_front
        try:
            got = wechat_in_front(self, allowed)
            self._window_text_error = ""
            return got
        except Exception as exc:
            self._window_text_error = str(exc)[:200]
            log.debug("could not read WeChat", exc_info=True)
            return None

    @property
    def window_text_error(self) -> str:
        return getattr(self, "_window_text_error", "")

    def can_read_wechat(self) -> bool:
        from .linux_wechat import rapid_available
        return bool(self._xdotool) and rapid_available()

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
            # WeChat's window only, read from its picture (linux_wechat.py).
            window_text=self.activity.can_read_wechat(),
            extra={"window_text_only": "WeChat"},
        )


def stacked_ids(said: str) -> list:
    """The windows `xprop -root _NET_CLIENT_LIST_STACKING` lists, from the bottom up."""
    import re
    return re.findall(r"0x[0-9a-fA-F]+", said.split("#", 1)[-1]) if "#" in said else []


def props_of(said: str) -> tuple:
    """A window's process and title, from `xprop -id <it> _NET_WM_PID _NET_WM_NAME`."""
    import re
    pid = re.search(r"_NET_WM_PID\(CARDINAL\)\s*=\s*(\d+)", said)
    name = re.search(r'_NET_WM_NAME\([^)]*\)\s*=\s*"(.*)"', said)
    return (int(pid.group(1)) if pid else 0, name.group(1) if name else "")


def monitors_of(said: str) -> list:
    """The screens `xrandr --listactivemonitors` lists, as (left, top, right, bottom)."""
    import re
    out = []
    for width, height, left, top in re.findall(r"(\d+)/\d+x(\d+)/\d+\+(\d+)\+(\d+)", said):
        out.append((int(left), int(top), int(left) + int(width), int(top) + int(height)))
    return out
