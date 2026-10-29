"""Versions of a run: what it made and where it stood, saved at each key
point, so the user can continue it from any of them.

A VERSION is saved when the contract is approved, after each round the
reviewer finds advanced (and each full review), when the final review is
done, when the deck is made, and when the run pauses, stops or finishes. It
holds:

  EVERY FILE OF THE WORK'S FOLDER - the deliverables, and this run's own
  records (its contract, to-do list, deck and report) - but not the
  version store itself, the run's installed packages, the project's run
  logs (`run_logs/`: the transcripts and live sessions), the runtime's
  scratch, or other runs' records.

  THE FILES THE AGENT WROTE IN A GRANTED FOLDER, and only those: the user's
  own files there are theirs, never taken into a version - and never put
  back over their changes either (see `restore_granted`).

  WHERE THE RUN STOOD: its plan (the contract, the rounds, the findings, the
  to-do list, the decisions, the deck, the limits and sessions), and the
  work's tree of cards and stages as rows of the database - enough to
  continue from exactly there.

THE STORE is content-addressed and plain Python - no git, no other program:
each file's bytes are kept once under their SHA-256, however many versions
hold them, and each database row likewise, so a version costs only what
changed since the one before. It is kept INSIDE THE RUN'S FOLDER
(`runs/<run>/.versions`) and writes nowhere else - every path it writes is
checked against the work's policy, as the agent's own writes are.

CONTINUING FROM AN OLDER VERSION ROLLS BACK: the work's folder becomes
exactly that version's files, and the plan and the tree become its state.
Nothing after it is lost - the versions saved after it stay, and the run
goes on as a new BRANCH from it, so the user can go back to either line.

A RESUME POINT (the user, 2026-10-24) is saved after every round the
reviewer finds advanced: the same files and state a version holds, so that
a run cut off - the application closed - goes on from there and loses at
most the round it was in. It is not a version: no slides, not listed among
the versions, not a checkpoint the full review counts from. Only the latest
of a line is kept, and only while it is after that line's head; a version
saved since leaves it behind. What no version and no resume point holds any
more is removed from the store (`collect`) - the work's own files are never
touched by that.

CONTINUED WITH NEW WORDS, the contract is amended for that line (see
`runner.Run._amend`): the version saved when the amendment is approved
carries, on its entry, the words and what changed in the contract. The
amended contract lives in the plan, so only the versions after it hold it -
the other line keeps its own.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .environments import is_environment

#: Inside a run's folder: where its versions are kept.
VERSIONS_DIR = ".versions"

#: At the top of a project's work folder: THE PROJECT'S RUN LOGS - a folder
#: per run, named by when it started, holding its full transcript and its
#: live session (see `transcript.py`). The record of what happened: never
#: part of a version, so a rollback - which removes every file the version
#: does not hold - can never remove or change a log. Defined here, where
#: the versions leave it out, so the tools and the runner take it from one
#: place without importing each other.
LOGS_DIR = "run_logs"

#: Folders that are never part of a version, wherever they are: the
#: runtime's scratch and Python's caches - not the work, and remade at will;
#: so are npm's packages (node_modules, installed again from package.json)
#: and the package downloads kept in a debugger way's folder.
SKIP_DIRS = (".agent_runtime", "__pycache__", "node_modules", ".npm-cache", ".pip-cache")

#: Inside a run's folder: where each version's slides are drawn - a folder
#: per version (`slides/v3/fig_progress.png`). Not the work: its figures are
#: kept with the version's slides instead (in the version's state, their
#: bytes in this store), so a past version shows the slides it had, and a
#: rollback neither restores nor removes them.
SLIDES_DIR = "slides"

#: Inside the run's own folder, also left out: the versions themselves, the
#: installed packages (thousands of files, and installed again on need), the
#: live session of a run from before the project's logs had a folder of
#: their own (LOGS_DIR) - the record of what happened, which a rollback must
#: not undo any more than it undoes the log - and the versions' slides.
SKIP_IN_RUN = (VERSIONS_DIR, ".packages", ".session", SLIDES_DIR)

#: The most files one version holds, and the largest file it keeps. A file
#: over the size is listed as skipped: a rollback neither restores nor
#: removes it.
MOST_FILES = 20000
MOST_BYTES = 256 * 1024 * 1024

#: The first line of versions.
MAIN = "main"

#: Inside the store: the latest resume point of each line.
RESUME_FILE = "resume.json"
#: An object newer than this is never removed by `collect`: one just written
#: - for a version being saved, its slides being drawn - may not be held by
#: any version yet.
COLLECT_GRACE_SECONDS = 1800
#: A resume point saved collects what nothing holds at most this often: a
#: progress point every ten minutes is a walk of the folder and its changed
#: files, not a sweep of the store each time (measured 2026-10-27).
COLLECT_EVERY_SECONDS = 1800

#: The tables the work's tree lives in, each restored whole for its scope.
TREE_TABLES = ("experience_cards", "experience_stages", "experience_edges")

#: A version's id, as made here: anything else is refused before it is
#: used as a file name.
VERSION_ID = re.compile(r"^v[0-9]{1,7}$")
SHA = re.compile(r"^[0-9a-f]{64}$")


class VersionError(Exception):
    """A version that cannot be read or restored - missing, or damaged."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_of_file(path: Path) -> Optional[str]:
    """A file's SHA-256, read in pieces; None when there is no such file."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for piece in iter(lambda: handle.read(1 << 20), b""):
                digest.update(piece)
    except OSError:
        return None
    return digest.hexdigest()


def _inside(target: Path, root: Path) -> bool:
    target, root = Path(target).resolve(), Path(root).resolve()
    return target == root or root in target.parents


class VersionStore:
    """The versions of one run, in its folder. Read-only without a policy;
    with one, every path it writes is checked against it first."""

    def __init__(self, run_dir: Path, policy: Any = None) -> None:
        self.run_dir = Path(run_dir)
        self.root = self.run_dir / VERSIONS_DIR
        self.policy = policy
        #: A version is saved by the run, and its slides added to it later by
        #: the thread that draws them: the index is changed by one at a time.
        self._lock = threading.RLock()
        #: (path, modified, size) -> sha: a file not changed since the last
        #: version is not read again.
        self._hashes: Dict[Tuple[str, int, int], str] = {}
        #: Objects known to be in the store since it was last collected: a
        #: file not changed is kept without a look at the store at all.
        self._present: set = set()
        self._collected_at = 0.0

    # -- writing, only here ----------------------------------------------------
    def _checked(self, target: Path) -> Path:
        """A path the store may write: inside its own folder, and - when it
        has the work's policy - allowed by it, as the agent's writes are."""
        if not _inside(target, self.root):
            raise VersionError(f"the version store writes only in {self.root}")
        if self.policy is not None:
            from ...runtime.filesystem import validate_write_path
            validate_write_path(str(target), self.policy)
        return target

    def _write(self, target: Path, data: bytes) -> None:
        """Written whole or not at all: to a temporary file beside it, then
        moved into place."""
        target = self._checked(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.tmp")
        temporary.write_bytes(data)
        # On Windows a file being read - the index, while the panel asks for
        # it - cannot be replaced for that moment: tried again, briefly.
        for attempt in range(20):
            try:
                os.replace(temporary, target)
                return
            except PermissionError:
                if attempt == 19:
                    temporary.unlink(missing_ok=True)
                    raise
                time.sleep(0.05)

    def _object(self, sha: str) -> Path:
        if not SHA.match(str(sha or "")):
            raise VersionError(f"not an object id: {sha!r}")
        return self.root / "objects" / sha[:2] / sha

    def put(self, data: bytes) -> str:
        sha = sha_of_bytes(data)
        target = self._object(sha)
        if not target.is_file():
            self._write(target, data)
        else:
            # Used again: as new as a fresh one, for `collect`'s grace.
            try:
                os.utime(self._checked(target), None)
            except OSError:
                pass
        return sha

    def put_json(self, value: Any) -> str:
        return self.put(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   default=str).encode("utf-8"))

    def put_file(self, path: Path, st: Any = None) -> Tuple[str, int]:
        """A file kept by its content: read only if it changed since it was
        last kept, and its bytes stored only if no version holds them yet.
        `st`: what a walk of its folder already saw of it."""
        st = st if st is not None else path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
        sha = self._hashes.get(key)
        if sha is None or (sha not in self._present and not self._object(sha).is_file()):
            data = path.read_bytes()
            sha = self.put(data)
            self._hashes[key] = sha
        self._present.add(sha)
        return sha, st.st_size

    # -- reading -----------------------------------------------------------------
    def get(self, sha: str) -> bytes:
        """An object's bytes - checked against its id, so a damaged one is
        never restored."""
        try:
            data = self._object(sha).read_bytes()
        except OSError:
            raise VersionError(f"a saved file is missing ({sha[:12]})") from None
        if sha_of_bytes(data) != sha:
            raise VersionError(f"a saved file is damaged ({sha[:12]})")
        return data

    def get_json(self, sha: str) -> Any:
        try:
            return json.loads(self.get(sha).decode("utf-8"))
        except ValueError:
            raise VersionError(f"a saved record is damaged ({sha[:12]})") from None

    def index(self) -> Dict[str, Any]:
        """Every version in brief, the head - the one the next is saved
        after - and the line it is on."""
        empty = {"head": "", "branch": MAIN, "branches": [MAIN], "versions": []}
        try:
            said = json.loads((self.root / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return empty
        if not isinstance(said, dict) or not isinstance(said.get("versions"), list):
            return empty
        return dict(empty, **said)

    def _save_index(self, index: Dict[str, Any]) -> None:
        self._write(self.root / "index.json",
                    json.dumps(index, ensure_ascii=False, indent=1).encode("utf-8"))

    def manifest(self, version_id: str) -> Dict[str, Any]:
        if not VERSION_ID.match(str(version_id or "")):
            raise VersionError(f"no such version: {version_id!r}")
        try:
            said = json.loads((self.root / "versions" / f"{version_id}.json")
                              .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise VersionError(f"no such version: {version_id}") from None
        if not isinstance(said, dict):
            raise VersionError(f"version {version_id} is damaged")
        return said

    # -- versions --------------------------------------------------------------
    def save(self, kind: str, label: str, files: Dict[str, Dict[str, Any]],
             granted: Dict[str, Dict[str, Any]], skipped: List[str],
             state_sha: str, summary: Dict[str, Any],
             amended: Optional[Dict[str, Any]] = None,
             message: Optional[Dict[str, Any]] = None,
             details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """A new version after the head, on the head's line; it becomes the
        head.

        `amended`: the version is the first after the user's new words
        amended the contract - the words and what changed in it, kept on the
        entry so the tree can show, per line, what that line was told.
        `message`: what it did, as a commit says it - its title (the label),
        the changes in a few lines, the files added, modified and deleted
        since its parent. `details`: what else names it (a round's number,
        what the reviewer found of it), kept as fields of their own."""
        with self._lock:
            return self._save(kind, label, files, granted, skipped, state_sha, summary,
                              amended, message, details)

    def _save(self, kind, label, files, granted, skipped, state_sha, summary, amended,
              message, details) -> Dict[str, Any]:
        details = dict(details or {})
        environments = details.pop("environments", None)
        index = self.index()
        number = len(index["versions"]) + 1
        version_id = f"v{number}"
        entry = {"id": version_id, "n": number, "parent": index["head"] or "",
                 "branch": index["branch"] or MAIN, "label": str(label)[:300],
                 "kind": kind, "at": _now(), "files": len(files),
                 "granted": len(granted), "summary": summary}
        if amended:
            entry["amended"] = amended
        if message:
            entry["message"] = message
        for name, value in (details or {}).items():
            if name not in entry and value is not None:
                entry[name] = value
        manifest = dict(entry, files=files, granted=granted,
                        skipped=skipped[:500], state=state_sha)
        if environments:
            manifest["environments"] = environments
        self._write(self.root / "versions" / f"{version_id}.json",
                    json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"))
        index["versions"].append(entry)
        index["head"] = version_id
        self._save_index(index)
        # A resume point of this line is behind the version now.
        points = self.resume_points()
        if points.pop(entry["branch"], None) is not None:
            self._write(self.root / RESUME_FILE,
                        json.dumps(points, ensure_ascii=False, indent=1).encode("utf-8"))
        return entry

    # -- resume points -----------------------------------------------------------
    def resume_points(self) -> Dict[str, Any]:
        try:
            said = json.loads((self.root / RESUME_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return said if isinstance(said, dict) else {}

    def resume_point(self) -> Optional[Dict[str, Any]]:
        """The latest resume point of the head's line - only while it was
        saved after the head: a version saved since, or the head moved, and
        it is left behind."""
        index = self.index()
        point = self.resume_points().get(index["branch"] or MAIN)
        if isinstance(point, dict) and str(point.get("after") or "") == str(index["head"] or "") \
                and SHA.match(str(point.get("state") or "")):
            return point
        return None

    def save_resume(self, label: str, files: Dict[str, Dict[str, Any]],
                    granted: Dict[str, Dict[str, Any]], skipped: List[str], state_sha: str,
                    details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """The line's resume point, in place of the one before it - and what
        neither a version nor a resume point holds any more, removed."""
        with self._lock:
            index = self.index()
            branch = index["branch"] or MAIN
            point = {"after": index["head"] or "", "branch": branch, "label": str(label)[:300],
                     "at": _now(), "files": files, "granted": granted,
                     "skipped": skipped[:500], "state": state_sha,
                     **{k: v for k, v in (details or {}).items() if v is not None}}
            points = self.resume_points()
            points[branch] = point
            self._write(self.root / RESUME_FILE,
                        json.dumps(points, ensure_ascii=False, indent=1).encode("utf-8"))
            if time.time() - self._collected_at >= COLLECT_EVERY_SECONDS:
                self.collect()
            return point

    def held(self) -> Optional[set]:
        """Every object a version or a resume point holds - None when one of
        them cannot be read, and so nothing may be removed."""
        held: set = set()
        kept: List[Dict[str, Any]] = []
        for path in sorted((self.root / "versions").glob("v*.json")):
            try:
                said = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            if isinstance(said, dict):
                kept.append(said)
        kept += [one for one in self.resume_points().values() if isinstance(one, dict)]
        for one in kept:
            for group in ("files", "granted"):
                for entry in (one.get(group) or {}).values():
                    sha = entry.get("sha") if isinstance(entry, dict) else None
                    if sha:
                        held.add(str(sha))
            state = str(one.get("state") or "")
            if not SHA.match(state):
                continue
            held.add(state)
            try:
                text = self.get(state).decode("utf-8", "replace")
            except VersionError:
                return None
            # What the state holds by its id: the tree's rows, the slides'
            # figures.
            held.update(re.findall(r"\b[0-9a-f]{64}\b", text))
        return held

    def collect(self) -> int:
        """What no version and no resume point holds, removed from the
        store - never one written in the last `COLLECT_GRACE_SECONDS`.
        Only the store's own objects: the work's files are not touched."""
        # What it removes is no longer known to be there.
        self._present = set()
        self._collected_at = time.time()
        objects = self.root / "objects"
        if not objects.is_dir():
            return 0
        held = self.held()
        if held is None:
            return 0
        cutoff = time.time() - COLLECT_GRACE_SECONDS
        removed = 0
        for folder in objects.iterdir():
            if not folder.is_dir():
                continue
            for path in folder.iterdir():
                if not SHA.match(path.name) or path.name in held:
                    continue
                try:
                    if path.stat().st_mtime > cutoff:
                        continue
                    self._checked(path).unlink()
                    removed += 1
                except OSError:
                    continue
        return removed

    def set_slides(self, version_id: str, slides: Optional[Dict[str, Any]],
                   summary: Dict[str, Any]) -> None:
        """A VERSION'S SLIDES, ADDED ONCE THEY ARE DRAWN - after the version
        was saved, while the work went on: kept in its state as if they had
        been there from the start, and how they came out in its summary."""
        with self._lock:
            manifest = self.manifest(version_id)
            state = self.get_json(str(manifest.get("state") or "")) or {}
            if not isinstance(state, dict):
                raise VersionError(f"version {version_id} is damaged")
            state["slides"] = slides
            manifest["state"] = self.put_json(state)
            manifest["summary"] = {k: v for k, v in dict(manifest.get("summary") or {}).items()
                                   if k != "slides_why"}
            manifest["summary"].update(summary)
            self._write(self.root / "versions" / f"{version_id}.json",
                        json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"))
            index = self.index()
            for one in index["versions"]:
                if one.get("id") == version_id:
                    one["summary"] = manifest["summary"]
            self._save_index(index)

    def move_head(self, version_id: str) -> str:
        """Continue from this version. One with versions after it starts a
        new line, so the line after it is kept as it was; the tip of a line
        carries that line on. The line's name is returned."""
        with self._lock:
            return self._move_head(version_id)

    def _move_head(self, version_id: str) -> str:
        index = self.index()
        known = {one["id"]: one for one in index["versions"]}
        if version_id not in known:
            raise VersionError(f"no such version: {version_id}")
        if any(one.get("parent") == version_id for one in index["versions"]):
            number = len(index["branches"]) + 1
            while f"branch {number}" in index["branches"]:
                number += 1
            branch = f"branch {number}"
            index["branches"] = [*index["branches"], branch]
        else:
            branch = known[version_id].get("branch") or MAIN
        index.update(head=version_id, branch=branch)
        self._save_index(index)
        return branch


