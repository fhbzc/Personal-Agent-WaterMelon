"""The computer shared: a Work debugger way waits its turn as any run does,
and starts by itself when there is room; every child runs a step below the
user's own programs; what an earlier run of the application left going is
found and ended; and the Resource debugger says what the application and
everything it started costs - processors, memory, graphics, disk."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from support import TemporaryAgent
from test_experience_debugger import _DebuggerBase, _Models, _until

from personalized_agent.common import children

SLEEPER = "import time; time.sleep(60)"


class ADebuggerWayWaitsItsTurnTests(_DebuggerBase):

    def setUp(self) -> None:
        super().setUp()
        self.room = {"free": False}
        self.counted = []

        def room(going):
            self.counted.append(going)
            return (True, "") if self.room["free"] else (False, "the computer is busy")
        self.app.work.governor.room = room

    def test_no_room_it_waits_and_starts_by_itself(self) -> None:
        self.app.work.models = _Models()
        project = self._project()
        said = self._start(project, "single")
        self.assertTrue(said.get("waiting"), said)
        self.assertEqual(said["why"], "the computer is busy")
        self.assertFalse(self.app.work._running)
        waits = self._get("/api/debugger/experience/runs",
                          {"project": project["id"], "variant": "single"})["waiting"]
        self.assertEqual((waits["why"], waits["position"]), ("the computer is busy", 1))
        shown = self._get("/api/debugger/experience/projects")["projects"][0]
        self.assertTrue([v for v in shown["variants"] if v["variant"] == "single"][0]["alive"])
        # Asked again while it waits: not twice in the line.
        from personalized_agent.api.errors import ApiError
        with self.assertRaises(ApiError):
            self._start(project, "single")
        # Still no room: still waiting.
        self.assertEqual(self.app.work.start_waiting(), [])
        self.room["free"] = True
        started = self.app.work.start_waiting()
        self.assertEqual(len(started), 1, started)
        self.assertIn("run", started[0])
        self.assertEqual(self._finished(project, "single")["status"], "done")
        self.assertEqual(self._get("/api/debugger/experience/runs",
                                   {"project": project["id"], "variant": "single"})["waiting"], {})

    def test_one_waiting_is_not_started_after_all(self) -> None:
        project = self._project()
        self.assertTrue(self._start(project, "noctx").get("waiting"))
        self.assertEqual(self._post("/api/debugger/experience/unwait",
                                    {"project": project["id"], "variant": "noctx"}),
                         {"unwaited": True})
        self.room["free"] = True
        self.assertEqual(self.app.work.start_waiting(), [])
        self.assertFalse(self.app.work._running)

    def test_clearing_a_way_takes_it_out_of_the_line(self) -> None:
        project = self._project()
        self.assertTrue(self._start(project, "full").get("waiting"))
        self._post("/api/debugger/experience/projects",
                   {"action": "clear", "id": project["id"], "variant": "full"})
        self.assertEqual(self.app.work.waiting, [])

    def test_the_first_asked_goes_first(self) -> None:
        project = self._project()
        self._start(project, "single")
        self._start(project, "noctx")
        self.room["free"] = True
        order = []
        real = self.app.work.start

        def start(unit, *args, **kwargs):
            order.append(unit["debug"]["variant"])
            return {"run": "r" + str(len(order))}
        self.app.work.start = start
        try:
            self.app.work.start_waiting()
        finally:
            self.app.work.start = real
        self.assertEqual(order, ["single", "noctx"])

    def test_every_run_going_is_counted_the_debuggers_too(self) -> None:
        models = _Models(gate=True)
        self.app.work.models = models
        self.room["free"] = True
        project = self._project()
        self.assertIn("run", self._start(project, "single"))
        self.assertTrue(_until(lambda: models.waiting >= 1, 30))
        self.counted.clear()
        self._start(project, "noctx")
        self.assertEqual(self.counted[0], 1)
        models.gate.set()


class AStepBelowTheUsersProgramsTests(unittest.TestCase):

    @unittest.skipUnless(sys.platform == "win32", "Windows' priority classes")
    def test_a_child_starts_below_normal_unless_the_caller_chose(self) -> None:
        seen = []

        class Started:
            pid = 0
            _handle = 0

        def popen(command, **kwargs):
            seen.append(int(kwargs.get("creationflags") or 0))
            return Started()
        with mock.patch.object(children.subprocess, "Popen", popen), \
                mock.patch.object(children, "_into_the_job", lambda process: True):
            children.popen(["x"])
            children.popen(["x"], creationflags=0x00000080)     # HIGH, chosen
        self.assertTrue(seen[0] & children.BELOW_NORMAL)
        self.assertFalse(seen[1] & children.BELOW_NORMAL)

    @unittest.skipUnless(sys.platform == "win32", "Windows' priority classes")
    def test_and_so_does_what_it_starts(self) -> None:
        import psutil
        child = children.popen([sys.executable, "-c", SLEEPER])
        try:
            self.assertEqual(psutil.Process(child.pid).nice(),
                             psutil.BELOW_NORMAL_PRIORITY_CLASS)
        finally:
            children.end_tree(child)


class LeftFromBeforeTests(unittest.TestCase):

    def setUp(self) -> None:
        self.mark = f"pa-left-{os.getpid()}-{time.time_ns()}"
        self.started = []

    def tearDown(self) -> None:
        import psutil
        for process in children.leftovers([self.mark]):
            try:
                process.kill()
            except psutil.Error:
                pass
        for one in self.started:
            try:
                one.kill()
            except OSError:
                pass

    def _orphan(self) -> int:
        """A process carrying the mark whose parent has ended - as one the
        application left when it was killed."""
        import psutil
        launcher = ("import subprocess, sys; p = subprocess.Popen([sys.executable, '-c', "
                    f"{SLEEPER!r}, {self.mark!r}], stdin=subprocess.DEVNULL, "
                    "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); print(p.pid)")
        done = subprocess.run([sys.executable, "-c", launcher], capture_output=True,
                              text=True, timeout=30)
        pid = int(done.stdout.strip())
        self.assertTrue(psutil.pid_exists(pid))
        return pid

    def test_found_and_ended(self) -> None:
        import psutil
        pid = self._orphan()
        found = [one.pid for one in children.leftovers([self.mark])]
        self.assertEqual(found, [pid])
        self.assertEqual(children.end_leftovers([self.mark]), 1)

        def gone():
            try:
                return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
            except psutil.Error:
                return True
        self.assertTrue(_until(gone, 15))

    def test_its_own_children_are_not_left_from_before(self) -> None:
        mine = subprocess.Popen([sys.executable, "-c", SLEEPER, self.mark])
        self.started.append(mine)
        _until(lambda: False, 0.5)
        self.assertEqual(children.leftovers([self.mark]), [])

    def test_no_mark_nothing_found(self) -> None:
        self.assertEqual(children.leftovers(["", "  "]), [])


class _Run:
    def __init__(self, folder, debug=True):
        self.folder = folder
        self.pid = 0
        self.unit = ({"id": "dbg_x:full", "name": "Notes",
                      "debug": {"try": 2, "label": "Full system", "folder": str(folder)}}
                     if debug else {"id": "u1", "name": "Garden planner"})

    def alive(self):
        return True


class TheResourceDebuggerTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        from personalized_agent.api.routes import build_routes
        self.routes = build_routes(self.app)
        from personalized_agent.debugger.resources import ResourceWatch
        self.watch = ResourceWatch(self.app)
        self.started = []

    def tearDown(self) -> None:
        for one in self.started:
            try:
                one.kill()
                one.wait(10)
            except OSError:
                pass
        self.agent.cleanup()

    def test_each_process_under_what_it_is_for(self) -> None:
        way = Path(self.agent.dir) / "bench" / "try_2" / "3_full"
        way.mkdir(parents=True)
        self.app.work._running["r1"] = _Run(way)
        child = subprocess.Popen([sys.executable, "-c", SLEEPER], cwd=str(way))
        self.started.append(child)
        self.watch.sample()
        time.sleep(0.3)
        seen = self.watch.sample()
        rows = {row["pid"]: row for row in seen["processes"]}
        self.assertEqual(rows[os.getpid()]["kind"], "app")
        self.assertEqual((rows[child.pid]["kind"], rows[child.pid]["owner"]),
                         ("debugger", "Notes - try 2, Full system"))
        self.assertGreater(rows[child.pid]["memory"], 0)
        self.assertGreaterEqual(seen["totals"]["debugger"]["memory"], rows[child.pid]["memory"])
        # Both kept - and `now` starts the sampling, which may already have
        # taken one of its own by the time it answers.
        self.assertGreaterEqual(len(self.watch.now()["history"]), 2)

    def test_what_tells_what_it_is_for(self) -> None:
        from personalized_agent.debugger.resources import ResourceWatch
        what = ResourceWatch._what_for
        self.assertEqual(what(5, "python.exe", "python embed_worker.py model", "", 1, {}, []),
                         ("embedding", "Embedding model"))
        self.assertEqual(what(5, "claude.exe", "claude -p", "", 1, {}, []),
                         ("models", "Model calls outside a run"))
        folder = os.path.normcase(os.path.join(tempfile.gettempdir(), "w"))
        self.assertEqual(what(5, "python.exe", f"python {folder}{os.sep}x.py", "", 1, {},
                              [(folder, "work", "Garden planner")]),
                         ("work", "Garden planner"))
        self.assertEqual(what(7, "python.exe", "", "", 1, {7: ("work", "Mine")}, []),
                         ("work", "Mine"))
        self.assertEqual(what(5, "notepad.exe", "", "", 1, {}, []), ("other", "notepad.exe"))

    def test_the_folders_it_keeps_things_in(self) -> None:
        (Path(self.agent.dir) / "bench").mkdir()
        self.routes[("POST", "/api/debugger/experience/projects")](
            _Body({"action": "create", "name": "Notes", "description": "a note",
                   "folder": str(Path(self.agent.dir) / "bench")}))
        places = {kind: (label, str(folder)) for label, kind, folder in self.watch.places()}
        self.assertEqual(places["home"][1], str(self.app.paths.root))
        self.assertEqual(places["debugger"], ("Work debugger: Notes",
                                              str(Path(self.agent.dir) / "bench")))

    def test_a_folder_measured_part_by_part(self) -> None:
        from personalized_agent.debugger.resources import PARTS_SHOWN, _place
        top = Path(self.agent.dir) / "measured"
        for n in range(PARTS_SHOWN + 2):
            part = top / f"part{n}" / "inner"
            part.mkdir(parents=True)
            (part / "data.bin").write_bytes(b"x" * (1000 * (n + 1)))
        (top / "loose.txt").write_bytes(b"y" * 10)
        said = _place("Measured", "home", top, time.time() + 60)
        self.assertEqual(said["files"], PARTS_SHOWN + 3)
        self.assertEqual(said["bytes"], sum(1000 * (n + 1) for n in range(PARTS_SHOWN + 2)) + 10)
        self.assertEqual(said["parts"][0], {"name": f"part{PARTS_SHOWN + 1}",
                                            "bytes": 1000 * (PARTS_SHOWN + 2), "files": 1})
        self.assertTrue(said["parts"][-1]["rest"])
        self.assertEqual(len(said["parts"]), PARTS_SHOWN + 1)

    def test_the_page_asks_and_it_samples_and_measures(self) -> None:
        said = self.routes[("GET", "/api/debugger/resources")](_Body({}))
        self.assertEqual([one["id"] for one in said["kinds"]],
                         ["app", "embedding", "work", "debugger", "models", "other"])
        watch = self.app.resource_watch
        self.assertTrue(_until(lambda: bool(watch.now()["latest"])
                               and bool(watch.now()["disk"]["measured_at"]), 60))
        labels = [place["label"] for place in watch.now()["disk"]["places"]]
        self.assertIn("The application's folder", labels)

    def test_the_tab_is_a_debuggers(self) -> None:
        web = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
        self.assertIn('data-tab="resourcedebug"', (web / "index.html").read_text(encoding="utf-8"))
        script = (web / "app.js").read_text(encoding="utf-8")
        self.assertIn('"languagedebug", "resourcedebug", "lessons"]', script)


class _Body:
    def __init__(self, body):
        self.body = body

    def get(self, key, default=None):
        return self.body.get(key, default)

    def require(self, key):
        return self.body[key]


if __name__ == "__main__":
    unittest.main()
