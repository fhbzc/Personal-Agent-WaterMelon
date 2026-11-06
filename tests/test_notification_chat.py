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


class AReplyTheChatClosedOnTests(unittest.TestCase):

    def test_it_comes_as_a_notification_opened_in_the_chat(self) -> None:
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        agent.app.chat_reply_notice("On the calendar", "conv_3")
        shown = agent.app.notifications.get(agent.app.notifications.recent(1)[0]["id"])
        self.assertEqual(shown["message"], "On the calendar")
        self.assertEqual(shown["metadata"]["chat_conversation"], "conv_3")


if __name__ == "__main__":
    unittest.main()
