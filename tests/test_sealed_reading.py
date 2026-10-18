"""A run whose folders are kept apart - each way and each try of the Work
debugger - is never given to a model that reads by itself where nothing
keeps it out. Codex runs its own commands in a sandbox that stops it writing,
not reading; until it runs inside the run's own isolation, such a call goes
to the next model on the list, or is refused saying why."""

from __future__ import annotations

import unittest

from support import TemporaryAgent

from personalized_agent.models.base import ModelRequest
from personalized_agent.models.manager import READS_UNCONFINED, ModelUnavailable
from test_model_chain import _Scripted


class PassedOverTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.models = self.agent.app.models
        self.codex = _Scripted("codex", kind="client")
        self.claude = _Scripted("claude_code", kind="client")
        for one in (self.codex, self.claude):
            self.models.register(one)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _ask(self, sealed):
        return self.models.invoke(ModelRequest(messages=[{"role": "user", "content": "hi"}],
                                               sealed_reading=sealed))

    def test_codex_is_passed_over_for_the_next(self) -> None:
        self.assertIn("codex", READS_UNCONFINED)
        self.models.set_chain([{"provider": "codex", "model": "m1"},
                               {"provider": "claude_code", "model": "m1"}])
        self.assertEqual(self._ask(True).text, "from claude_code")
        self.assertEqual(self.codex.calls, 0)
        # Not sealed: the list as it is.
        self.assertEqual(self._ask(False).text, "from codex")

    def test_codex_alone_is_refused_saying_why(self) -> None:
        self.models.set_chain([{"provider": "codex", "model": "m1"}])
        with self.assertRaises(ModelUnavailable) as refused:
            self._ask(True)
        self.assertTrue(refused.exception.availability.requires_user_action())
        self.assertIn("kept apart", str(refused.exception))
        self.assertEqual(self.codex.calls, 0)

    def test_a_debugger_runs_calls_are_sealed(self) -> None:
        source = (__import__("pathlib").Path(__file__).resolve().parents[1]
                  / "personalized_agent" / "agent" / "work" / "runner.py").read_text(
                      encoding="utf-8")
        self.assertIn('if isinstance(self.unit.get("debug"), dict) and not '
                      'request.sealed_reading:', source)


if __name__ == "__main__":
    unittest.main()
