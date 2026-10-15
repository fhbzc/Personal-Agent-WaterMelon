"""Prove the reader still matches the original specification, amendments excepted.

    python docs/verify_fidelity.py

Compares the word sequence of the source specification with the word sequence
the reader renders, ignoring blocks and sections marked as amendments.  A pass
means the original text is reproduced exactly, in order.

The source is the PDF when it is present.  When it is not - the reader has
replaced it, and edits are made in ``design-doc.md`` rather than in the
PDF - the comparison runs against ``pdf_baseline.json``: the token sequence
frozen from the last run that read the PDF and reported IDENTICAL - since
2026-10-15, the specification as revised then, which replaced the PDF's text
as the original.  Either way an accidental edit to the original text still
fails the check.
"""

from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from pdf_to_sections import lines_from_pdf  # noqa: E402

PDF = ROOT / "Design doc for personalized agent (English).pdf"
SECTIONS_JSON = HERE / "sections.json"
BASELINE_JSON = HERE / "pdf_baseline.json"


def source_tokens() -> tuple:
    """The original specification's words, and where they came from."""
    if PDF.exists():
        tokens: list = []
        for record in lines_from_pdf(PDF):
            tokens.extend(record["text"].split())
        return tokens, "PDF"
    if BASELINE_JSON.exists():
        baseline = json.loads(BASELINE_JSON.read_text(encoding="utf-8"))
        return list(baseline["tokens"]), f"baseline ({baseline['captured_on']})"
    raise SystemExit("no source to verify against: neither the PDF nor "
                     f"{BASELINE_JSON.name} is present")


def rendered_tokens(skip_amendments: bool = True) -> list:
    sections = json.loads(SECTIONS_JSON.read_text(encoding="utf-8"))
    source = HERE / "sections_source.json"
    original = ({s.get("number") for s in json.loads(source.read_text(encoding="utf-8"))}
                if source.exists() else set())
    tokens: list = []
    for section in sections:
        if skip_amendments and section.get("amended") and section.get("number") not in original:
            continue                          # a section the original never had
        if skip_amendments and section.get("amended") and section.get("id", "").count("-"):
            # A section added by an amendment contributes nothing to the original.
            if all(block.get("amended") for block in section["blocks"]):
                continue
        if section.get("label"):
            tokens.extend(section["label"].split())
        tokens.extend(section["title"].split())
        for block in section["blocks"]:
            if skip_amendments and block.get("amended"):
                continue
            if block["type"] in ("p", "pre", "quote", "h"):
                tokens.extend(block["text"].split())
            elif block["type"] == "ul":
                for item in block["items"]:
                    tokens.append("●")
                    tokens.extend(item.split())
            elif block["type"] == "ol":
                for index, item in enumerate(block["items"], 1):
                    tokens.append(f"{index}.")
                    tokens.extend(item.split())
    return tokens


def main() -> int:
    source, origin = source_tokens()
    rendered = rendered_tokens()
    print(f"source tokens   : {len(source)} (from {origin})")
    print(f"reader tokens   : {len(rendered)} (amendments excluded)")
    if source == rendered:
        print("RESULT: IDENTICAL - the original specification text is preserved word for word")
        return 0
    print("RESULT: DIFFERS")
    operations = [op for op in difflib.SequenceMatcher(a=source, b=rendered,
                                                       autojunk=False).get_opcodes()
                  if op[0] != "equal"]
    for tag, i1, i2, j1, j2 in operations[:20]:
        print(f"  {tag:8} source={source[i1:i2][:10]}  reader={rendered[j1:j2][:10]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
