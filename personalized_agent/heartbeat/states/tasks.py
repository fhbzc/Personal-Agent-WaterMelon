"""Tasks that survive the round that found them.

The grouping used to be rebuilt from nothing every hour: the tree was
computed, the tasks were numbered one to five in order of size, and the
numbers meant nothing an hour later. A reader who opened task 3 on Monday
and task 3 on Tuesday was looking at two unrelated things, and every
summary was written again for a group that had gained one state.

So a task is a row that persists. New states are *appended* to the task
they belong to - one dot product each against the task's centre - and the
grouping is only rebuilt when appending has stopped being honest: when
what is left over has become a task of its own, when a task's centre has
drifted away from what it was about, when it has spread wide enough to be
absorbing rather than matching, or when two tasks have started taking the
same states.

When a rebuild does happen, the new grouping is *matched* to the old one
by who is in it, so a task that is substantially the same thing keeps its
identity, its history, and its summary. What split, merged, appeared or
ended is recorded as that, rather than showing up as a renumbering.
"""

from __future__ import annotations

import array
import json
import logging
import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import todo

log = logging.getLogger(__name__)

#: The real run, and the debugger's sandbox.
#:
#: The sandbox exists so that the grouping can be tried on states somebody
#: made up, which is the only way to see what it does to a shape you chose
#: - on real states you can only see what it did. Same code, same limits,
#: same tables; a different library root and a different scope.
LIVE = "live"
SANDBOX = "sandbox"

#: HOW THESE NUMBERS WERE CHOSEN
#:
#: Not by taste. Two hundred and forty states were written the way the
#: review writes them, embedded by the model that will actually read
#: them - so the geometry is the real one even though the sentences are
#: made up - and replayed in arrival order, twenty at a time. Three of
#: the four subjects stay themselves. The fourth is one project walking
#: from the code to the writing-up in fifteen steps, each step close
#: enough to the last to be appended and the end nothing like the
#: beginning. That is the case this whole mechanism exists for, and it
#: cannot be tested with two separate subjects: those make a new task,
#: not a drifting one.
#:
#: At the end of every batch the appended grouping is compared with what
#: a full rebuild would have said, as an adjusted Rand index - agreement
#: over pairs, corrected for what chance gives. The plain Rand index was
#: useless here: over a grouping with five parts it sits near 1 whatever
#: anything does, because most pairs are in different parts either way.
#:
#: Each number below was swept with the other triggers switched off.
#: Swept together they hide each other: the first pass had the loose
#: trigger firing on every batch, so every other parameter was measured
#: against a grouping that had just been rebuilt anyway, and every
#: result came out flat.
#:
#: The three regimes, with the chosen set in the middle:
#:
#:      never rebuild        1 rebuild    mean 0.913   worst 0.662
#:      chosen              10 rebuilds   mean 0.990   worst 0.874
#:      rebuild every time  12 rebuilds   mean 1.000   worst 1.000
#:
#: So appending alone costs a third of the distance to a rebuild at its
#: worst moment, and the triggers buy almost all of it back. Note what
#: this does and does not claim: the saving in rebuilds is small here
#: because twenty new states against two hundred is a violent batch. On
#: an hourly heartbeat two or three states arrive against a calendar of
#: a thousand, and the same triggers fire far more rarely. What
#: continuity buys at that rate is not saved clustering - that is cheap -
#: but kept identity and unrewritten summaries.
#:
#: So the same parameters were measured again at the rate the heartbeat
#: runs at: a hundred and twenty states already grouped, then arrivals
#: three at a time, sixty rounds.
#:
#:      append only          0 rebuilds of 60   mean 0.902  worst 0.846
#:      chosen               4 rebuilds of 60   mean 0.986  worst 0.858
#:      rebuild every round 60 rebuilds of 60   mean 1.000  worst 1.000
#:
#: Which is the argument for the whole mechanism: 93 per cent of rounds
#: are a few dot products, and it recovers seven eighths of the distance
#: between never rebuilding and rebuilding every time.
#:
#: One honest note about that table. Turning off the drift check, or the
#: loose check, changed nothing: in this regime the backstop was the only
#: signal that fired. The other two are there for the shapes the hourly
#: replay does not contain - a project that walks somewhere else over a
#: month, a new subject arriving all at once - and they earned their
#: limits in the batch sweeps above, where those shapes were forced. They
#: are not carrying this table.

#: How far outside its own spread a state may be and still be appended.
#:
#: In standard deviations of the distances its members already sit at, so
#: a tight task stays tight and a broad one keeps taking broad things.
#: A single distance would do neither: the same number is generous for a
#: task whose members sit at 0.1 and mean for one whose members sit at
#: 0.4.
#:
#:      0.5   11 rebuilds   mean 0.992   worst 0.902   drift 0.015
#:      1.0   10 rebuilds   mean 0.990   worst 0.874   drift 0.015
#:      1.5    8 rebuilds   mean 0.967   worst 0.859   drift 0.016
#:      2.0    8 rebuilds   mean 0.958   worst 0.804   drift 0.035
#:      3.0    8 rebuilds   mean 0.958   worst 0.804   drift 0.044
#:      5.0    the grouping collapses: mean 0.711, drift 0.191
#:
#: 1.0 rather than 0.5 because the two are within noise of each other on
#: agreement and 0.5 pays an extra rebuild for it. Above 1.5 the drift a
#: task reaches doubles, which is the real reading of that column: a
#: generous radius is how a task walks somewhere else without any single
#: state ever looking wrong.
ACCEPT_AT = 1.0

#: How far a task's centre may move from where it was at its last rebuild
#: before the grouping is rebuilt, as cosine distance.
#:
#: This is the "twist" the appending is guarding against: a task that has
#: been fed one state at a time until it is about something else.
#:
#:      0.03   4 rebuilds   mean 0.891
#:      0.05   2 rebuilds   mean 0.921   worst 0.688
#:      0.08   never fires  mean 0.926   worst 0.571   peak drift 0.070
#:      0.12   never fires  - nothing on this corpus ever reached it
#:
#: 0.05, and the shape of that table is the whole argument. Above it the
#: trigger never fires at all, and the mean looks fine while the worst
#: moment falls to 0.571 - which is exactly what drift looks like from
#: the outside: the average stays respectable and one task quietly
#: becomes wrong. Below it the trigger fires on ordinary movement and
#: the mean falls, because a rebuild renumbers things that were fine.
DRIFT_LIMIT = 0.05

#: How much wider a task may get than it was at its last rebuild.
#:
#: A ratio of the mean distance from the centre. Above it the task is
#: absorbing rather than matching - every state is a little outside, and
#: a little outside repeated fifty times is a different task.
#:
#:      1.2   mean 0.816
#:      1.4   mean 0.816
#:      1.6   mean 0.926
#:      2.0   mean 0.926
#:      3.0   mean 0.926
#:
#: Kept as a guard, set where it does nothing. On this corpus the signal
#: never helped: at 1.6 and above it never fired, and tightening it made
#: things worse, because a task that widens is usually a task correctly
#: taking in a broader day. It stays in because absorption is a real
#: failure and the backstop alone would be slow to catch it - but it is
#: set at the loosest value that costs nothing, not at one chosen to
#: fire. A guard that has never fired is being honest about that here
#: rather than being tuned until it looks useful.
SPREAD_LIMIT = 1.6

#: How close a second task has to be before an appended state counts as
#: ambiguous, and what share of a round may be ambiguous before the
#: grouping is rebuilt.
#:
#: Two tasks that keep taking the same states are two tasks the data no
#: longer separates, and the boundary between them is stale.
AMBIGUOUS_WITHIN = 0.05
AMBIGUOUS_SHARE = 0.34

#: How many states may match no task at all before the grouping is
#: rebuilt. A pile of states that fit nowhere is a task nobody has
#: noticed yet.
#:
#:       3   mean 0.923
#:       5   mean 0.909
#:       8   mean 0.940   worst 0.785
#:      12   mean 0.857
#:
#: 8, and note it is deliberately not the five that makes a task in the
#: layering. Those are different questions. Five states that group
#: together are a task; five states that each match nothing are five
#: strangers, and rebuilding on them scatters the tasks that were fine.
#: Eight is where the loose pile is reliably one new thing rather than
#: noise - and the fall to 0.857 at twelve is what waiting past it costs.
#: WHEN TO LOOK AT THE PILE, which is no longer when to rebuild.
#:
#: Eight was right when finding out meant rebuilding the whole calendar:
#: the question cost a hundred times what it was worth, so it was worth
#: asking rarely. The pile is now clustered among its own members, which
#: is nearly free, so the honest threshold is the point at which it could
#: possibly hold a task at all - the same five that makes a task
#: anywhere else.
#:
#: What stops five strangers becoming a task is no longer the count. It
#: is that a candidate has to be about as tight as the tasks already
#: accepted; see PILE_ALLOWANCE in the heartbeat. That is the right test
#: and the count never was one.
LOOSE_IS_A_TASK = 5

