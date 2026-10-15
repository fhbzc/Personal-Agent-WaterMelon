"""Starting when the user signs in (Settings > General).

Each system's own way, in the user's own part of it - nothing that needs an
administrator:

- **Windows**: a value under HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run.
- **macOS**: a LaunchAgent, ~/Library/LaunchAgents/<label>.plist, RunAtLoad.
- **Linux**: an XDG autostart entry, ~/.config/autostart/<name>.desktop.

What is started is what is running now: the installed program, or, run
from the source, this Python with run.py - with the same root, and headless
if it is headless. The setting is the truth (`start_at_login`); each start
of the application puts the system's entry back in line with it, so a moved
installation is followed and one switched off stays off.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, List, Optional

from ..version import APP_NAME

#: The name the entry goes by, on every system.
ENTRY = "PersonalizedAgent"
MAC_LABEL = "org.personalizedagent.agent"
LINUX_FILE = "personalized-agent.desktop"
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command(root: Optional[Path] = None, headless: bool = False) -> List[str]:
    """How this application is started again, as it was started now."""
    if getattr(sys, "frozen", False):
        command = [sys.executable]
    else:
        python = Path(sys.executable)
        # No console window at sign-in, where there is one without it.
        quiet = python.with_name("pythonw.exe")
        if sys.platform.startswith("win") and quiet.exists():
            python = quiet
        run = Path(__file__).resolve().parents[2] / "run.py"
        command = [str(python), str(run)] if run.exists() else \
            [str(python), "-m", "personalized_agent"]
    if root is not None:
        command += ["--root", str(Path(root).resolve())]
    if headless:
        command.append("--headless")
    return command


class _Registry:
    """The user's Run key - the only part of the registry touched."""

    def get(self) -> Optional[str]:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY) as key:
                return str(winreg.QueryValueEx(key, ENTRY)[0])
        except OSError:
            return None

    def set(self, value: str) -> None:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY) as key:
            winreg.SetValueEx(key, ENTRY, 0, winreg.REG_SZ, value)

    def delete(self) -> None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, ENTRY)
        except OSError:
            pass


class Autostart:
    """Whether the application starts at sign-in, and making it so."""

    def __init__(self, command: List[str], system: str = sys.platform,
                 home: Optional[Path] = None, registry: Any = None) -> None:
        self.command = list(command)
        self.system = "windows" if system.startswith("win") else \
            "macos" if system == "darwin" else "linux"
        self.home = Path(home) if home is not None else Path.home()
        self._registry = registry

    # -- where ---------------------------------------------------------------
    def _registry_(self) -> Any:
        if self._registry is None:
            self._registry = _Registry()
        return self._registry

    def plist_path(self) -> Path:
        return self.home / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"

    def desktop_path(self) -> Path:
        base = os.environ.get("XDG_CONFIG_HOME") if self.home == Path.home() else None
        return Path(base or self.home / ".config") / "autostart" / LINUX_FILE

    def where(self) -> str:
        """Where the entry is, in words."""
        if self.system == "windows":
            return f"HKEY_CURRENT_USER\\{WINDOWS_RUN_KEY}\\{ENTRY}"
        return str(self.plist_path() if self.system == "macos" else self.desktop_path())

    # -- the entry as it should read ----------------------------------------
    def _windows_value(self) -> str:
        return subprocess.list2cmdline(self.command)

    def _plist(self) -> bytes:
        return plistlib.dumps({
            "Label": MAC_LABEL, "ProgramArguments": self.command, "RunAtLoad": True,
            "ProcessType": "Interactive",
            "WorkingDirectory": str(Path(self.command[-1]).parent
                                    if self.command[-1].endswith(".py") else self.home)})

    def _desktop(self) -> str:
        return ("[Desktop Entry]\nType=Application\n"
                f"Name={APP_NAME}\nComment=Starts {APP_NAME} when you sign in\n"
                f"Exec={shlex.join(self.command)}\n"
                "X-GNOME-Autostart-enabled=true\nHidden=false\nNoDisplay=false\n"
                "Terminal=false\n")

    # -- reading and changing --------------------------------------------------
    def enabled(self) -> bool:
        if self.system == "windows":
            return self._registry_().get() is not None
        return (self.plist_path() if self.system == "macos" else self.desktop_path()).exists()

    def current(self) -> bool:
        """Whether there is an entry, and it starts what is running now."""
        if self.system == "windows":
            return self._registry_().get() == self._windows_value()
        if self.system == "macos":
            path = self.plist_path()
            return path.exists() and path.read_bytes() == self._plist()
        path = self.desktop_path()
        return path.exists() and path.read_text(encoding="utf-8") == self._desktop()

    def set(self, on: bool) -> None:
        if self.system == "windows":
            if on:
                self._registry_().set(self._windows_value())
            else:
                self._registry_().delete()
            return
        path = self.plist_path() if self.system == "macos" else self.desktop_path()
        if not on:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.system == "macos":
            path.write_bytes(self._plist())
        else:
            path.write_text(self._desktop(), encoding="utf-8")

    def follow(self, wanted: bool) -> bool:
        """The system's entry put in line with the setting; whether anything
        was changed."""
        if wanted and not self.current():
            self.set(True)
            return True
        if not wanted and self.enabled():
            self.set(False)
            return True
        return False


__all__ = ["Autostart", "launch_command", "ENTRY", "MAC_LABEL", "LINUX_FILE"]
