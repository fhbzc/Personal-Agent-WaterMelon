"""The system security invariant: no agent controlled write leaves the workspace."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from support import ROOT, TemporaryAgent  # noqa: E402

sys.path.insert(0, str(ROOT))

from personalized_agent.runtime import filesystem as fs  # noqa: E402
from personalized_agent.runtime.permissions import SecurityViolation  # noqa: E402


class PathValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.policy = self.agent.app.policy
        self.runtime = self.agent.app.runtime

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_relative_path_is_anchored_to_the_workspace(self) -> None:
        resolved = fs.validate_write_path("documents/note.txt", self.policy)
        self.assertTrue(str(resolved).startswith(str(self.policy.workspace_root)))

    def test_absolute_path_outside_is_refused(self) -> None:
        outside = Path(self.agent.dir) / "outside.txt"
        with self.assertRaises(SecurityViolation):
            fs.validate_write_path(outside, self.policy)

    def test_parent_traversal_is_refused(self) -> None:
        with self.assertRaises(SecurityViolation):
            fs.validate_write_path("../escaped.txt", self.policy)
        with self.assertRaises(SecurityViolation):
            fs.validate_write_path("documents/../../escaped.txt", self.policy)

    def test_case_normalization_on_case_insensitive_platforms(self) -> None:
        upper = str(self.policy.write_root).upper() + os.sep + "x.txt"
        if os.path.normcase("A") == os.path.normcase("a"):
            resolved = fs.validate_write_path(upper, self.policy)
            self.assertTrue(fs.is_inside(resolved, self.policy.write_root))
        else:  # case sensitive filesystem: the upper case path is a different path
            with self.assertRaises(SecurityViolation):
                fs.validate_write_path(upper, self.policy)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unsupported")
    def test_symlink_escape_is_refused(self) -> None:
        target = Path(self.agent.dir) / "target"
        target.mkdir(exist_ok=True)
        link = self.policy.workspace_root / "escape_link"
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("creating symlinks requires privileges on this platform")
        with self.assertRaises(SecurityViolation):
            fs.validate_write_path(link / "file.txt", self.policy)

    def test_environment_variable_expansion_cannot_escape(self) -> None:
        os.environ["PA_TEST_ESCAPE"] = str(Path(self.agent.dir))
        try:
            with self.assertRaises(SecurityViolation):
                fs.validate_write_path("$PA_TEST_ESCAPE/x.txt", self.policy)
        finally:
            os.environ.pop("PA_TEST_ESCAPE", None)

    def test_runtime_write_and_read_roundtrip(self) -> None:
        path = self.runtime.write_text("documents/hello.txt", "hi")
        self.assertEqual(self.runtime.read_text(path), "hi")

    def test_archive_extraction_validates_members(self) -> None:
        import zipfile

        archive = self.policy.subdir("downloads") / "evil.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../../evil.txt", "payload")
        with self.assertRaises(SecurityViolation):
            self.runtime.extract_archive(archive, self.policy.subdir("generated"))

    def test_violations_are_reported_to_observability(self) -> None:
        with self.assertRaises(SecurityViolation):
            self.runtime.write_text(Path(self.agent.dir) / "nope.txt", "x")
        snapshot = self.agent.app.observability.snapshot()
        self.assertTrue(snapshot["agent_metrics"]["sandbox_violations"])


class GeneratedScriptGuardTests(unittest.TestCase):
    """A generated script sees the same three layers the tools do.

    Section 32, as amended: it writes in the agent's own directory, reads the
    rest of the workspace, and cannot touch the user's own material there.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.runtime = self.agent.app.runtime

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_script_can_write_inside_the_agents_own_directory(self) -> None:
        code = (
            "from pathlib import Path\n"
            "Path('documents').mkdir(exist_ok=True)\n"
            "Path('documents/ok.txt').write_text('written')\n"
            "print('done')\n"
        )
        result = self.runtime.run_python_code(code, timeout=60)
        self.assertTrue(result.ok, result.stderr)
        # A relative path means the agent's own documents, not the user's.
        agent_dir = self.agent.app.policy.write_root
        self.assertEqual((agent_dir / "documents/ok.txt").read_text(), "written")
        self.assertFalse((self.agent.workspace / "documents/ok.txt").exists())

    def test_script_cannot_modify_the_users_own_material(self) -> None:
        # The layer that matters: the workspace is not flat, and the user's
        # own files are not the agent's to rewrite.
        mine = self.agent.workspace / "documents" / "mine.txt"
        mine.parent.mkdir(parents=True, exist_ok=True)
        mine.write_text("the user wrote this")
        code = f"open({mine.as_posix()!r}, 'w').write('clobbered')\n"
        result = self.runtime.run_python_code(code, timeout=60)
        self.assertFalse(result.ok)
        self.assertEqual(mine.read_text(), "the user wrote this")

    def test_script_can_read_the_users_own_material(self) -> None:
        # Read only means readable. An agent that cannot see the work it is
        # helping with is not much help.
        mine = self.agent.workspace / "documents" / "brief.txt"
        mine.parent.mkdir(parents=True, exist_ok=True)
        mine.write_text("the brief")
        code = f"print(open({mine.as_posix()!r}).read())\n"
        result = self.runtime.run_python_code(code, timeout=60)
        self.assertTrue(result.ok, result.stderr)
        self.assertIn("the brief", result.stdout)

    def test_script_cannot_write_outside_the_workspace(self) -> None:
        escape = (Path(self.agent.dir) / "escaped.txt").as_posix()
        code = f"open({escape!r}, 'w').write('nope')\n"
        result = self.runtime.run_python_code(code, timeout=60)
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.violation)
        self.assertFalse((Path(self.agent.dir) / "escaped.txt").exists())

    def test_script_tempfile_is_redirected_into_the_workspace(self) -> None:
        code = (
            "import tempfile\n"
            "with tempfile.NamedTemporaryFile('w', delete=False) as handle:\n"
            "    handle.write('x')\n"
            "    print(handle.name)\n"
        )
        result = self.runtime.run_python_code(code, timeout=60)
        self.assertTrue(result.ok, result.stderr)
        self.assertIn(str(self.agent.app.policy.write_root), result.stdout)



