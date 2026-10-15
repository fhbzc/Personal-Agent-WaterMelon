"""Single running instance (section 110.2).

One agent runs at a time. A second one is refused, and told where the first
one is.

Two instances are not merely redundant, they are destructive: they share one
SQLite database, one scheduler - so every task fires twice - one Listener
capturing the same activity twice, and one credential store. The guard is
therefore taken before any of that is opened, not after.

Two things are held:

* a lock on ``internal/agent.lock`` inside the root, which is what actually
  protects that root's state and which names the process holding it;
* a system-wide named object, so a second instance is refused even when it was
  pointed at a different root.

Neither writes anything outside the root: the system-wide object is a kernel
object on Windows and an abstract socket address on Linux, and elsewhere the
root lock alone applies.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

LOCK_FILENAME = "agent.lock"
GLOBAL_NAME = "personalized-agent-single-instance"
#: Waives only the machine-wide guard, for a harness validating a build against
#: a throwaway root while the user's own agent is running. The root lock is not
#: waivable: two agents on one root is the case that destroys state.
ALLOW_SECOND_INSTANCE_ENV = "PERSONALIZED_AGENT_ALLOW_SECOND_INSTANCE"
# The byte that is locked. It sits past any content the file will ever hold, so
# the running instance's own description stays readable to whoever is refused -
# a lock taken over byte zero would hide the very details the message needs.
LOCK_BYTE_OFFSET = 1 << 20


class AlreadyRunning(RuntimeError):
    """Raised when another instance holds the guard."""

    def __init__(self, message: str, holder: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.holder = holder or {}


def _process_alive(pid: int) -> bool:
    """Whether a process id still refers to something running."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:                        # exists, owned by someone else
        return True
    return True


class SingleInstance:
    """The guard itself. Acquire before opening any state; release on exit."""

    def __init__(self, root: Path, *, system_wide: bool = True) -> None:
        self.root = Path(root)
        self.lock_path = self.root / "internal" / LOCK_FILENAME
        self._system_wide = system_wide
        self._handle: Optional[Any] = None
        self._mutex: Optional[Any] = None
        self._socket: Optional[socket.socket] = None
        self.held = False

    # -- reporting ----------------------------------------------------------
    def holder(self) -> Dict[str, Any]:
        """Whatever the running instance recorded about itself."""
        try:
            data = json.loads(self.lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def describe_conflict(self) -> str:
        """Name the instance that is running, as precisely as we can.

        The running instance describes itself in the lock file of the root it
        holds. When the conflict comes from the system-wide guard instead, that
        instance may be using a different root, and this one's lock file says
        nothing about it - so do not claim a root we cannot see.
        """
        holder = self.holder()
        parts = ["Personalized Agent is already running"]
        pid = holder.get("pid")
        if pid:
            started = holder.get("started_at")
            parts.append(f"(process {pid}"
                         + (f", started {started}" if started else "") + ")")
        where = holder.get("root")
        if where:
            parts.append(f"using {where}.")
        else:
            parts.append("on this machine, possibly using a different agent root.")
        parts.append("Close the running agent before starting another one.")
        return " ".join(parts)

    # -- acquisition --------------------------------------------------------
    def acquire(self) -> "SingleInstance":
        if self.held:
            return self
        self._acquire_system_wide()
        try:
            self._acquire_root_lock()
        except Exception:
            self._release_system_wide()
            raise
        self.held = True
        return self

    def _acquire_system_wide(self) -> None:
        if not self._system_wide:
            return
        if os.environ.get(ALLOW_SECOND_INSTANCE_ENV, "").strip().lower() in (
                "1", "true", "yes", "on"):
            log.warning("the machine-wide single-instance guard is waived by %s; "
                        "the root lock still applies", ALLOW_SECOND_INSTANCE_ENV)
            return
        if os.name == "nt":
            import ctypes

            ERROR_ALREADY_EXISTS = 183
            # A kernel object, not a file: nothing is written outside the root,
            # and the object disappears when the holding process does.
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            ctypes.set_last_error(0)
            handle = kernel32.CreateMutexW(None, True, f"Local\\{GLOBAL_NAME}")
            last = ctypes.get_last_error()
            if not handle:
                # The guard could not be created. Refusing to start over an
                # unavailable guard would be worse than the risk it covers, and
                # the root lock still protects this root's state.
                log.warning("could not create the single-instance guard (error %s)", last)
                return
            if last == ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(handle)
                raise AlreadyRunning(self.describe_conflict(), self.holder())
            self._mutex = handle
        if sys.platform.startswith("linux"):
            # The abstract namespace disappears with the process; no file.
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.bind("\0" + GLOBAL_NAME)
            except OSError:
                sock.close()
                raise AlreadyRunning(self.describe_conflict(), self.holder())
            self._socket = sock

    def _acquire_root_lock(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.lock_path, "a+", encoding="utf-8")
        try:
            self._lock_file(handle)
        except OSError:
            handle.close()
            raise AlreadyRunning(self.describe_conflict(), self.holder())
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({
            "pid": os.getpid(),
            "root": str(self.root),
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "executable": sys.executable,
        }))
        handle.flush()
        self._handle = handle

    @staticmethod
    def _lock_file(handle) -> None:
        """An advisory lock the operating system drops if we are killed."""
        if os.name == "nt":
            import msvcrt

            handle.seek(LOCK_BYTE_OFFSET)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            handle.seek(0)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    # -- release ------------------------------------------------------------
    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(LOCK_BYTE_OFFSET)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:                        # pragma: no cover - already gone
                log.debug("could not unlock %s", self.lock_path, exc_info=True)
            finally:
                handle.close()
            try:
                self.lock_path.unlink()
            except OSError:                        # pragma: no cover - raced
                pass
        self._release_system_wide()
        self.held = False

    def _release_system_wide(self) -> None:
        mutex, self._mutex = self._mutex, None
        if mutex is not None:
            import ctypes

            ctypes.windll.kernel32.ReleaseMutex(mutex)
            ctypes.windll.kernel32.CloseHandle(mutex)
        sock, self._socket = self._socket, None
        if sock is not None:
            sock.close()

    def __enter__(self) -> "SingleInstance":
        return self.acquire()

    def __exit__(self, *_exc: Any) -> None:
        self.release()


def stale_lock_holder(root: Path) -> Optional[Dict[str, Any]]:
    """A lock left behind by a process that is no longer running."""
    guard = SingleInstance(root)
    holder = guard.holder()
    pid = holder.get("pid")
    if holder and isinstance(pid, int) and not _process_alive(pid):
        return holder
    return None
