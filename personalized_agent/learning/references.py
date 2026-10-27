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
candidates, may read any candidate whole, and PROPOSES for each deliverable
at most `PROPOSED`, each with what it is, why it fits and what might be
taken. The reviewer CHOOSES of those, in its own context (the user,
2026-10-24) - at most `PER_CARD` a deliverable, or none: a reference that
does not fit is worse than none - with what to take, the form alone or some
of the content, and what not to.

What it gives is checked before the work agent sees it: there, readable by
that agent, not in a folder left out. One that is not is dropped, never
shown as a path it cannot open. Nothing here is learned.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

#: Candidates shown, at most; of one case; the first characters of each.
CANDIDATES = 20
PER_CASE = 3
EXCERPT = 1000
#: Of a passage kept, what is shown, at most.
PASSAGE = 1500
#: References proposed for one deliverable, given it, at most; the tool
#: rounds of a call.
PROPOSED = 3
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
user's from past work like it. For each deliverable, propose AT MOST
{per_card} that the work agent might look at while making it - one whose
form, structure or content may be worth following for that deliverable. For
each: what it is, in a sentence or two; why it fits the deliverable; and
what might be taken from it - its form alone (the sections of a README, the
layout of a report, how a project's folders are laid out), or parts of its
content too. The reviewer decides which are given.

Propose none for a deliverable when none fits it: a reference that does not
fit misleads, and is worse than none. A candidate's first lines are shown;
read one whole with read_file, or look into a folder with list_dir, before
you propose it if its first lines do not settle it.

Return ONLY this JSON object:
{{"cards": {{"<card id>": [{{"path": "...", "summary": "what it is",
                            "why": "why it fits", "take": "what might be taken"}}], ...}}}}"""


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
    work agent may read - most alike first. Of the learning list's items
    (items.py) kept as a reference: a web page by its address (the reviewer
    and the work agent read it on the web), a passage the user kept by its
    words; one not kept as a reference is none."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for case in finder.similar_cases(facets):
        places = [one for one in case.get("locations") or [] if isinstance(one, dict)
                  and not finder.leave_out.location_out(one)]
        item = finder.item_of_case(case) if hasattr(finder, "item_of_case") else None
        if item is not None:
            if "reference" not in (item.get("learn") or []) and item["whose"] == "others":
                continue
            if item["kind"] in ("url", "text"):
                path = item["source"] if item["kind"] == "url" else f"text:{item['id']}"
                if path.lower() in seen:
                    continue
                seen.add(path.lower())
                place = next((one for one in places if one.get("path") == path), {})
                out.append({"path": path, "what": str(place.get("what") or (
                    "someone else's work, which the user endorses" if item["whose"] == "others"
                    else "the user's own, on the web")), "at": str(place.get("at") or "")[:10],
                    "case": case["id"], "project": str(item.get("title") or case.get("title") or ""),
                    "web": item["kind"] == "url",
                    "passage": item["text"][:PASSAGE] if item["kind"] == "text" else "",
                    "kept": next((one.get("kept") for one in (item.get("fetched") or {}).values()
                                  if one.get("kept")), "")})
                if len(out) >= CANDIDATES:
                    return out
                continue
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


def excerpt(path: str, candidate: Optional[Dict[str, Any]] = None) -> str:
    """A candidate's first lines - a folder's first two levels; a page's
    from the copy kept of it; a passage's own words."""
    candidate = candidate or {}
    if candidate.get("passage"):
        return candidate["passage"][:EXCERPT]
    if candidate.get("web"):
        kept = str(candidate.get("kept") or "")
        try:
            text = Path(kept).read_text(encoding="utf-8").split("\n", 1)[1] if kept else ""
        except (OSError, IndexError):
            text = ""
        return (text[:EXCERPT] + ("\n..." if len(text) > EXCERPT else "")) if text else \
            "(a web page: open it with fetch_url)"
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
        lines += ["    " + line for line in excerpt(one["path"], one).splitlines()]
    return "\n".join(lines)


def _plain(text: Any, most: int = 300) -> str:
    return " ".join(str(text or "").split())[:most]


def checked(said: Any, cards: Sequence[Dict[str, Any]], readable: Callable[[str], bool],
            leave_out: Any, most: int = PER_CARD,
            among: Optional[Dict[str, List[Dict[str, str]]]] = None,
            known: Optional[Dict[str, Dict[str, Any]]] = None
            ) -> Dict[str, List[Dict[str, str]]]:
    """What a call gave: for each deliverable at most `most`, each there,
    readable by the work agent and in no folder left out - and, chosen by
    the reviewer, one of those proposed for that deliverable (`among`). A
    web page or a kept passage only as it was a candidate (`known`: the
    candidates by their path) - never an address a model wrote."""
    known = known or {}
    given = said.get("cards") if isinstance(said, dict) and isinstance(said.get("cards"), dict) else {}
    out: Dict[str, List[Dict[str, str]]] = {}
    for card in cards:
        kept: List[Dict[str, str]] = []
        allowed = None if among is None else {one["path"] for one in among.get(card["id"]) or []}
        for one in given.get(card["id"]) or []:
            if not isinstance(one, dict):
                continue
            path = str(one.get("path") or "").strip()
            take = _plain(one.get("take"))
            if not path or not take or any(path == k["path"] for k in kept):
                continue
            if allowed is not None and path not in allowed:
                continue
            if path in known and (known[path].get("web") or known[path].get("passage")):
                kept.append({"path": path, "take": take, **{
                    key: _plain(one.get(key)) for key in ("summary", "why", "not")
                    if _plain(one.get(key))},
                    **({"passage": known[path]["passage"]} if known[path].get("passage") else {}),
                    **({"title": str(known[path].get("project") or "")})})
                if len(kept) >= most:
                    break
                continue
            if path.startswith(("http://", "https://", "text:")):
                continue
            try:
                there = Path(path).expanduser().exists()
            except OSError:
                there = False
            if there and readable(path) and not leave_out.touches(path):
                kept.append({"path": path, "take": take, **{
                    key: _plain(one.get(key)) for key in ("summary", "why", "not")
                    if _plain(one.get(key))}})
            if len(kept) >= most:
                break
        out[card["id"]] = kept
    return out


def proposed_text(cards: Sequence[Dict[str, Any]],
                  proposed: Dict[str, List[Dict[str, str]]]) -> str:
    """The references proposed, as the reviewer is shown them: whole, by
    deliverable - each with its path, what it is, why it fits and what might
    be taken; the files themselves it opens if it needs."""
    lines: List[str] = []
    for card in cards:
        mine = proposed.get(card["id"]) or []
        if not mine:
            continue
        lines.append(f"\n{card['id']}: {card.get('title', '')}")
        for one in mine:
            lines.append(f"  - {one['path']}")
            for key, said in (("what it is", "summary"), ("why it fits", "why"),
                              ("might take", "take")):
                if one.get(said):
                    lines.append(f"      {key}: {one[said]}")
    return "\n".join(lines).strip()


def shown(references: Any) -> List[str]:
    """A card's references as its lines read: what to take, and what not -
    a passage the user kept, in its own words."""
    out = []
    for one in references or []:
        if not isinstance(one, dict) or not one.get("path"):
            continue
        if one.get("passage"):
            out.append(f"    look at the passage \"{one.get('title') or 'kept'}\" - "
                       f"{one.get('take')}" + (f" Not: {one['not']}" if one.get("not") else "")
                       + "\n      " + " ".join(str(one["passage"]).split())[:PASSAGE])
            continue
        out.append(f"    look at: {one.get('path')} - {one.get('take')}"
                   + (f" Not: {one['not']}" if one.get("not") else ""))
    return out


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
