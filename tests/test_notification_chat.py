"""WRITTEN ON A NOTIFICATION (the user, 2026-11-04): it closes and the words are a lesson; nothing
answers. A reply the chat closed on comes as a notification. Everything here is made up."""

from __future__ import annotations

import gc
import sys
import unittest
from unittest import mock

import support  # noqa: F401
from support import TemporaryAgent


class WrittenOnANotificationTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app

    def _lessons(self):
        return self.app.db.query("SELECT action, summary, reason FROM lesson_evidence ORDER BY at")

    def test_it_closes_and_is_a_lesson_in_their_words(self) -> None:
        said = self.app.notifications.notify("High water", "Gauge at noon", kind="proactive")
        self.assertTrue(self.app.notification_chat(said.id, "Not on weekends, please"))
        self.assertTrue(self.app.notifications.get(said.id)["read"])
        lesson = self._lessons()[-1]
        self.assertEqual(lesson["action"], "chat_reply")
        self.assertEqual(lesson["reason"], "Not on weekends, please")
        self.assertIn("High water", lesson["summary"])

    def test_nothing_answers_it(self) -> None:
        said = self.app.notifications.notify("High water", "Gauge at noon", kind="proactive")
        before = len(self.app.notifications.recent(50))
        with mock.patch.object(self.app.agent, "handle_message") as asked:
            self.app.notification_chat(said.id, "I am having lunch")
        asked.assert_not_called()
        self.assertEqual(len(self.app.notifications.recent(50)), before, "no reply comes")

    def test_every_notification_a_warning_too(self) -> None:
        said = self.app.notifications.notify("Disk full", "Almost", kind="warning")
        self.app.notification_chat(said.id, "Clear the old runs")
        self.assertEqual(self._lessons()[-1]["action"], "chat_reply")

    def test_what_it_asked_is_left_standing_no_longer(self) -> None:
        from personalized_agent.agent.feedback.store import TaskQuestion
        asked = self.app.questions.ask(TaskQuestion(
            task_id="", kind="suggestion", question="Q", metadata={"options": [
                {"key": "good_to_know", "label": "Good to know", "effect": "complete"}]}))
        said = self.app.notifications.notify("High water", "At noon", kind="proactive",
                                             template="suggestion",
                                             metadata={"question_id": asked.id})
        self.app.notification_chat(said.id, "Thanks")
        self.assertNotEqual(self.app.questions.get(asked.id).status, "pending")

    def test_nothing_written_or_no_such_one(self) -> None:
        said = self.app.notifications.notify("High water", "At noon", kind="proactive")
        self.assertFalse(self.app.notification_chat(said.id, "   "))
        self.assertFalse(self.app.notifications.get(said.id)["read"])
        self.assertFalse(self.app.notification_chat("notif_none", "Hello"))


