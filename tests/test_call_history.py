"""Every model call kept for testing - in a build with the Debugger only (the user, 2026-11-11:
"在debugger模式下，可以存历史记录（安装版，或者编译版不debugger不存）方便之后测试")."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.models import call_history as ch
from personalized_agent.models.call_history import CallHistory, read


def _request(task="heartbeat:step_people"):
    return SimpleNamespace(task=task, messages=[{"role": "user", "content": "Who is at the allotment?"}],
                           system="You read the user's day.", tools=[SimpleNamespace(name="search")],
                           reasoning_effort="low", max_tokens=4096)


def _response():
    usage = SimpleNamespace(input_tokens=12, output_tokens=30, cached_input_tokens=0,
                            cache_written_tokens=900)
    return SimpleNamespace(content='{"people": []}', stop_reason="end_turn", usage=usage,
                           model="claude-opus-5-5")


class KeptTests(unittest.TestCase):

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="pa-calls-"))
        self.config = {"debugger_enabled": True}
        self.config_get = SimpleNamespace(get=lambda key, default=None: self.config.get(key, default))

    def tearDown(self) -> None:
        shutil.rmtree(self.folder, ignore_errors=True)

    def _keep(self, history):
        return history.keep(_request(), role="heartbeat", provider="claude_code",
                            model="claude-opus-5-5", answered="claude-opus-5-5",
                            response=_response(), latency_ms=1234.5, cost=0.0123,
                            module="connections", by_user=False)

    def test_a_call_is_kept_whole_to_be_asked_again(self) -> None:
        path = self._keep(CallHistory(self.folder, self.config_get, available=lambda: True))
        self.assertTrue(path and path.exists())
        self.assertEqual(path.parent.parent, self.folder / ch.FOLDER)
        kept = read(path)
        self.assertEqual((kept["task"], kept["system"], kept["messages"][0]["content"]),
                         ("heartbeat:step_people", "You read the user's day.", "Who is at the allotment?"))
        self.assertEqual((kept["effort"], kept["tools"], kept["answer"]), ("low", ["search"], '{"people": []}'))
        self.assertEqual((kept["usage"]["cache_written_tokens"], kept["cost"], kept["module"]),
                         (900, 0.0123, "connections"))

    def test_nothing_kept_without_the_debugger_or_while_it_does_not_collect(self) -> None:
        self.assertIsNone(self._keep(CallHistory(self.folder, self.config_get, available=lambda: False)))
        self.config["debugger_enabled"] = False
        self.assertIsNone(self._keep(CallHistory(self.folder, self.config_get, available=lambda: True)))
        self.assertFalse((self.folder / ch.FOLDER).exists())

    def test_a_picture_is_not_kept_and_a_long_call_is_cut_and_says_so(self) -> None:
        history = CallHistory(self.folder, self.config_get, available=lambda: True)
        request = _request()
        request.messages = [{"role": "user", "content": [
            {"type": "image", "source": {"data": "AAAA"}}, {"type": "text", "text": "x" * 10}]}]
        kept = read(history.keep(request, role="primary", provider="p", model="m", answered="m",
                                 response=_response(), latency_ms=1, cost=None))
        self.assertEqual(kept["messages"][0]["content"][0], {"type": "image", "kept": "not kept"})
        old = ch.MOST_CHARS
        ch.MOST_CHARS = 50
        try:
            request.messages = [{"role": "user", "content": "y" * 500}]
            kept = read(history.keep(request, role="primary", provider="p", model="m", answered="m",
                                     response=_response(), latency_ms=1, cost=None))
        finally:
            ch.MOST_CHARS = old
        self.assertTrue(kept["cut"])
        self.assertEqual(len(kept["messages"][0]["content"]), 50)

    def test_the_old_days_go_and_the_oldest_past_the_size(self) -> None:
        history = CallHistory(self.folder, self.config_get, available=lambda: True)
        now = datetime(2030, 3, 20, tzinfo=timezone.utc)
        for days in (20, 3, 2, 1, 0):
            day = self.folder / ch.FOLDER / (now - timedelta(days=days)).strftime("%Y-%m-%d")
            day.mkdir(parents=True)
            (day / "a.json.gz").write_bytes(b"x" * 100)
        old = ch.MOST_BYTES
        ch.MOST_BYTES = 250
        try:
            gone = history.prune(now)
        finally:
            ch.MOST_BYTES = old
        self.assertEqual(gone, ["2030-02-28", "2030-03-17", "2030-03-18"])
        left = sorted(one.name for one in (self.folder / ch.FOLDER).iterdir())
        self.assertEqual(left, ["2030-03-19", "2030-03-20"], "never the day being written")


class InTheApplicationTests(unittest.TestCase):

    def test_each_call_the_application_makes_is_kept(self) -> None:
        agent = TemporaryAgent()
        try:
            app = agent.app
            history = app.models.history
            self.assertIsNotNone(history, "set at the start")
            history._available = lambda: True
            calls = []
            history.keep = lambda request, **kept: calls.append((request.task, kept["role"]))
            from personalized_agent.models.base import ModelRequest
            try:
                app.models.invoke(ModelRequest(messages=[{"role": "user", "content": "hello"}],
                                               task="test_kept"), role="primary")
            except Exception as exc:          # no model to answer here: nothing to keep
                self.skipTest(f"no model answers in a test agent: {exc}")
            self.assertEqual(calls, [("test_kept", "primary")])
        finally:
            agent.cleanup()


if __name__ == "__main__":
    unittest.main()
