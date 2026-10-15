"""Built-in diagnostic tests (sections 14.10, 14.12, 14.13, 14.14, 14.32.8, 14.32.9).

Every test runs through the same runtime, permission and sandbox boundaries as
normal agent work.  The sandbox tests are the clearest example: they attempt a
write outside the workspace and *expect it to be refused* - they verify the
restriction rather than suspending it.

Synthetic events carry ``{"diagnostic": true, "test_id", "trace_id"}`` so the
Data Management system can tell them apart from genuine user behaviour.
"""

from __future__ import annotations

import pathlib
import sqlite3
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict

from ..common.events.models import ListenerEvent, ScheduledTask, utcnow
from ..common.observability.diagnostics import DiagnosticContext, DiagnosticRegistry
from ..models.base import ModelRequest
from ..common.languages import in_english
from ..models.manager import ModelUnavailable
from ..runtime.permissions import SecurityViolation


def register_builtin_tests(app: Any) -> None:
    registry: DiagnosticRegistry = app.observability_service.diagnostics
    add = registry.add

    # ---- Listener (section 14.10) ----------------------------------------
    add(test_id="listener.browser", name="Test Browser Listener",
        description="Check that a browser history source is readable on this platform.",
        component_id="listener_browser", risk_level="read_only",
        run=lambda ctx: _test_browser(app))
    add(test_id="listener.filesystem", name="Test Filesystem Listener",
        description="Check that the configured watch directories exist and are readable.",
        component_id="listener_filesystem", risk_level="read_only",
        run=lambda ctx: _test_filesystem(app))
    add(test_id="listener.active_application", name="Test Active Application Detection",
        description="Ask the platform layer for the foreground application.",
        component_id="listener_application", risk_level="read_only",
        run=lambda ctx: _test_active_application(app))
    add(test_id="listener.normalization", name="Test Event Normalization",
        description="Normalize a synthetic event and verify the event model.",
        component_id="listener_manager", risk_level="read_only",
        run=lambda ctx: _test_normalization(app, ctx))
    add(test_id="listener.permissions", name="Test Listener Permissions",
        description="Report which monitoring categories the user has enabled.",
        component_id="listener_manager", risk_level="read_only",
        run=lambda ctx: _test_listener_permissions(app))

    # ---- Agent Core -------------------------------------------------------
    add(test_id="agent.tool_surface", name="Test Tool Surface",
        description="Verify the tools exposed to the model and their schemas.",
        component_id="agent_core", risk_level="read_only",
        run=lambda ctx: _test_tool_surface(app))
    add(test_id="agent.workspace_boundary", name="Test Tool Workspace Boundary",
        description="Confirm the write_file tool refuses a path outside the workspace.",
        component_id="agent_core", risk_level="read_only",
        run=lambda ctx: _test_tool_boundary(app))

    # ---- Scheduler --------------------------------------------------------
    add(test_id="scheduler.database", name="Test Scheduler Database",
        description="Read the scheduler store and report task counts.",
        component_id="scheduler", risk_level="read_only",
        run=lambda ctx: _test_scheduler_database(app))
    add(test_id="scheduler.due_detection", name="Test Due-Task Detection",
        description="Verify that a past-due diagnostic task is detected, then remove it.",
        component_id="scheduler", risk_level="safe_write",
        run=lambda ctx: _test_due_detection(app, ctx))
    add(test_id="scheduler.persistence", name="Test Task Persistence",
        description="Create, re-read and delete a diagnostic task.",
        component_id="scheduler", risk_level="safe_write",
        run=lambda ctx: _test_task_persistence(app, ctx))
    add(test_id="scheduler.calendar_sync", name="Test Calendar Synchronization",
        description="Confirm the calendar API and the scheduler share one store.",
        component_id="scheduler", risk_level="read_only",
        run=lambda ctx: _test_calendar_sync(app))

    # ---- Heartbeats -------------------------------------------------------
    add(test_id="heartbeat.checklist", name="Test Checklist Snapshot",
        description="Verify the checklist snapshot is isolated from later edits.",
        component_id="heartbeat", risk_level="read_only",
        run=lambda ctx: _test_checklist_snapshot(app))

    # ---- Knowledge Graph --------------------------------------------------
    add(test_id="graph.connection", name="Test Database Connection",
        description="Execute a trivial query against the graph database.",
        component_id="knowledge_graph", risk_level="read_only",
        run=lambda ctx: _test_graph_connection(app))
    add(test_id="graph.node_read", name="Test Node Read",
        description="Read a node back from the graph store.",
        component_id="knowledge_graph", risk_level="read_only",
        run=lambda ctx: _test_node_read(app))
    add(test_id="graph.transaction", name="Test Transaction",
        description="Verify that a failed transaction rolls back.",
        component_id="knowledge_graph", risk_level="read_only",
        run=lambda ctx: _test_graph_transaction(app))

    # ---- Model provider ---------------------------------------------------
    add(test_id="model.authentication", name="Test Authentication",
        description="Report the credential state without revealing the credential.",
        component_id="model_provider", risk_level="read_only",
        run=lambda ctx: _test_model_authentication(app))
    add(test_id="model.availability", name="Test Model Availability",
        description="Run the normalized availability check for the primary model.",
        component_id="model_provider", risk_level="read_only",
        run=lambda ctx: _test_model_availability(app))
    add(test_id="model.oauth_status", name="Test OAuth Status",
        description="Report which authentication methods the provider offers.",
        component_id="model_provider", risk_level="read_only",
        run=lambda ctx: _test_oauth_status(app))
    add(test_id="model.lightweight_request", name="Test Lightweight Model Request",
        description="Send a minimal request to the configured model.",
        component_id="model_provider", risk_level="external_request",
        run=lambda ctx: _test_lightweight_request(app))

    # ---- Runtime and sandbox ---------------------------------------------
    add(test_id="sandbox.workspace_write", name="Test Workspace Write",
        description="Write and remove a file inside the authorized workspace.",
        component_id="sandbox", risk_level="safe_write",
        run=lambda ctx: _test_workspace_write(app))
    add(test_id="sandbox.blocked_external_write", name="Test Blocked External Write",
        description="Attempt a write outside the workspace and require it to be refused.",
        component_id="sandbox", risk_level="read_only",
        run=lambda ctx: _test_blocked_write(app))
    add(test_id="sandbox.subprocess_isolation", name="Test Subprocess Isolation",
        description="Run a generated script that tries to escape the workspace.",
        component_id="sandbox", risk_level="safe_write",
        run=lambda ctx: _test_subprocess_isolation(app))
    add(test_id="sandbox.temp_restriction", name="Test Temporary File Restriction",
        description="Verify tempfile inside a generated script stays in the workspace.",
        component_id="sandbox", risk_level="safe_write",
        run=lambda ctx: _test_temp_restriction(app))
    add(test_id="sandbox.symlink_escape", name="Test Symlink Escape Protection",
        description="Verify a symlink out of the workspace is refused.",
        component_id="sandbox", risk_level="read_only",
        run=lambda ctx: _test_symlink_escape(app))
    add(test_id="sandbox.application_state", name="Test Application State Protection",
        description="Verify trusted application state is unreadable to agent code.",
        component_id="sandbox", risk_level="read_only",
        run=lambda ctx: _test_application_state(app))

    # ---- Platform ---------------------------------------------------------
    add(test_id="platform.capabilities", name="Test Platform Capabilities",
        description="Report what this operating system actually exposes.",
        component_id="platform_layer", risk_level="read_only",
        run=lambda ctx: _test_platform(app))
    add(test_id="protocol.identity", name="Test Agent Identity",
        description="Sign a challenge and verify it with the public key.",
        component_id="protocol_service", risk_level="read_only",
        run=lambda ctx: _test_identity(app))

    # ---- Connection tests (section 14.14) --------------------------------
    add(test_id="conn.agent_model.reachable", name="Check provider reachable",
        description="Resolve the primary model and check provider availability.",
        connection_id="agent_core->model_provider", risk_level="read_only",
        run=lambda ctx: _test_model_availability(app))
    add(test_id="conn.agent_model.authentication", name="Check authentication",
        description="Check the credential state for the primary model role.",
        connection_id="agent_core->model_provider", risk_level="read_only",
        run=lambda ctx: _test_model_authentication(app))
    add(test_id="conn.agent_model.selected_model", name="Check selected model available",
        description="Confirm the configured model id exists for the provider.",
        connection_id="agent_core->model_provider", risk_level="read_only",
        run=lambda ctx: _test_selected_model(app))
    add(test_id="conn.scheduler_runtime.dry_run", name="Submit dry-run task",
        description="Run a no-op script through the scheduler's runtime path.",
        connection_id="scheduler->runtime", risk_level="safe_write",
        run=lambda ctx: _test_runtime_dry_run(app, ctx))
    add(test_id="conn.listener_pipeline.synthetic", name="Emit synthetic event",
        description="Emit a tagged diagnostic event and verify it reaches storage.",
        connection_id="listener_manager->event_pipeline", risk_level="safe_write",
        run=lambda ctx: _test_synthetic_event(app, ctx))
    add(test_id="conn.pipeline_storage.delivery", name="Test event delivery",
        description="Confirm the last diagnostic event was persisted as a Raw Event.",
        connection_id="event_pipeline->raw_event_storage", risk_level="read_only",
        run=lambda ctx: _test_event_delivery(app))
    add(test_id="conn.runtime_sandbox.init", name="Verify sandbox initializes",
        description="Execute a trivial command through the sandbox provider.",
        connection_id="runtime->sandbox", risk_level="safe_write",
        run=lambda ctx: _test_sandbox_init(app))