class ItsSizeOfWordsTests(unittest.TestCase):

    def test_long_words_are_whole_and_scroll(self) -> None:
        """The day's report stopped in mid-word at 400 letters, with nowhere to read the rest."""
        import collections
        import tkinter as tk
        from types import SimpleNamespace
        from personalized_agent.ui.desktop import DesktopAgent
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(gc.collect)
        self.addCleanup(root.destroy)
        kept = {}
        desk = DesktopAgent.__new__(DesktopAgent)
        desk.root, desk.size = root, 128
        desk.config = SimpleNamespace(get=lambda key, default=None: kept.get(key, default),
                                      set=lambda key, value, internal=False: kept.__setitem__(key, value))
        desk._bubble_queue = collections.deque()
        desk._bubble, desk._bubble_count, desk._showing = None, None, None
        desk.app = SimpleNamespace(REMIND_LATER_MINUTES=30)
        desk._popup_font = lambda: ("Segoe UI", 12)
        desk._words = lambda text: text
        desk._fill = lambda text, *args: text.format(*args)
        desk._avatar_box = lambda: (900, 300, 128, 128)
        desk._options_for = lambda _question: [{"key": "", "label": "Got it", "primary": True}]
        desk._blocked = lambda: False
        text = "Your day: " + "A made-up sentence about the day. " * 120 + "THE END"
        desk.notify_bubble(text, notification_id="n1", template="passive")
        root.update()

        def texts(widget):
            found = [widget] if isinstance(widget, tk.Text) else []
            for one in widget.winfo_children():
                found += texts(one)
            return found
        said = "".join(one.get("1.0", "end-1c") for one in texts(desk._bubble))
        self.assertIn("THE END", said, "all of it")
        self.assertLessEqual(desk._bubble.winfo_height(), root.winfo_screenheight(), "on the screen")

    def test_the_pointer_on_it_and_ctrl_minus_draws_it_again_smaller_and_it_is_kept(self) -> None:
        import collections
        import tkinter as tk
        from types import SimpleNamespace
        from personalized_agent.ui.desktop import DesktopAgent
        root = tk.Tk()
        root.withdraw()
        # Tk's objects let go of on its own thread: collected on a worker's, they end the process.
        self.addCleanup(gc.collect)
        self.addCleanup(root.destroy)
        kept = {}
        desk = DesktopAgent.__new__(DesktopAgent)
        desk.root, desk.size = root, 128
        desk.config = SimpleNamespace(get=lambda key, default=None: kept.get(key, default),
                                      set=lambda key, value, internal=False: kept.__setitem__(key, value))
        desk._bubble_queue = collections.deque()
        desk._bubble, desk._bubble_count, desk._showing = None, None, None
        desk.app = SimpleNamespace(REMIND_LATER_MINUTES=30)
        desk._popup_font = lambda: ("Segoe UI", 12)
        desk._words = lambda text: text
        desk._fill = lambda text, *args: text.format(*args)
        desk._avatar_box = lambda: (900, 300, 128, 128)
        desk._options_for = lambda _question: [{"key": "", "label": "Got it", "primary": True}]
        desk._blocked = lambda: False
        desk.notify_bubble("High water at noon", notification_id="n1", template="passive")
        root.update()
        first = desk._bubble
        self.assertIsNotNone(first)
        high = first.winfo_height()
        first.focus_force()
        root.update()
        first.event_generate("<Command-minus>" if sys.platform == "darwin" else "<Control-minus>")
        root.update()
        self.assertEqual(kept["popup_zoom"], -1)
        self.assertIsNot(desk._bubble, first, "drawn again")
        self.assertTrue(desk._bubble.winfo_exists())
        self.assertLessEqual(desk._bubble.winfo_height(), high)
        desk._zoom_bubble(3)
        self.assertEqual(kept["popup_zoom"], 2)
        desk._bubble.destroy()


