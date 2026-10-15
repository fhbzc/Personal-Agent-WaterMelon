"""Replacing a file in place, on a machine where something else may hold it.

Every file this application rewrites - settings, user defaults, the credential
store - is written to a temporary file and then moved over the original, so
that a crash midway leaves the old file intact rather than half a new one.

On Windows that final move is not always allowed to happen immediately. If any
other process has the target open, even for the moment it takes to read it, the
move fails outright with a permission error instead of waiting. Backup and
sync clients, search indexers and antivirus scanners all open files they did
not create, and the agent root is a directory the user is invited to put
wherever they like - including inside a synced folder, which is the whole point
of it being relocatable (section 110.1).

The failure is brief and it is not about permissions in any sense the user
could act on, so it is waited out rather than reported. A move that keeps
failing past that is a real fault and is raised.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

#: Waits between attempts, in seconds. They lengthen because a scanner holding
#: the file is usually gone within milliseconds, and anything still holding it
#: after that is doing something slower than a glance.
BACKOFF = (0.01, 0.02, 0.05, 0.1, 0.2, 0.4)


def replace_atomically(source: Path, target: Path) -> None:
    """Move ``source`` onto ``target``, waiting out a transient hold on it."""
    for pause in BACKOFF:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            time.sleep(pause)
    # One last attempt, whose failure is the caller's to hear about: at this
    # point something is holding the file for longer than any scanner would.
    try:
        os.replace(source, target)
    except PermissionError:
        log.warning("could not replace %s after %.2fs of retries; "
                    "something else on this machine is holding it",
                    target, sum(BACKOFF))
        raise
