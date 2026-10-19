"""THE LOCAL INDEX, as the application runs it.

WHICH FOLDERS: every Work project's folders, by default - what the user
works in - and the folders the user adds in Settings, each with everything
under it; and, on one click, the records of their Claude Code and Codex
conversations. No other folder of theirs is read. The agent's own data is
never gone into.

WHEN: a few minutes after the application starts, then every few hours,
and soon after the folders change - each time reading again only what
changed (build.py). In a process of its own, at the lowest priority
(worker.py); one at a time.

SEARCH: by words, through `search` - each finding with its file, where in
it, and the lines there, read from the file as it is now. Only what the
asker may read is ever given: `allowed` decides each path.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .rules import LINES_PER_PIECE
from .store import IndexStore, fts5_available

log = logging.getLogger(__name__)

INDEX_FLAG = "--index-worker"
#: After the application starts, before the first build.
FIRST_AFTER_SECONDS = 180
#: Between builds.
EVERY_SECONDS = 6 * 3600
#: After the folders change.
AFTER_CHANGE_SECONDS = 20
#: The most of a finding's text given.
MOST_SHOWN = 1200


def session_folders() -> List[Path]:
    """Where Claude Code and Codex keep the records of the user's
    conversations with them."""
    home = Path.home()
    codex = Path(os.environ.get("CODEX_HOME") or home / ".codex")
    return [home / ".claude" / "projects", codex / "sessions"]


class LocalIndex:

    def __init__(self, app: Any) -> None:
        self.app = app
        self.path = Path(app.paths.data_dir) / "index" / "files.db"
        self._lock = threading.Lock()
        self._child: Optional[subprocess.Popen] = None
        self._progress: Dict[str, Any] = {}
        self._wake = threading.Event()
        self._halt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._soon_at = 0.0

    # -- which folders ------------------------------------------------------------------
    def enabled(self) -> bool:
        return bool(self.app.config.get("index_enabled", True))

    def added(self) -> List[str]:
        return [str(one) for one in self.app.config.get("index_folders") or [] if str(one).strip()]

    def project_folders(self) -> List[Dict[str, Any]]:
        """Every Work project's folders, as the projects list them - not a
        project the user removed, nor one of the Work debugger's."""
        experience = getattr(getattr(self.app, "state_heartbeat", None), "experience", None)
        store = getattr(experience, "store", None)
        if store is None:
            return []
        removed = set(self.app.config.get("experience_removed_units") or [])
        out = []
        try:
            for row in store.workspaces():
                if row.get("unit_id") in removed or str(row.get("unit_id")).startswith("dbg_"):
                    continue
                for one in row.get("paths") or []:
                    out.append({"path": str(one), "why": "project",
                                "project": row.get("name") or ""})
        except Exception:                           # pragma: no cover - no projects
            log.debug("could not read the projects' folders", exc_info=True)
        return out

    def roots(self) -> List[Dict[str, Any]]:
        """Every folder indexed, once each: the projects', the user's own,
        and - when added - the conversation records."""
        seen, out = set(), []

        def add(path: str, **said: Any) -> None:
            key = os.path.normcase(os.path.abspath(path))
            if key in seen or not path:
                return
            seen.add(key)
            out.append({"path": str(Path(path)), **said})
        for one in self.project_folders():
            add(one["path"], why="project", project=one["project"], sessions=False)
        for one in self.added():
            add(one, why="added", sessions=False)
        if self.app.config.get("index_sessions", False):
            for one in session_folders():
                if one.is_dir():
                    add(str(one), why="sessions", sessions=True)
        return out

    def never(self) -> List[str]:
        return [str(self.app.paths.data_dir)]

    # -- building -----------------------------------------------------------------------
    def begin(self) -> None:
        """Builds at their times, until the application stops."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="local-index", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        wait = FIRST_AFTER_SECONDS
        while not self._halt.is_set():
            self._wake.wait(wait)
            if self._halt.is_set():
                return
            if self._wake.is_set():
                self._wake.clear()
                remaining = self._soon_at - time.time()
                if remaining > 0:
                    wait = remaining
                    continue
            if self.enabled():
                self.build_now(wait=True)
            wait = EVERY_SECONDS

    def soon(self) -> None:
        """The folders changed: built again in a moment."""
        self._soon_at = time.time() + AFTER_CHANGE_SECONDS
        self._wake.set()

    def build_now(self, wait: bool = False) -> Dict[str, Any]:
        """A build started - unless one is going - and, with `wait`, its end
        waited for."""
        if not fts5_available():
            return {"error": "this Python's SQLite has no full-text search (FTS5)"}
        with self._lock:
            if self._child is not None and self._child.poll() is None:
                return {"building": True}
            job = self.path.parent / "job.json"
            job.parent.mkdir(parents=True, exist_ok=True)
            # Folders no longer indexed are forgotten first.
            self._forget_dropped()
            job.write_text(json.dumps({"db": str(self.path), "roots": self.roots(),
                                       "never": self.never()}, ensure_ascii=False),
                           encoding="utf-8")
            from ..common import children
            command = ([sys.executable, INDEX_FLAG, str(job)] if getattr(sys, "frozen", False)
                       else [sys.executable, "-m", "personalized_agent.index.worker", str(job)])
            environment = dict(os.environ)
            package_root = str(Path(__file__).resolve().parents[2])
            environment["PYTHONPATH"] = os.pathsep.join(
                [package_root, *filter(None, [environment.get("PYTHONPATH")])])
            try:
                self._child = children.popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                    env=environment, whole_tree=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    if sys.platform.startswith("win") else 0)
            except Exception as exc:                # pragma: no cover - not started
                log.warning("could not start the local index", exc_info=True)
                return {"error": str(exc)}
            self._progress = {"started_at": time.time()}
            child = self._child
        threading.Thread(target=self._read, args=(child,), daemon=True,
                         name="local-index-progress").start()
        if wait:
            child.wait()
        return {"building": True}

    def _read(self, child: subprocess.Popen) -> None:
        for line in child.stdout or []:
            try:
                said = json.loads(line)
            except ValueError:
                continue
            if isinstance(said, dict):
                self._progress.update(said)

    def _forget_dropped(self) -> None:
        if not self.path.exists():
            return
        wanted = {os.path.normcase(os.path.abspath(one["path"])) for one in self.roots()}
        store = IndexStore(self.path)
        try:
            for one in store.roots():
                if os.path.normcase(os.path.abspath(one["path"])) not in wanted:
                    store.drop_root(one["path"])
        finally:
            store.close()

    def halt(self) -> None:
        """The application stops: no more builds, and the one going ended."""
        self._halt.set()
        self._wake.set()
        with self._lock:
            child = self._child
        if child is not None and child.poll() is None:
            from ..common.children import end_tree
            end_tree(child)

    # -- what is shown ------------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        building = self._child is not None and self._child.poll() is None
        kept: Dict[str, Dict[str, Any]] = {}
        counts = {"files": 0, "texts": 0}
        if self.path.exists():
            store = IndexStore(self.path, read_only=True)
            try:
                kept = {os.path.normcase(os.path.abspath(one["path"])): one
                        for one in store.roots()}
                counts = store.counts()
            except Exception:                       # pragma: no cover - being made
                pass
            finally:
                store.close()
        folders = []
        for one in self.roots():
            row = kept.get(os.path.normcase(os.path.abspath(one["path"]))) or {}
            folders.append({**one, "files": int(row.get("files") or 0),
                            "texts": int(row.get("texts") or 0),
                            "state": row.get("state") or "not yet",
                            "done_at": row.get("done_at")})
        return {"enabled": self.enabled(), "available": fts5_available(),
                "building": building, "progress": dict(self._progress),
                "folders": folders, "sessions": bool(self.app.config.get("index_sessions", False)),
                "session_folders": [str(one) for one in session_folders() if one.is_dir()],
                **counts}

    # -- searching ----------------------------------------------------------------------
    def search(self, text: str, allowed: Callable[[str], bool] = lambda path: True,
               limit: int = 10) -> List[Dict[str, Any]]:
        """What holds these words, the best first: each with its file, the
        line it starts at and the lines there as the file is now - only
        files `allowed` says may be read."""
        if not self.path.exists() or not str(text or "").strip():
            return []
        store = IndexStore(self.path, read_only=True)
        try:
            found = store.search(text, limit=limit * 4)
        finally:
            store.close()
        out: List[Dict[str, Any]] = []
        now = time.time()
        for one in found:
            path = one["path"]
            try:
                if not allowed(path) or not os.path.isfile(path):
                    continue
            except Exception:
                continue
            # A little for being recent: of two as good, the one worked on lately.
            age_days = max(0.0, (now - float(one.get("mtime") or now)) / 86400)
            one["rank"] = one["score"] * (1.0 + 0.3 / (1.0 + age_days / 30))
            if one["found_in"] == "text":
                one["text"] = self._lines(path, int(one["line"] or 1))
            out.append(one)
        out.sort(key=lambda one: one["rank"])
        return [{"path": one["path"], "line": one.get("line"), "found_in": one["found_in"],
                 "modified": time.strftime("%Y-%m-%d", time.localtime(float(one["mtime"])))
                 if one.get("mtime") else "", **({"text": one["text"]} if "text" in one else {})}
                for one in out[:limit]]

    @staticmethod
    def _lines(path: str, line: int) -> str:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines()
        except OSError:
            return ""
        return "\n".join(lines[line - 1:line - 1 + LINES_PER_PIECE])[:MOST_SHOWN]


__all__ = ["LocalIndex", "INDEX_FLAG", "session_folders"]
