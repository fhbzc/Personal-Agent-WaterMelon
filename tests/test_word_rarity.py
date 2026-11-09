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



class SteeperTests(unittest.TestCase):
    """The user, 2026-11-08: "能压的多点吗" - a word in 7% of what they saw weighed 0.37."""

    def test_a_word_in_one_in_fifteen_now_weighs_a_seventh(self) -> None:
        self.assertLess(rr.weight_of(94, 1417), 0.16)
        self.assertGreater(rr.weight_of(1, 1417), 0.8, "a word seen once still counts nearly whole")
        self.assertEqual(rr.weight_of(1417, 1417), rr.FLOOR)
        self.assertEqual(rr.FLOOR, 0.1)


class CountedAnotherWayTests(_Base):
    def test_weights_counted_the_old_way_are_counted_again_at_once(self) -> None:
        self.rarity.refresh(["tide table"], self.now)
        self.assertFalse(self.rarity.due(["tide table"], self.now + timedelta(hours=2)))
        self.rarity._keep(rr.COUNTED_HOW, "log-share, floor 0.3")
        self.assertTrue(self.rarity.due(["tide table"], self.now + timedelta(hours=2)))


class OneUncommonWordTests(_Base):
    """The user, 2026-11-08, "C": "Claude", "Claude Code", "Anthropic" - words they see all day,
    and nothing else - made a recommendation."""

    def test_common_words_alone_are_no_match_however_many(self) -> None:
        reserve, rule = self.app.reserve, self.app.reserve_rule
        reserve.add("Harbour tables", PAGE, status=rs.READY,
                    keywords={"proper": ["Harbourline", "Tidewell", "Kelpmark"], "zh": [],
                              "en": ["spring tide"]})
        self.db.executemany("INSERT INTO reserve_word_rarity (word, seen, weight) VALUES (?, ?, ?)",
                            [("harbourline", 90, 0.2), ("tidewell", 90, 0.2),
                             ("kelpmark", 90, 0.2), ("spring tide", 1, 0.9)])
        rule._live_at = 0.0
        many = "harbourline tidewell kelpmark " * 3
        self.assertEqual(rule._scored(rule._ready()[0], many)[0], 0.0)
        self.assertFalse(rule.match("x", many, "page"))
        self.assertGreater(rule._scored(rule._ready()[0], many + " spring tide")[0], 0.0,
                           "with one word not common, it counts")

    def test_the_model_making_them_ready_is_told_the_common_words(self) -> None:
        self.db.executemany("INSERT INTO reserve_word_rarity (word, seen, weight) VALUES (?, ?, ?)",
                            [("harbourline", 90, 0.12), ("spring tide", 1, 0.9)])
        self.assertEqual(self.rarity.common(match.COMMON), ["harbourline"])
        self.assertIn("WORDS RARE ON THEIR SCREENS", rs.PREPARE_SYSTEM)
        asked = []
        decider = self.app.monitor_decider
        with mock.patch.object(decider, "_ask", side_effect=lambda system, prompt, *a, **k:
                               asked.append(prompt) or "{}"),                 mock.patch.object(decider, "_tasks", return_value=["Tide study"]):
            self.app.reserve.prepare(decider, self.now, ["a look"])
        self.assertIn("WORDS TOO COMMON ON THEIR SCREENS to set anything off by themselves: harbourline",
                      asked[0])


