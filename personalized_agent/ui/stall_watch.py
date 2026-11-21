"""The window's thread standing still, said in the log with what it was doing.

(The user, 2026-11-16: "我在chat里打字很慢，发送了点击半天发不出去" - and nothing in the log said why.) Tk owns one
thread; while it is held - a write waiting for the database, a picture taken, a slow call - no key, no
click, no redraw is seen. Here Tk beats every BEAT_SECONDS; a watcher on a thread of its own looks, and once
the thread has missed beats for SAID_AFTER_SECONDS it takes down where that thread is, and says it - with how
long it stood - when the thread beats again.

The computer asleep is not the thread held: the watcher, asleep too, sees its own wait run long and lets it go.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import traceback
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

#: How often Tk beats.
BEAT_SECONDS = 0.25
#: Held this long, the thread is said to stand still.
SAID_AFTER_SECONDS = 0.5
#: At most one line in the log this often - the rest counted into the next.
SAID_EVERY_SECONDS = 10.0
#: The watcher's own wait running this much longer than asked: the computer slept, not the thread.
ASLEEP_SECONDS = 2.0
#: WHERE THE THREAD'S TIME GOES (2026-11-16: the window's thread used a fifth of a core and the avatar's
#: drawing explained a quarter of it): at each look, what the thread is running, if anything; this often,
#: the most of it said.
TIME_SAID_EVERY_SECONDS = 600.0
TIME_SAID_MOST = 6

_HERE = os.path.normcase(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) + os.sep


def stack_of(frame: Any) -> Any:
    """A stack's frames, outermost first, as traceback.extract_stack gives them - WITHOUT THEIR LINES READ (2026-11-20,
    the memory looked at: each look read every source file on the stack into Python's line cache, 15 MB of the
    application's own code kept for good; a file, a line number and a function are all that is said)."""
    summary = traceback.StackSummary.extract(traceback.walk_stack(frame), lookup_lines=False)
    summary.reverse()
    return summary


def where(frame: Any, deepest: int = 8) -> str:
    """The project's own frames of a stack, innermost first: "file.py:12 function < ..." ("" for none)."""
    if frame is None:
        return ""
    stack = stack_of(frame)
    frames = [one for one in stack
              if os.path.normcase(os.path.abspath(one.filename)).startswith(_HERE)
              and "site-packages" not in one.filename]
    outermost = stack[-1:] if not frames else []
    return " < ".join(f"{os.path.basename(one.filename)}:{one.lineno} {one.name}"
                      for one in reversed((frames or outermost)[-deepest:]))


#: What a thread waiting - on a lock, a queue, the clock, a socket, a pipe - is found in: not running.
WAITING_IN = frozenset({"wait", "sleep", "acquire", "select", "recv", "recv_into", "readline", "read", "accept",
                        "get", "_wait_for_tstate_lock", "join", "serve_forever", "poll", "_recv", "readinto",
                        "_read_status", "communicate", "mainloop", "WaitForSingleObject", "wait_for"})


def others_running(frames: dict, but: Optional[int], most: int = 4) -> str:
    """THE THREADS RUNNING WHILE THE WINDOW'S STOOD STILL (2026-11-16: a frame of 25 ms stood 0.7-1.2 s - the
    window's thread waiting for Python's lock, held by another): each thread not found waiting, by its name and
    where it is - the one holding the lock among them."""
    names = {one.ident: one.name for one in threading.enumerate()}
    said = []
    for ident, frame in frames.items():
        if ident == but or ident == threading.get_ident() or frame is None:
            continue
        if frame.f_code.co_name in WAITING_IN:
            continue
        said.append(f"{names.get(ident, ident)}: {where(frame, deepest=2)}")
    return "; ".join(said[:most]) + (f"; {len(said) - most} more" if len(said) > most else "")


def waiting_on_tk(frame: Any) -> bool:
    """Whether the thread is waiting for Tk's next event - not running anything: its innermost frame Tk's own
    loop (no frame at all, likewise)."""
    code = getattr(frame, "f_code", None)
    if code is None:
        return True
    return code.co_name == "mainloop" and "tkinter" in os.path.normcase(code.co_filename)


