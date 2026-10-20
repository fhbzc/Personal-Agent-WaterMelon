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
    {"key": "calendar", "setting": "module_calendar_enabled", "name": "Calendar management",
     "what": "The agent heartbeat's notification step - reminders added, filled in and "
             "closed, and what waits for a time; invitations and agreed times acted on at "
             "once; the notification lessons learned.",
     "off": "The agent puts nothing on your calendar and closes nothing; reminders you "
            "added still ring. What waits for a time is no longer looked after."},
    {"key": "connections", "setting": "module_connections_enabled", "name": "Connections",
     "what": "The agent heartbeat finds the people in what is on your screen and adds or "
             "updates them in your contacts; the connection lessons learned. (Looking your "
             "contacts up on the web is Newspaper's.)",
     "off": "Contacts are no longer added or updated from what is on your screen. Your "
            "contacts are still looked up on the web while Newspaper is on - "
            "turn it off too to stop that."},
    {"key": "recommend", "setting": "module_recommend_enabled",
     "name": "Newspaper",
     "what": "What you read kept as a log, and an issue at the times you chose: a page for "
             "each project you are working on and for what you have been reading, each "
             "searched on the web; and your contacts looked up on the web, their public "
             "profiles filled in and their news found.",
     "off": "No issue is made and nothing read is kept; your contacts are no longer looked "
            "up on the web (from your screen they still are, while Connections is on). "
            "\"Make an issue now\" still works."},
    {"key": "experience", "setting": "module_experience_enabled", "name": "Work learning",
     "what": "Beside each state round, your work natures grouped into patterns and "
             "principles; how sure each work nature is, learned from what you do with it. "
             "(A project learned into stages is yours to start, in Work - not a function "
             "here.)",
     "off": "What was learned is still used; the natures are no longer grouped by "
            "themselves. Learning a project into stages in Work still works."},
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
    "state_heartbeat_english": "tasks",
    "heartbeat:step_nature": "core",
    "heartbeat:same_notification": "calendar",
    "heartbeat_trigger": "calendar",
    "notification_lessons": "calendar",
    "lessons_notifications": "calendar",
    "lessons_connections": "connections",
    "lessons_work": "experience",
    "experience_stages": "experience_manual",
    "experience_merge": "experience_manual",
    "state_heartbeat": "tasks",
    "connections": "recommend",
    "reading": "recommend",
    "experience": "experience",
    "heartbeat": "heartbeat (before modules)",
    "work_run": "manual_work",
    # The two final checks of a run: kinds of their own, in Work runs.
    "work_quality_check": "manual_work",
    "work_privacy_check": "manual_work",
    "agent_instruction": "manual_chat",
    "chat_followup": "manual_chat",
    "chat_history_summary": "manual_chat",
    "scheduled_task": "manual_scheduled",
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


def module_of(task: Any, by_user: bool = False) -> str:
    """The module a model call's task belongs to - and, for a function that
    runs by itself, whether the user ran it this time (`by_user`)."""
    task = str(task or "")
    if task in TASK_MODULE:
        key = TASK_MODULE[task]
    elif task.startswith("heartbeat:"):
        key = "core"
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
    return bool(config.get(one["setting"], True))


def shares(db: Any, hours: int = 72) -> Dict[str, Dict[str, Any]]:
    """What each function spent in the last `hours` - calls, tokens, the
    estimated cost - and its share of what every function that runs by
    itself spent ("of_automatic"), and of everything ("of_all"), the
    user's own work too. By cost; where nothing has a cost (a subscription
    that is not billed by the token), by tokens."""
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        rows = db.query("SELECT task, COALESCE(by_user, 0) AS by_user, COUNT(*) AS calls, "
                        "COALESCE(SUM(total_tokens), 0) AS tokens, "
                        "COALESCE(SUM(estimated_cost), 0) AS cost FROM model_calls "
                        "WHERE created_at >= ? GROUP BY task, COALESCE(by_user, 0)", (since,))
    except Exception:
        rows = []
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        mine = out.setdefault(module_of(row["task"], bool(row["by_user"])),
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
            "modules": [dict(one, enabled=enabled(config, one["key"]), spent=spent(one["key"]))
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
           "NAMES"]
