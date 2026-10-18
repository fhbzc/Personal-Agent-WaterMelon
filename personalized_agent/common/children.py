"""Child processes that die with the agent.

A process the agent starts - a Claude CLI call, a Codex call, a script a
task run executes - is a separate process, and on every desktop system a
child outlives its parent by default. The agent's own timeouts are kept by
the agent, so a child started just before the agent quit, crashed or was
ended in Task Manager would run on with nothing left to stop it: a model
call to the end, and a script stuck in a loop for good.

So every child is tied to the agent's life by the operating system, not by
the agent remembering to clean up - which a crash never does:

  - WINDOWS: every child is put in one job object made with "kill on job
    close". The job's only handle is the agent's; when the agent's process
    ends, by any route, Windows closes it and ends every process in the
    job, grandchildren included.

  - LINUX AND MACOS: there is no such job. Each long-lived child is started
    through a small watcher, which puts itself and the child in a process
    group of their own and checks every half second whether the agent is
    still its parent; when it is not, it kills the whole group. On Linux
    the kernel also tells the watcher at once (PR_SET_PDEATHSIG), so it
    does not wait for the next check.

Short system calls - a notification, a look at the active window - run
every second or two on Linux and macOS and end within seconds; they are
not given a watcher, which would cost a Python start each time. On Windows
they join the job too, which costs nothing.

BELOW NORMAL PRIORITY: every child starts a step below the user's own
programs (on Windows its priority class, which what it starts inherits;
elsewhere the watcher's niceness, which its child inherits). It is not a
limit: with the processors free, a child has all of them; only while the
user's programs want them do they go first.

LEFT FROM BEFORE (`leftovers`, `end_leftovers`): should one ever be left -
the job could not be made, or the watcher was killed with the agent - the
processes carrying the marks of this application's own folders are found
when it starts again, and ended.
"""

from __future__ import annotations

import ctypes
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

#: The flag the packaged application is started with to act as a watcher.
WATCH_FLAG = "--watch-child"

_WINDOWS = sys.platform.startswith("win")
#: Windows' priority classes, as creation flags: below normal is a child's.
BELOW_NORMAL = 0x00004000
_PRIORITY_CLASSES = (0x00000040 | BELOW_NORMAL | 0x00000020 | 0x00008000
                     | 0x00000080 | 0x00000100)
#: How much lower than the agent a watched child runs, elsewhere.
NICER_BY = 5
_job = None
_job_lock = threading.Lock()


