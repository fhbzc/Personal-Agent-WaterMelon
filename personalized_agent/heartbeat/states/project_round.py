"""ONE RUN OF THE STATE HEARTBEAT, AS TASKS AND PROJECTS (section 34.5).

The run reads the states written since the last one, has one model call
write them down as TASKS (given the user's current projects), groups the
last week's tasks into PROJECTS, and keeps both. See task_projects.py for
what each is and why.

THE SWITCH. The first run of this scheme marks every task and project of
the scheme before it as history - their content and their states as they
were, never regrouped (a regroup of kept tasks once cost the user their
record) - and reads the LAST WEEK in twelve-hour stretches, one after
another, so the projects are there from the start. Every later run reads
only what is new.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...common.languages import in_display, in_english
from ...models.base import ModelRequest
from ...models.manager import ModelUnavailable
from . import task_projects as tp
from .tasks import Kept, Task, now as task_now

log = logging.getLogger(__name__)

#: The setting that records when this scheme took over (live scope only).
SWITCHED_AT = "state_projects_since"

WRITER_LANGUAGE = in_display(
    '"line_display", "title_display" and "summary_display"'
) + in_english("everything else: each project line, and each task's title and summary - "
               "they are kept for the agent's own use")


def is_scheme_row(task: Any) -> bool:
    return str((getattr(task, "profile", None) or {}).get("scheme") or "") == tp.SCHEME


class ProjectRound:
    """One run, over one scope (the user's own states, or the sandbox)."""

    def __init__(self, heartbeat: Any, where: Any, report: Dict[str, Any]) -> None:
        self.hb = heartbeat
        self.where = where
        self.scope = where.scope
        self.db = heartbeat.db
        self.report = report
        self.tasks = tp.RunTasks(self.db, self.scope)
        self.kept = Kept(self.db, self.scope)
        self.run_id = "r-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        self.report.setdefault("scheme", tp.SCHEME)
        self.report.setdefault("calls", 0)
        self.report["stretches"] = []

    # -- the run ----------------------------------------------------------------
    def run(self) -> Dict[str, Any]:
        self._switch()
        stretches, newest = self._what_to_read()
        self.report["states_read"] = sum(len(states) for states, _window in stretches)
        if not stretches:
            self.report["note"] = "No state was written since the last run."
            return self.report
        for states, window in stretches:
            if not self.hb._going(self.where):
                self.report["note"] = self.hb._given_up(self.where)
                return self.report
            done = self._one_stretch(states, window)
            if not done:
                return self.report          # the model could not be reached
        self._name_what_is_new()
        self._dormant()
        if newest:
            self.hb._processed_to(self.where, newest)
        self.report["round_complete"] = True
        return self.report

    # -- the switch ---------------------------------------------------------------
    def _switch(self) -> None:
        """Mark the scheme before this one as history, once."""
        already = (self.hb._marks.get(("switched", self.scope))
                   if self.scope != "live" else self.hb.config.get(SWITCHED_AT, ""))
        if already:
            return
        moment = task_now()
        moved = {"tasks": 0, "projects": 0}
        try:
            before = [task for task in self.kept.live() if not is_scheme_row(task)]
            for task in before:
                task.archived_at = moment
                task.profile = dict(task.profile or {})
                task.profile["history_since"] = moment
            # Written without touching anything else of theirs.
            for task in before:
                self.db.execute("UPDATE state_tasks SET archived_at = ?, updated_at = ? "
                                "WHERE id = ? AND scope = ?",
                                (moment, moment, task.id, self.scope))
            moved["tasks"] = len(before)
            rows = self.db.query("SELECT id FROM state_projects WHERE scope = ? AND "
                                 "(archived_at IS NULL OR archived_at = '')", (self.scope,))
            for row in rows:
                self.db.execute("UPDATE state_projects SET archived_at = ? WHERE id = ? "
                                "AND scope = ?", (moment, dict(row)["id"], self.scope))
            moved["projects"] = len(rows)
        except Exception:
            log.warning("could not mark the earlier tasks as history", exc_info=True)
        if self.scope != "live":
            self.hb._marks[("switched", self.scope)] = moment
        else:
            self.hb.config.set(SWITCHED_AT, moment, internal=True)
        self.report["switched"] = {"at": moment, "kept_as_history": moved}

    # -- what is read ---------------------------------------------------------------
    def _what_to_read(self) -> Tuple[List[Tuple[List[Any], Tuple[datetime, datetime]]], str]:
        """The stretches to write, oldest first, and the moment to mark as
        read. The first run of the scheme: the last week, in twelve-hour
        stretches. Afterwards: what was written since the last run."""
        from .heartbeat import OBSERVED

        states_store = self.where.states
        cap = max(50, int(self.hb.config.get("state_heartbeat_max_states", 1000) or 1000))
        end = tp.now()
        if self.tasks.count() == 0:
            begin = end - timedelta(days=tp.WINDOW_DAYS)
            found = list(states_store.since(begin, limit=cap * 3, sources=OBSERVED))
            found = self._the_users(found)
            found.sort(key=self._when)
            self.report["window"] = {"kind": "the last week, at the switch",
                                     "from": tp.iso(begin), "to": tp.iso(end)}
            stretches = []
            at = begin
            while at < end:
                upto = min(end, at + timedelta(hours=tp.SLICE_HOURS))
                mine = [state for state in found if at <= self._when(state) < upto]
                for part in self._parts(mine):
                    stretches.append((part, (at, upto)))
                at = upto
            newest = max((self._updated(state) for state in found), default="")
            return stretches, newest or tp.iso(end)
        marker = str(self.hb._processed_until(self.where) or "")
        at_cap = marker.endswith("@cap")
        since = tp.moment(marker[:-4] if at_cap else marker) or (end - timedelta(days=1))
        fresh = list(states_store.after(since, limit=cap, sources=OBSERVED, inclusive=at_cap))
        fresh = self._the_users(fresh)
        held = self.tasks.held_states([str(state.id) for state in fresh])
        new = [state for state in fresh if str(state.id) not in held]
        new.sort(key=self._when)
        self.report["window"] = {"kind": "new since the last run", "from": marker,
                                 "read": len(fresh), "already_in_a_task": len(held)}
        newest = max((self._updated(state) for state in fresh), default="")
        if len(fresh) >= cap and newest:
            newest += "@cap"
        if not new:
            return [], newest
        low = min(self._when(state) for state in new)
        return [(part, (low, end)) for part in self._parts(new)], newest

    @staticmethod
    def _the_users(states: Sequence[Any]) -> List[Any]:
        return [state for state in states
                if str(getattr(state, "about", "user") or "user") == "user"]

    @staticmethod
    def _when(state: Any) -> datetime:
        return (tp.moment(getattr(state, "starts_at", None))
                or tp.moment(getattr(state, "created_at", None)) or tp.now())

    @staticmethod
    def _updated(state: Any) -> str:
        moment = (tp.moment(getattr(state, "updated_at", None))
                  or tp.moment(getattr(state, "created_at", None)))
        return tp.iso(moment)

    @staticmethod
    def _parts(states: List[Any]) -> List[List[Any]]:
        size = tp.MOST_STATES_PER_CALL
        return [states[at:at + size] for at in range(0, len(states), size)]

    # -- one stretch -------------------------------------------------------------------
    def _projects_now(self) -> List[Task]:
        return [task for task in self.kept.live() if is_scheme_row(task)]

    def _one_stretch(self, states: List[Any], window: Tuple[datetime, datetime]) -> bool:
        began = time.perf_counter()
        projects = self._projects_now()
        recent = sorted(projects, key=lambda one: one.last_seen, reverse=True)
        short_of = tp.short_labels([one.id for one in recent], "P")
        of_label = {label: pid for pid, label in short_of.items()}
        needing = {one.id: (one.described or {}).get("why_line") or "new"
                   for one in recent if not (one.described or {}).get("line")}
        rows = [{"id": one.id, "line": (one.described or {}).get("line", ""),
                 "title": ((one.described or {}).get("in_english") or {}).get("title")
                 or (one.described or {}).get("title", ""),
                 "summary": ((one.described or {}).get("in_english") or {}).get("summary")
                 or (one.described or {}).get("summary", ""),
                 "last_active": (one.last_seen or "")[:16]} for one in recent]
        tasks_of: Dict[str, List[Dict[str, Any]]] = {}
        for pid in needing:
            tasks_of[pid] = self.tasks.of_project(pid, limit=8)
        minutes = tp.worked_minutes(states, window)
        short = tp.short_labels([str(state.id) for state in states], "s")
        prompt = "\n\n".join(part for part in (
            tp.needing_text(needing, tasks_of,
                            {pid: (p.described or {}).get("line", "") for p in recent
                             for pid in [p.id]}, short_of),
            tp.project_list_text(rows, needing, short_of),
            tp.stretch_text(states, minutes, short)) if part)
        self.hb._at(self.report, "writing the tasks",
                    f"{len(states)} state(s) from {window[0]:%m-%d %H:%M}")
        answer, reply, usage = self._ask(tp.WRITER_SYSTEM, prompt, {"states": len(states)})
        stretch = {"from": tp.iso(window[0]), "to": tp.iso(window[1]),
                   "states": len(states), "sent": prompt, "reply": reply,
                   "tokens_in": int(getattr(usage, "input_tokens", 0) or 0),
                   "tokens_out": int(getattr(usage, "output_tokens", 0) or 0)}
        self.report["stretches"].append(stretch)
        if answer is None:
            return False
        lines = tp.read_lines(answer, of_label)
        written = tp.read_tasks(answer, states, short, of_label)
        by_id = {str(state.id): state for state in states}
        rows_written = []
        for task in written:
            mine = [by_id[sid] for sid in task["states"]]
            spans = [(self._when(state), tp.moment(getattr(state, "ends_at", None))
                      or self._when(state)) for state in mine]
            places: Counter = Counter()
            for state in mine:
                places.update(tp.directories(tp.state_places(state)))
            rows_written.append({
                **task, "id": tp.new_task_id(), "run_id": self.run_id,
                "written_at": tp.iso(tp.now()),
                "minutes": round(sum(minutes.get(sid, 0.0) for sid in task["states"]), 1),
                "starts_at": tp.iso(min(one for one, _ in spans)),
                "ends_at": tp.iso(max(two for _, two in spans)),
                "places": dict(places.most_common(12))})
        self.tasks.add(rows_written)
        stretch["tasks"] = [{key: row[key] for key in ("id", "title", "kind", "minutes",
                                                        "continues", "states")}
                            for row in rows_written]
        stretch["lines"] = lines
        self._regroup(window[1], lines)
        stretch["seconds"] = round(time.perf_counter() - began, 1)
        return True

    def _ask(self, system: str, prompt: str, metadata: Dict[str, Any]
             ) -> Tuple[Optional[Dict[str, Any]], str, Any]:
        from .heartbeat import _as_json

        try:
            response = self.hb.models.invoke(
                ModelRequest(messages=[{"role": "user", "content": prompt}],
                             system=system,
                             max_tokens=int(self.hb.config.get("max_output_tokens", 4096)
                                            or 4096) * 2,
                             task="state_heartbeat", language=WRITER_LANGUAGE,
                             metadata={"kind": "tasks", **metadata}),
                role="heartbeat")
        except ModelUnavailable as exc:
            self.report["blocked"] = str(exc)
            return None, "", None
        self.report["calls"] = int(self.report.get("calls", 0)) + 1
        usage = getattr(response, "usage", None)
        self.hb._add_usage(self.report, usage)
        reply = getattr(response, "text", "") or ""
        answer = _as_json(reply)
        if answer is None:
            self.report.setdefault("unreadable", 0)
            self.report["unreadable"] += 1
            answer = {}
        return answer, reply, usage

    # -- projects -------------------------------------------------------------------------
    def _vectors(self, tasks: Sequence[Dict[str, Any]]) -> Dict[str, List[float]]:
        embedder = getattr(self.hb, "embedder", None)
        if embedder is None or not self.hb.config.get("state_embedding_enabled", True):
            return {}
        texts = [f"{task.get('title', '')}. {task.get('summary', '')}" for task in tasks]
        try:
            found = embedder.embed(texts)
        except Exception:
            log.warning("the tasks could not be read by meaning", exc_info=True)
            return {}
        return {task["id"]: list(vector) for task, vector in zip(tasks, found or [])
                if vector}

    def _regroup(self, at: datetime, lines: Dict[str, Dict[str, str]]) -> None:
        """The last week's tasks, grouped again into projects, each project
        keeping its id while its main body goes on."""
        self.hb._at(self.report, "grouping the projects", "")
        # Every task written so far in the week (a stretch of the first run
        # sees none of the stretches after it: they are not written yet).
        window = self.tasks.since(at - timedelta(days=tp.WINDOW_DAYS))
        vectors = self._vectors(window)
        previous: Dict[str, List[str]] = {}
        for task in window:
            if task.get("project_id"):
                previous.setdefault(task["project_id"], []).append(task["id"])
        if vectors:
            groups = tp.group(window, vectors, {t["id"]: t.get("project_id", "")
                                                for t in window})
        else:
            # Without the meaning of the tasks: what the writers said.
            by: Dict[str, List[str]] = {}
            for task in window:
                if task.get("kind") == "routine":
                    continue
                key = task.get("continues") or task.get("project_id") or task["id"]
                by.setdefault(key, []).append(task["id"])
            groups = list(by.values())
        minutes = {task["id"]: float(task.get("minutes") or 0.0) for task in window}
        assigned, happened, ended = tp.inherit(groups, previous, minutes, tp.new_project_id)
        project_of = {tid: pid for pid, tids in assigned.items() for tid in tids}
        # Routine tasks belong to no project.
        project_of.update({task["id"]: "" for task in window
                           if task.get("kind") == "routine"})
        self.tasks.set_projects(project_of)
        by_id = {task["id"]: task for task in window}
        existing = {task.id: task for task in self.kept.live() if is_scheme_row(task)}
        written = []
        number = max([task.number for task in self.kept.all(limit=5000)] or [0]) + 1
        for pid, tids in assigned.items():
            row = existing.get(pid)
            what = happened.get(pid, {"kind": "new", "from": []})
            if row is None:
                row = Task(id=pid, number=number, seat=number,
                           born_of={"kind": what["kind"], "from": list(what["from"])})
                number += 1
            mine = [by_id[tid] for tid in tids if tid in by_id]
            said = lines.get(pid)
            self._fill(row, mine, vectors, said, what)
            written.append(row)
        self.kept.save(written)
        # The projects that ended in a split or a merge: kept, retired,
        # pointing at where their work went.
        for pid, heir in ended.items():
            old = existing.get(pid)
            if old is None:
                continue
            old.born_of = dict(old.born_of or {})
            old.born_of["joined_into"] = heir
            old.retired_at = task_now()
            self.kept.save([old])
        self.report["projects"] = {
            "count": len(assigned),
            "new": sorted(pid for pid, one in happened.items() if one["kind"] == "new"),
            "merged": {pid: one["from"] for pid, one in happened.items()
                       if one["kind"] == "merged"},
            "split": {pid: one["from"] for pid, one in happened.items()
                      if one["kind"] == "split"},
            "ended": ended}

    def _fill(self, row: Task, tasks: List[Dict[str, Any]],
              vectors: Dict[str, List[float]], said: Optional[Dict[str, str]],
              what: Dict[str, Any]) -> None:
        """A project's row, from its tasks: its title and summary are those
        of its most typical recent task; what it IS is its own line."""
        described = dict(row.described or {})
        everything = self.tasks.of_project(row.id, limit=1000) or tasks
        known = {task["id"] for task in everything}
        everything = everything + [task for task in tasks if task["id"] not in known]
        best = tp.typical(tasks, vectors) or (tasks[-1] if tasks else {})
        english = {"title": best.get("title", ""), "summary": best.get("summary", "")}
        if said:
            described["line"] = said["line"]
            described["line_display"] = said.get("line_display") or said["line"]
            described.pop("why_line", None)
            row.version = int(row.version or 1) + (1 if row.summary_at else 0)
            row.summary_at = task_now()
        elif what["kind"] in ("merged", "split") and what.get("from"):
            described["line"] = ""
            described["why_line"] = f"{what['kind']} from {len(what['from'])} project(s)"
        english["what_it_is"] = described.get("line", "")
        described["title"] = best.get("title_display") or best.get("title", "")
        described["summary"] = best.get("summary_display") or best.get("summary", "")
        described["what_it_is"] = described.get("line_display") or described.get("line", "")
        # The writer wrote both languages: the English copy is marked as made
        # from the words beside it, so it is not translated again.
        from .english_copies import stamped
        described["in_english"] = stamped(english, described,
                                          ("title", "summary", "what_it_is"))
        described["kind"] = next((task.get("kind") for task in tasks
                                  if task.get("kind") in ("work", "leisure")), "work")
        described["concrete"] = True
        described["minutes"] = round(sum(float(task.get("minutes") or 0.0)
                                         for task in everything), 1)
        described["last_task"] = best.get("id", "")
        described["tasks"] = len(everything)
        places: Counter = Counter()
        for task in everything:
            places.update(task.get("places") or {})
        described["directories"] = [place for place, _n in places.most_common(5)]
        members: List[str] = []
        for task in sorted(everything, key=lambda one: str(one.get("ends_at") or "")):
            members.extend(str(one) for one in task.get("states") or [])
        row.members = list(dict.fromkeys(members))[-600:]
        total: List[float] = []
        for task in tasks:
            vector = vectors.get(task["id"])
            if vector:
                total = ([a + b for a, b in zip(total, vector)] if total else list(vector))
        if total:
            row.total = total
        firsts = [str(task.get("starts_at") or "") for task in everything if task.get("starts_at")]
        lasts = [str(task.get("ends_at") or "") for task in everything if task.get("ends_at")]
        profile = dict(row.profile or {})
        profile.update({"v": 1, "scheme": tp.SCHEME,
                        "first_seen": min(firsts) if firsts else profile.get("first_seen", ""),
                        "last_seen": max(lasts) if lasts else profile.get("last_seen", ""),
                        "count": len(row.members), "weight": float(len(row.members)),
                        "places": dict(places.most_common(12)),
                        "minutes": described["minutes"]})
        row.profile = profile
        row.described = described
        row.updated_at = task_now()
        row.archived_at = ""

    # -- after the stretches -------------------------------------------------------------
    def _name_what_is_new(self) -> None:
        """A line for each project the last stretch made, so none is shown
        without one until the next run."""
        projects = [one for one in self._projects_now() if not (one.described or {}).get("line")]
        if not projects:
            return
        short_of = tp.short_labels([one.id for one in projects], "P")
        of_label = {label: pid for pid, label in short_of.items()}
        tasks_of = {one.id: self.tasks.of_project(one.id, limit=8) for one in projects}
        prompt = tp.needing_text({one.id: (one.described or {}).get("why_line") or "new"
                                  for one in projects}, tasks_of, {}, short_of)
        self.hb._at(self.report, "naming the new projects", f"{len(projects)} project(s)")
        answer, reply, _usage = self._ask(tp.LINES_SYSTEM, prompt, {"projects": len(projects)})
        self.report["naming"] = {"sent": prompt, "reply": reply}
        if not answer:
            return
        lines = tp.read_lines(answer, of_label)
        changed = []
        for one in projects:
            said = lines.get(one.id)
            if not said:
                continue
            described = dict(one.described or {})
            described["line"] = said["line"]
            described["line_display"] = said.get("line_display") or said["line"]
            described["what_it_is"] = described["line_display"]
            from .english_copies import stamped
            english = dict(described.get("in_english") or {})
            english["what_it_is"] = said["line"]
            described["in_english"] = stamped(english, described, ("what_it_is",))
            described.pop("why_line", None)
            one.described = described
            one.summary_at = task_now()
            changed.append(one)
        self.kept.save(changed)

    def _dormant(self) -> None:
        """A project with no task in the last week goes dormant: kept, not
        drawn, not continued - new work on the same thing later is new."""
        line = tp.iso(tp.now() - timedelta(days=tp.WINDOW_DAYS))
        quiet = [one for one in self._projects_now()
                 if one.last_seen and one.last_seen < line]
        for one in quiet:
            one.archived_at = task_now()
        if quiet:
            self.kept.save(quiet)
        self.report["dormant"] = [one.id for one in quiet]


def project_view(task: Any, run_tasks: Optional[tp.RunTasks] = None,
                 how_many_tasks: int = 0) -> Dict[str, Any]:
    """A project as the page shows it."""
    described = dict(getattr(task, "described", None) or {})
    out = {"id": task.id, "title": described.get("title", ""),
           "summary": described.get("summary", ""),
           "what_it_is": described.get("what_it_is") or described.get("line", ""),
           "kind": described.get("kind", "work"),
           "minutes": float(described.get("minutes") or 0.0),
           "tasks_count": int(described.get("tasks") or 0),
           "directories": list(described.get("directories") or []),
           "first_seen": task.first_seen, "last_seen": task.last_seen,
           "born_of": dict(getattr(task, "born_of", None) or {}),
           "dormant": bool(getattr(task, "archived_at", "")),
           "ended": bool(getattr(task, "retired_at", "")),
           "in_english": dict(described.get("in_english") or {})}
    if run_tasks is not None and how_many_tasks:
        out["tasks"] = [{key: one.get(key) for key in (
            "id", "title", "summary", "title_display", "summary_display", "kind",
            "minutes", "starts_at", "ends_at", "places", "run_id", "states")}
            for one in run_tasks.of_project(task.id, limit=how_many_tasks)]
    return out


__all__ = ["ProjectRound", "is_scheme_row", "project_view", "SWITCHED_AT"]
