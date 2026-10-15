"""System Debugger (section 14).

Covers the runtime component registry, structured tracing, the component and
connection inspectors, the diagnostic test registry and its security rules, the
export report, and the API surface the Debugger UI reads.
"""

from __future__ import annotations

import json
import socket
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from support import TemporaryAgent, keyed

from personalized_agent.common.observability.diagnostics import (
    RESULT_STATES,
    RISK_LEVELS,
    DiagnosticRegistry,
    DiagnosticTest,
)
from personalized_agent.common.observability.registry import (
    COMPONENT_STATES,
    SUBSYSTEMS,
)
from personalized_agent.common.observability.tracing import TraceStore, Tracer


class TopologyTests(unittest.TestCase):
    """Sections 14.2, 14.7, 14.16, 14.18 - the map comes from registration."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.service = self.app.observability_service

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_every_subsystem_is_registered(self) -> None:
        ids = {c.id for c in self.service.registry.components()}
        for key, _name in SUBSYSTEMS:
            self.assertIn(key, ids)

    def test_core_components_are_registered(self) -> None:
        ids = {c.id for c in self.service.registry.components()}
        for expected in ("agent_core", "listener_manager", "event_pipeline", "scheduler",
                         "heartbeat", "knowledge_graph", "model_provider",
                         "credential_manager", "runtime", "sandbox", "platform_layer",
                         "observability_service"):
            self.assertIn(expected, ids)

    def test_components_belong_to_a_subsystem(self) -> None:
        subsystem_ids = {key for key, _ in SUBSYSTEMS}
        for component in self.service.registry.components():
            if component.component_type == "subsystem":
                continue
            self.assertIn(component.parent_component, subsystem_ids, component.id)

    def test_connections_reference_registered_components(self) -> None:
        ids = {c.id for c in self.service.registry.components()}
        connections = self.service.registry.connections()
        self.assertGreater(len(connections), 10)
        for connection in connections:
            self.assertIn(connection.source, ids)
            self.assertIn(connection.target, ids)

    def test_topology_reports_live_state(self) -> None:
        topology = self.service.topology()
        states = {node["id"]: node["state"] for node in topology["components"]}
        for state in states.values():
            self.assertIn(state, COMPONENT_STATES)
        self.assertIn("application", topology)

    def test_a_new_component_appears_without_touching_the_ui(self) -> None:
        from personalized_agent.common.observability.registry import SystemComponent

        self.service.registry.register_component(
            SystemComponent(id="future_plugin", name="Future Plugin",
                            component_type="integration", parent_component="observation",
                            status_provider=lambda: {"state": "running"})
        )
        ids = [n["id"] for n in self.service.topology()["components"]]
        self.assertIn("future_plugin", ids)

    def test_disabled_is_distinct_from_failed(self) -> None:
        """Section 14.17 - a switched-off component is not a broken one."""
        self.app.config.set("heartbeat_enabled", False)
        state = self.service.registry.component("heartbeat").status()["state"]
        self.assertEqual(state, "disabled")

    def test_a_broken_status_provider_degrades_instead_of_raising(self) -> None:
        component = self.service.registry.component("agent_core")
        component.status_provider = lambda: 1 / 0
        status = component.status()
        self.assertEqual(status["state"], "degraded")
        self.assertIn("status provider failed", status["last_error"])


class TracingTests(unittest.TestCase):
    """Sections 14.5, 14.6, 14.28."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.service = self.app.observability_service

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_an_instruction_produces_one_end_to_end_trace(self) -> None:
        result = self.app.agent.handle_message("what am I working on?")
        trace = self.service.trace_detail(result["trace_id"])
        components = trace["components"]
        self.assertIn("agent_core", components)
        self.assertIn("model_provider", components)
        self.assertTrue(all(s["trace_id"] == result["trace_id"] for s in trace["steps"]))

    def test_spans_carry_the_structured_fields(self) -> None:
        self.app.agent.handle_message("hello")
        span = self.service.traces.query(component="agent_core", limit=1)[0].to_dict()
        for field in ("id", "trace_id", "span_id", "timestamp", "component", "operation",
                      "severity", "duration_ms", "status", "metadata"):
            self.assertIn(field, span)

    def test_secrets_never_reach_a_span(self) -> None:
        self.app.credentials.set("anthropic.api_key", "sk-ant-trace-secret-value")
        tracer = self.service.tracer_for("model_provider")
        tracer.event("model.invoke", inputs="key sk-ant-trace-secret-value used",
                     outputs={"api_key": "sk-ant-trace-secret-value"})
        payload = json.dumps([s.to_dict() for s in self.service.traces.all()])
        self.assertNotIn("sk-ant-trace-secret-value", payload)

    def test_retention_is_bounded(self) -> None:
        store = TraceStore(capacity=50)
        tracer = Tracer(store, "listener")
        for index in range(500):
            tracer.event(f"event-{index}")
        self.assertLessEqual(len(store.all()), 50)

    def test_high_volume_components_are_sampled_but_errors_are_kept(self) -> None:
        store = TraceStore(capacity=1000)
        store.set_sampling("listener", 5)
        tracer = Tracer(store, "listener")
        for _ in range(20):
            tracer.event("listener.event")
        sampled = len(store.all())
        self.assertLess(sampled, 20)
        tracer.event("listener.failure", severity="error", status="error")
        self.assertEqual(len(store.all()), sampled + 1)

    def test_the_heartbeat_is_traced(self) -> None:
        self.app.heartbeat.run_once(trigger="manual_debug")
        kinds = {t["kind"] for t in self.service.timeline(limit=20)}
        self.assertIn("heartbeat", kinds)

    def test_a_security_violation_is_traced_as_a_security_event(self) -> None:
        """Section 14.31.9 - distinguishable from a normal failure."""
        from personalized_agent.runtime.permissions import SecurityViolation

        with self.assertRaises(SecurityViolation):
            self.app.runtime.write_text(Path(self.agent.dir) / "escape.txt", "x")
        security = [s for s in self.service.traces.all() if s.severity == "security"]
        self.assertTrue(security)
        self.assertEqual(security[0].status, "blocked")

    def test_historical_window_filters_traces(self) -> None:
        self.app.agent.handle_message("older instruction")
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        self.assertEqual(self.service.timeline(limit=10, since=future), [])
        self.assertTrue(self.service.timeline(limit=10))


