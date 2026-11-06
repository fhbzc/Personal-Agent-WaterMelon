"""Sizes go by the screen, and what the user sized a window to is kept as a share of it."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

import support  # noqa: F401

from personalized_agent.ui import chat_bubble as cb
from personalized_agent.ui import sizes


def screen(wide, high):
    return SimpleNamespace(winfo_screenwidth=lambda: wide, winfo_screenheight=lambda: high)


class ByTheScreenTests(unittest.TestCase):

    def test_as_written_on_the_screen_they_were_written_for(self) -> None:
        self.assertEqual(sizes.px(screen(*sizes.REFERENCE), 380), 380)

    def test_the_same_share_of_a_larger_one(self) -> None:
        wide = screen(sizes.REFERENCE[0] * 2, sizes.REFERENCE[1] * 2)
        self.assertEqual(sizes.px(wide, 380), 760)

    def test_by_the_side_it_has_less_of(self) -> None:
        self.assertEqual(sizes.scale(screen(sizes.REFERENCE[0] * 3, sizes.REFERENCE[1])), 1.0)

    def test_a_smaller_one_keeps_them_readable(self) -> None:
        self.assertEqual(sizes.px(screen(1024, 600), 380), 380)

    def test_no_end_to_how_large_is_not_wanted(self) -> None:
        self.assertEqual(sizes.scale(screen(9000, 6000)), sizes.MOST)

    def test_a_screen_that_cannot_say_leaves_them(self) -> None:
        self.assertEqual(sizes.scale(object()), 1.0)


class KeptAsAShareTests(unittest.TestCase):

    def test_there_and_back(self) -> None:
        here = screen(1500, 800)
        self.assertEqual(sizes.kept(here, sizes.share(here, (600, 400))), (600, 400))

    def test_the_same_share_on_another_screen(self) -> None:
        self.assertEqual(sizes.kept(screen(3000, 1600), sizes.share(screen(1500, 800), (600, 400))),
                         (1200, 800))

    def test_pixels_kept_by_an_older_version_are_read(self) -> None:
        self.assertEqual(sizes.kept(screen(1500, 800), [420, 300]), (420, 300))

    def test_nothing_or_nonsense_is_nothing(self) -> None:
        for said in ([], None, ["x", "y"], [0, 0], [420]):
            self.assertIsNone(sizes.kept(screen(1500, 800), said), said)


class TheEdgesTests(unittest.TestCase):

    def test_dragged_no_smaller_than_this_screen_allows(self) -> None:
        began = (100, 100, 800, 600, 0, 0, "se")
        self.assertEqual(cb.sized_to(began, -900, -900, (600, 320))[:2], (600, 320))


if __name__ == "__main__":
    unittest.main()