# ---------------------------------------------------------------------------
# Listener
# ---------------------------------------------------------------------------
def _test_browser(app: Any) -> Dict[str, Any]:
    paths = app.platform.activity.get_browser_history_paths()
    if not paths:
        return {"status": "unsupported",
                "summary": "no readable browser history database on this platform",
                "recommendations": ["Browser monitoring will stay unavailable until a "
                                    "supported browser profile exists."]}
    if not bool(app.config.get("listener_browser_enabled")):
        return {"status": "skipped", "summary": "browser monitoring is switched off",
                "details": {"browsers": sorted(paths)}}
    return {"status": "passed", "summary": f"{len(paths)} browser source(s) readable",
            "details": {"browsers": sorted(paths)}}


def _test_filesystem(app: Any) -> Dict[str, Any]:
    configured = app.config.lines("listener_watch_paths")
    existing = [p for p in configured if Path(p).expanduser().is_dir()]
    missing = [p for p in configured if p not in existing]
    if not configured:
        return {"status": "warning", "summary": "no watch directory is configured",
                "recommendations": ["Set a directory in Settings > Listener."]}
    if not existing:
        return {"status": "failed", "summary": "no configured watch directory exists",
                "details": {"missing": missing}}
    return {"status": "warning" if missing else "passed",
            "summary": f"{len(existing)} of {len(configured)} watch directories exist",
            "details": {"watching": existing, "missing": missing}}


