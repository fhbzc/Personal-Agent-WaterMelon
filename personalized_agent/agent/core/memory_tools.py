"""THE CHAT'S SEARCHES OF WHAT THE APPLICATION KEEPS (the user, 2026-11-07: retrieval comes back
as TOOLS, each saying what it finds and when to use it - the model decides whether to search).

    search_todos      the user's to-do list, open and done
    search_work       their projects and tasks: what each is, how it stands, its open steps
    search_lessons    what was learned of how they want things done, and why they judge so
    search_activity   what they were doing in a stretch of time (the states)

Nothing is given with a message: the old library search was taken out of the chat on
2026-10-06 because what it gave unasked was a quarter duplicates, mechanical lines ("1886 files
edited", "16 pages on www.amazon.com", "firefox.exe - 13:39"), the agent's own heartbeat notes,
and so much of it (~150k characters) that answers failed. So each search here gives one copy of
each thing, leaves the mechanical lines and the agent's own records out, and stops at
`MOST_CHARS`, saying how many more there were.

READ ONLY: every search reads through `ReadOnly`, which refuses any write.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from ...models.base import ToolSpec

log = logging.getLogger(__name__)

#: All one search gives back, in characters; one item at most; and items, unless asked for fewer.
MOST_CHARS, MOST_ITEM, MOST_ITEMS = 6000, 600, 20
#: With no words and no time, the activity of this many hours before now.
LAST_HOURS = 24
#: The rows looked through for one search.
MOST_ROWS = 5000
#: The lesson-retriever's place, for whoever counts where lessons are used.
SITE = "chat"

NAMES = ("search_todos", "search_work", "search_lessons", "search_activity")


class ReadOnly:
    """A database that only reads: what these searches are given, so none of them can write."""

    def __init__(self, db: Any) -> None:
        self._db = db

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        if not re.match(r"\s*(select|with)\b", str(sql), re.I):
            raise PermissionError("the chat's searches only read")
        return self._db.query(sql, params)

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        raise PermissionError("the chat's searches only read")

    executemany = execute


# -- one copy of each, and no noise ------------------------------------------------------------
#: Counts and nothing else: "1886 files edited", "16 pages on www.amazon.com".
_COUNT = re.compile(r"^\W*\d[\d,.]*\s+[^\W\d]+(\s+[^\W\d]+)?(\s+(on|in|at|from|of)\s+\S+)?\W*$", re.I)
#: An application and a time and nothing else: "firefox.exe - 13:39", "Slack — 09:02".
_APP_TIME = re.compile(r"^\W*(\S+\.(exe|app)\b.*?|[\w.\-]+\s*[-–—|]\s*)\d{1,2}:\d{2}(:\d{2})?\W*$", re.I)
_APP_ALONE = re.compile(r"^\W*\S+\.(exe|app)\W*$", re.I)
#: What the agent wrote of itself: its checks, its scheduled tasks, its heartbeat.
_AGENTS_OWN = re.compile(r"^\W*(the agent\b|scheduled:|heartbeat\b|maintenance\b|"
                         r"the (state |agent )?heartbeat\b)", re.I)


def noise(text: Any) -> bool:
    """A mechanical line: a bare count, an application and a time, or nearly nothing."""
    text = " ".join(str(text or "").split())
    return (len(text) < 3 or bool(_COUNT.match(text)) or bool(_APP_TIME.match(text))
            or bool(_APP_ALONE.match(text)))


def agents_own(text: Any) -> bool:
    return bool(_AGENTS_OWN.match(str(text or "")))


def _clean(text: Any) -> str:
    """Its lines that say something, as one line."""
    lines = [" ".join(one.split()) for one in str(text or "").splitlines()]
    return " ".join(one for one in lines if one and not noise(one))


def _key(text: Any) -> str:
    return " ".join(re.sub(r"[\W_]+", " ", str(text or "").casefold()).split())


def _words(text: Any) -> set:
    from ...heartbeat.lessons.retrieve import words_of
    return words_of(text)


def _score(query: str, asked: set, text: str) -> float:
    """How much of what was asked for this holds: its words, or the words as written."""
    if not query:
        return 1.0
    low = text.casefold()
    whole = 1.0 if query.casefold() in low else 0.0
    if not asked:
        return whole
    return len(asked & _words(text)) / len(asked) + whole


def _limit(args: Dict[str, Any]) -> int:
    try:
        return max(1, min(50, int(args.get("limit") or MOST_ITEMS)))
    except (TypeError, ValueError):
        return MOST_ITEMS


def _header(line: str) -> bool:
    """A heading inside the results ("WHAT THE USER ACTUALLY DID:"), not an item."""
    return bool(line) and not line.startswith(("-", " ")) and line.rstrip().endswith(":")


def _cut(line: str) -> str:
    return line if len(line) <= MOST_ITEM else line[:MOST_ITEM - 1].rstrip() + "…"


def packed(heading: str, lines: Iterable[str], total: int = 0, most: int = MOST_CHARS) -> str:
    """The heading and the lines, each once, each line cut at `MOST_ITEM`, all within `most`; and
    how many more of the `total` found there were."""
    out: List[str] = []
    seen: set = set()
    used, shown, items = len(heading) + 1, 0, 0
    for line in lines:
        if _header(line):
            out.append(line)
            used += len(line) + 1
            continue
        # The same thing kept twice is the same words under another id, date or kind.
        key = _key(re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", line))
        if not key or key in seen:
            continue
        seen.add(key)
        items += 1
        line = "\n".join(_cut(one) for one in line.split("\n"))
        if used + len(line) + 1 > most - 160:
            continue
        out.append(line)
        used += len(line) + 1
        shown += 1
    # A heading with nothing shown under it goes.
    out = [one for n, one in enumerate(out)
           if not _header(one) or (n + 1 < len(out) and not _header(out[n + 1]))]
    if not shown:
        return f"{heading}\nNothing found."
    left = max(total, items) - shown
    tail = (f"\n({left} more not shown: search with other words or a shorter time.)"
            if left > 0 else "")
    return heading + "\n" + "\n".join(out) + tail


def _day(stamp: Any) -> str:
    return str(stamp or "")[:10]


def _local(stamp: Any) -> str:
    """A stored time on the user's clock."""
    from ...common.events.models import parse_iso
    from ...scheduler.core import zones
    try:
        return zones.wall_of(parse_iso(str(stamp)), zones.here()).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(stamp or "")[:16].replace("T", " ")


