"""A REMINDER THE AGENT WROTE, STILL SO WHEN ITS TIME COMES (the user, 2026-11-15: "是不是在通知发布之前要对这个再做
一个检查，看看是否过时").

The heartbeat writes a reminder from what it saw - "the build is waiting for your approval" - and the reminder
waits for its time. By then what it says may be over: the build approved, run and passed, the invitations sent.
It was told anyway, word for word as written. Now, its time come, the model reads it again beside what was
seen since it was last written - the states and the words on the screen - and says one of three things:

    send      still so, told as written
    rewrite   still to be told, some of it no longer so: told in new words
    drop      over, nothing left for the user to do: not told, and the report says why

Every reminder the agent set is read again - the heartbeat's from what it saw, and those the chat or delegated
work set (the user, 2026-11-15: "我觉得都覆盖，但是要明确和agent说，必须有充足的证据说明是已经做好了才取消弹出"): one
is held back only on plain evidence that it is done. The user's own are told as they are. Nothing seen since
it was written: told, and no call made.
The model is the heartbeat's (the user, 2026-11-15: "不要用小模型"). Any failure: told as written - a
reminder lost is worse than one late in its news.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

#: Whose reminders are read again at their time: the agent's - the heartbeat's, and the chat's or delegated
#: work's. Not the user's own.
READ_AGAIN = ("heartbeat", "agent")
SEND, REWRITE, DROP = "send", "rewrite", "drop"
VERDICTS = (SEND, REWRITE, DROP)
#: How much of what was seen since is shown: the states, and the screen's words (newest first).
MOST_STATES, MOST_SCREENS, SCREEN_CHARS, STATE_CHARS = 30, 80, 6000, 400

SYSTEM = """You check a reminder the user's personal agent wrote earlier, from what it saw on the user's
computer, now that the reminder's time has come and before it is shown to the user.

You are given the reminder (its title, its message, why it was written, and when it was last written),
and what was seen on the computer since then: the user's states (what they were doing) and the words read
from the windows in front, newest first.

Decide one of three:
- "send": what it says still holds, or nothing seen says otherwise. This is the answer whenever you are unsure.
- "rewrite": it should still be told, but part of it is plainly no longer so (a step done, a time moved, a
  number changed). Write it again with what is so now, as briefly as the original, in the same voice.
- "drop": ONLY on sufficient evidence that it is over - done, answered, cancelled or no longer needed - and
  that nothing is left for the user to do about it. Sufficient evidence is something seen that says so
  directly and about this very thing: the page showing it finished, the user's own reply sent, the meeting
  shown cancelled. Not enough: that it was likely done by now, that it has not come up again, that time has
  passed, that the user was busy with something near it, or a plan to do it. When the evidence is partial,
  or about something only like it, it is "send" (or "rewrite", when part of it is plainly no longer so).

A reminder held back that should have been told costs the user far more than one told that was no longer
needed: when in doubt, "send". Judge by what was seen, not by guesses. Words an assistant or a page wrote
about the matter count as seen, but when two say different things, the later one counts.

