"""Where a conversation's files are kept: `workspace/Chat/<its title>/`.

A file the chat writes used to land in the agent's own folder, beside every
other conversation's, with nothing to say which conversation made it. Each
conversation now has a folder of its own, named as the chat lists it - its
title, or the first thing the user said in it - so what one asked for is
found where they would look.

CHOSEN ONCE: the folder is made the first time the conversation writes, and
remembered with it (`conversations.folder`). A title changed later does not
move it, and does not split its files over two folders. A second
conversation of the same name is told apart by the date and time, as
delegated work is (`delegate.output_folder_for`).

PER CALL, NOT PER PROCESS: each tool call is given its own conversation's
folder (`place`), so two conversations answered at once never write into
each other's.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

#: The folder in the workspace that holds every conversation's.
CHAT_DIR = "Chat"

#: The tools whose relative paths mean this conversation's folder.
PLACED_TOOLS = ("write_file", "read_file")


def chat_root(workspace: Path) -> Path:
    return Path(workspace) / CHAT_DIR


def grant_chat(policy: Any) -> Any:
    """The chat's policy given `workspace/Chat` to write in, beside the
    agent's own folder - made if it is not there. Work runs build policies of
    their own and are never given it."""
    folder = chat_root(policy.workspace_root)
    folder.mkdir(parents=True, exist_ok=True)
    folder = folder.resolve()
    if folder not in [Path(one).resolve() for one in policy.extra_writable]:
        policy.extra_writable.append(folder)
    return policy


class ConversationFolder:
    """One conversation's folder - made when it first writes."""

    def __init__(self, conversations: Any, conversation_id: str, workspace: Path) -> None:
        self.conversations = conversations
        self.conversation_id = conversation_id
        self.root = chat_root(workspace)

    def existing(self) -> Optional[Path]:
        held = self.conversations.folder_of(self.conversation_id)
        return Path(held) if held else None

    def name(self) -> str:
        """As the chat lists it: its title, or the first thing said in it."""
        row = self.conversations.conversation(self.conversation_id) or {}
        said = str(row.get("title") or "").strip() \
            or self.conversations.first_said(self.conversation_id)
        said = re.sub(r"\s+", " ", said)[:60].strip(" .")
        return said or f"Conversation {datetime.now():%Y-%m-%d %H%M}"

    def ensure(self) -> Path:
        held = self.existing()
        if held is not None:
            held.mkdir(parents=True, exist_ok=True)
            return held
        from ..work.delegate import output_folder_for
        name = self.name()
        for _attempt in range(20):
            folder = output_folder_for(self.root, name)
            try:
                # Made, not merely found free: two conversations of one name
                # writing at the same moment must not take the same folder.
                folder.mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                continue
            self.conversations.set_folder(self.conversation_id, str(folder))
            return folder
        raise OSError(f"could not make a folder for this conversation in {self.root}")


def place(name: str, arguments: Dict[str, Any],
          folder: Optional[ConversationFolder]) -> Dict[str, Any]:
    """A tool call's relative path, made one in the conversation's folder:
    always for a write (the folder made if need be); for a read, when the
    file is there - else it is read as before."""
    if folder is None or name not in PLACED_TOOLS:
        return arguments
    path = str(arguments.get("path") or "").strip()
    if not path or os.path.isabs(path) or path.startswith("~") or re.match(r"^[A-Za-z]:", path):
        return arguments
    if name == "write_file":
        return {**arguments, "path": str(folder.ensure() / path)}
    held = folder.existing()
    if held is not None and (held / path).exists():
        return {**arguments, "path": str(held / path)}
    return arguments


__all__ = ["CHAT_DIR", "ConversationFolder", "chat_root", "grant_chat", "place"]
