"""LEARNING FROM THE CHAT (the user, 2026-11-16: "agent现在主要的learning来自chat"): the agent's memory as Markdown files
with an index, as Claude keeps it (agent/memory_files.py); the chat summarised and learned from while quiet
(agent/chat_digest.py); every conversation given what was said before (app._more_context)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from support import TemporaryAgent

from personalized_agent.agent import chat_digest as cd
from personalized_agent.agent.memory_files import INDEX, MemoryFiles, add_tools


class MemoryFilesTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.memory = MemoryFiles(Path(folder.name) / "memory")

    def test_kept_listed_kept_again_and_forgotten(self) -> None:
        self.assertEqual(self.memory.note(), "", "nothing kept, nothing said")
        made = self.memory.write("Seed club day", "the seed club meets on Mondays", "project",
                                 "The seed club meets Mondays at 3:30pm.\nWhy: they asked.\nHow to apply: remind them.")
        self.assertEqual(made, {"kept": "seed-club-day", "new": True})
        self.assertIn("type: project", self.memory.read("seed-club-day"))
        self.assertIn("- [Seed club day](seed-club-day.md) — the seed club meets on Mondays", self.memory.index())
        again = self.memory.write("seed-club-day", "the seed club meets on Tuesdays", "project", "Tuesdays now.")
        self.assertFalse(again["new"])
        self.assertEqual(self.memory.index().count("seed-club-day.md"), 1, "one line a file")
        self.assertIn("Tuesdays", self.memory.index())
        self.assertIn("seed-club-day.md", self.memory.note())
        self.assertEqual(self.memory.forget("seed-club-day"), {"forgotten": "seed-club-day"})
        self.assertNotIn("seed-club-day", self.memory.index())
        self.assertIsNone(self.memory.read("seed-club-day"))

    def test_the_agents_tools(self) -> None:
        tools = {}
        add_tools(SimpleNamespace(add=lambda spec, run: tools.setdefault(spec.name, (spec, run))), self.memory)
        self.assertEqual(sorted(tools), ["memory_forget", "memory_read", "memory_write"])
        self.assertTrue(tools["memory_read"][0].read_only)
        tools["memory_write"][1]({"name": "likes short answers", "description": "short answers, point first",
                                  "type": "feedback", "content": "Point first."})
        self.assertIn("Point first.", tools["memory_read"][1]({"name": "likes-short-answers"})["content"])
        self.assertIn("error", tools["memory_write"][1]({"name": "x", "description": "y", "type": "user"}))
        self.assertTrue((self.memory.root / INDEX).exists())


class _Agent:
    def __init__(self) -> None:
        self.read = []

    def _summary_read(self, prior, older, task_id, debug=None):
        self.read.append((prior, [one["content"] for one in older]))
        return f"summary of {len(older)}", ""


class DigestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.memory = MemoryFiles(Path(folder.name) / "memory")
        self.core = _Agent()
        self.lessons = mock.Mock()
        self.lessons.record.return_value = "ex1"
        self.conversation = self.app.conversations.create()
        self.digest = cd.ChatDigest(self.app.conversations, self.core, SimpleNamespace(invoke=lambda *a, **k: None),
                                    self.lessons, self.memory, lambda: self.conversation, lambda: False)

    def say(self, n: int, words: str = "x" * 400) -> None:
        for one in range(n):
            self.app.conversations.add_message(self.conversation, "user" if one % 2 == 0 else "assistant",
                                               f"{one}: {words}")

    def test_too_little_said_nothing_done(self) -> None:
        self.say(4)
        self.assertFalse(self.digest.digest(self.conversation))
        self.assertEqual(self.core.read, [])

    def test_summarised_kept_and_learned_from(self) -> None:
        self.say(cd.KEEP_LAST + 12)
        learned = {"lessons": [{"about": "notifications", "user_said": "this one was useless",
                                "situation": "a recommendation about a trailer", "rule": "When ..., do not ..."}],
                   "memories": [{"name": "seed club", "description": "Mondays", "type": "project", "content": "Mon."}]}
        with mock.patch("personalized_agent.models.answers.read_text", return_value=(json.dumps(learned), False)):
            self.assertTrue(self.digest.digest(self.conversation))
        kept = self.app.conversations.summary(self.conversation)
        self.assertEqual(kept["summary"], "summary of 12")
        self.assertEqual(kept["covered"], 12, "all but the last turns")
        self.lessons.record.assert_called_once()
        self.assertEqual(self.lessons.record.call_args[0][0], "notifications")
        self.lessons.rule_from_the_user.assert_called_once()
        self.assertIn("seed-club", self.memory.names())
        # Said again since, not enough: left; enough: extended from the summary before it.
        self.assertFalse(self.digest.digest(self.conversation))
        self.say(12)
        with mock.patch("personalized_agent.models.answers.read_text", return_value=("{}", False)):
            self.assertTrue(self.digest.digest(self.conversation))
        self.assertEqual(self.core.read[-1][0], "summary of 12", "extended, not rebuilt")

    def test_never_while_the_user_is_talking(self) -> None:
        busy = cd.ChatDigest(self.app.conversations, self.core, None, None, None, lambda: self.conversation,
                             lambda: True)
        self.say(cd.KEEP_LAST + 12)
        with mock.patch.object(busy, "digest") as digest, mock.patch.object(cd, "LOOK_EVERY_SECONDS", 0.01):
            busy.start()
            import time
            time.sleep(0.1)
            busy.stop()
        digest.assert_not_called()


class EveryConversationTests(unittest.TestCase):
    def test_another_conversation_is_given_what_was_said_before(self) -> None:
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        app = agent.app
        side = app.side_conversation()
        app.conversations.add_message(side, "user", "the seed club moved to Tuesday")
        app.conversations.save_summary(side, "m0", 0, "They garden; the seed club matters to them.")
        app.memory_files.write("seed club", "meets Tuesdays", "project", "Tuesdays.")
        other = app.conversations.create()
        given = app._more_context(other)
        self.assertIn("seed-club.md", given, "the memory's index, everywhere")
        self.assertIn("They garden", given)
        self.assertIn("the seed club moved to Tuesday", given)
        own = app._more_context(side)
        self.assertIn("seed-club.md", own)
        self.assertNotIn("WHAT WAS SAID BEFORE", own, "its own conversation is its context already")
        prompt = app.agent._system_prompt("", "", given)
        self.assertIn("YOUR MEMORY", prompt)
        self.assertIn("Rules used:", prompt, "the rules a decision followed, said for the user to judge")


class LearningUpdatesItsFilesTests(unittest.TestCase):
    """2026-11-17: "学习可以和cluade cli一样更新自己的md" - "后台学习改成能读写记忆文件的多轮调用"; and the answer reads
    the files itself, from the index: "记忆检索现在和claude的检索方式一样"."""

    def test_read_then_written_again_whole_in_rounds(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        memory = MemoryFiles(Path(folder.name) / "memory")
        memory.write("seed club", "when the club meets", "project", "The seed club meets Tuesdays.")
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        from personalized_agent.models.base import ToolCall
        rounds = [
            [ToolCall(id="c1", name="memory_read", arguments={"name": "seed-club"})],
            [ToolCall(id="c2", name="memory_write", arguments={
                "name": "seed-club", "description": "when the club meets", "type": "project",
                "content": "The seed club meets Thursdays now (Tuesdays before)."})],
            []]
        asked = []

        def invoke(request, role=None):
            asked.append(request)
            return SimpleNamespace(tool_calls=rounds[len(asked) - 1], raw_content="", text="{}")
        digest = cd.ChatDigest(agent.app.conversations, _Agent(), SimpleNamespace(invoke=invoke), mock.Mock(),
                               memory, lambda: "", lambda: False)
        with mock.patch("personalized_agent.models.answers.read_text", return_value=("{}", False)):
            done = digest.learn([{"role": "user", "content": "the seed club meets on Thursday now"}])
        self.assertEqual(len(asked), 3, "read, written, answered")
        self.assertEqual(sorted(spec.name for spec in asked[0].tools),
                         ["memory_forget", "memory_read", "memory_write"])
        self.assertIn("The seed club meets Tuesdays.", json.dumps(asked[1].messages[2]["content"]),
                      "what was kept, read back to it")
        self.assertIn("Thursdays now", memory.read("seed-club"))
        self.assertEqual(memory.index().count("seed-club.md"), 1, "updated, not kept twice")
        self.assertEqual(done["memories"], 1)
        self.assertIn("memory_read", cd.LEARN_SYSTEM)

    def test_the_answer_reads_them_itself(self) -> None:
        from personalized_agent.agent import memory_files
        self.assertIn("BEFORE YOU ANSWER, read (memory_read)", memory_files.NOTE)
        self.assertFalse(hasattr(memory_files.MemoryFiles, "relevant"), "no search by likeness")


class SummarisedMarkTests(unittest.TestCase):
    def test_the_chat_is_told_and_shows_a_mark_the_model_never_reads(self) -> None:
        """2026-11-17: "做好总结之后要在chat里说明总结已经做好了（总结这个用claude cli 一样的显示)"."""
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        app = agent.app
        side = app.side_conversation()
        for one in range(cd.KEEP_LAST + 12):
            app.conversations.add_message(side, "user" if one % 2 == 0 else "assistant", f"{one}: " + "x" * 400)
        digest = cd.ChatDigest(app.conversations, _Agent(), SimpleNamespace(invoke=lambda *a, **k: None),
                               mock.Mock(), None, lambda: side, lambda: False)
        digest.on_summarised = app._side_compacted
        told = []
        with mock.patch.object(app.side_events, "tell", lambda kind, **data: told.append(kind)), \
                mock.patch("personalized_agent.models.answers.read_text", return_value=("{}", False)):
            self.assertTrue(digest.digest(side))
        [mark] = [one for one in app.conversations.messages(side, limit=None)
                  if (one["metadata"] or {}).get("origin") == "compacted"]
        self.assertEqual((mark["role"], mark["metadata"]["count"], mark["metadata"]["summary"]),
                         ("system", 12, "summary of 12"))
        self.assertIn("compacted", told)


if __name__ == "__main__":
    unittest.main()