# -- the to-dos ---------------------------------------------------------------------------------
def search_todos(db: Any, args: Dict[str, Any]) -> str:
    query = str(args.get("query") or "").strip()
    asked = _words(query)
    done_too = args.get("include_done", True) is not False
    rows = db.query("SELECT * FROM todo_items WHERE deleted_at = '' ORDER BY created_at DESC "
                    "LIMIT ?", (MOST_ROWS,))
    found: List[Tuple[float, int, str]] = []
    seen: set = set()
    for n, row in enumerate(rows):
        if row.get("done_at") and not done_too:
            continue
        text, detail = _clean(row.get("text")), _clean(row.get("detail"))
        if not text or agents_own(text) or _key(f"{text} {detail}") in seen:
            continue
        score = _score(query, asked, f"{text} {detail}")
        if score <= 0:
            continue
        seen.add(_key(f"{text} {detail}"))
        line = f"- [{row['id']}] {text}" + (f" - {detail}" if detail else "")
        line += f" (added {_day(row.get('created_at'))}"
        line += f"; done {_day(row['done_at'])})" if row.get("done_at") else "; open)"
        # Open first, then the most like it, then the newest.
        found.append((-(score + (0 if row.get("done_at") else 0.5)), n, line))
    found.sort()
    limit = _limit(args)
    heading = f"The user's to-dos{f' about {query!r}' if query else ''}:"
    return packed(heading, [one[2] for one in found[:limit]], len(found))


