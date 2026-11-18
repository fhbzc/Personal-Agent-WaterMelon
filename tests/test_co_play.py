"""CO-PLAY (heartbeat/monitor/co_play.py; the user, 2026-11-17: "我在看其它所有内容，模型可以根据我的喜好决定要不要和我说话
（可以是推荐内容，但是也可以单纯的和我说话，比如提醒我之类的)" - videos, articles, pages; "5min最多一次"). Made up."""

from __future__ import annotations

import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from support import TemporaryAgent

from personalized_agent.common.notifications import category_of
from personalized_agent.heartbeat.monitor import co_play as cp

NOW = datetime(2026, 11, 17, 20, 0, tzinfo=timezone.utc)


class CoPlayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.asked = []
        self.answer = {"speak": True, "kind": "chat", "title": "That bean trick",
                       "say": "Fun one - the soaking step is what makes it work.", "why": "they like garden videos",
                       "worth": 0.9}
        models = SimpleNamespace(invoke=lambda request, role=None: self.asked.append((request, role)))
        self.company = cp.CoPlay(self.app.db, models, self.app.notifications, self.app.config,
                                 context=lambda: "They grow beans.", style=lambda: "short, casual",
                                 tell=lambda t, m, **k: self.app.monitor.relay("co_play", t, m, **k))

    def event(self, kind: str, at: datetime, **data) -> None:
        self.app.db.execute("INSERT INTO raw_events (id, event_type, source, timestamp, application, data, metadata, "
                            "processed) VALUES (?, ?, ?, ?, ?, ?, '{}', 0)",
                            (str(uuid.uuid4()), kind, kind.split(".")[0], at.isoformat(),
                             str(data.get("application") or ""), json.dumps(data)))

    def watching(self, at: datetime, text: str, title: str = "How to grow beans - YouTube - Firefox") -> None:
        self.event("application.focused", at - timedelta(minutes=4), window_title=title)
        self.event("screen.text", at - timedelta(seconds=10), application="firefox", window_title=title, text=text)

    def tick(self, now=NOW, answer=None):
        with mock.patch("personalized_agent.models.answers.read_text",
                        return_value=(json.dumps(answer or self.answer), False)):
            return self.company.tick(now)

    def test_watching_it_may_speak_its_tokens_apart(self) -> None:
        self.watching(NOW, "How to grow beans in a small garden. Soak them overnight first.")
        made = self.tick()
        self.assertTrue(made)
        request, role = self.asked[-1]
        self.assertEqual((request.task, role), ("co_play", "fast_reply"), "the small model (2026-11-17)")
        self.assertTrue(request.builtin_web, "it may look on the web, for a recommendation's link")
        self.assertTrue(request.web_guard is not None, "guarded: the user's words never leave in a search")
        given = json.loads(request.messages[0]["content"])
        self.assertIn("Soak them overnight", given["on_their_screen"])
        self.assertEqual(given["how_they_talk"], "short, casual")
        self.assertEqual(self.app.notifications.get(made)["metadata"]["co_play"]["kind"], "chat")

    def test_at_most_once_in_five_minutes_and_only_what_changed(self) -> None:
        self.watching(NOW, "How to grow beans in a small garden. Soak them overnight first.")
        self.assertTrue(self.tick())
        later = NOW + timedelta(minutes=3)
        self.watching(later, "A new video about tomatoes and how to stake them in the wind.")
        self.assertIsNone(self.tick(later), "less than five minutes")
        again = NOW + timedelta(minutes=6)
        self.watching(again, "How to grow beans in a small garden. Soak them overnight first!")
        self.assertIsNone(self.tick(again), "the same thing on the screen")

    def test_a_recommendation_with_its_link_and_nothing_said_when_it_says_so(self) -> None:
        self.watching(NOW, "How to grow beans in a small garden. Soak them overnight first.")
        made = self.tick(answer={"speak": True, "kind": "recommend", "title": "A good next one",
                                 "say": "This goes with it:\n- staking beans", "url": "https://example.org/beans",
                                 "worth": 0.85})
        shown = self.app.notifications.get(made)
        self.assertEqual((category_of(shown), shown["metadata"]["url"]), ("Recommended content", "https://example.org/beans"))
        self.watching(NOW + timedelta(minutes=10), "Something else entirely: a talk on cello bowing technique.")
        self.assertIsNone(self.tick(NOW + timedelta(minutes=10), answer={"speak": False}), "it chose to say nothing")

    def test_only_when_sure_it_is_worth_it(self) -> None:
        """2026-11-17: "你要让agent确认有价值才会说"."""
        self.watching(NOW, "How to grow beans in a small garden. Soak them overnight first.")
        self.assertIsNone(self.tick(answer=dict(self.answer, worth=0.6)), "wanting to speak is not enough")
        self.assertIn("SPEAK ONLY WHEN YOU ARE SURE", cp.SYSTEM)

    def test_not_while_writing_editing_or_in_private(self) -> None:
        self.watching(NOW, "Hi Ana, the seed list is attached for the club on Tuesday.", title="Inbox - Gmail - Firefox")
        self.assertIsNone(self.tick(), "a message being written is the side watch's, not company's")
        self.app.config.set("listener_private_mode", True, internal=True)
        self.watching(NOW + timedelta(minutes=10), "How to grow beans in a small garden.")
        self.assertIsNone(self.tick(NOW + timedelta(minutes=10)), "never in private mode")

    def test_its_task_is_known(self) -> None:
        from personalized_agent.common.modules import TASK_MODULE
        self.assertEqual(TASK_MODULE.get(cp.TASK), "co_play", "its tokens counted apart, under the Monitor")
        from personalized_agent.common.modules import BY_KEY
        for key in ("recommendation", "heads_up", "co_play", "learning_questions"):
            self.assertEqual(BY_KEY[key]["parent"], "monitor")
        self.app.config.set("module_co_play_enabled", False, internal=True)
        self.watching(NOW, "How to grow beans in a small garden. Soak them overnight first.")
        self.assertIsNone(self.tick(), "its own switch")


if __name__ == "__main__":
    unittest.main()
