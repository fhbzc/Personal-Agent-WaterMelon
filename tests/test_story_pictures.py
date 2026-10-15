"""A story's picture, whole (connections/avatars.fuller_picture and
round._keep_picture): a page shows a picture cut to fit - "800a", "-800x530",
a thumbnail - and names the whole one beside it; that one is kept."""

from __future__ import annotations

import unittest

from support import TemporaryAgent

from personalized_agent.heartbeat.connections import avatars
from personalized_agent.heartbeat.connections.avatars import fuller_picture

PAGE = """<html><head>
<meta property="og:image" content="https://cdn.example.org/gfx/news/hires/2026/stock-photo.jpg">
</head><body><p>Text.</p>
<div class="article-gallery" data-thumb="https://cdn.example.org/csz/news/tmb/2026/figure-one.jpg"
     data-src="https://cdn.example.org/gfx/news/hires/2026/figure-one.jpg">
  <figure><img src="https://cdn.example.org/csz/news/800a/2026/figure-one.jpg" width="800"
               height="530"></figure></div></body></html>"""


class TheWholePictureTests(unittest.TestCase):

    def test_the_gallery_names_it_whole(self) -> None:
        self.assertEqual(
            fuller_picture(PAGE, "https://cdn.example.org/csz/news/800a/2026/figure-one.jpg",
                           "https://news.example.org/a-story.html"),
            "https://cdn.example.org/gfx/news/hires/2026/figure-one.jpg")

    def test_the_widest_of_a_srcset(self) -> None:
        page = ('<img src="https://ex.org/up/2026/fig-800x530.jpg" srcset="'
                'https://ex.org/up/2026/fig-800x530.jpg 800w, https://ex.org/up/2026/fig.jpg 1600w">')
        self.assertEqual(fuller_picture(page, "https://ex.org/up/2026/fig-800x530.jpg",
                                        "https://ex.org/a"), "https://ex.org/up/2026/fig.jpg")

    def test_a_whole_one_is_not_traded_for_a_lesser(self) -> None:
        page = PAGE + '<img src="https://cdn.example.org/gfx/news/2026/stock-photo.jpg">'
        self.assertIsNone(fuller_picture(
            page, "https://cdn.example.org/gfx/news/hires/2026/stock-photo.jpg",
            "https://news.example.org/a-story.html"))

    def test_a_thumbnail_is_never_the_whole_one(self) -> None:
        self.assertIsNone(fuller_picture(
            '<img src="https://ex.org/thumb/pic.jpg">', "https://ex.org/a/pic.jpg", "https://ex.org/"))

    def test_nothing_named_beside_it(self) -> None:
        self.assertIsNone(fuller_picture('<img src="/a/b.jpg">', "https://ex.org/a/b.jpg",
                                         "https://ex.org/"))


class KeptWholeTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.round = self.agent.app.connections_round
        self.asked = []

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _get(self, url):
        self.asked.append(url)
        if url.endswith(".html"):
            return PAGE.encode("utf-8"), "text/html; charset=utf-8"
        if "/hires/" in url:
            return b"WHOLE" * 20, "image/jpeg"
        return b"CUT" * 20, "image/jpeg"

    def test_the_model_chose_the_cut_copy_the_whole_one_is_kept(self) -> None:
        name = self.round._keep_picture(
            "https://cdn.example.org/csz/news/800a/2026/figure-one.jpg", page=False,
            get=self._get, avatars=avatars, around="https://news.example.org/a-story.html")
        self.assertTrue(name)
        kept = self.agent.app.connections.briefing_picture_path(name).read_bytes()
        self.assertTrue(kept.startswith(b"WHOLE"))

    def test_without_the_page_the_choice_as_it_is(self) -> None:
        name = self.round._keep_picture(
            "https://cdn.example.org/csz/news/800a/2026/figure-one.jpg", page=False,
            get=self._get, avatars=avatars)
        kept = self.agent.app.connections.briefing_picture_path(name).read_bytes()
        self.assertTrue(kept.startswith(b"CUT"))


if __name__ == "__main__":
    unittest.main()