#: How many paths of each kind a version's message lists - the counts are
#: always whole.
MOST_CHANGED_LISTED = 60


def file_changes(before: Dict[str, Any], after: Dict[str, Any],
                 most: int = MOST_CHANGED_LISTED) -> Dict[str, Any]:
    """What a version changed in the work's files, against the version
    before it: by path, by the hash of each file's content - the files
    added, modified and deleted, each list at most `most` long, with the
    whole counts beside them."""
    before, after = before or {}, after or {}
    added = sorted(set(after) - set(before))
    deleted = sorted(set(before) - set(after))
    modified = sorted(one for one in set(after) & set(before)
                      if (after[one] or {}).get("sha") != (before[one] or {}).get("sha"))
    return {"added": added[:most], "modified": modified[:most], "deleted": deleted[:most],
            "counts": {"added": len(added), "modified": len(modified),
                       "deleted": len(deleted)}}


def with_message(entry: Dict[str, Any]) -> Dict[str, Any]:
    """A version's entry with its message: the one it was saved with, or -
    saved before versions had one - its label as the title, nothing else
    known."""
    if isinstance(entry.get("message"), dict) and entry["message"].get("title"):
        return entry
    return dict(entry, message={"title": str(entry.get("label") or ""), "body": [],
                                "files": None})