#: The backstop: rebuild when this share of the states have arrived since
#: the last rebuild, whatever the other signals say.
#:
#: Drift is measured against a centre that the drifting itself moves, so
#: it can creep without ever tripping. This is the guarantee that the
#: grouping is never older than a known fraction of the calendar.
#:
#:      0.1   11 rebuilds   mean 1.000
#:      0.2    8 rebuilds   mean 0.958   worst 0.804
#:      0.25   7 rebuilds   mean 0.965   worst 0.785
#:      0.4    5 rebuilds   mean 0.926
#:      0.6    3 rebuilds   mean 0.906
#:
#: 0.2. At 0.1 it rebuilds on almost every batch, which is the same as
#: not having this mechanism at all. Past 0.25 the agreement falls away
#: steadily and there is no plateau to sit on, so it is set where it
#: still costs a rebuild rather than where it is cheapest: this is the
#: only signal that cannot be fooled by gradual change.
REBUILD_AFTER = 0.2

#: How much of a task's membership a new cluster must share to be the
#: same task, as a Jaccard overlap.
#:
#: Below it the new cluster is something else and gets its own identity,
#: with what it came from recorded.
SAME_TASK_AT = 0.5

#: How much of an old task has to be inside a new group for the group to
#: be that task, continued. Not the overlap above: overlap is shared over
#: the UNION, so a task that grows falls below it however much of itself
#: it keeps - ten states joined by fifteen new ones is an overlap of 0.4,
#: with every one of the ten still there. This asks the question a reader
#: asks instead: is the work I knew still all in here?
CONTINUES_AT = 0.8
#: And how much of ANOTHER old task may be in it before it is a merge
#: rather than a continuation. Above this the group is two things joined,
#: and which name it should carry is not something to decide by default.
ALSO_HOLDS_AT = 0.5

#: How much of a task's membership may turn over before its summary is
#: written again.
#:
#: A task that gained four states is the same task and says so; a task
#: that is half new is a different claim and the old summary would be a
#: description of something that is no longer there.
RESUMMARISE_AFTER = 0.3


#: HOW FAST A TASK'S CENTRE FOLLOWS ITS WORK. A state's weight halves after
#: this many later states, so the centre is a mean that leans towards what
#: the task is now. With a plain mean a long task's centre stops moving, and
#: the work it has turned into starts failing to fit it. Counted in states,
#: not days: a task left alone for six months is not worn away by the wait.
HALF_LIFE_STATES = 50

#: How many of its state ids a task keeps by name. Older ones are in the
#: profile - the centre, the spread, the places - and not listed.
MEMBERS_KEPT = 200

#: How many of its most recent states a round reads back for each task, to
#: draw it, find the tasks inside it and describe where it stands.
RECENT_READ = 60

#: How many places a task's workspace profile keeps.
PLACES_KEPT = 40

#: The share of a task a place has to cover before the task counts as one
#: of the tasks USING it, which is what a place's specificity is counted by.
USES_A_PLACE_AT = 0.10

#: WHEN A TASK GOES QUIET, AND WHEN IT GOES DORMANT. Quiet is drawn and
#: marked; archived is not drawn, and is still there to be picked up.
QUIET_AFTER_DAYS = 3
ARCHIVE_AFTER_DAYS = 30

#: How many separate stretches of activity a task remembers. Past this the
#: two closest together are joined, so the oldest history coarsens rather
#: than being dropped.
STRETCHES_KEPT = 40

#: How many of its pieces' centres a task keeps.
PARTS_KEPT = 8

#: A state that has fitted nothing for this long is let go of: it was not
#: the start of something.
LOOSE_KEPT_DAYS = 30

#: HOW A STATE IS PLACED, measured on one machine's six weeks: the first
#: half of every task's states built it, and the second half was streamed
#: through in the order it happened (`calibrate_placement` in the commit
#: that set these).
#:
#: A state goes to the task with the lowest
#:
#:     distance from its centre  -  PLACE_COUNTS * W
#:
#: where W is how strongly it shares the task's places (0 to 1), if that
#: is at most FAR - and is left loose otherwise.
#:
#: What was measured, and why this and not the obvious rule:
#:
#:   - "within the task's own radius plus a few of its spreads" was the
#:     obvious rule and it is the wrong yardstick. A task's spread, taken
#:     from the states that made it, is far smaller than where its later
#:     states land: the median later state was 5.7 spreads out. At one
#:     spread 69% of them were left loose; at four, 70% went to the wrong
#:     task. The nearest centre, by contrast, picked the right task for 81%.
#:   - so: the nearest, by meaning and place, unless even that is too far.
#:     Own-task distances ran p50 0.41, p90 0.54; the states that belonged
#:     to no task sat p10 0.42, p50 0.48 from the nearest centre. 0.5:
#:            FAR   right  wrong  loose
#:           0.45   0.671  0.288  0.041
#:           0.50   0.676  0.302  0.022
#:           0.60   0.695  0.302  0.002   - and keeps 4 of 35 loose
#:   - the place counts: with PLACE_COUNTS 0 -> 0.25, right 0.632 -> 0.676
#:     and wrong 0.327 -> 0.305. Letting a place WIDEN a task as well, as a
#:     second setting, changed nothing measurable, so there is one.
#:   - how fast the centre follows its work hardly mattered (half-lives of
#:     20 to 1000 states all 0.68 right); 50 keeps a changing task one task.
FAR = 0.5
PLACE_COUNTS = 0.25

#: NOT A WALL, AND NO EXTRA ROOM, INSIDE A PROJECT. Both were measured on
#: the same replay, with the user's seven personalized_agent_release tasks
#: as one project: only 13 to 15 of its 223 states went to a task outside
#: it either way, a wall around it took right placements from 0.612 to
#: 0.598, and extra room inside it changed nothing at any amount. What goes
#: wrong is the other direction - large tasks taking states that are not
#: theirs - and a wall does not touch that. So a project groups its tasks;
#: it does not change where a state goes.

#: A state the two nearest tasks would take about equally is left loose
#: rather than guessed: at 0.01, wrong fell from 0.305 to 0.276 and right
#: stayed 0.678. The pile decides what it is.
TIE_WITHIN = 0.01

#: How strongly a state has to share an archived task's places for the task
#: to be picked up again without asking.
REVIVE_AT = 0.5

#: How far a task's centre may move from where it was when it was named
#: before the name is written again.
RENAME_AFTER = 0.15


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _keep_factor() -> float:
    return 0.5 ** (1.0 / max(1, HALF_LIFE_STATES))


