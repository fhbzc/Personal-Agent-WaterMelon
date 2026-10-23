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

TRIES. The same instruction may be tried again and again, each try in a
subfolder of its own - `try_1`, `try_2`, ... of the folder the user chose,
each with its three ways inside - and EACH TRY IS BLIND TO EVERY OTHER: a
way of one may not read any other try's folder or anything under it, nor
what Claude Code or Codex sessions started anywhere in those kept of
themselves (`~/.claude/projects/<the folder>`, `~/.codex/sessions`), so a
second try, or an evaluation the user holds in one try's folder, reaches
none of the others. Each try's ways are units of their own, with their own
runs, trees and versions: try 1 keeps the names projects had before tries
("<project>:<way>"), later ones are "<project>~<n>:<way>". A project made
before tries had its three ways in the folder itself: they are moved into
`try_1` the first time it is shown with none of them going, and its runs'
records follow them.

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

import json
import logging
import os
import re
import shutil
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


#: A try's subfolder of the project's folder: `try_1`, `try_2`, ...
TRY_FOLDER = "try_{n}"
_TRY_NAME = re.compile(r"^try_(\d+)$")
#: A project's reference for one of its tries after the first: "<id>~<n>".
TRY_MARK = "~"


def split_ref(ref: Any) -> Tuple[str, int]:
    """(project id, try) of a project's reference - try 1 for a bare id."""
    text = str(ref or "")
    if TRY_MARK in text:
        base, number = text.rsplit(TRY_MARK, 1)
        if number.isdigit() and int(number) >= 1:
            return base, int(number)
    return text, 1


def ref_of(project_id: str, try_n: int) -> str:
    """A try's reference: the bare id for try 1, as projects were named
    before tries, so the runs made then are try 1's."""
    return project_id if int(try_n) <= 1 else f"{project_id}{TRY_MARK}{int(try_n)}"


def try_folder(folder: Any, try_n: int) -> Path:
    return Path(folder) / TRY_FOLDER.format(n=int(try_n))


def tries_of(folder: Any) -> List[int]:
    """The tries a project's folder holds - the first, when it holds none
    (a try removed leaves the others as they are numbered)."""
    found = set()
    try:
        for entry in os.scandir(folder):
            matched = _TRY_NAME.match(entry.name)
            if matched and entry.is_dir(follow_symlinks=False):
                found.add(int(matched.group(1)))
    except OSError:
        pass
    return sorted(found) or [1]


def _tries_going_away(folder: Any) -> List[Path]:
    """Removed tries' folders, set aside and still being deleted: sealed as
    the tries they were."""
    try:
        return [Path(entry.path) for entry in os.scandir(folder)
                if entry.name.startswith(".try_") and ASIDE_MARK in entry.name
                and entry.is_dir(follow_symlinks=False)]
    except OSError:
        return []


def _legacy_ways(folder: Any) -> List[Path]:
    """A project's ways from before tries, still in its folder itself."""
    folder = Path(folder)
    return [one for variant in VARIANTS
            for one in (folder / VARIANT_FOLDERS[variant], folder / OLD_VARIANT_FOLDERS[variant])
            if one.is_dir() and not _is_link(str(one))]


def way_folder(folder: Any, try_n: int, variant: str) -> Path:
    """One way's folder in one try. Try 1's way still in the project's
    folder itself - from before tries, not moved yet - is where it is."""
    inside = variant_folder(try_folder(folder, try_n), variant)
    if int(try_n) == 1 and not inside.is_dir():
        legacy = variant_folder(folder, variant)
        if legacy.is_dir() and not _is_link(str(legacy)):
            return legacy
    return inside


def _claude_name(path: Any) -> str:
    """The name Claude Code gives the folder it keeps a working folder's
    sessions in: every character not a letter or digit made a hyphen."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(path))).lower()


_CWD = re.compile(r'"cwd"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _codex_cwd(path: Path) -> str:
    """Where a Codex session ran: said near the top of its file."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(16384)
    except OSError:
        return ""
    found = _CWD.search(head)
    if not found:
        return ""
    try:
        return json.loads(f'"{found.group(1)}"')
    except ValueError:
        return found.group(1)


def _code_root() -> Path:
    from ...common.paths import app_code_root
    return app_code_root()