Answer with JSON only:
{"verdict": "send" | "rewrite" | "drop", "title": "the new title, for rewrite", "message": "the new message, for rewrite", "why": "one sentence: what was seen that decided it - for drop, the evidence itself, with when it was seen"}"""


def _when(value: Any) -> Optional[datetime]:
    """Any moment kept - a datetime, ISO words with or without a zone - in UTC; None when none."""
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def written_at(db: Any, task: Any) -> Optional[datetime]:
    """When its words were last written: made, or last changed by the heartbeat (agent/reports.py's
    record of it)."""
    moments = [_when(getattr(task, "created_at", None))]
    try:
        for row in db.query("SELECT at FROM agent_did WHERE ref = ? AND kind IN "
                            "('notifications_scheduled', 'notifications_updated')", (task.id,)):
            moments.append(_when(row["at"]))
    except Exception:
        log.debug("when the reminder was changed could not be read", exc_info=True)
    moments = [one for one in moments if one is not None]
    return max(moments) if moments else None


def seen_since(db: Any, since: datetime) -> Dict[str, List[str]]:
    """What was seen since `since`: the states that went on after it, and the words of the windows in
    front, newest first."""
    states: List[str] = []
    try:
        for row in db.query("SELECT title, detail, starts_at, ends_at FROM states "
                            "ORDER BY created_at DESC LIMIT 200"):
            ended = _when(row["ends_at"]) or _when(row["starts_at"])
            if ended is None or ended < since:
                continue
            began = _when(row["starts_at"])
            states.append(f"{began.isoformat()[:16] if began else '?'} - {ended.isoformat()[:16]} "
                          f"{row['title']}: {str(row['detail'] or '')[:STATE_CHARS]}")
            if len(states) >= MOST_STATES:
                break
    except Exception:
        log.debug("the states could not be read", exc_info=True)
    screens: List[str] = []
    left = SCREEN_CHARS
    try:
        for row in db.query("SELECT timestamp, application, data FROM raw_events WHERE "
                            "event_type = 'screen.text' AND timestamp >= ? ORDER BY timestamp DESC LIMIT ?",
                            (since.isoformat(), MOST_SCREENS)):
            moment = _when(row["timestamp"])
            if moment is None or moment < since:
                continue
            data = json.loads(row["data"] or "{}")
            text = re.sub(r"\s+", " ", str(data.get("text") or "")).strip()
            if not text or any(text in one for one in screens):
                continue
            line = (f"{moment.isoformat()[:16]} {row['application'] or ''} "
                    f"[{data.get('window_title') or ''}]: {text}")[:max(left, 0)]
            if not line:
                break
            screens.append(line)
            left -= len(line)
    except Exception:
        log.debug("the screen's words could not be read", exc_info=True)
    return {"states": states, "screens": screens}


def _answer(text: str) -> Dict[str, Any]:
    found = re.search(r"\{.*\}", str(text or ""), re.S)
    if not found:
        return {}
    try:
        said = json.loads(found.group(0))
    except ValueError:
        return {}
    return said if isinstance(said, dict) else {}


def check(db: Any, models: Any, task: Any, title: str, message: str,
          now: Optional[datetime] = None) -> Dict[str, str]:
    """{"verdict": send | rewrite | drop, "title", "message", "why"} for the reminder `task`, its time come."""
    told = {"verdict": SEND, "title": title, "message": message, "why": ""}
    if str(getattr(task, "created_by", "") or "") not in READ_AGAIN or models is None:
        return told
    since = written_at(db, task)
    if since is None:
        return told
    seen = seen_since(db, since)
    if not seen["states"] and not seen["screens"]:
        return told
    now = now or datetime.now(timezone.utc)
    content = "\n".join([
        f"Now: {now.isoformat()[:16]} UTC",
        f"The reminder, last written {since.isoformat()[:16]} UTC:",
        f"Title: {title}",
        f"Message: {message}",
        f"Why it was written: {str(getattr(task, 'reason', '') or '')[:1000]}",
        "",
        "The user's states since then, newest first:",
        *(seen["states"] or ["(none)"]),
        "",
        "Words read from the windows in front since then, newest first:",
        *(seen["screens"] or ["(none)"]),
    ])
    try:
        from ...common.languages import in_display
        from ...models.base import ModelRequest
        response = models.invoke(ModelRequest(
            messages=[{"role": "user", "content": content}], system=SYSTEM, max_tokens=800,
            task="check a reminder is still so", language=in_display("what is said to the user")),
            role="heartbeat")
    except Exception:
        log.warning("whether the reminder %s is still so could not be asked; told as written", task.id,
                    exc_info=True)
        return told
    said = _answer(getattr(response, "text", ""))
    verdict = str(said.get("verdict") or "")
    why = str(said.get("why") or "").strip()[:500]
    if verdict == DROP and why:
        return {"verdict": DROP, "title": title, "message": message, "why": why}
    if verdict == REWRITE:
        new_title = str(said.get("title") or "").strip()[:200] or title
        new_message = str(said.get("message") or "").strip()[:4000]
        if new_message:
            return {"verdict": REWRITE, "title": new_title, "message": new_message, "why": why}
    return dict(told, why=why)
