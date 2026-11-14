"""Agent tool surface.

Tools are the only way the model touches the machine.  Every filesystem or
execution tool goes through the sandbox runtime, and every call is recorded as
a Raw Edit (section 47), so the Debugger can see exactly what the agent did.

Nothing is given with a message: the chat answers from what it is asked, and
acts on the workspace and the schedule. What the application keeps of the
user it searches for when it needs it (memory_tools.py); what was on the
screen it can ask for by time (agent/screen_history.py).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional, Any, Callable, Dict, List

from ...common.events.models import ScheduledTask, parse_iso, utcnow
from ...common.reason import BY_AGENT
from ...data_manage.text import estimate_tokens
from ...scheduler.core.recurrence import (
    RecurrenceError,
    describe as describe_recurrence,
    parse_recurrence,
)
from ...scheduler.core.given import HOW_TO_GIVE, ZONE_REFUSED, now_line, time_given
from ...models.base import ToolSpec
from ...runtime.permissions import SecurityViolation
from ...scheduler.execution.executors import (DEFAULT_CHECK_AFTER_SECONDS,
                                              DEFAULT_RECHECK_AFTER_SECONDS,
                                              VERIFICATION, _interval)

log = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: List[ToolSpec] = []
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = {}
        #: Tools that refuse an incomplete call in their own words.
        self._answers_for_itself: set = set()
        #: Tools offered only while they may be used (name -> fn() -> "" when it may, else why not):
        #: the Agents Society's, by what its key permits now (2026-11-12: "或者就不给他没权限的tool").
        self._when: Dict[str, Callable[[], str]] = {}

    def add(self, spec: ToolSpec, handler: Callable[[Dict[str, Any]], Any],
            answers_for_itself: bool = False,
            unless: Optional[Callable[[], str]] = None) -> None:
        """Register a tool.

        ``answers_for_itself`` says this tool already refuses an incomplete
        call in its own words, and should be left to. The check below exists
        because most tools met a missing argument however they happened to -
        ``{error: no record None}`` - and it answers in general terms: which
        tool, which argument, call it again. A tool that can say *why* the
        argument is wanted says more than that, and the general answer would
        replace it with less.
        """
        self._specs.append(spec)
        self._handlers[spec.name] = handler
        if answers_for_itself:
            self._answers_for_itself.add(spec.name)
        if unless is not None:
            self._when[spec.name] = unless

    def _barred(self, name: str) -> str:
        check = self._when.get(name)
        if check is None:
            return ""
        try:
            return str(check() or "")
        except Exception:
            return "it cannot be used now"

    @property
    def specs(self) -> List[ToolSpec]:
        """The tools offered now - none that may not be used now."""
        return [one for one in self._specs if not self._barred(one.name)]

    def has(self, name: str) -> bool:
        return name in self._handlers

    def call(self, name: str, arguments: Dict[str, Any]) -> Any:
        handler = self._handlers.get(name)
        if handler is None:
            return {"error": f"unknown tool {name}"}
        barred = self._barred(name)
        if barred:
            return {"error": f"{name} may not be used now: {barred}"}
        arguments = arguments or {}
        missing = ([] if name in self._answers_for_itself
                   else self._missing(name, arguments))
        if missing:
            # Said here rather than inside each tool.
            #
            # The schema marks arguments required, but nothing was checking:
            # under the text protocol a call is whatever the model wrote, and
            # the required list is a description the model may simply not
            # have followed. So a call arrived with no id, every tool met the
            # absence in its own way, and the one the user saw came back as
            # "no record None" - which names neither the tool, nor the
            # argument, nor what to do about it, and reads like a memory that
            # went missing rather than a call that was never complete.
            return {"error": (f"{name} needs {', '.join(missing)}. "
                              f"Call it again with "
                              f"{' and '.join(missing)} filled in."),
                    "missing": missing}
        return handler(arguments)

    def _missing(self, name: str, arguments: Dict[str, Any]) -> List[str]:
        """Required arguments this call left out or left empty.

        Empty counts as left out: a tool that takes an id cannot do anything
        with "", and answering "no record" for it describes the library when
        the fault is in the call.
        """
        for spec in self._specs:
            if spec.name != name:
                continue
            schema = spec.input_schema or {}
            required = schema.get("required") or []
            return [str(key) for key in required if _empty(arguments.get(key))]
        return []

    def describe(self) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in self._specs]


def _empty(value: Any) -> bool:
    """Whether an argument was left out, or supplied with nothing in it.

    The same thing from the tool's point of view: a tool that takes an id can
    do nothing with ``""``, and a search given an empty list of queries has
    not been asked anything. A number and a boolean are never empty - ``0``
    and ``False`` are answers.
    """
    if value is None:
        return True
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return False
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return not value
    return False


def _schema(properties: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False}


# Section 52.1, as amended. A ceiling rather than a style guide: the detail
# should still read as a plain account of what will happen, but the limit is
# there to stop a runaway, not to force brevity. Counted in tokens with the
# same estimator the retrieval budget uses, so "a token" means one thing
# across the application.
DESCRIPTION_TOKEN_LIMIT = 2000


#: How near in time, and how alike in words, an entry must be to be shown as perhaps the same.
ALREADY_WITHIN_HOURS = 24
ALREADY_ALIKE_AT = 0.5


def already_on_calendar(scheduler: Any, words: str, when: Any) -> list:
    """What is waiting on the calendar within a day of `when` that shares most of the words - by
    the shorter text's terms, as the heartbeat's check (heartbeat/core/heartbeat.py)."""
    return [{"task_id": task.id, "title": task.title,
             "at": task.scheduled_at.isoformat() if task.scheduled_at else "",
             "notifies": bool((task.task_payload or {}).get("notify_user"))}
            for task in alike_on_calendar(scheduler, words, when)]


def alike_on_calendar(scheduler: Any, words: str, when: Any) -> list:
    """The waiting tasks themselves that `already_on_calendar` says."""
    from datetime import timedelta
    from ..overview.keywords import terms
    store = getattr(scheduler, "store", None)
    if store is None or when is None:
        return []
    near = timedelta(hours=ALREADY_WITHIN_HOURS)
    try:
        waiting = store.list(status="pending", start=when - near, end=when + near, limit=200)
    except Exception:                                # pragma: no cover - added unchecked
        return []
    mine = set(terms(words))
    out = []
    for task in waiting:
        if task.execution_type != "notification":
            continue
        theirs = set(terms(f"{task.title}\n{task.description or ''}"))
        if not mine or not theirs:
            continue
        if len(mine & theirs) / min(len(mine), len(theirs)) >= ALREADY_ALIKE_AT:
            out.append(task)
    return out[:6]


def build_registry(
    *,
    runtime: Any,
    scheduler: Any,
    notifications: Any,
    config: Any,
) -> ToolRegistry:
    registry = ToolRegistry()

    def list_workspace(args: Dict[str, Any]) -> Any:
        from ...runtime.filesystem import workspace_tree

        return workspace_tree(runtime.policy, max_entries=int(args.get("limit", 200)))

    registry.add(
        ToolSpec("list_workspace", "List the files in the authorized workspace.",
                 _schema({"limit": {"type": "integer"}}, [])),
        list_workspace,
    )

    def read_file(args: Dict[str, Any]) -> Any:
        from ...runtime import documents
        from ...runtime.filesystem import validate_read_path
        try:
            path = str(args.get("path", ""))
            if documents.needs_reading(path):
                # A picture or a document (runtime/documents.py).
                where = validate_read_path(path, runtime.policy)
                return documents.read_for_model(where, 1, int(args.get("max_chars", 40000)))
            return {"content": runtime.read_text(path,
                                                 max_chars=int(args.get("max_chars", 40000)))}
        except SecurityViolation as exc:
            return exc.to_dict()
        except (OSError, ValueError) as exc:
            return {"error": str(exc)}

    registry.add(
        ToolSpec("read_file", "Read a file the agent is allowed to read: a text file as "
                 "it is; a picture as the words read in it, and the picture itself "
                 "where you can see pictures; a PDF, Word, PowerPoint or Excel file "
                 "as its text.",
                 _schema({"path": {"type": "string"}, "max_chars": {"type": "integer"}}, ["path"])),
        read_file,
    )

    def write_file(args: Dict[str, Any]) -> Any:
        try:
            path = runtime.write_text(str(args.get("path", "")), str(args.get("content", "")),
                                      append=bool(args.get("append", False)))
            return {"written": str(path)}
        except SecurityViolation as exc:
            return exc.to_dict()
        except OSError as exc:
            return {"error": str(exc)}

    registry.add(
        ToolSpec(
            "write_file",
            "Write a text file. Writes outside the authorized workspace are refused.",
            _schema(
                {"path": {"type": "string"}, "content": {"type": "string"},
                 "append": {"type": "boolean"}},
                ["path", "content"],
            ),
        ),
        write_file,
    )

    # NO PYTHON IN THE CHAT, for now: the user's choice. Running code is the
    # work runs' job, where it is isolated by the operating system and
    # confined to the work's own folder (runtime/isolation.py).

    # -- scheduler ----------------------------------------------------------
    #: The titles whose zone was refused once: asked again, a zone refused
    #: is the user's own time, said on the card (given.py).
    zone_asked: set = set()

    def schedule_task(args: Dict[str, Any]) -> Any:  # noqa: C901 - the calendar's one door
        # Section 52.1: a task says what it is, or it is not created.
        #
        # Substituting "Scheduled task" for a missing title does not rescue
        # the call - it produces a task nobody can identify, in a list where
        # every such task looks the same, and the mistake is invisible from
        # then on. Refusing says what is wrong while the caller can still fix
        # it.
        title = str(args.get("title") or "").strip()
        if not title:
            return {"error": "a task needs a title: what is this, in a few words, "
                             "so the user can tell it from every other task"}
        description = str(args.get("description") or "").strip()
        if not description:
            return {"error": "a task needs a description: what will happen when it "
                             "runs, in plain words the user can read at a glance"}
        # Section 52.1, as amended: the detail is for the user, the reasoning
        # is not.
        #
        # Asking one field to carry both produced descriptions of five hundred
        # to nine hundred characters - the evidence, the prior, what the answer
        # would settle - written agent to agent and unreadable in a calendar.
        # The reasoning is worth keeping; it just is not the thing the user
        # opens a task to find out.
        used = estimate_tokens(description)
        if used > DESCRIPTION_TOKEN_LIMIT:
            return {"error": f"the description is about {used} tokens and the limit is "
                             f"{DESCRIPTION_TOKEN_LIMIT}: say what will happen, and put "
                             "the evidence and the reasoning in 'rationale', where the "
                             "user can open it if they want it"}
        # Section 52.2: interrupting is a separate decision from scheduling.
        notify = str(args.get("notify") or "").strip().lower()
        if notify not in ("silent", "notify"):
            return {"error": "say whether this should interrupt the user: notify='silent' "
                             "puts it on the calendar to run quietly, notify='notify' "
                             "also raises a notification when it runs. Most checks the "
                             "agent runs for itself are silent; choose 'notify' only when "
                             "the user would want to be interrupted at that moment"}
        # Section 52.3: a repeat is stated as a rule, and checked here rather
        # than discovered when it fires every four seconds.
        # A TIME AS A CLOCK SHOWS IT, AND WHOSE CLOCK, read as the heartbeat's
        # notifications are - one reader, one rule for a zone (given.py). The
        # model never works out UTC: an offset taken today and stamped on a
        # date after the clocks change is an hour wrong. A zone refused is
        # the tool's error, saying why and what is accepted - asked once;
        # refused again, the time is the user's own and the card says so.
        raw = args.get("recurrence")
        given = dict(args)
        if not str(given.get("zone") or "").strip() and isinstance(raw, dict):
            given["zone"] = raw.get("zone")      # a repeat's zone is the time's
        asked_key = title.lower()
        when, said_time, refused = time_given(
            given, (lambda said, reason: "") if asked_key in zone_asked else None)
        if when is None:
            if refused.startswith(ZONE_REFUSED):
                zone_asked.add(asked_key)
            return {"error": refused}
        zone_asked.discard(asked_key)
        try:
            # On the clock of the zone its time is on.
            recurrence = parse_recurrence(
                {key: value for key, value in raw.items() if key != "zone"}
                if isinstance(raw, dict) else raw, zone=said_time["zone"])
        except RecurrenceError as exc:
            return {"error": str(exc)}
        # Only notifications: nothing on the calendar executes anything.
        execution_type = "notification"
        payload = dict(args.get("payload") or {})
        # AN EVENT THAT MAY INTEREST THEM is on the calendar quietly (the user, 2026-11-09).
        event = str(args.get("event") or "").strip().lower()
        payload["notify_user"] = notify == "notify" and event != "explore"
        # Section 59, as amended: how this task is to be settled, decided now.
        # The one scheduling the work is the one who knows what finishing looks
        # like and how soon it is worth asking.
        verification = args.get("verification")
        if isinstance(verification, dict):
            payload[VERIFICATION] = {
                "completed_when": str(verification.get("completed_when") or "")[:500],
                "check_after_seconds": _interval(
                    verification.get("check_after_seconds"), DEFAULT_CHECK_AFTER_SECONDS),
                "recheck_after_seconds": _interval(
                    verification.get("recheck_after_seconds"), DEFAULT_RECHECK_AFTER_SECONDS),
            }
        # The time as a clock showed it, whose clock, and what the source
        # wrote: shown on the calendar card, as the heartbeat's are.
        metadata = {"created_by": "agent", "time": said_time}
        rationale = str(args.get("rationale") or "").strip()
        if rationale:
            metadata["rationale"] = rationale
        if event in ("going", "explore"):
            metadata["event"] = event
        # WHERE IT IS, KEPT (the user, 2026-11-09: "如果有地点可以留下"): the day's agenda plans the
        # way there and back by it (heartbeat/agenda/items.py reads details' place).
        place = " ".join(str(args.get("place") or "").split())[:300]
        if place:
            metadata["details"] = dict(metadata.get("details") or {}, place=place)
        # NOT TWICE (the user, 2026-11-09: one meeting went on the calendar five times in twenty
        # minutes - from the heartbeat, the chat and a project's steps, none looking first) - AND
        # HELD BACK ONLY WHEN A MODEL SAYS IT IS THE SAME THING ("要模型确认是同一件事拦截"): what is
        # there within a day of it, alike in words, is put to the heartbeat's own check
        # (`registry.same_check`, heartbeat._is_it_the_same); "different", or no answer, it goes on.
        if not args.get("even_so"):
            alike = alike_on_calendar(scheduler, f"{title}\n{description}", when)
            checking = getattr(registry, "same_check", None)
            if alike and checking is not None:
                verdict = checking({"type": "reminder" if notify == "notify" else "notice",
                                    "title": title, "message": description}, when, alike) or {}
                same = str(verdict.get("same_as") or "")
                if same and not verdict.get("create_anyway"):
                    repeated = next((task for task in alike if task.id == same), alike[0])
                    return {"error": f"already on the calendar as \u201c{repeated.title}\u201d",
                            "same_as": repeated.id, "why": str(verdict.get("why") or ""),
                            "said": "A check found it is the same thing: it was not added. Say "
                                    "it is there already."}
                if verdict.get("why"):
                    metadata["checked_not_the_same"] = str(verdict["why"])[:300]
            elif alike:
                # No check to ask: the model that asked judges, told what is there.
                return {"error": "something like it is on the calendar already",
                        "already": already_on_calendar(scheduler, f"{title}\n{description}", when),
                        "said": "If one of these is the same thing, do not add it: say it is "
                                "there. If it is a different thing, call again with even_so: "
                                "true and say in rationale how it differs."}
        task = ScheduledTask(
            title=title,
            description=description,
            scheduled_at=when,
            execution_type=execution_type,
            task_payload=payload,
            metadata=metadata,
            recurrence=recurrence,
            timezone_name=said_time["zone"],
            created_by=BY_AGENT,
            reason=rationale,
        )
        created = scheduler.create_task(task)
        answer = {"task_id": created.id, "scheduled_at": created.scheduled_at.isoformat(),
                  "on_its_clock": f'{said_time["wall"]} {said_time["zone_said"]}',
                  "execution_type": created.execution_type, "notify": notify,
                  "repeats": describe_recurrence(created.recurrence)}
        if said_time.get("zone_unread"):
            answer["zone_unread"] = (
                f'the zone "{said_time["zone_unread"]}" could not be read and the '
                "time was taken as the user's own - tell the user")
        return answer

    registry.add(
        ToolSpec(
            "schedule_task",
            "Put an entry on this application's own calendar - a meeting, a "
            "reminder, something at a time - from Chat or anywhere else. It lasts, "
            "and it only notifies: nothing on the calendar is carried out. No "
            "calendar of the program running you, and no external one, is needed "
            "or stands in for it. Use this whenever something should happen later "
            "rather than during this reply. "
            "The title and the description are written for the user, not for you: "
            "the title is what they see in a list beside everything else scheduled, "
            "and the description says what will happen when it runs, in a sentence "
            "or two they can read at a glance. Keep your evidence and your reasoning "
            "out of it and put them in 'rationale' instead - the user can open that "
            "when they want it, and it does not have to be short. "
            "'notify' decides whether it interrupts them when its time comes: "
            "'silent' means it is on the calendar and nothing more, 'notify' means "
            "it also raises a notification - a meeting is silent unless they asked "
            "to be reminded. A check you are running to satisfy your own curiosity is "
            "silent. Choose 'notify' only when the answer is something the user would "
            "want put in front of them at that moment. A task missing any of these "
            "will be refused.\n\n" + HOW_TO_GIVE,
            _schema(
                {
                    "title": {"type": "string"},
                    "description": {"type": "string",
                                    "description": f"at most {DESCRIPTION_TOKEN_LIMIT} tokens"},
                    "rationale": {"type": "string",
                                  "description": "why you scheduled it, and on what "
                                                 "evidence; shown only on request"},
                    "notify": {"type": "string", "enum": ["silent", "notify"]},
                    "event": {"type": "string", "enum": ["going", "explore"],
                              "description": "for an event (a talk, a meetup): 'going' when "
                                             "they will probably go - they said so, or it is "
                                             "their own; 'explore' when it may interest them "
                                             "and nothing says they will go: it goes on "
                                             "quietly, never notifying. Leave it out for what "
                                             "is not an event"},
                    "place": {"type": "string",
                              "description": "where it happens, exactly as it was given - a "
                                             "room, a building, an address - when it is "
                                             "somewhere; an online one's link goes in the "
                                             "message. Leave it out when no place was given"},
                    "even_so": {"type": "boolean",
                                "description": "true only when you were told something like it "
                                               "is on the calendar already and it is NOT the "
                                               "same thing - say why in rationale"},
                    "recurrence": {
                        "type": "object",
                        "description": "omit for a task that runs once. To repeat: "
                                       "every plus unit (minutes|hours|days|weeks), "
                                       "or every_seconds. Stop it with count (how "
                                       "many runs) or until (YYYY-MM-DD HH:MM on "
                                       "the same clock as at_local); "
                                       "with neither it repeats indefinitely. A "
                                       "repeat of days or weeks keeps the same time "
                                       "on the clock of 'zone' across "
                                       "daylight-saving changes",
                    },
                    "verification": {
                        "type": "object",
                        "description": "optional, kept for the record: "
                                       "'completed_when' is what would count as "
                                       "done, in a sentence. Nothing is checked "
                                       "afterwards - a notification is settled "
                                       "once it is delivered. "
                                       "'recheck_after_seconds' is how long a "
                                       "'later' answer waits before it comes back",
                    },
                    "at_local": {"type": "string",
                                 "description": "YYYY-MM-DD HH:MM, as the clock "
                                                "of 'zone' shows it - never UTC "
                                                "worked out by you"},
                    "zone": {"type": "string",
                             "description": "whose clock at_local is on: \"user\" "
                                            "for the user's own, or the place's "
                                            "Region/City name (Europe/Paris) or "
                                            "UTC - see the tool's description"},
                    "as_written": {"type": "string",
                                   "description": "the time exactly as the user or "
                                                  "the source wrote it"},
                    "payload": {"type": "object"},
                },
                ["title", "description", "notify", "at_local", "zone"],
            ),
        ),
        schedule_task,
        # Its own refusals say what each field is *for* - what tells a
        # task from every other one, whether it interrupts. The general
        # answer would replace that with less.
        answers_for_itself=True,
    )

    def list_tasks(args: Dict[str, Any]) -> Any:
        tasks = scheduler.store.list(status=args.get("status"), limit=int(args.get("limit", 20)))
        return [
            {"id": t.id, "title": t.title, "scheduled_at": t.scheduled_at.isoformat(),
             "status": t.status, "execution_type": t.execution_type}
            for t in tasks
        ]

    registry.add(
        ToolSpec("list_scheduled_tasks", "List scheduled tasks.",
                 _schema({"status": {"type": "string"}, "limit": {"type": "integer"}}, [])),
        list_tasks,
    )

    def cancel_task(args: Dict[str, Any]) -> Any:
        reason = str(args.get("reason") or "").strip()
        if not reason:
            return {"cancelled": False, "task_id": args.get("task_id"),
                    "error": "say why: the reason is shown on the calendar"}
        task = scheduler.cancel(str(args.get("task_id", "")), reason=reason,
                                by="the agent")
        return {"cancelled": bool(task), "task_id": args.get("task_id")}

    registry.add(
        ToolSpec("cancel_scheduled_task",
                 "Cancel a scheduled task by id. It stays on the calendar, "
                 "marked cancelled, with the reason you give.",
                 _schema({"task_id": {"type": "string"},
                          "reason": {"type": "string",
                                     "description": "why, for the user"}},
                         ["task_id", "reason"])),
        cancel_task,
    )

    # -- user interaction ---------------------------------------------------
    # NO TOOL TO INTERRUPT THE USER (the user, 2026-11-13: "所有的通知必须在monitor里 ... 其它都取消"): what the agent
    # would tell them goes in its answer, the report, or a reminder on the calendar.

    def current_time(args: Dict[str, Any]) -> Any:
        now = utcnow()
        # The user's clock by name, as a time for schedule_task is given.
        return {"utc": now.isoformat(), "local": datetime.now().isoformat(),
                "user_clock": now_line()}

    registry.add(
        ToolSpec("current_time", "Get the current UTC and local time.", _schema({}, [])),
        current_time,
    )

    return registry


def result_to_text(value: Any, limit: int = 8000) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = repr(value)
    return text if len(text) <= limit else text[:limit] + " ... [truncated]"
