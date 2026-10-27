"""Coding assistant activity (sections 18, 25, and the amended 9).

Work done through an assistant - Claude Code and tools like it - is work the
user did, and an application name alone does not describe it: "Claude Code,
2h 10m" says nothing about which project was worked on.

These tools keep their own local session store, so this provider reads it the
same way the browser provider reads local history: on a poll, and only what the
user has enabled.

Two things are recorded, and they are separately controlled.

**The shape of a session, always.** That one happened, in which project, for how
long, and with how much back and forth. No content is read for this.

**What was said, only when asked for** (section 18, as amended). A coding
assistant is where a large part of the user's thinking now happens, and an
agent that may not read it is guessing at the work it exists to help with. So
the conversation can be recorded - but it is the most personal thing this
application can hold, so it is recorded only for the assistants ticked (every
one known, by default - Settings > Privacy), it stops when
monitoring is turned off like any other collection, and the parts of a transcript that are
bulk rather than conversation - the assistant's reasoning, tool calls, tool
results - stay out unless separately asked for.

Transcripts are tailed by byte offset rather than re-read, so a session that
runs for hours is read once as it grows.
"""

from __future__ import annotations

import collections
import io
import json
import os
import logging
import threading
import time
from dataclasses import dataclass
from fnmatch import fnmatch
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...common.agent_sessions import is_agent_session, is_the_agent_itself
from ...common.events.models import ListenerEvent
from ..core.provider import ListenerProvider, NotOnThisMachine, PluginManifest
from ..core.manager import STOPPING_GRACE_SECONDS

log = logging.getLogger(__name__)

#: Fields that carry what was said. Read only when the user has asked for
#: conversations to be recorded; named here so that the summary path can be
#: tested to leave them alone rather than merely claiming to.
CONTENT_FIELDS = ("content", "display", "pastedContents", "message", "text",
                  "summary", "toolUseResult")

#: Where a turn is cut when it runs long, unless the setting says otherwise.
DEFAULT_MAX_CHARS = 4000


def _lines(raw: Any) -> List[str]:
    """A multiline setting as the list of things the user actually typed."""
    if isinstance(raw, (list, tuple)):
        entries = [str(item) for item in raw]
    else:
        entries = str(raw or "").splitlines()
    return [line.strip() for line in entries if line.strip()]


def _matches_any(value: str, patterns: List[str]) -> bool:
    """Whether one project or tool matches any line the user wrote.

    A line matches what it names, anything inside it, or a glob - so a single
    line can name one project or a whole research directory, and the user does
    not have to know which form is expected.
    """
    if not patterns:
        return False
    text = str(value or "").replace("\\", "/").rstrip("/")
    lowered = text.lower()
    for pattern in patterns:
        rule = str(pattern).replace("\\", "/").rstrip("/").lower()
        if not rule:
            continue
        if lowered == rule or lowered.startswith(rule + "/"):
            return True
        if ("*" in rule or "?" in rule) and fnmatch(lowered, rule):
            return True
    return False


@dataclass(frozen=True)
class AssistantTool:
    """A tool whose local session store can be read."""

    key: str
    display_name: str
    #: Directory holding one subdirectory per project, each with session files.
    sessions_root: Path

    def available(self) -> bool:
        return self.sessions_root.is_dir()


def known_tools() -> List[AssistantTool]:
    home = Path.home()
    return [
        AssistantTool("claude_code", "Claude Code", home / ".claude" / "projects"),
    ]


def _decode_project(name: str) -> str:
    """Last resort only: the directory name with its separators restored.

    The encoding replaces every separator with a dash, so a folder whose own
    name contains one cannot be recovered - "CSS ranking" and "CSS-ranking"
    encode identically. The session file records the real path, so this is used
    only when that is missing.
    """
    return name or ""


