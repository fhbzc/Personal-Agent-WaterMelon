"""WHO STARTED IT: the user, with a button, or the agent by itself.

A function that runs by itself can also be run with a button - an issue
made now, the lessons learned now, the heartbeat run now. Its calls carry
the same task either way, so what the user started would be counted as the
function's own running cost, and the function would look dearer to keep on
than it is.

A function runs once at a time (each guards its own run), so while the
user's run of it is going, its calls are the user's. A button opens a
WINDOW for the tasks its run makes calls under, open while the run is -
`while_running` for one started on a thread of its own, `now` for one run
where the button waits - and each model call asks `by_user(task)` when it is
recorded.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator, List, Sequence, Tuple

_lock = threading.Lock()
#: Open windows: the task prefixes, whether the run is still going, and when
#: it was opened - a run on a thread is given a moment to begin.
_open: List[Tuple[Tuple[str, ...], Callable[[], bool], float]] = []
#: A run on a thread of its own counts as going this long after its button,
#: whatever its `alive` says - before it has begun, it has not ended.
STARTING = 5.0


def while_running(prefixes: Sequence[str], alive: Callable[[], bool]) -> None:
    """The user started a run on a thread of its own: its calls are theirs
    while `alive()` - and for the moment it takes to begin."""
    with _lock:
        _open.append((tuple(prefixes), alive, time.monotonic()))


@contextmanager
def now(prefixes: Sequence[str]) -> Iterator[None]:
    """The user started a run that is done before the button returns."""
    going = [True]
    while_running(prefixes, lambda: going[0])
    try:
        yield
    finally:
        going[0] = False


def by_user(task: str) -> bool:
    """Whether a call under `task` is made by a run the user started."""
    task = str(task or "")
    moment = time.monotonic()
    with _lock:
        alive = []
        for prefixes, going, opened in _open:
            try:
                still = moment - opened < STARTING or bool(going())
            except Exception:                    # pragma: no cover - a closed run
                still = False
            if still:
                alive.append((prefixes, going, opened))
        _open[:] = alive
        return any(task.startswith(prefix) for prefixes, _, _ in alive for prefix in prefixes)


def clear() -> None:
    """No window open - for a test."""
    with _lock:
        _open.clear()


__all__ = ["while_running", "now", "by_user", "clear", "STARTING"]
