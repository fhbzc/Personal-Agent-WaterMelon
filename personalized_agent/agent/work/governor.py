"""HOW MUCH OF THE COMPUTER THE WORK MAY HAVE: decided from what is free now.

Runs of the user's work go each in a process of their own (child.py), at a
lower priority than the agent's. Every few seconds the governor looks at the
computer - how busy its processors are, how much memory is free - and:

* says how many runs may go at once: about one for every four processor
  cores and every 1.5 GB free, never more than four, and always one when
  none is going and the memory allows it;
* keeps a run asked for while there is no room WAITING, and starts it when
  there is - the first asked, the first started;
* while the processors are busy (85% or more, over the last few looks),
  lowers the runs - every process of each - to the lowest priority, so the
  user's own programs come first; back to below normal once it is calm;
* while memory is short (under 8% free), PAUSES the newest run, saying why,
  and resumes it once a fifth is free again.

Nothing here touches a run of the Experience debugger: those stay in the
agent's own process, for whoever develops it.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

#: How often it looks.
LOOK_SECONDS = 5.0
#: Busy: the processors at least this busy, on average over the last looks.
BUSY_CPU = 85.0
CALM_CPU = 60.0
#: Short of memory: under this share free - a run paused; back over the
#: other, resumed.
SHORT_MEMORY = 0.08
ENOUGH_MEMORY = 0.20
#: A run's share: this much free memory for each run going.
GB_PER_RUN = 1.5
MOST_RUNS = 4


def _psutil() -> Any:
    try:
        import psutil
        return psutil
    except Exception:                          # pragma: no cover - required anyway
        return None


class Governor:
    """Looks at the computer and keeps the runs within it."""

    def __init__(self, work: Any) -> None:
        self.work = work
        self.samples: deque = deque(maxlen=3)
        self.free_gb = 4.0
        self.free_share = 0.5
        self.lowered = False
        #: Runs the governor paused for memory, to resume when it is back.
        self.paused_by_me: List[str] = []
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.look()

    # -- what it sees -----------------------------------------------------------------------
    def look(self) -> Dict[str, Any]:
        ps = _psutil()
        if ps is None:
            return self.seen()
        try:
            self.samples.append(float(ps.cpu_percent(interval=None)))
            memory = ps.virtual_memory()
            self.free_gb = memory.available / 2 ** 30
            self.free_share = memory.available / max(1, memory.total)
        except Exception:                      # pragma: no cover - seen next time
            log.debug("could not look at the computer", exc_info=True)
        return self.seen()

    def busy(self) -> bool:
        return bool(self.samples) and sum(self.samples) / len(self.samples) >= BUSY_CPU

    def seen(self) -> Dict[str, Any]:
        cpu = round(sum(self.samples) / len(self.samples), 1) if self.samples else None
        return {"cpu": cpu, "free_gb": round(self.free_gb, 2),
                "free_share": round(self.free_share, 3), "most_runs": self.most_runs(),
                "busy": self.busy(), "lowered": self.lowered}

    def most_runs(self) -> int:
        """How many runs may go at once, now."""
        cores = os.cpu_count() or 2
        by_memory = int(self.free_gb // GB_PER_RUN)
        return max(0, min(MOST_RUNS, max(1, cores // 4), by_memory))

    def room(self, going: int) -> Tuple[bool, str]:
        """Whether one more run may start now - and, when not, why."""
        if self.free_gb < 0.75:
            return False, "the computer is short of memory"
        if going == 0:
            return True, ""                    # one may always go, memory allowing
        if self.busy():
            return False, "the computer is busy"
        if going >= max(1, self.most_runs()):
            return False, f"{going} runs are going - as many as the computer has room for"
        return True, ""

    # -- what it does ----------------------------------------------------------------------
    def steer(self) -> None:
        """One look: the runs' priority, a run paused or resumed for memory,
        and a waiting run started when there is room."""
        self.look()
        children = [run for run in list(self.work._running.values())
                    if getattr(run, "pid", 0) and run.alive()]
        busy = self.busy()
        calm = bool(self.samples) and sum(self.samples) / len(self.samples) <= CALM_CPU
        if busy and not self.lowered:
            self._priority(children, lowest=True)
            self.lowered = True
        elif calm and self.lowered:
            self._priority(children, lowest=False)
            self.lowered = False
        if self.free_share < SHORT_MEMORY and children:
            newest = max(children, key=lambda run: getattr(run, "started_at", 0))
            if newest.id not in self.paused_by_me and newest.pause(
                    why="the computer is short of memory"):
                self.paused_by_me.append(newest.id)
        elif self.free_share > ENOUGH_MEMORY and self.paused_by_me:
            for run_id in list(self.paused_by_me):
                run = self.work._running.get(run_id)
                if run is not None and run.alive():
                    run.resume(why="memory is free again")
                self.paused_by_me.remove(run_id)
        self.work.start_waiting()

    @staticmethod
    def _priority(children: List[Any], lowest: bool) -> None:
        ps = _psutil()
        if ps is None:
            return
        if sys.platform == "win32":
            level = ps.IDLE_PRIORITY_CLASS if lowest else ps.BELOW_NORMAL_PRIORITY_CLASS
        else:
            level = 19 if lowest else 5
        for run in children:
            try:
                parent = ps.Process(run.pid)
                for process in [parent, *parent.children(recursive=True)]:
                    try:
                        process.nice(level)
                    except Exception:          # gone, or not ours to change
                        pass
            except Exception:
                continue

    # -- the loop ---------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="work-governor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(LOOK_SECONDS):
            try:
                self.steer()
            except Exception:                  # pragma: no cover - next look
                log.warning("the governor could not look", exc_info=True)


__all__ = ["Governor", "BUSY_CPU", "GB_PER_RUN"]
