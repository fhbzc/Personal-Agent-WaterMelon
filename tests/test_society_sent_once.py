"""ONE MESSAGE, SENT ONCE (2026-11-15): a message arrived twice - the same words, two ids on the server - one
draft sent twice, a Send pressed twice. A draft is taken for sending before it goes, so a second Send finds it
taken; what did not go is a draft again; one the application stopped in the middle of is said to perhaps have
gone; and the agent does not send the very words it sent the same agent a moment ago."""
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from personalized_agent.society import service as society_service
from personalized_agent.society.client import SocietyClient, SocietyError

from test_society import _join, _server, _service


class SentOnceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        here = Path(self._dir.name)
        self.transport, self.server = _server()
        self.mira = _service(here / "mira", self.transport, {"name": "Mira Holt"})
        self.theo = _service(here / "theo", self.transport, {"name": "Theo Brandt"})
        _join(self.server, self.mira, "Mira Holt")
        self.theo_id = _join(self.server, self.theo, "Theo Brandt")

    def tearDown(self) -> None:
        for one in (self.mira, self.theo):
            one.db.close()
        self._dir.cleanup()

    def _status(self, draft_id: str) -> str:
        return self.mira.db.query_one("SELECT status FROM society_messages WHERE id = ?", (draft_id,))["status"]

    def test_a_second_send_while_the_first_goes_sends_nothing(self) -> None:
        draft = self.mira.write_draft(self.theo_id, "Tea at 4?", "Tomorrow", written_by="agent")
        real = SocietyClient.inbox_send
        second = {}

        as_read = self.mira.db.query_one("SELECT * FROM society_messages WHERE id = ?", (draft["id"],))

        def pressed_again(client, *args, **kwargs):
            # The second press arrives while the first is on its way.
            try:
                self.mira.send_draft(draft["id"], draft["hash"])
            except ValueError as exc:
                second["said"] = str(exc)
            # And one that read the draft before the first took it - two at the same moment.
            try:
                self.mira._deliver(as_read, "user")
            except ValueError as exc:
                second["at_once"] = str(exc)
            return real(client, *args, **kwargs)

        with mock.patch.object(SocietyClient, "inbox_send", pressed_again):
            sent = self.mira.send_draft(draft["id"], draft["hash"])
        self.assertEqual(sent["status"], "sent")
        self.assertEqual(second.get("said"), society_service.BEING_SENT)
        self.assertEqual(second.get("at_once"), society_service.BEING_SENT)
        self.assertEqual(self.theo.collect()["collected"], 1, "one message reached them")
        with self.assertRaises(ValueError):
            self.mira.send_draft(draft["id"], draft["hash"])          # pressed again after: nothing

    def test_what_did_not_go_is_a_draft_again(self) -> None:
        draft = self.mira.write_draft(self.theo_id, "Tea at 4?", written_by="agent")

        def refused(client, *args, **kwargs):
            raise SocietyError(500, "the server is down")

        with mock.patch.object(SocietyClient, "inbox_send", refused):
            with self.assertRaises(SocietyError):
                self.mira.send_draft(draft["id"], draft["hash"])
        self.assertEqual(self._status(draft["id"]), "draft")
        self.assertEqual(self.mira.send_draft(draft["id"], draft["hash"])["status"], "sent", "sent once it can")
        self.assertEqual(self.theo.collect()["collected"], 1)

    def test_one_the_application_stopped_in_may_have_gone(self) -> None:
        draft = self.mira.write_draft(self.theo_id, "Tea at 4?", written_by="agent")
        long_ago = datetime.fromtimestamp(time.time() - society_service.SENDING_STUCK_SECONDS - 60,
                                          timezone.utc).isoformat(timespec="seconds")
        self.mira.db.execute("UPDATE society_messages SET status = 'sending', updated_at = ? WHERE id = ?",
                             (long_ago, draft["id"]))
        going = self.mira.write_draft(self.theo_id, "And at 5?", written_by="agent")
        self.mira.db.execute("UPDATE society_messages SET status = 'sending', updated_at = ? WHERE id = ?",
                             (datetime.now(timezone.utc).isoformat(timespec="seconds"), going["id"]))
        self.assertEqual(self.mira.unstick_sending(), 1)
        row = self.mira.db.query_one("SELECT status, error FROM society_messages WHERE id = ?", (draft["id"],))
        self.assertEqual((row["status"], row["error"]), ("draft", society_service.MAY_HAVE_GONE))
        self.assertEqual(self._status(going["id"]), "sending", "one still on its way is let be")
        # Sent after all, the warning goes with it.
        redone = self.mira.messages("drafts")[0]
        self.mira.send_draft(redone["id"], society_service.content_hash(redone))
        self.assertEqual(self.mira.db.query_one("SELECT error FROM society_messages WHERE id = ?",
                                                (draft["id"],))["error"], "")

    def test_the_agent_does_not_send_the_same_words_twice(self) -> None:
        self.mira.put_in_group("reply", peer=self.theo_id)
        self.mira.agent_send(self.theo_id, "Thanks - I will let you know.", answering=True)
        with self.assertRaises(PermissionError) as said:
            self.mira.agent_send(self.theo_id, "Thanks -  I will let you know. ", answering=True)
        self.assertEqual(str(said.exception), society_service.SAID_ALREADY)
        self.mira.agent_send(self.theo_id, "One more thing: the room is 4B.", answering=True)
        self.assertEqual(self.theo.collect()["collected"], 2)
        self.assertEqual(len(self.mira.messages("drafts")), 0, "the refused one left no draft behind")


if __name__ == "__main__":
    unittest.main()
