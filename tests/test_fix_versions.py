"""A fix of a version - 0.3.7.1 (the user, 2026-11-16: "既然是3.7的补丁，变成0.3.7.1").

Newer than the version it fixes, wherever versions are compared; four numbers
in Windows' own version, three in the macOS bundle (Apple asks for three, and
only the tag's run is notarized - too late to find out).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import ROOT  # noqa: F401 - puts the repository on the path

from build.macos import build_macos
from build.windows import build_windows
from personalized_agent.platform_layer import updater as up


class AFixIsNewerTests(unittest.TestCase):
    def test_a_fix_is_newer_than_what_it_fixes_and_older_than_the_next(self) -> None:
        self.assertTrue(up.is_newer("v0.3.7.1", "0.3.7"))
        self.assertTrue(up.is_newer("v0.3.8", "0.3.7.1"))
        self.assertFalse(up.is_newer("v0.3.7", "0.3.7.1"))
        self.assertFalse(up.is_newer("v0.3.7.1", "0.3.7.1"))

    def test_a_fix_can_be_gone_back_from(self) -> None:
        self.assertTrue(up.can_go_back_to("v0.3.7", "0.3.7.1"))


class TheBuildsSayItTests(unittest.TestCase):
    def test_the_windows_version_has_four_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as work, \
                mock.patch.object(build_windows, "work_dir", lambda target: Path(work)), \
                mock.patch.object(build_windows, "__version__", "0.3.7.1"):
            text = build_windows.write_version_resource().read_text(encoding="utf-8")
        self.assertIn("filevers=(0, 3, 7, 1)", text)
        self.assertIn("prodvers=(0, 3, 7, 1)", text)
        self.assertIn("StringStruct('ProductVersion', '0.3.7.1')", text)

    def test_a_version_of_three_numbers_ends_in_nought_on_windows(self) -> None:
        with tempfile.TemporaryDirectory() as work, \
                mock.patch.object(build_windows, "work_dir", lambda target: Path(work)), \
                mock.patch.object(build_windows, "__version__", "0.3.8"):
            text = build_windows.write_version_resource().read_text(encoding="utf-8")
        self.assertIn("filevers=(0, 3, 8, 0)", text)

    def test_the_macos_bundle_says_three_numbers(self) -> None:
        self.assertEqual(build_macos.bundle_version("0.3.7.1"), "0.3.7")
        self.assertEqual(build_macos.bundle_version("0.3.8"), "0.3.8")
        self.assertEqual(build_macos.bundle_version("1.0"), "1.0.0")


class TheNotesAreThereTests(unittest.TestCase):
    def test_this_version_has_its_notes_and_a_fix_is_marked_required(self) -> None:
        notes = ROOT / "docs" / "release-notes" / f"v{up.__version__}.md"
        self.assertTrue(notes.is_file(), notes)
        if len(up.__version__.split(".")) == 4:
            self.assertIn(up.REQUIRED_MARK, notes.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
