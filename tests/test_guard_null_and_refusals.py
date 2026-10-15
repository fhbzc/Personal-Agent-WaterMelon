"""Three small things about what the in-process guard refuses: the null
device is not a file outside any boundary (pytest opens it as it starts);
the warning a refusal leaves says what was refused, in the guard's words;
and the work agents are told before they try that their code opens no
connection, not even to a server of their own on localhost."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "personalized_agent" / "runtime"))

import guard  # noqa: E402  - the guard is loaded on its own, as children load it

from personalized_agent.agent.work import runner  # noqa: E402
from personalized_agent.runtime.sandbox import guard_refusals  # noqa: E402


class TheNullDeviceTests(unittest.TestCase):

    def test_every_spelling_is_the_null_device(self) -> None:
        for name in (os.devnull, "nul", "NUL", "nul:", "\\\\.\\nul", "//./nul",
                     "/dev/null", os.devnull.encode()):
            with self.subTest(name=name):
                self.assertTrue(guard._is_null_device(name))

    def test_a_file_named_like_it_is_not(self) -> None:
        for name in ("C:/work/nul.txt", "nullable.py", "/dev/nullx", 42):
            with self.subTest(name=name):
                self.assertFalse(guard._is_null_device(name))

    def test_neither_check_refuses_it(self) -> None:
        # With a workspace somewhere else entirely, the device is still open.
        saved = guard._workspace
        try:
            guard._workspace = str(ROOT / "nowhere")
            guard._check_write(os.devnull, "open")
            guard._check_read(os.devnull, "open")
            with self.assertRaises(guard.SandboxViolation):
                guard._check_write(str(ROOT / "elsewhere.txt"), "open")
        finally:
            guard._workspace = saved


class TheWarningSaysWhatWasRefusedTests(unittest.TestCase):

    def test_each_refusal_once_in_the_guards_words(self) -> None:
        stderr = ("Traceback (most recent call last):\n"
                  "guard.SandboxViolation: network: code run for a piece of work may "
                  "not open connections\n"
                  "INTERNALERROR> guard.SandboxViolation: open: outside the agent's "
                  "own directory 'C:\\\\w'\n"
                  "guard.SandboxViolation: network: code run for a piece of work may "
                  "not open connections\n")
        self.assertEqual(guard_refusals(stderr), [
            "network: code run for a piece of work may not open connections",
            "open: outside the agent's own directory 'C:\\\\w'"])
        self.assertEqual(guard_refusals("all well"), [])


class TheAgentsAreToldTests(unittest.TestCase):

    def test_what_code_may_reach_and_how_to_test_instead(self) -> None:
        # Each is given the environment, which says what code may reach.
        for name in ("AGENT_SYSTEM", "ALONE_SYSTEM"):
            with self.subTest(prompt=name):
                self.assertIn("{environment}", getattr(runner, name))
        self.assertIn("{network}", runner.ENVIRONMENT_RULES)
        self.assertIn("not even to a server it starts itself on", runner.NETWORK_NONE_AT_ALL)
        for rules in (runner.NETWORK_NONE_AT_ALL, runner.NETWORK_OWN_LOOPBACK):
            self.assertIn("test client", rules)


if __name__ == "__main__":
    unittest.main()
