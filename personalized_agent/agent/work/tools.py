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
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ...models.base import ToolSpec
from ...runtime.filesystem import validate_read_path
from ...runtime.permissions import SecurityViolation
from . import fetching, notebooks
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
WRITING_TOOLS = ("write_file", "edit_file", "edit_notebook", "delete_file", "run_python",
                 "run_tests",
                 "run_command", "install_packages", "download", "git_fetch",
                 "job_status", "stop_job", "wait_for", "run_outside",
                 "request_approval", "hand_over")
#: A job's own tools: whoever may start one (run_python, run_tests) may look
#: at it, wait for it and stop it - even where nothing else is written.
JOB_TOOLS = ("job_status", "stop_job", "wait_for")

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
JOB_TAIL = 8000
#: What job_status says of a job still running (the user, 2026-10-25: seen in
#: try 6, half its calls only "running" - the model asking again and again),
#: and what a second call waiting on a job one is waiting on already is told.
STILL_RUNNING_NOTE = (
    "Still running. Do not ask again and again: when there is nothing else to do, "
    "wait_for job={job} - ONE call, which returns when the job ends (or, with output, "
    "a regular expression, when it prints the line you are after); meanwhile go on "
    "with other work - every tool's result says when a job ended (jobs_ended).")
ALREADY_WAITING_NOTE = (
    "Already being waited for: another call of yours has been waiting on {job} for "
    "{seconds}s, and answers when it ends or that wait is over. This call did not wait "
    "- do not wait twice for the same job.")
#: WAIT_FOR (the user, 2026-10-25: watched, as Claude Code's Monitor does):
#: how long one call waits at most for a file or a server - then it says it
#: is still waiting, and the agent decides whether to wait again - and how
#: often it looks. WAITING ON A JOB HAS NO LIMIT OF ITS OWN (the user,
#: 2026-10-25, after try 6 - about twenty calls a way came back only to say
#: "still running", each a whole model turn): it lasts until the job ends,
#: which its own time bounds - WAIT_JOB_MOST only in case one never says so.
#: Nothing cuts a long wait short: Claude Code's watch counts an agent's tool
#: at work as no silence, and its and Codex's tool timeouts are a day.
WAIT_MOST = 300
WAIT_JOB_MOST = JOB_MOST_SECONDS + 60
WAIT_LOOK = 1.0
#: How often a job's own server is tried, from inside the sandbox.
WAIT_URL_LOOK = 4.0

#: RUN_COMMAND (the user, 2026-10-25: commands, as Claude Code runs them -
#: in the same sandbox): the shells a command may run in, the first the one
#: used unless another is asked for. ON WINDOWS cmd alone, measured in the
#: AppContainer: Windows PowerShell cannot find its own file system there
#: (the drive's root is not open to a container), so it starts no program
#: and knows no folder; Git Bash's runtime does not even start (0xC0000142).
SHELLS_WINDOWS = ("cmd",)
SHELLS_POSIX = ("bash", "sh")
#: How a command runs: written to a script of its own in the work's runtime
#: folder - no quoting to get wrong - and run by its shell from code run as
#: run_python's is, so every rule of the sandbox holds for it and for all it
#: starts. What it prints, both streams as one, line by line in UTF-8 (a
#: line that is not, in the system's own code page).
COMMAND_CODE = """import os, subprocess, sys
env = dict(os.environ)
env.update({env!r})
for name, first in {prepend!r}.items():
    env[name] = os.pathsep.join([first] + [one for one in [env.get(name, "")] if one])
child =subprocess.Popen({argv!r}, cwd={cwd!r}, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
for line in child.stdout:
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError:
        text = line.decode("oem" if os.name == "nt" else "latin-1", "replace")
    sys.stdout.write(text.replace("\\r\\n", "\\n"))
    sys.stdout.flush()
sys.exit(child.wait())
"""
#: PYTHON A COMMAND STARTS does not start through the guard, so it is without
#: the guard's mending of os.mkdir(path, 0o700) on Windows (runtime/guard.py):
#: in the AppContainer pytest's tmp_path then made folders the container is
#: refused (measured, try 6: PermissionError on pytest-N\.lock, and the old
#: ones never cleaned). That mending alone is given to it - a sitecustomize
#: first on PYTHONPATH, which then loads whatever sitecustomize comes after it.
COMMAND_PYTHON_FOLDER = "python"
COMMAND_PYTHON_FIX = '''"""Given by the agent's sandbox to Python started by a command: on
Windows, os.mkdir(path, 0o700) makes a folder only its user may enter - which
a process in an AppContainer is refused - so 0o700 is made the plain mode."""
import os
import sys

if os.name == "nt":
    _plain_mkdir = os.mkdir

    def _mkdir(path, mode=0o777, *args, **kwargs):
        return _plain_mkdir(path, 0o777 if mode == 0o700 else mode, *args, **kwargs)

    os.mkdir = _mkdir

# Whatever sitecustomize comes after this one on the path is loaded as well.
_here = os.path.normcase(os.path.dirname(os.path.abspath(__file__)))
_this = sys.modules.get(__name__)
_path = sys.path[:]
sys.path[:] = [one for one in sys.path
               if os.path.normcase(os.path.abspath(one or ".")) != _here]
sys.modules.pop(__name__, None)
try:
    import sitecustomize  # noqa: F401
except ImportError as exc:
    if exc.name != "sitecustomize":
        raise
    sys.modules[__name__] = _this
finally:
    sys.path[:] = _path
'''
#: PROGRAMS A COMMAND MAY NOT CALL ON WINDOWS - refused at once, nothing run,
#: with why, rather than the error they end in, which explains nothing
#: (measured, try 6: in the AppContainer PowerShell took collective\server.py
#: for C:\collective\server.py). Called as a program: first on a line, after
#: & | ( or call, start, cmd /c - with or without a folder and .exe.
SHELL_NOT_HERE = re.compile(
    r'(?im)(?:^|[&|(]|\b(?:call|start(?:\s+/\w+)*(?:\s+"[^"]*")?|cmd(?:\.exe)?(?:\s+/\w+)*)\s)'
    r'\s*@?"?(?:[^\s"&|<>()]*[\\/])?(powershell|pwsh|bash|wsl|sh)(?:\.exe)?"?(?=[\s&|)]|$)')
SHELL_NOT_HERE_SAID = {
    "powershell": "PowerShell does not work in this sandbox: in a Windows AppContainer it "
                  "cannot see the file system - a relative path becomes one under C:\\ - and "
                  "starts no program. Nothing was run. Say it in cmd (type, more +N, "
                  "findstr /n, dir /s /b, ...), or read and search with read_file, "
                  "search_files or run_python.",
    "bash": "Git Bash, WSL and sh do not start in this sandbox (their runtime fails, "
            "0xC0000142). Nothing was run. Say it in cmd, or do it in run_python.",
}


def shell_not_here(command: str, shell: str = "") -> str:
    """Why a command cannot run here, on Windows: it asks for, or calls,
    a shell that does not work in the sandbox (`SHELL_NOT_HERE`); '' when
    it may run."""
    if os.name != "nt":
        return ""
    names = [shell.strip().lower()] + [one.lower() for one in SHELL_NOT_HERE.findall(command)]
    for name in names:
        if name in ("powershell", "pwsh"):
            return SHELL_NOT_HERE_SAID["powershell"]
        if name in ("bash", "wsl", "sh"):
            return SHELL_NOT_HERE_SAID["bash"]
    return ""


def commands_allowed(runtime: Any = None) -> bool:
    """Whether commands may run here: only where the operating system
    confines every program on its own - an AppContainer, Seatbelt or
    bubblewrap - as the guard lets them start (runtime/guard.py,
    `other_program`); never with the sandbox off. In a packaged build too
    (the user, 2026-10-29): the system confines a command however the
    application was built."""
    if runtime is not None and not getattr(runtime, "sandbox_enabled", True):
        return False
    from ...runtime.isolation import isolation_status
    try:
        status = isolation_status()
    except Exception:
        return False
    return (bool(status.get("isolated"))
            and status.get("backend") in ("appcontainer", "seatbelt", "bubblewrap"))


def shells() -> Dict[str, str]:
    """The shells on this computer, by name: the system's cmd on Windows
    (`SHELLS_WINDOWS`), bash and sh elsewhere."""
    found: Dict[str, str] = {}
    if os.name == "nt":
        cmd = Path(os.environ.get("SystemRoot") or r"C:\Windows") / "System32" / "cmd.exe"
        if cmd.is_file():
            found["cmd"] = str(cmd)
        return found
    for name in SHELLS_POSIX:
        path = shutil.which(name) or (f"/bin/{name}" if Path(f"/bin/{name}").is_file() else "")
        if path:
            found[name] = path
    return found


def programs_to_read() -> List[Path]:
    """The folders on PATH a command's programs are in that the sandbox
    would otherwise not open: those under where sign-ins are kept (AppData
    on Windows - node, say, installed per user), each named on its own, so
    that what is around it stays shut. Never WindowsApps: its programs are
    links into installed apps."""
    from ...runtime.filesystem import is_inside
    from ...runtime.isolation import credential_stores
    try:
        stores = credential_stores()
    except Exception:
        return []
    out: List[Path] = []
    for one in (os.environ.get("PATH") or "").split(os.pathsep):
        one = one.strip().strip('"')
        if not one or "windowsapps" in one.lower():
            continue
        path = Path(one)
        try:
            if path.is_dir() and any(is_inside(path, store) for store in stores) \
                    and path not in out:
                out.append(path)
        except (OSError, ValueError):
            continue
    return out


def command_environment(home: Path) -> Dict[str, str]:
    """What a command's shell is given besides the sandbox's own clean
    environment: where programs are installed and the system's settings
    folders (none of them the user's), the per-user folders pointed into
    the work's runtime folder, and every prompt turned off - nothing waits
    for an answer that never comes."""
    env = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never",
           "NO_UPDATE_NOTIFIER": "1", "npm_config_update_notifier": "false",
           "CI": "1", "TERM": "dumb"}
    if os.name == "nt":
        for key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "CommonProgramFiles",
                    "CommonProgramFiles(x86)", "CommonProgramW6432", "ProgramData",
                    "SystemDrive", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "OS"):
            if os.environ.get(key):
                env[key] = os.environ[key]
        env["APPDATA"] = str(home / "roaming")
        env["LOCALAPPDATA"] = str(home / "local")
    else:
        env["LANG"] = os.environ.get("LANG") or "C.UTF-8"
        env["XDG_CONFIG_HOME"] = str(home / "config")
        env["XDG_CACHE_HOME"] = str(home / "cache")
    return env


