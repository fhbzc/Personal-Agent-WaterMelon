"""Raw Event and Raw Edit storage (section 45).

Writes here are deliberately cheap: the Listener and the Agent Core must be
able to record without semantic processing. Nothing turns these rows into
records of their own later. They are read as they are - by the agent
heartbeat (its last few hours), the Overview (the last day, and the past
week's chart) and page visit history - and a state copies what it relies on
when it is written, so they are kept for a while and then let go by age.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from ...common.events.models import (ListenerEvent, RawEdit, iso,
                                     json_dump, utcnow)
from ...common.file_noise import churns_regardless, sql_prefilter
from .database import Database

log = logging.getLogger(__name__)


#: Windows that are in front while nobody is using the computer: Windows'
#: lock screen, the sign-in screen, and a screensaver. Time in them is time
#: away.
_AWAY = ("lockapp", "logonui", ".scr")


def _away(application: str) -> bool:
    name = str(application or "").lower()
    return any(part in name for part in _AWAY)


class RawStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -- raw events ---------------------------------------------------------
    def add_event(self, event: ListenerEvent) -> ListenerEvent:
        self.db.execute(
            """
            INSERT OR REPLACE INTO raw_events
                (id, event_type, source, timestamp, application, data, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.event_type,
                event.source,
                iso(event.timestamp),
                event.application,
                json_dump(event.data),
                json_dump(event.metadata),
            ),
        )
        return event

    def add_events(self, events: List[ListenerEvent]) -> int:
        rows = [
            (
                e.id, e.event_type, e.source, iso(e.timestamp), e.application,
                json_dump(e.data), json_dump(e.metadata),
            )
            for e in events
        ]
        if rows:
            self.db.executemany(
                """
                INSERT OR REPLACE INTO raw_events
                    (id, event_type, source, timestamp, application, data, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def recent_events(
        self,
        limit: int = 100,
        application: Optional[str] = None,
        event_type: Optional[str] = None,
        since: Optional[datetime] = None,
        not_types: Optional[Iterable[str]] = None,
        not_under: Optional[Iterable[str]] = None,
        until: Optional[datetime] = None,
    ) -> List[ListenerEvent]:
        """The most recent events, narrowed before they are built.

        ``not_types`` and ``not_under`` drop rows in SQL rather than in the
        caller. That is not the same optimisation as filtering afterwards: a
        reader asking for a window of time gets every row in it built into an
        object first, and on one machine 17,301 of the 21,182 events in a day
        were the agent writing its own memory - every one of them read, parsed
        and thrown away. Excluded in the query, the same window costs 120 ms
        instead of 480.

        ``not_under`` applies to filesystem events, which are the ones that
        carry a path and the ones there are most of.
        """
        sql = "SELECT * FROM raw_events WHERE 1=1"
        params: List[Any] = []
        for unwanted in (not_types or ()):
            sql += " AND event_type <> ?"
            params.append(unwanted)
        for folder in (not_under or ()):
            sql += (" AND NOT (event_type >= 'filesystem.'"
                    " AND event_type < 'filesystem.\uffff'"
                    " AND json_extract(data, '$.path') LIKE ?)")
            params.append(f"{folder}%")
        if application:
            sql += " AND application = ?"
            params.append(application)
        if event_type:
            # A range rather than LIKE, so the (event_type, timestamp) index
            # can serve it. LIKE is case-insensitive by default, which makes
            # it unusable as a prefix scan, so "the newest N browser events"
            # walked the whole table newest-first looking for the 851 of them
            # among 50,000: 70 ms, on every click that opens a page.
            sql += " AND event_type >= ? AND event_type < ?"
            params.extend([event_type, event_type + "￿"])
        if since:
            sql += " AND timestamp >= ?"
            params.append(iso(since))
        if until:
            sql += " AND timestamp < ?"
            params.append(iso(until))
        # The tiebreak is not decoration. Events written in one burst share
        # a timestamp, and callers that spend a budget "on the most recent
        # first" were being handed an arbitrary order within each second -
        # so which readings of a window survived was up to the query plan.
        # rowid is insertion order, which is the order they were observed.
        sql += " ORDER BY timestamp DESC, rowid DESC LIMIT ?"
        params.append(limit)
        return [ListenerEvent.from_row(r) for r in self.db.query(sql, params)]

    #: How many rows are examined in one pass.
    #:
    #: The database narrows by LIKE first, so this is a ceiling on what the
    #: exact rules are asked about, not on the table. A machine with a year
    #: of history is cleared over several rounds rather than in one long one.
    NOISE_AT_MOST = 20000

    def prune_noise(self, patterns=(), names=(), folders=(),
                    dry_run: bool = False) -> Dict[str, int]:
        """Drop file events for files that change wherever they are.

        The rules that keep a registry hive log out of the record only ever
        applied to what arrived after they existed. Measured on one machine
        the day they went in: of 24,610 file events in the past day, **10,653
        were ntuser.dat.LOG2** - a Windows registry journal, written ten
        thousand times in ten minutes, none of it anybody's work, all of it
        already stored, and all of it waiting to be read by the data
        heartbeat and turned into sentences about the user's day.

        Different from prune_older_than, which lets go of what is past the
        keeping window and is a question of storage. This drops rows that
        were never anybody's activity, whatever their age.

        Deciding properly means fnmatch on a basename, which SQLite will not
        do, so the query narrows with LIKE - wider than the rule, never
        narrower - and each candidate is then asked the real question.
        """
        condition, params = sql_prefilter(patterns, names, folders)
        if not condition:
            return {"examined": 0, "removed": 0}
        rows = self.db.query(
            "SELECT id, data FROM raw_events"
            " WHERE event_type >= 'filesystem.' AND event_type < 'filesystem.\uffff'"
            f" AND {condition} LIMIT ?", [*params, self.NOISE_AT_MOST])
        doomed = []
        for row in rows:
            data = json.loads(row.get("data") or "{}")
            where = (data.get("path") or data.get("destination_path")
                     or data.get("source_path"))
            if churns_regardless(where, patterns=patterns, names=names,
                                 folders=folders):
                doomed.append(row["id"])
        if doomed and not dry_run:
            for start in range(0, len(doomed), 500):
                batch = doomed[start:start + 500]
                marks = ",".join("?" for _ in batch)
                self.db.execute(
                    f"DELETE FROM raw_events WHERE id IN ({marks})", batch)
        return {"examined": len(rows), "removed": len(doomed)}

    def count_events(self) -> int:
        """How many raw events are kept."""
        row = self.db.query_one("SELECT COUNT(*) AS c FROM raw_events")
        return int(row["c"]) if row else 0

    def prune_older_than(self, keep_days: int) -> Dict[str, int]:
        """Drop raw activity older than the keeping window.

        By age alone. What reads raw activity - the agent heartbeat's last
        few hours, the Overview's day and week, page visit history - never
        reaches back further than a week, and a state copies what it relies
        on when it is written. So a row older than the window is read by
        nothing, and kept it is only a table that grows.
        """
        if keep_days <= 0:
            return {"events": 0, "edits": 0}
        cutoff = (utcnow() - timedelta(days=int(keep_days))).isoformat()
        removed = {}
        for table in ("raw_events", "raw_edits"):
            with self.db.write() as conn:
                before = conn.execute(
                    f"SELECT COUNT(*) AS c FROM {table} WHERE timestamp < ?",
                    (cutoff,)).fetchone()
                count = int(before["c"]) if before else 0
                if count:
                    conn.execute(f"DELETE FROM {table} WHERE timestamp < ?", (cutoff,))
            removed[table.replace("raw_", "")] = count
        if any(removed.values()):
            log.info("pruned raw activity older than %d days: %s", keep_days, removed)
        return removed

    # -- raw edits ----------------------------------------------------------
    def add_edit(self, edit: RawEdit) -> RawEdit:
        self.db.execute(
            """
            INSERT OR REPLACE INTO raw_edits
                (id, event_type, source, timestamp, task_id, instruction, accessed_information,
                 action, result, related_nodes, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edit.id,
                edit.event_type,
                edit.source,
                iso(edit.timestamp),
                edit.task_id,
                edit.instruction,
                json_dump(edit.accessed_information),
                edit.action,
                edit.result,
                json_dump(edit.related_nodes),
                json_dump(edit.metadata),
            ),
        )
        return edit

    def events_by_ids(self, ids: Iterable[str]) -> List[ListenerEvent]:
        """These events, exactly; any that are gone are left out."""
        wanted = [str(one) for one in ids if one][:200]
        if not wanted:
            return []
        holes = ", ".join("?" for _ in wanted)
        rows = self.db.query(
            f"SELECT * FROM raw_events WHERE id IN ({holes})", tuple(wanted))
        return [ListenerEvent.from_row(r) for r in rows]

    def edits_by_ids(self, ids: Iterable[str]) -> List[RawEdit]:
        """These edits, exactly; any that are gone are left out."""
        wanted = [str(one) for one in ids if one][:200]
        if not wanted:
            return []
        holes = ", ".join("?" for _ in wanted)
        rows = self.db.query(
            f"SELECT * FROM raw_edits WHERE id IN ({holes})", tuple(wanted))
        return [RawEdit.from_row(r) for r in rows]

    def recent_edits(self, limit: int = 100, task_id: Optional[str] = None) -> List[RawEdit]:
        if task_id:
            rows = self.db.query(
                "SELECT * FROM raw_edits WHERE task_id = ? ORDER BY timestamp DESC LIMIT ?",
                (task_id, limit),
            )
        else:
            rows = self.db.query(
                "SELECT * FROM raw_edits ORDER BY timestamp DESC LIMIT ?", (limit,)
            )
        return [RawEdit.from_row(r) for r in rows]

    # -- statistics ---------------------------------------------------------
    def application_usage(self, since: Optional[datetime] = None,
                          until: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Foreground usage aggregation used by the Activity Overview (section 9).

        ``until`` bounds the window at the top. Without it every window that
        starts earlier also contains everything that came after, so a series of
        per-day windows all report the same running total.

        EACH STRETCH COUNTS ONLY THE PART OF IT INSIDE THE WINDOW. A focus
        period was added whole to the window it ENDED in, so four hours with
        the screen locked, ending at 15:10, all landed in the 15:00 hour -
        882% of it. And the period open at the end of a window was taken to
        be open even when it had already ended, which counted it twice: the
        hours that added up to 116% and 163%. Asked as a share of the hour,
        neither could hide; asked in minutes scaled to the busiest hour,
        both had looked like a busy afternoon.

        THE LOCK SCREEN IS NOT AN APPLICATION BEING USED. While the computer
        is locked, the window in front is Windows' own lock screen, and its
        hours - nearly ten in one day, on one machine - were the top line of
        the chart. They are time away, and are left out.
        """
        since = since or (datetime.now(timezone.utc) - timedelta(days=1))
        return self.application_usage_in([(since, until)])[0]

    def application_usage_in(
            self, windows: List[tuple]) -> List[List[Dict[str, Any]]]:
        """``application_usage`` for each ``(since, until)`` window, in order.

        The same answer as asking window by window, from ONE read of the
        focus periods. The hourly chart asked 25 times and the week asked 7,
        and each ask re-read and re-parsed up to two days of periods past its
        own end - the same rows, over and over, on every refresh.
        """
        now = datetime.now(timezone.utc)

        def moment(value: Any) -> Optional[datetime]:
            try:
                when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                return None
            return when if when.tzinfo else when.replace(tzinfo=timezone.utc)

        # Each window as the single ask had it: its start, the top it counts
        # to, and the bounds its rows were read between.
        asks = []
        for since, until in windows:
            horizon = min(until, now) if until is not None else now
            if horizon <= since:
                asks.append(None)
                continue
            asks.append((since, horizon, iso(since),
                         iso(horizon + timedelta(days=2)), {}))
        live = [ask for ask in asks if ask is not None]
        if not live:
            return [[] for _ in windows]

        def count(totals: Dict[str, Dict[str, Any]], since: datetime,
                  horizon: datetime, app: str, began: datetime,
                  ended: datetime, seen: str) -> None:
            if _away(app):
                return
            inside = (min(ended, horizon) - max(began, since)).total_seconds()
            if inside <= 0:
                return
            entry = totals.setdefault(
                app, {"application": app, "seconds": 0.0, "sessions": 0, "last_seen": None}
            )
            entry["seconds"] += inside
            entry["sessions"] += 1
            entry["last_seen"] = seen

        # Every period that ENDED after a window opened - and so may have
        # been under way inside it - up to a margin past the window's end,
        # because one that ended later can still have begun inside it.
        # Only the period's length is wanted from its data, so it is read out
        # by the database rather than every record parsed here.
        rows = self.db.query(
            """
            SELECT application, timestamp,
                   CASE WHEN json_valid(data)
                        THEN json_extract(data, '$.duration_seconds') END AS duration
            FROM raw_events
            WHERE event_type = 'application.focus_ended' AND application IS NOT NULL
              AND timestamp >= ? AND timestamp < ?
            ORDER BY timestamp
            """,
            (min(ask[2] for ask in live), max(ask[3] for ask in live)),
        )
        for row in rows:
            stamp = row["timestamp"]
            mine = [ask for ask in live if ask[2] <= stamp < ask[3]]
            if not mine:
                continue
            ended = moment(stamp)
            if ended is None:
                continue
            duration = float(row["duration"] or 0.0)
            began = ended - timedelta(seconds=duration)
            app = row["application"]
            if _away(app):
                continue
            for since, horizon, _low, _high, totals in mine:
                # Outside this window altogether - nothing of it to count.
                if ended <= since or began >= horizon:
                    continue
                count(totals, since, horizon, app, began, ended, stamp)

        # A focus period is normally written when the user moves to another
        # application.  Waiting for that transition makes a newly started
        # listener report zero usage while the user is still working in its
        # first application - so the one period open NOW is added, cut to
        # this window. Only if it is open now: the last focus before some
        # past hour's end is not open just because that hour has ended.
        latest = self.db.query_one(
            """
            SELECT application, event_type, timestamp FROM raw_events
            WHERE event_type IN ('application.focused', 'application.focus_ended')
              AND application IS NOT NULL
            ORDER BY timestamp DESC LIMIT 1
            """,
        )
        if latest is not None and latest["event_type"] == "application.focused":
            began = moment(latest["timestamp"])
            if began is not None:
                for since, horizon, _low, _high, totals in live:
                    count(totals, since, horizon, latest["application"], began,
                          now, iso(horizon))
        answers = []
        for ask in asks:
            if ask is None:
                answers.append([])
                continue
            ordered = sorted(ask[4].values(), key=lambda e: e["seconds"], reverse=True)
            for entry in ordered:
                entry["seconds"] = round(entry["seconds"], 1)
            answers.append(ordered)
        return answers
