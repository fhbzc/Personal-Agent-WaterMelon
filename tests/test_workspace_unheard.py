"""The whole workspace is left out of the file listener: it holds only what
the agents make and keep, and each run writes from a process of its own."""

from __future__ import annotations

import os
import unittest

from support import TemporaryAgent


class TheWorkspaceIsNotHeardTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.store = self.agent.app.config

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_memory_line_becomes_the_workspace_and_the_users_stay(self) -> None:
        workspace = str(self.store.get("workspace_root"))
        mine = os.path.join(os.path.expanduser("~"), "Scratch")
        self.store._values["listener_directory_exclusions"] = "\n".join(
            [os.path.join(workspace, "data"), mine])
        self.store._values.pop(self.store.WORKSPACE_UNHEARD_MARK, None)
        self.store._workspace_unheard_once()
        lines = self.store.get("listener_directory_exclusions").splitlines()
        self.assertIn(workspace, lines)
        self.assertIn(mine, lines, "the user's own line kept")
        self.assertNotIn(os.path.join(workspace, "data"), lines)
        # Once: taken out again later, it stays out.
        self.store.set("listener_directory_exclusions", mine)
        self.store._workspace_unheard_once()
        self.assertEqual(self.store.get("listener_directory_exclusions"), mine)

    def test_a_fresh_install_leaves_it_out(self) -> None:
        from personalized_agent.common.config.schema import default_directory_exclusions
        from personalized_agent.common.paths import default_root
        self.assertIn(str(default_root() / "workspace"),
                      default_directory_exclusions().splitlines())


if __name__ == "__main__":
    unittest.main()
