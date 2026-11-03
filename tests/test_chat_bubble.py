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
        words = next(one for one in card.winfo_children() if isinstance(one, tk.Label))
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

    def test_the_desktop_opens_one_and_keeps_its_size(self) -> None:
        from personalized_agent.ui.desktop import DesktopAgent
        kept = {"chat_bubble_size": [420, 300], "chat_bubble_zoom": 4}
        config = SimpleNamespace(get=lambda key, default=None: kept.get(key, default),
                                 set=lambda key, value, internal=False: kept.update({key: value}))
        fake = SimpleNamespace(
            root=self.root, config=config, queue=queue.Queue(), _conversation_id="conv_7",
            _words=lambda text: text, _popup_font=lambda: ("Segoe UI", 10),
            agent_window=SimpleNamespace(winfo_x=lambda: 1200, winfo_y=lambda: 800),
            _avatar_box=lambda: (1200, 800, 120, 120),
            app=SimpleNamespace(avatar_name=lambda: "Fern",
                                agent=SimpleNamespace(handle_message=lambda t, c: {})),
            open_chat=lambda: None)
        DesktopAgent.open_chat_bubble(fake)
        self.root.update()
        first = fake._chat_bubble
        self.assertEqual((first.window.winfo_width(), first.window.winfo_height()), (420, 300))
        self.assertEqual(first.conversation_id, "conv_7", "the chat page's conversation")
        DesktopAgent.open_chat_bubble(fake)
        self.assertIs(fake._chat_bubble, first, "one bubble - pressed again, written in again")
        first.on_size((500, 360))
        self.assertEqual(kept["chat_bubble_size"], [500, 360])
        # ITS ZOOM, a hidden setting: opened at it, each new one kept.
        self.assertEqual(first.zoom, 4)
        first.zoom_by(1)
        self.assertEqual(kept["chat_bubble_zoom"], 5)
        first.close()


if __name__ == "__main__":
    unittest.main()
