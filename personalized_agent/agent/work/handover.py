"""HANDING OVER FROM OUTSIDE THE PAGE: Explorer's "Delegate work".

Explorer starts this program once for each item chosen (`--delegate PATH`).
The first of them leaves a note in the agent's folder and waits a moment for
the rest, which add their item to it; then it opens the page - starting the
agent first if it is not running - with the note's id, and the page asks the
agent to make a piece of work of it (`take`), which it shows in the Delegate
work box: context collected, a plan to check.

The note holds only paths, and is taken - read and removed - by the agent
that the page signed in to; nothing is handed to anyone else.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: Items chosen together arrive within this long of each other.
GATHER_SECONDS = 1.5
#: A note not taken in this long is stale - the page was never opened.
STALE_SECONDS = 3600


def inbox(data_dir: Path) -> Path:
    return Path(data_dir) / "delegate_inbox"


class _Lock:
    """One process at a time over the notes."""

    def __init__(self, folder: Path) -> None:
        self.path = folder / ".lock"

    def __enter__(self) -> "_Lock":
        for _attempt in range(200):
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > 10:
                        self.path.unlink()     # left by a process that died
                except OSError:
                    pass
                time.sleep(0.02)
        raise TimeoutError("the hand-over notes are busy")

    def __exit__(self, *_exc: Any) -> None:
        os.close(self.fd)
        try:
            self.path.unlink()
        except OSError:
            pass


def hand_over(data_dir: Path, path: str) -> Tuple[str, bool]:
    """The item kept in a note - the one being gathered, if there is one, or
    a new one. Its id, and whether this process made it (and so opens the
    page)."""
    folder = inbox(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    item = str(Path(path).resolve()) if path else ""
    with _Lock(folder):
        now = time.time()
        for note in sorted(folder.glob("*.json")):
            try:
                said = json.loads(note.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if now - float(said.get("at") or 0) > STALE_SECONDS:
                note.unlink(missing_ok=True)
                continue
            if said.get("gathering") and now - float(said.get("at") or 0) < GATHER_SECONDS * 2:
                if item and item not in said["paths"]:
                    said["paths"].append(item)
                    note.write_text(json.dumps(said, ensure_ascii=False), encoding="utf-8")
                return said["id"], False
        ident = uuid.uuid4().hex[:16]
        (folder / f"{ident}.json").write_text(json.dumps(
            {"id": ident, "at": now, "gathering": True, "paths": [item] if item else []},
            ensure_ascii=False), encoding="utf-8")
        return ident, True


def gathered(data_dir: Path, ident: str) -> None:
    """The moment for the rest passed: the note is complete."""
    folder = inbox(data_dir)
    with _Lock(folder):
        note = folder / f"{ident}.json"
        try:
            said = json.loads(note.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        said["gathering"] = False
        note.write_text(json.dumps(said, ensure_ascii=False), encoding="utf-8")


def take(data_dir: Path, ident: str) -> Optional[Dict[str, List[str]]]:
    """The note read and removed: its folders and its files. None for no
    such note."""
    folder = inbox(data_dir)
    if not ident or not all(ch.isalnum() for ch in ident):
        return None
    with _Lock(folder):
        note = folder / f"{ident}.json"
        try:
            said = json.loads(note.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        note.unlink(missing_ok=True)
    folders, files = [], []
    for one in said.get("paths") or []:
        (folders if Path(one).is_dir() else files).append(one)
    return {"folders": folders, "files": files}


__all__ = ["hand_over", "gathered", "take", "inbox", "GATHER_SECONDS"]
