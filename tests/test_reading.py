"""What the user read, kept as a log for Customized Recommendation
(heartbeat/reading): the events put together into pieces, the reading told
from the tools, the thin ones read again from the public web - never an
address that does something when opened - written down in English, and
placed in topics that weigh what the user engaged with, lately."""

from __future__ import annotations

import json
import re
import unittest
from datetime import datetime, timedelta, timezone

from support import TemporaryAgent

from personalized_agent.common.events.models import ListenerEvent
from personalized_agent.heartbeat.reading.log import (UNTIL, clean_title, engagement,
                                                      public_address)
from personalized_agent.models.base import ModelResponse


class _Model:
    """Triage keeps what looks like reading and asks for the thin article
    again; extraction writes a topic from the title."""

    def __init__(self):
        self.asked = []

    def invoke(self, request, role=""):
        prompt = request.messages[0]["content"]
        self.asked.append(prompt)
        ids = re.findall(r"^\[(p\d+)\] ([^\n<(]*)", prompt, re.M)
        if "keep: true when" in prompt:
            return ModelResponse(text=json.dumps({"items": [
                {"id": ident, "keep": "Explorer" not in title,
                 "fetch": "Quantum" in title} for ident, title in ids]}))
        return ModelResponse(text=json.dumps({"pieces": [
            {"id": ident, "kind": "article",
             "topics": ["physics" if "Quantum" in title or "Qubit" in title else "cooking"],
             "entities": [], "points": [f"{title.strip()} says something new"],
             "summary": f"About {title.strip()}"} for ident, title in ids]}))


class _Embedder:
    def embed(self, texts, while_ok=None):
        return [[1.0, 0.0] if "physics" in text else [0.0, 1.0] for text in texts]


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.model = _Model()
        self.fetched = []
        log = self.app.reading
        log.models = self.model
        log.embedder = _Embedder()
        log.fetch = lambda url: self.fetched.append(url) or {
            "text": "The whole article about qubits and error correction. " * 20}
        self.log = log
        self.at = datetime.now(timezone.utc) - timedelta(hours=1)
        self.app.config.set(UNTIL, (self.at - timedelta(minutes=1)).isoformat(),
                            internal=True)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _event(self, kind, minutes, app="chrome", **data):
        self.app.raw_store.add_event(ListenerEvent(
            event_type=kind, source="test", application=app, data=data,
            timestamp=self.at + timedelta(minutes=minutes)))

    def _read_a_morning(self):
        self._event("browser.navigation", 0, url="https://news.example.org/quantum-advance",
                    title="Quantum advance in error correction")
        self._event("screen.text", 1, window_title="Quantum advance in error correction "
                    "- Google Chrome", text="Researchers report a new code.")
        self._event("application.focus_ended", 2, window_title="Quantum advance in error "
                    "correction - Google Chrome", duration_seconds=240)
        self._event("browser.navigation", 5, url="https://recipes.example.com/soup",
                    title="A winter soup")
        self._event("screen.text", 6, window_title="A winter soup - Google Chrome",
                    text="Slow-cooked onions make the base. " * 30)
        self._event("application.focus_ended", 7, app="explorer",
                    window_title="Downloads - File Explorer", duration_seconds=30)


class PiecesFromEventsTests(_Base):

    def test_a_morning_is_kept_as_what_was_read(self) -> None:
        self._read_a_morning()
        report = self.log.catch_up()
        self.assertEqual(report["kept"], 2, "the file manager is not reading")
        pieces = {one["title"]: one for one in self.log.pieces()}
        self.assertEqual(set(pieces), {"Quantum advance in error correction", "A winter soup"})
        quantum = pieces["Quantum advance in error correction"]
        # The page, its window and its screen are one piece: the time its
        # window was in front counts for the page.
        self.assertGreaterEqual(quantum["seconds"], 240)
        self.assertEqual(quantum["topics"], ["physics"])
        self.assertTrue(quantum["points"])
        # Thin on screen and asked for: read again from its address.
        self.assertTrue(quantum["fetched"])
        self.assertEqual(self.fetched, ["https://news.example.org/quantum-advance"])
        self.assertFalse(pieces["A winter soup"]["fetched"])
        # Where the log stands moved to the last event read.
        self.assertGreater(self.app.config.get(UNTIL), self.at.isoformat())

    def test_read_again_it_grows_not_doubles(self) -> None:
        self._read_a_morning()
        self.log.catch_up()
        asked = len(self.model.asked)
        self._event("browser.navigation", 30, url="https://recipes.example.com/soup/",
                    title="A winter soup")
        report = self.log.catch_up()
        self.assertEqual(report["read_again"], 1)
        self.assertEqual(len(self.model.asked), asked, "nothing new to ask about")
        soup = [one for one in self.log.pieces() if one["title"] == "A winter soup"][0]
        self.assertEqual(soup["visits"], 2)

    def test_its_own_windows_and_kept_out_sites_are_not_read(self) -> None:
        self.log.allowed = lambda url: "secret.example" not in url
        self._event("browser.navigation", 0, url="https://secret.example/a", title="Kept out")
        self._event("application.focus_ended", 1, app="PersonalizedAgent",
                    window_title="Personalized Agent", duration_seconds=500)
        self._event("browser.navigation", 2, url="http://127.0.0.1:8765/", title="Overview")
        self.assertEqual(self.log.catch_up().get("kept", 0), 0)
        self.assertEqual(self.log.pieces(), [])


