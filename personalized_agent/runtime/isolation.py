"""Operating system isolation for code the agent runs.

The in-process guard (guard.py) patches Python inside the child, and Python
can be un-patched from inside: `_io.FileIO(path, "w")` opens a file without
passing the patched `open`, the real `open` can be pulled out of the guarded
function's closure, and a native extension (numpy, or anything pip installs)
calls the operating system directly. All three were shown writing outside the
work's folder. So the rule - code writes only in the work's folder and the
folders the user granted, and reaches no network but what it is given - is
also kept by the operating system, which the child cannot patch:

  WINDOWS   an AppContainer (no administrator needed). The child runs with a
            token that can open only what an ACL names it in. Each folder it
            may write gets a capability of its own, granted "modify" on that
            folder; each folder it may read, a capability granted "read". A
            child is given the capabilities of exactly its folders, so one
            work's code cannot write another work's folder even though the
            ACL grants stay in place. No network capability: no connection
            out. Each run's code has a container of its own (named for the
            run's folder): Windows lets processes in one container reach
            each other on loopback, and nothing in another container nor
            on the computer itself - so a run can test a server it starts,
            and reaches no other run's, nor the application. The package
            installer alone gets "internetClient", in a container of its
            own.
  MACOS     Seatbelt (`sandbox-exec -p <profile>`): writes denied but in the
            folders, the credential stores and the application's own state
            unreadable, network denied - loopback too: Seatbelt cannot keep
            one run off another's ports - or outbound TCP 443 only for pip.
  LINUX     bubblewrap when it works here: the filesystem mounted read-only
            but the folders, the credential stores and the application's
            state hidden, its own network namespace for run_python - no
            network, and a loopback of the run's own.
            Else Landlock (kernel 5.13+): writes only beneath the folders;
            from Landlock ABI 4 on, TCP connect to 443 only (pip) or none.

Where none of these is available the child runs as before - the in-process
guard alone - and `isolation_status()` says so, with why, so the work panel
can say "not OS-isolated" plainly and installs cannot be automatic.

The guard stays inside the child in every case, as a second layer: it gives
a readable error at the point of the attempt, and it filters the package
installer's network by host name, which no backend here can do.

This file imports only the standard library at the top: on Linux it is also
run as a script, as the small launcher that applies Landlock to itself and
then becomes the real child (`python isolation.py --landlock-exec ...`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

#: No connection at all.
NETWORK_NONE = "none"
#: Outbound HTTPS only - the package installer; the guard inside narrows it
#: to the package index's host names.
NETWORK_PYPI = "pypi"
#: Unrestricted, as before: code the user's own chat agent runs. Work runs
#: never get it.
NETWORK_FULL = "full"
NETWORK_MODES = (NETWORK_NONE, NETWORK_PYPI, NETWORK_FULL)

#: Set in the child's environment: the backend that confines it, or "none".
OS_ISOLATION_ENV = "AGENT_OS_ISOLATION"
#: Set beside no network when the backend gives the run a loopback of its
#: own (see `own_loopback`); runtime/guard.py then lets connections to
#: 127.0.0.1 through, and the operating system keeps them to the run.
OWN_LOOPBACK_ENV = "AGENT_OWN_LOOPBACK"

#: The flag the Landlock launcher is run with (as a script, or the packaged
#: application re-entering itself - see __main__.py).
LANDLOCK_FLAG = "--landlock-exec"

#: The AppContainers children run in: one for code the agent runs, one for
#: the package installer. What a child may write is set by the capabilities
#: it is started with; the two containers differ only in what each is
#: DENIED - code may not change the installed packages, pip may.
APP_CONTAINER = "PersonalizedAgent.Work"
PIP_CONTAINER = "PersonalizedAgent.Pip"

#: Folders, by name, beneath the folders a child may write that it still may
#: not change: the run's saved versions (kept by the application, which
#: rolls the work back from them) and the installed packages (pip's alone).
VERSIONS_NAME = ".versions"
PACKAGES_NAME = ".packages"
#: How deep beneath a writable folder such a folder is looked for
#: (<work>/runs/<run>/.versions is three down).
_NAMED_DEPTH = 4

_WINDOWS = sys.platform.startswith("win")
_MAC = sys.platform == "darwin"
_LINUX = sys.platform.startswith("linux")

_status_lock = threading.Lock()
_status: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# What a child may never read, wherever it is allowed to read
# ---------------------------------------------------------------------------
_MAC_LIBRARY_SECRETS = (
    "Keychains", "Cookies", "Safari", "Mail", "Messages", "Accounts",
    "Containers", "Group Containers", "Application Support/Google",
    "Application Support/Firefox", "Application Support/BraveSoftware",
    "Application Support/Microsoft Edge", "Application Support/com.apple.TCC",
)


def credential_stores(home: Optional[Path] = None) -> List[Path]:
    """Where the user's sign-ins live, under their home.

    A folder granted for reading never carries these with it - its own
    entry and everything beneath stay closed - unless the folder asked for
    is itself inside one (a work folder under the temp directory in
    AppData, say): what is named exactly is granted.

    Every entry of the home whose name starts with a dot (.ssh, .aws,
    .azure, .gnupg, .kube, .docker, .config, .netrc, .git-credentials ...),
    and per system: AppData and the registry hives on Windows (credentials,
    DPAPI keys, browser profiles and cookies); on macOS the keychains,
    cookies, mail, messages and browser profiles under ~/Library."""
    home = Path(home or Path.home())
    found: List[Path] = []
    try:
        names = os.listdir(home)
    except OSError:
        names = []
    for name in names:
        lower = name.lower()
        if name.startswith("."):
            found.append(home / name)
        elif _WINDOWS and (lower == "appdata" or lower.startswith("ntuser")):
            found.append(home / name)
    if _WINDOWS and (home / "AppData") not in found:
        found.append(home / "AppData")
    if _MAC:
        found += [home / "Library" / one for one in _MAC_LIBRARY_SECRETS]
    return found


def _real(path: Any) -> Path:
    try:
        return Path(os.path.realpath(os.fspath(path)))
    except (OSError, ValueError):
        return Path(os.path.abspath(os.fspath(path)))


def _key(path: Path) -> str:
    return os.path.normcase(str(path))


def _within(path: Path, root: Path) -> bool:
    """`path` is `root` or beneath it."""
    a, b = _key(path), _key(root).rstrip("\\/")
    return a == b or a.startswith(b + os.sep)


def _distinct(paths: Iterable[Any]) -> List[Path]:
    """Real paths, without one beneath another already listed."""
    out: List[Path] = []
    for one in sorted({_real(p) for p in paths if p}, key=lambda p: len(str(p))):
        if not any(_within(one, kept) for kept in out):
            out.append(one)
    return out


#: THE FOLDERS THE GUARD IS LOADED FROM (runtime/sandbox.ensure_guard): a
#: copy of guard.py - and on Linux of this file, the Landlock launcher -
#: outside the application's code, which a run of the Experience debugger
#: may not read. Each is inside the application's protected state, so no
#: run can write it; it is the one folder of that state a child is let read
#: (`_reopened`). Every one made in this process is listed: tests make
#: several, and a child is granted them all rather than race for one.
_GUARD_HOMES: List[Path] = []
_guard_lock = threading.Lock()


def note_guard_home(folder: Any) -> Path:
    """Remember a folder the guard is loaded from; it, resolved."""
    real = _real(folder)
    with _guard_lock:
        if not any(_key(real) == _key(one) for one in _GUARD_HOMES):
            _GUARD_HOMES.append(real)
    return real


def guard_homes() -> List[Path]:
    """The folders the guard is loaded from that are there now."""
    with _guard_lock:
        return [one for one in _GUARD_HOMES if one.is_dir()]


#: Other folders of the protected state every child may read, as it may the
#: guard's: the testing browser the user installed, and the helper runs
#: import to drive it (runtime/browser.py). Read only, like the guard's.
_READING_HOMES: List[Path] = []


def note_reading_home(folder: Any) -> Path:
    """Remember a folder of the protected state every child may read."""
    real = _real(folder)
    with _guard_lock:
        if not any(_key(real) == _key(one) for one in _READING_HOMES):
            _READING_HOMES.append(real)
    return real


def reading_homes() -> List[Path]:
    """The guard's folders and the others every child may read, there now."""
    with _guard_lock:
        return [one for one in [*_GUARD_HOMES, *_READING_HOMES] if one.is_dir()]


def _reopened(path: Path, hidden: Sequence[Path]) -> bool:
    """Whether a folder every child must read may be given it: not when it
    is inside what is hidden - unless it is a guard home, or another folder
    kept in the application's state for every child to read (see
    `_GUARD_HOMES`, `_READING_HOMES`)."""
    if not any(_within(path, h) for h in hidden):
        return True
    return any(_key(path) == _key(one) for one in reading_homes())


def launcher_path() -> str:
    """This file, as the Landlock launcher runs it: the copy beside the
    guard when there is one - the original is in the application's code,
    which bubblewrap hides from a debugger run it starts the launcher in."""
    for home in guard_homes():
        copy = home / os.path.basename(__file__)
        if copy.is_file():
            return str(copy)
    return os.path.abspath(__file__)


def code_paths() -> List[Path]:
    """What every child reads to start at all: the interpreter (the base
    installation and the virtual environment, if any) and the folder the
    guard is imported from - a copy outside the application's code (see
    `_GUARD_HOMES`), never the code itself. In a packaged application, the
    folder the bundle runs from."""
    paths: List[Any] = [os.path.dirname(sys.executable)]
    if getattr(sys, "frozen", False):
        paths.append(getattr(sys, "_MEIPASS", None))
    else:
        paths += [sys.base_prefix, sys.prefix,
                  os.path.dirname(getattr(sys, "_base_executable", "") or sys.executable)]
        paths += guard_homes()
    with _guard_lock:
        paths += list(_READING_HOMES)
    return _distinct(p for p in paths if p and os.path.isdir(p))


#: READABLE ROOTS MADE READABLE IN THE BACKGROUND, by key: their state
#: ("running", "done", "failed") and how far along. A grant on the home
#: writes an entry onto every file beneath it - Dropbox and all - which
#: Windows does one file at a time: it held a run's first script for half an
#: hour. So it is made once, soon after the application starts
#: (`prepare_reading`), and a run never waits for it: a root still being
#: prepared is simply not readable to that run's code yet.
_BACKGROUND: Dict[str, Dict[str, Any]] = {}


def prepare_reading(readable: Sequence[Any], hidden: Sequence[Any]) -> None:
    """Make `readable` readable to the agents' code, once - the same grant a
    run would make, with the same exclusions, so a run afterwards finds it
    there. Blocking: run it on a thread of its own. Windows' AppContainer
    only; nothing elsewhere, where reading needs no grant."""
    status = isolation_status()
    if not status.get("isolated") or status.get("backend") != "appcontainer":
        return
    hidden_roots = [_real(p) for p in hidden if p]
    secrets = credential_stores()
    for root in _distinct([_real(p) for p in readable if p]):
        if any(_within(root, h) for h in hidden_roots):
            continue
        held = _BACKGROUND[_key(root)] = {
            "root": str(root), "state": "running", "done": 0, "total": 0,
            "at": "", "started_at": time.time(), "finished_at": None}

        def progress(done: int, total: int, path: Any, held=held) -> None:
            held.update(done=done, total=total, at=str(path))

        try:
            _AppContainer.grant("Read", root, [*hidden_roots, *secrets],
                                progress=progress)
            held.update(state="done", done=held["total"], at="",
                        finished_at=time.time())
            log.info("the agents' code can read %s (made readable in %.0fs)",
                     root, held["finished_at"] - held["started_at"])
        except Exception as exc:          # pragma: no cover - logged, retried next start
            held.update(state="failed", error=str(exc), finished_at=time.time())
            log.warning("could not make %s readable to the agents' code", root,
                        exc_info=True)


