"""The state calendar (section 34.1).

A state is something that is true over a stretch of time: what the user has
been doing this week, what they are doing this hour, what they intend to do,
and what the agent intends to do for them. It is written by the heartbeat and
it is what the calendar shows.

Three things distinguish it from a scheduled task:

* **A state is not executed.** It says what is the case or what is meant to
  happen; the scheduler is what makes things happen. Displaying an intention
  and acting on it are different, and conflating them means every note about
  the future becomes something that fires.
* **A state covers a span, not an instant.** "Writing the report this week"
  is true of Tuesday and of Thursday, and is the context an answer on either
  day needs. A task has a moment; a state has a period.
* **A state says where it came from.** Something the user said is not the
  same as something the agent inferred from watching them, and an answer that
  treats the two alike will eventually tell the user they decided something
  they never decided.

States are the first half of the context for any answer: what is true right
now is established before the library is searched (section 44.1).

Every state is also a file in ``states/`` (section 39.1, as amended), and that
file is rewritten whenever the state changes - including when the change came
from the user editing the task on the calendar.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...common.events.models import new_id, parse_iso, utcnow
from ...common.reason import BY_AGENT, BY_USER, reason_for
from ...data_manage.text import search_terms

log = logging.getLogger(__name__)

#: Where a state came from. The distinction is reported wherever a state is
#: shown or used, because the agent's guess and the user's instruction carry
#: very different authority.
SOURCE_USER = "user"                 # the user said so
SOURCE_AGENT = "agent"               # the agent inferred it from activity
SOURCE_TASK = "task"                 # it accompanies a scheduled task
SOURCES = (SOURCE_USER, SOURCE_AGENT, SOURCE_TASK)

#: Whose state it is: something the user is doing, or something the agent
#: means to do for them.
ABOUT_USER = "user"
ABOUT_AGENT = "agent"
#: A state's `kind` in its metadata when it is the part of a day that is no
#: piece of work - meals, rest, entertainment, chatting with friends. It is
#: recorded and shown, and never grouped into a task.
ROUTINE = "routine"
#: What a state is for, in its metadata's `kind`: WORK; LEISURE - done for
#: enjoyment, grouped into tasks of its own, never mixed with work, and what
#: the recommendations treat as a pastime; or ROUTINE (above).
WORK = "work"
LEISURE = "leisure"
KINDS = (WORK, LEISURE, ROUTINE)

ACTIVE = "active"
DONE = "done"
RETIRED = "retired"


#: How many states the floor carries before the question has to choose.
#:
#: At the measured average of about 968 characters a state, twelve is roughly
#: three thousand tokens - enough to say what is going on, small enough that
#: it is not most of the turn. The old value was forty, which was forty
#: because nothing had counted what forty cost.
STATES_IN_FLOOR = 12

#: The most that will ever be read to choose from. A bound on the choosing,
#: not on what may be active.
STATES_CEILING = 200


def most_like(query: str, states: Sequence["State"], limit: int) -> List["State"]:
    """The states this question is about, when they cannot all be carried.

    Scored on keyword overlap rather than on recency, because the floor is
    for answering *this* question: a state started an hour ago about
    something else is worse context than one started last week about exactly
    this. Ties - and a query with nothing to match on - fall back to the
    longest span, which is the state most likely to still be true.
    """
    terms = set(search_terms(query))
    if not terms:
        return sorted(states, key=lambda s: s.span_hours, reverse=True)[:limit]

    def score(state: "State") -> tuple:
        words = set(search_terms(f"{state.title} {state.detail}"))
        if not words:
            return (0.0, state.span_hours)
        hit = terms & words
        # Proportion of the question matched, not raw count: a long state
        # would otherwise win by containing more words than a short one.
        return (len(hit) / len(terms), state.span_hours)

    return sorted(states, key=score, reverse=True)[:limit]


@dataclass
class State:
    """One thing that is true, or meant to be, over a stretch of time."""

    id: str = ""
    title: str = ""
    detail: str = ""
    #: user | agent | task - where this came from.
    source: str = SOURCE_AGENT
    #: user | agent - whose state it is.
    about: str = ABOUT_USER
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    status: str = ACTIVE
    #: Set when the state accompanies something the scheduler will run.
    task_id: str = ""
    #: How many times it has been extended, so that a state which is never
    #: finished can be noticed rather than extended forever.
    extensions: int = 0
    #: Why the agent believes this (section 52.7, as amended). ``source`` says
    #: who; this says on what judgement. A state is the agent describing the
    #: user's situation back to them, and it can be wrong - so it has to be
    #: something they can argue with rather than a bare assertion.
    reason: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def span_hours(self) -> float:
        if not self.starts_at or not self.ends_at:
            return 0.0
        return (self.ends_at - self.starts_at).total_seconds() / 3600.0

    def covers(self, moment: datetime) -> bool:
        if self.starts_at and moment < self.starts_at:
            return False
        if self.ends_at and moment > self.ends_at:
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "detail": self.detail,
            "source": self.source,
            "about": self.about,
            "starts_at": self.starts_at.isoformat() if self.starts_at else None,
            "ends_at": self.ends_at.isoformat() if self.ends_at else None,
            "status": self.status,
            "task_id": self.task_id,
            "extensions": self.extensions,
            "reason": self.reason,
            # No horizon. It was "short" or "long" read off the span, which
            # is the span again in a word - and a word beside the dates is
            # a second place for them to disagree. A reader who wants to
            # know how long it runs has the dates and `span_hours`.
            "span_hours": round(self.span_hours, 2),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "metadata": self.metadata,
        }

    def as_context(self) -> str:
        """One line of context, saying what it is and how much to trust it."""
        when = ""
        if self.starts_at and self.ends_at:
            when = (f" [{self.starts_at.isoformat(timespec='minutes')} to "
                    f"{self.ends_at.isoformat(timespec='minutes')}]")
        whose = "the agent intends" if self.about == ABOUT_AGENT else "the user"
        origin = {SOURCE_USER: "the user said this",
                  SOURCE_AGENT: "inferred by the agent from activity",
                  SOURCE_TASK: "accompanies a scheduled task"}.get(
                      self.source, self.source)
        return (f"- ({whose}){when} {self.title}"
                + (f": {self.detail}" if self.detail else "")
                + f"  (source: {origin}"
                + (f"; why: {self.reason}" if self.reason else "") + ")")

    @staticmethod
    def from_row(row: Any) -> "State":
        import json

        return State(
            id=row["id"],
            title=row["title"],
            detail=row["detail"] or "",
            source=row["source"] or SOURCE_AGENT,
            about=row["about"] or ABOUT_USER,
            starts_at=parse_iso(row["starts_at"]),
            ends_at=parse_iso(row["ends_at"]),
            status=row["status"] or ACTIVE,
            task_id=row["task_id"] or "",
            extensions=int(row["extensions"] or 0),
            reason=(row["reason"] if "reason" in row.keys() else "") or "",
            created_at=parse_iso(row["created_at"]),
            updated_at=parse_iso(row["updated_at"]),
            metadata=json.loads(row["metadata"] or "{}"),
        )


class StateStore:
    """Where states live. Read by the calendar, by retrieval, and by the
    heartbeat step that decides whether they are finished."""

    def __init__(self, db: Any, library: Any = None) -> None:
        self.db = db
        #: Section 39.1, as amended: every state is also a file in states/,
        #: and the file says what the state currently is. Optional because a
        #: StateStore is constructed in tests and migrations with no library
        #: behind it, and a missing mirror must never stop a state being
        #: written - the database is the operational record, the file is what
        #: makes it readable.
        self.library = library

    # -- the file behind each state -----------------------------------------
    #: Where the mirrored file lives, kept on the state so that finding it
    #: again is not a scan of the folder. It rides along in the state's own
    #: metadata, which is already persisted.
    RECORD_PATH = "record_path"

    def _mirror(self, state: "State") -> None:
        """Write the state's file, creating it or bringing it up to date.

        Called from every path that changes a state - creation, extension,
        closing, and the scheduler moving a state because the user dragged the
        task across the calendar (section 60.1). All of those go through
        ``add`` or ``_save``, which is why mirroring in those two is enough.

        A failure here is logged and swallowed. The state has already been
        written to the database by the time this runs, and losing the state
        because the disk was busy would be a far worse outcome than a file
        that is briefly out of date - the next change rewrites it, and the
        data heartbeat reindexes whatever it finds.
        """
        if self.library is None:
            return
        try:
            from ...data_manage.library import STATES, Record

            history = list(state.metadata.get("history") or [])
            body = [state.detail or "(no detail)", ""]
            body.append(f"why: {state.reason}")
            body.append(f"status: {state.status}")
            if state.starts_at and state.ends_at:
                body.append(f"span: {state.starts_at.isoformat(timespec='minutes')}"
                            f" to {state.ends_at.isoformat(timespec='minutes')}")
            if state.task_id:
                body.append(f"accompanies task: {state.task_id}")
            if state.extensions:
                body.append(f"extended {state.extensions} time(s)")
            if history:
                body.extend(["", "## What it said before", *(f"- {h}" for h in history)])

            record = Record(
                folder=STATES,
                id=state.id,
                title=state.title or "(untitled state)",
                summary=state.as_context().lstrip("- "),
                body="\n".join(body),
                keywords=[w for w in (state.status, state.about,
                                      state.source) if w],
                references=[r for r in (
                    f"state: {state.id}",
                    f"task: {state.task_id}" if state.task_id else "",
                ) if r],
                source=state.source or "agent",
                created_at=state.created_at or utcnow(),
                observed_from=state.starts_at,
                observed_to=state.ends_at,
                metadata={"status": state.status, "task_id": state.task_id,
                          "extensions": state.extensions,
                          "reason": state.reason},
                path=str(state.metadata.get(self.RECORD_PATH) or ""),
            )
            written = self.library.put(record)
            if written.path and state.metadata.get(self.RECORD_PATH) != written.path:
                # First write: remember where it went, so the next change
                # updates this file rather than making a second one.
                state.metadata = {**state.metadata, self.RECORD_PATH: written.path}
                self._write_metadata(state)
        except Exception:            # pragma: no cover - a mirror, not the record
            log.debug("could not mirror state %s to disk", state.id, exc_info=True)

    def _write_metadata(self, state: "State") -> None:
        import json

        self.db.execute("UPDATE states SET metadata = ? WHERE id = ?",
                        (json.dumps(state.metadata, ensure_ascii=False), state.id))

    # -- writing ------------------------------------------------------------
    def add(self, state: State) -> State:
        import json

        now = datetime.now(timezone.utc)
        state.id = state.id or new_id("state")
        state.created_at = state.created_at or now
        state.updated_at = now
        if state.source not in SOURCES:
            state.source = SOURCE_AGENT
        # Section 52.7: a state always says why the agent believes it. The
        # user's own state needs no justification - they said so.
        state.reason = reason_for(
            state.reason, BY_USER if state.source == SOURCE_USER else BY_AGENT,
            f"state {state.title!r}")
        # WHERE THE USER WAS STANDING WHEN THIS HAPPENED. Stamped here, on
        # the way in, rather than asked of whoever is writing the state:
        # it is a fact about the machine's clock, every writer would have
        # to remember it, and one that forgot would leave a state that
        # cannot be placed in anybody's week. Not recomputed on the way
        # out either - the user may be in another zone by then, and the
        # honest answer is the zone they were in at the time.
        if "local" not in state.metadata:
            state.metadata = dict(state.metadata)
            state.metadata["local"] = local_span(state.starts_at,
                                                 state.ends_at, now)
        self.db.execute(
            "INSERT INTO states (id, title, detail, source, about, starts_at, ends_at, "
            "status, task_id, extensions, reason, created_at, updated_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (state.id, state.title, state.detail, state.source, state.about,
             state.starts_at.isoformat() if state.starts_at else None,
             state.ends_at.isoformat() if state.ends_at else None,
             state.status, state.task_id, state.extensions, state.reason,
             state.created_at.isoformat(), state.updated_at.isoformat(),
             json.dumps(state.metadata, ensure_ascii=False)),
        )
        self._mirror(state)
        return state

    def still_going(self, state_id: str, until: datetime,
                    note: str = "") -> Optional[State]:
        """Seen again, and still true: move the end and count nothing.

        Not an extension. An extension is a judgement that something is
        not finished and ought to run longer, and the count of them is how
        a state nobody ever completes gets noticed. A sighting is an
        observation that the thing is still happening, which is what every
        review makes about everything already on the calendar - counting
        those as extensions would make "extended eleven times" mean "the
        agent looked eleven times", and the signal it exists for is gone.
        """
        state = self.get(state_id)
        if state is None:
            return None
        if until > (state.ends_at or until):
            state.ends_at = until
        state.updated_at = datetime.now(timezone.utc)
        if note:
            state.metadata = dict(state.metadata)
            seen = list(state.metadata.get("seen_again") or [])
            seen.append(note[:300])
            state.metadata["seen_again"] = seen[-10:]
        self._save(state)
        return state

    def extend(self, state_id: str, until: datetime, note: str = "") -> Optional[State]:
        """Push a state's end out, because it is not finished yet.

        The count of extensions is kept: a state extended again and again is
        one the user is not actually completing, and that is worth noticing
        rather than hiding behind an ever-moving end date.
        """
        state = self.get(state_id)
        if state is None:
            return None
        state.ends_at = until
        state.extensions += 1
        state.updated_at = datetime.now(timezone.utc)
        if note:
            state.metadata = dict(state.metadata)
            state.metadata.setdefault("extension_notes", []).append(note[:300])
        self._save(state)
        return state

    def close(self, state_id: str, status: str = DONE, reason: str = "") -> Optional[State]:
        """Finish a state - completed, or no longer worth carrying."""
        state = self.get(state_id)
        if state is None:
            return None
        state.status = status if status in (DONE, RETIRED) else DONE
        state.updated_at = datetime.now(timezone.utc)
        if reason:
            state.metadata = dict(state.metadata)
            state.metadata["closed_because"] = reason[:300]
        self._save(state)
        return state

    def _save(self, state: State) -> None:
        import json

        self.db.execute(
            "UPDATE states SET title = ?, detail = ?, source = ?, about = ?, "
            "starts_at = ?, ends_at = ?, status = ?, task_id = ?, extensions = ?, "
            "reason = ?, updated_at = ?, metadata = ? WHERE id = ?",
            (state.title, state.detail, state.source, state.about,
             state.starts_at.isoformat() if state.starts_at else None,
             state.ends_at.isoformat() if state.ends_at else None,
             state.status, state.task_id, state.extensions, state.reason,
             (state.updated_at or datetime.now(timezone.utc)).isoformat(),
             json.dumps(state.metadata, ensure_ascii=False), state.id),
        )
        self._mirror(state)

    # -- reading ------------------------------------------------------------
    def get(self, state_id: str) -> Optional[State]:
        row = self.db.query_one("SELECT * FROM states WHERE id = ?", (state_id,))
        return State.from_row(row) if row else None

    def for_task(self, task_id: str, limit: int = 10) -> List[State]:
        """The states written to accompany one task.

        Needed because a task and its state are two rows saying one thing: a
        task moved to Friday whose state still claims Thursday is a record
        that contradicts itself, and the contradiction is invisible - both
        rows look fine on their own.
        """
        if not task_id:
            return []
        rows = self.db.query(
            "SELECT * FROM states WHERE task_id = ? AND status = ? "
            "ORDER BY starts_at DESC LIMIT ?",
            (task_id, ACTIVE, limit))
        return [State.from_row(row) for row in rows]

    def covering(self, moment: Optional[datetime] = None,
                 limit: int = STATES_IN_FLOOR,
                 query: str = "") -> List[State]:
        """The active states true at this moment, longest span first.

        This is the first half of the context for an answer. A state that
        spans the week is as true on Thursday as on Tuesday, so it is found by
        the span containing the moment rather than by when it was written.

        **All of them, until there are too many.** The floor is not searched -
        it is what can be known without asking a question - so while it fits,
        every active state goes in. Measured on a real installation it stopped
        fitting: 82 active states at an average of 968 characters is about
        twenty thousand tokens, on every turn, before the question has been
        read. The old ceiling of forty kept that finite and cut by
        ``starts_at``, which is to say it dropped whichever states happened to
        be oldest - a decision about this question made without reference to
        it.

        So past the limit the query chooses. Keyword overlap, the same
        tokenising the index uses, so that the words a state is picked by are
        the words a search would have matched. With no query there is nothing
        to choose on and the longest spans win, which is the old behaviour.
        """
        moment = moment or datetime.now(timezone.utc)
        stamp = moment.isoformat()
        # A generous read, because the choice is made below rather than by
        # the order rows come back in. Bounded so that a runaway number of
        # states cannot turn this into a full scan.
        rows = self.db.query(
            "SELECT * FROM states WHERE status = ? "
            "AND (starts_at IS NULL OR starts_at <= ?) "
            "AND (ends_at IS NULL OR ends_at >= ?) "
            "ORDER BY starts_at DESC LIMIT ?",
            (ACTIVE, stamp, stamp, max(int(limit) * 8, STATES_CEILING)))
        states = [State.from_row(row) for row in rows]
        if len(states) > limit:
            states = most_like(query, states, limit)
        states.sort(key=lambda s: s.span_hours, reverse=True)
        return states

    def between(self, start: datetime, end: datetime,
                limit: int = 200) -> List[State]:
        """Every state overlapping a window, for drawing the calendar."""
        rows = self.db.query(
            "SELECT * FROM states WHERE status != ? "
            "AND (ends_at IS NULL OR ends_at >= ?) "
            "AND (starts_at IS NULL OR starts_at <= ?) "
            "ORDER BY starts_at LIMIT ?",
            (RETIRED, start.isoformat(), end.isoformat(), limit))
        return [State.from_row(row) for row in rows]

    def review_groups(self, limit: int = 20) -> Tuple[List[State], List[State]]:
        """The states to judge, in the two groups they fall into.

        Returned separately rather than as one list, because the difference
        between them is the whole of what the judging is about and it is known
        here: one query selects the ones whose end has passed, another the ones
        still running. Flattening the two and handing over the result asks
        whoever reads it to work out again, from the dates, what a SELECT has
        just established - and when the reader is a model given twenty
        identical-looking lines and the current time somewhere else in the
        prompt, it mostly does not.

        A state with no end at all counts as ended: nothing will ever close it
        on its own, so it needs a verdict as much as one whose date has gone.
        """
        now = datetime.now(timezone.utc).isoformat()
        rows = self.db.query(
            "SELECT * FROM states WHERE status = ? "
            "AND (ends_at IS NULL OR ends_at <= ?) ORDER BY ends_at LIMIT ?",
            (ACTIVE, now, limit))
        ended = [State.from_row(row) for row in rows]
        seen = {e.id for e in ended}
        running = [s for s in self.covering(limit=limit) if s.id not in seen]
        return ended, running[:max(0, limit - len(ended))]

    def needing_review(self, limit: int = 20) -> List[State]:
        """The same states as one list, ended first."""
        ended, running = self.review_groups(limit)
        return (ended + running)[:limit]

    #: The sources that are a reading of what the user did.
    #:
    #: Not `task`. A state written to accompany a scheduled task -
    #: "Scheduled: check the FieldConf deadline" - is the agent's own note that
    #: something is queued. It is a real state and it belongs on the
    #: calendar, but it is not an observation of the user: nobody did it,
    #: it has no evidence under it, and the words in it are the words of
    #: the task the agent wrote for itself.
    OBSERVED = (SOURCE_USER, SOURCE_AGENT)

    def since(self, moment: datetime, limit: int = 1000,
              sources: Optional[Sequence[str]] = None,
              users_only: bool = False) -> List[State]:
        """States touched since a moment, newest first.

        By `updated_at` rather than `starts_at`, because the question the
        caller is asking is "what is new to me since I last looked", and
        a state that was edited is new to them again. The boundary is
        included: a caller using the oldest state it kept as its mark
        would otherwise lose that state the moment it recorded it.
        """
        rows = self.db.query(
            "SELECT * FROM states WHERE updated_at >= ?" + self._only(sources)
            + (f" AND about != '{ABOUT_AGENT}'" if users_only else "")
            + " ORDER BY updated_at DESC LIMIT ?",
            (moment.isoformat(), *tuple(sources or ()), int(limit)))
        return [State.from_row(row) for row in rows]

    def after(self, moment: datetime, limit: int = 1000,
              sources: Optional[Sequence[str]] = None,
              inclusive: bool = False) -> List[State]:
        """States touched at or after a moment, OLDEST first.

        For a reader that works through what is new in order and stops at
        a limit: what is left over is the newer part, and it is read next
        time rather than skipped. `since` is newest first, which suits a
        reader that wants the latest and would here drop the earliest.

        `inclusive` for a reader that stopped at a limit: two states written
        in the same tick of the clock, one either side of where it stopped,
        would otherwise lose the second. A reader that read everything up
        to a moment has handled that moment, and does not want it again.
        """
        rows = self.db.query(
            f"SELECT * FROM states WHERE updated_at {'>=' if inclusive else '>'} ?"
            + self._only(sources)
            + " ORDER BY updated_at ASC LIMIT ?",
            (moment.isoformat(), *tuple(sources or ()), int(limit)))
        return [State.from_row(row) for row in rows]

    def by_ids(self, state_ids: Sequence[str]) -> List[State]:
        """These states, in no particular order; any that are gone are left out."""
        wanted = [str(one) for one in state_ids if one]
        out: List[State] = []
        for start in range(0, len(wanted), 500):
            chunk = wanted[start:start + 500]
            holes = ", ".join("?" for _ in chunk)
            rows = self.db.query(
                f"SELECT * FROM states WHERE id IN ({holes})", tuple(chunk))
            out.extend(State.from_row(row) for row in rows)
        return out

    def timeline(self, sources: Optional[Sequence[str]] = None) -> List[str]:
        """When every state was written, as ISO strings, oldest first.

        EVERY state, with no limit and no window: this is what the
        overview is drawn from, and the whole point of that picture is to
        show which stretches of the calendar a grouping covered and which
        it never read. Capping it here would hide exactly the thing it
        exists to show.

        Two columns and not one, because a state written now about
        yesterday belongs on the picture where it happened: `starts_at`
        when there is one, and the moment it was written when there is not.
        """
        rows = self.db.query(
            "SELECT starts_at, created_at FROM states"
            + self._only(sources, first=True)
            + " ORDER BY COALESCE(starts_at, created_at)",
            tuple(sources or ()))
        return [str(row["starts_at"] or row["created_at"] or "")
                for row in rows
                if (row["starts_at"] or row["created_at"])]

    def recent(self, limit: int = 50,
               sources: Optional[Sequence[str]] = None,
               users_only: bool = False) -> List[State]:
        """The newest states. `users_only`: not the agent's own work runs -
        what is grouped into the user's tasks."""
        where = self._only(sources, first=True)
        if users_only:
            where += (" AND" if where else " WHERE") + f" about != '{ABOUT_AGENT}'"
        rows = self.db.query(
            "SELECT * FROM states" + where
            + " ORDER BY updated_at DESC LIMIT ?",
            (*tuple(sources or ()), limit))
        return [State.from_row(row) for row in rows]

    @staticmethod
    def _only(sources: Optional[Sequence[str]], first: bool = False) -> str:
        """The clause that keeps a query to certain sources, or nothing.

        Written into the SQL rather than filtered afterwards because these
        queries have a LIMIT on them: taking the newest thousand and then
        dropping a third of them leaves seven hundred, and the caller
        asked for a thousand.
        """
        if not sources:
            return ""
        # Placeholders, not the names themselves. These only ever come
        # from constants in this file, so nothing can reach it - but a
        # query built by pasting values in is a habit that outlives the
        # place it was safe in, and the next caller will pass a setting.
        holes = ", ".join("?" for _ in sources)
        return f" {'WHERE' if first else 'AND'} source IN ({holes})"

    #: Where the states written before any of this existed happened.
    #:
    #: A guess, and the only honest kind: the zone cannot be read off a
    #: record written a month ago, and the machine's zone today is not
    #: evidence about where somebody was in July. It is the user's own
    #: answer, and it is recorded ON each state as an assumption rather
    #: than as an observation, so that a Tuesday derived from it can be
    #: told from a Tuesday that was actually seen. Not named, this
    #: computer's own zone - the best guess there is with no answer.
    ZONE_BEFORE = ""

    def stamp_local_times(self, zone_name: str = "") -> int:
        """Give every state a local time, a weekday and a zone.

        Every state written from now on carries these, stamped as it goes
        in. The ones already on the calendar were written before that
        existed, and a calendar where two thirds of the entries can be
        placed in a week and the rest cannot is worse than either - every
        question about the user's Mondays would silently be a question
        about the last fortnight.

        So they are filled in, once, from a zone the user names. The
        daylight-saving offset is taken at the moment each state RAN and
        not at the moment of this pass, which is the whole reason a named
        zone is used rather than a fixed offset: half these states are
        from one side of a clock change.

        Marked `assumed`, because that is what it is. A state stamped as
        it was written knows where the machine was; these know only what
        they were told afterwards, and a reader deciding whether to trust
        a weekday should be able to see the difference.
        """
        import json

        named = zone_name or self.ZONE_BEFORE
        zone = zone_named(named) if named else None
        if named and zone is None:
            log.info("no time zone table for %s; the older states keep no "
                     "local time", named)
            return 0
        done = 0
        for row in self.db.query("SELECT id, starts_at, ends_at, metadata "
                                 "FROM states"):
            row = dict(row)
            try:
                held = json.loads(row.get("metadata") or "{}")
            except (TypeError, ValueError):
                held = {}
            if not isinstance(held, dict) or held.get("local"):
                continue
            said = local_span(_moment(row.get("starts_at")),
                              _moment(row.get("ends_at")), zone=zone)
            said["assumed"] = True
            said["assumed_why"] = (
                f"written before states carried a time zone; read as "
                f"{named or 'this computer' + chr(39) + 's time zone'}")
            held["local"] = said
            self.db.execute("UPDATE states SET metadata = ? WHERE id = ?",
                            (json.dumps(held, ensure_ascii=False), row["id"]))
            done += 1
        if done:
            log.info("gave %s older state(s) a local time", done)
        return done

    #: Words a state's file used to be filed under that named its horizon.
    HORIZON_WORDS = ("short", "long")

    def drop_horizons(self) -> int:
        """Take the horizon out of the files already written.

        A state's file was filed under "short" or "long" and its first line
        began "(short term, the user)". Both are the span again in a word,
        and the span is on the same page - so they were a second place for
        the two to disagree, and the word is what a search would match on.

        Removing it from the code leaves it in everything written before,
        which is worse than either: the folder then holds two kinds of
        state file and only the old ones can be found by a word the agent
        no longer uses. Rewritten from the state itself rather than edited
        in place, so the file comes out as it would if it were written
        today.

        Returns how many were rewritten. Nothing happens to the states in
        the database - the horizon was never stored there, only computed.

        One pass over the states folder, not one lookup per state. Finding
        each state's file by id searches the whole library, which is every
        folder and every file in it - so on a real library this was
        thirteen hundred scans of twelve thousand files, and the
        application did not finish starting.
        """
        if self.library is None:
            return 0
        from ...data_manage.library import STATES

        try:
            records = self.library.records(STATES)
        except Exception:            # pragma: no cover - a mirror, not the record
            log.debug("could not read the states folder", exc_info=True)
            return 0
        stale = [record for record in records
                 if any(word in (record.keywords or [])
                        for word in self.HORIZON_WORDS)
                 or " term," in (record.summary or "")]
        if not stale:
            return 0
        # Said before it happens, not after. It is a few seconds of file
        # writing on the startup path, and a few seconds of silence during
        # startup is indistinguishable from the thing having hung.
        log.info("taking the horizon out of %d state files", len(stale))
        fixed = 0
        for record in stale:
            state = self.get(record.id)
            if state is None:
                continue
            self._mirror(state)
            fixed += 1
        log.info("rewrote %d state files", fixed)
        return fixed

    def when_they_ran(self, sources: Optional[Sequence[str]] = None
                      ) -> List[Dict[str, Any]]:
        """Each state's id, status and span - and nothing else.

        FOUR COLUMNS, BECAUSE FOUR ARE WHAT IS WANTED. The picture of the
        tasks needs to know how long a task's states cover and how many of
        them are finished; it does not need their titles, details,
        evidence, sources or metadata. Reading the whole row to get four
        fields of it was measured at 461ms and 2.2MB over 786 states,
        against 36ms for this - and it happens on every poll of a panel
        somebody leaves open.
        """
        rows = self.db.query(
            "SELECT id, status, starts_at, ends_at FROM states"
            + self._only(sources, first=True),
            tuple(sources or ()))
        return [{"id": row["id"], "status": row["status"],
                 "starts_at": row["starts_at"], "ends_at": row["ends_at"]}
                for row in rows]

    def counts(self, sources: Optional[Sequence[str]] = None) -> Dict[str, int]:
        out = {ACTIVE: 0, DONE: 0, RETIRED: 0}
        for row in self.db.query("SELECT status, COUNT(*) AS c FROM states"
                                 + self._only(sources, first=True)
                                 + " GROUP BY status",
                                 tuple(sources or ())):
            out[row["status"]] = int(row["c"])
        return out