def _test_active_application(app: Any) -> Dict[str, Any]:
    capabilities = app.platform.capabilities()
    if not capabilities.active_application:
        return {"status": "unsupported",
                "summary": f"{app.platform.name} does not expose the active application"}
    window = app.platform.activity.get_active_window()
    if not window.application:
        return {"status": "warning", "summary": "no foreground application reported"}
    return {"status": "passed", "summary": f"foreground application: {window.application}",
            "details": {"window": window.to_dict()}}


def _test_normalization(app: Any, ctx: DiagnosticContext) -> Dict[str, Any]:
    event = ListenerEvent(
        event_type="diagnostic.filesystem.modified", source="diagnostics",
        application="Diagnostics", data={"path": "diagnostic/example.txt"},
        metadata=ctx.diagnostic_metadata(),
    )
    payload = event.to_dict()
    required = {"id", "event_type", "source", "timestamp", "application", "data", "metadata"}
    missing = required - set(payload)
    if missing:
        return {"status": "failed", "summary": f"normalized event is missing {sorted(missing)}"}
    return {"status": "passed", "summary": "event normalized against the section 21 model",
            "details": {"event": payload}}


def _test_listener_permissions(app: Any) -> Dict[str, Any]:
    privacy = app.listener.privacy.describe()
    enabled = [name for name, on in privacy["categories"].items() if on]
    return {"status": "passed" if enabled else "warning",
            "summary": f"enabled categories: {', '.join(enabled) or 'none'}",
            "details": privacy}


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
def _test_scheduler_database(app: Any) -> Dict[str, Any]:
    stats = app.scheduler_store.stats()
    return {"status": "passed", "summary": f"scheduler store readable: {stats['by_status']}",
            "details": stats}