def forget_grants(folder: Any) -> None:
    """A folder made anew in the place of one granted to the agents' code -
    a debugger way's, cleared: what this process remembers as granted there
    is forgotten, so the next child grants the new folder again."""
    real = _real(folder)
    names = {capability_name(kind, real) for kind in ("Write", "Read")}
    with _AppContainer._grant_lock:
        _AppContainer._granted = {memo for memo in _AppContainer._granted
                                  if memo[0] not in names}


def seal_from_containers(folders: Sequence[Any]) -> List[str]:
    """SEAL each folder from the agents' code - from every AppContainer that
    holds no grant of the folder's own: what it inherits from above (the
    one-time read grant on the home, any grant on a folder holding it) stops
    reaching it, and the entries its own runs were given stay. So a run
    whose readable roots hold the folder cannot open it, and the run that
    writes there still can. The folders the Experience debugger's ways work
    in are sealed so - from each other - when they are made and before every
    run; an AppContainer's access is decided by its allow entries alone, so
    a deny entry would not do it. Done once is enough: sealing again changes
    nothing. Windows' AppContainer only; elsewhere sealed folders are hidden
    per run (see `run_isolated`). The folders it sealed now."""
    status = isolation_status()
    if not status.get("isolated") or status.get("backend") != "appcontainer":
        return []
    done = []
    api = _AppContainer.api()
    for folder in _distinct([_real(p) for p in folders if p]):
        if not folder.is_dir():
            continue
        try:
            with _AppContainer._grant_lock:
                if api.seal(str(folder)):
                    done.append(str(folder))
        except OSError:
            log.warning("could not seal %s from the agents' code", folder, exc_info=True)
    return done


class CodeSeal:
    """THE APPLICATION'S CODE, SEALED FROM THE AGENTS' CODE WHILE A WAY OF
    THE EXPERIENCE DEBUGGER RUNS - and open again when none does.

    The user's decision: a way being measured may not read how the system
    measuring it works; real work may read it. On Windows the home is made
    readable to every container once (`prepare_reading`), so the code is
    readable to all of them or to none: sealed (`_WinApi.seal`) while any
    debugger run is alive - real runs cannot read it then, which they do
    not need - and unsealed (`_WinApi.unseal`) when the last one ends, and
    as the application starts, when none can be alive. Elsewhere it is
    hidden per run (`run_isolated`), and this does nothing.

    COUNTED: each debugger run holds it (`hold`) from before its first
    child until its thread ends (`release`). What is on disk follows the
    count, not the calls: one worker thread at a time brings the folder to
    what the count asks for NOW, and looks again - so a run that starts as
    the last one ends, or two that end together, leave it as the count
    says, whatever order the threads ran in. The work is never done on the
    caller's thread: the first seal rewrites what every file in the folder
    inherits, which takes seconds. A run's child waits for it (`wait`)
    before it starts, so no debugger run's code ever starts unsealed.

    Not the debugger's own three folders: those are sealed for good, from
    each other (`seal_from_containers`), and never unsealed."""

    _lock = threading.Lock()
    _changed = threading.Condition(_lock)
    #: key -> how many runs hold it sealed
    _holds: Dict[str, int] = {}
    _roots: Dict[str, Path] = {}
    #: key -> what is on disk as last made here: True sealed, False open;
    #: absent - not known yet (the first look makes it so).
    _applied: Dict[str, bool] = {}
    _worker: Optional[threading.Thread] = None
    #: key -> how the last change went, for the status page and the tests.
    _last: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def hold(cls, folder: Any) -> None:
        """A debugger run starts: sealed until it ends."""
        key, root = _key(_real(folder)), _real(folder)
        with cls._lock:
            cls._roots[key] = root
            cls._holds[key] = cls._holds.get(key, 0) + 1
            cls._kick()

    @classmethod
    def release(cls, folder: Any) -> None:
        """A debugger run ended: open again if it was the last."""
        key, root = _key(_real(folder)), _real(folder)
        with cls._lock:
            cls._roots[key] = root
            cls._holds[key] = max(0, cls._holds.get(key, 0) - 1)
            cls._kick()

    @classmethod
    def settle(cls, folder: Any) -> None:
        """As the application starts: as the count says - open, when no
        debugger run is alive (a seal left by one the application ended
        with is taken off)."""
        key, root = _key(_real(folder)), _real(folder)
        with cls._lock:
            cls._roots[key] = root
            cls._holds.setdefault(key, 0)
            cls._applied.pop(key, None)       # look at the disk again
            cls._kick()

    @classmethod
    def held(cls, folder: Any) -> int:
        with cls._lock:
            return cls._holds.get(_key(_real(folder)), 0)

    @classmethod
    def wait(cls, folder: Any, timeout: float = 600.0) -> bool:
        """Until what is on disk is what the count asks for; False when it
        took longer than `timeout`."""
        key = _key(_real(folder))
        deadline = time.time() + timeout
        with cls._lock:
            while cls._applied.get(key) != (cls._holds.get(key, 0) > 0):
                if key not in cls._holds:
                    return True                # nobody asked: nothing to wait for
                left = deadline - time.time()
                if left <= 0:
                    return False
                cls._changed.wait(min(left, 1.0))
            return True

    @classmethod
    def sealed_roots(cls) -> List[Path]:
        """The folders sealed on disk now, as last made here."""
        with cls._lock:
            return [cls._roots[key] for key, sealed in cls._applied.items()
                    if sealed and key in cls._roots]

    @classmethod
    def status(cls) -> List[Dict[str, Any]]:
        with cls._lock:
            return [{"root": str(cls._roots[key]), "holds": cls._holds.get(key, 0),
                     "sealed": cls._applied.get(key), **cls._last.get(key, {})}
                    for key in cls._roots]

    @classmethod
    def reset(cls) -> None:
        """Forget every count - for tests, after the worker is done."""
        with cls._lock:
            cls._holds.clear()
            cls._roots.clear()
            cls._applied.clear()
            cls._last.clear()

    # -- the worker -------------------------------------------------------------
    @classmethod
    def _kick(cls) -> None:
        """With the lock held: a worker, unless one is going."""
        if cls._worker is not None and cls._worker.is_alive():
            return
        cls._worker = threading.Thread(target=cls._work, name="code-seal", daemon=True)
        cls._worker.start()

    @classmethod
    def _work(cls) -> None:
        while True:
            with cls._lock:
                todo = [(key, cls._holds.get(key, 0) > 0) for key in cls._roots
                        if cls._applied.get(key) != (cls._holds.get(key, 0) > 0)]
                if not todo:
                    cls._worker = None
                    cls._changed.notify_all()
                    return
                key, sealed = todo[0]
                root = cls._roots[key]
            began = time.time()
            try:
                changed = cls._apply(root, sealed)
                outcome = {"state": "sealed" if sealed else "open", "changed": changed,
                           "seconds": round(time.time() - began, 2)}
            except Exception as exc:          # pragma: no cover - logged, tried next time
                log.warning("could not %s the application's code at %s",
                            "seal" if sealed else "unseal", root, exc_info=True)
                outcome = {"state": "failed", "error": str(exc)}
            with cls._lock:
                # Marked as asked even when it failed: a worker that tried
                # again and again would hold every run that waits for it.
                cls._applied[key] = sealed
                cls._last[key] = outcome
                cls._changed.notify_all()

    @staticmethod
    def _apply(root: Path, sealed: bool) -> bool:
        """Seal or unseal on disk; whether anything was written. Windows'
        AppContainer only."""
        status = isolation_status()
        if not status.get("isolated") or status.get("backend") != "appcontainer":
            return False
        if not root.is_dir():
            return False
        api = _AppContainer.api()
        with _AppContainer._grant_lock:
            changed = api.seal(str(root), keep_writes=True) if sealed \
                else api.unseal(str(root))
        if changed:
            log.info("the application's code at %s is %s the agents' code", root,
                     "sealed from" if sealed else "open again to")
        return bool(changed)


def reading_status() -> List[Dict[str, Any]]:
    """Each root being made readable in the background, and how far."""
    return [dict(one) for one in _BACKGROUND.values()]


def reading_not_ready() -> List[Dict[str, Any]]:
    return [dict(one) for one in _BACKGROUND.values() if one["state"] != "done"]


def grant_plan(root: Path, excluded: Sequence[Path]) -> List[Tuple[Path, bool]]:
    """How to open `root` without opening anything in `excluded`: a list of
    (path, whole) - whole means the path and everything beneath, else the
    entry alone (a folder that is listed, not entered).

    Only exclusions strictly beneath the root prune it; the root itself is
    granted even if it lies inside one (see credential_stores) - except a
    hidden path, which the caller drops before asking. Links and junctions
    on the way down are not followed: a junction under the home on Windows
    ("Application Data") leads straight back into AppData."""
    root = _real(root)
    inside = [one for one in (_real(e) for e in excluded)
              if _within(one, root) and _key(one) != _key(root)]
    if not inside:
        return [(root, True)]
    plan: List[Tuple[Path, bool]] = [(root, False)]
    try:
        entries = list(os.scandir(root))
    except OSError:
        return plan
    for entry in entries:
        child = Path(entry.path)
        if any(_key(child) == _key(one) for one in inside):
            continue
        try:
            if entry.is_symlink() or _is_reparse(entry):
                continue
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            continue
        if is_dir:
            plan += grant_plan(child, [one for one in inside if _within(one, child)])
        else:
            plan.append((child, True))
    return plan


def find_named(roots: Sequence[Path], names: Sequence[str],
               depth: int = _NAMED_DEPTH) -> List[Path]:
    """Every existing folder called one of `names` within `depth` levels of
    the roots - not followed through links, not looked inside."""
    wanted = {name.lower() for name in names}
    found: List[Path] = []
    if not wanted:
        return found

    def walk(folder: Path, left: int) -> None:
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return
        for entry in entries:
            try:
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if entry.name.lower() in wanted:
                found.append(Path(entry.path))
            elif left > 1 and entry.name != ".agent_runtime":
                walk(Path(entry.path), left - 1)

    for root in roots:
        if root.name.lower() in wanted:
            found.append(root)
        else:
            walk(root, depth)
    return found


def _is_reparse(entry: os.DirEntry) -> bool:
    try:
        attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT


# ---------------------------------------------------------------------------
# The one way in
# ---------------------------------------------------------------------------
def isolation_status() -> Dict[str, Any]:
    """{"isolated": bool, "backend": str, "why": str, "network": bool}.

    `isolated`: writes are confined by the operating system. `network`:
    the network is too (Landlock before ABI 4 confines writes only). Found
    out once, by trying, and kept."""
    global _status
    with _status_lock:
        if _status is None:
            _status = _detect()
        return dict(_status)


def reset_status() -> None:
    """Find out again on the next call - for tests."""
    global _status
    with _status_lock:
        _status = None