# -- projects and tasks -------------------------------------------------------------------------
def search_work(db: Any, args: Dict[str, Any]) -> str:
    from ...heartbeat.states.project_round import is_scheme_row, project_view
    from ...heartbeat.states.tasks import Kept
    query = str(args.get("query") or "").strip()
    asked = _words(query)
    tasks_by_project: Dict[str, List[Dict[str, Any]]] = {}
    for row in db.query("SELECT * FROM state_run_tasks WHERE scope = 'live' AND put_away = '' "
                        "ORDER BY COALESCE(ends_at, written_at) DESC LIMIT ?", (MOST_ROWS,)):
        title = _clean(row.get("title_display") or row.get("title"))
        if not title or agents_own(title) or noise(title):
            continue
        tasks_by_project.setdefault(str(row.get("project_id") or ""), []).append(row)

    def task_text(task: Dict[str, Any]) -> str:
        return " ".join(_clean(task.get(key)) for key in (
            "title_display", "title", "summary_display", "summary"))

    def task_line(task: Dict[str, Any]) -> str:
        title = _clean(task.get("title_display") or task.get("title"))
        summary = _clean(task.get("summary_display") or task.get("summary"))
        minutes = float(task.get("minutes") or 0)
        return (f"    task: {title}" + (f" - {summary[:240]}" if summary else "")
                + f" ({round(minutes)} min, {task.get('kind') or 'work'}, "
                  f"ended {_day(task.get('ends_at') or task.get('written_at'))})")

    found: List[Tuple[float, str, List[str]]] = []
    known = set()
    for row in Kept(db, "live").all(limit=MOST_ROWS):
        if not is_scheme_row(row) or getattr(row, "retired_at", ""):
            continue
        view = project_view(row)
        known.add(view["id"])
        title = _clean(view.get("title") or (view.get("in_english") or {}).get("title"))
        if not title or agents_own(title):
            continue
        what = _clean(view.get("what_it_is") or view.get("summary"))
        steps = [_clean(one.get("text")) for one in view.get("steps") or [] if not one.get("done")]
        before = " ".join((getattr(row, "described", None) or {}).get("names_before") or [])
        english = " ".join(str(v) for v in (view.get("in_english") or {}).values())
        own = _score(query, asked, f"{title} {what} {' '.join(steps)} {before} {english}")
        tasks = tasks_by_project.get(view["id"], [])
        matched = [one for one in tasks if query and _score(query, asked, task_text(one)) > 0]
        best = max([own] + [_score(query, asked, task_text(one)) * 0.8 for one in matched])
        if best <= 0:
            continue
        status = ("dormant" if view.get("dormant") else "active")
        hours = round(float(view.get("minutes") or 0) / 60, 1)
        line = (f"- PROJECT [{view['id']}] {title} ({view.get('kind') or 'work'}, {status}, "
                f"last worked {_day(view.get('last_seen')) or 'unknown'}, {hours} h in all, "
                f"{len(tasks)} task{'' if len(tasks) == 1 else 's'})" + (f": {what[:300]}" if what else ""))
        if steps:
            line += " Open steps: " + "; ".join(one for one in steps[:8] if one)
        under = [task_line(one) for one in (matched or tasks)[:3]]
        found.append((-best, str(view.get("last_seen") or ""), [line] + under))
    # Tasks in no project the search can show.
    for project_id, tasks in tasks_by_project.items():
        if project_id in known:
            continue
        for task in tasks:
            score = _score(query, asked, task_text(task))
            if score > 0:
                found.append((-score * 0.9, str(task.get("ends_at") or ""),
                              ["- TASK " + task_line(task).strip()[len("task: "):]]))
    # The most like it first; among equals, the latest worked on.
    found.sort(key=lambda one: one[1], reverse=True)
    found.sort(key=lambda one: one[0])
    limit = _limit(args)
    heading = f"The user's projects and tasks{f' about {query!r}' if query else ''}:"
    lines = ["\n".join(one[2]) for one in found[:limit]]
    return packed(heading, lines, len(found))