def command_script(shell: str, command: str) -> Tuple[str, str]:
    """(the script's text, its extension) for a command in a shell: cmd's
    in UTF-8 from its second line on (chcp 65001)."""
    if shell == "cmd":
        lines = command.replace("\r\n", "\n").split("\n")
        return "\r\n".join(["@echo off", "chcp 65001 >nul", *lines, ""]), ".cmd"
    return command.replace("\r\n", "\n") + "\n", ".sh"


def shell_argv(shell: str, program: str, script: Path, cwd: Path) -> List[str]:
    if shell == "cmd":
        # From the folder it starts in: in an AppContainer cmd.exe refuses a
        # script named by its whole path ("Access is denied"), measured.
        return [program, "/d", "/c", os.path.relpath(script, cwd)]
    if shell == "bash":
        return [program, "--noprofile", "--norc", script.as_posix()]
    return [program, script.as_posix()]


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

#: FIND_IN_FILES (the user, 2026-10-29): a function looked for by its name
#: was a file read whole from its first line, or a script written to search.
#: So lines are found by a word or a pattern, each with its number, and read
#: on from there - at most so many lines, files looked in, bytes a file and
#: characters of a line, in so many seconds.
MOST_FOUND = 200
MOST_FILES_SEARCHED = 20000
MOST_SEARCHED_BYTES = 8 * 1024 * 1024
MOST_FOUND_CHARS = 300
MOST_SEARCH_SECONDS = 30
#: Folders find_in_files does not go into from above - asked to look in one,
#: it does: installed packages, saved versions, caches, a repository's own
#: store, the agent's scratch; and the run's own folder (`_a_runs_own`), whose
#: reviewer's checks were 11,084 of a replication's 21,005 files.
SEARCH_SKIPPED = frozenset({".packages", ".versions", ".git", "__pycache__", "node_modules",
                            ".npm-cache", ".pip-cache", ".agent_runtime"})

#: The most files a granted folder is looked over for, before and after code
#: runs, to see what it changed.
MOST_FILES_WATCHED = 20000
#: Folders of the work's that are never its work, watched or walked.
WATCH_SKIPPED = (".agent_runtime", ".packages", ".versions", ".session", "node_modules",
                 ".npm-cache", ".pip-cache")


#: What a test run's copy leaves out: caches, the runtime's scratch, the
#: packages and the run's saved versions - none of them what tests read.
TEST_COPY_SKIPPED = ("__pycache__", ".pytest_cache", ".agent_runtime", ".packages",
                     ".versions", ".session", "run_logs", ".git", "node_modules", ".mypy_cache")
#: PYTEST'S ARGUMENTS THAT ONLY CHANGE HOW THE RESULT IS PRINTED - how
#: much it says, which outcomes it lists, the slowest tests, the shape of a
#: traceback, colour, the cache: the same tests run the same way whatever
#: they are (the user, 2026-10-25). Left out when telling whether tests
#: ran already; the run has TEST_REPORT instead, so whoever asks gets the
#: whole report. "-p no:cacheprovider" is always given (TESTS_CODE).
OUTPUT_ONLY_FLAGS = {"-q", "-qq", "-qqq", "--quiet", "-v", "-vv", "-vvv", "--verbose",
                     "--no-header", "-l", "--showlocals", "--no-showlocals"}
#: ... of which these two say how much of a FAILURE is shown - its
#: traceback's style, its locals: a result with failures is given again
#: only to one asking for them as it was run (`TestRuns.fits`).
SHOWS_LOCALS = ("-l", "--showlocals")
#: ... with their value joined ("--durations=10", "-rA", "--tb=short") or
#: given after them ("--durations", "10").
OUTPUT_ONLY_WITH_VALUE = ("--durations", "--durations-min", "--tb", "--color",
                          "--code-highlight", "-r")
#: What a result given again says first (`run_tests`).
NOT_A_NEW_RUN = ("NOT A NEW RUN: these tests were not run now. This is the result of the run "
                 "{by} made at {at} ({seconds} s) on exactly these files. Run them again with "
                 "fresh=true when you judge you should - a test that may pass one time and fail "
                 "the next (timing, threads, processes, ports), one that measures time or load, "
                 "or a result you doubt.")
#: How every test run reports: each test's outcome, the 50 slowest.
TEST_REPORT = ["-q", "-rA", "--durations=50"]
#: A folder of more than this many files, or bytes, is not looked at to
#: tell whether its tests ran: they are run.
TEST_FILES_MOST = 5000
TEST_BYTES_MOST = 200 * 1024 * 1024

#: A run's own folder's name (runner.py: "run_" and 16 hex digits).
RUN_FOLDER = re.compile(r"^run_[0-9a-f]{16}$")

TESTS_CODE = """import os, re, shutil, subprocess, sys
SRC = {src!r}
DST = {dst!r}
SKIP = set({skip})
RUN = re.compile(r"^run_[0-9a-f]{{16}}$")


def ignore(folder, names):
    return [one for one in names if one in SKIP
            or (os.path.basename(folder) == "runs" and RUN.match(one))
            or one in ("site-packages", "dist-packages")
            or os.path.exists(os.path.join(folder, one, "pyvenv.cfg"))
            or os.path.isdir(os.path.join(folder, one, "conda-meta"))]


if os.path.exists(DST):
    shutil.rmtree(DST)
shutil.copytree(SRC, DST, ignore=ignore)
env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
done = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *{args}],
                      cwd=DST, env=env)
sys.exit(done.returncode)
"""


class TestRuns:
    """TESTS THAT RAN, BY EXACTLY WHAT THEY RAN ON (the user, 2026-10-24):
    the content of every file of the folder - read whole and hashed, not
    told by its time - the arguments, the packages and the Python. Only
    exactly the same key gives a result again; a folder too large to read
    whole gives none, and its tests are run."""

    __test__ = False                     # not a test class, whatever pytest thinks

    def __init__(self) -> None:
        self._kept: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(folder: Path, args: Sequence[str], packages: Optional[List[Path]]) -> str:
        """The folder's files by their content, each with its path in it;
        the packages installed, as pip leaves them (reuse.packages_key - the
        top of their folder, not every file in it); the arguments; the
        Python."""
        import hashlib
        import platform
        digest = hashlib.sha256()
        digest.update(json.dumps({"args": list(args), "python": sys.version,
                                  "implementation": platform.python_implementation(),
                                  "skip": TEST_COPY_SKIPPED}).encode("utf-8"))
        files = total = 0
        for path in sorted(_files_under(Path(folder), skip=TEST_COPY_SKIPPED)):
            files += 1
            try:
                data = path.read_bytes()
            except OSError:
                return ""
            total += len(data)
            if files > TEST_FILES_MOST or total > TEST_BYTES_MOST:
                return ""
            digest.update(str(path.relative_to(folder)).replace("\\", "/").encode("utf-8")
                          + b"\0" + hashlib.sha256(data).digest())
        from .reuse import packages_key
        installed = packages_key(packages)
        if installed is None:
            return ""
        digest.update(b"\0PACKAGES\0" + installed.encode("utf-8"))
        return digest.hexdigest()

    @staticmethod
    def fits(kept: Dict[str, Any], failures_shown: Sequence[str]) -> bool:
        """A kept result, for one asking for failures to be shown so: one
        that passed has none to show; one with failures, only when they
        were shown as now asked - the traceback style, the locals."""
        result = kept.get("result") or {}
        if result.get("returncode") == 0:
            return True
        ran = [one for one in kept.get("ran_with") or []
               if one.startswith("--tb=") or one == "-l"]
        return sorted(ran) == sorted(failures_shown)

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            kept = self._kept.get(key)
            return json.loads(json.dumps(kept, default=str)) if kept else None

    def put(self, key: str, result: Dict[str, Any], by: str, seconds: float,
            ran_with: Sequence[str] = (), inputs: Optional[Dict[str, Any]] = None) -> None:
        """Kept - with what the tests read, listed and wrote, wherever it was
        (agent/work/reuse.py): given again only while all of it is as it was."""
        with self._lock:
            self._kept[key] = {"result": result, "by": by, "seconds": seconds,
                               "ran_with": list(ran_with), "at": time.strftime("%H:%M:%S"),
                               "inputs": inputs}


def _a_runs_own(parent: str, name: str) -> bool:
    """A run's own folder - runs/run_<id>: where the application keeps the
    run's state and the reviewer's checks, changing as the run goes; not
    the work (try 6: in it, no two test runs were ever the same)."""
    return os.path.basename(parent) == "runs" and RUN_FOLDER.match(name) is not None


def split_output_only(args: Sequence[str]) -> Tuple[List[str], List[str], List[str]]:
    """The arguments that choose and run the tests; those that only change
    how the result is printed (left out); and, of those, what a failure is
    shown with - the traceback style, the locals - given to the run
    ("--tb=long", "-l"), the last said winning."""
    kept: List[str] = []
    left: List[str] = []
    tb = ""
    locals_ = False
    items = list(args)
    i = 0
    while i < len(items):
        one = items[i]
        if one in OUTPUT_ONLY_FLAGS:
            left.append(one)
            if one in SHOWS_LOCALS:
                locals_ = True
            elif one == "--no-showlocals":
                locals_ = False
        elif one == "-p" and i + 1 < len(items) and items[i + 1] == "no:cacheprovider":
            left += [one, items[i + 1]]
            i += 1
        elif one in OUTPUT_ONLY_WITH_VALUE and i + 1 < len(items) \
                and not items[i + 1].startswith("-"):
            left += [one, items[i + 1]]
            tb = items[i + 1] if one == "--tb" else tb
            i += 1
        elif any(one.startswith(flag + "=") for flag in OUTPUT_ONLY_WITH_VALUE if flag != "-r") \
                or (one.startswith("-r") and len(one) > 2 and one[2:].isalpha()):
            left.append(one)
            tb = one.split("=", 1)[1] if one.startswith("--tb=") else tb
        else:
            kept.append(one)
        i += 1
    return kept, left, ([f"--tb={tb}"] if tb else []) + (["-l"] if locals_ else [])


def last_line(to_line: Any, start: int, count: int) -> Any:
    """Where a read ends (the user, 2026-10-29): `to_line`, counting from 1
    and read with it, as a slice's end - None for on to the end of the file;
    what is wrong with it, said, when it is no line after `start`."""
    if to_line in (None, ""):
        return None
    try:
        end = int(to_line)
    except (TypeError, ValueError):
        return "to_line is a line number"
    if end < start + 1:
        return f"to_line {end} is before from_line {start + 1}"
    return min(end, count)


