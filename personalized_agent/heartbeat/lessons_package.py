"""ALL THE USER'S LESSONS, PACKED TO BE CARRIED - and loaded on another
computer.

What the agent has learned about how the user works lives in several places
on this machine: the experience (stages, the cards of each piece of work,
the edges between them, the patterns and principles), the trees the agents
kept of each of the user's work projects (their "work:" scopes), the
notification lessons (the actions on the calendar and the running text drawn
from them), and the nature records in the library's user_nature folder with
everything said about them (their kinds, the stages and states they were
written from, their examples and history), and the lesson cards - each
answered question, every version of it. PACKAGE ALL LESSONS puts every one
of them in one folder, and a zip of it, that the user can carry or upload;
LOAD LESSONS on the other computer merges it into what is there.

NOT CARRIED: the Experience debugger's ways (units "dbg_...", in their own
"work:dbg_...:<way>" scopes) and the Build now test's scope, which are not
the user's lessons; the states and raw activity the lessons were drawn from,
which are this machine's history, not lessons; what the stage writer was
given (experience_prompts), which is for reading a run, and the terms stages
are found by, which are worked out again from the stages where they land.
NEVER CARRIED: the application's protected state - settings, credentials,
keys, the API key, the database itself - and a file that looks like a
secret wherever it is, or a reference to a window or site the listener is
told never to read (the password managers, banking, private windows of
"Windows never to read", the application and website denylists).

THE FOLDER, <workspace>/lesson_package/, is updated in place:

    package.json                  which package this is - its id, made once
    latest.json                   the last packaging: its manifest, its stats
    history.json                  each packaging's stats, oldest first
    manifests/manifest_<stamp>.json   each packaging, whole
    records/<kind>/<ab>/<sha>.json    each row or record, by its content
    objects/<ab>/<sha><ext>       each referenced file, by its content
    objects/<ab>/<sha><ext>.xz    (or .gz) one too large, compressed
    summaries/<sha16>_<ctx12>.md  one too large even compressed, summarised
                                  for the reason it was cited

ONLY WHAT IS NEW IS DONE AGAIN. Everything in the folder is named by its
content, so what is there already is never written twice and never changed:
a row unchanged since the last packaging is the same record file, a file
unchanged (same size and modification time, then the same hash) is the same
object, and a summary of the same file for the same context is the same
summary - no model call. A packaging writes a new manifest beside the old
ones and moves latest.json to it only when it has finished, so a packaging
cancelled or failed half way leaves the last one as it was.

REFERENCES. A lesson points at what it was drawn from: a stage's references
(the file, the page, the email, the window, the agent's own copies of
them), a card's evidence, a nature example's places. Each place is a
REFERENCE, numbered once (ref_000001, ...) and kept by that number in every
later packaging. Every file it names - the original where it is on this
computer, and each copy the agent kept of it - is carried:

  - as it is, when it is 20 MB or less;
  - compressed (the best of deflate - what zip and gzip use - and LZMA),
    when that brings it to 20 MB or less;
  - otherwise as a SUMMARY written by a model told WHY IT WAS CITED: the
    lesson that cites it (a stage's summary, a card's statement, a nature's
    title and the example's own words) and what the reference was for (its
    "what"), with the file's name, type, size and a bounded extract of what
    can be read of it - its text, for text, PDF, Word, PowerPoint and code;
    the names inside an archive; only its metadata for a picture or other
    binary. A file cited for several reasons is summarised for each.
  - a web page is carried as its address, and the text the agent kept of it
    as any other copy - nothing is fetched while packaging: a page behind a
    sign-in fetched from here would be its sign-in page.
  - a file no longer on this computer is recorded as missing, at its path.

LOADING merges and never deletes. A row missing here is added; for one this
computer has too, THE NEWER IS KEPT, by the time each kind keeps (see
`MERGE_RULES`); the same package loaded again changes nothing. The files
come out into <workspace>/data/imported_references/<package id>/<ref id>/,
where agents read references as they read the others, and each reference
loaded points at its copy there, with where it was originally at; one
summarised is given its summary as what it says.
"""

from __future__ import annotations

import fnmatch
import gzip
import hashlib
import json
import logging
import lzma
import mimetypes
import os
import platform
import re
import shutil
import threading
import time
import uuid
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from ..common.agent_writes import mark_written
from ..common.events.models import new_id
from .experience.references import is_local
from .experience.store import LIVE, count_use

log = logging.getLogger(__name__)

#: What a package says it is, and the version of how it is laid out.
FORMAT = "personalized-agent-lessons"
FORMAT_VERSION = 1

#: The package's folder in the workspace, and where the files of a package
#: loaded here go (in the library's folder, beside the agent's own copies of
#: references, which the stage pass's garbage collection does not reach).
PACKAGE_FOLDER = "lesson_package"
IMPORTED_FOLDER = "imported_references"

#: A file larger than this is compressed - and summarised if that does not
#: bring it under.
COPY_AT_MOST = 20 * 1024 * 1024

#: The zips kept in the workspace: the newest few, never an endless row.
ZIPS_KEPT = 3

#: Packagings kept in history.json.
HISTORY_KEPT = 100

#: How much of a large file is compressed first to see whether compressing
#: it is worth it - a video, an archive, random data do not compress, and
#: compressing a gigabyte of it twice to find that out would be the whole
#: packaging. Compressed to more than this share of itself, it is not tried.
SAMPLE_BYTES = 1024 * 1024
SAMPLE_RATIO = 0.92

#: LZMA is slow; past this a file is only deflated.
LZMA_AT_MOST = 256 * 1024 * 1024

#: The extract of a file a summary is written from: its beginning and its end.
EXTRACT_HEAD = 12_000
EXTRACT_TAIL = 2_000

#: A file cited for more reasons than this is summarised for the first ones
#: one by one, and for the rest together, each named.
SUMMARY_CONTEXTS = 8

#: How long a summary may be, in words.
SUMMARY_WORDS = 350

#: What the model calls of a packaging are counted under.
MODEL_TASK = "lessons_package"

#: The Experience debugger's projects begin so; nothing of theirs is carried.
DEBUG_PREFIX = "dbg_"

#: The tables carried row by row: (kind, table, key column, version column).
ROW_TABLES = (
    ("workspace", "experience_workspaces", "unit_id", "updated_at"),
    ("card", "experience_cards", "id", "updated_at"),
    ("stage", "experience_stages", "id", "updated_at"),
    ("thread", "experience_threads", "id", "updated_at"),
    ("pattern", "experience_patterns", "id", "updated_at"),
    ("calendar_action", "calendar_actions", "id", "reasoned_at"),
    # The user's connections, with their own changes to them - the
    # connection lessons (heartbeat/connections/store.py) - the user's
    # decision: packed with the rest of the lessons.
    ("connection_person", "connection_people", "id", "updated_at"),
    ("connection_account", "connection_accounts", "id", "updated_at"),
    ("connection_activity", "connection_activity", "id", "updated_at"),
    ("connection_action", "connection_actions", "id", "updated_at"),
    ("connection_source", "connection_sources", "id", "updated_at"),
    ("connection_lessons", "connection_lessons", "id", "updated_at"),
    # THE LESSON CARDS - each answered question, every version of it (the
    # user's decision: the package carries them with the rest). Not where
    # each was used: those are this computer's runs.
    ("lesson_card", "lesson_cards", "id", "updated_at"),
)

#: The small tables carried whole, inside the manifest.
LINK_TABLES = ("experience_edges", "stage_natures", "nature_links",
               "nature_kinds", "experience_learning", "calendar_nature")

#: How each kind is merged into what this computer has - said in the load
#: report and kept in each manifest, so it can be read where it is applied.
MERGE_RULES = {
    "workspace": "by unit id; the newer updated_at is kept",
    "card": "by id; the newer updated_at is kept",
    "stage": "by id; the newer updated_at is kept (its terms worked out again here)",
    "thread": "by id; the newer updated_at is kept",
    "pattern": "by id; the newer updated_at is kept (the next build regroups "
               "every nature, these with the rest)",
    "nature": "by record id; the newer updated_at of the record is kept",
    "calendar_action": "by id; added when missing; a reading (why, lesson) "
                       "fills one not yet read here; otherwise this computer's",
    "connection_person": "by id; the newer updated_at is kept (a deletion too)",
    "connection_account": "by id; the newer updated_at is kept",
    "connection_activity": "by id; the newer updated_at is kept",
    "connection_action": "by id; added when missing",
    "connection_source": "by id; the newer updated_at is kept",
    "connection_lessons": "the summary of the older changes: the newer is kept",
    "lesson_card": "by id; the newer updated_at is kept - never one resting on web "
                   "content, which no computer keeps",
    "experience_edges": "added when neither its id nor the same edge (scope, "
                        "source, target, kind) is here",
    "stage_natures": "added when missing",
    "nature_links": "added when missing",
    "nature_kinds": "added when missing; a kind said here is kept (no time is "
                    "kept to compare)",
    "experience_learning": "by unit; the later learned_at is kept",
    "calendar_nature": "each lesson new here is added to the list, unless a "
                       "lesson here already says it",
}

