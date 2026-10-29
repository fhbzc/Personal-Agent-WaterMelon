"""A RESULT GIVEN AGAIN ONLY ON EXACTLY THE SAME INPUTS (agent/work/reuse.py,
the user, 2026-10-28): run_python and run_tests, in the real sandbox -
every file the code read and every script it imported, wherever they are,
the folders it listed, the outputs as it left them; said to be so, the
agent free to run anew (fresh). Never given again: code that started a
program other than Python, or loaded a library opening files natively."""

from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from unittest import mock

from support import TemporaryAgent

from personalized_agent.agent.work import reuse


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        from personalized_agent.agent.work.tools import WorkTools
        from personalized_agent.runtime.sandbox import SandboxRuntime
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.folder = self.app.work.folder_for("Tide figures", "project-reuse321")
        self.tools = WorkTools(SandboxRuntime(self.app.work.policy_for(self.folder),
                                              platform=self.app.platform))
        self.tools.label = "the work agent"
        # Data the work reads that is not in its folder - the Desktop copy.
        self.outside = Path(self.agent.dir) / "desktop_copy"
        self.outside.mkdir()
        (self.outside / "tides.csv").write_text("day,height\n1,2.5\n", encoding="utf-8")
        (self.folder / "helpers.py").write_text(
            "from deeper import scale\n\ndef height(text):\n"
            "    return scale(float(text.splitlines()[1].split(',')[1]))\n", encoding="utf-8")
        (self.folder / "deeper.py").write_text("def scale(x):\n    return x * 2\n",
                                               encoding="utf-8")
        self.quick = mock.patch.object(reuse, "LEAST_SECONDS", 0.0)
        self.quick.start()

    def tearDown(self) -> None:
        self.quick.stop()
        self.agent.cleanup()

    def run_code(self, code, **more):
        said = self.tools.call("run_python", dict({"code": code}, **more))
        self.assertNotIn("error", said, said)
        return said

    # The work's own scripts are imported from its folder, as the agents do.
    CODE = ("import sys, uuid\nsys.path.insert(0, '.')\nimport helpers\n"
            "text = open(r'{data}', encoding='utf-8').read()\n"
            "open('figure.txt', 'w').write(str(helpers.height(text)))\n"
            "print(helpers.height(text), uuid.uuid4().hex)\n")


class RunPythonTests(_Base):

    def code(self):
        return self.CODE.format(data=self.outside / "tides.csv")

    def test_given_again_said_so_and_why(self) -> None:
        first = self.run_code(self.code())
        self.assertNotIn("not_a_new_run", first)
        again = self.run_code(self.code())
        self.assertEqual(again["stdout"], first["stdout"], "not run: the same output")
        self.assertIn("NOT A NEW RUN", again["not_a_new_run"])
        self.assertIn("fresh=true", again["not_a_new_run"])
        checked = again["reused"]["checked"]
        # The scripts it imported, however deep, and the data outside its folder.
        self.assertEqual(sorted(checked["scripts_imported"]), ["deeper.py", "helpers.py"])
        self.assertIn("tides.csv", checked["files_read_named"])
        self.assertEqual(checked["outputs_as_written"], 1)
        self.assertEqual(again["reused"]["by"], "the work agent")
        self.assertIn("cannot_see", again["reused"])

    def test_run_anew_when_anything_it_ran_on_changed(self) -> None:
        first = self.run_code(self.code())
        cases = [
            ("a function of a script it imports, two deep",
             lambda: (self.folder / "deeper.py").write_text(
                 "def scale(x):\n    return x * 3\n", encoding="utf-8")),
            ("its data outside the work's folder",
             lambda: (self.outside / "tides.csv").write_text("day,height\n1,4.0\n",
                                                             encoding="utf-8")),
            ("its output, gone", lambda: (self.folder / "figure.txt").unlink()),
        ]
        last = first
        for what, change in cases:
            with self.subTest(what):
                self.assertIn("not_a_new_run", self.run_code(self.code()))
                change()
                now = self.run_code(self.code())
                self.assertNotIn("not_a_new_run", now, what)
                self.assertNotEqual(now["stdout"], last["stdout"])
                last = now

    def test_fresh_runs_anew_and_a_quick_one_is_not_kept(self) -> None:
        first = self.run_code(self.code())
        fresh = self.run_code(self.code(), fresh=True)
        self.assertNotIn("not_a_new_run", fresh)
        self.assertNotEqual(fresh["stdout"], first["stdout"])
        with mock.patch.object(reuse, "LEAST_SECONDS", 60.0):
            self.run_code("print('quick')")
            self.assertNotIn("not_a_new_run", self.run_code("print('quick')"))

    def test_never_again_what_could_not_all_be_seen(self) -> None:
        # A library opening files in its own native code (by its name).
        (self.folder / "duckdb.py").write_text("X = 1\n", encoding="utf-8")
        self.run_code("import sys; sys.path.insert(0, '.'); import duckdb, uuid\nprint(uuid.uuid4().hex)")
        self.assertNotIn("not_a_new_run", self.run_code("import sys; sys.path.insert(0, '.'); import duckdb, uuid\nprint(uuid.uuid4().hex)"))
        # A program other than Python - but a Python child is seen.
        other = ("import subprocess, sys, uuid\n"
                 "subprocess.run(['cmd', '/c', 'echo hi'] if sys.platform == 'win32' "
                 "else ['echo', 'hi'])\nprint(uuid.uuid4().hex)")
        self.run_code(other)
        self.assertNotIn("not_a_new_run", self.run_code(other))
        child = ("import subprocess, sys, uuid\n"
                 "subprocess.run([sys.executable, '-c', "
                 "'import sys; sys.path.insert(0, \".\"); import helpers'])\n"
                 "print(uuid.uuid4().hex)")
        self.run_code(child)
        self.assertIn("not_a_new_run", self.run_code(child))

    def test_the_reviewer_is_given_the_work_agents_run(self) -> None:
        from personalized_agent.agent.work.tools import WorkTools
        reviewer = WorkTools(self.tools.runtime)
        reviewer.label = "the reviewer"
        reviewer.python_runs = self.tools.python_runs
        self.run_code(self.code())
        said = reviewer.call("run_python", {"code": self.code()})
        self.assertEqual(said["reused"]["by"], "the work agent")

    def test_where_the_time_went_kept_and_never_told_the_agent(self) -> None:
        logged = []
        self.tools.log = lambda name, said: logged.append((name, said))
        first = self.run_code(self.code())
        again = self.run_code(self.code())
        for said in (first, again):
            self.assertNotIn("_timing", said, "never told the agent")
            self.assertNotIn("timing", said)
        ran, reused = [said["timing"] for name, said in logged if name == "run_python"]
        for part in ("key", "reuse_check", "look_before", "code", "look_after",
                     "kept_for_reuse", "total", "waited_for_turn"):
            self.assertIn(part, ran)
        self.assertTrue(ran["kept"])
        self.assertFalse(ran["reused"])
        self.assertTrue(reused["reused"])
        self.assertNotIn("code", reused, "not run again")
        self.assertGreaterEqual(ran["total"], ran["code"])

    def test_run_command_is_never_given_again(self) -> None:
        from personalized_agent.agent.work.tools import commands_allowed
        if not commands_allowed(self.tools.runtime):
            self.skipTest("commands cannot run here")
        once = self.tools.call("run_command", {"command": "echo %RANDOM% %TIME%"
                                                          if __import__("os").name == "nt"
                                                          else "echo $RANDOM"})
        again = self.tools.call("run_command", {"command": "echo %RANDOM% %TIME%"
                                                           if __import__("os").name == "nt"
                                                           else "echo $RANDOM"})
        self.assertNotIn("not_a_new_run", again)
        self.assertNotIn("not_a_new_run", once)