class InspectorTests(unittest.TestCase):
    """Sections 14.31.3, 14.31.4."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.service = self.app.observability_service
        self.app.agent.handle_message("prime the traces")

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_component_inspector_sections(self) -> None:
        detail = self.service.component_detail("agent_core")
        self.assertEqual(detail["kind"], "component")
        for key in ("overview", "metrics", "traces", "tests", "errors"):
            self.assertIn(key, detail)
        overview = detail["overview"]
        for key in ("name", "id", "component_type", "state", "version", "uptime_seconds",
                    "dependencies", "incoming", "outgoing"):
            self.assertIn(key, overview)

    def test_connection_inspector_sections(self) -> None:
        detail = self.service.connection_detail("agent_core->model_provider")
        overview = detail["overview"]
        for key in ("source_name", "target_name", "connection_type", "interface",
                    "request_count", "failure_count", "average_latency_ms"):
            self.assertIn(key, overview)
        self.assertGreaterEqual(overview["request_count"], 1)
        self.assertTrue(detail["tests"])

    def test_unknown_ids_return_nothing(self) -> None:
        self.assertIsNone(self.service.component_detail("nope"))
        self.assertIsNone(self.service.connection_detail("nope"))

    def test_model_inspector_never_exposes_credentials(self) -> None:
        """Section 14.25."""
        self.app.credentials.set("anthropic.api_key", "sk-ant-inspector-secret")
        payload = json.dumps(self.service.component_detail("model_provider"))
        self.assertNotIn("sk-ant-inspector-secret", payload)
        self.assertIn("authentication_status", payload)

    def test_search_finds_components_connections_and_traces(self) -> None:
        found = self.service.search("model")
        self.assertTrue(found["components"])
        self.assertTrue(found["connections"])
        self.assertTrue(self.service.search("agent.instruction")["spans"])


class DiagnosticTests(unittest.TestCase):
    """Sections 14.10-14.15, 14.32.7-14.32.9."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.registry = self.app.observability_service.diagnostics

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_tests_are_registered_for_the_documented_components(self) -> None:
        for component_id in ("listener_manager", "scheduler", "model_provider",
                             "knowledge_graph", "sandbox", "agent_core"):
            self.assertTrue(self.registry.for_component(component_id), component_id)

    def test_connection_tests_exist(self) -> None:
        """Section 14.14."""
        for connection_id in ("agent_core->model_provider", "scheduler->runtime",
                              "listener_manager->event_pipeline"):
            self.assertTrue(self.registry.for_connection(connection_id), connection_id)

    def test_every_test_declares_a_known_risk_level(self) -> None:
        for test in self.registry.all():
            self.assertIn(test.risk_level, RISK_LEVELS)
            self.assertTrue(test.component_id or test.connection_id)

    def test_no_destructive_risk_level_is_used(self) -> None:
        """Section 14.13 - potentially destructive tests are not shipped."""
        for test in self.registry.all():
            self.assertNotEqual(test.risk_level, "state_changing")

    def test_a_result_has_the_documented_shape(self) -> None:
        result = self.registry.run("graph.connection")
        self.assertIn(result.status, RESULT_STATES)
        for field in ("test_id", "started_at", "completed_at", "summary", "details",
                      "trace_id", "recommendations"):
            self.assertIn(field, result.to_dict())

    def test_running_a_test_produces_a_trace(self) -> None:
        """Section 14.32.7 - a test execution has its own task and trace id."""
        result = self.registry.run("graph.node_read")
        trace = self.app.observability_service.trace_detail(result.trace_id)
        self.assertTrue(trace["steps"])
        self.assertTrue(trace["steps"][0]["metadata"].get("diagnostic"))

    def test_sandbox_test_verifies_the_block_instead_of_bypassing_it(self) -> None:
        """Section 14.32.8 - the Debugger is not an unrestricted execution path."""
        result = self.registry.run("sandbox.blocked_external_write")
        self.assertEqual(result.status, "passed")
        self.assertIn("refused", result.summary)
        self.assertEqual(result.details.get("error"), "security_violation")

    def test_application_state_stays_protected_during_diagnostics(self) -> None:
        result = self.registry.run("sandbox.application_state")
        self.assertEqual(result.status, "passed")

    def test_synthetic_events_are_tagged_as_diagnostic(self) -> None:
        """Section 14.32.9."""
        result = self.registry.run("conn.listener_pipeline.synthetic")
        self.assertEqual(result.status, "passed")
        events = self.app.raw_store.recent_events(limit=5)
        synthetic = [e for e in events if e.metadata.get("diagnostic")]
        self.assertTrue(synthetic)
        self.assertEqual(synthetic[0].metadata["test_id"], "conn.listener_pipeline.synthetic")
        self.assertIn("trace_id", synthetic[0].metadata)

    def test_unknown_test_is_reported_not_raised(self) -> None:
        result = self.registry.run("does.not.exist")
        self.assertEqual(result.status, "unsupported")

    def test_a_failing_test_is_captured(self) -> None:
        registry = DiagnosticRegistry()
        registry.register(DiagnosticTest("boom", "Boom", "raises", component_id="x",
                                         run=lambda ctx: 1 / 0))
        result = registry.run("boom")
        self.assertEqual(result.status, "failed")
        self.assertIn("ZeroDivisionError", result.summary)

    def test_history_records_executions(self) -> None:
        self.registry.run("graph.connection")
        self.assertTrue(self.registry.history(limit=5))

    def test_plugins_can_register_their_own_tests(self) -> None:
        """Section 14.15 - diagnostics are extensible."""
        self.registry.add(test_id="plugin.example", name="Plugin test",
                          description="registered by an integration",
                          component_id="listener_manager", risk_level="read_only",
                          run=lambda ctx: {"status": "passed", "summary": "ok"})
        self.assertIn("plugin.example",
                      [t.id for t in self.registry.for_component("listener_manager")])