# -- the work's folder ---------------------------------------------------------------
def _walked(folder: Path, run_id: str) -> Tuple[List[Tuple[str, Path, Any]], List[str]]:
    """Every file of the work's folder a version holds, with what the walk
    itself saw of it (its size and when it changed) - and those too many or
    too large to keep. Other runs' records are theirs, and left out; so is
    what SKIP_DIRS and SKIP_IN_RUN name, and the project's run logs
    (LOGS_DIR, at the top of the folder only - a folder of the work's own
    that happens to share the name deeper down is the work's). Links are
    not followed.

    LIGHT (measured 2026-10-27, 1329 files): what a folder listing already
    says of each entry is used - no second look at a file to ask if it is a
    link, or how big it is - so a progress point every ten minutes costs a
    walk of the folder and the files that changed, nothing more."""
    folder = Path(folder)
    run_dir = folder / "runs" / run_id
    found: List[Tuple[str, Path, Any]] = []
    skipped: List[str] = []
    if not folder.is_dir():
        return found, skipped

    def walk(here: Path, prefix: str) -> None:
        try:
            with os.scandir(here) as listing:
                entries = sorted(listing, key=lambda one: one.name)
        except OSError:
            return
        folders = []
        for entry in entries:
            try:
                if entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction()):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    folders.append(entry)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            relative = prefix + entry.name
            if st.st_size > MOST_BYTES or len(found) >= MOST_FILES:
                skipped.append(relative)
                continue
            found.append((relative, Path(entry.path), st))
        for entry in folders:
            name = entry.name
            if name in SKIP_DIRS:
                continue
            # AN INSTALLED ENVIRONMENT is not the work, and never kept
            # (environments.py): what it held is (`environments_of`).
            if is_environment(Path(entry.path), name):
                continue
            if here == folder and name == LOGS_DIR:
                continue
            if here == folder / "runs" and name != run_id:
                continue
            if here == run_dir and name in SKIP_IN_RUN:
                continue
            walk(Path(entry.path), prefix + name + "/")

    walk(folder, "")
    return found, skipped


