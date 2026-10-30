"""Finding a program the user installed - including one installed after this
application started.

A program found by its name is looked up on PATH, and PATH is what this
process was given when it started. An installer that adds its folder to PATH
changes it for programs started AFTER it: someone who installed this
application, then Codex, and connected it, was told Codex was not installed
until the agent was restarted. So when the name is not on this process's
PATH, it is looked for on the PATH a program started now would get - read
again from the system on Windows - and then in the places such programs are
put.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple


def _windows_path() -> str:
    """The machine's PATH and the user's, as the registry holds them now."""
    import winreg

    parts: List[str] = []
    for hive, key in ((winreg.HKEY_LOCAL_MACHINE,
                       r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                      (winreg.HKEY_CURRENT_USER, "Environment")):
        try:
            with winreg.OpenKey(hive, key) as opened:
                value = str(winreg.QueryValueEx(opened, "Path")[0] or "")
        except OSError:
            continue
        parts.append(winreg.ExpandEnvironmentStrings(value))
    return os.pathsep.join(part for part in parts if part)


def usual_places() -> List[str]:
    """Where command-line programs are put when PATH does not say: the
    package managers' own folders. A program started from the Dock or at
    sign-in on macOS gets almost no PATH at all."""
    home = Path.home()
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA") or str(home / "AppData" / "Roaming")
        return [str(Path(appdata) / "npm")]
    return [str(home / ".local" / "bin"), "/opt/homebrew/bin", "/usr/local/bin",
            str(home / ".npm-global" / "bin"), str(home / ".volta" / "bin"),
            str(home / ".bun" / "bin"), "/usr/bin"]


def current_path() -> str:
    """The PATH a program started now would get, as near as can be told."""
    fresh = ""
    if sys.platform.startswith("win"):
        try:
            fresh = _windows_path()
        except Exception:                    # no registry to read
            fresh = ""
    return os.pathsep.join(part for part in (fresh, os.pathsep.join(usual_places()))
                           if part)


def take_up_path(path: str) -> None:
    """This process's PATH, with the folders `path` has that it lacks added at
    its end - as a fresh start would have them.

    Finding the program is not enough: what it starts must be found too. A
    Codex installed with npm is a few lines that run Node, and someone who
    installed Node and Codex together, after the agent started, had a Codex
    that was found and then could not start, for want of the Node beside it.
    Everything this process starts from now on is given the folders."""
    have = [one for one in os.environ.get("PATH", "").split(os.pathsep) if one]
    seen = {os.path.normcase(one.rstrip("\\/")) for one in have}
    for one in path.split(os.pathsep):
        key = os.path.normcase(one.rstrip("\\/"))
        if one and key not in seen:
            have.append(one)
            seen.add(key)
    os.environ["PATH"] = os.pathsep.join(have)


def find_program(name: str, extra: Sequence[str] = ()) -> Optional[str]:
    """Where the program `name` is: on this process's PATH, else on the PATH
    as it is now - which this process then takes up, for what the program
    itself starts - else at one of `extra` (full paths to try)."""
    return find_program_where(name, extra)[0]


def find_program_where(name: str, extra: Sequence[str] = (),
                       fresh: bool = False) -> Tuple[Optional[str], str]:
    """`find_program`, and HOW it was found - "this process's PATH", "the
    PATH as the system has it now", "where such programs are put" or "a
    place given" - "" when it was not. `fresh`: the PATH as it is now
    first, before this process's own (the user, 2026-10-29: a Codex the
    process had found once was kept until the agent was restarted)."""
    if fresh:
        now = current_path()
        found = shutil.which(name, path=now)
        if found:
            take_up_path(now)
            return found, _how_now(found)
    found = shutil.which(name)
    if found:
        return found, "this process's PATH"
    now = current_path()
    found = shutil.which(name, path=now)
    if found:
        take_up_path(now)
        return found, _how_now(found)
    for candidate in extra:
        path = Path(candidate).expanduser()
        if path.is_file():
            return str(path), "a place given"
    return None, ""


def _how_now(found: str) -> str:
    folder = os.path.normcase(str(Path(found).parent)).rstrip("\\/")
    usual = {os.path.normcase(one).rstrip("\\/") for one in usual_places()}
    return "where such programs are put" if folder in usual \
        else "the PATH as the system has it now"


__all__ = ["find_program", "find_program_where", "current_path", "usual_places",
           "take_up_path"]
