"""The user's to-do list: what they took off a notification to do later,
and what they added to it by hand (the user, 2026-11-01).

"Add to to-do" on a notification stops it - no more notifications about it -
and keeps the thing here, shown beside the progress bars of the Overview's
Summary task until the user removes it. "+ Add a to-do" under the list adds
one of their own, with no notification behind it. ONLY THE USER REMOVES ONE, by hand,
after a Yes: the agent never marks one done.

IT MAY SAY ONE LOOKS DONE, OR IS NO LONGER WANTED (the user, 2026-11-09: "允许模型suggest代办已做完，
模型不能直接删to do, 但是suggest之后，那个to do会出现一个mark as done按钮"; "包括chat, 现在也要有把to do
建议删掉的功能"): `suggest` keeps which and why, and the list offers Mark as done or Remove with it.
The user may turn it down (`decline`); then done is suggested again only on clear progress since
(heartbeat/core/todo_check.py). And `rename` names who one is about, its first words kept.
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

    def edit(self, todo_id: str, text: str, detail: str = "") -> Optional[Dict[str, Any]]:
        """Its words, as the user wrote them; None when there is no such to-do."""
        there = self.get(todo_id)
        if there is None or there.get("deleted_at"):
            return None
        self.db.execute("UPDATE todo_items SET text = ?, detail = ? WHERE id = ?",
                        (str(text or "").strip()[:300] or there["text"],
                         str(detail or "").strip()[:1000], todo_id))
        return self.get(todo_id)

    def suggest(self, todo_id: str, why: str, evidence: Any = (),
                kind: str = "done") -> Optional[Dict[str, Any]]:
        """LOOKS DONE ("done") or NO LONGER WANTED ("remove"), says the agent - with why, and what
        it rests on. Never done by it."""
        import json
        there = self.get(todo_id)
        if there is None or there.get("deleted_at") or there.get("done_at"):
            return None
        self.db.execute(
            "UPDATE todo_items SET suggested_kind = ?, suggested_at = ?, suggested_why = ?, "
            "suggested_from = ? WHERE id = ?",
            ("remove" if kind == "remove" else "done", utcnow().isoformat(),
             " ".join(str(why or "").split())[:600],
                             json.dumps([str(one)[:120] for one in list(evidence or [])[:10]],
                                        ensure_ascii=False), todo_id))
        return self.get(todo_id)

    def decline(self, todo_id: str, why: str = "") -> Optional[Dict[str, Any]]:
        """NOT DONE, says the user: the suggestion goes, and when they said so is kept."""
        there = self.get(todo_id)
        if there is None:
            return None
        self.db.execute(
            "UPDATE todo_items SET suggested_kind = '', suggested_at = '', suggested_why = '', "
            "suggested_from = '[]', declined_at = ?, declined_why = ? WHERE id = ?",
            (utcnow().isoformat(), str(why or "")[:600], todo_id))
        return self.get(todo_id)

    def rename(self, todo_id: str, text: str, detail: Optional[str] = None
               ) -> Optional[Dict[str, Any]]:
        """Named by who it is about (the agent's): its first words kept."""
        there = self.get(todo_id)
        text = " ".join(str(text or "").split())[:300]
        if there is None or there.get("deleted_at") or not text or text == there["text"]:
            return None
        self.db.execute(
            "UPDATE todo_items SET text = ?, detail = ?, renamed_from = ? WHERE id = ?",
            (text, there["detail"] if detail is None else str(detail).strip()[:1000],
             there.get("renamed_from") or there["text"], todo_id))
        return self.get(todo_id)

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
