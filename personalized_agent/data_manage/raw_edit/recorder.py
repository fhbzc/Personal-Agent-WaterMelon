"""Agent activity recording (section 47).

Every meaningful agent action becomes a Raw Edit: cheap to write, and read
as it is - nothing turns it into a record of its own later. Traceability,
debugging and learning read from here, and it is let go by age with the
rest of the raw activity.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ...common.events.models import RawEdit
from ..storage.raw_store import RawStore

log = logging.getLogger(__name__)


class RawEditRecorder:
    def __init__(self, store: RawStore, bus: Any = None, observability: Any = None) -> None:
        self.store = store
        self.bus = bus
        self.observability = observability

    def record(
        self,
        event_type: str,
        *,
        source: str = "agent",
        task_id: Optional[str] = None,
        instruction: Optional[str] = None,
        accessed_information: Optional[List[str]] = None,
        action: Optional[str] = None,
        result: Optional[str] = None,
        related_nodes: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RawEdit:
        edit = RawEdit(
            event_type=event_type,
            source=source,
            task_id=task_id,
            instruction=instruction,
            accessed_information=accessed_information or [],
            action=action,
            result=result,
            related_nodes=related_nodes or [],
            metadata=metadata or {},
        )
        try:
            self.store.add_edit(edit)
        except Exception:
            log.exception("failed to persist raw edit %s", event_type)
            return edit
        if self.observability is not None:
            self.observability.increment("raw_edits.recorded")
        if self.bus is not None:
            self.bus.publish("raw_edit.recorded", edit.to_dict())
        return edit

    # -- convenience wrappers ----------------------------------------------
    def instruction_received(self, instruction: str, task_id: str, source: str = "user") -> RawEdit:
        return self.record("agent.instruction_received", source=source, task_id=task_id,
                           instruction=instruction)

    def context_retrieved(self, task_id: str, retrieval: Dict[str, Any]) -> RawEdit:
        return self.record(
            "agent.context_retrieved",
            task_id=task_id,
            accessed_information=[f"node:{nid}" for nid in retrieval.get("node_ids", [])],
            related_nodes=list(retrieval.get("node_ids", [])),
            action="knowledge graph traversal",
            result=retrieval.get("stopped_because"),
            metadata={"path": retrieval.get("path", []),
                      "tokens_used": retrieval.get("tokens_used"),
                      "global_escapes": retrieval.get("global_escapes")},
        )

    def tool_used(self, task_id: str, tool: str, arguments: Dict[str, Any],
                  result: str, ok: bool = True) -> RawEdit:
        return self.record(
            f"agent.tool.{tool}",
            task_id=task_id,
            action=f"{tool}({_short(arguments)})",
            result=_short(result, 800),
            metadata={"ok": ok, "arguments": arguments},
        )

    def agent_response(self, task_id: str, instruction: Optional[str], response: str) -> RawEdit:
        return self.record("agent.response", task_id=task_id, instruction=instruction,
                           action="responded to the user", result=_short(response, 2000))

    def heartbeat_action(self, task_id: str, action: str, result: str) -> RawEdit:
        return self.record("heartbeat.action", source="heartbeat", task_id=task_id,
                           action=action, result=_short(result, 800))

    def scheduler_event(self, task_id: str, event_type: str, action: str, result: str,
                        metadata: Optional[Dict[str, Any]] = None) -> RawEdit:
        return self.record(event_type, source="scheduler", task_id=task_id, action=action,
                           result=_short(result, 800), metadata=metadata or {})

    def model_operation(self, task_id: str, description: str, metadata: Dict[str, Any]) -> RawEdit:
        return self.record("agent.model_operation", task_id=task_id, action=description,
                           metadata=metadata)


def _short(value: Any, limit: int = 300) -> str:
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= limit else text[:limit] + " ..."