def _detect() -> Dict[str, Any]:
    if os.environ.get("PERSONALIZED_AGENT_NO_OS_ISOLATION"):
        return _unisolated("turned off by PERSONALIZED_AGENT_NO_OS_ISOLATION")
    try:
        if _WINDOWS:
            return _AppContainer.detect()
        if _MAC:
            return _Seatbelt.detect()
        if _LINUX:
            found = _Bubblewrap.detect()
            if found["isolated"]:
                return found
            landlock = _Landlock.detect()
            if landlock["isolated"]:
                landlock["why"] = f"{landlock['why']} (bubblewrap: {found['why']})"
                return landlock
            return _unisolated(f"bubblewrap: {found['why']}; Landlock: {landlock['why']}")
    except Exception as exc:                   # a probe must never break a run
        return _unisolated(f"{type(exc).__name__}: {exc}")
    return _unisolated(f"no isolation backend for {sys.platform}")


def own_loopback(status: Optional[Dict[str, Any]] = None) -> bool:
    """Whether code run with no network still reaches the servers its own
    run starts on 127.0.0.1 - and nothing else there: a Windows container
    per run, a Linux network namespace per run (bubblewrap). Not Seatbelt,
    which can open loopback only as a whole, to every program on the
    computer; not Landlock, whose ports are the same for every process."""
    status = status or isolation_status()
    return bool(status.get("isolated")) and status.get("backend") in (
        "appcontainer", "bubblewrap")


def _unisolated(why: str) -> Dict[str, Any]:
    return {"isolated": False, "backend": "none", "why": why, "network": False}


def describe() -> str:
    """One line for a run's report."""
    status = isolation_status()
    if not status["isolated"]:
        return f"not OS-isolated: {status['why']}"
    if not status["network"]:
        return f"OS-isolated ({status['backend']}; network: in-process guard only)"
    return f"OS-isolated ({status['backend']})"


def run_isolated(argv: Sequence[str], *, cwd: Any, writable: Sequence[Any],
                 readable: Optional[Sequence[Any]] = None,
                 hidden: Sequence[Any] = (), network: str = NETWORK_NONE,
                 readonly_names: Sequence[str] = (VERSIONS_NAME, PACKAGES_NAME),
                 env: Optional[Dict[str, str]] = None,
                 timeout: Optional[float] = None,
                 sealed: Sequence[Any] = (),
                 code: Optional[Any] = None,
                 closed: Sequence[Any] = ()) -> subprocess.CompletedProcess:
    """Run `argv` confined by the operating system, as `subprocess.run`
    would with captured text output; raises `subprocess.TimeoutExpired`
    (the whole tree ended) when it runs out of time.

    `writable`: the only folders it may write. `readable`: what it may read
    besides those and the interpreter - honoured exactly on Windows; on
    macOS and Linux the whole filesystem is readable but `hidden` and the
    credential stores. `hidden`: never readable (the application's own
    state). `network`: none, pypi or full. `readonly_names`: folders by
    name beneath the writable ones that it may read and not change - see
    VERSIONS_NAME; enforced on folders that exist when it starts (on macOS
    by a pattern, so also on any it would make), and not by Landlock, whose
    rules can only open a whole folder: there the guard alone keeps them.

    `sealed`: folders it may not read either - another way's, in the
    Experience debugger. Hidden like the application's state on macOS and
    Linux; on Windows each is sealed from every container but its own (see
    `seal_from_containers`) - hidden as the state is, every readable root
    above it would be granted again file by file. Landlock cannot hide a
    folder from reading: there the guard alone keeps them.

    `code`: the application's own code, for a run of the Experience
    debugger: it may not READ it (writing is not touched), but for the
    interpreter when that is kept in it. On Windows by the folder's ACL
    while any debugger run is alive (`CodeSeal`; this waits for it); on
    macOS by denying the reading of what is in it; under bubblewrap by
    covering it - unless a folder it writes holds the code, where covering
    would lose those writes and the guard alone keeps it. Landlock cannot
    hide it: the guard alone.

    `closed`: folders INSIDE what it writes that it may neither read nor
    write - the project's run logs. On Windows sealed from every container
    (`seal_from_containers`: what they inherit of the writable folder's
    grant stops at them; the user, and the application writing the logs as
    the user, keep theirs); on macOS denied after every opening; under
    bubblewrap covered over the writable binds. Landlock can neither hide
    nor close a folder inside an open one: the guard alone.

    With no backend available it runs unconfined - the guard inside is all
    there is - which `isolation_status()` reports."""
    if network not in NETWORK_MODES:
        raise ValueError(f"network must be one of {NETWORK_MODES}")
    argv = [str(one) for one in argv]
    cwd = _real(cwd)
    writable_roots = _distinct(writable)
    hidden_paths = [_real(p) for p in hidden if p]
    sealed_paths = [_real(p) for p in sealed if p]
    readable_roots = [_real(p) for p in (readable or []) if p]
    code_root = _real(code) if code else None
    closed_paths = [_real(p) for p in closed if p]
    status = isolation_status()
    backend = status["backend"] if status["isolated"] else "none"
    # The child is told what confines it (the guard adapts to it - see
    # guard.OS_ISOLATION_ENV).
    env = dict(os.environ if env is None else env)
    env[OS_ISOLATION_ENV] = backend
    env.pop(OWN_LOOPBACK_ENV, None)
    if network == NETWORK_NONE and own_loopback(status):
        env[OWN_LOOPBACK_ENV] = "1"
    if backend == "appcontainer":
        seal_from_containers([*sealed_paths, *closed_paths])
        if code_root is not None and not CodeSeal.wait(code_root):
            log.warning("the application's code is not sealed yet: the guard alone "
                        "keeps this child from reading it")
        return _AppContainer.run(argv, cwd, writable_roots, readable_roots,
                                 hidden_paths, network, readonly_names, env, timeout)
    if backend == "seatbelt":
        full = ["sandbox-exec", "-p",
                seatbelt_profile(writable_roots, [*hidden_paths, *sealed_paths], network,
                                 readonly_names=readonly_names, code=code_root,
                                 closed=closed_paths), *argv]
        return _run_posix(full, cwd, env, timeout)
    if backend == "bubblewrap":
        inner = argv
        if network == NETWORK_PYPI and _Landlock.abi() >= 4:
            # bubblewrap shares the network with pip; Landlock narrows it
            # to TCP 443 where the kernel can.
            inner = landlock_command(argv, None, [443])
        full = bubblewrap_command(inner, cwd, writable_roots, readable_roots,
                                  [*hidden_paths, *sealed_paths], network,
                                  readonly=find_named(writable_roots, readonly_names),
                                  code=code_root, closed=closed_paths)
        return _run_posix(full, cwd, env, timeout)
    if backend == "landlock":
        ports: Optional[List[int]] = None
        if _Landlock.abi() >= 4 and network != NETWORK_FULL:
            ports = [443] if network == NETWORK_PYPI else []
        full = landlock_command(argv, writable_roots, ports)
        return _run_posix(full, cwd, env, timeout)
    return _run_posix(argv, cwd, env, timeout)