def _glob_pattern(pattern: str) -> "re.Pattern[str]":
    """A glob as a pattern of a path from where it is looked for: "*" and
    "?" within one folder's name, "**" any number of folders."""
    parts = [one for one in pattern.replace("\\", "/").split("/") if one]
    out = ""
    for k, part in enumerate(parts):
        last = k == len(parts) - 1
        if part == "**":
            out += ".*" if last else "(?:[^/]+/)*"
            continue
        out += "".join("[^/]*" if ch == "*" else "[^/]" if ch == "?" else re.escape(ch)
                       for ch in part) + ("" if last else "/")
    return re.compile(out or ".*", re.IGNORECASE if os.name == "nt" else 0)


def _globbed(root: Path, pattern: str):
    """The files a glob of `root` finds, in a steady order - going no deeper
    than the pattern can reach, and never into SEARCH_SKIPPED, the run's own
    folder or an installed environment below `root` (find_files)."""
    from .environments import is_environment
    wanted = _glob_pattern(pattern or "*")
    parts = [one for one in str(pattern or "*").replace("\\", "/").split("/") if one]
    deepest = None if "**" in parts else len(parts) - 1
    for dirpath, dirnames, filenames in os.walk(root):
        relative = Path(dirpath).relative_to(root).as_posix()
        depth = 0 if relative == "." else relative.count("/") + 1
        if deepest is not None and depth >= deepest:
            dirnames[:] = []
        else:
            dirnames[:] = sorted(one for one in dirnames if one not in SEARCH_SKIPPED
                                 and not _a_runs_own(dirpath, one)
                                 and not is_environment(Path(dirpath) / one, one))
        if deepest is not None and depth != deepest:
            continue
        for name in sorted(filenames):
            if name.endswith((".pyc", ".pyo")):
                continue
            inside = name if relative == "." else f"{relative}/{name}"
            if wanted.fullmatch(inside):
                yield Path(dirpath) / name


def next_line(start: int, body: str, most: int) -> int:
    """Where a read cut at `most` characters goes on from, counting from 1:
    the line the cut fell in, read again whole."""
    return start + 1 + body[:most].count("\n")


def _clip_line(line: str) -> str:
    """A line find_in_files shows: at most MOST_FOUND_CHARS of it."""
    line = line.rstrip()
    return line if len(line) <= MOST_FOUND_CHARS else line[:MOST_FOUND_CHARS - 1] + "…"


