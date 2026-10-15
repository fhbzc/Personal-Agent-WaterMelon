"""Notification lessons: what the user's own actions on their notifications
say about the notifications they want.

The first kind of lesson on the Lessons panel. Each time the user adds,
edits, disables or deletes an event on the calendar, or marks a notification
as a bad notification, the whole of it is written down - the event as it was,
the notification as it was shown, what was done and when. A model is then
asked, in the background, why the user most likely did it and what the
LESSON is: what an agent scheduling notifications for them should do from
now on. The lessons make one running text, and the agent heartbeat is given
that text - the lessons, not the records - when it decides which
notifications to add.

THE RECORD IS READ ONCE, NOT KEPT. The whole event and notification are
what the model reads to draw the lesson; once it has, only what names the
action - its title - and the user's own words are kept beside the reading
and the lesson. The lesson is what is worth keeping.

AN EDIT IS SHOWN BOTH WAYS. An edit is the user saying what they wanted
INSTEAD of what was there, and that is only readable as a comparison: the
model is given the event whole as it was before the edit, whole as it is
after, and the fields that changed from one value to the other - and draws
the lesson from the difference itself. Nothing here decides what a change
means (a time moved is not labelled "a timing preference" in code); the
model reads the two and says. Once read, the changed fields - before and
after, in words - are kept with the title and the user's words, because
"moved from 09:00 to 14:00" is what names an edit the way a title names a
delete.

A LESSON IS NOT EXPERIENCE. The natures of the user's work are found through
stages, grouped into patterns and principles, and retrieved for review; this
is none of that. It lives in its own two tables, is never grouped, and is
read by exactly one step: the one that schedules notifications.

A LIST OF LESSONS, CHANGED ONLY WHERE AN ACTION CHANGES IT. Each action is
read with every lesson so far, and the model says only what to change: a
lesson to ADD, one to EDIT - most often narrowed with a condition, so that
it and the new one both hold where they used to conflict - or one to DELETE,
each with why. A lesson that still holds is not touched: it is not written
again, so it cannot drift. Every change is kept with what it was before and
after (`calendar_lesson_changes`); nothing is summarised away.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...common.events.models import new_id, utcnow
from ...common.languages import in_display
from ...models.base import ModelRequest

log = logging.getLogger(__name__)

#: What the user did. Each is the user saying something about a
#: notification - that they want one, that they wanted it otherwise, that
#: they do not want this one any more, that it should never have been there,
#: or that it was a bad one.
ACTIONS = ("add", "edit", "disable", "delete", "bad_notification")

#: How much of one changed value is kept, in words, once the record has been
#: let go of - enough to read "from 09:00 to 14:00" or a reworded message,
#: not a second copy of the record.
KEPT_CHANGE_CHARS = 300

#: How long the running text of lessons may be, in characters, before it is
#: summarised. A setting can change it.
DEFAULT_MAX_CHARS = 6000

REASON_PROMPT = """You keep the lessons on how one person wants the notifications an agent
gives them - which they add to their calendar, which they EDIT, which they
switch off, which they delete, and which they mark as a BAD NOTIFICATION -
so that the agent can notify them the way they would want.

You are given ONE ACTION the user just took - what they did and when, and
the complete record of the event and of the notification as it was shown -
and EVERY LESSON SO FAR, each with its id. Reason from the record itself:
who made it, what it says, when it was set for or shown, whether it
repeats, how long it had been there, and, for one the agent made, the
reason the agent gave for making it.

AN EDIT IS THE USER SAYING WHAT THEY WANTED INSTEAD. For an edit you are
given the event whole BEFORE and AFTER, and each field that changed. What
they changed, from what to what - and what they left as it was - is what
they wanted in place of what had been set. An edit that only corrects a
slip (a typo, a stray character) teaches nothing.

WHERE THE USER SAID WHY, THAT IS WHY: draw the lesson from their words.

