"""The full transcript of a run: every model call the builder (the work
agent) and the reviewer make, whole - the system prompt, the tools offered,
every message sent (tool results included), and the answer (its text and
the tools it called) - as plain text, written for a person to read.

WHERE IT IS KEPT. Every project has ONE folder of logs, `run_logs/` at the
top of its work folder, and in it a folder per run, named by when the run
started - `2026-10-09_14-32-05_run_ab12...` - so a file manager's sort by
name is the order the runs happened in. A run's folder holds its transcript,
`transcript-001.txt`, `transcript-002.txt`, ..., and its live session,
`events.jsonl` (see `runner.read_session`). A run from before the logs had
a folder of their own keeps reading - and, continued, writing - them where
they were, in `runs/<run>/.session/`: nothing is moved.

A file is closed once the next record would take it past `FILE_BYTES`: a
record is never split across files - unless it alone is larger than a file
may be, and then it is cut into parts, each marked as continued.

READABLE, AND NOTHING LEFT OUT. Each record begins with a block saying which
call it is, whose (the BUILDER's or the REVIEWER's), in which part of the
run, when it started and finished in the user's own time, the model and the
tokens in and out. Then the input - the system prompt, the tools offered,
every message, numbered - and the output. Values are written as they read,
not as escaped JSON: a tool's arguments a key a line, code and output on
lines of their own as they were written, a tool's result parsed and laid out
the same way. A conversation sends its earlier messages again with every
call; they are written again, whole, but each says it was sent before and
in which call, and the first new one says NEW - so a reader can jump to what
is new without anything being left out.

Written through the run's policy, like everything else the run writes; the
logs are no version's (`versions.work_files` leaves `run_logs` out, so a
rollback never touches them), and locked from the agents' own tools - the
record of what happened is not theirs to change.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: How large one transcript file may grow, in bytes (UTF-8).
FILE_BYTES = 5 * 1024 * 1024

#: The files' names: transcript-001.txt, transcript-002.txt, ...
FILE_PATTERN = re.compile(r"^transcript-(\d{3,})\.txt$")

#: The rule a record's header block is drawn between.
RULE = "=" * 80

#: A record's header, which carries its number: the rule, then
#: "CALL #0012 · BUILDER · round 3". A message that happens to hold such a
#: line is not taken for one - only a line right under the rule is.
HEADER = re.compile(r"^" + RULE + r"\nCALL #(\d+) ·", re.M)
#: The header records were written with before - read so a run continued
#: goes on numbering where it left off.
OLD_HEADER = re.compile(r"^##### #(\d+) ", re.M)

#: Who made a call, as the transcript names them.
WHO = {"worker": "BUILDER", "reviewer": "REVIEWER"}

#: How far each level of a value is indented.
INDENT = 2

#: Written once at the top of a project's `run_logs/`, to say what is there.
README_NAME = "README.txt"
README_TEXT = """\
RUN LOGS OF THIS PROJECT
========================

Everything the agents said while working on this project, whole.

ONE FOLDER PER RUN, named by when the run started, in this computer's time:

    2026-10-09_14-32-05_run_ab12cd34ef56ab78/

so sorting by name lists the runs in the order they happened. A run that is
continued later keeps writing to its own folder.

IN A RUN'S FOLDER

  transcript-001.txt, transcript-002.txt, ...
      The full transcript: every model call the BUILDER (the agent doing the
      work) and the REVIEWER (the agent checking it) made, in the order they
      were made, each with everything sent to the model - the system prompt,
      the tools offered, every message - and everything it answered. Read the
      files in number order; a new file is begun at about 5 MB.

      Each call begins with a block like this:

          ================================================================
          CALL #0012 · BUILDER · round 3
          started  2026-10-09 14:32:05 +08:00
          finished 2026-10-09 14:32:40 +08:00 (35 s)
          model ... · input 12,345 tokens · output 678 tokens
          ================================================================

      A conversation sends its earlier messages again with every call. They
      are written again, whole, marked "sent before, in #0009"; the first
      message not sent before is marked NEW - search for "· NEW" to jump to
      what is new in a call.

  events.jsonl
      The live session: one line of JSON per moment of the run (a tool used,
      a verdict, a milestone, a version saved) - what the application's page
      shows as the run goes. Meant for programs; the transcript is for
      reading.

These files are the record of what happened. The agents can neither read nor
change them, and rolling the work back to an earlier version leaves them as
they are.
"""


def file_name(number: int) -> str:
    return f"transcript-{number:03d}.txt"


def transcript_files(session_dir: Path) -> List[Path]:
    """The transcript's files, in order."""
    try:
        found = [one for one in Path(session_dir).iterdir()
                 if one.is_file() and FILE_PATTERN.match(one.name)]
    except OSError:
        return []
    return sorted(found, key=lambda one: int(FILE_PATTERN.match(one.name).group(1)))


