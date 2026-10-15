"""Core data models shared by every module.

These are the normalized abstractions described in the design specification
(sections 21, 39, 40, 46, 56).  Modules communicate through these structures
rather than through each other internal representations.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def json_load(value: Any) -> Any:
    if value in (None, ""):
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# Section 21 - Normalized Listener Event Model
# ---------------------------------------------------------------------------
@dataclass
class ListenerEvent:
    """What happened.  Listeners never interpret, they only record."""

    event_type: str
    source: str
    id: str = field(default_factory=lambda: new_id("event"))
    timestamp: datetime = field(default_factory=utcnow)
    application: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = iso(self.timestamp)
        return d

    @staticmethod
    def from_row(row: Dict[str, Any]) -> "ListenerEvent":
        return ListenerEvent(
            id=row["id"],
            event_type=row["event_type"],
            source=row["source"],
            timestamp=parse_iso(row["timestamp"]) or utcnow(),
            application=row.get("application"),
            data=json_load(row.get("data")),
            metadata=json_load(row.get("metadata")),
        )


# ---------------------------------------------------------------------------
# Section 46 - Raw Edit Schema
# ---------------------------------------------------------------------------
@dataclass
class RawEdit:
    """What the agent did.  Cheap to write, semantically organised later."""

    event_type: str
    source: str
    id: str = field(default_factory=lambda: new_id("edit"))
    timestamp: datetime = field(default_factory=utcnow)
    task_id: Optional[str] = None
    instruction: Optional[str] = None
    accessed_information: List[str] = field(default_factory=list)
    action: Optional[str] = None
    result: Optional[str] = None
    related_nodes: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = iso(self.timestamp)
        return d

    @staticmethod
    def from_row(row: Dict[str, Any]) -> "RawEdit":
        return RawEdit(
            id=row["id"],
            event_type=row["event_type"],
            source=row["source"],
            timestamp=parse_iso(row["timestamp"]) or utcnow(),
            task_id=row.get("task_id"),
            instruction=row.get("instruction"),
            accessed_information=json_load(row.get("accessed_information")) or [],
            action=row.get("action"),
            result=row.get("result"),
            related_nodes=json_load(row.get("related_nodes")) or [],
            metadata=json_load(row.get("metadata")),
        )


# ---------------------------------------------------------------------------
# Section 39 / 40 - Knowledge Graph
# ---------------------------------------------------------------------------
@dataclass
class KnowledgeNode:
    type: str
    title: str
    id: str = field(default_factory=lambda: new_id("node"))
    summary: str = ""
    content: str = ""
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    source: str = "agent"
    metadata: Dict[str, Any] = field(default_factory=dict)
    #: Words this node should be found by, maintained by the data heartbeat
    #: (section 51.1). A node's own text says what it is; its keywords say
    #: what someone might call it when looking for it.
    keywords: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["created_at"] = iso(self.created_at)
        d["updated_at"] = iso(self.updated_at)
        return d

    @staticmethod
    def from_row(row: Dict[str, Any]) -> "KnowledgeNode":
        return KnowledgeNode(
            id=row["id"],
            type=row["type"],
            title=row["title"],
            summary=row.get("summary") or "",
            content=row.get("content") or "",
            created_at=parse_iso(row.get("created_at")) or utcnow(),
            updated_at=parse_iso(row.get("updated_at")) or utcnow(),
            source=row.get("source") or "agent",
            metadata=json_load(row.get("metadata")),
            keywords=[str(k) for k in (json_load(row.get("keywords")) or [])
                      if str(k).strip()],
        )


@dataclass
class KnowledgeEdge:
    source_node_id: str
    target_node_id: str
    relationship_type: str
    id: str = field(default_factory=lambda: new_id("edge"))
    summary: str = ""
    weight: float = 1.0
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    relationship_log: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["created_at"] = iso(self.created_at)
        d["updated_at"] = iso(self.updated_at)
        return d

    @staticmethod
    def from_row(row: Dict[str, Any]) -> "KnowledgeEdge":
        return KnowledgeEdge(
            id=row["id"],
            source_node_id=row["source_node_id"],
            target_node_id=row["target_node_id"],
            relationship_type=row["relationship_type"],
            summary=row.get("summary") or "",
            weight=float(row.get("weight") or 1.0),
            created_at=parse_iso(row.get("created_at")) or utcnow(),
            updated_at=parse_iso(row.get("updated_at")) or utcnow(),
            relationship_log=json_load(row.get("relationship_log")) or [],
            metadata=json_load(row.get("metadata")),
        )


@dataclass
class EdgeView:
    """Section 40 - what a node lookup exposes for retrieval decisions.

    The full destination node is not loaded until it is selected.
    """

    edge_id: str
    target_node_id: str
    target_title: str
    target_type: str
    target_summary: str
    relationship_type: str
    relationship_summary: str
    weight: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Section 56 - Scheduled Task Schema
# ---------------------------------------------------------------------------
#: "closed": the agent saw it was already done, so it will not run; it
#: stays on the calendar saying so, and why.
TASK_STATES = ("pending", "running", "completed", "failed", "cancelled", "disabled",
               "closed")

EXECUTION_TYPES = (
    "notification",
    "python_script",
    "agent_task",
    "model_task",
    "system_action",
    "integration_action",
)


@dataclass
class ScheduledTask:
    title: str
    scheduled_at: datetime
    execution_type: str = "notification"
    id: str = field(default_factory=lambda: new_id("task"))
    description: str = ""
    created_at: datetime = field(default_factory=utcnow)
    timezone_name: str = "UTC"
    status: str = "pending"
    script_path: Optional[str] = None
    model_config: Dict[str, Any] = field(default_factory=dict)
    task_payload: Dict[str, Any] = field(default_factory=dict)
    retry_policy: Dict[str, Any] = field(
        default_factory=lambda: {"max_retries": 2, "backoff_seconds": 60}
    )
    metadata: Dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    last_error: Optional[str] = None
    last_run_at: Optional[datetime] = None
    lease_token: Optional[str] = None
    #: Section 52.3 - how this repeats, or ``{}`` for a task that runs once.
    #:
    #: A closed shape, checked on the way in, rather than another open dict:
    #: ``every_seconds`` is required, and the run stops at whichever of
    #: ``count`` and ``until`` arrives first. Both may be omitted, which is a
    #: repeat with no end - allowed, but it has to be said rather than fallen
    #: into.
    recurrence: Dict[str, Any] = field(default_factory=dict)
    #: How many times it has actually run. Separate from the rule above, the
    #: way ``attempts`` is separate from ``retry_policy``.
    runs: int = 0
    #: Who decided this should exist: user, agent, heartbeat, scheduler
    #: (section 52.7, as amended).
    created_by: str = "agent"
    #: The judgement that led to it. Filled in by the scheduler when the task
    #: is created, so that it is never absent - a task the user added says
    #: "added by user", and one the agent added with nothing recorded says so.
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["scheduled_at"] = iso(self.scheduled_at)
        d["created_at"] = iso(self.created_at)
        d["last_run_at"] = iso(self.last_run_at)
        return d

    @staticmethod
    def from_row(row: Dict[str, Any]) -> "ScheduledTask":
        # Section 52.3: A REPEAT'S END DATE COMES BACK AS A DATE. It is kept
        # in JSON, which writes it as text, and read back it stayed text - so
        # the first comparison with a time ("is the next run past the end?")
        # raised TypeError: the calendar failed to load and the task could not
        # be carried to its next run. Ported from PR #4 (daniela-miaut). One
        # that cannot be read as a date ends the repeat rather than leaving
        # it to run on for ever.
        recurrence = json_load(row.get("recurrence"))
        if isinstance(recurrence, dict) and isinstance(recurrence.get("until"), str):
            recurrence = dict(recurrence)
            recurrence["until"] = (parse_iso(recurrence["until"])
                                   or parse_iso(row.get("scheduled_at")) or utcnow())
        return ScheduledTask(
            id=row["id"],
            title=row["title"],
            description=row.get("description") or "",
            created_at=parse_iso(row.get("created_at")) or utcnow(),
            scheduled_at=parse_iso(row.get("scheduled_at")) or utcnow(),
            timezone_name=row.get("timezone_name") or "UTC",
            status=row.get("status") or "pending",
            execution_type=row.get("execution_type") or "notification",
            script_path=row.get("script_path"),
            model_config=json_load(row.get("model_config")),
            task_payload=json_load(row.get("task_payload")),
            retry_policy=json_load(row.get("retry_policy")) or {},
            metadata=json_load(row.get("metadata")),
            attempts=int(row.get("attempts") or 0),
            last_error=row.get("last_error"),
            last_run_at=parse_iso(row.get("last_run_at")),
            lease_token=row.get("lease_token"),
            recurrence=recurrence,
            runs=int(row.get("runs") or 0),
            created_by=row.get("created_by") or "agent",
            reason=row.get("reason") or "",
        )


# ---------------------------------------------------------------------------
# Notifications / proactive conversation payloads
# ---------------------------------------------------------------------------
@dataclass
class Notification:
    title: str
    message: str
    id: str = field(default_factory=lambda: new_id("notif"))
    created_at: datetime = field(default_factory=utcnow)
    kind: str = "info"  # info | warning | error | proactive
    dedupe_key: Optional[str] = None
    action_hint: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    read: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["created_at"] = iso(self.created_at)
        return d
