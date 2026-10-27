"""WHAT A TRY OF THE WORK DEBUGGER IS GIVEN OF THE USER: AS IT WAS WHEN THE
TRY BEGAN (the user, 2026-10-25).

A try runs for hours, and meanwhile the user's life is recorded and learned
from as always - and it is about the try: the user reading a blind review
of its ways, saying which is better and why, was recorded within the hour,
and learned from at the next learning. Searched or retrieved later by a way
still going, that is the answer handed to the one being judged. So every
way of a try is given the user's records and what was learned of their work
FROM A COPY MADE WHEN THE TRY BEGAN - its states, projects and stages, its
cases, judgments, reasons, references and word list - and nothing written
since. Real work is given them as they are now.

ONE COPY A TRY, made by the first of its ways that needs it, with the
try's start as its cutoff - the first start of any of its ways: one going
again after the agent restarted is given the same, and a try begun before
there were copies is given one cut at its start (a row written after it -
a state that ended after it, a case or a reason changed since - is not in
it). Forgotten when the try is - removed, or all its ways cleared - so a
try begun again is cut again.

WHAT THE USER TOOK AWAY SINCE STAYS AWAY: each time it is opened, what is
no longer kept at all is taken out of the copy too, and a unit is held
back as it is now (turned off, waiting for them to look). Nothing is ever
added to it.

It lies with the agent's database - where no way may read.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

log = logging.getLogger(__name__)

#: Where the copies are: beside the agent's database.
FOLDER = "debug_as_of"

#: WHAT IS COPIED - what a way's reviewer is given of what was learned
#: (learning/find.py) and what its work agent searches of the records
#: (context_search.records) - each with the columns that say when its words
#: were written: a row with any of them after the cutoff is not copied.
TABLES: Dict[str, Sequence[str]] = {
    "states": ("starts_at", "ends_at", "created_at"),
    "state_tasks": (),                  # its words, below (`_LATE_TASK`)
    "experience_stages": ("created_at", "updated_at"),
    "learn_units": (),
    "learn_folders": (),
    "learn_cases": ("created_at", "updated_at"),
    "learn_judgments": ("created_at",),
    "learn_reasons": ("created_at", "updated_at"),
    "learn_links": ("at",),
    "learn_overrides": ("at",),
    "learn_items": ("added_at", "updated_at"),
    "learn_terms": ("created_at",),
}
#: Of these, only the user's own: not the stages of the agent's runs.
WHERE = {"experience_stages": "scope = 'live'"}
#: Taken out when no longer kept at all, by their id.
BY_ID = ("states", "state_tasks", "experience_stages", "learn_units", "learn_cases",
         "learn_judgments", "learn_reasons", "learn_items", "learn_terms")
#: A PROJECT REWRITTEN AFTER THE CUTOFF keeps only the folders it is worked
#: in - what tells the folders left out of a way (leave_out.project_folders)
#: - and none of its words.
_LATE_TASK = ("updated_at", "summary_at", "rebuilt_at")

_lock = threading.Lock()


def when(value: Any) -> Optional[datetime]:
    """A time as the database keeps it - with its offset, in UTC, or a day
    alone - in UTC; None for none."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Written without an offset: the computer's own time.
    return moment.astimezone(timezone.utc)


def path_for(db: Any, ref: str) -> Optional[Path]:
    """Where a try's copy is - None for a database not in a file."""
    where = getattr(db, "path", None)
    if not where:
        return None
    return Path(where).parent / FOLDER / f"{_safe(ref)}.db"


def _safe(ref: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.~-]", "_", str(ref or "")) or "_"


def cutoff_of(db: Any) -> Optional[datetime]:
    """The cutoff a copy was made at."""
    try:
        row = db.query_one("SELECT cutoff FROM as_of LIMIT 1")
    except Exception:
        return None
    return when(row["cutoff"]) if row else None


def for_try(live: Any, ref: str, started: Iterable[Any]) -> Optional[Any]:
    """THE COPY A TRY'S WAYS ARE GIVEN, made now when there is none - cut
    at the earliest of `started` (its ways' starts) - and brought in line
    with what the user took away since. None when it cannot be had: then
    nothing of the user is given, never what is kept now."""
    from ...data_manage.storage.database import Database
    path = path_for(live, ref)
    if path is None:
        return None
    try:
        with _lock:
            if not path.exists():
                moments = [one for one in (when(s) for s in started) if one is not None]
                cutoff = min(moments) if moments else datetime.now(timezone.utc)
                make(live, path, cutoff)
            copy = Database(path)
            prune(copy, live)
        return copy
    except Exception:
        log.warning("the debugger's copy of what the user's work was could not be had",
                    exc_info=True)
        return None


def forget(live: Any, refs: Iterable[str]) -> None:
    """A try's copy gone - and its journal."""
    for ref in refs:
        path = path_for(live, ref)
        if path is None:
            continue
        for one in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
            try:
                one.unlink()
            except FileNotFoundError:
                pass
            except OSError:          # pragma: no cover - open: a later forget takes it
                log.warning("could not remove %s", one, exc_info=True)


def _uri(path: Any) -> str:
    return Path(path).resolve().as_uri() + "?mode=ro"


