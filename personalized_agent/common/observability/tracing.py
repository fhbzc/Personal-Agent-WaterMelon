"""Structured tracing (sections 14.5, 14.6, 14.28).

System execution is traced with structured spans rather than plain text logs, so
the Debugger can reconstruct a complete task lifecycle across components.

Retention is bounded and high-volume components are sampled: observability must
not interfere with normal operation (section 14.28).  Every value that reaches a
span passes through the secret redaction filter first (section 14.5).
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Iterator, List, Optional

from ..logging.setup import SECRETS

SEVERITIES = ("debug", "info", "warning", "error", "security")
STATUSES = ("started", "ok", "error", "blocked", "skipped")


def new_trace_id() -> str:
    return f"trace_{uuid.uuid4().hex[:16]}"


def new_span_id() -> str:
    return f"span_{uuid.uuid4().hex[:12]}"


def _summarize(value: Any, limit: int = 240) -> Optional[str]:
    """Short, redacted description of an input or output."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value
    elif isinstance(value, dict):
        text = ", ".join(f"{k}={_short(v)}" for k, v in list(value.items())[:8])
    elif isinstance(value, (list, tuple)):
        text = f"{len(value)} items"
    else:
        text = str(value)
    text = SECRETS.scrub(text)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _short(value: Any, limit: int = 60) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


@dataclass
class TraceEvent:
    """Section 14.5."""

    component: str
    operation: str
    trace_id: str
    span_id: str = field(default_factory=new_span_id)
    parent_span_id: Optional[str] = None
    id: str = field(default_factory=lambda: f"tev_{uuid.uuid4().hex[:12]}")
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    task_id: Optional[str] = None
    event_type: Optional[str] = None
    severity: str = "info"
    duration_ms: Optional[float] = None
    input_summary: Optional[str] = None
    output_summary: Optional[str] = None
    status: str = "ok"
    error: Optional[str] = None
    connection_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data


class TraceStore:
    """Bounded, thread-safe span storage with per-component sampling."""

    def __init__(self, capacity: int = 4000, retention_seconds: float = 3600.0) -> None:
        self.capacity = capacity
        self.retention_seconds = retention_seconds
        self._lock = threading.RLock()
        self._events: Deque[TraceEvent] = deque(maxlen=capacity)
        self._sampling: Dict[str, int] = {}
        self._counters: Dict[str, int] = {}
        self._dropped = 0
        # Section 14.33: the Debugger's master switch.
        #
        # Off means the monitoring does not run, not that the panel hides what
        # it collected anyway - a switch that only stops the display leaves
        # every span still being built, stored and evicted, which is the cost
        # the user turned it off to avoid.
        self._enabled = True

    # -- configuration ------------------------------------------------------
    def set_sampling(self, component: str, every_nth: int) -> None:
        """Record one span in *every_nth* for a high-volume component."""
        with self._lock:
            self._sampling[component] = max(1, int(every_nth))

    def set_enabled(self, enabled: bool) -> None:
        """Turn collection on or off. Off also drops what was collected."""
        with self._lock:
            self._enabled = bool(enabled)
            if not self._enabled:
                self._events.clear()
                self._counters.clear()
                self._dropped = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _should_record(self, event: TraceEvent) -> bool:
        if not self._enabled:
            return False
        if event.severity in ("error", "security", "warning"):
            return True  # failures are never sampled away
        with self._lock:
            rate = self._sampling.get(event.component, 1)
            if rate <= 1:
                return True
            count = self._counters.get(event.component, 0) + 1
            self._counters[event.component] = count
            if count % rate:
                self._dropped += 1
                return False
            return True

    # -- writing ------------------------------------------------------------
    def record(self, event: TraceEvent) -> Optional[TraceEvent]:
        if not self._should_record(event):
            return None
        event.input_summary = _summarize(event.input_summary)
        event.output_summary = _summarize(event.output_summary)
        if event.error:
            event.error = _summarize(event.error, 400)
        with self._lock:
            self._events.append(event)
        return event

    def prune(self) -> int:
        cutoff = time.time() - self.retention_seconds
        removed = 0
        with self._lock:
            while self._events and self._events[0].timestamp.timestamp() < cutoff:
                self._events.popleft()
                removed += 1
        return removed

    # -- reading ------------------------------------------------------------
    def all(self) -> List[TraceEvent]:
        with self._lock:
            return list(self._events)

    def query(
        self,
        *,
        trace_id: Optional[str] = None,
        task_id: Optional[str] = None,
        component: Optional[str] = None,
        connection_id: Optional[str] = None,
        severity: Optional[str] = None,
        event_type: Optional[str] = None,
        since: Optional[datetime] = None,
        text: Optional[str] = None,
        limit: int = 200,
    ) -> List[TraceEvent]:
        needle = (text or "").lower()
        results: List[TraceEvent] = []
        for event in reversed(self.all()):
            if trace_id and event.trace_id != trace_id:
                continue
            if task_id and event.task_id != task_id:
                continue
            if component and event.component != component:
                continue
            if connection_id and event.connection_id != connection_id:
                continue
            if severity and event.severity != severity:
                continue
            if event_type and event.event_type != event_type:
                continue
            if since and event.timestamp < since:
                continue
            if needle:
                haystack = " ".join(
                    str(part).lower() for part in (
                        event.component, event.operation, event.task_id, event.trace_id,
                        event.event_type, event.error, event.input_summary,
                        event.output_summary,
                    ) if part
                )
                if needle not in haystack:
                    continue
            results.append(event)
            if len(results) >= limit:
                break
        return results

    def traces(self, limit: int = 40, **filters: Any) -> List[Dict[str, Any]]:
        """Group spans into end-to-end traces (section 14.6)."""
        grouped: Dict[str, List[TraceEvent]] = {}
        for event in self.query(limit=self.capacity, **filters):
            grouped.setdefault(event.trace_id, []).append(event)
        summaries = []
        for trace_id, events in grouped.items():
            ordered = sorted(events, key=lambda e: e.timestamp)
            started = ordered[0].timestamp
            finished = max(
                (e.timestamp.timestamp() + (e.duration_ms or 0) / 1000 for e in ordered),
                default=started.timestamp(),
            )
            failed = [e for e in ordered if e.status in ("error", "blocked")]
            summaries.append(
                {
                    "trace_id": trace_id,
                    "task_id": next((e.task_id for e in ordered if e.task_id), None),
                    "root": ordered[0].operation,
                    "kind": ordered[0].metadata.get("kind", ordered[0].component),
                    "started_at": started.isoformat(),
                    "duration_ms": round((finished - started.timestamp()) * 1000, 1),
                    "span_count": len(ordered),
                    "components": list(dict.fromkeys(e.component for e in ordered)),
                    "connections": list(
                        dict.fromkeys(e.connection_id for e in ordered if e.connection_id)
                    ),
                    "status": "error" if failed else "ok",
                    "error": failed[0].error if failed else None,
                    "severity": max((e.severity for e in ordered),
                                    key=lambda s: SEVERITIES.index(s)
                                    if s in SEVERITIES else 0),
                }
            )
        summaries.sort(key=lambda t: t["started_at"], reverse=True)
        return summaries[:limit]

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "spans": len(self._events),
                "capacity": self.capacity,
                "retention_seconds": self.retention_seconds,
                "sampled_out": self._dropped,
                "sampling": dict(self._sampling),
            }


