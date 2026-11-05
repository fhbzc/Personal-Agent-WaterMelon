"""WHAT STILL OPEN BELONGS TO WHICH WORK (the user, 2026-11-04: "summary好task之后去看一下
还active的notification, to do, work assined 是包括在这个task/project下").

When a run of the state heartbeat has written its tasks, the model says which
tasks and projects each thing still open belongs to - several, or none. Clicking
a project shows what is open under it; each notification, to-do and work
assigned says what it is part of; and a model reading one is told it (`note`).

Only ids are kept: a task or a project renamed, moved or put away is read as it
is now, and one put away is no longer named.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...common.events.models import utcnow
from . import task_projects as tp
from .tasks import Kept

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS state_item_links (
    kind      TEXT NOT NULL,                  -- notification | todo | assigned
    item_id   TEXT NOT NULL,
    tasks     TEXT NOT NULL DEFAULT '[]',
    projects  TEXT NOT NULL DEFAULT '[]',
    at        TEXT NOT NULL,
    PRIMARY KEY (kind, item_id)
);
"""

KINDS = ("notification", "todo", "assigned")
#: What one call is given: the open things, the newest projects, tasks of each.
MOST_ITEMS, MOST_PROJECTS, TASKS_EACH = 60, 60, 8

#: The made-up examples are on purpose (see task_projects.WRITER_SYSTEM).
LINKS_SYSTEM = """You keep a personal activity record. Below are the user's PROJECTS, each with \
its latest tasks, and the things STILL OPEN for them: notifications not yet answered, to-dos, \
and work they handed to their agent.

Say which tasks and projects each open thing belongs to: the work it is part of, or that it \
is about. "Send the revised budget to the grant office" belongs to a task "Revising the grant \
budget". One thing may belong to several, or to none: a thing with no work of the user's \
behind it belongs to none. Name a task when it is about that task; name the project when it \
is about the project as a whole. Sharing only a topic, a tool, a website or a person is not \
belonging. When unsure, leave it out.

Answer with JSON only, every open thing in it: \
{"links": {"<open thing's id>": {"tasks": ["<task id>"], "projects": ["<project id>"]}}}"""


def _ids(text: Any) -> List[str]:
    try:
        return [str(one) for one in json.loads(text or "[]")]
    except (TypeError, ValueError):
        return []


