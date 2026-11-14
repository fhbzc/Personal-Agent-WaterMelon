"""PHASE 2 - THE USER'S WORD ON THE PLAN, AND THE PLAN'S REMINDERS (the user, 2026-11-09: past the
preview, "继续").

THE USER'S WORD (`Feedback`), from the calendar's buttons and from the chat (`add_tools`):
  done        done - a to-do of theirs ticked as they would tick it on their list
  later_today not in the hour now - later today
  tomorrow    not before tomorrow
  on_day      not before a day they name
  to_agent    the agent's to do (delegable)          mine   theirs (human)
  more_time   thirty minutes more than it was given
  drop        no longer to be done - a to-do of theirs removed as they would remove it
Each is kept (agenda_feedback - the reviewer's cases of how they change a plan), is a lesson, and
the day is planned again around it.

THE PLAN'S REMINDERS (`Reminders`, the user's choice of four, 2026-11-09):
  at each block's start      what, until when, its work note; a meeting or errand early enough
                             for the way there (a travel block of its own says it, else LEAD_MIN)
  approvals                  when a block of them begins, what is to be approved, together
  held in deep work          `quiet_now` - the Monitor's blockers hold the other reminders while a
                             deep block runs, until its end or the next batch, as the plan says
  a break                    when the stretch since the last break reaches their usual one
Each goes through what holds the rest ("through"): it is the plan's own word on the time.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from . import items as gathered
from . import triage as triaging

log = logging.getLogger(__name__)

ACTIONS = ("done", "later_today", "tomorrow", "on_day", "to_agent", "mine", "more_time", "drop")
#: Later today: not within this of now. A meeting or errand told this early when no travel block
#: comes before it.
LATER_TODAY_MIN = 60
LEAD_MIN = 10
#: Told within this after a block began (a tick missed is not a reminder lost).
LATE_MIN = 5
#: A thing to approve, told (in its block's reminder, or late): never told twice.
APPROVE_TOLD = "agenda.approve_told."
#: A thing of the plan handed to the agent (heartbeat/agenda/execute.py WORK_KEY).
WORK_KEY = "agenda.work."
#: A thing waiting for the user's answer, asked (once).
ASKED = "agenda.asked."


def _now_local() -> datetime:
    return datetime.now().astimezone().replace(tzinfo=None, second=0, microsecond=0)


from .execute import TOLD_DONE, question_for, waits_for_answer  # noqa: E402

def twins(db: Any) -> Dict[str, set]:
    """THE SAME THING ON THE PLATE MORE THAN ONCE - one thing (the user, 2026-11-13: "你就合成为一条，这样一次性关掉"):
    the open things the sorting said are the same ("same_as") or written word for word alike, each
    with the others of its group (itself among them). Those not twinned: not in it."""
    rows = db.query("SELECT id, title, triage FROM agenda_items WHERE status = 'open'")
    parent = {row["id"]: row["id"] for row in rows}

    def root(ident: str) -> str:
        while parent[ident] != ident:
            parent[ident] = parent[parent[ident]]
            ident = parent[ident]
        return ident

    def join(one: str, other: str) -> None:
        parent[root(one)] = root(other)
    by_words: Dict[str, str] = {}
    for row in rows:
        try:
            same = str((json.loads(row["triage"] or "{}") or {}).get("same_as") or "")
        except ValueError:
            same = ""
        if same in parent and same != row["id"]:
            join(row["id"], same)
        words = " ".join(str(row["title"] or "").lower().split())
        if words:
            if words in by_words:
                join(row["id"], by_words[words])
            else:
                by_words[words] = row["id"]
    groups: Dict[str, set] = {}
    for ident in parent:
        groups.setdefault(root(ident), set()).add(ident)
    return {ident: members for members in groups.values() if len(members) > 1 for ident in members}


class Feedback:
    """The user's word on one thing of the plan."""

    def __init__(self, agenda: Any, lessons: Optional[Callable[[str, str], Any]] = None,
                 todo_done: Optional[Callable[[str], Any]] = None,
                 todo_removed: Optional[Callable[[str], Any]] = None) -> None:
        self.agenda = agenda
        self.db = agenda.db
        self.lessons = lessons
        self.todo_done = todo_done
        self.todo_removed = todo_removed

    def apply(self, ident: str, action: str, day: str = "", by: str = "page",
              now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        if action not in ACTIONS:
            return None
        row = self.db.query_one("SELECT * FROM agenda_items WHERE id = ?", (ident,))
        if not row:
            return None
        now = now or _now_local()
        said = json.loads(row["triage"] or "{}")
        said["at"] = datetime.now(timezone.utc).isoformat()      # to be placed again (adjust.py)
        detail = ""
        closing = ""
        if action == "done":
            if row["source"] == "todo" and self.todo_done is not None:
                self.todo_done(row["ref_id"])
            closing = "done"
        elif action == "drop":
            if row["source"] == "todo" and self.todo_removed is not None:
                self.todo_removed(row["ref_id"])
            closing = "dropped"
        elif action == "later_today":
            said["user_after"] = (now + timedelta(minutes=LATER_TODAY_MIN)).strftime("%Y-%m-%d %H:%M")
            detail = said["user_after"]
        elif action in ("tomorrow", "on_day"):
            on = (now.date() + timedelta(days=1)) if action == "tomorrow" else _day(day)
            if on is None:
                return None
            said["user_not_before"] = on.isoformat()
            said.pop("user_after", None)
            detail = on.isoformat()
        elif action in ("to_agent", "mine"):
            who = "delegable" if action == "to_agent" else "human"
            said["who"] = said["set_by_user"] = who
            detail = who
        elif action == "more_time":
            said["user_minutes"] = int(said.get("minutes") or 30) + 30
            said["minutes"] = said["user_minutes"]
            detail = f"{said['user_minutes']} min"
        if closing:
            # ITS TWINS WITH IT - the same thing, closed at once (2026-11-13: "这样一次性关掉").
            others = sorted(twins(self.db).get(ident, set()) - {ident})
            said["user_state"] = closing
            self.db.execute("UPDATE agenda_items SET triage = ?, status = 'closed', closed_at = ?, "
                            "updated_at = ? WHERE id = ?",
                            (json.dumps(said, ensure_ascii=False), said["at"], said["at"], ident))
            for other in others:
                self._close_twin(other, closing, action, ident, by)
        else:
            self.db.execute("UPDATE agenda_items SET triage = ?, updated_at = ? WHERE id = ?",
                            (json.dumps(said, ensure_ascii=False), said["at"], ident))
        latest = self.agenda.latest(now.date()) or {}
        self.db.execute("INSERT INTO agenda_feedback (id, at, item_id, title, action, detail, plan_id, by) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (f"fb_{uuid.uuid4().hex[:12]}", said["at"], ident, row["title"], action, detail,
                         latest.get("id") or "", by))
        # ONLY THE USER'S OWN ACTIONS ARE LESSONS: what the agent found done already (agent/approvals.py,
        # by "agent_...") is not one.
        agent = str(by).startswith("agent")
        if not agent:
            self._lesson(row["title"], action, detail, said)
        self.agenda.plan_again(f"{'the agent' if agent else 'the user'}: {action} - {row['title'][:80]}")
        return {"id": ident, "action": action, "detail": detail}

    def _close_twin(self, ident: str, closing: str, action: str, of: str, by: str) -> None:
        """One of a thing's twins, closed with it - its to-do too; kept as the agent's doing, no lesson."""
        row = self.db.query_one("SELECT * FROM agenda_items WHERE id = ? AND status = 'open'", (ident,))
        if not row:
            return
        if row["source"] == "todo":
            told = self.todo_done if closing == "done" else self.todo_removed
            if told is not None:
                try:
                    told(row["ref_id"])
                except Exception:
                    log.warning("the twin's to-do could not be closed", exc_info=True)
        said = json.loads(row["triage"] or "{}")
        said["at"] = datetime.now(timezone.utc).isoformat()
        said["user_state"] = closing
        said["closed_with"] = of
        self.db.execute("UPDATE agenda_items SET triage = ?, status = 'closed', closed_at = ?, updated_at = ? "
                        "WHERE id = ?", (json.dumps(said, ensure_ascii=False), said["at"], said["at"], ident))
        self.db.execute("INSERT INTO agenda_feedback (id, at, item_id, title, action, detail, plan_id, by) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (f"fb_{uuid.uuid4().hex[:12]}", said["at"], ident, row["title"], action,
                         f"the same as {of}", "", "agent_twin_of_" + str(by)))

    def _lesson(self, title: str, action: str, detail: str, said: Dict[str, Any]) -> None:
        if self.lessons is None:
            return
        words = {"done": "marked done", "drop": "let go of", "later_today": "put off to later today",
                 "tomorrow": "put off to tomorrow", "on_day": f"put off to {detail}",
                 "to_agent": "gave to the agent", "mine": "took back to do themselves",
                 "more_time": f"gave more time ({detail})"}[action]
        try:
            self.lessons(f"agenda_{action}",
                         f'In the day\'s plan the user {words}: "{title[:200]}" (kind of work: '
                         f'"{said.get("kind", "")}", it was {said.get("who", "")}, '
                         f'{said.get("minutes", "")} min, {said.get("focus", "")}).')
        except Exception:
            log.warning("what the user did to the plan could not be kept as a lesson", exc_info=True)

    def recent(self, most: int = 20) -> List[Dict[str, Any]]:
        """The user's last words on the plan, newest first - the reviewer's cases."""
        return [dict(row) for row in self.db.query(
            "SELECT at, title, action, detail FROM agenda_feedback ORDER BY at DESC LIMIT ?", (most,))]


