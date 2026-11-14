"""THE AGENT'S MODULES - what it does by itself, each of which the user can turn
off to spend fewer tokens (Settings > Modules). Only what runs by itself is a
module: a heartbeat's step, a trigger, a clock. What the user starts - a work
run, a conversation, a scheduled job - is not: they choose it each time.

Each model call says what it was for (its `task`); `module_of` says which
module that is, so the tokens are counted per module.
"""

from __future__ import annotations

from typing import Any, Dict, List

#: Always on: everything else is built on it.
CORE = {"key": "core", "name": "Activity and states",
        "what": "What is on your screen recorded, and the agent heartbeat's first step: "
                "what you are doing, written as states.",
        "off": ""}

MODULES: List[Dict[str, str]] = [
    {"key": "tasks", "setting": "module_tasks_enabled", "name": "Task organisation",
     "what": "The state heartbeat writes down what you did in each stretch as tasks, with "
             "how long you worked on each, and groups them into projects that it names and "
             "follows over time.",
     "off": "No new tasks or projects. Newspaper and Work lose the background of what you "
            "are working on."},
    # THE DAY'S PLAN (heartbeat/agenda/), apart from Task organisation (the user, 2026-11-10: "auto
    # work, 包括planning那一整条都是可以关掉的"; "planning是大功能，auto-work是planning下的一个子功能，大功能关了
    # 子功能也关，但是允许子功能关大功能不关").
    {"key": "agenda", "setting": "module_agenda_enabled", "name": "The day's plan",
     "what": "Everything on your plate sorted - what the agent can do and what needs you - and "
             "planned into your day and the days ahead, adjusted as the day goes; a reminder as "
             "each block begins, what is waiting for your approval, and a break when you usually "
             "take one; what has gone away cleared from your lists.",
     "off": "No plan is made or followed: no block reminders, and no work handed to the agent. "
            "Your to-dos and calendar stay as they are; Plan now still works."},
    {"key": "agenda_work", "parent": "agenda", "setting": "module_agenda_work_enabled",
     "name": "Work the plan hands to the agent",
     "what": "The plan's things the agent can do, started by themselves when their time comes - "
             "as it would do them fully on its own, except that it acts only in its own work "
             "folder: when one is done you are told at once, to look at it before anything else "
             "is done.",
     "off": "The plan is still made and still reminds you, but the agent starts none of it: its "
            "part stays on the plan, for you."},
    {"key": "calendar", "setting": "module_calendar_enabled", "name": "Calendar management",
     "what": "The agent heartbeat's notification step - reminders added, filled in and "
             "closed, and what waits for a time; invitations and agreed times acted on at "
             "once; the notification lessons learned.",
     "off": "The agent puts nothing on your calendar and closes nothing; reminders you "
            "added still ring. What waits for a time is no longer looked after."},
    {"key": "connections", "setting": "module_connections_enabled", "name": "Connections",
     "what": "The agent heartbeat finds the people in what is on your screen and adds or "
             "updates them in your contacts; with each issue of the Newspaper, your "
             "contacts and you yourself looked up on the web, their public profiles filled "
             "in and their news found; the connection lessons learned.",
     "off": "Contacts are no longer added or updated, from your screen or from the web, "
            "and no one is looked up: an issue carries the news alone."},
    {"key": "recommend", "setting": "module_recommend_enabled",
     "name": "Newspaper",
     "what": "What you read kept as a log, and an issue at the times you chose: what your "
             "work would want to know and a page for each project you are working on and "
             "for what you have been reading, each searched on the web - and, while "
             "Connections is on, what your contacts have been doing.",
     "off": "No issue is made and nothing read is kept, so no one is looked up on the web "
            "either. \"Make an issue now\" still works."},
    {"key": "recommendation", "setting": "module_recommendation_enabled",
     "name": "Recommendations",
     "what": "Things worth telling you, made ready ahead from what the Newspaper left over and "
             "from the web, and told at a moment that suits, each with why. What you answer "
             "teaches it what to recommend. It runs on the Monitor.",
     "off": "Nothing is recommended by itself and no model is asked for it. The Newspaper and "
            "the Monitor's other work go on."},
    {"key": "monitor", "setting": "module_monitor_enabled", "name": "Monitor",
     "what": "The real-time signals - what you are doing, since when, how busy, your habits "
             "- and the Activators run on them: spam and scam warnings, recommendations at "
             "the right moments, breaks and the evening report; the heartbeat's step that "
             "judges what they queue and reprograms them.",
     "off": "Nothing is watched for and nothing is recommended or warned of by itself; "
            "the meeting invitations and agreed times are still acted on (Calendar)."},
    {"key": "experience", "setting": "module_experience_enabled", "name": "Work learning",
     "what": "Your work - your Work projects and the places you list in Work's Learning "
             "panel, with your coding agents' conversations - read into cases and the "
             "reasons behind how you judge your work, at the times you set there (when "
             "its switch is on).",
     "off": "No update by itself; what was learned is still used, and Update now in Work "
            "still works."},
    # LEARNING FROM WHAT THE USER DOES (the user, 2026-11-10: "所有的learning (基于反馈进行的分析）都是自动
    # 调用的，要单独进一个功能。如果这个功能被关了feedback还是会记录，但是不会进行学习").
    {"key": "learning", "setting": "module_learning_enabled", "name": "Learning from your answers",
     "what": "What you do with what the agent tells you and makes - every answer, edit, deletion "
             "and correction - read for the lessons in it: the notification, contact, "
             "recommendation, work and general lessons, their patterns, and the reasons behind "
             "your answers.",
     "off": "What you do is still written down, but nothing is learned from it: the lessons stay "
            "as they are, and once it is on again, all that was kept meanwhile is learned from."},
]

