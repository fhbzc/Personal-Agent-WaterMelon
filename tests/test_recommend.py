"""Newspaper (heartbeat/connections/recommend.py, schedule.py):
a page for each thing the user is doing, searched from its detail; each
story checked in code for how near it is to the page - mostly close, a few
broad among them, loose ones not printed, what was read or shown left out;
made at the times of day the user chose; and their say kept."""

from __future__ import annotations

import json
import random
import unittest
from datetime import datetime, timedelta, timezone

from support import TemporaryAgent

from personalized_agent.heartbeat.connections import recommend
from personalized_agent.heartbeat.connections.schedule import (IssueClock, latest_time,
                                                               next_time, parse_times)
from personalized_agent.models.base import ModelResponse


def _story(ident, score, vector, topic="", **more):
    return dict({"id": ident, "title": ident, "score": score, "_vector": vector,
                 "topic_id": topic, "sources": []}, **more)


def _task(db, ident, title, states, days_ago=1, concrete=True, project=None):
    """A task of the state heartbeat, with its states - `days_ago` the latest."""
    at = datetime.now(timezone.utc) - timedelta(days=days_ago)
    ids = []
    for n, (state_title, detail) in enumerate(states):
        sid = f"{ident}_s{n}"
        ids.append(sid)
        db.execute("INSERT INTO states (id, title, detail, source, starts_at, ends_at, status, "
                   "created_at, updated_at) VALUES (?, ?, ?, 'test', ?, ?, 'done', ?, ?)",
                   (sid, state_title, detail, (at - timedelta(minutes=n)).isoformat(),
                    (at - timedelta(minutes=n)).isoformat(), at.isoformat(), at.isoformat()))
    described = {"title": title, "summary": f"About {title}.", "concrete": concrete,
                 "importance": 3, "urgency": 2}
    db.execute("INSERT INTO state_tasks (id, number, members, scope, described, created_at, "
               "updated_at) VALUES (?, 1, ?, 'live', ?, ?, ?)",
               (ident, json.dumps(ids), json.dumps(described), at.isoformat(), at.isoformat()))


def _read(db, ident, topic, title, summary, points, vector, days_ago=1, label=None,
          seconds=900, kind="video"):
    at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    if label and not db.query_one("SELECT 1 FROM reading_topics WHERE id = ?", (topic,)):
        db.execute("INSERT INTO reading_topics (id, label, centre, pieces, created_at, "
                   "updated_at) VALUES (?, ?, ?, 1, ?, ?)",
                   (topic, label, json.dumps(vector), at, at))
    db.execute("INSERT INTO reading_pieces (id, first_at, last_at, url, url_key, title, "
               "title_key, kind, topics, points, summary, seconds, engagement, topic_id, "
               "vector, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?, "
               "2.0, ?, ?, ?, ?)", (ident, at, at, f"https://v.example/{ident}",
                                    f"v.example/{ident}", title, title.lower(), kind,
                                    json.dumps(points), summary, float(seconds), topic,
                                    json.dumps(vector), at, at))


