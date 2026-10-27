"""TOOL CALLS SIDE BY SIDE - the light, reading ones, and only while the
computer has room (the user, 2026-10-24).

A model often asks for several files, folders or searches in one turn; run
one after another, each waits for the one before. Here the READING TOOLS
(`READ_ONLY`) may run together, at most `MOST_SIDE_BY_SIDE` at once, while
everything else - writing, deleting, running code, waiting for a job,
installing, the web - runs alone, with nothing beside it.

Within the computer's limits, as the work governor (governor.py) keeps
them: while the processors are busy (`BUSY_CPU` or more) or memory is short,
the reading tools go one at a time as well.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager, nullcontext
from typing import Any, Callable, Iterator

from .governor import BUSY_CPU, SHORT_MEMORY, _psutil

#: The tools that only read: run side by side with one another.
READ_ONLY = frozenset({"list_dir", "read_file", "find_files", "search_files",
                       "search_records", "search_stages", "open_card", "open_stage",
                       "open_case", "open_reason"})
#: The tools that only WAIT - on a job, on a moment, on a helper agent: they
#: take no place at all, so that nothing waits behind a wait (a helper's
#: tools, a job's end being looked at).
WAITING = frozenset({"job_status", "wait_for", "stop_job", "delegate"})
#: At most this many at once.
MOST_SIDE_BY_SIDE = 4
#: The computer looked at no more often than this.
LOOK_EVERY = 2.0

_looked = {"at": 0.0, "busy": False}
_look_lock = threading.Lock()


def machine_busy() -> bool:
    """Whether the computer is busy now, as the governor counts busy: its
    processors at `BUSY_CPU` or more, or its memory short."""
    with _look_lock:
        if time.monotonic() - _looked["at"] < LOOK_EVERY:
            return bool(_looked["busy"])
        ps = _psutil()
        busy = False
        if ps is not None:
            try:
                memory = ps.virtual_memory()
                busy = float(ps.cpu_percent(interval=None)) >= BUSY_CPU or (
                    memory.total and memory.available / memory.total < SHORT_MEMORY)
            except Exception:                  # pragma: no cover - not known: not busy
                busy = False
        _looked.update(at=time.monotonic(), busy=bool(busy))
        return bool(busy)


class ToolGate:
    """Reading tools share it, up to `most` at once while the computer has
    room, one while it is busy; any other tool has it alone."""

    def __init__(self, most: int = MOST_SIDE_BY_SIDE,
                 busy: Callable[[], bool] = machine_busy) -> None:
        self.most = max(1, int(most))
        self.busy = busy
        self._cond = threading.Condition()
        self._readers = 0
        self._writer = False

    @contextmanager
    def reading(self) -> Iterator[None]:
        with self._cond:
            while self._writer or self._readers >= (1 if self.busy() else self.most):
                self._cond.wait(0.5)
            self._readers += 1
        try:
            yield
        finally:
            with self._cond:
                self._readers -= 1
                self._cond.notify_all()

    @contextmanager
    def alone(self) -> Iterator[None]:
        with self._cond:
            while self._writer or self._readers:
                self._cond.wait(0.5)
            self._writer = True
        try:
            yield
        finally:
            with self._cond:
                self._writer = False
                self._cond.notify_all()

    def hold(self, name: str) -> Any:
        if name in WAITING:
            return nullcontext()
        return self.reading() if name in READ_ONLY else self.alone()


__all__ = ["READ_ONLY", "WAITING", "MOST_SIDE_BY_SIDE", "ToolGate", "machine_busy"]
