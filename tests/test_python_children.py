"""Python child processes, with the guard loaded into them.

Agents test their work the way everyone does - `subprocess.run([sys.executable,
"-m", "pytest"])`, a server started with `Popen` - and the guard used to refuse
every process a run's code started, because a child ran with an unpatched
standard library. The user's decision: where the operating system confines
the run (an AppContainer, Seatbelt, bubblewrap, Landlock - which every process
it starts inherits), a PYTHON child may start, its command rewritten so it
loads the same guard first, under the same policy; ANY OTHER PROGRAM only
where the system alone keeps every rule - an AppContainer, Seatbelt or
bubblewrap, with no network (the user, 2026-10-25) - never under Landlock;
and where nothing confines the run, no child at all, as before
(runtime/guard.py, "Process creation and native code").

These run real children on this machine where it has OS isolation and are
skipped where it has none; the rewrite itself, and the fallback, are checked
everywhere.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "personalized_agent" / "runtime"))

import guard  # noqa: E402  - the guard on its own, as children load it

from personalized_agent.runtime import isolation  # noqa: E402

STATUS = isolation.isolation_status()
ISOLATED = bool(STATUS["isolated"])
WINDOWS = sys.platform.startswith("win")


def _last_json(text: str) -> dict:
    for line in reversed((text or "").strip().splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no result printed: {text!r}")


# ---------------------------------------------------------------------------
# The rewrite, on its own
# ---------------------------------------------------------------------------
class _Rewrite(unittest.TestCase):
    """The guard as a child of a confined run would hold it."""

    POLICY = {guard.WORKSPACE_ENV: "/work", guard.NO_NETWORK_ENV: "1",
              guard.OS_ISOLATION_ENV: "appcontainer", guard.SEALED_ENV: "/other"}

    def setUp(self) -> None:
        here = {os.path.normcase(os.path.abspath(sys.executable)),
                os.path.normcase(os.path.realpath(sys.executable))}
        patcher = mock.patch.multiple(guard, _os_backend="appcontainer", _offline=True,
                                      _interpreter=sys.executable, _interpreters=here,
                                      _guard_home="/the/guard", _child_policy=dict(self.POLICY))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tail(self, command: list) -> list:
        """What follows the bootstrap: mode, safe, target, args."""
        at = command.index("-c")
        return command[at + 2:]


class TheCommandIsRewrittenTests(_Rewrite):

    def test_module_script_and_code_each_keep_their_arguments(self) -> None:
        for given, expected in (
                (["python", "-m", "pytest", "-q", "tests"], ["m", "0", "pytest", "-q", "tests"]),
                ([sys.executable, "app.py", "--port", "5000"],
                 ["s", "0", "app.py", "--port", "5000"]),
                (["python3", "-c", "print(1)", "a", "-b"], ["c", "0", "print(1)", "a", "-b"]),
                (["py", "-3", "-m", "app"], ["m", "0", "app"]),
                (["python", "-I", "-m", "app"], ["m", "1", "app"]),
                (["python", "-uc", "x"], ["c", "0", "x"]),
                (["python", "--", "-odd.py"], ["s", "0", "-odd.py"])):
            with self.subTest(given=given):
                command = guard.child_command(given[0], given[1:])
                self.assertEqual(self.tail(command), expected)

    def test_it_is_this_interpreter_isolated_with_the_guard_loaded_first(self) -> None:
        command = guard.child_command("python", ["-m", "pytest"])
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[1], "-I")
        bootstrap = command[command.index("-c") + 1]
        self.assertIn(repr("/the/guard"), bootstrap)
        # Loaded and installed before anything that was asked for runs.
        self.assertLess(bootstrap.index("import guard"), bootstrap.index("guard.install()"))
        self.assertTrue(bootstrap.endswith("guard.run_child()"))

    def test_harmless_options_are_passed_on(self) -> None:
        command = guard.child_command("python", ["-u", "-B", "-W", "ignore", "-X", "utf8",
                                                 "-m", "app"])
        options = command[:command.index("-c")]
        # UTF-8 mode always, first: -I ignores PYTHONIOENCODING, and a piped
        # child wrote in the old Windows code page - Chinese text failed.
        self.assertEqual(options[1:4], ["-I", "-X", "utf8"])
        self.assertEqual(options[4:], ["-u", "-B", "-W", "ignore", "-X", "utf8"])

    def test_what_cannot_be_rewritten_safely_is_refused_with_how_instead(self) -> None:
        for given in (["python"], ["python", "-i", "x.py"], ["python", "-"],
                      ["python", "-S", "x.py"], ["python", "-X", "pycache_prefix=/x", "-c", "1"],
                      ["python", "--help"], ["python", "-m"]):
            with self.subTest(given=given):
                with self.assertRaises(guard.SandboxViolation) as caught:
                    guard.child_command(given[0], given[1:])
                self.assertIn("run a script, -m module or -c code", str(caught.exception))

    def test_other_programs_are_refused_with_the_python_hint(self) -> None:
        for program in ("cmd", "powershell", "git", "node", "bash", "/bin/sh",
                        "C:/Windows/py.exe", str(Path(tempfile.gettempdir()) / "python.exe")):
            with self.subTest(program=program):
                with self.assertRaises(guard.SandboxViolation) as caught:
                    guard.child_command(program, ["-c", "1"])
                said = str(caught.exception)
                self.assertIn("only Python child processes may be started", said)
                self.assertIn("subprocess.run([sys.executable, '-m', 'pytest'", said)
                self.assertNotIn("\n", said)

    def test_a_bare_python_name_is_this_interpreter_never_a_lookup(self) -> None:
        for name in ("python", "python3", "python3.12", "py", "PYTHON.EXE"):
            with self.subTest(name=name):
                self.assertTrue(guard._is_this_python(name))
        for name in ("pythonx", "python2", "ipython", "pip"):
            with self.subTest(name=name):
                self.assertFalse(guard._is_this_python(name))

    def test_the_policy_is_the_parents_whatever_env_said(self) -> None:
        given = {"PATH": "/bin", guard.WORKSPACE_ENV: "/", "agent_workspace": "/",
                 guard.CODE_OPEN_ENV: "/", "__PYVENV_LAUNCHER__": "/elsewhere/python"}
        env = guard.child_environment(given)
        self.assertEqual(env["PATH"], "/bin")
        self.assertEqual(env[guard.WORKSPACE_ENV], "/work")
        self.assertEqual(env[guard.SEALED_ENV], "/other")
        self.assertEqual(env[guard.NO_NETWORK_ENV], "1")
        self.assertNotIn(guard.CODE_OPEN_ENV, env)
        self.assertNotIn("__PYVENV_LAUNCHER__", env)
        if os.name == "nt":                         # names are one on Windows
            self.assertNotIn("agent_workspace", env)

    def test_no_child_without_os_isolation_or_in_a_packaged_app(self) -> None:
        for backend in ("none", "", "APPCONTAINER-ish"):
            with self.subTest(backend=backend), mock.patch.object(guard, "_os_backend", backend):
                with self.assertRaises(guard.SandboxViolation) as caught:
                    guard.child_command("python", ["-c", "1"])
                self.assertIn("no child process can start in this run", str(caught.exception))
                self.assertIn("pytest.main", str(caught.exception))
        with mock.patch.object(sys, "frozen", True, create=True):
            with self.assertRaises(guard.SandboxViolation):
                guard.child_command("python", ["-c", "1"])

    def test_no_child_in_a_process_that_may_reach_the_network(self) -> None:
        # The package installer (a list of places) and the chat agent's own
        # code (anywhere): no child, isolated or not - as before.
        with mock.patch.object(guard, "_offline", False):
            with self.assertRaises(guard.SandboxViolation) as caught:
                guard.child_command("python", ["-c", "1"])
            self.assertIn("may reach the network", str(caught.exception))

    def test_a_windows_command_line_is_read_as_the_child_would(self) -> None:
        import subprocess
        for argv in (["C:/Program Files/py/python.exe", "-c", "print(\"a b\")", ""],
                     ["python", "x\\", "a\\\\\"b", "two words\\", "l1\nl2", "\\\\srv\\s p"]):
            with self.subTest(argv=argv):
                line = subprocess.list2cmdline(argv)
                self.assertEqual(guard.split_windows_command_line(line), argv)


# ---------------------------------------------------------------------------
# Real children, confined by the operating system
# ---------------------------------------------------------------------------
class _Run(unittest.TestCase):

    sandbox_enabled = True

    def setUp(self) -> None:
        from personalized_agent.runtime.permissions import WorkspacePolicy
        from personalized_agent.runtime.sandbox import SandboxRuntime
        self.root = Path(tempfile.mkdtemp(prefix="pa-kids-")).resolve()
        workspace = self.root / "workspace"
        self.folder = workspace / "agent_work" / "proj-abc123"
        self.folder.mkdir(parents=True)
        self.internal = self.root / "internal"
        self.internal.mkdir()
        (self.internal / "settings.json").write_text("secret")
        self.other = workspace / "agent_work" / "other-way"
        self.other.mkdir()
        (self.other / "answer.txt").write_text("the other way's answer")
        self.outside = self.root / "outside.txt"
        self.policy = WorkspacePolicy(workspace_root=workspace, writable_root=self.folder,
                                      readable_paths=[self.root],
                                      protected_paths=[self.internal], network=False)
        self.policy.sealed_paths = [self.other]
        self.runtime = SandboxRuntime(self.policy, sandbox_enabled=self.sandbox_enabled)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def run_code(self, code: str, timeout: float = 120):
        return self.runtime.run_python_code(textwrap.dedent(code), timeout=timeout)

    def child(self, child_code: str, **run) -> dict:
        """The run's code starts a Python child running `child_code` and
        prints what came of it."""
        result = self.run_code(f"""
            import json, subprocess, sys
            done = subprocess.run([sys.executable, '-c', {child_code!r}],
                                  capture_output=True, text=True, timeout=120)
            print(json.dumps({{'rc': done.returncode, 'out': done.stdout,
                              'err': done.stderr}}))
            """, **run)
        self.assertNotIn("SyntaxError", result.stderr)
        return _last_json(result.stdout + "\n" + result.stderr)


@unittest.skipUnless(ISOLATED, f"no OS isolation here: {STATUS['why']}")
class APythonChildRunsUnderTheSameRulesTests(_Run):

    def test_a_child_runs_and_its_output_comes_back(self) -> None:
        said = self.child("print('hi')")
        self.assertEqual(said["rc"], 0, said)
        self.assertEqual(said["out"].strip(), "hi")

    def test_its_output_reaches_the_run_when_not_captured(self) -> None:
        result = self.run_code("""
            import subprocess, sys
            subprocess.run([sys.executable, '-c', "print('from the child')"], check=True)
            """)
        self.assertTrue(result.ok, result.stderr)
        self.assertIn("from the child", result.stdout)

    def test_it_writes_in_the_run_folder(self) -> None:
        said = self.child("open('made-by-child.txt', 'w').write('ok')")
        self.assertEqual(said["rc"], 0, said)
        self.assertEqual((self.folder / "made-by-child.txt").read_text(), "ok")

    def test_a_write_outside_is_refused_in_the_child_and_the_run_sees_it(self) -> None:
        target = self.outside.as_posix()
        said = self.child(f"open({target!r}, 'w').write('x')")
        self.assertNotEqual(said["rc"], 0, said)
        self.assertIn("SandboxViolation", said["err"])
        self.assertIn("write in that folder instead", said["err"])
        self.assertFalse(self.outside.exists())
        # Uncaptured, the refusal is the run's own: it fails, and says why.
        result = self.run_code(f"""
            import subprocess, sys
            subprocess.run([sys.executable, '-c', "open({target!r}, 'w').write('x')"])
            """)
        self.assertFalse(result.ok)
        self.assertIn("outside the agent's own directory", result.violation or "")
        self.assertFalse(self.outside.exists())

    def test_env_cannot_tell_the_child_it_may_write_elsewhere(self) -> None:
        target = self.outside.as_posix()
        result = self.run_code(f"""
            import json, os, subprocess, sys
            env = dict(os.environ, AGENT_WORKSPACE={self.root.as_posix()!r},
                       AGENT_OS_ISOLATION='none', AGENT_SEALED_PATHS='')
            done = subprocess.run([sys.executable, '-c',
                                   "open({target!r}, 'w').write('x')"],
                                  env=env, capture_output=True, text=True)
            print(json.dumps({{'rc': done.returncode, 'err': done.stderr}}))
            """)
        said = _last_json(result.stdout)
        self.assertNotEqual(said["rc"], 0, said)
        self.assertIn("SandboxViolation", said["err"])
        self.assertFalse(self.outside.exists())

    def test_the_child_reaches_no_server_of_this_computer(self) -> None:
        """Not the application's, not the user's: where the run has a
        loopback of its own, the operating system refuses it; elsewhere
        the guard refuses every connection."""
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        self.addCleanup(server.close)
        port = server.getsockname()[1]
        said = self.child(f"import socket; socket.create_connection(('127.0.0.1', {port}), 3)")
        self.assertNotEqual(said["rc"], 0, said)
        if not isolation.own_loopback(STATUS):
            self.assertIn("SandboxViolation: network", said["err"])
            self.assertIn("test_client()", said["err"])
        server.setblocking(False)
        with self.assertRaises(BlockingIOError):
            server.accept()

    def test_a_listing_leaves_out_what_may_not_be_read(self) -> None:
        """A sealed folder (as the project's run logs are) is not in its
        parent's listing, so a walk of the parent goes past it - no refusal,
        no warning; asked for by name it is still refused, saying why."""
        parent = self.other.parent
        result = self.run_code(f"""
            import os
            print("listed", sorted(os.listdir({str(parent)!r})))
            with os.scandir({str(parent)!r}) as found:
                print("scanned", sorted(one.name for one in found))
            walked = [os.path.basename(top) for top, _dirs, _files in os.walk({str(parent)!r})]
            print("walked", sorted(walked))
            try:
                os.listdir({str(self.other)!r})
                print("READ IT")
            except Exception as exc:
                print("refused", type(exc).__name__)
            """)
        self.assertIn("listed ['proj-abc123']", result.stdout, result.stderr)
        self.assertIn("scanned ['proj-abc123']", result.stdout)
        self.assertNotIn("other-way", result.stdout.split("refused")[0])
        self.assertIn("refused SandboxViolation", result.stdout)
        self.assertNotIn("READ IT", result.stdout)
        walked = next(line for line in result.stdout.splitlines() if line.startswith("walked"))
        self.assertIn("'proj-abc123'", walked)
        self.assertNotIn("other-way", walked)

    @unittest.skipUnless(sys.platform == "win32", "junctions")
    def test_a_junction_in_the_folder_is_still_seen_through_quickly(self) -> None:
        """The guard resolves paths the quick way in an AppContainer - what
        the code cannot change, once - but a link or junction the code's
        folder holds, at any depth, is still followed: a write through one
        to outside is refused by the guard itself, saying why."""
        import _winapi
        away = self.root / "away"
        away.mkdir()
        _winapi.CreateJunction(str(away), str(self.folder / "jump"))
        (self.folder / "deep" / "er").mkdir(parents=True)
        _winapi.CreateJunction(str(away), str(self.folder / "deep" / "er" / "jump"))
        for through in ("jump/x.txt", "deep/er/jump/y.txt"):
            with self.subTest(through=through):
                result = self.run_code(f"""
                    for n in range(3):
                        open('plain{{}}.txt'.format(n), 'w').write('fine')
                    try:
                        open({through!r}, 'w').write('escaped')
                        print('WROTE')
                    except Exception as exc:
                        print('refused', type(exc).__name__, exc)
                    """)
                self.assertNotIn("WROTE", result.stdout)
                self.assertIn("SandboxViolation", result.stdout + result.stderr, result.stdout)
                self.assertEqual(list(away.iterdir()), [])
        self.assertTrue((self.folder / "plain2.txt").exists())

    @unittest.skipUnless(sys.platform == "win32", "the Windows calls")
    def test_native_code_loads_and_the_system_still_refuses_what_it_must(self) -> None:
        """Under the operating system's confinement ctypes loads - uvicorn,
        click and colorama import it - and what it could reach around the
        guard the system refuses anyway: a file written outside the run's
        folder, a sealed folder read, the app's own state read."""
        outside = self.outside.as_posix()
        sealed = (self.other / "answer.txt").as_posix()
        state = (self.internal / "settings.json").as_posix()
        result = self.run_code(f"""
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL('kernel32', use_last_error=True)
            k32.CreateFileW.restype = wintypes.HANDLE
            k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                        wintypes.HANDLE]
            BAD = wintypes.HANDLE(-1).value
            def opened(path, write):
                h = k32.CreateFileW(path, 0x40000000 if write else 0x80000000, 0, None,
                                    2 if write else 3, 128, None)
                if h != BAD:
                    k32.CloseHandle(h)
                return h != BAD
            print("loaded", k32.GetTickCount() > 0)
            print("outside", opened({outside!r}, True))
            print("sealed", opened({sealed!r}, False))
            print("state", opened({state!r}, False))
            print("own", opened('mine.txt', True))
            """)
        self.assertIn("loaded True", result.stdout, result.stderr)
        self.assertIn("outside False", result.stdout)
        self.assertIn("sealed False", result.stdout)
        self.assertIn("state False", result.stdout)
        self.assertIn("own True", result.stdout, "its own folder it may write")
        self.assertFalse(self.outside.exists())

    def test_the_child_cannot_read_a_sealed_folder_or_the_apps_state(self) -> None:
        for path in (self.other / "answer.txt", self.internal / "settings.json"):
            with self.subTest(path=path.name):
                said = self.child(f"print(open({path.as_posix()!r}).read())")
                self.assertNotEqual(said["rc"], 0, said)
                self.assertNotIn("answer", said["out"])
                self.assertNotIn("secret", said["out"])
                self.assertIn("SandboxViolation", said["err"])

    def test_its_own_children_follow_the_same_rule(self) -> None:
        target = self.outside.as_posix()
        grandchild = f"open({target!r}, 'w').write('x')"
        said = self.child("import subprocess, sys; "
                          f"r = subprocess.run([sys.executable, '-c', {grandchild!r}], "
                          "capture_output=True, text=True); "
                          "print(r.returncode); print(r.stderr)")
        self.assertIn("SandboxViolation", said["out"], said)
        self.assertFalse(self.outside.exists())

    def test_other_programs_start_where_the_system_alone_confines_them(self) -> None:
        """The user, 2026-10-25: a shell, git - where the system keeps every
        rule on its own (an AppContainer, Seatbelt, bubblewrap); under
        Landlock still refused."""
        program = ["cmd", "/d", "/c", "echo hi"] if WINDOWS else ["sh", "-c", "echo hi"]
        result = self.run_code(f"""
            import subprocess
            done = subprocess.run({program!r}, capture_output=True, text=True)
            print(done.returncode, done.stdout.strip())
            """)
        if STATUS.get("backend") in ("appcontainer", "seatbelt", "bubblewrap"):
            self.assertTrue(result.ok, result.stderr)
            self.assertIn("0 hi", result.stdout)
        else:
            self.assertFalse(result.ok)
            self.assertIn("only Python child processes may be started", result.violation)

    def test_another_program_writes_no_more_than_the_run(self) -> None:
        if STATUS.get("backend") not in ("appcontainer", "seatbelt", "bubblewrap"):
            self.skipTest("other programs do not start here")
        target = os.path.relpath(self.outside, self.folder)
        program = (["cmd", "/d", "/c", f"echo x> {target}"] if WINDOWS
                   else ["sh", "-c", f"echo x > '{self.outside}'"])
        self.run_code(f"""
            import subprocess
            subprocess.run({program!r})
            """)
        self.assertFalse(self.outside.exists())

    def test_a_shell_is_refused_with_the_hint(self) -> None:
        result = self.run_code("""
            import subprocess, sys
            subprocess.run(f'"{sys.executable}" -c "print(1)"', shell=True)
            """)
        self.assertFalse(result.ok)
        self.assertIn("pass the command as a list", result.violation)

    def test_os_system_stays_refused(self) -> None:
        result = self.run_code("import os\nos.system('echo hi')\n")
        self.assertFalse(result.ok)
        self.assertIn("os.system", result.violation)
        self.assertIn("subprocess.run([sys.executable", result.violation)

    def test_python_dash_m_pytest_runs_the_runs_tests(self) -> None:
        (self.folder / "test_tiny.py").write_text(
            "def test_adds():\n    assert 1 + 1 == 2\n", encoding="utf-8")
        result = self.run_code("""
            import json, subprocess, sys
            done = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'test_tiny.py'],
                                  capture_output=True, text=True, timeout=180)
            print(json.dumps({'rc': done.returncode, 'out': done.stdout,
                              'err': done.stderr}))
            """, timeout=240)
        said = _last_json(result.stdout)
        self.assertEqual(said["rc"], 0, said)
        self.assertIn("1 passed", said["out"])

    def test_stdin_cwd_and_check_output_work_as_usual(self) -> None:
        (self.folder / "sub").mkdir()
        result = self.run_code("""
            import json, subprocess, sys
            upper = subprocess.run([sys.executable, '-c',
                                    'import sys; print(sys.stdin.read().upper())'],
                                   input='quiet', capture_output=True, text=True).stdout
            where = subprocess.check_output([sys.executable, '-c',
                                             'import os; print(os.getcwd())'],
                                            cwd='sub', text=True)
            print(json.dumps({'upper': upper.strip(), 'where': where.strip()}))
            """)
        said = _last_json(result.stdout)
        self.assertEqual(said["upper"], "QUIET", result.stderr)
        self.assertEqual(Path(said["where"]).name, "sub")

    def test_a_module_in_the_folder_runs_with_dash_m(self) -> None:
        (self.folder / "app.py").write_text("import sys\nprint('app', sys.argv[1:])\n")
        said = self.child("import subprocess, sys; "
                          "print(subprocess.run([sys.executable, '-m', 'app', 'x'], "
                          "capture_output=True, text=True).stdout)")
        self.assertIn("app ['x']", said["out"], said)

    @unittest.skipUnless(WINDOWS, "multiprocessing spawns only on Windows and macOS")
    def test_multiprocessing_spawn_runs_guarded(self) -> None:
        target = self.outside.as_posix()
        result = self.run_code(f"""
            import multiprocessing as mp
            def work():
                print('in the worker', flush=True)
                open({target!r}, 'w').write('x')
            if __name__ == '__main__':
                p = mp.Process(target=work); p.start(); p.join()
                print('worker exit', p.exitcode)
            """)
        self.assertIn("in the worker", result.stdout, result.stderr)
        self.assertIn("worker exit 1", result.stdout)
        self.assertIn("SandboxViolation", result.stderr)
        self.assertFalse(self.outside.exists())

    def test_a_child_left_running_ends_with_the_run(self) -> None:
        marker = self.folder / "still-running.txt"
        result = self.run_code(f"""
            import subprocess, sys
            subprocess.Popen([sys.executable, '-c',
                              "import time; time.sleep(5); open({marker.as_posix()!r}, 'w').write('x')"])
            print('started')
            """)
        self.assertTrue(result.ok, result.stderr)
        time.sleep(8)
        self.assertFalse(marker.exists(), "a child outlived the run")

    def test_a_child_running_at_the_timeout_is_killed_with_the_run(self) -> None:
        marker = self.folder / "after-timeout.txt"
        started = time.monotonic()
        result = self.run_code(f"""
            import subprocess, sys, time
            subprocess.Popen([sys.executable, '-c',
                              "import time; time.sleep(6); open({marker.as_posix()!r}, 'w').write('x')"])
            time.sleep(60)
            """, timeout=4)
        self.assertTrue(result.timed_out)
        self.assertLess(time.monotonic() - started, 30)
        time.sleep(8)
        self.assertFalse(marker.exists(), "a child outlived the run's timeout")


class WithoutOSIsolationNoChildStartsTests(_Run):
    """The fallback: the guard alone, so no child at all - as before."""

    sandbox_enabled = False

    def test_a_python_child_is_refused_and_says_what_instead(self) -> None:
        target = self.outside.as_posix()
        result = self.run_code(f"""
            import subprocess, sys
            subprocess.run([sys.executable, '-c', "open({target!r}, 'w').write('x')"])
            """)
        self.assertFalse(result.ok)
        self.assertIn("no child process can start in this run", result.violation)
        self.assertIn("pytest.main", result.violation)
        self.assertFalse(self.outside.exists())

    def test_the_environment_cannot_claim_isolation_after_the_fact(self) -> None:
        target = self.outside.as_posix()
        result = self.run_code(f"""
            import os, subprocess, sys
            os.environ['AGENT_OS_ISOLATION'] = 'appcontainer'
            subprocess.run([sys.executable, '-c', "open({target!r}, 'w').write('x')"])
            """)
        self.assertFalse(result.ok)
        self.assertIn("no child process can start", result.violation)
        self.assertFalse(self.outside.exists())

    def test_native_code_stays_shut_without_the_systems_confinement(self) -> None:
        result = self.run_code("""
            try:
                import ctypes
                ctypes.windll.kernel32.GetTickCount() if hasattr(ctypes, 'windll') \\
                    else ctypes.CDLL(None)
                print("LOADED")
            except Exception as exc:
                print("refused", type(exc).__name__)
            """)
        self.assertIn("refused", result.stdout, result.stderr)
        self.assertNotIn("LOADED", result.stdout)

    def test_what_builds_on_popen_still_imports_and_popen_still_refuses(self) -> None:
        """Popen refused as a CLASS: asyncio - and uvicorn, httpx, FastAPI's
        test client with it - subclass it as they are imported; a function
        in its place broke them all. Reaching past it to the real one starts
        nothing either: the choke point under it is closed."""
        target = self.outside.as_posix()
        result = self.run_code(f"""
            import asyncio, asyncio.subprocess, subprocess, sys
            print("loop", 42 if issubclass(subprocess.Popen, subprocess.Popen.__mro__[1]) else 0)
            for make in (subprocess.Popen, subprocess.Popen.__mro__[1]):
                try:
                    make([sys.executable, '-c', "open({target!r}, 'w').write('x')"])
                    print("STARTED")
                except Exception as exc:
                    print("refused", type(exc).__name__)
            """)
        self.assertIn("loop 42", result.stdout, result.stderr)
        self.assertEqual(result.stdout.count("refused"), 2, result.stdout + result.stderr)
        self.assertNotIn("STARTED", result.stdout)
        self.assertFalse(self.outside.exists())

    def test_multiprocessing_is_refused_too(self) -> None:
        result = self.run_code("""
            import multiprocessing as mp
            if __name__ == '__main__':
                p = mp.Process(target=print); p.start(); p.join()
            """)
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.violation)