class WhatTheUserIsDoingTests(unittest.TestCase):
    """1. Subjects: the tasks with something in them lately, with their
    detail; what was read and watched, with its titles, summaries and points."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.db = self.agent.app.db

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_an_active_task_with_its_states(self) -> None:
        _task(self.db, "t1", "Revise the replication package",
              [("Rerunning the weighting scripts", "The census fetch step failed twice.")])
        found = recommend.subjects(self.db)
        self.assertEqual([one["title"] for one in found], ["Revise the replication package"])
        detail = "\n".join(found[0]["detail"])
        self.assertIn("The census fetch step failed twice.", detail)

    def test_a_task_let_go_and_an_activity_are_not_pages(self) -> None:
        _task(self.db, "t1", "An old grant report", [("Writing", "Section 2.")], days_ago=40)
        _task(self.db, "t2", "Watch videos between things", [("Video", "A video.")],
              concrete=False)
        self.assertEqual(recommend.subjects(self.db), [])

    def test_what_was_watched_in_detail_not_a_heading(self) -> None:
        _read(self.db, "r1", "topic_p", "Can you learn piano on a digital keyboard?",
              "A teacher compares weighted keys with an acoustic action.",
              ["Weighted keys matter for dynamics"], [0.0, 1.0], label="music")
        found = recommend.subjects(self.db)
        self.assertEqual(found[0]["kind"], "interest")
        line = found[0]["detail"][0]
        for words in ("digital keyboard", "weighted keys with an acoustic action",
                      "Weighted keys matter for dynamics"):
            self.assertIn(words, line)


class AnInterestNotAGlanceTests(unittest.TestCase):
    """A topic is a page only when it is an interest: read on two days, for
    ten minutes in all, or three different things read in it. A glance at a
    product page, a video's first seconds, a fix looked up for an error, are
    not (each made a page of its own the user never wanted)."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.db = self.agent.app.db

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _titles(self):
        return sorted(one["title"] for one in recommend.subjects(self.db))

    def test_one_short_look_is_not_an_interest(self) -> None:
        _read(self.db, "g1", "topic_shop", "Lantern Co. camping lamp, 3 pack",
              "A product page for a camping lamp.", [], [1.0, 0.0], label="shopping", seconds=12)
        _read(self.db, "g2", "topic_sky", "Why Titan's lakes are methane",
              "A short video about Titan.", [], [0.0, 1.0], label="astronomy", seconds=46)
        self.assertEqual(self._titles(), [])

    def test_read_on_two_days_or_for_ten_minutes_is(self) -> None:
        _read(self.db, "d1", "topic_chess", "The Caro-Kann for club players",
              "An opening explained.", [], [1.0, 0.0], label="chess", seconds=90, days_ago=1)
        _read(self.db, "d2", "topic_chess", "Endgames every player should know",
              "Rook endgames.", [], [1.0, 0.0], label="chess", seconds=90, days_ago=3)
        _read(self.db, "l1", "topic_bread", "Sourdough from start to finish",
              "A long baking video.", [], [0.0, 1.0], label="baking", seconds=700)
        self.assertEqual(self._titles(), ["baking", "chess"])

    def test_glances_on_two_days_do_not_add_up_to_an_interest(self) -> None:
        for n, day in enumerate((1, 2)):
            _read(self.db, f"q{n}", "topic_feed", f"Clip {n}", "A clip.", [], [1.0, 0.0],
                  label="clips", seconds=8, days_ago=day)
        self.assertEqual(self._titles(), [])

    def test_an_evening_of_papers_each_open_briefly_is_an_interest(self) -> None:
        # Read between other windows: each under a minute, all on one day.
        for n, seconds in enumerate((26, 52, 30, 8)):
            _read(self.db, f"p{n}", "topic_weights", f"Survey weighting, paper {n}",
                  "A paper on survey weighting.", [], [1.0, 0.0], label="survey methods",
                  seconds=seconds, kind="paper")
        _read(self.db, "a1", "topic_road", "Passing a page on the way",
              "An address passed.", [], [0.0, 1.0], label="roads", seconds=2)
        self.assertEqual(self._titles(), ["survey methods"])

    def test_a_problem_looked_up_is_not_an_interest(self) -> None:
        for day in (1, 2):
            _read(self.db, f"h{day}", "topic_fix", "How to fix 'module not found' in Lumen",
                  "An assistant helping fix an install error.", [], [1.0, 0.0],
                  label="programming", seconds=400, days_ago=day, kind="help")
        self.assertEqual(self._titles(), [])

    def test_the_log_is_told_what_help_is(self) -> None:
        from personalized_agent.heartbeat.reading.log import EXTRACT
        self.assertIn("help", EXTRACT)
        self.assertIn("how to fix", EXTRACT)


class _Embed:
    """Vectors by keyword: piano, food, work."""

    def __call__(self, texts):
        return [self.one(text) for text in texts]

    def embed(self, texts, while_ok=None):
        return self(texts)

    @staticmethod
    def one(text):
        text = text.lower()
        if "piano" in text or "keyboard" in text:
            return [1.0, 0.0, 0.0]
        if "restaurant" in text or "sushi" in text:
            return [0.0, 1.0, 0.0]
        if "price" in text:
            return [0.0, 0.3, 0.95]
        return [0.0, 0.0, 1.0]


