"""What the operating system must allow, beside each switch that needs it -
looked at without asking, on every platform, with the place it is switched
on opened only on the user's click; nothing written."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from personalized_agent.platform_layer import permissions


class _Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class OnEachPlatformTests(unittest.TestCase):

    def test_macos_each_switch_and_its_permissions(self) -> None:
        with mock.patch.object(permissions, "platform", lambda: "macos"), \
                mock.patch.object(permissions, "_checks", lambda: {
                    "full_disk": lambda: False, "accessibility": lambda: True,
                    "automation": lambda: None, "screen": lambda: True,
                    "notifications": lambda: None}):
            said = permissions.status(_Config(listener_screenshots_enabled=False))
        items = {one["key"]: one for one in said["items"]}
        self.assertEqual(said["by_switch"]["listener_filesystem_enabled"], ["full_disk"])
        self.assertEqual(said["by_switch"]["listener_application_enabled"],
                         ["accessibility", "automation"])
        self.assertEqual(said["missing"], ["full_disk"])
        # A switch that is off needs nothing, and is not looked at.
        self.assertFalse(items["screen"]["needed"])
        self.assertIsNone(items["screen"]["granted"])
        self.assertTrue(items["notifications"]["can_open"])

    def test_windows_notifications_read_not_written(self) -> None:
        if not os.name == "nt":
            self.skipTest("the registry is Windows'")
        with mock.patch("winreg.SetValueEx") as written, \
                mock.patch("winreg.CreateKey") as made:
            said = permissions._windows_notifications()
        self.assertIn(said, (True, False, None))
        written.assert_not_called()
        made.assert_not_called()

    def test_windows_off_for_this_program(self) -> None:
        if not os.name == "nt":
            self.skipTest("the registry is Windows'")
        import winreg

        def query(key, name):
            return (0 if name == "Enabled" else 1, 4)
        with mock.patch.object(winreg, "OpenKey", mock.MagicMock()), \
                mock.patch.object(winreg, "QueryValueEx", query):
            self.assertFalse(permissions._windows_notifications())

    def test_linux_wayland_is_said_and_has_nothing_to_open(self) -> None:
        with mock.patch.object(permissions, "platform", lambda: "linux"), \
                mock.patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"}):
            said = permissions.status(_Config())
        item = said["items"][0]
        self.assertEqual((item["key"], item["granted"], item["can_open"]),
                         ("x11_session", False, False))
        self.assertEqual(said["by_switch"]["listener_screenshots_enabled"], ["x11_session"])
        with mock.patch.object(permissions, "platform", lambda: "linux"):
            self.assertFalse(permissions.open_place("x11_session"))

    def test_opened_on_windows_at_its_settings_page(self) -> None:
        ran = []

        class Done:
            returncode = 0
        with mock.patch.object(permissions, "platform", lambda: "windows"), \
                mock.patch.object(permissions.children, "run_short",
                                  lambda command, **kw: ran.append(command) or Done()):
            self.assertTrue(permissions.open_place("notifications"))
            self.assertFalse(permissions.open_place("full_disk"))
        self.assertEqual(ran, [["cmd", "/c", "start", "", "ms-settings:notifications"]])


class TheRoutesAndThePageTests(unittest.TestCase):

    def test_routes(self) -> None:
        from support import TemporaryAgent
        from personalized_agent.api.errors import ApiError
        from personalized_agent.api.routes import build_routes
        agent = TemporaryAgent()
        try:
            routes = build_routes(agent.app)

            class Req:
                def __init__(self, body):
                    self.body = body

                def get(self, key, default=None):
                    return self.body.get(key, default)

                def require(self, key):
                    return self.body[key]
            said = routes[("GET", "/api/permissions")](Req({}))
            self.assertEqual(said["platform"], permissions.platform())
            with self.assertRaises(ApiError):
                routes[("POST", "/api/permissions/open")](Req({"key": "nothing"}))
        finally:
            agent.cleanup()

    def test_the_page_says_it_beside_the_switch(self) -> None:
        web = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
        script = (web / "app.js").read_text(encoding="utf-8")
        self.assertIn("allowed.dataset.sysPermissionFor = field.key", script)
        self.assertIn('api.get("/api/permissions")', script)
        # Looked at as the settings open and when asked - never each time the
        # window comes back to the front (on a Mac each look asks System Events).
        self.assertNotIn('window.addEventListener("focus"', script)


if __name__ == "__main__":
    unittest.main()
