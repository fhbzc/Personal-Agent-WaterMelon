"""Anthropic provider adapter.

The adapter is the only place that knows Anthropic specific request shapes and
error codes; it translates both into the normalized states of section 80.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..base import (
    AuthMethod,
    AuthMethodInfo,
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
from .account_login import (
    AccountLoginSession,
    LoginState,
    account_login_unavailable_reason,
    available_backend,
    cli_path,
    describe_backends,
    missing_tool_remedy,
)

log = logging.getLogger(__name__)

API_KEY_CREDENTIAL = "anthropic.api_key"
# A long-lived token minted by an account sign-in (section 75.2). Held by the
# Credential Manager like any other secret and never returned to a caller.
OAUTH_TOKEN_CREDENTIAL = "anthropic.account_token"
# OAuth tokens go on Authorization: Bearer, and /v1/messages needs this beta.
OAUTH_BETA_HEADER = "oauth-2025-04-20"

#: NO ACCOUNT SIGN-IN FOR THE API (the user's decision, 2026-10-12): a Claude
#: subscription is used through Claude Code, the way Anthropic offers it. A
#: token minted by `claude setup-token` and sent to the API by this
#: application is a subscription used outside Anthropic's own apps - and
#: the same subscription is already one entry away. The API takes a key.
ACCOUNT_LOGIN_REFUSED = ("Account login is not offered for the Anthropic API: give an "
                         "API key - your Claude subscription is used through Claude Code")


# A Settings or Debugger refresh must never block on the network: provider calls
# made for the UI are bounded and never retried, and their failures are cached so
# a broken connection costs the timeout once, not on every refresh.
UI_TIMEOUT_SECONDS = 5.0
DISCOVERY_RETRY_SECONDS = 120.0

# Cached catalogue; refreshed from the Models API once authenticated.
#: HOW HARD A MODEL MAY BE ASKED TO THINK (`output_config.effort`): the
#: current models take all five; Opus and Sonnet 4.6 all but "xhigh"; older
#: ones none - for those it is not sent.
EFFORTS = ["low", "medium", "high", "xhigh", "max"]


def efforts_of(model: str) -> List[str]:
    name = str(model or "").lower()
    if any(old in name for old in ("haiku-4-5", "sonnet-4-5", "opus-4-5", "opus-4-1",
                                   "claude-3", "opus-4-0", "sonnet-4-0")):
        return []
    if "opus-4-6" in name or "sonnet-4-6" in name:
        return ["low", "medium", "high", "max"]
    return list(EFFORTS)


DEFAULT_MODELS: List[ModelDescriptor] = [
    ModelDescriptor("claude-opus-5-5", "Claude Opus 5.5", 1_000_000, 128_000, 4.0, 20.0,
                    reasoning_efforts=list(EFFORTS)),
    ModelDescriptor("claude-fable-5-1", "Claude Fable 5.1", 1_000_000, 128_000, 10.0, 50.0,
                    reasoning_efforts=list(EFFORTS)),
    ModelDescriptor("claude-sonnet-5-5", "Claude Sonnet 5.5", 1_000_000, 128_000, 2.0, 10.0,
                    reasoning_efforts=list(EFFORTS)),
    ModelDescriptor("claude-haiku-5-5", "Claude Haiku 5.5", 1_000_000, 128_000, 0.1, 0.5,
                    reasoning_efforts=list(EFFORTS)),
    ModelDescriptor("claude-opus-5", "Claude Opus 5", 1_000_000, 128_000, 5.0, 25.0,
                    reasoning_efforts=list(EFFORTS)),
    ModelDescriptor("claude-sonnet-5", "Claude Sonnet 5", 1_000_000, 128_000, 2.0, 10.0,
                    reasoning_efforts=list(EFFORTS)),
]


class AnthropicProvider(ModelProvider):
    provider_id = "anthropic"
    display_name = "Anthropic"

    def __init__(self, credentials: Any) -> None:
        self.credentials = credentials
        self._lock = threading.RLock()
        self._client = None
        self._client_key: Optional[str] = None
        self._models: Optional[List[ModelDescriptor]] = None
        self._discovery_failed_at: float = 0.0
        self._usage = UsageSnapshot()
        self._login_session: Optional[AccountLoginSession] = None
        #: Set when the endpoint turned down a request for prompt caching,
        #: so that it is not asked again. See _ask_for_caching.
        self._caching_refused = False

    # -- SDK plumbing -------------------------------------------------------
    def _sdk(self):
        try:
            import anthropic  # official SDK
        except ImportError as exc:  # pragma: no cover - depends on install
            raise ModelInvocationError(
                "the anthropic package is not installed (pip install anthropic)",
                ModelAvailability(
                    available=False,
                    state=AvailabilityState.PROVIDER_UNAVAILABLE,
                    reason="anthropic SDK not installed",
                    failure_class=FailureClass.USER_ACTION_REQUIRED,
                ),
            ) from exc
        return anthropic

    def _api_key(self) -> Optional[str]:
        stored = self.credentials.get(API_KEY_CREDENTIAL)
        if stored:
            return stored
        # Ambient credentials (env var or an `ant auth login` profile) are a
        # legitimate account based configuration - section 75.
        return os.environ.get("ANTHROPIC_API_KEY") or None

    def _account_token(self) -> Optional[str]:
        """A token the user set in their own environment. One minted by an
        account sign-in here before is not used (ACCOUNT_LOGIN_REFUSED)."""
        return os.environ.get("ANTHROPIC_AUTH_TOKEN") or None

    # -- account based credentials (section 75) -----------------------------
    @staticmethod
    def _profile_dirs() -> List[Path]:
        """Where the Anthropic CLI stores the profile it creates on login."""
        candidates = [Path.home() / ".config" / "anthropic"]
        config_home = os.environ.get("XDG_CONFIG_HOME")
        if config_home:
            candidates.append(Path(config_home) / "anthropic")
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / "anthropic")
        return candidates

    def _ambient_credential(self) -> Optional[Dict[str, str]]:
        """Detect an account based credential this machine already has.

        Returns ``None`` when the user has to do something first; the caller
        turns that into an actionable message rather than a silent no-op.
        """
        if os.environ.get("ANTHROPIC_API_KEY"):
            return {"source": "environment", "detail": "ANTHROPIC_API_KEY is set"}
        if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return {"source": "environment", "detail": "ANTHROPIC_AUTH_TOKEN is set"}
        for directory in self._profile_dirs():
            try:
                if directory.is_dir() and any(directory.iterdir()):
                    return {"source": "cli_profile",
                            "detail": f"signed in through the Anthropic CLI ({directory})"}
            except OSError:
                continue
        return None

    @staticmethod
    def _cli_available() -> Optional[str]:
        return cli_path()

    def can_initiate_login(self) -> bool:
        """Whether pressing Sign in can actually finish (sections 75.1, 76.1).

        A tool being installed is not enough: it must produce a credential this
        application can use. One that authenticates and is then refused offers
        the user a sign-in that ends where it began.
        """
        return bool(available_backend()) or bool(self._ambient_credential())

    # -- the sign-in itself (section 75.2) ----------------------------------
    def start_account_login(self, open_browser: bool = True) -> Dict[str, Any]:
        """Begin a browser based sign-in and return where to send the user."""
        with self._lock:
            session = self._login_session
            if session is not None and not session.describe()["finished"]:
                return session.describe()
            session = AccountLoginSession(on_token=self._store_account_token)
            self._login_session = session
        return session.start(open_browser=open_browser)

    def _store_account_token(self, token: str) -> None:
        """Called by the sign-in the moment a token exists, however it finished.

        The browser can complete the authorization on its own, in which case
        there is no code to paste and nothing would otherwise collect the
        result.
        """
        self.credentials.set(OAUTH_TOKEN_CREDENTIAL, token)
        with self._lock:
            self._client = None
            self._client_key = None
            self._models = None
            self._discovery_failed_at = 0.0

    def submit_account_login_code(self, code: str) -> Dict[str, Any]:
        session = self._login_session
        if session is None:
            return {"state": LoginState.IDLE, "detail": "no sign-in is in progress",
                    "authorization_url": None, "awaiting_code": False, "finished": True}
        result = session.submit_code(code)
        # The token is stored by the callback the moment it appears, so nothing
        # is collected here; drop the session's copy so no secret outlives the
        # sign-in that produced it (sections 83, 84).
        session.take_token()
        return result

    def account_login_status(self) -> Dict[str, Any]:
        session = self._login_session
        if session is None:
            return {"state": LoginState.IDLE, "detail": "", "authorization_url": None,
                    "awaiting_code": False, "finished": True}
        return session.describe()

    def cancel_account_login(self) -> Dict[str, Any]:
        session = self._login_session
        if session is None:
            return {"state": LoginState.IDLE, "detail": "", "authorization_url": None,
                    "awaiting_code": False, "finished": True}
        return session.cancel()

    def _get_client(self):
        """A client for whichever credential this installation actually has.

        An API key and an account token are different mechanisms: a key goes on
        x-api-key, a token goes on Authorization: Bearer and needs the OAuth
        beta header. An explicitly stored key wins, so a user who configures one
        is not silently answered through someone else's sign-in.
        """
        anthropic = self._sdk()
        key = self._api_key()
        token = None if key else self._account_token()
        identity = f"key:{key}" if key else (f"token:{token}" if token else "ambient")
        with self._lock:
            if self._client is None or self._client_key != identity:
                if key:
                    self._client = anthropic.Anthropic(api_key=key)
                elif token:
                    self._client = anthropic.Anthropic(
                        auth_token=token,
                        default_headers={"anthropic-beta": OAUTH_BETA_HEADER},
                    )
                else:
                    self._client = anthropic.Anthropic()
                self._client_key = identity
            return self._client

    def _ui_client(self):
        """Client for calls a user interface waits on: bounded, no retries."""
        return self._get_client().with_options(timeout=UI_TIMEOUT_SECONDS, max_retries=0)

    # -- ModelProvider ------------------------------------------------------
    def list_models(self) -> List[ModelDescriptor]:
        with self._lock:
            if self._models:
                return list(self._models)
            recently_failed = (time.time() - self._discovery_failed_at) < DISCOVERY_RETRY_SECONDS
        # Without a credential there is nothing to discover with, so do not open a
        # connection at all - the catalogue below is what Settings shows.
        if recently_failed or self.get_auth_status().state is not CredentialState.AUTHENTICATED:
            return list(DEFAULT_MODELS)
        try:
            client = self._ui_client()
            discovered = []
            for item in client.models.list():
                discovered.append(
                    ModelDescriptor(
                        id=item.id,
                        display_name=getattr(item, "display_name", item.id),
                        context_window=getattr(item, "max_input_tokens", None),
                        max_output_tokens=getattr(item, "max_tokens", None),
                        reasoning_efforts=efforts_of(item.id),
                    )
                )
            if discovered:
                with self._lock:
                    self._models = discovered
                return discovered
        except Exception:
            log.debug("model discovery failed; using cached catalogue", exc_info=True)
            with self._lock:
                self._discovery_failed_at = time.time()
        return list(DEFAULT_MODELS)

    def get_auth_methods(self) -> List[AuthMethod]:
        """An API key - and nothing else (ACCOUNT_LOGIN_REFUSED)."""
        return [AuthMethod.API_KEY]

    def describe_auth_methods(self) -> List[AuthMethodInfo]:
        """An API key only: a subscription goes through Claude Code."""
        return [AuthMethodInfo(AuthMethod.API_KEY)]

    def account_login_tools(self) -> List[Dict[str, Any]]:
        """Which sign-in tools this machine has, for Settings and diagnostics."""
        return describe_backends()

    def get_auth_status(self) -> AuthStatus:
        if self.credentials.has(API_KEY_CREDENTIAL):
            return AuthStatus(CredentialState.AUTHENTICATED, AuthMethod.API_KEY,
                              detail="API key stored in the credential manager")
        ambient = self._ambient_credential()
        if ambient:
            return AuthStatus(CredentialState.AUTHENTICATED, AuthMethod.ACCOUNT_LOGIN,
                              detail=ambient["detail"])
        return AuthStatus(CredentialState.NOT_CONFIGURED, None,
                          detail="no credential configured for this provider")

    def authenticate(self, config: Dict[str, Any]) -> AuthStatus:
        method = config.get("method", AuthMethod.API_KEY.value)
        if method == AuthMethod.API_KEY.value:
            api_key = (config.get("api_key") or "").strip()
            if not api_key:
                return AuthStatus(CredentialState.INVALID, AuthMethod.API_KEY,
                                  detail="empty API key")
            self.credentials.set(API_KEY_CREDENTIAL, api_key)
            with self._lock:
                self._client = None
                self._models = None
                self._discovery_failed_at = 0.0
            probe = self.check_availability(config.get("model") or DEFAULT_MODELS[0].id)
            if probe.available or probe.state is AvailabilityState.RATE_LIMITED:
                return AuthStatus(CredentialState.AUTHENTICATED, AuthMethod.API_KEY)
            if probe.state in (AvailabilityState.CREDENTIAL_INVALID,
                              AvailabilityState.AUTHENTICATION_REQUIRED):
                self.credentials.delete(API_KEY_CREDENTIAL)
                return AuthStatus(CredentialState.INVALID, AuthMethod.API_KEY,
                                  detail=probe.reason)
            return AuthStatus(CredentialState.AUTHENTICATED, AuthMethod.API_KEY,
                              detail=probe.reason)
        if method == AuthMethod.ACCOUNT_LOGIN.value:
            return AuthStatus(CredentialState.ERROR, AuthMethod.ACCOUNT_LOGIN,
                              detail=ACCOUNT_LOGIN_REFUSED)
        return AuthStatus(CredentialState.ERROR, detail=f"unsupported method {method}")

    def logout(self) -> AuthStatus:
        self.credentials.delete(API_KEY_CREDENTIAL)
        self.credentials.delete(OAUTH_TOKEN_CREDENTIAL)
        with self._lock:
            self._client = None
            self._client_key = None
        return AuthStatus(CredentialState.NOT_CONFIGURED)

    # -- error translation --------------------------------------------------
    def _translate(self, exc: Exception) -> ModelAvailability:
        anthropic = None
        try:
            import anthropic as anthropic_mod

            anthropic = anthropic_mod
        except ImportError:
            pass

        now = datetime.now(timezone.utc)
        name = type(exc).__name__
        message = str(exc)[:400]

        def verdict(state: AvailabilityState, failure: FailureClass,
                    auth_ok: bool = True) -> ModelAvailability:
            return ModelAvailability(
                available=False,
                state=state,
                reason=message or name,
                authentication_valid=auth_ok,
                provider_status=name,
                checked_at=now,
                failure_class=failure,
            )

        if anthropic is not None:
            if isinstance(exc, getattr(anthropic, "AuthenticationError", ())):
                return verdict(AvailabilityState.CREDENTIAL_INVALID,
                               FailureClass.USER_ACTION_REQUIRED, auth_ok=False)
            if isinstance(exc, getattr(anthropic, "PermissionDeniedError", ())):
                return verdict(AvailabilityState.PERMISSION_DENIED,
                               FailureClass.USER_ACTION_REQUIRED)
            if isinstance(exc, getattr(anthropic, "NotFoundError", ())):
                return verdict(AvailabilityState.MODEL_UNAVAILABLE,
                               FailureClass.USER_ACTION_REQUIRED)
            if isinstance(exc, getattr(anthropic, "RateLimitError", ())):
                lowered = message.lower()
                if "credit" in lowered or "quota" in lowered or "billing" in lowered:
                    return verdict(AvailabilityState.QUOTA_EXHAUSTED,
                                   FailureClass.USER_ACTION_REQUIRED)
                if not _looks_like_a_rate_limit(exc):
                    # A rate limit says how much is left and when to come back.
                    # A refusal carrying none of that, with no message, is not
                    # one: the credential is being turned away for this use, and
                    # waiting will not change it. Reporting it as temporary
                    # promises a retry that can never succeed.
                    return ModelAvailability(
                        available=False,
                        state=AvailabilityState.CREDENTIAL_INVALID,
                        reason=_subscription_refused_reason(),
                        authentication_valid=False,
                        provider_status=name,
                        checked_at=now,
                        failure_class=FailureClass.USER_ACTION_REQUIRED,
                    )
                # The provider's own text for a rate limit is often just
                # "Error", which reads as a fault rather than as a wait. Say
                # what it is, and how long, when the response says so.
                wait = _retry_after(exc)
                friendly = "the provider is rate limiting this credential"
                if wait:
                    friendly += f"; it can be retried in about {wait}"
                friendly += ". The agent will retry on its own."
                return ModelAvailability(
                    available=False,
                    state=AvailabilityState.RATE_LIMITED,
                    reason=friendly,
                    provider_status=name,
                    checked_at=now,
                    failure_class=FailureClass.TEMPORARY,
                    rate_limit_status=wait or "unknown",
                )
            if isinstance(exc, getattr(anthropic, "APIConnectionError", ())):
                return verdict(AvailabilityState.NETWORK_UNAVAILABLE, FailureClass.TEMPORARY)
            status_error = getattr(anthropic, "APIStatusError", ())
            if isinstance(exc, status_error):
                status = getattr(exc, "status_code", None)
                if status == 400 and "credit" in message.lower():
                    return verdict(AvailabilityState.QUOTA_EXHAUSTED,
                                   FailureClass.USER_ACTION_REQUIRED)
                if status and 500 <= int(status) < 600:
                    return verdict(AvailabilityState.PROVIDER_UNAVAILABLE, FailureClass.TEMPORARY)
                return verdict(AvailabilityState.UNKNOWN_ERROR, FailureClass.UNKNOWN)
        if isinstance(exc, ModelInvocationError):
            return exc.availability
        return verdict(AvailabilityState.UNKNOWN_ERROR, FailureClass.UNKNOWN)

    def check_availability(self, model: str) -> ModelAvailability:
        status = self.get_auth_status()
        if status.state is CredentialState.NOT_CONFIGURED:
            return ModelAvailability(
                available=False,
                state=AvailabilityState.AUTHENTICATION_REQUIRED,
                reason="no Anthropic credential configured",
                authentication_valid=False,
                failure_class=FailureClass.USER_ACTION_REQUIRED,
            )
        try:
            client = self._ui_client()
            client.models.retrieve(model)
            return ModelAvailability(
                available=True,
                state=AvailabilityState.AVAILABLE,
                authentication_valid=True,
                failure_class=FailureClass.TEMPORARY,
            )
        except Exception as exc:
            return self._translate(exc)

    # -- invocation ---------------------------------------------------------
    def invoke(self, request: ModelRequest, model: str) -> ModelResponse:
        client = self._get_client()
        kwargs: Dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens,
            "messages": request.messages,
            "thinking": {"type": "adaptive"},
        }
        # How hard it thinks, as chosen for this model on the list - only
        # where the model takes it.
        if request.reasoning_effort in efforts_of(model):
            kwargs["output_config"] = {"effort": request.reasoning_effort}
        if request.system:
            kwargs["system"] = request.system
        if request.tools:
            kwargs["tools"] = [t.to_dict() for t in request.tools]
        cached = self._ask_for_caching(client, kwargs)
        # The arguments exactly as the API is called with them, for a
        # debugger watching this call. The key is never among them: it lives
        # in the client object, not in the request.
        request.emit_debug("provider_request", {
            "provider": self.provider_id, "model": model, "kwargs": cached,
        })
        try:
            try:
                message = client.messages.create(**cached)
            except Exception as exc:
                if cached is kwargs or not _refused_caching(exc):
                    raise
                # Asked for something this endpoint does not take - a proxy
                # or an older API in front of it. The request without it is
                # the request as it always was; it is not asked for again.
                log.info("prompt caching was refused here; sending without it")
                self._caching_refused = True
                request.emit_debug("provider_request", {
                    "provider": self.provider_id, "model": model, "kwargs": kwargs,
                    "retry": "prompt caching was refused",
                })
                message = client.messages.create(**kwargs)
        except Exception as exc:
            raise ModelInvocationError(str(exc), self._translate(exc)) from exc

        text_parts: List[str] = []
        tool_calls: List[ToolCall] = []
        for block in message.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(block.text)
            elif block_type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name,
                                           arguments=dict(block.input or {})))

        # With caching asked for, most of a repeated prompt comes back as
        # read from the cache or written to it, and `input_tokens` is only
        # what was neither. Kept apart as the Claude Code provider keeps
        # them (see ModelUsage), so nothing the model read goes uncounted.
        usage = ModelUsage(
            input_tokens=_count(message.usage, "input_tokens"),
            output_tokens=_count(message.usage, "output_tokens"),
            cached_input_tokens=_count(message.usage, "cache_read_input_tokens"),
            cache_written_tokens=_count(message.usage, "cache_creation_input_tokens"),
        )
        self._usage.request_count += 1
        self._usage.input_tokens += usage.input_tokens
        self._usage.output_tokens += usage.output_tokens
        self._usage.total_tokens += usage.total_tokens

        return ModelResponse(
            text="\n".join(text_parts).strip(),
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=getattr(message, "stop_reason", None),
            raw_content=[b.model_dump() if hasattr(b, "model_dump") else b for b in message.content],
            model=model,
            provider=self.provider_id,
        )

    def usage(self) -> UsageSnapshot:
        return self._usage

    def _ask_for_caching(self, client: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """The same request, asking for its prefix to be cached.

        NOTHING ASKED FOR CACHING. Every call sent its whole prompt as new
        input: the system prompt, the tools, and in a tool loop every turn
        before this one, all of it identical to the call a moment before.
        A cache changes only how the input is billed and how soon the first
        token comes - the model reads the same prompt either way - so the
        answer is the same and the repeated part is read at a tenth of the
        price. The first call that writes it pays a quarter more for that
        part; a prefix not repeated within five minutes is that much dearer.

        Asked for once, at the top level, which caches up to the last block
        of the request: the system prompt, the tools and everything before
        the newest turn. An SDK too old to know the parameter is asked the
        same thing on the system prompt instead, the one place every
        version accepts it.
        """
        if self._caching_refused:
            return kwargs
        try:
            import inspect
            takes_it = "cache_control" in inspect.signature(
                client.messages.create).parameters
        except (TypeError, ValueError, AttributeError):
            takes_it = False
        if takes_it:
            return {**kwargs, "cache_control": {"type": "ephemeral"}}
        system = kwargs.get("system")
        if isinstance(system, str) and system:
            return {**kwargs, "system": [{"type": "text", "text": system,
                                          "cache_control": {"type": "ephemeral"}}]}
        return kwargs


def _count(usage: Any, name: str) -> int:
    """One of the usage figures, as a whole number, zero when not reported."""
    try:
        return int(getattr(usage, name, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _refused_caching(exc: Exception) -> bool:
    """Whether a request failed because it asked for caching.

    A 400 naming the parameter, or a client library that would not take it
    as an argument. Anything else is a real failure and is reported as one.
    """
    if isinstance(exc, TypeError):
        return "cache_control" in str(exc)
    status = getattr(exc, "status_code", None)
    return status == 400 and "cache_control" in str(exc)


def _retry_after(exc: Exception) -> Optional[str]:
    """How long the provider asked us to wait, when it said."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    for header in ("retry-after", "anthropic-ratelimit-requests-reset"):
        value = headers.get(header) if hasattr(headers, "get") else None
        if not value:
            continue
        try:
            seconds = int(float(value))
        except (TypeError, ValueError):
            return str(value)
        if seconds < 60:
            return f"{seconds} seconds"
        return f"{seconds // 60} minutes"
    return None


