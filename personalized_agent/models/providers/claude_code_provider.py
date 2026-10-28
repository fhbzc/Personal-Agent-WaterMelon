"""Claude Code as a model provider (section 76.1).

A Claude subscription licenses Claude's own tools, not another application's
API calls: a token minted from it authenticates and is then refused for the
Messages API. So rather than borrowing that credential, this provider asks the
licensed client to answer, and uses what it returns.

Claude Code answers non-interactively with ``claude -p``. It holds its own
sign-in, so this application stores no credential for it, and the work is
billed wherever that tool's account is billed.

Two things are deliberately withheld from it:

* **its tools** - it is asked to answer, not to act. It is run with no tools
  enabled, so it cannot read or write anything on the user's behalf. What the
  agent is allowed to do is decided by the agent's own tool layer and its
  sandbox, and routing actions through another program would go around both;
* **the workspace** - it runs in the agent's workspace directory, so that even
  a tool it somehow ran could not reach outside it (section 110.1).
"""

from __future__ import annotations

from dataclasses import replace

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..base import (
    AuthMethod,
    AuthStatus,
    AvailabilityState,
    CredentialState,
    FailureClass,
    ModelAvailability,
    ModelDescriptor,
    ModelInvocationError,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ToolCall,
    UsageSnapshot,
)
from ...common.agent_sessions import new_session_id
from ...common import children
# The mechanics of tools through a transcript are shared with Codex; the
# words the protocol is offered in (TOOL_PROTOCOL below) are this client's own.
from .local_tools import as_written, describe_tools, leading_tool_call, tool_results
from .local_tools import TOOL_PROTOCOL as SHARED_TOOL_PROTOCOL

log = logging.getLogger(__name__)


#: The client says it has stopped serving for a while, in its own words.
#:
#: It does not say "rate": the sentence is "You've hit your session limit ·
#: resets 11:50pm (Europe/Lisbon)". Matched on the shape of that rather
#: than on one spelling, because it is the client's wording and not an API
#: error code, and it has already been written more than one way.
def _limit_reached(lowered: str) -> bool:
    return (("session limit" in lowered or "weekly limit" in lowered
             or "limit reached" in lowered or "hit your limit" in lowered)
            or ("limit" in lowered and "resets" in lowered))


def _resets_at(detail: str) -> Optional[str]:
    """When the client says it will serve again, if it said."""
    found = re.search(r"resets?\s+(.+)$", detail.strip(), re.IGNORECASE)
    return found.group(1).strip(" .") if found else None


CLI_NAME = "claude"

#: The licensed client can be installed in more than one way, and a machine may
#: have several. Which one answers is the user's choice, not a search order
#: they cannot see: "the client said it is signed in" means nothing if they
#: cannot tell which client was asked (section 76.1).
#:
#: Each entry says where that kind of client keeps its executable. The paths
#: are candidates, not guarantees; presence is always checked on disk.
CLIENTS: Dict[str, Dict[str, Any]] = {
    "cli": {
        "display_name": "Claude Code (command line)",
        "note": "The standalone command-line client, installed with its own "
                "installer or through npm.",
        "install_url": "https://claude.com/product/claude-code",
        "candidates": [
            "~/.local/bin/claude", "~/.local/bin/claude.exe",
            "~/AppData/Roaming/npm/claude.cmd", "~/bin/claude",
            "/usr/local/bin/claude", "/opt/homebrew/bin/claude",
        ],
        "search_path": True,
    },
    "desktop": {
        "display_name": "Claude desktop application",
        "note": "The client bundled with the Claude desktop application.",
        "install_url": "https://claude.com/download",
        "candidates": [
            "~/AppData/Local/AnthropicClaude/claude.exe",
            "~/AppData/Local/AnthropicClaude/app/claude.exe",
            "~/AppData/Local/Programs/Claude/claude.exe",
            "C:/Program Files/Claude/claude.exe",
            # A Mac's: the application downloads its Claude Code into a folder
            # per version - claude-code/2.1.308/<build>/claude.app - and not
            # into Claude.app, whose MacOS/Claude is the application itself.
            "~/Library/Application Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude",
            "~/Library/Application Support/Claude/claude-code/*/claude",
            "~/AppData/Roaming/Claude/claude-code/*/*/claude.exe",
            "~/AppData/Roaming/Claude/claude-code/*/claude.exe",
            "/Applications/Claude.app/Contents/MacOS/claude",
            "/Applications/Claude.app/Contents/Resources/claude",
        ],
        "search_path": False,
    },
}

#: Tried in this order when the user has not chosen one.
CLIENT_ORDER = ("cli", "desktop")


def _version_key(path: Path) -> tuple:
    """The newest first: by the version the path names (2.1.308 after
    2.1.99), the folder of the latest build of it after that."""
    numbers = []
    for part in path.parts:
        if re.fullmatch(r"\d+(\.\d+)+", part):
            numbers = [int(n) for n in part.split(".")]
    try:
        made = path.stat().st_mtime
    except OSError:
        made = 0.0
    return (numbers, made)


def _found_at(candidate: str) -> List[Path]:
    """The files a candidate names: itself, or - with a * in it, for a
    folder per version - each match, the newest first."""
    path = Path(candidate).expanduser()
    if "*" not in candidate:
        return [path]
    parts = path.parts
    first = next(at for at, part in enumerate(parts) if "*" in part)
    base = Path(*parts[:first])
    try:
        found = list(base.glob(str(Path(*parts[first:])).replace("\\", "/")))
    except (OSError, ValueError):
        return []
    return sorted(found, key=_version_key, reverse=True)


def _named_exactly(path: Path) -> bool:
    """Whether the file at `path` bears that name, letter for letter. A Mac's
    disk ignores case: `Claude.app/Contents/MacOS/claude` is found, and is
    the desktop application `Claude` itself - asked whether it is signed
    in, it never answered, and every probe waited twenty seconds for it."""
    try:
        return path.name in os.listdir(path.parent)
    except OSError:
        return False
#: How long a call may be SILENT - nothing from the client, no tool of the
#: agent's running for it - before it is taken to have stopped working and
#: ended. Not a limit on how long it works: the client reports as it goes
#: (its output read as a stream), and a model thinking for eleven minutes
#: was measured never silent for more than 2.4 s. A fixed limit of 300 s
#: ended calls that were working - thinking at length - again and again.
DEFAULT_TIMEOUT_SECONDS = 150.0
#: And the most a call may work in all, not counting the time a tool of the
#: agent's runs for it (a run paused, an install waiting for the user): a
#: guard against a call that talks and never ends.
MOST_WORKING_SECONDS = 3 * 3600.0
#: What a tool of the agent's may take, as the client is told (ms): it is
#: run - and may wait for the user - by the agent, which ends it itself.
MCP_TOOL_TIMEOUT_MS = 24 * 3600 * 1000
STATUS_TIMEOUT_SECONDS = 20.0
#: How long the tool's sign-in state is trusted. Asking it costs a process
#: launch, and the interfaces that read provider state refresh on a timer:
#: without this, every refresh spawns one.
STATUS_CACHE_SECONDS = 60.0

