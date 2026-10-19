"""What the agent may do while working on a piece of the user's work.

READ widely, WRITE in few places. It reads files anywhere under the user's
home (the application's own state excepted), and searches and reads the
web. When it carries out a stage it may also write, delete and run Python -
ONLY inside the work's own folder and the folders the user granted it:
every write goes through the runtime's path check, and the code it runs is
started in a guarded interpreter that can write nowhere else and open no
network connection. The web is read through fetch_url and web_search
alone, which only read and carry none of the user's sign-ins.

Python packages can be installed for a run - from PyPI, wheels only, into
the run's own folder - by pip running in the same guard, whose network then
reaches PyPI and nothing else (see `packages.py`). Whether the user is
asked first is theirs to choose, per run.

Every change it makes is claimed - so the listener does not hear it as the
user's - and reported, so it is recorded as the agent's own activity.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from ...models.base import ToolSpec
from ...runtime.filesystem import validate_read_path
from ...runtime.permissions import SecurityViolation
from .versions import LOGS_DIR, SLIDES_DIR

#: How much of a page or file is handed back in one call.
MOST_CHARS = 40000

#: What a page fetch identifies itself as.
AGENT = "Mozilla/5.0 (personal agent; reading for its user)"


def _schema(props: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {"type": "object", "properties": props, "required": required,
            "additionalProperties": False}


def _page_text(raw: str) -> str:
    """A web page as text: scripts and styles out, tags out, space folded."""
    raw = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h\d>|</li>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
    return re.sub(r"\n\s*\n+", "\n\n", raw).strip()


def fetch(url: str, timeout: float = 20.0) -> Dict[str, Any]:
    """A web page, read into memory - never written anywhere."""
    if not re.match(r"(?i)^https?://", url or ""):
        return {"error": "only http and https addresses can be read"}
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            kind = response.headers.get("Content-Type", "")
            body = response.read(3_000_000)
    except Exception as exc:
        return {"error": f"could not read {url}: {exc}"[:400]}
    charset = (re.search(r"charset=([\w-]+)", kind) or [None, "utf-8"])[1]
    text = body.decode(charset or "utf-8", errors="replace")
    if "html" in kind.lower() or text.lstrip().startswith("<"):
        title = re.search(r"(?is)<title[^>]*>(.*?)</title>", text)
        # Its links, as addresses that exist: what the agent may open next.
        links = []
        for href in re.findall(r'(?is)<a[^>]+href="([^"#][^"]*)"', text):
            absolute = urllib.parse.urljoin(url, html.unescape(href))
            if re.match(r"(?i)^https?://", absolute) and absolute not in links:
                links.append(absolute)
            if len(links) >= MOST_LINKS:
                break
        text = _page_text(text)
        return {"url": url, "title": html.unescape(title.group(1)).strip()
                if title else "", "text": text[:MOST_CHARS],
                "cut": len(text) > MOST_CHARS, "links": links}
    return {"url": url, "text": text[:MOST_CHARS], "cut": len(text) > MOST_CHARS}


#: How many of a page's links come back with it.
MOST_LINKS = 150
#: How long a search may be, what looks like a secret, an address in a
#: text, an address as compared - the web guard's, where both checks on the
#: web are (runtime/web_guard.py); named here as they always were.
from ...runtime.web_guard import (  # noqa: E402
    MOST_QUERY, SECRET_LIKE, URL_IN_TEXT, WebGuard, address_key)


def _brave(query: str, key: str, limit: int, timeout: float) -> Dict[str, Any]:
    """One search of Brave's Search API. A key it does not accept, or a
    limit reached, said as what it is."""
    import json as _json
    with _brave_lock:
        wait = BRAVE_SPACING - (time.time() - _brave_last[0])
        if wait > 0:
            time.sleep(wait)
        _brave_last[0] = time.time()
    address = BRAVE_ENDPOINT + "?" + urllib.parse.urlencode(
        {"q": query, "count": max(1, min(20, int(limit)))})
    request = urllib.request.Request(address, headers={
        "Accept": "application/json", "X-Subscription-Token": key, "User-Agent": AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            said = _json.loads(response.read(4_000_000).decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 422):
            return {"error": "the Brave Search key was not accepted - the user can check "
                             "it in Settings > General > Web search", "unauthorized": True}
        if exc.code == 429:
            return {"error": "Brave Search's limit for this key is reached for now (too "
                             "many searches at once, or the month's): no web search for "
                             "a while - work from what you have"}
        return {"error": f"could not search: Brave Search answered HTTP {exc.code}"}
    except Exception as exc:
        return {"error": f"could not search: {exc}"[:400]}
    results = []
    for one in ((said.get("web") or {}).get("results") or [])[:limit]:
        if not isinstance(one, dict) or not one.get("url"):
            continue
        results.append({"title": _page_text(str(one.get("title") or "")),
                        "url": str(one["url"]),
                        "snippet": _page_text(str(one.get("description") or ""))})
    return {"query": query, "results": results, "engine": "Brave Search"}


#: THE WEB SEARCH: Brave's Search API, with the user's own key (Settings >
#: General > Web search), kept in the credential store as the model keys are.
#: Without one, DuckDuckGo's page - which may ask this computer to prove it
#: is a person, and is then said to be unavailable.
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
BRAVE_KEY = "brave_search.api_key"
BRAVE_KEY_ENV = "BRAVE_SEARCH_API_KEY"
BRAVE_KEY_PAGE = "https://brave.com/search/api/"
#: Searches kept this far apart: a key's plan allows about one a second.
BRAVE_SPACING = 1.1
DUCKDUCKGO_ENDPOINT = "https://html.duckduckgo.com/html/"

_search_keys: Any = None
_brave_lock = threading.Lock()
_brave_last = [0.0]


def configure_search(credentials: Any) -> None:
    """Where the web search's key is kept: the application's credential
    store (`app.py`)."""
    global _search_keys
    _search_keys = credentials


def search_key() -> str:
    """The Brave Search key: the one stored, else one in the environment."""
    stored = ""
    if _search_keys is not None:
        try:
            stored = _search_keys.get(BRAVE_KEY) or ""
        except Exception:        # pragma: no cover - as if none
            stored = ""
    return stored or os.environ.get(BRAVE_KEY_ENV, "")


def search_status() -> Dict[str, Any]:
    """Which search is used, and whether its key is set - never the key."""
    stored = bool(_search_keys is not None and _search_keys.get(BRAVE_KEY))
    from_env = bool(os.environ.get(BRAVE_KEY_ENV))
    return {"engine": "brave" if (stored or from_env) else "duckduckgo",
            "key_set": stored, "from_environment": from_env and not stored,
            "key_page": BRAVE_KEY_PAGE}


def search(query: str, limit: int = 8, timeout: float = 20.0,
           key: Optional[str] = None) -> Dict[str, Any]:
    """Web results for a query: title, address and snippet each - from
    Brave's Search API where there is a key (`key`, else the one stored),
    else from DuckDuckGo's page."""
    key = key if key is not None else search_key()
    if key:
        return _brave(query, key, limit, timeout)
    address = (DUCKDUCKGO_ENDPOINT + "?" + urllib.parse.urlencode({"q": query}))
    request = urllib.request.Request(address, headers={"User-Agent": AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            page = response.read(2_000_000).decode("utf-8", errors="replace")
    except Exception as exc:
        return {"error": f"could not search: {exc}"[:400]}
    if status == 202 or "bots use DuckDuckGo" in page or "anomaly" in page.lower():
        # A CHECK FOR A HUMAN, not results: said as what it is. Taken for no
        # results, it read as "nothing exists" - and an agent went on to put
        # addresses together from memory.
        # And what mends it, for the user reading the log: a key of their own.
        return {"error": "the search engine took this computer for a robot and asked "
                         "for a human check: no web search is possible for now. Say so, "
                         "and work from what you have and the pages you can open. "
                         "(No Brave Search key is set: with one - Settings > General > "
                         "Web search - search goes through Brave's API instead.)"}
    results = []
    for match in re.finditer(
            r'(?is)<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
            r'.*?(?:class="result__snippet"[^>]*>(.*?)</a>|$)', page):
        link = html.unescape(match.group(1))
        target = urllib.parse.parse_qs(urllib.parse.urlparse(link).query).get("uddg")
        results.append({"title": _page_text(match.group(2)),
                        "url": target[0] if target else link,
                        "snippet": _page_text(match.group(3) or "")})
        if len(results) >= limit:
            break
    return {"query": query, "results": results}


#: The tools that change something - offered only while a stage is being
#: carried out. A job is code running: its tools go with run_python.
WRITING_TOOLS = ("write_file", "delete_file", "run_python", "install_packages",
                 "job_status", "stop_job")

#: RUN_PYTHON: how long a call waits for its code - unless asked, and at
#: most - and how much of what it printed comes back.
RUN_SECONDS = 120
RUN_MOST_SECONDS = 900
MOST_PRINTED = 20000

#: JOBS - code run with background=true: so many going at once, each for so
#: long at most; job_status waits so long at most at a time, and returns so
#: much of what the job printed so far.
MOST_JOBS = 3
JOB_SECONDS = 1800
JOB_MOST_SECONDS = 3600
JOB_MOST_WAIT = 600
JOB_TAIL = 8000

#: What the guard writes when the code ends while programs it started are
#: still running (runtime/guard.py) - and what the result then says.
LEFT_RUNNING_MARK = "[sandbox] still running when the code ended: "
LEFT_RUNNING_NOTE = (
    "The programs above were still running when your code ended, so they were "
    "ended with it: whatever code starts ends when the code ends. To run something "
    "long, give run_python a larger timeout (at most {most} seconds), or run it "
    "with background=true and wait for it with job_status.")

#: How a job runs the code it was given: what it prints kept in its log as
#: it goes (a program it starts writes there too, given sys.stdout), and a
#: stop asked for ending it.
JOB_LAUNCHER = """import os, sys, runpy, threading, time
_log = open({log!r}, "a", encoding="utf-8", buffering=1)


class _Kept:
    def __init__(self, real):
        self.real = real

    def write(self, text):
        self.real.write(text)
        _log.write(text)
        return len(text)

    def flush(self):
        self.real.flush()
        _log.flush()

    def fileno(self):
        return _log.fileno()

    def isatty(self):
        return False

    def __getattr__(self, name):
        return getattr(self.real, name)


def _watch():
    while not os.path.exists({stop!r}):
        time.sleep(0.5)
    _log.write("\\n[stopped]\\n")
    _log.flush()
    os._exit(1)


sys.stdout, sys.stderr = _Kept(sys.stdout), _Kept(sys.stderr)
threading.Thread(target=_watch, daemon=True).start()
runpy.run_path({code!r}, run_name="__main__")
"""

#: The most files a granted folder is looked over for, before and after code
#: runs, to see what it changed.
MOST_FILES_WATCHED = 20000


class WorkTools:
    """The tools of one agent in a run, bound to its runtime.

    `cards(ids)` opens cards of the work's tree and the project's record;
    `find(query, kind)` finds past stages; `changed(action, paths, said)`
    is told every change the agent makes."""

    def __init__(self, runtime: Any, experience: Any = None,
                 log: Optional[Callable[[str, Dict[str, Any]], None]] = None,
                 cards: Optional[Callable[[List[str]], Any]] = None,
                 find: Optional[Callable[[str, str], Any]] = None,
                 changed: Optional[Callable[[str, List[str], Dict[str, Any]], None]] = None,
                 install: Optional[Callable[[List[str], str], Dict[str, Any]]] = None,
                 stages: Optional[Callable[[List[str]], Any]] = None
                 ) -> None:
        self.runtime = runtime
        self.experience = experience
        self.log = log or (lambda what, said: None)
        self.cards = cards
        self.find = find
        #: `stages(ids)`: past stages opened by id (`open_stage`); None: none
        #: to open.
        self.stages = stages
        self.changed = changed or (lambda action, paths, said: None)
        #: Files no tool may write or delete: the run's approved contract.
        #: Code that changes one anyway is caught when the round is checked,
        #: and the file is put back.
        self.locked: List[Path] = []
        #: `install(packages, why)`: installs for the run - asking the user
        #: first if that is their choice. None: no packages here.
        self.install = install
        #: Where the run's packages are: first on run_python's path.
        self.packages_dir: Optional[Path] = None
        #: THE ONLY TOOLS OFFERED, when not all are: a version's slides are
        #: drawn with a few (runner.SLIDES_TOOLS). One not among them is
        #: neither offered nor run.
        self.only: Optional[tuple] = None
        #: Tools of the writing kind given even where nothing is written
        #: with tools: a reviewer that tries what was built runs code.
        self.runs_code: tuple = ()
        #: Asked while job_status waits: True ends the wait (the run is
        #: stopping).
        self.stopped: Callable[[], bool] = lambda: False
        #: THE JOBS this agent started (run_python background=true), by id.
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self._jobs_lock = threading.Lock()
        #: THE WEB GUARD (runtime/web_guard.py): the addresses that exist -
        #: found in search results, on pages read, in the user's own files
        #: and instruction, in their recorded work; never one the agent
        #: could have made up (not its own files, not its code's output) -
        #: the sites the user named, and the words read of the user's and
        #: of the web: what fetch_url opens (`check_address`). The same
        #: guard holds a client's own web tools (`ModelRequest.web_guard`).
        self.guard = WebGuard()
        self.handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = {
            "list_dir": self.list_dir, "read_file": self.read_file,
            "read_video": self.read_video, "find_files": self.find_files, "write_file": self.write_file,
            "delete_file": self.delete_file,
            "run_python": self.run_python, "web_search": self.web_search,
            "install_packages": self.install_packages,
            "fetch_url": self.fetch_url, "open_card": self.open_card,
            "search_stages": self.search_stages, "open_stage": self.open_stage,
            "job_status": self.job_status, "stop_job": self.stop_job,
            "search_records": self.search_records,
            "search_files": self.search_files,
        }
        #: The user's own records to search (context_search.py): set by the
        #: run that has them - the database they are in.
        self.records_db: Any = None
        self._records_index: Any = None
        #: THE USER'S OWN FILES, searched by their words (index/service.py) -
        #: set by the run that may: none for the single model, or for the
        #: way with no context.
        self.files_index: Any = None

    @property
    def folder(self) -> Path:
        return Path(self.runtime.policy.write_root)

    @property
    def writable(self) -> List[Path]:
        roots = getattr(self.runtime.policy, "write_roots", None)
        return [Path(one) for one in (roots or [self.runtime.policy.write_root])]

    def specs(self, writing: bool = True) -> List[ToolSpec]:
        """The tools offered: all of them while a stage is carried out, the
        reading ones while it is planned or judged. Packages only where
        something can install them."""
        every = self._specs()
        # Cards and past stages only where there are some to open or search:
        # the Experience debugger's single model has neither.
        return [one for one in every if (writing or one.name not in WRITING_TOOLS
                                         or one.name in self.runs_code)
                and (self.only is None or one.name in self.only)
                and (one.name != "install_packages" or self.install is not None)
                and (one.name != "open_card" or self.cards is not None)
                and (one.name != "search_stages" or self.find is not None)
                and (one.name != "search_records" or self.records_db is not None)
                and (one.name != "search_files" or self.files_index is not None)
                and (one.name != "open_stage" or self.stages is not None)]

    def _specs(self) -> List[ToolSpec]:
        where = ", ".join(str(one) for one in self.writable)
        return [
            ToolSpec("list_dir", "List a folder: its files and subfolders, with each "
                     "file's size - at most 400 entries. Any folder under the user's "
                     "home can be listed. What the agents may not read is left out: "
                     "the run logs, the application's state, where sign-ins and keys "
                     "are kept.",
                     _schema({"path": {"type": "string"}}, ["path"])),
            ToolSpec("read_file", "Read a file: at most 40,000 characters a "
                     "call - when \"cut\" is true there is more: read on with "
                     "from_line (the line to start at, counting from 0); \"lines\" "
                     "is how many it has. A text file as it is; a PICTURE as the words "
                     "read in it, and the picture itself where you can see pictures; a "
                     "PDF as the text of each page (\"--- page N ---\"), a scanned page "
                     "read off its picture; Word, PowerPoint and Excel as their text and "
                     "cells. Any file under the user's home can be read, except the run "
                     "logs, the application's state and where sign-ins and keys are "
                     "kept. Another binary file is read with run_python.",
                     _schema({"path": {"type": "string"},
                              "from_line": {"type": "integer"}}, ["path"])),
            ToolSpec("read_video", "Watch a VIDEO file (mp4, mov, mkv, webm, ...): "
                     "frames taken evenly over the part asked for - as pictures where "
                     "you can see them, each with the words read in it - and what is "
                     "SAID in it, line by line with its time. start and end are seconds "
                     "(the whole video unless asked; speech is read 30 minutes at a "
                     "time); frames is how many (12 unless asked, at most 40). Look "
                     "closer at a moment by asking for a short part with more frames. "
                     "The frames and the transcript are kept in the work's own folder, "
                     "under videos/, to open by their paths; a video read again is "
                     "read from there.",
                     _schema({"path": {"type": "string"},
                              "start": {"type": "number"},
                              "end": {"type": "number"},
                              "frames": {"type": "integer"},
                              "speech": {"type": "boolean"}}, ["path"])),
            ToolSpec("find_files", "Find files under a folder by name pattern "
                     "(glob, e.g. **/*.py), optionally containing some text - at most "
                     "200 found.",
                     _schema({"root": {"type": "string"},
                              "pattern": {"type": "string"},
                              "containing": {"type": "string"}},
                             ["root", "pattern"])),
            ToolSpec("write_file", "Write a text file - whole, or added to its end "
                     "with append - ONLY inside: " + where + ". A path anywhere else "
                     "is refused. Relative paths are inside the first. The contract's "
                     "own files cannot be written.",
                     _schema({"path": {"type": "string"},
                              "content": {"type": "string"},
                              "append": {"type": "boolean"}},
                             ["path", "content"])),
            ToolSpec("delete_file", "Delete a file or a folder - ONLY inside: "
                     + where + ". The folders themselves cannot be deleted.",
                     _schema({"path": {"type": "string"}}, ["path"])),
            ToolSpec("run_python", "Run Python code as a NEW process each call: only "
                     "files carry over to the next call - no variables, no imports, no "
                     "running programs. It starts in the work's own folder, may write "
                     "only inside: " + where + ", and may read files elsewhere (not "
                     "the run logs, the application's state or where sign-ins are "
                     "kept). It opens NO network connection but as the work rules say "
                     "- read pages with fetch_url. Packages installed with "
                     "install_packages import as usual. "
                     f"THE CALL WAITS for the code to end - timeout seconds, "
                     f"{RUN_SECONDS} unless you ask, at most {RUN_MOST_SECONDS} - and "
                     f"returns what it printed (the last {MOST_PRINTED:,} characters of "
                     "each of stdout and stderr) and the files it changed. WHATEVER "
                     "THE CODE STARTS ENDS WHEN IT ENDS: a program started and not "
                     "waited for is stopped with it, never left running. For anything "
                     "longer, or to go on working while it runs, set background=true: "
                     f"it runs as a JOB for up to timeout seconds ({JOB_SECONDS} unless "
                     f"you ask, at most {JOB_MOST_SECONDS}) and the call returns its "
                     "id at once - then wait for it with job_status, not with a sleep "
                     "in code. A folder it can only read is read-only to everything: "
                     "a SQLite database there in WAL mode cannot be opened even "
                     "read-only - copy it into a folder you can write first.",
                     _schema({"code": {"type": "string"},
                              "timeout": {"type": "integer"},
                              "background": {"type": "boolean"}}, ["code"])),
            ToolSpec("job_status", "A job started with run_python background=true: "
                     "whether it is still running and for how long, what it has "
                     f"printed so far (the last {JOB_TAIL:,} characters) - and, once "
                     "it has ended, its whole result, as run_python gives one. With "
                     f"wait (seconds, at most {JOB_MOST_WAIT}) it waits until the job "
                     "ends or that time passes: the way to wait for one. With no job, "
                     "every job of this run. A job is stopped when the run ends.",
                     _schema({"job": {"type": "string"},
                              "wait": {"type": "integer"}}, [])),
            ToolSpec("stop_job", "Stop a job started with run_python "
                     "background=true, and every program it started.",
                     _schema({"job": {"type": "string"}}, ["job"])),
            ToolSpec("install_packages", "Install Python packages from PyPI for "
                     "this run, so run_python can import them - into this run's own "
                     "folder. Each one a plain name with an optional version (pandas, "
                     "numpy>=1.26, scikit-learn==1.5.*) - no addresses, paths or "
                     "options. Wheels only: nothing is built from source. Ask for "
                     "everything you need in one call, and say why. Where the user "
                     "approves each request, the call waits for their answer. pip run "
                     "from code cannot reach PyPI.",
                     _schema({"packages": {"type": "array",
                                           "items": {"type": "string"}},
                              "why": {"type": "string"}}, ["packages", "why"])),
            ToolSpec("web_search", "Search the web: up to 8 results, each a title, "
                     "an address and a snippet. A query is words - at most "
                     f"{MOST_QUERY} characters; one that carries anything like a "
                     "password, a key, a token or encoded data is refused.",
                     _schema({"query": {"type": "string"}}, ["query"])),
            ToolSpec("fetch_url", "Read a web page as text - at most 40,000 "
                     "characters (\"cut\" says there was more) - with up to 150 of its "
                     "links. Only an address that already exists is opened: one from a "
                     "search result, a page's links, the user's files or instruction, "
                     "or their recorded work - or the front page of a site one came "
                     "from. An address you put together yourself is refused; search "
                     "for it instead.",
                     _schema({"url": {"type": "string"}}, ["url"])),
            ToolSpec("open_card", "Open cards by their ids - of this work's "
                     "tree or the project's record: what each says, the cards "
                     "under it, and every attempt at it.",
                     _schema({"ids": {"type": "array", "items": {"type": "string"}}},
                             ["ids"])),
            ToolSpec("search_stages", "Find past stages like a step: by its "
                     "kind (goal, plan, execution, evaluation, adjustment, "
                     "delivery) and its words - IN ENGLISH, as stages are "
                     "keyworded. Returns the rules they show - the user's "
                     "principles, patterns and natures, each whole with what "
                     "shows it - and the stages as a list of lines, each with "
                     "why it was found.",
                     _schema({"query": {"type": "string"},
                              "kind": {"type": "string"}}, ["query"])),
            ToolSpec("search_records", "Search the user's own records - what they "
                     "did (their activity), their tasks and projects, and the stages "
                     "of their past work - by words IN ENGLISH, as the records are "
                     "kept. Returns the matches, the most relevant and recent first, "
                     "each with when it was and how relevant it is.",
                     _schema({"keywords": {"type": "array", "items": {"type": "string"}}},
                             ["keywords"])),
            ToolSpec("search_files", "Search the user's OWN FILES - every folder of "
                     "their Work projects, and the folders they chose - by WORDS, in any "
                     "language: the best pieces first, each with its file, the line it "
                     "starts at and the lines there, and when the file was last changed; "
                     "a file found by its name alone has no lines. Open one with "
                     "read_file for the rest. Look here for how the user did something "
                     "like this before.",
                     _schema({"query": {"type": "string"},
                              "limit": {"type": "integer"}}, ["query"])),
            ToolSpec("open_stage", "Open past stages by their ids (from the "
                     "experience you were shown, or search_stages): each one's "
                     "key information - what it was an attempt at, what was "
                     "known, what was decided and why, the user's own words, how "
                     "it was judged, what it produced and referred to, and the "
                     "stages linked to it. At most 6 at a time.",
                     _schema({"ids": {"type": "array", "items": {"type": "string"}}},
                             ["ids"])),
        ]

    def call(self, name: str, arguments: Dict[str, Any]) -> Any:
        handler = self.handlers.get(name)
        if handler is None or (self.only is not None and name not in self.only):
            return {"error": f"unknown tool {name}"}
        try:
            said = handler(arguments or {})
        except SecurityViolation as exc:
            said = exc.to_dict()
        except Exception as exc:
            said = {"error": f"{type(exc).__name__}: {exc}"[:600]}
        self.log(name, {"arguments": arguments, "result": said})
        return said

    # -- reading ---------------------------------------------------------
    def _readable(self, path: str) -> Path:
        return Path(validate_read_path(path, self.runtime.policy))

    def list_dir(self, args: Dict[str, Any]) -> Any:
        where = self._readable(str(args.get("path") or "."))
        if not where.is_dir():
            return {"error": f"not a folder: {where}"}
        entries = []
        from ...runtime.filesystem import (is_app_code, is_closed, is_credential_store,
                                           is_sealed)
        for child in sorted(where.iterdir())[:400]:
            # A folder sealed from this agent - another way's, in the
            # Experience debugger - is not listed at all; nor, for those
            # ways, the application's own code; nor, for any, the run logs.
            if is_sealed(child, self.runtime.policy) \
                    or is_app_code(child, self.runtime.policy) \
                    or is_closed(child, self.runtime.policy) \
                    or is_credential_store(child, self.runtime.policy):
                # Nor where the user's sign-ins are kept: AppData, .ssh ...
                continue
            try:
                entries.append({"name": child.name, "folder": child.is_dir(),
                                "bytes": child.stat().st_size if child.is_file() else None})
            except OSError:
                continue
        return {"path": str(where), "entries": entries}

    # -- what may leave ----------------------------------------------------------
    def know_addresses(self, text: str, source: str = "user", where: str = "") -> None:
        """Every address in `text` is one that exists, and may be opened -
        and its words are the user's ("user": their instruction, files and
        recorded work) or public ("web", read on the site `where`)."""
        self.guard.know(text, source, where)

    @property
    def known_addresses(self) -> set:
        return self.guard.known_addresses

    @property
    def known_hosts(self) -> set:
        return self.guard.known_hosts

    def _the_agents_own(self, where: Path) -> bool:
        """Whether a file is somewhere the agent can write - and so may hold
        an address it wrote there itself."""
        try:
            resolved = where.resolve()
        except OSError:
            return True
        for root in self.writable:
            try:
                resolved.relative_to(Path(root).resolve())
                return True
            except (ValueError, OSError):
                continue
        return False

    def check_address(self, url: str) -> Optional[str]:
        """Why this address may not be opened, or None.

        NOTHING READ LEAVES IN AN ADDRESS. Asking for a page sends its
        address to the site, so an address the agent composed - a site's
        name with what it read from the user's files put after it - is a
        way to send those files to anyone, and a page it read may tell it
        to do exactly that. So only an address that already exists is
        opened, or the front page of a site one came from - and, since the
        user widened it (2026-10-18), any page of a site they named or of a
        reference site, a known address made shorter, and a short address
        on a known site with nothing of theirs in it (runtime/web_guard.py)."""
        return self.guard.check_address(url)

    def check_query(self, query: str) -> Optional[str]:
        """Why this search may not be sent, or None: too long, or carrying
        something that looks like a key or an encoded payload."""
        return self.guard.check_query(query)

    def read_video(self, args: Dict[str, Any]) -> Any:
        """A video, read on this computer into frames and what is said -
        written only into the work's own folder (runtime/video.py)."""
        where = self._readable(str(args.get("path") or ""))
        from ...runtime import video
        if not video.is_video(where):
            return {"error": f"{where.name} is not a video this reads "
                             f"({', '.join(video.VIDEOS)})"}
        from ...runtime.filesystem import validate_write_path
        home = Path(validate_write_path(str(self.folder / "videos"), self.runtime.policy))
        refused = self._refuse_locked(home)
        if refused:
            return refused

        def seconds(key: str) -> Optional[float]:
            try:
                return float(args[key]) if args.get(key) is not None else None
            except (TypeError, ValueError):
                return None
        try:
            from ...runtime import documents
            said = video.read_video(where, home.parent, start=seconds("start") or 0.0,
                                    end=seconds("end"), frames=int(args.get("frames") or 0)
                                    or video.FRAMES,
                                    speech=args.get("speech") is not False,
                                    hiding=documents._hides(), stopped=self.stopped)
        except Exception as exc:
            return {"error": f"could not read {where.name}: {exc}"[:400]}
        # What it wrote is the agent's own, not the user's doing.
        self._claim([str(one) for one in Path(said["folder"]).iterdir()])
        return video.for_model(said, MOST_CHARS)

    def read_file(self, args: Dict[str, Any]) -> Any:
        where = self._readable(str(args.get("path") or ""))
        from ...runtime import video
        if video.is_video(where):
            # A video is watched, not read as bytes.
            return self.read_video({"path": str(where)})
        from ...runtime import documents
        if documents.needs_reading(where):
            # A picture or a document: read once, kept as a copy, read from
            # the copy after (runtime/documents.py).
            try:
                said = documents.read_for_model(where, int(args.get("from_line") or 1),
                                                MOST_CHARS)
            except Exception as exc:
                return {"error": f"could not read {where.name}: {exc}"[:400]}
            if not self._the_agents_own(where):
                self.know_addresses(said.get("text") or "")
            return said
        text = where.read_text(encoding="utf-8", errors="replace")
        # The user's own files are where real addresses are; the agent's
        # own folders are where it could have put one.
        if not self._the_agents_own(where):
            self.know_addresses(text)
        start = max(0, int(args.get("from_line") or 1) - 1)
        lines = text.splitlines()[start:]
        body = "\n".join(lines)
        return {"path": str(where), "from_line": start + 1,
                "text": body[:MOST_CHARS], "cut": len(body) > MOST_CHARS,
                "lines": len(text.splitlines())}

    def find_files(self, args: Dict[str, Any]) -> Any:
        root = self._readable(str(args.get("root") or "."))
        pattern = str(args.get("pattern") or "*")
        needle = str(args.get("containing") or "")
        found = []
        for path in root.glob(pattern):
            if len(found) >= 200:
                break
            if not path.is_file():
                continue
            try:
                self._readable(str(path))
            except SecurityViolation:
                continue
            if needle:
                try:
                    if needle not in path.read_text(encoding="utf-8",
                                                    errors="ignore"):
                        continue
                except OSError:
                    continue
            found.append(str(path))
        return {"root": str(root), "files": found}

    # -- writing: only here ------------------------------------------------
    def _claim(self, paths: List[str]) -> None:
        """The agent's own writing: not heard as the user's."""
        from ...common.agent_writes import mark_written
        for one in paths:
            mark_written(one)

    def _refuse_locked(self, target: Path) -> Optional[Dict[str, Any]]:
        target = Path(target).resolve()
        # And the run logs the policy closes, whoever made these tools.
        closed = list(getattr(self.runtime.policy, "closed_paths", None) or [])
        for kept in [*self.locked, *closed]:
            kept = Path(kept).resolve()
            # The file or folder itself, a folder holding it, or - for a
            # locked folder, the run's versions - anything inside it.
            if target == kept or target in kept.parents or kept in target.parents:
                if kept.name == ".versions":
                    return {"error": "that is the run's saved versions: they are "
                                     "locked and cannot be written or deleted"}
                if kept.name == LOGS_DIR:
                    return {"error": "that is the project's run logs - the record "
                                     "of what happened: every run's full transcript "
                                     "and live session. They are locked and cannot "
                                     "be written or deleted"}
                if kept.name == SLIDES_DIR:
                    return {"error": "that is the slides of the run's versions: each is "
                                     "drawn when its version is saved, and kept with it - "
                                     "they cannot be written or deleted from here"}
                if kept.name == ".session":
                    return {"error": "that is the run's live session - the record "
                                     "of what happened: it is locked and cannot be "
                                     "written or deleted"}
                return {"error": f"{kept.name} is the approved contract: it is "
                                 "locked and cannot be written or deleted"}
        return None

    def write_file(self, args: Dict[str, Any]) -> Any:
        from ...runtime.filesystem import validate_write_path
        target = validate_write_path(str(args.get("path") or ""),
                                     self.runtime.policy)
        refused = self._refuse_locked(Path(target))
        if refused:
            return refused
        self._claim([str(target)])
        written = self.runtime.write_text(str(args.get("path") or ""),
                                          str(args.get("content") or ""),
                                          append=bool(args.get("append")))
        self._claim([str(written)])
        said = {"written": str(written)}
        self.changed("wrote", [str(written)], said)
        return said

    def delete_file(self, args: Dict[str, Any]) -> Any:
        from ...runtime.filesystem import validate_write_path
        target = Path(validate_write_path(str(args.get("path") or ""),
                                          self.runtime.policy))
        if any(target.resolve() == Path(one).resolve() for one in self.writable):
            return {"error": "a folder the work may write in is not deleted"}
        refused = self._refuse_locked(target)
        if refused:
            return refused
        if not target.exists():
            return {"error": f"nothing at {target}"}
        gone = [str(one) for one in target.rglob("*")] if target.is_dir() else []
        gone.append(str(target))
        self._claim(gone)
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        said = {"deleted": str(target), "files": len(gone)}
        self.changed("deleted", gone, said)
        return said

    def _look_over(self) -> Dict[str, tuple]:
        """Every file in the folders it may write, with when it changed and
        how big it is - to see what code changed."""
        seen: Dict[str, tuple] = {}
        work_root = Path(self.runtime.policy.write_root)
        for root in self.writable:
            for dirpath, dirnames, filenames in os.walk(root):
                # The runtime's scratch, the installed packages and the saved
                # versions: none is the work, and each can be thousands of
                # files. Nor are the logs - the project's run logs at the top
                # of the work's folder, an older run's live session in its
                # own - which the application, not the code, writes to while
                # the code runs.
                dirnames[:] = [d for d in dirnames
                               if d not in (".agent_runtime", ".packages", ".versions",
                                            ".session")
                               and not (d == LOGS_DIR and Path(dirpath) == work_root)]
                for name in filenames:
                    whole = os.path.join(dirpath, name)
                    try:
                        st = os.stat(whole)
                    except OSError:
                        continue
                    seen[whole] = (st.st_mtime_ns, st.st_size)
                    if len(seen) >= MOST_FILES_WATCHED:
                        return seen
        return seen

    def run_python(self, args: Dict[str, Any]) -> Any:
        # A job only where it can be waited for: not among a version's
        # slides' few tools (`only`), where the code is waited for as it is.
        if args.get("background") and (self.only is None or "job_status" in self.only):
            return self._start_job(str(args.get("code") or ""), args.get("timeout"))
        timeout = max(5, min(RUN_MOST_SECONDS, int(args.get("timeout") or RUN_SECONDS)))
        before = self._look_over()
        result = self.runtime.run_python_code(str(args.get("code") or ""),
                                              timeout=timeout, sys_path=self._packages())
        return self._ran(result, before)

    def _packages(self) -> Optional[List[Path]]:
        return [self.packages_dir] if self.packages_dir is not None \
            and self.packages_dir.is_dir() else None

    def _ran(self, result: Any, before: Dict[str, tuple]) -> Dict[str, Any]:
        """What code that ran comes back as: what it printed, what it
        changed - and what it left running, which was ended with it."""
        after = self._look_over()
        touched = sorted(set(path for path in after if before.get(path) != after[path])
                         | (set(before) - set(after)))
        self._claim(touched)
        said = result.to_dict() if hasattr(result, "to_dict") else dict(
            vars(result))
        left = []
        if isinstance(said.get("stderr"), str) and LEFT_RUNNING_MARK in said["stderr"]:
            kept = []
            for line in said["stderr"].splitlines(keepends=True):
                if line.startswith(LEFT_RUNNING_MARK):
                    left.append(line[len(LEFT_RUNNING_MARK):].strip())
                else:
                    kept.append(line)
            said["stderr"] = "".join(kept)
        for name in ("stdout", "stderr"):
            if isinstance(said.get(name), str):
                said[name] = said[name][-MOST_PRINTED:]
        said["files_changed"] = touched[:200]
        if left:
            said["ended_with_the_code"] = left[:10]
            said["note"] = LEFT_RUNNING_NOTE.format(most=RUN_MOST_SECONDS)
        # STILL BEING MADE READABLE: the code ran, but could not read there.
        from ...runtime.isolation import reading_not_ready
        waiting = reading_not_ready()
        if waiting:
            said["reading"] = (
                "Code cannot read outside the work's own folders yet: "
                + "; ".join(f"{one['root']} is still being made readable "
                            f"({one['done']}/{one['total'] or '?'})" for one in waiting)
                + ". Read those files with read_file meanwhile, or try again later.")
        self.changed("ran code", touched, {"files_changed": len(touched)})
        return said

    # -- jobs: code that runs while the agent goes on ---------------------------------
    def _start_job(self, code: str, timeout: Any) -> Dict[str, Any]:
        """A JOB: the code run as run_python runs it - the same process, the
        same sandbox, the same folders - on a thread of its own, the call
        returning its id at once. What it prints is kept in its log as it
        goes; it ends when it ends, when its time is up, when stop_job
        stops it, or when the run ends."""
        seconds = max(5, min(JOB_MOST_SECONDS, int(timeout or JOB_SECONDS)))
        with self._jobs_lock:
            going = [one for one in self.jobs.values() if one["status"] == "running"]
            if len(going) >= MOST_JOBS:
                return {"error": f"{MOST_JOBS} jobs are running already ("
                                 + ", ".join(one["id"] for one in going)
                                 + "): wait for one with job_status, or stop one"}
            # Numbered as they start - job_1, job_2 - the id a model asking
            # in the same turn as it starts one would guess; each in a folder
            # of its own, which a job of an earlier session never shares.
            ident = f"job_{len(self.jobs) + 1}"
            folder = self.folder / ".agent_runtime" / "jobs" / f"{ident}-{uuid.uuid4().hex[:8]}"
            job = {"id": ident, "status": "running", "started": time.time(), "ended": 0.0,
                   "timeout": seconds, "folder": folder, "result": None}
            self.jobs[ident] = job
        try:
            self.runtime.mkdir(folder)
            self.runtime.write_text(folder / "code.py", code)
            self.runtime.write_text(folder / "output.log", "")
        except Exception as exc:
            with self._jobs_lock:
                self.jobs.pop(ident, None)
            return {"error": f"the job could not be set up: {exc}"[:400]}
        launcher = JOB_LAUNCHER.format(log=str(folder / "output.log"),
                                       stop=str(folder / "stop"), code=str(folder / "code.py"))

        def run() -> None:
            before = self._look_over()
            try:
                result = self.runtime.run_python_code(launcher, timeout=seconds,
                                                      sys_path=self._packages())
                said = self._ran(result, before)
            except Exception as exc:          # said, as a result is
                said = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:600]}
            stopped = (folder / "stop").exists()
            with self._jobs_lock:
                job["result"] = said
                job["ended"] = time.time()
                job["status"] = ("stopped" if stopped else "timed out" if said.get("timed_out")
                                 else "done" if said.get("ok") else "failed")

        threading.Thread(target=run, name=f"job-{ident}", daemon=True).start()
        return {"job": ident, "status": "running", "timeout": seconds,
                "note": f"Running in the background. Wait for it with job_status "
                        f"(wait up to {JOB_MOST_WAIT} seconds at a time); stop it with "
                        "stop_job. It is stopped when this run ends."}

    def _job_said(self, job: Dict[str, Any]) -> Dict[str, Any]:
        ended = job["ended"] or time.time()
        said: Dict[str, Any] = {"job": job["id"], "status": job["status"],
                                "seconds": round(ended - job["started"], 1),
                                "timeout": job["timeout"]}
        if job["status"] == "running":
            try:
                text = (job["folder"] / "output.log").read_text(encoding="utf-8",
                                                                 errors="replace")
            except OSError:
                text = ""
            said["printed_so_far"] = text[-JOB_TAIL:]
        else:
            said["result"] = job["result"]
        return said

    def job_status(self, args: Dict[str, Any]) -> Any:
        ident = str(args.get("job") or "").strip()
        if not ident:
            with self._jobs_lock:
                every = list(self.jobs.values())
            return {"jobs": [{"job": one["id"], "status": one["status"],
                              "seconds": round((one["ended"] or time.time())
                                               - one["started"], 1)} for one in every]}
        job = self.jobs.get(ident)
        if job is None:
            return self._no_such_job(ident)
        end = time.time() + max(0, min(JOB_MOST_WAIT, int(args.get("wait") or 0)))
        while job["status"] == "running" and time.time() < end and not self.stopped():
            time.sleep(0.5)
        return self._job_said(job)

    def stop_job(self, args: Dict[str, Any]) -> Any:
        ident = str(args.get("job") or "").strip()
        job = self.jobs.get(ident)
        if job is None:
            return self._no_such_job(ident)
        if job["status"] == "running":
            self._ask_to_stop(job)
            end = time.time() + 15
            while job["status"] == "running" and time.time() < end:
                time.sleep(0.2)
        return self._job_said(job)

    def _no_such_job(self, ident: str) -> Dict[str, Any]:
        """A job asked for that this run has not got - with the ones it has."""
        with self._jobs_lock:
            known = [f"{one['id']} ({one['status']})" for one in self.jobs.values()]
        return {"error": f"no job {ident or '(none named)'} in this run - "
                         + ("its jobs: " + ", ".join(known) if known else "it has none")}

    def _ask_to_stop(self, job: Dict[str, Any]) -> None:
        try:
            self.runtime.write_text(job["folder"] / "stop", "stop")
        except Exception:        # pragma: no cover - its time runs out instead
            pass

    def stop_jobs(self, wait: float = 15.0) -> None:
        """Every job still running, stopped - the run is ending."""
        with self._jobs_lock:
            going = [one for one in self.jobs.values() if one["status"] == "running"]
        for job in going:
            self._ask_to_stop(job)
        end = time.time() + wait
        while time.time() < end and any(one["status"] == "running" for one in going):
            time.sleep(0.2)

    def install_packages(self, args: Dict[str, Any]) -> Any:
        if self.install is None:
            return {"error": "packages cannot be installed here"}
        packages = args.get("packages")
        if isinstance(packages, str):
            packages = [packages]
        return self.install(list(packages or []), str(args.get("why") or ""))

    # -- the web, in memory --------------------------------------------------
    def web_search(self, args: Dict[str, Any]) -> Any:
        query = str(args.get("query") or "")
        refused = self.check_query(query)
        if refused:
            self.log("search refused", {"query": query[:200], "why": refused})
            return {"error": refused}
        said = search(query)
        self.changed("searched the web", [], {"query": query})
        for one in said.get("results") or []:
            self.know_addresses(" ".join(str(one.get(key) or "")
                                         for key in ("url", "title", "snippet")), "web", "search")
        return said

    def fetch_url(self, args: Dict[str, Any]) -> Any:
        url = str(args.get("url") or "")
        refused = self.check_address(url)
        if refused:
            self.log("page refused", {"url": url[:300], "why": refused})
            return {"error": refused}
        said = fetch(url)
        self.changed("read a page", [], {"url": url})
        self.know_addresses(" ".join([str(said.get("url") or ""), str(said.get("text") or "")]
                                     + list(said.get("links") or [])), "web",
                            urllib.parse.urlsplit(str(said.get("url") or url)).netloc)
        return said

    # -- the record ------------------------------------------------------------
    def open_card(self, args: Dict[str, Any]) -> Any:
        if self.cards is None:
            return {"error": "no cards to open"}
        said = self.cards([str(one) for one in args.get("ids") or []][:6])
        # The user's recorded work: the pages it was done on exist.
        self.know_addresses(json.dumps(said, ensure_ascii=False, default=str))
        return said

    def open_stage(self, args: Dict[str, Any]) -> Any:
        if self.stages is None:
            return {"error": "no stages to open"}
        said = self.stages([str(one) for one in args.get("ids") or []][:6])
        self.know_addresses(json.dumps(said, ensure_ascii=False, default=str))
        return said

    def search_records(self, args: Dict[str, Any]) -> Any:
        """The user's records, by their English words (context_search.py)."""
        if self.records_db is None:
            return {"error": "no records to search"}
        from .context_search import Index, records
        if self._records_index is None:
            self._records_index = Index(records(self.records_db))
        found = self._records_index.search([str(one) for one in args.get("keywords") or []])
        return {"found": [{"kind": one["kind"], "title": one["title"],
                           "detail": one.get("detail", ""), "at": one.get("at"),
                           "relevance": one["relevance"]} for one in found[:20]]}

    def search_files(self, args: Dict[str, Any]) -> Any:
        """The user's own files, by their words (the local index): only
        what this agent may read is ever given - the same rules as list_dir
        and read_file."""
        if self.files_index is None:
            return {"error": "no files to search"}
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "say what to search for"}
        try:
            limit = max(1, min(20, int(args.get("limit") or 8)))
        except (TypeError, ValueError):
            limit = 8
        found = self.files_index.search(query, allowed=self._may_read, limit=limit)
        for one in found:
            # The user's own files: where real addresses are.
            self.know_addresses(one.get("text") or "")
        return {"found": found} if found else {"found": [], "said": "nothing found for "
                                                                    "these words"}

    def _may_read(self, path: str) -> bool:
        """Whether this agent may read a file - as read_file decides, and as
        list_dir leaves out what it may not see."""
        from ...runtime.filesystem import (is_app_code, is_closed, is_credential_store,
                                           is_sealed)
        try:
            where = self._readable(path)
        except Exception:
            return False
        policy = self.runtime.policy
        return not (is_sealed(where, policy) or is_app_code(where, policy)
                    or is_closed(where, policy) or is_credential_store(where, policy))

    def search_stages(self, args: Dict[str, Any]) -> Any:
        if self.find is None:
            return {"error": "no stages to search"}
        said = self.find(str(args.get("query") or ""), str(args.get("kind") or ""))
        self.know_addresses(json.dumps(said, ensure_ascii=False, default=str))
        return said


