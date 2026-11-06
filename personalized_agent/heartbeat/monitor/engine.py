"""THE MONITOR: the Activators run by code on the real-time signals.

Every few seconds (`monitor_tick_seconds`) it looks - the windows written,
the signals read (signals.py) - and runs each Activator that looks at the
signals; what appears on the screen is matched as it comes, in the listener's
own thread, by the watcher that already did it for the two calendar rules
(heartbeat/core/triggers.py): only what is NEW in a window, a window looked at
once in a while, nothing while nobody is there, never the agent's own windows.

What holds is done - said, asked of the model, or queued for the heartbeat's
step_monitor - within limits code keeps, whatever an Activator says:

- each Activator's cooldown and most a day; "Not now" waits twice its
  cooldown, "Not today" stops it until tomorrow;
- recommendations and breaks: none in the quiet hours, none while the user is
  away; breaks at most `monitor_most_a_day` a day, `monitor_least_gap_minutes`
  after anything said. RECOMMENDATIONS HAVE NO LIMIT (the user, 2026-11-05: no
  number a day, no gap): the model is told how many there have been, and judges;
- one model asked at a time.

TWO LAYERS (the user, 2026-11-04): Activators, which set something off, and
BLOCKERS (the rules of quiet), which say when nothing is shown, for EVERY reminder
whoever raised it. What the Monitor would say of its own accord is not said: its
moment has passed. Anything else - a calendar reminder, a scam warning, a run's
result - is still made, and waits: the bubble and the page ask `blocker` before
they show it, and show it when the blocker is over. Meanwhile a small line says how
many wait, and the user can open them by hand.

EVERY NOTIFICATION KEEPS WHY IT WAS MADE (the user, 2026-11-03: "模型要记录
notification决定的context"): the Activator, what it saw - the window, the words,
the signals - and the model's reason, with the notification; a Bad
notification on it is kept whole (`on_bad`) and given back to the next
decisions that are like it (decide.py).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ...common.events.models import new_id, utcnow
from . import activators as act
from .builtins import OLD_BREAK, OLD_SPAM_THENS, QUIET, SPAM, builtins
from .signals import Signals

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS monitor_firings (
    id            TEXT PRIMARY KEY,
    activator_id  TEXT NOT NULL,
    at            TEXT NOT NULL,
    how           TEXT NOT NULL,              -- tick | text
    score         REAL NOT NULL DEFAULT 0,
    why           TEXT NOT NULL DEFAULT '',
    action        TEXT NOT NULL DEFAULT '',   -- notify | ask_model:<handler> | queue | held
    acted         INTEGER NOT NULL DEFAULT 0, -- something was said, asked or queued
    outcome       TEXT NOT NULL DEFAULT '{}',
    context       TEXT NOT NULL DEFAULT '{}',
    question_id   TEXT NOT NULL DEFAULT '',
    notification_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_monitor_firings ON monitor_firings(activator_id, at);
CREATE TABLE IF NOT EXISTS monitor_hits (
    id            TEXT PRIMARY KEY,
    activator_id  TEXT NOT NULL,
    firing_id     TEXT NOT NULL DEFAULT '',
    at            TEXT NOT NULL,
    score         REAL NOT NULL DEFAULT 0,
    context       TEXT NOT NULL DEFAULT '{}',
    judged_at     TEXT,
    decision      TEXT
);
CREATE INDEX IF NOT EXISTS idx_monitor_hits ON monitor_hits(judged_at, at);
CREATE TABLE IF NOT EXISTS monitor_bad (
    id              TEXT PRIMARY KEY,
    at              TEXT NOT NULL,
    notification_id TEXT NOT NULL,
    activator_id    TEXT NOT NULL DEFAULT '',
    said            TEXT NOT NULL DEFAULT '',  -- the user's own words, if any
    context         TEXT NOT NULL DEFAULT '{}' -- the notification and why it was made
);
"""