# -- what was learned ----------------------------------------------------------------------------
def _retrieved(retriever: Any, query: str, limit: int) -> str:
    """By the lessons' own retriever - by meaning where it can - at the chat's place when it
    takes one."""
    import inspect
    kwargs: Dict[str, Any] = {"rules": limit, "examples": max(3, limit // 2)}
    try:
        taken = inspect.signature(retriever.text).parameters
        if "site" in taken or any(one.kind == one.VAR_KEYWORD for one in taken.values()):
            kwargs["site"] = SITE
    except (TypeError, ValueError):
        pass
    return str(retriever.text(("general",), query, **kwargs) or "")


def _by_words(db: Any, query: str, limit: int) -> Tuple[List[str], int]:
    """The rules, the examples and the reasons, found by the words they share with the query."""
    asked = _words(query)
    found: List[Tuple[float, int, str]] = []
    seen: set = set()
    for n, rule in enumerate(db.query(
            "SELECT * FROM lesson_rules WHERE state IN ('active', 'candidate') "
            "ORDER BY confidence DESC LIMIT ?", (MOST_ROWS,))):
        statement = _clean(rule.get("statement"))
        score = _score(query, asked, statement)
        if statement and score > 0 and _key(statement) not in seen:
            seen.add(_key(statement))
            how = "in force" if rule.get("state") == "active" else "a possible rule, still forming"
            found.append((-score, n, f"- [{rule['id']}] ({rule.get('kind')}, {how}, "
                                     f"{round(float(rule.get('confidence') or 0) * 100)}% sure"
                                     f"{', set by the user' if rule.get('pinned') else ''}) "
                                     f"{statement}"))
    try:
        from ...learning.find import Finder
        finder = Finder(db)
        for n, reason in enumerate(finder.store.reasons(("active",))):
            if not finder._giveable(reason):
                continue
            when = str((reason.get("condition") or {}).get("text") or "")
            statement = _clean(reason.get("statement"))
            score = _score(query, asked, f"{statement} {when}")
            if statement and score > 0:
                found.append((-score, 10_000 + n, f"- [{reason['id']}] (why they judge so"
                                                  f"{', said by the user' if reason.get('source') == 'stated' else ''})"
                                                  f" {statement}" + (f" When: {when}" if when else "")))
    except Exception:
        log.debug("what was learned of the user's reasons could not be read", exc_info=True)
    found.sort()
    # Two thirds of the room to the rules, the rest to what the user did.
    rules = max(1, limit * 2 // 3)
    lines = [one[2] for one in found[:rules]]
    examples: List[Tuple[float, int, str]] = []
    for n, one in enumerate(db.query(
            "SELECT * FROM lesson_evidence WHERE action != 'outcome' ORDER BY at DESC LIMIT ?",
            (MOST_ROWS,))):
        said = _clean(one.get("english") or one.get("summary"))
        score = _score(query, asked, f"{said} {one.get('category') or ''} {one.get('reason') or ''}")
        if said and score > 0:
            line = f"- {_day(one.get('at'))} [{one.get('kind')}] {one.get('action') or ''}: {said}"
            if one.get("reason"):
                line += f" - the user's words: \"{_clean(one['reason'])[:300]}\""
            examples.append((-score, n, line))
    examples.sort()
    if examples:
        lines += ["WHAT THE USER ACTUALLY DID:"] + [one[2] for one in
                                                    examples[:max(2, limit - rules)]]
    return lines, len(found) + len(examples)


def search_lessons(db: Any, retriever: Any, args: Dict[str, Any]) -> str:
    query = str(args.get("query") or "").strip()
    limit = _limit(args)
    lines: List[str] = []
    total = 0
    if retriever is not None:
        try:
            for one in _retrieved(retriever, query, limit).splitlines():
                if not one.strip() or noise(one):
                    continue
                if one.startswith(" ") and lines and not _header(lines[-1]):
                    lines[-1] += "\n" + one          # an example's words, under it
                else:
                    lines.append(one)
        except Exception:
            log.info("the lessons could not be looked for by meaning", exc_info=True)
            lines = []
    if not lines:
        lines, total = _by_words(db, query, limit)
    heading = (f"What was learned of how the user wants things done"
               f"{f', like {query!r}' if query else ''} (the user's own actions and words; "
               "a rule 'still forming' is only a guess):")
    return packed(heading, lines, total)


# -- what they were doing --------------------------------------------------------------------------
def _moment(written: Any) -> Tuple[Optional[datetime], str]:
    from ...scheduler.core.given import time_given
    moment, _note, error = time_given({"at_local": str(written), "zone": "user"})
    return moment, error


def search_activity(db: Any, args: Dict[str, Any], now: Optional[datetime] = None) -> Any:
    query = str(args.get("query") or "").strip()
    asked = _words(query)
    now = now or datetime.now(timezone.utc)
    since: Optional[datetime] = None
    until: Optional[datetime] = None
    for key in ("from_local", "until_local"):
        if args.get(key):
            moment, error = _moment(args[key])
            if error or moment is None:
                return {"error": f"{key}: {error or 'could not be read'}"}
            if key == "from_local":
                since = moment
            else:
                until = moment
    if since is None and until is None and not query:
        since = now - timedelta(hours=LAST_HOURS)
    if since and until and since >= until:
        return {"error": "from_local must be before until_local"}
    where, params = ["status != 'retired'", "about = 'user'", "source != 'task'"], []
    if since is not None:
        where.append("(ends_at IS NULL OR ends_at >= ?)")
        params.append(since.astimezone(timezone.utc).isoformat())
    if until is not None:
        where.append("(starts_at IS NULL OR starts_at <= ?)")
        params.append(until.astimezone(timezone.utc).isoformat())
    rows = db.query(f"SELECT id, title, detail, source, starts_at, ends_at, metadata, created_at "
                    f"FROM states WHERE {' AND '.join(where)} "
                    "ORDER BY COALESCE(starts_at, created_at) DESC LIMIT ?", (*params, MOST_ROWS))
    found: List[Tuple[float, int, str, str]] = []
    times: Dict[str, int] = {}
    for n, row in enumerate(rows):
        title, detail = _clean(row.get("title")), _clean(row.get("detail"))
        if not title or noise(title) or agents_own(title):
            continue
        score = _score(query, asked, f"{title} {detail}")
        if score <= 0:
            continue
        key = _key(title)
        times[key] = times.get(key, 0) + 1
        if times[key] > 1:
            continue
        try:
            kind = str((json.loads(row.get("metadata") or "{}") or {}).get("kind") or "")
        except ValueError:
            kind = ""
        start, end = _local(row.get("starts_at") or row.get("created_at")), \
            _local(row.get("ends_at")) if row.get("ends_at") else "still going"
        span = f"{start} - {end[11:] if end[:10] == start[:10] else end}"
        found.append((-score if query else 0, n, key,
                      f"- {span}{f' ({kind})' if kind else ''}"
                      f"{' [the user said so]' if row.get('source') == 'user' else ''} {title}"
                      + (f" - {detail[:300]}" if detail else "")))
    found.sort(key=lambda one: (one[0], one[1]))
    limit = _limit(args)
    lines = [one[3] + (f" (and {times[one[2]] - 1} more time{'s' if times[one[2]] > 2 else ''} "
                       "like it)" if times[one[2]] > 1 else "") for one in found[:limit]]
    span = (f" from {_local(since.isoformat())}" if since else "") + \
        (f" until {_local(until.isoformat())}" if until else "")
    heading = (f"What the user was doing{span}{f', about {query!r}' if query else ''}, "
               "newest first (as the agent read it from their activity):")
    return packed(heading, lines, len(found))


# -- the tools ---------------------------------------------------------------------------------------
def _schema(properties: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "object", "properties": properties, "required": [],
            "additionalProperties": False}


_QUERY = {"type": "string", "description": "words to look for, in any language; leave out for "
                                           "the latest"}
_LIMIT = {"type": "integer", "description": f"how many at most (default {MOST_ITEMS})"}

#: Each says what it finds and when it is worth a call.
SPECS = [
    ToolSpec("search_todos",
             "Search the user's to-do list - what they put down to do, by hand, from the chat "
             "or from a notification - open and done, each with when it was added. Use it when "
             "the message is about something they meant to do, or to see whether a thing is "
             "already on their list before adding it.",
             _schema({"query": _QUERY, "include_done": {"type": "boolean",
                                                       "description": "done ones too (default true)"},
                      "limit": _LIMIT}), read_only=True),
    ToolSpec("search_work",
             "Search the user's projects and tasks, as the agent grouped them from their "
             "activity: what each project is, whether it is active or dormant, when it was last "
             "worked on and for how long, its open next steps, and its tasks. Use it when the "
             "message is about the user's work - a project, a paper, a piece of code, what is "
             "left to do on something, what they worked on.",
             _schema({"query": _QUERY, "limit": _LIMIT}), read_only=True),
    ToolSpec("search_lessons",
             "Search what was learned from the user's own actions and words: how they want "
             "things done (rules, with how sure each is), what they actually did in moments "
             "like it, and why they judge as they do. Use it before doing something the way "
             "they would want it, or when they ask what you know of their preferences. Write "
             "the query in English, as a description of the situation.",
             _schema({"query": _QUERY, "limit": _LIMIT}), read_only=True),
    ToolSpec("search_activity",
             "Search what the user was doing, as the agent read it from their activity: spans "
             "of time, each with what they were doing and a short account. Use it for what "
             "they did at a time ('this morning', 'last Tuesday'), or when they last did "
             "something. Give a time range on the user's clock, words, or both; with neither, "
             f"the last {LAST_HOURS} hours. For the screen itself - windows and the words on "
             "them - use screen_history.",
             _schema({"query": _QUERY,
                      "from_local": {"type": "string",
                                     "description": "on the user's clock: YYYY-MM-DD HH:MM"},
                      "until_local": {"type": "string",
                                      "description": "on the user's clock: YYYY-MM-DD HH:MM"},
                      "limit": _LIMIT}), read_only=True),
]


def add_tools(registry: Any, db: Any, retriever: Callable[[], Any] = lambda: None) -> None:
    """The four searches, each reading `db` and nothing else; `retriever` gives the lessons'
    retriever when the application has one (found by meaning), or None (found by words)."""
    reading = ReadOnly(db)

    def guarded(search: Callable[[Dict[str, Any]], Any]) -> Callable[[Dict[str, Any]], Any]:
        def run(args: Dict[str, Any]) -> Any:
            try:
                return search(args or {})
            except Exception as exc:
                log.info("a chat search failed", exc_info=True)
                return {"error": f"the search could not be made: {type(exc).__name__}: {exc}"[:300]}
        return run

    handlers = {
        "search_todos": lambda args: search_todos(reading, args),
        "search_work": lambda args: search_work(reading, args),
        "search_lessons": lambda args: search_lessons(reading, retriever(), args),
        "search_activity": lambda args: search_activity(reading, args),
    }
    for spec in SPECS:
        if not registry.has(spec.name):
            registry.add(spec, guarded(handlers[spec.name]))


__all__ = ["add_tools", "SPECS", "NAMES", "ReadOnly", "noise", "packed", "MOST_CHARS",
           "search_todos", "search_work", "search_lessons", "search_activity"]
