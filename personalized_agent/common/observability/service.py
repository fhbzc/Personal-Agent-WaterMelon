"""Observability Service (section 14.29).

    System components -> Observability Service -> trace + metric storage
                                               -> System Debugger UI
                                               -> Component / Connection inspectors
                                               -> Diagnostic tests

Components publish here instead of building their own debug interface, and the
Debugger reads only from this service.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ...version import APP_NAME, __version__
from .diagnostics import DiagnosticRegistry
from .metrics import Observability
from .registry import SystemRegistry
from .tracing import TraceStore, Tracer


class ObservabilityService:
    def __init__(self, metrics: Optional[Observability] = None,
                 trace_capacity: int = 4000) -> None:
        self.metrics = metrics or Observability()
        self.traces = TraceStore(capacity=trace_capacity)
        self.registry = SystemRegistry()
        self.tracer = Tracer(self.traces, "observability")
        self.diagnostics = DiagnosticRegistry(tracer=self.tracer)
        self._lock = threading.RLock()
        # High-volume producers are sampled so the Debugger cannot grow without
        # bound (section 14.28); failures are always kept.
        self.traces.set_sampling("listener", 5)
        self.traces.set_sampling("event_pipeline", 5)

    # -- component facing ---------------------------------------------------
    def tracer_for(self, component_id: str) -> Tracer:
        return self.tracer.for_component(component_id)

    # -- the master switch (section 14.33) ----------------------------------
    def set_monitoring(self, enabled: bool) -> None:
        self.traces.set_enabled(enabled)

    @property
    def monitoring_enabled(self) -> bool:
        return self.traces.enabled

    # -- topology -----------------------------------------------------------
    def topology(self) -> Dict[str, Any]:
        data = self.registry.topology()
        for node in data["components"]:
            node["test_count"] = len(self.diagnostics.for_component(node["id"]))
            node["error_count"] = len(
                self.traces.query(component=node["id"], severity="error", limit=50)
            )
        for edge in data["connections"]:
            edge["test_count"] = len(self.diagnostics.for_connection(edge["id"]))
            traffic = self.traces.query(connection_id=edge["id"], limit=200)
            edge["recent_traffic"] = len(traffic)
            edge["failures"] = len([t for t in traffic if t.status in ("error", "blocked")])
            edge["active"] = bool(
                traffic and (datetime.now(timezone.utc) - traffic[0].timestamp).total_seconds() < 10
            )
        data["application"] = {"name": APP_NAME, "version": __version__}
        data["generated_at"] = datetime.now(timezone.utc).isoformat()
        return data

    # -- inspectors ---------------------------------------------------------
    def component_detail(self, component_id: str, since: Optional[datetime] = None
                         ) -> Optional[Dict[str, Any]]:
        """Section 14.31.3 - Overview, Trace, Metrics, Tests, Errors."""
        component = self.registry.component(component_id)
        if component is None:
            return None
        related = self.registry.connections_for(component_id)
        status = component.status()
        traces = self.traces.query(component=component_id, since=since, limit=120)
        errors = [t for t in traces if t.severity in ("error", "warning", "security")]
        return {
            "kind": "component",
            "overview": {
                **component.to_dict(),
                "parent_name": getattr(
                    self.registry.component(component.parent_component or ""), "name", None
                ),
                "state": status.get("state"),
                "status": status,
                "dependencies": component.dependencies,
                "children": [c.to_dict() for c in self.registry.children(component_id)],
                "incoming": [c.to_dict() for c in related["incoming"]],
                "outgoing": [c.to_dict() for c in related["outgoing"]],
            },
            "metrics": component.metrics(),
            "traces": [t.to_dict() for t in traces[:60]],
            "errors": [t.to_dict() for t in errors[:40]],
            "tests": [t.to_dict() for t in self.diagnostics.for_component(component_id)],
            "test_history": self.diagnostics.history(limit=10),
        }

    def connection_detail(self, connection_id: str, since: Optional[datetime] = None
                          ) -> Optional[Dict[str, Any]]:
        """Section 14.31.4 - the Connection Inspector payload."""
        connection = self.registry.connection(connection_id)
        if connection is None:
            return None
        traces = self.traces.query(connection_id=connection_id, since=since, limit=200)
        failures = [t for t in traces if t.status in ("error", "blocked")]
        latencies = [t.duration_ms for t in traces if t.duration_ms is not None]
        successful = [t for t in traces if t.status == "ok"]
        source = self.registry.component(connection.source)
        target = self.registry.component(connection.target)
        return {
            "kind": "connection",
            "overview": {
                **connection.to_dict(),
                "source_name": source.name if source else connection.source,
                "target_name": target.name if target else connection.target,
                "status": connection.status(),
                "state": connection.status().get("state"),
                "request_count": len(traces),
                "failure_count": len(failures),
                "average_latency_ms": round(sum(latencies) / len(latencies), 2)
                if latencies else None,
                "last_success": successful[0].timestamp.isoformat() if successful else None,
                "last_failure": failures[0].timestamp.isoformat() if failures else None,
            },
            "traces": [t.to_dict() for t in traces[:60]],
            "errors": [t.to_dict() for t in failures[:40]],
            "tests": [t.to_dict() for t in self.diagnostics.for_connection(connection_id)],
            "test_history": self.diagnostics.history(limit=10),
        }

    # -- traces -------------------------------------------------------------
    def trace_detail(self, trace_id: str) -> Dict[str, Any]:
        """Section 14.6 / 14.31.5 - one end-to-end execution."""
        spans = sorted(self.traces.query(trace_id=trace_id, limit=500),
                       key=lambda e: e.timestamp)
        steps = []
        previous = None
        for span in spans:
            gap = None
            if previous is not None:
                gap = round((span.timestamp - previous.timestamp).total_seconds() * 1000, 1)
            steps.append({**span.to_dict(), "gap_ms": gap})
            previous = span
        return {
            "trace_id": trace_id,
            "steps": steps,
            "components": list(dict.fromkeys(s.component for s in spans)),
            "connections": list(dict.fromkeys(s.connection_id for s in spans
                                              if s.connection_id)),
            "compact": " → ".join(dict.fromkeys(s.component for s in spans)),
            "status": "error" if any(s.status in ("error", "blocked") for s in spans) else "ok",
        }

    def timeline(self, limit: int = 40, **filters: Any) -> List[Dict[str, Any]]:
        return self.traces.traces(limit=limit, **filters)

    def window(self, minutes: Optional[int]) -> Optional[datetime]:
        """Section 14.32.10 - live vs. historical mode."""
        if not minutes:
            return None
        return datetime.now(timezone.utc) - timedelta(minutes=minutes)

    # -- search (sections 14.26, 14.31.7) -----------------------------------
    def search(self, query: str, limit: int = 30) -> Dict[str, Any]:
        needle = (query or "").strip().lower()
        if not needle:
            return {"query": query, "components": [], "connections": [], "traces": [],
                    "tests": []}
        components = [
            c.to_dict() for c in self.registry.components()
            if needle in c.id.lower() or needle in c.name.lower()
            or needle in c.component_type.lower()
        ][:limit]
        connections = [
            c.to_dict() for c in self.registry.connections()
            if needle in c.id.lower() or needle in c.source.lower()
            or needle in c.target.lower() or needle in c.connection_type.lower()
        ][:limit]
        matching = self.traces.query(text=needle, limit=limit * 4)
        trace_ids = list(dict.fromkeys(t.trace_id for t in matching))[:limit]
        traces = [t for t in self.traces.traces(limit=200) if t["trace_id"] in trace_ids]
        tests = [t.to_dict() for t in self.diagnostics.all()
                 if needle in t.id.lower() or needle in t.name.lower()][:limit]
        return {
            "query": query,
            "components": components,
            "connections": connections,
            "traces": traces,
            "spans": [t.to_dict() for t in matching[:limit]],
            "tests": tests,
        }

    # -- export (section 14.27) --------------------------------------------
    def diagnostic_report(self, trace_ids: Optional[List[str]] = None,
                          platform: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        topology = self.topology()
        traces = []
        for trace_id in (trace_ids or [t["trace_id"] for t in self.timeline(limit=10)]):
            traces.append(self.trace_detail(trace_id))
        snapshot = self.metrics.snapshot()
        report = {
            "application": {"name": APP_NAME, "version": __version__},
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "topology": topology,
            "component_states": {
                node["id"]: node["state"] for node in topology["components"]
            },
            "errors": snapshot["agent_metrics"]["errors"],
            "security_violations": snapshot["agent_metrics"]["sandbox_violations"],
            "model_metrics": snapshot["model_metrics"],
            "system_metrics": snapshot["system_metrics"],
            "traces": traces,
            "test_results": self.diagnostics.history(limit=50),
            "platform_capabilities": platform or {},
            "trace_store": self.traces.stats(),
        }
        return json.loads(_redact(json.dumps(report, default=str)))

    def snapshot(self) -> Dict[str, Any]:
        data = self.metrics.snapshot()
        data["trace_store"] = self.traces.stats()
        data["components"] = len(self.registry.components())
        data["connections"] = len(self.registry.connections())
        data["diagnostic_tests"] = len(self.diagnostics.all())
        return data


def _redact(payload: str) -> str:
    """Section 14.27 - secrets and credentials never leave in a report."""
    from ..logging.setup import SECRETS

    return SECRETS.scrub(payload)
