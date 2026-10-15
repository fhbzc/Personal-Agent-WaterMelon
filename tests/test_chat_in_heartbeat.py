"""The user's direct chat with the agent is part of what every step of the
agent heartbeat reads: each message in the window - the user's and the
agent's reply - an event of its own, said as which it is; read whole by its
id. What the Chat debugger sent is a test, not the user, and is left out."""

from __future__ import annotations

import unittest
from datetime import timedelta

from support import TemporaryAgent

from personalized_agent.common.events.models import utcnow
from personalized_agent.heartbeat.core.heartbeat import CHAT_APPLICATION


class TheDirectChatIsReadTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_each_message_said_as_whose(self) -> None:
        store = self.app.conversations
        conversation = store.create("Planning")
        asked = store.add_message(conversation, "user", "Remind me to call Ana on Friday")
        store.add_message(conversation, "assistant", "I will remind you on Friday at 9.")
        tested = store.create("A test")
        store.add_message(tested, "user", "a scenario of the debugger",
                          metadata={"origin": "chat_debugger"})
        events = self.beat._activity_since(10)
        chat = [one for one in events if one.get("application") == CHAT_APPLICATION]
        said = " ".join(str(one) for one in chat)
        self.assertIn("THE USER, to you (the agent): Remind me to call Ana on Friday", said)
        self.assertIn("YOU (the agent), replying to the user: I will remind you", said)
        self.assertNotIn("a scenario of the debugger", said)
        opened = self.beat._open_by_ids([asked["id"]])[asked["id"]]
        self.assertEqual((opened["who"], opened["content"]),
                         ("the user", "Remind me to call Ana on Friday"))
        self.assertEqual(opened["conversation"], "Planning")

    def test_only_the_window(self) -> None:
        store = self.app.conversations
        conversation = store.create()
        store.add_message(conversation, "user", "said long ago")
        self.app.db.execute("UPDATE messages SET created_at = ? WHERE content = ?",
                            ((utcnow() - timedelta(hours=3)).isoformat(), "said long ago"))
        events = self.beat._activity_since(10)
        self.assertFalse([one for one in events
                          if "said long ago" in str(one)])

    def test_the_steps_are_told_what_it_is(self) -> None:
        from personalized_agent.heartbeat.core.heartbeat import StateFloor
        floor = StateFloor(minutes=5, events=[], states=[])
        self.assertIn("DIRECT CHAT WITH YOU", floor.context_text())


if __name__ == "__main__":
    unittest.main()
