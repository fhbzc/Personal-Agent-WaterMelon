"""THE AGENT'S MEMORY, AS FILES (the user, 2026-11-16: "支持创建新的md file, chat里说的东西记在memory里（和claude 一样的memory
机制)").

One fact a file - Markdown, a few lines of frontmatter (its name, a line saying what it is, its kind) and the fact - in
the data folder's `memory`; and `MEMORY.md` beside them, ONE LINE A FILE, the index the agent is given with every
answer (`index`). The agent writes them itself, from the chat and from what it learns of the chat
(agent/chat_digest.py): who the user is (user), how they want things done and why (feedback), what they are working
on and by when (project), where things are kept (reference). A fact already kept is updated, not written twice; one
found wrong is taken out. Kept on this computer only, never sent anywhere but to the model answering.
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models.base import ToolSpec

log = logging.getLogger(__name__)

KINDS = ("user", "feedback", "project", "reference")
INDEX = "MEMORY.md"
#: The most of the index given with an answer; of one file; files kept.
MOST_INDEX, MOST_FILE, MOST_FILES = 12000, 6000, 400

NOTE = """

[YOUR MEMORY - files you keep about the user and their work (memory_write / memory_read / memory_forget), as \
Claude keeps its own. Its index, one line a file. BEFORE YOU ANSWER, read (memory_read) each file whose line bears \
on what is asked or done now - nothing is read for you. What is kept reflects what was true when it was written - \
check it against what you see now before you rely on it:
{index}
]"""


def slug(name: Any) -> str:
    """A file's name from what the agent calls it: lower case, words joined by "-"."""
    words = re.sub(r"[^\w]+", "-", str(name or "").strip().lower(), flags=re.UNICODE).strip("-")
    return words[:80] or "note"


class MemoryFiles:
    def __init__(self, root: Any) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()

    # -- reading -----------------------------------------------------------------------------------
    def index(self) -> str:
        path = self.root / INDEX
        try:
            return path.read_text(encoding="utf-8")[:MOST_INDEX].strip()
        except OSError:
            return ""

    def note(self) -> str:
        """For the agent's prompt - "" while nothing is kept."""
        kept = self.index()
        return NOTE.format(index=kept) if kept else ""

    def read(self, name: str) -> Optional[str]:
        path = self.root / f"{slug(name)}.md"
        try:
            return path.read_text(encoding="utf-8")[:MOST_FILE]
        except OSError:
            return None

    def names(self) -> List[str]:
        try:
            return sorted(one.stem for one in self.root.glob("*.md") if one.name != INDEX)
        except OSError:
            return []

    # -- writing -----------------------------------------------------------------------------------
    def write(self, name: str, description: str, kind: str, content: str) -> Dict[str, Any]:
        """Kept, or kept again as now said: its file written whole, its line in the index one line."""
        ident = slug(name)
        kind = kind if kind in KINDS else "user"
        description = " ".join(str(description or "").split())[:200]
        content = str(content or "").strip()[:MOST_FILE]
        if not content:
            return {"error": "a memory needs its content"}
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / f"{ident}.md"
            new = not path.exists()
            if new and len(self.names()) >= MOST_FILES:
                return {"error": f"{MOST_FILES} memories are kept already - update or forget one first"}
            path.write_text(f"---\nname: {ident}\ndescription: {description}\nmetadata:\n  type: {kind}\n---\n\n"
                            f"{content}\n", encoding="utf-8")
            self._index_line(ident, description)
        log.info("memory %s: %s", "kept" if new else "kept again", ident)
        return {"kept": ident, "new": new}

    def forget(self, name: str) -> Dict[str, Any]:
        ident = slug(name)
        with self._lock:
            path = self.root / f"{ident}.md"
            if not path.exists():
                return {"error": f"no memory called {ident}"}
            path.unlink()
            self._index_line(ident, None)
        log.info("memory forgotten: %s", ident)
        return {"forgotten": ident}

    def _index_line(self, ident: str, description: Optional[str]) -> None:
        path = self.root / INDEX
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        mark = f"]({ident}.md)"
        lines = [one for one in lines if mark not in one]
        if description is not None:
            title = ident.replace("-", " ").capitalize()
            lines.append(f"- [{title}]({ident}.md) — {description}")
        path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def add_tools(registry: Any, memory: MemoryFiles) -> None:
    for spec, run in tools_of(memory):
        registry.add(spec, run)


def tools_of(memory: MemoryFiles) -> List[Any]:
    """The memory's tools - (spec, what runs it) - for the agent's registry, and for the chat's learning, which
    reads a file before it writes it again (agent/chat_digest.py)."""
    made: List[Any] = []

    class _Made:
        @staticmethod
        def add(spec: Any, run: Any) -> None:
            made.append((spec, run))
    registry = _Made()
    def write(args: Dict[str, Any]) -> Any:
        return memory.write(str(args.get("name") or ""), str(args.get("description") or ""),
                            str(args.get("type") or "user"), str(args.get("content") or ""))

    def read(args: Dict[str, Any]) -> Any:
        found = memory.read(str(args.get("name") or ""))
        return {"content": found} if found is not None else {"error": "no such memory", "kept": memory.names()}

    def forget(args: Dict[str, Any]) -> Any:
        return memory.forget(str(args.get("name") or ""))

    registry.add(ToolSpec(
        "memory_write",
        "Keep something in your memory - a file of its own, listed in your memory's index, which you are given "
        "with every answer from now on. WITHOUT BEING ASKED, whenever the user says something worth knowing next "
        "time: who they are, what they work on and by when, how they want things done and why, where things are. "
        "One fact a file; a fact already kept (see the index) is written again under the same name, never twice. "
        "Not what the files, the calendar or the conversation itself already hold, and nothing only for now. "
        "Lessons on how to do things go with lesson_add as well.",
        {"type": "object", "properties": {
            "name": {"type": "string", "description": "short, lower case, words joined by - ; the same name updates it"},
            "description": {"type": "string", "description": "one line: what it is - the index's line"},
            "type": {"type": "string", "enum": list(KINDS),
                     "description": "user (who they are), feedback (how they want things done, with why), "
                                    "project (work, goals, dates - absolute), reference (where things are)"},
            "content": {"type": "string", "description": "the fact; for feedback and project, then Why: and How to "
                                                         "apply: lines"}},
         "required": ["name", "description", "type", "content"]}), write)
    registry.add(ToolSpec(
        "memory_read",
        "Read one file of your memory, by its name in the index.",
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        read_only=True), read)
    registry.add(ToolSpec(
        "memory_forget",
        "Take a file out of your memory - one found wrong, or one the user asks you to forget.",
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}), forget)
    return made


__all__ = ["MemoryFiles", "add_tools", "tools_of", "KINDS", "slug"]
