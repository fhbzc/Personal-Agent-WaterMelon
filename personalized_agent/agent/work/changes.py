"""WHAT CODE CHANGED, HEARD AS IT CHANGES - not found by walking the
folder before and after (the user, 2026-10-29).

What a tool's code changed is told the agent ("files_changed"), kept in
the run's record of what it changed, and marked the agent's - not the
user's - for the listener. It was found by looking at every file of the
folders the work may write, before the code and after: up to 20,000
files twice a step, and with a replication's seven virtual environments
in its folder, about half a minute of every step.

Now the system says what changed while the code runs - the same
notifications the listener hears the user's folders by (watchdog: one
watch a folder and all under it) - from Python and from any other program
alike. Where a watch cannot be made, the walk is the fallback, with the
installed environments left out of it (environments.py).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional, Sequence, Set

log = logging.getLogger(__name__)

#: After the code ends, the notifications still on their way are waited
#: for this long before the watch is closed.
SETTLE_SECONDS = 0.3
#: HEARD TO THE END, NOT WAITED A WHILE FOR (2026-11-02: on a busy build
#: machine a file a command had just written was told after the 0.3 s - and
#: missed): a mark is written where the work keeps its own runtime and
#: heard back - a watched folder tells its changes in order, so all before
#: it are in. Waited for this long at most.
FLUSH_MOST_SECONDS = 5.0

try:                                                   # pragma: no branch
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    WATCHING = True
except Exception:                                      # pragma: no cover - the walk then
    FileSystemEventHandler = object                    # type: ignore
    Observer = None                                    # type: ignore
    WATCHING = False


class _Heard(FileSystemEventHandler):                  # type: ignore[misc]
    def __init__(self, paths: Set[str], lock: threading.Lock) -> None:
        self.paths, self.lock = paths, lock

    def on_any_event(self, event) -> None:             # pragma: no cover - by the system
        if getattr(event, "is_directory", False) or getattr(event, "event_type", "") in (
                "opened", "closed_no_write"):
            return
        with self.lock:
            for path in (getattr(event, "src_path", ""), getattr(event, "dest_path", "")):
                if path:
                    self.paths.add(str(path))


class Watch:
    """The folders the work may write, heard while code runs: `stop()` says
    which files were made, changed or removed - or None when the watch
    could not be made, for the walk to be used instead."""

    def __init__(self, roots: Sequence[Path],
                 left_out: Callable[[str], bool] = lambda _path: False,
                 flush_in: Optional[Path] = None) -> None:
        self.roots = [Path(one) for one in roots if Path(one).is_dir()]
        self.left_out = left_out
        #: Where the end's mark may be written: the work's own runtime folder
        #: (never one of the user's), inside a watched folder.
        self.flush_in = Path(flush_in) if flush_in else None
        self._paths: Set[str] = set()
        self._lock = threading.Lock()
        self._observer = None

    def start(self) -> bool:
        if not WATCHING or not self.roots:
            return False
        try:
            observer = Observer()
            handler = _Heard(self._paths, self._lock)
            for root in self.roots:
                observer.schedule(handler, str(root), recursive=True)
            observer.daemon = True
            observer.start()
        except Exception:
            log.debug("could not watch %s - walked instead", self.roots, exc_info=True)
            return False
        self._observer = observer
        return True

    def stop(self) -> Optional[Set[str]]:
        observer = self._observer
        if observer is None:
            return None
        mark = self._flush()
        if mark is None or len(self.roots) > 1:
            time.sleep(SETTLE_SECONDS)       # the folders no mark was heard back from
        try:
            observer.stop()
            observer.join(5)
        except Exception:                              # pragma: no cover - heard so far
            log.debug("the watch did not close cleanly", exc_info=True)
        self._observer = None
        with self._lock:
            heard = set(self._paths)
        out: Set[str] = set()
        for path in heard:
            if self.left_out(path) or (mark is not None and Path(path).name == mark.name):
                continue
            target = Path(path)
            # A file made, changed or gone - not a folder.
            if target.is_dir():
                continue
            out.add(str(target))
        return out

    def _flush(self) -> Optional[Path]:
        """The mark written and heard back - its path - or None, where none
        could be: no runtime folder in a watched one, or not heard in time.
        Known by its name, which no other file has: a folder may be told in
        another spelling of its path (a short name on Windows)."""
        where = self.flush_in
        if where is None or not any(root == where or root in where.parents
                                    for root in self.roots):
            return None
        mark = where / f".watch-end-{uuid.uuid4().hex[:12]}"
        try:
            where.mkdir(parents=True, exist_ok=True)
            mark.write_bytes(b"")
        except OSError:
            return None
        heard = False
        until = time.monotonic() + FLUSH_MOST_SECONDS
        try:
            while time.monotonic() < until:
                with self._lock:
                    heard = any(Path(path).name == mark.name for path in self._paths)
                if heard:
                    break
                time.sleep(0.02)
        finally:
            try:
                mark.unlink()
            except OSError:
                pass
        return mark if heard else None


__all__ = ["Watch", "WATCHING", "SETTLE_SECONDS", "FLUSH_MOST_SECONDS"]
