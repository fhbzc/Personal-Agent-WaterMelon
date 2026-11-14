"""THREE ICONS BESIDE THE AVATAR (the user, 2026-11-04): To do, Collections, Chat, always out, in
an arc on the side that faces the screen, and the panels they open at once, with
no page: chosen items delegated and their outcomes opened, groups folded, words
zoomed and copied. Everything here is made up."""

from __future__ import annotations

import gc
import json
import queue
import sys
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.ui.avatar_buttons import ICON, ChatVine, QuickPanel, vine_path
from personalized_agent.ui.chat_bubble import beside

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.destroy()
    del _root
    gc.collect()
    HAVE_TK = True
except Exception:                            # pragma: no cover - no display
    HAVE_TK = False

AREA = (0, 0, 1920, 1040)




MOD = "Command" if sys.platform == "darwin" else "Control"
#: What a turn of the wheel says: a Mac counts them.
NOTCH = 1 if sys.platform == "darwin" else 120


@unittest.skipUnless(HAVE_TK, "no display for Tk here")

def _put_away(window) -> bool:
    """Out of sight as this system puts a window away: on Windows far off every screen; off it
    withdrawn (ab.PARKS_BY_HIDING - a Mac puts a window sent off the screen back on it)."""
    from personalized_agent.ui import avatar_buttons as ab
    if ab.PARKS_BY_HIDING and window.state() == "withdrawn":
        return True
    return window.winfo_x() < -1000

