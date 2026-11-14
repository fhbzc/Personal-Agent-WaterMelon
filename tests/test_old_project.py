"""BACK TO AN OLD PROJECT (the user, 2026-11-07): "New work begins" taken out; back for a while at a
project left for days, the model looks at the screen and, if it is that project, says what was done,
what is left and where its things are. The two lines come from the user's own weeks. And what of a
piece of work is kept where, written with its tasks."""

from __future__ import annotations

import base64
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.heartbeat.monitor import signals as sg
from personalized_agent.heartbeat.monitor.operations import ProjectMatcher
from personalized_agent.heartbeat.states import task_projects as tp

#: A one-pixel picture.
PIXEL = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


class NewWorkIsGoneTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.monitor = self.agent.app.monitor

    def test_it_is_not_built_in_and_not_an_event(self) -> None:
        listed = [one["builtin"] for one in self.monitor.store.list()]
        self.assertNotIn("new_work", listed)
        self.assertIn("old_project", listed)
        self.assertNotIn("new_work", sg.EVENTS)
        self.assertIn("old_project", sg.EVENTS)
        old = next(one for one in self.monitor.store.list() if one["builtin"] == "old_project")
        self.assertEqual(old["cooldown_minutes"], 5, "the user, 2026-11-07: five minutes between looks")

    def test_an_installed_copy_is_taken_away_once(self) -> None:
        from personalized_agent.heartbeat.monitor.builtins import GONE
        ident, _warnings = self.monitor.store.add(
            {"name": "New work begins", "group": "recommend", "purpose": "x",
             "when": {"event": "back"},
             "then": [{"do": "ask_model", "handler": "recommend", "brief": "x"}]},
            "built in", "built in", builtin="new_work")
        self.monitor._write_builtins()
        self.assertNotIn(ident, [one["id"] for one in self.monitor.store.list()])
        self.assertEqual(self.monitor.store.changes(ident)[0]["why"], GONE["new_work"])

    def test_no_limit_a_day_only_five_minutes_between(self) -> None:
        """The user, 2026-11-07: "我觉得不要设置限制，冷却5min就行"."""
        old = next(one for one in self.monitor.store.list() if one["builtin"] == "old_project")
        now = datetime.now(timezone.utc).astimezone().replace(hour=12, minute=0, second=0,
                                                             microsecond=0)
        for one in range(7):
            self.monitor.db.execute(
                "INSERT INTO monitor_firings (id, activator_id, at, how, score, why, action, acted, "
                "context) VALUES (?, ?, ?, 'tick', 0, '', 'ask_model:old_project', 1, '{}')",
                (f"f{one}", old["id"], (now - timedelta(minutes=10 + one)).isoformat()))
        self.assertNotIn("already today", self.monitor._held(old, "tick", now))
        self.assertNotIn("cooldown", self.monitor._held(old, "tick", now))
        self.assertEqual(self.monitor._held(old, "tick", now - timedelta(minutes=7)), "in its cooldown")

    def test_only_a_built_in_one_asks_this_way(self) -> None:
        from personalized_agent.heartbeat.monitor import activators as act
        with self.assertRaises(ValueError):
            act.check({"name": "Mine", "group": "recommend", "purpose": "x",
                       "when": {"event": "old_project"},
                       "then": [{"do": "ask_model", "handler": "old_project"}]})


class WhenItIsSaidTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.signals = self.agent.app.monitor_signals

    def _now(self, **more):
        said = {"present": True, "project_id": "p-orchard", "date": "2026-03-11",
                "project_dwell_minutes": 3.0, "project_days_since": 5.0,
                "old_project_least_minutes": 2.0, "old_project_least_days": 3.0}
        said.update(more)
        return said

    def test_back_long_enough_at_one_left_long_enough_until_told_that_day(self) -> None:
        self.assertTrue(self.signals._old_project(self._now()))
        self.assertTrue(self.signals._old_project(self._now()),
                        "looked at and not told: the day's chance is not spent")
        self.signals._set_kv(sg.OLD_TOLD.format("p-orchard"), "2026-03-11")
        self.assertFalse(self.signals._old_project(self._now()), "told: not again that day")
        self.assertTrue(self.signals._old_project(self._now(date="2026-03-12")))

    def test_a_window_looked_at_waits_half_an_hour_another_does_not(self) -> None:
        at = datetime(2026, 3, 11, 15, 0, tzinfo=timezone.utc)
        slack = {"application": "slack.exe", "title": "general (Channel) - Lab - Slack"}
        self.signals._set_kv(sg.OLD_LOOKED.format(sg.old_window_key(**slack)), at.isoformat())
        self.assertFalse(self.signals._old_project(self._now(
            at=(at + timedelta(minutes=29)).isoformat(), **slack)))
        self.assertTrue(self.signals._old_project(self._now(
            at=(at + timedelta(minutes=31)).isoformat(), **slack)))
        self.assertTrue(self.signals._old_project(self._now(
            at=(at + timedelta(minutes=10)).isoformat(), application="Code.exe",
            title="answers.csv - orchard - Visual Studio Code")), "another window: only the 5 minutes")

    def test_today_is_the_users_day_not_utcs(self) -> None:
        """The user, 2026-11-07: "今天是用用户的time zone判断的" - the zone the computer is in."""
        moment = datetime(2026, 3, 12, 3, 30, tzinfo=timezone.utc)
        self.signals.idle = lambda: 1.0
        said = self.signals.snapshot(moment)
        self.assertEqual(said["date"], moment.astimezone().date().isoformat())
        self.assertTrue(self.signals._old_project(self._now(date=said["date"])))
        self.signals._set_kv(sg.OLD_TOLD.format("p-orchard"), said["date"])
        self.assertFalse(self.signals._old_project(self._now(date=said["date"])))

    def test_not_for_a_glance_a_recent_one_a_new_one_or_nobody(self) -> None:
        self.assertFalse(self.signals._old_project(self._now(project_dwell_minutes=0.3)))
        self.assertFalse(self.signals._old_project(self._now(project_days_since=2.0)))
        self.assertFalse(self.signals._old_project(self._now(project_days_since=-1.0)),
                         "never worked on before is not an old one")
        self.assertFalse(self.signals._old_project(self._now(present=False)))
        self.assertFalse(self.signals._old_project(self._now(project_id="")))

    def test_the_event_is_said_with_the_rest(self) -> None:
        looked = SimpleNamespace(_previous={"project_id": "p-orchard"}, _closing_up=lambda: False,
                                 _old_project=lambda now: True)
        said = sg.Signals._events_of(looked, {
            "present": False, "back_after_minutes": 0, "date": "2026-03-11",
            "project_id": "p-orchard", "project_days_since": 5.0, "project_dwell_minutes": 3.0,
            "depth": "", "activity": "",
            "mode": ""})
        self.assertEqual(said, ["old_project"])

    def test_the_time_is_the_projects_own_and_carries_over_a_quarter(self) -> None:
        start = datetime(2026, 3, 11, 9, 0, tzinfo=timezone.utc)
        first = SimpleNamespace(start=start, minutes=15)
        self.signals._live = {"dwell": 60.0}
        self.assertEqual(self.signals._dwell(first, "p-orchard", start + timedelta(minutes=14)), 60.0)
        second = SimpleNamespace(start=start + timedelta(minutes=15), minutes=15)
        self.signals._live = {"dwell": 70.0}
        self.assertEqual(self.signals._dwell(second, "p-orchard", start + timedelta(minutes=17)),
                         130.0)
        self.assertEqual(self.signals._dwell(second, "p-harbour", start + timedelta(minutes=17)),
                         70.0, "another project before it adds nothing")
        self.signals._live = {"dwell": 900.0}
        third = SimpleNamespace(start=start + timedelta(minutes=45), minutes=15)
        self.assertEqual(self.signals._dwell(third, "p-orchard", start + timedelta(minutes=46)),
                         60.0, "never more than the time there was")

    def test_by_paths_only_names_that_are_the_projects_alone(self) -> None:
        with mock.patch.object(self.signals, "_path_map", lambda: {
                "orchard_survey": {"p-orchard"}, "shared_notes": {"p-orchard", "p-harbour"}}):
            self.assertEqual(self.signals._seconds_by_paths({
                "t:orchard_survey - Visual Studio Code": 50.0, "p:shared_notes": 40.0,
                "h:example.org": 30.0}, "p-orchard"), 50.0)

    def test_by_history_only_what_speaks_for_it(self) -> None:
        matcher = ProjectMatcher()
        start = datetime(2026, 3, 10, 9, tzinfo=timezone.utc)
        matcher.learn([("p-orchard", {"t:orchard.ipynb": 600.0, "h:mail.example": 60.0}, start),
                       ("p-harbour", {"t:tides.py": 600.0, "h:mail.example": 600.0},
                        start + timedelta(hours=2))])
        self.assertEqual(matcher.seconds_for({"t:orchard.ipynb": 80.0, "h:mail.example": 40.0},
                                             "p-orchard"), 80.0)


class TheLinesFromTheirWeeksTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.signals = self.agent.app.monitor_signals
        self.db = self.signals.db
        self.now = datetime.now(timezone.utc).astimezone().replace(hour=12, minute=0, second=0,
                                                                  microsecond=0)

    def _window(self, when: datetime, rate: float, project: str = "") -> None:
        self.db.execute("INSERT INTO activity_windows (start_at, minutes, local_start, main, "
                        "switch_rate, project_id) VALUES (?, 15, ?, 'coding', ?, ?)",
                        (when.astimezone(timezone.utc).isoformat(), when.isoformat(), rate, project))

    def test_too_little_seen_two_minutes_and_three_days(self) -> None:
        self.assertEqual(self.signals._old_project_lines(self.now),
                         {"old_project_least_minutes": 2.0, "old_project_least_days": 3.0})

    def test_from_how_they_move_and_how_long_they_leave_a_project(self) -> None:
        for one in range(7):             # two projects, each every 4 days: 12 times away of 4
            self._window(self.now - timedelta(days=4 * one, hours=2), 0.0, "p-orchard")
            self._window(self.now - timedelta(days=4 * one, hours=3), 0.0, "p-harbour")
        for one in range(12):            # 90 focus changes an hour: 40 s a window, four of them
            self._window(self.now - timedelta(days=one, hours=1), 90.0)
        said = self.signals._old_project_lines(self.now)
        self.assertEqual(said["old_project_least_days"], 4.0)
        self.assertEqual(said["old_project_least_minutes"], 2.7)

    def test_held_between_their_bounds(self) -> None:
        for one in range(12):            # daily, 600 changes an hour
            self._window(self.now - timedelta(days=one, hours=1), 600.0, "p-orchard")
        said = self.signals._old_project_lines(self.now)
        self.assertEqual(said["old_project_least_days"], 2.0)
        self.assertEqual(said["old_project_least_minutes"], 1.0)


class WhatTheModelSeesAndSaysTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.decider = self.app.monitor_decider
        self.db = self.decider.db
        described = {"title": "Orchard survey", "what_it_is": "A survey of old orchards",
                     "summary": "Cleaning the survey answers",
                     "todo": [{"id": "t1", "text": "Weight the answers by region"},
                              {"id": "t2", "text": "Draw the map", "done": True}]}
        self.db.execute("INSERT INTO state_tasks (id, number, members, described, created_at, "
                        "updated_at) VALUES ('p-orchard', 1, '[]', ?, '2026-03-01', '2026-03-01')",
                        (json.dumps(described),))
        tp.RunTasks(self.db).add([{
            "id": "t-1", "title": "Clean the survey answers", "summary": "Answers cleaned",
            "states": [], "project_id": "p-orchard", "ends_at": "2026-03-05T10:00:00+00:00",
            "resources": [{"kind": "main", "where": "D:\\work\\orchard", "what": "its folder"},
                          {"kind": "data", "where": "D:\\work\\orchard\\answers.csv",
                           "what": "the answers"}]}])
        self.one = next(a for a in self.app.monitor.store.list() if a["builtin"] == "old_project")
        self.context = {"activator": self.one, "why": ["event old_project"], "signals": {
            "project_id": "p-orchard", "project": "Orchard survey", "project_how": "paths",
            "project_dwell_minutes": 3.0, "project_days_since": 6.0, "date": "2026-03-11",
            "application": "Code.exe", "title": "answers.csv - orchard - Visual Studio Code"}}
        shot = Path(self.agent.root) / "shot.png"
        shot.write_bytes(PIXEL)
        now = datetime.now(timezone.utc).isoformat()
        for kind, data in (("screen.text", {"window_title": self.context["signals"]["title"],
                                            "text": "region,answer,weight"}),
                           ("screen.shot", {"path": str(shot)})):
            self.db.execute("INSERT INTO raw_events (id, event_type, source, timestamp, application, "
                            "data, metadata, processed) VALUES (?, ?, 'test', ?, 'Code.exe', ?, '{}', 0)",
                            (f"e-{kind}", kind, now, json.dumps(data)))

    def _asked(self, answer):
        asked = []

        def ask(system, content, task, **more):
            asked.append({"content": content, **more})
            return json.dumps(answer)
        return asked, ask

    def test_not_that_project_nothing_is_sent(self) -> None:
        asked, ask = self._asked({"same": False, "seen": "a chat where its link passed"})
        with mock.patch.object(self.decider, "_ask", side_effect=ask), \
                mock.patch.object(self.app.monitor, "say") as said:
            out = self.decider.old_project(self.app.monitor, self.one, self.context, "f1")
        said.assert_not_called()
        self.assertFalse(out["sent"])
        self.assertIn("link passed", out["why"])
        self.assertEqual(self.app.monitor._kv(sg.OLD_TOLD.format("p-orchard")), "",
                         "not that project: it may be looked at again today")
        looked = self.app.monitor._kv(sg.OLD_LOOKED.format(sg.old_window_key(
            "Code.exe", "answers.csv - orchard - Visual Studio Code")))
        self.assertTrue(looked, "but not in this window for half an hour")

    def test_that_project_where_it_stood_what_is_left_where_things_are(self) -> None:
        asked, ask = self._asked({"same": True, "seen": "its answers.csv is open", "title": "Back at the orchard survey",
                                  "message": "Weight the answers next.", "reason": "back after 6 days"})
        with mock.patch.object(self.decider, "_ask", side_effect=ask), \
                mock.patch.object(self.app.monitor, "say", return_value={"said": True}) as said:
            self.decider.old_project(self.app.monitor, self.one, self.context, "f1")
        content = asked[0]["content"]
        for words in ("A survey of old orchards", "Clean the survey answers",
                      "Weight the answers by region", "main | D:\\work\\orchard | its folder",
                      "region,answer,weight", "A picture of it"):
            self.assertIn(words, content)
        self.assertNotIn("Draw the map", content, "a finished to-do is not left")
        self.assertEqual(asked[0]["pictures"][0]["media_type"], "image/png")
        kwargs = said.call_args.kwargs
        self.assertTrue(kwargs["content"])
        self.assertTrue(kwargs["summary"], "a summary, not a notification or a recommendation")
        self.assertIn("answers.csv is open", kwargs["why"])
        self.assertEqual(self.app.monitor._kv(sg.OLD_TOLD.format("p-orchard")), "2026-03-11")

    def test_unsure_is_not_the_same(self) -> None:
        asked, ask = self._asked({"title": "Back at it", "message": "x"})
        with mock.patch.object(self.decider, "_ask", side_effect=ask), \
                mock.patch.object(self.app.monitor, "say") as said:
            self.decider.old_project(self.app.monitor, self.one, self.context, "f1")
        said.assert_not_called()


