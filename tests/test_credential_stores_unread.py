"""The agents' own tools never read where the user's sign-ins are kept.

The readable root is the whole home, and the home holds AppData (browser
profiles and their cookies, some in plain text; DPAPI keys) and its
dot-folders (.ssh, .aws, .git-credentials ...). The operating system keeps
the agents' CODE out of them already; the tools run in the application's
own process, with the user's authority, and are refused here - except a
folder named for the agent that happens to lie inside one."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from personalized_agent.runtime import filesystem as fs
from personalized_agent.runtime.permissions import SecurityViolation, WorkspacePolicy


class TheCredentialStoresAreNotReadTests(unittest.TestCase):

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.home = Path(self.dir.name).resolve() / "home"
        for one in ("AppData/Local/Google/Chrome/User Data/Default",
                    ".ssh", "Documents"):
            (self.home / one).mkdir(parents=True)
        (self.home / "AppData/Local/Google/Chrome/User Data/Default/Cookies").write_text("c")
        (self.home / ".ssh/id_ed25519").write_text("k")
        (self.home / "Documents/paper.md").write_text("mine")
        self.patch = mock.patch("pathlib.Path.home", return_value=self.home)
        self.patch.start()
        fs._stores_cache.update(at=0.0, home="", stores=[])
        self.policy = WorkspacePolicy.create(self.home / "Workspace",
                                             readable_paths=[self.home])

    def tearDown(self) -> None:
        self.patch.stop()
        fs._stores_cache.update(at=0.0, home="", stores=[])
        self.dir.cleanup()

    def test_a_browser_profile_and_keys_are_refused(self) -> None:
        for one in ("AppData/Local/Google/Chrome/User Data/Default/Cookies",
                    ".ssh/id_ed25519"):
            with self.subTest(path=one):
                with self.assertRaises(SecurityViolation) as said:
                    fs.validate_read_path(self.home / one, self.policy)
                self.assertIn("sign-ins are kept", str(said.exception))

    def test_the_users_own_files_are_still_read(self) -> None:
        self.assertEqual(fs.validate_read_path(self.home / "Documents/paper.md", self.policy),
                         (self.home / "Documents/paper.md").resolve())

    def test_a_folder_named_for_the_agent_inside_one_stays_open(self) -> None:
        # A work folder under the temp directory in AppData, say.
        inside = WorkspacePolicy.create(self.home / "AppData/Local/Temp/work",
                                        readable_paths=[self.home])
        mine = inside.writable_root / "notes.md"
        mine.write_text("x")
        self.assertEqual(fs.validate_read_path(mine, inside), mine.resolve())
        # ... and the rest of AppData does not open with it.
        with self.assertRaises(SecurityViolation):
            fs.validate_read_path(
                self.home / "AppData/Local/Google/Chrome/User Data/Default/Cookies", inside)

    def test_listing_the_home_leaves_them_out(self) -> None:
        from personalized_agent.agent.work.tools import WorkTools
        runtime = mock.Mock(policy=self.policy)
        tools = WorkTools(runtime, log=lambda *a, **k: None)
        names = {one["name"] for one in tools.list_dir({"path": str(self.home)})["entries"]}
        self.assertIn("Documents", names)
        self.assertNotIn("AppData", names)
        self.assertNotIn(".ssh", names)


if __name__ == "__main__":
    unittest.main()
