"""The operating system's half of the boundary (runtime/isolation.py).

The in-process guard can be stepped past from inside the child - `_io.FileIO`,
the real `open` taken out of the guarded function's closure, `_socket`, a
native library - so these tests run children that do exactly that, or that
have no guard at all, and check what the operating system let through. They
run for real on the platform they are on (Windows: an AppContainer; macOS:
Seatbelt; Linux: bubblewrap or Landlock) and are skipped where it has none;
the profile and command construction is checked everywhere.
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
from pathlib import Path, PurePosixPath
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from personalized_agent.runtime import isolation  # noqa: E402
from personalized_agent.runtime.isolation import (  # noqa: E402
    NETWORK_FULL, NETWORK_NONE, NETWORK_PYPI, run_isolated)

STATUS = isolation.isolation_status()
ISOLATED = bool(STATUS["isolated"])
WINDOWS = sys.platform.startswith("win")


def _env(home: Path) -> dict:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home),
           "USERPROFILE": str(home), "PYTHONIOENCODING": "utf-8"}
    for key in ("SYSTEMROOT", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def _python(code: str) -> list:
    """A child with NO guard at all: only the operating system stops it."""
    return [sys.executable, "-I", "-c", textwrap.dedent(code)]


def _last_json(text: str) -> dict:
    for line in reversed((text or "").strip().splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no result printed: {text!r}")


class _Folders(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="pa-iso-")).resolve()
        self.work = self.root / "work"
        self.work.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def run_child(self, code: str, network: str = NETWORK_NONE, timeout: float = 120,
                  **kwargs) -> subprocess.CompletedProcess:
        return run_isolated(_python(code), cwd=self.work, writable=[self.work],
                            network=network, env=_env(self.work), timeout=timeout,
                            **kwargs)


@unittest.skipUnless(ISOLATED, f"no OS isolation here: {STATUS['why']}")
class WritesAreConfinedByTheSystemTests(_Folders):

    def test_inside_is_written_and_outside_is_not_by_any_route(self) -> None:
        target = self.outside / "escaped.txt"
        done = self.run_child(f"""
            import _io, json, os
            out = {{}}
            open('inside.txt', 'w').write('ok')
            out['inside'] = open('inside.txt').read()
            target = {str(target)!r}
            for how in ('open', 'fileio', 'os.open'):
                try:
                    if how == 'open':
                        open(target, 'w').write('x')
                    elif how == 'fileio':
                        _io.FileIO(target, 'w').write(b'x')
                    else:
                        os.close(os.open(target, os.O_WRONLY | os.O_CREAT))
                    out[how] = 'WROTE'
                except OSError as exc:
                    out[how] = type(exc).__name__
            print(json.dumps(out))
            """)
        out = _last_json(done.stdout)
        self.assertEqual(out["inside"], "ok", done.stderr)
        self.assertFalse(target.exists(), out)
        if STATUS["backend"] != "bubblewrap":    # there /tmp is a private tmpfs
            for how in ("open", "fileio", "os.open"):
                self.assertNotEqual(out[how], "WROTE", how)

    @unittest.skipUnless(WINDOWS, "CreateFileW is Windows")
    def test_native_code_cannot_write_outside(self) -> None:
        target = self.outside / "native.txt"
        done = self.run_child(f"""
            import ctypes, json
            from ctypes import wintypes
            k32 = ctypes.WinDLL('kernel32', use_last_error=True)
            k32.CreateFileW.restype = wintypes.HANDLE
            k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                        wintypes.HANDLE]
            h = k32.CreateFileW({str(target)!r}, 0x40000000, 0, None, 2, 0x80, None)
            print(json.dumps({{'handle': h if h not in (None, -1) and h != 2**64 - 1 else None,
                              'error': ctypes.get_last_error()}}))
            """)
        out = _last_json(done.stdout)
        self.assertIsNone(out["handle"], done.stdout)
        self.assertEqual(out["error"], 5)        # ERROR_ACCESS_DENIED
        self.assertFalse(target.exists())

    @unittest.skipUnless(WINDOWS, "the AppContainer's own storage is Windows")
    def test_the_containers_own_storage_is_not_writable(self) -> None:
        api = isolation._AppContainer.api()
        sid = api.container_sid(isolation.APP_CONTAINER)
        self.assertTrue(sid)
        profile = Path(os.environ["LOCALAPPDATA"]) / "Packages" / \
            isolation.APP_CONTAINER.lower() / "AC"
        target = profile / "pa-iso-test.txt"
        done = self.run_child(f"""
            import json
            try:
                open({str(target)!r}, 'w').write('x'); r = 'WROTE'
            except OSError as exc:
                r = type(exc).__name__
            print(json.dumps({{'r': r}}))
            """)
        self.assertNotEqual(_last_json(done.stdout)["r"], "WROTE")
        self.assertFalse(target.exists())
        target.unlink(missing_ok=True)

    def test_hidden_state_cannot_be_read(self) -> None:
        secret_dir = self.root / "internal"
        secret_dir.mkdir()
        (secret_dir / "settings.json").write_text("secret")
        done = run_isolated(_python(f"""
            import json
            try:
                r = open({str(secret_dir / 'settings.json')!r}).read()
            except OSError as exc:
                r = type(exc).__name__
            print(json.dumps({{'r': r}}))
            """), cwd=self.work, writable=[self.work], readable=[self.root],
            hidden=[secret_dir], env=_env(self.work), timeout=60)
        if STATUS["backend"] == "landlock":
            self.skipTest("Landlock here confines writes only")
        self.assertNotEqual(_last_json(done.stdout)["r"], "secret", done.stderr)

    @unittest.skipUnless(WINDOWS, "ACL grants are Windows")
    def test_an_earlier_wider_grant_does_not_keep_hidden_state_open(self) -> None:
        secret_dir = self.root / "internal"
        secret_dir.mkdir()
        (secret_dir / "settings.json").write_text("secret")
        # Granted once with nothing excluded - as a test with another app's
        # boundary did - then asked again with the state hidden.
        isolation._AppContainer.grant("Read", self.root, [])
        isolation._AppContainer._granted.clear()
        api = isolation._AppContainer.api()
        sid = api.capability_sid(isolation.capability_name("Read", self.root))
        self.assertTrue(api.has_access(str(secret_dir), sid, isolation._READ_EXECUTE, True))
        isolation._AppContainer.grant("Read", self.root, [secret_dir])
        self.assertFalse(api.has_access(str(secret_dir / "settings.json"), sid,
                                        isolation._READ_EXECUTE, False))
        self.assertTrue(api.has_access(str(self.work), sid, isolation._READ_EXECUTE, True))
        isolation._AppContainer.withdraw("Read", self.root)
        self.assertFalse(api.has_access(str(self.work), sid, isolation._READ_EXECUTE, False))

    def test_a_timeout_ends_the_child(self) -> None:
        marker = self.work / "late.txt"
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_child(f"""
                import time
                time.sleep(30)
                open({str(marker)!r}, 'w').write('still running')
                """, timeout=3)
        self.assertLess(time.monotonic() - started, 25)
        time.sleep(1)
        self.assertFalse(marker.exists())


@unittest.skipUnless(ISOLATED and STATUS.get("network"),
                     f"the network is not confined here: {STATUS['why']}")
class TheNetworkIsClosedBySystemTests(_Folders):

    def setUp(self) -> None:
        super().setUp()
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(5)
        self.server.settimeout(0.5)
        self.port = self.server.getsockname()[1]
        self.accepted = []
        self.stop = False

        def accept() -> None:
            while not self.stop:
                try:
                    conn, _ = self.server.accept()
                    self.accepted.append(True)
                    conn.close()
                except OSError:
                    continue

        self.thread = threading.Thread(target=accept, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.stop = True
        self.thread.join(3)
        self.server.close()
        super().tearDown()

    def test_no_connection_even_to_a_listener_on_this_machine(self) -> None:
        done = self.run_child(f"""
            import _socket, json
            s = _socket.socket()
            s.settimeout(5)
            try:
                s.connect(('127.0.0.1', {self.port})); r = 'CONNECTED'
            except OSError as exc:
                r = type(exc).__name__
            print(json.dumps({{'r': r}}))
            """)
        self.assertNotEqual(_last_json(done.stdout)["r"], "CONNECTED", done.stderr)
        time.sleep(0.5)
        self.assertEqual(self.accepted, [])

    def test_the_package_installer_reaches_https_only_where_allowed(self) -> None:
        try:
            socket.create_connection(("pypi.org", 443), timeout=5).close()
        except OSError:
            self.skipTest("offline: pypi.org cannot be reached from here")
        code = """
            import json, socket
            out = {}
            for name, port in (('pypi.org', 443), ('pypi.org', 80)):
                try:
                    socket.create_connection((name, port), timeout=8).close()
                    out[f'{name}:{port}'] = 'CONNECTED'
                except OSError as exc:
                    out[f'{name}:{port}'] = type(exc).__name__
            print(json.dumps(out))
            """
        pip = _last_json(self.run_child(code, network=NETWORK_PYPI).stdout)
        self.assertEqual(pip["pypi.org:443"], "CONNECTED", pip)
        if STATUS["backend"] in ("seatbelt", "landlock"):    # filtered by port there
            self.assertNotEqual(pip["pypi.org:80"], "CONNECTED", pip)
        closed = _last_json(self.run_child(code, network=NETWORK_NONE).stdout)
        self.assertNotEqual(closed["pypi.org:443"], "CONNECTED", closed)


@unittest.skipUnless(ISOLATED, f"no OS isolation here: {STATUS['why']}")
class TheGuardBypassedStillCannotEscapeTests(unittest.TestCase):
    """run_python as a work run starts it - guard and all - with code that
    takes the guard apart first."""

    def setUp(self) -> None:
        from personalized_agent.runtime.permissions import WorkspacePolicy
        from personalized_agent.runtime.sandbox import SandboxRuntime
        self.root = Path(tempfile.mkdtemp(prefix="pa-iso-run-")).resolve()
        workspace = self.root / "workspace"
        self.folder = workspace / "agent_work" / "proj-abc123"
        self.folder.mkdir(parents=True)
        self.internal = self.root / "internal"
        self.internal.mkdir()
        (self.internal / "settings.json").write_text("secret")
        self.run_dir = self.folder / "runs" / "run_1"
        self.versions = self.run_dir / ".versions"
        (self.versions / "objects").mkdir(parents=True)
        self.saved = self.versions / "objects" / "abc"
        self.saved.write_text("saved")
        self.packages = self.run_dir / ".packages"
        self.packages.mkdir()
        self.policy = WorkspacePolicy(workspace_root=workspace, writable_root=self.folder,
                                      readable_paths=[self.root],
                                      protected_paths=[self.internal], network=False)
        self.runtime = SandboxRuntime(self.policy)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def run_code(self, code: str) -> dict:
        result = self.runtime.run_python_code(textwrap.dedent(code), timeout=120)
        self.assertNotIn("not OS-isolated", result.os_isolation)
        return _last_json(result.stdout + "\n" + result.stderr)

    BYPASS = """
        import builtins, json, os, _io
        # The real open, out of the guarded one's closure.
        real_open = next(c.cell_contents for c in builtins.open.__closure__
                         if callable(c.cell_contents)
                         and getattr(c.cell_contents, '__name__', '') == 'open')
        out = {}
        def attempt(name, action):
            try:
                action(); out[name] = 'DONE'
            except Exception as exc:
                out[name] = type(exc).__name__
        """

    def test_the_closure_trick_and_fileio_write_nothing_outside(self) -> None:
        target = self.root / "escaped.txt"
        out = self.run_code(self.BYPASS + f"""
        attempt('closure', lambda: real_open({str(target)!r}, 'w').write('x'))
        attempt('fileio', lambda: _io.FileIO({str(target)!r}, 'w').write(b'x'))
        attempt('inside', lambda: real_open('made.txt', 'w').write('ok'))
        attempt('read secret', lambda: real_open({str(self.internal / 'settings.json')!r}).read())
        print(json.dumps(out))
        """)
        self.assertEqual(out["inside"], "DONE", out)
        self.assertNotEqual(out["closure"], "DONE", out)
        self.assertNotEqual(out["fileio"], "DONE", out)
        if STATUS["backend"] != "landlock":
            self.assertNotEqual(out["read secret"], "DONE", out)
        self.assertFalse(target.exists())
        self.assertTrue((self.folder / "made.txt").exists())

    @unittest.skipUnless(WINDOWS, "the real CreateProcess is held on Windows")
    def test_a_child_started_past_the_guard_is_still_confined(self) -> None:
        """Python children may start where the system confines the run, the
        guard loaded into each (runtime/guard.child_command) - and the real
        CreateProcess is in the rewrite's closure, as the real `open` is in
        the guarded one's. Taken out, it starts a child with no guard at all:
        which is still in the container, so writes, reads and connects no
        more than its parent could."""
        target = self.root / "escaped-by-child.txt"
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        port = listener.getsockname()[1]
        unguarded = textwrap.dedent(f"""
            import json, socket
            out = {{}}
            def attempt(name, action):
                try:
                    action(); out[name] = 'DONE'
                except Exception as exc:
                    out[name] = type(exc).__name__
            attempt('write', lambda: open({str(target)!r}, 'w').write('x'))
            attempt('read secret', lambda: open({str(self.internal / 'settings.json')!r}).read())
            attempt('localhost', lambda: socket.create_connection(('127.0.0.1', {port}), 5))
            attempt('inside', lambda: open('child-made.txt', 'w').write('ok'))
            print(json.dumps(out))
            """)
        out = self.run_code(f"""
        import _winapi, json, subprocess, sys
        real = next(c.cell_contents for c in _winapi.CreateProcess.__closure__
                    if getattr(c.cell_contents, '__name__', '') == 'CreateProcess')
        _winapi.CreateProcess = real
        done = subprocess.run([sys.executable, '-I', '-c', {unguarded!r}],
                              capture_output=True, text=True, timeout=120)
        print(done.stdout.strip().splitlines()[-1] if done.stdout.strip() else
              json.dumps({{'no output': done.stderr[-300:]}}))
        """)
        self.assertNotIn("no output", out, out)
        self.assertEqual(out["inside"], "DONE", out)
        self.assertNotEqual(out["write"], "DONE", out)
        self.assertNotEqual(out["read secret"], "DONE", out)
        self.assertNotEqual(out["localhost"], "DONE", out)
        self.assertFalse(target.exists())

    @unittest.skipUnless(STATUS.get("network"), "the network is not confined here")
    def test_raw_sockets_connect_nowhere(self) -> None:
        out = self.run_code(self.BYPASS + """
        import _socket
        def connect():
            s = _socket.socket(); s.settimeout(5); s.connect(('1.1.1.1', 443))
        attempt('connect', connect)
        print(json.dumps(out))
        """)
        self.assertNotEqual(out["connect"], "DONE", out)

    def test_the_versions_and_the_packages_cannot_be_changed(self) -> None:
        versions, saved = str(self.versions), str(self.saved)
        out = self.run_code(self.BYPASS + f"""
        attempt('create', lambda: _io.FileIO(os.path.join({versions!r}, 'new'), 'w').write(b'x'))
        attempt('modify', lambda: _io.FileIO({saved!r}, 'w').write(b'x'))
        attempt('append', lambda: real_open({saved!r}, 'a').write('x'))
        attempt('delete', lambda: os.unlink.__wrapped__({saved!r})
                if hasattr(os.unlink, '__wrapped__') else __import__('nt' if os.name == 'nt'
                else 'posix').unlink({saved!r}))
        attempt('rename dir', lambda: __import__('nt' if os.name == 'nt' else 'posix').rename(
            {versions!r}, {versions!r} + '-moved'))
        attempt('remove dir', lambda: __import__('nt' if os.name == 'nt' else 'posix').rmdir(
            {versions!r}))
        attempt('package', lambda: _io.FileIO(os.path.join({str(self.packages)!r}, 'evil.py'),
                                              'w').write(b'x'))
        attempt('elsewhere', lambda: _io.FileIO(os.path.join({str(self.run_dir)!r}, 'ok.txt'),
                                                'w').write(b'x'))
        print(json.dumps(out))
        """)
        self.assertEqual(out["elsewhere"], "DONE", out)
        if STATUS["backend"] == "landlock":
            self.skipTest("Landlock cannot close a folder inside an open one: the guard does")
        for name in ("create", "modify", "append", "delete", "rename dir", "remove dir",
                     "package"):
            self.assertNotEqual(out[name], "DONE", (name, out))
        self.assertEqual(self.saved.read_text(), "saved")
        self.assertFalse((self.versions / "new").exists())
        self.assertFalse((self.packages / "evil.py").exists())
        # The application itself still writes its versions.
        from personalized_agent.agent.work.versions import VersionStore
        store = VersionStore(self.run_dir, self.policy)
        store._write(self.versions / "objects" / "def", b"by the host")
        self.assertEqual((self.versions / "objects" / "def").read_bytes(), b"by the host")

    def test_the_guard_refuses_the_versions_too(self) -> None:
        out = self.run_code(f"""
        import json
        try:
            open({str(self.versions / 'x')!r}, 'w'); r = 'DONE'
        except Exception as exc:
            r = type(exc).__name__
        print(json.dumps({{'r': r}}))
        """)
        self.assertEqual(out["r"], "SandboxViolation")

    def test_pip_may_write_the_packages(self) -> None:
        from personalized_agent.agent.work.packages import Installer
        installer = Installer(self.policy)
        result = installer.run_guarded(self.run_dir, textwrap.dedent(f"""
            import json
            out = {{}}
            for name, path in (('packages', {str(self.packages / 'six.py')!r}),
                               ('versions', {str(self.versions / 'x')!r})):
                try:
                    open(path, 'w').write('x'); out[name] = 'DONE'
                except Exception as exc:
                    out[name] = type(exc).__name__
            print(json.dumps(out))
            """), timeout=120)
        out = _last_json(result.stdout)
        self.assertEqual(out["packages"], "DONE", result.stderr)
        self.assertNotEqual(out["versions"], "DONE")


class ConstructionTests(unittest.TestCase):
    """What each backend would be told - checked on every platform."""

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="pa-iso-home-")).resolve()
        for name in (".ssh", ".aws", "Documents", "Library/Keychains", "AppData/Roaming"):
            (self.home / name).mkdir(parents=True)
        (self.home / ".netrc").write_text("x")
        (self.home / "Documents" / "notes.txt").write_text("x")

    def tearDown(self) -> None:
        shutil.rmtree(self.home, ignore_errors=True)

    def test_credential_stores_are_the_dot_entries_and_the_systems_own(self) -> None:
        found = {p.name for p in isolation.credential_stores(self.home)}
        self.assertTrue({".ssh", ".aws", ".netrc"} <= found, found)
        self.assertNotIn("Documents", found)
        if WINDOWS:
            self.assertIn("AppData", found)

    def test_a_grant_never_opens_what_is_excluded_beneath(self) -> None:
        excluded = [self.home / ".ssh", self.home / "AppData"]
        plan = dict((str(p), whole) for p, whole in isolation.grant_plan(self.home, excluded))
        self.assertIs(plan[str(self.home)], False)              # listed, not entered
        self.assertIs(plan[str(self.home / "Documents")], True)
        self.assertNotIn(str(self.home / ".ssh"), plan)
        self.assertNotIn(str(self.home / "AppData"), plan)
        # A root asked for by name is granted even inside an excluded one.
        inner = self.home / "AppData" / "Roaming"
        self.assertEqual(isolation.grant_plan(inner, excluded), [(inner, True)])

    def test_named_folders_are_found_where_runs_keep_them(self) -> None:
        run = self.home / "work" / "runs" / "run_1"
        (run / ".versions" / "objects").mkdir(parents=True)
        (run / ".packages").mkdir()
        found = isolation.find_named([self.home / "work"], [".versions", ".packages"])
        self.assertEqual(sorted(p.name for p in found), [".packages", ".versions"])

    def test_the_seatbelt_profile(self) -> None:
        # Posix paths whatever this runs on: the profile is macOS's.
        root = PurePosixPath("/Users/me/PersonalizedAgent/workspace/agent_work/p-1")
        profile = isolation.seatbelt_profile([root], [Path("/Users/me/PA/internal")],
                                             NETWORK_NONE, home=self.home,
                                             readonly_names=[".versions"])
        lines = profile.splitlines()
        self.assertEqual(lines[:2], ["(version 1)", "(allow default)"])
        self.assertIn("(deny file-write*)", lines)
        self.assertIn(f'(allow file-write* (subpath "{root}"))', lines)
        self.assertIn("(deny network*)", lines)
        self.assertTrue(any("internal" in line and "deny file-read*" in line
                            for line in lines))
        self.assertTrue(any(".ssh" in line and "deny file-read*" in line for line in lines))
        # The versions' denial comes after every allow - later rules win.
        last_allow = max(i for i, line in enumerate(lines) if line.startswith("(allow file-write"))
        versions = [i for i, line in enumerate(lines) if "versions" in line]
        self.assertTrue(versions and min(versions) > last_allow, profile)
        pip = isolation.seatbelt_profile([root], [], NETWORK_PYPI, home=self.home)
        self.assertIn('(allow network-outbound (remote tcp "*:443"))', pip)
        self.assertNotIn("network", isolation.seatbelt_profile([root], [], NETWORK_FULL,
                                                              home=self.home))

    def test_the_bubblewrap_command(self) -> None:
        work = PurePosixPath("/home/me/PA/workspace/agent_work/p-1")
        versions = work / "runs" / "r" / ".versions"
        args = isolation.bubblewrap_command(
            ["python3", "-c", "pass"], work, [work], [], [Path("/home/me/PA/internal")],
            NETWORK_NONE, home=self.home, readonly=[versions])
        joined = " ".join(args)
        self.assertEqual(args[:3], ["bwrap", "--ro-bind", "/"])
        self.assertIn(f"--bind {work} {work}", joined)
        self.assertIn("--unshare-net", args)
        self.assertIn("--die-with-parent", args)
        self.assertIn("--new-session", args)
        self.assertIn(f"--tmpfs {self.home / '.ssh'}", joined)
        self.assertIn(f"--ro-bind /dev/null {self.home / '.netrc'}", joined)
        self.assertGreater(joined.index(f"--ro-bind {versions}"),
                           joined.index(f"--bind {work}"))
        self.assertEqual(args[-4:], ["--", "python3", "-c", "pass"])
        shared = isolation.bubblewrap_command(["true"], work, [work], [], [], NETWORK_PYPI,
                                              home=self.home)
        self.assertNotIn("--unshare-net", shared)

    def test_the_landlock_launcher(self) -> None:
        args = isolation.landlock_command(["python3", "x.py"], [Path("/w")], [443])
        self.assertIn(isolation.LANDLOCK_FLAG, args)
        spec = json.loads(args[args.index(isolation.LANDLOCK_FLAG) + 1])
        self.assertEqual(spec, {"writable": [str(Path("/w"))], "ports": [443]})
        self.assertEqual(args[-3:], ["--", "python3", "x.py"])

    def test_the_interpreter_is_granted_on_its_own_never_under_the_home(self) -> None:
        # With the home readable, the home's grant leaves AppData out - and
        # Python usually lives in AppData\Local\Programs. Recorded, not run.
        calls = []
        fake = mock.Mock()
        fake.container_sid.return_value = b"c"
        fake.capability_sid.return_value = b"s"
        fake.launch.return_value = subprocess.CompletedProcess([], 0, "", "")
        python_dir = self.home / "AppData" / "Local" / "Programs" / "Python"
        python_dir.mkdir(parents=True)
        work = self.home / "Documents"
        with mock.patch.object(isolation._AppContainer, "api", return_value=fake), \
                mock.patch.object(isolation._AppContainer, "grant",
                                  side_effect=lambda kind, root, ex: calls.append(
                                      (kind, Path(root), list(ex))) or b"s"), \
                mock.patch.object(isolation, "code_paths", return_value=[python_dir]), \
                mock.patch.object(isolation, "credential_stores",
                                  return_value=[self.home / "AppData"]):
            isolation._AppContainer.run(["python"], work, [work], [self.home], [],
                                        NETWORK_NONE, (), {}, 10)
        python_grant = [c for c in calls if c[1] == python_dir]
        self.assertEqual(len(python_grant), 1, calls)
        self.assertNotIn(self.home / "AppData", python_grant[0][2])
        home_grant = [c for c in calls if c[1] == self.home]
        self.assertIn(self.home / "AppData", home_grant[0][2])

    def test_capabilities_are_one_per_folder(self) -> None:
        a = isolation.capability_name("Write", Path(self.home / "a"))
        b = isolation.capability_name("Write", Path(self.home / "b"))
        r = isolation.capability_name("Read", Path(self.home / "a"))
        self.assertEqual(len({a, b, r}), 3)
        self.assertTrue(a.startswith(isolation.APP_CONTAINER + ".Write."))


class TheExpectedBackendTests(unittest.TestCase):

    @unittest.skipUnless(os.environ.get("PA_EXPECT_OS_ISOLATION"),
                         "set PA_EXPECT_OS_ISOLATION=1 where a backend must be present (CI)")
    def test_this_platforms_backend_is_there(self) -> None:
        # Skipping every real test above would look like passing: where a
        # backend is expected, its absence is a failure, with the reason.
        expected = {"win32": ("appcontainer",), "darwin": ("seatbelt",)}.get(
            sys.platform, ("bubblewrap", "landlock"))
        self.assertIn(STATUS["backend"], expected, STATUS)


class WithoutIsolationTests(unittest.TestCase):

    def test_auto_installs_are_refused_and_the_report_says_why(self) -> None:
        from personalized_agent.agent.work.packages import ASK, mode_of
        none = {"isolated": False, "backend": "none", "why": "no backend here",
                "network": False}
        with mock.patch.object(isolation, "isolation_status", return_value=none):
            self.assertEqual(mode_of("auto"), ASK)
            self.assertEqual(isolation.describe(), "not OS-isolated: no backend here")
        yes = {"isolated": True, "backend": "x", "why": "", "network": True}
        with mock.patch.object(isolation, "isolation_status", return_value=yes):
            self.assertEqual(mode_of("auto"), "auto")

    def test_without_a_backend_the_child_still_runs(self) -> None:
        none = {"isolated": False, "backend": "none", "why": "test", "network": False}
        root = Path(tempfile.mkdtemp(prefix="pa-iso-none-")).resolve()
        try:
            with mock.patch.object(isolation, "isolation_status", return_value=none):
                done = run_isolated(_python("print('ran')"), cwd=root, writable=[root],
                                    env=_env(root), timeout=60)
            self.assertEqual(done.stdout.strip(), "ran")
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