#: Groups held to the limits of what interrupts on the agent's own account.
SPOKEN_GROUPS = ("recommend", "rest")
#: A hit not judged by the step for this long is let go.
HIT_EXPIRES_HOURS = 12
#: What a firing keeps of a window's words.
MOST_TEXT = 4000
#: Where a window is, for keywords' "where".
BROWSERS = ("chrome", "msedge", "edge", "firefox", "brave", "safari", "opera", "vivaldi", "arc")
MAIL_PROGRAMS = ("outlook", "thunderbird", "mail", "foxmail", "spark", "airmail")
MAIL_TITLES = ("gmail", "outlook", "inbox", "收件箱", "邮件", "邮箱", "受信トレイ", "받은편지함",
               "posteingang", "boîte de réception", "bandeja de entrada", "posta in arrivo",
               "входящие", "mail")
TERMINALS = ("terminal", "powershell", "pwsh", "cmd", "conhost", "wezterm", "alacritty",
             "iterm", "konsole", "mintty")


def where_of(config: Any, application: str, title: str) -> str:
    """page | email | chat | terminal | other - for keywords' "where"."""
    app, said = str(application or "").lower(), str(title or "").lower()
    if any(name in app for name in MAIL_PROGRAMS) or (
            any(name in app for name in BROWSERS) and any(word in said for word in MAIL_TITLES)):
        return "email"
    if any(name in app for name in TERMINALS):
        return "terminal"
    try:
        from ...listener.providers import fast_reading
        if fast_reading.is_conversation(config, application, title):
            return "chat"
    except Exception:
        pass
    if any(name in app for name in BROWSERS):
        return "page"
    return "other"


def _filled(text: str, signals: Dict[str, Any]) -> str:
    """{signal_name} in what is said, put in."""
    def one(match: "re.Match[str]") -> str:
        value = signals.get(match.group(1))
        if value is None:
            return match.group(0)
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return str(value)
    return re.sub(r"\{([a-z_]+)\}", one, str(text or ""))


def words(text: str, config: Any) -> str:
    """One of the application's own texts (a built-in Activator's, a button's)
    in the display language - what the model writes is in it already."""
    try:
        from ...common.interface_words import shown
        return shown(text, config)
    except Exception:
        return text


class ActivatorRule:
    """One text Activator, as the watcher (triggers.py) runs a rule: its words
    matched on what is new in a window, and - once the window has settled -
    what it does."""

    def __init__(self, monitor: "Monitor", activator: Dict[str, Any]) -> None:
        self.monitor = monitor
        self.activator = activator
        self.name = activator["id"]
        self.why = activator.get("purpose") or activator["name"]
        self.action = "activator"
        #: Its own cooldown in a window (triggers.mark).
        self.cooldown = float(activator["cooldown_minutes"]) * 60.0
        self._last: Dict[str, Any] = {}

    def allowed(self) -> bool:
        return self.monitor.allowed(self.activator)

    def match(self, title: str, text: str, where: str) -> bool:
        ok, score, why = act.evaluate(self.activator["when"], {
            "title": title, "text": text, "where": where,
            "signals": self.monitor.latest, "events": ("text",)})
        if ok:
            self._last = {"score": score, "why": why, "where": where}
        return ok and act.tier_for(self.activator, score) is not None

    def matches(self, title: str, text: str) -> bool:      # the watcher's older call
        return self.match(title, text, "any")

    def act(self, window: str, application: str, said: str, fresh: str) -> Dict[str, Any]:
        last = dict(self._last)
        return self.monitor.fire(self.activator, last.get("score", 0.0), last.get("why", []),
                                 how="text", window={"title": window, "application": application,
                                                     "text": said, "fresh": fresh,
                                                     "where": last.get("where", "")})


