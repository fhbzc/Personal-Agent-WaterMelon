"""In-process guard injected into agent generated Python scripts.

Section 2.3 requires enforcement at the application level *and*, where
technically possible, at the OS level.  This module is the application level
half; the OS level half is runtime/isolation.py, which confines the whole
child from outside (an AppContainer, Seatbelt, bubblewrap or Landlock). Code
inside the child can take this module apart - `_io.FileIO`, the real `open`
in a guarded function's closure, a native extension - which is why it is not
the only half where the system has the other; where it has not, it is all
there is, and the run says so.

It is imported inside the child interpreter before the generated script runs and
wraps every standard entry point so that:

* a write outside ``AGENT_WORKSPACE`` raises instead of succeeding;
* a read *or* write inside ``AGENT_PROTECTED_PATHS`` - the application's trusted
  internal state (sections 108-110) - raises as well;
* a read or write inside ``AGENT_CLOSED_PATHS`` - the project's run logs -
  raises;
* a read inside ``AGENT_CODE_ROOT`` - the application's own code, set for the
  Experience debugger's runs - raises, but in the folders ``AGENT_CODE_OPEN``
  names (the interpreter, when it lives there);
* temporary files land in the workspace runtime directory, not the system one;
* a child process starts only where the operating system confines this one,
  only as Python, and only with this guard loaded into it first - the command
  is rewritten to do so (`child_command`); every other program is refused.

It is intentionally dependency free and standalone: the child process only needs
this single file on ``sys.path`` - and it is loaded from a copy of it outside
the application's code (runtime/sandbox.ensure_guard), since the child may not
read the code.
"""

from __future__ import annotations

import builtins
import io
import os
import sys
import threading
import time

WORKSPACE_ENV = "AGENT_WORKSPACE"
#: Folders the user granted, beside the agent's own: writable too.
GRANTED_ENV = "AGENT_GRANTED_PATHS"
#: Set when the script may open no network connection at all.
NO_NETWORK_ENV = "AGENT_NO_NETWORK"
#: Set when the script may connect to these places only - "host:port",
#: separated by commas. Used for the package installer alone, which must
#: reach the package index and nothing else.
NETWORK_ALLOW_ENV = "AGENT_NETWORK_ALLOW"
#: Folders put first on sys.path before the script runs: the packages
#: installed for this piece of work.
SYS_PATH_ENV = "AGENT_SYS_PATH"
#: Folders, by name, that may be read and never changed wherever they are
#: beneath the folders it may write: the run's saved versions, and - for
#: code, not for pip - the installed packages. Separated by os.pathsep.
READONLY_NAMES_ENV = "AGENT_READONLY_NAMES"
#: Set by runtime/isolation.py: what confines this process from outside.
OS_ISOLATION_ENV = "AGENT_OS_ISOLATION"
#: Set by runtime/isolation.py beside NO_NETWORK_ENV when the operating
#: system gives the run a loopback of its own - a Windows AppContainer per
#: run (processes in one container reach each other on 127.0.0.1, nothing
#: in another container nor on the computer itself), a Linux network
#: namespace of its own: code may then connect to servers its own run
#: starts, on this computer, and nowhere else.
OWN_LOOPBACK_ENV = "AGENT_OWN_LOOPBACK"
#: The testing browser's program (runtime/browser.py), set where a run may
#: use it: the one program besides Python a child may start - and only
#: where the run has a loopback of its own, which is all it can reach.
BROWSER_ENV = "AGENT_BROWSER"
READABLE_ENV = "AGENT_READABLE_ROOT"
READONLY_ENV = "AGENT_READONLY_PATHS"
PROTECTED_ENV = "AGENT_PROTECTED_PATHS"
#: Folders sealed from this script: neither read nor written, nor listed -
#: in the Experience debugger, the other two ways' folders. Separated by
#: os.pathsep.
SEALED_ENV = "AGENT_SEALED_PATHS"
SEALED_REASON = ("that folder is closed to this work: it may not be read")
#: The application's own code, set for a way of the Experience debugger:
#: never READ from here - the user's decision, so a way being measured does
#: not learn how the system that measures it works. Writing is left to the
#: rules above.
CODE_ENV = "AGENT_CODE_ROOT"
#: Inside it, what may still be read: the interpreter, when it lives there
#: (a virtual environment kept in the checkout). Separated by os.pathsep.
CODE_OPEN_ENV = "AGENT_CODE_OPEN"
CODE_REASON = "that is this application's own code: the agents may not read it"
#: The record of what happened - the project's run logs: neither read nor
#: written, nor listed, by code a run runs. Separated by os.pathsep.
CLOSED_ENV = "AGENT_CLOSED_PATHS"
CLOSED_REASON = ("that is the project's run logs - the record of what happened: "
                 "the agents may not read it")
RUNTIME_DIR = ".agent_runtime"

_installed = False
_workspace = None
_readable = None
_readonly: list = []
_protected: list = []
_sealed: list = []
_code_root = None
_code_open: list = []
_closed: list = []
_granted: list = []
_readonly_names: set = set()

_WRITE_MODE_CHARS = ("w", "a", "x", "+")


class SandboxViolation(PermissionError):
    """Raised inside generated scripts when a filesystem boundary is crossed."""


def _normcase(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _resolve(path) -> str:
    text = os.fspath(path)
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    if not os.path.isabs(text):
        text = os.path.join(os.getcwd(), text)
    text = os.path.normpath(text)
    fast = _fast_resolving()
    if fast:
        # One file operation asks five or six checks about the same path,
        # each of which resolved it again.
        said = _last_resolved(text)
        if said is not None:
            return said
    candidate = text
    tail = []
    while candidate and not os.path.exists(candidate):
        parent, name = os.path.split(candidate)
        if parent == candidate:
            break
        tail.append(name)
        candidate = parent
    try:
        base = _real_existing(candidate) if fast else os.path.realpath(candidate)
    except OSError:
        base = candidate
    for name in reversed(tail):
        base = os.path.join(base, name)
    if fast:
        _remember_resolved(text, base)
    return base


# ---------------------------------------------------------------------------
# RESOLVING A PATH QUICKLY INSIDE WINDOWS' APPCONTAINER
#
# There Windows will not give a process the final path of a file
# (GetFinalPathNameByHandle fails), so os.path.realpath walks up the path a
# folder at a time - two failing calls a level, twenty-odd for a file in a
# run's folder: 0.8 ms a resolve where it takes 0.1 ms outside, and every
# file the code opened was resolved five times. A file operation took 45 ms.
#
# So, in an AppContainer only - where the operating system enforces every
# rule here on its own and the guard is a second line - what this process
# cannot change is resolved once: the folders it may write (it cannot
# replace them - their parents are not its to write) and every folder
# outside them. Below its writable folders each level is looked at (one
# lstat) for a link or junction, and one found is resolved the full way.
_FAST_KEPT = 4096
_stable: dict = {}
_writable_real: list = []
_recent = threading.local()


def _fast_resolving() -> bool:
    return os.name == "nt" and _os_backend == "appcontainer"


def _last_resolved(text: str):
    held = getattr(_recent, "held", None)
    if held is not None and held[0] == text and time.monotonic() - held[2] < 0.05:
        return held[1]
    return None


def _remember_resolved(text: str, resolved: str) -> None:
    _recent.held = (text, resolved, time.monotonic())


def _is_link_entry(path: str) -> bool:
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & 0x400)          # FILE_ATTRIBUTE_REPARSE_POINT