class ItsBarAndItsSmallestWordsTests(unittest.TestCase):
    """The user, 2026-11-06: the bar on the right could not be hit, and the words went no smaller."""

    def _desk(self, points, step):
        import collections
        import tkinter as tk
        from types import SimpleNamespace
        from personalized_agent.ui.desktop import DesktopAgent
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(gc.collect)
        self.addCleanup(root.destroy)
        kept = {"popup_zoom": step}
        desk = DesktopAgent.__new__(DesktopAgent)
        desk.root, desk.size = root, 128
        desk.config = SimpleNamespace(get=lambda key, default=None: kept.get(key, default),
                                      set=lambda key, value, internal=False: kept.__setitem__(key, value))
        desk._bubble_queue = collections.deque()
        desk._bubble, desk._bubble_count, desk._showing = None, None, None
        desk.app = SimpleNamespace(REMIND_LATER_MINUTES=30)
        desk._popup_font = lambda: ("Segoe UI", points)
        desk._words = lambda text: text
        desk._fill = lambda text, *args: text.format(*args)
        desk._avatar_box = lambda: (900, 300, 128, 128)
        desk._options_for = lambda _question: [{"key": "", "label": "Got it", "primary": True}]
        desk._blocked = lambda: False
        desk.notify_bubble("Your day: " + "A made-up sentence about the day. " * 120,
                           notification_id="n1", template="passive",
                           reason="For your survey", trigger="The evening report")
        root.update()
        return desk

    def _all(self, widget, kind):
        found = [widget] if isinstance(widget, kind) else []
        for one in widget.winfo_children():
            found += self._all(one, kind)
        return found

    def _bar(self, desk):
        """Its thumb comes once Tk has counted the lines, a moment after the card is up."""
        import time
        import tkinter as tk
        bar = self._all(desk._bubble, tk.Canvas)[-1]
        until = time.monotonic() + 5
        while not bar.find_all() and time.monotonic() < until:
            desk.root.update()
            time.sleep(0.01)
        return bar

    def _sizes(self, desk):
        import tkinter as tk
        import tkinter.font as tkfont
        return [tkfont.Font(root=desk.root, font=one.cget("font")).actual("size")
                for one in self._all(desk._bubble, (tk.Text, tk.Label))]

    def test_the_bar_is_pressed_anywhere_on_its_strip(self) -> None:
        import tkinter as tk
        from personalized_agent.ui import sizes
        from personalized_agent.ui.desktop import BAR_WIDE
        desk = self._desk(12, 0)
        bar = self._bar(desk)
        box = next(one for one in self._all(desk._bubble, tk.Text) if float(one.yview()[1]) < 1.0)
        self.assertEqual(bar.winfo_width(), sizes.px(bar, BAR_WIDE))
        self.assertGreaterEqual(bar.winfo_width(), 16)
        bar.event_generate("<ButtonPress-1>", x=0, y=bar.winfo_height() - 2)
        bar.event_generate("<ButtonRelease-1>", x=0, y=bar.winfo_height() - 2)
        desk.root.update()
        self.assertGreater(box.yview()[0], 0.0, "at the far edge from the thumb")

    def test_the_bar_is_seen_on_its_card_and_lit_under_the_pointer(self) -> None:
        import tkinter as tk
        from personalized_agent.ui.desktop import BUBBLE_LOOK
        look = BUBBLE_LOOK[False]
        desk = self._desk(12, 0)
        bar = self._bar(desk)
        self.assertEqual(bar.cget("bg"), look["bg"])
        thumb = bar.find_all()[0]
        self.assertEqual(bar.itemcget(thumb, "fill"), look["muted"])
        bar.event_generate("<Enter>", x=1, y=1)
        desk.root.update()
        self.assertEqual(bar.itemcget(bar.find_all()[0], "fill"), look["ink"])

    def test_large_words_go_by_share(self) -> None:
        said = self._sizes(self._desk(26, -5))
        self.assertEqual(max(abs(one) for one in said), 14, "a title of 13 and one, not 21")

    def test_every_part_of_it_goes_down_with_the_words(self) -> None:
        from personalized_agent.ui import sizes
        said = [abs(one) for one in self._sizes(self._desk(12, sizes.ZOOM_LEAST))]
        self.assertLessEqual(max(said), sizes.SMALLEST_WORDS + 1)

    def test_the_steps_as_they_were_stay_where_they_were(self) -> None:
        from personalized_agent.ui import chat_bubble, handed_note, sizes
        self.assertEqual(sizes.ZOOMS[sizes.ZOOM_START], 1.0)
        self.assertEqual(sizes.ZOOMS[sizes.ZOOM_START - 5], 0.5, "where the chat's least was")
        self.assertEqual(sizes.ZOOM_START + sizes.ZOOM_LEAST, 0)
        self.assertEqual(sizes.ZOOM_START + sizes.ZOOM_MOST, len(sizes.ZOOMS) - 1)
        self.assertIs(chat_bubble.ZOOMS, sizes.ZOOMS)
        self.assertIs(handed_note.ZOOMS, sizes.ZOOMS)


class AReplyTheChatClosedOnTests(unittest.TestCase):

    def test_no_longer_a_notification_it_is_in_its_conversation(self) -> None:
        """2026-11-13: only recommendations, the calendar, the report, risks, the day ahead, closing up and
        the application itself are told - "其它都取消"."""
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        before = len(agent.app.notifications.recent(50))
        agent.app.chat_reply_notice("On the calendar", "conv_3")
        self.assertEqual(len(agent.app.notifications.recent(50)), before)


if __name__ == "__main__":
    unittest.main()
