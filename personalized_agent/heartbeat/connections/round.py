"""With each state heartbeat round: WHAT THE USER NEEDS TO KNOW of the people
around them - for the work they are doing now.

The user's design, in order:

  1. The user's current tasks and projects - as the state heartbeat has
     summarised them, each title and description - are what this starts
     from: what does that work need to know about people?
  2. The contacts are searched for it, by the model, with tools: by name,
     relationship, organisation, tag and recent activity; a person opened
     whole. One it finds whose news is old is looked up on the public web
     (a search, a public page - never an account of the user's); so is an
     important one not looked at for a long while, even unasked. What is
     found is written to them, dated, with where it came from.
  3. A ranked list of what the user needs to know - about one person, or
     across many - for the Overview. Never the calendar.

And the user's own changes to their contacts past the most kept word for
word are summarised (store.KEEP_ACTIONS).

It searches until the model has what there is (the user's decision). Only
a runaway is stopped - `RUNAWAY_CALLS` model calls or `RUNAWAY_WEB` web
lookups in a round, far past what a round needs, since it runs unattended
with every state heartbeat. Its progress is shown in the State heartbeat
panel as a bar.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from ...models.base import ModelRequest, ToolSpec
from ...common.languages import in_display, in_english
from .store import ConnectionsStore, RELATIONSHIPS, now

log = logging.getLogger(__name__)

#: A round that has asked the model this many times is ended: not a budget,
#: a guard against a model that would never stop.
RUNAWAY_CALLS = 200
#: And this many web lookups of the app's own search: as high, and for the
#: same reason - a round runs with every state heartbeat, unattended.
RUNAWAY_WEB = 100
#: Looked up again on the web when the news of one the work needs is older.
STALE_DAYS = 30
#: An important person (closeness 4 or 5) unseen this long is looked up even
#: unasked - at most `MOST_PROACTIVE` a round.
PROACTIVE_DAYS = 45
MOST_PROACTIVE = 3
#: Every current high-level task is read - up to this many, a guard only.
MOST_TASKS = 60
#: The user themselves is looked up on the public web again after this many
#: hours - their own step of the round.
SELF_HOURS = 20
#: The round's stages, for the State heartbeat panel's bar.
STAGES = 7
#: What the user has been doing - every state, in a task or not - over the
#: last this many hours, at most `MOST_RECENT` of them, is read for what they
#: may need to know.
RECENT_HOURS = 48
MOST_RECENT = 40
#: At most this many of each kind of name are given to be tidied.
MOST_NAMES = 120
MODEL_TASK = "connections"

SYSTEM = """You help the user keep up with the people around them, for the work they
are doing now. You are given what the user is working on, and tools to
search their contacts, open one whole, look a person up on the PUBLIC web,
and write down what you find.

Rules:
  - Only public information: a web search, a public page. Never sign in, never
    post, never contact anyone. A page that needs a sign-in is not read.
  - Nothing invented. Every fact you write down says where it came from and
    when it happened; a fact you cannot date is dated when you found it.
  - EVERY FACT YOU ARE GIVEN CARRIES ITS TIMES: when it happened (`at`), when
    it was found (`found_at` - a page read, a window the user saw) and when it
    was written or last changed. Things go out of date - a job, a place, a
    plan: judge each by how old it is; one that matters now and is old, look
    up again, or say it may have changed.
  - What the user decided about a contact (their changes, dated, below) is
    theirs: a person they deleted is not looked up.
  - You do not add contacts: the agent heartbeat adds them, from what the user
    had on screen, and records what it sees of them there. You keep the ones
    there are up to date ACTIVELY - looking them up - and the user too.
  - Be brief and useful: the user reads the result on their Overview."""

NEEDS = """WHAT THE USER IS WORKING ON - every one of their current high-level tasks
and projects, as their state heartbeat summarised them:

{work}

TODAY: {today}

WORK OUT, TASK BY TASK, WHAT THE USER WOULD WANT TO READ NOW - INFORMATION:
news that bears on it, a paper, an article, a release, a better source, what
people they know published or announced about it. NOT who is waiting on them,
who wrote to them, deadlines or reminders - those are the calendar's, not
news. Each need says where to look: among their CONTACTS (the public news of
people they know) or on the WEB, or both. A few that matter, not everything;
none is a good answer.

Return ONLY this JSON object:
{{"needs": [{{"need": "what they would want to know", "for": "the task, project or activity it serves",
             "look": "contacts|web|both", "search": "what to look for"}}]}}"""

PLAN = """WHAT THE USER IS WORKING ON - their current high-level tasks and projects, as
their state heartbeat summarised them:

{work}

WHAT THEY WOULD WANT TO KNOW, as worked out from them - look for it among
their contacts:

{needs}

IMPORTANT PEOPLE NOT LOOKED AT FOR A WHILE (look up any of
them on the public web if anything of theirs may matter now - their work
changed, they posted, a paper came out):

{stale}

WHAT THE LAST ISSUES CARRIED - do not find these again:
{known}

LESSONS ABOUT THE USER'S CONTACTS - learned from their own changes:
{lessons}

ORGANISATIONS ALREADY WRITTEN - an organisation you write that means one of
these is written as it is here:
{orgs}

TODAY: {today}

DO THIS - a search of their CONTACTS, in as many steps as it takes:
1. Search the contacts for each need (search_people, open_person) - and
   search again from what you find: a person's organisation, a project, a
   tag leads to others.
2. One whose news is older than {stale_days} days and matters now: look them
   up (web_search, fetch_page) and write what you find (record_findings) -
   or, finding nothing new, mark_checked. Fill in their PUBLIC PROFILES too:
   one not listed that you find, and are sure is theirs - it links to one of
   theirs, or matches their name with their organisation or field - goes in
   with record_findings. When unsure, leave it.
3. Then answer with NEWS OF THE PEOPLE THEY KNOW worth reading, the best
   first: what one of them published, released, announced or said in public,
   a new role, a talk, a prize - with the page it is on; or a pattern across
   several (three of their collaborators moved to one company). NOT their
   messages to the user, who is waiting on the user, or reminders: those are
   not news, and not for here. (The public web is searched for the rest at
   the same time, apart.)

Return ONLY this JSON object when you are done:
{{"items": [{{"title": "short, what the user sees first",
             "opening": "what the story begins with - a sentence, or a short paragraph of two or three: a natural link from something specific they are doing or have asked themselves - the task, the file, the talk, the question they are stuck on - to this, so they want to read on. In passing, in their terms, as a friend who knows their work would put it; never a label like Why this matters, never flattery. Empty when there is no real link",
             "detail": "one to three plain sentences",{english}
             "people": ["the ids of the people it is about"],
             "kind": "person|across",
             "for": "the task or project it serves, or \\"general\\"",
             "importance": 1,
             "why": "why the user needs it now",
             "sources": ["where it comes from: an activity, a page address"]}}]}}