def _around(place: Path, keep: Path) -> List[Path]:
    """`place` sealed whole - or, where `keep` (a way's own folder) is inside
    it, everything in it but the way down to `keep`."""
    place, keep = Path(place), Path(keep)
    if not _inside(keep, place):
        return [place]
    out: List[Path] = []
    at = place
    for part in keep.resolve().relative_to(place.resolve()).parts:
        try:
            out += [Path(one.path) for one in os.scandir(at) if one.name != part]
        except OSError:
            pass
        at = at / part
    return out


def _inside(path: Any, root: Any) -> bool:
    try:
        path, root = Path(path).resolve(), Path(root).resolve()
    except OSError:
        return False
    return path == root or root in path.parents


def sessions_under(roots: List[Path], home: Optional[Path] = None) -> List[str]:
    """WHAT ASSISTANTS KEPT OF THEMSELVES while working in any of `roots`:
    each folder Claude Code (the command line and the desktop app alike)
    keeps a working folder's sessions in, and each Codex session file that
    ran there - a try's own and every one of its subfolders'."""
    home = Path(home or Path.home())
    out: List[str] = []
    names = [_claude_name(root) for root in roots]
    projects = home / ".claude" / "projects"
    try:
        for entry in os.scandir(projects):
            low = entry.name.lower()
            if entry.is_dir() and any(low == name or low.startswith(name + "-")
                                      for name in names):
                out.append(entry.path)
    except OSError:
        pass
    codex = Path(os.environ.get("CODEX_HOME") or home / ".codex") / "sessions"
    if codex.is_dir():
        for session in codex.rglob("*.jsonl"):
            ran = _codex_cwd(session)
            if ran and any(_inside(ran, root) for root in roots):
                out.append(str(session))
    return out


def _extra(row: Dict[str, Any]) -> List[str]:
    """The folders the user named for a project to leave out, beside its own."""
    try:
        said = json.loads(row.get("leave_out") or "[]")
    except (TypeError, ValueError):
        return []
    return [str(one) for one in said if str(one or "").strip()] if isinstance(said, list) else []


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
    project_id, try_n = split_ref(parsed[0])
    try:
        rows = db.query("SELECT folder FROM debug_projects WHERE id = ?", (project_id,))
    except Exception:        # pragma: no cover - a database without the table
        return None
    if not rows:
        return None
    return way_folder(rows[0]["folder"], try_n, parsed[1])


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


