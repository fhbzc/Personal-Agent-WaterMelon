"""A COMMAND RUN IN A TERMINAL WINDOW THE USER SEES (the user, 2026-11-21: "你那个mac, claude cli 没有登录，你要给一个登录的
位置"): a sign-in that asks its own questions and opens its own browser page - `claude auth login` - run where the user can
answer it: Terminal on a Mac (a .command file opened by it - no AppleScript, so no Automation question), a command window
on Windows, the desktop's terminal on Linux. The window is the user's; this program only starts it."""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)


def mac_script(command: List[str], said_after: str) -> str:
    """The .command file's words: the command, then what to do next, the window left open."""
    return ("#!/bin/zsh\n"
            f"{shlex.join(command)}\n"
            "echo\n"
            f"echo {shlex.quote(said_after)}\n")


def open_in_terminal(command: List[str], title: str, folder: Path, said_after: str = "",
                     platform: Optional[str] = None) -> bool:
    """`command` run in a new terminal window titled `title` (Windows); its script, where one is needed, kept in
    `folder`. Whether the window could be started."""
    platform = platform or sys.platform
    try:
        if platform == "win32":
            line = subprocess.list2cmdline(command)
            after = f" & echo. & echo {said_after}" if said_after else ""
            subprocess.Popen(f'start "{title}" cmd.exe /k {line}{after}', shell=True)
            return True
        if platform == "darwin":
            folder.mkdir(parents=True, exist_ok=True)
            script = folder / "sign_in.command"
            script.write_text(mac_script(command, said_after), encoding="utf-8")
            os.chmod(script, 0o755)
            subprocess.Popen(["open", "-a", "Terminal", str(script)])
            return True
        for terminal, flag in (("x-terminal-emulator", "-e"), ("gnome-terminal", "--"), ("konsole", "-e"),
                               ("xterm", "-e")):
            found = shutil.which(terminal)
            if found:
                subprocess.Popen([found, flag, *command])
                return True
        return False
    except Exception:
        log.info("a terminal could not be opened for %s", title, exc_info=True)
        return False


__all__ = ["mac_script", "open_in_terminal"]