def environments_of(folder: Path, run_id: str) -> List[Dict[str, Any]]:
    """The installed environments in the work's folder - each by its path
    and the packages it holds - kept with a version for a rollback to say
    what changed in them (it leaves them as they are)."""
    from .environments import found_in
    try:
        found = found_in(Path(folder), skip=SKIP_DIRS)
    except OSError:
        return []
    # Another run's records are theirs.
    return [one for one in found
            if not (one["path"].startswith("runs/") and not one["path"].startswith(f"runs/{run_id}/"))]


def work_files(folder: Path, run_id: str) -> Tuple[Dict[str, Path], List[str]]:
    """Every file of the work's folder a version holds, by its path inside
    it ("notes/plan.md"), and those too many or too large to keep
    (`_walked`)."""
    found, skipped = _walked(folder, run_id)
    return {relative: whole for relative, whole, _st in found}, skipped


def keep_files(store: VersionStore, folder: Path, run_id: str
               ) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """The work's folder, kept: each file's object and size - a file not
    changed since it was last kept, by what the walk saw, not read again."""
    found, skipped = _walked(folder, run_id)
    files: Dict[str, Dict[str, Any]] = {}
    for relative, whole, st in found:
        try:
            sha, size = store.put_file(whole, st)
        except OSError:
            skipped.append(relative)
            continue
        files[relative] = {"sha": sha, "size": size}
    return files, skipped


