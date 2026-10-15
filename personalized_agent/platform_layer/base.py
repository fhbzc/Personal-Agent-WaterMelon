"""Platform abstraction interfaces (sections 26-28).

Higher level modules depend on these abstractions only.  Every ``if platform ==
"windows"`` style branch lives inside a concrete implementation module.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Sequence
from ..common import children

try:
    import psutil
except Exception:  # pragma: no cover - optional
    psutil = None


# ---------------------------------------------------------------------------
# Capability map (section 28)
# ---------------------------------------------------------------------------
@dataclass
class PlatformCapabilities:
    active_application: bool = False
    active_window_title: bool = False
    filesystem_events: bool = False
    browser_history: bool = False
    process_isolation: bool = False
    native_notifications: bool = False
    process_info: bool = False
    #: Whether the contents of the window in front can be read at all
    #: (section 19.2). Separate from active_window_title, which is about
    #: knowing *which* window: one is a name, the other is everything in it.
    window_text: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "active_application": self.active_application,
            "active_window_title": self.active_window_title,
            "filesystem_events": self.filesystem_events,
            "browser_history": self.browser_history,
            "process_isolation": self.process_isolation,
            "native_notifications": self.native_notifications,
            "process_info": self.process_info,
            "window_text": self.window_text,
        }
        data.update(self.extra)
        return data


@dataclass
class ActiveWindow:
    application: Optional[str] = None
    title: Optional[str] = None
    pid: Optional[int] = None
    executable: Optional[str] = None
    bundle_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "application": self.application,
            "title": self.title,
            "pid": self.pid,
            "executable": self.executable,
            "bundle_id": self.bundle_id,
        }


@dataclass
class ExecutionResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    isolation: str = "none"
    command: Sequence[str] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "isolation": self.isolation,
        }


# ---------------------------------------------------------------------------
# Provider interfaces (section 27)
# ---------------------------------------------------------------------------
class PlatformActivityProvider(ABC):
    @abstractmethod
    def get_active_application(self) -> Optional[str]:
        ...

    @abstractmethod
    def get_active_window(self) -> ActiveWindow:
        ...

    def get_browser_history_paths(self) -> Dict[str, Path]:
        """Map of browser name to local history database path."""
        return {}

    def seconds_since_input(self) -> Optional[float]:
        """How long since the keyboard or the mouse was last touched.

        The one signal that says a **person** did something, as opposed to a
        program doing it on their behalf or on its own. A machine left alone
        goes on writing files - registry hives, caches, sync clients,
        indexers - and none of that is the user's activity, however faithfully
        it is recorded.

        ``None`` where the platform cannot say, which is not the same as zero
        and must not be read as "the user is here".
        """
        return None

    def text_in_front(self) -> Optional[Any]:
        """The text of the window in front, or nothing if it cannot be read.

        Every desktop publishes what its applications are showing so that a
        screen reader can speak it, and the same interface answers a question
        nothing else can: not *that* a window was in front but **what was in
        it** (section 19.2).

        ``None`` where the platform has no such interface, or where this
        build cannot reach it. Reading is expensive enough and personal
        enough that it happens only when asked for.
        """
        return None

    def front_window_image(self) -> Optional[Any]:
        """A picture of the window in front - its own area only, not the
        screen - as a Pillow image, or nothing where it cannot be taken.

        For what text cannot carry: a slide, a figure, a design. Taken only
        when the screenshot provider's rule asks for it.
        """
        return None

    @property
    def window_text_error(self) -> str:
        """Why the window in front cannot be read, when it cannot."""
        return "this platform does not expose window contents to the agent"

    def files_the_user_opened(self, since: float) -> Dict[str, float]:
        """What the desktop itself says the user opened, and when.

        Every desktop keeps this for its own purposes - the Recent menu, the
        jump list, the reopen list - and what makes it worth reading here is
        what never goes in: a program calling ``open()`` does not appear. Only
        a file opened the way a person opens one is written down, which is
        exactly the distinction a filesystem notification cannot make.

        Keys are the most specific identifier the platform gives up: a full
        path where its record holds one, a bare filename where it does not.
        An empty mapping means "this platform does not keep such a record, or
        would not say", and is read as no evidence rather than as a denial.
        """
        return {}


class PlatformNotificationProvider(ABC):
    @abstractmethod
    def send_notification(self, title: str, message: str) -> bool:
        ...


class PlatformProcessProvider(ABC):
    @abstractmethod
    def get_process_info(self, pid: int) -> Dict[str, Any]:
        ...


class PlatformSandboxProvider(ABC):
    @abstractmethod
    def create_restricted_process(
        self,
        command: Sequence[str],
        writable_root: Path,
        readable_paths: Optional[Sequence[Path]] = None,
        environment: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        **boundary: Any,
    ) -> ExecutionResult:
        """`boundary`, where the provider confines the process itself:
        writable_roots, hidden_paths, sealed_paths, code_root, closed_paths,
        network and readonly_names (see runtime/isolation.run_isolated)."""
        ...

    @property
    def isolation_name(self) -> str:
        return "none"


# ---------------------------------------------------------------------------
# Shared default implementations
# ---------------------------------------------------------------------------
class PsutilProcessProvider(PlatformProcessProvider):
    def get_process_info(self, pid: int) -> Dict[str, Any]:
        if psutil is None:
            return {"pid": pid, "available": False}
        try:
            proc = psutil.Process(pid)
            with proc.oneshot():
                return {
                    "pid": pid,
                    "available": True,
                    "name": proc.name(),
                    "exe": _safe(proc.exe),
                    "cmdline": _safe(proc.cmdline),
                    "create_time": _safe(proc.create_time),
                    "username": _safe(proc.username),
                }
        except Exception:
            return {"pid": pid, "available": False}


def _safe(getter):
    try:
        return getter()
    except Exception:
        return None


class SubprocessSandbox(PlatformSandboxProvider):
    """Baseline execution used when no OS isolation mechanism is available.

    Application level path validation still applies (section 32) and the child
    process is given a sanitized environment whose temporary directory points
    inside the workspace, so ``tempfile`` cannot escape it.
    """

    isolation = "subprocess"

    @property
    def isolation_name(self) -> str:
        return self.isolation

    def build_environment(
        self, writable_root: Path, environment: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        # Temporary files belong to the workspace runtime area (section 111).
        tmp = writable_root / ".agent_runtime" / "temp"
        tmp.mkdir(parents=True, exist_ok=True)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(writable_root),
            "USERPROFILE": str(writable_root),
            "TMPDIR": str(tmp),
            "TEMP": str(tmp),
            "TMP": str(tmp),
            "AGENT_WORKSPACE": str(writable_root),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if os.name == "nt":
            for key in ("SYSTEMROOT", "SystemRoot", "COMSPEC", "PATHEXT", "WINDIR"):
                if key in os.environ:
                    env[key] = os.environ[key]
        if environment:
            env.update({str(k): str(v) for k, v in environment.items()})
        return env

    def run(
        self,
        command: Sequence[str],
        writable_root: Path,
        env: Dict[str, str],
        timeout: Optional[float],
        isolation: str,
    ) -> ExecutionResult:
        try:
            completed = children.run(
                list(command),
                cwd=str(writable_root),
                env=env,
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=timeout,
                shell=False,
            )
            return ExecutionResult(
                returncode=completed.returncode,
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
                isolation=isolation,
                command=tuple(command),
            )
        except subprocess.TimeoutExpired as exc:
            return ExecutionResult(
                returncode=-1,
                stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
                stderr="execution timed out",
                timed_out=True,
                isolation=isolation,
                command=tuple(command),
            )
        except FileNotFoundError as exc:
            return ExecutionResult(-1, "", f"command not found: {exc}", False, isolation, tuple(command))

    def create_restricted_process(
        self,
        command: Sequence[str],
        writable_root: Path,
        readable_paths: Optional[Sequence[Path]] = None,
        environment: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        **boundary: Any,
    ) -> ExecutionResult:
        env = self.build_environment(writable_root, environment)
        return self.run(command, writable_root, env, timeout, self.isolation_name)


class IsolatedSandbox(SubprocessSandbox):
    """The operating system's own confinement, on every platform: an
    AppContainer on Windows, Seatbelt on macOS, bubblewrap or Landlock on
    Linux - chosen at run time by runtime/isolation.py, which says why when
    none is there and the process then runs as SubprocessSandbox does, with
    the in-process guard alone. The guard runs inside either way."""

    @property
    def isolation_name(self) -> str:
        from ..runtime.isolation import isolation_status

        status = isolation_status()
        return status["backend"] if status["isolated"] else self.isolation

    def create_restricted_process(
        self,
        command: Sequence[str],
        writable_root: Path,
        readable_paths: Optional[Sequence[Path]] = None,
        environment: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
        **boundary: Any,
    ) -> ExecutionResult:
        from ..runtime import isolation

        env = self.build_environment(writable_root, environment)
        network = boundary.get("network")
        if network is None:                  # a caller that did not say: as told
            network = (isolation.NETWORK_NONE if env.get("AGENT_NO_NETWORK")
                       else isolation.NETWORK_PYPI if env.get("AGENT_NETWORK_ALLOW")
                       else isolation.NETWORK_FULL)
        roots = list(boundary.get("writable_roots") or [writable_root])
        names = boundary.get("readonly_names")
        name = self.isolation_name
        try:
            done = isolation.run_isolated(
                list(command), cwd=writable_root, writable=roots,
                readable=list(readable_paths or []),
                hidden=list(boundary.get("hidden_paths") or []), network=network,
                sealed=list(boundary.get("sealed_paths") or []),
                code=boundary.get("code_root"),
                closed=list(boundary.get("closed_paths") or []),
                readonly_names=(isolation.VERSIONS_NAME, isolation.PACKAGES_NAME)
                if names is None else list(names),
                env=env, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout if isinstance(exc.stdout, str) else ""
            return ExecutionResult(-1, out or "", "execution timed out", True, name,
                                   tuple(command))
        except FileNotFoundError as exc:
            return ExecutionResult(-1, "", f"command not found: {exc}", False, name,
                                   tuple(command))
        except OSError as exc:
            # The confinement itself could not be set up: the child is not
            # run at all rather than run without it.
            return ExecutionResult(-1, "", f"could not start the isolated process: {exc}",
                                   False, name, tuple(command))
        return ExecutionResult(done.returncode, done.stdout or "", done.stderr or "",
                               isolation=name, command=tuple(command))


class Platform:
    """Bundle of the platform specific providers plus a capability map."""

    name = "unknown"

    def __init__(self) -> None:
        self.activity: PlatformActivityProvider
        self.notifications: PlatformNotificationProvider
        self.processes: PlatformProcessProvider = PsutilProcessProvider()
        self.sandbox: PlatformSandboxProvider = SubprocessSandbox()

    def capabilities(self) -> PlatformCapabilities:  # pragma: no cover - overridden
        return PlatformCapabilities()

    def describe(self) -> Dict[str, Any]:
        return {
            "platform": self.name,
            "isolation": self.sandbox.isolation_name,
            "capabilities": self.capabilities().to_dict(),
        }


def which(binary: str) -> Optional[str]:
    return shutil.which(binary)


class NullNotificationProvider(PlatformNotificationProvider):
    def send_notification(self, title: str, message: str) -> bool:
        return False
