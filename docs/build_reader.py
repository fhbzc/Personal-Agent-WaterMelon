"""Build the HTML reader for the design specification.

    python docs/build_reader.py

``docs/design-doc.md`` is the editable source: edit it in any text editor and
run this script.  It writes:

* ``Design doc for personalized agent (English).html`` - standalone, opens from disk
* ``docs/reader_fragment.html``                        - body only, for publishing
* ``docs/sections.json``                               - the structured content

Anything that differs from the original specification is marked as an amendment
in the reader automatically, by comparing the edited document against
``docs/sections_source.json`` - the original: first the parse of the PDF, and
since 2026-10-15 the specification as revised then, brought in line with the
application as built.  There is no flag to set by hand.  ``docs/verify_fidelity.py`` reports the same
comparison as a word-level check.

If the PDF is present it is re-parsed and the two cached artefacts are
refreshed; the PDF itself is never modified.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from markdown_doc import from_markdown, to_markdown  # noqa: E402

PDF = ROOT / "Design doc for personalized agent (English).pdf"
TEMPLATE = HERE / "reader_template.html"
MARKDOWN = HERE / "design-doc.md"
SECTIONS_JSON = HERE / "sections.json"
SOURCE_JSON = HERE / "sections_source.json"
STANDALONE = ROOT / "Design doc for personalized agent (English).html"
FRAGMENT = HERE / "reader_fragment.html"


def refresh_original() -> List[Dict[str, Any]]:
    """The unamended specification: re-parsed from the PDF when it is here."""
    if PDF.exists():
        from pdf_to_sections import parse

        sections = parse(PDF)
        for section in sections:
            section.pop("level", None)
        SOURCE_JSON.write_text(json.dumps(sections, ensure_ascii=False, indent=1),
                               encoding="utf-8")
        return sections
    if SOURCE_JSON.exists():
        return json.loads(SOURCE_JSON.read_text(encoding="utf-8"))
    raise SystemExit(f"no original specification: neither {PDF.name} nor "
                     f"{SOURCE_JSON.name} is present")


def _fingerprint(block: Dict[str, Any]) -> str:
    payload = {k: v for k, v in block.items() if k != "amended"}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def mark_amendments(sections: List[Dict[str, Any]],
                    original: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flag whatever the edited document changed, relative to the original."""
    by_number = {s.get("number"): s for s in original}
    for section in sections:
        source = by_number.get(section.get("number"))
        if source is None:
            section["amended"] = True                # a section that is entirely new
            for block in section["blocks"]:
                block["amended"] = True
            continue
        known = {_fingerprint(b) for b in source["blocks"]}
        changed = False
        for block in section["blocks"]:
            if _fingerprint(block) in known:
                block.pop("amended", None)
            else:
                block["amended"] = True
                changed = True
        if section.get("title") != source.get("title"):
            changed = True
        if changed:
            section["amended"] = True
        else:
            section.pop("amended", None)
    return sections


def load_source() -> List[Dict[str, Any]]:
    if not MARKDOWN.exists():
        raise SystemExit(f"{MARKDOWN.name} is missing - it is the editable source")
    text = MARKDOWN.read_text(encoding="utf-8")
    sections = from_markdown(text)
    # An edit that this build could not read back exactly would be silently
    # dropped, so refuse to build rather than publish a lossy render.
    if from_markdown(to_markdown(sections)) != sections:
        raise SystemExit(f"{MARKDOWN.name} does not survive a round trip - "
                         "a block in it uses a construction the reader format "
                         "cannot represent")
    return sections


def build() -> None:
    original = refresh_original()
    sections = mark_amendments(load_source(), original)
    SECTIONS_JSON.write_text(json.dumps(sections, ensure_ascii=False, indent=1),
                             encoding="utf-8")

    payload = json.dumps(sections, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    fragment = TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload)
    FRAGMENT.write_text(fragment, encoding="utf-8")

    head, body = fragment.split('<div class="shell">', 1)
    STANDALONE.write_text(
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"{head}</head>\n<body>\n"
        f'<div class="shell">{body}\n</body>\n</html>\n',
        encoding="utf-8",
    )

    amended = [s["number"] for s in sections if s.get("amended")]
    print(f"source     : {MARKDOWN.name}"
          f"{' (original refreshed from the PDF)' if PDF.exists() else ''}")
    print(f"sections   : {len(sections)}")
    print(f"amended    : {', '.join(amended) if amended else 'none'}")
    print(f"standalone : {STANDALONE} ({STANDALONE.stat().st_size // 1024} KB)")
    print(f"fragment   : {FRAGMENT} ({FRAGMENT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    build()