class StallWatch:
    """Started on Tk's thread with its root; `stop` ends it."""

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 frames: Callable[[], dict] = sys._current_frames,
                 say: Optional[Callable[[str], None]] = None) -> None:
        self.clock = clock
        self.frames = frames
        self.say = say or (lambda line: log.warning("%s", line))
        self.thread_id: Optional[int] = None
        self.last_beat = clock()
        self.stuck_at: Optional[str] = None       # where the thread was, once it stood still
        self.said_at = -SAID_EVERY_SECONDS
        self.still_said_at = -SAID_EVERY_SECONDS * 6       # when a stand-still still going was said
        self.unsaid = 0
        self.longest_unsaid = 0.0
        self._going = threading.Event()
        self._root: Any = None
        #: What the thread was found running at each look ("" for waiting on Tk), since it was last said.
        self.running: dict = {}
        self.looks = 0
        self.time_said_at = clock()

    # -- on Tk's thread ------------------------------------------------------------------------
    def start(self, root: Any) -> None:
        self.thread_id = threading.get_ident()
        self._root = root
        self.last_beat = self.clock()
        self._going.set()
        root.after(int(BEAT_SECONDS * 1000), self._beat_and_again)
        threading.Thread(target=self._watch, name="window-stall-watch", daemon=True).start()

    def stop(self) -> None:
        self._going.clear()

    def _beat_and_again(self) -> None:
        if not self._going.is_set():
            return
        self.beat()
        try:
            self._root.after(int(BEAT_SECONDS * 1000), self._beat_and_again)
        except Exception:                          # the window gone
            self.stop()

    def beat(self) -> None:
        """Tk is free: a stand-still that ended is said."""
        now = self.clock()
        stood = now - self.last_beat - BEAT_SECONDS
        self.last_beat = now
        if self.stuck_at is None:
            return
        at, self.stuck_at = self.stuck_at, None
        if stood < SAID_AFTER_SECONDS:
            return
        if now - self.said_at < SAID_EVERY_SECONDS:
            self.unsaid += 1
            self.longest_unsaid = max(self.longest_unsaid, stood)
            return
        more = (f" ({self.unsaid} more since the last said, the longest {self.longest_unsaid:.1f} s)"
                if self.unsaid else "")
        self.say(f"the window's thread stood still for {stood:.1f} s{more}: {at or '?'}")
        self.said_at, self.unsaid, self.longest_unsaid = now, 0, 0.0

    # -- on the watcher's thread -----------------------------------------------------------------
    def look(self, waited_too_long: bool = False) -> None:
        """One look: what the thread is running counted; past SAID_AFTER_SECONDS without a beat, where it
        is taken down once."""
        if waited_too_long:                        # the computer slept: nothing stood still
            self.last_beat = self.clock()
            self.stuck_at = None
            return
        if self.thread_id is None:
            return
        frames = self.frames()
        frame = frames.get(self.thread_id)
        self.count(frame)
        if self.stuck_at is not None:
            # STILL STANDING STILL, SAID WHILE IT STANDS (2026-11-16: the avatar froze for good - Tk's thread waiting
            # in Thread.start() - and nothing was said, a stand-still being said only once it ended).
            stood = self.clock() - self.last_beat - BEAT_SECONDS
            if stood >= SAID_EVERY_SECONDS and self.clock() - self.still_said_at >= SAID_EVERY_SECONDS * 6:
                self.still_said_at = self.clock()
                self.say(f"the window's thread has stood still for {stood:.0f} s and still does: {self.stuck_at}")
            return
        if self.clock() - self.last_beat - BEAT_SECONDS >= SAID_AFTER_SECONDS:
            others = ""
            try:
                others = others_running(frames, self.thread_id)
            except Exception:                      # pragma: no cover - said without them
                pass
            self.stuck_at = where(frame) + (f" | running beside it: {others}" if others else "")

    def count(self, frame: Any) -> None:
        """What the thread runs, counted by its innermost two of the project's own frames - none past
        the loop that waits on Tk is the thread waiting; and every TIME_SAID_EVERY_SECONDS, the most of
        it said."""
        running = "" if waiting_on_tk(frame) else " < ".join(where(frame, deepest=2).split(" < ")[:2])
        self.running[running] = self.running.get(running, 0) + 1
        self.looks += 1
        now = self.clock()
        if now - self.time_said_at < TIME_SAID_EVERY_SECONDS:
            return
        busy = sorted(((n, at) for at, n in self.running.items() if at), reverse=True)[:TIME_SAID_MOST]
        if self.looks and busy:
            share = 100.0 * sum(n for at, n in self.running.items() if at) / self.looks
            self.say_time(f"the window's thread was running {share:.0f}% of the looks in "
                          f"{(now - self.time_said_at) / 60:.0f} min: " +
                          "; ".join(f"{100.0 * n / self.looks:.1f}% {at}" for n, at in busy))
        self.running, self.looks, self.time_said_at = {}, 0, now

    def say_time(self, line: str) -> None:
        log.info("%s", line)

    def _watch(self) -> None:
        step = BEAT_SECONDS / 2
        while self._going.is_set():
            asked = time.monotonic()
            time.sleep(step)
            self.look(waited_too_long=time.monotonic() - asked > step + ASLEEP_SECONDS)
