"""THE NOTE BESIDE WHAT WAS HANDED OVER (the user, 2026-11-01): a card of
today's kind on the desktop - on its left what was handed over, whole; on
its right how it is used and what to check, each a box to tick - each side
zoomed on its own, its words selectable, its size dragged, never taller than
the screen. Everything in it is made up; nothing here touches the clipboard."""

from __future__ import annotations

import gc
import sys
import time
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

    def test_zoomed_whole_and_kept(self) -> None:
        """The user, 2026-11-08 (in place of 2026-11-05's one zoom for both): each part zoomed by
        itself - "item instruction panel 左右两个也是支持单独缩放的"."""
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
        self.assertEqual((note.zooms["item"], note.zooms["how"]), (hn.ZOOM_START + 1, hn.ZOOM_START))
        self.assertGreater(points(email), before[0], "its words larger")
        self.assertEqual(points(how), before[1], "the other side's as they were")
        self.assertEqual(built, [], "nothing built again (the user: no refresh)")
        note.zoom_by(-1, "how")
        note.zoom_by(-1, "how")
        self.assertEqual((note.zooms["item"], note.zooms["how"]), (hn.ZOOM_START + 1, hn.ZOOM_START - 2))
        start = hn.ZOOM_START
        self.assertEqual(self.zooms, [("item", start + 1), ("how", start - 1), ("how", start - 2)],
                         "each told, to be kept for the next")
        # Ctrl and the keys.
        note.window.winfo_containing = lambda x, y: None
        note.window.focus_force()
        note._pressed(SimpleNamespace(widget=note._areas["item"]))
        note.window.event_generate(f"<{MOD}-Key-0>")
        self.root.update()
        self.assertEqual((note.zooms["item"], note.zooms["how"]), (hn.ZOOM_START, hn.ZOOM_START - 2),
                         "Ctrl+0: the part pressed in, back as it opens")
        # Ctrl and the wheel.
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

    def test_what_was_handed_over_opened_again_from_the_note(self) -> None:
        """The user, 2026-11-08: "在to do help panel里重新打开deliverable的东西" - a page, a folder,
        a file, an email opened again from its side; an answer, read in the note, has none."""
        opened, why = [], [""]
        page = dict(NOTE, item={"kind": "page", "text": "https://example.org/tides"})
        note = hn.HandedNote(self.root, page, lambda text: text, "Segoe UI", 10,
                             on_open=lambda: (opened.append(1), why[0])[1])
        note.show()
        self.root.update()
        again = [one for one in self._all(note.window) if isinstance(one, tk.Label)
                 and str(one.cget("text")) == "Open the page"]
        self.assertEqual(len(again), 1)
        # ON A LINE OF ITS OWN, under what it is ("open XX放在email(item名)下面一行。单独成行").
        self.assertEqual(len(again[0].master.winfo_children()), 1, "nothing beside it")
        self._click(again[0])
        self.root.update()
        self.assertEqual(opened, [1])
        why[0] = "it is no longer there"
        self._click(again[0])
        self.root.update()
        self.assertIn("it is no longer there", str(note._copied.cget("text")), "said why not")
        note.close()
        answer = dict(NOTE, item={"kind": "answer", "text": "The tide turns at six."})
        note = hn.HandedNote(self.root, answer, lambda text: text, "Segoe UI", 10,
                             on_open=lambda: "")
        note.show()
        self.root.update()
        self.assertFalse([one for one in self._all(note.window) if isinstance(one, tk.Label)
                          and str(one.cget("text")).startswith("Open the")], "an answer is read here")
        note.close()

    def test_what_was_ticked_is_kept_and_ticked_again_when_opened_again(self) -> None:
        """The user, 2026-11-08: the checklist ticked, kept, and as it was the next time."""
        told = []
        note = hn.HandedNote(self.root, dict(NOTE, ticked=["The date: Wednesday the 4th"]),
                             lambda text: text, "Segoe UI", 10,
                             on_tick=lambda words, shown: told.append(words))
        note.show()
        self.root.update()
        self.assertEqual(note.ticked, [False, True], "as it was left")
        boxes = [one for one in self._all(note.window) if isinstance(one, tk.Label)
                 and str(one.cget("text")) in ("☐", "☑")]
        self.assertEqual([str(one.cget("text")) for one in boxes], ["☐", "☑"])
        boxes[0].event_generate("<Button-1>")
        self.root.update()
        self.assertEqual(told[-1], ["It answers: The talk & the date", "The date: Wednesday the 4th"])
        boxes[1].event_generate("<Button-1>")
        self.root.update()
        self.assertEqual(told[-1], ["It answers: The talk & the date"])
        note.close()

    def test_a_projects_panel_opens_each_thing_it_made_in_its_own(self) -> None:
        """The user, 2026-11-08: "project level to do instruction 要有每个item 打开的链接"."""
        opened = []
        whole = dict(NOTE, scope="project", items=[{"n": 0, "kind": "email", "title": "Re: The talk"},
                                                  {"n": 1, "kind": "file", "title": "Slides"}])
        note = hn.HandedNote(self.root, whole, lambda text: text, "Segoe UI", 10,
                             on_item=opened.append)
        note.show()
        self.root.update()
        links = [one for one in self._all(note.window) if isinstance(one, tk.Label)
                 and str(one.cget("text")).startswith("↗")]
        self.assertEqual([str(one.cget("text")) for one in links],
                         ["↗  Email: Re: The talk", "↗  File: Slides"])
        self._click(links[1])
        self.root.update()
        self.assertEqual(opened, [1])
        note.close()
        plain = hn.HandedNote(self.root, whole, lambda text: text, "Segoe UI", 10)
        plain.show()
        self.root.update()
        self.assertFalse([one for one in self._all(plain.window) if isinstance(one, tk.Label)
                          and str(one.cget("text")).startswith("↗")], "nowhere to open them: none")
        plain.close()

    def test_a_projects_panel_its_part_on_top_and_the_item_chosen_below(self) -> None:
        """The user, 2026-11-08: on top the project - its whole instructions, scrolled - and a row
        for each item: the row shows it below, its button opens it too."""
        asked = []
        report = "\n".join(f"Line {n} of what the work did for the harbour project." for n in range(60))
        whole = dict(NOTE, scope="project", run="run_tide", n=0,
                     item={"kind": "email", "text": "Dear Ada"},
                     project={"title": "Harbour project", "about": report},
                     items=[{"n": 0, "kind": "email", "title": "Re: The talk"},
                            {"n": 1, "kind": "file", "title": "Slides"},
                            {"n": 2, "kind": "answer", "title": "The tide times"}])
        note = hn.HandedNote(self.root, whole, lambda text: text, "Segoe UI", 10,
                             on_item=lambda n, opening: asked.append((n, opening)))
        note.show()
        self.root.update()
        self.assertEqual(note._about_text.get("1.0", "end-1c"), report, "whole, not cut")
        # THREE PARTS, EACH SCROLLED BY ITSELF (the user, 2026-11-08: "everything it made应该是个单独的
        # panel ... 分成上下3块"): the guide, what it made, the item - the guide no taller than its share.
        self.assertEqual(len(note._top_scrolls), 2, "the guide, and what it made, apart")
        canvas, area, _inner = note._top_scrolls[0]
        self.root.update()
        most = int(note.window.winfo_screenheight() * hn.GUIDE_SHARE)
        self.assertLessEqual(canvas.winfo_height(), most + 2)
        first = canvas.yview()[0]
        note._on_wheel(SimpleNamespace(delta=-120, num=0, state=0, widget=canvas))
        self.root.update()
        self.assertGreater(canvas.yview()[0], first, "the wheel over it scrolls it")
        made = note._top_scrolls[1][0]
        self.assertEqual(made.yview()[0], 0.0, "what it made, as it was")
        self.assertTrue(all(scrolled is not canvas for scrolled, _s, _p in note._panes),
                        "the item below scrolls apart")
        # ZOOMED OVER THEM, as the right-hand side ("也要支持ctrl +-, 滚轮之类的放大缩小").
        self.assertEqual(note._side_of(canvas), "guide")
        self.assertEqual(note._side_of(note._top_scrolls[1][0]), "made")
        # EACH OF THE FOUR ZOOMED BY ITSELF ("四个小panel是支持单独的 缩放").
        zoomed = []
        note.on_zoom = lambda side, step: zoomed.append((side, step))
        note.zoom_by(1, "guide")
        self.assertEqual(zoomed, [("guide", hn.ZOOM_START + 1)])
        self.assertEqual((note.zooms["made"], note.zooms["item"], note.zooms["how"]),
                         (hn.ZOOM_START,) * 3)
        note.zoom_by(-1, "guide")
        # ITS HEADER PRESSED, WHAT IT MADE FOLDS ("everything it made 这个是可以点击header collapose的").
        self.root.update()
        open_height = note._made_area.winfo_height()
        note._fold_made()
        self.root.update()
        self.assertTrue(note._made_folded)
        self.assertFalse(note._made_rows.winfo_ismapped())
        self.assertLess(note._made_area.winfo_height(), open_height)
        note._fold_made()
        self.root.update()
        self.assertFalse(note._made_folded)
        self.assertTrue(note._made_rows.winfo_ismapped())
        # THE ITEM BELOW SEEN WHOLE, ITS HEIGHT COUNTED (an item chosen showed only once dragged).
        self.root.update()
        bottom = note._middle
        self.assertGreater(bottom.winfo_height(), 60)
        self.assertLessEqual(bottom.winfo_rooty() + bottom.winfo_height(),
                             note.window.winfo_rooty() + note.window.winfo_height() + 2)
        labels = [one for one in self._all(note.window) if isinstance(one, tk.Label)]
        rows = [one for one in labels if "Slides" in str(one.cget("text"))
                or "Re: The talk" in str(one.cget("text")) or "tide times" in str(one.cget("text"))]
        self.assertEqual(len(rows), 3)
        self.assertTrue(str(rows[0].cget("text")).startswith("▸"), "the one shown below")
        self._click(rows[1])
        self.root.update()
        self.assertEqual(asked, [(1, False)], "the row: shown below, nothing opened")
        opens = [one for one in labels if str(one.cget("text")) == "Open the file"]
        self._click(opens[0])
        self.root.update()
        self.assertEqual(asked[-1], (1, True), "its button: opened, and shown below")
        self.assertFalse([one for one in labels if str(one.cget("text")) == "Open the answer"],
                         "an answer is read here: no button")
        self.root.update()
        old = note.window
        where = (old.winfo_x(), old.winfo_y(), old.winfo_width(), old.winfo_height())
        guide_part, made_part, old_middle = (note._top_scrolls[0][1], note._top_scrolls[1][1],
                                             note._middle)
        note.show_item(dict(NOTE, run="run_tide", n=1, title="Slides", how="Present them.",
                            check=["The dates are right"], ticked=["The dates are right"],
                            item={"kind": "file", "text": "slides.pptx"}))
        self.root.update()
        # NOTHING DRAWN AGAIN BUT THE ITEM (the user, 2026-11-08: "有没有完全没有refresh的方法"): the same
        # window, where it stood and as large; its guide and list the same; the item below put in
        # place of the one before.
        self.assertIs(note.window, old)
        self.assertEqual((old.winfo_x(), old.winfo_y(), old.winfo_width(), old.winfo_height()),
                         where)
        self.assertTrue(guide_part.winfo_exists() and made_part.winfo_exists())
        self.assertIs(note._top_scrolls[0][1], guide_part)
        self.assertFalse(old_middle.winfo_exists(), "the item before let go")
        self.assertTrue(note._middle.winfo_ismapped())
        self.assertGreater(note._middle.winfo_height(), 60)
        self.assertTrue(any("slides.pptx" in box.get("1.0", "end-1c") for box in note._texts),
                        "the item chosen, below")
        # EACH PART ITS OWN - AND + ("另外两个panel还没有+-").
        for part in (guide_part, made_part):
            marks = {str(one.cget("text")) for one in self._all(part) if isinstance(one, tk.Label)}
            self.assertLessEqual({"+", "−"}, marks)
        # WHAT IT MADE, A HEADER: no arrow, larger - and it stays where it is, above what scrolls
        # ("everything it made 是第二个panel的header, 不会随着下拉框被拉下去").
        head = note._made_head
        self.assertEqual(str(head.cget("text")), "Everything it made")
        made_canvas = note._top_scrolls[1][0]
        self.assertFalse(self._inside(head, made_canvas), "not in what scrolls")
        self.assertTrue(self._inside(head, made_part))
        self.assertEqual(note.note["n"], 1)
        self.assertEqual(note.note["project"]["title"], "Harbour project", "the top as it was")
        self.assertEqual(note.ticked, [True], "ticked as it was left")
        rows = [one for one in self._all(note.window) if isinstance(one, tk.Label)
                and str(one.cget("text")).startswith("▸")]
        self.assertIn("Slides", str(rows[0].cget("text")))
        note.close()

    def test_a_projects_own_checks_ticked_and_kept_as_the_projects(self) -> None:
        told = []
        whole = dict(NOTE, scope="project", run="run_tide", n=0, item={"kind": "email", "text": "x"},
                     project={"title": "Harbour", "about": "1. Send it.", "guided": True,
                              "check": ["The hall is booked", "Ada said yes"],
                              "ticked": ["Ada said yes"]},
                     items=[{"n": 0, "kind": "email", "title": "Re: The talk"}])
        note = hn.HandedNote(self.root, whole, lambda text: text, "Segoe UI", 10,
                             on_item=lambda *a: None,
                             on_tick=lambda words, shown: told.append((words, shown["n"])))
        note.show()
        self.root.update()
        texts = [str(one.cget("text")) for one in self._all(note.window) if isinstance(one, tk.Label)]
        self.assertIn("How to use it all", texts)
        boxes = [one for one in self._all(note.window) if isinstance(one, tk.Label)
                 and str(one.cget("text")) in ("☐", "☑")]
        self.assertEqual([str(one.cget("text")) for one in boxes[:2]], ["☐", "☑"], "as left")
        boxes[0].event_generate("<Button-1>")
        self.root.update()
        self.assertEqual(told[-1], (["The hall is booked", "Ada said yes"], "project"))
        note.close()

    @staticmethod
    def _inside(widget, area) -> bool:
        return str(widget) == str(area) or str(widget).startswith(str(area) + ".")

    def test_closed_it_is_gone_at_once(self) -> None:
        """The user, 2026-11-08: "点击叉掉之后要卡一秒才关掉，能让他即时关吗"."""
        note = self._note()
        window = note.window
        note.close()
        self.assertIsNone(note.window)
        self.assertEqual(window.state(), "withdrawn", "hidden the moment it is closed")
        time.sleep(hn.CLOSE_AFTER_MS / 1000 + 0.1)
        self.root.update()
        self.assertFalse(window.winfo_exists(), "its parts let go of after")

    def test_folded_its_square_as_large_as_its_cross(self) -> None:
        """The user, 2026-11-08: "最大化按钮没有对齐，大小也不对" - drawn as the chat's is: a box as wide as
        the ✕'s, as tall as its line, the mark in its middle, as wide as most of the ✕."""
        note = self._note()
        self.root.update()
        close, fold = note._close_button, note._fold_button
        glyph = int(self.root.tk.call("font", "measure", close.cget("font"), "✕"))

        def box_of_mark():
            return fold.bbox("all")
        note.toggle_folded()
        self.root.update()
        self.assertEqual(note._fold_mark, "square")
        left, top, right, bottom = box_of_mark()
        width, height = int(fold.cget("width")), int(fold.cget("height"))
        side = right - left
        self.assertAlmostEqual(side, round(glyph * 0.72), delta=3, msg="as wide as most of the ✕")
        self.assertAlmostEqual(left + right, width, delta=2, msg="in the middle, across")
        self.assertAlmostEqual(top + bottom, height, delta=2, msg="in the middle, up and down")
        self.assertEqual(fold.winfo_reqheight(), close.winfo_reqheight(), "one line, both")
        note.toggle_folded()
        self.root.update()
        self.assertEqual(note._fold_mark, "line")
        note.close()

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
        for widget in (note._fold_button, note._close_button):     # the fold mark drawn, the ✕
            self.assertTrue(widget.winfo_ismapped() and widget.winfo_width() > 1, widget)
        note.close()
        plain = self._note(dict(NOTE, work="Garden plan"))
        self.assertIn("Garden plan", self._texts(plain._bar_frame))
        plain.close()

    def test_folded_to_its_bar_and_opened_as_it_was(self) -> None:
        """The user, 2026-11-02: a way to minimize it - collapsed as Zoom's
        is, a bar left."""
        note = self._note(dict(NOTE, item=EMAIL))
        window = note.window
        self.assertEqual(note._fold_mark, "line", "its line beside the ✕")
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
        self.assertEqual(note._fold_mark, "square")
        note.unfold()
        self.root.update()
        self.assertFalse(note.folded)
        self.assertTrue(note._middle.winfo_ismapped())
        self.assertTrue(note._foot_frame.winfo_ismapped())
        self.assertEqual((window.winfo_width(), window.winfo_height()), before, "as it was")
        if words is not None:
            self.assertAlmostEqual(words.yview()[0], scrolled, places=3,
                                   msg="where it was scrolled to")
        self.assertEqual(note._fold_mark, "line")
        # A CLICK on its bar folds it, and again opens it; a drag only moves it (the user,
        # 2026-11-08); its bar of the body's ground, and a wider scroll bar.
        from types import SimpleNamespace
        self.assertEqual(str(note._bar_frame.cget("bg")), note.palette["bg"])
        self.assertEqual(str(note._bar_frame.cget("cursor")), "hand2")
        x, y = note._bar_frame.winfo_rootx() + 30, note._bar_frame.winfo_rooty() + 5
        note._grab(SimpleNamespace(x_root=x, y_root=y))
        note._move(SimpleNamespace(x_root=x + 40, y_root=y + 2))
        note._bar_released(SimpleNamespace(x_root=x + 40, y_root=y + 2))
        self.root.update()
        self.assertFalse(note.folded, "dragged: moved, not folded")
        note._grab(SimpleNamespace(x_root=x, y_root=y))
        note._bar_released(SimpleNamespace(x_root=x + 1, y_root=y))
        self.root.update()
        self.assertTrue(note.folded)
        note._grab(SimpleNamespace(x_root=x, y_root=y))
        note._bar_released(SimpleNamespace(x_root=x, y_root=y))
        self.root.update()
        self.assertFalse(note.folded)
        self.assertEqual(hn.SCROLL_WIDE, 14)
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

    def test_by_its_top_an_outline_while_dragged_and_put_there_let_go(self) -> None:
        """The user, 2026-11-09: "project instruction panel如果调整上边框巨卡" - moved as well as
        sized, all of it was drawn again each step (150-260 ms, measured)."""
        note = self._note(dict(NOTE, item=EMAIL))
        window = note.window
        # Low enough that 60 up is on the screen everywhere: a Mac keeps a window below its menu bar.
        window.geometry(f"+{window.winfo_x()}+300")
        self.root.update()
        x, y, height = window.winfo_x(), window.winfo_y(), window.winfo_height()
        note._edge_start(SimpleNamespace(x_root=x + 50, y_root=y), "n")
        for step in range(1, 11):
            note._edge_move(SimpleNamespace(x_root=x + 50, y_root=y - 6 * step))
            self.root.update()
        self.assertEqual((window.winfo_y(), window.winfo_height()), (y, height),
                         "not moved while it is dragged")
        self.assertEqual(len(note._ghost), 4, "its outline instead")
        self.assertEqual(note._ghost[0].winfo_y(), y - 60, "where it will be")
        note._resize_end()
        self.root.update()
        self.assertIsNone(note._ghost, "the outline gone")
        self.assertEqual(window.winfo_y(), y - 60)
        self.assertEqual(window.winfo_height(), height + 60, "put there at once, let go")
        note.close()

    def test_folded_its_bar_has_no_open_folder(self) -> None:
        """The user, 2026-11-09: "open folder这些按键也在最小化的header上"."""
        note = hn.HandedNote(self.root, dict(NOTE, item=EMAIL, many=True), lambda t: t, "Segoe UI",
                             10, on_folder=lambda: None)
        note.show()
        self.root.update()
        self.assertTrue(note._folder_button.winfo_ismapped())
        note.fold()
        self.root.update()
        self.assertFalse(note._folder_button.winfo_ismapped(), "folded: its name, – and ✕")
        note.unfold()
        self.root.update()
        self.assertTrue(note._folder_button.winfo_ismapped(), "opened: back")
        bar = note._bar_frame.pack_slaves()
        self.assertLess(bar.index(note._fold_button), bar.index(note._folder_button),
                        "in its place, left of –")
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

    def test_from_the_bus_to_the_screen_at_the_one_zoom(self) -> None:
        import queue
        from personalized_agent.ui.desktop import DesktopAgent
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(gc.collect)                    # on Tk's thread (TheNoteTests.tearDown)
        self.addCleanup(root.destroy)
        kept = {"popup_zoom": 2}
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
        self.assertEqual((note.zooms["item"], note.zooms["how"]), (hn.ZOOM_START + 2, hn.ZOOM_START + 2),
                         "opened at the zoom kept")
        note.zoom_by(1, "how")
        # KEPT AS THAT PART'S OWN (the user, 2026-11-08: "并且记住历史选择") - the popups' as they were.
        self.assertEqual(kept["popup_zoom"], 2)
        self.assertEqual(kept["handed_note_zooms"], {"how": hn.ZOOM_START + 3})
        self.assertEqual((note.zooms["item"], note.zooms["how"]), (hn.ZOOM_START + 2, hn.ZOOM_START + 3))
        DesktopAgent._show_handed_note(fake, dict(payload))
        root.update()
        self.assertEqual(fake._handed_note.zooms["how"], hn.ZOOM_START + 3, "opened as it was left")
        note = fake._handed_note
        note._save()
        self.assertEqual(saved, [("run_7", 1, EMAIL["text"])], "saved to its run and item")
        from personalized_agent.ui import sizes
        note.on_layout((700, 400), 0.4)
        self.assertEqual((kept["handed_note_size"], kept["handed_note_split"]),
                         (sizes.share(fake.root, (700, 400)), 0.4),
                         "its size, as a share of the screen, and its sides' kept for the next note")
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

    def test_folded_and_opened_again_the_list_on_top_comes_back(self) -> None:
        """The user, 2026-11-10: "在最小化之后他就不会显示Item list (只有这个Item的内容了"."""
        import queue
        from personalized_agent.ui.desktop import DesktopAgent
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(gc.collect)
        self.addCleanup(root.destroy)
        kept = {}
        config = SimpleNamespace(get=lambda key, default=None: kept.get(key, default),
                                 set=lambda key, value, internal=False: kept.update({key: value}))
        app = SimpleNamespace(work=SimpleNamespace(save_handed=lambda *a: {}),
                              db=SimpleNamespace(query_one=lambda *a: None, execute=lambda *a: None),
                              agenda_approved=lambda ident, how: {"kind": "replies"},
                              approval_item=lambda row: dict(NOTE, item=EMAIL, run=row["run"], n=0))
        fake = SimpleNamespace(queue=queue.Queue(), root=root, config=config, app=app,
                               _words=lambda text: text, _popup_font=lambda: ("Segoe UI", 10))
        rows = [{"id": f"agenda_{n}", "title": f"Reply {n}", "run": f"run_{n}", "n": 0,
                 "state": "ready", "answered": ""} for n in range(3)]
        DesktopAgent._show_handed_note(fake, dict(NOTE, item=EMAIL, run="run_0", n=0, scope="approval",
                                                  work="To approve", approval={"rows": rows},
                                                  approval_at=0))
        root.update()
        note = fake._handed_note
        [part] = note._stack_parts
        before = part.winfo_height()
        self.assertGreater(before, 48, "the list on top, shown")
        note.fold()
        root.update()
        note.unfold()
        root.update()
        self.assertGreaterEqual(part.winfo_height(), before - 2, "as tall again as it was")
        note.close()

    def test_a_question_in_the_approvals_panel_is_answered_in_a_line(self) -> None:
        """The user, 2026-11-10: "make the question clear" - asked in its row, answered there."""
        import queue
        from personalized_agent.ui.desktop import DesktopAgent
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(gc.collect)
        self.addCleanup(root.destroy)
        kept = {}
        config = SimpleNamespace(get=lambda key, default=None: kept.get(key, default),
                                 set=lambda key, value, internal=False: kept.update({key: value}))
        answered = []
        app = SimpleNamespace(
            work=SimpleNamespace(save_handed=lambda *a: {}),
            db=SimpleNamespace(query_one=lambda *a: None, execute=lambda *a: None),
            agenda_approved=lambda ident, how: answered.append((ident, how)) or {"how": "answered"},
            approval_item=lambda row: {"title": row["title"], "question": "Who is the call with?",
                                       "check": [], "item": {}})
        fake = SimpleNamespace(queue=queue.Queue(), root=root, config=config, app=app,
                               _words=lambda text: text, _popup_font=lambda: ("Segoe UI", 10))
        rows = [{"id": "agenda_q", "title": "Evening call on the garden chat", "run": "", "n": 0,
                 "state": "needs_answer", "answered": "",
                 "question": "Is the evening call with Wren Halloway, on Tuesday at 23:00?"}]
        DesktopAgent._show_handed_note(fake, dict(title="Evening call on the garden chat",
                                                  question="Who is the call with?", check=[],
                                                  item={}, scope="approval", work="To approve",
                                                  approval={"rows": rows}, approval_at=0))
        root.update()
        note = fake._handed_note
        row = [one for one in note._made_rows.winfo_children() if isinstance(one, tk.Frame)][0]
        frames = [one for one in row.winfo_children() if isinstance(one, tk.Frame)]
        words = [child.cget("text") for frame in frames for child in frame.winfo_children()
                 if isinstance(child, tk.Label)]
        self.assertIn("Is the evening call with Wren Halloway, on Tuesday at 23:00?", words)
        self.assertIn("Answer it below.", words)
        # "你得给我一个回答的窗口呀": under the question, a box to answer in.
        self.assertIsNotNone(note._answer_box, "a window to answer in, under the question")
        note._answer_box.insert("1.0", "With Wren, Tuesday 23:00")
        note._send_answer()
        for _ in range(200):
            try:
                kind, call = fake.queue.get(timeout=0.05)
            except queue.Empty:
                continue
            if kind == "call":
                call()
                root.update()
                break
        self.assertEqual(answered, [("agenda_q", "answer:With Wren, Tuesday 23:00")])
        self.assertTrue(rows[0]["answered"].startswith("answer:"), "said answered on its row")
        self.assertIsNone(note._answer_box, "answered: said so where the box was")
        fake._handed_note.close()

    def test_the_approvals_panel_its_answers_on_top_the_one_chosen_below(self) -> None:
        """The user, 2026-11-09: "每个审批session可以用类似project instruction的panel。但是现在最上面那个
        project level instruction可以删去，就一个是选项，一个是instruction进行审批"."""
        import queue
        from personalized_agent.ui.desktop import DesktopAgent
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(gc.collect)
        self.addCleanup(root.destroy)
        kept = {}
        config = SimpleNamespace(get=lambda key, default=None: kept.get(key, default),
                                 set=lambda key, value, internal=False: kept.update({key: value}))
        answered, shown = [], []
        app = SimpleNamespace(
            work=SimpleNamespace(save_handed=lambda *a: {}),
            db=SimpleNamespace(query_one=lambda *a: None, execute=lambda *a: None),
            agenda_approved=lambda ident, how: answered.append((ident, how)) or {"kind": "replies"},
            approval_item=lambda row: shown.append(row["id"]) or dict(NOTE, item=EMAIL, run=row["run"], n=0))
        fake = SimpleNamespace(queue=queue.Queue(), root=root, config=config, app=app,
                               _words=lambda text: text, _popup_font=lambda: ("Segoe UI", 10))
        rows = [{"id": "agenda_a", "title": "Reply to the seed club", "run": "run_a", "n": 0, "state": "ready",
                 "answered": ""},
                {"id": "agenda_b", "title": "Answer Wren Halloway", "run": "run_b", "n": 0, "state": "ready",
                 "answered": ""},
                {"id": "agenda_c", "title": "Book the orchard hall", "run": "", "n": 0, "state": "working",
                 "answered": ""}]
        DesktopAgent._show_handed_note(fake, dict(NOTE, item=EMAIL, run="run_a", n=0, scope="approval",
                                                  work="To approve, until 11:00",
                                                  approval={"rows": rows}, approval_at=0))
        root.update()
        note = fake._handed_note
        self.assertEqual(len(note._top_scrolls), 1, "no project's guide on top: the rows alone")
        def titles():
            return [[child.cget("text") for child in row.winfo_children()
                     if isinstance(child, tk.Label)][0]
                    for row in note._made_rows.winfo_children() if isinstance(row, tk.Frame)]
        # READY ON TOP, THE REST FOLDED (the user, 2026-11-10): the one still being worked on under
        # a line of its own, opened when pressed.
        self.assertEqual(titles(), ["▸  Reply to the seed club", "Answer Wren Halloway"])
        self.assertEqual(note._approval_fold.cget("text"), "▸  Not ready yet (1)")
        note._fold_approval_later()
        root.update()
        self.assertEqual(titles(),
                         ["▸  Reply to the seed club", "Answer Wren Halloway", "Book the orchard hall"])
        note._fold_approval_later()
        root.update()
        self.assertEqual(len(titles()), 2, "folded again")
        # The one-click answer first (the user, 2026-11-10: "要给我一个一键approve按钮").
        first_row = [row for row in note._made_rows.winfo_children() if isinstance(row, tk.Frame)][0]
        buttons = [child for frame in first_row.winfo_children() if isinstance(frame, tk.Frame)
                   for child in frame.winfo_children()]
        said = [str(one.cget("text")) for one in buttons if "text" in one.keys()]
        self.assertTrue(said and said[0] == "Approve - let it do it", said)

        def run_queued():
            for _ in range(200):
                try:
                    kind, call = fake.queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                if kind == "call":
                    call()
                    root.update()
                    return
            self.fail("nothing came back")
        note._answer(0, "as_it_was")
        run_queued()
        self.assertEqual(answered, [("agenda_a", "as_it_was")])
        self.assertEqual(rows[0]["answered"], "as_it_was")
        note._answer(0, "changed")
        self.assertEqual(len(answered), 1, "answered once")
        # CHOSEN BY ANY PART OF ITS ROW BUT ITS BUTTONS (the user, 2026-11-11: "approval的一行的非
        # button位置都能选中"): here the space beside its buttons, under its title.
        second_row = [row for row in note._made_rows.winfo_children() if isinstance(row, tk.Frame)][1]
        under = [frame for frame in second_row.winfo_children() if isinstance(frame, tk.Frame)][0]
        self.assertEqual(str(under.cget("cursor")), "hand2")
        root.update()
        under.event_generate("<ButtonPress-1>", x=2, y=2)
        under.event_generate("<ButtonRelease-1>", x=2, y=2)
        run_queued()
        self.assertEqual(shown, ["agenda_b"])
        self.assertEqual(fake._handed_note.note["run"], "run_b", "the one chosen, below")
        self.assertEqual(fake._handed_note.note["approval_at"], 1)
        fake._handed_note.close()

    def test_a_whole_projects_note_stands_apart_from_a_to_dos(self) -> None:
        """The user, 2026-11-08: a to-do's note gives way to the next to-do's; a whole project's,
        of another ground, only to the next whole project's."""
        import queue
        from personalized_agent.ui.desktop import DesktopAgent
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(gc.collect)
        self.addCleanup(root.destroy)
        kept = {}
        config = SimpleNamespace(get=lambda key, default=None: kept.get(key, default),
                                 set=lambda key, value, internal=False: kept.update({key: value}))
        fake = SimpleNamespace(queue=queue.Queue(), root=root, config=config,
                               app=SimpleNamespace(work=SimpleNamespace(save_handed=lambda *a: {})),
                               _words=lambda text: text, _popup_font=lambda: ("Segoe UI", 10))
        todo = dict(NOTE, item=EMAIL, run="run_1", n=0, scope="todo")
        whole = dict(NOTE, item=EMAIL, run="run_2", n=0, scope="project")
        DesktopAgent._show_handed_note(fake, whole)
        project_note = fake._handed_note
        self.assertEqual(project_note.palette["bg"], hn.PROJECT_PALETTE["bg"])
        DesktopAgent._show_handed_note(fake, todo)
        first_todo = fake._handed_note
        self.assertNotEqual(first_todo.palette["bg"], hn.PROJECT_PALETTE["bg"])
        DesktopAgent._show_handed_note(fake, todo)
        self.assertIsNone(first_todo.window, "the next to-do's takes the place of a to-do's")
        self.assertIsNotNone(project_note.window, "not of a whole project's")
        DesktopAgent._show_handed_note(fake, whole)
        self.assertIsNone(project_note.window, "a whole project's gives way to the next one's")
        for one in fake._handed_notes.values():
            one.close()


if __name__ == "__main__":
    unittest.main()
