"""WHEN THE MONITOR ASKS THE MODEL - three times:

- AN ACTIVATOR FIRED ("ask_model", handler "recommend"): at a moment code
  chose, the model decides WHETHER to recommend anything and WHAT (the user,
  2026-11-03: "trigger的时候，调用模型，让模型觉得有什么是需要推荐的 ... 模型可以
  选择是否send notification"), from the moment, the signals and habits, the
  work going on, what was learned, and the lessons and bad notifications
  LIKE THIS ONE - retrieved, never all of them; it may look on the web.
- THE EVENING REPORT ("evening"): asked first - now, later, at the fixed
  time, not today - and written from the day's windows and work.
- THE HEARTBEAT'S step_monitor: what was queued, judged once each - said or
  let go - and, when it is needed, the Activators reprogrammed (the user:
  "step_monitor还可以编程新的monitor(如果有必要)").

Each decision keeps its reason in the notification (engine.say).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from . import activators as act
from .signals import Signals

log = logging.getLogger(__name__)

RECOMMEND_TASK = "monitor_recommend"
EVENING_TASK = "monitor_evening"
STEP_TASK = "heartbeat:step_monitor"
#: The moment said again in English before its lessons are looked for.
QUERY_TASK = "monitor_lessons_query"
#: Tool rounds a recommendation may take on the web.
MOST_TOOL_ROUNDS = 4
#: Lessons and bad notifications retrieved for one decision.
LESSONS_K, BAD_K = 8, 4

RECOMMEND_SYSTEM = """You are the user's personal agent. Code has noticed a moment that may
be right for recommending something to the user - the moment and why are
given. YOU DECIDE whether anything is worth saying now, and what.

- Say nothing unless it is really worth an interruption now: new, specific,
  and about what they are doing or care about. "send": false is a good answer.
- One thing, or a few links on one thing. Real pages only - search the web
  for papers, news or pages; never invent an address.
- Follow the LESSONS about the user's notifications, and learn from what they
  did in moments like this: not what they called bad, not at a moment they
  put off.
- Write the title and message in the user's display language, short.

Answer with JSON alone:
{"send": true, "title": "...", "message": "...", "links": [{"title": "...", "url": "https://..."}],
 "why": "why this, now"}"""

EVENING_SYSTEM = """You write the user's evening report of their day, from the windows of
time and the work recorded: what they worked on and for how long, what moved,
what is left open for tomorrow - and, against their usual days, anything
unusual. A few short lines, warm and plain, in the user's display language.
Answer with the report's text alone."""

STEP_SYSTEM = """You are the step of the agent heartbeat that looks after the MONITOR: the
Activators that code runs on the user's real-time signals.

1. JUDGE what the Activators queued for you - each once: say it to the user
   (a short title and message, in the user's display language) or let it go.
   A middling spam or scam score is said only when the words really are a
   scam's or phishing's - not an ordinary newsletter, a bank's own page or a
   conversation about scams.
2. ONLY IF IT IS NEEDED, reprogram the Activators: when one keeps being called
   a bad notification, narrow it; when the user plainly needs one that is not
   there, add it. Most rounds change nothing.

{rules}

Answer with JSON alone:
{{"decisions": [{{"hit": "<id>", "notify": true, "title": "...", "message": "...", "why": "..."}}],
  "program": [{{"op": "add", "activator": {{...}}, "why": "..."}},
              {{"op": "edit", "id": "<id>", "changes": {{...}}, "why": "..."}},
              {{"op": "delete", "id": "<id>", "why": "only when it must go"}}]}}"""

WEB_TOOLS = None


def _web_tools() -> List[Any]:
    from ...models.base import ToolSpec
    return [ToolSpec("web_search", "Search the public web. Only public results.",
                     {"type": "object", "properties": {"query": {"type": "string"}},
                      "required": ["query"]}, read_only=True),
            ToolSpec("fetch_page", "Read a public web page.",
                     {"type": "object", "properties": {"url": {"type": "string"}},
                      "required": ["url"]}, read_only=True)]


def _json(text: str) -> Dict[str, Any]:
    from ..states.heartbeat import _as_json
    return _as_json(text or "") or {}


