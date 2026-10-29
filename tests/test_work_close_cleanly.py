"""THE APPLICATION CLOSING - Quit, or an update taken up (the user,
2026-10-28): every run going stops cleanly, keeping where it stands as its
progress point now - in a round, the round and what it did in it; between
rounds, after the last. Continued (by hand, nothing told), it goes on from
there, not from a point before."""

from __future__ import annotations

import json
import threading
import time

from test_work_runs import _TwoAgents
from test_work_versions import _RunsBase

from personalized_agent.agent.work.runner import INTERRUPTED
from personalized_agent.agent.work.versions import VersionStore


class _HeldIn(_TwoAgents):
    """Answers as the two agents do - and holds the call whose words say
    `where` until let go, saying when it is there."""

    def __init__(self, where, after_tools=0, **kwargs):
        kwargs.setdefault("rounds_to_complete", 99)
        kwargs.setdefault("full_review", "never")
        super().__init__(**kwargs)
        self.where, self.after_tools = where, after_tools
        self.there = threading.Event()
        self.go = threading.Event()

    def invoke(self, request, role=""):
        text = json.dumps(request.messages, default=str)
        tools = sum(1 for one in request.messages if one["role"] == "assistant")
        if self.where in text and tools >= self.after_tools and not self.go.is_set():
            self.there.set()
            self.go.wait(30)
        return super().invoke(request, role)


class ClosedCleanlyTests(_RunsBase):

    def _start(self, models):
        self.app.work.models = models
        started = self.app.work.start(self.unit, "write the notes")
        self.assertNotIn("error", started, started)
        self.assertTrue(models.there.wait(30), "the run did not get there")
        return started["run"], self.agent_folder(started)

    @staticmethod
    def agent_folder(started):
        from pathlib import Path
        return Path(started["folder"])

    def _closed(self, run_id, folder, models):
        """Closed while held - its point read before the call is let go: the
        process would end here, nothing after it."""
        kept = self.app.work.close("it was quit")
        point = VersionStore(folder / "runs" / run_id).resume_point()
        self.app.work.stop(run_id, now=True)
        models.go.set()
        self._wait(run_id)
        # Marked as the next start marks it.
        self.app.db.execute("UPDATE work_runs SET status = ? WHERE id = ?",
                            (INTERRUPTED, run_id))
        return kept, point

    def test_in_a_round_it_goes_on_from_that_round_told_what_it_did(self) -> None:
        # Held in round 1's work agent call, after its first tool.
        models = _HeldIn("WORK ONE ROUND", after_tools=1)
        run_id, folder = self._start(models)
        kept, point = self._closed(run_id, folder, models)
        self.assertEqual(kept, [run_id])
        self.assertEqual(VersionStore(folder / "runs" / run_id).resume_point(), point,
                         "still the latest when it is continued")
        self.assertRegex(point["label"],
                         r"^in round 1, at \d\d:\d\d, when the application closed \(it was quit\)$")
        events = self.app.work.live(run_id)["events"]
        self.assertTrue(any(one["kind"] == "closing" for one in events))
        agents = _TwoAgents(rounds_to_complete=1)
        said, after = self._continue(run_id, agents)
        self.assertTrue(said["rolled_back"]["resume_point"])
        told = [str(request.messages[0]["content"]) for request in agents.asked
                if "THIS ROUND WAS CUT OFF IN ITS MIDDLE" in str(request.messages[0]["content"])]
        self.assertTrue(told, "the round it was in, told what it had done")
        self.assertIn("write_file(notes.md)", told[0])
        self.assertEqual(after["status"], "done", after["log"][-6:])

    def test_in_a_rounds_review_the_round_is_still_known(self) -> None:
        models = _HeldIn("REVIEW THIS ROUND")
        run_id, folder = self._start(models)
        kept, point = self._closed(run_id, folder, models)
        self.assertEqual(kept, [run_id])
        self.assertTrue(point["label"].startswith("in round 1, at "), point["label"])
        state = VersionStore(folder / "runs" / run_id).get_json(point["state"])
        did = state["plan"]["resumed_mid_round"]["did"]
        self.assertIn("write_file(notes.md)", did, "what its work agent did, under review")

    def test_between_rounds_after_the_last(self) -> None:
        # Held in the full review after round 1: the round is recorded.
        models = _HeldIn("DO THE FULL REVIEW NOW", full_review="always")
        run_id, folder = self._start(models)
        kept, point = self._closed(run_id, folder, models)
        self.assertEqual(kept, [run_id])
        self.assertEqual(point["label"], "after round 1, when the application closed (it was quit)")
        self.assertEqual(point["round"], 1)

    def test_nothing_going_nothing_kept(self) -> None:
        self.assertEqual(self.app.work.close("it was quit"), [])

    def test_the_closing_waits_no_longer_than_it_may(self) -> None:
        from unittest import mock
        from personalized_agent.agent.work import runner as runner_module
        models = _HeldIn("WORK ONE ROUND", after_tools=1)
        run_id, _folder = self._start(models)
        slow = threading.Event()
        with mock.patch.object(runner_module.Run, "close_for",
                               lambda run, why: slow.wait(10)):
            began = time.monotonic()
            self.assertEqual(self.app.work.close("it was quit", seconds=0.5), [])
            self.assertLess(time.monotonic() - began, 5)
        slow.set()
        models.go.set()
        self.app.work.stop(run_id, now=True)
        self._wait(run_id)


class TheApplicationClosesItsWorkTests(_RunsBase):

    def _closed(self, updating=None):
        from unittest import mock
        asked = []
        self.app.work.close = lambda why, **_: asked.append(why) or []
        self.app.config.set("api_port", 8943)
        with mock.patch.object(self.app.updater, "updating", return_value=updating):
            self.app.start(with_api=False)
            self.app.stop()
        self.assertIn("WorkRuns", self.app._shutdown_spent)
        return asked

    def test_quit(self) -> None:
        self.assertEqual(self._closed(), ["it was quit"])

    def test_an_update(self) -> None:
        self.assertEqual(self._closed({"to": "0.3.0", "step": "installing"}), ["for an update"])
