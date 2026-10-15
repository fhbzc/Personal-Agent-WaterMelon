"""Conversation storage for the dialog interface."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...common.events.models import json_dump, json_load, new_id


class ConversationStore:
    def __init__(self, db: Any) -> None:
        self.db = db

    def create(self, title: str = "", project_id: Optional[str] = None) -> str:
        if project_id and not self.project(project_id):
            raise ValueError("there is no such chat project")
        conversation_id = new_id("conv")
        self.db.execute(
            "INSERT INTO conversations (id, created_at, title, project_id) "
            "VALUES (?, ?, ?, ?)",
            (conversation_id, datetime.now(timezone.utc).isoformat(), title,
             project_id or None),
        )
        return conversation_id

    def conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        return self.db.query_one("SELECT * FROM conversations WHERE id = ?",
                                 (conversation_id,))

    def ensure(self, conversation_id: Optional[str]) -> str:
        if conversation_id:
            row = self.db.query_one("SELECT id FROM conversations WHERE id = ?",
                                    (conversation_id,))
            if row:
                return conversation_id
        return self.create()

    def add_message(self, conversation_id: str, role: str, content: str,
                    metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        message_id = new_id("msg")
        created = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "INSERT INTO messages (id, conversation_id, created_at, role, content, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (message_id, conversation_id, created, role, content, json_dump(metadata or {})),
        )
        return {"id": message_id, "conversation_id": conversation_id, "created_at": created,
                "role": role, "content": content, "metadata": metadata or {}}

    def messages(self, conversation_id: str,
                 limit: Optional[int] = 50) -> List[Dict[str, Any]]:
        """The first `limit` messages in order - all of them with None.

        In the order they were said: two messages written in the same
        instant are ordered by when they were written, not by chance.
        """
        rows = self.db.query(
            "SELECT * FROM messages WHERE conversation_id = ? "
            "ORDER BY created_at, rowid LIMIT ?",
            (conversation_id, -1 if limit is None else int(limit)),
        )
        for row in rows:
            row["metadata"] = json_load(row.get("metadata"))
        return rows

    def recent_messages(self, conversation_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """The last `limit` messages, in the order they were said."""
        rows = self.db.query(
            "SELECT * FROM messages WHERE conversation_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (conversation_id, max(0, int(limit))),
        )
        rows.reverse()
        for row in rows:
            row["metadata"] = json_load(row.get("metadata"))
        return rows

    # -- what the model is told of a long conversation -------------------------
    def summary(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        return self.db.query_one("SELECT * FROM chat_summaries WHERE conversation_id = ?",
                                 (conversation_id,))

    def save_summary(self, conversation_id: str, through_id: str, covered: int,
                     summary: str) -> Dict[str, Any]:
        row = {"conversation_id": conversation_id, "through_id": through_id,
               "covered": int(covered), "summary": summary,
               "created_at": datetime.now(timezone.utc).isoformat()}
        self.db.execute(
            "INSERT INTO chat_summaries (conversation_id, through_id, covered, summary, "
            "created_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(conversation_id) DO UPDATE SET "
            "through_id = excluded.through_id, covered = excluded.covered, "
            "summary = excluded.summary, created_at = excluded.created_at",
            tuple(row[key] for key in ("conversation_id", "through_id", "covered",
                                       "summary", "created_at")))
        return row

    # -- follow-ups ----------------------------------------------------------
    def message(self, message_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one("SELECT * FROM messages WHERE id = ?", (message_id,))
        if row is not None:
            row["metadata"] = json_load(row.get("metadata"))
        return row

    def add_followup(self, message: Dict[str, Any], about: str, question: str,
                     answer: str) -> Dict[str, Any]:
        followup = {"id": new_id("fup"),
                    "conversation_id": str(message.get("conversation_id") or ""),
                    "message_id": str(message["id"]), "about": about,
                    "question": question, "answer": answer,
                    "created_at": datetime.now(timezone.utc).isoformat()}
        self.db.execute(
            "INSERT INTO chat_followups (id, conversation_id, message_id, about, "
            "question, answer, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            tuple(followup[key] for key in (
                "id", "conversation_id", "message_id", "about", "question",
                "answer", "created_at")))
        return followup

    def followups(self, message_ids: List[str]) -> List[Dict[str, Any]]:
        if not message_ids:
            return []
        return self.db.query(
            "SELECT * FROM chat_followups WHERE message_id IN ("
            + ",".join("?" * len(message_ids)) + ") ORDER BY created_at",
            tuple(message_ids))

    def delete_followup(self, followup_id: str) -> bool:
        return self.db.execute("DELETE FROM chat_followups WHERE id = ?",
                               (followup_id,)).rowcount > 0

    def conversations(self, limit: Optional[int] = 30) -> List[Dict[str, Any]]:
        """Conversations, the one last spoken in first - all of them with None.

        A conversation with no title of its own is named by the first thing
        the user said in it, which is what they will recognise it by.
        """
        return self.db.query(
            """
            SELECT c.id, c.created_at, c.project_id,
                   COALESCE(NULLIF(c.title, ''),
                       (SELECT substr(content, 1, 120) FROM messages
                        WHERE conversation_id = c.id AND role = 'user'
                        ORDER BY created_at, rowid LIMIT 1), 'New chat') AS title,
                   COALESCE(MAX(m.created_at), c.created_at) AS updated_at,
                   COUNT(m.id) AS message_count
            FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id
            GROUP BY c.id ORDER BY updated_at DESC, COALESCE(MAX(m.rowid), 0) DESC,
                     c.created_at DESC, c.id DESC LIMIT ?
            """,
            (-1 if limit is None else int(limit),),
        )

    # -- projects ----------------------------------------------------------------
    def projects(self) -> List[Dict[str, Any]]:
        return self.db.query("SELECT * FROM chat_projects ORDER BY created_at, id")

    def project(self, project_id: str) -> Optional[Dict[str, Any]]:
        return self.db.query_one("SELECT * FROM chat_projects WHERE id = ?", (project_id,))

    def create_project(self, name: str) -> Dict[str, Any]:
        name = str(name or "").strip()
        if not name or len(name) > 120:
            raise ValueError("a project's name is 1 to 120 characters")
        project = {"id": new_id("project"),
                   "created_at": datetime.now(timezone.utc).isoformat(), "name": name}
        self.db.execute("INSERT INTO chat_projects (id, created_at, name) VALUES (?, ?, ?)",
                        (project["id"], project["created_at"], project["name"]))
        return project

    def first_said(self, conversation_id: str) -> str:
        """The first thing the user said in it - what an untitled one is
        listed by."""
        row = self.db.query_one(
            "SELECT content FROM messages WHERE conversation_id = ? AND role = 'user' "
            "ORDER BY created_at, rowid LIMIT 1", (conversation_id,))
        return str((row or {}).get("content") or "")[:120]

    def folder_of(self, conversation_id: str) -> str:
        """Where its files are kept, once it wrote one (agent/dialog/files.py)."""
        row = self.db.query_one("SELECT folder FROM conversations WHERE id = ?",
                                (conversation_id,))
        return str((row or {}).get("folder") or "")

    def set_folder(self, conversation_id: str, folder: str) -> None:
        self.db.execute("UPDATE conversations SET folder = ? WHERE id = ?",
                        (folder, conversation_id))

    def set_title(self, conversation_id: str, title: str) -> None:
        self.db.execute("UPDATE conversations SET title = ? WHERE id = ?",
                        (title[:120], conversation_id))
