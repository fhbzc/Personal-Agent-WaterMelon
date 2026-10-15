"""The browser work runs test with (runtime/browser.py, runtime/agent_browser.py).

The user's decision: a clean headless Chrome per run - Google's "Chrome for
Testing" headless shell, installed once from Settings - which opens only the
servers its own run starts; none of the user's accounts, nothing of another
run's. It runs inside the run's own container with its own sandbox off (it
cannot start there otherwise; the user agreed), so the container is what
confines it: it writes only in the run's folders and reaches only the run's
own loopback.

The install is checked everywhere with a made-up archive. The real browser
is driven only where one can be had: set PA_TEST_BROWSER_ZIP to a downloaded
chrome-headless-shell zip (and the computer must give runs a loopback of
their own).
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import zipfile
from email.message import Message
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "personalized_agent" / "runtime"))

import guard  # noqa: E402

from personalized_agent.runtime import browser, isolation  # noqa: E402

STATUS = isolation.isolation_status()
REAL_ZIP = os.environ.get("PA_TEST_BROWSER_ZIP", "")
URL = browser.DOWNLOAD_PREFIX + "155.0.1.2/" + (browser.platform_key() or "x") + "/shell.zip"


def _archive(files: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, data in files.items():
            bundle.writestr(name, data)
    return buffer.getvalue()


class _Answer(io.BytesIO):
    def __init__(self, data: bytes, md5: bytes = None) -> None:
        super().__init__(data)
        self.headers = Message()
        self.headers["Content-Length"] = str(len(data))
        if md5 is not False:
            digest = md5 if md5 is not None else hashlib.md5(data).digest()
            self.headers["x-goog-hash"] = "crc32c=AAAAAA=="
            self.headers["x-goog-hash"] = "md5=" + base64.b64encode(digest).decode()


def _opener(data: bytes, md5: bytes = None):
    return lambda url, timeout=None: _Answer(data, md5)


SHELL = {f"chrome-headless-shell-x/{browser.executable_name()}": b"MZ program",
         "chrome-headless-shell-x/resources.pak": b"pak"}


class InstallTests(unittest.TestCase):

    def setUp(self) -> None:
        self.data = Path(tempfile.mkdtemp(prefix="pa-br-")).resolve()
        self.addCleanup(shutil.rmtree, self.data, True)

    def test_installed_checked_and_in_use(self) -> None:
        program = browser.install(self.data, "155.0.1.2", URL, opener=_opener(_archive(SHELL)))
        self.assertEqual(program, browser.installed(self.data))
        self.assertTrue(isolation._within(program, self.data / "browser" / "155.0.1.2"))
        current = json.loads((self.data / "browser" / "current.json").read_text())
        self.assertEqual(current["version"], "155.0.1.2")
        # The helper beside it, as in the code, and both readable to runs.
        helper = self.data / "browser" / "lib" / "agent_browser.py"
        self.assertEqual(helper.read_bytes(),
                         (ROOT / "personalized_agent/runtime/agent_browser.py").read_bytes())
        homes = [isolation._key(one) for one in isolation.reading_homes()]
        self.assertIn(isolation._key(program.parent.parent), homes)
        self.assertIn(isolation._key(helper.parent), homes)
        self.assertIn(isolation._key(helper.parent),
                      [isolation._key(one) for one in isolation.code_paths()])
        self.assertFalse((self.data / "browser" / ".download").exists())

    def test_a_download_that_does_not_match_installs_nothing(self) -> None:
        with self.assertRaises(RuntimeError) as said:
            browser.install(self.data, "155.0.1.2", URL,
                            opener=_opener(_archive(SHELL), md5=b"0" * 16))
        self.assertIn("checksum", str(said.exception))
        self.assertIsNone(browser.installed(self.data))
        with self.assertRaises(RuntimeError):
            browser.install(self.data, "155.0.1.2", URL,
                            opener=_opener(_archive(SHELL), md5=False))
        self.assertIsNone(browser.installed(self.data))

    def test_only_googles_storage_and_no_path_out_of_the_archive(self) -> None:
        with self.assertRaises(ValueError):
            browser.install(self.data, "1.0", "https://example.com/shell.zip",
                            opener=_opener(_archive(SHELL)))
        with self.assertRaises(ValueError):
            browser.install(self.data, "../x", URL, opener=_opener(_archive(SHELL)))
        bad = _archive({**SHELL, "../../escaped.txt": b"x"})
        with self.assertRaises(RuntimeError):
            browser.install(self.data, "155.0.1.2", URL, opener=_opener(bad))
        self.assertFalse((self.data.parent / "escaped.txt").exists())

    def test_a_newer_one_replaces_the_older_and_remove_takes_all(self) -> None:
        browser.install(self.data, "155.0.1.2", URL, opener=_opener(_archive(SHELL)))
        browser.install(self.data, "156.0.0.1", URL.replace("155.0.1.2", "156.0.0.1"),
                        opener=_opener(_archive(SHELL)))
        self.assertFalse((self.data / "browser" / "155.0.1.2").exists())
        self.assertIn("156.0.0.1", str(browser.installed(self.data)))
        browser.remove(self.data)
        self.assertIsNone(browser.installed(self.data))

    def test_a_run_is_given_it_only_with_a_loopback_of_its_own(self) -> None:
        browser.install(self.data, "155.0.1.2", URL, opener=_opener(_archive(SHELL)))
        with mock.patch.object(isolation, "own_loopback", return_value=False):
            self.assertIsNone(browser.for_run(self.data))
        with mock.patch.object(isolation, "own_loopback", return_value=True):
            self.assertIsNotNone(browser.for_run(self.data))

    def test_the_installer_reports_progress_and_failure(self) -> None:
        installer = browser.Installer(self.data)
        with mock.patch.object(browser, "latest", side_effect=RuntimeError("offline")):
            installer.start()
            installer.wait(10)
        self.assertEqual(installer.state["state"], "failed")
        self.assertIn("offline", installer.state["error"])
        self.assertFalse(installer.status()["installed"])


class TheGuardStartsOnlyThatBrowserTests(unittest.TestCase):

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="pa-brg-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.program = self.dir / browser.executable_name()
        self.program.write_bytes(b"MZ")
        self.other = self.dir / ("other.exe" if os.name == "nt" else "other")
        self.other.write_bytes(b"MZ")
        here = {os.path.normcase(os.path.realpath(sys.executable))}
        patcher = mock.patch.multiple(guard, _os_backend="appcontainer", _offline=True,
                                      _interpreter=sys.executable, _interpreters=here,
                                      _guard_home="/g", _child_policy={},
                                      _browser_path=str(self.program))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_browser_starts_as_asked(self) -> None:
        command = guard.child_command(str(self.program), ["--no-sandbox", "about:blank"])
        self.assertEqual(command, [str(self.program), "--no-sandbox", "about:blank"])

    def test_another_program_still_refused_with_the_hint(self) -> None:
        with self.assertRaises(guard.SandboxViolation) as said:
            guard.child_command(str(self.other), [])
        self.assertIn("agent_browser.Browser()", str(said.exception))

    def test_not_where_the_run_has_no_browser(self) -> None:
        with mock.patch.object(guard, "_browser_path", ""):
            with self.assertRaises(guard.SandboxViolation):
                guard.child_command(str(self.program), [])

    def test_it_is_taken_as_the_process_started_never_later(self) -> None:
        env = {guard.NO_NETWORK_ENV: "1", guard.OWN_LOOPBACK_ENV: "1",
               guard.OS_ISOLATION_ENV: "appcontainer", guard.BROWSER_ENV: str(self.program)}
        with mock.patch.dict(os.environ, env):
            guard._note_children_policy()
            self.assertEqual(guard._browser_path, str(self.program))
            os.environ[guard.BROWSER_ENV] = str(self.other)     # the code's own change
            with self.assertRaises(guard.SandboxViolation):
                guard.child_command(str(self.other), [])
        # Without a loopback of the run's own, none at all.
        with mock.patch.dict(os.environ, {**env, guard.OS_ISOLATION_ENV: "seatbelt"}):
            guard._note_children_policy()
            self.assertEqual(guard._browser_path, "")


class TheRunIsToldTests(unittest.TestCase):

    def test_the_prompt_tells_of_the_browser_only_when_there_is_one(self) -> None:
        from personalized_agent.agent.work import runner
        with mock.patch.object(isolation, "own_loopback", return_value=True):
            self.assertIn("agent_browser", runner.network_rules(browser=True))
            self.assertNotIn("agent_browser", runner.network_rules(browser=False))
        with mock.patch.object(isolation, "own_loopback", return_value=False):
            self.assertNotIn("agent_browser", runner.network_rules(browser=True))


# ---------------------------------------------------------------------------
# The real browser, in real runs
# ---------------------------------------------------------------------------
SITE = {
    "index.html": """<!doctype html><title>Form</title>
