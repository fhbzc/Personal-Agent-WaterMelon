"""Python packages for a work run: plain PyPI requirements only, installed
by pip running inside the guard - writes only in the run's folder and the
shared download cache, network only to PyPI - and, unless the user chose
Auto, only after they approve each request."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from support import TemporaryAgent
from test_work_runs import _TwoAgents

GUARD_DIR = (Path(__file__).resolve().parents[1] / "personalized_agent" / "runtime")


class RequirementValidationTests(unittest.TestCase):

    def test_plain_requirements_are_accepted(self) -> None:
        from personalized_agent.agent.work.packages import validate
        for one in ("pandas", "numpy>=1.26", "scikit-learn==1.5.*",
                    "requests>=2,<3", "zope.interface~=6.0", "six"):
            allowed, refused = validate([one])
            self.assertEqual(refused, [], one)
            self.assertEqual(len(allowed), 1, one)

    def test_anything_else_is_refused(self) -> None:
        from personalized_agent.agent.work.packages import validate
        for one in ("https://example.org/evil-1.0-py3-none-any.whl",
                    "http://pypi.org/simple/six",
                    "git+https://github.com/someone/thing",
                    "hg+https://example.org/repo",
                    "./local/package", "../pkg", "C:\\pkgs\\x.whl", "/tmp/x.whl",
                    "-e .", "--index-url=https://evil.example/simple",
                    "--extra-index-url https://evil.example/simple",
                    "-r requirements.txt", "six --index-url x",
                    "thing @ https://example.org/thing.zip",
                    "thing[extra]", "thing[socks] @ https://example.org/x",
                    'six; python_version < "3.8"', "", "   ", "six==", "si x"):
            allowed, refused = validate([one])
            self.assertEqual(allowed, [], one)
            self.assertTrue(refused, one)

    def test_a_list_is_needed_and_one_bad_entry_refuses_the_request(self) -> None:
        from personalized_agent.agent.work.packages import Installer, validate
        self.assertTrue(validate({"six": 1})[1])
        allowed, refused = validate(["six", "https://example.org/x.whl"])
        self.assertEqual(allowed, ["six"])
        self.assertTrue(refused)
        # The installer runs nothing for a request with a refused entry.
        said = Installer.__new__(Installer).install(Path("."), ["six", "-e ."])
        self.assertTrue(said["error"].startswith("refused"))


class TheNetworkAllowListTests(unittest.TestCase):
    """The guard's list of places, in a child of its own: the lookup and the
    connect are faked first, so no real network is used."""

    SCRIPT = r'''
import json, socket, sys
sys.path.insert(0, GUARD_DIR)
calls = []
socket.getaddrinfo = lambda host, port, *a, **k: [
    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("151.101.0.223", int(port)))]
socket.socket.connect = lambda self, address: calls.append(list(address))
socket.socket.connect_ex = lambda self, address: calls.append(list(address)) or 0
import guard
guard._only_network(guard._allowed_places("pypi.org:443,files.pythonhosted.org:443"))
out = {}
def attempt(name, fn):
    try:
        fn()
        out[name] = "allowed"
    except guard.SandboxViolation as exc:
        out[name] = "refused: " + str(exc)
s = socket.socket()
attempt("connect before any lookup", lambda: s.connect(("151.101.0.223", 443)))
attempt("lookup pypi", lambda: socket.getaddrinfo("pypi.org", 443))
attempt("lookup files", lambda: socket.getaddrinfo("files.pythonhosted.org", "443"))
attempt("connect pypi", lambda: s.connect(("151.101.0.223", 443)))
attempt("create_connection pypi", lambda: socket.create_connection(("PyPI.org.", 443)))
attempt("lookup another host", lambda: socket.getaddrinfo("example.com", 443))
attempt("lookup an address", lambda: socket.getaddrinfo("151.101.0.223", 443))
attempt("lookup an ipv6 address", lambda: socket.getaddrinfo("::1", 443))
attempt("lookup another port", lambda: socket.getaddrinfo("pypi.org", 80))
attempt("connect another address", lambda: s.connect(("8.8.8.8", 443)))
attempt("connect another port", lambda: s.connect(("151.101.0.223", 80)))
attempt("create_connection another host",
        lambda: socket.create_connection(("example.com", 443)))
attempt("datagram", lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        .sendto(b"x", ("151.101.0.223", 443)))
print(json.dumps({"out": out, "calls": calls}))
'''

    def test_only_pypi_on_443_is_reached(self) -> None:
        code = self.SCRIPT.replace("GUARD_DIR", repr(str(GUARD_DIR)))
        done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                              text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        said = json.loads(done.stdout.strip().splitlines()[-1])
        out = said["out"]
        for allowed in ("lookup pypi", "lookup files", "connect pypi",
                        "create_connection pypi"):
            self.assertEqual(out[allowed], "allowed", (allowed, out[allowed]))
        for refused in ("connect before any lookup", "lookup another host",
                        "lookup an address", "lookup an ipv6 address",
                        "lookup another port", "connect another address",
                        "connect another port", "create_connection another host",
                        "datagram"):
            self.assertTrue(out[refused].startswith("refused"), (refused, out[refused]))
        self.assertIn("address given directly", out["lookup an address"])
        # Only the allowed connects reached the (fake) network.
        self.assertEqual(said["calls"], [["151.101.0.223", 443]] * 2)

    def test_no_network_outranks_a_list(self) -> None:
        code = ("import os, socket, sys\n"
                f"sys.path.insert(0, {str(GUARD_DIR)!r})\n"
                "import guard\n"
                "guard.install()\n"
                "try:\n"
                "    socket.getaddrinfo('pypi.org', 443)\n"
                "    print('LOOKED UP')\n"
                "except guard.SandboxViolation as exc:\n"
                "    print('refused', exc)\n")
        env = dict(os.environ, AGENT_NO_NETWORK="1",
                   AGENT_NETWORK_ALLOW="pypi.org:443")
        env.pop("AGENT_WORKSPACE", None)
        done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                              text=True, timeout=120, env=env)
        self.assertIn("refused", done.stdout, done.stderr)
        self.assertNotIn("LOOKED UP", done.stdout)


class PipRunsInsideTheGuardTests(unittest.TestCase):
    """The child pip runs in - its policy and environment, exactly - with a
    tiny script in place of pip."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.folder = self.app.work.folder_for("Some work", "project-pkg123")
        self.installer = self.app.work.installer_for(self.app.work.policy_for(self.folder))
        self.run_dir = self.folder / "runs" / "run_pkgtest"
        self.outside = Path(self.agent.dir) / "outside-the-run.txt"

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_it_writes_in_the_run_and_the_cache_and_nowhere_else(self) -> None:
        cache = self.installer.cache_dir()
        work_file = self.folder / "beside-the-run.txt"
        home_file = Path.home() / "pa-pip-escape-test.txt"
        home_file.unlink(missing_ok=True)
        targets = {"run": self.run_dir / "inside.txt", "cache": cache / "inside.txt",
                   "work": work_file, "home": home_file, "outside": self.outside}
        code = ("import json, os, socket, subprocess, sys\n"
                f"targets = {json.dumps({k: str(v) for k, v in targets.items()})}\n"
                "out = {}\n"
                "for name, path in targets.items():\n"
                "    try:\n"
                "        open(path, 'w').write('x')\n"
                "        out[name] = 'written'\n"
                "    except Exception as exc:\n"
                "        out[name] = type(exc).__name__\n"
                "try:\n"
                "    socket.getaddrinfo('example.com', 443)\n"
                "    out['other host'] = 'looked up'\n"
                "except Exception as exc:\n"
                "    out['other host'] = type(exc).__name__\n"
                "try:\n"
                "    subprocess.run([sys.executable, '-c', 'pass'])\n"
                "    out['process'] = 'started'\n"
                "except Exception as exc:\n"
                "    out['process'] = type(exc).__name__\n"
                "out['env'] = {k: os.environ.get(k) for k in ('PIP_CONFIG_FILE', "
                "'AGENT_NETWORK_ALLOW', 'PYTHONNOUSERSITE', 'APPDATA', 'HTTPS_PROXY', "
                "'HTTP_PROXY', 'PIP_INDEX_URL')}\n"
                "print(json.dumps(out))\n")
        result = self.installer.run_guarded(self.run_dir, code, timeout=120)
        out = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(out["run"], "written", result.stderr)
        self.assertEqual(out["cache"], "written")
        for refused in ("work", "home", "outside"):
            self.assertEqual(out[refused], "SandboxViolation", refused)
        self.assertFalse(work_file.exists())
        self.assertFalse(home_file.exists())
        self.assertFalse(self.outside.exists())
        self.assertEqual(out["other host"], "SandboxViolation")
        self.assertEqual(out["process"], "SandboxViolation")
        env = out["env"]
        self.assertEqual(env["PIP_CONFIG_FILE"], os.devnull)
        self.assertEqual(env["AGENT_NETWORK_ALLOW"],
                         "pypi.org:443,files.pythonhosted.org:443")
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")
        self.assertTrue(Path(env["APPDATA"]).resolve().is_relative_to(
            self.run_dir.resolve()))
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "PIP_INDEX_URL"):
            self.assertIsNone(env[name], name)

    def test_a_project_kept_outside_the_workspace_installs_too(self) -> None:
        """A project on the desktop: its runs' folders are outside the
        workspace, and pip - given nothing else to read - was refused the
        script it was about to run in its own run's folder."""
        outside = Path(self.agent.dir) / "desktop" / "my-project"
        outside.mkdir(parents=True)
        installer = self.app.work.installer_for(self.app.work.policy_for(outside))
        result = installer.run_guarded(outside / "runs" / "run_out", "print('ran')",
                                       timeout=120)
        self.assertIsNone(result.violation, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "ran", result.stderr)

    def test_the_cache_is_beside_the_works_and_not_writable_by_their_code(self) -> None:
        from personalized_agent.agent.work.tools import WorkTools
        from personalized_agent.runtime.sandbox import SandboxRuntime
        cache = self.installer.cache_dir()
        self.assertEqual(cache.parent, self.folder.parent)
        tools = WorkTools(SandboxRuntime(self.app.work.policy_for(self.folder),
                                         platform=self.app.platform))
        said = tools.call("run_python", {"code": (
            f"open(r'{cache / 'poisoned.txt'}', 'w').write('x')\n")})
        self.assertFalse((cache / "poisoned.txt").exists(), said)