#: Marks a sign-in state that could not be established, as distinct from one
#: established to be signed out. Reporting the first as the second sends the
#: user to fix a sign-in that was never broken.
UNREACHABLE = "_probe_failed"

#: THE MODELS, BY NAME: Claude Code takes a full model name as well as an
#: alias, and a name says which model answers - "opus" said only "whichever
#: Opus this client means today". The first is the one chosen when none is:
#: the latest Opus.
#: How hard it may be asked to think: `claude --effort` takes these.
EFFORTS = ["low", "medium", "high", "xhigh", "max"]

MODELS: List[ModelDescriptor] = [
    ModelDescriptor("claude-opus-5-5", "Claude Opus 5.5", 1_000_000, 128_000,
                    reasoning_efforts=list(EFFORTS)),
    ModelDescriptor("claude-fable-5-1", "Claude Fable 5.1", 1_000_000, 128_000,
                    reasoning_efforts=list(EFFORTS)),
    ModelDescriptor("claude-sonnet-5-5", "Claude Sonnet 5.5", 1_000_000, 128_000,
                    reasoning_efforts=list(EFFORTS)),
    ModelDescriptor("claude-haiku-5-5", "Claude Haiku 5.5", 1_000_000, 128_000,
                    reasoning_efforts=list(EFFORTS)),
    ModelDescriptor("claude-opus-5", "Claude Opus 5", 1_000_000, 128_000,
                    reasoning_efforts=list(EFFORTS)),
    ModelDescriptor("claude-sonnet-5", "Claude Sonnet 5", 1_000_000, 128_000,
                    reasoning_efforts=list(EFFORTS)),
    ModelDescriptor("claude-opus-4-8", "Claude Opus 4.8", 1_000_000, 128_000,
                    reasoning_efforts=list(EFFORTS)),
]

#: The aliases a setting may still hold, and the model each means now.
ALIASES = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5",
           "haiku": "claude-haiku-5-5", "fable": "claude-fable-5-1"}

#: Tools the client is denied by name. The real guarantee is that nothing runs
#: with bypassPermissions, so under -p there is nobody to approve a tool; this
#: list is the second layer.
#:
#: A name the CLI does not recognise is ignored rather than fatal - tested by
#: denying ``Bash`` alongside an invented name and asking for a shell command,
#: which was still refused. So the list may name things a given build might
#: not have, and should: a client that gains a capability later must not gain
#: it here by default.
#:
#: **Two kinds of thing are denied.** The first is acting on the machine -
#: reading, writing, running, fetching. The second is subtler and was found
#: the hard way: the client can schedule, remind, notify and delegate *for
#: itself*, and those look to the model like perfectly good ways to do what
#: the user asked. They are not. They live and die with one invocation of the
#: CLI, while the agent has a scheduler that survives restarts - so a user who
#: asked to be reminded at two o'clock got a job that existed only inside a
#: subprocess that had already exited, and was told, accurately and uselessly,
#: that it was "session-only".
DENIED_TOOLS = (
    # Acting on the machine.
    "Bash", "Write", "Edit", "NotebookEdit", "Read", "Glob", "Grep",
    "WebFetch", "WebSearch", "Task",
    # Arranging for something to happen later. The agent's own scheduler is
    # the only thing here that outlives the call.
    "CronCreate", "CronDelete", "CronList", "ScheduleWakeup", "RemoteTrigger",
    # Reaching the user, or anyone else, by a route the agent does not own.
    "PushNotification", "SendMessage", "SendUserFile", "EndConversation",
    # Delegating, publishing, or acquiring more tools mid-answer.
    "Agent", "Workflow", "Artifact", "ArtifactComments", "ArtifactData",
    "Monitor", "TaskOutput", "TaskStop", "ToolSearch", "Skill",
)

#: How the agent's own tools are offered to a client that cannot call them
#: through the API's tool protocol.
#: The agent's tools, offered as text: the same words for Claude Code and
#: Codex (local_tools.TOOL_PROTOCOL).
TOOL_PROTOCOL = SHARED_TOOL_PROTOCOL


#: How the agent's tools are offered when they are served to the client
#: natively (`ModelRequest.tool_runner`, over MCP - see mcp_bridge).
NATIVE_TOOLS = (
    "Your tools for this conversation are served to you directly: call them "
    "as tools. Each named in your instructions as `name` is the tool "
    "`mcp__pa__name` (run_python is mcp__pa__run_python, and so on). They "
    "are really run - the agent executes each call in its own sandbox and "
    "gives you back what it returned. The program you are running inside has "
    "no other tools for you. When you have what you need, answer as you were "
    "asked."
)


#: The agent's own tools for the web, by every name they go by. With the
#: model's own search (`ModelRequest.builtin_web`) they are not offered:
#: Claude Code's WebSearch and WebFetch are, in their place - held to the
#: same checks as the agent's (`ModelRequest.web_guard`) by hooks around
#: them, and only so: with no guard to hold them to, they are not offered.
WEB_TOOL_NAMES = ("web_search", "fetch_page", "fetch_url")
BUILTIN_WEB = ("WebSearch", "WebFetch")

BUILTIN_WEB_NOTE = (
    "SEARCH THE WEB WITH YOUR OWN TOOLS: WebSearch to search, WebFetch to read a "
    "page. Where your instructions name web_search, fetch_page or fetch_url, use "
    "these instead - they do the same. Public pages only: never sign in to "
    "anything, never post or send anything."
)


#: Its own reading tools, offered with `ModelRequest.native_read`: they only
#: read, and are denied every place the agents may not read.
NATIVE_READ = ("Read", "Glob", "Grep")
#: The most characters the places denied to it may take on the command line.
READ_RULES_CHARS = 16_000

NATIVE_READ_NOTE = (
    "READ FILES WITH YOUR OWN TOOLS TOO: Read opens a file directly - a PDF page by "
    "page, an image as a picture - and Glob and Grep find files and words. Some "
    "places are closed to you (where the user's sign-ins are kept, other runs' "
    "folders, the application's own data): a refusal there is final. Anything you "
    "change - a file written, code run - is done with the tools you were given."
)


