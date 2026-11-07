"""THE AVATAR TALKS WHILE A REPLY COMES OUT (the user, 2026-11-06), the quick one or the full one,
and stops when it ends."""

from __future__ import annotations

import json
import queue
import unittest
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.agent.core import agent_core
from personalized_agent.ui.desktop import DesktopAgent


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.spoken = []
        self.app.bus.subscribe("agent.speaking", lambda _t, said: self.spoken.append(said["seconds"]))


class TheAgentSaysSoTests(_Base):
    def test_as_a_reply_is_written(self) -> None:
        told = []
        self.app.agent._reply_so_far("t1", "Hello", told.append)
        self.assertEqual(told, ["Hello"])
        self.assertEqual(self.spoken, [agent_core.STREAM_SECONDS])

    def test_not_at_every_word(self) -> None:
        for text in ("H", "He", "Hel", "Hell"):
            self.app.agent._reply_so_far("t1", text, lambda _text: None)
        self.assertEqual(len(self.spoken), 1)

    def test_work_nobody_is_reading_says_nothing(self) -> None:
        self.app.agent._reply_so_far("t1", "Hello", None)
        self.assertEqual(self.spoken, [])

    def test_a_tool_call_written_out_is_no_reply(self) -> None:
        self.app.agent._reply_so_far("t1", '{"tool": "x"}', lambda _text: None)
        self.assertEqual(self.spoken, [])

    def test_a_reply_all_at_once_for_about_as_long_as_it_takes(self) -> None:
        self.app.agent.speaks_whole("Yes.")
        self.app.agent.speaks_whole("word " * 100)
        self.assertEqual(self.spoken, [agent_core.MOST_SPOKEN_SECONDS / 4,
                                       agent_core.MOST_SPOKEN_SECONDS])

    def test_nothing_said_nothing_shown(self) -> None:
        self.app.agent.speaks_whole("  ")
        self.assertEqual(self.spoken, [])

    def test_the_quick_reply(self) -> None:
        answer = SimpleNamespace(text=json.dumps({"reply": "Noted.", "full_agent": "stop"}))
        self.app.two_speed.models = SimpleNamespace(invoke=lambda *_a, **_k: answer)
        self.app.two_speed.send("Hello")
        self.assertEqual(len(self.spoken), 1)

    def test_the_full_reply_written_at_once(self) -> None:
        self.app.agent.handle_message("hello")
        self.assertEqual(len(self.spoken), 1)

    def test_the_full_reply_as_it_came_is_not_said_twice(self) -> None:
        real = self.app.agent.run_instruction

        def written(asked, context, debug=False):
            self.app.agent._reply_so_far("t1", "Hello", context["on_text"])
            return real(asked, context, debug=debug)
        with mock.patch.object(self.app.agent, "run_instruction", written):
            self.app.agent.handle_message("hello", on_text=lambda _text: None)
        self.assertEqual(self.spoken, [agent_core.STREAM_SECONDS])


class TheMouthTests(unittest.TestCase):
    def _desk(self):
        planned, cancelled = [], []

        def after(ms, then):
            planned.append((ms, then))
            return len(planned)
        desk = SimpleNamespace(state="idle", queue=queue.Queue(), _spoken=None,
                               root=SimpleNamespace(after=after, after_cancel=cancelled.append))
        desk.set_state = lambda state: setattr(desk, "state", state)
        desk._spoken = lambda: DesktopAgent._spoken(desk)
        return desk, planned, cancelled

    def test_it_talks_and_stops_when_no_more_comes(self) -> None:
        desk, planned, cancelled = self._desk()
        DesktopAgent._speak_for(desk, 1.5)
        self.assertEqual(desk.state, "talking")
        DesktopAgent._speak_for(desk, 1.5)
        self.assertEqual(cancelled, [1], "more came: it goes on")
        self.assertEqual(planned[-1][0], 1500)
        planned[-1][1]()
        self.assertEqual(desk.state, "idle")

    def test_it_leaves_another_state_alone(self) -> None:
        desk, planned, _cancelled = self._desk()
        DesktopAgent._speak_for(desk, 1.0)
        desk.state = "notification"
        planned[-1][1]()
        self.assertEqual(desk.state, "notification")

    def test_told_through_the_queue(self) -> None:
        desk, _planned, _cancelled = self._desk()
        DesktopAgent._on_speaking(desk, "agent.speaking", {"seconds": 2.0})
        self.assertEqual(desk.queue.get_nowait(), ("speaking", 2.0))


if __name__ == "__main__":
    unittest.main()
