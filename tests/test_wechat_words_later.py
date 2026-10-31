"""WeChat's picture now, its words later (the user, 2026-10-30;
listener/providers/deferred_ocr.py): the picture is taken when WeChat is
read and its words recognised on a thread of their own; the reading keeps
its place - its event of the moment the picture was taken, the summaries
held at the oldest picture waiting - and at most MOST_WAITING wait, the
oldest let go and said to be so."""

from __future__ import annotations

import threading
import time
import unittest
from datetime import timedelta

from support import TemporaryAgent

from personalized_agent.common.events.models import parse_iso, utcnow
from personalized_agent.listener.providers import deferred_ocr
from personalized_agent.platform_layer.window_text import WindowText


def _waiting(minutes_ago, gate=None, words="消息"):
    taken = (utcnow() - timedelta(minutes=minutes_ago)).isoformat()

    def finish():
        if gate is not None:
            gate.wait(10)
        return WindowText(application="WeChat", title="微信", text=words, how="linux_rapidocr",
                          pid=7, handle=7, captured_at=taken,
                          wechat_capture={"peer_name": "小陈", "messages": [],
                                          "layout_status": "recognized",
                                          "unresolved_text": words})
    return WindowText(application="WeChat", title="微信", text="", how="linux_rapidocr_waiting",
                      pid=7, handle=7, finish=finish, captured_at=taken)


def _until(test, check, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return
        time.sleep(0.02)
    test.fail("not in time")


class TheWaitingRoomTests(unittest.TestCase):

    def test_one_after_another_and_the_oldest_let_go_past_the_most(self) -> None:
        gate = threading.Event()
        told = []
        room = deferred_ocr.DeferredOcr(lambda done, waiting, dropped:
                                        told.append((done, waiting, dropped)), most=3)
        self.addCleanup(room.stop)
        first = _waiting(10, gate)
        room.put(first)                       # taken up at once, and held there
        _until(self, lambda: room.status()["waiting"] == 0)
        later = [_waiting(9 - index, gate) for index in range(4)]
        for one in later:
            room.put(one)
        # Three may wait: the oldest of the four was let go, and said so.
        self.assertEqual(room.status()["waiting"], 3)
        self.assertEqual(room.status()["let_go"], 1)
        self.assertEqual(told, [(None, later[0], True)])
        # The summaries are held at the oldest still waiting - the first,
        # being read now.
        self.assertEqual(deferred_ocr.oldest_waiting(), parse_iso(first.captured_at))
        gate.set()
        _until(self, lambda: len(told) == 5)
        self.assertEqual([one[1] for one in told[1:]], [first] + later[1:])
        self.assertTrue(all(one[0].text == "消息" for one in told[1:]))
        self.assertIsNone(deferred_ocr.oldest_waiting())

    def test_what_still_waits_goes_with_the_agent(self) -> None:
        gate = threading.Event()
        room = deferred_ocr.DeferredOcr(lambda *_: None, most=5)
        room.put(_waiting(5, gate))
        _until(self, lambda: room.status()["waiting"] == 0)
        room.put(_waiting(4, gate))
        room.stop()
        gate.set()
        _until(self, lambda: deferred_ocr.oldest_waiting() is None)
        self.assertEqual(room.status()["waiting"], 0)


class _WeChatInFront:
    def __init__(self, reading):
        self.reading = reading
        outer = self

        class activity:
            @staticmethod
            def seconds_since_input():
                return 1.0

            @staticmethod
            def text_in_front_allowed(_allowed):
                return outer.reading

        self.activity = activity
        self.name = "test"


class TheScreenReaderTakesItInLaterTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.config = self.agent.app.config
        self.config.set("listener_enabled", True)
        self.config.set("listener_screen_enabled", True)
        self.seen = []

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_reading_returns_at_once_and_its_words_come_of_their_moment(self) -> None:
        from personalized_agent.listener.providers.screen import ScreenProvider
        gate = threading.Event()
        waiting = _waiting(2, gate, words="明天下午三点交稿")
        provider = ScreenProvider(self.config, _WeChatInFront(waiting))
        provider.bind(self.seen.append)
        self.addCleanup(provider.stop)
        began = time.monotonic()
        provider._look()
        self.assertLess(time.monotonic() - began, 1.0, "the words were not waited for")
        self.assertEqual(self.seen, [])
        self.assertEqual(provider.get_status()["wechat_words_later"]["waiting"], 0)
        gate.set()
        _until(self, lambda: self.seen)
        [event] = self.seen
        self.assertEqual(event.data["text"], "明天下午三点交稿")
        self.assertEqual(event.data["wechat_capture"]["peer_name"], "小陈")
        self.assertEqual(event.timestamp, parse_iso(waiting.captured_at),
                         "of the moment the picture was taken")

    def test_a_picture_let_go_is_said_to_be_unread(self) -> None:
        from personalized_agent.listener.providers.screen import ScreenProvider
        provider = ScreenProvider(self.config, _WeChatInFront(None))
        provider.bind(self.seen.append)
        let_go = _waiting(1)
        provider._recognised(None, let_go, True)
        [event] = self.seen
        self.assertEqual(event.data["wechat_capture"]["layout_reason"], "ocr_queue_full")
        self.assertEqual(event.data["text"], "")
        self.assertEqual(event.timestamp, parse_iso(let_go.captured_at))


if __name__ == "__main__":
    unittest.main()