#: The days, in the order `weekday()` gives them.
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday")


def _moment(value: Any) -> Optional[datetime]:
    """A stored timestamp, read back, or nothing.

    Anything unreadable is nothing rather than an exception: this runs
    over every state the user has, and one row with a mangled date should
    cost that row its weekday and not the whole pass.
    """
    if not value:
        return None
    try:
        when = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def zone_named(name: str) -> Optional[Any]:
    """A named zone, or nothing if this machine has no table for it.

    `zoneinfo` needs the operating system's tz database, and Windows does
    not ship one - it is the `tzdata` package there, which may or may not
    be installed. Returning None rather than raising lets the caller say
    "this could not be worked out" instead of failing a pass over the
    user's whole calendar because of a missing dependency.
    """
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(str(name))
    except Exception:                # pragma: no cover - platform dependent
        log.debug("no time zone table for %s", name, exc_info=True)
        return None


def local_span(starts_at: Optional[datetime] = None,
               ends_at: Optional[datetime] = None,
               now: Optional[datetime] = None,
               zone: Optional[Any] = None) -> Dict[str, Any]:
    """The same span, said where the user is standing.

    Everything here is stored in UTC, which is right - one clock, no
    ambiguity, and arithmetic that works across a move - and it is not
    what anybody's week looks like. A state running from 23:00 to 01:00
    UTC is a Tuesday evening in Lisbon and a Wednesday morning in
    Shanghai, and every question a person asks about their own calendar -
    what do my Mondays look like, do I work late, was that the weekend -
    is a question about the second one. The conversion cannot be done
    afterwards either: the user may be in a different zone by then, and
    the honest answer is the zone they were in when it happened.

    So each state carries the offset that was in force, the name the
    system gave the zone, both ends of its span in local time, and which
    day of the week that was. Kept as written text rather than recomputed
    on the way out, because that is the fact: what the clock on their wall
    said. A stored offset is also the only thing that survives a move -
    recomputing later would silently restate last month in this month's
    zone.
    """
    when = now or datetime.now(timezone.utc)
    # The offset in force WHEN THE STATE RAN, not when it was stamped.
    # Those differ across a daylight-saving change, so a January state
    # stamped in September came out labelled WEST with a +01:00 offset
    # while its own local times read +00:00 - the record disagreeing with
    # itself in the same dictionary. The zone is taken at `starts_at`
    # where there is one, and only otherwise at the moment of writing.
    stood = starts_at or when
    if stood.tzinfo is None:
        stood = stood.replace(tzinfo=timezone.utc)
    # The machine's zone, unless a particular one was asked for. A named
    # zone is what the backfill uses: the states written before any of
    # this existed happened somewhere, and that somewhere is a fact the
    # user has to supply - it cannot be read off a file written a month
    # ago on a machine that may since have moved.
    here = stood.astimezone(zone) if zone is not None else stood.astimezone()
    stamped = (when.astimezone(zone) if zone is not None
               else when.astimezone())
    shift = here.utcoffset() or timedelta(0)
    total = int(shift.total_seconds())
    sign = "-" if total < 0 else "+"
    hours, rest = divmod(abs(total), 3600)
    out: Dict[str, Any] = {
        # The name the system gives it. Not an IANA key: there is no way
        # to get one from the standard library on every platform, and a
        # guessed "Asia/Shanghai" that is wrong is worse than "CST".
        "zone": here.tzname() or "",
        "offset": f"{sign}{hours:02d}:{rest // 60:02d}",
        "offset_minutes": total // 60,
        "recorded_at": stamped.isoformat(timespec="seconds"),
    }
    for name, moment in (("starts_at", starts_at), ("ends_at", ends_at)):
        if moment is None:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        there = moment.astimezone(here.tzinfo)
        out[name] = there.isoformat(timespec="seconds")
        out[name + "_day"] = WEEKDAYS[there.weekday()]
    # The day of the week the state is filed under, which is the day it
    # STARTED. A state that runs past midnight belongs to the evening it
    # began in - that is the sitting a person remembers.
    if "starts_at_day" in out:
        out["weekday"] = out["starts_at_day"]
    else:
        out["weekday"] = WEEKDAYS[here.weekday()]
    return out


