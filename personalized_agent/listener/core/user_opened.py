"""What the desktop itself says the user opened (section 19).

Every desktop keeps a list of the files its user has opened, and keeps it for
its own purposes - the Recent menu, the jump list, the "reopen" list. The
thing that makes it worth reading here is what does *not* go in it: a program
reading a file with ``open()`` never appears. Only a file opened the way a
person opens one - double-clicked, chosen in an open dialog, handed to an
application by the desktop - is written down.

That is the definition being looked for, already maintained, on all three
platforms and without any privilege:

- Windows: ``%APPDATA%\\Microsoft\\Windows\\Recent``, where Explorer writes a
  shortcut per file
- macOS: the Spotlight attribute ``kMDItemLastUsedDate``, which LaunchServices
  updates and a plain read does not
- Linux: ``recently-used.xbel`` (GTK/freedesktop) and ``RecentDocuments`` (KDE)

It is read as **evidence for**, never against. A file that is in the list was
opened by the user; a file that is not may still have been - opened from a
command line, or by an application that does not register what it opens - so
its absence proves nothing and is allowed to prove nothing.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


class WhatTheUserOpened:
    """The desktop's own account of what its user opened, kept fresh."""

    #: How often the platform is asked again.
    #:
    #: Reading it means a directory scan or a subprocess, and the question is
    #: asked once per file event - which on a busy machine is thousands a
    #: minute. Half a minute stale is no loss: the answer is about which
    #: files were opened, and one opened seconds ago has not been reported by
    #: the filesystem yet either.
    REFRESH_SECONDS = 30.0

    #: How far back the list is asked for. The same window a read's own
    #: account of itself is believed within.
    LOOK_BACK = 6 * 3600.0

    #: How far apart the desktop's record and the file event may be.
    #:
    #: They describe the same act from two sides and neither is punctual: the
    #: desktop writes its entry when the application asks it to, and the
    #: filesystem reports the read when Windows flushes. Five minutes is wide
    #: enough for both and far short of a second sitting at the same file.
    CLOSE_ENOUGH = 300.0

    def __init__(self, platform: Any = None) -> None:
        self.platform = platform
        self._opened: Dict[str, float] = {}
        self._asked_at = 0.0
        self._lock = threading.Lock()
        self.last_error: str = ""

    # -- the question -------------------------------------------------------
    def opened_by_the_user(self, path: Optional[str], when: float) -> bool:
        """Whether the desktop says the user opened this file around then."""
        if not path:
            return False
        self._refresh_if_stale()
        with self._lock:
            known = self._opened
        if not known:
            return False
        for key in (os.path.normcase(os.path.abspath(str(path))),
                    os.path.normcase(os.path.basename(str(path)))):
            # The basename is tried second and on purpose: Windows records a
            # shortcut named after the file, and the path inside it is not
            # always recoverable. Two files of the same name in different
            # folders is a wrong answer in the generous direction, which is
            # the direction this evidence is allowed to be wrong in.
            at = known.get(key)
            if at is not None and abs(at - when) <= self.CLOSE_ENOUGH:
                return True
        return False

    # -- keeping it fresh ---------------------------------------------------
    def _refresh_if_stale(self) -> None:
        now = time.time()
        with self._lock:
            if now - self._asked_at < self.REFRESH_SECONDS:
                return
            self._asked_at = now
        activity = getattr(self.platform, "activity", None)
        ask = getattr(activity, "files_the_user_opened", None)
        if ask is None:
            return
        try:
            opened = ask(now - self.LOOK_BACK) or {}
        except Exception as exc:       # a desktop that will not answer is not fatal
            self.last_error = str(exc)[:200]
            log.debug("could not read what the user opened", exc_info=True)
            return
        self.last_error = ""
        with self._lock:
            self._opened = {os.path.normcase(str(k)): float(v)
                            for k, v in opened.items()}

    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {"files": len(self._opened), "last_error": self.last_error}
