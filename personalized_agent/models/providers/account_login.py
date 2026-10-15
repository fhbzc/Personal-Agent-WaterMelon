"""Browser based account login (sections 75, 75.1, 75.2).

The user presses *Sign in* and finishes in their browser.  They never open a
terminal, and they never leave the application to read instructions.

Anthropic issues no OAuth client identity to third party desktop applications,
and reusing one that belongs to another application would be impersonation
(section 75.2).  The supported path is therefore a provider tool the user has
already installed, which mints the credential and which this application hosts:

* **Claude Code** - ``claude setup-token`` prints an authorization URL, takes the
  code back, and returns a long-lived token, which is stored through the
  Credential Manager and never displayed.
* **Anthropic CLI** - ``ant auth login --no-browser`` prints an authorization
  URL, takes the code back, and writes its own profile, which the official SDKs
  resolve on their own.

Either way this application captures the URL, opens the browser itself, collects
the code in its own window, and hands it back to the tool.  The tool's console
is never the user's interface, so a tool that only renders to a terminal is
driven through a pseudo-terminal that this application owns.
"""

from __future__ import annotations

import importlib.util
import logging
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from ...common import children

log = logging.getLogger(__name__)

# Terminal rendering the user must never see, and the shapes we look for in it.
ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][A-B0-2]|[\x00-\x08\x0b-\x0c\x0e-\x1f]")
URL_PATTERN = re.compile(r"https://[^\s\"'<>]+")
TOKEN_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")
# The tool reports a rejected code and then waits for another one, so an error
# has to be recognised: waiting for a token that will never come looks to the
# user exactly like a sign-in that has hung.
ERROR_LINE = re.compile(r"(?:oauth|authentication|login)\s*error\s*:?\s*(.+)", re.I)
RETRY_PROMPT = re.compile(r"press\s*enter\s*to\s*retry", re.I)

URL_TIMEOUT_SECONDS = 60.0
SESSION_TIMEOUT_SECONDS = 900.0
EXCHANGE_TIMEOUT_SECONDS = 120.0
# How long a tool may take to wind up after handing back its token.
POST_TOKEN_GRACE_SECONDS = 5.0

# A pseudo-terminal wide enough that the tool does not wrap the URL it prints:
# rejoining a wrapped URL is guesswork, because the next line may be the tool's
# own prose, so the width is chosen to stop wrapping from happening.
PTY_COLUMNS = 1000
PTY_SIZE = (50, PTY_COLUMNS)

CLAUDE_CODE_URL = "https://claude.com/product/claude-code"
ANTHROPIC_CLI_URL = "https://docs.claude.com/en/api/overview"


class LoginState:
    IDLE = "idle"
    STARTING = "starting"
    AWAITING_AUTHORIZATION = "awaiting_authorization"
    EXCHANGING = "exchanging"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Transports: how the tool is run so that this application, not a console, is
# the user's interface.
# ---------------------------------------------------------------------------
class PipeTransport:
    """For a tool that speaks over plain pipes."""

    name = "pipe"

    def __init__(self, command: List[str]) -> None:
        self.process = children.popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if sys.platform.startswith("win") else 0,
        )

    def read_stream(self, sink: "queue.Queue") -> None:
        try:
            for line in self.process.stdout:
                sink.put(line)
        except Exception:                          # pragma: no cover - pipe closed
            log.debug("sign-in output stream ended", exc_info=True)
        finally:
            sink.put(None)

    def write(self, text: str) -> None:
        self.process.stdin.write(text)
        self.process.stdin.flush()

    def alive(self) -> bool:
        return self.process.poll() is None

    def wait(self, timeout: float) -> Optional[int]:
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except Exception:                      # pragma: no cover - already gone
                pass


