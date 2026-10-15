"""What a run spends, call by call, and how the user hears it has ended:
input counted whole - the cached input too - and limited apart from the
output; every model call kept, for the chart of them over time, and given
to the page as they come; and, when the user asked for it, ONE sound when
the run reaches its end - never under the tests."""

from __future__ import annotations

import os
import sys
import time
import types
import unittest
from unittest import mock

from support import TemporaryAgent
from test_work_runs import _TwoAgents


class _Cached(_TwoAgents):
    """The two agents, on a provider with a prompt cache: each call reads
    three tokens plainly, forty thousand from the cache and a thousand into
    it, and writes five hundred."""

    @staticmethod
    def _reply(text="", calls=None):
        from personalized_agent.models.base import ModelUsage
        reply = _TwoAgents._reply(text, calls)
        reply.usage = ModelUsage(input_tokens=3, output_tokens=500,
                                 cached_input_tokens=40_000, cache_written_tokens=1_000)
        return reply


class _Failing(_TwoAgents):
    """Fails the first call it is asked - a provider that is down."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.failed = False

    def invoke(self, request, role=""):
        if not self.failed:
            self.failed = True
            raise RuntimeError("the provider is down")
        return super().invoke(request, role)


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.unit = {"id": "task-calls1", "name": "Write notes", "kind": "task", "why": ""}
        self.played = []
        self.app.work.play_sound = lambda outcome: self.played.append(outcome)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _wait(self):
        for _ in range(600):
            runs = self.app.work.runs(self.unit["id"])
            if runs and not runs[0]["alive"] and runs[0]["status"] != "running":
                break
            time.sleep(0.1)
        return self.app.work.runs(self.unit["id"])[0]

    def _run(self, models, **kwargs):
        self.app.work.models = models
        started = self.app.work.start(self.unit, "write the notes", **kwargs)
        self.assertNotIn("error", started, started)
        return self._wait(), started


class InputIsCountedWholeTests(_Base):

    def test_count_use_counts_the_cached_input(self) -> None:
        from personalized_agent.heartbeat.experience.store import count_use
        from personalized_agent.models.base import ModelUsage
        used = {"calls": 0, "tokens_in": 0, "tokens_out": 0}
        reply = types.SimpleNamespace(usage=ModelUsage(
            input_tokens=4, output_tokens=7, cached_input_tokens=30_000,
            cache_written_tokens=500))
        count_use(used, reply)
        count_use(used, reply)
        self.assertEqual(used, {"calls": 2, "tokens_in": 61_008, "tokens_out": 14,
                                "tokens_in_cached": 60_000,
                                "tokens_in_cache_written": 1_000})
        # A provider with no cache: the three numbers it always had.
        plain = {"calls": 0, "tokens_in": 0, "tokens_out": 0}
        count_use(plain, types.SimpleNamespace(usage=ModelUsage(input_tokens=9,
                                                                output_tokens=2)))
        self.assertEqual(plain, {"calls": 1, "tokens_in": 9, "tokens_out": 2})
        # No usage at all: a call, of nothing.
        count_use(plain, types.SimpleNamespace(usage=None))
        self.assertEqual(plain, {"calls": 2, "tokens_in": 9, "tokens_out": 2})

    def test_a_run_counts_and_records_each_call_whole(self) -> None:
        run, started = self._run(_Cached())
        self.assertEqual(run["status"], "done", run["log"][-6:])
        calls = run["calls"]
        total = run["plan"]["total"]
        self.assertEqual(len(calls), total["calls"])
        self.assertGreater(len(calls), 4)
        # Input whole: 3 + 40,000 + 1,000 a call - and the run's total too.
        self.assertEqual({one["tokens_in"] for one in calls}, {41_003})
        self.assertEqual({one["tokens_out"] for one in calls}, {500})
        self.assertEqual(total["tokens_in"], 41_003 * len(calls))
        self.assertEqual(calls[0]["tokens_in_cached"], 40_000)
        self.assertEqual(calls[0]["tokens_in_cache_written"], 1_000)
        # Who made each, in which part of the run, when, numbered in order.
        self.assertEqual([one["n"] for one in calls], list(range(1, len(calls) + 1)))
        self.assertEqual({one["who"] for one in calls}, {"builder", "reviewer"})
        self.assertTrue(all(one["part"] for one in calls))
        self.assertTrue(all(one["started"] <= one["finished"] for one in calls))
        self.assertTrue(all(one["seconds"] >= 0 for one in calls))
        self.assertFalse(any(one.get("failed") for one in calls))
        # Given once, beside the plan - not again inside it.
        self.assertNotIn("calls", run["plan"])
        # The transcript says the input whole, and how it got it.
        from pathlib import Path
        logs = sorted((Path(started["folder"]) / "run_logs").glob(f"*_{run['id']}"))[0]
        text = (logs / "transcript-001.txt").read_text(encoding="utf-8")
        self.assertIn("input 41,003 tokens (40,000 read from the cache, 1,000 written "
                      "to it) · output 500 tokens", text)

    def test_the_live_session_gives_the_calls_after_the_last_seen(self) -> None:
        run, _started = self._run(_Cached())
        every = self.app.work.live(run["id"], 0)["calls"]
        self.assertEqual([one["n"] for one in every], [one["n"] for one in run["calls"]])
        after = self.app.work.live(run["id"], 0, calls=3)["calls"]
        self.assertEqual([one["n"] for one in after], [one["n"] for one in every][3:])
        self.assertEqual(self.app.work.live(run["id"], 0, calls=10 ** 6)["calls"], [])
        # And through the route, as the page asks.
        from personalized_agent.api.routes import build_routes
        from test_experience_debugger import _Request
        routes = build_routes(self.app)
        said = routes[("GET", "/api/work/live")](_Request({"run": run["id"], "calls": "2"}))
        self.assertEqual(said["calls"][0]["n"], 3)

    def test_a_failed_call_is_kept_as_failed(self) -> None:
        run, _started = self._run(_Failing())
        calls = run["calls"]
        self.assertTrue(calls[0]["failed"], calls[0])
        self.assertIn("the provider is down", calls[0]["error"])
        self.assertEqual(calls[0]["tokens_in"], 0)

    def test_only_the_latest_are_kept(self) -> None:
        from personalized_agent.agent.work import runner
        with mock.patch.object(runner, "CALLS_KEPT", 3):
            run, _started = self._run(_Cached())
        self.assertEqual(len(run["calls"]), 3)
        self.assertEqual(run["calls"][-1]["n"], run["plan"]["total"]["calls"])
        self.assertEqual(run["plan"]["calls_made"], run["plan"]["total"]["calls"])


class TokensInAndOutAreLimitedApartTests(_Base):

    def test_the_input_limit_pauses_a_run_and_the_output_one_is_far(self) -> None:
        from personalized_agent.agent.work.runner import NOT_FINISHED
        # Each call reads 41,003: a limit of 300,000 in, its reserve of
        # 150,000 kept for the wrap-up, stops new work after a few calls.
        run, started = self._run(_Cached(), limits={
            "minutes": 0, "calls": 0, "tokens_in": 300_000, "tokens_out": 10 ** 7})
        self.assertEqual(run["status"], NOT_FINISHED, run["log"][-6:])
        session = run["plan"]["sessions"][-1]
        self.assertEqual(session["ended_because"], "the input token limit")
        self.assertEqual(session["limits"]["tokens_in"], 300_000)
        self.assertEqual(started["limits"], {"minutes": 0, "tokens_in": 300_000,
                                             "tokens_out": 10 ** 7, "calls": 0})

    def test_the_output_limit_does_too(self) -> None:
        run, _started = self._run(_Cached(), limits={
            "minutes": 0, "calls": 0, "tokens_in": 0, "tokens_out": 2_000})
        self.assertEqual(run["plan"]["sessions"][-1]["ended_because"],
                         "the output token limit")

    def test_the_debuggers_limits_are_none_unless_given(self) -> None:
        from personalized_agent.agent.work.debug_projects import NO_LIMITS, limits_of
        self.assertEqual(NO_LIMITS, {"minutes": 0, "tokens_in": 0, "tokens_out": 0,
                                     "calls": 0})
        self.assertEqual(limits_of({}), NO_LIMITS)
        self.assertEqual(limits_of({"tokens_in": "5000", "tokens_out": ""}),
                         dict(NO_LIMITS, tokens_in=5000))
        # One limit on both, as a caller of before sends it: kept.
        self.assertEqual(limits_of({"tokens": 700}), dict(NO_LIMITS, tokens=700))

    def test_the_limits_route_says_both(self) -> None:
        from personalized_agent.api.routes import build_routes
        from test_experience_debugger import _Request
        said = build_routes(self.app)[("GET", "/api/work/limits")](_Request({}))
        # No limit by default (the user's decision, 2026-10-12): 0 is none.
        self.assertEqual(said["defaults"], {"minutes": 0, "tokens_in": 0,
                                            "tokens_out": 0, "calls": 0})
        self.assertEqual(said["none"], 0)
        self.assertNotIn("tokens", said["defaults"])


class ASoundWhenItEndsTests(_Base):

    def test_played_once_when_it_ends_never_per_call(self) -> None:
        run, started = self._run(_Cached(), sound=True)
        self.assertEqual(run["status"], "done")
        self.assertTrue(started["sound"])
        self.assertTrue(run["sound"])
        self.assertTrue(run["plan"]["sessions"][-1]["sound"])
        self.assertGreater(run["plan"]["total"]["calls"], 4)
        self.assertEqual(self.played, ["done"])

    def test_not_played_unless_asked(self) -> None:
        run, started = self._run(_Cached())
        self.assertEqual(run["status"], "done")
        self.assertFalse(started["sound"])
        self.assertFalse(run["sound"])
        self.assertEqual(self.played, [])
        # Only a plain yes is one.
        from personalized_agent.agent.work.runner import wants_sound
        self.assertEqual([wants_sound(one) for one in
                          (True, 1, "1", "true", "yes", "on", False, 0, "", "no", None,
                           "maybe", 2, {})],
                         [True] * 6 + [False] * 8)

    def test_each_session_has_its_own_choice(self) -> None:
        """Stopped at a limit with no sound asked; continued with one: the
        continued session plays it, once, as it ends - finished."""
        from personalized_agent.agent.work.runner import NOT_FINISHED
        run, _started = self._run(_TwoAgents(), limits={"calls": 8, "minutes": 0,
                                                        "tokens_in": 0, "tokens_out": 0})
        self.assertEqual(run["status"], NOT_FINISHED)
        self.assertEqual(self.played, [])
        said = self.app.work.continue_run(run["id"], self.unit, {"calls": 0, "minutes": 0},
                                          sound=True)
        self.assertNotIn("error", said, said)
        run = self._wait()
        self.assertEqual(run["status"], "done", run["log"][-6:])
        self.assertEqual([one["sound"] for one in run["plan"]["sessions"]], [False, True])
        self.assertEqual(self.played, ["done"])

    def test_a_run_that_stops_at_a_limit_plays_it_as_stopped(self) -> None:
        run, _started = self._run(_TwoAgents(), sound="1",
                                  limits={"calls": 8, "minutes": 0})
        self.assertNotEqual(run["status"], "done")
        self.assertEqual(self.played, ["stopped"])

    def test_a_player_that_fails_is_not_the_runs_failure(self) -> None:
        def broken(outcome):
            self.played.append(outcome)
            raise OSError("no sound card")
        self.app.work.play_sound = broken
        run, _started = self._run(_Cached(), sound=True)
        self.assertEqual(run["status"], "done")
        self.assertEqual(self.played, ["done"])


class ThePlayerTests(unittest.TestCase):
    """The machine's own sound, played without waiting - and never under the
    tests."""

    def _fake_winsound(self):
        fake = types.SimpleNamespace(SND_ALIAS=0x10000, SND_ASYNC=0x1, SND_NODEFAULT=0x2,
                                     MB_ICONASTERISK=0x40, MB_ICONHAND=0x10, played=[])
        fake.PlaySound = lambda sound, flags: fake.played.append((sound, flags))
        fake.MessageBeep = lambda kind: fake.played.append(("beep", kind))
        return fake

    def test_nothing_is_played_under_the_tests(self) -> None:
        from personalized_agent.common import sound
        self.assertEqual(os.environ.get("PA_NO_SOUND"), "1")
        fake = self._fake_winsound()
        with mock.patch.dict(sys.modules, {"winsound": fake}), \
                mock.patch.object(sound.subprocess, "Popen") as popen:
            self.assertFalse(sound.play_end_sound("done"))
        self.assertEqual(fake.played, [])
        popen.assert_not_called()

    def test_windows_plays_a_system_sound_asynchronously(self) -> None:
        from personalized_agent.common import sound
        fake = self._fake_winsound()
        with mock.patch.dict(os.environ, {"PA_NO_SOUND": ""}), \
                mock.patch.dict(sys.modules, {"winsound": fake}), \
                mock.patch.object(sound.sys, "platform", "win32"):
            self.assertTrue(sound.play_end_sound("done"))
            self.assertTrue(sound.play_end_sound("failed"))
        self.assertEqual([one[0] for one in fake.played],
                         ["SystemAsterisk", "SystemExclamation"])
        self.assertTrue(all(one[1] & fake.SND_ASYNC for one in fake.played))

    def test_a_mac_plays_one_of_its_sounds_without_waiting(self) -> None:
        from personalized_agent.common import sound
        with mock.patch.dict(os.environ, {"PA_NO_SOUND": ""}), \
                mock.patch.object(sound.sys, "platform", "darwin"), \
                mock.patch.object(sound.shutil, "which", return_value="/usr/bin/afplay"), \
                mock.patch.object(sound.Path, "is_file", return_value=True), \
                mock.patch.object(sound.subprocess, "Popen") as popen:
            self.assertTrue(sound.play_end_sound("failed"))
        command = popen.call_args[0][0]
        self.assertEqual(command[0], "afplay")
        self.assertTrue(command[1].endswith("Basso.aiff"), command)

    def test_it_never_raises(self) -> None:
        from personalized_agent.common import sound
        with mock.patch.dict(os.environ, {"PA_NO_SOUND": ""}), \
                mock.patch.object(sound.sys, "platform", "linux"), \
                mock.patch.object(sound.shutil, "which", side_effect=OSError("broken")):
            self.assertFalse(sound.play_end_sound("done"))


if __name__ == "__main__":
    unittest.main()