# -- Windows: one job, killed with the agent ------------------------------------
def _windows_job() -> Optional[int]:
    """The agent's job object, made once: closing it ends every child."""
    global _job
    with _job_lock:
        if _job is not None:
            return _job or None
        try:
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p,
                                                  wintypes.LPCWSTR]
            kernel32.SetInformationJobObject.argtypes = [
                wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            kernel32.SetInformationJobObject.restype = wintypes.BOOL

            class IoCounters(ctypes.Structure):
                _fields_ = [(name, ctypes.c_ulonglong) for name in (
                    "ReadOperationCount", "WriteOperationCount",
                    "OtherOperationCount", "ReadTransferCount",
                    "WriteTransferCount", "OtherTransferCount")]

            class Basic(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                            ("PerJobUserTimeLimit", ctypes.c_longlong),
                            ("LimitFlags", wintypes.DWORD),
                            ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t),
                            ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t),
                            ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class Extended(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", Basic),
                            ("IoInfo", IoCounters),
                            ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                raise OSError(ctypes.get_last_error(), "CreateJobObjectW")
            info = Extended()
            # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            info.BasicLimitInformation.LimitFlags = 0x2000
            # 9: JobObjectExtendedLimitInformation
            if not kernel32.SetInformationJobObject(
                    handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise OSError(ctypes.get_last_error(), "SetInformationJobObject")
            _job = handle
        except Exception:                  # pragma: no cover - reported once
            log.warning("could not make a job object; children will not be "
                        "ended with the agent", exc_info=True)
            _job = 0
        return _job or None


def _into_the_job(process: subprocess.Popen) -> bool:
    job = _windows_job()
    if not job:
        return False
    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE,
                                                      wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        if kernel32.AssignProcessToJobObject(job, int(process._handle)):
            return True
        log.debug("could not put pid %s in the job (%s)", process.pid,
                  ctypes.get_last_error())
    except Exception:                      # pragma: no cover - best effort
        log.debug("could not put a child in the job", exc_info=True)
    return False


# -- Linux and macOS: a watcher per child -------------------------------------------
#: Given to the watcher first: when the child ends, whatever it started ends
#: too (see `watch_main`).
WHOLE_TREE_FLAG = "--whole-tree"


def _watched(command: Sequence[str], whole_tree: bool = False) -> List[str]:
    """The command, run under a watcher that ends it when the agent ends."""
    me = str(os.getpid())
    flags = [WHOLE_TREE_FLAG] if whole_tree else []
    if getattr(sys, "frozen", False):
        return [sys.executable, WATCH_FLAG, *flags, me, *command]
    # THIS FILE, RUN AS IT IS - it needs nothing but the standard library -
    # rather than imported as part of the package: a sandboxed child is
    # given a bare environment and its own working directory, where the
    # package cannot be found.
    return [sys.executable, "-I", os.path.abspath(__file__), *flags, me, *command]


def watch_main(argv: Sequence[str]) -> int:
    """Run a command; end it, and everything it started, if the agent goes.

    WITH WHOLE_TREE_FLAG FIRST, also when the command itself ends: code a
    sandboxed run executes may start Python children of its own (a server,
    with Popen), and none may outlive the run - on Windows the run's job
    ends them all as its first process ends (runtime/isolation.py), and
    here this does. The child is put in a process group of its own, which
    the guard inside keeps every process it starts in (no new session, no
    other group), and that group is killed when the child ends, or when the
    watcher is told to end (SIGTERM - see `end_tree`) or the agent goes."""
    argv = list(argv)
    whole = bool(argv) and argv[0] == WHOLE_TREE_FLAG
    if whole:
        argv = argv[1:]
    if len(argv) < 2:
        return 2
    agent = int(argv[0])
    command = list(argv[1:])
    try:
        os.setsid()                        # a group of its own, to kill whole
    except OSError:
        pass
    if sys.platform.startswith("linux"):
        try:                                # told at once when the agent dies
            libc = ctypes.CDLL(None, use_errno=True)
            libc.prctl(1, int(signal.SIGTERM))   # PR_SET_PDEATHSIG
        except Exception:
            pass
    held: dict = {}                        # "child": the child's group, once started

    def end_child_group() -> None:
        if "child" in held:
            try:
                os.killpg(held["child"], signal.SIGKILL)
            except OSError:
                pass

    def end(*_args: Any) -> None:
        if whole and "child" not in held:
            # The child is being started: ended as soon as it is.
            held["pending"] = True
            return
        try:
            end_child_group()
            os.killpg(os.getpgrp(), signal.SIGKILL)
        finally:
            os._exit(137)

    signal.signal(signal.SIGTERM, end)
    if os.getppid() != agent:              # gone before we got here
        return 137
    try:                                   # below the user's own programs
        os.nice(NICER_BY)
    except (OSError, AttributeError):
        pass
    if whole:
        if sys.version_info >= (3, 11):
            child = subprocess.Popen(command, process_group=0)
        else:                              # pragma: no cover - older Pythons
            child = subprocess.Popen(command, preexec_fn=os.setpgrp)
        held["child"] = child.pid
        if held.get("pending"):
            end()
    else:
        child = subprocess.Popen(command)
    while True:
        try:
            code = child.wait(timeout=0.5)
            if whole:
                end_child_group()          # what it started ends with it
            return code if code >= 0 else 128 - code
        except subprocess.TimeoutExpired:
            pass
        if os.getppid() != agent:
            end()


# -- left from before ----------------------------------------------------------------
def _said(line: str) -> str:
    """A command line, its paths as they are: a path written inside
    Python's own quotes has its backslashes doubled."""
    return os.path.normcase(line.replace("\\\\", "\\"))


def leftovers(marks: Sequence[str]) -> List[Any]:
    """The processes an earlier run of this application left: a command
    line carrying one of `marks` - this application's own folders, which
    no other program names - not started by this process. psutil
    processes; none when it cannot look."""
    try:
        import psutil
    except Exception:                       # pragma: no cover - required anyway
        return []
    wanted = [os.path.normcase(str(mark)) for mark in marks if str(mark).strip()]
    if not wanted:
        return []
    me = psutil.Process(os.getpid())
    try:
        mine = {me.pid, *(one.pid for one in me.children(recursive=True))}
    except Exception:                       # pragma: no cover - looked at next time
        mine = {me.pid}
    found = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        if process.pid in mine:
            continue
        try:
            line = _said(" ".join(process.info.get("cmdline") or []))
        except Exception:                   # gone, or not ours to read
            continue
        if any(mark in line for mark in wanted):
            found.append(process)
    # Only the top of each: what one of them started ends with it.
    pids = {one.pid for one in found}
    tops = []
    for process in found:
        try:
            if process.ppid() not in pids:
                tops.append(process)
        except Exception:
            continue
    return tops


def end_leftovers(marks: Sequence[str]) -> int:
    """End what an earlier run of this application left (`leftovers`), each
    with everything it started. How many were ended."""
    ended = 0
    for process in leftovers(marks):
        try:
            for one in [*process.children(recursive=True), process]:
                try:
                    one.kill()
                except Exception:
                    pass
            ended += 1
        except Exception:                   # gone already
            continue
    if ended:
        log.info("ended %d process(es) an earlier run of the application left", ended)
    return ended


# -- the two ways in ------------------------------------------------------------------
def popen(command: Sequence[str], short: bool = False, whole_tree: bool = False,
          **kwargs: Any) -> subprocess.Popen:
    """Start a child that ends when the agent does. `whole_tree`: on Linux
    and macOS, what the child starts ends when it ends (see `watch_main`);
    on Windows a sandboxed child's own job does that."""
    command = list(command)
    if _WINDOWS:
        # NO WINDOW OF ITS OWN. A console program (git, say) started from a
        # process with no console - the installed application, or tests run
        # in the background - was given a new console window, one for every
        # call: a reading of commits flashed windows on the taskbar without
        # end. Unless the caller asked for a console or none at all.
        flags = int(kwargs.get("creationflags") or 0)
        own = (getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10)
               | getattr(subprocess, "DETACHED_PROCESS", 0x8))
        if not flags & own:
            flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        if not flags & _PRIORITY_CLASSES:
            # BELOW NORMAL, unless the caller chose (module docstring).
            flags |= BELOW_NORMAL
        kwargs["creationflags"] = flags
        process = subprocess.Popen(command, **kwargs)
        _into_the_job(process)
        return process
    if short:
        return subprocess.Popen(command, **kwargs)
    return subprocess.Popen(_watched(command, whole_tree), **kwargs)


def end_tree(process: subprocess.Popen) -> None:
    """End a child and everything it started - on a timeout, say."""
    try:
        if _WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)],
                           capture_output=True, timeout=15,
                           creationflags=getattr(subprocess,
                                                 "CREATE_NO_WINDOW", 0))
        else:
            try:
                # The watcher's group: told first, so that a watcher of a
                # whole tree ends the child's group as well as its own -
                # killed outright, it could not; then killed, if it has not.
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
            except (OSError, AttributeError):
                process.kill()
    except Exception:                      # pragma: no cover - best effort
        pass
    try:
        process.kill()
    except Exception:
        pass


