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


if __name__ == "__main__":
    unittest.main()
