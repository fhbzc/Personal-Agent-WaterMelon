"""RESUME SEEN AS IT HAPPENS, AND QUICKER (the user, 2026-10-27: resumed from
the to-do, and Work showed nothing for the two minutes the run was rolled
back to its progress point)."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401

from personalized_agent.agent.work import child
from personalized_agent.runtime import filesystem as fs

WEB = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"


class RememberedForOneBatchTests(unittest.TestCase):

    def test_only_inside_the_batch_and_only_on_its_thread(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            folder = Path(work)
            calls = []
            real = fs._real_of_existing_ancestor

            def counted(path):
                calls.append(str(path))
                return real(path)
            with mock.patch.object(fs, "_real_of_existing_ancestor", side_effect=counted):
                fs.real_of_existing_ancestor(folder)
                fs.real_of_existing_ancestor(folder)
                self.assertEqual(len(calls), 2, "outside a batch: worked out each time")
                calls.clear()
                with fs.remembering():
                    fs.real_of_existing_ancestor(folder)
                    fs.real_of_existing_ancestor(folder)
                    other = threading.Thread(target=fs.real_of_existing_ancestor, args=(folder,))
                    other.start()
                    other.join()
                self.assertEqual(len(calls), 2, "once in the batch; again on another thread")
                calls.clear()
                fs.real_of_existing_ancestor(folder)
                self.assertEqual(len(calls), 1, "forgotten after it")


class TheRollbackSaysWhatItDoesTests(unittest.TestCase):

    def test_checking_then_putting_back_with_the_counts(self) -> None:
        from personalized_agent.agent.work import versions
        said = []
        say = versions._Saying(said.append)
        say("checking", 0, 10, now=True)
        say("checking", 3, 10)                      # within half a second: not said
        say("putting back", 0, 4, now=True)
        self.assertEqual(said, [{"step": "checking", "done": 0, "total": 10},
                                {"step": "putting back", "done": 0, "total": 4}])

    def test_the_agent_reads_it_in_words(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            folder = Path(work)
            self.assertEqual(child.starting_said(folder, "continue"), "resuming")
            self.assertEqual(child.starting_said(None, "start"), "starting")
            child._write_json(folder / child.STARTING,
                              {"step": "checking", "done": 3200, "total": 7856})
            self.assertEqual(child.starting_said(folder, "continue"),
                             "rolling back to its progress point: 3,200 of 7,856 files checked")

    def test_the_child_writes_it_where_the_agent_reads(self) -> None:
        source = (Path(child.__file__)).read_text(encoding="utf-8")
        self.assertIn("work.on_progress = lambda said: _write_json(folder / STARTING, said)",
                      source)
        runner = (Path(child.__file__).parent / "runner.py").read_text(encoding="utf-8")
        self.assertIn('progress=getattr(self, "on_progress", None)', runner)


class AStartingRunIsAmongTheRunsGoingTests(unittest.TestCase):

    def test_said_as_starting_with_what_it_does(self) -> None:
        from personalized_agent.agent.work.runner import WorkRuns
        work = WorkRuns.__new__(WorkRuns)
        work._running = {}
        with tempfile.TemporaryDirectory() as folder:
            child._write_json(Path(folder) / child.STARTING,
                              {"step": "putting back", "done": 10, "total": 3514})
            work.coming = {"work_1": {"run": "run_1", "unit": "work_1", "kind": "continue",
                                      "since": "now", "folder": Path(folder), "debug": False}}
            states = work.run_states()
        self.assertEqual(len(states), 1)
        self.assertEqual((states[0]["run"], states[0]["state"]), ("run_1", "starting"))
        self.assertIn("10 of 3,514 files put back", states[0]["activity"])

    def test_launch_holds_it_while_its_process_starts(self) -> None:
        from personalized_agent.agent.work.runner import WorkRuns
        work = WorkRuns.__new__(WorkRuns)
        work._running, work.coming = {}, {}
        seen_while = []

        def launch(owner, kind, unit, seen=None, **args):
            seen("child-folder")
            seen_while.append(dict(owner.coming[unit["id"]]))
            return {"error": "stopped here"}
        with mock.patch.object(child, "launch", side_effect=launch):
            work._launch("continue", {"id": "work_1"}, {"run_id": "run_1"})
        self.assertEqual(seen_while[0]["run"], "run_1")
        self.assertEqual(seen_while[0]["folder"], "child-folder")
        self.assertEqual(work.coming, {}, "let go once it has started, or not")


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    return source[start:source.index("\n}\n", start) + 3]


class ThePageShowsItTests(unittest.TestCase):

    def test_the_todo_offers_nothing_to_pause_while_it_starts(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is needed")
        source = (WEB / "app.js").read_text(encoding="utf-8")
        script = ("function escapeHtml(s) { return String(s); }"
                  "const workGoing = { byUnit: { w: { run: 'run_1', state: 'starting' } } };"
                  + _function(source, "ovTodoControls")
                  + "process.stdout.write(JSON.stringify(ovTodoControls({ unit: 'w' })));")
        done = subprocess.run([node, "-e", script], capture_output=True, text=True,
                              encoding="utf-8", timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), "")

    def test_works_line_says_it(self) -> None:
        source = (WEB / "app.js").read_text(encoding="utf-8")
        line = _function(source, "xpDrawTitleRun")
        self.assertIn('coming.state === "starting"', line)
        self.assertIn("coming.activity", line)
        self.assertIn('starting: "starting"', source[source.index("const OV_TODO_STATE"):][:200])


if __name__ == "__main__":
    unittest.main()