def _files_under(root: Path, skip: Sequence[str] = ()) -> List[Path]:
    from .environments import is_environment
    out: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Never an installed environment (environments.py).
        dirnames[:] = [one for one in dirnames if one not in skip
                       and not (skip and _a_runs_own(dirpath, one))
                       and not is_environment(Path(dirpath) / one, one)]
        for name in filenames:
            if name.endswith((".pyc", ".pyo")):
                continue
            out.append(Path(dirpath) / name)
    return out


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
        #: `install(packages, why, ecosystem, folder)`: installs for the run
        #: - pip's or npm's - asking the user first if that is their choice.
        #: None: no packages here.
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
        #: THE READING TOOLS SIDE BY SIDE, everything else alone - within
        #: the computer's limits (parallel.py); what each call says is
        #: recorded one at a time.
        from .parallel import ToolGate
        self.gate = ToolGate()
        self._log_lock = threading.Lock()
        #: TESTS THAT RAN, by exactly what they ran on (`run_tests`): shared
        #: by the work agent and the reviewer of one run (runner.py).
        self.test_runs = TestRuns()
        # run_python's results, by what they ran on (reuse.py) - shared by
        # the agents of a run as the tests' are.
        from .reuse import PythonRuns
        self.python_runs = PythonRuns()
        #: Who these tools are, in a result given again ("the work agent").
        self.label = ""
        self.handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = {
            "list_dir": self.list_dir, "read_file": self.read_file,
            "read_video": self.read_video, "find_files": self.find_files,
            "find_in_files": self.find_in_files, "write_file": self.write_file,
            "edit_file": self.edit_file, "run_tests": self.run_tests,
            "edit_notebook": self.edit_notebook,
            "delete_file": self.delete_file,
            "run_python": self.run_python, "run_command": self.run_command,
            "web_search": self.web_search,
            "install_packages": self.install_packages,
            "download": self.download, "git_fetch": self.git_fetch,
            "fetch_url": self.fetch_url, "open_card": self.open_card,
            "search_stages": self.search_stages, "open_stage": self.open_stage,
            "job_status": self.job_status, "stop_job": self.stop_job,
            "wait_for": self.wait_for, "delegate": self.hand_over,
            "run_outside": lambda args: self.outside(args) if self.outside
            else {"error": "the sandbox holds for this work"},
            "request_approval": lambda args: self.approve(args) if self.approve
            else {"error": "nothing here is asked of the user"},
            "search_records": self.search_records,
            "search_files": self.search_files,
            "open_case": self.open_case, "open_reason": self.open_reason,
            "hand_over": self.give_to_user,
        }
        #: WHAT THE USER GETS (handed.py): `on_hand_over(item)` keeps an item
        #: the work hands over - set by the run whose work it is; None where
        #: nothing is handed to the user (a helper, a reviewer).
        self.on_hand_over: Optional[Callable[[Dict[str, Any]], Any]] = None
        #: WHAT WAS LEARNED OF THE USER'S WORK (learning/find.py), opened by
        #: id: set for the reviewer that has it - none for the work agent,
        #: nor for the way with no context.
        self.learned: Any = None
        #: The user's own records to search (context_search.py): set by the
        #: run that has them - the database they are in.
        self.records_db: Any = None
        self._records_index: Any = None
        #: THE USER'S OWN FILES, searched by their words (index/service.py) -
        #: set by the run that may: none for the single model, or for the
        #: way with no context.
        self.files_index: Any = None
        #: A WORK DEBUGGER WAY'S CUTOFF (as_of.py): no file the index finds
        #: that changed after its try began. None: every one.
        self.as_of: Any = None
        #: `delegate(args)`: a task handed to a helper agent (runner.py,
        #: `Run._delegate`), and the kinds of helper it may be - None where
        #: there are none: a helper has none of its own.
        self.delegate: Optional[Callable[[Dict[str, Any]], Any]] = None
        #: Asked before every call: True when a helper's job was stopped -
        #: its tools then refuse, and it answers with what it has.
        self.halted: Callable[[], bool] = lambda: False
        #: WITHOUT THE SANDBOX (unconfined.py), set by the run that has it:
        #: `outside(args)` runs a command on this computer, judged first;
        #: `approve(args)` asks the user to approve one that was refused.
        #: None: the sandbox holds.
        self.outside: Optional[Callable[[Dict[str, Any]], Any]] = None
        self.approve: Optional[Callable[[Dict[str, Any]], Any]] = None
        #: A helper's: the tools of the agent that handed it its task.
        self.parent_tools: Any = None

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
        runs_code = tuple(self.runs_code)
        if {"run_python", "run_tests"} & set(runs_code):
            runs_code += JOB_TOOLS
        # Cards and past stages only where there are some to open or search:
        # the Experience debugger's single model has neither.
        return [one for one in every if (writing or one.name not in WRITING_TOOLS
                                         or one.name in runs_code)
                and (self.only is None or one.name in self.only)
                and (one.name != "install_packages" or self.install is not None)
                and (one.name != "run_command" or commands_allowed(self.runtime))
                and (one.name != "delegate" or self.delegate is not None)
                and (one.name != "run_outside" or self.outside is not None)
                and (one.name != "request_approval" or self.approve is not None)
                and (one.name != "open_card" or self.cards is not None)
                and (one.name != "hand_over" or self.on_hand_over is not None)
                and (one.name != "search_stages" or self.find is not None)
                and (one.name != "search_records" or self.records_db is not None)
                and (one.name != "search_files" or self.files_index is not None)
                and (one.name != "open_stage" or self.stages is not None)
                and (one.name not in ("open_case", "open_reason") or self.learned is not None)]

    def _specs(self) -> List[ToolSpec]:
        from .parallel import READ_ONLY
        specs = self._all_specs()
        for one in specs:
            one.read_only = one.name in READ_ONLY
        return specs

    def _all_specs(self) -> List[ToolSpec]:
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
                     "from_line = its next_from_line (lines count from 1); \"lines\" "
                     "is how many it has. to_line is the last line read (with it); "
                     "without it, on to the end. Looking for one part - a function, a "
                     "setting - find its line with find_in_files and read only around "
                     "it (from_line a little before, to_line where it ends), not the "
                     "whole file. A text file as it is; a PICTURE as the words "
                     "read in it, and the picture itself where you can see pictures; a "
                     "PDF as the text of each page (\"--- page N ---\"), a scanned page "
                     "read off its picture; Word, PowerPoint and Excel as their text and "
                     "cells. Any file under the user's home can be read, except the run "
                     "logs, the application's state and where sign-ins and keys are "
                     "kept. Another binary file is read with run_python.",
                     _schema({"path": {"type": "string"},
                              "from_line": {"type": "integer"},
                              "to_line": {"type": "integer"}}, ["path"])),
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
                     "200 found. For the LINES that hold it, find_in_files. Installed "
                     "packages, saved versions, caches, .agent_runtime and the run's own "
                     "folder are not gone into unless root is inside them.",
                     _schema({"root": {"type": "string"},
                              "pattern": {"type": "string"},
                              "containing": {"type": "string"}},
                             ["root", "pattern"])),
            ToolSpec("find_in_files", "Find LINES in text files - like grep: each "
                     "line holding the pattern, with its file and LINE NUMBER, grouped by "
                     "file (\"12: the line\"; with context, the lines around it as "
                     "\"11- ...\"). root is a folder (searched all through) or one file; "
                     "pattern a regular expression (literal=true: the words as written; "
                     "ignore_case=true); files a name pattern (\"*.py\", \"**/*.md\"); "
                     "context 0-5 lines either side; most lines (100 unless asked, at "
                     f"most {MOST_FOUND}). A line found that OPENS a block - a "
                     "function, a class, a {...} block, a Markdown section - is in its "
                     "file's \"blocks\" with ends_about: where that block LIKELY ends, and "
                     "by what it was told (Python's parser, indentation, matching braces, "
                     "the next heading). It is an ESTIMATE - yours to change: read with "
                     "read_file from_line a little before and to_line=ends_about, and if "
                     "the part looks cut off read on, or ask for less. "
                     "Installed packages, saved versions, caches, .agent_runtime, the "
                     "run's own folder and binary files are not searched unless root is "
                     "inside them; a notebook is searched as its stored JSON.",
                     _schema({"root": {"type": "string"},
                              "pattern": {"type": "string"},
                              "literal": {"type": "boolean"},
                              "ignore_case": {"type": "boolean"},
                              "files": {"type": "string"},
                              "context": {"type": "integer"},
                              "most": {"type": "integer"}},
                             ["root", "pattern"])),
            ToolSpec("write_file", "Write a text file - whole, or added to its end "
                     "with append - ONLY inside: " + where + ". A path anywhere else "
                     "is refused. Relative paths are inside the first. The contract's "
                     "own files cannot be written.",
                     _schema({"path": {"type": "string"},
                              "content": {"type": "string"},
                              "append": {"type": "boolean"}},
                             ["path", "content"])),
            ToolSpec("edit_file", "Change PART of a text file in place: each edit "
                     "replaces old_text - copied exactly from the file, and found in it "
                     "exactly once (add lines around it to make it so) - with new_text. "
                     "The edits are applied in order, all or none. ONLY inside: " + where
                     + ". Faster than writing the whole file again: use it to change a "
                     "file that exists, write_file for a new one or one rewritten whole.",
                     _schema({"path": {"type": "string"},
                              "edits": {"type": "array", "items": {
                                  "type": "object",
                                  "properties": {"old_text": {"type": "string"},
                                                 "new_text": {"type": "string"}},
                                  "required": ["old_text", "new_text"]}}},
                             ["path", "edits"])),
            ToolSpec("edit_notebook", "Change ONE CELL of a Jupyter notebook (.ipynb) - "
                     "everything else in it kept as it is. cell: its number (from 0, as "
                     "read_file shows the notebook) or its id. mode: replace (the default: "
                     "its source becomes source), insert (a new cell before that one; the "
                     "number of cells puts it at the end) or delete. cell_type: code, "
                     "markdown or raw (a new cell is code unless you say). A code cell "
                     "changed loses its outputs. ONLY inside: " + where + ". Run a "
                     "notebook with run_command (jupyter nbconvert --to notebook --execute).",
                     _schema({"path": {"type": "string"},
                              "cell": {"type": "string"},
                              "mode": {"type": "string", "enum": list(notebooks.MODES)},
                              "cell_type": {"type": "string",
                                            "enum": list(notebooks.CELL_TYPES)},
                              "source": {"type": "string"}}, ["path", "cell"])),
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
                     "read-only - copy it into a folder you can write first. THE SAME "
                     "CODE ON EXACTLY THE SAME INPUTS - every file it read and every "
                     "script it imported, wherever they are, the folders it listed, the "
                     "files it wrote still as it left them, the packages - is NOT RUN "
                     "AGAIN: the earlier result comes back, saying so, what was checked "
                     "and what could not be. When you judge it should run anew - you "
                     "are not sure, or time, randomness or something it cannot see "
                     "matters - call it with fresh=true.",
                     _schema({"code": {"type": "string"},
                              "timeout": {"type": "integer"},
                              "background": {"type": "boolean"},
                              "fresh": {"type": "boolean"}}, ["code"])),
            ToolSpec("run_command", "Run a COMMAND in a shell - "
                     + ("cmd (Windows PowerShell and Git Bash do not work in this "
                        "sandbox; one line or several, as in a .cmd file)" if os.name == "nt"
                        else "bash (the default) or sh")
                     + " - as a terminal would: git, node, npm run, a build, a server, "
                     "anything installed on this computer. It runs under the SAME rules as "
                     "run_python, kept by the operating system for every program it starts: "
                     "it may write only inside: " + where + "; it reads what the work may "
                     "read; it reaches NO network - so nothing is downloaded from here: "
                     "packages with install_packages (pip or npm), repositories with "
                     "git_fetch, files with download. It starts in the work's own folder "
                     "unless cwd says another inside it; nothing is typed into it: a "
                     "command that waits for input ends instead. Each call is a NEW shell: "
                     "nothing carries over but files. THE CALL WAITS for the command to end "
                     f"- timeout seconds, {RUN_SECONDS} unless you ask, at most "
                     f"{RUN_MOST_SECONDS} - and returns its exit code, what it printed "
                     f"(both streams together, the last {MOST_PRINTED:,} characters) and "
                     "the files it changed; whatever it starts ends with it. "
                     "background=true runs it as a JOB, as run_python does.",
                     _schema({"command": {"type": "string"},
                              "shell": {"type": "string"},
                              "cwd": {"type": "string"},
                              "timeout": {"type": "integer"},
                              "background": {"type": "boolean"}}, ["command"])),
            ToolSpec("run_tests", "Run a folder's tests with pytest - on a fresh copy of "
                     "the folder, so nothing they write lands in it - always with the whole "
                     "report: each test's outcome and the 50 slowest. args: which tests and "
                     "how they run (a test file, -k, -x, -m, ...); arguments that only "
                     "change the printing (-q, -v, -r..., --durations) need not be given; "
                     "--tb and -l are what failures are shown with. WHEN EXACTLY THE SAME "
                     "TESTS ALREADY RAN - every file of the "
                     "folder the same, byte for byte, the same tests chosen, the same "
                     "packages and the same Python - the result comes back at once, saying "
                     "when it ran and who ran it; fresh=true "
                     "runs them again all the same (a test you think is not steady). Use "
                     "it rather than starting pytest from run_python. background=true "
                     "runs it as a job, as run_python does.",
                     _schema({"folder": {"type": "string"},
                              "args": {"type": "array", "items": {"type": "string"}},
                              "timeout": {"type": "integer"},
                              "background": {"type": "boolean"},
                              "fresh": {"type": "boolean"}}, ["folder"])),
            ToolSpec("job_status", "A job started with background=true (run_python, "
                     "run_command, run_tests): whether it is still running and for how "
                     f"long, what it has printed so far (the last {JOB_TAIL:,} characters) "
                     "- and, once it has ended, its whole result, as run_python gives one. "
                     "A LOOK, NOW: to wait until a job ends, use wait_for (one call). With "
                     "wait (seconds) it waits that long at most, never past the job's end. "
                     "With no job, every job of this run. A job is stopped when the run "
                     "ends. Every tool's result also says which jobs ended since your last "
                     "call (jobs_ended).",
                     _schema({"job": {"type": "string"},
                              "wait": {"type": "integer"}}, [])),
            ToolSpec("run_outside", "Run a COMMAND ON THIS COMPUTER, OUTSIDE THE SANDBOX - "
                     "the user chose to let this work run without it - as their account: "
                     "shell cmd (the default), powershell or bash on Windows; bash or sh "
                     "elsewhere; cwd any folder (the work's own unless you say). Each command "
                     "is judged first: one that would change or delete anything outside the "
                     "work's folder, install software, send anything out, or keep running "
                     "afterwards is REFUSED as needing the user's approval - then find "
                     "another way. NEVER, approved or not: the agent's own data, where "
                     "sign-ins are kept, anything written out with the user's accounts "
                     "(push, publish, post, send). The call waits for it - timeout seconds, "
                     f"{RUN_SECONDS} unless you ask, at most {RUN_MOST_SECONDS} - and returns "
                     "its exit code and what it printed.",
                     _schema({"command": {"type": "string"},
                              "shell": {"type": "string"},
                              "cwd": {"type": "string"},
                              "timeout": {"type": "integer"}}, ["command"])),
            ToolSpec("request_approval", "Ask the USER to approve ONE exact command that was "
                     "refused - only when you have tried other ways, at least two, each "
                     "said in tried, and none could do it. why: what it is needed for. The "
                     "run waits for their answer; approved, the command is run once, "
                     "outside the sandbox, and what it printed comes back. Never for what "
                     "is never done (the agent's own data, sign-ins, writing out with the "
                     "user's accounts). Denied: do not ask again - do the work another way, "
                     "or say in your answer what the user must do.",
                     _schema({"command": {"type": "string"},
                              "shell": {"type": "string"},
                              "cwd": {"type": "string"},
                              "why": {"type": "string"},
                              "tried": {"type": "array", "items": {"type": "string"}}},
                             ["command", "why", "tried"])),
            ToolSpec("delegate", "Hand ONE TASK to a HELPER AGENT, which does it while you "
                     "go on: kind explore - it reads and searches (files, the user's "
                     "records, the web) and changes nothing; several can go side by side "
                     "- or kind worker - it may change files and run code and commands; "
                     "one at a time, and only where you may write. It has your tools (but "
                     "this one), under the same rules, and NONE "
                     "of your context: say in task all it needs - what to do, where, what "
                     "you know already, and what to return. Give a worker files you are "
                     "not changing yourself meanwhile. It answers with a JSON object "
                     "(done, summary, files_changed, open). background=true: it runs as a "
                     "job - wait_for it, or see jobs_ended - and the call returns at once; "
                     "otherwise the call waits for its answer. Its tokens count as this "
                     "run's.",
                     _schema({"task": {"type": "string"},
                              "kind": {"type": "string", "enum": ["explore", "worker"]},
                              "background": {"type": "boolean"}}, ["task"])),
            ToolSpec("wait_for", "WATCH for something and return the moment it happens - "
                     "rather than waiting a fixed time or asking again and again: a job "
                     "ending (job, or jobs - several, returning when the FIRST of them "
                     "ends, saying which still run); a job printing a line that matches "
                     "output (a regular expression, e.g. 'listening on|ready'; one job); a "
                     "file appearing (file, a path); a server the work started answering "
                     "(url, an address on 127.0.0.1 or localhost - tried from inside the "
                     "sandbox). WAITING ON A JOB, it waits UNTIL IT ENDS - however long, as "
                     "long as the job's own time - unless you give timeout (seconds); so "
                     "call it ONCE, when there is nothing else to do meanwhile, and never "
                     "for a job that does not end by itself (a server: wait for its "
                     "'ready' line instead). A file or a url alone: timeout seconds, "
                     f"{WAIT_MOST} at most, 60 unless you ask; then it says it is still "
                     "waiting. Returns when a job ends first, saying so.",
                     _schema({"job": {"type": "string"},
                              "jobs": {"type": "array", "items": {"type": "string"}},
                              "output": {"type": "string"},
                              "file": {"type": "string"},
                              "url": {"type": "string"},
                              "timeout": {"type": "integer"}}, [])),
            ToolSpec("stop_job", "Stop a job started with run_python "
                     "background=true, and every program it started.",
                     _schema({"job": {"type": "string"}}, ["job"])),
            ToolSpec("install_packages", "Install packages for this work. PYTHON "
                     "(ecosystem pip, the default): from PyPI into this run's own folder, "
                     "so run_python imports them - each a plain name with an optional "
                     "version (pandas, numpy>=1.26, scikit-learn==1.5.*); wheels only, "
                     "nothing built from source. JAVASCRIPT (ecosystem npm): from the "
                     "public npm registry into the node_modules of folder (the work's "
                     "own folder unless you name one inside it), added to its "
                     "package.json - each a name with an optional @version or range "
                     "(react, lodash@^4.17, @types/node@20); with none, what its "
                     "package.json lists. Install scripts never run: build a package "
                     "that needs them with run_command (npm rebuild <name>). No "
                     "addresses, paths, git or file references, or options. Ask for "
                     "everything you need in one call, and say why. Where the user "
                     "approves each request, the call waits for their answer. Neither "
                     "pip nor npm run from code or a command reaches the network.",
                     _schema({"packages": {"type": "array",
                                           "items": {"type": "string"}},
                              "why": {"type": "string"},
                              "ecosystem": {"type": "string", "enum": ["pip", "npm"]},
                              "folder": {"type": "string"}}, ["packages", "why"])),
            ToolSpec("download", "Download a FILE from the web into the work's folder - "
                     "an https address that already exists (a search result, a page's "
                     "link, the user's files or instruction - as fetch_url opens), at most "
                     f"{fetching.MOST_DOWNLOAD // (1024 * 1024)} MB. It is saved, never "
                     "opened or run: unpack or use it with run_command or run_python. path: "
                     "where to save it, inside the work's folder (downloads/<its name> "
                     "unless you say).",
                     _schema({"url": {"type": "string"},
                              "path": {"type": "string"}}, ["url"])),
            ToolSpec("git_fetch", "Clone a public git REPOSITORY over https into the work's "
                     "folder (path: a new folder inside it; the repository's name unless "
                     "you say) - shallow (depth 1) unless you ask for more (0: its whole "
                     "history), ref: a branch or tag. Its address must already exist, as "
                     "fetch_url's must. Nothing of it runs while it is fetched: no hook, "
                     "no submodule, no symbolic link. Then use git locally with "
                     "run_command (log, diff, branch, commit). Nothing is ever pushed.",
                     _schema({"url": {"type": "string"},
                              "path": {"type": "string"},
                              "depth": {"type": "integer"},
                              "ref": {"type": "string"}}, ["url"])),
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
            ToolSpec("open_case", "Open a CASE of the user's past work by its id "
                     "(C-...): what the deliverable was, where its files are, what it "
                     "looks like, its versions, every judgment the user made in it and "
                     "how it was made.",
                     _schema({"id": {"type": "string"}}, ["id"])),
            ToolSpec("open_reason", "Open a REASON of the user's by its id (R-...): "
                     "when it holds and what it is excepted from, what it predicts, its "
                     "history, and the cases it rests on - the ones most like this job "
                     "first, a page at a time.",
                     _schema({"id": {"type": "string"}, "page": {"type": "integer"}}, ["id"])),
            ToolSpec("open_stage", "Open past stages by their ids (from the "
                     "experience you were shown, or search_stages): each one's "
                     "key information - what it was an attempt at, what was "
                     "known, what was decided and why, the user's own words, how "
                     "it was judged, what it produced and referred to, and the "
                     "stages linked to it. At most 6 at a time.",
                     _schema({"ids": {"type": "array", "items": {"type": "string"}}},
                             ["ids"])),
            ToolSpec(
                "hand_over", "HAND A RESULT TO THE USER, in the form most convenient "
                "for what they will do with it - every result, at the end (again "
                "when it changes; the same kind and title replaces it). KINDS: "
                "\"email\" (to, cc, bcc, subject, body) - opened in their own mail as a "
                "new message, filled in, for them to send; \"event\" (title, start, end "
                "- ISO times, or a date for all day - where, details) - opened in their "
                "calendar, filled in; \"text\" (text) - words to paste elsewhere, "
                "copied; \"file\" or \"folder\" (path) - one this work made, opened; "
                "\"answer\" (text) - shown. NOTHING IS SENT, SAVED OR SUBMITTED: it is "
                "opened for the user, and what is done with it is theirs.",
                _schema({"kind": {"type": "string",
                                  "enum": ["email", "event", "text", "file", "folder",
                                           "answer"]},
                         "title": {"type": "string",
                                   "description": "what it is, in a few words"},
                         "to": {"type": "array", "items": {"type": "string"}},
                         "cc": {"type": "array", "items": {"type": "string"}},
                         "bcc": {"type": "array", "items": {"type": "string"}},
                         "subject": {"type": "string"}, "body": {"type": "string"},
                         "start": {"type": "string"}, "end": {"type": "string"},
                         "where": {"type": "string"}, "details": {"type": "string"},
                         "text": {"type": "string"}, "path": {"type": "string"}},
                        ["kind", "title"])),
        ]

    def give_to_user(self, args: Dict[str, Any]) -> Any:
        """A result handed to the user (handed.py): kept by the run, and
        opened for them - never sent, saved or submitted."""
        from . import handed
        if self.on_hand_over is None:
            return {"error": "nothing is handed to the user from here"}
        policy = self.runtime.policy
        roots = [Path(policy.write_root), *[Path(one) for one in policy.extra_writable]]

        def resolve(path: str) -> Optional[Path]:
            if not str(path or "").strip():
                return None
            try:
                target = self._readable(path).resolve()
            except Exception:
                return None
            for root in roots:
                root = root.resolve()
                if target == root or root in target.parents:
                    return target
            return None
        item = handed.checked(args, resolve)
        if item.get("error"):
            return item
        self.on_hand_over(item)
        return {"handed": item["kind"], "title": item["title"],
                "note": "the user opens it - nothing was sent or saved for them"}

    def call(self, name: str, arguments: Dict[str, Any]) -> Any:
        handler = self.handlers.get(name)
        if handler is None or (self.only is not None and name not in self.only):
            return {"error": f"unknown tool {name}"}
        if self.halted():
            return {"error": "stopped: you were stopped - answer now, with the JSON "
                             "object, from what you have"}
        began = time.time()
        waited = 0.0
        try:
            with self.gate.hold(name):
                waited = time.time() - began
                said = handler(arguments or {})
        except SecurityViolation as exc:
            said = exc.to_dict()
        except Exception as exc:
            said = {"error": f"{type(exc).__name__}: {exc}"[:600]}
        # WHERE ITS TIME WENT (the user, 2026-10-29): kept in the run's log,
        # never told the agent - a part of it said by the tool (`_timing`).
        parts = said.pop("_timing", None) if isinstance(said, dict) else None
        timing = dict(parts or {}, total=round(time.time() - began, 2),
                      waited_for_turn=round(waited, 2))
        # THE JOBS THAT ENDED meanwhile, told with whatever is called next -
        # nobody has to keep asking.
        ended = self.jobs_ended() if isinstance(said, dict) else []
        if ended:
            said = dict(said, jobs_ended=ended)
        with self._log_lock:
            self.log(name, {"arguments": arguments, "result": said, "timing": timing})
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
                                                MOST_CHARS, to_line=args.get("to_line"))
            except Exception as exc:
                return {"error": f"could not read {where.name}: {exc}"[:400]}
            if not self._the_agents_own(where):
                self.know_addresses(said.get("text") or "")
            return said
        text = where.read_text(encoding="utf-8", errors="replace")
        if where.suffix.lower() == ".ipynb":
            # A notebook as its cells, each with what it printed (notebooks.py).
            try:
                text = notebooks.shown(text)
            except ValueError:
                pass
        # The user's own files are where real addresses are; the agent's
        # own folders are where it could have put one.
        if not self._the_agents_own(where):
            self.know_addresses(text)
        every = text.splitlines()
        start = max(0, int(args.get("from_line") or 1) - 1)
        end = last_line(args.get("to_line"), start, len(every))
        if isinstance(end, str):
            return {"error": end}
        body = "\n".join(every[start:end])
        said = {"path": str(where), "from_line": start + 1,
                "text": body[:MOST_CHARS], "cut": len(body) > MOST_CHARS,
                "lines": len(every)}
        if end is not None:
            said["to_line"] = end
        if said["cut"]:
            said["next_from_line"] = next_line(start, body, MOST_CHARS)
        return said

    def find_files(self, args: Dict[str, Any]) -> Any:
        root = self._readable(str(args.get("root") or "."))
        pattern = str(args.get("pattern") or "*")
        needle = str(args.get("containing") or "")
        found = []
        too_big = 0
        # As a glob finds them, but never into what find_in_files leaves out
        # from above (SEARCH_SKIPPED, the run's own folder, an environment):
        # from the top of a replication's folder a glob went through some
        # 70,000 files, seven virtual environments among them - 5 to 97
        # seconds a call (2026-10-29).
        for path in _globbed(root, pattern):
            if len(found) >= 200:
                break
            try:
                self._readable(str(path))
            except SecurityViolation:
                continue
            if needle:
                try:
                    if path.stat().st_size > MOST_SEARCHED_BYTES:
                        too_big += 1
                        continue
                    if needle not in path.read_text(encoding="utf-8",
                                                    errors="ignore"):
                        continue
                except OSError:
                    continue
            found.append(str(path))
        said = {"root": str(root), "files": found}
        if too_big:
            said["not_searched"] = (f"{too_big} file(s) over {MOST_SEARCHED_BYTES // 2**20} MB "
                                    "were not looked into for the text")
        return said

    def find_in_files(self, args: Dict[str, Any]) -> Any:
        """THE LINES THAT HOLD A WORD OR A PATTERN, each with its number - in
        one file or all through a folder (FIND_IN_FILES above)."""
        root = self._readable(str(args.get("root") or "."))
        pattern = str(args.get("pattern") or "")
        if not pattern:
            return {"error": "say what to find: pattern"}
        try:
            wanted = re.compile(re.escape(pattern) if args.get("literal") else pattern,
                                re.IGNORECASE if args.get("ignore_case") else 0)
        except re.error as exc:
            return {"error": f"not a regular expression ({exc}): send literal=true "
                             "to find the words as written"}
        names = str(args.get("files") or "*").replace("\\", "/")
        try:
            around = max(0, min(5, int(args.get("context") or 0)))
            most = max(1, min(MOST_FOUND, int(args.get("most") or 100)))
        except (TypeError, ValueError):
            return {"error": "context and most are whole numbers"}
        from ...runtime import documents
        began = time.time()
        found: List[Dict[str, Any]] = []
        shown = searched = 0
        left = {"binary": 0, "too big": 0, "not text": 0, "not readable": 0}
        cut = ""
        for path in self._searched(root, names):
            if cut:
                break
            if searched >= MOST_FILES_SEARCHED:
                cut = f"stopped after {MOST_FILES_SEARCHED} files - narrow root or files"
                break
            if time.time() - began > MOST_SEARCH_SECONDS:
                cut = f"stopped after {MOST_SEARCH_SECONDS} seconds - narrow root or files"
                break
            try:
                self._readable(str(path))
                if path.stat().st_size > MOST_SEARCHED_BYTES:
                    left["too big"] += 1
                    continue
                if documents.needs_reading(path):
                    left["not text"] += 1
                    continue
                raw = path.read_bytes()
            except (SecurityViolation, OSError):
                left["not readable"] += 1
                continue
            if b"\0" in raw[:8192]:
                left["binary"] += 1
                continue
            searched += 1
            lines = raw.decode("utf-8", errors="replace").splitlines()
            hits = [n for n, line in enumerate(lines) if wanted.search(line)]
            if not hits:
                continue
            if len(hits) > most - shown:
                cut = f"more than {most} lines hold it - ask for more with most, or narrow it"
                hits = hits[:most - shown]
                if not hits:
                    break
            shown += len(hits)
            out: List[str] = []
            last = -1
            for n in hits:
                for k in range(max(0, n - around, last + 1), min(len(lines), n + around + 1)):
                    if out and k > last + 1:
                        out.append("--")
                    mark = ":" if k == n or wanted.search(lines[k]) else "-"
                    out.append(f"{k + 1}{mark} {_clip_line(lines[k])}")
                    last = k
            entry: Dict[str, Any] = {"path": str(path), "lines": out}
            # Where each block a line found opens likely ends (blocks.py).
            from .blocks import ends_of
            ends = ends_of(path.suffix, lines, hits)
            if ends:
                entry["blocks"] = [{"line": n + 1, "ends_about": end, "by": by}
                                   for n, (end, by) in sorted(ends.items())]
            found.append(entry)
            # The user's own files are where real addresses are (read_file).
            if not self._the_agents_own(path):
                self.know_addresses("\n".join(out))
        said: Dict[str, Any] = {"root": str(root), "pattern": pattern, "files": found,
                                "lines_found": shown, "files_searched": searched}
        skipped = {k: v for k, v in left.items() if v}
        if skipped:
            said["not_searched"] = skipped
        if cut:
            said["cut"] = cut
        if not found:
            said["note"] = "no line holds it" + (
                "" if args.get("ignore_case") else " - ignore_case=true for any case")
        return said

    def _searched(self, root: Path, names: str):
        """The files find_in_files looks in, in a steady order: `root` itself
        when it is a file; else every file under it whose name, or its path
        from `root` for a pattern with a "/", matches `names` - never into
        SEARCH_SKIPPED or an installed environment (environments.py)."""
        import fnmatch
        from .environments import is_environment
        if root.is_file():
            yield root
            return
        by_path = "/" in names
        flat = names[3:] if names.startswith("**/") else names
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(one for one in dirnames if one not in SEARCH_SKIPPED
                                 and not _a_runs_own(dirpath, one)
                                 and not is_environment(Path(dirpath) / one, one))
            for name in sorted(filenames):
                if name.endswith((".pyc", ".pyo")):
                    continue
                whole = Path(dirpath) / name
                if by_path:
                    relative = whole.relative_to(root).as_posix()
                    if not (fnmatch.fnmatch(relative, names) or fnmatch.fnmatch(relative, flat)):
                        continue
                elif not fnmatch.fnmatch(name, names):
                    continue
                yield whole

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

    def edit_file(self, args: Dict[str, Any]) -> Any:
        """PART OF A FILE CHANGED IN PLACE: each edit's old text, found
        exactly once, replaced by its new text - all of them, or none. A
        file with Windows line ends keeps them."""
        from ...runtime.filesystem import validate_write_path
        target = Path(validate_write_path(str(args.get("path") or ""), self.runtime.policy))
        refused = self._refuse_locked(target)
        if refused:
            return refused
        if not target.is_file():
            return {"error": f"no file at {target}: write a new one with write_file"}
        edits = [one for one in args.get("edits") or [] if isinstance(one, dict)]
        if not edits:
            return {"error": "no edits: each is {old_text, new_text}"}
        try:
            raw = target.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            return {"error": "not a UTF-8 text file: change it with run_python"}
        windows = "\r\n" in raw
        text = raw.replace("\r\n", "\n") if windows else raw
        for number, one in enumerate(edits, 1):
            old = str(one.get("old_text") or "").replace("\r\n", "\n")
            new = str(one.get("new_text") or "").replace("\r\n", "\n")
            if not old:
                return {"error": f"edit {number}: old_text is empty - nothing was changed"}
            found = text.count(old)
            if found != 1:
                return {"error": f"edit {number}: old_text is found {found} times, not once - "
                                 "nothing was changed; copy it exactly from the file, with "
                                 "lines around it to make it unique"}
            text = text.replace(old, new, 1)
        if windows:
            text = text.replace("\n", "\r\n")
        self._claim([str(target)])
        written = self.runtime.write_text(str(target), text)
        self._claim([str(written)])
        said = {"edited": str(written), "edits": len(edits),
                "lines": text.count("\n") + (0 if text.endswith("\n") else 1)}
        self.changed("edited", [str(written)], said)
        return said

    # -- tests: run once for exactly the same files ------------------------------------
    def edit_notebook(self, args: Dict[str, Any]) -> Any:
        """One cell of a notebook replaced, inserted or deleted (notebooks.py)."""
        from ...runtime.filesystem import validate_write_path
        target = Path(validate_write_path(str(args.get("path") or ""), self.runtime.policy))
        refused = self._refuse_locked(target)
        if refused:
            return refused
        if not target.is_file():
            return {"error": f"no notebook at {target}"}
        if args.get("cell") is None:
            return {"error": "say which cell: its number or its id"}
        try:
            text, said = notebooks.edit(
                target.read_text(encoding="utf-8"), args.get("cell"),
                str(args.get("mode") or "replace"), str(args.get("source") or ""),
                str(args["cell_type"]) if args.get("cell_type") else None)
        except (ValueError, UnicodeDecodeError) as exc:
            return {"error": str(exc)[:400]}
        self._claim([str(target)])
        self.runtime.write_text(target, text)
        said = dict(said, path=str(target))
        self.changed("edited", [str(target)], said)
        return said

    def run_tests(self, args: Dict[str, Any]) -> Any:
        """A folder's tests with pytest, on a fresh copy of it - or, when
        exactly the same tests ran on exactly the same files (TestRuns),
        their result again, said to be so."""
        folder = self._readable(str(args.get("folder") or "."))
        if not folder.is_dir():
            return {"error": f"not a folder: {folder}"}
        asked = [str(one) for one in args.get("args") or []]
        if any(not one or "\x00" in one for one in asked):
            return {"error": "an empty argument"}
        # What only changes the printing is not what tells tests apart: run
        # with the whole report, whoever asked and however.
        pytest_args, left_out, failures_shown = split_output_only(asked)
        ran_with = TEST_REPORT + failures_shown + pytest_args
        from .reuse import read_records, told, unchanged
        mark = time.time()
        timing: Dict[str, Any] = {}

        def lap(name: str) -> None:
            nonlocal mark
            timing[name] = round(time.time() - mark, 2)
            mark = time.time()
        key = self.test_runs.key(folder, pytest_args, self._packages())
        lap("key")
        if key and not args.get("fresh"):
            kept = self.test_runs.get(key)
            # And everything else they read, listed and wrote, as it was.
            if kept is not None and not unchanged(kept.get("inputs")):
                kept = None
            if kept is not None and TestRuns.fits(kept, failures_shown):
                words, record = told("run_tests", kept["by"], kept["at"], kept["seconds"],
                                     kept["inputs"], "the same tests, on exactly these files")
                # Said first, before what the run printed: NOT A NEW RUN.
                lap("reuse_check")
                return {"_timing": dict(timing, reused=True),
                        "not_a_new_run": words + " " + NOT_A_NEW_RUN.format(
                            by=kept["by"], at=kept["at"], seconds=kept["seconds"]),
                        **kept["result"], "files_changed": [], "reused": {
                    **record,
                    "ran_at": kept["at"], "by": kept["by"], "seconds": kept["seconds"],
                    "ran_with": kept.get("ran_with") or [],
                    "why": "every file of the folder, the tests chosen, the packages and "
                           "Python are exactly what they were when these tests ran - this is "
                           "that run's result, with the whole report (arguments that only "
                           "change the printing are not told apart); fresh=true runs them "
                           "again"}}
        copy = self.folder / ".agent_runtime" / "test_runs" / (key[:16] if key else
                                                               uuid.uuid4().hex[:16])
        code = TESTS_CODE.format(src=str(folder), dst=str(copy), args=repr(ran_with),
                                 skip=repr(TEST_COPY_SKIPPED))
        by = self.label or "an agent of this run"
        recording = self._recording()

        def kept(said: Dict[str, Any], seconds: float) -> None:
            # Only a run that ended by itself is kept: not one cut off - and
            # one whose reading could not all be seen is not kept at all.
            from .reuse import inputs_of
            record = read_records(recording)
            if isinstance(said, dict):
                said["ran_with"] = ran_with
                if left_out:
                    said["printing_arguments_replaced"] = left_out
            if key and isinstance(said, dict) and not said.get("timed_out") \
                    and not said.get("violation") and said.get("returncode") is not None:
                inputs, _why = inputs_of(record, self._not_inputs())
                if inputs is not None:
                    self.test_runs.put(key, said, by, seconds, ran_with, inputs)

        from ...runtime.guard import RECORD_ENV
        if args.get("background") and (self.only is None or "job_status" in self.only):
            return self._start_job(code, args.get("timeout"), done=kept,
                                   environment={RECORD_ENV: str(recording)})
        timeout = max(5, min(RUN_MOST_SECONDS, int(args.get("timeout") or RUN_MOST_SECONDS)))
        lap("reuse_check")
        started = time.time()
        before = self._changes_begin()
        lap("look_before")
        result = self.runtime.run_python_code(code, timeout=timeout, sys_path=self._packages(),
                                              environment={RECORD_ENV: str(recording)})
        lap("code")
        said = self._ran(result, before)
        lap("look_after")
        kept(said, round(time.time() - started, 1))
        lap("kept_for_reuse")
        if isinstance(said, dict):
            said["_timing"] = dict(timing, reused=False)
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

    def _changes_begin(self) -> Any:
        """Before code runs: the folders it may write WATCHED (changes.py) -
        or, where no watch can be made, every file in them as it is, to be
        compared after (`_look_over`)."""
        from .changes import Watch
        try:
            watch = Watch(self.writable, self._left_out)
            if watch.start():
                return watch
        except Exception:                          # no watch: walked instead
            pass
        return self._look_over()

    def _changes_end(self, before: Any) -> List[str]:
        """What the code made, changed or removed, as heard - or as the
        folders differ from how they were."""
        from .changes import Watch
        if isinstance(before, Watch):
            heard = before.stop()
            if heard is not None:
                return sorted(heard)
            before = {}
        after = self._look_over()
        return sorted(set(path for path in after if before.get(path) != after[path])
                      | (set(before) - set(after)))

    def _left_out(self, path: str) -> bool:
        """Not the work's, of what a watch heard: the runtime's scratch, the
        packages, the versions, the logs, caches - and any installed
        environment."""
        whole = Path(path)
        roots = self.writable
        work_root = Path(self.runtime.policy.write_root)
        for root in roots:
            try:
                parts = whole.relative_to(root).parts
            except ValueError:
                continue
            if any(part in WATCH_SKIPPED or part == "__pycache__" for part in parts[:-1]):
                return True
            if parts and parts[0] == LOGS_DIR and Path(root) == work_root:
                return True
            if not hasattr(self, "_environments_seen"):
                from .environments import Seen
                self._environments_seen = Seen()
            return self._environments_seen(str(whole), roots)
        return True

    def _look_over(self) -> Dict[str, tuple]:
        """Every file in the folders it may write, with when it changed and
        how big it is - to see what code changed, where it cannot be heard
        (`_changes_begin`)."""
        from .environments import is_environment
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
                               if d not in WATCH_SKIPPED
                               and not (d == LOGS_DIR and Path(dirpath) == work_root)
                               # Nor an installed environment (environments.py).
                               and not is_environment(Path(dirpath) / d, d)]
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
        # THE SAME CODE ON EXACTLY THE SAME INPUTS (reuse.py): its result
        # again, said to be so - the agent runs it anew with fresh=true.
        from ...runtime.guard import RECORD_ENV
        from .reuse import read_records
        code = str(args.get("code") or "")
        mark = time.time()
        timing: Dict[str, Any] = {}

        def lap(name: str) -> None:
            nonlocal mark
            timing[name] = round(time.time() - mark, 2)
            mark = time.time()
        key = self.python_runs.key(code, self._packages(), self.folder)
        lap("key")
        if not args.get("fresh"):
            again = self.python_runs.again(key)
            lap("reuse_check")
            if again is not None:
                return dict(again, _timing=dict(timing, reused=True))
        recording = self._recording()
        started = time.time()
        before = self._changes_begin()
        lap("look_before")
        result = self.runtime.run_python_code(code, timeout=timeout, sys_path=self._packages(),
                                              environment={RECORD_ENV: str(recording)})
        lap("code")
        said = self._ran(result, before)
        lap("look_after")
        why = self.python_runs.keep(key, read_records(recording), said,
                                    self.label or "an agent of this run",
                                    time.time() - started, self._not_inputs())
        lap("kept_for_reuse")
        if isinstance(said, dict):
            said["_timing"] = dict(timing, reused=False, kept=not why,
                                   **({"not_kept": why} if why else {}))
        return said

    def _recording(self) -> Path:
        """Where the code's processes write what they read (guard.RECORD_ENV):
        the run's own scratch - never counted an input."""
        folder = self.folder / ".agent_runtime" / "records"
        self.runtime.mkdir(folder)
        return folder / uuid.uuid4().hex[:16]

    def _not_inputs(self) -> List[str]:
        from .reuse import skip_roots
        return skip_roots(self._packages(), self.folder / ".agent_runtime")

    def run_command(self, args: Dict[str, Any]) -> Any:
        """A command in a shell, in the sandbox (`COMMAND_CODE`): its script
        written in the work's runtime folder, run by the shell from guarded
        code - foreground or as a job."""
        from ...runtime.filesystem import validate_write_path
        command = str(args.get("command") or "")
        if not command.strip() or "\x00" in command:
            return {"error": "say the command to run"}
        if not commands_allowed(self.runtime):
            return {"error": "commands cannot run on this computer: the operating system "
                             "does not confine them here - use run_python"}
        refused = shell_not_here(command, str(args.get("shell") or ""))
        if refused:
            return {"error": refused}
        known = shells()
        order = SHELLS_WINDOWS if os.name == "nt" else SHELLS_POSIX
        shell = str(args.get("shell") or "").strip().lower() or next(
            (one for one in order if one in known), "")
        if shell not in known:
            return {"error": f"no shell {shell or '(none found)'} here - "
                             + ("these are: " + ", ".join(sorted(known)) if known
                                else "none was found")}
        cwd = self.folder
        if args.get("cwd"):
            cwd = Path(validate_write_path(str(args["cwd"]), self.runtime.policy))
            if not cwd.is_dir():
                return {"error": f"not a folder: {cwd}"}
        text, extension = command_script(shell, command)
        runtime_dir = self.folder / ".agent_runtime"
        script = runtime_dir / "commands" / f"{uuid.uuid4().hex[:12]}{extension}"
        home = runtime_dir / "home"
        for one in ("roaming", "local", "config", "cache"):
            self.runtime.mkdir(home / one)
        self.runtime.mkdir(script.parent)
        self.runtime.write_text(script, text)
        prepend: Dict[str, str] = {}
        if os.name == "nt":
            fix = home / COMMAND_PYTHON_FOLDER
            self.runtime.mkdir(fix)
            self.runtime.write_text(fix / "sitecustomize.py", COMMAND_PYTHON_FIX)
            prepend["PYTHONPATH"] = str(fix)
        code = COMMAND_CODE.format(env=command_environment(home), prepend=prepend,
                                   argv=shell_argv(shell, known[shell], script, cwd),
                                   cwd=str(cwd))
        readable = programs_to_read()
        if args.get("background") and (self.only is None or "job_status" in self.only):
            said = self._start_job(code, args.get("timeout"), readable=readable)
            if isinstance(said, dict) and said.get("job"):
                said["shell"] = shell
            return said
        timeout = max(5, min(RUN_MOST_SECONDS, int(args.get("timeout") or RUN_SECONDS)))
        before = self._changes_begin()
        result = self.runtime.run_python_code(code, timeout=timeout, readable=readable)
        said = self._ran(result, before)
        said["shell"] = shell
        return said

    def _packages(self) -> Optional[List[Path]]:
        return [self.packages_dir] if self.packages_dir is not None \
            and self.packages_dir.is_dir() else None

    def _ran(self, result: Any, before: Any) -> Dict[str, Any]:
        """What code that ran comes back as: what it printed, what it
        changed - and what it left running, which was ended with it."""
        touched = self._changes_end(before)
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
    def _start_job(self, code: str, timeout: Any,
                   done: Optional[Callable[[Dict[str, Any], float], None]] = None,
                   readable: Optional[List[Path]] = None,
                   environment: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
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
            before = self._changes_begin()
            try:
                result = self.runtime.run_python_code(
                    launcher, timeout=seconds, sys_path=self._packages(),
                    **({"readable": readable} if readable else {}),
                    **({"environment": environment} if environment else {}))
                said = self._ran(result, before)
            except Exception as exc:          # said, as a result is
                said = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:600]}
            stopped = (folder / "stop").exists()
            with self._jobs_lock:
                job["result"] = said
                job["ended"] = time.time()
                job["status"] = ("stopped" if stopped else "timed out" if said.get("timed_out")
                                 else "done" if said.get("ok") else "failed")
            if done is not None and not stopped:
                try:
                    done(said, round(job["ended"] - job["started"], 1))
                except Exception:              # pragma: no cover - kept or not, the job ended
                    pass

        threading.Thread(target=run, name=f"job-{ident}", daemon=True).start()
        return {"job": ident, "status": "running", "timeout": seconds,
                "note": "Running in the background. Go on with what does not depend on "
                        "it; then wait_for it - one call, returning when it ends. Stop it "
                        "with stop_job. It is stopped when this run ends."}

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
            job["told"] = True
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
        if job["status"] == "running":
            since = self._begin_waiting(job)
            if since is not None:
                said = self._job_said(job)
                said["note"] = self._already_waiting(job, since)
                return said
            try:
                end = time.time() + max(0, min(WAIT_JOB_MOST, int(args.get("wait") or 0)))
                while job["status"] == "running" and time.time() < end and not self.stopped():
                    time.sleep(0.5)
            finally:
                self._end_waiting(job)
        said = self._job_said(job)
        if said["status"] == "running" and (self.only is None or "wait_for" in self.only):
            said["note"] = STILL_RUNNING_NOTE.format(job=job["id"])
        return said

    # WAITING ONCE (the user, 2026-10-25): seen in try 6, two calls in one
    # turn waiting on the same job (wait 600 and wait 240), both answering
    # when it ended. A second call of the same agent waits for nothing: it
    # is told the first one is waiting. Another agent's (a helper's) is its own.
    def _begin_waiting(self, job: Dict[str, Any]) -> Optional[float]:
        """None when no call of this agent was waiting on the job - this one
        now is; else since when the one that is has been waiting."""
        with self._jobs_lock:
            waiting = job.setdefault("waiting", {})
            since = waiting.get(id(self))
            if since is None:
                waiting[id(self)] = time.time()
            return since

    def _end_waiting(self, job: Dict[str, Any]) -> None:
        with self._jobs_lock:
            job.get("waiting", {}).pop(id(self), None)

    @staticmethod
    def _already_waiting(job: Dict[str, Any], since: float) -> str:
        return ALREADY_WAITING_NOTE.format(job=job["id"], seconds=round(time.time() - since))

    def wait_for(self, args: Dict[str, Any]) -> Any:
        """Until what was asked for happens - a job ending (the first of
        several), a line it prints, a file appearing, the work's own server
        answering - or the time is up, looking every WAIT_LOOK seconds. On a
        job, until it ends (`WAIT_JOB_MOST`) unless a timeout is given."""
        named = [args.get("job")] + list(args.get("jobs") or [])
        idents = list(dict.fromkeys(str(one).strip() for one in named
                                    if str(one or "").strip()))
        pattern = str(args.get("output") or "")
        file = str(args.get("file") or "").strip()
        url = str(args.get("url") or "").strip()
        jobs = []
        for ident in idents:
            job = self.jobs.get(ident)
            if job is None:
                return self._no_such_job(ident)
            jobs.append(job)
        if pattern and len(jobs) != 1:
            return {"error": "output is watched in one job's printing: name that job"}
        if not (jobs or file or url):
            return {"error": "say what to wait for: a job, output (with its job), a file "
                             "or a url"}
        found = None
        if pattern:
            try:
                found = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
            except re.error as exc:
                return {"error": f"output is not a regular expression: {exc}"}
        target = None
        if file:
            target = self._readable(file)
        if url and not re.match(r"(?i)^http://(127\.0\.0\.1|localhost)(:\d+)?(/|$)", url):
            return {"error": "url is one of the work's own servers: http://127.0.0.1:<port>/..."}
        if jobs:
            seconds = max(1, min(WAIT_JOB_MOST, int(args.get("timeout") or WAIT_JOB_MOST)))
        else:
            seconds = max(1, min(WAIT_MOST, int(args.get("timeout") or 60)))
        mine: List[Dict[str, Any]] = []
        try:
            for job in jobs:
                if job["status"] != "running":
                    continue
                since = self._begin_waiting(job)
                if since is not None:
                    return self._with_jobs({"met": False, "waited": 0.0,
                                            "what": self._already_waiting(job, since)}, jobs)
                mine.append(job)
            return self._watching(jobs, found, target, url, seconds)
        finally:
            for job in mine:
                self._end_waiting(job)

    def _watching(self, jobs: List[Dict[str, Any]], found: Optional["re.Pattern[str]"],
                  target: Optional[Path], url: str, seconds: int) -> Dict[str, Any]:
        began = time.time()
        end = began + seconds
        tried_url = 0.0
        said: Dict[str, Any] = {}
        while not self.stopped():
            if found is not None:
                try:
                    text = (jobs[0]["folder"] / "output.log").read_text(encoding="utf-8",
                                                                         errors="replace")
                except OSError:
                    text = ""
                match = found.search(text)
                if match:
                    line_start = text.rfind("\n", 0, match.start()) + 1
                    line_end = text.find("\n", match.end())
                    said = {"met": True, "what": "the job printed it",
                            "line": text[line_start:line_end if line_end >= 0 else None][:500]}
                    break
            if target is not None and target.exists():
                said = {"met": True, "what": "the file is there", "file": str(target)}
                break
            if url and time.time() - tried_url >= WAIT_URL_LOOK:
                tried_url = time.time()
                answer = self._try_own_url(url)
                if answer is not None:
                    said = {"met": True, "what": "the server answered", "status": answer}
                    break
            ended = [job for job in jobs if job["status"] != "running"]
            if ended:
                said = {"met": found is None and target is None and not url,
                        "what": "; ".join(f"{job['id']} ended ({job['status']})"
                                          for job in ended)}
                going = [job["id"] for job in jobs if job["status"] == "running"]
                if going:
                    said["still_running"] = going
                break
            if time.time() >= end:
                said = {"met": False, "what": f"still waiting after {seconds} seconds"}
                break
            time.sleep(WAIT_LOOK)
        else:
            said = {"met": False, "what": "the run is stopping"}
        said["waited"] = round(time.time() - began, 1)
        return self._with_jobs(said, jobs)

    def _with_jobs(self, said: Dict[str, Any], jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """A wait's answer with the jobs it was on: one, as job_status says
        it; several, those that ended whole and the rest in a line each."""
        if len(jobs) == 1:
            said["job"] = self._job_said(jobs[0])
        elif jobs:
            said["jobs"] = [self._job_said(job) if job["status"] != "running" else
                            {"job": job["id"], "status": "running",
                             "seconds": round(time.time() - job["started"], 1)}
                            for job in jobs]
        return said

    def _try_own_url(self, url: str) -> Optional[int]:
        """The status a server of the work's answers `url` with, tried from
        inside the sandbox - where its own loopback is; None when nothing
        answers."""
        code = ("import urllib.request, urllib.error\n"
                "try:\n"
                f"    print(urllib.request.urlopen({url!r}, timeout=3).status)\n"
                "except urllib.error.HTTPError as e:\n"
                "    print(e.code)\n"
                "except Exception:\n"
                "    print('none')\n")
        try:
            result = self.runtime.run_python_code(code, timeout=20)
        except Exception:
            return None
        last = (getattr(result, "stdout", "") or "").strip().splitlines()[-1:] or ["none"]
        return int(last[0]) if last[0].isdigit() else None

    def jobs_ended(self) -> List[Dict[str, Any]]:
        """The jobs that ended since this was last asked - said once each,
        with every tool's result (`call`)."""
        out = []
        with self._jobs_lock:
            for one in self.jobs.values():
                if one["status"] != "running" and not one.get("told"):
                    one["told"] = True
                    printed = [line for line in str((one.get("result") or {}).get(
                        "stdout") or "").splitlines() if line.strip()]
                    out.append({"job": one["id"], "status": one["status"],
                                "seconds": round((one["ended"] or time.time())
                                                 - one["started"], 1),
                                "last_line": printed[-1][:300] if printed else ""})
        return out

    def start_helper(self, who: str, work: Callable[[], Dict[str, Any]],
                     halted: Dict[str, bool]) -> Dict[str, Any]:
        """A helper agent as a JOB of these tools: waited for, looked at and
        stopped as any job (job_status, wait_for, stop_job, jobs_ended)."""
        with self._jobs_lock:
            ident = f"job_{len(self.jobs) + 1}"
            folder = self.folder / ".agent_runtime" / "jobs" / f"{ident}-{uuid.uuid4().hex[:8]}"
            job = {"id": ident, "status": "running", "started": time.time(), "ended": 0.0,
                   "timeout": 0, "folder": folder, "result": None, "helper": who,
                   "halted": halted}
            self.jobs[ident] = job
        try:
            self.runtime.mkdir(folder)
            self.runtime.write_text(folder / "output.log", "")
        except Exception:                      # pragma: no cover - its log only
            pass

        def run() -> None:
            said = work()
            try:
                self.runtime.write_text(folder / "output.log",
                                        json.dumps(said, ensure_ascii=False, indent=1))
            except Exception:                  # pragma: no cover - its log only
                pass
            with self._jobs_lock:
                job["result"] = dict(said, helper=who)
                job["ended"] = time.time()
                job["status"] = ("stopped" if halted["halt"] else
                                 "failed" if said.get("error") or said.get("done") is False
                                 else "done")

        threading.Thread(target=run, name=f"helper-{ident}", daemon=True).start()
        return {"job": ident, "status": "running", "helper": who,
                "note": "The helper is working in the background: wait_for this job, or go "
                        "on - jobs_ended says when it is done; its answer is the job's "
                        "result (job_status)."}

    def hand_over(self, args: Dict[str, Any]) -> Any:
        if self.delegate is None:
            return {"error": "no helper agents here"}
        return self.delegate(args)

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
        if job.get("halted") is not None:
            # A helper: its tools refuse from now on, and it answers.
            job["halted"]["halt"] = True
            return
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
        ecosystem = str(args.get("ecosystem") or "pip").strip().lower()
        if ecosystem not in ("pip", "npm"):
            return {"error": "ecosystem is pip or npm"}
        if ecosystem == "pip":
            return self.install(list(packages or []), str(args.get("why") or ""))
        from ...runtime.filesystem import validate_write_path
        folder = Path(validate_write_path(str(args.get("folder") or self.folder),
                                          self.runtime.policy))
        if not folder.is_dir():
            return {"error": f"not a folder: {folder}"}
        said = self.install(list(packages or []), str(args.get("why") or ""), "npm", folder)
        if isinstance(said, dict) and not said.get("error") and said.get("folder"):
            self._claim([str(folder / "package.json"), str(folder / "package-lock.json")])
            self.changed("installed packages", [str(folder / "package.json")], said)
        return said

    # -- what is fetched from the network: outside, never run there (fetching.py) ----
    def _fetch_target(self, given: Any, default: Path) -> Path:
        from ...runtime.filesystem import validate_write_path
        return Path(validate_write_path(str(given or default), self.runtime.policy))

    def download(self, args: Dict[str, Any]) -> Any:
        url = str(args.get("url") or "").strip()
        refused = self.check_address(url)
        if refused:
            self.log("download refused", {"url": url[:300], "why": refused})
            return {"error": refused}
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(
            urllib.parse.urlsplit(url).path).name or "download")[:120] or "download"
        target = self._fetch_target(args.get("path"), self.folder / "downloads" / name)
        refused = self._refuse_locked(target)
        if refused:
            return refused
        if target.is_dir():
            return {"error": f"{target} is a folder - give the file's path"}
        self.runtime.mkdir(target.parent)
        said = fetching.download(url, target)
        if not said.get("error"):
            self._claim([str(target)])
            self.changed("downloaded", [str(target)], said)
        return said

    def git_fetch(self, args: Dict[str, Any]) -> Any:
        url = str(args.get("url") or "").strip()
        refused = self.check_address(url)
        if refused:
            self.log("git fetch refused", {"url": url[:300], "why": refused})
            return {"error": refused}
        name = re.sub(r"\.git$", "", Path(urllib.parse.urlsplit(url).path).name or "repo")
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:100] or "repo"
        target = self._fetch_target(args.get("path"), self.folder / name)
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            return {"error": f"{target} is there already - clone into a new folder"}
        try:
            depth = int(args.get("depth", 1) if args.get("depth") is not None else 1)
        except (TypeError, ValueError):
            depth = 1
        self.runtime.mkdir(target.parent)
        said = fetching.git_clone(url, target, self.folder / ".agent_runtime" / "home",
                                  depth=max(0, depth), ref=str(args.get("ref") or ""))
        if not said.get("error"):
            made = [str(one) for one in target.rglob("*") if one.is_file()][:5000]
            self._claim(made)
            self.changed("cloned", made[:200], said)
        return said

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

    def open_case(self, args: Dict[str, Any]) -> Any:
        if self.learned is None:
            return {"error": "nothing learned to open"}
        said = self.learned.open_case(str(args.get("id") or ""))
        self.know_addresses(said)
        return {"case": said}

    def open_reason(self, args: Dict[str, Any]) -> Any:
        if self.learned is None:
            return {"error": "nothing learned to open"}
        try:
            page = max(1, int(args.get("page") or 1))
        except (TypeError, ValueError):
            page = 1
        said = self.learned.open_reason(str(args.get("id") or ""), page)
        self.know_addresses(said)
        return {"reason": said}

    def search_records(self, args: Dict[str, Any]) -> Any:
        """The user's records, by their English words (context_search.py)."""
        if self.records_db is None:
            return {"error": "no records to search"}
        from .context_search import Index, records
        if self._records_index is None:
            self._records_index = Index(records(self.records_db,
                                                getattr(self, "leave_out", None)))
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
        allowed = self._may_read
        if self.as_of is not None:
            # As they were when the try began: one changed since is not given.
            def allowed(path: str) -> bool:
                return self._may_read(path) and not self._changed_since(path)
        found = self.files_index.search(query, allowed=allowed, limit=limit)
        for one in found:
            # The user's own files: where real addresses are.
            self.know_addresses(one.get("text") or "")
        return {"found": found} if found else {"found": [], "said": "nothing found for "
                                                                    "these words"}

    def _changed_since(self, path: str) -> bool:
        """Whether a file changed after the cutoff - or cannot be told."""
        try:
            return os.path.getmtime(path) > self.as_of.timestamp()
        except (OSError, AttributeError, TypeError, ValueError):
            return True

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