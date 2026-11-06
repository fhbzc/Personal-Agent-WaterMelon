"""THE RESERVE: what may be recommended, made ready ahead (heartbeat/monitor/reserve.py). The pages
and the work here are made up."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.heartbeat.monitor import reserve as rs
from personalized_agent.heartbeat.monitor.reserve import Reserve, chain_line

PAGE = "https://tides.example/harbour-tables"


class _Model:
    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []

    def invoke(self, request, role="primary"):
        self.asked.append(request)
        answer = self.answers.pop(0) if self.answers else {}
        return SimpleNamespace(text=answer if isinstance(answer, str) else json.dumps(answer),
                               tool_calls=[], usage=None)


class TheReserveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.reserve = self.app.reserve
        self.app.config.set("monitor_recommend_search", False)

    def _read(self, url: str = "", title: str = "") -> None:
        from personalized_agent.heartbeat.reading.log import title_key, url_key
        self.app.db.execute(
            "INSERT INTO reading_pieces (id, first_at, last_at, url, url_key, title, title_key, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"r{url}{title}", "2026-11-05T08:00:00+00:00", "2026-11-05T08:00:00+00:00", url,
             url_key(url), title, title_key(title), "2026-11-05T08:00:00+00:00",
             "2026-11-05T08:00:00+00:00"))

    def _model(self, *answers) -> _Model:
        model = _Model(*answers)
        self.app.monitor_decider.models = model
        return model

    # -- what comes in --
    def test_what_an_issue_left_over_comes_in_and_what_it_printed_does_not(self) -> None:
        printed = {"id": "s1", "title": "Spring tides", "sources": ["https://tides.example/spring"]}
        left = {"id": "s2", "title": "Harbour tables", "detail": "New tables", "sources": [PAGE]}
        self.assertEqual(self.reserve.from_the_issue([printed, left], [printed], set()), 1)
        self.assertEqual([one["title"] for one in self.reserve.items(rs.RAW)], ["Harbour tables"])

    def test_a_story_printed_later_is_not_told(self) -> None:
        left = {"id": "s2", "title": "Harbour tables", "sources": [PAGE]}
        self.reserve.from_the_issue([left], [], set())
        self.reserve.from_the_issue([], [dict(left, id="s9")], set())
        self.assertEqual(self.reserve.items(rs.RAW), [])
        self.assertEqual(self.reserve.items(rs.SEEN)[0]["seen_how"], "newspaper")

    def test_each_thing_comes_in_once_whatever_became_of_it(self) -> None:
        ident = self.reserve.add("Harbour tables", PAGE)
        self.assertTrue(ident)
        self.assertEqual(self.reserve.add("Harbour tables", PAGE), "")
        self.reserve.told(ident)
        self.assertEqual(self.reserve.add("Harbour tables again", PAGE), "")

    def test_what_was_read_does_not_come_in(self) -> None:
        self._read(PAGE)
        self.assertEqual(self.reserve.add("Harbour tables", PAGE), "")

    # -- already seen --
    def test_read_since_it_is_marked_at_the_heartbeat_by_address_or_by_title(self) -> None:
        by_address = self.reserve.add("Harbour tables", PAGE)
        by_title = self.reserve.add("A guide to neap tides", "https://other.example/neap")
        self._read(PAGE)
        self._read("https://mirror.example/copy", "A guide to neap tides")
        self.assertEqual(self.reserve.mark_seen(), 2)
        self.assertEqual(self.reserve.get(by_address)["seen_how"], "address")
        self.assertEqual(self.reserve.get(by_title)["seen_how"], "title")

    def test_looked_at_once_more_just_before_it_is_sent(self) -> None:
        ident = self.reserve.add("Harbour tables", PAGE, status=rs.READY)
        self.assertTrue(self.reserve.still_fresh(ident))
        self._read(PAGE)
        self.assertFalse(self.reserve.still_fresh(ident))
        self.assertEqual(self.reserve.get(ident)["status"], rs.SEEN)

    # -- made ready --
    def test_the_model_makes_it_ready_with_its_angle_and_its_words(self) -> None:
        kept = self.reserve.add("Harbour tables", PAGE, detail="New tables")
        dropped = self.reserve.add("Ten tips for sailors", "https://tips.example/ten")
        model = self._model({"items": [{
            "from": kept, "title": "Harbour tables", "url": PAGE, "detail": "Tables for 2027",
            "angle": "Their tide study needs these", "why": "They have been fitting tide data",
            "proper": ["Thistle Harbour"], "zh": ["潮汐表"], "en": ["tide table"]}]})
        self.assertEqual(self.reserve.prepare(self.app.monitor_decider), 1)
        ready = self.reserve.get(kept)
        self.assertEqual(ready["status"], rs.READY)
        self.assertEqual(ready["keywords"], {"proper": ["Thistle Harbour"], "zh": ["潮汐表"],
                                             "en": ["tide table"]})
        self.assertEqual(ready["angle"], "Their tide study needs these")
        self.assertEqual(self.reserve.get(dropped)["status"], rs.DROPPED)
        asked = model.asked[0]
        self.assertEqual(asked.task, rs.PREPARE_TASK)
        self.assertIn("THE LESSONS", asked.messages[0]["content"])

    def test_no_word_to_be_found_by_and_it_is_not_ready(self) -> None:
        ident = self.reserve.add("Harbour tables", PAGE)
        self._model({"items": [{"from": ident, "title": "Harbour tables", "url": PAGE}]})
        self.assertEqual(self.reserve.prepare(self.app.monitor_decider), 0)
        self.assertEqual(self.reserve.get(ident)["status"], rs.DROPPED)

    def test_an_invented_address_is_left_out(self) -> None:
        self._model({"items": [{"title": "Made up", "url": "https://nowhere.example/invented",
                                "en": ["tide table"]}]})
        self.assertEqual(self.reserve.prepare(self.app.monitor_decider), 0)
        self.assertEqual(self.reserve.items(rs.READY), [])

    def test_not_every_heartbeat(self) -> None:
        now = datetime(2026, 11, 5, 9, tzinfo=timezone.utc)
        self._model({"items": []}, {"items": []})
        self.assertTrue(self.reserve.due(now), "never run, and few ready")
        self.reserve.step(self.app.monitor_decider, now)
        self.assertFalse(self.reserve.due(now + timedelta(minutes=15)))
        self.assertTrue(self.reserve.due(now + timedelta(hours=6)))
        self.reserve.add("Harbour tables", PAGE)
        self.assertFalse(self.reserve.due(now + timedelta(minutes=30)))
        self.assertTrue(self.reserve.due(now + timedelta(hours=1)), "something waits: sooner")

    def test_a_model_that_is_away_is_not_asked_but_what_was_read_is_still_marked(self) -> None:
        ident = self.reserve.add("Harbour tables", PAGE)
        self._read(PAGE)
        model = self._model()
        self.assertEqual(self.reserve.step(self.app.monitor_decider, asks=False), {"seen": 1})
        self.assertEqual(model.asked, [])
        self.assertEqual(self.reserve.get(ident)["status"], rs.SEEN)

    def test_only_a_deep_reserve_stops_it_looking(self) -> None:
        self.assertEqual(rs.MOST_READY, 100)
        now = datetime.now(timezone.utc)
        self.app.db.execute("INSERT INTO kv (key, value) VALUES (?, ?)",
                            (rs.LAST_RUN, (now - timedelta(hours=7)).isoformat()))
        for one in range(rs.MOST_READY - 1):
            self.reserve.add(f"Tide note {one}", f"https://tides.example/{one}", status=rs.READY)
        self.assertTrue(self.reserve.due(now), "99 ready: most of them will never be set off")
        self.reserve.add("Tide note last", "https://tides.example/last", status=rs.READY)
        self.assertFalse(self.reserve.due(now))

    def test_past_its_time_it_sets_nothing_off_and_the_model_is_asked(self) -> None:
        now = datetime.now(timezone.utc)
        kept = self.reserve.add("Harbour tables", PAGE, status=rs.READY, valid_days=3)
        gone = self.reserve.add("A regatta next week", "https://tides.example/regatta",
                                status=rs.READY, valid_days=3)
        self.assertEqual(len(self.reserve.live(now)), 2)
        later = now + timedelta(days=4)
        self.assertEqual(self.reserve.live(later), [])
        self.assertEqual(len(self.reserve.past_its_time(later)), 2)
        self.assertTrue(self.reserve.due(later))
        model = self._model({"items": [], "extend": [{"id": kept, "valid_days": 60}]})
        self.reserve.prepare(self.app.monitor_decider, later)
        self.assertIn("PAST ITS TIME", model.asked[0].messages[0]["content"])
        self.assertEqual([one["id"] for one in self.reserve.live(later)], [kept])
        self.assertEqual(self.reserve.get(gone)["status"], rs.DROPPED)

    def test_past_its_time_it_is_not_sent(self) -> None:
        ident = self.reserve.add("Harbour tables", PAGE, status=rs.READY, valid_days=1)
        self.app.db.execute("UPDATE reserve_items SET expires_at = ? WHERE id = ?",
                            ("2020-01-01T00:00:00+00:00", ident))
        self.assertFalse(self.reserve.still_fresh(ident))

    def test_how_long_is_the_models_within_reason(self) -> None:
        now = datetime(2026, 11, 5, tzinfo=timezone.utc)
        self.assertEqual(rs._until(now, 7), (now + timedelta(days=7)).isoformat())
        self.assertEqual(rs._until(now, "soon"), (now + timedelta(days=rs.VALID_DAYS)).isoformat())
        self.assertEqual(rs._until(now, 9999), (now + timedelta(days=rs.MOST_VALID_DAYS)).isoformat())

    def test_no_answer_to_go_by_and_nothing_is_dropped(self) -> None:
        ident = self.reserve.add("Harbour tables", PAGE)
        self._model("not json at all")
        self.assertEqual(self.reserve.prepare(self.app.monitor_decider), 0)
        self.assertEqual(self.reserve.get(ident)["status"], rs.RAW)

    def test_what_was_kept_before_it_had_a_time_gets_one(self) -> None:
        ident = self.reserve.add("Harbour tables", PAGE, status=rs.READY)
        self.app.db.execute("UPDATE reserve_items SET expires_at = '' WHERE id = ?", (ident,))
        Reserve(self.app.db, self.app.config)
        self.assertTrue(self.reserve.get(ident)["expires_at"])

    # -- the module --
    def test_switched_off_nothing_runs_and_nothing_is_recommended(self) -> None:
        self.app.config.set("module_recommendation_enabled", False)
        model = self._model()
        self.assertEqual(self.reserve.step(self.app.monitor_decider), {})
        self.assertEqual(model.asked, [])
        self.assertEqual(self.reserve.from_the_issue([{"id": "s", "title": "Tide"}], [], set()), 0)
        self.assertFalse(self.app.monitor.allowed({"group": "recommend", "enabled": True}))
        self.assertTrue(self.app.monitor.allowed({"group": "rest", "enabled": True}), "the rest goes on")

    def test_its_calls_are_counted_as_its_own(self) -> None:
        from personalized_agent.common import modules
        self.assertEqual(modules.module_of(rs.PREPARE_TASK), "recommendation")
        self.assertEqual(modules.module_of("monitor_recommend"), "recommendation")

    # -- no limit, and the chain --
    def test_the_days_number_goes_up_with_each(self) -> None:
        monitor = self.app.monitor
        context = {"activator": {"id": "a", "name": "A moment", "group": "recommend"}}
        for number in (1, 2, 3):
            monitor.say("Tide tables", "New tables", context, f"f{number}", content=True,
                        recommended=True)
            self.assertEqual(monitor.recommended_today(), number)
        last = self.app.db.query_one("SELECT metadata FROM notifications ORDER BY created_at DESC, "
                                     "rowid DESC LIMIT 1")
        self.assertEqual(json.loads(last["metadata"])["nth_today"], 3)

    def test_the_lesson_holds_the_whole_chain_but_not_the_screen(self) -> None:
        item = {"id": "rsv1", "source": "what an issue of the newspaper left over",
                "angle": "Their tide study needs these", "why": "They fit tide data",
                "keywords": {"proper": ["Thistle Harbour"], "zh": ["潮汐表"], "en": ["tide table"]}}
        chain = Reserve.chain(item, hit=["潮汐表"], score=7, judged=True)
        line = chain_line({"chain": chain, "reason": "You opened a tide chart", "nth_today": 4})
        for said in ("newspaper left over", "Their tide study", "They fit tide data",
                     "Thistle Harbour", "The words that did: 潮汐表", "Score 7", "judged by the model",
                     "You opened a tide chart", "number 4"):
            self.assertIn(said, line)
        self.assertNotIn("screen", json.dumps(chain))

    def test_a_moments_recommendation_says_what_set_it_off(self) -> None:
        line = chain_line({"monitor": {"activator": "Back from a break"}, "nth_today": 1})
        self.assertIn("Back from a break", line)

    def test_every_answer_to_one_keeps_the_chain_in_its_lesson(self) -> None:
        shown = {"id": "n1", "title": "Tide tables", "message": "New tables", "kind": "proactive",
                 "metadata": {"recommended": True, "nth_today": 2,
                              "chain": {"angle": "Their tide study needs these"}}}
        ident = self.app.learn_answer(shown, "good_to_know")
        row = self.app.db.query_one("SELECT kind, summary FROM lesson_evidence WHERE id = ?", (ident,))
        self.assertEqual(row["kind"], "recommendations")
        self.assertIn("Their tide study needs these", row["summary"])
        self.assertIn("number 2", row["summary"])


class NoLimitADayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.monitor = self.agent.app.monitor
        self.agent.app.config.set("monitor_most_a_day", 1)
        self.agent.app.config.set("monitor_least_gap_minutes", 1)
        self.monitor.latest = {"present": True}
        self.monitor.notifications = SimpleNamespace(_in_quiet_hours=lambda: False)

    def _fired(self, activator) -> None:
        when = datetime.now(timezone.utc) - timedelta(hours=1)
        self.monitor.db.execute(
            "INSERT INTO monitor_activators (id, data, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (activator["id"], json.dumps(activator), when.isoformat(), when.isoformat()))
        self.monitor.db.execute(
            "INSERT INTO monitor_firings (id, at, how, activator_id, acted) VALUES (?, ?, 'tick', ?, 1)",
            (f"f{activator['id']}", when.isoformat(), activator["id"]))

    def test_a_recommendation_is_not_held_by_the_number_a_break_is(self) -> None:
        rest = {"id": "b1", "group": "rest", "most_per_day": 9, "cooldown_minutes": 0}
        self._fired(rest)
        recommend = {"id": "r1", "group": "recommend", "most_per_day": 9, "cooldown_minutes": 0}
        now = datetime.now(timezone.utc)
        self.assertEqual(self.monitor._held(recommend, "tick", now), "")
        self.assertEqual(self.monitor._held(dict(rest, id="b2"), "tick", now),
                         "the most for a day already said")


if __name__ == "__main__":
    unittest.main()