class TopicsTests(_Base):

    def test_pieces_fall_into_topics_weighed_by_engagement(self) -> None:
        self._read_a_morning()
        self._event("browser.navigation", 10, url="https://news.example.org/qubit-chip",
                    title="Qubit chip doubles coherence")
        self.log.catch_up()
        topics = self.log.topics.describe()
        self.assertEqual(sorted(one["label"] for one in topics), ["cooking", "physics"])
        physics = [one for one in topics if one["label"] == "physics"][0]
        self.assertEqual(physics["pieces"], 2)
        self.assertGreater(physics["weight"], 0)

    def test_what_was_read_long_ago_weighs_less(self) -> None:
        from personalized_agent.heartbeat.reading.topics import decayed
        now = datetime.now(timezone.utc)
        self.assertAlmostEqual(decayed(1.0, now.isoformat(), now), 1.0)
        self.assertAlmostEqual(decayed(1.0, (now - timedelta(days=14)).isoformat(), now), 0.5)


class AddressesReadAgainTests(unittest.TestCase):
    """Opening some addresses DOES something - or shows what only the link's
    holder may see: never read again."""

    def test_only_plain_public_pages(self) -> None:
        for url in ("https://news.example.org/quantum-advance",
                    "https://arxiv.org/abs/2410.01234",
                    "https://blog.example.com/post?page=2"):
            self.assertTrue(public_address(url), url)
        for url in (
                "https://list.example.com/unsubscribe?id=1",
                "https://example.com/confirm/abc",
                "https://example.com/verify-email",
                "https://example.com/accept-invite/9",
                "https://example.com/page?token=abc",
                "https://example.com/page?code=1234",
                "https://example.com/s?x=AbCdEf0123456789AbCdEf0123",
                "https://example.com/login",
                "https://example.com/oauth/callback",
                "https://mail.google.com/mail/u/0/#inbox",
                "https://docs.google.com/document/d/1x/edit",
                "https://www.dropbox.com/s/abc/file.pdf",
                "https://xyz.sharepoint.com/sites/team",
                "https://www.notion.so/page-123",
                "http://localhost:3000/", "http://192.168.1.4/", "http://10.0.0.2/a",
                "ftp://example.com/file",
                "https://user:pw@example.com/"):
            self.assertFalse(public_address(url), url)

    def test_a_title_without_its_browser(self) -> None:
        self.assertEqual(clean_title("A winter soup - Google Chrome"), "A winter soup")
        self.assertEqual(clean_title("Paper — Mozilla Firefox"), "Paper")
        self.assertEqual(clean_title("News - Personal - Microsoft​ Edge"), "News")

    def test_engagement(self) -> None:
        self.assertLess(engagement(5, 1, 0), engagement(600, 1, 0))
        self.assertGreater(engagement(600, 3, 800), engagement(600, 1, 0))


class TheAppTests(_Base):

    def test_the_agent_heartbeat_has_it_and_the_page_reads_it(self) -> None:
        from personalized_agent.api.routes import build_routes
        self.assertIs(self.app.heartbeat.reading, self.app.reading)
        self._read_a_morning()
        self.log.catch_up()
        said = build_routes(self.app)[("GET", "/api/reading")](None)
        self.assertEqual(said["count"], 2)
        self.assertEqual(len(said["topics"]), 2)


if __name__ == "__main__":
    unittest.main()
