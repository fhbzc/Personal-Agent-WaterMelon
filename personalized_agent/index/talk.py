"""A CONVERSATION RECORD, AS THE INDEX KEEPS IT: what was said, and what
was done - not what the tools gave back.

A record of Claude Code's or Codex's is one JSON line a turn, and most of
it is not the conversation: measured on one machine, the words the user
and the agent said were about 14% of it, the tools' calls and results 6%,
and the rest the record's own fields, written again on every line. So
what is kept of it:

  * THE USER'S WORDS - without what the tool put in their turn (the
    reminders it adds, its own notes); in a subagent's record none: what
    reads as the user there is the agent that started it.
  * THE AGENT'S TEXT REPLIES - their words; its thinking is not kept.
  * EACH CALL, AS ONE LINE: the tool, and what it was on - the file
    changed or read and how many lines went in and out, the pattern
    searched, the page, a command's own description (never the command:
    what is typed there can hold a key). Not what was written, and not
    what came back - only that it failed, when it did.

The same text is what a search shows of it: its line numbers are this
text's, not the file's. And each piece is kept with WHERE IN THE FILE its
first line comes from - the byte its turn starts at, and how many of that
turn's lines come before it - so what a search shows is read from there,
a few turns, never the whole record again: one of 1.4 GB took 46 seconds
to show six lines of, every search (2026-10-29). A record is only ever
added to, so where a turn starts stays where it is.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from bisect import bisect_right
from typing import Any, Dict, Iterator, List, Optional, Tuple

#: The most of one call's line.
MOST_CALL = 300

_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
#: What Codex puts in the user's turn that the user did not write.
_CODEX_NOT_SAID = re.compile(
    r"^\s*(<(environment_context|user_instructions|permissions|collaboration_mode|"
    r"skills_instructions|turn_aborted|user_shell_command)\b|# AGENTS\.md instructions)")
_PATCHED = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$|^\*\*\* Move to: (.+)$",
                      re.MULTILINE)


#: The most of a record read to show one piece of it, from where it starts.
MOST_READ_FOR_A_PIECE = 20_000_000


def _turns(handle: Any, subagent: bool, at: int = 0) -> Iterator[Tuple[int, List[str]]]:
    """Each turn of a record read from byte `at` on, as what is kept of it,
    with the byte its line starts at - those that keep nothing too."""
    handle.seek(at)
    offset = at
    for raw in handle:
        start = offset
        offset += len(raw)
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            yield start, (_claude(entry, subagent) if "payload" not in entry
                          else _codex(entry.get("payload") or {}))


def talk_kept(path: Path, most: Optional[int] = None
              ) -> Optional[Tuple[str, List[Tuple[int, int]]]]:
    """The record's conversation as lines of text, and where each turn's
    lines begin: (its first line, counting from 0 as the text's
    splitlines() does, the byte its turn starts at) - or None when it is
    not a record that can be read, or what is kept of it is more than
    `most` characters."""
    try:
        handle = open(path, "rb")
    except OSError:
        return None
    subagent = "subagents" in Path(path).parts
    lines: List[str] = []
    starts: List[Tuple[int, int]] = []
    kept = counted = 0
    with handle:
        for at, said in _turns(handle, subagent):
            if not said:
                continue
            starts.append((counted, at))
            lines.extend(said)
            # The text is these joined by line breaks: each one's lines,
            # as splitlines() will count them in the whole.
            counted += sum(len((one + "\n").splitlines()) for one in said)
            kept += sum(len(one) + 1 for one in said)
            if most is not None and kept > most:
                return None
    return "\n".join(lines), starts


def talk_text(path: Path, most: Optional[int] = None) -> Optional[str]:
    """The record's conversation as lines of text, or None when it is not
    a record that can be read - or when what is kept of it is more than
    `most` characters."""
    kept = talk_kept(path, most)
    return kept[0] if kept is not None else None


def where_lines_are(starts: List[Tuple[int, int]], line: int) -> Tuple[int, int]:
    """Where a line of the text (from 1) comes from in the file: the byte
    its turn starts at, and how many of that turn's lines come before it."""
    firsts = [one[0] for one in starts]
    k = max(0, bisect_right(firsts, line - 1) - 1)
    if not starts:
        return 0, line - 1
    return starts[k][1], line - 1 - starts[k][0]


