"""TASKS AND PROJECTS (section 34.5, 2026-10-19).

What the state heartbeat makes of the user's states, in two levels:

  TASK      what one run found the user doing: the unread states of that
            stretch, grouped by a model into the distinct things done - a
            title, a summary (English, with a copy in the display
            language), its kind (work, leisure or routine), the states it
            holds, how long it was actually worked on, when, and where.
            Written once and kept (`state_run_tasks`).

  PROJECT   the tasks that serve one thing, across runs: grouped by what
            the tasks mean (embeddings of their English text) and by the
            writer's own "this continues project X", with one partition
            over both. A project CARRIES ON an earlier one that gave it 70%
            of its recent time, and keeps that one's id only when the
            earlier one is also 70% of what it was made of (`inherit`).
            Kept as rows of
            `state_tasks`, marked with this scheme, so everything that
            reads the user's work - learning, Work, recommendations, the
            English copies - reads projects.

The writer of a run is shown the projects with a task in the latest 48
hours of work, each as one line saying what it IS - one level above any
single occasion - and may say a task continues one. A project's name and
that line are written by the model when it is new, and again when its
time has grown by 30% (and half an hour) since it was last named.

Measured on a labelled week (scratchpad seq5, 2026-10-19): 24 projects,
the paper and its talks in one, the big subjects clean. A path layer
(shared folders) was tried and left out: it pulled unrelated research
together through a shared research folder.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

#: How a row of `state_tasks` written by this scheme says so (its profile).
SCHEME = "projects"

#: Two tasks are linked by meaning from this cosine similarity of their
#: English "title. summary" up. Measured: 0.55 joined the paper with an
#: unrelated talk; 0.6 kept every small subject clean.
MEANING_AT = 0.6
#: The resolution of the partition: lower is coarser.
RESOLUTION = 0.5
#: How much "the writer said it continues this project" counts, beside
#: meaning.
HINT_WEIGHT = 1.0
#: CARRYING ON (user, 2026-10-19). A project carries on an earlier one that
#: gave it this share of its time in the window: the earlier one's history
#: is its own on the Overview, and what was learned from it goes with it.
#: It also keeps the earlier one's id when the earlier one is this share
#: of what it was made of (its tasks grouped before, not the new ones).
CARRIES_AT = 0.7
#: THE WINDOW: the latest tasks until this many hours of work (work and
#: leisure), however many days that takes - days the computer was off do
#: not count. Its tasks are grouped again each run; the writer is shown
#: the projects in it; one with no task in it is dormant, and new work on
#: the same thing later is a new project.
WINDOW_HOURS = 48.0
#: The first run reads the last this many days, in stretches.
WINDOW_DAYS = 7
#: A project is named again when its time has grown by this share since it
#: was last named, and by at least this many minutes.
RENAME_GROWTH = 0.3
RENAME_LEAST_MINUTES = 30.0
#: Projects named in one call; more are named in further calls.
NAMES_PER_CALL = 20
#: COMING BACK (user, 2026-10-19): a project named or renamed in a run is
#: compared with the dormant and ended ones by what it is - its English
#: name and line against each of theirs, every earlier name too - and the
#: ones from this cosine up, the RELINK_CANDIDATES most alike, are shown
#: to the model, which says which it carries on and which it is. Measured
#: on the labelled record (scratchpad relink_*, 2026-10-19): from 0.55 the
#: subject a project came back to was among its candidates 35 times in 40
#: by day and 5 in 8 by project, with 0.2 others a project beside it;
#: shared folders found nothing meaning did not, and are not used.
RELINK_AT = 0.55
RELINK_CANDIDATES = 10
#: Tasks shown with each project in that call.
RELINK_EXAMPLES = 5
#: The first run reads the last week in stretches of this many hours, one
#: after another, so each stretch sees the projects the earlier ones made.
SLICE_HOURS = 12
#: A project's title is its most typical task of its latest run; when that
#: one is atypical, the most typical of this many hours before it.
TITLE_FALLBACK_HOURS = 36
#: States in one call; more are cut into consecutive calls.
MOST_STATES_PER_CALL = 160
KINDS = ("work", "leisure", "routine")

#: Not a place of any one piece of work: programs, the system, mail and
#: chat, search, sites visited for everything.
NOT_A_PLACE = re.compile(
    r"(?i)^(c:[\\/](program files|programdata|windows)|[a-z]:[\\/]?$)"
    r"|mail\.google|outlook\.|slack\.com|wechat|weixin|whatsapp|teams\.microsoft"
    r"|google\.com/search|bing\.com|baidu\.com|chatgpt\.com|claude\.ai/new"
    r"|bilibili\.com/?$|youtube\.com/?$|linkedin\.com/(feed|notifications)"
    r"|firefox|/applications/[^/]+\.app")
_URL = re.compile(r"""https?://[^\s<>"'|)\]]+""")
_PATH = re.compile(r"""(?i)(?:\b[a-z]:[\\/]|(?<![\w.])/(?:users|home|volumes)/)[^\s<>"'|*?]+""")