def _day(text: Any) -> Optional[date]:
    try:
        return datetime.strptime(str(text)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _clock(day: date, text: Any) -> Optional[datetime]:
    try:
        hours, minutes = str(text).split(":")[:2]
        return datetime(day.year, day.month, day.day) + timedelta(hours=int(hours), minutes=int(minutes))
    except (ValueError, TypeError):
        return None


class Reminders:
    """The plan's own reminders, told when their time comes - and the quiet of a deep block."""

    def __init__(self, agenda: Any, notify: Callable[..., Any],
                 allowed: Optional[Callable[[], bool]] = None) -> None:
        self.agenda = agenda
        self.db = agenda.db
        self.notify = notify
        self.allowed = allowed

    def _kv(self, key: str) -> str:
        row = self.db.query_one("SELECT value FROM kv WHERE key = ?", (key,))
        return str(row["value"]) if row and row["value"] else ""

    def _set(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE "
                        "SET value = excluded.value", (key, value))

    def _today(self, now: datetime) -> Optional[Dict[str, Any]]:
        latest = self.agenda.latest(now.date())
        if not latest:
            return None
        return latest

    def blocks(self, now: datetime) -> List[Dict[str, Any]]:
        latest = self._today(now)
        if not latest:
            return []
        out = []
        for block in (latest.get("plan") or {}).get("blocks") or []:
            if not isinstance(block, dict):
                continue
            start, end = _clock(now.date(), block.get("start")), _clock(now.date(), block.get("end"))
            if start is not None and end is not None and end > start:
                out.append(dict(block, _start=start, _end=end))
        return sorted(out, key=lambda one: one["_start"])

    # -- the quiet of deep work ---------------------------------------------------------------
    def quiet_now(self, now: Optional[datetime] = None) -> str:
        """While a deep block runs, the Monitor's blocker - the other reminders held until its end,
        or until the next batch when the plan says so ("held_reminders")."""
        if self.allowed is not None and not self.allowed():
            return ""
        now = now or _now_local()
        blocks = self.blocks(now)
        held = str(((self._today(now) or {}).get("plan") or {}).get("held_reminders") or "")
        for n, block in enumerate(blocks):
            if block.get("kind") != "deep" or not (block["_start"] <= now < block["_end"]):
                continue
            until = block["_end"]
            if "batch" in held:
                nxt = next((one for one in blocks[n + 1:] if one.get("kind") in ("batch", "review")), None)
                until = nxt["_start"] if nxt else until
            return f"Deep work until {until:%H:%M}"
        return ""

    # -- told when their time comes -----------------------------------------------------------
    def tick(self, now: Optional[datetime] = None) -> List[str]:
        """What is due now, told once."""
        if self.allowed is not None and not self.allowed():
            return []
        now = now or _now_local()
        told: List[str] = []
        latest = self._today(now)
        if latest:
            things = {one["id"]: one for one in self.agenda.things()}
            blocks = self.blocks(now)
            for n, block in enumerate(blocks):
                lead = 0
                if block.get("kind") in ("meeting", "errand") and not (
                        n and blocks[n - 1].get("kind") == "travel"
                        and blocks[n - 1]["_end"] >= block["_start"] - timedelta(minutes=5)):
                    lead = LEAD_MIN
                if block.get("kind") == "free":
                    continue                     # SHOWN ON THE CALENDAR, NOT TOLD (the user, 2026-11-10)
                due = block["_start"] - timedelta(minutes=lead)
                # MISSED, TOLD LATE (the user, 2026-11-10): the agent away at its start, it is told
                # once it is back, while the block lasts - said late.
                late = due + timedelta(minutes=LATE_MIN) < now < block["_end"] and \
                    block.get("kind") not in ("break", "travel")
                if not (due <= now <= due + timedelta(minutes=LATE_MIN)) and not late:
                    continue
                # Once a block, whichever plan of the day holds it (a replan keeps its blocks).
                key = f"agenda.told.{latest.get('day') or now.date()}.{block.get('start')}." \
                      f"{block.get('title', '')}"[:200]
                if self._kv(key) or self._kv(
                        f"agenda.told.{latest['id']}.{block.get('start')}.{block.get('title', '')}"[:200]):
                    continue
                self._set(key, now.isoformat())
                title, message = self._words(block, things)
                if late:
                    title = f"Late - {title}"
                # WHICH ARE TO BE APPROVED: pressed, the reminder opens them in the approvals panel (the
                # user, 2026-11-09: "点提醒就打开 ... 每个审批session可以用类似project instruction的panel").
                approve = [str(ident) for ident in block.get("items") or [] if ident in things and (
                    things[ident].get("triage") or {}).get("who") == "approve"
                    and not waits_for_answer(things[ident], self._kv(WORK_KEY + str(ident)))] \
                    if block.get("kind") in ("batch", "review") else []
                for ident in approve:
                    self._set(APPROVE_TOLD + ident, now.isoformat())
                self.notify(title, message, {"agenda": {"plan": latest["id"], "block": block.get("start"),
                                                        "kind": block.get("kind"),
                                                        **({"approve": approve,
                                                            "until": block.get("end")} if approve else {})}})
                told.append(title)
                told += self._questions(now, latest, block, things)
            told += self._missed_approvals(now, latest, blocks, things)
            told += self._open_questions(now, latest, things)
        # NO BREAK REMINDER OF THE PLAN'S OWN (the user, 2026-11-13: "把这个删掉" - a break reminder is the user's
        # own to add, in the Monitor, as said 2026-11-07); a break the plan put in the day is still told.
        return told

    def _open_questions(self, now: datetime, latest: Dict[str, Any],
                        things: Dict[str, Dict[str, Any]]) -> List[str]:
        """WHAT THE AGENT NEEDS TO KNOW, ASKED AS IT COMES UP (the user, 2026-11-10: what waits for
        them is told, not held for a block): every thing that waits for their word and was not
        asked yet - each question standing on its own, each asked once."""
        asking = [ident for ident, one in things.items()
                  if waits_for_answer(one, self._kv(WORK_KEY + ident)) and not self._kv(ASKED + ident)]
        if not asking:
            return []
        for ident in asking:
            self._set(ASKED + ident, now.isoformat())
        title = "A question for you" if len(asking) == 1 else f"Questions for you: {len(asking)}"
        self.notify(title, "\n\n".join(question_for(things[ident]) for ident in asking),
                    {"agenda": {"plan": latest["id"], "approve": asking, "until": "", "ask": True}})
        return [title]

    def _questions(self, now: datetime, latest: Dict[str, Any], block: Dict[str, Any],
                   things: Dict[str, Dict[str, Any]]) -> List[str]:
        """WHAT THE AGENT NEEDS TO KNOW, ASKED (the user, 2026-11-10): the things of a block that
        wait for the user's word - each question standing on its own - once, whatever block holds
        it again; the reminder opens them to be answered in a line."""
        asking = [str(ident) for ident in block.get("items") or [] if str(ident) in things
                  and waits_for_answer(things[str(ident)], self._kv(WORK_KEY + str(ident)))
                  and not self._kv(ASKED + str(ident))]
        if not asking:
            return []
        for ident in asking:
            self._set(ASKED + ident, now.isoformat())
        title = "A question for you" if len(asking) == 1 else f"Questions for you: {len(asking)}"
        self.notify(title, "\n\n".join(question_for(things[ident]) for ident in asking),
                    {"agenda": {"plan": latest["id"], "approve": asking, "until": "", "ask": True}})
        return [title]

    def _missed_approvals(self, now: datetime, latest: Dict[str, Any], blocks: List[Dict[str, Any]],
                          things: Dict[str, Dict[str, Any]]) -> List[str]:
        """WHAT WAS TO BE APPROVED IN A BLOCK GONE BY UNTOLD (the user, 2026-11-10: "不会悄悄丢掉"): the
        things of today's blocks of approvals, past, whose work the agent did, never told - in one
        reminder of their own, opening the approvals panel."""
        missed: List[str] = []
        for block in blocks:
            if block.get("kind") not in ("batch", "review") or \
                    now <= block["_start"] + timedelta(minutes=LATE_MIN) or now < block["_end"]:
                continue
            for ident in block.get("items") or []:
                ident = str(ident)
                thing = things.get(ident)
                if thing is None or (thing.get("triage") or {}).get("who") != "approve" or \
                        thing.get("user_state") or ident in missed:
                    continue
                if self._kv(APPROVE_TOLD + ident) or self._kv(TOLD_DONE + ident) or not self._kv(WORK_KEY + ident):
                    continue                     # told, or nothing of the agent's to look at
                missed.append(ident)
        if not missed:
            return []
        for ident in missed:
            self._set(APPROVE_TOLD + ident, now.isoformat())
        title = f"To approve, missed earlier: {len(missed)}"
        self.notify(title, "\n".join(f"- {things[ident]['title']}" for ident in missed),
                    {"agenda": {"plan": latest["id"], "approve": missed, "until": ""}})
        return [title]

    def _words(self, block: Dict[str, Any], things: Dict[str, Dict[str, Any]]) -> Any:
        kind = str(block.get("kind") or "")
        titles = [things[ident]["title"] for ident in block.get("items") or [] if ident in things]
        if kind == "travel":
            return (f"Time to go: {block.get('title', '')}",
                    f"Until {block.get('end')}." + (f"\nTo {block['place']}." if block.get("place") else ""))
        if kind == "break":
            return f"A break, until {block.get('end')}", str(block.get("why") or "")
        approving = [things[ident]["title"] for ident in block.get("items") or []
                     if ident in things and (things[ident].get("triage") or {}).get("who") == "approve"
                     and not waits_for_answer(things[ident], self._kv(WORK_KEY + str(ident)))]
        if approving and kind in ("batch", "review"):
            return (f"To approve, until {block.get('end')}: {len(approving)}",
                    "\n".join(f"- {one}" for one in approving))
        lines = [f"- {one}" for one in titles]
        note = block.get("note") or {}
        for label, key in (("Last time", "left_off"), ("Folder", "folder"), ("The agent prepared", "agent_prepares")):
            if note.get(key):
                lines.append(f"{label}: {note[key]}")
        if note.get("files"):
            lines.append("Files: " + ", ".join(str(one) for one in note["files"] if one))
        if block.get("place"):
            lines.append(f"Where: {block['place']}")
        return f"Now, until {block.get('end')}: {block.get('title', '')}", "\n".join(lines)


def add_tools(registry: Any, agenda: Any, feedback: Feedback) -> None:
    """THE PLAN FROM THE CHAT: what is planned; and the user's word on a thing of it - "put X off to
    tomorrow", "I did X", "let the agent do X"."""
    from ...models.base import ToolSpec

    def schema(properties: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
        return {"type": "object", "properties": properties, "required": required}

    def listed(args: Dict[str, Any]) -> Any:
        latest = agenda.latest() or {}
        plan = latest.get("plan") or {}
        things = {one["id"]: one for one in agenda.things()}
        return {"day": latest.get("day"),
                "blocks": [{"start": b.get("start"), "end": b.get("end"), "kind": b.get("kind"),
                            "title": b.get("title"),
                            "things": [{"id": i, "title": things[i]["title"]} for i in b.get("items") or []
                                       if i in things]} for b in plan.get("blocks") or []],
                "later": [{"id": e.get("item"), "day": e.get("day"),
                           "title": (things.get(e.get("item")) or {}).get("title", "")}
                          for e in (plan.get("later") or [])[:80]]}

    def act(args: Dict[str, Any]) -> Any:
        said = feedback.apply(str(args.get("id") or ""), str(args.get("action") or ""),
                              day=str(args.get("day") or ""), by="chat")
        return said or {"error": "no such thing on the plan, or no such action - see agenda_list"}

    registry.add(ToolSpec(
        "agenda_list", "Read the user's plan: the day's blocks with the things in each (by id), and "
                       "the things put on the days ahead.", schema({}, []), read_only=True), listed)
    registry.add(ToolSpec(
        "agenda_change",
        "Change the user's plan as they say - only when they say it: a thing done (done), put off "
        "(later_today, tomorrow, or on_day with a day), given to the agent (to_agent) or taken back "
        "(mine), given more time (more_time), or no longer to be done (drop). The day is planned "
        "again around it. The thing's id from agenda_list.",
        schema({"id": {"type": "string", "description": "the thing's id, from agenda_list"},
                "action": {"type": "string", "enum": list(ACTIONS)},
                "day": {"type": "string", "description": "for on_day: YYYY-MM-DD"}},
               ["id", "action"])), act)


__all__ = ["Feedback", "Reminders", "add_tools", "ACTIONS"]
