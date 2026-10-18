"""The chat's list and its notes, as drawn on both pages - the main window's
Chat tab and the chat window - with titles as long as real ones.

One long title made the whole list as wide as itself, and every row's menu
sat at the far right of that width, past the edge of the list: no row showed
its menu, "hello" no more than the long one. And a note in the margin folded
only from its small triangle, not from its top row."""

from __future__ import annotations

import html
import json
import re
import unittest

import test_chat_navigation as nav

PROBE = r"""
  window.addEventListener('load', async () => {
    const wait = (ms) => new Promise(r => setTimeout(r, ms));
    const out = {};
    try {
      await wait(400);
      const side = document.querySelector('#chat-sidebar');
      if (side.hidden) document.querySelector('[data-chat-sidebar-open]').click();
      await wait(200);
      const edge = side.getBoundingClientRect().right;
      const more = [...document.querySelectorAll('.chat-nav-more')];
      out.rows = more.length;
      out.outside = more.filter(b => b.getBoundingClientRect().right > edge + 0.5).length;
      const short = [...document.querySelectorAll('.chat-nav-line')]
        .find(l => l.textContent.startsWith('hello'));
      out.shortInside = short.querySelector('.chat-nav-more').getBoundingClientRect().right <= edge;
      short.querySelector('.chat-nav-more').click(); await wait(100);
      out.menu = [...document.querySelectorAll('.chat-nav-menu button')].map(b => b.textContent);
      document.body.click(); await wait(50);
      // The note in the margin: its whole top row folds it.
      const card = document.querySelector('.followup');
      card.querySelector('.fu-title').click(); await wait(50);
      out.foldedByTitle = card.classList.contains('folded');
      card.querySelector('.fu-head').click(); await wait(50);
      out.unfoldedByRow = !card.classList.contains('folded');
      card.querySelector('.fu-toggle').click(); await wait(50);
      out.foldedByTriangle = card.classList.contains('folded');
      card.querySelector('.fu-toggle').click(); await wait(50);
      card.querySelector('.fu-close').click(); await wait(50);
      out.closeDidNotFold = !card.classList.contains('folded');
    } catch (e) { out.error = String(e && e.stack || e); }
    document.title = JSON.stringify(out);
  });
"""


class TheListAndItsNotesTests(unittest.TestCase):

    def _page(self, filename: str) -> str:
        page = nav.ChatNavigationUiTests._page(None, filename)
        page = page.replace(
            "title:'Conversation '+(i+1)",
            "title: i === 3 ? 'hello' : 'Please look at the replication package for the "
            "paper and tell me what is missing before Friday '+(i+1)")
        page = page.split("<script>" + nav.CHECKS + "</script>")[0]
        return page + "<script>" + PROBE + "</script></body></html>"

    def test_every_rows_menu_is_in_the_list_and_a_notes_row_folds_it(self) -> None:
        engine = nav._browser()
        if not engine:
            self.skipTest("a browser is needed to draw the page")
        for filename in ("index.html", "chat.html"):
            with self.subTest(page=filename):
                stdout, stderr = nav._run_browser_probe(engine, self._page(filename),
                                                        size="1400,900", budget=6000)
                title = re.search(r"<title>(.*?)</title>", stdout, re.S)
                self.assertIsNotNone(title, stderr[-1500:])
                out = json.loads(html.unescape(title.group(1)))
                self.assertNotIn("error", out, out)
                self.assertEqual(out["rows"], 46)
                self.assertEqual(out["outside"], 0, "a row's menu past the edge of the list")
                self.assertTrue(out["shortInside"])
                self.assertEqual(out["menu"], ["Pin", "Rename", "Delete"])
                self.assertTrue(out["foldedByTitle"], "the title folds it")
                self.assertTrue(out["unfoldedByRow"], "the row unfolds it")
                self.assertTrue(out["foldedByTriangle"], "the triangle still does")
                self.assertTrue(out["closeDidNotFold"])


if __name__ == "__main__":
    unittest.main()
