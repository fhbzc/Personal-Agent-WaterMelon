"""Notification subsystem with deduplication (sections 87, 88, 91).

**The agent speaks in its own bubble.** Nothing here is handed to the
operating system's notification centre: one of this agent's interruptions is a
template with options attached, and a system notification can carry none of
them. Sending both put the same words on screen twice - once answerable, once
not - and the one the user reached first was usually the one they could do
nothing with.


Data Heartbeat failures must be visible, but a heartbeat that runs every five
minutes must not produce an identical warning every five minutes.  The service
therefore deduplicates by key and re-notifies only when something materially
changed: a different error type, a newly required user action, or the
configured reminder interval elapsing.
"""

from __future__ import annotations

import json
import re
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..platform_layer import get_platform
from .events.models import Notification, iso, json_dump

log = logging.getLogger(__name__)

#: WHAT IT CAME FROM WHEN NO TRIGGER OF THE MONITOR SENT IT: who put it on the calendar, or that.
NOT_A_TRIGGER = {"heartbeat": "None: the heartbeat's round", "agent": "None: the chat",
                 "user": "None: you added it", "scheduler": "None: scheduled on the calendar"}
NOT_A_TRIGGER_AT_ALL = "None: sent by the application"


#: WHAT KIND EACH IS, as the waiting list groups them and its line counts them (the user,
#: 2026-11-07: "return back to old project 和晚间summary改成 summary类型（不同于notification和
#: recommendation)"; "N waiting的时候，要写清楚category"). In this order; each counted in these words.
CATEGORIES = ("Recommended content", "Summaries", "Reminders", "Other")
#: THE LINE BESIDE THE AVATAR, a row a kind in this order (the user, 2026-11-07: "应该是分行 X
#: notification / X summary / X recommendation ... 按照这个顺序，每行不同的颜色"): its name, the
#: groups of the list it counts, its words.
LINE_KINDS = (("notifications", ("Reminders", "Other"), ("{0} notification", "{0} notifications")),
              ("summaries", ("Summaries",), ("{0} summary", "{0} summaries")),
              ("recommendations", ("Recommended content",),
               ("{0} recommendation", "{0} recommendations")))


def category_of(row: Dict[str, Any]) -> str:
    """A notification's kind: a summary, a recommendation, a reminder or another."""
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata or "{}")
        except ValueError:
            metadata = {}
    if metadata.get("summary"):
        return "Summaries"
    if metadata.get("recommended"):
        return "Recommended content"
    return "Reminders" if row.get("kind") == "proactive" else "Other"


def _is_report_summary(metadata: Any) -> bool:
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata or "{}")
        except ValueError:
            return False
    metadata = metadata if isinstance(metadata, dict) else {}
    return bool(metadata.get("approvals") or metadata.get("agent_did"))


def waiting_lines(counts: Dict[str, int], words: Any) -> List[Tuple[str, str]]:
    """The rows beside the avatar - (kind, "2 notifications") - for each kind something waits of,
    in LINE_KINDS' order; `words` puts each in the display language."""
    out: List[Tuple[str, str]] = []
    for kind, groups, said in LINE_KINDS:
        many = sum(int(counts.get(group) or 0) for group in groups)
        if many:
            out.append((kind, words(said[0 if many == 1 else 1]).replace("{0}", str(many))))
    return out


def told_from(metadata: Any) -> str:
    """WHICH TRIGGER OF THE MONITOR A NOTIFICATION CAME FROM (the user, 2026-11-06: said on each one),
    or that none did and what did instead."""
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata or "{}")
        except ValueError:
            metadata = {}
    metadata = metadata if isinstance(metadata, dict) else {}
    said = metadata.get("monitor") or {}
    named = said.get("shown_as") or said.get("activator") or metadata.get("trigger")
    if named:
        return str(named)
    return NOT_A_TRIGGER.get(str(metadata.get("made_by") or metadata.get("source") or ""),
                             NOT_A_TRIGGER_AT_ALL)

