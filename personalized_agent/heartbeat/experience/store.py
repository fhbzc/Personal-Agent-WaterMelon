"""Where the user's experience is kept: stage graphs, and how they work.

Two structures, both append-only in spirit.

A TREE OF CARDS per piece of work, with the attempts inside them. A card is
a piece of the deliverable - what must be true when it is done, how that is
judged, why the card above needs it. The root is the work's goal, the cards
under it the deliverables, the cards under those the parts. A stage is one
attempt inside a card, one step of the loop the work went through - a goal
set, a plan made, work done, a result evaluated, a plan adjusted, a
deliverable accepted - and stages are joined by dependency edges as well. A
stage keeps its id, a card keeps its id, and the tree grows: when a task
joins a project its root is put under the project's, and when it leaves it
is taken out again; the edges its joining made are taken away by their
reason.

PATTERNS AND PRINCIPLES over the nature records: a pattern is a group of
records about one way of working, a principle a group of patterns. They are
regrouped as records arrive, and keep their ids while they are recognisably
the same group.

The two meet at the states: a stage is built from states, and a nature
record is linked to the states it was written from.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

LIVE = "live"

#: The steps of the loop a piece of work goes through.
STAGE_KINDS = ("goal", "plan", "execution", "evaluation", "adjustment",
               "delivery")

#: What each edge says about the stage it leads from.
EDGE_KINDS = ("continues", "produces", "evaluates", "revises", "depends_on")

#: What a deliverable is - a fixed list, so that an email is never held up
#: as an example for a code change.
#: The two kinds of nature record. WORK: the user's taste and standards in
#: work, and how they like to work together - what the agent plans their
#: work by. PERSONAL: habits, rhythm, interests - for a companion, later.
NATURE_KINDS = ("work", "personal")

#: Where a card stands.
CARD_STATUSES = ("open", "working", "done", "set_aside")

#: How a card is judged: by a test that passes or fails (code that runs, a
#: build, a number that must match), by the user, or by an agent.
CHECKED_BY = ("test", "user", "agent")

#: What a record's kind may be said as: one kind, or BOTH - a record can be
#: about their taste in work and about who they are at once, and is then
#: grouped in both kinds' patterns.
NATURE_SAID = ("work", "personal", "both")

MEDIA = ("email", "message", "code", "document", "figure", "slides",
         "data", "form", "meeting", "setup", "other")

#: How long the user's description of a project they made may be.
DESCRIPTION_CHARS = 4000


def description_of(held: Optional[Dict[str, Any]]) -> str:
    """The user's own description of a project they made - never what
    reading its folders says it is for."""
    return str(((held or {}).get("summary") or {}).get("description") or "")


def model_task(scope: str, part: str = "") -> str:
    """What the experience's model calls are counted under, apart from the
    state heartbeat's: the user's own experience, or the Build now test.

    `part` tells apart what the user starts - a task learned into stages,
    and the natures those stages are written with ("stages"); natures that
    are the same merged ("merge") - from what runs by itself, the natures
    grouped into patterns beside each state round (no part)."""
    name = f"experience_{part}" if part else "experience"
    return name if scope == LIVE else f"{name}_test"


def input_of(usage: Any) -> Dict[str, int]:
    """WHAT ONE CALL READ, whole, and how it got it.

    `input_tokens` alone is only what was neither read from the provider's
    prompt cache nor written into it - on a cached provider a handful: a
    work run's transcript said "input 4 tokens" for a call that read forty
    thousand, and an input limit counted that way would never be reached.
    So the input of a call is all three together - `tokens_in` - and the two
    cached parts are kept beside it, so it can still be told how much of it
    was old (read from the cache) and how much new (written to it)."""
    plain = int(getattr(usage, "input_tokens", 0) or 0)
    cached = int(getattr(usage, "cached_input_tokens", 0) or 0)
    written = int(getattr(usage, "cache_written_tokens", 0) or 0)
    return {"tokens_in": plain + cached + written, "tokens_in_cached": cached,
            "tokens_in_cache_written": written}


def count_use(used: Dict[str, int], response: Any) -> None:
    """Add one model call's tokens to a running count: what went in - all
    of it, the cached input too (see `input_of`) - and what came out."""
    usage = getattr(response, "usage", None)
    used["calls"] = used.get("calls", 0) + 1
    read = input_of(usage)
    used["tokens_in"] = used.get("tokens_in", 0) + read["tokens_in"]
    # The breakdown only where there is one: a count of a provider with no
    # cache keeps the three numbers it always had.
    for name in ("tokens_in_cached", "tokens_in_cache_written"):
        if read[name] or name in used:
            used[name] = used.get(name, 0) + read[name]
    used["tokens_out"] = used.get("tokens_out", 0) + int(
        getattr(usage, "output_tokens", 0) or 0)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _loads(text: Any, default: Any) -> Any:
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


class ExperienceStore:
    """Reads and writes the experience tables of one scope."""

    def __init__(self, db: Any, scope: str = LIVE) -> None:
        self.db = db
        self.scope = scope

    # -- stages -----------------------------------------------------------
    @staticmethod
    def _stage(row: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(row)
        for name, default in (("deliverable", {}), ("evaluation", {}),
                              ("states", []), ("written_by", {}),
                              ("notes", {}), ("work", {})):
            out[name] = _loads(out.get(name), default)
        return out

    def add_stage(self, stage: Dict[str, Any]) -> Dict[str, Any]:
        stamp = now()
        row = {
            "id": stage.get("id") or new_id("stage"),
            "task_id": str(stage["task_id"]),
            "thread_id": str(stage.get("thread_id") or ""),
            "kind": str(stage["kind"]),
            "medium": str(stage.get("medium") or ""),
            "summary": str(stage.get("summary") or "")[:2000],
            "deliverable": json.dumps(stage.get("deliverable") or {},
                                      ensure_ascii=False),
            "evaluation": json.dumps(stage.get("evaluation") or {},
                                     ensure_ascii=False),
            "states": json.dumps(list(stage.get("states") or [])),
            "began": str(stage.get("began") or ""),
            "ended": str(stage.get("ended") or ""),
            "written_by": json.dumps(stage.get("written_by") or {}),
            "notes": json.dumps(stage.get("notes") or {}, ensure_ascii=False),
            "work": json.dumps(stage.get("work") or {}, ensure_ascii=False),
            "card_id": str(stage.get("card_id") or ""),
        }
        self.db.execute(
            "INSERT INTO experience_stages (id, scope, task_id, thread_id, "
            "kind, medium, summary, deliverable, evaluation, states, began, "
            "ended, written_by, notes, created_at, updated_at, work, card_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["id"], self.scope, row["task_id"], row["thread_id"],
             row["kind"], row["medium"], row["summary"], row["deliverable"],
             row["evaluation"], row["states"], row["began"], row["ended"],
             row["written_by"], row["notes"], stamp, stamp, row["work"],
             row["card_id"]))
        made = self.stage(row["id"]) or {}
        # What it can be found by, worked out now: it is looked up many
        # times, and indexed once.
        if made:
            from .index import StageIndex
            StageIndex(self.db, self.scope).add(
                made, self.card(made["card_id"]) if made.get("card_id") else None)
        return made

    def stage(self, stage_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.query("SELECT * FROM experience_stages WHERE id = ?",
                             (stage_id,))
        return self._stage(rows[0]) if rows else None

    def stages(self, task_ids: Optional[Iterable[str]] = None
               ) -> List[Dict[str, Any]]:
        if task_ids is None:
            rows = self.db.query(
                "SELECT * FROM experience_stages WHERE scope = ? "
                "ORDER BY began, created_at", (self.scope,))
        else:
            wanted = [str(one) for one in task_ids]
            if not wanted:
                return []
            holes = ", ".join("?" for _ in wanted)
            rows = self.db.query(
                f"SELECT * FROM experience_stages WHERE scope = ? AND "
                f"task_id IN ({holes}) ORDER BY began, created_at",
                (self.scope, *wanted))
        return [self._stage(row) for row in rows]

    def stage_count(self, task_ids: Optional[Iterable[str]] = None) -> int:
        """How many stages `stages(task_ids)` would return, counted by the
        database: the panel asks for the counts every few seconds while
        something is learned, and parsing every stage's JSON to count them
        was most of what that cost."""
        if task_ids is None:
            rows = self.db.query("SELECT COUNT(*) AS n FROM experience_stages "
                                 "WHERE scope = ?", (self.scope,))
        else:
            wanted = [str(one) for one in task_ids]
            if not wanted:
                return 0
            holes = ", ".join("?" for _ in wanted)
            rows = self.db.query(
                f"SELECT COUNT(*) AS n FROM experience_stages WHERE scope = ? "
                f"AND task_id IN ({holes})", (self.scope, *wanted))
        return int(rows[0]["n"]) if rows else 0

    def correct_stage(self, stage_id: str, fields: Dict[str, Any],
                      why: str) -> None:
        """A stage corrected in place - its id kept - with the reason noted."""
        current = self.stage(stage_id)
        if current is None:
            return
        notes = dict(current.get("notes") or {})
        notes.setdefault("corrections", []).append(
            {"at": now(), "why": why[:500], "fields": sorted(fields)})
        allowed = {"summary", "kind", "medium", "thread_id", "evaluation",
                   "deliverable", "card_id"}
        sets, values = [], []
        for name, value in fields.items():
            if name not in allowed:
                continue
            sets.append(f"{name} = ?")
            values.append(json.dumps(value, ensure_ascii=False)
                          if isinstance(value, (dict, list)) else str(value))
        sets += ["notes = ?", "updated_at = ?"]
        values += [json.dumps(notes, ensure_ascii=False), now()]
        self.db.execute(
            f"UPDATE experience_stages SET {', '.join(sets)} WHERE id = ?",
            (*values, stage_id))
        # What it says changed: what it is found by changes with it.
        corrected = self.stage(stage_id)
        if corrected is not None:
            from .index import StageIndex
            StageIndex(self.db, self.scope).add(
                corrected, self.card(corrected["card_id"])
                if corrected.get("card_id") else None)

    def move_stages(self, stage_ids: Sequence[str], task_id: str) -> None:
        """Stages follow their states into another task, ids kept."""
        for one in stage_ids:
            self.db.execute(
                "UPDATE experience_stages SET task_id = ?, updated_at = ? "
                "WHERE id = ?", (task_id, now(), one))

    # -- learning, per project ----------------------------------------------
    def learning(self, unit_id: str) -> Dict[str, Any]:
        rows = self.db.query("SELECT * FROM experience_learning WHERE unit_id = ?",
                             (unit_id,))
        if not rows:
            return {"unit_id": unit_id, "until": "", "done": 0, "learned_at": "",
                    "calls": 0, "tokens_in": 0, "tokens_out": 0, "counted_since": ""}
        return dict(rows[0])

    def learnings(self) -> Dict[str, Dict[str, Any]]:
        return {row["unit_id"]: dict(row) for row in
                self.db.query("SELECT * FROM experience_learning")}

    def save_learning(self, unit_id: str, **fields: Any) -> None:
        """Where a unit's learning stands. `spent` - {calls, tokens_in,
        tokens_out} - is ADDED to what it has cost so far."""
        held = self.learning(unit_id)
        spent = fields.pop("spent", None) or {}
        for name in ("calls", "tokens_in", "tokens_out"):
            held[name] = int(held.get(name) or 0) + int(spent.get(name) or 0)
        if spent and not held.get("counted_since"):
            held["counted_since"] = now()
        held.update({k: v for k, v in fields.items() if k in held})
        self.db.execute(
            "INSERT INTO experience_learning (unit_id, until, done, learned_at, "
            "calls, tokens_in, tokens_out, counted_since) VALUES (?, ?, ?, ?, ?, "
            "?, ?, ?) ON CONFLICT(unit_id) DO UPDATE SET until = excluded.until, "
            "done = excluded.done, learned_at = excluded.learned_at, calls = "
            "excluded.calls, tokens_in = excluded.tokens_in, tokens_out = "
            "excluded.tokens_out, counted_since = excluded.counted_since",
            (unit_id, str(held["until"]), int(bool(held["done"])),
             str(held["learned_at"]), int(held["calls"]), int(held["tokens_in"]),
             int(held["tokens_out"]), str(held["counted_since"])))

    # -- cards --------------------------------------------------------------
    @staticmethod
    def _card(row: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(row)
        for name, default in (("specification", []), ("checked_by", {}),
                              ("why", {}), ("refs", []), ("written_by", {})):
            out[name] = _loads(out.get(name), default)
        return out

    def add_card(self, card: Dict[str, Any]) -> Dict[str, Any]:
        stamp = now()
        card_id = card.get("id") or new_id("card")
        self.db.execute(
            "INSERT INTO experience_cards (id, scope, unit_id, task_id, "
            "parent_id, type, title, statement, specification, checked_by, "
            "why, refs, status, replaced_by, status_why, written_by, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?, ?, ?)",
            (card_id, self.scope, str(card.get("unit_id") or ""),
             str(card.get("task_id") or ""), str(card.get("parent_id") or ""),
             str(card.get("type") or ""), str(card.get("title") or "")[:300],
             str(card.get("statement") or "")[:1500],
             json.dumps(card.get("specification") or [], ensure_ascii=False),
             json.dumps(card.get("checked_by") or {}, ensure_ascii=False),
             json.dumps(card.get("why") or {}, ensure_ascii=False),
             json.dumps(card.get("refs") or [], ensure_ascii=False),
             str(card.get("status") or "open"), "", "",
             json.dumps(card.get("written_by") or {}), stamp, stamp))
        return self.card(card_id) or {}

    def card(self, card_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.query("SELECT * FROM experience_cards WHERE id = ?",
                             (card_id,))
        return self._card(rows[0]) if rows else None

    def root(self, unit_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM experience_cards WHERE scope = ? AND unit_id = ? "
            "ORDER BY created_at LIMIT 1", (self.scope, unit_id))
        return self._card(rows[0]) if rows else None

    def cards(self, task_ids: Optional[Iterable[str]] = None
              ) -> List[Dict[str, Any]]:
        """The cards of these tasks - a task's own root among them - or
        every card in the scope."""
        if task_ids is None:
            rows = self.db.query("SELECT * FROM experience_cards WHERE scope = ? "
                                 "ORDER BY created_at", (self.scope,))
        else:
            wanted = [str(one) for one in task_ids]
            if not wanted:
                return []
            holes = ", ".join("?" for _ in wanted)
            rows = self.db.query(
                f"SELECT * FROM experience_cards WHERE scope = ? AND "
                f"(task_id IN ({holes}) OR unit_id IN ({holes})) "
                f"ORDER BY created_at", (self.scope, *wanted, *wanted))
        return [self._card(row) for row in rows]

    def update_card(self, card_id: str, fields: Dict[str, Any]) -> None:
        allowed = {"parent_id", "type", "title", "statement", "specification",
                   "checked_by", "why", "refs", "status", "replaced_by",
                   "status_why", "task_id"}
        sets, values = [], []
        for name, value in fields.items():
            if name not in allowed:
                continue
            sets.append(f"{name} = ?")
            values.append(json.dumps(value, ensure_ascii=False)
                          if isinstance(value, (dict, list)) else str(value))
        if not sets:
            return
        self.db.execute(
            f"UPDATE experience_cards SET {', '.join(sets)}, updated_at = ? "
            f"WHERE id = ?", (*values, now(), card_id))

    def move_cards(self, task_from: str, task_to: str) -> None:
        """A task's cards follow its work into another task, ids kept."""
        self.db.execute(
            "UPDATE experience_cards SET task_id = ?, updated_at = ? WHERE "
            "scope = ? AND task_id = ?", (task_to, now(), self.scope, task_from))

    # -- threads ----------------------------------------------------------
    def add_thread(self, task_id: str, title: str) -> Dict[str, Any]:
        stamp = now()
        thread_id = new_id("thread")
        self.db.execute(
            "INSERT INTO experience_threads (id, scope, task_id, title, "
            "status, why, created_at, updated_at) VALUES (?, ?, ?, ?, "
            "'open', '', ?, ?)",
            (thread_id, self.scope, task_id, title[:300], stamp, stamp))
        return {"id": thread_id, "task_id": task_id, "title": title,
                "status": "open", "why": ""}

    def threads(self, task_ids: Iterable[str]) -> List[Dict[str, Any]]:
        wanted = [str(one) for one in task_ids]
        if not wanted:
            return []
        holes = ", ".join("?" for _ in wanted)
        return [dict(row) for row in self.db.query(
            f"SELECT * FROM experience_threads WHERE scope = ? AND task_id "
            f"IN ({holes}) ORDER BY created_at", (self.scope, *wanted))]

    def close_thread(self, thread_id: str, status: str, why: str) -> None:
        if status not in ("finished", "abandoned", "open"):
            return
        self.db.execute(
            "UPDATE experience_threads SET status = ?, why = ?, "
            "updated_at = ? WHERE id = ?",
            (status, why[:500], now(), thread_id))

    def move_threads(self, thread_ids: Sequence[str], task_id: str) -> None:
        for one in thread_ids:
            self.db.execute(
                "UPDATE experience_threads SET task_id = ?, updated_at = ? "
                "WHERE id = ?", (task_id, now(), one))

    # -- edges ------------------------------------------------------------
    def add_edge(self, source: str, target: str, kind: str,
                 reason: str = "") -> None:
        if source == target or kind not in EDGE_KINDS:
            return
        exists = self.db.query(
            "SELECT 1 FROM experience_edges WHERE scope = ? AND source = ? "
            "AND target = ? AND kind = ?", (self.scope, source, target, kind))
        if exists:
            return
        self.db.execute(
            "INSERT INTO experience_edges (id, scope, source, target, kind, "
            "reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_id("edge"), self.scope, source, target, kind, reason[:200],
             now()))

    def edges(self, stage_ids: Optional[Iterable[str]] = None
              ) -> List[Dict[str, Any]]:
        if stage_ids is None:
            return [dict(row) for row in self.db.query(
                "SELECT * FROM experience_edges WHERE scope = ?",
                (self.scope,))]
        wanted = [str(one) for one in stage_ids]
        if not wanted:
            return []
        holes = ", ".join("?" for _ in wanted)
        return [dict(row) for row in self.db.query(
            f"SELECT * FROM experience_edges WHERE scope = ? AND "
            f"(source IN ({holes}) OR target IN ({holes}))",
            (self.scope, *wanted, *wanted))]

    def drop_edges(self, reason: str) -> int:
        """Every edge one act made, and nothing else - undoing a join."""
        rows = self.db.query(
            "SELECT COUNT(*) AS c FROM experience_edges WHERE scope = ? AND "
            "reason = ?", (self.scope, reason))
        self.db.execute("DELETE FROM experience_edges WHERE scope = ? AND "
                        "reason = ?", (self.scope, reason))
        return int(rows[0]["c"]) if rows else 0

    def drop_join_edges(self, project_id: str, stage_ids: Sequence[str]) -> int:
        """The edges joins into a project made that touch these stages."""
        wanted = [str(one) for one in stage_ids]
        if not wanted:
            return 0
        holes = ", ".join("?" for _ in wanted)
        where = (f"scope = ? AND reason LIKE ? AND (source IN ({holes}) OR "
                 f"target IN ({holes}))")
        values = (self.scope, f"join:{project_id}:%", *wanted, *wanted)
        rows = self.db.query(f"SELECT COUNT(*) AS c FROM experience_edges "
                             f"WHERE {where}", values)
        self.db.execute(f"DELETE FROM experience_edges WHERE {where}", values)
        return int(rows[0]["c"]) if rows else 0

    # -- marks: where a task's stages are written up to -------------------
    def mark(self, task_id: str) -> Dict[str, Any]:
        rows = self.db.query(
            "SELECT * FROM experience_marks WHERE task_id = ? AND scope = ?",
            (task_id, self.scope))
        if not rows:
            return {"states_seen": [], "open_tail": []}
        return {"states_seen": _loads(rows[0]["states_seen"], []),
                "open_tail": _loads(rows[0]["open_tail"], [])}

    def set_mark(self, task_id: str, states_seen: Sequence[str],
                 open_tail: Sequence[str]) -> None:
        self.db.execute(
            "INSERT INTO experience_marks (task_id, scope, states_seen, "
            "open_tail, updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT"
            "(task_id, scope) DO UPDATE SET states_seen = excluded.states_seen, "
            "open_tail = excluded.open_tail, updated_at = excluded.updated_at",
            (task_id, self.scope, json.dumps(list(dict.fromkeys(states_seen))),
             json.dumps(list(dict.fromkeys(open_tail))), now()))

    # -- nature links -------------------------------------------------------
    def natures_of(self, state_ids: Iterable[str]) -> Dict[str, List[str]]:
        """For each state, the nature records written from it."""
        wanted = [str(one) for one in state_ids]
        out: Dict[str, List[str]] = {}
        for start in range(0, len(wanted), 400):
            chunk = wanted[start:start + 400]
            if not chunk:
                continue
            holes = ", ".join("?" for _ in chunk)
            for row in self.db.query(
                    f"SELECT state_id, record_id FROM nature_links WHERE "
                    f"state_id IN ({holes})", tuple(chunk)):
                out.setdefault(row["state_id"], []).append(row["record_id"])
        return out

    def link_count(self) -> int:
        rows = self.db.query("SELECT COUNT(*) AS c FROM nature_links")
        return int(rows[0]["c"]) if rows else 0

    # -- patterns and principles --------------------------------------------
    @staticmethod
    def _pattern(row: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(row)
        out["members"] = _loads(out.get("members"), [])
        out["written_by"] = _loads(out.get("written_by"), {})
        # How it came to be what it is - its words before each rewording,
        # and each split - kept in `written_by` so that every rewrite of the
        # group carries it; handed out on its own for the page to show.
        history = out["written_by"].get("history") \
            if isinstance(out["written_by"], dict) else None
        out["history"] = list(history) if isinstance(history, list) else []
        return out

    def patterns(self, layer: Optional[str] = None,
                 kind: Optional[str] = None) -> List[Dict[str, Any]]:
        """Patterns or principles - of one kind of nature, when `kind` is
        given ('' for those built before there were kinds)."""
        if layer:
            rows = self.db.query(
                "SELECT * FROM experience_patterns WHERE scope = ? AND "
                "layer = ? ORDER BY support DESC", (self.scope, layer))
        else:
            rows = self.db.query(
                "SELECT * FROM experience_patterns WHERE scope = ? "
                "ORDER BY layer DESC, support DESC", (self.scope,))
        out = [self._pattern(row) for row in rows]
        if kind is not None:
            out = [row for row in out if (row.get("nature_kind") or "") == kind]
        return out

    # -- workspaces -------------------------------------------------------------
    def _key(self, unit_id: str) -> str:
        """A unit's workspace row in this scope. The live one is kept under
        the unit's own id; another scope's - the Build now test's - under
        the id with the scope before it, so the two never share a row:
        the table's key is the unit alone."""
        return unit_id if self.scope == LIVE else f"{self.scope}|{unit_id}"

    def workspaces(self, origin: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = self.db.query("SELECT * FROM experience_workspaces WHERE scope = ? "
                             "ORDER BY created_at", (self.scope,))
        out = []
        prefix = f"{self.scope}|"
        for row in rows:
            row = dict(row)
            if self.scope != LIVE and str(row["unit_id"]).startswith(prefix):
                row["unit_id"] = str(row["unit_id"])[len(prefix):]
            row["summary"] = _loads(row.get("summary"), {})
            row["paths"] = [str(one) for one in _loads(row.get("paths"), [])
                            if str(one).strip()] or [row["path"]]
            if origin is None or row["origin"] == origin:
                out.append(row)
        return out

    def create_work_project(self, name: str) -> Dict[str, Any]:
        """A project the user makes for the agents to work on: a name and
        nothing to read - it has no history of the user's, and what the
        agents do in it is theirs. Its folders are the ones granted to the
        work (`work_folders`), not a workspace read for stages."""
        unit_id = new_id("work")
        return self.set_workspace(unit_id, "", "work", name.strip())

    def create_project(self, name: str, paths: Sequence[str],
                       description: str = "") -> Dict[str, Any]:
        """A project the user makes of one folder or several - with what it
        is for, in their words, as its goal."""
        unit_id = new_id("proj")
        held = self.set_workspace(unit_id, paths[0], "chosen", name)
        self.set_paths(unit_id, paths)
        if description.strip():
            # THE USER'S DESCRIPTION is kept in a key of its own: the goal
            # beside it is what reading the folders later says the project
            # is for, and is rewritten each time - the description never.
            words = description.strip()[:DESCRIPTION_CHARS]
            self.db.execute(
                "UPDATE experience_workspaces SET summary = ? WHERE unit_id = ?",
                (json.dumps({"title": name, "goal": words, "description": words},
                            ensure_ascii=False), self._key(unit_id)))
        return self.workspace(unit_id) or held

    def set_description(self, unit_id: str, description: str,
                        name: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """The user's description of a project they made, changed in their
        own words - and, if given, its name. None for no such project."""
        held = self.workspace(unit_id)
        if held is None:
            return None
        summary = dict(held.get("summary") or {})
        summary["description"] = str(description or "").strip()[:DESCRIPTION_CHARS]
        self.db.execute(
            "UPDATE experience_workspaces SET summary = ?, name = ?, updated_at = ? "
            "WHERE unit_id = ?",
            (json.dumps(summary, ensure_ascii=False),
             str(name).strip() if name is not None and str(name).strip() else held["name"],
             now(), self._key(unit_id)))
        return self.workspace(unit_id)

    def keep_descriptions(self) -> int:
        """THE DESCRIPTIONS OF PROJECTS MADE BEFORE THEY HAD A PLACE OF THEIR
        OWN: a project the user made kept what they said it is for only as
        its goal - the field reading its folders rewrites. Copied, once, to
        its own key while the goal still holds it. How many were copied."""
        copied = 0
        for one in self.workspaces(origin="chosen"):
            summary = one.get("summary") or {}
            if not str(one["unit_id"]).startswith("proj_") or "description" in summary:
                continue
            summary = dict(summary, description=str(summary.get("goal") or "")
                           .strip()[:DESCRIPTION_CHARS])
            self.db.execute("UPDATE experience_workspaces SET summary = ? WHERE unit_id = ?",
                            (json.dumps(summary, ensure_ascii=False),
                             self._key(one["unit_id"])))
            copied += 1
        return copied

    def set_paths(self, unit_id: str, paths: Sequence[str],
                  name: Optional[str] = None) -> None:
        """A project's folders - changed, it is read again from the start."""
        held = self.workspace(unit_id)
        if held is None:
            return
        paths = [str(one) for one in paths if str(one).strip()]
        changed = sorted(paths) != sorted(held["paths"])
        self.db.execute(
            "UPDATE experience_workspaces SET paths = ?, path = ?, name = ?, "
            "processed_until = ?, processing_until = ?, updated_at = ? "
            "WHERE unit_id = ?",
            (json.dumps(paths, ensure_ascii=False), paths[0] if paths else held["path"],
             name if name is not None else held["name"],
             "" if changed else held["processed_until"],
             "" if changed else (held.get("processing_until") or ""), now(),
             self._key(unit_id)))

    # -- what the stage writer was given --------------------------------------------
    #: How many of a unit's prompts are kept.
    PROMPTS_KEPT = 6

    def save_prompt(self, unit_id: str, kind: str, system: str,
                    messages: List[Dict[str, Any]], answer: str,
                    written_by: Optional[Dict[str, Any]] = None,
                    calls: int = 1) -> None:
        self.db.execute(
            "INSERT INTO experience_prompts (id, unit_id, kind, system, messages, "
            "answer, calls, written_by, created_at, scope) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id("prompt"), unit_id, kind, system,
             json.dumps(messages, ensure_ascii=False, default=str), answer or "",
             int(calls), json.dumps(written_by or {}), now(), self.scope))
        old = self.db.query("SELECT id FROM experience_prompts WHERE unit_id = ? "
                            "AND scope = ? ORDER BY created_at DESC",
                            (unit_id, self.scope))[self.PROMPTS_KEPT:]
        for row in old:
            self.db.execute("DELETE FROM experience_prompts WHERE id = ?", (row["id"],))

    def prompt_index(self) -> List[Dict[str, Any]]:
        """Every prompt kept, without its text: what to list before one is
        opened - a workspace pass's can run to hundreds of kilobytes."""
        return [dict(row) for row in self.db.query(
            "SELECT id, unit_id, kind, calls, written_by, created_at, "
            "length(system) + length(messages) + length(answer) AS size "
            "FROM experience_prompts WHERE scope = ? ORDER BY created_at DESC",
            (self.scope,))]

    def prompt(self, prompt_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.query("SELECT * FROM experience_prompts WHERE id = ?",
                             (prompt_id,))
        if not rows:
            return None
        row = dict(rows[0])
        row["messages"] = _loads(row.get("messages"), [])
        row["written_by"] = _loads(row.get("written_by"), {})
        return row

    def prompts(self, unit_id: str) -> List[Dict[str, Any]]:
        rows = self.db.query("SELECT * FROM experience_prompts WHERE unit_id = ? "
                             "AND scope = ? ORDER BY created_at DESC",
                             (unit_id, self.scope))
        out = []
        for row in rows:
            row = dict(row)
            row["messages"] = _loads(row.get("messages"), [])
            row["written_by"] = _loads(row.get("written_by"), {})
            out.append(row)
        return out

    # -- natures a stage showed ---------------------------------------------------
    def link_stage_nature(self, stage_id: str, record_id: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO stage_natures (stage_id, record_id, "
                        "created_at) VALUES (?, ?, ?)", (stage_id, record_id, now()))

    def natures_of_stages(self, stage_ids: Iterable[str]) -> Dict[str, List[str]]:
        ids = list(stage_ids)
        out: Dict[str, List[str]] = {}
        for start in range(0, len(ids), 400):
            chunk = ids[start:start + 400]
            holes = ", ".join("?" for _ in chunk)
            for row in self.db.query(f"SELECT stage_id, record_id FROM stage_natures "
                                     f"WHERE stage_id IN ({holes})", tuple(chunk)):
                out.setdefault(row["stage_id"], []).append(row["record_id"])
        return out

    def stages_without_natures(self, most: int = 200) -> List[Dict[str, Any]]:
        """Stages whose natures have not been written: not linked to any, and
        not marked as read for them (a stage that showed nothing is marked)."""
        rows = self.db.query(
            "SELECT * FROM experience_stages WHERE scope = ? AND id NOT IN "
            "(SELECT stage_id FROM stage_natures) ORDER BY began LIMIT ?",
            (self.scope, most * 3))
        out = [self._stage(row) for row in rows]
        return [one for one in out if not (one.get("notes") or {}).get("natured")][:most]

    def mark_natured(self, stage_id: str) -> None:
        stage = self.stage(stage_id)
        if stage is None:
            return
        notes = dict(stage.get("notes") or {}, natured=now())
        self.db.execute("UPDATE experience_stages SET notes = ? WHERE id = ?",
                        (json.dumps(notes, ensure_ascii=False), stage_id))

    def workspace(self, unit_id: str) -> Optional[Dict[str, Any]]:
        found = [one for one in self.workspaces() if one["unit_id"] == unit_id]
        return found[0] if found else None

    def set_workspace(self, unit_id: str, path: str, origin: str,
                      name: str = "") -> Dict[str, Any]:
        """A unit's folder - set anew starts its reading from the beginning."""
        stamp = now()
        self.db.execute(
            "INSERT INTO experience_workspaces (unit_id, scope, path, origin, "
            "name, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON "
            "CONFLICT(unit_id) DO UPDATE SET path = excluded.path, origin = "
            "excluded.origin, name = CASE WHEN excluded.name != '' THEN "
            "excluded.name ELSE experience_workspaces.name END, "
            "processed_until = CASE WHEN experience_workspaces.path != "
            "excluded.path THEN '' ELSE experience_workspaces.processed_until "
            "END, updated_at = excluded.updated_at",
            (self._key(unit_id), self.scope, path, origin, name, stamp, stamp))
        return self.workspace(unit_id) or {}

    def set_processed(self, unit_id: str, until: str,
                      summary: Optional[Dict[str, Any]] = None) -> None:
        if summary:
            # What reading the folders found replaces the summary - never
            # the user's own description, which is theirs.
            held = (self.workspace(unit_id) or {}).get("summary") or {}
            if "description" in held:
                summary = dict(summary, description=held["description"])
            self.db.execute("UPDATE experience_workspaces SET processed_until = ?, "
                            "summary = ?, updated_at = ? WHERE unit_id = ?",
                            (until, json.dumps(summary, ensure_ascii=False), now(),
                             self._key(unit_id)))
        else:
            self.db.execute("UPDATE experience_workspaces SET processed_until = ?, "
                            "updated_at = ? WHERE unit_id = ?",
                            (until, now(), self._key(unit_id)))

    def set_processing(self, unit_id: str, until: str) -> None:
        """The cutoff of a run under way - "" when it has finished."""
        self.db.execute("UPDATE experience_workspaces SET processing_until = ? "
                        "WHERE unit_id = ?", (until, self._key(unit_id)))

    def clear(self) -> Dict[str, int]:
        """Everything this scope holds, gone - for the Build now test, which
        starts each run from nothing. Never for the live scope."""
        if self.scope == LIVE:
            raise ValueError("the live experience is never cleared this way")
        gone: Dict[str, int] = {}
        stages = [row["id"] for row in self.db.query(
            "SELECT id FROM experience_stages WHERE scope = ?", (self.scope,))]
        for start in range(0, len(stages), 400):
            chunk = stages[start:start + 400]
            holes = ", ".join("?" for _ in chunk)
            self.db.execute(f"DELETE FROM stage_natures WHERE stage_id IN ({holes})",
                            tuple(chunk))
        from .index import terms_written
        terms_written()
        for table in ("experience_stages", "experience_cards", "stage_terms",
                      "experience_threads", "experience_edges",
                      "experience_marks", "experience_workspaces",
                      "experience_patterns", "experience_prompts"):
            gone[table] = self.db.execute(f"DELETE FROM {table} WHERE scope = ?",
                                          (self.scope,)).rowcount
        terms_written()
        return gone

    def mark_delegated(self, unit_id: str, source: str = "delegate",
                       output: str = "") -> None:
        """A project made of work the user handed over (agent/work/delegate.py):
        listed under Delegated work - and, given no folder, the one in the
        workspace its runs write in (`output`)."""
        held = (self.workspace(unit_id) or {}).get("summary") or {}
        said = dict(held, delegated=True, source=source)
        if output:
            said["output_folder"] = output
        self.db.execute("UPDATE experience_workspaces SET summary = ? WHERE unit_id = ?",
                        (json.dumps(said, ensure_ascii=False), self._key(unit_id)))

    def drop_workspace(self, unit_id: str) -> None:
        self.db.execute("DELETE FROM experience_workspaces WHERE unit_id = ?",
                        (self._key(unit_id),))

    # -- the two kinds of nature record ---------------------------------------
    def nature_kinds(self) -> Dict[str, str]:
        """Every record whose kind is known: record id -> work or personal."""
        return {row["record_id"]: row["kind"] for row in self.db.query(
            "SELECT record_id, kind FROM nature_kinds")}

    def set_nature_kind(self, record_id: str, kind: str,
                        written_by: Optional[Dict[str, Any]] = None) -> None:
        if kind not in NATURE_SAID or not record_id:
            return
        self.db.execute(
            "INSERT INTO nature_kinds (record_id, kind, written_by, created_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(record_id) DO UPDATE SET kind = "
            "excluded.kind, written_by = excluded.written_by",
            (record_id, kind, json.dumps(written_by or {}), now()))

    def refresh_last_active(self, library: Any) -> int:
        """When each pattern and principle was last seen in the user's work,
        as tasks keep theirs. A nature record is seen when the work it was
        read from happened - the latest of the stages it was written from,
        or the stretch the agent heartbeat observed it over - and a pattern
        is last active when the latest of its records was; a principle, the
        latest of its patterns. Natures keep no time of their own here.
        Worked out by code, from what is kept; returns how many changed."""
        from .patterns import nature_records

        def iso(value: Any) -> str:
            return value.isoformat() if hasattr(value, "isoformat") else str(value or "")

        through_stages = {row["record_id"]: str(row["at"] or "") for row in self.db.query(
            "SELECT sn.record_id AS record_id, MAX(COALESCE(NULLIF(s.ended, ''), "
            "s.began)) AS at FROM stage_natures sn JOIN experience_stages s ON "
            "s.id = sn.stage_id WHERE s.scope = ? GROUP BY sn.record_id",
            (self.scope,))}
        seen: Dict[str, str] = {}
        for record in nature_records(library):
            seen[record.id] = through_stages.get(record.id) or iso(
                getattr(record, "observed_to", None)
                or getattr(record, "observed_from", None)
                or getattr(record, "updated_at", None) or record.created_at)
        changed = 0
        pattern_at: Dict[str, str] = {}
        for layer in ("pattern", "principle"):
            for row in self.patterns(layer):
                if layer == "pattern":
                    at = max((seen[one] for one in row["members"] if one in seen),
                             default="")
                    pattern_at[row["id"]] = at
                else:
                    at = max((pattern_at[one] for one in row["members"]
                              if one in pattern_at), default="")
                if at != str(row.get("last_active") or ""):
                    self.db.execute("UPDATE experience_patterns SET last_active = ? "
                                    "WHERE id = ?", (at, row["id"]))
                    changed += 1
        return changed

    #: Two times a principle was seen this close are one stretch of it.
    STRETCH_GAP_DAYS = 2

    def active_stretches(self, library: Any) -> Dict[str, List[List[str]]]:
        """When each pattern and principle was active, as a task's work is
        drawn: the stretches of time its natures were seen in the user's
        work - each stage they were written from, or the span the agent
        heartbeat observed one over - with gaps of a couple of days or more
        left as gaps. By pattern or principle id."""
        from datetime import datetime, timedelta
        from .patterns import nature_records

        def moment(value: Any) -> Optional[datetime]:
            if value is None or value == "":
                return None
            if hasattr(value, "isoformat"):
                when = value
            else:
                try:
                    when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                except ValueError:
                    return None
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return when

        spans: Dict[str, List[tuple]] = {}
        for row in self.db.query(
                "SELECT sn.record_id AS record_id, s.began AS began, s.ended AS "
                "ended FROM stage_natures sn JOIN experience_stages s ON s.id = "
                "sn.stage_id WHERE s.scope = ?", (self.scope,)):
            began, ended = moment(row["began"]), moment(row["ended"] or row["began"])
            if began is not None:
                spans.setdefault(row["record_id"], []).append((began, ended or began))
        for record in nature_records(library):
            if record.id in spans:
                continue
            began = moment(getattr(record, "observed_from", None)) or moment(
                record.created_at)
            ended = moment(getattr(record, "observed_to", None)) or began
            if began is not None:
                spans[record.id] = [(began, ended or began)]

        gap = timedelta(days=self.STRETCH_GAP_DAYS)

        def merged(pieces: List[tuple]) -> List[List[str]]:
            out: List[List[Any]] = []
            for began, ended in sorted(pieces):
                if out and began - out[-1][1] <= gap:
                    out[-1][1] = max(out[-1][1], ended)
                else:
                    out.append([began, ended])
            return [[one.isoformat(), two.isoformat()] for one, two in out]

        result: Dict[str, List[List[str]]] = {}
        of_pattern: Dict[str, List[tuple]] = {}
        for row in self.patterns("pattern"):
            pieces = [one for member in row["members"] for one in spans.get(member, [])]
            of_pattern[row["id"]] = pieces
            result[row["id"]] = merged(pieces)
        for row in self.patterns("principle"):
            result[row["id"]] = merged([one for member in row["members"]
                                        for one in of_pattern.get(member, [])])
        return result

    def save_pattern(self, pattern: Dict[str, Any]) -> Dict[str, Any]:
        stamp = now()
        pattern_id = pattern.get("id") or new_id(
            "principle" if pattern["layer"] == "principle" else "pattern")
        self.db.execute(
            "INSERT INTO experience_patterns (id, scope, layer, parent, "
            "statement, detail, members, support, written_by, created_at, "
            "updated_at, nature_kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET parent = excluded.parent, statement = "
            "excluded.statement, detail = excluded.detail, members = "
            "excluded.members, support = excluded.support, written_by = "
            "excluded.written_by, updated_at = excluded.updated_at, "
            "nature_kind = excluded.nature_kind",
            (pattern_id, self.scope, pattern["layer"],
             str(pattern.get("parent") or ""),
             str(pattern.get("statement") or "")[:600],
             str(pattern.get("detail") or "")[:3000],
             json.dumps(list(pattern.get("members") or [])),
             int(pattern.get("support") or 0),
             json.dumps(pattern.get("written_by") or {}), stamp, stamp,
             str(pattern.get("nature_kind") or "")))
        return dict(pattern, id=pattern_id)

    def drop_patterns(self, keep: Iterable[str]) -> None:
        kept = set(keep)
        for row in self.patterns():
            if row["id"] not in kept:
                self.db.execute("DELETE FROM experience_patterns WHERE id = ?",
                                (row["id"],))
