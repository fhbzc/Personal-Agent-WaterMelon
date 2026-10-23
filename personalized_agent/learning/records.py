"""THE USER'S CONVERSATIONS WITH CODING AGENTS, and the rules they wrote for
them - the most direct record of what they asked for, sent back and decided.

Two kinds are read:

  - Claude Code: `~/.claude/projects/<folder>/<session>.jsonl`, a line a
    turn. The folder is named after the directory the session was started
    in, but not reversibly - so where a session belongs is read from the
    "cwd" its lines carry, never from the folder's name.
  - Codex: `$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl` (CODEX_HOME is
    ~/.codex by default), a line an event; "session_meta" and
    "turn_context" carry the directory.

From each: the user's own words, in order and whole; the agent's replies,
cut short; and every EDIT the agent made - which file, and the text it put
there - so a change found in a file can be told to be the agent's
(`reading.py`), and only the rest taken as the user's. What the agent read
or edited also says which project folders a session belongs to, when it was
started in one that holds everything (the home folder, the desktop).

And the rules written down for the agents: CLAUDE.md and AGENTS.md in a
project's folders and in the agents' own folders, and Claude Code's memory
of a project (`<folder>/memory/*.md`) - what the agent noted from the
user's words.

Read only, and each file read again only when it changed.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

#: What the harness puts in a user turn that the user did not write.
HARNESS = ("Another Claude session sent a message", "<agent-message",
           "<task-notification", "<system-reminder", "<command-",
           "Caveat: The messages below", "<local-command", "<environment_context>",
           "<user_instructions>", "# AGENTS.md instructions", "<permissions instructions>",
           "[Request interrupted")
#: An agent's reply is kept this long; the user's words whole, up to this.
REPLY_CHARS = 700
USER_CHARS = 6000
#: The text an edit put in a file, kept this long - enough to find it.
EDIT_CHARS = 4000
#: A session file larger than this is read in part (its end): a session
#: that long is mostly the agent's own output.
MOST_BYTES = 60 * 1024 * 1024

EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit", "str_replace_based_edit_tool"}
READ_TOOLS = {"Read", "NotebookRead", "Glob", "Grep"}


@dataclass
class Turn:
    at: str
    role: str                     # user | agent
    text: str


@dataclass
class Edit:
    at: str
    file: str
    added: str                    # the text the agent put in the file


@dataclass
class Session:
    path: str
    tool: str                     # claude | codex
    id: str
    cwd: str = ""
    started: str = ""
    ended: str = ""
    turns: List[Turn] = field(default_factory=list)
    edits: List[Edit] = field(default_factory=list)
    touched: List[str] = field(default_factory=list)   # files read or edited
    #: The commands the user gave the agent ("/init"), each with when.
    commands: List[Tuple[str, str]] = field(default_factory=list)
    #: STARTED BY A PROGRAM, not by a person: Claude Code run headless or
    #: through its SDK (entrypoint "sdk-..."), `codex exec`, a Codex sub-agent.
    #: Its "user" turns are a program's prompts - this application's own
    #: calls, its Work runs, tests, scripts - never the user's words.
    by_program: bool = False

    def folders(self) -> List[str]:
        """The directories the session worked in: where it was started,
        and every folder of a file it read or edited."""
        out = [self.cwd] if self.cwd else []
        for one in self.touched:
            parent = os.path.dirname(one)
            if parent and parent not in out:
                out.append(parent)
        return out


def claude_home() -> Path:
    return Path.home() / ".claude"


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def claude_sessions_root() -> Path:
    return claude_home() / "projects"


def codex_sessions_root() -> Path:
    return codex_home() / "sessions"


def session_files(root: Path, tool: str) -> List[Path]:
    """Every session file under a record folder."""
    if not root.is_dir():
        return []
    try:
        if tool == "claude":
            return sorted(p for folder in root.iterdir() if folder.is_dir()
                          for p in folder.glob("*.jsonl"))
        return sorted(root.rglob("rollout-*.jsonl"))
    except OSError:
        return []


def _harness(text: str) -> bool:
    return text.lstrip().startswith(HARNESS)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for one in content:
            if isinstance(one, dict) and one.get("type") in ("text", "input_text", "output_text"):
                parts.append(str(one.get("text") or ""))
        return "\n".join(part for part in parts if part)
    return ""


def _edits_of_claude(item: Dict[str, Any]) -> List[Tuple[str, str]]:
    """(file, text put there) for one Claude Code tool call."""
    name = item.get("name")
    args = item.get("input") or {}
    if not isinstance(args, dict):
        return []
    path = str(args.get("file_path") or args.get("notebook_path") or args.get("path") or "")
    if name == "Write":
        return [(path, str(args.get("content") or ""))]
    if name == "Edit" or name == "str_replace_based_edit_tool":
        return [(path, str(args.get("new_string") or args.get("new_str") or ""))]
    if name == "MultiEdit":
        return [(path, str(one.get("new_string") or ""))
                for one in args.get("edits") or [] if isinstance(one, dict)]
    if name == "NotebookEdit":
        return [(path, str(args.get("new_source") or ""))]
    return []


#: A command the user gave Claude Code, as its record keeps it.
_COMMAND = re.compile(r"<command-name>\s*(/[^<\s]+)\s*</command-name>")

_PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update) File: (.+)$")


def patch_edits(patch: str) -> List[Tuple[str, str]]:
    """(file, added lines) for an apply_patch text."""
    out: List[Tuple[str, str]] = []
    current, added = "", []
    for line in str(patch or "").splitlines():
        found = _PATCH_FILE.match(line)
        if found or line.startswith("*** End Patch") or line.startswith("*** Delete File"):
            if current:
                out.append((current, "\n".join(added)))
            current, added = (found.group(1).strip() if found else ""), []
            continue
        if current and line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
    if current:
        out.append((current, "\n".join(added)))
    return out


def _edits_of_codex(payload: Dict[str, Any]) -> List[Tuple[str, str]]:
    kind = payload.get("type")
    if kind == "custom_tool_call" and payload.get("name") == "apply_patch":
        return patch_edits(str(payload.get("input") or ""))
    if kind in ("function_call", "local_shell_call"):
        args: Any = payload.get("arguments") or payload.get("action") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {"input": args}
        if not isinstance(args, dict):
            return []
        if payload.get("name") == "apply_patch":
            return patch_edits(str(args.get("input") or args.get("patch") or ""))
        command = args.get("command")
        if isinstance(command, list) and command and "apply_patch" in str(command[0]):
            return patch_edits(str(command[-1]))
        if isinstance(command, str) and "*** Begin Patch" in command:
            return patch_edits(command)
    return []


def _resolved(path: str, cwd: str) -> str:
    if not path:
        return ""
    candidate = Path(path)
    if not candidate.is_absolute() and cwd:
        candidate = Path(cwd) / candidate
    return os.path.normpath(str(candidate))


def _read_claude(path: Path) -> Session:
    session = Session(path=str(path), tool="claude", id=path.stem)
    touched: List[str] = []
    for row in _lines(path):
        stamp = str(row.get("timestamp") or "")
        if not stamp:
            continue
        if row.get("cwd") and not session.cwd:
            session.cwd = str(row["cwd"])
        if str(row.get("entrypoint") or "").startswith("sdk"):
            session.by_program = True
        session.started = session.started or stamp
        session.ended = stamp
        message = row.get("message") or {}
        if not isinstance(message, dict):
            continue
        role = message.get("role") or row.get("type")
        content = message.get("content")
        if role == "user":
            # A tool's result comes back as a user line: not the user's.
            if isinstance(content, list) and any(
                    isinstance(one, dict) and one.get("type") == "tool_result"
                    for one in content):
                continue
            text = _text_of(content).strip()
            command = _COMMAND.search(text)
            if command:
                session.commands.append((stamp, command.group(1).strip()))
            if text and not _harness(text) and not row.get("isMeta"):
                session.turns.append(Turn(stamp, "user", text[:USER_CHARS]))
        elif role == "assistant":
            text = _text_of(content).strip()
            if text:
                session.turns.append(Turn(stamp, "agent", text[:REPLY_CHARS]))
            for item in content if isinstance(content, list) else []:
                if not isinstance(item, dict) or item.get("type") != "tool_use":
                    continue
                for file, added in _edits_of_claude(item):
                    file = _resolved(file, session.cwd)
                    if file:
                        session.edits.append(Edit(stamp, file, added[:EDIT_CHARS]))
                        touched.append(file)
                if item.get("name") in READ_TOOLS:
                    args = item.get("input") or {}
                    file = _resolved(str(args.get("file_path") or args.get("path") or ""),
                                     session.cwd)
                    if file:
                        touched.append(file)
    session.touched = list(dict.fromkeys(touched))
    return session


def _read_codex(path: Path) -> Session:
    session = Session(path=str(path), tool="codex", id=path.stem)
    touched: List[str] = []
    for row in _lines(path):
        stamp = str(row.get("timestamp") or "")
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        kind = row.get("type")
        if kind in ("session_meta", "turn_context"):
            if payload.get("cwd") and not session.cwd:
                session.cwd = str(payload["cwd"])
            if kind == "session_meta" and payload.get("id"):
                session.id = str(payload["id"])
            if kind == "session_meta":
                source = payload.get("source")
                if isinstance(source, dict) or str(source or "") in ("exec", "mcp") \
                        or str(payload.get("originator") or "").startswith("codex_exec"):
                    session.by_program = True
            continue
        if not stamp:
            continue
        session.started = session.started or stamp
        session.ended = stamp
        if kind != "response_item":
            continue
        if payload.get("type") == "message":
            text = _text_of(payload.get("content")).strip()
            role = payload.get("role")
            if not text or _harness(text):
                continue
            if role == "user":
                if re.match(r"/[a-z][\w-]*(\s|$)", text):
                    session.commands.append((stamp, text.split()[0]))
                session.turns.append(Turn(stamp, "user", text[:USER_CHARS]))
            elif role == "assistant":
                session.turns.append(Turn(stamp, "agent", text[:REPLY_CHARS]))
            continue
        for file, added in _edits_of_codex(payload):
            file = _resolved(file, session.cwd)
            if file:
                session.edits.append(Edit(stamp, file, added[:EDIT_CHARS]))
                touched.append(file)
    session.touched = list(dict.fromkeys(touched))
    return session


def _lines(path: Path) -> Iterable[Dict[str, Any]]:
    try:
        size = path.stat().st_size
        with open(path, encoding="utf-8", errors="replace") as handle:
            if size > MOST_BYTES:
                handle.seek(size - MOST_BYTES)
                handle.readline()
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


_KEPT: Dict[str, Tuple[Tuple[int, int], Session]] = {}
_LOCK = threading.Lock()


def read_session(path: Any) -> Optional[Session]:
    """One session file, read again only when it changed since."""
    path = Path(path)
    try:
        info = path.stat()
    except OSError:
        return None
    stamp = (info.st_mtime_ns, info.st_size)
    key = str(path)
    with _LOCK:
        held = _KEPT.get(key)
    if held and held[0] == stamp:
        return held[1]
    tool = "codex" if path.name.startswith("rollout-") else "claude"
    session = _read_codex(path) if tool == "codex" else _read_claude(path)
    with _LOCK:
        _KEPT[key] = (stamp, session)
    return session


def all_sessions(tools: Sequence[str] = ("claude", "codex")) -> List[Session]:
    out = []
    if "claude" in tools:
        out += [s for s in map(read_session, session_files(claude_sessions_root(), "claude")) if s]
    if "codex" in tools:
        out += [s for s in map(read_session, session_files(codex_sessions_root(), "codex")) if s]
    return out


def _temp_roots() -> List[str]:
    import tempfile
    roots = [tempfile.gettempdir(), os.environ.get("TEMP") or "", os.environ.get("TMP") or ""]
    out: List[str] = []
    for one in roots:
        if not one:
            continue
        for form in (one, os.path.realpath(one)):   # a short name, and its long one
            if form not in out:
                out.append(form)
    return out


def in_temp(path: Any) -> bool:
    """Within the system's folder for passing files: what is there is a
    program's, made to be thrown away - never a piece of the user's work."""
    path = str(path or "")
    if not path:
        return False
    forms = [path]
    try:
        if os.path.exists(path):
            forms.append(os.path.realpath(path))
    except (OSError, ValueError):
        pass
    return any(is_within(form, root) for form in forms for root in _temp_roots())


def is_within(path: str, folder: str) -> bool:
    try:
        path_n = os.path.normcase(os.path.abspath(path))
        folder_n = os.path.normcase(os.path.abspath(folder))
    except (TypeError, ValueError):
        return False
    return path_n == folder_n or path_n.startswith(folder_n.rstrip("\\/") + os.sep)


# -- the rules the user wrote down for the agents ---------------------------------
RULE_FILES = ("CLAUDE.md", "AGENTS.md", "CLAUDE.local.md")


def rule_files_in(folder: Any) -> List[Path]:
    """CLAUDE.md / AGENTS.md at the top of a folder and in its .claude."""
    folder = Path(folder)
    out = [folder / name for name in RULE_FILES if (folder / name).is_file()]
    nested = folder / ".claude" / "CLAUDE.md"
    if nested.is_file():
        out.append(nested)
    return out


def global_rule_files() -> List[Path]:
    """The rules every session of an agent is given, wherever it runs."""
    out = [claude_home() / "CLAUDE.md", codex_home() / "AGENTS.md"]
    return [path for path in out if path.is_file()]


def memory_files(session_path: Any) -> List[Path]:
    """Claude Code's memory of the project a session belongs to: the
    `memory` folder beside its sessions."""
    folder = Path(session_path).parent / "memory"
    try:
        return sorted(folder.glob("*.md")) if folder.is_dir() else []
    except OSError:
        return []
