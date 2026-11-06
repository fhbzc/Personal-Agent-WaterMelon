"""WHAT IS ON THE SCREEN AGAINST THE RESERVE (heartbeat/monitor/match.py). Made-up pages."""

from __future__ import annotations

import json
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.heartbeat.monitor import match, reserve as rs
from personalized_agent.platform_layer import browser_extension, native_host

WORDS = {"proper": ["Thistle Harbour"], "zh": ["潮汐表", "涨潮"], "en": ["tide table", "spring tide"]}
PAGE = "https://tides.example/harbour-tables"


class TheWordsTests(unittest.TestCase):
    def test_whole_words_in_latin_letters_anywhere_in_chinese(self) -> None:
        self.assertTrue(match.found("tide", "the tide came"))
        self.assertFalse(match.found("tide", "a tidewater town"))
        self.assertTrue(match.found("潮汐表", "这份潮汐表很新"))

    def test_each_word_counted_once_and_a_name_counts_more(self) -> None:
        score, hit = match.score_of(WORDS, "Tide table, tide table, tide table")
        self.assertEqual((score, hit), (match.CONCEPT_WORTH, ["tide table"]))
        score, _hit = match.score_of(WORDS, "thistle harbour")
        self.assertEqual(score, match.PROPER_WORTH)

    def test_chinese_as_the_ocr_writes_it_with_spaces(self) -> None:
        self.assertEqual(match.score_of(WORDS, "第 0400 条 潮 汐 表 显 示")[1], ["潮汐表"])
        self.assertEqual(match.score_of(WORDS, "the tide\ntable for May")[1], ["tide table"],
                         "wrapped over two lines")

    def test_by_score(self) -> None:
        self.assertEqual(match.tier_of(match.score_of(WORDS, "a tide table")[0]), "low")
        self.assertEqual(match.tier_of(match.score_of(WORDS, "tide table, 涨潮")[0]), "middle")
        self.assertEqual(match.tier_of(match.score_of(
            WORDS, "Thistle Harbour tide table 涨潮 潮汐表")[0]), "high")


class _Picture:
    width, height = 2000, 1000


class _Activity:
    def __init__(self, title="Tides - Browser", image=_Picture()):
        self.title, self.image = title, image

    def get_active_window(self):
        return SimpleNamespace(title=self.title, application="browser", pid=1)

    def front_window_image(self):
        return self.image


class TheMatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.reserve = self.app.reserve
        self.item = self.reserve.add("Harbour tables", PAGE, detail="New tables", status=rs.READY,
                                     angle="Their tide study needs these", why="They fit tide data",
                                     keywords=WORDS)
        self.rule = self.app.reserve_rule
        self.rule.activity = _Activity()
        self.rule._live_at = 0.0
        self.model = SimpleNamespace(asked=[], invoke=self._invoke, answer={"yes": True})
        self.app.monitor_decider.models = self.model
        self.app.monitor.latest = {"present": True}

    def _invoke(self, request, role="primary"):
        self.model.asked.append(request)
        return SimpleNamespace(text=json.dumps(self.model.answer), tool_calls=[], usage=None)

    def _on_screen(self, words: str):
        return mock.patch("personalized_agent.runtime.documents.ocr_picture", return_value=words)

    def _hits(self):
        return match.hits(self.app.db)

    def test_the_rule_is_given_to_the_watcher(self) -> None:
        self.assertIn(self.rule, self.app.triggers.rules)

    def test_new_words_are_looked_at_but_never_the_title_or_a_terminal(self) -> None:
        said = "tide table and spring tide"
        self.assertTrue(self.rule.match("x", said, "page"))
        self.assertTrue(self.rule.match("x", said, "chat"), "WeChat goes as a page does")
        self.assertFalse(self.rule.match(said, "", "page"))
        self.assertFalse(self.rule.match("x", said, "terminal"))
        self.assertFalse(self.rule.match("x", "a tide table", "page"), "low")

    def test_first_it_only_records_and_no_model_is_asked(self) -> None:
        with self._on_screen("tide table and spring tide"):
            said = self.rule.act("Tides", "browser", "", "")
        self.assertEqual(said["recorded"], self.item)
        self.assertEqual(self.model.asked, [])
        self.assertEqual(self.reserve.get(self.item)["status"], rs.READY)
        hit = self._hits()[0]
        self.assertEqual((hit["tier"], hit["done"], hit["seen_by"]), ("middle", "recorded", "ocr"))

    def test_out_of_sight_it_does_not_match(self) -> None:
        with self._on_screen("the top of the page, nothing about the sea"):
            said = self.rule.act("Tides", "browser", "tide table spring tide far below", "")
        self.assertEqual(said["skipped"], "not in what they can see")
        self.assertEqual(self._hits(), [])

    def test_what_they_can_see_not_known_it_does_not_match(self) -> None:
        with self._on_screen(""):
            self.assertEqual(self.rule.act("Tides", "browser", "", "")["skipped"],
                             "what they can see is not known")

    def test_only_the_window_in_front(self) -> None:
        self.rule.activity = _Activity("Something else")
        with self._on_screen("tide table and spring tide") as read:
            self.rule.act("Tides", "browser", "", "")
        read.assert_not_called()

    def test_the_extensions_word_comes_before_the_ocr(self) -> None:
        browser_extension.keep_visible(self.app.data_dir, "Tides", "tide table and spring tide")
        with self._on_screen("nothing") as read:
            self.rule.act("Tides", "browser", "", "")
        read.assert_not_called()
        self.assertEqual(self._hits()[0]["seen_by"], "extension")

    def test_the_extensions_word_on_another_page_is_not_taken(self) -> None:
        browser_extension.keep_visible(self.app.data_dir, "Another page", "tide table spring tide")
        words, how = match.visible_words(self.app.data_dir, _Activity(image=None), "Tides")
        self.assertEqual((words, how), ("", "ocr"))

    def test_a_small_window_is_read_larger(self) -> None:
        from PIL import Image
        with self._on_screen("words") as read:
            match.visible_words(self.app.data_dir, _Activity(image=Image.new("RGB", (1000, 700))), "Tides")
        self.assertEqual(read.call_args.args[0].size, (2000, 1400))

    def test_an_old_word_from_the_extension_is_not_taken(self) -> None:
        browser_extension.keep_visible(self.app.data_dir, "Tides", "tide table spring tide")
        with mock.patch.object(time, "time", return_value=time.time() + match.VISIBLE_FOR + 5), \
                self._on_screen("the ocr's words"):
            self.assertEqual(match.visible_words(self.app.data_dir, _Activity(), "Tides"),
                             ("the ocr's words", "ocr"))

    def _sending(self) -> None:
        self.app.config.set("recommendation_record_only", False)

    def test_in_the_middle_a_model_is_asked_with_no_tools_and_a_short_prompt(self) -> None:
        self._sending()
        with self._on_screen("tide table and spring tide password: hunter2"):
            said = self.rule.act("Tides", "browser", "", "")
        self.assertTrue(said["said"])
        asked = self.model.asked[0]
        self.assertEqual(asked.task, match.JUDGE_TASK)
        self.assertEqual(asked.tools, [])
        self.assertLess(len(asked.system), 200)
        self.assertNotIn("LESSONS", asked.messages[0]["content"])
        self.assertNotIn("hunter2", asked.messages[0]["content"])
        self.assertEqual(self.reserve.get(self.item)["status"], rs.TOLD)

    def test_the_model_saying_no_sends_nothing_and_it_stays_ready(self) -> None:
        self._sending()
        self.model.answer = {"yes": False}
        with self._on_screen("tide table and spring tide"):
            self.rule.act("Tides", "browser", "", "")
        self.assertEqual(self.reserve.get(self.item)["status"], rs.READY)
        self.assertEqual(self._hits()[0]["done"], "the model said no")

    def test_very_high_it_is_sent_without_a_model_says_why_and_keeps_the_chain(self) -> None:
        self._sending()
        with self._on_screen("Thistle Harbour tide table spring tide 涨潮"):
            said = self.rule.act("Tides", "browser", "", "")
        self.assertEqual(self.model.asked, [])
        shown = self.app.notifications.get(said["notification_id"])
        meta = shown["metadata"]
        self.assertEqual(meta["reason"], "Their tide study needs these")
        self.assertTrue(meta["recommended"])
        self.assertEqual(meta["nth_today"], 1)
        chain = meta["monitor"]["context"]["chain"]
        self.assertEqual(chain["reserve_id"], self.item)
        self.assertIn("Thistle Harbour", chain["hit"])
        self.assertEqual(meta["url"], PAGE)

    def test_told_once(self) -> None:
        self._sending()
        with self._on_screen("Thistle Harbour tide table spring tide 涨潮"):
            self.rule.act("Tides", "browser", "", "")
            self.assertEqual(self.rule.act("Tides", "browser", "", "")["skipped"],
                             "not in what they can see")

    def test_read_in_the_meantime_it_is_not_sent(self) -> None:
        self._sending()
        with mock.patch.object(self.reserve, "still_fresh", return_value=False), \
                self._on_screen("Thistle Harbour tide table spring tide 涨潮"):
            self.assertIn("skipped", self.rule.act("Tides", "browser", "", ""))

    def test_a_rule_of_quiet_holds_it(self) -> None:
        self._sending()
        self.app.monitor.latest = {"present": False}
        with self._on_screen("tide table and spring tide"):
            self.assertEqual(self.rule.act("Tides", "browser", "", "")["held"], "the user is away")
        self.assertEqual(self.model.asked, [])

    def test_switched_off_it_is_not_run(self) -> None:
        self.app.config.set("module_recommendation_enabled", False)
        self.assertFalse(self.rule.allowed())


class NoLimitTests(unittest.TestCase):
    def test_no_number_a_day_and_no_gap_for_a_recommendation(self) -> None:
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        monitor = agent.app.monitor
        monitor.latest = {"present": True}
        monitor.notifications = SimpleNamespace(_in_quiet_hours=lambda: False)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        moment = {"id": "r1", "group": "recommend", "most_per_day": 1, "cooldown_minutes": 0}
        monitor.db.execute("INSERT INTO monitor_activators (id, data, created_at, updated_at) VALUES "
                           "(?, ?, ?, ?)", ("r1", json.dumps(moment), now.isoformat(), now.isoformat()))
        monitor.db.execute("INSERT INTO monitor_firings (id, at, how, activator_id, acted) VALUES "
                           "('f1', ?, 'tick', 'r1', 1)", (now.isoformat(),))
        self.assertEqual(monitor._held(moment, "text", now), "")


class TheExtensionsWordTests(unittest.TestCase):
    def test_kept_by_the_host_the_last_only(self) -> None:
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        for words in ("first", "second"):
            said = native_host.answer({"type": "visible", "title": "Tides", "text": words},
                                      agent.app.data_dir, lambda *a: None)
            self.assertTrue(said["ok"])
        self.assertEqual(browser_extension.visible_report(agent.app.data_dir)["text"], "second")


if __name__ == "__main__":
    unittest.main()