class Task:
    """One task, as it is kept between rounds."""

    def __init__(self, **fields: Any) -> None:
        self.id: str = fields.get("id", "")
        self.number: int = int(fields.get("number", 0))
        #: WHERE IT SITS ON THE PAGE, assigned once and kept for life.
        #:
        #: `number` is its position in the current reading order and moves
        #: whenever the order does; this is the thing the order is made
        #: from. A task that is still the same task is still in the same
        #: place, so a reader who opened the third row yesterday finds the
        #: same work in the third row today. Anything genuinely new goes
        #: after everything that already existed - including the extra
        #: pieces of a task that split, of which one inherits the seat and
        #: the rest are new things that happen to have a parent.
        #:
        #: -1 until it has one, which is how a task written before seats
        #: existed is told from one sitting in position zero.
        self.seat: int = int(fields.get("seat", -1))
        self.members: List[str] = list(fields.get("members") or [])
        #: The sum of the member vectors, not their mean: appending a
        #: state is then exact rather than a mean of means.
        self.total: List[float] = list(fields.get("total") or [])
        #: The centre as it was at the last rebuild, which is what drift
        #: is measured against.
        self.anchor: List[float] = list(fields.get("anchor") or [])
        self.radius: float = float(fields.get("radius", 0.0))
        self.spread: float = float(fields.get("spread", 0.0))
        self.anchor_radius: float = float(fields.get("anchor_radius", 0.0))
        self.created_at: str = fields.get("created_at") or now()
        self.rebuilt_at: str = fields.get("rebuilt_at") or self.created_at
        self.updated_at: str = fields.get("updated_at") or self.created_at
        self.version: int = int(fields.get("version", 1))
        self.born_of: Dict[str, Any] = dict(fields.get("born_of") or
                                            {"kind": "new", "from": []})
        self.retired_at: str = fields.get("retired_at") or ""
        self.summary_id: str = fields.get("summary_id") or ""
        self.summary_at: str = fields.get("summary_at") or ""
        self.summary_members: int = int(fields.get("summary_members", 0))
        #: The task described in parts: where the work lives, what it is
        #: in aid of, what it is, and where it stands. Kept apart from the
        #: summary sentence because they are updated differently - a round
        #: that finds three new states usually changes only `status`, and
        #: rewriting the other three would be three claims restated by a
        #: model that had no new reason to restate them.
        self.described: Dict[str, Any] = dict(fields.get("described") or {})
        #: WHAT IT KEEPS INSTEAD OF EVERY STATE: the weight behind its
        #: centre, how many states it has ever taken, when it was first and
        #: last active, its places and their weights, its most recent states.
        self.profile: Dict[str, Any] = dict(fields.get("profile") or {})
        #: When it went dormant. Not drawn; still there to be picked up.
        self.archived_at: str = fields.get("archived_at") or ""
        #: What happened to it in the round being reported. Not stored.
        self.happened: Dict[str, Any] = {}

    # -- what it is --------------------------------------------------------
    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def centre(self) -> List[float]:
        """The mean of its members, scaled to unit length."""
        return _unit(self.total)

    @property
    def profiled(self) -> bool:
        """Whether it keeps a profile - every task does once it has been
        read through once since profiles existed."""
        return bool(self.profile.get("v"))

    @property
    def count(self) -> int:
        """How many states it has ever taken, not only those it lists."""
        return int(self.profile.get("count") or len(self.members))

    @property
    def first_seen(self) -> str:
        return str(self.profile.get("first_seen") or "")

    @property
    def last_seen(self) -> str:
        return str(self.profile.get("last_seen") or "")

    def recent(self, how_many: int = RECENT_READ) -> List[str]:
        """Its most recent states, newest last."""
        return list(self.members[-max(1, how_many):])

    def phase(self, at: Optional[datetime] = None) -> str:
        """active, quiet, or archived - from when it was last worked on."""
        if self.archived_at:
            return "archived"
        last = _moment(self.last_seen)
        if last is None:
            return "active"
        idle = ((at or datetime.now(timezone.utc)) - last).total_seconds()
        if idle >= ARCHIVE_AFTER_DAYS * 86400:
            return "archived"
        if idle >= QUIET_AFTER_DAYS * 86400:
            return "quiet"
        return "active"

    def places(self) -> Dict[str, float]:
        return dict(self.profile.get("places") or {})

    def coverage(self, place: str) -> float:
        """The share of the task's (weighted) states that touched a place."""
        weight = float(self.profile.get("weight") or 0.0)
        if weight <= 0:
            return 0.0
        return min(1.0, float(self.places().get(place, 0.0)) / weight)

    def weighed_places(self, specific: Optional[Dict[str, float]] = None,
                       how_many: int = 8) -> List[Dict[str, Any]]:
        """Its places, each with how much of the task is there, how few
        other tasks use it, and the two multiplied - which is what a state
        sharing that place is credited with."""
        specific = specific or {}
        rows = []
        for place in self.places():
            coverage = self.coverage(place)
            alone = specific.get(place, 1.0)
            rows.append({"place": place, "coverage": round(coverage, 3),
                         "specific": round(alone, 3),
                         "weight": round(coverage * alone, 3)})
        rows.sort(key=lambda row: (-row["weight"], row["place"]))
        return rows[:how_many]

    def region(self, specific: Optional[Dict[str, float]] = None,
               how_many: int = 3) -> List[str]:
        """Where its work mostly is: its places by weight, most telling first."""
        specific = specific or {}
        scored = sorted(((self.coverage(place) * specific.get(place, 1.0),
                          place) for place in self.places()),
                        key=lambda pair: (-pair[0], pair[1]))
        out: List[str] = []
        for score, place in scored:
            if score <= 0:
                break
            # A folder whose file is already listed says less than the file.
            if any(one.startswith(place + "/") for one in out):
                continue
            out = [one for one in out if not place.startswith(one + "/")]
            out.append(place)
            if len(out) >= how_many:
                break
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "number": self.number, "seat": self.seat,
                "size": self.size, "count": self.count,
                "phase": self.phase(), "first_seen": self.first_seen,
                "last_seen": self.last_seen, "stretches": self.stretches(),
                "archived_at": self.archived_at,
                "members": list(self.members),
                "radius": round(self.radius, 4),
                "spread": round(self.spread, 4),
                "drift": round(self.drift(), 4),
                "widened": round(self.widened(), 3),
                "created_at": self.created_at, "rebuilt_at": self.rebuilt_at,
                "updated_at": self.updated_at, "version": self.version,
                "born_of": dict(self.born_of),
                "summary_id": self.summary_id,
                "summary_at": self.summary_at,
                "summary_members": self.summary_members,
                "described": dict(self.described),
                "happened": dict(self.happened)}

    # -- how it has moved ---------------------------------------------------
    def drift(self) -> float:
        """How far its centre is from where the last rebuild left it."""
        if not self.anchor or not self.total:
            return 0.0
        return max(0.0, 1.0 - _cosine(self.centre, self.anchor))

    def widened(self) -> float:
        """How much wider it is than the last rebuild left it."""
        if self.anchor_radius <= 0:
            return 1.0
        return self.radius / self.anchor_radius

    # -- changing it --------------------------------------------------------
    def take(self, state_id: str, vector: Sequence[float],
             places: Sequence[str] = (), began: str = "",
             ended: str = "") -> None:
        """Append one state, and move the centre by its share.

        A profiled task weighs the new state against the old ones decayed:
        the centre, the spread and the places all lean towards what the
        task is now, and none of them needs the old states to be read
        again. A task from before profiles keeps the plain sum it had.
        """
        if state_id in self.members:
            return
        self.members.append(state_id)
        if len(self.members) > MEMBERS_KEPT:
            self.members = self.members[-MEMBERS_KEPT:]
        if not self.profiled:
            if not self.total:
                self.total = list(vector)
            else:
                self.total = [a + b for a, b in zip(self.total, vector)]
            self.updated_at = now()
            return
        keep = _keep_factor()
        weight = float(self.profile.get("weight") or 0.0)
        if self.total and weight > 0:
            apart = 1.0 - _cosine(vector, self.centre)
            before = weight * keep
            after = before + 1.0
            mean = self.radius
            fresh = mean + (apart - mean) / after
            variance = (before * (self.spread ** 2 + (mean - fresh) ** 2)
                        + (apart - fresh) ** 2) / after
            self.radius = fresh
            self.spread = math.sqrt(max(0.0, variance))
            self.total = [a * keep + b for a, b in zip(self.total, vector)]
        else:
            after = 1.0
            self.total = list(vector)
            self.radius = self.spread = 0.0
        held = {place: value * keep
                for place, value in (self.profile.get("places") or {}).items()}
        for place in set(places):
            held[place] = held.get(place, 0.0) + 1.0
        if len(held) > PLACES_KEPT:
            held = dict(sorted(held.items(), key=lambda pair: -pair[1])
                        [:PLACES_KEPT])
        self.profile["places"] = {place: round(value, 4)
                                  for place, value in held.items()}
        self.profile["weight"] = round(after, 4)
        self.profile["count"] = int(self.profile.get("count") or 0) + 1
        if began and (not self.first_seen or began < self.first_seen):
            self.profile["first_seen"] = began
        if ended and ended > self.last_seen:
            self.profile["last_seen"] = ended
        if began:
            self._stretch(began, ended or began)
        self.updated_at = now()

    def stretches(self) -> List[List[str]]:
        """When it was worked on, as separate stretches, oldest first."""
        return [list(one) for one in (self.profile.get("stretches") or [])]

    def _stretch(self, began: str, ended: str) -> None:
        """Add one state's time to the stretches it falls in or between.

        A new stretch starts where nothing was added for longer than a task
        takes to go quiet - so six months away is a gap in the drawing,
        not a line drawn through it as if the work had gone on.
        """
        gap = QUIET_AFTER_DAYS * 86400
        held = [list(one) for one in (self.profile.get("stretches") or [])]
        held.append([began, ended])
        held.sort(key=lambda one: one[0])
        joined: List[List[str]] = []
        for start, end in held:
            if joined:
                last = _moment(joined[-1][1])
                here = _moment(start)
                if (last is not None and here is not None
                        and (here - last).total_seconds() <= gap):
                    joined[-1][1] = max(joined[-1][1], end)
                    continue
            joined.append([start, end])
        while len(joined) > STRETCHES_KEPT:
            # The two with the shortest gap between them become one.
            gaps = []
            for index in range(len(joined) - 1):
                left = _moment(joined[index][1])
                right = _moment(joined[index + 1][0])
                gaps.append(((right - left).total_seconds()
                             if left and right else 0.0, index))
            _gap, index = min(gaps)
            joined[index] = [joined[index][0],
                             max(joined[index][1], joined[index + 1][1])]
            del joined[index + 1]
        self.profile["stretches"] = joined

    def parts(self) -> List[List[float]]:
        """The centres of its pieces, as last found."""
        return [list(one) for one in (self.profile.get("parts") or [])
                if one]

    def set_parts(self, centres: Sequence[Sequence[float]]) -> None:
        self.profile["parts"] = [[round(float(value), 5) for value in centre]
                                 for centre in list(centres)[:PARTS_KEPT]]

    #: What a profile keeps when it is built again from the states: which
    #: states the name was written from is about the name, not the states;
    #: and the states the model took out of it, which it is offered again
    #: only in a round it also gains others (heartbeat, `kicked_out`).
    KEPT_THROUGH_A_REBUILD = ("summarised", "summarised_sampled", "kicked_out")

    def start_profile(self) -> None:
        """Begin keeping a profile; `take` fills it in."""
        kept = {key: self.profile[key] for key in self.KEPT_THROUGH_A_REBUILD
                if key in (getattr(self, "profile", None) or {})}
        self.profile = {"v": 1, "weight": 0.0, "count": 0, "places": {},
                        "first_seen": "", "last_seen": "", **kept}
        self.total = []
        self.radius = self.spread = 0.0

    def named(self) -> None:
        """It was just described: its centre now is what the name is about,
        and these are the states the name was written from."""
        self.anchor = list(self.centre)
        self.anchor_radius = self.radius
        self.profile["summarised"] = list(self.members)
        self.profile.pop("summarised_sampled", None)

    def changed_since_named(self) -> float:
        """The share of its states that are not the ones it was named from -
        come since, or gone since - against how many it was named from."""
        was = self.profile.get("summarised")
        if isinstance(was, list) and self.profile.get("summarised_sampled"):
            # Read back from the summary, which names a sample of what it
            # was written from: what has left is known exactly, what has
            # come only as a count.
            gone = len(set(was) - set(self.members)) / max(1, len(was))
            grown = max(0, self.size - self.summary_members) / max(
                1, self.summary_members)
            return gone + grown
        if isinstance(was, list):
            before, now = set(was), set(self.members)
            return len(before ^ now) / max(1, len(before))
        if not self.summary_members:
            return 0.0
        return abs(self.size - self.summary_members) / self.summary_members

    def absorb(self, other: "Task") -> None:
        """Take another task into this one - the same work, found twice."""
        mine = float(self.profile.get("weight") or 0.0)
        theirs = float(other.profile.get("weight") or 0.0)
        if self.total and other.total:
            self.total = [a + b for a, b in zip(self.total, other.total)]
        elif other.total:
            self.total = list(other.total)
        if mine + theirs > 0:
            self.radius = ((self.radius * mine + other.radius * theirs)
                           / (mine + theirs))
            self.spread = ((self.spread * mine + other.spread * theirs)
                           / (mine + theirs))
        places = self.places()
        for place, value in other.places().items():
            places[place] = places.get(place, 0.0) + value
        self.profile["places"] = dict(sorted(
            places.items(), key=lambda pair: -pair[1])[:PLACES_KEPT])
        self.profile["weight"] = round(mine + theirs, 4)
        self.profile["count"] = self.count + other.count
        for name, pick in (("first_seen", min), ("last_seen", max)):
            values = [one for one in (self.profile.get(name),
                                      other.profile.get(name)) if one]
            if values:
                self.profile[name] = pick(values)
        for start, end in other.stretches():
            self._stretch(start, end)
        self.set_parts(self.parts() + other.parts())
        both = list(dict.fromkeys(self.members + other.members))
        self.members = both[-MEMBERS_KEPT:]
        self.updated_at = now()

    def settle(self, vectors: Dict[str, Sequence[float]]) -> None:
        """Work out its radius and spread from the members it now has."""
        centre = self.centre
        apart = [1.0 - _cosine(vectors[state], centre)
                 for state in self.members if state in vectors]
        if not apart:
            self.radius = self.spread = 0.0
            return
        self.radius = sum(apart) / len(apart)
        self.spread = math.sqrt(sum((one - self.radius) ** 2 for one in apart)
                                / len(apart))

    def anchored(self) -> None:
        """Called at a rebuild: this is what drift is measured from now."""
        self.anchor = list(self.centre)
        self.anchor_radius = self.radius
        self.rebuilt_at = now()

    def accepts(self, vector: Sequence[float], accept_at: float) -> float:
        """How far inside this task a state sits, as a margin.

        Positive means it belongs here: the state is closer to the centre
        than the members already are, allowing for how varied they are.
        The number is returned rather than a yes or no so that the
        nearest task can be told from the merely acceptable one.
        """
        if not self.total:
            return -1.0
        apart = 1.0 - _cosine(vector, self.centre)
        allowed = self.radius + accept_at * self.spread
        # A task of one has no spread to speak of, so it is given the
        # room a pair would have: without it the first state after a
        # rebuild could never be appended to anything.
        if self.size < 3:
            allowed = max(allowed, self.radius + 0.1)
        return allowed - apart