def _looks_like_a_rate_limit(exc: Exception) -> bool:
    """Whether a 429 carries the marks of an actual rate limit.

    A real one says what is left and when to return - the quota headers, or a
    retry-after, or at minimum an explanation. One with none of those is a
    refusal wearing a 429.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        for name in headers.keys():
            lowered = str(name).lower()
            if "ratelimit" in lowered or lowered == "retry-after":
                return True
    body = str(exc)
    return bool(re.search(r"rate limit|too many requests|slow down|try again in",
                          body, re.I))


def _subscription_refused_reason() -> str:
    """What to do about a subscription credential the API will not accept.

    Section 76.1: the way to use a subscription is the client licensed for it,
    so name that first - and say how to get it when it is not installed.
    """
    import shutil as _shutil

    base = ("this credential was refused for API use: a Claude subscription covers "
            "Claude's own tools, not another application's API calls. ")
    if _shutil.which("claude"):
        return base + ("Claude Code is installed on this machine - choose the "
                       "\"Claude Code CLI\" model in Settings > General > Models to answer "
                       "on your subscription, or set an API key from the Anthropic "
                       "Console for API billing.")
    return base + ("Install Claude Code to answer on your subscription, and choose the "
                   "\"Claude Code CLI\" model in Settings > General > Models - or set an "
                   "API key from the Anthropic Console for API billing.")
