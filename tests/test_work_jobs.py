"""Code that takes longer than one call, and what code leaves running.

A JOB: run_python with background=true runs the code as run_python runs it
- the same sandbox, the same folders - while the agent goes on; job_status
says how it goes (what it printed so far) and, waited on, returns its whole
result once it ends; stop_job stops it and what it started; the run's end
stops every job.

WHAT THE CODE STARTS ENDS WHEN IT ENDS: a program started and not waited
for was ended with the code, silently - an agent waited ten minutes for a
check that had died with the script that started it. The result says so
now, and how to run it instead. And the work rules say all of it, with every
limit and prohibition of the environment, in one place."""

from __future__ import annotations

import time
import unittest
from pathlib import Path

from support import TemporaryAgent

from personalized_agent.agent.work import tools as tools_module
from personalized_agent.agent.work.tools import WorkTools


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        experience = self.app.state_heartbeat.experience
        made = experience.store.create_work_project("Jobs")
        self.unit = experience.unit(made["unit_id"])
        self.folder = self.app.work.folder_of(self.unit)
        self.folder.mkdir(parents=True, exist_ok=True)
        runtime = self.app.work._runtime(self.unit, self.folder)
        self.tools = WorkTools(runtime)
        self.addCleanup(self.tools.stop_jobs)

    def tearDown(self) -> None:
        self.agent.cleanup()


class WhatTheCodeLeavesRunningTests(_Base):

    def test_said_and_ended(self) -> None:
        said = self.tools.call("run_python", {"code": (
            "import subprocess, sys\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(4); "
            "open(\"late.txt\", \"w\").write(\"x\")'])\n"
            "print('started')\n")})
        self.assertIn("started", said["stdout"])
        self.assertEqual(len(said.get("ended_with_the_code") or []), 1, said)
        self.assertIn("time.sleep(4)", said["ended_with_the_code"][0])
        self.assertIn("ended with it", said["note"])
        self.assertIn("background=true", said["note"])
        self.assertNotIn(tools_module.LEFT_RUNNING_MARK, said["stderr"])
        time.sleep(6)
        self.assertFalse((self.folder / "late.txt").exists(), "it did not outlive the code")

    def test_a_program_waited_for_is_not_said(self) -> None:
        said = self.tools.call("run_python", {"code": (
            "import subprocess, sys\n"
            "subprocess.run([sys.executable, '-c', 'print(1)'], check=True)\n")})
        self.assertTrue(said["ok"], said)
        self.assertNotIn("ended_with_the_code", said)


class JobsTests(_Base):

    def test_the_launcher_is_python(self) -> None:
        code = tools_module.JOB_LAUNCHER.format(log="a.log", stop="stop", code="code.py")
        compile(code, "launcher", "exec")

    def test_runs_while_the_agent_goes_on_and_is_waited_for(self) -> None:
        began = time.time()
        said = self.tools.call("run_python", {"background": True, "code": (
            "import subprocess, sys, time\n"
            "print('first part', flush=True)\n"
            "subprocess.run([sys.executable, '-c', 'print(\"from its child\")'],\n"
            "               stdout=sys.stdout, check=True)\n"
            "time.sleep(3)\n"
            "open('made.txt', 'w').write('x')\n"
            "print('last part')\n")})
        self.assertLess(time.time() - began, 3, "returned at once")
        job = said["job"]
        self.assertEqual(said["status"], "running")
        seen = None
        for _ in range(60):
            seen = self.tools.call("job_status", {"job": job})
            if "from its child" in seen.get("printed_so_far", ""):
                break
            time.sleep(0.1)
        self.assertEqual(seen["status"], "running")
        self.assertIn("first part", seen["printed_so_far"])
        self.assertIn("from its child", seen["printed_so_far"])
        done = self.tools.call("job_status", {"job": job, "wait": 60})
        self.assertEqual(done["status"], "done", done)
        self.assertIn("last part", done["result"]["stdout"])
        self.assertIn(str(self.folder / "made.txt"), done["result"]["files_changed"])
        listed = self.tools.call("job_status", {})
        self.assertEqual([one["job"] for one in listed["jobs"]], [job])
        # Numbered as they start, and one not there said with those that are.
        self.assertEqual(job, "job_1")
        missing = self.tools.call("job_status", {"job": "job_9"})
        self.assertIn("its jobs: job_1 (done)", missing["error"])

    def test_stopped_with_what_it_started(self) -> None:
        said = self.tools.call("run_python", {"background": True, "code": (
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8); "
            "open(\"child.txt\", \"w\").write(\"x\")'])\n"
            "time.sleep(60)\n"
            "open('never.txt', 'w').write('x')\n")})
        time.sleep(1.5)
        stopped = self.tools.call("stop_job", {"job": said["job"]})
        self.assertEqual(stopped["status"], "stopped", stopped)
        time.sleep(9)
        self.assertFalse((self.folder / "never.txt").exists())
        self.assertFalse((self.folder / "child.txt").exists(), "its child stopped with it")

    def test_so_many_at_once_and_ended_with_the_run(self) -> None:
        jobs = [self.tools.call("run_python", {"background": True,
                                               "code": "import time\ntime.sleep(60)\n"})
                for _ in range(tools_module.MOST_JOBS)]
        self.assertTrue(all(one.get("job") for one in jobs), jobs)
        refused = self.tools.call("run_python", {"background": True, "code": "print(1)"})
        self.assertIn("jobs are running already", refused["error"])
        self.tools.stop_jobs()
        self.assertEqual({self.tools.call("job_status", {"job": one["job"]})["status"]
                          for one in jobs}, {"stopped"})

    def test_not_among_the_slides_tools(self) -> None:
        self.tools.only = ("run_python", "read_file")
        said = self.tools.call("run_python", {"background": True, "code": "print('here')"})
        self.assertNotIn("job", said)
        self.assertIn("here", said["stdout"])
        self.assertNotIn("job_status", [one.name for one in self.tools.specs()])


class TheEnvironmentIsSaidTests(unittest.TestCase):

    def test_every_limit_and_prohibition_in_one_place(self) -> None:
        from personalized_agent.agent.work.runner import (AGENT_SYSTEM, ALONE_SYSTEM,
                                                          environment_rules)
        said = environment_rules("C:/work", "No network.", debug=True)
        for words in ("run logs", "where sign-ins and", "ONLY in C:/work", "WAL mode",
                      "NEW Python process", "ENDS WHEN IT ENDS", "background=true",
                      "job_status", "at most 900", "no shell", "pip run from code",
                      "No network.", "other folders closed to this work", "NATIVE LIBRARIES"):
            self.assertIn(words, said)
        self.assertNotIn("closed to this work", environment_rules("C:/work", "x"))
        # A way is never told it is one of several being compared.
        for words in ("debugger", "variant", "other two ways", "benchmark"):
            self.assertNotIn(words, said.lower())
        for system in (AGENT_SYSTEM, ALONE_SYSTEM):
            self.assertIn("{environment}", system)
            self.assertNotIn("{network}", system)


if __name__ == "__main__":
    unittest.main()
