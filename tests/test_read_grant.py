"""The home made readable to the agents' code ONCE, in the background - never
inside a run. A grant on the home writes an entry onto every file under it,
one at a time, and it held a run's first script for half an hour; while it
is being made, a run's code simply cannot read there yet. And the
notifications those permission changes raise are not the user opening files."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from personalized_agent.runtime import isolation
from personalized_agent.runtime.isolation import NETWORK_NONE


class _Home(unittest.TestCase):

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="pa-grant-")).resolve()
        isolation._BACKGROUND.clear()

    def tearDown(self) -> None:
        isolation._BACKGROUND.clear()
        shutil.rmtree(self.home, ignore_errors=True)


class TheHomeIsMadeReadableInTheBackgroundTests(_Home):

    def _appcontainer(self):
        return mock.patch.object(isolation, "isolation_status", return_value={
            "isolated": True, "backend": "appcontainer"})

    def test_it_is_granted_once_with_its_progress_said(self) -> None:
        seen = []

        def grant(kind, root, excluded, progress=None):
            for done in range(3):
                progress(done, 3, root / f"part{done}")
                seen.append(dict(isolation._BACKGROUND[isolation._key(root)]))
            return b"s"

        with self._appcontainer(), \
                mock.patch.object(isolation._AppContainer, "grant", side_effect=grant), \
                mock.patch.object(isolation, "credential_stores", return_value=[]):
            isolation.prepare_reading([self.home], [self.home / "internal"])
        self.assertEqual([one["state"] for one in seen], ["running"] * 3)
        self.assertEqual(seen[-1]["done"], 2)
        self.assertEqual(seen[-1]["total"], 3)
        (said,) = isolation.reading_status()
        self.assertEqual(said["state"], "done")
        self.assertEqual(isolation.reading_not_ready(), [])

    def test_a_failure_is_said_and_the_root_stays_unready(self) -> None:
        with self._appcontainer(), \
                mock.patch.object(isolation._AppContainer, "grant",
                                  side_effect=OSError("access denied")), \
                mock.patch.object(isolation, "credential_stores", return_value=[]):
            isolation.prepare_reading([self.home], [])
        (said,) = isolation.reading_not_ready()
        self.assertEqual(said["state"], "failed")
        self.assertIn("access denied", said["error"])

    def test_nothing_is_done_without_appcontainer(self) -> None:
        with mock.patch.object(isolation, "isolation_status", return_value={
                "isolated": True, "backend": "seatbelt"}), \
                mock.patch.object(isolation._AppContainer, "grant") as grant:
            isolation.prepare_reading([self.home], [])
        grant.assert_not_called()
        self.assertEqual(isolation.reading_status(), [])


class ARunNeverWaitsForItTests(_Home):

    def _run(self):
        calls = []
        fake = mock.Mock()
        fake.container_sid.return_value = b"c"
        fake.capability_sid.return_value = b"s"
        fake.launch.return_value = subprocess.CompletedProcess([], 0, "", "")
        work = self.home / "work"
        work.mkdir(exist_ok=True)
        with mock.patch.object(isolation._AppContainer, "api", return_value=fake), \
                mock.patch.object(isolation._AppContainer, "grant",
                                  side_effect=lambda kind, root, ex, progress=None:
                                  calls.append((kind, Path(root))) or b"s"), \
                mock.patch.object(isolation, "code_paths", return_value=[]), \
                mock.patch.object(isolation, "credential_stores", return_value=[]):
            isolation._AppContainer.run(["python"], work, [work], [self.home], [],
                                        NETWORK_NONE, (), {}, 10)
        return calls

    def test_a_root_still_being_made_readable_is_left_out(self) -> None:
        isolation._BACKGROUND[isolation._key(self.home)] = {
            "root": str(self.home), "state": "running", "done": 1, "total": 9}
        calls = self._run()
        self.assertNotIn(("Read", self.home), calls)
        self.assertIn(("Write", self.home / "work"), calls)

    def test_once_it_is_done_the_run_reads_there(self) -> None:
        isolation._BACKGROUND[isolation._key(self.home)] = {
            "root": str(self.home), "state": "done", "done": 9, "total": 9}
        self.assertIn(("Read", self.home), self._run())

    def test_with_nothing_in_the_background_it_is_granted_as_before(self) -> None:
        self.assertIn(("Read", self.home), self._run())


class APermissionChangeIsNotAnOpeningTests(_Home):

    def test_old_and_unread_is_not_recorded_at_all(self) -> None:
        from personalized_agent.listener.providers.filesystem import FilesystemProvider

        path = self.home / "paper.pdf"
        path.write_bytes(b"%PDF-1.4")
        long_ago = time.time() - 3 * 24 * 3600
        os.utime(path, (long_ago, long_ago))      # neither read nor written lately
        provider = FilesystemProvider.__new__(FilesystemProvider)
        provider._seen = __import__("collections").OrderedDict()
        provider._opened = 0
        self.assertEqual(provider._read_or_written(str(path), {}), (None, None))

        os.utime(path, (time.time() - 60, long_ago))   # read a minute ago
        kind, _when = provider._read_or_written(str(path), {})
        self.assertEqual(kind, "filesystem.opened")

    def test_pypdf_says_only_its_errors(self) -> None:
        import logging

        import personalized_agent.listener.providers.filesystem  # noqa: F401
        self.assertEqual(logging.getLogger("pypdf").level, logging.ERROR)


class TheWorkersCodeIsToldTests(_Home):

    def test_run_python_says_what_it_cannot_read_yet(self) -> None:
        from personalized_agent.agent.work import tools as work_tools

        isolation._BACKGROUND[isolation._key(self.home)] = {
            "root": str(self.home), "state": "running", "done": 3, "total": 40}
        tools = work_tools.WorkTools.__new__(work_tools.WorkTools)
        tools.packages_dir = None
        tools._look_over = lambda: {}
        tools._claim = lambda touched: None
        tools.changed = lambda *a, **k: None
        tools.runtime = mock.Mock()
        tools.runtime.run_python_code.return_value = mock.Mock(
            to_dict=lambda: {"ok": True, "stdout": "", "stderr": ""})
        said = tools.run_python({"code": "print(1)"})
        self.assertIn("still being made readable (3/40)", said["reading"])
        self.assertIn("read_file", said["reading"])


if __name__ == "__main__":
    unittest.main()
