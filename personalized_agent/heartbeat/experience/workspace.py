"""A project's workspace, read for its stages.

States say what the user was doing in a sentence; the work itself is on
disk - the history of the repository, the drafts and final versions, the
figures, and the conversations the user had with their agents in that
folder. So a project with a workspace has its stages written from the
workspace as well as its states, and a folder the user adds by hand - work
from before the agent was listening - from its workspace alone.

The workspace is READ ONLY here, confined to its own folder, and never a
file that holds a secret. And it is read incrementally: each project keeps
when it was last processed, and a pass reads only what changed after that,
up to the stage pass's own cutoff - one freeze for everything.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ...models.base import ToolSpec

log = logging.getLogger(__name__)

#: The most of a file one read returns.
READ_AT_MOST = 30000

#: Folders that are never the user's work.
SKIPPED_FOLDERS = {".git", "node_modules", "__pycache__", ".venv", "venv", "env",
                   ".mypy_cache", ".pytest_cache", ".idea", ".vscode", "dist",
                   "build", ".tox", ".cache", "site-packages", ".ipynb_checkpoints"}

#: Files never read: they hold secrets, whatever their contents look like.
SECRET = re.compile(
    r"(^|[\\/])(\.env(\..*)?|.*\.(pem|key|p12|pfx|kdbx|keystore)|id_(rsa|dsa|ecdsa|ed25519)(\.pub)?|"
    r".*credentials?.*|.*secrets?.*|\.netrc|\.pgpass|.*token.*\.json)$", re.I)

#: The most changed files listed for one pass.
CHANGES_LISTED = 400


def git_date(value: Any) -> str:
    """A date as git should read it. git fills a date given without a time
    with the time of day it is now - "since 5 October" read at four in the
    afternoon is since four that afternoon - so a bare date gets midnight."""
    text = str(value or "").strip()
    return f"{text} 00:00:00" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else text


#: What marks a folder as a project's own root.
ROOT_MARKS = (".git", ".claude", "CLAUDE.md", "README.md", "README.txt", "README",
              "pyproject.toml", "setup.py", "package.json", ".Rproj.user",
              "renv.lock", "Makefile", ".vscode")

#: Folders that hold everything, not one piece of work.
CONTAINERS = ("", "users", "desktop", "documents", "downloads", "dropbox",
              "onedrive", "appdata", "pictures", "videos", "music")


def real_path(place: str) -> Optional[Path]:
    """A place as the tasks keep it - lower case, forward slashes, "-" and
    "_" as one, perhaps "..." in the middle - found on disk as it really is."""
    from .materials import Materials
    text = str(place or "").strip()
    if not re.match(r"^[a-z]:/|^/", text):
        return None
    if "..." in text or "…" in text:
        # Cut short: found whole, or not at all - Windows reads "..." as a
        # folder that exists, and a place that is half a guess is not one.
        found = Materials.whole_path(text.replace("/", os.sep))
        return real_path(found.replace(os.sep, "/").lower()) if found else None
    # A path that exists as written - perhaps in Windows's short form,
    # PROGRA~1 - in its long form first.
    try:
        if Path(text).exists():
            text = str(Path(text).resolve()).replace("\\", "/").lower()
    except OSError:
        pass
    # Segment by segment, matching each as the tasks spell it - so the path
    # comes back as it is really spelt, not in the tasks' lower case.
    parts = [one for one in text.split("/") if one]
    here = Path(parts[0] + os.sep) if re.match(r"^[a-z]:$", parts[0]) else Path("/")
    for part in parts[1:]:
        want = part.replace("-", "_").lower()
        try:
            match = next((child for child in here.iterdir()
                          if child.name.replace("-", "_").lower() == want), None)
        except OSError:
            return None
        if match is None:
            return None
        here = match
    return here


def is_container(path: Path) -> bool:
    """The home folder and the folders that hold everything in it."""
    home = Path.home().resolve()
    try:
        resolved = path.resolve()
    except OSError:
        return True
    if resolved == home or resolved in home.parents or len(resolved.parts) <= 2:
        return True
    try:
        rel = resolved.relative_to(home)
    except ValueError:
        return False
    return len(rel.parts) == 1 and rel.parts[0].lower() in CONTAINERS


def project_root(path: Path) -> Path:
    """The folder a place belongs to as a piece of work: the nearest folder
    at or above it that marks a project - a repository, an agent's settings,
    a README - short of the containers; or the place itself."""
    here = path if path.is_dir() else path.parent
    for candidate in [here, *here.parents]:
        if is_container(candidate):
            break
        if any((candidate / mark).exists() for mark in ROOT_MARKS):
            return candidate
    return here


def unit_id_for(path: Path) -> str:
    """A folder the user added, as a project: an id that is its path's."""
    return "dir-" + hashlib.sha1(str(Path(path).resolve()).lower()
                                  .encode("utf-8")).hexdigest()[:12]