class TheirOwnLineTests(_Base):
    """The user, 2026-11-07/08: the line their own - about 30 matches a day to the model."""

    def _asked(self, n, days_ago=1, done="the model said no"):
        for one in range(n):
            self.db.execute("INSERT INTO reserve_hits (id, at, reserve_id, title, score, hit, seen_by, "
                            "place, tier, done) VALUES (?, ?, 'r', 't', 4, '[]', 'ocr', 'page', "
                            "'middle', ?)", (f"h{days_ago}-{one}-{done[:3]}",
                                             (self.now - timedelta(days=days_ago)).isoformat(), done))

    def test_starts_at_three_and_moves_once_a_day(self) -> None:
        self.assertEqual(match.line_of(self.db), match.MIDDLE)
        self._read("x", days_ago=1)
        self._asked(60)                           # 60 on the one day they were here: too many
        moved = match.move_line(self.db, 30, self.now)
        self.assertAlmostEqual(moved["line"], match.MIDDLE * match.LINE_STEP, places=3)
        self.assertEqual(moved["asked_a_day"], 60.0)
        self.assertEqual(match.move_line(self.db, 30, self.now + timedelta(hours=3)), {}, "once a day")
        self.assertGreater(match.line_of(self.db), match.MIDDLE)

    def test_too_few_lowers_it_and_days_away_do_not_count(self) -> None:
        self._read("x", days_ago=1)
        self._asked(5)
        moved = match.move_line(self.db, 30, self.now)
        self.assertLess(moved["line"], match.MIDDLE)
        self.assertEqual(moved["asked_a_day"], 5.0, "one day at the computer, not seven")

    def test_what_did_not_reach_the_model_is_not_counted(self) -> None:
        self._read("x", days_ago=1)
        self._asked(30)
        self._asked(100, done="not in what they can see")
        self._asked(100, done="held: a rule of quiet")
        moved = match.move_line(self.db, 30, self.now)
        self.assertEqual(moved["line"], match.MIDDLE, "30 asked: as it should be")

    def test_a_recommendation_marked_bad_raises_it(self) -> None:
        self._read("x", days_ago=1)
        self._asked(30)
        self.db.execute("INSERT INTO monitor_bad (id, at, notification_id, activator_id, said, context) "
                        "VALUES ('b1', ?, 'n', 'reserve', '', '{}')", (self.now.isoformat(),))
        moved = match.move_line(self.db, 30, self.now)
        self.assertAlmostEqual(moved["line"], match.MIDDLE * match.LINE_BAD_STEP, places=3)

    def test_the_rule_and_its_tiers_go_by_it(self) -> None:
        reserve, rule = self.app.reserve, self.app.reserve_rule
        reserve.add("Harbour tables", PAGE, status=rs.READY, keywords=WORDS)
        rule._live_at = 0.0
        self.assertTrue(rule.match("x", "tide table and spring tide", "page"), "4 over 3")
        self.db.execute("INSERT INTO kv (key, value) VALUES (?, '5')", (match.LINE_KEY,))
        rule._live_at = 0.0
        self.assertFalse(rule.match("x", "tide table and spring tide", "page"), "4 under 5")
        self.assertEqual(match.tier_of(5.5, 5.0), "middle")
        self.assertEqual(match.tier_of(6, 5.0), "high", "never under the other by a fifth")
        self.assertEqual(match.tier_of(7, 3.0, 8.0), "middle")
        self.assertEqual(match.tier_of(8, 3.0, 8.0), "high")

    def test_sent_without_a_model_by_its_own_count(self) -> None:
        """The user, 2026-11-08: "2倍太低了 ... 也按照个数来，比如一天平均5个"."""
        self.assertEqual(match.high_of(self.db), match.HIGH)
        self._read("x", days_ago=1)
        self._asked(30)                                      # the other line stays
        self._asked(20, done="sent")                         # middle, sent after the model: not it
        for one in range(12):                                # 12 sent without a model: too many
            self.db.execute("INSERT INTO reserve_hits (id, at, reserve_id, title, score, hit, "
                            "seen_by, place, tier, done) VALUES (?, ?, 'r', 't', 9, '[]', 'ocr', "
                            "'page', 'high', 'sent')", (f"hi{one}",
                                                        (self.now - timedelta(days=1)).isoformat()))
        moved = match.move_line(self.db, 50, self.now, 5)
        self.assertAlmostEqual(moved["high"], match.HIGH * match.LINE_STEP, places=3)
        self.assertEqual(moved["sent_without_a_model_a_day"], 12.0)
        self.assertAlmostEqual(match.high_of(self.db), match.HIGH * match.LINE_STEP, places=3)

    def test_it_never_comes_under_the_other(self) -> None:
        self._read("x", days_ago=1)
        self._asked(500)                                     # the other goes up and up
        for day in range(30):
            match.move_line(self.db, 30, self.now + timedelta(days=day), 5)
        self.assertGreaterEqual(match.high_of(self.db), match.line_of(self.db) * match.HIGH_ABOVE)

    def test_the_second_setting_is_there_at_five(self) -> None:
        self.assertEqual(self.app.config.get("monitor_recommend_direct_per_day"), 5)

    def test_the_setting_is_there_at_thirty(self) -> None:
        self.assertEqual(self.app.config.get("monitor_recommend_asks_per_day"), 30)


if __name__ == "__main__":
    unittest.main()
