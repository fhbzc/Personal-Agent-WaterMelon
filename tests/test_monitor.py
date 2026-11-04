"""THE MONITOR (the user, 2026-11-03): real-time signals by code; Activators the
model writes and code runs - a protected one never changed by the model; what
they queue judged once by the heartbeat's step; every notification with why
it was made, a bad one given back to the decisions like it; the lessons
retrieved, not all given. Everything here is made up."""

from __future__ import annotations

import json
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.heartbeat.monitor import activators as act
from personalized_agent.heartbeat.monitor.decide import apply_program
from personalized_agent.heartbeat.monitor.engine import where_of
from personalized_agent.heartbeat.monitor.types import Window


class _Model:
    """The model as the tests play it: what it was asked, and its answers in turn."""

    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []

    def invoke(self, request, role="primary"):
        self.asked.append(request)
        answer = self.answers.pop(0) if self.answers else {}
        return SimpleNamespace(text=answer if isinstance(answer, str) else json.dumps(answer),
                               tool_calls=[], usage=None)


GARDEN = {"name": "Seed sale", "purpose": "Tell the user of a seed sale on a page", "group": "other",
          "when": {"keywords": {"words": {"seed sale": 3, "种子特卖": 3, "種セール": 3,
                                          "씨앗 할인": 3, "распродажа семян": 3},
                                "where": ["page"]}},
          "then": [{"do": "notify", "title": "A seed sale"}], "cooldown_minutes": 30,
          "most_per_day": 2}


class ActivatorRulesTests(unittest.TestCase):

    def test_held_to_its_shape_by_code(self) -> None:
        clean, warnings = act.check(GARDEN)
        self.assertEqual(warnings, [])
        self.assertEqual(clean["when"]["keywords"]["where"], ["page"])
        for bad, says in (
                (dict(GARDEN, when={"signal": "no_such", "op": ">", "value": 1}), "no signal"),
                (dict(GARDEN, when={"pattern": "seed.*sale"}), "only plain keywords"),
                (dict(GARDEN, then=[{"do": "ask_model", "handler": "calendar"}]), "handler"),
                (dict(GARDEN, then=[{"do": "explode"}]), "do is one of"),
                (dict(GARDEN, name=""), "needs a name")):
            with self.assertRaises(ValueError) as said:
                act.check(bad)
            self.assertIn(says, str(said.exception))

    def test_every_display_language_is_asked_for(self) -> None:
        only_english = dict(GARDEN, when={"keywords": {"words": {"seed sale": 3}}})
        _clean, warnings = act.check(only_english)
        self.assertEqual(len(warnings), 1)
        for language in ("Chinese", "Japanese", "Korean", "Russian"):
            self.assertIn(language, warnings[0])
        self.assertIn("EVERY DISPLAY LANGUAGE", act.RULES_FOR_THE_MODEL)

    def test_scored_by_the_words_found_and_where(self) -> None:
        clean, _ = act.check(GARDEN)
        ok, score, why = act.evaluate(clean["when"], {"where": "page", "text": "A SEED SALE today"})
        self.assertEqual((ok, score), (True, 3.0))
        self.assertEqual(why, ["“seed sale”"])
        self.assertFalse(act.evaluate(clean["when"], {"where": "email", "text": "seed sale"})[0])
        self.assertFalse(act.evaluate(clean["when"], {"where": "page", "text": "seedsale"})[0],
                         "a whole word")
        self.assertTrue(act.evaluate(clean["when"], {"where": "page", "text": "今日种子特卖"})[0])

    def test_signals_events_and_trees(self) -> None:
        cond = {"all": [{"event": "back"}, {"signal": "back_after_minutes", "op": ">=", "value": 30},
                        {"not": {"signal": "mode", "op": "==", "value": "busy"}}]}
        clean, _ = act.check(dict(GARDEN, when=cond))
        ctx = {"signals": {"back_after_minutes": 45, "mode": "working"}, "events": ["back"]}
        self.assertTrue(act.evaluate(clean["when"], ctx)[0])
        self.assertFalse(act.evaluate(clean["when"], dict(ctx, events=[]))[0])
        self.assertFalse(act.evaluate(clean["when"], dict(ctx, signals={"back_after_minutes": 10}))[0])

    def test_where_a_window_is(self) -> None:
        self.assertEqual(where_of(None, "msedge.exe", "Inbox (3) - Gmail"), "email")
        self.assertEqual(where_of(None, "OUTLOOK.EXE", "Re: tea"), "email")
        self.assertEqual(where_of(None, "WindowsTerminal.exe", "verify your account"), "terminal")
        self.assertEqual(where_of(None, "chrome.exe", "Seeds for spring"), "page")


class MonitorTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.monitor = self.app.monitor
        self.store = self.monitor.store
        self.monitor.latest = {"present": True, "since_break_minutes": 130, "events": []}

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _builtin(self, key):
        return next(one for one in self.store.list() if one["builtin"] == key)

    def test_the_built_in_ones_three_protected(self) -> None:
        listed = {one["builtin"]: one for one in self.store.list()}
        for key in ("invitation", "time_agreed", "spam_risk", "day_began", "back", "new_work",
                    "project_return", "scattered", "leisure", "break", "evening"):
            self.assertIn(key, listed)
        self.assertEqual({key for key, one in listed.items() if one["protected"]},
                         {"invitation", "time_agreed", "spam_risk"})
        # Written once: one deleted is not brought back.
        self.store.delete(listed["leisure"]["id"], "chat", "the user rests without the agent")
        from personalized_agent.heartbeat.monitor.builtins import builtins
        self.assertEqual(self.store.seed(builtins()), 0)

    def test_a_protected_one_is_never_changed_by_the_model(self) -> None:
        spam = self._builtin("spam_risk")
        said = apply_program(self.store, {"op": "edit", "id": spam["id"],
                                          "changes": {"cooldown_minutes": 1}, "why": "x"}, "heartbeat")
        self.assertIn("protected", said["error"])
        said = apply_program(self.store, {"op": "delete", "id": spam["id"], "why": "x"}, "chat")
        self.assertIn("protected", said["error"])
        self.assertIsNotNone(self.store.get(spam["id"]))
        # The user lifts it: then the model may.
        self.store.protect(spam["id"], False)
        said = apply_program(self.store, {"op": "edit", "id": spam["id"],
                                          "changes": {"cooldown_minutes": 45}, "why": "fewer"}, "heartbeat")
        self.assertNotIn("error", said)
        self.assertEqual(self.store.get(spam["id"])["cooldown_minutes"], 45)
        ops = [(one["by"], one["op"]) for one in self.store.changes(spam["id"])]
        self.assertEqual(ops[:2], [("heartbeat", "edit"), ("user", "unprotect")])

    def test_said_at_once_with_why_it_was_made_and_held_to_its_limits(self) -> None:
        brk = self._builtin("break")
        said = self.monitor.fire(brk, 0, ["since_break_minutes 130"])
        self.assertTrue(said["said"], said)
        shown = self.app.notifications.get(said["notification_id"])
        self.assertEqual(shown["title"], "Time for a break?")
        self.assertIn("130", shown["message"], "the signal put in")
        kept = shown["metadata"]["monitor"]
        self.assertEqual((kept["activator"], kept["context"]["signals"]["since_break_minutes"]),
                         ("A break", 130))
        # Its cooldown: not again at once.
        again = self.monitor.fire(brk, 0, [])
        self.assertEqual(again["held"], "in its cooldown")
        # Not today: until tomorrow.
        self.monitor.on_answered("feedback.answered", {"id": said["question_id"], "answer": "not_today"})
        # Past its cooldown, and still today: late at night there is no such time.
        later = datetime.now(timezone.utc) + timedelta(minutes=50)
        if later.astimezone().date() == datetime.now().astimezone().date():
            self.assertEqual(self.monitor.fire(brk, 0, [], now=later)["held"],
                             "not today - the user said so")

    def test_queued_then_judged_once_by_the_step(self) -> None:
        spam = self._builtin("spam_risk")
        self.monitor.fire(spam, 5, ["“gift card”", "“urgent”"], how="text",
                          window={"title": "A prize for you", "application": "msedge",
                                  "fresh": "Urgent: buy a gift card", "where": "email"})
        hits = self.monitor.pending_hits()
        self.assertEqual(len(hits), 1)
        model = _Model({"decisions": [{"hit": hits[0]["id"], "notify": True,
                                       "title": "Probably a scam", "message": "Do not buy the card.",
                                       "why": "a gift card asked for urgently"}],
                        "program": [{"op": "delete", "id": spam["id"], "why": "noisy"}]})
        self.app.monitor_decider.models = model
        report = self.app.monitor_decider.step(self.monitor)
        self.assertEqual((report["judged"], report["said"]), (1, 1))
        self.assertIn("protected", report["program"][0]["error"], "code held it, not the prompt")
        self.assertEqual(self.monitor.pending_hits(), [])
        self.assertEqual(model.asked[0].task, "heartbeat:step_monitor")
        self.assertIn("EVERY DISPLAY LANGUAGE", model.asked[0].system)
        # Judged once: the next step has nothing to judge and asks no model.
        self.assertIn("skipped", self.app.monitor_decider.step(self.monitor))
        self.assertEqual(len(model.asked), 1)

    def test_the_model_decides_whether_and_what_with_a_bad_one_like_it_given(self) -> None:
        back = self._builtin("back")
        context = {"activator": {"id": back["id"], "name": back["name"], "group": "recommend"},
                   "why": ["event back"], "signals": {"project": "Raised beds", "title": "Beds plan"}}
        # A bad notification of this Activator before.
        shown = self.app.notifications.notify(
            "Old beds news", "Something about raised beds", kind="proactive",
            metadata={"monitor": {"activator_id": back["id"], "why": "said too soon",
                                  "context": context}})
        self.app.bad_notification(shown.id, "not while I am planning the beds")
        model = _Model({"send": True, "title": "A drainage guide", "message": "Gravel under beds.",
                        "links": [{"title": "Guide", "url": "https://example.org/beds"},
                                  {"title": "bad", "url": "javascript:alert(1)"}],
                        "why": "they plan raised beds"})
        self.app.monitor_decider.models = model
        tier = back["then"][0]
        said = self.app.monitor_decider.recommend(self.monitor, back, tier, context, "monfire_x")
        self.assertTrue(said["said"])
        self.assertEqual(said["links"], [{"title": "Guide", "url": "https://example.org/beds"}])
        asked = model.asked[0].messages[0]["content"]
        self.assertIn("not while I am planning the beds", asked, "the bad one like it")
        self.assertEqual(model.asked[0].task, "monitor_recommend")
        kept = self.app.notifications.get(said["notification_id"])["metadata"]["monitor"]
        self.assertEqual(kept["why"], "they plan raised beds")
        # "send": false - nothing said.
        self.app.monitor_decider.models = _Model({"send": False, "why": "nothing new"})
        self.assertEqual(self.app.monitor_decider.recommend(self.monitor, back, tier, context, "f2"),
                         {"sent": False, "why": "nothing new"})

    def test_open_opens_what_was_found(self) -> None:
        opened = []
        self.monitor.opener = opened.append
        back = self._builtin("back")
        firing = self.monitor.fire(back, 0, [])["firing"]
        context = {"activator": {"id": back["id"], "name": back["name"], "group": "recommend"}}
        said = self.monitor.say("A guide", "Gravel.", context, firing,
                                links=[{"title": "Guide", "url": "https://example.org/beds"}])
        self.monitor._outcome(firing, dict(said, links=[{"url": "https://example.org/beds"}]))
        self.monitor.on_answered("feedback.answered", {"id": said["question_id"], "answer": "accept"})
        self.assertEqual(opened, ["https://example.org/beds"])

    def test_the_watcher_runs_the_text_ones(self) -> None:
        ident, _ = self.store.add(GARDEN, "chat", "the user asked")
        rules = {rule.name: rule for rule in self.app.triggers.rules}
        self.assertIn(ident, rules)
        watcher = self.app.triggers
        watcher.idle = lambda: 1.0
        event = {"application": "chrome.exe", "data": {"window_title": "Spring seeds",
                                                       "text": "Big seed sale this week", "handle": 7}}
        watcher.heard("listener.screen", dict(event, data=dict(event["data"], text="Welcome")))
        watcher.heard("listener.screen", event)
        due = [one for one in watcher._due.values() if one["rule"].name == ident]
        self.assertEqual(len(due), 1, "marked, to run when the page has settled")
        said = due[0]["rule"].act("Spring seeds", "chrome.exe", "Big seed sale this week",
                                  "Big seed sale this week")
        self.assertTrue(said["said"])
        self.store.switch(ident, False)
        self.assertFalse(rules[ident].allowed() and self.store.get(ident)["enabled"])

    def test_the_chat_tools(self) -> None:
        tools = self.app.agent.tools
        for name in ("monitor_signals", "monitor_list", "monitor_add", "monitor_edit", "monitor_delete"):
            self.assertTrue(tools.has(name), name)
        said = tools.call("monitor_add", {"activator": dict(GARDEN, name="Bulbs"), "why": "asked"})
        self.assertIn("id", said)
        spam = self._builtin("spam_risk")
        self.assertIn("protected", tools.call("monitor_delete", {"id": spam["id"], "why": "x"})["error"])


class SignalsTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.signals = self.agent.app.monitor_signals
        self.idle = [1.0]
        self.signals.idle = lambda: self.idle[0]

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_back_after_an_absence(self) -> None:
        now = datetime.now(timezone.utc)
        self.signals.snapshot(now)
        self.idle[0] = 2400.0                    # forty minutes with no keyboard
        away = self.signals.snapshot(now + timedelta(seconds=10))
        self.assertFalse(away["present"])
        self.idle[0] = 2.0
        back = self.signals.snapshot(now + timedelta(seconds=20))
        self.assertIn("back", back["events"])
        self.assertGreaterEqual(back["back_after_minutes"], 39)
        self.assertNotIn("back", self.signals.snapshot(now + timedelta(seconds=30))["events"],
                         "said once")

    def test_weeks_of_habits(self) -> None:
        local = datetime.now(timezone.utc).astimezone()
        windows = []
        for days in range(1, 15):
            day = (local - timedelta(days=days)).replace(hour=9, minute=0, second=0, microsecond=0)
            if day.weekday() >= 5 or (local.weekday() >= 5) != (day.weekday() >= 5):
                continue
            for n in range(8):                   # 09:00 to 11:00, a break after an hour
                windows.append(Window(start=day + timedelta(minutes=15 * n), main="writing",
                                      since_break_min=15.0 * ((n % 4) + 1), project="Garden book",
                                      project_id="project_garden"))
        self.signals.store.add(windows)
        said = self.signals.habits(local)
        if said["habit_days"] >= 3:
            self.assertEqual(said["usual_start_hour"], 9.0)
            self.assertEqual(said["usual_stretch_minutes"], 60.0)
            self.assertIn("Garden book", self.signals.habits_text(local))