def _iso(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


def _when(value: str) -> Optional[float]:
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return None


class Workspace:
    """One project's folder: what is in it, what changed, and reading it."""

    def __init__(self, root: Path) -> None:
        self.given = Path(root)
        self.root = Path(root).resolve()

    # -- confinement ----------------------------------------------------------
    def inside(self, path: Any) -> Path:
        """A path inside this workspace - relative ones resolved against it -
        or an error: nothing outside the folder is read, and no secret."""
        given = Path(str(path or "."))
        whole = (given if given.is_absolute() else self.root / given).resolve()
        if whole != self.root and self.root not in whole.parents:
            raise PermissionError(f"outside this project's folder: {whole}")
        if SECRET.search(str(whole)):
            raise PermissionError(f"a file that holds secrets is never read: {whole.name}")
        if any(part in SKIPPED_FOLDERS for part in whole.relative_to(self.root).parts
               if part != ".git"):
            pass
        return whole

    # -- what changed ---------------------------------------------------------
    def files_changed(self, since: Optional[str], until: str) -> List[Dict[str, Any]]:
        """Files modified after `since` (all, when None) and by `until`."""
        low = _when(since) if since else None
        high = _when(until) or datetime.now(timezone.utc).timestamp()
        out = []
        for here, folders, files in os.walk(self.root):
            folders[:] = [f for f in folders if f not in SKIPPED_FOLDERS
                          and not f.startswith(".")]
            for name in files:
                path = Path(here) / name
                if SECRET.search(str(path)):
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if stat.st_mtime > high or (low is not None and stat.st_mtime <= low):
                    continue
                out.append({"path": str(path.relative_to(self.root)),
                            "modified": _iso(stat.st_mtime), "bytes": stat.st_size})
        out.sort(key=lambda one: one["modified"], reverse=True)
        return out

    def is_repository(self) -> bool:
        return (self.root / ".git").exists()

    def _git(self, *args: str, timeout: int = 60) -> str:
        from ...common import children
        done = children.run_short(["git", "-C", str(self.root), *args],
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=timeout)
        return done.stdout if done.returncode == 0 else ""

    def commits(self, since: Optional[str], until: str, most: int = 300
                ) -> List[Dict[str, str]]:
        """Commits made after `since` and by `until`, newest first."""
        if not self.is_repository():
            return []
        args = ["log", f"--max-count={most}", "--date=iso-strict",
                "--format=%H%x1f%ad%x1f%s", f"--until={git_date(until)}"]
        if since:
            args.append(f"--since={git_date(since)}")
        out = []
        for line in self._git(*args).splitlines():
            parts = line.split("\x1f")
            if len(parts) == 3:
                out.append({"commit": parts[0][:12], "date": parts[1],
                            "subject": parts[2][:200]})
        return out

    def conversations(self) -> List[Path]:
        """The agent conversations filed under this folder: Claude Code keeps
        each folder's in one directory named after its path."""
        projects = Path.home() / ".claude" / "projects"
        if not projects.is_dir():
            return []
        # Named after the folder as it was opened - the long form or the
        # short one Windows also has - so both are looked for, any case.
        names = {re.sub(r"[^A-Za-z0-9]", "-", str(one)).lower()
                 for one in (self.root, self.given.absolute(), _short(self.root))}
        out = []
        for folder in projects.iterdir():
            low = folder.name.lower()
            if folder.is_dir() and any(low == name or low.startswith(name + "-")
                                       for name in names):
                out += sorted(folder.glob("*.jsonl"))
        return out

    def said_in_conversations(self, since: Optional[str], until: str,
                              most: int = 400) -> List[Dict[str, str]]:
        """What the user asked and the agent answered in this folder's
        conversations, after `since` and by `until` - the user's own words,
        in order. The agent's background runs are not conversations here:
        they are filed under the agent's own workspace, not this folder."""
        out: List[Dict[str, str]] = []
        for path in self.conversations():
            for stamp, role, text in _turns_of(path):
                if role is None and not stamp:
                    raise text
                if stamp > until or (since and stamp <= since):
                    continue
                if role is None:
                    raise text
                out.append({"at": stamp[:19], "session": path.stem[:8],
                            "role": role, "text": text})
        out.sort(key=lambda one: one["at"])
        return out[-most:]

    # -- an overview, for the first pass ----------------------------------------
    def overview(self) -> str:
        """What the folder holds: its top levels, its notes, how much is in it."""
        lines = [f"FOLDER: {self.root}"]
        for here, folders, files in os.walk(self.root):
            depth = len(Path(here).relative_to(self.root).parts)
            folders[:] = sorted(f for f in folders if f not in SKIPPED_FOLDERS
                                and not f.startswith("."))
            if depth >= 2:
                folders[:] = []
            indent = "  " * depth
            shown = sorted(files)[:25]
            lines.append(f"{indent}{Path(here).name}/  ({len(files)} files)")
            lines += [f"{indent}  {name}" for name in shown]
            if len(lines) > 160:
                lines.append("  ...")
                break
        for note in ("README.md", "README.txt", "README", "CLAUDE.md", "NOTES.md"):
            path = self.root / note
            if path.is_file():
                lines += ["", f"--- {note} (start) ---",
                          path.read_text(encoding="utf-8", errors="replace")[:4000]]
        return "\n".join(lines)


class Workspaces:
    """Several folders read as one project's workspace - a project the user
    made of more than one folder. Each keeps its own history and
    conversations; a path is relative to a folder, or starts with the
    folder's name when there is more than one."""

    def __init__(self, roots: Sequence[Any]) -> None:
        self.parts = [Workspace(Path(one)) for one in roots]
        self.root = self.parts[0].root
        self.roots = [part.root for part in self.parts]

    def _part_for(self, path: Any) -> "Workspace":
        given = Path(str(path or "."))
        if given.is_absolute():
            whole = given.resolve()
            for part in self.parts:
                if whole == part.root or part.root in whole.parents:
                    return part
            raise PermissionError(f"outside this project's folders: {whole}")
        head = given.parts[0] if given.parts else ""
        for part in self.parts:
            if head and head.lower() == part.root.name.lower():
                return part
        for part in self.parts:
            if (part.root / given).exists():
                return part
        return self.parts[0]

    def inside(self, path: Any) -> Path:
        part = self._part_for(path)
        given = Path(str(path or "."))
        if not given.is_absolute() and given.parts and \
                given.parts[0].lower() == part.root.name.lower() and \
                not (part.root / given).exists():
            given = Path(*given.parts[1:]) if len(given.parts) > 1 else Path(".")
        return part.inside(given)

    def is_repository(self) -> bool:
        return any(part.is_repository() for part in self.parts)

    def _git(self, *args: str, timeout: int = 60) -> str:
        for part in self.parts:
            if part.is_repository():
                said = part._git(*args, timeout=timeout)
                if said:
                    return said
        return ""

    def files_changed(self, since: Optional[str], until: str) -> List[Dict[str, Any]]:
        out = []
        for part in self.parts:
            for one in part.files_changed(since, until):
                out.append(dict(one, path=f"{part.root.name}/{one['path']}"))
        out.sort(key=lambda one: one["modified"], reverse=True)
        return out

    def commits(self, since: Optional[str], until: str, most: int = 300
                ) -> List[Dict[str, str]]:
        out = []
        for part in self.parts:
            out += [dict(one, folder=part.root.name)
                    for one in part.commits(since, until, most)]
        out.sort(key=lambda one: one["date"], reverse=True)
        return out[:most]

    def conversations(self) -> List[Path]:
        return [one for part in self.parts for one in part.conversations()]

    def said_in_conversations(self, since: Optional[str], until: str,
                              most: int = 400) -> List[Dict[str, str]]:
        out = [one for part in self.parts
               for one in part.said_in_conversations(since, until, most)]
        out.sort(key=lambda one: one["at"])
        return out[-most:]

    def overview(self) -> str:
        return "\n\n".join(part.overview() for part in self.parts)


def open_workspace(paths: Sequence[Any]):
    """One folder as a Workspace; several as one Workspaces."""
    folders = [Path(one) for one in paths if str(one).strip()]
    if not folders:
        raise ValueError("a workspace needs a folder")
    return Workspace(folders[0]) if len(folders) == 1 else Workspaces(folders)


def _short(path: Path) -> Path:
    """A path's short form on Windows (USERS~1 style names), or itself."""
    if os.name != "nt":
        return path
    try:
        import ctypes
        buffer = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, 1024):
            return Path(buffer.value)
    except Exception:
        pass
    return path


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text") or "") for part in content
                         if isinstance(part, dict) and part.get("type") == "text")
    return ""


