"""A FOLDER OR A FILE OPENED IN FRONT (the user, 2026-11-08: "folder都不是直接跳出来的而是打开，你可以让他直接
跳出来"). Everything here is made up; nothing on the screen is moved."""

from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401

from personalized_agent.platform_layer import front_folder as ff


class InFrontTests(unittest.TestCase):

    def test_its_window_found_by_its_name(self) -> None:
        windows = [(1, "Tides - Firefox"), (2, "Report.docx - Word"), (3, "harbour"), (4, "harbour notes")]
        self.assertEqual(ff.window_named("harbour", windows), 3, "the folder's own, not one like it")
        self.assertEqual(ff.window_named("Report.docx", windows), 2)
        self.assertIsNone(ff.window_named("nowhere", windows))
        self.assertIsNone(ff.window_named("", windows))

    def test_looked_for_until_it_is_there_and_brought_forward(self) -> None:
        shown = iter([[], [], [(9, "harbour")]])
        brought = []
        with mock.patch.object(ff, "LOOK_EVERY", 0), \
                mock.patch("personalized_agent.platform_layer.wechat_open._front", brought.append):
            self.assertTrue(ff._bring_forward("harbour", time.monotonic(), find=lambda: next(shown)))
        self.assertEqual(brought, [9])

    def test_not_there_in_time_nothing_is_moved(self) -> None:
        brought = []
        with mock.patch.object(ff, "LOOK_SECONDS", 0.05), mock.patch.object(ff, "LOOK_EVERY", 0.01), \
                mock.patch("personalized_agent.platform_layer.wechat_open._front", brought.append):
            self.assertFalse(ff._bring_forward("harbour", time.monotonic(), find=lambda: []))
        self.assertEqual(brought, [])



class AWorksFolderTests(unittest.TestCase):
    """The user, 2026-11-08: "所有的work status的细节都会有open folder的选项"."""

    def test_its_own_folder_opened_in_front_and_said_when_it_is_gone(self) -> None:
        import tempfile
        from support import TemporaryAgent
        from personalized_agent.api.errors import ApiError
        from personalized_agent.api.routes import build_routes
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        app = agent.app
        folder = tempfile.mkdtemp(prefix="work-folder-")
        app.db.execute("INSERT INTO work_runs (id, unit_id, unit_name, folder, status, instruction, plan, "
                       "log, final, started_at, updated_at) VALUES ('r1', 'u1', 'Tides', ?, 'done', '', "
                       "'{}', '[]', '{}', '2026-11-01', '2026-11-01')", (folder,))
        routes = build_routes(app)
        opened = []
        with mock.patch.object(ff, "opened_in_front", opened.append):
            said = routes[("POST", "/api/work/folder")](SimpleNamespace(require=lambda key: "r1",
                                                                        get=lambda key, d=None: d))
        self.assertEqual([str(one) for one in opened], [folder])
        self.assertEqual(said["opened"], folder)
        import shutil
        shutil.rmtree(folder, ignore_errors=True)
        with self.assertRaises(ApiError):
            app.open_run_folder("r1")


if __name__ == "__main__":
    unittest.main()
