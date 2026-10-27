"""THE WORK'S COMMANDS, WHAT IT FETCHES, WHAT IT WATCHES, ITS NOTEBOOKS AND
ITS HELPERS (the user, 2026-10-25: as Claude Code has them - in the sandbox):

- run_command: a shell command (cmd on Windows; bash or sh elsewhere), run
  from guarded code, so the system's rules hold for it and all it starts -
  offered only where the system alone confines every program;
- the guard lets another program start only there (an AppContainer,
  Seatbelt, bubblewrap - offline), never under Landlock;
- download, git_fetch and npm: fetched OUTSIDE, run inside - only https
  addresses that exist, no configuration of the user's, nothing run while
  fetched, npm from the registry alone with its scripts off;
- wait_for: returns the moment a job ends, prints a line, a file appears;
  every result says which jobs ended since;
- edit_notebook: one cell replaced, inserted or deleted, the rest kept;
- delegate: a task handed to a helper agent - at most three at once, one
  worker, none of its own.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from support import TemporaryAgent

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "personalized_agent" / "runtime"))

import guard  # noqa: E402  - the guard on its own, as children load it

from personalized_agent.agent.work import fetching, notebooks, parallel  # noqa: E402
from personalized_agent.agent.work import tools as T  # noqa: E402
from personalized_agent.agent.work.runner import Run  # noqa: E402
from personalized_agent.runtime import isolation  # noqa: E402
from personalized_agent.runtime.sandbox import ScriptResult  # noqa: E402

STATUS = isolation.isolation_status()
NATIVE = STATUS.get("isolated") and STATUS.get("backend") in ("appcontainer", "seatbelt",
                                                              "bubblewrap")
WINDOWS = sys.platform.startswith("win")


class _WithTools(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        app = self.agent.app
        unit = {"id": "unit_cmds", "name": "A pledge form", "kind": "task"}
        self.folder = Path(app.work.folder_of(unit))
        self.runtime = app.work._runtime(unit, self.folder)
        self.runtime.mkdir(self.folder)
        self.tools = T.WorkTools(self.runtime)

    def tearDown(self) -> None:
        self.agent.cleanup()


# ---------------------------------------------------------------------------
class AnotherProgramStartsOnlyWhereTheSystemKeepsTheRulesTests(unittest.TestCase):

    def setUp(self) -> None:
        here = {os.path.normcase(os.path.abspath(sys.executable))}
        patcher = mock.patch.multiple(guard, _os_backend="appcontainer", _offline=True,
                                      _interpreters=here, _browser_path="")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_shell_or_git_starts_as_asked_python_is_still_guarded(self) -> None:
        for program in ("cmd", "git", "C:/Program Files/nodejs/node.exe", "/bin/sh"):
            with self.subTest(program=program):
                self.assertTrue(guard.other_program(program))
        self.assertFalse(guard.other_program("python"))
        self.assertFalse(guard.other_program(sys.executable))

    def test_not_under_landlock_offline_or_not(self) -> None:
        with mock.patch.object(guard, "_os_backend", "landlock"):
            self.assertFalse(guard.other_program("git"))
        with mock.patch.object(guard, "_offline", False):
            self.assertFalse(guard.other_program("git"))
        with mock.patch.object(guard, "_os_backend", "none"):
            self.assertFalse(guard.other_program("git"))


# ---------------------------------------------------------------------------
class RunCommandTests(_WithTools):

    def test_offered_only_where_the_system_confines_every_program(self) -> None:
        with mock.patch.object(T, "commands_allowed", return_value=False):
            self.assertNotIn("run_command", [one.name for one in self.tools.specs()])
            said = self.tools.run_command({"command": "echo hi"})
            self.assertIn("cannot run on this computer", said["error"])
        with mock.patch.object(T, "commands_allowed", return_value=True):
            self.assertIn("run_command", [one.name for one in self.tools.specs()])
        with mock.patch.object(isolation, "isolation_status",
                               return_value={"isolated": True, "backend": "landlock"}):
            self.assertFalse(T.commands_allowed())
        self.runtime.sandbox_enabled = False
        self.assertFalse(T.commands_allowed(self.runtime))

    def test_windows_has_cmd_alone(self) -> None:
        if WINDOWS:
            self.assertEqual(list(T.shells()), ["cmd"])
            self.assertEqual(T.SHELLS_WINDOWS, ("cmd",))

    def test_the_command_is_a_script_run_from_guarded_code(self) -> None:
        ran = {}

        def run(code, timeout=0, sys_path=None, environment=None, readable=None):
            ran.update(code=code, timeout=timeout, readable=readable)
            return ScriptResult(ok=True, returncode=0, stdout="hi\n", stderr="",
                                isolation="test")
        self.runtime.run_python_code = run
        with mock.patch.object(T, "commands_allowed", return_value=True), \
                mock.patch.object(T, "shells", return_value={"cmd": "C:/W/cmd.exe",
                                                             "sh": "/bin/sh"}), \
                mock.patch.object(T, "programs_to_read", return_value=[Path("/progs")]):
            shell = "cmd" if WINDOWS else "sh"
            said = self.tools.run_command({"command": 'echo "a & b"\necho two',
                                           "shell": shell, "timeout": 30})
        self.assertEqual(said["returncode"], 0)
        self.assertEqual(said["shell"], shell)
        self.assertEqual(ran["timeout"], 30)
        self.assertEqual(ran["readable"], [Path("/progs")])
        scripts = list((self.folder / ".agent_runtime" / "commands").iterdir())
        self.assertEqual(len(scripts), 1)
        text = scripts[0].read_bytes().decode("utf-8")
        self.assertIn('echo "a & b"', text, "the command as written: no quoting to get wrong")
        if shell == "cmd":
            self.assertTrue(text.startswith("@echo off\r\nchcp 65001 >nul\r\n"))
            # By its path from where cmd starts: a whole path is refused in a container.
            self.assertIn(repr(os.path.relpath(scripts[0], self.folder)), ran["code"])
        self.assertIn("stdin=subprocess.DEVNULL", ran["code"])
        self.assertIn("GIT_TERMINAL_PROMPT", ran["code"])

    def test_cwd_only_inside_the_work(self) -> None:
        with mock.patch.object(T, "commands_allowed", return_value=True):
            said = self.tools.call("run_command", {"command": "echo hi",
                                                   "cwd": str(Path(tempfile.gettempdir()))})
        self.assertIn("error", said)

    def test_programs_on_path_under_appdata_are_named_alone(self) -> None:
        store = Path(tempfile.mkdtemp(prefix="pa-store-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(store, ignore_errors=True))
        (store / "node").mkdir()
        (store / "WindowsApps").mkdir()
        path = os.pathsep.join([str(store / "node"), str(store / "WindowsApps"), "/usr/bin"])
        with mock.patch.dict(os.environ, {"PATH": path}), \
                mock.patch("personalized_agent.runtime.isolation.credential_stores",
                           return_value=[store]):
            self.assertEqual(T.programs_to_read(), [store / "node"])

    @unittest.skipUnless(WINDOWS, "PowerShell and Git Bash: Windows")
    def test_powershell_and_bash_are_refused_at_once_saying_why(self) -> None:
        ran = []
        self.runtime.run_python_code = lambda *a, **k: ran.append(a)
        with mock.patch.object(T, "commands_allowed", return_value=True):
            for command in ('powershell -NoProfile -Command "Get-Content a.py"',
                            "dir & pwsh -c x", 'echo a\r\n"C:\\Windows\\System32\\'
                            'WindowsPowerShell\\v1.0\\powershell.exe" -c x',
                            "start /b powershell.exe x", "cmd /c powershell x"):
                with self.subTest(command=command):
                    said = self.tools.run_command({"command": command})
                    self.assertIn("PowerShell does not work in this sandbox", said["error"])
            for command in ("bash -lc ls", "git status && bash x.sh", "call wsl ls",
                            "type a | sh"):
                with self.subTest(command=command):
                    self.assertIn("Git Bash", self.tools.run_command({"command": command})["error"])
            said = self.tools.run_command({"command": "dir", "shell": "powershell"})
            self.assertIn("PowerShell does not work", said["error"])
        self.assertEqual(ran, [], "nothing was run")
        # Named, not called: no refusal.
        for command in ('findstr /n /c:"bash" notes.txt', "echo sh and powershell",
                        "git push --dry-run", "python -m pytest -k shell"):
            with self.subTest(command=command):
                self.assertEqual(T.shell_not_here(command), "")

    @unittest.skipUnless(WINDOWS, "the 0o700 folders: Windows")
    def test_python_a_command_starts_is_given_the_guards_mkdir_mending_alone(self) -> None:
        ran = {}

        def run(code, timeout=0, sys_path=None, environment=None, readable=None):
            ran.update(code=code)
            return ScriptResult(ok=True, returncode=0, stdout="", stderr="", isolation="test")
        self.runtime.run_python_code = run
        with mock.patch.object(T, "commands_allowed", return_value=True), \
                mock.patch.object(T, "programs_to_read", return_value=[]):
            self.tools.run_command({"command": "python -m pytest"})
        fix = self.folder / ".agent_runtime" / "home" / T.COMMAND_PYTHON_FOLDER
        self.assertEqual((fix / "sitecustomize.py").read_text(encoding="utf-8"),
                         T.COMMAND_PYTHON_FIX)
        self.assertIn(repr({"PYTHONPATH": str(fix)}), ran["code"])
        # In a Python of its own: 0o700 made plain, other modes as asked, and a
        # sitecustomize further on the path still loaded.
        import subprocess
        other = Path(tempfile.mkdtemp(prefix="pa-site-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(other, ignore_errors=True))
        probe = ("import os\n"
                 "seen = []\n"
                 "os.mkdir.__globals__['_plain_mkdir'] = lambda p, m=0o777, *a, **k: "
                 "seen.append(m)\n"
                 "os.mkdir('a', 0o700); os.mkdir('b', 0o755)\n"
                 "print(seen, os.environ.get('CHAINED'))\n")
        for chained in (False, True):
            with self.subTest(chained=chained):
                if chained:
                    (other / "sitecustomize.py").write_text(
                        "import os\nos.environ['CHAINED'] = 'yes'\n", encoding="utf-8")
                env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(fix), str(other)]))
                env.pop("CHAINED", None)
                out = subprocess.run([sys.executable, "-c", probe], env=env, cwd=str(other),
                                     capture_output=True, text=True, timeout=60)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(out.stderr, "")
                self.assertEqual(out.stdout.split(), ["[511,", "493]",
                                                      "yes" if chained else "None"])

    @unittest.skipUnless(NATIVE and WINDOWS, f"an AppContainer: {STATUS['why']}")
    def test_in_the_container_a_0o700_folder_is_the_commands_own(self) -> None:
        exe = sys.executable
        with mock.patch.object(T, "programs_to_read", return_value=[]):
            said = self.tools.run_command({"command": (
                f'"{exe}" -c "import os, tempfile; d = tempfile.mkdtemp(dir=\'.\'); '
                f'open(os.path.join(d, \'x\'), \'w\').write(\'1\'); print(\'made\')"'),
                "timeout": 120})
            self.assertEqual(said["returncode"], 0, said)
            self.assertIn("made", said["stdout"])
            # Without it (-I: no PYTHONPATH), the folder it made is refused to it.
            said = self.tools.run_command({"command": (
                f'"{exe}" -I -c "import os; os.mkdir(\'bare\', 0o700); '
                f'open(\'bare/x\', \'w\').write(\'1\')"'), "timeout": 120})
            self.assertNotEqual(said["returncode"], 0, said)
            self.assertIn("PermissionError", said["stdout"])

    @unittest.skipUnless(NATIVE, f"no system confinement for programs here: {STATUS['why']}")
    def test_a_real_command_runs_and_writes_no_more_than_the_work(self) -> None:
        with mock.patch.object(T, "programs_to_read", return_value=[]):
            said = self.tools.run_command({"command": "echo hi 中文", "timeout": 60})
            self.assertEqual(said["returncode"], 0, said)
            self.assertIn("hi 中文", said["stdout"])
            said = self.tools.run_command({"command": "exit /b 7" if WINDOWS else "exit 7"})
            self.assertEqual(said["returncode"], 7)
            outside = self.folder.parent / "outside.txt"
            target = os.path.relpath(outside, self.folder)
            self.tools.run_command({"command": f"echo x> {target}" if WINDOWS
                                    else f"echo x > '{outside}'"})
            self.assertFalse(outside.exists())
            said = self.tools.run_command({"command": "echo made> made.txt"})
            self.assertTrue((self.folder / "made.txt").exists())
            self.assertIn(str(self.folder / "made.txt"), said["files_changed"])


# ---------------------------------------------------------------------------
class FetchedOutsideTests(_WithTools):

    def test_npm_names_and_ranges_only(self) -> None:
        allowed, refused = fetching.check_npm(
            ["react", "lodash@^4.17.21", "@types/node@20", "left-pad@latest",
             "evil@http://x/y.tgz", "a/b", "file:../x", "git+https://x/y", "x@npm:y",
             "c@1 && calc"])
        self.assertEqual(allowed, ["react", "lodash@^4.17.21", "@types/node@20",
                                   "left-pad@latest"])
        self.assertEqual(len(refused), 6)

    def test_a_package_json_that_fetches_elsewhere_is_refused(self) -> None:
        self.assertEqual(fetching.check_package_json(
            {"dependencies": {"react": "^18.2.0", "@scope/x": "1.x"}}), [])
        wrong = fetching.check_package_json(
            {"dependencies": {"a": "github:me/a", "b": "file:../b", "c": "https://x/c.tgz"},
             "devDependencies": {"d": "git+ssh://x"}, "workspaces": ["p/*"],
             "overrides": {"e": "1"}})
        self.assertEqual(len(wrong), 6)

    def test_a_lock_resolving_elsewhere_is_refused(self) -> None:
        good = {"packages": {"": {}, "node_modules/a": {
            "resolved": "https://registry.npmjs.org/a/-/a-1.0.0.tgz"}}}
        bad = {"packages": {"node_modules/b": {"resolved": "https://evil.example/b.tgz"},
                            "node_modules/c": {"resolved": "file:../c", "link": True}}}
        self.assertEqual(fetching.check_lock(good), [])
        self.assertEqual(len(fetching.check_lock(bad)), 2)

    def test_npm_runs_in_a_stage_with_no_configuration_and_no_scripts(self) -> None:
        project = self.folder / "web"
        project.mkdir()
        (project / "package.json").write_text('{"name": "web", "dependencies": {}}',
                                              encoding="utf-8")
        (project / ".npmrc").write_text("registry=https://evil.example/\nprefix=C:/\n",
                                        encoding="utf-8")
        ran = {}

        def run(command, cwd, env, timeout):
            ran.update(command=list(command), cwd=Path(cwd), env=env)
            stage = Path(cwd)
            self.assertEqual(sorted(one.name for one in stage.iterdir()), ["package.json"],
                             "the stage holds package.json alone - no .npmrc of the project")
            (stage / "node_modules" / "react").mkdir(parents=True)
            (stage / "node_modules" / "react" / "package.json").write_text(
                '{"version": "18.3.1"}', encoding="utf-8")
            (stage / "package.json").write_text(
                '{"name": "web", "dependencies": {"react": "^18.3.1"}}', encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        with mock.patch.object(fetching, "npm_program", return_value=["node", "npm-cli.js"]), \
                mock.patch.object(fetching, "_run", run):
            said = fetching.npm_install(project, ["react@^18"], self.folder / "stage",
                                        self.folder / "cache", self.folder / "home")
        self.assertNotIn("error", said, said)
        command = ran["command"]
        for flag in ("--ignore-scripts", "--userconfig", "--globalconfig", "--prefix"):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("--registry") + 1], fetching.NPM_REGISTRY)
        self.assertEqual(ran["env"]["HOME"], str(self.folder / "home"))
        self.assertTrue((project / "node_modules" / "react" / "package.json").exists())
        self.assertEqual(said["installed"], [{"name": "react", "version": "18.3.1"}])
        self.assertIn('"react"', (project / "package.json").read_text(encoding="utf-8"))

    def test_git_clones_https_alone_with_nothing_of_the_users(self) -> None:
        home = self.folder / "home"
        for url in ("http://x.example/a.git", "ssh://git@x/a", "https://me:pw@x.example/a",
                    "file:///C:/repo"):
            with self.subTest(url=url):
                self.assertIn("error", fetching.git_clone(url, self.folder / "a", home))
        ran = []

        def run(command, cwd, env, timeout):
            ran.append((list(command), env))
            if "clone" in command:
                Path(command[-1]).mkdir()
                (Path(command[-1]) / "README.md").write_text("hi", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="abc123 first\n", stderr="")
        with mock.patch.object(fetching, "git_program", return_value="git"), \
                mock.patch.object(fetching, "_run", run):
            said = fetching.git_clone("https://github.example/me/repo.git",
                                      self.folder / "repo", home, ref="main")
        self.assertNotIn("error", said, said)
        command, env = ran[0]
        joined = " ".join(command)
        for part in ("protocol.allow=never", "protocol.https.allow=always", "core.hooksPath=",
                     "core.symlinks=false", "credential.helper=", "--no-recurse-submodules",
                     "--depth 1", "--branch main"):
            self.assertIn(part, joined)
        self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertNotIn("push", joined)

    def test_a_download_is_https_capped_and_never_half_kept(self) -> None:
        self.assertIn("error", fetching.download("http://x.example/f.zip",
                                                 self.folder / "f.zip"))

        class _Response:
            def __init__(self, size):
                self.left = size
                self.headers = {"Content-Type": "application/zip"}

            def geturl(self):
                return "https://cdn.example/f.zip"

            def read(self, most):
                if self.left <= 0:
                    return b""
                part = min(most, self.left)
                self.left -= part
                return b"x" * part

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        with mock.patch.object(fetching.urllib.request, "urlopen",
                               return_value=_Response(3 * 1024 * 1024)):
            said = fetching.download("https://x.example/f.zip", self.folder / "f.zip",
                                     most=2 * 1024 * 1024)
        self.assertIn("larger than", said["error"])
        self.assertFalse((self.folder / "f.zip").exists())
        self.assertFalse((self.folder / "f.zip.part").exists())
        with mock.patch.object(fetching.urllib.request, "urlopen",
                               return_value=_Response(1000)):
            said = fetching.download("https://x.example/f.zip", self.folder / "f.zip")
        self.assertEqual(said["bytes"], 1000)

    def test_the_tools_open_only_an_address_that_exists(self) -> None:
        for tool in ("download", "git_fetch"):
            with self.subTest(tool=tool):
                said = self.tools.call(tool, {"url": "https://made-up.example/x"})
                self.assertIn("did not come from a search", said["error"])
        self.tools.know_addresses("see https://github.example/me/repo", "web",
                                  "github.example")
        with mock.patch.object(fetching, "git_clone",
                               return_value={"cloned": "x", "path": "p"}) as clone:
            self.tools.call("git_fetch", {"url": "https://github.example/me/repo"})
        self.assertEqual(clone.call_args[0][1], self.folder / "repo")


# ---------------------------------------------------------------------------
class WaitForTests(_WithTools):

    def _job(self, ident: str, status: str = "running") -> dict:
        folder = self.folder / ".agent_runtime" / "jobs" / ident
        folder.mkdir(parents=True)
        (folder / "output.log").write_text("", encoding="utf-8")
        job = {"id": ident, "status": status, "started": time.time(), "ended": 0.0,
               "timeout": 60, "folder": folder, "result": None}
        self.tools.jobs[ident] = job
        return job

    def test_it_returns_the_moment_the_line_is_printed(self) -> None:
        job = self._job("job_1")

        def later():
            time.sleep(1.5)
            (job["folder"] / "output.log").write_text("booting\nlistening on :8765\n",
                                                      encoding="utf-8")
        threading.Thread(target=later, daemon=True).start()
        began = time.time()
        said = self.tools.wait_for({"job": "job_1", "output": "listening on", "timeout": 30})
        self.assertTrue(said["met"])
        self.assertEqual(said["line"], "listening on :8765")
        self.assertLess(time.time() - began, 10)

    def test_a_file_a_job_ending_and_the_time_running_out(self) -> None:
        target = self.folder / "built.txt"
        threading.Timer(1.0, lambda: target.write_text("ok")).start()
        self.assertTrue(self.tools.wait_for({"file": str(target), "timeout": 20})["met"])
        job = self._job("job_2")
        threading.Timer(1.0, lambda: job.update(status="done", ended=time.time())).start()
        said = self.tools.wait_for({"job": "job_2", "timeout": 20})
        self.assertTrue(said["met"])
        self.assertIn("ended", said["what"])
        self._job("job_3")
        said = self.tools.wait_for({"job": "job_3", "output": "never", "timeout": 2})
        self.assertFalse(said["met"])
        self.assertIn("still waiting", said["what"])

    def test_every_result_says_once_which_jobs_ended(self) -> None:
        job = self._job("job_4")
        job.update(status="done", ended=time.time(), result={"stdout": "a\nlast line\n"})
        said = self.tools.call("list_dir", {"path": str(self.folder)})
        self.assertEqual(said["jobs_ended"][0]["job"], "job_4")
        self.assertEqual(said["jobs_ended"][0]["last_line"], "last line")
        self.assertNotIn("jobs_ended", self.tools.call("list_dir", {"path": str(self.folder)}))

    def test_on_a_job_it_waits_until_it_ends_unless_told_otherwise(self) -> None:
        self._job("job_10")
        target = self.folder / "never.txt"
        asked = []
        with mock.patch.object(self.tools, "_watching",
                               side_effect=lambda jobs, f, t, u, seconds: asked.append(seconds)
                               or {"met": False}):
            self.tools.wait_for({"job": "job_10"})
            self.tools.wait_for({"job": "job_10", "timeout": 30})
            self.tools.wait_for({"jobs": ["job_10"], "timeout": 10 ** 9})
            self.tools.wait_for({"file": str(target)})
            self.tools.wait_for({"file": str(target), "timeout": 10 ** 9})
        self.assertEqual(asked, [T.WAIT_JOB_MOST, 30, T.WAIT_JOB_MOST, 60, T.WAIT_MOST])
        self.assertGreater(T.WAIT_JOB_MOST, T.JOB_MOST_SECONDS, "past the longest job")

    def test_several_jobs_it_returns_when_the_first_ends(self) -> None:
        self._job("job_7")
        last = self._job("job_8")
        threading.Timer(1.0, lambda: last.update(status="failed", ended=time.time(),
                                                 result={"ok": False})).start()
        began = time.time()
        said = self.tools.wait_for({"jobs": ["job_7", "job_8"]})
        self.assertLess(time.time() - began, 10)
        self.assertTrue(said["met"])
        self.assertEqual(said["what"], "job_8 ended (failed)")
        self.assertEqual(said["still_running"], ["job_7"])
        self.assertEqual([(one["job"], one["status"]) for one in said["jobs"]],
                         [("job_7", "running"), ("job_8", "failed")])
        self.assertEqual(said["jobs"][1]["result"], {"ok": False}, "the one that ended, whole")
        self.assertNotIn("printed_so_far", said["jobs"][0])
        # One that has ended already: at once.
        said = self.tools.wait_for({"job": "job_7", "jobs": ["job_8"]})
        self.assertLess(said["waited"], 2)
        self.assertEqual(self.tools.jobs["job_7"].get("waiting"), {}, "no wait left behind")
        self.assertIn("one job", self.tools.wait_for({"jobs": ["job_7", "job_8"],
                                                      "output": "x"})["error"])
        self.assertIn("no job job_99", self.tools.wait_for({"jobs": ["job_7", "job_99"]})["error"])

    def test_a_job_still_running_points_to_wait_for(self) -> None:
        self._job("job_5")
        said = self.tools.job_status({"job": "job_5"})
        self.assertEqual(said["status"], "running")
        self.assertIn("wait_for job=job_5", said["note"])
        self.tools.only = {"run_python", "job_status"}
        self.assertNotIn("note", self.tools.job_status({"job": "job_5"}))

    def test_a_job_is_waited_for_once_by_the_same_agent(self) -> None:
        job = self._job("job_6")
        first = {}
        waiting = threading.Thread(
            target=lambda: first.update(self.tools.job_status({"job": "job_6", "wait": 20})),
            daemon=True)
        waiting.start()
        time.sleep(1.0)
        began = time.time()
        said = self.tools.job_status({"job": "job_6", "wait": 20})
        self.assertIn("Already being waited for", said["note"])
        said = self.tools.wait_for({"job": "job_6", "timeout": 20})
        self.assertFalse(said["met"])
        self.assertIn("Already being waited for", said["what"])
        self.assertLess(time.time() - began, 5, "the second calls did not wait")
        # A helper's tools wait on their own.
        helper = T.WorkTools(self.runtime)
        helper.jobs = self.tools.jobs
        said = helper.job_status({"job": "job_6", "wait": 1})
        self.assertNotIn("Already", said.get("note", ""))
        job.update(status="done", ended=time.time(), result={"ok": True})
        waiting.join(10)
        self.assertEqual(first["status"], "done")
        self.assertNotIn("note", first)
        self.assertEqual(job["waiting"], {}, "no one is waiting any more")
        self.assertEqual(self.tools.job_status({"job": "job_6", "wait": 5})["status"], "done")

    def test_waiting_takes_no_place_among_the_tools(self) -> None:
        gate = parallel.ToolGate()
        with gate.alone():
            with gate.hold("wait_for"), gate.hold("delegate"), gate.hold("job_status"):
                pass


# ---------------------------------------------------------------------------
NOTEBOOK = {"nbformat": 4, "nbformat_minor": 5, "metadata": {"kernelspec": {"name": "py"}},
            "cells": [
                {"id": "aa11", "cell_type": "markdown", "metadata": {}, "source": ["# Title\n"]},
                {"id": "bb22", "cell_type": "code", "metadata": {"tags": ["x"]},
                 "execution_count": 3, "source": ["print(1)\n"],
                 "outputs": [{"output_type": "stream", "name": "stdout", "text": ["1\n"]},
                             {"output_type": "display_data",
                              "data": {"image/png": "AAA", "text/plain": ["<Figure>"]}}]}]}


class NotebookTests(_WithTools):

    def setUp(self) -> None:
        super().setUp()
        self.path = self.folder / "n.ipynb"
        self.path.write_text(json.dumps(NOTEBOOK), encoding="utf-8")

    def test_read_as_its_cells_with_what_they_printed(self) -> None:
        text = self.tools.read_file({"path": str(self.path)})["text"]
        self.assertIn("--- cell 1 [bb22] (code) [ran 3] ---", text)
        self.assertIn("print(1)", text)
        self.assertIn("[image/png]", text)

    def test_one_cell_replaced_inserted_deleted_the_rest_kept(self) -> None:
        said = self.tools.call("edit_notebook", {"path": str(self.path), "cell": "bb22",
                                                 "source": "print(2)\nprint(3)"})
        self.assertEqual(said["replaced"], 1)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        cell = data["cells"][1]
        self.assertEqual(cell["source"], ["print(2)\n", "print(3)"])
        self.assertEqual(cell["outputs"], [])
        self.assertIsNone(cell["execution_count"])
        self.assertEqual(cell["metadata"], {"tags": ["x"]})
        self.assertEqual(data["metadata"], NOTEBOOK["metadata"])
        said = self.tools.call("edit_notebook", {"path": str(self.path), "cell": "2",
                                                 "mode": "insert", "cell_type": "markdown",
                                                 "source": "end"})
        self.assertEqual(said["inserted"], 2)
        self.assertEqual(len(said["id"]), 8)
        self.tools.call("edit_notebook", {"path": str(self.path), "cell": "0",
                                          "mode": "delete"})
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual([one["cell_type"] for one in data["cells"]], ["code", "markdown"])

    def test_what_cannot_be_is_refused(self) -> None:
        for args in ({"cell": "9"}, {"cell": "zz"}, {"cell": "0", "mode": "move"},
                     {"cell": "0", "cell_type": "picture"}):
            with self.subTest(args=args):
                self.assertIn("error", self.tools.call("edit_notebook",
                                                       dict(args, path=str(self.path))))
        outside = Path(tempfile.gettempdir()) / "n.ipynb"
        self.assertIn("error", self.tools.call("edit_notebook",
                                               {"path": str(outside), "cell": "0"}))


# ---------------------------------------------------------------------------
class HelperTests(_WithTools):

    def _run(self, answer=None, wait: float = 0.0):
        run = SimpleNamespace(_helpers={"going": 0, "worker": False, "made": 0},
                              _helpers_lock=threading.Lock(), review_tools=object(),
                              asked=[])
        run._event = lambda *a, **k: None
        run._environment_text = lambda: "THE ENVIRONMENT"

        def ask(system, messages, tools, writing, rounds, who, quiet):
            run.asked.append({"system": system, "task": messages[0]["content"],
                              "tools": tools, "writing": writing, "who": who})
            time.sleep(wait)
            return dict(answer or {"done": True, "summary": "found it"})
        run._ask = ask
        run._helper_tools = lambda parent, kind, who, halted: Run._helper_tools(
            run, parent, kind, who, halted)
        run._tool_used = lambda *a, **k: None
        run._unconfined_for = lambda tools: None
        return run

    def test_a_helper_has_the_tools_but_delegate_and_the_same_sandbox(self) -> None:
        run = self._run()
        self.tools.delegate = lambda args: None
        said = Run._delegate(run, self.tools, ("explore", "worker"),
                             {"task": "find the pledge form", "kind": "explore"})
        self.assertEqual(said["summary"], "found it")
        self.assertEqual(said["helper"], "helper 1")
        asked = run.asked[0]
        self.assertIn("THE ENVIRONMENT", asked["system"])
        self.assertIn("READ AND SEARCH ONLY", asked["system"])
        self.assertFalse(asked["writing"])
        helper = asked["tools"]
        self.assertIs(helper.runtime, self.tools.runtime)
        self.assertIs(helper.guard, self.tools.guard)
        self.assertIsNone(helper.delegate, "no helper of its own")
        self.assertNotIn("delegate", [one.name for one in helper.specs()])
        self.assertIsNot(helper.gate, self.tools.gate, "a place of its own among the tools")
        self.assertEqual(run._helpers["going"], 0)

    def test_at_most_three_at_once_one_worker_and_only_what_is_allowed(self) -> None:
        run = self._run(wait=1.5)
        with mock.patch.object(parallel, "machine_busy", return_value=False):
            jobs = [Run._delegate(run, self.tools, ("explore", "worker"),
                                  {"task": f"t{n}", "kind": "worker" if n == 0 else "explore",
                                   "background": True}) for n in range(3)]
            self.assertTrue(all(one.get("job") for one in jobs), jobs)
            said = Run._delegate(run, self.tools, ("explore", "worker"), {"task": "t4"})
            self.assertIn("going already", said["error"])
        self.assertIn("only explores", Run._delegate(
            run, self.tools, ("explore",), {"task": "x", "kind": "worker"})["error"])
        said = self.tools.wait_for({"job": jobs[0]["job"], "timeout": 30})
        self.assertTrue(said["met"])
        self.assertEqual(said["job"]["result"]["summary"], "found it")
        with mock.patch.object(parallel, "machine_busy", return_value=False):
            run._helpers["worker"] = True
            self.assertIn("one at a time", Run._delegate(
                run, self.tools, ("explore", "worker"),
                {"task": "x", "kind": "worker"})["error"])

    def test_a_stopped_helper_is_refused_its_tools_and_answers(self) -> None:
        halted = {"halt": False}
        run = self._run()
        helper = Run._helper_tools(run, self.tools, "explore", "helper 9", halted)
        self.assertNotIn("error", helper.call("list_dir", {"path": str(self.folder)}))
        halted["halt"] = True
        self.assertIn("stopped", helper.call("list_dir", {"path": str(self.folder)})["error"])


class OneFileGrantedTests(unittest.TestCase):
    """A FILE handed over is the work's to change - that file alone (the
    user, 2026-10-26): granted, it is written in place; the file beside it
    is not - by the tools, the guard and the system's container alike."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        app = self.agent.app
        self.club = Path(self.agent.dir) / "club"
        self.club.mkdir()
        self.notes = self.club / "notes.md"
        self.notes.write_text("old", encoding="utf-8")
        (self.club / "minutes.md").write_text("theirs", encoding="utf-8")
        unit = {"id": "unit_one_file", "name": "The notes", "kind": "task"}
        self.assertNotIn("error", app.work.grant(unit["id"], str(self.notes)))
        self.folder = Path(app.work.folder_of(unit))
        self.runtime = app.work._runtime(unit, self.folder)
        self.runtime.mkdir(self.folder)
        self.tools = T.WorkTools(self.runtime)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_its_tools_write_it_and_not_the_file_beside_it(self) -> None:
        said = self.tools.call("write_file", {"path": str(self.notes), "content": "new"})
        self.assertNotIn("error", said, said)
        self.assertEqual(self.notes.read_text(encoding="utf-8"), "new")
        said = self.tools.call("write_file", {"path": str(self.club / "minutes.md"),
                                              "content": "x"})
        self.assertTrue(said.get("error") or said.get("violation"), said)
        self.assertEqual((self.club / "minutes.md").read_text(encoding="utf-8"), "theirs")

    @unittest.skipUnless(NATIVE and WINDOWS, f"an AppContainer: {STATUS['why']}")
    def test_in_the_container_that_file_alone(self) -> None:
        exe = sys.executable
        with mock.patch.object(T, "programs_to_read", return_value=[]):
            # Python started bare (-I): no guard - the container alone decides.
            said = self.tools.run_command({"command": (
                f'"{exe}" -I -c "open(r\'{self.notes}\', \'a\').write(\'+\'); print(\'wrote\')"'),
                "timeout": 120})
            self.assertEqual(said["returncode"], 0, said)
            self.assertIn("wrote", said["stdout"])
            said = self.tools.run_command({"command": (
                f'"{exe}" -I -c "open(r\'{self.club / "minutes.md"}\', \'a\').write(\'+\')"'),
                "timeout": 120})
            self.assertNotEqual(said["returncode"], 0, said)
            self.assertIn("PermissionError", said["stdout"])
        self.assertEqual((self.club / "minutes.md").read_text(encoding="utf-8"), "theirs")


if __name__ == "__main__":
    unittest.main()
