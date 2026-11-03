"""THE NOTE BESIDE WHAT WAS HANDED OVER (the user, 2026-11-01): a card of
today's kind on the desktop - on its left what was handed over, whole; on
its right how it is used and what to check, each a box to tick - each side
zoomed on its own, its words selectable, its size dragged, never taller than
the screen. Everything in it is made up; nothing here touches the clipboard."""

from __future__ import annotations

import gc
import sys
import unittest
from types import SimpleNamespace

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.destroy()
    HAVE_TK = True
except Exception:                                  # pragma: no cover - no display
    HAVE_TK = False

from personalized_agent.ui import handed_note as hn

#: The keys as this system writes them: Command and ⌘V on a Mac.
MOD = "Command" if sys.platform == "darwin" else "Control"
PASTE = "⌘V" if sys.platform == "darwin" else "Ctrl+V"

NOTE = {"title": "Reply to Ada about the talk date",
        "how": "Press Reply in the thread that opens, paste the reply, and send it yourself.",
        "check": ["It answers: The talk & the date", "The date: Wednesday the 4th"]}
EMAIL = {"kind": "email", "meta": ["Sent to: ada@example.com", "Subject: Re: The talk"],
         "text": "Dear Ada,\n\nWednesday works.\n\n" + "More words.\n" * 120}


