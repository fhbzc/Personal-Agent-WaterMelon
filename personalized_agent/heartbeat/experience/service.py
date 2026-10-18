"""The experience, as one service: kept up after each state round, built
on request, drawn for the panel, and searched for a step of work."""

from __future__ import annotations

import json
import re
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any, Callable, Dict, List, Optional

from ...common.room import at_once
from .natures import NatureSorter, kind_of, kinds_of
from .patterns import PatternBuilder, nature_records
from .retrieve import Retriever, backfill_links
from .stages import CALLS_PER_ROUND, StageWriter, task_name
from .store import NATURE_KINDS, ExperienceStore, LIVE, description_of

#: The Build now test's scope: its stages, natures and patterns, kept apart.
BUILD = "build"


def scoped_key(scope: str, name: str) -> str:
    """A setting a scope keeps: the live experience's under its own name, the
    Build now test's with the scope in it - asked here by everything that
    reads one, so they cannot disagree about where it is."""
    if scope == LIVE:
        return name
    return name.replace("experience_", f"experience_{scope}_", 1)


def datetime_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

log = logging.getLogger(__name__)

#: Patterns are worked out again when this many nature records have arrived
#: since the last time - regrouping on every new record names nothing new.
REGROUP_AFTER = 20

#: The most of a nature's examples sent when it is opened - all of them, as
#: a nature has so far been seen in one to a few; the total is said with it.
EXAMPLES_SHOWN = 200


def _locations(references: List[Any]) -> List[Dict[str, str]]:
    """Where a case can be opened - each place once, as text."""
    out: Dict[str, Dict[str, str]] = {}
    for one in references:
        if not isinstance(one, dict):
            continue
        where = str(one.get("location") or "").strip()
        if not where:
            continue
        said = {"location": where[:600], "what": str(one.get("what") or "")[:300],
                "kind": str(one.get("kind") or ""), "access": str(one.get("access") or "")}
        if where in out:
            # The same place named twice: what the first left blank, the
            # second may say.
            out[where].update({k: v for k, v in said.items() if v and not out[where][k]})
        else:
            out[where] = said
    return list(out.values())[:24]


def _specific(folder: str, unit: Dict[str, Any], every_unit: List[Dict[str, Any]],
              experience: "Experience") -> float:
    """How few of the OTHER pieces of work touch a folder - any place inside
    it, enough - from 1 (none) to 0 (all): `specificity`, over units, where a
    project and its tasks are one piece of work."""
    mine = str(folder).replace("\\", "/").lower().replace("-", "_").rstrip("/")
    others = [one for one in every_unit if one["id"] != unit["id"]]
    if not others:
        return 1.0
    touching = 0
    for other in others:
        for place, share in experience._places_of(other).items():
            if share >= experience.WORKSPACE_SHARE and (
                    place == mine or place.startswith(mine + "/")):
                touching += 1
                break
    return 1.0 - touching / len(others)



def _moment_key(text: str) -> float:
    """When, as a number to sort by - 0 for never."""
    from datetime import datetime, timezone
    try:
        when = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()

