"""THE APPLICATION'S SOURCE, READ BUT NEVER CHANGED BY AN AGENT (the user, 2026-11-12: "撤销那个不能读
agent代码的限制吧。只要他不会直接改源代码（只能在自己的directory里改)就行").

A run may read the code again - only the Experience debugger's ways are kept from it, as before - but
no agent writes in it: not the file tools, not the code a run runs, not the operating system around
that code - not even through a folder the user granted that holds it. A folder of the run's own is
its own, wherever it is. A stand-in plays the source here; the real checkout is never written."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

from support import TemporaryAgent

from personalized_agent.runtime import isolation
from personalized_agent.runtime.isolation import NETWORK_NONE


class _WithAStandIn(unittest.TestCase):
    """A granted folder (the Desktop, say) holding a stand-in for the source."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        # Resolved: an 8.3 short name cannot be opened from inside an AppContainer.
        self.granted = Path(tempfile.mkdtemp(prefix="pa-granted-src-")).resolve()
        self.source = self.granted / "watermelon_src"
        (self.source / "pkg").mkdir(parents=True)
        (self.source / "pkg" / "judge.py").write_text("HOW_IT_JUDGES = 1\n", encoding="utf-8")
        (self.granted / "mine.txt").write_text("the user's own", encoding="utf-8")
        for target in ("personalized_agent.common.paths.app_code_root",
                       "personalized_agent.common.paths.code_root_for_children"):
            patcher = mock.patch(target, return_value=self.source)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.app.work.grant("project-src123", str(self.granted))
        self.folder = self.app.work.folder_for("Work beside the source", "project-src123")
        from personalized_agent.agent.work.tools import WorkTools
        from personalized_agent.runtime.sandbox import SandboxRuntime
        self.runtime = SandboxRuntime(
            self.app.work.policy_for(self.folder, self.app.work.folders("project-src123")),
            platform=self.app.platform)
        self.tools = WorkTools(self.runtime)

    def tearDown(self) -> None:
        shutil.rmtree(self.granted, ignore_errors=True)
        self.agent.cleanup()


class TheToolsTests(_WithAStandIn):

    def test_read_but_never_written_nor_deleted(self) -> None:
        said = self.tools.call("read_file", {"path": str(self.source / "pkg" / "judge.py")})
        self.assertIn("HOW_IT_JUDGES", said.get("text", ""), said)
        for call, args in (("write_file", {"path": str(self.source / "pkg" / "judge.py"),
                                           "content": "changed"}),
                           ("write_file", {"path": str(self.source / "new.py"), "content": "x"}),
                           ("delete_file", {"path": str(self.source / "pkg" / "judge.py")})):
            said = self.tools.call(call, args)
            self.assertEqual(said.get("error"), "security_violation", (call, said))
            self.assertIn("own source code", said["message"])
        self.assertEqual((self.source / "pkg" / "judge.py").read_text(encoding="utf-8"),
                         "HOW_IT_JUDGES = 1\n")
        self.assertFalse((self.source / "new.py").exists())
        # The rest of the granted folder, and its own, as before.
        said = self.tools.call("write_file", {"path": str(self.granted / "mine.txt"),
                                              "content": "changed"})
        self.assertNotIn("error", said, said)
        said = self.tools.call("write_file", {"path": "notes.md", "content": "its own"})
        self.assertNotIn("error", said, said)

    def test_a_folder_of_its_own_inside_the_source_is_its_own(self) -> None:
        from personalized_agent.runtime.filesystem import validate_write_path
        from personalized_agent.runtime.permissions import WorkspacePolicy
        inside = self.source / "scratch"
        inside.mkdir()
        policy = WorkspacePolicy.create(inside)
        own = Path(policy.write_root)
        own.mkdir(parents=True, exist_ok=True)
        self.assertEqual(validate_write_path(str(own / "a.txt"), policy).parent, own.resolve())

    def test_the_debuggers_ways_alone_are_kept_from_reading_it(self) -> None:
        """The undo of 2026-11-11: real work reads the code again."""
        unit = {"id": "unit_real", "name": "Real work", "kind": "task"}
        runtime = self.app.work._runtime(unit, self.app.work.folder_of(unit))
        self.assertIsNone(runtime.policy.code_root)
        self.assertNotIn("AGENT_CODE_ROOT", runtime._child_environment(None))
        self.assertIsNone(self.app.work.code_to_hold(unit))