class ThePagesTests(unittest.TestCase):

    def _subject(self, key, kind, vector, title=None):
        return {"key": key, "kind": kind, "title": title or key, "text": key,
                "detail": [f"{key} detail"], "vector": vector, "anchors": [vector],
                "topic_id": key if kind == "interest" else ""}

    def test_at_most_eight_things_alike_together(self) -> None:
        found = [self._subject(f"w{n}", "work", [1.0, n / 100.0, 0.0]) for n in range(6)] + \
            [self._subject(f"i{n}", "interest", [0.0, 1.0, n / 100.0]) for n in range(5)]
        pages = recommend.plan_pages(found, None, most=8)
        self.assertEqual(len(pages), 8)
        for page in pages:
            kinds = {one["kind"] for one in page["subjects"]}
            self.assertEqual(len(kinds), 1, "alike kinds share a page first")
        self.assertEqual(sum(len(page["subjects"]) for page in pages), 11, "none dropped")

    def test_every_project_has_a_page_of_its_own(self) -> None:
        # The user's rule (2026-10-18): a page is about a project; ten
        # projects are ten pages, however alike, and what was read goes
        # together and then onto the nearest project's page.
        found = [self._subject(f"project:p{n}", "work", [1.0, n / 100.0, 0.0])
                 for n in range(10)] +             [self._subject(f"i{n}", "interest", [0.0, 1.0, n / 100.0]) for n in range(3)]
        pages = recommend.plan_pages(found, None, most=8)
        self.assertEqual(len(pages), 10)
        for page in pages:
            projects = [one for one in page["subjects"] if one["key"].startswith("project:")]
            self.assertEqual(len(projects), 1, [one["key"] for one in page["subjects"]])
        self.assertEqual(sum(len(page["subjects"]) for page in pages), 13, "none dropped")

    def test_states_no_run_read_go_under_the_nearest_project(self) -> None:
        grant = dict(self._subject("project:grant", "work", [1.0, 0.0, 0.0]),
                     detail=["PROJECT: The coastal-erosion grant",
                             "STATE [2026-10-18 07:00-07:30 UTC, 30 min]: They edit the budget."])
        unsorted = {"key": "recent:states", "kind": "work", "title": "What you did lately",
                    "text": "lately", "topic_id": "",
                    "detail": ["STATE [2026-10-18 08:50-09:00 UTC, 10 min]: They draft the grant aims.",
                               "STATE [2026-10-18 08:40-08:45 UTC, 5 min]: They watch a piano video."],
                    "vector": [0.7, 0.7, 0.0], "anchors": [[0.95, 0.05, 0.0], [0.0, 1.0, 0.0]]}
        pages = recommend.plan_pages([grant, unsorted], None)
        self.assertEqual(len(pages), 1, "no page of their own")
        self.assertEqual(pages[0]["detail"], [
            "PROJECT: The coastal-erosion grant",
            "STATE [2026-10-18 08:50-09:00 UTC, 10 min]: They draft the grant aims.",
            "STATE [2026-10-18 07:00-07:30 UTC, 30 min]: They edit the budget."])

    def test_a_state_of_leisure_goes_only_under_a_project_of_leisure(self) -> None:
        work = dict(self._subject("project:grant", "work", [1.0, 0.0, 0.0]),
                    detail=["PROJECT: The coastal-erosion grant"], leisure=False)
        unsorted = {"key": "recent:states", "kind": "work", "title": "What you did lately",
                    "text": "lately", "topic_id": "", "kinds": ["leisure"],
                    "detail": ["STATE [2026-10-18 21:00-21:40 UTC, 40 min]: They watch a "
                               "documentary on coastal erosion."],
                    "vector": [1.0, 0.0, 0.0], "anchors": [[1.0, 0.0, 0.0]]}
        pages = recommend.plan_pages([work, unsorted], None)
        self.assertEqual(pages[0]["detail"], ["PROJECT: The coastal-erosion grant"],
                         "close in meaning, but leisure is not put on a page of work")

    def test_a_topic_of_a_tasks_work_goes_with_it(self) -> None:
        pages = recommend.plan_pages([self._subject("w", "work", [1.0, 0.0, 0.0]),
                                      self._subject("i", "interest", [0.9, 0.1, 0.0])], None)
        self.assertEqual(len(pages), 1)
        self.assertIn("i detail", pages[0]["detail"])

    def test_one_turned_away_is_left_out(self) -> None:
        pages = recommend.plan_pages([self._subject("i", "interest", [0.0, 1.0, 0.0])], None,
                                     said={"i": {"for": 0, "against": 3}})
        self.assertEqual(pages, [])


