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
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ..platform_layer import get_platform
from .events.models import Notification, iso, json_dump

log = logging.getLogger(__name__)

#: The kind of notification the agent raises of its own accord, as opposed to
#: the ones the system raises about itself (a failure, a sign-in, a warning).
#: Only these carry a template: the others are not decisions the user judges.
PROACTIVE = "proactive"


def _templates():
    from ..agent.feedback.templates import ALL_TEMPLATES, REMINDER, template_of
    return ALL_TEMPLATES, REMINDER, template_of


ALL_TEMPLATES, REMINDER, template_of = _templates()


class NotificationService:
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
    ) -> Optional[Notification]:
        if not bool(self.config.get("notifications_enabled", True)) and not force:
            return None
        metadata = dict(metadata or {})
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

    def mark_read(self, notification_id: str) -> None:
        self.db.execute("UPDATE notifications SET read = 1 WHERE id = ?", (notification_id,))

    def mark_all_read(self) -> None:
        self.db.execute("UPDATE notifications SET read = 1 WHERE read = 0")

    def purge_older_than(self, days: int = 30) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cursor = self.db.execute(
            "DELETE FROM notifications WHERE created_at < ?", (iso(cutoff),)
        )
        return cursor.rowcount if cursor else 0