<h1>Hello</h1>
<form id=f><input id=name><select id=size><option value=s>S</option>
<option value=l>L</option></select><button id=go type=button>Go</button></form>
<p id=out></p>
<script>
document.getElementById('go').addEventListener('click', () => {
  document.getElementById('out').textContent =
    'hi ' + document.getElementById('name').value + ' ' + document.getElementById('size').value;
});
console.log('page ready');
</script>""",
    "broken.html": "<p>x</p><script>undefinedFunction()</script>",
}

DRIVE = """
import json, os, subprocess, sys
from agent_browser import Browser, BrowserError, free_port, wait_for_server
port = free_port()
server = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind",
                           "127.0.0.1", "--directory", "site"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
out = {}
try:
    wait_for_server(f"http://127.0.0.1:{port}/")
    with Browser(width=900, height=600) as page:
        page.goto(f"http://127.0.0.1:{port}/index.html")
        out["title"] = page.title
        out["h1"] = page.text("h1")
        page.type("#name", "Ada").select("#size", "l").click("#go")
        page.wait_for("#out")
        out["result"] = page.text("#out")
        out["shot"] = page.screenshot("shots/form.png")
        out["console"] = [one["text"] for one in page.console]
        page.goto(f"http://127.0.0.1:{port}/broken.html")
        out["errors"] = page.errors
        try:
            page.goto("https://example.com/")
            out["internet"] = "OPENED"
        except BrowserError as exc:
            out["internet"] = str(exc)
        try:
            page.goto("http://127.0.0.1:%PORT%/")
            out["other"] = "OPENED " + page.text()
        except BrowserError as exc:
            out["other"] = str(exc)
        out["profile"] = page.profile
    out["profile_left"] = os.path.exists(out["profile"])
