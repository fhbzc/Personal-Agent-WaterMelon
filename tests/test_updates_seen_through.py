"""AN UPDATE SEEN THROUGH, AND ONE THAT HAPPENS (the user, 2026-10-27).

- Every Windows update until 0.2.8 quit and never came back: the shell that
  was to run the installer, started detached, ended without running a word.
  It is started with no window now, and says it is there before the agent
  quits - or nothing is quit.
- The download says its share done, then each step - and the page, which
  outlives the agent, follows it to the new version.
- Check now says what it found on the page, not in a notice.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401

from personalized_agent.platform_layer import updater as up

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "personalized_agent" / "api" / "web"


class _Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _release(tag="v9.0.0"):
    files = [up.ASSET["windows"], up.SUMS]
    return {"tag": tag, "page": f"https://example.org/releases/{tag}",
            "published": "2026-11-01T00:00:00Z", "notes": "", "prerelease": False,
            "draft": False,
            "files": {name: {"url": f"https://example.org/{name}", "size": 3 << 20}
                      for name in files}}


class TheHelperRunsTests(unittest.TestCase):

    def test_started_with_no_window_never_detached(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            updater = up.Updater(_Config(display_language="zh"), Path(work))
            updater.folder.mkdir(parents=True)

            def started(command, **kwargs):
                # The helper says it is there, as the real one does first.
                (updater.folder / up.HELPER_READY).write_text("ready")
                return mock.Mock()
            with mock.patch.object(up.subprocess, "Popen", side_effect=started) as popen:
                updater._after_quit_windows(Path("C:/data/it's here/setup.exe"))
        flags = popen.call_args.kwargs["creationflags"]
        self.assertFalse(flags & getattr(subprocess, "DETACHED_PROCESS", 0x8),
                         "a detached PowerShell ends without running its command")
        if sys.platform == "win32":
            self.assertTrue(flags & subprocess.CREATE_NO_WINDOW)
        script = popen.call_args.args[0][-1]
        self.assertIn("Set-Content -LiteralPath", script, "it says it is there first")
        self.assertIn("'/SILENT'", script, "the installer's own progress window")
        self.assertNotIn("VERYSILENT", script)
        self.assertIn("it''s here", script, "a quote in the path is doubled")

    def test_no_helper_no_quit(self) -> None:
        quit_called = []
        with tempfile.TemporaryDirectory() as work:
            updater = up.Updater(_Config(), Path(work))
            updater.folder.mkdir(parents=True)
            updater.quit = lambda: quit_called.append(True)
            with mock.patch.object(up, "HELPER_READY_WITHIN", 0.3), \
                    mock.patch.object(up.subprocess, "Popen"):      # a helper that says nothing
                with self.assertRaises(RuntimeError) as said:
                    updater.install(Path(work) / "setup.exe", "windows", "v9.0.0")
        self.assertIn("could not be started", str(said.exception))
        self.assertEqual(quit_called, [], "the agent stays: nothing would bring it back")

    @unittest.skipUnless(sys.platform == "win32", "the Windows helper")
    def test_the_real_helper_says_it_is_there(self) -> None:
        """The very shell, started as the updater starts it - it must run its
        command (a detached one did not). Its installer is a path that is not
        there: once this process ends, starting it fails, and nothing runs."""
        if not shutil.which("powershell"):
            self.skipTest("no PowerShell")
        with tempfile.TemporaryDirectory() as work:
            updater = up.Updater(_Config(), Path(work))
            updater.folder.mkdir(parents=True)
            started = time.monotonic()
            updater._after_quit_windows(Path(work) / "no-such-setup.exe")
            self.assertLess(time.monotonic() - started, up.HELPER_READY_WITHIN)
            self.assertFalse((updater.folder / up.HELPER_READY).exists(),
                             "read, and taken away")


class _Answer(io.BytesIO):
    headers = {"Content-Length": str(3 << 20)}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class EachStepIsSaidTests(unittest.TestCase):

    def test_the_download_says_its_share_and_then_checking(self) -> None:
        import hashlib
        payload = b"x" * (3 << 20)
        seen = []
        with tempfile.TemporaryDirectory() as work:
            updater = up.Updater(_Config(), Path(work))
            original = updater._set

            def watch(**values):
                original(**values)
                if "progress" in values or "step" in values:
                    seen.append((updater.state.get("step"),
                                 dict(updater.state.get("progress") or {})))

            def get(url, timeout=30.0):
                if url.endswith(up.SUMS):
                    return _Answer(f"{hashlib.sha256(payload).hexdigest()}  "
                                   f"{up.ASSET['windows']}\n".encode())
                return _Answer(payload)
            with mock.patch.object(updater, "_set", side_effect=watch), \
                    mock.patch.object(up, "_get", side_effect=get), \
                    mock.patch.object(up, "PROGRESS_EVERY", 0.0):
                updater.download(_release(), "windows")
                said = updater.updating()
        steps = [one[0] for one in seen]
        self.assertEqual(steps[0], "downloading")
        self.assertEqual(steps[-1], "checking")
        self.assertTrue(any(0 < p.get("done", 0) < p.get("total", 0) for _, p in seen),
                        "a share part way is said")
        self.assertEqual(seen[-1][1]["done"], len(payload))
        self.assertEqual(said["to"], "v9.0.0")

    def test_a_failed_install_stops_being_followed(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            updater = up.Updater(_Config(updates="install"), Path(work),
                                 working=lambda: False)
            updater.quit = lambda: None

            def broken(release, kind):
                updater._set(step="downloading", updating_to=release["tag"])
                raise RuntimeError("the network went away")
            with mock.patch.object(up, "how_installed", return_value="windows"), \
                    mock.patch.object(up, "published_releases", return_value=[_release()]), \
                    mock.patch.object(updater, "download", side_effect=broken):
                with self.assertRaises(RuntimeError):
                    updater.once()
            self.assertIsNone(updater.updating(), "the page stops following, and says why")

    def test_the_regular_status_carries_it(self) -> None:
        source = (ROOT / "personalized_agent" / "app.py").read_text(encoding="utf-8")
        self.assertIn('"updating": self.updater.updating()', source)


class CheckNowSaysItOnThePageTests(unittest.TestCase):

    def test_no_notice_for_a_look_the_user_asked_for(self) -> None:
        told = []
        with tempfile.TemporaryDirectory() as work:
            updater = up.Updater(_Config(), Path(work),
                                 notify=lambda *a, **k: told.append(a[0]))
            with mock.patch.object(up, "published_releases", return_value=[_release()]), \
                    mock.patch.object(up, "how_installed", return_value=""):
                updater._for_you = True          # Check now
                updater.once()
                self.assertEqual(told, [], "on the page, where the user is looking")
                updater.once()                   # the daily look, after
            self.assertEqual(told, [], "counted as told: not told again")
            self.assertTrue(updater.status()["newer"])

    def test_a_look_of_its_own_still_tells(self) -> None:
        told = []
        with tempfile.TemporaryDirectory() as work:
            updater = up.Updater(_Config(), Path(work),
                                 notify=lambda *a, **k: told.append(a[0]))
            with mock.patch.object(up, "published_releases", return_value=[_release()]), \
                    mock.patch.object(up, "how_installed", return_value=""):
                updater.once()
        self.assertEqual(len(told), 1)

    def test_check_now_marks_the_look_as_asked_for(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            updater = up.Updater(_Config(), Path(work))
            with mock.patch.object(up.threading, "Thread"):
                updater.check_now()
            self.assertTrue(updater._for_you)


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    return source[start:source.index("\n}\n", start) + 3]


class ThePageFollowsItTests(unittest.TestCase):

    def _lines(self, info, gone):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is needed")
        source = (WEB / "app.js").read_text(encoding="utf-8")
        start = source.index("const UPDATE_STEPS = [")
        steps = source[start:source.index("];", start) + 2]
        script = (steps + _function(source, "updateStepLines")
                  + "process.stdout.write(JSON.stringify(updateStepLines("
                  + json.dumps(info) + "," + json.dumps(gone) + ")));")
        done = subprocess.run([node, "-e", script], capture_output=True, text=True,
                              encoding="utf-8", timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_the_download_with_its_share(self) -> None:
        lines = self._lines({"to": "v0.2.8", "step": "downloading",
                             "progress": {"done": 50 << 20, "total": 200 << 20}}, False)
        self.assertEqual([one["state"] for one in lines], ["now", "next", "next", "next"])
        self.assertIn("25% (50 of 200 MB)", lines[0]["text"])

    def test_the_agent_gone_is_starting_again(self) -> None:
        lines = self._lines({"to": "v0.2.8", "step": "installing"}, True)
        self.assertEqual([one["state"] for one in lines], ["done", "done", "done", "now"])
        self.assertIn("the agent is closed meanwhile", lines[3]["text"])

    def test_followed_from_the_regular_status_and_reopened_on_the_new_one(self) -> None:
        source = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("if (status.updating) followUpdate(status.updating);", source)
        follow = source[source.index("async function followUpdate"):]
        follow = follow[:follow.index("\n}\n")]
        self.assertIn('api.get("/api/updates")', follow)
        self.assertIn("location.reload()", follow)
        self.assertIn("UPDATE_SLOW_SECONDS", source)


if __name__ == "__main__":
    unittest.main()