def keep_granted(store: VersionStore, written: Dict[str, Optional[str]],
                 roots: Iterable[Path]) -> Dict[str, Dict[str, Any]]:
    """The files the agent wrote in granted folders, as it left them. One
    the user has changed since is theirs now, and not taken."""
    roots = [Path(one) for one in roots]
    kept: Dict[str, Dict[str, Any]] = {}
    for path, agents in (written or {}).items():
        target = Path(path)
        if not agents or not target.is_file() \
                or not any(_inside(target, root) for root in roots):
            continue
        if sha_of_file(target) != agents:
            continue
        try:
            sha, size = store.put_file(target)
        except OSError:
            continue
        kept[str(target)] = {"sha": sha, "size": size}
    return kept


def _roots_of(folder: Path, run_id: str) -> Tuple[Path, Path, List[Path]]:
    """The work's folder, its logs and what versions leave out of the run,
    resolved - once for a whole rollback, not for each of its files."""
    run_dir = Path(folder) / "runs" / run_id
    return (Path(folder).resolve(), (Path(folder) / LOGS_DIR).resolve(),
            [(run_dir / one).resolve() for one in SKIP_IN_RUN])


def _work_target(folder: Path, run_id: str, relative: str, policy: Any,
                 roots: Optional[Tuple[Path, Path, List[Path]]] = None) -> Path:
    """Where a file of a version goes back to: inside the work's folder, not
    in what versions leave out, and allowed by the work's policy. `roots`:
    `_roots_of`, resolved once by a caller checking many files."""
    from ...runtime.filesystem import validate_write_path
    parts = Path(str(relative)).parts
    # On Windows "C:x" is relative and still names another drive.
    if not parts or Path(relative).is_absolute() or ".." in parts \
            or (os.name == "nt" and ":" in str(relative)):
        raise VersionError(f"not a path inside the work's folder: {relative!r}")
    target = Path(folder) / Path(*parts)
    home, logs, left_out = roots or _roots_of(folder, run_id)
    real = target.resolve()

    def within(root: Path) -> bool:
        return real == root or root in real.parents
    if not within(home) or within(logs) or any(within(one) for one in left_out):
        raise VersionError(f"not a path a version restores: {relative!r}")
    # ANOTHER RUN'S RECORDS are its folder under runs/ - not a file the work
    # itself left loose in runs/ (a server's log, say), which versions keep
    # with the rest of the work and give back with it.
    if parts[0] == "runs" and len(parts) >= 3 and parts[1] != run_id:
        raise VersionError(f"another run's records are not restored: {relative!r}")
    validate_write_path(str(target), policy)
    return target


