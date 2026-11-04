"""CHAT TOPICS (the user, 2026-11-03, from PR #8): conversations under topics,
renamed, deleted, moved; each topic's shared context given to every chat in
it, and to no other. Everything here is made up."""

from __future__ import annotations

import unittest

import support  # noqa: F401
from support import TemporaryAgent


class TopicsTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.store = self.app.conversations

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _recording(self):
        sent = []
        original = self.app.models.invoke

        def record(request, role="primary"):
            sent.append(request)
            return original(request, role=role)

        self.app.models.invoke = record
        return sent

    def test_made_renamed_given_a_context_and_deleted_its_chats_kept(self) -> None:
        topic = self.store.create_project("Garden")
        self.store.edit_project(topic["id"], "Garden plans", "We grow tomatoes on raised beds.")
        chat = self.store.create()
        self.store.move_to_project(chat, topic["id"])
        self.assertEqual(self.store.conversation(chat)["project_id"], topic["id"])
        self.assertEqual(self.store.topic_context(chat),
                         "Topic: Garden plans\nWe grow tomatoes on raised beds.")
        self.assertTrue(self.store.delete_project(topic["id"]))
        self.assertIsNotNone(self.store.conversation(chat), "its conversations kept")
        self.assertIsNone(self.store.conversation(chat)["project_id"])
        self.assertEqual(self.store.topic_context(chat), "")

    def test_names_and_contexts_held_to_their_bounds(self) -> None:
        topic = self.store.create_project("Garden")
        with self.assertRaises(ValueError):
            self.store.edit_project(topic["id"], "  ", "")
        with self.assertRaises(ValueError):
            self.store.edit_project(topic["id"], "Garden", "x" * (self.store.MOST_CONTEXT + 1))
        with self.assertRaises(ValueError):
            self.store.move_to_project(self.store.create(), "project_none")
        self.assertIsNone(self.store.edit_project("project_none", "Garden", ""))

    def test_the_context_is_given_to_every_chat_in_it_and_no_other(self) -> None:
        topic = self.store.create_project("Garden")
        self.store.edit_project(topic["id"], "Garden", "The beds face south.")
        inside, outside = self.store.create(), self.store.create()
        self.store.move_to_project(inside, topic["id"])
        sent = self._recording()
        self.app.agent.handle_message("Where should the tomatoes go?", inside)
        self.app.agent.handle_message("Where should the tomatoes go?", outside)
        self.assertIn("The beds face south.", sent[0].system)
        self.assertIn("under a topic of the user's", sent[0].system)
        self.assertNotIn("The beds face south.", sent[1].system)

    def test_over_the_api(self) -> None:
        from personalized_agent.api.routes import build_routes
        from personalized_agent.api.server import Request
        routes = build_routes(self.app)

        def post(path, body):
            return routes[("POST", path)](Request("POST", path, {}, body, {}))
        topic = post("/api/chat/projects", {"name": "Garden"})
        chat = self.store.create()
        post("/api/chat/projects/edit", {"id": topic["id"], "name": "Garden", "context": "Sunny"})
        post("/api/conversation/topic", {"id": chat, "topic": topic["id"]})
        self.assertEqual(self.store.topic_context(chat), "Topic: Garden\nSunny")
        post("/api/conversation/topic", {"id": chat, "topic": ""})
        self.assertEqual(self.store.topic_context(chat), "")
        post("/api/chat/projects/delete", {"id": topic["id"]})
        self.assertEqual(self.store.projects(), [])


if __name__ == "__main__":
    unittest.main()
