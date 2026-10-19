"""BUILDING THE INDEX: each folder walked, and only what changed read again.

A file is read again when its size or its time changed; a file gone is
forgotten; a folder walked to its end is marked done, with how many files
it holds. What is read is cut into pieces of LINES_PER_PIECE lines, each
kept as its words. The walk yields often - a pause between files - and
stops at once when it is told to (`going`); started again, it does not
read again what it read before, so a stop costs nothing.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .rules import LINES_PER_PIECE, how_kept, read_text, skipped_folder
from .store import IndexStore
from .tokens import body

#: Files between commits: what a stop may have to read again.
COMMIT_EVERY = 200
#: A short pause every this many files, so the walk never holds the disk.
PAUSE_EVERY = 50
PAUSE_SECONDS = 0.05


def pieces_of(text: str) -> List[Tuple[int, str]]:
    """The words of each LINES_PER_PIECE lines, with the line they start at
    (from 1)."""
    lines = text.splitlines()
    return [(start + 1, body("\n".join(lines[start:start + LINES_PER_PIECE])))
            for start in range(0, len(lines), LINES_PER_PIECE)]


def name_of(path: Path, root: Path) -> str:
    """What a file is found by, besides its words: its name, and the names
    of the folders between it and the folder indexed."""
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = (path.name,)
    return " ".join(parts).replace("_", " ").replace("-", " ").replace(".", " ")


def walk(root: Path, never: Iterable[Path]) -> Iterable[Tuple[Path, os.stat_result]]:
    """Every file under a folder, its subfolders too, but those left alone
    (rules.skipped_folder); links are not followed."""
    never = list(never)
    stack = [root]
    while stack:
        folder = stack.pop()
        try:
            with os.scandir(folder) as entries:
                listed = list(entries)
        except OSError:
            continue
        for entry in listed:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    path = Path(entry.path)
                    if not skipped_folder(path, never):
                        stack.append(path)
                elif entry.is_file(follow_symlinks=False):
                    yield Path(entry.path), entry.stat(follow_symlinks=False)
            except OSError:
                continue


def build(store: IndexStore, roots: List[Dict[str, Any]], never: Iterable[Path] = (),
          going: Callable[[], bool] = lambda: True,
          told: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
    """Every folder brought up to date. `roots`: {"path", "sessions"} each;
    `never`: the places never gone into (the agent's own data). Returns
    what it did."""
    never = [Path(one) for one in never]
    said = {"read": 0, "kept_name": 0, "unchanged": 0, "forgotten": 0, "stopped": False}
    for root_said in roots:
        root = Path(root_said["path"])
        key = str(root)
        if not root.is_dir():
            store.mark(key, state="missing")
            store.commit()
            continue
        sessions = bool(root_said.get("sessions"))
        store.mark(key, state="building", started_at=time.time())
        store.commit()
        known = store.known(key)
        seen = set()
        files = texts = 0
        for count, (path, info) in enumerate(walk(root, never), start=1):
            if not going():
                said["stopped"] = True
                store.commit()
                return said
            if count % PAUSE_EVERY == 0:
                time.sleep(PAUSE_SECONDS)
            kind = how_kept(path, info, sessions=sessions)
            if not kind:
                continue
            text_path = str(path)
            seen.add(text_path)
            files += 1
            texts += kind == "text"
            held = known.get(text_path)
            if held is not None and held[1] == info.st_size and abs(
                    (held[2] or 0) - info.st_mtime) < 1e-6:
                said["unchanged"] += 1
                continue
            pieces: List[Tuple[int, str]] = []
            if kind == "text":
                text = read_text(path)
                if text is None:
                    kind = "name"
                else:
                    pieces = pieces_of(text)
            store.put(text_path, key, info.st_size, info.st_mtime, kind, pieces,
                      name_of(path, root))
            said["read" if kind == "text" else "kept_name"] += 1
            if (said["read"] + said["kept_name"]) % COMMIT_EVERY == 0:
                store.mark(key, files=files, texts=texts)
                store.commit()
                if told:
                    told({"root": key, "files": files, **said})
        for gone in set(known) - seen:
            store.drop(known[gone][0])
            said["forgotten"] += 1
        store.mark(key, files=files, texts=texts, state="done", done_at=time.time())
        store.commit()
        if told:
            told({"root": key, "files": files, "done": True, **said})
    return said


__all__ = ["build", "walk", "pieces_of", "name_of"]