importance runs 1 (worth knowing) to 5 (act on it now). An empty items list is
a good answer when nothing about people bears on their work now."""

TOOLS = [
    ToolSpec("search_people", "Search the user's contacts by any words - name, "
             "relationship, organisation, role, profession, place, tag, what they have "
             "been doing. Names and organisations as they are written; any other word "
             "IN ENGLISH, as tags are kept. Returns each with its id, how close, and "
             "when their news was last updated.",
             {"type": "object", "properties": {
                 "query": {"type": "string"},
                 "relationship": {"type": "string",
                                  "description": "one of " + ", ".join(RELATIONSHIPS)
                                  + ", or another the user uses"},
                 "tag": {"type": "string"}}}),
    ToolSpec("open_person", "One contact whole: who they are, their accounts and "
             "public profiles, and what they have been doing, dated, newest first.",
             {"type": "object", "properties": {"id": {"type": "string"}},
              "required": ["id"]}),
    ToolSpec("web_search", "Search the public web. Only public results; nothing is "
             "signed in to.", {"type": "object", "properties": {
                 "query": {"type": "string"}}, "required": ["query"]}),
    ToolSpec("fetch_page", "Read a public web page (a profile the user gave, a "
             "result of a search).", {"type": "object", "properties": {
                 "url": {"type": "string"}}, "required": ["url"]}),
    ToolSpec("record_findings", "Write what you found about one contact: what they "
             "did, dated, each with where it came from; their organisations (each "
             "with their role there, the main one first), profession, place or a few "
             "tags - tags IN ENGLISH; and their PUBLIC PROFILES not listed yet (LinkedIn, "
             "GitHub, Scholar, a homepage...) - only one surely theirs, not a namesake's. "
             "What you write replaces what is there - the user's own words too, when the "
             "web shows they are out of date; a profile the user took off is not added "
             "back.",
             {"type": "object", "properties": {
                 "id": {"type": "string"},
                 "profiles": {"type": "array", "items": {"type": "object", "properties": {
                     "url": {"type": "string"}, "platform": {"type": "string"},
                     "handle": {"type": "string"},
                     "why": {"type": "string",
                             "description": "why it is theirs: what links it to them"}},
                     "required": ["url"]}},
                 "activity": {"type": "array", "items": {"type": "object", "properties": {
                     "at": {"type": "string"}, "what": {"type": "string"},
                     "url": {"type": "string"}}}},
                 "affiliations": {"type": "array", "items": {"type": "object", "properties": {
                     "org": {"type": "string"}, "role": {"type": "string"}}}},
                 "profession": {"type": "string"},
                 "location": {"type": "string"},
                 "tags": {"type": "array", "items": {"type": "string"}}},
                 "required": ["id"]}),
    ToolSpec("mark_checked", "Say a contact was looked at and nothing new was found.",
             {"type": "object", "properties": {"id": {"type": "string"}},
              "required": ["id"]}),
]

MY_SYSTEM = """You keep the user's own public profile up to date - what can be found
about them on the public web. Only public pages: a web search, a page that
needs no sign-in. Never sign in, never post, never contact anyone. Nothing
from their private messages or screen is yours here.

Be sure a page is THEIRS and not a namesake's: it links to, or is linked
from, a profile of theirs below, or matches their name together with their
organisation or field. When unsure, leave it."""

MY_PLAN = """THE USER, as known:
{me}

THEIR INBOX - details already waiting for them, or that they dismissed (do
not suggest those again):
{inbox}

TODAY: {today}

DO THIS - and search for as long as there is more to find:
1. Read their public profiles (fetch_page) and search for them (web_search)
   to see what is public about them now.
2. What they DID in public that is not recorded yet - a post, a talk, a
   paper, a release, an award, a new role announced - record it with
   record_my_activity: each dated when it happened, with the address of the
   public page it is on. It is recorded at once.
3. Their PROFILE DETAILS - their name, their main organisation and role
   there, their profession, place, a public profile of theirs not listed,
   one listed that is gone or not theirs: you do
   not change these. Suggest each change with propose_my_profile, saying why
   and the page that shows it; it goes to their inbox for them to check.
   Only a real change: not what is already so.

When done, answer with one line: what you found."""

MY_TOOLS = [
    TOOLS[2],                             # web_search
    TOOLS[3],                             # fetch_page
    ToolSpec("record_my_activity", "Record what the user did in public, from a public "
             "page: each item dated when it happened, with the page's address.",
             {"type": "object", "properties": {
                 "activity": {"type": "array", "items": {"type": "object", "properties": {
                     "at": {"type": "string"}, "what": {"type": "string"},
                     "url": {"type": "string"}}, "required": ["what", "url"]}}},
              "required": ["activity"]}),
    ToolSpec("propose_my_profile", "Suggest changes to the user's own profile details. "
             "Not made: each goes to their inbox, for them to accept or dismiss. field is "
             "name, profession or location (with value), affiliation (their main "
             "organisation now, with org and role), add_profile (with url, and platform) "
             "or remove_profile (with url).",
             {"type": "object", "properties": {
                 "changes": {"type": "array", "items": {"type": "object", "properties": {
                     "field": {"type": "string", "enum": ["name", "profession", "location",
                                                          "affiliation", "add_profile",
                                                          "remove_profile"]},
                     "value": {"type": "string"}, "url": {"type": "string"},
                     "org": {"type": "string"}, "role": {"type": "string"},
                     "platform": {"type": "string"}, "why": {"type": "string"},
                     "source": {"type": "string",
                                "description": "the public page that shows it"}},
                     "required": ["field", "why", "source"]}}},
              "required": ["changes"]}),
]

TIDY = """Below are the LABELS the user's public profiles and their contacts' accounts
carry, and the ORGANISATIONS their contacts are at - each with how many use it.
Some may be ONE THING WRITTEN DIFFERENTLY: "UCL" and "University College
London", "Twitter" and "X", "Google" and "Google LLC". Group only those
that mean the same; leave apart what merely looks alike (two universities of
one city, a company and its parent). For each group, the name to keep: the
clearest and most usual one.

LABELS:
{labels}

ORGANISATIONS:
{orgs}

Return ONLY this JSON object - an empty list when nothing is written twice:
{{"groups": [{{"kind": "label|org", "keep": "the name kept", "same": ["each other name of it"]}}]}}"""

#: WHAT THE USER NEEDS TO KNOW, KEPT AS A LIST: each round's findings are
#: not a new list but changes to the one there is - what still holds stays
#: as it is, and nothing is taken off without saying why.
UPDATE = """WHAT THE USER NEEDS TO KNOW - the list as it stands, each item with its id:
{current}

WHAT THIS ROUND FOUND - in their contacts and on the public web:
{found}

WHAT THEY ARE WORKING ON NOW, in short:
{work}

TODAY: {today}

Update the list. Return ONLY the changes, as this JSON object:
{{"changes": [
  {{"op": "add", "item": {{"title": "...", "opening": "...", "detail": "...", "people": ["ids"],
                          "kind": "person|across|web", "for": "...", "importance": 1,
                          "why": "...", "sources": ["..."]}}}},
  {{"op": "edit", "id": "an id above", "item": {{"only the fields that change": "..."}},
   "why": "what changed"}},
  {{"op": "delete", "id": "an id above",
   "why": "past its date, done, superseded, or no longer bearing on their work"}}]}}

  - AN ITEM THAT STILL HOLDS AND STILL MATTERS IS LEFT AS IT IS: not listed.
    Do not reword one that does not need it.
  - A finding that is an item already: an edit of it when it adds something
    (a new date, a change, a better source); nothing otherwise.
  - Delete only with a reason - an event past, a thing done or no longer
    true, or one that no longer bears on what they are doing.
  - importance runs 1 (worth knowing) to 5 (act on it now). At most {most}
    items in all. An empty list of changes is a good answer.
  - "opening" is what an item begins with - a sentence, or a short
    paragraph of two or three: a natural link from
    something specific they are doing - the task, the file, the talk, the
    question they are stuck on - to this, so they want to read on. In
    passing, in their terms, as a friend who knows their work would put it;
    never a label like "Why this matters", never flattery. Empty when there
    is no real link."""

#: The most items the list keeps.
MOST_KNOW = 20

WEB_SYSTEM = """You look up, on the PUBLIC web, what the user would want to know now. Only
public pages: a web search, a page that needs no sign-in. Never sign in,
never post. Nothing invented: every item says where it came from. Brief and
useful: the user reads the result on their Overview."""

WEB_PLAN = """WHAT THE USER WOULD WANT TO KNOW NOW, as worked out from their current
high-level tasks:

