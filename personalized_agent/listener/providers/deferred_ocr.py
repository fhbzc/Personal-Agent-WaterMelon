"""WECHAT'S PICTURE NOW, ITS WORDS LATER (the user, 2026-10-30).

Recognising the words in a picture of WeChat takes a second or more on a
Mac (Apple Vision) and on Linux (RapidOCR), and done as the window was read
it held the reading up: what the user was shown next waited behind it, and
the time read often windows are allowed went to recognition. So the picture
is taken when the window is read - verified in front, and allowed, at that
moment - and its words are recognised here, one picture after another, on a
thread of their own. The reading keeps its place: its event carries the
moment the picture was taken, and the summaries do not read past a picture
still waiting (`oldest_waiting`, the agent heartbeat's window), so a late
one is never left behind.

The pictures are held in memory only, never written anywhere, and at most
MOST_WAITING at once: past that the oldest is let go and said to be so - a
reading of WeChat that could not be recognised in time - rather than lost
without a word. What is still waiting when the agent closes is let go with
it. Windows reads WeChat's words from WeChat itself and waits for nothing.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime
from typing import Any, Callable, Deque, Dict, Optional, Tuple

from ...common.events.models import parse_iso, utcnow

log = logging.getLogger(__name__)

#: How many pictures may wait at once. TO BE MEASURED ON A MAC (the user,
#: 2026-10-30): how long recognition takes there against how often WeChat
#: is read decides whether twenty is too few or more than needed.
MOST_WAITING = 20

#: Every picture waiting, in every waiting room: when it was taken.
_holds: Dict[int, datetime] = {}
_holds_lock = threading.Lock()


def oldest_waiting() -> Optional[datetime]:
    """When the oldest picture still waiting for its words was taken - or
    None: what the summaries may not read past."""
    with _holds_lock:
        return min(_holds.values()) if _holds else None


def _hold(token: int, when: datetime) -> None:
    with _holds_lock:
        _holds[token] = when


def _release(token: int) -> None:
    with _holds_lock:
        _holds.pop(token, None)


class DeferredOcr:
    """Pictures waiting for their words, recognised one after another.

    `deliver(done, waiting, dropped)` is told of each: `done` the reading
    with its words (None when they could not be had), `waiting` the reading
    put in, `dropped` when it was let go for room."""

    def __init__(self, deliver: Callable[[Any, Any, bool], None],
                 most: int = MOST_WAITING) -> None:
        self.deliver = deliver
        self.most = max(1, int(most))
        self._queue: Deque[Tuple[int, Any]] = deque()
        self._ready = threading.Condition()
        self._thread: Optional[threading.Thread] = None
        self._stopping = False
        self.recognised = 0
        self.failed = 0
        self.dropped = 0
        self.most_seen = 0

    def put(self, waiting: Any) -> None:
        """A reading whose words are to come (`waiting.finish()`)."""
        token = id(waiting)
        let_go = None
        with self._ready:
            if self._stopping:
                return
            if len(self._queue) >= self.most:
                let_go = self._queue.popleft()
                self.dropped += 1
            self._queue.append((token, waiting))
            self.most_seen = max(self.most_seen, len(self._queue))
            _hold(token, parse_iso(getattr(waiting, "captured_at", "")) or utcnow())
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._work, name="wechat-ocr",
                                                daemon=True)
                self._thread.start()
            self._ready.notify()
        if let_go is not None:
            log.info("a picture of WeChat was let go before its words were read: "
                     "%d were waiting", self.most)
            try:
                self.deliver(None, let_go[1], True)
            finally:
                _release(let_go[0])

    def _work(self) -> None:
        while True:
            with self._ready:
                while not self._queue and not self._stopping:
                    self._ready.wait()
                if self._stopping:
                    return
                token, waiting = self._queue.popleft()
            try:
                try:
                    done = waiting.finish()
                except Exception:
                    log.warning("WeChat's words could not be read", exc_info=True)
                    done = None
                if done is None:
                    self.failed += 1
                else:
                    self.recognised += 1
                self.deliver(done, waiting, False)
            except Exception:
                log.warning("a reading of WeChat could not be taken in", exc_info=True)
            finally:
                # Only once it is taken in may the summaries read past it.
                _release(token)

    def stop(self) -> None:
        """Let everything waiting go: the pictures were never kept."""
        with self._ready:
            self._stopping = True
            for token, _waiting in self._queue:
                _release(token)
            self._queue.clear()
            self._ready.notify_all()

    def status(self) -> Dict[str, Any]:
        with self._ready:
            waiting = len(self._queue)
        return {"waiting": waiting, "most": self.most, "most_seen": self.most_seen,
                "recognised": self.recognised, "failed": self.failed,
                "let_go": self.dropped}
