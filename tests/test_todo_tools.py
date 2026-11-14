"""THE CHAT ADDS TO-DOS (the user, 2026-11-03: "chat里要能直接加to do"; the
chat from the avatar said it could not). The to-dos here are made up."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.agent.core.tools import ToolRegistry
from personalized_agent.agent.feedback.todo_list import TodoList
from personalized_agent.agent.feedback.todo_tools import add_tools
from personalized_agent.data_manage.storage.database import Database


class ToolsTests(unittest.TestCase):

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._dir.name) / "agent.db")
        self.todos = TodoList(self.db)
        self.registry = ToolRegistry()
        add_tools(self.registry, self.todos)

    def tearDown(self) -> None:
        self.db.close()
        self._dir.cleanup()

    def test_added_from_the_chat_onto_the_users_list(self) -> None:
        said = self.registry.call("todo_add", {"text": "  Water the  tomatoes ",
                                               "detail": "before Friday"})
        self.assertIn("to-do list", said["said"])
        [one] = self.todos.open()
        self.assertEqual((one["text"], one["detail"], one["notification_id"]),
                         ("Water the tomatoes", "before Friday", ""))
        self.assertEqual([item["text"] for item in self.registry.call("todo_list", {})["todos"]],
                         ["Water the tomatoes"])

    def test_nothing_added_without_words(self) -> None:
        self.assertIn("error", self.registry.call("todo_add", {"text": "   "}))
        self.assertEqual(self.todos.open(), [])

    def test_no_tool_takes_one_off(self) -> None:
        # The agent may SUGGEST one done or gone (2026-11-09, a button on the to-do); no tool of
        # the chat's takes one off by itself.
        self.assertEqual(sorted(spec.name for spec in self.registry.specs),
                         ["todo_add", "todo_list", "todo_suggest_done", "todo_suggest_remove"])
        for spec in self.registry.specs:
            self.assertFalse(spec.name.endswith(("_delete", "_remove", "_done"))
                             and not spec.name.startswith("todo_suggest_"), spec.name)


class TheChatHasThemTests(unittest.TestCase):

    def test_the_agents_chat_is_given_them(self) -> None:
        agent = TemporaryAgent()
        try:
            self.assertTrue(agent.app.agent.tools.has("todo_add"))
            self.assertTrue(agent.app.agent.tools.has("todo_list"))
        finally:
            agent.cleanup()


if __name__ == "__main__":
    unittest.main()