def _writable_roots_real() -> list:
    if not _writable_real:
        for root in [_workspace, *_granted]:
            if root:
                try:
                    real = os.path.realpath(root)
                except OSError:
                    continue
                _writable_real.append((os.path.normcase(os.path.abspath(root)),
                                       os.path.normcase(real), real))
    return _writable_real


def _real_existing(path: str) -> str:
    """os.path.realpath of a path that exists, the quick way (above)."""
    key = os.path.normcase(path)
    for given, real_key, real in _writable_roots_real():
        for root_key in (given, real_key):
            if key == root_key or key.startswith(root_key.rstrip(os.sep) + os.sep):
                rest = [one for one in path[len(root_key):].split(os.sep) if one]
                at = real
                for index, name in enumerate(rest):
                    at = os.path.join(at, name)
                    if _is_link_entry(at):
                        return os.path.realpath(os.path.join(at, *rest[index + 1:]))
                return at
    parent, name = os.path.split(path)
    if not name:
        return os.path.realpath(path)
    real_parent = _stable.get(os.path.normcase(parent))
    if real_parent is None:
        real_parent = os.path.realpath(parent)
        if len(_stable) >= _FAST_KEPT:
            _stable.clear()
        _stable[os.path.normcase(parent)] = real_parent
    whole = os.path.join(real_parent, name)
    if _is_link_entry(whole):
        return os.path.realpath(whole)
    return whole


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _inside_workspace(path) -> bool:
    if _workspace is None:
        return True
    try:
        resolved = _normcase(_resolve(path))
    except (TypeError, ValueError):
        return False
    return _within(resolved, _normcase(_workspace)) or _is_granted(path)


def _is_granted(path) -> bool:
    """Inside a folder the user granted this work."""
    if not _granted:
        return False
    try:
        resolved = _normcase(_resolve(path))
    except (TypeError, ValueError):
        return False
    return any(_within(resolved, _normcase(root)) for root in _granted)


def _is_readonly(path) -> bool:
    """Inside the workspace, readable, never writable (section 32, amended)."""
    if not _readonly:
        return False
    try:
        resolved = _normcase(_resolve(path))
    except (TypeError, ValueError):
        return True
    return any(_within(resolved, _normcase(root)) for root in _readonly)


def _is_protected(path) -> bool:
    if not _protected:
        return False
    try:
        resolved = _normcase(_resolve(path))
    except (TypeError, ValueError):
        return True
    return any(_within(resolved, _normcase(root)) for root in _protected)


def _is_sealed(path) -> bool:
    if not _sealed:
        return False
    try:
        resolved = _normcase(_resolve(path))
    except (TypeError, ValueError):
        return True
    return any(_within(resolved, _normcase(root)) for root in _sealed)


def _is_app_code(path) -> bool:
    """Inside the application's own code, and not in a folder of it that
    stays open to this script (CODE_OPEN_ENV)."""
    if not _code_root:
        return False
    try:
        resolved = _normcase(_resolve(path))
    except (TypeError, ValueError):
        return True
    if not _within(resolved, _normcase(_code_root)):
        return False
    return not any(_within(resolved, _normcase(one)) for one in _code_open)


def _is_closed(path) -> bool:
    if not _closed:
        return False
    try:
        resolved = _normcase(_resolve(path))
    except (TypeError, ValueError):
        return True
    return any(_within(resolved, _normcase(root)) for root in _closed)


# ---------------------------------------------------------------------------
# A LISTING LEAVES OUT WHAT MAY NOT BE READ
#
# A folder the code may not read - the project's run logs, a sealed or
# closed folder, the application's state - was listed with the rest, and the
# first os.walk of the work's folder stepped into it and was refused: a
# warning in the log each time, for a script that only wanted the work's
# files. Listed, such a folder is not there; asked for by name, it is still
# refused, saying why.
def _hidden_roots() -> set:
    return {_normcase(root) for root in (*_protected, *_sealed, *_closed) if root}


def _listing(func, name):
    def guarded(path=".", *args, **kwargs):
        _check_read(path, name)
        listed = func(path, *args, **kwargs)
        hidden = _hidden_roots()
        if not hidden or isinstance(path, int):
            return listed
        try:
            raw = os.fsdecode(path) if path is not None else "."
            base = _resolve(raw)
        except (TypeError, ValueError):
            return listed

        def shown(entry_name) -> bool:
            return _normcase(os.path.join(base, os.fsdecode(entry_name))) not in hidden

        if name.endswith("listdir"):
            return [one for one in listed if shown(one)]
        return _FilteredScandir(listed, shown)
    return guarded


class _FilteredScandir:
    """os.scandir's iterator, without the entries `shown` leaves out - and,
    as it, a context manager."""

    def __init__(self, inner, shown):
        self._inner, self._shown = inner, shown

    def __iter__(self):
        return self

    def __next__(self):
        while True:
            entry = next(self._inner)
            if self._shown(entry.name):
                return entry

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        close = getattr(self._inner, "close", None)
        if close is not None:
            close()


def _deny(path, operation: str, reason: str):
    raise SandboxViolation(f"{operation}: {reason} ({path!r})")


def _in_readonly_folder(path) -> bool:
    """Beneath a folder called one of the read-only names (see
    READONLY_NAMES_ENV) - the folder itself included."""
    if not _readonly_names:
        return False
    try:
        parts = _normcase(_resolve(path)).split(os.sep)
    except (TypeError, ValueError):
        return True
    return any(os.path.normcase(name) in parts for name in _readonly_names)


#: THE NULL DEVICE, under each of its spellings: writing to it throws the
#: output away and reading it gives nothing, so it is outside no boundary.
#: pytest opens it as it starts (os.devnull, which Windows opens as the
#: device path dot-nul) and was refused as a file outside the agent's
#: directory - a work run could not run its own tests.
_NULL_DEVICES = {"nul", "nul:", "\\\\.\\nul", "//./nul", "/dev/null",
                 os.devnull.lower()}


def _is_null_device(path) -> bool:
    try:
        named = os.fspath(path)
    except TypeError:
        return False
    if isinstance(named, bytes):
        named = named.decode("utf-8", "replace")
    return isinstance(named, str) and named.strip().lower() in _NULL_DEVICES


def _check_write(path, operation: str) -> None:
    if _is_null_device(path):
        return
    if _is_protected(path):
        _deny(path, operation, "the application's trusted internal state is not writable")
    if _is_sealed(path):
        _deny(path, operation, SEALED_REASON)
    if _is_closed(path):
        _deny(path, operation, CLOSED_REASON)
    if _in_readonly_folder(path):
        _deny(path, operation, "the run's saved versions and installed packages are "
                               "kept by the application; code cannot change them")
    if _is_granted(path):
        # The user chose this folder for the work: it outranks the
        # read-only layers.
        return
    if _is_readonly(path):
        _deny(path, operation, "the user's own material is read only to the agent; "
                               f"write a changed copy in {_workspace!r} instead")
    if not _inside_workspace(path):
        # _workspace is the agent's own directory here, not the whole
        # workspace: the wider workspace is readable, and the message says so
        # rather than leaving a script author to guess the shape of the rule -
        # and says where to write instead.
        _deny(path, operation,
              f"outside the agent's own directory {_workspace!r}; write in that "
              f"folder instead - the workspace {_readable!r} can be read but not "
              "written")


def _check_read(path, operation: str) -> None:
    if _is_null_device(path):
        return
    if _is_protected(path):
        _deny(path, operation, "the application's trusted internal state is not readable")
    if _is_sealed(path):
        _deny(path, operation, SEALED_REASON)
    if _is_app_code(path):
        _deny(path, operation, CODE_REASON)
    if _is_closed(path):
        _deny(path, operation, CLOSED_REASON)