#: Files that are text, and documents whose text is extracted.
DOCUMENTS = (".pdf", ".docx", ".pptx")
TEXTS = (".txt", ".md", ".csv", ".tsv", ".json", ".tex", ".bib", ".py", ".r",
         ".js", ".ts", ".html", ".htm", ".css", ".yaml", ".yml", ".toml",
         ".ipynb", ".sql", ".sh", ".log", ".xml", ".rst", ".java", ".c",
         ".cpp", ".h", ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".m",
         ".jl", ".ini", ".cfg", ".bat", ".ps1", ".jsonl")

#: Already compressed: stored in the zip as they are.
STORED_IN_ZIP = (".xz", ".gz", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".webp",
                 ".mp4", ".mov", ".mp3", ".docx", ".pptx", ".xlsx", ".7z", ".bz2")

#: Names that are a secret wherever they are: keys, certificates, password
#: vaults, tokens, the files tools keep credentials in. A reference to one
#: is carried as its path alone.
SECRET_NAMES = (
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", "*.pem", "*.key",
    "*.pfx", "*.p12", "*.keystore", "*.jks", "*.kdbx", "*.kdb", "*.1pif",
    "*.agilekeychain", "*.keychain", "*.keychain-db", ".env", ".env.*",
    "*.env", ".netrc", "_netrc", ".pgpass", ".git-credentials", ".npmrc",
    ".pypirc", "credentials*", "*credential*", "*secret*", "*password*",
    "*passwd*", "*.token", "*token*.json", "*token*.txt", "api_key*",
    "apikey*", "*api-key*", "*.ovpn", "wallet.dat", "cookies*",
    "login data*", "web data*")

_WINDOWS = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_SAFE_REL = re.compile(r"^(?:objects|summaries|records|manifests)/[A-Za-z0-9_.\-/]+$")
_PACKAGE_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_REF_ID = re.compile(r"^ref_\d{6,}$")
_ZIP_NAME = re.compile(r"^lessons_\d{8}-\d{6}-\d{3}\.zip$")

SUMMARY_PROMPT = """YOU ARE PACKING UP WHAT AN AGENT HAS LEARNED ABOUT HOW ONE PERSON WORKS,
to carry it to another computer. One file the agent's lessons point to is
too large to carry, even compressed, so it travels as YOUR SUMMARY instead:
on the other computer, the lesson that cites it reads your summary in the
file's place.

The file is cited for a particular reason, given below as THE CONTEXT:
which lesson cites it - a stage of the person's work, a card of a
deliverable, or an example of the person's nature - what that lesson says,
and what the reference was for (its "what"). SUMMARISE THE FILE AS IT
MATTERS FOR THAT REFERENCE: what in it bears on what the lesson used it
for; what it shows about the work, its state at the time, or the person's
standards - concretely, with the names, numbers, sections, headings and
decisions that pin it down - so that someone reading only the lesson and
your summary knows what was being pointed at and what it said. When
several contexts are listed, say for each what in the file matters for it.

You are given what could be read of the file: an extract of its text (its
beginning and its end), the names inside it, or only its metadata. Say
plainly what you could not see - never describe what is not shown.

At most {words} words. Plain text, no preamble."""


class Cancelled(Exception):
    """The user cancelled the packaging, or the loading."""


# -- small helpers ------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stamp() -> str:
    """A packaging's name: when it was made, to the millisecond, in local
    time - what the user sees in the folder."""
    moment = datetime.now()
    return moment.strftime("%Y%m%d-%H%M%S-") + f"{moment.microsecond // 1000:03d}"


def _moment(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        when = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def is_newer(theirs: Any, ours: Any) -> bool:
    """Whether a version stamped `theirs` is strictly newer than `ours` -
    one with no time is never newer than one with a time."""
    one, two = _moment(theirs), _moment(ours)
    if one is None:
        return False
    if two is None:
        return True
    return one > two


def ref_key(location: Any) -> str:
    """What a place is known by, the same on every computer: a Windows path
    with its separators and case made one, any other location as written.
    Not `os.path.normcase` - that is this machine's rule, and the package is
    read on another."""
    text = str(location or "").strip()
    if _WINDOWS.match(text):
        text = re.sub(r"[\\/]+", "/", text).lower()
        return "//" + text[2:] if text.startswith("//") else text
    return text


def _rests_on_web(card: Dict[str, Any]) -> bool:
    """A lesson card resting on a web page, even partly - never kept
    (heartbeat/experience/lesson_cards.py)."""
    if str(card.get("tier") or "") == "web":
        return True
    evidence = _loads(card.get("evidence"), [])
    return any(isinstance(one, dict) and one.get("type") == "web" for one in evidence or [])


def carried_scope(scope: Any) -> bool:
    """Whether a scope's experience is the user's lessons: the live one, and
    the tree of each of their work projects - never the Experience
    debugger's ways, or the Build now test's."""
    scope = str(scope or "")
    if scope == LIVE:
        return True
    if scope.startswith("work:"):
        unit = scope[len("work:"):]
        return bool(unit) and not unit.startswith(DEBUG_PREFIX)
    return False


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      default=str).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _loads(text: Any, default: Any) -> Any:
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


def _safe_name(name: str, default: str = "file") -> str:
    cleaned = _UNSAFE.sub("_", str(name or "")).strip(" ._")
    return (cleaned[:100] or default)


def _write_bytes(path: Path, data: bytes) -> None:
    """A file written whole or not at all: to a part file, then moved."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    mark_written(part)
    part.write_bytes(data)
    mark_written(path)
    os.replace(part, path)


def _human(size: Any) -> str:
    try:
        size = float(size or 0)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _folder_size(folder: Path) -> int:
    total = 0
    for path in folder.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


# -- what a lesson points at -----------------------------------------------------
def _files_of(reference: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Every file a reference names, with its role: the original, where it
    is a path on a computer, and each copy the agent kept of it."""
    out: List[Tuple[str, str]] = []
    location = str(reference.get("location") or "").strip()
    if is_local(location):
        out.append((location, "original"))
    copies = reference.get("copies")
    if isinstance(copies, dict):
        for name, value in sorted(copies.items()):
            if isinstance(value, str) and is_local(value):
                out.append((value, f"copy_{_safe_name(name, 'copy')}"))
    for name in ("transcript", "snapshot", "screenshot"):
        value = reference.get(name)
        if isinstance(value, str) and is_local(value):
            out.append((value, name))
    for index, value in enumerate(reference.get("screenshots") or []):
        if isinstance(value, str) and is_local(value):
            out.append((value, f"screenshot_{index + 1}"))
    seen, kept = set(), []
    for path, role in out:
        if ref_key(path) not in seen:
            seen.add(ref_key(path))
            kept.append((path, role))
    return kept


class _Collector:
    """Every reference the lessons hold, by place, with every context that
    cites it: which lesson, what it says, and what the place was for."""

    def __init__(self) -> None:
        self.refs: Dict[str, Dict[str, Any]] = {}

    def walk(self, value: Any, owner: Dict[str, Any],
             chain: Tuple[Dict[str, Any], ...] = ()) -> None:
        if isinstance(value, list):
            for one in value:
                self.walk(one, owner, chain)
        elif isinstance(value, dict):
            location = value.get("location")
            if isinstance(location, str) and location.strip():
                self.add(value, owner, chain)
            for one in value.values():
                if isinstance(one, (dict, list)):
                    self.walk(one, owner, chain + (value,))

    def add(self, reference: Dict[str, Any], owner: Dict[str, Any],
            chain: Tuple[Dict[str, Any], ...]) -> None:
        location = str(reference["location"]).strip()
        key = ref_key(location)
        held = self.refs.setdefault(key, {
            "key": key, "location": location, "title": "", "kind": "",
            "contexts": [], "files": {}})
        held["title"] = held["title"] or str(reference.get("title") or "")[:300]
        held["kind"] = held["kind"] or str(reference.get("kind") or "")[:20]
        context = dict(owner)
        context["what"] = str(reference.get("what") or "")[:400]
        # The example the place is in, for a nature: the user's own words
        # and its label are why it was cited.
        example = next((one for one in reversed(chain) if "said" in one), None)
        if example is not None:
            context["example"] = str(example.get("what") or "")[:300]
            context["said"] = str(example.get("said") or "")[:600]
        if context not in held["contexts"]:
            held["contexts"].append(context)
        for path, role in _files_of(reference):
            held["files"].setdefault(ref_key(path), (path, role))


