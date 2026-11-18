"""What the window's thread would write, written on a thread of its own.

THE WINDOW NEVER WAITS FOR THE DATABASE (the user, 2026-11-16: "我在chat里打字很慢，发送了点击半天发不出去").
The chat bubble kept its draft as it was typed, and once more as it was sent, with a write on Tk's own
thread - and every write in the application waits its turn behind one lock, a work's own process holding
SQLite's for up to 30 s. While another write held it (the old WeChat rows cleared in one of 6.9 s), the
avatar and the bubble stood still: keys and clicks waited in line.

So a write is handed over here and the window goes on. For a key only the newest value is written - a
draft typed on while the one before waits is kept as it now is, not as it was. What is still to be written
is read back from here first, and written before the application closes.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional

log = logging.getLogger(__name__)

_MISSING = object()


class LaterWrites:
    """(key, value) written by `write` on one thread of its own, in turn; for a key, its newest value."""

    def __init__(self, write: Callable[[str, Any], None], name: str = "window-writes") -> None:
        self._write = write
        self._name = name
        self._waiting: Dict[str, Any] = {}
        #: Taken to be written and not written yet: still what is read back.
        self._writing: Dict[str, Any] = {}
        self._busy = 0
        self._lock = threading.Condition()
        self._thread: Optional[threading.Thread] = None

    def put(self, key: str, value: Any) -> None:
        """Handed over: returns at once."""
        with self._lock:
            self._waiting.pop(key, None)            # the newest goes to the end
            self._waiting[key] = value
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
                self._thread.start()
            self._lock.notify_all()

    def waiting(self, key: str, default: Any = _MISSING) -> Any:
        """The value handed over for `key` and not yet written - waiting, or being written - or `default`."""
        with self._lock:
            if key in self._waiting:
                return self._waiting[key]
            return self._writing.get(key, default)

    def is_waiting(self, key: str) -> bool:
        with self._lock:
            return key in self._waiting or key in self._writing

    def flush(self, seconds: float = 5.0) -> bool:
        """Waits until all that was handed over is written, or `seconds` pass. True when all is."""
        with self._lock:
            return self._lock.wait_for(lambda: not self._waiting and not self._busy, timeout=seconds)

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._waiting:
                    self._thread = None
                    self._lock.notify_all()
                    return
                key = next(iter(self._waiting))
                value = self._waiting.pop(key)
                self._writing[key] = value
                self._busy += 1
            try:
                self._write(key, value)
            except Exception:                      # one write's trouble is not the next one's
                log.warning("what the window handed over (%s) could not be written", key, exc_info=True)
            finally:
                with self._lock:
                    if self._writing.get(key) is value:
                        del self._writing[key]
                    self._busy -= 1
                    self._lock.notify_all()
