"""WHAT THE FILTERS KEPT OUT, as it happened - for the Record log.

Which source, what kind of event, which rule kept it out and when: NEVER
what was in it. That is the point of keeping it out. Held in memory only,
since the agent started: the most recent `DROPPED_KEPT`, and a count for
each rule.

The rules (`WHY`) are the Privacy settings that drop a record whole. Hiding
a password or a code inside a record that is kept is not one of them: that
record is in the log, marked where something was hidden.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List

#: How many of the latest are kept to show.
DROPPED_KEPT = 200

#: Each rule, by the key a refusal names it with.
WHY = {
    "untrusted_capture": "the screenshot was not produced by the trusted local capture",
    "switch_off": "its switch in Settings > Privacy is off",
    "application_list": "the application allowlist or denylist",
    "website_list": "the website allowlist or denylist",
    "not_here": "Only while you are here: nobody was at the computer",
    "not_yours": "Only what you did yourself: a program did it, not you",
    "agents_own": "the agent's own files",
    "excluded_folder": "an excluded folder",
    "ignored_name": "an ignored file name",
    "credentials": "a screenshot whose words held a password or a code",
}

_lock = threading.Lock()
_dropped: "deque[Dict[str, Any]]" = deque(maxlen=DROPPED_KEPT)
_counts: Dict[str, int] = {}
STARTED = datetime.now(timezone.utc).isoformat()


def note(source: str, category: str, event_type: str, why: str,
         application: str = "") -> None:
    """One record kept out, by `why` (a key of `WHY`). The agent's own files
    are only counted: thousands an hour, they would bury the rest."""
    with _lock:
        _counts[why] = _counts.get(why, 0) + 1
        if why == "agents_own":
            return
        _dropped.appendleft({"at": datetime.now(timezone.utc).isoformat(),
                             "source": str(source or ""), "category": str(category or ""),
                             "type": str(event_type or ""), "why": why,
                             "application": str(application or "")[:120]})


def recent(limit: int = 100) -> List[Dict[str, Any]]:
    with _lock:
        return list(_dropped)[:max(0, int(limit))]


def counts() -> Dict[str, int]:
    with _lock:
        return dict(_counts)


def reset() -> None:
    """For a test."""
    with _lock:
        _dropped.clear()
        _counts.clear()


__all__ = ["note", "recent", "counts", "reset", "WHY", "STARTED", "DROPPED_KEPT"]