@unittest.skipUnless(HAVE_TK, "no display for Tk here")
class TheNoteTests(unittest.TestCase):

    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.zooms = []

    def tearDown(self) -> None:
        self.root.destroy()
        # Its widgets and the notes' callbacks hold one another: let go here,
        # on the thread Tk is on - collected on another thread, Tk ends the
        # process ("async handler deleted by the wrong thread").
        del self.root
        gc.collect()

    def _note(self, note=NOTE, zoom=hn.ZOOM_START):
        note = hn.HandedNote(self.root, note, lambda text: text, "Segoe UI", 10, zoom=zoom,
                             on_zoom=lambda side, step: self.zooms.append((side, step)))
        note.show()
        self.root.update()
        return note

    def _click(self, widget):
        """Pressed and let go over it, as a button is."""
        self.root.update()
        widget.event_generate("<ButtonPress-1>", x=2, y=2)
        widget.event_generate("<ButtonRelease-1>", x=2, y=2)

    @staticmethod
    def _without_the_clipboard(note):
        """What Copy would put on the clipboard - never the user's own."""
        copied = []
        note.window.clipboard_clear = lambda: None
        note.window.clipboard_append = copied.append
        return copied

    def _texts(self, widget):
        """Every label's words and every text's, in the order they are made."""
        out = []
        for child in widget.winfo_children():
            if isinstance(child, tk.Text):
                out.append(child.get("1.0", "end-1c"))
            else:
                try:
                    out.append(str(child.cget("text")))
                except tk.TclError:
                    pass
            out += self._texts(child)
        return out

    def test_on_top_with_its_own_bar_how_and_each_check(self) -> None:
        note = self._note()
        window = note.window
        self.assertTrue(window.attributes("-topmost"))
        if sys.platform == "darwin":
            # A Mac's "plain" style: no title bar, and still written in
            # (an overrideredirect window never takes the keys there).
            style = str(window.tk.call("::tk::unsupported::MacWindowStyle", "style", window._w))
            self.assertIn("plain", style, "its own bar, not the system's")
        else:
            self.assertTrue(window.overrideredirect(), "its own bar, not the system's")
        texts = self._texts(window)
        for words in ("Before you use it", NOTE["title"], "How to use it", NOTE["how"],
                      "Check before you use it", "It answers: The talk & the date", "Done"):
            self.assertIn(words, texts)
        self.assertEqual(texts.count("☐"), 2, "a box to tick for each")
        self.assertEqual(texts.count("✕"), 1, "one ✕ for the whole of it")
        note.close()
        self.assertIsNone(note.window)

    def test_each_side_zoomed_on_its_own_and_kept(self) -> None:
        """The user, 2026-11-01: the two sides zoomed apart; one ✕ for both."""
        note = self._note(dict(NOTE, item=EMAIL))
        built = []
        rebuild = note._build
        note._build = lambda: (built.append(1), rebuild())
        email = next(box for box in note._texts if box.get("1.0", "end-1c") == EMAIL["text"])
        how = next(box for box in note._texts if box.get("1.0", "end-1c") == NOTE["how"])
        points = lambda box: int(self.root.tk.call("font", "actual", box.cget("font"), "-size"))
        before = (points(email), points(how))
        note.zoom_by(1, "item")
        self.root.update()
        self.assertEqual(note.zooms, {"item": hn.ZOOM_START + 1, "how": hn.ZOOM_START})
        self.assertGreater(points(email), before[0], "its words larger")
        self.assertEqual(points(how), before[1], "the other side's as they were")
        self.assertEqual(built, [], "nothing built again (the user: no refresh)")
        note.zoom_by(-1, "how")
        note.zoom_by(-1, "how")
        self.assertEqual(note.zooms["how"], hn.ZOOM_START - 2)
        self.assertEqual(self.zooms, [("item", 3), ("how", 1), ("how", 0)],
                         "each told, to be kept for the next")
        # Ctrl and the keys: the side last pressed in - the pointer, wherever
        # the real one happens to be, taken as over neither side.
        note.window.winfo_containing = lambda x, y: None
        note.window.focus_force()
        note._pressed(SimpleNamespace(widget=note._areas["item"]))
        note.window.event_generate(f"<{MOD}-Key-0>")
        self.root.update()
        self.assertEqual(note.zooms, {"item": hn.ZOOM_START, "how": hn.ZOOM_START - 2})
        # Ctrl and the wheel: the side it is over.
        words = note._panes[1][0]
        words.event_generate("<MouseWheel>", delta=120, state=0x0004, x=10, y=10)
        self.root.update()
        self.assertEqual(note.zooms["how"], hn.ZOOM_START - 1)
        note.close()

    def test_never_taller_than_the_screen_and_what_it_says_scrolls(self) -> None:
        # Taller than any screen: how to use it is kept to its first 1200
        # characters (handed.HOW_MOST) - on a Mac, whose fonts are drawn
        # smaller, those fit (found on the lab's Mac, 2026-11-02) - so many
        # things to check as well.
        long = dict(NOTE, how=" ".join(["Open the sheet and compare its totals."] * 60),
                    check=[f"Line {n}: the month's total matches the sheet" for n in range(80)])
        note = self._note(long, zoom=len(hn.ZOOMS) - 1)
        window = note.window
        self.assertLessEqual(window.winfo_y() + window.winfo_height(),
                             window.winfo_screenheight())
        [(body, scrolls, _side)] = note._panes
        self.assertTrue(scrolls, "what it says has a bar of its own")
        body.event_generate("<MouseWheel>", delta=-360, x=20, y=20)
        self.root.update()
        self.assertGreater(body.yview()[0], 0.0, "the wheel scrolls it")
        note.close()

    def test_a_short_one_does_not_scroll(self) -> None:
        note = self._note()
        self.assertEqual([scrolls for _body, scrolls, _side in note._panes], [False],
                         "no bar when it all fits")
        note.close()

    def test_what_was_handed_over_on_the_left_how_on_the_right(self) -> None:
        """The user, 2026-11-01: two sides - the email itself on the left,
        saying it is on the clipboard; how and what to check on the right."""
        note = self._note(dict(NOTE, item=EMAIL, copied=True))
        texts = self._texts(note.window)
        for words in ("Email", "Copy all", f"✓  Already on your clipboard - paste it with {PASTE}",
                      "Sent to: ada@example.com\nSubject: Re: The talk", EMAIL["text"],
                      "How to use it", "It answers: The talk & the date"):
            self.assertIn(words, texts)
        (left, left_scrolls, _l), (right, right_scrolls, _r) = note._panes
        self.assertTrue(left.winfo_rootx() < right.winfo_rootx(), "the email on the left")
        self.assertEqual((left_scrolls, right_scrolls), (True, False), "each side on its own")
        left.event_generate("<MouseWheel>", delta=-240, x=20, y=20)
        self.root.update()
        self.assertGreater(left.yview()[0], 0.0)
        self.assertEqual(right.yview()[0], 0.0, "the side the wheel was not over stays")
        self.assertEqual(note._side_of(right), "how", "the right side is not taken for the left")
        copied = self._without_the_clipboard(note)
        note._copy(EMAIL["text"])
        self.assertEqual(copied, [EMAIL["text"]])
        self.assertIn(f"✓  Copied - paste it with {PASTE}", self._texts(note.window))
        note.close()

    def test_not_copied_it_does_not_say_so_until_copy_is_pressed(self) -> None:
        note = self._note(dict(NOTE, item={"kind": "text", "meta": [], "text": "Sure."}))
        texts = self._texts(note.window)
        self.assertNotIn(f"✓  Already on your clipboard - paste it with {PASTE}", texts)
        self.assertEqual(len(note._panes), 2)
        self._without_the_clipboard(note)
        note._copy("Sure.")
        self.root.update()
        self.assertTrue(note._copied.winfo_ismapped())
        note.close()

    def test_its_words_can_be_selected_and_copied(self) -> None:
        """The user, 2026-11-01: the instructions selectable - Ctrl+C, and
        the right button's Copy."""
        note = self._note(dict(NOTE, item=EMAIL))
        how = next(box for box in note._texts if box.get("1.0", "end-1c") == NOTE["how"])
        self.assertEqual(str(how.cget("state")), "disabled", "read, never written in")
        # Ctrl+A - bound, and what it does called by name: a key generated
        # reaches it only while it has the desktop's focus, which another
        # window may take in the middle of a test.
        self.assertTrue(how.bind(f"<{MOD}-a>"), "Ctrl+A (⌘A) is bound")
        how.select_everything()
        self.assertEqual(how.get("sel.first", "sel.last"), NOTE["how"], "Ctrl+A: all of it")
        copied = self._without_the_clipboard(note)
        how.tag_remove("sel", "1.0", "end")
        how.tag_add("sel", "1.0", "1.5")
        how.event_generate("<<Copy>>")                    # what Ctrl+C is
        self.assertEqual(copied, ["Press"], "what is selected")
        menu = note._menu(how, False, lambda: None, lambda: None)
        labels = [menu.entrycget(n, "label") for n in range(menu.index("end") + 1)
                  if menu.type(n) == "command"]
        self.assertEqual(labels, ["Copy", "Select all"], "read only: nothing to cut or paste")
        # A box is ticked by its box; pressing what it says selects it instead.
        box = next(child for child in self._all(note.window)
                   if isinstance(child, tk.Label) and child.cget("text") == "☐")
        box.event_generate("<Button-1>")
        self.assertEqual(note.ticked, [True, False])
        note.close()

    def test_copy_all_and_a_press_says_what_it_did(self) -> None:
        """The user, 2026-11-01: a Copy all for the message; Copy gave no sign."""
        note = self._note(dict(NOTE, item=EMAIL))
        [button] = [child for child in self._all(note.window)
                    if isinstance(child, tk.Label) and child.cget("text") == "Copy all"]
        copied = self._without_the_clipboard(note)
        # Pressed and dragged off it: nothing (the user, 2026-11-02: done
        # when let go over it, as a button is - not when pressed).
        self.root.update()
        button.event_generate("<ButtonPress-1>", x=2, y=2)
        button.event_generate("<ButtonRelease-1>", x=-50, y=-50)
        self.assertEqual(copied, [], "let go elsewhere: not copied")
        self._click(button)
        self.assertEqual(copied, [EMAIL["text"]], "the whole of it")
        self.assertEqual(button.cget("text"), "✓  Copied", "said on the button pressed")
        self.root.after(1700)
        self.root.update()
        self.assertEqual(button.cget("text"), "Copy all", "then as it was")
        note.close()

    def test_what_was_handed_over_can_be_changed_and_copied_as_changed(self) -> None:
        """The user, 2026-11-01: the deliverable edited there, with Cut, Copy
        and Paste."""
        note = self._note(dict(NOTE, item=EMAIL, copied=True))
        words = note._words
        self.assertEqual(str(words.cget("state")), "normal", "written in")
        menu = note._menu(words, True, lambda: None, lambda: None)
        labels = [menu.entrycget(n, "label") for n in range(menu.index("end") + 1)
                  if menu.type(n) == "command"]
        self.assertEqual(labels, ["Cut", "Copy", "Paste", "Select all"])
        words.insert("1.0", "Hello again. ")
        self.root.update()
        self.assertEqual(note._copied.cget("text"),
                         "✎  Changed - press Copy to copy what you changed",
                         "the clipboard no longer holds what it says")
        copied = self._without_the_clipboard(note)
        note._copy_item()
        self.assertEqual(copied, ["Hello again. " + EMAIL["text"]], "as it now is")
        self.assertTrue(note._copied.cget("text").startswith("✓  Copied"))
        note.zoom_by(1, "item")
        self.root.update()
        self.assertTrue(note._words.get("1.0", "end-1c").startswith("Hello again. "),
                        "kept through a zoom")
        note.close()

    def test_save_keeps_what_was_changed(self) -> None:
        """The user, 2026-11-01: Save, and it is kept."""
        kept = []
        note = hn.HandedNote(self.root, dict(NOTE, item=dict(EMAIL, editable=True)),
                             lambda text: text, "Segoe UI", 10,
                             on_save=lambda words: kept.append(words) or True)
        note.show()
        self.root.update()
        shown = lambda: note._save_button.winfo_ismapped()
        self.assertFalse(shown(), "no Save before anything is changed")
        note._words.insert("end", "\nP.S. Room 2.")
        self.root.update()
        self.assertTrue(shown(), "changed: Save")
        self.assertEqual(note._copied.cget("text"), "✎  Changed - Save keeps it, Copy copies it")
        note._save()
        self.root.update()
        self.assertEqual(kept, [EMAIL["text"] + "\nP.S. Room 2."])
        self.assertEqual(note._copied.cget("text"), "✓  Saved - opened again, it is this")
        self.assertFalse(shown(), "saved: Save goes")
        note._words.insert("end", " Thanks.")
        self.root.update()
        self.assertTrue(shown(), "changed again: Save again")
        note.zoom_by(1, "item")
        self.root.update()
        self.assertTrue(shown(), "still not saved after a zoom")
        note.on_save = lambda words: False
        note._save()
        self.root.update()
        self.assertEqual(note._copied.cget("text"), "✕  Not saved - copy it, so it is not lost")
        self.assertTrue(shown(), "not saved: Save stays")
        note.close()

    def test_no_save_where_nothing_can_keep_it_and_a_file_is_not_written_in(self) -> None:
        note = self._note(dict(NOTE, item=EMAIL))              # no on_save
        note._words.insert("end", "x")
        self.root.update()
        self.assertNotIn("Save", self._texts(note.window), "nothing to keep it in")
        note.close()
        file = {"kind": "file", "meta": [], "text": "C:/w/report.pdf", "editable": False}
        note = hn.HandedNote(self.root, dict(NOTE, item=file), lambda text: text, "Segoe UI",
                             10, on_save=lambda words: True)
        note.show()
        self.root.update()
        self.assertEqual(str(note._words.cget("state")), "disabled")
        self.assertNotIn("Save", self._texts(note.window))
        note.close()

    def _all(self, widget):
        out = []
        for child in widget.winfo_children():
            out += [child] + self._all(child)
        return out

    def test_headed_by_the_work_s_name(self) -> None:
        """The user, 2026-11-02: its header the work's name, not "Before you
        use it" - cut short when long, its buttons never pushed out."""
        long = "Summary of the garden club's spring planting plan and seed order " * 3
        note = self._note(dict(NOTE, work=long))
        texts = self._texts(note._bar_frame)
        self.assertNotIn("Before you use it", texts)
        shown = next(one for one in texts if one.startswith("Summary of the garden"))
        self.assertLessEqual(len(shown), hn.HEADING_MOST)
        self.assertTrue(shown.endswith("…"))
        self.assertEqual(note.window.title(), shown)
        for button in ("–", "✕"):
            widget = next(one for one in note._bar_frame.winfo_children()
                          if isinstance(one, tk.Label) and one.cget("text") == button)
            self.assertTrue(widget.winfo_ismapped() and widget.winfo_width() > 1, button)
        note.close()
        plain = self._note(dict(NOTE, work="Garden plan"))
        self.assertIn("Garden plan", self._texts(plain._bar_frame))
        plain.close()

    def test_folded_to_its_bar_and_opened_as_it_was(self) -> None:
        """The user, 2026-11-02: a way to minimize it - collapsed as Zoom's
        is, a bar left."""
        note = self._note(dict(NOTE, item=EMAIL))
        window = note.window
        self.assertIn("–", self._texts(note._bar_frame), "– beside the ✕")
        words = note._texts[-1] if note._texts else None
        if words is not None:
            words.yview_moveto(0.5)
        self.root.update()
        before = (window.winfo_width(), window.winfo_height())
        right, top = window.winfo_x() + window.winfo_width(), window.winfo_y()
        scrolled = words.yview()[0] if words is not None else None
        note.fold()
        self.root.update()
        self.assertTrue(note.folded)
        self.assertFalse(note._middle.winfo_ismapped(), "what it says taken away")
        self.assertFalse(note._foot_frame.winfo_ismapped())
        self.assertTrue(note._bar_frame.winfo_ismapped(), "its bar left")
        self.assertLess(window.winfo_height(), 80, "only a bar")
        self.assertLess(window.winfo_width(), before[0])
        self.assertAlmostEqual(window.winfo_x() + window.winfo_width(), right, delta=2,
                               msg="where it stood, by its right edge")
        self.assertEqual(window.winfo_y(), top)
        self.assertTrue(window.attributes("-topmost"), "still on top")
        self.assertEqual(note._fold_button.cget("text"), "□")
        note.unfold()
        self.root.update()
        self.assertFalse(note.folded)
        self.assertTrue(note._middle.winfo_ismapped())
        self.assertTrue(note._foot_frame.winfo_ismapped())
        self.assertEqual((window.winfo_width(), window.winfo_height()), before, "as it was")
        if words is not None:
            self.assertAlmostEqual(words.yview()[0], scrolled, places=3,
                                   msg="where it was scrolled to")
        self.assertEqual(note._fold_button.cget("text"), "–")
        # A double click on its bar folds it, and again opens it (Tk makes
        # no double click of its own for a test: the binding, then its act).
        self.assertTrue(note._bar_frame.bind("<Double-Button-1>"))
        note.toggle_folded()
        self.root.update()
        self.assertTrue(note.folded)
        note.toggle_folded()
        self.root.update()
        self.assertFalse(note.folded)
        note.close()

    def test_its_size_dragged_by_its_corner(self) -> None:
        note = self._note(dict(NOTE, item=EMAIL))
        width, height = note.window.winfo_width(), note.window.winfo_height()
        x, y = note._grip.winfo_rootx(), note._grip.winfo_rooty()
        note._resize_start(SimpleNamespace(x_root=x, y_root=y))
        note._resize_move(SimpleNamespace(x_root=x - 150, y_root=y - 120))
        note._resize_end()
        self.root.update()
        self.assertLess(note.window.winfo_width(), width)
        self.assertLess(note.window.winfo_height(), height)
        kept = note.window.winfo_width()
        note.zoom_by(1, "how")
        self.root.update()
        self.assertAlmostEqual(note.window.winfo_width(), kept, delta=3,
                               msg="its size kept while zoomed")
        note._resize_start(SimpleNamespace(x_root=x, y_root=y))
        note._resize_move(SimpleNamespace(x_root=x - 5000, y_root=y - 5000))
        note._resize_end()
        self.root.update()
        self.assertGreaterEqual(note.window.winfo_width(), hn.LEAST[0], "never smaller than this")
        note.close()

    def test_sized_by_any_edge_and_shared_by_the_line_between(self) -> None:
        """The user, 2026-11-01: its edges dragged as a window's; the line
        between the sides dragged to share the width anew."""
        note = self._note(dict(NOTE, item=EMAIL))
        window = note.window
        where = lambda: (window.winfo_x(), window.winfo_y(), window.winfo_width(),
                         window.winfo_height())
        self.assertEqual(len(note._strips), 8, "four edges, four corners")
        x, y, width, height = where()
        note._edge_start(SimpleNamespace(x_root=100, y_root=100), "w")
        note._edge_move(SimpleNamespace(x_root=40, y_root=100))
        note._resize_end()
        self.root.update()
        x2, y2, width2, height2 = where()
        self.assertGreater(width2, width + 50, "wider by its left edge")
        self.assertAlmostEqual(x2 + width2, x + width, delta=2, msg="its right edge where it was")
        note._edge_start(SimpleNamespace(x_root=100, y_root=100), "n")
        note._edge_move(SimpleNamespace(x_root=100, y_root=150))
        note._resize_end()
        self.root.update()
        x3, y3, width3, height3 = where()
        self.assertLess(height3, height2)
        self.assertAlmostEqual(y3 + height3, y2 + height2, delta=2, msg="its bottom where it was")
        left, right = note._areas["item"].winfo_width(), note._areas["how"].winfo_width()
        at = note._divider_frame.winfo_rootx()
        note._split_start(SimpleNamespace(x_root=at))
        note._split_move(SimpleNamespace(x_root=at + 100))
        note._split_end()
        self.root.update()
        self.assertGreater(note._areas["item"].winfo_width(), left + 80)
        self.assertLess(note._areas["how"].winfo_width(), right - 80)
        self.assertAlmostEqual(window.winfo_width(), width3, delta=3, msg="its size kept")
        shared = note._split
        note.zoom_by(1, "how")
        self.root.update()
        self.assertEqual(note._split, shared, "kept while a side is zoomed")
        note._split_start(SimpleNamespace(x_root=at))
        note._split_move(SimpleNamespace(x_root=at - 5000))
        note._split_end()
        self.root.update()
        self.assertGreaterEqual(note._areas["item"].winfo_width(), hn.SIDE_LEAST - 2,
                                "never narrower than this")
        note.close()

    def test_a_form_each_part_its_own_box_copy_and_save(self) -> None:
        """A form (the user, 2026-11-01): each part under the box it goes
        into, copied on its own, saved together."""
        form = {"kind": "form", "meta": ["Goes into: https://review.example.org/7"],
                "editable": True, "text": "",
                "parts": [{"field": "Summary", "text": "The paper studies ferns."},
                          {"field": "Weaknesses", "text": "Small sample. " * 200}]}
        kept = []
        note = hn.HandedNote(self.root, dict(NOTE, item=form, copied=True), lambda t: t,
                             "Segoe UI", 10, on_save=lambda words: kept.append(words) or True)
        note.show()
        self.root.update()
        texts = self._texts(note.window)
        for words in ("Form to fill", "1.  Summary", "2.  Weaknesses",
                      "The paper studies ferns.", f"✓  Already on your clipboard - paste it with {PASTE}"):
            self.assertIn(words, texts)
        self.assertEqual(texts.count("Copy"), 2, "a Copy for each part")
        copied = self._without_the_clipboard(note)
        second = note._part_boxes[1]
        copy = [child for child in self._all(note.window)
                if isinstance(child, tk.Label) and child.cget("text") == "Copy"][1]
        self._click(copy)
        self.assertEqual(copied, [form["parts"][1]["text"]])
        self.assertLessEqual(note.window.winfo_y() + note.window.winfo_height(),
                             note.window.winfo_screenheight(), "a long form scrolls")
        note._part_boxes[0].insert("end", " Well.")
        self.root.update()
        self.assertTrue(note._save_button.winfo_ismapped())
        note._save()
        self.assertEqual(kept, [["The paper studies ferns. Well.", form["parts"][1]["text"]]])
        self.assertTrue(second.winfo_exists())
        note.close()

    def test_what_it_says_follows_as_it_is_sized_nothing_built_again(self) -> None:
        """The user, 2026-11-01: "no refresh" - sized, its words follow; its
        size and share kept for the next note."""
        kept = []
        note = hn.HandedNote(self.root, dict(NOTE, item=EMAIL), lambda t: t, "Segoe UI", 10,
                             on_layout=lambda size, split: kept.append((size, split)))
        note.show()
        self.root.update()
        built = []
        rebuild = note._build
        note._build = lambda: (built.append(1), rebuild())
        how = next(box for box in note._texts if box.get("1.0", "end-1c") == NOTE["how"])
        lines = int(how.cget("height"))
        note._edge_start(SimpleNamespace(x_root=500, y_root=100), "w")
        for step in (60, 200, 420):
            note._edge_move(SimpleNamespace(x_root=500 + step, y_root=100))
            self.root.update()
        note._resize_end()
        self.root.update()
        # Given once a frame and laid out a moment after the last change: a
        # busy machine (the suite in four processes) took longer than the
        # 120 ms this once waited - waited for, up to 3 s.
        import time
        until = time.monotonic() + 3.0
        while int(how.cget("height")) <= lines and time.monotonic() < until:
            self.root.after(20)
            self.root.update()
        self.assertEqual(built, [], "nothing built again")
        self.assertGreater(int(how.cget("height")), lines, "its words wrapped anew, narrower")
        self.assertEqual(kept[-1][0], (note.window.winfo_width(), note.window.winfo_height()))
        at = note._divider_frame.winfo_rootx()
        note._split_start(SimpleNamespace(x_root=at))
        note._split_move(SimpleNamespace(x_root=at - 120))
        self.root.update()
        note._split_end()
        self.root.update()
        self.assertEqual(built, [], "nor for the line between the sides")
        self.assertAlmostEqual(kept[-1][1], note._split)
        size, split = kept[-1]
        note.close()
        again = hn.HandedNote(self.root, dict(NOTE, item=EMAIL), lambda t: t, "Segoe UI", 10,
                              kept_size=size, split=split)
        again.show()
        self.root.update()
        self.assertAlmostEqual(again.window.winfo_width(), size[0], delta=3,
                               msg="the next note as the user left the last")
        self.assertAlmostEqual(again._split, split)
        again.close()

    def test_moved_or_dragged_it_does_not_flicker(self) -> None:
        """The user, 2026-11-01: it flickered as it was dragged. Moved by its
        bar, nothing is laid out again; sized, the pointer's moves are given
        at most once a frame."""
        note = self._note(dict(NOTE, item=EMAIL))
        laid = []
        reflow = note._reflow
        note._reflow = lambda: (laid.append(1), reflow())
        note._grab(SimpleNamespace(x_root=1000, y_root=100))
        for step in range(30):
            note._move(SimpleNamespace(x_root=1000 - step * 3, y_root=100 + step))
            self.root.update()
        self.root.after(120)
        self.root.update()
        self.assertEqual(laid, [], "moved: nothing laid out again")
        given = []
        geometry = note.window.geometry
        note.window.geometry = lambda *a: (given.append(a) if a else None, geometry(*a))[1]
        note._edge_start(SimpleNamespace(x_root=500, y_root=100), "w")
        for step in range(30):
            note._edge_move(SimpleNamespace(x_root=500 + step * 4, y_root=100))
        self.root.update()
        note._resize_end()
        self.assertLessEqual(len(given), 2, "thirty moves, given once or twice - not thirty")
        note.close()

    def test_as_long_as_the_work_made_it(self) -> None:
        """The user, 2026-11-01: a review of a paper is long - shown whole,
        scrolled within itself."""
        review = "\n\n".join(f"Section {n}. The sample in table {n} is small." * 4
                             for n in range(1, 400))
        note = self._note(dict(NOTE, item={"kind": "text", "meta": [], "text": review}))
        words, scrolls, _side = note._panes[0]
        self.assertEqual(words.get("1.0", "end-1c"), review, "every word of it")
        self.assertTrue(scrolls)
        self.assertLess(words.winfo_height(), note.window.winfo_screenheight(),
                        "scrolled within itself, not drawn whole")
        words.yview_moveto(1.0)
        self.root.update()
        self.assertEqual(words.yview()[1], 1.0, "its end reached")
        note.close()