# -- the writer's instruction ----------------------------------------------------

#: The examples are made up on purpose: an example taken from the user's
#: own subjects is an answer, not an example (it made one measurement
#: worthless).
WRITER_SYSTEM = """You are the task writer of a personal activity record. You read what the \
user did in one stretch of time - STATES, a few minutes of activity each - and you write \
down the distinct things they did: their TASKS.

STEP 1 - PROJECT NAMES. When PROJECTS NEEDING A NAME are given (new, carrying on others, \
or grown since they were named), write for each a "title" - a short name for the whole \
project, at most 10 words - and ONE "line" saying WHAT THE PROJECT IS: the underlying \
thing its tasks serve, one level above any single occasion - e.g. "A grant proposal on \
coastal erosion and the meetings about it", "Restoring an old motorcycle", "Watching \
cooking videos for leisure". Not a meeting, a person or a date unless the whole project \
is that one occasion. At most 20 words.

STEP 2 - TASKS. Group the stretch's states into TASKS - the distinct things the user did. \
A task is one piece of work or one activity: building one project, preparing one talk, one \
application, watching videos for leisure, catching up on messages. Put states together when \
they serve the same thing, even when they look different (an email, a chat and a terminal \
session about the same project are one task); keep apart things that only share a tool, a \
site or a topic. Every state goes to exactly one task.

USE THE PROJECT LIST: a task continues a listed project when it serves THE SAME THING the \
project is about - the same paper, the same product, the same application - whatever the \
activity: writing it, submitting it, presenting it, or discussing it with someone. Sharing \
only a topic, a field or a website is NOT continuing. When it continues one, name it in the \
project's own words and set "continues" to that project's id; otherwise "continues" is "".

For each task: "title" (an imperative line naming the thing, at most 15 words), "summary" \
(one or two plain sentences: what was done in this stretch), "kind" ("work", "leisure", or \
"routine" = keeping up with messages, mail, feeds, chores), "continues", "states" (the state \
ids), and "title_display" and "summary_display": the same title and summary in the user's \
display language.

Answer with JSON only:
{"lines": {"<project id>": {"title": "...", "title_display": "...", "line": "...",
                            "line_display": "..."}},
 "tasks": [{"title": "...", "summary": "...", "title_display": "...",
            "summary_display": "...", "kind": "work", "continues": "",
            "states": ["s1", "s2"]}]}"""

#: Only the names, for the projects still waiting for one when a run's
#: stretches are written - all of them in one call (NAMES_PER_CALL a call).
LINES_SYSTEM = """You name the projects of a personal activity record. For each project \
given, with its tasks, write a "title" - a short name for the whole project, at most 10 \
words - and ONE "line" saying WHAT THE PROJECT IS: the underlying thing its tasks serve, \
one level above any single occasion - e.g. "A grant proposal on coastal erosion and the \
meetings about it", "Restoring an old motorcycle", "Watching cooking videos for leisure". \
Not a meeting, a person or a date unless the whole project is that one occasion. At most \
20 words. Also both in the user's display language.

Answer with JSON only: {"lines": {"<project id>": {"title": "...", "title_display": "...", \
"line": "...", "line_display": "..."}}}"""


