"""WHAT WAS ON THE SCREEN, BY TIME (agent/screen_history.py): the chat may ask, and is not given it.
Made-up windows."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.agent import screen_history as sh
from personalized_agent.scheduler.core import zones

NOW = datetime(2026, 3, 11, 18, 0, tzinfo=timezone.utc)


def clock(moment: datetime) -> str:
    return zones.wall_of(moment, zones.here()).strftime("%Y-%m-%d %H:%M")


class TheScreenByTimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.db = self.app.db
        self.rows = 0

    def _event(self, minutes_ago, kind, title, application="firefox.exe", **data):
        self.rows += 1
        self.db.execute(
            "INSERT INTO raw_events (id, event_type, source, timestamp, application, data, metadata, "
            "processed) VALUES (?, ?, 'test', ?, ?, ?, '{}', 0)",
            (f"e{self.rows}", kind, (NOW - timedelta(minutes=minutes_ago)).isoformat(), application,
             json.dumps(dict(data, window_title=title))))

    def _front(self, minutes_ago, title, **data):
        self._event(minutes_ago, "application.focused", title, **data)

    def _read(self, minutes_ago, title, text, **data):
        self._event(minutes_ago, "screen.text", title, text=text, **data)

    def _look(self, **args):
        listening = SimpleNamespace(get=lambda _name, _default=None: True, lines=self.app.config.lines)
        return sh.look(self.db, listening, args, NOW)

    def test_the_last_minutes_when_no_time_is_given(self) -> None:
        self._front(30, "Long before")
        self._front(4, "Harbour calendar")
        self._read(4, "Harbour calendar", "Guests: Ada Marlow")
        said = self._look()
        self.assertEqual([one["title"] for one in said["windows"]], ["Harbour calendar"])
        self.assertEqual(said["read"][0]["text"], "Guests: Ada Marlow")

    def test_a_stretch_on_the_users_clock(self) -> None:
        self._front(120, "Too early")
        self._front(60, "In the stretch")
        self._front(5, "Too late")
        said = self._look(from_local=clock(NOW - timedelta(minutes=90)),
                          until_local=clock(NOW - timedelta(minutes=30)))
        self.assertEqual([one["title"] for one in said["windows"]], ["In the stretch"])

    def test_oldest_first_a_window_said_once_until_it_changes(self) -> None:
        for minutes, title in ((6, "One"), (5, "One"), (4, "Two"), (3, "One")):
            self._front(minutes, title)
        self.assertEqual([one["title"] for one in self._look()["windows"]], ["One", "Two", "One"])

    def test_by_words_in_the_title_or_the_text(self) -> None:
        self._read(3, "Tide tables", "low water")
        self._read(2, "Notes", "the 潮汐 at noon")
        self._read(1, "Other", "nothing here")
        said = self._look(words="潮汐")
        self.assertEqual([one["title"] for one in said["read"]], ["Notes"])
        self.assertEqual([one["title"] for one in self._look(words="TIDE")["read"]], ["Tide tables"])

    def test_the_latest_text_is_kept_when_there_is_too_much(self) -> None:
        for minutes in range(9, 0, -1):
            self._read(minutes, f"Page {minutes}", "x" * 2000)
        read = self._look()["read"]
        self.assertEqual(read[-1]["title"], "Page 1")
        self.assertLessEqual(sum(len(one["text"]) for one in read), sh.MOST_TEXT)
        self.assertEqual(read, sorted(read, key=lambda one: one["at"]))

    def test_the_same_text_is_given_once(self) -> None:
        self._read(3, "Page", "the same")
        self._read(2, "Page", "the same")
        self.assertEqual(len(self._look()["read"]), 1)

    def test_a_window_excluded_since_is_left_out(self) -> None:
        self._front(3, "Harbour Bank - savings")
        self._read(3, "Harbour Bank - savings", "balance")
        said = self._look()
        self.assertEqual((said["windows"], said["read"]), ([], []))

    def test_this_applications_own_is_left_out(self) -> None:
        self._read(3, "WaterMelon - Google Chrome", "its own page")
        self._front(2, "Watermelon", application="python.exe", pid=str(sh.os.getpid()))
        said = self._look()
        self.assertEqual((said["windows"], said["read"]), ([], []))

    def test_text_showing_a_password_is_left_out(self) -> None:
        self._read(3, "Sign in", "secret words")
        with mock.patch.object(sh, "shows_credentials", side_effect=lambda text: "secret" in text):
            self.assertEqual(self._look()["read"], [])

    def test_nothing_with_the_listener_off(self) -> None:
        self._front(3, "Page")
        config = mock.Mock()
        config.get.return_value = False
        self.assertIn("error", sh.look(self.db, config, {}, NOW))

    def test_a_time_it_cannot_read_is_said(self) -> None:
        self.assertIn("error", self._look(from_local="this morning"))
        self.assertIn("error", self._look(from_local=clock(NOW), until_local=clock(NOW)))

    def test_nothing_found_says_so(self) -> None:
        self.assertEqual(self._look()["said"], "nothing recorded for this time")

    def test_the_chat_has_it_and_is_not_given_it(self) -> None:
        tools = {one.name: one for one in self.app.agent.tools.specs}
        self.assertTrue(tools["screen_history"].read_only)
        from personalized_agent.agent.front_screen import front_note
        self._front(3, "Harbour calendar")
        self._read(3, "Harbour calendar", "Guests: Ada Marlow")
        self._front(1, "Tide tables")
        self.assertNotIn("Ada Marlow", front_note(self.db, self.app.config))


if __name__ == "__main__":
    unittest.main()
