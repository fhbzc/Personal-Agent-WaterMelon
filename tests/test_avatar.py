"""The Avatar panel: which avatar sits on the desktop and what it is called.

Watermelon is the first and the default: drawn from shapes, its face drawn
live so its eyes follow the pointer. Its name is the name the user gives it,
or, given none, its own."""

from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

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


class TuckedIntoTheSideTests(unittest.TestCase):
    """THE USER, 2026-11-04: left beside the side of the screen it sneaks in
    after a while, only a leaf out; a click brings it out. Carried, it looks
    held."""

    def _desktop(self, x, y=300):
        from personalized_agent.agent.character.watermelon import Watermelon
        from personalized_agent.ui.desktop import DesktopAgent

        class Window:
            def __init__(self):
                self.x, self.y = x, y

            def winfo_x(self):
                return self.x

            def winfo_y(self):
                return self.y

            def winfo_screenwidth(self):
                return 1920

            def geometry(self, spec):
                _x, _y = spec.lstrip("+").split("+")
                self.x, self.y = int(_x), int(_y)

        desktop = DesktopAgent.__new__(DesktopAgent)
        desktop.agent_window = Window()
        desktop.size = 128
        desktop._live = {"poked": 5.0}
        desktop._rig = SimpleNamespace(TUCKS=True)
        desktop._on_hover_enter = lambda *a: None
        return desktop

    def _sides(self, left=True, right=True):
        from personalized_agent.ui.avatar_tuck import Sides
        return mock.patch("personalized_agent.ui.avatar_tuck.sides_of",
                          return_value=Sides(0, 1920, left, right))

    def _finish(self, desktop):
        desktop._slide_on(desktop._live, time.monotonic() + 5)

    def _carry(self, desktop, to):
        at = SimpleNamespace
        desktop._start_drag(at(x=64, y=64, x_root=desktop.agent_window.x + 64,
                               y_root=desktop.agent_window.y + 64))
        desktop._on_drag(at(x=0, y=0, x_root=to + 64, y_root=desktop.agent_window.y + 64))

    def _clickable(self, desktop):
        waiting = []
        desktop.root = SimpleNamespace(after=lambda ms, fn: waiting.append(fn) or "job",
                                       after_cancel=lambda job: None)
        desktop._click_job = None
        desktop._live["start"] = 0.0
        desktop.double_click_seconds = lambda: 0.5
        return waiting

    def test_the_press_does_nothing_to_be_seen_and_letting_go_there_is_the_click(self) -> None:
        # The user, 2026-11-04: it hopped at the press and opened the chat.
        desktop = self._desktop(500)
        waiting = self._clickable(desktop)
        at = SimpleNamespace(x=64, y=64, x_root=564, y_root=364)
        desktop._start_drag(at)
        self.assertEqual(desktop._live["poked"], 5.0, "no hop")
        self.assertEqual(waiting, [], "no chat on its way")
        desktop._end_drag(at)
        self.assertEqual(len(waiting), 1, "let go where it was pressed: a click")
        self.assertNotEqual(desktop._live["poked"], 5.0, "the hop comes now")

    def test_dragged_it_is_held_at_once_and_no_click_follows(self) -> None:
        desktop = self._desktop(500)
        waiting = self._clickable(desktop)
        desktop._start_drag(SimpleNamespace(x=64, y=64, x_root=564, y_root=364))
        desktop._on_drag(SimpleNamespace(x=0, y=0, x_root=570, y_root=364))
        self.assertTrue(desktop._carried, "held from the first step")
        with self._sides():
            desktop._end_drag(SimpleNamespace(x_root=570, y_root=364))
        self.assertEqual(waiting, [])

    def test_let_go_without_its_press_is_no_click(self) -> None:
        # A Mac's Control-click is a right click: only its let go comes here.
        desktop = self._desktop(500)
        waiting = self._clickable(desktop)
        desktop._end_drag(SimpleNamespace(x_root=564, y_root=364))
        self.assertEqual(waiting, [])

    def test_where_it_goes_in(self) -> None:
        from personalized_agent.ui import avatar_tuck as tuck
        sides = tuck.Sides(0, 1920, True, True, 0, 1040, True, True)
        self.assertEqual(tuck.side_to_tuck(10, 400, 128, sides), "left")
        self.assertEqual(tuck.side_to_tuck(-40, 400, 128, sides), "left", "past it too")
        self.assertEqual(tuck.side_to_tuck(1780, 400, 128, sides), "right")
        self.assertEqual(tuck.side_to_tuck(900, 10, 128, sides), "top")
        self.assertEqual(tuck.side_to_tuck(900, 900, 128, sides), "bottom")
        self.assertEqual(tuck.side_to_tuck(900, 400, 128, sides), "")
        # Where the avatar starts is not beside the side.
        self.assertEqual(tuck.side_to_tuck(1920 - 128 - 60, 1080 - 128 - 120, 128, sides), "")
        self.assertEqual(tuck.tucked_at("right", 1800, 400, 128, sides), (1920 - 34, 400))
        self.assertEqual(tuck.tucked_at("left", 5, 400, 128, sides), (34 - 128, 400))
        self.assertEqual(tuck.tucked_at("top", 900, 5, 128, sides), (900, 34 - 128))
        self.assertEqual(tuck.tucked_at("bottom", 900, 900, 128, sides), (900, 1040 - 34))
        for side, x, y in (("right", 1800, 400), ("left", 5, 400), ("top", 900, 5),
                           ("bottom", 900, 900)):
            back = tuck.back_at(side, x, y, 128, sides)
            self.assertEqual(tuck.side_to_tuck(*back, 128, sides), side, "out, still beside it")
            point = tuck.seen_at(side, *tuck.tucked_at(side, x, y, 128, sides), 128)
            self.assertTrue(0 <= point[0] < 1920 and 0 <= point[1] < 1040, side)

    def test_never_towards_another_screen_or_the_taskbar(self) -> None:
        from personalized_agent.ui import avatar_tuck as tuck
        self.assertEqual(tuck.side_to_tuck(1780, 400, 128, tuck.Sides(0, 1920, True, False)), "")
        self.assertEqual(tuck.side_to_tuck(5, 400, 128, tuck.Sides(0, 1920, False, True)), "")
        taskbar = tuck.Sides(0, 1920, True, True, 0, 1040, True, False)
        self.assertEqual(tuck.side_to_tuck(900, 900, 128, taskbar), "")
        self.assertEqual(tuck.side_to_tuck(900, 5, 128, taskbar), "top", "the top still is")

    def test_at_the_top_it_goes_up_and_comes_down_to_look(self) -> None:
        from personalized_agent.ui.avatar_tuck import Sides
        sides = Sides(0, 1920, True, True, 0, 1040, True, False)
        desktop = self._desktop(900, 300)
        with mock.patch("personalized_agent.ui.avatar_tuck.sides_of", return_value=sides):
            desktop._start_drag(SimpleNamespace(x=64, y=64, x_root=964, y_root=364))
            desktop._on_drag(SimpleNamespace(x=0, y=0, x_root=964, y_root=70))
            desktop._end_drag()
            self.assertEqual(desktop._beside, "top")
            desktop._beside_since = 100.0
            desktop._live["start"] = 0.0
            self._sneak(desktop, 500)
            self.assertTrue(self._sneak(desktop, 505).done)
            desktop._sneaked_in(desktop._live)
            self.assertEqual((desktop.agent_window.x, desktop.agent_window.y), (900, 34 - 128))
            self.assertEqual(desktop._reach(desktop._live), 34.0)
            desktop._live["hover"] = True
            desktop._peek_on(desktop._live)
            self._finish(desktop)
            self.assertEqual(desktop.agent_window.y, 92 - 128)
            self.assertEqual(desktop._reach(desktop._live), 92.0)
            desktop._start_drag(SimpleNamespace(x=5, y=60, x_root=964, y_root=20))
            desktop._end_drag()
            self._finish(desktop)
        self.assertEqual(desktop.agent_window.y, 16)

    def test_the_leaves_lie_along_each_side(self) -> None:
        from personalized_agent.agent.character.watermelon import Watermelon
        rig = Watermelon(scale=2)
        for edge, box in (("top", (0, 256 - 68, 256, 256)), ("bottom", (0, 0, 256, 68))):
            seen = rig.frame("idle", size=256, tuck=1.0, grip=1.0, edge=edge).crop(box)
            shown = seen.getchannel("A").getbbox()
            self.assertGreater(shown[2] - shown[0], 180, f"{edge}: along it")
            self.assertEqual(shown[1] if edge == "top" else shown[3], 0 if edge == "top" else 68,
                             f"{edge}: and reach it")

    def _sneak(self, desktop, seconds, on_it=False):
        """Time passing: `seconds` after it began, or would begin."""
        return desktop._sneak_on(desktop._live, seconds, on_it)

    def _beside(self, desktop):
        with self._sides():
            self._carry(desktop, 1785)
            desktop._end_drag()
        desktop._beside_since = 100.0
        desktop._live["start"] = 0.0

    def test_not_at_once_and_not_while_anything_goes_on(self) -> None:
        from personalized_agent.ui.avatar_tuck import SNEAK_AFTER
        desktop = self._desktop(1500)
        self._beside(desktop)
        self.assertEqual(desktop._beside, "right")
        self.assertEqual(getattr(desktop, "_tucked", ""), "", "not at once")
        with self._sides():
            self.assertIsNone(self._sneak(desktop, 100 + SNEAK_AFTER - 1))
            self.assertIsNone(self._sneak(desktop, 100 + SNEAK_AFTER + 1, on_it=True))
            self.assertEqual(desktop._beside_since, 100 + SNEAK_AFTER + 1, "the wait begins again")
            desktop.state = "thinking"
            self.assertIsNone(self._sneak(desktop, 200))
            desktop.state = "idle"
            self.assertIsNone(self._sneak(desktop, 200 + SNEAK_AFTER - 1))
            self.assertIsNotNone(self._sneak(desktop, 200 + SNEAK_AFTER + 1))

    def test_it_looks_round_tiptoes_in_and_only_then_slips_off(self) -> None:
        from personalized_agent.ui.avatar_tuck import sneak_at
        start = sneak_at(0.0)
        self.assertEqual((start.walk, start.tuck, start.look), (0.0, 0.0, -1.0))
        self.assertEqual(sneak_at(0.6).look, 1.0, "the other way")
        self.assertEqual(sneak_at(1.0).walk, 0.0, "and has not moved yet")
        self.assertGreater(sneak_at(1.45).hop, 0, "a step")
        self.assertEqual(sneak_at(1.85).hop, 0, "a pause")
        walked = [sneak_at(n / 10).walk for n in range(40)]
        self.assertEqual(walked, sorted(walked))
        self.assertEqual(sneak_at(3.0).tuck, 0.0, "peeking, all of it")
        self.assertTrue(sneak_at(4.0).done)
        self.assertEqual(sneak_at(4.0).tuck, 1.0)

    def test_in_only_a_leaf_and_a_click_brings_it_out(self) -> None:
        desktop = self._desktop(1500)
        self._beside(desktop)
        with self._sides():
            self.assertEqual(self._sneak(desktop, 500).walk, 0.0)
            step = self._sneak(desktop, 505)
            self.assertTrue(step.done)
            desktop._sneaked_in(desktop._live)
            self.assertEqual((desktop._tucked, desktop._beside), ("right", ""))
            self.assertEqual(desktop.agent_window.x, 1920 - 34)
            self.assertEqual(desktop._live["tuck"], 1.0)
            self.assertEqual(desktop._live["grip"], 1.0)
            # The leaf clicked: no chat, no hop, it comes out.
            desktop._click_job = None
            desktop._on_click(SimpleNamespace(x_root=1900, y_root=360))
            self.assertIsNone(desktop._click_job)
            desktop._start_drag(SimpleNamespace(x=5, y=60, x_root=1900, y_root=360))
            desktop._end_drag()
            self.assertEqual(desktop._tucked, "")
            self._finish(desktop)
        self.assertEqual(desktop.agent_window.x, 1920 - 128 - 16)
        self.assertEqual(desktop._live["tuck"], 0.0)
        self.assertEqual(desktop._live["grip"], 0.0, "its leaves back under it")
        self.assertEqual(desktop._beside, "right", "and in a while it goes in again")

    def test_caught_at_it_it_goes_back(self) -> None:
        desktop = self._desktop(1500)
        self._beside(desktop)
        with self._sides():
            self._sneak(desktop, 500)
            self._sneak(desktop, 502.3)
            self.assertGreater(desktop.agent_window.x, 1785)
            self.assertIsNone(self._sneak(desktop, 502.4, on_it=True))
        self.assertNotIn("sneak", desktop._live)
        self._finish(desktop)
        self.assertEqual(desktop.agent_window.x, 1785)
        self.assertEqual(desktop._live["poked"], 502.4, "with a hop")
        self.assertEqual(desktop._live["caught_until"], 502.4 + desktop.CAUGHT_SECONDS, "and that face")

    def test_dragged_by_its_leaf_it_comes_out_in_the_hand(self) -> None:
        desktop = self._desktop(1920 - 34)
        desktop._tucked = "right"
        desktop._live["tuck"] = 1.0
        desktop._start_drag(SimpleNamespace(x=5, y=60, x_root=1900, y_root=360))
        desktop._on_drag(SimpleNamespace(x=0, y=0, x_root=1700, y_root=500))
        self.assertEqual(desktop._tucked, "")
        self.assertEqual(desktop._live["tuck"], 0.0)
        self.assertEqual((desktop.agent_window.x, desktop.agent_window.y), (1700 - 64, 500 - 64))

    def test_let_go_elsewhere_it_stays(self) -> None:
        desktop = self._desktop(500)
        with self._sides():
            self._carry(desktop, 800)
            desktop._end_drag()
        self.assertEqual(desktop._beside, "")
        desktop._live["start"] = 0.0
        self.assertIsNone(self._sneak(desktop, 9999))

    def test_only_a_leaf_is_out(self) -> None:
        from PIL import ImageChops
        from personalized_agent.agent.character.watermelon import Watermelon
        from personalized_agent.ui.avatar_tuck import PEEK
        rig = Watermelon(scale=2)
        out = int(256 * PEEK)
        box = (0, 0, out, 256)
        tucked = rig.frame("idle", size=256, tuck=1.0, grip=1.0).crop(box)
        self.assertIsNone(ImageChops.difference(
            tucked, rig.leaves_at(1.0, 34).crop(box)).getbbox(alpha_only=False), "nothing of the body")
        seen = tucked.getchannel("A").getbbox()
        self.assertGreater(seen[3] - seen[1], 180, "the leaves stand up the side")
        self.assertEqual(seen[2], out, "and reach it")
        turned = rig.frame("idle", size=256, tuck=1.0, grip=1.0, facing="right")
        turned = turned.crop((256 - out, 0, 256, 256)).getchannel("A").getbbox()
        self.assertEqual(turned[0], 0, "on the left side, the same")

    def test_the_pointer_on_its_leaf_brings_its_head_out_and_away_back_in(self) -> None:
        desktop = self._desktop(1920 - 34)
        desktop._tucked = "right"
        desktop._live.update(tuck=1.0, grip=1.0, hover=True, edge=1920)
        with self._sides():
            desktop._peek_on(desktop._live)
            self._finish(desktop)
            self.assertEqual(desktop.agent_window.x, 1920 - 68, "both eyes out")
            self.assertEqual(desktop._live["tuck"], 0.0)
            self.assertEqual(desktop._live["grip"], 1.0, "the leaves still holding on")
            self.assertEqual(desktop._reach(desktop._live), 68.0, "at the side as it moved")
            self.assertEqual(desktop._tucked, "right", "still in the side")
            desktop._peek_on(desktop._live)
            self.assertNotIn("slide", desktop._live, "once")
            desktop._live["hover"] = False
            desktop._peek_on(desktop._live)
            self._finish(desktop)
        self.assertEqual(desktop.agent_window.x, 1920 - 34)
        self.assertEqual(desktop._live["tuck"], 1.0)

    def test_looking_out_a_click_still_brings_it_out(self) -> None:
        desktop = self._desktop(1920 - 68)
        desktop._tucked = "right"
        desktop._live.update(tuck=0.0, hover=True, peeking=True)
        with self._sides():
            desktop._start_drag(SimpleNamespace(x=5, y=60, x_root=1900, y_root=360))
            desktop._end_drag()
            self._finish(desktop)
        self.assertEqual((desktop._tucked, desktop.agent_window.x), ("", 1920 - 128 - 16))
        self.assertFalse(desktop._live["peeking"])

    def test_pulled_it_leans_back_and_let_go_it_stands(self) -> None:
        desktop = self._desktop(500)
        live = desktop._live
        self.assertEqual(desktop._lean_on(live, 1.0, True, 500), 0.0, "not moved yet")
        leant = [desktop._lean_on(live, 1.0 + n / 20, True, 500 + 40 * n) for n in range(1, 9)]
        self.assertGreater(leant[-1], 5, "pulled right, it tips back to the left")
        self.assertLessEqual(leant[-1], desktop.LEAN_MOST)
        self.assertEqual(leant, sorted(leant), "after the hand, not at once")
        for n in range(30):
            desktop._lean_on(live, 3.0 + n / 20, False, 820)
        self.assertEqual(live["lean"], 0.0)
        desktop._lean_on(live, 5.0, True, 820)
        back = [desktop._lean_on(live, 5.0 + n / 20, True, 820 - 40 * n) for n in range(1, 5)]
        self.assertLess(back[-1], 0, "the other way, the other side")

    def test_the_body_leans_the_same_way_whichever_way_it_faces(self) -> None:
        from PIL import ImageChops, ImageOps
        from personalized_agent.agent.character.watermelon import Watermelon
        rig = Watermelon(scale=2)
        upright = rig.frame("grabbed", size=256)
        left = rig.frame("grabbed", size=256, lean=9.0)
        self.assertIsNotNone(ImageChops.difference(upright, left).getbbox(alpha_only=False))
        leaves = rig.leaves.getchannel("A").getbbox()
        self.assertEqual(rig.frame("idle", size=256, lean=9.0, facing="right").size, (256, 256))
        turned = rig.frame("idle", size=256, lean=9.0, facing="right")
        self.assertIsNone(ImageChops.difference(
            ImageOps.mirror(turned), rig.frame("idle", size=256, lean=-9.0)).getbbox(alpha_only=False))
        self.assertTrue(leaves)

    def test_held_and_sneaking_it_looks_it(self) -> None:
        from PIL import ImageChops
        from personalized_agent.agent.character.watermelon import Watermelon
        rig = Watermelon(scale=2)
        for mood in ("grabbed", "sneaky"):
            self.assertIsNotNone(ImageChops.difference(rig.frame(mood), rig.frame("idle")).getbbox(alpha_only=False))


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