{needs}

WHAT THEY ARE WORKING ON, in short:
{work}

WHAT THE LAST ISSUES CARRIED - do not find these again:
{known}

TODAY: {today}

Search the web for it (web_search, fetch_page) - and again from what you
find - for INFORMATION worth reading for it: an article, a paper, a release,
a guide, news that bears on what they are doing - each a page to read. Not
reminders, deadlines or messages: information. Not about the people they
know - their contacts are searched at the same time, apart.

Return ONLY this JSON object - an empty list when nothing is worth it:
{{"items": [{{"title": "short, what the user sees first",
             "opening": "what the story begins with - a sentence, or a short paragraph of two or three: a natural link from something specific they are doing or have asked themselves - the task, the file, the talk, the question they are stuck on - to this, so they want to read on. In passing, in their terms, as a friend who knows their work would put it; never a label like Why this matters, never flattery. Empty when there is no real link",
             "detail": "one to three plain sentences",{english}
             "for": "the task, project or activity it serves",
             "importance": 1,
             "why": "why the user would want it now",
             "sources": ["the page addresses it comes from"],
             "image_url": "the address of the picture the page shows with it, if it has one"}}]}}
importance runs 1 (worth knowing) to 5 (act on it now)."""

SUMMARISE = """Below are the user's own changes to their contacts - adds, edits, deletes,
merges, each dated, with their reason when they gave one - the OLDEST of
them, past the most kept word for word. Fold them into the summary of the
older changes (given, if there is one), so that what they show stays usable:
who the user removed and why, what they corrected, what kinds of contact they
keep or do not. Keep names and dates where they matter (a deletion with its
date). At most 300 words. Return only the summary text.

THE SUMMARY SO FAR:
{summary}

THE CHANGES TO FOLD IN:
{changes}"""


def _days_ago(value: Any) -> Optional[float]:
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds() / 86400.0


def work_context(db: Any) -> str:
    """The user's current tasks and projects, as the state heartbeat
    summarised them: each title and description, the most important first."""
    lines: List[str] = []
    try:
        rows = db.query("SELECT described FROM state_tasks WHERE scope = 'live' AND "
                        "COALESCE(retired_at, '') = '' AND COALESCE(archived_at, '') = '' "
                        "AND COALESCE(described, '') != '' ORDER BY updated_at DESC")
    except Exception:            # no state heartbeat tables: nothing to say
        rows = []
    tasks = []
    for row in rows:
        try:
            said = json.loads(row["described"])
        except (ValueError, TypeError):
            continue
        if not isinstance(said, dict) or not said.get("title"):
            continue
        try:
            weight = int(said.get("importance") or 0) + int(said.get("urgency") or 0)
        except (TypeError, ValueError):
            weight = 0
        tasks.append((weight, said))
    tasks.sort(key=lambda one: one[0], reverse=True)
    for _weight, said in tasks[:MOST_TASKS]:
        description = str(said.get("summary") or said.get("goal") or "")
        status = str(said.get("status") or "")
        # What a summary judged not to be a task is still what they DO:
        # their full activity, given as such.
        what = "ACTIVITY" if said.get("concrete") is False else "TASK"
        lines.append(f"- {what}: {said['title']}\n  {description[:400]}"
                     + (f"\n  now: {status[:300]}" if status else ""))
    try:
        projects = db.query("SELECT name, why FROM state_projects WHERE scope = 'live' "
                            "ORDER BY updated_at DESC LIMIT 6")
    except Exception:
        projects = []
    for row in projects:
        # A project is a top-level task - their aggregate - and said as one.
        lines.append(f"- TASK: {row['name']}\n  {str(row['why'] or '')[:400]}")
    return "\n".join(lines) or "Nothing summarised yet."


def recent_states(db: Any, hours: int = RECENT_HOURS, most: int = MOST_RECENT) -> str:
    """WHAT THE USER HAS BEEN DOING: every state the agent heartbeat wrote in
    the last `hours` - part of a task or not, a video watched as much as a
    paper written - newest first, each with when, and marked when it is part
    of no task."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        rows = db.query(
            "SELECT s.title, s.detail, s.created_at, s.task_id, "
            "(SELECT 1 FROM state_task_loose l WHERE l.state_id = s.id AND l.scope = 'live') "
            "AS loose FROM states s WHERE s.source IN ('agent', 'user') "
            "AND s.status != 'retired' AND s.created_at >= ? "
            "ORDER BY s.created_at DESC LIMIT ?", (since, int(most)))
    except Exception:            # no states yet: nothing to say
        rows = []
    lines = []
    for row in rows:
        when = str(row["created_at"] or "")[:16].replace("T", " ")
        detail = " ".join(str(row["detail"] or "").split())[:240]
        lines.append(f"- {when} UTC{' (part of no task)' if row['loose'] else ''}: "
                     f"{row['title']}" + (f" - {detail}" if detail else ""))
    return "\n".join(lines) or "Nothing recorded lately."


