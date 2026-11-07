"""HOW RARE A TRIGGER WORD IS FOR THIS USER (heartbeat/monitor/rarity.py). Made-up screens."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.heartbeat.monitor import match, rarity as rr, reserve as rs

WORDS = {"proper": [], "zh": [], "en": ["tide table", "spring tide"]}
PAGE = "https://tides.example/harbour-tables"


class TheWeightTests(unittest.TestCase):
    def test_a_word_never_seen_weighs_all_one_in_everything_the_least(self) -> None:
        self.assertEqual(rr.weight_of(0, 500), 1.0)
        self.assertEqual(rr.weight_of(500, 500), rr.FLOOR)

    def test_the_more_seen_the_less_it_weighs(self) -> None:
        self.assertGreater(rr.weight_of(5, 500), rr.weight_of(25, 500))
        self.assertGreater(rr.weight_of(25, 500), rr.weight_of(125, 500))

    def test_few_documents_say_nothing(self) -> None:
        self.assertEqual(rr.weight_of(10, rr.LEAST_DOCUMENTS - 1), 1.0)

    def test_as_the_words_are_matched(self) -> None:
        texts = [match.folded("the tide table"), match.folded("a tidewater town"),
                 match.folded("潮 汐 表")]
        self.assertEqual(rr.seen_in("tide", texts), 1, "whole words")
        self.assertEqual(rr.seen_in("Tide Table", texts), 1)
        self.assertEqual(rr.seen_in("潮汐表", texts), 1)


class TheScoreTests(unittest.TestCase):
    def test_a_word_counts_by_its_weight(self) -> None:
        said = match.score_of(WORDS, "tide table and spring tide", {"tide table": 0.5})
        self.assertEqual(said[0], match.CONCEPT_WORTH * 0.5 + match.CONCEPT_WORTH)

    def test_without_weights_as_it_was(self) -> None:
        self.assertEqual(match.score_of(WORDS, "tide table")[0], match.CONCEPT_WORTH)

    def test_repeated_on_a_page_it_counts_once(self) -> None:
        self.assertEqual(match.score_of(WORDS, "tide table " * 20, {"tide table": 0.5})[0], 1.0)


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.db = self.app.db
        self.rarity = self.app.reserve.rarity
        self.now = datetime.now(timezone.utc)
        self.rows = 0

    def _read(self, text, handle=1, days_ago=0, title="Tides"):
        self.rows += 1
        at = (self.now - timedelta(days=days_ago)).isoformat()
        self.db.execute("INSERT INTO raw_events (id, timestamp, source, event_type, application, data) "
                        "VALUES (?, ?, 'screen', 'screen.text', 'Browser', ?)",
                        (f"e{self.rows}", at, json.dumps({"window_title": title, "handle": handle,
                                                          "text": text})))


class TheDocumentsTests(_Base):
    def test_a_window_on_a_day_however_often_it_was_read(self) -> None:
        for _ in range(50):
            self._read("the tide table")
        self._read("the tide table", days_ago=1)
        self._read("something else", handle=2)
        texts = rr.documents(self.db, (self.now - timedelta(days=rr.DAYS)).isoformat())
        self.assertEqual(len(texts), 3)
        self.assertEqual(rr.seen_in("tide table", texts), 2)

    def test_what_they_read_counts_too(self) -> None:
        at = self.now.isoformat()
        self.db.execute("INSERT INTO reading_pieces (id, first_at, last_at, url, url_key, title, "
                        "title_key, summary, created_at, updated_at) VALUES ('r1', ?, ?, ?, ?, ?, ?, "
                        "?, ?, ?)", (at, at, PAGE, PAGE, "Harbour notes", "harbour notes",
                                     "On the spring tide", at, at))
        texts = rr.documents(self.db, (self.now - timedelta(days=1)).isoformat())
        self.assertEqual(rr.seen_in("spring tide", texts), 1)

    def test_nothing_older_is_counted(self) -> None:
        self._read("the tide table", days_ago=rr.DAYS + 1)
        self.assertEqual(rr.documents(self.db, (self.now - timedelta(days=rr.DAYS)).isoformat()), [])


class TheStepTests(_Base):
    def _plenty(self, common=60):
        for one in range(common):
            self._read("the tide table", handle=one + 1)

    def test_kept_and_said(self) -> None:
        self._plenty()
        self.assertEqual(self.rarity.refresh(["Tide Table", "spring tide"], self.now), 60)
        weights = self.rarity.weights()
        self.assertEqual(weights["tide table"], rr.FLOOR)
        self.assertEqual(weights["spring tide"], 1.0)
        said = self.rarity.described()
        self.assertEqual(said["documents"], 60)
        self.assertTrue(said["at"])

    def test_due_once_a_day_and_soon_for_new_words(self) -> None:
        self.assertFalse(self.rarity.due([], self.now), "no words: nothing to count")
        self.assertTrue(self.rarity.due(["tide table"], self.now))
        self.rarity.refresh(["tide table"], self.now)
        later = self.now + timedelta(minutes=30)
        self.assertFalse(self.rarity.due(["tide table", "new word"], later), "not at every step")
        self.assertTrue(self.rarity.due(["tide table", "new word"], self.now + timedelta(hours=1)))
        self.assertFalse(self.rarity.due(["tide table"], self.now + timedelta(hours=2)))
        self.assertTrue(self.rarity.due(["tide table"], self.now + timedelta(hours=24)))

    def test_the_heartbeats_step_counts_what_is_ready(self) -> None:
        self._plenty()
        reserve = self.app.reserve
        reserve.add("Harbour tables", PAGE, status=rs.READY, keywords=WORDS)
        said = reserve.step(self.app.monitor_decider, self.now, asks=False)
        self.assertEqual(said["rarity_documents"], 60)
        self.assertEqual(reserve.rarity.weights()["tide table"], rr.FLOOR)
        self.assertNotIn("rarity_documents", reserve.step(self.app.monitor_decider, self.now,
                                                          asks=False))

    def test_the_rule_goes_by_it(self) -> None:
        self._plenty()
        reserve, rule = self.app.reserve, self.app.reserve_rule
        reserve.add("Harbour tables", PAGE, status=rs.READY, keywords=WORDS)
        rule._live_at = 0.0
        self.assertTrue(rule.match("x", "tide table and spring tide", "page"), "before it is counted")
        reserve.rarity.refresh(reserve.trigger_words(), self.now)
        rule._live_at = 0.0
        self.assertFalse(rule.match("x", "tide table and spring tide", "page"),
                         "a word seen all day adds little")

    def test_the_debugger_shows_it(self) -> None:
        from personalized_agent.debugger.recommendation import report
        self._plenty()
        self.rarity.refresh(["tide table"], self.now)
        said = report(self.app)
        self.assertEqual(said["rarity"]["weights"]["tide table"], rr.FLOOR)
        self.assertEqual(said["rarity"]["documents"], 60)


if __name__ == "__main__":
    unittest.main()
