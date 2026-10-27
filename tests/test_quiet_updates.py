"""UPDATES THE USER DOES NOT FEEL (the user, 2026-10-27: "like Claude CLI's").

A newer version is made ready beside the program in the background - what
did not change linked, not written - and taken up at the next start or when
the user is away: the parts moved, the program started and watched, and all
moved back if it does not come up. A version that would not start is never
made ready again.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import support  # noqa: F401

from personalized_agent.platform_layer import quiet_update as quiet
from personalized_agent.platform_layer import updater as up

ROOT = Path(__file__).resolve().parents[1]


class _Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _release(tag="v9.0.0"):
    files = [up.ASSET["windows"], quiet.QUIET_ASSET["windows"], up.SUMS]
    return {"tag": tag, "page": f"https://example.org/{tag}", "published": "", "notes": "New.",
            "prerelease": False, "draft": False,
            "files": {name: {"url": f"https://example.org/{name}", "size": 1} for name in files}}


class MadeReadyBesideTests(unittest.TestCase):

    def test_what_did_not_change_is_linked_not_written(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            live = work / "program"
            (live / "_internal" / "torch").mkdir(parents=True)
            (live / "_internal" / "torch" / "big.dll").write_bytes(b"same" * 1000)
            (live / "Agent.exe").write_bytes(b"old program")
            archive = work / "portable.zip"
            with zipfile.ZipFile(archive, "w") as packed:
                packed.writestr("_internal/torch/big.dll", b"same" * 1000)
                packed.writestr("Agent.exe", b"new program")
                packed.writestr("_internal/api/web/app.js", b"new page")
            said = quiet.unpack_beside(archive, live, work / "ready")
            self.assertEqual(said, {"linked": 1, "written": 2})
            ready = work / "ready"
            self.assertEqual((ready / "Agent.exe").read_bytes(), b"new program")
            self.assertTrue(os.path.samefile(ready / "_internal" / "torch" / "big.dll",
                                             live / "_internal" / "torch" / "big.dll"),
                            "a hard link: no room, no time")
            self.assertEqual((live / "Agent.exe").read_bytes(), b"old program",
                             "the running one is not touched")

    def test_a_path_outside_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            archive = work / "bad.zip"
            with zipfile.ZipFile(archive, "w") as packed:
                packed.writestr("../outside.txt", b"x")
            with self.assertRaises(RuntimeError):
                quiet.unpack_beside(archive, work / "live", work / "ready")
            self.assertFalse((work / "outside.txt").exists())


class WhatIsKeptOfItTests(unittest.TestCase):

    def _staged(self, folder: Path, tag: str, part: Path) -> None:
        quiet._write(folder / quiet.STAGED, {"tag": tag, "kind": "windows",
                                             "dir": str(part.parent), "parts": [str(part)],
                                             "live_parts": [str(part)]})

    def test_ready_while_it_is_there_and_newer(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            folder, part = Path(work) / "updates", Path(work) / "ready" / "_internal"
            part.mkdir(parents=True)
            self._staged(folder, "v99.0.0", part)
            self.assertEqual(quiet.staged(folder)["tag"], "v99.0.0")
            self._staged(folder, "v0.0.1", part)
            self.assertIsNone(quiet.staged(folder), "not newer: let go")
            self.assertFalse((folder / quiet.STAGED).exists())

    def test_one_that_would_not_start_is_never_ready_again(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            folder, part = Path(work) / "updates", Path(work) / "ready" / "_internal"
            part.mkdir(parents=True)
            self._staged(folder, "v99.0.0", part)
            quiet._write(folder / quiet.RESULT, {"ok": False, "tag": "v99.0.0",
                                                 "why": "the new version did not start"})
            said = quiet.take_result(folder)
            self.assertFalse(said["ok"])
            self.assertIn("v99.0.0", quiet.skipped(folder))
            self.assertIsNone(quiet.staged(folder))
            self.assertFalse(part.exists(), "what was made ready goes")
            self.assertIsNone(quiet.take_result(folder), "told once")

    def test_parts_that_would_not_move_are_tried_again_three_times(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            folder, part = Path(work) / "updates", Path(work) / "ready" / "_internal"
            part.mkdir(parents=True)
            self._staged(folder, "v99.0.0", part)
            for attempt in range(1, quiet.MOST_TRIES + 1):
                quiet._write(folder / quiet.RESULT, {"ok": False, "tag": "v99.0.0",
                                                     "why": "could not move it aside"})
                quiet.heed_result(folder)
                quiet.heed_result(folder)          # heeded once, however often read
                if attempt < quiet.MOST_TRIES:
                    self.assertEqual(quiet.staged(folder)["tag"], "v99.0.0",
                                     "kept, to be tried again")
            self.assertIn("v99.0.0", quiet.skipped(folder))
            self.assertIsNone(quiet.staged(folder))


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(sys.platform == "win32", "the Windows helper, run for real")
class TheWindowsHelperRunsTests(unittest.TestCase):
    """The very PowerShell, run: the parts moved, a program started and
    watched. The "program" is this Python, listening on a port - or not."""

    def setUp(self) -> None:
        if not shutil.which("powershell"):
            self.skipTest("no PowerShell")
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.work = Path(work.name)
        self.live = self.work / "program"
        (self.live / "_internal").mkdir(parents=True)
        (self.live / "_internal" / "version.txt").write_text("old", encoding="utf-8")
        self.ready = self.work / "program" / ".update" / "v9.0.0"
        (self.ready / "_internal").mkdir(parents=True)
        (self.ready / "_internal" / "version.txt").write_text("new", encoding="utf-8")
        self.result = self.work / "applied.json"
        self.ready_file = self.work / "helper-ready.txt"
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        self.pid = gone.pid
        self.started = []

    def _run(self, program: str, port: int, seconds: int) -> dict:
        script = self.work / "program.py"
        script.write_text(program, encoding="utf-8")
        pairs = [[str(self.live / "_internal"), str(self.ready / "_internal"),
                  str(self.live / ".update" / "previous" / "_internal")]]
        helper = self.work / "take-up.ps1"
        with mock.patch.object(quiet, "HEALTHY_WITHIN", seconds):
            text = quiet.windows_helper(self.pid, pairs, [sys.executable, str(script)], port,
                                        self.result, self.ready_file, "v9.0.0", self.live)
        helper.write_text(text.replace(quiet.UNINSTALL_KEY, "HKCU:\\NoSuchKeyForThisTest"),
                          encoding="utf-8-sig")
        steps = Path(str(self.result) + ".log")
        try:
            # A CI machine is slower than a desk's: minutes, not seconds.
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                            "Bypass", "-File", str(helper)], timeout=seconds + 240,
                           capture_output=True)
        except subprocess.TimeoutExpired:
            self.fail("the helper did not finish - its steps:\n"
                      + (steps.read_text(encoding="utf-8-sig") if steps.exists() else "none"))
        self.addCleanup(self._end_started)
        self.assertTrue(steps.exists(), "it says what it did, step by step")
        return json.loads(self.result.read_text(encoding="utf-8-sig"))

    def _end_started(self) -> None:
        # The "program" the helper started ends by itself (see the scripts).
        time.sleep(0.1)

    def test_up_in_time_it_is_kept(self) -> None:
        port = _free_port()
        said = self._run(f"""
