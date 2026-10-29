"""BUILDING THE INDEX: each folder walked, and only what changed read again.

A file is read again when its size or its time changed - or when the
rules of what is read changed and it is now kept differently; a file gone
is forgotten; a folder walked to its end is marked done, with how many
files it holds.

EVERY FOLDER WALKED ONCE, AND ONLY ONCE: a folder inside another one
indexed is walked with it, not again on its own (`outermost`). Walked on
its own as well, each of its files was taken as new by each walk, and read
twice at every build. What is read is cut into pieces of LINES_PER_PIECE lines, each
kept as its words. The walk yields often - a pause between files - and
stops at once when it is told to (`going`); started again, it does not
read again what it read before, so a stop costs nothing.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .rules import (LINES_PER_PIECE, MOST_SESSION_BYTES, RULES_VERSION, how_kept, read_text,
                    skipped_folder)
from .store import IndexStore
from .talk import talk_kept, where_lines_are
from .tokens import body

#: Files between commits: what a stop may have to read again.
COMMIT_EVERY = 200
#: Files walked between two words of how far it is.
TELL_EVERY = 500
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


def _key(path: Any) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _inside(key: str, outer: str) -> bool:
    return key == outer or key.startswith(outer.rstrip(os.sep) + os.sep)


def outermost(roots: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """The folders walked - none inside another - in the order given, and
    each one left out with the folder it is walked in."""
    walked: List[Dict[str, Any]] = []
    within: Dict[str, str] = {}
    for one in sorted(roots, key=lambda one: len(_key(one["path"]))):
        key = _key(one["path"])
        outer = next((other for other in walked if _inside(key, _key(other["path"]))), None)
        if outer is None:
            walked.append(one)
        elif key != _key(outer["path"]):
            within[str(Path(one["path"]))] = str(Path(outer["path"]))
    order = {id(one): n for n, one in enumerate(roots)}
    walked.sort(key=lambda one: order[id(one)])
    return walked, within


def build(store: IndexStore, roots: List[Dict[str, Any]], never: Iterable[Path] = (),
          going: Callable[[], bool] = lambda: True,
          told: Optional[Callable[[Dict[str, Any]], None]] = None,
          only: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Every folder brought up to date. `roots`: {"path", "sessions"} each;
    `never`: the places never gone into (the agent's own data); `only`:
    those folders alone (folders just added). Returns what it did."""
    never = [Path(one) for one in never]
    said = {"read": 0, "kept_name": 0, "unchanged": 0, "forgotten": 0, "stopped": False}
    walked, within = outermost(roots)
    # A record is a record by the folder it is in, whichever folder walks it.
    records = [_key(one["path"]) for one in roots if one.get("sessions")]
    chosen = None if only is None else {_key(one) for one in only}
    # The rules changed since the last build: each file kept is looked at again.
    recheck = store.meta("rules") != str(RULES_VERSION)
    # Conversation records kept before each piece was kept with where in
    # the file it starts (talk.py): read again once, each.
    talk_again = store.meta("talk_at") != "1"
    for inner in within:
        store.mark(inner, state="within", files=0, texts=0, done_at=time.time())
    store.commit()
    going_through = [one for one in walked
                     if chosen is None or _key(one["path"]) in chosen]
    # HOW FAR IT IS: the files each folder had at its last build are what
    # this one expects to walk - none known for a folder never built.
    before = {_key(row["path"]): int(row["files"] or 0) for row in store.roots()}
    expected = [before.get(_key(one["path"]), 0) for one in going_through]
    if told:
        told({"expected": sum(expected), "expected_all": all(expected), "walked": 0})
    walked_so_far = 0
    for root_said in going_through:
        root = Path(root_said["path"])
        key = str(root)
        if told:
            told({"root": key, "walked": walked_so_far})
        if not root.is_dir():
            store.mark(key, state="missing")
            store.commit()
            continue
        store.mark(key, state="building", started_at=time.time())
        store.commit()
        known = store.known_under(key)
        seen = set()
        files = texts = 0
        for count, (path, info) in enumerate(walk(root, never), start=1):
            if not going():
                said["stopped"] = True
                store.commit()
                return said
            if count % PAUSE_EVERY == 0:
                time.sleep(PAUSE_SECONDS)
            walked_so_far += 1
            if told and walked_so_far % TELL_EVERY == 0:
                told({"root": key, "walked": walked_so_far})
            record = bool(records) and any(_inside(_key(path), one) for one in records)
            kind = how_kept(path, info, sessions=record)
            if not kind:
                continue
            text_path = str(path)
            seen.add(text_path)
            files += 1
            texts += kind != "name"
            held = known.get(text_path)
            if held is not None and held[1] == info.st_size and abs(
                    (held[2] or 0) - info.st_mtime) < 1e-6 and (not recheck or held[3] == kind) \
                    and not (talk_again and kind == "talk"):
                if held[4] != key:
                    store.move(held[0], key)
                said["unchanged"] += 1
                continue
            pieces: List[Tuple[Any, ...]] = []
            if kind == "talk":
                kept = talk_kept(path, most=MOST_SESSION_BYTES)
                if kept is None:
                    kind = "name"
                else:
                    # Each piece with where in the file it starts.
                    pieces = [(line, words, *where_lines_are(kept[1], line))
                              for line, words in pieces_of(kept[0])]
            elif kind != "name":
                text = read_text(path)
                if text is None:
                    kind = "name"
                else:
                    pieces = pieces_of(text)
            store.put(text_path, key, info.st_size, info.st_mtime, kind, pieces,
                      name_of(path, root))
            said["kept_name" if kind == "name" else "read"] += 1
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
            told({"root": key, "files": files, "done": True, "walked": walked_so_far, **said})
    if chosen is None:
        store.set_meta("rules", str(RULES_VERSION))
        store.set_meta("talk_at", "1")
        store.commit()
    return said


__all__ = ["build", "walk", "pieces_of", "name_of", "outermost"]
