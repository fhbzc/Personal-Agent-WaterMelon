"""THE ACCOUNT'S CALENDAR ON THE AGENTS SOCIETY (the site's /api 5.5, 2026-11-14; the user: "agentssociety的doc
更新了calender, 你要支持这个功能").

    read          the account's events (GET /v2/calendar) and the invitations waiting (GET
                  /v2/calendar/invitations), as the key permits (calendar.read)
    the inbox     what the site says of an event - an invitation, a change, a cancelling, an answer
                  (`"kind": "calendar"`) - is never a message to answer: kept, and the calendar read
                  again (service.collect hands it here)
    local         WHAT THE USER GOES TO IS ON THEIR OWN CALENDAR (the user: "网站事件要不要进本地日历 - 要的"):
                  each event they organize or accepted is a reminder on the application's calendar at
                  its time; moved there, moved here; cancelled or declined, cancelled here - each once,
                  by the event's id, and said in the report
    answering     ONE PERMISSION OF THE AGENT'S OWN, THE USER'S TO SET, for everything about invitations -
                  answering them, inviting, changing, cancelling (the user: "和invitation相关的process权限在
                  agent端是单独权限(一个权限决定能否invite/accept/edit invitation)，用户可以设定"):
                    judge  THE AGENT JUDGES (the default) - what it can tell the user would want, it does,
                           once its review on the user's behalf lets it through, and says so; WHAT IT
                           CANNOT TELL GOES TO THE USER (the user: "agent判断自己可以处理就处理，不能处理还是
                           问用户") - in the report, Needs your look
                    ask    nothing is done before the user says so: each in Needs your look
                    read   kept and shown, nothing answered or changed by the agent
                  The key's own calendar.* permissions hold under all three.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ..common.languages import in_display
from . import identity as keys

log = logging.getLogger(__name__)

#: How the agent handles invitations - the user's to set (one setting for all of it).
CALENDAR_MODES = ("judge", "ask", "read")
DEFAULT_MODE = "judge"
RESPONSES = ("accepted", "tentative", "declined")
#: The site's parts of the calendar permission.
PARTS = ("read", "write", "invite", "respond")
#: The calendar and the invitations read again this often; this far back and ahead.
READ_SECONDS = 300
DAYS_BEFORE, DAYS_AHEAD = 1, 60
#: The site's limits on what an event holds.
MOST_TITLE, MOST_DESCRIPTION, MOST_LOCATION = 200, 5000, 300
#: What the agent may do on the calendar of its own in an hour.
AGENT_ACTS_AN_HOUR = 30

SCHEMA = """
-- EACH SITE EVENT THE USER GOES TO, ON THEIR OWN CALENDAR: the application's reminder for it.
CREATE TABLE IF NOT EXISTS society_calendar_local (
    event_id    TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL DEFAULT '',
    sequence    INTEGER NOT NULL DEFAULT 0,
    start       TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    kept_at     TEXT NOT NULL DEFAULT ''
);
-- EACH INVITATION LOOKED AT ONCE: answered by the agent, asked of the user, or left (read).
CREATE TABLE IF NOT EXISTS society_calendar_decided (
    event_id    TEXT PRIMARY KEY,
    at          TEXT NOT NULL DEFAULT '',
    answer      TEXT NOT NULL DEFAULT '',   -- accepted | tentative | declined | asked | left
    by          TEXT NOT NULL DEFAULT '',   -- agent | user
    held_id     TEXT NOT NULL DEFAULT '',   -- its approval, when asked of the user
    why         TEXT NOT NULL DEFAULT ''
);
-- WHAT THE SITE SAID OF AN EVENT IN THE INBOX - kept, never answered as a message.
CREATE TABLE IF NOT EXISTS society_calendar_notes (
    id          TEXT PRIMARY KEY,           -- the inbox message's id
    event_id    TEXT NOT NULL DEFAULT '',
    peer_id     TEXT NOT NULL DEFAULT '',
    subject     TEXT NOT NULL DEFAULT '',
    text        TEXT NOT NULL DEFAULT '',
    at          REAL NOT NULL DEFAULT 0,
    received_at TEXT NOT NULL DEFAULT ''
);
"""

JUDGE = """YOU ARE THE PERSONAL AGENT OF {user} (their account on the Agents Society: {me}). Someone has
invited {user} to an event on the Agents Society calendar. You act for {user} and for no one else.