def _claim(paths: Iterable[Path]) -> None:
    """Written by the application, not the user: the listener does not hear
    it as the user's activity."""
    from ...common.agent_writes import mark_written
    for one in paths:
        mark_written(str(one))


class _Saying:
    """What a rollback is doing, said at most twice a second: `say` is given
    {"step", "done", "total"} (the process starting a run writes it where
    the agent reads it - child.py)."""

    def __init__(self, say: Any) -> None:
        self.say, self.at = say, 0.0

    def __call__(self, step: str, done: int, total: int, now: bool = False) -> None:
        if self.say is None or (not now and time.monotonic() - self.at < 0.5):
            return
        self.at = time.monotonic()
        try:
            self.say({"step": step, "done": done, "total": total})
        except Exception:                      # pragma: no cover - only words
            pass


def restore_files(store: VersionStore, manifest: Dict[str, Any], folder: Path,
                  run_id: str, policy: Any, progress: Any = None) -> Dict[str, Any]:
    """The work's folder made exactly the version's: files it does not hold
    removed, files that differ written back. Every object needed is checked
    first, so a damaged version changes nothing. `progress`: told what it
    is doing as it goes (`_Saying`)."""
    from ...runtime.filesystem import remembering
    from .environments import inside, roots_in
    say = _Saying(progress)
    files = manifest.get("files") or {}
    # INSTALLED ENVIRONMENTS ARE LEFT AS THEY ARE (the user, 2026-10-29): a
    # version kept before they were left out holds their files - not
    # written back, as they are not removed.
    held = roots_in(files)
    files = {relative: entry for relative, entry in files.items()
             if not inside(relative, held)}
    skipped = set(manifest.get("skipped") or [])
    current, too_big = work_files(folder, run_id)
    roots = _roots_of(folder, run_id)
    total = len(files) + len(current)
    say("checking", 0, total, now=True)
    with remembering():
        done = _restore_files(store, files, skipped, current, too_big, roots, folder,
                              run_id, policy, say, total)
    left = environments_left(manifest, folder, run_id)
    if left:
        done["environments"] = left
    return done


