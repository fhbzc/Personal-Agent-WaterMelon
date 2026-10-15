"""Model providers that speak the OpenAI chat API.

Most model services other than Anthropic take the same request: OpenAI
itself, Google's Gemini (through its OpenAI-compatible endpoint), DeepSeek,
Moonshot (Kimi), Alibaba's Qwen, Zhipu's GLM, xAI, Mistral, Groq,
SiliconFlow, Together - and the relays that front many models with one key,
OpenRouter first among them. One provider class serves them all, one
instance per service (`PRESETS`), each registered as a provider of its own
so it is chosen, and holds its key, as any other is; and one more for any
other service that speaks the same API, at an address the user gives.

The conversation is kept as the rest of the application keeps it - in
Anthropic's shape: text, tool_use and tool_result blocks - and translated
to the chat API on the way out and back on the way in, so a run, a review
or a heartbeat neither knows nor cares which service answers.

THE SAME TEXT IS READ AGAIN AND AGAIN. A work run sends its system prompt,
its tools and every turn before this one with each call - a two-hour run
read some nineteen million tokens of input, nearly all of it read before.
Billed by the token, input a service serves from its PROMPT CACHE costs
about a tenth. So it is cached wherever the service offers it (`caching`):
most cache a repeated beginning on their own ("auto") and say how much they
served from it, which is counted apart; through OpenRouter, Claude and
Gemini cache only what the request marks, so the system prompt and the
newest message are marked ("marked") - as the Anthropic provider asks for
it. A service at the user's own address may or may not ("unknown").

Only the standard library is used: nothing to install, and nothing that
can be out of date with the service. The key is kept in the credential
store, never shown again, and never put in a request's debug copy.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

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

log = logging.getLogger(__name__)

#: How long one call may take - a long answer with tools is minutes - and
#: how long a check that Settings waits on may.
CALL_TIMEOUT_SECONDS = 600.0
CHECK_TIMEOUT_SECONDS = 15.0
#: A model list that could not be read is not asked for again before this.
DISCOVERY_RETRY_SECONDS = 120.0


#: HOW HARD A MODEL THINKS, where the service lets it be set: the levels
#: each family takes. A model not listed takes none - its effort is not sent.
OPENAI_EFFORTS = ["minimal", "low", "medium", "high"]
O_SERIES_EFFORTS = ["low", "medium", "high"]
GEMINI_EFFORTS = ["low", "medium", "high"]
GROK_MINI_EFFORTS = ["low", "high"]


def efforts_for(provider_id: str, model: str, item: Optional[Dict[str, Any]] = None
                ) -> List[str]:
    """The reasoning efforts `model` of `provider_id` takes: OpenAI's GPT-5
    and o-series, Gemini 2.5 and later, Grok 3 mini - and on OpenRouter,
    any model its catalog says takes reasoning."""
    name = model.lower().rsplit("/", 1)[-1]
    if provider_id == "openrouter":
        supported = (item or {}).get("supported_parameters") or []
        if "reasoning" in supported or "reasoning_effort" in supported:
            return list(O_SERIES_EFFORTS)
        return []
    if provider_id == "openai":
        if name.startswith("gpt-5") and "chat" not in name:
            return list(OPENAI_EFFORTS)
        if name[:2] in ("o1", "o3", "o4") and "mini-2024" not in name:
            return list(O_SERIES_EFFORTS)
        return []
    if provider_id == "google":
        if name.startswith(("gemini-2.5", "gemini-3")):
            return list(GEMINI_EFFORTS)
        return []
    if provider_id == "xai" and name.startswith("grok-3-mini"):
        return list(GROK_MINI_EFFORTS)
    return []


@dataclass(frozen=True)
class Preset:
    """One service: its id, its name, where its API is, the environment
    variables a key may already be in, and where a key is made."""

    provider_id: str
    display_name: str
    base_url: str
    env: Tuple[str, ...] = ()
    key_page: str = ""
    note: str = ""
    #: How it caches a repeated prompt: "auto" (on its own), "marked" (only
    #: what the request marks - for some of its models), "unknown".
    caching: str = "auto"


PRESETS: Tuple[Preset, ...] = (
    Preset("openrouter", "OpenRouter", "https://openrouter.ai/api/v1",
           ("OPENROUTER_API_KEY",), "https://openrouter.ai/keys",
           "One key for models of many companies.", caching="marked"),
    Preset("openai", "OpenAI", "https://api.openai.com/v1", ("OPENAI_API_KEY",),
           "https://platform.openai.com/api-keys"),
    Preset("google", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
           ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "https://aistudio.google.com/apikey"),
    Preset("deepseek", "DeepSeek", "https://api.deepseek.com/v1", ("DEEPSEEK_API_KEY",),
           "https://platform.deepseek.com/api_keys"),
    Preset("moonshot", "Moonshot (Kimi)", "https://api.moonshot.cn/v1", ("MOONSHOT_API_KEY",),
           "https://platform.moonshot.cn/console/api-keys",
           "Outside China: https://api.moonshot.ai/v1 as the address."),
    Preset("qwen", "Alibaba Qwen (DashScope)",
           "https://dashscope.aliyuncs.com/compatible-mode/v1", ("DASHSCOPE_API_KEY",),
           "https://bailian.console.aliyun.com/",
           "Outside China: https://dashscope-intl.aliyuncs.com/compatible-mode/v1."),
    Preset("zhipu", "Zhipu GLM", "https://open.bigmodel.cn/api/paas/v4",
           ("ZHIPUAI_API_KEY", "ZHIPU_API_KEY"), "https://open.bigmodel.cn/usercenter/apikeys"),
    Preset("xai", "xAI (Grok)", "https://api.x.ai/v1", ("XAI_API_KEY",), "https://console.x.ai"),
    Preset("mistral", "Mistral", "https://api.mistral.ai/v1", ("MISTRAL_API_KEY",),
           "https://console.mistral.ai/api-keys"),
    Preset("groq", "Groq", "https://api.groq.com/openai/v1", ("GROQ_API_KEY",),
           "https://console.groq.com/keys"),
    Preset("siliconflow", "SiliconFlow", "https://api.siliconflow.cn/v1",
           ("SILICONFLOW_API_KEY",), "https://cloud.siliconflow.cn/account/ak"),
    Preset("together", "Together AI", "https://api.together.xyz/v1", ("TOGETHER_API_KEY",),
           "https://api.together.ai/settings/api-keys"),
    Preset("custom_openai", "Other OpenAI-compatible API", "", (), "",
           "Any service that takes the OpenAI chat API: give its address.",
           caching="unknown"),
)


class OpenAICompatibleProvider(ModelProvider):
    """One service that speaks the OpenAI chat API (`Preset`)."""

    kind = "api"

    def __init__(self, credentials: Any, preset: Preset) -> None:
        self.credentials = credentials
        self.preset = preset
        self.provider_id = preset.provider_id
        self.display_name = preset.display_name
        self._lock = threading.RLock()
        self._models: Optional[List[ModelDescriptor]] = None
        self._discovery_failed_at = 0.0
        self._usage = UsageSnapshot()
        #: The service turned down "max_tokens" and asked for
        #: "max_completion_tokens" (OpenAI's newer models): not sent again.
        self._completion_tokens = False
        #: The service turned down the marks that ask for caching: not
        #: asked for again (`_marked`).
        self._marks_refused = False

    # -- where, and with what ------------------------------------------------
    @property
    def _key_name(self) -> str:
        return f"{self.provider_id}.api_key"

    @property
    def _base_name(self) -> str:
        return f"{self.provider_id}.base_url"

    def _api_key(self) -> Optional[str]:
        stored = self.credentials.get(self._key_name)
        if stored:
            return stored
        for name in self.preset.env:
            if os.environ.get(name):
                return os.environ[name]
        return None

    def base_url(self) -> str:
        """Where the API is: the address the user gave, else the service's."""
        given = self.credentials.get(self._base_name) or ""
        return (given or self.preset.base_url).strip().rstrip("/")

    def capability_model(self) -> Dict[str, Any]:
        return {**super().capability_model(), "kind": self.kind,
                "base_url": self.base_url(), "default_base_url": self.preset.base_url,
                "needs_base_url": not self.preset.base_url,
                "key_page": self.preset.key_page, "note": self.preset.note,
                "caching": self.preset.caching, "billed_per_token": True}

    # -- the HTTP ------------------------------------------------------------------
    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None,
                 timeout: float = CALL_TIMEOUT_SECONDS) -> Dict[str, Any]:
        base, key = self.base_url(), self._api_key()
        if not base:
            raise ModelInvocationError("no address is set for this API", ModelAvailability(
                available=False, state=AvailabilityState.AUTHENTICATION_REQUIRED,
                reason="no address is set for this API: give it in Settings",
                failure_class=FailureClass.USER_ACTION_REQUIRED))
        if not key:
            raise ModelInvocationError(f"no {self.display_name} API key", ModelAvailability(
                available=False, state=AvailabilityState.AUTHENTICATION_REQUIRED,
                reason=f"no {self.display_name} API key: add one in Settings",
                authentication_valid=False, failure_class=FailureClass.USER_ACTION_REQUIRED))
        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json",
                   "User-Agent": "personalized-agent"}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.provider_id == "openrouter":
            # OpenRouter's own attribution headers: optional, and harmless.
            headers["X-Title"] = "Personalized Agent"
        call = urllib.request.Request(base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(call, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            said = _error_text(exc)
            raise ModelInvocationError(f"{self.display_name}: HTTP {exc.code} {said}"[:500],
                                       self._verdict(exc.code, said, exc.headers)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            timed_out = "timed out" in str(reason).lower() or isinstance(exc, TimeoutError)
            raise ModelInvocationError(
                f"{self.display_name} " + ("did not answer in time" if timed_out
                                           else f"could not be reached: {reason}"),
                ModelAvailability(
                    available=False, state=AvailabilityState.NETWORK_UNAVAILABLE,
                    reason=("timed out" if timed_out else f"could not be reached: {reason}"),
                    failure_class=FailureClass.TEMPORARY)) from exc
        try:
            said = json.loads(raw.decode("utf-8") or "{}")
        except ValueError as exc:
            raise ModelInvocationError(f"{self.display_name} answered with no JSON",
                                       ModelAvailability(
                                           available=False,
                                           state=AvailabilityState.PROVIDER_UNAVAILABLE,
                                           reason="the answer was not JSON",
                                           failure_class=FailureClass.TEMPORARY)) from exc
        if isinstance(said, dict) and said.get("error") and not said.get("choices") \
                and not said.get("data"):
            # Some relays answer 200 with the error inside.
            error = said["error"] if isinstance(said["error"], dict) else {"message": said["error"]}
            code = int(error.get("code") or 0) if str(error.get("code") or "").isdigit() else 0
            text = str(error.get("message") or error)[:300]
            raise ModelInvocationError(f"{self.display_name}: {text}",
                                       self._verdict(code or 500, text, None))
        return said if isinstance(said, dict) else {}

    def _verdict(self, code: int, said: str, headers: Any) -> ModelAvailability:
        """What a failed call means: one the user must mend (a key, credit,
        a model not offered), or one that passes (busy, a rate limit, the
        service down)."""
        lowered = said.lower()

        def verdict(state: AvailabilityState, failure: FailureClass, reason: str,
                    auth_ok: bool = True, wait: Optional[str] = None) -> ModelAvailability:
            return ModelAvailability(available=False, state=state, reason=reason[:300],
                                     authentication_valid=auth_ok, provider_status=str(code),
                                     failure_class=failure, rate_limit_status=wait)

        if code == 401:
            return verdict(AvailabilityState.CREDENTIAL_INVALID, FailureClass.USER_ACTION_REQUIRED,
                           f"the {self.display_name} key was not accepted: {said}", False)
        if code == 402 or "insufficient_quota" in lowered or "insufficient credit" in lowered \
                or "insufficient balance" in lowered:
            return verdict(AvailabilityState.QUOTA_EXHAUSTED, FailureClass.USER_ACTION_REQUIRED,
                           f"the {self.display_name} account is out of credit: {said}")
        if code == 403:
            return verdict(AvailabilityState.PERMISSION_DENIED, FailureClass.USER_ACTION_REQUIRED,
                           f"{self.display_name} refused this request: {said}")
        if code == 404:
            return verdict(AvailabilityState.MODEL_UNAVAILABLE, FailureClass.USER_ACTION_REQUIRED,
                           f"{self.display_name} does not offer this model, or the address "
                           f"is wrong: {said}")
        if code == 429:
            wait = None
            if headers is not None:
                wait = headers.get("retry-after") or headers.get("x-ratelimit-reset-requests")
            return verdict(AvailabilityState.RATE_LIMITED, FailureClass.TEMPORARY,
                           f"{self.display_name} is rate limiting this key"
                           + (f"; it can be retried in about {wait}s" if wait else "")
                           + f": {said}", wait=str(wait) if wait else "unknown")
        if code in (408, 409, 425) or code >= 500:
            return verdict(AvailabilityState.PROVIDER_UNAVAILABLE, FailureClass.TEMPORARY,
                           f"{self.display_name} is unavailable for now (HTTP {code}): {said}")
        return verdict(AvailabilityState.UNKNOWN_ERROR, FailureClass.UNKNOWN,
                       f"{self.display_name} answered HTTP {code}: {said}")

    # -- ModelProvider -------------------------------------------------------------
    def list_models(self) -> List[ModelDescriptor]:
        """The service's models, as it lists them - read once it has a key,
        and a hint for the picker only (`accepts_model_id`)."""
        with self._lock:
            if self._models is not None:
                return list(self._models)
            recently = time.time() - self._discovery_failed_at < DISCOVERY_RETRY_SECONDS
        if recently or not self._api_key() or not self.base_url():
            return []
        try:
            said = self._request("GET", "/models", timeout=CHECK_TIMEOUT_SECONDS)
        except ModelInvocationError:
            log.debug("could not list the models of %s", self.provider_id, exc_info=True)
            with self._lock:
                self._discovery_failed_at = time.time()
            return []
        found = []
        for item in said.get("data") or said.get("models") or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            ident = str(item["id"])
            if ident.startswith("models/"):          # Gemini's own form of an id
                ident = ident[len("models/"):]
            found.append(ModelDescriptor(
                id=ident, display_name=str(item.get("name") or item.get("display_name") or ident),
                reasoning_efforts=efforts_for(self.provider_id, ident, item),
                context_window=_number(item.get("context_length") or item.get("context_window")),
                max_output_tokens=_number((item.get("top_provider") or {}).get(
                    "max_completion_tokens") if isinstance(item.get("top_provider"), dict)
                    else None)))
        found.sort(key=lambda one: one.id)
        with self._lock:
            self._models = found
        return list(found)

    def _efforts_of(self, model: str) -> List[str]:
        """The levels `model` takes: as the catalog read says, or - not read
        yet - as its family does."""
        with self._lock:
            listed = next((one for one in self._models or [] if one.id == model), None)
        if listed is not None:
            return list(listed.reasoning_efforts or [])
        return efforts_for(self.provider_id, model)

    def accepts_model_id(self, model: str) -> bool:
        # The list is the service's catalog of the moment: a model not in it
        # is the service's to refuse, not ours.
        return bool(str(model or "").strip())

    def get_auth_methods(self) -> List[AuthMethod]:
        return [AuthMethod.API_KEY]

    def get_auth_status(self) -> AuthStatus:
        if not self.base_url():
            return AuthStatus(CredentialState.NOT_CONFIGURED, None,
                              detail="no address given for this API")
        if self.credentials.has(self._key_name):
            return AuthStatus(CredentialState.AUTHENTICATED, AuthMethod.API_KEY,
                              detail="API key stored in the credential manager")
        for name in self.preset.env:
            if os.environ.get(name):
                return AuthStatus(CredentialState.AUTHENTICATED, AuthMethod.API_KEY,
                                  detail=f"{name} is set")
        return AuthStatus(CredentialState.NOT_CONFIGURED, None,
                          detail="no API key configured for this provider")

    def authenticate(self, config: Dict[str, Any]) -> AuthStatus:
        """An API key - and, for a service at another address, the address -
        stored, then tried. A key the service turns away is not kept."""
        base = str(config.get("base_url") or "").strip().rstrip("/")
        if base:
            if not base.lower().startswith(("https://", "http://")):
                return AuthStatus(CredentialState.INVALID, AuthMethod.API_KEY,
                                  detail="the address must start with https://")
            self.credentials.set(self._base_name, base)
        elif config.get("base_url") is not None and self.preset.base_url:
            # Cleared: the service's own address again.
            self.credentials.delete(self._base_name)
        api_key = str(config.get("api_key") or "").strip()
        if not api_key:
            return AuthStatus(CredentialState.INVALID, AuthMethod.API_KEY, detail="empty API key")
        if not self.base_url():
            return AuthStatus(CredentialState.INVALID, AuthMethod.API_KEY,
                              detail="give the address of the API too")
        self.credentials.set(self._key_name, api_key)
        with self._lock:
            self._models = None
            self._discovery_failed_at = 0.0
        probe = self.check_availability(str(config.get("model") or ""))
        if probe.available or probe.state is AvailabilityState.RATE_LIMITED:
            return AuthStatus(CredentialState.AUTHENTICATED, AuthMethod.API_KEY)
        if probe.state in (AvailabilityState.CREDENTIAL_INVALID,
                           AvailabilityState.AUTHENTICATION_REQUIRED):
            self.credentials.delete(self._key_name)
            return AuthStatus(CredentialState.INVALID, AuthMethod.API_KEY, detail=probe.reason)
        return AuthStatus(CredentialState.AUTHENTICATED, AuthMethod.API_KEY, detail=probe.reason)

    def logout(self) -> AuthStatus:
        self.credentials.delete(self._key_name)
        self.credentials.delete(self._base_name)
        with self._lock:
            self._models = None
        return AuthStatus(CredentialState.NOT_CONFIGURED)

    def check_availability(self, model: str) -> ModelAvailability:
        """Whether the key works: the service's model list, read - which
        costs nothing, as a call to a model would."""
        status = self.get_auth_status()
        if status.state is not CredentialState.AUTHENTICATED:
            return ModelAvailability(
                available=False, state=AvailabilityState.AUTHENTICATION_REQUIRED,
                reason=status.detail or f"no {self.display_name} API key configured",
                authentication_valid=False, failure_class=FailureClass.USER_ACTION_REQUIRED)
        try:
            self._request("GET", "/models", timeout=CHECK_TIMEOUT_SECONDS)
        except ModelInvocationError as exc:
            return exc.availability
        return ModelAvailability(available=True, state=AvailabilityState.AVAILABLE,
                                 authentication_valid=True, failure_class=FailureClass.TEMPORARY)

    def invoke(self, request: ModelRequest, model: str) -> ModelResponse:
        messages = to_chat(request)
        marked = self._marked(messages, model)
        body: Dict[str, Any] = {"model": model, "messages": marked}
        if self.provider_id == "openrouter":
            # Its usage whole - what was read from the cache, what it cost.
            body["usage"] = {"include": True}
        body["max_completion_tokens" if self._completion_tokens else "max_tokens"] = \
            int(request.max_tokens)
        effort = request.reasoning_effort
        if effort and effort in self._efforts_of(model):
            # OpenRouter takes it as its own object; the rest as OpenAI does.
            if self.provider_id == "openrouter":
                body["reasoning"] = {"effort": effort}
            else:
                body["reasoning_effort"] = effort
        if request.tools:
            body["tools"] = [{"type": "function", "function": {
                "name": tool.name, "description": tool.description,
                "parameters": tool.input_schema or {"type": "object", "properties": {}}}}
                for tool in request.tools]
        # What is sent, for a debugger watching this call: the key is in a
        # header, never in the body.
        request.emit_debug("provider_request", {"provider": self.provider_id, "model": model,
                                                "base_url": self.base_url(), "body": body})
        try:
            said = self._request("POST", "/chat/completions", body)
        except ModelInvocationError as exc:
            text = str(exc).lower()
            if marked is not messages and "cache_control" in text:
                # Marks it does not take: the request as it always was, and
                # not asked again.
                log.info("%s refused the caching marks; sending without them",
                         self.provider_id)
                self._marks_refused = True
                body["messages"] = messages
                said = self._request("POST", "/chat/completions", body)
                return self._response(said, model)
            if "max_completion_tokens" in text and "max_tokens" in body:
                # A newer model takes the other name for the same limit.
                self._completion_tokens = True
                body["max_completion_tokens"] = body.pop("max_tokens")
                said = self._request("POST", "/chat/completions", body)
            else:
                raise
        return self._response(said, model)

    def _marked(self, messages: List[Dict[str, Any]], model: str) -> List[Dict[str, Any]]:
        """THE CONVERSATION, MARKED FOR CACHING where the service caches only
        what is marked: through OpenRouter, a Claude or Gemini model - the
        system prompt and the newest message, so all before them is read
        from the cache on the next call. Elsewhere, as it is."""
        if self.preset.caching != "marked" or self._marks_refused:
            return messages
        lowered = str(model or "").lower()
        if not any(name in lowered for name in ("anthropic/", "claude", "google/gemini")):
            return messages
        out = [dict(one) for one in messages]

        def mark(message: Dict[str, Any]) -> None:
            content = message.get("content")
            if isinstance(content, str) and content:
                message["content"] = [{"type": "text", "text": content,
                                       "cache_control": {"type": "ephemeral"}}]
            elif isinstance(content, list):
                parts = [dict(part) for part in content]
                for part in reversed(parts):
                    if part.get("type") == "text":
                        part["cache_control"] = {"type": "ephemeral"}
                        break
                message["content"] = parts

        if out and out[0].get("role") == "system":
            mark(out[0])
        last = next((one for one in reversed(out) if one.get("role") in ("user", "tool")
                     and one.get("content")), None)
        if last is not None and last is not out[0]:
            if last.get("role") == "tool" and isinstance(last.get("content"), str):
                # A tool's message takes text only; marked as text parts.
                last["content"] = [{"type": "text", "text": last["content"],
                                    "cache_control": {"type": "ephemeral"}}]
            else:
                mark(last)
        return out

    def _response(self, said: Dict[str, Any], model: str) -> ModelResponse:
        choice = (said.get("choices") or [{}])[0] or {}
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            text = "".join(str(part.get("text") or "") for part in content
                           if isinstance(part, dict))
        else:
            text = str(content or "")
        calls: List[ToolCall] = []
        for one in message.get("tool_calls") or []:
            function = (one or {}).get("function") or {}
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except ValueError:
                arguments = {"_unparsed": str(function.get("arguments") or "")}
            calls.append(ToolCall(id=str(one.get("id") or f"call_{len(calls)}"),
                                  name=str(function.get("name") or ""),
                                  arguments=arguments if isinstance(arguments, dict)
                                  else {"value": arguments}))
        usage = said.get("usage") or {}
        prompt = int(usage.get("prompt_tokens") or 0)
        details = usage.get("prompt_tokens_details") or {}
        # Read from the cache: OpenAI's and OpenRouter's figure, DeepSeek's,
        # Moonshot's. Written to it: OpenRouter's, for a model that marks.
        cached = int(details.get("cached_tokens") or usage.get("prompt_cache_hit_tokens")
                     or usage.get("cached_tokens") or 0)
        written = int(details.get("cache_write_tokens") or 0)
        try:
            # OpenRouter says what it billed for the call.
            billed = float(usage["cost"]) if usage.get("cost") is not None else None
        except (TypeError, ValueError):
            billed = None
        counted = ModelUsage(input_tokens=max(0, prompt - cached - written),
                             output_tokens=int(usage.get("completion_tokens") or 0),
                             cached_input_tokens=cached, cache_written_tokens=written,
                             cost_usd=billed)
        with self._lock:
            self._usage.request_count += 1
            self._usage.input_tokens += counted.input_tokens
            self._usage.output_tokens += counted.output_tokens
            self._usage.total_tokens += counted.total_tokens
        # The answer kept as the rest of the conversation is: in blocks.
        raw: List[Dict[str, Any]] = []
        if text:
            raw.append({"type": "text", "text": text})
        raw += [{"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                for call in calls]
        finish = str(choice.get("finish_reason") or "")
        return ModelResponse(text=text.strip(), tool_calls=calls, usage=counted,
                             stop_reason={"tool_calls": "tool_use", "length": "max_tokens",
                                          "stop": "end_turn"}.get(finish, finish or None),
                             raw_content=raw or [{"type": "text", "text": ""}],
                             model=str(said.get("model") or model), provider=self.provider_id)

    def usage(self) -> UsageSnapshot:
        return self._usage


def to_chat(request: ModelRequest) -> List[Dict[str, Any]]:
    """THE CONVERSATION, AS THE CHAT API TAKES IT: a system message; text,
    and images as data addresses; an answer's tool calls as its tool_calls;
    each tool's result as a message of its own - an image a tool returned
    after them, from the user, since a tool message carries text only.
    A model's thinking is its own, and not sent back."""
    out: List[Dict[str, Any]] = []
    if request.system:
        out.append({"role": "system", "content": str(request.system)})
    for message in request.messages or []:
        role = message.get("role") or "user"
        content = message.get("content")
        if isinstance(content, str) or content is None:
            out.append({"role": role, "content": content or ""})
            continue
        parts: List[Dict[str, Any]] = []
        calls: List[Dict[str, Any]] = []
        results: List[Dict[str, Any]] = []
        images_after: List[Dict[str, Any]] = []
        for block in content if isinstance(content, list) else [content]:
            if not isinstance(block, dict):
                parts.append({"type": "text", "text": str(block)})
                continue
            kind = block.get("type")
            if kind == "text":
                parts.append({"type": "text", "text": str(block.get("text") or "")})
            elif kind == "image":
                url = _image_url(block)
                if url:
                    parts.append({"type": "image_url", "image_url": {"url": url}})
            elif kind == "tool_use":
                calls.append({"id": str(block.get("id") or ""), "type": "function",
                              "function": {"name": str(block.get("name") or ""),
                                           "arguments": json.dumps(block.get("input") or {},
                                                                   ensure_ascii=False)}})
            elif kind == "tool_result":
                text, images = _result_parts(block.get("content"))
                if block.get("is_error") and not text.startswith("Error"):
                    text = f"Error: {text}"
                results.append({"role": "tool", "tool_call_id": str(block.get("tool_use_id") or ""),
                                "content": text})
                images_after += images
            # thinking, redacted thinking: the model's own, not sent back.
        out += results
        if role == "assistant":
            said: Dict[str, Any] = {"role": "assistant",
                                    "content": "".join(p["text"] for p in parts
                                                       if p["type"] == "text") or None}
            if calls:
                said["tool_calls"] = calls
            if said["content"] is not None or calls:
                out.append(said)
        else:
            if images_after:
                parts = [{"type": "text", "text": "The images the tools returned:"},
                         *images_after, *parts]
            if parts:
                only_text = all(p["type"] == "text" for p in parts)
                out.append({"role": role, "content": "\n".join(p["text"] for p in parts)
                            if only_text else parts})
    return out


def _image_url(block: Dict[str, Any]) -> str:
    source = block.get("source") or {}
    if source.get("type") == "base64" and source.get("data"):
        return f"data:{source.get('media_type') or 'image/png'};base64,{source['data']}"
    if source.get("type") == "url" and source.get("url"):
        return str(source["url"])
    return ""


def _result_parts(content: Any) -> Tuple[str, List[Dict[str, Any]]]:
    if isinstance(content, str):
        return content, []
    texts, images = [], []
    for one in content or []:
        if isinstance(one, dict) and one.get("type") == "image":
            url = _image_url(one)
            if url:
                images.append({"type": "image_url", "image_url": {"url": url}})
        elif isinstance(one, dict):
            texts.append(str(one.get("text") or ""))
        else:
            texts.append(str(one))
    return "\n".join(texts), images


def _error_text(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8", "replace")
    except Exception:          # pragma: no cover - said without it
        return str(exc.reason or "")
    try:
        said = json.loads(raw)
    except ValueError:
        return raw.strip()[:300]
    error = said.get("error") if isinstance(said, dict) else None
    if isinstance(error, dict):
        return " ".join(str(error.get(key)) for key in ("type", "code", "message")
                        if error.get(key))[:300]
    return str(error or said)[:300]


def _number(value: Any) -> Optional[int]:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def providers(credentials: Any) -> List[OpenAICompatibleProvider]:
    """One provider for each service."""
    return [OpenAICompatibleProvider(credentials, preset) for preset in PRESETS]