class PtyTransport:
    """For a tool that only renders to a terminal.

    Section 75.2: the application hosts that terminal rather than handing the
    user to one.
    """

    name = "pty"

    def __init__(self, command: List[str]) -> None:
        from winpty import PtyProcess

        self.process = PtyProcess.spawn(command, dimensions=PTY_SIZE)

    @staticmethod
    def available() -> bool:
        """Whether a pseudo-terminal can actually be created here.

        Section 113, as amended: this decides whether account login is offered,
        so it must test what the sign-in will rely on. The module importing is
        not enough - the pty is created by helper executables that ship
        alongside it, and a packaged build that dropped them imports cleanly and
        then produces a terminal that emits nothing.
        """
        try:
            spec = importlib.util.find_spec("winpty")
        except Exception:                          # pragma: no cover - broken install
            return False
        if spec is None or not spec.origin:
            return False
        package = Path(spec.origin).parent
        required = ("winpty.dll",)
        # Either backend is enough: the older agent, or the ConPTY pair.
        backends = (("winpty-agent.exe",), ("conpty.dll", "OpenConsole.exe"))
        if not all((package / name).exists() for name in required):
            return False
        return any(all((package / name).exists() for name in backend)
                   for backend in backends)

    def read_stream(self, sink: "queue.Queue") -> None:
        try:
            while True:
                data = self.process.read(1024)
                if not data:
                    break
                sink.put(data)
        except Exception:                          # EOF when the tool exits
            log.debug("sign-in terminal ended", exc_info=True)
        finally:
            sink.put(None)

    def write(self, text: str) -> None:
        self.process.write(text)

    def alive(self) -> bool:
        try:
            return bool(self.process.isalive())
        except Exception:                          # pragma: no cover
            return False

    def wait(self, timeout: float) -> Optional[int]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.alive():
                return int(getattr(self.process, "exitstatus", 0) or 0)
            time.sleep(0.2)
        return None

    def stop(self) -> None:
        try:
            if self.alive():
                self.process.terminate(force=True)
        except Exception:                          # pragma: no cover
            log.debug("could not stop the sign-in terminal", exc_info=True)


# ---------------------------------------------------------------------------
# Backends: the tools that can mint an account credential on this machine.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LoginBackend:
    key: str
    executable: str
    display_name: str
    arguments: Tuple[str, ...]
    needs_terminal: bool
    #: True when the tool hands us a token we must store ourselves; False when
    #: it writes its own credential store that the SDK already resolves.
    returns_token: bool
    #: True when the tool opens the browser itself. Opening it again would put
    #: the user in front of two authorization pages for one sign-in.
    opens_browser: bool
    install_url: str
    account_note: str
    #: True when the credential it produces can drive this application's API
    #: calls. A tool whose credential authenticates and is then refused is not
    #: a way in, and offering it as one wastes the user's time (75.2).
    yields_api_credential: bool = True

    def path(self) -> Optional[str]:
        return shutil.which(self.executable)

    def usable(self) -> bool:
        if self.path() is None or not self.yields_api_credential:
            return False
        return PtyTransport.available() if self.needs_terminal else True

    def command(self) -> List[str]:
        return [self.path() or self.executable, *self.arguments]


BACKENDS: Tuple[LoginBackend, ...] = (
    # Ordered by what the credential can actually do. The Anthropic CLI signs
    # in for the API and writes a profile the official SDKs resolve on their
    # own, so it is tried first; Claude Code's token authenticates but is
    # refused for this application's API calls (section 75.2, as amended).
    LoginBackend(
        key="anthropic_cli",
        executable="ant",
        display_name="Anthropic CLI",
        arguments=("auth", "login", "--no-browser"),
        needs_terminal=False,
        returns_token=False,
        opens_browser=False,     # --no-browser: the tool prints, we open
        install_url=ANTHROPIC_CLI_URL,
        account_note="Signs in to your Anthropic account for API use and stores a "
                     "profile the official SDKs read. API usage is billed to your "
                     "Anthropic account, separately from any Claude subscription.",
    ),
    LoginBackend(
        key="claude_code",
        executable="claude",
        display_name="Claude Code",
        arguments=("setup-token",),
        needs_terminal=True,
        returns_token=True,
        opens_browser=True,
        yields_api_credential=False,
        install_url=CLAUDE_CODE_URL,
        account_note="Mints a token from your Claude subscription. That credential is "
                     "for Claude's own tools: this application's API requests are "
                     "refused with it, so use an API key or the Anthropic CLI instead.",
    ),
)


def available_backend() -> Optional[LoginBackend]:
    """The first tool on this machine that can complete a sign-in."""
    for backend in BACKENDS:
        if backend.usable():
            return backend
    return None