class Monitor:
    """The Activators, run; what they did, kept."""

    def __init__(self, db: Any, config: Any, signals: Signals, notifications: Any = None,
                 questions: Any = None, decider: Any = None, heartbeat: Any = None,
                 opener: Optional[Callable[[str], Any]] = None,
                 pictures: Optional[Path] = None) -> None:
        self.db = db
        self.config = config
        self.signals = signals
        self.notifications = notifications
        self.questions = questions
        self.decider = decider
        self.heartbeat = heartbeat
        self.opener = opener or webbrowser.open
        #: Where the notifications' pictures are kept (pictures.py).
        self.pictures = pictures
        #: Set by the application: what the user added to their collection.
        self.keeper: Optional[Callable[[Dict[str, Any]], None]] = None
        self.store = act.ActivatorStore(db)
        with self.db.write() as conn:
            conn.executescript(SCHEMA)
        self.latest: Dict[str, Any] = {}
        #: The blocker that holds now, by name - "" when none does. Kept at each
        #: look, so that the window thread can ask without reading the database.
        self.blocker = ""
        #: How many reminders wait behind it, as the bubble's owner says.
        self.reminders_waiting = 0
        self.last_error = ""
        self._lock = threading.RLock()
        self._asking = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._rules: Dict[str, ActivatorRule] = {}
        try:
            self.store.seed(builtins())
            self.store.reword("break", OLD_BREAK)
            self.store.seed_quiet(QUIET)
            for old in OLD_SPAM_THENS:
                self.store.retier("spam_risk", old, act.check(SPAM, builtin=True)[0]["then"])
        except Exception:                              # pragma: no cover - seeded next start
            log.warning("the built-in Activators could not be written", exc_info=True)

    # -- switches -----------------------------------------------------------------------
    def enabled(self) -> bool:
        from ...common.modules import enabled as module_on
        return module_on(self.config, "monitor")

    def allowed(self, activator: Dict[str, Any]) -> bool:
        """Whether this one runs now: its switch, and its module's."""
        from ...common.modules import enabled as module_on
        if not activator.get("enabled", True):
            return False
        if activator.get("group") == "calendar":
            return module_on(self.config, "calendar")
        if activator.get("group") == "recommend" and not module_on(self.config, "recommendation"):
            return False
        return self.enabled()

    def text_rules(self) -> List[ActivatorRule]:
        """The text Activators, as rules for the watcher - made again when one changed."""
        out = []
        for one in self.store.list():
            if not one["text_driven"]:
                continue
            rule = self._rules.get(one["id"])
            if rule is None or rule.activator.get("updated_at") != one["updated_at"]:
                rule = self._rules[one["id"]] = ActivatorRule(self, one)
            out.append(rule)
        return out

    # -- looking ------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(5)

    def _loop(self) -> None:
        while not self._stop.wait(max(5.0, float(self.config.get("monitor_tick_seconds", 10)))):
            try:
                self.tick()
            except Exception as exc:                   # the next look tries again
                self.last_error = str(exc)[:200]
                log.warning("the Monitor could not look", exc_info=True)

    def tick(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """One look: the windows written, the signals read, each Activator on
        them run."""
        if not self.enabled():
            self.blocker = ""
            return []
        when = now or datetime.now(timezone.utc)
        self.signals.record_if_due(when)
        snapshot = self.signals.snapshot(when)
        self.latest = snapshot
        self.blocker = self._quieted()
        fired = []
        for one in self.store.list():
            if one["text_driven"] or not self.allowed(one):
                continue
            ok, score, why = act.evaluate(one["when"], {"signals": snapshot,
                                                        "events": snapshot.get("events", ())})
            if ok:
                fired.append(self.fire(one, score, why, how="tick", now=when))
        return fired

    # -- limits -------------------------------------------------------------------------
    def _kv(self, key: str) -> str:
        row = self.db.query_one("SELECT value FROM kv WHERE key = ?", (key,))
        return str(row["value"]) if row and row["value"] is not None else ""

    def _set_kv(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO "
                        "UPDATE SET value = excluded.value", (key, value))

    def _held(self, activator: Dict[str, Any], how: str, when: datetime) -> str:
        """Why it may not act now - "" when it may."""
        local = when.astimezone()
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        ident = activator["id"]
        if self._kv(f"monitor.off_until.{ident}") > local.date().isoformat():
            return "not today - the user said so"
        until = self._kv(f"monitor.snoozed.{ident}")
        if until and until > when.isoformat():
            return "not now - the user said so"
        rows = self.db.query("SELECT at FROM monitor_firings WHERE activator_id = ? AND acted = 1 "
                             "AND at >= ? ORDER BY at DESC",
                             (ident, midnight.astimezone(timezone.utc).isoformat()))
        if activator.get("group") != "recommend" and len(rows) >= int(activator["most_per_day"]):
            return f"{activator['most_per_day']} already today"
        if how == "tick":
            last = self.db.query_one("SELECT MAX(at) AS t FROM monitor_firings WHERE "
                                     "activator_id = ? AND acted = 1", (ident,))
            if last and last["t"] and when - datetime.fromisoformat(last["t"]) < timedelta(
                    minutes=float(activator["cooldown_minutes"])):
                return "in its cooldown"
        if activator.get("group") in SPOKEN_GROUPS:
            if not self.latest.get("present", True):
                return "the user is away"
            quieted = self._quieted()
            if quieted:
                return f"a rule of quiet: {quieted}"
            quiet = getattr(self.notifications, "_in_quiet_hours", None)
            if callable(quiet) and quiet():
                return "quiet hours"
            spoken = self.db.query(
                "SELECT f.at FROM monitor_firings f JOIN monitor_activators a ON a.id = "
                "f.activator_id WHERE f.acted = 1 AND f.at >= ? AND a.data LIKE ? "
                "ORDER BY f.at DESC", (midnight.astimezone(timezone.utc).isoformat(), '%"group": "re%'))
            spoken = [row for row in spoken]
            if activator.get("group") == "rest":
                rests = self.db.query_one(
                    "SELECT COUNT(*) AS n FROM monitor_firings f JOIN monitor_activators a ON a.id "
                    "= f.activator_id WHERE f.acted = 1 AND f.at >= ? AND a.data LIKE ?",
                    (midnight.astimezone(timezone.utc).isoformat(), '%"group": "rest"%'))
                if int(rests["n"]) >= int(self.config.get("monitor_most_a_day", 8)):
                    return "the most for a day already said"
                if spoken and when - datetime.fromisoformat(spoken[0]["at"]) < timedelta(
                        minutes=float(self.config.get("monitor_least_gap_minutes", 20))):
                    return "said something too lately"
        return ""

    def _quieted(self) -> str:
        """The rule of quiet that applies now, if one does."""
        moment = {"signals": self.latest or {}, "events": (self.latest or {}).get("events") or []}
        for rule in self.store.quiet_rules():
            if act.evaluate(rule["when"], moment)[0]:
                return str(rule["name"])
        return ""

    # -- acting -------------------------------------------------------------------------
    def fire(self, activator: Dict[str, Any], score: float, why: List[str], how: str = "tick",
             window: Optional[Dict[str, Any]] = None,
             now: Optional[datetime] = None) -> Dict[str, Any]:
        """An Activator that holds: within the limits, what its tier says done -
        and kept, with what it saw."""
        when = now or datetime.now(timezone.utc)
        tier = act.tier_for(activator, score)
        context = {"activator": {"id": activator["id"], "name": activator["name"],
                                 "group": activator["group"], "purpose": activator.get("purpose")},
                   "why": why[:20], "score": score,
                   "signals": {k: v for k, v in (self.latest or {}).items()
                               if k not in ("events", "running")},
                   "events": list((self.latest or {}).get("events") or [])}
        if window:
            context["window"] = {"title": str(window.get("title") or "")[:200],
                                 "application": str(window.get("application") or ""),
                                 "where": window.get("where", ""),
                                 "new_text": str(window.get("fresh") or "")[-MOST_TEXT:],
                                 "text": str(window.get("text") or "")[-MOST_TEXT:]}
        firing = new_id("monfire")
        held = "" if tier is not None else "no tier for this score"
        held = held or self._held(activator, how, when)
        action = "held" if held else (tier["do"] + (":" + tier.get("handler", "")
                                                   if tier["do"] == "ask_model" else ""))
        self.db.execute(
            "INSERT INTO monitor_firings (id, activator_id, at, how, score, why, action, acted, "
            "context) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (firing, activator["id"], when.isoformat(), how, score, "; ".join(why)[:600],
             action, 0 if held else 1, json.dumps(context, ensure_ascii=False, default=str)))
        if held:
            return {"firing": firing, "held": held}
        outcome: Dict[str, Any] = {}
        try:
            if tier["do"] == "notify":
                outcome = self._notify(activator, tier, context, firing)
                if tier.get("and_then"):
                    self._and_then(activator, tier["and_then"], score, outcome, context, window,
                                   how, when)
            elif tier["do"] == "queue":
                outcome = self._queue(activator, score, context, firing, when)
            else:
                outcome = self._ask(activator, tier, context, firing, window)
        except Exception as exc:
            log.warning("an Activator could not act", exc_info=True)
            outcome = {"error": str(exc)[:300]}
        self._outcome(firing, outcome)
        return dict(outcome, firing=firing)

    def _and_then(self, activator: Dict[str, Any], follow: Dict[str, Any], score: float,
                  said: Dict[str, Any], context: Dict[str, Any], window: Optional[Dict[str, Any]],
                  how: str, when: datetime) -> None:
        """CODE HAS SPOKEN; NOW A MODEL (the user, 2026-11-04: "在调model之前，就可以直接发一个
        通知 ... 然后模型之后可以精细判断再发通知"). A firing of its own, so that what
        answers the first stays with the first; not counted against its limits."""
        firing = new_id("monfire")
        context = dict(context, said_already={"title": said.get("title"),
                                              "message": said.get("message")})
        action = follow["do"] + (":" + follow.get("handler", "")
                                 if follow["do"] == "ask_model" else "")
        self.db.execute(
            "INSERT INTO monitor_firings (id, activator_id, at, how, score, why, action, acted, "
            "context) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (firing, activator["id"], when.isoformat(), how, score, "after what code said",
             action, json.dumps(context, ensure_ascii=False, default=str)))
        outcome = self._queue(activator, score, context, firing, when) if follow["do"] == "queue" \
            else self._ask(activator, follow, context, firing, window)
        self._outcome(firing, outcome)

    def _outcome(self, firing: str, outcome: Dict[str, Any]) -> None:
        self.db.execute("UPDATE monitor_firings SET outcome = ?, notification_id = ?, "
                        "question_id = ? WHERE id = ?",
                        (json.dumps(outcome, ensure_ascii=False, default=str)[:6000],
                         str(outcome.get("notification_id") or ""),
                         str(outcome.get("question_id") or ""), firing))

    def recommended_today(self) -> int:
        """How many have been recommended today: told to the model, kept in the lesson."""
        return int(self._kv(f"monitor.recommended.{datetime.now().astimezone().date()}") or 0)

    def picture_path(self, name: Any) -> Optional[Path]:
        """A kept picture, by the name the notification carries."""
        if self.pictures is None or not name:
            return None
        path = Path(self.pictures) / Path(str(name)).name
        return path if path.is_file() else None

    def say(self, title: str, message: str, context: Dict[str, Any], firing: str,
            choices: str = "", why: str = "", url: str = "", picture: str = "",
            content: bool = False, reason: str = "",
            recommended: bool = False) -> Dict[str, Any]:
        """A notification, with why it was made kept in it. `content` is what the
        model sends: answered Read detail, when it has a page, Good to know or
        Bad notification (the user, 2026-11-04). `reason` is shown with it: why the
        user is being told; `why` is kept for the model."""
        if self.notifications is None:
            return {"said": False, "why": "nothing to say it with"}
        from ...agent.feedback.templates import REMINDER, SUGGESTION
        metadata = {"monitor": {"firing": firing, "activator_id": context["activator"]["id"],
                                "activator": context["activator"]["name"],
                                "group": context["activator"]["group"], "why": why,
                                "context": context},
                    "content": content, "url": url, "picture": picture, "reason": reason,
                    "recommended": recommended}
        if recommended:
            metadata["nth_today"] = self.recommended_today() + 1
            metadata["chain"] = context.get("chain") or {}
            self._set_kv(f"monitor.recommended.{datetime.now().astimezone().date()}",
                         str(metadata["nth_today"]))
        template, question_id = REMINDER, ""
        if (choices or content) and self.questions is not None:
            from ...agent.feedback.store import TaskQuestion
            options = ([{"key": "accept", "label": "Take a break", "effect": "complete"},
                        {"key": "decline", "label": "Not now", "effect": "complete"},
                        {"key": "not_today", "label": "Not today", "effect": "complete"}]
                       if choices == "break" else
                       ([{"key": "read_detail", "label": "Read detail", "effect": "complete"}]
                        if url else [])
                       + [{"key": "good_to_know", "label": "Good to know", "effect": "complete"}]
                       + ([{"key": "add_to_collection", "label": "Add to collection",
                            "effect": "complete"}] if recommended else []))
            for option in options:
                option["label"] = words(option["label"], self.config)
            asked = self.questions.ask(TaskQuestion(task_id="", kind=SUGGESTION, question=title,
                                                    task_detail=message,
                                                    metadata={"options": options,
                                                              "monitor_firing": firing}))
            question_id = getattr(asked, "id", "") or ""
            metadata["question_id"] = question_id
            template = SUGGESTION
        sent = self.notifications.notify(title, message, kind="proactive",
                                         dedupe_key=f"monitor:{firing}", template=template,
                                         metadata=metadata)
        said = {"said": sent is not None, "title": title, "message": message, "url": url,
                "picture": picture, "reason": reason,
                "notification_id": getattr(sent, "id", "") if sent is not None else "",
                "question_id": question_id}
        # Now, not when the caller is done: an answer finds its firing by this.
        self._outcome(firing, said)
        return said

    def _notify(self, activator: Dict[str, Any], tier: Dict[str, Any],
                context: Dict[str, Any], firing: str) -> Dict[str, Any]:
        signals = context.get("signals") or {}
        matched = ", ".join(str(one) for one in context.get("why") or [])
        reason = _filled(words(tier.get("reason", ""), self.config), dict(signals, matched=matched))
        return self.say(words(_filled(tier["title"], signals), self.config),
                        words(_filled(tier.get("message", ""), signals), self.config),
                        context, firing, choices=tier.get("choices", ""),
                        why="said by the Activator, without a model", reason=reason)

    def _queue(self, activator: Dict[str, Any], score: float, context: Dict[str, Any],
               firing: str, when: datetime) -> Dict[str, Any]:
        hit = new_id("monhit")
        self.db.execute("INSERT INTO monitor_hits (id, activator_id, firing_id, at, score, context) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (hit, activator["id"], firing, when.isoformat(), score,
                         json.dumps(context, ensure_ascii=False, default=str)))
        return {"queued": hit}

    def _ask(self, activator: Dict[str, Any], tier: Dict[str, Any], context: Dict[str, Any],
             firing: str, window: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        handler = tier.get("handler", "recommend")
        if handler == "calendar":
            # The notification step on that window, as before the Monitor.
            from ..core.triggers import Rule, notify_now
            rule = Rule(activator["builtin"] or activator["id"], activator.get("purpose") or "",
                        None, None)
            return notify_now(self.heartbeat, rule, (window or {}).get("title", ""),
                              (window or {}).get("text", ""), (window or {}).get("fresh", ""))
        if self.decider is None:
            return {"skipped": "no model to ask"}

        def ask() -> None:
            if not self._asking.acquire(blocking=False):
                self._outcome(firing, {"skipped": "the model is being asked about another"})
                return
            try:
                if handler in ("evening", "evening_now"):
                    said = self.decider.evening(self, activator, context, firing,
                                                at_once=handler == "evening_now")
                else:
                    said = self.decider.recommend(self, activator, tier, context, firing,
                                                  recommended=handler == "recommend")
            except Exception as exc:
                log.warning("the model could not be asked for an Activator", exc_info=True)
                said = {"error": str(exc)[:300]}
            finally:
                self._asking.release()
            self._outcome(firing, said)
        threading.Thread(target=ask, name="monitor-ask", daemon=True).start()
        return {"asking": handler}

    # -- the user's answers -------------------------------------------------------------
    def on_answered(self, _topic: str, payload: Any = None) -> None:
        """An answer to what a firing asked: Read detail opens the page; Good to
        know settles it; Not now waits twice its cooldown; Not today, until
        tomorrow."""
        if not isinstance(payload, dict):
            return
        question = str(payload.get("id") or "")
        row = self.db.query_one("SELECT * FROM monitor_firings WHERE question_id = ?", (question,))
        if row is None or not question:
            return
        row = dict(row)
        answer = str(payload.get("answer") or "")
        outcome = json.loads(row["outcome"] or "{}")
        outcome["answer"] = answer
        self._outcome(row["id"], outcome)
        activator = self.store.get(row["activator_id"]) or {"cooldown_minutes": 30, "then": []}
        if self.decider is not None and any(tier.get("handler") == "evening"
                                            for tier in activator.get("then") or []):
            # The evening report's question: now, later, at the fixed time, not today.
            threading.Thread(target=self.decider.on_evening_answer, args=(self, row, answer),
                             name="monitor-evening", daemon=True).start()
            return
        now = datetime.now(timezone.utc)
        if answer == "decline":
            self._set_kv(f"monitor.snoozed.{row['activator_id']}",
                         (now + timedelta(minutes=2 * float(activator["cooldown_minutes"]))).isoformat())
        elif answer == "not_today":
            self._set_kv(f"monitor.off_until.{row['activator_id']}",
                         (now.astimezone() + timedelta(days=1)).date().isoformat())
        elif answer == "add_to_collection" and self.keeper is not None:
            self.keeper(outcome)
        elif answer == "read_detail" and str(outcome.get("url") or "").startswith(
                ("https://", "http://")):
            try:
                self.opener(outcome["url"])
            except Exception:
                log.info("could not open %s", str(outcome["url"])[:120], exc_info=True)

    def on_bad(self, notification: Dict[str, Any], said: str = "") -> bool:
        """A notification of the Monitor's called bad: kept WHOLE - what it
        said, why it was made, what was on the screen - for the decisions
        like it (decide.py) and the step that may reprogram the Activator."""
        meta = (notification or {}).get("metadata") or {}
        monitor = meta.get("monitor")
        if not isinstance(monitor, dict):
            return False
        if meta.get("question_id") and self.questions is not None:
            # This is its answer: nothing is left standing to be answered again.
            self.questions.expire(str(meta["question_id"]))
        self.db.execute(
            "INSERT INTO monitor_bad (id, at, notification_id, activator_id, said, context) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (new_id("monbad"), utcnow().isoformat(), str(notification.get("id") or ""),
             str(monitor.get("activator_id") or ""), str(said or "")[:2000],
             json.dumps({"title": notification.get("title"), "message": notification.get("message"),
                         "why": monitor.get("why"), "context": monitor.get("context")},
                        ensure_ascii=False, default=str)))
        return True

    # -- what was done ----------------------------------------------------------------
    def pending_hits(self) -> List[Dict[str, Any]]:
        """What waits for the step: not judged yet, not too old."""
        cut = (datetime.now(timezone.utc) - timedelta(hours=HIT_EXPIRES_HOURS)).isoformat()
        self.db.execute("UPDATE monitor_hits SET judged_at = ?, decision = ? WHERE judged_at IS "
                        "NULL AND at < ?", (utcnow().isoformat(),
                                            json.dumps({"expired": True}), cut))
        return [dict(row, context=json.loads(row["context"] or "{}")) for row in self.db.query(
            "SELECT * FROM monitor_hits WHERE judged_at IS NULL ORDER BY at LIMIT 40")]

    def judged(self, hit: str, decision: Dict[str, Any]) -> None:
        """Judged once - never again (the user, 2026-11-03)."""
        self.db.execute("UPDATE monitor_hits SET judged_at = ?, decision = ? WHERE id = ? AND "
                        "judged_at IS NULL", (utcnow().isoformat(),
                                              json.dumps(decision, ensure_ascii=False), hit))

    def firings(self, limit: int = 60) -> List[Dict[str, Any]]:
        rows = self.db.query("SELECT f.*, a.data AS activator FROM monitor_firings f LEFT JOIN "
                             "monitor_activators a ON a.id = f.activator_id ORDER BY f.at DESC "
                             "LIMIT ?", (limit,))
        out = []
        for row in rows:
            one = dict(row)
            one["outcome"] = json.loads(one.get("outcome") or "{}")
            one["name"] = (json.loads(one.pop("activator") or "{}") or {}).get("name", "")
            one.pop("context", None)
            out.append(one)
        return out

    def bad_ones(self, limit: int = 50) -> List[Dict[str, Any]]:
        return [dict(row, context=json.loads(row["context"] or "{}")) for row in self.db.query(
            "SELECT * FROM monitor_bad ORDER BY at DESC LIMIT ?", (limit,))]

    def status(self) -> Dict[str, Any]:
        """For the Monitor panel: the signals now, the Activators, what they did."""
        return {"enabled": self.enabled(), "signals": {k: v for k, v in (self.latest or {}).items()},
                "habits": self.signals.habits(),
                "activators": self.store.list(), "firings": self.firings(),
                "quiet": self.store.quiet_rules(),
                "changes": self.store.changes(limit=80),
                "waiting": len(self.db.query("SELECT id FROM monitor_hits WHERE judged_at IS NULL")),
                "blocker": self.blocker, "reminders_waiting": self.reminders_waiting,
                "windows": self.signals.store.count(),
                "last_error": self.last_error or self.signals.last_error}


__all__ = ["Monitor", "ActivatorRule", "where_of"]
