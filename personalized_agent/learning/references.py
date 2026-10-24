"""THE USER'S PAST WORK AS A REFERENCE for a deliverable of this work: a file
or folder of theirs the work agent is pointed to - the README whose sections
it follows, the report whose layout it keeps, the project whose folders it
lays out alike - with what to take from it (the user, 2026-10-23).

The candidates are where the deliverables of the cases most like this job
are (`Finder.similar_cases`) - the cases left out never among them, nor a
place in a folder left out; the newest of a deliverable, a final one before
a draft; a few of each case, each with its first lines (a folder: its first
two levels). A call of their own - not the reviewer's own context, which
every one of its calls carries again - is shown the deliverables and the
candidates, may read any candidate whole, and gives each deliverable at most
`PER_CARD` - or none: a reference that does not fit is worse than none.
What to take from it, the form alone or some of the content, it says.

What it gives is checked before the work agent sees it: there, readable by
that agent, not in a folder left out. One that is not is dropped, never
shown as a path it cannot open. Nothing here is learned.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence

#: Candidates shown, at most; of one case; the first characters of each.
CANDIDATES = 20
PER_CASE = 3
EXCERPT = 1000
#: References given one deliverable, at most; the tool rounds of the call.
PER_CARD = 2
READS = 8
#: What a file's first lines can be read of; folders not walked into.
TEXT_SUFFIXES = {".md", ".txt", ".rst", ".py", ".r", ".rmd", ".qmd", ".tex", ".bib",
                 ".html", ".htm", ".css", ".js", ".ts", ".tsx", ".jsx", ".json",
                 ".yml", ".yaml", ".toml", ".ini", ".cfg", ".csv", ".sql", ".sh",
                 ".ps1", ".bat", ".ipynb"}
SKIPPED = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode",
           "dist", "build", ".next", ".mypy_cache", ".pytest_cache"}
TREE_LINES = 40

PROMPT = """THE USER'S PAST WORK AS REFERENCES FOR THIS WORK'S DELIVERABLES.

Below are the deliverables of a piece of work, and files and folders of the
user's from past work like it. For each deliverable, choose AT MOST {per_card}
that the work agent should look at while making it - one whose form,
structure or content is worth following for that deliverable - and say in
one sentence what to take from it: its form alone (the sections of a README,
the layout of a report, how a project's folders are laid out), or parts of
its content too. You decide which, by what fits.

Choose none for a deliverable when none fits it: a reference that does not
fit misleads, and is worse than none. A candidate's first lines are shown;
read one whole with read_file, or look into a folder with list_dir, before
you choose it if its first lines do not settle it.

Return ONLY this JSON object:
{{"cards": {{"<card id>": [{{"path": "...", "take": "one sentence"}}], ...}}}}"""


def _newest_first(places: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Of each path its newest mention; a final version before a draft."""
    by_path: Dict[str, Dict[str, Any]] = {}
    for one in places:
        path = str(one.get("path") or "").strip()
        if path and str(one.get("at") or "") >= str((by_path.get(path) or {}).get("at") or ""):
            by_path[path] = one
    return sorted(by_path.values(), key=lambda one: (
        "draft" in str(one.get("what") or "").lower(), str(one.get("at") or "")[::-1]))


def candidates(finder: Any, facets: Dict[str, List[str]],
               readable: Callable[[str], bool]) -> List[Dict[str, Any]]:
    """Where the deliverables of the cases most like this job are, that the
    work agent may read - most alike first."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for case in finder.similar_cases(facets):
        places = [one for one in case.get("locations") or [] if isinstance(one, dict)
                  and not finder.leave_out.location_out(one)]
        taken = 0
        for one in _newest_first(places):
            path = Path(str(one.get("path") or "")).expanduser()
            key = str(path).lower()
            if key in seen or finder.leave_out.touches(str(path)):
                continue
            try:
                there = path.exists()
            except OSError:
                there = False
            if not there or not readable(str(path)):
                continue
            seen.add(key)
            out.append({"path": str(path), "what": str(one.get("what") or ""),
                        "at": str(one.get("at") or "")[:10], "case": case["id"],
                        "project": str(case.get("title") or "")})
            taken += 1
            if len(out) >= CANDIDATES:
                return out
            if taken >= PER_CASE:
                break
    return out


def excerpt(path: str) -> str:
    """A candidate's first lines - a folder's first two levels."""
    where = Path(path)
    try:
        if where.is_dir():
            lines: List[str] = []
            for child in sorted(where.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
                if child.name in SKIPPED or child.name.startswith("."):
                    continue
                lines.append(child.name + ("/" if child.is_dir() else ""))
                if child.is_dir():
                    for inner in sorted(child.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))[:8]:
                        if inner.name not in SKIPPED and not inner.name.startswith("."):
                            lines.append("  " + inner.name + ("/" if inner.is_dir() else ""))
                if len(lines) >= TREE_LINES:
                    lines.append("  ...")
                    break
            return "\n".join(lines) or "(an empty folder)"
        if where.suffix.lower() in TEXT_SUFFIXES:
            with where.open("r", encoding="utf-8", errors="replace") as handle:
                text = handle.read(EXCERPT)
            return text + ("\n..." if len(text) >= EXCERPT else "")
    except OSError as exc:
        return f"(could not be read: {exc.__class__.__name__})"
    return f"(a {where.suffix or 'binary'} file: open it with read_file)"


def content(cards: Sequence[Dict[str, Any]], found: Sequence[Dict[str, Any]]) -> str:
    """What the call is shown: the deliverables, then each candidate."""
    lines = ["THE DELIVERABLES:"]
    for card in cards:
        lines.append(f"  {card['id']}: {card.get('title', '')} - {str(card.get('statement') or '')[:500]}")
    lines += ["", "THE CANDIDATES (the user's past work, most like this job first):"]
    for n, one in enumerate(found, 1):
        lines.append(f"\n[{n}] {one['path']}")
        lines.append(f"    {one['what'] or 'deliverable'} of \"{one['project']}\" ({one['case']}), {one['at']}")
        lines += ["    " + line for line in excerpt(one["path"]).splitlines()]
    return "\n".join(lines)


def checked(said: Any, cards: Sequence[Dict[str, Any]], readable: Callable[[str], bool],
            leave_out: Any) -> Dict[str, List[Dict[str, str]]]:
    """What the call gave, as the work agent is to see it: for each
    deliverable at most `PER_CARD`, each there, readable by that agent and
    in no folder left out."""
    given = said.get("cards") if isinstance(said, dict) and isinstance(said.get("cards"), dict) else {}
    out: Dict[str, List[Dict[str, str]]] = {}
    for card in cards:
        kept: List[Dict[str, str]] = []
        for one in given.get(card["id"]) or []:
            if not isinstance(one, dict):
                continue
            path = str(one.get("path") or "").strip()
            take = " ".join(str(one.get("take") or "").split())[:300]
            if not path or not take or any(path == k["path"] for k in kept):
                continue
            try:
                there = Path(path).expanduser().exists()
            except OSError:
                there = False
            if there and readable(path) and not leave_out.touches(path):
                kept.append({"path": path, "take": take})
            if len(kept) >= PER_CARD:
                break
        out[card["id"]] = kept
    return out


def shown(references: Any) -> List[str]:
    """A card's references as its lines read."""
    return [f"    look at: {one.get('path')} - {one.get('take')}"
            for one in references or [] if isinstance(one, dict) and one.get("path")]


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