def lines_from(path: Path, at: int, skip: int, count: int) -> str:
    """`count` lines of a record's text from where a piece starts - read
    from byte `at` on, past `skip` lines of that turn, a few turns; never
    more than MOST_READ_FOR_A_PIECE of it."""
    try:
        handle = open(path, "rb")
    except OSError:
        return ""
    subagent = "subagents" in Path(path).parts
    lines: List[str] = []
    with handle:
        for start, said in _turns(handle, subagent, at):
            lines.extend(said)
            if len("\n".join(lines).splitlines()) >= skip + count \
                    or start - at > MOST_READ_FOR_A_PIECE:
                break
    return "\n".join("\n".join(lines).splitlines()[skip:skip + count])


def _said(who: str, text: str) -> List[str]:
    text = text.strip()
    return [f"{who}: {text}"] if text else []


def _claude(entry: Dict[str, Any], subagent: bool) -> List[str]:
    kind = entry.get("type")
    message = entry.get("message") or {}
    content = message.get("content") if isinstance(message, dict) else None
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
    if not isinstance(blocks, list):
        return []
    out: List[str] = []
    if kind == "user":
        theirs = not (subagent or entry.get("isSidechain") or entry.get("isMeta"))
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and theirs:
                out += _said("user", _REMINDER.sub("", str(block.get("text") or "")))
            elif block.get("type") == "tool_result" and block.get("is_error"):
                out.append("  [failed]")
    elif kind == "assistant":
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                out += _said("assistant", str(block.get("text") or ""))
            elif block.get("type") == "tool_use":
                out.append(_call(str(block.get("name") or "tool"), block.get("input") or {}))
    return out


def _codex(payload: Dict[str, Any]) -> List[str]:
    kind = payload.get("type")
    if kind == "message":
        role = payload.get("role")
        if role not in ("user", "assistant"):
            return []
        out: List[str] = []
        for block in payload.get("content") or []:
            text = str((block or {}).get("text") or "") if isinstance(block, dict) else ""
            if role == "user" and _CODEX_NOT_SAID.match(text):
                continue
            out += _said(role, text)
        return out
    if kind == "function_call":
        try:
            arguments = json.loads(payload.get("arguments") or "{}")
        except ValueError:
            arguments = {}
        name = str(payload.get("name") or "tool")
        if name in ("shell", "exec_command", "shell_command") and isinstance(arguments, dict):
            where = arguments.get("workdir") or ""
            return [_short(f"  [{name}]" + (f" in {where}" if where else ""))]
        return [_call(name, arguments if isinstance(arguments, dict) else {})]
    if kind == "custom_tool_call":
        name = str(payload.get("name") or "tool")
        if name == "apply_patch":
            files = [one or other for one, other in _PATCHED.findall(str(payload.get("input") or ""))]
            return [_short("  [apply_patch] " + ", ".join(files))]
        return [_short(f"  [{name}]")]
    if kind == "local_shell_call":
        return ["  [shell]"]
    if kind == "function_call_output":
        output = payload.get("output")
        if isinstance(output, dict) and output.get("success") is False:
            return ["  [failed]"]
    return []


def _lines(text: Any) -> int:
    return len(str(text or "").splitlines())


def _call(name: str, given: Dict[str, Any]) -> str:
    """One call, as what it was on: never what it wrote, nor a command."""
    if not isinstance(given, dict):
        given = {}
    on = given.get("file_path") or given.get("notebook_path") or given.get("path") or ""
    said = ""
    if name == "Edit":
        said = f"(+{_lines(given.get('new_string'))} -{_lines(given.get('old_string'))})"
    elif name == "MultiEdit":
        edits = [one for one in given.get("edits") or [] if isinstance(one, dict)]
        said = (f"(+{sum(_lines(one.get('new_string')) for one in edits)} "
                f"-{sum(_lines(one.get('old_string')) for one in edits)})")
    elif name in ("Write", "NotebookEdit"):
        said = f"(+{_lines(given.get('content') or given.get('new_source'))})"
    elif name in ("Grep", "Glob"):
        said = json.dumps(str(given.get("pattern") or ""), ensure_ascii=False)
        on = f"in {on}" if on else ""
    elif name == "WebFetch":
        on = str(given.get("url") or "")
    elif name == "WebSearch":
        said = json.dumps(str(given.get("query") or ""), ensure_ascii=False)
    elif name == "Skill":
        on = str(given.get("skill") or "")
    elif given.get("description"):
        said = str(given.get("description"))
    return _short(" ".join(part for part in (f"  [{name}]", str(on), said) if part))


def _short(line: str) -> str:
    line = " ".join(line.split()) if "\n" in line else line
    return line[:MOST_CALL]


def lines_of(text: str, line: int, count: int) -> str:
    """From line `line` (from 1), `count` lines of a record's text."""
    return "\n".join(text.splitlines()[line - 1:line - 1 + count])


__all__ = ["talk_text", "talk_kept", "where_lines_are", "lines_from", "lines_of"]
