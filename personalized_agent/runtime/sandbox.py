"""Runtime and sandbox layer (sections 29-33).

Everything the agent can execute goes through :class:`SandboxRuntime`:
generated Python scripts, scheduler scripts, external commands, subprocesses,
temporary files and downloads.  The rest of the agent never calls
``subprocess`` directly.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..common.observability.tracing import NullTracer
from ..common.paths import is_frozen
from ..platform_layer import Platform, get_platform
from ..platform_layer.base import ExecutionResult
from . import filesystem as fs
from . import isolation
from .guard import (BROWSER_ENV, CLOSED_ENV, CODE_ENV, CODE_OPEN_ENV, GRANTED_ENV,
                    NETWORK_ALLOW_ENV, NO_NETWORK_ENV, PROTECTED_ENV, READABLE_ENV,
                    READONLY_ENV, READONLY_NAMES_ENV, SEALED_ENV, SYS_PATH_ENV,
                    WORKSPACE_ENV)
from .permissions import SecurityViolation, WorkspacePolicy

log = logging.getLogger(__name__)

# Development: a plain interpreter plus the standalone guard module - loaded
# from its copy outside the application's code (ensure_guard), compiled in
# memory alone (no child leaves a compiled guard beside it for the next one
# to load), and its folder off the path again before the script runs.
_GUARD_BOOTSTRAP = (
    "import sys, runpy; "
    "sys.path.insert(0, {guard_dir!r}); "
    "sys.dont_write_bytecode = True; "
    "import guard; "
    "sys.dont_write_bytecode = False; "
    "sys.path.remove({guard_dir!r}); "
    "guard.install(); "
    "sys.argv = [{script!r}] + sys.argv[1:]; "
    "runpy.run_path({script!r}, run_name='__main__')"
)

# Packaged: the bundled runtime re-executes itself in script runner mode
# (section 107 - packaging must not weaken the security model).
SCRIPT_RUNNER_FLAG = "--run-guarded-script"

#: The guard's folder, by its place in the application's protected state.
GUARD_FOLDER = "guard"

_guard_lock = threading.Lock()


def _digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def ensure_guard(folder: Path) -> Optional[Path]:
    """THE GUARD, LOADED FROM OUTSIDE THE APPLICATION'S CODE.

    A child imports the guard before anything else it runs, so it must read
    the guard's file - and a run of the Experience debugger may not read the
    application's code (WorkspacePolicy.code_root). So the guard is copied,
    byte for byte, into a folder of its own - `<internal>/guard`, inside the
    protected state: no run may write there by every rule that already
    keeps that state, and it is the one folder of it a child may read
    (runtime/isolation.guard_homes) - and every child loads it from there.
    On Linux the Landlock launcher - isolation.py, run as a script - goes
    with it, for the same reason; nowhere else, so a child can read no more
    of how it is kept than it must.

    Compared by hash each time, and written again when it differs - a guard
    changed in the code is the one the next child loads, and a copy changed
    by anything else is put back before a child loads it. The folder, or
    None in a packaged application, whose bundle holds the guard."""
    if is_frozen():
        return None
    folder = Path(folder)
    here = Path(__file__).resolve().parent
    wanted = [here / "guard.py"]
    if sys.platform.startswith("linux"):
        wanted.append(here / "isolation.py")
    with _guard_lock:
        _own_folder(folder)
        # Compiled copies are never loaded in place of the source: the
        # source is what is compared here.
        shutil.rmtree(folder / "__pycache__", ignore_errors=True)
        for source in wanted:
            copy = folder / source.name
            if _digest(copy) != _digest(source):
                partial = folder / f".{source.name}.partial"
                shutil.copyfile(source, partial)
                os.replace(partial, copy)
    return isolation.note_guard_home(folder)


def _own_folder(folder: Path) -> None:
    """The folder, made - on POSIX the user's own and no one else's, as the
    shared temporary folder a policy with no protected state falls back to
    could otherwise hold one planted by another user."""
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        return
    info = folder.stat()
    if info.st_uid != os.getuid():
        raise PermissionError(f"{folder} belongs to another user: the guard is "
                              "not loaded from there")
    if info.st_mode & 0o022:
        os.chmod(folder, 0o700)


def guard_home_for(policy: WorkspacePolicy) -> Path:
    """Where a policy's children load the guard from: the protected state's
    guard folder (the application's - common/paths.AppPaths.guard_dir); a
    folder of the user's own under the temporary folder for a policy that
    has no protected state (tests, tools)."""
    protected = list(getattr(policy, "protected_paths", None) or [])
    if protected:
        return Path(protected[0]) / GUARD_FOLDER
    try:
        import getpass
        user = getpass.getuser()
    except Exception:  # pragma: no cover - no user name to be had
        user = "user"
    return Path(tempfile.gettempdir()) / f"personalized-agent-guard-{user}"


def guard_refusals(stderr: str, most: int = 3) -> List[str]:
    """What the in-process guard refused, as it said it - each distinct
    refusal once, in order, at most `most`."""
    found: List[str] = []
    for line in str(stderr or "").splitlines():
        at = line.find("SandboxViolation:")
        if at < 0:
            continue
        text = line[at + len("SandboxViolation:"):].strip()[:300]
        if text and text not in found:
            found.append(text)
        if len(found) >= most:
            break
    return found


@dataclass
class ScriptResult:
    ok: bool
    returncode: int
    stdout: str
    stderr: str
    isolation: str
    script_path: Optional[str] = None
    timed_out: bool = False
    violation: Optional[str] = None
    #: In plain words, whether the operating system confined the script -
    #: "not OS-isolated: <why>" when only the guard inside it did.
    os_isolation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "isolation": self.isolation,
            "os_isolation": self.os_isolation,
            "script_path": self.script_path,
            "timed_out": self.timed_out,
            "violation": self.violation,
        }


class SandboxRuntime:
    def __init__(
        self,
        policy: WorkspacePolicy,
        *,
        platform: Optional[Platform] = None,
        observability: Any = None,
        bus: Any = None,
        sandbox_enabled: bool = True,
        default_timeout: float = 120.0,
        tracer: Any = None,
    ) -> None:
        self.policy = policy
        self.tracer = tracer or NullTracer()
        self.platform = platform or get_platform()
        self.observability = observability
        self.bus = bus
        self.sandbox_enabled = sandbox_enabled
        self.default_timeout = default_timeout

    # -- reporting ----------------------------------------------------------
    def report_violation(self, kind: str, detail: Dict[str, Any]) -> None:
        log.warning("sandbox violation (%s): %s", kind, detail)
        # Section 14.31.9 - a security event is distinguishable from a crash.
        self.tracer.event(
            f"sandbox.violation.{kind}", severity="security", status="blocked",
            event_type="security.violation", connection_id="runtime->sandbox",
            error=detail.get("message"), outputs=detail.get("requested_path"),
            metadata={"kind": kind, **detail},
        )
        if self.observability is not None:
            self.observability.record_violation(kind, detail)
        if self.bus is not None:
            self.bus.publish("runtime.violation", {"kind": kind, **detail})

    @property
    def isolation(self) -> str:
        if not self.sandbox_enabled:
            return "application-only"
        return self.platform.sandbox.isolation_name

    def os_isolation(self) -> str:
        """One line for a run's report: confined by the operating system, or
        "not OS-isolated: <why>" (see runtime/isolation.py)."""
        if not self.sandbox_enabled:
            return "not OS-isolated: the sandbox is turned off in settings"
        return isolation.describe()

    @property
    def network_mode(self) -> str:
        """What the operating system lets the child reach: nothing (work
        runs), HTTPS only (the package installer - the guard inside narrows
        it to the package index), or anything (code the user's own chat
        agent runs, as before)."""
        if not getattr(self.policy, "network", True):
            return isolation.NETWORK_NONE
        if getattr(self.policy, "network_allow", None):
            return isolation.NETWORK_PYPI
        return isolation.NETWORK_FULL

    # -- validated filesystem helpers --------------------------------------
    def write_text(self, path, content: str, append: bool = False) -> Path:
        try:
            return fs.safe_write_text(path, content, self.policy, append=append)
        except SecurityViolation as exc:
            self.report_violation("write_path", exc.to_dict())
            raise

    def read_text(self, path, max_chars: int = 200_000) -> str:
        try:
            return fs.safe_read_text(path, self.policy, max_chars=max_chars)
        except SecurityViolation as exc:
            self.report_violation("read_path", exc.to_dict())
            raise

    def delete(self, path) -> Path:
        try:
            return fs.safe_delete(path, self.policy)
        except SecurityViolation as exc:
            self.report_violation("delete_path", exc.to_dict())
            raise

    def mkdir(self, path) -> Path:
        try:
            return fs.safe_mkdir(path, self.policy)
        except SecurityViolation as exc:
            self.report_violation("mkdir_path", exc.to_dict())
            raise

    def extract_archive(self, archive, destination) -> Path:
        try:
            return fs.safe_extract_archive(archive, destination, self.policy)
        except SecurityViolation as exc:
            self.report_violation("archive_extract", exc.to_dict())
            raise

    def temp_path(self, name: str) -> Path:
        return self.policy.temp_dir / name

    # -- execution ----------------------------------------------------------
    def _child_environment(self, environment: Optional[Dict[str, str]]) -> Dict[str, str]:
        """Tell the child process both of its boundaries (sections 107-110)."""
        # Section 32, as amended: the child is told where it may write, which
        # is the agent's own directory rather than the whole workspace. The
        # workspace it may read is a separate boundary and a separate variable.
        env = {
            WORKSPACE_ENV: str(self.policy.write_root),
            READABLE_ENV: str(self.policy.workspace_root),
            READONLY_ENV: os.pathsep.join(str(p) for p in self.policy.readonly_paths),
            PROTECTED_ENV: os.pathsep.join(str(p) for p in self.policy.protected_paths),
        }
        granted = getattr(self.policy, "extra_writable", None) or []
        if granted:
            env[GRANTED_ENV] = os.pathsep.join(str(p) for p in granted)
        sealed = getattr(self.policy, "sealed_paths", None) or []
        if sealed:
            env[SEALED_ENV] = os.pathsep.join(str(p) for p in sealed)
        names = getattr(self.policy, "child_readonly_names", None) or []
        if names:
            env[READONLY_NAMES_ENV] = os.pathsep.join(names)
        # The run logs: neither read nor written by code a run runs.
        closed = getattr(self.policy, "closed_paths", None) or []
        if closed:
            env[CLOSED_ENV] = os.pathsep.join(str(p) for p in closed)
        # A run of the Experience debugger: the application's code is not
        # read - but the interpreter, when it lives in it.
        code = getattr(self.policy, "child_code_root", None)
        if code is not None:
            env[CODE_ENV] = str(code)
            inside = [one for one in isolation.code_paths()
                      if fs.is_inside(one, code)]
            if inside:
                env[CODE_OPEN_ENV] = os.pathsep.join(str(one) for one in inside)
        if not getattr(self.policy, "network", True):
            env[NO_NETWORK_ENV] = "1"
            # The testing browser, where the user installed one and the
            # system keeps it to this run's own servers (runtime/browser.py).
            program = self.browser()
            if program is not None:
                env[BROWSER_ENV] = str(program)
        elif getattr(self.policy, "network_allow", None):
            env[NETWORK_ALLOW_ENV] = ",".join(self.policy.network_allow)
        if environment:
            env.update({str(k): str(v) for k, v in environment.items()})
        return env

    def browser(self) -> Optional[Path]:
        """The testing browser this runtime's code may start, or None: one
        is installed (in the protected state - runtime/browser.py), the
        code may reach no network, and the system gives the run a loopback
        of its own. Never in a packaged application, where no child starts."""
        if is_frozen() or getattr(self.policy, "network", True):
            return None
        protected = list(getattr(self.policy, "protected_paths", None) or [])
        if not protected:
            return None
        from . import browser
        try:
            return browser.for_run(Path(protected[0]))
        except Exception:                      # pragma: no cover - no browser, then
            log.warning("could not look for the testing browser", exc_info=True)
            return None

    def execute(
        self,
        command: Sequence[str],
        *,
        readable_paths: Optional[Sequence[Path]] = None,
        environment: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> ExecutionResult:
        """Section 33 - the common ``SandboxProvider.execute`` surface."""
        timeout = timeout or self.default_timeout
        readable = list(readable_paths or self.policy.readable_paths)
        environment = self._child_environment(environment)
        if self.sandbox_enabled:
            provider = self.platform.sandbox
        else:
            from ..platform_layer.base import SubprocessSandbox

            provider = SubprocessSandbox()
        with self.tracer.span(
            "runtime.execute", event_type="runtime.execute",
            connection_id="runtime->sandbox",
            inputs={"command": " ".join(str(p) for p in command)[:160],
                    "isolation": self.isolation},
        ) as span:
            # Every folder it may write, the state it may never read, and the
            # network it may reach: the operating system's half of the
            # boundary, where the platform has one (runtime/isolation.py).
            result = provider.create_restricted_process(
                command=list(command),
                writable_root=self.policy.write_root,
                readable_paths=readable,
                environment=environment,
                timeout=timeout,
                writable_roots=list(self.policy.write_roots),
                hidden_paths=list(self.policy.protected_paths),
                sealed_paths=list(getattr(self.policy, "sealed_paths", None) or []),
                code_root=getattr(self.policy, "child_code_root", None),
                closed_paths=list(getattr(self.policy, "closed_paths", None) or []),
                network=self.network_mode,
                readonly_names=list(getattr(self.policy, "child_readonly_names",
                                            None) or []),
            )
            span.output_summary = f"exit {result.returncode}"
            if result.returncode != 0:
                span.severity = "warning"
                span.status = "error"
                span.error = (result.stderr or "")[-200:]
        if self.observability is not None:
            self.observability.increment("runtime.executions")
        return result

    def store_generated_script(self, code: str, name: Optional[str] = None) -> Path:
        """Generated code lives in the agent's own ``.agent_runtime/scripts`` (section 111).

        It is never written into the application's installation directory.
        """
        from ..common.events.models import new_id

        filename = name or f"{new_id('script')}.py"
        if not filename.endswith(".py"):
            filename += ".py"
        target = self.policy.scripts_dir / filename
        return self.write_text(target, code)

    def script_command(self, script: Path, args: Optional[List[str]] = None) -> List[str]:
        """Build the interpreter invocation for a guarded script.

        In a packaged application there is no system Python to call, so the
        bundled executable re-enters itself in script runner mode instead.
        """
        if is_frozen():
            return [sys.executable, SCRIPT_RUNNER_FLAG, str(script), *(args or [])]
        # The guard's copy outside the application's code (ensure_guard).
        guard_dir = str(self.guard_dir())
        bootstrap = _GUARD_BOOTSTRAP.format(guard_dir=guard_dir, script=str(script))
        # UTF-8 throughout (-X utf8): -I ignores PYTHONIOENCODING, and on
        # Windows a pipe was then written in the old code page - a print of
        # any Chinese text failed with UnicodeEncodeError.
        return [sys.executable, "-I", "-X", "utf8", "-c", bootstrap, *(args or [])]

    def guard_dir(self) -> Path:
        """The folder this runtime's children load the guard from, made
        and checked now (see `ensure_guard`)."""
        return ensure_guard(guard_home_for(self.policy)) or Path(__file__).resolve().parent

    def run_python_script(
        self,
        script_path,
        args: Optional[List[str]] = None,
        timeout: Optional[float] = None,
        sys_path: Optional[Sequence[Path]] = None,
        environment: Optional[Dict[str, str]] = None,
    ) -> ScriptResult:
        """Run a script with the in-process guard installed (section 31).

        `sys_path` is put first on the script's import path - the packages
        installed for a piece of work - and `environment` is added to the
        child's sanitized one."""
        try:
            resolved = fs.validate_read_path(script_path, self.policy)
        except SecurityViolation as exc:
            self.report_violation("script_path", exc.to_dict())
            return ScriptResult(False, -1, "", str(exc), self.isolation, violation=str(exc))
        if not resolved.exists():
            return ScriptResult(False, -1, "", f"script not found: {resolved}", self.isolation)

        command = self.script_command(resolved, args)
        guard_dir = self.guard_dir()
        extra = dict(environment or {})
        sys_path = list(sys_path or [])
        if self.browser() is not None:
            # Where `import agent_browser` finds the helper.
            from . import browser
            sys_path.append(browser.helper_folder(Path(self.policy.protected_paths[0])))
        if sys_path:
            extra[SYS_PATH_ENV] = os.pathsep.join(str(one) for one in sys_path)
        result = self.execute(
            command,
            readable_paths=[guard_dir, resolved.parent, *self.policy.readable_paths],
            environment=extra or None,
            timeout=timeout,
        )
        violation = None
        if "SandboxViolation" in (result.stderr or ""):
            # WHAT WAS REFUSED, in the guard's own words - a network
            # connection, a read of a sealed folder, a write outside the run's
            # own: "script_write" with one fixed sentence said a write was
            # attempted whatever it was, and named nothing.
            said = guard_refusals(result.stderr or "")
            violation = ("the guard refused: " + "; ".join(said)) if said else \
                "the guard refused something the script tried"
            self.report_violation(
                "guard_refused", {"script": str(resolved), "message": violation}
            )
        return ScriptResult(
            ok=result.returncode == 0 and violation is None,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            isolation=result.isolation,
            script_path=str(resolved),
            timed_out=result.timed_out,
            violation=violation,
            os_isolation=self.os_isolation(),
        )

    def run_python_code(self, code: str, timeout: Optional[float] = None,
                        sys_path: Optional[Sequence[Path]] = None) -> ScriptResult:
        script = self.store_generated_script(code)
        return self.run_python_script(script, timeout=timeout, sys_path=sys_path)

    def download(self, url: str, destination: str, timeout: float = 60.0) -> Path:
        """Downloads are writes and obey the same workspace rule (section 31)."""
        import urllib.request

        target = fs.validate_write_path(destination, self.policy)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("only http(s) downloads are supported")
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - validated
            data = response.read()
        target.write_bytes(data)
        return target

    # -- self test (sections 107, 115) --------------------------------------
    def self_test(self) -> Dict[str, Any]:
        """Verify the filesystem invariants on this machine, in this build.

        A packaged application runs an embedded interpreter, so the checks that
        matter in development have to be re-run where the artifact is installed
        (section 107 - packaging must not weaken the security model).
        """
        checks: List[Dict[str, Any]] = []

        def record(name: str, passed: bool, detail: str) -> None:
            checks.append({"check": name, "passed": bool(passed), "detail": detail[:400]})

        # 1. A write inside the workspace succeeds.
        try:
            probe = self.write_text(self.policy.temp_dir / "selftest.txt", "ok")
            record("workspace_write_allowed", probe.exists(), str(probe))
        except Exception as exc:
            record("workspace_write_allowed", False, f"{type(exc).__name__}: {exc}")

        # 2. A write outside the workspace is refused.
        outside = Path(tempfile.gettempdir()) / "personalized-agent-selftest-escape.txt"
        try:
            self.write_text(outside, "should never happen")
            record("workspace_escape_blocked", False, f"the write to {outside} succeeded")
        except SecurityViolation as exc:
            record("workspace_escape_blocked", True, str(exc))
        except Exception as exc:
            record("workspace_escape_blocked", True, f"{type(exc).__name__}: {exc}")

        # 3. Trusted application state is unreadable to agent controlled code.
        if self.policy.protected_paths:
            target = Path(self.policy.protected_paths[0]) / "settings" / "settings.json"
            try:
                self.read_text(target)
                record("application_state_protected", False, f"{target} was readable")
            except SecurityViolation as exc:
                record("application_state_protected", True, str(exc))
            except Exception as exc:
                record("application_state_protected", True, f"{type(exc).__name__}: {exc}")
        else:
            record("application_state_protected", False, "no protected paths are configured")

        # 4. A generated script runs, and 5. cannot escape the workspace.
        escape_target = (Path(tempfile.gettempdir()) / "pa-script-escape.txt").as_posix()
        script = self.run_python_code(
            "from pathlib import Path\n"
            "Path('.agent_runtime/temp/script_selftest.txt').write_text('ok')\n"
            "print('script ran')\n",
            timeout=90,
        )
        record("generated_script_runs", script.ok, (script.stdout or script.stderr).strip())
        escape = self.run_python_code(
            f"open({escape_target!r}, 'w').write('nope')\n", timeout=90
        )
        escaped_file = Path(escape_target)
        record(
            "generated_script_escape_blocked",
            (not escape.ok) and not escaped_file.exists(),
            (escape.stderr or "").strip()[-400:] or "script failed without output",
        )
        if escaped_file.exists():  # never expected; clean up if it somehow happened
            escaped_file.unlink(missing_ok=True)

        # 6. The layer that the escape check cannot see: the user's own
        # material is inside the workspace, so nothing above proves a script
        # cannot rewrite it (section 32, as amended).
        witness = self.policy.workspace_root / "documents" / ".selftest_witness"
        original = "belongs to the user"
        try:
            witness.parent.mkdir(parents=True, exist_ok=True)
            witness.write_text(original, encoding="utf-8")
            clobber = self.run_python_code(
                f"open({witness.as_posix()!r}, 'w').write('clobbered')\n", timeout=90
            )
            intact = witness.read_text(encoding="utf-8") == original
            record(
                "user_material_not_writable",
                (not clobber.ok) and intact,
                "the user's file was rewritten by a generated script" if not intact
                else (clobber.stderr or "").strip()[-400:] or "refused without output",
            )
        except Exception as exc:  # the check itself failed; say so rather than pass
            record("user_material_not_writable", False, f"{type(exc).__name__}: {exc}")
        finally:
            witness.unlink(missing_ok=True)

        # 7. Section 31, as amended: a script must not be able to step off the
        # guarded surface by starting a process that has none. Checked here
        # rather than only in the tests because the packaged application ships
        # its own compiled copy of the guard. Where the operating system
        # confines a run that reaches no network, the child starts - with the
        # guard loaded into it, which refuses the write; anywhere else no
        # child starts at all.
        # Either way nothing is written outside and the script fails.
        spawn_target = (Path(tempfile.gettempdir()) / "pa-spawn-escape.txt")
        spawn_target.unlink(missing_ok=True)
        spawned = self.run_python_code(
            "import subprocess, sys\n"
            "subprocess.run([sys.executable, '-c', "
            f"'open(\"{spawn_target.as_posix()}\", \"w\").write(\"x\")'])\n",
            timeout=90,
        )
        record(
            "process_creation_blocked",
            (not spawned.ok) and not spawn_target.exists(),
            "a generated script started a process that wrote outside"
            if spawn_target.exists()
            else (spawned.stderr or "").strip()[-300:] or "refused without output",
        )
        spawn_target.unlink(missing_ok=True)

        return {
            "passed": all(check["passed"] for check in checks),
            "isolation": self.isolation,
            "packaged": is_frozen(),
            "workspace_root": str(self.policy.workspace_root),
            "writable_root": str(self.policy.write_root),
            "readonly_paths": [str(path) for path in self.policy.readonly_paths],
            "protected_paths": [str(p) for p in self.policy.protected_paths],
            "checks": checks,
        }

    def describe(self) -> Dict[str, Any]:
        return {
            "isolation": self.isolation,
            "sandbox_enabled": self.sandbox_enabled,
            "packaged": is_frozen(),
            "script_interpreter": "bundled runtime" if is_frozen() else "development interpreter",
            "platform": self.platform.describe(),
            "policy": self.policy.to_dict(),
        }
