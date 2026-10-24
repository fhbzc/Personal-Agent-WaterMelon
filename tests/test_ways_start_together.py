"""TWO WAYS STARTED TOGETHER BOTH START (try 5, 2026-10-24): each way's
process began by saving the settings through the one settings.tmp, the
other's move found it gone, and that way read "not started" with nothing
said. Now each process saves through a file of its own, a value set as it
was is not written again, and a way that could not start says why."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401

from personalized_agent.common.config import store as config_store
from personalized_agent.common.config.store import ConfigStore

ROOT = Path(__file__).resolve().parents[1]


class TheSettingsSavedTogetherTests(unittest.TestCase):

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.dir = Path(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_each_process_its_own_tmp(self) -> None:
        self.assertEqual(config_store._own_tmp(self.dir / "settings.json").name,
                         f"settings.{os.getpid()}.tmp")

    def test_two_processes_saving_at_once(self) -> None:
        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(ROOT)!r})
            from pathlib import Path
            from personalized_agent.common.config.store import ConfigStore
            store = ConfigStore(Path({str(self.dir)!r}))
            for n in range(150):
                store.set("workspace_root", f"/home/sam/garden/{{sys.argv[1]}}/{{n}}",
                          internal=True)
            print("saved")
        """)
        children = [subprocess.Popen([sys.executable, "-c", script, name],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    for name in ("one", "two")]
        for child in children:
            out, err = child.communicate(timeout=120)
            self.assertEqual((child.returncode, out.strip()), (0, "saved"), err[-600:])
        self.assertTrue(ConfigStore(self.dir).get("workspace_root").startswith("/home/sam/garden/"))

    def test_a_value_set_as_it_was_is_not_written_again(self) -> None:
        store = ConfigStore(self.dir)
        store.set("workspace_root", "/home/sam/garden", internal=True)
        with mock.patch.object(store, "_save") as save:
            store.set("workspace_root", "/home/sam/garden", internal=True)
            save.assert_not_called()
            store.set("workspace_root", "/home/sam/orchard", internal=True)
            save.assert_called_once()


class AWayThatCouldNotStartTests(unittest.TestCase):

    def test_said_on_the_way(self) -> None:
        from personalized_agent.agent.work.debug_projects import DebugProjects
        from personalized_agent.agent.work.runner import WorkRuns

        class Work:
            waited_out = {}
            started_out = {}

            def waiting_of(self, unit_id):
                return None

            def unwait(self, unit_id):
                return False
        projects = DebugProjects.__new__(DebugProjects)
        projects.work = Work()
        projects.unit = lambda project, variant: {"id": "dbg_1~5:noctx"}
        self.assertEqual(projects.waiting("dbg_1", "noctx"), {})
        Work.started_out["dbg_1~5:noctx"] = "the run could not start: a file was gone"
        self.assertEqual(projects.waiting("dbg_1", "noctx"),
                         {"could_not_start": "the run could not start: a file was gone"})
        Work.waited_out["dbg_1~5:noctx"] = "no room"
        self.assertTrue(projects.waiting("dbg_1", "noctx")["waited"])
        projects.unwait("dbg_1", "noctx")
        self.assertEqual(projects.waiting("dbg_1", "noctx"), {})

        runs = WorkRuns.__new__(WorkRuns)
        runs.in_subprocess = True
        runs._lock = __import__("threading").Lock()
        runs.starting, runs.started_out, runs.waited_out = {}, {}, {}
        runs._busy = lambda unit: ""
        runs.has_history = lambda unit: True
        runs._wait_for_room = lambda kind, unit, args: None
        runs._launch = lambda kind, unit, args: {"error": "the run could not start: gone"}
        unit = {"id": "dbg_1~5:noctx", "debug": {"folder": "/home/sam/trials"}}
        self.assertEqual(runs._way_apart("start", unit, False, {"instruction": "x"}),
                         {"error": "the run could not start: gone"})
        self.assertEqual(runs.started_out, {"dbg_1~5:noctx": "the run could not start: gone"})
        runs._launch = lambda kind, unit, args: {"run_id": "run_1"}
        runs._way_apart("start", unit, False, {"instruction": "x"})
        self.assertEqual(runs.started_out, {}, "started again: no longer said")

    def test_the_page_says_it(self) -> None:
        script = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("It could not start: ${escapeHtml(refused)}", script)
        self.assertIn('refused ? "could not start"', script)


if __name__ == "__main__":
    unittest.main()
