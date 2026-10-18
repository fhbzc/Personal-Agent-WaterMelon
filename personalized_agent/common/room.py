"""How much work the agent may do at once without getting in the user's way.

The heartbeats' work is mostly model calls, and many of them do not depend
on each other - one project's summary and another's, one project's stages
and another's. Run one at a time they take as long as their sum; run all at
once they can take the machine from the person using it. This decides, each
time another call is about to start, how many may be going: never fewer
than one - one at a time is what the agent always did - and more only when
the computer has room for them AND room left over for the user.

What it reads, the same way on every platform and for every provider:

  - WHETHER THE USER IS AT THE COMPUTER: the keyboard or the mouse touched in
    the last two minutes. Where the platform cannot say, they are taken to be
    there. At the computer, at most two at once.
  - THE PROCESSOR, without the agent's own share: what everything else is
    using. Another call starts only if half of it stays free while the user
    is there, a quarter while they are away.
  - MEMORY: another call starts only if what is free after it - by what the
    calls going now actually take, measured from the processes the agent
    started (a local client such as Claude Code or Codex is a process of a
    couple of hundred megabytes; an API call is next to nothing) - stays
    above the larger of two gigabytes and a fifth of the machine.
  - THE BATTERY: unplugged, at most two; below thirty per cent, one.
  - THE PROVIDER: refused lately - a rate limit, a quota, a provider that
    could not be reached - one at a time for ten minutes.

It is asked before each call starts, not once: a call already running is
never stopped, but the next one waits while the user needs the machine.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

log = logging.getLogger(__name__)

#: How many at once, at most, unless the settings say otherwise.
MOST = 3

#: The user is at the computer if they touched it this recently.
AT_THE_COMPUTER_SECONDS = 120

#: The share of the processor left free for the user: while they are at the
#: computer, and while they are away.
CPU_LEFT_FREE_HERE = 0.50
CPU_LEFT_FREE_AWAY = 0.25

#: What one more call is counted as taking of the processor, when deciding
#: whether the share above would still be free after it starts.
CPU_PER_CALL = 0.05

#: Memory kept free: the larger of these.
MEMORY_KEPT_FREE_BYTES = 2 * 1024 ** 3
MEMORY_KEPT_FREE_SHARE = 0.20

#: What a call is taken to need before any has been measured.
CALL_BYTES_GUESS = 300 * 1024 ** 2
#: And at least this, however little one is measured to take.
CALL_BYTES_FLOOR = 32 * 1024 ** 2

#: How long a refusal keeps it to one at a time.
REFUSED_SECONDS = 600

#: How often the machine is looked at again.
LOOK_EVERY = 2.0


class Room:
    """How many calls may be going at once, now."""

    def __init__(self, config: Any = None, models: Any = None,
                 activity: Any = None, role: str = "heartbeat") -> None:
        self.config = config
        self.models = models
        self.activity = activity
        self.role = role
        self._lock = threading.Lock()
        self._seen: Dict[str, Any] = {}
        self._seen_at = 0.0
        self._running = 0
        self._per_call = float(CALL_BYTES_GUESS)
        self._processes: Dict[int, Any] = {}

    # -- the limit ------------------------------------------------------------
    def most(self) -> int:
        try:
            value = int(self.config.get("state_heartbeat_at_once", MOST)) \
                if self.config is not None else MOST
        except (TypeError, ValueError):
            value = MOST
        return max(1, min(8, value))

    def allowed(self, running: int) -> Tuple[int, str]:
        """How many may be going, with `running` going now - and why."""
        most = self.most()
        if most <= 1:
            return 1, "one at a time, as set"
        seen = self.look(running)
        limit, why = most, []
        here = seen["idle"] is None or seen["idle"] < AT_THE_COMPUTER_SECONDS
        if here:
            limit = min(limit, 2)
            why.append("you are at the computer" if seen["idle"] is not None
                       else "whether you are at the computer is not known")
        else:
            why.append("you are away")
        refused = seen.get("refused")
        if refused:
            return 1, f"one at a time: the model was refused lately ({refused})"
        battery = seen.get("battery")
        if battery and not battery["plugged"]:
            if battery["percent"] < 30:
                return 1, f"one at a time: on battery, {battery['percent']:.0f}% left"
            limit = min(limit, 2)
            why.append("on battery")
        # Another one only if the processor keeps its share free for the
        # user AFTER it starts - judged on what everything else is using,
        # not the agent, with a little for each call that would be going.
        free = 1.0 - seen["cpu_others"]
        keep = CPU_LEFT_FREE_HERE if here else CPU_LEFT_FREE_AWAY
        if free < keep + CPU_PER_CALL * (running + 1):
            limit = min(limit, max(1, running))
        why.append(f"the processor is {seen['cpu_others']:.0%} busy")
        # And memory: what would be free after one more.
        kept = max(MEMORY_KEPT_FREE_BYTES,
                   MEMORY_KEPT_FREE_SHARE * seen["memory_total"])
        more = int(max(0.0, seen["memory_free"] - kept) // self._per_call)
        limit = min(limit, max(1, running + more))
        why.append(f"{seen['memory_free'] / 1024 ** 3:.1f} GB free, about "
                   f"{self._per_call / 1024 ** 2:.0f} MB a call")
        return max(1, limit), ", ".join(why)

    # -- the machine ----------------------------------------------------------
    def look(self, running: int = 0) -> Dict[str, Any]:
        """The machine now - looked at no more than every two seconds."""
        with self._lock:
            if self._seen and time.time() - self._seen_at < LOOK_EVERY:
                return self._seen
            seen = {"idle": self._idle(), "refused": self._refused(),
                    "battery": None, "cpu_others": 0.0,
                    "memory_free": float(CALL_BYTES_GUESS * 100),
                    "memory_total": float(CALL_BYTES_GUESS * 100)}
            try:
                import psutil
            except ImportError:      # pragma: no cover - psutil is a dependency
                psutil = None
            if psutil is not None:
                try:
                    memory = psutil.virtual_memory()
                    seen["memory_free"] = float(memory.available)
                    seen["memory_total"] = float(memory.total)
                    every = psutil.cpu_percent(interval=None) / 100.0
                    ours, children = self._ours(psutil)
                    seen["cpu_others"] = max(0.0, every - ours)
                    if running and children:
                        self._per_call = max(CALL_BYTES_FLOOR,
                                             children / max(1, running))
                    battery = psutil.sensors_battery()
                    if battery is not None:
                        seen["battery"] = {"plugged": bool(battery.power_plugged),
                                           "percent": float(battery.percent)}
                except Exception:    # pragma: no cover - a look, not a step
                    log.debug("could not look at the machine", exc_info=True)
            self._seen, self._seen_at = seen, time.time()
            return seen

    def _ours(self, psutil: Any) -> Tuple[float, float]:
        """The agent's own share of the processor, and what its children
        hold in memory - each process remembered, because a share of the
        processor is measured between two looks at the same one."""
        try:
            me = psutil.Process(os.getpid())
            family = [me] + me.children(recursive=True)
        except Exception:            # pragma: no cover
            return 0.0, 0.0
        cores = psutil.cpu_count() or 1
        share, children = 0.0, 0.0
        alive = {}
        for one in family:
            known = self._processes.get(one.pid, one)
            alive[one.pid] = known
            try:
                share += known.cpu_percent(interval=None) / 100.0 / cores
                # The calls' processes, not the embedding model's: that one
                # is held for the round whatever runs beside it.
                if one.pid != me.pid and not any(
                        "embed" in part for part in (known.cmdline() or [])):
                    children += float(known.memory_info().rss)
            except Exception:        # gone between the list and the look
                continue
        self._processes = alive
        return min(1.0, share), children

    def _idle(self) -> Optional[float]:
        if self.activity is None:
            return None
        try:
            return self.activity.seconds_since_input()
        except Exception:            # pragma: no cover - a look, not a step
            return None

    def _refused(self) -> str:
        ask = getattr(self.models, "refused_since", None)
        if ask is None:
            return ""
        try:
            return ask(self.role, REFUSED_SECONDS)
        except Exception:            # pragma: no cover
            return ""


def at_once(items: Iterable[Any], work: Callable[[Any], Any],
            room: Optional[Room], going: Callable[[], bool] = lambda: True,
            landed: Optional[Callable[[Any, Any, Optional[BaseException]], Any]] = None,
            told: Optional[Callable[[int, int, str], None]] = None,
            pool: Any = None, kind: str = "work") -> int:
    """Each item through `work`, as many at once as `room` allows.

    `items` is read one at a time, on this thread, as there is room for the
    next - so deciding what comes next can be part of reading it. `landed`
    is called on this thread as each one finishes, in the order they
    finish, with its result or the exception it raised; returning False
    stops any more from starting, and the ones going finish and land.
    `told(running, allowed, why)` hears each time the number changes.
    With no room given, one at a time.

    WITH THE POOL (pool.py), each takes a place of `kind` there before it
    starts and gives it back when it ends: how many go at once is decided
    with everything else the agent has going, not this alone.

    Returns how many were started.
    """
    finished: "queue.Queue[Tuple[Any, Any, Optional[BaseException]]]" = queue.Queue()
    # This batch's place in the pool's line, while it waits.
    key = f"{kind}:batch-{id(finished)}"
    source = iter(items)
    running, started, stopped, exhausted = 0, 0, False, False

    def run(item: Any, place: Any = None) -> None:
        try:
            finished.put((item, work(item), None))
        except BaseException as exc:   # handed to `landed`, on the caller
            finished.put((item, None, exc))
        finally:
            if pool is not None:
                pool.give_back(place)

    while True:
        while not stopped and not exhausted and going():
            place = None
            if pool is not None:
                place = pool.try_take(kind, key=key)
                if place is None:
                    break
                allowed, why = pool.allowed()
            else:
                allowed, why = room.allowed(running) if room is not None \
                    else (1, "one at a time")
                if running >= allowed:
                    break
            try:
                item = next(source)
            except StopIteration:
                exhausted = True
                if pool is not None:
                    pool.give_back(place)
                break
            threading.Thread(target=run, args=(item, place), daemon=True,
                             name="at-once").start()
            running += 1
            started += 1
            if told is not None:
                told(running, allowed, why)
        if not running:
            # Nothing of its own going: with the pool, it may only be waiting
            # for a place - something more important first, or the computer
            # full. Asked again, until it is stopped.
            if pool is not None and not stopped and not exhausted and going():
                time.sleep(LOOK_EVERY)
                continue
            if pool is not None:
                pool.stop_waiting(key)
            return started
        try:
            item, result, error = finished.get(timeout=LOOK_EVERY)
        except queue.Empty:
            continue
        running -= 1
        if landed is not None and landed(item, result, error) is False:
            stopped = True
        elif landed is None and error is not None:
            stopped = True
            log.warning("a piece of work failed", exc_info=error)


__all__ = ["Room", "at_once", "MOST"]
