"""THE FOLDER MAP: which folders of a listed place are one piece of work.

Bottom up, by a model:

  1. Each LEAF folder (one with no folders in it) is summarised from what
     is in it - its files' names, sizes and dates, the start of a few of its
     texts, and the first words of the conversations with coding agents that
     worked in it.
  2. Each folder above is judged from its OWN files and its children's
     summaries: which of the children are ONE THING (attempts at it, or the
     small tasks of one larger piece of work) - grouped; and whether the
     folder itself COVERS most of what is in it (by the amount of work, not
     the count of folders), with the children it does not cover named as
     exceptions.
  3. A folder that covers its children is summarised as one, and judged
     again with ITS siblings and parent, and so on up. A folder that does
     not stops it: its groups are units each, and nothing above it can be
     one thing with it.

A Work project's folder is a unit of its own already (`places.py`): it is
never grouped with its siblings, and a folder that covers it leaves it out.

Each folder is kept with a fingerprint of what is in it; only folders that
changed - and the folders above them - are summarised and judged again.
Folders alike in shape (a hundred runs of an experiment) are sampled: a few
summarised, the rest said to be "like these".
"""

from __future__ import annotations

import hashlib
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .scrub import clean

#: How deep below the place folders are looked into, and how many at most.
MOST_DEPTH = 8
MOST_FOLDERS = 3000
#: What a leaf is summarised from.
FILES_SHOWN = 40
HEADS_SHOWN = 3
HEAD_CHARS = 300
#: Leaves summarised in one call; children shown to one judgment.
LEAVES_PER_CALL = 30
CHILDREN_SHOWN = 60
#: More children alike in shape than this, and only `SAMPLED` of them are
#: summarised.
ALIKE_AT = 12
SAMPLED = 4

LEAF_PROMPT = """SAY WHAT EACH OF THESE FOLDERS OF THE USER'S IS.

Each folder is shown with its files (name, size, date), the start of a few
of its texts, and the first words of conversations with coding agents that
worked in it. For each, write ONE sentence of at most 30 words: what piece
of the user's work it holds - the thing it is for (a paper on X, slides
for Y, the code of Z, the data of an experiment on W), and what is in it
(drafts, versions, figures, data). Plain words; never guess past what is
shown.

Return ONLY this JSON object:
{"folders": {"<folder id>": "one sentence", ...}}"""

