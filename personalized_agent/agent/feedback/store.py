"""What the user thought of what the agent decided (section 52.4).

The agent schedules things on its own judgement. Nothing until now recorded
whether that judgement was any good: a task ran, and the only trace was that
it had run. An agent that cannot tell a welcome interruption from an unwelcome
one goes on making the same call forever, and the user's only recourse is to
delete tasks one at a time.

Every question is built from one of two templates, and there is no third - see
``templates.py`` for why the set is fixed and what each option does:

* a **permission**, before the agent acts, when it is not confident the user
  wants it done at all. The task waits for the answer.
* a **reminder**, telling the user to do something. Nothing waits on it.

Both end in the same place: a judgement, kept as a node in the graph, where
retrieval can reach it the next time a similar decision comes up. The option
the user pressed also says what becomes of the task, because a button whose
consequence is decided elsewhere is a button whose label can quietly stop
being true.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from ...common.events.models import iso, new_id, parse_iso, utcnow
from .templates import (PERMISSION, REMINDER, answers_for, kind_aliases,
                        options_for, template_of)

log = logging.getLogger(__name__)

#: A question's ``kind`` names the template it was built from. There are two,
#: and there is no third: every interruption the agent raises is one of them.
#: See ``templates.py`` for why they are fixed and what each option does.
#:
#: Asked before the agent acts; the task waits for the answer.
KIND_PERMISSION = PERMISSION
#: Telling the user to do something; nothing waits on the answer.
KIND_REMINDER = REMINDER

#: The names these went by before the templates. Aliases rather than history:
#: rows already in the database carry the old word, and ``kind_aliases`` makes
#: a query for either name find both, so an upgrade does not strand a queue of
#: unanswerable questions.
KIND_APPROVAL = KIND_PERMISSION
KIND_REVIEW = KIND_REMINDER

PENDING = "pending"
ANSWERED = "answered"
EXPIRED = "expired"


def answers_for_kind(kind: str) -> tuple:
    """The answers this question accepts, which its template decides."""
    return answers_for(kind)


@dataclass
class TaskQuestion:
    """One thing the agent asked the user about one task."""

    task_id: str
    kind: str
    question: str
    id: str = field(default_factory=lambda: new_id("ask"))
    #: What the task said it would do - the user is judging this, so it is
    #: kept with the question rather than looked up later, when the task may
    #: have been edited or deleted.
    task_detail: str = ""
    #: Why the agent scheduled it, in its own words.
    arrangement: str = ""
    asked_at: Optional[datetime] = None
    answered_at: Optional[datetime] = None
    answer: str = ""
    comment: str = ""
    status: str = PENDING
    #: The graph node this became once answered.
    node_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def options(self) -> List[Dict[str, Any]]:
        """The buttons this question offers, with their labels.

        Taken from what was recorded when the question was asked, in
        preference to rebuilding them from the template now. The delay option
        carries an interval chosen for this task at that moment; regenerating
        it later would show the user a different number from the one the agent
        decided on, and - once answered - a different one again from the one
        they pressed.
        """
        recorded = (self.metadata or {}).get("options")
        if isinstance(recorded, list) and recorded:
            return [o for o in recorded if isinstance(o, dict)]
        return [o.to_dict() for o in options_for(self.kind)]

    @property
    def answers(self) -> tuple:
        """Just the keys, for validating what comes back."""
        return tuple(str(o.get("key")) for o in self.options if o.get("key"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "kind": self.kind,
            # The template this belongs to, with the old names resolved.
            # ``kind`` is whatever was written at the time - a row asked
            # before the templates existed says "approval" - and a caller
            # that branches on it has to carry the mapping too. It is one
            # line here and a second vocabulary everywhere else.
            "template": template_of(self.kind),
            "question": self.question,
            "task_detail": self.task_detail,
            "arrangement": self.arrangement,
            "options": list(self.options),
            "asked_at": iso(self.asked_at),
            "answered_at": iso(self.answered_at),
            "answer": self.answer,
            "comment": self.comment,
            "status": self.status,
            "node_id": self.node_id,
            "metadata": self.metadata,
        }

    @staticmethod
    def from_row(row: Any) -> "TaskQuestion":
        return TaskQuestion(
            id=row["id"],
            task_id=row["task_id"],
            kind=row["kind"],
            question=row["question"],
            task_detail=row["task_detail"] or "",
            arrangement=row["arrangement"] or "",
            asked_at=parse_iso(row["asked_at"]),
            answered_at=parse_iso(row["answered_at"]),
            answer=row["answer"] or "",
            comment=row["comment"] or "",
            status=row["status"] or PENDING,
            node_id=row["node_id"] or "",
            metadata=json.loads(row["metadata"] or "{}"),
        )


class TaskQuestionStore:
    def __init__(self, db: Any) -> None:
        self.db = db

    def ask(self, question: TaskQuestion) -> TaskQuestion:
        question.asked_at = question.asked_at or utcnow()
        self.db.execute(
            "INSERT INTO task_questions (id, task_id, kind, question, task_detail, "
            "arrangement, asked_at, answered_at, answer, comment, status, node_id, "
            "metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (question.id, question.task_id, question.kind, question.question,
             question.task_detail, question.arrangement, iso(question.asked_at),
             iso(question.answered_at), question.answer, question.comment,
             question.status, question.node_id, json.dumps(question.metadata)),
        )
        return question

    def get(self, question_id: str) -> Optional[TaskQuestion]:
        rows = self.db.query("SELECT * FROM task_questions WHERE id = ?", (question_id,))
        return TaskQuestion.from_row(rows[0]) if rows else None

    def pending(self, limit: int = 50) -> List[TaskQuestion]:
        rows = self.db.query(
            "SELECT * FROM task_questions WHERE status = ? ORDER BY asked_at ASC LIMIT ?",
            (PENDING, limit))
        return [TaskQuestion.from_row(row) for row in rows]

    def pending_for(self, task_id: str, kind: str = "") -> Optional[TaskQuestion]:
        sql = "SELECT * FROM task_questions WHERE task_id = ? AND status = ?"
        params: List[Any] = [task_id, PENDING]
        if kind:
            # Every name this template has gone by, so that a row written under
            # the old one is still found - and so is a caller still asking by
            # it. The kind is matched in SQL, where resolving it in Python
            # afterwards would be too late.
            aliases = kind_aliases(kind)
            sql += f" AND kind IN ({', '.join('?' for _ in aliases)})"
            params.extend(aliases)
        rows = self.db.query(sql + " ORDER BY asked_at DESC LIMIT 1", tuple(params))
        return TaskQuestion.from_row(rows[0]) if rows else None

    def recent(self, limit: int = 50) -> List[TaskQuestion]:
        rows = self.db.query(
            "SELECT * FROM task_questions ORDER BY asked_at DESC LIMIT ?", (limit,))
        return [TaskQuestion.from_row(row) for row in rows]

    def answer(self, question_id: str, answer: str,
               comment: str = "") -> Optional[TaskQuestion]:
        question = self.get(question_id)
        if question is None:
            return None
        if answer not in question.answers:
            raise ValueError(
                f"{answer!r} is not one of {', '.join(question.answers)}")
        question.answer = answer
        question.comment = comment
        question.status = ANSWERED
        question.answered_at = utcnow()
        self.db.execute(
            "UPDATE task_questions SET answer = ?, comment = ?, status = ?, "
            "answered_at = ? WHERE id = ?",
            (answer, comment, ANSWERED, iso(question.answered_at), question_id))
        return question

    def attach_node(self, question_id: str, node_id: str) -> None:
        self.db.execute("UPDATE task_questions SET node_id = ? WHERE id = ?",
                        (node_id, question_id))

    def expire(self, question_id: str) -> None:
        """A question nobody answered is closed rather than left open forever."""
        self.db.execute("UPDATE task_questions SET status = ? WHERE id = ?",
                        (EXPIRED, question_id))


__all__ = ["TaskQuestion", "TaskQuestionStore",
           "KIND_PERMISSION", "KIND_REMINDER", "KIND_APPROVAL", "KIND_REVIEW",
           "answers_for_kind", "options_for",
           "PENDING", "ANSWERED", "EXPIRED"]
