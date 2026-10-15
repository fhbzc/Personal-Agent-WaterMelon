"""The application's own code, kept from the Experience debugger's ways.

The user's decision: a way of the Experience debugger - which is measured -
may not READ how the system that measures it works; real work may, as it
always could. So for a debugger run the code is refused by the work tools,
left out of what they list, refused by the guard inside code it runs, and
kept by the operating system around that code: on Windows the code is
sealed from every container while any debugger run is alive, and opened
again when the last one ends (runtime/isolation.CodeSeal).

And every child, debugger's or not, starts without reading the code: the
guard it loads is a copy outside it (runtime/sandbox.ensure_guard).
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

from support import TemporaryAgent
from test_experience_debugger import _DebuggerBase, _Models, _until

from personalized_agent.common.paths import app_code_root
from personalized_agent.runtime import filesystem as fs
from personalized_agent.runtime import isolation
from personalized_agent.runtime.isolation import NETWORK_NONE, CodeSeal
from personalized_agent.runtime.permissions import SecurityViolation, WorkspacePolicy
from personalized_agent.runtime.sandbox import SandboxRuntime, ensure_guard

RUNTIME_DIR = Path(isolation.__file__).resolve().parent


def _appcontainer() -> bool:
    return isolation.isolation_status().get("backend") == "appcontainer"


class TheCodeRootTests(unittest.TestCase):

    def test_from_source_it_is_the_checkout(self) -> None:
        root = app_code_root()
        self.assertTrue((root / "personalized_agent" / "runtime" / "guard.py").is_file())
        self.assertTrue((root / "tests").is_dir())

    def test_the_real_checkout_is_refused_to_a_policy_that_keeps_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            policy = WorkspacePolicy.create(Path(tmp) / "ws",
                                            readable_paths=[Path.home(), app_code_root()],
                                            protected_paths=[Path(tmp) / "internal"])
            readme = app_code_root() / "personalized_agent" / "runtime" / "guard.py"
            # Real work, the chat: read as before.
            self.assertEqual(fs.validate_read_path(readme, policy), readme)
            policy.code_root = app_code_root()
            with self.assertRaises(SecurityViolation) as caught:
                fs.validate_read_path(readme, policy)
            self.assertEqual(caught.exception.boundary, "application_code")
            self.assertIn("this application's own code", str(caught.exception))
            # Reading alone: the write rules decide writing, as before.
            with self.assertRaises(SecurityViolation) as caught:
                fs.validate_write_path(readme, policy)
            self.assertNotEqual(caught.exception.boundary, "application_code")


class _WithRuns(_DebuggerBase):
    """A debugger project, and the stand-in for the code the test agent
    has (tests/support.py)."""

    def setUp(self) -> None:
        super().setUp()
        self.code = self.agent.code_root
        self.judge = self.code / "pkg" / "judge.py"

    def tearDown(self) -> None:
        super().tearDown()
        CodeSeal.wait(self.code, 120)
        CodeSeal.reset()

    def _debug_tools(self, variant="single"):
        from personalized_agent.agent.work.debug_projects import DebugProjects
        from personalized_agent.agent.work.tools import WorkTools
        project = self._project()
        unit = DebugProjects(self.app.work).unit(project["id"], variant)
        runtime = self.app.work._runtime(unit, self.app.work.folder_of(unit))
        return runtime, WorkTools(runtime)

    def _real_tools(self):
        from personalized_agent.agent.work.tools import WorkTools
        unit = {"id": "unit_real", "name": "Real work", "kind": "task"}
        runtime = self.app.work._runtime(unit, self.app.work.folder_of(unit))
        return runtime, WorkTools(runtime)


class TheToolsTests(_WithRuns):

    def test_a_debugger_run_cannot_read_list_or_find_the_code(self) -> None:
        runtime, tools = self._debug_tools()
        self.assertEqual(Path(runtime.policy.code_root), self.code.resolve())
        said = tools.call("read_file", {"path": str(self.judge)})
        self.assertEqual(said.get("error"), "security_violation", said)
        self.assertIn("this application's own code", said["message"])
        said = tools.call("list_dir", {"path": str(self.code)})
        self.assertEqual(said.get("error"), "security_violation", said)
        listed = [one["name"] for one in tools.call(
            "list_dir", {"path": str(self.agent.dir)})["entries"]]
        self.assertNotIn(self.code.name, listed)
        self.assertIn(self.chosen.name, listed)      # the rest of the folder is there
        found = tools.call("find_files", {"root": str(self.agent.dir), "pattern": "**/*.py"})
        self.assertFalse(any(Path(one) == self.judge for one in found["files"]), found)
        said = tools.call("find_files", {"root": str(self.code), "pattern": "**/*"})
        self.assertEqual(said.get("error"), "security_violation", said)

    def test_real_work_reads_it_as_before(self) -> None:
        runtime, tools = self._real_tools()
        self.assertIsNone(runtime.policy.code_root)
        said = tools.call("read_file", {"path": str(self.judge)})
        self.assertIn("HOW_IT_JUDGES", said.get("text", ""), said)
        listed = [one["name"] for one in tools.call(
            "list_dir", {"path": str(self.agent.dir)})["entries"]]
        self.assertIn(self.code.name, listed)

    def test_the_chat_reads_it_as_before(self) -> None:
        self.assertIsNone(self.app.policy.code_root)
        self.assertIn("HOW_IT_JUDGES", self.app.runtime.read_text(self.judge))

    def test_only_a_debugger_childs_guard_is_told(self) -> None:
        runtime, _tools = self._debug_tools()
        env = runtime._child_environment(None)
        self.assertEqual(env.get("AGENT_CODE_ROOT"), str(self.code.resolve()))
        real, _tools = self._real_tools()
        self.assertNotIn("AGENT_CODE_ROOT", real._child_environment(None))

    def test_code_a_debugger_run_runs_cannot_open_it(self) -> None:
        _runtime, tools = self._debug_tools()
        said = tools.call("run_python", {"code": (
            "import os\n"
            f"for read in (lambda: open(r'{self.judge}').read(),\n"
            f"             lambda: os.listdir(r'{self.code}')):\n"
            "    try:\n"
            "        print('READ', read())\n"
            "    except Exception as exc:\n"
            "        print('refused:', type(exc).__name__, exc)\n")})
        out = str(said.get("stdout"))
        self.assertNotIn("READ", out, said)
        self.assertIn("this application's own code", out, said)
        # Real work's code reads it.
        _runtime, tools = self._real_tools()
        said = tools.call("run_python", {"code": f"print(open(r'{self.judge}').read())"})
        self.assertIn("HOW_IT_JUDGES", str(said.get("stdout")), said)

    def test_the_debugger_refuses_a_folder_in_the_code(self) -> None:
        from personalized_agent.api.errors import ApiError
        inside = self.code / "bench"
        inside.mkdir()
        for folder in (self.code, inside):
            with self.assertRaises(ApiError, msg=folder) as caught:
                self._project(folder=str(folder))
            self.assertIn("own code", str(caught.exception))

    def test_the_ways_are_still_sealed_from_each_other(self) -> None:
        runtime, tools = self._debug_tools("single")
        (self.chosen / "B" / "theirs.txt").write_text("theirs")
        said = tools.call("read_file", {"path": str(self.chosen / "B" / "theirs.txt")})
        self.assertIn("another benchmark variant's folder", said.get("message", ""), said)
        self.assertEqual(sorted(Path(p).name for p in runtime.policy.sealed_paths), ["B", "C"])


class ARunHoldsTheSealTests(_WithRuns):

    def test_a_debugger_run_holds_it_while_alive_and_real_work_never(self) -> None:
        applied = []
        with mock.patch.object(CodeSeal, "_apply",
                               side_effect=lambda root, sealed: applied.append(sealed)):
            models = _Models(gate=True)
            self.app.work.models = models
            project = self._project()
            self._start(project, "single")
            self.assertTrue(_until(lambda: models.waiting >= 1, 30))
            self.assertEqual(CodeSeal.held(self.code), 1)
            self.assertTrue(CodeSeal.wait(self.code, 30))
            self.assertEqual(applied, [True])
            models.gate.set()
            self._finished(project, "single")
            self.assertTrue(_until(lambda: CodeSeal.held(self.code) == 0, 30))
            self.assertTrue(CodeSeal.wait(self.code, 30))
            self.assertEqual(applied, [True, False])

    def test_real_work_never_holds_it(self) -> None:
        unit = {"id": "unit_real", "name": "Real work", "kind": "task"}
        runtime = self.app.work._runtime(unit, self.app.work.folder_of(unit))
        run = mock.Mock(debug=False, runtime=runtime)
        self.assertIsNone(self.app.work.hold_code(run))
        self.assertEqual(CodeSeal.held(self.code), 0)

    def test_the_application_starts_it_open(self) -> None:
        with mock.patch.object(CodeSeal, "settle") as settle:
            self.app.work.settle_code()
        settle.assert_called_once_with(self.code)


class CountedTests(unittest.TestCase):
    """What is on disk follows the count, not the order of the calls."""

    def setUp(self) -> None:
        CodeSeal.reset()
        self.root = Path(tempfile.mkdtemp(prefix="pa-seal-")).resolve()
        self.applied = []
        self.lock = threading.Lock()

        def apply(root, sealed):
            time.sleep(0.01)                       # long enough to overlap
            with self.lock:
                self.applied.append(sealed)
            return True

        self.patch = mock.patch.object(CodeSeal, "_apply", side_effect=apply)
        self.patch.start()

    def tearDown(self) -> None:
        CodeSeal.wait(self.root, 30)
        self.patch.stop()
        CodeSeal.reset()
        os.rmdir(self.root)

    def test_held_twice_released_once_stays_sealed(self) -> None:
        CodeSeal.hold(self.root)
        CodeSeal.hold(self.root)
        self.assertTrue(CodeSeal.wait(self.root, 10))
        CodeSeal.release(self.root)
        self.assertTrue(CodeSeal.wait(self.root, 10))
        self.assertEqual(self.applied, [True])
        self.assertEqual(CodeSeal.sealed_roots(), [self.root])
        CodeSeal.release(self.root)
        self.assertTrue(CodeSeal.wait(self.root, 10))
        self.assertEqual(self.applied, [True, False])
        self.assertEqual(CodeSeal.sealed_roots(), [])
        # Never below none.
        CodeSeal.release(self.root)
        self.assertEqual(CodeSeal.held(self.root), 0)

    def test_starting_settles_it_open(self) -> None:
        CodeSeal.settle(self.root)
        self.assertTrue(CodeSeal.wait(self.root, 10))
        self.assertEqual(self.applied, [False])

    def test_many_runs_at_once_end_as_the_count_says(self) -> None:
        def one_run():
            CodeSeal.hold(self.root)
            time.sleep(0.005)
            CodeSeal.release(self.root)

        threads = [threading.Thread(target=one_run) for _ in range(40)]
        for thread in threads:
            thread.start()
        CodeSeal.hold(self.root)                  # one still alive at the end
        for thread in threads:
            thread.join()
        self.assertTrue(CodeSeal.wait(self.root, 30))
        self.assertEqual(CodeSeal.held(self.root), 1)
        self.assertTrue(self.applied and self.applied[-1] is True, self.applied)
        # Each change undoes the one before: never sealed twice in a row.
        self.assertTrue(all(a != b for a, b in zip(self.applied, self.applied[1:])),
                        self.applied)
        CodeSeal.release(self.root)
        self.assertTrue(CodeSeal.wait(self.root, 30))
        self.assertIs(self.applied[-1], False)


class TheGuardIsLoadedFromOutsideTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_a_byte_for_byte_copy_outside_the_code(self) -> None:
        folder = self.app.paths.guard_dir
        self.assertEqual(ensure_guard(folder), folder.resolve())
        copy = folder / "guard.py"
        self.assertEqual(copy.read_bytes(), (RUNTIME_DIR / "guard.py").read_bytes())
        self.assertFalse(fs.is_inside(folder.resolve(), app_code_root()))
        # Inside the protected state: no run may write it.
        self.assertTrue(fs.is_inside(folder.resolve(),
                                     Path(self.app.policy.protected_paths[0]).resolve()))
        # Changed by anything else: put back before the next child.
        copy.write_text("def install(): pass\n")
        ensure_guard(folder)
        self.assertEqual(copy.read_bytes(), (RUNTIME_DIR / "guard.py").read_bytes())
        # The Landlock launcher goes with it on Linux alone.
        self.assertEqual((folder / "isolation.py").exists(), sys.platform.startswith("linux"))

    def test_children_load_it_from_there_and_still_run(self) -> None:
        runtime = SandboxRuntime(self.app.policy, sandbox_enabled=False)
        command = runtime.script_command(Path("x.py"))
        joined = " ".join(command)
        self.assertIn(repr(str(self.app.paths.guard_dir.resolve())), joined)
        self.assertNotIn(repr(str(RUNTIME_DIR))[1:-1], joined)
        self.assertNotIn(RUNTIME_DIR, isolation.code_paths())
        result = runtime.run_python_code("import guard, sys\nprint('ran', guard._installed)\n")
        self.assertTrue(result.ok, result.stderr)
        self.assertIn("ran True", result.stdout)
        self.assertFalse((self.app.paths.guard_dir / "__pycache__").exists())

    def test_under_the_operating_system_too(self) -> None:
        if not isolation.isolation_status().get("isolated"):
            self.skipTest("no operating system isolation here")
        runtime = SandboxRuntime(self.app.policy, platform=self.app.platform,
                                 sandbox_enabled=True)
        result = runtime.run_python_code("print('ran')\n", timeout=120)
        self.assertTrue(result.ok, result.stderr)
        self.assertIn("ran", result.stdout)


class TheBackendsTests(unittest.TestCase):

    def test_seatbelt_denies_reading_the_code_after_every_opening(self) -> None:
        work = PurePosixPath("/Users/me/PA/workspace/agent_work/p-1")
        code = PurePosixPath("/Users/me/src/personalized_agent_release")
        profile = isolation.seatbelt_profile([work], [], NETWORK_NONE,
                                             home=Path(tempfile.gettempdir()), code=code)
        lines = profile.splitlines()
        deny = lines.index(f'(deny file-read-data (subpath "{code}"))')
        last_open = max(i for i, line in enumerate(lines)
                        if line.startswith("(allow file-read*"))
        self.assertGreater(deny, last_open)
        self.assertFalse(any("deny file-write" in line and str(code) in line
                             for line in lines))       # writing is not touched
        self.assertNotIn(str(code), isolation.seatbelt_profile([work], [], NETWORK_NONE,
                                                               home=Path(tempfile.gettempdir())))

    def test_bubblewrap_covers_the_code_unless_a_written_folder_holds_it(self) -> None:
        work = PurePosixPath("/home/me/PA/workspace/agent_work/p-1")
        code = PurePosixPath("/home/me/src/checkout")
        home = Path(tempfile.gettempdir())
        args = " ".join(isolation.bubblewrap_command(["true"], work, [work], [], [],
                                                      NETWORK_NONE, home=home, code=code))
        self.assertIn(f"--tmpfs {code}", args)
        self.assertLess(args.index(f"--tmpfs {code}"), args.index(f"--bind {work}"))
        above = PurePosixPath("/home/me/src")
        args = " ".join(isolation.bubblewrap_command(["true"], above, [above], [], [],
                                                      NETWORK_NONE, home=home, code=code))
        self.assertNotIn(f"--tmpfs {code}", args)

    def test_the_guard_home_is_reopened_inside_the_protected_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            internal = Path(tmp).resolve() / "internal"
            home = ensure_guard(internal / "guard")
            self.assertTrue(isolation._reopened(home, [internal]))
            self.assertFalse(isolation._reopened(internal / "settings", [internal]))
            self.assertIn(home, isolation.code_paths())
            args = isolation.bubblewrap_command(["true"], Path(tmp), [Path(tmp)], [],
                                                [internal], NETWORK_NONE,
                                                home=Path(tmp))
            joined = " ".join(args)
            self.assertIn(f"--ro-bind {home} {home}", joined)

    def test_the_landlock_launcher_runs_from_the_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = isolation.note_guard_home(Path(tmp))
            (home / "isolation.py").write_bytes(Path(isolation.__file__).read_bytes())
            try:
                args = isolation.landlock_command(["python3", "x.py"], [Path("/w")], [443])
                self.assertIn(str(home / "isolation.py"), args)
            finally:
                os.remove(home / "isolation.py")


@unittest.skipUnless(sys.platform.startswith("win"), "Windows' AppContainer")
class TheOperatingSystemTests(unittest.TestCase):
    """Past every guard - a bare interpreter in the AppContainer - the code
    is unreadable while a debugger run holds it, readable again after, its
    ACL as it was; a folder granted above it is still written."""

    def setUp(self) -> None:
        if not _appcontainer():
            self.skipTest("no AppContainer isolation here")
        CodeSeal.reset()
        self.base = Path(tempfile.mkdtemp(prefix="pa-code-")).resolve()
        self.code = self.base / "code"
        (self.code / "pkg").mkdir(parents=True)
        (self.code / "pkg" / "judge.py").write_text("HOW_IT_JUDGES = 1\n")
        self.work = self.base / "work"
        self.work.mkdir()
        self.api = isolation._AppContainer.api()

    def tearDown(self) -> None:
        CodeSeal.wait(self.code, 120)
        if CodeSeal.held(self.code):
            for _ in range(CodeSeal.held(self.code)):
                CodeSeal.release(self.code)
            CodeSeal.wait(self.code, 120)
        CodeSeal.reset()
        import shutil
        shutil.rmtree(self.base, ignore_errors=True)

    def _child(self, snippet, writable):
        done = isolation.run_isolated([sys.executable, "-I", "-c", snippet], cwd=self.work,
                                      writable=writable, readable=[self.base], timeout=120)
        return done.returncode, done.stdout + done.stderr

    def _acl(self):
        dacl, descriptor = self.api._dacl(str(self.code))
        try:
            return sorted((kind, flags, access,
                           self.api.ctypes.string_at(at, self.api.GetLengthSid(at)))
                          for kind, flags, access, at in self.api._entries(dacl))
        finally:
            self.api.LocalFree(descriptor)

    def test_sealed_while_held_open_after(self) -> None:
        read = f"print(open({str(self.code / 'pkg' / 'judge.py')!r}).read())"
        write = (f"open({str(self.code / 'pkg' / 'new.txt')!r}, 'w').write('x'); "
                 "print('wrote')")
        code, out = self._child(read, [self.work])
        self.assertEqual(code, 0, out)
        self.assertIn("HOW_IT_JUDGES", out)
        before = self._acl()
        CodeSeal.hold(self.code)
        self.assertTrue(CodeSeal.wait(self.code, 300))
        for writable in ([self.work], [self.base]):
            code, out = self._child(read, writable)
            self.assertNotEqual(code, 0, out)
            self.assertIn("PermissionError", out)
        # A folder granted above it - for the first time while it is sealed
        # - still writes in it: writing is not the seal's to change.
        later = self.base
        code, out = self._child(write, [later])
        self.assertEqual(code, 0, out)
        self.assertTrue((self.code / "pkg" / "new.txt").is_file())
        CodeSeal.release(self.code)
        self.assertTrue(CodeSeal.wait(self.code, 300))
        code, out = self._child(read, [self.work])
        self.assertEqual(code, 0, out)
        self.assertIn("HOW_IT_JUDGES", out)
        # As it was - but for the grant on the folder above, made since,
        # which reaches into it by inheritance as any grant does.
        after = [one for one in self._acl() if one not in before]
        self.assertTrue(all(flags & 0x10 for _kind, flags, _access, _sid in after), after)
        self.assertFalse([one for one in before if one not in self._acl()])
        self.assertFalse(self.api.is_protected(str(self.code)))


if __name__ == "__main__":
    unittest.main()
