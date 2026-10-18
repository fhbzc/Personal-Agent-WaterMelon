"""Settings > Shortcuts: keys that work in every program - Delegate work,
Ctrl+Alt+D unless the user sets others - held with the system, refused and
said so when another program holds them."""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

from support import TemporaryAgent

WEB = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"


class _Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)

    def set(self, key, value, **_kw):
        self[key] = value


class ParseTests(unittest.TestCase):

    def test_written_one_way(self) -> None:
        from personalized_agent.platform_layer.hotkeys import parse
        self.assertEqual(parse("ctrl + alt + d")[0], "Ctrl+Alt+D")
        self.assertEqual(parse("Alt+Ctrl+d")[0], "Ctrl+Alt+D")
        self.assertEqual(parse("Ctrl+Shift+F5")[0], "Ctrl+Shift+F5")
        self.assertEqual(parse("Win+Space")[0], "Win+Space")
        self.assertEqual(parse("Ctrl+Alt+D")[1:], (0x0002 | 0x0001, ord("D")))

    def test_what_cannot_be_a_shortcut(self) -> None:
        from personalized_agent.platform_layer.hotkeys import ShortcutError, parse
        for bad in ("", "D", "Shift+D", "Ctrl+Alt", "Ctrl+D+E", "Ctrl+Tab", "Ctrl+F25"):
            with self.subTest(bad=bad), self.assertRaises(ShortcutError):
                parse(bad)


class HeldTests(unittest.TestCase):
    """Held, changed, refused - with the system's own registration, where
    this is Windows."""

    COMBO = "Ctrl+Alt+Shift+F24"          # held by nothing anyone uses

    @unittest.skipUnless(sys.platform == "win32", "Windows holds these with the system")
    def test_held_and_a_second_holder_refused(self) -> None:
        from personalized_agent.platform_layer.hotkeys import Shortcuts, ShortcutError
        pressed = threading.Event()
        first = Shortcuts(_Config(shortcut_delegate_work=self.COMBO),
                          {"delegate_work": pressed.set})
        first.start()
        self.addCleanup(first.stop)
        said = first.describe()["shortcuts"][0]
        self.assertTrue(said["held"], said)
        self.assertEqual(said["combo"], self.COMBO)
        # Another holder of the same keys - another program, as far as the
        # system is concerned - is refused, and keeps what it had.
        config = _Config(shortcut_delegate_work="")
        second = Shortcuts(config, {"delegate_work": lambda: None})
        second.start()
        self.addCleanup(second.stop)
        with self.assertRaises(ShortcutError) as refused:
            second.change("delegate_work", self.COMBO)
        self.assertIn("another program already uses these keys", str(refused.exception))
        self.assertEqual(config.get("shortcut_delegate_work"), "")
        # Let go, and then they can be taken.
        first.stop()
        import time
        time.sleep(0.3)
        second.change("delegate_work", self.COMBO)
        self.assertEqual(config.get("shortcut_delegate_work"), self.COMBO)
        self.assertTrue(second.describe()["shortcuts"][0]["held"])

    def test_turned_off(self) -> None:
        from personalized_agent.platform_layer.hotkeys import Shortcuts
        config = _Config()
        shortcuts = Shortcuts(config, {"delegate_work": lambda: None})
        shortcuts.change("delegate_work", "")
        self.assertEqual(config["shortcut_delegate_work"], "")
        said = shortcuts.describe()["shortcuts"][0]
        self.assertFalse(said["held"])
        self.assertEqual(said["combo"], "")


class RouteTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_set_where_no_desktop_holds_them(self) -> None:
        from personalized_agent.api.routes import ApiError, build_routes

        class _Req:
            def __init__(self, body):
                self.body = body

            def get(self, key, default=None):
                return self.body.get(key, default)
        routes = build_routes(self.app)
        said = routes[("GET", "/api/shortcuts")](_Req({}))
        self.assertEqual(said["shortcuts"][0]["combo"], "Ctrl+Alt+D")
        self.assertTrue(said["selection_button"])
        said = routes[("POST", "/api/shortcuts")](_Req({"key": "delegate_work",
                                                        "combo": "alt+ctrl+k"}))
        self.assertEqual(said["shortcuts"][0]["combo"], "Ctrl+Alt+K")
        with self.assertRaises(ApiError):
            routes[("POST", "/api/shortcuts")](_Req({"key": "delegate_work", "combo": "K"}))
        said = routes[("POST", "/api/shortcuts")](_Req({"selection_button": False}))
        self.assertFalse(said["selection_button"])
        self.assertFalse(self.app.config.get("delegate_selection_button"))


class PageTests(unittest.TestCase):

    def test_a_view_of_its_own(self) -> None:
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-settings-view="shortcuts"', html)
        self.assertIn('data-settings-pane="shortcuts"', html)
        js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn('"privacy", "shortcuts"]', js)
        self.assertIn('api.post("/api/shortcuts"', js)
        # The shortcut opens the quick box, empty, to be written in.
        desktop = (WEB.parents[1] / "ui" / "desktop.py").read_text(encoding="utf-8")
        self.assertIn('self.queue.put(("call", self._show_quick_delegate))', desktop)
        self.assertNotIn("read_now", desktop)


if __name__ == "__main__":
    unittest.main()
