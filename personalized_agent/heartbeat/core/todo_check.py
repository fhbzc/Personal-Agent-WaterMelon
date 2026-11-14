"""THE TO-DO LIST, LOOKED OVER (the user, 2026-11-09: "允许模型suggest代办已做完，模型不能直接删to do,
但是suggest之后，那个to do会出现一个mark as done按钮，让用户关闭更快。模型的suggest finished可以被用户
override, override之后下一次要suggest finished必须必用户上次override之后有明显progress才能关(这个suggest
finish也在heartbeat里做)").

A to-do stayed open for a reply sent days before, a page put online a week before; and it said
"学姐" where the contacts knew who. Every three hours at most (the user, 2026-11-09), and once
before the day ahead is written, the open to-dos with something new about them - new on the list,
or with activity found about them since the last look; the rest are not sent again - are shown to
the model with what was found about each since it was put on the list - activity, chat and the
people who may be meant (related.py) - and the model says:

- which LOOK DONE, with why and the lines that show it. Never done by it: the to-do offers Mark as
  done, and the user may say it is not (`TodoList.decline`). One they said is not done is said to
  look done again only on a line read AFTER they said so - held so by this code, not left to the
  model's word.
- which NAME SOMEONE BY A ROLE (学姐, "a junior colleague") where the people make it plain who:
  its line said again with the name. Once: its first words are kept.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional

from ...common.events.models import utcnow

log = logging.getLogger(__name__)

#: How often at most, and the kv keys of when it last looked and at what.
EVERY_MINUTES = 180
AT_KEY, SEEN_KEY = "todo_check.at", "todo_check.seen"
#: + a to-do's id: the agent's search words for it, and what they were written for.
WORDS_KEY = "todo_check.words."
MOST_TODOS = 30

SYSTEM = """You look over the user's to-do list. Each open to-do is given with what was found about
it since it was put on the list: the user's activity as the agent read it (their mail, chats and
pages among it), what was said in the chat with the agent, and the people who may be meant.

1. LOOKS DONE. Say a to-do looks done only when what was found plainly shows the thing itself done
   - the reply sent, the page put online, the meeting set - not that it was talked about, planned
   or started. Give why, in one sentence written to the user, and the ids of the lines that show
   it. A to-do with "they_said_not_done" was said to look done before and the user said it is not:
   say it again only on a line dated after that moment that shows it done. Most of the time none
   look done: then say none.

2. WHO IT IS ABOUT. A to-do that names someone only by a role or a word of address - 学姐, 学长,
   "a junior colleague", "their advisor" - where the people listed and what was found make it plain
   who is meant: give its line again with their name as the contacts have it, nothing else
   changed, in the to-do's own language. Not when it could be more than one of them.

