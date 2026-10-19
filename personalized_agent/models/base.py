"""Model provider abstraction (sections 70-81).

The Agent Core never learns how a provider authenticates, what its error codes
mean, or whether it bills per token.  It sees normalized states only.
"""

from __future__ import annotations

import copy
import logging
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)


class AuthMethod(str, Enum):
    API_KEY = "api_key"
    OAUTH = "oauth"
    ACCOUNT_LOGIN = "account_login"
    NONE = "none"


class CredentialState(str, Enum):
    """Section 78."""

    NOT_CONFIGURED = "not_configured"
    AUTHENTICATION_REQUIRED = "authentication_required"
    AUTHENTICATING = "authenticating"
    AUTHENTICATED = "authenticated"
    EXPIRED = "expired"
    INVALID = "invalid"
    REVOKED = "revoked"
    ERROR = "error"


class AvailabilityState(str, Enum):
    """Section 79."""

    AVAILABLE = "available"
    AUTHENTICATION_REQUIRED = "authentication_required"
    CREDENTIAL_INVALID = "credential_invalid"
    QUOTA_EXHAUSTED = "quota_exhausted"
    RATE_LIMITED = "rate_limited"
    MODEL_UNAVAILABLE = "model_unavailable"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    NETWORK_UNAVAILABLE = "network_unavailable"
    PERMISSION_DENIED = "permission_denied"
    UNKNOWN_ERROR = "unknown_error"


class FailureClass(str, Enum):
    """Section 89 - is this worth retrying, or does the user have to act?"""

    TEMPORARY = "temporary"
    USER_ACTION_REQUIRED = "user_action_required"
    UNKNOWN = "unknown"


USER_ACTION_STATES = {
    AvailabilityState.AUTHENTICATION_REQUIRED,
    AvailabilityState.CREDENTIAL_INVALID,
    AvailabilityState.QUOTA_EXHAUSTED,
    AvailabilityState.PERMISSION_DENIED,
    AvailabilityState.MODEL_UNAVAILABLE,
}

#: A sign-in that stopped working. The application asks for it again itself
#: (app._on_credential_lost, "Sign in to ... again"); whatever else meets the
#: same failure does not say it a second time - one problem, one message
#: (section 88.1).
SIGN_IN_STATES = frozenset({
    AvailabilityState.AUTHENTICATION_REQUIRED,
    AvailabilityState.CREDENTIAL_INVALID,
})


@dataclass
class ModelDescriptor:
    id: str
    display_name: str
    context_window: Optional[int] = None
    max_output_tokens: Optional[int] = None
    input_cost_per_mtok: Optional[float] = None
    output_cost_per_mtok: Optional[float] = None
    #: How hard the model may be asked to think, as its own client lists
    #: the levels. Empty means the catalog did not say, which is not the
    #: same as "none": the client is then left to judge a level itself.
    reasoning_efforts: List[str] = field(default_factory=list)
    default_reasoning_effort: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ModelAvailability:
    """Section 80."""

    available: bool
    state: AvailabilityState = AvailabilityState.AVAILABLE
    reason: Optional[str] = None
    authentication_valid: bool = False
    quota_status: Optional[str] = None
    rate_limit_status: Optional[str] = None
    provider_status: Optional[str] = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    failure_class: FailureClass = FailureClass.UNKNOWN

    def requires_user_action(self) -> bool:
        return (not self.available) and (
            self.failure_class is FailureClass.USER_ACTION_REQUIRED
            or self.state in USER_ACTION_STATES
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "state": self.state.value,
            "reason": self.reason,
            "authentication_valid": self.authentication_valid,
            "quota_status": self.quota_status,
            "rate_limit_status": self.rate_limit_status,
            "provider_status": self.provider_status,
            "checked_at": self.checked_at.isoformat(),
            "failure_class": self.failure_class.value,
            "requires_user_action": self.requires_user_action(),
        }


@dataclass
class AuthStatus:
    state: CredentialState
    method: Optional[AuthMethod] = None
    account: Optional[str] = None
    detail: Optional[str] = None
    expires_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "method": self.method.value if self.method else None,
            "account": self.account,
            "detail": self.detail,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