class Decider:
    """The model's part of the Monitor."""

    def __init__(self, db: Any, config: Any, models: Any, signals: Signals,
                 lessons: Any = None, experience: Optional[Callable[[str], List[str]]] = None,
                 tasks: Optional[Callable[[], List[str]]] = None,
                 search: Optional[Callable[[str], Any]] = None,
                 fetch: Optional[Callable[[str], Any]] = None) -> None:
        self.db = db
        self.config = config
        self.models = models
        self.signals = signals
        #: The notification lessons (heartbeat/core/calendar_nature.py): `relevant_text`.
        self.lessons = lessons
        self.experience = experience
        self.tasks = tasks
        if search is None or fetch is None:
            from ...agent.work.tools import fetch as _fetch, search as _search
            search, fetch = search or _search, fetch or _fetch
        self.search, self.fetch = search, fetch

    # -- what a decision is given ------------------------------------------------------
    def _query(self, context: Dict[str, Any]) -> str:
        window = context.get("window") or {}
        signals = context.get("signals") or {}
        return " ".join(str(part) for part in (
            context.get("activator", {}).get("name"), context.get("activator", {}).get("purpose"),
            window.get("title"), window.get("new_text", "")[:600], signals.get("project"),
            signals.get("title")) if part)

    def lessons_for(self, query: str) -> str:
        if self.lessons is None:
            return ""
        try:
            # Said again in English first, in the lessons' terms (the user,
            # 2026-11-03): the moment is in any language, the lessons in English.
            return self.lessons.relevant_text(query, LESSONS_K, rewrite_task=QUERY_TASK)
        except Exception:
            log.debug("the lessons could not be retrieved", exc_info=True)
            return ""

    def _recent_said(self, monitor: Any) -> List[str]:
        out = []
        for row in monitor.firings(30):
            outcome = row.get("outcome") or {}
            if outcome.get("title"):
                out.append(f"{row['at'][:16]} {row.get('name')}: {outcome['title']}"
                           + (f" - answered {outcome['answer']}" if outcome.get("answer") else ""))
        return out[:12]

    def _experience(self, query: str) -> List[str]:
        if self.experience is None:
            return []
        try:
            return list(self.experience(query))[:6]
        except Exception:
            return []

    def _tasks(self) -> List[str]:
        if self.tasks is None:
            return []
        try:
            return list(self.tasks())[:12]
        except Exception:
            return []

    # -- the model, with the web ------------------------------------------------------
    def _ask(self, system: str, content: str, task: str, web: bool = False,
             max_tokens: int = 1500) -> str:
        from ...common.languages import in_display
        from ...models.base import ModelRequest
        from ...runtime.web_guard import WebGuard
        guard = WebGuard()
        guard.know(content, "user")
        messages: List[Dict[str, Any]] = [{"role": "user", "content": content}]
        tools = _web_tools() if web else []

        def run(name: str, arguments: Dict[str, Any]) -> Any:
            if name == "web_search":
                query = str(arguments.get("query") or "")
                refused = guard.check_query(query)
                if refused:
                    return {"error": refused}
                said = self.search(query)
                guard.know(json.dumps(said, ensure_ascii=False, default=str), "web")
                return said
            if name == "fetch_page":
                url = str(arguments.get("url") or "")
                refused = guard.check_address(url)
                if refused:
                    return {"error": refused}
                said = self.fetch(url)
                guard.know(json.dumps(said, ensure_ascii=False, default=str)[:20000], "web")
                return said
            return {"error": f"no tool {name}"}
        for _round in range(MOST_TOOL_ROUNDS + 1):
            response = self.models.invoke(ModelRequest(
                messages=messages, system=system, tools=tools, max_tokens=max_tokens, task=task,
                tool_runner=run if tools else None, builtin_web=web, web_guard=guard,
                language=in_display("what is said to the user")), role="heartbeat")
            calls = getattr(response, "tool_calls", None) or []
            if not calls or _round == MOST_TOOL_ROUNDS:
                return str(getattr(response, "text", "") or "")
            messages.append({"role": "assistant", "content": getattr(response, "raw_content", None)
                             or getattr(response, "text", "")})
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": call.id,
                 "content": json.dumps(run(call.name, call.arguments or {}), ensure_ascii=False,
                                       default=str)[:20000]} for call in calls]})
            if _round == MOST_TOOL_ROUNDS - 1:
                tools = []
        return ""

    # -- 1. a recommendation ------------------------------------------------------------
    def recommend(self, monitor: Any, activator: Dict[str, Any], tier: Dict[str, Any],
                  context: Dict[str, Any], firing: str) -> Dict[str, Any]:
        query = self._query(context)
        signals = context.get("signals") or {}
        parts = [
            f"THE MOMENT: {activator['name']} - {activator.get('purpose') or ''}\n"
            f"What it asks you: {tier.get('brief') or 'whether to recommend anything now'}\n"
            f"Why it fired: {'; '.join(context.get('why') or []) or '-'}",
            "THE USER NOW (code's signals):\n" + json.dumps(
                {k: signals.get(k) for k in ("hour", "weekday", "mode", "depth", "activity",
                                             "application", "title", "project", "project_how", "task",
                                             "project_minutes",
                                             "project_days_since", "work_started_today",
                                             "stretch_minutes", "since_break_minutes", "work_minutes",
                                             "worked_today_minutes", "back_after_minutes")},
                ensure_ascii=False),
            "THEIR HABITS:\n" + self.signals.habits_text(),
            "THE WORK GOING ON:\n" + ("\n".join(f"- {one}" for one in self._tasks()) or "-"),
            "WHAT WAS LEARNED OF HOW THEY WORK, like this moment:\n"
            + ("\n".join(f"- {one}" for one in self._experience(query)) or "-"),
            "LATELY SAID BY THE MONITOR:\n" + ("\n".join(self._recent_said(monitor)) or "-"),
            "LESSONS ABOUT THEIR NOTIFICATIONS - the rules and what they did in moments "
            "like this one:\n" + (self.lessons_for(query) or "None learned yet."),
        ]
        searches = bool(self.config.get("monitor_recommend_search", True))
        answer = _json(self._ask(RECOMMEND_SYSTEM, "\n\n".join(parts), RECOMMEND_TASK,
                                 web=searches))
        if not answer.get("send") or not str(answer.get("title") or "").strip():
            return {"sent": False, "why": str(answer.get("why") or "the model said nothing now")[:400]}
        links = [{"title": str(one.get("title") or "")[:160], "url": str(one.get("url") or "")}
                 for one in answer.get("links") or [] if isinstance(one, dict)
                 and str(one.get("url") or "").startswith(("https://", "http://"))][:5]
        context = dict(context, model_why=str(answer.get("why") or "")[:600])
        said = monitor.say(str(answer["title"])[:120], str(answer.get("message") or "")[:900],
                           context, firing, links=links, why=str(answer.get("why") or "")[:600])
        return dict(said, links=links, why=answer.get("why"))

    # -- 2. the evening report ---------------------------------------------------------
    def _evening_key(self, day: str) -> str:
        return f"monitor.evening.{day}"

    def evening(self, monitor: Any, activator: Dict[str, Any], context: Dict[str, Any],
                firing: str) -> Dict[str, Any]:
        """Asked first; at the fixed time, written then; done once a day."""
        local = datetime.now(timezone.utc).astimezone()
        day = local.date().isoformat()
        state = monitor._kv(self._evening_key(day))
        if state in ("done", "declined"):
            return {"skipped": f"the evening report is {state} today"}
        fixed = str(self.config.get("monitor_evening_fixed_time", "21:00") or "21:00")
        if state == "fixed":
            if local.strftime("%H:%M") < fixed:
                return {"skipped": f"waiting for {fixed}"}
            return self.write_evening(monitor, context, firing)
        if state.startswith("later:") and state[6:] > datetime.now(timezone.utc).isoformat():
            return {"skipped": "asked to wait"}
        if monitor.questions is None or monitor.notifications is None:
            return self.write_evening(monitor, context, firing)
        from ...agent.feedback.store import TaskQuestion
        from ...agent.feedback.templates import SUGGESTION
        from ...common.interface_words import shown
        from ...common.languages import display_language
        from ...common.interface_words import fill
        code = display_language(self.config)
        options = [{"key": "accept", "label": shown("Now", self.config), "effect": "complete"},
                   {"key": "later", "label": shown("Later", self.config), "effect": "complete"},
                   {"key": "at_fixed", "label": fill("At {0}", code, fixed), "effect": "complete"},
                   {"key": "not_today", "label": shown("Not today", self.config),
                    "effect": "complete"}]
        title = shown("Your evening report", self.config)
        hours = round(float(context.get("signals", {}).get("worked_today_minutes") or 0) / 60, 1)
        message = fill("You have worked {0} hours today. Shall I sum up your day?", code, hours)
        asked = monitor.questions.ask(TaskQuestion(task_id="", kind=SUGGESTION, question=title,
                                                   task_detail=message,
                                                   metadata={"options": options,
                                                             "monitor_firing": firing}))
        question = getattr(asked, "id", "") or ""
        sent = monitor.notifications.notify(
            title, message, kind="proactive", dedupe_key=f"monitor:{firing}", template=SUGGESTION,
            metadata={"question_id": question,
                      "monitor": {"firing": firing, "activator_id": activator["id"],
                                  "activator": activator["name"], "group": activator["group"],
                                  "why": "the evening report, asked first", "context": context}})
        monitor._set_kv(self._evening_key(day), "asked")
        return {"asked": True, "question_id": question, "title": title,
                "notification_id": getattr(sent, "id", "") if sent is not None else ""}

    def on_evening_answer(self, monitor: Any, row: Dict[str, Any], answer: str) -> bool:
        local = datetime.now(timezone.utc).astimezone()
        day = local.date().isoformat()
        if answer == "accept":
            context = json.loads(row.get("context") or "{}")
            monitor._set_kv(self._evening_key(day), "writing")
            self.write_evening(monitor, context, row["id"])
        elif answer == "later":
            monitor._set_kv(self._evening_key(day), "later:" + (
                datetime.now(timezone.utc) + timedelta(minutes=float(
                    self.config.get("monitor_evening_later_minutes", 60)))).isoformat())
        elif answer == "at_fixed":
            monitor._set_kv(self._evening_key(day), "fixed")
        elif answer == "not_today":
            monitor._set_kv(self._evening_key(day), "declined")
        else:
            return False
        return True

    def write_evening(self, monitor: Any, context: Dict[str, Any], firing: str) -> Dict[str, Any]:
        local = datetime.now(timezone.utc).astimezone()
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        windows = [w for w in self.signals.store.since(midnight) if w.main != "away"]
        by_project: Dict[str, float] = {}
        for w in windows:
            name = w.project or w.main
            by_project[name] = by_project.get(name, 0.0) + w.minutes * (1 - w.idle_share)
        parts = [f"TODAY, {local.strftime('%A %d %B')}: worked "
                 f"{sum(by_project.values()) / 60:.1f} h, from "
                 f"{windows[0].start.strftime('%H:%M') if windows else '-'}.",
                 "BY PROJECT OR KIND OF WORK (minutes):\n" + "\n".join(
                     f"- {name}: {minutes:.0f}" for name, minutes in
                     sorted(by_project.items(), key=lambda kv: -kv[1])[:12]),
                 "THE WORK GOING ON:\n" + ("\n".join(f"- {one}" for one in self._tasks()) or "-"),
                 "THEIR USUAL DAYS:\n" + self.signals.habits_text()]
        text = self._ask(EVENING_SYSTEM, "\n\n".join(parts), EVENING_TASK, max_tokens=900).strip()
        monitor._set_kv(self._evening_key(local.date().isoformat()), "done")
        if not text:
            return {"sent": False, "why": "nothing written"}
        from ...common.interface_words import shown
        return monitor.say(shown("Your day", self.config), text[:1400], dict(context, report=True),
                           firing,
                           why="the evening report")

    # -- 3. the heartbeat's step -------------------------------------------------------
    def step(self, monitor: Any) -> Dict[str, Any]:
        """step_monitor: the queued hits judged once each, and - only when it is
        needed - the Activators reprogrammed. No model when there is nothing
        to judge and no bad notification since the last."""
        from ...common.modules import enabled as module_on
        if not module_on(self.config, "monitor"):
            return {"skipped": "the Monitor is switched off"}
        hits = monitor.pending_hits()
        seen = monitor._kv("monitor.step.bad_seen")
        bad = [one for one in monitor.bad_ones(50) if one["at"] > seen]
        if not hits and not bad:
            return {"skipped": "nothing queued and no bad notification since the last"}
        query = " ".join(self._query(hit["context"]) for hit in hits[:10]) or " ".join(
            json.dumps(one["context"], ensure_ascii=False)[:300] for one in bad[:5])
        queued = [{"hit": hit["id"], "activator": hit["context"].get("activator", {}).get("name"),
                   "score": hit["score"], "why": hit["context"].get("why"),
                   "window": {k: (hit["context"].get("window") or {}).get(k)
                              for k in ("title", "application", "where", "new_text")},
                   "at": hit["at"]} for hit in hits]
        counts = {}
        for row in self.db.query(
                "SELECT activator_id, COUNT(*) AS n, SUM(acted) AS acted FROM monitor_firings "
                "WHERE at >= ? GROUP BY activator_id",
                ((datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),)):
            counts[row["activator_id"]] = {"fired_7_days": row["n"], "acted": row["acted"]}
        bad_counts: Dict[str, int] = {}
        for one in monitor.bad_ones(200):
            bad_counts[one["activator_id"]] = bad_counts.get(one["activator_id"], 0) + 1
        listed = [dict(one, last_7_days=counts.get(one["id"], {}),
                       bad_notifications=bad_counts.get(one["id"], 0))
                  for one in monitor.store.for_the_model()]
        parts = [
            "QUEUED FOR YOU TO JUDGE - each once:\n" + (json.dumps(queued, ensure_ascii=False,
                                                                   indent=1) if queued else "None."),
            "BAD NOTIFICATIONS SINCE THE LAST TIME:\n" + (json.dumps(
                [{"at": one["at"], "activator_id": one["activator_id"], "said": one["said"],
                  "notification": {"title": one["context"].get("title"),
                                   "message": one["context"].get("message")},
                  "why_it_was_made": one["context"].get("why")} for one in bad[:10]],
                ensure_ascii=False, indent=1) if bad else "None."),
            "LESSONS ABOUT THEIR NOTIFICATIONS, those like these:\n"
            + (self.lessons_for(query) or "None learned yet."),
            "THE ACTIVATORS NOW:\n" + json.dumps(listed, ensure_ascii=False)[:30000],
            "THE SIGNALS AND EVENTS THEY CAN USE:\n" + json.dumps(Signals.describe(),
                                                                  ensure_ascii=False),
        ]
        answer = _json(self._ask(STEP_SYSTEM.format(rules=act.RULES_FOR_THE_MODEL),
                                 "\n\n".join(parts), STEP_TASK, max_tokens=4000))
        report: Dict[str, Any] = {"judged": 0, "said": 0, "program": []}
        by_id = {hit["id"]: hit for hit in hits}
        for decision in answer.get("decisions") or []:
            if not isinstance(decision, dict):
                continue
            hit = by_id.pop(str(decision.get("hit") or ""), None)
            if hit is None:
                continue
            said: Dict[str, Any] = {}
            if decision.get("notify") and str(decision.get("title") or "").strip():
                context = dict(hit["context"], model_why=str(decision.get("why") or "")[:600])
                said = monitor.say(str(decision["title"])[:120],
                                   str(decision.get("message") or "")[:900], context,
                                   hit["firing_id"] or hit["id"],
                                   why=str(decision.get("why") or "")[:600])
                report["said"] += 1
            monitor.judged(hit["id"], dict(decision, said=bool(said.get("said"))))
            report["judged"] += 1
        for hit in by_id.values():                   # not answered: let go, judged
            monitor.judged(hit["id"], {"notify": False, "why": "not answered by the step"})
        for change in answer.get("program") or []:
            report["program"].append(background_change(monitor, change))
        if bad:
            monitor._set_kv("monitor.step.bad_seen", bad[0]["at"])
        return report