class TheAgentsAreToldTests(unittest.TestCase):

    def test_what_may_start_is_said_as_the_system_allows_it(self) -> None:
        from unittest import mock
        from personalized_agent.agent.work import runner
        # Said in the environment both prompts carry (`environment_rules`).
        with mock.patch.object(runner, "native_code_loads", return_value=False):
            said = " ".join(runner.environment_rules("C:/work", "NETWORK RULES").split())
        self.assertIn("CHILD PROCESSES: Python only", said)
        self.assertIn("each runs under these same rules", said)
        self.assertIn("No other program starts", said)
        self.assertIn("NETWORK: NETWORK RULES", said)
        with mock.patch.object(runner, "native_code_loads", return_value=True):
            said = " ".join(runner.environment_rules("C:/work", "NETWORK RULES").split())
        self.assertIn("COMMANDS: run_command", said)
        self.assertIn("reaches no network", said)
        self.assertIn("git_fetch", said)
        self.assertNotIn("No other program starts", said)
        for name in ("AGENT_SYSTEM", "ALONE_SYSTEM"):
            with self.subTest(prompt=name):
                self.assertIn("{environment}", getattr(runner, name))

    def test_where_native_code_cannot_load_they_are_told_what_will_not(self) -> None:
        from unittest import mock
        from personalized_agent.agent.work import runner
        from personalized_agent.runtime import isolation
        for status, told in (({"isolated": True, "backend": "appcontainer"}, False),
                             ({"isolated": True, "backend": "landlock"}, True),
                             ({"isolated": False, "backend": "none"}, True)):
            with self.subTest(status=status), \
                    mock.patch.object(isolation, "isolation_status", return_value=status):
                rule = runner.native_rule()
                if told:
                    for said in ("uvicorn", "click", "colorama", "wsgiref", "test_client()"):
                        self.assertIn(said, rule)
                else:
                    self.assertEqual(rule, "")


if __name__ == "__main__":
    unittest.main()