class _WantsPackages(_TwoAgents):
    """The work agent asks for packages at the start of its first round,
    then carries on as the two agents do."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.install_results = []
        self.asked_for = False
        self.systems = []

    def invoke(self, request, role=""):
        from personalized_agent.models.base import ToolCall
        first = request.messages[0]["content"]
        first = first if isinstance(first, str) else json.dumps(first)
        last = request.messages[-1]["content"]
        if "You review a work agent" not in request.system and "WORK ONE ROUND" in first:
            self.systems.append(request.system)
            if not self.asked_for:
                self.asked_for = True
                return self._reply("", [ToolCall(
                    id="p1", name="install_packages",
                    arguments={"packages": ["six", "numpy>=1.26"],
                               "why": "to read the data files"})])
            answered = [one for one in last if isinstance(one, dict)
                        and one.get("tool_use_id") == "p1"] if isinstance(last, list) else []
            if answered:
                self.install_results.append(json.loads(answered[0]["content"]))
                return self._reply("", [ToolCall(id="c1", name="write_file",
                                                 arguments={"path": "notes.md",
                                                            "content": "hello"})])
        return super().invoke(request, role)


class _FakeInstaller:
    def __init__(self):
        self.calls = []

    def install(self, run_dir, requirements):
        self.calls.append((Path(run_dir), list(requirements)))
        return {"requested": list(requirements), "folder": str(Path(run_dir) / ".packages"),
                "installed": [{"name": "six", "version": "1.17.0"},
                              {"name": "numpy", "version": "2.1.3"}]}


class AskMeOrAutoTests(unittest.TestCase):

    def setUp(self) -> None:
        from personalized_agent.api.routes import build_routes
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.unit = {"id": "task-pkg789", "name": "Read data", "kind": "task", "why": ""}
        self.installer = _FakeInstaller()
        self.app.work.installer_for = lambda policy: self.installer
        self.routes = build_routes(self.app)

    def tearDown(self) -> None:
        for run in list(self.app.work._running.values()):
            run.stop_now = True
        self._until(lambda: not any(r.alive() for r in self.app.work._running.values()))
        self.agent.cleanup()

    @staticmethod
    def _until(condition, seconds=60):
        for _ in range(int(seconds * 10)):
            if condition():
                return True
            time.sleep(0.1)
        return False

    def _post(self, path, body):
        class Request:
            def __init__(self, body):
                self.body = body

            def get(self, key, default=None):
                return self.body.get(key, default)

            def require(self, key):
                from personalized_agent.api.errors import ApiError
                if self.body.get(key) in (None, ""):
                    raise ApiError(f"missing parameter: {key}")
                return self.body[key]

        return self.routes[("POST", path)](Request(body))

    def _start(self, packages):
        self.app.work.models = self.models = _WantsPackages()
        said = self.app.work.start(self.unit, "read the data", packages=packages)
        self.assertNotIn("error", said, said)
        return said

    def _row(self):
        return self.app.work.runs(self.unit["id"])[0]

    def _pending(self):
        self.assertTrue(self._until(lambda: self._row()["status"] == "waiting for you"),
                        self._row()["status"])
        requests = self._row()["plan"]["packages"]["requests"]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["status"], "pending")
        return requests[0]

    def _finished(self):
        self.assertTrue(self._until(lambda: not self._row()["alive"]
                                    and self._row()["status"] != "running"))
        return self._row()

    def test_ask_me_waits_and_approve_installs(self) -> None:
        started = self._start("ask")
        self.assertEqual(started["packages"], "ask")
        request = self._pending()
        self.assertEqual(request["packages"], ["six", "numpy>=1.26"])
        self.assertEqual(request["why"], "to read the data files")
        self.assertEqual(self.installer.calls, [])
        self.assertIn("approves each request", self.models.systems[0])
        said = self._post("/api/work/approve", {"run": started["run"],
                                                "request": request["id"], "allow": True})
        self.assertEqual(said["request"]["status"], "approved")
        row = self._finished()
        self.assertEqual(row["status"], "done")
        self.assertEqual(len(self.installer.calls), 1)
        run_dir, asked = self.installer.calls[0]
        self.assertEqual(asked, ["six", "numpy>=1.26"])
        self.assertEqual(run_dir, Path(started["folder"]) / "runs" / started["run"])
        self.assertEqual(self.models.install_results[0]["installed"][0]["name"], "six")
        book = row["plan"]["packages"]
        self.assertEqual(book["requests"][0]["status"], "installed")
        self.assertEqual({one["name"] for one in book["installed"]}, {"six", "numpy"})
        self.assertTrue(any(e.get("what") == "installed packages" for e in row["log"]))
        report = (run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("## Installed packages (you approved each request)", report)
        self.assertIn("six 1.17.0", report)
        # Answered once: a second answer changes nothing, and is not found
        # once the run is over.
        from personalized_agent.api.errors import ApiError
        with self.assertRaises(ApiError) as caught:
            self._post("/api/work/approve", {"run": started["run"],
                                             "request": request["id"], "allow": False})
        self.assertEqual(caught.exception.status, 404)

    def test_deny_tells_the_agent(self) -> None:
        started = self._start("ask")
        request = self._pending()
        self._post("/api/work/approve", {"run": started["run"],
                                         "request": request["id"], "allow": False})
        row = self._finished()
        self.assertEqual(self.installer.calls, [])
        told = self.models.install_results[0]
        self.assertFalse(told["approved"])
        self.assertIn("did not approve", told["message"])
        self.assertEqual(row["plan"]["packages"]["requests"][0]["status"], "denied")

    def test_the_wait_is_not_counted_against_the_minutes(self) -> None:
        started = self._start("ask")
        self._pending()
        run = self.app.work._running[started["run"]]
        run.began -= 3600                     # an hour has passed...
        run.waited = 3600 - 1                 # ...all of it waiting
        run.limits = dict(run.limits, minutes=30)
        self.assertTrue(run._going(work=True))
        run.waited = 0
        self.assertFalse(run._going(work=True))
        run.waited = 3600 - 1

    def test_stop_ends_the_wait(self) -> None:
        started = self._start("ask")
        request = self._pending()
        self.assertTrue(self.app.work.stop(started["run"]))
        row = self._finished()
        self.assertEqual(self.installer.calls, [])
        settled = row["plan"]["packages"]["requests"][0]
        self.assertEqual(settled["status"], "not approved")
        self.assertIn("stopped", settled["note"])
        self.assertFalse(self.models.install_results[0]["approved"])
        # Unknown runs and requests are not found.
        from personalized_agent.api.errors import ApiError
        for body in ({"run": "run_nope", "request": request["id"], "allow": True},
                     {"run": started["run"], "request": "req_nope", "allow": True}):
            with self.assertRaises(ApiError) as caught:
                self._post("/api/work/approve", body)
            self.assertEqual(caught.exception.status, 404)

    def test_stop_now_ends_the_wait_too(self) -> None:
        started = self._start("ask")
        self._pending()
        self.app.work.stop(started["run"], now=True)
        row = self._finished()
        self.assertEqual(row["plan"]["packages"]["requests"][0]["status"], "not approved")
        self.assertEqual(self.installer.calls, [])

    def test_auto_installs_without_asking(self) -> None:
        started = self._start("auto")
        row = self._finished()
        self.assertEqual(started["packages"], "auto")
        self.assertEqual(len(self.installer.calls), 1)
        self.assertNotIn("approves each request", self.models.systems[0])
        book = row["plan"]["packages"]
        self.assertEqual(book["mode"], "auto")
        self.assertEqual(book["requests"][0]["status"], "installed")
        self.assertFalse(any(e.get("what") == "asked to install packages"
                             for e in row["log"]))
        report = (Path(started["folder"]) / "runs" / started["run"]
                  / "report.md").read_text(encoding="utf-8")
        self.assertIn("installed without asking you - Auto", report)

    def test_continue_keeps_the_choice_unless_changed(self) -> None:
        started = self._start("auto")
        self._finished()
        self.app.db.execute("UPDATE work_runs SET status = 'not finished' WHERE id = ?",
                            (started["run"],))
        self.app.work.models = _TwoAgents()
        again = self.app.work.continue_run(started["run"], self.unit)
        self.assertEqual(again["packages"], "auto")
        self._finished()
        self.app.db.execute("UPDATE work_runs SET status = 'not finished' WHERE id = ?",
                            (started["run"],))
        again = self.app.work.continue_run(started["run"], self.unit, packages="ask")
        self.assertEqual(again["packages"], "ask")
        self.assertEqual(self._finished()["plan"]["packages"]["mode"], "ask")

    def test_a_restart_settles_a_waiting_request_as_not_approved(self) -> None:
        from personalized_agent.agent.work.runner import INTERRUPTED, WorkRuns
        plan = {"packages": {"mode": "ask", "installed": [], "requests": [
            {"id": "req_1", "packages": ["six"], "why": "x", "status": "pending"}]}}
        self.app.db.execute(
            "INSERT INTO work_runs (id, unit_id, unit_name, folder, status, plan, "
            "started_at, updated_at) VALUES ('run_old', 'task-1', 'x', '', "
            "'waiting for you', ?, '2026-10-01', '2026-10-01')", (json.dumps(plan),))
        again = WorkRuns(self.app.db, self.app.policy, self.app.models, None)
        run = again.runs("task-1")[0]
        self.assertEqual(run["status"], INTERRUPTED)
        self.assertTrue(run["continuable"])
        request = run["plan"]["packages"]["requests"][0]
        self.assertEqual(request["status"], "not approved")
        self.assertIn("application closed", request["note"])
        # And it is not answerable: the run is not going.
        self.assertIsNone(again.approve("run_old", "req_1", True))


class RunPythonSeesThePackagesTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_runs_packages_come_first_and_the_guard_stays(self) -> None:
        from personalized_agent.agent.work.tools import WorkTools
        from personalized_agent.runtime.sandbox import SandboxRuntime
        folder = self.app.work.folder_for("Some work", "project-sys456")
        tools = WorkTools(SandboxRuntime(self.app.work.policy_for(folder),
                                         platform=self.app.platform))
        tools.packages_dir = folder / "runs" / "run_1" / ".packages"
        tools.packages_dir.mkdir(parents=True)
        (tools.packages_dir / "only_here_pkg.py").write_text("WHERE = 'the run'\n")
        said = tools.call("run_python", {"code": (
            "import sys, socket, only_here_pkg\n"
            "print(sys.path[0])\n"
            "print(only_here_pkg.WHERE)\n"
            "try:\n"
            "    socket.getaddrinfo('pypi.org', 443)\n"
            "    print('NETWORK')\n"
            "except Exception as exc:\n"
            "    print('no network:', type(exc).__name__)\n")})
        lines = str(said.get("stdout")).splitlines()
        self.assertEqual(Path(lines[0]).resolve(), tools.packages_dir.resolve(), said)
        self.assertEqual(lines[1], "the run")
        self.assertEqual(lines[2], "no network: SandboxViolation")

    def test_the_tool_is_offered_only_where_it_can_install(self) -> None:
        from personalized_agent.agent.work.tools import WorkTools
        from personalized_agent.runtime.sandbox import SandboxRuntime
        folder = self.app.work.folder_for("Some work", "project-sys456")
        runtime = SandboxRuntime(self.app.work.policy_for(folder), platform=self.app.platform)
        names = lambda tools, writing: [s.name for s in tools.specs(writing=writing)]
        self.assertNotIn("install_packages", names(WorkTools(runtime), True))
        able = WorkTools(runtime, install=lambda packages, why: {})
        self.assertIn("install_packages", names(able, True))
        self.assertNotIn("install_packages", names(able, False))


class ThePanelAsksTests(unittest.TestCase):
    """The work panel: a request waiting for an answer, with Approve and
    Deny, each posted as it should be; the choice of asking in the start
    form; and beside Continue, the run's own choice, changeable."""

    def setUp(self) -> None:
        from test_panels import _javascript_engine
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def test_a_waiting_request_and_the_choice_of_asking(self) -> None:
        import html
        import re
        web = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        base = {"folder": "C:/w/agent_work/x", "started_at": "2026-10-08T12:00:00",
                "instruction": "read the data", "final": {}, "log": []}
        going = dict(base, id="run_1", alive=True, status="waiting for you",
                     continuable=False, plan={"packages": {"mode": "ask", "requests": [
                         {"id": "req_0", "packages": ["pandas"], "why": "tables",
                          "status": "installed",
                          "installed": [{"name": "pandas", "version": "2.2.3"}]},
                         {"id": "req_1", "packages": ["six", "numpy>=1.26"],
                          "why": "to read the data files", "status": "pending"}]}})
        idle = dict(base, id="run_2", alive=False, status="not finished",
                    continuable=True, plan={"packages": {"mode": "auto", "requests": [
                        {"id": "req_9", "packages": ["six"], "why": "x",
                         "status": "installed", "installed": []}]}})
        stub = {"/api/work/runs": {"runs": [going]},
                "/api/work/folders": {"folders": [], "warning": ""},
                "/api/work/limits": {"defaults": {"minutes": 45, "tokens": 1000000,
                                                  "calls": 200}},
                "/api/work/tree": {"cards": [], "stages": [], "edges": []}}
        steps = (
            "experienceState.unit='task-pkg';await xpLoadRuns();"
            "const box=document.querySelector('.xp-package-ask');"
            "out.ask=box.textContent.replace(/\\s+/g,' ');"
            "out.buttons=[...box.querySelectorAll('[data-xp-approve]')].map(b=>b.textContent.trim());"
            "out.summary=document.querySelector('.xp-run summary').textContent.replace(/\\s+/g,' ');"
            "out.history=document.querySelector('.xp-packages').textContent.replace(/\\s+/g,' ');"
            "out.radios=[...document.querySelectorAll('input[name=\"xp-packages\"]')]"
            "  .map(r=>r.value+':'+r.checked+':'+r.parentElement.textContent.trim());"
            "box.querySelector('[data-xp-allow=\"1\"]').dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "await wait();"
            "document.querySelector('.xp-package-ask [data-xp-allow=\"0\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait();"
            "STUB['/api/work/runs']={runs:[IDLE]};await xpLoadRuns();"
            "out.boxes=document.querySelectorAll('.xp-package-ask').length;"
            "const pick=document.querySelector('[data-xp-continue-packages=\"run_2\"]');"
            "out.kept=pick.value;"
            "document.querySelector('[data-xp-continue=\"run_2\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait();"
            "document.querySelector('[data-xp-continue-packages=\"run_2\"]').value='ask';"
            "document.querySelector('[data-xp-continue=\"run_2\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait();"
            "document.querySelector('input[name=\"xp-packages\"][value=\"auto\"]').checked=true;"
            "await xpLoadRuns();"
            "out.keptAuto=document.querySelector('input[name=\"xp-packages\"][value=\"auto\"]').checked;"
            "document.getElementById('xp-start').dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "await wait();"
            "out.posts=window.__posts;")
        harness = (
            "<script>window.__posts=[];const STUB=" + json.dumps(stub) + ";"
            "const IDLE=" + json.dumps(idle) + ";"
            "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
            "if(o&&o.method==='POST')window.__posts.push({p,body:JSON.parse(o.body||'{}')});"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "headers:{get:()=>null},json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';const out={};"
            "const wait=()=>new Promise(r=>setTimeout(r,60));"
            "try{" + steps + "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err,out});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
        match = re.search(r"<title>(.*?)</title>", completed.stdout or "", re.S)
        self.assertIsNotNone(match, "the page never finished rendering")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        self.assertIn("The agent asks to install six, numpy>=1.26", out["ask"])
        self.assertIn("Why: to read the data files", out["ask"])
        self.assertEqual(out["buttons"], ["Approve", "Deny"])
        self.assertIn("waiting for you", out["summary"])
        self.assertIn("pandas: installed - pandas 2.2.3", out["history"])
        self.assertIn("each request approved by you", out["history"])
        self.assertEqual(out["radios"], ["ask:true:Ask me before installing packages",
                                         "auto:false:Auto"])
        posts = out["posts"]
        self.assertEqual(posts[0], {"p": "/api/work/approve", "body": {
            "run": "run_1", "request": "req_1", "allow": True}})
        self.assertEqual(posts[1], {"p": "/api/work/approve", "body": {
            "run": "run_1", "request": "req_1", "allow": False}})
        # A run not going asks nothing; Continue keeps its choice, or the
        # one made beside it.
        self.assertEqual(out["boxes"], 0)
        self.assertEqual(out["kept"], "auto")
        self.assertEqual([(one["p"], one["body"].get("packages")) for one in posts[2:4]],
                         [("/api/work/continue", "auto"), ("/api/work/continue", "ask")])
        # The start form's choice survives a redraw and is sent - with the
        # rest, to the Delegate work box, which drafts the plan to check first.
        self.assertTrue(out["keptAuto"])
        self.assertEqual(posts[4]["p"], "/api/work/drafts")
        self.assertEqual(posts[4]["body"]["packages"], "auto")
        self.assertTrue(posts[4]["body"]["write_first"])


def _pypi_reachable() -> bool:
    import socket
    try:
        socket.create_connection(("pypi.org", 443), timeout=5).close()
        return True
    except OSError:
        return False


@unittest.skipUnless(os.environ.get("PA_TEST_REAL_PIP", "1") != "0" and _pypi_reachable(),
                     "PyPI is not reachable from here")
class ARealInstallTests(unittest.TestCase):
    """The real thing: a tiny pure wheel installed through the guarded pip,
    and nothing written anywhere but the run's folder and the cache."""

    def test_six_is_installed_into_the_run_alone(self) -> None:
        from personalized_agent.agent.work.packages import Installer
        from personalized_agent.runtime.permissions import WorkspacePolicy
        root = Path(tempfile.mkdtemp(prefix="pa-real-pip-")).resolve()
        try:
            folder = root / "workspace" / "agent_work" / "proj-abc123"
            folder.mkdir(parents=True)
            (root / "workspace" / "documents").mkdir()
            policy = WorkspacePolicy(workspace_root=root / "workspace",
                                     writable_root=folder, network=False)
            installer = Installer(policy)
            run_dir = folder / "runs" / "run_real"
            said = installer.install(run_dir, ["six"], timeout=600)
            self.assertNotIn("error", said, said)
            self.assertEqual([one["name"] for one in said["installed"]], ["six"])
            allowed = [run_dir.resolve(), installer.cache_dir().resolve()]
            for path in root.rglob("*"):
                if path.is_file():
                    self.assertTrue(any(path.resolve().is_relative_to(one)
                                        for one in allowed), path)
            # And the run's code can use it.
            from personalized_agent.runtime.sandbox import SandboxRuntime
            ran = SandboxRuntime(policy).run_python_code(
                "import six; print(six.__file__)", timeout=120,
                sys_path=[run_dir / ".packages"])
            self.assertIn(".packages", ran.stdout, ran.stderr)
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
