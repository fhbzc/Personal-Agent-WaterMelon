"""THE EXPERIENCE DEBUGGER: the same piece of work done three ways, side by
side, to see what each part of the system is worth.

A debugger project is made by hand - never one of the projects the user is
working on, and never shown in Experience. It has a name, the user's
description, ONE INITIAL INSTRUCTION, and one folder the user chose. In that
folder three subfolders are made, one per way, and each way works in its own
and nowhere else:

  A - ONE MODEL ON ITS OWN: the instruction (and the
                    description) given once, the same tools the work agent
                    has to read, run code, search the web and write - and no
                    contract, no cards, no stages, no reviewer. It works
                    until it says it is done.
  B - BUILDER AND REVIEWER, the contract and the rounds as the
                    whole system has them, but the reviewer gets NOTHING of
                    the user's experience: no past stages, no natures, no
                    patterns or principles, no search of them.
  C - THE WHOLE SYSTEM, its reviewer READING the user's real
                    experience as it does for their own work.

All three start from the same words, so the comparison is fair. Each way is
started on its own, and once started they may run side by side - with each
other, and beside a run of the user's own work. By default nothing limits
them: no minutes, tokens or model calls (any may be set).

KEPT APART. Each way is a unit of its own ("<project>:<way>"), its tree in
its own "work:" scope, its runs its own rows: nothing it writes reaches the
user's learned experience, and nothing of the user's is looked up for it
except what the full way's reviewer reads. Each way's folder is SEALED from
the other two - it can neither write nor even read what they made (see
WorkspacePolicy.sealed_paths). Removing a project removes its records here,
never a file. CLEARING A WAY is the other way round: everything that way
wrote - inside its own folder and nowhere else - is deleted, with its runs,
and the other two ways and the project stay; it starts again from nothing.
"""

from __future__ import annotations

import logging
import os
import stat
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .runner import FULL, NO_CONTEXT, SINGLE, TOTAL_TOKENS, work_scope

log = logging.getLogger(__name__)

#: The three ways, in the order they are shown.
VARIANTS = (SINGLE, NO_CONTEXT, FULL)

#: EACH WAY'S SUBFOLDER of the project's folder - NAMED FOR NOTHING. Every
#: call tells a way where it may write, and a folder called "3_full" or
#: "1_single_model" told the model which way it was, and that it was being
#: compared: what the three are run to measure, changed by being measured.
#: Which letter is which way is said only on the page.
VARIANT_FOLDERS = {SINGLE: "A", NO_CONTEXT: "B", FULL: "C"}

#: The names the subfolders were first made with: a project made then keeps
#: working in them (its runs' records name them), and only its new ones are
#: made under the letters.
OLD_VARIANT_FOLDERS = {SINGLE: "1_single_model", NO_CONTEXT: "2_no_context",
                       FULL: "3_full"}


def variant_folder(folder: Any, variant: str) -> Path:
    """The way's subfolder of a project's folder: the letter - or the name
    it was first made with, when that is there and the letter is not."""
    folder = Path(folder)
    new = folder / VARIANT_FOLDERS[variant]
    old = folder / OLD_VARIANT_FOLDERS[variant]
    return old if old.is_dir() and not new.exists() else new

#: What each way is called on the page.
VARIANT_LABELS = {SINGLE: "Single model", NO_CONTEXT: "Builder + reviewer, no context",
                  FULL: "Full"}

#: What each way is, in a line.
VARIANT_ABOUT = {
    SINGLE: "one model with the same tools - no contract, no stages, no reviewer",
    NO_CONTEXT: "contract and rounds, but the reviewer gets none of your experience",
    FULL: "the whole system, its reviewer reading your experience",
}

#: A debugger project's id begins so - and a unit of one of its ways is
#: "<id>:<way>".
PREFIX = "dbg_"

#: No limit on anything: what a debugger run is started with by default.
#: Tokens in and out are limited apart, as a run of the user's own work's
#: are (see `runner.DEFAULT_LIMITS`).
NO_LIMITS = {"minutes": 0, "tokens_in": 0, "tokens_out": 0, "calls": 0}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def unit_id_of(project_id: str, variant: str) -> str:
    return f"{project_id}:{variant}"