class ThePackagesKeyTests(unittest.TestCase):
    """The packages read from the top of their folder: quick however many
    files they hold, changed by an install, an upgrade or a removal."""

    def setUp(self) -> None:
        import tempfile
        self.root = Path(tempfile.mkdtemp(prefix="packages-key-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self._install("numpy", "1.26.0", files=3000)

    def _install(self, name, version, files=2):
        package = self.root / name
        package.mkdir(exist_ok=True)
        for n in range(files):
            (package / f"m{n}.py").write_text("x = 1\n", encoding="utf-8")
        info = self.root / f"{name}-{version}.dist-info"
        info.mkdir()
        (info / "RECORD").write_text(f"{name}/m0.py,,\n", encoding="utf-8")
        return info

    def test_quick_however_many_files(self) -> None:
        began = time.perf_counter()
        reuse.packages_key([self.root])
        self.assertLess(time.perf_counter() - began, 0.5)
        # The top of the folder alone: no file inside a package is looked at.
        with mock.patch.object(reuse.os, "walk", side_effect=AssertionError("walked")):
            self.assertTrue(reuse.packages_key([self.root]))

    def test_an_install_an_upgrade_and_a_removal_change_it(self) -> None:
        import shutil
        first = reuse.packages_key([self.root])
        self.assertEqual(reuse.packages_key([self.root]), first, "the same, read again")
        added = self._install("six", "1.16.0")
        second = reuse.packages_key([self.root])
        self.assertNotEqual(second, first, "installed")
        added.rename(self.root / "six-1.17.0.dist-info")
        third = reuse.packages_key([self.root])
        self.assertNotEqual(third, second, "upgraded")
        record = self.root / "six-1.17.0.dist-info" / "RECORD"
        time.sleep(0.02)
        record.write_text("six/m0.py,,\nsix/m1.py,,\n", encoding="utf-8")
        __import__("os").utime(record, ns=(time.time_ns(), time.time_ns()))
        self.assertNotEqual(reuse.packages_key([self.root]), third, "installed again over it")
        shutil.rmtree(self.root / "six")
        shutil.rmtree(self.root / "six-1.17.0.dist-info")
        self.assertNotEqual(reuse.packages_key([self.root]), third, "removed")

    def test_none_installed_yet(self) -> None:
        self.assertTrue(reuse.packages_key([self.root / "not-yet"]))


class RunTestsTests(_Base):

    def test_tests_reading_outside_their_folder_run_anew_when_that_changed(self) -> None:
        tests = self.folder / "pkg"
        tests.mkdir()
        (tests / "test_tide.py").write_text(
            f"def test_height():\n"
            f"    text = open(r'{self.outside / 'tides.csv'}', encoding='utf-8').read()\n"
            f"    assert '2.5' in text\n", encoding="utf-8")
        first = self.tools.call("run_tests", {"folder": str(tests)})
        self.assertEqual(first.get("returncode"), 0, json.dumps(first)[:1500])
        again = self.tools.call("run_tests", {"folder": str(tests)})
        self.assertIn("NOT A NEW RUN", again["not_a_new_run"])
        self.assertIn("fresh=true", again["not_a_new_run"])
        self.assertIn("tides.csv", again["reused"]["checked"]["files_read_named"])
        time.sleep(0.05)
        (self.outside / "tides.csv").write_text("day,height\n1,9.9\n", encoding="utf-8")
        now = self.tools.call("run_tests", {"folder": str(tests)})
        self.assertNotIn("not_a_new_run", now)
        self.assertNotEqual(now.get("returncode"), 0, "it ran, and the data changed")


if __name__ == "__main__":
    unittest.main()
