"""THE INFORMATION QUEUE (agent/information_queue.py; the user, 2026-11-16): what the agent is to tell is held and
judged by the agent - when, and how - and the user's own reminders are never dropped nor told late."""

from __future__ import annotations

import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from support import TemporaryAgent

from personalized_agent.agent import information_queue as iq
from personalized_agent.agent.talk_style import TalkStyle


def _when(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.app.config.set("information_queue", True, internal=True)
        self.chatting = False
        self.queue = iq.InformationQueue(self.app.notifications, SimpleNamespace(), self.app.config,
                                         chatting=lambda: self.chatting)
        self.app.notifications.gate = self.queue.gate
        self.created = []
        self.app.bus.subscribe("notification.created", lambda _t, said: self.created.append(said["id"]))

    def notify(self, **meta):
        with mock.patch.object(iq.threading, "Thread") as thread:     # judged by hand here, not on a thread
            made = self.app.notifications.notify("Seed club at 3:30pm", "Room 2, bring the bean packets.",
                                                 kind="proactive", metadata=meta)
        self.started = thread.call_count
        return made

    def meta(self, ident):
        return self.app.notifications.get(ident)["metadata"]


class HeldAsItIsMadeTests(_Base):
    def test_not_chatting_it_is_held_and_judged_at_once(self) -> None:
        made = self.notify(reason="the club meets today")
        self.assertTrue(self.meta(made.id)["held"])
        self.assertEqual(self.started, 1, "one judgement, at once")
        self.assertEqual(self.created, [], "told nothing yet")
        self.assertEqual(self.app.notifications.waiting(), [])
        self.assertEqual(self.app.notifications.for_the_side(), [])

    def test_the_users_own_reminder_not_chatting_is_told_at_once(self) -> None:
        made = self.notify(made_by="user")
        self.assertNotIn("held", self.meta(made.id))
        self.assertEqual(self.created, [made.id])

    def test_the_users_own_reminder_while_chatting_waits_a_moment_only(self) -> None:
        self.chatting = True
        made = self.notify(made_by="agent", made_in="agent")      # asked for in a chat
        meta = self.meta(made.id)
        self.assertTrue(meta["held"] and meta["own"])
        self.assertLessEqual(_when(meta["judge_by"]) - datetime.now(timezone.utc),
                             timedelta(seconds=iq.OWN_GRACE_SECONDS + 1))
        self.assertEqual(self.queue.judge(self.app.notifications.get(made.id)), "now", "never dropped")
        self.assertEqual(self.created, [made.id])

    def test_what_is_wrong_and_what_waits_on_approval_are_never_held(self) -> None:
        for kind in iq.NEVER_HELD:
            made = self.app.notifications.notify("Wrong", f"a {kind}", kind=kind)
            self.assertIn(made.id, self.created)

    def test_off_nothing_is_held(self) -> None:
        self.app.config.set("information_queue", False, internal=True)
        made = self.notify()
        self.assertEqual(self.created, [made.id])


class JudgedTests(_Base):
    def judged(self, said, row=None):
        made = self.notify(reason="the club meets today")
        with mock.patch.object(self.queue, "_ask", return_value=said):
            return made, self.queue.judge(self.app.notifications.get(made.id))

    def test_now_it_is_told_in_the_agents_own_words(self) -> None:
        made, decision = self.judged(("now", None, "the club is soon", "The seed club's at 3:30 - bring the beans!",
                                             ["Tell garden things in the afternoon"]))
        self.assertEqual(decision, "now")
        self.assertEqual(self.created, [made.id])
        meta = self.meta(made.id)
        self.assertEqual(meta["said"], "The seed club's at 3:30 - bring the beans!")
        # 2026-11-16: "列出某个decision用到了哪些规则（选最重要的，不超过3条）".
        self.assertEqual(meta["rules_used"], ["Tell garden things in the afternoon"])
        self.assertNotIn("held", meta)
        self.assertEqual([one["id"] for one in self.app.notifications.for_the_side()], [made.id])

    def test_told_in_its_own_words_every_link_kept(self) -> None:
        """2026-11-17: "允许agent整体转述，但是要agent一定保留里面的所有url/超链接信息"."""
        import inspect
        row = {"title": "A bean guide", "message": "Read it at https://example.org/beans. Also (https://seeds.test/b)",
               "metadata": {"url": "https://example.org/beans"}}
        self.assertEqual(iq.links_of(row), ["https://example.org/beans", "https://seeds.test/b"])
        self.assertEqual(iq.with_links("A good one on beans: https://example.org/beans", row),
                         "A good one on beans: https://example.org/beans\nhttps://seeds.test/b", "the one left out added")
        self.assertEqual(iq.with_links("Both: https://example.org/beans https://seeds.test/b", row),
                         "Both: https://example.org/beans https://seeds.test/b")
        self.assertEqual(iq.with_links("", row), "", "nothing said, nothing added")
        self.assertIn("with_links(", inspect.getsource(iq.InformationQueue._ask))
        self.assertIn("KEEP EVERY LINK", iq.JUDGE_SYSTEM)
        self.assertIn("KEEPING EVERY LINK", iq.CHAT_NOTE)

    def test_where_from_about_what_and_the_screen_when_needed(self) -> None:
        """2026-11-17: "标明来源 ... 与什么content相关，这样模型可以判断" - "允许调用当前user的屏幕截图/ocr或者text"."""
        made = self.notify(reason="You wrote 'attached' and nothing is", created_by="heads_up",
                           related_to="the Outlook draft to the seed club")
        row = self.app.notifications.get(made.id)
        item = self.queue._item(row)
        self.assertEqual((item["from"], item["related_to"]), ("heads_up", "the Outlook draft to the seed club"))
        self.assertIn("attached", item["why_raised"])
        looked = []
        self.queue.screen = lambda how: looked.append(how) or {"title": "Seed club - Message", "text": "see attached"}
        from personalized_agent.models.base import ToolCall
        rounds = [[ToolCall(id="t1", name="screen_text_now", arguments={})], []]
        asked = []

        def invoke(request, role=None):
            asked.append(request)
            return SimpleNamespace(tool_calls=rounds[len(asked) - 1], raw_content="", text="")
        self.queue.models = SimpleNamespace(invoke=invoke)
        with mock.patch("personalized_agent.models.answers.read_text",
                        return_value=('{"decision": "now", "say": "No attachment yet.", "why": "still on it"}', False)):
            said = self.queue._ask(row)
        self.assertEqual(said[0], "now")
        self.assertEqual(looked, ["text"], "it looked at the screen")
        self.assertEqual(sorted(spec.name for spec in asked[0].tools), ["screen_picture_now", "screen_text_now"])
        self.assertIn("related_to", iq.JUDGE_SYSTEM)
        from personalized_agent.common.modules import TASK_MODULE
        self.assertEqual(TASK_MODULE.get(iq.TASK), "information_queue", "its tokens apart, under the Monitor")
        # The Monitor off, the queue off too: told as it comes, no model asked (2026-11-17).
        self.app.config.set("module_monitor_enabled", False, internal=True)
        self.assertFalse(self.queue.on)
        self.app.config.set("module_monitor_enabled", True, internal=True)

    def test_drop_is_read_and_logged_only(self) -> None:
        with self.assertLogs("personalized_agent.agent.information_queue", "INFO") as said:
            made, decision = self.judged(("drop", None, "already known", "", []))
        self.assertEqual(decision, "drop")
        self.assertEqual(self.created, [])
        self.assertTrue(self.app.notifications.get(made.id)["read"])
        self.assertIn("information dropped", "\n".join(said.output))

    def test_later_is_held_till_the_time_named_within_bounds(self) -> None:
        far = datetime.now(timezone.utc) + timedelta(days=5)
        made, decision = self.judged(("later", far, "after the meeting", "", []))
        self.assertEqual(decision, "later")
        due = _when(self.meta(made.id)["judge_by"])
        # A time it named: kept up to a day on (2026-11-20: "just before tomorrow's 11 a.m." had been cut to 12 hours).
        self.assertLessEqual(due - datetime.now(timezone.utc), timedelta(hours=iq.LATER_NAMED_MOST_HOURS, minutes=1))
        self.assertEqual(self.created, [])

    def test_a_time_named_within_a_day_is_kept_as_named(self) -> None:
        tomorrow = datetime.now(timezone.utc) + timedelta(hours=20)
        made, decision = self.judged(("later", tomorrow, "just before tomorrow's opening", "", []))
        self.assertEqual(decision, "later")
        due = _when(self.meta(made.id)["judge_by"])
        self.assertLess(abs((due - tomorrow).total_seconds()), 60, "not cut to twelve hours")

    def test_later_never_past_what_it_is_about(self) -> None:
        soon = datetime.now(timezone.utc) + timedelta(minutes=20)
        made = self.notify(deadline=soon.isoformat())
        with mock.patch.object(self.queue, "_ask", return_value=("later", soon + timedelta(hours=3), "", "", [])):
            self.queue.judge(self.app.notifications.get(made.id))
        self.assertLessEqual(_when(self.meta(made.id)["judge_by"]), soon)

    def test_a_judgement_that_cannot_be_had_is_told(self) -> None:
        made = self.notify()
        with mock.patch.object(self.queue, "_ask", side_effect=RuntimeError("no model")):
            self.assertEqual(self.queue.judge(self.app.notifications.get(made.id)), "now")
        self.assertEqual(self.created, [made.id])

    def test_judged_again_when_its_time_comes(self) -> None:
        made = self.notify()
        meta = dict(self.meta(made.id), judge_by=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
        self.app.notifications.set_metadata(made.id, meta)
        with mock.patch.object(self.queue, "_ask", return_value=("now", None, "", "", [])):
            self.queue.look()
        self.assertEqual(self.created, [made.id])


class InTheChatTests(_Base):
    def test_given_to_the_full_agent_and_told_by_its_mark(self) -> None:
        """A reply naming what it told inside it (the way before) - still read."""
        self.chatting = True
        made = self.notify(reason="the club meets today")
        note = self.queue.for_the_chat()
        self.assertIn(made.id, note)
        answer, ids = iq.told_in(f"Sure - done.\n\nBefore I forget: the seed club is at 3:30.\n[[told: {made.id}]]")
        self.assertNotIn("[[told", answer)
        self.assertEqual(ids, [made.id])
        self.queue.told(ids)
        self.assertTrue(self.app.notifications.get(made.id)["read"])
        self.assertTrue(self.meta(made.id)["told_in_chat"])
        self.assertEqual(self.queue.for_the_chat(), "")

    def test_told_apart_a_message_of_its_own(self) -> None:
        """2026-11-17: "一个话就是回答一个的，后面[a to-do of theirs]和当前的回答没关系，应该另起一个chat message"."""
        self.chatting = True
        with mock.patch.object(iq.threading, "Thread"):
            made = self.app.notifications.notify("Seed club", "Room 2. https://seeds.test/club", kind="proactive",
                                                 metadata={"reason": "the club meets today"})
        note = self.queue.for_the_chat()
        self.assertIn("[[tell:", note)
        self.assertIn("ONE MESSAGE, ONE THING", note)
        said = f"Hello! Good morning.\n\n[[tell: {made.id}]]\nThe seed club meets at 3:30 in room 2."
        reply, apart, named = iq.told_apart(said)
        self.assertEqual(reply, "Hello! Good morning.", "the reply alone")
        self.assertEqual(apart, [(made.id, "The seed club meets at 3:30 in room 2.")])
        self.assertEqual(iq.before_the_marks(said), "Hello! Good morning.", "never shown while it is written")
        self.assertTrue(self.queue.tell_apart(made.id, apart[0][1]))
        meta = self.meta(made.id)
        self.assertNotIn("held", meta)
        self.assertEqual(meta["said"], "The seed club meets at 3:30 in room 2.\nhttps://seeds.test/club",
                         "its own message, its link kept")
        self.assertIn(made.id, self.created, "told: in the panel, a message of its own")
        self.assertFalse(self.queue.tell_apart(made.id, "again"), "told once")

    def test_what_is_urgent_interrupts_first(self) -> None:
        """2026-11-17: "如果是打断，你要让主agent知道这是打断的情况，让他有相关的承接（比如说不好意思打断一下)"."""
        self.chatting = True
        made = self.notify(reason="the club meets today")
        note = self.queue.for_the_chat()
        self.assertIn("INTERRUPTS", note)
        self.assertIn("[[reply]]", note)
        writing = f"[[tell: {made.id}]]\nSorry to interrupt - the seed club starts in ten minutes."
        self.assertEqual(iq.interrupting(writing), [], "not yet: still being written")
        self.assertEqual(iq.before_the_marks(writing), "", "nothing of the reply yet")
        writing += "\n[[reply]]\nHello! Good morning."
        self.assertEqual(iq.interrupting(writing),
                         [(made.id, "Sorry to interrupt - the seed club starts in ten minutes.")], "told at once")
        self.assertEqual(iq.before_the_marks(writing), "Hello! Good morning.")
        reply, apart, _named = iq.told_apart(writing + "\n\n[[tell: other]]\nBy the way, the beans came.")
        self.assertEqual(reply, "Hello! Good morning.")
        self.assertEqual([one[0] for one in apart], [made.id, "other"], "what interrupted first, the rest after")
        self.assertTrue(self.queue.tell_apart(made.id, apart[0][1]))
        meta = self.meta(made.id)
        self.assertGreaterEqual(meta["released_at"], self.app.notifications.get(made.id)["created_at"],
                           "shown where it was told, not where it was made")

    def test_nothing_held_nothing_said(self) -> None:
        self.assertEqual(self.queue.for_the_chat(), "")


class TalkStyleTests(unittest.TestCase):
    def test_read_again_only_when_enough_is_new_and_not_too_soon(self) -> None:
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        app = agent.app
        style = TalkStyle(app.db, SimpleNamespace(), app.config)
        self.assertEqual(style.note(), "")
        self.assertFalse(style._due(), "nothing written yet")
        ident = app.conversations.create()
        for n in range(25):
            app.conversations.add_message(ident, "user", f"short one {n}")
        self.assertTrue(style._due())
        app.config.set("talk_style", "Short, mixes Chinese and English.", internal=True)
        app.config.set("talk_style_through", 25, internal=True)
        app.config.set("talk_style_at", datetime.now(timezone.utc).isoformat(), internal=True)
        self.assertFalse(style._due(), "read just now")
        self.assertIn("mixes Chinese and English", style.note())


if __name__ == "__main__":
    unittest.main()