def installed_backend() -> Optional[LoginBackend]:
    """A tool that is installed, whether or not we can currently drive it."""
    for backend in BACKENDS:
        if backend.path():
            return backend
    return None


def cli_path() -> Optional[str]:
    backend = available_backend() or installed_backend()
    return backend.path() if backend else None


# ---------------------------------------------------------------------------
@dataclass
class AccountLoginSession:
    """One in-progress sign-in, owned by the provider."""

    state: str = LoginState.IDLE
    authorization_url: Optional[str] = None
    detail: str = ""
    backend: Optional[LoginBackend] = None
    #: Set only when the tool returns a token for us to store. Never rendered.
    token: Optional[str] = None
    started_at: float = field(default_factory=time.monotonic)
    #: Called with the token the moment one is captured, however the sign-in
    #: completed. The session never keeps a secret longer than it must.
    on_token: Optional[Callable[[str], None]] = None
    _transport: Optional[Any] = None
    _watcher: Optional[threading.Thread] = None
    _output: "queue.Queue" = field(default_factory=queue.Queue)
    _buffer: str = ""
    #: How much of the output has already been turned into a message, so one
    #: rejection is not reported again on the next attempt.
    _reported: int = 0
    #: Links already handed to the user. A rejected code makes the tool start
    #: over with a new one, and the old one is dead.
    _seen_urls: set = field(default_factory=set)
    _opened_browser: bool = False
    _closed: bool = False
    _lock: threading.RLock = field(default_factory=threading.RLock)

    # -- lifecycle ----------------------------------------------------------
    def start(self, open_browser: bool = True) -> Dict[str, Any]:
        backend = available_backend()
        if backend is None:
            self.state = LoginState.FAILED
            self.detail = account_login_unavailable_reason()
            return self.describe()
        self.backend = backend
        self.state = LoginState.STARTING
        self.started_at = time.monotonic()
        try:
            self._transport = (PtyTransport(backend.command()) if backend.needs_terminal
                               else PipeTransport(backend.command()))
        except Exception as exc:
            self.state = LoginState.FAILED
            self.detail = f"could not start {backend.display_name}: {exc}"
            return self.describe()

        threading.Thread(target=self._transport.read_stream, args=(self._output,),
                         name="account-login-reader", daemon=True).start()

        url = self._await_url()
        if url is None:
            # Say what actually happened. "No URL" on its own is unactionable:
            # the interesting cases are a tool that produced nothing at all
            # (its terminal support is broken) and one that produced output we
            # could not read.
            produced = len(_plain(self._buffer).strip())
            alive = self._transport is not None and self._transport.alive()
            self.cancel()
            self.state = LoginState.FAILED
            if produced == 0 and not alive:
                self.detail = (
                    f"{backend.display_name} started but produced no output, so the "
                    f"sign-in could not begin. This usually means its terminal "
                    f"support is incomplete in this build."
                )
            elif produced == 0:
                self.detail = (f"{backend.display_name} produced no output within "
                               f"{int(URL_TIMEOUT_SECONDS)} seconds")
            else:
                self.detail = (f"{backend.display_name} did not offer an authorization "
                               f"URL. It said: {_tail(self._buffer)}")
            log.warning("account sign-in could not start: %s (%d characters read)",
                        self.detail, produced)
            return self.describe()

        self.authorization_url = url
        self.state = LoginState.AWAITING_AUTHORIZATION
        self.detail = ("Approve the sign-in in your browser, then paste the code "
                       "it gives you below.")
        # Only open the page if the tool did not already do it: two tabs for one
        # sign-in is worse than none, because only one of them carries the
        # verifier this session is waiting on.
        self._opened_browser = bool(open_browser and not backend.opens_browser)
        if self._opened_browser:
            try:
                webbrowser.open(url)
            except Exception:                      # pragma: no cover - no browser
                log.debug("could not open a browser for the sign-in", exc_info=True)

        # The tool may finish on its own: where the browser can hand the result
        # straight back, there is no code for the user to paste and the tool
        # simply prints its token and exits. Watching from here means both
        # completions are handled, and the one the user is not expecting does
        # not look like a failure.
        self._watcher = threading.Thread(target=self._watch, name="account-login-watch",
                                         daemon=True)
        self._watcher.start()
        return self.describe()

    def _watch(self) -> None:
        """Single reader of the tool's output, until the sign-in resolves."""
        outcome, value = self._await_completion(SESSION_TIMEOUT_SECONDS)
        with self._lock:
            if self.state in (LoginState.COMPLETED, LoginState.CANCELLED):
                return
            if outcome == "token":
                self._accept_token(value)
                return
            if outcome == "rejected":
                self._reject(value)
                return
            wants_token = bool(self.backend and self.backend.returns_token)
            if outcome == "exit" and value == 0 and not wants_token:
                self.state = LoginState.COMPLETED
                self.detail = (f"signed in through {self.backend.display_name}"
                               if self.backend else "signed in")
                return
            if outcome == "timeout":
                self.state = LoginState.FAILED
                self.detail = ("the sign-in was not completed in time. "
                               f"{_tail(self._buffer)}").strip()
            elif outcome == "exit":
                self.state = LoginState.FAILED
                self.detail = (
                    f"{self.backend.display_name if self.backend else 'the sign-in'} "
                    f"finished without returning a credential. "
                    f"{_tail(self._buffer)}").strip()
            elif outcome == "gone":
                self.state = LoginState.FAILED
                self.detail = "the sign-in stopped before it finished"
    def _reject(self, message: str) -> None:
        """End the attempt with the tool's own words.

        The tool offers a retry of its own, but taking it means driving an
        interactive prompt and then depending on the new link it prints - and a
        keypress that arrives a moment early is silently dropped, leaving the
        user on a link that can only fail again. Ending here is deterministic:
        the next Sign in starts a fresh process with a fresh link.
        """
        self.state = LoginState.FAILED
        self.detail = f"{message}. Press Sign in again to start a new sign-in."
        self._finish_process()

    def _accept_token(self, token: str) -> None:
        """Record a captured token exactly once, then let the tool wind up."""
        self.token = token
        self.state = LoginState.COMPLETED
        self.detail = (f"signed in through {self.backend.display_name}"
                       if self.backend else "signed in")
        callback = self.on_token
        if callback is not None:
            try:
                callback(token)
            except Exception:                      # pragma: no cover - storage failure
                log.exception("could not store the credential from the sign-in")
                self.state = LoginState.FAILED
                self.detail = "the sign-in succeeded but the credential could not be stored"
        self._finish_process()

    def submit_code(self, code: str) -> Dict[str, Any]:
        """Hand the code from the browser to the tool, if it is still waiting.

        The watcher started with the sign-in owns the tool's output, so this
        only writes and then waits for the sign-in to resolve. A sign-in that
        already completed on its own is reported as completed rather than as a
        tool that is no longer running.
        """
        code = (code or "").strip()
        with self._lock:
            if self.state == LoginState.COMPLETED:
                return self.describe()
            if not code:
                self.detail = "enter the code from your browser first"
                return self.describe()
            transport = self._transport
            if transport is None or not transport.alive():
                if self.state != LoginState.FAILED:
                    self.state = LoginState.FAILED
                    self.detail = ("the sign-in is no longer running; start it again"
                                   if not self._buffer else
                                   f"the sign-in ended before the code was used. "
                                   f"{_tail(self._buffer)}")
                return self.describe()
            self.state = LoginState.EXCHANGING
            self.detail = "completing the sign-in"
            try:
                transport.write(code + ("\r" if transport.name == "pty" else "\n"))
            except Exception as exc:
                self.state = LoginState.FAILED
                self.detail = f"could not send the code to the sign-in: {exc}"
                return self.describe()

        # Waiting ends when the sign-in resolves - or when the tool rejects the
        # code and asks for another, which is an answer too: leaving the user
        # on "completing the sign-in" until a timeout hides what the tool said.
        deadline = time.time() + EXCHANGE_TIMEOUT_SECONDS
        while time.time() < deadline:
            if self.state in (LoginState.COMPLETED, LoginState.FAILED,
                              LoginState.CANCELLED, LoginState.AWAITING_AUTHORIZATION):
                return self.describe()
            time.sleep(0.2)
        self.cancel()
        self.state = LoginState.FAILED
        self.detail = ("the sign-in did not finish in time. "
                       f"{_tail(self._buffer)}").strip()
        return self.describe()

    def _finish_process(self) -> None:
        """Let the tool wind up on its own, then make sure it is gone.

        A tool that returns a token may keep its terminal open afterwards, so
        waiting for it to exit would hang the sign-in; but it is given a moment
        to finish its own bookkeeping before being stopped.
        """
        transport = self._transport
        if transport is None:
            return
        transport.wait(POST_TOKEN_GRACE_SECONDS)
        with self._lock:
            self._transport = None
        transport.stop()

    def cancel(self) -> Dict[str, Any]:
        with self._lock:
            transport, self._transport = self._transport, None
        if transport is not None:
            transport.stop()
        if self.state not in (LoginState.COMPLETED, LoginState.FAILED):
            self.state = LoginState.CANCELLED
            self.detail = "the sign-in was cancelled"
        return self.describe()

    def take_token(self) -> Optional[str]:
        """Hand the minted token to the caller once, then forget it."""
        token, self.token = self.token, None
        return token

    # -- state --------------------------------------------------------------
    def expired(self) -> bool:
        return (self.state in (LoginState.AWAITING_AUTHORIZATION, LoginState.STARTING)
                and time.monotonic() - self.started_at > SESSION_TIMEOUT_SECONDS)

    def describe(self) -> Dict[str, Any]:
        if self.expired():
            self.cancel()
            self.state = LoginState.FAILED
            self.detail = "the sign-in timed out; start it again"
        return {
            "state": self.state,
            "authorization_url": self.authorization_url,
            "detail": self.detail,
            "tool": self.backend.display_name if self.backend else None,
            "account_note": self.backend.account_note if self.backend else None,
            "awaiting_code": self.state == LoginState.AWAITING_AUTHORIZATION,
            "finished": self.state in (LoginState.COMPLETED, LoginState.FAILED,
                                       LoginState.CANCELLED),
        }

    # -- plumbing -----------------------------------------------------------
    def _drain(self, timeout: float) -> bool:
        """Collect whatever the tool has written. False once it has finished.

        The end-of-output sentinel arrives exactly once, so it is remembered:
        otherwise a caller draining until this returns False spins forever on an
        empty queue after some other call has already taken the sentinel.
        """
        if self._closed and self._output.empty():
            return False
        try:
            chunk = self._output.get(timeout=timeout)
        except queue.Empty:
            return not self._closed
        if chunk is None:
            self._closed = True
            return False
        self._buffer += chunk
        return True

    def _await_url(self) -> Optional[str]:
        """The next authorization URL the tool prints, not one already used."""
        deadline = time.monotonic() + URL_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if not self._drain(0.5) and not self._buffer:
                break
            url = self._next_url()
            if url:
                return url
            if self._transport is not None and not self._transport.alive():
                # Read whatever is left before giving up.
                while self._drain(0.2):
                    pass
                return self._next_url()
        return None

    def _next_url(self) -> Optional[str]:
        for candidate in URL_PATTERN.findall(_plain(self._buffer)):
            url = candidate.rstrip(".,)")
            if url not in self._seen_urls:
                self._seen_urls.add(url)
                return url
        return None

    def _await_completion(self, timeout: float):
        """Wait for whatever *this* tool does to signal that it is done.

        The signal differs by backend, and using the wrong one is what makes a
        sign-in appear to hang: a tool that hands back a token prints it and
        then keeps its terminal open, so waiting for the process to exit waits
        for something that will not happen.
        """
        wants_token = bool(self.backend and self.backend.returns_token)
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._drain(0.3)
            text = _plain(self._buffer)
            if wants_token:
                found = TOKEN_PATTERN.search(text)
                if found:
                    return "token", found.group(0)
            rejected = self._rejection(text)
            if rejected:
                return "rejected", rejected
            transport = self._transport
            if transport is None:
                return "gone", None
            if not transport.alive():
                while self._drain(0.2):
                    pass
                text = _plain(self._buffer)
                if wants_token:
                    found = TOKEN_PATTERN.search(text)
                    if found:
                        return "token", found.group(0)
                return "exit", transport.wait(5) or 0
        return "timeout", None

    def _rejection(self, text: str) -> Optional[str]:
        """The tool's own words for a code it would not accept."""
        unseen = text[self._reported:]
        if not (ERROR_LINE.search(unseen) or RETRY_PROMPT.search(unseen)):
            return None
        self._reported = len(text)
        match = ERROR_LINE.search(unseen)
        if match:
            message = match.group(1).strip()
            message = RETRY_PROMPT.split(message)[0].strip()
            return " ".join(message.split())[:200] or "the code was not accepted"
        return "the code was not accepted"