def _open(path: Any) -> sqlite3.Connection:
    """A connection that may attach the live database read-only - by its URI."""
    return sqlite3.connect(Path(path).resolve().as_uri(), uri=True, timeout=30.0)


def make(live: Any, path: Path, cutoff: datetime) -> None:
    """The copy, cut at `cutoff`: the application's tables, empty; the
    tables above filled from `live` without what was written after it."""
    from ...data_manage.storage.database import Database
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.stem}.{os.getpid()}.{threading.get_ident()}.part")
    _remove(temp)
    made = Database(temp)
    made.close()
    conn = _open(temp)
    try:
        conn.execute("ATTACH DATABASE ? AS live", (_uri(live.path),))
        for table, stamps in TABLES.items():
            names = _shared_columns(conn, table)
            if not names:
                continue
            columns = ", ".join(f'"{one}"' for one in names)
            where = f" WHERE {WHERE[table]}" if table in WHERE else ""
            conn.execute(f'INSERT INTO main."{table}" ({columns}) '
                         f'SELECT {columns} FROM live."{table}"{where}')
            stamps = [one for one in stamps if one in names]
            if stamps:
                late = [(row[0],) for row in conn.execute(
                    f'SELECT rowid, {", ".join(stamps)} FROM main."{table}"')
                    if _after(row[1:], cutoff)]
                conn.executemany(f'DELETE FROM main."{table}" WHERE rowid = ?', late)
        _late_tasks(conn, cutoff)
        _orphans(conn)
        conn.execute("CREATE TABLE as_of (cutoff TEXT NOT NULL, made_at TEXT NOT NULL)")
        conn.execute("INSERT INTO as_of (cutoff, made_at) VALUES (?, ?)",
                     (cutoff.astimezone(timezone.utc).isoformat(),
                      datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.execute("DETACH DATABASE live")
    finally:
        conn.close()
    try:
        os.replace(temp, path)
    except OSError:
        # Another way of the try made it first, and has it open.
        _remove(temp)
        if not path.exists():
            raise


def prune(copy: Any, live: Any) -> None:
    """What the user took away since out of the copy too; each unit held
    back as it is now. Nothing added."""
    conn = _open(copy.path)
    try:
        conn.execute("ATTACH DATABASE ? AS live", (_uri(live.path),))
        for table in BY_ID:
            if _shared_columns(conn, table, ("id",)):
                conn.execute(f'DELETE FROM main."{table}" WHERE id NOT IN '
                             f'(SELECT id FROM live."{table}")')
        if _shared_columns(conn, "learn_units", ("state",)):
            conn.execute("UPDATE main.learn_units SET state = (SELECT state FROM "
                         "live.learn_units AS now WHERE now.id = main.learn_units.id)")
        _orphans(conn)
        conn.commit()
        conn.execute("DETACH DATABASE live")
    finally:
        conn.close()


def _shared_columns(conn: sqlite3.Connection, table: str,
                    needed: Sequence[str] = ()) -> List[str]:
    """The columns a table has both here and in the live database - none
    when it lacks one of `needed` in either."""
    mine = [row[1] for row in conn.execute(f'PRAGMA main.table_info("{table}")')]
    theirs = {row[1] for row in conn.execute(f'PRAGMA live.table_info("{table}")')}
    names = [one for one in mine if one in theirs]
    return [] if any(one not in names for one in needed) else names


def _after(values: Sequence[Any], cutoff: datetime) -> bool:
    return any((moment := when(value)) is not None and moment > cutoff for value in values)


def _late_tasks(conn: sqlite3.Connection, cutoff: datetime) -> None:
    names = [row[1] for row in conn.execute('PRAGMA main.table_info("state_tasks")')]
    stamps = [one for one in _LATE_TASK if one in names]
    if not stamps or "described" not in names:
        return
    late = []
    for row in conn.execute(f'SELECT rowid, described, {", ".join(stamps)} FROM main.state_tasks'):
        if not _after(row[2:], cutoff):
            continue
        try:
            said = json.loads(row[1] or "{}")
        except (TypeError, ValueError):
            said = {}
        folders = said.get("directories") if isinstance(said, dict) else None
        late.append((json.dumps({"directories": folders or []}), row[0]))
    conn.executemany("UPDATE main.state_tasks SET described = ? WHERE rowid = ?", late)


def _orphans(conn: sqlite3.Connection) -> None:
    """What rests on what is not in the copy: not in it either."""
    conn.execute("DELETE FROM main.learn_judgments WHERE case_id NOT IN "
                 "(SELECT id FROM main.learn_cases)")
    conn.execute("DELETE FROM main.learn_links WHERE judgment_id NOT IN "
                 "(SELECT id FROM main.learn_judgments) OR reason_id NOT IN "
                 "(SELECT id FROM main.learn_reasons)")
    conn.execute("DELETE FROM main.learn_overrides WHERE (COALESCE(judgment_id, '') != '' "
                 "AND judgment_id NOT IN (SELECT id FROM main.learn_judgments)) "
                 "OR winner NOT IN (SELECT id FROM main.learn_reasons) "
                 "OR loser NOT IN (SELECT id FROM main.learn_reasons)")


def _remove(path: Path) -> None:
    for one in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            one.unlink()
        except FileNotFoundError:
            pass