class Experience:
    """Stage graphs, patterns and principles, and the search across them."""

    def __init__(self, db: Any, config: Any, states: Any, library: Any,
                 embedder: Any, models: Any, scope: str = LIVE,
                 tasks_scope: Optional[str] = None) -> None:
        self.db = db
        self.config = config
        #: The clock that updates the changed projects by itself (auto.py).
        self.auto_clock: Any = None
        #: The one pool everything side by side takes its places in
        #: (common/pool.py); none, as many as `room` allows of its own.
        self.pool: Any = None
        #: The projects queued by the automatic update - after what the user
        #: asked for, in the pool's order.
        self._learning_auto: set = set()
        self.states = states
        self.library = library
        self.embedder = embedder
        self.models = models
        # WHERE IT WRITES, AND WHOSE WORK IT READS. The live experience reads
        # the live tasks and writes the live tables. The Build now test reads
        # the same live tasks - the same input, through the same code - and
        # writes only its own scope and its own library, so nothing it does
        # reaches the live experience.
        self.scope = scope
        self.tasks_scope = tasks_scope or scope
        self.store = ExperienceStore(db, scope)
        try:
            # The descriptions of projects made before they had a place of
            # their own, kept there before reading the folders can rewrite
            # the goal that holds them.
            self.store.keep_descriptions()
        except Exception:            # pragma: no cover - a database without the table
            log.debug("could not keep the projects' descriptions", exc_info=True)
        self._building = threading.Lock()
        self.progress: Dict[str, Any] = {"running": False}
        #: Each project the user made, while it is processed.
        self.project_progress: Dict[str, Dict[str, Any]] = {}
        #: How far this round's share of the work has got, for the state
        #: heartbeat's progress bars: the stages, written at the end of the
        #: round, and the patterns, built beside it.
        self.round_progress: Dict[str, Any] = {}
        self.patterns_progress: Dict[str, Any] = {}
        self._patterns_running = threading.Lock()
        # One writer at a time per project, when several are written at
        # once; and how many may go at once - given by the state heartbeat,
        # which owns it (common/room.py). None is one at a time.
        self._unit_locks: Dict[str, Any] = {}
        self._unit_locks_lock = threading.Lock()
        self.room: Any = None
        #: Projects being learned, and those waiting for room.
        self.learning_progress: Dict[str, Dict[str, Any]] = {}
        self._learn_queue: List[str] = []
        self._learn_lock = threading.Lock()
        self._learn_thread: Optional[threading.Thread] = None
        #: Told what each part of a round cost - its time window, calls and
        #: tokens - by the state heartbeat, which keeps the round's account.
        #: Called as on_spent(part, began, ended, used).
        self.on_spent = None
        #: Whether the pattern build under way is part of a round, and so
        #: counted in its account - not when the user pressed "Build now".
        self._counted = True
        #: Whether a task is shown - set by the state heartbeat, which
        #: decides that. A task that is not shown gets no stages and cannot
        #: be worked on: it is not something the user is doing.
        self.is_shown = lambda task: (task.described or {}).get(
            "concrete") is not False

    def _key(self, name: str) -> str:
        return scoped_key(self.scope, name)

    # -- the parts -----------------------------------------------------------
    def stages_writer(self, going=None) -> StageWriter:
        writer = StageWriter(self.db, self.states, self.models, self.scope,
                             going=going)
        writer.until = self._stage_pass().get("until", "")
        root = getattr(self.library, "root", None)
        from .materials import Materials
        # A stage's references are its states' materials, kept when the
        # states were written; nothing is searched for again.
        writer.materials = Materials(self.db, (root / "references")
                                     if root is not None else None)
        # What each stage shows about the user is written with it.
        writer.library = self.library
        # In the language the user reads.
        writer.language = self.language()
        # The known natures closest to the work are shown with it, so a
        # trait seen again becomes one more example of its nature; each
        # example says the project and task it was in.
        writer.embedder = self.embedder
        writer.names = self._units_of_tasks
        return writer

    def language(self) -> str:
        """The user's display language, by name - what stages, cards and
        natures are written in."""
        from ...common.languages import display_language, name_of
        return name_of(display_language(self.config))

    def backfill_materials(self) -> Dict[str, int]:
        """ONCE: the states written before they had materials, from the
        fortnight whose raw activity is still kept - before any stage is
        written from them, by a round or by "Build now"."""
        if self.config.get("experience_materials_backfilled"):
            return {}
        said: Dict[str, int] = {}
        try:
            from .materials import Materials
            root = getattr(self.library, "root", None)
            said = Materials(self.db, (root / "references") if root is not None
                             else None).backfill(self.states)
        except Exception:        # pragma: no cover - not the round's fault
            log.warning("could not give past states materials", exc_info=True)
        self.config.set("experience_materials_backfilled", True, internal=True)
        return said

    def repin_references(self) -> Dict[str, int]:
        """ONCE, for the stages written before references were pinned down
        as they are now: each reference and deliverable read again by
        script - whole paths, agent sessions' conversations, commits, and
        a copy of each local file - with no model asked."""
        flag = "experience_references_pinned_v2"
        if self.config.get(flag):
            return {}
        report = {"stages": 0, "references": 0}
        try:
            writer = self.stages_writer()
            for stage in self.store.stages():
                members = list(stage.get("states") or [])
                work = dict(stage.get("work") or {})
                made = dict(stage.get("deliverable") or {})
                work["references"] = writer._keep_references(
                    list(work.get("references") or []), members)
                made["references"] = writer._keep_references(
                    list(made.get("references") or []), members)
                self.db.execute(
                    "UPDATE experience_stages SET work = ?, deliverable = ? "
                    "WHERE id = ?", (json.dumps(work, ensure_ascii=False),
                                     json.dumps(made, ensure_ascii=False),
                                     stage["id"]))
                report["stages"] += 1
                report["references"] += len(work["references"]) + len(
                    made["references"])
        except Exception:        # pragma: no cover - not the round's fault
            log.warning("could not pin down past references", exc_info=True)
        self.config.set(flag, True, internal=True)
        return report

    def collect_references(self, cutoff: str = "") -> Dict[str, int]:
        """Once every stage up to the pass's cutoff is written: the copies no
        stage cites, of states more than the setting's days before that
        cutoff, deleted - so they do not fill the disk."""
        root = getattr(self.library, "root", None)
        try:
            from .references import collect_garbage
            said = collect_garbage(
                self.db, (root / "references") if root is not None else None,
                int(self.config.get("experience_reference_keep_days", 30) or 0),
                cutoff)
        except Exception:        # pragma: no cover - never the round's fault
            log.warning("could not clear old reference copies", exc_info=True)
            return {}
        if said.get("deleted"):
            log.info("deleted %d reference copies no stage cites (%d bytes)",
                     said["deleted"], said["bytes"])
        return said

    # -- workspaces ------------------------------------------------------------
    def directory_units(self) -> List[Dict[str, Any]]:
        """The folders the user added, each a project of its own."""
        out = []
        for one in self.store.workspaces(origin="chosen"):
            said = one.get("summary") or {}
            out.append({"id": one["unit_id"], "kind": "directory",
                        "name": one.get("name") or said.get("title")
                        or Path(one["path"]).name,
                        "why": said.get("goal") or "", "status": said.get("status") or "",
                        "tasks": [], "workspace": one["path"],
                        "paths": one.get("paths") or [one["path"]],
                        "description": description_of(one)})
        return out

    def _units(self, writer: StageWriter, live, projects) -> List[Dict[str, Any]]:
        return (list(writer.units(live, projects)) + self.directory_units()
                + self.work_units())

    def _shown_units(self, writer: StageWriter, live, projects
                     ) -> List[Dict[str, Any]]:
        """Every unit the panel shows: the ones at work now - found from what
        the user is doing, marked `active` - and EVERY ONE EVER LEARNED.

        WHAT WAS LEARNED STAYS. A project or task that has gone quiet, or
        ended, was dropped from the list with its tasks, and its stages and
        lessons with it - though nothing about what was learned from it had
        stopped being true. A unit with a card or a stage stays, built again
        from its tasks as they are kept, dormant or ended."""
        found = list(writer.units(live, projects))
        for unit in found:
            unit["active"] = True
        # A project the user removed is not shown again.
        removed = self.removed()
        units = [unit for unit in found + self.directory_units() + self.work_units()
                 if unit["id"] not in removed]
        from .stages import unit_task_ids
        seen = {unit["id"] for unit in units} | set(removed)
        covered = {one for unit in units for one in unit_task_ids(unit)}
        learned = [row["unit_id"] for row in self.db.query(
            "SELECT DISTINCT unit_id FROM experience_cards WHERE scope = ? "
            "AND unit_id != ''", (self.scope,))]
        learned += [row["task_id"] for row in self.db.query(
            "SELECT DISTINCT task_id FROM experience_stages WHERE scope = ?",
            (self.scope,))]
        wanted = [one for one in dict.fromkeys(learned)
                  if one and one not in seen and one not in covered]
        if not wanted:
            return units
        try:
            from ..states.tasks import Kept, Projects
            kept = Kept(self.db, self.tasks_scope)
            every_project = {project.id: project for project in
                             Projects(self.db, self.tasks_scope).all()}
        except Exception:                 # pragma: no cover - a read
            log.debug("could not read the learned units", exc_info=True)
            return units
        for one in wanted:
            project = every_project.get(one)
            if project is not None:
                tasks = kept.by_ids(list(project.tasks or []))
                units.append({"id": one, "kind": "project",
                              "name": project.name, "why": project.why,
                              "status": (project.described or {}).get("status", ""),
                              "tasks": tasks, "active": False})
                seen.add(one)
                covered.update(task.id for task in tasks)
                continue
            if one in covered:
                continue
            tasks = kept.by_ids([one])
            if not tasks:
                continue
            said = tasks[0].described or {}
            units.append({"id": one, "kind": "task", "name": task_name(tasks[0]),
                          "why": said.get("goal", ""),
                          "status": said.get("status", ""), "tasks": tasks,
                          "active": False})
            seen.add(one)
        return units

    def work_units(self) -> List[Dict[str, Any]]:
        """The projects the user made for the agents to work on. Never read
        for stages - nothing of the user's is in them."""
        return [{"id": one["unit_id"], "kind": "new project",
                 "name": one.get("name") or "a new project", "why": "",
                 "status": "", "tasks": [], "workspace": "",
                 "description": description_of(one),
                 # Handed over by the user: listed under Delegated work.
                 "delegated": bool((one.get("summary") or {}).get("delegated"))}
                for one in self.store.workspaces(origin="work")]

    def add_directory(self, path: str) -> Dict[str, Any]:
        """A folder the user chose: a project of its own, read from the
        beginning on the next pass."""
        from .workspace import unit_id_for
        folder = Path(path).expanduser().resolve()
        if not folder.is_dir():
            raise ValueError(f"not a folder: {folder}")
        # No name of its own: the one its summary gives, once read - the
        # folder's until then.
        return self.store.set_workspace(unit_id_for(folder), str(folder),
                                        "chosen", "")

    def workspace_of(self, unit: Dict[str, Any], writer: StageWriter):
        """A unit's workspace: as recorded, or - for a project the listener
        saw - the repository its states point to most, recorded once."""
        from .workspace import Workspace
        held = self.store.workspace(unit["id"])
        if held is None and unit.get("tasks"):
            if unit["id"] in getattr(self, "_no_workspace", set()):
                return None, None
            live, _retired, projects = self._work(retired=False)
            folders = self._detect_folders(unit, writer.units(live, projects))
            if not folders:
                self._no_workspace = getattr(self, "_no_workspace", set()) | {unit["id"]}
                return None, None
            held = self.store.set_workspace(unit["id"], folders[0], "detected",
                                            unit.get("name") or "")
            self.store.set_paths(unit["id"], folders)
            held = self.store.workspace(unit["id"])
        if held is None or held.get("origin") == "work":
            # A project made for the agents has no folder of the user's to
            # read - what is in its granted folders is the agents' work.
            return None, None
        from .workspace import open_workspace
        # An empty path is no folder - not the current directory.
        folders = [one for one in held.get("paths") or [held["path"]]
                   if str(one).strip() and Path(one).is_dir()]
        if not folders:
            return None, None
        return open_workspace(folders), held

    #: How much of a unit's work must have touched a place for its folder to
    #: be part of the unit's workspace - a place touched once in passing is
    #: not where the work is.
    WORKSPACE_SHARE = 0.1

    #: Of two folders one inside the other, the inner one is the work's when
    #: it has at least this share of what the outer one has.
    NESTED_SHARE = 0.6

    def _places_of(self, unit: Dict[str, Any]) -> Dict[str, float]:
        """Every place the unit's work touched, with how much of it did - its
        tasks' own record of where they were."""
        out: Dict[str, float] = {}
        for task in unit.get("tasks") or []:
            for place in task.places():
                out[place] = max(out.get(place, 0.0), task.coverage(place))
        return out

    #: How specific a folder must be to a piece of work to be read as part
    #: of it: 1 when no other piece of work touches it, 0 when all do. Half:
    #: a folder most of the user's work passes through is generic.
    WORKSPACE_SPECIFIC = 0.5

    def _detect_folders(self, unit: Dict[str, Any],
                        every_unit: Optional[List[Dict[str, Any]]] = None
                        ) -> List[str]:
        """EVERY LOCAL PLACE THE WORK TOUCHED, as the folders to read: each
        place that enough of it touched, found on disk, lifted to the root of
        the project it is in; the home folder and the folders that hold
        everything left out, and a folder inside another taken once."""
        from .workspace import is_container, project_root, real_path
        chosen: Dict[str, float] = {}
        for place, share in self._places_of(unit).items():
            if share < self.WORKSPACE_SHARE:
                continue
            path = real_path(place)
            if path is None or not path.exists():
                continue
            root = project_root(path)
            if is_container(root):
                continue
            key = str(root.resolve())
            chosen[key] = max(chosen.get(key, 0.0), share)
        # ONE FOLDER INSIDE ANOTHER: which is the work's? The deeper one, as
        # long as it still has most of the outer one's share of the work -
        # "Research" (0.30) holds the replication package (0.20), which is
        # the work's; the package holds its scripts (0.10), which are a part
        # of it, not the work.
        drop = set()
        for outer in chosen:
            for inner in chosen:
                if outer != inner and Path(outer) in Path(inner).parents:
                    if chosen[inner] >= self.NESTED_SHARE * chosen[outer]:
                        drop.add(outer)
                    else:
                        drop.add(inner)
        kept = [one for one in chosen if one not in drop]
        # ONLY WHAT IS SPECIFIC TO THIS WORK: a folder many other pieces of
        # work also pass through is where the user works, not this work.
        if every_unit:
            kept = [one for one in kept
                    if _specific(one, unit, every_unit, self) >= self.WORKSPACE_SPECIFIC]
        return sorted(kept, key=lambda one: -chosen[one])

    def workspace_pass(self, writer: StageWriter, unit: Dict[str, Any],
                       budget: Optional[int] = None,
                       until: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Read a unit's workspace for what changed since it was last read,
        up to this pass's cutoff - the one freeze every reading shares - and
        write the stages of it, with its newest states. None when there is
        nothing to read."""
        workspace, held = self.workspace_of(unit, writer)
        if workspace is None:
            return None
        until = until or self._stage_pass(open_one=True).get("until", "")
        since = str(held.get("processed_until") or "")
        if since and since >= until:
            return None
        from .stages import STATES_PER_CALL
        states = writer.pending(unit)[:STATES_PER_CALL] if unit.get("tasks") else []
        writer.places_touched = self._places_of(unit)
        said = writer.extend_from_workspace(unit, workspace, since, until,
                                            states=states, budget=budget)
        if not said.get("error"):
            # As far as its stages reach: a long history read in parts
            # carries on from there next time.
            self.store.set_processed(unit["id"], said.get("covered_until") or until,
                                     said.get("project"))
        return said

    def _stages_to_read_for_natures(self) -> List[Dict[str, Any]]:
        """The stages of the projects the listener saw that have not yet been
        read for what they show - those of the user's own projects wait for
        their Process or Update."""
        mine = {one["unit_id"] for one in self.store.workspaces(origin="chosen")}
        return [s for s in self.store.stages_without_natures(500)
                if s["task_id"] not in mine]

    # -- the projects the user makes ------------------------------------------------
    def create_project(self, name: str, paths: List[str],
                       description: str = "") -> Dict[str, Any]:
        folders = []
        for one in paths:
            folder = Path(str(one)).expanduser().resolve()
            if not folder.is_dir():
                raise ValueError(f"not a folder: {folder}")
            folders.append(str(folder))
        if not folders:
            raise ValueError("a project needs a folder")
        return self.store.create_project(name.strip() or Path(folders[0]).name,
                                         folders, description)

    def process_project(self, unit_id: str, most_calls: int = 200,
                        resume: bool = False) -> bool:
        """Process - or update - a project the user made: its folders read for
        what changed since it was last processed, up to now - the moment
        asked, one freeze for all of it - as many passes as a long history
        needs; then what the new stages show about the user. In a thread of
        its own; one at a time per project.

        The cutoff is kept with the project until the run has done both, so
        a run cut short - an error, the calls running out, the agent quitting
        - can be RESUMED: under the same cutoff, from as far as its stages
        reached, then the natures of every stage still without them."""
        held = self.store.workspace(unit_id)
        if held is None or held.get("origin") != "chosen":
            return False
        progress = self.project_progress.setdefault(unit_id, {})
        if progress.get("running"):
            return False
        from datetime import datetime, timezone
        until = (held.get("processing_until") or "") if resume else ""
        if not until:
            until = datetime.now(timezone.utc).isoformat()
            self.store.set_processing(unit_id, until)
        progress.clear()
        progress.update(running=True, began=time.time(), calls=0, stages=0,
                        natures=0, step="reading its folders", until=until,
                        resumed=bool(resume and until == held.get("processing_until")))

        def run() -> None:
            try:
                writer = self.stages_writer(lambda: progress.get("running"))
                read_to_the_end = False
                for _ in range(12):
                    unit = next((u for u in self.directory_units()
                                 if u["id"] == unit_id), None)
                    if unit is None or progress["calls"] >= most_calls \
                            or not progress.get("running"):
                        break
                    said = self.workspace_pass(writer, unit, until=until)
                    if said is None:
                        # Nothing left before the cutoff.
                        read_to_the_end = True
                        break
                    progress["calls"] += said.get("calls", 0)
                    progress["stages"] += said.get("stages", 0)
                    # What the stages show, written with them.
                    progress["natures"] += said.get("natures", 0)
                    if said.get("error"):
                        progress["error"] = said["error"]
                        break
                    if not said.get("covered_until"):
                        read_to_the_end = True
                        break
                progress["step"] = "what the stages show about you"
                from .stages import unit_task_ids
                ids = set(unit_task_ids({"id": unit_id, "tasks": []}))
                fresh = [s for s in self.store.stages_without_natures(500)
                         if s["task_id"] in ids]
                said = self.natures_from_stages(
                    fresh, budget=max(1, most_calls - progress["calls"]),
                    going=lambda: progress.get("running"))
                progress["calls"] += said.get("calls", 0)
                progress["natures"] += said.get("natures", 0)
                left = [s for s in self.store.stages_without_natures(500)
                        if s["task_id"] in ids]
                if read_to_the_end and not left and not progress.get("error"):
                    self.store.set_processing(unit_id, "")
                else:
                    progress["unfinished"] = True
            except Exception as exc:     # pragma: no cover - reported
                log.exception("processing a project failed")
                progress["error"] = str(exc)[:500]
            finally:
                progress.update(running=False, step="done",
                                seconds=round(time.time() - progress["began"], 1))

        threading.Thread(target=self._holding_the_model(run),
                         name=f"project-{unit_id}", daemon=True).start()
        return True

    def natures_from_stages(self, stages: List[Dict[str, Any]], budget: int,
                            going=None) -> Dict[str, Any]:
        """What these stages show about the user - mostly work nature -
        written and linked to them."""
        if not stages or self.models is None:
            return {"calls": 0, "natures": 0, "stages": 0}
        from .stage_natures import StageNatures
        writer = StageNatures(self.db, self.library, self.models, self.scope,
                              embedder=self.embedder)
        writer.language = self.language()
        writer.names = self._units_of_tasks
        began = time.time()
        said = writer.write(stages, budget, going)
        self._spent("stage natures", began, writer.used)
        return said

    def _stage_pass(self, open_one: bool = False,
                    until: Optional[str] = None) -> Dict[str, Any]:
        """The stage pass under way - its cutoff - opening one if asked.

        THE CUTOFF IS GIVEN WHEN THERE IS ONE TO GIVE. A state round passes
        its own - the line it read the states up to - so its stages cover
        exactly the activity its task summaries did, however many visits
        that takes; the Build now test passes the one it froze when it
        began. Without one, a pass opens frozen at the moment it opens.
        A pass that is done keeps its cutoff and says so.
        """
        key = self._key("experience_stages_pass")
        held = self.config.get(key) or {}
        if isinstance(held, str):
            try:
                held = json.loads(held)
            except ValueError:
                held = {}
        if until:
            if held.get("until") != until:
                held = {"until": until}
                self.config.set(key, held, internal=True)
            return held
        if open_one and (not held.get("until") or held.get("done")):
            from datetime import datetime, timezone
            held = {"until": datetime.now(timezone.utc).isoformat()}
            self.config.set(key, held, internal=True)
        return held

    def _close_stage_pass_if_done(self, writer: StageWriter,
                                  live, projects) -> bool:
        """Closed once every unit is caught up to the cutoff - its states in
        stages, AND its workspace read up to it. A reading cut short keeps
        the pass open, so the next visit resumes it under the same cutoff
        rather than opening a later one."""
        units = writer.units(live, projects)
        if any(writer.pending(unit) for unit in units):
            return False
        if self._workspaces_behind(writer, units):
            return False
        held = self._stage_pass()
        self.config.set(self._key("experience_stages_pass"),
                        {"until": held.get("until", ""), "done": True},
                        internal=True)
        return True

    def _workspaces_behind(self, writer: StageWriter,
                           units: List[Dict[str, Any]]) -> List[str]:
        """The units whose workspace is not yet read up to the pass's cutoff."""
        until = self._stage_pass().get("until", "")
        if not until:
            return []
        behind = []
        for unit in units:
            workspace, held = self.workspace_of(unit, writer)
            if workspace is not None and str(held.get("processed_until") or "") < until:
                behind.append(unit["id"])
        return behind

    def patterns_builder(self, going=None, kind: str = "work") -> PatternBuilder:
        return PatternBuilder(self.db, self.library, self.embedder,
                              self.models, self.scope, going=going, kind=kind)

    def sorter(self, builder: Optional[PatternBuilder] = None) -> NatureSorter:
        builder = builder or self.patterns_builder()
        return NatureSorter(self.db, self.models, self.scope,
                            lambda prompt, items, tokens: builder._ask(
                                prompt, items, tokens=tokens))

    def _spent(self, part: str, began: float, used: Dict[str, int]) -> None:
        """Tell the round's account what one part cost, if anyone keeps it."""
        if self.on_spent is None or not self._counted:
            return
        if not used.get("calls") and time.time() - began < 1:
            return
        try:
            self.on_spent(part, began, time.time(), dict(used))
        except Exception:        # pragma: no cover - an account, not a step
            log.debug("could not count what %s cost", part, exc_info=True)

    def retriever(self) -> Retriever:
        # Found by what each stage is, by code: no embedding model.
        return Retriever(self.db, self.library, scope=self.scope)

    def _work(self, shown_only: bool = True, retired: bool = True):
        """The tasks, the retired ones, and the projects - as kept.

        Only the SHOWN tasks by default: what is hidden - not one piece of
        work, or too little of it - and what is dormant is not cut into
        stages and not offered to be worked on. A project keeps only its
        shown tasks, and one with none is left out.

        `retired=False`: no retired tasks ([] in their place) - for the
        readers that drop them, which are the panel's, asked every few
        seconds; reading 500 tasks to throw them away was a part of that.
        """
        from ..states.tasks import Kept, Projects
        kept = Kept(self.db, self.tasks_scope)
        live = kept.live()
        retired = ([task for task in kept.all(500) if task.retired_at]
                   if retired else [])
        projects = Projects(self.db, self.tasks_scope).all()
        if shown_only:
            live = [task for task in live if self.is_shown(task)]
            ids = {task.id for task in live}
            for project in projects:
                project.tasks = [one for one in project.tasks or [] if one in ids]
            projects = [project for project in projects if project.tasks]
        return live, retired, projects

    # -- kept up after a round ----------------------------------------------------
    @contextmanager
    def _unit_held(self, unit_id: str, wait: bool = True):
        """One writer at a time for a project's stages: two at once would each
        extend the same frontier. Yields whether it was got - with
        `wait=False`, a project already being written is not."""
        with self._unit_locks_lock:
            lock = self._unit_locks.setdefault(unit_id, threading.Lock())
        got = lock.acquire(blocking=wait)
        try:
            yield got
        finally:
            if got:
                lock.release()

    def _holding_the_model(self, run: Callable[[], None]) -> Callable[[], None]:
        """A piece of work that holds the embedding model while it runs: loaded
        at its first use, and let go - its process ended - when it is done."""
        def held() -> None:
            if self.embedder is None:
                return run()
            with self.embedder.loaded():
                return run()
        return held

    def after_round(self, budget: int = CALLS_PER_ROUND, going=None,
                    until: Optional[str] = None) -> Dict[str, Any]:
        """A visit's share of turning the round's work into stages, under the
        round's own cutoff - `until`, the line it read the states up to.

        Says `pass_closed` when every unit is caught up to it, and `progress`
        when this visit wrote something: the round is kept open while it is
        neither, and carried on two seconds later, so its stages are done
        under its cutoff before it is called finished."""
        report: Dict[str, Any] = {"units": []}
        if not self.config.get("experience_enabled", True):
            report["skipped"] = "turned off"
            return report
        going = going or (lambda: True)
        began = time.time()
        backfilled = self.backfill_materials()
        if backfilled:
            report["materials_backfilled"] = backfilled
        repinned = self.repin_references()
        if repinned:
            report["references_repinned"] = repinned
        every, retired, _all_projects = self._work(shown_only=False)
        live, _retired, projects = self._work(retired=False)
        held = self._stage_pass(open_one=not until, until=until)
        writer = self.stages_writer(going)
        if held.get("done"):
            # Caught up already: what is left is what the new stages show
            # about the user.
            report["pass_closed"] = True
            if going():
                said = self.natures_from_stages(
                    self._stages_to_read_for_natures(), budget=2, going=going)
                report["stage_natures"] = said
                report["progress"] = bool(said.get("calls"))
            return report
        report["followed"] = writer.follow_the_tasks(every, retired, projects)
        report["joins"] = writer.joins(projects)
        progress = self.round_progress = {
            "phase": "stages", "units": 0, "units_done": 0,
            "calls": 0, "budget": budget, "patterns": 0, "named": 0}
        units = writer.units(live, projects)
        waiting = [unit for unit in units if writer.pending(unit)]
        progress["units"] = len(waiting)
        # EACH PROJECT'S STAGES BESIDE THE OTHERS', as many at once as the
        # computer has room for (common/room.py). Each has its own writer,
        # and a project is never written by two at once. The round's calls
        # are shared out: one project waiting has all of them, several have
        # a share each, as one at a time had them in turn.
        share = max(1, budget // max(1, len(waiting)))
        used: Dict[str, int] = {}
        jobs: List[Dict[str, Any]] = [{"kind": "workspace"}]
        jobs += [{"kind": "states", "unit": unit} for unit in waiting[:budget]]
        doing: List[str] = []

        def work(job: Dict[str, Any]) -> Any:
            mine = self.stages_writer(going)
            try:
                if job["kind"] == "workspace":
                    # ONE WORKSPACE READ A ROUND, at most: a folder that
                    # changed, read for what changed - a round is not held for
                    # every project's. A project being written beside it is
                    # left for the next round.
                    for unit in units:
                        if not going():
                            return None
                        with self._unit_held(unit["id"], wait=False) as free:
                            if not free:
                                continue
                            said = self.workspace_pass(mine, unit)
                        if said is not None:
                            return said
                    return None
                with self._unit_held(job["unit"]["id"]):
                    said = mine.extend(job["unit"], budget=share)
                    said["done"] = not mine.pending(job["unit"])
                    return said
            finally:
                for name, value in mine.used.items():
                    used[name] = used.get(name, 0) + int(value or 0)

        def starting(job: Dict[str, Any]) -> Dict[str, Any]:
            if "unit" in job:
                doing.append(job["unit"]["name"])
            progress["unit"] = ", ".join(doing)
            return job

        def landed(job: Dict[str, Any], said: Any, error: Any) -> bool:
            if "unit" in job and job["unit"]["name"] in doing:
                doing.remove(job["unit"]["name"])
            progress["unit"] = ", ".join(doing)
            if error is not None:
                log.warning("writing stages failed", exc_info=error)
                return True
            if said is None:
                return True
            progress["calls"] = progress.get("calls", 0) + int(said.get("calls", 0))
            if job["kind"] == "workspace":
                report.setdefault("workspaces", []).append(said)
            else:
                if said.pop("done", False):
                    progress["units_done"] += 1
                report["units"].append(said)
            return True

        at_once((starting(job) for job in jobs), work, self.room,
                going=going, landed=landed, pool=self.pool, kind="learning",
                told=lambda running, allowed, why: progress.update(
                    at_once={"running": running, "allowed": allowed, "why": why}))
        writer.used.update({name: writer.used.get(name, 0) + value
                            for name, value in used.items()})
        progress.update(phase="done", unit="")
        # Progress is what got further: a unit whose answers were cut off
        # even at the fewest states, or a folder whose answer was, did not -
        # the round is not kept turning on them, and says so.
        # A unit got further when it wrote stages or read states - a call
        # whose answer was cut off read none, and is not progress.
        report["progress"] = any(
            one.get("stages") or one.get("states")
            for one in report["units"]) or any(
            not one.get("error") for one in report.get("workspaces") or [])
        failed = [one for one in report["units"] + (report.get("workspaces") or [])
                  if one.get("error")]
        if failed:
            report["errors"] = [{"unit": one.get("unit"), "error": one["error"]}
                                for one in failed]
        until = self._stage_pass().get("until", "")
        report["pass_closed"] = self._close_stage_pass_if_done(
            writer, live, projects)
        if report["pass_closed"]:
            # Every state up to the cutoff is in stages now: what they did
            # not cite, from long enough before it, never will be.
            report["references_collected"] = self.collect_references(until)
        report["seconds"] = round(time.time() - began, 1)
        self._spent("stages", began, writer.used)
        # And what the new stages show about the user - a call or two a round.
        if going():
            report["stage_natures"] = self.natures_from_stages(
                self._stages_to_read_for_natures(), budget=2, going=going)
        # The patterns are not done here: they are built beside the round,
        # from its start - see `start_patterns`. Started here as well, in
        # case this visit is one that carries on an open round.
        report["patterns"] = {"started": self.start_patterns(going)}
        return report

    def start_patterns(self, going=None, budget: int = 20,
                       counted: bool = True) -> bool:
        """Patterns and principles, built in a thread of their own.

        THEY DEPEND ON NOTHING THE ROUND DOES. Stages are written from the
        round's tasks, so they come after it; patterns are written from the
        nature records, which the round does not touch - so they run beside
        it, from its start, and take as many calls as they need without
        holding it up. One build at a time; one that is running, or not
        due, is left alone.
        """
        if not self.config.get("experience_enabled", True):
            return False
        if not self._patterns_running.acquire(blocking=False):
            return False
        going = going or (lambda: True)

        self._counted = counted

        def run() -> None:
            self.patterns_progress = {"patterns_running": True,
                                      "began": time.time()}
            try:
                said = self._regroup_if_due(going, budget=budget)
                self.patterns_progress.update(said=said)
            except Exception:    # pragma: no cover - never the round's fault
                log.warning("building the patterns failed", exc_info=True)
            finally:
                self.patterns_progress["patterns_running"] = False
                self._patterns_running.release()

        threading.Thread(target=self._holding_the_model(run), name="experience-patterns",
                         daemon=True).start()
        return True

    def _regroup_if_due(self, going, budget: int) -> Dict[str, Any]:
        """A pattern round: opened with a cutoff, carried on until it closes.

        LIKE A STATE ROUND, IT IS FROZEN WHEN IT OPENS. It takes the nature
        records written up to that moment and no others, and keeps that
        cutoff until everything in it is done - so one stopped part way,
        by a quit or by running out of calls, carries on with exactly the
        same records, and a record written meanwhile waits for the next
        round rather than shifting this one under it.

        TWO KINDS, BUILT APART AND AT ONCE. Every record is first given its
        kind - work or personal - and the patterns built before there were
        kinds are carried over; then each kind's patterns and principles are
        built from its own records only, the two in parallel.
        """
        held = self.config.get(self._key("experience_patterns_round")) or {}
        if isinstance(held, str):
            try:
                held = json.loads(held)
            except ValueError:
                held = {}
        asked_for = getattr(self, "_regroup_now", False)
        self._regroup_now = False
        records = nature_records(self.library)
        if not held.get("until"):
            last = str(self.config.get(self._key("experience_grouped_until")) or "")
            new = [r for r in records if r.created_at.isoformat() > last]
            unsorted = self.sorter().unsorted(records)
            if not asked_for and len(new) < REGROUP_AFTER \
                    and self.store.patterns() and not unsorted:
                return {"skipped": f"{len(new)} new record(s) since the "
                                   f"last round"}
            held = {"until": max((r.created_at.isoformat() for r in records),
                                 default=""), "opened": time.strftime(
                                     "%Y-%m-%dT%H:%M:%S")}
            self.config.set(self._key("experience_patterns_round"), held, internal=True)
        until = held["until"]
        records = [r for r in records if not until
                   or r.created_at.isoformat() <= until]
        if not self.config.get("experience_links_backfilled"):
            made = backfill_links(self.db, self.library)
            self.config.set("experience_links_backfilled", True, internal=True)
            log.info("linked %d past state-nature pairs by run", made)

        # 1. Every record its kind.
        sorting = self.patterns_progress.setdefault("sorting", {})
        began = time.time()
        asker = self.patterns_builder(going)
        sorted_now = self.sorter(asker).sort(records, budget, going, sorting)
        self._spent("sorting", began, asker.used)
        self.unsorted_count = sorted_now["left"]
        said: Dict[str, Any] = {"until": until, "sorted": sorted_now}
        # THE KINDS ARE BUILT FROM WHAT IS SORTED, not once everything is:
        # each kind's builder reads only the records known to be of it, and
        # one sorted later joins its kind's patterns as any new record does.
        # Waiting for the last of 1,400 records left both trees empty for a
        # round while the old patterns sat in one.
        #
        # 2. The patterns from before there were kinds, given theirs - once
        # most records have a kind, so a pattern's kind is read from most of
        # what is under it, not from a few.
        if self.store.patterns(kind="") and \
                sorted_now["left"] <= len(records) / 2:
            said["carried_over"] = self.sorter().carry_over(records)

        # 3. Each kind's patterns and principles, the two at once.
        results: Dict[str, Dict[str, Any]] = {}

        def build(kind: str) -> None:
            mine = self.patterns_progress.setdefault(kind, {})
            mine.update(running=True)
            builder = self.patterns_builder(going, kind=kind)
            builder.progress = mine
            began = time.time()
            try:
                results[kind] = builder.build(budget=budget, until=until)
                self._spent(kind, began, builder.used)
            except Exception as exc:   # pragma: no cover - reported
                log.warning("building the %s patterns failed", kind,
                            exc_info=True)
                results[kind] = {"unfinished": f"failed: {exc}"[:200]}
            finally:
                mine["running"] = False

        threads = [threading.Thread(target=build, args=(kind,),
                                    name=f"experience-{kind}-patterns",
                                    daemon=True) for kind in NATURE_KINDS]
        for one in threads:
            one.start()
        for one in threads:
            one.join()
        said.update(results)
        # When each pattern and principle was last active, after grouping.
        try:
            self.store.refresh_last_active(self.library)
        except Exception:        # pragma: no cover - worked out again later
            log.warning("could not work out when the patterns were active",
                        exc_info=True)
        if sorted_now["left"]:
            results["sorting"] = {"unfinished": f"{sorted_now['left']} "
                                                f"records still to sort"}
        if not any(r.get("unfinished") for r in results.values()):
            # Closed: the next round takes what was written after this one.
            self.config.set(self._key("experience_grouped_until"), until, internal=True)
            self.config.set(self._key("experience_patterns_round"), {}, internal=True)
        else:
            said["unfinished"] = "; ".join(
                f"{kind}: {r['unfinished']}" for kind, r in results.items()
                if r.get("unfinished"))
        return said

    # -- built on request ---------------------------------------------------------
    # -- Build now: the test ------------------------------------------------------


    # -- learning, project by project ----------------------------------------------
    #: The most model calls one project's learning may make in one go.
    LEARN_CALLS = 400

    def learn(self, unit_ids: List[str], auto: bool = False) -> List[str]:
        """Learn these projects - their stages, and what the stages show
        about the user - each from where its last learning stopped, finished
        or not, up to now. Several at once, as many as the
        computer has room for (common/room.py); the rest wait their turn.
        Returns the ones taken on."""
        if self.scope != LIVE:
            raise ValueError("learning is the user's own experience")
        # WHILE THE AUTOMATIC UPDATE RUNS, the buttons start nothing: it is
        # learning the record, and two learnings would each read what the
        # other is writing (auto.py).
        if not auto and self.auto_running():
            return []
        taken = []
        with self._learn_lock:
            for unit_id in unit_ids:
                mine = self.learning_progress.get(unit_id) or {}
                if mine.get("running") or mine.get("queued"):
                    continue
                if not self._learnable(unit_id):
                    continue
                self.learning_progress[unit_id] = {"queued": True, "step": "waiting"}
                if auto:
                    self._learning_auto.add(unit_id)
                else:
                    self._learning_auto.discard(unit_id)
                self._learn_queue.append(unit_id)
                taken.append(unit_id)
            if taken and not (self._learn_thread and self._learn_thread.is_alive()):
                self._learn_thread = threading.Thread(
                    target=self._dispatch_learning, name="experience-learning",
                    daemon=True)
                self._learn_thread.start()
        return taken

    def auto_running(self) -> bool:
        return bool(self.auto_clock is not None and self.auto_clock.running())

    #: The projects the user removed, by id: never shown, learned or
    #: updated again - the record of them is gone with them.
    REMOVED_KEY = "experience_removed_units"

    def removed(self) -> Dict[str, Dict[str, Any]]:
        said = self.config.get(self.REMOVED_KEY) or {}
        return dict(said) if isinstance(said, dict) else {}

    def remove_unit(self, unit_id: str) -> Dict[str, Any]:
        """REMOVE A PROJECT, as the user asked (after confirming on the
        page): what was learned from it - its stages and cards - is deleted
        and found by no search again, and the project is not listed, learned
        or updated again. The natures, patterns and principles stay: they
        may rest on other projects too (ExperienceStore.forget_unit). Not
        while it is being learned: stopped first."""
        mine = self.learning_progress.get(unit_id) or {}
        if mine.get("running") or mine.get("queued"):
            return {"error": "it is being learned now - stop its learning first"}
        live, _retired, projects = self._work(retired=False)
        unit = next((u for u in self._shown_units(self.stages_writer(), live, projects)
                     if u["id"] == unit_id), None)
        if unit is None:
            return {"error": "no such project"}
        from .stages import unit_task_ids
        gone = self.store.forget_unit(unit_id, unit_task_ids(unit))
        removed = self.removed()
        removed[unit_id] = {"name": unit.get("name", ""), "kind": unit.get("kind", ""),
                            "at": datetime.now(timezone.utc).isoformat()}
        self.config.set(self.REMOVED_KEY, removed, internal=True)
        return {"removed": unit_id, "gone": gone}

    def stop_learning(self, unit_id: str) -> bool:
        mine = self.learning_progress.get(unit_id)
        if not mine:
            return False
        mine["stop"] = True
        with self._learn_lock:
            if unit_id in self._learn_queue:
                self._learn_queue.remove(unit_id)
                self.learning_progress.pop(unit_id, None)
        return True

    def _learnable(self, unit_id: str) -> bool:
        """A project or task of the user's - not one made for the agents,
        which holds nothing of theirs to learn."""
        if unit_id in self.removed():
            return False
        unit = self.full_unit(unit_id)
        return unit is not None and unit.get("kind") != "new project"

    def _dispatch_learning(self) -> None:
        """Start each waiting project as soon as there is room for it."""
        running: Dict[str, threading.Thread] = {}
        places: Dict[str, Any] = {}
        while True:
            with self._learn_lock:
                for unit_id, thread in list(running.items()):
                    if not thread.is_alive():
                        running.pop(unit_id)
                        if self.pool is not None:
                            self.pool.give_back(places.pop(unit_id, None))
                while self._learn_queue:
                    if self.pool is not None:
                        # A place in the one pool: as many projects at once
                        # as there is room for beside everything else.
                        first = self._learn_queue[0]
                        place = self.pool.try_take(
                            "auto learning" if first in self._learning_auto else "learning",
                            first, key=f"learning-queue-{id(self)}")
                        if place is None:
                            break
                        places[self._learn_queue[0]] = place
                    else:
                        allowed = (self.room.allowed(len(running))[0]
                                   if self.room is not None else 1)
                        if len(running) >= max(1, allowed):
                            break
                    unit_id = self._learn_queue.pop(0)
                    thread = threading.Thread(target=self._learn_one, args=(unit_id,),
                                              name=f"learning-{unit_id}", daemon=True)
                    running[unit_id] = thread
                    thread.start()
                if not self._learn_queue and self.pool is not None:
                    # Nothing left waiting: out of the pool's line.
                    self.pool.stop_waiting(f"learning-queue-{id(self)}")
                if not running and not self._learn_queue:
                    break
            time.sleep(0.5)
        # The natures it wrote are grouped by the state heartbeat's third
        # step, as every nature record is - not here. What it changes now
        # is when the patterns they are already in were last active.
        try:
            self.store.refresh_last_active(self.library)
        except Exception:        # pragma: no cover - worked out again later
            log.warning("could not work out when the patterns were active",
                        exc_info=True)

    def _learn_one(self, unit_id: str) -> None:
        """One project, learned: its folder read up to the cutoff, in parts,
        then its states, then what its stages show about the user. What it
        costs is added to its own account as it goes."""
        progress = self.learning_progress.setdefault(unit_id, {})
        progress.update(running=True, queued=False, step="starting", calls=0,
                        stages=0, began=time.time())
        # ALWAYS UP TO NOW. Where the last learning stopped - finished or cut
        # short - is kept in what was read: the states marked seen and how far
        # the folder was read. So an update carries on from there, to the
        # present; there is no resuming under an old cutoff.
        from datetime import datetime, timezone
        until = datetime.now(timezone.utc).isoformat()
        self.store.save_learning(unit_id, until=until, done=0)
        progress["until"] = until

        def going() -> bool:
            return not progress.get("stop") and progress["calls"] < self.LEARN_CALLS

        def spent(used: Dict[str, int], since: Dict[str, int]) -> None:
            delta = {k: int(used.get(k, 0)) - int(since.get(k, 0))
                     for k in ("calls", "tokens_in", "tokens_out")}
            since.update({k: int(used.get(k, 0)) for k in delta})
            progress["calls"] += delta["calls"]
            self.store.save_learning(unit_id, spent=delta)

        try:
            unit = self.full_unit(unit_id)
            if unit is None:
                raise ValueError("no such project or task")
            every, retired, projects = self._work(shown_only=False)
            writer = self.stages_writer(going)
            writer.until = until
            writer.follow_the_tasks(every, retired, projects)
            counted: Dict[str, int] = {}
            with self._unit_held(unit_id):
                progress["step"] = "reading its folder"
                for _part in range(30):
                    if not going():
                        break
                    said = self.workspace_pass(writer, unit, until=until)
                    spent(writer.used, counted)
                    if said is None:
                        break
                    progress["stages"] += int(said.get("stages") or 0)
                    if said.get("error") or not said.get("covered_until"):
                        break
                progress["step"] = "turning its states into stages"
                while going() and unit.get("tasks") and writer.pending(unit):
                    said = writer.extend(unit, budget=4)
                    spent(writer.used, counted)
                    progress["stages"] += int(said.get("stages") or 0)
                    if not said["calls"] or said.get("error"):
                        progress["error"] = said.get("error", "")
                        break
                if unit.get("kind") == "project" and going():
                    project = next((one for one in projects if one.id == unit_id), None)
                    if project is not None:
                        writer.joins([project])
                        spent(writer.used, counted)
            # What its stages written before natures came with them show.
            progress["step"] = "what its stages show about you"
            from .stages import unit_task_ids
            ids = set(unit_task_ids(unit))
            left = [s for s in self.store.stages_without_natures(500)
                    if s["task_id"] in ids]
            if left and going():
                from .stage_natures import StageNatures
                natures = StageNatures(self.db, self.library, self.models, self.scope,
                                       embedder=self.embedder)
                natures.language = self.language()
                natures.names = self._units_of_tasks
                natures.write(left, budget=max(1, self.LEARN_CALLS - progress["calls"]),
                              going=going)
                spent(natures.used, {})
            workspace, kept = self.workspace_of(unit, writer)
            caught_up = not (unit.get("tasks") and writer.pending(unit)) and (
                workspace is None or str((kept or {}).get("processed_until") or "") >= until)
            if caught_up and not progress.get("stop"):
                self.store.save_learning(unit_id, done=1, learned_at=until)
                progress["done"] = True
        except Exception as exc:     # pragma: no cover - reported, not raised
            log.exception("learning %s failed", unit_id)
            progress["error"] = str(exc)[:500]
        finally:
            progress.update(running=False, step="done" if progress.get("done")
                            else "stopped", seconds=round(time.time() - progress["began"], 1))

    def learning_of(self, unit_id: str) -> Dict[str, Any]:
        """A unit's learning, for its row: whether it has been learned, up to
        when, what it has cost over all its learning, and what is going on."""
        held = self.store.learning(unit_id)
        going = dict(self.learning_progress.get(unit_id) or {})
        going.pop("began", None)
        return {"learned": bool(held.get("learned_at")),
                "until": held.get("until", ""), "done": bool(held.get("done")),
                "learned_at": held.get("learned_at", ""),
                "calls": int(held.get("calls") or 0),
                "tokens": int(held.get("tokens_in") or 0) + int(held.get("tokens_out") or 0),
                "tokens_in": int(held.get("tokens_in") or 0),
                "tokens_out": int(held.get("tokens_out") or 0),
                "counted_since": held.get("counted_since", ""),
                "learnable": True, **going}


    def stop(self) -> None:
        """Every learning under way stopped, and the waiting ones dropped."""
        self.progress["running"] = False
        self._stopping = True
        for unit_id in list(self.learning_progress):
            self.stop_learning(unit_id)

    # -- for the panel ---------------------------------------------------------------
    def units(self) -> List[Dict[str, Any]]:
        live, _retired, projects = self._work(retired=False)
        writer = self.stages_writer()
        out = []
        for unit in self._shown_units(writer, live, projects):
            from .stages import unit_task_ids
            ids = unit_task_ids(unit)
            held = self.store.workspace(unit["id"]) or {}
            out.append({"id": unit["id"], "kind": unit["kind"],
                        # At work now, found from what the user is doing.
                        "active": bool(unit.get("active")),
                        # Its learning: learned or not, and what it has cost.
                        "learning": self.learning_of(unit["id"]),
                        # Updated by itself at the times chosen, or not.
                        "auto": (self.auto_clock is None
                                 or self.auto_clock.opted_in(unit["id"])),
                        "workspace": held.get("path", ""),
                        "workspace_origin": held.get("origin", ""),
                        # Every folder of a project the user made, and what
                        # they said it is for, in their own words.
                        "paths": [one for one in held.get("paths") or []
                                  if str(one).strip()] if held.get("path") else [],
                        "description": description_of(held),
                        "processed_until": held.get("processed_until", ""),
                        "name": unit["name"],
                        "stages": self.store.stage_count(ids),
                        "tasks": [{"id": task.id, "title": task_name(task)}
                                  for task in unit["tasks"]],
                        # When something last changed in it: the newest state
                        # of any of its tasks - or, a folder of the user's,
                        # how far it has been read.
                        "last_active": max([str(getattr(task, "last_seen", "") or "")
                                            for task in unit["tasks"]]
                                           + [str(held.get("processed_until") or "")]),
                        "pending": len(writer.pending(unit)) if unit.get("tasks") else 0})
        # THE MOST LATELY ACTIVE FIRST: what last changed is at the top; one
        # never active at the bottom, the most learned of those first.
        out.sort(key=lambda unit: (_moment_key(unit["last_active"]), unit["stages"]),
                 reverse=True)
        return out

    def unit(self, unit_id: str) -> Optional[Dict[str, Any]]:
        """One unit, as a run is told about it."""
        live, _retired, projects = self._work(retired=False)
        writer = self.stages_writer()
        found = next((u for u in self._units(writer, live, projects)
                      if u["id"] == unit_id), None)
        if found is None:
            return None
        return {k: v for k, v in found.items() if k != "tasks"}

    def full_unit(self, unit_id: str) -> Optional[Dict[str, Any]]:
        """One unit with its tasks - what its tree is read through."""
        live, _retired, projects = self._work(retired=False)
        writer = self.stages_writer()
        return next((u for u in self._units(writer, live, projects)
                     if u["id"] == unit_id), None)

    def graph(self, unit_id: str) -> Dict[str, Any]:
        live, _retired, projects = self._work(retired=False)
        writer = self.stages_writer()
        unit = next((u for u in self._shown_units(writer, live, projects)
                     if u["id"] == unit_id), None)
        if unit is None:
            return {"error": "no such unit"}
        from .stages import unit_task_ids
        ids = unit_task_ids(unit)
        # THE STAGES ALONE: what each showed of the user and what it is found
        # by are its card's, asked for when one is opened (`stage_detail`) -
        # every nature record is a file, and while a project is learned they
        # change, so each picture read them all again.
        stages = self.store.stages(ids)
        return {"unit": {k: v for k, v in unit.items() if k != "tasks"},
                "tasks": [{"id": task.id, "title": task_name(task)}
                          for task in unit["tasks"]],
                "cards": self.cards_of(writer, unit),
                "stages": stages,
                "edges": self.store.edges([s["id"] for s in stages])}

    def all_graphs(self) -> List[Dict[str, Any]]:
        """Every shown project and task's graph, for one picture of them all."""
        live, _retired, projects = self._work(retired=False)
        out = []
        writer = self.stages_writer()
        for unit in self._shown_units(writer, live, projects):
            from .stages import unit_task_ids
            ids = unit_task_ids(unit)
            stages = self.store.stages(ids)
            if not stages:
                continue
            out.append({"unit": {k: v for k, v in unit.items() if k != "tasks"},
                        "cards": self.cards_of(writer, unit), "stages": stages,
                        "edges": self.store.edges([s["id"] for s in stages])})
        return out

    def cards_of(self, writer: Any, unit: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The unit's cards in the order the tree is read - each after the
        card it is a piece of - with how deep it sits and what that level
        is called. Nothing is written for a unit that has no root yet: the
        page reading it is not a reason to make one."""
        from .stages import level_of
        root = self.store.root(unit["id"])
        if root is None:
            return []
        _root, cards = writer.tree(unit)
        children: Dict[str, List[Dict[str, Any]]] = {}
        for card in cards.values():
            if card["id"] != _root["id"]:
                children.setdefault(card["parent_id"], []).append(card)
        out: List[Dict[str, Any]] = []

        def walk(card: Dict[str, Any], depth: int) -> None:
            out.append(dict(card, depth=depth, level=level_of(depth)))
            for child in children.get(card["id"], []):
                walk(child, depth + 1)

        walk(_root, 0)
        return out

    def stage_detail(self, stage_id: str) -> Optional[Dict[str, Any]]:
        """One stage's card, when it is opened: the natures it showed - each
        with its kind and pattern - and what it is found by."""
        stage = self.store.stage(stage_id)
        if stage is None:
            return None
        said = self._with_natures([dict(stage)])[0]
        return {"id": stage_id, "natures": said.get("natures", []),
                "found_by": said.get("found_by", {"words": [], "places": []})}

    def _with_natures(self, stages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Each stage with the natures it showed - each its title, kind and
        the pattern it was grouped into - for the stage's card."""
        linked = self.store.natures_of_stages([s["id"] for s in stages])
        wanted = {one for ids in linked.values() for one in ids}
        known = getattr(self, "_nature_seen", None)
        if known is None:
            known = self._nature_seen = {}
        from .stage_natures import example_of
        if wanted - set(known):
            # Read once, then kept: the panel asks every few seconds and the
            # records are files.
            for record in nature_records(self.library):
                known[record.id] = {"id": record.id, "title": record.title,
                                    "summary": record.summary or "",
                                    "aspect": str((getattr(record, "metadata", None)
                                                   or {}).get("aspect") or ""),
                                    "example": example_of(record)}
        kinds = self.store.nature_kinds()
        grouped = {}
        for pattern in self.store.patterns("pattern"):
            for one in pattern["members"]:
                grouped.setdefault(one, pattern["statement"])
        # And what each is found by - the index's words and places.
        from .index import StageIndex, found_by
        found = found_by(StageIndex(self.db, self.scope), [s["id"] for s in stages])
        for stage in stages:
            stage["found_by"] = found.get(stage["id"], {"words": [], "places": []})
        for stage in stages:
            stage["natures"] = [
                {**known[one], "kind": kinds.get(one, ""),
                 "pattern": grouped.get(one, "")}
                for one in linked.get(stage["id"], []) if one in known]
        return stages

    def nature(self, record_id: str) -> Optional[Dict[str, Any]]:
        """One nature, whole, for when it is clicked: what it says, its
        example and where that can be opened, the pattern it was grouped
        into and the principle above it - each with when it was last
        active - and every lesson it is attached to."""
        from .stage_natures import example_of
        from .patterns import folder_records
        # The nature folder is the first the library looks in, so a record
        # found there is the one `get` would return - from the kept reading
        # (patterns.folder_records), not by opening the 5,000 files of every
        # folder one by one: six seconds a click, measured.
        natures = folder_records(self.library, "user_nature")
        record = next((one for one in natures if one.id == record_id), None)
        if record is None:
            record = self.library.get(record_id)
        if record is None:
            return None
        pattern = next((p for p in self.store.patterns("pattern")
                        if record_id in p["members"]), None)
        principle = next((p for p in self.store.patterns("principle")
                          if pattern is not None and pattern["id"] in p["members"]), None)
        holes = self.db.query(
            "SELECT s.id, s.kind, s.summary, s.task_id, s.card_id, s.began FROM "
            "stage_natures sn JOIN experience_stages s ON s.id = sn.stage_id "
            "WHERE sn.record_id = ? AND s.scope = ? ORDER BY s.began",
            (record_id, self.scope))
        meta = getattr(record, "metadata", None) or {}

        def brief(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
            return {} if row is None else {
                "id": row["id"], "statement": row["statement"],
                "last_active": str(row.get("last_active") or ""),
                "support": row["support"]}

        examples = self.nature_examples(record)
        titles = {one.id: one.title for one in natures}

        def named(one: str) -> Dict[str, str]:
            return {"id": one, "title": titles.get(one, "")}

        from .stage_natures import history_of
        def merged_names(entry: Dict[str, Any]) -> List[Dict[str, str]]:
            # A merged nature's file is gone; its history keeps it whole, so
            # its title is read from there.
            kept = {str(m.get("id")): str(m.get("title") or "")
                    for m in entry.get("merged") or [] if isinstance(m, dict)}
            return [{"id": one, "title": titles.get(one) or kept.get(str(one), "")}
                    for one in entry.get("merged_from") or []]

        history = [dict(one, merged_from=merged_names(one),
                        split_from=named(one["split_from"]) if one.get("split_from") else {})
                   for one in history_of(record)]
        return {"id": record.id, "title": record.title,
                "summary": record.summary or "", "body": record.body or "",
                "kind": self.store.nature_kinds().get(record_id, ""),
                "aspect": str(meta.get("aspect") or ""),
                "source": record.source, "example": example_of(record),
                "written_by": str(meta.get("written_by") or ""),
                "created_at": record.created_at.isoformat()
                if hasattr(record.created_at, "isoformat") else str(record.created_at),
                "updated_at": record.updated_at.isoformat()
                if hasattr(record.updated_at, "isoformat") else str(record.updated_at or ""),
                "pattern": brief(pattern), "principle": brief(principle),
                "lessons": [dict(row) for row in holes],
                # EVERY CASE IT WAS SEEN IN, newest first: each lesson it was
                # learned from or linked to, and each state linked to it.
                "examples": examples[:EXAMPLES_SHOWN],
                "examples_total": len(examples),
                # What it said before, newest first - [] when never rewritten.
                "history": history}

    def merge_same_natures(self, dry_run: bool = True, mode: str = "similarity",
                           patterns: List[str] = (), budget: int = 200,
                           sets: Optional[List[Dict[str, Any]]] = None,
                           backup: Any = None) -> Dict[str, Any]:
        """ONCE: the natures within each pattern that are the same trait,
        merged into one whose examples are all of theirs (nature_merge).

        A DRY RUN unless told otherwise: what would be merged, and nothing
        changed - kept, so that applying merges exactly what was shown.
        Applied, it merges the `sets` given, or the last dry run's."""
        from .nature_merge import NatureMerger
        merger = NatureMerger(self.db, self.library, self.models, self.scope,
                              embedder=self.embedder, language=self.language())
        if dry_run:
            began = time.time()
            said = merger.preview(mode=mode, patterns=patterns, budget=budget)
            self._merge_preview = said
            self._spent("natures merged", began, merger.used)
            return said
        if sets is None:
            sets = (getattr(self, "_merge_preview", None) or {}).get("sets")
        if not sets:
            return {"merged": 0, "sets": 0,
                    "error": "nothing to merge: run the dry run first"}
        said = merger.apply(sets, backup=Path(backup) if backup else None)
        # What was read of the natures and their patterns is read again.
        self._nature_seen = {}
        self._pattern_tree = None
        self._merge_preview = None
        return said

    def nature_examples(self, record: Any) -> List[Dict[str, Any]]:
        """Every case a nature was seen in, newest first.

        A case is each lesson (stage) it rests on - the ones its record
        names (metadata.stages) and the ones linked to it (stage_natures) -
        and each state linked to it (nature_links, from before natures were
        written from lessons). Each says what was done, in which project and
        task, when, what came of it, and where it can be opened. The cases
        the record itself keeps (metadata.examples - the user's own words, a
        short label and every place it touched) go with the lessons they
        were seen in; one kept with no lesson is a case of its own."""
        from .stage_natures import examples_of, stages_of
        kept = examples_of(record)
        by_stage: Dict[str, Dict[str, Any]] = {}
        loose: List[Dict[str, Any]] = []
        for one in kept:
            stage_id = str(one.get("stage") or "")
            if not stage_id:
                loose.append(one)
                continue
            mine = by_stage.setdefault(stage_id, {"said": "", "what": "",
                                                  "references": [], "at": ""})
            # Two cases from one lesson are shown as one, with both their
            # words and every place.
            mine["said"] = "\n".join(filter(None, [mine["said"], str(one.get("said") or "")]))
            mine["what"] = mine["what"] or str(one.get("what") or "")
            mine["references"] += list(one.get("references") or [])
            mine["at"] = mine["at"] or str(one.get("at") or "")
        named = stages_of(record)
        linked = [row["stage_id"] for row in self.db.query(
            "SELECT stage_id FROM stage_natures WHERE record_id = ?", (record.id,))]
        ids = list(dict.fromkeys(named + linked + list(by_stage)))
        rows: Dict[str, Dict[str, Any]] = {}
        for start in range(0, len(ids), 400):
            chunk = ids[start:start + 400]
            holes = ", ".join("?" for _ in chunk)
            for row in self.db.query(f"SELECT * FROM experience_stages WHERE id IN "
                                     f"({holes})", tuple(chunk)):
                rows[row["id"]] = self.store._stage(row)
        cards: Dict[str, Dict[str, Any]] = {}
        card_ids = [s["card_id"] for s in rows.values() if s.get("card_id")]
        for start in range(0, len(card_ids), 400):
            chunk = card_ids[start:start + 400]
            holes = ", ".join("?" for _ in chunk)
            for row in self.db.query(f"SELECT id, title, statement FROM "
                                     f"experience_cards WHERE id IN ({holes})",
                                     tuple(chunk)):
                cards[row["id"]] = dict(row)
        units = self._units_of_tasks([s["task_id"] for s in rows.values()])
        out: List[Dict[str, Any]] = []
        for stage_id in ids:
            stage = rows.get(stage_id)
            mine = by_stage.get(stage_id) or {}
            if stage is None:
                out.append({"source": "stage", "id": stage_id, "missing": True,
                            "title": "", "what": str(mine.get("what") or ""),
                            "said": str(mine.get("said") or ""),
                            "label": str(mine.get("what") or ""),
                            "summary": "", "kind": "", "project": "", "task": "",
                            "task_id": "", "began": str(mine.get("at") or ""),
                            "ended": "", "deliverable": "", "own_example": bool(mine),
                            "locations": _locations(mine.get("references") or [])})
                continue
            work = stage.get("work") or {}
            decision = work.get("decision") or {}
            output = work.get("output") or {}
            deliverable = stage.get("deliverable") or {}
            card = cards.get(stage.get("card_id") or "") or {}
            summary = str(stage.get("summary") or "")
            title = (card.get("title") or decision.get("about")
                     or re.split(r"(?<=[.!?。])\s", summary, maxsplit=1)[0])
            unit = units.get(stage["task_id"], {})
            out.append({
                "source": "stage", "id": stage_id, "missing": False,
                "kind": stage.get("kind") or "", "medium": stage.get("medium") or "",
                "title": str(title or "")[:240],
                "what": str(mine.get("what") or output.get("what")
                            or decision.get("chose") or "")[:800],
                # The user's own words in this case, and its short label.
                "said": str(mine.get("said") or ""),
                "label": str(mine.get("what") or ""),
                "summary": summary[:1200],
                "verdict": str((stage.get("evaluation") or {}).get("verdict") or ""),
                "project": unit.get("project", ""), "task": unit.get("task", ""),
                "task_id": stage["task_id"],
                "began": stage.get("began") or "", "ended": stage.get("ended") or "",
                "deliverable": str(deliverable.get("excerpt") or "")[:400]
                if isinstance(deliverable, dict) else "",
                "own_example": bool(mine),
                "locations": _locations(
                    list(mine.get("references") or [])
                    + list(work.get("references") or [])
                    + list((deliverable.get("references") or [])
                           if isinstance(deliverable, dict) else []))})
        # States linked to it - natures written before lessons were.
        states = [row["state_id"] for row in self.db.query(
            "SELECT state_id FROM nature_links WHERE record_id = ?", (record.id,))]
        for start in range(0, len(states), 400):
            chunk = states[start:start + 400]
            holes = ", ".join("?" for _ in chunk)
            try:
                found = self.db.query(f"SELECT id, title, detail, task_id, starts_at, "
                                      f"ends_at FROM states WHERE id IN ({holes})",
                                      tuple(chunk))
            except Exception:                 # pragma: no cover - an older database
                found = []
            units.update(self._units_of_tasks([row["task_id"] for row in found
                                               if row["task_id"]]))
            for row in found:
                unit = units.get(str(row["task_id"] or ""), {})
                out.append({"source": "state", "id": row["id"], "missing": False,
                            "kind": "state", "title": str(row["title"] or "")[:240],
                            "what": "", "said": "", "label": "",
                            "summary": str(row["detail"] or "")[:1200],
                            "project": unit.get("project", ""),
                            "task": unit.get("task", ""),
                            "task_id": str(row["task_id"] or ""),
                            "began": str(row["starts_at"] or ""),
                            "ended": str(row["ends_at"] or ""), "deliverable": "",
                            "own_example": False, "locations": []})
        # A case kept with no lesson - a record with no stage - is still one.
        written = record.created_at
        for number, one in enumerate(loose):
            out.append({"source": "record",
                        "id": record.id if not number else f"{record.id}#{number}",
                        "missing": False, "kind": "", "title": "",
                        "what": str(one.get("what") or ""),
                        "said": str(one.get("said") or ""),
                        "label": str(one.get("what") or ""),
                        "summary": "", "project": str(one.get("project") or ""),
                        "task": str(one.get("task") or ""), "task_id": "",
                        "began": str(one.get("at") or "") or (
                            written.isoformat() if hasattr(written, "isoformat")
                            else str(written)),
                        "ended": "", "deliverable": "", "own_example": True,
                        "locations": _locations(one.get("references") or [])})
        out.sort(key=lambda one: str(one.get("ended") or one.get("began") or ""),
                 reverse=True)
        return out

    def _units_of_tasks(self, task_ids: List[str]) -> Dict[str, Dict[str, str]]:
        """Each task's title and the project it is in - retired tasks too, a
        lesson is kept after its task has gone."""
        wanted = [one for one in dict.fromkeys(task_ids) if one]
        if not wanted:
            return {}
        out: Dict[str, Dict[str, str]] = {}
        try:
            from ..states.tasks import Kept, Projects
            tasks = {task.id: task for task in Kept(self.db, self.tasks_scope).by_ids(wanted)}
            projects = Projects(self.db, self.tasks_scope).all()
        except Exception:                     # pragma: no cover - a name, not a step
            log.debug("could not name the tasks of a nature's examples", exc_info=True)
            tasks, projects = {}, []
        project_of = {}
        for project in projects:
            for one in project.tasks or []:
                project_of.setdefault(one, project.name)
            if project.id in wanted:
                project_of.setdefault(project.id, project.name)
        for one in wanted:
            task = tasks.get(one)
            out[one] = {"task": str(((task.described or {}).get("title") if task else "")
                                    or ""),
                        "project": str(project_of.get(one) or "")}
        return out

    def pattern_tree(self) -> Dict[str, Any]:
        """The patterns and principles of each kind of nature, apart - read
        at most once a minute: the overview asks on every refresh, and the
        records are files."""
        held = getattr(self, "_pattern_tree", None)
        if held and time.time() - held[0] < 60:
            return held[1]
        said = self._pattern_tree_now()
        self._pattern_tree = (time.time(), said)
        return said

    def _pattern_tree_now(self) -> Dict[str, Any]:
        records = {r.id: r for r in nature_records(self.library)}
        kinds = self.store.nature_kinds()

        from .stage_natures import examples_of

        def pattern_row(p):
            return {**p, "members": [
                {"id": one, "title": records[one].title,
                 "written_by": (records[one].metadata or {}).get("written_by", ""),
                 # How many real cases it was seen in, for its row.
                 "examples": len(examples_of(records[one]))}
                for one in p["members"] if one in records]}

        stretches = self.store.active_stretches(self.library)

        def tree(kind: str) -> Dict[str, Any]:
            principles = [dict(pr, stretches=stretches.get(pr["id"], []))
                          for pr in self.store.patterns("principle", kind=kind)]
            patterns = {p["id"]: p for p in self.store.patterns("pattern",
                                                                 kind=kind)}
            placed = {one for pr in principles for one in pr["members"]}
            return {"kind": kind,
                    "records": sum(1 for r in records.values()
                                   if ((kind in kinds_of(r, kinds)) if kind
                                       else not kind_of(r, kinds))),
                    "principles": [{**pr, "patterns": [
                        pattern_row(patterns[one]) for one in pr["members"]
                        if one in patterns]} for pr in principles],
                    "unplaced": [pattern_row(p) for key, p in patterns.items()
                                 if key not in placed]}

        out = [tree(kind) for kind in NATURE_KINDS]
        if self.store.patterns(kind=""):
            out.append(tree(""))         # built before there were kinds
        return {"kinds": out, "records": len(records),
                "unsorted": sum(1 for r in records.values()
                                if not kind_of(r, kinds)),
                "links": self.store.link_count()}

    def standing(self) -> Dict[str, Any]:
        """Where the experience stands when nothing is being written - for
        the progress bars, so they are there from the start: how many
        pieces of work have states waiting to become stages, and for each
        kind of nature how many patterns have words. Worked out at most
        every twenty seconds."""
        held = getattr(self, "_standing", None)
        if held and time.time() - held[0] < 20:
            return dict(held[1])
        try:
            live, _retired, projects = self._work(retired=False)
            writer = self.stages_writer()
            waiting = sum(1 for unit in writer.units(live, projects)
                          if writer.pending(unit))
            said: Dict[str, Any] = {"units": waiting, "units_done": 0}
            # The open pass: its cutoff stays until every unit is caught up
            # to it, so one cut short is resumed from there.
            until = self._stage_pass().get("until", "")
            if until:
                said["pass_until"] = until
                ids = {unit["id"] for unit in writer.units(live, projects)}
                said["workspaces_behind"] = sum(
                    1 for one in self.store.workspaces(origin="detected")
                    if one["unit_id"] in ids
                    and str(one.get("processed_until") or "") < until)
            for kind in NATURE_KINDS:
                patterns = self.store.patterns("pattern", kind=kind)
                said[kind] = {"patterns": len(patterns),
                              "named": sum(1 for p in patterns
                                           if p["statement"]),
                              "principles": len(self.store.patterns(
                                  "principle", kind=kind))}
            if getattr(self, "unsorted_count", None):
                said["unsorted"] = self.unsorted_count
        except Exception:        # pragma: no cover - a read, not a step
            said = {}
        self._standing = (time.time(), said)
        return dict(said)

    def status(self) -> Dict[str, Any]:
        return {"stages": self.store.stage_count(),
                "patterns": len(self.store.patterns("pattern")),
                "principles": len(self.store.patterns("principle")),
                "links": self.store.link_count(),
                # What is being learned now, and what waits for room.
                "learning": sum(1 for one in self.learning_progress.values()
                                if one.get("running")),
                "waiting": sum(1 for one in self.learning_progress.values()
                               if one.get("queued"))}