class ExportTests(unittest.TestCase):
    """Section 14.27."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.app.agent.handle_message("prime")

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_report_contains_the_documented_material(self) -> None:
        report = self.app.observability_service.diagnostic_report(
            platform=self.app.platform.describe()
        )
        for key in ("topology", "component_states", "errors", "traces", "test_results",
                    "platform_capabilities", "application"):
            self.assertIn(key, report)

    def test_report_is_redacted(self) -> None:
        self.app.credentials.set("anthropic.api_key", "sk-ant-export-secret-value")
        self.app.observability_service.tracer_for("model_provider").event(
            "probe", outputs="sk-ant-export-secret-value"
        )
        payload = json.dumps(self.app.observability_service.diagnostic_report())
        self.assertNotIn("sk-ant-export-secret-value", payload)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class DebuggerApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.port = _free_port()
        self.app.config.set("api_port", self.port)
        from personalized_agent.api.server import ApiServer

        self.server = ApiServer(self.app)
        self.server.start()
        self.base = f"http://127.0.0.1:{self.port}"
        self.app.agent.handle_message("prime the debugger")

    def tearDown(self) -> None:
        self.server.stop()
        self.agent.cleanup()

    def get(self, path: str):
        with urllib.request.urlopen(keyed(self.app, f"{self.base}{path}"), timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def post(self, path: str, payload: dict):
        request = urllib.request.Request(
            f"{self.base}{path}", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(keyed(self.app, request), timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_topology_endpoint(self) -> None:
        topology = self.get("/api/debugger/topology")
        self.assertTrue(topology["components"])
        self.assertTrue(topology["connections"])
        self.assertTrue(topology["subsystems"])

    def test_component_and_connection_endpoints(self) -> None:
        component = self.get("/api/debugger/component?id=agent_core")
        self.assertEqual(component["overview"]["id"], "agent_core")
        connection = self.get("/api/debugger/connection?id=agent_core->model_provider")
        self.assertEqual(connection["kind"], "connection")

    def test_unknown_component_is_404(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/debugger/component?id=nope")
        self.assertEqual(ctx.exception.code, 404)

    def test_timeline_and_trace_endpoints(self) -> None:
        timeline = self.get("/api/debugger/timeline?limit=10")
        self.assertTrue(timeline["traces"])
        trace_id = timeline["traces"][0]["trace_id"]
        trace = self.get(f"/api/debugger/trace?id={trace_id}")
        self.assertTrue(trace["steps"])
        self.assertIn("compact", trace)

    def test_tests_endpoint_and_execution(self) -> None:
        listing = self.get("/api/debugger/tests?component=knowledge_graph")
        self.assertTrue(listing["tests"])
        result = self.post("/api/debugger/tests/run", {"test_id": "graph.connection"})
        self.assertEqual(result["status"], "passed")

    def test_search_endpoint(self) -> None:
        found = self.get("/api/debugger/search?q=scheduler")
        self.assertTrue(found["components"])

    def test_export_writes_into_the_workspace_only(self) -> None:
        result = self.post("/api/debugger/export", {})
        written = Path(result["written"])
        self.assertTrue(written.exists())
        self.assertTrue(str(written).startswith(str(self.app.policy.write_root)))

    def test_debugger_ui_assets_are_served(self) -> None:
        with urllib.request.urlopen(f"{self.base}/debugger.js", timeout=20) as response:
            body = response.read().decode("utf-8")
        self.assertIn("System Debugger", body)


if __name__ == "__main__":
    unittest.main()
