"""The Avatar panel: which avatar sits on the desktop and what it is called.

Watermelon is the first and the default: drawn from shapes, its face drawn
live so its eyes follow the pointer. Its name is the name the user gives it,
or, given none, its own."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from support import TemporaryAgent

from personalized_agent.agent.character import watermelon
from personalized_agent.agent.character.manager import avatar_name

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "personalized_agent" / "resources" / "characters" / "watermelon"


class _Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class WatermelonTests(unittest.TestCase):

    def test_it_is_the_one_shipped_and_the_old_one_is_gone(self) -> None:
        manifest = json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((manifest["name"], manifest["display_name"]),
                         ("watermelon", "Watermelon"))
        self.assertEqual(manifest["rig"]["kind"], "watermelon")
        for state in watermelon.STATES:
            self.assertTrue(list((PACKAGE / state).glob("*.png")), state)
        self.assertTrue((PACKAGE / "layers" / "body.png").exists())
        self.assertFalse((PACKAGE.parent / "default").exists())

    def test_its_eyes_follow_where_it_looks(self) -> None:
        art = watermelon.Watermelon(scale=2, package=PACKAGE)
        cx, cy = watermelon.EYES[0][:2]

        def darkest_x(look):
            frame = art.frame("idle", look=look, size=128).convert("L")
            row = [frame.getpixel((x, int(cy))) for x in range(int(cx) - 8, int(cx) + 9)]
            return row.index(min(row))

        self.assertLess(darkest_x((-1.0, 0.0)), darkest_x((1.0, 0.0)))

    def test_each_mood_draws(self) -> None:
        art = watermelon.Watermelon(scale=2, package=PACKAGE)
        idle = art.frame("idle").tobytes()
        for mood in ("hover", "talking", "thinking", "notification"):
            self.assertNotEqual(art.frame(mood, t=0.5).tobytes(), idle, mood)
        self.assertNotEqual(art.frame("idle", blink=1.0).tobytes(), idle)
        self.assertNotEqual(art.frame("idle", squash=0.85, hop=5).tobytes(), idle)


class ItFacesTheMiddleTests(unittest.TestCase):

    def test_on_the_right_it_faces_left_and_on_the_left_right(self) -> None:
        self.assertEqual(watermelon.facing_for(1700, 128, 1920, "right"), "left")
        self.assertEqual(watermelon.facing_for(100, 128, 1920, "left"), "right")
        # Near the middle it keeps the way it faced.
        self.assertEqual(watermelon.facing_for(900, 128, 1920, "left"), "left")
        self.assertEqual(watermelon.facing_for(900, 128, 1920, "right"), "right")

    def test_turned_round_it_is_the_mirror_and_still_looks_at_the_pointer(self) -> None:
        from PIL import ImageOps
        art = watermelon.Watermelon(scale=2, package=PACKAGE)
        left = art.frame("idle", look=(-0.5, 0.2))
        right = art.frame("idle", look=(0.5, 0.2), facing="right")
        self.assertEqual(ImageOps.mirror(left).tobytes(), right.tobytes())


class ItIsCarriedSmoothlyTests(unittest.TestCase):
    """Dragged, the window follows the pointer's place on the screen - from
    its place within the window, which moves with each step, it shook - and
    a press that becomes a drag does not hop."""

    def _desktop(self):
        from personalized_agent.ui.desktop import DesktopAgent

        class Window:
            def __init__(self):
                self.x, self.y, self.moves = 500, 300, []

            def winfo_x(self):
                return self.x

            def winfo_y(self):
                return self.y

            def geometry(self, spec):
                self.moves.append(spec)
                x, y = spec.lstrip("+").split("+")
                self.x, self.y = int(x), int(y)

        desktop = DesktopAgent.__new__(DesktopAgent)
        desktop.agent_window = Window()
        desktop._live = {"poked": 5.0}
        return desktop

    def test_it_follows_the_pointer_on_the_screen(self) -> None:
        from types import SimpleNamespace as At
        desktop = self._desktop()
        desktop._start_drag(At(x=40, y=30, x_root=540, y_root=330))
        desktop._on_drag(At(x=41, y=30, x_root=542, y_root=331))     # a tremble
        self.assertEqual(desktop.agent_window.moves, [])
        self.assertFalse(desktop._dragged)
        # Whatever the place within the window says - it lags the window.
        for step in range(1, 6):
            desktop._on_drag(At(x=999, y=-5, x_root=540 + 10 * step, y_root=330 + 3 * step))
        self.assertEqual(desktop.agent_window.moves,
                         [f"+{500 + 10 * s}+{300 + 3 * s}" for s in range(1, 6)])
        self.assertTrue(desktop._dragged)
        self.assertEqual(desktop._live["poked"], -10.0)


class TheReadoutWaitsTests(unittest.TestCase):
    """What it is doing, beside it, only once the pointer has RESTED on it -
    two seconds - and never while it is being carried."""

    def _desktop(self, on=True):
        from personalized_agent.ui.desktop import DesktopAgent

        class Root:
            def __init__(self):
                self.waiting = []

            def after(self, ms, work):
                self.waiting.append((ms, work))
                return f"after#{len(self.waiting)}"

            def after_cancel(self, job):
                self.waiting = [one for n, one in enumerate(self.waiting, 1)
                                if f"after#{n}" != job]

        desktop = DesktopAgent.__new__(DesktopAgent)
        desktop.root = Root()
        desktop._hover = None
        desktop._hover_job = None
        desktop._live = {"poked": 5.0}
        desktop.opened = []
        desktop._pointer_on_avatar = lambda: on
        desktop._open_hover = lambda: desktop.opened.append(True)
        return desktop

    def test_not_at_once_but_after_two_seconds_still_on_it(self) -> None:
        desktop = self._desktop()
        desktop._on_hover_enter(object())
        self.assertEqual(desktop.opened, [])
        self.assertEqual(desktop.root.waiting[0][0], 2000)
        desktop.root.waiting[0][1]()
        self.assertEqual(desktop.opened, [True])

    def test_a_pointer_that_passed_on_opens_nothing(self) -> None:
        desktop = self._desktop(on=False)
        desktop._on_hover_enter(object())
        desktop.root.waiting[0][1]()
        self.assertEqual(desktop.opened, [])

    def test_not_while_it_is_carried(self) -> None:
        from types import SimpleNamespace as At

        desktop = self._desktop()
        desktop.agent_window = SimpleWindow()
        desktop._start_drag(At(x=40, y=30, x_root=540, y_root=330))
        desktop._on_drag(At(x=0, y=0, x_root=600, y_root=380))
        self.assertTrue(desktop._carried)
        desktop._on_hover_enter(object())
        self.assertEqual(desktop.root.waiting, [], "nothing waits to open while carried")
        # Let go: it waits its two seconds again.
        desktop._end_drag()
        self.assertEqual(desktop.root.waiting[0][0], 2000)


class SimpleWindow:
    def __init__(self):
        self.x, self.y = 500, 300

    def winfo_x(self):
        return self.x

    def winfo_y(self):
        return self.y

    def geometry(self, spec):
        x, y = spec.lstrip("+").split("+")
        self.x, self.y = int(x), int(y)


class ItsNameTests(unittest.TestCase):

    def test_the_name_given_or_its_own(self) -> None:
        self.assertEqual(avatar_name(_Config(character="watermelon", agent_name="")),
                         "Watermelon")
        self.assertEqual(avatar_name(_Config(character="watermelon", agent_name=" Pip ")),
                         "Pip")

    def test_the_agent_calls_itself_by_it(self) -> None:
        agent = TemporaryAgent()
        try:
            app = agent.app
            self.assertEqual(app.config.get("character"), "watermelon")
            self.assertEqual(app.avatar_name(), "Watermelon")
            self.assertIn("You are Watermelon", app.agent._system_prompt())
            app.config.set("agent_name", "Pip")
            self.assertIn("You are Pip", app.agent._system_prompt())
        finally:
            agent.cleanup()

    def test_the_old_default_gives_way_once(self) -> None:
        agent = TemporaryAgent()
        try:
            store = agent.app.config
            store._values.update({"character": "default", "agent_name": "Aria"})
            store._values.pop(store.WATERMELON_MARK, None)
            store._watermelon_once()
            self.assertEqual(store.get("character"), "watermelon")
            self.assertEqual(store.get("agent_name"), "")
            # Once: named Aria again later, it stays Aria.
            store.set("agent_name", "Aria")
            store._watermelon_once()
            self.assertEqual(store.get("agent_name"), "Aria")
        finally:
            agent.cleanup()


class TheRouteTests(unittest.TestCase):

    def test_the_panel_is_given_the_rig_and_its_layers(self) -> None:
        from personalized_agent.api.routes import build_routes
        agent = TemporaryAgent()
        try:
            routes = build_routes(agent.app)

            class _Ask(dict):
                def get(self, key, default=None):
                    return super().get(key, default)

            said = routes[("GET", "/api/characters")](_Ask(layers="1"))
            one = next(c for c in said["characters"] if c["name"] == "watermelon")
            self.assertEqual(one["display_name"], "Watermelon")
            self.assertEqual(one["rig"]["eyes"], [list(e) for e in watermelon.EYES])
            self.assertTrue(one["layers"]["body"].startswith("data:image/png;base64,"))
            self.assertEqual(said["name"], "Watermelon")
            light = routes[("GET", "/api/characters")](_Ask())
            self.assertNotIn("layers", next(c for c in light["characters"]
                                            if c["name"] == "watermelon"))
        finally:
            agent.cleanup()


if __name__ == "__main__":
    unittest.main()
