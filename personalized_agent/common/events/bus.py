"""Thread-safe in-process publish/subscribe bus.

Modules communicate through named topics instead of importing each other.
A failing subscriber never breaks the publisher or the other subscribers
(sections 2.1 and 23 - error isolation).
"""

from __future__ import annotations

import fnmatch
import logging
import threading
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Tuple

log = logging.getLogger(__name__)

Subscriber = Callable[[str, Any], None]


class EventBus:
    def __init__(self, history_size: int = 500) -> None:
        self._lock = threading.RLock()
        self._subscribers: List[Tuple[str, Subscriber]] = []
        self._history: Deque[Tuple[str, Any]] = deque(maxlen=history_size)

    def subscribe(self, pattern: str, callback: Subscriber) -> Callable[[], None]:
        """Subscribe to a topic glob, e.g. ``listener.*`` or ``model.**``."""
        with self._lock:
            entry = (pattern, callback)
            self._subscribers.append(entry)

        def unsubscribe() -> None:
            with self._lock:
                if entry in self._subscribers:
                    self._subscribers.remove(entry)

        return unsubscribe

    def publish(self, topic: str, payload: Any = None) -> None:
        with self._lock:
            self._history.append((topic, payload))
            targets = [cb for pattern, cb in self._subscribers if fnmatch.fnmatch(topic, pattern)]
        for cb in targets:
            try:
                cb(topic, payload)
            except Exception:  # isolation: one bad subscriber must not break the rest
                log.exception("event subscriber failed for topic %s", topic)

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._history)[-limit:]
        return [{"topic": t, "payload": p} for t, p in items]
