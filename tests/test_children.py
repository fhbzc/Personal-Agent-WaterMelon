"""A child the agent starts ends when the agent ends - by any route, a crash
included - on Windows by a job object, on Linux and macOS by a watcher."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest

from support import ROOT

from personalized_agent.common import children


def _alive(pid: int) -> bool:
    if sys.platform.startswith("win"):
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x102  # timeout
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _gone_within(pid: int, seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return not _alive(pid)


SLEEPER = "import time; time.sleep(120)"


class AChildEndsWithTheAgentTests(unittest.TestCase):

    def test_a_child_outlives_nothing_when_its_parent_dies(self) -> None:
        """The parent starts a long child through the helper, says its pid,
        and dies at once without cleaning up - the way a crash does."""
        parent = (
            "import os, sys, time\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "from personalized_agent.common import children\n"
            f"child = children.popen([sys.executable, '-c', {SLEEPER!r}])\n"
            "time.sleep(1.5)\n"
            "print(child.pid, flush=True)\n"
            "os._exit(0)\n")
        done = subprocess.run([sys.executable, "-c", parent], capture_output=True,
                              text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        pid = int(done.stdout.strip().splitlines()[-1])
        # On Linux and macOS that pid is the watcher's: it and the child are
        # one group, and ending it ends both.
        self.assertTrue(_gone_within(pid, 8),
                        f"pid {pid} outlived the parent that started it")

    def test_running_out_of_time_ends_the_whole_tree(self) -> None:
        """A child that starts its own child: a timeout ends both."""
        script = (
            "import subprocess, sys, time\n"
            f"g = subprocess.Popen([sys.executable, '-c', {SLEEPER!r}])\n"
            "print(g.pid, flush=True)\n"
            "time.sleep(120)\n")
        started = time.time()
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            children.run([sys.executable, "-c", script], capture_output=True,
                         text=True, timeout=4)
        self.assertLess(time.time() - started, 30)
        said = caught.exception.output or ""
        if isinstance(said, bytes):
            said = said.decode()
        grandchild = int(said.strip().splitlines()[0])
        self.assertTrue(_gone_within(grandchild, 8),
                        "the grandchild outlived the timeout")

    def test_an_ordinary_call_returns_as_subprocess_would(self) -> None:
        done = children.run([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
                            input="hello", capture_output=True, text=True,
                            timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), "HELLO")

    @unittest.skipIf(sys.platform.startswith("win"), "the watcher is for Linux and macOS")
    def test_elsewhere_the_child_runs_under_a_watcher(self) -> None:
        command = children._watched(["echo", "hi"])
        self.assertIn(str(os.getpid()), command)
        self.assertEqual(command[-2:], ["echo", "hi"])


class SettingsSavedFromManyThreadsTests(unittest.TestCase):
    """The round and the patterns beside it save settings at once; every
    save goes through one .tmp file, and none may fail."""

    def test_saves_at_once_all_land(self) -> None:
        import threading
        from support import TemporaryAgent

        agent = TemporaryAgent()
        try:
            config = agent.app.config
            failed = []

            def save(n: int) -> None:
                try:
                    for i in range(15):
                        config.set(f"test_thread_{n}", i, internal=True)
                except Exception as exc:
                    failed.append(exc)

            threads = [threading.Thread(target=save, args=(n,)) for n in range(6)]
            for one in threads:
                one.start()
            for one in threads:
                one.join()
            self.assertEqual(failed, [])
            for n in range(6):
                self.assertEqual(config.get(f"test_thread_{n}"), 14)
        finally:
            agent.cleanup()