#: The made-up examples are on purpose (see WRITER_SYSTEM).
RELINK_SYSTEM = """You keep the projects of a personal activity record together over time. \
Each NEW project below was just found in the user's latest work; with it are EARLIER \
projects that had stopped - dormant or ended - and look like it. Each comes with its name, \
what it is, and some of its tasks.

For each new project say:
- "same_as": the ONE earlier project it IS - the same piece of work resumed: most of the new \
project's content is that project's, and most of that project's is the new one's. At most \
one; "" when none. A new project marked "(already has an earlier self)" cannot be the same \
as another: give it "".
- "continues": the earlier projects it CARRIES ON - most of that earlier project's work is \
taken up in the new one, though the new one is more than it (e.g. "Draft chapter 3 of the \
cookbook" carried on by "Writing and publishing a family cookbook"). Several or none.
An earlier project goes to at most one new project. Sharing only a topic, a field, a tool \
or a website is neither. When unsure, say neither.
"why": one short sentence.

Answer with JSON only:
{"links": {"<new id>": {"same_as": "<earlier id or empty>", "continues": ["<earlier id>"], \
"why": "..."}}}"""


# -- time and places ---------------------------------------------------------------

def moment(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        found = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            found = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return found if found.tzinfo else found.replace(tzinfo=timezone.utc)


def iso(value: Optional[datetime]) -> str:
    return value.astimezone(timezone.utc).isoformat() if value else ""


def now() -> datetime:
    return datetime.now(timezone.utc)


def state_places(state: Any) -> List[str]:
    """The files, folders and pages a state rests on."""
    metadata = getattr(state, "metadata", None) or {}
    found: List[str] = []
    for key in ("sources", "evidence"):
        for one in metadata.get(key) or []:
            text = one if isinstance(one, str) else json.dumps(one, ensure_ascii=False)
            for hit in _URL.findall(text) + _PATH.findall(text):
                hit = hit.rstrip(".,;:'")
                if hit not in found:
                    found.append(hit)
    return found[:12]


def folder_of(place: str) -> str:
    """A place as the folder or page it is in: a file's folder; a page
    without its query."""
    if re.match(r"(?i)^https?://", place):
        return place.split("?")[0].split("#")[0]
    return re.sub(r"[\\/][^\\/]+\.[A-Za-z0-9]{1,5}$", "", place)


def directories(places: Iterable[str]) -> Counter:
    counted: Counter = Counter()
    for place in places:
        if place and not NOT_A_PLACE.search(place):
            counted[folder_of(place)] += 1
    return counted


def worked_minutes(states: Sequence[Any], window: Tuple[Optional[datetime],
                                                        Optional[datetime]]
                   ) -> Dict[str, float]:
    """Each state's minutes as the review said, held to what it can be:
    no more than its own span, and the stretch's states together no more
    than the stretch - scaled down alike when they are over. The review
    estimates the time; this only stops an estimate from inflating a
    project."""
    out: Dict[str, float] = {}
    for state in states:
        metadata = getattr(state, "metadata", None) or {}
        try:
            said = max(0.0, float(metadata.get("minutes") or 0.0))
        except (TypeError, ValueError):
            said = 0.0
        began, ended = moment(getattr(state, "starts_at", None)), moment(
            getattr(state, "ends_at", None))
        if began and ended and ended >= began:
            span = (ended - began).total_seconds() / 60
            said = min(said, span) if said else span
        out[str(getattr(state, "id", ""))] = said
    low, high = window
    if low and high and high > low:
        room = (high - low).total_seconds() / 60
        total = sum(out.values())
        if total > room > 0:
            scale = room / total
            out = {key: value * scale for key, value in out.items()}
    return out


# -- the tasks store -------------------------------------------------------------------

class RunTasks:
    """The tasks each run wrote. Never rewritten but for the project they
    belong to, which the grouping decides."""

    COLUMNS = ("id", "scope", "run_id", "written_at", "title", "summary",
               "title_display", "summary_display", "kind", "states", "minutes",
               "starts_at", "ends_at", "places", "continues", "project_id")

    def __init__(self, db: Any, scope: str = "live") -> None:
        self.db = db
        self.scope = str(scope or "live")

    def add(self, tasks: Sequence[Dict[str, Any]]) -> int:
        written = 0
        for task in tasks:
            try:
                self.db.execute(
                    "INSERT OR REPLACE INTO state_run_tasks (id, scope, run_id, "
                    "written_at, title, summary, title_display, summary_display, "
                    "kind, states, minutes, starts_at, ends_at, places, continues, "
                    "project_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (task["id"], self.scope, task.get("run_id", ""),
                     task.get("written_at") or iso(now()), task.get("title", ""),
                     task.get("summary", ""), task.get("title_display", ""),
                     task.get("summary_display", ""), task.get("kind", "work"),
                     json.dumps(task.get("states") or []),
                     float(task.get("minutes") or 0.0), task.get("starts_at") or None,
                     task.get("ends_at") or None,
                     json.dumps(dict(task.get("places") or {})),
                     task.get("continues", ""), task.get("project_id", "")))
                written += 1
            except Exception:  # pragma: no cover - a lost row, not a lost run
                log.debug("could not keep run task %s", task.get("id"), exc_info=True)
        return written

    def set_projects(self, project_of: Dict[str, str]) -> None:
        for task_id, project_id in project_of.items():
            try:
                self.db.execute("UPDATE state_run_tasks SET project_id = ? "
                                "WHERE id = ? AND scope = ?",
                                (project_id, task_id, self.scope))
            except Exception:  # pragma: no cover
                log.debug("could not file task %s", task_id, exc_info=True)

    def _read(self, sql: str, args: Tuple[Any, ...]) -> List[Dict[str, Any]]:
        if self.db is None:
            return []
        try:
            rows = self.db.query(sql, args)
        except Exception:
            log.debug("the run tasks are not there yet", exc_info=True)
            return []
        out = []
        for row in rows:
            row = dict(row)
            row["states"] = _loads(row.get("states"), [])
            row["places"] = _loads(row.get("places"), {})
            row["minutes"] = float(row.get("minutes") or 0.0)
            out.append(row)
        return out

    def since(self, when: datetime) -> List[Dict[str, Any]]:
        """The tasks whose work ended in or after `when`, oldest first."""
        return self._read("SELECT * FROM state_run_tasks WHERE scope = ? AND "
                          "COALESCE(ends_at, written_at) >= ? "
                          "ORDER BY COALESCE(ends_at, written_at)",
                          (self.scope, iso(when)))

    def window(self, hours: float = WINDOW_HOURS) -> List[Dict[str, Any]]:
        """THE WINDOW, oldest first: the latest tasks until `hours` of work
        and leisure, and the routine ones among them."""
        latest = self._read("SELECT * FROM state_run_tasks WHERE scope = ? "
                            "ORDER BY COALESCE(ends_at, written_at) DESC LIMIT 5000",
                            (self.scope,))
        total, start = 0.0, ""
        for task in latest:
            if total >= hours * 60:
                break
            start = str(task.get("ends_at") or task.get("written_at") or "")
            if task.get("kind") != "routine":
                total += task["minutes"]
        return [task for task in reversed(latest)
                if str(task.get("ends_at") or task.get("written_at") or "") >= start]

    def move_project(self, project_from: str, project_to: str) -> None:
        """Every task of a project carried on, the ones before the window
        too, into the project that carries it on."""
        try:
            self.db.execute("UPDATE state_run_tasks SET project_id = ? "
                            "WHERE project_id = ? AND scope = ?",
                            (project_to, project_from, self.scope))
        except Exception:  # pragma: no cover
            log.debug("could not move the tasks of %s", project_from, exc_info=True)

    def of_project(self, project_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        return self._read("SELECT * FROM state_run_tasks WHERE scope = ? AND "
                          "project_id = ? ORDER BY COALESCE(ends_at, written_at) DESC "
                          "LIMIT ?", (self.scope, project_id, int(limit)))

    def latest(self, limit: int = 30) -> List[Dict[str, Any]]:
        return self._read("SELECT * FROM state_run_tasks WHERE scope = ? "
                          "ORDER BY COALESCE(ends_at, written_at) DESC LIMIT ?",
                          (self.scope, int(limit)))

    def count(self) -> int:
        if self.db is None:
            return 0
        try:
            rows = self.db.query("SELECT COUNT(*) AS n FROM state_run_tasks "
                                 "WHERE scope = ?", (self.scope,))
            return int(dict(rows[0]).get("n") or 0) if rows else 0
        except Exception:
            return 0

    def held_states(self, state_ids: Sequence[str]) -> set:
        """Which of these states a task already holds."""
        wanted = {str(one) for one in state_ids}
        if not wanted:
            return set()
        held = set()
        for row in self._read("SELECT states FROM state_run_tasks WHERE scope = ?",
                              (self.scope,)):
            held.update(str(one) for one in row["states"] if str(one) in wanted)
        return held


# -- one stretch, written ------------------------------------------------------------

def stretch_text(states: Sequence[Any], minutes: Dict[str, float],
                 short: Dict[str, str]) -> str:
    """The states as the writer reads them: one line each."""
    lines = []
    for state in states:
        metadata = getattr(state, "metadata", None) or {}
        sid = str(getattr(state, "id", ""))
        began, ended = moment(getattr(state, "starts_at", None)), moment(
            getattr(state, "ends_at", None))
        when = (f"{began.astimezone():%m-%d %H:%M}-{(ended or began).astimezone():%H:%M}"
                if began else "")
        where = "; ".join(one[:80] for one in state_places(state)[:3])
        lines.append("|".join([
            short[sid], when, f"{minutes.get(sid, 0):.0f}m",
            str(metadata.get("kind") or "?"),
            str(getattr(state, "title", "") or "")[:160],
            str(getattr(state, "detail", "") or "")[:200], where]))
    return ("STATES (id|when|minutes|kind|title|detail|places):\n" + "\n".join(lines))


def project_list_text(rows: Sequence[Dict[str, Any]], needing: Dict[str, str],
                      short_of: Dict[str, str]) -> str:
    if not rows:
        return "THE USER'S PROJECTS: none yet."
    lines = ["THE USER'S PROJECTS (a task in the latest 48 hours of work), newest first - "
             "id | what it is | what was last done | last active"]
    for row in rows:
        label = short_of[row["id"]]
        what = ("(you write it in step 1)" if row["id"] in needing
                else row.get("line") or row.get("title") or "?")
        lines.append(f"{label} | {what} | {row.get('title', '')}: "
                     f"{str(row.get('summary', ''))[:140]} | last active "
                     f"{row.get('last_active', '')}")
    return "\n".join(lines)


def needing_text(needing: Dict[str, str], tasks_of: Dict[str, List[Dict[str, Any]]],
                 lines: Dict[str, str], short_of: Dict[str, str]) -> str:
    if not needing:
        return ""
    out = ["PROJECTS NEEDING A NAME (with their tasks, newest first):"]
    for project_id, why in needing.items():
        out.append(f"== {short_of.get(project_id, project_id)} ({why})")
        before = lines.get(project_id)
        if before:
            out.append(f"   named before: {before}")
        for task in sorted(tasks_of.get(project_id, []),
                           key=lambda one: str(one.get("ends_at") or ""),
                           reverse=True)[:8]:
            out.append(f"   - {task.get('title', '')} - {str(task.get('summary', ''))[:200]}")
    return "\n".join(out)


def read_tasks(answer: Dict[str, Any], states: Sequence[Any],
               short: Dict[str, str], project_of_label: Dict[str, str]
               ) -> List[Dict[str, Any]]:
    """The writer's tasks, held to the rules: every state of the stretch in
    exactly one task - a state named twice stays in the first, one not
    named goes into a task of its own - and "continues" only a listed
    project."""
    by_short = {label: sid for sid, label in short.items()}
    known = {str(getattr(state, "id", "")) for state in states}
    placed: set = set()
    out: List[Dict[str, Any]] = []
    for task in answer.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        ids = []
        for label in task.get("states") or []:
            sid = by_short.get(str(label).strip())
            if sid and sid in known and sid not in placed:
                ids.append(sid)
                placed.add(sid)
        if not ids:
            continue
        kind = str(task.get("kind") or "work").strip().lower()
        said = str(task.get("continues") or "").strip()
        out.append({
            "title": str(task.get("title") or "").strip()[:200],
            "summary": str(task.get("summary") or "").strip()[:800],
            "title_display": str(task.get("title_display") or "").strip()[:200],
            "summary_display": str(task.get("summary_display") or "").strip()[:800],
            "kind": kind if kind in KINDS else "work",
            "continues": project_of_label.get(said, ""),
            "states": ids})
    left = [str(getattr(state, "id", "")) for state in states
            if str(getattr(state, "id", "")) not in placed]
    for sid in left:
        state = next(one for one in states if str(getattr(one, "id", "")) == sid)
        metadata = getattr(state, "metadata", None) or {}
        kind = str(metadata.get("kind") or "work").lower()
        title = str(getattr(state, "title", "") or "Other activity")[:200]
        out.append({"title": title, "summary": str(getattr(state, "detail", "") or "")[:800],
                    "title_display": "", "summary_display": "",
                    "kind": kind if kind in KINDS else "work", "continues": "",
                    "states": [sid], "unnamed": True})
    return out


def relink_text(new: Sequence[Dict[str, Any]], earlier: Sequence[Dict[str, Any]]) -> str:
    """The new projects and their earlier look-alikes, each with its tasks.
    `new`: id, label, title, line, tasks, kept (it has an earlier self),
    candidates (labels, most alike first); `earlier`: id, label, title,
    line, before (earlier names), last_active, tasks."""
    def tasks_of(one: Dict[str, Any]) -> List[str]:
        return [f"   - {task.get('title', '')} - {str(task.get('summary', ''))[:160]}"
                for task in one.get("tasks") or []]

    out = ["NEW PROJECTS:"]
    for one in new:
        out.append(f"== {one['label']}{' (already has an earlier self)' if one.get('kept') else ''}"
                   f" | {one.get('title', '')} | {one.get('line', '')}")
        out.extend(tasks_of(one))
        out.append(f"   look-alikes: {', '.join(one.get('candidates') or [])}")
    out.append("\nEARLIER PROJECTS:")
    for one in earlier:
        before = f" | earlier called: {'; '.join(one['before'])}" if one.get("before") else ""
        out.append(f"== {one['label']} | {one.get('title', '')} | {one.get('line', '')}{before}"
                   f" | last active {str(one.get('last_active') or '')[:10]}")
        out.extend(tasks_of(one))
    return "\n".join(out)


def read_links(answer: Dict[str, Any], new_of_label: Dict[str, str],
               earlier_of_label: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    """The model's links, held to the earlier projects it was shown."""
    out: Dict[str, Dict[str, Any]] = {}
    for label, said in (answer.get("links") or {}).items():
        new_id = new_of_label.get(str(label).strip())
        if not new_id or not isinstance(said, dict):
            continue
        same = earlier_of_label.get(str(said.get("same_as") or "").strip(), "")
        continues = [earlier_of_label[str(one).strip()] for one in said.get("continues") or []
                     if str(one).strip() in earlier_of_label]
        out[new_id] = {"same_as": same,
                       "continues": [one for one in dict.fromkeys(continues) if one != same],
                       "why": str(said.get("why") or "").strip()[:300]}
    return out


def read_lines(answer: Dict[str, Any], project_of_label: Dict[str, str]
               ) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for label, said in (answer.get("lines") or {}).items():
        project_id = project_of_label.get(str(label).strip())
        if not project_id:
            continue
        if isinstance(said, str):
            said = {"line": said}
        if not isinstance(said, dict) or not str(said.get("line") or "").strip():
            continue
        out[project_id] = {"line": str(said.get("line")).strip()[:300],
                           "line_display": str(said.get("line_display") or "").strip()[:300],
                           "title": str(said.get("title") or "").strip()[:200],
                           "title_display": str(said.get("title_display") or "").strip()[:200]}
    return out


# -- grouping tasks into projects ------------------------------------------------------

def louvain(layers: Sequence[Any], gammas: Sequence[float],
            weights: Sequence[float], seed: int = 0) -> List[List[int]]:
    """One shared partition maximising sum_l w_l * Q_l(gamma_l) over the
    layers - modularity per layer, Louvain moves, then the groups made
    nodes, until nothing moves. Written here because the usual libraries
    are GPL and the application is MIT."""
    import numpy as np

    n = layers[0].shape[0]
    if n == 0:
        return []
    rng = np.random.default_rng(seed)
    members = [[i] for i in range(n)]
    mats = [np.array(A, dtype=float) for A in layers]
    totals = [A.sum() for A in mats]
    while True:
        m = mats[0].shape[0]
        comm = np.arange(m)
        deg = [A.sum(axis=1) for A in mats]
        linked = np.zeros((m, m), dtype=bool)
        for A in mats:
            linked |= A > 0
        np.fill_diagonal(linked, False)

        def gain(i: int, c: int) -> float:
            inside = comm == c
            said = 0.0
            for A, k, two_m, g, w in zip(mats, deg, totals, gammas, weights):
                if two_m > 0:
                    said += w * (A[i, inside].sum()
                                 - g * k[i] * k[inside].sum() / two_m) / two_m
            return said

        moved_any, improved = False, True
        while improved:
            improved = False
            for i in rng.permutation(m):
                was = comm[i]
                comm[i] = -1
                best, best_gain = comm.max() + 1, 0.0
                for c in set(comm[linked[i]]) | {was}:
                    if c < 0:
                        continue
                    g = gain(i, c)
                    if g > best_gain + 1e-12 or (c == was and abs(g - best_gain) <= 1e-12):
                        best, best_gain = c, g
                comm[i] = best
                if best != was:
                    improved = moved_any = True
        renumber = {c: k for k, c in enumerate(sorted(set(comm)))}
        comm = np.array([renumber[c] for c in comm])
        if not moved_any or len(renumber) == m:
            groups: Dict[int, List[int]] = defaultdict(list)
            for node, c in enumerate(comm):
                groups[c] += members[node]
            return list(groups.values())
        new_members: List[List[int]] = [[] for _ in renumber]
        for node, c in enumerate(comm):
            new_members[c] += members[node]
        P = np.zeros((m, len(renumber)))
        P[np.arange(m), comm] = 1.0
        mats = [P.T @ A @ P for A in mats]
        members = new_members


def group(tasks: Sequence[Dict[str, Any]], vectors: Dict[str, List[float]],
          project_of: Dict[str, str]) -> List[List[str]]:
    """The window's tasks in projects: work and leisure apart, routine in
    none. Meaning and the writer's "continues" are two layers of one
    partition; where a writer said a task continues a project, it is
    linked to that project's other tasks."""
    import numpy as np

    out: List[List[str]] = []
    for kind in ("work", "leisure"):
        mine = [task for task in tasks if task.get("kind") == kind
                and task["id"] in vectors]
        if not mine:
            continue
        V = np.array([vectors[task["id"]] for task in mine], dtype=float)
        norms = np.linalg.norm(V, axis=1, keepdims=True)
        V = V / np.where(norms == 0, 1.0, norms)
        sims = V @ V.T
        M = np.where(sims >= MEANING_AT, sims, 0.0)
        np.fill_diagonal(M, 0.0)
        H = np.zeros_like(M)
        for a, task in enumerate(mine):
            said = task.get("continues") or ""
            if not said:
                continue
            for b, other in enumerate(mine):
                if b != a and (project_of.get(other["id"]) == said
                               or other.get("continues") == said):
                    H[a, b] = H[b, a] = 1.0
        layers, gammas, weights = [M], [RESOLUTION], [1.0]
        if H.sum() > 0:
            layers.append(H)
            gammas.append(RESOLUTION)
            weights.append(HINT_WEIGHT)
        for part in louvain(layers, gammas, weights):
            out.append([mine[k]["id"] for k in part])
    return out


def inherit(groups: Sequence[Sequence[str]], previous: Dict[str, Sequence[str]],
            minutes: Dict[str, float], make_id: Callable[[], str]
            ) -> Tuple[Dict[str, List[str]], Dict[str, Dict[str, Any]], Dict[str, str]]:
    """Which project each group is, by WORKED MINUTES, over the tasks of
    the window only (user, 2026-10-19):

    - an earlier project that gave CARRIES_AT of its minutes in the window
      to one group is CARRIED ON by it - its history is the group's on the
      Overview, and what was learned from it goes with it;
    - the group keeps the id of the one it carries on that is CARRIES_AT
      of its earlier-grouped minutes (its new tasks are not counted):
      "continued". Otherwise it is new - "carried" when it carries others
      on, "new" when it carries none;
    - an earlier project carried on by none ends: its history stays its
      own, and so does what was learned from it. A split is just that.

    Returns (project id -> its tasks, project id -> what happened, each
    ended project -> the project that carries it on, "" if none).
    """
    old_of = {task: pid for pid, tasks in previous.items() for task in tasks}
    share = lambda part, whole: part / whole if whole > 0 else 0.0  # noqa: E731
    old_minutes: Dict[str, float] = defaultdict(float)
    for task, pid in old_of.items():
        old_minutes[pid] += minutes.get(task, 0.0) or 0.0
    went: Dict[str, Dict[int, float]] = defaultdict(lambda: defaultdict(float))
    came: Dict[int, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    known: Dict[int, float] = defaultdict(float)
    for g, tasks in enumerate(groups):
        for task in tasks:
            pid = old_of.get(task)
            if pid:
                m = minutes.get(task, 0.0) or 0.0
                went[pid][g] += m
                came[g][pid] += m
                known[g] += m
    carried_by: Dict[str, int] = {}
    for pid, where in went.items():
        g, m = max(where.items(), key=lambda pair: (pair[1], -pair[0]))
        if share(m, old_minutes[pid]) >= CARRIES_AT:
            carried_by[pid] = g
    out: Dict[str, List[str]] = {}
    happened: Dict[str, Dict[str, Any]] = {}
    group_pid: Dict[int, str] = {}
    for g, tasks in enumerate(groups):
        carried = sorted((pid for pid, h in carried_by.items() if h == g),
                         key=lambda pid: (-came[g][pid], pid))
        same = next((pid for pid in carried
                     if share(came[g][pid], known[g]) >= CARRIES_AT), None)
        pid = same or make_id()
        happened[pid] = {"kind": "continued" if same else ("carried" if carried else "new"),
                         "from": carried}
        out[pid] = list(tasks)
        group_pid[g] = pid
    ended = {pid: (group_pid[carried_by[pid]] if pid in carried_by else "")
             for pid in previous if pid not in out}
    return out, happened, ended


def typical(tasks: Sequence[Dict[str, Any]], vectors: Dict[str, List[float]]
            ) -> Optional[Dict[str, Any]]:
    """The project's most typical task of its latest run: the one nearest
    its centre (more minutes breaks a tie); when even that one is far from
    the centre, the most typical of the hours before it."""
    import numpy as np

    have = [task for task in tasks if task["id"] in vectors]
    if not have:
        return max(tasks, key=lambda one: str(one.get("ends_at") or ""), default=None)
    V = np.array([vectors[task["id"]] for task in have], dtype=float)
    centre = V.mean(axis=0)
    norm = np.linalg.norm(centre)
    centre = centre / (norm if norm else 1.0)

    def near(task: Dict[str, Any]) -> float:
        v = np.array(vectors[task["id"]], dtype=float)
        n = np.linalg.norm(v)
        return float((v / (n if n else 1.0)) @ centre)

    latest_run = max(have, key=lambda one: str(one.get("ends_at") or ""))["run_id"]
    last = [task for task in have if task.get("run_id") == latest_run] or have
    best = max(last, key=lambda one: (near(one), one.get("minutes") or 0.0))
    if near(best) >= 0.5:
        return best
    end = moment(best.get("ends_at")) or now()
    recent = [task for task in have
              if (moment(task.get("ends_at")) or end) >= end - timedelta(hours=TITLE_FALLBACK_HOURS)]
    return max(recent or have, key=near)


def short_labels(ids: Sequence[str], prefix: str) -> Dict[str, str]:
    return {one: f"{prefix}{n}" for n, one in enumerate(ids, 1)}


def new_project_id() -> str:
    return "p-" + uuid.uuid4().hex[:12]


def new_task_id() -> str:
    return "t-" + uuid.uuid4().hex[:12]


def _loads(text: Any, fallback: Any) -> Any:
    if isinstance(text, (list, dict)):
        return text
    try:
        found = json.loads(text) if text else fallback
    except (TypeError, ValueError):
        return fallback
    return found if isinstance(found, type(fallback)) else fallback


__all__ = ["SCHEME", "RunTasks", "WRITER_SYSTEM", "LINES_SYSTEM", "group", "inherit",
           "typical", "worked_minutes", "read_tasks", "read_lines", "louvain",
           "state_places", "directories", "WINDOW_DAYS", "WINDOW_HOURS", "SLICE_HOURS",
           "RELINK_SYSTEM", "relink_text", "read_links"]
