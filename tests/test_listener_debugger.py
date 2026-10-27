"""Inspect the real routing and storage without collecting anything new."""
import json
import unittest
import socket
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from support import TemporaryAgent
from support import keyed
from personalized_agent.api.routes import build_routes
from personalized_agent.api.server import ApiServer, Request
from personalized_agent.common.events.models import ListenerEvent
from personalized_agent.common.logging.setup import SECRETS
from personalized_agent.debugger.listener import snapshot
from personalized_agent.listener.core.provider import ListenerProvider


class Provider(ListenerProvider):
    def __init__(self, name, status=None):
        super().__init__()
        self.provider_id = name
        self.category = name
        self.status = status or {"running": True}

    def start(self): pass
    def stop(self): pass
    def get_status(self): return self.status
    def supported_event_types(self): return []
    def get_capabilities(self): return {}


class ListenerDebuggerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = TemporaryAgent()
        self.app = self.fixture.app
        self.app.observability_service.set_monitoring(True)
        self.app.config.set("listener_enabled", True)
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        self.fixture.cleanup()

    def event(self, kind="screen.text", app="WeChat", source="screen", minutes=5, **data):
        event = ListenerEvent(event_type=kind, application=app, source=source,
                              timestamp=self.now - timedelta(minutes=minutes), data=data)
        self.app.raw_store.add_event(event)
        return event

    def test_real_content_and_links_are_read_without_changing_storage(self):
        event = self.event(text="some visible message", how="macos_vision_ocr")
        self.app.db.execute("INSERT INTO state_materials (state_id,event_id,kind,created_at) VALUES (?,?,?,?)",
                            ("state_fixture", event.id, "screen", self.now.isoformat()))
        before = self.app.db.query("SELECT * FROM raw_events")
        result = snapshot(self.app)
        self.assertEqual(result["applications"][0]["characters"], 20)
        self.assertEqual(result["applications"][0]["coverage"], "unverified")
        self.assertEqual(result["events"][0]["linked_states"], 1)
        self.assertIn("some visible message", result["events"][0]["data_preview"])
        self.assertEqual(before, self.app.db.query("SELECT * FROM raw_events"))

    def test_time_app_and_source_filters_apply_to_stored_bodies(self):
        self.event(text="wechat sample")
        self.event(app="ChatGPT", text="mentioning Reddit or 小红书 does not classify the app")
        self.event("browser.navigation", source="browser", app="Chrome", url="https://example.com")
        self.event(minutes=2000, text="too old")
        result = snapshot(self.app, application="WeChat", source="screen")
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["application"], "WeChat")
        self.assertEqual(len(result["applications"]), 3)
        self.assertFalse(snapshot(self.app, application="' OR 1=1 --")["events"])

    def test_fast_sampling_reports_actual_provider_policy_without_starting_collection(self):
        # FAST SAMPLING (fast_reading.py): chats, mail and social media read
        # often - as the reader says it is pacing them.
        fast = {'fastest_seconds': 3.0, 'slower': [[30.0, 5.0]],
                'cpu_budget_percent': 25, 'battery_floor_seconds': 5.0, 'on_battery': False,
                'over_budget': False, 'in_front': True, 'looks': 4, 'looks_unchanged': 3,
                'held_by_budget': 0}
        provider = Provider('screen', {'running': True, 'wechat_immediate_write': True,
                                       'focus_check_seconds': 1.0, 'fast': fast})
        self.app.listener.register(provider)
        settings = snapshot(self.app)['settings']
        self.assertEqual(settings['screen_interval_seconds'], 15)
        self.assertEqual(settings['focus_check_seconds'], 1.0)
        self.assertTrue(settings['wechat_immediate_write'])
        self.assertEqual(settings['fast']['fastest_seconds'], 3.0)
        self.assertTrue(settings['fast']['in_front'])

    def test_long_focus_gap_is_a_caution_not_a_loss_percentage(self):
        self.event(minutes=60, text="older visible text")
        self.event("application.focus_ended", source="application", minutes=5, duration_seconds=120)
        # A period overlapping the captured text is not a gap after it.
        self.event("application.focus_ended", source="application", minutes=50, duration_seconds=1200)
        unit = snapshot(self.app)["applications"][0]
        self.assertEqual(unit["long_focus_without_new_text"], 1)
        self.assertEqual(unit["coverage"], "unverified")
        self.assertNotIn("coverage_percent", unit)

    def test_screenshot_loss_and_nested_browser_error_are_visible(self):
        self.app.listener.register(Provider("screenshot", {"taken": 7}))
        self.app.listener.register(Provider("browser", {"running": True, "browsers": {"Safari":"query failed"}}))
        self.app.listener._states["browser"]["state"] = "running"
        result = snapshot(self.app)
        self.assertIn("screenshot_not_persisted", [i["kind"] for i in result["issues"]])
        self.assertNotIn("screenshot_path_excluded", [i["kind"] for i in result["issues"]])
        self.assertEqual(next(p for p in result["providers"] if p["id"] == "browser")["state"], "degraded")

    def test_per_app_failure_is_visible_even_when_another_app_read_succeeded(self):
        when = self.now.isoformat()
        self.app.listener.register(Provider('screen', {'running': True, 'last_error': None,
            'reader': {'last_error': '', 'applications': [
                {'application': 'WeChat', 'status': 'failed', 'last_attempt_at': when,
                 'last_failure_at': when, 'last_error': 'e5rtError <untrusted>', 'method': ''},
                {'application': 'Mail', 'status': 'ok', 'last_attempt_at': when,
                 'last_success_at': when, 'last_error': '', 'method': 'macos_accessibility'}]}}))
        self.app.listener._states['screen']['state'] = 'running'
        result = snapshot(self.app)
        self.assertEqual(result['providers'][0]['state'], 'degraded')
        issue = next(i for i in result['issues'] if i['kind'] == 'screen_read_failed')
        self.assertEqual(issue['application'], 'WeChat')
        apps = {a['application']: a for a in result['applications']}
        self.assertEqual(apps['WeChat']['reading']['status'], 'failed')
        self.assertEqual(apps['WeChat']['text_events'], 0)
        self.assertEqual(apps['Mail']['reading']['status'], 'ok')

    def test_a_successful_read_is_no_issue(self):
        when = self.now.isoformat()
        self.app.listener.register(Provider('screen', {'running': True, 'reader': {'applications': [
            {'application': 'WeChat', 'status': 'ok', 'last_attempt_at': when,
             'last_success_at': when, 'method': 'macos_vision_ocr'}]}}))
        self.app.listener._states['screen']['state'] = 'running'
        result = snapshot(self.app)
        self.assertEqual(result['providers'][0]['state'], 'running')
        self.assertEqual(result['issues'], [])

    def test_rejections_are_counted_without_keeping_denied_content(self):
        provider = Provider("filesystem")
        self.app.listener.register(provider)
        self.app.config.set("listener_only_while_present", False)
        self.app.config.set("listener_only_what_the_user_did", False)
        provider.emit(ListenerEvent(event_type="filesystem.created", source="filesystem", application="WeChat",
                                    data={"path": str(self.fixture.workspace / "data/references/screens/test.jpg"),
                                          "text": "denied-private-body"}))
        route = self.app.listener.health()["providers"][0]["routing"]
        self.assertEqual(route["received"], 1)
        self.assertEqual(route["filtered"], 1)
        self.assertEqual(route["filter_reasons"], {"directory_excluded": 1})
        self.assertFalse(self.app.db.query("SELECT * FROM raw_events"))
        self.assertNotIn("denied-private-body", json.dumps(self.app.listener.health()))
        # Health snapshots cannot be changed by later routing updates.
        provider.emit(ListenerEvent(event_type="filesystem.created", source="filesystem", data={"path":str(self.fixture.workspace / "other.jpg")}))
        self.assertEqual(route["received"], 1)

    def test_success_and_failure_counters_follow_actual_persistence(self):
        provider = Provider("screen")
        self.app.listener.register(provider)
        provider.emit(ListenerEvent(event_type="screen.text", source="screen", data={"text":"visible"}))
        with patch.object(self.app.raw_store, "add_event", side_effect=RuntimeError("fixture failure")):
            provider.emit(ListenerEvent(event_type="screen.text", source="screen", data={"text":"failed"}))
        state = self.app.listener.health()["providers"][0]
        self.assertEqual(state["events"], 1)
        self.assertEqual(state["routing"]["failed"], 1)
        self.assertTrue(state["routing"]["last_recorded_at"])

    def test_preview_redacts_registered_secrets_and_marks_truncation(self):
        secret = "listener-fixture-secret-78abcde"
        SECRETS.register(secret)
        self.event(text=secret + "x" * 17000)
        result = snapshot(self.app)
        self.assertNotIn(secret, json.dumps(result))
        self.assertTrue(result["events"][0]["preview_truncated"])

    def test_sampling_is_bounded_and_disclosed(self):
        for i in range(6): self.event(text=str(i), minutes=i)
        with patch("personalized_agent.debugger.listener.MAX_ROWS", 3):
            result = snapshot(self.app, hours=999, limit=1)
        self.assertTrue(result["window"]["sample_limited"])
        self.assertEqual(result["window"]["sample_events"], 3)
        self.assertEqual(result["window"]["hours"], 168)
        self.assertEqual(len(result["events"]), 1)

    def test_monitoring_off_does_not_read_raw_data(self):
        self.app.observability_service.set_monitoring(False)
        with patch.object(self.app.db, "query", side_effect=AssertionError("read while off")):
            self.assertFalse(snapshot(self.app)["monitoring"])

    def test_route_is_read_only_and_absent_without_debugger(self):
        path = "/api/debugger/listener"
        routes = build_routes(self.app)
        self.assertNotIn(("POST", path), routes)
        result = routes[("GET", path)](Request("GET", path, {"hours":["-5"]}, {}, {}))
        self.assertEqual(result["window"]["hours"], 1)
        with patch("personalized_agent.api.routes.debugger_available", return_value=False):
            self.assertNotIn(("GET", path), build_routes(self.app))

    def test_http_requires_auth_and_serves_the_new_page_assets(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.app.config.set("api_port", port)
        server = ApiServer(self.app)
        server.start()
        base = f"http://127.0.0.1:{port}"
        try:
            with self.assertRaises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(base + "/api/debugger/listener", timeout=5)
            self.assertEqual(denied.exception.code, 401)
            with urllib.request.urlopen(keyed(self.app, base + "/api/debugger/listener"), timeout=5) as response:
                self.assertTrue(json.load(response)["monitoring"])
            for asset in ("/listener-debugger.js", "/listener-debugger.css"):
                with urllib.request.urlopen(base + asset, timeout=5) as response:
                    self.assertEqual(response.status, 200)
        finally:
            server.stop()