class OneThatFailsIsSkippedTests(unittest.TestCase):
    """A thing on the desktop's queue that raised ended the loop that shows
    them: every bubble and note after it was silently lost until a restart.
    Now it alone is skipped, and the loop goes on."""

    def test_the_rest_shown_and_the_loop_asked_again(self) -> None:
        import queue
        from personalized_agent.ui.desktop import DesktopAgent
        shown, asked = [], []

        def handle(kind, payload):
            if kind == "bad":
                raise RuntimeError("drawn wrong")
            shown.append(kind)
        fake = SimpleNamespace(queue=queue.Queue(), _handle=handle, _drain_queue=None,
                               root=SimpleNamespace(after=lambda ms, then: asked.append(ms)))
        for kind in ("bad", "handed_note", "notification"):
            fake.queue.put((kind, {}))
        with self.assertLogs("personalized_agent.ui.desktop", "WARNING"):
            DesktopAgent._drain_queue(fake)
        self.assertEqual(shown, ["handed_note", "notification"])
        self.assertEqual(asked, [150], "asked again")


@unittest.skipUnless(HAVE_TK, "no display for Tk here")
class OpenedThroughTheDesktopTests(unittest.TestCase):
    """The way it comes up: opened on the page, published, queued, shown."""

    def test_from_the_bus_to_the_screen_each_sides_zoom_kept(self) -> None:
        import queue
        from personalized_agent.ui.desktop import DesktopAgent
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(gc.collect)                    # on Tk's thread (TheNoteTests.tearDown)
        self.addCleanup(root.destroy)
        kept = {"handed_note_zoom_item": 4, "handed_note_zoom_how": 1}
        config = SimpleNamespace(get=lambda key, default=None: kept.get(key, default),
                                 set=lambda key, value, internal=False: kept.update({key: value}))
        saved = []
        answers = []
        work = SimpleNamespace(save_handed=lambda run, n, words: saved.append((run, n, words))
                               or (answers.pop(0) if answers else {"body": words}))
        fake = SimpleNamespace(queue=queue.Queue(), root=root, config=config,
                               app=SimpleNamespace(work=work),
                               _words=lambda text: text, _popup_font=lambda: ("Segoe UI", 10))
        DesktopAgent._on_handed_opened(fake, "work.handed_opened",
                                       dict(NOTE, item=EMAIL, run="run_7", n=1))
        kind, payload = fake.queue.get_nowait()
        self.assertEqual(kind, "handed_note")
        DesktopAgent._show_handed_note(fake, payload)
        root.update()
        note = fake._handed_note
        self.assertEqual(note.zooms, {"item": 4, "how": 1}, "opened at the zooms kept")
        note.zoom_by(1, "how")
        self.assertEqual(kept["handed_note_zoom_how"], 2)
        self.assertEqual(kept["handed_note_zoom_item"], 4)
        note = fake._handed_note
        note._save()
        self.assertEqual(saved, [("run_7", 1, EMAIL["text"])], "saved to its run and item")
        note.on_layout((700, 400), 0.4)
        self.assertEqual((kept["handed_note_size"], kept["handed_note_split"]),
                         ([700, 400], 0.4), "its size and share kept for the next note")
        DesktopAgent._show_handed_note(fake, payload)
        self.assertEqual((fake._handed_note._size, fake._handed_note._split), ((700, 400), 0.4))
        note = fake._handed_note
        answers.append({"error": "going"})                    # carried on meanwhile
        note._save()
        self.assertEqual(note._copied.cget("text"), "✕  It is working on it again - save once "
                         "it is done; copy it so it is not lost")
        DesktopAgent._show_handed_note(fake, payload)       # a newer one in its place
        self.assertIsNone(note.window)
        fake._handed_note.close()


if __name__ == "__main__":
    unittest.main()