def parse_unit(unit_id: str) -> Optional[Tuple[str, str]]:
    """(project, way) of a debugger unit; None for any other."""
    text = str(unit_id or "")
    if not text.startswith(PREFIX) or ":" not in text:
        return None
    project, variant = text.rsplit(":", 1)
    return (project, variant) if variant in VARIANTS else None


def is_debug_unit(unit_id: str) -> bool:
    return parse_unit(unit_id) is not None


def variant_folder_of(db: Any, unit_id: str) -> Optional[Path]:
    """The folder a debugger unit's runs work in - its way's subfolder of
    its project's folder; None for a unit that is not one, or whose project
    is gone."""
    parsed = parse_unit(unit_id)
    if parsed is None:
        return None
    try:
        rows = db.query("SELECT folder FROM debug_projects WHERE id = ?", (parsed[0],))
    except Exception:        # pragma: no cover - a database without the table
        return None
    if not rows:
        return None
    return variant_folder(rows[0]["folder"], parsed[1])


def limits_of(said: Any) -> Dict[str, int]:
    """A debugger run's limits: what the user gave - each left out, empty or
    zero or less is NO LIMIT. Unlike a run of the user's own work, nothing
    is limited by default."""
    said = said if isinstance(said, dict) else {}
    out = {}
    # One limit on tokens in and out together, from a caller of before:
    # kept as it was given.
    for name in (*NO_LIMITS, TOTAL_TOKENS):
        given = said.get(name)
        try:
            value = 0 if given in (None, "") else int(float(given))
        except (TypeError, ValueError):
            value = 0
        if name in NO_LIMITS or value > 0:
            out[name] = max(0, value)
    return out


def _is_link(path: str) -> bool:
    """A symbolic link, or a Windows junction - either a way into somewhere
    else, never followed when a folder is emptied."""
    try:
        if os.path.islink(path):
            return True
        isjunction = getattr(os.path, "isjunction", None)
        if isjunction is not None and isjunction(path):
            return True
        # A reparse point of another kind (a mount point, a cloud placeholder
        # of a folder) is not walked into either.
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)) \
            and os.path.isdir(path)
    except OSError:
        return False


def _entry_is_link(entry: "os.DirEntry") -> bool:
    """`_is_link`, of an entry of a listing - from what the listing already
    says of it, with no call to the system per entry: on twenty thousand
    files that call alone was five seconds of a clear."""
    try:
        if entry.is_symlink():
            return True
        is_junction = getattr(entry, "is_junction", None)
        if is_junction is not None and is_junction():
            return True
        attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)) \
            and entry.is_dir(follow_symlinks=False)
    except OSError:
        return False


def count_folder(folder: Path, counts: Dict[str, Any]) -> None:
    """What `empty_folder` would remove, counted - links as links, never
    walked into - and nothing removed."""
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return
    for entry in entries:
        if _entry_is_link(entry):
            counts["links"] += 1
        elif entry.is_dir(follow_symlinks=False):
            count_folder(Path(entry.path), counts)
            counts["folders"] += 1
        else:
            counts["files"] += 1


#: Folders set aside by a clear and deleted in the background (`clear`) -
#: and those being deleted now, each by one deleter only.
ASIDE_MARK = ".cleared-"
_deleting: List[threading.Thread] = []
_being_deleted: set = set()
_deleting_lock = threading.Lock()
#: One clear at a time (`DebugProjects.clear`) - the projects are made anew
#: for each request, so the lock is the module's.
_CLEARING = threading.RLock()


