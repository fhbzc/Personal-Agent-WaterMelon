"""Section 19.2: real screen events from AX/OCR, without reading test users' screens."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from support import TemporaryAgent
from personalized_agent.platform_layer.macos_window_text import (
    FrontWindow, MacWindowTextReader, NativeReader,
)
from personalized_agent.listener.providers.screen import ScreenProvider


class Backend:
    def __init__(self):
        self.window = FrontWindow("WeChat", "Test chat", 999999, 123, (0, 0, 600, 500))
        self.front = Mock(side_effect=lambda: self.window)
        self.accessible_text = Mock(return_value=("", 4, False, True))
        self.ocr = Mock(return_value="测试消息：我计划明天下午三点提交草稿。")


class MacReadingTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.reader = MacWindowTextReader(self.backend)

    def test_accessibility_text_does_not_need_an_image(self):
        self.backend.window.application = 'Mail'
        body = "A visible message with enough content to read directly from Accessibility."
        self.backend.accessible_text.return_value = (body, 3, False, True)
        reading = self.reader.read(lambda front: True)
        self.assertEqual(reading.text, body)
        self.assertEqual(reading.how, "macos_accessibility")
        self.backend.ocr.assert_not_called()

    def test_a_custom_drawn_window_falls_back_to_ocr(self):
        reading = self.reader.read(lambda front: True)
        self.assertIn("明天下午三点", reading.text)
        self.assertEqual(reading.how, "macos_vision_ocr")
        self.assertEqual(reading.title, "Test chat")
        self.assertEqual(self.reader.status()["ocr_readings"], 1)

    def test_exclusion_prevents_both_readers_not_just_storage(self):
        self.assertIsNone(self.reader.read(lambda front: False))
        self.backend.accessible_text.assert_not_called()
        self.backend.ocr.assert_not_called()

    def test_secure_fields_prevent_ocr(self):
        self.backend.accessible_text.return_value = ("", 4, True, True)
        self.assertIsNone(self.reader.read(lambda front: True))
        self.backend.ocr.assert_not_called()

    def test_incomplete_walk_does_not_risk_ocr_over_unchecked_fields(self):
        self.backend.accessible_text.return_value = ("", 1200, False, False)
        self.assertIsNone(self.reader.read(lambda front: True))
        self.backend.ocr.assert_not_called()

    def test_focus_change_during_ocr_discards_the_text(self):
        other = FrontWindow("Mail", "Another window", 2, 456, (0, 0, 600, 500))
        self.backend.front.side_effect = [self.backend.window, self.backend.window, other]
        self.assertIsNone(self.reader.read(lambda front: True))

    def test_verified_foreground_frame_can_finish_ocr_after_switching_away(self):
        original = self.backend.window
        def recognise(_front):
            self.backend.ocr_snapshot_identity = original.identity
            self.backend.window = FrontWindow('Mail', 'Inbox', 2, 456, original.bounds)
            return '这条消息在切走之前已出现在微信前台画面中。'
        self.backend.ocr.side_effect = recognise
        reading = self.reader.read(lambda front: True)
        self.assertEqual(reading.application, 'WeChat')
        self.assertEqual(reading.title, 'Test chat')
        self.assertIn('切走之前', reading.text)
        self.assertEqual(self.backend.front.call_count, 2)

    def test_verified_frame_is_still_discarded_if_privacy_changes_during_ocr(self):
        self.backend.ocr_snapshot_identity = self.backend.window.identity
        self.assertIsNone(self.reader.read(Mock(side_effect=[True, True, False])))

    def test_privacy_changed_during_read_discards_the_text(self):
        self.assertIsNone(self.reader.read(Mock(side_effect=[True, True, False])))

    def test_permission_error_is_visible_and_does_not_read(self):
        self.backend.front.side_effect = RuntimeError("Allow Accessibility")
        self.assertIsNone(self.reader.read(lambda front: True))
        self.assertIn("Accessibility", self.reader.status()["last_error"])
        self.backend.ocr.assert_not_called()

    def test_ocr_permission_error_is_visible(self):
        self.backend.ocr.side_effect = RuntimeError("Allow Screen Recording")
        self.assertIsNone(self.reader.read(lambda front: True))
        self.assertIn("Screen Recording", self.reader.last_error)

    def test_failure_of_wechat_remains_visible_after_other_app_succeeds(self):
        self.backend.ocr.side_effect = RuntimeError('Local Tesseract OCR failed: exit 1')
        self.assertIsNone(self.reader.read(lambda front: True))
        self.backend.window = FrontWindow('Mail', 'Inbox', 3, 4, (0, 0, 600, 500))
        self.backend.accessible_text.return_value = ('A full accessible paragraph in a different application.', 3, False, True)
        self.reader.read(lambda front: True)
        status = {a['application']: a for a in self.reader.status()['applications']}
        self.assertEqual(status['WeChat']['status'], 'failed')
        self.assertEqual(status['WeChat']['consecutive_failures'], 1)
        self.assertIn('exit 1', status['WeChat']['last_error'])
        self.assertEqual(status['Mail']['status'], 'ok')
        self.assertNotIn('Inbox', str(status))
        self.assertNotIn('paragraph', str(status))
        self.backend.window = FrontWindow('WeChat', 'Chat', 2, 8, (0, 0, 600, 500))
        self.backend.ocr.side_effect = None
        self.reader.read(lambda front: True)
        recovered = {a['application']: a for a in self.reader.status()['applications']}['WeChat']
        self.assertEqual(recovered['status'], 'ok')
        self.assertEqual(recovered['last_error'], '')
        self.assertTrue(recovered['last_failure_at'])
        self.assertTrue(recovered['last_success_at'])

    def test_short_accessibility_text_does_not_hide_ocr_failure(self):
        self.backend.accessible_text.return_value = ('Toolbar', 3, False, True)
        self.backend.ocr.side_effect = RuntimeError('Apple Vision could not read the window')
        self.assertEqual(self.reader.read(lambda front: True).text, 'Toolbar')
        self.assertEqual(self.reader.status()['applications'][0]['status'], 'degraded')

    def test_focus_change_does_not_clear_an_unrecovered_application_failure(self):
        self.backend.ocr.side_effect = RuntimeError('Apple Vision could not read the window')
        self.reader.read(lambda front: True)
        window = self.backend.window
        other = FrontWindow('Mail', 'Inbox', 3, 4, window.bounds)
        self.backend.front.side_effect = [window, other]
        self.assertIsNone(self.reader.read(lambda front: True))
        self.assertEqual(self.reader.status()['applications'][0]['status'], 'failed')

    def test_fallback_method_is_attached_to_the_actual_reading(self):
        self.backend.ocr_method = 'macos_vision_ocr'
        self.assertEqual(self.reader.read(lambda front: True).how, 'macos_vision_ocr')
        self.assertEqual(self.reader.ocr_readings, 1)








class MacAccessibleTreeTests(unittest.TestCase):
    def test_depth_limit_is_incomplete_even_when_no_other_nodes_remain(self):
        native = NativeReader.__new__(NativeReader)
        tree = {"AXRole": "AXTextField", "AXSubrole": "AXSecureTextField"}
        for _ in range(32):
            tree = {"AXRole": "AXGroup", "AXChildren": [tree]}
        native.attr = lambda element, name: element.get(name)
        native.rect = lambda element: (0, 0, 100, 100)
        front = FrontWindow("Test", "Test", 1, 1, (0, 0, 100, 100), tree)
        self.assertFalse(native.accessible_text(front)[3])

    def test_hidden_offscreen_and_secure_content_are_not_read(self):
        native = NativeReader.__new__(NativeReader)
        secret = {"AXRole": "AXTextField", "AXSubrole": "AXSecureTextField",
                  "AXValue": "password"}
        visible = {"AXRole": "AXStaticText", "AXValue": "Visible message"}
        hidden = {"AXRole": "AXStaticText", "AXHidden": True, "AXValue": "Hidden chat"}
        outside = {"AXRole": "AXStaticText", "AXValue": "Other page", "outside": True}
        tree = {"AXRole": "AXWindow", "AXChildren": [secret, visible, hidden, outside]}
        asked = []
        def attr(element, name):
            asked.append((element, name))
            return element.get(name)
        native.attr = attr
        native.rect = lambda e: (900, 900, 10, 10) if e.get("outside") else (0, 0, 100, 100)
        front = FrontWindow("Test", "Test", 1, 1, (0, 0, 100, 100), tree)
        text, count, secure, complete = native.accessible_text(front)
        self.assertEqual(text, "Visible message")
        self.assertTrue(secure)
        self.assertTrue(complete)
        self.assertFalse(any(e is secret and key == "AXValue" for e, key in asked))


class MacScreenPipelineTests(unittest.TestCase):
    def setUp(self):
        self.agent = TemporaryAgent()
        self.config = self.agent.app.config
        self.config.set("listener_enabled", True)
        self.config.set("listener_screen_enabled", True)
        self.backend = Backend()
        self.reader = MacWindowTextReader(self.backend)
        self.platform = SimpleNamespace(name="macos", activity=SimpleNamespace(
            text_in_front_allowed=self.reader.read,
            seconds_since_input=lambda: 0,
            window_text_status=self.reader.status,
        ))
        self.provider = ScreenProvider(self.config, self.platform)
        self.events = []
        self.provider.bind(self.events.append)

    def tearDown(self):
        self.agent.cleanup()

    def test_ocr_reaches_raw_activity_and_the_heartbeat_once(self):
        self.provider._look()
        self.assertEqual(len(self.events), 1, 'WeChat is emitted without a buffer flush')
        self.assertFalse(self.provider.get_status()['gathering_now'])
        self.provider.flush_what_is_gathered()
        self.provider._look()
        self.provider.flush_what_is_gathered()
        self.assertEqual(len(self.events), 1)
        event = self.events[0]
        self.assertEqual(event.event_type, "screen.text")
        self.assertEqual(event.data["how"], "macos_vision_ocr")
        self.agent.app.raw_store.add_event(event)
        floor = self.agent.app.heartbeat._state_floor("test-reading")
        self.assertIn("明天下午三点", str(floor.to_dict()))

    def test_master_switch_prevents_native_reads(self):
        self.agent.app.config.set('listener_enabled', False)
        self.provider._look()
        self.backend.accessible_text.assert_not_called()
        self.backend.ocr.assert_not_called()
        self.assertEqual(self.events, [])

    def test_application_denylist_prevents_capture(self):
        self.config.set("listener_application_denylist", "WeChat")
        self.provider._look()
        self.backend.accessible_text.assert_not_called()
        self.backend.ocr.assert_not_called()
        self.assertEqual(self.events, [])

    def test_excluded_window_prevents_capture(self):
        self.config.set("listener_screen_excluded_applications", "Test chat")
        self.provider._look()
        self.backend.accessible_text.assert_not_called()
        self.backend.ocr.assert_not_called()

    def test_switching_chat_title_keeps_separate_events(self):
        self.provider._look()
        self.backend.window.title = "Different chat"
        self.backend.ocr.return_value = "Another participant discussing an unrelated appointment."
        self.provider._look()
        self.provider.flush_what_is_gathered()
        self.assertEqual([e.data["window_title"] for e in self.events],
                         ["Test chat", "Different chat"])



    # FAST SAMPLING (fast_reading.py): a chat in front is looked at every
    # 3 seconds - its picture, cheaply - and read only when the picture changed.
    def _at(self, now, picture=None):
        if picture is not None:
            self.picture = picture
        with patch('personalized_agent.listener.providers.screen.time.monotonic', return_value=now), \
                patch('personalized_agent.listener.providers.fast_reading._on_battery', return_value=False):
            return self.provider._tick()

    def _chat_in_front(self):
        from PIL import Image
        self.picture = Image.new('L', (90, 80), 0)
        self.platform.activity.peek_foreground_application = lambda: self.backend.window.application
        self.platform.activity.front_window_image = lambda: self.picture

    def _changed_picture(self, shade):
        from PIL import Image
        picture = Image.new('L', (90, 80), 0)
        for x in range(0, 90, 10):
            for y in range(80):
                picture.putpixel((x, y), shade)
        return picture

    def test_any_focus_change_wakes_the_worker_without_native_work_in_the_callback(self):
        for application in ('WeChat', 'Weixin', '微信', 'Mail'):
            self.provider._wake.clear()
            self.provider._focus_changed('', {'application': application})
            self.assertTrue(self.provider._wake.is_set())
        self.backend.front.assert_not_called()

    def test_a_chat_in_front_is_looked_at_every_3_seconds_and_read_once_while_unchanged(self):
        self._chat_in_front()
        self.assertEqual(self._at(100), 3.0)
        self.assertEqual(self.backend.ocr.call_count, 1)
        for now in (103, 106, 109):
            self.assertEqual(self._at(now), 3.0)
        self.assertEqual(self.backend.ocr.call_count, 1, 'the same picture is not read again')
        self.assertEqual(len(self.events), 1)

    def test_a_quiet_chat_backs_off_and_a_changed_picture_is_read_at_once(self):
        self._chat_in_front()
        self._at(100)
        self.assertEqual(self._at(111), 3.0, 'unchanged 10 s: still every 3 s')
        self.assertEqual(self._at(131), 5.0, 'unchanged 30 s: every 5 s')
        self.backend.ocr.return_value += '\n这是刚出现的新消息。'
        self.assertEqual(self._at(133, self._changed_picture(255)), 3.0)
        self.assertEqual(self.backend.ocr.call_count, 2)
        self.assertEqual(len(self.events), 2)

    def test_typing_keeps_every_3_seconds(self):
        self._chat_in_front()
        keyboard = self.platform.activity.seconds_since_keyboard_input = Mock(return_value=100)
        self._at(100)
        self.assertEqual(self._at(140), 5.0)
        keyboard.return_value = .2
        self.assertEqual(self._at(141), 3.0)
        self.assertEqual(self.backend.ocr.call_count, 2, 'typing reads, even before the picture shows it')

    def test_reading_stays_within_its_share_of_one_core(self):
        self._chat_in_front()
        self.config.set('listener_fast_cpu_percent', 10)
        self.provider._pace.took(95, 4.0)          # 4 s of reading in the last 30 s: over 3 s
        self.assertEqual(self._at(100), 5.0)
        self.assertEqual(self.backend.ocr.call_count, 0)
        self.assertEqual(self.provider._pace.held_by_budget, 1)
        self.assertEqual(self._at(126, self._changed_picture(255)), 3.0, 'within budget again')
        self.assertEqual(self.backend.ocr.call_count, 1)

    def test_on_battery_at_most_every_five_seconds(self):
        self._chat_in_front()
        with patch('personalized_agent.listener.providers.screen.time.monotonic', return_value=100), \
                patch('personalized_agent.listener.providers.fast_reading._on_battery', return_value=True):
            self.assertEqual(self.provider._tick(), 5.0)

    def test_other_windows_keep_the_ordinary_interval(self):
        self._chat_in_front()
        self.backend.window.application = 'Preview'
        self.backend.window.title = 'A synthetic report.pdf'
        self.backend.accessible_text.return_value = ('A sufficiently long accessible document from another application.', 3, False, True)
        self._at(100)
        self.assertEqual(self.provider._interval(), 15)
        for now in (101, 105, 110):
            self._at(now, self._changed_picture(now))
        self.assertEqual(self.backend.accessible_text.call_count, 1, 'a changed picture does not hurry it')
        self._at(115)
        self.assertEqual(self.backend.accessible_text.call_count, 2)

    def test_short_chat_visit_is_stored_before_leaving_without_waiting_for_application_poll(self):
        manager = self.agent.app.listener
        manager.register(self.provider)
        self._chat_in_front()
        self.backend.window.application = 'Preview'
        self.backend.window.title = 'A synthetic report.pdf'
        self.backend.accessible_text.return_value = ('A sufficiently long accessible document from another application.', 3, False, True)
        self._at(100)
        # A 6-second visit fits entirely between ordinary 15-second reads.
        self.backend.window.application, self.backend.window.title = 'WeChat', 'Test chat'
        self.backend.accessible_text.return_value = ('', 4, False, True)
        self._at(105)
        self.assertEqual(len(self.agent.app.db.query("SELECT id FROM raw_events WHERE application='WeChat'")), 1)
        self.backend.ocr.return_value += '\n这是刚刚回复的一条新消息。'
        self._at(106, self._changed_picture(255))
        stored = self.agent.app.db.query("SELECT data FROM raw_events WHERE application='WeChat' AND event_type='screen.text'")
        self.assertEqual(len(stored), 2)
        self.assertIn('刚刚回复', str(stored[-1]))
        self.assertFalse(self.provider.get_status()['gathering_now'])
        self.backend.window.application = 'Preview'
        self._at(111)
        self.assertEqual(self.provider._interval(), 15)

    def test_leaving_stops_chat_reads_and_reentering_reads_immediately(self):
        self._chat_in_front()
        self._at(100)
        calls = self.backend.ocr.call_count
        self.backend.window.application = 'Preview'
        self.backend.accessible_text.return_value = ('A sufficiently long accessible document from another application.', 3, False, True)
        self._at(101)
        self.assertEqual(self.backend.ocr.call_count, calls)
        self.backend.window.application = 'WeChat'
        self.backend.accessible_text.return_value = ('', 4, False, True)
        self.assertEqual(self._at(102), 3.0)
        self.assertEqual(self.backend.ocr.call_count, calls + 1)

    def test_read_failures_are_tried_again_not_taken_for_a_quiet_chat(self):
        self._chat_in_front()
        self.backend.ocr.side_effect = RuntimeError('Apple Vision could not read the window')
        self._at(100)
        self.backend.ocr.side_effect = None
        self.assertEqual(self._at(103), 3.0)
        self.assertEqual(self.backend.ocr.call_count, 2, 'the same picture, read again after a failure')
        self.assertEqual(len(self.events), 1)

    def test_status_says_how_it_is_paced(self):
        self._chat_in_front()
        self._at(100)
        self._at(101)
        fast = self.provider.get_status()['fast']
        self.assertEqual((fast['fastest_seconds'], fast['cpu_budget_percent'], fast['battery_floor_seconds']),
                         (3.0, 25, 5.0))
        self.assertEqual((fast['looks'], fast['looks_unchanged']), (2, 1))
        self.assertTrue(fast['in_front'])


class MacOCRImageCacheTests(unittest.TestCase):
    def test_identical_window_images_do_not_repeat_local_recognition(self):
        native = NativeReader.__new__(NativeReader)
        native.q = Mock()
        native._encode_png = Mock(return_value=b'current window png')
        native._ocr_engine = Mock()
        native._ocr_engine.read.return_value = 'Visible document paragraph'
        native._ocr_engine.method = 'macos_vision_ocr'
        native.q.CGPreflightScreenCaptureAccess.return_value = True
        native.q.CGDataProviderCopyData.return_value = b'unchanged image pixels'
        window = FrontWindow('Google Chrome', 'Example document', 1, 2, (0, 0, 600, 500))
        native.front = Mock(return_value=window)
        self.assertEqual(native.ocr(window), 'Visible document paragraph')
        self.assertEqual(native.ocr(window), 'Visible document paragraph')
        native._ocr_engine.read.assert_called_once_with(b'current window png')
        self.assertEqual(native.ocr_method, 'macos_vision_ocr')
        self.assertEqual(native.ocr_image_cache_hits, 1)
        native.q.CGDataProviderCopyData.return_value = b'changed pixels'
        native.ocr(window)
        self.assertEqual(native._ocr_engine.read.call_count, 2)
        for pixels in (b'unchanged image pixels', b'changed pixels') * 3:
            native.q.CGDataProviderCopyData.return_value = pixels
            native.ocr(window)
        self.assertEqual(native._ocr_engine.read.call_count, 2, 'Caret blink frames reuse exact-image OCR')
        self.assertEqual(native.ocr_executions, 2)

    def test_focus_change_before_or_during_capture_never_reads_a_background_window(self):
        from personalized_agent.platform_layer.macos_window_text import CaptureFocusChanged
        native = NativeReader.__new__(NativeReader)
        native.q = Mock()
        native._ocr_engine = Mock()
        window = FrontWindow('WeChat', 'Chat', 1, 2, (0, 0, 600, 500))
        other = FrontWindow('Mail', 'Inbox', 3, 4, window.bounds)
        for sequence in ([other], [window, other]):
            native.q.reset_mock()
            native.front = Mock(side_effect=sequence)
            with self.assertRaises(CaptureFocusChanged):
                native.ocr(window)
            native._ocr_engine.read.assert_not_called()
            self.assertIsNone(native.ocr_snapshot_identity)
        self.assertEqual(native.q.CGWindowListCreateImage.call_count, 1)


class MacCapabilityDiscoveryTests(unittest.TestCase):
    def test_denied_browser_directories_do_not_break_platform_startup(self):
        from pathlib import Path
        from personalized_agent.platform_layer.macos import MacActivityProvider
        with patch.object(Path, 'exists', return_value=True), \
             patch.object(Path, 'iterdir', side_effect=PermissionError('denied')):
            paths = MacActivityProvider().get_browser_history_paths()
        self.assertNotIn('Firefox', paths)
        with patch.object(Path, 'exists', side_effect=PermissionError('denied')):
            self.assertEqual(MacActivityProvider().get_browser_history_paths(), {})
