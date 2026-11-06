"""AFTER A TO-DO GOES (the user, 2026-11-05: "删除to do, mark as done 之后，模型会被额外调用做一个query,
去看有没有什么notification或者什么要manage的。这个调用模型让模型自己检查").

Marked done or removed, a to-do may leave things behind that were only there for it: a reminder
still to come, a step on a project's list. Seeing an email sent is not possible from the screen
(a reminder fired for a reply already written, 2026-11-05), but here the user has said so. So the
model is shown what is still waiting and judges what no longer has a reason to.

The judgement is the model's. The code holds only the limits: it acts on what it was shown, a
reminder that is still pending, a step the user has not taken back - and never on the user's own
to-do list, which only they take things off (todo_tools.py). What it closes keeps its place and
says why.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable, Dict, List

log = logging.getLogger(__name__)

TABLE = "state_tasks"
#: The most of each shown to the model.
MOST_SHOWN = 200

SYSTEM = """The user has just finished with something on their to-do list: "done" means they say it
is done, "removed" means they took it off, which may only mean it is no longer wanted. You are shown
it, and what is still waiting for them: reminders that have not fired yet, and the open steps on
their projects.

Decide which of those no longer have a reason to be there because of this. A reminder or a step
about the same thing has none: "Write to Sam about lunch" done means "Reply to Sam to set a day"
should not come. Go by what it is about, not by shared words: another thing to do for the same
person, or a later step that comes after this one, still stands. Most of the time nothing else is
related, and then say so by listing nothing. When in doubt, leave it.

"the_rest_of_their_list" is for you to see only: nothing on it can be changed here.

Answer in JSON only:
{"close": [{"id": "the reminder's id", "why": "one sentence"}],
 "finish": [{"work": "the work's id", "step": "the step's id", "why": "one sentence"}]}"""


class AfterTodo:
    def __init__(self, db: Any, models: Callable[[], Any], scheduler: Any, todos: Any,
                 allowed: Callable[[], bool] = lambda: True) -> None:
        self.db = db
        self._models = models
        self.scheduler = scheduler
        self.todos = todos
        self.allowed = allowed

    def check(self, todo: Dict[str, Any], how: str) -> None:
        """In the background: the page does not wait for a model."""
        if not self.allowed():
            return
        threading.Thread(target=self._safely, args=(todo, how), daemon=True,
                         name="after-todo").start()

    def _safely(self, todo: Dict[str, Any], how: str) -> None:
        try:
            self.run(todo, how)
        except Exception:
            log.warning("what a to-do left behind could not be looked at", exc_info=True)

    def _reminders(self) -> List[Dict[str, Any]]:
        store = getattr(self.scheduler, "store", None)
        if store is None:
            return []
        return [{"id": one.id, "title": one.title, "about": one.description,
                 "at": one.scheduled_at.isoformat() if one.scheduled_at else ""}
                for one in store.list(status="pending", limit=MOST_SHOWN)
                if one.execution_type == "notification"]

    def _steps(self) -> List[Dict[str, Any]]:
        from ...heartbeat.states import todo as steps
        from ...heartbeat.states.tasks import LIVE
        out: List[Dict[str, Any]] = []
        for row in self.db.query(
                f"SELECT id, described FROM {TABLE} WHERE scope = ? AND "
                "(retired_at IS NULL OR retired_at = '') AND (archived_at IS NULL OR archived_at = '')",
                (LIVE,)):
            try:
                said = json.loads(dict(row).get("described") or "{}")
            except (TypeError, ValueError):
                continue
            for one in steps.items(said if isinstance(said, dict) else {}):
                if not one.get("done") and not one.get("open_by_user"):
                    out.append({"work": row["id"], "work_title": str(said.get("title") or ""),
                                "step": str(one.get("id")), "text": str(one.get("text") or ""),
                                "reminder": str(one.get("reminder") or "")})
        return out[:MOST_SHOWN]

    def run(self, todo: Dict[str, Any], how: str) -> Dict[str, Any]:
        """One look: what was closed and finished. No model is asked when nothing is waiting."""
        reminders, open_steps = self._reminders(), self._steps()
        if not reminders and not open_steps:
            return {"asked": False, "closed": [], "finished": []}
        from ...common.languages import in_english
        from ...heartbeat.states.heartbeat import _as_json
        from ...models.base import ModelRequest
        handed = {"what_the_user_did": how,
                  "the_to_do": {"text": todo.get("text"), "detail": todo.get("detail")},
                  "reminders_waiting": reminders, "open_steps": open_steps,
                  "the_rest_of_their_list": [one.get("text") for one in self.todos.open()][:MOST_SHOWN]}
        response = self._models().invoke(ModelRequest(
            messages=[{"role": "user", "content": json.dumps(handed, ensure_ascii=False, indent=1)[:60000]}],
            system=SYSTEM, max_tokens=1500, task="after_todo",
            language=in_english("why each was closed or finished")), role="heartbeat")
        said = _as_json(getattr(response, "text", "") or "") or {}
        said = said if isinstance(said, dict) else {}
        closed = self._close(said.get("close"), {one["id"] for one in reminders}, todo, how)
        finished = self._finish(said.get("finish"), open_steps, todo, how)
        return {"asked": True, "closed": closed, "finished": finished}

    def _close_one(self, which: str, why: str, todo: Dict[str, Any], how: str) -> bool:
        from ...common.events.models import utcnow
        task = self.scheduler.store.get(which)
        if task is None or task.status != "pending":
            return False
        metadata = dict(task.metadata or {})
        metadata.update({"closed_by": f"the look after a to-do was {how}", "closed_reason": why[:1000],
                         "closed_at": utcnow().isoformat(),
                         "closed_evidence": [str(todo.get("text") or "")[:300]]})
        self.scheduler.update_task(which, status="closed", lease_token=None, metadata=metadata)
        return True

    def _close(self, entries: Any, shown: set, todo: Dict[str, Any], how: str) -> List[str]:
        out: List[str] = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            which, why = str(entry.get("id") or ""), str(entry.get("why") or "").strip()
            if which in shown and why and self._close_one(which, why, todo, how):
                out.append(which)
        return out

    def _finish(self, entries: Any, shown: List[Dict[str, Any]], todo: Dict[str, Any],
                how: str) -> List[str]:
        from ...heartbeat.states import todo as steps
        from ...heartbeat.states.tasks import LIVE
        known = {(one["work"], one["step"]): one for one in shown}
        out: List[str] = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            work, step = str(entry.get("work") or ""), str(entry.get("step") or "")
            why = str(entry.get("why") or "").strip()
            found = known.get((work, step))
            if found is None or not why:
                continue
            steps.change(self.db, TABLE, LIVE, work,
                         lambda said, step=step, why=why: steps.ticked(said, step, why))
            out.append(step)
            if found["reminder"]:
                self._close_one(found["reminder"], why, todo, how)
        return out


__all__ = ["AfterTodo", "SYSTEM"]