def _delete_aside(folder: Path) -> None:
    """A way's folder set aside by a clear, deleted - as `empty_folder`
    deletes, links as links - and then itself. What is already gone is
    not a failure."""
    try:
        counts: Dict[str, Any] = {"files": 0, "folders": 0, "links": 0, "not_removed": []}
        empty_folder(folder, counts)
        try:
            _remove_one(str(folder), os.rmdir)
        except FileNotFoundError:
            pass
        except OSError as exc:
            counts["not_removed"].append({"path": str(folder), "why": str(exc)[:300]})
        left = [one for one in counts["not_removed"]
                if os.path.lexists(str(one.get("path") or ""))]
        if left:
            log.warning("a cleared way's old files could not all be deleted: %s", left[:5])
    finally:
        with _deleting_lock:
            _being_deleted.discard(os.path.normcase(str(folder)))


def _delete_in_background(folder: Path) -> None:
    """Deleted on a thread of its own - unless one is deleting it already:
    a clear made while the last one's old files are still going does not
    set a second deleter on them."""
    key = os.path.normcase(str(folder))
    with _deleting_lock:
        if key in _being_deleted:
            return
        _being_deleted.add(key)
    thread = threading.Thread(target=_delete_aside, args=(folder,),
                              name="debugger-clear", daemon=True)
    _deleting.append(thread)
    thread.start()


def wait_deleted(timeout: float = 120.0) -> None:
    """Until the folders set aside by clears are deleted (for tests)."""
    for thread in list(_deleting):
        thread.join(timeout)
        if not thread.is_alive():
            _deleting.remove(thread)


def _writable(path: str) -> None:
    """A read-only file or folder made writable - so it can be deleted."""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD
                 | (stat.S_IEXEC if os.path.isdir(path) else 0))
    except OSError:
        pass


def _remove_one(path: str, remove) -> None:
    """Remove one entry; read-only, it is made writable and tried again."""
    try:
        remove(path)
    except PermissionError:
        _writable(path)
        remove(path)


def _unlink_link(path: str) -> None:
    """THE LINK ITSELF removed - never what it points to: a link to a file
    is unlinked; a link to a folder, or a junction, is removed as an empty
    folder is, which takes the link away and leaves its target as it is.
    Nothing is made writable first: on a link that could reach its target."""
    try:
        os.unlink(path)
    except OSError:
        os.rmdir(path)


def empty_folder(folder: Path, counts: Dict[str, Any]) -> None:
    """Everything INSIDE `folder` deleted - files, folders, links - and the
    folder itself kept. Links and junctions are removed as links: what they
    lead to, wherever it is, is never walked into or touched. What cannot
    be deleted is listed in counts["not_removed"], and the rest goes on."""
    try:
        entries = list(os.scandir(folder))
    except OSError as exc:
        counts["not_removed"].append({"path": str(folder), "why": str(exc)[:300]})
        return
    for entry in entries:
        path = entry.path
        try:
            if _entry_is_link(entry):
                _unlink_link(path)
                counts["links"] += 1
            elif entry.is_dir(follow_symlinks=False):
                empty_folder(Path(path), counts)
                _remove_one(path, os.rmdir)
                counts["folders"] += 1
            else:
                _remove_one(path, os.unlink)
                counts["files"] += 1
        except OSError as exc:
            counts["not_removed"].append({"path": path, "why": str(exc)[:300]})