def background_change(monitor: Any, change: Any) -> Dict[str, Any]:
    """A change the heartbeat wants: an edit or a deletion held to what is on
    record (monitor/evidence.py). Held back, it is reported, not done - a new
    Activator takes nothing from anything there is."""
    evidence = getattr(monitor, "evidence", None)
    if evidence is not None and isinstance(change, dict) and change.get("op") in ("edit", "delete"):
        held = evidence.background_refusal(str(change["op"]), str(change.get("id") or ""),
                                           str(change.get("why") or ""))
        if held:
            return {"op": change["op"], "id": change.get("id"), "held": held}
    return apply_program(monitor.store, change, "heartbeat")


def apply_program(store: act.ActivatorStore, change: Any, by: str) -> Dict[str, Any]:
    """One change the model asked for - held by code: a protected Activator
    is never changed, whatever is asked."""
    if not isinstance(change, dict):
        return {"error": "a change is an object"}
    op = str(change.get("op") or "")
    why = str(change.get("why") or "")[:600]
    try:
        if op == "add":
            ident, warnings = store.add(change.get("activator") or {}, by, why)
            return {"op": "add", "id": ident, "warnings": warnings}
        if op == "edit":
            clean, warnings = store.edit(str(change.get("id") or ""), change.get("changes") or {},
                                         by, why)
            return {"op": "edit", "id": change.get("id"), "warnings": warnings}
        if op == "delete":
            store.delete(str(change.get("id") or ""), by, why)
            return {"op": "delete", "id": change.get("id")}
        return {"error": f"op is add, edit or delete, not {op!r}"}
    except ValueError as exc:
        return {"op": op, "id": change.get("id"), "error": str(exc)}


__all__ = ["Decider", "apply_program", "RECOMMEND_TASK", "EVENING_TASK", "STEP_TASK"]