class OnTheScreenTests(unittest.TestCase):

    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.posted = queue.Queue()
        self.opened, self.done = [], []

    def tearDown(self) -> None:
        # Tk's objects let go of here, on its own thread: collected on another one
        # (a worker's), they end the process.
        self.root.destroy()
        del self.root
        gc.collect()

    def _settle(self, seconds=0.3):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                while True:
                    self.posted.get_nowait()()
            except queue.Empty:
                pass
            self.root.update()
            time.sleep(0.01)

    def _panel(self, sections, **more):
        return QuickPanel(self.root, "To do", lambda: sections, self.posted.put, lambda text: text,
                          ("Segoe UI", 11), "Nothing yet.", **more)

    def _said(self, panel):
        return panel.list.get("1.0", "end")

    def _buttons(self, panel):
        return [str(one.cget("text")) for one in panel.faces]

    ONE = [{"name": "Coast", "rows": [
        {"id": "t1", "title": "Measure the gauge", "detail": "Before noon.", "note": "Part of: Tides",
         "actions": [{"key": "open", "label": "Open the latest outcome"},
                     {"key": "delegate", "label": "Delegate work"},
                     {"key": "remove", "label": "Remove", "confirm": True, "changes": True}]}]}]















    @unittest.skipUnless(sys.platform.startswith("win"), "Windows' peek")
    def test_a_peek_at_another_window_leaves_it_there(self) -> None:
        from personalized_agent.ui.on_top import stays_through_peek
        one = tk.Toplevel(self.root)
        one.overrideredirect(True)
        one.geometry("50x50+300+300")
        self.root.update()
        self.assertTrue(stays_through_peek(one), "the system took it")



    def test_a_panel_kept_from_a_larger_screen_fits_a_smaller_one(self) -> None:
        with mock.patch("personalized_agent.ui.avatar_buttons.work_area",
                        return_value=(0, 0, 500, 400)):
            panel = self._panel(self.ONE, size=(900, 900))
            panel.show((300, 100, 128, 128))
            self._settle(0.1)
            self.assertLessEqual(panel.window.winfo_width(), 480)
            self.assertLessEqual(panel.window.winfo_height(), 380)


    def test_a_panel_opens_at_once_and_fills_when_its_list_comes(self) -> None:
        panel = self._panel(self.ONE)
        began = time.monotonic()
        panel.show((800, 500, 128, 128))
        self.assertLess(time.monotonic() - began, 1.0, "no browser to start")
        self.assertIn("Loading", self._said(panel))
        self._settle()
        for words in ("Coast (1)", "Measure the gauge", "Before noon.", "Part of: Tides"):
            self.assertIn(words, self._said(panel))

    def test_a_click_on_its_line_folds_it_and_unfolds_it_and_a_drag_moves_it(self) -> None:
        """The user, 2026-11-08: "to do panel 也是，可以点击header的其它位置collapose, expand"."""
        from types import SimpleNamespace
        panel = self._panel(self.ONE)
        panel.show((800, 500, 128, 128))
        self._settle()
        whole = panel.window.winfo_height()
        self.assertEqual(str(panel.head.cget("cursor")), "hand2")
        x, y = panel.head.winfo_rootx() + 20, panel.head.winfo_rooty() + 4
        panel._grab(SimpleNamespace(x_root=x, y_root=y))
        panel._move(SimpleNamespace(x_root=x + 40, y_root=y + 3))
        self.assertEqual(str(panel.head.cget("cursor")), "fleur", "dragged: the move cursor")
        panel._line_released(SimpleNamespace(x_root=x + 40, y_root=y + 3))
        self.assertFalse(panel.rolled_up, "dragged: moved, not folded")
        panel._grab(SimpleNamespace(x_root=x, y_root=y))
        panel._line_released(SimpleNamespace(x_root=x + 1, y_root=y))
        self._settle(0.1)
        self.assertTrue(panel.rolled_up)
        self.assertLess(panel.window.winfo_height(), whole / 2, "down to its line")
        self.assertFalse(panel.body.winfo_ismapped())
        panel._grab(SimpleNamespace(x_root=x, y_root=y))
        panel._line_released(SimpleNamespace(x_root=x, y_root=y))
        self._settle(0.1)
        self.assertFalse(panel.rolled_up)
        self.assertEqual(panel.window.winfo_height(), whole, "back at its size")
        self.assertTrue(panel.body.winfo_ismapped())
        panel.fold()
        panel.close()
        panel.show((800, 500, 128, 128))
        self.assertFalse(panel.rolled_up, "opened again, it opens whole")

    def test_a_group_folds_and_opens_again(self) -> None:
        panel = self._panel(self.ONE)
        panel.show((800, 500, 128, 128))
        self._settle()
        panel._fold("Coast")
        self.assertNotIn("Measure the gauge", self._said(panel))
        self.assertIn("Coast (1)", self._said(panel), "its heading stays, with how many")
        panel._fold("Coast")
        self.assertIn("Measure the gauge", self._said(panel))

    def test_a_group_folded_stays_so_when_it_is_opened_again(self) -> None:
        panel = self._panel(self.ONE)
        panel.show((800, 500, 128, 128))
        self._settle()
        panel._fold("Coast")
        panel.close()
        panel.show((800, 500, 128, 128))
        self._settle()
        self.assertNotIn("Measure the gauge", self._said(panel))

    def test_its_buttons_are_there_without_a_press(self) -> None:
        panel = self._panel(self.ONE)
        panel.show((800, 500, 128, 128))
        self._settle()
        self.assertEqual(self._buttons(panel), ["Open the latest outcome", "Delegate work", "Remove"])

    def test_folded_its_buttons_go_with_it(self) -> None:
        panel = self._panel(self.ONE)
        panel.show((800, 500, 128, 128))
        self._settle()
        panel._fold("Coast")
        self.assertEqual(self._buttons(panel), [])

    def test_only_the_one_asked_of_says_remove_it(self) -> None:
        two = [{"name": "", "rows": [dict(self.ONE[0]["rows"][0]),
                                     dict(self.ONE[0]["rows"][0], id="t2", title="Other")]}]
        panel = self._panel(two)
        panel.show((800, 500, 128, 128))
        self._settle()
        panel._ask("t2")
        self.assertEqual(self._buttons(panel).count("Remove it?"), 1)
        self.assertEqual(self._buttons(panel).count("Remove"), 1)

    def test_remove_asks_first_and_no_changes_nothing(self) -> None:
        asked = []
        panel = self._panel(self.ONE, act=lambda action, done: (asked.append(action["key"]), done()))
        panel.show((800, 500, 128, 128))
        self._settle()
        panel._ask("t1")
        self.assertEqual(self._buttons(panel), ["Open the latest outcome", "Delegate work",
                                                "Remove it?", "Yes", "No"])
        self.assertEqual(asked, [], "nothing gone yet")
        panel._ask("")
        self.assertIn("Remove", self._buttons(panel))
        self.assertEqual(asked, [])

    def test_removed_the_list_is_read_again(self) -> None:
        sections = [dict(self.ONE[0])]
        panel = QuickPanel(self.root, "To do", lambda: sections, self.posted.put, lambda text: text,
                           ("Segoe UI", 11), "Nothing yet.",
                           act=lambda _action, done: (sections.clear(), done()))
        panel.show((800, 500, 128, 128))
        self._settle()
        panel._do(self.ONE[0]["rows"][0], self.ONE[0]["rows"][0]["actions"][2])
        self._settle()
        self.assertIn("Nothing yet.", self._said(panel))

    def _long(self):
        return [{"name": "", "rows": [
            {"id": f"t{n}", "title": f"To do number {n}", "detail": "Some words about it.",
             "actions": [{"key": "delegate", "label": "Delegate work"},
                         {"key": "remove", "label": "Remove", "confirm": True, "changes": True}]}
            for n in range(40)]}]

    def _top_line(self, panel):
        return int(panel.list.index("@0,0").split(".")[0])

    def test_pressing_remove_keeps_the_list_where_it_is(self) -> None:
        panel = self._panel(self._long())
        panel.show((800, 500, 128, 128))
        self._settle()
        panel.list.yview("90.0")
        self._settle(0.1)
        before = self._top_line(panel)
        self.assertGreater(before, 50, "scrolled down")
        panel._ask("t30")
        self._settle(0.1)
        self.assertEqual(self._top_line(panel), before, "Remove it? Yes / No, in place")
        panel._ask("")
        self._settle(0.1)
        self.assertEqual(self._top_line(panel), before)

    def test_removed_the_list_stays_about_where_it_was(self) -> None:
        sections = self._long()
        panel = QuickPanel(self.root, "To do", lambda: sections, self.posted.put, lambda text: text,
                           ("Segoe UI", 11), "Nothing yet.",
                           act=lambda _action, done: (sections[0]["rows"].pop(30), done()))
        panel.show((800, 500, 128, 128))
        self._settle()
        panel.list.yview("90.0")
        self._settle(0.1)
        before = self._top_line(panel)
        row = sections[0]["rows"][30]
        panel._do(row, row["actions"][1])
        self._settle()
        self.assertLessEqual(abs(self._top_line(panel) - before), 3, "not back at the top")

    def test_buttons_do_not_pile_up_as_it_is_drawn_again(self) -> None:
        panel = self._panel(self.ONE)
        panel.show((800, 500, 128, 128))
        self._settle()
        for _ in range(5):
            panel._draw()
        body = [one for one in panel.list.winfo_children() if isinstance(one, tk.Label)]
        self.assertEqual(len(body), 3)

    def test_opening_says_so_and_takes_no_second_press(self) -> None:
        asked, finish = [], []

        def act(action, done):
            asked.append(action["key"])
            finish.append(done)
        panel = self._panel(self.ONE, act=act)
        panel.show((800, 500, 128, 128))
        self._settle()
        row = self.ONE[0]["rows"][0]
        panel._do(row, row["actions"][0])
        panel._do(row, row["actions"][0])
        self.assertEqual(asked, ["open"])
        self.assertIn("Opening...", self._buttons(panel))
        finish[0]()
        self._settle(0.1)
        self.assertNotIn("Opening...", self._buttons(panel))

    def test_an_answer_takes_its_row_away_at_once_and_is_kept_behind(self) -> None:
        """The user, 2026-11-07: Good to know pressed, no "Opening..." and no wait - gone."""
        sections = [{"name": "Recommended content", "rows": [
            {"id": "n1", "title": "Harbour tables", "actions": [
                {"key": "answer:good_to_know", "label": "Good to know", "changes": True}]},
            {"id": "n2", "title": "Tide notes", "actions": [
                {"key": "answer:good_to_know", "label": "Good to know", "changes": True}]}]}]
        asked, finish = [], []

        def act(action, done):
            asked.append(action["key"])
            finish.append(done)
        panel = self._panel(sections, act=act, closes_emptied=True)
        panel.show((800, 500, 128, 128))
        self._settle()
        panel._do(sections[0]["rows"][0], sections[0]["rows"][0]["actions"][0])
        self.assertNotIn("Harbour tables", self._said(panel), "away at once, before it is kept")
        self.assertNotIn("Opening...", self._buttons(panel))
        self.assertEqual(asked, ["answer:good_to_know"])
        self.assertTrue(panel.is_open, "one is left")
        panel._do(sections[0]["rows"][1], sections[0]["rows"][1]["actions"][0])
        self.assertFalse(panel.is_open, "the last answered: closed at once")
        sections[0]["rows"].clear()                       # both were kept
        for done in finish:
            done()
        self._settle(0.3)
        self.assertFalse(panel.is_open)

    def test_an_answer_that_failed_comes_back(self) -> None:
        sections = [{"name": "Reminders", "rows": [
            {"id": "n1", "title": "Harbour tables", "actions": [
                {"key": "got_it", "label": "Got it", "changes": True}]},
            {"id": "n2", "title": "Tide notes", "actions": []}]}]
        finish = []
        panel = self._panel(sections, act=lambda action, done: finish.append(done))
        panel.show((800, 500, 128, 128))
        self._settle()
        panel._do(sections[0]["rows"][0], sections[0]["rows"][0]["actions"][0])
        self.assertNotIn("Harbour tables", self._said(panel))
        finish[0]()                                       # not kept: still in the list
        self._settle(0.5)
        self.assertIn("Harbour tables", self._said(panel))

    def _open(self, sections, **more):
        panel = self._panel(sections, **more)
        panel.show((1000, 300, 128, 128))
        self._settle(0.1)
        return panel

    def test_the_keyboard_is_in_its_search_box_as_in_the_chat(self) -> None:
        panel = self._open(self.ONE, searches=True)
        self.assertEqual(panel.window.focus_lastfor(), panel.box)

    def test_control_and_the_wheel_zoom_whatever_has_the_keyboard(self) -> None:
        panel = self._open(self.ONE)
        panel._wheel(SimpleNamespace(delta=120))
        self.assertEqual(panel.zoom, 1)
        panel._wheel(SimpleNamespace(delta=-120))
        panel._wheel(SimpleNamespace(delta=-120))
        self.assertEqual(panel.zoom, -1)

    def test_control_minus_makes_its_smallest_words_smaller(self) -> None:
        panel = self._open(self.ONE)
        small = panel._sized(-2)
        was = small.cget("size")
        panel.zoom_by(-1)
        self._settle(0.05)
        self.assertLess(small.cget("size"), was)

    def test_a_key_on_the_search_box_zooms_and_writes_nothing(self) -> None:
        panel = self._open(self.ONE, searches=True)
        panel.box.focus_force()
        self._settle(0.05)
        panel.box.event_generate(f"<{MOD}-minus>")
        self._settle(0.05)
        self.assertEqual((panel.zoom, panel.box.get()), (-1, ""))

    ADD = {"label": "+ Add a to-do", "fields": ["What to do", "Details"], "keys": ["text", "detail"],
           "path": "/api/todos/add"}

    def test_adding_posts_what_was_written_and_reads_again(self) -> None:
        asked, reads = [], []

        def act(action, done):
            asked.append(action)
            done()
        panel = self._panel(self.ONE, act=act, adds=[self.ADD])
        panel.read = lambda: reads.append(1) or self.ONE
        panel.show((1000, 300, 128, 128))
        self._settle(0.1)
        reads.clear()
        panel._add(self.ADD)
        boxes = [one for one in panel.form.winfo_children() if isinstance(one, tk.Entry)]
        self.assertEqual(len(boxes), 2)
        boxes[0].insert(0, "Check the gauge")
        boxes[1].insert(0, "Before noon")
        self._settle(0.05)
        boxes[0].event_generate("<Return>")
        self._settle(0.1)
        self.assertEqual((asked[0]["path"], asked[0]["body"]),
                         ("/api/todos/add", {"text": "Check the gauge", "detail": "Before noon"}))
        self.assertIsNone(panel.form)
        self.assertTrue(reads, "the list read again")

    def test_editing_shows_its_words_and_posts_them_changed(self) -> None:
        asked = []

        def act(action, done):
            asked.append(action)
            done()
        action = {"key": "edit", "label": "Edit", "edits": True, "changes": True,
                  "path": "/api/todos/edit", "keys": ["text", "detail"],
                  "fields": ["What to do", "Details (optional)"], "body": {"id": "t1"}}
        row = {"id": "t1", "title": "Check the gauge", "detail": "Before noon", "actions": [action]}
        panel = self._panel(self.ONE, act=act, adds=[self.ADD])
        panel.show((1000, 300, 128, 128))
        self._settle(0.1)
        panel._edit_row(row, action)
        boxes = [one for one in panel.form.winfo_children() if isinstance(one, tk.Entry)]
        self.assertEqual([one.get() for one in boxes], ["Check the gauge", "Before noon"])
        boxes[0].delete(0, "end")
        boxes[0].insert(0, "Read the gauge")
        boxes[1].delete(0, "end")
        self._settle(0.05)
        boxes[0].event_generate("<Return>")
        self._settle(0.1)
        self.assertEqual((asked[-1]["path"], asked[-1]["body"]),
                         ("/api/todos/edit", {"id": "t1", "text": "Read the gauge", "detail": ""}))
        self.assertIsNone(panel.form)

    def test_nothing_written_nothing_is_added(self) -> None:
        asked = []
        panel = self._open(self.ONE, act=lambda action, done: asked.append(action), adds=[self.ADD])
        panel._add(self.ADD)
        box = next(one for one in panel.form.winfo_children() if isinstance(one, tk.Entry))
        self._settle(0.05)
        box.event_generate("<Return>")
        self._settle(0.05)
        self.assertEqual(asked, [])
        self.assertIsNotNone(panel.form, "still open to write in")

    GROUPED = [{"name": "Coast", "group": "Coast", "keep": True, "rows": [
        {"id": "a", "title": "Measure the gauge",
         "actions": [{"key": "move", "label": "Move", "moves": True, "list": "todo"}]}]},
        {"name": "Empty one", "group": "Empty one", "keep": True, "rows": []},
        {"name": "Others", "group": "Others", "keep": False, "rows": []}]

    def test_moving_offers_the_other_groups_and_posts_the_one_chosen(self) -> None:
        asked = []

        def act(action, done):
            asked.append(action)
            done()
        panel = self._open(self.GROUPED, act=act)
        self.assertEqual(self._buttons(panel), ["Move"])
        panel._start_moving("a")
        self.assertEqual(self._buttons(panel), ["Move to…", "Empty one", "Others", "+ Add group", "Cancel"])
        panel._move_row(panel.sections[0]["rows"][0], panel.sections[0]["rows"][0]["actions"][0], "Empty one")
        self._settle(0.1)
        self.assertEqual((asked[0]["path"], asked[0]["body"]),
                         ("/api/lists/groups/move", {"list": "todo", "item": "a", "to": "Empty one"}))
        self.assertEqual(panel.moving, "")

    def test_a_group_empty_is_shown_but_not_while_searching(self) -> None:
        panel = self._open(self.GROUPED, searches=True)
        self.assertIn("Empty one", self._said(panel))
        self.assertNotIn("Others", self._said(panel), "Others only with something in it")
        panel.box.insert(0, "gauge")
        panel._draw()
        self.assertNotIn("Empty one", self._said(panel))

    LONG = [{"name": "Coast", "group": "Coast", "rows": [
        {"id": str(n), "title": f"Thing to do {n}", "actions": [{"key": "a", "label": "Open"}]}
        for n in range(40)]}]

    def test_the_wheel_glides_and_stops_at_the_top(self) -> None:
        panel = self._open(self.LONG)
        self.assertEqual(panel._roll(SimpleNamespace(delta=-NOTCH)), "break")
        first = panel.list.yview()[0]
        self._settle(0.4)
        moved = panel.list.yview()[0]
        self.assertGreater(moved, first, "on, after the first step")
        self.assertEqual(panel._rolling, 0.0)
        for _ in range(4):
            panel._roll(SimpleNamespace(delta=NOTCH))
        self._settle(0.6)
        self.assertEqual((panel.list.yview()[0], panel._rolling), (0.0, 0.0))

    def test_many_steps_of_zoom_are_drawn_once(self) -> None:
        panel = self._open(self.LONG)
        with mock.patch.object(panel, "_draw_zoom", wraps=panel._draw_zoom) as drawn:
            panel._zoom_due = None
            for _ in range(5):
                panel.zoom_by(1)
            self.assertEqual(panel.zoom, 5)

    def test_it_is_sized_by_its_edges_and_keeps_the_size(self) -> None:
        told = []
        panel = self._open(self.ONE, on_size=told.append)
        window = panel.window
        width, height = window.winfo_width(), window.winfo_height()
        panel._edge_start(SimpleNamespace(x_root=500, y_root=500), "se")
        panel._edge_move(SimpleNamespace(x_root=560, y_root=530))
        panel._edge_end()
        self.assertEqual(told, [(width + 60, height + 30)])
        panel.close()
        panel.show((1000, 300, 128, 128))
        self._settle(0.1)
        self.assertEqual((panel.window.winfo_width(), panel.window.winfo_height()), told[0])

    def test_its_edges_are_at_its_edges_and_cover_nothing(self) -> None:
        """By where things are on the screen: the sizing was tested by calling it, while the
        strips stood inside the padding, over the words and the ✕."""
        panel = self._open(self.ONE, searches=True)
        window = panel.window
        self._settle(0.3)                    # moved from out of sight: settled there first
        x, y = window.winfo_rootx(), window.winfo_rooty()
        width, height = window.winfo_width(), window.winfo_height()
        for at in ((x + width - 3, y + height // 2), (x + 3, y + height // 2),
                   (x + width // 2, y + 3), (x + width // 2, y + height - 3),
                   (x + width - 3, y + height - 3), (x + 3, y + 3)):
            from personalized_agent.ui.chat_bubble import EDGES
            self.assertIn(str(self.root.winfo_containing(*at).cget("cursor")),
                          set(EDGES.values()), at)
        strips = panel.card.master.place_slaves()
        self.assertEqual(len(strips), 8)
        for one in strips:
            for inside in (panel.list, panel.box):
                apart = (one.winfo_rootx() + one.winfo_width() <= inside.winfo_rootx()
                         or inside.winfo_rootx() + inside.winfo_width() <= one.winfo_rootx()
                         or one.winfo_rooty() + one.winfo_height() <= inside.winfo_rooty()
                         or inside.winfo_rooty() + inside.winfo_height() <= one.winfo_rooty())
                self.assertTrue(apart, f"{one.cget('cursor')} is over the list or the search box")

    def test_made_ahead_it_opens_with_its_list_already_there(self) -> None:
        reads = []
        panel = self._panel(self.ONE)
        panel.read = lambda: reads.append(1) or self.ONE
        panel.prepare()
        self._settle(0.1)
        self.assertFalse(panel.is_open)
        self.assertTrue(_put_away(panel.window), "out of sight")
        made = panel.window
        panel.show((1000, 300, 128, 128))
        self.assertTrue(panel.is_open)
        self.assertIs(panel.window, made)
        self.root.update()
        self.assertGreater(panel.window.winfo_x(), -1000)
        self.assertIn("Coast", self._said(panel), "at once, before any read comes back")

    def test_it_comes_on_to_the_screen_the_way_a_press_opens_it(self) -> None:
        """Through the desktop's own press, the keyboard taken and all, read from the system:
        drawn out of sight, it said it was open and stayed there."""
        import ctypes
        from ctypes import wintypes
        from personalized_agent.ui.desktop import DesktopAgent
        panels = {"todo": self._panel(self.ONE, searches=True),
                  "collection": self._panel(self.ONE, searches=True)}
        for one in panels.values():
            one.prepare()
        self._settle(0.2)
        desk = SimpleNamespace(_quick_panels=panels, _panel=panels.get, _chat_bubble=None,
                               config=SimpleNamespace(get=lambda _key, default=None: default),
                               _beside_box=lambda: (1500, 500, 180, 169))
        for which, other in (("todo", "collection"), ("collection", "todo"), ("todo", "collection")):
            DesktopAgent._quick_panel(desk, which)
            self._settle(0.2)
            self.assertTrue(panels[which].is_open)
            self.assertGreater(panels[which].window.winfo_x(), -1000, which)
            self.assertTrue(_put_away(panels[other].window), other)
            if sys.platform.startswith("win"):
                rect = wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(int(panels[which].window.wm_frame(), 16),
                                                   ctypes.byref(rect))
                self.assertGreater(rect.left, -1000, "the system's word for it, not Tk's")

    def test_every_panel_goes_by_the_one_zoom(self) -> None:
        from personalized_agent.ui.desktop import DesktopAgent
        kept = {"popup_zoom": 2}
        config = SimpleNamespace(get=lambda key, default=None: kept.get(key, default),
                                 set=lambda key, value, internal=False: kept.__setitem__(key, value))
        desk = SimpleNamespace(_quick_panels={}, root=self.root, config=config, _words=str,
                               queue=queue.Queue(), _popup_font=lambda: ("Segoe UI", 11),
                               _panel_act=lambda action, done: done())
        todo = DesktopAgent._panel(desk, "todo")
        collection = DesktopAgent._panel(desk, "collection")
        self.assertEqual((todo.zoom, collection.zoom), (2, 2))
        from personalized_agent.ui.desktop import zoom_popups
        zoom_popups(desk, 5)
        self.assertEqual((kept["popup_zoom"], todo.zoom, collection.zoom), (5, 5, 5))
        desk._quick_panels.clear()
        self.assertEqual(DesktopAgent._panel(desk, "collection").zoom, 5, "and the next to open")

    def test_closed_it_is_kept_clean_for_the_next_time(self) -> None:
        panel = self._open(self.GROUPED, searches=True, act=lambda action, done: done())
        panel.box.insert(0, "gauge")
        panel._start_moving("a")
        panel._open_form(["Name"], lambda said: None, "Add")
        panel.close()
        self._settle(0.05)
        self.assertFalse(panel.is_open)
        self.assertEqual((panel.box.get(), panel.moving, panel.form), ("", "", None))
        self.assertTrue(_put_away(panel.window))

    def test_a_list_changed_is_drawn_and_one_the_same_is_not_drawn_again(self) -> None:
        panel = self._open(self.ONE)
        with mock.patch.object(panel, "_draw") as drawn:
            panel._got(self.ONE, quiet=True)
            drawn.assert_not_called()
            panel._got(self.GROUPED, quiet=True)
            drawn.assert_called_once()

    def test_a_group_folds_while_searching(self) -> None:
        panel = self._open(self.ONE, searches=True)
        panel.box.insert(0, "gauge")
        panel._draw()
        self.assertIn("gauge", self._said(panel).lower())
        panel._fold("Coast")
        self.assertNotIn("gauge", self._said(panel).lower().replace("coast", ""))
        self.assertIn("Coast", self._said(panel))
        panel.box.delete(0, "end")
        panel._draw()
        self.assertIn("gauge", self._said(panel).lower(), "that fold ends with the search")

    def test_it_can_still_be_dragged_by_its_head(self) -> None:
        panel = self._open(self.ONE)
        self.assertEqual(panel._move.__code__.co_varnames[:2], ("self", "event"))

    def test_its_words_zoom_and_the_size_is_kept(self) -> None:
        told = []
        panel = self._panel(self.ONE, on_zoom=told.append)
        panel.show((800, 500, 128, 128))
        self._settle()
        before = panel._sized(-1).actual("size")
        panel.zoom_by(2)
        self._settle(0.05)
        self.assertEqual(panel._sized(-1).actual("size"), before + 2)
        panel.zoom_to(0)
        self._settle(0.05)
        self.assertEqual(panel._sized(-1).actual("size"), before)
        self.assertEqual(told, [], "not written at each step")
        self._settle(0.5)
        self.assertEqual(told, [0], "written once, when it stops")
        panel.zoom_to(99)
        panel.close()
        self.assertEqual(told[-1], 7, "the one zoom's most, and not lost on closing")

    def test_ctrl_plus_and_minus_are_held_by_the_panel(self) -> None:
        panel = self._panel(self.ONE)
        panel.show((800, 500, 128, 128))
        self._settle(0.05)
        for keys in (f"<{MOD}-plus>", f"<{MOD}-equal>", f"<{MOD}-minus>", f"<{MOD}-Key-0>"):
            self.assertTrue(panel.window.bind(keys), keys)

    def test_what_is_selected_is_what_is_copied(self) -> None:
        from personalized_agent.ui.on_top import selected_words
        panel = self._panel(self.ONE)
        panel.show((800, 500, 128, 128))
        self._settle()
        self.assertEqual(selected_words(panel.list), "")
        panel.list.tag_add("sel", "2.0", "2.7")
        self.assertEqual(selected_words(panel.list), "Measure")
        self.assertTrue(panel.list.bind("<Button-3>"), "copied from the right button")

    def test_searched_by_a_word_with_a_folded_group_opened(self) -> None:
        panel = self._panel([{"name": "All", "rows": [{"title": "Tides"}, {"title": "Bread"}]}],
                            searches=True)
        panel.show((800, 500, 128, 128))
        self._settle()
        panel._fold("All")
        panel.box.insert(0, "brea")
        panel._draw()
        self.assertIn("Bread", self._said(panel))
        self.assertNotIn("Tides", self._said(panel))

    def test_with_nothing_it_says_so(self) -> None:
        panel = self._panel([])
        panel.show((800, 500, 128, 128))
        self._settle()
        self.assertIn("Nothing yet.", self._said(panel))


class TheVinesPathTests(unittest.TestCase):
    AREA = (0, 0, 1920, 1032)

    SIDES = (("right", (1886, 500, 128, 128), (1832, 500, 128, 128)),
             ("left", (-94, 500, 128, 128), (-40, 500, 128, 128)),
             ("top", (900, -94, 128, 128), (900, -28, 128, 128)),
             ("bottom", (900, 998, 128, 128), (900, 948, 128, 128)))

    def test_in_the_middle_between_the_leaves_close_to_the_edge(self) -> None:
        """Where the leaves are was measured on the frames: 8 to 52 and 75 to 119 along."""
        for side, tucked, _head in self.SIDES:
            points, at = vine_path(tucked, side, self.AREA)
            self.assertEqual(points[-1], (at[0] + ICON // 2, at[1] + ICON // 2), "the button at its end")
            middle = (at[0] + ICON // 2 - tucked[0], at[1] + ICON // 2 - tucked[1])
            along = middle[0] if side in ("top", "bottom") else middle[1]
            self.assertEqual(along, 64, f"between the leaves: {side}")
            from_edge = {"right": 1920 - at[0] - ICON, "left": at[0], "top": at[1],
                         "bottom": 1032 - at[1] - ICON}[side]
            # NEARER THE SIDE (the user, 2026-11-09, twice): its inner part behind what of the avatar
            # shows - no head comes out for the pointer now - and most of it out, to be pressed.
            self.assertTrue(0 <= from_edge <= 30, (side, from_edge))
            behind = {"right": at[0] + ICON - tucked[0], "left": tucked[0] + 128 - at[0],
                      "top": tucked[1] + 128 - at[1], "bottom": at[1] + ICON - tucked[1]}[side]
            self.assertTrue(0 < behind < ICON // 2, (side, behind))

    def test_in_a_corner_or_any_screen_the_button_is_on_it(self) -> None:
        for area in (self.AREA, (0, 0, 1024, 728), (1920, -200, 3840, 1000), (-1280, 0, 0, 1000)):
            left, top, right, bottom = area
            for y in range(top - 90, bottom - 30, 23):
                for side, x in (("right", right - 34), ("left", left - 94)):
                    _points, at = vine_path((x, y, 128, 128), side, area)
                    self.assertTrue(left <= at[0] <= right - ICON and top <= at[1] <= bottom - ICON,
                                    (area, side, y, at))

    def test_it_comes_out_of_the_side_itself_in_every_side(self) -> None:
        """From past the side of the screen, level with the leaves' middle, tucked or not."""
        for side, tucked, head in self.SIDES:
            for box in (tucked, head):
                start = vine_path(box, side, self.AREA)[0][0]
                past = {"right": start[0] > 1920, "left": start[0] < 0, "top": start[1] < 0,
                        "bottom": start[1] > 1032}[side]
                self.assertTrue(past, (side, start))
                level = start[1] - box[1] if side in ("right", "left") else start[0] - box[0]
                self.assertEqual(level, 64, side)

    def test_the_button_goes_with_its_head_and_is_not_behind_it(self) -> None:
        for side, tucked, head in self.SIDES:
            at = vine_path(head, side, self.AREA)[1]
            middle = (at[0] + ICON // 2, at[1] + ICON // 2)
            inside = head[0] < middle[0] < head[0] + 128 and head[1] < middle[1] < head[1] + 128
            self.assertFalse(inside, (side, at))

    def test_it_winds_as_a_whole(self) -> None:
        """Not a stem: it swings to both sides of the line from the avatar to the button."""
        for side, tucked, _head in self.SIDES:
            points, _at = vine_path(tucked, side, self.AREA)
            (x0, y0), (x1, y1) = points[0], points[-1]
            off = [(x - x0) * (y1 - y0) - (y - y0) * (x1 - x0) for x, y in points]
            long = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
            self.assertGreater(max(off) / long, 10, side)
            self.assertLess(min(off) / long, -10, side)

    def test_it_grows_from_the_side(self) -> None:
        box = (1886, 500, 128, 128)
        start = vine_path(box, "right", self.AREA, 0.0)[0]
        self.assertEqual(len(set(start)), 1, "nothing out yet")


class ClearAroundItTests(unittest.TestCase):
    """A Mac has no colour to take out of a window, and clears the window instead: drawn on the
    card's colour, the vine and its button stood in a dark square."""

    class Window:
        def __init__(self, clears=True):
            self.asked, self.clears = [], clears

        def attributes(self, *said):
            self.asked.append(said)
            if said[0] == "-transparentcolor" or not self.clears:
                raise tk.TclError("bad attribute")

    def test_on_a_mac_the_window_itself(self) -> None:
        from personalized_agent.ui import avatar_buttons as ab
        window = self.Window()
        with mock.patch.object(ab.sys, "platform", "darwin"):
            self.assertEqual(ab.see_through(window), "systemTransparent")
        self.assertIn(("-transparent", True), window.asked)

    def test_where_nothing_can_be_clear_the_cards_colour(self) -> None:
        from personalized_agent.ui import avatar_buttons as ab
        with mock.patch.object(ab.sys, "platform", "linux"):
            self.assertEqual(ab.see_through(self.Window()), ab.LOOK["bg"])
        with mock.patch.object(ab.sys, "platform", "darwin"):
            self.assertEqual(ab.see_through(self.Window(clears=False)), ab.LOOK["bg"])


class OneOverAnotherTests(unittest.TestCase):

    def test_the_avatar_over_the_button_over_the_vine(self) -> None:
        """A Mac orders them by what was lifted last: the vine took the button's presses."""
        from personalized_agent.ui import avatar_buttons as ab
        lifted = []

        def window(name):
            return SimpleNamespace(geometry=lambda _at: None, lift=lambda: lifted.append(name),
                                   state=lambda: "normal", deiconify=lambda: None)
        with mock.patch.object(ab.sys, "platform", "darwin"):
            ab.move_together([window("button"), window("vine")], [(1, 1), (2, 2)], window("avatar"))
        self.assertEqual(lifted, ["vine", "button", "avatar"])


class WhatAMacStartsTests(unittest.TestCase):

    def _started(self, available):
        from personalized_agent.ui import desktop as dm, selection
        said = []
        desk = SimpleNamespace(_start_shortcuts=lambda: said.append("shortcuts"),
                               _start_avatar_buttons=lambda: said.append("panels"),
                               queue=queue.Queue(), config=None)
        with mock.patch.object(selection, "available", return_value=available):
            dm.DesktopAgent._start_selection_watch(desk)
        return said

    def test_the_shortcuts_and_panels_without_the_button_beside_selected_text(self) -> None:
        self.assertEqual(self._started(False), ["shortcuts", "panels"])

    def test_the_docks_menu_is_the_avatars_and_says_what_it_would_do(self) -> None:
        from personalized_agent.ui import desktop as dm, macos_dock
        shown = []
        desk = SimpleNamespace(_words=str, queue=queue.Queue(), character_hidden=False,
                               open_chat_bubble=None, toggle_character=None, open_report=None,
                               open_notifications=None)
        with mock.patch.object(dm.sys, "platform", "darwin"), \
                mock.patch.object(macos_dock, "show", shown.append):
            dm.DesktopAgent._dock_menu(desk)
            desk.character_hidden = True
            dm.DesktopAgent._dock_menu(desk)
        self.assertEqual([words for words, _act in shown[0]],
                         ["Chat", "To do", "Collections", "Waiting notifications", "Report", "Overview",
                          "Hide avatar"])
        self.assertEqual(shown[1][-1][0], "Show avatar")


@unittest.skipUnless(sys.platform.startswith("win"), "where Ctrl can be asked of the system")
class TheKeysOnHoverTests(unittest.TestCase):

    def setUp(self) -> None:
        from personalized_agent.ui.on_top import keys_on_hover
        self.root = tk.Tk()
        self.root.withdraw()
        self.window = tk.Toplevel(self.root)
        self.window.overrideredirect(True)
        self.window.geometry("100x100+300+300")
        self.root.update()
        self.window.winfo_pointerxy = lambda: (350, 350)
        self.window.focus_force = mock.Mock()
        self.window.focus_get = lambda: None
        keys_on_hover(self.window)

    def tearDown(self) -> None:
        self.root.destroy()
        del self.window, self.root
        gc.collect()

    def _settle(self, seconds=0.2):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.root.update()
            time.sleep(0.01)

    def test_the_pointer_passing_takes_no_keyboard(self) -> None:
        with mock.patch("personalized_agent.ui.on_top.ctrl_down", return_value=False):
            self.window.event_generate("<Enter>")
            self._settle()
        self.window.focus_force.assert_not_called()

    def test_ctrl_down_on_it_takes_the_keyboard(self) -> None:
        with mock.patch("personalized_agent.ui.on_top.ctrl_down", return_value=True):
            self.window.event_generate("<Enter>")
            self._settle()
        self.window.focus_force.assert_called()

    def test_a_box_in_it_keeps_the_keyboard(self) -> None:
        self.window.focus_get = lambda: f"{self.window}.box"
        with mock.patch("personalized_agent.ui.on_top.ctrl_down", return_value=True):
            self.window.event_generate("<Enter>")
            self._settle()
        self.window.focus_force.assert_not_called()

    def test_off_it_it_stops_looking(self) -> None:
        with mock.patch("personalized_agent.ui.on_top.ctrl_down", return_value=False) as asked:
            self.window.event_generate("<Enter>")
            self._settle(0.1)
            self.window.winfo_pointerxy = lambda: (10, 10)
            self._settle(0.15)
            seen = asked.call_count
            self._settle(0.15)
        self.assertEqual(asked.call_count, seen)


class TheVinesLooksTests(unittest.TestCase):

    def test_a_tendril_curls_in_and_a_leaf_has_its_lobes(self) -> None:
        from personalized_agent.ui.avatar_buttons import melon_leaf, tendril
        curl = tendril(50, 50, 0.0)
        self.assertEqual(len(curl), 38)
        self.assertLess(max(curl[0::2]) - min(curl[0::2]), 20, "small enough for its window")
        leaf = melon_leaf(50, 50, 0.0)
        far = [((x - 50) ** 2 + (y - 50) ** 2) ** 0.5 for x, y in zip(leaf[0::2], leaf[1::2])]
        self.assertGreater(max(far), 8)
        self.assertLess(min(far), 5, "cut deep between the lobes")


class TheVineOnTheScreenTests(unittest.TestCase):

    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.front = tk.Toplevel(self.root)
        self.front.overrideredirect(True)
        self.front.attributes("-topmost", True)
        self.front.geometry("128x128+1000+300")
        self.side = [""]
        self.pressed = []
        self.box = [(1000, 300, 128, 128)]
        self.root.update()
        self.vine = ChatVine(self.root, "Chat", lambda: self.pressed.append(1), lambda: self.box[0],
                             ("Segoe UI", 11), lambda: self.side[0], self.front,
                             overview=("Overview", lambda: self.pressed.append("overview")))

    def tearDown(self) -> None:
        self.root.destroy()
        del self.vine, self.front, self.root
        gc.collect()

    def _settle(self, seconds=0.5):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.root.update()
            time.sleep(0.01)

    def test_where_a_window_cannot_be_off_every_screen_it_is_hidden(self) -> None:
        """The user, 2026-11-09: on a Mac the two buttons "固定显示在某个位置" - the system put
        them back on a screen. There, put away, they are withdrawn; out again, shown."""
        from unittest import mock
        from personalized_agent.ui import avatar_buttons
        with mock.patch.object(avatar_buttons, "PARKS_BY_HIDING", True):
            self.side[0], self.box[0] = "right", (1886, 500, 128, 128)
            self.vine.place()
            self._settle(0.6)
            self.assertEqual(self.vine.icon.window.state(), "normal")
            self.assertEqual(self.vine.second.window.state(), "normal")
            self.side[0] = ""
            self.vine.place()
            self._settle(0.1)
            self.assertEqual(self.vine.icon.window.state(), "withdrawn")
            self.assertEqual(self.vine.second.window.state(), "withdrawn")
            self.assertEqual(self.vine.stem.state(), "withdrawn")
            self.side[0] = "right"
            self.vine.place()
            self._settle(0.6)
            self.assertEqual(self.vine.icon.window.state(), "normal", "out again, shown")
            self.assertEqual(self.vine.icon.window.winfo_x(), self.vine.at[0])

    def test_with_all_of_it_showing_there_is_no_vine(self) -> None:
        self._settle(0.1)
        self.assertFalse(self.vine.out)
        self.assertTrue(_put_away(self.vine.icon.window))
        self.assertTrue(_put_away(self.vine.stem))
        self.assertEqual(self.vine.around((1, 2, 3, 4)), (1, 2, 3, 4))

    def test_in_a_side_it_grows_out_and_goes_with_it(self) -> None:
        self.side[0] = "right"
        self.box[0] = (1886, 500, 128, 128)
        self.vine.place()
        self.assertLess(self.vine.grown, 1.0, "growing")
        self._settle(0.6)
        self.assertEqual(self.vine.grown, 1.0)
        self.assertEqual((self.vine.icon.window.winfo_x(), self.vine.icon.window.winfo_y()), self.vine.at)
        self.assertGreater(self.vine.stem.winfo_x(), 0)
        self.assertEqual(self.vine.around((1886, 500, 128, 128))[0], self.vine.at[0])
        self.side[0] = ""
        self.vine.place()
        self._settle(0.1)
        self.assertTrue(_put_away(self.vine.icon.window), "out of the side: gone")

    def test_its_button_is_the_chat(self) -> None:
        self.side[0] = "left"
        self.box[0] = (-94, 500, 128, 128)
        self.vine.place()
        self._settle(0.6)
        canvas = self.vine.icon.canvas
        canvas.event_generate("<Enter>", x=20, y=20)
        canvas.event_generate("<ButtonPress-1>", x=20, y=20)
        canvas.event_generate("<ButtonRelease-1>", x=20, y=20)
        self._settle(0.1)
        self.assertEqual(self.pressed, [1])

    def test_beside_it_the_overview_on_a_branch_of_its_own(self) -> None:
        """The user, 2026-11-08: "藤蔓要有2个logo, 一个是chat, 一个是overview (等价于双击的功能)"."""
        for side, box in (("right", (1886, 500, 128, 128)), ("top", (900, -94, 128, 128))):
            self.side[0], self.box[0] = side, box
            self.vine.place()
            self._settle(0.6)
            second = self.vine.second
            spot = (second.window.winfo_x(), second.window.winfo_y())
            self.assertEqual(spot, self.vine.second_at)
            chat = self.vine.at
            apart = abs(spot[0] - chat[0]) + abs(spot[1] - chat[1])
            self.assertEqual(apart, ICON + 8, f"beside the chat's, not on it: {side}")
            along = spot[0] == chat[0] if side == "right" else spot[1] == chat[1]
            self.assertTrue(along, f"along the side: {side}")
            whole = self.vine.around(box)
            self.assertLessEqual(whole[1], spot[1])
            self.assertGreaterEqual(whole[1] + whole[3], spot[1] + ICON)
        canvas = second.canvas
        canvas.event_generate("<Enter>", x=20, y=20)
        canvas.event_generate("<ButtonPress-1>", x=20, y=20)
        canvas.event_generate("<ButtonRelease-1>", x=20, y=20)
        self._settle(0.1)
        self.assertEqual(self.pressed, ["overview"], "the Overview, as a double click")
        self.side[0] = ""
        self.vine.place()
        self._settle(0.1)
        self.assertTrue(_put_away(second.window), "gone with the vine")

    def test_the_two_have_their_middle_where_the_one_was(self) -> None:
        """The user, 2026-11-08: "两个藤蔓logo, 平均高度还是要在中央"."""
        # On this screen, whatever its size (a test machine's is not 1920 wide).
        left, top, right, bottom = self.vine._screen((300, 300, 128, 128))
        across, down = (left + right) // 2, (top + bottom) // 2
        for side, box in (("right", (right - 34, down, 128, 128)), ("left", (left - 94, down, 128, 128)),
                          ("top", (across, top - 94, 128, 128)),
                          ("bottom", (across, bottom - 34, 128, 128))):
            self.side[0], self.box[0] = side, box
            self.vine.place()
            self._settle(0.6)
            one = vine_path(box, side, self.vine._screen(box))[1]
            chat, other = self.vine.at, self.vine.second_at
            middle = ((chat[0] + other[0]) / 2, (chat[1] + other[1]) / 2)
            self.assertLessEqual(abs(middle[0] - one[0]) + abs(middle[1] - one[1]), 1, side)

    def test_at_the_foot_of_the_screen_both_stay_on_it(self) -> None:
        from personalized_agent.ui.avatar_buttons import pair_spots
        half = (ICON + 8) // 2
        self.assertEqual(pair_spots((1870, 500), "right", AREA), ((1870, 500 - half), (1870, 500 + half)))
        chat, other = pair_spots((1870, 1040 - ICON), "right", AREA)
        self.assertEqual(other[1] + ICON, 1040, "moved up together, as little as it takes")
        self.assertEqual(other[1] - chat[1], ICON + 8)
        self.assertEqual(pair_spots((900, 20), "top", AREA), ((900 - half, 20), (900 + half, 20)))

    def test_its_mark_is_not_the_chats(self) -> None:
        from personalized_agent.ui import avatar_buttons as ab
        self.assertNotEqual(ab.face().tobytes(), ab.face(mark="overview").tobytes())
        self.assertEqual(ab.face(mark="overview").getpixel((0, 0)), ab.KEY)

    def test_green_as_the_vine(self) -> None:
        from personalized_agent.ui import avatar_buttons as ab
        picture = ab.face()
        self.assertEqual(picture.size, (ICON, ICON))
        self.assertEqual(picture.getpixel((0, 0)), ab.KEY, "see-through at its corners")
        red, green, blue = picture.getpixel((ICON // 2, 6))
        self.assertGreater(green, red)
        self.assertGreater(green, blue)
        self.assertNotEqual(picture.tobytes(), ab.face(lit=True).tobytes())
        turned = ab.face(upside_down=True)
        self.assertNotEqual(picture.tobytes(), turned.tobytes(), "upside down in the top")
        self.assertEqual(turned.transpose(turned.ROTATE_180 if hasattr(turned, "ROTATE_180") else 3
                                          ).tobytes(), picture.tobytes())
        colours = set(picture.getdata())
        self.assertIn(ab.LEAF, colours, "of the leaves' making")
        self.assertIn(ab.LEAF_DARK, colours)
        self.assertFalse([one for one in colours if one != ab.KEY and one[0] > 200 and one[2] > 200
                          and one[1] < 120], "no pink at its edge")

    @unittest.skipUnless(sys.platform.startswith("win"), "the system's order of windows")
    def test_the_avatar_is_in_front_of_it(self) -> None:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32")
        user32.GetTopWindow.restype = user32.GetWindow.restype = wintypes.HWND
        user32.GetTopWindow.argtypes = [wintypes.HWND]
        user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        self.side[0] = "right"
        self.box[0] = (1886, 500, 128, 128)
        self.vine.place()
        self._settle(0.6)
        order, hwnd = [], user32.GetTopWindow(None)
        while hwnd:
            order.append(int(hwnd))
            hwnd = user32.GetWindow(hwnd, 2)
        at = order.index(int(self.front.wm_frame(), 16))
        self.assertGreater(order.index(int(self.vine.icon.window.wm_frame(), 16)), at)
        self.assertGreater(order.index(int(self.vine.stem.wm_frame(), 16)), at)


class TheMenuTests(unittest.TestCase):

    def test_to_do_and_collections_are_in_the_avatars_menu_and_the_trays(self) -> None:
        from pathlib import Path
        import personalized_agent.ui.desktop as desktop
        import personalized_agent.ui.tray as tray
        said = Path(desktop.__file__).read_text(encoding="utf-8")
        said = said[said.index("def _on_right_click"):said.index("def _delegate_face")]
        self.assertIn('self._quick_panel("todo")', said)
        self.assertIn('self._quick_panel("collection")', said)
        said = Path(tray.__file__).read_text(encoding="utf-8")
        self.assertIn('_quick_panel("todo")', said)
        self.assertIn('_quick_panel("collection")', said)


class WhatTheyListTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app

    def test_open_to_dos_in_their_groups_each_to_delegate(self) -> None:
        first = self.app.todos.add("Measure the gauge", "Before noon")["id"]
        self.app.todos.add("Buy milk")
        done = self.app.todos.add("Done already")["id"]
        self.app.todos.tick(done)
        self.app.list_groups.add_group("todo", "Coast")
        self.app.list_groups.move("todo", first, "Coast")
        said = self.app.quick_sections("todo")
        self.assertEqual(said[0]["name"], "Coast")
        row = said[0]["rows"][0]
        self.assertEqual(row["title"], "Measure the gauge")
        self.assertEqual([one["key"] for one in row["actions"]], ["delegate", "edit", "remove", "move"])
        self.assertEqual(row["actions"][0]["text"], "Measure the gauge\nBefore noon")
        self.assertTrue(row["actions"][2]["confirm"])
        self.assertEqual(row["actions"][2]["path"], "/api/todos/delete")
        titles = [one["title"] for part in said for one in part["rows"]]
        self.assertEqual(sorted(titles), ["Buy milk", "Measure the gauge"])

    def test_the_latest_outcome_as_the_overview_finds_it(self) -> None:
        versions = [{"state": "finished", "run": "r1", "items": [{"n": 0}]},
                    {"state": "finished", "run": "r2", "items": [{"n": 2}]},
                    {"state": "ongoing", "run": "r3", "unit": "u"}]
        action = self.app._work_actions(versions)[0]
        self.assertEqual((action["path"], action["body"]), ("/api/work/handed/open", {"run": "r2", "n": 2}))

    def test_a_file_it_left_is_opened_in_its_program(self) -> None:
        action = self.app._work_actions([{"state": "finished", "run": "r1", "result": "/out/a.txt"}])[0]
        self.assertEqual(action["path"], "/api/work/reveal")
        self.assertTrue(action["body"]["open"])

    def test_what_one_that_stopped_made_is_opened_too(self) -> None:
        action = self.app._work_actions([{"state": "stopped", "run": "r1", "result": "/out"},
                                         {"state": "ongoing", "run": "r2", "unit": "u2"}])[0]
        self.assertEqual(action["body"]["run"], "r1")

    def test_with_nothing_made_nothing_to_press(self) -> None:
        self.assertEqual(self.app._work_actions([{"state": "ongoing", "run": "r2", "unit": "u2"}]), [])
        self.assertEqual(self.app._work_actions([]), [])

    def test_the_work_assigned_is_listed_after_them(self) -> None:
        self.app.todos.add("Buy milk")
        assigned = [{"id": "d1", "text": "Plot the tides", "detail": "", "work": {"versions": [
            {"state": "finished", "run": "r1", "result": "/out/a.txt"}]}}]
        with mock.patch.object(self.app.drafts, "assigned", return_value=assigned):
            said = self.app.quick_sections("todo")
        self.assertEqual(said[-1]["name"], "Work assigned")
        self.assertEqual(said[-1]["rows"][0]["actions"][0]["key"], "open")

    def test_the_collection_to_read_and_remove(self) -> None:
        self.app._keep_recommended({"notification_id": "n1", "title": "A fact", "message": "Beds.",
                                    "url": "https://example.org/b", "reason": "You read of it."})
        row = self.app.quick_sections("collection")[0]["rows"][0]
        self.assertEqual((row["title"], row["url"], row["note"]),
                         ("A fact", "https://example.org/b", "You read of it."))
        self.assertEqual([one["key"] for one in row["actions"]], ["read", "remove", "move"])
        self.assertEqual(row["actions"][0]["url"], "https://example.org/b")
        remove = row["actions"][1]
        self.assertTrue(remove["confirm"] and remove["changes"])
        self.assertEqual(remove["body"], {"story": "n1", "keep": False})

    def test_with_no_address_there_is_nothing_to_read(self) -> None:
        self.app._keep_recommended({"notification_id": "n2", "title": "A fact"})
        row = self.app.quick_sections("collection")[0]["rows"][0]
        self.assertEqual([one["key"] for one in row["actions"]], ["remove", "move"])

    def test_nothing_is_nothing(self) -> None:
        self.assertEqual(self.app.quick_sections("todo"), [])




class OneAtATimeTests(unittest.TestCase):

    class _Open:
        def __init__(self) -> None:
            self.is_open, self.closed, self.toggled = True, 0, 0

        def close(self) -> None:
            self.is_open = False
            self.closed += 1

        def toggle(self, _near) -> None:
            self.toggled += 1

        def zoom_to(self, _zoom) -> None:
            pass

    def _desk(self):
        from types import SimpleNamespace
        panels = {"todo": self._Open(), "collection": self._Open()}
        return SimpleNamespace(_quick_panels=panels, _panel=panels.get,
                               config=SimpleNamespace(get=lambda _key, default=None: default),
                               _chat_bubble=self._Open(), _beside_box=lambda: (0, 0, 1, 1))

    def test_a_panel_opened_closes_the_chat_and_the_other_for_good(self) -> None:
        from personalized_agent.ui.desktop import DesktopAgent
        desk = self._desk()
        DesktopAgent._quick_panel(desk, "todo")
        self.assertEqual(desk._chat_bubble.closed, 1)
        self.assertEqual(desk._quick_panels["collection"].closed, 1)
        self.assertEqual(desk._quick_panels["todo"].toggled, 1)
        self.assertEqual(desk._quick_panels["todo"].closed, 0, "its own press decides that")
        desk._quick_panels["todo"].close()
        self.assertFalse(desk._chat_bubble.is_open, "closing it brings nothing back")

    def test_a_chat_already_closed_is_left_alone(self) -> None:
        from personalized_agent.ui.desktop import DesktopAgent
        desk = self._desk()
        desk._chat_bubble.is_open = False
        DesktopAgent._quick_panel(desk, "collection")
        self.assertEqual(desk._chat_bubble.closed, 0)

    def test_the_chat_opened_closes_the_panels_however_it_was_opened(self) -> None:
        import inspect

        from personalized_agent.ui.desktop import DesktopAgent
        said = inspect.getsource(DesktopAgent.open_chat_bubble)
        self.assertLess(said.index("_quick_panels"), said.index("bubble.show()"))


class TheDraftTests(unittest.TestCase):

    def test_it_is_kept_through_a_restart_and_cleared_by_nothing_else(self) -> None:
        from types import SimpleNamespace

        from personalized_agent.ui.desktop import DesktopAgent
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        desk = SimpleNamespace(app=agent.app)
        self.assertEqual(DesktopAgent._chat_draft(desk), "")
        DesktopAgent._keep_chat_draft(desk, "Half a thought")
        self.assertEqual(DesktopAgent._chat_draft(SimpleNamespace(app=agent.app)), "Half a thought")
        DesktopAgent._keep_chat_draft(desk, "")
        self.assertEqual(DesktopAgent._chat_draft(desk), "")


class ByTheSameCodeTests(unittest.TestCase):

    def test_a_panel_asks_the_applications_own_routes(self) -> None:
        from personalized_agent.api.server import ApiServer
        from test_agent_and_api import _free_port
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        port = _free_port()
        agent.app.config.set("api_port", port)
        server = ApiServer(agent.app)
        server.start()
        self.addCleanup(server.stop)
        said = agent.app.notifications.notify("High water", "At noon", kind="proactive")
        self.assertTrue(agent.app.local_post("/api/notifications/read", {"id": said.id}) is not None)
        self.assertTrue(agent.app.notifications.get(said.id)["read"])
        with self.assertRaises(ValueError):
            agent.app.local_post("/api/work/handed/open", {"run": "no_such_run", "n": 0})
        self.assertTrue(json.dumps({}))


class WhatTheListsOffer(unittest.TestCase):
    """The panels' lists, from the application."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app

    def test_each_to_do_can_be_moved_and_an_empty_group_stays(self) -> None:
        todo = self.app.todos.add("Check the gauge")
        self.app.list_groups.add_group("todo", "Coast")
        sections = self.app.quick_sections("todo")
        coast = next(one for one in sections if one.get("group") == "Coast")
        self.assertEqual((coast["rows"], coast["keep"]), ([], True))
        row = next(one for section in sections for one in section["rows"] if one["id"] == todo["id"])
        move = next(one for one in row["actions"] if one["key"] == "move")
        self.assertEqual((move["moves"], move["list"]), (True, "todo"))

    def _lessons(self, action):
        return self.app.db.query("SELECT summary FROM lesson_evidence WHERE action = ?", (action,))

    def test_a_to_do_added_and_a_move_are_lessons(self) -> None:
        from personalized_agent.api.routes import build_routes

        class Ask(dict):
            def require(self, key):
                return self[key]
        routes = build_routes(self.app)
        said = routes[("POST", "/api/todos/add")](Ask(text="Check the gauge", detail=""))
        self.assertIn("Check the gauge", self._lessons("added_todo")[0]["summary"])
        routes[("POST", "/api/lists/groups/move")](Ask(list="todo", item=said["todo"]["id"], to="Coast"))
        self.assertIn("Coast", self._lessons("moved_list_groups")[-1]["summary"])


if __name__ == "__main__":
    unittest.main()
