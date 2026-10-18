"""Model management (sections 70-97).

Responsibilities:

* map subsystem roles (primary, heartbeat, data heartbeat, scheduled task) to a
  provider and model, honouring optional overrides;
* expose one normalized availability verdict to every caller;
* emit ``model.*`` events on state transitions, including recovery;
* never silently substitute a provider (section 94/95).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..common.observability.tracing import NullTracer
from .prices import cache_write_times, estimate
from .base import (
    AvailabilityState,
    CredentialState,
    FailureClass,
    ModelAvailability,
    ModelInvocationError,
    ModelProvider,
    ModelRequest,
    ModelResponse,
)

log = logging.getLogger(__name__)

ROLES = ("primary", "heartbeat", "scheduled_task")

_ROLE_OVERRIDE_KEYS = {
    "heartbeat": "heartbeat_model_override",
    "scheduled_task": "scheduled_task_model_override",
}

#: What a usage limit says, for a provider that files it as a rate limit
#: (Claude Code's session limit): it does not pass in seconds, so the next
#: model on the list answers meanwhile.
_USAGE_LIMIT_WORDS = ("usage limit", "session limit", "weekly limit", "limit reached",
                      "hit your limit", "resets")


def switches(availability: ModelAvailability) -> bool:
    """Whether a model that failed so hands the call to the NEXT ON THE LIST:
    what only the user can mend - a sign-in, a key, credit, a model not
    offered, a client not installed - and a usage limit. Not a timeout or a
    busy moment: that call is tried again, not passed on."""
    if availability.requires_user_action():
        return True
    if availability.state is AvailabilityState.QUOTA_EXHAUSTED:
        return True
    reason = str(availability.reason or "").lower()
    return (availability.state is AvailabilityState.RATE_LIMITED
            and any(word in reason for word in _USAGE_LIMIT_WORDS))


_STATE_EVENTS = {
    AvailabilityState.AVAILABLE: "model.available",
    AvailabilityState.AUTHENTICATION_REQUIRED: "model.authentication_required",
    AvailabilityState.CREDENTIAL_INVALID: "model.authentication_failed",
    AvailabilityState.QUOTA_EXHAUSTED: "model.quota_exhausted",
    AvailabilityState.RATE_LIMITED: "model.rate_limited",
    AvailabilityState.MODEL_UNAVAILABLE: "model.unavailable",
    AvailabilityState.PROVIDER_UNAVAILABLE: "model.unavailable",
    AvailabilityState.NETWORK_UNAVAILABLE: "model.unavailable",
    AvailabilityState.PERMISSION_DENIED: "model.unavailable",
    AvailabilityState.UNKNOWN_ERROR: "model.unavailable",
}


@dataclass
class ResolvedModel:
    role: str
    provider_id: str
    model_id: str
    is_override: bool

    @property
    def label(self) -> str:
        return f"{self.provider_id}/{self.model_id}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "provider": self.provider_id,
            "model": self.model_id,
            "is_override": self.is_override,
            "label": self.label,
        }


#: CLIENTS THAT READ BY THEMSELVES, WHERE NOTHING CONFINES THEM. Codex runs
#: its own commands in its own sandbox, which keeps it from writing and NOT
#: from reading - so the folders a run may not read (another way's, another
#: try's) are open to it. Claude Code is told every place it may not read
#: (`ModelRequest.native_read`); a model reached by its API reads nothing
#: but what it is sent. Until Codex runs inside the run's own isolation, a
#: call whose reading must stay sealed is not given to it.
READS_UNCONFINED = frozenset({"codex"})


class ModelUnavailable(Exception):
    def __init__(self, resolved: ResolvedModel, availability: ModelAvailability) -> None:
        super().__init__(f"{resolved.label}: {availability.reason or availability.state.value}")
        self.resolved = resolved
        self.availability = availability


class ModelManager:
    ROLE_CONNECTIONS = {
        "primary": "agent_core->model_provider",
        "heartbeat": "heartbeat->model_provider",
        "scheduled_task": "scheduler->model_provider",
    }

    def __init__(self, config: Any, credentials: Any, bus: Any = None,
                 observability: Any = None, availability_ttl: float = 60.0,
                 tracer: Any = None) -> None:
        self.config = config
        self.tracer = tracer or NullTracer()
        self.credentials = credentials
        self.bus = bus
        self.observability = observability
        self.availability_ttl = availability_ttl
        self._lock = threading.RLock()
        self._providers: Dict[str, ModelProvider] = {}
        self._availability: Dict[str, Tuple[float, ModelAvailability]] = {}
        self._last_state: Dict[str, AvailabilityState] = {}
        #: The model that last answered each role - on the list, the first
        #: that worked.
        self.answered: Dict[str, str] = {}
        self._register_defaults()
        # A stored provider/model pair that cannot work is repaired here rather
        # than at every use: it outlives the edit that created it (section 82).
        try:
            self.reconcile_primary()
        except Exception:                          # pragma: no cover - defensive
            log.debug("could not reconcile the primary model", exc_info=True)

    # -- registry -----------------------------------------------------------
    def _register_defaults(self) -> None:
        from .providers.anthropic_provider import AnthropicProvider
        from .providers.claude_code_provider import ClaudeCodeProvider, ClaudeDesktopProvider
        from .providers.codex_provider import CodexProvider
        from .providers.offline_provider import OfflineProvider

        self.register(OfflineProvider(self.credentials))
        self.register(AnthropicProvider(self.credentials))
        # Section 76.1: a licensed client already on the machine can answer,
        # which is how a subscription is used without borrowing its credential.
        self.register(ClaudeCodeProvider(self.credentials, settings=self.config))
        self.register(ClaudeDesktopProvider(self.credentials, settings=self.config))
        self._desktop_chosen_before()
        self._claude_models_by_name()
        self.register(CodexProvider(self.credentials))
        # The services that speak the OpenAI chat API - OpenAI, Gemini,
        # DeepSeek, the relays like OpenRouter, and one at any address.
        from .providers.openai_compatible import providers as openai_compatible
        for provider in openai_compatible(self.credentials):
            self.register(provider)

    def register(self, provider: ModelProvider) -> None:
        with self._lock:
            self._providers[provider.provider_id] = provider

    def providers(self) -> List[ModelProvider]:
        with self._lock:
            return list(self._providers.values())

    def provider(self, provider_id: str) -> Optional[ModelProvider]:
        with self._lock:
            return self._providers.get(provider_id)

    # -- resolution ---------------------------------------------------------
    def _parse_override(self, raw: str) -> Optional[Tuple[str, str]]:
        raw = (raw or "").strip()
        if not raw or "/" not in raw:
            return None
        provider_id, model_id = raw.split("/", 1)
        provider_id, model_id = provider_id.strip(), model_id.strip()
        if not provider_id or not model_id or self.provider(provider_id) is None:
            return None
        return provider_id, model_id

    # -- the list of models, in order -----------------------------------------
    def _claude_models_by_name(self) -> None:
        """A Claude Code model kept by its alias ("opus") is kept by the name
        of the model it means (claude-opus-5-5): the list offers names, so
        the setting shows the model that answers."""
        from .providers.claude_code_provider import ALIASES

        clients = ("claude_code", "claude_desktop")
        try:
            if (self.config.get("primary_provider") in clients
                    and self.config.get("primary_model") in ALIASES):
                self.config.set("primary_model", ALIASES[self.config.get("primary_model")])
            chain = self.config.get("model_chain") or []
            if isinstance(chain, list) and any(
                    isinstance(one, dict) and one.get("provider") in clients
                    and one.get("model") in ALIASES for one in chain):
                self.config.set("model_chain", [
                    dict(one, model=ALIASES[one["model"]])
                    if isinstance(one, dict) and one.get("provider") in clients
                    and one.get("model") in ALIASES else one
                    for one in chain])
            for key in ("heartbeat_model_override", "data_heartbeat_model_override",
                        "scheduled_task_model_override"):
                said = str(self.config.get(key) or "")
                provider, _, model = said.partition("/")
                if provider in clients and model in ALIASES:
                    self.config.set(key, f"{provider}/{ALIASES[model]}")
        except Exception:                                   # pragma: no cover
            log.warning("could not name the Claude Code model", exc_info=True)

    def _desktop_chosen_before(self) -> None:
        """The desktop application's client, chosen before it was a model of
        its own (local_client = desktop): what was Claude Code there is now
        the desktop application."""
        try:
            if str(self.config.get("local_client") or "") != "desktop":
                return
            if self.config.get("primary_provider") == "claude_code":
                self.config.set("primary_provider", "claude_desktop")
            chain = self.config.get("model_chain") or []
            if isinstance(chain, list) and any(
                    isinstance(one, dict) and one.get("provider") == "claude_code" for one in chain):
                self.config.set("model_chain", [
                    dict(one, provider="claude_desktop")
                    if isinstance(one, dict) and one.get("provider") == "claude_code" else one
                    for one in chain])
            self.config.set("local_client", "auto")
        except Exception:                                   # pragma: no cover
            log.warning("could not carry the desktop client over", exc_info=True)

    def chain(self, role: str = "primary") -> List[ResolvedModel]:
        """THE MODELS, IN ORDER (Settings > General > Models): each call goes
        to the first that works (`invoke`). From before the list: the primary
        model, then the fallback when it was turned on."""
        raw = self.config.get("model_chain") or []
        out: List[ResolvedModel] = []
        for one in raw if isinstance(raw, list) else []:
            if not isinstance(one, dict):
                continue
            provider_id = str(one.get("provider") or "").strip()
            model_id = str(one.get("model") or "").strip()
            if self.provider(provider_id) is not None and model_id:
                out.append(ResolvedModel(role, provider_id, model_id, False))
        if out:
            return out
        if self.config.get("model_chain_emptied"):
            return []
        out = [self._primary(role)]
        fallback = self._fallback(role)
        # Kept as it was set, the same model too: asked afresh, by its own check.
        if fallback is not None:
            out.append(ResolvedModel(role, fallback.provider_id, fallback.model_id, False))
        return out

    def set_chain(self, entries: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """The list, as the user ordered it: kept, and the first of it made the
        primary model too, for whatever reads that."""
        kept: List[Dict[str, str]] = []
        for one in entries or []:
            provider_id = str((one or {}).get("provider") or "").strip()
            model_id = str((one or {}).get("model") or "").strip()
            if self.provider(provider_id) is None:
                raise ValueError(f"no provider {provider_id!r}")
            if not model_id:
                raise ValueError(f"choose a model for {provider_id}")
            if any(k["provider"] == provider_id and k["model"] == model_id for k in kept):
                continue
            entry = {"provider": provider_id, "model": model_id}
            # How hard it thinks: one of the levels the model takes, or its
            # own default when none is chosen.
            effort = str((one or {}).get("effort") or "").strip()
            if effort and effort != "default":
                if effort not in self.efforts_of(provider_id, model_id):
                    raise ValueError(f"{provider_id}/{model_id} does not take the "
                                     f"reasoning effort {effort!r}")
                entry["effort"] = effort
            kept.append(entry)
        self.config.set("model_chain", kept)
        # EMPTIED BY THE USER, and so empty: not the models from before the
        # list (`chain`), which would come back as if never removed.
        self.config.set("model_chain_emptied", not kept, internal=True)
        if kept:
            self.config.set("primary_provider", kept[0]["provider"])
            self.config.set("primary_model", kept[0]["model"])
        self.invalidate()
        return kept

    def efforts_of(self, provider_id: str, model_id: str) -> List[str]:
        """The levels of thinking a model on the list may be asked for."""
        provider = self.provider(provider_id)
        if provider is None:
            return []
        if hasattr(provider, "reasoning_options"):
            try:
                return [one for one in provider.reasoning_options(model_id).get("efforts") or []
                        if one != "default"]
            except Exception:                           # pragma: no cover
                return []
        descriptor = next((m for m in provider.list_models() if m.id == model_id), None)
        if descriptor is None and hasattr(provider, "_efforts_of"):
            # Its catalog not read: as the model's family takes it.
            return provider._efforts_of(model_id)
        return list(getattr(descriptor, "reasoning_efforts", None) or [])

    def _as_configured(self, request: ModelRequest) -> ModelRequest:
        """The request as the settings have it: the model's own web search,
        unless the user chose the app's (Settings > Web search)."""
        if request.tools and str(self.config.get("web_search_with", "model")
                                 or "model") == "model":
            return replace(request, builtin_web=True)
        return request

    def effort_on_list(self, resolved: ResolvedModel) -> str:
        """The effort chosen for this model on the list, or ""."""
        for one in self.config.get("model_chain") or []:
            if (isinstance(one, dict) and one.get("provider") == resolved.provider_id
                    and one.get("model") == resolved.model_id):
                return str(one.get("effort") or "")
        return ""

    def resolve(self, role: str = "primary") -> ResolvedModel:
        override_key = _ROLE_OVERRIDE_KEYS.get(role)
        if override_key:
            parsed = self._parse_override(self.config.get(override_key, ""))
            if parsed:
                return ResolvedModel(role, parsed[0], parsed[1], True)
        chain = self.chain(role)
        # With the list emptied, what there was before it - only to be named:
        # `invoke` asks none.
        return chain[0] if chain else self._primary(role)

    def _primary(self, role: str = "primary") -> ResolvedModel:
        primary_provider = str(self.config.get("primary_provider"))
        primary_model = str(self.config.get("primary_model"))
        # The model list offers "provider/model", and the override settings use
        # that form, so the primary model may carry its provider too. Reading it
        # as a bare model id would send "anthropic/claude-opus-5" to Anthropic
        # as a model name.
        if "/" in primary_model:
            parsed = self._parse_override(primary_model)
            if parsed:
                primary_provider, primary_model = parsed
            else:
                primary_model = primary_model.split("/", 1)[1].strip()
        if self.provider(primary_provider) is None:
            primary_provider, primary_model = "offline", "offline-1"
        return ResolvedModel(role, primary_provider, primary_model, False)

    # -- availability -------------------------------------------------------
    def _emit_state(self, resolved: ResolvedModel, availability: ModelAvailability) -> None:
        key = resolved.label
        previous = self._last_state.get(key)
        state = availability.state
        if previous == state:
            return
        self._last_state[key] = state
        topic = _STATE_EVENTS.get(state, "model.unavailable")
        payload = {
            "provider": resolved.provider_id,
            "model": resolved.model_id,
            "role": resolved.role,
            "previous_state": previous.value if previous else None,
            **availability.to_dict(),
        }
        if self.bus is not None:
            self.bus.publish(topic, payload)
            if previous is not None and state is AvailabilityState.AVAILABLE:
                self.bus.publish("model.recovered", payload)  # section 92
        if self.observability is not None:
            self.observability.set_state(f"model.{resolved.role}", payload)

    def reconcile_primary(self) -> Dict[str, Any]:
        """Make the configured provider and model a pair that can work.

        Section 82, as amended. This runs at startup and after anything that
        can change the provider, because the mismatch is not only created by a
        settings edit: a configuration written before, or by authenticating with
        a different provider, leaves the previous provider's model in place and
        every role resolves to something the provider has never heard of.
        """
        provider_id = str(self.config.get("primary_provider") or "")
        configured = str(self.config.get("primary_model") or "")
        # The Settings picker exposes provider-qualified model values. Selecting
        # one is a provider choice too: keeping the old provider and later
        # repairing the impossible pair back to its first model silently turns
        # a deliberate Codex choice into offline/offline-1.
        embedded = self._parse_override(configured) if "/" in configured else None
        if embedded:
            provider_id, model_id = embedded
            if self.model_belongs_to_provider(provider_id, model_id):
                changes: Dict[str, Any] = {"was": configured}
                if self.config.get("primary_provider") != provider_id:
                    self.config.set("primary_provider", provider_id)
                    changes["primary_provider"] = provider_id
                if configured != model_id:
                    self.config.set("primary_model", model_id)
                    changes["primary_model"] = model_id
                return changes
            # A provider-qualified value can outlive its provider catalogue.
            # Keep the provider the user chose, then repair the unavailable
            # model below instead of reverting to the previously selected one.
            self.config.set("primary_provider", provider_id)
            configured = model_id

        provider = self.provider(provider_id)
        if provider is None:
            return {}
        model_id = configured
        if model_id and self.model_belongs_to_provider(provider_id, model_id):
            if configured != model_id:
                self.config.set("primary_model", model_id)
                return {"primary_model": model_id, "was": configured}
            return {}
        try:
            models = provider.list_models()
        except Exception:                          # pragma: no cover - discovery failed
            return {}
        if not models:
            return {}
        replacement = models[0].id
        self.config.set("primary_model", replacement)
        log.warning("primary model %r is not offered by %r; using %r instead",
                    configured, provider_id, replacement)
        return {"primary_model": replacement, "was": configured}

    def cached_availability(self, resolved: "ResolvedModel"):
        """What is already known about a model, without checking (section 7.5)."""
        cached = self._availability.get(resolved.label)
        return cached[1] if cached else None

    def refused_since(self, role: str, seconds: float) -> str:
        """The state a role's model was last refused with, if that was within
        the last `seconds` - a rate limit, an exhausted quota, an
        unreachable provider - else "". The same for every provider: it is
        what each call left behind, not a question asked of the provider."""
        try:
            resolved = self.resolve(role)
        except Exception:            # pragma: no cover - nothing configured
            return ""
        with self._lock:
            cached = self._availability.get(resolved.label)
        if not cached:
            return ""
        at, availability = cached
        if availability.available or time.time() - at > seconds:
            return ""
        return str(getattr(availability.state, "value", availability.state))

    def model_belongs_to_provider(self, provider_id: str, model_id: str) -> bool:
        """Whether this model id may be sent to this provider.

        The provider decides. For most that is "it is in my list"; for one
        whose list is a cached picker hint (Codex), a newer model missing
        from a stale cache is still sent, and the client - the authority on
        what the account may use - answers for it.
        """
        provider = self.provider(provider_id)
        if provider is None:
            return False
        try:
            return provider.accepts_model_id(model_id)
        except Exception:                          # discovery is best effort
            return True

    def _misconfigured(self, resolved: ResolvedModel) -> Optional[ModelAvailability]:
        """A model that does not belong to its provider is a settings mistake.

        Sending it anyway produces a bare 404 naming a model the user never
        chose - the provider has no idea what "offline-1" is - which reads as a
        fault in the agent rather than as a setting to correct.
        """
        if self.model_belongs_to_provider(resolved.provider_id, resolved.model_id):
            return None
        provider = self.provider(resolved.provider_id)
        try:
            choices = ", ".join(d.id for d in provider.list_models()[:4]) if provider else ""
        except Exception:                          # pragma: no cover - discovery failed
            choices = ""
        reason = (f"{resolved.model_id!r} is not a model offered by "
                  f"{resolved.provider_id!r}")
        if choices:
            reason += f". Choose one of: {choices}"
        return ModelAvailability(
            available=False,
            state=AvailabilityState.MODEL_UNAVAILABLE,
            reason=reason,
            authentication_valid=True,
            failure_class=FailureClass.USER_ACTION_REQUIRED,
        )

    def check_availability(self, role: str = "primary", force: bool = False) -> ModelAvailability:
        return self._check(self.resolve(role), force)

    def _check(self, resolved: ResolvedModel, force: bool = False) -> ModelAvailability:
        mistake = self._misconfigured(resolved)
        if mistake is not None:
            self._emit_state(resolved, mistake)
            return mistake
        key = resolved.label
        now = time.time()
        with self._lock:
            cached = self._availability.get(key)
            if cached and not force and (now - cached[0]) < self.availability_ttl:
                return cached[1]
        provider = self.provider(resolved.provider_id)
        if provider is None:
            availability = ModelAvailability(
                available=False,
                state=AvailabilityState.PROVIDER_UNAVAILABLE,
                reason=f"provider {resolved.provider_id} is not registered",
                failure_class=FailureClass.USER_ACTION_REQUIRED,
            )
        else:
            try:
                availability = provider.check_availability(resolved.model_id)
            except Exception as exc:  # provider adapters must not leak
                log.debug("availability check raised", exc_info=True)
                availability = ModelAvailability(
                    available=False,
                    state=AvailabilityState.UNKNOWN_ERROR,
                    reason=str(exc)[:300],
                    failure_class=FailureClass.UNKNOWN,
                )
        with self._lock:
            self._availability[key] = (now, availability)
        self._emit_state(resolved, availability)
        return availability

    def invalidate(self, role: Optional[str] = None) -> None:
        with self._lock:
            if role is None:
                self._availability.clear()
            else:
                self._availability.pop(self.resolve(role).label, None)

    # -- fallback (section 94) ---------------------------------------------
    def _fallback(self, role: str) -> Optional[ResolvedModel]:
        if not bool(self.config.get("model_fallback_enabled")):
            return None
        parsed = self._parse_override(self.config.get("model_fallback_target", ""))
        if not parsed:
            return None
        return ResolvedModel(role, parsed[0], parsed[1], True)

    # -- invocation ---------------------------------------------------------
    def invoke(self, request: ModelRequest, role: str = "primary") -> ModelResponse:
        """ONE CALL, DOWN THE LIST: to the first model that works. One that
        cannot answer as things stand (`switches`) - known so already, or
        found so now - passes the call to the next; a passing failure is
        raised, for the caller to try again. When none can, the first one's
        failure is raised: it is the one the user put first."""
        resolved = self.resolve(role)
        candidates = [resolved] if resolved.is_override else self.chain(role)
        if not candidates:
            raise ModelUnavailable(resolved, ModelAvailability(
                False, AvailabilityState.MODEL_UNAVAILABLE,
                reason="there is no model on your list - add one in Settings > General > Models",
                failure_class=FailureClass.USER_ACTION_REQUIRED))
        if getattr(request, "sealed_reading", False):
            confined = [one for one in candidates
                        if one.provider_id not in READS_UNCONFINED]
            if not confined:
                raise ModelUnavailable(candidates[0], ModelAvailability(
                    False, AvailabilityState.PERMISSION_DENIED,
                    reason=("this run's folders are kept apart from one another, and "
                            f"{candidates[0].label} reads by itself where nothing keeps "
                            "it out - put Claude Code or a model reached by its API on "
                            "your list of models (Settings > General) for this run"),
                    failure_class=FailureClass.USER_ACTION_REQUIRED))
            if len(confined) != len(candidates):
                request.emit_debug("model_resolution", {
                    "role": role, "requested": candidates[0].to_dict(),
                    "chosen": confined[0].to_dict(), "fallback_used": True,
                    "decision": "passed over: it reads where this run may not"})
            candidates = confined
        first: Optional[ModelUnavailable] = None
        for at, one in enumerate(candidates):
            last = at == len(candidates) - 1
            try:
                response = self._invoke_one(request, role, one, passed_on=at > 0)
            except ModelUnavailable as exc:
                first = first or exc
                if last or not switches(exc.availability):
                    if first is not exc and not switches(exc.availability):
                        raise
                    raise first
                log.info("%s cannot answer (%s): the next on the list, %s",
                         one.label, exc.availability.state.value, candidates[at + 1].label)
                request.emit_debug("model_resolution", {
                    "role": role, "requested": one.to_dict(),
                    "availability": exc.availability.to_dict(),
                    "chosen": candidates[at + 1].to_dict(), "fallback_used": True,
                    "decision": "next on the list"})
                continue
            self.answered[role] = one.label
            return response
        raise first or RuntimeError("no model on the list")    # pragma: no cover

    def _invoke_one(self, request: ModelRequest, role: str, resolved: ResolvedModel,
                    passed_on: bool = False) -> ModelResponse:
        requested = resolved
        # The role's own model through `check_availability`, as asked of it
        # anywhere else; the others on the list by their own check.
        availability = (self._check(resolved) if passed_on
                        else self.check_availability(role))
        fallback_used = passed_on
        # A condition the user must act on - no credential, an invalid one, a
        # model the provider does not offer - means the request cannot work,
        # and saying so is better than failing at the provider.
        #
        # A temporary one does not. Refusing a request the user just made
        # because an earlier probe saw a rate limit answers with stale news:
        # the probe costs the same quota as the attempt, the limit may already
        # have passed, and the attempt is the authoritative answer. So try it,
        # and let the provider decide (sections 80, 89).
        if not availability.available and availability.requires_user_action():
            # What was refused, and why, before the refusal itself: a watcher
            # reading only the error sees the reason and never which model
            # was asked. The next on the list answers (`invoke`).
            request.emit_debug("model_resolution", {
                "role": role, "requested": requested.to_dict(),
                "availability": availability.to_dict(), "chosen": None,
                "fallback_used": False, "decision": "denied",
            })
            raise ModelUnavailable(resolved, availability)
        elif not availability.available:
            log.info("attempting %s despite a temporary condition (%s)",
                     resolved.label, availability.state.value)
        provider = self.provider(resolved.provider_id)
        if provider is None:
            request.emit_debug("model_resolution", {
                "role": role, "requested": requested.to_dict(),
                "availability": availability.to_dict(),
                "chosen": resolved.to_dict(), "fallback_used": fallback_used,
                "decision": "provider_unavailable",
            })
            raise ModelUnavailable(
                resolved,
                ModelAvailability(False, AvailabilityState.PROVIDER_UNAVAILABLE,
                                  reason="provider not registered",
                                  failure_class=FailureClass.USER_ACTION_REQUIRED),
            )
        request.emit_debug("model_resolution", {
            "role": role, "requested": requested.to_dict(),
            "availability": availability.to_dict(),
            "chosen": resolved.to_dict(), "fallback_used": fallback_used,
            "decision": "fallback" if fallback_used else "attempt",
        })
        # How hard the primary model thinks goes with the primary model: a
        # role that inherits it inherits its effort too. A role with a model
        # of its own, or the fallback, keeps that model's defaults - an
        # effort chosen for one model is not a statement about another.
        # Applied to a copy, so the caller's request is never changed.
        # HOW HARD IT THINKS: as chosen for this model on the list. From
        # before the list, the primary Codex model's effort.
        effort = self.effort_on_list(resolved)
        if not effort and not self.config.get("model_chain"):
            effort = str(self.config.get("primary_reasoning_effort", "default") or "default")
            # Not the fallback's: chosen for the primary model alone.
            if resolved.provider_id != "codex" or resolved.is_override or fallback_used:
                effort = ""
        if effort and effort != "default" and request.reasoning_effort is None:
            request = replace(request, reasoning_effort=effort)
        request = self._as_configured(request)
        # WHICH LANGUAGE IT WRITES IN, as the call said it (`language=`):
        # said first in the system prompt, whole - the display language and
        # each exception to it (Language.compose) - the one place the words
        # are made.
        if request.language is not None:
            request = replace(request, system=request.language.compose(
                request.system, self.config) or None)
        # A MODEL THAT READS TEXT ONLY is given no picture: each is said
        # where it was, its words read here being in the text already
        # (models/vision.py, runtime/documents.py).
        from .vision import sees_images, without_pictures
        if not sees_images(resolved.provider_id, resolved.model_id, self.config) and any(
                isinstance(message.get("content"), list) for message in request.messages):
            request = replace(request, messages=[
                dict(message, content=without_pictures(message.get("content")))
                for message in request.messages])
        started = time.perf_counter()
        try:
            with self.tracer.span(
                "model.invoke", event_type="model.invoke",
                connection_id=self.ROLE_CONNECTIONS.get(role),
                inputs={"model": resolved.label, "task": request.task,
                        "messages": len(request.messages)},
                metadata={"role": role, "model": resolved.label, "kind": "model"},
            ) as span:
                response = provider.invoke(request, resolved.model_id)
                span.output_summary = (
                    f"{response.usage.total_tokens} tokens, "
                    f"stop={response.stop_reason}"
                )
        except ModelInvocationError as exc:
            latency = (time.perf_counter() - started) * 1000
            self.tracer.event(
                "model.unavailable", severity="error", status="error",
                connection_id=self.ROLE_CONNECTIONS.get(role),
                error=str(exc)[:200], event_type="model.unavailable",
                metadata={"role": role, "model": resolved.label,
                          "state": exc.availability.state.value},
            )
            with self._lock:
                self._availability[resolved.label] = (time.time(), exc.availability)
            self._emit_state(resolved, exc.availability)
            if self.observability is not None:
                self.observability.record_model_call(
                    provider=resolved.provider_id, model=resolved.model_id, task=request.task,
                    input_tokens=0, output_tokens=0, latency_ms=latency, ok=False,
                    error=str(exc)[:200],
                    **({"language": _language_of(request)}
                       if _takes(self.observability.record_model_call, "language") else {}),
                    **({"by_user": _by_user(request)}
                       if _takes(self.observability.record_model_call, "by_user") else {}),
                )
            raise ModelUnavailable(resolved, exc.availability) from exc

        latency = (time.perf_counter() - started) * 1000
        good = ModelAvailability(available=True, state=AvailabilityState.AVAILABLE,
                                 authentication_valid=True,
                                 failure_class=FailureClass.TEMPORARY)
        with self._lock:
            self._availability[resolved.label] = (time.time(), good)
        self._emit_state(resolved, good)
        if self.observability is not None:
            descriptor = next(
                (m for m in provider.list_models() if m.id == resolved.model_id), None
            )
            usage = response.usage
            cached = int(getattr(usage, "cached_input_tokens", 0) or 0)
            written = int(getattr(usage, "cache_written_tokens", 0) or 0)
            # WHICH MODEL ANSWERED, by its own name where the provider gives
            # it ("claude-opus-5-5" for "opus"), and WHAT IT COST: as the
            # provider says, else at the model's list price (prices.py).
            answered = str(getattr(response, "model", "") or "") or resolved.model_id
            cost = getattr(usage, "cost_usd", None)
            if cost is None:
                cost = estimate(answered, usage.input_tokens, usage.output_tokens,
                                cached, written, write_times=cache_write_times(resolved.provider_id))
            if cost is None and descriptor and descriptor.input_cost_per_mtok is not None:
                # Input read from the cache is billed at a tenth of the input
                # price and input written to it at a quarter more. Left out,
                # a cached call looked almost free: `input_tokens` is only
                # what was neither, a handful on a prompt that repeats.
                price_in = descriptor.input_cost_per_mtok or 0
                cost = round(
                    (usage.input_tokens + 0.1 * cached + 1.25 * written) / 1e6 * price_in
                    + usage.output_tokens / 1e6 * (descriptor.output_cost_per_mtok or 0),
                    6,
                )
            figures = dict(
                provider=resolved.provider_id, model=answered, task=request.task,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                latency_ms=latency, ok=True, estimated_cost=cost,
            )
            # The cached input goes along wherever it can be written down.
            # It was dropped here: 4,139 calls on one machine recorded 11,084
            # input tokens between them, because the input that mattered -
            # read from the cache or written to it - was never passed on.
            if _takes(self.observability.record_model_call, "cached_input_tokens"):
                figures.update(cached_input_tokens=cached,
                               cache_written_tokens=written)
            # And which language it was told to write in: what was sent, for
            # the Language debugger to set beside what the code says.
            if _takes(self.observability.record_model_call, "language"):
                figures.update(language=_language_of(request))
            # And whether the user started the run it was made in.
            if _takes(self.observability.record_model_call, "by_user"):
                figures.update(by_user=_by_user(request))
            self.observability.record_model_call(**figures)
        return response

    # -- settings surface ---------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        providers = []
        for provider in self.providers():
            try:
                auth = provider.get_auth_status().to_dict()
            except Exception as exc:
                auth = {"state": CredentialState.ERROR.value, "detail": str(exc)[:200]}
            providers.append(
                {
                    **provider.capability_model(),
                    "models": [m.to_dict() for m in provider.list_models()],
                    "auth_status": auth,
                    "usage": provider.usage().to_dict(),
                }
            )
        roles = {}
        for role in ROLES:
            resolved = self.resolve(role)
            cached = self._availability.get(resolved.label)
            # A model that does not belong to its provider is knowable without
            # asking anyone, so report it rather than "not checked": it is a
            # setting to correct, and waiting for a probe to say so hides it.
            availability = cached[1] if cached else self._misconfigured(resolved)
            roles[role] = {
                **resolved.to_dict(),
                "availability": availability.to_dict() if availability else None,
            }
        chain = []
        for one in self.chain("primary"):
            provider = self.provider(one.provider_id)
            cached = self._availability.get(one.label)
            availability = cached[1] if cached else self._misconfigured(one)
            try:
                auth = provider.get_auth_status().to_dict() if provider else {}
            except Exception as exc:          # pragma: no cover - said as an error
                auth = {"state": CredentialState.ERROR.value, "detail": str(exc)[:200]}
            from .vision import sees_images
            chain.append({**one.to_dict(),
                          # Whether it is given pictures, or their words only.
                          "sees_images": sees_images(one.provider_id, one.model_id,
                                                     self.config),
                          "effort": self.effort_on_list(one),
                          "efforts": self.efforts_of(one.provider_id, one.model_id),
                          "display_name": getattr(provider, "display_name", one.provider_id),
                          "kind": getattr(provider, "kind", "api"), "auth_status": auth,
                          "availability": availability.to_dict() if availability else None})
        return {
            "providers": providers,
            "chain": chain,
            "answered": self.answered.get("primary", ""),
            "roles": roles,
            "fallback_enabled": bool(self.config.get("model_fallback_enabled")),
            "fallback_target": self.config.get("model_fallback_target"),
            "credential_backend": self.credentials.describe(),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }


def _by_user(request: Any) -> bool:
    """Whether the call is made by a run the user started with a button."""
    try:
        from ..common.observability.started import by_user
        return by_user(getattr(request, "task", ""))
    except Exception:                    # pragma: no cover - measurement
        return False


def _language_of(request: Any) -> str:
    """Which language a call was told to write in, as JSON rows - "" when
    it was not told."""
    language = getattr(request, "language", None)
    if language is None:
        return ""
    try:
        import json
        return json.dumps(language.rules(), ensure_ascii=False)
    except Exception:                    # pragma: no cover - measurement
        return ""


def _takes(function: Any, name: str) -> bool:
    """Whether a callable accepts a keyword of this name.

    The recorder lives with the observability layer and learns new figures
    on its own schedule; passing one it does not take would fail the call
    being recorded, which is a far worse outcome than a missing figure.
    """
    import inspect

    try:
        parameters = inspect.signature(function).parameters
    except (TypeError, ValueError):
        return False
    return name in parameters or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())