#: WHAT THE USER STARTS, counted apart from the functions and from one
#: another, but not a function: no switch, and no share of what the
#: functions spent. Each its own: a work run, a conversation, a scheduled
#: task, lessons the user asked about, a task learned into stages in
#: Experience, and the rest.
MANUAL_GROUPS = [
    ("manual_work", "Work runs"),
    ("manual_chat", "Chat"),
    ("manual_scheduled", "Scheduled tasks"),
    ("manual_lessons", "Lessons"),
    ("experience_manual", "Work: tasks into stages"),
    ("manual_other", "Diagnostics and the rest"),
]
MANUAL_KEYS = tuple(key for key, _name in MANUAL_GROUPS)
#: A function that runs by itself, run by the user with a button: its own
#: key with this after it, counted with what the user started.
BY_YOU = ":by_you"

BY_KEY = {one["key"]: one for one in MODULES}

#: What each model call was for, and the module it belongs to; "heartbeat"
#: alone is the agent heartbeat from before its steps were told apart.
TASK_MODULE = {
    "heartbeat:step_state": "core",
    "heartbeat:step_people": "connections",
    "heartbeat:step_notify": "calendar",
    "heartbeat:step_todo": "tasks",
    # What a to-do leaving the list leaves behind (agent/feedback/after_todo.py).
    "after_todo": "tasks",
    # The to-do list looked over (heartbeat/core/todo_check.py), and the day's agenda
    # (heartbeat/agenda/): its things sorted, the day built and reviewed.
    "todo_check": "tasks",
    "todo_check_words": "tasks",
    "agenda_words": "agenda",
    "agenda_triage": "agenda",
    "agenda_build": "agenda",
    "agenda_review": "agenda",
    "state_heartbeat_english": "tasks",
    "heartbeat:same_notification": "calendar",
    "heartbeat_trigger": "calendar",
    # THE MONITOR (heartbeat/monitor/): a recommendation, the evening report, its step.
    "monitor_recommend": "recommendation",
    # Back to a project left a while (heartbeat/monitor/decide.py).
    "monitor_old_project": "monitor",
    "recommendation_prepare": "recommendation",
    "recommendation_judge": "recommendation",
    "monitor_evening": "monitor",
    "monitor_morning": "monitor",
    # The moment said again in English before its lessons are looked for.
    "monitor_lessons_query": "monitor",
    "calendar_lessons_query": "calendar",
    "connections_lessons_query": "connections",
    "heartbeat:step_monitor": "monitor",
    # Every lesson learned from what the user did: Learning from your answers.
    "notification_lessons": "learning",
    "lessons_notifications": "learning",
    "lessons_connections": "learning",
    "lessons_recommendations": "learning",
    "lessons_general": "learning",
    "lessons_work": "learning",
    "lessons_groups": "learning",
    # The reasons behind the user's answers (learning/reasons.py: predict, across).
    "learning_answers": "learning",
    "experience_stages": "experience_manual",
    "experience_merge": "experience_manual",
    "state_heartbeat": "tasks",
    # AN ISSUE'S TWO PARTS (the user, 2026-10-30): the contacts, which stop
    # with Connections, and the news. Calls from before the two were named
    # apart were most of them the contacts' search: Connections'.
    "connections": "connections",
    "newspaper": "recommend",
    "reading": "recommend",
    "experience": "experience",
    # THE LEARNING (learning/): its folders mapped, its units read, its
    # reasons found, its words kept; a plan for an item of its list drafted,
    # and the heartbeat's look for what the user endorses (learning/items.py).
    "learning_folders": "experience",
    "learning_read": "experience",
    "learning_reasons": "experience",
    "learning_terms": "experience",
    "learning_plan": "experience",
    # What was handed over split into units, and followed (learning/sources.py).
    "learning_split": "experience",
    "learning_interests": "experience",
    "heartbeat": "heartbeat (before modules)",
    "work_run": "manual_work",
    # The two final checks of a run: kinds of their own, in Work runs.
    "work_quality_check": "manual_work",
    "work_privacy_check": "manual_work",
    # The quick way's monitor (2026-10-31): the user's own work too.
    "work_monitor": "manual_work",
    # Whether a name read on a mail page is who a reply is for (handed.py).
    "reply_person": "manual_work",
    "agent_instruction": "manual_chat",
    "chat_fast": "manual_chat",
    "chat_followup": "manual_chat",
    "chat_history_summary": "manual_chat",
    # A summary document of answers the user ticked (agent/chat_summary.py).
    "chat_summary_document": "manual_chat",
    "scheduled_task": "manual_scheduled",
    # The to-do list and Work assigned grouped (agent/feedback/list_groups.py).
    "list_grouping": "tasks",
    "verification": "manual_scheduled",
    "task_completion": "manual_scheduled",
    "lessons_package": "manual_lessons",
    "lessons": "manual_lessons",
    "diagnostic": "manual_other",
    # REMOVED from the application on 2026-10-06 - the data heartbeat and
    # the knowledge library - and their calls still in the chart's month:
    # Other, the user's choice; they take no colour from what still runs.
    # (A function only switched off is not this: it keeps its own.)
    "data_heartbeat": "other",
    "library_compaction": "other",
    "library_relabel": "other",
}
#: A task named in no module is shown by its own name, so nothing spends
#: unseen under "Other": this before it.
UNNAMED = "task:"
#: Kept for the token chart's older readers: every task the user starts.
MANUAL_TASKS = tuple(task for task, key in TASK_MODULE.items() if key in MANUAL_KEYS)