SAY ONLY WHAT CHANGES IN THE LESSONS. A lesson that still holds is LEFT AS
IT IS - do not list it, do not reword it. Each change is one of:
  {"op": "add", "lesson": "...", "why": "..."}      - something new learned
  {"op": "edit", "id": "...", "lesson": "...", "why": "..."}
                                                  - a lesson that no longer
    holds AS WORDED: its whole new wording. Above all, a CONFLICT between a
    lesson and what this action shows is RESOLVED, not decided for one side:
    narrow the lesson with the condition that tells the cases apart (which
    kind of event, how far ahead, whether its time is settled, who made it,
    what time of day) so that both hold.
  {"op": "delete", "id": "...", "why": "..."}      - ONLY a lesson this
    action shows to be simply wrong, that no condition would save, or one
    now said in full by another.
Each lesson is an instruction for notifications not yet made - concrete:
which kind of notification, about what, how often, at what time of day,
how worded, made by whom; to do, or not to do. One or two sentences
("Do not ...", "Only ...", "Prefer ...").

Answer with JSON only:
{"why": "why they most likely did it - one or two sentences",
 "changes": [ ... ]}
An action that teaches nothing new: "changes": []."""


class CalendarNature:
    """The record of notification actions, and the lessons drawn from them."""

    def __init__(self, db: Any, models: Any = None, config: Any = None,
                 lessons: Any = None) -> None:
        self.db = db
        self.models = models
        self.config = config
        #: THE LESSONS ENGINE (heartbeat/lessons/engine.py): every action is
        #: a piece of its evidence, and the lessons are what it learns -
        #: never drawn from one action alone.
        self.engine = lessons
        # One worker, so actions are reasoned about in the order they were
        # taken and the text is never written by two at once.
        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._again = False

    # -- what the user did ------------------------------------------------
    def record(self, action: str, task: Any = None,
               notification: Any = None, said: str = "",
               after: Any = None) -> Optional[str]:
        """Write down one action, whole, and reason about it in the
        background. `said` is the user's own reason, when they gave one.

        For an edit, `task` is the event as it was BEFORE and `after` the
        event as it is now; both are kept whole, with what changed from one
        to the other, for the model to compare."""
        if action not in ACTIONS:
            return None
        task_row = _as_dict(task)
        notice = _as_dict(notification)
        raw = {"task": task_row or None, "notification": notice or None,
               "user_said": str(said or "").strip()[:2000] or None}
        if action == "edit":
            after_row = _as_dict(after)
            raw["after"] = after_row or None
            raw["changes"] = changes_between(task_row, after_row)
        action_id = new_id("calact")
        try:
            self.db.execute(
                "INSERT INTO calendar_actions (id, acted_at, action, task_id, "
                "event) VALUES (?, ?, ?, ?, ?)",
                (action_id, utcnow().isoformat(), action,
                 str(task_row.get("id") or (notice.get("metadata") or {}).get(
                     "task_id") or ""),
                 json.dumps(raw, ensure_ascii=False, default=str)))
        except Exception:
            log.warning("a notification action could not be recorded",
                        exc_info=True)
            return None
        self.reason_in_background()
        if self.engine is not None:
            try:
                self.engine.record("notifications", action_id,
                                    evidence_text(action, raw), action=action,
                                    reason=raw.get("user_said") or "")
                # THE AGENT CORRECTED: a notification it made on lessons,
                # deleted, disabled, edited or called bad - against them.
                if action != "add" and task_row.get("id"):
                    self.engine.outcome("notifications", str(task_row["id"]),
                                         evidence_text(action, raw)[:600])
            except Exception:
                log.warning("the action could not be learned from", exc_info=True)
        return action_id

    def upgrade(self) -> bool:
        """Once: lessons kept the old way - a running text of entries, and a
        summary of the oldest - become a list, a lesson each: an entry as
        the lesson its action drew, a summary a lesson a line. Kept as it
        was said; each carried over is written down."""
        old = self.db.query(
            "SELECT * FROM calendar_nature WHERE kind IN ('entry', 'summary') "
            "ORDER BY CASE kind WHEN 'summary' THEN 0 ELSE 1 END, created_at, rowid")
        if not old:
            return False
        lessons: List[tuple] = []
        for row in old:
            if row["kind"] == "summary":
                for line in str(row["content"] or "").splitlines():
                    said = line.strip().lstrip("-*\u2022").strip()
                    if said:
                        lessons.append((said, None, row["created_at"]))
                continue
            action = self.db.query_one(
                "SELECT lesson FROM calendar_actions WHERE id = ?", (row.get("action_id"),))
            said = str((action or {}).get("lesson") or "").strip() or _entry_lesson(
                str(row["content"] or ""))
            if said:
                lessons.append((said, row.get("action_id"), row["created_at"]))
        at = utcnow().isoformat()
        with self.db.write() as conn:
            conn.execute("DELETE FROM calendar_nature WHERE kind IN ('entry', 'summary')")
            seen = set()
            for said, action_id, when in lessons:
                key = " ".join(said.lower().split())
                if key in seen:
                    continue
                seen.add(key)
                ident = new_id("calnat")
                conn.execute(
                    "INSERT INTO calendar_nature (id, created_at, kind, content, action_id) "
                    "VALUES (?, ?, 'lesson', ?, ?)", (ident, when or at, said, action_id))
                conn.execute(
                    "INSERT INTO calendar_lesson_changes (id, at, action_id, op, lesson_id, "
                    "before, after, why) VALUES (?, ?, ?, 'carried', ?, '', ?, ?)",
                    (new_id("callch"), at, action_id or "", ident, said,
                     "kept from the running text, as a lesson of its own"))
        log.info("notification lessons: %d carried over as a list", len(seen))
        return True

    def let_go_of_records(self) -> int:
        """Once: records kept whole before they were let go of, trimmed to
        what names them, for every action already read."""
        trimmed = 0
        for row in self.db.query(
                "SELECT id, event FROM calendar_actions "
                "WHERE reasoned_at IS NOT NULL"):
            raw = _raw_of(row.get("event"))
            bare = _bare(raw)
            if raw != bare:
                self.db.execute("UPDATE calendar_actions SET event = ? WHERE id = ?",
                                (json.dumps(bare, ensure_ascii=False), row["id"]))
                trimmed += 1
        return trimmed

    # -- what the user takes back --------------------------------------------
    def forget(self, ident: str) -> bool:
        """Take something back, AT ONCE: a lesson (its id) - rejected, never
        learned again - or an action, and with it the evidence it was."""
        if self.engine is not None:
            if self.engine.rule(ident) is not None:
                self.engine.set_state(ident, "reject")
                return True
            row = self.db.query_one("SELECT id FROM calendar_actions WHERE id = ?", (ident,))
            if row is None:
                return False
            self.engine.forget_source("notifications", ident)
            self.db.execute("DELETE FROM calendar_actions WHERE id = ?", (ident,))
            return True
        with self._lock:
            lesson = self.db.query_one(
                "SELECT * FROM calendar_nature WHERE id = ? AND kind = 'lesson'", (ident,))
            if lesson is not None:
                with self.db.write() as conn:
                    conn.execute("DELETE FROM calendar_nature WHERE id = ?", (ident,))
                    self._log(conn, "", "delete", ident, lesson["content"], "",
                              "deleted by the user")
                return True
            row = self.db.query_one(
                "SELECT id FROM calendar_actions WHERE id = ?", (ident,))
            if row is None:
                return False
            changes = self.db.query(
                "SELECT * FROM calendar_lesson_changes WHERE action_id = ? "
                "ORDER BY at DESC, rowid DESC", (ident,))
            with self.db.write() as conn:
                for change in changes:
                    now = conn.execute("SELECT content FROM calendar_nature WHERE id = ?",
                                       (change["lesson_id"],)).fetchone()
                    if change["op"] in ("add", "carried") and now is not None:
                        conn.execute("DELETE FROM calendar_nature WHERE id = ?",
                                     (change["lesson_id"],))
                    elif change["op"] == "edit" and now is not None \
                            and now[0] == change["after"]:
                        conn.execute("UPDATE calendar_nature SET content = ? WHERE id = ?",
                                     (change["before"], change["lesson_id"]))
                    elif change["op"] == "delete" and now is None:
                        conn.execute(
                            "INSERT INTO calendar_nature (id, created_at, kind, content, "
                            "action_id) VALUES (?, ?, 'lesson', ?, NULL)",
                            (change["lesson_id"], utcnow().isoformat(), change["before"]))
                conn.execute("DELETE FROM calendar_lesson_changes WHERE action_id = ?",
                             (ident,))
                conn.execute("DELETE FROM calendar_actions WHERE id = ?", (ident,))
        return True

    def reason_in_background(self) -> None:
        """Learning from the actions is the lessons engine's now: it is only
        asked to go on. Without one, each action is reasoned about alone, as
        it was before."""
        if self.engine is not None:
            return                     # learned with the state heartbeat's round
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                # The worker running now picks this one up before it stops.
                self._again = True
                return
            self._worker = threading.Thread(
                target=self._work, name="notification-lessons", daemon=True)
            self._worker.start()

    def _work(self) -> None:
        while True:
            with self._lock:
                self._again = False
            try:
                self.reason_pending()
            except Exception:
                log.warning("reasoning about notification actions failed",
                            exc_info=True)
            with self._lock:
                if not self._again:
                    self._worker = None
                    return

    def wait(self, timeout: float = 30.0) -> None:
        """For tests: until the background reasoning has finished."""
        worker = self._worker
        if worker is not None:
            worker.join(timeout)
        if self.engine is not None:
            self.engine.wait(timeout)

    # -- the reasoning ----------------------------------------------------
    def pending(self) -> List[Dict[str, Any]]:
        if self.engine is not None:
            return []                  # the engine keeps its own
        return [dict(row) for row in self.db.query(
            "SELECT * FROM calendar_actions WHERE reasoned_at IS NULL "
            "ORDER BY acted_at, rowid")]

    def reason_pending(self) -> int:
        """Each action not yet reasoned about, oldest first. One the model
        could not answer stays pending and is tried again with the next."""
        done = 0
        for row in self.pending():
            read = self._why(row)
            if read is None:
                break
            self._add(row, *read)
            done += 1
        return done

    def _why(self, row: Dict[str, Any]) -> Optional[tuple]:
        """(why, changes) for one action, or None when the model could not
        be asked."""
        if self.models is None:
            return None
        raw = _raw_of(row.get("event"))
        lessons = self.lessons()
        asked = "\n".join([
            "THE ACTION",
            f"What the user did: {_verb(row['action'])}",
            *([f"What they changed: {line}" for line in _change_lines(raw)]
              if row["action"] == "edit" else []),
            f"When they did it: {_local(row['acted_at'])}",
            f"Why, in their own words: {raw.get('user_said')}"
            if raw.get("user_said") else "They did not say why.",
            "",
            _record_text(raw),
            "",
            "EVERY LESSON SO FAR, each with its id",
            "\n".join(f"[{one['id']}] {one['content']}" for one in lessons)
            or "None yet - this is the first.",
        ])
        try:
            response = self.models.invoke(
                ModelRequest(
                    messages=[{"role": "user", "content": asked}],
                    system=REASON_PROMPT,
                    max_tokens=1500,
                    task="notification_lessons",
                    language=in_display(),
                    metadata={"context_summary":
                              f"why the user {_verb(row['action'])}: "
                              f"{_title_of(raw)}"[:2000]},
                ),
                role="heartbeat",
            )
        except Exception as exc:
            log.info("why a notification action was taken could not be "
                     "asked: %s", exc)
            return None
        text = (response.text or "").strip()
        answered = _json_of(text)
        if not answered:
            # Not JSON: nothing is changed on a reading that cannot be read -
            # it is kept as the why, and said so in the log.
            log.warning("the notification lesson answer was not JSON: %r", text[:200])
            return (text, []) if text else None
        why = str(answered.get("why") or "").strip()
        changes = answered.get("changes")
        if changes is None and str(answered.get("lesson") or "").strip():
            # An answer in the old form: one lesson, added.
            changes = [{"op": "add", "lesson": answered["lesson"], "why": why}]
        changes = [one for one in (changes or []) if isinstance(one, dict)]
        return (why, changes) if (why or changes) else None

    def _add(self, row: Dict[str, Any], why: str, changes: Any = ()) -> None:
        """The action read: its changes made to the lessons, each written
        down with what it was before and after; what is not changed is
        left exactly as it was. A change that names no lesson there is,
        or says nothing, is left out."""
        if isinstance(changes, str):
            # Called the old way, with one lesson.
            changes = [{"op": "add", "lesson": changes, "why": why}] if changes else []
        known = {one["id"]: one["content"] for one in self.lessons()}
        made: List[str] = []
        at = utcnow().isoformat()
        with self.db.write() as conn:
            for change in changes or []:
                op = str(change.get("op") or "").strip().lower()
                said = " ".join(str(change.get("lesson") or "").split())
                because = str(change.get("why") or "").strip()
                ident = str(change.get("id") or "").strip()
                if op == "add" and said:
                    if " ".join(said.lower().split()) in {
                            " ".join(v.lower().split()) for v in known.values()}:
                        continue
                    ident = new_id("calnat")
                    conn.execute(
                        "INSERT INTO calendar_nature (id, created_at, kind, content, "
                        "action_id) VALUES (?, ?, 'lesson', ?, ?)", (ident, at, said, row["id"]))
                    self._log(conn, row["id"], "add", ident, "", said, because)
                    known[ident] = said
                    made.append(f"Added: {said}")
                elif op == "edit" and ident in known and said and said != known[ident]:
                    conn.execute("UPDATE calendar_nature SET content = ? WHERE id = ?",
                                 (said, ident))
                    self._log(conn, row["id"], "edit", ident, known[ident], said, because)
                    made.append(f"Changed: {known[ident]} \u2192 {said}")
                    known[ident] = said
                elif op == "delete" and ident in known and because:
                    # Never without a reason.
                    conn.execute("DELETE FROM calendar_nature WHERE id = ?", (ident,))
                    self._log(conn, row["id"], "delete", ident, known[ident], "", because)
                    made.append(f"Removed: {known[ident]}")
                    known.pop(ident)
            # Read, so the record is let go of: only its title and the
            # user's own words stay.
            conn.execute("UPDATE calendar_actions SET event = ? WHERE id = ?",
                         (json.dumps(_bare(_raw_of(row.get("event"))),
                                     ensure_ascii=False), row["id"]))
            conn.execute(
                "UPDATE calendar_actions SET reasoned_at = ?, why = ?, lesson = ? "
                "WHERE id = ?", (at, why, "\n".join(made), row["id"]))

    @staticmethod
    def _log(conn: Any, action_id: str, op: str, lesson_id: str, before: str,
             after: str, why: str) -> None:
        conn.execute(
            "INSERT INTO calendar_lesson_changes (id, at, action_id, op, lesson_id, "
            "before, after, why) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id("callch"), utcnow().isoformat(), action_id or "", op, lesson_id,
             before or "", after or "", why or ""))

    def adopt(self, lessons: Any, why: str = "loaded from a lessons package") -> int:
        """Lessons from elsewhere (a lessons package), each added unless one
        here already says it - to the engine, as lessons in force."""
        if self.engine is not None:
            known = {" ".join(one["statement"].lower().split())
                     for one in self.engine.rules("notifications", ("candidate", "active"))}
            added = 0
            for said in lessons or []:
                said = " ".join(str(said or "").split())
                if said and said.lower() not in known:
                    known.add(said.lower())
                    self.engine._new_rule("notifications", {"statement": said}, why,
                                           state="active")
                    added += 1
            return added
        known = {" ".join(one["content"].lower().split()) for one in self.lessons()}
        added = 0
        at = utcnow().isoformat()
        with self.db.write() as conn:
            for said in lessons or []:
                said = " ".join(str(said or "").split())
                key = said.lower()
                if not said or key in known:
                    continue
                known.add(key)
                ident = new_id("calnat")
                conn.execute(
                    "INSERT INTO calendar_nature (id, created_at, kind, content, action_id) "
                    "VALUES (?, ?, 'lesson', ?, NULL)", (ident, at, said))
                self._log(conn, "", "add", ident, "", said, why)
                added += 1
        return added

    # -- what is known ----------------------------------------------------
    def max_chars(self) -> int:
        try:
            value = int(self.config.get("calendar_nature_max_chars",
                                        DEFAULT_MAX_CHARS))
        except (AttributeError, TypeError, ValueError):
            value = DEFAULT_MAX_CHARS
        return max(1000, value)

    def lessons(self) -> List[Dict[str, Any]]:
        """Every lesson in force, oldest first."""
        if self.engine is not None:
            return [{"id": one["id"], "created_at": one["created_at"],
                     "content": one["statement"], "action_id": None}
                    for one in self.engine.rules("notifications", ("active",))]
        return [dict(row) for row in self.db.query(
            "SELECT id, created_at, content, action_id FROM calendar_nature "
            "WHERE kind = 'lesson' ORDER BY created_at, rowid")]

    def text(self) -> str:
        """The lessons, as the agent is given them: those the engine has
        learned, each with its id and how sure - never the raw actions."""
        engine = self.engine
        if engine is not None:
            return engine.text("notifications")
        return "\n".join(f"- {one['content']}" for one in self.lessons())

    def summary(self) -> str:
        """Nothing is summarised any more: the lessons are kept a line each."""
        return ""

    def changes(self, action_id: str = "") -> List[Dict[str, Any]]:
        """What was changed in the lessons - by one action, or all of it."""
        if action_id:
            return [dict(row) for row in self.db.query(
                "SELECT * FROM calendar_lesson_changes WHERE action_id = ? "
                "ORDER BY at, rowid", (action_id,))]
        return [dict(row) for row in self.db.query(
            "SELECT * FROM calendar_lesson_changes ORDER BY at DESC, rowid DESC LIMIT 200")]

    def describe(self, limit: int = 500) -> Dict[str, Any]:
        """For the Lessons panel: the running text, and every action it
        rests on with its complete record, newest first."""
        counts = self.db.query_one(
            "SELECT COUNT(*) AS n, SUM(reasoned_at IS NULL) AS waiting "
            "FROM calendar_actions")
        lessons = []
        for row in self.db.query(
                "SELECT id, acted_at, action, task_id, event, reasoned_at, why, "
                "lesson FROM calendar_actions ORDER BY acted_at DESC, rowid DESC "
                "LIMIT ?", (int(limit),)):
            raw = _raw_of(row.get("event"))
            lessons.append({**row, "event": raw, "title": _title_of(raw),
                            "did": _done(row["action"]),
                            # An edit says what it changed, from what to
                            # what - from the whole record while it is kept,
                            # and from what is kept of it after.
                            "changes": _change_lines(raw)})
        engine = self.engine
        if engine is not None:
            learned = engine.describe("notifications")
            read = learned.pop("read")
            for one in lessons:
                one["read"] = read.get(one["id"]) or []
            return {"kind": "notifications", "title": "Notification lessons",
                    "text": self.text(), "engine": learned,
                    "actions": int((counts or {}).get("n") or 0),
                    "waiting": learned["waiting"], "lessons": lessons}
        return {"kind": "notifications", "title": "Notification lessons",
                "text": self.text(), "summary": self.summary(),
                # The lessons in force, a line each, each one its own to delete.
                "list": [{"id": one["id"], "text": one["content"],
                          "since": one["created_at"]} for one in self.lessons()],
                "max_chars": self.max_chars(),
                "actions": int((counts or {}).get("n") or 0),
                "waiting": int((counts or {}).get("waiting") or 0),
                "lessons": lessons}


# -- helpers --------------------------------------------------------------
def _entry_lesson(content: str) -> str:
    """The lesson in an entry of the old running text: its first line, the
    guidance, without the words on what it was learned from."""
    first = str(content or "").strip().splitlines()[0] if str(content or "").strip() else ""
    return first.lstrip("-*• ").split(" (learned ")[0].strip()


def lesson_text(row: Dict[str, Any], lesson: str) -> str:
    """One lesson as the agent is given it: the guidance, and in a few words
    what it was learned from - never the record itself."""
    raw = _raw_of(row.get("event"))
    when = _local(row["acted_at"]).rsplit(" ", 1)[0]
    return (f"- {lesson} (learned when the user {_verb(row['action'])} "
            f"\"{_title_of(raw)}\", {when})")


def _as_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    return dict(value) if isinstance(value, dict) else {}


def evidence_text(action: str, raw: Dict[str, Any]) -> str:
    """One action, as a piece of evidence: what the user did, and the event
    as it read - without the complete record."""
    whole = _record_text(raw)
    readable = whole.split("THE COMPLETE RECORD", 1)[0].strip()
    said = raw.get("user_said")
    return (f"The user {_verb(action)}.\n{readable}"
            + (f"\nThe user said: {said}" if said else ""))[:2000]


def _record_text(raw: Dict[str, Any]) -> str:
    """The record, readable first and then whole. An edit's is the event
    before, the event after, and what changed - side by side, so the two can
    be compared rather than one of them remembered."""
    task = raw.get("task") or {}
    notice = raw.get("notification") or {}
    lines = []
    if "after" in raw:
        changed = _change_lines(raw)
        lines += _event_lines(task, "THE EVENT BEFORE THE EDIT")
        lines += _event_lines(raw.get("after") or {}, "THE EVENT AFTER THE EDIT")
        lines += (["WHAT CHANGED"] + [f"- {line}" for line in changed]
                  + ([] if changed else ["- (nothing)"]) + [""])
    elif task:
        lines += _event_lines(task, "THE EVENT")
    if notice:
        lines += ["THE NOTIFICATION AS IT WAS SHOWN",
                  f"Title: {notice.get('title') or '(none)'}",
                  f"Message: {notice.get('message') or '(none)'}",
                  f"Shown: {_local(notice.get('created_at'))}",
                  ""]
    lines += ["THE COMPLETE RECORD",
              json.dumps(raw, ensure_ascii=False, indent=1, sort_keys=True,
                         default=str)]
    return "\n".join(lines)


def _event_lines(task: Dict[str, Any], heading: str) -> List[str]:
    """One event, whole and readable: what it says, when (in the user's own
    time), how it repeats in words, what kind, who made it and why."""
    if not task:
        return []
    payload = task.get("task_payload") or {}
    lines = [heading, f"Title: {task.get('title') or '(none)'}"]
    if payload.get("message"):
        lines.append(f"Message the notification shows: {payload['message']}")
    if task.get("description"):
        lines.append(f"Detail: {task['description']}")
    lines.append(f"Kind: {task.get('execution_type') or 'notification'}"
                 + (f" ({payload['template']})" if payload.get("template") else ""))
    if task.get("scheduled_at"):
        lines.append(f"Set for: {_local(task['scheduled_at'])} (the user's time)"
                     + (f", timezone {task['timezone_name']}"
                        if task.get("timezone_name") else ""))
    lines.append(f"Repeats: {_repeat_words(task.get('recurrence'))}"
                 + (f" ({task['runs']} run(s) so far)" if task.get("runs") else ""))
    if task.get("status"):
        lines.append(f"Status: {task['status']}")
    lines.append(f"Made by: {_made_by(task)}"
                 + (f", on {_local(task['created_at'])}"
                    if task.get("created_at") else ""))
    if task.get("reason"):
        lines.append(f"Why {'the user' if task.get('created_by') == 'user' else 'the agent'}"
                     f" made it: {task['reason']}")
    lines.append(f"Interrupts the user when it runs: "
                 f"{'yes' if payload.get('notify_user') else 'no'}")
    lines.append("")
    return lines


def _repeat_words(rule: Any) -> str:
    """How an event repeats, as a person says it."""
    from ...scheduler.core.recurrence import describe

    if not rule or not isinstance(rule, dict):
        return "does not repeat - runs once"
    rule = dict(rule)
    if isinstance(rule.get("until"), str):
        try:
            rule["until"] = datetime.fromisoformat(
                rule["until"].replace("Z", "+00:00"))
        except ValueError:
            rule.pop("until")
    try:
        return describe(rule)
    except (TypeError, ValueError, AttributeError):
        return json.dumps(rule, default=str)


def _message_of(task: Dict[str, Any]) -> str:
    """The words a notification shows."""
    payload = task.get("task_payload") or {}
    return str(payload.get("message") or task.get("description") or "")


def changes_between(before: Dict[str, Any],
                    after: Dict[str, Any]) -> List[Dict[str, str]]:
    """What an edit changed, field by field, each value in words. Facts only
    - what it was and what it is - never what the change is taken to mean;
    that is the model's to say."""
    if not before or not after:
        return []
    readings = (
        ("title", lambda t: str(t.get("title") or "")),
        ("message", _message_of),
        ("time", lambda t: _local(t.get("scheduled_at"))
         if t.get("scheduled_at") else ""),
        ("repeat", lambda t: _repeat_words(t.get("recurrence"))),
    )
    changes = []
    for field_name, read in readings:
        was, now = read(before), read(after)
        if was != now:
            changes.append({"field": field_name, "before": was, "after": now})
    return changes