class ItemLinks:
    """`state_item_links`, read as the work is now."""

    def __init__(self, db: Any, scope: str = "live") -> None:
        self.db = db
        self.tasks = tp.RunTasks(db, scope)
        self.kept = Kept(db, scope)
        with self.db.write() as conn:
            conn.executescript(SCHEMA)

    def put(self, kind: str, item: str, tasks: Sequence[str], projects: Sequence[str]) -> None:
        self.db.execute(
            "INSERT INTO state_item_links (kind, item_id, tasks, projects, at) VALUES "
            "(?, ?, ?, ?, ?) ON CONFLICT(kind, item_id) DO UPDATE SET tasks = excluded.tasks, "
            "projects = excluded.projects, at = excluded.at",
            (kind, item, json.dumps(list(tasks)), json.dumps(list(projects)),
             utcnow().isoformat()))

    @staticmethod
    def _project(row: Any) -> Optional[Dict[str, Any]]:
        described = dict(getattr(row, "described", None) or {})
        if described.get("deleted_by_user") or not described.get("title"):
            return None
        english = dict(described.get("in_english") or {})
        return {"kind": "project", "id": row.id, "project_id": row.id,
                "title": described["title"],
                "title_english": english.get("title") or described["title"]}

    def _task(self, ident: str) -> Optional[Dict[str, Any]]:
        one = self.tasks.one(ident)
        if one is None:
            return None
        title = one.get("title_display") or one.get("title") or ""
        return {"kind": "task", "id": ident, "project_id": one.get("project_id") or "",
                "title": title, "title_english": one.get("title") or title} if title else None

    def moved(self, old: Sequence[str], new: str) -> None:
        """Projects merged: what was linked to them is linked to the one they became."""
        gone = set(old)
        for row in self.db.query("SELECT kind, item_id, projects FROM state_item_links"):
            ids = _ids(row["projects"])
            if gone & set(ids):
                kept = [one for one in ids if one not in gone]
                self.db.execute("UPDATE state_item_links SET projects = ? WHERE kind = ? AND "
                                "item_id = ?", (json.dumps(kept + [new] if new not in kept else kept),
                                                row["kind"], row["item_id"]))

    def of_many(self, kind: str, items: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
        """What each is part of: its tasks, then its projects."""
        wanted = [str(one) for one in items if one]
        if not wanted:
            return {}
        holes = ", ".join("?" for _ in wanted)
        out: Dict[str, List[Dict[str, Any]]] = {}
        for row in self.db.query(f"SELECT * FROM state_item_links WHERE kind = ? AND item_id IN "
                                 f"({holes})", (kind, *wanted)):
            links = [self._task(one) for one in _ids(row["tasks"])]
            links += [self._project(one) for one in self.kept.by_ids(_ids(row["projects"]))]
            kept = [one for one in links if one]
            if kept:
                out[row["item_id"]] = kept
        return out

    def of(self, kind: str, item: str) -> List[Dict[str, Any]]:
        return self.of_many(kind, [item]).get(str(item), [])

    def note(self, kind: str, item: str) -> str:
        """What a model reading it is told, in English as the tasks are kept."""
        try:
            names = [one["title_english"] for one in self.of(kind, item)]
        except Exception:
            log.debug("what it is part of could not be read", exc_info=True)
            return ""
        return f"Part of the user's work: {'; '.join(names)}." if names else ""

    def by_project(self, items: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """The open things under each project, by their own link or a task's."""
        out: Dict[str, List[Dict[str, Any]]] = {}
        for kind in KINDS:
            mine = [one for one in items if one["kind"] == kind]
            links = self.of_many(kind, [one["id"] for one in mine])
            for one in mine:
                for project in {link["project_id"] for link in links.get(one["id"], [])
                                if link["project_id"]}:
                    out.setdefault(project, []).append(
                        {key: one.get(key, "") for key in ("kind", "id", "text", "detail")})
        return out


def links_text(items: Sequence[Dict[str, Any]], projects: Sequence[Any],
               tasks_of: Dict[str, List[Dict[str, Any]]]
               ) -> Tuple[str, Dict[str, Tuple[str, str]], Dict[str, str], Dict[str, str]]:
    """The prompt, and what its short labels stand for."""
    of_item = {f"I{n}": (one["kind"], one["id"]) for n, one in enumerate(items, 1)}
    of_project = {f"P{n}": one.id for n, one in enumerate(projects, 1)}
    of_task: Dict[str, str] = {}
    lines = ["PROJECTS AND THEIR LATEST TASKS:"]
    for label, one in zip(of_project, projects):
        described = dict(one.described or {})
        english = dict(described.get("in_english") or {})
        lines.append(f"{label}: {english.get('title') or described.get('title') or ''} - "
                     f"{english.get('what_it_is') or described.get('what_it_is') or ''}")
        for task in tasks_of.get(one.id, []):
            tag = f"T{len(of_task) + 1}"
            of_task[tag] = task["id"]
            lines.append(f"  {tag}: {task.get('title') or ''} - "
                         f"{str(task.get('summary') or '')[:200]}")
    lines += ["", "STILL OPEN:"]
    for label, one in zip(of_item, items):
        detail = str(one.get("detail") or "")[:300]
        lines.append(f"{label} ({one['kind']}): {one.get('text') or ''}"
                     + (f" - {detail}" if detail else ""))
    return "\n".join(lines), of_item, of_project, of_task


def keep_answer(links: ItemLinks, answer: Dict[str, Any], of_item: Dict[str, Tuple[str, str]],
                of_project: Dict[str, str], of_task: Dict[str, str]) -> int:
    """The model's links kept. What it did not answer stands as it was; a label it
    made up is passed over."""
    said = answer.get("links")
    kept = 0
    for label, value in (said.items() if isinstance(said, dict) else ()):
        item = of_item.get(str(label))
        if item is None or not isinstance(value, dict):
            continue
        links.put(item[0], item[1],
                  [of_task[one] for one in value.get("tasks") or [] if one in of_task],
                  [of_project[one] for one in value.get("projects") or [] if one in of_project])
        kept += 1
    return kept


__all__ = ["ItemLinks", "KINDS", "LINKS_SYSTEM", "MOST_ITEMS", "MOST_PROJECTS", "TASKS_EACH",
           "keep_answer", "links_text"]