import socket, time
s = socket.socket(); s.bind(("127.0.0.1", {port})); s.listen(1)
time.sleep(20)
""", port, 30)
        self.assertTrue(said["ok"], said)
        self.assertEqual((self.live / "_internal" / "version.txt").read_text(encoding="utf-8"),
                         "new")
        self.assertEqual((self.live / ".update" / "previous" / "_internal" / "version.txt")
                         .read_text(encoding="utf-8"), "old", "the old kept beside")
        self.assertTrue(self.ready_file.exists(), "it said it was there")

    def test_not_up_it_is_taken_back(self) -> None:
        said = self._run("import sys; sys.exit(3)\n", _free_port(), 15)
        self.assertFalse(said["ok"])
        self.assertIn("did not start", said["why"])
        self.assertEqual((self.live / "_internal" / "version.txt").read_text(encoding="utf-8"),
                         "old", "the old one back in its place")
        self.assertEqual((self.ready / "_internal" / "version.txt").read_text(encoding="utf-8"),
                         "new", "the new one back where it was made ready")


@unittest.skipIf(sys.platform == "win32", "the Mac and Linux helper, run for real")
class ThePosixHelperRunsTests(unittest.TestCase):

    def test_up_in_time_it_is_kept_else_taken_back(self) -> None:
        if not shutil.which("bash"):
            self.skipTest("no bash")
        for exits, kept in ((False, True), (True, False)):
            with self.subTest(exits=exits), tempfile.TemporaryDirectory() as work:
                work = Path(work)
                live, new = work / "App.AppImage", work / ".update" / "v9.0.0" / "App.AppImage"
                new.parent.mkdir(parents=True)
                live.write_text("old")
                new.write_text("new")
                port = _free_port()
                program = work / "program.py"
                program.write_text("import sys; sys.exit(3)\n" if exits else f"""
