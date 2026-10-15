"""Offline rule based provider.

This is not a language model.  It exists so the whole system - listener,
graph, heartbeats, scheduler, UI - can be started, demonstrated and tested
without network access or credentials.  It answers the structured requests the
subsystems make (data heartbeat aggregation, heartbeat reasoning) from the
structured payload those subsystems already attach to the request, and returns
an explicit "offline provider" note for free form conversation.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List

from ..base import (
    AuthMethod,
    AuthStatus,
    AvailabilityState,
    CredentialState,
    FailureClass,
    ModelAvailability,
    ModelDescriptor,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    UsageSnapshot,
)

MODELS = [
    ModelDescriptor("offline-1", "Offline rule based model", 32_000, 4_096, 0.0, 0.0),
]


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class OfflineProvider(ModelProvider):
    provider_id = "offline"
    display_name = "Offline (no network)"
    kind = "offline"

    def __init__(self, credentials: Any = None) -> None:
        self.credentials = credentials
        self._usage = UsageSnapshot()

    def list_models(self) -> List[ModelDescriptor]:
        return list(MODELS)

    def get_auth_methods(self) -> List[AuthMethod]:
        return [AuthMethod.NONE]

    def get_auth_status(self) -> AuthStatus:
        return AuthStatus(CredentialState.AUTHENTICATED, AuthMethod.NONE,
                          detail="no credential required")

    def authenticate(self, config: Dict[str, Any]) -> AuthStatus:
        return self.get_auth_status()

    def logout(self) -> AuthStatus:
        return self.get_auth_status()

    def check_availability(self, model: str) -> ModelAvailability:
        known = any(m.id == model for m in MODELS)
        if not known:
            return ModelAvailability(
                available=False,
                state=AvailabilityState.MODEL_UNAVAILABLE,
                reason=f"unknown offline model {model}",
                authentication_valid=True,
                failure_class=FailureClass.USER_ACTION_REQUIRED,
            )
        return ModelAvailability(available=True, state=AvailabilityState.AVAILABLE,
                                 authentication_valid=True,
                                 failure_class=FailureClass.TEMPORARY)

    # -- structured responders ---------------------------------------------
    def _heartbeat(self, request: ModelRequest) -> str:
        context = request.metadata.get("context_summary", "")
        return json.dumps(
            {
                "state_analysis": context[:400] or "No graph context was available.",
                "actions": [],
                "checklist_update": None,
                "message_to_user": None,
            }
        )

    def _conversation(self, request: ModelRequest) -> str:
        last_user = ""
        for message in reversed(request.messages):
            if message.get("role") == "user":
                content = message.get("content")
                if isinstance(content, str):
                    last_user = content
                elif isinstance(content, list):
                    last_user = " ".join(
                        block.get("text", "") for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                break
        return (
            "[offline provider] No language model is configured, so I cannot reason about "
            "this request. Open Settings > Models to select a provider and sign in.\n\n"
            f"Received: {last_user.strip()[:400]}"
        )

    def invoke(self, request: ModelRequest, model: str) -> ModelResponse:
        # FOR TESTS OF REAL CHILD PROCESSES ONLY (tests/test_work_processes.py):
        # a run that lasts long enough to pause and stop, and a model that
        # asks the user to fix something. Set only in a test's environment -
        # a child process inherits it, and nothing else ever sets it.
        delay = os.environ.get("PA_TEST_OFFLINE_DELAY")
        if delay:
            time.sleep(float(delay))
        shut = os.environ.get("PA_TEST_OFFLINE_SHUT")
        if shut and os.path.exists(shut):
            from ..base import AvailabilityState, FailureClass, ModelAvailability,                 ModelInvocationError
            raise ModelInvocationError("the usage limit is used up (test)", ModelAvailability(
                available=False, state=AvailabilityState.QUOTA_EXHAUSTED,
                reason="the usage limit is used up (test)",
                failure_class=FailureClass.USER_ACTION_REQUIRED))
        if str(request.task or "").startswith("heartbeat"):
            text = self._heartbeat(request)
        else:
            text = self._conversation(request)
        prompt_text = json.dumps(request.messages, default=str) + (request.system or "")
        usage = ModelUsage(input_tokens=_estimate_tokens(prompt_text),
                           output_tokens=_estimate_tokens(text))
        self._usage.request_count += 1
        self._usage.input_tokens += usage.input_tokens
        self._usage.output_tokens += usage.output_tokens
        self._usage.total_tokens += usage.total_tokens
        return ModelResponse(text=text, usage=usage, stop_reason="end_turn",
                             model=model, provider=self.provider_id)

    def usage(self) -> UsageSnapshot:
        return self._usage
