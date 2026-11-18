"""THE PICTURES THAT WENT WITH CHAT MESSAGES, KEPT AS CONTEXT (agent/chat_pictures.py; the user, 2026-11-17: "每张图chat
进去的时候，都会先让一个模型写一个summary of content ... 模型可以search读原图"). Everything here is made up."""

from __future__ import annotations

import base64
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from support import TemporaryAgent

from personalized_agent.agent import chat_pictures as cp


def _picture() -> dict:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (40, 30), (20, 120, 60)).save(out, "JPEG")
    return {"media_type": "image/jpeg", "data": base64.b64encode(out.getvalue()).decode("ascii")}


class KeptAndDescribedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.asked = []
        models = SimpleNamespace(invoke=lambda request, role=None: self.asked.append((request, role)))
        self.pictures = cp.ChatPictures(Path(folder.name), self.app.conversations, models)
        self.conversation = self.app.conversations.create()

    def test_kept_described_given_later_and_seen_again(self) -> None:
        said = self.app.conversations.add_message(self.conversation, "user", "what is this chart?")
        with mock.patch.object(cp.threading, "Thread") as thread:
            self.assertTrue(self.pictures.keep(said["id"], [_picture()], "what is this chart?"))
        kept = self.app.conversations.message(said["id"])["metadata"]["picture"]
        self.assertTrue((self.pictures.folder / kept["file"]).is_file())
        self.assertIn("not described yet", cp.told(self.app.conversations.message(said["id"])))
        # Described by the main model, on a thread of its own - here at once.
        args = thread.call_args.kwargs["args"]
        with mock.patch("personalized_agent.models.answers.read_text",
                        return_value=("A bar chart of bean sales by month, May highest.", False)):
            self.assertIn("bar chart", self.pictures.describe(*args))
        request, role = self.asked[0]
        self.assertEqual((request.task, role), (cp.TASK, "primary"))
        self.assertEqual(request.messages[0]["content"][1]["type"], "image")
        later = cp.told(self.app.conversations.message(said["id"]))
        self.assertIn("A bar chart of bean sales", later)
        self.assertIn(f'read_chat_picture("{said["id"]}")', later)
        seen = self.pictures.read(said["id"])
        self.assertEqual(seen["_pictures"][0]["media_type"], "image/jpeg")
        self.assertIn("error", self.pictures.read("msg_none"))
        # A later turn is given it beside that message.
        from personalized_agent.agent.core.agent_core import _verbatim
        self.assertIn("A bar chart of bean sales",
                      _verbatim([self.app.conversations.message(said["id"])])[0]["content"])

    def test_a_message_with_none_is_as_it_was(self) -> None:
        said = self.app.conversations.add_message(self.conversation, "user", "hello")
        self.assertFalse(self.pictures.keep(said["id"], [], "hello"))
        self.assertEqual(cp.told(self.app.conversations.message(said["id"])), "hello")

    def test_the_agents_tool_and_the_chat_keeping_it(self) -> None:
        tools = {}
        cp.add_tools(SimpleNamespace(add=lambda spec, run: tools.setdefault(spec.name, (spec, run))), self.pictures)
        self.assertTrue(tools["read_chat_picture"][0].read_only)
        self.assertIs(self.app.two_speed.on_picture.__self__, self.app.chat_pictures)
        self.assertIn("read_chat_picture", [one.name for one in self.app.agent.tools.specs])


if __name__ == "__main__":
    unittest.main()
