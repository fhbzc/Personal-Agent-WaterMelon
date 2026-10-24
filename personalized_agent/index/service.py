"""THE LOCAL INDEX, as the application runs it.

WHICH FOLDERS: WHERE THE LEARNING READS (learning/places.py) - one list,
kept in Work's Learning panel: the folders the user listed and those their
agents' records bring, the records themselves when listed, their Work
projects' folders. No other folder of theirs is read. The agent's own data
is never gone into.

WHEN: AT THE LEARNING'S TIMES, at the start of each of its updates - one
switch and one clock for both (learning/service.py): an update the user
starts brings it up to date too, and a time missed while the computer was
off brings one update when it is back. Not when the application starts.
A folder just added is read soon after, alone, while the switch is on; a
folder taken out is forgotten then. Each time only what changed is read
again (build.py). In a process of its own, at the lowest priority
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .rules import LINES_PER_PIECE
from .store import IndexStore, fts5_available
from .talk import lines_of, talk_text

log = logging.getLogger(__name__)

INDEX_FLAG = "--index-worker"
#: After the folders change: the folders added read, those taken out forgotten.
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
        """The learning's switch: one for both."""
        learning = getattr(self.app, "learning", None)
        return bool(learning is not None and learning.auto_on())

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

    def roots(self, wait: bool = True) -> List[Dict[str, Any]]:
        """Every folder indexed, once each: where the learning reads - the
        user's Work projects' folders, the places they listed and what those
        bring, and their agents' records when listed."""
        seen, out = set(), []

        def add(path: str, **said: Any) -> None:
            key = os.path.normcase(os.path.abspath(path))
            if key in seen or not path:
                return
            seen.add(key)
            out.append({"path": str(Path(path)), **said})
        learning = getattr(self.app, "learning", None)
        if learning is not None:
            try:
                for one in learning.places.index_roots(getattr(self.app, "db", None), wait):
                    add(one["path"], **{k: v for k, v in one.items() if k != "path"})
            except Exception:                       # pragma: no cover - none then
                log.debug("could not read the learning's places", exc_info=True)
        else:
            for one in self.project_folders():
                add(one["path"], why="project", project=one["project"], sessions=False)
        return out

    def never(self) -> List[str]:
        return [str(self.app.paths.data_dir)]

    # -- building -----------------------------------------------------------------------
    def begin(self) -> None:
        """The folders added read, and those taken out forgotten, soon after
        they change - until the application stops. The builds at their
        times are the learning's (`build_now` from its update)."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="local-index", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._halt.is_set():
            self._wake.wait()
            if self._halt.is_set():
                return
            self._wake.clear()
            remaining = self._soon_at - time.time()
            while remaining > 0 and not self._halt.wait(remaining):
                remaining = self._soon_at - time.time()
            if self._halt.is_set():
                return
            self.build_now(wait=True, only_new=True)

    def soon(self) -> None:
        """The folders changed: built again in a moment."""
        self._soon_at = time.time() + AFTER_CHANGE_SECONDS
        self._wake.set()

    def build_now(self, wait: bool = False, only_new: bool = False) -> Dict[str, Any]:
        """A build started - unless one is going - and, with `wait`, its end
        waited for (one going waited for first, then this one). `only_new`:
        the folders taken out forgotten, and those never read yet read - and
        those only while the switch is on."""
        if not fts5_available():
            return {"error": "this Python's SQLite has no full-text search (FTS5)"}
        going = self._child
        if going is not None and going.poll() is None:
            if not wait:
                return {"building": True}
            going.wait()
        with self._lock:
            if self._child is not None and self._child.poll() is None:
                return {"building": True}
            job = self.path.parent / "job.json"
            job.parent.mkdir(parents=True, exist_ok=True)
            # Folders no longer indexed are forgotten first.
            self._forget_dropped()
            roots = self.roots()
            only = None
            if only_new:
                only = self._not_read_yet(roots) if self.enabled() else []
                if not only:
                    return {"building": False}
            job.write_text(json.dumps({"db": str(self.path), "roots": roots,
                                       "never": self.never(), "only": only},
                                      ensure_ascii=False),
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

    def _not_read_yet(self, roots: List[Dict[str, Any]]) -> List[str]:
        """The folders walked that no build has gone through yet."""
        from .build import outermost
        walked, _within = outermost(roots)
        done = set()
        if self.path.exists():
            store = IndexStore(self.path, read_only=True)
            try:
                done = {os.path.normcase(os.path.abspath(one["path"])) for one in store.roots()
                        if one.get("state") == "done"}
            finally:
                store.close()
        return [one["path"] for one in walked
                if os.path.normcase(os.path.abspath(one["path"])) not in done]

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
    def shown(self) -> Dict[str, Any]:
        """HOW FAR A BUILD IS, for the Learning panel's own bar beside the
        learning's (the user, 2026-10-24): the folder being read, the files
        walked of those the last build had (`share`, None when that is not
        known - a folder never built), or the file being made small again;
        when none is going, when the last ended and what it keeps."""
        building = self._child is not None and self._child.poll() is None
        said = dict(self._progress)
        out: Dict[str, Any] = {"building": building}
        if building:
            expected = int(said.get("expected") or 0)
            walked = int(said.get("walked") or 0)
            known = bool(said.get("expected_all")) and expected > 0
            out.update({"root": str(said.get("root") or ""), "walked": walked,
                        "expected": expected if known else 0,
                        "share": min(0.99, walked / expected) if known else None,
                        "tidying": bool(said.get("tidying")),
                        "waiting": str(said.get("waiting") or "")})
            return out
        if self.path.exists():
            store = IndexStore(self.path, read_only=True)
            try:
                ended = [one.get("done_at") for one in store.roots()
                         if one.get("state") == "done" and one.get("done_at")]
                out["done_at"] = (datetime.fromtimestamp(max(ended), timezone.utc).isoformat()
                                  if ended else None)
                out.update(store.counts())
            except Exception:                       # pragma: no cover - being made
                pass
            finally:
                store.close()
        return out

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
        for one in self.roots(wait=False):
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
        records: Dict[str, str] = {}
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
                one["text"] = self._lines(path, int(one["line"] or 1), one.get("kind"), records)
            out.append(one)
        out.sort(key=lambda one: one["rank"])
        return [{"path": one["path"], "line": one.get("line"), "found_in": one["found_in"],
                 "modified": time.strftime("%Y-%m-%d", time.localtime(float(one["mtime"])))
                 if one.get("mtime") else "", **({"text": one["text"]} if "text" in one else {})}
                for one in out[:limit]]

    @staticmethod
    def _lines(path: str, line: int, kind: Optional[str] = None,
               records: Optional[Dict[str, str]] = None) -> str:
        """The piece found, from the file as it is now - a conversation
        record's from what is kept of it (talk.py), read once a search."""
        if kind == "talk":
            records = {} if records is None else records
            if path not in records:
                records[path] = talk_text(Path(path)) or ""
            return lines_of(records[path], line, LINES_PER_PIECE)[:MOST_SHOWN]
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines()
        except OSError:
            return ""
        return "\n".join(lines[line - 1:line - 1 + LINES_PER_PIECE])[:MOST_SHOWN]


__all__ = ["LocalIndex", "INDEX_FLAG", "session_folders"]