def _change_lines(raw: Dict[str, Any]) -> List[str]:
    """Each change as one line: the field, what it was, what it is."""
    lines = []
    for change in raw.get("changes") or []:
        if isinstance(change, dict):
            lines.append(f"{change.get('field')}: \"{change.get('before') or ''}\""
                         f" -> \"{change.get('after') or ''}\"")
    return lines


def _title_of(raw: Dict[str, Any]) -> str:
    """What names the event: for an edit, what it is called now."""
    task = raw.get("task") or {}
    after = raw.get("after") or {}
    notice = raw.get("notification") or {}
    return str(after.get("title") or task.get("title") or notice.get("title")
               or "an event")


def _made_by(task: Dict[str, Any]) -> str:
    by = str(task.get("created_by") or "")
    return {"user": "the user", "heartbeat": "the agent heartbeat",
            "scheduler": "the scheduler"}.get(by, "the agent")


def _bare(raw: Dict[str, Any]) -> Dict[str, Any]:
    """What is kept of a record once it has been read: its title, and what
    the user said."""
    task = raw.get("task") or {}
    notice = raw.get("notification") or {}
    bare = {"task": {"title": task.get("title")} if task else None,
            "notification": {"title": notice.get("title")} if notice else None,
            "user_said": raw.get("user_said") or None}
    if "after" in raw:
        # An edit is named by what it changed: the fields, before and after,
        # in words and cut short - not the two events again.
        after = raw.get("after") or {}
        bare["after"] = {"title": after.get("title")} if after else None
        bare["changes"] = [
            {"field": str(change.get("field") or ""),
             "before": str(change.get("before") or "")[:KEPT_CHANGE_CHARS],
             "after": str(change.get("after") or "")[:KEPT_CHANGE_CHARS]}
            for change in raw.get("changes") or [] if isinstance(change, dict)]
    return bare


def _done(action: str) -> str:
    """What the user did, as the Lessons panel names it - by the user, and
    so not to be read as the panel's own Delete."""
    return {"add": "Added by user", "edit": "Edited by user",
            "disable": "Disabled by user",
            "delete": "Deleted by user",
            "bad_notification": "Marked bad by user"}.get(action, action)


def _verb(action: str) -> str:
    return {"add": "added this event", "edit": "edited this event",
            "disable": "disabled this event",
            "delete": "deleted this event",
            "bad_notification": "marked this as a bad notification"}.get(
        action, action)


def _local(value: Any) -> str:
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return str(value or "")
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone().strftime("%a %d %b %Y %H:%M")


def _raw_of(value: Any) -> Dict[str, Any]:
    try:
        loaded = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    if "task" in loaded or "notification" in loaded:
        return loaded
    # Written before the record was kept whole: the event alone.
    return {"task": loaded or None, "notification": None}


def _json_of(text: str) -> Optional[Dict[str, Any]]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        found = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


__all__ = ["CalendarNature", "ACTIONS", "DEFAULT_MAX_CHARS", "lesson_text",
           "changes_between"]
