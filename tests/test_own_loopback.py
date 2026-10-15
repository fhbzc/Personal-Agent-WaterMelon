"""A run reaches the servers it starts itself, on this computer - and nothing
else there.

Agents test what they build the way everyone does: start the server, send it
requests. The user's decision: a run's code may connect to 127.0.0.1, but
only to its own run's servers - never another run's (several go at once:
the Experience debugger's three ways, real work beside them), nor the
application's, nor anything else of the user's. The operating system keeps
it: on Windows each run's code has an AppContainer of its own, and Windows
lets processes in one container reach each other on loopback and nothing in
another container nor on the computer itself (no administrator change); on
Linux bubblewrap gives each run a network namespace of its own. Seatbelt and
Landlock cannot keep runs apart there: no loopback at all. The guard opens
loopback only where the system does (runtime/guard._own_loopback_only).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "personalized_agent" / "runtime"))

import guard  # noqa: E402

from personalized_agent.runtime import isolation  # noqa: E402

STATUS = isolation.isolation_status()
OWN = isolation.own_loopback(STATUS)
WINDOWS = sys.platform.startswith("win")


def _last_json(text: str) -> dict:
    for line in reversed((text or "").strip().splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no result printed: {text!r}")


class TheGuardChoosesTests(unittest.TestCase):

    def kind(self, **env) -> str:
        names = (guard.NO_NETWORK_ENV, guard.OWN_LOOPBACK_ENV, guard.OS_ISOLATION_ENV,
                 guard.NETWORK_ALLOW_ENV)
        clean = {k: v for k, v in os.environ.items() if k not in names}
        with mock.patch.dict(os.environ, {**clean, **env}, clear=True):
            return guard._network_kind()

    def test_loopback_only_where_the_system_makes_it_the_runs(self) -> None:
        on = {guard.NO_NETWORK_ENV: "1", guard.OWN_LOOPBACK_ENV: "1"}
        for backend in ("appcontainer", "bubblewrap"):
            self.assertEqual(self.kind(**on, **{guard.OS_ISOLATION_ENV: backend}),
                             "own_loopback")
        for backend in ("seatbelt", "landlock", "none", ""):
            with self.subTest(backend=backend):
                self.assertEqual(self.kind(**on, **{guard.OS_ISOLATION_ENV: backend}), "none")
        self.assertEqual(self.kind(**{guard.NO_NETWORK_ENV: "1",
                                      guard.OS_ISOLATION_ENV: "appcontainer"}), "none")
        # A list of places is never loopback, and no network outranks it.
        self.assertEqual(self.kind(**{guard.NETWORK_ALLOW_ENV: "pypi.org:443",
                                      guard.OWN_LOOPBACK_ENV: "1"}), "places")

    def test_a_child_is_given_the_runs_value_not_its_own(self) -> None:
        self.assertIn(guard.OWN_LOOPBACK_ENV, guard._POLICY_NAMES)

    def test_what_is_loopback(self) -> None:
        for host in ("127.0.0.1", "127.8.9.10", "::1", "localhost", "LOCALHOST.",
                     "app.localhost", "::ffff:127.0.0.1", b"127.0.0.1"):
            with self.subTest(host=host):
                self.assertTrue(guard._is_loopback(host))
        for host in ("0.0.0.0", "10.0.0.1", "192.168.1.2", "example.com",
                     "localhost.example.com", "::", "", None, "169.254.1.1"):
            with self.subTest(host=host):
                self.assertFalse(guard._is_loopback(host))


class TheGuardsLoopbackTests(unittest.TestCase):
    """`_own_loopback_only` in a process of its own (it patches socket)."""

    SCRIPT = textwrap.dedent("""
        import json, socket, sys
        sys.path.insert(0, sys.argv[1])
        import guard
        guard._own_loopback_only()
        out = {}
        def attempt(name, action):
            try:
                action(); out[name] = 'DONE'
            except guard.SandboxViolation as exc:
                out[name] = 'refused: ' + str(exc)
            except Exception as exc:
                out[name] = type(exc).__name__
        server = socket.socket(); server.bind(('127.0.0.1', 0)); server.listen(2)
        port = server.getsockname()[1]
        attempt('own server', lambda: socket.create_connection(('127.0.0.1', port), 3).close())
        attempt('by name', lambda: socket.create_connection(('localhost', port), 3).close())
        attempt('bind any', lambda: socket.socket().bind(('0.0.0.0', 0)))
        attempt('bind lookup', lambda: socket.getaddrinfo(None, 0, flags=socket.AI_PASSIVE))
        attempt('a name', lambda: socket.getaddrinfo('example.com', 443))
        attempt('an address', lambda: socket.socket().connect(('10.255.255.1', 80)))
        attempt('udp out', lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                .sendto(b'x', ('8.8.8.8', 53)))
        if hasattr(socket, 'AF_UNIX'):
            attempt('unix', lambda: socket.socket(socket.AF_UNIX).connect('/tmp/x.sock'))
        print(json.dumps(out))
        """)

    def test_own_servers_yes_anything_else_no(self) -> None:
        done = subprocess.run([sys.executable, "-I", "-c", self.SCRIPT,
                               str(ROOT / "personalized_agent" / "runtime")],
                              capture_output=True, text=True, timeout=60)
        out = _last_json(done.stdout + "\n" + done.stderr)
        for name in ("own server", "by name", "bind any", "bind lookup"):
            self.assertEqual(out[name], "DONE", out)
        for name in ("a name", "an address", "udp out", "unix"):
            if name in out:
                with self.subTest(name=name):
                    self.assertTrue(out[name].startswith("refused: network:"), out)
                    self.assertIn("its own run starts", out[name])


class TheRunIsToldTests(unittest.TestCase):

    def test_isolation_sets_it_for_no_network_alone_and_drops_the_callers(self) -> None:
        seen = {}

        def run(argv, cwd, writable, readable, hidden, network, names, env, timeout):
            seen[network] = env.get(isolation.OWN_LOOPBACK_ENV)
            return subprocess.CompletedProcess(argv, 0, "", "")

        here = Path(tempfile.mkdtemp(prefix="pa-lo-")).resolve()
        self.addCleanup(shutil.rmtree, here, True)
        status = {"isolated": True, "backend": "appcontainer", "network": True, "why": ""}
        with mock.patch.object(isolation, "isolation_status", return_value=status), \
                mock.patch.object(isolation, "seal_from_containers", return_value=[]), \
                mock.patch.object(isolation._AppContainer, "run", side_effect=run):
            for network in isolation.NETWORK_MODES:
                isolation.run_isolated(["x"], cwd=here, writable=[here], network=network,
                                       env={isolation.OWN_LOOPBACK_ENV: "1"})
        self.assertEqual(seen, {"none": "1", "pypi": None, "full": None})

    def test_only_where_runs_are_kept_apart(self) -> None:
        for backend, own in (("appcontainer", True), ("bubblewrap", True),
                             ("seatbelt", False), ("landlock", False)):
            with self.subTest(backend=backend):
                self.assertEqual(isolation.own_loopback(
                    {"isolated": True, "backend": backend}), own)
        self.assertFalse(isolation.own_loopback({"isolated": False, "backend": "none"}))

    def test_the_prompt_says_what_holds_here(self) -> None:
        from personalized_agent.agent.work import runner
        with mock.patch.object(isolation, "own_loopback", return_value=True):
            self.assertEqual(runner.network_rules(), runner.NETWORK_OWN_LOOPBACK)
        with mock.patch.object(isolation, "own_loopback", return_value=False):
            self.assertEqual(runner.network_rules(), runner.NETWORK_NONE_AT_ALL)
        said = " ".join(runner.NETWORK_OWN_LOOPBACK.split())
        self.assertIn('("127.0.0.1", 0)', said)
        self.assertIn("never a fixed port", said)
        self.assertIn("subprocess.Popen([sys.executable", said)


class EachRunHasItsOwnContainerTests(unittest.TestCase):

    def test_named_for_the_runs_folder(self) -> None:
        a = isolation.run_container(Path(tempfile.gettempdir()) / "w" / "A")
        b = isolation.run_container(Path(tempfile.gettempdir()) / "w" / "B")
        self.assertNotEqual(a, b)
        self.assertEqual(a, isolation.run_container(Path(tempfile.gettempdir()) / "w" / "A"))
        self.assertTrue(a.startswith(isolation.APP_CONTAINER + "."))
        self.assertLessEqual(len(a), 64)


# ---------------------------------------------------------------------------
# Real runs, confined by the operating system
# ---------------------------------------------------------------------------
SERVE_AND_ASK = """
import json, socket, subprocess, sys, time, urllib.request
probe = socket.socket(); probe.bind(("127.0.0.1", 0))
port = probe.getsockname()[1]; probe.close()
server = subprocess.Popen([sys.executable, "-m", "http.server", str(port),
                           "--bind", "127.0.0.1"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
body = ""
try:
    for _ in range(100):
        try:
            body = urllib.request.urlopen(f"http://127.0.0.1:{port}/hello.txt",
                                          timeout=2).read().decode()
            break
        except OSError:
            time.sleep(0.2)
finally:
    server.terminate(); server.wait(10)
print(json.dumps({"body": body}))
"""

WAIT_FOR_ONE = """
import json, socket, sys
server = socket.socket(); server.bind(("127.0.0.1", 0)); server.listen(1)
open("port.txt", "w").write(str(server.getsockname()[1]))
server.settimeout(10)
try:
    server.accept(); print(json.dumps({"accepted": True}))
except OSError:
    print(json.dumps({"accepted": False}))
"""

KNOCK = """
import json, socket
try:
    socket.create_connection(("127.0.0.1", PORT), 4).close(); out = "DONE"
except Exception as exc:
    out = type(exc).__name__ + ": " + str(exc)
print(json.dumps({"connect": out}))
"""


@unittest.skipUnless(OWN, f"no loopback of a run's own here: {STATUS}")
class RealRunsTests(unittest.TestCase):

    def setUp(self) -> None:
        from personalized_agent.runtime.permissions import WorkspacePolicy
        from personalized_agent.runtime.sandbox import SandboxRuntime
        self.root = Path(tempfile.mkdtemp(prefix="pa-loop-")).resolve()
        workspace = self.root / "workspace"
        self.runs = {}
        for name in ("A", "B"):
            folder = workspace / "agent_work" / name
            folder.mkdir(parents=True)
            policy = WorkspacePolicy(workspace_root=workspace, writable_root=folder,
                                     readable_paths=[self.root], network=False)
            self.runs[name] = (folder, SandboxRuntime(policy, sandbox_enabled=True))

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def run_code(self, name: str, code: str):
        return self.runs[name][1].run_python_code(textwrap.dedent(code), timeout=120)

    def test_a_run_starts_its_server_and_talks_to_it(self) -> None:
        (self.runs["A"][0] / "hello.txt").write_text("hello from A")
        result = self.run_code("A", SERVE_AND_ASK)
        self.assertEqual(_last_json(result.stdout + "\n" + result.stderr)["body"],
                         "hello from A", result.stderr)

    def test_another_runs_server_is_out_of_reach(self) -> None:
        folder_a = self.runs["A"][0]
        waited = {}
        thread = threading.Thread(target=lambda: waited.update(
            result=self.run_code("A", WAIT_FOR_ONE)))
        thread.start()
        for _ in range(200):
            if (folder_a / "port.txt").exists() and (folder_a / "port.txt").read_text():
                break
            time.sleep(0.1)
        port = int((folder_a / "port.txt").read_text())
        knocked = self.run_code("B", KNOCK.replace("PORT", str(port)))
        thread.join(60)
        said = _last_json(knocked.stdout + "\n" + knocked.stderr)
        self.assertNotEqual(said["connect"], "DONE", said)
        # The system refused it: the guard let loopback through.
        self.assertNotIn("SandboxViolation", said["connect"])
        result = waited["result"]
        self.assertFalse(_last_json(result.stdout + "\n" + result.stderr)["accepted"])

    @unittest.skipUnless(WINDOWS, "containers are Windows'")
    def test_the_containers_profile_goes_when_its_run_ends(self) -> None:
        self.run_code("A", "print('x')")
        name = isolation.run_container(self.runs["A"][0])
        self.assertNotIn(name, isolation._AppContainer._live)
        self.assertNotIn(name, isolation._AppContainer.api()._containers)


if __name__ == "__main__":
    unittest.main()
