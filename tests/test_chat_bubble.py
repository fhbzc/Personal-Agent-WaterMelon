"""A CONVERSATION IN A BUBBLE BESIDE THE AVATAR (the user, 2026-11-01): one
click opens it; what is written is sent on a thread of its own and the
answer written in; the conversation is the chat page's; its size is the
user's, dragged by any edge, and kept. Everything said here is made up."""

from __future__ import annotations

import gc
import queue
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.destroy()
    HAVE_TK = True
except Exception:                                  # pragma: no cover - no display
    HAVE_TK = False

from personalized_agent.ui import chat_bubble as cb


MOD = "Command" if sys.platform == "darwin" else "Control"


class BesideTheAvatarTests(unittest.TestCase):
    """The user, 2026-11-01: a bubble comes up beside the avatar."""

    def test_at_its_left_level_with_its_middle(self) -> None:
        x, y = cb.beside((1500, 700, 120, 120), (340, 200), (1920, 1080))
        self.assertEqual(x, 1500 - 340 - cb.GAP)
        self.assertEqual(y + 100, 760, "its middle at the avatar's")

    def test_at_its_right_where_the_left_has_no_room(self) -> None:
        x, _y = cb.beside((40, 500, 120, 120), (340, 200), (1920, 1080))
        self.assertEqual(x, 40 + 120 + cb.GAP)

    def test_always_on_the_screen(self) -> None:
        x, y = cb.beside((1500, 1000, 120, 120), (340, 400), (1920, 1080))
        self.assertLessEqual(y + 400, 1080 - cb.MARGIN)
        x, y = cb.beside((1500, -50, 120, 120), (340, 400), (1920, 1080))
        self.assertGreaterEqual(y, cb.MARGIN)

    def test_above_the_taskbar_however_tall_it_is(self) -> None:
        """The user, 2026-11-02: its buttons could not be pressed - under
        the taskbar. Kept in the part of the screen windows may use."""
        _x, y = cb.beside((1732, 832, 128, 128), (400, 360), (1920, 1080),
                          area=(0, 0, 1920, 1000))
        self.assertLessEqual(y + 360, 1000 - cb.MARGIN)


@unittest.skipUnless(HAVE_TK, "no display for Tk here")
class TheNotificationBubbleOnTheScreenTests(unittest.TestCase):
    """The user, 2026-11-02: "notification是不是太靠下了，我都不能点击button
    了 (而且notification无法移动)"."""

    def setUp(self) -> None:
        import collections
        from personalized_agent.ui import desktop
        self.root = tk.Tk()
        self.root.withdraw()
        root = self.root

        class Harness(desktop.DesktopAgent):
            def __init__(self) -> None:              # no super().__init__: no avatar
                self.root = root
                self._bubble_queue = collections.deque()
                self._bubble, self._bubble_count, self._showing = None, None, None
                self.app = SimpleNamespace(REMIND_LATER_MINUTES=30)

            def _popup_font(self):
                return ("Segoe UI", 12)

            def _words(self, text):
                return text

            def _fill(self, text, *args):
                return text.format(*args)

            def _avatar_box(self):
                return (1732, 832, 128, 128)        # low on the right, as the user's

            def _options_for(self, _question):
                return [{"key": "", "label": "Got it", "primary": True}]
        self.agent = Harness()

    def tearDown(self) -> None:
        self.root.destroy()
        del self.root, self.agent
        gc.collect()

    def test_all_of_it_above_the_taskbar_and_dragged_by_its_words(self) -> None:
        area = (0, 0, 1920, 1000)
        with mock.patch("personalized_agent.ui.chat_bubble.work_area", return_value=area):
            self.agent.notify_bubble(
                "Delegated work did not start: The garden plan could not start because "
                "its folder is in use - open it, change it and start it again.",
                notification_id="n1", template="reminder")
        bubble = self.agent._bubble
        self.root.update()
        self.assertGreater(bubble.winfo_height(), 200, "its whole card, buttons and all")
        self.assertLessEqual(bubble.winfo_y() + bubble.winfo_height(), area[3],
                             "its buttons above the taskbar")
        card = bubble.winfo_children()[0]
        words = next(one for one in card.winfo_children() if isinstance(one, tk.Frame))
        x0, y0 = bubble.winfo_x(), bubble.winfo_y()
        words.event_generate("<ButtonPress-1>", x=5, y=5, rootx=x0 + 30, rooty=y0 + 60)
        words.event_generate("<B1-Motion>", x=5, y=5, rootx=x0 - 170, rooty=y0 - 40)
        self.root.update()
        self.assertEqual((bubble.winfo_x(), bubble.winfo_y()), (x0 - 200, y0 - 100), "moved")
        # Its buttons are pressed, never dragged.
        got_it = next(one for one in self._all(card) if isinstance(one, tk.Label)
                      and str(one.cget("text")).startswith("Got it"))
        self.assertNotIn("grab", str(got_it.bind("<B1-Motion>")) or "")
        self.assertEqual(got_it.bind("<B1-Motion>"), "")

    def _texts(self):
        return [one for one in self._all(self.agent._bubble) if isinstance(one, tk.Text)]

    def test_its_title_and_words_can_be_selected_and_copied_never_dragged(self) -> None:
        self.agent.notify_bubble("High water: The gauge reads 3.2 metres at noon, and rising "
                                 "through the afternoon along the whole of the north coast.",
                                 notification_id="n1", template="passive")
        self.root.update()
        title, words = self._texts()
        self.assertEqual(title.get("1.0", "end-1c"), "High water")
        self.assertIn("north coast.", words.get("1.0", "end-1c"))
        self.assertEqual(str(words.cget("state")), "disabled", "read, not edited")
        self.assertEqual(words.bind("<B1-Motion>"), "", "dragging selects, it moves nothing")
        self.assertGreater(int(words.cget("height")), 1, "all of its lines show")
        self.assertTrue(words.bind("<Button-3>"), "copied from the right button")
        from personalized_agent.ui.on_top import copy_to_clipboard, selected_words
        words.tag_add("sel", "1.0", "1.3")
        self.assertEqual(selected_words(words), "The")
        copy_to_clipboard(words, selected_words(words))
        self.assertEqual(self.root.clipboard_get(), "The")

    def test_it_says_why_it_is_told(self) -> None:
        self.agent.notify_bubble("High water: At noon", notification_id="n1", template="passive",
                                 reason="You opened the tide tables.")
        self.root.update()
        said = [one.get("1.0", "end-1c") for one in self._texts()]
        self.assertIn("Why: You opened the tide tables.", said)

    def test_with_no_reason_nothing_is_said_of_it(self) -> None:
        self.agent.notify_bubble("High water: At noon", notification_id="n1", template="passive")
        self.root.update()
        self.assertFalse(any("Why:" in one.get("1.0", "end-1c") for one in self._texts()))

    def test_lines_are_counted_by_the_font(self) -> None:
        from personalized_agent.ui.desktop import lines_of
        by_eight = lambda piece: 8 * len(piece)      # noqa: E731 - a font of even width
        self.assertEqual(lines_of("", by_eight, 80), 1)
        self.assertEqual(lines_of("one two", by_eight, 80), 1)
        self.assertEqual(lines_of("aaaa bbbb cccc dddd", by_eight, 80), 2)
        self.assertEqual(lines_of("one\ntwo", by_eight, 80), 2)
        self.assertEqual(lines_of("潮" * 25, by_eight, 80), 3, "wrapped with no spaces")
        self.assertEqual(lines_of("x" * 25, by_eight, 80), 3, "one word wider than the line")

    def test_a_word_written_under_it_goes_and_it_closes(self) -> None:
        told = []
        self.agent.app.notification_chat = lambda ident, said: told.append((ident, said))
        self.agent.notify_bubble("High water: At noon", notification_id="n1", template="passive")
        self.root.update()
        box = next(one for one in self._all(self.agent._bubble) if isinstance(one, tk.Entry))
        box.delete(0, "end")
        box.insert(0, "Not on weekends, please")
        box.focus_force()
        self.root.update()
        box.event_generate("<Return>")
        self.root.update()
        import time
        time.sleep(0.1)
        self.assertIsNone(self.agent._bubble, "it closes")
        self.assertEqual(told, [("n1", "Not on weekends, please")])

    def test_the_hint_alone_is_not_sent(self) -> None:
        told = []
        self.agent.app.notification_chat = lambda *one: told.append(one)
        self.agent.notify_bubble("High water: At noon", notification_id="n1", template="passive")
        self.root.update()
        box = next(one for one in self._all(self.agent._bubble) if isinstance(one, tk.Entry))
        box.focus_force()
        self.root.update()
        box.event_generate("<Return>")
        self.root.update()
        self.assertEqual(told, [])
        self.assertIsNotNone(self.agent._bubble)

    def test_a_reply_in_a_notification_opens_in_the_chat(self) -> None:
        opened = []
        self.agent.open_chat = lambda: opened.append(self.agent._conversation_id)
        self.agent.app.notification_got_it = lambda _ident: None
        self.agent.notify_bubble("Reply from your chat: On the calendar", notification_id="n1",
                                 template="passive", conversation="conv_9")
        self.root.update()
        button = next(one for one in self._all(self.agent._bubble) if isinstance(one, tk.Label)
                      and str(one.cget("text")) == "Read the whole reply")
        self.assertTrue(button)
        self.agent._bubble_read_reply(self.agent._showing)
        self.assertEqual(opened, ["conv_9"])

    def _all(self, widget):
        out = []
        for child in widget.winfo_children():
            out += [child] + self._all(child)
        return out