def _unit(vector: Sequence[float]) -> List[float]:
    size = math.sqrt(sum(value * value for value in vector))
    if size <= 0:
        return list(vector)
    return [value / size for value in vector]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    size = (math.sqrt(sum(a * a for a in left))
            * math.sqrt(sum(b * b for b in right)))
    return 0.0 if size <= 0 else max(-1.0, min(1.0, dot / size))


def _moment(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def specificity(tasks: Sequence[Task]) -> Dict[str, float]:
    """How few tasks use each place, from 0 (every task) to 1 (one task).

    A place on many tasks - a chat site, the mail, the home folder - says
    where the user works, not what they are working on. Counted over tasks
    rather than states, because the question it answers is which TASK a
    state belongs to.
    """
    total = len(tasks)
    if total <= 1:
        return {place: 1.0 for task in tasks for place in task.places()}
    uses: Dict[str, int] = {}
    for task in tasks:
        for place in task.places():
            if task.coverage(place) >= USES_A_PLACE_AT:
                uses[place] = uses.get(place, 0) + 1
    # The share of the OTHER tasks that do not use it: a place only one task
    # uses is as telling as a place can be, however few tasks there are -
    # a log-based measure called it 37% specific when there were two.
    return {place: max(0.0, 1.0 - (count - 1) / (total - 1))
            for place, count in uses.items()}


def shares_with(task: Task, places: Sequence[str],
                specific: Dict[str, float]) -> Tuple[float, str]:
    """How strongly a state shares a task's places, 0 to 1, and which place.

    The strongest single place they share, weighted by how much of the task
    is there and how few tasks use it. The strongest and not the sum: a
    state that opens a new file in the task's project folder shares the
    folder, and should not count for less because the file is new.
    """
    best, which = 0.0, ""
    for place in places:
        if place not in task.places():
            continue
        score = task.coverage(place) * specific.get(place, 1.0)
        if score > best:
            best, which = score, place
    return min(1.0, best), which


def missing_from(task: Task, places: Sequence[str],
                 specific: Dict[str, float]) -> Tuple[float, str]:
    """How telling the most telling place a state works in that the task
    has never been - 0 when the task has been everywhere it works.

    A place under one the task holds is not new: a new file in the task's
    project folder is the task's folder. Only somewhere wholly new counts.
    """
    held = task.places()
    best, which = 0.0, ""
    for one in places:
        if one in held or any(one.startswith(other + "/") for other in held):
            continue
        score = specific.get(one, 1.0)
        if score > best:
            best, which = score, one
    return best, which


def fits(task: Task, vector: Sequence[float], places: Sequence[str],
         specific: Dict[str, float], accept_at: float = ACCEPT_AT,
         counts: float = PLACE_COUNTS, far: float = FAR) -> Dict[str, Any]:
    """How well a state fits a task, by meaning and by workspace.

    The score is the distance from the task's centre less what the shared
    places are worth; lower is closer, and above `far` it does not fit at
    all. A task from before profiles is judged by the rule it had.

    WORKING SOMEWHERE THE TASK HAS NEVER BEEN COUNTS AGAINST IT. A state
    whose most telling place - its project's folder, its site - is one the
    task has never had, and which shares only something more common with it
    - the mail, the chat - is work somewhere else that happens to have the
    mail open beside it. Credited for the mail, a day of a new project went
    into an email task, and once inside the task held the project's folder
    too, so the rest of it followed. So the place it lacks is weighed
    against the place it shares: as a cost when it is the more telling.
    """
    if not task.total:
        return {"fits": False, "score": 9.0, "rank": -9.0, "distance": 1.0,
                "workspace": 0.0, "place": ""}
    apart = 1.0 - _cosine(vector, task.centre)
    # OR ONE OF ITS PIECES. A task made of several pieces has its centre
    # somewhere between them, and a state plainly about one piece can be
    # far from the middle - it would be left loose, and the pile would
    # make a second task of a piece the first one already holds.
    part = -1
    for index, centre in enumerate(task.parts()):
        nearer = 1.0 - _cosine(vector, centre)
        if nearer < apart:
            apart, part = nearer, index
    shared, place = shares_with(task, places, specific)
    lacking, elsewhere = missing_from(task, places, specific)
    score = apart - counts * shared + counts * max(0.0, lacking - shared)
    if task.profiled:
        ok = score <= far
    else:
        ok = task.accepts(vector, accept_at) >= 0
    return {"fits": ok, "score": round(score, 4), "rank": -score,
            "distance": round(apart, 4), "workspace": round(shared, 4),
            "place": place, "part": part,
            "elsewhere": elsewhere if lacking > shared else ""}


def leaning(task: Task, leisure_of: Optional[Dict[str, Optional[bool]]]) -> Optional[bool]:
    """Whether a task is one of LEISURE (True) or of WORK (False), by most of
    its states whose kind is known - or None while none of them is. A state
    joins only a task of its own kind: grouped together, the evening's films
    took the day's work into them and the other way round."""
    if not leisure_of or not task.members:
        return None
    # BY ALL ITS STATES, not by those whose kind was said: a task of two
    # hundred states, fourteen of them written since kinds were, was called
    # one of leisure on eight against six. Leisure when most of the task is
    # said to be; work likewise; neither while most of it said nothing.
    total = len(task.members)
    enjoyed = sum(1 for one in task.members if leisure_of.get(one) is True)
    worked = sum(1 for one in task.members if leisure_of.get(one) is False)
    if enjoyed * 2 > total:
        return True
    if worked * 2 > total:
        return False
    return None


def place(tasks: Sequence[Task], arrivals: Sequence[str],
          vectors: Dict[str, Sequence[float]],
          places_of: Dict[str, Sequence[str]],
          when_of: Dict[str, Tuple[str, str]],
          specific: Optional[Dict[str, float]] = None, *,
          accept_at: float = ACCEPT_AT, counts: float = PLACE_COUNTS,
          far: float = FAR, tie_within: float = TIE_WITHIN,
          dormant: Sequence[Task] = (),
          revive_at: float = REVIVE_AT,
          leisure_of: Optional[Dict[str, Optional[bool]]] = None,
          barred: Optional[Dict[str, Iterable[str]]] = None) -> Dict[str, Any]:
    """Put each new state in the task it belongs to, or leave it loose.

    A DORMANT task competes too, when the state shares its places strongly:
    work picked up again in the same folder belongs to the task that was
    left there, not to whichever active task it is worded most like. A
    dormant task it only reads like is the caller's to ask about.

    The nearest task by meaning and place, if it is near enough and not
    tied with another; oldest state first, and each task moves as it takes
    one, so a task that is changing follows its own work through the batch.
    What comes back says, for every state, where it went and on what
    evidence.
    """
    specific = specific if specific is not None else specificity(tasks)
    placed: Dict[str, str] = {}
    loose: List[str] = []
    evidence: Dict[str, Dict[str, Any]] = {}
    #: Dormant task -> the states that pick it up; the caller wakes it.
    woken: Dict[str, List[str]] = {}
    ambiguous = 0
    for state in arrivals:
        vector = vectors.get(state)
        if not vector:
            loose.append(state)
            continue
        scored = []
        # ITS OWN KIND ONLY: leisure with leisure, work with work.
        mine = (leisure_of or {}).get(state)

        def other_kind(task: Task) -> bool:
            if mine is None:
                return False
            theirs = leaning(task, leisure_of)
            return theirs is not None and theirs != mine
        # And not a task the model took this state out of, this round
        # (`barred`, state -> task ids).
        not_in = set((barred or {}).get(state) or ())
        for task in tasks:
            if task.retired_at or task.archived_at or other_kind(task) or task.id in not_in:
                continue
            said = fits(task, vector, places_of.get(state, ()), specific,
                        accept_at, counts, far)
            scored.append((said["score"], task, said))
        for task in dormant:
            if task.retired_at or other_kind(task) or task.id in not_in:
                continue
            said = fits(task, vector, places_of.get(state, ()), specific,
                        accept_at, counts, far)
            if said["fits"] and said["workspace"] >= revive_at:
                scored.append((said["score"], task, dict(said, dormant=True)))
        scored.sort(key=lambda row: row[0])
        if not scored or not scored[0][2]["fits"]:
            loose.append(state)
            if scored:
                evidence[state] = {"task": "", "why": "no task near enough",
                                   "nearest": scored[0][1].id,
                                   **scored[0][2]}
            continue
        score, task, said = scored[0]
        if len(scored) > 1 and scored[1][0] - score < tie_within:
            ambiguous += 1
            loose.append(state)
            evidence[state] = {"task": "", "why": "two tasks about equally near",
                               "nearest": task.id,
                               "between": [task.id, scored[1][1].id], **said}
            continue
        if said.get("dormant"):
            woken.setdefault(task.id, []).append(state)
            evidence[state] = {"task": task.id, "why": "picked a dormant "
                               "task up again", **said}
            continue
        began, ended = when_of.get(state, ("", ""))
        task.take(state, vector, places_of.get(state, ()), began, ended)
        task.happened["took"] = int(task.happened.get("took", 0)) + 1
        placed[state] = task.id
        evidence[state] = {"task": task.id, **said}
    for task in tasks:
        if task.happened.get("took") and not task.profiled:
            task.settle(vectors)
    return {"placed": placed, "loose": loose, "ambiguous": ambiguous,
            "arrivals": len(arrivals), "evidence": evidence, "woken": woken}


#: How few of the rest of a task may share the place a group inside it
#: works in, for the group to be work somewhere else.
OTHER_PLACE_SHARE = 0.10
#: How telling a place must be to set a group apart: not the mail or chat.
OTHER_PLACE_SPECIFIC = 0.9


def apart_by_place(task: Task, places_of: Dict[str, Sequence[str]],
                   vectors: Dict[str, Sequence[float]],
                   specific: Dict[str, float], min_nodes: int,
                   share: float = OTHER_PLACE_SHARE,
                   telling: float = OTHER_PLACE_SPECIFIC
                   ) -> Optional[Dict[str, Any]]:
    """A group inside a task whose work is somewhere the rest never is.

    The states that share one telling place - a project's folder, a site -
    which almost none of the task's other states touch, and which are
    further from the rest in meaning than either is wide. That is another
    piece of work that was let in; it is given back as its own task. The
    largest such group, or None.
    """
    return apart_within(task.members, places_of, vectors, specific, min_nodes,
                        share, telling)


def apart_within(members: Sequence[str], places_of: Dict[str, Sequence[str]],
                 vectors: Dict[str, Sequence[float]],
                 specific: Dict[str, float], min_nodes: int,
                 share: float = OTHER_PLACE_SHARE,
                 telling: float = OTHER_PLACE_SPECIFIC,
                 workspace_only: bool = True) -> Optional[Dict[str, Any]]:
    """`apart_by_place` for any group of anything with places and vectors -
    a task's states, a pattern's natures, a project's tasks. With
    `workspace_only` off, any place counts, for things whose places are not
    folders or sites (a nature's places are the projects it came from)."""
    members = [one for one in members if one in vectors]
    if len(members) < 2 * min_nodes:
        return None
    usable = (_a_workspace if workspace_only else (lambda _place: True))
    counted: Dict[str, List[str]] = {}
    for one in members:
        for place in set(places_of.get(one, ())):
            if specific.get(place, 1.0) >= telling and usable(place):
                counted.setdefault(place, []).append(one)
    best = None
    for place, group in counted.items():
        if len(group) < min_nodes or len(group) > len(members) - min_nodes:
            continue
        inside = set(group)
        rest = [one for one in members if one not in inside]
        # The rest must not be there too.
        if sum(1 for one in rest if place in places_of.get(one, ())) \
                > share * len(rest):
            continue
        # And the group must be other work by meaning, not the same work
        # in a second folder: further apart than either side is wide.
        mine, theirs = _mean(group, vectors), _mean(rest, vectors)
        between = 1.0 - _cosine(mine, theirs)
        wide = max(_width(group, vectors, mine), _width(rest, vectors, theirs))
        if between <= wide:
            continue
        # The widest place that sets them apart: a project's folder rather
        # than one of its subfolders.
        key = (len(group), -len(place))
        if best is None or key > best[0]:
            best = (key, {"place": place, "states": list(group),
                          "apart": round(between, 4), "wide": round(wide, 4)})
    if best is None:
        return None
    found = best[1]
    # AND THE REST OF THAT WORK. A state of the same project that names
    # only its site, not its folder, is the project's too: every state of
    # the task working only in places the group has and the rest does not
    # goes with it.
    inside = set(found["states"])
    rest = [one for one in members if one not in inside]
    theirs = {place for one in rest for place in places_of.get(one, ())}
    ours = {place for one in inside for place in places_of.get(one, ())
            if specific.get(place, 1.0) >= telling and usable(place)}
    distinct = {place for place in ours
                if sum(1 for one in rest if place in places_of.get(one, ()))
                <= share * len(rest)}
    for one in rest:
        held = set(places_of.get(one, ()))
        if held & distinct:
            inside.add(one)
    if len(inside) > len(members) - min_nodes:
        return found
    found["states"] = [one for one in members if one in inside]
    found["with_it"] = sorted(distinct)[:10]
    return found


def _a_workspace(place: str) -> bool:
    """A folder or a site - where work is kept - and not a window's or a
    page's title, which names one document rather than where the work is."""
    if ":" in place[:3] or place.startswith(("/", "~")):
        return True
    host = place.split("/", 1)[0]
    return (" " not in place and "." in host
            and host.rsplit(".", 1)[-1].isalpha())


def _mean(ids: Sequence[str], vectors: Dict[str, Sequence[float]]) -> List[float]:
    total: List[float] = []
    for one in ids:
        vector = _unit(vectors[one])
        total = [a + b for a, b in zip(total, vector)] if total else list(vector)
    return _unit(total) if total else []


def _width(ids: Sequence[str], vectors: Dict[str, Sequence[float]],
           centre: Sequence[float]) -> float:
    if not ids or not centre:
        return 0.0
    return sum(1.0 - _cosine(vectors[one], centre) for one in ids) / len(ids)


# -- the round that does not rebuild ---------------------------------------
def append(tasks: Sequence[Task], arrivals: Sequence[str],
           vectors: Dict[str, Sequence[float]],
           accept_at: float = ACCEPT_AT,
           ambiguous_within: float = AMBIGUOUS_WITHIN) -> Dict[str, Any]:
    """Put each new state in the task it belongs to, or leave it loose.

    One dot product per task per state, which is nothing: four hundred
    tasks and forty new states is sixteen thousand multiplications of
    384 numbers, and there are never four hundred tasks.

    Nothing is rebuilt and no summary is written. What comes back is what
    happened, so the caller can decide whether appending is still honest.
    """
    placed: Dict[str, str] = {}
    loose: List[str] = []
    ambiguous = 0
    for state in arrivals:
        vector = vectors.get(state)
        if not vector:
            continue
        scored = sorted(((task.accepts(vector, accept_at), task)
                         for task in tasks if not task.retired_at),
                        key=lambda pair: -pair[0])
        if not scored or scored[0][0] < 0:
            loose.append(state)
            continue
        best, task = scored[0]
        if len(scored) > 1 and scored[1][0] >= 0:
            # Two tasks would both have it. How much better the first one
            # is, is what says whether the boundary between them is still
            # real.
            if best - scored[1][0] < ambiguous_within:
                ambiguous += 1
        task.take(state, vector)
        task.happened["took"] = int(task.happened.get("took", 0)) + 1
        placed[state] = task.id
    for task in tasks:
        if task.happened.get("took"):
            task.settle(vectors)
    return {"placed": placed, "loose": loose, "ambiguous": ambiguous,
            "arrivals": len(arrivals)}


def should_rebuild(tasks: Sequence[Task], appended: Dict[str, Any],
                   loose: Sequence[str], total_states: int,
                   since_rebuild: int, *,
                   drift_limit: float = DRIFT_LIMIT,
                   spread_limit: float = SPREAD_LIMIT,
                   ambiguous_share: float = AMBIGUOUS_SHARE,
                   rebuild_after: float = REBUILD_AFTER,
                   loose_is_a_task: int = LOOSE_IS_A_TASK,
                   ) -> Dict[str, Any]:
    """Whether appending has stopped being honest, and which signal says so.

    Every signal is reported whether or not it fired, because the useful
    question on a quiet round is not "did it rebuild" but "how close was
    it", and a panel that only says yes or no cannot answer that.
    """
    live = [task for task in tasks if not task.retired_at]
    drifted = [task for task in live if task.drift() > drift_limit]
    widened = [task for task in live if task.widened() > spread_limit]
    arrivals = max(1, int(appended.get("arrivals") or 0))
    ambiguous = int(appended.get("ambiguous") or 0)
    share_new = (since_rebuild / total_states) if total_states else 0.0
    signals = [
        {"name": "the loose states are a task",
         "value": len(loose), "limit": loose_is_a_task,
         "fired": len(loose) >= loose_is_a_task,
         "why": ("What no task would take has become enough to be one of "
                 "its own, which appending cannot discover.")},
        {"name": "a task has drifted",
         "value": round(max([task.drift() for task in live] or [0.0]), 4),
         "limit": drift_limit,
         "fired": bool(drifted),
         "why": ("Its centre has moved away from what it was about when "
                 "it was last rebuilt - fed one state at a time until it "
                 "is a different thing.")},
        {"name": "a task has widened",
         "value": round(max([task.widened() for task in live] or [1.0]), 3),
         "limit": spread_limit,
         "fired": bool(widened),
         "why": ("It is absorbing rather than matching: every state a "
                 "little outside, fifty times over.")},
        {"name": "two tasks want the same states",
         "value": round(ambiguous / arrivals, 3),
         "limit": ambiguous_share,
         "fired": (ambiguous / arrivals) >= ambiguous_share and ambiguous > 1,
         "why": ("The data no longer separates them, so the boundary "
                 "between them is stale.")},
        {"name": "enough has arrived since the last rebuild",
         "value": round(share_new, 3), "limit": rebuild_after,
         "fired": share_new >= rebuild_after,
         "why": ("The backstop. Drift is measured against a centre that "
                 "drifting moves, so it can creep without tripping; this "
                 "is what stops the grouping being older than a known "
                 "fraction of the calendar.")},
    ]
    fired = [signal for signal in signals if signal["fired"]]
    return {"rebuild": bool(fired), "signals": signals,
            "because": [signal["name"] for signal in fired],
            "drifted": [task.id for task in drifted],
            "widened": [task.id for task in widened]}


# -- the round that does rebuild -------------------------------------------
def match(old: Sequence[Task], groups: Sequence[Sequence[str]],
          same_at: float = SAME_TASK_AT) -> List[Dict[str, Any]]:
    """Line the new grouping up against the old one.

    By who is in it, not by where it sits in a list. A task that is
    substantially the same thing keeps its id, its history and its
    summary; what split, merged, arrived or ended is recorded as that
    rather than as a renumbering, which is what a reader would otherwise
    have to reverse-engineer from the sizes.
    """
    live = [task for task in old if not task.retired_at]
    taken: Dict[str, bool] = {}
    out: List[Dict[str, Any]] = []
    for members in groups:
        mine = set(members)
        best, overlap = None, 0.0
        for task in live:
            theirs = set(task.members)
            if not theirs:
                continue
            shared = len(mine & theirs)
            if not shared:
                continue
            score = shared / len(mine | theirs)
            if score > overlap:
                best, overlap = task, score
        if best is not None and overlap >= same_at and not taken.get(best.id):
            taken[best.id] = True
            out.append({"members": list(members), "task": best,
                        "kind": "the same task", "overlap": round(overlap, 3)})
            continue
        # THE SAME WORK, WITH MORE OF IT. Everything that task held is
        # still here and nothing else of note joined it, so it keeps its
        # row - and with the row its name, its pieces and its history. A
        # rebuild used to retire it and start a nameless row in its place
        # because it had GROWN, which is what work does.
        kept_of = {}
        for task in live:
            theirs = set(task.members)
            if theirs and not taken.get(task.id):
                kept_of[task.id] = (len(mine & theirs) / len(theirs), task)
        if kept_of:
            ranked = sorted(kept_of.values(), key=lambda one: -one[0])
            share, carried = ranked[0]
            also = ranked[1][0] if len(ranked) > 1 else 0.0
            if share >= CONTINUES_AT and also < ALSO_HOLDS_AT:
                taken[carried.id] = True
                out.append({"members": list(members), "task": carried,
                            "kind": "continues", "kept": round(share, 3),
                            "overlap": round(overlap, 3)})
                continue
        # Not the same task. Whatever it is made of is worth recording:
        # a reader looking for last week's task 3 should find where it
        # went rather than find it gone.
        from_ids = [task.id for task in live
                    if len(mine & set(task.members)) >= max(
                        1, len(set(task.members)) // 2)]
        out.append({"members": list(members), "task": None,
                    "kind": "split from" if len(from_ids) == 1 else
                            "merged from" if len(from_ids) > 1 else "new",
                    "from": from_ids, "overlap": round(overlap, 3)})
    return out


def wants_summary(task: Task, resummarise_after: float = RESUMMARISE_AFTER
                  ) -> bool:
    """Whether the summary it has still describes what it holds.

    A task that gained four states is the same task and says so on the
    page; a task that is half new is a different claim, and the summary
    it has is a description of something that is no longer there.
    """
    if not task.summary_id:
        return True
    # TURNED DOWN BEFORE IT COULD SAY WHAT WAS INSIDE. A group the model
    # called no one task used to be hidden whole, with any real task in it;
    # it is asked once more, now that the answer can take that task out.
    said = task.described or {}
    if said.get("concrete") is False and not said.get("asked_for_buried"):
        return True
    # JUDGED BEFORE AN ANSWER COULD LEAVE THE JUDGEMENT OUT SAFELY. An
    # answer that sent only a status turned a group the model had called
    # no task into one; each task is asked once more, judging it afresh.
    if not said.get("concrete_asked"):
        return True
    if task.profiled:
        # What makes its name wrong is that the work has MOVED - its centre
        # is far from where it was when the name was written - OR THAT IT IS
        # LARGELY OTHER STATES NOW. A big task's centre hardly moves for a
        # day of new work, so a task named for one thing went on being
        # called that while a third of it became another project. A few new
        # states only move where it stands, which the status call answers.
        return (bool(task.anchor) and task.drift() > RENAME_AFTER) \
            or task.changed_since_named() >= resummarise_after
    if not task.summary_members:
        return True
    grown = abs(task.size - task.summary_members) / max(1, task.summary_members)
    return grown >= resummarise_after


#: How many times a read that lost a race is tried again, and how long
#: it waits the first time (doubling, then trebling). Three tries inside
#: a tenth of a second: long enough to outlast a task being written,
#: short enough that a page waiting on it never notices.
WHEN_BUSY_TRIES = 3
WHEN_BUSY_WAIT = 0.02


class Kept:
    """Where the tasks live between runs.

    Two vectors per task are stored as raw floats rather than JSON: at
    three hundred and eighty-four dimensions a task's sum is a page and a
    half of decimal text, and there are two of them, and the file the
    user reads is not improved by that. They are a machine's working
    numbers, so they are kept the way `state_vectors` keeps them.

    Every read tolerates a database made before this table existed, and
    every write tolerates failing: a lost task is a grouping rebuilt from
    nothing, which is what the old behaviour was, not a broken run.
    """

    def __init__(self, db: Any, scope: str = LIVE) -> None:
        self.db = db
        #: Whose tasks these are. The debugger runs the same code over a
        #: different library, and it must not rewrite the real tasks on
        #: the way past - a test that damages what it is testing beside
        #: is not a test.
        self.scope = str(scope or LIVE)

    # -- reading ------------------------------------------------------------
    def live(self) -> List[Task]:
        """The tasks that are neither retired nor dormant, in their own order."""
        rows = self._rows("SELECT * FROM state_tasks WHERE scope = ? "
                          "AND (retired_at IS NULL OR retired_at = '') "
                          "AND (archived_at IS NULL OR archived_at = '') "
                          "ORDER BY number", (self.scope,))
        return [self._task(row) for row in rows]

    def archived(self) -> List[Task]:
        """The dormant tasks: not drawn, and there to be picked up again."""
        rows = self._rows("SELECT * FROM state_tasks WHERE scope = ? "
                          "AND (retired_at IS NULL OR retired_at = '') "
                          "AND archived_at IS NOT NULL AND archived_at != '' "
                          "ORDER BY archived_at DESC", (self.scope,))
        return [self._task(row) for row in rows]

    def loose_since(self) -> Dict[str, str]:
        """Each loose state, and when it first fitted nothing."""
        rows = self._rows("SELECT state_id, since FROM state_task_loose "
                          "WHERE scope = ?", (self.scope,))
        return {str(dict(row).get("state_id") or ""): str(dict(row).get(
            "since") or "") for row in rows}

    def all(self, limit: int = 200) -> List[Task]:
        rows = self._rows(
            "SELECT * FROM state_tasks WHERE scope = ? "
            "ORDER BY retired_at IS NOT NULL, number LIMIT ?",
            (self.scope, int(limit)))
        return [self._task(row) for row in rows]

    def by_ids(self, task_ids: Sequence[str]) -> List[Task]:
        """These tasks, retired or not; any that are gone are left out."""
        wanted = [str(one) for one in task_ids if one]
        if not wanted:
            return []
        holes = ", ".join("?" for _ in wanted)
        rows = self._rows(
            f"SELECT * FROM state_tasks WHERE scope = ? AND id IN ({holes})",
            (self.scope, *wanted))
        return [self._task(row) for row in rows]

    def runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        """The last few runs, newest first."""
        rows = self._rows("SELECT * FROM state_task_runs WHERE scope = ? "
                          "ORDER BY ran_at DESC LIMIT ?",
                          (self.scope, int(limit)))
        out = []
        for row in rows:
            entry = dict(row)
            for field in ("signals", "lineage"):
                entry[field] = _loads(entry.get(field), [])
            out.append(entry)
        return out

    def loose(self) -> List[str]:
        """The states already offered to every task and taken by none."""
        rows = self._rows("SELECT state_id FROM state_task_loose "
                          "WHERE scope = ? ORDER BY since", (self.scope,))
        return [str(dict(row).get("state_id") or "") for row in rows]

    def set_loose(self, state_ids: Iterable[str]) -> int:
        """Remember that these matched nothing, so they are not offered again."""
        if self.db is None:
            return 0
        when = now()
        kept = 0
        for state_id in state_ids:
            try:
                self.db.execute(
                    "INSERT INTO state_task_loose (state_id, scope, since) "
                    "VALUES (?, ?, ?) ON CONFLICT(state_id, scope) DO NOTHING",
                    (state_id, self.scope, when))
                kept += 1
            except Exception:    # pragma: no cover
                log.debug("could not remember %s as loose", state_id,
                          exc_info=True)
        return kept

    def forget_loose(self, state_ids: Optional[Iterable[str]] = None) -> int:
        """They are in a task now, or gone. Either way they are not loose."""
        if self.db is None:
            return 0
        if state_ids is None:
            try:
                self.db.execute("DELETE FROM state_task_loose WHERE scope = ?",
                                (self.scope,))
            except Exception:    # pragma: no cover
                log.debug("could not clear the loose pile", exc_info=True)
            return 0
        gone = 0
        for state_id in state_ids:
            try:
                self.db.execute(
                    "DELETE FROM state_task_loose WHERE state_id = ? "
                    "AND scope = ?", (state_id, self.scope))
                gone += 1
            except Exception:    # pragma: no cover
                log.debug("could not forget %s", state_id, exc_info=True)
        return gone

    def states_at_last_rebuild(self) -> int:
        """How many states there were when the grouping was last rebuilt.

        The backstop is "how much of the calendar is new", and the
        obvious way to count that - adding up the arrivals of each round
        since - is wrong. A state that no task would take is offered
        again every round, so a handful of states that fit nowhere would
        be counted over and over and force a rebuild that nothing had
        actually asked for. The calendar's own growth cannot double-count.

        Returns -1 when there has never been a rebuild, which is not the
        same as a calendar that was rebuilt when it was empty.
        """
        rows = self._rows("SELECT states FROM state_task_runs "
                          "WHERE scope = ? AND rebuilt = 1 "
                          "ORDER BY ran_at DESC LIMIT 1", (self.scope,))
        return int(dict(rows[0]).get("states") or 0) if rows else -1

    def _rows(self, sql: str, args: Tuple[Any, ...] = ()) -> List[Any]:
        """The rows, or an empty list - and it waits before saying so.

        AN EMPTY LIST MEANT TWO THINGS AND ONE OF THEM WAS A LIE. The
        quiet `except` here was written for a table that does not exist
        yet, which is a real case on a first run. But it also caught a
        read that lost a race with a write, and then "the folder has no
        tasks" was returned for a folder with nine - so the panel threw
        away its drawing and told the reader to wait for their first
        hundred states. Reported as the picture emptying every time a
        task was saved, which is exactly when the race happens.

        So a read that was merely in the way is tried again, briefly, and
        anything that still fails is said out loud rather than whispered
        into the debug log.
        """
        if self.db is None:
            return []
        last: Optional[Exception] = None
        for attempt in range(WHEN_BUSY_TRIES):
            try:
                return list(self.db.query(sql, args))
            except Exception as exc:
                last = exc
                busy = any(word in str(exc).lower()
                           for word in ("locked", "busy"))
                if not busy:
                    break
                time.sleep(WHEN_BUSY_WAIT * (attempt + 1))
        missing = "no such table" in str(last).lower()
        if missing:
            log.debug("the task table is not there yet", exc_info=True)
        else:
            log.warning("could not read the kept tasks: %s", last)
        return []

    @staticmethod
    def _task(row: Any) -> Task:
        row = dict(row)
        return Task(
            id=row.get("id", ""), number=row.get("number", 0),
            seat=row.get("seat", -1),
            members=_loads(row.get("members"), []),
            total=_floats(row.get("total")), anchor=_floats(row.get("anchor")),
            radius=row.get("radius", 0.0), spread=row.get("spread", 0.0),
            anchor_radius=row.get("anchor_radius", 0.0),
            version=row.get("version", 1),
            born_of=_loads(row.get("born_of"), {"kind": "new", "from": []}),
            summary_id=row.get("summary_id") or "",
            summary_at=row.get("summary_at") or "",
            summary_members=row.get("summary_members", 0),
            described=_loads(row.get("described"), {}),
            profile=_loads(row.get("profile"), {}),
            archived_at=row.get("archived_at") or "",
            created_at=row.get("created_at"), rebuilt_at=row.get("rebuilt_at"),
            updated_at=row.get("updated_at"), retired_at=row.get("retired_at"))

    # -- writing ------------------------------------------------------------
    def save(self, tasks: Iterable[Task]) -> int:
        """Write these tasks down, keeping what is already there."""
        if self.db is None:
            return 0
        tasks = list(tasks)
        # THE NEXT STEPS ARE THE ROW'S, NOT THIS COPY'S. The user edits
        # them from the page while a round holds its tasks in memory, so
        # the round's copy is whatever they were when it read them - and
        # writing it back would quietly undo the edit. See `todo`.
        with todo.LOCK:
            todo.carried(self.db, "state_tasks", self.scope, tasks)
            return self._save(tasks)

    def _save(self, tasks: List[Task]) -> int:
        written = 0
        for task in tasks:
            try:
                self.db.execute(
                    "INSERT INTO state_tasks (id, number, members, total, "
                    "anchor, dims, radius, spread, anchor_radius, version, "
                    "born_of, summary_id, summary_at, summary_members, "
                    "described, seat, profile, archived_at, "
                    "created_at, rebuilt_at, updated_at, retired_at, "
                    "scope) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    "?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET number = excluded.number, "
                    "members = excluded.members, total = excluded.total, "
                    "anchor = excluded.anchor, dims = excluded.dims, "
                    "radius = excluded.radius, spread = excluded.spread, "
                    "anchor_radius = excluded.anchor_radius, "
                    "version = excluded.version, born_of = excluded.born_of, "
                    "summary_id = excluded.summary_id, "
                    "summary_at = excluded.summary_at, "
                    "summary_members = excluded.summary_members, "
                    "described = excluded.described, "
                    "seat = excluded.seat, "
                    "profile = excluded.profile, "
                    "archived_at = excluded.archived_at, "
                    "rebuilt_at = excluded.rebuilt_at, "
                    "updated_at = excluded.updated_at, "
                    "retired_at = excluded.retired_at "
                    # Belt and braces behind the unique ids: one scope can
                    # never update another's row, whatever an id collision
                    # would otherwise allow.
                    "WHERE state_tasks.scope = excluded.scope",
                    (task.id, task.number, json.dumps(task.members),
                     _blob(task.total), _blob(task.anchor),
                     len(task.total), task.radius, task.spread,
                     task.anchor_radius, task.version,
                     json.dumps(task.born_of), task.summary_id,
                     task.summary_at or None, task.summary_members,
                     json.dumps(task.described), task.seat,
                     json.dumps(task.profile), task.archived_at or None,
                     task.created_at, task.rebuilt_at, task.updated_at,
                     task.retired_at or None, self.scope))
                written += 1
            except Exception:    # pragma: no cover - see the class note
                log.debug("could not keep task %s", task.id, exc_info=True)
        return written

    def retire(self, task_ids: Iterable[str], at: str = "") -> int:
        """End these tasks without deleting them.

        A task that stopped being a task is a fact about the user's
        months, and the tasks that replaced it point back at it.
        """
        if self.db is None:
            return 0
        when = at or now()
        ended = 0
        for task_id in task_ids:
            try:
                self.db.execute(
                    "UPDATE state_tasks SET retired_at = ?, updated_at = ? "
                    "WHERE id = ? AND scope = ?",
                    (when, when, task_id, self.scope))
                ended += 1
            except Exception:    # pragma: no cover
                log.debug("could not retire task %s", task_id, exc_info=True)
        return ended

    def record(self, entry: Dict[str, Any]) -> None:
        """One row for what this run did, so the panel can say why."""
        if self.db is None:
            return
        tree = entry.get("tree")
        try:
            self.db.execute(
                "INSERT INTO state_task_runs (id, ran_at, states, arrivals, "
                "placed, loose, ambiguous, rebuilt, signals, lineage, tree, "
                "seen_from, scope, shape) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (entry.get("id") or f"run-{now()}", entry.get("ran_at") or now(),
                 int(entry.get("states", 0)), int(entry.get("arrivals", 0)),
                 int(entry.get("placed", 0)), int(entry.get("loose", 0)),
                 int(entry.get("ambiguous", 0)),
                 1 if entry.get("rebuilt") else 0,
                 json.dumps(entry.get("signals") or []),
                 json.dumps(entry.get("lineage") or []),
                 json.dumps(tree) if tree else "",
                 entry.get("seen_from") or "", self.scope,
                 json.dumps(entry.get("shape") or {})))
        except Exception:        # pragma: no cover
            log.debug("could not record the run", exc_info=True)
            return
        if not tree:
            return
        # Only the newest rebuild keeps its picture. A thousand states is
        # eighty kilobytes of brackets, and a year of them on rows nobody
        # will open again is a database that grows for no reader.
        try:
            self.db.execute(
                "UPDATE state_task_runs SET tree = '' WHERE scope = ? "
                "AND tree != '' AND id != ?",
                (self.scope, entry.get("id") or ""))
        except Exception:        # pragma: no cover
            log.debug("could not drop the older trees", exc_info=True)

    def seen_from(self) -> str:
        """How far back this grouping has ever looked.

        Everything older is before the beginning, not a backlog: the
        first round reads a bounded window, and on a calendar of five
        thousand the four thousand it never read must be treated as seen
        or every later round would find them in no task and rebuild on
        them forever.
        """
        rows = self._rows("SELECT seen_from FROM state_task_runs "
                          "WHERE scope = ? AND seen_from != '' "
                          "ORDER BY ran_at DESC LIMIT 1", (self.scope,))
        return str(dict(rows[0]).get("seen_from") or "") if rows else ""

    def shape_at_last_rebuild(self) -> Dict[str, Any]:
        """The settings the grouping was built with, last time it was built."""
        rows = self._rows("SELECT shape FROM state_task_runs "
                          "WHERE scope = ? AND rebuilt = 1 "
                          "ORDER BY ran_at DESC LIMIT 1", (self.scope,))
        return _loads(dict(rows[0]).get("shape"), {}) if rows else {}

    def last_tree(self) -> Dict[str, Any]:
        """The picture as the last rebuild left it, and when that was.

        An appending round does not build a tree - joining every state to
        every other is exactly the work appending exists to avoid - so
        the page shows the last one, labelled as of then rather than
        passed off as this hour's.
        """
        rows = self._rows("SELECT ran_at, tree FROM state_task_runs "
                          "WHERE scope = ? AND tree != '' "
                          "ORDER BY ran_at DESC LIMIT 1", (self.scope,))
        if not rows:
            return {}
        row = dict(rows[0])
        tree = _loads(row.get("tree"), {})
        if isinstance(tree, dict) and tree:
            tree["as_of"] = row.get("ran_at") or ""
        return tree if isinstance(tree, dict) else {}


class Project:
    """Several tasks that are one larger piece of work.

    Found from where the work lives - the same project folder, the same
    paper - and only kept once the model has said they are one thing. The
    tasks keep their own names, profiles and pieces under it.
    """

    def __init__(self, **fields: Any) -> None:
        self.id: str = fields.get("id", "")
        self.name: str = fields.get("name", "") or ""
        self.why: str = fields.get("why", "") or ""
        self.home: str = fields.get("home", "") or ""
        self.tasks: List[str] = list(fields.get("tasks") or [])
        self.places: List[str] = list(fields.get("places") or [])
        #: The tasks the model said are not part of it, each with how many
        #: states it had then - a task that has grown by a third since is
        #: asked about again, because it may not be the same work any more.
        held = fields.get("declined") or {}
        self.declined: Dict[str, int] = (
            dict(held) if isinstance(held, dict)
            else {str(one): 0 for one in held})
        #: Its status, the urgency and importance of its most pressing task,
        #: and how many tasks it had when it was named.
        self.described: Dict[str, Any] = dict(fields.get("described") or {})
        self.created_at: str = fields.get("created_at") or now()
        self.updated_at: str = fields.get("updated_at") or self.created_at

    def holds(self, places: Sequence[str]) -> bool:
        """Whether a state with these places is in this project's places."""
        own = [one for one in [self.home] + self.places if one]
        for place in places:
            for mine in own:
                if place == mine or place.startswith(mine + "/"):
                    return True
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "why": self.why,
                "home": self.home, "tasks": list(self.tasks),
                "places": list(self.places),
                "status": str(self.described.get("status") or ""),
                # Its next steps: a project is always at the top, so it
                # always has a list, even an empty one.
                **todo.drawn(self.described),
                "created_at": self.created_at,
                "updated_at": self.updated_at}

    def turned_down(self, task: "Task") -> bool:
        """Whether the model said this task is not part of it - and it has
        not grown by a third since, which would make it worth asking again."""
        if task.id not in self.declined:
            return False
        then = int(self.declined.get(task.id) or 0)
        return not (then and task.count >= then * 1.5)


class Projects:
    """Where the projects live between rounds."""

    def __init__(self, db: Any, scope: str = LIVE) -> None:
        self.db = db
        self.scope = str(scope or LIVE)

    def all(self, include_history: bool = False) -> List[Project]:
        """The projects in use - and with `include_history`, also those of
        the scheme before tasks and projects were made per run (section
        34.5), kept as history."""
        if self.db is None:
            return []
        try:
            rows = self.db.query("SELECT * FROM state_projects WHERE scope = ? "
                                 "ORDER BY created_at", (self.scope,))
            if not include_history:
                rows = [row for row in rows
                        if not (dict(row).get("archived_at") or "")]
        except Exception:
            log.debug("the projects table is not there yet", exc_info=True)
            return []
        out = []
        for row in rows:
            row = dict(row)
            out.append(Project(
                id=row.get("id", ""), name=row.get("name", ""),
                why=row.get("why", ""), home=row.get("home", ""),
                tasks=_loads(row.get("tasks"), []),
                places=_loads(row.get("places"), []),
                declined=_loads(row.get("declined"), {}),
                described=_loads(row.get("described"), {}),
                created_at=row.get("created_at"),
                updated_at=row.get("updated_at")))
        return out

    def save(self, project: Project) -> None:
        if self.db is None:
            return
        # Its next steps as the row has them, for the reason `Kept.save`
        # gives: the user may have changed them since this copy was read.
        with todo.LOCK:
            todo.carried(self.db, "state_projects", self.scope, [project])
            self._save(project)

    def _save(self, project: Project) -> None:
        project.updated_at = now()
        try:
            self.db.execute(
                "INSERT INTO state_projects (id, scope, name, why, home, tasks, "
                "places, declined, described, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name = excluded.name, "
                "why = excluded.why, home = excluded.home, "
                "tasks = excluded.tasks, places = excluded.places, "
                "declined = excluded.declined, described = excluded.described, "
                "updated_at = excluded.updated_at "
                "WHERE state_projects.scope = excluded.scope",
                (project.id, self.scope, project.name, project.why,
                 project.home, json.dumps(project.tasks),
                 json.dumps(project.places), json.dumps(project.declined),
                 json.dumps(project.described),
                 project.created_at, project.updated_at))
        except Exception:        # pragma: no cover
            log.debug("could not keep project %s", project.id, exc_info=True)

    def forget(self) -> None:
        if self.db is None:
            return
        try:
            self.db.execute("DELETE FROM state_projects WHERE scope = ?",
                            (self.scope,))
        except Exception:        # pragma: no cover
            log.debug("could not clear the projects", exc_info=True)


def _blob(values: Sequence[float]) -> bytes:
    return array.array("f", [float(value) for value in values]).tobytes()


def _floats(blob: Any) -> List[float]:
    if not blob:
        return []
    try:
        return list(array.array("f", bytes(blob)))
    except Exception:            # pragma: no cover - a truncated row
        return []


def _loads(text: Any, fallback: Any) -> Any:
    if not text:
        return fallback
    try:
        return json.loads(text)
    except Exception:            # pragma: no cover
        return fallback


__all__ = ["Task", "Kept", "Project", "Projects", "LIVE", "SANDBOX",
           "append", "match", "should_rebuild",
           "place", "fits", "specificity", "shares_with",
           "HALF_LIFE_STATES", "MEMBERS_KEPT", "RECENT_READ",
           "QUIET_AFTER_DAYS", "ARCHIVE_AFTER_DAYS", "LOOSE_KEPT_DAYS",
           "FAR", "PLACE_COUNTS", "TIE_WITHIN", "REVIVE_AT",
           "RENAME_AFTER",
           "wants_summary", "now",
           "ACCEPT_AT", "DRIFT_LIMIT", "SPREAD_LIMIT", "AMBIGUOUS_WITHIN",
           "LOOSE_IS_A_TASK",
           "AMBIGUOUS_SHARE", "REBUILD_AFTER", "SAME_TASK_AT",
           "RESUMMARISE_AFTER"]
