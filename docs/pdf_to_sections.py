"""Structure the design doc PDF into sections, preserving every word.

Formatting only.  Headings come from the font size the exporter used (23pt and
17pt against 11pt body text), so wrapped headings and numbering quirks in the
source are handled without guessing from the text.  Body text keeps its exact
wording; only extraction artefacts are repaired:

* justified paragraphs split across lines are rejoined;
* indented schema/code/diagram lines keep their spacing in a <pre> block;
* bullet and numbered lists become real lists.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pdfplumber

BULLET = "●"
HEADING_SIZE = 15.0
NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(\S.*)$")
LIST_ITEM = re.compile(r"^(\d+)\.\s+(\S.*)$")
SENTENCE_END = re.compile(r"[.:!?)\]}\"']$")
CODE_CHARS = ("+--", "--+", "->", "|", "==", "{", "}", "(", ")", "[", "]", "=")


def lines_from_pdf(path: Path) -> list[dict]:
    """One record per visual line: text (with indentation), size, page."""
    records: list[dict] = []
    with pdfplumber.open(str(path)) as pdf:
        for page_number, page in enumerate(pdf.pages, 1):
            grouped: dict[float, list] = {}
            for char in page.chars:
                grouped.setdefault(round(char["top"], 0), []).append(char)
            for top in sorted(grouped):
                chars = sorted(grouped[top], key=lambda c: c["x0"])
                text = "".join(c["text"] for c in chars).rstrip()
                size = max(round(c["size"], 1) for c in chars)
                x0 = min(c["x0"] for c in chars)
                first = next((c for c in chars if c["text"].strip()), chars[0])
                bold = "Bold" in (first.get("fontname") or "")
                x1 = max(c["x1"] for c in chars)
                records.append({"text": text, "size": size, "page": page_number,
                                "x0": x0, "x1": x1, "bold": bold})
    return records


def is_preformatted(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    indented = text.startswith("  ")
    if indented:
        words = stripped.split()
        # An indented full sentence is an emphasis quote, not code.
        prose = len(words) >= 6 and not any(c in stripped for c in ("=", "(", ")", "{", "}", "|"))
        return not prose
    if any(marker in stripped for marker in ("+--", "--+", "->")):
        return True
    if re.fullmatch(r"[|^v<>+\-\s]+", stripped):
        return True
    if stripped.startswith(("def ", "class ", "import ", "from ", "@", "{", "}", '"')):
        return True
    if re.fullmatch(r"[\w.\-]+(/[\w.\-<>]*)+/?", stripped):          # paths
        return True
    if re.fullmatch(r"<[A-Z_]+>(/[\w.\-]*)*/?", stripped):           # <AGENT_WORKSPACE>/...
        return True
    if re.fullmatch(r"[a-z_][a-z0-9_]*", stripped):                  # schema field names
        return True
    if re.fullmatch(r"[A-Za-z_][\w]*\s*:\s*[\w\[\]| .]+", stripped) and "  " not in stripped:
        return True
    if re.fullmatch(r"[A-Z][A-Za-z]*(Event|Edit|Node|Edge|Task|Provider|Availability|Manager|"
                    r"Interface|Model|Result|Report|Test|Trace|State|Status|Config|Policy|"
                    r"Component|Connection|Snapshot|Registry|Metrics|Session|Budget)s?:?",
                    stripped):
        return True
    return False


# The exporter indents body text by position: 72pt is the text column, 90pt is a
# list item, and 102pt is an indented emphasis block - the document uses those
# for its hard invariants.
BODY_LEFT = 72.0
QUOTE_LEFT = 96.0
# The text column ends at ~542pt and is ragged-right: a wrapped line stops
# between roughly 470 and 542, while a deliberately broken line (a label, an item
# in a list of names) stops far short of that.
WRAP_EDGE = 490.0
WRAP_EDGE_SOFT = 470.0


def is_quote(record: dict) -> bool:
    return record["x0"] >= QUOTE_LEFT and not record["text"].strip().startswith(BULLET)


def parse(path: Path) -> list[dict]:
    records = lines_from_pdf(path)
    sections: list[dict] = []
    current: dict | None = None
    prose: list[str] = []
    pre: list[str] = []
    quote: list[str] = []
    pending_list: dict | None = None

    def new_section(number: str, title: str, label: str = "") -> dict:
        return {
            "number": number,
            "label": label or number,
            "id": "s" + number.replace(".", "-") if number else "title",
            "title": title,
            "level": number.count(".") + 1 if number else 0,
            "page": record["page"],
            "blocks": [],
        }

    def flush_prose() -> None:
        if not prose or current is None:
            prose.clear()
            return
        text = " ".join(prose).strip()
        prose.clear()
        if text:
            current["blocks"].append({"type": "p", "text": text})

    def flush_pre() -> None:
        if not pre or current is None:
            pre.clear()
            return
        block = list(pre)
        pre.clear()
        while block and not block[0].strip():
            block.pop(0)
        while block and not block[-1].strip():
            block.pop()
        if not block:
            return
        indents = [len(l) - len(l.lstrip()) for l in block if l.strip()]
        shift = min(indents) if indents else 0
        current["blocks"].append({"type": "pre",
                                  "text": "\n".join(l[shift:].rstrip() for l in block)})

    def flush_quote() -> None:
        if quote and current is not None:
            current["blocks"].append({"type": "quote", "text": " ".join(quote)})
        quote.clear()

    def flush_list() -> None:
        nonlocal pending_list
        if pending_list and current is not None:
            current["blocks"].append(pending_list)
        pending_list = None

    def flush_all() -> None:
        flush_prose()
        flush_pre()
        flush_quote()
        flush_list()

    previous_x1 = 0.0
    for record in records:
        text = record["text"]
        stripped = text.strip()

        # ---- headings -----------------------------------------------------
        if record["size"] >= HEADING_SIZE:
            if not stripped:
                continue
            match = NUMBERED_HEADING.match(stripped)
            if match and re.match(r"^\d", stripped):
                flush_all()
                if current is not None:
                    sections.append(current)
                label = stripped[: len(stripped) - len(match.group(2))].strip()
                current = new_section(match.group(1), match.group(2), label)
                continue
            # A heading line without a number: either the continuation of the
            # heading above it, or an unnumbered sub-heading inside a section.
            if current is not None and not current["blocks"] and not prose and not pre:
                current["title"] = f"{current['title']} {stripped}".strip()
            elif current is not None:
                flush_all()
                current["blocks"].append({"type": "h", "text": stripped})
            else:
                current = new_section("", stripped)
            continue

        if current is None:
            current = new_section("", "Personalized Agent")

        # ---- body ---------------------------------------------------------
        if not stripped:
            flush_all()
            continue

        if stripped.startswith(BULLET):
            flush_prose()
            flush_pre()
            item = stripped[len(BULLET):].strip()
            if pending_list and pending_list["type"] == "ul":
                pending_list["items"].append(item)
            else:
                flush_list()
                pending_list = {"type": "ul", "items": [item]}
            continue

        list_match = LIST_ITEM.match(stripped)
        if list_match and not is_preformatted(text):
            expected = 1 if not (pending_list and pending_list["type"] == "ol") \
                else len(pending_list["items"]) + 1
            if int(list_match.group(1)) == expected:
                flush_prose()
                flush_pre()
                if pending_list and pending_list["type"] == "ol":
                    pending_list["items"].append(list_match.group(2).strip())
                else:
                    flush_list()
                    pending_list = {"type": "ol", "items": [list_match.group(2).strip()],
                                    "start": 1}
                continue

        if is_preformatted(text):
            flush_prose()
            flush_list()
            pre.append(text)
            continue

        if is_quote(record):
            flush_prose()
            flush_pre()
            flush_list()
            if quote and not SENTENCE_END.search(quote[-1]):
                quote[-1] = quote[-1] + " " + stripped
            else:
                quote.append(stripped)
            continue
        flush_quote()

        flush_pre()
        flush_list()
        # A wrapped paragraph continues until the line is punctuated, unless the
        # next line is a bold label - the exporter uses bold to start a new one.
        continues_sentence = stripped[:1].islower()
        wrapped = (previous_x1 >= WRAP_EDGE
                   or (previous_x1 >= WRAP_EDGE_SOFT and continues_sentence))
        if (prose and wrapped and not record["bold"]
                and not SENTENCE_END.search(prose[-1])):
            prose[-1] = prose[-1] + " " + stripped
        else:
            flush_prose()          # the previous paragraph is complete
            prose.append(stripped)
        previous_x1 = record["x1"]

    flush_all()
    if current is not None:
        sections.append(current)
    return [s for s in sections if s["blocks"] or s["number"]]


def main() -> int:
    pdf_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    sections = parse(pdf_path)
    out_path.write_text(json.dumps(sections, ensure_ascii=False), encoding="utf-8")
    numbered = [s for s in sections if s["number"]]
    print(f"{len(sections)} sections ({len(numbered)} numbered) -> {out_path}")
    kinds: dict[str, int] = {}
    for section in sections:
        for block in section["blocks"]:
            kinds[block["type"]] = kinds.get(block["type"], 0) + 1
    print("blocks:", kinds)
    print("\nfirst 12 sections:")
    for section in sections[:12]:
        print(f"  {section['number'] or '-':>9}  p{section['page']:<4} {section['title'][:56]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
