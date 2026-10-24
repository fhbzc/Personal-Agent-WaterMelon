"""EACH RUN IN A PROCESS OF ITS OWN, FOR REAL: real child processes, scheduled
by the governor from what the computer has free - like a small operating
system's scheduler - and told through their control files.

Slow (a minute or two): each run is a real process of this program, with the
offline model made to answer slowly (PA_TEST_OFFLINE_DELAY) so a run lasts
long enough to pause, resume, approve and stop. What the governor sees is set
where a case needs it (how much memory is free, how busy the processors are);
everything else - the processes, their priority, the files they are told and
shown through - is real.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

import psutil

from support import TemporaryAgent

from personalized_agent.agent.work.governor import Governor

DELAY = "1.5"


def _wait(until, seconds=60.0, every=0.3):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        said = until()
        if said:
            return said
        time.sleep(every)
    return until()


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.before = {k: os.environ.get(k) for k in ("PA_TEST_OFFLINE_DELAY",
                                                      "PA_TEST_OFFLINE_SHUT")}
        os.environ["PA_TEST_OFFLINE_DELAY"] = DELAY
        self.shut = Path(tempfile.mkdtemp()) / "shut"
        os.environ["PA_TEST_OFFLINE_SHUT"] = str(self.shut)
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.work = self.app.work
        self.work.in_subprocess = True
        self.work.governor = Governor(self.work)

    def tearDown(self) -> None:
        for run in list(self.work._running.values()):
            if getattr(run, "process", None) is not None and run.process.poll() is None:
                run.process.kill()
                run.process.wait(timeout=20)
        self.agent.cleanup()
        for key, value in self.before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _project(self, name):
        made = self.app.state_heartbeat.experience.store.create_work_project(name)
        return self.app.state_heartbeat.experience.unit(made["unit_id"])

    def _live(self, run_id):
        return self.work.live(run_id, 0) or {}


class ARunInItsOwnProcessTests(_Base):

    def test_paused_resumed_approved_and_stopped_through_its_control_file(self) -> None:
        said = self.work.start(self._project("Tea notes"), "Write a short note about tea")
        run_id = said["run"]
        child = self.work._running[run_id]
        self.assertNotEqual(child.pid, os.getpid())
        process = psutil.Process(child.pid)
        if sys.platform == "win32":
            self.assertEqual(process.nice(), psutil.BELOW_NORMAL_PRIORITY_CLASS,
                             "below the agent's own priority")
        # Shown live from what it writes: its events and its snapshot.
        self.assertTrue(_wait(lambda: len(self._live(run_id).get("events") or []) >= 2))
        # Paused, it says so; resumed, it goes on.
        self.assertTrue(self.work.pause(run_id))
        self.assertTrue(_wait(lambda: self._live(run_id).get("progress", {}).get("pause_asked")))
        self.assertTrue(self.work.resume(run_id))
        self.assertTrue(_wait(lambda: not self._live(run_id).get("progress", {}).get(
            "pause_asked")))
        # The model needs the user: the run asks, and an approval goes through.
        self.shut.write_text("shut", encoding="utf-8")
        pending = _wait(lambda: self._live(run_id).get("pending"), seconds=90)
        self.assertTrue(pending, "the run asks the user about its model")
        self.shut.unlink()
        self.work.approve(run_id, pending[0]["id"], True)
        self.assertTrue(_wait(lambda: not self._live(run_id).get("pending"), seconds=60))
        # Stopped now: the process ends, and its record says how it ended.
        self.assertTrue(self.work.stop(run_id, now=True))
        self.assertTrue(_wait(lambda: not child.alive(), seconds=90))
        # And the process itself ends - a moment after the run says it did.
        self.assertTrue(_wait(lambda: child.process.poll() is not None, seconds=30))
        events = " ".join(one["text"] for one in self._live(run_id)["events"])
        for words in ("You paused the run", "You resumed the run"):
            self.assertIn(words, events)
        self.assertNotEqual(self.work.run(run_id)["status"], "running")


class TheSchedulerTests(_Base):

    def test_what_it_sees_here(self) -> None:
        seen = self.work.governor.look()
        print(f"\nthis computer: {os.cpu_count()} cores, processors {seen['cpu']}% busy, "
              f"{seen['free_gb']} GB free - room for {seen['most_runs']} runs at once")
        self.assertGreaterEqual(seen["most_runs"], 0)
        self.assertTrue(self.work.governor.room(0)[0] or seen["free_gb"] < 0.75)

    def test_only_as_many_as_there_is_room_for_the_rest_wait_then_start(self) -> None:
        gov = self.work.governor
        gov.look = lambda: gov.seen()                 # what it sees, set here
        gov.samples.clear()
        gov.samples.extend([20.0] * 3)
        gov.free_gb, gov.free_share = 3.2, 0.4         # room for two: 3.2 GB / 1.5
        # and processors for more than two (one run per four cores)
        from unittest import mock
        cores = mock.patch("personalized_agent.agent.work.governor.os.cpu_count",
                           return_value=16)
        cores.start()
        self.addCleanup(cores.stop)
        units = [self._project(f"Piece {n}") for n in range(4)]
        said = [self.work.start(unit, f"Piece {n}: a short note") for n, unit in enumerate(units)]
        started = [one for one in said if one.get("run")]
        waiting = [one for one in said if one.get("waiting")]
        self.assertEqual((len(started), len(waiting)), (2, 2), said)
        self.assertEqual(sum(1 for run in self.work._running.values() if run.alive()), 2)
        self.assertEqual([one["position"] for one in waiting], [1, 2])
        # One ends: the first waiting starts - the next look.
        first = self.work._running[started[0]["run"]]
        self.work.stop(first.id, now=True)
        self.assertTrue(_wait(lambda: not first.alive(), seconds=90))
        gov.steer()
        self.assertEqual(len(self.work.waiting), 1)
        self.assertEqual(sum(1 for run in self.work._running.values() if run.alive()), 2)
        # The computer busy: no new start, and the runs go to the lowest
        # priority; calm again, back to below normal.
        gov.samples.clear()
        gov.samples.extend([95.0] * 3)
        second = self.work._running[started[1]["run"]]
        self.work.stop(second.id, now=True)
        self.assertTrue(_wait(lambda: not second.alive(), seconds=90))
        gov.steer()
        alive = [run for run in self.work._running.values() if run.alive()]
        self.assertEqual(len(alive), 1, "busy: no other start")
        self.assertEqual(len(self.work.waiting), 1)
        if sys.platform == "win32":
            self.assertEqual(psutil.Process(alive[0].pid).nice(), psutil.IDLE_PRIORITY_CLASS)
            gov.samples.clear()
            gov.samples.extend([10.0] * 3)
            gov.steer()
            self.assertEqual(psutil.Process(alive[0].pid).nice(),
                             psutil.BELOW_NORMAL_PRIORITY_CLASS)
        # Calm: the last one waiting starts.
        self.assertEqual(self.work.waiting, [])

    def test_short_of_memory_the_newest_run_pauses_and_resumes(self) -> None:
        gov = self.work.governor
        gov.look = lambda: gov.seen()
        gov.samples.clear()
        gov.samples.extend([20.0] * 3)
        gov.free_gb, gov.free_share = 8.0, 0.5
        said = self.work.start(self._project("Memory"), "A short note")
        run_id = said["run"]
        self.assertTrue(_wait(lambda: len(self._live(run_id).get("events") or []) >= 1))
        gov.free_gb, gov.free_share = 0.9, 0.05
        gov.steer()
        self.assertTrue(_wait(lambda: self._live(run_id).get("progress", {}).get("pause_asked")))
        events = " ".join(one["text"] for one in self._live(run_id)["events"])
        self.assertIn("short of memory", events)
        gov.free_gb, gov.free_share = 8.0, 0.5
        gov.steer()
        self.assertTrue(_wait(lambda: not self._live(run_id).get("progress", {}).get(
            "pause_asked")))
        self.work.stop(run_id, now=True)


class ADebuggerWayInItsOwnProcessTests(_Base):
    """A WAY OF THE EXPERIENCE DEBUGGER goes in a process of its own too: the
    agent holds the application's code sealed for it while that process
    lives, and its child finds the way by the way's own id."""

    def setUp(self) -> None:
        super().setUp()
        from personalized_agent.agent.work.debug_projects import DebugProjects
        self.projects = DebugProjects(self.work)
        bench = Path(self.agent.dir) / "bench"
        bench.mkdir()
        self.project = self.projects.create("Tea notes", "plain", "", str(bench))["project"]

    def tearDown(self) -> None:
        from personalized_agent.runtime.isolation import CodeSeal
        super().tearDown()
        CodeSeal.wait(self.agent.code_root, 120)
        CodeSeal.reset()

    def test_started_shown_held_sealed_and_stopped(self) -> None:
        from unittest import mock
        from personalized_agent.common import paths
        from personalized_agent.runtime.isolation import CodeSeal
        code = self.agent.code_root
        self.work.sandbox_enabled = True
        with mock.patch.object(paths, "code_root_for_children", lambda: code):
            said = self.projects.start(self.project["id"], "single",
                                       instruction="Write a short note about tea")
        self.assertFalse(said.get("error"), said)
        run_id = said["run"]
        child = self.work._running[run_id]
        self.assertNotEqual(child.pid, os.getpid(), "not in the agent's process")
        self.assertTrue(child.debug)
        self.assertTrue(self.work.going(debug=True))
        self.assertFalse(self.work.going(debug=False))
        # The code sealed for it from before it started, while it goes.
        self.assertEqual(CodeSeal.held(code), 1)
        # Shown live, and listed with the rest - paused or not, as it says.
        self.assertTrue(_wait(lambda: len(self._live(run_id).get("events") or []) >= 2))
        listed = {one["id"]: one for one in self.work.runs(said["unit"])}
        self.assertTrue(listed[run_id]["alive"])
        self.assertFalse(listed[run_id]["paused"])
        self.assertEqual(self.work.run(run_id)["unit_id"], said["unit"])
        # Stopped: its process ends, and the code is let go with it.
        self.assertTrue(self.work.stop(run_id, now=True))
        self.assertTrue(_wait(lambda: child.process.poll() is not None, seconds=90))
        self.assertTrue(_wait(lambda: CodeSeal.held(code) == 0, seconds=10))

    def test_starting_while_its_process_gets_ready_and_the_others_not_kept_waiting(self) -> None:
        # The full way took two minutes to start: its last run, ended, was
        # shown meanwhile, and a second Start came back "already going".
        import threading
        gate = threading.Event()
        launched = []

        def slow(kind, unit, args):
            launched.append(unit["id"])
            if unit["id"].endswith(":full"):
                gate.wait(30)
            return {"run": "r-" + unit["id"]}
        self.work._launch = slow
        self.work._room_for = lambda head=False: ""
        said = {}
        first = threading.Thread(target=lambda: said.update(self.projects.start(
            self.project["id"], "full", instruction="Write a short note about tea")))
        first.start()
        self.assertTrue(_wait(lambda: len(launched) == 1, seconds=20))
        unit_id = launched[0]
        self.assertEqual(self.work.waiting_of(unit_id)["starting"], True)
        again = self.projects.start(self.project["id"], "full", instruction="again")
        self.assertIn("starting", again.get("error", ""))
        beside = self.projects.start(self.project["id"], "noctx", instruction="Write a note")
        self.assertFalse(beside.get("error"), beside)
        self.assertEqual(len(launched), 2, "started while the full way was getting ready")
        gate.set()
        first.join(30)
        self.assertEqual(said.get("run"), "r-" + unit_id)
        self.assertIsNone(self.work.waiting_of(unit_id))

    def test_the_child_never_changes_a_seal_held_for_it(self) -> None:
        from personalized_agent.runtime.isolation import CodeSeal
        code = self.agent.code_root
        CodeSeal.held_elsewhere(code)
        self.assertTrue(CodeSeal.wait(code, timeout=1))
        self.assertIn(Path(os.path.realpath(code)), [Path(os.path.realpath(one))
                                                     for one in CodeSeal.sealed_roots()])
        CodeSeal.hold(code)
        CodeSeal.release(code)
        CodeSeal.release(code)
        self.assertEqual(CodeSeal.held(code), 1, "its count is not the whole of them")
        self.assertIsNone(CodeSeal._worker, "nothing on disk changed from here")


if __name__ == "__main__":
    unittest.main()