@unittest.skipUnless(HAVE_TK, "no display for Tk here")
class TheBubbleTests(unittest.TestCase):

    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.posted = queue.Queue()

    def tearDown(self) -> None:
        self.root.destroy()
        del self.root
        gc.collect()                       # on Tk's thread (test_handed_note)

    def _settle(self, seconds=1.0):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            try:
                while True:
                    self.posted.get_nowait()()
            except queue.Empty:
                pass
            self.root.update()
            time.sleep(0.02)

    def _bubble(self, send, **more):
        bubble = cb.ChatBubble(self.root, name="Fern", family="Segoe UI", size=10, send=send,
                               post=self.posted.put, open_full=lambda c: None, **more)
        bubble.show(near=(1200, 800))
        self.root.update()
        return bubble

    def test_it_says_what_goes_with_a_message_and_it_can_be_taken_off(self) -> None:
        front = {"title": "Tide tables - Chrome", "address": "https://example.com/tides"}
        bubble = self._bubble(lambda *_a, **_k: {}, front=lambda: dict(front))
        self._settle(0.3)
        said = [one.cget("text") for one in bubble.front_row.winfo_children()]
        self.assertIn("Tide tables - Chrome (https://example.com/tides)", said[0])
        self.assertTrue(bubble.front_row.winfo_ismapped())
        bubble._without_front("Tide tables - Chrome")
        self.assertEqual(bubble.front_off, "Tide tables - Chrome")
        self.assertFalse(bubble.front_row.winfo_children())
        bubble._draw_front(dict(front))
        self.assertFalse(bubble.front_row.winfo_children(), "the same window: still off")
        bubble._draw_front({"title": "Notes - Word"})
        self.assertEqual(bubble.front_off, "", "another window: told again")
        self.assertTrue(bubble.front_row.winfo_children())

    def test_what_is_on_the_screen_is_right_under_its_head_whatever_came_first(self) -> None:
        front = {"title": "Tide tables - Chrome", "address": "https://example.com/tides"}
        bubble = self._bubble(lambda *_a, **_k: {}, front=lambda: dict(front))
        self._settle(0.3)
        place = lambda: bubble.card.pack_slaves().index(bubble.front_row)
        self.assertEqual(place(), 1, "before anything was said")
        bubble._write("You", "you", "Hello")
        self.root.update()
        self.assertEqual(place(), 1, "with something said under it")
        self.assertLess(place(), bubble.card.pack_slaves().index(bubble.said_box))
        bubble._draw_front({"title": ""})
        self.assertNotIn(bubble.front_row, bubble.card.pack_slaves())
        bubble._draw_front({"title": "Notes - Word"})
        self.assertEqual(place(), 1, "come back when something was already said")

    def test_on_a_bar_it_is_not_there_and_back_it_is(self) -> None:
        front = {"title": "Tide tables - Chrome"}
        bubble = self._bubble(lambda *_a, **_k: {}, front=lambda: dict(front))
        self._settle(0.3)
        bubble.minimize()
        self.root.update()
        self.assertNotIn(bubble.front_row, bubble.card.pack_slaves())
        bubble.expand()
        self.root.update()
        self.assertEqual(bubble.card.pack_slaves().index(bubble.front_row), 1)

    def test_the_box_to_write_in_is_always_there(self) -> None:
        """At large words in a window the user sized, what is on the screen over two lines and the
        hint over two, it was squeezed to nothing, under an empty space."""
        front = {"title": "A window with a long title, as a terminal has - " * 3}
        bubble = self._bubble(lambda *_a, **_k: {}, front=lambda: dict(front), kept_size=(420, 240))
        self._settle(0.3)
        bubble._draw_front(dict(front))
        self.root.update()
        for _ in range(3):
            self.assertTrue(bubble.row.winfo_ismapped())
            self.assertGreaterEqual(bubble.row.winfo_height(), bubble.row.winfo_reqheight())
            self.assertLessEqual(bubble.row.winfo_y() + bubble.row.winfo_height(),
                                 bubble.card.winfo_height())
            bubble._write("You", "you", "A made-up question " * 8)
            self.root.update()
        bubble.minimize()
        self.root.update()
        bubble.expand()
        self.root.update()
        self.assertTrue(bubble.row.winfo_ismapped(), "and after a bar")
        self.assertGreaterEqual(bubble.row.winfo_height(), bubble.row.winfo_reqheight())

    def test_what_was_not_sent_is_kept_when_it_is_closed(self) -> None:
        kept = []
        bubble = self._bubble(lambda *_a, **_k: {}, on_draft=kept.append)
        bubble.box.insert("1.0", "Half a thought")
        bubble.close()
        self.assertEqual(kept[-1], "Half a thought")

    def test_it_is_in_the_box_when_it_is_opened_again(self) -> None:
        bubble = self._bubble(lambda *_a, **_k: {}, draft="Half a thought")
        self.assertEqual(bubble.box.get("1.0", "end-1c"), "Half a thought")
        bubble.close()

    def test_several_lines_show(self) -> None:
        bubble = self._bubble(lambda *_a, **_k: {}, draft="One\nTwo\nThree")
        self._settle(0.1)
        self.assertEqual(int(bubble.box.cget("height")), 3)
        bubble.close()

    def test_it_is_kept_as_it_is_typed_for_a_crash(self) -> None:
        kept = []
        bubble = self._bubble(lambda *_a, **_k: {}, on_draft=kept.append)
        bubble.box.insert("1.0", "Hal")
        bubble._draft_changed()
        bubble.box.insert("end-1c", "f")
        bubble._draft_changed()
        self.assertEqual(kept, [], "not at each key")
        self._settle(0.6)
        self.assertEqual(kept, ["Half"], "once, after the typing stopped")
        bubble.close()

    def test_sent_it_is_a_draft_no_more(self) -> None:
        kept = []
        bubble = self._bubble(lambda *_a, **_k: {"answer": "Yes."}, on_draft=kept.append)
        bubble.box.insert("1.0", "Book the room")
        bubble.submit()
        self.assertEqual(kept[-1], "")
        self._settle(0.2)
        bubble.close()
        self.assertEqual(kept[-1], "")

    def test_a_reply_after_it_closed_is_told_once(self) -> None:
        go, late = threading.Event(), []

        def send(_text, _conversation, _files=None, _mentions=None, on_text=None):
            go.wait(5)
            return {"answer": "Booked for Friday.", "conversation_id": "conv_1"}
        bubble = self._bubble(send, streams=True, on_late=lambda *said: late.append(said))
        bubble.box.insert("1.0", "Book the room")
        bubble.submit()
        self._settle(0.1)
        bubble.close()
        go.set()
        self._settle(0.5)
        self.assertEqual(late, [("Booked for Friday.", "conv_1")])

    def test_a_reply_read_in_the_open_bubble_is_not_told_again(self) -> None:
        late = []
        bubble = self._bubble(lambda *_a, **_k: {"answer": "Booked.", "conversation_id": "c"},
                              streams=True, on_late=lambda *said: late.append(said))
        bubble.box.insert("1.0", "Book the room")
        bubble.submit()
        self._settle(0.4)
        bubble.close()
        self._settle(0.1)
        self.assertEqual(late, [])

    def test_the_full_agents_answer_is_told_and_the_quick_one_only_if_unread(self) -> None:
        late = []
        bubble = self._bubble(lambda *_a, **_k: {}, on_late=lambda *said: late.append(said))
        bubble.close()
        bubble._fold_done({"turn": "t", "quick": "On it", "conversation": "c"},
                          {"answer": "It is on the calendar."})
        bubble._fold_done({"turn": "t", "quick": "On it", "conversation": "c"},
                          {"answer": "", "nothing": True})
        bubble._fold_done({"turn": "t", "quick": "On it", "conversation": "c", "seen": True},
                          {"answer": "", "nothing": True})
        self.assertEqual(late, [("It is on the calendar.", "c"), ("On it", "c")])

    def test_closed_while_the_model_works_the_work_goes_on_to_its_end(self) -> None:
        """THE USER, 2026-11-04: once the model is called the turn is said in full,
        the chat closed or not."""
        go, finished, told = threading.Event(), [], []

        def send(_text, _conversation, _files=None, _mentions=None, on_text=None):
            go.wait(5)
            on_text("Booked")                # the reply so far, to a window gone
            finished.append("the whole turn")
            return {"answer": "Booked for Friday.", "conversation_id": "conv_1"}
        bubble = self._bubble(send, streams=True, on_conversation=told.append)
        bubble.box.insert("1.0", "Book the room")
        bubble.submit()
        self._settle(0.1)
        bubble.close()
        self.assertFalse(bubble.is_open)
        self.assertEqual(finished, [], "closed before the model was done")
        go.set()
        self._settle(0.5)
        self.assertEqual(finished, ["the whole turn"])
        self.assertEqual(told, [], "it does not reach into the next conversation")
        self.assertFalse(bubble.waiting)

    def test_sent_apart_and_answered_in_the_same_conversation(self) -> None:
        asked = []

        def send(text, conversation):
            asked.append((text, conversation, threading.current_thread().name))
            time.sleep(0.2)
            return {"answer": "Booked for Friday at 10:00.", "conversation_id": "conv_1"}
        told = []
        bubble = self._bubble(send, on_conversation=told.append)
        bubble.box.insert("1.0", "Book the room")
        began = time.monotonic()
        bubble.submit()
        self.assertLess(time.monotonic() - began, 0.1, "the avatar is not held up")
        self.assertTrue(bubble.waiting)
        self.assertIn("Thinking…", bubble.said.get("1.0", "end-1c"))
        self._settle(1.0)
        said = bubble.said.get("1.0", "end-1c")
        self.assertEqual(said, "You\nBook the room\nFern\nBooked for Friday at 10:00.")
        self.assertEqual(asked[0][:2], ("Book the room", None))
        self.assertEqual(asked[0][2], "chat-bubble", "on a thread of its own")
        self.assertEqual((bubble.conversation_id, told), ("conv_1", ["conv_1"]))
        bubble.box.insert("1.0", "And Monday?")
        bubble.submit()
        self._settle(1.0)
        self.assertEqual(asked[1][1], "conv_1", "the same conversation")
        bubble.close()

    def test_enter_sends_shift_enter_does_not_and_empty_is_nothing(self) -> None:
        sent = []
        bubble = self._bubble(lambda text, c: sent.append(text) or {"answer": "ok"})
        self.assertEqual(bubble._enter(SimpleNamespace(state=0x0001)), "", "a new line")
        bubble.submit()
        self._settle(0.3)
        self.assertEqual(sent, [], "nothing written, nothing sent")
        bubble.box.insert("1.0", "Hi")
        self.assertEqual(bubble._enter(SimpleNamespace(state=0)), "break")
        self._settle(0.5)
        self.assertEqual(sent, ["Hi"])
        bubble.close()

    def test_a_file_attached_and_sent_with_or_without_words(self) -> None:
        # The user, 2026-11-04: the bubble too.
        asked = []

        class Files:
            def add_paths(self, paths):
                return {"files": [{"id": "chatfile_1", "name": "notes.txt", "picture": False}],
                        "skipped": []}

            def from_clipboard(self, with_text):
                asked.append(("clipboard", with_text))
                return {"files": [], "skipped": []}

        def send(text, conversation, files=None):
            asked.append((text, files))
            return {"answer": "Seen.", "conversation_id": "c1"}
        bubble = self._bubble(send, files=Files())
        with mock.patch("tkinter.filedialog.askopenfilenames", return_value=("x/notes.txt",)):
            bubble._pick()
        self._settle(0.2)
        self.assertEqual([one["name"] for one in bubble.attached], ["notes.txt"])
        self.assertTrue(bubble.files_row.winfo_ismapped(), "shown over the box")
        bubble.submit()
        self._settle(0.3)
        self.assertEqual(asked, [("", ["chatfile_1"])], "a file alone is a message")
        self.assertIn("[notes.txt]", bubble.said.get("1.0", "end"))
        self.assertEqual(bubble.attached, [])
        self.assertFalse(bubble.files_row.winfo_ismapped())
        bubble.close()

    def _with_a_picture(self, send):
        from PIL import Image

        class Files:
            def add_paths(self, paths):
                return {"files": [{"id": "chatfile_1", "name": "bed.png", "picture": True}],
                        "skipped": []}

            def thumbnail(self, ident, side):
                return Image.new("RGBA", (side, side), (200, 30, 30, 255))
        bubble = self._bubble(send, files=Files())
        with mock.patch("tkinter.filedialog.askopenfilenames", return_value=("x/bed.png",)):
            bubble._pick()
        self._settle(0.2)
        return bubble

    def _drop_data(self, path):
        """A data object as the system hands one over, holding a file."""
        import ctypes
        import struct
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        data = struct.pack("<IiiII", 20, 0, 0, 0, 1) + (path + "\0\0").encode("utf-16-le")
        drop = kernel32.GlobalAlloc(0x0042, len(data))
        ctypes.memmove(kernel32.GlobalLock(drop), data, len(data))
        kernel32.GlobalUnlock(drop)
        return drop

    @unittest.skipUnless(sys.platform.startswith("win"), "the system's own message, on Windows")
    def test_where_ole_cannot_a_file_let_go_is_still_taken(self) -> None:
        import ctypes
        from ctypes import wintypes
        from personalized_agent.ui import file_drop
        taken = []

        class Files:
            def add_paths(self, paths):
                taken.extend(paths)
                return {"files": [{"id": "chatfile_1", "name": "notes.txt", "picture": False}],
                        "skipped": []}
        with mock.patch.object(file_drop, "_ole", return_value=False):
            bubble = self._bubble(lambda text, conversation: {}, files=Files())
        self.assertIsNotNone(bubble.clip, "and + is there as well")
        user32 = ctypes.windll.user32
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        path = "C:\\made\\up\\notes.txt"
        user32.PostMessageW(user32.GetAncestor(bubble.window.winfo_id(), 2), 0x0233,
                            self._drop_data(path), 0)
        self._settle(0.4)
        self.assertEqual(taken, [path])
        self.assertEqual([one["name"] for one in bubble.attached], ["notes.txt"])
        bubble.close()

    @unittest.skipUnless(sys.platform.startswith("win"), "the system's own calls, on Windows")
    def test_a_file_over_it_covers_it_and_let_go_is_attached(self) -> None:
        # The calls the system makes of a drop target, made of the real one -
        # with a data object written out as the system's is.
        import ctypes
        from ctypes import wintypes
        from personalized_agent.ui import file_drop
        taken = []

        class Files:
            def add_paths(self, paths):
                taken.extend(paths)
                return {"files": [{"id": "chatfile_1", "name": "notes.txt", "picture": False}],
                        "skipped": []}
        bubble = self._bubble(lambda text, conversation: {}, files=Files())
        user32 = ctypes.windll.user32
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        handle = int(user32.GetAncestor(bubble.window.winfo_id(), 2))
        kept = file_drop._held[handle]
        self.assertEqual(kept[0], "ole")
        calls = kept[1]
        path = "C:\\made\\up\\notes.txt"
        pointer = ctypes.c_void_p

        class Medium(ctypes.Structure):
            _fields_ = [("medium", wintypes.DWORD), ("handle", pointer), ("release", pointer)]

        def get(_this, _format, medium):
            one = ctypes.cast(medium, ctypes.POINTER(Medium))[0]
            one.medium, one.handle, one.release = 1, self._drop_data(path), None
            return 0
        get_call = ctypes.WINFUNCTYPE(ctypes.c_long, pointer, pointer, pointer)(get)
        ask_call = ctypes.WINFUNCTYPE(ctypes.c_long, pointer, pointer)(lambda _this, _format: 0)
        table = (pointer * 6)(None, None, None, ctypes.cast(get_call, pointer), None,
                              ctypes.cast(ask_call, pointer))
        data = (pointer * 1)(ctypes.cast(table, pointer))
        thing = ctypes.cast(data, pointer)
        effect = wintypes.DWORD(0)
        calls[3](None, thing, 0, 0, ctypes.byref(effect))
        self.assertEqual(effect.value, 1, "it will take it")
        self._settle(0.1)
        self.assertIsNotNone(bubble.drop_cover, "covered the moment it is over")
        self.assertGreater(bubble.drop_cover.winfo_height(), 60)
        calls[5](None)
        self._settle(0.1)
        self.assertIsNone(bubble.drop_cover, "gone away, uncovered")
        calls[3](None, thing, 0, 0, ctypes.byref(effect))
        calls[6](None, thing, 0, 0, ctypes.byref(effect))
        self._settle(0.3)
        self.assertIsNone(bubble.drop_cover)
        self.assertEqual(taken, [path])
        self.assertEqual([one["name"] for one in bubble.attached], ["notes.txt"])
        bubble.close()
        self.assertNotIn(handle, file_drop._held, "let go of with the window")

    def test_on_a_mac_a_clear_view_takes_what_is_dragged(self) -> None:
        # AppKit written out as far as this needs: the real one is on a Mac.
        from personalized_agent.ui import file_drop

        class View:
            def __init__(self):
                self.kinds, self.gone = [], False

            @classmethod
            def alloc(cls):
                return cls()

            def initWithFrame_(self, _frame):
                return self

            def setAutoresizingMask_(self, _mask):
                pass

            def registerForDraggedTypes_(self, kinds):
                self.kinds = kinds

            def unregisterDraggedTypes(self):
                self.kinds = []

            def removeFromSuperview(self):
                self.gone = True

        size = SimpleNamespace(width=380, height=120)
        added = []
        content = SimpleNamespace(bounds=lambda: SimpleNamespace(size=size), addSubview_=added.append)
        native = SimpleNamespace(title=lambda: "tk", contentView=lambda: content)
        kit = SimpleNamespace(
            NSView=View, NSURL="url", NSDragOperationCopy=1, NSViewWidthSizable=2, NSViewHeightSizable=16,
            NSPasteboardTypeFileURL="public.file-url",
            NSApplication=SimpleNamespace(sharedApplication=lambda: SimpleNamespace(windows=lambda: [native])))
        window = SimpleNamespace(update_idletasks=lambda: None, title=lambda: "tk",
                                 winfo_width=lambda: 380, winfo_height=lambda: 120)
        told, overs = [], []
        with mock.patch.dict(sys.modules, {"AppKit": kit}), \
                mock.patch.object(file_drop, "_mac_view", None), \
                mock.patch.object(sys, "platform", "darwin"):
            self.assertTrue(file_drop.accept_files(window, told.append, overs.append))
            view = added[0]
            self.assertEqual(view.kinds, ["public.file-url"])
            self.assertIsNone(view.hitTest_(None), "the pointer goes through to Tk")
            self.assertEqual(view.draggingEntered_(None), 1)
            view.draggingExited_(None)
            self.assertEqual(overs, [True, False])
            board = SimpleNamespace(readObjectsForClasses_options_=lambda classes, options: [
                SimpleNamespace(path=lambda: "/made/up/notes.txt")])
            self.assertTrue(view.performDragOperation_(
                SimpleNamespace(draggingPasteboard=lambda: board)))
            self.assertEqual(told, [["/made/up/notes.txt"]])
            file_drop.let_go(window)
            self.assertTrue(view.gone)

    def test_the_reply_is_shown_as_it_is_written_and_the_whole_takes_its_place(self) -> None:
        go = threading.Event()

        def send(text, conversation, files, mentions, on_text=None):
            on_text("The first ")
            go.wait(3)
            return {"answer": "The first words and the rest.", "conversation_id": "c1"}
        bubble = self._bubble(send, streams=True)
        bubble.box.insert("end", "Tell me")
        bubble.submit()
        self._settle(0.3)
        self.assertIn("The first", bubble.said.get("1.0", "end"), "before it is finished")
        self.assertNotIn("Thinking", bubble.said.get("1.0", "end"))
        go.set()
        self._settle(0.3)
        said = bubble.said.get("1.0", "end")
        self.assertIn("The first words and the rest.", said)
        self.assertEqual(said.count("The first"), 1, "once")
        bubble.close()

    def _two_speeds(self, done):
        finish = []

        def turn(text, conversation=None, files=None, mentions=None, on_done=None):
            if text is None:
                return True
            finish.append(lambda: on_done(done))
            return {"answer": "Looking into it, more below.", "layer": "fast", "turn": "turn_1",
                    "conversation_id": "c1", "message_id": "m1"}
        bubble = self._bubble(lambda text, conversation: {}, turn=turn)
        bubble.box.insert("end", "What is on today?")
        bubble.submit()
        self._settle(0.3)
        return bubble, finish

    def test_the_quick_reply_at_once_the_full_one_folded_under_it(self) -> None:
        bubble, finish = self._two_speeds({"state": "done", "answer": "Nothing today.", "kind": "more"})
        said = bubble.said
        self.assertIn("Looking into it, more below.", said.get("1.0", "end"))
        self.assertTrue(said.tag_ranges("fast"), "in its colour")
        self.assertFalse(bubble.waiting, "the next message may be written at once")
        finish[0]()
        self._settle(0.2)
        body = next(one for one in said.tag_names() if one.startswith("fold_body"))
        head = body.replace("body", "head")
        self.assertIn("More detail", said.get(*said.tag_ranges(head)))
        self.assertTrue(said.compare(said.tag_ranges(head)[0], ">=", said.tag_ranges("fast")[1]),
                        "under the quick reply")
        self.assertTrue(said.get(*said.tag_ranges(head)).startswith("\n"), "on a line of its own")
        self.assertEqual(said.get(*said.tag_ranges(body)).strip(), "Nothing today.", "whole")
        self.assertEqual(int(said.tag_cget(body, "elide")), 1, "folded")
        bubble._fold_toggle(body)
        self.assertEqual(int(said.tag_cget(body, "elide")), 0, "opened")
        bubble._fold_toggle(body)
        self.assertEqual(int(said.tag_cget(body, "elide")), 1, "and folded again")
        bubble.close()

    def test_nothing_to_add_leaves_nothing_behind(self) -> None:
        bubble, finish = self._two_speeds({"state": "done", "answer": "", "nothing": True})
        self.assertIn("Looking into it…", bubble.said.get("1.0", "end"))
        finish[0]()
        self._settle(0.2)
        self.assertNotIn("Looking into it…", bubble.said.get("1.0", "end"))
        bubble.close()

    def test_a_full_agent_that_answers_before_its_line_is_drawn(self) -> None:
        fold = {"turn": "t"}
        bubble = self._bubble(lambda text, conversation: {})
        bubble._fold_done(fold, {"answer": "Quick work.", "kind": "correction"})
        bubble._fold_begin(fold)
        self.assertIn("A correction", bubble.said.get("1.0", "end"))
        bubble.close()

    def test_the_box_is_one_line_and_grows_with_what_is_written(self) -> None:
        bubble = self._bubble(lambda text, conversation: {})
        self.assertEqual(int(bubble.box.cget("height")), 1)
        bubble.box.insert("end", "\n".join("line" for _ in range(3)))
        bubble._fit_box()
        self.assertEqual(int(bubble.box.cget("height")), 3)
        bubble.box.insert("end", "\n".join("line" for _ in range(20)))
        bubble._fit_box()
        self.assertEqual(int(bubble.box.cget("height")), cb.BOX_TALLEST)
        bubble.close()

    def test_a_picture_is_seen_before_it_is_sent(self) -> None:
        bubble = self._with_a_picture(lambda text, conversation, files=None: {})
        self.assertEqual(len(bubble._thumbs), 1)
        self.assertTrue(bubble.files_row.winfo_ismapped())
        self.assertGreaterEqual(bubble.files_row.winfo_height(), cb.THUMB)
        bubble.close()

    def test_at_brings_up_the_files_and_the_one_chosen_goes_with_the_message(self) -> None:
        asked = []

        def send(text, conversation, files=None, mentions=None):
            asked.append((text, files, mentions))
            return {"answer": "Seen.", "conversation_id": "c1"}
        bubble = self._with_a_picture(send)
        bubble.box.insert("end", "Look")
        bubble.submit()
        self._settle(0.3)
        self.assertEqual(asked, [("Look", ["chatfile_1"], None)])
        bubble.box.insert("end", "and @be")
        bubble._watch_mentions()
        self.root.update()
        self.assertTrue(bubble.mention_row.winfo_ismapped(), "sent before: still there to @")
        self.assertEqual(bubble._enter(SimpleNamespace(state=0)), "break", "Enter chooses")
        self.assertEqual(bubble.box.get("1.0", "end-1c"), "and @bed.png ")
        self.assertFalse(bubble.mention_row.winfo_ismapped())
        bubble.submit()
        self._settle(0.3)
        self.assertEqual(asked[-1], ("and @bed.png", [], ["chatfile_1"]))
        bubble.box.insert("end", "write to a@b")
        bubble._watch_mentions()
        self.assertIsNone(bubble._mention, "in a word, no")
        bubble.close()

    def test_pasted_nothing_there_is_said_and_words_go_on_as_ever(self) -> None:
        asked = []

        class Files:
            def from_clipboard(self, with_text):
                asked.append(with_text)
                return {"files": [], "skipped": []}
        bubble = self._bubble(lambda text, conversation: {}, files=Files())
        self.root.clipboard_clear()
        self.root.clipboard_append("plain words")
        bubble._pasted()
        self._settle(0.2)
        self.assertEqual(asked, [True], "words: only the pictures of an HTML copy")
        self.assertFalse(bubble.files_row.winfo_ismapped(), "nothing to say about it")
        bubble.close()

    def test_a_long_conversation_has_a_bar_on_its_right(self) -> None:
        # The user, 2026-11-03: the chat from the avatar, once it is long.
        bubble = self._bubble(lambda text, c: {"answer": "Noted."})
        bubble.box.insert("1.0", "Hi")
        bubble.submit()
        self._settle(0.5)
        self.assertFalse(bubble.bar.wanted, "short: no bar")
        self.assertEqual(bubble.bar.canvas.find_all(), ())
        for n in range(40):
            bubble._write("Fern", "agent", f"Line {n} of a long answer about the garden.")
        self._settle(0.3)
        self.assertTrue(bubble.bar.wanted)
        self.assertTrue(bubble.bar.canvas.find_all(), "the thumb drawn")
        self.assertGreater(bubble.bar.canvas.winfo_x(), bubble.said.winfo_x(), "on the right")
        bubble.bar.text.yview_moveto(0.0)
        self._settle(0.2)
        self.assertEqual(bubble.bar.view[0], 0.0, "it follows the view")
        bubble.close()

    def test_while_sized_the_old_turns_are_folded_and_all_back_after(self) -> None:
        # The user, 2026-11-03: sizing it went in jerks - each width had the
        # whole conversation measured again.
        bubble = self._bubble(lambda text, c: {"answer": "ok"})
        for n in range(150):
            bubble._write("Fern", "agent", f"Turn {n} about the garden.")
        self._settle(0.2)
        everything = bubble.said.get("1.0", "end-1c")
        bubble._edge_start(SimpleNamespace(x_root=500, y_root=500), "se")
        folded = bubble.said.tag_ranges(cb.FOLDED)
        self.assertTrue(folded, "the old turns folded")
        self.assertIn("Turn 149", bubble.said.get(folded[-1], "end"), "the newest shown")
        bubble._edge_move(SimpleNamespace(x_root=560, y_root=540))
        bubble._edge_end()
        self.assertEqual(bubble.said.tag_ranges(cb.FOLDED), ())
        self.assertEqual(bubble.said.get("1.0", "end-1c"), everything, "nothing lost")
        bubble.close()

    def test_a_failure_is_said_in_it(self) -> None:
        def send(text, conversation):
            raise RuntimeError("no model is set")
        bubble = self._bubble(send)
        bubble.box.insert("1.0", "Hi")
        with self.assertLogs("personalized_agent.ui.chat_bubble", "WARNING"):
            bubble.submit()
            self._settle(0.5)
        self.assertIn("It could not answer: no model is set", bubble.said.get("1.0", "end-1c"))
        self.assertFalse(bubble.waiting)
        bubble.close()

    def test_its_size_dragged_by_any_edge_and_kept(self) -> None:
        kept = []
        bubble = self._bubble(lambda t, c: {"answer": "ok"}, on_size=kept.append)
        width, height = bubble.window.winfo_width(), bubble.window.winfo_height()
        bubble._edge_start(SimpleNamespace(x_root=500, y_root=500), "nw")
        bubble._edge_move(SimpleNamespace(x_root=380, y_root=300))
        bubble._edge_end()
        self.root.update()
        size = (bubble.window.winfo_width(), bubble.window.winfo_height())
        self.assertEqual(size, (width + 120, height + 200))
        self.assertEqual(kept, [size])
        bubble.box.insert("1.0", "Hi")
        bubble.submit()
        self._settle(0.5)
        self.assertEqual((bubble.window.winfo_width(), bubble.window.winfo_height()), size,
                         "sized by the user, it stays so as it is talked in")
        bubble.close()
        again = self._bubble(lambda t, c: {}, kept_size=size)
        self.assertEqual((again.window.winfo_width(), again.window.winfo_height()), size,
                         "the next time, as the user left it")
        again.close()

    def test_dragged_its_moves_are_given_once_a_frame(self) -> None:
        """The user, 2026-11-01: the chat bubble flickered as it was dragged."""
        bubble = self._bubble(lambda t, c: {"answer": "ok"})
        given = []
        geometry = bubble.window.geometry
        bubble.window.geometry = lambda *a: (given.append(a) if a else None, geometry(*a))[1]
        bubble._grab(SimpleNamespace(x_root=900, y_root=500))
        for step in range(30):
            bubble._move(SimpleNamespace(x_root=900 - step * 3, y_root=500 + step))
        bubble._edge_start(SimpleNamespace(x_root=500, y_root=500), "nw")
        for step in range(30):
            bubble._edge_move(SimpleNamespace(x_root=500 - step * 4, y_root=500 - step * 3))
        self.root.update()
        bubble._edge_end()
        self.assertLessEqual(len(given), 3, "sixty moves, given a few times - not sixty")
        bubble.close()

    def test_zoomed_by_ctrl_plus_and_minus_nothing_made_again(self) -> None:
        """The user, 2026-11-02: "chat bubble 也要支持ctrl + - 缩放"."""
        zoomed = []
        bubble = self._bubble(lambda t, c: {"answer": "ok"}, on_zoom=zoomed.append)
        modifier = "Command" if sys.platform == "darwin" else "Control"
        for key in ("plus", "equal", "minus", "0"):
            self.assertTrue(bubble.window.bind(f"<{modifier}-{key}>"), key)
        box_font, said_font = bubble.box.cget("font"), bubble.said.cget("font")
        size = lambda name: int(self.root.tk.call("font", "configure", name, "-size"))
        before, box = size(box_font), bubble.box
        bubble.zoom_by(1)
        bubble.zoom_by(1)
        self.root.update()
        self.assertIs(bubble.box, box, "nothing made again")
        self.assertGreater(size(box_font), before)
        self.assertEqual(size(box_font), size(said_font), "what was said too")
        self.assertEqual(zoomed, [cb.ZOOM_START + 1, cb.ZOOM_START + 2], "each kept")
        bubble.zoom_to(cb.ZOOM_START)
        self.assertEqual(size(box_font), before, "Ctrl 0: as it started")
        for _ in range(20):
            bubble.zoom_by(-1)
        self.assertEqual(bubble.zoom, 0, "never smaller than the smallest")
        bubble.close()
        self.assertEqual(bubble._fonts, {}, "its fonts let go")

    def test_minimised_to_a_bar_nothing_lost_closed_a_new_one(self) -> None:
        # The user, 2026-11-03: a bar when minimised, all of it back when
        # expanded; closed, the next one opened is a new conversation.
        bubble = self._bubble(lambda t, c: {"answer": "Watered.", "conversation_id": "conv_2"})
        bubble.box.insert("1.0", "Water the ferns")
        bubble.submit()
        self._settle(0.5)
        bubble.box.insert("1.0", "half written")
        tall = bubble.window.winfo_height()
        bottom = bubble.window.winfo_y() + tall
        bubble.minimize()
        self.root.update()
        self.assertTrue(bubble.minimized)
        self.assertLess(bubble.window.winfo_height(), tall / 2, "only its line: a bar")
        self.assertFalse(bubble.row.winfo_ismapped())
        self.assertEqual(bubble.window.winfo_y() + bubble.window.winfo_height(), bottom,
                         "its bottom where it was")
        # An answer that comes meanwhile: the bar stays a bar, its dot lit.
        bubble._write("Fern", "agent", "And the tomatoes too.")
        self.root.update()
        self.assertTrue(bubble.minimized)
        self.assertFalse(bubble.said_box.winfo_ismapped())
        dot, oval = bubble._dot
        self.assertEqual(dot.itemcget(oval, "fill"), cb.LOOK["you"])
        bubble.show()                            # the avatar pressed: as it was
        self.root.update()
        self.assertFalse(bubble.minimized)
        self.assertTrue(bubble.row.winfo_ismapped() and bubble.said_box.winfo_ismapped())
        self.assertIn("And the tomatoes too.", bubble.said.get("1.0", "end-1c"))
        self.assertEqual(bubble.box.get("1.0", "end-1c"), "half written", "nothing lost")
        self.assertEqual(dot.itemcget(oval, "fill"), cb.LOOK["dot"])
        self.assertEqual(bubble.conversation_id, "conv_2")
        bubble.window.event_generate("<Escape>")
        self.root.update()
        self.assertTrue(bubble.minimized, "Escape minimises - nothing is lost by it")
        bubble.close()
        self.assertIsNone(bubble.conversation_id, "closed: the next is a new conversation")

    def test_ctrl_and_the_wheel_zoom_it_wherever_the_pointer_is(self) -> None:
        bubble = self._bubble(lambda text, c: "Noted.")
        start = bubble.zoom
        # Bound on each, and handled there: a Text's own wheel would scroll it first. (Tk gives a
        # made-up wheel event to the keyboard's window, so the binding is what can be read here.)
        for one in (bubble.window, bubble.said, bubble.box):
            self.assertTrue(one.bind(f"<{MOD}-MouseWheel>"), one)
        self.assertEqual(bubble._wheel(SimpleNamespace(delta=120)), "break")
        self.assertEqual(bubble.zoom, start + 1)
        bubble._wheel(SimpleNamespace(delta=-120))
        self.assertEqual(bubble.zoom, start)

    def test_where_to_write_can_be_seen(self) -> None:
        bubble = self._bubble(lambda text, c: "Noted.")
        self.assertEqual(int(bubble.row.cget("highlightthickness")), 1)
        bubble.box.event_generate("<FocusOut>")
        self.root.update()
        self.assertEqual(bubble.row.cget("highlightbackground"), cb.LOOK["field_line"])
        bubble.box.event_generate("<FocusIn>")
        self.root.update()
        self.assertEqual(bubble.row.cget("highlightbackground"), cb.LOOK["primary"])
        bubble.box.event_generate("<FocusOut>")
        self.root.update()
        self.assertEqual(bubble.row.cget("highlightbackground"), cb.LOOK["field_line"])

    def test_its_corner_shows_it_can_be_sized(self) -> None:
        bubble = self._bubble(lambda text, c: "Noted.")
        rim = bubble.window.winfo_children()[0]
        grips = [one for one in rim.winfo_children() if isinstance(one, tk.Canvas)]
        self.assertEqual(len(grips), 1)
        self.assertEqual(grips[0].cget("cursor"), cb.EDGES["se"])
        self.assertEqual(len(grips[0].find_all()), 3)

    def test_its_corners_lie_over_nothing_in_it(self) -> None:
        """Over the box they showed as two black corners, once it stood out from the ground."""
        bubble = self._bubble(lambda text, c: "Noted.")
        self.root.update()
        rim = bubble.window.winfo_children()[0]

        def box(one):
            return (one.winfo_rootx(), one.winfo_rooty(), one.winfo_rootx() + one.winfo_width(),
                    one.winfo_rooty() + one.winfo_height())
        row = box(bubble.row)
        strips = [one for one in rim.winfo_children() if one.winfo_manager() == "place"]
        self.assertEqual(len(strips), len(cb.EDGES))
        for one in strips:
            left, top, right, bottom = box(one)
            self.assertFalse(left < row[2] and right > row[0] and top < row[3] and bottom > row[1],
                             (one.cget("cursor"), box(one), row))

    def test_a_bar_taken_by_its_edge_opens(self) -> None:
        bubble = self._bubble(lambda text, c: "Noted.")
        bubble.minimize()
        self.root.update()
        bubble._edge_start(SimpleNamespace(x_root=0, y_root=0), "se")
        self.root.update()
        self.assertFalse(bubble.minimized)
        self.assertIsNone(bubble._resizing, "opened, not sized by the same press")

    def test_the_desktop_opens_one_and_keeps_its_size(self) -> None:
        from personalized_agent.ui.desktop import DesktopAgent
        kept = {"chat_bubble_size": [420, 300], "chat_bubble_zoom": 4}
        config = SimpleNamespace(get=lambda key, default=None: kept.get(key, default),
                                 set=lambda key, value, internal=False: kept.update({key: value}))
        fake = SimpleNamespace(
            root=self.root, config=config, queue=queue.Queue(), _conversation_id="conv_7",
            _words=lambda text: text, _popup_font=lambda: ("Segoe UI", 10),
            agent_window=SimpleNamespace(winfo_x=lambda: 1200, winfo_y=lambda: 800),
            _avatar_box=lambda: (1200, 800, 120, 120), _beside_box=lambda: (1200, 800, 120, 120), _chat_draft=lambda: "",
            _keep_chat_draft=lambda _text: None,
            app=SimpleNamespace(avatar_name=lambda: "Fern",
                                agent=SimpleNamespace(handle_message=lambda t, c: {})),
            open_chat=lambda: None)
        DesktopAgent.open_chat_bubble(fake)
        self.root.update()
        first = fake._chat_bubble
        self.assertEqual((first.window.winfo_width(), first.window.winfo_height()), (420, 300))
        # A NEW CONVERSATION each time one is opened (the user, 2026-11-03).
        self.assertIsNone(first.conversation_id, "a new conversation")
        DesktopAgent.open_chat_bubble(fake)
        self.assertIs(fake._chat_bubble, first, "one bubble - pressed again, written in again")
        first.on_size((500, 360))
        from personalized_agent.ui import sizes
        self.assertEqual(kept["chat_bubble_size"], sizes.share(self.root, (500, 360)), "its share")
        self.assertEqual(sizes.kept(self.root, kept["chat_bubble_size"]), (500, 360))
        # ITS ZOOM, a hidden setting: the older one of its own read, every window's kept.
        self.assertEqual(first.zoom, 4)
        first.zoom_by(1)
        self.assertEqual(kept["popup_zoom"], 3)
        first.close()


if __name__ == "__main__":
    unittest.main()