def _writable_again(remove, path: str, _info: Any) -> None:
    """shutil.rmtree's second try at a read-only file: made writable."""
    try:
        os.chmod(path, stat.S_IWRITE)
        remove(path)
    except OSError:
        pass


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
        """A project's record, by its id or one of its tries' references."""
        rows = self.db.query("SELECT * FROM debug_projects WHERE id = ?",
                             (split_ref(project_id)[0],))
        return dict(rows[0]) if rows else None

    def _units_of(self, row: Dict[str, Any]) -> List[str]:
        """Every unit of every try of a project."""
        return [unit_id_of(ref_of(row["id"], n), variant)
                for n in tries_of(row["folder"]) for variant in VARIANTS]

    def sealed_for(self, row: Dict[str, Any], try_n: int, variant: str) -> List[str]:
        """WHAT ONE WAY OF ONE TRY MAY NOT READ, beyond what no run may: the
        other two ways of its try; every other try's folder whole; a way
        from before tries still in the project's folder, to every try but
        the first; the rest of its own try but the way down to its own
        folder (the user's notes on its ways); what Claude Code and Codex
        kept of their sessions in any try but the way's own - AND THE AGENT
        ITSELF: its data folder's workspace
        (what it learned of the user, as files; the way's own folder, if it
        is in there, left open), and the sessions Claude Code and Codex kept
        of work in the agent's code or its data. (The agent's code - the
        checkout, or an installed copy's whole folder - is shut by
        `app_code_root`.) Its experience a way reads through the agent's tools,
        never as files."""
        folder = Path(row["folder"])
        own = way_folder(folder, try_n, variant)
        sealed = [way_folder(folder, try_n, other) for other in VARIANTS if other != variant]
        others = [try_folder(folder, n) for n in tries_of(folder) if n != try_n]
        if try_n != 1:
            others += _legacy_ways(folder)
        roots = [one for one in others if one.exists()]
        sealed += roots + _tries_going_away(folder)
        # ITS OWN TRY, but the way down to its own folder: what the user wrote
        # at the top of a try - an evaluation of its ways, this try's too -
        # is their judgement, which a way being judged may not read and tune
        # itself to. (The top of the project's folder stays readable: it is
        # where the user puts what the work is to be done with.)
        here = try_folder(folder, try_n)
        if _inside(own, here):
            sealed += _around(here, own)
        agent = self._the_agents_own()
        sealed += [one for place in agent for one in _around(place, own)]
        # THE FOLDERS THE USER LEFT OUT of this project - the project it
        # redoes, say: no way reads them, nor what was kept of work in them.
        extra = [Path(one) for one in _extra(row)]
        sealed += [one for place in extra for one in _around(place, own)]
        own_kept = set(sessions_under([own]))
        sealed += [one for one in sessions_under(extra) if one not in own_kept]
        # And every session an assistant kept of work in any try - an
        # evaluation run at the top of one - but the way's own.
        mine = set(sessions_under([own]))
        tries = [try_folder(folder, n) for n in tries_of(folder)]
        theirs = [one for one in sessions_under(tries) if one not in mine]
        return list(dict.fromkeys(
            [str(one) for one in sealed] + sessions_under(roots) + theirs
            + sessions_under(agent + [_code_root()])))

    def _the_agents_own(self) -> List[Path]:
        """The agent's own folder a way may not read, sealed as the other
        tries are: its data folder's workspace. (The folder an installed copy
        runs from is its code root - `app_code_root` - refused by every file
        tool but not sealed from the code a way runs, which is that copy's
        own executable and must read it to start.)"""
        workspace = getattr(getattr(self.work, "app_policy", None), "workspace_root", None)
        return [Path(workspace)] if workspace and Path(workspace).exists() else []

    def _into_tries(self, row: Dict[str, Any]) -> None:
        """A project from before tries, its three ways moved into `try_1` -
        and its runs' records with them - when none of them is going. Once:
        then `try_1` is there. A way that cannot be moved now (a file of it
        open) stays where it is and is still try 1's (`way_folder`)."""
        folder = Path(row["folder"])
        legacy = _legacy_ways(folder)
        if not legacy:
            return
        units = [unit_id_of(row["id"], variant) for variant in VARIANTS]
        if any(run.alive() and run.unit.get("id") in units
               for run in list(self.work._running.values())):
            return
        first = try_folder(folder, 1)
        first.mkdir(exist_ok=True)
        for old in legacy:
            new = first / old.name
            if new.exists():
                continue
            try:
                os.rename(old, new)
            except OSError:
                log.warning("could not move %s into %s", old, first, exc_info=True)
                continue
            log.info("moved a debugger way into its first try: %s -> %s", old, new)
            for column in ("folder", "plan", "final"):
                self.db.execute(
                    f"UPDATE work_runs SET {column} = REPLACE({column}, ?, ?) "
                    f"WHERE unit_id IN ({', '.join('?' * len(units))})",
                    (str(old), str(new), *units))
            try:
                from ...runtime.isolation import forget_grants
                forget_grants(old)
            except Exception:    # pragma: no cover - granted again per run
                log.debug("could not forget the grants of %s", old, exc_info=True)

    def new_try(self, project_id: str) -> Dict[str, Any]:
        """A NEW TRY: the next `try_<n>` with its three ways, made the one
        shown - the same instruction, and blind to every other try."""
        row = self._row(project_id)
        if row is None:
            return {"error": "no such project"}
        self._into_tries(row)
        number = max(tries_of(row["folder"])) + 1
        made = []
        for variant in VARIANTS:
            sub = try_folder(row["folder"], number) / VARIANT_FOLDERS[variant]
            sub.mkdir(parents=True, exist_ok=True)
            made.append(str(sub))
        try:
            from ...runtime.isolation import seal_from_containers
            seal_from_containers(made + [str(try_folder(row["folder"], n))
                                         for n in tries_of(row["folder"])])
        except Exception:        # pragma: no cover - sealed again before every run
            log.warning("could not seal a new try's folders", exc_info=True)
        return self.use_try(row["id"], number)

    def use_try(self, project_id: str, number: Any) -> Dict[str, Any]:
        """Which try of a project is shown, and started from."""
        row = self._row(project_id)
        if row is None:
            return {"error": "no such project"}
        try:
            number = int(number)
        except (TypeError, ValueError):
            return {"error": "no such try"}
        if number not in tries_of(row["folder"]):
            return {"error": "no such try"}
        self.db.execute("UPDATE debug_projects SET current_try = ?, updated_at = ? WHERE id = ?",
                        (number, _now(), row["id"]))
        return {"project": self.project(ref_of(row["id"], number))}

    def unit(self, project_id: str, variant: str) -> Optional[Dict[str, Any]]:
        """One way of a project, as a run is told about it: the project's
        name and the user's description as they are - the same for all
        three, and saying nothing of which way it is - its way's folder, and
        the two folders sealed from it."""
        row = self._row(project_id)
        if row is None or variant not in VARIANTS:
            return None
        try_n = split_ref(project_id)[1]
        ref = ref_of(row["id"], try_n)
        own = way_folder(row["folder"], try_n, variant)
        return {"id": unit_id_of(ref, variant), "kind": "debug project",
                "name": row["name"], "description": row["description"],
                "why": "", "paths": [str(own)],
                "debug": {"project": ref, "variant": variant, "try": try_n,
                          "label": VARIANT_LABELS[variant],
                          "folder": str(own),
                          "sealed": self.sealed_for(row, try_n, variant),
                          "leave_out": self.leave_out_folders(row)}}

    def _shown(self, row: Dict[str, Any], try_n: Optional[int] = None) -> Dict[str, Any]:
        """A project as the page shows it: ONE TRY of it - the current one
        unless another is named - under that try's reference, so all the
        page does with it is done to that try; and which tries there are."""
        self._into_tries(row)
        folder = Path(row["folder"])
        tries = tries_of(folder)
        try_n = try_n if try_n in tries else max(
            [n for n in tries if n == int(row.get("current_try") or 0)] or [max(tries)])
        ref = ref_of(row["id"], try_n)
        variants = []
        for variant in VARIANTS:
            unit_id = unit_id_of(ref, variant)
            runs = self.db.query("SELECT id, status FROM work_runs WHERE unit_id = ? "
                                 "ORDER BY started_at DESC", (unit_id,))
            alive = any(run.alive() and run.unit.get("id") == unit_id
                        for run in list(self.work._running.values()))
            waiting = self.work.waiting_of(unit_id)
            variants.append({"variant": variant, "label": VARIANT_LABELS[variant],
                             "about": VARIANT_ABOUT[variant], "unit": unit_id,
                             "folder": str(way_folder(folder, try_n, variant)),
                             "runs": len(runs), "alive": alive or bool(waiting),
                             "waiting": waiting,
                             "status": runs[0]["status"] if runs else ""})
        return {"id": ref, "base": row["id"], "try": try_n, "tries": tries,
                "try_folder": str(try_folder(folder, try_n)),
                "name": row["name"], "description": row["description"],
                "instruction": row["instruction"], "folder": row["folder"],
                "leave_out": self.leave_out_folders(row),
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "variants": variants}

    # -- what its experience leaves out ------------------------------------------
    def leave_out_folders(self, row: Dict[str, Any]) -> List[str]:
        """THE FOLDERS NOTHING LEARNED IS TAKEN FROM for this project's full
        way (learning/leave_out.py): its own folder - what its tries made and
        what the user wrote of them there - and those the user named."""
        return list(dict.fromkeys([str(row["folder"])] + _extra(row)))

    def leave_out_view(self, project_id: str) -> Dict[str, Any]:
        """Each folder left out, with the sessions Claude Code and Codex kept
        of work in it, and how many of the cases learned that leaves out."""
        row = self._row(project_id)
        if row is None:
            return {"error": "no such project"}
        from ...learning.leave_out import LeaveOut
        from ...learning.store import LearningStore
        folders = []
        records: List[str] = []
        for one in self.leave_out_folders(row):
            kept = sessions_under([Path(one)])
            records += kept
            folders.append({"path": one, "own": one == str(row["folder"]),
                            "claude": [k for k in kept if not k.endswith(".jsonl")],
                            "codex": len([k for k in kept if k.endswith(".jsonl")])})
        cases, judgments = LeaveOut(self.leave_out_folders(row), records).parts_out(
            LearningStore(self.db))
        return {"folders": folders, "cases": len(cases), "judgments": len(judgments)}

    def set_leave_out(self, project_id: str, add: Any = None, remove: Any = None) -> Dict[str, Any]:
        """A folder named for the project to leave out - or no longer; never
        its own folder."""
        row = self._row(project_id)
        if row is None:
            return {"error": "no such project"}
        extra = _extra(row)
        if add:
            folder = Path(str(add)).expanduser()
            if not folder.is_dir():
                return {"error": f"no such folder: {add}"}
            if _inside(folder, row["folder"]):
                return {"error": "the project's own folder is left out already"}
            extra.append(str(folder.resolve()))
        if remove:
            extra = [one for one in extra if one != str(remove)]
        self.db.execute("UPDATE debug_projects SET leave_out = ?, updated_at = ? WHERE id = ?",
                        (json.dumps(list(dict.fromkeys(extra))), _now(), row["id"]))
        return self.leave_out_view(project_id)

    def projects(self) -> List[Dict[str, Any]]:
        return [self._shown(dict(row)) for row in self.db.query(
            "SELECT * FROM debug_projects ORDER BY created_at DESC")]

    def project(self, project_id: str) -> Optional[Dict[str, Any]]:
        row = self._row(project_id)
        if row is None:
            return None
        return self._shown(row, split_ref(project_id)[1] if TRY_MARK in str(project_id)
                           else None)

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
            sub = try_folder(chosen, 1) / VARIANT_FOLDERS[variant]
            sub.mkdir(parents=True, exist_ok=True)
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
        units = self._units_of(row)
        if any(run.alive() and run.unit.get("id") in units
               for run in list(self.work._running.values())):
            return {"error": "one of its ways is going: stop it first"}
        self._unwait(units)
        from ...heartbeat.experience.store import ExperienceStore
        for unit_id in units:
            try:
                ExperienceStore(self.db, work_scope(unit_id)).clear()
            except Exception:        # pragma: no cover - the rows go anyway
                log.debug("could not clear the tree of %s", unit_id, exc_info=True)
            self.db.execute("DELETE FROM work_run_experience WHERE run_id IN "
                            "(SELECT id FROM work_runs WHERE unit_id = ?)", (unit_id,))
            self.db.execute("DELETE FROM work_runs WHERE unit_id = ?", (unit_id,))
            for run_id in [one for one, run in list(self.work._running.items())
                           if run.unit.get("id") == unit_id]:
                self.work._running.pop(run_id, None)
        self.db.execute("DELETE FROM debug_projects WHERE id = ?", (row["id"],))
        return {"removed": row["id"]}

    def remove_try(self, project_id: str) -> Dict[str, Any]:
        """REMOVE A TRY, as the user confirmed: its folder and everything in
        it - what its three ways made, and whatever the user wrote at its
        top - deleted; its runs and its ways' trees forgotten; and the
        sessions Claude Code and Codex kept of work in it deleted too, so a
        try numbered as it was later is not handed them as its own. The
        other tries, and the project's own folder, as they are. Not the only
        try (Clear all empties that), nor while one of its ways is going."""
        row = self._row(project_id)
        if row is None:
            return {"error": "no such project"}
        self._into_tries(row)
        folder = Path(row["folder"])
        try_n = split_ref(project_id)[1]
        tries = tries_of(folder)
        if try_n not in tries:
            return {"error": "no such try"}
        self._unwait([unit_id_of(ref_of(row["id"], try_n), variant) for variant in VARIANTS])
        if len(tries) == 1:
            return {"error": "it is the only try - Clear all empties it instead"}
        ref = ref_of(row["id"], try_n)
        units = [unit_id_of(ref, variant) for variant in VARIANTS]
        if any(run.alive() and run.unit.get("id") in units
               for run in list(self.work._running.values())):
            return {"error": "a way of this try is going: stop it first"}
        here = try_folder(folder, try_n)
        counts: Dict[str, Any] = {"files": 0, "folders": 0, "links": 0, "runs": 0,
                                  "sessions": 0, "not_removed": [], "left_alone": []}
        with _CLEARING:
            sessions = sessions_under([here])
            if _is_link(str(here)):
                counts["left_alone"].append({"path": str(here),
                                             "why": "it is a link to another folder"})
            elif here.is_dir():
                count_folder(here, counts)
                # Set aside - one rename - and deleted in the background; or,
                # refused (a file of it open), emptied in place.
                aside = folder / f".{here.name}{ASIDE_MARK}{time.time_ns()}"
                try:
                    os.rename(here, aside)
                    _delete_in_background(aside)
                except OSError:
                    empty_folder(here, counts)
                    try:
                        _remove_one(str(here), os.rmdir)
                    except OSError as exc:
                        counts["not_removed"].append({"path": str(here),
                                                      "why": str(exc)[:300]})
                try:
                    from ...runtime.isolation import forget_grants
                    for one in [here] + [way_folder(folder, try_n, v) for v in VARIANTS]:
                        forget_grants(one)
                except Exception:        # pragma: no cover - granted again if asked
                    log.debug("could not forget a removed try's grants", exc_info=True)
            from ...heartbeat.experience.store import ExperienceStore
            for unit_id in units:
                try:
                    ExperienceStore(self.db, work_scope(unit_id)).clear()
                except Exception:        # pragma: no cover - the rows go anyway
                    log.debug("could not clear the tree of %s", unit_id, exc_info=True)
                counts["runs"] += len(self.db.query(
                    "SELECT id FROM work_runs WHERE unit_id = ?", (unit_id,)))
                self.db.execute("DELETE FROM work_run_experience WHERE run_id IN "
                                "(SELECT id FROM work_runs WHERE unit_id = ?)", (unit_id,))
                self.db.execute("DELETE FROM work_runs WHERE unit_id = ?", (unit_id,))
                for run_id in [one for one, run in list(self.work._running.items())
                               if run.unit.get("id") == unit_id]:
                    self.work._running.pop(run_id, None)
            for one in sessions:
                try:
                    if os.path.isdir(one) and not os.path.islink(one):
                        shutil.rmtree(one, onerror=_writable_again)
                    else:
                        os.unlink(one)
                    counts["sessions"] += 1
                except OSError as exc:
                    counts["not_removed"].append({"path": one, "why": str(exc)[:300]})
        left = [n for n in tries_of(folder) if n != try_n] or [1]
        shown = int(row.get("current_try") or 0)
        if shown not in left:
            shown = max(left)
            self.db.execute("UPDATE debug_projects SET current_try = ?, updated_at = ? "
                            "WHERE id = ?", (shown, _now(), row["id"]))
        return {"removed_try": try_n, "folder": str(here), **counts,
                "project": self.project(ref_of(row["id"], shown))}

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
        try_n = split_ref(project_id)[1]
        unit_id = unit_id_of(ref_of(row["id"], try_n), variant)
        if any(run.alive() and run.unit.get("id") == unit_id
               for run in list(self.work._running.values())):
            return {"error": "this way is going: stop it first"}
        self._unwait([unit_id])
        way = way_folder(row["folder"], try_n, variant)
        # The folder the way is in: its try's - or, from before tries, the
        # project's own.
        chosen = way.parent
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
        self.db.execute("DELETE FROM work_run_experience WHERE run_id IN "
                        "(SELECT id FROM work_runs WHERE unit_id = ?)", (unit_id,))
        self.db.execute("DELETE FROM work_runs WHERE unit_id = ?", (unit_id,))
        for run_id in [one for one, run in list(self.work._running.items())
                       if run.unit.get("id") == unit_id]:
            self.work._running.pop(run_id, None)
        return {"cleared": unit_id, "variant": variant, "folder": str(way),
                "emptied": emptied, **counts,
                "project": self.project(ref_of(row["id"], try_n))}

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
        ref = ref_of(row["id"], split_ref(project_id)[1])
        self._unwait([unit_id_of(ref, variant) for variant in VARIANTS])
        going = [variant for variant in VARIANTS
                 if any(run.alive() and run.unit.get("id") == unit_id_of(ref, variant)
                        for run in list(self.work._running.values()))]
        if going:
            return {"error": "a way is going (" + ", ".join(VARIANT_FOLDERS[one]
                                                            for one in going)
                             + "): stop it first"}
        with _CLEARING:
            ways = {variant: self._clear(ref, variant) for variant in VARIANTS}
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
            for variant, said in ways.items()}, "project": self.project(ref)}

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

    def _unwait(self, unit_ids: Any) -> None:
        for unit_id in unit_ids:
            self.work.unwait(unit_id)

    def waiting(self, project_id: str, variant: str) -> Dict[str, Any]:
        """A way waiting for room (runner.WorkRuns._room_for): why, since
        when, how far along - and, one that waited and then could not
        start, why not."""
        unit = self.unit(project_id, variant)
        if unit is None:
            return {}
        said = self.work.waiting_of(unit["id"]) or {}
        out = self.work.waited_out.get(unit["id"])
        return dict(said, **({"could_not_start": out} if out else {}))

    def unwait(self, project_id: str, variant: str) -> Dict[str, Any]:
        """A way waiting for room, not started after all."""
        unit = self.unit(project_id, variant)
        if unit is None:
            return {"error": "no such project or way"}
        self.work.waited_out.pop(unit["id"], None)
        return {"unwaited": self.work.unwait(unit["id"])}

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
        row = self._row(project_id)
        if row is None or variant not in VARIANTS:
            return []
        return self.work.runs(unit_id_of(ref_of(row["id"], split_ref(project_id)[1]),
                                         variant))
