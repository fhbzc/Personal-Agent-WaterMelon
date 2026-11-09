"""DELEGATE WORK - and how every piece of work starts: context, a plan the
user checks, then the run.

The user hands something over - text they selected, a folder or a file they
right-clicked, a to-do, or what they typed into Add work - and before
anything is done:

1. CONTEXT IS COLLECTED, in a process of its own (child.py): what they
   handed over; for selected text, the window it came from (its title, its
   address, the words it showed - as read off the screen, never by signing
   in) and the public page at that address, read as anyone could; what is
   in the folders (their names, a README); and what of the user's own
   records - states, tasks, stages, from any time - bears on it, found by
   its words (context_search.py).
2. A HIGH-LEVEL PLAN IS DRAFTED from it: not the detailed plan - that is
   the run's contract - but enough for the user to see whether the task was
   understood: what it is, the goal, the main steps, what will be delivered
   and what is left out, and anything it needs to ask.
3. THE USER EDITS IT, or not, and presses Start - or cancels. Nothing runs
   before that.
4. THE RUN GOES, the approved plan given to it word for word as the user's.

Delegated work becomes a project of its own, under "Delegated work" in
Experience; work started from a project or task goes on that one.

A TO-DO HANDED OVER AGAIN IS A TRY OF THE SAME WORK (the user, 2026-10-31):
not a project of its own but another run of the first try's, V2, V3... -
its context the first's, not collected again, and its instruction box
opened with the try before's words, to change. What differs is its own:
the instruction, the plan drafted from it, its run, its tree, and its
folder - the first try's name with its own ("Title V2"), so no try's files,
nor going back to one's save point, touch another's.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...common.languages import in_display
from .picked import told as picked_told

log = logging.getLogger(__name__)

COLLECTING, DRAFTING, READY, STARTED, CANCELLED, FAILED = (
    "collecting", "drafting", "ready", "started", "cancelled", "failed")
#: Handed over, waiting for the user's instruction: nothing is collected until
#: they press Create (`compose`) - from anywhere work is handed over.
WRITING = "writing"
#: Start pressed, and the work being set going apart (`start_apart`): the
#: page is answered at once, and asks how it went.
STARTING = "starting"
#: SAID IN THE PLAN WHEN IT COULD NOT BE WRITTEN (2026-11-07), in the display language.
PLAN_CUT = ("The plan could not be written: the model's answer stopped at the length limit. "
            "Write it here, or hand the work over again.")
PLAN_UNREAD = ("The plan could not be written: the model's answer could not be read. "
               "Write it here, or hand the work over again.")
#: What the plan says - the user may change each.
PLAN_PARTS = ("title", "understanding", "goal", "steps", "deliverables", "out_of_scope",
              "questions")
#: HOW THE WORK IS DONE (the user, 2026-10-26): "quick" - one agent on its
#: own judgement, with the user's context - or "full", Thorough: a builder
#: and a reviewer, with a contract. The plan says which it would take and
#: why; the user chooses.
WAYS = ("quick", "full")
#: How much is read of each thing collected.
PAGE_CHARS, SCREEN_CHARS, README_CHARS, MOST_ENTRIES = 8000, 6000, 3000, 60
#: Of each try before, what it reported last, read this far.
TRY_REPORT = 2500
#: A DOCUMENT ON THEIR COMPUTER, READ INTO WHAT THE PLAN IS DRAFTED FROM (the
#: user, 2026-11-02 - a PDF right-clicked in the browser: the plan knew it
#: only from their records, its page read as nothing): the one open where
#: they right-clicked (a file:// address), and each file handed over - read
#: here (runtime/documents.py: a PDF's pages, Word, PowerPoint, Excel, a
#: picture's words), this much of its text; none larger than this is read.
DOCUMENT_CHARS, FILE_CHARS, DOCUMENT_LARGEST = 12000, 6000, 100 * 1024 * 1024
#: Files read as they are.
TEXT_SUFFIXES = (".md", ".txt", ".py", ".csv", ".json", ".tex", ".r", ".js", ".html",
                 ".yaml", ".yml", ".toml", ".ipynb")
#: WHO IT IS FOR (the user, 2026-10-31): at most this many people, and of
#: each, what passed between them and the user - the latest this many, each
#: read this far.
MOST_PEOPLE, MOST_EXCHANGES, EXCHANGE_CHARS = 4, 14, 1500
#: A PERSON'S EXCHANGES, ON THE WORK'S SUBJECT (the user, 2026-11-08: "人名+工作"): those that are -
#: and, fewer than this, the latest of the rest beside them, to say who they are.
LEAST_EXCHANGES = 3
#: Words too common to tell a subject by.
TOPIC_STOP = frozenset("""with from about their they them that this what when where which will would
should could have been were your yours into over under after before more most some such than then
also only just very work works working email emails mail meeting meetings follow follow-up draft
drafts project projects platform paper papers site page pages tool tools list plan plans call calls
team note notes update updates thing things time today week month year next last first send sent
reply write written check done make made user users agent agents report reports doc docs
document documents""".split())
#: THE USER'S LESSONS FOR A PIECE OF WORK (the user, 2026-11-08): of how they work, and general;
#: so many rules and examples at most, found by what it is and by so many of its steps.
LESSON_KINDS = ("work", "general")
LESSON_RULES, LESSON_EXAMPLES, LESSON_STEPS, LESSON_CHARS = 10, 3, 6, 6000
#: Rules that name the work's subject itself, given first, at most so many.
LESSON_NAMED = 8
#: THE OTHER SOURCES (the user, 2026-11-08: "收集时加上这几个来源"): at most so many of each.
MOST_SAID, MOST_LEARNED, MOST_OTHER_WORKS, MOST_MAIL, MOST_CALENDAR, MOST_REMINDERS = 10, 10, 6, 5, 8, 6

PLAN_PROMPT = """THE USER IS HANDING YOU A PIECE OF WORK. Before any of it is done, say
how you understand it, as a short HIGH-LEVEL PLAN they will check and may
change: not the detailed plan - only enough for them to see at a glance
whether you understood the task.

Below is what they handed over, and the context collected around it: the
window it came from (when they selected text in it) and its public page, a
document of theirs open there ("document": its text, or the start of it),
the folders and files they gave and what is in them, and what was FOUND of their own
records by its words - their activity, tasks and past work, each with how
relevant and how recent it is. Use what bears on the task; leave out what
does not.

THIS PLAN IS HIGH-LEVEL, AND QUICK: drafted from what is below, with no
looking further. The detailed plan is made when the work starts - with what
was found here as references, and the work's own tools to search the
user's records, their files and the web.

