"""Runtime system registry (sections 14.2, 14.3, 14.7, 14.16, 14.17, 14.18).

The Debugger draws its topology from this registry rather than from a hand
maintained diagram, so a component that registers itself appears in the System
Map automatically.  Each component supplies callables for its own status,
metrics and traces; nothing here reaches into another subsystem's internals.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# Section 14.3 / 14.17 - runtime states, including the ones that separate a
# component the user switched off from one that broke.
COMPONENT_STATES = (
    "starting", "running", "idle", "busy", "degraded", "blocked",
    "failed", "stopped", "disabled", "unavailable",
)

CONNECTION_STATES = ("healthy", "idle", "degraded", "blocked", "failed", "unavailable")

# Section 14.18 - the top level of the hierarchy.
SUBSYSTEMS = (
    ("frontend", "Frontend"),
    ("agent", "Agent Core"),
    ("observation", "Observation"),
    ("knowledge", "Knowledge"),
    ("scheduling", "Scheduling"),
    ("models", "Models"),
    ("runtime", "Runtime"),
    ("platform", "Platform"),
)

StatusProvider = Callable[[], Dict[str, Any]]
MetricsProvider = Callable[[], Dict[str, Any]]


@dataclass
class SystemComponent:
    """Section 14.16."""

    id: str
    name: str
    component_type: str
    parent_component: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)
    description: str = ""
    version: Optional[str] = None
    platform_implementation: Optional[str] = None
    status_provider: Optional[StatusProvider] = None
    metrics_provider: Optional[MetricsProvider] = None
    registered_at: float = field(default_factory=time.time)

    # -- live state ---------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        """Never raises: a broken provider is itself a diagnosable condition."""
        if self.status_provider is None:
            return {"state": "running"}
        try:
            data = self.status_provider() or {}
        except Exception as exc:
            return {"state": "degraded",
                    "last_error": f"status provider failed: {type(exc).__name__}: {exc}"}
        state = str(data.get("state", "running"))
        if state not in COMPONENT_STATES:
            state = "running"
        data["state"] = state
        return data

    def metrics(self) -> Dict[str, Any]:
        if self.metrics_provider is None:
            return {}
        try:
            return self.metrics_provider() or {}
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "component_type": self.component_type,
            "parent_component": self.parent_component,
            "dependencies": list(self.dependencies),
            "description": self.description,
            "version": self.version,
            "platform_implementation": self.platform_implementation,
            "uptime_seconds": round(time.time() - self.registered_at, 1),
        }


@dataclass
class SystemConnection:
    """Section 14.7 - a communication path, selectable in its own right."""

    id: str
    source: str
    target: str
    connection_type: str
    interface: str = ""
    description: str = ""
    status_provider: Optional[StatusProvider] = None

    def status(self) -> Dict[str, Any]:
        if self.status_provider is None:
            return {"state": "idle"}
        try:
            data = self.status_provider() or {}
        except Exception as exc:
            return {"state": "degraded", "last_error": f"{type(exc).__name__}: {exc}"}
        state = str(data.get("state", "idle"))
        if state not in CONNECTION_STATES:
            state = "idle"
        data["state"] = state
        return data

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "connection_type": self.connection_type,
            "interface": self.interface,
            "description": self.description,
        }


class SystemRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._components: Dict[str, SystemComponent] = {}
        self._connections: Dict[str, SystemConnection] = {}

    # -- registration -------------------------------------------------------
    def register_component(self, component: SystemComponent) -> SystemComponent:
        with self._lock:
            self._components[component.id] = component
        return component

    def register_connection(self, connection: SystemConnection) -> SystemConnection:
        with self._lock:
            self._connections[connection.id] = connection
        return connection

    def unregister_component(self, component_id: str) -> None:
        with self._lock:
            self._components.pop(component_id, None)

    # -- lookup -------------------------------------------------------------
    def component(self, component_id: str) -> Optional[SystemComponent]:
        with self._lock:
            return self._components.get(component_id)

    def connection(self, connection_id: str) -> Optional[SystemConnection]:
        with self._lock:
            return self._connections.get(connection_id)

    def components(self) -> List[SystemComponent]:
        with self._lock:
            return list(self._components.values())

    def connections(self) -> List[SystemConnection]:
        with self._lock:
            return list(self._connections.values())

    def children(self, component_id: str) -> List[SystemComponent]:
        return [c for c in self.components() if c.parent_component == component_id]

    def connections_for(self, component_id: str) -> Dict[str, List[SystemConnection]]:
        incoming = [c for c in self.connections() if c.target == component_id]
        outgoing = [c for c in self.connections() if c.source == component_id]
        return {"incoming": incoming, "outgoing": outgoing}

    # -- topology -----------------------------------------------------------
    def topology(self) -> Dict[str, Any]:
        """The payload the System Map is drawn from (section 14.31.1)."""
        nodes = []
        for component in self.components():
            status = component.status()
            nodes.append(
                {
                    **component.to_dict(),
                    "status": status,
                    "state": status.get("state", "running"),
                    "has_children": bool(self.children(component.id)),
                }
            )
        edges = []
        for connection in self.connections():
            status = connection.status()
            edges.append({**connection.to_dict(), "status": status,
                          "state": status.get("state", "idle")})
        return {
            "subsystems": [{"id": key, "name": name} for key, name in SUBSYSTEMS],
            "components": nodes,
            "connections": edges,
            "states": list(COMPONENT_STATES),
            "connection_states": list(CONNECTION_STATES),
        }