class TheCodeARunRunsTests(_WithAStandIn):

    def test_refused_by_the_guard_and_the_system(self) -> None:
        env = self.runtime._child_environment(None)
        self.assertEqual(env.get("AGENT_SOURCE_ROOT"), str(self.source.resolve()))
        said = self.tools.call("run_python", {"code": (
            f"open(r'{self.granted / 'made.txt'}', 'w').write('ok')\n"
            "try:\n"
            f"    open(r'{self.source / 'pkg' / 'judge.py'}', 'w').write('changed')\n"
            "    print('WROTE')\n"
            "except Exception as exc:\n"
            "    print('refused:', type(exc).__name__, exc)\n"
            f"print('READ', open(r'{self.source / 'pkg' / 'judge.py'}').read().strip())\n")})
        out = str(said.get("stdout"))
        self.assertNotIn("WROTE", out, said)
        self.assertIn("own source code", out, said)
        self.assertIn("READ HOW_IT_JUDGES = 1", out, said)
        self.assertTrue((self.granted / "made.txt").exists(), said)
        self.assertEqual((self.source / "pkg" / "judge.py").read_text(encoding="utf-8"),
                         "HOW_IT_JUDGES = 1\n")


def _appcontainer() -> bool:
    status = isolation.isolation_status()
    return bool(status.get("isolated")) and status.get("backend") == "appcontainer"


class UnderWindowsTests(unittest.TestCase):
    """The operating system itself: a command the guard is not in (cmd's echo) cannot write
    the source through a folder granted that holds it - and reads it."""

    def setUp(self) -> None:
        if not _appcontainer():
            self.skipTest("no AppContainer isolation here")
        self.base = Path(tempfile.mkdtemp(prefix="pa-src-os-")).resolve()
        self.source = self.base / "watermelon_src"
        self.source.mkdir()
        (self.source / "judge.py").write_text("HOW_IT_JUDGES = 1\n", encoding="utf-8")
        self.work = self.base / "work"
        self.work.mkdir()
        patcher = mock.patch.object(isolation, "source_root", return_value=self.source)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.base, True)

    def _cmd(self, line: str):
        done = isolation.run_isolated(["cmd.exe", "/d", "/c", line], cwd=self.work,
                                      writable=[self.base], readable=[self.base], timeout=120)
        return done.returncode, done.stdout + done.stderr

    def test_written_beside_it_not_in_it_and_read(self) -> None:
        # No quotes inside: the line goes to cmd quoted whole (the folders hold no spaces).
        code, out = self._cmd(f'echo beside>{self.base / "beside.txt"}')
        self.assertEqual(code, 0, out)
        self.assertTrue((self.base / "beside.txt").is_file())
        code, out = self._cmd(f'echo changed>{self.source / "judge.py"}')
        self.assertNotEqual(code, 0, out)
        code, out = self._cmd(f'echo new>{self.source / "new.py"}')
        self.assertNotEqual(code, 0, out)
        self.assertFalse((self.source / "new.py").exists())
        self.assertEqual((self.source / "judge.py").read_text(encoding="utf-8"),
                         "HOW_IT_JUDGES = 1\n")
        code, out = self._cmd(f'type {self.source / "judge.py"}')
        self.assertEqual(code, 0, out)
        self.assertIn("HOW_IT_JUDGES", out)


class TheOtherSystemsTests(unittest.TestCase):

    def test_seatbelt_denies_writing_it_last_but_a_folder_of_the_runs_own(self) -> None:
        desktop = PurePosixPath("/Users/me/Desktop")
        source = PurePosixPath("/Users/me/Desktop/watermelon_src")
        own = PurePosixPath("/Users/me/Desktop/watermelon_src/scratch")
        profile = isolation.seatbelt_profile([desktop, own], [], NETWORK_NONE,
                                             home=Path(tempfile.gettempdir()), source=source)
        lines = profile.splitlines()
        denied = lines.index(f'(deny file-write* (subpath "{source}"))')
        opened = max(n for n, line in enumerate(lines)
                     if line.startswith("(allow file-read* file-write*"))
        self.assertGreater(denied, opened, "after the writable folders' opening")
        self.assertEqual(lines[denied + 1], f'(allow file-write* (subpath "{own}"))')
        self.assertNotIn("file-read", lines[denied], "read as before")

    def test_bubblewrap_binds_it_read_only_over_the_writable_folder(self) -> None:
        work = Path(tempfile.mkdtemp(prefix="pa-bwrap-src-")).resolve()
        self.addCleanup(shutil.rmtree, work, True)
        source = work / "watermelon_src"
        own = source / "scratch"
        own.mkdir(parents=True)
        with mock.patch.object(isolation, "source_root", return_value=source):
            readonly = isolation._source_inside([work])
        self.assertEqual(readonly, [source])
        args = isolation.bubblewrap_command(["true"], work, [work, own], [], [], NETWORK_NONE,
                                            home=Path(tempfile.gettempdir()), readonly=readonly)
        joined = " ".join(args)
        bound = joined.index(f"--bind {work} {work}")
        ro = joined.index(f"--ro-bind {source} {source}")
        again = joined.rindex(f"--bind {own} {own}")
        self.assertLess(bound, ro)
        self.assertLess(ro, again, "its own folder writable over the cover")
        with mock.patch.object(isolation, "source_root", return_value=source):
            self.assertEqual(isolation._source_inside([work / "elsewhere"]), [])


if __name__ == "__main__":
    unittest.main()
