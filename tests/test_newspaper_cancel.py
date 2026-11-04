"""CANCEL WHILE AN ISSUE IS MADE (the user, 2026-11-03: "在generate的时候我可以点
cancel, 这样你就当什么没发生过，不generate新的issue"): no issue kept, the
pictures fetched for it alone deleted, those kept before left; once the issue
is saved, Cancel is refused. Everything here is made up."""

from __future__ import annotations

import threading
import unittest

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.heartbeat.connections import avatars
from personalized_agent.heartbeat.connections.round import Cancelled

PNG = b"\x89PNG\r\n\x1a\n" + b"made-up picture"


class CancelTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.round = self.app.connections_round
        self.round.embedder = None
        self.store = self.app.connections
        self.reached = threading.Event()

    def tearDown(self) -> None:
        self.round.wait(10)
        self.agent.cleanup()

    def _stories(self, *_args, **_kwargs):
        return [{"title": "A new tomato variety for cold springs", "summary": "Bred to set fruit early.",
                 "url": "https://example.org/tomato", "kind": "web", "page": "special", "gain": 3.0,
                 "recommended": True, "importance": 3}]

    def _pictured_until_cancelled(self, items, going):
        """A picture fetched for the issue, then held until Cancel is pressed."""
        name = self.round._keep_picture("https://example.org/tomato.png", page=False,
                                        get=lambda url: (PNG, "image/png"), avatars=avatars)
        self.picture = name
        self.reached.set()
        for _ in range(200):
            if not going():
                break
            threading.Event().wait(0.05)
        return items

    def test_cancelled_nothing_is_kept(self) -> None:
        old = self.store.save_briefing_picture(b"\x89PNG\r\n\x1a\nkept before", "png")
        issues = len(self.store.briefings())
        self.round._page_branch = self._stories
        self.round._pictured = self._pictured_until_cancelled
        self.assertTrue(self.round.start_special("Tomatoes", "Tomatoes").get("started"))
        self.assertTrue(self.reached.wait(20))
        folder = self.store.folder / "briefing"
        self.assertTrue((folder / self.picture).exists())
        said = self.round.cancel()
        self.assertTrue(said.get("cancelled"), said)
        self.round.wait(20)
        self.assertEqual(len(self.store.briefings()), issues, "no issue kept")
        self.assertEqual((self.round.progress["running"], self.round.progress["stage"]),
                         (False, "cancelled"))
        self.assertFalse((folder / self.picture).exists(), "its picture deleted")
        self.assertTrue((folder / old).exists(), "a picture kept before stays")
        # Nothing going now: nothing to cancel.
        self.assertIn("error", self.round.cancel())

    def test_once_saved_it_is_made(self) -> None:
        self.round._begin(lambda: True)
        self.round._thread = type("Alive", (), {"is_alive": lambda self: True,
                                                "join": lambda self, timeout=None: None})()
        self.round._about_to_save()
        self.assertEqual(self.round.cancel()["error"], "The issue is already made.")
        self.round._begin(lambda: True)
        self.assertTrue(self.round.cancel()["cancelled"])
        with self.assertRaises(Cancelled):
            self.round._about_to_save()
        self.round._thread = None


if __name__ == "__main__":
    unittest.main()