class AssistantProvider(ListenerProvider):
    provider_id = "assistant"
    category = "assistant"

    def __init__(self, config: Any, platform: Any = None,
                 poll_seconds: float = 120.0) -> None:
        super().__init__()
        self.config = config
        self.platform = platform
        self.poll_seconds = poll_seconds
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._tools = known_tools()
        #: session id -> (last activity, records seen), so a session that is
        #: still being worked in is reported once per change, not once per poll.
        self._seen: Dict[str, Tuple[str, int]] = {}
        self._emitted = 0
        self._sources: Dict[str, str] = {}
        #: transcript path -> how far into it we have read. A session that
        #: runs for hours is read once as it grows, not re-read each poll.
        self._offsets: Dict[str, int] = {}
        #: Nothing said before recording was switched on is swept up when it is.
        self._primed = False
        #: transcript path -> (shape, summary). Reading a transcript means
        #: parsing every line of it as JSON, and a poll re-read all of them:
        #: measured on one machine, **2,277 files and 557 MB every two
        #: minutes**, taking 4.4 seconds of a core to learn that almost
        #: nothing had changed. A file whose size and modification time are
        #: where they were is the file we already read.
        self._summaries: "collections.OrderedDict[str, Tuple[tuple, Any]]" = \
            collections.OrderedDict()
        #: transcript path -> (bytes folded, modification time, file identity,
        #: tally of those bytes, the last bytes of them). What lets a
        #: transcript that grew be read from where it was left - see
        #: _summarise.
        self._tallies: Dict[str, tuple] = {}

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        if not any(tool.available() for tool in self._tools):
            raise NotOnThisMachine("no assistant session store was found")
        # Every start begins at the end of the transcripts as they stand.
        #
        # This provider is stopped whenever collection is off - monitoring or
        # the category switched off, the application not running - and the
        # transcripts go on being written the whole time. Resuming from the
        # old position would read back exactly the stretch the user had
        # collection turned off for, which is the one thing turning it off is
        # for. So what was said while this was not running was said while this
        # was not running, and recording begins again from now.
        self._primed = False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="listener-assistant",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=STOPPING_GRACE_SECONDS)

    #: QUIET AT START (measured 2026-10-26): the first pass reads every
    #: session there is, and it took the interpreter from the desktop window
    #: as the application came up. Where recording starts from is fixed at
    #: once - a look at each file's size - and the pass waits this long.
    QUIET_AT_START = 60.0

    def _loop(self) -> None:
        if self.QUIET_AT_START > 0:
            self._skip_to_end()
            self._primed = True
            if self._stop.wait(self.QUIET_AT_START):
                return
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception as exc:                # pragma: no cover - defensive
                self.last_error = str(exc)
                log.debug("assistant poll failed", exc_info=True)
            self._stop.wait(self.poll_seconds)

    # -- reading ------------------------------------------------------------
    def _sessions(self, tool: AssistantTool) -> List[Dict[str, Any]]:
        """One summary per session file. Only timestamps and counts are read."""
        sessions: List[Dict[str, Any]] = []
        cutoff = time.time() - 30 * 86400
        try:
            project_dirs = [d for d in tool.sessions_root.iterdir() if d.is_dir()]
        except OSError as exc:
            self._sources[tool.key] = f"unreadable: {exc}"
            return sessions
        seen = set()
        for project_dir in project_dirs:
            for path in project_dir.glob("*.jsonl"):
                # THE AGENT'S OWN, NOT READ AT ALL. Its model calls are filed
                # under their session id, which carries the agent's mark, and
                # every one of them was dropped by `poll` - after it had been
                # read through. Measured on one machine: 14,700 of 15,100
                # transcripts and 2.15 of 2.6 GB were the agent's own, parsed
                # line by line every two minutes to be thrown away - over half
                # the CPU the whole application used.
                if is_agent_session(path.stem):
                    continue
                try:
                    # One stat serves both questions - old enough to skip,
                    # and changed since it was last read - because at two
                    # thousand files a second stat each is half the cost of
                    # the whole pass.
                    info = path.stat()
                except OSError:
                    continue
                if info.st_mtime < cutoff:
                    continue
                seen.add(str(path))
                summary = self._summary_of(path, info)
                if summary is None:
                    continue
                summary["project"] = summary.get("project") or _decode_project(project_dir.name)
                summary["tool"] = tool.display_name
                summary["tool_key"] = tool.key
                sessions.append(summary)
        # What is no longer there, or no longer recent, is forgotten - so the
        # memory holds this pass's files, and never pushes out one of them
        # to make room for one that has gone.
        prefix = str(tool.sessions_root)
        for key in [key for key in self._summaries
                    if key.startswith(prefix) and key not in seen]:
            del self._summaries[key]
        for key in [key for key in self._tallies
                    if key.startswith(prefix) and key not in seen]:
            del self._tallies[key]
        self._sources[tool.key] = f"{len(sessions)} recent sessions"
        return sessions

    #: How many transcripts are remembered. Enough for a month of them on a
    #: machine that is used heavily; the oldest is dropped first, and dropping
    #: one costs a re-read rather than a wrong answer. A pass reads every file
    #: in the same order, so a memory smaller than the pass drops each file
    #: just before it comes round again and nothing is ever remembered.
    REMEMBERED = 20000

    def _summary_of(self, path: Path,
                    info: Optional[os.stat_result] = None) -> Optional[Dict[str, Any]]:
        """The summary of a transcript, read only if it has changed.

        The shape is the size and the modification time together. Either one
        alone can stay still across a real change - a transcript rewritten in
        place keeps its size, and a clock with a one-second resolution can
        hide a write - and a transcript only ever grows, so the pair moving
        is what a new line looks like.
        """
        try:
            if info is None:
                info = path.stat()
            shape = (info.st_mtime_ns, info.st_size)
        except OSError:
            self._tallies.pop(str(path), None)
            return None
        key = str(path)
        known = self._summaries.get(key)
        if known is not None and known[0] == shape:
            self._summaries.move_to_end(key)
            return dict(known[1]) if known[1] is not None else None
        summary = self._summarise(path)
        self._summaries[key] = (shape, dict(summary) if summary else None)
        self._summaries.move_to_end(key)
        while len(self._summaries) > self.REMEMBERED:
            dropped, _ = self._summaries.popitem(last=False)
            self._tallies.pop(dropped, None)
        return summary

    @staticmethod
    def _fold(handle: Any, tally: Dict[str, Any]) -> None:
        """Add every line read from `handle` to a running summary.

        Everything a summary holds can be carried forward a line at a time -
        the earliest and latest timestamp, the last session id, the first
        working directory, a count - so reading a transcript in two pieces
        and folding both into one tally gives what reading it whole gives.
        """
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            # Only these keys are ever taken from the store.
            stamp = entry.get("timestamp")
            if isinstance(stamp, str) and stamp:
                first, last = tally["first"], tally["last"]
                tally["first"] = stamp if first is None or stamp < first else first
                tally["last"] = stamp if last is None or stamp > last else last
            identifier = entry.get("sessionId")
            if isinstance(identifier, str) and identifier:
                tally["session_id"] = identifier
            # The session records where it was working; the directory
            # name is a lossy encoding of the same thing.
            if tally["project"] is None:
                where = entry.get("cwd")
                if isinstance(where, str) and where:
                    tally["project"] = where
            tally["records"] += 1

    @staticmethod
    def _fresh_tally(path: Path) -> Dict[str, Any]:
        return {"first": None, "last": None, "records": 0,
                "session_id": path.stem, "project": None}

    @staticmethod
    def _as_summary(path: Path, tally: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not tally["records"] or tally["last"] is None:
            return None
        return {"session_id": tally["session_id"], "started_at": tally["first"],
                "last_activity": tally["last"], "records": tally["records"],
                "project": tally["project"], "path": str(path)}

    @staticmethod
    def _summarise_whole(path: Path) -> Optional[Dict[str, Any]]:
        """Timestamps and a record count, from a reading of the whole file.

        What `_summarise` must always agree with; kept as the reference it
        is tested against.
        """
        tally = AssistantProvider._fresh_tally(path)
        try:
            with path.open(encoding="utf-8", errors="ignore") as handle:
                AssistantProvider._fold(handle, tally)
        except OSError:
            return None
        return AssistantProvider._as_summary(path, tally)

    #: How many bytes just before the point already read are compared on the
    #: next pass, to tell a transcript that grew from one that was replaced.
    #: A transcript only ever grows; a file rewritten in place does not end
    #: its old length with the same bytes it ended with before.
    SEAM = 256

    def _summarise(self, path: Path) -> Optional[Dict[str, Any]]:
        """Timestamps and a record count. Nothing that was said is read.

        The same summary as `_summarise_whole`, reading only what was
        appended since the last time.

        THE TRANSCRIPT BEING WRITTEN IS THE BIG ONE. A session in progress
        changes on every turn, so it was re-parsed from the start on every
        pass - measured on one machine, a 312 MB live session took **5.2 s
        of a core** each time, to add the handful of lines written in the
        last two minutes. What was already read is folded into a tally and
        kept, with the byte offset just past its last complete line, and the
        next pass parses only from there.

        Only whole lines are kept in the tally. A last line still being
        written - no newline yet - is parsed for this pass's answer, as the
        full read would parse it, and parsed again next time when it is
        finished. Anything that does not look like the same file having
        grown - shorter than before, older than before, another file under
        the same name, or different bytes where the old end was - is read
        again from the start, which is what was always done.
        """
        try:
            info = path.stat()
        except OSError:
            self._tallies.pop(str(path), None)
            return None
        key = str(path)
        known = self._tallies.get(key)
        identity = (getattr(info, "st_dev", 0), getattr(info, "st_ino", 0))
        start, tally, seam = 0, None, b""
        if known is not None:
            offset, old_mtime, old_identity, old_tally, old_seam = known
            if (info.st_size >= offset and info.st_mtime_ns >= old_mtime
                    and identity == old_identity):
                start, tally, seam = offset, dict(old_tally), old_seam
        try:
            with path.open("rb") as raw:
                if start:
                    raw.seek(start - len(seam))
                    if raw.read(len(seam)) != seam:
                        start, tally, seam = 0, None, b""
                        raw.seek(0)
                if tally is None:
                    tally = self._fresh_tally(path)
                read, carry, seam = self._fold_whole_lines(raw, tally, seam)
        except OSError:
            self._tallies.pop(key, None)
            return None
        end = start + read
        if end:
            self._tallies[key] = (end, info.st_mtime_ns, identity, dict(tally), seam)
        else:
            self._tallies.pop(key, None)
        if carry:
            # The unfinished line: in this answer, not in what is kept.
            answer = dict(tally)
            self._fold(self._text(carry), answer)
            tally = answer
        return self._as_summary(path, tally)

    #: How much of a transcript is decoded at a time. A first read of a long
    #: session goes through it in pieces this size rather than holding all
    #: of it - 312 MB on the machine this was measured on - at once.
    BLOCK = 8 * 1024 * 1024

    @staticmethod
    def _text(data: bytes) -> Any:
        """Bytes as the lines `path.open(encoding="utf-8", errors="ignore")`
        would give - the same decoder and the same newline handling."""
        return io.TextIOWrapper(io.BytesIO(data), encoding="utf-8", errors="ignore")

    def _fold_whole_lines(self, raw: Any, tally: Dict[str, Any],
                          seam: bytes) -> Tuple[int, bytes, bytes]:
        """Fold every complete line from here to the end of the file.

        Returns how many bytes that was, what is left after the last
        newline, and the last `SEAM` bytes of what was folded.

        Pieces are cut just after a newline byte. In UTF-8 a newline is a
        byte of its own, so a cut there never splits a character, and the
        decoder - which has nothing pending after a newline - starts the
        next piece exactly as it would have carried on: the same text,
        split into the same lines.
        """
        read, carry = 0, b""
        while True:
            block = raw.read(self.BLOCK)
            if not block:
                return read, carry, seam
            if carry:
                block, carry = carry + block, b""
            cut = block.rfind(b"\n") + 1
            if not cut:
                carry = block
                continue
            self._fold(self._text(block[:cut]), tally)
            read += cut
            seam = (seam + block[max(0, cut - self.SEAM):cut])[-self.SEAM:]
            carry = block[cut:]

    # -- the conversation itself (section 18, as amended) -------------------
    def _content_settings(self) -> Dict[str, Any]:
        get = self.config.get if self.config is not None else (lambda *a: None)
        try:
            limit = int(get("listener_assistant_max_chars", DEFAULT_MAX_CHARS))
        except (TypeError, ValueError):
            limit = DEFAULT_MAX_CHARS
        # Section 18, as amended: the list of assistants is the switch.
        #
        # A separate on/off beside a list of what to record is two controls for
        # one decision, and they can disagree - on with nothing ticked, or off
        # with everything ticked, both of which have to mean something and
        # neither of which reads as anything. What is ticked is recorded;
        # nothing ticked records nothing.
        # "*" - the default - is every assistant found, one installed later too.
        only_tools = _lines(get("listener_assistant_content_tools", "*"))
        return {
            "enabled": bool(only_tools),
            "thinking": bool(get("listener_assistant_include_thinking", False)),
            "tools": bool(get("listener_assistant_include_tool_calls", False)),
            "limit": max(200, min(100000, limit)),
            "only_tools": only_tools,
            "only_projects": _lines(get("listener_assistant_content_projects", "")),
            "never_projects": _lines(get("listener_assistant_content_exclusions", "")),
        }

    def _content_allowed(self, tool: "AssistantTool", project: str,
                         options: Dict[str, Any]) -> bool:
        """Whether this session's conversation may be read.

        Recording what was said is not one decision for the whole machine. A
        coding assistant is used for everything the user does, and the reason
        to record it - so the agent can see the work it is helping with - does
        not apply to every project on the disk equally. So the switch turns it
        on, and these say where it applies.

        An empty list of projects means "no restriction" - all of the ticked
        assistant's projects. An empty list of assistants means none at all,
        because that list is the switch. The exclusions are checked last, so a
        directory can be recorded with one project inside it held back.
        """
        # An assistant is read only if it was ticked. There is no "empty means
        # all" here any more: empty is what the user chooses when they want
        # none of this recorded, and it has to mean that.
        if not (_matches_any(tool.display_name, options["only_tools"])
                or _matches_any(tool.key, options["only_tools"])):
            return False
        if options["only_projects"] and not _matches_any(
                project, options["only_projects"]):
            return False
        if _matches_any(project, options["never_projects"]):
            return False
        return True

    @staticmethod
    def _text_of(content: Any, thinking: bool, tools: bool) -> str:
        """One message's blocks, flattened to what a person would call its text.

        A transcript block is one of several kinds and most of a long session
        is not conversation: reasoning restates the reply, and a tool result
        can be an entire file. Each is included only when asked for.
        """
        if isinstance(content, str):
            return content.strip()
        if not isinstance(content, list):
            return ""
        parts: List[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                parts.append(str(block.get("text", "")))
            elif kind == "thinking" and thinking:
                parts.append("[thinking] " + str(block.get("thinking", "")))
            elif kind == "tool_use" and tools:
                parts.append(f"[tool: {block.get('name', '?')}] "
                             + json.dumps(block.get("input", {}),
                                          ensure_ascii=False)[:400])
            elif kind == "tool_result" and tools:
                body = block.get("content")
                text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
                parts.append("[result] " + str(text)[:400])
        return "\n".join(part for part in parts if part).strip()

    def _turns(self, path: Path, tool: AssistantTool, options: Dict[str, Any]) -> int:
        """Emit whatever has been appended to one transcript since last time.

        The cursor is a byte offset per file: a session that runs for hours is
        read once as it grows, not re-read from the beginning on every poll.
        """
        key = str(path)
        try:
            size = path.stat().st_size
        except OSError:
            return 0
        start = int(self._offsets.get(key, 0))
        if start > size:                     # the file was rotated or truncated
            start = 0
        if start == size:
            return 0
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(start)
                chunk = handle.read()
                self._offsets[key] = handle.tell()
        except OSError:
            return 0

        emitted = 0
        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue                     # a line still being written
            if not isinstance(entry, dict) or entry.get("type") not in ("user", "assistant"):
                continue
            message = entry.get("message") or {}
            text = self._text_of(message.get("content"), options["thinking"],
                                 options["tools"])
            if not text:
                continue                     # a turn that was only tool traffic
            cut = len(text) > options["limit"]
            if cut:
                text = text[:options["limit"]]
            self.emit(ListenerEvent(
                event_type="assistant.message",
                source=self.provider_id,
                application=tool.display_name,
                timestamp=_parse(entry.get("timestamp")),
                data={
                    "tool": tool.display_name,
                    "role": entry.get("type"),
                    "text": text,
                    "truncated": cut,
                    "session_id": str(entry.get("sessionId") or path.stem),
                    "project": entry.get("cwd") or "",
                    "branch": entry.get("gitBranch") or "",
                    "model": message.get("model") or "",
                    "sidechain": bool(entry.get("isSidechain")),
                },
                metadata={"collection": "session_store", "content": "included"},
            ))
            emitted += 1
            self._emitted += 1
        return emitted

    def _skip_to_end(self) -> None:
        """Move every cursor to the end of its file.

        Used when recording is off or suspended, so that switching it on does
        not sweep up everything said while it was not running. What was said
        while collection was off was said while collection was off.
        """
        for tool in self._tools:
            if not tool.available():
                continue
            try:
                for path in tool.sessions_root.glob("**/*.jsonl"):
                    # The agent's own are never read for what was said.
                    if is_agent_session(path.stem):
                        continue
                    try:
                        self._offsets[str(path)] = path.stat().st_size
                    except OSError:
                        continue
            except OSError:
                continue

    def _agent_root(self) -> str:
        """Where this application keeps its own storage, if it has said."""
        get = self.config.get if self.config is not None else (lambda *a: None)
        try:
            return str(get("agent_root", "") or "")
        except Exception:                        # a config that cannot answer
            return ""

    def poll(self) -> int:
        emitted = 0
        options = self._content_settings()
        if not options["enabled"]:
            # Not merely "do not emit": the cursors follow the files, so that
            # turning this on later starts from now rather than from history.
            self._skip_to_end()
            self._primed = True
        elif not self._primed:
            # First poll with recording on. Everything already in the
            # transcripts was said before the user asked for any of it to be
            # recorded, so it is not swept up now.
            self._skip_to_end()
            self._primed = True
        for tool in self._tools:
            if not tool.available():
                self._sources[tool.key] = "not installed"
                continue
            for session in self._sessions(tool):
                # Section 18, as amended: a session this application started
                # is the agent answering itself, not work done at this desk.
                # The provider runs the client to think with, the client
                # writes that run into the same store read here, and without
                # this every model call arrived back a moment later as the
                # user's activity - attributed to whichever window was in
                # front while the agent worked in the background.
                if is_the_agent_itself(session["session_id"], session["project"],
                                       self._agent_root()):
                    continue
                key = f"{tool.key}:{session['session_id']}"
                previous = self._seen.get(key)
                fingerprint = (session["last_activity"], session["records"])
                if previous == fingerprint:
                    continue
                self._seen[key] = fingerprint
                # Decided per session, so the event itself records whether its
                # conversation was read - which is the only way to tell later
                # that a session was seen but deliberately not transcribed.
                record_content = options["enabled"] and self._content_allowed(
                    tool, session["project"], options)
                emitted += 1
                self._emitted += 1
                self.emit(ListenerEvent(
                    event_type="assistant.session",
                    source=self.provider_id,
                    application=session["tool"],
                    timestamp=_parse(session["last_activity"]),
                    data={
                        "tool": session["tool"],
                        "project": session["project"],
                        "session_id": session["session_id"],
                        "started_at": session["started_at"],
                        "last_activity": session["last_activity"],
                        "exchanges": session["records"],
                    },
                    metadata={"collection": "session_store",
                              "content": "included" if record_content else "excluded"},
                ))
                if record_content:
                    try:
                        emitted += self._turns(Path(session["path"]), tool, options)
                    except Exception as exc:      # a transcript must not stop the rest
                        self.last_error = str(exc)[:200]
                        log.debug("could not read a transcript", exc_info=True)
        return emitted

    # -- interface ----------------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "tools": dict(self._sources),
            "emitted": self._emitted,
            "last_error": self.last_error,
        }

    def supported_event_types(self) -> List[str]:
        return ["assistant.session"]

    def get_capabilities(self) -> Dict[str, Any]:
        return {
            "session_stores": [tool.display_name for tool in self._tools
                               if tool.available()],
            "realtime": False,
            "records_content": False,
        }

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            plugin_id=self.provider_id,
            version="1.0.0",
            supported_platforms=["windows", "macos", "linux"],
            supported_event_types=self.supported_event_types(),
            required_permissions=["read_assistant_sessions"],
            description="Assistant sessions: which tool, which project, when and how "
                        "much exchange - never what was said.",
        )

    def describe(self) -> Dict[str, Any]:
        return {
            "provider": self.provider_id,
            "category": self.category,
            "tools": [
                {"tool": tool.display_name, "available": tool.available()}
                for tool in self._tools
            ],
            "records": "which project, when, and how much - never what was said",
            "supported_event_types": self.supported_event_types(),
        }


def _parse(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


__all__ = ["AssistantProvider", "AssistantTool", "known_tools", "CONTENT_FIELDS",
           "timedelta"]