def _from_the_harness(text: str) -> bool:
    from .materials import _from_the_harness as said_by_harness
    return said_by_harness(text)


#: Each conversation file's turns as last read - (timestamp, role, text) -
#: by path, with the file's modification time and size when it was read.
#: Read again only when one of those changed.
_TURNS: Dict[str, Any] = {}
_TURNS_LOCK = threading.Lock()


def _turns_of(path: Path) -> List[Tuple[str, str, str]]:
    """Every turn of the user's or the agent's in one conversation file, in
    the file's order, whatever its time - the window is chosen by the caller.

    KEPT, AND READ AGAIN ONLY WHEN THE FILE CHANGED. One folder's
    conversations were 232 files, 345 MB: parsing them took six seconds,
    and a pass asked for them in its header and again on every call of the
    conversation tool - up to thirty passes a learning. Claude Code only
    adds to a session's file, so most of them never change again; the ones
    that are still being written are read again whole, exactly as before.
    A file that could not be read to the end is not kept: what was read of
    it is returned, as it was."""
    try:
        info = os.stat(path)
    except OSError:
        return []
    key = str(path)
    stamp = (info.st_mtime_ns, info.st_size)
    with _TURNS_LOCK:
        held = _TURNS.get(key)
    if held is not None and held[0] == stamp:
        return held[1]
    out: List[Tuple[str, str, str]] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                stamp_of = ""
                try:
                    stamp_of = str(row.get("timestamp") or "")
                    if not stamp_of:
                        continue
                    message = row.get("message") or {}
                    role = message.get("role") or row.get("type")
                    text = _text_of(message.get("content"))
                except (AttributeError, TypeError) as exc:
                    # A line that is not a turn's shape failed the reading
                    # when it was in the window; it still does, then.
                    out.append((stamp_of, None, exc))
                    continue
                if role not in ("user", "assistant") or not text.strip():
                    continue
                if role == "user" and _from_the_harness(text):
                    continue
                out.append((stamp_of, role, text[:1500]))
    except OSError:
        return out
    with _TURNS_LOCK:
        _TURNS[key] = (stamp, out)
    return out


