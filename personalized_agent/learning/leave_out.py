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
in one, or half of where its deliverables are or every one of its
judgments lies there. Of any other case only what lies there is left out
(a case of a large project that once looked at a note in the folder keeps
the rest). The reasons are given only on what is left: an inferred one
needs two supporting judgments outside, a stated one one.
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


def _within(path: str, root: str) -> bool:
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

    def parts_out(self, store: Any) -> Tuple[Set[str], Set[str]]:
        """(the cases left out whole, the judgments left out - theirs and
        every other that lies in what is left out), of all the store has."""
        if not self:
            return set(), set()
        units = {one["id"]: one for one in store.units()}
        by_case: Dict[str, List[Dict[str, Any]]] = {}
        for one in store.judgments():
            by_case.setdefault(one["case_id"], []).append(one)
        cases = {case["id"] for case in store.cases()
                 if self.case_out(case, units.get(case["unit_id"]) or {}, by_case.get(case["id"], []))}
        judgments = {one["id"] for case_id, mine in by_case.items() for one in mine
                     if case_id in cases or self.judgment_out(one)}
        return cases, judgments