finally:
    server.terminate(); server.wait(10)
print(json.dumps(out))
"""


def _last_json(text: str) -> dict:
    for line in reversed((text or "").strip().splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no result printed: {text[-2000:]!r}")


@unittest.skipUnless(REAL_ZIP and os.path.isfile(REAL_ZIP),
                     "set PA_TEST_BROWSER_ZIP to a chrome-headless-shell zip")
@unittest.skipUnless(isolation.own_loopback(STATUS), f"no loopback of a run's own: {STATUS}")
class TheRealBrowserTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp(prefix="pa-realbr-")).resolve()
        cls.internal = cls.root / "internal"
        data = Path(REAL_ZIP).read_bytes()
        browser.install(cls.internal, "155.0.0.0", URL.replace("155.0.1.2", "155.0.0.0"),
                        opener=_opener(data))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def runtime(self, name: str):
        from personalized_agent.runtime.permissions import WorkspacePolicy
        from personalized_agent.runtime.sandbox import SandboxRuntime
        workspace = self.root / "workspace"
        folder = workspace / "agent_work" / name
        (folder / "site").mkdir(parents=True, exist_ok=True)
        for file, text in SITE.items():
            (folder / "site" / file).write_text(text, encoding="utf-8")
        policy = WorkspacePolicy(workspace_root=workspace, writable_root=folder,
                                 readable_paths=[workspace], network=False,
                                 protected_paths=[self.internal])
        return folder, SandboxRuntime(policy, sandbox_enabled=True)

    def test_a_run_drives_its_own_browser_on_its_own_server(self) -> None:
        folder, runtime = self.runtime("A")
        result = runtime.run_python_code(DRIVE.replace("%PORT%", "1"), timeout=180)
        out = _last_json(result.stdout + "\n" + result.stderr)
        self.assertEqual(out["title"], "Form", result.stderr[-1500:])
        self.assertEqual(out["h1"], "Hello")
        self.assertEqual(out["result"], "hi Ada l")
        self.assertIn("page ready", out["console"])
        self.assertTrue(any("undefinedFunction" in one for one in out["errors"]), out)
        self.assertTrue((folder / "shots" / "form.png").read_bytes().startswith(b"\x89PNG"))
        self.assertIn("could not open https://example.com/", out["internet"])
        self.assertFalse(out["profile_left"])

    def test_two_runs_at_once_never_meet(self) -> None:
        """Run A serves and waits; run B's browser cannot open A's server."""
        folder_a, runtime_a = self.runtime("A2")
        _folder_b, runtime_b = self.runtime("B2")
        serve = textwrap.dedent("""
            import socket, http.server, socketserver, os, threading, time
            os.chdir("site")
            srv = socketserver.TCPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
            open("../port.txt", "w").write(str(srv.server_address[1]))
            srv.timeout = 1
            end = time.time() + 40
            while time.time() < end and not os.path.exists("../stop.txt"):
                srv.handle_request()
            """)
        thread = threading.Thread(target=lambda: runtime_a.run_python_code(serve, timeout=120))
        thread.start()
        for _ in range(300):
            if (folder_a / "port.txt").exists() and (folder_a / "port.txt").read_text():
                break
            time.sleep(0.1)
        port = (folder_a / "port.txt").read_text()
        try:
            result = runtime_b.run_python_code(DRIVE.replace("%PORT%", port), timeout=180)
        finally:
            (folder_a / "stop.txt").write_text("x")
            thread.join(60)
        out = _last_json(result.stdout + "\n" + result.stderr)
        self.assertEqual(out["result"], "hi Ada l")          # B's own works
        self.assertFalse(out["other"].startswith("OPENED"), out["other"])
        self.assertIn(f"could not open http://127.0.0.1:{port}/", out["other"])


if __name__ == "__main__":
    unittest.main()
