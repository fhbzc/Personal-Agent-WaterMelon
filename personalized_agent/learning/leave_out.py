"""WHAT A RUN MAY NOT BE GIVEN of what was learned: the cases from folders
left out of it, and what Claude Code and Codex kept of work in them (the
user, 2026-10-22: a Work debugger project never gets experience from its
own folder - nor from folders the user names for it, such as the project
it redoes - or it would be given the answer).

WHAT LIES THERE is what rests on such a folder or on such a session: a
judgment that rests on a file there or on "session <id>" of one, a place
a case's deliverables are. A path written relative (`old_report/notes.md`) is
taken as in the folder whose name it begins with - leaving out one too
many rather than giving one too many.

A CASE IS LEFT OUT WHOLE when it is of that folder: its unit's folder is
in one - or, for a unit read from one of the user's projects, half of the
project's folders are - or half of where its deliverables are or every one
of its judgments lies there. Of any other case only what lies there is left out
(a case of a large project that once looked at a note in the folder keeps
the rest). The reasons are given only on what is left: an inferred one
needs two supporting judgments outside, a stated one one.

THE USER'S RECORDS a way searches (a state, a task, a stage of their work)
the same: one is left out whole when half or more of the places it rests on
lie there - or, resting on none, when its words name such a folder; of any
other, the words that name one are taken out.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple

_ABSOLUTE = re.compile(r"^(?:[a-z]:/|/|~/)")
_SESSION = re.compile(r"^session\s+(\S+)", re.IGNORECASE)
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _norm(path: Any) -> str:
    text = str(path or "").strip().replace("\\", "/").rstrip("/").lower()
    if text.startswith("~/"):
        text = str(Path.home()).replace("\\", "/").lower() + text[1:]
    return text


def _spelled(name: str) -> str:
    """A folder's name as a pattern: either slash, and the hyphen and
    underscore taken as one (a summary writes them interchangeably)."""
    return "".join("[\\\\/]" if ch == "/" else "[-_]" if ch in "-_" else re.escape(ch)
                   for ch in name)


def _telling(name: str) -> bool:
    """A folder's own name that says which folder it is - not "docs" or
    "report", which are words before they are folders."""
    return len(name) >= 6 and bool(re.search(r"[-_.0-9]", name))


def _within(path: str, root: str) -> bool:
    # The hyphen and the underscore as one: a project's folders are kept as
    # the review writes them (heartbeat._canonical_place).
    path, root = path.replace("-", "_"), root.replace("-", "_")
    return bool(root) and (path == root or path.startswith(root + "/"))


def session_ids(records: Iterable[Any]) -> Set[str]:
    """The sessions in these records: a Claude Code folder's session files,
    a Codex session file - by name, and by the id in it."""
    out: Set[str] = set()
    for one in records:
        path = Path(str(one))
        names: List[str] = []
        if path.is_dir():
            try:
                names = [entry.name for entry in os.scandir(path) if entry.name.endswith(".jsonl")]
            except OSError:
                names = []
        elif path.suffix == ".jsonl":
            names = [path.name]
        for name in names:
            stem = name[:-len(".jsonl")].lower()
            out.add(stem)
            out.update(_UUID.findall(stem))
    return out


class LeaveOut:
    def __init__(self, folders: Iterable[Any] = (), records: Iterable[Any] = ()) -> None:
        self.folders = [one for one in dict.fromkeys(_norm(f) for f in folders) if one]
        self.records = [one for one in dict.fromkeys(_norm(r) for r in records) if one]
        self.names = {one.rsplit("/", 1)[-1] for one in self.folders}
        self.sessions = session_ids(records)

    def __bool__(self) -> bool:
        return bool(self.folders)

    def _naming(self) -> "re.Pattern[str]":
        """The words that name a folder left out: its path, and anything
        under it; its own name, where that says which folder it is."""
        if getattr(self, "_named", None) is None:
            paths = sorted(self.folders, key=len, reverse=True)
            names = sorted((one for one in self.names if _telling(one)), key=len, reverse=True)
            parts = [_spelled(one) + r"(?:[\\/][^\s\"'<>|,;]*)?" for one in paths]
            parts += [r"(?<![\w.-])" + _spelled(one) + r"(?![\w-])" for one in names]
            self._named = re.compile("|".join(parts) or r"(?!x)x", re.IGNORECASE)
        return self._named

    def named_in(self, text: Any) -> bool:
        """Whether words name a folder left out."""
        return bool(self) and bool(self._naming().search(str(text or "")))

    def redact(self, text: Any) -> str:
        """Words with what names a folder left out taken out."""
        text = str(text or "")
        return self._naming().sub("[left out]", text) if self else text

    def record_out(self, places: Iterable[Any], text: Any = "") -> bool:
        """Whether one of the user's records is of a folder left out - and
        so not given at all: half or more of the places it rests on lie
        there, or, resting on none, its words name one."""
        if not self:
            return False
        places = [one for one in places if str(one or "").strip()]
        if places:
            return 2 * sum(1 for one in places if self.touches(one)) >= len(places)
        return self.named_in(text)

    def touches(self, ref: Any) -> bool:
        """Whether a path - or "session <id>" - lies in what is left out."""
        text = str(ref or "").strip()
        said = _SESSION.match(text)
        if said:
            return said.group(1).lower() in self.sessions
        path = _norm(text)
        if not path:
            return False
        if _ABSOLUTE.match(path):
            return any(_within(path, root) for root in self.folders + self.records)
        first = path.lstrip("./").split("/", 1)[0]
        return first in self.names

    def judgment_out(self, judgment: Dict[str, Any]) -> bool:
        return any(self.touches(ref) for ref in judgment.get("refs") or [])

    def location_out(self, location: Any) -> bool:
        return isinstance(location, dict) and self.touches(location.get("path"))

    def case_out(self, case: Dict[str, Any], unit: Dict[str, Any],
                 judgments: List[Dict[str, Any]]) -> bool:
        """Whether a case is of a folder left out - and so left out whole."""
        if not self:
            return False
        # A unit's folder in a folder left out; one that holds one is not out
        # by that alone - only what of it lies there.
        if any(_ABSOLUTE.match(_norm(one)) and self.touches(one)
               for one in (unit or {}).get("folders") or []):
            return True
        places = [one for one in case.get("locations") or [] if isinstance(one, dict)]
        if places and 2 * sum(1 for one in places if self.location_out(one)) >= len(places):
            return True
        return bool(judgments) and all(self.judgment_out(one) for one in judgments)

    def folders_out(self, folders: Iterable[Any]) -> bool:
        """Whether half or more of a project's folders lie in what is left out."""
        folders = [one for one in folders if str(one or "").strip()]
        return bool(self) and bool(folders) and \
            2 * sum(1 for one in folders if self.touches(one)) >= len(folders)

    def parts_out(self, store: Any) -> Tuple[Set[str], Set[str]]:
        """(the cases left out whole, the judgments left out - theirs and
        every other that lies in what is left out), of all the store has."""
        if not self:
            return set(), set()
        units = {one["id"]: one for one in store.units()}
        # A unit read from one of the user's projects keeps no folder of its
        # own when the project's did not resolve on disk: the project's.
        of_projects = project_folders(getattr(store, "db", None),
                                      [one.get("key") for one in units.values()
                                       if one.get("kind") == "work"])
        whole = {one["id"] for one in units.values() if one.get("kind") == "work"
                 and self.folders_out(of_projects.get(str(one.get("key") or ""), []))}
        by_case: Dict[str, List[Dict[str, Any]]] = {}
        for one in store.judgments():
            by_case.setdefault(one["case_id"], []).append(one)
        cases = {case["id"] for case in store.cases()
                 if case["unit_id"] in whole
                 or self.case_out(case, units.get(case["unit_id"]) or {}, by_case.get(case["id"], []))}
        judgments = {one["id"] for case_id, mine in by_case.items() for one in mine
                     if case_id in cases or self.judgment_out(one)}
        return cases, judgments


def project_folders(db: Any, ids: Iterable[Any]) -> Dict[str, List[str]]:
    """The folders each of the user's projects (a scheme row of the state
    heartbeat's tasks) is worked in, as it keeps them."""
    import json
    ids = [str(one) for one in ids if str(one or "").strip()]
    if db is None or not ids:
        return {}
    out: Dict[str, List[str]] = {}
    try:
        rows = db.query(f"SELECT id, described FROM state_tasks WHERE id IN "
                        f"({', '.join('?' * len(ids))})", tuple(ids))
    except Exception:
        return {}
    for row in rows:
        try:
            said = json.loads(row["described"] or "{}")
        except (TypeError, ValueError):
            continue
        folders = said.get("directories") if isinstance(said, dict) else None
        out[str(row["id"])] = [str(one.get("path") if isinstance(one, dict) else one)
                               for one in folders or []]
    return out