# ---------------------------------------------------------------------------
def _plain(text: str) -> str:
    return ANSI.sub("", text)


def _first_url(buffer: str, columns: int = PTY_COLUMNS) -> Optional[str]:
    """Find the authorization URL in what the tool printed.

    A wrapped URL is never reassembled by guesswork. A terminal wraps by
    filling a line and continuing on the next one, and what follows on that
    next line is just as likely to be the tool's own text - a hint, a prompt -
    which glued onto the URL produces a link that looks right and does not
    work. The terminal is therefore made wide enough that the URL fits on one
    line, and a URL that did fill a line exactly is treated as incomplete
    rather than handed over possibly truncated.
    """
    text = _plain(buffer)
    match = URL_PATTERN.search(text)
    if not match:
        return None
    line_start = text.rfind("\n", 0, match.start()) + 1
    consumed = match.end() - line_start
    if columns and consumed and consumed % columns == 0:
        return None
    return match.group(0).rstrip(".,)")


def _tail(buffer: str, limit: int = 200) -> str:
    """The last of the tool's output, with anything token-shaped removed."""
    text = TOKEN_PATTERN.sub("<token>", _plain(buffer))
    return " ".join(text.split())[-limit:]


def account_login_unavailable_reason() -> str:
    refused = next((b for b in BACKENDS
                    if b.path() and not b.yields_api_credential), None)
    if refused is not None:
        return (f"{refused.display_name} is installed, but the credential it mints is "
                f"refused for this application's API calls: a Claude subscription "
                f"covers Claude's own tools, not another application's API use")
    installed = installed_backend()
    if installed and installed.needs_terminal and not PtyTransport.available():
        return (f"{installed.display_name} is installed, but this build cannot host "
                f"its sign-in: the pseudo-terminal support it needs (pywinpty) is "
                f"not available")
    names = " or ".join(backend.display_name for backend in BACKENDS)
    return (f"signing in with your Anthropic account needs {names}, and neither is "
            f"installed on this machine")