class EarnedByEvidenceTests(unittest.TestCase):
    """THE USER, 2026-11-04: the more is changed the more must be looked up; nobody
    is asked, and what cannot be shown is not done. A rule of quiet is one change."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.monitor, self.tools = self.app.monitor, self.app.agent.tools
        self.store = self.monitor.store
        self.app.lesson_retriever.embedder = None
        self.free = [one for one in self.store.list() if not one["protected"]]

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _fired(self, activator, days):
        for n, day in enumerate(days):
            self.app.db.execute(
                "INSERT INTO monitor_firings (id, activator_id, at, how, score, why, action, acted, "
                "context) VALUES (?, ?, ?, 'tick', 1, 'a reason', 'notify', 1, '{}')",
                (f"fire_{activator['id']}_{day}_{n}_{len(days)}", activator["id"], f"{day}T09:00:00+00:00"))

    def _cases(self, activator):
        said = self.tools.call("monitor_evidence", {"id": activator["id"], "about": "meetings"})
        return [one["id"] for one in said["cases"]]

    def _edit(self, activator, cited, cooldown=99):
        return self.tools.call("monitor_edit", {"id": activator["id"], "why": "asked",
                                                "changes": {"cooldown_minutes": cooldown},
                                                "evidence": cited})

    def test_the_first_change_needs_a_look_and_no_more(self) -> None:
        one = self.free[0]
        self.assertIn("monitor_evidence", self._edit(one, [])["error"])
        self._cases(one)
        self.assertNotIn("error", self._edit(one, []), "looked: enough for one")

    def test_a_second_needs_cases_and_they_must_be_real(self) -> None:
        first, second = self.free[:2]
        self._cases(first)
        self._edit(first, [])
        self._fired(second, ["2026-03-01", "2026-03-01", "2026-03-02"])
        cases = self._cases(second)
        self.assertIn("3 are needed", self._edit(second, cases[:2])["error"])
        self.assertIn("3 are needed", self._edit(second, ["made_up", "too", "here"])["error"])
        self.assertNotIn("error", self._edit(second, cases[:3]))

    def test_the_most_need_more_than_one_day_and_evidence_builds_up(self) -> None:
        for one in self.free[:3]:
            self._fired(one, ["2026-03-01"] * 3)
            self._edit(one, self._cases(one))
        fourth = self.free[3]
        self._fired(fourth, ["2026-03-01"] * 3)
        self.assertIn("from 2 days", self._edit(fourth, self._cases(fourth))["error"])
        self._fired(fourth, ["2026-03-05"])          # said again, another day
        self.assertNotIn("error", self._edit(fourth, self._cases(fourth)))

    def test_deleting_takes_as_much_as_the_most(self) -> None:
        one = self.free[0]
        self._cases(one)
        held = self.tools.call("monitor_delete", {"id": one["id"], "why": "asked", "evidence": []})
        self.assertIn("error", held)
        self.assertIsNotNone(self.store.get(one["id"]))

    def test_the_heartbeat_is_held_harder_and_said_to_be(self) -> None:
        from personalized_agent.heartbeat.monitor.decide import background_change
        one = self.free[0]
        change = {"op": "edit", "id": one["id"], "changes": {"cooldown_minutes": 99}, "why": "noisy"}
        held = background_change(self.monitor, change)
        self.assertIn("nobody asked", held["held"])
        self.assertNotEqual(self.store.get(one["id"])["cooldown_minutes"], 99)
        self._fired(one, ["2026-03-01"] * 3)
        self.assertNotIn("held", background_change(self.monitor, change))
        self.assertEqual(self.store.get(one["id"])["cooldown_minutes"], 99)
        gone = background_change(self.monitor, {"op": "delete", "id": one["id"], "why": "noisy"})
        self.assertIn("days", gone["held"])
        self.assertIsNotNone(self.store.get(one["id"]))
        added = background_change(self.monitor, {"op": "add", "why": "a new one", "activator": {
            "name": "Tea", "purpose": "tea", "group": "other",
            "when": {"signal": "hour", "op": ">=", "value": 16},
            "then": [{"do": "queue"}]}})
        self.assertNotIn("held", added, "a new one takes nothing from anything there is")

    def test_what_the_heartbeat_changed_counts_towards_the_turn(self) -> None:
        from personalized_agent.heartbeat.monitor.decide import background_change
        first, second = self.free[:2]
        self._fired(first, ["2026-03-01"] * 3)
        background_change(self.monitor, {"op": "edit", "id": first["id"], "why": "noisy",
                                         "changes": {"cooldown_minutes": 99}})
        self._cases(second)
        self.assertIn("change 2", self._edit(second, [])["error"])

    def _ordered(self, activator, quote):
        return self.tools.call("monitor_edit", {"id": activator["id"], "why": "ordered",
                                                "changes": {"cooldown_minutes": 180},
                                                "user_said": quote})

    def test_the_users_own_order_stands_for_the_evidence(self) -> None:
        chat = self.app.conversations.ensure(None)
        self.app.conversations.add_message(chat, "user", "把所有推荐类的规则  冷却时间都改成 3 小时")
        for one in self.free[:5]:
            self.assertNotIn("error", self._ordered(one, "把所有推荐类的规则 冷却时间"), one["name"])
            self.assertEqual(self.store.get(one["id"])["cooldown_minutes"], 180)

    def test_words_they_did_not_write_are_refused(self) -> None:
        chat = self.app.conversations.ensure(None)
        self.app.conversations.add_message(chat, "user", "以后开会别提醒我")
        one = self.free[0]
        self.assertIn("not words the user", self._ordered(one, "把所有规则都改掉")["error"])
        self.assertIn("not words the user", self._ordered(one, "别")["error"], "too little to be an order")
        self.app.conversations.add_message(chat, "assistant", "把所有规则都改掉")
        self.assertIn("not words the user", self._ordered(one, "把所有规则都改掉")["error"],
                      "the agent's own words are no order")

    def test_words_of_long_ago_are_no_order_now(self) -> None:
        chat = self.app.conversations.ensure(None)
        said = self.app.conversations.add_message(chat, "user", "把所有规则都改掉")
        self.app.db.execute("UPDATE messages SET created_at = '2026-01-01T00:00:00+00:00' WHERE id = ?",
                            (said["id"],))
        self.assertIn("not words the user", self._ordered(self.free[0], "把所有规则都改掉")["error"])

    def test_nobody_is_asked(self) -> None:
        self._edit(self.free[0], [])
        self.assertEqual(self.app.db.query("SELECT id FROM task_questions"), [])

    def test_one_rule_of_quiet_stills_all_that_speaks_and_nothing_else(self) -> None:
        said = self.tools.call("monitor_quiet_add", {
            "name": "In a meeting", "why": "the user asked",
            "when": {"signal": "activity", "op": "==", "value": "meeting"}})
        self.assertIn("id", said)
        self.monitor.latest = {"present": True, "activity": "meeting", "since_break_minutes": 130,
                               "events": []}
        brk = next(one for one in self.store.list() if one["builtin"] == "break")
        self.assertEqual(self.monitor.fire(brk, 0, [])["held"], "a rule of quiet: In a meeting")
        spam = next(one for one in self.store.list() if one["builtin"] == "spam_risk")
        self.assertNotIn("rule of quiet", str(self.monitor._held(spam, "tick", datetime.now(timezone.utc))))
        self.monitor.latest["activity"] = "coding"
        self.assertEqual(self.monitor._quieted(), "")
        self.assertEqual(len(self.monitor.status()["quiet"]), 1)

    def test_a_bad_condition_is_refused(self) -> None:
        self.assertIn("error", self.tools.call("monitor_quiet_add", {
            "name": "x", "why": "y", "when": {"signal": "no_such", "op": "==", "value": 1}}))

    def test_taken_back_once_each(self) -> None:
        one = self.free[0]
        before = self.store.get(one["id"])["cooldown_minutes"]
        self._cases(one)
        self._edit(one, [], cooldown=before + 7)
        quiet = self.store.add_quiet("In a meeting", {"signal": "activity", "op": "==", "value": "meeting"},
                                     "chat", "asked")
        ids = [row["id"] for row in self.store.changes(limit=2)]
        self.assertEqual(self.store.undo(ids), 2)
        self.assertEqual(self.store.get(one["id"])["cooldown_minutes"], before)
        self.assertEqual(self.store.quiet_rules(), [])
        self.assertEqual(self.store.undo(ids), 0, "once")
        self.assertTrue(quiet)


class TheUsersOwnWorkTests(unittest.TestCase):
    """THE USER, 2026-11-04: "连续工作了160分钟，但是其实很多都是我的agent在工作"."""

    START = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)

    def _at(self, minutes):
        return (self.START + timedelta(minutes=minutes)).isoformat()

    def _focus(self, application, title, minutes):
        return {"event_type": "application.focused", "timestamp": self._at(minutes),
                "application": application, "data": {"application": application, "title": title}}

    def _windows(self, events, busy=(), count=2):
        from personalized_agent.heartbeat.monitor.windows import build_windows
        return build_windows(self.START, self.START + timedelta(minutes=15 * count), 15, events, busy=busy)

    def _hands(self, off, on):
        return [{"event_type": "presence.hands_off", "timestamp": self._at(off), "data": {}},
                {"event_type": "presence.hands_on", "timestamp": self._at(on), "data": {}}]

    def test_at_their_own_work_it_all_counts(self) -> None:
        windows = self._windows([self._focus("Code", "garden.py", 0)])
        self.assertEqual(windows[-1].main, "coding")
        self.assertAlmostEqual(windows[-1].own_work_min, 30, delta=0.5)

    def test_an_agent_at_work_with_their_hands_off_is_not_their_work(self) -> None:
        busy = [(self.START, self.START + timedelta(minutes=30))]
        windows = self._windows([self._focus("Code", "garden.py", 0), *self._hands(5, 25)], busy)
        self.assertAlmostEqual(windows[-1].own_work_min, 10, delta=0.5)

    def test_what_a_coding_assistant_says_shows_it_at_work(self) -> None:
        said = [{"event_type": "assistant.message", "timestamp": self._at(n), "data": {}}
                for n in range(6, 25, 2)]
        windows = self._windows([self._focus("Code", "garden.py", 0), *self._hands(5, 25), *said])
        self.assertLess(windows[-1].own_work_min, 12)

    def test_reading_with_no_agent_at_work_is_their_work_hands_off_or_not(self) -> None:
        windows = self._windows([self._focus("Code", "garden.py", 0), *self._hands(5, 25)])
        self.assertAlmostEqual(windows[-1].own_work_min, 30, delta=0.5)

    def test_their_hands_on_it_is_their_work_whatever_the_agent_does(self) -> None:
        busy = [(self.START, self.START + timedelta(minutes=30))]
        windows = self._windows([self._focus("Code", "garden.py", 0)], busy)
        self.assertAlmostEqual(windows[-1].own_work_min, 30, delta=0.5)

    def test_resting_is_the_break(self) -> None:
        def was(application, title, began, ended):
            return {"event_type": "application.focus_ended", "timestamp": self._at(ended),
                    "application": application,
                    "data": {"application": application, "window_title": title,
                             "duration_seconds": (ended - began) * 60.0,
                             "deactivation_time": self._at(ended)}}
        events = [self._focus("Code", "garden.py", 0), was("Code", "garden.py", 0, 15),
                  self._focus("Chrome", "bilibili", 15), was("Chrome", "bilibili", 15, 30),
                  self._focus("Code", "garden.py", 30)]
        windows = self._windows(events, count=3)
        self.assertEqual(windows[1].main, "entertainment")
        self.assertEqual(windows[1].own_work_min, 0)
        self.assertAlmostEqual(windows[2].own_work_min, 15, delta=0.5)
        self.assertAlmostEqual(windows[2].since_break_min, 45, delta=0.5,
                               msg="at the screen all the same")

    def test_the_time_at_the_screen_counts_whoever_is_working(self) -> None:
        busy = [(self.START, self.START + timedelta(minutes=30))]
        windows = self._windows([self._focus("Code", "garden.py", 0), *self._hands(5, 25)], busy)
        self.assertAlmostEqual(windows[-1].since_break_min, 30, delta=0.5)

    def test_two_reminders_the_work_one_sooner(self) -> None:
        from personalized_agent.heartbeat.monitor import builtins as made
        every = {one["builtin"]: one for one in made.BUILTINS} if hasattr(made, "BUILTINS") else {
            one["builtin"]: one for one in made.builtins()}
        screen, work = every["break"], every["work_break"]
        self.assertEqual(screen["when"]["all"][1]["signal"], "since_break_minutes")
        self.assertEqual(work["when"]["all"][1]["signal"], "work_minutes")
        self.assertLess(work["when"]["all"][1]["value"], screen["when"]["all"][1]["value"])

    def test_kept_words_of_the_break_are_changed_and_the_users_are_not(self) -> None:
        from personalized_agent.heartbeat.monitor import activators as act, builtins as made
        agent = TemporaryAgent()
        try:
            store = act.ActivatorStore(agent.app.db)
            row = agent.app.db.query_one("SELECT id, data FROM monitor_activators WHERE builtin='break'")
            old = row["data"].replace(json.dumps(made.SCREEN_MESSAGE, ensure_ascii=False),
                                      json.dumps(made.OLD_BREAK[1][0], ensure_ascii=False))
            agent.app.db.execute("UPDATE monitor_activators SET data=? WHERE id=?", (old, row["id"]))
            self.assertEqual(store.reword("break", made.OLD_BREAK), 1)
            self.assertIn("at the screen", agent.app.db.query_one(
                "SELECT data FROM monitor_activators WHERE id=?", (row["id"],))["data"])
            self.assertEqual(store.reword("break", made.OLD_BREAK), 0, "once")
        finally:
            agent.cleanup()


class TaskAndHowTests(unittest.TestCase):
    """THE USER, 2026-11-04: an Activator may say which task, how sure the
    project is, and a name in part."""

    def test_contains_for_text_only(self) -> None:
        clean, _ = act.check(dict(GARDEN, when={"signal": "project", "op": "contains",
                                                "value": "Raised"}))
        ctx = {"signals": {"project": "The raised beds plan"}}
        self.assertTrue(act.evaluate(clean["when"], ctx)[0], "whatever the case")
        self.assertFalse(act.evaluate(clean["when"], {"signals": {"project": "Tea house"}})[0])
        with self.assertRaises(ValueError):
            act.check(dict(GARDEN, when={"signal": "hour", "op": "contains", "value": 9}))

    def test_written_on_how_the_project_was_told(self) -> None:
        clean, _ = act.check(dict(GARDEN, when={"all": [
            {"signal": "task", "op": "contains", "value": "drainage"},
            {"not": {"signal": "project_how", "op": "==", "value": "words"}}]}))
        ctx = {"signals": {"task": "Plan the drainage", "project_how": "paths"}}
        self.assertTrue(act.evaluate(clean["when"], ctx)[0])
        ctx["signals"]["project_how"] = "words"
        self.assertFalse(act.evaluate(clean["when"], ctx)[0])

    def test_the_task_within_a_project(self) -> None:
        from personalized_agent.heartbeat.monitor.signals import Signals, words_of
        signals = Signals.__new__(Signals)
        signals._unit_words = lambda: {}
        signals._unit_tasks = {
            "project_garden": [("task_a", "Plan the drainage", words_of("plan drainage gravel")),
                               ("task_b", "Order tomato seeds", words_of("order tomato seeds"))],
            "project_tea": [("task_c", "Book the tea house", words_of("book tea house"))]}
        self.assertEqual(signals.task_in("project_garden", {"t:tomato seeds order": 60.0}, set()),
                         ("task_b", "Order tomato seeds"))
        self.assertEqual(signals.task_in("project_garden", {}, words_of("gravel under the drainage")),
                         ("task_a", "Plan the drainage"))
        self.assertEqual(signals.task_in("project_tea", {}, set()),
                         ("task_c", "Book the tea house"), "the only one")
        self.assertEqual(signals.task_in("project_garden", {}, set()), ("", ""), "nothing says which")


class PlacedByWordsTests(unittest.TestCase):
    """THE USER, 2026-11-03: the events are in every language - a window is
    placed by the words it shares with each piece of work, not by an English
    model of meanings."""

    def test_words_in_any_language(self) -> None:
        from personalized_agent.heartbeat.monitor.signals import words_of
        self.assertEqual(words_of("Raised beds - drainage plan"),
                         {"raised", "beds", "drainage", "plan"})
        self.assertTrue({"菜园", "园排", "排水"} <= words_of("菜园排水计划"))
        self.assertNotIn("google", words_of("Google Chrome"), "a program's name says nothing")


if __name__ == "__main__":
    unittest.main()