def _is_write_mode(mode: str) -> bool:
    return any(char in mode for char in _WRITE_MODE_CHARS)


# ---------------------------------------------------------------------------
# Process creation and native code (section 31, as amended - and amended
# again: Python children, with this guard loaded into them)
#
# Patching the filesystem surface confines a script only as far as the script
# stays on that surface. A script that starts another process leaves it
# entirely: the child is an ordinary process with an unpatched standard
# library, so `subprocess.run([sys.executable, ...])`, `os.system` and
# `os.startfile` each wrote anywhere the user can before any of this was
# added. Loading a native library is the same escape without the second
# process - `ctypes` calls `CreateFileW` directly and never touches `open`.
#
# But agents test their work the way everyone does - `subprocess.run(
# [sys.executable, "-m", "pytest"])`, a server started with `Popen(
# [sys.executable, "-m", "app"])` - and every such call was refused: forty
# times across the three ways of one Experience debugger session. So a child
# may start when two things hold, and not otherwise:
#
#   1. THE OPERATING SYSTEM CONFINES IT. Where runtime/isolation.py put this
#      process in an AppContainer, Seatbelt, bubblewrap or Landlock, all it
#      starts is in the same confinement - Windows starts a container
#      process's child in that container with the same capabilities, Seatbelt
#      and Landlock are inherited across fork and exec, and bubblewrap's
#      namespaces hold everything started in them - so a child can write,
#      read and reach no more than its parent. It ends with the run too: on
#      Windows the run's job (kill on close) holds every process in it, and
#      elsewhere the watcher kills the whole process group when the run's
#      first process ends (common/children.py), which is why no child may
#      leave that group here. Where there is no such confinement
#      (OS_ISOLATION_ENV "none", or not set) no child starts at all, as
#      before: the guard is all there is then, and a child would be out of it.
#      Nor in a process that may reach the network (no NO_NETWORK_ENV): the
#      package installer and the chat agent's own code start none, as before -
#      the decision was made for work runs, which reach none.
#   2. IT IS PYTHON, AND THIS GUARD IS LOADED INTO IT FIRST. The command is
#      rewritten (`child_command`): whatever Python it named, it is this
#      interpreter; the arguments become `-I -c <bootstrap> <mode> <safe>
#      <target> <args...>`, and the bootstrap imports this very file from the
#      folder this process loaded it from, installs it, and only then runs the
#      module, script or code that was asked for (`run_child`). The child's
#      environment carries this process's policy - where it may write, what
#      is sealed, closed or the application's code, no network, the packages'
#      folders - as this process was given it, whatever the caller's `env=`
#      said (`child_environment`). Any other program - a shell, git, node,
#      PowerShell - is refused: the system would confine it, but none of the
#      rules that only the guard keeps (a network filtered by host name, the
#      folders Landlock cannot hide) would hold in it.
#
# The rewrite sits at the lowest choke point - `_winapi.CreateProcess` on
# Windows, `_posixsubprocess.fork_exec` elsewhere - so subprocess, asyncio and
# multiprocessing's spawn all pass through it, and a reloaded subprocess finds
# it there still. Everything else that starts a process - os.system,
# os.popen, os.startfile, the spawn and exec families, fork - is refused
# outright, and so is the native loader.
#
# The real CreateProcess is held in a closure here, and code in this process
# can pull it out, as it can the real `open`: that is why condition 1 is a
# condition. Without the system's confinement nothing here holds the real one.
#
# Every refusal says what to do instead, on one line: it is read by the agent
# that tried, and it goes into the application's log (sandbox.guard_refusals).
# ---------------------------------------------------------------------------
_NO_CHILD_REASON = (
    "no child process can start in this run - this computer gives it no OS "
    "isolation, so a child would run outside every rule here; do the work in "
    "this process instead: import the module, or run tests with "
    "pytest.main([...]) rather than python -m pytest"
)
_PYTHON_ONLY = (
    "only Python child processes may be started, with the command as a list - "
    "e.g. subprocess.run([sys.executable, '-m', 'pytest', ...]); use the Python "
    "equivalent of the program"
)
_SPAWN_REASON = ("it starts a shell or a process the guard cannot be loaded into; "
                 + _PYTHON_ONLY)
_FORK_REASON = (
    "forking is refused; for multiprocessing call "
    "multiprocessing.set_start_method('spawn') first, or use threads, or "
    "subprocess.run([sys.executable, ...])"
)
_SHELL_REASON = (
    "shell=True starts a shell, which is another program; pass the command as a "
    "list instead, e.g. subprocess.run([sys.executable, '-m', 'pytest'])"
)
_GROUP_REASON = (
    "a child stays in the run's process group, so that it ends with the run; "
    "leave out start_new_session, process_group and preexec_fn"
)
_NATIVE_REASON = (
    "loading a native library would leave the sandbox: it can call the "
    "operating system directly, without passing through any guarded function"
)

#: The backends that confine every process this one starts (see
#: runtime/isolation.py); OS_ISOLATION_ENV names one of them, or "none".
_ISOLATING_BACKENDS = ("appcontainer", "seatbelt", "bubblewrap", "landlock")

#: Every variable that carries the policy to a child. A child is given this
#: process's values of them and no others - not what `env=` said.
_POLICY_NAMES = (WORKSPACE_ENV, GRANTED_ENV, NO_NETWORK_ENV, NETWORK_ALLOW_ENV,
                 SYS_PATH_ENV, READONLY_NAMES_ENV, OS_ISOLATION_ENV, READABLE_ENV,
                 READONLY_ENV, PROTECTED_ENV, SEALED_ENV, CODE_ENV, CODE_OPEN_ENV,
                 CLOSED_ENV, OWN_LOOPBACK_ENV, BROWSER_ENV)

#: What a child runs first (`child_command`): this file, from the folder this
#: process loaded it from, compiled in memory alone and taken off the path
#: again - as runtime/sandbox's bootstrap loads it into the run's first
#: process - installed; and then `run_child` runs what was asked for.
CHILD_BOOTSTRAP = (
    "import sys; "
    "sys.path.insert(0, {home!r}); "
    "_b = sys.dont_write_bytecode; sys.dont_write_bytecode = True; "
    "import guard; "
    "sys.dont_write_bytecode = _b; del _b; "
    "sys.path.remove({home!r}); "
    "guard.install(); "
    "guard.run_child()"
)

#: Interpreter options passed on to a child as they are: none of them changes
#: where the interpreter finds its library or what it runs.
_PASSED_FLAGS = frozenset("BuOqvbR")
#: Options every child is given anyway: -I is -E, -s and -P together.
_SUBSUMED_FLAGS = frozenset("IEsP")
#: -X options passed on; any other is refused (pycache_prefix, say, would
#: have the child write its compiled files somewhere of its choosing).
_PASSED_X = frozenset(("utf8", "dev", "faulthandler", "importtime",
                       "warn_default_encoding", "tracemalloc", "no_debug_ranges",
                       "int_max_str_digits", "frozen_modules"))

#: CREATE_BREAKAWAY_FROM_JOB: never passed on - the run's job is what ends
#: the child with the run.
_BREAKAWAY = 0x01000000

# Taken as install() runs, before any of the script's own code.
_os_backend = ""
_child_policy: dict = {}
_interpreter = ""
_interpreters: set = set()
_guard_home = ""
_offline = False
_browser_path = ""


