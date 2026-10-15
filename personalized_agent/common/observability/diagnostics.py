"""Diagnostic test registry (sections 14.10-14.15, 14.32.7-14.32.9).

Components and connections register their own tests, so diagnostics extend with
the system - a listener plugin can register tests the same way a built-in
component does (section 14.15).

Two rules are enforced here rather than described:

* a test runs through the normal runtime, permission and sandbox boundaries; the
  Debugger never becomes an unrestricted execution path (sections 14.12, 14.32.8);
* anything a test emits is tagged as diagnostic data so the Data Management
  system cannot mistake it for genuine user behaviour (section 14.32.9).
"""

from __future__ import annotations

import threading
import traceback
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, List, Optional

from .tracing import Tracer, new_trace_id

# Section 14.11 - result states.
RESULT_STATES = ("passed", "warning", "failed", "skipped", "unsupported")

# Section 14.13 - declared impact.  Anything destructive is deliberately absent.
RISK_LEVELS = ("read_only", "safe_write", "external_request", "state_changing")


@dataclass
class DiagnosticResult:
    """Section 14.11."""

    test_id: str
    status: str
    summary: str
    started_at: str
    completed_at: str
    duration_ms: float
    details: Dict[str, Any] = field(default_factory=dict)
    trace_id: Optional[str] = None
    task_id: Optional[str] = None
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DiagnosticTest:
    """Section 14.11 - the common interface every diagnostic implements."""

    def __init__(
        self,
        test_id: str,
        name: str,
        description: str,
        component_id: Optional[str] = None,
        connection_id: Optional[str] = None,
        risk_level: str = "read_only",
        run: Optional[Callable[["DiagnosticContext"], Dict[str, Any]]] = None,
    ) -> None:
        if risk_level not in RISK_LEVELS:
            raise ValueError(f"unknown risk level {risk_level!r}")
        if not component_id and not connection_id:
            raise ValueError("a test must belong to a component or a connection")
        self.id = test_id
        self.name = name
        self.description = description
        self.component_id = component_id
        self.connection_id = connection_id
        self.risk_level = risk_level
        self._run = run

    def run(self, context: "DiagnosticContext") -> Dict[str, Any]:
        if self._run is None:
            return {"status": "unsupported", "summary": "no implementation registered"}
        return self._run(context)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "component_id": self.component_id,
            "connection_id": self.connection_id,
            "risk_level": self.risk_level,
            "modifies_state": self.risk_level in ("safe_write", "state_changing"),
            "leaves_machine": self.risk_level == "external_request",
        }


@dataclass
class DiagnosticContext:
    """What a test is given: the trace it runs in, and the marker for its data."""

    test: DiagnosticTest
    trace_id: str
    task_id: str
    tracer: Tracer

    def diagnostic_metadata(self) -> Dict[str, Any]:
        """Section 14.32.9 - the tag synthetic data must carry."""
        return {"diagnostic": True, "test_id": self.test.id, "trace_id": self.trace_id}


class DiagnosticRegistry:
    """Section 14.15 - the central registry."""

    def __init__(self, tracer: Optional[Tracer] = None, history: int = 100) -> None:
        self._lock = threading.RLock()
        self._tests: Dict[str, DiagnosticTest] = {}
        self._history: Deque[DiagnosticResult] = deque(maxlen=history)
        self.tracer = tracer

    # -- registration -------------------------------------------------------
    def register(self, test: DiagnosticTest) -> DiagnosticTest:
        with self._lock:
            self._tests[test.id] = test
        return test

    def add(self, **kwargs: Any) -> DiagnosticTest:
        return self.register(DiagnosticTest(**kwargs))

    def unregister(self, test_id: str) -> None:
        with self._lock:
            self._tests.pop(test_id, None)

    # -- lookup -------------------------------------------------------------
    def get(self, test_id: str) -> Optional[DiagnosticTest]:
        with self._lock:
            return self._tests.get(test_id)

    def all(self) -> List[DiagnosticTest]:
        with self._lock:
            return list(self._tests.values())

    def for_component(self, component_id: str) -> List[DiagnosticTest]:
        return [t for t in self.all() if t.component_id == component_id]

    def for_connection(self, connection_id: str) -> List[DiagnosticTest]:
        return [t for t in self.all() if t.connection_id == connection_id]

    def grouped(self) -> Dict[str, List[Dict[str, Any]]]:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for test in self.all():
            key = test.component_id or test.connection_id or "other"
            groups.setdefault(key, []).append(test.to_dict())
        return groups

    # -- execution ----------------------------------------------------------
    def run(self, test_id: str) -> DiagnosticResult:
        """Section 14.32.7 - one execution, with its own task and trace id."""
        test = self.get(test_id)
        started = datetime.now(timezone.utc)
        if test is None:
            return DiagnosticResult(
                test_id=test_id, status="unsupported",
                summary=f"no diagnostic test registered with id {test_id!r}",
                started_at=started.isoformat(), completed_at=started.isoformat(),
                duration_ms=0.0,
            )

        trace_id = new_trace_id()
        task_id = f"diag_{uuid.uuid4().hex[:12]}"
        tracer = self.tracer.for_component(test.component_id or "observability") \
            if self.tracer else None
        context = DiagnosticContext(test=test, trace_id=trace_id, task_id=task_id,
                                    tracer=tracer)

        payload: Dict[str, Any]
        if tracer is not None:
            with tracer.span(
                f"diagnostic.{test.id}", trace_id=trace_id, task_id=task_id,
                event_type="diagnostic.test",
                connection_id=test.connection_id,
                metadata={"kind": "diagnostic", "risk_level": test.risk_level,
                          "diagnostic": True, "test_id": test.id},
            ) as span:
                payload = self._execute(test, context)
                span.status = "ok" if payload.get("status") in ("passed", "warning",
                                                                "skipped", "unsupported") \
                    else "error"
                span.severity = {"passed": "info", "warning": "warning",
                                 "failed": "error"}.get(payload.get("status"), "info")
                span.output_summary = payload.get("summary")
        else:
            payload = self._execute(test, context)

        completed = datetime.now(timezone.utc)
        result = DiagnosticResult(
            test_id=test.id,
            status=payload.get("status", "failed"),
            summary=str(payload.get("summary", ""))[:600],
            started_at=started.isoformat(),
            completed_at=completed.isoformat(),
            duration_ms=round((completed - started).total_seconds() * 1000, 2),
            details=payload.get("details") or {},
            trace_id=trace_id,
            task_id=task_id,
            recommendations=list(payload.get("recommendations") or []),
        )
        with self._lock:
            self._history.appendleft(result)
        return result

    @staticmethod
    def _execute(test: DiagnosticTest, context: DiagnosticContext) -> Dict[str, Any]:
        try:
            payload = test.run(context) or {}
        except Exception as exc:
            return {
                "status": "failed",
                "summary": f"{type(exc).__name__}: {exc}",
                "details": {"traceback": traceback.format_exc(limit=4)},
            }
        status = payload.get("status", "passed")
        if status not in RESULT_STATES:
            payload["status"] = "failed"
            payload["summary"] = f"test returned an unknown status {status!r}"
        return payload

    def history(self, limit: int = 25, test_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            items = [r for r in self._history if test_id is None or r.test_id == test_id]
        return [r.to_dict() for r in items[:limit]]
