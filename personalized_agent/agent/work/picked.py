"""WHAT WAS RIGHT-CLICKED (the user, 2026-11-01: "在任何一个网页，程序，我可以直接
右键，delegate work - context就是这个打开的网址或者文件本身加其它related, 或者选中一行
字，context就是这个网址，尤其是这个字"; "对全平台，全工具支持").

Every "Delegate work" on a right-click - in a web page (the browser
extension), in an editor (VS Code and Cursor, Sublime Text, JetBrains), in
Office, in macOS's Services menu, in a file manager - hands over the same
thing, PICKED: what was clicked, and where.

    kind   page | selection | link | image | file | folder
    where  browser | vscode | sublime | jetbrains | office | services | files
    app    the program, as it names itself ("Google Chrome", "Cursor")
    url    the page's address - a web page, or the document's
    title  the page's or the window's title
    text   THE SELECTED TEXT - what the work is about above all
    around the words around it - its paragraph, its lines
    page   the page's or the file's own words, read this far
    link   a link right-clicked, and its words (link_text)
    src    a picture's address
    file   a file - the one open, or right-clicked - and `line`, where

Made a piece of work (`as_draft`): the selected text is what was handed
over; the page or file is the window it came from, with its address; a file
or a folder is where it works. The Delegate panel shows it as it was clicked
(`shown`), and the plan and the run are told it first (`told`).

Nothing here is read from anywhere: only what the program handed over, kept
as far as the limits below.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

KINDS = ("page", "selection", "link", "image", "file", "folder")
WHERE = ("browser", "vscode", "sublime", "jetbrains", "office", "services", "files",
         "editor")
#: How much is kept of each.
TEXT_CHARS, AROUND_CHARS, PAGE_CHARS, SHORT = 20000, 4000, 8000, 2000


def _clean(value: Any, most: int) -> str:
    return str(value or "").replace("\x00", "").strip()[:most]


def picked(raw: Any) -> Dict[str, Any]:
    """What a program handed over, kept as PICKED - each part checked and
    cut to its length; what it did not say is left out. Its kind, when it
    did not say one, is what it holds: selected text, a link, a picture, a
    file, else the page."""
    raw = raw if isinstance(raw, dict) else {}
    said: Dict[str, Any] = {}
    for key, most in (("app", 120), ("title", 500), ("url", SHORT), ("text", TEXT_CHARS),
                      ("around", AROUND_CHARS), ("page", PAGE_CHARS), ("link", SHORT),
                      ("link_text", 500), ("src", SHORT), ("file", 1000)):
        value = _clean(raw.get(key), most)
        if value:
            said[key] = value
    for key in ("url", "link", "src"):
        # An address of the web, or of a file - never a script.
        if key in said and said[key].lower().startswith(("javascript:", "data:")):
            said.pop(key)
    try:
        line = int(raw.get("line") or 0)
        if line > 0:
            said["line"] = line
    except (TypeError, ValueError):
        pass
    where = _clean(raw.get("where"), 20).lower()
    said["where"] = where if where in WHERE else "editor"
    kind = _clean(raw.get("kind"), 20).lower()
    if kind not in KINDS:
        kind = ("selection" if said.get("text") else "link" if said.get("link")
                else "image" if said.get("src") else "file" if said.get("file") else "page")
    if kind == "file" and said.get("file") and Path(said["file"]).is_dir():
        kind = "folder"
    said["kind"] = kind
    return said


def as_draft(one: Dict[str, Any]) -> Dict[str, Any]:
    """The piece of work it makes (`Drafts.create`): the selected text is
    what was handed over; the page, or the window, is where it came from -
    with what was picked, for the panel and the plan; a file or a folder is
    where it works."""
    files: List[str] = []
    folders: List[str] = []
    path = one.get("file") or ""
    if path and Path(path).exists():
        (folders if Path(path).is_dir() else files).append(str(Path(path)))
    window = {"title": one.get("title") or (Path(path).name if path else ""),
              "app": one.get("app") or "", "picked": one}
    url = one.get("url") or ""
    if url.lower().startswith(("http://", "https://")):
        window["url"] = url
    # The words it showed: the page's own, as the program read them - not
    # looked for again off the screen.
    words = "\n\n".join(part for part in (one.get("around") or "", one.get("page") or "")
                        if part)
    if words:
        window["text"] = words
    return {"text": one.get("text") or "", "files": files, "folders": folders,
            "window": window, "source": "picked"}


def from_file(path: str, app: str = "", lines: Any = None, columns: Any = None,
              where: str = "jetbrains") -> Dict[str, Any]:
    """WHAT WAS RIGHT-CLICKED IN AN EDITOR THAT NAMES ONLY WHERE (JetBrains'
    External Tools): the file, and where the selection is in it - its first
    and last lines and columns, from 1 - read off the file, which the editor
    saved before it ran the tool. No selection where the two are the same."""
    said: Dict[str, Any] = {"where": where, "app": app, "file": str(path or "")}
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return picked(dict(said, kind="file"))
    # Each line one character apart, whatever the file's line ends.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    rows = text.split("\n")
    try:
        first, last = (int(one) for one in (lines or [])[:2])
        start, end = (int(one) for one in (columns or [])[:2])
    except (TypeError, ValueError):
        return picked(dict(said, kind="file", page=text[:PAGE_CHARS]))
    if first < 1 or last < first or last > len(rows) + 1:
        return picked(dict(said, kind="file", page=text[:PAGE_CHARS]))

    def at(row: int, column: int) -> int:
        return sum(len(one) + 1 for one in rows[:row - 1]) + max(0, column - 1)
    chosen = text[at(first, start):at(last, end)] if (first, start) != (last, end) else ""
    said.update({"line": first, "text": chosen, "page": text[:PAGE_CHARS],
                 "kind": "selection" if chosen.strip() else "file"})
    if chosen.strip():
        said["around"] = "\n".join(rows[max(0, first - 7):last + 6])
    return picked(said)


KIND_WORDS = {"page": "the page", "selection": "selected text", "link": "a link",
              "image": "a picture", "file": "the file", "folder": "the folder"}


def told(one: Dict[str, Any]) -> List[str]:
    """What the plan and the run are told of it, first: what was
    right-clicked, where - and that the selected text is what it is about."""
    if not one:
        return []
    lines = [f"WHAT THE USER RIGHT-CLICKED: {KIND_WORDS.get(one.get('kind'), 'it')}"
             + (f", in {one['app']}" if one.get("app") else "")]
    if one.get("title"):
        lines.append(f"  its page or window: {one['title']}")
    if one.get("url"):
        lines.append(f"  its address: {one['url']}")
    if one.get("file"):
        lines.append(f"  the file: {one['file']}" + (f", line {one['line']}"
                                                      if one.get("line") else ""))
    if one.get("link"):
        lines.append(f"  the link: {one['link']}" + (f" ({one['link_text']})"
                                                      if one.get("link_text") else ""))
    if one.get("src"):
        lines.append(f"  the picture: {one['src']}")
    if one.get("text"):
        lines += ["  THE SELECTED TEXT - what the work is about above all; the page or the "
                  "file is its context:", "    " + one["text"].replace("\n", "\n    ")]
    if one.get("around"):
        lines += ["  the words around it:", "    " + one["around"].replace("\n", "\n    ")]
    return lines


__all__ = ["picked", "as_draft", "told", "from_file", "KINDS", "WHERE", "KIND_WORDS"]
