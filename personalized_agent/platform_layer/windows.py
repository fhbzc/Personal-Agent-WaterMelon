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
    #: A window can be asked what it draws (`user_window_image`, `itself_only`).
    draws_windows = True

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

    def _window_itself(self, hwnd: Any, box: tuple) -> Optional[Any]:
        """WHAT THE WINDOW DRAWS, NOT WHAT IS ON THE SCREEN THERE (the user, 2026-11-04: the chat
        or the Delegate work box must not be in the picture): read off the screen, whatever
        stands over it was in it - the avatar, its buttons, a bubble still open. None
        where the window gives nothing, and the screen is read as before."""
        ctypes = self._ctypes
        try:
            from ctypes import wintypes

            from PIL import Image
            user32, gdi32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
            handle = ctypes.c_void_p
            user32.GetDC.restype = handle
            user32.GetDC.argtypes = [handle]
            user32.ReleaseDC.argtypes = [handle, handle]
            user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
            user32.PrintWindow.argtypes = [wintypes.HWND, handle, wintypes.UINT]
            gdi32.CreateCompatibleDC.restype = handle
            gdi32.CreateCompatibleDC.argtypes = [handle]
            gdi32.CreateCompatibleBitmap.restype = handle
            gdi32.CreateCompatibleBitmap.argtypes = [handle, ctypes.c_int, ctypes.c_int]
            gdi32.SelectObject.restype = handle
            gdi32.SelectObject.argtypes = [handle, handle]
            gdi32.DeleteObject.argtypes = [handle]
            gdi32.DeleteDC.argtypes = [handle]
            gdi32.GetDIBits.argtypes = [handle, handle, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                        ctypes.c_void_p, wintypes.UINT]
            # In real pixels, as `box` is: on a screen scaled up the system would otherwise
            # tell this thread a smaller window than it draws.
            user32.SetThreadDpiAwarenessContext.restype = handle
            user32.SetThreadDpiAwarenessContext.argtypes = [handle]
            was = user32.SetThreadDpiAwarenessContext(handle(-4))   # per monitor
            rect = wintypes.RECT()
            found = user32.GetWindowRect(hwnd, ctypes.byref(rect))
            if was:
                user32.SetThreadDpiAwarenessContext(was)
            if not found:
                return None
            width, height = rect.right - rect.left, rect.bottom - rect.top
            if width <= 0 or height <= 0:
                return None

            class Header(ctypes.Structure):
                _fields_ = [("size", wintypes.DWORD), ("width", wintypes.LONG),
                            ("height", wintypes.LONG), ("planes", wintypes.WORD),
                            ("bits", wintypes.WORD), ("compression", wintypes.DWORD),
                            ("image_size", wintypes.DWORD), ("x_ppm", wintypes.LONG),
                            ("y_ppm", wintypes.LONG), ("used", wintypes.DWORD),
                            ("important", wintypes.DWORD)]
            screen = user32.GetDC(None)
            memory = gdi32.CreateCompatibleDC(screen)
            bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
            old = gdi32.SelectObject(memory, bitmap)
            try:
                if not user32.PrintWindow(hwnd, memory, 2):      # PW_RENDERFULLCONTENT
                    return None
                header = Header(ctypes.sizeof(Header), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
                pixels = ctypes.create_string_buffer(width * height * 4)
                if not gdi32.GetDIBits(memory, bitmap, 0, height, pixels, ctypes.byref(header), 0):
                    return None
            finally:
                gdi32.SelectObject(memory, old)
                gdi32.DeleteObject(bitmap)
                gdi32.DeleteDC(memory)
                user32.ReleaseDC(None, screen)
            image = Image.frombuffer("RGB", (width, height), pixels.raw, "raw", "BGRX", 0, 1)
            image = image.crop((box[0] - rect.left, box[1] - rect.top,
                                box[2] - rect.left, box[3] - rect.top))
            return image if image.convert("L").getextrema()[1] >= 16 else None
        except Exception:
            log.debug("the window could not draw itself", exc_info=True)
            return None

    def user_window_image(self, itself_only: bool = False) -> Optional[Any]:
        """`itself_only`: never what is on the screen there, which what opened since
        could cover. The topmost real window that is not this application's: pressed on the
        avatar, the avatar is in front, and the window meant is the one under it.
        Down the windows in their order; the desktop reached, there is none."""
        if not self.available:
            return None
        ctypes = self._ctypes
        try:
            from ctypes import wintypes

            from PIL import ImageGrab
            user32 = ctypes.WinDLL("user32")     # its own: the types below stay here
            dwm = ctypes.WinDLL("dwmapi")
            for name in ("GetTopWindow", "GetWindow"):
                getattr(user32, name).restype = wintypes.HWND
            user32.GetTopWindow.argtypes = [wintypes.HWND]
            user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
            user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.IsWindowVisible.argtypes = [wintypes.HWND]
            user32.IsIconic.argtypes = [wintypes.HWND]
            user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
            user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
            hwnd = user32.GetTopWindow(None)
            while hwnd:
                box = self._a_users_window(user32, dwm, hwnd, wintypes)
                if box == "desktop":
                    return None
                if box:
                    drawn = self._window_itself(hwnd, box)
                    if drawn is not None or itself_only:
                        return drawn
                    return ImageGrab.grab(bbox=box, all_screens=True)
                hwnd = user32.GetWindow(hwnd, 2)         # GW_HWNDNEXT
        except Exception:
            log.debug("could not take the user's window", exc_info=True)
        return None

    def _a_users_window(self, user32: Any, dwm: Any, hwnd: Any, wintypes: Any) -> Any:
        """Its frame, if it is one of the user's; "desktop" at the desktop itself."""
        ctypes = self._ctypes
        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, name, 64)
        if name.value in ("Progman", "WorkerW"):
            return "desktop"
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        tool = user32.GetWindowLongW(hwnd, -20) & 0x80       # WS_EX_TOOLWINDOW
        if (pid.value == os.getpid() or tool or not user32.IsWindowVisible(hwnd)
                or user32.IsIconic(hwnd) or not user32.GetWindowTextLengthW(hwnd)
                or name.value in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd")):
            return None
        cloaked = wintypes.DWORD()               # on another desktop, or a suspended app
        dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        rect = wintypes.RECT()
        if cloaked.value or dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect),
                                                      ctypes.sizeof(rect)) != 0:
            return None
        if rect.right - rect.left < 120 or rect.bottom - rect.top < 90:
            return None
        return (rect.left, rect.top, rect.right, rect.bottom)

    def count_windows(self) -> Optional[int]:
        """The windows on the taskbar: visible, titled, owned by no other window, not a tool
        window, and not hidden by the shell (other desktops, suspended store apps)."""
        if self._user32 is None:
            return None
        import ctypes
        from ctypes import wintypes
        user32 = self._user32
        found = [0]
        GWL_EXSTYLE, WS_EX_TOOLWINDOW, GW_OWNER, DWMWA_CLOAKED = -20, 0x80, 4, 14

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def each(hwnd, _):
            try:
                if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
                    return True
                if user32.GetWindowTextLengthW(hwnd) == 0:
                    return True
                if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
                    return True
                cloaked = wintypes.DWORD(0)
                if ctypes.windll.dwmapi.DwmGetWindowAttribute(
                        hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked),
                        ctypes.sizeof(cloaked)) == 0 and cloaked.value:
                    return True
                found[0] += 1
            except Exception:
                pass
            return True
        try:
            user32.EnumWindows(each, 0)
        except Exception:
            log.debug("could not count the windows", exc_info=True)
            return None
        return found[0]

    def what_holds_the_screen(self) -> str:
        """Windows' own word (SHQueryUserNotificationState): a slide show, or a
        film or game over the whole screen."""
        if self._ctypes is None:
            return ""
        state = self._ctypes.c_int(0)
        try:
            shell = self._ctypes.windll.shell32
            if shell.SHQueryUserNotificationState(self._ctypes.byref(state)) != 0:
                return ""
        except (AttributeError, OSError):
            return ""
        return {2: "fullscreen", 3: "fullscreen", 4: "presentation"}.get(state.value, "")

    def microphone_users(self) -> list:
        """From what Windows keeps for its own microphone indicator - only read:
        a program started on it and not stopped has it now."""
        try:
            import winreg
        except ImportError:
            return []
        root = (r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
                r"\ConsentStore\microphone")
        found = set()
        for path in (root, root + r"\NonPackaged"):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                    for n in range(winreg.QueryInfoKey(key)[0]):
                        name = winreg.EnumKey(key, n)
                        try:
                            with winreg.OpenKey(key, name) as one:
                                started = winreg.QueryValueEx(one, "LastUsedTimeStart")[0]
                                stopped = winreg.QueryValueEx(one, "LastUsedTimeStop")[0]
                        except OSError:
                            continue
                        if started and not stopped:
                            # A path with # for its slashes, or a packaged app's name.
                            found.add(name.rsplit("#", 1)[-1].split("_")[0].lower()
                                      .removesuffix(".exe"))
            except OSError:
                continue
        return sorted(found)

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
        "$env:PA_APP).Show($toast);"
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
        from ..version import APP_NAME
        # Shown as who it is from.
        env = dict(os.environ, PA_TITLE=title, PA_BODY=message, PA_APP=APP_NAME)
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