{guard}

{commands}

SAY HOW {user} WOULD ANSWER IT: "accepted" (yes), "tentative" (maybe) or "declined" (no) - ONLY WHEN YOU
CAN TELL: who asks ({user}'s contact, or someone {user} does not know), what it is, and whether {user}'s
own calendar is free then (given below). WHEN YOU CANNOT TELL - a stranger, something {user} would want to
decide, a clash you cannot weigh, anything you are unsure of - answer "ask": {user} is asked, and
nothing is sent. Asking costs little; a wrong answer goes to the person who invited them.
Return ONLY this JSON object:
{{"answer": "accepted | tentative | declined | ask",
  "why": "in one or two sentences, for {user}",
  "for_user": "what {user} should know of it - empty if nothing"}}"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: Any, most: int) -> str:
    said = "".join(ch for ch in str(value or "") if ch in "\n\t" or ord(ch) >= 32)
    return said.strip()[:most]


def _moment(event: Dict[str, Any], key: str = "start") -> Optional[datetime]:
    """An event's start (or end) as a moment - from its Unix seconds, else its ISO time."""
    seconds = event.get(f"{key}_at")
    if isinstance(seconds, (int, float)) and seconds > 0:
        return datetime.fromtimestamp(float(seconds), timezone.utc)
    said = str(event.get(key) or "")
    if not said:
        return None
    try:
        when = datetime.fromisoformat(said.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _local(when: Optional[datetime]) -> str:
    return when.astimezone().strftime("%Y-%m-%d %H:%M %Z") if when else ""


class SiteCalendar:
    def __init__(self, society: Any) -> None:
        self.society = society
        self.app = society.app
        self.db = society.db
        with self.db.write() as conn:
            conn.executescript(SCHEMA)
        self._deciding = threading.Lock()
        self._read_at = 0.0

    # -- what the key permits, and what the user lets the agent do ------------------------------
    def may(self, part: str) -> bool:
        return self.society.may("calendar." + part)

    def _must(self, part: str, words: str) -> None:
        if not self.may(part):
            raise PermissionError(f"this agent's key may not {words} - give it calendar.{part} on "
                                  "your account page on the website")

    def mode(self) -> str:
        said = self.society._state("calendar_mode")
        return said if said in CALENDAR_MODES else DEFAULT_MODE

    def set_mode(self, mode: str) -> Dict[str, Any]:
        if mode not in CALENDAR_MODES:
            raise ValueError("invitations are judged by the agent (judge), asked of you (ask) or only "
                             "read (read)")
        self.society._set_state("calendar_mode", mode)
        return self.shown()

    # -- read from the website ----------------------------------------------------------------
    def read(self) -> None:
        """The account's events and its invitations waiting, where the key permits; what the user goes
        to kept on their own calendar."""
        self._read_at = time.time()
        society = self.society
        if not self.may("read"):
            society._set_state("site_calendar", [])
            society._set_state("calendar_invitations", [])
            return
        now = datetime.now(timezone.utc)
        try:
            said = society.client.calendar((now - timedelta(days=DAYS_BEFORE)).isoformat(),
                                           (now + timedelta(days=DAYS_AHEAD)).isoformat())
            events = [one for one in said.get("events") or [] if isinstance(one, dict)][:500]
            society._set_state("site_calendar", events)
            society._set_state("calendar_timezone", str(said.get("timezone") or ""))
        except Exception:
            log.info("the society calendar could not be read", exc_info=True)
            events = None
        try:
            waiting = [one for one in (society.client.calendar_invitations() or {}).get("invitations")
                       or [] if isinstance(one, dict)][:200]
            society._set_state("calendar_invitations", waiting)
        except Exception:
            log.info("the society calendar invitations could not be read", exc_info=True)
        if events is not None:
            self.keep_local(events)

    def read_now_and_then(self) -> None:
        if time.time() - self._read_at >= READ_SECONDS:
            self.read()

    def on_inbox(self, message: Dict[str, Any], sender: str, name: str) -> bool:
        """WHAT THE SITE SAYS OF AN EVENT in the inbox: kept, never answered as a message - and the
        calendar read again at once. False when it was kept already."""
        ident = _text(message.get("id"), 80)
        done = self.db.execute(
            "INSERT OR IGNORE INTO society_calendar_notes (id, event_id, peer_id, subject, text, at, "
            "received_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ident, _text(message.get("event") or message.get("thread"), 80), sender,
             _text(message.get("subject"), 120), _text(message.get("text"), 2000),
             float(message.get("at") or 0), _now()))
        self._read_at = 0.0                       # read again on the next round
        return bool(getattr(done, "rowcount", 1))

    # -- what the user goes to, on their own calendar -------------------------------------------
    @staticmethod
    def goes(event: Dict[str, Any]) -> bool:
        return (str(event.get("status") or "confirmed") == "confirmed"
                and (bool(event.get("you_organize")) or event.get("your_response") == "accepted"))

    def keep_local(self, events: List[Dict[str, Any]]) -> Dict[str, int]:
        """Each event the user organizes or accepted, a reminder on their own calendar at its time;
        changed on the site (its `sequence`), changed here; cancelled or declined, cancelled here."""
        scheduler = getattr(self.app, "scheduler", None)
        counts = {"added": 0, "moved": 0, "cancelled": 0}
        if scheduler is None:
            return counts
        kept = {row["event_id"]: row for row in self.db.query("SELECT * FROM society_calendar_local")}
        now = datetime.now(timezone.utc)
        for event in events:
            ident = _text(event.get("id"), 120)
            start = _moment(event)
            if not ident or start is None:
                continue
            row = kept.get(ident)
            if self.goes(event):
                if row is None:
                    if start >= now:
                        counts["added"] += self._add_local(scheduler, ident, event, start)
                elif int(event.get("sequence") or 0) != int(row["sequence"] or 0) or \
                        str(event.get("title") or "") != row["title"] or start.isoformat() != row["start"]:
                    counts["moved"] += self._move_local(scheduler, row, event, start)
            elif row is not None and row["task_id"]:
                counts["cancelled"] += self._cancel_local(scheduler, row, event)
        return counts

    def _who(self, event: Dict[str, Any]) -> str:
        organizer = str(event.get("organizer") or "")
        if not organizer or self.society._is_me(organizer):
            return "you"
        return self.society._name_of(organizer) or organizer

    def _add_local(self, scheduler: Any, ident: str, event: Dict[str, Any], start: datetime) -> int:
        from ..common.events.models import ScheduledTask
        from ..common.reason import BY_AGENT
        title = _text(event.get("title"), MOST_TITLE) or "An event"
        description = "\n".join(part for part in (
            _text(event.get("description"), 1000),
            f"Where: {_text(event.get('location'), MOST_LOCATION)}" if event.get("location") else "",
            f"On the Agents Society calendar, organized by {self._who(event)}.") if part)
        try:
            created = scheduler.create_task(ScheduledTask(
                title=title, description=description, scheduled_at=start,
                execution_type="notification", task_payload={"notify_user": True},
                metadata={"created_by": "society", "society_event": ident,
                          "all_day": bool(event.get("all_day"))},
                timezone_name=str(event.get("timezone") or "UTC"), created_by=BY_AGENT,
                reason="An event you go to on the Agents Society calendar"))
        except Exception:
            log.exception("a society event could not be put on the calendar")
            return 0
        self.db.execute(
            "INSERT OR REPLACE INTO society_calendar_local (event_id, task_id, sequence, start, title, "
            "kept_at) VALUES (?, ?, ?, ?, ?, ?)",
            (ident, str(getattr(created, "id", "") or ""), int(event.get("sequence") or 0),
             start.isoformat(), str(event.get("title") or ""), _now()))
        from ..agent.reports import did
        did(self.db, "calendar_added", f"Put on your calendar: {title} - {_local(start)}",
            "An event you go to on the Agents Society calendar", source="society",
            ref=str(getattr(created, "id", "") or ident))
        return 1

    def _move_local(self, scheduler: Any, row: Dict[str, Any], event: Dict[str, Any],
                    start: datetime) -> int:
        title = _text(event.get("title"), MOST_TITLE) or row["title"] or "An event"
        if row["task_id"]:
            try:
                scheduler.update_task(row["task_id"], scheduled_at=start, title=title,
                                      status="pending", lease_token=None)
            except Exception:
                log.exception("a society event could not be moved on the calendar")
                return 0
        self.db.execute("UPDATE society_calendar_local SET sequence = ?, start = ?, title = ?, kept_at = ? "
                        "WHERE event_id = ?", (int(event.get("sequence") or 0), start.isoformat(),
                                               str(event.get("title") or ""), _now(), row["event_id"]))
        from ..agent.reports import did
        did(self.db, "calendar_moved", f"Changed on your calendar, as on the Agents Society: {title} - "
            f"{_local(start)}", f"Changed by {self._who(event)}", source="society",
            ref=row["task_id"] or row["event_id"])
        return 1

    def _cancel_local(self, scheduler: Any, row: Dict[str, Any], event: Dict[str, Any]) -> int:
        why = ("Cancelled on the Agents Society calendar" if event.get("status") == "cancelled"
               else "You no longer go to it on the Agents Society calendar")
        try:
            scheduler.cancel(row["task_id"], why, by="society")
        except Exception:
            log.exception("a society event could not be taken off the calendar")
            return 0
        self.db.execute("UPDATE society_calendar_local SET task_id = '', kept_at = ? WHERE event_id = ?",
                        (_now(), row["event_id"]))
        from ..agent.reports import did
        did(self.db, "calendar_cancelled", f"Taken off your calendar: {row['title'] or 'an event'}", why,
            source="society", ref=row["task_id"])
        return 1

    # -- what the user sees ---------------------------------------------------------------------
    def _events(self) -> List[Dict[str, Any]]:
        return [one for one in self.society._state("site_calendar") or [] if isinstance(one, dict)]

    def invitations(self) -> List[Dict[str, Any]]:
        return [one for one in self.society._state("calendar_invitations") or [] if isinstance(one, dict)]

    def _shown_event(self, one: Dict[str, Any]) -> Dict[str, Any]:
        organizer = str(one.get("organizer") or "")
        decided = self.db.query_one("SELECT * FROM society_calendar_decided WHERE event_id = ?",
                                    (str(one.get("id") or ""),)) or {}
        return dict(one, organizer_name="" if self.society._is_me(organizer)
                    else self.society._name_of(organizer), start_local=_local(_moment(one)),
                    end_local=_local(_moment(one, "end")), decided=dict(decided) if decided else None)

    def shown(self) -> Dict[str, Any]:
        """The panel's: what the key permits, how the agent handles invitations, those waiting, and the
        events ahead (cancelled and declined ones apart)."""
        now = datetime.now(timezone.utc)
        ahead = [one for one in self._events() if str(one.get("status")) != "cancelled"
                 and one.get("your_response") != "declined" and (_moment(one, "end") or now) >= now]
        ahead.sort(key=lambda one: _moment(one) or now)
        return {"may": {part: self.may(part) for part in PARTS}, "mode": self.mode(),
                "modes": list(CALENDAR_MODES), "timezone": self.society._state("calendar_timezone") or "",
                "invitations": [self._shown_event(one) for one in self.invitations()],
                "events": [self._shown_event(one) for one in ahead[:60]]}

    # -- acting on the website ------------------------------------------------------------------
    def respond(self, event: str, response: str, scope: str = "", by: str = "user",
                why: str = "") -> Dict[str, Any]:
        self._must("respond", "answer calendar invitations")
        if response not in RESPONSES:
            raise ValueError("an answer is accepted, tentative or declined")
        said = self.society.client.calendar_respond(event, response, scope)
        self._decided(event, response, by, why=why)
        if by != "user":
            from ..agent.reports import did
            title = str(said.get("title") or event)
            did(self.db, "society_calendar_answered",
                f"{ {'accepted': 'Accepted', 'tentative': 'Answered maybe to', 'declined': 'Declined'}[response]} "
                f"the invitation: {title}", why, source="society", ref=event)
        self.read()
        return said

    def _clean_event(self, fields: Dict[str, Any], making: bool) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, most in (("title", MOST_TITLE), ("description", MOST_DESCRIPTION),
                          ("location", MOST_LOCATION), ("start", 60), ("end", 60), ("timezone", 60),
                          ("scope", 20), ("id", 120)):
            if fields.get(key) not in (None, ""):
                out[key] = _text(fields[key], most + 1)
                if len(out[key]) > most:
                    raise ValueError(f"an event's {key} is at most {most} characters")
        if "all_day" in fields:
            out["all_day"] = bool(fields["all_day"])
        if fields.get("recurrence") is not None:
            rule = fields["recurrence"]
            out["recurrence"] = [str(one) for one in (rule if isinstance(rule, list) else [rule])][:1]
        if making and not out.get("title"):
            raise ValueError("give the event a title")
        if making and not out.get("start"):
            raise ValueError("give the event a start: an ISO time with its offset")
        guests = [keys.normal_id(str(one)) for one in fields.get("attendees") or []]
        if any(not one for one in guests):
            raise ValueError("a guest is an account id, pa-XXXX-XXXX")
        if guests:
            out["attendees"] = guests[:100]
        return out

    def create(self, fields: Dict[str, Any], by: str = "user", why: str = "") -> Dict[str, Any]:
        body = self._clean_event(fields, making=True)
        self._must("write", "make calendar events")
        if body.get("attendees"):
            self._must("invite", "invite guests to calendar events")
        made = self.society.client.calendar_create(body)
        self._did(by, "society_calendar_made", f"Made the event: {body['title']}"
                  + (f", inviting {self._names(body.get('attendees'))}" if body.get("attendees") else ""),
                  why, str(made.get("id") or ""))
        self.read()
        return made

    def edit(self, fields: Dict[str, Any], by: str = "user", why: str = "") -> Dict[str, Any]:
        body = self._clean_event(fields, making=False)
        if not body.get("id"):
            raise ValueError("which event: its id")
        body.pop("attendees", None)
        self._must("write", "change calendar events")
        said = self.society.client.calendar_edit(body)
        self._did(by, "society_calendar_changed", f"Changed the event: {said.get('title') or body['id']}",
                  why, body["id"])
        self.read()
        return said

    def invite(self, event: str, attendees: List[str], by: str = "user", why: str = "") -> Dict[str, Any]:
        guests = [keys.normal_id(str(one)) for one in attendees or []]
        if not guests or any(not one for one in guests):
            raise ValueError("a guest is an account id, pa-XXXX-XXXX")
        self._must("invite", "invite guests to calendar events")
        said = self.society.client.calendar_invite(event, guests)
        self._did(by, "society_calendar_invited", f"Invited {self._names(guests)} to "
                  f"{said.get('title') or event}", why, event)
        self.read()
        return said

    def uninvite(self, event: str, account: str, by: str = "user", why: str = "") -> Dict[str, Any]:
        who = keys.normal_id(account)
        if not who:
            raise ValueError("a guest is an account id, pa-XXXX-XXXX")
        self._must("invite", "take guests off calendar events")
        said = self.society.client.calendar_uninvite(event, who)
        self._did(by, "society_calendar_uninvited", f"Took {self._names([who])} off "
                  f"{said.get('title') or event}", why, event)
        self.read()
        return said

    def cancel(self, event: str, scope: str = "", by: str = "user", why: str = "") -> Dict[str, Any]:
        self._must("write", "cancel calendar events")
        said = self.society.client.calendar_cancel(event, scope)
        self._did(by, "society_calendar_cancelled", f"Cancelled the event: {said.get('title') or event}",
                  why, event)
        self.read()
        return said

    def _names(self, accounts: Optional[List[str]]) -> str:
        return ", ".join(self.society._name_of(one) or one for one in accounts or [])

    def _did(self, by: str, kind: str, title: str, why: str, ref: str) -> None:
        if by == "user":
            return
        from ..agent.reports import did
        did(self.db, kind, title, why, source="society", ref=ref)

    # -- the agent's own doing: one permission, the user's to set -----------------------------
    OPS = ("respond", "create", "edit", "invite", "uninvite", "cancel")

    def do(self, op: str, args: Dict[str, Any], by: str = "user", why: str = "") -> Dict[str, Any]:
        """One thing on the calendar, by name."""
        if op == "respond":
            return self.respond(str(args.get("id") or ""), str(args.get("response") or ""),
                                str(args.get("scope") or ""), by=by, why=why)
        if op == "create":
            return self.create(args, by=by, why=why)
        if op == "edit":
            return self.edit(args, by=by, why=why)
        if op == "invite":
            return self.invite(str(args.get("id") or ""), list(args.get("attendees") or []), by=by, why=why)
        if op == "uninvite":
            return self.uninvite(str(args.get("id") or ""), str(args.get("account") or ""), by=by, why=why)
        if op == "cancel":
            return self.cancel(str(args.get("id") or ""), str(args.get("scope") or ""), by=by, why=why)
        raise ValueError(f"on the calendar: {', '.join(self.OPS)} - not {op!r}")

    def _acts_this_hour(self) -> int:
        since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
        try:
            row = self.db.query_one("SELECT COUNT(*) AS n FROM agent_did WHERE at >= ? AND kind LIKE "
                                    "'society_calendar_%'", (since,)) or {}
        except Exception:                          # nothing kept of what it did yet
            return 0
        return int(row.get("n") or 0)

    def agent_act(self, op: str, args: Dict[str, Any], why: str = "", said_by: str = "chat",
                  outside: Any = None) -> Dict[str, Any]:
        """WHAT THE AGENT DOES ON THE CALENDAR (a tool's call): as the user set it - "read", not at all;
        "ask", held for the user; "judge", done once its review on the user's behalf lets it through,
        else held for the user. Never more than the key permits."""
        if op not in self.OPS:
            return {"error": f"on the calendar: {', '.join(self.OPS)} - not {op!r}"}
        mode = self.mode()
        if mode == "read":
            return {"error": "your user set the calendar to be read only: you neither answer, invite "
                             "nor change anything there - tell them, if they want it done"}
        needs = {"respond": "respond", "create": "write", "edit": "write", "cancel": "write",
                 "invite": "invite", "uninvite": "invite"}[op]
        if not self.may(needs) or (op == "create" and args.get("attendees") and not self.may("invite")):
            return {"error": f"this agent's key may not do that - calendar.{needs} is not given on the "
                             "website"}
        try:
            self._clean_event(args, making=op == "create") if op in ("create", "edit") else None
        except ValueError as exc:
            return {"error": str(exc)}
        title = self._action_title(op, args)
        if mode == "judge" and self._acts_this_hour() < AGENT_ACTS_AN_HOUR:
            from ..agent import outside_review
            passed, review_why = outside_review.one_action(
                self.app, {"kind": f"calendar_{op}", "title": title, **{k: v for k, v in args.items()
                                                                          if k != "description"}},
                outside if outside is not None else [],
                what=f"acting on {self._user()}'s Agents Society calendar, as their agent: an answer, an "
                     "invitation or a change goes to the other people on the event at once",
                task="society_calendar_act", about=str(args.get("id") or ""))
            if passed:
                try:
                    return {"done": True, "op": op, "said": self.do(op, args, by=said_by, why=why)}
                except (ValueError, PermissionError) as exc:
                    return {"error": str(exc)}
                except Exception as exc:
                    return {"error": f"the website did not take it: {exc}"[:300]}
            why = "\n".join(part for part in (why, f"Held back by your agent's review: {review_why}")
                            if part.strip())
        held = self._hold(op, args, title, why)
        return {"held": held, "said": "Not done yet: it waits for your user's yes, in their report "
                                      "(Needs your look). Tell them so."}

    def _action_title(self, op: str, args: Dict[str, Any]) -> str:
        event = self._event_named(str(args.get("id") or ""))
        name = str(args.get("title") or event.get("title") or args.get("id") or "")
        return {"respond": f"Answer “{args.get('response')}” to the invitation: {name}",
                "create": f"Make the event: {name}" + (f", inviting {self._names(args.get('attendees'))}"
                                                        if args.get("attendees") else ""),
                "edit": f"Change the event: {name}",
                "invite": f"Invite {self._names(args.get('attendees'))} to {name}",
                "uninvite": f"Take {self._names([str(args.get('account') or '')])} off {name}",
                "cancel": f"Cancel the event: {name}"}[op][:300]

    def _event_named(self, ident: str) -> Dict[str, Any]:
        return next((one for one in self._events() + self.invitations() if one.get("id") == ident), {})

    def _hold(self, op: str, args: Dict[str, Any], title: str, why: str) -> str:
        approvals = getattr(self.app, "approvals", None)
        if approvals is None:
            return ""
        start = _moment(self._event_named(str(args.get("id") or "")) or args)
        return approvals.hold("society", "society_calendar_action", title, {"op": op, "args": args},
                              why=why or "Your agent would do this on your Agents Society calendar.",
                              due=start.astimezone().strftime("%Y-%m-%d %H:%M") if start else "")

    def carry(self, payload: Dict[str, Any]) -> None:
        """The user's yes to a thing held: done as they approved it."""
        self.do(str(payload.get("op") or ""), dict(payload.get("args") or {}), by="user")

    # -- the invitations waiting: the agent's own answers, as the user set it -------------------
    def _decided(self, event: str, answer: str, by: str, held: str = "", why: str = "") -> None:
        self.db.execute("INSERT OR REPLACE INTO society_calendar_decided (event_id, at, answer, by, held_id, "
                        "why) VALUES (?, ?, ?, ?, ?, ?)", (event, _now(), answer, by, held, why[:1000]))

    def decide_waiting(self) -> int:
        """Each invitation not looked at yet: answered by the agent where it can tell ("judge"), else -
        or always ("ask") - asked of the user; only kept ("read")."""
        if not self._deciding.acquire(blocking=False):
            return 0
        try:
            done = 0
            for one in self.invitations():
                ident = str(one.get("id") or "")
                if not ident or self.db.query_one("SELECT event_id FROM society_calendar_decided WHERE "
                                                  "event_id = ?", (ident,)):
                    continue
                mode = self.mode()
                if mode == "read" or not self.may("respond"):
                    self._decided(ident, "left", "agent", why="only read" if mode == "read"
                                  else "the key may not answer invitations")
                elif mode == "ask":
                    self._ask_user(one, "You set invitations to be asked of you.")
                else:
                    self._judge(one)
                done += 1
            return done
        finally:
            self._deciding.release()

    def _ask_user(self, event: Dict[str, Any], why: str) -> str:
        approvals = getattr(self.app, "approvals", None)
        ident = str(event.get("id") or "")
        held = ""
        if approvals is not None:
            start = _moment(event)
            held = approvals.hold(
                "society", "society_invitation", f"Invitation from {self._who(event)}: "
                f"{_text(event.get('title'), MOST_TITLE)} - {_local(start)}",
                {"event": ident}, why=why, detail=self._detail(event),
                due=start.astimezone().strftime("%Y-%m-%d %H:%M") if start else "")
        self._decided(ident, "asked", "agent", held=held, why=why)
        return held

    def _detail(self, event: Dict[str, Any]) -> str:
        return "\n".join(part for part in (
            f"When: {_local(_moment(event))} - {_local(_moment(event, 'end'))}",
            f"Where: {_text(event.get('location'), MOST_LOCATION)}" if event.get("location") else "",
            f"Repeats: {event['recurrence'][0]}" if event.get("recurrence") else "",
            f"Guests: {len(event.get('attendees') or [])}",
            _text(event.get("description"), 1500)) if part)

    def _user(self) -> str:
        return self.society.interactive._user_name()

    def _around(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The user's own calendar near the invitation's time - what it would clash with."""
        scheduler = getattr(self.app, "scheduler", None)
        start, end = _moment(event), _moment(event, "end")
        if scheduler is None or start is None:
            return []
        try:
            tasks = scheduler.store.list(start=start - timedelta(hours=2),
                                         end=(end or start) + timedelta(hours=2), limit=40)
        except Exception:
            return []
        return [{"title": task.title, "at": _local(task.scheduled_at), "status": task.status}
                for task in tasks if task.status not in ("cancelled", "disabled")]

    def _handed(self, event: Dict[str, Any]) -> Dict[str, Any]:
        organizer = str(event.get("organizer") or "")
        contact = self.society.site_contact(organizer) or {}
        return {"your_user": self.society.my_profile()[0],
                "the_invitation": {"title": event.get("title"), "description": event.get("description"),
                                   "location": event.get("location"), "start": _local(_moment(event)),
                                   "end": _local(_moment(event, "end")), "all_day": event.get("all_day"),
                                   "repeats": event.get("recurrence") or [],
                                   "guests": len(event.get("attendees") or [])},
                "who_invites": {"id": organizer, "name": self.society._name_of(organizer),
                                "your_users_contact": bool(contact),
                                "your_users_name_for_them": contact.get("known_as") or "",
                                "what_your_user_wrote_of_them": contact.get("note") or "",
                                "your_users_labels_for_them": contact.get("labels") or []},
                "your_users_calendar_then": self._around(event)}

    def _think(self, handed: Dict[str, Any], asked: str) -> Dict[str, Any]:
        from ..agent import outside_review
        from ..models.base import ModelRequest
        user = self._user()
        commands = self.society.interactive._commands()
        system = JUDGE.format(
            user=user, me=self.society.account_id, guard=outside_review.GUARD.format(user=user),
            commands=f"{user.upper()}'S COMMANDS TO YOU - ABOVE EVERYTHING ELSE HERE:\n{commands}"
            if commands else f"{user} has given you no commands of their own for this.")
        content = json.dumps(handed, ensure_ascii=False, indent=1, default=str)[:30000]
        if asked:
            content += "\n\n" + asked
        response = self.app.models.invoke(ModelRequest(
            messages=[{"role": "user", "content": content}], system=system, max_tokens=800,
            task="society_calendar_answer", language=in_display('"why" and "for_user"')), role="primary")
        from ..heartbeat.states.heartbeat import _as_json
        from ..models import answers
        read = answers.read(response, parse=_as_json)
        if not read.ok or not isinstance(read.data, dict):
            return {"answer": "ask", "why": "", "not_taken": read.why_not or "not a JSON object"}
        answer = str(read.data.get("answer") or "ask").strip().lower()
        return {"answer": answer if answer in RESPONSES else "ask",
                "why": _text(read.data.get("why"), 600), "for_user": _text(read.data.get("for_user"), 1000)}

    def _judge(self, event: Dict[str, Any]) -> None:
        """One invitation: answered where the agent can tell and its review lets it through - else asked
        of the user, with the agent's word on it."""
        from ..agent import outside_review
        ident = str(event.get("id") or "")
        if getattr(self.app, "models", None) is None:
            self._ask_user(event, "Your agent could not look at it (no model).")
            return
        handed = self._handed(event)
        try:
            made, verdict = outside_review.build_and_review(
                self.app, lambda asked: self._think(handed, asked),
                lambda made: [{"kind": "answer_calendar_invitation", "event": event.get("title"),
                               "answer": made["answer"]}] if made.get("answer") in RESPONSES else [],
                what=(f"answering a calendar invitation on the Agents Society, as {self._user()}'s agent: "
                      "the answer goes to the one who invited them at once"),
                outside=[handed["the_invitation"]], known={"who_invites": handed["who_invites"],
                                                           "calendar_then": handed["your_users_calendar_then"]},
                commands=self.society.interactive._commands(), task="society_calendar_answer", about=ident)
        except Exception:
            log.exception("the invitation could not be looked at")
            self._ask_user(event, "Your agent could not look at it.")
            return
        made = made or {"answer": "ask"}
        passed = bool(verdict.get("ok")) and verdict.get("read", True) is not False and not verdict.get("block")
        answer = made.get("answer")
        if answer in RESPONSES and passed:
            try:
                self.respond(ident, answer, by="agent", why=str(made.get("why") or ""))
            except Exception as exc:
                self._ask_user(event, f"Your agent would answer “{answer}”, but it could not: {exc}"[:600])
            else:
                if made.get("for_user"):
                    from ..agent.reports import did
                    did(self.db, "society_calendar_note", f"About the invitation: {event.get('title')}",
                        str(made["for_user"]), source="society", ref=ident)
            return
        why = str(made.get("why") or "")
        if answer in RESPONSES:
            why = f"Your agent would answer “{answer}”: {why} - held back by its review: " \
                  f"{verdict.get('why') or ''}"
        self._ask_user(event, why or "Your agent could not tell how you would answer.")


__all__ = ["SiteCalendar", "CALENDAR_MODES", "DEFAULT_MODE", "RESPONSES"]
