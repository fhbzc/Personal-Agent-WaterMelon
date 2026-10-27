"""A native saved screenshot reaches storage; directory exclusions still hold."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from support import TemporaryAgent
from test_screenshots import slide, _Activity
from test_listener_debugger import Provider
from personalized_agent.agent.states.store import State
from personalized_agent.common.events.models import ListenerEvent
from personalized_agent.common.file_noise import rules_from
from personalized_agent.debugger.listener import snapshot
from personalized_agent.heartbeat.experience.materials import Materials
from personalized_agent.listener.providers.screenshot import ScreenshotProvider



def _link_or_skip(case, make):
    """A symbolic link made - or the test skipped where links cannot be made
    (Windows without the right, or developer mode)."""
    try:
        make()
    except (OSError, NotImplementedError) as exc:
        case.skipTest(f"no symbolic links here: {exc}")

class ScreenshotIngestTests(unittest.TestCase):
    def setUp(self):
        self.fixture = TemporaryAgent()
        self.app = self.fixture.app
        self.app.config.set("listener_enabled", True)
        self.app.config.set("listener_screenshots_enabled", True)
        self.app.config.set("listener_only_while_present", False)
        self.app.config.set("listener_only_what_the_user_did", False)
        self.activity = _Activity()
        self.activity.window.title = "A synthetic test slide.pptx"
        self.activity.images = [slide()]
        self.provider = ScreenshotProvider(self.app.config, SimpleNamespace(activity=self.activity, name="test"))
        self.provider.DWELL = 0
        self.provider.PER_WINDOW = 0
        self.app.listener.register(self.provider)
        self.ocr = patch("personalized_agent.runtime.documents.ocr_picture", return_value="Synthetic public test chart")
        self.later = patch("personalized_agent.runtime.documents.read_later")
        self.ocr.start()
        self.later.start()

    def tearDown(self):
        self.later.stop()
        self.ocr.stop()
        self.fixture.cleanup()

    def capture(self):
        paths = []
        for _ in range(4):
            one = self.provider.look()
            if one: paths.append(one)
        return paths

    def events(self):
        return self.app.db.query("SELECT * FROM raw_events WHERE event_type='screen.shot'")

    def picture(self):
        path = self.fixture.workspace / "data/references/screens/2026-10-20/fixture.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 16), "blue").save(path)
        return path

    def shot(self, path, source="screenshot"):
        return ListenerEvent(event_type="screen.shot", source=source, application="WeChat",
                             data={"path":str(path), "window_title":"Synthetic test chart"})

    def test_capture_routes_once_and_can_be_linked_as_state_picture(self):
        [path] = self.capture()
        [row] = self.events()
        self.assertEqual(json.loads(row["data"])["path"], path)
        self.assertTrue(Path(path).is_file())
        self.assertFalse(self.app.listener.privacy.path_allowed(path))
        self.assertIsNone(self.provider._emitting_capture)
        state = self.app.states.add(State(title="Inspected a synthetic chart"))
        materials = Materials(self.app.db, Path(self.app.library.root) / "references")
        [linked] = materials.link(state, [row["id"]], {})
        self.assertEqual(linked["kind"], "picture")
        self.assertEqual(linked["copies"]["picture"], path)
        self.assertTrue(materials.linked(row["id"]))
        self.app.raw_store.prune_noise(**rules_from(self.app.config))
        self.assertEqual(len(self.events()), 1)
        visible = self.app.overview._worth_showing(self.app.raw_store.events_by_ids([row["id"]]))
        self.assertEqual([event.id for event in visible], [row["id"]])
        result = snapshot(self.app)
        self.assertFalse([i for i in result["issues"] if i["kind"].startswith("screenshot_")])
        [status] = self.app.listener.health()["providers"]
        self.assertEqual(status["events"], 1)
        self.assertEqual(status["routing"]["filtered"], 0)
        self.assertEqual(self.capture(), [])
        self.assertEqual(len(self.events()), 1)

    def test_file_activity_in_screenshot_folder_is_still_filtered(self):
        path = self.picture()
        provider = Provider("filesystem")
        self.app.listener.register(provider)
        provider.emit(ListenerEvent(event_type="filesystem.created", source="filesystem", data={"path":str(path)}))
        self.assertFalse(self.app.db.query("SELECT * FROM raw_events"))
        state = next(p for p in self.app.listener.health()["providers"] if p["provider_id"] == "filesystem")
        self.assertEqual(state["routing"]["filter_reasons"], {"directory_excluded":1})

    def test_source_string_and_native_emit_without_fresh_capture_are_not_proof(self):
        path = self.picture()
        self.provider.emit(self.shot(path))
        self.app.listener._on_event(self.shot(path))
        impersonator = Provider("app_integration")
        self.app.listener.register(impersonator)
        impersonator.emit(self.shot(path))
        self.assertEqual(self.events(), [])

    def test_native_capture_vouch_cannot_be_reused_during_event_delivery(self):
        self.app.bus.subscribe("listener.screen.shot", lambda _topic, payload:
            self.provider.emit(self.shot(Path(payload["data"]["path"]))))
        self.capture()
        self.assertEqual(len(self.events()), 1)
        routing = self.app.listener.health()["providers"][0]["routing"]
        self.assertEqual(routing["filter_reasons"], {"untrusted_or_invalid_screenshot":1})

    def test_even_a_native_pending_capture_cannot_escape_its_evidence_root(self):
        outside = self.fixture.dir / "outside.jpg"
        Image.new("RGB", (16,16), "red").save(outside)
        self.provider._emitting_capture = outside
        try:
            self.provider.emit(self.shot(outside))
        finally:
            self.provider._emitting_capture = None
        self.assertEqual(self.events(), [])

    def test_symlink_or_traversal_paths_cannot_gain_the_exception(self):
        path = self.picture()
        root = path.parents[1]
        outside = self.fixture.dir / "outside.jpg"
        Image.new("RGB", (16,16), "red").save(outside)
        alias = path.with_name("alias.jpg")
        _link_or_skip(self, lambda: alias.symlink_to(outside))
        traversal = root / "2026-10-20" / ".." / "2026-10-20" / "fixture.jpg"
        for candidate in (alias, traversal):
            with self.subTest(path=candidate.name):
                self.provider._emitting_capture = candidate
                self.provider.emit(self.shot(candidate))
        self.provider._emitting_capture = None
        self.assertEqual(self.events(), [])

    def test_symlink_in_the_evidence_root_is_rejected(self):
        root = self.fixture.workspace / "data/references/screens"
        root.parent.mkdir(parents=True, exist_ok=True)
        outside = self.fixture.dir / "outside-folder"
        outside.mkdir()
        _link_or_skip(self, lambda: root.symlink_to(outside, target_is_directory=True))
        self.capture()
        self.assertEqual(self.events(), [])

    def test_native_captures_still_respect_switches_and_excluded_apps(self):
        for key in ("listener_enabled", "listener_screenshots_enabled"):
            with self.subTest(key=key):
                self.app.config.set(key, False)
                self.assertEqual(self.capture(), [])
                self.app.config.set(key, True)
        self.app.config.set("listener_application_denylist", "POWERPNT")
        self.capture()
        self.assertEqual(self.events(), [])

    def test_disabled_after_save_cannot_be_persisted(self):
        original = self.provider._save
        def save(*args):
            path = original(*args)
            self.app.config.set("listener_screenshots_enabled", False)
            return path
        with patch.object(self.provider, "_save", side_effect=save):
            self.capture()
        self.assertEqual(self.events(), [])

    def test_credentials_and_filename_exclusions_still_block(self):
        with patch("personalized_agent.runtime.documents.ocr_picture", return_value="Password: hunter2"):
            self.assertEqual(self.capture(), [])
        self.activity.window.title = "Another synthetic chart.pptx"
        self.app.config.set("listener_excluded_file_patterns", "*.jpg")
        self.capture()
        self.assertEqual(self.events(), [])
        self.assertEqual(self.app.listener.health()["providers"][0]["routing"]["filter_reasons"],
                         {"file_pattern_excluded":1})