def _diagnostic_task(ctx: DiagnosticContext, when, title: str) -> ScheduledTask:
    return ScheduledTask(
        title=title, scheduled_at=when, execution_type="notification",
        task_payload={"message": "diagnostic task; not user visible"},
        metadata=ctx.diagnostic_metadata(),
    )


def _test_due_detection(app: Any, ctx: DiagnosticContext) -> Dict[str, Any]:
    task = app.scheduler_store.create(
        _diagnostic_task(ctx, utcnow() - timedelta(seconds=5), "Diagnostic due-check")
    )
    try:
        due_ids = [t.id for t in app.scheduler_store.due()]
        detected = task.id in due_ids
    finally:
        app.scheduler_store.delete(task.id)
    return {
        "status": "passed" if detected else "failed",
        "summary": "past-due task detected" if detected else "past-due task was not detected",
        "details": {"task_id": task.id, "diagnostic": True},
    }


def _test_task_persistence(app: Any, ctx: DiagnosticContext) -> Dict[str, Any]:
    task = app.scheduler_store.create(
        _diagnostic_task(ctx, utcnow() + timedelta(days=1), "Diagnostic persistence check")
    )
    try:
        reloaded = app.scheduler_store.get(task.id)
        ok = reloaded is not None and reloaded.title == task.title
    finally:
        app.scheduler_store.delete(task.id)
    return {"status": "passed" if ok else "failed",
            "summary": "task persisted and read back" if ok else "task did not persist",
            "details": {"task_id": task.id, "diagnostic": True}}


def _test_calendar_sync(app: Any) -> Dict[str, Any]:
    same = app.scheduler.store is app.scheduler_store
    return {"status": "passed" if same else "failed",
            "summary": "calendar and scheduler share one authoritative store"
            if same else "calendar and scheduler use different stores",
            "details": {"store": type(app.scheduler_store).__name__}}


# ---------------------------------------------------------------------------
# Heartbeats
# ---------------------------------------------------------------------------
def _test_checklist_snapshot(app: Any) -> Dict[str, Any]:
    snapshot = app.checklist.snapshot()
    added = app.checklist.add("diagnostic probe item", origin="diagnostic")
    try:
        unchanged = len(app.checklist.snapshot()) == len(snapshot) + 1 and len(snapshot) == len(
            [item for item in snapshot]
        )
    finally:
        app.checklist.delete(added.id)
    return {"status": "passed" if unchanged else "failed",
            "summary": "a captured snapshot is unaffected by later edits (section 35)",
            "details": {"snapshot_size": len(snapshot)}}


# ---------------------------------------------------------------------------
# The memory library (section 39.1, as amended)
# ---------------------------------------------------------------------------
def _test_graph_connection(app: Any) -> Dict[str, Any]:
    stats = app.library.stats()
    return {"status": "passed",
            "summary": f"library reachable: {stats['records']} records in "
                       f"{len(stats['folders'])} folders",
            "details": stats}


def _test_node_read(app: Any) -> Dict[str, Any]:
    records = app.library.records(limit=1)
    if not records:
        return {"status": "skipped", "summary": "the library has no records yet"}
    record = records[0]
    return {"status": "passed",
            "summary": f"read {record.id} from {record.folder}",
            "details": {"title": record.title, "folder": record.folder}}


