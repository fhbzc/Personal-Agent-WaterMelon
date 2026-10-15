"""WAITING FOR A TIME: what the user agreed to whose time is not known yet - "the
time works for me" about a talk, when the message with the time is folded
away; a meeting both said yes to, its day still to be set.

The notification step (heartbeat.py, step 3, and the triggers that run it at
once - triggers.py) is given these every time. It writes one when something
is agreed without a time; it RESOLVES one when the time is seen - adding the
notification for it in the same answer - and DROPS one that is off. The user
can take one off, or reword it, on the Calendar.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

#: How the step is told of them, and what it may answer about them.
INSTRUCTION = """AND WHAT IS WAITING FOR A TIME.

Something the user agreed to whose time is not known - "the time works for
me", and the message with the time is not on the screen; a meeting agreed,
its day still to be set - is written as WAITING (under "pending"): what it
is, with whom, and what in the activity shows it. Never a notification at a
guessed time.

Every one already waiting is listed. When the activity gives its time - an
invitation, a message with the date - ADD the notification for it as any
other, and RESOLVE the waiting one (under "pending_resolved"), saying where
the time came from. When it is called off, or turns out not to happen, DROP
it (under "pending_dropped"), saying why. One already listed is not written
again."""

RETURNS = {
    "pending": [{"title": "short: what is waiting for a time, and with whom",
                 "detail": "one or two sentences: what was agreed, and what is still missing",
                 "people": ["the names of the people it is with"],
                 "evidence": ["the ids of the events or states that show it"]}],
    "pending_resolved": [{"id": "a waiting one's id, exactly (wait_...)",
                          "why": "where its time came from - the notification added for it"}],
    "pending_dropped": [{"id": "a waiting one's id, exactly (wait_...)",
                         "why": "what showed it is off"}],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PendingStore:
    """The things waiting for a time."""

    def __init__(self, db: Any) -> None:
        self.db = db

    def add(self, title: str, detail: str = "", people: Optional[List[str]] = None,
            evidence: Optional[List[str]] = None, by: str = "agent") -> Optional[str]:
        from ...common.events.models import new_id
        title = " ".join(str(title or "").split())[:200]
        if not title:
            return None
        # Not twice: one open with the same words is the same thing.
        for one in self.open():
            if one["title"].casefold() == title.casefold():
                return None
        ident = new_id("wait")
        self.db.execute(
            "INSERT INTO pending_confirmations (id, title, detail, people, evidence, status, "
            "by, created_at, updated_at, closed_why, task_id) VALUES (?, ?, ?, ?, ?, 'open', ?, "
            "?, ?, '', '')",
            (ident, title, str(detail or "")[:800],
             json.dumps([str(p)[:80] for p in people or []][:8], ensure_ascii=False),
             json.dumps([str(e)[:80] for e in evidence or []][:12]), by, _now(), _now()))
        return ident

    def close(self, ident: str, status: str, why: str = "", task_id: str = "") -> bool:
        if status not in ("resolved", "dropped"):
            raise ValueError("resolved or dropped")
        row = self.db.query_one("SELECT status FROM pending_confirmations WHERE id = ?",
                                (str(ident),))
        if row is None or row["status"] != "open":
            return False
        self.db.execute("UPDATE pending_confirmations SET status = ?, closed_why = ?, task_id = ?, "
                        "updated_at = ? WHERE id = ?",
                        (status, str(why or "")[:600], str(task_id or ""), _now(), str(ident)))
        return True

    def edit(self, ident: str, title: str = "", detail: Optional[str] = None) -> bool:
        row = self.db.query_one("SELECT * FROM pending_confirmations WHERE id = ?", (str(ident),))
        if row is None:
            return False
        self.db.execute("UPDATE pending_confirmations SET title = ?, detail = ?, updated_at = ? "
                        "WHERE id = ?",
                        ((title or row["title"])[:200],
                         row["detail"] if detail is None else str(detail)[:800], _now(),
                         str(ident)))
        return True

    def open(self) -> List[Dict[str, Any]]:
        return [self._shaped(row) for row in self.db.query(
            "SELECT * FROM pending_confirmations WHERE status = 'open' ORDER BY created_at")]

    def recent(self, most: int = 50) -> List[Dict[str, Any]]:
        return [self._shaped(row) for row in self.db.query(
            "SELECT * FROM pending_confirmations ORDER BY updated_at DESC LIMIT ?", (int(most),))]

    @staticmethod
    def _shaped(row: Any) -> Dict[str, Any]:
        one = dict(row)
        for key in ("people", "evidence"):
            try:
                one[key] = json.loads(one.get(key) or "[]")
            except ValueError:
                one[key] = []
        return one

    def lines(self) -> List[str]:
        """Each open one as the step reads it."""
        return [f"[{one['id']}] {one['title']}" + (f" - {one['detail']}" if one["detail"] else "")
                + (f" (with {', '.join(one['people'])})" if one["people"] else "")
                + f" - waiting since {one['created_at'][:16]}" for one in self.open()]

    def apply(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """What the step said of them: new ones, resolved, dropped."""
        added, resolved, dropped = [], [], []
        for one in payload.get("pending") or []:
            if isinstance(one, dict):
                ident = self.add(one.get("title"), one.get("detail") or "",
                                 one.get("people") or [], one.get("evidence") or [])
                if ident:
                    added.append({"id": ident, "title": str(one.get("title"))[:200]})
        for key, status, out in (("pending_resolved", "resolved", resolved),
                                 ("pending_dropped", "dropped", dropped)):
            for one in payload.get(key) or []:
                if isinstance(one, dict) and self.close(str(one.get("id") or ""), status,
                                                        str(one.get("why") or "")):
                    out.append(str(one.get("id")))
        return {key: value for key, value in (("pending_added", added),
                                              ("pending_resolved", resolved),
                                              ("pending_dropped", dropped)) if value}


__all__ = ["PendingStore", "INSTRUCTION", "RETURNS"]