def _run_posix(argv: List[str], cwd: Path, env: Optional[Dict[str, str]],
               timeout: Optional[float]) -> subprocess.CompletedProcess:
    """Through the application's children module - ended with the agent,
    the whole tree killed on a timeout - when imported as part of it. The
    whole tree also when the child ends: the Python children its code may
    start (runtime/guard.py) must not outlive the run, as on Windows the
    job ends them (`_WinApi.launch`)."""
    try:
        from ..common import children
    except Exception:                          # run as a plain script
        return subprocess.run(argv, cwd=str(cwd), env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    return children.run(argv, cwd=str(cwd), env=env, capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=timeout,
                        whole_tree=True)


# ---------------------------------------------------------------------------
# macOS - Seatbelt
# ---------------------------------------------------------------------------
def _sbpl(path: Path) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _sbpl_regex(text: str) -> str:
    """Literal text inside a Seatbelt regex #"...": regex characters
    escaped, and the quote."""
    out = []
    for char in text:
        if char in ".^$*+?()[]{}|\\":
            out.append("\\" + char)
        elif char == '"':
            out.append('\\"')
        else:
            out.append(char)
    return "".join(out)


def seatbelt_profile(writable: Sequence[Path], hidden: Sequence[Path],
                     network: str, home: Optional[Path] = None,
                     readonly_names: Sequence[str] = (),
                     code: Optional[Path] = None,
                     closed: Sequence[Path] = ()) -> str:
    """The profile: everything allowed but writes, the secrets and the
    network. Later rules win in a Seatbelt profile, so each opening comes
    after the denial it narrows. `code`: the application's own code, its
    files not to be read, and `closed`, folders inside the writable ones
    neither read nor written (see `run_isolated`)."""
    home = Path(home or Path.home())
    lines = ["(version 1)", "(allow default)",
             # Another application driven by Apple events, or the keychain
             # asked for a password: both would act with the user's
             # authority, outside every rule below.
             "(deny appleevent-send)",
             '(deny mach-lookup (global-name "com.apple.SecurityServer") '
             '(global-name "com.apple.securityd") (global-name "com.apple.secd"))',
             "(deny file-write*)"]
    for root in writable:
        lines.append(f"(allow file-write* (subpath {_sbpl(root)}))")
    lines.append('(allow file-write* (literal "/dev/null") (literal "/dev/zero") '
                 '(literal "/dev/dtracehelper") (literal "/dev/tty") '
                 '(regex #"^/dev/fd/"))')
    secrets = _distinct([*credential_stores(home), *hidden])
    # The interpreter may live under a dot-folder (~/.pyenv): read again.
    reopened = [p for p in code_paths() if _reopened(p, hidden)]
    if secrets:
        lines.append("(deny file-read* file-write* "
                     + " ".join(f"(subpath {_sbpl(p)})" for p in secrets) + ")")
    if reopened:
        lines.append("(allow file-read* "
                     + " ".join(f"(subpath {_sbpl(p)})" for p in reopened) + ")")
    # The folders it writes are its own, wherever they are.
    writable_open = [root for root in writable
                     if not any(_within(root, h) for h in hidden)]
    if writable_open:
        lines.append("(allow file-read* file-write* "
                     + " ".join(f"(subpath {_sbpl(p)})" for p in writable_open) + ")")
    if code is not None:
        # After every opening above, so none reopens it: what is in the
        # code is not read - its data; it is still seen, and written where
        # the rules above let it be - and the interpreter, when it lives
        # there, is read again.
        lines.append(f"(deny file-read-data (subpath {_sbpl(code)}))")
        inside = [p for p in code_paths() if _within(p, code)]
        if inside:
            lines.append("(allow file-read-data "
                         + " ".join(f"(subpath {_sbpl(p)})" for p in inside) + ")")
    if closed:
        # After the writable folders' opening, which holds them.
        lines.append("(deny file-read* file-write* "
                     + " ".join(f"(subpath {_sbpl(p)})" for p in closed) + ")")
    # Last, so nothing above opens them again: the named folders anywhere
    # beneath the writable ones - those there now and any it would make.
    for root in writable:
        for name in readonly_names:
            pattern = f"^{_sbpl_regex(str(root))}(/.*)?/{_sbpl_regex(name)}(/.*)?$"
            lines.append(f'(deny file-write* (regex #"{pattern}"))')
    if network == NETWORK_NONE:
        lines.append("(deny network*)")
    elif network == NETWORK_PYPI:
        # Seatbelt filters by port, not by host name: any host on 443 for
        # pip alone; the guard inside allows only the package index's names.
        lines += ["(deny network*)",
                  '(allow network-outbound (remote tcp "*:443"))',
                  '(allow network-outbound (literal "/private/var/run/mDNSResponder"))']
    return "\n".join(lines) + "\n"


class _Seatbelt:
    @staticmethod
    def detect() -> Dict[str, Any]:
        tool = shutil.which("sandbox-exec") or (
            "/usr/bin/sandbox-exec" if os.path.exists("/usr/bin/sandbox-exec") else None)
        if not tool:
            return _unisolated("sandbox-exec is not on this Mac")
        # The real profile, with every kind of rule used, must compile.
        probe = seatbelt_profile([_real(os.getcwd())], [], NETWORK_PYPI,
                                 code=_real(os.path.dirname(os.path.abspath(__file__))))
        try:
            done = subprocess.run([tool, "-p", probe, "/usr/bin/true"],
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=30)
        except Exception as exc:
            return _unisolated(f"sandbox-exec could not run: {exc}")
        if done.returncode != 0:
            return _unisolated("sandbox-exec refused the profile: "
                               + (done.stderr or "").strip()[:300])
        return {"isolated": True, "backend": "seatbelt", "network": True,
                "why": "Seatbelt (sandbox-exec)"}


# ---------------------------------------------------------------------------
# Linux - bubblewrap, else Landlock
# ---------------------------------------------------------------------------
def bubblewrap_command(argv: Sequence[str], cwd: Path, writable: Sequence[Path],
                       readable: Sequence[Path], hidden: Sequence[Path],
                       network: str, home: Optional[Path] = None,
                       bwrap: str = "bwrap",
                       readonly: Sequence[Path] = (),
                       code: Optional[Path] = None,
                       closed: Sequence[Path] = ()) -> List[str]:
    """The filesystem read-only, a private /tmp, the secrets covered, the
    folders it writes bound writable - in that order, each step on top of
    the one before. `code`: the application's own code, covered too; and
    `closed`, covered over the writable binds (see `run_isolated`)."""
    home = Path(home or Path.home())
    args = [bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
            "--tmpfs", "/tmp"]
    # What it was asked to read and lies under /tmp, back on top of the
    # private one (a script's own folder, in tests).
    for root in readable:
        if _within(root, Path("/tmp")) and root.exists():
            args += ["--ro-bind", str(root), str(root)]
    for secret in _distinct([*credential_stores(home), *hidden]):
        if secret.is_dir():
            args += ["--tmpfs", str(secret)]
        elif secret.exists():
            args += ["--ro-bind", "/dev/null", str(secret)]
    # Covered - unless a folder it writes holds it: the writes there would
    # land in the cover and be lost, and writing is not this rule's to
    # change. Then the guard inside alone keeps the code from it.
    cover_code = code is not None and not any(_within(code, root) for root in writable)
    if cover_code:
        args += ["--tmpfs", str(code)]
    for root in code_paths():
        if not _reopened(root, hidden):
            continue
        # The interpreter, or the guard's folder, under something covered:
        # the credential stores, the protected state, /tmp - or the code.
        if any(_within(root, s) for s in [*credential_stores(home), *hidden,
                                           Path("/tmp")]) \
                or (cover_code and _within(root, code)):
            args += ["--ro-bind", str(root), str(root)]
    for root in writable:
        args += ["--bind", str(root), str(root)]
    # Read-only again, over the writable binds: the run's versions, the
    # installed packages.
    for folder in readonly:
        args += ["--ro-bind", str(folder), str(folder)]
    # The run logs, over the writable folder that holds them: what code
    # writes there lands in the cover and is gone.
    for folder in closed:
        if Path(folder).is_dir():
            args += ["--tmpfs", str(folder)]
    if network == NETWORK_NONE:
        args.append("--unshare-net")
    args += ["--unshare-pid", "--die-with-parent", "--new-session",
             "--chdir", str(cwd), "--", *argv]
    return args


class _Bubblewrap:
    @staticmethod
    def detect() -> Dict[str, Any]:
        tool = shutil.which("bwrap")
        if not tool:
            return _unisolated("bwrap is not installed")
        try:
            done = subprocess.run(
                [tool, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
                 "--unshare-net", "--unshare-pid", "--die-with-parent",
                 "--new-session", "--", "/bin/true"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=30)
        except Exception as exc:
            return _unisolated(f"bwrap could not run: {exc}")
        if done.returncode != 0:
            # Most often: unprivileged user namespaces are turned off.
            return _unisolated("bwrap could not start a sandbox here: "
                               + (done.stderr or "").strip()[:300])
        return {"isolated": True, "backend": "bubblewrap", "network": True,
                "why": "bubblewrap"}


# Landlock (include/uapi/linux/landlock.h). The syscall numbers are the same
# on every architecture: they were added after the tables were unified.
_SYS_CREATE_RULESET, _SYS_ADD_RULE, _SYS_RESTRICT_SELF = 444, 445, 446
_LL_WRITE_FILE, _LL_REMOVE_DIR, _LL_REMOVE_FILE = 1 << 1, 1 << 4, 1 << 5
_LL_MAKE = sum(1 << bit for bit in range(6, 13))   # char dir reg sock fifo block sym
_LL_REFER, _LL_TRUNCATE = 1 << 13, 1 << 14
_LL_NET_BIND, _LL_NET_CONNECT = 1 << 0, 1 << 1


def landlock_command(argv: Sequence[str], writable: Optional[Sequence[Path]],
                     ports: Optional[Sequence[int]]) -> List[str]:
    """The launcher, then the child: it confines itself and becomes `argv`.
    `writable` None: no filesystem rule; `ports` None: no network rule,
    [] none at all, [443] that port alone."""
    spec = json.dumps({"writable": None if writable is None else [str(p) for p in writable],
                       "ports": None if ports is None else list(ports)})
    if getattr(sys, "frozen", False):
        return [sys.executable, LANDLOCK_FLAG, spec, "--", *argv]
    return [sys.executable, "-I", launcher_path(), LANDLOCK_FLAG, spec,
            "--", *argv]


class _Landlock:
    _abi: Optional[int] = None

    @classmethod
    def abi(cls) -> int:
        if cls._abi is None:
            cls._abi = _landlock_abi()
        return cls._abi

    @classmethod
    def detect(cls) -> Dict[str, Any]:
        abi = cls.abi()
        if abi < 1:
            return _unisolated("Landlock is not available in this kernel")
        return {"isolated": True, "backend": "landlock", "network": abi >= 4,
                "why": f"Landlock ABI {abi}" + ("" if abi >= 4 else
                                               " - the network is not confined")}


def _libc() -> Any:
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


def _landlock_abi() -> int:
    if not _LINUX:
        return 0
    try:
        import ctypes
        version = _libc().syscall(ctypes.c_long(_SYS_CREATE_RULESET), None,
                                  ctypes.c_size_t(0), ctypes.c_uint32(1))
        return int(version) if version > 0 else 0
    except Exception:
        return 0


def landlock_restrict(writable: Optional[Sequence[str]], ports: Optional[Sequence[int]]) -> None:
    """Confine this process and everything it starts. Raises if it cannot -
    a launcher that went on unconfined would be worse than one that fails."""
    import ctypes

    abi = _landlock_abi()
    if abi < 1:
        raise OSError("Landlock is not available")
    libc = _libc()

    class RulesetAttr(ctypes.Structure):
        _fields_ = [("fs", ctypes.c_uint64), ("net", ctypes.c_uint64)]

    class PathBeneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]

    class NetPort(ctypes.Structure):
        _fields_ = [("allowed", ctypes.c_uint64), ("port", ctypes.c_uint64)]

    write_rights = _LL_WRITE_FILE | _LL_REMOVE_DIR | _LL_REMOVE_FILE | _LL_MAKE
    if abi >= 2:
        write_rights |= _LL_REFER
    if abi >= 3:
        write_rights |= _LL_TRUNCATE
    attr = RulesetAttr(write_rights if writable is not None else 0, 0)
    if ports is not None:
        if abi < 4:
            raise OSError("this kernel's Landlock cannot confine the network")
        attr.net = _LL_NET_BIND | _LL_NET_CONNECT
    size = ctypes.sizeof(RulesetAttr) if abi >= 4 else 8
    ruleset = libc.syscall(ctypes.c_long(_SYS_CREATE_RULESET), ctypes.byref(attr),
                           ctypes.c_size_t(size), ctypes.c_uint32(0))
    if ruleset < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset")

    def add(kind: int, rule: Any) -> None:
        if libc.syscall(ctypes.c_long(_SYS_ADD_RULE), ctypes.c_int(ruleset),
                        ctypes.c_int(kind), ctypes.byref(rule), ctypes.c_uint32(0)) < 0:
            raise OSError(ctypes.get_errno(), "landlock_add_rule")

    file_rights = _LL_WRITE_FILE | (_LL_TRUNCATE if abi >= 3 else 0)
    targets = [(root, write_rights) for root in (writable or [])]
    if writable is not None:
        targets += [(device, file_rights) for device in
                    ("/dev/null", "/dev/zero", "/dev/tty", "/dev/full")]
    for path, rights in targets:
        try:
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        except OSError:
            continue
        try:
            if not os.path.isdir(path):
                rights &= _LL_WRITE_FILE | _LL_TRUNCATE
            add(1, PathBeneath(rights, fd))     # LANDLOCK_RULE_PATH_BENEATH
        finally:
            os.close(fd)
    for port in (ports or []):
        add(2, NetPort(_LL_NET_CONNECT, int(port)))   # LANDLOCK_RULE_NET_PORT
    if libc.prctl(38, 1, 0, 0, 0) != 0:         # PR_SET_NO_NEW_PRIVS
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_NO_NEW_PRIVS)")
    if libc.syscall(ctypes.c_long(_SYS_RESTRICT_SELF), ctypes.c_int(ruleset),
                    ctypes.c_uint32(0)) < 0:
        raise OSError(ctypes.get_errno(), "landlock_restrict_self")
    os.close(ruleset)


def landlock_exec_main(args: Sequence[str]) -> int:
    """`<spec> -- argv...`: confine, then become argv."""
    args = list(args)
    if len(args) < 3 or args[1] != "--":
        print(f"usage: {LANDLOCK_FLAG} <spec> -- <command...>", file=sys.stderr)
        return 2
    spec = json.loads(args[0])
    try:
        landlock_restrict(spec.get("writable"), spec.get("ports"))
    except Exception as exc:
        print(f"could not confine the process: {exc}", file=sys.stderr)
        return 126
    command = args[2:]
    os.execvp(command[0], command)
    return 127                                   # never reached