Answer in JSON only:
{"done": [{"id": "the to-do's id", "why": "one sentence", "shown_by": ["state_... or msg_..."]}],
 "rename": [{"id": "the to-do's id", "text": "its line, the name in it"}]}"""


class TodoCheck:
    def __init__(self, db: Any, todos: Any, models: Callable[[], Any], config: Any = None) -> None:
        self.db = db
        self.todos = todos
        self._models = models
        self.config = config

    # -- when -----------------------------------------------------------------------------
    def _kv(self, key: str) -> str:
        row = self.db.query_one("SELECT value FROM kv WHERE key = ?", (key,))
        return str(row["value"]) if row and row["value"] else ""

    def _set(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE "
                        "SET value = excluded.value", (key, value))

    def _newest_read(self) -> str:
        # The newest and how many: two written in one tick of the clock share a stamp.
        row = self.db.query_one("SELECT MAX(created_at) AS at, COUNT(*) AS n FROM states "
                                "WHERE about = 'user'")
        return f"{row['at']}|{row['n']}" if row and row["at"] else ""

    def due(self, now: Any = None) -> bool:
        """Every EVERY_MINUTES at most, and only when something was read since it last looked."""
        now = now or utcnow()
        last = self._kv(AT_KEY)
        if last:
            try:
                from datetime import datetime
                if now - datetime.fromisoformat(last) < timedelta(minutes=EVERY_MINUTES):
                    return False
            except ValueError:
                pass
        newest = self._newest_read()
        return bool(newest) and newest != self._kv(SEEN_KEY)

    # -- the look ------------------------------------------------------------------------
    def shown(self, changed_since: str = "") -> List[Dict[str, Any]]:
        """Each open to-do not already suggested, with what was found about it - and, given
        `changed_since`, only those new since then or with something found since then: what
        nothing new is about is not sent again."""
        from . import related
        out = []
        todos = []
        for one in self.todos.open()[:MOST_TODOS]:
            if one.get("suggested_at"):
                continue
            reason = ""
            if one.get("task_id"):
                row = self.db.query_one("SELECT reason, description FROM scheduled_tasks WHERE id = ?",
                                        (one["task_id"],))
                reason = f"{row['description'] or ''} {row['reason'] or ''}" if row else ""
            todos.append((one, f"{one['text']} {one.get('detail') or ''} {reason}"))
        words = self._search_words(todos)
        for one, about in todos:
            found = related.context(self.db, about, one["created_at"], words=words.get(one["id"], []))
            # By when each was WRITTEN, to the second: a clock that gives two moments one stamp
            # makes "made before the last look" one of "made at it".
            if changed_since and one["created_at"] <= changed_since and not any(
                    line.get("written", line["at"]) > changed_since[:19]
                    for line in found["activity_about_it"] + found["said_in_the_chat"]):
                continue                     # nothing new about it since the last look
            out.append({"id": one["id"], "text": one["text"], "detail": one.get("detail") or "",
                        "put_on_the_list": one["created_at"][:16],
                        **({"they_said_not_done": one["declined_at"][:16]}
                           if one.get("declined_at") else {}),
                        **({"renamed_already": True} if one.get("renamed_from") else {}),
                        **found})
        return out

    def _search_words(self, todos: List[Any]) -> Dict[str, List[str]]:
        """THE AGENT'S OWN SEARCH WORDS for each (related.search_words), kept with what they were
        written for and written again only when a to-do's words change - one call for all new."""
        import hashlib
        from . import related
        out: Dict[str, List[str]] = {}
        wanted = []
        for one, about in todos:
            key = hashlib.sha1(about.encode("utf-8")).hexdigest()[:16]
            try:
                kept = json.loads(self._kv(f"{WORDS_KEY}{one['id']}") or "{}")
            except ValueError:
                kept = {}
            if kept.get("for") == key:
                out[one["id"]] = list(kept.get("words") or [])
            else:
                wanted.append((one["id"], about, key))
        if wanted:
            said = related.search_words(self._models(), [{"id": ident, "text": about}
                                                         for ident, about, _key in wanted],
                                        "todo_check_words")
            for ident, _about, key in wanted:
                out[ident] = said.get(ident, [])
                if ident in said:
                    self._set(f"{WORDS_KEY}{ident}", json.dumps({"for": key, "words": out[ident]},
                                                                ensure_ascii=False))
        return out

    def run(self, now: Any = None) -> Dict[str, Any]:
        now = now or utcnow()
        last = self._kv(AT_KEY)
        self._set(AT_KEY, now.isoformat())
        self._set(SEEN_KEY, self._newest_read())
        shown = self.shown(changed_since=last)
        if not shown:
            return {"asked": False}
        from ...common.languages import in_english
        from ...heartbeat.states.heartbeat import _as_json
        from ...models.base import ModelRequest
        handed = shown
        response = self._models().invoke(ModelRequest(
            messages=[{"role": "user", "content": json.dumps(
                {"now": now.isoformat()[:16], "open_todos": handed}, ensure_ascii=False,
                indent=1)[:80000]}],
            system=SYSTEM, max_tokens=2000, task="todo_check",
            language=in_english("why each looks done")), role="heartbeat")
        said = _as_json(getattr(response, "text", "") or "") or {}
        said = said if isinstance(said, dict) else {}
        return {"asked": True, "suggested": self._suggest(said.get("done"), shown),
                "renamed": self._rename(said.get("rename"), shown)}

    def _suggest(self, entries: Any, shown: List[Dict[str, Any]]) -> List[str]:
        known = {one["id"]: one for one in shown}
        out = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            one = known.get(str(entry.get("id") or ""))
            why = " ".join(str(entry.get("why") or "").split())
            if one is None or not why:
                continue
            lines = {line["id"]: line["at"] for line in one["activity_about_it"]}
            lines.update({line["id"]: line["at"] for line in one["said_in_the_chat"]})
            shown_by = [str(ident) for ident in entry.get("shown_by") or [] if str(ident) in lines]
            if not shown_by:
                continue                    # what it rests on must be what it was shown
            # TURNED DOWN BEFORE: only on clear progress since - a line read after they said so.
            if one.get("they_said_not_done") and not any(
                    lines[ident] > one["they_said_not_done"] for ident in shown_by):
                continue
            if self.todos.suggest(one["id"], why, shown_by) is not None:
                out.append(one["id"])
        return out

    def _rename(self, entries: Any, shown: List[Dict[str, Any]]) -> List[str]:
        known = {one["id"]: one for one in shown}
        out = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            one = known.get(str(entry.get("id") or ""))
            text = " ".join(str(entry.get("text") or "").split())
            names = [person["name"] for person in (one or {}).get("people_who_may_be_meant") or []]
            # Once, and only to a name it was shown.
            if one is None or one.get("renamed_already") or not any(
                    name and name in text for name in names):
                continue
            if self.todos.rename(one["id"], text) is not None:
                out.append(one["id"])
        return out


__all__ = ["TodoCheck", "SYSTEM", "EVERY_MINUTES"]