def _owner_of(kind: str, row: Dict[str, Any]) -> Dict[str, Any]:
    """Which lesson cites a reference, as a summary of it is told."""
    if kind == "stage":
        return {"owner": "stage", "id": row.get("id"), "scope": row.get("scope"),
                "stage_kind": row.get("kind"), "medium": row.get("medium") or "",
                "summary": str(row.get("summary") or "")[:800]}
    if kind == "card":
        return {"owner": "card", "id": row.get("id"), "scope": row.get("scope"),
                "title": str(row.get("title") or "")[:300],
                "statement": str(row.get("statement") or "")[:600]}
    return {"owner": kind, "id": row.get("id"),
            "title": str(row.get("title") or "")[:300],
            "summary": str(row.get("summary") or "")[:600]}


# -- what can be read of a file, for its summary ------------------------------------
def _extract(path: Path, size: int) -> Tuple[str, str]:
    """(what was given, the text) - a bounded extract of what can be read
    of a file: its text, the names in an archive, or its metadata."""
    kind = path.suffix.lower()
    mime = mimetypes.guess_type(str(path))[0] or ""
    try:
        if kind in DOCUMENTS:
            from .experience.materials import extract_text
            text = extract_text(path)
            if text:
                return ("an extract of its text (its beginning and end)",
                        _head_and_tail(text))
        with open(path, "rb") as handle:
            head = handle.read(64 * 1024)
            tail = b""
            if size > 64 * 1024:
                handle.seek(max(64 * 1024, size - 16 * 1024))
                tail = handle.read(16 * 1024)
        if kind in TEXTS or _looks_like_text(head):
            text = head.decode("utf-8", errors="replace")
            if tail:
                text = (text[:EXTRACT_HEAD] + "\n[...]\n"
                        + tail.decode("utf-8", errors="replace")[-EXTRACT_TAIL:])
            return ("an extract of its text (its beginning and end)",
                    _head_and_tail(text))
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
            shown = "\n".join(names[:200])
            more = f"\n... and {len(names) - 200} more" if len(names) > 200 else ""
            return ("the names of the files inside it (an archive)", shown + more)
        if mime.startswith("image/"):
            return ("only its metadata: it is a picture, which could not be "
                    "shown", f"type {mime}; first bytes {head[:32].hex()}")
        return ("only its metadata: its content is binary",
                f"type {mime or 'unknown'}; first bytes {head[:64].hex()}")
    except Exception as exc:              # pragma: no cover - an unreadable file
        return ("only its metadata: it could not be read", str(exc)[:300])


def _looks_like_text(head: bytes) -> bool:
    if not head:
        return False
    if b"\x00" in head[:8192]:
        return False
    try:
        head[:8192].decode("utf-8")
        return True
    except UnicodeDecodeError as exc:
        return exc.start > 8000            # cut in the middle of a character


def _head_and_tail(text: str) -> str:
    if len(text) <= EXTRACT_HEAD + EXTRACT_TAIL:
        return text
    return text[:EXTRACT_HEAD] + "\n[...]\n" + text[-EXTRACT_TAIL:]


def _context_lines(context: Dict[str, Any]) -> List[str]:
    owner = context.get("owner")
    if owner == "stage":
        lines = [f"- cited by a stage of the person's work ({context.get('stage_kind') or '?'}"
                 + (f", {context['medium']}" if context.get("medium") else "") + ")",
                 f"- what the stage says: {context.get('summary') or '(nothing said)'}"]
    elif owner == "card":
        lines = [f"- cited by a card of a deliverable: {context.get('title') or '?'}",
                 f"- what must be true when it is done: {context.get('statement') or '?'}"]
    else:
        lines = [f"- cited by an example of the person's nature: {context.get('title') or '?'}",
                 f"- what the nature says: {context.get('summary') or '?'}"]
        if context.get("example"):
            lines.append(f"- the example: {context['example']}")
        if context.get("said"):
            lines.append(f"- the person's own words in it: {context['said']}")
    lines.append(f"- what the reference was for: {context.get('what') or '(not said)'}")
    return lines