def window_for(moment: Optional[datetime] = None,
               starts_at: Optional[str] = None,
               ends_at: Optional[str] = None) -> tuple:
    """When a state actually runs.

    The period is the agent's to state, because only it knows what the state
    is about. Something that began on Tuesday and ends on Thursday cannot be
    expressed by choosing between two fixed shapes, and choosing one anyway
    produces a state whose span is a lie: a fixed "long" window of three days
    back and four days forward makes every long state a week long and every
    one of them still running four days from now, whatever it describes.

    There is no horizon to pass any more. It named a choice between those
    two shapes, and the shapes are gone: what is left is a fallback for a
    state that gives no dates at all, and the only honest reading of one of
    those is that it is about now. Something longer-lived is a state already
    on the calendar, carried on one window at a time.
    """
    moment = moment or datetime.now(timezone.utc)
    given_start = _parse_moment(starts_at)
    given_end = _parse_moment(ends_at)
    if given_start and given_end and given_end > given_start:
        return given_start, given_end
    if given_start and not given_end:
        # A beginning with no end: the shortest honest reading is that it is
        # still going, so it runs a little past now and is seen again - or
        # it is not, and it ends where it stopped being true.
        return given_start, max(moment, given_start) + timedelta(hours=2)
    if given_end and not given_start:
        return given_end - timedelta(hours=2), given_end
    return moment - timedelta(hours=1), moment + timedelta(hours=1)


def _parse_moment(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    parsed = parse_iso(str(value))
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


__all__ = ["State", "StateStore", "window_for", "SOURCE_USER", "SOURCE_AGENT",
           "SOURCE_TASK", "SOURCES", "ABOUT_USER", "ABOUT_AGENT", "ROUTINE", "WORK",
           "LEISURE", "KINDS",
           "ACTIVE", "DONE", "RETIRED"]