def _a_signal_said(one: str) -> str:
    """A signal as its trigger saw it, in plain words: "present True" - at the computer; "hour
    20.52" - at 20:31; "worked_today_minutes 1021" - worked today 1021 min."""
    name, _, rest = one.partition(" ")
    value, _, after = rest.partition(" ")
    try:
        number = float(value)
    except ValueError:
        number = None
    if name == "present":
        return "at the computer" if value == "True" else "away"
    if name in ("hour", "day_hour") and number is not None:
        hours = number % 24
        return (f"at {int(hours):02d}:{int(round((hours % 1) * 60)) % 60:02d}"
                + (f" {after}" if after else ""))
    words = name.replace("_minutes", "").replace("_", " ")
    if number is not None:
        said = f"{words} {number:g} min" if name.endswith("_minutes") else f"{words} {number:g}"
        return said + (f" {after}" if after else "")
    return re.sub(r"(\d+)\.0\b", r"\1", one).replace("_", " ")


def what_set_it_off(metadata: Any) -> str:
    """WHAT EXACTLY SET IT OFF, in a line (the user, 2026-11-08: "这个为什么trigger, 要说出关键词是什么
    ... 或者关键condition是什么"): the words that matched on the screen and where, or the conditions
    of its trigger that held - "" when nothing is kept of it."""
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata or "{}")
        except ValueError:
            metadata = {}
    metadata = metadata if isinstance(metadata, dict) else {}
    context = (metadata.get("monitor") or {}).get("context") or {}
    if not isinstance(context, dict):
        return ""
    where = str((context.get("window") or {}).get("title") or "").strip()
    chain = context.get("chain") or metadata.get("chain") or {}
    hit = [str(one) for one in (chain.get("hit") or []) if str(one).strip()] \
        if isinstance(chain, dict) else []
    if hit:
        return (f"the words on the screen: {', '.join(hit[:8])}"
                + (f" (in {where[:120]})" if where else ""))
    said = []
    for one in context.get("why") or []:
        one = str(one).strip()
        if not one:
            continue
        if one.startswith("event "):
            one = one[len("event "):].replace("_", " ")
        elif one.startswith("the words: "):
            one = "the words on the screen: " + one[len("the words: "):]
        else:
            one = _a_signal_said(one)
        said.append(one)
    if not said:
        return ""
    return "; ".join(said[:6])[:300] + (f" (in {where[:120]})" if where and len(said) else "")


#: The kind of notification the agent raises of its own accord, as opposed to
#: the ones the system raises about itself (a failure, a sign-in, a warning).
#: Only these carry a template: the others are not decisions the user judges.
PROACTIVE = "proactive"

#: TWO LEVELS (the user, 2026-11-06): shown at once, or kept in the waiting list - a line beside the
#: avatar says how many, and it is opened by hand. A notification pops unless its sender says
#: otherwise, and what goes wrong and what the agent is stuck on pop whatever is said.
POP, COLLAPSE = "pop", "collapse"
ALWAYS_POP = ("error", "warning", "approval")
_LEVEL_OF_ROW = "CASE WHEN json_valid(metadata) THEN json_extract(metadata, '$.level') END"


def _templates():
    from ..agent.feedback.templates import ALL_TEMPLATES, REMINDER, template_of
    return ALL_TEMPLATES, REMINDER, template_of


ALL_TEMPLATES, REMINDER, template_of = _templates()


