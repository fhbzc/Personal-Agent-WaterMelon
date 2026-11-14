"""WHAT THE SCREEN SHOWED, A SOURCE OF THE LEARNING - and WHAT THE EARLIER LEARNING FOUND (the
user, 2026-11-07: "新的学习加一个来源，'屏幕活动的状态'，然后用新的方法把所有历史重新学一遍 ...
另外对之前的用户也是这样，会自动检索他们有没有旧版经验没学过，有的话重新学").

THE UNITS are the user's pieces of work as the state heartbeat grouped them:

  - a PROJECT of theirs (a scheme row of `state_tasks`): a Work project still going is the unit
    the learning already reads it as (`work:...`, its folders and conversations too) - its screen
    is read into the same unit, beside the rest of it; any other - ended, or for leisure - is a
    unit of its own (`activity:...`);
  - a TASK OF BEFORE THE PROJECTS (the older grouping of states, before 2026-10-19), a unit of
    its own;
  - WHAT NO GROUPING TOOK (`activity:loose`), once the project round has passed it by a day.

THEIR MATERIAL is the states - when, what the user was doing, its detail, and why the agent
watching the screen thinks so (what it saw) - with the task they were grouped into: its title,
summary, minutes and places. Read in the same flow as every other unit (`reading.py`): into
cases and judgments, then the reasons.

ONLY THE USER'S: never a state about what the agent itself meant to do, never one retired as not
so, never routine (meals, rest), never a task the user put away. Never what lies in a place the
user left out or deleted from the learning, and never what names a window or site the listener
is told never to read (the password managers, banking, private windows; the denylists): such a
state is not shown at all. The Experience debugger's scopes are not the user's: only `live`.

READ ONCE: each state read is kept in `learn_from_activity`, with the unit it was read in -
never read twice, whatever project it is regrouped into later; what comes after is read at the
learning's next updates. A part whose answer was cut off even at its smallest, or could not be
read, is recorded failed there and moved past (models/answers.py).

THE EARLIER LEARNING'S PATTERNS (`experience_patterns`, written until 2026-10-19 from the same
screen): the distilled old lessons, each read once (`learn_from_earlier`) in one unit,
`earlier:experience`, into the kind of work it is about (a case) and what the user does in it
(judgments, of the strength "earlier": found by an agent watching, from several of their pieces
of work, never their own words) - with how many it was seen in. The reasons come of them as of
everything else: one they bear out is borne out, those no reason explains are explained
together. Its principles are shown with each pattern under them. The old stages and cards are
not read: they were written from the very states read here.

ALL HISTORY, AT THE LEARNING'S OWN PACE: what is not learned yet is planned into every update
(the same way any unit is), at most `PER_UPDATE` of it an update - the earlier patterns first,
then the most recent work - within the day's limits. For a user from before, the first look (at
the start, `catch_up`) finds what there is; nothing for a new one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

KIND = "activity"
EARLIER_KIND = "earlier"
EARLIER_UNIT = "earlier:experience"
LOOSE = "loose"
#: Of the screen's activity and the earlier patterns, at most this much an update (characters):
#: ten readings of `reading.CHUNK_CHARS` - the history taken over several updates, not at once.
#: Half an update's reading (learning_reads_per_update, 100 pieces of 50,000) at the most: the
#: history of the screen caught up in some two days, the rest left to conversations and work
#: (the user, 2026-11-08).
PER_UPDATE = 2_500_000
#: A piece shown at most this long: a long task is shown in parts.
PIECE_CHARS = 20_000
#: A state no grouping held this long after the project round's latest run is read by itself.
LOOSE_AFTER = timedelta(hours=24)
#: Said once, when the history was first looked for, and when it was all learned.
CAUGHT_UP = "learning_history_2026_11_07"
#: Never read: windows and sites the listener never reads (common/config/schema.py).
NEVER_READ_KEYS = ("listener_screen_excluded_applications", "listener_application_denylist")
SITES_KEY = "listener_website_denylist"

SCHEMA = """
CREATE TABLE IF NOT EXISTS learn_from_activity (
    state_id TEXT PRIMARY KEY,
    unit_id  TEXT NOT NULL DEFAULT '',
    failed   TEXT NOT NULL DEFAULT '',
    at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS learn_from_earlier (
    pattern_id TEXT PRIMARY KEY,
    version    TEXT NOT NULL DEFAULT '',
    failed     TEXT NOT NULL DEFAULT '',
    at         TEXT NOT NULL
);
"""

#: Shown with a chunk of the screen's activity (reading.py).
ACTIVITY_NOTE = """WHAT THE SCREEN SHOWED - THE STATES - is what the agent watching the user's
screen wrote they were doing, each with why it thinks so (what it saw), grouped into tasks. It is
the agent's description of what THE USER did, not the user's words: a judgment from it is what
they did - "changed" where they changed, rewrote or turned down something themselves, "approved"
where they sent, submitted or published it as it was, else "accepted"; never "written" nor
"noted". "said" only when the record quotes the user typing their own call about the work - never
a page's words or another person's. A case is the deliverable the time went into (the paper, the
app, the reply); time spent only reading or watching is a case only when it shows what they
choose and how - leave out what shows nothing of their judgment."""

#: A continued case, only what changed (the user, 2026-11-11; reading.READ_PROMPT).
EARLIER_PROMPT = """WHAT AN EARLIER LEARNING FOUND OF HOW THE USER WORKS: WRITE IT AS CASES AND
JUDGMENTS.

Until 2026-10-19 an earlier learning of this application watched the user's screen and wrote
PATTERNS of how they work - each from several pieces of their work (how many is said), under a
PRINCIPLE. They are the agent's findings, not the user's words. For each pattern write:

CASES - the kind of deliverable it is about (emails to a kind of person, a paper's figures, an
app's interface): a new one gets a ref "new1", "new2"... and is written in full - title (a few
words naming the kind of thing), facets (term ids of the WORD LIST: deliverable, audience,
presentation, purpose, environment - only what the pattern shows), looks_like (what such a
deliverable of theirs looks like, by the pattern: 1-3 sentences). An existing case (listed with
its id) is continued, never written again: its id as "ref" and ONLY what this pattern changes of
it (new "items", a facet newly shown, the "looks_like" if it is different) - what is left out is
kept as it was; one nothing changes in is not listed, its judgments name it by its id.

JUDGMENTS - what the pattern says THE USER decides in it, one decision each:
  - pattern: its id; case: the case's id or ref; item: which part, if one;
  - aspect: {aspects}
  - object: the term id from the word list for WHAT in the output it is about;
  - direction: what the user wants of it - short and concrete, as the pattern says;
  - why: the pattern's own reason for it, if it gives one;
  - at: the date it was last seen, YYYY-MM-DD.
Write a judgment only for what the pattern says the user does or wants; a pattern about what the
agent did, or too vague to say what to do differently, gives none.

THE WORD LIST - choose from it. Only when no term fits, add one in "new_terms": under the most
fitting existing term (its id as "parent"), with a one-line definition; then use its id
"<dimension>:<term>".

Return ONLY this JSON object:
{"cases": [{"ref": "new1", "title": "", "facets": {}, "looks_like": "", "items": []},
           {"ref": "C-...", "items": ["only the new ones"]}],
 "judgments": [{"pattern": "", "case": "", "item": "", "aspect": "", "object": "object:...",
                "direction": "", "why": "", "at": ""}],
 "new_terms": [{"dim": "", "term": "", "parent": "", "definition": ""}]}"""


def ready(db: Any) -> None:
    with db.write() as conn:
        conn.executescript(SCHEMA)


def unit_id_of(key: str) -> str:
    """A unit of the screen's activity that is not a Work project's own."""
    return f"{KIND}:" + hashlib.sha1(str(key).lower().encode("utf-8")).hexdigest()[:12]


def work_unit_id(project_id: str) -> str:
    """A Work project's unit, as the learning names it (service._unit_id)."""
    return "work:" + hashlib.sha1(str(project_id).lower().encode("utf-8")).hexdigest()[:12]


def _loads(text: Any, default: Any) -> Any:
    if isinstance(text, type(default)):
        return text
    try:
        value = json.loads(text) if text else default
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def _when(value: Any) -> str:
    return str(value or "")[:16].replace("T", " ")


def _short(text: Any, most: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= most else text[:most - 3] + "..."


def _moment(text: str) -> Optional[datetime]:
    try:
        found = datetime.fromisoformat(str(text or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return found if found.tzinfo else found.replace(tzinfo=timezone.utc)


class Activity:
    """The screen's activity and the earlier patterns, as units of the learning: who owns each
    state, what each unit has not had read, and what of it is never shown."""

    def __init__(self, db: Any, config: Any = None, left_out: Iterable[Any] = (),
                 work_projects: Optional[Sequence[str]] = None) -> None:
        from .leave_out import LeaveOut
        self.db = db
        self.config = config
        ready(db)
        left = [str(one) for one in left_out if str(one or "").strip()]
        self.leave_out = LeaveOut([one for one in left if not one.lower().startswith("http")])
        self.addresses = [one.lower() for one in left if one.lower().startswith("http")]
        self._work = None if work_projects is None else set(work_projects)
        self._owners: Optional[Dict[str, str]] = None
        self._units: Dict[str, Dict[str, Any]] = {}
        self._never = self._never_read()

    # -- what is never shown ----------------------------------------------------------------
    def _lines(self, key: str) -> Optional[List[str]]:
        if self.config is None:
            return None
        try:
            if hasattr(self.config, "lines"):
                return [one.lower() for one in self.config.lines(key)]
            value = self.config.get(key, None)
        except Exception:                       # pragma: no cover - a config without it
            return None
        if value is None:
            return None
        return [one.strip().lower() for one in str(value).splitlines() if one.strip()]

    def _never_read(self) -> "re.Pattern[str]":
        """The windows and sites the listener never reads, as words: a state that names one is
        not shown. With no setting, the application's own list."""
        from ..common.config.schema import DEFAULT_UNREADABLE_WINDOWS
        names: List[str] = []
        for key in NEVER_READ_KEYS:
            found = self._lines(key)
            names += list(DEFAULT_UNREADABLE_WINDOWS) if found is None and \
                key == NEVER_READ_KEYS[0] else (found or [])
        names += self._lines(SITES_KEY) or []
        names += self.addresses
        parts = [r"(?<![\w])" + re.escape(one) + r"(?![\w])" for one in dict.fromkeys(names) if one]
        return re.compile("|".join(parts) or r"(?!x)x", re.IGNORECASE)

    def shown(self, text: str) -> bool:
        """Whether a state may be shown: it names no window or site never read, and does not
        rest on a place the user left out."""
        if self._never.search(text):
            return False
        return not self.leave_out.record_out([], text)

    def tidy(self, text: str) -> str:
        return self.leave_out.redact(text) if self.leave_out else text

    # -- who owns each state ----------------------------------------------------------------
    def work_projects(self) -> set:
        if self._work is None:
            from .places import Places
            self._work = {one["id"] for one in Places.work_projects(self.db)}
        return self._work

    def _projects(self) -> Dict[str, Dict[str, Any]]:
        """The user's projects (scheme rows, `live` only), and the older grouping's tasks."""
        out: Dict[str, Dict[str, Any]] = {}
        try:
            # The user's own: never a sandbox's, nor the debugger's ("dbg_...").
            rows = self.db.query("SELECT id, members, profile, described, retired_at FROM "
                                 "state_tasks WHERE scope = 'live' AND id NOT LIKE 'dbg_%'")
        except Exception:
            return out
        for row in rows:
            profile = _loads(row["profile"], {})
            described = _loads(row["described"], {})
            scheme = str(profile.get("scheme") or "") == "projects"
            title = str(described.get("title") or described.get("summary")
                        or described.get("doing") or "")[:200]
            about = str(described.get("what_it_is") or described.get("line")
                        or described.get("summary") or described.get("goal") or "")[:400]
            out[row["id"]] = {"id": row["id"], "scheme": scheme, "title": title, "about": about,
                              "kind": str(described.get("kind") or "work"),
                              "members": _loads(row["members"], []) if not scheme else []}
        return out

    def owners(self) -> Dict[str, str]:
        """Each state not read yet - the user's, not routine, not retired - and the unit it is
        read in. Worked out once."""
        if self._owners is not None:
            return self._owners
        projects = self._projects()
        work = self.work_projects()
        rows = self.db.query(
            "SELECT s.id, s.starts_at, s.ends_at FROM states s WHERE s.about != 'agent' "
            "AND s.status != 'retired' AND COALESCE(CASE WHEN json_valid(s.metadata) THEN "
            "json_extract(s.metadata, '$.kind') END, '') != 'routine' "
            "AND s.id NOT IN (SELECT state_id FROM learn_from_activity)")
        wanted = {row["id"]: str(row["starts_at"] or row["ends_at"] or "") for row in rows}
        owners: Dict[str, str] = {}
        held: set = set()
        latest_run = ""
        try:
            tasks = self.db.query("SELECT states, project_id, kind, put_away, written_at FROM "
                                  "state_run_tasks WHERE scope = 'live' AND project_id NOT LIKE "
                                  "'dbg_%'")
        except Exception:
            tasks = []
        for task in tasks:
            ids = [str(one) for one in _loads(task["states"], [])]
            held.update(ids)
            latest_run = max(latest_run, str(task["written_at"] or ""))
            if task["put_away"] or task["kind"] == "routine":
                continue                        # put away by the user, or no piece of work
            project = projects.get(str(task["project_id"] or ""))
            if project is None or not project["scheme"]:
                unit = unit_id_of(LOOSE)
            elif project["id"] in work:
                unit = work_unit_id(project["id"])
            else:
                unit = unit_id_of(project["id"])
            self._remember(unit, project, project_id=str(task["project_id"] or ""))
            for one in ids:
                if one in wanted:
                    owners.setdefault(one, unit)
        for project in projects.values():
            if project["scheme"]:
                continue
            unit = unit_id_of("before:" + project["id"])
            for one in project["members"]:
                one = str(one)
                if one in wanted and one not in held and one not in owners:
                    owners[one] = unit
                    self._remember(unit, project, before=True)
        # What no grouping took, once the project round has passed it by.
        edge = (_moment(latest_run) or datetime.now(timezone.utc)) - LOOSE_AFTER
        loose = unit_id_of(LOOSE)
        for one, at in wanted.items():
            if one in owners or one in held:
                continue
            moment = _moment(at)
            if moment is not None and moment < edge:
                owners[one] = loose
        if any(unit == loose for unit in owners.values()):
            self._remember(loose, None)
        self._owners = owners
        return owners

    def _remember(self, unit_id: str, project: Optional[Dict[str, Any]], before: bool = False,
                  project_id: str = "") -> None:
        if unit_id in self._units:
            return
        if unit_id.startswith("work:"):
            self._units[unit_id] = {"id": unit_id, "kind": "work", "key": project_id}
        elif project is None or unit_id == unit_id_of(LOOSE):
            self._units[unit_id] = {
                "id": unit_id, "kind": KIND, "key": LOOSE,
                "title": "Screen activity in no project",
                "summary": "What your screen showed you doing that was grouped into no project."}
        else:
            title = project["title"] or ("earlier work" if before else "a project")
            self._units[unit_id] = {
                "id": unit_id, "kind": KIND,
                "key": ("before:" if before else "") + project["id"],
                "title": title,
                "summary": ("What your screen showed of it, grouped before the projects"
                            if before else f"What your screen showed of this {project['kind']}"
                            " project") + (f": {project['about']}" if project["about"] else "")}

    def units(self) -> List[Dict[str, Any]]:
        """The units with something of the screen not read yet - not the Work projects' own,
        which the learning lists itself."""
        owned = set(self.owners().values())
        return [dict(one) for unit_id, one in self._units.items()
                if unit_id in owned and one["kind"] == KIND]

    # -- what a unit has to read --------------------------------------------------------------
    def _states(self, ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        ids = list(ids)
        for start in range(0, len(ids), 400):
            part = ids[start:start + 400]
            for row in self.db.query(
                    "SELECT id, title, detail, source, starts_at, ends_at, reason FROM states "
                    f"WHERE id IN ({', '.join('?' for _ in part)})", tuple(part)):
                out[row["id"]] = dict(row)
        return out

    def _line(self, state: Dict[str, Any]) -> str:
        said = " (the user said so)" if state.get("source") == "user" else ""
        why = f" - seen: {_short(state.get('reason'), 900)}" if state.get("reason") else ""
        return (f"  [{_when(state.get('starts_at'))} to {_when(state.get('ends_at'))}] "
                f"{_short(state.get('title'), 240)}{said}: {_short(state.get('detail'), 1500)}{why}")

    def pieces(self, unit: Dict[str, Any], cutoff: str) -> List[Tuple[str, str, List[str]]]:
        """What of the screen a unit has not had read, up to the cutoff: (when, text, the states
        in it), oldest first. Nothing of what is never shown."""
        mine = [one for one, unit_id in self.owners().items() if unit_id == unit["id"]]
        if not mine:
            return []
        states = self._states(mine)
        out: List[Tuple[str, str, List[str]]] = []
        taken: set = set()
        if unit.get("kind") == "work" or not str(unit.get("key") or "").startswith(
                ("before:", LOOSE)):
            out += self._task_pieces(unit, states, cutoff, taken)
        rest = [states[one] for one in mine if one in states and one not in taken
                and str(states[one].get("starts_at") or "") <= cutoff]
        by_day: Dict[str, List[Dict[str, Any]]] = {}
        for state in sorted(rest, key=lambda one: str(one.get("starts_at") or "")):
            by_day.setdefault(str(state.get("starts_at") or "")[:10], []).append(state)
        for day, day_states in by_day.items():
            out += self._piece(f"WHAT THE SCREEN SHOWED ON {day or 'a day not known'}:",
                               day_states)
        return sorted(out, key=lambda one: one[0])

    def _task_pieces(self, unit: Dict[str, Any], states: Dict[str, Dict[str, Any]],
                     cutoff: str, taken: set) -> List[Tuple[str, str, List[str]]]:
        out: List[Tuple[str, str, List[str]]] = []
        rows = self.db.query(
            "SELECT * FROM state_run_tasks WHERE scope = 'live' AND put_away = '' AND "
            "project_id = ? AND kind != 'routine' AND written_at <= ? "
            "ORDER BY COALESCE(starts_at, written_at)", (str(unit.get("key") or ""), cutoff))
        for task in rows:
            ids = [str(one) for one in _loads(task["states"], []) if str(one) in states]
            if not ids:
                continue
            taken.update(ids)
            places = [place for place in _loads(task["places"], {})
                      if not self.leave_out.touches(place)][:6]
            head = (f"A TASK [{_when(task['starts_at'])} to {_when(task['ends_at'])}, "
                    f"{round(float(task['minutes'] or 0))} min, {task['kind']}]: "
                    f"{_short(task['title'], 200)} - {_short(task['summary'], 800)}"
                    + (f"\n  where: {', '.join(places)}" if places else "")
                    + "\n  WHAT THE SCREEN SHOWED IN IT:")
            out += self._piece(head, [states[one] for one in ids])
        return out

    def _piece(self, head: str, states: Sequence[Dict[str, Any]]
               ) -> List[Tuple[str, str, List[str]]]:
        """States under a heading, in pieces of at most `PIECE_CHARS` - each with the ids it
        holds. A state never shown is in none, and not marked read: should the user take a
        place off what is left out, it is read then."""
        out: List[Tuple[str, str, List[str]]] = []
        lines: List[str] = []
        ids: List[str] = []
        first = ""
        size = len(head)
        for state in sorted(states, key=lambda one: str(one.get("starts_at") or "")):
            text = self._line(state)
            if not self.shown(text):
                continue
            text = self.tidy(text)
            if lines and size + len(text) > PIECE_CHARS:
                out.append((first, "\n".join([head, *lines]), ids))
                lines, ids, size = [], [], len(head)
            if not lines:
                first = str(state.get("starts_at") or "")
            lines.append(text)
            ids.append(state["id"])
            size += len(text) + 1
        if lines:
            out.append((first, "\n".join([head, *lines]), ids))
        return out

    def shown_left(self) -> int:
        """How many states not read yet may be shown."""
        states = self._states(list(self.owners()))
        return sum(1 for state in states.values() if self.shown(self._line(state)))

    # -- the earlier learning's patterns --------------------------------------------------------
    def earlier(self) -> List[Dict[str, Any]]:
        """The earlier patterns not read yet (the user's own scope, never a debugger's), each
        with its principle."""
        try:
            rows = [dict(one) for one in self.db.query(
                "SELECT * FROM experience_patterns WHERE scope = 'live' AND id NOT LIKE 'dbg_%' "
                "ORDER BY created_at")]
        except Exception:
            return []
        principles = {one["id"]: one for one in rows if one["layer"] == "principle"}
        read = {row["pattern_id"]: row["version"] for row in self.db.query(
            "SELECT pattern_id, version FROM learn_from_earlier")}
        out = []
        for one in rows:
            if one["layer"] != "pattern" or read.get(one["id"]) == str(one["updated_at"] or ""):
                continue
            text = f"{one['statement']} {one['detail']}"
            if not self.shown(text):
                continue
            one["principle"] = (principles.get(one.get("parent") or "") or {}).get("statement", "")
            out.append(one)
        return out

    def earlier_pieces(self) -> List[Tuple[str, str, Dict[str, Any]]]:
        out = []
        for one in self.earlier():
            seen = str(one.get("last_active") or one.get("updated_at") or "")[:10]
            text = (f"PATTERN {one['id']} (seen in {int(one.get('support') or 0)} of their pieces "
                    f"of work, last on {seen or 'a day not known'}"
                    + (f"; under the principle: {_short(one['principle'], 400)}"
                       if one.get("principle") else "") + "):\n"
                    f"  {_short(one['statement'], 600)}\n  {_short(one['detail'], 2500)}")
            out.append((str(one.get("created_at") or ""), self.tidy(text),
                        {"kind": "earlier", "pattern": one["id"],
                         "version": str(one.get("updated_at") or ""),
                         "support": int(one.get("support") or 0), "at": seen}))
        return out

    # -- marks ----------------------------------------------------------------------------------
    def read(self, unit_id: str, ids: Sequence[str], failed: str = "") -> None:
        at = datetime.now(timezone.utc).isoformat()
        with self.db.write() as conn:
            conn.executemany("INSERT OR REPLACE INTO learn_from_activity (state_id, unit_id, "
                             "failed, at) VALUES (?, ?, ?, ?)",
                             [(one, unit_id, failed[:300], at) for one in ids])
        if self._owners is not None:
            for one in ids:
                self._owners.pop(one, None)

    def earlier_read(self, pattern_id: str, version: str, failed: str = "") -> None:
        self.db.execute("INSERT OR REPLACE INTO learn_from_earlier (pattern_id, version, failed, "
                        "at) VALUES (?, ?, ?, ?)",
                        (pattern_id, version, failed[:300], datetime.now(timezone.utc).isoformat()))

    def left(self) -> Dict[str, int]:
        """What of the history is not learned yet: states, earlier patterns."""
        return {"states": self.shown_left(), "patterns": len(self.earlier())}


def allot(candidates: Sequence[Tuple[str, Sequence[Any]]], budget: Optional[int] = None
          ) -> Dict[str, int]:
    """How much of each unit's screen and earlier patterns an update reads: the earlier
    patterns first, then the units of the most recent work - in each, its latest first - until
    `budget`. A unit given anything gets at least its latest piece."""
    def latest(one: Tuple[str, Sequence[Any]]) -> str:
        return max((piece.at for piece in one[1]), default="")

    order = sorted(candidates, key=latest, reverse=True)
    order.sort(key=lambda one: one[0] != EARLIER_UNIT)
    out: Dict[str, int] = {}
    left = PER_UPDATE if budget is None else budget
    for unit_id, pieces in order:
        given = 0
        for piece in sorted(pieces, key=lambda one: one.at, reverse=True):
            size = len(piece.text)
            if left <= 0 or (given and size > left):
                break
            given += size
            left -= size
        out[unit_id] = given
    return out


def catch_up(db: Any, config: Any, activity: Optional[Activity] = None) -> Dict[str, Any]:
    """WHAT OF THE HISTORY IS NOT LEARNED (the user, 2026-11-07): looked for once, at the start;
    said once when it is first found and once when it is all learned. What there is goes into the
    next updates' plans by itself - nothing to do for a new user."""
    activity = activity or Activity(db, config)
    left = activity.left()
    said = dict(config.get(CAUGHT_UP, {}) or {}) if config is not None else {}
    if config is not None:
        if not said:
            said = {"found": left, "at": datetime.now(timezone.utc).isoformat()}
            if not any(left.values()):
                said["done"] = said["at"]
            config.set(CAUGHT_UP, said, internal=True)
            if any(left.values()):
                log.info("learning: the history not learned yet (%s) goes into the next updates",
                         left)
        elif not any(left.values()) and not said.get("done"):
            said["done"] = datetime.now(timezone.utc).isoformat()
            config.set(CAUGHT_UP, said, internal=True)
    return dict(said, left=left)


__all__ = ["KIND", "EARLIER_KIND", "EARLIER_UNIT", "PER_UPDATE", "Activity", "allot", "catch_up",
           "unit_id_of", "work_unit_id", "ready", "ACTIVITY_NOTE", "EARLIER_PROMPT"]