class Ended(Exception):
    """A watched child ended by its watch (`run(watch=...)`) - why, said."""

    def __init__(self, reason: str, output: str = "", stderr: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.output = output
        self.stderr = stderr


def _input_file(input: Any, kwargs: Dict[str, Any]) -> Tuple[Any, str]:
    """THE INPUT IN A FILE, there before the child starts - opened for it as
    its standard input, and the file's name, to remove once it has ended.

    It was written to a pipe by a thread of this process once the child had
    started. Claude Code waits only three seconds for its input and then
    gives up ("no stdin data received in 3s"): with this process busy - two
    runs, a learning and a heartbeat at once on a loaded computer - the
    thread was not given its turn in time, and calls failed, a run of work
    with them. A file needs no turn: it is all there from the first read.
    The same bytes as the pipe carried: text as the pipe would have written
    it (its line ends too)."""
    import tempfile
    if isinstance(input, (bytes, bytearray)):
        data = bytes(input)
    else:
        text = str(input)
        if kwargs.get("text") or kwargs.get("encoding") or kwargs.get("universal_newlines"):
            text = text.replace("\n", os.linesep)
        data = text.encode(kwargs.get("encoding") or "utf-8", kwargs.get("errors") or "strict")
    handle, name = tempfile.mkstemp(prefix="pa-input-")
    with os.fdopen(handle, "wb") as written:
        written.write(data)
    return open(name, "rb"), name


def _done_with(held: Any, name: str) -> None:
    try:
        held.close()
    except OSError:
        pass
    try:
        os.unlink(name)
    except OSError:                         # pragma: no cover - the temp folder's
        pass


def _run_watched(command: Sequence[str], input: Any, on_line: Any, watch: Any,
                 short: bool, whole_tree: bool,
                 **kwargs: Any) -> subprocess.CompletedProcess:
    """`run`, its output read as it comes - each line of standard output
    handed to `on_line` - and `watch()` asked about twice a second: a
    reason (a string) ends the whole tree then, raising `Ended`. For a
    child that may work a long while and says so as it goes, where a
    fixed time limit would end it while it is still working."""
    kwargs["stdout"] = subprocess.PIPE
    kwargs["stderr"] = subprocess.PIPE
    held, name = (_input_file(input, kwargs) if input is not None else (None, ""))
    kwargs["stdin"] = held if held is not None else subprocess.DEVNULL
    try:
        process = popen(command, short=short, whole_tree=whole_tree, **kwargs)
    except BaseException:
        if held is not None:
            _done_with(held, name)
        raise
    out: list = []
    err: list = []

    def read_out() -> None:
        for line in process.stdout:
            out.append(line)
            if on_line is not None:
                try:
                    on_line(line)
                except Exception:              # pragma: no cover - the reader goes on
                    pass

    def read_err() -> None:
        for line in process.stderr:
            err.append(line)

    threads = [threading.Thread(target=one, daemon=True) for one in (read_out, read_err)]
    for thread in threads:
        thread.start()
    try:
        while process.poll() is None:
            reason = watch() if watch is not None else None
            if reason:
                end_tree(process)
                for thread in threads:
                    thread.join(5)
                raise Ended(str(reason), "".join(out), "".join(err))
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
    except BaseException:
        end_tree(process)
        raise
    finally:
        if held is not None:
            _done_with(held, name)
    for thread in threads:
        thread.join(10)
    return subprocess.CompletedProcess(list(command), process.returncode,
                                       "".join(out), "".join(err))


def run(command: Sequence[str], *, input: Any = None,
        timeout: Optional[float] = None, capture_output: bool = False,
        short: bool = False, whole_tree: bool = False,
        on_line: Any = None, watch: Any = None,
        **kwargs: Any) -> subprocess.CompletedProcess:
    """`subprocess.run`, for a child that ends when the agent does - and
    whose whole tree is ended, not just itself, when it runs out of time
    (and, with `whole_tree`, when it ends - see `popen`).

    `on_line` and `watch`: read as it goes, and ended when `watch` says why
    (`_run_watched`) - text output only."""
    if on_line is not None or watch is not None:
        return _run_watched(command, input, on_line, watch, short, whole_tree, **kwargs)
    if capture_output:
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)
    held, name = None, ""
    if input is not None and "stdin" not in kwargs:
        held, name = _input_file(input, kwargs)
        kwargs["stdin"] = held
    try:
        process = popen(command, short=short, whole_tree=whole_tree, **kwargs)
    except BaseException:
        if held is not None:
            _done_with(held, name)
        raise
    try:
        out, err = process.communicate(None if held is not None else input,
                                       timeout=timeout)
    except subprocess.TimeoutExpired:
        end_tree(process)
        try:
            out, err = process.communicate(timeout=5)
        except Exception:
            out, err = None, None
        raise subprocess.TimeoutExpired(list(command), timeout or 0,
                                        output=out, stderr=err)
    except BaseException:
        end_tree(process)
        raise
    finally:
        if held is not None:
            _done_with(held, name)
    return subprocess.CompletedProcess(list(command), process.returncode,
                                       out, err)


def run_short(command: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """A quick system call - in the job on Windows, without a watcher
    elsewhere, where it ends within its own few seconds."""
    return run(command, short=True, **kwargs)


if __name__ == "__main__":                 # run as the watcher itself
    sys.exit(watch_main(sys.argv[1:]))