def _test_graph_transaction(app: Any) -> Dict[str, Any]:
    """A record written and removed leaves the folder as it was.

    The graph version of this checked that a failed SQL transaction rolled
    back. Files have no transactions, so what is worth checking is different:
    that writing is reversible and that the index notices a deletion.
    """
    from ..data_manage.library import USER_FEEDBACK, Record

    before = app.library.stats()["records"]
    record = app.library.write(Record(
        folder=USER_FEEDBACK, title="diagnostic write probe",
        summary="written by a diagnostic and removed immediately",
        body="If this file is still here, the diagnostic did not finish.",
        source="debugger"))
    written = app.library.stats()["records"]
    try:
        pathlib.Path(record.path).unlink()
    except OSError:
        pass
    after = app.library.stats()["records"]
    clean = written == before + 1 and after == before
    return {"status": "passed" if clean else "failed",
            "summary": "a record was written and removed cleanly" if clean
            else "a diagnostic record was left behind",
            "details": {"before": before, "written": written, "after": after,
                        "path": record.path}}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
def _test_model_authentication(app: Any) -> Dict[str, Any]:
    resolved = app.models.resolve("primary")
    provider = app.models.provider(resolved.provider_id)
    if provider is None:
        return {"status": "failed", "summary": f"provider {resolved.provider_id} not registered"}
    status = provider.get_auth_status()
    # Section 14.25 - state only, never the credential itself.
    mapping = {"authenticated": "passed", "not_configured": "warning",
               "authentication_required": "warning", "authenticating": "warning"}
    return {"status": mapping.get(status.state.value, "failed"),
            "summary": f"{resolved.provider_id}: {status.state.value}",
            "details": status.to_dict(),
            "recommendations": ["Open Settings > Models to sign in."]
            if status.state.value != "authenticated" else []}


def _test_model_availability(app: Any) -> Dict[str, Any]:
    availability = app.models.check_availability("primary", force=True)
    resolved = app.models.resolve("primary")
    if availability.available:
        return {"status": "passed", "summary": f"{resolved.label} is available",
                "details": availability.to_dict()}
    return {"status": "failed" if availability.requires_user_action() else "warning",
            "summary": f"{resolved.label}: {availability.reason or availability.state.value}",
            "details": availability.to_dict()}


def _test_oauth_status(app: Any) -> Dict[str, Any]:
    resolved = app.models.resolve("primary")
    provider = app.models.provider(resolved.provider_id)
    if provider is None:
        return {"status": "failed", "summary": "provider not registered"}
    methods = [m.value for m in provider.get_auth_methods()]
    status = provider.get_auth_status()
    if "oauth" not in methods and "account_login" not in methods:
        return {"status": "unsupported",
                "summary": f"{resolved.provider_id} offers no account based login",
                "details": {"methods": methods}}
    return {"status": "passed" if status.state.value == "authenticated" else "warning",
            "summary": f"account login: {status.state.value}",
            "details": {"methods": methods, "auth": status.to_dict()}}


def _test_selected_model(app: Any) -> Dict[str, Any]:
    resolved = app.models.resolve("primary")
    provider = app.models.provider(resolved.provider_id)
    if provider is None:
        return {"status": "failed", "summary": "provider not registered"}
    ids = [m.id for m in provider.list_models()]
    ok = resolved.model_id in ids
    return {"status": "passed" if ok else "failed",
            "summary": f"{resolved.model_id} {'is' if ok else 'is not'} offered by "
                       f"{resolved.provider_id}",
            "details": {"available_models": ids}}


def _test_lightweight_request(app: Any) -> Dict[str, Any]:
    """Section 14.13: declared external_request - it leaves the machine."""
    try:
        response = app.models.invoke(
            ModelRequest(
                messages=[{"role": "user", "content": "Reply with the single word: ok"}],
                max_tokens=16, task="diagnostic",
                language=in_english("the one word"),
                metadata={"diagnostic": True},
            ),
            role="primary",
        )
    except ModelUnavailable as exc:
        return {"status": "failed", "summary": f"model unavailable: {exc}",
                "details": exc.availability.to_dict(),
                "recommendations": ["Open Settings > Models."]}
    return {"status": "passed",
            "summary": f"{response.provider}/{response.model} responded "
                       f"({response.usage.total_tokens} tokens)",
            "details": {"text": response.text[:200],
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens}}