@dataclass
class AuthMethodInfo:
    """One authentication method, and whether it can be completed here.

    Section 77, as amended: a method the provider supports but that cannot be
    completed on this machine is still disclosed - with the reason and the
    remedy - rather than omitted. Omitting it leaves the user believing the
    provider does not offer it at all.
    """

    method: AuthMethod
    available: bool = True
    reason: Optional[str] = None
    remedy: Optional[str] = None
    remedy_url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "method": self.method.value,
            "available": self.available,
            "reason": self.reason,
            "remedy": self.remedy,
            "remedy_url": self.remedy_url,
        }


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "description": self.description,
                "input_schema": self.input_schema}


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class ModelRequest:
    messages: List[Dict[str, Any]]
    system: Optional[str] = None
    tools: List[ToolSpec] = field(default_factory=list)
    max_tokens: int = 4096
    task: str = "general"
    metadata: Dict[str, Any] = field(default_factory=dict)
    #: WHICH LANGUAGE IT WRITES IN (common/languages.Language) - said here
    #: and only here: `in_display(...) + in_english(...)`. The manager adds
    #: the words for it to the system prompt and records it with the call;
    #: the Language debugger reads it off the source. None: not said.
    language: Any = None
    #: Where a debugger wants to see what was actually sent - the resolved
    #: model, the exact payload handed to the client. None for every
    #: ordinary call, and then nothing is copied or kept. Never part of the
    #: request's identity: two requests differing only in who is watching
    #: are the same request.
    debug: Optional[Callable[[str, Dict[str, Any]], None]] = field(
        default=None, repr=False, compare=False)
    #: How hard to think, for a client that takes it (Codex). None leaves
    #: the client's own configuration in charge.
    reasoning_effort: Optional[str] = None
    #: THE MODEL'S OWN WEB SEARCH: a client that has one (Claude Code)
    #: searches and reads pages with it, in place of the agent's web tools
    #: (`WEB_TOOL_NAMES`) - which a client without one is still given.
    builtin_web: bool = False
    #: THE CHECKS ON THE WEB the agent's own web tools are held to
    #: (runtime/web_guard.py): a client's own web tools are held to them too
    #: - and, with none given, not offered (Claude Code's hooks).
    web_guard: Any = field(default=None, repr=False, compare=False)
    #: THE CLIENT'S OWN READING TOOLS (Claude Code's Read - PDFs and images
    #: too - Glob and Grep), denied every place listed: all the agents may
    #: not read (`filesystem.unreadable_for`). None: not offered at all.
    native_read: Optional[List[str]] = field(default=None, repr=False, compare=False)
    #: WHAT THIS CALL READS MUST STAY SEALED: a run whose folders are kept
    #: from one another (each way and each try of the Work debugger). A
    #: client that reads by itself where nothing confines it is passed over
    #: (`ModelManager.invoke`, `READS_UNCONFINED`).
    sealed_reading: bool = field(default=False, repr=False, compare=False)
    #: Runs a tool the model calls natively - for a client that offers the
    #: tools that way (Claude Code, over MCP): (name, arguments) -> what it
    #: returned. With it, such a client runs the tools during the call and
    #: answers with none left to run. None: a tool call comes back in the
    #: response, for the caller to run. Never part of the request's identity.
    tool_runner: Optional[Callable[[str, Dict[str, Any]], Any]] = field(
        default=None, repr=False, compare=False)
    #: Asked while the call goes: True ends it at once (the run was stopped).
    cancelled: Optional[Callable[[], bool]] = field(default=None, repr=False, compare=False)
    #: Told what the call has used so far, as it goes - a long call with
    #: tools is many rounds of the model - so a caller can keep to its limits.
    on_usage: Optional[Callable[["ModelUsage"], None]] = field(
        default=None, repr=False, compare=False)

    def emit_debug(self, kind: str, data: Dict[str, Any]) -> None:
        """Hand a snapshot to whoever is watching, if anyone is.

        A copy, so that a watcher which edits what it was given cannot edit
        the call; and a watcher that fails is logged and forgotten, because
        being watched must never be the reason a call did not happen.
        """
        if not callable(self.debug):
            return
        try:
            self.debug(kind, copy.deepcopy(data))
        except Exception:
            log.debug("model debug callback failed", exc_info=True)


