"""The live avatar's frames drawn once and kept (the user, 2026-11-16: "不拖慢电脑整体的速度" - a still avatar
was drawn afresh six times a second, a fifth of a core on the window's thread)."""

from __future__ import annotations

import inspect
import unittest

from support import ROOT  # noqa: F401 - puts the repository on the path

from personalized_agent.ui import avatar_frames as af


class TheBreathTests(unittest.TestCase):
    def test_it_goes_round_its_poses(self) -> None:
        poses = {af.breathing(step * af.BREATH_SECONDS / af.BREATH_STEPS) for step in range(af.BREATH_STEPS * 3)}
        self.assertLessEqual(len(poses), af.BREATH_STEPS)
        self.assertGreater(len(poses), af.BREATH_STEPS // 2, "still a breath, not a jump")
        self.assertAlmostEqual(af.breathing(0.0), af.breathing(af.BREATH_SECONDS))
        self.assertLessEqual(max(poses), 1 + af.BREATH_DEPTH + 1e-9)
        self.assertGreaterEqual(min(poses), 1 - af.BREATH_DEPTH - 1e-9)


class TheKeptFramesTests(unittest.TestCase):
    def frames_of(self, seconds: float, step: float = 0.16, **said) -> af.FrameCache:
        cache = af.FrameCache()
        t = 0.0
        while t < seconds:
            look, blink, squash, hop, smile = af.rounded((0.0, 0.0), 0.0, af.breathing(t), 0.0, 0.0)
            key = cache.key(said.get("mood", "idle"), look, blink, squash, hop, 128, "left", smile,
                            said.get("more", {}), False)
            if cache.get(key) is None:
                cache.put(key, object())
            t += step
        return cache

    def test_a_still_avatar_is_drawn_for_one_breath_then_only_shown(self) -> None:
        cache = self.frames_of(30.0)
        self.assertLessEqual(cache.drawn, af.BREATH_STEPS)
        self.assertGreater(cache.reused, 150)

    def test_what_moves_with_time_is_drawn_every_time(self) -> None:
        for mood in ("talking", "thinking"):
            cache = self.frames_of(5.0, mood=mood)
            self.assertEqual(cache.reused, 0, mood)
        cache = self.frames_of(5.0, more={"tuck": 0.4})
        self.assertEqual(cache.reused, 0)

    def test_rounded_past_what_shows(self) -> None:
        a = af.rounded((0.301, -0.499), 0.012, 1.00731, 0.2, 0.51)
        b = af.rounded((0.299, -0.501), 0.0, 1.007309, 0.0, 0.49)
        self.assertEqual(a, b)
        self.assertNotEqual(af.rounded((0.3, 0.0), 0, 1, 0, 0), af.rounded((0.4, 0.0), 0, 1, 0, 0))
        self.assertIsNone(af.rounded((0, 0), 0, 1, 0, None)[4], "no smile given stays none")

    def test_the_oldest_let_go(self) -> None:
        cache = af.FrameCache(kept=3)
        for n in range(5):
            cache.put(("k", n), n)
        self.assertEqual(len(cache), 3)
        self.assertIsNone(cache.get(("k", 0)))
        self.assertEqual(cache.get(("k", 4)), 4)


class TheFlatFaceOnlyWhereItIsTests(unittest.TestCase):
    """The flat avatar's face textured and scaled only where it is drawn (22 of a frame's 29 ms were the whole
    layer's): every pixel as when the whole layer was."""

    def test_every_pixel_as_when_the_whole_layer_was(self) -> None:
        import itertools
        from unittest import mock

        import numpy as np

        from personalized_agent.agent.character import watermelon_flat as wf
        rig = wf.Watermelon(scale=2)
        poses = list(itertools.product(["idle", "hover", "grabbed", "sneaky", "notification"],
                                       [(0.0, 0.0), (1.0, -1.0), (-0.6, 0.4)], [0.0, 1.0], [0.0, 0.6]))
        near = [np.asarray(rig.frame(m, look, blink, 0.5, 1.01, 0.0, size=128, smile=smile))
                for m, look, blink, smile in poses]
        with mock.patch.object(wf, "FACE_MARGIN", 1000):      # the whole layer, as before
            whole = [np.asarray(rig.frame(m, look, blink, 0.5, 1.01, 0.0, size=128, smile=smile))
                     for m, look, blink, smile in poses]
        for pose, a, b in zip(poses, near, whole):
            self.assertEqual(int(np.abs(a.astype(int) - b.astype(int)).max()), 0, pose)

    def test_the_grain_is_made_once(self) -> None:
        from personalized_agent.agent.character import watermelon_flat as wf
        self.assertIs(wf._grain(2), wf._grain(2))


class TheAvatarUsesThemTests(unittest.TestCase):
    def test_the_window_draws_through_the_kept_frames(self) -> None:
        from personalized_agent.ui.desktop import DesktopAgent
        source = inspect.getsource(DesktopAgent._animate_live)
        self.assertIn("avatar_frames.breathing(t)", source)
        self.assertIn("cache.get(key)", source)
        self.assertIn('live.get("shown_key") != shown_now', source, "the same frame is not shown again")
        self.assertIn("self._frame_cache = None", inspect.getsource(DesktopAgent._load_character))


if __name__ == "__main__":
    unittest.main()
