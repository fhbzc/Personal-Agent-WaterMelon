"""The user's to-do list: what they took off a notification to do later.

"Add to to-do" on a notification stops it - no more notifications about it -
and keeps the thing here, shown beside the progress bars of the Overview's
Summary task until the user removes it. ONLY THE USER REMOVES ONE, by hand,
after a Yes: the agent never marks one done.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ...common.events.models import new_id, utcnow

log = logging.getLogger(__name__)

#: How long a done one stays on the list, struck through, before it goes.
DONE_SHOWN_DAYS = 3


class TodoList:
    """`todo_items` rows, one per thing the user put off to do."""

    def __init__(self, db: Any) -> None:
        self.db = db

    def add(self, text: str, detail: str = "", notification_id: str = "",
            task_id: str = "") -> Dict[str, Any]:
        """One more; a notification already on the list is not added twice."""
        if notification_id:
            there = self.db.query_one(
                "SELECT * FROM todo_items WHERE notification_id = ? AND deleted_at = ''",
                (notification_id,))
            if there is not None:
                return dict(there)
        row = {"id": new_id("todo"), "created_at": utcnow().isoformat(),
               "text": str(text or "").strip()[:300] or "(no title)",
               "detail": str(detail or "").strip()[:1000],
               "notification_id": notification_id, "task_id": task_id}
        self.db.execute(
            "INSERT INTO todo_items (id, created_at, text, detail, notification_id, task_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (row["id"], row["created_at"], row["text"], row["detail"],
             row["notification_id"], row["task_id"]))
        return self.get(row["id"]) or row

    def get(self, todo_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one("SELECT * FROM todo_items WHERE id = ?", (todo_id,))
        return dict(row) if row is not None else None

    def shown(self) -> List[Dict[str, Any]]:
        """What the list shows: every open one, oldest first, then the ones
        done in the last few days."""
        from datetime import timedelta
        since = (utcnow() - timedelta(days=DONE_SHOWN_DAYS)).isoformat()
        return [dict(row) for row in self.db.query(
            "SELECT * FROM todo_items WHERE deleted_at = '' AND (done_at = '' OR done_at >= ?) "
            "ORDER BY (done_at != ''), created_at", (since,))]

    def open(self) -> List[Dict[str, Any]]:
        return [dict(row) for row in self.db.query(
            "SELECT * FROM todo_items WHERE deleted_at = '' AND done_at = '' "
            "ORDER BY created_at")]

    def tick(self, todo_id: str, done: bool = True, by: str = "user",
             why: str = "") -> Optional[Dict[str, Any]]:
        """Done - by the user, or by the agent with why - or not done again."""
        if self.get(todo_id) is None:
            return None
        if done:
            self.db.execute("UPDATE todo_items SET done_at = ?, done_by = ?, done_why = ? "
                            "WHERE id = ? AND done_at = ''",
                            (utcnow().isoformat(), by, str(why or "")[:600], todo_id))
        else:
            self.db.execute("UPDATE todo_items SET done_at = '', done_by = '', done_why = '' "
                            "WHERE id = ?", (todo_id,))
        return self.get(todo_id)

    def delete(self, todo_id: str) -> bool:
        if self.get(todo_id) is None:
            return False
        self.db.execute("UPDATE todo_items SET deleted_at = ? WHERE id = ?",
                        (utcnow().isoformat(), todo_id))
        return True


__all__ = ["TodoList", "DONE_SHOWN_DAYS"]
