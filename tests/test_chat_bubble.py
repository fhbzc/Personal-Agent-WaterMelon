"""A CONVERSATION IN A BUBBLE BESIDE THE AVATAR (the user, 2026-11-01): one
click opens it; what is written is sent on a thread of its own and the
answer written in; the conversation is the chat page's; its size is the
user's, dragged by any edge, and kept. Everything said here is made up."""

from __future__ import annotations

import gc
import queue
import threading
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

    def test_the_desktop_opens_one_and_keeps_its_size(self) -> None:
        from personalized_agent.ui.desktop import DesktopAgent
        kept = {"chat_bubble_size": [420, 300]}
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
        first.close()


if __name__ == "__main__":
    unittest.main()