class DebugProjects:
    """The Experience debugger's projects, and their three ways' runs -
    started, continued and listed through the work runs (`WorkRuns`)."""

    def __init__(self, work: Any) -> None:
        self.work = work
        self.db = work.db

    # -- the record ------------------------------------------------------------
    def _row(self, project_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.query("SELECT * FROM debug_projects WHERE id = ?",
                             (str(project_id or ""),))
        return dict(rows[0]) if rows else None

    def unit(self, project_id: str, variant: str) -> Optional[Dict[str, Any]]:
        """One way of a project, as a run is told about it: the project's
        name and the user's description as they are - the same for all
        three, and saying nothing of which way it is - its way's folder, and
        the two folders sealed from it."""
        row = self._row(project_id)
        if row is None or variant not in VARIANTS:
            return None
        folder = Path(row["folder"])
        return {"id": unit_id_of(row["id"], variant), "kind": "debug project",
                "name": row["name"], "description": row["description"],
                "why": "", "paths": [str(variant_folder(folder, variant))],
                "debug": {"project": row["id"], "variant": variant,
                          "label": VARIANT_LABELS[variant],
                          "folder": str(variant_folder(folder, variant)),
                          "sealed": [str(variant_folder(folder, other))
                                     for other in VARIANTS if other != variant]}}

    def _shown(self, row: Dict[str, Any]) -> Dict[str, Any]:
        folder = Path(row["folder"])
        variants = []
        for variant in VARIANTS:
            unit_id = unit_id_of(row["id"], variant)
            runs = self.db.query("SELECT id, status FROM work_runs WHERE unit_id = ? "
                                 "ORDER BY started_at DESC", (unit_id,))
            alive = any(run.alive() and run.unit.get("id") == unit_id
                        for run in list(self.work._running.values()))
            variants.append({"variant": variant, "label": VARIANT_LABELS[variant],
                             "about": VARIANT_ABOUT[variant], "unit": unit_id,
                             "folder": str(variant_folder(folder, variant)),
                             "runs": len(runs), "alive": alive,
                             "status": runs[0]["status"] if runs else ""})
        return {"id": row["id"], "name": row["name"], "description": row["description"],
                "instruction": row["instruction"], "folder": row["folder"],
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "variants": variants}

    def projects(self) -> List[Dict[str, Any]]:
        return [self._shown(dict(row)) for row in self.db.query(
            "SELECT * FROM debug_projects ORDER BY created_at DESC")]

    def project(self, project_id: str) -> Optional[Dict[str, Any]]:
        row = self._row(project_id)
        return self._shown(row) if row else None

    # -- making, changing, removing -------------------------------------------------
    def folder_problem(self, path: str, but: str = "") -> Dict[str, Any]:
        """{"folder": resolved} for a folder a project may be made in, or
        {"error": why not}: what a granted folder must be - not a whole
        drive, not the home folder itself, not the application's own state
        - and not the user's own material in the workspace (read only to the
        agents), nor inside or around another debugger project's folder, nor
        the application's own code or inside it: the ways may not read the
        code (WorkspacePolicy.code_root), and a way that worked in it would
        have to."""
        said = self.work.writable_folder(path)
        if said.get("error"):
            return said
        folder: Path = said["folder"]
        code = getattr(self.work, "code_root", None)
        if code is not None:
            code = Path(code).resolve()
            if folder == code or code in folder.parents:
                return {"error": "that is this application's own code, which the "
                                 "debugger's ways may not read - choose a folder "
                                 "outside it"}
        policy = self.work.app_policy
        for layer in list(getattr(policy, "readonly_paths", None) or []):
            layer = Path(layer).resolve()
            if folder == layer or layer in folder.parents:
                return {"error": "that holds your own material in the workspace, which "
                                 "the agents may only read - choose another folder"}
        work_root = (Path(policy.workspace_root) / "agent_work").resolve()
        if folder == work_root or work_root in folder.parents:
            return {"error": "that is inside the agents' own work folder - choose "
                             "another folder"}
        for row in self.db.query("SELECT id, folder FROM debug_projects"):
            if row["id"] == but:
                continue
            other = Path(row["folder"]).resolve()
            if folder == other or other in folder.parents or folder in other.parents:
                return {"error": "another debugger project works in that folder - "
                                 "choose one of its own"}
        return {"folder": folder}

    def create(self, name: str, description: str, instruction: str,
               folder: str) -> Dict[str, Any]:
        """A project, made: its record, and the three subfolders in the folder
        the user chose (those already there are used as they are) - each
        sealed from the other ways' code. No instruction is needed yet: it
        is given when a way is started (see `start`)."""
        name = str(name or "").strip()
        instruction = str(instruction or "").strip()
        if not name:
            return {"error": "give the project a name"}
        said = self.folder_problem(folder)
        if said.get("error"):
            return said
        chosen: Path = said["folder"]
        made = []
        for variant in VARIANTS:
            sub = chosen / VARIANT_FOLDERS[variant]
            sub.mkdir(exist_ok=True)
            made.append(str(sub))
        try:
            from ...runtime.isolation import seal_from_containers
            seal_from_containers(made)
        except Exception:        # pragma: no cover - sealed again before every run
            log.warning("could not seal the folders of a debugger project", exc_info=True)
        project_id = f"{PREFIX}{uuid.uuid4().hex[:12]}"
        stamp = _now()
        self.db.execute("INSERT INTO debug_projects (id, name, description, instruction, "
                        "folder, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (project_id, name[:300], str(description or "").strip()[:4000],
                         instruction[:8000], str(chosen), stamp, stamp))
        return {"project": self.project(project_id), "folders": made}

    def update(self, project_id: str, name: Any = None, description: Any = None,
               instruction: Any = None) -> Dict[str, Any]:
        """Its name, description or instruction, changed - for the runs
        started after; those already made keep what they were started with."""
        row = self._row(project_id)
        if row is None:
            return {"error": "no such project"}
        fields: Dict[str, Any] = {}
        if name is not None:
            if not str(name).strip():
                return {"error": "a project needs a name"}
            fields["name"] = str(name).strip()[:300]
        if description is not None:
            fields["description"] = str(description).strip()[:4000]
        if instruction is not None:
            fields["instruction"] = str(instruction).strip()[:8000]
        if fields:
            sets = ", ".join(f"{key} = ?" for key in fields)
            self.db.execute(f"UPDATE debug_projects SET {sets}, updated_at = ? WHERE id = ?",
                            (*fields.values(), _now(), row["id"]))
        return {"project": self.project(row["id"])}

    def remove(self, project_id: str) -> Dict[str, Any]:
        """Its record gone - its runs' rows and its ways' trees too - and
        NOTHING ON DISK: what the three ways made stays in their folders.
        Not while one of them is going."""
        row = self._row(project_id)
        if row is None:
            return {"error": "no such project"}
        units = [unit_id_of(row["id"], variant) for variant in VARIANTS]
        if any(run.alive() and run.unit.get("id") in units
               for run in list(self.work._running.values())):
            return {"error": "one of its ways is going: stop it first"}
        from ...heartbeat.experience.store import ExperienceStore
        for unit_id in units:
            try:
                ExperienceStore(self.db, work_scope(unit_id)).clear()
            except Exception:        # pragma: no cover - the rows go anyway
                log.debug("could not clear the tree of %s", unit_id, exc_info=True)
            self.db.execute("DELETE FROM work_runs WHERE unit_id = ?", (unit_id,))
            for run_id in [one for one, run in list(self.work._running.items())
                           if run.unit.get("id") == unit_id]:
                self.work._running.pop(run_id, None)
        self.db.execute("DELETE FROM debug_projects WHERE id = ?", (row["id"],))
        return {"removed": row["id"]}

    def clear(self, project_id: str, variant: str) -> Dict[str, Any]:
        """CLEAR ONE WAY: everything it wrote, gone - and the other two ways
        and the project stay as they are; this way starts again from nothing.

        In the way's folder (`1_single_model`, `2_no_context` or `3_full`)
        everything INSIDE it is deleted - the work's files, its runs and
        their versions, its packages, its run logs - and the folder itself
        is kept, sealed from the other two again as it was made. NOTHING
        ELSE is touched: not the other two ways' folders, nor the folder the
        user chose, which may hold their own files - the ways' input. A link
        or junction inside is removed as a link - what it points to is never
        walked into. A folder that is itself a link is left alone, said so.

        Then the way's records: its runs' rows, and its tree (as removing
        the project clears them). Not while THIS way is going - the others
        may be. What was removed, counted."""
        # ONE CLEAR AT A TIME: a second asked for while the first goes - a
        # second click, Clear all over a Clear - waits for it, and then
        # clears what is left (nothing), rather than racing it for the
        # folder.
        with _CLEARING:
            return self._clear(project_id, variant)

    def _clear(self, project_id: str, variant: str) -> Dict[str, Any]:
        row = self._row(project_id)
        if row is None:
            return {"error": "no such project"}
        if variant not in VARIANTS:
            return {"error": f"no such way: {variant}"}
        unit_id = unit_id_of(row["id"], variant)
        if any(run.alive() and run.unit.get("id") == unit_id
               for run in list(self.work._running.values())):
            return {"error": "this way is going: stop it first"}
        chosen = Path(row["folder"])
        way = chosen / VARIANT_FOLDERS[variant]
        counts: Dict[str, Any] = {"files": 0, "folders": 0, "links": 0, "runs": 0,
                                  "not_removed": [], "left_alone": []}
        emptied = ""
        if _is_link(str(way)):
            # Its folder leads somewhere else: what is there is not this
            # project's to delete.
            counts["left_alone"].append({"path": str(way),
                                         "why": "it is a link to another folder"})
        elif way.is_dir():
            try:
                inside = way.resolve().parent == chosen.resolve()
            except OSError:
                inside = False
            if inside:
                # What it held, counted - then, where the folder's seal goes
                # with it (an AppContainer's: on the folder itself), the
                # folder SET ASIDE - one rename - a fresh one made in its
                # place, and the old deleted in the background: a way with
                # its packages installed is tens of thousands of files, and
                # Windows takes seconds a thousand to delete them. Elsewhere
                # a seal is by path - the folder set aside would be open to
                # the other ways while it went - so it is emptied in place.
                for one in chosen.glob(f".{way.name}{ASIDE_MARK}*"):
                    if one.is_dir() and not _is_link(str(one)):
                        _delete_in_background(one)   # left by one cut short
                count_folder(way, counts)
                aside = self._set_aside(way)
                if aside is None:
                    counts.update(files=0, folders=0, links=0)
                    empty_folder(way, counts)
                else:
                    _delete_in_background(aside)
                emptied = str(way)
            else:
                counts["left_alone"].append({"path": str(way),
                                             "why": "it is not in the project's folder"})
        if emptied:
            try:
                from ...runtime.isolation import seal_from_containers
                seal_from_containers([emptied])
            except Exception:        # pragma: no cover - sealed again before every run
                log.warning("could not seal a debugger way's folder", exc_info=True)
        from ...heartbeat.experience.store import ExperienceStore
        try:
            ExperienceStore(self.db, work_scope(unit_id)).clear()
        except Exception:        # pragma: no cover - the rows go anyway
            log.debug("could not clear the tree of %s", unit_id, exc_info=True)
        counts["runs"] = len(self.db.query(
            "SELECT id FROM work_runs WHERE unit_id = ?", (unit_id,)))
        self.db.execute("DELETE FROM work_runs WHERE unit_id = ?", (unit_id,))
        for run_id in [one for one, run in list(self.work._running.items())
                       if run.unit.get("id") == unit_id]:
            self.work._running.pop(run_id, None)
        return {"cleared": unit_id, "variant": variant, "folder": str(way),
                "emptied": emptied, **counts, "project": self.project(row["id"])}

    def clear_all(self, project_id: str) -> Dict[str, Any]:
        """CLEAR ALL: the three ways cleared, each as `clear` clears one -
        their folders emptied and kept, their runs and trees forgotten; the
        project, and the user's own files beside the three, stay. All or
        none: not while any way is going, so a project is never left with
        two ways cleared and one not. What was removed, added up, with each
        way's own report."""
        row = self._row(project_id)
        if row is None:
            return {"error": "no such project"}
        going = [variant for variant in VARIANTS
                 if any(run.alive() and run.unit.get("id") == unit_id_of(row["id"], variant)
                        for run in list(self.work._running.values()))]
        if going:
            return {"error": "a way is going (" + ", ".join(VARIANT_FOLDERS[one]
                                                            for one in going)
                             + "): stop it first"}
        with _CLEARING:
            ways = {variant: self._clear(project_id, variant) for variant in VARIANTS}
        failed = {variant: said["error"] for variant, said in ways.items()
                  if said.get("error")}
        if failed:
            return {"error": "; ".join(f"{VARIANT_FOLDERS[variant]}: {why}"
                                       for variant, why in failed.items())}
        total: Dict[str, Any] = {key: sum(int(said.get(key) or 0) for said in ways.values())
                                 for key in ("files", "folders", "links", "runs")}
        for key in ("not_removed", "left_alone"):
            total[key] = [one for said in ways.values() for one in said.get(key) or []]
        return {"cleared_all": row["id"], **total, "ways": {
            variant: {key: value for key, value in said.items() if key != "project"}
            for variant, said in ways.items()}, "project": self.project(row["id"])}

    @staticmethod
    def _set_aside(way: Path) -> Optional[Path]:
        """The way's folder renamed aside, sealed as it was, and a fresh one
        in its place - or None, where that is not how its seal works or the
        rename was refused (a file of it open, say)."""
        try:
            from ...runtime.isolation import isolation_status
            status = isolation_status()
        except Exception:                 # pragma: no cover - then in place
            return None
        if not status.get("isolated") or status.get("backend") != "appcontainer":
            return None
        aside = way.parent / f".{way.name}{ASIDE_MARK}{time.time_ns()}"
        try:
            os.rename(way, aside)
        except OSError:
            return None
        try:
            try:
                way.mkdir()
            except FileExistsError:
                # Made again meanwhile - by a run starting: the old one is
                # set aside all the same.
                if not way.is_dir():
                    raise
            # A new folder in the old one's place: granted to the next run
            # afresh, not taken to be granted already.
            from ...runtime.isolation import forget_grants
            forget_grants(way)
        except OSError:                   # pragma: no cover - put back
            try:
                os.rename(aside, way)
            except OSError:
                log.warning("a cleared way's folder could not be made again", exc_info=True)
            return None
        return aside

    # -- its runs ---------------------------------------------------------------
    def _ready(self, project_id: str, variant: str) -> Tuple[Optional[Dict[str, Any]], str]:
        unit = self.unit(project_id, variant)
        if unit is None:
            return None, "no such project or way"
        folder = Path(unit["debug"]["folder"])
        if not folder.parent.is_dir():
            return None, f"the project's folder is gone: {folder.parent}"
        folder.mkdir(exist_ok=True)
        return unit, ""

    def start(self, project_id: str, variant: str, limits: Any = None,
              packages: Any = None, instruction: Any = None,
              sound: Any = False) -> Dict[str, Any]:
        """One way, started - with the instruction given as it starts, with no
        limit unless one is given. Without one, the instruction a way of
        this project was last started with. The words a way starts with are
        kept as the project's last instruction, so the next way is offered
        the same - a fair comparison starts all three from one set of words."""
        unit, problem = self._ready(project_id, variant)
        if unit is None:
            return {"error": problem}
        row = self._row(project_id)
        words = str(instruction or "").strip()[:8000] or str(row["instruction"] or "").strip()
        if not words:
            return {"error": "say what you want done"}
        # Packages install without asking unless "ask" is chosen: a way being
        # measured is not held up for the user (the debugger's default).
        said = self.work.start(unit, words, limits=limits_of(limits),
                               packages=packages or "auto", sound=sound)
        if not said.get("error") and words != row["instruction"]:
            self.db.execute("UPDATE debug_projects SET instruction = ?, updated_at = ? "
                            "WHERE id = ?", (words, _now(), row["id"]))
        return dict(said, unit=unit["id"], variant=variant) if not said.get("error") else said

    def continue_run(self, project_id: str, variant: str, run_id: str,
                     words: str = "", from_version: str = "", limits: Any = None,
                     packages: Any = None, sound: Any = False) -> Dict[str, Any]:
        """One way's run, carried on - with the user's words, from a version,
        with new limits (none unless given) - as any run is (see
        `WorkRuns.continue_run`)."""
        unit, problem = self._ready(project_id, variant)
        if unit is None:
            return {"error": problem}
        return self.work.continue_run(run_id, unit, limits=limits_of(limits),
                                      packages=packages, from_version=from_version,
                                      instruction=words, sound=sound)

    def runs(self, project_id: str, variant: str) -> List[Dict[str, Any]]:
        """One way's runs, newest first - each as the work panel draws a
        run."""
        if self._row(project_id) is None or variant not in VARIANTS:
            return []
        return self.work.runs(unit_id_of(project_id, variant))