class WorkspaceLayerTests(unittest.TestCase):
    """Section 32, as amended: the workspace is layered, not flat.

    The agent acts, so it writes - but in one directory of its own. The rest
    of the workspace is the user's, readable and never writable, and the
    internal state is neither.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.policy = self.agent.app.policy
        self.mine = self.policy.workspace_root / "documents" / "mine.txt"
        self.mine.parent.mkdir(parents=True, exist_ok=True)
        self.mine.write_text("the user wrote this")

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_agent_has_a_directory_of_its_own(self) -> None:
        self.assertEqual(self.policy.write_root.name, "agent")
        self.assertTrue(self.policy.write_root.is_dir())
        self.assertTrue(fs.is_inside(self.policy.write_root,
                                     self.policy.workspace_root))

    def test_it_can_write_there(self) -> None:
        # The point of the amendment: the agent acts. A permission that
        # refuses every write is not a narrower permission, it is none.
        written = self.agent.app.runtime.write_text("documents/note.md", "done")
        self.assertTrue(fs.is_inside(written, self.policy.write_root))
        self.assertEqual(written.read_text(), "done")

    def test_a_relative_path_names_one_file_in_both_directions(self) -> None:
        self.agent.app.runtime.write_text("documents/note.md", "done")
        found = fs.validate_read_path("documents/note.md", self.policy)
        self.assertEqual(found.read_text(), "done")

    def test_it_cannot_overwrite_the_users_own_material(self) -> None:
        with self.assertRaises(SecurityViolation) as caught:
            fs.validate_write_path(self.mine, self.policy)
        # Refused as the user's material, not merely as the wrong directory:
        # the two facts are different and the first is the one worth saying.
        self.assertEqual(caught.exception.boundary, "readonly")

    def test_it_can_read_the_users_own_material(self) -> None:
        found = fs.validate_read_path(self.mine, self.policy)
        self.assertEqual(found.read_text(), "the user wrote this")

    def test_it_cannot_write_loose_in_the_workspace_either(self) -> None:
        with self.assertRaises(SecurityViolation):
            fs.validate_write_path(self.policy.workspace_root / "loose.txt",
                                   self.policy)

    def test_the_internal_state_is_neither_read_nor_written(self) -> None:
        # Where the event log lives: what was recorded about the user is not
        # something the agent can read back or quietly revise.
        internal = self.agent.app.paths.data_dir / "database" / "events.db"
        for check in (fs.validate_write_path, fs.validate_read_path):
            with self.assertRaises(SecurityViolation):
                check(internal, self.policy)

    def test_the_layers_are_reported_to_whoever_asks(self) -> None:
        described = self.policy.to_dict()
        self.assertTrue(described["writable_root"].endswith("agent"))
        self.assertTrue(any(p.endswith("documents")
                            for p in described["readonly_paths"]))

    def test_the_runtime_scratch_is_inside_the_agents_directory(self) -> None:
        # Section 111, as amended - otherwise it would need its own exception
        # to the one rule about where the agent writes.
        self.assertTrue(fs.is_inside(self.policy.scripts_dir,
                                     self.policy.write_root))


class ScriptEscapeTests(unittest.TestCase):
    """Section 31, as amended: a script cannot step off the guarded surface.

    Patching the filesystem functions confines a script only while it keeps
    using them. Starting another process leaves the sandbox entirely - the
    child runs with the user's own authority and an unpatched standard library
    - and loading a native library calls the operating system without touching
    a guarded function at all. Each vector below was demonstrated writing
    outside the workspace before the blocks were added.

    These run with the guard alone (TemporaryAgent turns the OS sandbox
    off), where no child process starts at all. Where the operating system
    confines the run, a Python child may start, with the guard loaded into
    it - tests/test_python_children.py - and these vectors are refused there
    too, by the guard in the child.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.runtime = self.agent.app.runtime
        # Genuinely outside the workspace and outside the user's home, so a
        # file appearing here cannot be explained by any allowed path.
        self.outside = Path("C:/Windows/Temp/pa_escape_test.txt") \
            if os.name == "nt" else Path("/tmp/pa_escape_test.txt")
        self.outside.unlink(missing_ok=True)

    def tearDown(self) -> None:
        self.outside.unlink(missing_ok=True)
        self.agent.cleanup()

    def _run(self, code: str):
        result = self.runtime.run_python_code(code, timeout=120)
        self.assertNotIn("SyntaxError", result.stderr or "",
                         "the test's own code failed to compile")
        return result

    def _assert_contained(self, code: str) -> None:
        result = self._run(code)
        self.assertFalse(self.outside.exists(),
                         f"a script wrote outside the workspace: {self.outside}")
        self.assertFalse(result.ok, "the escape attempt was not refused")

    @property
    def _target(self) -> str:
        return self.outside.as_posix()

    def test_subprocess_cannot_start_an_unguarded_child(self) -> None:
        self._assert_contained(
            "import subprocess, sys\n"
            "subprocess.run([sys.executable, '-c', "
            f"'open(\"{self._target}\", \"w\").write(\"x\")'])"
        )

    def test_os_system_cannot_shell_out(self) -> None:
        self._assert_contained(
            f"import os\nos.system('cmd /c echo x > \"{self._target}\"')"
        )

    def test_ctypes_cannot_call_the_operating_system_directly(self) -> None:
        self._assert_contained(
            "import ctypes\n"
            f"ctypes.windll.kernel32.CreateFileW('{self._target}', "
            "0x40000000, 0, None, 2, 128, None)"
        )

    def test_multiprocessing_is_process_creation_too(self) -> None:
        self._assert_contained(
            "import multiprocessing as mp\n"
            f"def w():\n    open('{self._target}', 'w').write('x')\n"
            "if __name__ == '__main__':\n"
            "    p = mp.Process(target=w); p.start(); p.join()"
        )

    def test_reloading_subprocess_does_not_restore_the_escape(self) -> None:
        # Why the block sits on the low level choke point rather than on the
        # friendly name: reloading the module would put the friendly name back.
        self._assert_contained(
            "import importlib, subprocess, sys\n"
            "importlib.reload(subprocess)\n"
            "subprocess.run([sys.executable, '-c', "
            f"'open(\"{self._target}\", \"w\").write(\"x\")'])"
        )

    def test_without_os_isolation_the_refusal_says_what_to_do_instead(self) -> None:
        result = self._run("import subprocess, sys\n"
                           "subprocess.run([sys.executable, '-m', 'pytest'])")
        self.assertFalse(result.ok)
        self.assertIn("no child process can start in this run", result.violation)
        self.assertIn("pytest.main", result.violation)

    def test_reloading_os_does_not_restore_the_escape(self) -> None:
        self._assert_contained(
            "import importlib, os\n"
            "importlib.reload(os)\n"
            f"os.system('cmd /c echo x > \"{self._target}\"')"
        )

    def test_a_script_that_needs_no_escape_still_runs(self) -> None:
        # The blocks must not have made the sandbox useless.
        result = self._run(
            "from pathlib import Path\n"
            "Path('generated').mkdir(exist_ok=True)\n"
            "Path('generated/fine.txt').write_text('ok')\n"
            "print('ran')"
        )
        self.assertTrue(result.ok, result.stderr)
        self.assertIn("ran", result.stdout)