#: What each is called where it is shown.
NAMES = dict([(CORE["key"], CORE["name"])] + [(one["key"], one["name"]) for one in MODULES]
             + [(key, f"Started by you: {name}") for key, name in MANUAL_GROUPS]
             + [("heartbeat (before modules)", "Agent heartbeat (before modules)"),
                ("other", "Other")])


def is_manual(key: str) -> bool:
    """Whether what a key counts was started by the user."""
    return key in MANUAL_KEYS or str(key).endswith(BY_YOU)


def module_of(task: Any, by_user: bool = False, module: str = "") -> str:
    """The module a model call's task belongs to - and, for a function that
    runs by itself, whether the user ran it this time (`by_user`). `module`: the one the call
    was made for, where it was said (common/observability/started.py: the plan's own work)."""
    task = str(task or "")
    if module and module in BY_KEY:
        return module
    if task in TASK_MODULE:
        key = TASK_MODULE[task]
    elif task.startswith("heartbeat:"):
        key = "core"
    elif task.startswith("lessons_") and task != "lessons_package":
        key = "learning"
    elif task.startswith("work"):
        key = "manual_work"
    elif task:
        key = UNNAMED + task
    else:
        key = "other"
    if by_user and not is_manual(key) and key not in ("other", "heartbeat (before modules)")             and not key.startswith(UNNAMED):
        return key + BY_YOU
    return key


def module_name(key: str) -> str:
    """What a module key is called where it is shown."""
    key = str(key or "")
    if key.startswith(UNNAMED):
        return f"Not in a module: {key[len(UNNAMED):]}"
    if key.endswith(BY_YOU):
        return f"{NAMES.get(key[:-len(BY_YOU)], key[:-len(BY_YOU)])} (started by you)"
    return NAMES.get(key, key)


def enabled(config: Any, key: str) -> bool:
    """Whether a module is on - the core always is."""
    if key == "core" or config is None:
        return True
    one = BY_KEY.get(key)
    if one is None:
        return True
    if one.get("parent") and not enabled(config, one["parent"]):
        return False                       # a part of a module turned off is off with it
    return own_switch(config, key)


def own_switch(config: Any, key: str) -> bool:
    """A module's own switch, whatever its parent's."""
    one = BY_KEY.get(key)
    if config is None or one is None:
        return True
    return bool(config.get(one["setting"], True))


