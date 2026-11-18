"""What the side panel is told the moment it happens - pushed, not asked for.

THE SIDE PANEL IS NOT POLLED (the user, 2026-11-16: the sidebar "一定要快 ... 并且不拖慢电脑整体的速度"). The page
asked for the waiting list every 8 s, and for a chat turn every 300 ms while it was answered. Now it holds one
connection open (GET /api/side/stream, Server-Sent Events) and is told what changed - the list ("notes"), a
chat turn ("turn", with its id) - and fetches only that.

Each thing told has a number; a page that was away for a moment says the last it had and is told what it
missed (a few dozen are kept). What is not told from where it happens - the waiting list is put together from
the notifications, the approvals, the recommendations - is found by its look (`watch`): while a side panel is
listening, and only then, the list's fingerprint is read every WATCH_SECONDS and told when it changes.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Iterator, List, Optional, Tuple

#: Things told and kept for a page that comes back.
KEPT = 64
#: Said this often when nothing happens, so a connection gone is found and let go.
KEEP_ALIVE_SECONDS = 25.0
#: While a page listens: how often what is not told from where it happens is looked at.
WATCH_SECONDS = 2.0


class SideEvents:
    def __init__(self) -> None:
        self._said = threading.Condition()
        self._seq = 0
        self._kept: Deque[Tuple[int, str, Dict[str, Any]]] = deque(maxlen=KEPT)
        self._listening = 0
        self._watched: List[Tuple[str, Callable[[], Any], List[Any]]] = []
        self._watcher: Optional[threading.Thread] = None
        self.stopped = threading.Event()

    # -- told ----------------------------------------------------------------------------------------------
    def tell(self, kind: str, **data: Any) -> int:
        """`kind` happened: every page listening is told at once. Its number."""
        with self._said:
            self._seq += 1
            self._kept.append((self._seq, kind, data))
            self._said.notify_all()
            return self._seq

    @property
    def last(self) -> int:
        with self._said:
            return self._seq

    def after(self, seq: int, seconds: float) -> List[Tuple[int, str, Dict[str, Any]]]:
        """What was told after `seq` - waited for up to `seconds` when nothing was."""
        end = time.monotonic() + seconds
        with self._said:
            while True:
                newer = [one for one in self._kept if one[0] > seq]
                if newer or self.stopped.is_set():
                    return newer
                left = end - time.monotonic()
                if left <= 0:
                    return []
                self._said.wait(left)

    # -- what is found by its look --------------------------------------------------------------------------
    def watch(self, kind: str, look: Callable[[], Any]) -> None:
        """`kind` told whenever what `look()` says changes - looked at only while a page listens."""
        self._watched.append((kind, look, [None]))

    def _watch(self) -> None:
        for _kind, look, seen in self._watched:            # where things stand when a page starts listening
            try:
                seen[0] = look()
            except Exception:
                seen[0] = None
        while not self.stopped.wait(WATCH_SECONDS):
            with self._said:
                if not self._listening:
                    self._watcher = None
                    return
            for kind, look, seen in self._watched:
                try:
                    now = look()
                except Exception:                           # looked at again next time
                    continue
                if now != seen[0]:
                    seen[0] = now
                    self.tell(kind)

    # -- one page's connection --------------------------------------------------------------------------------
    def stream(self, since: int = 0) -> Iterator[bytes]:
        """One listening page's events, as Server-Sent Events: what it missed since `since`, then each as it
        is told, a keep-alive between - until it goes or the application stops. Where it starts is taken
        now, as it is asked for, not when the first piece is read: told in between, it is not missed."""
        with self._said:
            self._listening += 1
            if self._watched and self._watcher is None:
                self._watcher = threading.Thread(target=self._watch, name="side-events-watch", daemon=True)
                self._watcher.start()
            last = since if 0 < since <= self._seq else self._seq
        return self._pieces(last)

    def _pieces(self, last: int) -> Iterator[bytes]:
        try:
            yield b"retry: 2000\n\n"
            yield f"event: hello\ndata: {json.dumps({'last': last})}\n\n".encode("utf-8")
            while not self.stopped.is_set():
                told = self.after(last, KEEP_ALIVE_SECONDS)
                if not told:
                    yield b": still here\n\n"
                    continue
                for seq, kind, data in told:
                    last = seq
                    yield (f"id: {seq}\nevent: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                           ).encode("utf-8")
        finally:
            with self._said:
                self._listening -= 1

    @property
    def listening(self) -> int:
        with self._said:
            return self._listening

    def stop(self) -> None:
        self.stopped.set()
        with self._said:
            self._said.notify_all()
