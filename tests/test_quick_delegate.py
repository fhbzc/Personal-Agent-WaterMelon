"""The shortcut's quick Delegate work box: the agent's own window, there at
once - a title and a description, the folders and files, Create - and the
page opened only then, on the piece of work."""

from __future__ import annotations

import gc
import unittest
from unittest import mock

from support import TemporaryAgent

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.destroy()
    HAVE_TK = True
except Exception:                                  # pragma: no cover - no display
    HAVE_TK = False


class _Drafts:
    def __init__(self):
        self.made, self.composed = [], []

    def create(self, **kw):
        self.made.append(kw)
        return {"id": "draft_quick1"}

    def compose(self, draft_id, instruction="", title=""):
        self.composed.append((draft_id, instruction, title))
        return {"draft": {"id": draft_id}}


class _App:
    def __init__(self):
        self.drafts = _Drafts()


@unittest.skipUnless(HAVE_TK, "no display for Tk here")
class QuickBoxTests(unittest.TestCase):

    def setUp(self) -> None:
        from personalized_agent.ui.quick_delegate import QuickDelegate
        self.root = tk.Tk()
        self.root.withdraw()
        self.opened, self.posted = [], []
        self.app = _App()
        self.box = QuickDelegate(self.root, self.app, self.opened.append, lambda text: text,
                                 self.posted.append)

    def tearDown(self) -> None:
        self.box.close()
        self.root.destroy()
        # Tk's objects let go of on its own thread: collected on a worker's, they end the process.
        del self.box, self.root
        gc.collect()

    def _run_posted(self) -> None:
        import time
        for _ in range(100):
            if self.posted:
                break
            time.sleep(0.02)
        while self.posted:
            self.posted.pop(0)()

    def test_title_and_description_handed_over_then_the_page(self) -> None:
        self.box.show()
        self.root.update()
        self.box.title.insert(0, "Tidy the seed catalogue")
        self.box.text.insert("1.0", "Sort the seeds by sowing month.")
        self.box.folders.append("C:/garden/seeds")
        self.box.submit()
        self._run_posted()
        self.assertEqual(self.app.drafts.made[0]["folders"], ["C:/garden/seeds"])
        self.assertEqual(self.app.drafts.made[0]["source"], "shortcut")
        self.assertEqual(self.app.drafts.composed,
                         [("draft_quick1", "Sort the seeds by sowing month.",
                           "Tidy the seed catalogue")])
        self.assertEqual(self.opened, ["/?solo=delegate#delegate=draft_quick1"])
        self.assertIsNone(self.box.window, "the quick box closes once the page is opened")

    def test_what_is_in_front_goes_with_it_unless_taken_off(self) -> None:
        self.box._read_front = lambda: {"application": "chrome.exe", "title": "Tide tables"}
        self.box.show()
        self.root.update()
        self.box.text.insert("1.0", "Sort them.")
        self.box.submit()
        self._run_posted()
        self.assertEqual(self.app.drafts.made[0]["options"]["front"]["title"], "Tide tables")
        self.box.show()
        self.root.update()
        self.box._without_front()
        self.box.text.insert("1.0", "Sort them.")
        self.box.submit()
        self._run_posted()
        self.assertIsNone(self.app.drafts.made[1]["options"])

    def test_its_buttons_are_always_on_it(self) -> None:
        self.box._read_front = lambda: {"title": "A long title in front " * 8}
        self.box.show()
        self.box.folders.extend(["C:/garden/seeds", "C:/garden/beds", "C:/garden/tools"])
        self.box._draw_chosen()
        self.root.update()
        window, button = self.box.window, self.box.next_button
        bottom = button.winfo_rooty() + button.winfo_height()
        self.assertLessEqual(bottom, window.winfo_rooty() + window.winfo_height(),
                             "Create was once pushed off the bottom of the box")

    def test_pressed_twice_it_is_handed_over_once(self) -> None:
        self.box.show()
        self.root.update()
        self.box.text.insert("1.0", "Sort them.")
        self.box.submit()
        self.box.submit()
        self._run_posted()
        self.assertEqual(len(self.app.drafts.made), 1)

    def test_nothing_written_is_not_handed_over(self) -> None:
        self.box.show()
        self.root.update()
        self.box.submit()
        self.assertEqual(self.app.drafts.made, [])
        self.assertTrue(self.box.note.cget("text"))

    def test_pressed_again_one_box(self) -> None:
        self.box.show()
        first = self.box.window
        self.box.show()
        self.assertIs(self.box.window, first)

    def test_its_edges_cover_none_of_its_words(self) -> None:
        """The user, 2026-11-09: "delegate work的边框好像挡到字了"."""
        self.box._read_front = lambda: {"title": "Tide tables"}
        self.box.show()
        self.root.update()
        rim = self.box._frame
        card = rim.pack_slaves()[0]
        words = []

        def walk(widget):
            for child in widget.winfo_children():
                if child.winfo_class() in ("Label", "Entry", "Text", "Canvas") and child.winfo_ismapped():
                    words.append(child)
                walk(child)
        walk(card)
        self.assertTrue(words)
        for strip in rim.place_slaves():
            for one in words:
                apart = (strip.winfo_rootx() + strip.winfo_width() <= one.winfo_rootx()
                         or one.winfo_rootx() + one.winfo_width() <= strip.winfo_rootx()
                         or strip.winfo_rooty() + strip.winfo_height() <= one.winfo_rooty()
                         or one.winfo_rooty() + one.winfo_height() <= strip.winfo_rooty())
                self.assertTrue(apart, (one.winfo_class(), str(one.cget("text") if one.winfo_class()
                                                               == "Label" else "")))

    def test_one_button_for_files_or_a_folder_and_either_dropped(self) -> None:
        """The user, 2026-11-09: "为什么要add folder和add file分开，不是用一个就行吗"."""
        import tempfile
        from pathlib import Path
        self.box.show()
        self.root.update()
        texts = []

        def walk(widget):
            for child in widget.winfo_children():
                if child.winfo_class() == "Label":
                    texts.append(str(child.cget("text")))
                walk(child)
        walk(self.box.window)
        self.assertIn("Add files or a folder", texts)
        self.assertNotIn("Add folder", texts)
        self.assertNotIn("Add files", texts)
        with tempfile.TemporaryDirectory() as there:
            folder = Path(there) / "seeds"
            folder.mkdir()
            sheet = Path(there) / "sowing.txt"
            sheet.write_text("March", encoding="utf-8")
            self.box._dropped([str(folder), str(sheet), str(sheet)])
            self.root.update()
            self.assertEqual(self.box.folders, [str(folder)], "a folder as a folder")
            self.assertEqual(self.box.files, [str(sheet)], "a file as a file, once")

    def test_one_picker_each_told_from_the_disk_and_a_choice_only_where_it_cannot(self) -> None:
        """The user, 2026-11-09: "为什么不能做一个按钮，根据我选的位置系统自动判断是文件夹还是文件"."""
        import tempfile
        from pathlib import Path
        from personalized_agent.ui import pick_both
        self.box.show()
        self.root.update()
        with tempfile.TemporaryDirectory() as there:
            folder = Path(there) / "beds"
            folder.mkdir()
            sheet = Path(there) / "rows.txt"
            sheet.write_text("peas", encoding="utf-8")
            with mock.patch.object(pick_both, "pick", return_value=[str(folder), str(sheet)]):
                self.box._add_menu()
            self.assertEqual((self.box.folders, self.box.files), ([str(folder)], [str(sheet)]))
        asked = []
        with mock.patch.object(pick_both, "pick", return_value=None), \
                mock.patch("tkinter.Menu.tk_popup", lambda *_a: asked.append(True)):
            self.box._add_menu()
        self.assertEqual(asked, [True], "where the system cannot: which, asked")

    def test_its_corners_have_the_systems_diagonal_pointer(self) -> None:
        """The user, 2026-11-09: "左下角右下角那个为什么不用和其他边框拉动大小一样的鼠标"."""
        import sys
        from personalized_agent.ui.chat_bubble import EDGES
        if sys.platform.startswith("win"):
            self.assertEqual({EDGES[one] for one in ("nw", "se")}, {"size_nw_se"})
            self.assertEqual({EDGES[one] for one in ("ne", "sw")}, {"size_ne_sw"})
        self.box.show()
        self.root.update()
        shown = {str(one.cget("cursor")) for one in self.box._frame.place_slaves()}
        self.assertEqual(shown, set(EDGES.values()))

    def test_the_shortcut_again_closes_it(self) -> None:
        """The user, 2026-11-09: "c+a+d, delegate work要能关掉（打开一次，再输入关掉）"."""
        self.box.toggle()
        self.root.update()
        self.assertIsNotNone(self.box.window)
        self.box.toggle()
        self.assertIsNone(self.box.window, "pressed again: closed")
        self.box.toggle()
        self.root.update()
        self.assertIsNotNone(self.box.window, "and again: open")

    def test_sized_by_its_edges_never_smaller_than_what_is_in_it_and_kept(self) -> None:
        """The user, 2026-11-09: "直接快捷键显示的delegate work panel不能鼠标拉动调整大小"."""
        from types import SimpleNamespace
        kept = {}
        self.app.config = SimpleNamespace(get=lambda key, default=None: kept.get(key, default),
                                          set=lambda key, value, internal=False: kept.update({key: value}))
        self.box.show()
        self.root.update()
        window = self.box.window
        self.assertEqual(len(self.box._frame.place_slaves()), 8, "four edges and four corners")
        width, height = window.winfo_width(), window.winfo_height()
        self.box._edge_start(SimpleNamespace(x_root=900, y_root=700), "se")
        self.box._edge_move(SimpleNamespace(x_root=1000, y_root=820))
        self.box._edge_end()
        self.root.update()
        self.assertEqual((window.winfo_width(), window.winfo_height()), (width + 100, height + 120))
        self.assertEqual(kept["quick_delegate_size"], [width + 100, height + 120])
        self.box._edge_start(SimpleNamespace(x_root=900, y_root=700), "se")
        self.box._edge_move(SimpleNamespace(x_root=100, y_root=100))
        self.box._edge_end()
        self.root.update()
        self.assertGreaterEqual(window.winfo_height(), self.box._frame.winfo_reqheight(),
                                "its buttons still on it")
        self.box.close()
        self.box.show()
        self.root.update()
        self.assertEqual(self.box.window.winfo_width(), kept["quick_delegate_size"][0],
                         "opened again as large as it was made")


class TheTitleIsTheWorksTests(unittest.TestCase):
    """A title the user gave is said to the planner and is the plan's own."""

    def test_given_title_kept_over_the_planners(self) -> None:
        from personalized_agent.agent.work import delegate
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        drafts = agent.app.drafts
        draft = drafts.create(source="shortcut", write_first=True)
        with mock.patch.object(drafts, "_collect_apart"):
            said = drafts.compose(draft["id"], "Sort the seeds by sowing month.",
                                  title="Tidy the seed catalogue")
        options = said["draft"]["options"]
        self.assertEqual(options["title"], "Tidy the seed catalogue")
        self.assertTrue(options["instruction"].startswith("Tidy the seed catalogue\n\n"))
        planned = delegate.plan_of({"title": "Seed catalogue cleanup", "goal": "g"})
        with mock.patch.object(drafts, "collect", return_value={}), \
                mock.patch.object(drafts, "plan", return_value=planned):
            drafts.collect_and_plan(draft["id"])
        self.assertEqual(drafts.get(draft["id"])["plan"]["title"], "Tidy the seed catalogue")


if __name__ == "__main__":
    unittest.main()