# ---------------------------------------------------------------------------
# Windows - AppContainer
# ---------------------------------------------------------------------------
#: FILE_GENERIC_READ | FILE_GENERIC_EXECUTE, and "modify" (that plus write
#: and delete) - what icacls calls RX and M.
_READ_EXECUTE = 0x1200A9
_MODIFY = 0x1301BF
#: The bits of an entry that read: the data (and a folder's list), the
#: extended attributes, execute (mapping a file is reading it) - and the
#: generic read, execute and all.
_READING_BITS = 0x1 | 0x8 | 0x20 | 0x80000000 | 0x20000000 | 0x10000000
#: The bits that write: data, appending (a folder's new files and
#: folders), extended attributes, deleting a child, attributes, deleting,
#: the generic write and all.
_WRITING_BITS = 0x2 | 0x4 | 0x10 | 0x40 | 0x100 | 0x10000 | 0x40000000 | 0x10000000
#: "modify" without reading: what a seal that keeps writing leaves of a
#: write grant (see `_WinApi.seal`). No grant this application makes
#: writes without reading, so an entry of this shape is a seal's own -
#: which is how `_WinApi.unseal` knows what to take off.
_WRITE_ONLY = _MODIFY & ~_READING_BITS & 0xFFFFFFFF
#: The well-known "internetClient" capability: outbound internet, no LAN,
#: no loopback.
_INTERNET_CLIENT = "S-1-15-3-1"
_ALL_APP_PACKAGES = "S-1-15-2-1"


def run_container(root: Path) -> str:
    """The container a run's code runs in, named for the run's own folder
    (its first writable one): its processes reach each other on loopback,
    and no other run's (see the module's notes). Windows' limit on a name
    is 64 characters."""
    digest = hashlib.sha256(_key(_real(root)).encode("utf-8")).hexdigest()[:24]
    return f"{APP_CONTAINER}.{digest}"


def capability_name(kind: str, path: Path) -> str:
    """One capability per folder and kind of access: a child holds those of
    its own folders only."""
    digest = hashlib.sha256(_key(_real(path)).encode("utf-8")).hexdigest()[:32]
    return f"{APP_CONTAINER}.{kind}.{digest}"


class _AppContainer:
    _api: Any = None
    _granted: set = set()
    _grant_lock = threading.Lock()
    #: Children alive in each run's container: its profile is deleted when
    #: the last ends, so containers do not pile up one per folder ever run.
    _live: Dict[str, int] = {}
    _live_lock = threading.Lock()

    # -- the API, bound once ------------------------------------------------
    @classmethod
    def api(cls) -> Any:
        if cls._api is None:
            cls._api = _WinApi()
        return cls._api

    @classmethod
    def detect(cls) -> Dict[str, Any]:
        """Start cmd.exe in the container - System32 is readable to every
        container without any grant - and see its exit code come back."""
        try:
            api = cls.api()
            system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
            done = api.launch([os.path.join(system, "cmd.exe"), "/d", "/c", "exit 7"],
                              cwd=system, env=None, capabilities=[], timeout=30)
        except Exception as exc:
            return _unisolated(f"an AppContainer could not be started: {exc}")
        if done.returncode != 7:
            return _unisolated(f"the AppContainer probe ended with {done.returncode}: "
                               + (done.stderr or "").strip()[:200])
        return {"isolated": True, "backend": "appcontainer", "network": True,
                "why": "AppContainer"}

    @classmethod
    def grant(cls, kind: str, root: Path, excluded: Sequence[Path],
              progress: Optional[Any] = None) -> Optional[bytes]:
        """Grant `kind` (read or write) on `root`, but not on what is
        excluded beneath it, to that folder's own capability; its SID, or
        None when the root does not exist. Once per folder per process;
        idempotent on disk too - an ACL that already holds the entry is
        left as it is."""
        api = cls.api()
        name = capability_name(kind, root)
        sid = api.capability_sid(name)
        memo = (name, tuple(sorted(_key(p) for p in excluded if _within(p, root))))
        if memo in cls._granted:
            return sid
        if not root.exists():
            return None
        mask = _MODIFY if kind == "Write" else _READ_EXECUTE
        with cls._grant_lock:
            plan = grant_plan(root, excluded)
            for done, (path, whole) in enumerate(plan):
                if progress is not None:
                    progress(done, len(plan), path)
                inherit = whole and path.is_dir()
                if not whole and api.has_inheritable(str(path), sid):
                    # A folder that is only listed must not pass its grant
                    # down: something beneath it is excluded. An earlier
                    # grant made with other exclusions (or none) would
                    # otherwise keep that excluded folder open - taking the
                    # entry off takes what it passed down off with it.
                    api.revoke(str(path), sid)
                if api.has_access(str(path), sid, mask, inherit):
                    continue
                api.grant(str(path), sid, mask, inherit)
            cls._granted.add(memo)
        return sid

    @classmethod
    def withdraw(cls, kind: str, root: Path) -> None:
        """Take a folder's grant off again, and what it passed down - for
        the user who wants the ACLs back as they were (the grants open the
        folder to this application's containers only)."""
        api = cls.api()
        sid = api.capability_sid(capability_name(kind, root))

        # Entries of its own are only where a grant was placed: on a folder
        # and, where it was only listed, on what is inside it - so only a
        # folder that still names the capability itself is looked into.
        def take_off(path: Path) -> None:
            api.revoke(str(path), sid)
            try:
                entries = list(os.scandir(path)) if path.is_dir() else []
            except OSError:
                entries = []
            for entry in entries:
                if entry.is_symlink() or _is_reparse(entry):
                    continue
                if api.has_entry(entry.path, sid):
                    take_off(Path(entry.path))

        with cls._grant_lock:
            if root.exists():
                take_off(_real(root))
            cls._granted = {m for m in cls._granted
                            if m[0] != capability_name(kind, root)}

    @staticmethod
    def temp_inside(env: Optional[Dict[str, str]], writable: List[Path],
                    container: str = APP_CONTAINER) -> Dict[str, str]:
        """Windows rewrites a container's LOCALAPPDATA to
        <LOCALAPPDATA>\\Packages\\<container>\\AC, and its TEMP and TMP to
        the Temp folder in that - whatever they were given as. So
        LOCALAPPDATA is pointed into the first folder it may write, and its
        temporary files land there too, not in the profile's storage
        (closed, above) or the user's own temp. Windows will not start the
        process without one at all."""
        env = dict(os.environ if env is None else env)
        local = env.get("LOCALAPPDATA")
        if writable and not (local and any(_within(_real(local), root)
                                           for root in writable)):
            local = str(writable[0] / ".agent_runtime" / "localappdata")
        local = local or os.environ.get("LOCALAPPDATA") or str(Path.home())
        env["LOCALAPPDATA"] = local
        if writable:
            os.makedirs(os.path.join(local, "Packages", container.lower(),
                                     "AC", "Temp"), exist_ok=True)
        return env

    @classmethod
    def run(cls, argv: List[str], cwd: Path, writable: List[Path],
            readable: List[Path], hidden: List[Path], network: str,
            readonly_names: Sequence[str], env: Optional[Dict[str, str]],
            timeout: Optional[float]) -> subprocess.CompletedProcess:
        api = cls.api()
        if network == NETWORK_PYPI:
            container = PIP_CONTAINER
        else:
            container = run_container(writable[0]) if writable else APP_CONTAINER
        with cls._live_lock:
            cls._live[container] = cls._live.get(container, 0) + 1
        try:
            return cls._run_in(container, argv, cwd, writable, readable, hidden,
                               network, readonly_names, env, timeout)
        finally:
            with cls._live_lock:
                cls._live[container] -= 1
                if not cls._live[container]:
                    del cls._live[container]
                    if container not in (APP_CONTAINER, PIP_CONTAINER):
                        api.forget_container(container)

    @classmethod
    def _read_roots(cls, reads: Sequence[Any]) -> List[Path]:
        """The folders to grant reading on: one beneath another is folded
        into it, as its grant reaches it - unless it, or a folder between
        the two, is SEALED (an Experience debugger way's): a sealed folder
        inherits nothing from above, and only a grant of its own opens it.
        Folded in, a way's slides were refused every file of the work they
        drew."""
        named = list(dict.fromkeys(_real(one) for one in reads if one))
        outer = _distinct(named)
        api = cls.api()
        out = list(outer)
        for one in named:
            if one in out:
                continue
            above = next((root for root in outer if _within(one, root)), None)
            at = one
            while above is not None and at != above and _within(at, above):
                try:
                    sealed = api.is_protected(str(at))
                except OSError:
                    sealed = True              # not known: granted, to be sure
                if sealed:
                    out.append(one)
                    break
                at = at.parent
        return out

    @classmethod
    def _run_in(cls, container: str, argv: List[str], cwd: Path, writable: List[Path],
                readable: List[Path], hidden: List[Path], network: str,
                readonly_names: Sequence[str], env: Optional[Dict[str, str]],
                timeout: Optional[float]) -> subprocess.CompletedProcess:
        api = cls.api()
        container_sid = api.container_sid(container)
        secrets = credential_stores()
        capabilities: List[bytes] = []
        own: Dict[str, bytes] = {}              # writable root -> its capability
        for root in writable:
            if any(_within(root, h) for h in hidden):
                continue
            sid = cls.grant("Write", root, [*hidden, *secrets])
            if sid:
                capabilities.append(sid)
                own[_key(root)] = sid
        # The application's code, while it is sealed (CodeSeal): a folder
        # this child writes that holds it passed its grant down into it
        # before the seal - and a grant made since stops at it. Writing is
        # not the seal's to change, so the writing half is given there
        # explicitly; unsealing takes it off again (`_WinApi.unseal`).
        for code in CodeSeal.sealed_roots():
            for key, sid in own.items():
                if _within(code, Path(key)) and _key(code) != key \
                        and not api.has_access(str(code), sid, _WRITE_ONLY, True):
                    with cls._grant_lock:
                        api.grant(str(code), sid, _WRITE_ONLY, True)
        # The named folders, after the grants above (which then stop at
        # them). An AppContainer's access is decided by ALLOW entries alone -
        # a deny entry naming the container or a capability is not
        # consulted, which was tried - so a folder this child may not change
        # stops inheriting, and keeps only the reading half of this child's
        # write grants. Another child's grant there (pip's, on .packages) is
        # kept as it was; a folder this child MAY change, that an earlier
        # child closed, gets this child's grant back explicitly.
        closed = {name.lower() for name in readonly_names}
        for folder in find_named(writable, (VERSIONS_NAME, PACKAGES_NAME)):
            holders = [sid for key, sid in own.items() if _within(folder, Path(key))]
            if folder.name.lower() in closed:
                api.close_for(str(folder), holders)
            else:
                for sid in holders:
                    if not api.has_access(str(folder), sid, _MODIFY, True):
                        api.grant(str(folder), sid, _MODIFY, True)
        # The interpreter and the app's code on their own, never folded into
        # a wider readable folder: a grant on the home leaves AppData out,
        # and Python is usually installed under AppData\Local\Programs.
        for root in code_paths():
            if not _reopened(root, hidden):
                continue
            sid = cls.grant("Read", root, hidden)
            if sid:
                capabilities.append(sid)
        reads = list(readable)
        if not any(_within(cwd, root) for root in [*writable, *reads]):
            reads.append(cwd)
        for root in cls._read_roots(reads):
            if any(_within(root, h) for h in hidden):
                continue
            held = _BACKGROUND.get(_key(root))
            if held is not None and held["state"] != "done":
                # Still being made readable in the background: not this
                # child's to wait for. Its code cannot read there yet.
                continue
            sid = cls.grant("Read", root, [*hidden, *secrets])
            if sid:
                capabilities.append(sid)
        if network in (NETWORK_PYPI, NETWORK_FULL):
            capabilities.append(api.sid_from_string(_INTERNET_CLIENT))
        program = argv[0]
        if not os.path.isabs(program):
            program = shutil.which(program, path=(env or {}).get("PATH")) or program
        env = cls.temp_inside(env, writable, container)
        # Each once: Windows refuses the same capability twice (error 87),
        # and a folder can be both code and readable (the guard's own).
        capabilities = list(dict.fromkeys(capabilities))
        return api.launch([program, *argv[1:]], cwd=str(cwd), env=env,
                          capabilities=capabilities, timeout=timeout,
                          container=container_sid)


