"""WHO WROTE A RULE FILE'S LINES - the user, or an agent - and where an
agent's memory came from: by code, never by the model's say.

A rule the user wrote for their agents (CLAUDE.md, AGENTS.md) is the
strongest evidence there is - one is enough for a reason to hold - so a
line counts as the USER'S only on evidence:

  1. AN AGENT'S EDITS: a line an agent's edit put in that file (a session's
     Write or Edit of it, `/init` among them) is the agent's;
  2. THE HISTORY: in a repository, each line's commit (`git blame`) - one
     signed as made with an agent (a "Co-Authored-By" Claude, Codex,
     Copilot or Cursor line, "Generated with Claude Code") is the agent's,
     any other the user's;
  3. otherwise, where agents did edit the file, a line none of their edits
     put there is the user's (they wrote around what the agent wrote); and
     where nothing is known of the file at all, a line is UNKNOWN - not the
     user's.

An agent's MEMORY of a project (Claude Code's `memory/*.md`) is what the
agent noted from the user's words: it counts as such only when the user's
words it was noted from are found - the last thing the user said before the
agent wrote that file, in the same conversation.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence, Set

from . import records

#: A commit made with an agent, by what it says of itself.
AGENT_COMMIT = re.compile(
    r"(?im)^\s*co-authored-by:.*\b(claude|anthropic|codex|openai|copilot|cursor|chatgpt|gemini)\b"
    r"|generated with \[?claude code|\U0001F916 generated with")


def _same(path_a: str, path_b: str) -> bool:
    try:
        return os.path.normcase(os.path.abspath(path_a)) == os.path.normcase(os.path.abspath(path_b))
    except (TypeError, ValueError):
        return False


def _git(args: Sequence[str], timeout: int = 30) -> str:
    try:
        done = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace",
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def _blame(path: Path) -> Optional[List[str]]:
    """Each line's commit, in order - None when the file is not in a
    repository's history."""
    folder = str(path.parent)
    said = _git(["git", "-C", folder, "blame", "--line-porcelain", "--", path.name])
    if not said:
        return None
    commits: List[str] = []
    current = ""
    for line in said.splitlines():
        if line.startswith("\t"):
            commits.append(current)
        else:
            head = line.split(" ", 1)[0]
            if re.fullmatch(r"[0-9a-f]{40}", head):
                current = head
    return commits


def _agent_commits(path: Path) -> Set[str]:
    said = _git(["git", "-C", str(path.parent), "log", "--format=%H%x1f%B%x1e", "--",
                 path.name])
    out = set()
    for one in said.split("\x1e"):
        if "\x1f" not in one:
            continue
        commit, body = one.split("\x1f", 1)
        if AGENT_COMMIT.search(body):
            out.add(commit.strip())
    return out


def agent_lines(path: Path, sessions: Sequence[records.Session]) -> Set[str]:
    """Every line an agent's edit put in this file, stripped."""
    out: Set[str] = set()
    for session in sessions:
        for edit in session.edits:
            if _same(edit.file, str(path)):
                out.update(line.strip() for line in edit.added.splitlines() if line.strip())
    return out


def line_authors(path: Path, lines: Sequence[str],
                 sessions: Sequence[records.Session]) -> List[str]:
    """"user", "agent" or "unknown" for each line of the file as it is."""
    by_agent = agent_lines(path, sessions)
    blame = _blame(path)
    if blame is not None and len(blame) == len(lines):
        agents = _agent_commits(path)
        return ["agent" if line.strip() in by_agent or commit in agents else
                ("user" if line.strip() else "unknown")
                for line, commit in zip(lines, blame)]
    out = []
    for line in lines:
        if not line.strip():
            out.append("unknown")
        elif line.strip() in by_agent:
            out.append("agent")
        elif by_agent:
            out.append("user")
        else:
            out.append("unknown")
    return out


def memory_source(path: Path, sessions: Sequence[records.Session]) -> str:
    """The user's words an agent's memory file was noted from: the last
    thing the user said before an agent first wrote it - "" when no
    conversation shows it."""
    for session in sessions:
        writes = [edit for edit in session.edits if _same(edit.file, str(path))]
        if not writes:
            continue
        first = min(edit.at for edit in writes)
        said = [turn for turn in session.turns if turn.role == "user" and turn.at <= first]
        if said:
            return said[-1].text
    return ""


def init_made(path: Path, sessions: Sequence[records.Session]) -> bool:
    """Whether an agent's `/init` made this file: an /init in a conversation,
    and that conversation's agent writing the file after it."""
    for session in sessions:
        inits = [at for at, name in session.commands if name.lstrip("/") == "init"]
        if not inits:
            continue
        if any(_same(edit.file, str(path)) and edit.at >= min(inits) for edit in session.edits):
            return True
    return False