def _unconfined_checks(request: ModelRequest) -> Any:
    """Without the sandbox: the agent's check of the client's own tools,
    for its hook (agent/work/unconfined.py) - each refusal told."""
    spec = request.unconfined
    if not spec:
        return None
    from ...agent.work import unconfined
    places = [Path(str(one)) for one in spec.get("places") or []]
    told = spec.get("on_denied")

    def checks(body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if body.get("hook_event_name") not in (None, "PreToolUse"):
            return None
        why = unconfined.hook(body, places)
        if why is None:
            return None
        if callable(told):
            told(str(body.get("tool_name") or ""), body.get("tool_input") or {}, why)
        return unconfined.denied_answer(why)
    return checks


def read_rule(place: str) -> str:
    """A place denied to Read, as Claude Code writes one: an absolute path as
    //c/Users/... (a Windows drive as its lower-case letter), a folder with
    everything in it."""
    path = Path(place)
    text = path.as_posix()
    if len(text) >= 2 and text[1] == ":":
        text = "/" + text[0].lower() + text[2:]
    text = "/" + text.rstrip("/") if text != "/" else "/"
    if path.is_dir() or text.endswith("/") or not path.exists():
        return f"Read({text.rstrip('/')}/**)" if text != "/" else "Read(//**)"
    return f"Read({text})"


def _system_to_file(command: List[str]) -> "tuple[str, Optional[Path]]":
    """The command's system prompt moved into a file of its own - the
    command then names the file (--append-system-prompt-file). The prompt,
    and the file to remove once the call is over; none when there is none."""
    import tempfile
    if "--append-system-prompt" not in command:
        return "", None
    at = command.index("--append-system-prompt")
    system = command[at + 1]
    handle, name = tempfile.mkstemp(prefix="pa-system-", suffix=".txt")
    with os.fdopen(handle, "w", encoding="utf-8") as written:
        written.write(system)
    command[at:at + 2] = ["--append-system-prompt-file", name]
    return system, Path(name)


def _settings_to_file(command: List[str]) -> Optional[Path]:
    """The command's settings (the web hooks) moved into a file of their own
    - a path, not JSON, on a command line a shell may read; the file to
    remove once the call is over, or None."""
    import tempfile
    if "--settings" not in command:
        return None
    at = command.index("--settings")
    handle, name = tempfile.mkstemp(prefix="pa-settings-", suffix=".json")
    with os.fdopen(handle, "w", encoding="utf-8") as written:
        written.write(command[at + 1])
    command[at + 1] = name
    return Path(name)


class ClaudeCodeProvider(ModelProvider):
    """CLAUDE CODE, THE COMMAND LINE: `claude` as installed on its own. The
    Claude desktop application's client is another provider
    (`ClaudeDesktopProvider`): a different program, signed in on its own,
    perhaps to another account - so chosen as itself, never in place of
    this one."""

    provider_id = "claude_code"
    display_name = "Claude Code CLI"
    kind = "client"
    #: Which of `CLIENTS` this provider runs.
    client = "cli"

    def __init__(self, credentials: Any = None,
                 workspace: Any = None,
                 timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
                 settings: Any = None) -> None:
        self.credentials = credentials
        self.workspace = workspace
        # Which installed client answers is the user's explicit choice.
        self.settings = settings
        self.timeout_seconds = timeout_seconds
        self._usage = UsageSnapshot()
        self._status_cache: Optional[Dict[str, Any]] = None
        self._status_client: Optional[str] = None
        self._status_checked_at = 0.0

    # -- availability -------------------------------------------------------
    def executable(self) -> Optional[str]:
        """The client that will actually answer, or None if there is none."""
        return self._resolve()[1]

    def clients(self) -> List[Dict[str, Any]]:
        """Every kind of client, and whether it is actually here.

        A client that is not installed is still listed: the user picks it and
        is told it is not installed, which is the answer they were looking
        for. Hiding it just means the option they expected is missing.
        """
        chosen, _, _ = self._resolve()
        rows = []
        for key, spec in CLIENTS.items():
            path = self._locate(key)
            rows.append({
                "client": key,
                "display_name": spec["display_name"],
                "note": spec["note"],
                "installed": path is not None,
                "path": path or "",
                "in_use": key == chosen and path is not None,
                "install_url": spec["install_url"],
                "detail": (f"installed at {path}" if path else
                           f"not installed on this machine - {spec['install_url']}"),
            })
        return rows

    @staticmethod
    def _locate(client: str) -> Optional[str]:
        """Where a particular kind of client is installed, if it is."""
        spec = CLIENTS.get(client)
        if not spec:
            return None
        if spec.get("search_path"):
            # Including one installed after this application started
            # (platform_layer/programs.py).
            from ...platform_layer.programs import find_program
            found = find_program(CLI_NAME)
            if found:
                return found
        for candidate in spec["candidates"]:
            for path in _found_at(candidate):
                if path.is_file() and _named_exactly(path):
                    return str(path)
        return None

    def _choice(self) -> str:
        return self.client

    #: What to do when it is not signed in.
    SIGN_IN = f"Claude Code is not signed in. Run `{CLI_NAME} auth login`."

    def _chosen_path(self) -> str:
        getter = getattr(self, "settings", None)
        try:
            return str(getter.get("local_client_path") if getter else "") or ""
        except Exception:                                   # pragma: no cover
            return ""

    @staticmethod
    def _resolve_choice(choice: str, explicit_path: str = "") -> tuple:
        """Which client answers, as (client key, path, why not).

        A choice the user made is honoured or reported - never quietly
        replaced by a different client that happens to be installed, which
        would make "signed in" refer to an account they did not pick.
        """
        if explicit_path:
            path = Path(explicit_path).expanduser()
            if path.is_file():
                return "custom", str(path), None
            return "custom", None, f"the client set here is not at {explicit_path}"
        if choice in CLIENTS:
            found = ClaudeCodeProvider._locate(choice)
            if found:
                return choice, found, None
            spec = CLIENTS[choice]
            return choice, None, (f"{spec['display_name']} is not installed on this "
                                  f"machine; install it from {spec['install_url']}, "
                                  f"or choose a different client")
        for key in CLIENT_ORDER:
            found = ClaudeCodeProvider._locate(key)
            if found:
                return key, found, None
        return "auto", None, "no Claude client is installed on this machine"

    def _resolve(self) -> tuple:
        return self._resolve_choice(self._choice(), self._chosen_path())

    def _auth_status(self, force: bool = False) -> Dict[str, Any]:
        """Whether the client is signed in, asked at most once a minute.

        It is a local question, but answering it starts a process, and the
        panels that show provider state refresh on a timer.

        There are three answers here, not two: signed in, not signed in, and
        *could not ask*. The third used to be reported as the second, which
        told the user their client was signed out when in truth this
        application had failed to run it - so they went looking for a sign-in
        problem that did not exist. The reason is kept, and reported as itself.
        """
        now = time.time()
        cli = self.executable()
        # The cache is keyed on the client it describes: switching clients must
        # not inherit the previous one's answer, which would attribute one
        # account's sign-in to another.
        if (not force and self._status_cache is not None
                and self._status_client == cli
                and now - self._status_checked_at < STATUS_CACHE_SECONDS):
            return self._status_cache
        status = self._ask_client(cli)
        self._status_cache = status
        self._status_client = cli
        self._status_checked_at = now
        return status

    def _ask_client(self, cli: Optional[str]) -> Dict[str, Any]:
        """Run the sign-in probe once, recording why it failed if it did."""
        if not cli:
            return {}
        try:
            completed = children.run(
                [cli, "auth", "status", "--json"], capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=STATUS_TIMEOUT_SECONDS,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if sys.platform.startswith("win") else 0,
            )
        except subprocess.TimeoutExpired:
            reason = (f"{cli} did not answer within "
                      f"{STATUS_TIMEOUT_SECONDS:.0f}s")
            log.warning("the sign-in probe timed out: %s", reason)
            return {UNREACHABLE: reason}
        except (OSError, subprocess.SubprocessError) as exc:
            reason = f"{cli} could not be run ({exc})"
            log.warning("the sign-in probe failed: %s", reason)
            return {UNREACHABLE: reason}
        if completed.returncode != 0:
            # A STATUS IS AN ANSWER, WHATEVER THE EXIT CODE. Signed out, the
            # client prints {"loggedIn": false, ...} and exits non-zero -
            # and that was read as "the probe failed", logged as a warning,
            # and treated as "could not check, so carry on". The agent then
            # called a model it was not signed in to, every call was
            # refused, and nobody was asked to sign in.
            try:
                parsed = json.loads(completed.stdout or "")
            except ValueError:
                parsed = None
            if isinstance(parsed, dict) and "loggedIn" in parsed:
                return parsed
            # Both streams: this client reports some failures on stdout.
            said = ((completed.stderr or "").strip()
                    or (completed.stdout or "").strip())[:200]
            lowered = said.lower()
            # A client that ran and said "not signed in" is an answer, not a
            # failure to ask - and it is the one the user can act on.
            if any(w in lowered for w in ("login", "logged in", "not authenticated")):
                return {}
            reason = said or f"{cli} exited with code {completed.returncode}"
            log.warning("the sign-in probe failed: %s", reason)
            return {UNREACHABLE: reason}
        try:
            parsed = json.loads(completed.stdout or "{}")
        except ValueError:
            reason = f"{cli} answered with something this application could not read"
            log.warning("the sign-in probe failed: %s", reason)
            return {UNREACHABLE: reason}
        return parsed if isinstance(parsed, dict) else {}

    def list_models(self) -> List[ModelDescriptor]:
        return list(MODELS)

    def accepts_model_id(self, model: str) -> bool:
        # A name of the list, or an alias the client still takes.
        return model in ALIASES or any(one.id == model for one in MODELS)

    def get_auth_methods(self) -> List[AuthMethod]:
        # The tool holds its own sign-in; this application stores nothing.
        return [AuthMethod.NONE]

    def get_auth_status(self) -> AuthStatus:
        client, path, problem = self._resolve()
        if not path:
            return AuthStatus(CredentialState.NOT_CONFIGURED, AuthMethod.NONE,
                              detail=problem
                              or f"{CLI_NAME} is not installed on this machine")
        status = self._auth_status()
        if status.get("loggedIn"):
            account = status.get("email") or status.get("orgName")
            plan = status.get("subscriptionType")
            # Which client is signed in matters: two clients on one machine can
            # be signed in to different accounts, and the bill follows this one.
            name = CLIENTS.get(client, {}).get("display_name", "the chosen client")
            return AuthStatus(
                CredentialState.AUTHENTICATED, AuthMethod.NONE, account=account,
                detail=f"signed in to {name}"
                       + (f" ({plan})" if plan else "")
                       + "; this application stores no credential for it",
            )
        unreachable = status.get(UNREACHABLE)
        if unreachable:
            name = CLIENTS.get(client, {}).get("display_name", "the chosen client")
            return AuthStatus(
                CredentialState.ERROR, AuthMethod.NONE,
                detail=f"could not ask {name} whether it is signed in: {unreachable}. "
                       f"It will still be tried.")
        return AuthStatus(CredentialState.AUTHENTICATION_REQUIRED, AuthMethod.NONE,
                          detail=self.SIGN_IN)

    def authenticate(self, config: Dict[str, Any]) -> AuthStatus:
        # There is nothing to authenticate here: the tool owns its sign-in.
        return self.get_auth_status()

    def logout(self) -> AuthStatus:
        return AuthStatus(CredentialState.NOT_CONFIGURED, AuthMethod.NONE,
                          detail=f"sign out with `{CLI_NAME} auth logout`")

    def check_availability(self, model: str) -> ModelAvailability:
        now = datetime.now(timezone.utc)
        _, path, problem = self._resolve()
        if not path:
            # The reason names the client the user actually chose: "claude is
            # not installed" is not an answer to "use the desktop application".
            return ModelAvailability(
                available=False, state=AvailabilityState.PROVIDER_UNAVAILABLE,
                reason=problem or f"{CLI_NAME} is not installed on this machine",
                checked_at=now, failure_class=FailureClass.USER_ACTION_REQUIRED)
        status = self._auth_status()
        if status.get(UNREACHABLE):
            # We could not ask. That is not grounds to refuse the user an
            # answer: the client itself is the authority, so let it decide,
            # and classify this as temporary so nothing is blocked on it.
            log.info("proceeding without a sign-in check: %s", status[UNREACHABLE])
            return ModelAvailability(
                available=True, state=AvailabilityState.AVAILABLE,
                authentication_valid=False, checked_at=now,
                reason=f"the sign-in state could not be checked: {status[UNREACHABLE]}",
                failure_class=FailureClass.TEMPORARY)
        if not status.get("loggedIn"):
            return ModelAvailability(
                available=False, state=AvailabilityState.AUTHENTICATION_REQUIRED,
                reason=f"Claude Code is not signed in. Run `{CLI_NAME} auth login`.",
                checked_at=now, failure_class=FailureClass.USER_ACTION_REQUIRED)
        return ModelAvailability(available=True, state=AvailabilityState.AVAILABLE,
                                 authentication_valid=True, checked_at=now,
                                 failure_class=FailureClass.TEMPORARY)

    # -- invocation ---------------------------------------------------------
    def _prompt(self, request: ModelRequest) -> str:
        """Render the conversation as one prompt.

        The tool takes a prompt, not a message list, so the roles are named in
        the text; dropping them would leave the model unable to tell what it
        said from what the user said.
        """
        parts: List[str] = []
        for message in request.messages:
            role = str(message.get("role", "user"))
            content = message.get("content")
            speaker = {"user": "User", "assistant": "Assistant"}.get(role, role.title())
            if isinstance(content, list):           # tool results and blocks
                results = self._tool_results(content)
                if results is not None:
                    # What a tool returned is not the user speaking. The API
                    # carries a tool result on a user turn, which is its
                    # convention and invisible there; rendered into a
                    # transcript it reads as the user pasting a block of JSON,
                    # and an answer built on it gets disowned - "these are
                    # from my own records, not from checking just now".
                    parts.append(results)
                    continue
                # Text, a picture said where it was - never its bytes.
                from ..vision import text_of
                content = text_of(content)
            parts.append(f"{speaker}: {content}")
        parts.append("Assistant:")
        prompt = "\n\n".join(parts)
        if getattr(request, "native_read", None):
            # Claude sees pictures: the file one came from, opened with Read.
            prompt = prompt.replace("[a picture]", "[a picture - to see it, open the file at "
                                    "its path above with your Read tool]")
        return prompt

    @staticmethod
    def _tool_results(blocks: List[Any]) -> Optional[str]:
        """The blocks rendered as tool output, or None if that is not what they are."""
        return tool_results(blocks)

    def _system(self, request: ModelRequest, native: bool = False) -> str:
        pieces = [request.system] if request.system else []
        if request.tools and native:
            pieces.append(NATIVE_TOOLS)
        elif request.tools:
            # Each tool with its whole argument schema, not only its name:
            # see describe_tools for why a name alone was not enough.
            pieces.append(TOOL_PROTOCOL.format(tools=describe_tools(request.tools)))
        return "\n\n".join(piece for piece in pieces if piece)

    def _command(self, request: ModelRequest, model: str,
                 session_id: Optional[str] = None, bridge: Any = None) -> List[str]:
        if request.unconfined:
            return self._unconfined_command(request, model, session_id, bridge)
        command = [
            self.executable() or CLI_NAME,
            # The prompt is written to stdin, not passed as an argument: a
            # conversation grows with every turn and tool result, and a command
            # line has a hard length limit that it would eventually cross -
            # silently, and only for the users who had talked longest.
            "-p",
            # Read as it comes: what the client is doing - thinking, writing,
            # calling a tool - arrives as it does it, so a call is ended when
            # it falls SILENT, not when a clock runs out (see
            # DEFAULT_TIMEOUT_SECONDS); and its last line is the envelope.
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--model", model,
            # How hard it thinks, as chosen for this model on the list.
            *(["--effort", request.reasoning_effort]
              if request.reasoning_effort in EFFORTS else []),
            # It is asked to answer, not to act. The guarantee is the absence
            # of --permission-mode bypassPermissions: under -p nothing can
            # approve a tool, so a tool that needs approval is refused. The
            # deny list is a second layer.
            "--disallowed-tools", *[one for one in DENIED_TOOLS
                                    if not (request.builtin_web and one in BUILTIN_WEB)
                                    and not (request.native_read is not None
                                             and one in NATIVE_READ)],
            # Its own reading tools denied every place the agents may not read.
            *[read_rule(one) for one in request.native_read or []],
            # And no MCP servers at all, since none are passed with it.
            #
            # Without this the client inherits whatever the *user* has
            # configured in their own Claude Code - document connectors, issue
            # trackers, anything. Those are tools this application never chose
            # to grant, cannot see, and does not sandbox, and the model
            # reaches for them: asked to set a reminder it answered in terms
            # of the connectors it had found, having tried to call the agent's
            # own scheduler as if it were one of them. What the agent may do
            # is decided by the agent's tool layer (sections 31, 107), and
            # that decision is worth nothing if another program's
            # configuration can add to it.
            "--strict-mcp-config",
            # Nothing kept of the call in the user's own ~/.claude: every
            # call of the application's was written there as a session of
            # theirs - thousands of them, outside the application's folders.
            # The application keeps its own record of every call (the run's
            # transcript, the model-call log).
            "--no-session-persistence",
        ]
        # The client writes a transcript of this run into its own session
        # store, and that store is what the assistant listener reads. Left
        # unnamed, every model call came back a moment later as activity at
        # the user's desk - the agent hearing its own voice. Naming the
        # session with a mark the listener recognises closes that loop, and
        # the mark travels on the id itself, so it still reads as ours in a
        # transcript found by a later process (section 18, as amended).
        if session_id:
            command += ["--session-id", session_id]
        # ITS OWN WEB TOOLS ARE NEVER ALLOWED HERE: not denied either, each
        # search and page is let through by the hook alone, when the agent's
        # checks pass (`ToolBridge.hook_settings`) - so a hook that cannot
        # run lets nothing through: under -p nothing else can approve it.
        web: List[str] = []
        if request.native_read is not None:
            web += list(NATIVE_READ)
        if request.builtin_web and getattr(bridge, "guard", None) is not None:
            # ITS OWN WEB TOOLS, CHECKED: the hooks before and after each
            # search and page, answered by this call's server - in a file
            # of its own when the call runs (`_settings_to_file`).
            command += ["--settings", json.dumps(bridge.hook_settings())]
        # The tools allowed by name - never the flag with none after it: it
        # takes the next word for its own, and an option after it is lost
        # (measured: the settings above, so no hook ran).
        allowed = [*(bridge.allowed() if bridge is not None else []), *web]
        if bridge is not None:
            # The agent's tools, natively: this call's server and nothing
            # else (--strict-mcp-config above), its tools allowed by name.
            command += ["--mcp-config", bridge.config()]
        if allowed:
            command += ["--allowedTools", *allowed]
        system = self._system(request, native=bridge is not None)
        if system:
            command += ["--append-system-prompt", system]
        return command

    def _unconfined_command(self, request: ModelRequest, model: str,
                            session_id: Optional[str], bridge: Any) -> List[str]:
        """WITHOUT THE SANDBOX (agent/work/unconfined.py): its own tools - all
        but those that reach anyone, arrange for later or publish - in its
        "auto" permission mode, with NOBODY to answer a permission prompt:
        whatever would need one is refused at once. Never the places listed
        (deny rules for its file tools; its hook, answered by the agent, for
        every command and file before it runs). It works from the work's
        folder. Otherwise as any call: no MCP server but the agent's, no
        session kept in the user's ~/.claude."""
        from ...agent.work.unconfined import CLAUDE_STILL_DENIED
        spec = request.unconfined or {}
        places = [str(one) for one in spec.get("places") or []]
        denied = list(CLAUDE_STILL_DENIED)
        for place in places:
            rule = read_rule(place)
            denied += [rule, rule.replace("Read(", "Edit(", 1),
                       rule.replace("Read(", "Write(", 1)]
        command = [
            self.executable() or CLI_NAME, "-p",
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--model", model,
            *(["--effort", request.reasoning_effort]
              if request.reasoning_effort in EFFORTS else []),
            "--permission-mode", "auto", "--permission-prompts", "none",
            "--disallowed-tools", *denied,
            "--strict-mcp-config", "--no-session-persistence",
        ]
        if spec.get("workdir"):
            command += ["--add-dir", str(spec["workdir"])]
        if session_id:
            command += ["--session-id", session_id]
        if bridge is not None:
            command += ["--settings", json.dumps(bridge.hook_settings(
                unconfined=True, web=bool(request.builtin_web and bridge.guard is not None)))]
            command += ["--mcp-config", bridge.config(), "--allowedTools", *bridge.allowed()]
        system = self._system(request, native=bridge is not None)
        if system:
            command += ["--append-system-prompt", system]
        return command

    def invoke(self, request: ModelRequest, model: str) -> ModelResponse:
        cli = self.executable()
        if not cli:
            verdict = self.check_availability(model)
            raise ModelInvocationError(verdict.reason or "Claude Code is unavailable",
                                       verdict)
        # Its own reading only where the places it is denied fit on the
        # command line (Windows ends one at 32,767 characters): past that it
        # is not offered - never offered with a place left out.
        if request.native_read is not None and sum(
                len(read_rule(one)) + 1 for one in request.native_read) > READ_RULES_CHARS:
            request = replace(request, native_read=None)
        # NEVER THE APP'S SEARCH BESIDE ITS OWN (the user, 2026-10-27): it
        # has search of its own, so web_search is not offered it - anywhere.
        # Its own is used where the agent's checks hold it (the hooks the
        # call's server answers); where they cannot, it has no search at all,
        # and the app's page readers alone.
        wanted = any(tool.name in WEB_TOOL_NAMES for tool in request.tools or [])
        request = replace(request, tools=[tool for tool in request.tools or []
                                          if tool.name != "web_search"])
        web = wanted and request.web_guard is not None and callable(request.tool_runner)
        if web:
            # Its own search in place of the agent's web tools.
            request = replace(request, tools=[tool for tool in request.tools
                                              if tool.name not in WEB_TOOL_NAMES])
            request = replace(request, system="\n\n".join(
                one for one in (request.system, BUILTIN_WEB_NOTE) if one))
        # On the request, not the provider: calls run at once (a round's two
        # searches) and each is its own.
        request = replace(request, builtin_web=web)
        if request.native_read is not None:
            request = replace(request, system="\n\n".join(
                one for one in (request.system, NATIVE_READ_NOTE) if one))
        # A server for the call when it has tools to serve - or its own web
        # tools to check: without it, no hook could ask.
        if (request.tools or web) and callable(request.tool_runner):
            from .mcp_bridge import ToolBridge
            with ToolBridge(request.tools, request.tool_runner,
                            guard=request.web_guard if web else None,
                            checks=_unconfined_checks(request)) as bridge:
                return self._invoke(request, model, cli, bridge)
        return self._invoke(request, model, cli, None)

    def _invoke(self, request: ModelRequest, model: str, cli: str,
                bridge: Any) -> ModelResponse:
        started = time.perf_counter()
        command = self._command(request, model, new_session_id(), bridge=bridge)
        # THE SYSTEM PROMPT IN A FILE, not on the command line: Windows ends a
        # command line at 32,767 characters, and experience learning's prompt
        # alone is past 26,000 - the call never ran ("The filename or
        # extension is too long"). Removed once the call is over.
        system, system_file = _system_to_file(command)
        settings_file = _settings_to_file(command)
        prompt = self._prompt(request)
        cwd = str(self.workspace) if self.workspace else None
        if request.unconfined and (request.unconfined or {}).get("workdir"):
            # Without the sandbox, its own tools work from the work's folder.
            cwd = str(request.unconfined["workdir"])
        # Exactly what the client is handed, for a debugger watching this
        # call - the command, the system prompt it reads from its file, the
        # prompt on stdin, where it runs. Nothing is copied when nobody is
        # watching.
        request.emit_debug("provider_request", {
            "provider": self.provider_id, "model": model,
            "command": command, "system": system, "input": prompt, "cwd": cwd,
        })
        stream = _Stream(request, bridge, self.timeout_seconds)
        env = dict(os.environ)
        # The agent's tools listed to the model at once, not looked up
        # through a tool of the client's own (a wasted round, and denied);
        # and a tool of the agent's not cut off by the client: the agent
        # runs it, may wait for the user in it, and ends it itself.
        env["ENABLE_TOOL_SEARCH"] = "false"
        env["MCP_TOOL_TIMEOUT"] = str(MCP_TOOL_TIMEOUT_MS)
        try:
            completed = children.run(
                command,
                input=prompt,
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                cwd=cwd, env=env,
                on_line=stream.line, watch=stream.watch,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if sys.platform.startswith("win") else 0,
            )
        except children.Ended as exc:
            if exc.reason == "cancelled":
                message = "the call was ended: the run was stopped"
                raise ModelInvocationError(message, ModelAvailability(
                    available=False, state=AvailabilityState.UNKNOWN_ERROR,
                    reason=message, failure_class=FailureClass.UNKNOWN)) from exc
            message = f"Claude Code {exc.reason}"
            raise ModelInvocationError(message, ModelAvailability(
                available=False, state=AvailabilityState.PROVIDER_UNAVAILABLE,
                reason=message, failure_class=FailureClass.TEMPORARY)) from exc
        except subprocess.TimeoutExpired as exc:   # pragma: no cover - no clock here now
            message = f"Claude Code did not answer within {int(self.timeout_seconds)}s"
            raise ModelInvocationError(message, ModelAvailability(
                available=False, state=AvailabilityState.PROVIDER_UNAVAILABLE,
                reason=message, failure_class=FailureClass.TEMPORARY)) from exc
        except OSError as exc:
            message = f"could not run {CLI_NAME}: {exc}"
            raise ModelInvocationError(message, ModelAvailability(
                available=False, state=AvailabilityState.PROVIDER_UNAVAILABLE,
                reason=message, failure_class=FailureClass.USER_ACTION_REQUIRED)) from exc
        finally:
            for made in (system_file, settings_file):
                if made is not None:
                    try:
                        made.unlink()
                    except OSError:
                        pass

        if completed.returncode != 0:
            # The envelope is on stdout even when the exit code is not zero,
            # and it carries the client's own sentence about what went wrong.
            # Read without it, the reason became the raw JSON - truncated
            # mid-field on its way to the log, so what the user saw of "you
            # have hit your session limit" was a wall of zeroed telemetry.
            verdict = self._translate(completed, self._envelope(completed.stdout))
            raise ModelInvocationError(verdict.reason or "Claude Code failed", verdict)
        try:
            payload = _result_of(completed.stdout)
        except ValueError as exc:
            message = "Claude Code returned output this application could not read"
            raise ModelInvocationError(message, ModelAvailability(
                available=False, state=AvailabilityState.UNKNOWN_ERROR,
                reason=message, failure_class=FailureClass.UNKNOWN)) from exc
        # Valid JSON is not necessarily the envelope: a bare string or list
        # parses without complaint and then fails on the first field read.
        if not isinstance(payload, dict):
            message = "Claude Code returned output this application could not read"
            raise ModelInvocationError(message, ModelAvailability(
                available=False, state=AvailabilityState.UNKNOWN_ERROR,
                reason=message, failure_class=FailureClass.UNKNOWN))
        if payload.get("is_error"):
            verdict = self._translate(completed, payload)
            raise ModelInvocationError(verdict.reason or "Claude Code failed", verdict)

        text = str(payload.get("result") or "")
        # Its tools were run during the call: none is left to run.
        tool_calls = [] if bridge is not None else self._tool_calls(text, request)
        usage = self._usage_of(payload)
        # The model that answered, by its own name: "opus" is whichever Opus
        # the client means by it today.
        answered = self._answered_by(payload) or model
        # Only what the tool actually reported (section 81).
        self._usage = UsageSnapshot(
            request_count=self._usage.request_count + 1,
            input_tokens=self._usage.input_tokens + usage.input_tokens,
            output_tokens=self._usage.output_tokens + usage.output_tokens,
            total_tokens=self._usage.total_tokens + usage.total_tokens,
            estimated_cost=(self._usage.estimated_cost or 0.0)
            + float(payload.get("total_cost_usd") or 0.0),
        )
        log.debug("claude code answered in %.0f ms", (time.perf_counter() - started) * 1000)
        return ModelResponse(
            text="" if tool_calls else text,
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=str(payload.get("stop_reason") or "end_turn"),
            # What goes back into the transcript as "what you said last turn".
            #
            # When the reply was a call, that is the call and nothing else. The
            # parser already discards anything the model wrote after it - but
            # this is the other half: left as the whole reply, a result the
            # model invented would be read back to it next turn as its own
            # words, immediately above the real result. Teaching it, turn by
            # turn, that writing results is what it does here.
            raw_content=self._as_written(text, tool_calls),
            model=answered,
            provider=self.provider_id,
            tool_exchanges=list(bridge.calls) if bridge is not None else [],
        )

    @staticmethod
    def _as_written(text: str, tool_calls: List[ToolCall]) -> str:
        """The assistant's turn as it should be remembered (local_tools.as_written)."""
        return as_written(text, tool_calls)

    @staticmethod
    def _usage_of(payload: Dict[str, Any]) -> ModelUsage:
        usage = payload.get("usage") or {}
        try:
            input_tokens = int(usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
            # Read FROM the cache is old input the model had already
            # processed; written TO it is new input on its way in. Adding
            # them was the first version and it said the opposite of the
            # truth: a call sending a thousand new tokens and re-reading
            # fourteen thousand old ones came out as "fifteen thousand
            # cached", which reads as though nothing new was sent.
            cached = int(usage.get("cache_read_input_tokens") or 0)
            written = int(usage.get("cache_creation_input_tokens") or 0)
        except (TypeError, ValueError):
            input_tokens = output_tokens = cached = written = 0
        try:
            # What the client says the call cost, at list price.
            cost = (float(payload["total_cost_usd"])
                    if payload.get("total_cost_usd") is not None else None)
        except (TypeError, ValueError):
            cost = None
        return ModelUsage(input_tokens=input_tokens,
                          output_tokens=output_tokens,
                          cached_input_tokens=cached,
                          cache_written_tokens=written,
                          cost_usd=cost)

    @staticmethod
    def _answered_by(payload: Dict[str, Any]) -> str:
        """The model that did the call's work - of those the client names in
        `modelUsage`, the one that cost the most."""
        named = payload.get("modelUsage")
        if not isinstance(named, dict) or not named:
            return ""
        def cost(item):
            try:
                return float((item[1] or {}).get("costUSD") or 0)
            except (TypeError, ValueError, AttributeError):
                return 0.0
        return str(max(named.items(), key=cost)[0])

    def _tool_calls(self, text: str, request: ModelRequest) -> List[ToolCall]:
        """A tool call, when the reply holds one: the first JSON object that
        names a tool this request offered, with the reason before it kept out
        and anything after it discarded (local_tools.leading_tool_call)."""
        return leading_tool_call(text, request, "claude_code")

    @staticmethod
    def _envelope(stdout: Optional[str]) -> Dict[str, Any]:
        """The client's JSON envelope, if that is what it printed - the
        stream's last "result" line, or the whole of its output."""
        try:
            payload = _result_of(stdout)
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _translate(completed: subprocess.CompletedProcess,
                   payload: Optional[Dict[str, Any]] = None) -> ModelAvailability:
        # The CLI reports some failures on stdout with a non-zero exit, so both
        # streams have to be read before calling the failure empty.
        payload = payload if isinstance(payload, dict) else {}
        # The envelope's own sentence first, then the error stream, then
        # anything printed plainly - but never the envelope itself, which is
        # telemetry and says nothing a reader can act on.
        printed = (completed.stdout or "").strip()
        detail = (str(payload.get("result") or "").strip()
                  or (completed.stderr or "").strip()
                  or ("" if payload or printed.startswith(("{", "[")) else printed)
                  ).strip()[:300]
        if not detail:
            # Anything rather than the envelope itself. Printed as the reason
            # it is a wall of zeroed telemetry, cut off mid-field by the
            # length limits between here and the log, and it says nothing a
            # reader can act on - not even that there was no answer.
            detail = (f"Claude Code exited with code {completed.returncode} "
                      f"and no message")
        lowered = detail.lower()
        # An expired sign-in is the one failure here the user can actually fix,
        # so it is worth recognising in the several shapes the client says it.
        #
        # "authenticat", not "authentication": the client says "Failed to
        # authenticate: OAuth session expired and could not be refreshed",
        # which the longer word does not match. That one sentence was filed
        # as an unknown error, so the prompt to sign in again - which exists
        # and waits for exactly this - was never shown, and every part of
        # the agent failed quietly for as long as nobody looked at the log.
        needs_sign_in = ("not logged in", "please run /login", "run /login",
                         "invalid api key", "unauthorized", "authenticat",
                         "oauth", "session expired", "sign in again",
                         "log in again", '"loggedin": false')
        if any(phrase in lowered for phrase in needs_sign_in):
            state = AvailabilityState.AUTHENTICATION_REQUIRED
            failure = FailureClass.USER_ACTION_REQUIRED
        elif ("rate" in lowered and "limit" in lowered) or _limit_reached(lowered):
            # "You've hit your session limit · resets 11:50pm
            # (Europe/Lisbon)" - which says "limit" without saying "rate",
            # and was therefore an unknown error: the one class of failure
            # that is certain to end by itself, filed as the one nobody knows
            # what to do about. It waits, and it says how long for.
            state = AvailabilityState.RATE_LIMITED
            failure = FailureClass.TEMPORARY
        elif "usage limit" in lowered or "quota" in lowered:
            state = AvailabilityState.QUOTA_EXHAUSTED
            failure = FailureClass.USER_ACTION_REQUIRED
        else:
            state = AvailabilityState.UNKNOWN_ERROR
            failure = FailureClass.UNKNOWN
        return ModelAvailability(
            available=False, state=state,
            reason=detail or f"{CLI_NAME} exited with code {completed.returncode}",
            failure_class=failure,
            # When it comes back, where a caller deciding whether to wait can
            # read it rather than parse the sentence again.
            rate_limit_status=(_resets_at(detail)
                               if state is AvailabilityState.RATE_LIMITED else None),
        )

    def usage(self) -> UsageSnapshot:
        return self._usage

    def capability_model(self) -> Dict[str, Any]:
        status = self._auth_status()
        model = super().capability_model()
        model.update({
            "runs_locally": True,
            "stores_no_credential": True,
            "signed_in_as": status.get("email"),
            "subscription": status.get("subscriptionType"),
            "note": "Answers come from Claude Code on this machine, using its own "
                    "sign-in and billed to its account. It is run with no tools, so "
                    "it cannot read or write anything on your behalf.",
        })
        return model


def _result_of(stdout: Optional[str]) -> Any:
    """The client's envelope: the last "result" line of its stream - or, a
    client answering with one JSON object, that object. ValueError when
    there is neither."""
    text = (stdout or "").strip()
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            return event
    return json.loads(text or "{}")


class _Stream:
    """One call's stream, watched: when it last said anything, what it has
    used so far, and whether it is to be ended - silent too long, working
    too long in all, or the run stopped."""

    def __init__(self, request: ModelRequest, bridge: Any, silent_seconds: float) -> None:
        self.request = request
        self.bridge = bridge
        self.silent_seconds = float(silent_seconds)
        self.last = time.monotonic()
        self.working = 0.0
        self.mark = time.monotonic()
        self.usage: Dict[str, Dict[str, int]] = {}

    def line(self, line: str) -> None:
        self.last = time.monotonic()
        if self.request.unconfined and '"tool_result"' in line:
            self._refusals(line)
        if '"usage"' not in line or not callable(self.request.on_usage):
            return
        try:
            event = json.loads(line)
        except ValueError:
            return
        message = event.get("message") if isinstance(event, dict) else None
        if event.get("type") != "assistant" or not isinstance(message, dict):
            return
        usage = message.get("usage") or {}
        self.usage[str(message.get("id") or len(self.usage))] = {
            key: int(usage.get(key) or 0) for key in
            ("input_tokens", "output_tokens", "cache_read_input_tokens",
             "cache_creation_input_tokens")}
        total = {key: sum(one[key] for one in self.usage.values())
                 for key in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                             "cache_creation_input_tokens")}
        try:
            self.request.on_usage(ModelUsage(
                input_tokens=total["input_tokens"], output_tokens=total["output_tokens"],
                cached_input_tokens=total["cache_read_input_tokens"],
                cache_written_tokens=total["cache_creation_input_tokens"]))
        except Exception:                    # pragma: no cover - the caller's count
            log.debug("could not report a call's usage so far", exc_info=True)

    def _refusals(self, line: str) -> None:
        """WITHOUT THE SANDBOX: each of its own tools refused for want of
        permission, told as it comes back (`ModelRequest.unconfined`)."""
        from ...agent.work.unconfined import is_denial
        told = (self.request.unconfined or {}).get("on_denied")
        if not callable(told):
            return
        try:
            event = json.loads(line)
        except ValueError:
            return
        message = event.get("message") if isinstance(event, dict) else None
        if not isinstance(message, dict) or event.get("type") != "user":
            return
        for part in message.get("content") or []:
            if not isinstance(part, dict) or part.get("type") != "tool_result" \
                    or not part.get("is_error"):
                continue
            content = part.get("content")
            text = content if isinstance(content, str) else " ".join(
                str(one.get("text") or "") for one in content or [] if isinstance(one, dict))
            if is_denial(text):
                try:
                    told("client", {"tool_use_id": part.get("tool_use_id")}, text[:400])
                except Exception:            # pragma: no cover - the caller's record
                    log.debug("could not tell a refusal", exc_info=True)

    def watch(self) -> Optional[str]:
        now = time.monotonic()
        busy = self.bridge is not None and self.bridge.busy
        if not busy:
            self.working += now - self.mark
        self.mark = now
        if callable(self.request.cancelled):
            try:
                if self.request.cancelled():
                    return "cancelled"
            except Exception:                # pragma: no cover
                pass
        if busy:
            self.last = now                  # a tool of the agent's is working
            return None
        heard = max(self.last, self.bridge.last_activity if self.bridge is not None else 0.0)
        if now - heard > self.silent_seconds:
            return f"was silent for {int(self.silent_seconds)}s"
        if self.working > MOST_WORKING_SECONDS:
            return f"worked for more than {int(MOST_WORKING_SECONDS // 60)} min"
        return None


class ClaudeDesktopProvider(ClaudeCodeProvider):
    """THE CLAUDE DESKTOP APPLICATION'S CLIENT: the Claude Code that comes
    with the desktop application, signed in with it. Found where the
    application keeps it (`CLIENTS["desktop"]`); never the command line's."""

    provider_id = "claude_desktop"
    display_name = "Claude desktop app"
    client = "desktop"
    SIGN_IN = "The Claude desktop application is not signed in: open it and sign in."

    def _chosen_path(self) -> str:
        # A program chosen by hand is the command line's (local_client_path).
        return ""