Return ONLY this JSON object:
{"context_summary": "two or three sentences: what you found that bears on it,
                     and where",
 "title": "a short name for the work, four to eight words",
 "understanding": "one or two sentences: what you understand them to want",
 "goal": "one sentence: what is true when it is done",
 "steps": ["three to seven main steps, each a short line"],
 "deliverables": ["what they will get - each IN THE FORM MOST CONVENIENT for
                   what they will do with it, and how they get it: an email
                   they will send - a draft opened in their own mail, ready to
                   send; a meeting - an event for their calendar; words to
                   paste - text to copy; a document to share - .docx or .pdf;
                   figures - a sheet; a change to their files - the change;
                   an answer - the answer. With "full", always a README.md
                   saying how to check and use what was made"],
 "out_of_scope": ["what you will not do, where it could be mistaken for part
                   of it"],
 "people": ["each person the work is addressed to or is about, by the name
             the records give them, with their email address where it is
             known: "Name <address>" - none when it is for no one"],
 "questions": ["only what you cannot settle from the context and would change
                the plan - often none"],
 "way": "quick or full",
 "way_why": "one sentence: why that way, from what the work is"}

THE WAY: "quick" - ONE AGENT ON ITS OWN, told everything at the start, as
a session of a coding assistant would do it: light work with one result the
user will read themselves - an email, a reply, a note, a short document, a
small change. "full" - THOROUGH: a builder and a reviewer, a contract of
what each deliverable must meet, checked round by round - work with several
deliverables that depend on each other, code to build and test, anything
long, or where a mistake is costly. When you are not sure, "full".
With "quick", a README.md only when it makes more than one file."""

#: THE PLAN WHILE THE THOROUGH WAY IS SWITCHED OFF (the user, 2026-10-31):
#: the same one call, with nothing of a way to choose - every piece of work
#: is done the quick way.
PLAN_PROMPT_QUICK = PLAN_PROMPT.replace(
    'With "full", always a README.md\n                   saying how to check and use what was made"],',
    'With more than one file, a README.md\n                   saying how to check and use what was made"],'
).replace(""",
 "way": "quick or full",
 "way_why": "one sentence: why that way, from what the work is"}""", "}").split("\n\nTHE WAY:")[0]


def local_path(url: str) -> Optional[Path]:
    """The file on this computer a file:// address names (as the browser
    shows a PDF opened from the disk) - None for any other address, and for
    one on another computer's share."""
    if not str(url or "").lower().startswith("file:"):
        return None
    from urllib.parse import urlparse
    from urllib.request import url2pathname
    parsed = urlparse(url)
    if parsed.netloc not in ("", "localhost"):
        return None
    try:
        return Path(url2pathname(parsed.path))
    except (OSError, ValueError):
        return None


def read_document(path: Path, most: int) -> Dict[str, Any]:
    """A DOCUMENT OF THE USER'S, READ HERE (DOCUMENT_CHARS): its path and name,
    how many pages, the first `most` characters of its text and, when there
    is more, how much - or why it was not read. Only read, never changed."""
    out: Dict[str, Any] = {"path": str(path), "name": path.name}
    try:
        if not path.is_file():
            return dict(out, error="no such file")
        size = path.stat().st_size
        out["size"] = size
        if size > DOCUMENT_LARGEST:
            return dict(out, error="too large to read before the work starts - the work reads it")
        suffix = path.suffix.lower()
        from ...runtime import documents
        if suffix in TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8", errors="replace")
        elif suffix in documents.DOCUMENTS or suffix in documents.PICTURES:
            said = documents.extract(path)
            text = str(said.get("text") or "")
            if said.get("pages"):
                out["pages"] = len(said["pages"])
        else:
            return dict(out, error="not a kind of file read here - the work reads it")
    except Exception as exc:                          # a document unread is said, never fatal
        log.debug("could not read %s", path, exc_info=True)
        return dict(out, error=str(exc)[:200])
    out["chars"] = len(text)
    out["text"] = text[:most]
    if len(text) > most:
        out["cut"] = f"the first {most} of its {len(text)} characters - the work reads the rest"
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


#: The pictures handed over that the plan is shown, beside the one of the screen.
MOST_PLAN_PICTURES = 3

#: What goes with what was in front of the user when they delegated directly.
FRONT_NOTE = ("What was on the user's screen when they delegated this, read by this application, "
              "not said by them. The work may have nothing to do with it: use it only where "
              "their words point to it. Its \"picture\" is a screenshot, which read_file shows.")


class Drafts:
    """The `work_drafts` rows: each piece of work handed over, from its
    context to its run."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.db = app.db
        #: The drafts being started now (`start_apart`), and how each start
        #: went - what `start` said - for the page to ask.
        self._starting: set = set()
        self._started: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        #: The drafts waiting for their plan to be started (`_when_ready`).
        self._waiting: set = set()
        self._resume_waiting()

    # -- the agent's side ---------------------------------------------------------------------
    def create(self, text: str = "", folders: Optional[List[str]] = None,
               files: Optional[List[str]] = None, window: Optional[Dict[str, Any]] = None,
               unit_id: str = "", source: str = "delegate",
               options: Optional[Dict[str, Any]] = None, collect: bool = True,
               write_first: bool = False) -> Dict[str, Any]:
        """A piece of work handed over: kept, and its context collected and
        its plan drafted in a process of its own (`collect` false: not yet -
        the tests). `write_first`: as every Delegate work is - kept, and
        WAITING FOR THE USER'S INSTRUCTION, which `compose` takes; nothing is
        collected before it."""
        # WHERE IT WORKS IS THE USER'S TO SAY (the user, 2026-10-26): what is
        # handed over is where it works - listed in the box, each to take
        # off: a folder, to work in; a FILE, that file alone, changed in
        # place - never its folder, which opened the whole of it to changes
        # nobody asked for.
        folders = [str(Path(one)) for one in folders or [] if str(one).strip()]
        files = [str(Path(one)) for one in files or [] if str(one).strip()]
        folders += [one for one in files if one not in folders]
        window = dict(window or {})
        # THE WINDOW IT CAME FROM: only text selected in one has one - its
        # words read off the screen record. Anything else handed over (a
        # to-do, Add work, a folder) came from no window.
        if source == "selection" and window.get("title") and not window.get("text"):
            window.update(self._screen_now(window))
        # RIGHT-CLICKED IN macOS's SERVICES MENU (picked.py): the text alone
        # comes - the window it was in, as the listener last read it.
        if source == "picked" and (window.get("picked") or {}).get("where") == "services" \
                and not window.get("text") and not window.get("url"):
            now = self._screen_now({})
            window.update({k: v for k, v in now.items() if k != "page_title"
                           and (k != "title" or not window.get("title"))})
        # A TO-DO HANDED OVER AGAIN: opened with the try before's words, its
        # context taken as it was (`collect_and_plan`), its folders given.
        options = dict(options or {})
        before = self._try_before(str(options.get("todo") or "")) \
            if source == "todo" and options.get("todo") else None
        if before is not None:
            options.setdefault("instruction", asked_of(before))
            options["context_from"] = before["id"]
            # Where it works: the folders its work was given, as the box lists them.
            if not folders:
                try:
                    folders = list(self.app.work.folders(before["unit_id"]))                         or list(before.get("folders") or [])
                except Exception:
                    folders = list(before.get("folders") or [])
        row = {"id": "draft_" + uuid.uuid4().hex[:12], "created_at": _now(),
               "updated_at": _now(), "source": source, "text": str(text or "")[:20000],
               "folders": folders[:12], "files": files[:24], "window": window,
               "unit_id": str(unit_id or ""),
               "status": WRITING if write_first else COLLECTING, "context": {},
               "plan": {}, "plan_by": "", "options": options or {}, "run_id": "",
               "error": ""}
        self.db.execute(
            "INSERT INTO work_drafts (id, created_at, updated_at, source, text, folders, "
            "files, window, unit_id, status, context, plan, plan_by, options, run_id, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["id"], row["created_at"], row["updated_at"], row["source"], row["text"],
             json.dumps(row["folders"], ensure_ascii=False),
             json.dumps(row["files"], ensure_ascii=False),
             json.dumps(row["window"], ensure_ascii=False), row["unit_id"], row["status"],
             "{}", "{}", "", json.dumps(row["options"], ensure_ascii=False), "", ""))
        if collect and not write_first:
            self._collect_apart(row["id"])
        return self.get(row["id"]) or row

    def _person(self, name: str, addresses: Optional[List[str]] = None,
                topic: Optional[List[str]] = None) -> Dict[str, Any]:
        """WHO IT IS FOR (the user, 2026-10-31: "我怎么给这个人写邮件，他是什么
        人，我是什么人，我之前是怎么写的"): a person the work is for or about -
        who they are, as the contacts have them, and WHAT PASSED BETWEEN THEM
        AND THE USER: every record that names them, the latest kept, in the
        order they happened, each with its words whole as far as it goes."""
        addresses = [one.lower() for one in addresses or []]
        said: Dict[str, Any] = {"name": name, "contact": self._contact_of(name, addresses),
                                "exchanges": []}
        contact = said["contact"] or {}
        addresses = list(dict.fromkeys(addresses + list(contact.get("addresses") or [])))
        said["addresses"] = addresses
        # HOW EACH RECORD WAS FOUND (the user, 2026-10-31: "标出怎么匹配上的"):
        # by an address - one person's alone - else by a name.
        by = {one: "email address" for one in addresses}
        known = [str(one) for one in contact.get("names") or []]
        for kind, names in (("name", [name, *known[:1]]), ("alias", known[1:])):
            for given in names:
                for one in _names_said([str(given)]):
                    by.setdefault(one, kind if one == " ".join(str(given).split())
                                  else "family name")
        words = list(by)
        if not words:
            return said
        where = " OR ".join("(title LIKE ? OR detail LIKE ?)" for _ in words)
        params: List[Any] = []
        for one in words:
            params += [f"%{one}%", f"%{one}%"]
        try:
            # What the agent did itself is not what passed between them.
            rows = self.db.query(
                f"SELECT title, detail, starts_at, ends_at FROM states WHERE ({where}) "
                "AND NOT (source = 'agent' AND about = 'agent') "
                "ORDER BY COALESCE(ends_at, starts_at) DESC LIMIT ?",
                (*params, MOST_EXCHANGES * 6))
        except Exception:              # pragma: no cover - the work goes on without it
            rows = []
        # A name said as a word of its own: "Quill", never the "quill" of "Quillon".
        named = re.compile("|".join(
            (rf"(?<![^\W\d_]){re.escape(one)}(?![^\W\d_])" if one.isascii() else re.escape(one))
            for one in words), re.IGNORECASE)
        rows = [row for row in rows if named.search(f"{row['title'] or ''} {row['detail'] or ''}")]
        if topic:
            # ON THE WORK'S SUBJECT (the user, 2026-11-08: "人名+工作"): what passed between them about
            # it - a few of the latest besides, where too few are, to say who they are.
            strong, rest = topic_words(topic)
            about = [row for row in rows
                     if on_topic(f"{row['title'] or ''} {row['detail'] or ''}", strong, rest)]
            if len(about) < LEAST_EXCHANGES:
                about += [row for row in rows if row not in about][:LEAST_EXCHANGES - len(about)]
            rows = [row for row in rows if row in about]

        def matched(row: Any) -> str:
            text = f"{row['title'] or ''} {row['detail'] or ''}"
            for word, kind in by.items():
                if re.search((rf"(?<![^\W\d_]){re.escape(word)}(?![^\W\d_])" if word.isascii()
                              else re.escape(word)), text, re.IGNORECASE):
                    return f"{kind} {word}"
            return ""
        # Where their own words are quoted first - how the user writes to them -
        # then the latest; told in the order they happened.
        quoted = [row for row in rows if QUOTED.search(str(row["detail"] or ""))]
        kept = {id(row) for row in quoted[:MOST_EXCHANGES // 2]}
        for row in rows:
            if len(kept) >= MOST_EXCHANGES:
                break
            kept.add(id(row))
        said["exchanges"] = [{"at": str(row["ends_at"] or row["starts_at"] or ""),
                              "title": str(row["title"] or ""),
                              "detail": str(row["detail"] or "")[:EXCHANGE_CHARS],
                              "matched": matched(row)}
                             for row in reversed(rows) if id(row) in kept]
        return said

    def _contacts_named(self, draft: Dict[str, Any]) -> List[str]:
        """The contacts what was handed over, or the instruction, names - by a
        name of theirs, as the heartbeat finds them in a window."""
        store = getattr(self.app, "connections", None)
        if store is None:
            return []
        try:
            from ...heartbeat.connections.people import _named_in
            from ...heartbeat.connections.store import name_key
            said = " ".join([asked_of(draft), str(draft.get("text") or ""),
                             str((draft.get("window") or {}).get("title") or "")])
            hay = name_key(said)
            return [one["name"] for one in store.people()
                    if _named_in(one, hay) and _said_as_them(one, said)][:MOST_PEOPLE]
        except Exception:                # pragma: no cover - the work goes on without it
            return []

    def _contact_of(self, name: str,
                    addresses: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """WHO THEY ARE, as the user's contacts have them (heartbeat/
        connections/store.py) - their public profiles: the contact an email
        address is (the user, 2026-10-31: "按email地址match") - one person's
        alone - else the one the name or an alias is, else the one contact
        every word of it fits; None when none does."""
        store = getattr(self.app, "connections", None)
        if store is None:
            return None
        try:
            person = store.find("", accounts=[{"platform": "email", "handle": one}
                                              for one in addresses or []],
                                include_deleted=False) if addresses else None
            if person is None:
                # By a name: only one contact's - two of the same name are not
                # told apart by guessing.
                from ...heartbeat.connections.people import standing_word
                from ...heartbeat.connections.store import name_key
                key = name_key(name)
                # A word for how they stand to the user (学姐) names no one.
                same = [] if standing_word(name) else [
                    one for one in store.people() if key and key in {
                        name_key(one["name"]), *[name_key(alias) for alias in one["aliases"]
                                                 if not standing_word(alias)]}]
                if len(same) > 1:
                    return {"names": [name], "who": "", "relation": "", "addresses": [],
                            "several": len(same)}
                person = same[0] if same else None
            if person is None:
                found = store.search(name, most=2)
                person = found[0] if len(found) == 1 else None
        except Exception:                # pragma: no cover - the work goes on without it
            return None
        if not person:
            return None
        said = _who(person)
        try:
            # HOW THE USER WRITES TO THEM: their own words kept, the latest last.
            said["how_written"] = [{"at": str(one.get("at") or "")[:16],
                                    "channel": str(one.get("channel") or ""),
                                    "text": str(one.get("text") or "")}
                                   for one in store.writing(person["id"])]
        except Exception:                # pragma: no cover - the work goes on without it
            said["how_written"] = []
        try:
            said["addresses"] = [str(one["handle"]).lower() for one in store.accounts(person["id"])
                                 if str(one.get("platform") or "").lower() == "email"
                                 and "@" in str(one.get("handle") or "")]
        except Exception:                # pragma: no cover - the work goes on without it
            said["addresses"] = []
        return said

    def _me(self) -> Optional[Dict[str, Any]]:
        """WHO THE USER IS, as their own profile has them - read, never made
        here (the contacts make it)."""
        store = getattr(self.app, "connections", None)
        if store is None:
            return None
        try:
            row = self.db.query_one("SELECT id FROM connection_people WHERE is_self = 1 "
                                    "ORDER BY created_at LIMIT 1")
            person = store.get(row["id"]) if row else None
        except Exception:                # pragma: no cover - the work goes on without it
            return None
        said = _who(person) if person else None
        return said if said and (said.get("who") or said.get("names")) else None

    def _thorough(self) -> bool:
        """Whether the thorough way is open (runner.THOROUGH_OPEN)."""
        return bool(getattr(self.app.work, "thorough_open", True))

    def _tries(self, todo: str, besides: str = "",
               removed_too: bool = False) -> List[Dict[str, Any]]:
        """A to-do's tries started and not removed (`removed_too`: those the
        user removed as well), the first first."""
        out = []
        for row in self.db.query("SELECT id FROM work_drafts WHERE source = 'todo' "
                                 "AND status IN (?, ?) AND id != ? ORDER BY created_at",
                                 (STARTED, STARTING, besides)):
            draft = self.get(row["id"])
            options = (draft or {}).get("options") or {}
            if draft is not None and str(options.get("todo") or "") == todo \
                    and (removed_too or not options.get("removed")):
                out.append(draft)
        return out

    def _tries_said(self, todo: str, besides: str = "") -> List[Dict[str, Any]]:
        """THE TRIES BEFORE (the user, 2026-10-31: a new try knows them):
        each started try of the to-do, the first first - what it was asked,
        where it stands, what it reported last, what it handed over and the
        folder its result is in."""
        return self._said_of_tries(self._tries(todo, besides=besides))

    def _project_tries(self, options: Dict[str, Any], besides: str = "") -> List[Dict[str, Any]]:
        """A PROJECT'S TRIES BEFORE (the user, 2026-11-08: "对整个project做，他可以复用每个to do之前
        的部分，也可以复用上个project版本的部分"): for a step, that step's tries; for the whole, the
        whole's earlier versions and every step's tries - started and not removed, the first
        first."""
        project = str(options.get("project") or "")
        if not project:
            return []
        whole, step = bool(options.get("whole")), str(options.get("step") or "")
        out = []
        for row in self.db.query("SELECT id FROM work_drafts WHERE source = 'project' "
                                 "AND status IN (?, ?) AND id != ? ORDER BY created_at",
                                 (STARTED, STARTING, besides)):
            draft = self.get(row["id"])
            mine = (draft or {}).get("options") or {}
            if draft is None or str(mine.get("project") or "") != project or mine.get("removed"):
                continue
            if whole or (not mine.get("whole") and str(mine.get("step") or "") == step):
                out.append(draft)
        return out

    def _said_of_tries(self, drafts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Each try started, as a new one is told it: what it was asked, where it stands, what it
        reported last, what it handed over and the folder its result is in - and, of a project,
        whether it was a step of it or the whole."""
        out: List[Dict[str, Any]] = []
        work = self.app.work
        for draft in drafts:
            if draft.get("status") != STARTED or not draft.get("run_id"):
                continue
            came = self._came_of(str(draft.get("unit_id") or ""), str(draft["run_id"]))
            run = work.run(str(came.get("run") or draft["run_id"])) or {}
            plan, final = run.get("plan") or {}, run.get("final") or {}
            out.append({
                "version": int((draft.get("options") or {}).get("version") or len(out) + 1),
                "asked": asked_of(draft), "state": str(came.get("state") or ""),
                "ended": str(came.get("ended") or run.get("ended_at") or "")[:16],
                "report": str(final.get("answer") or plan.get("last_report") or "")[:TRY_REPORT],
                "folder": str(run.get("folder") or ""),
                "handed": [f"{one.get('kind')}: {one.get('title')}"
                           for one in plan.get("handed") or [] if isinstance(one, dict)][:6],
                **({"part": "the whole project" if (draft.get("options") or {}).get("whole")
                    else "one step of the project"}
                   if draft.get("source") == "project" else {})})
        return out

    def _tries_for(self, draft: Dict[str, Any], besides: str = "") -> List[Dict[str, Any]]:
        """The tries before a piece of work, said: a to-do's, or a project's step's or whole's."""
        options = draft.get("options") or {}
        if draft.get("source") == "project":
            return self._said_of_tries(self._project_tries(options, besides=besides))
        return self._tries_said(str(options.get("todo") or ""), besides=besides)

    def _try_before(self, todo: str, for_work: bool = False) -> Optional[Dict[str, Any]]:
        """The latest try of a to-do - the one a new try is opened from, its
        words and what was handed over - or None. ONE THE USER REMOVED COUNTS
        TOO (the user, 2026-10-31: removing a try does not change what the
        next one opens with): removed is not listed, not unsaid. `for_work`:
        the latest whose project is still there and shown - the one a new try
        is started in."""
        experience = self.app.state_heartbeat.experience
        hidden = experience.removed() if for_work and hasattr(experience, "removed") else {}
        for draft in reversed(self._tries(todo, removed_too=True)):
            unit = str(draft.get("unit_id") or "")
            if not for_work:
                return draft
            if unit and unit not in hidden and experience.unit(unit) is not None:
                return draft
        return None

    def _screen_now(self, window: Dict[str, Any]) -> Dict[str, Any]:
        """The window in front, as the listener last read it off the screen
        - its words, never by signing in (listener/providers/screen.py)."""
        since = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        try:
            rows = self.db.query(
                "SELECT event_type, data FROM raw_events WHERE event_type IN "
                "('screen.text', 'browser.navigation') AND timestamp >= ? "
                "ORDER BY timestamp DESC LIMIT 40", (since,))
        except Exception:
            return {}
        out: Dict[str, Any] = {}
        for row in rows:
            try:
                said = json.loads(row["data"] or "{}")
            except (TypeError, ValueError):
                continue
            if row["event_type"] == "screen.text" and "text" not in out:
                title = str(said.get("window_title") or "")
                if window.get("title") and title and window["title"] not in title:
                    continue
                out.update({"title": window.get("title") or title,
                            "app": window.get("app") or said.get("application") or "",
                            "text": str(said.get("text") or "")[:SCREEN_CHARS]})
            elif row["event_type"] == "browser.navigation" and "url" not in out:
                # The page a browser in front was showing - its address, to
                # be read as anyone could.
                out["url"] = window.get("url") or str(said.get("url") or "")
                out.setdefault("page_title", str(said.get("title") or ""))
        if "text" not in out:
            out.pop("url", None)
        return out

    def _collect_apart(self, draft_id: str) -> None:
        """Collected and drafted in a process of its own - or, where runs go
        in this process, on a thread of its own."""
        work = self.app.work
        if getattr(work, "in_subprocess", False) and getattr(work, "paths", None) is not None:
            from .child import spawn_draft
            try:
                spawn_draft(work, draft_id)
                return
            except Exception:
                log.warning("could not start the draft's process; drafting here",
                            exc_info=True)
        threading.Thread(target=self.collect_and_plan, args=(draft_id,),
                         name=f"draft-{draft_id}", daemon=True).start()

    def get(self, draft_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one("SELECT * FROM work_drafts WHERE id = ?", (draft_id,))
        if row is None:
            return None
        out = dict(row)
        for name, empty in (("folders", []), ("files", []), ("window", {}), ("context", {}),
                            ("plan", {}), ("options", {})):
            try:
                out[name] = json.loads(out.get(name) or "null") or empty
            except (TypeError, ValueError):
                out[name] = empty
        # Each place it works, a folder or a file - as the box shows it.
        out["kinds"] = {one: "file" if Path(one).is_file() else "folder"
                        for one in out.get("folders") or []}
        with self._lock:
            going = draft_id in self._starting
            said = self._started.get(draft_id)
        if out.get("status") == STARTING and not going:
            # Its start went with the application: to be started again.
            out["status"] = READY
            out["error"] = out.get("error") or "it did not start - the application closed"
        if said is not None:
            out["start"] = said
        return out

    def recent(self, most: int = 20) -> List[Dict[str, Any]]:
        return [self.get(row["id"]) for row in self.db.query(
            "SELECT id FROM work_drafts ORDER BY created_at DESC LIMIT ?", (most,))]

    def saved(self, most: int = 50) -> List[Dict[str, Any]]:
        """THE DRAFTS SAVED (`save`), not started nor discarded: the newest
        first, for Work's list of them."""
        out = []
        for row in self.db.query("SELECT id FROM work_drafts WHERE options LIKE ? AND "
                                 "status NOT IN (?, ?, ?) ORDER BY updated_at DESC LIMIT ?",
                                 ("%\"saved\": true%", STARTED, STARTING, CANCELLED, most)):
            draft = self.get(row["id"])
            if draft is not None and (draft.get("options") or {}).get("saved") \
                    and not (draft.get("options") or {}).get("removed"):
                out.append(draft)
        return out

    def save(self, draft_id: str, instruction: Optional[str] = None,
             plan: Optional[Dict[str, Any]] = None, folders: Optional[List[str]] = None,
             options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """SAVE AS DRAFT (the user, 2026-10-26) - in place of Create: what the
        box holds kept as it is - the instruction, where it works, the
        settings and the way chosen, the plan as changed - to be opened again
        from Work's list of drafts and started later. Saved before its
        instruction was taken, the instruction is taken now, and the
        context collected and the plan drafted meanwhile."""
        draft = self.get(draft_id)
        if draft is None:
            return {"error": "no such piece of work"}
        if draft["status"] in (STARTED, STARTING):
            return {"error": "it was started already"}
        kept = dict(draft.get("options") or {})
        kept.update({k: v for k, v in (options or {}).items()
                     if k in ("sound", "unconfined", "way", "see_tries")})
        kept["saved"] = True
        values: Dict[str, Any] = {"options": kept}
        if folders is not None:
            values["folders"] = [str(one) for one in folders if str(one).strip()][:12]
        if isinstance(plan, dict) and draft["status"] in (READY, FAILED) \
                and any(plan.get(part) for part in PLAN_PARTS):
            approved = plan_of(plan, fallback=asked_of(draft) or draft.get("text") or "",
                               thorough=self._thorough())
            values["plan"] = approved
            if approved != plan_of(draft.get("plan") or {}, thorough=self._thorough()):
                values["plan_by"] = "user"
        self._set(draft_id, **values)
        if draft["status"] == WRITING and (str(instruction or "").strip() or draft.get("text")
                                           or draft.get("folders") or draft.get("files")
                                           or draft.get("unit_id")):
            self.compose(draft_id, instruction or "")
        return {"draft": self.get(draft_id)}

    def of_todos(self) -> Dict[str, Dict[str, Any]]:
        """WHAT CAME OF A TO-DO HANDED OVER, by its id - EVERY TIME IT WAS
        (the user, 2026-10-26): a to-do may be handed over again and again,
        each a VERSION of its own (V1, V2, ...), the oldest first:
        {"versions": [...]}. Each is being written or drafted (`drafting`,
        its draft to open again), at work (`ongoing`), finished (`finished`,
        with what it handed over, else the deck's first deliverable there,
        else its folder) or ended otherwise (`stopped`). A cancelled one is
        as if it had never been."""
        out: Dict[str, Dict[str, Any]] = {}
        # THE NEXT VERSION, removed ones counted (the user, 2026-11-01: V4 and
        # V5 listed, V1-V3 removed - "Delegate work" said V3; the next is V6).
        most: Dict[str, int] = {}
        for row in self.db.query("SELECT id, status, options, unit_id, run_id FROM work_drafts "
                                 "WHERE source = 'todo' ORDER BY created_at"):
            try:
                options = json.loads(row["options"] or "{}") or {}
            except (TypeError, ValueError):
                options = {}
            todo = str(options.get("todo") or "")
            if todo and row["status"] in (STARTED, STARTING):
                most[todo] = max(most.get(todo, 0), int(options.get("version") or 1))
            if not todo or row["status"] == CANCELLED or options.get("removed"):
                continue
            versions = out.setdefault(todo, {"versions": []})["versions"]
            said = {"draft": row["id"],
                    "version": int(options.get("version") or 0) or len(versions) + 1,
                    "state": "ongoing" if row["status"] == STARTING
                    or options.get("start_when_ready") else "drafting"}
            if row["status"] == STARTED and row["unit_id"]:
                said.update(self._came_of(row["unit_id"], str(row["run_id"] or "")))
            elif (options.get("start_when_ready") or {}).get("held"):
                said["held"] = options["start_when_ready"]["held"]
            if options.get("start_failed") and row["status"] != STARTED:
                said.update(state="drafting",
                            failed=str(options["start_failed"].get("error") or ""))
            versions.append(said)
        for todo, came in out.items():
            came["next"] = most.get(todo, 0) + 1
        return out

    def handed_from_a_whole_project(self, unit_id: str) -> bool:
        """Whether a work was a whole project handed over (its note told apart, 2026-11-08)."""
        for row in self.db.query("SELECT options FROM work_drafts WHERE unit_id = ? AND "
                                 "source = 'project'", (str(unit_id or ""),)):
            try:
                if (json.loads(row["options"] or "{}") or {}).get("whole"):
                    return True
            except (TypeError, ValueError):
                continue
        return False

    def of_projects(self) -> Dict[str, Dict[str, Any]]:
        """WHAT CAME OF A PROJECT'S STEPS HANDED OVER (the user, 2026-11-08), as `of_todos` says it
        of a to-do: by project, {"whole": [versions], "steps": {step id: [versions]}}, the oldest
        first - each being drafted, at work, finished with what it made, or stopped."""
        out: Dict[str, Dict[str, Any]] = {}
        for row in self.db.query("SELECT id, status, options, unit_id, run_id FROM work_drafts "
                                 "WHERE source = 'project' ORDER BY created_at"):
            try:
                options = json.loads(row["options"] or "{}") or {}
            except (TypeError, ValueError):
                options = {}
            project = str(options.get("project") or "")
            if not project or row["status"] == CANCELLED or options.get("removed"):
                continue
            mine = out.setdefault(project, {"whole": [], "steps": {}})
            versions = mine["whole"] if options.get("whole") else mine["steps"].setdefault(
                str(options.get("step") or ""), [])
            said = {"draft": row["id"], "version": len(versions) + 1,
                    "state": "ongoing" if row["status"] == STARTING
                    or options.get("start_when_ready") else "drafting"}
            if row["status"] == STARTED and row["unit_id"]:
                said.update(self._came_of(row["unit_id"], str(row["run_id"] or "")))
            elif (options.get("start_when_ready") or {}).get("held"):
                said["held"] = options["start_when_ready"]["held"]
            if options.get("start_failed") and row["status"] != STARTED:
                said.update(state="drafting",
                            failed=str(options["start_failed"].get("error") or ""))
            versions.append(said)
        return out

    def assigned(self, most: int = 60) -> List[Dict[str, Any]]:
        """WORK ASSIGNED - EVERY PIECE OF WORK DELEGATED NOT FROM A TO-DO (the
        user, 2026-11-02: "overview在to do左边加个panel, 是works assigned ...
        delegate work (不是通过to do的) 都会放在这里"): right-clicked in a
        program or a page, selected text, Explorer, Add work - each started,
        or being started, the newest first, with where it stands as a to-do's
        work says it (`of_todos`' versions). One removed is not listed again. EVERY ONE, A TO-DO'S
        TOO (the user, 2026-11-08: "to do或者对task整个的delegate work, 可以在work panel看到"):
        a to-do's work is under the to-do as well."""
        out: List[Dict[str, Any]] = []
        for row in self.db.query(
                "SELECT * FROM work_drafts WHERE status IN (?, ?, ?, ?, ?, ?) "
                "ORDER BY created_at DESC LIMIT ?",
                (STARTED, STARTING, COLLECTING, DRAFTING, READY, FAILED, int(most) * 3)):
            draft = self.get(row["id"])
            if not draft:
                continue
            options = draft.get("options") or {}
            if options.get("removed"):
                continue
            # By what was asked of it (the row): a start the application's
            # closing cut off is listed too, its plan to start again.
            failed = options.get("start_failed") or {}
            if row["status"] in (COLLECTING, DRAFTING, READY, FAILED) \
                    and not options.get("start_when_ready") and not failed:
                if not options.get("saved"):
                    continue                 # being written in the box: not one of the list yet
            said: Dict[str, Any] = {"draft": draft["id"], "version": 1,
                                    "state": "ongoing" if draft["status"] == STARTING
                                    or options.get("start_when_ready") else "drafting"}
            if draft["status"] == READY and draft.get("error"):
                said["held"] = draft["error"]
            if failed and draft["status"] != STARTED:
                # IT DID NOT START - said, never left silent: its plan to
                # open, change and start again.
                said.update(state="drafting", failed=str(failed.get("error") or ""))
            if draft["status"] == STARTED and draft.get("unit_id"):
                said.update(self._came_of(draft["unit_id"], str(draft.get("run_id") or "")))
            elif (options.get("start_when_ready") or {}).get("held"):
                said["held"] = options["start_when_ready"]["held"]
            out.append({"id": draft["id"], "text": self._title_of(draft),
                        "detail": self._from_where(draft), "at": draft["created_at"],
                        "source": draft["source"], "work": {"versions": [said], "next": 2}})
            if len(out) >= most:
                break
        return out

    @staticmethod
    def _title_of(draft: Dict[str, Any]) -> str:
        """What a piece of work is called: its plan's title, else what the
        user asked, else what they handed over."""
        options = draft.get("options") or {}
        for one in ((draft.get("plan") or {}).get("title"), options.get("title"),
                    asked_of(draft), draft.get("text")):
            line = " ".join(str(one or "").split())
            if line:
                return line[:160]
        return "Delegated work"

    @staticmethod
    def _from_where(draft: Dict[str, Any]) -> str:
        """Where it was handed over from, in a few words."""
        window = draft.get("window") or {}
        picked = window.get("picked") or {}
        if picked:
            where = picked.get("title") or picked.get("file") or picked.get("url") or ""
            app = picked.get("app") or ""
            return " - ".join(part for part in (app, str(where)[:120]) if part)
        files = list(draft.get("files") or []) + [one for one in draft.get("folders") or []
                                                  if one not in (draft.get("files") or [])]
        if files:
            names = [Path(one).name or one for one in files[:3]]
            return ", ".join(names) + (f" +{len(files) - 3}" if len(files) > 3 else "")
        if window.get("title"):
            return " - ".join(part for part in (window.get("app") or "", window["title"]) if part)
        return ""

    def _next_version(self, todo: str, besides: str = "") -> int:
        """The version a to-do's work started now is: one more than the
        latest of it started before (`besides`: the one being started)."""
        most = 0
        for row in self.db.query("SELECT options FROM work_drafts WHERE source = 'todo' "
                                 "AND status IN (?, ?) AND id != ?",
                                 (STARTED, STARTING, besides)):
            try:
                options = json.loads(row["options"] or "{}") or {}
            except (TypeError, ValueError):
                continue
            if str(options.get("todo") or "") == todo:
                most = max(most, int(options.get("version") or 1))
        return most + 1

    def _came_of(self, unit_id: str, run_id: str = "") -> Dict[str, Any]:
        """Where a try stands - several tries share a work: the latest run
        of its own line (its tree: carried on thoroughly, a run of its own
        in the same try) - else the work's latest."""
        from .runner import DONE, tree_of
        work = self.app.work
        first = work.run(run_id) if run_id else None
        run = None
        if first is not None:
            line = [row for row in (work.run(one["id"]) for one in self.db.query(
                "SELECT id FROM work_runs WHERE unit_id = ? ORDER BY started_at DESC LIMIT 30",
                (unit_id,))) if row is not None and tree_of(row) == tree_of(first)]
            run = line[0] if line else first
            live = work._running.get(run["id"]) if hasattr(work, "_running") else None
            run["alive"] = bool(live is not None and live.alive())
            if run["alive"]:
                run["status"] = live.status
        else:
            run = work.latest_of(unit_id)
        if run is None:
            return {"state": "ongoing"}
        said: Dict[str, Any] = {"run": run["id"], "unit": unit_id,
                                "status": str(run.get("status") or "")}
        if run["alive"]:
            return dict(said, state="ongoing")
        if run.get("status") != DONE:
            # Carried on from the to-do, as in Work (the user, 2026-10-26):
            # one that did not stop cleanly goes back to its last save point.
            from .runner import CONTINUABLE, GOES_BACK, _resumes_from
            from .versions import VersionStore
            said.update(continuable=said["status"] in CONTINUABLE,
                        goes_back=said["status"] in GOES_BACK)
            if said["goes_back"]:
                try:
                    run_dir = work.run_dir_of(run)
                    index = VersionStore(run_dir).index()
                    head = next((one for one in index["versions"]
                                 if one["id"] == index["head"]), None)
                    if head:
                        said["back_to"] = _resumes_from(run_dir, head).get("label", "")
                except Exception:          # the button says less, not wrong
                    pass
            return dict(said, state="stopped", **self._made_by(run, unit_id))
        said["ended"] = str(run.get("ended_at") or "")
        return dict(said, state="finished", **self._made_by(run, unit_id))

    def _made_by(self, run: Dict[str, Any], unit_id: str) -> Dict[str, Any]:
        """WHAT THERE IS TO OPEN (the user, 2026-11-06: "必须必须有open deliverable"), stopped or
        finished: there is no Work panel to go and look in. What it handed to the user first
        (handed.py) - opened as it is meant to be: an email in their mail, an event in their
        calendar - else the deck's deliverable, else its folder. Only a folder that has gone
        leaves nothing."""
        work = self.app.work
        items = (run.get("plan") or {}).get("handed") or []
        if items:
            # WHAT IT IS, before it is opened: each thing handed over - its
            # kind and what it is called, a file's or folder's name.
            return {"handed": str(items[0].get("title") or items[0].get("kind") or ""),
                    "items": [{"n": n, "kind": str(one.get("kind") or ""),
                               "title": str(one.get("title") or ""),
                               "name": Path(str(one.get("path"))).name if one.get("path") else ""}
                              for n, one in enumerate(items[:8])]}
        made = [str(one.get("location") or "") for one in
                ((run.get("plan") or {}).get("deck") or {}).get("deliverables") or []
                if isinstance(one, dict)]
        output = ((self.app.state_heartbeat.experience.store.workspace(unit_id) or {})
                  .get("summary") or {}).get("output_folder") or ""
        if int((run.get("plan") or {}).get("attempt") or 0) > 1:
            output = ""          # a later try's result is in its own folder
        for one in made + [output, str(run.get("folder") or "")]:
            found = work.readable_by(run["id"], one) if one else None
            if found is not None:
                return {"result": one, "result_kind": "folder" if found.is_dir() else "file",
                        "result_name": found.name}
        return {"result": ""}

    def _set(self, draft_id: str, **values: Any) -> None:
        sets = ", ".join(f"{name} = ?" for name in values) + ", updated_at = ?"
        params = [json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list))
                  else value for value in values.values()]
        self.db.execute(f"UPDATE work_drafts SET {sets} WHERE id = ?",
                        (*params, _now(), draft_id))

    # -- collecting and drafting (in the draft's own process) -----------------------------------
    def collect_and_plan(self, draft_id: str) -> Optional[Dict[str, Any]]:
        draft = self.get(draft_id)
        if draft is None or draft["status"] not in (COLLECTING, FAILED):
            return draft
        try:
            # A try of work handed over before: its context, not collected again.
            # A TRY OF WORK HANDED OVER BEFORE: what was handed over, as it was
            # collected - its window, its page, its folders and files - and
            # the user's records searched again, for what happened since.
            before = self.get(str((draft.get("options") or {}).get("context_from") or ""))
            kept = {k: v for k, v in ((before or {}).get("context") or {}).items()
                    if k not in ("query", "found", "summary", "tries", "people", "me")}
            context = self._found(draft, kept) if kept else self.collect(draft)
            if before is not None or draft.get("source") == "project":
                # What the tries before made, for the plan to start from - a project's step's,
                # or the whole's and its steps' (2026-11-08).
                context["tries"] = self._tries_for(draft, besides=draft_id)
                if not context["tries"]:
                    context.pop("tries", None)
            self._set(draft_id, status=DRAFTING, context=context)
            plan = self.plan(draft, context)
            # The user's own title, where they gave one.
            given = str((draft.get("options") or {}).get("title") or "").strip()
            if given:
                plan["title"] = given
            self._set(draft_id, status=READY, plan=plan, plan_by="agent", error="")
        except Exception as exc:
            log.exception("could not draft the plan of %s", draft_id)
            self._set(draft_id, status=FAILED, error=f"could not draft the plan: {exc}"[:500])
        return self.get(draft_id)

    def collect(self, draft: Dict[str, Any]) -> Dict[str, Any]:
        """What bears on the work, gathered: the window and its page, the
        folders, and what is FOUND of the user's own records by its words
        (context_search.py) - relevant, from any time, the recent first."""
        context: Dict[str, Any] = {}
        # WHAT WAS IN FRONT WHEN IT WAS DELEGATED DIRECTLY (the user, 2026-11-04), said
        # to be no more than that: the work may have nothing to do with it.
        front = (draft.get("options") or {}).get("front") or {}
        if front:
            context["front_screen"] = dict(front, note=FRONT_NOTE)
        # WHAT IT IS PART OF (heartbeat/states/item_links.py), a to-do's by the to-do.
        links = getattr(self.app, "item_links", None)
        todo = str((draft.get("options") or {}).get("todo") or "")
        note = (links.note("todo", todo) if todo else links.note("assigned", draft.get("id") or "")) \
            if links is not None else ""
        if note:
            context["part_of"] = note
        window = draft.get("window") or {}
        if window:
            context["window"] = {k: window.get(k) for k in ("title", "app", "url")
                                 if window.get(k)}
            if window.get("text"):
                context["window"]["text"] = str(window["text"])[:SCREEN_CHARS]
            # WHAT WAS RIGHT-CLICKED (picked.py): as it was clicked.
            if window.get("picked"):
                context["picked"] = {k: v for k, v in window["picked"].items()
                                     if k not in ("page", "around")}
        url = str(window.get("url") or "")
        if url.lower().startswith(("http://", "https://")):
            context["page"] = self._page(url)
        # A DOCUMENT OF THEIRS OPEN WHERE THEY RIGHT-CLICKED (DOCUMENT_CHARS).
        local = local_path(str((window.get("picked") or {}).get("url") or url))
        if local is not None:
            context["document"] = read_document(local, DOCUMENT_CHARS)
        context["folders"] = [self._folder(Path(one)) for one in draft.get("folders") or []]
        context["files"] = [self._file(Path(one)) for one in draft.get("files") or []][:6]
        unit = self._unit(draft)
        if unit is not None:
            context["work"] = {"name": unit.get("name"), "kind": unit.get("kind"),
                               "about": str(unit.get("description") or unit.get("why")
                                            or "")[:1500]}
        return self._found(draft, context)

    def _found(self, draft: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """FOUND BY ITS WORDS: what was handed over, in English search words
        (one model call), matched against every state, task and stage - for
        each try of the same work again (the user, 2026-10-31: so it sees
        what happened since), by its own instruction."""
        window = draft.get("window") or {}
        from .context_search import Index, english_words, records
        handed = {"text": "\n".join(one for one in (asked_of(draft), draft.get("text") or "")
                                    if one),
                  "unit": (context.get("work") or {}).get("name"),
                  "folders": [Path(one).name for one in draft.get("folders") or []],
                  "files": [Path(one).name for one in draft.get("files") or []],
                  "page": (context.get("page") or {}).get("title") or window.get("title") or ""}
        query = english_words(self.app.models, handed)
        self._index = Index(records(self.db))
        context["query"] = query
        context["found"] = [{k: one.get(k) for k in ("kind", "id", "title", "detail", "at",
                                                      "relevance", "recency")}
                            for one in self._index.search(query["keywords"])]
        try:
            context.update(self._more_found(list(query.get("keywords") or []), draft))
        except Exception:                        # the work goes on with what was found
            log.warning("the other sources could not be read for %s", draft.get("id"), exc_info=True)
        try:
            lessons = self._lessons_for(draft, query)
            if lessons:
                context["lessons"] = lessons
        except Exception:                        # the work goes on without them
            log.warning("the lessons could not be read for %s", draft.get("id"), exc_info=True)
        return context

    def _lessons_for(self, draft: Dict[str, Any], query: Dict[str, Any]) -> str:
        """THE USER'S OWN RULES THAT BEAR ON IT (the user, 2026-11-08: "分派工作时，也把这类 lesson 里和
        工作相关的规则 ... 给它"): the lessons kept from what they did and said - of how they work,
        and general - found by what the work is and by each of its steps, every way of working
        given them (quick too)."""
        retriever = getattr(self.app, "lesson_retriever", None)
        if retriever is None:
            return ""
        lines = [line.strip() for line in str(draft.get("text") or "").splitlines()]
        steps = [re.sub(r"^\s*(?:\d+[.)]|[-*•])\s*", "", line) for line in lines
                 if re.match(r"^\s*(?:\d+[.)]|[-*•])\s+\S", line)][:LESSON_STEPS]
        parts = [str(query.get("sentence") or "") or asked_of(draft) or str(draft.get("text") or "")[:600],
                 *steps]
        said = retriever.in_parts(LESSON_KINDS, parts, rules=LESSON_RULES, examples=LESSON_EXAMPLES,
                                  rewrite_task="lessons", site="work draft")
        said = str(said or "").strip()
        if said.lower().startswith("none"):
            said = ""
        # ITS SUBJECT BY NAME, ALWAYS (measured 2026-11-08 on the user's own: by meaning alone, two of
        # four rules naming OpenArxiv were left out for rules about another project's releases):
        # every rule kept that names what the work is about, first.
        strong, _rest = topic_words(list(query.get("keywords") or []))
        named = [row for row in self._rows("SELECT id, statement, state, confidence FROM lesson_rules "
                                           "WHERE state != 'retired' AND kind IN ('work', 'general') "
                                           "ORDER BY confidence DESC")
                 if strong and on_topic(str(row["statement"] or ""), strong, set())
                 and str(row["id"]) not in said][:LESSON_NAMED]
        if named:
            said = "\n".join(
                ["Rules about this very subject, by name:"]
                + [f"- [{row['id']}] {row['statement']} ({'a candidate' if row['state'] == 'candidate' else 'in force'}; "
                   f"{round(float(row['confidence'] or 0) * 100)}% sure)" for row in named]
                + ([""] + ["Rules like it:", said] if said else []))
        return said[:LESSON_CHARS]

    def _more_found(self, keywords: List[str], draft: Dict[str, Any]) -> Dict[str, Any]:
        """BY THE SAME WORDS, WHAT ELSE BEARS ON IT (the user, 2026-11-08: "收集时加上这几个来源"):
        what the user said of it in the chat; what was learned of their work like it (cases and
        what they worked on, from their sessions); other work on the same subject and its folder;
        their mail threads on it, by title; their calendar and the agent's reminders on it."""
        strong, rest = topic_words(keywords)
        if not (strong or rest):
            return {}

        def about(*texts: Any) -> bool:
            return on_topic(" ".join(str(one or "") for one in texts), strong, rest)
        out: Dict[str, Any] = {}
        said = [{"at": str(row["created_at"] or "")[:16], "who": "user" if row["role"] == "user"
                 else "agent", "said": str(row["content"] or "")[:600]}
                for row in self.db.query("SELECT created_at, role, content FROM messages WHERE "
                                         "role IN ('user', 'assistant') ORDER BY created_at DESC "
                                         "LIMIT 3000")
                if about(row["content"])]
        if said:
            # THEIR OWN WORDS FIRST: what the user said, then the agent's answers.
            said.sort(key=lambda one: (one["who"] != "user", one["at"]))
            out["said_in_chat"] = sorted(said[:MOST_SAID], key=lambda one: one["at"])
        learned: List[Dict[str, Any]] = []
        for row in self._rows("SELECT title, looks_like, how_made, updated_at FROM learn_cases "
                              "WHERE alias_of IS NULL OR alias_of = '' ORDER BY updated_at DESC"):
            if about(row["title"], row["looks_like"]):
                learned.append({"what": "a piece of their work", "title": str(row["title"] or ""),
                                "about": str(row["looks_like"] or row["how_made"] or "")[:400]})
        for row in self._rows("SELECT title, summary, updated_at FROM learn_units "
                              "ORDER BY updated_at DESC"):
            if about(row["title"], row["summary"]):
                learned.append({"what": "what they worked on", "title": str(row["title"] or ""),
                                "about": str(row["summary"] or "")[:400]})
        if learned:
            out["learned"] = learned[:MOST_LEARNED]
        tried = {str(one.get("folder") or "") for one in self._tries_for(draft, besides=draft.get("id")
                                                                          or "")}
        works = []
        for row in self._rows("SELECT unit_name, folder, final, ended_at, status FROM work_runs "
                              "WHERE folder != '' ORDER BY started_at DESC LIMIT 400"):
            if str(row["folder"]) in tried or not about(row["unit_name"]):
                continue
            try:
                final = json.loads(row["final"] or "{}") if row["final"] else {}
            except ValueError:
                final = {}
            if any(one["folder"] == str(row["folder"]) for one in works):
                continue
            works.append({"work": str(row["unit_name"] or ""), "folder": str(row["folder"]),
                          "ended": str(row["ended_at"] or "")[:16], "state": str(row["status"] or ""),
                          "report": str((final or {}).get("answer") or "")[:500]})
        if works:
            out["other_works"] = works[:MOST_OTHER_WORKS]
        mail = [{"title": str(row["title"] or ""), "url": str(row["url"] or ""),
                 "seen": str(row["last_seen"] or "")[:16]}
                for row in self._rows("SELECT url, title, people, last_seen FROM work_mail_pages "
                                      "ORDER BY last_seen DESC LIMIT 400")
                if about(row["title"], row["people"])]
        if mail:
            out["mail"] = mail[:MOST_MAIL]
        calendar = [{"title": str(row["title"] or ""), "when": str(row["scheduled_at"] or "")[:16],
                     "state": str(row["status"] or ""), "about": str(row["description"] or "")[:300]}
                    for row in self._rows("SELECT title, description, scheduled_at, status FROM "
                                          "scheduled_tasks ORDER BY scheduled_at DESC LIMIT 600")
                    if about(row["title"], row["description"])]
        if calendar:
            out["calendar"] = calendar[:MOST_CALENDAR]
        reminders = [{"title": str(row["title"] or ""), "when": str(row["created_at"] or "")[:16],
                      "about": str(row["message"] or "")[:300]}
                     for row in self._rows("SELECT title, message, created_at FROM notifications "
                                           "ORDER BY created_at DESC LIMIT 600")
                     if about(row["title"], row["message"])]
        if reminders:
            out["reminders"] = reminders[:MOST_REMINDERS]
        return out

    def _rows(self, sql: str) -> List[Any]:
        """Rows of a table that may not be there (a source switched off, never made): none then."""
        try:
            return list(self.db.query(sql))
        except Exception:
            return []

    @staticmethod
    def _page(url: str) -> Dict[str, Any]:
        """The public page, as anyone could read it - never signed in."""
        try:
            from .tools import fetch
            said = fetch(url)
        except Exception as exc:
            return {"url": url, "error": str(exc)[:200]}
        if isinstance(said, dict):
            return {"url": url, "title": str(said.get("title") or "")[:300],
                    "text": str(said.get("text") or "")[:PAGE_CHARS],
                    **({"error": str(said["error"])[:200]} if said.get("error") else {})}
        return {"url": url, "text": str(said)[:PAGE_CHARS]}

    @staticmethod
    def _folder(folder: Path) -> Dict[str, Any]:
        out: Dict[str, Any] = {"path": str(folder)}
        try:
            entries = sorted(folder.iterdir(), key=lambda one: (not one.is_dir(), one.name))
        except OSError as exc:
            out["error"] = str(exc)[:200]
            return out
        out["entries"] = [one.name + ("/" if one.is_dir() else "")
                          for one in entries[:MOST_ENTRIES]]
        out["more"] = max(0, len(entries) - MOST_ENTRIES)
        for name in ("README.md", "README.txt", "README", "readme.md"):
            readme = folder / name
            if readme.is_file():
                try:
                    out["readme"] = readme.read_text(encoding="utf-8",
                                                     errors="replace")[:README_CHARS]
                except OSError:
                    pass
                break
        return out

    @staticmethod
    def _file(path: Path) -> Dict[str, Any]:
        out: Dict[str, Any] = {"path": str(path)}
        try:
            out["size"] = path.stat().st_size
            if path.suffix.lower() in TEXT_SUFFIXES:
                out["start"] = path.read_text(encoding="utf-8",
                                              errors="replace")[:README_CHARS]
                return out
        except OSError as exc:
            out["error"] = str(exc)[:200]
            return out
        # A PDF, Word, PowerPoint, Excel or a picture: its words (DOCUMENT_CHARS).
        read = read_document(path, FILE_CHARS)
        if read.get("text"):
            out["start"] = read["text"]
            out.update({key: read[key] for key in ("pages", "chars", "cut") if key in read})
        elif read.get("error"):
            out["error"] = read["error"]
        return out

    def _unit(self, draft: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not draft.get("unit_id"):
            return None
        try:
            return self.app.state_heartbeat.experience.unit(draft["unit_id"])
        except Exception:
            return None

    @staticmethod
    def _pictures(draft: Dict[str, Any]) -> List[Dict[str, str]]:
        """The pictures handed over, and the one of the screen, as a model sees them."""
        from ...runtime.documents import PICTURES
        from ..front_picture import block
        paths = [one for one in draft.get("files") or []
                 if Path(one).suffix.lower() in PICTURES][:MOST_PLAN_PICTURES]
        front = str(((draft.get("options") or {}).get("front") or {}).get("picture") or "")
        return [said for said in map(block, paths + ([front] if front else [])) if said]

    def plan(self, draft: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """The HIGH-LEVEL plan, in the user's language - one call, from what
        was collected and found: quick, for the user to check. The detailed
        plan is the run's, which looks further with its own tools."""
        from ...models.base import ModelRequest
        handed = {"their instruction": asked_of(draft) or "(none - go by what they handed over)",
                  "what they handed over": draft.get("text") or "(nothing typed or selected)",
                  # RIGHT-CLICKED (picked.py): the selected text above all.
                  **({"what they right-clicked": "\n".join(picked_told(
                      (draft.get("window") or {}).get("picked") or {}))}
                     if (draft.get("window") or {}).get("picked") else {}),
                  "folders they gave": draft.get("folders") or [],
                  "files they gave": draft.get("files") or [],
                  # A project's step or whole: where the project's files are, to read only.
                  **({"the project's own folders, to read, never to write": read}
                     if (read := [str(one) for one in
                                  (draft.get("options") or {}).get("read_from") or []]) else {}),
                  "context": context}
        content: Any = json.dumps(handed, ensure_ascii=False, indent=1)[:60000]
        pictures = self._pictures(draft)
        if pictures:
            # SEEN, NOT ONLY READ: a model that sees none has them taken out
            # (models/vision.py); their words are in what was collected.
            content = [{"type": "text", "text": content}, *[
                {"type": "image", "source": {"type": "base64", "media_type": one["media_type"],
                                             "data": one["data"]}} for one in pictures]]
        response = self.app.models.invoke(ModelRequest(
            messages=[{"role": "user", "content": content}],
            system=PLAN_PROMPT if self._thorough() else PLAN_PROMPT_QUICK,
            max_tokens=2000, task="work_draft",
            language=in_display("every field of the plan and the context summary - names of "
                                "files, people, products and code keep their own wording")),
            role="primary")
        from ...heartbeat.states.heartbeat import _as_json
        from ...models import answers
        read = answers.read(response, parse=_as_json)
        said = read.data if read.ok else {}
        if not read.ok:
            log.warning("the plan of %s could not be written: %s", draft.get("id"), read.why_not)
        context["summary"] = str(said.get("context_summary") or "")[:1500]
        # WHO IT IS FOR, as the plan names them: what passed between them
        # and the user, found in the records - of this try, as it stands now.
        named = [" ".join(str(one).split())[:160] for one in said.get("people") or []
                 if isinstance(one, str) and str(one).strip()][:MOST_PEOPLE]
        # BY DEFAULT TOO (the user, 2026-10-31): every contact what was handed
        # over or asked names, whether the plan named them or not.
        for one in self._contacts_named(draft):
            if len(named) >= MOST_PEOPLE:
                break
            if not any(_name_and_addresses(given)[0].lower() == one.lower() for given in named):
                named.append(one)
        topic = list((context.get("query") or {}).get("keywords") or [])
        people = [self._person(*_name_and_addresses(one), topic=topic) for one in named]
        context["people"] = [one for one in people if one.get("exchanges") or one.get("contact")]
        if context["people"]:
            context["me"] = self._me()
        self._set(draft["id"], context=context)
        plan = plan_of(said, fallback=asked_of(draft) or draft.get("text") or "",
                       thorough=self._thorough())
        if not read.ok:
            # SAID, NOT AN EMPTY PLAN THAT LOOKS REAL (2026-11-07): an answer cut off or
            # unreadable gave a plan of nothing under a made-up title. What is shown at approval
            # says the plan could not be written, and why.
            from ...common.interface_words import shown
            plan["understanding"] = shown(PLAN_CUT if read.cut else PLAN_UNREAD,
                                          getattr(self.app, "config", None))
            plan["not_written"] = read.why_not
        return plan

    # -- the user's side ------------------------------------------------------------------------
    def start_apart(self, draft_id: str, plan: Optional[Dict[str, Any]] = None,
                    folders: Optional[List[str]] = None,
                    options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """START, ANSWERED AT ONCE: setting the work going - its project
        made, its folders granted, room found, its process started - took
        seconds, the box held open meanwhile. Now it is done apart: the
        draft is "starting" until it is "started", or back where it was with
        why it did not start; how it went is in the draft (`get`, "start")."""
        draft = self.get(draft_id)
        if draft is None:
            return {"error": "no such piece of work"}
        if draft["status"] in (STARTED, STARTING):
            return {"error": "it was started already", "run": draft["run_id"]}
        if draft["status"] == WRITING:
            return {"error": "say what you want done first"}
        if draft["status"] in (COLLECTING, DRAFTING):
            # STARTED BEFORE ITS PLAN IS DRAFTED (the user, 2026-10-26): it
            # starts once it is, with these settings - nothing more to approve.
            way = str((plan or {}).get("way") or "auto")
            kept = dict(draft.get("options") or {},
                        **{k: v for k, v in (options or {}).items()
                           if k in ("sound", "unconfined", "see_tries")},
                        start_when_ready={"way": way, "at": _now(),
                                          # where it works, as the box said it
                                          **({"folders": [str(one) for one in folders]}
                                             if folders is not None else {})})
            self._set(draft_id, options=kept, error="")
            self._wait_then_start(draft_id)
            return {"starting": True, "draft": draft_id, "when_ready": True}
        before = draft["status"]
        with self._lock:
            self._starting.add(draft_id)
            self._started.pop(draft_id, None)
        self._set(draft_id, status=STARTING, error="")

        def go() -> None:
            try:
                said = self.start(draft_id, plan=plan, folders=folders, options=options)
            except Exception as exc:                 # pragma: no cover - said, not raised
                log.exception("the work %s could not be started", draft_id)
                said = {"error": f"{type(exc).__name__}: {exc}"[:600]}
            if said.get("error"):
                self._set(draft_id, status=before, error=str(said["error"])[:600])
                self._did_not_start(draft_id, str(said["error"]))
            else:
                self._started_after_all(draft_id)
            with self._lock:
                self._started[draft_id] = {k: v for k, v in said.items()
                                           if isinstance(v, (str, int, float, bool, type(None)))}
                self._starting.discard(draft_id)

        threading.Thread(target=go, name=f"work-start-{draft_id}", daemon=True).start()
        return {"starting": True, "draft": draft_id}

    def _did_not_start(self, draft_id: str, why: str) -> None:
        """A START THAT FAILED, NEVER SILENTLY (the user, 2026-11-02: the box
        closes at Start - "委托启动失败可能悄悄发生"): kept on the draft, so
        Work assigned lists it - "did not start", its plan to open and start
        again - and said to the user in a notification."""
        draft = self.get(draft_id) or {}
        options = dict(draft.get("options") or {},
                       start_failed={"error": str(why)[:600], "at": _now()})
        self._set(draft_id, options=options)
        notifications = getattr(self.app, "notifications", None)
        if notifications is None:
            return
        try:
            notifications.notify("Delegated work did not start",
                                 f"{self._title_of(draft)}: {str(why)[:300]}",
                                 kind="warning", dedupe_key=f"work-start-failed-{draft_id}",
                                 action_hint="Open it under Work assigned on the Overview, "
                                             "change it, and start it again.")
        except Exception:                        # pragma: no cover - listed anyway
            log.debug("the failed start was not notified", exc_info=True)

    def _started_after_all(self, draft_id: str) -> None:
        """Started now: a failed start before is no longer said."""
        draft = self.get(draft_id) or {}
        if (draft.get("options") or {}).get("start_failed"):
            self._set(draft_id, options={k: v for k, v in draft["options"].items()
                                         if k != "start_failed"})

    def _wait_then_start(self, draft_id: str) -> None:
        with self._lock:
            if draft_id in self._waiting:
                return
            self._waiting.add(draft_id)
        threading.Thread(target=self._when_ready, args=(draft_id,),
                         name=f"work-start-when-ready-{draft_id}", daemon=True).start()

    def _when_ready(self, draft_id: str, every: float = 1.0,
                    most: float = 30 * 60.0) -> None:
        """A draft started before its plan was drafted: once it is - or
        could not be, and is to be written from what was handed over - it
        is started with the plan as drafted, the way chosen (or the plan's
        own, "auto") and the settings given."""
        try:
            began = time.monotonic()
            while time.monotonic() - began < most:
                draft = self.get(draft_id)
                if draft is None or draft["status"] in (CANCELLED, STARTED, STARTING):
                    return
                wanted = (draft.get("options") or {}).get("start_when_ready")
                if not wanted:
                    return
                if draft["status"] in (READY, FAILED):
                    folders = wanted.get("folders", draft.get("folders")) or []
                    held = self.app.work.folder_in_use(folders) \
                        if hasattr(self.app.work, "folder_in_use") else None
                    if held:
                        # STARTED BY ITSELF: never into a folder other work is
                        # in - it waits for the folder, and says so.
                        said = f"waiting for the folder {held['folder']}: \"{held['by']}\" " \
                               "is working in it"
                        if wanted.get("held") != said:
                            self._set(draft_id, options=dict(
                                draft.get("options") or {},
                                start_when_ready=dict(wanted, held=said)))
                        began = time.monotonic()
                        time.sleep(every)
                        continue
                    options = {k: v for k, v in (draft.get("options") or {}).items()
                               if k != "start_when_ready"}
                    self._set(draft_id, options=options)
                    plan = dict(draft.get("plan") or {})
                    if wanted.get("way") in WAYS:
                        plan["way"] = wanted["way"]
                    self.start_apart(draft_id, plan=plan,
                                     folders=wanted.get("folders", draft.get("folders")),
                                     options={"plan_unseen": True})
                    return
                time.sleep(every)
            why = "its plan was not drafted in time - start it again"
            draft = self.get(draft_id) or {}
            self._set(draft_id, error=why, options={
                k: v for k, v in (draft.get("options") or {}).items() if k != "start_when_ready"})
            self._did_not_start(draft_id, why)
        finally:
            with self._lock:
                self._waiting.discard(draft_id)

    def _resume_waiting(self) -> None:
        """Drafts started before their plan was drafted, when the application
        closed: waited for again."""
        try:
            rows = self.db.query("SELECT id FROM work_drafts WHERE options LIKE ? AND "
                                 "status IN (?, ?, ?, ?)",
                                 ("%start_when_ready%", COLLECTING, DRAFTING, READY, FAILED))
        except Exception:                        # pragma: no cover - a database before drafts
            return
        for row in rows:
            self._wait_then_start(row["id"])

    def start(self, draft_id: str, plan: Optional[Dict[str, Any]] = None,
              folders: Optional[List[str]] = None,
              options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Started, with the plan as the user left it: on its project or task,
        or - handed over - as a project of its own under Delegated work."""
        draft = self.get(draft_id)
        if draft is None:
            return {"error": "no such piece of work"}
        if draft["status"] == STARTED:
            return {"error": "it was started already", "run": draft["run_id"]}
        if draft["status"] in (WRITING, COLLECTING, DRAFTING):
            return {"error": "its plan is not drafted yet - Create it first"}
        approved = plan_of(plan if isinstance(plan, dict) else draft.get("plan") or {},
                           fallback=asked_of(draft) or draft.get("text") or "",
                           thorough=self._thorough())
        edited = isinstance(plan, dict) and plan_of(plan, thorough=self._thorough()) \
            != plan_of(draft.get("plan") or {}, thorough=self._thorough())
        folders = [str(one) for one in (folders if folders is not None
                                        else draft.get("folders") or []) if str(one).strip()]
        experience = self.app.state_heartbeat.experience
        work = self.app.work
        held = work.folder_in_use(folders) if hasattr(work, "folder_in_use") else None
        if held:
            return {"error": work.folder_said(held), "folder_held": held}
        # The box's settings as the user left them, over what it opened with.
        given_options = options or {}
        options = dict(draft.get("options") or {},
                       **{k: v for k, v in given_options.items()
                          if k in ("sound", "unconfined", "see_tries")})
        # A PLAN THE USER NEVER SAW (started before it was drafted, by itself
        # once it was - `_wait_then_start`): said so to the work. Started from
        # the box, its plan in front of them, it is theirs again.
        options.pop("plan_unseen", None)
        if given_options.get("plan_unseen"):
            options["plan_unseen"] = True
        tried: Dict[str, Any] = {}
        if not draft.get("unit_id") and draft.get("source") == "todo" and options.get("todo"):
            options["version"] = self._next_version(str(options["todo"]), draft_id)
            tried["attempt"] = options["version"]
            before = self._try_before(str(options["todo"]), for_work=True) \
                if options["version"] > 1 else None
            if before is not None:
                # A TRY OF THE SAME WORK (the user, 2026-10-31): its project
                # the first try's; its folder that one's name with its own -
                # "Title V2" - and its tree its own.
                unit = experience.unit(before["unit_id"])
                from .runner import WORK_DIR
                tried.update(tree=f"v{options['version']}", folder=str(output_folder_for(
                    Path(work.app_policy.workspace_root) / WORK_DIR,
                    f"{unit['name']} V{options['version']}")))
        if draft.get("source") == "project" and options.get("project"):
            same = [one for one in self._project_tries(options, besides=draft_id)
                    if bool((one.get("options") or {}).get("whole")) == bool(options.get("whole"))]
            if same:
                tried["attempt"] = len(same) + 1
        if tried.get("folder"):
            made = None
        elif draft.get("unit_id"):
            unit = experience.unit(draft["unit_id"])
            if unit is None:
                return {"error": "its project or task is gone"}
            made = None
        else:
            name = approved["title"] or "Delegated work"
            # Its earlier tries' work gone: a project of its own, named so.
            if tried.get("attempt", 0) > 1:
                name = f"{name} (V{tried['attempt']})"
            made = experience.store.create_work_project(name)
            # GIVEN NO FOLDER: what it makes goes in agent_work, where all the
            # agents' work is, in a folder named after it - a second of the
            # same name told apart by when.
            from .runner import WORK_DIR
            # A STEP OF A PROJECT (the user, 2026-11-08: "在项目下的to do, 会创建一个项目level的文件夹，
            # 然后每个to do 单独一个子文件夹"): in the agent's own, a folder for the project and in it
            # one for this work. Never the project's own folders: those only the user gives.
            under = folder_name(str((draft.get("options") or {}).get("under") or ""), "")
            output = "" if folders else str(output_folder_for(
                Path(work.app_policy.workspace_root) / WORK_DIR / under if under else
                Path(work.app_policy.workspace_root) / WORK_DIR, name))
            experience.store.mark_delegated(made["unit_id"], draft.get("source") or "delegate",
                                            output=output)
            unit = experience.unit(made["unit_id"])
        for path in folders:
            said = work.grant(unit["id"], path)
            if said.get("error"):
                if made is not None:
                    experience.store.drop_workspace(unit["id"])
                return {"error": said["error"]}
        # WHAT IT WAS TOLD TO READ, its sandboxed code let read it - a project's folders, on any
        # drive (the user, 2026-11-08: "code by need").
        if options.get("read_from") and hasattr(work, "let_read"):
            work.let_read(unit["id"], options.get("read_from"))
        # THE TRIES BEFORE, as they stand now - unless the user said not.
        earlier = self._tries_said(str(options.get("todo") or ""), besides=draft_id) \
            if tried.get("attempt", 0) > 1 and options.get("see_tries", True) is not False \
            else []
        if draft.get("source") == "project" and options.get("see_tries", True) is not False:
            earlier = self._tries_for(dict(draft, options=options), besides=draft_id)
        said = work.start(unit, instruction_of(draft, approved, folders, earlier=earlier,
                                              unseen=bool(options.get("plan_unseen"))),
                          # NOTHING TO SET (the user, 2026-11-06): no limits, whatever a draft
                          # saved before kept; packages by themselves, and where the system
                          # does not confine installs, asked all the same.
                          limits={}, packages="auto",
                          sound=options.get("sound"),
                          way=approved["way"],
                          **({"unconfined": True} if options.get("unconfined") else {}),
                          **tried)
        if said.get("error"):
            if made is not None:
                experience.store.drop_workspace(unit["id"])
            return {"error": said["error"]}
        self._set(draft_id, status=STARTED, plan=approved, options=options,
                  plan_by="user" if edited else "agent", run_id=str(said.get("run") or ""),
                  unit_id=unit["id"])
        return dict(said, unit=unit["id"], draft=draft_id)

    def compose(self, draft_id: str, instruction: str = "", title: str = "") -> Dict[str, Any]:
        """The user's instruction, as they wrote it in the box - then the
        context collected and the plan drafted, for them to approve. A
        `title` the user gave (the shortcut's box) is the work's title: said
        to the planner with the instruction, and the plan's own in place of
        the one the planner would have named it."""
        draft = self.get(draft_id)
        if draft is None:
            return {"error": "no such piece of work"}
        if draft["status"] != WRITING:
            return {"error": "its plan is already being drafted", "draft": draft}
        instruction = str(instruction or "").strip()[:20000]
        title = " ".join(str(title or "").split())[:120]
        if title:
            instruction = f"{title}\n\n{instruction}" if instruction else title
        if not (instruction or str(draft.get("text") or "").strip() or draft.get("folders")
                or draft.get("files") or draft.get("unit_id")):
            return {"error": "say what you want done"}
        options = dict(draft.get("options") or {}, instruction=instruction)
        if title:
            options["title"] = title
        self._set(draft_id, options=options, status=COLLECTING)
        self._collect_apart(draft_id)
        return {"draft": self.get(draft_id)}

    def remove(self, draft_id: str) -> Dict[str, Any]:
        """A DRAFT OR A WORK'S RESULT REMOVED, as the user confirmed (the user,
        2026-10-26): not shown again - in the to-do's work, in Work's drafts,
        and, started, its project not in Work's list (`remove_unit`). NOTHING
        IS DELETED: its files stay where they are. Not while it is going."""
        draft = self.get(draft_id)
        if draft is None:
            return {"error": "no such piece of work"}
        if draft["status"] == STARTING or (draft["status"] == STARTED and draft.get("unit_id")
                                           and self._came_of(draft["unit_id"],
                                                             str(draft.get("run_id") or ""))
                                           ["state"] == "ongoing"):
            return {"error": "it is going - stop it first"}
        todo = str((draft.get("options") or {}).get("todo") or "")
        others = [one for one in self._tries(todo, besides=draft_id)
                  if one.get("unit_id") == draft.get("unit_id")] if todo else []
        if draft["status"] == STARTED and draft.get("unit_id") and others:
            # ONE TRY of work others share (2026-10-31): that try's run alone
            # is not listed in it again.
            if draft.get("run_id"):
                self.app.work.hide_run(str(draft["run_id"]))
        elif draft["status"] == STARTED and draft.get("unit_id"):
            said = self.app.state_heartbeat.experience.remove_unit(draft["unit_id"])
            if said.get("error") and said["error"] != "no such project":
                return said
        options = {k: v for k, v in (draft.get("options") or {}).items()
                   if k != "start_when_ready"}
        options["removed"] = True
        self._set(draft_id, options=options)
        return {"removed": draft_id}

    def forget_run(self, run_id: str) -> None:
        """A TRY REMOVED IN WORK (the user, 2026-10-31): the to-do's version
        that started it is not shown either - the project stays."""
        for row in self.db.query("SELECT id, options FROM work_drafts WHERE run_id = ?",
                                 (run_id,)):
            try:
                options = json.loads(row["options"] or "{}") or {}
            except (TypeError, ValueError):
                options = {}
            options["removed"] = True
            self._set(row["id"], options=options)

    def discard(self, draft_id: str) -> bool:
        """A saved draft thrown away: as one cancelled."""
        return self.cancel(draft_id)

    def cancel(self, draft_id: str) -> bool:
        draft = self.get(draft_id)
        if draft is None or draft["status"] in (STARTED, STARTING):
            return False
        if (draft.get("options") or {}).get("start_when_ready"):
            # Cancelled while it waited for its plan: it does not start.
            self._set(draft_id, options={k: v for k, v in draft["options"].items()
                                         if k != "start_when_ready"})
        self._set(draft_id, status=CANCELLED)
        return True


#: Files that run as programs when opened - shown in their folder, never
#: opened by a click on the page.
PROGRAM_SUFFIXES = frozenset((
    ".exe", ".com", ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse",
    ".wsf", ".wsh", ".msc", ".msi", ".msp", ".scr", ".pif", ".cpl", ".hta", ".jar",
    ".lnk", ".url", ".reg", ".inf", ".appref-ms", ".application", ".py", ".pyw",
    ".sh", ".command", ".app", ".pkg", ".dmg", ".run", ".bin", ".desktop"))


def runs_as_program(path: Path) -> bool:
    """Whether opening the file would run it: by its suffix, or - off
    Windows - its being marked executable."""
    import os
    if Path(path).suffix.lower() in PROGRAM_SUFFIXES:
        return True
    return os.name != "nt" and os.access(str(path), os.X_OK)


def folder_name(name: str, default: str = "Delegated work") -> str:
    """A name as a folder may have it: no separators or characters a folder cannot hold - a name,
    never a path - at most 80 characters."""
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", str(name)).strip(" .")
    return re.sub(r"\s+", " ", clean)[:80].strip(" .") or default


def output_folder_for(workspace: Path, name: str,
                      now: Optional[datetime] = None) -> Path:
    """The folder in agent_work named after the work - its name as a folder
    may have it - or, where one of that name is there already, the same name
    with the date and time, so copies are told apart."""
    clean = folder_name(name)
    folder = Path(workspace) / clean
    if not folder.exists():
        return folder
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H%M")
    folder = Path(workspace) / f"{clean} {stamp}"
    count = 2
    while folder.exists():
        folder = Path(workspace) / f"{clean} {stamp} ({count})"
        count += 1
    return folder


def plan_of(said: Any, fallback: str = "", thorough: bool = True) -> Dict[str, Any]:
    """A plan as kept: each part there, lists as lists of short lines.
    `thorough`: false while the thorough way is switched off - the quick
    way, whatever was said."""
    said = said if isinstance(said, dict) else {}
    out: Dict[str, Any] = {}
    for part in PLAN_PARTS:
        value = said.get(part)
        if part in ("title", "understanding", "goal"):
            out[part] = str(value or "").strip()[:600]
        else:
            items = value if isinstance(value, list) else str(value or "").splitlines()
            out[part] = [str(one).strip(" -*\t")[:400] for one in items
                         if str(one).strip(" -*\t")][:12]
    if not out["title"]:
        out["title"] = " ".join(str(fallback).split()[:8])[:80] or "Delegated work"
    # The way, as the planner suggested it or the user chose: thorough when
    # nothing says otherwise.
    way = str(said.get("way") or "").strip().lower()
    out["way"] = (way if way in WAYS else "full") if thorough else "quick"
    out["way_why"] = str(said.get("way_why") or "").strip()[:400] if thorough else ""
    if out["way"] == "full" and not any("readme" in one.lower() for one in out["deliverables"]):
        out["deliverables"].append("README.md - what was made, where it is, how to check "
                                   "it and how to use it")
    return out


#: Words quoted in a record, in double quotes (an apostrophe is a "Theo's"),
#: twenty letters or more: someone's own.
QUOTED = re.compile(r"[\"“]([^\"”]{20,})[\"”]")


#: An email address, in a name the plan gives ("Name <address>").
ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _name_and_addresses(said: str) -> tuple:
    """A person as the plan names them: their name, and their addresses."""
    addresses = ADDRESS.findall(said)
    name = " ".join(ADDRESS.sub(" ", said).replace("<", " ").replace(">", " ").split())
    return (name or (addresses[0] if addresses else said)), addresses


def _said_as_them(person: Dict[str, Any], text: str) -> bool:
    """WHETHER `text` NAMES THIS CONTACT - not another person whose name holds
    theirs (the user, 2026-11-02: a reply to a professor was told about a
    WeChat contact "Ben", found inside "Ben Thistlewood" - and "ben" is
    inside "Benjamin" too). A name in letters counts said as a word of its
    own, and a one-word one not as the first name of someone else ("Ben"
    followed by "Thistlewood"); a name in Chinese, Japanese or Korean
    characters, which has no spaces to tell, as before."""
    standing = []
    try:
        from ...heartbeat.connections.people import standing_word
    except Exception:                    # pragma: no cover - the check as before
        standing_word = lambda _one: False   # noqa: E731
    for name in [person.get("name") or "", *(person.get("aliases") or [])]:
        name = " ".join(str(name).split())
        if not name or standing_word(name):
            continue
        if not name.isascii():
            standing.append(True)
            continue
        # The surname after it told by its capital - matched as written.
        followed = r"(?!(?-i:\s+[A-Z][a-z]))" if " " not in name else ""
        if re.search(rf"(?<![^\W\d_]){re.escape(name)}(?![^\W\d_]){followed}", text,
                     re.IGNORECASE):
            return True
    return any(standing)


def _names_said(names: List[str]) -> List[str]:
    """How a person may be named in the records: each of their names whole,
    and - written in letters - its last part, the family name ("Quillfeather";
    a first name alone is too many people's); a name in characters (沈清禾)
    only whole."""
    out: List[str] = []
    for one in names:
        one = " ".join(str(one or "").split())
        if len(one) >= 2:
            out.append(one)
        parts = [part for part in re.split(r"[\s,]+", one) if part]
        if one.isascii() and len(parts) > 1 and len(parts[-1].strip(".")) >= 3:
            out.append(parts[-1].strip("."))
    return list(dict.fromkeys(out))


def _who(person: Dict[str, Any]) -> Dict[str, Any]:
    """A contact, as the work is told it: their names, who they are, and
    what they are to the user."""
    places = [" ".join(filter(None, [str(one.get("role") or ""), str(one.get("org") or "")]))
              for one in person.get("affiliations") or [] if isinstance(one, dict)]
    who = "; ".join(filter(None, [
        ", ".join(one for one in places if one.strip()),
        ", ".join(str(one) for one in person.get("professions") or []),
        ", ".join(str(one) for one in person.get("locations") or []),
        str(person.get("summary") or "")[:600]]))
    from ...heartbeat.connections.people import standing_word
    # Their names - never a word for how they stand to the user (学姐): another
    # 学姐's records are not theirs.
    return {"names": [str(person.get("name") or "")] + [
                str(one) for one in person.get("aliases") or [] if not standing_word(one)],
            "who": who, "relation": ", ".join(str(one) for one in
                                               person.get("relationships") or []),
            **({"called": [str(one) for one in person["called"]]} if person.get("called") else {})}


def asked_of(draft: Dict[str, Any]) -> str:
    """The instruction the user wrote in the box, before Create."""
    return str((draft.get("options") or {}).get("instruction") or "").strip()


#: A PLAN THE USER NEVER SAW (the user, 2026-11-02: Start closes the box at
#: once - the plan drafted after, by the agent, was told to the work as
#: "the plan the user approved - follow it; it is theirs", and each of its
#: choices - how many sections, how long, what more - was kept to as theirs).
UNSEEN_PLAN = ("THE PLAN DRAFTED FOR THIS WORK - BY THE AGENT; THE USER DID NOT SEE IT (they "
               "pressed Start before it was written). Their own words above, and their ways "
               "(what was learned of them), come first: follow the plan where it fits what "
               "they asked; where it asks for more than they did - more parts, more length, "
               "more than the request - do what they asked instead. Its open questions were "
               "never answered: settle each from their words and their ways, and say how")


#: A FILE TOO BIG TO COPY (the user, 2026-11-08: "如果有些文件太大，不方便copy, 可以用地址的方式link"):
#: pointed to where it is, said so, instead of copied.
TOO_BIG_TO_COPY = ("A file too big to copy conveniently - a large data set, a model, a video - may be "
                   "pointed to where it is instead: say its full path in what you hand over, and "
                   "that it is used from there, not copied:")


def topic_words(keywords: List[str]) -> Tuple[set, set]:
    """The work's words, as a subject is told by: its first two (the subject itself, as the search
    words put it first) and the rest - each the words in them long enough and not common."""
    def words(phrase: Any) -> set:
        return {one for one in re.findall(r"[a-z0-9][a-z0-9'\-]{3,}", str(phrase or "").lower())
                if one not in TOPIC_STOP}
    strong = set().union(*(words(one) for one in keywords[:2])) if keywords else set()
    rest = set().union(*(words(one) for one in keywords[2:])) - strong if keywords else set()
    return strong, rest


def on_topic(text: str, strong: set, rest: set) -> bool:
    """On the work's subject: one of its own words, or two of the rest."""
    low = str(text or "").lower()

    def has(word: str) -> bool:
        return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", low) is not None
    return any(has(one) for one in strong) or sum(1 for one in rest if has(one)) >= 2


def more_found_lines(context: Dict[str, Any]) -> List[str]:
    """The other sources, said to the work (the user, 2026-11-08)."""
    lines: List[str] = []
    lessons = str(context.get("lessons") or "").strip()
    if lessons:
        lines.append("THE USER'S OWN RULES AND WAYS THAT BEAR ON IT - lessons kept from what they "
                     "did and said: follow each where it applies to this work; where one does "
                     "not, leave it:")
        lines += [f"  {line}" for line in lessons.splitlines()]
        lines.append("")
    said = context.get("said_in_chat") or []
    if said:
        lines.append("WHAT WAS SAID OF IT IN THE CHAT - the user's own words are what they want; "
                     "the agent's are what it answered then:")
        lines += [f"  - ({one.get('at')}) {str(one.get('who')).upper()}: {one.get('said')}"
                  for one in said]
        lines.append("")
    learned = context.get("learned") or []
    if learned:
        lines.append("WHAT WAS LEARNED OF THE USER'S WORK ON IT - from their own sessions, what "
                     "they made and worked on like this; read the folders it names as the "
                     "detail needs:")
        lines += [f"  - ({one.get('what')}) {one.get('title')}: {one.get('about')}" for one in learned]
        lines.append("")
    works = context.get("other_works") or []
    if works:
        lines.append("OTHER WORK DONE ON THE SAME SUBJECT - each in its own folder: READ them and "
                     "build on what fits, never write in them:")
        lines += [f"  - {one.get('work')} ({one.get('state')}, {one.get('ended') or 'not ended'}): "
                  f"{one.get('folder')}" + (f" - it reported: {one.get('report')}"
                                             if one.get("report") else "") for one in works]
        lines.append("")
    mail = context.get("mail") or []
    if mail:
        lines.append("THE USER'S MAIL THREADS ON IT, by their titles as last opened (open them "
                     "only through what the user can open; never sign in):")
        lines += [f"  - {one.get('title')} ({one.get('seen')})" for one in mail]
        lines.append("")
    calendar = context.get("calendar") or []
    reminders = context.get("reminders") or []
    if calendar or reminders:
        lines.append("ON THE USER'S CALENDAR AND IN THE AGENT'S REMINDERS ABOUT IT:")
        lines += [f"  - (calendar, {one.get('when')}) {one.get('title')}: {one.get('about')}"
                  for one in calendar]
        lines += [f"  - (reminder, {one.get('when')}) {one.get('title')}: {one.get('about')}"
                  for one in reminders]
        lines.append("")
    return lines


def instruction_of(draft: Dict[str, Any], plan: Dict[str, Any],
                   folders: Optional[List[str]] = None,
                   earlier: Optional[List[Dict[str, Any]]] = None,
                   unseen: bool = False) -> str:
    """What the run is told: the user's instruction and what they handed
    over, WHERE IT WORKS, and the plan they approved - theirs, word for word.
    `folders`: the ones the user gave it, as they left them in the box.
    `unseen`: the plan was drafted after they pressed Start - never seen by
    them, said so (UNSEEN_PLAN)."""
    lines = []
    if asked_of(draft):
        lines += ["THE USER'S INSTRUCTION:", asked_of(draft), ""]
    picked_lines = picked_told((draft.get("window") or {}).get("picked") or {})
    if picked_lines:
        # RIGHT-CLICKED (picked.py): what, where - the selected text first.
        lines += picked_lines + [""]
    elif str(draft.get("text") or "").strip():
        lines += ["WHAT THE USER HANDED OVER:", str(draft["text"]).strip(), ""]
    for path in draft.get("files") or []:
        lines.append(f"FILE THEY HANDED OVER: {path}")
    if draft.get("files"):
        lines.append("")
    window = draft.get("window") or {}
    if window.get("title") or window.get("url"):
        lines += [f"IT CAME FROM: {window.get('title') or ''} {window.get('url') or ''}".strip(),
                  ""]
    if (draft.get("context") or {}).get("part_of"):
        lines += [draft["context"]["part_of"], ""]
    front = (draft.get("options") or {}).get("front") or {}
    if front:
        lines += [FRONT_NOTE] + [f"  {key}: {value}" for key, value in front.items()] + [""]
    # ITS TEXT, AND WHERE THE FILE IS (the user, 2026-11-02: "你要给他text, 然后
    # 告诉他文件地址，他如果要读full pdf可以自己慢慢打开"): the document open
    # where they right-clicked, read before the work began (DOCUMENT_CHARS).
    document = (draft.get("context") or {}).get("document") or {}
    if document.get("path"):
        about = ", ".join(one for one in (
            f"{document['pages']} pages" if document.get("pages") else "",
            f"{document['chars']} characters of text" if document.get("chars") else "") if one)
        lines.append(f"THE DOCUMENT OPEN WHERE THEY RIGHT-CLICKED - the file: {document['path']}"
                     + (f" ({about})" if about else ""))
        if document.get("text"):
            lines.append("ITS TEXT" + (f" - {document['cut']}; open the file itself when the "
                                       "work needs more of it" if document.get("cut")
                                       else ", whole") + ":")
            lines += ["<<<", str(document["text"]).strip(), ">>>"]
        elif document.get("error"):
            lines.append(f"  not read beforehand ({document['error']}) - open the file itself")
        lines.append("")
    # THE TRIES BEFORE (the user, 2026-10-31): what they found and made.
    if earlier:
        lines.append("THIS WORK WAS TRIED BEFORE - each try, the first first. What they found "
                     "and made you MAY use or carry on from, where it helps - read their reports "
                     "and their folders - or not at all: this try follows its own instruction "
                     "and its own plan. WHATEVER THIS TRY DELIVERS IS WHOLE IN ITS OWN FOLDER: "
                     "copy into it what it keeps of an earlier one; never leave a result only in "
                     "an earlier folder, and never write into one. " + TOO_BIG_TO_COPY)
        for one in earlier:
            lines.append(f"  V{one.get('version')} ({(one['part'] + ', ') if one.get('part') else ''}"
                         f"{one.get('state') or 'started'}"
                         + (f", {one['ended']}" if one.get("ended") else "") + ")"
                         + (f" - asked: {one['asked']}" if one.get("asked") else ""))
            if one.get("folder"):
                lines.append(f"    its folder: {one['folder']}")
            if one.get("handed"):
                lines.append("    it handed over: " + "; ".join(one["handed"]))
            if one.get("report"):
                lines.append("    it reported: " + " ".join(str(one["report"]).split()))
        lines.append("")
    # WHO IT IS FOR, and what passed between them and the user - and who
    # the user is, to write as them.
    me = (draft.get("context") or {}).get("me") or {}
    if me.get("who") and (draft.get("context") or {}).get("people"):
        lines += [f"WHO THE USER IS: {me['who']}", ""]
    for person in (draft.get("context") or {}).get("people") or []:
        lines.append(f"WHO IT IS FOR: {person.get('name')}"
                     + (f" <{', '.join(person['addresses'])}>" if person.get("addresses") else ""))
        contact = person.get("contact") or {}
        if contact.get("several"):
            lines.append(f"  {contact['several']} of the user's contacts are called this: find "
                         "which one from what passed between them, below, before you write")
        elif not contact:
            lines.append("  not among the user's contacts: before you write, find who they "
                         "are from public pages - by their name and address - and say so")
        for key, label in (("who", "who they are"), ("relation", "to the user")):
            if contact.get(key):
                lines.append(f"  {label}: {contact[key]}")
        if contact.get("called"):
            lines.append("  the user calls them: " + ", ".join(contact["called"]))
        if contact.get("how_written"):
            lines.append("  HOW THE USER WRITES TO THEM - their own words, kept from their "
                         "messages to them, the latest last; write as these are written:")
            lines += [f"  - ({one.get('at')}, {one.get('channel') or 'a message'}) "
                      f"{' '.join(str(one.get('text') or '').split())}"
                      for one in contact["how_written"]]
        if person.get("exchanges"):
            lines.append("  WHAT PASSED BETWEEN THEM AND THE USER, the latest last - the "
                         "user's own words, where quoted, are how they write to them:")
            lines.append("  (each found by the name or address in brackets - one found by a "
                         "family name alone may be someone else of that name)")
            lines += [f"  - ({str(one.get('at') or '')[:16]}) [{one.get('matched') or 'name'}] "
                      f"{one.get('title')}: {one.get('detail')}" for one in person["exchanges"]]
        lines.append("")
    found = (draft.get("context") or {}).get("found") or []
    if found:
        lines.append("REFERENCES FROM THE USER'S OWN RECORDS - found by the work's words, "
                     "the most relevant and recent first; search further with "
                     "search_records, and read files and the web, as the detail needs. "
                     "What the user wrote to OTHER people among them shows how they "
                     "write - not what to say to this one:")
        lines += [f"  - [{one.get('kind')}] {one.get('title')} ({str(one.get('at') or '')[:10]})"
                  for one in found]
        lines.append("")
    lines += more_found_lines(draft.get("context") or {})
    # WHERE IT WORKS (the user, 2026-10-26): a folder they gave is the work's
    # own - worked in directly; none, a folder of its own is.
    given = [str(one) for one in (folders if folders is not None
                                  else draft.get("folders") or []) if str(one).strip()]
    if given:
        lines += ["WHERE YOU WORK - what the user gave for this work. Work in it "
                  "DIRECTLY: in a folder, change, add to and fix what is there as the work "
                  "needs; a FILE, change that file in place - nothing beside it is yours "
                  "to change. It is their copy, so leave alone what the work does not "
                  "need changed:",
                  *[f"  - {one}{' (a file)' if Path(one).is_file() else ''}" for one in given],
                  ""]
    elif not draft.get("unit_id"):
        lines += ["WHERE YOU WORK: no folder was given - make what you make in your own "
                  "folder, the one you may write in.", ""]
    # ITS PROJECT'S OWN FOLDERS, TO READ (the user, 2026-11-08: "读应该是可以的，只要告诉模型之前的
    # directory在哪就行"): a step of a project, or the whole of it, is told where the project's
    # things are - to read and copy from, never to write in; what it makes is in its own folder.
    reading = [str(one) for one in ((draft.get("options") or {}).get("read_from") or [])
               if str(one).strip() and str(one) not in given][:8]
    if reading:
        lines += ["THE PROJECT'S OWN FOLDERS - where its files are. READ them, and copy from them "
                  "what the work needs, as you need; NEVER write, change or delete anything in "
                  "them - they are the user's working copy. Everything you make, a changed copy "
                  "of one of their files too, goes in your own folder. " + TOO_BIG_TO_COPY,
                  *[f"  - {one}" for one in reading], ""]
    if unseen:
        heading = UNSEEN_PLAN + (":" if plan.get("way") == "quick" else
                                 "; the detailed contract is drafted from it, by the same rule:")
    else:
        heading = ("THE PLAN THE USER APPROVED - follow it; it is theirs:"
                   if plan.get("way") == "quick" else
                   "THE PLAN THE USER APPROVED - follow it; it is theirs, and the detailed "
                   "contract is drafted from it:")
    lines += [heading,
              f"Title: {plan.get('title')}",
              f"What they want: {plan.get('understanding')}",
              f"Goal: {plan.get('goal')}"]
    for name, label in (("steps", "Steps"), ("deliverables", "Deliverables"),
                        ("out_of_scope", "Not part of it"),
                        ("questions", "Open questions - never answered by the user"
                         if unseen else "Open questions")):
        if plan.get(name):
            lines.append(f"{label}:")
            lines += [f"  - {one}" for one in plan[name]]
    return "\n".join(lines)


__all__ = ["Drafts", "plan_of", "instruction_of", "asked_of", "WRITING", "output_folder_for", "PLAN_PARTS", "COLLECTING", "READY",
           "STARTED", "CANCELLED", "FAILED"]
