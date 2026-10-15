"""DELEGATE WORK in Explorer's right-click menu (Windows).

Right-click a folder, a file, the empty part of a folder or the desktop,
and "Delegate work" is there - with the avatar's face as its icon. It starts this program
with what was clicked (`--delegate PATH`), which hands it to the agent: a
folder becomes one the work may change, a file is named in what it is asked
(and its folder is where the work is done). The agent then shows the
Delegate work box - the context it collected and its plan, to check first.

Only the user's own part of the registry is written (HKCU\\Software\\
Classes): nothing needs an administrator, and nothing of anyone else's is
touched. Always there - not a setting: each start of the agent puts the
entries in place again (its icon the avatar's face, its words the display
language's), and the uninstaller takes them out (`--explorer-menu off`).
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)

#: The verb's key - the same under each place it appears.
VERB = "PersonalizedAgent.Delegate"
#: Where it appears: a file, a folder, the empty part of a folder, the
#: desktop. (On Windows 11 these are under "Show more options", or a
#: right-click with Shift held: its first menu lists only packaged apps'.)
PLACES = (r"Software\Classes\*\shell", r"Software\Classes\Directory\shell",
          r"Software\Classes\Directory\Background\shell",
          r"Software\Classes\DesktopBackground\Shell")
#: What each place passes: the item clicked, or the folder being shown.
ARGUMENT = {PLACES[0]: "%1", PLACES[1]: "%1", PLACES[2]: "%V", PLACES[3]: "%V"}
#: The words, in each display language - then the icon.
LABELS = {"en": "Delegate work", "zh": "委托工作", "es": "Delegar trabajo",
          "ru": "Поручить работу", "fr": "Déléguer une tâche", "de": "Arbeit delegieren",
          "it": "Delega un lavoro",
          "ja": "作業を任せる", "ko": "작업 맡기기"}


def available() -> bool:
    return sys.platform == "win32"


def _quoted(command: List[str]) -> str:
    return subprocess.list2cmdline(command)


def install(command: List[str], icon: Optional[Path], language: str = "en") -> bool:
    """The entries put in place: `command` is how the program is started,
    `--delegate` and the item are added to it."""
    if not available():
        return False
    import winreg
    label = LABELS.get(language, LABELS["en"])
    for place in PLACES:
        key_path = f"{place}\\{VERB}"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, label)
            winreg.SetValueEx(key, "MUIVerb", 0, winreg.REG_SZ, label)
            if icon is not None and Path(icon).exists():
                winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, str(icon))
            # Several items chosen together come as one piece of work: each
            # is passed, and the program gathers them (`collect_handover`).
            winreg.SetValueEx(key, "MultiSelectModel", 0, winreg.REG_SZ, "Player")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{key_path}\\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ,
                              _quoted([*command, "--delegate"]) + f' "{ARGUMENT[place]}"')
    return True


def remove() -> bool:
    """Every entry taken out."""
    if not available():
        return False
    import winreg
    for place in PLACES:
        for sub in (f"{place}\\{VERB}\\command", f"{place}\\{VERB}"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            except FileNotFoundError:
                pass
            except OSError:
                log.debug("could not remove %s", sub, exc_info=True)
    return True


def present() -> bool:
    if not available():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{PLACES[1]}\\{VERB}\\command"):
            return True
    except OSError:
        return False


def follow(on: bool, command: List[str], icon: Optional[Path], language: str = "en") -> bool:
    """In line with the setting: put in place - with the current avatar's
    face and the current language - or taken out."""
    try:
        return install(command, icon, language) if on else remove()
    except Exception:                          # pragma: no cover - the system's own
        log.warning("the Explorer menu could not be set", exc_info=True)
        return False


__all__ = ["install", "remove", "present", "follow", "available", "VERB", "LABELS"]