# -- the tools a stage writer reads a workspace with ------------------------------------
def _schema(props: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


WORKSPACE_TOOLS = [
    ToolSpec("list_dir", "List a folder of this project: its files and "
             "subfolders. Paths are relative to the project's folder.",
             _schema({"path": {"type": "string"}}, ["path"])),
    ToolSpec("read_file", "Read a file of this project, whole or from a line. "
             "Long files come back in part; ask again with from_line.",
             _schema({"path": {"type": "string"},
                      "from_line": {"type": "integer"}}, ["path"])),
    ToolSpec("find_files", "Find files of this project by name pattern "
             "(glob, e.g. **/*.py), optionally only those containing a text.",
             _schema({"pattern": {"type": "string"},
                      "containing": {"type": "string"}}, ["pattern"])),
    ToolSpec("git_log", "The project's commits, newest first - optionally "
             "only those touching a path, or in a period (ISO dates).",
             _schema({"path": {"type": "string"}, "since": {"type": "string"},
                      "until": {"type": "string"}}, [])),
    ToolSpec("git_show", "One commit: its message, the files it changed, and "
             "its diff (long ones in part).",
             _schema({"commit": {"type": "string"}}, ["commit"])),
    ToolSpec("conversation", "What the user asked and the agent answered in "
             "this project's agent conversations, in a period (ISO dates) - "
             "the user's own words: their asks, verdicts and decisions.",
             _schema({"since": {"type": "string"}, "until": {"type": "string"},
                      "containing": {"type": "string"}}, [])),
]


class WorkspaceTools:
    """The read-only tools of one pass over one workspace."""

    def __init__(self, workspace: Workspace, until: str) -> None:
        self.ws = workspace
        self.until = until

    def call(self, name: str, args: Dict[str, Any]) -> Any:
        try:
            return getattr(self, "_" + name)(args or {})
        except PermissionError as exc:
            return {"error": str(exc)}
        except AttributeError:
            return {"error": f"unknown tool {name}"}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"[:400]}

    def _list_dir(self, args: Dict[str, Any]) -> Any:
        if isinstance(self.ws, Workspaces) and str(args.get("path") or ".") in (".", ""):
            return {"folders": [str(root) for root in self.ws.roots],
                    "note": "start a path with a folder's name to read in it"}
        where = self.ws.inside(args.get("path") or ".")
        if not where.is_dir():
            return {"error": f"not a folder: {where}"}
        entries = []
        for child in sorted(where.iterdir())[:300]:
            if child.name in SKIPPED_FOLDERS or SECRET.search(str(child)):
                continue
            try:
                entries.append({"name": child.name, "folder": child.is_dir(),
                                "bytes": child.stat().st_size if child.is_file() else None,
                                "modified": _iso(child.stat().st_mtime)})
            except OSError:
                continue
        return {"path": str(where), "entries": entries}

    def _read_file(self, args: Dict[str, Any]) -> Any:
        where = self.ws.inside(args.get("path"))
        if not where.is_file():
            return {"error": f"not a file: {where}"}
        from .materials import extract_text, DOCUMENTS
        if where.suffix.lower() in DOCUMENTS:
            text = extract_text(where)
        else:
            text = where.read_text(encoding="utf-8", errors="replace")
        start = max(0, int(args.get("from_line") or 1) - 1)
        body = "\n".join(text.splitlines()[start:])
        return {"path": str(where), "from_line": start + 1,
                "text": body[:READ_AT_MOST], "cut": len(body) > READ_AT_MOST}

    def _find_files(self, args: Dict[str, Any]) -> Any:
        pattern = str(args.get("pattern") or "*")
        needle = str(args.get("containing") or "")
        found = []
        for path in self.ws.root.glob(pattern):
            if len(found) >= 200:
                break
            if not path.is_file() or SECRET.search(str(path)) or any(
                    part in SKIPPED_FOLDERS for part in path.relative_to(self.ws.root).parts):
                continue
            if needle:
                try:
                    if needle not in path.read_text(encoding="utf-8", errors="ignore"):
                        continue
                except OSError:
                    continue
            found.append(str(path.relative_to(self.ws.root)))
        return {"files": found}

    def _git_log(self, args: Dict[str, Any]) -> Any:
        if not self.ws.is_repository():
            return {"error": "this folder is not a git repository"}
        until = min(str(args.get("until") or self.until), self.until)
        gitargs = ["log", "--max-count=200", "--date=iso-strict",
                   "--format=%h%x1f%ad%x1f%s", f"--until={git_date(until)}"]
        if args.get("since"):
            gitargs.append(f"--since={git_date(args['since'])}")
        out = []
        for part in getattr(self.ws, "parts", [self.ws]):
            if not part.is_repository():
                continue
            mine = list(gitargs)
            if args.get("path"):
                where = self.ws.inside(args["path"])
                if part.root not in where.parents and where != part.root:
                    continue
                mine += ["--", str(where.relative_to(part.root))]
            rows = [line.split("\x1f") for line in part._git(*mine).splitlines()]
            out += [{"commit": r[0], "date": r[1], "subject": r[2],
                     **({"folder": part.root.name} if isinstance(self.ws, Workspaces)
                        else {})} for r in rows if len(r) == 3]
        out.sort(key=lambda one: one["date"], reverse=True)
        return {"commits": out[:200]}

    def _git_show(self, args: Dict[str, Any]) -> Any:
        sha = str(args.get("commit") or "")
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", sha):
            return {"error": "a commit is its hash"}
        shown = self.ws._git("show", "--stat", "--patch", "--date=iso-strict",
                             "--format=%H%n%ad%n%n%s%n%n%b", sha)
        return {"commit": sha, "text": shown[:READ_AT_MOST],
                "cut": len(shown) > READ_AT_MOST}

    def _conversation(self, args: Dict[str, Any]) -> Any:
        until = min(str(args.get("until") or self.until), self.until)
        said = self.ws.said_in_conversations(args.get("since"), until, most=2000)
        needle = str(args.get("containing") or "").lower()
        if needle:
            said = [one for one in said if needle in one["text"].lower()]
        return {"turns": said[-120:], "more_before": max(0, len(said) - 120)}
