"""One panel per module (section 14.33).

The Debugger could already say which components exist and how long each took.
What it could not say is what any one of them actually holds. "raw_event_storage
- healthy, 4,102 events" tells you the module is alive and nothing about what
is in it, so a wrong event is invisible until something downstream misbehaves.

Each module therefore reports three things, and a panel shows all three:

* what it is - its name and what it is for, in a sentence
* how it is - the status and metrics it already provided
* what is in it - a detail view of its own shape, defined here

The detail is per module because modules are not alike: an event store is
interesting broken down by event type, a scheduler by what is due next, a
graph by what kinds of node it holds. One generic table for all of them would
be the summary that was already unhelpful.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

#: How many of each kind are shown in a "recent" list.
PER_KIND = 10

_PROVIDERS: Dict[str, Callable[[Any, Dict[str, Any]], Dict[str, Any]]] = {}


def detail_provider(component_id: str):
    """Register the detail view for one module."""
    def register(fn):
        _PROVIDERS[component_id] = fn
        return fn
    return register


def has_detail(component_id: str) -> bool:
    return component_id in _PROVIDERS


def detail_for(app: Any, component_id: str,
               options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """What this module holds, or why there is nothing to show."""
    if not app.observability_service.monitoring_enabled:
        return {"monitoring": "off",
                "note": "The Debugger's master switch is off, so nothing is "
                        "being collected. Turn it on in Settings to gather data "
                        "from here on; what happened while it was off was not "
                        "recorded."}
    provider = _PROVIDERS.get(component_id)
    if provider is None:
        return {}
    try:
        return provider(app, options or {})
    except Exception as exc:                       # a panel must never break
        log.exception("detail provider for %s failed", component_id)
        return {"error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Event log
# ---------------------------------------------------------------------------
@detail_provider("raw_event_storage")
def _raw_events(app: Any, options: Dict[str, Any]) -> Dict[str, Any]:
    """Everything the Listener has recorded, broken down by kind.

    Event types are the axis that matters here. A count of four thousand
    events says nothing; four thousand of which 3,900 are window focus and
    two are browser history says immediately that the browser provider is not
    working.
    """
    store = app.raw_store
    counts = _event_type_counts(app)
    chosen = str(options.get("event_type") or "")
    limit = int(options.get("limit") or PER_KIND)

    groups: List[Dict[str, Any]] = []
    kinds = [chosen] if chosen else [row["event_type"] for row in counts]
    for kind in kinds:
        events = store.recent_events(limit=limit, event_type=kind)
        groups.append({
            "event_type": kind,
            "count": next((r["count"] for r in counts if r["event_type"] == kind), 0),
            # Every field the row holds, not a chosen few: the point of
            # looking at a stored event is to see what was actually stored.
            "events": [event.to_dict() for event in events],
        })

    return {
        "summary": {
            "total events": sum(row["count"] for row in counts),
            "event types": len(counts),
        },
        "choices": {
            "field": "event_type",
            "label": "Event type",
            "options": [{"value": row["event_type"],
                         "label": f"{row['event_type']} ({row['count']})"}
                        for row in counts],
            "selected": chosen,
        },
        "groups": groups,
    }


def _event_type_counts(app: Any) -> List[Dict[str, Any]]:
    rows = app.db.query(
        "SELECT event_type, COUNT(*) AS count FROM raw_events "
        "GROUP BY event_type ORDER BY count DESC")
    return [{"event_type": r["event_type"], "count": r["count"]} for r in rows]


# ---------------------------------------------------------------------------
# Edit log - what the agent did to its own data
# ---------------------------------------------------------------------------
@detail_provider("raw_edit_storage")
def _raw_edits(app: Any, options: Dict[str, Any]) -> Dict[str, Any]:
    rows = app.db.query(
        "SELECT source, COUNT(*) AS count FROM raw_edits "
        "GROUP BY source ORDER BY count DESC")
    counts = [{"source": r["source"], "count": r["count"]} for r in rows]
    chosen = str(options.get("source") or "")
    limit = int(options.get("limit") or PER_KIND)

    groups = []
    for source in ([chosen] if chosen else [r["source"] for r in counts]):
        edits = app.db.query(
            "SELECT * FROM raw_edits WHERE source = ? ORDER BY timestamp DESC LIMIT ?",
            (source, limit))
        groups.append({
            "source": source,
            "count": next((c["count"] for c in counts if c["source"] == source), 0),
            "events": [dict(row) for row in edits],
        })
    return {
        "summary": {
            "total edits": sum(c["count"] for c in counts),
            "sources": len(counts),
        },
        "choices": {"field": "source", "label": "Written by",
                    "options": [{"value": c["source"],
                                 "label": f"{c['source']} ({c['count']})"}
                                for c in counts],
                    "selected": chosen},
        "groups": groups,
    }


# ---------------------------------------------------------------------------
# Knowledge graph
# ---------------------------------------------------------------------------
@detail_provider("knowledge_graph")
def _graph(app: Any, options: Dict[str, Any]) -> Dict[str, Any]:
    rows = app.db.query(
        "SELECT type, COUNT(*) AS count FROM nodes GROUP BY type ORDER BY count DESC")
    counts = [{"type": r["type"], "count": r["count"]} for r in rows]
    chosen = str(options.get("type") or "")
    limit = int(options.get("limit") or PER_KIND)

    groups = []
    for kind in ([chosen] if chosen else [c["type"] for c in counts]):
        nodes = app.db.query(
            "SELECT * FROM nodes WHERE type = ? ORDER BY updated_at DESC LIMIT ?",
            (kind, limit))
        groups.append({
            "type": kind,
            "count": next((c["count"] for c in counts if c["type"] == kind), 0),
            "events": [dict(row) for row in nodes],
        })
    stats = app.library.stats()
    return {
        # Counted the way the library counts, not the way the graph did.
        "summary": {"records": stats.get("records", 0),
                    "folders": len(stats.get("folders") or {}),
                    "kinds": len(counts)},
        "choices": {"field": "type", "label": "Node type",
                    "options": [{"value": c["type"],
                                 "label": f"{c['type']} ({c['count']})"}
                                for c in counts],
                    "selected": chosen},
        "groups": groups,
    }


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
@detail_provider("scheduler")
def _scheduler(app: Any, options: Dict[str, Any]) -> Dict[str, Any]:
    rows = app.db.query(
        "SELECT status, COUNT(*) AS count FROM scheduled_tasks "
        "GROUP BY status ORDER BY count DESC")
    counts = [{"status": r["status"], "count": r["count"]} for r in rows]
    chosen = str(options.get("status") or "")
    limit = int(options.get("limit") or PER_KIND)

    groups = []
    for status in ([chosen] if chosen else [c["status"] for c in counts]):
        tasks = app.db.query(
            "SELECT * FROM scheduled_tasks WHERE status = ? "
            "ORDER BY scheduled_at DESC LIMIT ?", (status, limit))
        groups.append({
            "status": status,
            "count": next((c["count"] for c in counts if c["status"] == status), 0),
            "events": [dict(row) for row in tasks],
        })
    return {
        "summary": dict(app.scheduler.status()),
        "choices": {"field": "status", "label": "Status",
                    "options": [{"value": c["status"],
                                 "label": f"{c['status']} ({c['count']})"}
                                for c in counts],
                    "selected": chosen},
        "groups": groups,
    }


# ---------------------------------------------------------------------------
# The state calendar
# ---------------------------------------------------------------------------
@detail_provider("agent_core")
def _states(app: Any, options: Dict[str, Any]) -> Dict[str, Any]:
    rows = app.db.query(
        "SELECT source, COUNT(*) AS count FROM states GROUP BY source ORDER BY count DESC")
    counts = [{"source": r["source"], "count": r["count"]} for r in rows]
    chosen = str(options.get("source") or "")
    limit = int(options.get("limit") or PER_KIND)

    groups = []
    for source in ([chosen] if chosen else [c["source"] for c in counts]):
        states = app.db.query(
            "SELECT * FROM states WHERE source = ? ORDER BY updated_at DESC LIMIT ?",
            (source, limit))
        groups.append({
            "source": source,
            "count": next((c["count"] for c in counts if c["source"] == source), 0),
            "events": [dict(row) for row in states],
        })
    return {
        "summary": {"states": sum(c["count"] for c in counts),
                    "sources": len(counts)},
        "choices": {"field": "source", "label": "Written by",
                    "options": [{"value": c["source"],
                                 "label": f"{c['source']} ({c['count']})"}
                                for c in counts],
                    "selected": chosen},
        "groups": groups,
    }


__all__ = ["detail_for", "has_detail", "detail_provider", "PER_KIND"]
