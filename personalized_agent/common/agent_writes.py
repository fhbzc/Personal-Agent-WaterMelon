"""What the agent wrote, so the listener does not record it as activity.

The agent keeps its memory in the workspace, and the workspace is watched -
which is right, because the workspace is also where the user's own documents
and downloads live. So every record the agent wrote, and every rebuild of the
index, arrived back at the agent as a filesystem event, became raw activity,
and cost a model call to interpret. Measured on one day: 12,022 of 14,892
events were the agent watching its own memory directory, ``index/state.json``
foremost among them, which is rewritten every round.

That is a loop rather than an expense. Interpreting its own bookkeeping
produces records about its own bookkeeping, which are themselves files, which
are themselves events.

**Ownership, not location** - for the folders that are the user's. The
workspace itself is not watched at all now: it holds only what the agents
make and keep, and each run writes from a process of its own, beyond any
claim made here (common/config/schema.default_directory_exclusions). What
follows is for everywhere else the agent writes: what distinguishes the
noise is not where the file is but *who wrote it*, and the agent knows which
writes were its own at the moment it makes them.

**And only its bookkeeping, not its work.** The distinction is not "the agent
wrote it" but "the agent wrote it *to keep its own records*". A record in the
library, a rebuilt index: nobody asked for those, and they say nothing about
the user's day. But when the agent writes a file the user asked for - code in
a conversation, a document a scheduled task produced - that is something that
happened, and it belongs in the record of what happened as much as if the
user had typed it. So only the library and the index claim their writes; what
goes through the sandbox on the user's behalf is left alone deliberately.

**And briefly.** A claim lasts seconds, not for ever. The same file the agent
wrote this morning may be edited by the user this afternoon, and that edit is
their activity and must be recorded. A permanent list of "our files" would
suppress it forever; a short claim suppresses only the event the write itself
caused.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Dict

#: How long a write stays claimed.
#:
#: Long enough to cover the delay between a file being written and the watcher
#: reporting it - polling backends can be several seconds behind - and short
#: enough that a user editing the same file soon after is still recorded.
CLAIM_SECONDS = 20.0

#: A ceiling on how many claims are held at once, so that a burst of writes
#: cannot grow this without limit if nothing is consuming them.
MAX_CLAIMS = 4096


class AgentWrites:
    """Paths this application wrote, for the few seconds after it wrote them."""

    def __init__(self, ttl: float = CLAIM_SECONDS) -> None:
        self.ttl = ttl
        self._lock = threading.Lock()
        self._claims: Dict[str, float] = {}

    @staticmethod
    def _key(path: object) -> str:
        return os.path.normcase(os.path.abspath(str(path)))

    def mark(self, path: object) -> None:
        """Say that the agent is writing this, now."""
        if not path:
            return
        now = time.monotonic()
        with self._lock:
            self._claims[self._key(path)] = now
            if len(self._claims) > MAX_CLAIMS:
                self._expire(now)
                if len(self._claims) > MAX_CLAIMS:
                    # Still too many: drop the oldest half rather than grow.
                    for key in sorted(self._claims,
                                      key=self._claims.get)[:MAX_CLAIMS // 2]:
                        self._claims.pop(key, None)

    def claims(self, path: object) -> bool:
        """Whether this event is the agent hearing its own writing."""
        if not path:
            return False
        key = self._key(path)
        now = time.monotonic()
        with self._lock:
            self._expire(now)
            written = self._claims.get(key)
            return written is not None and (now - written) <= self.ttl

    def _expire(self, now: float) -> None:
        stale = [k for k, at in self._claims.items() if now - at > self.ttl]
        for key in stale:
            self._claims.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._claims.clear()

    def pending(self) -> int:
        with self._lock:
            self._expire(time.monotonic())
            return len(self._claims)


#: One registry for the process.
#:
#: The library writes and the listener listens in the same process but know
#: nothing about each other, and threading a handle from one to the other
#: would mean every caller in between holding something neither of them uses.
AGENT_WRITES = AgentWrites()


def mark_written(path: object) -> None:
    """Record that the agent wrote this path."""
    AGENT_WRITES.mark(path)


def was_written_by_agent(path: object) -> bool:
    """Whether the agent wrote this path a moment ago."""
    return AGENT_WRITES.claims(path)


__all__ = ["AgentWrites", "AGENT_WRITES", "mark_written",
           "was_written_by_agent", "CLAIM_SECONDS"]