class Tracer:
    """Creates spans for one component, keeping the current trace context."""

    def __init__(self, store: TraceStore, component: str,
                 context: Optional["TraceContext"] = None) -> None:
        self.store = store
        self.component = component
        self.context = context or TraceContext()

    def for_component(self, component: str) -> "Tracer":
        return Tracer(self.store, component, self.context)

    @contextmanager
    def span(
        self,
        operation: str,
        *,
        trace_id: Optional[str] = None,
        task_id: Optional[str] = None,
        event_type: Optional[str] = None,
        connection_id: Optional[str] = None,
        severity: str = "info",
        inputs: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Iterator[TraceEvent]:
        parent = self.context.current_span()
        event = TraceEvent(
            component=self.component,
            operation=operation,
            trace_id=trace_id or self.context.current_trace() or new_trace_id(),
            parent_span_id=parent,
            task_id=task_id or self.context.current_task(),
            event_type=event_type,
            connection_id=connection_id,
            severity=severity,
            input_summary=inputs,
            status="started",
            metadata=dict(metadata or {}),
        )
        started = time.perf_counter()
        self.context.push(event)
        try:
            yield event
            if event.status == "started":
                event.status = "ok"
        except Exception as exc:
            event.status = "error"
            event.severity = "error"
            event.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            event.duration_ms = round((time.perf_counter() - started) * 1000, 2)
            self.context.pop()
            self.store.record(event)

    def event(
        self,
        operation: str,
        *,
        trace_id: Optional[str] = None,
        task_id: Optional[str] = None,
        event_type: Optional[str] = None,
        connection_id: Optional[str] = None,
        severity: str = "info",
        status: str = "ok",
        inputs: Any = None,
        outputs: Any = None,
        error: Optional[str] = None,
        duration_ms: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[TraceEvent]:
        """Record a point-in-time span (no enclosing block)."""
        event = TraceEvent(
            component=self.component,
            operation=operation,
            trace_id=trace_id or self.context.current_trace() or new_trace_id(),
            parent_span_id=self.context.current_span(),
            task_id=task_id or self.context.current_task(),
            event_type=event_type,
            connection_id=connection_id,
            severity=severity,
            status=status,
            input_summary=inputs,
            output_summary=outputs,
            error=error,
            duration_ms=duration_ms,
            metadata=dict(metadata or {}),
        )
        return self.store.record(event)


class TraceContext:
    """Per-thread trace/span stack so nested work joins the same trace."""

    def __init__(self) -> None:
        self._local = threading.local()

    def _stack(self) -> List[TraceEvent]:
        stack = getattr(self._local, "stack", None)
        if stack is None:
            stack = []
            self._local.stack = stack
        return stack

    def push(self, event: TraceEvent) -> None:
        self._stack().append(event)

    def pop(self) -> None:
        stack = self._stack()
        if stack:
            stack.pop()

    def current_span(self) -> Optional[str]:
        stack = self._stack()
        return stack[-1].span_id if stack else None

    def current_trace(self) -> Optional[str]:
        stack = self._stack()
        return stack[-1].trace_id if stack else None

    def current_task(self) -> Optional[str]:
        for event in reversed(self._stack()):
            if event.task_id:
                return event.task_id
        return None

    @contextmanager
    def bind(self, trace_id: str, task_id: Optional[str] = None) -> Iterator[str]:
        """Start a new logical execution (section 14.6: one trace_id per task)."""
        anchor = TraceEvent(component="context", operation="bind", trace_id=trace_id,
                            task_id=task_id)
        self.push(anchor)
        try:
            yield trace_id
        finally:
            self.pop()


class NullTracer:
    """Used when a component runs without observability attached (tests, tools)."""

    def for_component(self, component: str) -> "NullTracer":
        return self

    @contextmanager
    def span(self, operation: str, **kwargs: Any) -> Iterator[TraceEvent]:
        yield TraceEvent(component="none", operation=operation,
                         trace_id=kwargs.get("trace_id") or "trace_none")

    def event(self, operation: str, **kwargs: Any) -> None:
        return None