class ConnectionsRound:
    """One round of it at a time, on a thread of its own; its progress for
    the State heartbeat panel's bar."""


    def __init__(self, store: ConnectionsStore, models: Any, db: Any,
                 search: Optional[Callable[[str], Any]] = None,
                 fetch: Optional[Callable[[str], Any]] = None,
                 download: Optional[Callable[[str], Any]] = None) -> None:
        self.store = store
        self.models = models
        self.db = db
        if search is None or fetch is None:
            from ...agent.work.tools import fetch as _fetch, search as _search
            search, fetch = search or _search, fetch or _fetch
        self.search, self.fetch = search, fetch
        from . import avatars
        self.download = download or avatars.download
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        #: WHAT THE USER READS (heartbeat/reading) and the embedder: set by
        #: the application - the topics each issue recommends from.
        self.reading: Any = None
        self.embedder: Any = None
        #: The round's count, kept by its two searches at once.
        self._count_lock = threading.RLock()
        self.progress: Dict[str, Any] = {"running": False}

    # -- starting --------------------------------------------------------------
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, going: Callable[[], bool] = lambda: True,
              look_me_up: bool = False) -> bool:
        """A round, on its own thread - False when one is going already.
        `look_me_up`: the user's own step now, however lately it ran."""
        with self._lock:
            if self.running():
                return False
            self.progress = {"running": True, "stage": "starting", "done": 0,
                             "total": STAGES, "started_at": now(), "calls": 0, "web": 0}
            self._thread = threading.Thread(target=self._run, args=(going, look_me_up),
                                            name="connections-round", daemon=True)
            self._thread.start()
        return True

    def wait(self, timeout: Optional[float] = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _stage(self, done: int, stage: str) -> None:
        self.progress.update(done=done, stage=stage)

    def _run(self, going: Callable[[], bool], look_me_up: bool = False) -> None:
        try:
            said = self.run(going, look_me_up)
            self.progress.update(running=False, done=STAGES, stage="done", finished_at=now(),
                                 items=len(said.get("items") or []))
        except Exception as exc:
            log.warning("the connections round failed", exc_info=True)
            self.progress.update(running=False, stage="failed", error=str(exc)[:300],
                                 finished_at=now())

    # -- the round ----------------------------------------------------------------
    def run(self, going: Callable[[], bool] = lambda: True,
            look_me_up: bool = False) -> Dict[str, Any]:
        used = {"calls": 0, "web": 0, "tokens_in": 0, "tokens_out": 0}
        self._stage(0, "summarising the oldest of your changes to contacts")
        self._fold_lessons(used)
        if not going():
            return {}
        self._stage(1, "one spelling for each label and organisation")
        tidied = self._tidy_names(used)
        if not going():
            return {}
        self._stage(2, "looking you up on the public web")
        me_said = self._look_me_up(used, going, look_me_up)
        if not going():
            return {}
        # WHAT THE USER WOULD WANT TO KNOW: from their work - and from what
        # they have been doing, part of a task or not (a video watched).
        self._stage(3, "working out what you would want to know")
        work = work_context(self.db)
        today = datetime.now().astimezone().strftime("%A %d %B %Y, %H:%M %Z")
        needs = self._needs(used, work, today)
        if not going():
            return {}
        needs_text = "\n".join(
            f"- {one.get('need')} (for {one.get('for') or 'general'}; look: "
            f"{one.get('look') or 'both'}; search: {one.get('search') or one.get('need')})"
            for one in needs) or "Nothing in particular - what bears on their work now."
        # TWO SEARCHES AT ONCE: their contacts, step by step from what each
        # step finds; and the public web, directly.
        self._stage(4, "searching your contacts and the web")
        stale = self._stale()
        known = "\n".join(f"- {title}" for title in self._recent_stories()) \
            or "Nothing yet."
        plan = PLAN.format(
            work=work, needs=needs_text, english=self._english_field(),
            stale="\n".join(f"- [{one['id']}] {one['name']} ({one['relationship'] or '?'}, "
                            f"closeness {one['closeness']}) - last looked at "
                            f"{(one['checked_at'] or 'never')[:10]}" for one in stale)
            or "None.", proactive=MOST_PROACTIVE, lessons=self.store.lessons_text(),
            orgs=", ".join(one["name"] for one in self.store.names_in_use("org")[:MOST_NAMES])
            or "None yet.", today=today, stale_days=STALE_DAYS, known=known)
        web_items: List[Any] = []
        web_wanted = any(str(one.get("look") or "both").lower() in ("web", "both")
                         for one in needs) or not needs
        branch = None
        if web_wanted:
            branch = threading.Thread(
                target=lambda: web_items.extend(self._web_branch(used, going, needs_text,
                                                                 work, today, known)),
                name="connections-web", daemon=True)
            branch.start()
        # AND A PAGE FOR EACH THING THEY ARE DOING: each searched from its
        # detail for what they have not seen (recommend.py).
        pages = self._pages()
        topic_items: List[Any] = []
        topics_branch = None
        if pages:
            from .recommend import since_of
            since = since_of((self.store.briefing() or {}).get("at"))
            topics_branch = threading.Thread(
                target=lambda: topic_items.extend(self._page_branch(
                    used, going, pages, today, since)),
                name="connections-pages", daemon=True)
            topics_branch.start()
        messages: List[Dict[str, Any]] = [{"role": "user", "content": plan}]
        answer = self._loop(messages, used, going, system=SYSTEM,
                            language=in_display("the stories, and what you record of a "
                                                "person")
                            + in_english('their tags, and each story\'s "english"'))
        if branch is not None:
            branch.join()
        if topics_branch is not None:
            topics_branch.join()
        self._stage(5, "ranking what you need to know")
        said = _json_of(answer)
        found = self._items([*(said.get("items") or []),
                             *[dict(one, kind="web") for one in web_items
                               if isinstance(one, dict)]])
        # EVERY STORY FRESH: information found for this issue - nothing is
        # carried over as a list (what was carried is left out, above and by
        # the paper's own filter).
        found = [dict(one, recommended=True) for one in found]
        changes: List[Dict[str, Any]] = []
        items = found
        # THE PAPER: what was found for their work and for each page,
        # checked, scored and laid out a page each.
        items, paper = self._paper(items, topic_items, pages)
        self._stage(5, "a picture for each story")
        items = self._pictured(items, going)
        self.store.save_briefing(needs, items, dict(used, stale_offered=len(stale),
                                                    web_items=len(web_items),
                                                    topic_items=len(topic_items),
                                                    found=len(found), changes=changes,
                                                    paper=paper,
                                                    pages=[{"key": one["key"],
                                                            "title": one.get("heading")
                                                            or one["title"],
                                                            "subjects": [s["title"] for s in
                                                                         one["subjects"]]}
                                                           for one in pages]))
        self.progress.update(calls=used["calls"], web=used["web"])
        # AND THEIR PICTURES: the people with a public profile and none yet -
        # no model, a few fetches each (avatars.py).
        self._stage(6, "pictures of your contacts")
        pictures = 0
        if self.store.folder is not None and going():
            from . import avatars
            for person_id in avatars.wanting(self.store):
                if not going():
                    break
                if avatars.fetch_avatar(self.store, person_id, get=self.download).get("ok"):
                    pictures += 1
        return {"needs": needs, "items": items, "used": used, "pictures": pictures,
                "me": me_said, "tidied": tidied}

    def _recent_stories(self, issues: int = 3, most: int = 60) -> List[str]:
        """The titles the last issues carried - not to be found again."""
        out: List[str] = []
        for one in self.store.briefings()[:issues]:
            for item in (self.store.briefing(one["id"]) or {}).get("items") or []:
                title = str(item.get("title") or "")
                if title and title not in out:
                    out.append(title)
        return out[:most]

    def _pages(self) -> List[Dict[str, Any]]:
        """THE PAGES OF THIS ISSUE (recommend.py, 1-2): what the user is
        doing now - their tasks and projects with something in them lately,
        and what they have been reading and watching - at most MOST_PAGES,
        things alike sharing one; each with its detail."""
        from .recommend import plan_pages, said_of_topics, subjects
        try:
            # The English copies the pages are measured by, filled first
            # where missing (english_copies.py).
            beat = getattr(self, "state_heartbeat", None)
            if beat is not None:
                beat.fill_english_copies()
            found = subjects(self.db)
            embed = (lambda texts: self.embedder.embed(texts)) \
                if self.embedder is not None else None
            return plan_pages(found, embed, said_of_topics(self.db))
        except Exception:
            log.warning("the pages could not be planned", exc_info=True)
            return []

    def _page_branch(self, used: Dict[str, int], going: Callable[[], bool],
                     pages: List[Dict[str, Any]], today: str,
                     since: datetime) -> List[Dict[str, Any]]:
        """Each page searched on the public web FROM ITS DETAIL - what the
        user did, read and watched for it, not its heading - for what they
        have not seen: mostly close, a few broad; each story with its gain,
        why it is there and what in it is new to them."""
        from .recommend import (BROAD_PER_PAGE, LEISURE_PAGE, PAGE_PLAN, PER_TOPIC, TOPIC_SYSTEM,
                                known_points)

        def handle(name: str, args: Dict[str, Any]) -> Any:
            if name not in ("web_search", "fetch_page"):
                return {"error": f"no tool {name} here"}
            return self._tool(name, args, used)

        out: List[Dict[str, Any]] = []
        when = since.astimezone().strftime("%A %d %B %Y")
        shown = "\n".join(f"- {title}" for title in self._recent_stories()) or "Nothing yet."
        examples = self._examples()
        for page in pages:
            if not going() or not self._room(used):
                break
            known: List[str] = []
            for topic_id in page.get("topic_ids") or []:
                known += [one for one in known_points(self.db, topic_id) if one not in known]
            about = "\n".join(f"- {'WORK' if one['kind'] == 'work' else 'READING/WATCHING'}: "
                              f"{one['title']}" for one in page["subjects"])
            try:
                answer = self._loop([{"role": "user", "content": PAGE_PLAN.format(
                    subjects=about,
                    purpose=LEISURE_PAGE if page.get("leisure") else "",
                    detail="\n".join(f"- {line}" for line in page["detail"])
                    or "Nothing written down yet.",
                    known="\n".join(f"- {one}" for one in known[:10]) or "Nothing in particular.",
                    shown=shown, since=when, today=today, broad=BROAD_PER_PAGE,
                    most=PER_TOPIC, english=self._english_field(),
                    liked=self._near(examples, page, "more"),
                    disliked=self._near(examples, page, "less"))}], used, going,
                    tools=[TOOLS[2], TOOLS[3]], handle=handle, room=lambda: self._room(used),
                    counted={"calls": 0}, system=TOPIC_SYSTEM,
                    language=in_display("the page's heading and its stories")
                    + in_english('each story\'s "english"'))
            except Exception:
                log.warning("the page %s could not be searched", page["title"], exc_info=True)
                continue
            said = _json_of(answer)
            heading = str(said.get("heading") or "").strip()
            if heading:
                page["heading"] = heading[:60]
            for one in (said.get("items") or [])[:PER_TOPIC]:
                if not isinstance(one, dict) or not str(one.get("title") or "").strip():
                    continue
                story = self._items([dict(one, kind="web", **{"for": page.get("heading")
                                                             or page["title"]})])
                if not story:
                    continue
                try:
                    gain = max(0.0, min(5.0, float(one.get("gain"))))
                except (TypeError, ValueError):
                    gain = 1.0
                out.append(dict(story[0], page=page["key"], gain=gain, recommended=True,
                                because=str(one.get("because") or "")[:200],
                                said_relation=str(one.get("relation") or "")[:10],
                                new_to_you=str(one.get("new_to_you") or "")[:400],
                                published=str(one.get("published") or "")[:10]))
        return out

    def _english_field(self) -> str:
        """Each story's English, asked for only when the user does not read
        English - when they do, what they read is what is measured."""
        from ...common.languages import needs_english_copy
        from .recommend import STORY_ENGLISH
        config = getattr(self.models, "config", None)
        return STORY_ENGLISH if config is not None and needs_english_copy(config) else ""

    def _examples(self) -> List[Dict[str, Any]]:
        """What the user said "more like this" and "not interested" to, each
        with its vector - measured once for the issue."""
        from .recommend import said_examples
        examples = said_examples(self.db, self.store)
        if examples and self.embedder is not None:
            try:
                vectors = self.embedder.embed([one["english"] for one in examples])
            except Exception:
                vectors = []
            for one, vector in zip(examples, vectors):
                one["vector"] = vector
        return examples

    def _near(self, examples: List[Dict[str, Any]], page: Dict[str, Any], action: str,
              most: int = 5) -> str:
        """The examples of one say that belong to a page: near what it is about."""
        from ..reading.topics import cosine
        from .recommend import BROAD_AT
        mine = []
        for one in examples:
            if one["action"] != action or not one.get("vector"):
                continue
            near = max((cosine(one["vector"], anchor) for anchor in page.get("anchors") or []),
                       default=0.0)
            if near >= BROAD_AT:
                mine.append((near, one))
        mine.sort(key=lambda pair: -pair[0])
        return "\n".join(f"- {one['title']} ({one['english'][:200]})"
                         for _, one in mine[:most]) or "None yet."

    def _paper(self, items: List[Dict[str, Any]], page_items: List[Dict[str, Any]],
               pages: List[Dict[str, Any]]):
        """The issue's stories, checked, scored and laid out a page each
        (recommend.lay_out)."""
        from .recommend import lay_out, seen_addresses
        from .store import new_id
        stories = [dict(one, id=one.get("id") or new_id("know"))
                   for one in [*items, *page_items]]
        if not stories:
            return [], {"front": [], "brief": [], "pages": []}
        try:
            issues = [self.store.briefing(one["id"]) or {}
                      for one in self.store.briefings()[:30]]
            seen = seen_addresses(self.db, issues)
            read_vectors = [json.loads(row["vector"]) for row in self.db.query(
                "SELECT vector FROM reading_pieces WHERE vector IS NOT NULL "
                "ORDER BY last_at DESC LIMIT 500")]
        except Exception:                # no reading log yet: nothing to weigh against
            seen, read_vectors = set(), []
        from .recommend import story_text
        shown_vectors: List[List[float]] = []
        recent = [story_text(item) for one in self.store.briefings()[:3]
                  for item in (self.store.briefing(one["id"]) or {}).get("items") or []][:60]
        if recent and self.embedder is not None:
            try:
                shown_vectors = self.embedder.embed(recent)
            except Exception:
                shown_vectors = []
        examples = self._examples()
        picked, paper = lay_out(
            stories, pages, self.embedder, read_vectors, shown_vectors, seen,
            liked=[one["vector"] for one in examples if one["action"] == "more"
                   and one.get("vector")],
            disliked=[one["vector"] for one in examples if one["action"] == "less"
                      and one.get("vector")])
        return picked, paper

    def _needs(self, used: Dict[str, int], work: str, today: str) -> List[Dict[str, Any]]:
        """What the user would want to know, worked out once - then searched
        for in their contacts and on the web at the same time."""
        if not self._room(used):
            return []
        response = self._ask([{"role": "user", "content": NEEDS.format(
            work=work, today=today)}], used,
            # What each is for is shown above its stories.
            language=in_display('each need, and what it is for ("for") - the words to '
                                'search with in whatever language the pages are written in'))
        said = _json_of(str(getattr(response, "text", "") or ""))
        return [{key: str(one.get(key) or "")[:300] for key in ("need", "for", "look", "search")}
                for one in said.get("needs") or [] if isinstance(one, dict)
                and str(one.get("need") or "").strip()][:12]

    def _web_branch(self, used: Dict[str, int], going: Callable[[], bool], needs: str,
                    work: str, today: str, known: str = "Nothing yet.") -> List[Dict[str, Any]]:
        """THE PUBLIC WEB, searched for what the user would want to know -
        beside the search of their contacts, within its own few calls and the
        round's web lookups. What it found, as items."""
        mine = {"calls": 0}

        def room() -> bool:
            return self._room(used)

        def handle(name: str, args: Dict[str, Any]) -> Any:
            if name not in ("web_search", "fetch_page"):
                return {"error": f"no tool {name} here"}
            return self._tool(name, args, used)

        try:
            answer = self._loop([{"role": "user", "content": WEB_PLAN.format(
                needs=needs, work=work[:6000], today=today, known=known,
                english=self._english_field())}], used, going,
                tools=[TOOLS[2], TOOLS[3]], handle=handle, room=room, counted=mine,
                system=WEB_SYSTEM,
                language=in_display("the stories") + in_english('each story\'s "english"'))
        except Exception:
            log.warning("the web search of the connections round failed", exc_info=True)
            return []
        return [one for one in _json_of(answer).get("items") or [] if isinstance(one, dict)]

    # -- one spelling for each thing -----------------------------------------------
    def _tidy_names(self, used: Dict[str, int]) -> Dict[str, Any]:
        """THE LABELS OF PROFILES AND THE ORGANISATIONS, each written one way:
        the model is shown every one in use and groups those that are one
        thing written differently ("UCL", "University College London"); each
        group is then written as one - the user's own spelling when one of
        them is theirs - and learned, so it is written so from then on.
        Asked only when the names have changed since they were last tidied."""
        store = self.store
        names = {kind: store.names_in_use(kind)[:MOST_NAMES] for kind in ("label", "org")}
        names["label"] = [one for one in names["label"] if one["count"]]
        if all(len(one) < 2 for one in names.values()):
            return {"skipped": "fewer than two of each"}
        seen = json.dumps({kind: sorted(one["name"] for one in got)
                           for kind, got in names.items()}, ensure_ascii=False, sort_keys=True)
        import hashlib
        print_ = hashlib.sha256(seen.encode("utf-8")).hexdigest()
        if store.meta("names_tidied") == print_:
            return {"skipped": "nothing new since they were last tidied"}
        if not self._room(used):
            return {"skipped": "no room left this round"}

        def listed(kind: str) -> str:
            return "\n".join(f"- {one['name']} ({one['count']})" for one in names[kind]) \
                or "(none)"

        response = self._ask([{"role": "user", "content": TIDY.format(
            labels=listed("label"), orgs=listed("org"))}], used,
            # Labels are found by, in one language; an organisation keeps its
            # own name, as it writes it.
            language=in_english("each label's one spelling - an organisation keeps its "
                                "own name as it writes it"))
        said = _json_of(str(getattr(response, "text", "") or ""))
        merged = []
        for group in said.get("groups") or []:
            if not isinstance(group, dict) or group.get("kind") not in ("label", "org"):
                continue
            kind = group["kind"]
            known = {one["name"]: one for one in names[kind]}
            members = [str(one) for one in group.get("same") or [] if str(one) in known]
            keep = str(group.get("keep") or "").strip()
            if keep in known and keep not in members:
                members.append(keep)
            if len(members) < 2 or not keep:
                continue
            # The user's own spelling, when one of them is theirs.
            theirs = sorted((one for one in members if known[one]["user"]),
                            key=lambda one: -known[one]["user"])
            if theirs:
                keep = theirs[0]
            changed = store.merge_names(kind, keep, [one for one in members if one != keep])
            merged.append({"kind": kind, "keep": keep,
                           "same": [one for one in members if one != keep], "changed": changed})
        # What was just merged is part of what was seen: the next round
        # looks again only when something new comes.
        after = json.dumps({kind: sorted(one["name"] for one in store.names_in_use(kind)
                                         [:MOST_NAMES] if kind == "org" or one["count"])
                            for kind in ("label", "org")}, ensure_ascii=False, sort_keys=True)
        store.meta("names_tidied", hashlib.sha256(after.encode("utf-8")).hexdigest())
        return {"merged": merged}

    # -- the user's own step ------------------------------------------------------
    def _look_me_up(self, used: Dict[str, int], going: Callable[[], bool],
                    now_anyway: bool = False) -> Dict[str, Any]:
        """THE USER, looked up on the public web as a contact is - from their
        own public profiles and a search: what they did in public, recorded
        at once; a change to their profile details, put in their inbox for
        them to check. Nothing from their screen: only public pages. At most
        once in `SELF_HOURS`, unless asked."""
        me = self.store.me()
        if not me["accounts"]:
            return {"skipped": "no public profile of yours given"}
        age = _days_ago(me["checked_at"]) if me["checked_at"] else None
        if not now_anyway and age is not None and age * 24 < SELF_HOURS:
            return {"skipped": "looked up lately"}
        mine = {"calls": 0, "web": 0, "activity": 0, "asked": 0}

        def room() -> bool:
            return self._room(used)

        def handle(name: str, args: Dict[str, Any]) -> Any:
            return self._my_tool(name, args, used, mine)

        messages: List[Dict[str, Any]] = [{"role": "user", "content": MY_PLAN.format(
            me=_me_text(me), inbox=self._inbox_text(),
            today=datetime.now().astimezone().strftime("%A %d %B %Y, %H:%M %Z"))}]
        before = used["calls"]
        self._loop(messages, used, going, tools=MY_TOOLS, handle=handle, room=room,
                   counted=mine, system=MY_SYSTEM,
                   # What was found, and why each change is proposed: in the inbox.
                   language=in_display("what you found, and why each change is proposed - "
                                       "a profile's own fields keep their own wording"))
        self.store.mark_checked(me["id"])
        return {"calls": used["calls"] - before, "web": mine["web"],
                "activity": mine["activity"], "asked": mine["asked"]}

    def _inbox_text(self) -> str:
        lines = [f"- waiting for the user: {one['title']}" for one in self.store.inbox("open")]
        lines += [f"- the user DISMISSED, {str(one['decided_at'])[:10]}: {one['title']}"
                  + (f" - {one['reason']}" if one["reason"] else "")
                  for one in self.store.inbox("decided", 30) if one["status"] == "dismissed"]
        return "\n".join(lines) or "Nothing."

    def _my_tool(self, name: str, args: Dict[str, Any], used: Dict[str, int],
                 mine: Dict[str, int]) -> Any:
        store = self.store
        if name in ("web_search", "fetch_page"):
            mine["web"] += 1
            return self._tool(name, args, used)
        me = store.me()
        try:
            if name == "record_my_activity":
                added = 0
                for item in args.get("activity") or []:
                    if not isinstance(item, dict):
                        continue
                    url = str(item.get("url") or "")
                    if not url.lower().startswith(("http://", "https://")):
                        continue              # only what a public page shows
                    if store.add_activity(me["id"], str(item.get("what") or ""),
                                          str(item.get("at") or ""), "your public page",
                                          url=url):
                        added += 1
                mine["activity"] += added
                return {"ok": True, "activity_added": added}
            if name == "propose_my_profile":
                asked, said = 0, []
                for change in args.get("changes") or []:
                    if not isinstance(change, dict):
                        continue
                    item = store.propose_for_me(
                        change, str(change.get("why") or ""),
                        [str(change.get("source") or "")] if change.get("source") else [])
                    if item:
                        asked += 1
                        said.append(item["title"])
                mine["asked"] += asked
                return {"ok": True, "put_in_the_inbox": said,
                        "note": "the rest already say so, wait already, or were dismissed"}
        except Exception as exc:          # the model reads the failure
            log.debug("a connections tool failed", exc_info=True)
            return {"error": f"{type(exc).__name__}: {exc}"[:300]}
        return {"error": f"no tool {name}"}

    def _stale(self) -> List[Dict[str, Any]]:
        out = []
        for person in self.store.people():
            if person["closeness"] < 4:
                continue
            age = _days_ago(person["checked_at"]) if person["checked_at"] else None
            if age is None or age > PROACTIVE_DAYS:
                out.append(person)
        return out[:10]

    def _room(self, used: Dict[str, int]) -> bool:
        """Room for another model call: only a runaway is stopped."""
        return used["calls"] < RUNAWAY_CALLS

    def _ask(self, messages: List[Dict[str, Any]], used: Dict[str, int], tools: Any = None,
             runner: Any = None, system: str = SYSTEM, language: Any = None) -> Any:
        response = self.models.invoke(ModelRequest(
            messages=messages, system=system, tools=tools or [], max_tokens=6000,
            task=MODEL_TASK, tool_runner=runner, language=language), role="primary")
        usage = getattr(response, "usage", None)
        with self._count_lock:
            used["calls"] += 1
            used["tokens_in"] += int(getattr(usage, "input_tokens", 0) or 0) + int(
                getattr(usage, "cached_input_tokens", 0) or 0) + int(
                getattr(usage, "cache_written_tokens", 0) or 0)
            used["tokens_out"] += int(getattr(usage, "output_tokens", 0) or 0)
        self.progress.update(calls=used["calls"])
        return response

    def _loop(self, messages: List[Dict[str, Any]], used: Dict[str, int],
              going: Callable[[], bool], tools: Any = None, handle: Any = None,
              room: Any = None, counted: Optional[Dict[str, int]] = None,
              system: str = SYSTEM, language: Any = None) -> str:
        """The model, its tools run for it, until it answers - within the
        round's budget (or the step's, `room`); out of it, asked once more,
        without tools."""
        tools = TOOLS if tools is None else tools
        handle = handle or (lambda name, arguments: self._tool(name, arguments, used))
        room = room or (lambda: self._room(used))

        def run(name: str, arguments: Dict[str, Any]) -> Any:
            return handle(name, arguments or {})

        while room() and going():
            if counted is not None:
                counted["calls"] += 1
            response = self._ask(messages, used, tools, run, system=system,
                                 language=language)
            calls = getattr(response, "tool_calls", None) or []
            if not calls:
                return str(getattr(response, "text", "") or "")
            messages.append({"role": "assistant",
                             "content": getattr(response, "raw_content", None)
                             or getattr(response, "text", "")})
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": call.id,
                 "content": json.dumps(handle(call.name, call.arguments or {}),
                                       ensure_ascii=False, default=str)[:30000]}
                for call in calls]})
        if not going():
            return ""
        messages.append({"role": "user", "content": "That is all the room for this "
                         "round: answer now, with the JSON object, from what you have."})
        return str(getattr(self._ask(messages, used, system=system, language=language),
                           "text", "") or "")

    def _tool(self, name: str, args: Dict[str, Any], used: Dict[str, int]) -> Any:
        store = self.store
        try:
            if name == "search_people":
                return {"people": [_brief(one) for one in store.search(
                    str(args.get("query") or ""), str(args.get("relationship") or ""),
                    str(args.get("tag") or ""), most=20)]}
            if name == "open_person":
                person = store.get(str(args.get("id") or ""), with_details=True)
                if person is None or person["deleted"] or person["is_self"]:
                    return {"error": "no such contact"}
                person.pop("actions", None)
                return person
            if name in ("web_search", "fetch_page"):
                with self._count_lock:
                    if used["web"] >= RUNAWAY_WEB:
                        return {"error": "the round has looked up the web "
                                         f"{RUNAWAY_WEB} times: answer with what you found"}
                    used["web"] += 1
                self.progress.update(web=used["web"])
                if name == "web_search":
                    return self.search(str(args.get("query") or ""))
                url = str(args.get("url") or "")
                said = self.fetch(url)
                for source in store.sources():
                    if source["url"].rstrip("/") == url.rstrip("/"):
                        store.source_fetched(source["id"])
                if isinstance(said, dict) and isinstance(said.get("text"), str):
                    said = dict(said, text=said["text"][:12000])
                return said
            if name == "record_findings":
                person = store.get(str(args.get("id") or ""))
                if person is None or person["deleted"] or person["is_self"]:
                    return {"error": "no such contact"}
                fields = {key: args.get(key) for key in ("org", "role", "location", "tags",
                                                         "profession", "affiliations")
                          if args.get(key)}
                if fields:
                    store.edit(person["id"], by="agent", reason="found on the public web",
                               **fields)
                profiles = 0
                for item in args.get("profiles") or []:
                    if isinstance(item, dict) and store.found_profile(
                            person["id"], str(item.get("url") or ""),
                            str(item.get("platform") or ""), str(item.get("handle") or ""),
                            str(item.get("why") or "")):
                        profiles += 1
                added = 0
                for item in args.get("activity") or []:
                    if isinstance(item, dict) and store.add_activity(
                            person["id"], str(item.get("what") or ""),
                            str(item.get("at") or "") or now(), "the public web",
                            url=str(item.get("url") or "")):
                        added += 1
                store.mark_checked(person["id"])
                return {"ok": True, "activity_added": added, "profiles_added": profiles}
            if name == "mark_checked":
                person = store.get(str(args.get("id") or ""))
                if person is None:
                    return {"error": "no such contact"}
                store.mark_checked(person["id"])
                return {"ok": True}
        except Exception as exc:          # the model reads the failure
            log.debug("a connections tool failed", exc_info=True)
            return {"error": f"{type(exc).__name__}: {exc}"[:300]}
        return {"error": f"no tool {name}"}

    def _pictured(self, items: List[Dict[str, Any]],
                  going: Callable[[], bool]) -> List[Dict[str, Any]]:
        """A PICTURE FOR EACH STORY, kept here - never loaded from the web by
        the page: one about a person, their picture; one from the web, the
        picture its page names (og:image) - the model's own pick first. A
        story that has one keeps it; one with none found has none."""
        from . import avatars
        out = []
        for item in items:
            item = dict(item)
            if not item.get("image") and item.get("people"):
                person = self.store.get(item["people"][0])
                if person and person.get("avatar"):
                    item["image"] = {"person": person["id"]}
            if not item.get("image") and self.store.folder is not None and going():
                tries = [str(one) for one in [item.get("image_url"),
                                              *(item.get("sources") or [])] if one]
                # The story's page, where the picture chosen may be named
                # whole beside the cut-down copy shown.
                around = next((str(one) for one in item.get("sources") or []
                               if str(one).startswith(("http://", "https://"))), "")
                for url in tries[:3]:
                    page = url != item.get("image_url")
                    name = self._keep_picture(url, page=page, get=self.download,
                                              avatars=avatars, around="" if page else around)
                    if name:
                        item["image"] = {"kept": name}
                        break
            out.append(item)
        return out

    def _keep_picture(self, url: str, page: bool, get: Callable[[str], Any],
                      avatars: Any, around: str = "") -> str:
        """The picture at `url` - or named by the page at it - kept in the
        connections folder; its file's name, or "" when there is none.

        WHOLE, where the page names it whole: a picture is often shown cut
        to fit ("800a", "-800x530"), the whole one named beside it - in the
        page the picture was named by, or `around`, the story's page."""
        try:
            if not url.startswith(("http://", "https://")):
                return ""
            text, base = "", url
            if page:
                data, kind = get(url)
                if not str(kind).startswith("text/html"):
                    return ""
                text = data.decode("utf-8", "replace")
                url = avatars.page_picture(text, url) or ""
                if not url:
                    return ""
            elif around.startswith(("http://", "https://")):
                try:
                    data, kind = get(around)
                    if str(kind).startswith("text/html"):
                        text, base = data.decode("utf-8", "replace"), around
                except Exception:            # the picture as chosen, then
                    text = ""
            whole = avatars.fuller_picture(text, url, base) if text else None
            for one in [whole, url]:
                if not one:
                    continue
                try:
                    data, kind = get(one)
                except Exception:
                    continue
                ext = avatars.TYPES.get(kind)
                if ext is not None and data:
                    return self.store.save_briefing_picture(data, ext)
            return ""
        except Exception:                    # one source failing is not the end
            log.debug("no picture from %s", url, exc_info=True)
            return ""

    def _current_items(self) -> List[Dict[str, Any]]:
        """The list as it stands, each item with an id - one written before
        items had ids is given one now."""
        from .store import new_id
        briefing = self.store.briefing() or {}
        out = []
        for one in briefing.get("items") or []:
            # A recommendation is made once: not kept on the list. (Its topic
            # says nothing of that - a story of the list is put on a topic's
            # page when it is about one.)
            if isinstance(one, dict) and one.get("title") and not one.get("recommended"):
                out.append(dict(one, id=one.get("id") or new_id("know"),
                                added_at=one.get("added_at") or briefing.get("at") or now()))
        return out

    def _updated(self, current: List[Dict[str, Any]], found: List[Dict[str, Any]],
                 used: Dict[str, int], work: str, today: str
                 ) -> "tuple[List[Dict[str, Any]], List[Dict[str, Any]]]":
        """THE LIST UPDATED: the model is given every item as it stands and
        what this round found, and answers with only the changes (UPDATE) -
        each applied here. Nothing on the list yet: what was found is it.
        The model not to be had: the list kept, what was found added."""
        from .store import new_id
        at = now()

        def fresh(item: Dict[str, Any]) -> Dict[str, Any]:
            return dict(item, id=new_id("know"), added_at=at, updated_at=at)

        if not current:
            items = [fresh(one) for one in found][:MOST_KNOW]
            return items, [{"op": "add", "id": one["id"], "title": one["title"]}
                           for one in items]
        if not self._room(used):
            items = (current + [fresh(one) for one in found])[:MOST_KNOW]
            return items, [{"op": "add", "id": one["id"], "title": one["title"],
                            "why": "the round's model budget was used up"}
                           for one in items[len(current):]]
        shown = [{key: one.get(key) for key in ("id", "title", "opening", "detail", "people",
                                                "names", "kind", "for", "importance", "why",
                                                "sources", "added_at")} for one in current]
        response = self._ask([{"role": "user", "content": UPDATE.format(
            current=json.dumps(shown, ensure_ascii=False, default=str)[:30000],
            found=json.dumps(found, ensure_ascii=False, default=str)[:30000]
            or "Nothing new.", work=work[:4000], today=today, most=MOST_KNOW)}], used,
            system="You keep a short list up to date, changing only what needs it.",
            language=in_display())
        said = _json_of(str(getattr(response, "text", "") or ""))
        by_id = {one["id"]: one for one in current}
        order = [one["id"] for one in current]
        changes: List[Dict[str, Any]] = []
        for change in said.get("changes") or []:
            if not isinstance(change, dict):
                continue
            op = str(change.get("op") or "").lower()
            ident = str(change.get("id") or "")
            why = str(change.get("why") or "")[:300]
            if op == "add":
                shaped = self._items([change.get("item") or {}])
                if shaped:
                    item = fresh(shaped[0])
                    by_id[item["id"]] = item
                    order.append(item["id"])
                    changes.append({"op": "add", "id": item["id"], "title": item["title"]})
            elif op == "edit" and ident in by_id:
                merged = dict(by_id[ident], **{k: v for k, v in (change.get("item") or {})
                                               .items() if k not in ("id", "added_at")})
                shaped = self._items([merged])
                if shaped:
                    by_id[ident] = dict(shaped[0], id=ident,
                                        added_at=by_id[ident].get("added_at") or at,
                                        updated_at=at)
                    changes.append({"op": "edit", "id": ident,
                                    "title": by_id[ident]["title"], "why": why})
            elif op == "delete" and ident in by_id and why:
                # Never taken off without a reason.
                changes.append({"op": "delete", "id": ident,
                                "title": by_id[ident]["title"], "why": why})
                del by_id[ident]
                order.remove(ident)
        items = [by_id[one] for one in order]
        items.sort(key=lambda one: one.get("importance", 1), reverse=True)
        return items[:MOST_KNOW], changes

    def _items(self, raw: Any) -> List[Dict[str, Any]]:
        out = []
        for one in raw or []:
            if not isinstance(one, dict) or not str(one.get("title") or "").strip():
                continue
            try:
                importance = max(1, min(5, int(one.get("importance") or 1)))
            except (TypeError, ValueError):
                importance = 1
            people = [str(p) for p in one.get("people") or [] if self.store.get(str(p))]
            out.append({"title": str(one["title"])[:160],
                        "detail": str(one.get("detail") or "")[:800],
                        # Said for the agent's own use: what it is measured by.
                        **({"english": str(one["english"])[:600]}
                           if str(one.get("english") or "").strip() else {}),
                        "people": people[:12],
                        "names": [self.store.get(p)["name"] for p in people[:12]],
                        "kind": one.get("kind") if one.get("kind") in ("across", "web")
                        else "person",
                        "for": str(one.get("for") or "general")[:160],
                        "importance": importance,
                        "why": str(one.get("why") or "")[:600],
                        # How the story begins: the link to the user's own
                        # work, said in passing rather than labelled.
                        **({"opening": str(one["opening"]).strip()[:700]}
                           if str(one.get("opening") or "").strip() else {}),
                        "sources": [str(s)[:300] for s in one.get("sources") or []][:8],
                        **({"image_url": str(one["image_url"])[:500]}
                           if str(one.get("image_url") or "").startswith("http") else {}),
                        **({"image": one["image"]} if isinstance(one.get("image"), dict)
                           else {})})
        out.sort(key=lambda one: one["importance"], reverse=True)
        return out[:20]

    def _fold_lessons(self, used: Dict[str, int]) -> None:
        """The user's changes past the most kept word for word, summarised -
        not with the lessons engine, which learns from every one of them."""
        if getattr(self.store, "lessons", None) is not None:
            return
        overflow = self.store.overflowing_actions()
        if not overflow or not self._room(used):
            return
        changes = "\n".join(
            f"- {str(one['at'])[:16]} {one['action']} {one['person_name']}"
            + (f" - reason: {one['reason']}" if one["reason"] else "")
            + (f" {json.dumps(one['after'], ensure_ascii=False)[:200]}"
               if one["action"] == "edit" else "") for one in overflow)
        response = self._ask([{"role": "user", "content": SUMMARISE.format(
            summary=self.store.lessons_summary().get("text") or "(none yet)",
            changes=changes)}], used, system="You summarise faithfully and briefly.",
            language=in_display())
        text = str(getattr(response, "text", "") or "").strip()
        if text:
            self.store.fold_actions(overflow, text)