class NotificationService:
    #: What the user is doing now, kept with what is shown (set by the application).
    moment_now: Any = None

    def __init__(self, db: Any, config: Any, bus: Any = None, platform: Any = None) -> None:
        self.db = db
        self.config = config
        self.bus = bus
        #: Kept because callers pass it and the rest of the application asks
        #: this service what platform it is on. Nothing here sends a system
        #: notification through it any more.
        self.platform = platform or get_platform()
        self._lock = threading.RLock()
        self._last: Dict[str, Dict[str, Any]] = {}
        #: Where an interruption's reply options come from (section 52.6).
        #:
        #: Attached after construction, because the question store is built
        #: later than this service. Without it a proactive notification is
        #: still delivered - an interruption the user cannot answer is better
        #: than one they never receive - but it is the degraded case, and it
        #: says so in the log.
        self.questions: Any = None

    # -- policy -------------------------------------------------------------
    def _in_quiet_hours(self) -> bool:
        raw = str(self.config.get("quiet_hours") or "").strip()
        if not raw or "-" not in raw:
            return False
        try:
            start_s, end_s = raw.split("-", 1)
            start = datetime.strptime(start_s.strip(), "%H:%M").time()
            end = datetime.strptime(end_s.strip(), "%H:%M").time()
        except ValueError:
            return False
        now = datetime.now().time()
        if start <= end:
            return start <= now <= end
        return now >= start or now <= end

    def _should_suppress(self, dedupe_key: Optional[str], signature: str) -> bool:
        if not dedupe_key:
            return False
        cooldown = int(self.config.get("notification_cooldown_seconds", 3600))
        with self._lock:
            previous = self._last.get(dedupe_key)
        if previous is None:
            return False
        if previous["signature"] != signature:
            return False  # section 91: the error type changed - notify again
        elapsed = (datetime.now(timezone.utc) - previous["at"]).total_seconds()
        return elapsed < cooldown

    # -- through the Monitor --------------------------------------------------
    #: THE MONITOR'S WAY TO SAY IT (the user, 2026-11-13: "所有的通知必须在monitor里，收到monitor的统一调配"): (builtin,
    #: title, message, **said) - set by the application to `Monitor.relay`. Nothing else is said but
    #: through it: `notify` is the Monitor's own.
    relay: Optional[Any] = None

    def system(self, title: str, message: str, *, kind: str = "warning", level: Optional[str] = None,
               startup: bool = False, **said: Any) -> Any:
        """WHAT THE USER MUST KNOW OF THE APPLICATION ITSELF ("系统类的可以保留") - signed out, a model down, the
        agent stopped, an update - through the Monitor's "The application itself": its switch, its
        silences. `level` "collapse": rather waiting. Without a Monitor nothing is said - but the
        agent not starting at all (`startup`), which no Monitor is there to say."""
        relay = self.relay
        if relay is None:
            if startup:
                return self.notify(title, message, kind=kind, **said)
            log.info("no Monitor to say it through: %s", title)
            return None
        return relay("system", title, message, kind=kind, wait=level == COLLAPSE, **said)

    # -- api ----------------------------------------------------------------
    def notify(
        self,
        title: str,
        message: str,
        *,
        kind: str = "info",
        dedupe_key: Optional[str] = None,
        signature: Optional[str] = None,
        action_hint: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        force: bool = False,
        template: Optional[str] = None,
        question: Optional[Dict[str, Any]] = None,
        level: str = POP,
    ) -> Optional[Notification]:
        if not bool(self.config.get("notifications_enabled", True)) and not force:
            return None
        metadata = dict(metadata or {})
        metadata["level"] = COLLAPSE if level == COLLAPSE and kind not in ALWAYS_POP else POP
        if self.moment_now is not None:
            try:
                metadata.setdefault("moment", self.moment_now())
            except Exception:
                log.debug("what the user was doing could not be read", exc_info=True)
        if kind == PROACTIVE:
            # Section 52.6, as amended: an interruption the agent chose to
            # raise is one of the two templates, so that it arrives with the
            # options for answering it. A notification with no way to reply is
            # how "stop reminding me" became impossible to say.
            template = self._template_for(title, template)
            metadata["template"] = template
        signature = signature or f"{kind}:{message[:120]}"
        if not force and self._should_suppress(dedupe_key, signature):
            log.debug("suppressed duplicate notification %s", dedupe_key)
            return None

        notification = Notification(
            title=title, message=message, kind=kind, dedupe_key=dedupe_key,
            action_hint=action_hint, metadata=metadata,
        )
        if kind == PROACTIVE:
            # Before the row is written and before anything is told about it:
            # the id of the question is part of what this notification *is*,
            # and whatever draws the notification needs it to offer the
            # buttons. The bubble already knew how to read it; nothing had
            # ever written it, so every interruption fell back to a single
            # "Got it" that recorded nothing and settled nothing.
            asked = self._offer_a_reply(notification, template or REMINDER,
                                        question or {})
            if asked:
                notification.metadata["question_id"] = asked
        self.db.execute(
            """
            INSERT INTO notifications
                (id, created_at, kind, title, message, dedupe_key, action_hint, metadata, read)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                notification.id, iso(notification.created_at), notification.kind,
                notification.title, notification.message, notification.dedupe_key,
                notification.action_hint, json_dump(notification.metadata),
            ),
        )
        if dedupe_key:
            with self._lock:
                self._last[dedupe_key] = {"signature": signature,
                                          "at": datetime.now(timezone.utc)}

        quiet = self._in_quiet_hours() and kind not in ("error",)
        # Nothing is sent to the operating system's notification centre.
        #
        # An interruption from this agent is one of three templates and
        # arrives with the options for answering it (section 52.6). A system
        # notification carries no options, cannot be answered, and is drawn
        # by something this application does not control - so sending one
        # meant the same words appearing twice, once answerable and once not,
        # which reads as two reminders and is one. The bubble beside the
        # character is where the agent speaks.
        if self.bus is not None:
            self.bus.publish("notification.created", {**notification.to_dict(), "quiet": quiet})
        return notification

    # -- the reply that comes with it ---------------------------------------
    @staticmethod
    def _template_for(title: str, template: Optional[str]) -> str:
        """Which of the two this is, insisting on one of them.

        A caller that names neither is not refused - dropping the
        interruption would lose the thing the agent was trying to say - but
        it is recorded as a reminder, which is what an unsolicited note is,
        and the omission is logged rather than passed over.
        """
        resolved = template_of(template or "")
        if resolved in ALL_TEMPLATES:
            return resolved
        if template:
            log.warning("notification %r asked for template %r, which is not one "
                        "of %s; treating it as a reminder",
                        title, template, ALL_TEMPLATES)
        else:
            log.warning("notification %r named no template; treating it as a "
                        "reminder", title)
        return REMINDER

    def _offer_a_reply(self, notification: Notification, template: str,
                       question: Dict[str, Any]) -> str:
        """Raise the question whose options answer this interruption.

        Returns its id, which the notification carries so that whatever draws
        it - the bubble beside the character, the card in the web view - can
        offer that question's own buttons rather than a bare acknowledgement.
        """
        if self.questions is None:
            # The usual case now: no question is raised, and the bubble and
            # the page answer it with their own buttons - Got it, Remind me
            # later (a reminder) and Bad notification.
            log.debug("%r is answered with the bubble's own buttons",
                      notification.title)
            return ""
        from ..agent.feedback.store import TaskQuestion
        from ..agent.feedback.templates import options_for

        task_id = str(question.get("task_id")
                      or notification.metadata.get("task_id") or "")
        try:
            if task_id:
                standing = self.questions.pending_for(task_id, template)
                if standing is not None:
                    # Already asked about this task and not yet answered.
                    # Asking again would stack identical questions for one
                    # interruption - but this notification still points at
                    # the one already standing, so it can still be answered.
                    return standing.id
            delay = question.get("delay_seconds")
            asked = self.questions.ask(TaskQuestion(
                task_id=task_id,
                kind=template,
                question=question.get("question") or notification.title,
                task_detail=str(question.get("task_detail") or notification.message),
                arrangement=str(question.get("arrangement") or ""),
                metadata={"notification_id": notification.id,
                          "options": [o.to_dict() for o in options_for(
                              template, int(delay) if delay else None)]},
            ))
            # ``ask`` may or may not hand the stored question back; either way
            # the id is the one that was put on it.
            return getattr(asked, "id", "") or ""
        except Exception:        # an interruption must still arrive
            log.exception("could not offer a reply to notification %s",
                          notification.id)
            return ""

    def clear_dedupe(self, dedupe_key: str) -> None:
        """Called on recovery (section 92) so the next problem notifies again."""
        with self._lock:
            self._last.pop(dedupe_key, None)

    def recent(self, limit: int = 50, unread_only: bool = False) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM notifications"
        if unread_only:
            sql += " WHERE read = 0"
        sql += " ORDER BY created_at DESC LIMIT ?"
        rows = self.db.query(sql, (limit,))
        for row in rows:
            row["read"] = bool(row["read"])
        return rows

    def waiting(self, limit: int = 200) -> List[Dict[str, Any]]:
        """What is kept in the waiting list and not yet dealt with, the newest first."""
        rows = self.db.query(f"SELECT * FROM notifications WHERE read = 0 AND {_LEVEL_OF_ROW} = ? "
                             "ORDER BY created_at DESC LIMIT ?", (COLLAPSE, int(limit)))
        for row in rows:
            row["read"] = False
            try:
                row["metadata"] = json.loads(row.get("metadata") or "{}")
            except (TypeError, ValueError):
                row["metadata"] = {}
        return rows

    def waiting_by_category(self) -> Dict[str, int]:
        """How many wait, of each kind (`category_of`)."""
        out: Dict[str, int] = {}
        for row in self.waiting(1000):
            name = category_of(row)
            out[name] = out.get(name, 0) + 1
        return out

    def waiting_count(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM notifications WHERE read = 0 AND "
                                f"{_LEVEL_OF_ROW} = ?", (COLLAPSE,))
        return int(row["n"]) if row is not None else 0

    def get(self, notification_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one("SELECT * FROM notifications WHERE id = ?",
                                (notification_id,))
        if row is None:
            return None
        row["read"] = bool(row["read"])
        try:
            row["metadata"] = json.loads(row.get("metadata") or "{}")
        except (TypeError, ValueError):
            row["metadata"] = {}
        return row

    def _told_read(self, ids: List[str]) -> None:
        """READ, SAID AT ONCE (the user, 2026-11-13: "这个1 summary还是要很久才关掉"): whoever read it -
        the page, the report, a button - the line beside the avatar is drawn again now, not at its
        next look."""
        if self.bus is not None and ids:
            try:
                self.bus.publish("notification.read", {"ids": list(ids)})
            except Exception:                    # pragma: no cover - drawn at the next look
                pass

    def mark_read(self, notification_id: str) -> None:
        self.db.execute("UPDATE notifications SET read = 1 WHERE id = ?", (notification_id,))
        self._told_read([notification_id])

    def mark_all_read(self) -> None:
        self.db.execute("UPDATE notifications SET read = 1 WHERE read = 0")
        self._told_read(["*"])

    def read_report_summaries(self) -> int:
        """THE REPORT OPENED IS ITS NOTIFICATIONS READ: those that say what waits for approval, and
        what the agent did - each told in the report, read there. How many."""
        ids = [row["id"] for row in self.db.query(
            "SELECT id, metadata FROM notifications WHERE read = 0")
            if _is_report_summary(row.get("metadata"))]
        for ident in ids:
            self.db.execute("UPDATE notifications SET read = 1 WHERE id = ?", (ident,))
        self._told_read(ids)
        return len(ids)

    def purge_older_than(self, days: int = 30) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cursor = self.db.execute(
            "DELETE FROM notifications WHERE created_at < ?", (iso(cutoff),)
        )
        return cursor.rowcount if cursor else 0
