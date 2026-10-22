"""WHERE LEARNING READS - and the local index searches: ONE list.

The user lists PLACES in Work's Learning panel: folders, and, with one
click, their Claude Code or Codex records. Each brings the other along:

  - a FOLDER brings every conversation with a coding agent that worked in
    it (started there, or reading and editing its files);
  - a RECORD FOLDER brings every project folder its conversations worked
    in: where a session was started - or, when that was a folder holding
    everything (the home folder, the desktop), the project folders of the
    files it read and edited. Each is listed as found through it, and the
    user may leave one out.

Beside them, the user's WORK PROJECTS (the state heartbeat's projects of
the work kind) are read by themselves, with their own folders - fixed
units, never regrouped by the folder map.

Never the application's own folders: its data, and the agent's workspace
(its own runs are not the user's work).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import records

PLACES = "learning_places"
EXCLUDED = "learning_excluded"
KINDS = ("folder", "claude", "codex")
#: What marks a folder as a project's own root (beside the experience's).
ROOT_MARKS = (".git", ".claude", "CLAUDE.md", "AGENTS.md", "README.md", "README.txt",
              "README", "pyproject.toml", "setup.py", "package.json", ".Rproj.user",
              "renv.lock", "Makefile", ".vscode", "main.tex")


def _norm(path: Any) -> str:
    try:
        return os.path.normcase(os.path.abspath(str(path)))
    except (TypeError, ValueError):
        return ""


def _is_container(path: Path) -> bool:
    from ..heartbeat.experience.workspace import is_container
    return is_container(path)


def project_root(path: Path) -> Path:
    """The nearest folder at or above `path` that marks a piece of work,
    short of the folders that hold everything; or the folder itself."""
    here = path if path.is_dir() else path.parent
    for candidate in [here, *here.parents]:
        if _is_container(candidate):
            break
        try:
            if any((candidate / mark).exists() for mark in ROOT_MARKS):
                return candidate
        except OSError:
            break
    return here


class Places:
    def __init__(self, config: Any, never: Sequence[Any] = ()) -> None:
        self.config = config
        self.never = [_norm(one) for one in never if one]
        #: The folders of the projects the user made in Work (app.py), read
        #: and searched as places of theirs; none here.
        self.work_tab_folders = lambda: []
        #: What the record folders bring, worked out at most once a minute
        #: (the panel asks every few seconds while an update goes): (when,
        #: what).
        self._found: Optional[Tuple[float, List[Dict[str, Any]]]] = None
        self._finding: Any = None

    # -- the list -------------------------------------------------------------
    def listed(self) -> List[Dict[str, Any]]:
        value = self.config.get(PLACES, []) or []
        return [dict(one) for one in value if isinstance(one, dict) and one.get("path")]

    def excluded(self) -> List[str]:
        return [str(one) for one in (self.config.get(EXCLUDED, []) or [])]

    def is_never(self, path: Any) -> bool:
        return any(records.is_within(str(path), one) for one in self.never)

    def add(self, path: Any, kind: str = "folder") -> Dict[str, Any]:
        """Add a place; raises ValueError saying why it cannot be."""
        if kind not in KINDS:
            raise ValueError("a place is a folder, or Claude Code's or Codex's records")
        if kind == "claude":
            path = records.claude_sessions_root()
        elif kind == "codex":
            path = records.codex_sessions_root()
        path = Path(str(path)).expanduser()
        if not path.is_dir():
            raise ValueError(f"{path} is not a folder on this computer")
        if self.is_never(path):
            raise ValueError("the application's own folders are never read")
        listed = self.listed()
        if any(_norm(one["path"]) == _norm(path) for one in listed):
            return {"path": str(path), "kind": kind}
        one = {"path": str(path), "kind": kind,
               "added_at": datetime.now(timezone.utc).isoformat()}
        self.config.set(PLACES, listed + [one], internal=True)
        self._found = None
        return one

    def remove(self, path: Any) -> bool:
        listed = self.listed()
        kept = [one for one in listed if _norm(one["path"]) != _norm(path)]
        if len(kept) == len(listed):
            return False
        self.config.set(PLACES, kept, internal=True)
        self._found = None
        return True

    def set_excluded(self, path: Any, out: bool) -> None:
        current = self.excluded()
        others = [one for one in current if _norm(one) != _norm(path)]
        self.config.set(EXCLUDED, others + ([str(path)] if out else []), internal=True)
        self._found = None

    def _left_out(self, path: Any) -> bool:
        return self.is_never(path) or any(records.is_within(str(path), one)
                                          for one in self.excluded())

    # -- what each brings -----------------------------------------------------
    def record_tools(self) -> List[str]:
        return [one["kind"] for one in self.listed() if one["kind"] in ("claude", "codex")]

    def sessions(self, tools: Sequence[str] = ("claude", "codex")) -> List[records.Session]:
        """Every conversation of the user's with these agents - never one
        the application's own agent had (its workspace)."""
        return [one for one in records.all_sessions(tools)
                if not (one.cwd and self.is_never(one.cwd))]

    def session_folders(self, session: records.Session) -> List[str]:
        """The project folders a session worked in."""
        out: List[str] = []
        start = Path(session.cwd) if session.cwd else None
        if start is not None and start.exists() and not _is_container(start):
            out.append(str(project_root(start)))
        else:
            for file in session.touched:
                folder = Path(file).parent
                if folder.exists() and not _is_container(folder):
                    root = str(project_root(folder))
                    if root not in out:
                        out.append(root)
        return [one for one in out if not self._left_out(one)]

    def found_folders(self, wait: bool = True) -> List[Dict[str, Any]]:
        """The folders the listed record folders bring: each with the
        conversations that worked in it and which agent's they were.
        `wait=False` (the panel): what was worked out last, and - when it is
        old or there is none - worked out again behind, not waited for: the
        first reading of every conversation can take a while."""
        tools = self.record_tools()
        if not tools:
            return []
        if self._found is not None and time.monotonic() - self._found[0] < 60:
            return [dict(one) for one in self._found[1]]
        if not wait:
            if self._finding is None or not self._finding.is_alive():
                import threading
                self._finding = threading.Thread(target=self.found_folders, name="learning-found",
                                                 daemon=True)
                self._finding.start()
            return [dict(one) for one in (self._found[1] if self._found else [])]
        by_folder: Dict[str, Dict[str, Any]] = {}
        for session in self.sessions(tools):
            for folder in self.session_folders(session):
                key = _norm(folder)
                one = by_folder.setdefault(key, {"path": folder, "via": set(),
                                                 "sessions": []})
                one["via"].add(session.tool)
                one["sessions"].append(session.path)
        listed = {_norm(one["path"]) for one in self.listed()}
        out = []
        for key, one in sorted(by_folder.items()):
            if key in listed:
                continue
            out.append({"path": one["path"], "via": sorted(one["via"]),
                        "sessions": sorted(set(one["sessions"]))})
        self._found = (time.monotonic(), out)
        return [dict(one) for one in out]

    def work_tab(self) -> List[Dict[str, Any]]:
        """The folders of the user's projects in Work, not within a listed
        place (that one maps them) nor left out."""
        listed = [one["path"] for one in self.listed() if one["kind"] == "folder"]
        out = []
        try:
            given = list(self.work_tab_folders() or [])
        except Exception:                      # pragma: no cover - none then
            given = []
        for one in given:
            path = str(one.get("path") or "")
            if not path or not Path(path).is_dir() or self._left_out(path):
                continue
            if any(records.is_within(path, place) for place in listed):
                continue
            out.append({"path": path, "project": one.get("project", "")})
        return out

    def folders(self, wait: bool = True) -> List[Dict[str, Any]]:
        """Every folder learning reads, listed or found, not left out."""
        out = [{"path": one["path"], "why": "listed"} for one in self.listed()
               if one["kind"] == "folder" and not self._left_out(one["path"])]
        out += [{"path": one["path"], "why": "found", "via": one["via"]}
                for one in self.found_folders(wait)]
        out += [{"path": one["path"], "why": "work"} for one in self.work_tab()]
        return out

    def sessions_in(self, folder: Any, sessions: Optional[Sequence[records.Session]] = None
                    ) -> List[records.Session]:
        """The conversations that worked in a folder: started in it, or
        reading and editing files in it."""
        sessions = sessions if sessions is not None else self.sessions()
        folder = str(folder)
        out = []
        for session in sessions:
            if session.cwd and records.is_within(session.cwd, folder):
                out.append(session)
            elif any(records.is_within(one, folder) for one in session.touched):
                out.append(session)
        return out

    # -- the user's Work projects ---------------------------------------------
    @staticmethod
    def work_projects(db: Any) -> List[Dict[str, Any]]:
        """The state heartbeat's projects of the work kind, not ended, each
        with its folders as they are on disk."""
        try:
            from ..heartbeat.states.project_round import is_scheme_row, project_view
            from ..heartbeat.states.tasks import Kept
            from ..heartbeat.experience.workspace import real_path
        except Exception:                      # pragma: no cover - import
            return []
        out = []
        for row in Kept(db, "live").all(limit=5000):
            if not is_scheme_row(row) or getattr(row, "retired_at", ""):
                continue
            view = project_view(row)
            if view.get("kind") != "work":
                continue
            folders = []
            for place in view.get("directories") or []:
                place = place.get("path") if isinstance(place, dict) else place
                found = real_path(str(place or ""))
                if found is not None and found.is_dir() and not _is_container(found):
                    root = str(project_root(found))
                    if root not in folders:
                        folders.append(root)
            out.append({"id": view["id"], "title": view.get("title", ""),
                        "summary": view.get("what_it_is") or view.get("summary", ""),
                        "folders": folders, "last_seen": view.get("last_seen", "")})
        return out

    # -- what the local index reads ------------------------------------------
    def index_roots(self, db: Any = None, wait: bool = True) -> List[Dict[str, Any]]:
        """The same places, for the local index: every folder learning
        reads, the Work projects' folders, and the record folders listed."""
        out: List[Dict[str, Any]] = []
        seen = set()

        def add(path: str, why: str, sessions: bool = False, **extra: Any) -> None:
            key = _norm(path)
            if key and key not in seen and not self.is_never(path):
                seen.add(key)
                out.append({"path": path, "why": why, "sessions": sessions, **extra})

        if db is not None:
            for project in self.work_projects(db):
                for folder in project["folders"]:
                    add(folder, "project", project=project["title"])
        for one in self.folders(wait):
            add(one["path"], {"listed": "added", "work": "project"}.get(one["why"], "found"))
        for one in self.listed():
            if one["kind"] in ("claude", "codex"):
                add(one["path"], "sessions", sessions=True, tool=one["kind"])
        return out
