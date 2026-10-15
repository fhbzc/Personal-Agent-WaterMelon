"""Integration ingest provider (sections 18, 23, 24).

External integrations - a VS Code extension, a custom application - do not
run inside the agent process.  They authenticate through the application
protocol (sections 61-65) and push already normalized events to the local
API, which hands them to this provider.

The provider is therefore the in-process endpoint of the plugin system: it
enforces the event contract, tags provenance, and emits through the same
pipeline as any built in provider.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...common.events.models import ListenerEvent, parse_iso
from ..core.provider import ListenerProvider, PluginManifest


class IngestProvider(ListenerProvider):
    def __init__(
        self,
        config: Any,
        provider_id: str = "app_integration",
        category: str = "integration",
        allowed_prefixes: Optional[List[str]] = None,
        description: str = "",
    ) -> None:
        super().__init__()
        self.config = config
        self.provider_id = provider_id
        self.category = category
        self.allowed_prefixes = allowed_prefixes or ["integration."]
        self.description = description
        self._active = False
        self._lock = threading.RLock()
        self._counts: Dict[str, int] = {}
        self._last_seen: Optional[str] = None

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        self._active = True

    def stop(self) -> None:
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    # -- ingestion ----------------------------------------------------------
    def accept(self, payload: Dict[str, Any], agent_id: Optional[str] = None) -> ListenerEvent:
        """Validate and emit one externally supplied event."""
        if not self._active:
            raise PermissionError(f"{self.provider_id} is not enabled")
        event_type = str(payload.get("event_type") or "").strip()
        if not event_type:
            raise ValueError("event_type is required")
        if not any(event_type.startswith(prefix) for prefix in self.allowed_prefixes):
            raise ValueError(
                f"event_type must start with one of {self.allowed_prefixes}"
            )
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            raise ValueError("data must be an object")
        event = ListenerEvent(
            event_type=event_type,
            source=self.provider_id,
            application=payload.get("application"),
            timestamp=parse_iso(payload.get("timestamp")) or datetime.now(timezone.utc),
            data=data,
            metadata={
                **(payload.get("metadata") or {}),
                "integration": payload.get("integration") or "unknown",
                "authenticated_agent": agent_id,
            },
        )
        with self._lock:
            self._counts[event_type] = self._counts.get(event_type, 0) + 1
            self._last_seen = event.timestamp.isoformat()
        self.emit(event)
        return event

    # -- interface ----------------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": self._active,
                "accepted_events": dict(self._counts),
                "last_seen": self._last_seen,
                "note": "events arrive from authenticated external integrations",
            }

    def supported_event_types(self) -> List[str]:
        return [f"{prefix}*" for prefix in self.allowed_prefixes]

    def get_capabilities(self) -> Dict[str, Any]:
        return {
            "push_based": True,
            "requires_authentication": True,
            "allowed_prefixes": self.allowed_prefixes,
        }

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            plugin_id=self.provider_id,
            version="1.0.0",
            supported_platforms=["windows", "macos", "linux"],
            supported_event_types=self.supported_event_types(),
            required_permissions=["protocol_authentication"],
            configuration_schema={},
            description=self.description or "Endpoint for external integration plugins.",
        )