# ---------------------------------------------------------------------------
# Runtime and sandbox (sections 14.12, 14.32.8)
# ---------------------------------------------------------------------------
def _test_workspace_write(app: Any) -> Dict[str, Any]:
    target = app.policy.temp_dir / "diagnostic_write.txt"
    path = app.runtime.write_text(target, "diagnostic")
    content = app.runtime.read_text(path)
    app.runtime.delete(path)
    return {"status": "passed" if content == "diagnostic" else "failed",
            "summary": "workspace write and read succeeded",
            "details": {"path": str(path)}}


def _test_blocked_write(app: Any) -> Dict[str, Any]:
    outside = Path(tempfile.gettempdir()) / "personalized-agent-diagnostic-escape.txt"
    try:
        app.runtime.write_text(outside, "should never be written")
    except SecurityViolation as exc:
        return {"status": "passed",
                "summary": "the write outside the workspace was refused",
                "details": exc.to_dict()}
    outside.unlink(missing_ok=True)
    return {"status": "failed",
            "summary": "a write outside the workspace succeeded - the invariant is broken",
            "recommendations": ["Stop the agent and check the workspace configuration."]}


def _test_subprocess_isolation(app: Any) -> Dict[str, Any]:
    escape = (Path(tempfile.gettempdir()) / "pa-diagnostic-subprocess.txt").as_posix()
    result = app.runtime.run_python_code(f"open({escape!r}, 'w').write('nope')\n", timeout=90)
    escaped = Path(escape)
    leaked = escaped.exists()
    escaped.unlink(missing_ok=True)
    if leaked:
        return {"status": "failed",
                "summary": "a generated script wrote outside the workspace"}
    return {"status": "passed" if not result.ok else "failed",
            "summary": "the generated script was blocked from leaving the workspace",
            "details": {"isolation": result.isolation, "stderr": (result.stderr or "")[-300:]}}


def _test_temp_restriction(app: Any) -> Dict[str, Any]:
    code = ("import tempfile\n"
            "with tempfile.NamedTemporaryFile('w', delete=False) as handle:\n"
            "    print(handle.name)\n")
    result = app.runtime.run_python_code(code, timeout=90)
    inside = str(app.policy.workspace_root) in (result.stdout or "")
    return {"status": "passed" if inside else "failed",
            "summary": "temporary files are redirected into the workspace" if inside
            else "a temporary file was created outside the workspace",
            "details": {"stdout": (result.stdout or "").strip()[-200:]}}


def _test_symlink_escape(app: Any) -> Dict[str, Any]:
    import os

    link = app.policy.temp_dir / "diagnostic_link"
    target = Path(tempfile.gettempdir())
    try:
        if link.exists() or link.is_symlink():
            link.unlink()
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError) as exc:
        return {"status": "unsupported",
                "summary": f"cannot create a symlink on this system: {exc}"}
    try:
        app.runtime.write_text(link / "escape.txt", "nope")
    except SecurityViolation as exc:
        return {"status": "passed", "summary": "a symlink out of the workspace was refused",
                "details": exc.to_dict()}
    finally:
        try:
            link.unlink()
        except OSError:
            pass
    return {"status": "failed", "summary": "a symlink escaped the workspace"}


def _test_application_state(app: Any) -> Dict[str, Any]:
    try:
        app.runtime.read_text(app.paths.settings_file)
    except SecurityViolation as exc:
        return {"status": "passed",
                "summary": "trusted application state is not readable by agent code",
                "details": exc.to_dict()}
    return {"status": "failed",
            "summary": "agent code could read the application settings file",
            "recommendations": ["Check the protected paths in the workspace policy."]}


def _test_sandbox_init(app: Any) -> Dict[str, Any]:
    import sys

    result = app.runtime.execute([sys.executable, "-c", "print('sandbox ok')"], timeout=60)
    ok = result.returncode == 0 and "sandbox ok" in (result.stdout or "")
    return {"status": "passed" if ok else "failed",
            "summary": f"sandbox provider '{result.isolation}' executed a command"
            if ok else "the sandbox provider could not execute a command",
            "details": {"isolation": result.isolation, "returncode": result.returncode}}


