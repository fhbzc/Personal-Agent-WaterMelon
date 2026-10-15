"""A SOUND WHEN A RUN ENDS - played by the application itself, so it is
heard with the page closed, or another window in front.

One of the machine's own sounds, the way it plays them: on Windows the
system's asterisk or exclamation sound (winsound - asynchronous, it never
holds the run up); on a Mac one of the sounds in /System/Library/Sounds,
through afplay; elsewhere a freedesktop sound through paplay or
canberra-gtk-play, and the terminal's bell when there is neither. Best
effort, every step of it: a machine with no sound, or none of these, plays
nothing - and nothing here ever raises.

Never under the tests (PA_NO_SOUND): a test that reaches the real player
plays nothing, as the folder choosers open nothing (PA_NO_DIALOGS)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

#: How a run ended, as the player is told: finished, failed - or stopped
#: otherwise (at a limit, by the user, stalled, blocked).
DONE, FAILED, STOPPED = "done", "failed", "stopped"

#: The Mac's sounds for each.
MAC_SOUNDS = {DONE: "Glass", FAILED: "Basso", STOPPED: "Ping"}

#: The freedesktop sound theme's, for each - by name, and as a file.
FREEDESKTOP = {DONE: "complete", FAILED: "dialog-error", STOPPED: "dialog-information"}
FREEDESKTOP_DIR = Path("/usr/share/sounds/freedesktop/stereo")


def sound_disabled() -> bool:
    """True where no sound may be played: under the tests."""
    return bool(os.environ.get("PA_NO_SOUND"))


def play_end_sound(outcome: str = DONE) -> bool:
    """Play the sound a run ends with, without waiting for it to finish.
    True when one was set going - not a promise anyone heard it."""
    if sound_disabled():
        return False
    try:
        if sys.platform.startswith("win"):
            return _windows(outcome)
        if sys.platform == "darwin":
            return _mac(outcome)
        return _elsewhere(outcome)
    except Exception:        # pragma: no cover - a sound is never worth an error
        log.debug("could not play the end-of-run sound", exc_info=True)
        return False


def _windows(outcome: str) -> bool:
    import winsound
    alias = "SystemExclamation" if outcome == FAILED else "SystemAsterisk"
    try:
        # The system's own sound by its alias, played asynchronously; with
        # SND_NODEFAULT, an alias with no sound set plays nothing rather
        # than the default beep.
        winsound.PlaySound(alias, winsound.SND_ALIAS | winsound.SND_ASYNC
                           | winsound.SND_NODEFAULT)
        return True
    except RuntimeError:
        # MessageBeep plays asynchronously too.
        winsound.MessageBeep(winsound.MB_ICONHAND if outcome == FAILED
                             else winsound.MB_ICONASTERISK)
        return True


def _quietly(command: list) -> bool:
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True)
    return True


def _mac(outcome: str) -> bool:
    sound = Path("/System/Library/Sounds") / f"{MAC_SOUNDS.get(outcome, 'Glass')}.aiff"
    if shutil.which("afplay") and sound.is_file():
        return _quietly(["afplay", str(sound)])
    return _bell()


def _elsewhere(outcome: str) -> bool:
    name = FREEDESKTOP.get(outcome, "complete")
    sound = FREEDESKTOP_DIR / f"{name}.oga"
    if shutil.which("paplay") and sound.is_file():
        return _quietly(["paplay", str(sound)])
    if shutil.which("canberra-gtk-play"):
        return _quietly(["canberra-gtk-play", "-i", name])
    return _bell()


def _bell() -> bool:
    stream = getattr(sys, "__stdout__", None)
    if stream is None:
        return False
    try:
        stream.write("\a")
        stream.flush()
        return True
    except (OSError, ValueError):
        return False
