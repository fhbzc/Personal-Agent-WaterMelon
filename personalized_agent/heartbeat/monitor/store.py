"""Where windows are kept (section 36.3.3).

From PR #6 (the rhythm of the day, branch junyi), taken on 2026-11-03 as
the Monitor's real-time input (monitor/signals.py).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from .types import Window


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _row_to_window(row: Dict[str, Any]) -> Window:
    return Window(
        start=datetime.fromisoformat(row["local_start"]),
        minutes=int(row["minutes"]), main=row["main"],
        activities=json.loads(row["activities"] or "{}"), task=row["task"] or "",
        session_elapsed_min=row["session_elapsed_min"],
        since_break_min=row["since_break_min"], on_task_min=row["on_task_min"],
        own_work_min=row.get("own_work_min") or 0.0,
        switch_rate=row["switch_rate"], idle_share=row["idle_share"],
        references=json.loads(row["refs"] or "[]"), state_id=row["state_id"],
        project_id=row.get("project_id") or "", project=row.get("project") or "",
        on_project_min=row.get("on_project_min") or 0.0,
        project_today_min=row.get("project_today_min") or 0.0)


#: One row per window of time, kept long after the raw events it was counted
#: from are let go (eight days): the weeks of HABITS the Monitor's signals
#: read (the user, 2026-11-03: "几周来的习惯是很有用的东西") - and only
#: what the window was, never what was on the screen.
SCHEMA = """
CREATE TABLE IF NOT EXISTS activity_windows (
    start_at            TEXT PRIMARY KEY,   -- UTC ISO, the window's start
    minutes             INTEGER NOT NULL,
    local_start         TEXT NOT NULL,      -- the same moment, user's clock
    main                TEXT NOT NULL,
    activities          TEXT NOT NULL DEFAULT '{}',
    task                TEXT NOT NULL DEFAULT '',
    session_elapsed_min REAL NOT NULL DEFAULT 0,
    since_break_min     REAL NOT NULL DEFAULT 0,
    own_work_min        REAL NOT NULL DEFAULT 0,
    on_task_min         REAL NOT NULL DEFAULT 0,
    switch_rate         REAL NOT NULL DEFAULT 0,
    idle_share          REAL NOT NULL DEFAULT 0,
    refs                TEXT NOT NULL DEFAULT '[]',
    state_id            TEXT,
    project_id          TEXT DEFAULT '',
    project             TEXT DEFAULT '',
    on_project_min      REAL DEFAULT 0,
    project_today_min   REAL DEFAULT 0
);
"""


class WindowStore:
    def __init__(self, db: Any) -> None:
        self.db = db
        with self.db.write() as conn:
            conn.executescript(SCHEMA)
            names = [row[1] for row in conn.execute("PRAGMA table_info(activity_windows)")]
            if "own_work_min" not in names:              # a table from before it
                conn.execute("ALTER TABLE activity_windows ADD COLUMN own_work_min "
                             "REAL NOT NULL DEFAULT 0")

    def prune(self, keep_days: int) -> int:
        """Windows older than `keep_days` let go (setting monitor_windows_keep_days)."""
        from datetime import timedelta
        cut = datetime.now(timezone.utc) - timedelta(days=max(1, int(keep_days)))
        cursor = self.db.execute("DELETE FROM activity_windows WHERE start_at < ?", (_utc(cut),))
        return int(cursor.rowcount or 0)

    def add(self, windows: List[Window]) -> int:
        rows = [(_utc(w.start), w.minutes, w.start.isoformat(), w.main,
                 json.dumps(w.activities, ensure_ascii=False), w.task,
                 w.session_elapsed_min, w.since_break_min, w.on_task_min,
                 w.switch_rate, w.idle_share,
                 json.dumps(w.references, ensure_ascii=False), w.state_id,
                 w.project_id, w.project, w.on_project_min, w.project_today_min, w.own_work_min)
                for w in windows]
        if rows:
            self.db.executemany(
                "INSERT OR REPLACE INTO activity_windows (start_at, minutes, "
                "local_start, main, activities, task, session_elapsed_min, "
                "since_break_min, on_task_min, switch_rate, idle_share, refs, "
                "state_id, project_id, project, on_project_min, project_today_min, "
                "own_work_min) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        return len(rows)

    def all(self) -> List[Window]:
        return [_row_to_window(r) for r in self.db.query(
            "SELECT * FROM activity_windows ORDER BY start_at")]

    def between(self, start: datetime, end: datetime) -> List[Window]:
        return [_row_to_window(r) for r in self.db.query(
            "SELECT * FROM activity_windows WHERE start_at >= ? AND start_at < ? "
            "ORDER BY start_at", (_utc(start), _utc(end)))]

    def since(self, start: datetime) -> List[Window]:
        return [_row_to_window(r) for r in self.db.query(
            "SELECT * FROM activity_windows WHERE start_at >= ? ORDER BY start_at",
            (_utc(start),))]

    def last(self, n: int = 1) -> List[Window]:
        rows = self.db.query(
            "SELECT * FROM activity_windows ORDER BY start_at DESC LIMIT ?", (n,))
        return [_row_to_window(r) for r in reversed(rows)]

    def before(self, moment: datetime, n: int = 1) -> List[Window]:
        """The ``n`` windows just before ``moment``, oldest first."""
        rows = self.db.query(
            "SELECT * FROM activity_windows WHERE start_at < ? "
            "ORDER BY start_at DESC LIMIT ?", (_utc(moment), n))
        return [_row_to_window(r) for r in reversed(rows)]

    def count(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM activity_windows")
        return int(row["n"]) if row else 0

    def fingerprint(self) -> tuple:
        """Changes when windows are added, removed or given a different project."""
        row = self.db.query_one(
            "SELECT COUNT(*) AS n, MAX(start_at) AS m, "
            "ROUND(SUM(on_project_min), 1) AS s, "
            "SUM(LENGTH(COALESCE(project_id, ''))) AS l FROM activity_windows")
        return tuple(row.values()) if row else ()

    def delete_between(self, start: datetime, end: datetime) -> int:
        cursor = self.db.execute(
            "DELETE FROM activity_windows WHERE start_at >= ? AND start_at < ?",
            (_utc(start), _utc(end)))
        return int(cursor.rowcount or 0)