def _children_allowed() -> bool:
    """Condition 1 above - and only in a process that may reach no network
    at all (NO_NETWORK_ENV): the work runs the decision was made for. The
    package installer, which reaches the package index, and code the user's
    own chat agent runs, which reaches anything, start no child, as before.
    A packaged application has no interpreter to rewrite a command for -
    its executable is the application - so none there either."""
    return (_os_backend in _ISOLATING_BACKENDS and _offline
            and not getattr(sys, "frozen", False))


#: The backends that confine a process's files AND its network on their
#: own - Landlock only from its fourth version, which this cannot see from
#: inside the process, so not Landlock.
_NATIVE_BACKENDS = ("appcontainer", "seatbelt", "bubblewrap")


def _native_allowed() -> bool:
    """Native libraries (ctypes) may load: the operating system confines
    this process - its writes, its reads of sealed, closed and secret
    folders, and its network - and it may reach no network at all (not
    the package installer, which reaches the package index, and only this
    guard keeps it there). Never in a packaged application."""
    return (_os_backend in _NATIVE_BACKENDS and _offline
            and not getattr(sys, "frozen", False))


def _no_child_reason() -> str:
    """Why no child may start here, and what to do instead."""
    if _os_backend in _ISOLATING_BACKENDS and not _offline:
        return ("no child process can start in a process that may reach the "
                "network; do the work in this process instead")
    return _NO_CHILD_REASON


def _note_children_policy() -> None:
    """What a child is given: this process's policy as it took effect, its
    interpreter, and the folder this file was loaded from."""
    global _os_backend, _child_policy, _interpreter, _interpreters, _guard_home
    global _offline, _browser_path
    _os_backend = (os.environ.get(OS_ISOLATION_ENV) or "").strip().lower()
    _offline = bool(os.environ.get(NO_NETWORK_ENV))
    # As the process was started, never as its code may set it later.
    _browser_path = ((os.environ.get(BROWSER_ENV) or "").strip()
                     if _network_kind() == "own_loopback" else "")
    policy = {name: os.environ.get(name) or "" for name in _POLICY_NAMES}
    policy[WORKSPACE_ENV] = _workspace or ""
    policy[READABLE_ENV] = _readable or ""
    policy[READONLY_ENV] = os.pathsep.join(_readonly)
    policy[PROTECTED_ENV] = os.pathsep.join(_protected)
    _child_policy = {name: value for name, value in policy.items() if value}
    _interpreter = sys.executable or ""
    found = set()
    for one in (sys.executable, getattr(sys, "_base_executable", None)):
        if not one:
            continue
        found.add(os.path.normcase(os.path.abspath(one)))
        try:
            found.add(os.path.normcase(os.path.realpath(one)))
        except (OSError, ValueError):
            pass
    _interpreters = found
    _guard_home = os.path.dirname(os.path.abspath(__file__))


def _is_python_name(name: str) -> bool:
    """python, python3, python3.12 or py - with .exe or without."""
    low = name.lower()
    if low.endswith(".exe"):
        low = low[:-4]
    if low in ("python", "python3", "py"):
        return True
    return low.startswith("python3.") and low[len("python3."):].isdigit()


def _is_this_python(program: str, cwd=None) -> bool:
    """Whether `program` names this interpreter. A bare name (python,
    python3, py) is never looked up - PATH, or the folder the child starts
    in, could hold any program of that name; this interpreter is run in its
    place. A path must be this interpreter, or the base installation's, by
    its real path."""
    if not program:
        return False
    if os.path.basename(program) == program and "/" not in program:
        return _is_python_name(program)
    path = program if os.path.isabs(program) else os.path.join(cwd or os.getcwd(), program)
    candidates = [path]
    if os.name == "nt" and not os.path.splitext(path)[1]:
        candidates.append(path + ".exe")      # as CreateProcess would find it
    for one in candidates:
        try:
            if os.path.normcase(os.path.realpath(one)) in _interpreters \
                    or os.path.normcase(os.path.abspath(one)) in _interpreters:
                return True
        except (OSError, ValueError):
            continue
    return False


def _browser_program() -> str:
    """The testing browser, where this run may start it: named by the
    application (BROWSER_ENV) and only with a loopback of the run's own -
    both as this process was started (`_note_children_policy`)."""
    return _browser_path


def _the_browser(program: str, cwd=None) -> str:
    """The browser's own path when `program` is it, by its real path; ""."""
    wanted = _browser_program()
    if not wanted or not program:
        return ""
    path = program if os.path.isabs(program) else os.path.join(cwd or os.getcwd(), program)
    try:
        same = os.path.normcase(os.path.realpath(path)) == \
            os.path.normcase(os.path.realpath(wanted))
    except (OSError, ValueError):
        return ""
    return wanted if same and os.path.isfile(wanted) else ""


def child_command(program, args, cwd=None) -> list:
    """The command a child is started with: this interpreter, `-I`, the
    options passed on, and the bootstrap that loads this guard and then runs
    the module, script or code of `args` with the rest of them as its argv.

    `python -m mod args`, `python script.py args` and `python -c code args`
    are rewritten; whatever cannot be rewritten safely - another program, an
    interactive interpreter, a script on standard input, an option that is
    not known here - raises SandboxViolation saying why and what instead."""
    if not _children_allowed():
        raise SandboxViolation(f"process: {_no_child_reason()}")
    program = os.fsdecode(program) if isinstance(program, bytes) else str(program or "")
    args = [os.fsdecode(one) if isinstance(one, bytes) else str(one) for one in args]
    if not _is_this_python(program, cwd):
        browser = _the_browser(program, cwd)
        if browser:
            return [browser, *args]
        raise SandboxViolation(f"process {program!r}: {_PYTHON_ONLY}"
                               + ("; for a browser, use agent_browser.Browser()"
                                  if _browser_program() else ""))

    def refuse(why: str):
        raise SandboxViolation(f"process {program!r}: {why}; run a script, -m module "
                               "or -c code, with the command as a list")

    launcher = os.path.splitext(os.path.basename(program))[0].lower() == "py"
    flags: list = []
    safe = False
    mode = target = None
    ended = False
    index = 0
    while index < len(args) and mode is None:
        arg = args[index]
        if arg == "--":
            index += 1
            ended = True
            break
        if arg == "--version":
            mode, target = "c", "import sys; print('Python ' + sys.version.split()[0])"
            index = len(args)
            break
        if arg == "-" or not arg.startswith("-"):
            break
        if arg.startswith("--"):
            refuse(f"the option {arg} is not supported in a child here")
        if launcher and index == 0 and arg[1:2].isdigit():
            index += 1                        # py -3, py -3.12: this interpreter
            continue
        at = 1
        while at < len(arg):
            letter = arg[at]
            if letter in "cmWX":
                value = arg[at + 1:]
                if not value:
                    index += 1
                    if index >= len(args):
                        refuse(f"-{letter} needs a value")
                    value = args[index]
                if letter == "c":
                    mode, target = "c", value
                elif letter == "m":
                    mode, target = "m", value
                elif letter == "W":
                    flags += ["-W", value]
                elif value.split("=", 1)[0] in _PASSED_X:
                    flags += ["-X", value]
                else:
                    refuse(f"-X {value} is not supported in a child here")
                break
            if letter == "V":
                mode, target = "c", "import sys; print('Python ' + sys.version.split()[0])"
                index = len(args) - 1
                break
            if letter in _SUBSUMED_FLAGS:
                safe = safe or letter in "IP"
            elif letter in _PASSED_FLAGS:
                flags.append("-" + letter)
            elif letter == "i":
                refuse("an interactive interpreter (-i) would wait for input that "
                       "never comes")
            else:
                refuse(f"the option -{letter} is not supported in a child here")
            at += 1
        index += 1
    rest = args[index:]
    if mode is None:
        if not rest:
            refuse("with no script, module or -c it would wait for input that never "
                   "comes")
        if rest[0] == "-":
            refuse("a script read from standard input is not supported")
        if rest[0].startswith("-") and not ended:
            refuse(f"the option {rest[0]} is not supported in a child here")
        mode, target, rest = "s", rest[0], rest[1:]
    bootstrap = CHILD_BOOTSTRAP.format(home=_guard_home)
    # UTF-8 throughout, as the run's own process (runtime/sandbox): -I
    # ignores PYTHONIOENCODING, and a child's output, piped, was written in
    # the old Windows code page otherwise.
    return [_interpreter, "-I", "-X", "utf8", *flags, "-c", bootstrap, mode, "1" if safe else "0",
            target, *rest]