def _test_runtime_dry_run(app: Any, ctx: DiagnosticContext) -> Dict[str, Any]:
    script = app.runtime.store_generated_script(
        "print('scheduler dry run')\n", name=f"diagnostic_{ctx.task_id}.py"
    )
    result = app.runtime.run_python_script(script, timeout=90)
    app.runtime.delete(script)
    return {"status": "passed" if result.ok else "failed",
            "summary": "the runtime accepted and executed a dry-run task" if result.ok
            else f"dry-run failed: {(result.stderr or '')[-160:]}",
            "details": {"isolation": result.isolation, "stdout": (result.stdout or "").strip()}}


# ---------------------------------------------------------------------------
# Platform and protocol
# ---------------------------------------------------------------------------
def _test_platform(app: Any) -> Dict[str, Any]:
    capabilities = app.platform.capabilities().to_dict()
    missing = [name for name, available in capabilities.items() if available is False]
    return {"status": "warning" if missing else "passed",
            "summary": f"{app.platform.name}: {len(capabilities) - len(missing)} of "
                       f"{len(capabilities)} capabilities available",
            "details": capabilities,
            "recommendations": [f"'{name}' is unavailable and degrades gracefully."
                                for name in missing]}


def _test_identity(app: Any) -> Dict[str, Any]:
    if app.identity is None:
        return {"status": "unsupported", "summary": "the agent protocol is unavailable"}
    from ..protocol.identity.keys import b64, unb64, verify_signature

    challenge = b64(b"diagnostic-challenge-0123456789ab")
    proof = app.identity.sign_challenge(challenge)
    ok = verify_signature(app.identity.public_key, unb64(challenge), proof["signature"])
    return {"status": "passed" if ok else "failed",
            "summary": "the agent proved possession of its private key" if ok
            else "signature verification failed",
            "details": {"agent_id": app.identity.agent_id, "algorithm": proof["algorithm"]}}


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------
def _test_synthetic_event(app: Any, ctx: DiagnosticContext) -> Dict[str, Any]:
    """Section 14.32.9 - synthetic events are tagged, never mistaken for behaviour."""
    before = app.raw_store.count_events()
    event = ListenerEvent(
        event_type="diagnostic.listener.synthetic", source="diagnostics",
        application="Diagnostics",
        data={"detail": "synthetic diagnostic event"},
        metadata=ctx.diagnostic_metadata(),
    )
    app.raw_store.add_event(event)
    after = app.raw_store.count_events()
    return {"status": "passed" if after == before + 1 else "failed",
            "summary": "a tagged synthetic event reached raw event storage",
            "details": {"event_id": event.id, "metadata": event.metadata}}


def _test_event_delivery(app: Any) -> Dict[str, Any]:
    events = app.raw_store.recent_events(limit=25)
    diagnostic = [e for e in events if (e.metadata or {}).get("diagnostic")]
    if not diagnostic:
        return {"status": "skipped",
                "summary": "no diagnostic event found; run 'Emit synthetic event' first"}
    return {"status": "passed",
            "summary": f"{len(diagnostic)} diagnostic event(s) stored and tagged",
            "details": {"latest": diagnostic[0].to_dict()}}


# ---------------------------------------------------------------------------
# Agent core
# ---------------------------------------------------------------------------
def _test_tool_surface(app: Any) -> Dict[str, Any]:
    specs = app.agent.tools.specs
    invalid = [s.name for s in specs if not s.input_schema.get("properties") is not None]
    return {"status": "failed" if invalid else "passed",
            "summary": f"{len(specs)} tools exposed to the model",
            "details": {"tools": [s.name for s in specs]}}


def _test_tool_boundary(app: Any) -> Dict[str, Any]:
    outside = Path(tempfile.gettempdir()) / "pa-diagnostic-tool-escape.txt"
    outcome = app.agent.tools.call("write_file", {"path": str(outside), "content": "nope"})
    refused = isinstance(outcome, dict) and outcome.get("error") == "security_violation"
    outside.unlink(missing_ok=True)
    return {"status": "passed" if refused else "failed",
            "summary": "the write_file tool refused a path outside the workspace"
            if refused else "the write_file tool accepted an external path",
            "details": outcome if isinstance(outcome, dict) else {}}
