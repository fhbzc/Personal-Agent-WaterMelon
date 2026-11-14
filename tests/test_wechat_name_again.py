"""WeChat's chat name is 0x0 for a moment as a chat changes: read again, twice, half a second
apart; only of the window in front; and what was read is never lost. Everything here is made up."""

from __future__ import annotations

import ctypes
import unittest
from unittest import mock

import support  # noqa: F401

from personalized_agent.platform_layer import window_text as wt

NO_NAME = ("words", {"layout_status": "unrecognized", "layout_reason": "chat_header_not_readable",
                     "unresolved_text": "words"}, 1)
NAMED = ("words", {"layout_status": "recognized", "layout_reason": "", "peer_name": "Robin"}, 1)
HANDLE = 7


class ReadAgainTests(unittest.TestCase):

    def _read(self, frames, fronts=None):
        reader = object.__new__(wt.WindowTextReader)
        reader.last_error = ""
        reader._wechat_frame = mock.Mock(side_effect=frames)
        windll = mock.Mock()
        windll.user32.SetThreadDpiAwarenessContext.return_value = 0
        windll.user32.GetForegroundWindow.side_effect = fronts or (lambda: HANDLE)
        with mock.patch.object(wt.WindowTextReader, "available", new_callable=mock.PropertyMock,
                               return_value=True), \
                mock.patch.object(ctypes, "windll", windll, create=True), \
                mock.patch.object(wt.time, "sleep") as slept:
            said = reader.wechat_frame(HANDLE)
        return said, reader._wechat_frame.call_count, [one.args[0] for one in slept.call_args_list]

    def test_read_at_once_there_is_no_wait(self) -> None:
        self.assertEqual(self._read([NAMED]), (NAMED, 1, []))

    def test_half_a_second_later(self) -> None:
        self.assertEqual(self._read([NO_NAME, NAMED]), (NAMED, 2, [0.5]))

    def test_and_half_a_second_after_that(self) -> None:
        self.assertEqual(self._read([NO_NAME, NO_NAME, NAMED]), (NAMED, 3, [0.5, 0.5]))

    def test_then_it_stops_with_what_it_has(self) -> None:
        self.assertEqual(self._read([NO_NAME, NO_NAME, NO_NAME, NAMED]), (NO_NAME, 3, [0.5, 0.5]))

    def test_not_of_a_window_no_longer_in_front(self) -> None:
        said, reads, _slept = self._read([NO_NAME, NAMED], fronts=lambda: 99)
        self.assertEqual((said, reads), (NO_NAME, 1), "the picture would be of something else")

    def test_a_page_that_is_no_chat_any_more_keeps_what_was_read(self) -> None:
        self.assertEqual(self._read([NO_NAME, None])[0], NO_NAME)

    def test_only_for_the_name(self) -> None:
        other = ("", {"layout_status": "unrecognized", "layout_reason": "message_list_not_found"}, 0)
        self.assertEqual(self._read([other]), (other, 1, []))


class StillNoNameTests(unittest.TestCase):
    """Read as far as it goes: the words reach what the agent reads, said to be nobody's yet."""

    def test_the_words_are_kept_and_given_to_nobody(self) -> None:
        import tempfile
        from datetime import datetime, timezone
        from pathlib import Path

        from PIL import Image

        from personalized_agent.common.events.models import ListenerEvent
        from personalized_agent.data_manage.storage.database import Database
        from personalized_agent.data_manage.storage.raw_store import RawStore
        from personalized_agent.platform_layer.wechat_items import parse_wechat_items

        capture = parse_wechat_items(
            Image.new("RGB", (900, 700), (237, 237, 237)), (300, 80, 560, 500),
            [{"text": "The answer came", "box": (300, 200, 560, 40)},
             {"text": "Thank you", "box": (300, 120, 560, 40)}], [])
        capture.update(scope_id="visit", captured_at=datetime.now(timezone.utc).isoformat())
        with tempfile.TemporaryDirectory() as work:
            db = Database(Path(work) / "db.sqlite")
            try:
                raw = RawStore(db)
                event = ListenerEvent(event_type="screen.text", source="screen", application="WeChat",
                                      data={"text": "raw input", "wechat_capture": capture})
                raw.add_event(event)
                self.assertEqual(raw.count_events(), 1, "it reaches the record")
                said = event.data["text"].splitlines()
                self.assertEqual(said[0], "[界面未完成结构识别，"
                                          "不作本人发言证据]")
                self.assertEqual(said[1:], ["Thank you", "The answer came"], "top to bottom")
                self.assertEqual(event.data["wechat_messages"], [], "as nobody's message")
                self.assertEqual(raw.wechat.list_conversations()["conversations"], [],
                                 "and in no one's conversation")
            finally:
                db.close()


class _Rect:
    def __init__(self, left, top, width, height):
        self.left, self.top, self.right, self.bottom = left, top, left + width, top + height


class _Label:
    def __init__(self, ident, name, rect):
        self.CachedAutomationId = self.CurrentAutomationId = ident
        self.CachedName = self.CurrentName = name
        self.CachedBoundingRectangle = self.CurrentBoundingRectangle = rect


class _Found:
    def __init__(self, labels):
        self.labels, self.Length = labels, len(labels)

    def GetElement(self, index):
        return self.labels[index]


class NameAmongAllTextsTests(unittest.TestCase):
    """2026-11-14, read from the user's own WeChat: a panel open right of the chat put 39 texts ahead
    of the chat's name - the 41st - and the first 40 were all that was looked at."""

    def _window(self, cached=True):
        side = [_Label("side_panel.text", f"line {n}", _Rect(1376, 182 + 50 * n, 479, 37)) for n in range(39)]
        labels = side + [_Label("chat.row", "Robin", _Rect(300, 50, 868, 26)),
                         _Label("content_view.left_ui_.current_chat_name_label", "Robin", _Rect(300, 50, 76, 26)),
                         _Label("chat.status", "", _Rect(378, 50, 35, 26))]
        window = mock.Mock()
        if cached:
            window.FindAllBuildCache.return_value = _Found(labels)
        else:
            window.FindAllBuildCache.side_effect = OSError("no cache here")
        window.FindAll.return_value = _Found(labels)
        reader = object.__new__(wt.WindowTextReader)
        reader._automation = mock.Mock()
        return reader, window

    def test_the_41st_text_is_found(self) -> None:
        reader, window = self._window()
        self.assertEqual(reader._wechat_headers(window, (0, 0)), [{"text": "Robin", "box": (300, 50, 76, 26)}])
        window.FindAll.assert_not_called()

    def test_asked_one_by_one_when_the_cache_cannot_be_had(self) -> None:
        reader, window = self._window(cached=False)
        self.assertEqual(reader._wechat_headers(window, (100, 10)), [{"text": "Robin", "box": (200, 40, 76, 26)}])


if __name__ == "__main__":
    unittest.main()