@dataclass
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    #: Input the provider served from its prompt cache instead of reading
    #: it again. Old input, seen before, and cheap.
    cached_input_tokens: int = 0
    #: Input the provider wrote INTO the cache on this call. New input,
    #: never seen before, and the most expensive kind.
    #:
    #: The two are kept apart because adding them together says the
    #: opposite of the truth. Summed under one word, a call that sent a
    #: thousand new tokens and re-read fourteen thousand old ones reads as
    #: "fifteen thousand cached", which invites the conclusion that
    #: nothing new was sent. `input_tokens` alone is no better: it is
    #: whatever was neither cached nor caching, which on these providers
    #: is a handful.
    cache_written_tokens: int = 0
    #: What the provider itself said the call cost, in USD - Claude Code at
    #: list price, OpenRouter as billed - or None when it did not say.
    cost_usd: Optional[float] = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def read_tokens(self) -> int:
        """Everything the model read on this call, however it got it."""
        return (self.input_tokens + self.cached_input_tokens
                + self.cache_written_tokens)

    @property
    def new_tokens(self) -> int:
        """Everything the model had not seen before."""
        return self.input_tokens + self.cache_written_tokens


@dataclass
class ModelResponse:
    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: ModelUsage = field(default_factory=ModelUsage)
    stop_reason: Optional[str] = None
    raw_content: Any = None
    model: str = ""
    provider: str = ""
    #: The tools the client ran during the call (`ModelRequest.tool_runner`):
    #: each {"name", "arguments", "result", "error"}, in order.
    tool_exchanges: List[Dict[str, Any]] = field(default_factory=list)


class ModelInvocationError(Exception):
    """Normalized provider failure carrying an availability verdict."""

    def __init__(self, message: str, availability: ModelAvailability) -> None:
        super().__init__(message)
        self.availability = availability


@dataclass
class UsageSnapshot:
    """Section 81 - only report what the provider actually told us."""

    request_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: Optional[float] = None
    remaining_quota: Optional[str] = None
    rate_limit_remaining: Optional[str] = None
    quota_reset_time: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        # Distinguish "known provider value" from "not reported by provider".
        for key in ("remaining_quota", "rate_limit_remaining", "quota_reset_time",
                    "estimated_cost"):
            if data[key] is None:
                data[key] = None  # rendered as "not reported by provider"
        return data


class ModelProvider(ABC):
    """Section 76."""

    provider_id: str = "provider"
    display_name: str = "Provider"
    #: What it is, for Settings to group by: a service's API ("api"), a
    #: client installed on this machine that answers ("client"), or the
    #: application's own stand-in ("offline").
    kind: str = "api"

    @abstractmethod
    def list_models(self) -> List[ModelDescriptor]:
        ...

    def accepts_model_id(self, model: str) -> bool:
        """Whether an id may be sent to this provider at all.

        By default, when it is in the provider's list - or when the provider
        lists nothing. A provider whose list is only a picker's hint, cached
        from a catalog that changes under it, says otherwise for itself:
        absence from a stale list is not proof the server refuses the model.
        """
        models = self.list_models()
        return not models or any(descriptor.id == model for descriptor in models)

    @abstractmethod
    def get_auth_methods(self) -> List[AuthMethod]:
        ...

    @abstractmethod
    def get_auth_status(self) -> AuthStatus:
        ...

    @abstractmethod
    def authenticate(self, config: Dict[str, Any]) -> AuthStatus:
        ...

    @abstractmethod
    def logout(self) -> AuthStatus:
        ...

    @abstractmethod
    def check_availability(self, model: str) -> ModelAvailability:
        ...

    @abstractmethod
    def invoke(self, request: ModelRequest, model: str) -> ModelResponse:
        ...

    def usage(self) -> UsageSnapshot:
        return UsageSnapshot()

    def describe_auth_methods(self) -> List["AuthMethodInfo"]:
        """Every method this provider supports, completable or not (section 77).

        The default treats everything ``get_auth_methods`` returns as
        completable; a provider whose methods depend on the machine overrides
        this to say why one is not, and what the user can do about it.
        """
        return [AuthMethodInfo(method) for method in self.get_auth_methods()]

    def capability_model(self) -> Dict[str, Any]:
        """Section 77 - what the Settings UI is allowed to show."""
        described = self.describe_auth_methods()
        return {
            "provider": self.provider_id,
            "display_name": self.display_name,
            "kind": self.kind,
            "authentication_methods": [m.value for m in self.get_auth_methods()],
            "authentication_method_details": [m.to_dict() for m in described],
        }
