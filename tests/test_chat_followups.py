"""Follow-ups in the chat: a question about one reply - a word or phrase in
it, or the whole of it - asked on its own, with nothing of the conversation,
kept under that reply and never added to the conversation."""

from __future__ import annotations

import json
import socket
import unittest
import urllib.error
import urllib.request

from support import TemporaryAgent, keyed

from personalized_agent.models.base import ModelResponse


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ChatFollowUpTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.asked = []

        def answer(request, *args, **kwargs):
            self.asked.append(request)
            return ModelResponse(text="It is a public data service.")

        self.app.models.invoke = answer
        self.port = _free_port()
        self.app.config.set("api_port", self.port)
        from personalized_agent.api.server import ApiServer

        self.server = ApiServer(self.app)
        self.server.start()
        self.base = f"http://127.0.0.1:{self.port}"
        conversation = self.app.conversations.create()
        self.app.conversations.add_message(conversation, "user",
                                           "Where do the citations come from?")
        self.reply = self.app.conversations.add_message(
            conversation, "assistant", "They are fetched from the census API.")
        self.conversation = conversation

    def tearDown(self) -> None:
        self.server.stop()
        self.agent.cleanup()

    def _post(self, path: str, payload: dict):
        request = urllib.request.Request(
            f"{self.base}{path}", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(keyed(self.app, request), timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def _get(self, path: str):
        with urllib.request.urlopen(keyed(self.app, f"{self.base}{path}"), timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_the_question_is_the_whole_prompt(self) -> None:
        said = self._post("/api/chat/followup", {
            "message_id": self.reply["id"], "about": "census API",
            "question": "what is census API"})
        self.assertEqual(said["answer"], "It is a public data service.")
        self.assertEqual(said["about"], "census API")
        request = self.asked[-1]
        # Nothing of the conversation: one message, the question itself.
        self.assertEqual(request.messages,
                         [{"role": "user", "content": "what is census API"}])
        self.assertNotIn("citations", json.dumps(request.messages))
        self.assertNotIn("fetched", request.system or "")

    def test_with_no_question_it_asks_what_it_is(self) -> None:
        self._post("/api/chat/followup", {"message_id": self.reply["id"],
                                          "about": "census API"})
        self.assertEqual(self.asked[-1].messages[0]["content"], "what is census API")
        self._post("/api/chat/followup", {"message_id": self.reply["id"]})
        self.assertEqual(self.asked[-1].messages[0]["content"],
                         "what is They are fetched from the census API.")

    def test_it_is_kept_under_the_reply_and_not_in_the_conversation(self) -> None:
        said = self._post("/api/chat/followup", {
            "message_id": self.reply["id"], "about": "census API"})
        shown = self._get(f"/api/conversation?id={self.conversation}")
        self.assertEqual(len(shown["messages"]), 2)
        self.assertEqual([one["id"] for one in shown["followups"]], [said["id"]])
        self.assertEqual(shown["followups"][0]["message_id"], self.reply["id"])

    def test_it_can_be_deleted(self) -> None:
        said = self._post("/api/chat/followup", {
            "message_id": self.reply["id"], "about": "census API"})
        self.assertTrue(self._post("/api/chat/followup/delete",
                                   {"id": said["id"]})["deleted"])
        shown = self._get(f"/api/conversation?id={self.conversation}")
        self.assertEqual(shown["followups"], [])

    def test_only_a_reply_can_be_followed_up(self) -> None:
        user_said = self.app.conversations.messages(self.conversation)[0]
        with self.assertRaises(urllib.error.HTTPError) as refused:
            self._post("/api/chat/followup", {"message_id": user_said["id"]})
        self.assertEqual(refused.exception.code, 404)

    def test_a_chat_turn_says_which_reply_it_is(self) -> None:
        result = self.app.agent.handle_message("hello")
        stored = self.app.conversations.message(result["message_id"])
        self.assertEqual(stored["role"], "assistant")
        self.assertEqual(stored["content"], result["answer"])


if __name__ == "__main__":
    unittest.main()