def compact_experience(said: Dict[str, Any]) -> Dict[str, Any]:
    """RETRIEVED EXPERIENCE, AS A MODEL IS GIVEN IT: first the RULES - the
    user's principles, patterns and natures - each WHOLE, with what shows it
    (a nature's cases, a pattern's natures, a principle's patterns); then
    the STAGES as an INDEX - a line each: what it was, where, and why it was
    found - to open with `open_stage` for what was decided, why, and how it
    was judged. Every stage given whole crowded the rules out: cut at its
    length, the rules - after them - were never seen at all."""
    rules: Dict[str, List[Dict[str, Any]]] = {"principles": [], "patterns": [], "natures": []}
    for rule in said.get("rules") or []:
        one = {"id": rule.get("id", ""), "statement": rule.get("statement", ""),
               "detail": rule.get("detail", ""), "support": rule.get("support")}
        if rule.get("last_active"):
            one["last_active"] = rule["last_active"]
        # How sure the lessons engine is of a nature: follow, or lean on it.
        if rule.get("how_sure"):
            one["how_sure"] = rule["how_sure"]
        if rule.get("examples"):
            one["examples"] = rule["examples"]
        rules[rule["kind"] + "s"].append(one)
    stages = []
    for chain in said.get("chains") or []:
        for s in chain.get("stages") or []:
            card = s.get("card") or {}
            stages.append({
                "id": s.get("id", ""), "kind": s.get("kind", ""),
                "found": s.get("found", ""), "why_found": s.get("why") or [],
                "card": card.get("title", ""), "level": card.get("level", ""),
                "summary": _one_line(s.get("summary") or "", STAGE_LINE_CHARS)})
    return {"rules": rules, "stages": stages,
            "open": "open_stage with stage ids gives each one's key information: "
                    "what was decided and why, the user's own words, how it was "
                    "judged, what it produced and referred to."}