class SummariesAreAKindOfTheirOwnTests(unittest.TestCase):
    """The user, 2026-11-07: back to an old project and the evening report are summaries; the line
    beside the avatar says what kinds wait."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.monitor = self.app.monitor
        self.one = next(a for a in self.monitor.store.list() if a["builtin"] == "old_project")

    def test_said_as_a_summary_it_waits_unless_it_pops_and_is_grouped_apart(self) -> None:
        from personalized_agent.common.notifications import category_of
        context = {"activator": self.one, "signals": {}}
        waits = self.monitor.say("Back at the orchard survey", "x", context, "f1", content=True,
                                 summary=True)
        pops = self.monitor.say("Your day", "y", dict(context, activator=dict(self.one, delivery="pop")),
                                "f2", summary=True)
        rows = {row["id"]: row for row in self.app.notifications.waiting()}
        self.assertIn(waits["notification_id"], rows)
        self.assertNotIn(pops["notification_id"], rows)
        self.assertEqual(category_of(rows[waits["notification_id"]]), "Summaries")
        self.monitor.say("Tide tables", "z", context, "f3", content=True, recommended=True)
        names = [part["name"] for part in self.app.quick_sections("waiting")]
        self.assertEqual(names, ["Recommended content", "Summaries"])

    def test_the_evening_report_is_a_summary_and_still_comes_up(self) -> None:
        decider = self.app.monitor_decider
        evening = next(a for a in self.monitor.store.list() if a["builtin"] == "evening_closing")
        with mock.patch.object(decider, "_ask", return_value="A long day on the survey."),                 mock.patch.object(self.monitor, "say", return_value={"said": True}) as said:
            decider.write_evening(self.monitor, {"activator": evening, "signals": {}}, "f1")
        self.assertTrue(said.call_args.kwargs["summary"])
        self.assertNotIn("pops", said.call_args.kwargs, "the evening trigger's own setting says")

    def test_the_line_a_row_a_kind_notifications_summaries_recommendations(self) -> None:
        from personalized_agent.common.notifications import waiting_lines
        same = lambda text: text  # noqa: E731
        self.assertEqual(waiting_lines({"Recommended content": 2, "Summaries": 1, "Reminders": 1,
                                        "Other": 2}, same),
                         [("notifications", "3 notifications"), ("summaries", "1 summary"),
                          ("recommendations", "2 recommendations")])
        self.assertEqual(waiting_lines({"Summaries": 2}, same), [("summaries", "2 summaries")])
        self.assertEqual(waiting_lines({}, same), [])
        zh = {"{0} summary": "{0} 条总结", "{0} recommendations": "{0} 条推荐"}
        self.assertEqual(waiting_lines({"Summaries": 1, "Recommended content": 2}, zh.get),
                         [("summaries", "1 条总结"), ("recommendations", "2 条推荐")])

    def test_each_row_its_colour(self) -> None:
        from personalized_agent.common.notifications import LINE_KINDS
        from personalized_agent.ui import desktop
        colours = [desktop.WAITING_INK[kind] for kind, _groups, _said in LINE_KINDS]
        self.assertEqual(len(set(colours)), 3)
        self.assertEqual(desktop.SUMMARY_DOT, desktop.WAITING_INK["summaries"])


class ResourcesTests(unittest.TestCase):

    def test_a_task_keeps_only_places_its_states_have(self) -> None:
        places = ["D:\\work\\orchard\\answers.csv", "https://maps.example/orchards?x=1"]
        got = tp.read_resources([
            {"kind": "main", "where": "D:\\work\\orchard", "what": "its folder"},
            {"kind": "main", "where": "D:\\work\\orchard\\answers.csv", "what": "the answers"},
            {"kind": "page", "where": "https://elsewhere.example/", "what": "made up"},
            {"kind": "strange", "where": "https://maps.example/orchards?x=1", "what": "the map"},
            {"kind": "data", "where": "D:\\work\\orchard", "what": "twice"}], places)
        self.assertEqual([(one["kind"], one["where"]) for one in got], [
            ("main", "D:\\work\\orchard"), ("other", "D:\\work\\orchard\\answers.csv"),
            ("other", "https://maps.example/orchards?x=1")])
        self.assertEqual(tp.read_resources("not a list", places), [])

    def test_a_project_has_each_place_once_its_main_first(self) -> None:
        tasks = [{"ends_at": "2026-03-05", "resources": [
                     {"kind": "data", "where": "D:\\work\\orchard\\answers.csv", "what": "answers"},
                     {"kind": "main", "where": "D:\\work\\orchard", "what": "its folder"}]},
                 {"ends_at": "2026-03-01", "resources": [
                     {"kind": "main", "where": "D:/work/orchard/", "what": "older words"},
                     {"kind": "main", "where": "D:\\old\\orchard", "what": "where it was"}]}]
        got = tp.resources_of(tasks)
        self.assertEqual(got[0], {"kind": "main", "where": "D:\\work\\orchard", "what": "its folder",
                                  "tasks": 2, "last": "2026-03-05"})
        self.assertEqual([one["kind"] for one in got].count("main"), 1)
        self.assertEqual(len(got), 3)

    def test_written_with_its_tasks_and_read_back(self) -> None:
        state = SimpleNamespace(id="st1", title="Cleaning answers", detail="",
                                metadata={"kind": "work",
                                          "sources": ["D:\\work\\orchard\\answers.csv"]})
        answer = {"tasks": [{"title": "Clean the answers", "states": ["s1"], "resources": [
            {"kind": "data", "where": "D:\\work\\orchard\\answers.csv", "what": "the answers"}]}]}
        written = tp.read_tasks(answer, [state], {"st1": "s1"}, {})
        self.assertEqual(written[0]["resources"][0]["what"], "the answers")
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        store = tp.RunTasks(agent.app.db)
        store.add([dict(written[0], id="t-9", project_id="p-orchard")])
        self.assertEqual(store.one("t-9")["resources"], written[0]["resources"])

    def test_the_writer_is_asked_for_them(self) -> None:
        self.assertIn('"resources"', tp.WRITER_SYSTEM)
        self.assertIn("a link someone posted in a chat", tp.WRITER_SYSTEM)


if __name__ == "__main__":
    unittest.main()