import socket, time
s = socket.socket(); s.bind(("127.0.0.1", {port})); s.listen(1)
time.sleep(20)
""")
                result, ready = work / "applied.json", work / "ready.txt"
                with mock.patch.object(quiet, "HEALTHY_WITHIN", 15):
                    text = quiet.posix_helper(999999, [[str(live), str(new),
                                                         str(work / ".update" / "previous" /
                                                             "App.AppImage")]],
                                              [sys.executable, str(program)], port, result,
                                              ready, "v9.0.0", str(program))
                script = work / "take-up.sh"
                script.write_text(text)
                subprocess.run(["bash", str(script)], timeout=120, capture_output=True)
                said = json.loads(result.read_text())
                self.assertEqual(said["ok"], kept)
                self.assertEqual(live.read_text(), "new" if kept else "old")


class TheUpdaterTakesItUpQuietlyTests(unittest.TestCase):

    def setUp(self) -> None:
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.folder = Path(work.name)
        self.away = None
        self.working = False
        self.updater = up.Updater(_Config(), self.folder, working=lambda: self.working,
                                  away=lambda: self.away)
        self.quit = []
        self.updater.quit = lambda: self.quit.append(True)
        self.made_ready, self.taken_up = [], []
        patches = [mock.patch.object(up, "how_installed", return_value="windows"),
                   mock.patch.object(up, "published_releases", return_value=[_release()]),
                   mock.patch.object(quiet, "can_make_ready", return_value=True),
                   mock.patch.object(self.updater, "make_ready",
                                     side_effect=lambda r, k, shown=False:
                                     self.made_ready.append((r["tag"], shown))),
                   mock.patch.object(self.updater, "take_up_now",
                                     side_effect=lambda: self.taken_up.append(True))]
        for one in patches:
            one.start()
            self.addCleanup(one.stop)

    def test_by_default_told_not_made_ready(self) -> None:
        # The user, 2026-10-27: looked for and told by default.
        told = []
        self.updater.notify = lambda *a, **k: told.append(a[0])
        self.updater.once()
        self.assertEqual(self.made_ready, [])
        self.assertEqual(len(told), 1)

    def test_had_by_itself_made_ready_in_the_background_not_taken_up(self) -> None:
        self.updater.config["updates"] = "install"
        self.working = True                       # work running changes nothing yet
        self.updater.once()
        self.assertEqual(self.made_ready, [("v9.0.0", False)], "not shown: in the background")
        self.assertEqual(self.taken_up, [])
        self.assertEqual(self.quit, [])

    def test_asked_for_it_is_made_ready_quietly_too(self) -> None:
        # Every update a quiet one - the one asked for too: used from the
        # next start, or at once with Restart now.
        self.updater.install_now("v9.0.0")
        self.updater.once()
        self.assertEqual(self.made_ready, [("v9.0.0", False)])
        self.assertEqual(self.taken_up, [], "not taken up at once")

    def test_restart_now_uses_it_unless_work_runs(self) -> None:
        self.working = True
        with self.assertRaises(RuntimeError):
            self.updater.restart_now()
        self.assertEqual(self.taken_up, [])
        self.working = False
        self.updater.restart_now()
        self.assertEqual(self.taken_up, [True])

    def test_a_version_that_did_not_start_is_not_made_ready_again(self) -> None:
        quiet.skip(self.updater.folder, "v9.0.0")
        self.updater.once()
        self.assertEqual(self.made_ready, [])

    def test_taken_up_only_when_the_user_is_away_and_no_work_runs(self) -> None:
        with mock.patch.object(self.updater, "_ready", return_value={"tag": "v9.0.0"}):
            for away, working, taken in ((None, False, 0), (60.0, False, 0),
                                         (up.AWAY_SECONDS + 1, True, 0),
                                         (up.AWAY_SECONDS + 1, False, 1)):
                with self.subTest(away=away, working=working):
                    self.taken_up.clear()
                    self.away, self.working = away, working
                    self.updater._take_up_when_away()
                    self.assertEqual(len(self.taken_up), taken)

    def test_one_taken_back_is_said_once(self) -> None:
        told = []
        self.updater.notify = lambda *a, **k: told.append(a[0])
        quiet._write(self.updater.folder / quiet.RESULT,
                     {"ok": False, "tag": "v9.0.0", "why": "the new version did not start"})
        self.updater._said_taken_back()
        self.assertIn("was not kept", self.updater.status()["error"])
        self.assertEqual(len(told), 1)
        self.updater._said_taken_back()
        self.assertEqual(len(told), 1)


class TakenUpAtTheStartTests(unittest.TestCase):

    def test_before_anything_opens(self) -> None:
        source = (ROOT / "personalized_agent" / "__main__.py").read_text(encoding="utf-8")
        taken = source.index("take_up_at_start(")
        self.assertLess(source.index("guard = SingleInstance("), taken,
                        "only once no other copy runs")
        self.assertLess(taken, source.index("application = AgentApplication("))

    def test_a_ready_version_is_handed_to_its_helper(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            folder = root / "internal" / "updates"
            part = root / "ready" / "_internal"
            part.mkdir(parents=True)
            quiet._write(folder / quiet.STAGED, {"tag": "v99.0.0", "kind": "windows",
                                                 "dir": str(part.parent), "parts": [str(part)],
                                                 "live_parts": [str(part)], "notes": "New."})
            with mock.patch.object(up, "how_installed", return_value="windows"), \
                    mock.patch.object(quiet, "run_helper") as helper:
                self.assertTrue(quiet.take_up_at_start(root, ["--headless"]))
            self.assertEqual(helper.call_args.args[4], ["--headless"])
            marker = json.loads((folder / up.MARKER).read_text(encoding="utf-8"))
            self.assertEqual(marker["to"], "v99.0.0", "what is new, for the new version")

    def test_nothing_ready_nothing_done(self) -> None:
        with tempfile.TemporaryDirectory() as work, \
                mock.patch.object(up, "how_installed", return_value="windows"), \
                mock.patch.object(quiet, "run_helper") as helper:
            self.assertFalse(quiet.take_up_at_start(Path(work), []))
        helper.assert_not_called()


if __name__ == "__main__":
    unittest.main()