#: WHO STARTED A PIECE OF WORK, by the work's source (agent/work/delegate.py), when it was not
#: the user: its calls are that module's, not "Started by you" (the user, 2026-11-10).
STARTED_BY_SOURCE = {"agenda": "agenda_work"}


def shares(db: Any, hours: int = 72) -> Dict[str, Dict[str, Any]]:
    """What each function spent in the last `hours` - calls, tokens, the
    estimated cost - and its share of what every function that runs by
    itself spent ("of_automatic"), and of everything ("of_all"), the
    user's own work too. By cost; where nothing has a cost (a subscription
    that is not billed by the token), by tokens."""
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        rows = db.query("SELECT task, COALESCE(by_user, 0) AS by_user, "
                        "COALESCE(module, '') AS module, COUNT(*) AS calls, "
                        "COALESCE(SUM(total_tokens), 0) AS tokens, "
                        "COALESCE(SUM(estimated_cost), 0) AS cost FROM model_calls "
                        "WHERE created_at >= ? GROUP BY task, COALESCE(by_user, 0), "
                        "COALESCE(module, '')", (since,))
    except Exception:
        rows = []
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        mine = out.setdefault(module_of(row["task"], bool(row["by_user"]), row["module"]),
                              {"calls": 0, "total_tokens": 0, "estimated_cost": 0.0})
        mine["calls"] += int(row["calls"])
        mine["total_tokens"] += int(row["tokens"])
        mine["estimated_cost"] = round(mine["estimated_cost"] + float(row["cost"] or 0), 6)
    by_cost = any(one["estimated_cost"] > 0 for one in out.values())
    measure = "estimated_cost" if by_cost else "total_tokens"
    automatic = sum(one[measure] for key, one in out.items() if not is_manual(key))
    everything = sum(one[measure] for one in out.values())
    for key, one in out.items():
        one["by"] = "cost" if by_cost else "tokens"
        one["of_automatic"] = (round(100.0 * one[measure] / automatic, 1)
                               if automatic and not is_manual(key) else 0.0)
        one["of_all"] = round(100.0 * one[measure] / everything, 1) if everything else 0.0
    return out


def _together(parts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Several of what was spent, as one."""
    out: Dict[str, Any] = {"calls": 0, "total_tokens": 0, "estimated_cost": 0.0,
                           "of_automatic": 0.0, "of_all": 0.0}
    for one in parts:
        out["calls"] += int(one.get("calls") or 0)
        out["total_tokens"] += int(one.get("total_tokens") or 0)
        out["estimated_cost"] = round(out["estimated_cost"] + float(one.get("estimated_cost") or 0), 6)
        out["of_all"] = round(out["of_all"] + float(one.get("of_all") or 0), 1)
        out["by"] = one.get("by", out.get("by"))
    return out


def described(config: Any, usage: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Every module, on or off, with what it does, what turning it off means,
    and what it spent lately."""
    def spent(key: str) -> Dict[str, Any]:
        return usage.get(key) or {"calls": 0, "total_tokens": 0, "estimated_cost": 0.0}
    return {"core": dict(CORE, enabled=True, spent=spent("core")),
            # `on`: its own switch, as its box shows it - a part of a module off is off however
            # its own is set (`enabled`).
            "modules": [dict(one, enabled=enabled(config, one["key"]),
                             on=own_switch(config, one["key"]), spent=spent(one["key"]))
                        for one in MODULES],
            # What the user started, all of it - and each kind of it apart:
            # their work, chat, scheduled tasks, lessons, Experience learned,
            # and the functions they ran with a button.
            "manual": _together([one for key, one in usage.items() if is_manual(key)]),
            "manual_parts": sorted(
                [dict(spent(key), key=key, name=module_name(key))
                 for key in usage if is_manual(key)],
                key=lambda one: (-float(one.get("estimated_cost") or 0),
                                 -int(one.get("total_tokens") or 0))),
            # Automatic calls counted under no function: the agent heartbeat's
            # from before its steps were counted apart - shown, so the shares
            # add up.
            "unassigned": [dict(one, name=key) for key, one in sorted(usage.items())
                           if key not in BY_KEY and key != "core" and not is_manual(key)],
            # For the token chart, to tell each call's module.
            "task_modules": dict(TASK_MODULE), "manual_tasks": list(MANUAL_TASKS),
            "names": dict(NAMES)}


__all__ = ["MODULES", "CORE", "TASK_MODULE", "MANUAL_KEYS", "MANUAL_GROUPS", "BY_YOU",
           "module_of", "module_name", "is_manual", "enabled", "described", "BY_KEY",
           "NAMES", "own_switch", "STARTED_BY_SOURCE"]