def last_number(text: str) -> int:
    """The number of the last record in a transcript file's text - written
    now or before the transcript was made readable. 0 for none."""
    found = HEADER.findall(text) or OLD_HEADER.findall(text)
    return int(found[-1]) if found else 0


# -- time, as the user reads it ---------------------------------------------------
def local_time(at: float) -> str:
    """A moment in this computer's time, with its offset from UTC:
    "2026-10-09 14:32:05 +08:00"."""
    moment = datetime.fromtimestamp(at).astimezone()
    offset = moment.strftime("%z") or "+0000"
    return f"{moment.strftime('%Y-%m-%d %H:%M:%S')} {offset[:3]}:{offset[3:]}"


def duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds} s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {seconds} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min {seconds} s"


# -- values, laid out to be read --------------------------------------------------
def _scalar(value: Any) -> Optional[str]:
    """A value that fits after "key: " on one line, as it reads - a string
    bare, a number or true/false/null as JSON. None for one that does not:
    a string of several lines, a dict or list with something in it."""
    if isinstance(value, str):
        if "\n" in value or "\r" in value:
            return None
        return value if value.strip() else json.dumps(value)
    if value is None or isinstance(value, (bool, int, float)):
        return json.dumps(value)
    if isinstance(value, (dict, list, tuple)) and not value:
        return "{}" if isinstance(value, dict) else "[]"
    if isinstance(value, (dict, list, tuple)):
        return None
    return str(value)


