"""Register the running system with the Observability Service.

Sections 14.2, 14.7, 14.16, 14.17 and 14.18: the System Map is generated from
this registration, so a component that registers here appears in the Debugger
without anyone editing a drawing.  Every status provider reads live state from
the component it describes.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from ..common.observability.registry import (
    SUBSYSTEMS,
    SystemComponent,
    SystemConnection,
)
from ..version import __version__


def _safe(fn: Callable[[], Any], default: Any = None) -> Any:
    try:
        return fn()
    except Exception:
        return default



#: Section 14.33: a panel headed by a name and a blank line says only
#: that the module exists. Each listener says what it watches.
LISTENER_DESCRIPTIONS = {
    "application":
        "Which application and window has focus, and for how long (section "
        "19)",
    "filesystem":
        "Files created, changed and removed in the watched directories "
        "(section 20)",
    "browser":
        "Pages visited, read from the browser's own history (section 21)",
    "app_integration":
        "Events sent in by an integrated desktop application over the local "
        "protocol",
}


def register_system(app: Any) -> None:
    """Register components and connections for a running application."""
    service = app.observability_service
    registry = service.registry

    # ---- subsystems (section 14.18) --------------------------------------
    for key, name in SUBSYSTEMS:
        registry.register_component(
            SystemComponent(id=key, name=name, component_type="subsystem",
                            description=f"{name} subsystem")
        )

    def component(component_id: str, name: str, parent: str, component_type: str,
                  *, status: Optional[Callable[[], Dict[str, Any]]] = None,
                  metrics: Optional[Callable[[], Dict[str, Any]]] = None,
                  dependencies: Optional[list] = None, description: str = "",
                  platform_implementation: Optional[str] = None) -> None:
        registry.register_component(
            SystemComponent(
                id=component_id, name=name, component_type=component_type,
                parent_component=parent, dependencies=dependencies or [],
                description=description, version=__version__,
                platform_implementation=platform_implementation,
                status_provider=status, metrics_provider=metrics,
            )
        )

    def connection(connection_id: str, source: str, target: str, connection_type: str,
                   interface: str = "", description: str = "",
                   status: Optional[Callable[[], Dict[str, Any]]] = None) -> None:
        registry.register_connection(
            SystemConnection(id=connection_id, source=source, target=target,
                             connection_type=connection_type, interface=interface,
                             description=description, status_provider=status)
        )

    # ---- frontend ---------------------------------------------------------
    component(
        "desktop_ui", "Desktop UI", "frontend", "ui",
        status=lambda: {"state": "running" if app.ui_attached else "stopped",
                        "detail": "character window and conversation interface"},
        description="Desktop agent window (sections 5-7)",
    )
    component(
        "api_server", "Local API", "frontend", "service",
        status=lambda: {"state": "running" if app.api else "stopped",
                        "endpoint": app.api_base_url},
        metrics=lambda: {"routes": len(app.api.routes) if app.api else 0},
        description="Loopback HTTP API and static frontend host",
    )
    component(
        "notification_service", "Notification Service", "frontend", "service",
        status=lambda: {
            "state": "running" if app.config.get("notifications_enabled") else "disabled",
            "unread": len(_safe(lambda: app.notifications.recent(limit=50, unread_only=True), [])),
        },
        metrics=lambda: {"recent": len(_safe(lambda: app.notifications.recent(limit=50), []))},
        description="Everything the agent says unprompted: delivery, quiet "
                    "hours and duplicate suppression (section 91)"
    )

    # ---- agent ------------------------------------------------------------
    component(
        "agent_core", "Agent Core", "agent", "core",
        status=lambda: _agent_status(app),
        metrics=lambda: {
            "tools": len(app.agent.tools.specs),
            "max_tool_iterations": app.agent.max_tool_iterations,
            **{k: v for k, v in app.observability.snapshot()["agent_metrics"]["counters"].items()
               if k.startswith("agent.")},
        },
        dependencies=["model_provider", "runtime", "scheduler"],
        description="Direct user instruction workflow (section 67): answered "
                    "from the instruction, nothing searched",
    )
    component(
        "dialog_store", "Conversations", "agent", "storage",
        status=lambda: {"state": "running",
                        "conversations": len(_safe(lambda: app.conversations.conversations(), []))},
        description="Conversations and their messages, as the user and the "
                    "agent exchanged them"
    )

    # ---- observation ------------------------------------------------------
    component(
        "listener_manager", "Listener Manager", "observation", "manager",
        status=lambda: _listener_manager_status(app),
        metrics=lambda: {"providers": len(app.listener.provider_ids())},
        description="Provider discovery, health and event routing (section 23)",
    )
    for provider_id, label in (
        ("application", "Application Listener"),
        ("filesystem", "Filesystem Listener"),
        ("browser", "Browser Listener"),
        ("app_integration", "Application Integrations"),
    ):
        component(
            f"listener_{provider_id}", label, "observation", "listener",
            description=LISTENER_DESCRIPTIONS.get(provider_id, ""),
            status=(lambda pid=provider_id: _listener_provider_status(app, pid)),
            metrics=(lambda pid=provider_id: _listener_provider_metrics(app, pid)),
            platform_implementation=app.platform.name,
        )
    component(
        "event_pipeline", "Event Pipeline", "observation", "pipeline",
        status=lambda: {"state": "running" if app.listener_running else "idle"},
        description="Normalized events on their way to raw storage (section 2.4)",
    )

    # ---- knowledge --------------------------------------------------------
    component(
        "knowledge_graph", "Memory Library", "knowledge", "storage",
        status=lambda: _graph_status(app),
        metrics=lambda: _safe(lambda: app.library.stats(), {}),
        description="The nodes and weighted edges holding what the agent "
                    "knows: who the user is, their world, and what they "
                    "made of the agent's decisions (sections 38-40)"
    )
    component(
        "raw_event_storage", "Raw Event Storage", "knowledge", "storage",
        status=lambda: {"state": "running"},
        description="Every event the Listener recorded, kept for "
                    "raw_activity_keep_days and then let go (section 22)"
    )
    component(
        "raw_edit_storage", "Raw Edit Storage", "knowledge", "storage",
        status=lambda: {"state": "running"},
        description="The agent's own audit trail: what it did to its data, "
                    "and what came of it (section 23)"
    )
    # ---- scheduling -------------------------------------------------------
    component(
        "scheduler", "Scheduler", "scheduling", "service",
        status=lambda: _scheduler_status(app),
        metrics=lambda: _safe(lambda: app.scheduler.status(), {}),
        dependencies=["runtime", "model_provider"],
        description="Tasks waiting for their moment, and what happened when "
                    "they ran (sections 52, 60)"
    )
    component(
        "heartbeat", "Agent Heartbeat", "scheduling", "heartbeat",
        description="The periodic four-step review: the calendar, who the "
                    "user is, what to do, and judging what is still true "
                    "(sections 34-37)",
        status=lambda: _heartbeat_status(app),
        metrics=lambda: _heartbeat_metrics(app),
        dependencies=["model_provider", "scheduler"],
    )

    # ---- models -----------------------------------------------------------
    component(
        "model_manager", "Model Manager", "models", "manager",
        status=lambda: {"state": "running",
                        "roles": {r: app.models.resolve(r).label
                                  for r in ("primary", "heartbeat",
                                            "scheduled_task")}},
        metrics=lambda: _model_manager_metrics(app),
        description="Which model answers which role, and whether it is "
                    "currently usable (sections 74-77)"
    )
    component(
        "model_provider", "Primary Model Provider", "models", "provider",
        status=lambda: _model_provider_status(app),
        metrics=lambda: _model_provider_metrics(app),
        dependencies=["credential_manager"],
        description="Section 76 provider abstraction; credentials never shown",
    )
    component(
        "credential_manager", "Credential Manager", "models", "service",
        status=lambda: {"state": "running",
                        "backend": _safe(lambda: app.credentials.backend, "unknown")},
        metrics=lambda: {"backend": _safe(lambda: app.credentials.backend, "unknown")},
        description="OS keychain or encrypted store (section 83)",
    )

    # ---- runtime ----------------------------------------------------------
    component(
        "runtime", "Runtime", "runtime", "service",
        description="Sandboxed execution of generated scripts, confined to "
                    "the agent's own workspace directory (sections 31-33)",
        status=lambda: {"state": "running", "isolation": app.runtime.isolation,
                        "workspace": str(app.policy.workspace_root)},
        metrics=lambda: _runtime_metrics(app),
        dependencies=["sandbox"],
    )
    component(
        "sandbox", "Sandbox", "runtime", "service",
        status=lambda: _sandbox_status(app),
        metrics=lambda: _runtime_metrics(app),
        platform_implementation=app.platform.sandbox.isolation_name,
        description="Section 33 execution isolation",
    )

    # ---- platform ---------------------------------------------------------
    component(
        "platform_layer", "Platform Layer", "platform", "layer",
        status=lambda: {"state": "running", "platform": app.platform.name,
                        "capabilities": app.platform.capabilities().to_dict()},
        metrics=lambda: app.platform.capabilities().to_dict(),
        platform_implementation=app.platform.name,
        description="The operating-system specific implementations the rest "
                    "of the application depends on (section 106)"
    )
    component(
        "event_bus", "Event Bus", "platform", "service",
        status=lambda: {"state": "running"},
        metrics=lambda: {"recent_events": len(_safe(lambda: app.bus.recent(limit=50), []))},
        description="In-process publish and subscribe, by which components "
                    "learn of each other's work without being wired "
                    "together"
    )
    component(
        "observability_service", "Observability Service", "platform", "service",
        status=lambda: {"state": "running"},
        metrics=lambda: service.traces.stats(),
        description="Section 14.29 - traces, metrics and diagnostics",
    )
    component(
        "protocol_service", "Agent Protocol", "platform", "service",
        status=lambda: {"state": "running" if app.identity else "unavailable",
                        "agent_id": app.identity.agent_id if app.identity else None},
        metrics=lambda: _safe(lambda: app.auth.describe(), {}),
        description="The local challenge-response endpoint that integrated "
                    "applications connect to"
    )

    # ---- connections (section 14.7) --------------------------------------
    connection("desktop_ui->api_server", "desktop_ui", "api_server", "local http",
               "REST over loopback", "The frontend reads everything through the API")
    connection("api_server->agent_core", "api_server", "agent_core", "in-process call",
               "AgentCore.handle_message")
    connection("agent_core->model_provider", "agent_core", "model_provider",
               "model invocation", "ModelManager.invoke(role=primary)",
               status=lambda: _model_link_status(app, "primary"))
    connection("agent_core->runtime", "agent_core", "runtime", "sandboxed execution",
               "SandboxRuntime.run_python_code")
    connection("agent_core->scheduler", "agent_core", "scheduler", "in-process call",
               "Scheduler.create_task")
    connection("agent_core->raw_edit_storage", "agent_core", "raw_edit_storage",
               "record", "RawEditRecorder.record")
    for provider_id in ("application", "filesystem", "browser", "app_integration"):
        connection(f"listener_{provider_id}->listener_manager", f"listener_{provider_id}",
                   "listener_manager", "event emission", "ListenerProvider.emit")
    connection("listener_manager->event_pipeline", "listener_manager", "event_pipeline",
               "internal event stream", "ListenerManager._on_event",
               "Normalized events, after the privacy filter (section 25)")
    connection("event_pipeline->raw_event_storage", "event_pipeline", "raw_event_storage",
               "persistence", "RawStore.add_event")
    connection("heartbeat->model_provider", "heartbeat", "model_provider",
               "model invocation", "ModelManager.invoke(role=heartbeat)",
               status=lambda: _model_link_status(app, "heartbeat"))
    connection("heartbeat->scheduler", "heartbeat", "scheduler", "in-process call",
               "Scheduler.create_task")
    connection("scheduler->runtime", "scheduler", "runtime", "sandboxed execution",
               "TaskExecutor.python_script")
    connection("scheduler->model_provider", "scheduler", "model_provider",
               "model invocation", "ModelManager.invoke(role=scheduled_task)",
               status=lambda: _model_link_status(app, "scheduled_task"))
    connection("runtime->sandbox", "runtime", "sandbox", "process isolation",
               "PlatformSandboxProvider.create_restricted_process")
    connection("model_manager->model_provider", "model_manager", "model_provider",
               "provider dispatch", "ModelProvider.invoke")
    connection("model_provider->credential_manager", "model_provider", "credential_manager",
               "credential lookup", "CredentialManager.get")
    connection("scheduler->notification_service", "scheduler", "notification_service",
               "notification", "NotificationService.notify")


# ---------------------------------------------------------------------------
# Status providers
# ---------------------------------------------------------------------------
def _agent_status(app: Any) -> Dict[str, Any]:
    active = app.observability.get_state("agent.active_task") or \
        app.observability.snapshot()["agent_metrics"]["active_task"]
    return {"state": "busy" if active else "idle", "current_task": active}


def _listener_manager_status(app: Any) -> Dict[str, Any]:
    health = _safe(lambda: app.listener.health(), {}) or {}
    providers = health.get("providers", [])
    states = {p["provider_id"]: p.get("state") for p in providers}
    if not health.get("running"):
        state = "stopped"
    elif any(s == "failed" for s in states.values()):
        state = "degraded"
    else:
        state = "running"
    return {"state": state, "providers": states}


def _provider_record(app: Any, provider_id: str) -> Dict[str, Any]:
    health = _safe(lambda: app.listener.health(), {}) or {}
    for provider in health.get("providers", []):
        if provider["provider_id"] == provider_id:
            return provider
    return {}


def _listener_provider_status(app: Any, provider_id: str) -> Dict[str, Any]:
    record = _provider_record(app, provider_id)
    if not record:
        return {"state": "unavailable", "detail": "provider not registered"}
    mapping = {
        "running": "running",
        "registered": "starting",
        "stopped": "stopped",
        "failed": "failed",
        "disabled_by_settings": "disabled",
    }
    return {
        "state": mapping.get(record.get("state", ""), "unavailable"),
        "events": record.get("events"),
        "last_error": record.get("last_error"),
        "category": record.get("category"),
    }


def _listener_provider_metrics(app: Any, provider_id: str) -> Dict[str, Any]:
    record = _provider_record(app, provider_id)
    return {"events": record.get("events", 0), "errors": record.get("errors", 0),
            "status": record.get("status", {})}


def _graph_status(app: Any) -> Dict[str, Any]:
    """What the memory holds, counted the way it is now stored.

    This asked for ``nodes`` and ``edges``, which is what the knowledge graph
    had. The library has folders of records and no edges at all, so the two
    keys were simply missing and inspecting the component in the Debugger
    raised ``KeyError: 'nodes'`` - the panel reporting a fault in the thing it
    was trying to describe.

    Read with defaults rather than by subscript, so that the next change to
    what the library counts degrades to a zero instead of to an exception.
    """
    stats = _safe(lambda: app.library.stats(), None)
    if stats is None:
        return {"state": "failed", "last_error": "the library folders are unreachable"}
    folders = stats.get("folders") or {}
    return {
        "state": "running",
        "records": stats.get("records", sum(folders.values())),
        "folders": len(folders),
        "bytes": stats.get("bytes", 0),
        "by folder": folders,
    }


def _heartbeat_status(app: Any) -> Dict[str, Any]:
    status = _safe(lambda: app.heartbeat.status(), {}) or {}
    if not status.get("enabled"):
        return {"state": "disabled"}
    report = status.get("last_report") or {}
    if report.get("blocked"):
        return {"state": "blocked", "reason": report.get("reason")}
    return {"state": "running" if status.get("running") else "stopped",
            "last_run": report.get("finished_at"),
            "interval_seconds": status.get("interval_seconds")}


def _heartbeat_metrics(app: Any) -> Dict[str, Any]:
    status = _safe(lambda: app.heartbeat.status(), {}) or {}
    report = status.get("last_report") or {}
    return {
        "last_heartbeat": report.get("finished_at"),
        "interval_seconds": status.get("interval_seconds"),
        "checklist": report.get("checklist"),
        "checklist_items": len(status.get("checklist", [])),
        "actions": report.get("actions"),
        "message_to_user": report.get("message_to_user"),
        "state_analysis": report.get("state_analysis"),
        "blocked": bool(report.get("blocked")),
    }


def _scheduler_status(app: Any) -> Dict[str, Any]:
    status = _safe(lambda: app.scheduler.status(), {}) or {}
    by_status = status.get("by_status", {})
    if not bool(app.config.get("scheduler_enabled", True)):
        state = "disabled"
    elif not status.get("running"):
        state = "stopped"
    elif by_status.get("running"):
        state = "busy"
    elif by_status.get("failed"):
        state = "degraded"
    else:
        state = "idle"
    return {"state": state, "last_tick": status.get("last_tick"),
            "pending": by_status.get("pending", 0), "failed": by_status.get("failed", 0)}


def _model_provider_status(app: Any) -> Dict[str, Any]:
    resolved = app.models.resolve("primary")
    availability = _safe(lambda: app.models.check_availability("primary"), None)
    if availability is None:
        return {"state": "unavailable", "model": resolved.label}
    if availability.available:
        state = "running"
    elif availability.requires_user_action():
        state = "blocked"
    else:
        state = "degraded"
    return {
        "state": state,
        "model": resolved.label,
        "availability": availability.state.value,
        "reason": availability.reason,
        "requires_user_action": availability.requires_user_action(),
    }


def _model_provider_metrics(app: Any) -> Dict[str, Any]:
    snapshot = app.observability.snapshot()["model_metrics"]
    provider = app.models.provider(app.models.resolve("primary").provider_id)
    auth = _safe(lambda: provider.get_auth_status().to_dict(), {}) if provider else {}
    # Section 14.25: authentication status is shown, credentials never are.
    return {
        "provider": app.models.resolve("primary").provider_id,
        "model": app.models.resolve("primary").model_id,
        "authentication_method": auth.get("method"),
        "authentication_status": auth.get("state"),
        "input_tokens": snapshot["totals"]["input"],
        "output_tokens": snapshot["totals"]["output"],
        "invocations": snapshot["totals"]["calls"],
        "errors": snapshot["totals"]["errors"],
        "average_latency_ms": snapshot["average_latency_ms"],
        "by_model": snapshot["by_model"],
    }


def _model_manager_metrics(app: Any) -> Dict[str, Any]:
    described = _safe(lambda: app.models.describe(), {}) or {}
    return {
        "providers": [p["provider"] for p in described.get("providers", [])],
        "roles": {role: info["label"] for role, info in described.get("roles", {}).items()},
        "fallback_enabled": described.get("fallback_enabled"),
    }


def _model_link_status(app: Any, role: str) -> Dict[str, Any]:
    availability = _safe(lambda: app.models.check_availability(role), None)
    if availability is None:
        return {"state": "unavailable"}
    if availability.available:
        return {"state": "healthy", "model": app.models.resolve(role).label}
    return {
        "state": "blocked" if availability.requires_user_action() else "degraded",
        "model": app.models.resolve(role).label,
        "reason": availability.reason,
        "availability": availability.state.value,
    }


def _sandbox_status(app: Any) -> Dict[str, Any]:
    violations = app.observability.snapshot()["agent_metrics"]["sandbox_violations"]
    return {
        "state": "degraded" if violations else "running",
        "isolation": app.platform.sandbox.isolation_name,
        "blocked_writes": len(violations),
        "workspace": str(app.policy.workspace_root),
    }


def _runtime_metrics(app: Any) -> Dict[str, Any]:
    snapshot = app.observability.snapshot()["agent_metrics"]
    return {
        "workspace": str(app.policy.workspace_root),
        "writable_roots": [str(app.policy.workspace_root)],
        "protected_paths": [str(p) for p in app.policy.protected_paths],
        "isolation": app.runtime.isolation,
        "executions": snapshot["counters"].get("runtime.executions", 0),
        "blocked_writes": snapshot["counters"].get("sandbox.violations", 0),
        "violations": snapshot["sandbox_violations"][-10:],
    }
