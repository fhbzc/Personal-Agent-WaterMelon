"""EVERYTHING ON THE USER'S PLATE, IN ONE LIST (agenda_items) - gathered by code, no model.

What reads the user's activity already writes what it finds where it belongs: step_notify a reminder
on the calendar, a to-do, what waits for a time; a project's round its next steps; delegated work
its result. This keeps one row for each, by where it came from and its id there (`source`,
`ref_id`), and closes the row of one gone from there. A row whose words changed gets a new
`fingerprint`, and is judged again (triage.py).

  todo      the user's to-dos, open
  calendar  what is on the calendar in the next week - a reminder, or something to go to (an event)
  waiting   agreed, waiting for a time (pending_confirmations)
  step      a project's next steps not done
  work      delegated work: going, finished to be looked at, or stopped
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

log = logging.getLogger(__name__)

#: How far ahead the calendar is read - the plan has no end, so a year (the user, 2026-11-09: two
#: reminders three weeks off were never gathered while it was a week); how many of each at most.
AHEAD_DAYS = 366
MOST_EACH = 80
#: A calendar notification held for the agent (the plan's word, when its time came): kept on the
#: plan after its time until the agent's part is done and looked at - for so long at most.
HELD = "agenda.held."
HELD_DAYS = 7
#: The words in an event's details that say where it is.
PLACE_WORDS = ("place", "location", "where", "venue", "room", "address", "地点", "地址", "会议室")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return default


def _moment(text: Any) -> Optional[datetime]:
    try:
        at = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _place(details: Any) -> str:
    if not isinstance(details, dict):
        return ""
    for key, value in details.items():
        if any(word in str(key).lower() for word in PLACE_WORDS) and str(value or "").strip():
            return " ".join(str(value).split())[:200]
    return ""


# -- where each is kept ------------------------------------------------------------------------
def from_todos(db: Any) -> Iterable[Dict[str, Any]]:
    for row in db.query("SELECT * FROM todo_items WHERE done_at = '' AND deleted_at = '' "
                        "ORDER BY created_at DESC LIMIT ?", (MOST_EACH,)):
        note = (f" (the agent thinks it is {row['suggested_kind'] or 'done'}: "
                f"{row['suggested_why']})") if row.get("suggested_at") else ""
        yield {"source": "todo", "ref_id": row["id"], "title": row["text"],
               "detail": f"{row.get('detail') or ''}{note}".strip(),
               "since": row["created_at"]}


def _held(db: Any, now: datetime) -> Dict[str, datetime]:
    """The calendar notifications held for the agent, by task id, with when - within HELD_DAYS."""
    out: Dict[str, datetime] = {}
    try:
        rows = db.query("SELECT key, value FROM kv WHERE key LIKE ?", (HELD + "%",))
    except Exception:
        return out
    for row in rows:
        at = _moment(row["value"])
        if at is not None and now - at <= timedelta(days=HELD_DAYS):
            out[str(row["key"])[len(HELD):]] = at
    return out


def from_calendar(db: Any, now: datetime) -> Iterable[Dict[str, Any]]:
    until = now + timedelta(days=AHEAD_DAYS)
    held = _held(db, now)
    rows = list(db.query("SELECT * FROM scheduled_tasks WHERE status = 'pending' "
                         "AND execution_type = 'notification' ORDER BY scheduled_at LIMIT 400"))
    for ident in held:
        row = db.query_one("SELECT * FROM scheduled_tasks WHERE id = ? AND status != 'pending'",
                           (ident,))
        if row is not None:
            rows.append(row)
    for row in rows:
        at = _moment(row["scheduled_at"])
        if at is None or not (now - timedelta(hours=1) <= at <= until or row["id"] in held):
            continue
        metadata = _json(row.get("metadata"), {})
        payload = _json(row.get("task_payload"), {})
        details = metadata.get("details") or {}
        event = metadata.get("event") or ""
        kind = ("something to go to" if event == "going" else
                "something that may interest them, nothing they must do" if event == "explore"
                else "a reminder")
        said = "; ".join(f"{k}: {v}" for k, v in list(details.items())[:6]) if isinstance(
            details, dict) else ""
        yield {"source": "calendar", "ref_id": row["id"], "title": row["title"],
               "detail": " ".join(f"[{kind}] {row.get('description') or ''} {said}".split())[:600],
               "fixed_at": at.isoformat(), "place": _place(details),
               "quiet": event == "explore" or payload.get("notify_user") is False,
               "since": row["created_at"]}


def from_waiting(db: Any) -> Iterable[Dict[str, Any]]:
    for row in db.query("SELECT * FROM pending_confirmations WHERE status = 'open' "
                        "ORDER BY updated_at DESC LIMIT ?", (MOST_EACH,)):
        people = ", ".join(str(one) for one in _json(row.get("people"), [])[:6])
        yield {"source": "waiting", "ref_id": row["id"], "title": row["title"],
               "detail": " ".join(f"[agreed, its time not set yet] {row.get('detail') or ''} "
                                  f"{('with ' + people) if people else ''}".split())[:600],
               "since": row["created_at"]}


def from_steps(db: Any) -> Iterable[Dict[str, Any]]:
    from ..states import todo as steps
    from ..states.tasks import Kept
    count = 0
    for task in Kept(db).live():
        said = task.described or {}
        title = str(said.get("title") or "").strip()
        if not title or said.get("deleted_by_user"):
            continue
        for step in steps.items(said):
            if step.get("done") or not step.get("id"):
                continue
            yield {"source": "step", "ref_id": f"{task.id}:{step['id']}", "title": step["text"],
                   "detail": f"[a next step of the project] {title}", "project": title,
                   "since": str(step.get("at") or getattr(task, "created_at", "") or "")}
            count += 1
            if count >= MOST_EACH:
                return


def from_work(assigned: Optional[Callable[[], List[Dict[str, Any]]]]) -> Iterable[Dict[str, Any]]:
    if assigned is None:
        return
    for one in assigned()[:MOST_EACH]:
        versions = ((one.get("work") or {}).get("versions") or [{}])
        latest = versions[-1] if versions else {}
        status = str(latest.get("status") or "")
        state = str(latest.get("state") or "")
        if latest.get("alive") or state == "ongoing":
            how = "the agent is doing it"
        elif status == "done":
            how = "the agent finished it: for the user to look at"
        elif state == "drafting":
            how = "its plan is being written; not started"
        else:
            how = f"the agent's work stopped ({status or state or 'unknown'}): for the user to look at"
        yield {"source": "work", "ref_id": one["id"], "title": one.get("text") or "Delegated work",
               "detail": f"[delegated work; {how}] {one.get('detail') or ''}".strip(),
               "since": one.get("at") or ""}


def ended(db: Any, source: str, ref: str) -> Any:
    """WHEN AND HOW A THING LEFT ITS SOURCE, as the source kept it - not when the agenda next
    looked (a side agent's note, 2026-11-09: a to-do ticked at 9:30 and found at 10:40 was kept
    as done at 10:40, and how long it took learned wrong). ("", "gone") where the source says
    nothing: deleted, gone by, or no longer there. "done" only for what was done."""
    try:
        if source == "todo":
            row = db.query_one("SELECT done_at, deleted_at FROM todo_items WHERE id = ?", (ref,))
            if row and row["done_at"]:
                return str(row["done_at"]), "done"
            if row and row["deleted_at"]:
                return str(row["deleted_at"]), "gone"
        elif source == "step":
            from ..states import todo as steps
            from ..states.tasks import Kept
            task_id, _, step_id = ref.partition(":")
            for task in Kept(db).live():
                if str(task.id) != task_id:
                    continue
                for step in steps.items(task.described or {}):
                    if str(step.get("id")) == step_id and step.get("done"):
                        return str(step.get("done_at") or ""), "done"
    except Exception:
        log.warning("when %s %s ended could not be read", source, ref, exc_info=True)
    return "", "gone"


# -- the list, kept --------------------------------------------------------------------------
def fingerprint(one: Dict[str, Any]) -> str:
    said = "|".join(str(one.get(key) or "") for key in ("title", "detail", "fixed_at", "place",
                                                        "project"))
    return hashlib.sha1(said.encode("utf-8")).hexdigest()[:16]


def collect(db: Any, assigned: Optional[Callable[[], List[Dict[str, Any]]]] = None,
            now: Optional[datetime] = None) -> Dict[str, int]:
    """Every source read; a row added for each new, changed where its words changed, closed where
    it is gone. A source that cannot be read leaves its rows as they were."""
    now = now or datetime.now(timezone.utc)
    stamp = _now()
    counts = {"added": 0, "changed": 0, "closed": 0, "open": 0}
    readers = {"todo": lambda: from_todos(db), "calendar": lambda: from_calendar(db, now),
               "waiting": lambda: from_waiting(db), "step": lambda: from_steps(db),
               "work": lambda: from_work(assigned)}
    for source, read in readers.items():
        try:
            found = list(read())
        except Exception:
            log.warning("the agenda could not read %s; its rows kept", source, exc_info=True)
            continue
        kept = {row["ref_id"]: row for row in db.query(
            "SELECT * FROM agenda_items WHERE source = ?", (source,))}
        seen = set()
        for one in found:
            ref = str(one["ref_id"])
            seen.add(ref)
            print_ = fingerprint(one)
            extra = json.dumps({"quiet": bool(one.get("quiet")), "since": one.get("since") or ""})
            row = kept.get(ref)
            if row is None:
                db.execute(
                    "INSERT INTO agenda_items (id, source, ref_id, title, detail, fixed_at, place, "
                    "project, status, fingerprint, triage, triaged_for, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, '', ?, ?)",
                    (f"agenda_{uuid.uuid4().hex[:12]}", source, ref, str(one.get("title") or "")[:400],
                     str(one.get("detail") or "")[:1200], one.get("fixed_at") or "",
                     one.get("place") or "", one.get("project") or "", print_,
                     json.dumps({"_from": json.loads(extra)}), stamp, stamp))
                counts["added"] += 1
            elif row["fingerprint"] == print_ and row["status"] != "open" and \
                    '"user_state"' in str(row.get("triage") or ""):
                continue                  # done or let go of by the user, and not changed since
            elif row["fingerprint"] != print_ or row["status"] != "open":
                db.execute(
                    "UPDATE agenda_items SET title = ?, detail = ?, fixed_at = ?, place = ?, "
                    "project = ?, fingerprint = ?, status = 'open', closed_at = '', updated_at = ? "
                    "WHERE id = ?",
                    (str(one.get("title") or "")[:400], str(one.get("detail") or "")[:1200],
                     one.get("fixed_at") or "", one.get("place") or "", one.get("project") or "",
                     print_, stamp, row["id"]))
                counts["changed"] += 1
        for ref, row in kept.items():
            if ref not in seen and row["status"] == "open":
                when, how = ended(db, source, ref)
                said = _json(row.get("triage"), {})
                said = dict(said if isinstance(said, dict) else {}, _closed=how)
                db.execute("UPDATE agenda_items SET status = 'closed', closed_at = ?, updated_at = ?, "
                           "triage = ? WHERE id = ?",
                           (when or stamp, stamp, json.dumps(said, ensure_ascii=False), row["id"]))
                counts["closed"] += 1
    row = db.query_one("SELECT COUNT(*) AS n FROM agenda_items WHERE status = 'open'")
    counts["open"] = int(row["n"]) if row else 0
    return counts


def open_items(db: Any) -> List[Dict[str, Any]]:
    out = []
    for row in db.query("SELECT * FROM agenda_items WHERE status = 'open' ORDER BY created_at"):
        one = dict(row)
        one["triage"] = _json(one.get("triage"), {})
        out.append(one)
    return out


__all__ = ["collect", "open_items", "fingerprint", "AHEAD_DAYS"]