def environments_left(manifest: Dict[str, Any], folder: Path,
                      run_id: str) -> List[Dict[str, Any]]:
    """The environments a rollback left as they are - each with what it
    holds now that the version's did not, and what it no longer holds."""
    then = {one.get("path"): set(one.get("packages") or [])
            for one in manifest.get("environments") or [] if isinstance(one, dict)}
    out = []
    for one in environments_of(folder, run_id):
        was = then.get(one["path"])
        now = set(one["packages"])
        entry = {"path": one["path"], "packages": one["count"], "left_as_is": True}
        if was is None:
            entry["since"] = "made after the version"
        else:
            entry["added_since"] = sorted(now - was)[:50]
            entry["gone_since"] = sorted(was - now)[:50]
        out.append(entry)
    return out


def _restore_files(store: VersionStore, files: Dict[str, Any], skipped: Set[str],
                   current: Dict[str, Path], too_big: List[str], roots: Any, folder: Path,
                   run_id: str, policy: Any, say: "_Saying", total: int) -> Dict[str, Any]:
    to_write: List[Tuple[Path, str]] = []
    # A path no version may write is left as it is, and said - not the whole
    # rollback refused for it.
    refused: List[str] = []
    for done, (relative, entry) in enumerate(files.items()):
        say("checking", done, total)
        try:
            target = _work_target(folder, run_id, relative, policy, roots)
        except VersionError as exc:
            refused.append(str(exc))
            continue
        whole = current.get(relative)
        if whole is not None and sha_of_file(whole) == entry.get("sha"):
            continue
        store.get(str(entry.get("sha") or ""))       # missing or damaged: stop here
        to_write.append((target, entry["sha"]))
    to_remove = []
    for done, relative in enumerate(current):
        say("checking", len(files) + done, total)
        if relative in files or relative in skipped:
            continue
        try:
            _work_target(folder, run_id, relative, policy, roots)
        except VersionError as exc:
            refused.append(str(exc))
            continue
        to_remove.append(current[relative])
    _claim([one for one, _sha in to_write] + to_remove)
    changing = len(to_remove) + len(to_write)
    say("putting back", 0, changing, now=True)
    for done, target in enumerate(to_remove):
        say("putting back", done, changing)
        try:
            target.unlink()
        except OSError:
            pass
    for done, (target, sha) in enumerate(to_write):
        say("putting back", len(to_remove) + done, changing)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(store.get(sha))
    return {"restored": [one.relative_to(folder).as_posix() for one, _ in to_write],
            "removed": [one.relative_to(folder).as_posix() for one in to_remove],
            "not_kept": sorted(set(too_big) | skipped), "refused": refused[:50]}