def missing_tool_remedy() -> Tuple[str, str]:
    refused = next((b for b in BACKENDS
                    if b.path() and not b.yields_api_credential), None)
    if refused is not None:
        return (f"To use your Claude subscription, choose the \"Claude Code CLI\" "
                f"provider above - {refused.display_name} answers on this machine and "
                f"needs no credential here. For API billing instead, set an API key "
                f"from the Anthropic Console below.", CLAUDE_CODE_URL)
    installed = installed_backend()
    if installed and installed.needs_terminal and not PtyTransport.available():
        return ("Install pywinpty (pip install pywinpty) so the application can host "
                "the sign-in itself. An API key works without it.",
                installed.install_url)
    return ("Install Claude Code, or the Anthropic CLI, then reopen this dialog - "
            "the application hosts the browser sign-in and stores the credential "
            "it mints. An API key works without either.",
            BACKENDS[0].install_url)


def describe_backends() -> List[Dict[str, Any]]:
    """What is on this machine, for the Settings interface and diagnostics."""
    return [
        {
            "key": backend.key,
            "tool": backend.display_name,
            "executable": backend.executable,
            "installed": backend.path() is not None,
            "usable": backend.usable(),
            "needs_terminal": backend.needs_terminal,
            "returns_token": backend.returns_token,
            "yields_api_credential": backend.yields_api_credential,
            "account_note": backend.account_note,
            "install_url": backend.install_url,
        }
        for backend in BACKENDS
    ]


# Kept for callers that only want to know whether anything can sign in.
CLI_NAME = "claude"
CLI_INSTALL_URL = CLAUDE_CODE_URL