class _WinApi:
    """The Windows calls, through ctypes: SIDs, ACLs, and a process started
    in the AppContainer, in a job that ends it and all it starts."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes as w

        self.ctypes = ctypes
        self.w = w
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        uenv = ctypes.WinDLL("userenv", use_last_error=True)
        kbase = ctypes.WinDLL("kernelbase", use_last_error=True)
        P = ctypes.c_void_p
        PP = ctypes.POINTER(ctypes.c_void_p)

        def bind(dll, name, res, *args):
            fn = getattr(dll, name)
            fn.restype, fn.argtypes = res, list(args)
            return fn

        self.DeriveAppContainerSid = bind(
            uenv, "DeriveAppContainerSidFromAppContainerName", ctypes.c_long,
            w.LPCWSTR, PP)
        self.CreateAppContainerProfile = bind(
            uenv, "CreateAppContainerProfile", ctypes.c_long, w.LPCWSTR, w.LPCWSTR,
            w.LPCWSTR, P, w.DWORD, PP)
        self.DeleteAppContainerProfile = bind(
            uenv, "DeleteAppContainerProfile", ctypes.c_long, w.LPCWSTR)
        self.GetAppContainerFolderPath = bind(
            uenv, "GetAppContainerFolderPath", ctypes.c_long, w.LPCWSTR,
            ctypes.POINTER(ctypes.c_wchar_p))
        self.CoTaskMemFree = bind(ctypes.WinDLL("ole32"), "CoTaskMemFree", None, P)
        self.ConvertSidToStringSid = bind(adv, "ConvertSidToStringSidW", w.BOOL, P,
                                          ctypes.POINTER(ctypes.c_wchar_p))
        self.DeriveCapabilitySids = bind(
            kbase, "DeriveCapabilitySidsFromName", w.BOOL, w.LPCWSTR,
            ctypes.POINTER(PP), ctypes.POINTER(w.DWORD), ctypes.POINTER(PP),
            ctypes.POINTER(w.DWORD))
        self.ConvertStringSidToSid = bind(adv, "ConvertStringSidToSidW", w.BOOL,
                                          w.LPCWSTR, PP)
        self.GetLengthSid = bind(adv, "GetLengthSid", w.DWORD, P)
        self.EqualSid = bind(adv, "EqualSid", w.BOOL, P, P)
        self.FreeSid = bind(adv, "FreeSid", P, P)
        self.LocalFree = bind(k32, "LocalFree", P, P)
        self.GetNamedSecurityInfo = bind(
            adv, "GetNamedSecurityInfoW", w.DWORD, w.LPCWSTR, ctypes.c_int, w.DWORD,
            PP, PP, PP, PP, PP)
        self.SetNamedSecurityInfo = bind(
            adv, "SetNamedSecurityInfoW", w.DWORD, w.LPWSTR, ctypes.c_int, w.DWORD,
            P, P, P, P)
        self.SetEntriesInAcl = bind(adv, "SetEntriesInAclW", w.DWORD, w.ULONG, P, P, PP)
        self.GetAclInformation = bind(adv, "GetAclInformation", w.BOOL, P, P,
                                      w.DWORD, ctypes.c_int)
        self.GetAce = bind(adv, "GetAce", w.BOOL, P, w.DWORD, PP)
        self.GetSecurityDescriptorControl = bind(
            adv, "GetSecurityDescriptorControl", w.BOOL, P, ctypes.POINTER(w.WORD),
            ctypes.POINTER(w.DWORD))
        self.InitializeProcThreadAttributeList = bind(
            k32, "InitializeProcThreadAttributeList", w.BOOL, P, w.DWORD, w.DWORD,
            ctypes.POINTER(ctypes.c_size_t))
        self.UpdateProcThreadAttribute = bind(
            k32, "UpdateProcThreadAttribute", w.BOOL, P, w.DWORD, ctypes.c_size_t,
            P, ctypes.c_size_t, P, P)
        self.DeleteProcThreadAttributeList = bind(
            k32, "DeleteProcThreadAttributeList", None, P)
        self.CreateProcess = bind(
            k32, "CreateProcessW", w.BOOL, w.LPCWSTR, w.LPWSTR, P, P, w.BOOL,
            w.DWORD, P, w.LPCWSTR, P, P)
        self.CreateJobObject = bind(k32, "CreateJobObjectW", w.HANDLE, P, w.LPCWSTR)
        self.SetInformationJobObject = bind(k32, "SetInformationJobObject", w.BOOL,
                                            w.HANDLE, ctypes.c_int, P, w.DWORD)
        self.AssignProcessToJobObject = bind(k32, "AssignProcessToJobObject",
                                             w.BOOL, w.HANDLE, w.HANDLE)
        self.TerminateJobObject = bind(k32, "TerminateJobObject", w.BOOL,
                                       w.HANDLE, w.UINT)
        self.ResumeThread = bind(k32, "ResumeThread", w.DWORD, w.HANDLE)
        self.TerminateProcess = bind(k32, "TerminateProcess", w.BOOL, w.HANDLE, w.UINT)
        self.WaitForSingleObject = bind(k32, "WaitForSingleObject", w.DWORD,
                                        w.HANDLE, w.DWORD)
        self.GetExitCodeProcess = bind(k32, "GetExitCodeProcess", w.BOOL, w.HANDLE,
                                       ctypes.POINTER(w.DWORD))
        self.CloseHandle = bind(k32, "CloseHandle", w.BOOL, w.HANDLE)
        self._sids: Dict[str, bytes] = {}
        self._containers: Dict[str, bytes] = {}

    # -- SIDs -------------------------------------------------------------
    def _copy_sid(self, pointer: Any) -> bytes:
        return self.ctypes.string_at(pointer, self.GetLengthSid(pointer))

    def container_sid(self, name: str = APP_CONTAINER) -> bytes:
        if name not in self._containers:
            self._containers[name] = self._make_container(name)
        return self._containers[name]

    def forget_container(self, name: str) -> None:
        """Delete a container's profile (its storage and registry key go
        with it); a later child in it makes it again, with the same SID."""
        self._containers.pop(name, None)
        try:
            self.DeleteAppContainerProfile(name)
        except Exception:                     # pragma: no cover - left for next time
            log.debug("could not delete the container %s", name, exc_info=True)

    def _make_container(self, name: str) -> bytes:
        """The container's SID, its profile made first if it is not there:
        Windows will not start a process in a container that has none.

        A profile comes with storage of the container's own - a folder under
        %LOCALAPPDATA%\\Packages and a registry key - which the container
        may write. That would be one more place outside the work's folder,
        so the container's entry is taken off both, every time (it costs a
        lookup when it is already off)."""
        ct = self.ctypes
        pointer = ct.c_void_p()
        result = self.CreateAppContainerProfile(
            name, name.replace(".", " "),
            "Code the Personalized Agent runs for a piece of work", None, 0,
            ct.byref(pointer))
        if result & 0xFFFFFFFF == 0x800700B7:     # the profile already exists
            result = self.DeriveAppContainerSid(name, ct.byref(pointer))
        if result != 0:
            raise OSError(f"AppContainer profile: 0x{result & 0xFFFFFFFF:08x}")
        try:
            sid = self._copy_sid(pointer)
        finally:
            self.FreeSid(pointer)
        self._close_own_storage(sid, name)
        return sid

    def _close_own_storage(self, sid: bytes, name: str) -> None:
        ct = self.ctypes
        text = ct.c_wchar_p()
        buffer = ct.create_string_buffer(sid, len(sid))
        if not self.ConvertSidToStringSid(buffer, ct.byref(text)):
            raise ct.WinError(ct.get_last_error())
        try:
            sid_text = text.value
        finally:
            self.LocalFree(ct.cast(text, ct.c_void_p))
        folder = ct.c_wchar_p()
        if self.GetAppContainerFolderPath(sid_text, ct.byref(folder)) == 0:
            try:
                where = folder.value
            finally:
                self.CoTaskMemFree(ct.cast(folder, ct.c_void_p))
            # The folder is "...\Packages\<name>\AC"; its parent holds it.
            for path in (where, os.path.dirname(where)):
                if os.path.isdir(path) and self.has_entry(path, sid, 1):
                    self.revoke(path, sid, 1)
        key = ("CURRENT_USER\\Software\\Classes\\Local Settings\\Software\\Microsoft"
               "\\Windows\\CurrentVersion\\AppContainer\\Storage\\"
               + name.lower())
        try:
            if self.has_entry(key, sid, 4):                  # SE_REGISTRY_KEY
                self.revoke(key, sid, 4)
        except OSError:
            pass                                 # no key, nothing to close

    def capability_sid(self, name: str) -> bytes:
        if name in self._sids:
            return self._sids[name]
        ct, w = self.ctypes, self.w
        groups, sids = ct.POINTER(ct.c_void_p)(), ct.POINTER(ct.c_void_p)()
        group_count, sid_count = w.DWORD(), w.DWORD()
        if not self.DeriveCapabilitySids(name, ct.byref(groups), ct.byref(group_count),
                                         ct.byref(sids), ct.byref(sid_count)):
            raise ct.WinError(ct.get_last_error())
        try:
            if sid_count.value < 1:
                raise OSError(f"no capability SID for {name}")
            found = self._copy_sid(sids[0])
        finally:
            for i in range(group_count.value):
                self.LocalFree(groups[i])
            for i in range(sid_count.value):
                self.LocalFree(sids[i])
            self.LocalFree(ct.cast(groups, ct.c_void_p))
            self.LocalFree(ct.cast(sids, ct.c_void_p))
        self._sids[name] = found
        return found

    def sid_from_string(self, text: str) -> bytes:
        pointer = self.ctypes.c_void_p()
        if not self.ConvertStringSidToSid(text, self.ctypes.byref(pointer)):
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        try:
            return self._copy_sid(pointer)
        finally:
            self.LocalFree(pointer)

    # -- ACLs -------------------------------------------------------------
    def _dacl(self, path: str, kind: int = 1) -> Tuple[Any, Any]:
        """The DACL of a file (kind 1) or registry key (4), and the
        descriptor that holds it - LocalFree the descriptor."""
        ct = self.ctypes
        dacl, descriptor = ct.c_void_p(), ct.c_void_p()
        error = self.GetNamedSecurityInfo(path, kind, 0x4, None, None, ct.byref(dacl),
                                          None, ct.byref(descriptor))
        if error:
            raise ct.WinError(error, f"reading the ACL of {path}")
        return dacl, descriptor

    def _entries(self, dacl: Any) -> Iterable[Tuple[int, int, int, int]]:
        """(type, flags, access mask, address of the SID) of each entry."""
        ct, w = self.ctypes, self.w

        class SizeInfo(ct.Structure):
            _fields_ = [("AceCount", w.DWORD), ("AclBytesInUse", w.DWORD),
                        ("AclBytesFree", w.DWORD)]

        info = SizeInfo()
        if not self.GetAclInformation(dacl, ct.byref(info), ct.sizeof(info), 2):
            raise ct.WinError(ct.get_last_error())
        for index in range(info.AceCount):
            ace = ct.c_void_p()
            if not self.GetAce(dacl, index, ct.byref(ace)):
                continue
            header = ct.string_at(ace, 8)
            yield (header[0], header[1], int.from_bytes(header[4:8], "little"),
                   ace.value + 8)

    def has_access(self, path: str, sid: bytes, mask: int, inherit: bool) -> bool:
        """An allow entry for `sid` - or for every app container - with all
        of `mask`, inheritable when that is asked for."""
        ct = self.ctypes
        dacl, descriptor = self._dacl(path)
        try:
            if not dacl:                       # a NULL DACL: open to all
                return True
            wanted = ct.create_string_buffer(sid, len(sid))
            apps_sid = self.sid_from_string(_ALL_APP_PACKAGES)
            apps = ct.create_string_buffer(apps_sid, len(apps_sid))
            for kind, flags, access, at in self._entries(dacl):
                if kind != 0 or flags & 0x08 or access & mask != mask:
                    continue                   # not allow, inherit-only, too little
                if inherit and flags & 0x03 != 0x03:
                    continue
                if self.EqualSid(at, wanted) or self.EqualSid(at, apps):
                    return True
            return False
        finally:
            self.LocalFree(descriptor)

    def has_inheritable(self, path: str, sid: bytes) -> bool:
        """An allow entry of its own (not inherited) for `sid` that passes
        itself down to what is inside."""
        ct = self.ctypes
        dacl, descriptor = self._dacl(path)
        try:
            if not dacl:
                return False
            wanted = ct.create_string_buffer(sid, len(sid))
            return any(kind == 0 and not flags & 0x10 and flags & 0x03
                       and self.EqualSid(at, wanted)
                       for kind, flags, _access, at in self._entries(dacl))
        finally:
            self.LocalFree(descriptor)

    def has_entry(self, path: str, sid: bytes, kind: int = 1) -> bool:
        """Any entry of its own (not inherited) naming `sid`."""
        ct = self.ctypes
        dacl, descriptor = self._dacl(path, kind)
        try:
            if not dacl:
                return False
            wanted = ct.create_string_buffer(sid, len(sid))
            return any(not flags & 0x10 and self.EqualSid(at, wanted)
                       for _kind, flags, _access, at in self._entries(dacl))
        finally:
            self.LocalFree(descriptor)

    def close_for(self, path: str, sids: Sequence[bytes]) -> bool:
        """Make `path` read-only to holders of `sids`: it stops inheriting
        (what it inherited is kept, as entries of its own) and every allow
        entry naming one of them keeps only its read and execute bits.
        Windows carries the result down to what is inside. False when it was
        so already - nothing is written then."""
        ct, w = self.ctypes, self.w
        dacl, descriptor = self._dacl(path)
        try:
            if not dacl:
                raise OSError(f"{path} has no ACL to close")
            control, revision = w.WORD(), w.DWORD()
            self.GetSecurityDescriptorControl(descriptor, ct.byref(control),
                                              ct.byref(revision))
            protected = bool(control.value & 0x1000)
            buffers = [ct.create_string_buffer(sid, len(sid)) for sid in sids]
            header = ct.string_at(dacl, 8)
            denies: List[bytes] = []
            allows: List[bytes] = []
            changed = False
            for kind, flags, access, at in self._entries(dacl):
                raw = bytearray(ct.string_at(at - 8, int.from_bytes(
                    ct.string_at(at - 6, 2), "little")))
                if kind == 0 and any(self.EqualSid(at, b) for b in buffers) \
                        and access & ~_READ_EXECUTE & 0xFFFFFFFF:
                    access &= _READ_EXECUTE
                    changed = True
                    if not access:
                        continue
                raw[1] = flags & ~0x10 & 0xFF            # no longer INHERITED_ACE
                raw[4:8] = access.to_bytes(4, "little")
                (denies if kind in (1, 6) else allows).append(bytes(raw))
            if protected and not changed:
                return False
            body = b"".join(denies + allows)
            acl = (bytes([header[0], 0]) + (8 + len(body)).to_bytes(2, "little")
                   + len(denies + allows).to_bytes(2, "little") + b"\0\0" + body)
            new_dacl = ct.create_string_buffer(acl, len(acl))
            # DACL, and PROTECTED: it no longer takes what its parent passes on.
            error = self.SetNamedSecurityInfo(path, 1, 0x4 | 0x80000000, None, None,
                                              new_dacl, None)
            if error:
                raise ct.WinError(error, f"closing {path}")
            return True
        finally:
            self.LocalFree(descriptor)

    def _is_app_sid(self, at: int) -> bool:
        """Whether the SID at this address is a package's or a capability's
        (S-1-15-2-... or S-1-15-3-...): what the agents' containers are
        allowed by."""
        ct = self.ctypes
        head = ct.string_at(at, 8)
        if head[1] < 1 or head[2:8] != bytes([0, 0, 0, 0, 0, 15]):
            return False
        first = int.from_bytes(ct.string_at(at + 8, 4), "little")
        return first in (2, 3)

    def seal(self, path: str, keep_writes: bool = False) -> bool:
        """Take `path` out of what containers inherit: it stops inheriting
        (what it inherited is kept as entries of its own) except every allow
        entry for a package or a capability, which it inherited from above;
        its own entries - the grants of the runs that write in it - stay.
        Windows carries the result down to what is inside. False when it was
        sealed already - nothing is written then.

        `keep_writes`: sealed from READING alone - an inherited entry that
        writes stays, as one of its own, with its reading bits taken off
        (`_WRITE_ONLY`): a run granted a folder above this one still writes
        in it as before, and cannot read it. The application's code, while
        a debugger run is alive (`CodeSeal`)."""
        ct, w = self.ctypes, self.w
        dacl, descriptor = self._dacl(path)
        try:
            if not dacl:
                raise OSError(f"{path} has no ACL to seal")
            control, revision = w.WORD(), w.DWORD()
            self.GetSecurityDescriptorControl(descriptor, ct.byref(control),
                                              ct.byref(revision))
            protected = bool(control.value & 0x1000)
            header = ct.string_at(dacl, 8)
            denies: List[bytes] = []
            allows: List[bytes] = []
            dropped = False
            for kind, flags, access, at in self._entries(dacl):
                raw = bytearray(ct.string_at(at - 8, int.from_bytes(
                    ct.string_at(at - 6, 2), "little")))
                if flags & 0x10 and kind == 0 and self._is_app_sid(at):
                    dropped = True
                    if not (keep_writes and access & _WRITING_BITS):
                        continue               # inherited from above: not kept
                    # Its writing half, as an entry of its own.
                    raw[4:8] = (_WRITE_ONLY if access & 0x10000000
                                else access & ~_READING_BITS & 0xFFFFFFFF
                                ).to_bytes(4, "little")
                raw[1] = flags & ~0x10 & 0xFF            # no longer INHERITED_ACE
                (denies if kind in (1, 6) else allows).append(bytes(raw))
            if protected and not dropped:
                return False
            body = b"".join(denies + allows)
            acl = (bytes([header[0], 0]) + (8 + len(body)).to_bytes(2, "little")
                   + len(denies + allows).to_bytes(2, "little") + bytes(2) + body)
            new_dacl = ct.create_string_buffer(acl, len(acl))
            # DACL, and PROTECTED: it no longer takes what its parent passes on.
            error = self.SetNamedSecurityInfo(path, 1, 0x4 | 0x80000000, None, None,
                                              new_dacl, None)
            if error:
                raise ct.WinError(error, f"sealing {path}")
            return True
        finally:
            self.LocalFree(descriptor)

    def is_protected(self, path: str) -> bool:
        """Whether `path` takes nothing its parent passes on."""
        ct, w = self.ctypes, self.w
        _dacl, descriptor = self._dacl(path)
        try:
            control, revision = w.WORD(), w.DWORD()
            self.GetSecurityDescriptorControl(descriptor, ct.byref(control),
                                              ct.byref(revision))
            return bool(control.value & 0x1000)
        finally:
            self.LocalFree(descriptor)

    def _write_explicit(self, path: str, header: bytes, entries: List[bytes]) -> None:
        """Write `entries` - every one of its own - as the DACL of `path`,
        and let it inherit again: Windows adds what the parent passes on
        after them, and carries the result down to what is inside."""
        ct = self.ctypes
        denies = [one for one in entries if one[0] in (1, 6)]
        allows = [one for one in entries if one[0] not in (1, 6)]
        body = b"".join(denies + allows)
        acl = (bytes([header[0], 0]) + (8 + len(body)).to_bytes(2, "little")
               + len(denies + allows).to_bytes(2, "little") + bytes(2) + body)
        new_dacl = ct.create_string_buffer(acl, len(acl))
        # DACL, and UNPROTECTED: it takes what its parent passes on again.
        error = self.SetNamedSecurityInfo(path, 1, 0x4 | 0x20000000, None, None,
                                          new_dacl, None)
        if error:
            raise ct.WinError(error, f"unsealing {path}")

    def unseal(self, path: str) -> bool:
        """Undo `seal`: `path` inherits again - the home's read grant
        reaches it once more - and what the seal left behind is taken off:
        the writing halves it kept (`_WRITE_ONLY` entries of an app's SID),
        and the entries of its own that only copy what it inherits now (the
        seal kept what was inherited as entries of its own). Every other
        entry of its own stays as it was. False when it was not sealed -
        nothing is written then."""
        ct = self.ctypes
        dacl, descriptor = self._dacl(path)
        try:
            if not dacl:
                return False
            control, revision = self.w.WORD(), self.w.DWORD()
            self.GetSecurityDescriptorControl(descriptor, ct.byref(control),
                                              ct.byref(revision))
            if not control.value & 0x1000:
                return False                   # not sealed: inherits already
            header = ct.string_at(dacl, 8)
            kept: List[bytes] = []
            for kind, flags, access, at in self._entries(dacl):
                if flags & 0x10:
                    continue                   # none while sealed; Windows adds them
                if kind == 0 and self._is_app_sid(at) and access & _WRITING_BITS \
                        and not access & _READING_BITS:
                    continue                   # the seal's own writing half
                kept.append(ct.string_at(at - 8, int.from_bytes(
                    ct.string_at(at - 6, 2), "little")))
        finally:
            self.LocalFree(descriptor)
        self._write_explicit(path, header, kept)
        # What it inherits now is there: an entry of its own that says the
        # same - type, access, SID, how it passes down - is the seal's copy.
        dacl, descriptor = self._dacl(path)
        try:
            inherited = set()
            own: List[Tuple[bytes, Tuple[int, int, int, bytes]]] = []
            for kind, flags, access, at in self._entries(dacl):
                raw = ct.string_at(at - 8, int.from_bytes(ct.string_at(at - 6, 2), "little"))
                shape = (kind, flags & 0x0F, access, bytes(raw[8:]))
                if flags & 0x10:
                    inherited.add(shape)
                else:
                    own.append((raw, shape))
        finally:
            self.LocalFree(descriptor)
        remaining = [raw for raw, shape in own if shape not in inherited]
        if len(remaining) != len(own):
            self._write_explicit(path, header, remaining)
        return True

    def grant(self, path: str, sid: bytes, mask: int, inherit: bool) -> None:
        """Add one allow entry, as icacls /grant would, keeping the rest of
        the ACL and whether it inherits from its parent. Windows carries an
        inheritable entry down to what is already inside."""
        self._set_entry(path, sid, 1, mask, 0x3 if inherit else 0x0, 1)

    def revoke(self, path: str, sid: bytes, kind: int = 1) -> None:
        """Take every entry of its own naming `sid` off."""
        self._set_entry(path, sid, 4, 0, 0, kind)

    def _set_entry(self, path: str, sid: bytes, mode: int, mask: int,
                   inheritance: int, kind: int) -> None:
        ct, w = self.ctypes, self.w

        class Trustee(ct.Structure):
            _fields_ = [("pMultipleTrustee", ct.c_void_p),
                        ("MultipleTrusteeOperation", ct.c_int),
                        ("TrusteeForm", ct.c_int), ("TrusteeType", ct.c_int),
                        ("ptstrName", ct.c_void_p)]

        class ExplicitAccess(ct.Structure):
            _fields_ = [("grfAccessPermissions", w.DWORD), ("grfAccessMode", ct.c_int),
                        ("grfInheritance", w.DWORD), ("Trustee", Trustee)]

        dacl, descriptor = self._dacl(path, kind)
        buffer = ct.create_string_buffer(sid, len(sid))
        new_dacl = ct.c_void_p()
        try:
            control, revision = w.WORD(), w.DWORD()
            self.GetSecurityDescriptorControl(descriptor, ct.byref(control),
                                              ct.byref(revision))
            # mode: GRANT_ACCESS 1, DENY_ACCESS 3, REVOKE_ACCESS 4. Trustee
            # form 0: a SID.
            entry = ExplicitAccess(mask, mode, inheritance,
                                   Trustee(None, 0, 0, 0, ct.cast(buffer, ct.c_void_p)))
            error = self.SetEntriesInAcl(1, ct.byref(entry), dacl, ct.byref(new_dacl))
            if error:
                raise ct.WinError(error, f"building the ACL of {path}")
            protected = control.value & 0x1000  # SE_DACL_PROTECTED
            info = 0x4 | (0x80000000 if protected else 0x20000000)
            error = self.SetNamedSecurityInfo(path, kind, info, None, None, new_dacl, None)
            if error:
                raise ct.WinError(error, f"writing the ACL of {path}")
        finally:
            if new_dacl:
                self.LocalFree(new_dacl)
            self.LocalFree(descriptor)

    # -- a process in the container ---------------------------------------
    def launch(self, argv: List[str], *, cwd: str, env: Optional[Dict[str, str]],
               capabilities: List[bytes], timeout: Optional[float],
               container: Optional[bytes] = None) -> subprocess.CompletedProcess:
        import _winapi
        import msvcrt

        ct, w = self.ctypes, self.w

        class SidAndAttributes(ct.Structure):
            _fields_ = [("Sid", ct.c_void_p), ("Attributes", w.DWORD)]

        class SecurityCapabilities(ct.Structure):
            _fields_ = [("AppContainerSid", ct.c_void_p),
                        ("Capabilities", ct.POINTER(SidAndAttributes)),
                        ("CapabilityCount", w.DWORD), ("Reserved", w.DWORD)]

        class StartupInfo(ct.Structure):
            _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR),
                        ("lpDesktop", w.LPWSTR), ("lpTitle", w.LPWSTR),
                        ("dwX", w.DWORD), ("dwY", w.DWORD), ("dwXSize", w.DWORD),
                        ("dwYSize", w.DWORD), ("dwXCountChars", w.DWORD),
                        ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD),
                        ("dwFlags", w.DWORD), ("wShowWindow", w.WORD),
                        ("cbReserved2", w.WORD), ("lpReserved2", ct.c_void_p),
                        ("hStdInput", w.HANDLE), ("hStdOutput", w.HANDLE),
                        ("hStdError", w.HANDLE)]

        class StartupInfoEx(ct.Structure):
            _fields_ = [("StartupInfo", StartupInfo), ("lpAttributeList", ct.c_void_p)]

        class ProcessInformation(ct.Structure):
            _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE),
                        ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]

        keep = []                               # buffers that must outlive the call
        container_sid = container or self.container_sid()
        container = ct.create_string_buffer(container_sid, len(container_sid))
        keep.append(container)
        entries = (SidAndAttributes * max(1, len(capabilities)))()
        for index, sid in enumerate(capabilities):
            buffer = ct.create_string_buffer(sid, len(sid))
            keep.append(buffer)
            entries[index].Sid = ct.cast(buffer, ct.c_void_p)
            entries[index].Attributes = 0x4     # SE_GROUP_ENABLED
        security = SecurityCapabilities(
            ct.cast(container, ct.c_void_p),
            entries if capabilities else None, len(capabilities), 0)

        # Pipes: the child's ends inheritable, ours not.
        current = _winapi.GetCurrentProcess()

        def inheritable(handle: int) -> int:
            copy = _winapi.DuplicateHandle(current, handle, current, 0, True,
                                           _winapi.DUPLICATE_SAME_ACCESS)
            _winapi.CloseHandle(handle)
            return copy

        in_read, in_write = _winapi.CreatePipe(None, 0)
        out_read, out_write = _winapi.CreatePipe(None, 0)
        err_read, err_write = _winapi.CreatePipe(None, 0)
        in_read, out_write, err_write = (inheritable(in_read), inheritable(out_write),
                                         inheritable(err_write))
        child_ends = [in_read, out_write, err_write]

        # Two attributes: the container, and the exact handles it inherits -
        # without the list it would inherit every inheritable handle of the
        # agent, an open file among them, and write through it whatever its
        # ACL says.
        size = ct.c_size_t()
        self.InitializeProcThreadAttributeList(None, 2, 0, ct.byref(size))
        attributes = ct.create_string_buffer(size.value)
        if not self.InitializeProcThreadAttributeList(attributes, 2, 0, ct.byref(size)):
            raise ct.WinError(ct.get_last_error())
        handles = (w.HANDLE * 3)(*child_ends)
        job = None
        process = ProcessInformation()
        try:
            if not self.UpdateProcThreadAttribute(
                    attributes, 0, 0x20009,       # PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES
                    ct.byref(security), ct.sizeof(security), None, None):
                raise ct.WinError(ct.get_last_error())
            if not self.UpdateProcThreadAttribute(
                    attributes, 0, 0x20002,       # PROC_THREAD_ATTRIBUTE_HANDLE_LIST
                    handles, ct.sizeof(handles), None, None):
                raise ct.WinError(ct.get_last_error())
            startup = StartupInfoEx()
            startup.StartupInfo.cb = ct.sizeof(StartupInfoEx)
            startup.StartupInfo.dwFlags = 0x100   # STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput = in_read
            startup.StartupInfo.hStdOutput = out_write
            startup.StartupInfo.hStdError = err_write
            startup.lpAttributeList = ct.cast(attributes, ct.c_void_p)

            block = None
            if env is not None:
                text = "".join(f"{k}={v}\0" for k, v in
                               sorted(env.items(), key=lambda kv: kv[0].upper()))
                block = ct.create_unicode_buffer(text + "\0")
            command = ct.create_unicode_buffer(subprocess.list2cmdline(argv))
            flags = (0x00080000                   # EXTENDED_STARTUPINFO_PRESENT
                     | 0x00000004                 # CREATE_SUSPENDED
                     | 0x00000400                 # CREATE_UNICODE_ENVIRONMENT
                     | 0x08000000)                # CREATE_NO_WINDOW
            if not self.CreateProcess(argv[0], command, None, None, True, flags,
                                      block, cwd, ct.byref(startup), ct.byref(process)):
                raise ct.WinError(ct.get_last_error(), f"starting {argv[0]}")
            job = self._job()
            if not self.AssignProcessToJobObject(job, process.hProcess):
                error = ct.get_last_error()
                self.TerminateProcess(process.hProcess, 1)
                raise ct.WinError(error, "putting the child in its job")
            self.ResumeThread(process.hThread)
        except BaseException:
            for handle in (in_write, out_read, err_read):
                _winapi.CloseHandle(handle)
            if job:
                self.CloseHandle(job)
            raise
        finally:
            for handle in child_ends:
                _winapi.CloseHandle(handle)
            self.DeleteProcThreadAttributeList(attributes)
            if process.hThread:
                self.CloseHandle(process.hThread)
        _winapi.CloseHandle(in_write)           # no input: the child reads EOF

        chunks: Dict[str, bytes] = {}

        def drain(name: str, handle: int) -> None:
            with os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY), "rb") as stream:
                chunks[name] = stream.read()

        readers = [threading.Thread(target=drain, args=(n, h), daemon=True)
                   for n, h in (("out", out_read), ("err", err_read))]
        for reader in readers:
            reader.start()
        timed_out = False
        try:
            wait = 0xFFFFFFFF if timeout is None else max(0, int(timeout * 1000))
            if self.WaitForSingleObject(process.hProcess, wait) == 0x102:  # WAIT_TIMEOUT
                timed_out = True
                self.TerminateJobObject(job, 1)
                self.WaitForSingleObject(process.hProcess, 5000)
            code = w.DWORD()
            self.GetExitCodeProcess(process.hProcess, ct.byref(code))
        finally:
            # Closing the job's only handle ends whatever it still holds.
            self.CloseHandle(job)
            self.CloseHandle(process.hProcess)
        for reader in readers:
            reader.join(10)

        def text(name: str) -> str:
            return chunks.get(name, b"").decode("utf-8", "replace").replace("\r\n", "\n")

        if timed_out:
            raise subprocess.TimeoutExpired(argv, timeout or 0, output=text("out"),
                                            stderr=text("err"))
        returncode = code.value if code.value < 0x80000000 else code.value - 0x100000000
        return subprocess.CompletedProcess(argv, returncode, text("out"), text("err"))

    def _job(self) -> Any:
        """A job of its own: kill on close (the agent's end, by any route,
        is the child's end), and no reach into the user's desktop - the
        clipboard, other windows' handles, system settings."""
        ct, w = self.ctypes, self.w

        class Basic(ct.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ct.c_longlong),
                        ("PerJobUserTimeLimit", ct.c_longlong),
                        ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", ct.c_size_t),
                        ("MaximumWorkingSetSize", ct.c_size_t),
                        ("ActiveProcessLimit", w.DWORD), ("Affinity", ct.c_size_t),
                        ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]

        class Extended(ct.Structure):
            _fields_ = [("Basic", Basic), ("IoInfo", ct.c_ulonglong * 6),
                        ("ProcessMemoryLimit", ct.c_size_t),
                        ("JobMemoryLimit", ct.c_size_t),
                        ("PeakProcessMemoryUsed", ct.c_size_t),
                        ("PeakJobMemoryUsed", ct.c_size_t)]

        job = self.CreateJobObject(None, None)
        if not job:
            raise ct.WinError(ct.get_last_error(), "CreateJobObjectW")
        limits = Extended()
        limits.Basic.LimitFlags = 0x2000        # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.SetInformationJobObject(job, 9, ct.byref(limits), ct.sizeof(limits)):
            error = ct.get_last_error()
            self.CloseHandle(job)
            raise ct.WinError(error, "SetInformationJobObject")
        ui = w.DWORD(0xFF)                      # every JOB_OBJECT_UILIMIT_*
        self.SetInformationJobObject(job, 4, ct.byref(ui), ct.sizeof(ui))
        return job


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == LANDLOCK_FLAG:
        return landlock_exec_main(args[1:])
    print(json.dumps(isolation_status()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