def restore_granted(store: VersionStore, manifest: Dict[str, Any],
                    agents: Dict[str, Optional[str]], policy: Any
                    ) -> Dict[str, Any]:
    """The agent's files in granted folders, put back as the version had
    them - each ONLY if it is still as the agent last left it (`agents`:
    path -> the hash of its last write, None if it deleted it). One the user
    changed since is theirs: left, and said so. Nothing here is ever
    deleted - a file the agent wrote after the version stays."""
    from ...runtime.filesystem import validate_write_path
    roots = [Path(one) for one in getattr(policy, "extra_writable", None) or []]
    restored, kept, refused = [], [], []
    plan: List[Tuple[Path, str]] = []
    for path, entry in (manifest.get("granted") or {}).items():
        target = Path(path)
        if not any(_inside(target, root) for root in roots):
            refused.append(str(target))
            continue
        validate_write_path(str(target), policy)
        now = sha_of_file(target)
        if now == entry.get("sha"):
            continue
        if str(target) not in agents or now != agents[str(target)]:
            kept.append(str(target))
            continue
        store.get(str(entry.get("sha") or ""))
        plan.append((target, entry["sha"]))
    _claim([one for one, _sha in plan])
    for target, sha in plan:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(store.get(sha))
        restored.append(str(target))
    held = set((manifest.get("granted") or {}).keys())
    left = sorted(path for path, sha in (agents or {}).items()
                  if sha and path not in held and Path(path).is_file())
    return {"restored": restored, "kept": kept, "left": left, "refused": refused}


# -- the work's tree ------------------------------------------------------------------
def keep_tree(store: VersionStore, db: Any, scope: str) -> Dict[str, List[str]]:
    """The work's cards, stages and edges, each row kept as an object of its
    own: most rows do not change from one version to the next."""
    kept: Dict[str, List[str]] = {}
    for table in TREE_TABLES:
        rows = db.query(f"SELECT * FROM {table} WHERE scope = ? ORDER BY id", (scope,))
        kept[table] = [store.put_json(dict(row)) for row in rows]
    return kept


def restore_tree(store: VersionStore, db: Any, scope: str,
                 kept: Dict[str, List[str]]) -> Dict[str, int]:
    """The work's tree made the version's, in one transaction - every row
    read and checked first. A row is written only into this work's scope and
    only into the table's own columns, whatever the saved row says."""
    rows: Dict[str, List[Dict[str, Any]]] = {}
    for table in TREE_TABLES:
        rows[table] = []
        for sha in (kept or {}).get(table) or []:
            row = store.get_json(sha)
            if isinstance(row, dict):
                rows[table].append(row)
    columns = {table: [one["name"] for one in db.query(f"PRAGMA table_info({table})")]
               for table in TREE_TABLES}
    with db.write() as conn:
        for table in TREE_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE scope = ?", (scope,))
            for row in rows[table]:
                names = [one for one in columns[table] if one in row]
                values = [scope if one == "scope" else row[one] for one in names]
                if "scope" not in names:
                    names.append("scope")
                    values.append(scope)
                # IGNORE, not REPLACE: a saved row never takes over a row of
                # another scope that happens to have its id.
                conn.execute(f"INSERT OR IGNORE INTO {table} ({', '.join(names)}) "
                             f"VALUES ({', '.join('?' for _ in names)})", values)
    # What each stage is found by, worked out again for the stages it has now.
    from ...heartbeat.experience.index import StageIndex
    from ...heartbeat.experience.store import ExperienceStore
    held = ExperienceStore(db, scope)
    index = StageIndex(db, scope)
    index.clear()
    index.ensure(held.stages(), {one["id"]: one for one in held.cards()})
    return {table: len(rows[table]) for table in TREE_TABLES}
