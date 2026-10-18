"""What is enjoyed and what is worked at are interests by different rules.
Work read for ten minutes is worth following; a pastime is an interest only
when it lasts - one film recap watched once made a page of an actor's next
film. And a page of something enjoyed recommends more of the pleasure, not
lessons in it: listening to a lot of piano is not wanting to learn it."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

from support import TemporaryAgent

from personalized_agent.heartbeat.connections import recommend
from personalized_agent.heartbeat.reading import log


class TwoRulesTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.db = self.agent.app.db
        self.now = datetime.now(timezone.utc)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _topic(self, ident, label, pieces):
        at = self.now.isoformat()
        self.db.execute("INSERT INTO reading_topics (id, label, centre, pieces, created_at, "
                        "updated_at) VALUES (?, ?, '[0.1, 0.2]', ?, ?, ?)",
                        (ident, label, len(pieces), at, at))
        for n, (days_ago, seconds, purpose) in enumerate(pieces):
            when = (self.now - timedelta(days=days_ago)).isoformat()
            self.db.execute(
                "INSERT INTO reading_pieces (id, first_at, last_at, title, kind, summary, "
                "seconds, engagement, topic_id, created_at, updated_at, purpose) "
                "VALUES (?, ?, ?, ?, 'video', 'A piece.', ?, 1.0, ?, ?, ?, ?)",
                (f"{ident}-{n}", when, when, f"{label} {n}", seconds, ident, when, when,
                 purpose))

    def _found(self):
        return {one["title"]: one for one in recommend.subjects(self.db, now=self.now)
                if one.get("topic_id")}

    def test_one_long_film_recap_is_not_an_interest(self) -> None:
        self._topic("t_recap", "film recap", [(0, 1018, "leisure")])
        self.assertNotIn("film recap", self._found())

    def test_a_pastime_that_lasts_is_one(self) -> None:
        self._topic("t_piano", "piano music", [(9, 300, "leisure"), (4, 300, "leisure"),
                                                (1, 300, "leisure")])
        found = self._found()
        self.assertIn("piano music", found)
        self.assertTrue(found["piano music"]["leisure"])

    def test_but_not_one_that_has_stopped(self) -> None:
        last = recommend.ACTIVE_DAYS + 1      # past the window read as "lately"
        self._topic("t_old", "old show", [(last + 10, 300, "leisure"),
                                           (last + 5, 300, "leisure"), (last, 300, "leisure")])
        self.assertNotIn("old show", self._found(), "nothing of it lately")

    def test_work_read_once_for_ten_minutes_still_is(self) -> None:
        self._topic("t_soil", "soil moisture models", [(0, 660, "work")])
        found = self._found()
        self.assertIn("soil moisture models", found)
        self.assertFalse(found["soil moisture models"]["leisure"])

    def test_read_before_it_was_asked_keeps_the_old_rule(self) -> None:
        self._topic("t_unsaid", "unsaid", [(0, 700, "")])
        self.assertIn("unsaid", self._found())


class APageOfPleasureTests(unittest.TestCase):

    def test_told_more_of_the_pleasure_not_lessons(self) -> None:
        self.assertIn("{purpose}", recommend.PAGE_PLAN)
        self.assertIn("not lessons", recommend.LEISURE_PAGE)
        self.assertIn("piano", recommend.LEISURE_PAGE)
        filled = recommend.PAGE_PLAN.format(
            subjects="- READING/WATCHING: piano music", purpose=recommend.LEISURE_PAGE,
            detail="", known="", shown="", liked="", disliked="", since="", today="",
            broad=1, most=3, english="")
        self.assertIn("THEY ENJOY THIS AS A PASTIME", filled)

    def test_a_page_is_leisure_only_when_all_of_it_is(self) -> None:
        found = [{"key": "topic:a", "kind": "interest", "topic_id": "a", "title": "piano",
                  "leisure": True, "detail": ["x"], "vector": [1.0, 0.0], "anchors": []},
                 {"key": "task:b", "kind": "work", "title": "soil paper",
                  "detail": ["y"], "vector": [0.0, 1.0], "anchors": []}]
        pages = recommend.plan_pages(found, None)
        by_title = {page["title"]: page for page in pages}
        self.assertTrue(by_title["piano"]["leisure"])
        self.assertFalse(by_title["soil paper"]["leisure"])

    def test_the_reading_log_asks_why_each_thing_was_read(self) -> None:
        self.assertIn('"purpose": "work or leisure"', log.EXTRACT)
        json.dumps(log.EXTRACT)


if __name__ == "__main__":
    unittest.main()
