"""One memory, as a file (section 39.1, as amended).

A record is a markdown file with a small front matter block. The format is
chosen for the reader rather than for the parser: someone who opens
``user_nature/`` in a text editor should be able to see what the agent thinks
of them and change it, and that is the whole reason the library replaced a
graph in a database.

Which means the parser has to be forgiving. A file the user has edited by hand
is the normal case, not the error case: a missing field, a stray blank line, a
key the user invented are all things to survive rather than to reject. The one
thing that cannot be recovered from is an unreadable file, and that is skipped
and reported rather than allowed to stop a round.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...common.events.models import new_id, utcnow

#: The folders a record can live in. These are the categories retrieval asks
#: for, so they are part of the interface rather than an arrangement of files.
USER_NATURE = "user_nature"       # habits, character, how the user works
USER_FEEDBACK = "user_feedback"   # the user's verdict on what the agent did
STATES = "states"                 # what was true over a span
STATE_TASKS = "state_tasks"       # what a run of states adds up to
OTHER = "others"                  # everything observed that is none of those
AGENT_LESSON = "agent-lesson"     # what the agent has worked out about working

#: The folders kept. `others` and `agent-lesson` are names only now: the
#: data heartbeat wrote the one and the chat's memory tools the other, and
#: nothing writes or reads either any more.
FOLDERS = (USER_NATURE, USER_FEEDBACK, STATES, STATE_TASKS)

#: Where the index lives. Not a folder of records - nothing writes memories
#: here, and it can be deleted and rebuilt from the other five at any time.
INDEX = "index"

_FRONT = "---"
_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")


#: The heading a summary's own index sits under, in its body.
_INDEX_HEADING = "## What this covers, by term"

_INDEX_BLOCK = re.compile(
    re.escape(_INDEX_HEADING) + r"\s*```json\s*(\{.*?\})\s*```", re.DOTALL)


def _split_child_index(body: str):
    """Take the summary's own index back out of its body.

    Returned separately so that the prose a person reads and the structure a
    search uses do not have to be the same thing, while still living in one
    file. A body with no such block - every record that is not a summary -
    comes back unchanged with an empty index.
    """
    match = _INDEX_BLOCK.search(body or "")
    if not match:
        return body, {}
    try:
        parsed = json.loads(match.group(1))
    except ValueError:
        return body, {}
    if not isinstance(parsed, dict):
        return body, {}
    index = {str(term): [str(i) for i in ids]
             for term, ids in parsed.items() if isinstance(ids, list)}
    return (body[:match.start()] + body[match.end():]).strip(), index


def _parse_bool(value: Any, default: bool = True) -> bool:
    text = str(value).strip().lower()
    if text in ("true", "yes", "1"):
        return True
    if text in ("false", "no", "0"):
        return False
    return default


def _parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _parse_time(value: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class Record:
    """One thing the agent knows, and where it came from."""

    folder: str
    title: str
    id: str = field(default_factory=lambda: new_id("rec"))
    summary: str = ""
    body: str = ""
    keywords: List[str] = field(default_factory=list)
    #: What produced this: heartbeat, experience, agent, user_feedback,
    #: scheduler, user. One word, and it answers "who says so" - and a
    #: writer that does not say is not guessed at.
    source: str = ""
    #: What it was drawn from, concretely enough to go back and look: raw
    #: event ids, file paths, an application, a task or question id, a URL.
    #:
    #: The source says which part of the system wrote it; a reference says
    #: which particular thing it was about. Without them a record is an
    #: assertion with no way to check it - which is the failure the graph
    #: made structural, and putting these in files fixes nothing on its own
    #: if the files do not say where their contents came from.
    references: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=utcnow)
    #: When the file was last written. Equal to created_at for everything
    #: except a state, which is rewritten whenever it changes (section 34.2).
    updated_at: Optional[datetime] = None
    #: The stretch of activity this was drawn from (section 49.5). Separate
    #: from created_at because when a thing happened and when it was written
    #: down are different facts, and a round can write about yesterday.
    observed_from: Optional[datetime] = None
    observed_to: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    #: Where this came from on disk. Empty for a record not yet written.
    path: str = ""
    #: How far above the raw observations this sits (section 50.2, as
    #: amended). 0 is something that was actually observed; 1 summarises
    #: level 0 records; 2 summarises level 1, and so on.
    level: int = 0
    #: The records this one summarises, by id. Empty for a level 0 record.
    #: Together with ``references``, which carries their paths, this is the
    #: pointer down to what the summary was made from - a summary that cannot
    #: be opened out into its originals is a claim with the evidence deleted.
    covers: List[str] = field(default_factory=list)
    #: An inverted index over those records: term to the ids under this
    #: summary that carry it (section 50.4, as amended).
    #:
    #: This is what makes a summary an index rather than a label. The library's
    #: own index says which summary to open; this says which of the things
    #: underneath it to open next. Without it, descending means reading a list
    #: of forty titles and guessing - which costs a model call per level and
    #: makes a wide summary expensive to go through. With it, going down is the
    #: same operation as searching at the top: look the terms up, get the few
    #: that match.
    child_index: Dict[str, List[str]] = field(default_factory=dict)
    #: The id of the summary that covers this one, if any. Kept even after a
    #: record comes back into the index: that a summary was made from it is a
    #: fact about both of them, and the summary still lists it.
    summarised_by: str = ""
    #: Which languages this record's keywords are written in (section 12.2,
    #: as amended). Empty means unknown - a record written before the setting
    #: existed - which is treated as needing the current language added.
    #:
    #: The record's own text is never translated. Only the words it is found
    #: by are added to, because what it says should stay as it was written
    #: while the way in has to exist in the language the user asks in.
    keyword_languages: List[str] = field(default_factory=list)
    #: Whether search can reach this directly (section 50.2, as amended).
    #:
    #: **This, and not ``summarised_by``, is what decides.** The two usually
    #: agree - folding a record sets one and clears the other - but they say
    #: different things, and the difference is the whole of reactivation. A
    #: record that was folded away and then turned out to be wanted again goes
    #: back into the index while still being covered by the summary that was
    #: made from it.
    #:
    #: Every object has it, summaries included. A summary is an ordinary
    #: record: it is indexed, it is retrieved, it goes cold, and it is folded
    #: into a summary above it. Nothing about the chain is special-cased.
    to_be_indexed: bool = True

    # -- the shape the rest of the system already expects --------------------
    # Retrieval used to hand back graph nodes and seven call sites read
    # `.type` and `.content` off them. A record is what they get now, and
    # renaming the fields at every one of those sites would be churn for its
    # own sake.
    @property
    def type(self) -> str:
        return self.folder

    @property
    def content(self) -> str:
        return self.body

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "folder": self.folder, "type": self.folder,
            "title": self.title, "summary": self.summary, "body": self.body,
            "keywords": list(self.keywords), "source": self.source,
            "references": list(self.references),
            "keyword_languages": list(self.keyword_languages),
            "level": self.level, "covers": list(self.covers),
            "child_index": {k: list(v) for k, v in self.child_index.items()},
            "summarised_by": self.summarised_by,
            "to_be_indexed": self.to_be_indexed,
            "created_at": self.created_at.isoformat(),
            "updated_at": (self.updated_at or self.created_at).isoformat(),
            "observed_from": self.observed_from.isoformat() if self.observed_from else None,
            "observed_to": self.observed_to.isoformat() if self.observed_to else None,
            "metadata": dict(self.metadata), "path": self.path,
        }

    # -- the file format ----------------------------------------------------
    def to_text(self) -> str:
        lines = [_FRONT,
                 f"id: {self.id}",
                 f"title: {self.title}",
                 f"summary: {self.summary}",
                 f"keywords: {', '.join(self.keywords)}",
                 f"source: {self.source}",
                 f"references: {'; '.join(self.references)}",
                 f"keyword_languages: {', '.join(self.keyword_languages)}",
                 f"level: {self.level}",
                 f"created_at: {self.created_at.isoformat()}",
                 f"updated_at: {(self.updated_at or self.created_at).isoformat()}"]
        if self.covers:
            lines.append(f"covers: {', '.join(self.covers)}")
        if self.summarised_by:
            lines.append(f"summarised_by: {self.summarised_by}")
        if not self.to_be_indexed:
            # Written only when false. The default is that a record is
            # findable, and a file that says nothing should mean that - a user
            # who writes their own note into a folder has not opted out of
            # being able to find it.
            lines.append("to_be_indexed: false")
        if self.observed_from or self.observed_to:
            lines.append(
                "observed: "
                + (self.observed_from.isoformat() if self.observed_from else "?")
                + " .. "
                + (self.observed_to.isoformat() if self.observed_to else "?"))
        for key, value in sorted(self.metadata.items()):
            if key not in {"id", "title", "summary", "keywords", "source",
                           "references", "created_at", "updated_at", "observed",
                           "level", "covers", "summarised_by",
                           "to_be_indexed", "keyword_languages"}:
                lines.append(f"{key}: {value}")
        lines.append(_FRONT)
        lines.append("")
        lines.append(self.body.strip())
        if self.child_index:
            # In the body rather than the front matter: it is far too big for
            # a `key: value` line, and it belongs where someone reading the
            # file can see what this summary is an index *of*.
            lines.append("")
            lines.append(_INDEX_HEADING)
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(self.child_index, ensure_ascii=False,
                                    sort_keys=True))
            lines.append("```")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def from_text(text: str, folder: str, path: str = "") -> "Record":
        """Read a record back, giving up on nothing that can be guessed.

        A file with no front matter at all is still a record: its first line
        becomes the title and the rest the body. That is what a user gets if
        they drop their own notes into one of these folders, and refusing to
        read it would be the wrong answer to a reasonable thing to do.
        """
        head: Dict[str, str] = {}
        body = text
        stripped = text.lstrip()
        if stripped.startswith(_FRONT):
            rest = stripped[len(_FRONT):].lstrip("\r\n")
            end = rest.find("\n" + _FRONT)
            if end != -1:
                for line in rest[:end].splitlines():
                    match = _LINE.match(line.strip())
                    if match:
                        head[match.group(1).lower()] = match.group(2).strip()
                body = rest[end + len(_FRONT) + 1:].lstrip("\r\n")

        body, child_index = _split_child_index(body)

        known = {"id", "title", "summary", "keywords", "source", "references",
                 "created_at", "updated_at", "observed", "level", "covers",
                 "summarised_by", "to_be_indexed", "keyword_languages"}
        observed_from = observed_to = None
        if "observed" in head:
            halves = [h.strip() for h in head["observed"].split("..")]
            observed_from = _parse_time(halves[0]) if halves else None
            observed_to = _parse_time(halves[1]) if len(halves) > 1 else None

        title = head.get("title", "")
        if not title:
            # No front matter, or a user's own file: the first non-empty line
            # is what they would call it.
            for line in body.splitlines():
                if line.strip():
                    title = line.strip().lstrip("# ").strip()
                    break
        return Record(
            folder=folder,
            title=title or "(untitled)",
            id=head.get("id") or new_id("rec"),
            summary=head.get("summary", ""),
            body=body.strip(),
            keywords=[k.strip() for k in head.get("keywords", "").split(",") if k.strip()],
            source=head.get("source", "user"),
            references=[r.strip() for r in head.get("references", "").split(";")
                        if r.strip()],
            created_at=_parse_time(head.get("created_at", "")) or utcnow(),
            updated_at=_parse_time(head.get("updated_at", "")),
            level=_parse_int(head.get("level")),
            covers=[c.strip() for c in head.get("covers", "").split(",") if c.strip()],
            child_index=child_index,
            summarised_by=head.get("summarised_by", "").strip(),
            to_be_indexed=_parse_bool(head.get("to_be_indexed"), True),
            keyword_languages=[c.strip() for c
                               in head.get("keyword_languages", "").split(",")
                               if c.strip()],
            observed_from=observed_from,
            observed_to=observed_to,
            metadata={k: v for k, v in head.items() if k not in known},
            path=path,
        )


__all__ = ["Record", "FOLDERS", "INDEX", "USER_NATURE", "USER_FEEDBACK",
           "STATES", "OTHER", "AGENT_LESSON"]
