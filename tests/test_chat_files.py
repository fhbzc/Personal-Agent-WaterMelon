"""Each conversation keeps the files it makes in `workspace/Chat/<its title>/`
(agent/dialog/files.py): chosen when it first writes, kept when its title
changes, told apart from another of the same name, and never mixed with a
conversation answered at the same time."""

from __future__ import annotations

import threading
import unittest
from pathlib import Path

from support import TemporaryAgent

from personalized_agent.models.base import ModelResponse, ToolCall


class ChatFilesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.chat_root = Path(self.app.policy.workspace_root) / "Chat"

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _model_writes(self, path: str, content: str, also_read: bool = False) -> None:
        """A model that writes `path` with the words it was asked in, then
        (optionally) reads it back, then answers."""
        lock = threading.Lock()
        turns = {}

        def invoke(request, role="primary"):
            asked = next(m["content"] for m in reversed(request.messages)
                         if m["role"] == "user" and isinstance(m["content"], str))
            with lock:
                step = turns.get(asked, 0)
                turns[asked] = step + 1
            if step == 0:
                return ModelResponse(tool_calls=[ToolCall(
                    "w1", "write_file", {"path": path, "content": f"{content}: {asked}"})])
            if step == 1 and also_read:
                return ModelResponse(tool_calls=[ToolCall("r1", "read_file", {"path": path})])
            return ModelResponse(text="Done.")

        self.app.models.invoke = invoke

    def _folder(self, conversation_id: str) -> Path:
        return Path(self.app.conversations.folder_of(conversation_id))

    def test_a_relative_write_lands_in_the_conversations_own_folder(self) -> None:
        self._model_writes("notes/summary.md", "the summary")
        said = self.app.agent.handle_message("Summarise the quarterly review")
        folder = self._folder(said["conversation_id"])
        self.assertEqual(folder.parent, self.chat_root)
        self.assertEqual(folder.name, "Summarise the quarterly review")
        written = folder / "notes" / "summary.md"
        self.assertEqual(written.read_text(encoding="utf-8"),
                         "the summary: Summarise the quarterly review")
        # Nothing went to the agent's own folder.
        self.assertFalse((Path(self.app.policy.write_root) / "notes" / "summary.md").exists())

    def test_it_reads_back_what_it_wrote(self) -> None:
        self._model_writes("draft.txt", "first draft", also_read=True)
        seen = []
        original = self.app.agent._execute_tool

        def watching(task_id, name, arguments):
            outcome = original(task_id, name, arguments)
            seen.append((name, arguments, outcome))
            return outcome

        self.app.agent._execute_tool = watching
        said = self.app.agent.handle_message("Write a first draft")
        read = [one for one in seen if one[0] == "read_file"]
        self.assertEqual(read[0][2]["content"], "first draft: Write a first draft")
        self.assertTrue(read[0][1]["path"].startswith(str(self._folder(said["conversation_id"]))))

    def test_the_folder_stays_when_the_title_changes(self) -> None:
        self._model_writes("a.txt", "one")
        said = self.app.agent.handle_message("Plan the offsite")
        cid = said["conversation_id"]
        first = self._folder(cid)
        self.app.conversations.set_title(cid, "Offsite in May")
        self._model_writes("b.txt", "two")
        self.app.agent.handle_message("Add the venue", conversation_id=cid)
        self.assertEqual(self._folder(cid), first)
        self.assertTrue((first / "a.txt").exists())
        self.assertTrue((first / "b.txt").exists())

    def test_a_second_conversation_of_the_same_name_is_told_apart(self) -> None:
        self._model_writes("a.txt", "x")
        one = self.app.agent.handle_message("Same words")["conversation_id"]
        self._model_writes("a.txt", "x")
        two = self.app.agent.handle_message("Same words")["conversation_id"]
        self.assertNotEqual(self._folder(one), self._folder(two))
        self.assertTrue(self._folder(two).name.startswith("Same words "))
        self.assertEqual(self._folder(one).parent, self._folder(two).parent)

    def test_conversations_answered_at_once_keep_apart(self) -> None:
        self._model_writes("out.txt", "made")
        results = {}

        def turn(words):
            results[words] = self.app.agent.handle_message(words)

        threads = [threading.Thread(target=turn, args=(f"Job number {n}",)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        for words, said in results.items():
            folder = self._folder(said["conversation_id"])
            self.assertEqual(folder.name, words)
            self.assertEqual((folder / "out.txt").read_text(encoding="utf-8"),
                             f"made: {words}")
        self.assertEqual(len(results), 4)

    def test_no_conversation_writes_where_it_always_did(self) -> None:
        # A tool called outside a conversation: relative to the agent's folder.
        self.app.agent.tools.call("write_file", {"path": "plain.txt", "content": "x"})
        self.assertTrue((Path(self.app.policy.write_root) / "plain.txt").exists())

    def test_nothing_outside_the_workspace_becomes_writable(self) -> None:
        outside = self.agent.dir / "escape.txt"
        self._model_writes(str(outside), "x")
        self.app.agent.handle_message("Try to write outside")
        self.assertFalse(outside.exists())
        # And the Chat folder is the chat's: the user's own part of the
        # workspace stays read-only to it.
        self.assertIn(self.chat_root.resolve(),
                      [Path(p).resolve() for p in self.app.policy.extra_writable])


if __name__ == "__main__":
    unittest.main()