def _brief(person: Dict[str, Any]) -> Dict[str, Any]:
    times = person.get("field_times") or {}
    return {"id": person["id"], "name": person["name"], "aliases": person["aliases"][:5],
            "relationships": person["relationships"], "closeness": person["closeness"],
            "profession": person["profession"], "professions": person.get("professions", [])[:4],
            "locations": person.get("locations", [])[:4],
            "affiliations": person["affiliations"][:4],
            "tags": person["tags"][:10],
            "org_found_at": (times.get("org") or {}).get("found_at", ""),
            "role_found_at": (times.get("role") or {}).get("found_at", ""),
            "last_activity_at": person.get("last_activity_at", ""),
            "checked_at": person["checked_at"]}


def _me_text(me: Dict[str, Any]) -> str:
    times = me.get("field_times") or {}

    def when(field: str) -> str:
        found = (times.get(field) or {}).get("found_at")
        return f" (found {str(found)[:10]})" if found else ""

    lines = [f"Name: {me['name']}{when('name')}"]
    for field, label in (("profession", "Profession"), ("location", "Place")):
        lines.append(f"{label}: {me[field] or '(none)'}{when(field) if me[field] else ''}")
    lines.append("Organisations, the main one first:" + ("" if me["affiliations"] else " (none)")
                 + when("affiliations" if "affiliations" in times else "org"))
    lines += [f"  - {one.get('role') or '?'} at {one.get('org') or '?'}"
              for one in me["affiliations"]]
    lines.append("Their public profiles:")
    lines += [f"  - {one.get('platform') or ''} {one.get('url') or one.get('handle')}"
              f" (found {str(one.get('found_at') or '')[:10]})" for one in me["accounts"]]
    recent = me.get("activity") or []
    lines.append("What they did in public, already recorded:"
                 + ("" if recent else " nothing yet."))
    lines += [f"  - {str(one['at'])[:10]}: {one['what']}" for one in recent[:15]]
    return "\n".join(lines)


def _json_of(text: str) -> Dict[str, Any]:
    text = str(text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        value = json.loads(text[start:end + 1])
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}