class CheckedScoredLaidOutTests(unittest.TestCase):

    def setUp(self) -> None:
        self.pages = [
            {"key": "piano", "title": "Piano", "topic_ids": ["topic_p"],
             "anchors": [[1.0, 0.0, 0.0]], "subjects": []},
            {"key": "food", "title": "Restaurant reviews", "topic_ids": ["topic_f"],
             "anchors": [[0.0, 1.0, 0.0]], "subjects": []}]

    def _story(self, ident, title, page, gain=4, url=None, **more):
        return dict({"id": ident, "title": title, "detail": "", "gain": gain, "page": page,
                     "sources": [url or f"https://n.example/{ident}"]}, **more)

    def test_loosely_connected_is_not_printed(self) -> None:
        """Food prices for someone who watches restaurant reviews."""
        printed, paper = recommend.lay_out(
            [self._story("a", "A sushi counter reviewed", "food"),
             self._story("b", "Food prices fell 0.9%", "food")],
            self.pages, _Embed(), [], [], set())
        self.assertEqual([one["id"] for one in printed], ["a"])
        self.assertEqual(printed[0]["relation"], "close")

    def test_read_shown_or_the_same_story_is_left_out(self) -> None:
        printed, _ = recommend.lay_out(
            [self._story("a", "Piano pedalling explained", "piano", url="https://seen.example/x"),
             self._story("b", "Piano keyboard action compared", "piano"),
             self._story("c", "A sushi omakase reviewed", "food")],
            self.pages, _Embed(), read_vectors=[[1.0, 0.0, 0.0]],
            shown_vectors=[[0.0, 1.0, 0.0]], seen={"seen.example/x"})
        self.assertEqual(printed, [], "read, the same as read, the same as shown")

    def test_only_close_stories_are_printed(self) -> None:
        # The user's decision (2026-10-18): no stories a step to the side.
        class _Lean(_Embed):
            @staticmethod
            def one(text):
                return [0.4, 0.0, 0.9165] if "broad" in text else [1.0, 0.0, 0.0]
        stories = [self._story(f"c{n}", f"close {n}", "piano", gain=5 - n * 0.5)
                   for n in range(4)] + \
            [self._story(f"b{n}", f"broad {n}", "piano") for n in range(3)]
        printed, paper = recommend.lay_out(stories, self.pages, _Lean(), [], [], set())
        order = paper["pages"][0]["stories"]
        self.assertEqual(order, ["c0", "c1", "c2", "c3"])
        self.assertEqual({one["relation"] for one in printed}, {"close"})

    def test_a_story_found_for_one_page_put_where_it_belongs(self) -> None:
        printed, paper = recommend.lay_out(
            [self._story("a", "A new piano method", "food")], self.pages, _Embed(), [], [], set())
        self.assertEqual(printed[0]["page"], "piano")

    def test_the_front_page_the_best_of_each(self) -> None:
        printed, paper = recommend.lay_out(
            [self._story("p1", "Piano one", "piano", gain=5),
             self._story("p2", "Piano two", "piano", gain=4),
             self._story("f1", "Restaurant one", "food", gain=3)],
            self.pages, _Embed(), [], [], set())
        self.assertEqual(paper["front"], ["p1", "f1"])
        self.assertEqual(paper["brief"], ["p2"])
        self.assertEqual([page["key"] for page in paper["pages"]], ["piano", "food"])

    def test_news_of_someone_they_know_is_never_left_out(self) -> None:
        """Far from every page: on a page of its own, not dropped."""
        printed, paper = recommend.lay_out(
            [self._story("p", "Ana Ruiz is now VP Finance", "", kind="person", people=["ana"]),
             self._story("w", "Food prices fell 0.9%", "")],
            self.pages, _Embed(), [], [], set())
        self.assertEqual([one["id"] for one in printed], ["p"])
        self.assertEqual(printed[0]["page"], "people")
        self.assertIn("people", [page["key"] for page in paper["pages"]])

    def test_with_no_page_what_was_found_is_kept(self) -> None:
        printed, paper = recommend.lay_out([self._story("a", "Something", "")], [], _Embed(),
                                           [], [], set())
        self.assertEqual([one["id"] for one in printed], ["a"])
        self.assertEqual(paper["front"], ["a"])

    def test_old_news_is_less_fresh(self) -> None:
        now = datetime.now(timezone.utc)
        self.assertAlmostEqual(recommend.freshness((now - timedelta(days=7)).isoformat(), now),
                               0.5, places=2)
        self.assertEqual(recommend.freshness(""), 1.0)


