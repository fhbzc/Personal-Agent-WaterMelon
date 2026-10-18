"""The installed program's own folder is never listened to, wherever it was
installed - as the data folder is not. The default exclusions covered it only
where it goes by default (Program Files, AppData). Run from the source, the
folder is the code someone is working on: their work, and listened to."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

from personalized_agent.common import file_noise


class _Config:
    def __init__(self, root: str = "") -> None:
        self.root = root

    def get(self, key, default=None):
        return self.root if key == "agent_root" else default

    def lines(self, key):
        return []


class TheProgramsFolderTests(unittest.TestCase):

    def test_installed_anywhere_it_is_not_listened_to(self) -> None:
        exe = Path("D:/Apps/PersonalizedAgent/PersonalizedAgent.exe")
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", str(exe)):
            folders = file_noise.excluded_folders(_Config("D:/Apps/PersonalizedAgent/data"))
        self.assertEqual(folders[0], "D:/Apps/PersonalizedAgent/data")
        self.assertIn(str(exe.resolve().parent), folders)

    def test_on_macos_the_whole_bundle(self) -> None:
        exe = Path("/Applications/Personalized Agent.app/Contents/MacOS/Personalized Agent")
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", str(exe)):
            self.assertEqual(Path(file_noise.program_folder()).name, "Personalized Agent.app")

    def test_run_from_the_source_it_is_the_users_work(self) -> None:
        with mock.patch.object(sys, "frozen", False, create=True):
            self.assertEqual(file_noise.program_folder(), "")
            self.assertEqual(file_noise.excluded_folders(_Config("C:/root")), ["C:/root"])


if __name__ == "__main__":
    unittest.main()