# -- the package as a whole ------------------------------------------------------------
class LessonsPackage:
    """Package all lessons, and load a package: in the background, one at
    a time, with what it is doing said as it goes."""

    def __init__(self, db: Any, library: Any, models: Any, config: Any,
                 workspace: Any, protected: Sequence[Any] = (),
                 calendar_nature: Any = None) -> None:
        self.db = db
        self.library = library
        self.models = models
        self.config = config
        self.workspace = Path(workspace)
        self.folder = self.workspace / PACKAGE_FOLDER
        self.calendar_nature = calendar_nature
        #: Never read from: the application's own state, the package itself.
        self.protected = [Path(one) for one in protected] + [self.folder]
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()
        #: What is under way: {kind, phase, done, total, started_at}.
        self.progress: Dict[str, Any] = {}
        self.last_error = ""

    # -- where things are --------------------------------------------------------
    @property
    def imported_root(self) -> Path:
        return Path(self.library.root) / IMPORTED_FOLDER

    def zips(self) -> List[Path]:
        """The zips a packaging made, oldest first - by their names alone,
        so no other zip of the user's is ever counted, or let go of."""
        try:
            return sorted(one for one in self.workspace.glob("lessons_*.zip")
                          if _ZIP_NAME.match(one.name))
        except OSError:                     # pragma: no cover
            return []

    # -- in the background ----------------------------------------------------------
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _start(self, kind: str, work: Any) -> bool:
        with self._lock:
            if self.running():
                return False
            self._cancel.clear()
            self.last_error = ""
            self.progress = {"kind": kind, "phase": "starting", "done": 0,
                             "total": 0, "started_at": _now()}

            def run() -> None:
                try:
                    work()
                except Cancelled:
                    self.last_error = f"the {kind} was cancelled; nothing it " \
                                      f"had not finished was kept as done"
                except Exception as exc:
                    log.warning("lessons %s failed", kind, exc_info=True)
                    self.last_error = f"the {kind} failed: {exc}"
                finally:
                    self.progress = {}

            self._thread = threading.Thread(target=run, name=f"lessons-{kind}",
                                            daemon=True)
            self._thread.start()
            return True

    def start_package(self) -> bool:
        return self._start("packaging", self.package)

    def start_load(self, path: Any) -> bool:
        return self._start("loading", lambda: self.load(path))

    def cancel(self) -> bool:
        if not self.running():
            return False
        self._cancel.set()
        return True

    def wait(self, timeout: float = 120.0) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _check(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    def _say(self, phase: str, done: int = 0, total: int = 0) -> None:
        if self.progress is not None:
            self.progress.update(phase=phase, done=done, total=total)

    # -- what the page shows ----------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        latest = self._read_json(self.folder / "latest.json") or {}
        history = self._read_json(self.folder / "history.json") or []
        return {
            "folder": str(self.folder), "exists": self.folder.is_dir(),
            "running": self.running(),
            "progress": dict(self.progress or {}) if self.running() else {},
            "last_error": self.last_error,
            "last": latest.get("stats") or None,
            "manifest": latest.get("manifest") or "",
            "package_id": latest.get("package_id") or "",
            "history": list(reversed(history))[:20] if isinstance(history, list) else [],
            "zips": [{"name": one.name, "path": str(one),
                      "bytes": one.stat().st_size} for one in reversed(self.zips())
                     if one.exists()],
            "last_load": self.last_load(),
            "merge_rules": MERGE_RULES,
        }

    def last_load(self) -> Optional[Dict[str, Any]]:
        row = self.db.query_one("SELECT value FROM kv WHERE key = ?",
                                ("lessons_package_last_load",))
        return _loads(row["value"], None) if row else None

    @staticmethod
    def _read_json(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # -- what may never be carried ------------------------------------------------------
    def refused(self, path: Any) -> str:
        """Why a file may not be carried - '' when it may."""
        try:
            resolved = Path(path).resolve()
        except (OSError, ValueError):
            resolved = Path(path)
        for root in self.protected:
            try:
                root = root.resolve()
            except OSError:                 # pragma: no cover
                pass
            if resolved == root or root in resolved.parents:
                return "the application's protected state" \
                    if root != self.folder.resolve() else "the package itself"
        name = resolved.name.lower()
        if any(fnmatch.fnmatch(name, pattern) for pattern in SECRET_NAMES):
            return "it looks like a credential or a key"
        return ""

    def private(self, location: str, title: str = "") -> str:
        """Why a reference is one the listener is told never to read - a
        password manager, a bank, a private window, a denied application or
        site - or ''."""
        lists = []
        for key in ("listener_screen_excluded_applications",
                    "listener_application_denylist"):
            try:
                lists += [one.lower() for one in self.config.lines(key)]
            except Exception:               # pragma: no cover - a config without it
                continue
        try:
            sites = [one.lower() for one in self.config.lines("listener_website_denylist")]
        except Exception:                   # pragma: no cover
            sites = []
        said = [title.lower()]
        if location.startswith(("http://", "https://")):
            host = (urlparse(location).hostname or "").lower()
            if any(site and (host == site or host.endswith("." + site) or site in host)
                   for site in sites):
                return "a site the listener never records"
            said.append(host)
        elif is_local(location):
            said.append(Path(location).name.lower())
        else:
            said.append(location.lower())
        for name in lists:
            if name and any(name in one for one in said if one):
                return f"what the listener never reads ({name})"
        return ""

    # ================================================================= packaging
    def package(self) -> Dict[str, Any]:
        """PACKAGE ALL LESSONS, now, in this thread. Returns the stats."""
        return _Packing(self).go()

    # ================================================================== loading
    def load(self, path: Any) -> Dict[str, Any]:
        """LOAD LESSONS from a package - its zip, or its folder - now, in
        this thread. Returns what was added, updated and skipped."""
        report = _Loading(self, Path(str(path))).go()
        self.db.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO "
            "UPDATE SET value = excluded.value",
            ("lessons_package_last_load", json.dumps(report, ensure_ascii=False)))
        return report


# ============================================================================ packing
class _Packing:
    """One packaging."""

    def __init__(self, owner: LessonsPackage) -> None:
        self.owner = owner
        self.db = owner.db
        self.folder = owner.folder
        self.used: Dict[str, int] = {"calls": 0, "tokens_in": 0, "tokens_out": 0}
        self.bytes_added = 0
        self.refs_stats = {"references": 0, "files": 0, "copied": 0,
                           "compressed": 0, "summarised": 0, "reused": 0,
                           "missing": 0, "excluded": 0, "unsummarised": 0,
                           "folders": 0, "urls": 0}
        #: A file's work done once a packaging, however many cite it.
        self._files: Dict[str, Dict[str, Any]] = {}

    # -- the folder -------------------------------------------------------------
    def _identity(self) -> Dict[str, Any]:
        path = self.folder / "package.json"
        held = LessonsPackage._read_json(path)
        if isinstance(held, dict) and _PACKAGE_ID.match(str(held.get("package_id") or "")):
            return held
        held = {"format": FORMAT, "package_id": f"pkg_{uuid.uuid4().hex[:16]}",
                "created_at": _now(), "machine": platform.node()}
        _write_bytes(path, json.dumps(held, indent=1).encode("utf-8"))
        return held

    def _previous(self) -> Dict[str, Any]:
        latest = LessonsPackage._read_json(self.folder / "latest.json") or {}
        name = str(latest.get("manifest") or "")
        if not name.startswith("manifests/"):
            return {}
        return LessonsPackage._read_json(self.folder / name) or {}

    def _put(self, rel: str, data: bytes) -> bool:
        """A file of the package by its content: written once. True when it
        was written now."""
        target = self.folder / rel
        if target.exists():
            return False
        _write_bytes(target, data)
        self.bytes_added += len(data)
        return True

    # -- the run ---------------------------------------------------------------------
    def go(self) -> Dict[str, Any]:
        owner = self.owner
        began = time.monotonic()
        started = _now()
        stamp = _stamp()
        self.folder.mkdir(parents=True, exist_ok=True)
        identity = self._identity()
        previous = self._previous()
        owner._say("reading the lessons")
        collector = _Collector()
        rows_out: Dict[str, List[Dict[str, Any]]] = {}
        items: Dict[str, Dict[str, int]] = {}
        before = previous.get("rows") or {}

        def keep(kind: str, key: str, row: Dict[str, Any], version: Any) -> None:
            data = _canonical(row)
            sha = _sha(data)
            self._put(f"records/{kind}/{sha[:2]}/{sha}.json", data)
            rows_out.setdefault(kind, []).append(
                {"key": key, "sha": sha, "updated": str(version or "")})
            had = {one["key"]: one["sha"] for one in before.get(kind) or []} \
                if kind not in self._before_index else self._before_index[kind]
            self._before_index[kind] = had
            count = items.setdefault(kind, {"total": 0, "new": 0, "updated": 0,
                                            "unchanged": 0})
            count["total"] += 1
            if key not in had:
                count["new"] += 1
            elif had[key] != sha:
                count["updated"] += 1
            else:
                count["unchanged"] += 1

        self._before_index: Dict[str, Dict[str, str]] = {}
        stage_ids: set = set()
        for kind, table, key, version in ROW_TABLES:
            for row in self.db.query(f"SELECT * FROM {table}"):
                owner._check()
                if kind == "calendar_action" or kind.startswith("connection_"):
                    pass
                elif not carried_scope(row.get("scope")):
                    continue
                elif kind == "workspace" and str(row.get("unit_id") or "").startswith(DEBUG_PREFIX):
                    continue
                if kind == "stage":
                    stage_ids.add(row["id"])
                    for column in ("work", "deliverable"):
                        collector.walk(_loads(row.get(column), {}), _owner_of(kind, row))
                elif kind == "card":
                    collector.walk(_loads(row.get("refs"), []), _owner_of(kind, row))
                keep(kind, str(row[key]), row, row.get(version))
        # The nature records: files in the library, read whole.
        from ..data_manage.library import USER_NATURE
        nature_ids: set = set()
        for path in self.owner.library.files(USER_NATURE):
            owner._check()
            record = self.owner.library.read(path)
            if record is None:
                continue
            try:
                text = Path(path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):   # pragma: no cover
                continue
            nature_ids.add(record.id)
            meta = record.metadata or {}
            owned = _owner_of("nature", {"id": record.id, "title": record.title,
                                         "summary": record.summary})
            for name in ("examples", "example", "history"):
                collector.walk(_loads(meta.get(name), None), owned)
            updated = (record.updated_at or record.created_at)
            keep("nature", record.id, {"id": record.id, "file": Path(path).name,
                                       "text": text,
                                       "updated_at": updated.isoformat()},
                 updated.isoformat())
        # The small tables, whole.
        links = self._links(stage_ids, nature_ids)
        prior_links = previous.get("links") or {}
        for table, rows in links.items():
            held = {_canonical(one) for one in prior_links.get(table) or []}
            items[table] = {"total": len(rows),
                            "new": sum(1 for one in rows if _canonical(one) not in held),
                            "updated": 0, "unchanged": 0}
            items[table]["unchanged"] = items[table]["total"] - items[table]["new"]
        # The references.
        references, file_index = self._references(collector, previous)
        # The manifest: written whole, then made the latest.
        owner._check()
        owner._say("writing the manifest")
        seconds = round(time.monotonic() - began, 2)
        stats = {
            "stamp": stamp, "started_at": started, "finished_at": _now(),
            "seconds": seconds, "calls": self.used.get("calls", 0),
            "tokens_in": self.used.get("tokens_in", 0),
            "tokens_out": self.used.get("tokens_out", 0),
            "tokens_in_cached": self.used.get("tokens_in_cached", 0),
            "tokens_in_cache_written": self.used.get("tokens_in_cache_written", 0),
            "items": items, "references": dict(self.refs_stats),
        }
        manifest = {
            "format": FORMAT, "version": FORMAT_VERSION,
            "package_id": identity["package_id"], "stamp": stamp,
            "created_at": _now(), "machine": platform.node(),
            "app_version": _app_version(),
            "schema": {"tables": self._schema()},
            "merge_rules": MERGE_RULES,
            "rows": rows_out, "links": links,
            "references": references, "file_index": file_index,
            "next_ref": self._next_number,
        }
        manifest_rel = f"manifests/manifest_{stamp}.json"
        manifest["stats"] = dict(stats, bytes_added=self.bytes_added)
        data = json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")
        self._put(manifest_rel, data)
        stats["bytes_added"] = self.bytes_added
        stats["manifest"] = manifest_rel
        stats["package_bytes"] = _folder_size(self.folder)
        latest = {"format": FORMAT, "version": FORMAT_VERSION,
                  "package_id": identity["package_id"], "manifest": manifest_rel,
                  "stats": stats}
        _write_bytes(self.folder / "latest.json",
                     json.dumps(latest, ensure_ascii=False, indent=1).encode("utf-8"))
        # The zip, for carrying: after the manifest, so the package is whole
        # whether or not the zip is made.
        owner._say("making the zip")
        zip_began = time.monotonic()
        made = self._zip(stamp)
        stats.update(zip=made.name, zip_path=str(made), zip_bytes=made.stat().st_size,
                     zip_seconds=round(time.monotonic() - zip_began, 2),
                     seconds_total=round(time.monotonic() - began, 2))
        latest["stats"] = stats
        _write_bytes(self.folder / "latest.json",
                     json.dumps(latest, ensure_ascii=False, indent=1).encode("utf-8"))
        history = LessonsPackage._read_json(self.folder / "history.json")
        history = history if isinstance(history, list) else []
        history = (history + [{k: v for k, v in stats.items() if k != "items"}
                              | {"items": {k: dict(v) for k, v in items.items()}}]
                   )[-HISTORY_KEPT:]
        _write_bytes(self.folder / "history.json",
                     json.dumps(history, ensure_ascii=False, indent=1).encode("utf-8"))
        return stats

    def _schema(self) -> Dict[str, List[str]]:
        tables = [table for _kind, table, _key, _version in ROW_TABLES] + list(LINK_TABLES)
        return {table: [row["name"] for row in self.db.query(f"PRAGMA table_info({table})")]
                for table in tables}

    def _links(self, stage_ids: set, nature_ids: set) -> Dict[str, List[Dict[str, Any]]]:
        out: Dict[str, List[Dict[str, Any]]] = {}
        out["experience_edges"] = [row for row in self.db.query(
            "SELECT * FROM experience_edges ORDER BY created_at, id")
            if carried_scope(row.get("scope"))]
        out["stage_natures"] = [row for row in self.db.query(
            "SELECT * FROM stage_natures ORDER BY stage_id, record_id")
            if row["stage_id"] in stage_ids]
        out["nature_links"] = [row for row in self.db.query(
            "SELECT * FROM nature_links ORDER BY state_id, record_id")
            if row["record_id"] in nature_ids]
        out["nature_kinds"] = [row for row in self.db.query(
            "SELECT * FROM nature_kinds ORDER BY record_id")]
        out["experience_learning"] = [row for row in self.db.query(
            "SELECT * FROM experience_learning ORDER BY unit_id")
            if not str(row["unit_id"]).startswith(DEBUG_PREFIX)
            and "|" not in str(row["unit_id"])]
        out["calendar_nature"] = self.db.query(
            "SELECT * FROM calendar_nature ORDER BY created_at, id")
        return out

    # -- the references ----------------------------------------------------------------
    def _references(self, collector: _Collector, previous: Dict[str, Any]
                    ) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        owner = self.owner
        numbers = {str(one.get("key")): str(one.get("id"))
                   for one in previous.get("references") or []
                   if _REF_ID.match(str(one.get("id") or ""))}
        self._next_number = int(previous.get("next_ref") or 0) or (
            max([int(one[4:]) for one in numbers.values()] or [0]) + 1)
        self._prior_files = previous.get("file_index") or {}
        file_index: Dict[str, Dict[str, Any]] = {}
        out: List[Dict[str, Any]] = []
        keys = sorted(collector.refs)
        for done, key in enumerate(keys):
            owner._check()
            owner._say("the references", done, len(keys))
            held = collector.refs[key]
            number = numbers.get(key)
            if number is None:
                number = f"ref_{self._next_number:06d}"
                self._next_number += 1
            location = held["location"]
            entry: Dict[str, Any] = {"id": number, "key": key, "location": location,
                                     "title": held["title"], "kind": held["kind"],
                                     "contexts": held["contexts"], "files": []}
            self.refs_stats["references"] += 1
            if location.startswith(("http://", "https://")):
                entry["is"] = "url"
                self.refs_stats["urls"] += 1
            elif is_local(location):
                entry["is"] = "path"
            else:
                entry["is"] = "name"         # a window, a message, a material
            why = owner.private(location, held["title"])
            if why:
                entry["excluded"] = why
            for path_key, (path, role) in sorted(held["files"].items()):
                if why:
                    stored = {"path": path, "role": role, "stored": "excluded", "why": why}
                    self.refs_stats["excluded"] += 1
                else:
                    stored = dict(self._file(path), role=role)
                    if stored["stored"] == "summarise":
                        stored = self._summaries(stored, entry)
                    file_index[path_key] = {k: v for k, v in stored.items()
                                            if k not in ("role", "summaries")}
                self.refs_stats["files"] += 1
                entry["files"].append(stored)
            out.append(entry)
        return out, file_index

    def _file(self, path: str) -> Dict[str, Any]:
        """One file's place in the package: copied, compressed, to be
        summarised, missing or refused - worked out once a packaging."""
        key = ref_key(path)
        if key in self._files:
            return dict(self._files[key])
        found = self._file_now(path)
        self._files[key] = found
        return dict(found)

    def _file_now(self, path: str) -> Dict[str, Any]:
        owner = self.owner
        entry: Dict[str, Any] = {"path": path}
        why = owner.refused(path)
        if why:
            self.refs_stats["excluded"] += 1
            return dict(entry, stored="excluded", why=why)
        target = Path(path)
        try:
            info = target.stat()
        except (OSError, ValueError):
            self.refs_stats["missing"] += 1
            return dict(entry, stored="missing")
        if target.is_dir():
            self.refs_stats["folders"] += 1
            return dict(entry, stored="folder")
        size, mtime = int(info.st_size), int(info.st_mtime_ns)
        prior = self._prior_files.get(ref_key(path)) or {}
        if prior.get("size") == size and prior.get("mtime_ns") == mtime \
                and re.fullmatch(r"[0-9a-f]{64}", str(prior.get("sha256") or "")):
            sha = prior["sha256"]              # unchanged: not read again
        else:
            sha = self._hash(target)
        ext = target.suffix.lower()[:12] if re.fullmatch(
            r"\.[A-Za-z0-9_]{1,11}", target.suffix or "") else ""
        entry.update(size=size, mtime_ns=mtime, sha256=sha, ext=ext,
                     name=target.name)
        if size > COPY_AT_MOST and prior.get("sha256") == sha and \
                prior.get("stored") in ("summarised", "unsummarised"):
            # Known not to compress below the most: summarised again only
            # for a context it has no summary for.
            return dict(entry, stored="summarise")
        if size <= COPY_AT_MOST:
            rel = f"objects/{sha[:2]}/{sha}{ext}"
            stored = self.folder / rel
            if stored.exists() and stored.stat().st_size == size:
                self.refs_stats["reused"] += 1
            else:
                self._copy(target, stored)
                self.bytes_added += size
                self.refs_stats["copied"] += 1
            return dict(entry, stored="copied", object=rel)
        for method, suffix in (("lzma", ".xz"), ("gzip", ".gz")):
            rel = f"objects/{sha[:2]}/{sha}{ext}{suffix}"
            if (self.folder / rel).exists():
                self.refs_stats["reused"] += 1
                return dict(entry, stored="compressed", object=rel, compression=method,
                            compressed_size=(self.folder / rel).stat().st_size)
        squeezed = self._compress(target, sha, ext, size)
        if squeezed is not None:
            rel, method, small = squeezed
            self.bytes_added += small
            self.refs_stats["compressed"] += 1
            return dict(entry, stored="compressed", object=rel, compression=method,
                        compressed_size=small)
        return dict(entry, stored="summarise")

    def _hash(self, path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            while True:
                self.owner._check()
                chunk = handle.read(4 * 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def _copy(self, source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        mark_written(part)
        shutil.copyfile(source, part)
        mark_written(target)
        os.replace(part, target)

    def _compress(self, path: Path, sha: str, ext: str, size: int
                  ) -> Optional[Tuple[str, str, int]]:
        """The file compressed, the best of deflate and LZMA - or None when
        neither brings it to COPY_AT_MOST. A sample is tried first: what
        does not compress is not compressed whole to find that out."""
        with open(path, "rb") as handle:
            sample = handle.read(SAMPLE_BYTES)
        if not sample or len(zlib.compress(sample, 1)) > SAMPLE_RATIO * len(sample):
            return None
        tried: List[Tuple[int, Path, str, str]] = []
        directory = self.folder / "objects" / sha[:2]
        directory.mkdir(parents=True, exist_ok=True)
        methods = [("gzip", ".gz")] + ([("lzma", ".xz")] if size <= LZMA_AT_MOST else [])
        for method, suffix in methods:
            part = directory / f"{sha}{ext}{suffix}.part"
            mark_written(part)
            opener = (lambda p: gzip.open(p, "wb", compresslevel=6)) if method == "gzip" \
                else (lambda p: lzma.open(p, "wb", preset=6))
            with open(path, "rb") as source, opener(part) as sink:
                while True:
                    self.owner._check()
                    chunk = source.read(4 * 1024 * 1024)
                    if not chunk:
                        break
                    sink.write(chunk)
            tried.append((part.stat().st_size, part, method, suffix))
        tried.sort()
        best = tried[0]
        for _size, part, _method, _suffix in tried[1:]:
            part.unlink(missing_ok=True)
        small, part, method, suffix = best
        if small > COPY_AT_MOST:
            part.unlink(missing_ok=True)
            return None
        rel = f"objects/{sha[:2]}/{sha}{ext}{suffix}"
        mark_written(self.folder / rel)
        os.replace(part, self.folder / rel)
        return rel, method, small

    # -- summaries -------------------------------------------------------------------
    def _summaries(self, stored: Dict[str, Any], entry: Dict[str, Any]) -> Dict[str, Any]:
        """A file too large even compressed: summarised for each context it
        is cited in (the first SUMMARY_CONTEXTS one by one, the rest
        together), each summary written once and kept."""
        contexts = list(entry.get("contexts") or [])
        groups: List[List[int]] = [[index] for index in range(min(len(contexts),
                                                                  SUMMARY_CONTEXTS - 1))]
        if len(contexts) >= SUMMARY_CONTEXTS:
            groups.append(list(range(SUMMARY_CONTEXTS - 1, len(contexts))))
        if not groups:
            groups = [[]]
        summaries = []
        for group in groups:
            asked = [contexts[i] for i in group] or [{"owner": "?", "what": ""}]
            rel = self._summary(stored, entry, asked)
            if rel:
                summaries.append({"contexts": group, "path": rel})
        if summaries:
            return dict(stored, stored="summarised", summaries=summaries)
        self.refs_stats["unsummarised"] += 1
        return dict(stored, stored="unsummarised",
                    why="too large to carry, and no model could summarise it; "
                        "tried again at the next packaging")

    def _summary(self, stored: Dict[str, Any], entry: Dict[str, Any],
                 contexts: List[Dict[str, Any]]) -> Optional[str]:
        sha = str(stored["sha256"])
        said = _sha(_canonical([{k: v for k, v in one.items() if k != "scope"}
                                for one in contexts]))[:12]
        rel = f"summaries/{sha[:16]}_{said}.md"
        if (self.folder / rel).exists():
            self.refs_stats["reused"] += 1
            return rel
        models = self.owner.models
        if models is None:
            return None
        path = Path(stored["path"])
        how, extract = _extract(path, int(stored.get("size") or 0))
        lines = ["THE CONTEXT" + (" - each of them" if len(contexts) > 1 else "")]
        for index, context in enumerate(contexts):
            if len(contexts) > 1:
                lines.append(f"Context {index + 1}:")
            lines += _context_lines(context)
        modified = datetime.fromtimestamp(int(stored.get("mtime_ns") or 0) / 1e9,
                                          tz=timezone.utc).isoformat()
        lines += ["", "THE FILE",
                  f"- name: {path.name}",
                  f"- originally at: {entry.get('location')}"
                  + (f" (this file is the agent's {stored.get('role')})"
                     if str(stored.get("role") or "").startswith("copy") else ""),
                  f"- type: {mimetypes.guess_type(str(path))[0] or path.suffix or 'unknown'}",
                  f"- size: {_human(stored.get('size'))}, modified {modified}",
                  f"- what you are given: {how}", "", extract]
        from ..models.base import ModelRequest
        from ..common.languages import in_display
        system = SUMMARY_PROMPT.replace("{words}", str(SUMMARY_WORDS))
        self.owner._check()
        try:
            response = models.invoke(
                ModelRequest(messages=[{"role": "user", "content": "\n".join(lines)}],
                             system=system, max_tokens=1500, task=MODEL_TASK,
                             language=in_display(),
                             metadata={"kind": "lessons package summary",
                                       "context_summary":
                                       f"summarising {path.name} for a lesson"}),
                role="heartbeat")
        except Exception as exc:
            log.info("a reference could not be summarised: %s", exc)
            return None
        count_use(self.used, response)
        text = str(getattr(response, "text", "") or "").strip()
        if not text:
            return None
        cited = "\n".join(f"- {one.get('owner')} {one.get('id')}: "
                          f"{one.get('what') or ''}" for one in contexts)
        body = (f"# Summary of {path.name}\n\n"
                f"Originally at: {entry.get('location')}\n"
                f"This file: {stored.get('path')} ({_human(stored.get('size'))}, "
                f"sha256 {sha})\n"
                f"Too large to carry ({_human(stored.get('size'))} and did not "
                f"compress below {_human(COPY_AT_MOST)}): summarised for why it "
                f"was cited.\nCited by:\n{cited}\n\n{text}\n")
        self._put(rel, body.encode("utf-8"))
        self.refs_stats["summarised"] += 1
        return rel

    # -- the zip ---------------------------------------------------------------------------
    def _zip(self, stamp: str) -> Path:
        workspace = self.owner.workspace
        target = workspace / f"lessons_{stamp}.zip"
        part = workspace / f"lessons_{stamp}.zip.part"
        files = sorted(path for path in self.folder.rglob("*")
                       if path.is_file() and not path.name.endswith(".part"))
        mark_written(part)
        with zipfile.ZipFile(part, "w", allowZip64=True) as archive:
            for index, path in enumerate(files):
                self.owner._check()
                if index % 50 == 0:
                    self.owner._say("making the zip", index, len(files))
                name = f"{PACKAGE_FOLDER}/{path.relative_to(self.folder).as_posix()}"
                kind = zipfile.ZIP_STORED if path.suffix.lower() in STORED_IN_ZIP \
                    else zipfile.ZIP_DEFLATED
                archive.write(path, name, compress_type=kind)
        mark_written(target)
        os.replace(part, target)
        for old in self.owner.zips()[:-ZIPS_KEPT]:
            try:
                old.unlink()
            except OSError:                 # pragma: no cover - open elsewhere
                continue
        return target


def _app_version() -> str:
    try:
        from ..version import __version__
        return str(__version__)
    except Exception:                       # pragma: no cover
        return ""


# ============================================================================ loading
class _Source:
    """A package to read: its folder, or its zip - read where it is, never
    unpacked by the names it holds."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.archive: Optional[zipfile.ZipFile] = None
        self.root: Optional[Path] = None
        self.prefix = ""
        if path.is_dir():
            for candidate in (path, path / PACKAGE_FOLDER):
                if (candidate / "latest.json").is_file():
                    self.root = candidate
                    break
            if self.root is None:
                raise ValueError(f"{path} is not a lessons package: it has no latest.json")
        elif path.is_file() and zipfile.is_zipfile(path):
            self.archive = zipfile.ZipFile(path)
            names = [one for one in self.archive.namelist()
                     if one == "latest.json" or one.endswith("/latest.json")]
            if not names:
                raise ValueError(f"{path.name} is not a lessons package: it has no latest.json")
            self.prefix = min(names, key=lambda one: one.count("/"))[:-len("latest.json")]
        else:
            raise ValueError(f"{path} is neither a lessons package folder nor its zip")

    def close(self) -> None:
        if self.archive is not None:
            self.archive.close()

    @staticmethod
    def _checked(rel: str) -> str:
        rel = str(rel or "")
        if rel not in ("latest.json", "package.json") and (
                not _SAFE_REL.match(rel) or ".." in rel.split("/")):
            raise ValueError(f"a package names a file outside itself: {rel!r}")
        return rel

    def open(self, rel: str):
        rel = self._checked(rel)
        if self.archive is not None:
            return self.archive.open(self.prefix + rel)
        return open(self.root / rel, "rb")

    def read(self, rel: str) -> bytes:
        with self.open(rel) as handle:
            return handle.read()

    def json(self, rel: str) -> Any:
        return json.loads(self.read(rel).decode("utf-8"))


class _Loading:
    """One loading: a package merged into what this computer has."""

    def __init__(self, owner: LessonsPackage, path: Path) -> None:
        self.owner = owner
        self.db = owner.db
        self.path = path
        self.report: Dict[str, Any] = {"path": str(path), "started_at": _now(),
                                       "kinds": {}, "references": {
                                           "files_copied": 0, "summaries": 0,
                                           "rewritten": 0},
                                       "calendar": {"lessons_added": 0,
                                                    "summarised_again": False},
                                       "conflicts": []}
        self._local: Dict[str, Dict[str, str]] = {}
        self._made: Dict[str, Optional[Dict[str, Any]]] = {}

    def _count(self, kind: str, what: str, by: int = 1) -> None:
        counts = self.report["kinds"].setdefault(kind, {"added": 0, "updated": 0,
                                                        "skipped": 0})
        counts[what] = counts.get(what, 0) + by

    def go(self) -> Dict[str, Any]:
        began = time.monotonic()
        source = _Source(self.path)
        try:
            latest = source.json("latest.json")
            if latest.get("format") != FORMAT:
                raise ValueError("not a lessons package")
            if int(latest.get("version") or 0) > FORMAT_VERSION:
                raise ValueError("this package was made by a newer version of the "
                                 "application; update it to load the package")
            manifest = source.json(source._checked(str(latest.get("manifest") or "")))
            package_id = str(manifest.get("package_id") or "")
            if not _PACKAGE_ID.match(package_id):
                raise ValueError("the package has no readable id")
            self.source, self.manifest, self.package_id = source, manifest, package_id
            self.report.update(package_id=package_id, stamp=manifest.get("stamp"),
                               machine=manifest.get("machine"),
                               merge_rules=MERGE_RULES)
            self.by_key = {str(one.get("key")): one for one in manifest.get("references") or []
                           if _REF_ID.match(str(one.get("id") or ""))}
            self._merge()
        finally:
            source.close()
        # WHAT THE OTHER COMPUTER'S USER DID, learned from here: every
        # action loaded is evidence for the lessons engine.
        catch_up = getattr(self.owner, "lessons_catch_up", None)
        if callable(catch_up) and getattr(self.owner, "lessons", None) is not None:
            try:
                # Learned from with the state heartbeat's next round.
                catch_up()
            except Exception:              # pragma: no cover - the load stands
                log.warning("the loaded actions could not be learned from", exc_info=True)
        self.report["finished_at"] = _now()
        self.report["seconds"] = round(time.monotonic() - began, 2)
        totals = {"added": 0, "updated": 0, "skipped": 0}
        for counts in self.report["kinds"].values():
            for name in totals:
                totals[name] += int(counts.get(name) or 0)
        self.report["totals"] = totals
        return self.report

    # -- the merge ----------------------------------------------------------------
    def _merge(self) -> None:
        owner = self.owner
        rows = self.manifest.get("rows") or {}
        # The package's columns are matched to this database's: a column
        # this version does not have is left out, one it has and the
        # package does not takes its default.
        changed_stages: List[Tuple[str, str]] = []
        new_actions: List[Dict[str, Any]] = []
        for kind, table, key, version in ROW_TABLES:
            entries = rows.get(kind) or []
            columns = {one["name"] for one in self.db.query(f"PRAGMA table_info({table})")}
            for done, entry in enumerate(entries):
                owner._check()
                owner._say(f"loading {kind}s", done, len(entries))
                row = self._record(kind, entry)
                if row is None:
                    continue
                if kind == "calendar_action":
                    self._calendar_action(row, columns, new_actions)
                    continue
                if kind == "lesson_card" and _rests_on_web(row):
                    self._count(kind, "skipped")
                    self.report["conflicts"].append(
                        f"{kind} {row.get(key)}: rests on web content, not kept")
                    continue
                if not kind.startswith("connection_") and \
                        not carried_scope(row.get("scope")):
                    continue
                held = self.db.query_one(f"SELECT * FROM {table} WHERE {key} = ?",
                                         (row[key],))
                if held is not None and "scope" in held and \
                        held.get("scope") != row.get("scope") and \
                        not carried_scope(held.get("scope")):
                    # Ids are random; one held here by a scope the package
                    # does not carry is not renamed - it is left alone, and
                    # said.
                    self._count(kind, "skipped")
                    self.report["conflicts"].append(
                        f"{kind} {row[key]}: held here by {held.get('scope')}, left as it is")
                    continue
                if held is not None and not is_newer(row.get(version), held.get(version)):
                    self._count(kind, "skipped")
                    continue
                row = self._rewritten(kind, row)
                kept = {name: value for name, value in row.items() if name in columns}
                if held is None:
                    names = list(kept)
                    self.db.execute(
                        f"INSERT INTO {table} ({', '.join(names)}) VALUES "
                        f"({', '.join('?' for _ in names)})",
                        tuple(kept[name] for name in names))
                    self._count(kind, "added")
                else:
                    names = [name for name in kept if name != key]
                    self.db.execute(
                        f"UPDATE {table} SET {', '.join(f'{name} = ?' for name in names)} "
                        f"WHERE {key} = ?", (*[kept[name] for name in names], row[key]))
                    self._count(kind, "updated")
                if kind == "stage":
                    changed_stages.append((str(row.get("scope") or LIVE), str(row["id"])))
        self._natures(rows.get("nature") or [])
        self._link_tables()
        self._index(changed_stages)
        self._calendar_text(new_actions, rows.get("calendar_nature") or [])

    def _record(self, kind: str, entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        sha = str(entry.get("sha") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            return None
        try:
            data = self.source.read(f"records/{kind}/{sha[:2]}/{sha}.json")
        except (KeyError, OSError):
            self.report["conflicts"].append(f"{kind} {entry.get('key')}: its record "
                                            f"is missing from the package")
            return None
        if _sha(data) != sha:
            self.report["conflicts"].append(f"{kind} {entry.get('key')}: its record "
                                            f"is damaged")
            return None
        row = json.loads(data.decode("utf-8"))
        return row if isinstance(row, dict) else None

    def _rewritten(self, kind: str, row: Dict[str, Any]) -> Dict[str, Any]:
        """A row's references made this computer's: each pointing at its
        copy here, with where it was originally at."""
        row = dict(row)
        owner = (kind, str(row.get("id") or ""))
        columns = {"stage": ("work", "deliverable"), "card": ("refs",)}.get(kind, ())
        for column in columns:
            value = _loads(row.get(column), None)
            if value is None:
                continue
            row[column] = json.dumps(self._rewrite(value, owner), ensure_ascii=False)
        return row

    def _rewrite(self, value: Any, owner: Tuple[str, str]) -> Any:
        if isinstance(value, list):
            return [self._rewrite(one, owner) for one in value]
        if isinstance(value, dict):
            out = {name: self._rewrite(one, owner) if isinstance(one, (dict, list)) else one
                   for name, one in value.items()}
            location = out.get("location")
            if isinstance(location, str) and location.strip():
                out = self._rewrite_reference(out, owner)
            return out
        return value

    def _rewrite_reference(self, reference: Dict[str, Any],
                           owner: Tuple[str, str]) -> Dict[str, Any]:
        ref = self.by_key.get(ref_key(reference["location"]))
        if ref is None:
            return reference
        local = self._materialise(ref)
        out = dict(reference)
        original = str(ref.get("location") or reference["location"])
        out["originally_at"] = str(reference.get("originally_at") or original)
        out["imported_from"] = f"{self.package_id}/{ref['id']}"
        by_path = local["paths"]
        summary = self._summary_for(ref, local, owner)
        files = {ref_key(one.get("path")): one for one in ref.get("files") or []}
        if ref_key(reference["location"]) in by_path:
            out["location"] = by_path[ref_key(reference["location"])]
        elif summary is not None and any(
                one.get("role") == "original" and one.get("stored") == "summarised"
                for one in files.values()):
            out["location"] = summary[0]
        copies = dict(out.get("copies") or {}) if isinstance(out.get("copies"), dict) else {}
        for name, value in list(copies.items()):
            if isinstance(value, str) and ref_key(value) in by_path:
                copies[name] = by_path[ref_key(value)]
        for name in ("transcript", "snapshot", "screenshot"):
            value = out.get(name)
            if isinstance(value, str) and ref_key(value) in by_path:
                out[name] = by_path[ref_key(value)]
        if isinstance(out.get("screenshots"), list):
            out["screenshots"] = [by_path.get(ref_key(one), one) if isinstance(one, str)
                                  else one for one in out["screenshots"]]
        if summary is not None:
            # SUMMARISED: what the reference says is its summary - the text
            # an agent reads of it, unless a smaller copy of its text came.
            out["summary"] = summary[1][:6000]
            out["summarised"] = True
            text = copies.get("text")
            if not (isinstance(text, str) and str(Path(text)).startswith(
                    str(self.owner.imported_root)) and Path(text).exists()):
                copies["text"] = summary[0]
        if copies:
            out["copies"] = copies
        if any(one.get("stored") == "missing" for one in files.values()) and \
                not by_path:
            out["missing_when_packaged"] = True
        if is_local(str(out.get("location") or "")) and Path(out["location"]).exists():
            out["access"] = "local"
        if out != reference:
            self.report["references"]["rewritten"] += 1
        return out

    def _materialise(self, ref: Dict[str, Any]) -> Dict[str, Any]:
        """A reference's files, out of the package into this computer's
        folder for them - once; what is there already is not copied again."""
        ref_id = str(ref["id"])
        if ref_id in self._made:
            return self._made[ref_id]
        folder = self.owner.imported_root / self.package_id / ref_id
        paths: Dict[str, str] = {}
        summaries: List[Dict[str, Any]] = []
        for one in ref.get("files") or []:
            self.owner._check()
            stored = one.get("stored")
            name = _safe_name(f"{one.get('role') or 'file'}_"
                              f"{Path(str(one.get('path') or '')).name or 'file'}")
            if stored in ("copied", "compressed") and one.get("object"):
                target = folder / name
                size = int(one.get("size") or -1)
                if not (target.exists() and target.stat().st_size == size):
                    try:
                        self._unpack(str(one["object"]), one.get("compression"), target)
                        self.report["references"]["files_copied"] += 1
                    except (KeyError, OSError, ValueError, EOFError, lzma.LZMAError) as exc:
                        self.report["conflicts"].append(
                            f"{ref_id}: {one.get('path')} could not be taken out "
                            f"of the package ({exc})")
                        continue
                paths[ref_key(one.get("path"))] = str(target)
            elif stored == "summarised":
                for index, said in enumerate(one.get("summaries") or []):
                    target = folder / (_safe_name(f"summary_{index + 1}_{name}") + ".md")
                    try:
                        if not target.exists():
                            data = self.source.read(str(said.get("path") or ""))
                            _write_bytes(target, data)
                            self.report["references"]["summaries"] += 1
                        text = target.read_text(encoding="utf-8")
                    except (KeyError, OSError, ValueError) as exc:
                        self.report["conflicts"].append(
                            f"{ref_id}: a summary could not be taken out ({exc})")
                        continue
                    summaries.append({"contexts": list(said.get("contexts") or []),
                                      "path": str(target), "text": text,
                                      "file": ref_key(one.get("path"))})
        made = {"paths": paths, "summaries": summaries}
        self._made[ref_id] = made
        return made

    def _unpack(self, rel: str, compression: Any, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        mark_written(part)
        with self.source.open(rel) as raw:
            if compression == "lzma":
                stream: Any = lzma.LZMAFile(raw)
            elif compression == "gzip":
                stream = gzip.GzipFile(fileobj=raw)
            else:
                stream = raw
            with open(part, "wb") as sink:
                shutil.copyfileobj(stream, sink, 4 * 1024 * 1024)
        mark_written(target)
        os.replace(part, target)

    @staticmethod
    def _summary_for(ref: Dict[str, Any], local: Dict[str, Any],
                     owner: Tuple[str, str]) -> Optional[Tuple[str, str]]:
        """The summary written for the context this reference is cited in
        - (its file here, its text) - or the first, or None."""
        summaries = local.get("summaries") or []
        if not summaries:
            return None
        contexts = ref.get("contexts") or []
        mine = [index for index, one in enumerate(contexts)
                if (str(one.get("owner")), str(one.get("id"))) == owner]
        for one in summaries:
            if any(index in one["contexts"] for index in mine):
                return one["path"], one["text"]
        return summaries[0]["path"], summaries[0]["text"]

    # -- natures -----------------------------------------------------------------------
    def _natures(self, entries: List[Dict[str, Any]]) -> None:
        from ..data_manage.library import USER_NATURE, Record
        library = self.owner.library
        held: Dict[str, Any] = {}
        for record in library.records(USER_NATURE):
            held[record.id] = record
        folder = library.folder(USER_NATURE)
        for done, entry in enumerate(entries):
            self.owner._check()
            self.owner._say("loading natures", done, len(entries))
            row = self._record("nature", entry)
            if row is None:
                continue
            record = Record.from_text(str(row.get("text") or ""), folder=USER_NATURE)
            if record.id != str(row.get("id") or record.id):
                continue
            here = held.get(record.id)
            theirs = record.updated_at or record.created_at
            if here is not None and not is_newer(
                    theirs.isoformat(), (here.updated_at or here.created_at).isoformat()):
                self._count("nature", "skipped")
                continue
            owner = ("nature", record.id)
            moved: List[Tuple[str, str]] = []
            for name in ("examples", "example", "history"):
                value = _loads(record.metadata.get(name), None)
                if value is None:
                    continue
                rewritten = self._rewrite(value, owner)
                moved += _moved_locations(value, rewritten)
                record.metadata[name] = json.dumps(rewritten, ensure_ascii=False)
            body = record.body
            for old, new in dict.fromkeys(moved):
                body = body.replace(f"  - {old}", f"  - {new} (originally at {old})")
            record.body = body
            text = record.to_text()
            if here is not None and here.path:
                path = Path(here.path)
            else:
                name = _safe_name(str(row.get("file") or ""), "nature")
                name = name if name.lower().endswith(".md") else name + ".md"
                path = folder / name
                suffix = 2
                while path.exists():
                    path = folder / f"{name[:-3]}-{suffix}.md"
                    suffix += 1
            folder.mkdir(parents=True, exist_ok=True)
            _write_bytes(path, text.encode("utf-8"))
            try:
                library._counts_changed()
            except Exception:               # pragma: no cover
                pass
            self._count("nature", "updated" if here is not None else "added")

    # -- the small tables ------------------------------------------------------------------
    def _link_tables(self) -> None:
        links = self.manifest.get("links") or {}
        for row in links.get("experience_edges") or []:
            if not carried_scope(row.get("scope")):
                continue
            if self.db.query_one("SELECT 1 FROM experience_edges WHERE id = ? OR "
                                 "(scope = ? AND source = ? AND target = ? AND kind = ?)",
                                 (row.get("id"), row.get("scope"), row.get("source"),
                                  row.get("target"), row.get("kind"))):
                self._count("experience_edges", "skipped")
                continue
            self._insert("experience_edges", row)
            self._count("experience_edges", "added")
        for table in ("stage_natures", "nature_links", "nature_kinds"):
            for row in links.get(table) or []:
                if self._insert(table, row, ignore=True):
                    self._count(table, "added")
                else:
                    self._count(table, "skipped")
        for row in links.get("experience_learning") or []:
            unit = str(row.get("unit_id") or "")
            if not unit or unit.startswith(DEBUG_PREFIX) or "|" in unit:
                continue
            here = self.db.query_one("SELECT * FROM experience_learning WHERE unit_id = ?",
                                     (unit,))
            if here is None:
                self._insert("experience_learning", row)
                self._count("experience_learning", "added")
            elif is_newer(row.get("learned_at"), here.get("learned_at")):
                self.db.execute("DELETE FROM experience_learning WHERE unit_id = ?", (unit,))
                self._insert("experience_learning", row)
                self._count("experience_learning", "updated")
            else:
                self._count("experience_learning", "skipped")

    def _columns(self, table: str) -> set:
        if table not in self._local:
            self._local[table] = {one["name"] for one in
                                  self.db.query(f"PRAGMA table_info({table})")}
        return self._local[table]

    def _insert(self, table: str, row: Dict[str, Any], ignore: bool = False) -> bool:
        kept = {name: value for name, value in row.items() if name in self._columns(table)}
        names = list(kept)
        cursor = self.db.execute(
            f"INSERT {'OR IGNORE ' if ignore else ''}INTO {table} ({', '.join(names)}) "
            f"VALUES ({', '.join('?' for _ in names)})",
            tuple(kept[name] for name in names))
        return bool(getattr(cursor, "rowcount", 1))

    def _index(self, stages: List[Tuple[str, str]]) -> None:
        """What each stage loaded is found by, worked out here as it is for
        a stage written here."""
        if not stages:
            return
        from .experience.index import StageIndex
        from .experience.store import ExperienceStore
        for scope, stage_id in stages:
            store = ExperienceStore(self.db, scope)
            stage = store.stage(stage_id)
            if stage is None:
                continue
            try:
                StageIndex(self.db, scope).add(
                    stage, store.card(stage["card_id"]) if stage.get("card_id") else None)
            except Exception:               # pragma: no cover - found later instead
                log.info("a loaded stage could not be indexed", exc_info=True)

    # -- notification lessons -----------------------------------------------------------------
    def _calendar_action(self, row: Dict[str, Any], columns: set,
                         new_actions: List[Dict[str, Any]]) -> None:
        here = self.db.query_one("SELECT * FROM calendar_actions WHERE id = ?",
                                 (row.get("id"),))
        if here is None:
            self._insert("calendar_actions", row)
            self._count("calendar_action", "added")
            new_actions.append(row)
        elif not here.get("reasoned_at") and row.get("reasoned_at"):
            self.db.execute("UPDATE calendar_actions SET reasoned_at = ?, why = ?, "
                            "lesson = ?, event = ? WHERE id = ?",
                            (row.get("reasoned_at"), row.get("why"), row.get("lesson"),
                             row.get("event") or here.get("event"), row["id"]))
            self._count("calendar_action", "updated")
            new_actions.append(row)
        else:
            self._count("calendar_action", "skipped")

    def _calendar_text(self, actions: List[Dict[str, Any]],
                       lessons: Any = ()) -> None:
        """The package's notification lessons, each added here unless a
        lesson here already says it: the package's list (a lesson a row),
        and - from a package made before there was a list - the lesson each
        of its new actions drew."""
        from .core.calendar_nature import _entry_lesson
        said = [str(row.get("content") or "") for row in lessons or []
                if isinstance(row, dict) and row.get("kind") == "lesson"]
        said += [_entry_lesson(str(row.get("content") or "")) for row in lessons or []
                 if isinstance(row, dict) and row.get("kind") == "entry"]
        said += [str(row.get("lesson") or "") for row in sorted(
                     actions, key=lambda one: str(one.get("acted_at") or ""))
                 if row.get("reasoned_at") and row.get("lesson")
                 and not str(row.get("lesson")).startswith(("Added:", "Changed:", "Removed:"))]
        nature = self.owner.calendar_nature
        added = nature.adopt(said) if nature is not None else 0
        self.report["calendar"]["lessons_added"] = added


def _moved_locations(before: Any, after: Any) -> List[Tuple[str, str]]:
    """(old, new) for each reference a rewrite moved."""
    out: List[Tuple[str, str]] = []
    if isinstance(before, list) and isinstance(after, list):
        for one, two in zip(before, after):
            out += _moved_locations(one, two)
    elif isinstance(before, dict) and isinstance(after, dict):
        old, new = before.get("location"), after.get("location")
        if isinstance(old, str) and isinstance(new, str) and old != new:
            out.append((old, new))
        for name, value in before.items():
            if isinstance(value, (dict, list)) and name in after:
                out += _moved_locations(value, after[name])
    return out


__all__ = ["LessonsPackage", "Cancelled", "FORMAT", "FORMAT_VERSION",
           "PACKAGE_FOLDER", "IMPORTED_FOLDER", "COPY_AT_MOST", "MERGE_RULES",
           "SUMMARY_PROMPT", "carried_scope", "ref_key", "is_newer"]