if __name__ == "__main__":
    unittest.main()


class AtomicReplaceTests(unittest.TestCase):
    """A file this application rewrites must survive another process holding it.

    On Windows the move that completes an atomic write fails outright, with a
    permission error, if anything else has the target open - a sync client, an
    indexer, a scanner. The agent root is a directory the user is invited to
    put anywhere, including a synced folder, so this is ordinary, not exotic.
    """

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="pa-atomic-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_brief_hold_on_the_target_is_waited_out(self) -> None:
        from personalized_agent.common.atomic import replace_atomically

        source, target = self.dir / "new.tmp", self.dir / "settings.json"
        target.write_text("old", encoding="utf-8")
        source.write_text("new", encoding="utf-8")

        attempts = []
        real = os.replace

        def held(a, b):
            attempts.append(1)
            if len(attempts) < 3:                 # let go on the third try
                raise PermissionError(5, "Access is denied")
            return real(a, b)

        with unittest.mock.patch("os.replace", held):
            replace_atomically(source, target)
        self.assertEqual(target.read_text(encoding="utf-8"), "new")
        self.assertGreaterEqual(len(attempts), 3)

    def test_a_hold_that_never_lets_go_is_reported(self) -> None:
        from personalized_agent.common.atomic import replace_atomically

        source, target = self.dir / "new.tmp", self.dir / "settings.json"
        source.write_text("new", encoding="utf-8")
        with unittest.mock.patch("os.replace", side_effect=PermissionError(5, "denied")):
            with self.assertRaises(PermissionError):
                replace_atomically(source, target)

    def test_settings_survive_a_held_file(self) -> None:
        # The whole point: the user's setting is saved, not lost to a scanner.
        agent = TemporaryAgent()
        try:
            real = os.replace
            state = {"first": True}

            def held(a, b):
                if state["first"]:
                    state["first"] = False
                    raise PermissionError(5, "Access is denied")
                return real(a, b)

            with unittest.mock.patch("os.replace", held):
                agent.app.config.set("popup_font_size", 15)
            self.assertEqual(agent.app.config.get("popup_font_size"), 15)
        finally:
            agent.cleanup()
