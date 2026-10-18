"""ONE POOL FOR EVERYTHING THAT RUNS SIDE BY SIDE.

The agent has many modules that each do work in parallel: the state
heartbeat's summaries and status calls, the learning of projects (by the
user's button and by itself), the runs of the user's work, an issue of the
Newspaper. Each used to ask the computer's room (room.py) how many of ITS
OWN it could have going - and each counted only its own, so together they
could take the machine from the person using it.

Now each takes a PLACE here before it starts, and gives it back when it
ends; work that keeps its own count (the runs of the user's work, an issue
being made) is counted by asking it. How many places there are is decided
from everything going, together, by the same rules as before (room.py:
whether the user is at the computer, the processor, memory, the battery,
the provider) - asked again each time one more would start. Never fewer
than one: when the computer is tight, one thing at a time, whatever kind.

BALANCED, THEN BY PRIORITY. A place that comes free goes to the kind
waiting with the FEWEST going: with room for four and four kinds waiting,
one each - not all four to the runs of work while the rest wait. Between
kinds with as many going, to the one that matters more (PRIORITY) - so
with room for only one, priority alone decides. What the user started
matters most - a run of their work, a project they pressed Start or Update
on - then what keeps the Overview up to date (the grouping of states), then
an issue of the Newspaper, and last the learning updated by itself.
Nothing going is ever stopped for it: this decides only who starts next. A
waiter that stops asking is forgotten after WAITER_FORGOTTEN seconds.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

#: How much each kind matters: the lower, the sooner it is given a place.
PRIORITY = {"work": 0, "learning": 1, "grouping": 2, "newspaper": 3,
            "auto learning": 4}
#: A kind not listed waits behind every listed one.
PRIORITY_OTHER = 5
#: A waiter not heard from in this long is no longer waiting.
WAITER_FORGOTTEN = 10.0


class Place:
    """One piece of work's place in the pool, until it gives it back."""

    __slots__ = ("id", "kind", "label", "since")

    def __init__(self, ident: int, kind: str, label: str) -> None:
        self.id = ident
        self.kind = kind
        self.label = label
        self.since = time.time()


class Pool:
    """The one pool (module docstring)."""

    def __init__(self, room: Any = None) -> None:
        self.room = room
        self._lock = threading.RLock()
        self._held: Dict[int, Place] = {}
        self._ids = itertools.count(1)
        #: Kinds that keep their own count: kind -> what is going of it.
        self._counted: Dict[str, Callable[[], List[str]]] = {}
        #: What waits for a place: its key -> (priority, kind, last asked).
        self._waiting: Dict[str, Tuple[int, str, float]] = {}

    # -- what is going ---------------------------------------------------------------------
    def counts(self, kind: str, going: Callable[[], List[str]]) -> None:
        """A kind that keeps its own count - asked, each time, what of it
        is going (a label for each)."""
        self._counted[kind] = going

    def going(self) -> List[Tuple[str, str]]:
        """(kind, label) of everything going now."""
        out = [(place.kind, place.label) for place in list(self._held.values())]
        for kind, going in list(self._counted.items()):
            try:
                out += [(kind, str(one)) for one in going() or []]
            except Exception:              # pragma: no cover - counted as none
                log.debug("could not count %s", kind, exc_info=True)
        return out

    def allowed(self) -> Tuple[int, str]:
        """How many may be going, of every kind together - and why."""
        total = len(self.going())
        if self.room is None:
            return max(1, total + 1), "no limit known"
        allowed, why = self.room.allowed(total)
        return max(1, allowed), why

    @staticmethod
    def priority_of(kind: str) -> int:
        return PRIORITY.get(kind, PRIORITY_OTHER)

    # -- waiting, and a place ----------------------------------------------------------------
    def may_start(self, kind: str, key: str = "",
                  priority: Optional[int] = None) -> Tuple[bool, str]:
        """Whether one more of `kind` may start now - for work that keeps
        its own count, and so takes no place. While it may not, it waits
        (under `key`) and is counted before anything less important."""
        with self._lock:
            return self._ask(kind, key or kind, priority)

    def try_take(self, kind: str, label: str = "", key: str = "",
                 priority: Optional[int] = None) -> Optional[Place]:
        """A place, if there is one now - else None, and waiting for one
        under `key` (by default the kind and label)."""
        with self._lock:
            may, _why = self._ask(kind, key or f"{kind}:{label}", priority)
            if not may:
                return None
            place = Place(next(self._ids), kind, label)
            self._held[place.id] = place
            return place

    def _ask(self, kind: str, key: str, priority: Optional[int]) -> Tuple[bool, str]:
        now = time.time()
        mine = self.priority_of(kind) if priority is None else int(priority)
        for other, (_p, _k, asked) in list(self._waiting.items()):
            if now - asked > WAITER_FORGOTTEN:
                self._waiting.pop(other, None)
        going = self.going()
        # BALANCED FIRST, THEN BY PRIORITY: a place goes to the kind waiting
        # with the fewest going - four kinds and room for four, one each -
        # and between kinds with as many going, to the one that matters
        # more; with room for one, that is priority alone.
        have: Dict[str, int] = {}
        for one, _label in going:
            have[one] = have.get(one, 0) + 1
        me = (have.get(kind, 0), mine)
        ahead = [k for other, (p, k, _a) in self._waiting.items()
                 if other != key and k != kind and (have.get(k, 0), p) < me]
        if ahead:
            self._waiting[key] = (mine, kind, now)
            return False, f"waiting behind {ahead[0]}, which has its turn first"
        allowed, why = self.allowed()
        if len(going) < allowed:
            self._waiting.pop(key, None)
            return True, why
        self._waiting[key] = (mine, kind, now)
        return False, (f"{len(going)} going of every kind, as many as there is "
                       f"room for ({why})")

    def take(self, kind: str, label: str = "",
             going: Callable[[], bool] = lambda: True,
             every: float = 2.0) -> Optional[Place]:
        """A place, waited for - or None once `going` says to stop."""
        while going():
            place = self.try_take(kind, label)
            if place is not None:
                return place
            time.sleep(every)
        self.stop_waiting(f"{kind}:{label}")
        return None

    def stop_waiting(self, key: str) -> None:
        with self._lock:
            self._waiting.pop(key, None)

    def give_back(self, place: Optional[Place]) -> None:
        if place is not None:
            with self._lock:
                self._held.pop(place.id, None)

    def status(self) -> Dict[str, Any]:
        allowed, why = self.allowed()
        going = self.going()
        kinds: Dict[str, int] = {}
        for kind, _label in going:
            kinds[kind] = kinds.get(kind, 0) + 1
        with self._lock:
            waiting = sorted(((p, k) for p, k, _a in self._waiting.values()))
        return {"going": [{"kind": kind, "label": label} for kind, label in going],
                "by_kind": kinds, "allowed": allowed, "why": why,
                "waiting": [{"kind": k, "priority": p} for p, k in waiting]}


__all__ = ["Pool", "Place", "PRIORITY"]