def value_lines(value: Any, depth: int = 0) -> List[str]:
    """Any value as lines to read, indented `depth` levels - whole. A dict a
    key a line, a list an item a line ("- "), a string of several lines on
    lines of its own, as it was written: code reads as code, output as
    output, never as one long string of escaped line breaks."""
    pad = " " * (INDENT * depth)
    one = _scalar(value)
    if one is not None:
        return [pad + one]
    if isinstance(value, str):
        return [(pad + line) if line.strip() else ""
                for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lines: List[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            short = _scalar(item)
            if short is not None:
                lines.append(f"{pad}{key}: {short}")
            else:
                lines.append(f"{pad}{key}:")
                lines += value_lines(item, depth + 1)
        return lines
    for item in value:
        short = _scalar(item)
        if short is not None:
            lines.append(f"{pad}- {short}")
            continue
        inner = value_lines(item, depth + 1)
        inner_pad = " " * (INDENT * (depth + 1))
        if isinstance(item, str) or not inner or not inner[0].startswith(inner_pad):
            # Lines of text: the dash alone, the text under it as written.
            lines.append(f"{pad}-")
            lines += inner
        else:
            lines.append(f"{pad}- {inner[0][len(inner_pad):]}")
            lines += inner[1:]
    return lines


def _parsed(text: str) -> Any:
    """A string that is JSON - as a tool's result is sent - as the value it
    holds; anything else as it is."""
    stripped = text.strip()
    if stripped[:1] in ("{", "[", '"'):
        try:
            return json.loads(stripped)
        except ValueError:
            pass
    return text


def _readable(value: Any, depth: int = 1) -> str:
    """A tool's arguments or result, laid out under its line."""
    if isinstance(value, str):
        value = _parsed(value)
    if value is None or value == "":
        return " " * (INDENT * depth) + "(nothing)"
    return "\n".join(value_lines(value, depth))


def _call_text(name: Any, call_id: Any, arguments: Any) -> str:
    return f"[tool call {name or ''} id={call_id or ''}]\n{_readable(arguments)}"


def _block(block: Any) -> str:
    """One part of a message: its text, a tool's result, a tool called."""
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        text = getattr(block, "text", None)
        if isinstance(text, str):
            return text
        name = getattr(block, "name", None)
        if name:
            return _call_text(name, getattr(block, "id", ""), getattr(block, "input", None))
        return str(block)
    kind = block.get("type")
    if kind == "text":
        return str(block.get("text") or "")
    if kind == "tool_result":
        content = block.get("content")
        if isinstance(content, list):
            content = "\n".join(_block(one) for one in content)
        failed = " (an error)" if block.get("is_error") else ""
        return (f"[tool result for {block.get('tool_use_id', '')}{failed}]\n"
                f"{_readable(content)}")
    if kind == "tool_use":
        return _call_text(block.get("name", ""), block.get("id", ""), block.get("input"))
    return f"[{kind or 'part'}]\n{_readable(block)}"


def message_body(message: Dict[str, Any]) -> str:
    """What one message says, whole, laid out to be read."""
    content = message.get("content")
    if isinstance(content, list):
        return "\n\n".join(_block(one) for one in content)
    return _block(content) if content is not None else ""


def message_text(message: Dict[str, Any]) -> str:
    return f"--- {message.get('role', '')} ---\n{message_body(message)}"


def answer_text(response: Any) -> str:
    """The whole answer: its text, then each tool it called with all of its
    arguments."""
    if response is None:
        return "(no answer)"
    parts = []
    # The tools it ran itself during the call (Claude Code, natively), in
    # order, whole - each with what it returned - before what it answered.
    for n, one in enumerate(getattr(response, "tool_exchanges", None) or [], start=1):
        parts.append(_call_text(str(one.get("name", "")), f"ran during the call #{n}",
                                one.get("arguments"))
                     + "\n" + ("returned an error: " if one.get("error") else "returned: ")
                     + str(one.get("result", "")))
    parts.append(str(getattr(response, "text", "") or ""))
    for call in getattr(response, "tool_calls", None) or []:
        parts.append(_call_text(getattr(call, "name", ""), getattr(call, "id", ""),
                                getattr(call, "arguments", None)))
    return "\n\n".join(one for one in parts if one) or "(empty answer)"


def _tool_definition(tool: Any) -> Tuple[str, str]:
    """A tool offered: its name, and all of what the model was told of it."""
    if isinstance(tool, dict):
        name = str(tool.get("name") or "")
        said = {k: v for k, v in tool.items() if k != "name"}
    else:
        name = str(getattr(tool, "name", ""))
        said = {"description": getattr(tool, "description", ""),
                "input_schema": getattr(tool, "input_schema", None)}
    return name, "\n".join([f"* {name}", *value_lines(said, 1)])


def ensure_readme(logs_root: Path, write: Any = None) -> None:
    """The project's `run_logs/` says what is in it - written once. `write`
    (relative name, text) writes it through the run's policy; without one
    it is written here, by the application itself."""
    target = Path(logs_root) / README_NAME
    if target.exists():
        return
    try:
        from ...common.agent_writes import mark_written
        # The application's own record: not heard as the user's activity.
        mark_written(str(target))
    except Exception:        # pragma: no cover - a nicety
        pass
    if write is not None:
        write(README_NAME, README_TEXT)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(README_TEXT, encoding="utf-8")


class Transcript:
    """The transcript of one run, written as its calls are made - across its
    sessions: a run continued goes on numbering where it left off."""

    def __init__(self, runtime: Any, folder: Path, relative_dir: str,
                 limit: int = FILE_BYTES) -> None:
        #: What writes - the run's runtime, through the work's policy.
        self.runtime = runtime
        self.folder = Path(folder)
        #: The run's folder of logs, relative to the work's folder.
        self.relative_dir = relative_dir.rstrip("/")
        self.limit = max(1024, int(limit))
        self._lock = threading.Lock()
        files = transcript_files(self.folder / self.relative_dir)
        self.number = int(FILE_PATTERN.match(files[-1].name).group(1)) if files else 1
        self.size = files[-1].stat().st_size if files else 0
        self.seq = 0
        if files:
            try:
                self.seq = last_number(files[-1].read_text(encoding="utf-8",
                                                           errors="replace"))
            except OSError:
                self.seq = 0
        #: Each agent's last call: its number, its system prompt and its
        #: messages as written - to say which of the next call's were sent
        #: before. Held for this session only: after a restart every message
        #: is new again, which is still true of what was sent.
        self._last: Dict[str, Tuple[int, str, List[str]]] = {}
        #: Each set of tools offered, by its fingerprint: the call whose
        #: record wrote their definitions out.
        self._tools_shown: Dict[str, int] = {}

    @property
    def files(self) -> List[Path]:
        return transcript_files(self.folder / self.relative_dir)

    def _append(self, number: int, text: str) -> None:
        relative = f"{self.relative_dir}/{file_name(number)}"
        try:
            from ...common.agent_writes import mark_written
            # The application's own record: not heard as the user's activity.
            mark_written(str(self.folder / relative))
        except Exception:        # pragma: no cover - a nicety
            pass
        self.runtime.write_text(relative, text, append=True)

    def _fits(self, text: str, budget: int) -> int:
        """How many characters of `text` take at most `budget` bytes."""
        n = min(len(text), budget)
        while n > 0 and len(text[:n].encode("utf-8")) > budget:
            excess = len(text[:n].encode("utf-8")) - budget
            n -= max(1, excess // 4)
        return max(1, n)

    def _write(self, record: str) -> None:
        data = record.encode("utf-8")
        if self.size and self.size + len(data) > self.limit:
            # A record is not split across files: the next one is begun.
            self.number += 1
            self.size = 0
        if len(data) <= self.limit - self.size:
            self._append(self.number, record)
            self.size += len(data)
            return
        # LARGER THAN A FILE MAY BE: cut into parts, each in a file of its
        # own, each saying where it goes on.
        rest = record
        part = 1
        while rest:
            mark_on = f"\n----- (record continued in {file_name(self.number + 1)}) -----\n"
            budget = self.limit - self.size - len(mark_on.encode("utf-8")) - 128
            take = self._fits(rest, max(1024, budget))
            chunk, rest = rest[:take], rest[take:]
            if part > 1:
                chunk = (f"----- (record continued from {file_name(self.number - 1)}, "
                         f"part {part}) -----\n") + chunk
            if rest:
                chunk += mark_on
            self._append(self.number, chunk)
            self.size += len(chunk.encode("utf-8"))
            if rest:
                self.number += 1
                self.size = 0
                part += 1

    def _tools_lines(self, tools: Optional[List[Any]]) -> List[str]:
        """The tools offered: their names, and - the first time this set is
        offered - every definition, whole; after that, where it was."""
        if not tools:
            return ["--- tools offered: none ---"]
        defined = [_tool_definition(one) for one in tools]
        names = [name for name, _text in defined]
        lines = [f"--- tools offered ({len(names)}): {', '.join(names)} ---"]
        whole = "\n".join(text for _name, text in defined)
        key = hashlib.sha256(whole.encode("utf-8")).hexdigest()
        if key in self._tools_shown:
            lines.append(f"(their definitions are as written out in #{self._tools_shown[key]:04d})")
        else:
            self._tools_shown[key] = self.seq
            lines.append(whole)
        return lines

    def record(self, who: str, part: str, model: str, tokens_in: int, tokens_out: int,
               system: str, messages: List[Dict[str, Any]], response: Any = None,
               tools: Optional[List[Any]] = None, error: str = "",
               started: Optional[float] = None, cached: int = 0,
               cache_written: int = 0) -> int:
        """One model call, whole: who made it, in which part of the run,
        when it started (`started`, seconds since the epoch - measured
        before it was made) and finished, what went in and what came out.
        `tokens_in` is all that went in; `cached` of it was read from the
        provider's prompt cache and `cache_written` written into it - said
        beside it, so "input" is never the handful that was neither. Its
        number."""
        with self._lock:
            self.seq += 1
            finished = time.time()
            started = finished if started is None else min(float(started), finished)
            title = " · ".join(one for one in (f"CALL #{self.seq:04d}",
                                               WHO.get(who, str(who or "-").upper()),
                                               str(part or "").strip()) if one)
            header = [RULE, title,
                      f"started  {local_time(started)}",
                      f"finished {local_time(finished)} ({duration(finished - started)})",
                      f"model {model or '-'} · input {int(tokens_in):,} tokens"
                      + (f" ({int(cached or 0):,} read from the cache, "
                         f"{int(cache_written or 0):,} written to it)"
                         if cached or cache_written else "")
                      + f" · output {int(tokens_out):,} tokens",
                      RULE]
            # What this agent sent before - its last call's system prompt
            # and messages: the ones this call begins with again are said so.
            before_seq, before_system, before_messages = self._last.get(who, (0, None, []))
            bodies = [message_body(one) for one in messages or []]
            roles = [str((one or {}).get("role", "")) for one in messages or []]
            same = 0
            while (same < len(bodies) and same < len(before_messages)
                   and bodies[same] == before_messages[same]):
                same += 1
            total = len(bodies)
            lines = [*header, "", "=== INPUT ==="]
            if same:
                lines.append(f"(messages 1-{same} of {total} were sent before, in "
                             f"#{before_seq:04d}"
                             + (f"; what is new begins at message {same + 1})"
                                if same < total else "; nothing new)"))
            lines.append("")
            again = before_seq and system == before_system
            lines += [f"--- system prompt{f' (same as in #{before_seq:04d})' if again else ''} ---",
                      system or "(none)", ""]
            lines += self._tools_lines(tools)
            lines.append("")
            for n, (role, body) in enumerate(zip(roles, bodies), start=1):
                mark = (f" (sent before, in #{before_seq:04d})" if n <= same
                        else " · NEW")
                lines += [f"--- message {n} of {total} · {role}{mark} ---", body, ""]
            if not total:
                lines += ["(no messages)", ""]
            lines += ["=== OUTPUT ===",
                      f"(the call failed: {error})" if error else answer_text(response),
                      "", "", ""]
            self._last[who] = (self.seq, system or "", bodies)
            self._write("\n".join(lines))
            return self.seq
