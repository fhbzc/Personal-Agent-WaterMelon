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
from typing import Any, Callable, Dict, Iterator, List, Sequence, Tuple

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


# -- MADE FOR A MODULE, NOT BY THE USER (the user, 2026-11-10: the work the day's plan starts by
# itself counted under the plan, not under "Started by you", so that turned off its tokens are
# gone). Three ways a call is known to be such: the process it runs in was started for it (a
# work's own process, agent/work/child.py), the thread it runs on said so (`on_behalf`), or the run
# or draft it names in its metadata was marked (`mark`) - a run going in this process.
_process_module = [""]
_marked: Dict[Tuple[str, str], str] = {}
_here = threading.local()


def for_this_process(module: str) -> None:
    """Every call this process makes is made for `module` ("" for none)."""
    _process_module[0] = str(module or "")


def mark(kind: str, ident: str, module: str) -> None:
    """The calls naming this run or draft (`metadata["run"]` / `["draft"]`) are `module`'s."""
    if ident and module:
        with _lock:
            _marked[(str(kind), str(ident))] = str(module)


@contextmanager
def on_behalf(module: str) -> Iterator[None]:
    """The calls made on this thread meanwhile are `module`'s."""
    before = getattr(_here, "module", "")
    _here.module = str(module or "") or before
    try:
        yield
    finally:
        _here.module = before


def module_for(request: Any) -> str:
    """The module a call was made for, where that was said; "" when it was not."""
    here = getattr(_here, "module", "")
    if here:
        return here
    said = getattr(request, "metadata", None) or {}
    if isinstance(said, dict):
        with _lock:
            for kind in ("run", "draft"):
                ident = str(said.get(kind) or "")
                if ident and (kind, ident) in _marked:
                    return _marked[(kind, ident)]
    return _process_module[0]


def clear() -> None:
    """No window open, nothing marked - for a test."""
    with _lock:
        _open.clear()
        _marked.clear()
    _process_module[0] = ""
    _here.module = ""


__all__ = ["while_running", "now", "by_user", "clear", "STARTING", "for_this_process", "mark",
           "on_behalf", "module_for"]
