"""Listener provider interface and plugin descriptor (sections 22 and 24).

A provider observes, normalizes and emits.  It must not interpret, and it must
never touch Data Management storage or the Knowledge Graph.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ...common.events.models import ListenerEvent

log = logging.getLogger(__name__)

EmitFn = Callable[[ListenerEvent], None]


@dataclass
class PluginManifest:
    """Section 24 - what every listener plugin has to describe."""

    plugin_id: str
    version: str
    supported_platforms: List[str]
    supported_event_types: List[str]
    required_permissions: List[str] = field(default_factory=list)
    configuration_schema: Dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ListenerProvider(ABC):
    """Section 22."""

    provider_id: str = "provider"
    category: str = "generic"  # browser | filesystem | application | integration

    def __init__(self) -> None:
        self._emit: Optional[EmitFn] = None
        self._error: Optional[str] = None

    # -- wiring -------------------------------------------------------------
    def bind(self, emit: EmitFn) -> None:
        self._emit = emit

    def emit(self, event: ListenerEvent) -> None:
        if self._emit is None:
            log.debug("provider %s emitted before being bound", self.provider_id)
            return
        self._emit(event)

    # -- interface ----------------------------------------------------------
    @abstractmethod
    def start(self) -> None:
        ...

    @abstractmethod
    def stop(self) -> None:
        ...

    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        ...

    @abstractmethod
    def supported_event_types(self) -> List[str]:
        ...

    @abstractmethod
    def get_capabilities(self) -> Dict[str, Any]:
        ...

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            plugin_id=self.provider_id,
            version="1.0.0",
            supported_platforms=["windows", "macos", "linux"],
            supported_event_types=self.supported_event_types(),
        )

    def settings_changed(self) -> None:
        """Re-read anything this provider decided at startup.

        Called while it is running, whenever a listener setting changes. The
        default does nothing, which is right for a provider that reads its
        settings on every round; one that reads them once, at start, overrides this.
        """

    # -- helpers ------------------------------------------------------------
    def record_error(self, message: str) -> None:
        self._error = message
        log.warning("listener provider %s: %s", self.provider_id, message)

    @property
    def last_error(self) -> Optional[str]:
        return self._error
