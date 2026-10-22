"""READING ONE UNIT into cases and judgments.

What a unit holds since it was last read, up to the update's cutoff, in the
order it happened:

  - its CONVERSATIONS with coding agents: the user's words whole, the
    agent's replies cut short, and which files the agent edited;
  - the RULES written for the agents (CLAUDE.md, AGENTS.md, Claude Code's
    memory of the project) - whole when new, as a change when changed;
  - its FILES: the first time, what is there and the start of the latest
    deliverables; after, each changed file as a change against the copy
    kept when it was last read. A change is the AGENT'S where the lines it
    added are lines an agent's edit put in that file, the USER'S otherwise
    - and a file no agent edited in between changed by the user alone;
  - its commits (git), as context;
  - for a Work project, its tasks: what the user did, as the state
    heartbeat summarised it.

Every word goes through `scrub` first. Then, in chunks, a model writes the
CASES (a deliverable: what it is for, who for, where it is, what it looks
like, how it was made) and the user's JUDGMENTS in them. A judgment the
model says the agent made is not kept; one quoting the user is kept only
when the quote is really there; "written" and "noted" only for what rests
on a rule file or the agent's memory.

How far each source was read is kept as it goes (`marks`), so a stopped
update carries on where it stopped.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import records
from .authorship import init_made, line_authors, memory_source
from .scrub import scrub
from .store import STRENGTHS, LearningStore, dumps, now
from .vocab import ASPECTS, ASPECT_WORDS, DIMENSIONS, OBJECT, Vocabulary

#: One call reads at most this much of a unit.
CHUNK_CHARS = 50_000
#: The first time a unit is read, the start of this many of its latest
#: deliverables is shown, this much of each.
FIRST_FILES = 15
FIRST_FILE_CHARS = 2500
#: A changed file's change shown, at most.
DIFF_CHARS = 4000
#: Files kept as copies to tell later changes by: text and documents up to
#: this size; data files never.
COPY_AT_MOST = 300_000
#: Files listed for a unit, at most.
FILES_LISTED = 120
#: The first reading of a unit goes back this far first; what is older is
#: read at the end of the update (`older`).
RECENT_DAYS = 180
DELIVERABLE_SUFFIXES = {".docx", ".doc", ".pptx", ".ppt", ".pdf", ".md", ".tex", ".txt",
                        ".html", ".rtf", ".odt", ".odp", ".key", ".pages", ".xlsx", ".ipynb"}
DATA_SUFFIXES = {".csv", ".tsv", ".jsonl", ".ndjson", ".log", ".json", ".parquet", ".npy"}

READ_PROMPT = """WRITE WHAT THIS PIECE OF THE USER'S WORK SHOWS: ITS CASES AND THE USER'S JUDGMENTS.

You are shown one unit of the user's work - a project, or a group of their
folders - and what happened in it in a stretch of time: their conversations
with coding agents (the user's words whole, the agents' replies cut short,
and which files an agent edited), the rules they wrote for the agents, the
changes to their files - each change marked as the AGENT'S (lines an agent's
edit put there) or the USER'S - their commits, and, for a project, what the
user did as summarised from their screen.

THIS RECORD IS FOR DOING THE USER'S WORK THE WAY THEY WOULD. Write:

CASES - each one deliverable the work is about: a paper, a slide deck, an
app, an email, a figure, a dataset. An existing case (listed with its id)
is continued, never written again; a new one gets a ref "new1", "new2"...
For each case:
  - title: a few words naming the thing (which paper, which talk, which app);
  - facets: the terms from the WORD LIST it is, in each dimension -
    deliverable, audience (who it is for), presentation (how it reaches
    them), purpose, environment (tools) - as term ids, as specific as the
    list allows; leave a dimension out when the record does not show it;
  - looks_like: what the deliverable looks like NOW - its structure, length,
    style, the choices visible in it (2-4 sentences, concrete);
  - how_made: how the work was done - tools, steps, order (1-2 sentences);
  - locations: where it is - [{"path": "...", "what": "final version /
    draft / figures / notes", "at": "YYYY-MM-DD"}];
  - items: its parts that judgments are about (a figure, a section, the
    budget page);
  - version: when a new version of it appeared in this stretch,
    {"at": "YYYY-MM-DD", "what": "what changed in it"}; otherwise null.

JUDGMENTS - what THE USER decided, changed, sent back, asked for, approved
or let stand, in a case. One judgment is one decision:
  - case: the case's id or ref; item: which part, if one;
  - aspect: {aspects}
  - object: the term id from the word list for WHAT in the output it is about;
  - direction: what the user wants of it - "less", "first", "grey with one
    accent colour", "checked on the live page" - short and concrete;
  - said: THE USER'S OWN WORDS that show it, copied exactly from the record
    shown - or "" when the user did not say it in words;
  - why: their reason, in their words where they gave it, else what the
    record shows of it;
  - by: "user" when the user made the call - they asked for it, changed it
    themselves, sent the agent's version back or chose between options;
    "agent" when the agent decided and the user never weighed in. Only the
    user's are kept - say "agent" honestly;
  - strength: "written" - a rule the user wrote down for the agents: a
    line of a rules file (CLAUDE.md, AGENTS.md) marked [USER], quoted in
    "said" - a line marked [AGENT] or [?] is not the user's words, and a
    README is not a rules file: at most "accepted"; "noted" - an entry of
    an agent's memory, when the user's words it was noted from are shown;
    "changed" - the user changed or turned down what was there; "approved" -
    they said yes to it; "accepted" - they let it stand without a word;
  - at: the date it happened, YYYY-MM-DD;
  - refs: where it can be opened - file paths, "session <id>", a commit.

NEVER a judgment from a change the record marks as the agent's, unless the
user's words asked for exactly that change. A fact of this one piece of work
(this dataset's column names) is a case's detail, not a judgment.

THE WORD LIST - choose from it. Only when no term fits, add one in
"new_terms": under the most fitting existing term (its id as "parent"),
with a one-line definition; then use its id "<dimension>:<term>".

Return ONLY this JSON object:
{"cases": [{"ref": "C-... or new1", "title": "", "facets": {"deliverable": ["deliverable:..."], ...},
            "looks_like": "", "how_made": "", "locations": [], "items": [], "version": null}],
 "judgments": [{"case": "", "item": "", "aspect": "", "object": "object:...", "direction": "",
                "said": "", "why": "", "by": "user", "strength": "", "at": "", "refs": []}],
 "new_terms": [{"dim": "", "term": "", "parent": "", "definition": ""}]}"""

READ_PROMPT = READ_PROMPT.replace("{aspects}", "; ".join(
    f'"{name}" - {words}' for name, words in ASPECT_WORDS.items()))


def _iso(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


def _sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]


class Piece:
    """One thing that happened, to be shown in order."""

    def __init__(self, at: str, text: str, mark: Tuple[str, str, Any],
                 rule: Optional[Dict[str, Any]] = None) -> None:
        self.at = at
        self.text = text
        self.mark = mark          # (kind, key, value) to keep once read
        #: A rules file or an agent's memory shown in it: its path, and what
        #: of it is the user's (`_strength`).
        self.rule = rule


class UnitReader:
    def __init__(self, db: Any, ask: Callable[..., Dict[str, Any]], copies_dir: Path,
                 places: Any, never: Sequence[Any] = ()) -> None:
        self.db = db
        self.store = LearningStore(db)
        self.vocab = Vocabulary(db)
        self.ask = ask
        self.copies_dir = Path(copies_dir)
        self.places = places
        self.never = list(never)
        self.removed = 0          # secrets taken out, for the log
        #: Looking only: nothing kept (the copies) - an update's plan.
        self.dry = False

    # -- gathering ------------------------------------------------------------
    def _clean(self, text: str) -> str:
        out, count = scrub(text)
        self.removed += count
        return out

    def _sessions(self, unit: Dict[str, Any], all_sessions: Sequence[records.Session]
                  ) -> List[records.Session]:
        if unit.get("only_files"):
            return []
        found: Dict[str, records.Session] = {}
        for folder in unit.get("folders") or []:
            for one in self.places.sessions_in(folder, all_sessions):
                if any(records.is_within(one.cwd or "", out)
                       for out in unit.get("leave_out") or []):
                    continue
                found[one.path] = one
        return sorted(found.values(), key=lambda one: one.started)

    def _session_pieces(self, sessions: Sequence[records.Session], marks: Dict[str, Any],
                        since: str, cutoff: str) -> List[Piece]:
        out = []
        read = marks.setdefault("sessions", {})
        for session in sessions:
            done = max(str(read.get(session.path) or ""), since)
            turns = [turn for turn in session.turns if done < turn.at <= cutoff]
            edits = [edit for edit in session.edits if done < edit.at <= cutoff]
            if not turns and not edits:
                continue
            lines = [f"CONVERSATION with {session.tool} (session {session.id[:8]}, "
                     f"started in {session.cwd or 'an unknown folder'}):"]
            events = [(turn.at, f"  [{turn.at[:16]}] "
                       + ("USER: " if turn.role == "user" else "AGENT: ") + turn.text)
                      for turn in turns]
            events += [(edit.at, f"  [{edit.at[:16]}] AGENT EDITED {edit.file}")
                       for edit in edits]
            events.sort(key=lambda one: one[0])
            lines += [text for _at, text in events]
            last = max(one[0] for one in events)
            out.append(Piece(turns[0].at if turns else edits[0].at,
                             self._clean("\n".join(lines)), ("sessions", session.path, last)))
        return out

    def _rule_pieces(self, unit: Dict[str, Any], sessions: Sequence[records.Session],
                     marks: Dict[str, Any],
                     everyone: Sequence[records.Session] = ()) -> List[Piece]:
        """The rules written for the agents, each line marked by who wrote
        it (authorship.py) - and an agent's memory, with the user's words it
        was noted from, when they are found. `everyone`: every conversation,
        for whose edits made a file (the global rules are edited from
        anywhere)."""
        everyone = list(everyone) or list(sessions)
        files: List[Path] = []
        for folder in unit.get("folders") or []:
            files += records.rule_files_in(folder)
        for session in sessions:
            if session.tool == "claude":
                files += records.memory_files(session.path)
        if unit.get("kind") == "rules":
            files += records.global_rule_files()
        seen = marks.setdefault("rules", {})
        out = []
        for path in dict.fromkeys(files):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")[:12000]
                stamp = _iso(path.stat().st_mtime)
            except OSError:
                continue
            text = self._clean(text)
            digest = _sha(text)
            if seen.get(str(path)) == digest:
                continue
            old = self._copy(path)
            changed = f"CHANGED SINCE IT WAS LAST READ:\n{self._diff(old, text)}\n" if old else ""
            if path.parent.name == "memory":
                source = self._clean(memory_source(path, everyone))[:1500]
                said = (f'NOTED FROM THE USER\'S WORDS: "{source}"' if source else
                        "WHERE IT WAS NOTED FROM IS NOT FOUND: not known to be the user's words")
                body = (f"THE AGENT'S MEMORY OF THIS PROJECT: {path}\n{said}\n{changed}"
                        f"THE MEMORY:\n{text}")
                rule = {"path": str(path), "kind": "memory", "source": source}
            else:
                lines = text.splitlines()
                authors = line_authors(path, lines, everyone)
                mark = {"user": "[USER]", "agent": "[AGENT]", "unknown": "[?]"}
                made = " (made by an agent's /init)" if init_made(path, everyone) else ""
                body = (f"RULES WRITTEN FOR THE AGENTS{made}: {path}\n{changed}"
                        "THE FILE NOW, EACH LINE MARKED BY WHO WROTE IT:\n"
                        + "\n".join(f"{mark[who]} {line}" for who, line in zip(authors, lines)))
                rule = {"path": str(path), "kind": "rule",
                        "user_text": "\n".join(line for who, line in zip(authors, lines)
                                               if who == "user")}
            self._keep_copy(path, text)
            out.append(Piece(stamp, body, ("rules", str(path), digest), rule))
        return out

    def _copy_path(self, path: Path) -> Path:
        return self.copies_dir / (hashlib.sha1(str(path).lower().encode("utf-8")).hexdigest()
                                  + ".txt")

    def _copy(self, path: Path) -> str:
        try:
            return self._copy_path(path).read_text(encoding="utf-8")
        except OSError:
            return ""

    def _keep_copy(self, path: Path, text: str) -> None:
        if self.dry:
            return
        try:
            self.copies_dir.mkdir(parents=True, exist_ok=True)
            self._copy_path(path).write_text(text, encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _diff(old: str, new: str, agent_lines: Sequence[str] = ()) -> str:
        agent = {line.strip() for text in agent_lines for line in text.splitlines()
                 if len(line.strip()) >= 8}
        out = []
        for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=1):
            if line.startswith(("---", "+++")):
                continue
            if line.startswith("+") and agent:
                who = "AGENT" if line[1:].strip() in agent else "USER"
                out.append(f"+[{who}] {line[1:]}")
            else:
                out.append(line)
        text = "\n".join(out)
        return text[:DIFF_CHARS] + ("\n... (more)" if len(text) > DIFF_CHARS else "")

    def _walk_files(self, unit: Dict[str, Any]) -> List[Tuple[Path, os.stat_result]]:
        from ..index.rules import secret_name, skipped_folder, cloud_only
        out = []
        leave_out = [str(one) for one in unit.get("leave_out") or []]
        for folder in unit.get("folders") or []:
            root = Path(folder)
            if not root.is_dir():
                continue
            if unit.get("only_files"):
                walker = [(str(root), [], [one.name for one in root.iterdir() if one.is_file()])]
            else:
                walker = os.walk(root)
            for current, dirs, files in walker:
                here = Path(current)
                dirs[:] = [d for d in dirs if not skipped_folder(here / d, [Path(n) for n in self.never])
                           and not any(records.is_within(str(here / d), one) for one in leave_out)]
                for name in files:
                    path = here / name
                    try:
                        info = path.stat()
                    except OSError:
                        continue
                    if secret_name(name) or cloud_only(info, name):
                        continue
                    out.append((path, info))
                if len(out) > 20000:
                    break
        return out

    def _file_pieces(self, unit: Dict[str, Any], sessions: Sequence[records.Session],
                     marks: Dict[str, Any], first: bool) -> List[Piece]:
        from ..heartbeat.experience.materials import extract_text
        files = self._walk_files(unit)
        seen = marks.setdefault("files", {})
        agent_edits: Dict[str, List[str]] = {}
        for session in sessions:
            for edit in session.edits:
                agent_edits.setdefault(os.path.normcase(edit.file), []).append(edit.added)
        out: List[Piece] = []
        if not files:
            return out
        if first:
            listing = sorted(files, key=lambda one: -one[1].st_mtime)
            lines = ["THE FILES OF THIS UNIT (newest first):"]
            for path, info in listing[:FILES_LISTED]:
                lines.append(f"  - {path}  {info.st_size} bytes  {_iso(info.st_mtime)[:10]}")
            if len(listing) > FILES_LISTED:
                lines.append(f"  ... and {len(listing) - FILES_LISTED} more")
            shown = 0
            for path, info in listing:
                if shown >= FIRST_FILES:
                    break
                if path.suffix.lower() not in DELIVERABLE_SUFFIXES:
                    continue
                text = extract_text(path)
                if text.strip():
                    shown += 1
                    lines.append(f"\nTHE START OF {path} ({_iso(info.st_mtime)[:10]}):\n"
                                 + text[:FIRST_FILE_CHARS])
            stamp = _iso(listing[0][1].st_mtime) if listing else now()
            out.append(Piece(stamp, self._clean("\n".join(lines)), ("listing", "", "")))
        for path, info in files:
            key = str(path)
            state = [int(info.st_mtime), int(info.st_size)]
            if seen.get(key) == state:
                continue
            suffix = path.suffix.lower()
            keepable = info.st_size <= COPY_AT_MOST and suffix not in DATA_SUFFIXES
            if not keepable:
                seen[key] = state
                continue
            text = extract_text(path)
            if not text:
                seen[key] = state
                continue
            text = self._clean(text)
            old = self._copy(path)
            if first or (not old and key not in seen):
                # The first sight of it: kept to tell later changes by.
                self._keep_copy(path, text)
                if first:
                    seen[key] = state
                    continue
                body = f"A NEW FILE: {path} ({_iso(info.st_mtime)[:10]})\n{text[:FIRST_FILE_CHARS]}"
                out.append(Piece(_iso(info.st_mtime), body, ("files", key, state)))
                continue
            if old == text:
                seen[key] = state
                continue
            edits = agent_edits.get(os.path.normcase(key), [])
            who = "" if edits else " (no agent edited it: the change is the user's)"
            change = self._diff(old, text, edits)
            self._keep_copy(path, text)
            out.append(Piece(_iso(info.st_mtime),
                             f"CHANGED: {path} ({_iso(info.st_mtime)[:10]}){who}\n{change}",
                             ("files", key, state)))
        return out

    def _git_pieces(self, unit: Dict[str, Any], marks: Dict[str, Any], since: str,
                    cutoff: str) -> List[Piece]:
        out = []
        seen = marks.setdefault("git", {})
        for folder in unit.get("folders") or []:
            if not (Path(folder) / ".git").exists():
                continue
            after = max(str(seen.get(folder) or ""), since)
            args = ["git", "-C", folder, "log", "--date=iso-strict",
                    "--pretty=format:%ad|%an|%s", f"--until={cutoff}", "-n", "200"]
            if after:
                args.append(f"--since={after}")
            try:
                done = subprocess.run(args, capture_output=True, text=True, timeout=30,
                                      encoding="utf-8", errors="replace",
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.SubprocessError):
                continue
            lines = [line for line in (done.stdout or "").splitlines() if line.strip()]
            if not lines:
                continue
            latest = lines[0].split("|", 1)[0]
            out.append(Piece(lines[-1].split("|", 1)[0],
                             self._clean(f"COMMITS in {folder}:\n" + "\n".join(
                                 "  " + line for line in reversed(lines))),
                             ("git", folder, latest)))
        return out

    def _task_pieces(self, unit: Dict[str, Any], marks: Dict[str, Any], cutoff: str
                     ) -> List[Piece]:
        if unit.get("kind") != "work":
            return []
        try:
            from ..heartbeat.states.task_projects import RunTasks
        except Exception:                      # pragma: no cover - import
            return []
        until = str(marks.get("tasks") or "")
        tasks = [one for one in RunTasks(self.db, "live").of_project(unit["key"], limit=500)
                 if until < str(one.get("ends_at") or "") <= cutoff]
        if not tasks:
            return []
        tasks.sort(key=lambda one: str(one.get("ends_at") or ""))
        lines = ["WHAT THE USER DID IN THIS PROJECT (from their screen, summarised):"]
        for one in tasks:
            lines.append(f"  [{str(one.get('starts_at') or '')[:16]}] {one.get('title', '')}: "
                         f"{one.get('summary', '')} ({round(float(one.get('minutes') or 0))} min)")
        return [Piece(str(tasks[0].get("starts_at") or ""), self._clean("\n".join(lines)),
                      ("tasks", "", str(tasks[-1].get("ends_at") or "")))]

    # -- reading --------------------------------------------------------------
    def pending(self, unit: Dict[str, Any], cutoff: str, all_sessions: Sequence[records.Session],
                older: bool = False) -> Tuple[List[Piece], Dict[str, Any]]:
        """What there is to read in a unit, in order, and the marks it would
        leave. `older`: the part before the recent window of a first read."""
        marks = json.loads(json.dumps(unit.get("marks") or {}))
        first = not unit.get("first_read")
        since = ""
        if first and not older:
            since = (datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
                     - timedelta(days=RECENT_DAYS)).isoformat()
        sessions = self._sessions(unit, all_sessions)
        pieces: List[Piece] = []
        if older:
            limit = str(marks.get("recent_from") or "")
            pieces += self._session_pieces(sessions, {"sessions": {}}, "", limit)
            pieces += self._git_pieces(unit, {"git": {}}, "", limit)
            # What is read of the older part moves no mark: the marks are
            # already past it.
            pieces = [Piece(one.at, one.text, ("older", "", "")) for one in pieces]
            return sorted(pieces, key=lambda one: one.at), marks
        pieces += self._session_pieces(sessions, marks, since, cutoff)
        pieces += self._rule_pieces(unit, sessions, marks, all_sessions)
        pieces += self._file_pieces(unit, sessions, marks, first)
        pieces += self._git_pieces(unit, marks, since, cutoff)
        pieces += self._task_pieces(unit, marks, cutoff)
        if first:
            marks["recent_from"] = since
        pieces.sort(key=lambda one: one.at)
        return pieces, marks

    @staticmethod
    def size(pieces: Sequence[Piece]) -> int:
        return sum(len(one.text) for one in pieces)

    def read(self, unit: Dict[str, Any], pieces: Sequence[Piece], marks: Dict[str, Any],
             progress: Optional[Callable[[int], None]] = None,
             stopped: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
        """Read the pieces in chunks; keep cases and judgments; leave the
        marks as each chunk is done. Stops early when `stopped()`."""
        report = {"chunks": 0, "cases": 0, "judgments": 0, "left_agent": 0, "failed": ""}
        chunk: List[Piece] = []
        size = 0

        def flush() -> bool:
            nonlocal chunk, size
            if not chunk:
                return True
            ok = self._read_chunk(unit, chunk, report)
            if not ok:
                return False
            for piece in chunk:
                kind, key, value = piece.mark
                if kind in ("sessions", "files", "rules", "git"):
                    marks.setdefault(kind, {})[key] = value
                elif kind == "tasks":
                    marks["tasks"] = value
            unit["marks"] = marks
            self.store.save_unit(unit)
            if progress is not None:
                progress(size)
            chunk, size = [], 0
            return True

        for piece in pieces:
            if stopped is not None and stopped():
                return report
            text = piece.text[:CHUNK_CHARS]
            if size + len(text) > CHUNK_CHARS and chunk:
                if not flush():
                    return report
            chunk.append(Piece(piece.at, text, piece.mark, piece.rule))
            size += len(text)
        if not flush():
            return report
        unit["marks"] = marks
        self.store.save_unit(unit)
        return report

    def _read_chunk(self, unit: Dict[str, Any], chunk: Sequence[Piece],
                    report: Dict[str, Any]) -> bool:
        cases = self.store.cases(unit["id"])
        known = ["THE CASES OF THIS UNIT SO FAR:"] + [
            f"  {one['id']}: {one['title']} - {dumps(one['facets'])} - "
            f"{one['looks_like'][:200]} items: {', '.join(one['items'][:12])}"
            for one in cases[-40:]] if cases else ["THE CASES OF THIS UNIT SO FAR: none"]
        text = "\n".join([
            f"THE UNIT: {unit.get('title', '')} - {unit.get('summary', '')}",
            "Its folders: " + (", ".join(unit.get("folders") or []) or "-"),
            "", *known, "", "THE WORD LIST:",
            self.vocab.listing(list(DIMENSIONS) + [OBJECT]), "",
            "WHAT HAPPENED, IN ORDER:", *[one.text for one in chunk]])
        said = self.ask("read", READ_PROMPT, text, 8000, "the cases and judgments")
        report["chunks"] += 1
        if not said or said.get("_failed"):
            report["failed"] = said.get("_failed", "no answer") if said else "no answer"
            return False
        self._keep(unit, said, "\n".join(one.text for one in chunk), report,
                   [one.rule for one in chunk if one.rule])
        return True

    # -- keeping --------------------------------------------------------------
    def _tag(self, tag: Any, dim: str, added: Dict[str, str]) -> str:
        tag = str(tag or "").strip()
        if not tag:
            return ""
        if ":" not in tag:
            tag = f"{dim}:{tag}"
        tag = added.get(tag, tag)
        found = self.vocab.resolve(tag)
        return found if found and found.startswith(dim + ":") else ""

    @staticmethod
    def _names(ref: str, path: str) -> bool:
        ref = ref.replace("\\", "/").strip().lower().lstrip("./")
        path = path.replace("\\", "/").lower()
        return bool(ref) and (path == ref or path.endswith("/" + ref) or ref.endswith(path))

    def _strength(self, strength: str, quote: str, refs: Sequence[str],
                  rules: Sequence[Dict[str, Any]]) -> str:
        """The two strongest kinds of evidence, as code can stand behind
        them: "written" - a line of a rules file shown here, quoted, that is
        the USER'S (authorship.py) - else "accepted": a line an agent wrote,
        or one no one is known to have, or a file that is no rules file (a
        README), is at most what the user let stand; "noted" - an entry of
        an agent's memory shown here whose source, the user's words, was
        found - else "approved"."""
        if strength == "written":
            files = [one for one in rules if one["kind"] == "rule"
                     and any(self._names(ref, one["path"]) for ref in refs)]
            mine = [one for one in files if quote and
                    " ".join(quote.lower().split()) in " ".join(one["user_text"].lower().split())]
            return "written" if mine else "accepted"
        if strength == "noted":
            files = [one for one in rules if one["kind"] == "memory" and one["source"]
                     and any(self._names(ref, one["path"]) for ref in refs)]
            return "noted" if files else "approved"
        return strength

    def _keep(self, unit: Dict[str, Any], said: Dict[str, Any], shown: str,
              report: Dict[str, Any], rules: Sequence[Dict[str, Any]] = ()) -> None:
        added: Dict[str, str] = {}
        for one in said.get("new_terms") or []:
            if not isinstance(one, dict):
                continue
            dim = str(one.get("dim") or "")
            tag = self.vocab.add(dim, str(one.get("term") or ""),
                                 str(one.get("parent") or ""), str(one.get("definition") or ""))
            if tag:
                added[f"{dim}:{str(one.get('term') or '').strip().lower()}"] = tag
        refs: Dict[str, str] = {}
        for one in said.get("cases") or []:
            if not isinstance(one, dict):
                continue
            ref = str(one.get("ref") or "")
            existing = self.store.case(ref) if ref.startswith("C-") else None
            case = existing or {"unit_id": unit["id"], "began": "", "ended": ""}
            if one.get("title"):
                case["title"] = str(one["title"])[:200]
            facets = dict(case.get("facets") or {})
            for dim in DIMENSIONS:
                values = (one.get("facets") or {}).get(dim) if isinstance(one.get("facets"), dict) else None
                tags = [self._tag(value, dim, added) for value in values or []]
                tags = [tag for tag in dict.fromkeys(tags) if tag]
                if tags:
                    facets[dim] = tags
            case["facets"] = facets
            for name in ("looks_like", "how_made"):
                if one.get(name):
                    case[name] = self._clean(str(one[name]))[:1500]
            locations = list(case.get("locations") or [])
            for place in one.get("locations") or []:
                if isinstance(place, dict) and place.get("path") and \
                        all(old.get("path") != place["path"] for old in locations):
                    locations.append({"path": str(place["path"]), "what": str(place.get("what") or ""),
                                      "at": str(place.get("at") or "")})
            case["locations"] = locations[-30:]
            items = list(case.get("items") or [])
            for item in one.get("items") or []:
                if str(item) and str(item) not in items:
                    items.append(str(item)[:120])
            case["items"] = items[-60:]
            version = one.get("version")
            if isinstance(version, dict) and version.get("what"):
                case["versions"] = (list(case.get("versions") or []) + [
                    {"at": str(version.get("at") or ""), "what": self._clean(str(version["what"]))[:400]}])[-30:]
            case_id = self.store.save_case(case)
            refs[ref or case_id] = case_id
            refs[case_id] = case_id
            if not existing:
                report["cases"] += 1
            self.vocab.used([tag for values in facets.values() for tag in values], case_id)
        lowered = shown.lower()
        for one in said.get("judgments") or []:
            if not isinstance(one, dict):
                continue
            if str(one.get("by") or "").lower() != "user":
                report["left_agent"] += 1
                continue
            case_id = refs.get(str(one.get("case") or ""))
            if not case_id:
                case_id = str(one.get("case") or "")
                if not (case_id.startswith("C-") and self.store.case(case_id)):
                    continue
            aspect = str(one.get("aspect") or "")
            if aspect not in ASPECTS:
                continue
            obj = self._tag(one.get("object"), OBJECT, added)
            if not obj:
                continue
            quote = self._clean(str(one.get("said") or "")).strip()
            # The user's words are the user's only where they are shown.
            if quote and quote.lower()[:200] not in lowered:
                quote = ""
            strength = str(one.get("strength") or "accepted")
            if strength not in STRENGTHS:
                strength = "accepted"
            refs_given = [str(ref)[:300] for ref in one.get("refs") or []][:6]
            strength = self._strength(strength, quote, refs_given, rules)
            self.store.save_judgment({
                "case_id": case_id, "item": str(one.get("item") or "")[:120],
                "aspect": aspect, "object": obj,
                "direction": self._clean(str(one.get("direction") or ""))[:300],
                "said": quote[:1000], "why": self._clean(str(one.get("why") or ""))[:600],
                "strength": strength, "at": str(one.get("at") or "")[:10],
                "refs": refs_given, "state": "new"})
            report["judgments"] += 1
            self.vocab.used([obj], case_id)
