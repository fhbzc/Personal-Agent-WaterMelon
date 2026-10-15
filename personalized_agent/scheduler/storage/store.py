"""Scheduler persistence (sections 59, 60).

This store is the single authoritative representation of scheduled work.  The
scheduler loop and the Calendar UI both read and write here; there is no second
schedule anywhere in the system.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ...common.events.models import ScheduledTask, iso, json_dump, utcnow
from ...data_manage.storage.database import Database

ACTIVE_STATES = ("pending",)

#: A task that ran, may have finished, and is waiting for the data heartbeat
#: to say which (section 59, as amended).
#:
#: It is deliberately not ``pending``: a task in this state must not be run
#: again, because running it twice is exactly the harm that being unsure about
#: the first run threatens. It is not ``completed`` either, because nobody has
#: established that yet. Claiming either one would be a guess recorded as a
#: fact, and the calendar would show it as settled.
AWAITING_VERIFICATION = "awaiting_verification"


class SchedulerStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -- writes -------------------------------------------------------------
    def create(self, task: ScheduledTask) -> ScheduledTask:
        self.db.execute(
            """
            INSERT INTO scheduled_tasks
                (id, title, description, created_by, reason, created_at, scheduled_at,
                 timezone_name, status,
                 execution_type, script_path, model_config, task_payload, retry_policy,
                 metadata, attempts, last_error, last_run_at, lease_token,
                 recurrence, runs)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.id, task.title, task.description, task.created_by, task.reason,
                iso(task.created_at),
                iso(task.scheduled_at), task.timezone_name, task.status, task.execution_type,
                task.script_path, json_dump(task.model_config), json_dump(task.task_payload),
                json_dump(task.retry_policy), json_dump(task.metadata), task.attempts,
                task.last_error, iso(task.last_run_at), task.lease_token,
                json_dump(task.recurrence), task.runs,
            ),
        )
        return task

    def update(self, task_id: str, **fields: Any) -> Optional[ScheduledTask]:
        allowed = {
            "title", "description", "scheduled_at", "timezone_name", "status", "execution_type",
            "script_path", "model_config", "task_payload", "retry_policy", "metadata",
            "attempts", "last_error", "last_run_at", "lease_token",
            "recurrence", "runs",
        }
        assignments, params = [], []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in ("scheduled_at", "last_run_at") and isinstance(value, datetime):
                value = iso(value)
            if key in ("model_config", "task_payload", "retry_policy", "metadata",
                       "recurrence") and isinstance(
                value, (dict, list)
            ):
                value = json_dump(value)
            assignments.append(f"{key} = ?")
            params.append(value)
        if not assignments:
            return self.get(task_id)
        params.append(task_id)
        self.db.execute(
            f"UPDATE scheduled_tasks SET {', '.join(assignments)} WHERE id = ?", params
        )
        return self.get(task_id)

    def delete(self, task_id: str) -> bool:
        cursor = self.db.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
        return bool(cursor.rowcount)

    # -- reads --------------------------------------------------------------
    def get(self, task_id: str) -> Optional[ScheduledTask]:
        row = self.db.query_one("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,))
        return ScheduledTask.from_row(row) if row else None

    def list(
        self,
        status: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: int = 500,
    ) -> List[ScheduledTask]:
        sql = "SELECT * FROM scheduled_tasks WHERE 1=1"
        params: List[Any] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if start:
            sql += " AND scheduled_at >= ?"
            params.append(iso(start))
        if end:
            sql += " AND scheduled_at <= ?"
            params.append(iso(end))
        sql += " ORDER BY scheduled_at LIMIT ?"
        params.append(limit)
        return [ScheduledTask.from_row(r) for r in self.db.query(sql, params)]

    def next_due(self, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        """The next pending task and how long until it runs (section 7.5)."""
        now = now or utcnow()
        rows = self.db.query(
            "SELECT * FROM scheduled_tasks WHERE status = 'pending' "
            "AND scheduled_at >= ? ORDER BY scheduled_at LIMIT 1",
            (iso(now),),
        )
        if not rows:
            return None
        task = ScheduledTask.from_row(rows[0])
        return {
            "id": task.id,
            "title": task.title,
            "execution_type": task.execution_type,
            "scheduled_at": task.scheduled_at.isoformat(),
            "in_seconds": max(0.0, (task.scheduled_at - now).total_seconds()),
        }

    def due(self, now: Optional[datetime] = None, limit: int = 20) -> List[ScheduledTask]:
        now = now or utcnow()
        rows = self.db.query(
            """
            SELECT * FROM scheduled_tasks
            WHERE status = 'pending' AND scheduled_at <= ?
            ORDER BY scheduled_at LIMIT ?
            """,
            (iso(now), limit),
        )
        return [ScheduledTask.from_row(r) for r in rows]

    # -- execution bookkeeping ---------------------------------------------
    def claim(self, task: ScheduledTask) -> Optional[str]:
        """Duplicate execution prevention: only one claimer wins the row."""
        token = secrets.token_hex(8)
        cursor = self.db.execute(
            """
            UPDATE scheduled_tasks
            SET status = 'running', lease_token = ?, attempts = attempts + 1, last_run_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (token, iso(utcnow()), task.id),
        )
        return token if cursor.rowcount else None

    def complete(self, task_id: str, result: str = "") -> None:
        self.db.execute(
            "UPDATE scheduled_tasks SET status = 'completed', lease_token = NULL, "
            "last_error = NULL, metadata = json_set(COALESCE(NULLIF(metadata,''),'{}'), "
            "'$.last_result', ?) WHERE id = ?",
            (result[:2000], task_id),
        )

    def delay(self, task_id: str, until: datetime, note: str = "") -> None:
        """Not finished, and not wrong: put it back on the calendar at a new time.

        Distinct from ``fail(retry_at=...)``, which moves the row identically
        and means the opposite about the run. A delay leaves no error and
        **resets ``attempts``**, because claiming a task increments that
        counter and the counter is what the retry policy spends: without the
        reset, a task delayed three times would have used up its retries
        before its first real failure, and would then be marked failed on the
        first thing that actually went wrong. A task that is repeatedly
        not-yet is not a task that is repeatedly broken.
        """
        self.db.execute(
            """
            UPDATE scheduled_tasks
            SET status = 'pending', lease_token = NULL, last_error = NULL,
                attempts = 0, scheduled_at = ?,
                metadata = json_set(COALESCE(NULLIF(metadata,''),'{}'),
                                    '$.last_delay', ?)
            WHERE id = ?
            """,
            (iso(until), note[:500], task_id),
        )

    def postpone(self, task_id: str, until: datetime, note: str = "") -> None:
        """"Remind me later": moved like a delay, and the move written down -
        `metadata.postponed`, each {from, to, at} - so the reminder says how
        it got to its time: 7:00 -> 7:30 -> 8:00."""
        import json

        task = self.get(task_id)
        if task is None:
            return
        held = dict(task.metadata or {})
        log = list(held.get("postponed") or [])
        log.append({"from": iso(task.scheduled_at), "to": iso(until), "at": iso(utcnow())})
        held["postponed"] = log[-50:]
        self.db.execute("UPDATE scheduled_tasks SET metadata = ? WHERE id = ?",
                        (json.dumps(held, ensure_ascii=False), task_id))
        self.delay(task_id, until, note)

    def await_verification(self, task_id: str, result: str = "") -> None:
        """Park a task whose execution could not tell whether it finished."""
        self.db.execute(
            f"""
            UPDATE scheduled_tasks
            SET status = '{AWAITING_VERIFICATION}', lease_token = NULL,
                last_error = NULL,
                metadata = json_set(COALESCE(NULLIF(metadata,''),'{{}}'),
                                    '$.last_result', ?)
            WHERE id = ?
            """,
            (result[:2000], task_id),
        )

    def awaiting_verification(self, limit: int = 20) -> List[ScheduledTask]:
        """The tasks the data heartbeat still has to settle, oldest first."""
        rows = self.db.query(
            """
            SELECT * FROM scheduled_tasks
            WHERE status = ? ORDER BY last_run_at, scheduled_at LIMIT ?
            """,
            (AWAITING_VERIFICATION, limit),
        )
        return [ScheduledTask.from_row(r) for r in rows]

    def fail(self, task_id: str, error: str, retry_at: Optional[datetime] = None) -> None:
        if retry_at is not None:
            self.db.execute(
                """
                UPDATE scheduled_tasks
                SET status = 'pending', lease_token = NULL, last_error = ?, scheduled_at = ?
                WHERE id = ?
                """,
                (error[:1000], iso(retry_at), task_id),
            )
        else:
            self.db.execute(
                "UPDATE scheduled_tasks SET status = 'failed', lease_token = NULL, last_error = ? "
                "WHERE id = ?",
                (error[:1000], task_id),
            )

    def recover_orphans(self) -> int:
        """Agent restart recovery (section 59): running rows had no chance to finish."""
        cursor = self.db.execute(
            """
            UPDATE scheduled_tasks
            SET status = 'pending', lease_token = NULL,
                last_error = 'recovered after agent restart'
            WHERE status = 'running'
            """
        )
        return cursor.rowcount or 0

    def stats(self) -> Dict[str, Any]:
        rows = self.db.query(
            "SELECT status, COUNT(*) AS c FROM scheduled_tasks GROUP BY status"
        )
        upcoming = self.db.query(
            "SELECT COUNT(*) AS c FROM scheduled_tasks WHERE status='pending' AND scheduled_at <= ?",
            (iso(utcnow() + timedelta(days=1)),),
        )
        return {
            "by_status": {r["status"]: r["c"] for r in rows},
            "due_next_24h": upcoming[0]["c"] if upcoming else 0,
        }