def _policy_key(name: str) -> bool:
    key = name.upper() if os.name == "nt" else name
    # __PYVENV_LAUNCHER__ names the virtual environment the interpreter acts
    # as - its pyvenv.cfg, and the library that names: never the caller's.
    return key in _POLICY_NAMES or key == "__PYVENV_LAUNCHER__"


def child_environment(given=None) -> dict:
    """The child's environment: what the caller gave (this process's own
    when it gave none), with the policy variables taken out and this
    process's put in - so a child cannot be told it may write elsewhere."""
    source = os.environ if given is None else given
    env = {}
    for key, value in dict(source).items():
        key = os.fsdecode(key) if isinstance(key, bytes) else str(key)
        if _policy_key(key):
            continue
        env[key] = os.fsdecode(value) if isinstance(value, bytes) else str(value)
    env.update(_child_policy)
    return env


def split_windows_command_line(line: str) -> list:
    """A Windows command line split as the C runtime splits it for a
    program's argv - the inverse of subprocess.list2cmdline. The program's
    name, first, by the simpler rule Windows reads it with. Only to read
    what was asked: the child is started with the list rebuilt from this,
    so it sees exactly what was checked here."""
    line = line or ""
    out: list = []
    size = len(line)
    at = 0
    while at < size and line[at] in " \t":
        at += 1
    if at < size and line[at] == '"':
        end = line.find('"', at + 1)
        end = size if end < 0 else end
        out.append(line[at + 1:end])
        at = end + 1
    else:
        end = at
        while end < size and line[end] not in " \t":
            end += 1
        if end > at:
            out.append(line[at:end])
        at = end
    while True:
        while at < size and line[at] in " \t":
            at += 1
        if at >= size:
            return out
        piece: list = []
        quoted = False
        while at < size:
            char = line[at]
            if char == "\\":
                end = at
                while end < size and line[end] == "\\":
                    end += 1
                count = end - at
                if end < size and line[end] == '"':
                    piece.append("\\" * (count // 2))
                    if count % 2:
                        piece.append('"')
                        at = end + 1
                    else:
                        at = end
                else:
                    piece.append("\\" * count)
                    at = end
            elif char == '"':
                if quoted and at + 1 < size and line[at + 1] == '"':
                    piece.append('"')
                    at += 2
                else:
                    quoted = not quoted
                    at += 1
            elif char in " \t" and not quoted:
                break
            else:
                piece.append(char)
                at += 1
        out.append("".join(piece))


def run_child() -> None:
    """In a child, after the bootstrap installed this guard: run what the
    command asked for, in a fresh __main__, with the argv and the first
    entry of sys.path the interpreter itself would have given it - the
    current folder for -c and -m, the script's own folder for a script,
    none when the caller asked for -I or -P."""
    import runpy
    import types

    mode, safe, target, *rest = sys.argv[1:]
    main = types.ModuleType("__main__")
    main.__dict__["__builtins__"] = builtins
    sys.modules["__main__"] = main
    if mode == "c":
        sys.argv = ["-c", *rest]
        if safe != "1":
            sys.path.insert(0, "")
        exec(compile(target, "<string>", "exec"), main.__dict__)
    elif mode == "m":
        sys.argv = ["-m", *rest]
        if safe != "1":
            sys.path.insert(0, os.getcwd())
        runpy._run_module_as_main(target)
    else:
        _check_read(target, "python")
        sys.argv = [target, *rest]
        if safe != "1":
            sys.path.insert(0, os.path.dirname(os.path.abspath(target)))
        runpy.run_path(target, run_name="__main__")


def _windows_create_process(real, list2cmdline):
    """`_winapi.CreateProcess`, every command rewritten (`child_command`)."""
    def CreateProcess(application_name, command_line, process_attributes,
                      thread_attributes, inherit_handles, creation_flags,
                      env_mapping, current_directory, startup_info):
        argv = split_windows_command_line(command_line or "")
        program = application_name or (argv[0] if argv else "")
        command = child_command(program, argv[1:], current_directory)
        return real(command[0], list2cmdline(command), process_attributes,
                    thread_attributes, inherit_handles,
                    int(creation_flags or 0) & ~_BREAKAWAY,
                    child_environment(env_mapping), current_directory, startup_info)
    return CreateProcess


def _posix_fork_exec(real):
    """`_posixsubprocess.fork_exec`, every command rewritten, and kept in
    the run's process group. Its first arguments have kept their places
    since 3.8: args, executable_list, close_fds, pass_fds, cwd, env, six
    pipe ends, the error pipe's two, restore_signals, call_setsid, then
    (3.11 on) the process group; preexec_fn's place moved."""
    version = sys.version_info[:2]
    preexec_at = 21 if version >= (3, 11) else 20 if version >= (3, 9) else 16

    def fork_exec(*args):
        args = list(args)
        if len(args) > preexec_at and args[preexec_at] is not None:
            raise SandboxViolation(f"subprocess (preexec_fn): {_GROUP_REASON}")
        argv = [os.fsdecode(one) for one in args[0]]
        found = [os.fsdecode(one) for one in (args[1] or ())]
        first = argv[0] if argv else ""
        # A bare name is looked up along PATH: each candidate ends in it.
        if found and not (os.sep not in first and os.path.basename(found[0]) == first):
            program = found[0]
        else:
            program = first
        cwd = os.fsdecode(args[4]) if args[4] else None
        command = child_command(program, argv[1:], cwd)
        given = None
        if args[5] is not None:
            given = {}
            for entry in args[5]:
                key, _, value = os.fsdecode(entry).partition("=")
                given[key] = value
        args[0] = command
        args[1] = (os.fsencode(command[0]),)
        args[5] = [os.fsencode(f"{key}={value}")
                   for key, value in child_environment(given).items()]
        if len(args) > 15:
            args[15] = False                  # call_setsid
        if version >= (3, 11) and len(args) > 16:
            args[16] = -1                     # pgid_to_set: none
        return real(*args)
    return fork_exec


def _guard_popen(subprocess) -> None:
    """Refusals at the friendly name, with the hint, for what the choke
    point would refuse or quietly undo: a shell, and on POSIX a child
    leaving the run's process group."""
    popen = subprocess.Popen
    real_init = popen.__init__
    if getattr(real_init, "_agent_guarded", False):
        return

    def __init__(self, args, *more, **kwargs):
        # Popen(args, bufsize, executable, stdin, stdout, stderr,
        #       preexec_fn, close_fds, shell, ...)
        if kwargs.get("shell", more[7] if len(more) > 7 else False):
            raise SandboxViolation(f"subprocess (shell=True): {_SHELL_REASON}")
        if os.name != "nt" and (
                kwargs.get("start_new_session")
                or kwargs.get("process_group") not in (None, -1)
                or kwargs.get("preexec_fn", more[5] if len(more) > 5 else None)
                is not None):
            raise SandboxViolation(f"subprocess: {_GROUP_REASON}")
        real_init(self, args, *more, **kwargs)
        _STARTED.append(self)

    __init__._agent_guarded = True
    popen.__init__ = __init__
    import atexit
    atexit.register(_say_what_is_left_running)


#: Every program the code started, and what is said, as the code ends, of
#: each still running: it is ended with the code (the sandbox's job), and
#: the tool that ran the code tells the agent so (agent/work/tools.py).
_STARTED: list = []
LEFT_RUNNING_MARK = "[sandbox] still running when the code ended: "


def _say_what_is_left_running() -> None:
    for one in _STARTED[:50]:
        try:
            if one.poll() is not None:
                continue
            command = one.args if isinstance(one.args, str) else " ".join(
                str(part) for part in one.args)
            sys.__stderr__.write(LEFT_RUNNING_MARK + command[:300] + "\n")
            sys.__stderr__.flush()
        except Exception:
            continue


def _refusing_popen(subprocess, reason: str) -> None:
    """Where no child may start, Popen STAYS A CLASS - one that refuses to
    be made. A function in its place broke every module that builds on it
    as it is imported: asyncio's Windows event loop subclasses it, and with
    it went uvicorn, httpx and FastAPI's test client, though none of them
    starts a child."""
    real = subprocess.Popen
    if getattr(real, "_agent_refusing", False):
        return

    class Popen(real):
        _agent_refusing = True

        def __init__(self, *args, **kwargs):
            raise SandboxViolation(f"subprocess.Popen: {reason}")

        def __del__(self):           # never made: nothing to clean up
            pass

    Popen.__module__ = real.__module__
    try:
        subprocess.Popen = Popen
    except (AttributeError, TypeError):  # pragma: no cover - refuses assignment
        pass


def _blocked(label: str, reason: str):
    def refuse(*args, **kwargs):
        raise SandboxViolation(f"{label}: {reason}")
    return refuse


def _block_attributes(module, names, reason) -> None:
    for name in names:
        if getattr(module, name, None) is None:
            continue
        try:
            setattr(module, name, _blocked(f"{module.__name__}.{name}", reason))
        except (AttributeError, TypeError):  # a module that refuses assignment
            pass


def _install_process_and_native_blocks() -> None:
    allowed = _children_allowed()
    # The choke points every spawn goes through, whatever calls it: rewritten
    # where children may start, refused where none may.
    try:
        import _winapi
    except Exception:
        _winapi = None
    if _winapi is not None and getattr(_winapi, "CreateProcess", None) is not None:
        if allowed:
            import subprocess
            _winapi.CreateProcess = _windows_create_process(_winapi.CreateProcess,
                                                            subprocess.list2cmdline)
        else:
            _block_attributes(_winapi, ("CreateProcess",), _no_child_reason())
    try:
        import _posixsubprocess
    except Exception:
        _posixsubprocess = None
    if _posixsubprocess is not None and getattr(_posixsubprocess, "fork_exec", None):
        if allowed:
            _posixsubprocess.fork_exec = _posix_fork_exec(_posixsubprocess.fork_exec)
        else:
            _block_attributes(_posixsubprocess, ("fork_exec",), _no_child_reason())

    # os.system and friends live in the builtin nt/posix module; patching there
    # survives a reload of os, which would otherwise copy them back.
    spawn_reason = _SPAWN_REASON if allowed else _no_child_reason()
    fork_reason = _FORK_REASON if allowed else _no_child_reason()
    import os as _os
    for module_name in ("nt", "posix", "os"):
        try:
            module = _os if module_name == "os" else __import__(module_name)
        except Exception:
            continue
        _block_attributes(module, _SPAWNING_NAMES, spawn_reason)
        _block_attributes(module, _FORKING_NAMES, fork_reason)
        # A child that left the run's process group would outlive it.
        _block_attributes(module, _SESSION_NAMES, _GROUP_REASON)

    try:
        import subprocess
        if allowed:
            # subprocess took its own reference to fork_exec as it was
            # imported, and would use posix_spawn (refused above) when it can.
            if getattr(subprocess, "_fork_exec", None) is not None and \
                    _posixsubprocess is not None:
                subprocess._fork_exec = _posixsubprocess.fork_exec
            if hasattr(subprocess, "_USE_POSIX_SPAWN"):
                subprocess._USE_POSIX_SPAWN = False
            _guard_popen(subprocess)
        else:
            _block_attributes(
                subprocess,
                ("run", "call", "check_call", "check_output",
                 "getoutput", "getstatusoutput"),
                _no_child_reason(),
            )
            _refusing_popen(subprocess, _no_child_reason())
    except Exception:
        pass

    # NATIVE CODE, where the operating system confines the process on its
    # own: what ctypes could reach around this guard - a write outside the
    # run's folders, a connection, a sealed or closed folder, the user's
    # sign-ins - the system refuses there anyway, so it is let load; uvicorn,
    # click and colorama load it as they are imported, and the agents went
    # round them. Elsewhere it is the way out, and stays shut.
    if _native_allowed():
        return
    # Native code: block the loader itself, so ctypes cannot be reloaded past it.
    try:
        import _ctypes
        _block_attributes(_ctypes, ("LoadLibrary", "dlopen"), _NATIVE_REASON)
    except Exception:
        pass
    try:
        import ctypes
        _block_attributes(
            ctypes, ("CDLL", "WinDLL", "OleDLL", "PyDLL", "LibraryLoader"),
            _NATIVE_REASON,
        )
    except Exception:
        # On Windows ctypes loads kernel32 as it is imported, so with the
        # loader blocked it cannot be imported at all. Say so the way
        # libraries expect - an ImportError - so one that can do without it
        # (pip's lookup of the user's folders, for one) does, instead of
        # failing on an AttributeError it never planned for.
        if "ctypes" not in sys.modules:
            sys.meta_path.insert(0, _NoCtypes())


class _NoCtypes:
    """Refuses ``import ctypes`` with the reason, as an ImportError."""

    @staticmethod
    def find_spec(name, path=None, target=None):
        if name == "ctypes" or name.startswith("ctypes."):
            raise ImportError(f"{name}: {_NATIVE_REASON}", name=name)
        return None


_SPAWNING_NAMES = (
    "system", "popen", "startfile",
    "posix_spawn", "posix_spawnp",
    "execl", "execle", "execlp", "execlpe",
    "execv", "execve", "execvp", "execvpe",
    "spawnl", "spawnle", "spawnlp", "spawnlpe",
    "spawnv", "spawnve", "spawnvp", "spawnvpe",
)
_FORKING_NAMES = ("fork", "forkpty")
_SESSION_NAMES = ("setsid", "setpgid", "setpgrp")


def _no_network() -> None:
    """No connection out of this process: a work run reads the web through
    its fetch tool, which only reads and carries none of the user's
    sign-ins - code it writes cannot post, upload or sign in anywhere."""
    import socket

    def refused(*_args, **_kwargs):
        raise SandboxViolation("network: code run for a piece of work may not "
                               "open connections, not even to this computer "
                               "(localhost); test a web app in process - Flask's "
                               "app.test_client(), FastAPI's TestClient, or call the "
                               "WSGI/ASGI app directly; read pages with the fetch tool")

    socket.socket.connect = refused
    socket.socket.connect_ex = refused
    socket.socket.sendto = refused
    socket.create_connection = refused
    socket.getaddrinfo = refused


#: The backends that give a run a loopback of its own (OWN_LOOPBACK_ENV).
_OWN_LOOPBACK_BACKENDS = ("appcontainer", "bubblewrap")
_LOOPBACK_NAMES = ("localhost", "localhost.localdomain", "ip6-localhost")


def _is_loopback(host) -> bool:
    """127.0.0.0/8, ::1 (also as ::ffff:127.x), or the name localhost."""
    import ipaddress
    text = host.decode("ascii", "replace") if isinstance(host, bytes) else str(host or "")
    text = text.lower().rstrip(".").split("%")[0]
    if text in _LOOPBACK_NAMES or text.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return (mapped or address).is_loopback


def _own_loopback_only() -> None:
    """Connections to this computer's loopback alone - which the operating
    system has made the run's own (OWN_LOOPBACK_ENV): its code may test a
    server it starts itself, as a browser or a client would, and reaches
    neither another run's servers, nor the application, nor anything else
    on this computer or beyond. Here a name other than localhost is not
    looked up, an address that is not loopback is refused, and so is a
    socket that is not an internet one (a Unix socket is a door to another
    program); the operating system keeps the rest."""
    import socket

    def refuse(what: str):
        raise SandboxViolation(
            f"network: {what} - code run for a piece of work reaches only servers "
            "its own run starts, on this computer (127.0.0.1 / localhost); read "
            "pages with the fetch tool")

    def check(sock, address) -> None:
        if sock.family not in (socket.AF_INET, socket.AF_INET6) \
                or not isinstance(address, tuple) or len(address) < 2:
            refuse(f"{address!r} is not a loopback address")
        if not _is_loopback(address[0]):
            refuse(f"{address[0]} is not this computer's loopback")

    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_sendto = socket.socket.sendto
    real_sendmsg = getattr(socket.socket, "sendmsg", None)

    def guarded_getaddrinfo(host, port, *args, **kwargs):
        # No host is a bind to every interface, an address is no lookup:
        # both are answered here; connecting is checked at connect.
        if host is not None and host != b"" and host != "":
            text = host.decode("ascii", "replace") if isinstance(host, bytes) else str(host)
            literal = True
            try:
                import ipaddress
                ipaddress.ip_address(text.split("%")[0])
            except ValueError:
                literal = False
            if not literal and not _is_loopback(text):
                refuse(f"{text} is not looked up")
        return real_getaddrinfo(host, port, *args, **kwargs)

    def guarded_connect(self, address):
        check(self, address)
        return real_connect(self, address)

    def guarded_connect_ex(self, address):
        check(self, address)
        return real_connect_ex(self, address)

    def guarded_sendto(self, data, *args):
        check(self, args[-1] if args else None)
        return real_sendto(self, data, *args)

    def guarded_sendmsg(self, buffers, ancdata=(), flags=0, address=None):
        if address is not None:
            check(self, address)
            return real_sendmsg(self, buffers, ancdata, flags, address)
        return real_sendmsg(self, buffers, ancdata, flags)

    socket.getaddrinfo = guarded_getaddrinfo
    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.socket.sendto = guarded_sendto
    if real_sendmsg is not None:
        socket.socket.sendmsg = guarded_sendmsg


# ---------------------------------------------------------------------------
# A list of places (the package installer)
#
# Installing a package needs the package index and nothing else. The list is
# checked at the two points every connection passes: the lookup of a name,
# and the connect to an address. Checking names only would let an address be
# typed in directly; checking addresses only would mean trusting whatever a
# name resolved to before - so an address is allowed only if an allowed
# lookup in this process returned it.
# ---------------------------------------------------------------------------
def _allowed_places(text: str) -> dict:
    """``"pypi.org:443,files.pythonhosted.org:443"`` as {host: {ports}}."""
    places: dict = {}
    for entry in (text or "").split(","):
        host, _, port = entry.strip().rpartition(":")
        if host and port.isdigit():
            places.setdefault(host.lower().rstrip("."), set()).add(int(port))
    return places


def _only_network(places: dict) -> None:
    """Connections to the named hosts and ports alone.

    A name is checked when it is looked up, and a connection may then go
    only to an address that an allowed lookup returned, on an allowed port -
    so an address typed in directly, another host, another port, or a proxy
    in between are all refused. Datagrams are refused: the lookups
    themselves are made by the operating system, not by this process."""
    import ipaddress
    import socket

    real_getaddrinfo = socket.getaddrinfo
    reached: dict = {}            # address -> the ports it was looked up for

    def refuse(what: str):
        raise SandboxViolation(
            f"network: {what} - only {', '.join(sorted(places))} can be "
            "reached from here")

    def port_of(port) -> int:
        if isinstance(port, bytes):
            port = port.decode("ascii", "replace")
        if isinstance(port, str) and not port.isdigit():
            try:
                return socket.getservbyname(port, "tcp")
            except OSError:
                return -1
        try:
            return int(port)
        except (TypeError, ValueError):
            return -1

    def guarded_getaddrinfo(host, port, *args, **kwargs):
        name = host.decode("ascii", "replace") if isinstance(host, bytes) else str(host or "")
        name = name.lower().rstrip(".")
        try:
            ipaddress.ip_address(name.split("%")[0])
            refuse(f"an address given directly ({name}) is not allowed")
        except ValueError:
            pass
        wanted = port_of(port)
        if name not in places:
            refuse(f"{name or '(no host)'} is not an allowed host")
        if wanted not in places[name]:
            refuse(f"port {port} of {name} is not allowed")
        found = real_getaddrinfo(host, port, *args, **kwargs)
        for _family, _kind, _proto, _name, address in found:
            if isinstance(address, tuple) and address:
                reached.setdefault(str(address[0]).split("%")[0].lower(),
                                   set()).add(wanted)
        return found

    def check(address) -> None:
        if not isinstance(address, tuple) or len(address) < 2:
            refuse(f"{address!r} is not a network address that is allowed")
        ip = str(address[0]).split("%")[0].lower()
        if ip not in reached:
            refuse(f"{address[0]} was not found by looking up an allowed host")
        if port_of(address[1]) not in reached[ip]:
            refuse(f"port {address[1]} of {address[0]} is not allowed")

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded_connect(self, address):
        check(address)
        return real_connect(self, address)

    def guarded_connect_ex(self, address):
        check(address)
        return real_connect_ex(self, address)

    def no_datagrams(*_args, **_kwargs):
        refuse("datagrams are not allowed")

    socket.getaddrinfo = guarded_getaddrinfo
    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.socket.sendto = no_datagrams
    if hasattr(socket.socket, "sendmsg"):
        socket.socket.sendmsg = no_datagrams


def _network_kind() -> str:
    """"none", "own_loopback", "places" or "" (anything): no network
    outranks a list of places - both set is none at all - and the run's own
    loopback is opened only where the operating system makes it the run's."""
    if os.environ.get(NO_NETWORK_ENV):
        backend = (os.environ.get(OS_ISOLATION_ENV) or "").strip().lower()
        if os.environ.get(OWN_LOOPBACK_ENV) and backend in _OWN_LOOPBACK_BACKENDS:
            return "own_loopback"
        return "none"
    return "places" if os.environ.get(NETWORK_ALLOW_ENV) else ""


def _apply_network() -> None:
    kind = _network_kind()
    if kind == "none":
        _no_network()
    elif kind == "own_loopback":
        _own_loopback_only()
    elif kind == "places":
        _only_network(_allowed_places(os.environ[NETWORK_ALLOW_ENV]))


def install(workspace: str | None = None, protected: list | None = None,
            readonly: list | None = None, readable: str | None = None) -> None:
    """Patch the filesystem surface of the standard library. Idempotent."""
    global _installed, _workspace, _readable, _readonly, _protected, _granted, _sealed
    global _readonly_names, _code_root, _code_open, _closed
    if _installed:
        return
    _readonly_names = {name for name in (os.environ.get(READONLY_NAMES_ENV) or "")
                       .split(os.pathsep) if name}
    _granted = [_resolve(entry) for entry in
                (os.environ.get(GRANTED_ENV) or "").split(os.pathsep) if entry]
    _apply_network()
    # Packages installed for the work come first, before anything of the
    # interpreter's own; they are still run under every rule here.
    for entry in reversed([one for one in (os.environ.get(SYS_PATH_ENV) or "")
                           .split(os.pathsep) if one]):
        sys.path.insert(0, entry)
    _workspace = workspace or os.environ.get(WORKSPACE_ENV)
    _readable = readable or os.environ.get(READABLE_ENV) or _workspace
    raw_readonly = readonly if readonly is not None else [
        entry for entry in (os.environ.get(READONLY_ENV) or "").split(os.pathsep) if entry
    ]
    _readonly = [_resolve(entry) for entry in raw_readonly]
    raw_protected = protected if protected is not None else [
        entry for entry in (os.environ.get(PROTECTED_ENV) or "").split(os.pathsep) if entry
    ]
    _protected = [_resolve(entry) for entry in raw_protected]
    _sealed = [_resolve(entry) for entry in
               (os.environ.get(SEALED_ENV) or "").split(os.pathsep) if entry]
    _code_root = _resolve(os.environ[CODE_ENV]) if os.environ.get(CODE_ENV) else None
    _code_open = [_resolve(entry) for entry in
                  (os.environ.get(CODE_OPEN_ENV) or "").split(os.pathsep) if entry]
    _closed = [_resolve(entry) for entry in
               (os.environ.get(CLOSED_ENV) or "").split(os.pathsep) if entry]
    if not _workspace:
        return
    # Roots are resolved as the paths checked against them are - a short
    # (8.3) name or a link in a root would otherwise refuse its own files.
    _workspace = _resolve(_workspace)

    real_open = builtins.open
    real_os_open = os.open

    # THE NULL DEVICE WHERE THE SYSTEM WILL NOT OPEN IT: a process in an
    # AppContainer on some Windows (Windows Server, as GitHub's runners) is
    # refused "nul" itself, so subprocess.DEVNULL and pytest's start failed
    # with "Permission denied: 'nul'". There, a file of the run's own stands
    # in for it: what is written goes nowhere that matters, a read is empty.
    null_stand_in = os.path.join(_workspace, RUNTIME_DIR, "null-device")

    def _null_stand_in_flags(flags):
        written = flags & (getattr(os, "O_WRONLY", 0) | getattr(os, "O_RDWR", 0))
        return flags | getattr(os, "O_CREAT", 0) | (getattr(os, "O_TRUNC", 0) if written else 0)

    def guarded_open(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            if _is_write_mode(str(mode)):
                _check_write(file, "open")
            else:
                _check_read(file, "open")
        try:
            return real_open(file, mode, *args, **kwargs)
        except PermissionError:
            if not (isinstance(file, (str, bytes, os.PathLike)) and _is_null_device(file)):
                raise
            os.makedirs(os.path.dirname(null_stand_in), exist_ok=True)
            stand_in_mode = mode if _is_write_mode(str(mode)) else str(mode).replace("r", "a+", 1)
            return real_open(null_stand_in, stand_in_mode, *args, **kwargs)

    builtins.open = guarded_open
    io.open = guarded_open

    def guarded_os_open(path, flags, *args, **kwargs):
        write_flags = getattr(os, "O_WRONLY", 0) | getattr(os, "O_RDWR", 0) | getattr(os, "O_CREAT", 0)
        if flags & write_flags:
            _check_write(path, "os.open")
        else:
            _check_read(path, "os.open")
        try:
            return real_os_open(path, flags, *args, **kwargs)
        except PermissionError:
            if not _is_null_device(path):
                raise
            os.makedirs(os.path.dirname(null_stand_in), exist_ok=True)
            return real_os_open(null_stand_in, _null_stand_in_flags(flags), 0o600)

    os.open = guarded_os_open

    def wrap_single(func, name, check=_check_write):
        def guarded(path, *args, **kwargs):
            check(path, name)
            return func(path, *args, **kwargs)

        return guarded

    def wrap_pair(func, name):
        def guarded(src, dst, *args, **kwargs):
            _check_write(dst, name)
            _check_read(src, name)
            return func(src, dst, *args, **kwargs)

        return guarded

    # Python 3.12.4+ gives os.mkdir(path, 0o700) on Windows an ACL naming the
    # user, SYSTEM and the administrators alone, which a process in an
    # AppContainer is refused - and tempfile.mkdtemp, which asks for 0o700,
    # then tries ten thousand names before it gives up (pip hung on it).
    # Inside the container its folders are the container's whatever the
    # mode: the plain one is used.
    if os.name == "nt" and os.environ.get(OS_ISOLATION_ENV) == "appcontainer":
        plain_mkdir = os.mkdir

        def mkdir_in_container(path, mode=0o777, *args, **kwargs):
            return plain_mkdir(path, 0o777 if mode == 0o700 else mode, *args, **kwargs)

        os.mkdir = mkdir_in_container

    for name in ("remove", "unlink", "rmdir", "mkdir", "makedirs", "truncate", "chmod"):
        original = getattr(os, name, None)
        if original is not None:
            setattr(os, name, wrap_single(original, f"os.{name}"))

    for name in ("listdir", "scandir"):
        original = getattr(os, name, None)
        if original is not None:
            setattr(os, name, _listing(original, f"os.{name}"))

    for name in ("rename", "replace", "link", "symlink"):
        original = getattr(os, name, None)
        if original is not None:
            setattr(os, name, wrap_pair(original, f"os.{name}"))

    try:
        import shutil

        for name in ("copy", "copy2", "copyfile", "move", "copytree"):
            original = getattr(shutil, name, None)
            if original is not None:
                setattr(shutil, name, wrap_pair(original, f"shutil.{name}"))
        rmtree = getattr(shutil, "rmtree", None)
        if rmtree is not None:
            shutil.rmtree = wrap_single(rmtree, "shutil.rmtree")
    except Exception:  # pragma: no cover - shutil is always importable
        pass

    # Temporary files belong in the workspace runtime area (section 111).
    try:
        import tempfile

        tmp_dir = os.path.join(_workspace, RUNTIME_DIR, "temp")
        os.makedirs(tmp_dir, exist_ok=True)
        tempfile.tempdir = tmp_dir
    except Exception:  # pragma: no cover
        pass

    # What a child of this process is given - taken now, before any of the
    # script's own code could change the environment it is taken from.
    _note_children_policy()
    _install_process_and_native_blocks()

    _installed = True


def run_script(script: str, argv: list | None = None) -> None:
    """Install the guard, then execute *script* as ``__main__``."""
    install()
    import runpy

    sys.argv = [script, *(argv or [])]
    runpy.run_path(script, run_name="__main__")


def main() -> int:
    """``python guard.py <script.py> [args...]`` - install, then run the script."""
    if len(sys.argv) < 2:
        print("usage: guard.py <script.py> [args...]", file=sys.stderr)
        return 2
    run_script(sys.argv[1], sys.argv[2:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