PARENT_PROMPT = """IS THIS FOLDER ONE PIECE OF WORK - AND WHICH OF WHAT IS IN IT GO TOGETHER?

You are shown a folder of the user's: its own files, and the folders in
it, each with a sentence saying what it is. Some are marked FIXED: a
project the user works on, read on its own - never put in a group, but
you may still say the folder covers it.

  1. GROUPS: which of the folders in it are ONE THING - several attempts
     at the same deliverable, or the small tasks of one larger piece of
     work (the proposal, its figures and its talk, for one grant). A folder
     that is a thing of its own is a group of one. Every folder that is not
     FIXED is in exactly one group.
  2. COVERS: is this folder, as a whole, one piece of work that most of
     what is in it belongs to? Judge by how much of the work is in each,
     not by how many folders there are. A folder that only holds unrelated
     things (a home folder, "research", "projects", "Downloads") does not.
     When it covers most but not all, name the folders it does NOT cover.
  3. SUMMARY: one sentence of at most 30 words - what this folder is, as a
     whole when it covers its contents, or what it holds when it does not.

Return ONLY this JSON object:
{"groups": [["folder id", ...], ...],
 "covers": true or false,
 "exceptions": ["folder id", ...],
 "summary": "one sentence",
 "group_titles": ["a few words for each group, in order", ...]}"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d")


def _shape(name: str) -> str:
    """A folder's name with its numbers and dates taken out: folders of one
    shape are runs of one thing."""
    return re.sub(r"\d+", "#", name.lower())


class Node:
    def __init__(self, path: Path, depth: int) -> None:
        self.path = path
        self.depth = depth
        self.children: List["Node"] = []
        self.files: List[Tuple[str, int, float]] = []
        self.fixed = ""           # the Work project it is, if it is one
        self.sampled_out = False  # one of many alike: not summarised
        self.alike: List[str] = []
        self.alike_paths: List[str] = []
        self.fingerprint = ""


class FolderMap:
    def __init__(self, store: Any, ask: Callable[..., Dict[str, Any]],
                 never: Sequence[Any] = (), fixed: Optional[Dict[str, str]] = None,
                 sessions_in: Optional[Callable[[str], List[Any]]] = None) -> None:
        self.store = store
        self.ask = ask
        self.never = [Path(one) for one in never if one]
        # folder (normcased) -> the Work project's title
        self.fixed = {os.path.normcase(os.path.abspath(k)): v for k, v in (fixed or {}).items()}
        self.sessions_in = sessions_in
        self.calls = 0

    # -- the tree -------------------------------------------------------------
    def _walk(self, root: Path) -> Node:
        from ..index.rules import skipped_folder
        top = Node(root, 0)
        count = 0
        stack = [top]
        while stack:
            node = stack.pop()
            key = os.path.normcase(os.path.abspath(node.path))
            if key in self.fixed and node is not top:
                node.fixed = self.fixed[key]
                continue
            try:
                entries = list(os.scandir(node.path))
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir():
                        path = Path(entry.path)
                        if skipped_folder(path, self.never):
                            continue
                        if node.depth + 1 > MOST_DEPTH or count >= MOST_FOLDERS:
                            continue
                        child = Node(path, node.depth + 1)
                        node.children.append(child)
                        count += 1
                        stack.append(child)
                    elif entry.is_file():
                        info = entry.stat()
                        node.files.append((entry.name, int(info.st_size), info.st_mtime))
                except OSError:
                    continue
            node.children.sort(key=lambda one: one.path.name.lower())
        self._sample(top)
        self._fingerprint(top)
        return top

    def _sample(self, node: Node) -> None:
        by_shape: Dict[str, List[Node]] = defaultdict(list)
        for child in node.children:
            by_shape[_shape(child.path.name)].append(child)
        for shape, alike in by_shape.items():
            if len(alike) > ALIKE_AT:
                keep = alike[:SAMPLED]
                for one in alike[SAMPLED:]:
                    one.sampled_out = True
                keep[0].alike = [one.path.name for one in alike[SAMPLED:]]
                keep[0].alike_paths = [str(one.path) for one in alike[SAMPLED:]]
        for child in node.children:
            if not child.sampled_out:
                self._sample(child)

    def _fingerprint(self, node: Node) -> str:
        digest = hashlib.sha1()
        for name, size, mtime in sorted(node.files):
            digest.update(f"{name}|{size}|{int(mtime)}\n".encode("utf-8", "replace"))
        for child in node.children:
            if child.sampled_out:
                digest.update(f"~{child.path.name}\n".encode("utf-8", "replace"))
            else:
                digest.update(f"{child.path.name}:{self._fingerprint(child)}\n"
                              .encode("utf-8", "replace"))
        node.fingerprint = digest.hexdigest()[:16]
        return node.fingerprint

    # -- what a leaf is -------------------------------------------------------
    def _leaf_text(self, node: Node, number: int) -> str:
        from ..heartbeat.experience.materials import extract_text
        lines = [f"FOLDER f{number}: {node.path.name}  ({node.path})"]
        files = sorted(node.files, key=lambda one: -one[2])
        for name, size, mtime in files[:FILES_SHOWN]:
            lines.append(f"  - {name}  {size} bytes  {_date(mtime)}")
        if len(files) > FILES_SHOWN:
            lines.append(f"  ... and {len(files) - FILES_SHOWN} more files")
        heads = 0
        for name, size, _mtime in files:
            if heads >= HEADS_SHOWN:
                break
            if size > 2_000_000:
                continue
            text = extract_text(node.path / name)
            if text.strip():
                heads += 1
                lines.append(f"  START OF {name}: " + clean(text[:HEAD_CHARS]).replace("\n", " "))
        if node.alike:
            lines.append(f"  (and {len(node.alike)} more folders like this one: "
                         + ", ".join(node.alike[:8]) + ")")
        if self.sessions_in is not None:
            for session in self.sessions_in(str(node.path))[:3]:
                first = next((turn.text for turn in session.turns if turn.role == "user"), "")
                if first:
                    lines.append(f"  A CONVERSATION WITH {session.tool.upper()}: "
                                 + clean(first[:200]).replace("\n", " "))
        return "\n".join(lines)

    def _summarise_leaves(self, leaves: List[Node]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for start in range(0, len(leaves), LEAVES_PER_CALL):
            chunk = leaves[start:start + LEAVES_PER_CALL]
            text = "\n\n".join(self._leaf_text(node, n) for n, node in enumerate(chunk))
            said = self.ask("folders", LEAF_PROMPT, text, 3000, "the summaries")
            self.calls += 1
            folders = said.get("folders") if isinstance(said.get("folders"), dict) else {}
            for n, node in enumerate(chunk):
                summary = str(folders.get(f"f{n}") or "").strip()
                if summary:
                    out[str(node.path)] = summary[:400]
        return out

    # -- the map --------------------------------------------------------------
    def map(self, place: Any) -> List[Dict[str, Any]]:
        """The units of one listed place: each {key, title, summary,
        folders}. Work projects' folders are not among them."""
        root = Path(str(place))
        if not root.is_dir():
            return []
        tree = self._walk(root)
        summaries: Dict[str, str] = {}
        # Leaves first, the ones that changed.
        leaves: List[Node] = []

        def gather(node: Node) -> None:
            if node.fixed or node.sampled_out:
                return
            if not node.children:
                kept = self.store.folder(str(node.path))
                if kept and kept["fingerprint"] == node.fingerprint and kept["summary"]:
                    summaries[str(node.path)] = kept["summary"]
                else:
                    leaves.append(node)
            for child in node.children:
                gather(child)

        gather(tree)
        if leaves:
            fresh = self._summarise_leaves(leaves)
            for node in leaves:
                summary = fresh.get(str(node.path), "")
                if summary:
                    summaries[str(node.path)] = summary
                    self.store.save_folder(str(node.path), str(root), node.fingerprint,
                                           summary, {"whole": True})
        units: List[Dict[str, Any]] = []
        whole, summary = self._judge(tree, summaries, units, str(root))
        if whole:
            units.append({"key": str(tree.path), "title": tree.path.name,
                          "summary": summary, "folders": [str(tree.path)],
                          "leave_out": self._fixed_below(tree)})
        return units

    def _fixed_below(self, node: Node) -> List[str]:
        out = []
        for child in node.children:
            if child.fixed:
                out.append(str(child.path))
            elif not child.sampled_out:
                out += self._fixed_below(child)
        return out

    def _judge(self, node: Node, summaries: Dict[str, str], units: List[Dict[str, Any]],
               place: str) -> Tuple[bool, str]:
        """(whether the folder is one piece of work, its summary); units
        found below it that are not part of it are added to `units`."""
        if node.fixed:
            return False, node.fixed
        if not node.children:
            return True, summaries.get(str(node.path), node.path.name)
        results: List[Tuple[Node, bool, str]] = []
        for child in node.children:
            if child.sampled_out:
                continue
            whole, summary = self._judge(child, summaries, units, place)
            results.append((child, whole, summary))
        kept = self.store.folder(str(node.path))
        if kept and kept["fingerprint"] == node.fingerprint and kept["verdict"].get("judged"):
            verdict = kept["verdict"]
        else:
            verdict = self._ask_parent(node, results)
            self.store.save_folder(str(node.path), place, node.fingerprint,
                                   verdict.get("summary", ""),
                                   dict(verdict, judged=not verdict.get("unjudged")))
        ids = {f"c{n}": child for n, (child, _w, _s) in enumerate(results)}
        by_path = {str(child.path): (whole, summary) for child, whole, summary in results}
        any_split = any(not whole and not child.fixed for child, whole, _s in results)
        covers = bool(verdict.get("covers")) and not any_split
        exceptions = {str(ids[one].path) for one in verdict.get("exceptions") or []
                      if one in ids}
        if covers:
            for path in exceptions:
                whole, summary = by_path.get(path, (False, ""))
                if whole:
                    units.append({"key": path, "title": Path(path).name, "summary": summary,
                                  "folders": [path], "leave_out": []})
            return True, str(verdict.get("summary") or node.path.name)
        # Not one thing: each group of whole children is a unit.
        titles = list(verdict.get("group_titles") or [])
        placed = set()
        for n, group in enumerate(verdict.get("groups") or []):
            members = [str(ids[one].path) for one in group if one in ids
                       and by_path.get(str(ids[one].path), (False, ""))[0]]
            if not members:
                continue
            placed.update(members)
            # The folders sampled out go with the one that stood for them.
            members += [alike for one in group if one in ids for alike in ids[one].alike_paths]
            title = str(titles[n]) if n < len(titles) and titles[n] else Path(members[0]).name
            units.append({"key": "|".join(sorted(members)), "title": title,
                          "summary": " / ".join(by_path[one][1] for one in members)[:600],
                          "folders": members, "leave_out": []})
        for child, whole, summary in results:
            if whole and not child.fixed and str(child.path) not in placed:
                units.append({"key": str(child.path), "title": child.path.name,
                              "summary": summary,
                              "folders": [str(child.path)] + child.alike_paths,
                              "leave_out": []})
        if node.files:
            units.append({"key": str(node.path) + "|files", "title": node.path.name,
                          "summary": f"the files directly in {node.path.name}",
                          "folders": [str(node.path)], "only_files": True, "leave_out": []})
        return False, str(verdict.get("summary") or node.path.name)

    def _ask_parent(self, node: Node, results: List[Tuple[Node, bool, str]]) -> Dict[str, Any]:
        lines = [f"THE FOLDER: {node.path.name}  ({node.path})", "ITS OWN FILES:"]
        for name, size, mtime in sorted(node.files, key=lambda one: -one[2])[:FILES_SHOWN]:
            lines.append(f"  - {name}  {size} bytes  {_date(mtime)}")
        if not node.files:
            lines.append("  (none)")
        lines.append("THE FOLDERS IN IT:")
        for n, (child, whole, summary) in enumerate(results[:CHILDREN_SHOWN]):
            mark = " [FIXED: a project of the user's]" if child.fixed else ""
            mixed = " [holds several unrelated things]" if not whole and not child.fixed else ""
            size = sum(one[1] for one in child.files)
            lines.append(f"  c{n}: {child.path.name}{mark}{mixed} - {clean(summary)[:300]}"
                         f"  ({len(child.files)} files, {size} bytes directly in it)")
            if child.alike:
                lines.append(f"      and {len(child.alike)} more folders like it")
        said = self.ask("folders", PARENT_PROMPT, "\n".join(lines), 2000, "the summary")
        self.calls += 1
        if not said or said.get("_failed"):
            # Unjudged: each child stands alone - nothing joined by a guess.
            return {"groups": [[f"c{n}"] for n in range(len(results))], "covers": False,
                    "exceptions": [], "summary": node.path.name, "unjudged": True}
        return said