class SinceTests(unittest.TestCase):

    def test_how_far_an_issue_looks_back(self) -> None:
        now = datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)
        self.assertEqual(recommend.since_of("", now), now - timedelta(days=1))
        self.assertEqual(recommend.since_of((now - timedelta(days=30)).isoformat(), now),
                         now - timedelta(days=7))
        self.assertEqual(recommend.since_of((now - timedelta(days=3)).isoformat(), now),
                         now - timedelta(days=3))


class TheClockTests(unittest.TestCase):

    def test_times_read_from_the_setting(self) -> None:
        self.assertEqual(parse_times("18:30, 8:00 and 08:00; 25:00 x"), ["08:00", "18:30"])
        self.assertEqual(parse_times(""), [])

    def test_the_latest_time_passed_and_the_next(self) -> None:
        tz = timezone(timedelta(hours=-5))
        now = datetime(2026, 10, 14, 9, 15, tzinfo=tz)
        self.assertEqual(latest_time(["08:00", "18:30"], now),
                         datetime(2026, 10, 14, 8, 0, tzinfo=tz))
        early = datetime(2026, 10, 14, 7, 0, tzinfo=tz)
        self.assertEqual(latest_time(["08:00", "18:30"], early),
                         datetime(2026, 10, 13, 18, 30, tzinfo=tz))
        self.assertEqual(next_time(["08:00", "18:30"], now),
                         datetime(2026, 10, 14, 18, 30, tzinfo=tz))

    def test_one_issue_per_time_and_one_for_all_missed(self) -> None:
        tz = timezone(timedelta(hours=-5))

        class _Config(dict):
            def get(self, key, default=None):
                return super().get(key, default)

        class _Round:
            started = 0

            def running(self):
                return False

            def start(self, going=None):
                _Round.started += 1
                return True

        class _Store:
            at = ""

            def briefing(self):
                return {"at": self.at} if self.at else None

        store = _Store()
        # By itself only with its switch on.
        off = IssueClock(_Config(recommend_times="08:00"), _Round(), store)
        self.assertFalse(off.due(datetime(2026, 10, 14, 9, 15, tzinfo=tz)))
        clock = IssueClock(_Config(recommend_times="08:00", recommend_auto_on=True),
                           _Round(), store)
        # Away three days, back at 9:15: one issue - not three.
        store.at = datetime(2026, 10, 11, 8, 5, tzinfo=tz).isoformat()
        now = datetime(2026, 10, 14, 9, 15, tzinfo=tz)
        self.assertTrue(clock.look(now))
        self.assertFalse(clock.look(now + timedelta(minutes=1)), "not twice for one time")
        self.assertEqual(_Round.started, 1)
        # Made: not due again until the next 8:00.
        store.at = datetime(2026, 10, 14, 9, 20, tzinfo=tz).isoformat()
        clock._started_for = None
        self.assertFalse(clock.due(datetime(2026, 10, 14, 23, 0, tzinfo=tz)))
        self.assertTrue(clock.due(datetime(2026, 10, 15, 8, 1, tzinfo=tz)))


class _TopicModel:
    """Answers a topic's search at once, with two stories."""

    def __init__(self):
        self.asked = []

    def invoke(self, request, role=""):
        prompt = request.messages[0]["content"]
        self.asked.append(prompt)
        return ModelResponse(text=json.dumps({"items": [
            {"title": "A new qubit record", "detail": "Coherence doubled.", "gain": 4,
             "relation": "close", "because": "the error-correction video",
             "new_to_you": "the record itself", "published": "2026-10-13",
             "sources": ["https://news.example.org/qubit-record"]},
            {"title": "The same old thing", "detail": "Repeats.", "gain": 0,
             "sources": ["https://news.example.org/old"]}]}))


class TheIssueTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.app.connections_round.wait(10)
        self.agent.cleanup()

    def _a_topic(self):
        at = datetime.now(timezone.utc).isoformat()
        self.app.db.execute("INSERT INTO reading_topics (id, label, centre, pieces, created_at, "
                            "updated_at) VALUES ('topic_q', 'quantum computing', '[1, 0]', 1, "
                            "?, ?)", (at, at))
        self.app.db.execute(
            "INSERT INTO reading_pieces (id, first_at, last_at, url, url_key, title, title_key, "
            "kind, topics, points, summary, seconds, engagement, topic_id, vector, created_at, "
            "updated_at) VALUES ('read_1', ?, ?, 'https://a.example/x', 'a.example/x', 'X', "
            "'x', 'article', '[\"quantum\"]', '[\"Error correction got better\"]', 'X', "
            "900, 1.0, 'topic_q', '[1, 0]', ?, ?)", (at, at, at, at))

    def test_a_page_is_searched_from_its_detail_not_its_heading(self) -> None:
        self._a_topic()
        model = _TopicModel()
        round_ = self.app.connections_round
        round_.models = model
        round_.embedder = None
        pages = round_._pages()
        self.assertEqual([page["title"] for page in pages], ["quantum computing"])
        found = round_._page_branch({"calls": 0, "web": 0, "tokens_in": 0, "tokens_out": 0},
                                    lambda: True, pages, "today",
                                    datetime.now(timezone.utc) - timedelta(days=1))
        asked = model.asked[0]
        # What they read, in detail: the piece's own summary and points.
        self.assertIn("Error correction got better", asked)
        self.assertIn("not from the headings above", asked)
        self.assertEqual(found[0]["page"], "topic:topic_q")
        self.assertEqual(found[0]["because"], "the error-correction video")
        self.assertEqual(found[0]["gain"], 4.0)
        # Laid out on its page, and on the front page.
        items, paper = round_._paper([], found, pages)
        self.assertEqual(items[0]["title"], "A new qubit record")
        self.assertIn(items[0]["id"], paper["front"])

    def test_the_users_say_is_kept_and_what_they_knew_is_known(self) -> None:
        from personalized_agent.api.routes import build_routes
        self._a_topic()
        ident = self.app.connections.save_briefing([], [
            {"id": "know_1", "title": "A new qubit record", "detail": "Coherence doubled.",
             "topic_id": "topic_q", "sources": ["https://news.example.org/qubit-record"]}],
            {"paper": {"front": ["know_1"], "brief": [], "pages": []}})

        class _Req:
            def __init__(self, body):
                self.body = body

            def require(self, key):
                return self.body[key]

        say = build_routes(self.app)[("POST", "/api/recommend/feedback")]
        say(_Req({"issue": ident, "story": "know_1", "action": "knew"}))
        say(_Req({"issue": ident, "story": "know_1", "action": "opened"}))
        self.assertTrue(say(_Req({"issue": ident, "story": "know_1",
                                  "action": "opened"})).get("again"), "opened once")
        # Knowing it is following the topic: both count for it.
        self.assertEqual(recommend.said_of_topics(self.app.db)["topic_q"],
                         {"for": 2, "against": 0})
        self.assertIn("Coherence doubled.", recommend.known_points(self.app.db, "topic_q"))
        # Changed to "not interested": the knowing taken back with it.
        say(_Req({"issue": ident, "story": "know_1", "action": "less"}))
        self.assertEqual(recommend.said_of_topics(self.app.db)["topic_q"],
                         {"for": 1, "against": 1})
        self.assertNotIn("Coherence doubled.",
                         recommend.known_points(self.app.db, "topic_q"))
        from personalized_agent.api.errors import ApiError
        with self.assertRaises(ApiError):
            say(_Req({"issue": ident, "story": "know_1", "action": "love"}))

    def test_a_kept_story_stays_and_keeping_it_is_no_say_on_it(self) -> None:
        self._a_topic()
        ident = self.app.connections.save_briefing([], [
            {"id": "know_1", "title": "A new qubit record", "detail": "Coherence doubled.",
             "topic_id": "topic_q", "sources": ["https://news.example.org/qubit-record"]}],
            {"paper": {"front": ["know_1"], "brief": [], "pages": [
                {"key": "topic:topic_q", "title": "Quantum computing",
                 "stories": ["know_1"]}]}})
        kept = recommend.collect(self.app.db, self.app.connections, ident, "know_1")
        self.assertTrue(kept["ok"])
        # Once: a second time is the first.
        self.assertEqual(recommend.collect(self.app.db, self.app.connections, ident,
                                           "know_1")["id"], kept["id"])
        shelf = recommend.collection(self.app.db)
        self.assertEqual([one["story"]["title"] for one in shelf["items"]],
                         ["A new qubit record"])
        self.assertEqual(shelf["items"][0]["story"]["page_title"], "Quantum computing")
        self.assertEqual(recommend.collected_of(self.app.db, ident), ["know_1"])
        # Not "more like this": nothing counted for its topic.
        self.assertEqual(recommend.said_of_topics(self.app.db).get("topic_q"), None)
        # And the say is still theirs, on the kept story too.
        recommend.record_feedback(self.app.db, self.app.connections, ident, "know_1", "less")
        self.assertEqual(recommend.collection(self.app.db)["says"], {"know_1": "less"})
        # ITS ISSUE GONE (only the newest hundred are kept): the collection's
        # own copy takes the say, and keeps it.
        self.app.db.execute("DELETE FROM connection_briefings WHERE id = ?", (ident,))
        recommend.record_feedback(self.app.db, self.app.connections, ident, "know_1", "more")
        self.assertEqual(recommend.collection(self.app.db)["says"], {"know_1": "more"})
        self.assertEqual(recommend.collection(self.app.db)["items"][0]["story"]["title"],
                         "A new qubit record")
        # Taken out: gone from the collection, the say left as it was.
        recommend.uncollect(self.app.db, "know_1")
        self.assertEqual(recommend.collection(self.app.db)["items"], [])
        self.assertEqual(recommend.says_of(self.app.db, ident), {"know_1": "more"})
        with self.assertRaises(KeyError):
            recommend.collect(self.app.db, self.app.connections, ident, "know_9")

    def test_what_they_knew_makes_a_story_like_it_less_new(self) -> None:
        # What the user already knew is kept with its vector: a story like it
        # is then judged against it - less new, or left out.
        class _Embedder:
            def embed(self, texts, while_ok=None):
                return [[0.0, 1.0] for _ in texts]

        ident = self.app.connections.save_briefing([], [
            {"id": "know_1", "title": "Soup science", "detail": "Onions.", "topic_id": "t2",
             "sources": ["https://food.example/s"]}], {"paper": {"front": ["know_1"],
                                                               "brief": [], "pages": []}})
        recommend.record_feedback(self.app.db, self.app.connections, ident, "know_1",
                                  "knew", embedder=_Embedder())
        row = self.app.db.query_one("SELECT vector, kind FROM reading_pieces")
        self.assertEqual((json.loads(row["vector"]), row["kind"]), ([0.0, 1.0], "known"))
        read_vectors = [json.loads(row["vector"])]
        like_it = {"id": "x", "title": "More soup science", "gain": 5, "page": "t2",
                   "recommended": True, "sources": ["https://food.example/other"]}
        page = {"key": "t2", "title": "Soup", "topic_ids": ["t2"], "anchors": [[0.0, 1.0]]}
        self.assertEqual(recommend.lay_out([like_it], [page], _Embedder(), read_vectors, [],
                                           set())[0], [])

    def test_one_preference_a_story_and_a_change_takes_the_last_back(self) -> None:
        self._a_topic()
        ident = self.app.connections.save_briefing([], [
            {"id": "know_1", "title": "A new qubit record", "detail": "Coherence doubled.",
             "topic_id": "topic_q", "sources": ["https://news.example.org/qubit-record"]}],
            {"paper": {"front": ["know_1"], "brief": [], "pages": []}})
        say = lambda action: recommend.record_feedback(self.app.db, self.app.connections,
                                                       ident, "know_1", action)
        topic = lambda: recommend.said_of_topics(self.app.db).get("topic_q",
                                                                   {"for": 0, "against": 0})
        say("more")
        self.assertEqual(topic(), {"for": 1, "against": 0})
        # The same again: counted once.
        self.assertTrue(say("more").get("unchanged"))
        self.assertEqual(topic(), {"for": 1, "against": 0})
        # Changed: the +1 taken back, the -1 counted.
        say("less")
        self.assertEqual(topic(), {"for": 0, "against": 1})
        self.assertEqual(recommend.says_of(self.app.db, ident), {"know_1": "less"})
        # Already knew: kept as known - and taken back when changed again.
        say("knew")
        known = "SELECT COUNT(*) AS n FROM reading_pieces WHERE kind = 'known'"
        self.assertEqual(self.app.db.query_one(known)["n"], 1)
        say("more")
        self.assertEqual(self.app.db.query_one(known)["n"], 0)
        self.assertEqual(topic(), {"for": 1, "against": 0})
        # Opened is not a preference: it stands beside one.
        say("opened")
        self.assertEqual(recommend.says_of(self.app.db, ident), {"know_1": "more"})

    def test_the_issue_is_no_longer_a_state_heartbeat_step(self) -> None:
        self.assertNotIn("connections", self.app.state_heartbeat._steps_left())
        self.assertIsNotNone(self.app.issue_clock)


if __name__ == "__main__":
    unittest.main()
