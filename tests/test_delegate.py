"""Delegate work, and how every piece of work starts: context collected, a
plan the user checks, then the run - each run in a process of its own,
within what the computer has free - ending with a README of what was made."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from support import TemporaryAgent

from personalized_agent.agent.work import child, governor, handover
from personalized_agent.agent.work.delegate import (COLLECTING, READY, STARTED, WRITING,
                                                    instruction_of, plan_of)
from personalized_agent.models.base import ModelResponse


class _Models:
    def __init__(self, plan):
        self.plan, self.asked = plan, []

    def invoke(self, request, role=""):
        self.asked.append(request)
        return ModelResponse(text=json.dumps(self.plan))


PLAN = {"title": "Summarise the meeting", "understanding": "A one-page summary.",
        "goal": "One page with each decision.", "steps": ["Read the notes", "Write it"],
        "deliverables": ["summary.md"], "out_of_scope": ["Sending it"], "questions": []}


class DraftsTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.folder = Path(self.agent.dir) / "meeting"
        self.folder.mkdir()
        (self.folder / "README.md").write_text("Notes of the team meeting.", encoding="utf-8")
        (self.folder / "notes.md").write_text("Decided: ship on Friday.", encoding="utf-8")

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_context_then_a_plan_in_the_users_language(self) -> None:
        self.app.models = _Models(PLAN)
        draft = self.app.drafts.create(text="Summarise this", folders=[str(self.folder)],
                                       window={"title": "Meeting - Notes"}, collect=False)
        self.assertEqual(draft["status"], COLLECTING)
        said = self.app.drafts.collect_and_plan(draft["id"])
        self.assertEqual(said["status"], READY)
        folder = said["context"]["folders"][0]
        self.assertIn("notes.md", folder["entries"])
        self.assertIn("team meeting", folder["readme"])
        self.assertEqual(said["plan"]["title"], "Summarise the meeting")
        self.assertTrue(any("README" in one for one in said["plan"]["deliverables"]),
                        "always a README of what was made")
        # Two calls: what was handed over as English search words, then the
        # plan - in the user's language - from what was found.
        words, asked = self.app.models.asked
        self.assertEqual(words.language.rules()[0]["rule"], "internal")
        self.assertEqual(asked.task, "work_draft")
        self.assertEqual(asked.language.rules()[0]["rule"], "display")
        self.assertIn("folders they gave", asked.messages[0]["content"])
        self.assertIn("query", said["context"])

    def test_nothing_runs_before_start_and_the_users_plan_is_kept(self) -> None:
        self.app.models = _Models(PLAN)
        draft = self.app.drafts.create(text="Summarise this", folders=[str(self.folder)],
                                       collect=False)
        self.app.drafts.collect_and_plan(draft["id"])
        self.assertEqual(self.app.db.query("SELECT id FROM work_runs"), [], "not before Start")
        started = []
        self.app.work.start = lambda unit, instruction, **kw: (
            started.append((unit, instruction)) or {"run": "run_x", "folder": "f"})
        edited = dict(PLAN, title="The Friday decision, one page")
        said = self.app.drafts.start(draft["id"], plan=edited)
        self.assertEqual(said["run"], "run_x")
        unit, instruction = started[0]
        self.assertIn("The Friday decision, one page", instruction)
        self.assertIn("WHAT THE USER HANDED OVER", instruction)
        kept = self.app.drafts.get(draft["id"])
        self.assertEqual((kept["status"], kept["plan_by"]), (STARTED, "user"))
        # Handed over: a project of its own, under Delegated work, with the folder.
        units = self.app.state_heartbeat.experience.work_units()
        mine = [one for one in units if one["id"] == said["unit"]]
        self.assertTrue(mine and mine[0]["delegated"])
        self.assertIn(str(self.folder), self.app.work.folders(said["unit"]))

    def test_given_no_folder_it_writes_in_agent_work_under_its_name(self) -> None:
        self.app.models = _Models(PLAN)
        self.app.work.start = lambda unit, instruction, **kw: {"run": "run_x"}
        workspace = Path(self.app.work.app_policy.workspace_root) / "agent_work"
        made = []
        for _ in range(2):
            draft = self.app.drafts.create(text="Summarise this", collect=False)
            self.app.drafts.collect_and_plan(draft["id"])
            said = self.app.drafts.start(draft["id"], folders=[])
            unit = self.app.state_heartbeat.experience.unit(said["unit"])
            folder = self.app.work.folder_of(unit)
            folder.mkdir(parents=True, exist_ok=True)
            made.append(folder)
        self.assertEqual(made[0], workspace / "Summarise the meeting")
        self.assertEqual(made[1].parent, workspace)
        self.assertTrue(made[1].name.startswith("Summarise the meeting 20"),
                        "a second copy told apart by when")

    def test_a_file_handed_over_brings_its_folder(self) -> None:
        draft = self.app.drafts.create(files=[str(self.folder / "notes.md")], collect=False)
        self.assertIn(str(self.folder), draft["folders"])

    def test_every_delegate_work_waits_for_the_instruction_first(self) -> None:
        """Handed over from anywhere: kept, and nothing collected until the
        user writes their instruction and presses Create."""
        self.app.models = _Models(PLAN)
        collected = []
        self.app.drafts._collect_apart = lambda draft_id: collected.append(draft_id)
        draft = self.app.drafts.create(folders=[str(self.folder)], source="explorer",
                                       write_first=True)
        self.assertEqual(draft["status"], WRITING)
        self.assertEqual(collected, [], "nothing collected before Create")
        self.assertIn("not drafted yet", self.app.drafts.start(draft["id"])["error"])
        said = self.app.drafts.compose(draft["id"], "Only the decisions, as a table")
        self.assertEqual(said["draft"]["status"], COLLECTING)
        self.assertEqual(collected, [draft["id"]])
        self.assertIn("already", self.app.drafts.compose(draft["id"], "again")["error"])
        planned = self.app.drafts.collect_and_plan(draft["id"])
        self.assertEqual(planned["status"], READY)
        # The plan was drafted from the instruction, and the run is told it.
        self.assertIn("Only the decisions, as a table",
                      self.app.models.asked[-1].messages[0]["content"])
        told = instruction_of(planned, plan_of(PLAN))
        self.assertIn("THE USER'S INSTRUCTION:\nOnly the decisions, as a table", told)

    def test_the_instruction_is_optional_but_something_must_be_said(self) -> None:
        self.app.drafts._collect_apart = lambda draft_id: None
        handed = self.app.drafts.create(text="Book the room", source="todo", write_first=True)
        self.assertNotIn("error", self.app.drafts.compose(handed["id"], ""))
        empty = self.app.drafts.create(source="menu", write_first=True)
        self.assertIn("say what you want", self.app.drafts.compose(empty["id"], "  ")["error"])
        self.assertNotIn("error", self.app.drafts.compose(empty["id"], "Tidy my notes"))

    def test_cancelled_never_runs(self) -> None:
        draft = self.app.drafts.create(text="x", collect=False)
        self.assertTrue(self.app.drafts.cancel(draft["id"]))
        self.assertEqual(self.app.drafts.get(draft["id"])["status"], "cancelled")

    def test_the_routes(self) -> None:
        from personalized_agent.api.routes import build_routes

        class _Ask(dict):
            def get(self, key, default=None):
                return super().get(key, default)

            def require(self, key):
                return self[key]

            def int_param(self, key, default=0):
                return int(self.get(key, default))

        self.app.drafts._collect_apart = lambda draft_id: None
        routes = build_routes(self.app)
        made = routes[("POST", "/api/work/drafts")](_Ask(text="Do it", folders=[str(self.folder)]))
        ident = made["draft"]["id"]
        # From the page: waiting for the instruction, then Create.
        self.assertEqual(made["draft"]["status"], WRITING)
        composed = routes[("POST", "/api/work/drafts/compose")](_Ask(id=ident,
                                                                     instruction="As a table"))
        self.assertEqual(composed["draft"]["status"], COLLECTING)
        self.assertEqual(composed["draft"]["options"]["instruction"], "As a table")
        self.assertEqual(routes[("GET", "/api/work/drafts")](_Ask(id=ident))["draft"]["id"], ident)
        self.assertTrue(routes[("POST", "/api/work/drafts/cancel")](_Ask(id=ident))["ok"])
        icon = routes[("GET", "/api/avatar/icon")](_Ask())
        self.assertTrue(icon["png"].startswith("data:image/png;base64,"))


class APlanTests(unittest.TestCase):

    def test_kept_in_shape(self) -> None:
        said = plan_of({"steps": "one\n- two", "title": ""}, fallback="Write the summary now")
        self.assertEqual(said["steps"], ["one", "two"])
        self.assertEqual(said["title"], "Write the summary now")
        self.assertIn("README.md", said["deliverables"][-1])

    def test_the_run_is_told_it_word_for_word(self) -> None:
        told = instruction_of({"text": "Summarise", "window": {"title": "Notes"}},
                              plan_of(PLAN))
        for words in ("Summarise", "IT CAME FROM: Notes", "Read the notes", "Sending it"):
            self.assertIn(words, told)


class HandedOverFromExplorerTests(unittest.TestCase):

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_items_chosen_together_are_one_piece_of_work(self) -> None:
        a, b = self.dir / "a", self.dir / "b.txt"
        a.mkdir()
        b.write_text("x", encoding="utf-8")
        first, mine = handover.hand_over(self.dir, str(a))
        second, also_mine = handover.hand_over(self.dir, str(b))
        self.assertTrue(mine)
        self.assertFalse(also_mine)
        self.assertEqual(first, second)
        handover.gathered(self.dir, first)
        said = handover.take(self.dir, first)
        self.assertEqual((said["folders"], said["files"]), ([str(a.resolve())], [str(b.resolve())]))
        self.assertIsNone(handover.take(self.dir, first), "taken once")
        self.assertIsNone(handover.take(self.dir, "../x"))

    def test_the_menu_entries_in_the_users_registry(self) -> None:
        from personalized_agent.platform_layer import explorer_menu
        written = {}

        class _Key:
            def __init__(self, path):
                self.path = path

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        fake = SimpleNamespace(
            HKEY_CURRENT_USER="HKCU", REG_SZ=1,
            CreateKey=lambda root, path: (written.setdefault(path, {}), _Key(path))[1],
            SetValueEx=lambda key, name, _r, _t, value: written[key.path].__setitem__(name, value),
            DeleteKey=lambda root, path: written.pop(path),
            OpenKey=lambda root, path: _Key(path) if path in written else (_ for _ in ()).throw(OSError()))
        with mock.patch.dict("sys.modules", {"winreg": fake}), \
                mock.patch.object(explorer_menu, "available", lambda: True):
            icon = self.dir / "face.ico"
            icon.write_bytes(b"ico")
            explorer_menu.install(["C:\\App\\PersonalizedAgent.exe"], icon, "zh")
            folder_key = r"Software\Classes\Directory\shell\PersonalizedAgent.Delegate"
            self.assertEqual(written[folder_key][""], "委托工作")
            self.assertEqual(written[folder_key]["Icon"], str(icon))
            self.assertEqual(written[folder_key + r"\command"][""],
                             'C:\\App\\PersonalizedAgent.exe --delegate "%1"')
            self.assertIn('"%V"', written[r"Software\Classes\Directory\Background\shell"
                                          r"\PersonalizedAgent.Delegate\command"][""])
            # The desktop too: right-clicked, it passes the desktop folder.
            self.assertIn('"%V"', written[r"Software\Classes\DesktopBackground\Shell"
                                          r"\PersonalizedAgent.Delegate\command"][""])
            self.assertTrue(all(key.startswith("Software\\Classes") for key in written))
            explorer_menu.remove()
            self.assertEqual(written, {})


class AChildProcessTests(unittest.TestCase):
    """A run in a process of its own: told through its control file, shown
    from its snapshot."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_what_it_is_told_it_does(self) -> None:
        done = []

        class _Run:
            stopping = stop_now = False

            def pause(self, why=""):
                done.append(("pause", why))

            def resume(self, why=""):
                done.append(("resume", why))

            def release(self, now=False):
                done.append(("stop", now))

            def answer(self, request, allow):
                done.append(("answer", request, allow))

        held = child.ChildRun(SimpleNamespace(), self.dir, None, {"run": "run_1"}, {"id": "u"})
        held.alive = lambda: True
        held.pause(why="the computer is short of memory")
        held.answer("req_1", True)
        held.release(now=True)
        run, seen = _Run(), set()
        for order in child._orders(self.dir, seen):
            child._obey(run, order)
        self.assertEqual(done, [("pause", "the computer is short of memory"),
                                ("answer", "req_1", True), ("stop", True)])
        self.assertTrue(run.stop_now)
        self.assertEqual(child._orders(self.dir, seen), [], "each done once")

    def test_the_command_starts_this_program_as_a_child(self) -> None:
        said = child.command(self.dir / "spec.json")
        self.assertEqual(said[-2], child.CHILD_FLAG)
        self.assertIn("personalized_agent", " ".join(said))

    def test_the_child_finds_the_program_from_any_folder(self) -> None:
        """An agent started by a double click on run.py, or at sign-in, is
        in System32: the child is started there and still finds the package
        (with `-m` it did not, and every run failed on "No module named")."""
        import subprocess
        import sys
        said = child.command(self.dir / "spec.json")
        self.assertEqual(said[1], "-c")
        boot = said[2].replace("from personalized_agent.__main__ import main; sys.exit(main())",
                               "import personalized_agent; print(personalized_agent.__file__)")
        self.assertNotEqual(boot, said[2], "the child starts the program's main")
        elsewhere = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, elsewhere, True)
        ran = subprocess.run([sys.executable, "-c", boot, said[3]], cwd=elsewhere,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertEqual(Path(ran.stdout.strip()).resolve(),
                         Path(child.__file__).resolve().parents[2] / "__init__.py")

    def test_a_child_that_never_says_it_started_is_not_left_going(self) -> None:
        """Waited for while it lives; at the end of the wait, ended - never
        a run going that the agent does not hold."""
        import sys
        asleep = [sys.executable, "-c", "import time; time.sleep(120)"]
        spawned = []
        with mock.patch.object(child, "command", lambda _spec: asleep), \
                mock.patch.object(child, "STARTING_SECONDS", 1.5):
            said = child._start_child(SimpleNamespace(), self.dir, {"id": "u"}, None, spawned)
        self.assertEqual(said, {"error": "the run's process did not start in time"})
        self.assertIsNotNone(spawned[0].wait(timeout=30), "ended, not left going")


class TheGovernorTests(unittest.TestCase):

    def _governor(self, cpu, free_gb, total_gb=16):
        work = SimpleNamespace(_running={}, start_waiting=lambda: [])
        with mock.patch.object(governor, "_psutil", lambda: None):
            said = governor.Governor(work)
        said.samples.extend([cpu] * 3)
        said.free_gb, said.free_share = free_gb, free_gb / total_gb
        return said

    def test_room_by_what_is_free(self) -> None:
        calm = self._governor(20, 12)
        self.assertEqual(calm.room(0), (True, ""))
        with mock.patch("os.cpu_count", lambda: 16):
            self.assertTrue(calm.room(2)[0])
            self.assertFalse(calm.room(4)[0], "four at most")
        busy = self._governor(95, 12)
        self.assertTrue(busy.room(0)[0], "one may always go")
        self.assertEqual(busy.room(1), (False, "the computer is busy"))
        short = self._governor(10, 0.5)
        self.assertEqual(short.room(0), (False, "the computer is short of memory"))

    def test_short_of_memory_the_newest_run_waits(self) -> None:
        gov = self._governor(30, 0.6, total_gb=16)
        paused = []
        old = SimpleNamespace(id="old", pid=1, started_at=1, alive=lambda: True,
                              pause=lambda why="": paused.append(("old", why)) or True)
        new = SimpleNamespace(id="new", pid=2, started_at=2, alive=lambda: True,
                              pause=lambda why="": paused.append(("new", why)) or True,
                              resume=lambda why="": paused.append(("back", why)))
        gov.work._running = {"old": old, "new": new}
        gov.look = lambda: gov.seen()
        gov.steer()
        self.assertEqual(paused, [("new", "the computer is short of memory")])
        gov.free_share = 0.5
        gov.steer()
        self.assertEqual(paused[-1], ("back", "memory is free again"))

    def test_a_run_asked_for_without_room_waits_and_then_starts(self) -> None:
        agent = TemporaryAgent()
        try:
            work = agent.app.work
            work.in_subprocess = True
            work.governor = self._governor(10, 0.5)
            unit = {"id": "work_1", "name": "x"}
            work.has_history = lambda unit: True
            said = work.start(unit, "do it")
            self.assertTrue(said["waiting"])
            self.assertEqual(said["why"], "the computer is short of memory")
            launched = []
            work._launch = lambda kind, unit, args: launched.append(kind) or {"run": "r"}
            work.governor.free_gb = 8
            work.start_waiting()
            self.assertEqual(launched, ["start"])
            self.assertEqual(work.waiting, [])
        finally:
            agent.cleanup()


class TheReadmeTests(unittest.TestCase):

    def test_every_run_that_made_something_ends_with_one(self) -> None:
        from personalized_agent.agent.work import runner
        self.assertIn("HOW TO CHECK IT", runner.README_PROMPT)
        self.assertIn("HOW TO USE IT", runner.README_PROMPT)
        written = {}
        run = SimpleNamespace(
            _going=lambda work=True: True, _stage=lambda *a: None, _brief=lambda: "BRIEF",
            _system=lambda: "S", tools=None, id="run_1", final={},
            _ask=lambda *a, **k: {"readme": "# What was made\nsummary.md"},
            runtime=SimpleNamespace(read_text=lambda name: "# The user's own README"),
            _write=lambda name, text: written.__setitem__(name, text),
            _event=lambda *a, **k: None)
        name = runner.Run._readme(run, True)
        self.assertEqual(name, "README-deliverable.md", "the user's own README is kept")
        self.assertTrue(written[name].startswith(runner.README_MARK))
        run.runtime = SimpleNamespace(read_text=lambda name: (_ for _ in ()).throw(OSError()))
        self.assertEqual(runner.Run._readme(run, True), "README.md")


class TheSelectionTests(unittest.TestCase):

    def test_never_a_password_field_nor_the_agents_own(self) -> None:
        from personalized_agent.ui import selection

        def element(password=False, pid=1, text="the selected words"):
            ranges = SimpleNamespace(Length=1, GetElement=lambda i: SimpleNamespace(
                GetText=lambda most: text))
            pattern = SimpleNamespace(QueryInterface=lambda iface: SimpleNamespace(
                GetSelection=lambda: ranges))
            return SimpleNamespace(CurrentIsPassword=password, CurrentProcessId=pid,
                                   GetCurrentPattern=lambda pid_: pattern)

        uia = SimpleNamespace(IUIAutomationTextPattern=object)
        with mock.patch.object(selection, "_window_in_front",
                               lambda: {"title": "Notes", "app": "notepad.exe"}):
            said = selection.SelectionWatch._selected(
                SimpleNamespace(GetFocusedElement=lambda: element()), uia)
            self.assertEqual(said["text"], "the selected words")
            self.assertEqual(said["title"], "Notes")
            self.assertIsNone(selection.SelectionWatch._selected(
                SimpleNamespace(GetFocusedElement=lambda: element(password=True)), uia))
            self.assertIsNone(selection.SelectionWatch._selected(
                SimpleNamespace(GetFocusedElement=lambda: element(pid=os.getpid())), uia))


if __name__ == "__main__":
    unittest.main()
