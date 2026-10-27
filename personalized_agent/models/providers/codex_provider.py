"""Codex CLI as a local model provider (section 76.1).

Codex owns its sign-in.  This adapter never reads or stores that credential;
it asks the installed CLI for an answer in its non-interactive JSONL mode.

The CLI is always started ephemerally with a read-only sandbox. The agent
remains the authority that decides whether a proposed tool call may run through
its own sandbox.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...common import children
from ...common.languages import in_english
from ...common.config.schema import REASONING_EFFORTS
from ...version import APP_NAME, __version__
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
# How tools travel through a transcript is shared with Claude Code; the words
# they are offered in (TOOL_PROTOCOL below) are this client's own.
from .local_tools import as_written, describe_tools, leading_tool_call, tool_results
from .local_tools import TOOL_PROTOCOL as SHARED_TOOL_PROTOCOL
from .mcp_bridge import SERVER_NAME

log = logging.getLogger(__name__)

CLI_NAME = "codex"
DEFAULT_TIMEOUT_SECONDS = 300.0
STATUS_TIMEOUT_SECONDS = 20.0
STATUS_CACHE_SECONDS = 60.0
#: How long "Refresh models" waits for the client's whole catalog.
MODEL_REFRESH_TIMEOUT_SECONDS = 30.0
UNREACHABLE = "_probe_failed"
#: One listing of Codex's own tools at a time (`CodexProvider.confined`).
_PROBING = threading.Lock()

#: The picker's last resort, checked against https://learn.chatgpt.com/docs/models
#: on 2026-10-01. It is a list to choose from, not a statement of what the
#: account may use: the installed client's own catalog comes first, and
#: whether a model may be used is the client's answer (accepts_model_id).
MODELS: List[ModelDescriptor] = [
    ModelDescriptor("default", "Codex default model"),
    ModelDescriptor("gpt-6-astra", "GPT-6 Astra"),
    ModelDescriptor("gpt-6.1-sol", "GPT-6.1 Sol"),
    ModelDescriptor("gpt-6-sol", "GPT-6 Sol"),
    ModelDescriptor("gpt-6-luna", "GPT-6 Luna"),
    ModelDescriptor("gpt-5.6-sol", "GPT-5.6 Sol"),
    ModelDescriptor("gpt-5.6-terra", "GPT-5.6 Terra"),
    ModelDescriptor("gpt-5.6-luna", "GPT-5.6 Luna"),
    ModelDescriptor("gpt-5.5", "GPT-5.5"),
]

#: CODEX'S OWN TOOLS, OFF for every call the agent makes: its shell, its
#: code mode, its web search, the computer and browser it can drive, the
#: plugins, apps, hooks and skills the user installed for their own use.
#: The agent's calls carry web pages and other people's words; what they
#: may do is the agent's tools, run under its own guard - never a tool of
#: the user's Codex, which nothing here confines. A name an older or newer
#: Codex does not know is only warned about by it.
OWN_TOOLS_OFF = ("shell_tool", "unified_exec", "code_mode", "code_mode_host",
                 "computer_use", "browser_use", "browser_use_external", "apps",
                 "plugins", "remote_plugin", "hooks", "goals", "memories",
                 "image_generation", "view_image", "skill_search",
                 "skill_mcp_dependency_install", "tool_suggest",
                 # Agents of its own, started mid-answer: outside the agent's
                 # sight, its guard and its count of what a run spent.
                 "multi_agent", "multi_agent_v2")

#: Off for every call, the agent's tools served or not: what Codex would
#: run by itself - its shell, the user's computer and browser, plugins.
#: CODE MODE is what is left on when the agent's tools are served to it
#: (`NATIVE_TOOLS`): Codex calls every tool through it, an MCP server's too.
CODE_MODE = ("code_mode", "code_mode_host")

#: How the agent's tools are offered when they are served to Codex natively
#: (`ModelRequest.tool_runner`, over MCP - see mcp_bridge): as the tools of
#: one MCP server, "pa", and nothing else.
NATIVE_TOOLS = (
    "Your tools for this conversation are served to you directly, as the tools "
    "of the MCP server named `pa`: call them as tools. Each named in your "
    "instructions as `name` is that server's tool `name` (run_python is pa's "
    "run_python, and so on). They are really run - the agent executes each call "
    "in its own sandbox and gives you back what it returned; reading and writing "
    "files is done with them. The program you are running inside has no other "
    "tools for you: its own shell and file editing are switched off, so do not "
    "try them. When you have what you need, answer as you were asked."
)

#: The agent's own tools for the web, by every name they go by. With the
#: model's own search (`ModelRequest.builtin_web`) Codex's web search is
#: offered BESIDE them, from OpenAI's CACHED INDEX only (`web_search=
#: "cached"`): it runs on OpenAI's servers, where no hook of Codex's and no
#: check of the agent's can see it (its hooks guide: hosted tools do not
#: take the hook path), and live it could open any address the model made
#: up - with what it read inside. Cached, it opens no live page at all; the
#: agent's own tools, held to the agent's checks (runtime/web_guard.py),
#: read the live web.
WEB_TOOL_NAMES = ("web_search", "fetch_page", "fetch_url")
WEB_NOTE = (
    "YOUR OWN WEB SEARCH reads an index OpenAI keeps, not the live web: use it "
    "to find things out. To read a page as it is now, use the agent's own tools - "
    "web_search to find its address, then fetch_url or fetch_page to read it; an "
    "address you make up yourself is opened only where nothing you read could "
    "travel inside it. Public pages only: never sign in to anything, never post "
    "or send anything."
)

#: WHAT CODEX MAY OFFER OF ITS OWN to a call the agent makes, once its own
#: tools are off (`_own_tools_off`) - measured, not assumed: the MCP
#: resource tools, which reach only the agent's own server, and its patch
#: tool, which its read-only sandbox refuses. Anything else - a tool a later
#: Codex adds - and its reading is no longer known to be confined
#: (`confined`): the turning off is by name, and a name not on the list
#: stays on.
OWN_TOOLS_ALLOWED = frozenset({"list_mcp_resources", "list_mcp_resource_templates",
                               "read_mcp_resource", "apply_patch",
                               # The time, read off the clock: offered by some
                               # models and not others.
                               "clock__curr_time"})
#: The question that lists them: run in Codex's code mode, it names every
#: tool the mode can call (`ALL_TOOLS`), and reports through an error - code
#: mode has no console.
PROBE_ASK = "\n".join([
    "Debugging your runtime, nothing else: in your code mode (the exec tool), run exactly "
    "this JavaScript and show me its full output verbatim. Do not call any other tool.",
    "const all = ALL_TOOLS;",
    "const names = (Array.isArray(all) ? all : Object.keys(all)).map(t => typeof t === "
    "'string' ? t : (t.name || ''));",
    "throw new Error('TOOLNAMES ' + JSON.stringify(names));",
])

#: The longest instructions given as developer_instructions on the command
#: line - Windows holds 32,767 characters in all; longer, they go in the
#: prompt.
DEVELOPER_INSTRUCTIONS_MOST = 24_000

#: How long the agent's own MCP server is waited for at the start of a call.
MCP_STARTUP_SECONDS = 30
#: A tool of the agent's is not cut off by Codex: the agent runs it, may
#: wait for the user in it (an install to approve), and ends it itself.
MCP_TOOL_SECONDS = 24 * 3600

#: The agent's tools, offered as text: the same words for Claude Code and
#: Codex (local_tools.TOOL_PROTOCOL).
TOOL_PROTOCOL = SHARED_TOOL_PROTOCOL


class CodexProvider(ModelProvider):
    provider_id = "codex"
    display_name = "Codex CLI"
    kind = "client"

    def __init__(self, credentials: Any = None, workspace: Any = None,
                 timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.credentials = credentials
        self.workspace = workspace
        self.timeout_seconds = timeout_seconds
        self._usage = UsageSnapshot()
        #: This Codex refused developer_instructions: the instructions go in
        #: the prompt from then on (`_instructions_apart`).
        self._developer_refused = False
        self._status_cache: Optional[Dict[str, Any]] = None
        self._status_checked_at = 0.0
        #: The catalog the client gave on "Refresh models", and the cache
        #: file's stamp at that moment: it stands until Codex rewrites its
        #: cache, which is then newer than anything asked for here.
        self._refreshed_models: Optional[List[ModelDescriptor]] = None
        self._model_cache_stamp: Optional[int] = None
        #: The last catalog that could be read whole. Codex rewrites its
        #: cache in place, and a read caught halfway is not a reason for the
        #: picker - and the reasoning levels it offers - to fall back to the
        #: bundled list for one refresh of the Settings page.
        self._cached_models: Optional[List[ModelDescriptor]] = None

    def executable(self) -> Optional[str]:
        # Including a Codex installed after this application started, which
        # this process's own PATH does not show (platform_layer/programs.py).
        from ...platform_layer.programs import find_program
        return find_program(CLI_NAME)

    @staticmethod
    def _model_cache_path() -> Path:
        home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        return home / "models_cache.json"

    def _cache_stamp(self) -> Optional[int]:
        try:
            return self._model_cache_path().stat().st_mtime_ns
        except OSError:
            return None

    def _ask_client(self, cli: Optional[str]) -> Dict[str, Any]:
        if not cli:
            return {}
        try:
            completed = children.run(
                [cli, "login", "status"], capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=STATUS_TIMEOUT_SECONDS,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if sys.platform.startswith("win") else 0,
            )
        except subprocess.TimeoutExpired:
            return {UNREACHABLE: f"{cli} did not answer within {STATUS_TIMEOUT_SECONDS:.0f}s"}
        except (OSError, subprocess.SubprocessError) as exc:
            return {UNREACHABLE: f"{cli} could not be run ({exc})"}

        detail = ((completed.stdout or "").strip() or (completed.stderr or "").strip())[:300]
        lowered = detail.lower()
        if completed.returncode == 0 and not any(
                phrase in lowered for phrase in ("not logged", "not authenticated", "signed out")):
            return {"logged_in": True, "detail": detail}
        if any(phrase in lowered for phrase in ("not logged", "not authenticated", "signed out",
                                                 "login required")):
            return {"logged_in": False, "detail": detail}
        return {UNREACHABLE: detail or f"{cli} exited with code {completed.returncode}"}

    def _auth_status(self, force: bool = False) -> Dict[str, Any]:
        now = time.time()
        if (not force and self._status_cache is not None
                and now - self._status_checked_at < STATUS_CACHE_SECONDS):
            return self._status_cache
        self._status_cache = self._ask_client(self.executable())
        self._status_checked_at = now
        return self._status_cache

    def list_models(self) -> List[ModelDescriptor]:
        # Read only model metadata, never credentials. Settings refreshes must
        # not start a CLI process or wait on network discovery. Re-read so a
        # catalog refreshed by Codex is reflected on the next Settings refresh.
        if (self._refreshed_models is not None
                and self._cache_stamp() == self._model_cache_stamp):
            return list(self._refreshed_models)
        try:
            payload = json.loads(self._model_cache_path().read_text(encoding="utf-8"))
            rows = payload.get("models") if isinstance(payload, dict) else None
            if isinstance(rows, list):
                models = [MODELS[0]]
                seen = {"default"}
                for row in rows:
                    if not isinstance(row, dict) or row.get("visibility") != "list":
                        continue
                    model_id = row.get("slug")
                    if not isinstance(model_id, str) or not model_id.strip() or model_id in seen:
                        continue
                    models.append(self._descriptor(model_id, row.get("display_name"), row))
                    seen.add(model_id)
                if len(models) > 1:
                    self._cached_models = models
                    return list(models)
        except (OSError, ValueError):
            pass  # Missing, partially written, or incompatible client cache.
        return list(self._cached_models or self._refreshed_models or MODELS)

    def accepts_model_id(self, model: str) -> bool:
        """Whether an id may be sent to Codex.

        The catalog here is a cache of the client's, and the client learns of
        a new model before its cache is rewritten. Refusing an OpenAI-style id
        because a stale picker list lacks it denied the user a model their
        account had - so any such id is sent, and Codex answers for it. What
        is still refused is the obvious mistake: another provider's model, or
        something that is not a model id at all.
        """
        if model == "default" or re.fullmatch(
                r"(?:gpt-[a-z0-9][a-z0-9._-]*|o[0-9]+(?:-[a-z0-9._-]+)?)", model):
            return True
        return super().accepts_model_id(model)

    @staticmethod
    def _descriptor(model_id: str, name: Any, row: Dict[str, Any]) -> ModelDescriptor:
        """One catalog row, from the cache file or from the app-server.

        The two spell the same things differently - the cache in snake case,
        the server in camel case - and the levels a model takes come as
        objects or as bare words. Read both ways here, so a model refreshed
        from the server offers the same levels as one read from the cache.
        """
        levels = row.get("supportedReasoningEfforts", row.get("supported_reasoning_levels"))
        efforts: List[str] = []
        if isinstance(levels, list):
            for level in levels:
                effort = (level.get("reasoningEffort", level.get("effort"))
                          if isinstance(level, dict) else level)
                if effort in REASONING_EFFORTS and effort not in efforts:
                    efforts.append(effort)
        default = row.get("defaultReasoningEffort", row.get("default_reasoning_level"))
        return ModelDescriptor(
            model_id, name if isinstance(name, str) and name else model_id,
            reasoning_efforts=efforts,
            default_reasoning_effort=default if default in REASONING_EFFORTS else None)

    def reasoning_options(self, model: str) -> Dict[str, Any]:
        """The efforts this model may be asked for, "default" first.

        A catalog that does not say - an older one, or the "default" alias,
        which is whatever model the user's Codex configuration names - is
        offered the whole vocabulary, and Codex itself refuses a level its
        model does not take.
        """
        descriptor = next((m for m in self.list_models() if m.id == model), None)
        efforts = descriptor.reasoning_efforts if descriptor else []
        return {"efforts": ["default", *(efforts or REASONING_EFFORTS)],
                "model_default": descriptor.default_reasoning_effort if descriptor else None}

    def refresh_models(self) -> List[ModelDescriptor]:
        """Ask the installed client for its catalog, now - and only when asked.

        Settings reads the client's cache file and nothing else: a page that
        refreshes on a timer must not start a process each time. This is the
        "Refresh models" button. It starts ``codex app-server`` read-only,
        never asking for approval, initializes it, pages through
        ``model/list`` and stops it - no inference turn is ever started, and
        Codex owns the sign-in and its own cache throughout.

        The new list is published only once every page has arrived. A
        failure part way - a timeout, an error, an exit, a list with nothing
        in it - leaves the last good catalog exactly as it was.
        """
        cli = self.executable()
        if not cli:
            raise RuntimeError("Codex is not installed on this machine. Install "
                               "Codex to refresh its models.")
        # Through the children wrapper, so a refresh in flight when the agent
        # quits ends with it rather than running on with nobody reading it.
        process = children.popen(
            [cli, "app-server", "--listen", "stdio://",
             "-c", 'sandbox_mode="read-only"', "-c", 'approval_policy="never"'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            cwd=str(self.workspace) if self.workspace else None,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if sys.platform.startswith("win") else 0,
        )
        replies: "queue.Queue[Optional[str]]" = queue.Queue()

        def read_replies() -> None:
            try:
                for line in process.stdout:
                    replies.put(line)
            except (OSError, ValueError):
                pass                       # the pipe closed under the reader
            finally:
                replies.put(None)

        reader = threading.Thread(target=read_replies, name="codex-model-list",
                                  daemon=True)
        reader.start()
        deadline = time.monotonic() + MODEL_REFRESH_TIMEOUT_SECONDS

        def send(message: Dict[str, Any]) -> None:
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()

        def ask(request_id: int, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
            send({"id": request_id, "method": method, "params": params})
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Codex took too long to list its models. Try again.")
                try:
                    line = replies.get(timeout=remaining)
                except queue.Empty:
                    raise RuntimeError("Codex took too long to list its models. "
                                       "Try again.") from None
                if line is None:
                    raise RuntimeError("Codex stopped before listing its models. Check "
                                       "your Codex installation and sign-in.")
                try:
                    reply = json.loads(line)
                except ValueError:
                    continue                # a diagnostic line, not a reply
                if not isinstance(reply, dict) or reply.get("id") != request_id:
                    continue                # a notification, or another reply
                if "error" in reply:
                    raise RuntimeError("Codex could not list its models. Check your "
                                       "Codex installation and sign-in.")
                result = reply.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError("Codex answered the model list with something "
                                       "this application could not read.")
                return result

        try:
            ask(1, "initialize", {"clientInfo": {
                "name": "personalized_agent", "title": APP_NAME,
                "version": __version__}})
            send({"method": "initialized", "params": {}})
            models = [MODELS[0]]
            seen = {"default"}
            cursors: set = set()
            cursor: Optional[str] = None
            request_id = 2
            while True:
                params: Dict[str, Any] = {"limit": 100, "includeHidden": False}
                if cursor is not None:
                    params["cursor"] = cursor
                result = ask(request_id, "model/list", params)
                rows = result.get("data")
                if not isinstance(rows, list):
                    raise RuntimeError("Codex answered the model list with something "
                                       "this application could not read.")
                for row in rows:
                    if not isinstance(row, dict) or row.get("hidden"):
                        continue
                    model_id = row.get("model") or row.get("id")
                    if not isinstance(model_id, str) or not model_id.strip() \
                            or model_id in seen:
                        continue
                    models.append(self._descriptor(model_id, row.get("displayName"), row))
                    seen.add(model_id)
                cursor = result.get("nextCursor")
                if cursor is None:
                    break
                # A cursor seen before would page for ever.
                if not isinstance(cursor, str) or not cursor or cursor in cursors:
                    raise RuntimeError("Codex paged its model list in a way this "
                                       "application could not follow.")
                cursors.add(cursor)
                request_id += 1
            if len(models) == 1:
                raise RuntimeError("Codex listed no models to choose from. Check "
                                   "your Codex sign-in.")
            self._model_cache_stamp = self._cache_stamp()
            self._refreshed_models = models
            self._cached_models = models
            return list(models)
        except OSError as exc:
            raise RuntimeError("Could not talk to Codex to refresh its models.") from exc
        finally:
            for stream in (process.stdin,):
                try:
                    stream.close()
                except OSError:
                    pass
            if process.poll() is None:
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    children.end_tree(process)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:  # pragma: no cover
                        log.warning("codex app-server did not stop")
            reader.join(timeout=2)
            try:
                process.stdout.close()
            except OSError:
                pass

    def get_auth_methods(self) -> List[AuthMethod]:
        return [AuthMethod.NONE]

    def get_auth_status(self) -> AuthStatus:
        cli = self.executable()
        if not cli:
            return AuthStatus(CredentialState.NOT_CONFIGURED, AuthMethod.NONE,
                              detail="Codex is not installed on this machine")
        status = self._auth_status()
        if status.get("logged_in"):
            # Said where it is chosen: a Codex found not confined is not
            # used where reading must stay sealed - and why (`confined`, as
            # last found: nothing is asked of it here).
            unconfined = sorted({said[1] for said in (getattr(self, "_confined", None)
                                                       or {}).values() if not said[0]})
            return AuthStatus(CredentialState.AUTHENTICATED, AuthMethod.NONE,
                              detail="signed in to Codex; this application stores no credential"
                              + ("" if not unconfined else
                                 " - not used for the Work debugger's runs: "
                                 + "; ".join(unconfined)))
        if status.get(UNREACHABLE):
            return AuthStatus(CredentialState.ERROR, AuthMethod.NONE,
                              detail="could not ask Codex whether it is signed in: "
                                     f"{status[UNREACHABLE]}. It will still be tried.")
        return AuthStatus(CredentialState.AUTHENTICATION_REQUIRED, AuthMethod.NONE,
                          detail="Codex is not signed in. Run `codex login`.")

    def authenticate(self, config: Dict[str, Any]) -> AuthStatus:
        return self.get_auth_status()

    def logout(self) -> AuthStatus:
        return AuthStatus(CredentialState.NOT_CONFIGURED, AuthMethod.NONE,
                          detail="sign out with `codex logout`")

    def check_availability(self, model: str) -> ModelAvailability:
        now = datetime.now(timezone.utc)
        if not self.executable():
            return ModelAvailability(
                available=False, state=AvailabilityState.PROVIDER_UNAVAILABLE,
                reason="Codex is not installed on this machine", checked_at=now,
                failure_class=FailureClass.USER_ACTION_REQUIRED,
            )
        status = self._auth_status()
        if status.get(UNREACHABLE):
            return ModelAvailability(
                available=True, state=AvailabilityState.AVAILABLE,
                authentication_valid=False, checked_at=now,
                reason=f"the Codex sign-in state could not be checked: {status[UNREACHABLE]}",
                failure_class=FailureClass.TEMPORARY,
            )
        if not status.get("logged_in"):
            return ModelAvailability(
                available=False, state=AvailabilityState.AUTHENTICATION_REQUIRED,
                reason="Codex is not signed in. Run `codex login`.", checked_at=now,
                failure_class=FailureClass.USER_ACTION_REQUIRED,
            )
        return ModelAvailability(available=True, state=AvailabilityState.AVAILABLE,
                                 authentication_valid=True, checked_at=now,
                                 failure_class=FailureClass.TEMPORARY)

    def _instructions_apart(self, system: str) -> bool:
        """Whether the application's instructions go to Codex as its own
        DEVELOPER INSTRUCTIONS (-c developer_instructions) rather than in the
        prompt as text (the user, 2026-10-27, PR #3) - as Claude Code is
        given them, as its system prompt. Not through a .cmd or .bat (npm's
        on Windows): cmd.exe reads its line again, and a % or a ^ in the
        words would be changed. Not past what a command line holds. Not
        once this Codex has refused the setting (`_invoke`)."""
        if not system or getattr(self, "_developer_refused", False):
            return False
        program = str(self.executable() or "").lower()
        if program.endswith((".cmd", ".bat")):
            return False
        return len(system) <= DEVELOPER_INSTRUCTIONS_MOST

    def _prompt(self, request: ModelRequest, native: bool = False) -> str:
        parts: List[str] = []
        system = self._system(request, native=native)
        if system and not self._instructions_apart(system):
            parts.append(f"System instructions:\n{system}")
        for message in request.messages:
            role = str(message.get("role", "user"))
            content = message.get("content")
            if isinstance(content, list):
                # A tool's result is labelled as one, not passed off as the
                # user pasting JSON (local_tools.tool_results).
                results = tool_results(content)
                if results is not None:
                    parts.append(results)
                    continue
                # Text, a picture said where it was - the pictures themselves
                # are given with --image (`_pictures`).
                from ..vision import text_of
                content = text_of(content)
            speaker = {"user": "User", "assistant": "Assistant"}.get(role, role.title())
            parts.append(f"{speaker}: {content}")
        from ..vision import pictures_in
        if pictures_in(request.messages):
            parts.append(f"(The latest pictures in this conversation - at most "
                         f"{self.MOST_PICTURES} - are attached to this message, in order.)")
        parts.append("Assistant:")
        return "\n\n".join(parts)

    def _system(self, request: ModelRequest, native: bool = False) -> str:
        pieces = [request.system] if request.system else []
        if request.tools and native:
            pieces.append(NATIVE_TOOLS)
        elif request.tools:
            # Each tool with its whole argument schema (local_tools.describe_tools).
            pieces.append(TOOL_PROTOCOL.format(tools=describe_tools(request.tools)))
        return "\n\n".join(piece for piece in pieces if piece)

    def _command(self, request: ModelRequest, model: str,
                 pictures: Sequence[str] = (), bridge: Any = None) -> List[str]:
        command = [
            self.executable() or CLI_NAME,
            "exec", "--json", "--ephemeral", "--skip-git-repo-check",
            "--sandbox", "read-only",
        ]
        if model == "default":
            # The user's configuration is not loaded (`_own_tools_off`); the
            # model it names is still the one "default" means.
            model = self._configured_model() or model
        if model != "default":
            command.extend(["--model", model])
        effort = request.reasoning_effort
        if effort and effort != "default":
            # Refused here rather than by Codex part way into a turn: a level
            # the model does not take is a setting to correct, and saying so
            # before starting costs nothing.
            if effort not in self.reasoning_options(model)["efforts"]:
                raise ValueError(f"The Codex model {model!r} does not take the reasoning "
                                 f"effort {effort!r}. Choose another in Settings > Models.")
            command.extend(["-c", f"model_reasoning_effort={json.dumps(effort)}"])
        elif self._configured("model_reasoning_effort") in REASONING_EFFORTS:
            command.extend(["-c", "model_reasoning_effort="
                            + json.dumps(self._configured("model_reasoning_effort"))])
        # THE PICTURES IN THE CONVERSATION, given as pictures: each written
        # to a file of its own for this call (`_pictures`), the latest few.
        for picture in pictures:
            command.extend(["--image", picture])
        # After the effort is known to be one the model takes: a setting to
        # correct is refused before anything is started, even Codex's help.
        command.extend(self._own_tools_off(native=bridge is not None,
                                           web=bool(request.builtin_web)))
        if bridge is not None:
            # The agent's tools, natively: this call's server and no other
            # (the user's own are not loaded - `_own_tools_off`).
            command.extend([
                "-c", f"mcp_servers.{SERVER_NAME}.url={json.dumps(bridge.url)}",
                "-c", f"mcp_servers.{SERVER_NAME}.startup_timeout_sec={MCP_STARTUP_SECONDS}",
                "-c", f"mcp_servers.{SERVER_NAME}.tool_timeout_sec={MCP_TOOL_SECONDS}",
                # Called without asking: no one can answer Codex's approval
                # in a call, and what each of these tools may do is decided
                # by the agent's own guard, which runs it.
                "-c", f'mcp_servers.{SERVER_NAME}.default_tools_approval_mode="approve"'])
        system = self._system(request, native=bridge is not None)
        if self._instructions_apart(system):
            command.extend(["-c", "developer_instructions="
                            + json.dumps(system, ensure_ascii=False)])
        command.append("-")
        return command

    def _own_tools_off(self, native: bool = False, web: bool = False) -> List[str]:
        """WHAT CODEX MAY USE OF ITS OWN: what the agent can still hold to
        its permissions, and nothing else (`OWN_TOOLS_OFF`).

        The user's configuration is not loaded - its MCP servers, plugins,
        hooks and rules, which the agent never granted - with the sign-in
        still theirs; a Codex too old to skip it has its MCP servers emptied
        instead. Its web search is its own when the settings say the
        model's (`web`) - from OpenAI's cached index only (`WEB_TOOL_NAMES`). Its code mode stays on when
        the agent's tools are served to it (`native`): Codex calls every
        tool through it. Its shell, file editing and picture viewing stay
        off - Codex's sandbox cannot be kept out of the places the agents
        may not read (sign-ins, keys, the application's state)."""
        flags: List[str] = []
        if self._takes("--ignore-user-config"):
            flags.append("--ignore-user-config")
        else:
            flags.extend(["-c", "mcp_servers={}"])
        if self._takes("--ignore-rules"):
            flags.append("--ignore-rules")
        flags.extend(["-c", f'web_search="{"cached" if web else "disabled"}"'])
        # No agent of its own below this one: its multi-agent tools stay in
        # code mode's list whatever the feature says, and a helper started
        # there works outside the agent's sight, guard and count.
        flags.extend(["-c", "agents.max_depth=0",
                      "-c", "agents.max_concurrent_threads_per_session=1"])
        for name in OWN_TOOLS_OFF:
            if native and name in CODE_MODE:
                continue
            flags.extend(["-c", f"features.{name}=false"])
        return flags

    def confined(self, model: str = "default") -> Tuple[bool, str]:
        """WHETHER THIS CODEX'S READING IS CONFINED to the agent's own tools,
        as the agent starts it, for this model: (yes or no, and why not).
        Asked of the installed program once per program file and version and
        per model - which tools a model is given differs - its code mode made
        to list the tools it can call (`PROBE_ASK`), and kept for this
        process. Only what `OWN_TOOLS_ALLOWED` and the agent's own server
        name may be there. Not asked, or no list that can be read: not
        confined. A run whose folders are kept apart uses Codex only when it
        is (models/manager.READS_UNCONFINED)."""
        cli = self.executable()
        if not cli:
            return False, "Codex is not installed"
        try:
            key = (cli, os.stat(cli).st_mtime_ns, model)
        except OSError:
            key = (cli, 0, model)
        known = getattr(self, "_confined", None) or {}
        if key in known:
            return known[key]
        with _PROBING:
            known = getattr(self, "_confined", None) or {}
            if key in known:
                return known[key]
            names = self._probe_tools(model)
            if names is None:
                said = (False, "what this Codex offers of its own could not be listed")
            else:
                extra = sorted(name for name in names
                               if name not in OWN_TOOLS_ALLOWED
                               and not name.startswith(f"mcp__{SERVER_NAME}__"))
                said = (not extra, "" if not extra else
                        "this Codex offers tools of its own the agent cannot hold to its "
                        "permissions: " + ", ".join(extra))
            if not said[0]:
                log.warning("Codex is not used where reading must stay sealed: %s", said[1])
            self._confined = {**known, key: said}
            return said

    def _probe_tools(self, model: str = "default") -> Optional[List[str]]:
        """The names of every tool Codex's code mode can call, as the agent
        starts it with its own tools served - or None, when no list came."""
        from ..base import ToolSpec
        from .mcp_bridge import ToolBridge
        tools = [ToolSpec("probe", "Does nothing.", {"type": "object", "properties": {}})]
        request = ModelRequest(messages=[{"role": "user", "content": PROBE_ASK}],
                               tools=tools, tool_runner=lambda name, arguments: {"ok": True},
                               task="diagnostic", language=in_english("the tool names"))
        try:
            with ToolBridge(tools, request.tool_runner) as bridge:
                command = self._command(request, model, bridge=bridge)
                completed = children.run(
                    command, input=self._prompt(request, native=True), capture_output=True,
                    text=True, encoding="utf-8", errors="replace", timeout=240,
                    cwd=str(self.workspace) if self.workspace else None,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    if sys.platform.startswith("win") else 0)
        except Exception:                          # pragma: no cover - not listed
            log.info("could not list what Codex offers of its own", exc_info=True)
            return None
        found = re.search(r"TOOLNAMES (\[[^\]]*\])",
                          (completed.stdout or "").replace('\\"', '"'))
        if not found:
            return None
        try:
            names = json.loads(found.group(1))
        except ValueError:
            return None
        return [str(one) for one in names if str(one)] if isinstance(names, list) else None

    def _takes(self, flag: str) -> bool:
        """Whether the installed Codex knows this option of `codex exec` -
        asked once per program file (`codex exec --help`)."""
        cli = self.executable()
        if not cli:
            return False
        try:
            key = (cli, os.stat(cli).st_mtime_ns)
        except OSError:
            key = (cli, 0)
        known = getattr(self, "_help", None)
        if not known or known[0] != key:
            try:
                done = children.run(
                    [cli, "exec", "--help"], capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=STATUS_TIMEOUT_SECONDS,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    if sys.platform.startswith("win") else 0)
                said = (done.stdout or "") + (done.stderr or "")
            except (OSError, subprocess.SubprocessError):
                said = ""
            known = self._help = (key, said)
        return flag in known[1]

    @staticmethod
    def _configured(key: str) -> Optional[str]:
        """One value of the user's Codex configuration - the model it names,
        or how hard it thinks - read from it, nothing else of it used: what
        "default" has always meant."""
        try:
            import tomllib
        except ImportError:                  # pragma: no cover - Python 3.10
            return None
        home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        try:
            with open(home / "config.toml", "rb") as handle:
                value = tomllib.load(handle).get(key)
        except (OSError, ValueError):
            return None
        return value.strip() if isinstance(value, str) and value.strip() else None

    def _configured_model(self) -> Optional[str]:
        return self._configured("model")

    #: Pictures given to one call, at most: the latest.
    MOST_PICTURES = 4

    def _pictures(self, request: ModelRequest) -> List[str]:
        """The latest pictures in the conversation, each written to a file in
        a folder of this call's own - removed after it (`_forget_pictures`)."""
        import base64
        import tempfile
        from ..vision import pictures_in
        found = pictures_in(request.messages)[-self.MOST_PICTURES:]
        if not found:
            return []
        folder = tempfile.mkdtemp(prefix="pa-codex-pictures-")
        paths = []
        for n, one in enumerate(found):
            suffix = ".jpg" if "jpeg" in one["media_type"] else ".png"
            path = os.path.join(folder, f"picture-{n + 1}{suffix}")
            with open(path, "wb") as handle:
                handle.write(base64.b64decode(one["data"]))
            paths.append(path)
        return paths

    @staticmethod
    def _forget_pictures(paths: List[str]) -> None:
        import shutil
        if paths:
            shutil.rmtree(os.path.dirname(paths[0]), ignore_errors=True)

    @staticmethod
    def _tool_calls(text: str, request: ModelRequest) -> List[ToolCall]:
        """A tool call, when the reply holds one.

        It used to be a call only when the whole reply was one JSON object,
        so a call followed by anything - most often the model writing the
        result it expected - was shown to the user as the answer, raw JSON
        and invented result together. Read the way Claude Code's replies
        are (local_tools.leading_tool_call): the first object naming an
        offered tool is the call, and what follows it is dropped.
        """
        return leading_tool_call(text, request, "codex")

    @staticmethod
    def _parse_events(output: str) -> tuple[str, ModelUsage, str]:
        text = ""
        usage = ModelUsage()
        stop_reason = "end_turn"
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            item = event.get("item")
            if event.get("type") == "item.completed" and isinstance(item, dict):
                if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                    text = item["text"]
            if event.get("type") == "turn.completed":
                raw_usage = event.get("usage") or {}
                if isinstance(raw_usage, dict):
                    # Its input counts the part read from the cache too.
                    whole = int(raw_usage.get("input_tokens") or 0)
                    cached = min(whole, int(raw_usage.get("cached_input_tokens") or 0))
                    usage = ModelUsage(
                        input_tokens=whole - cached,
                        output_tokens=int(raw_usage.get("output_tokens") or 0),
                        cached_input_tokens=cached,
                    )
                stop_reason = str(event.get("status") or stop_reason)
        return text, usage, stop_reason

    @staticmethod
    def _translate(completed: subprocess.CompletedProcess) -> ModelAvailability:
        detail = ((completed.stderr or "").strip() or (completed.stdout or "").strip())[:300]
        lowered = detail.lower()
        if any(phrase in lowered for phrase in ("not logged", "not authenticated", "login")):
            state, failure = AvailabilityState.AUTHENTICATION_REQUIRED, FailureClass.USER_ACTION_REQUIRED
        elif "rate" in lowered and "limit" in lowered:
            state, failure = AvailabilityState.RATE_LIMITED, FailureClass.TEMPORARY
        elif "quota" in lowered or "usage limit" in lowered:
            state, failure = AvailabilityState.QUOTA_EXHAUSTED, FailureClass.USER_ACTION_REQUIRED
        else:
            state, failure = AvailabilityState.UNKNOWN_ERROR, FailureClass.UNKNOWN
        return ModelAvailability(available=False, state=state,
                                 reason=detail or f"Codex exited with code {completed.returncode}",
                                 failure_class=failure)

    def invoke(self, request: ModelRequest, model: str) -> ModelResponse:
        cli = self.executable()
        if not cli:
            verdict = self.check_availability(model)
            raise ModelInvocationError(verdict.reason or "Codex is unavailable", verdict)
        web = bool(request.builtin_web) and any(
            tool.name in WEB_TOOL_NAMES for tool in request.tools or [])
        if web:
            # Its own search beside the agent's web tools - cached only
            # (`WEB_TOOL_NAMES`; Settings > Web search: the model's own).
            request = replace(request, system="\n\n".join(
                one for one in (request.system, WEB_NOTE) if one))
        request = replace(request, builtin_web=web)
        if request.tools and callable(request.tool_runner):
            from .mcp_bridge import ToolBridge
            with ToolBridge(request.tools, request.tool_runner) as bridge:
                return self._invoke(request, model, bridge)
        return self._invoke(request, model, None)

    def _invoke(self, request: ModelRequest, model: str, bridge: Any) -> ModelResponse:
        pictures = self._pictures(request)
        try:
            command = self._command(request, model, pictures, bridge=bridge)
        except ValueError as exc:
            self._forget_pictures(pictures)
            raise ModelInvocationError(str(exc), ModelAvailability(
                available=False, state=AvailabilityState.MODEL_UNAVAILABLE,
                reason=str(exc), failure_class=FailureClass.USER_ACTION_REQUIRED)) from exc
        prompt = self._prompt(request, native=bridge is not None)
        cwd = str(self.workspace) if self.workspace else None
        # Exactly what the client is handed, for a debugger watching this call.
        request.emit_debug("provider_request", {
            "provider": self.provider_id, "model": model,
            "command": command, "input": prompt, "cwd": cwd,
        })
        # WITH THE AGENT'S TOOLS RUNNING IN IT, a call ends when it falls
        # silent - a tool of the agent's at work is not silence - or the run
        # is stopped, never at a fixed time (as Claude Code's calls do).
        watched: Dict[str, Any] = {}
        if bridge is not None:
            from .claude_code_provider import _Stream
            stream = _Stream(request, bridge, self.timeout_seconds)
            watched = {"on_line": stream.line, "watch": stream.watch}
        else:
            watched = {"timeout": self.timeout_seconds}
        try:
            completed = children.run(
                command, input=prompt, capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                cwd=cwd, **watched,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if sys.platform.startswith("win") else 0,
            )
        except children.Ended as exc:
            message = ("the call was ended: the run was stopped" if exc.reason == "cancelled"
                       else f"Codex {exc.reason}")
            raise ModelInvocationError(message, ModelAvailability(
                available=False, state=AvailabilityState.PROVIDER_UNAVAILABLE
                if exc.reason != "cancelled" else AvailabilityState.UNKNOWN_ERROR,
                reason=message, failure_class=FailureClass.TEMPORARY
                if exc.reason != "cancelled" else FailureClass.UNKNOWN)) from exc
        except subprocess.TimeoutExpired as exc:
            message = f"Codex did not answer within {int(self.timeout_seconds)}s"
            raise ModelInvocationError(message, ModelAvailability(
                available=False, state=AvailabilityState.PROVIDER_UNAVAILABLE,
                reason=message, failure_class=FailureClass.TEMPORARY)) from exc
        except OSError as exc:
            message = f"could not run Codex: {exc}"
            raise ModelInvocationError(message, ModelAvailability(
                available=False, state=AvailabilityState.PROVIDER_UNAVAILABLE,
                reason=message, failure_class=FailureClass.USER_ACTION_REQUIRED)) from exc
        finally:
            # The pictures' files were for this call only.
            self._forget_pictures(pictures)
        if completed.returncode != 0 and "developer_instructions" in " ".join(command) \
                and "developer_instructions" in (completed.stderr or "") \
                and not getattr(self, "_developer_refused", False):
            # A Codex that does not take the setting: told in the prompt, as
            # before, from now on - and this call made again so.
            log.info("this Codex refused developer_instructions; the instructions "
                     "go in the prompt")
            self._developer_refused = True
            return self._invoke(request, model, bridge)
        if completed.returncode != 0:
            verdict = self._translate(completed)
            raise ModelInvocationError(verdict.reason or "Codex failed", verdict)

        text, usage, stop_reason = self._parse_events(completed.stdout or "")
        if not text:
            verdict = ModelAvailability(
                available=False, state=AvailabilityState.UNKNOWN_ERROR,
                reason="Codex returned no assistant message", failure_class=FailureClass.UNKNOWN)
            raise ModelInvocationError(verdict.reason, verdict)
        tool_calls = self._tool_calls(text, request)
        self._usage = UsageSnapshot(
            request_count=self._usage.request_count + 1,
            input_tokens=self._usage.input_tokens + usage.input_tokens,
            output_tokens=self._usage.output_tokens + usage.output_tokens,
            total_tokens=self._usage.total_tokens + usage.total_tokens,
        )
        # A call is remembered as the call, never with what the model wrote
        # after it (local_tools.as_written).
        return ModelResponse(text="" if tool_calls else text, tool_calls=tool_calls,
                             usage=usage, stop_reason=stop_reason,
                             raw_content=as_written(text, tool_calls),
                             model=model, provider=self.provider_id,
                             tool_exchanges=list(bridge.calls) if bridge is not None else [])

    def usage(self) -> UsageSnapshot:
        return self._usage

    def capability_model(self) -> Dict[str, Any]:
        model = super().capability_model()
        model.update({
            "runs_locally": True,
            "stores_no_credential": True,
            "note": "Answers come from the installed Codex CLI using its own sign-in. "
                    "It runs ephemerally with a read-only sandbox.",
        })
        return model
