"""Markdown <-> section-structure conversion for the design specification.

``docs/design-doc.md`` is the editable source of the specification reader.  Edit
that file in any text editor and run ``python docs/build_reader.py`` to
regenerate the HTML.  This module is the only place that knows the format:

    # 12. Settings
    <!-- number: 12 | label: 12. | id: s12 | page: 25 -->

    Initial settings include:

    ## A subheading

    > A quoted requirement.

    - a bullet
    - another bullet

    1. a numbered step

    ```
    verbatim / preformatted text
    ```

Everything a section needs lives in the heading and the metadata comment that
follows it; everything after that is content.  The mapping is exact in both
directions - ``docs/build_reader.py`` refuses to build if a round trip through
this module would change the document.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

META_RE = re.compile(r"^<!--\s*(.*?)\s*-->$")
FENCE = "```"


# ---------------------------------------------------------------- rendering
def _render_meta(section: Dict[str, Any]) -> str:
    fields = [f"number: {section.get('number', '')}",
              f"label: {section.get('label', '')}",
              f"id: {section.get('id', '')}",
              f"page: {section.get('page', '')}"]
    return "<!-- " + " | ".join(fields) + " -->"


def _render_block(block: Dict[str, Any]) -> List[str]:
    kind = block["type"]
    if kind == "p":
        return [block["text"]]
    if kind == "h":
        return ["## " + block["text"]]
    if kind == "quote":
        return ["> " + block["text"]]
    if kind == "ul":
        return ["- " + item for item in block["items"]]
    if kind == "ol":
        start = int(block.get("start", 1))
        return [f"{index}. {item}" for index, item in enumerate(block["items"], start)]
    if kind == "pre":
        return [FENCE, *block["text"].split("\n"), FENCE]
    raise ValueError(f"unknown block type {kind!r}")


def to_markdown(sections: List[Dict[str, Any]]) -> str:
    """Render the section structure as the editable Markdown document."""
    out: List[str] = [
        "<!-- Personalized Agent - design specification.",
        "",
        "     This file is the editable source of",
        "     'Design doc for personalized agent (English).html'.",
        "     Edit it, then run:  python docs/build_reader.py",
        "",
        "     The metadata comment under each heading carries the section's",
        "     number, anchor and original page; leave it in place when editing",
        "     prose. To add a section, copy an existing heading and give it a",
        "     new number and id. Anything that differs from the original",
        "     specification is marked as an amendment in the reader",
        "     automatically - there is no flag to set by hand.",
        "-->",
        "",
    ]
    for section in sections:
        heading = " ".join(part for part in (section.get("label", ""),
                                             section.get("title", "")) if part)
        out.append(f"# {heading}".rstrip())
        out.append(_render_meta(section))
        note = section.get("amendment_note")
        if note:
            out.append(f"<!-- note: {note} -->")
        out.append("")
        for block in section["blocks"]:
            out.extend(_render_block(block))
            out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


# ------------------------------------------------------------------ parsing
def _parse_meta(line: str) -> Dict[str, str]:
    match = META_RE.match(line.strip())
    if not match:
        return {}
    body = match.group(1)
    if "|" not in body and ":" not in body:
        return {}
    fields: Dict[str, str] = {}
    for part in body.split("|"):
        key, _, value = part.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def _flush(blocks: List[Dict[str, Any]], kind: str, buffer: List[str],
           start: int = 1) -> None:
    if not buffer:
        return
    if kind == "ul":
        blocks.append({"type": "ul", "items": list(buffer)})
    elif kind == "ol":
        blocks.append({"type": "ol", "items": list(buffer), "start": start})
    buffer.clear()


def from_markdown(text: str) -> List[Dict[str, Any]]:
    """Parse the editable Markdown document back into the section structure."""
    sections: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}
    pending_kind = ""
    pending: List[str] = []
    pending_start = 1
    lines = text.split("\n")
    index = 0

    def close_list() -> None:
        nonlocal pending_kind
        if current:
            _flush(current["blocks"], pending_kind, pending, pending_start)
        pending_kind = ""

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if stripped.startswith("# "):
            close_list()
            heading = stripped[2:].strip()
            meta = _parse_meta(lines[index + 1]) if index + 1 < len(lines) else {}
            if not meta:
                raise ValueError(f"section {heading!r} has no metadata comment")
            label = meta.get("label", "")
            title = heading[len(label):].strip() if label and heading.startswith(label) \
                else heading
            current = {
                "number": meta.get("number", ""),
                "label": label,
                "id": meta.get("id", ""),
                "title": title,
                "page": int(meta["page"]) if meta.get("page", "").isdigit() else meta.get("page", ""),
                "blocks": [],
            }
            sections.append(current)
            index += 2
            if index < len(lines):
                note = _parse_meta(lines[index])
                if set(note) == {"note"}:
                    current["amendment_note"] = note["note"]
                    index += 1
            continue

        if not current:
            index += 1                      # document preamble
            continue

        if not stripped:
            close_list()
            index += 1
            continue

        if stripped == FENCE:
            close_list()
            body: List[str] = []
            index += 1
            while index < len(lines) and lines[index].strip() != FENCE:
                body.append(lines[index])
                index += 1
            index += 1
            current["blocks"].append({"type": "pre", "text": "\n".join(body)})
            continue

        if stripped.startswith("## "):
            close_list()
            current["blocks"].append({"type": "h", "text": stripped[3:].strip()})
        elif stripped.startswith("> "):
            close_list()
            current["blocks"].append({"type": "quote", "text": stripped[2:].strip()})
        elif stripped.startswith("- "):
            if pending_kind != "ul":
                close_list()
                pending_kind = "ul"
            pending.append(stripped[2:].strip())
        elif re.match(r"^\d+\.\s", stripped):
            if pending_kind != "ol":
                close_list()
                pending_kind = "ol"
                pending_start = int(stripped.split(".", 1)[0])
            pending.append(re.sub(r"^\d+\.\s+", "", stripped))
        elif META_RE.match(stripped):
            pass                            # a comment the author left in place
        else:
            close_list()
            current["blocks"].append({"type": "p", "text": stripped})
        index += 1

    close_list()
    return sections
