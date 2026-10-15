"""What was in front, at a moment that has already passed (section 19).

The window in front is the evidence for whether a file change was the user's
or a program's, and the Listener has always had it - but only ever for *now*.
That is enough for a write, which is reported as it happens, and useless for
a read, which Windows reports when it gets round to flushing the access time,
up to an hour later. Asked then, the question returns whatever the user
happens to be looking at, which is evidence about nothing.

So what was in front is remembered as a trail rather than a reading. Nothing
new is sampled for it: the Application provider already emits a focus event
whenever the window or its title changes, and those events pass through the
manager on their way to storage, so the trail is fed from the stream that
exists.

A trail that knows nothing about a moment says so, and the caller treats that
as "no evidence", never as "not the user" - an agent that stopped recording
because its own history was too short would be worse than one that recorded
too much.
"""

from __future__ import annotations

import bisect
import threading
from typing import List, Optional, Tuple

#: A title long enough to be quoted from is long enough to be stored.
LONGEST_TITLE = 400


class FocusTrail:
    """Which application and window title were in front, moment by moment."""

    #: How far back the trail goes.
    #:
    #: The same six hours a file's account of when it was read is believed
    #: for; a moment older than the oldest event that can arrive is a moment
    #: nothing will ever ask about.
    REMEMBER_FOR = 6 * 3600.0

    #: A ceiling in case something changes its title continuously. Dropping
    #: the oldest is right: the newest moments are the ones being asked about.
    AT_MOST = 20000

    def __init__(self) -> None:
        self._when: List[float] = []
        self._what: List[Tuple[Optional[str], Optional[str]]] = []
        self._lock = threading.Lock()

    def remember(self, application: Optional[str], title: Optional[str],
                 at: float) -> None:
        """Record that this was in front from ``at`` until the next entry."""
        if not application and not title:
            return
        title = (title or "")[:LONGEST_TITLE] or None
        with self._lock:
            if self._when and at < self._when[-1]:
                # Out of order, which focus events are not - but a clock that
                # steps backwards would break every search that follows.
                at = self._when[-1]
            if self._what and self._what[-1] == (application, title):
                return
            self._when.append(at)
            self._what.append((application, title))
            self._forget_what_is_too_old(at)

    def _forget_what_is_too_old(self, now: float) -> None:
        cutoff = now - self.REMEMBER_FOR
        drop = bisect.bisect_right(self._when, cutoff) - 1
        if drop > 0:
            # One entry before the cutoff is kept: it is the one that was in
            # front *at* the cutoff, and dropping it would lose every moment
            # between it and the next change.
            del self._when[:drop]
            del self._what[:drop]
        if len(self._when) > self.AT_MOST:
            over = len(self._when) - self.AT_MOST
            del self._when[:over]
            del self._what[:over]

    def at(self, when: float) -> Tuple[Optional[str], Optional[str]]:
        """What was in front at that moment, or (None, None) if unknown.

        Unknown is the honest answer for a moment before the trail starts -
        the agent was not running, or had just started - and it is not the
        same as "nothing was in front".
        """
        with self._lock:
            if not self._when:
                return None, None
            index = bisect.bisect_right(self._when, when) - 1
            if index < 0:
                return None, None
            return self._what[index]

    def __len__(self) -> int:
        with self._lock:
            return len(self._when)

    def describe(self) -> dict:
        with self._lock:
            return {
                "moments": len(self._when),
                "since": self._when[0] if self._when else None,
                "until": self._when[-1] if self._when else None,
            }