#: How much of a stage's summary its line in the index shows.
STAGE_LINE_CHARS = 220


def _one_line(text: str, most: int) -> str:
    one = " ".join(str(text or "").split())
    return one if len(one) <= most else one[:most - 1] + "\u2026"


def experience_text(found: Dict[str, Any], most: int) -> str:
    """Retrieved experience as JSON, within `most` characters - never cut in
    the middle: what does not fit is left out whole, the stages' lines
    first (each can still be opened), then the rules the step is least like.
    The rules are what the model judges by; they are the last to go."""
    found = json.loads(json.dumps(found or {}, ensure_ascii=False, default=str))

    def size() -> int:
        return len(json.dumps(found, ensure_ascii=False, default=str))

    stages = found.get("stages") or []
    while stages and size() > most:
        stages.pop()
    rules = found.get("rules") or {}
    for kind in ("natures", "patterns", "principles"):
        ones = rules.get(kind) or []
        while ones and size() > most:
            ones.pop()
    return json.dumps(found, ensure_ascii=False, default=str)


def stage_key_info(stage: Dict[str, Any], card: Optional[Dict[str, Any]] = None,
                   links: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """A STAGE OPENED: its key information - what it was an attempt at, what
    was known, what was decided and why, the user's own words, how it was
    judged, what it produced and what it referred to, and the stages linked
    to it. Not every field it keeps: what a model deciding the same way now
    needs."""
    work = stage.get("work") or {}
    decision = work.get("decision") or {}
    output = work.get("output") or {}
    evaluation = stage.get("evaluation") or {}
    return {
        "id": stage.get("id", ""), "kind": stage.get("kind", ""),
        "medium": stage.get("medium", ""), "summary": stage.get("summary", ""),
        "attempt_at": {key: (card or {}).get(key) for key in
                       ("title", "statement", "level", "status", "checked_by",
                        "specification") if (card or {}).get(key)},
        "input": [{key: one.get(key) for key in ("what", "from") if one.get(key)}
                  for one in work.get("input") or [] if isinstance(one, dict)][:10],
        "decision": {key: decision.get(key) for key in
                     ("about", "options", "chose", "by", "why", "rejected")
                     if decision.get(key)},
        "output": {key: output.get(key) for key in ("what", "design", "specification")
                   if output.get(key)},
        "judged": {key: evaluation.get(key) for key in ("verdict", "criteria")
                   if evaluation.get(key)},
        "user_words": [str(one) for one in evaluation.get("words") or []][:10],
        "references": [{key: one.get(key) for key in ("what", "location", "material")
                        if one.get(key)}
                       for one in work.get("references") or [] if isinstance(one, dict)][:10],
        "linked": [dict(one) for one in links][:12],
    }