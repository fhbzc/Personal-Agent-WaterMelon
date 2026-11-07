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
from unittest import mock
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
                    "new_text", "leisure", "break", "evening",
                    "evening_closing"):
            self.assertIn(key, listed)
        self.assertEqual({key for key, one in listed.items() if one["protected"]},
                         {"invitation", "time_agreed", "spam_risk"})
        # Written once: one deleted is not brought back.
        self.store.delete(listed["leisure"]["id"], "chat", "the user rests without the agent")
        from personalized_agent.heartbeat.monitor.builtins import builtins
        self.assertEqual(self.store.seed(builtins()), 0)

    def test_back_to_a_project_is_gone(self) -> None:
        from personalized_agent.heartbeat.monitor import signals as sg
        self.assertNotIn("project_return", [one["builtin"] for one in self.store.list()])
        self.assertNotIn("project_return", sg.EVENTS)
        with self.assertRaises(ValueError):
            act.check(dict(GARDEN, when={"event": "project_return"}))
        looked = SimpleNamespace(_previous={"project_id": "p1"}, _closing_up=lambda: False)
        said = sg.Signals._events_of(looked, {
            "present": False, "back_after_minutes": 0, "date": "2026-03-11", "project_id": "p2",
            "project_days_since": 3.0, "depth": "", "activity": "", "mode": ""})
        self.assertEqual(said, ["project_changed"])

    def test_nothing_for_scattered_work(self) -> None:
        from personalized_agent.heartbeat.monitor import signals as sg
        self.assertNotIn("scattered", [one["builtin"] for one in self.store.list()])
        self.assertNotIn("scattered", sg.EVENTS)
        with self.assertRaises(ValueError):
            act.check(dict(GARDEN, when={"event": "scattered"}))
        looked = SimpleNamespace(_previous={"project_id": "p1", "depth": ""}, _closing_up=lambda: False)
        said = sg.Signals._events_of(looked, {
            "present": False, "back_after_minutes": 0, "date": "2026-03-11", "project_id": "p1",
            "project_days_since": 0.0, "depth": "scattered", "activity": "", "mode": ""})
        self.assertEqual(said, [])

    def test_one_kept_from_before_is_taken_away_and_stays_away(self) -> None:
        from personalized_agent.heartbeat.monitor.builtins import GONE
        ident, _warnings = self.store.add(dict(GARDEN, when={"event": "project_changed"}), "built in",
                                          "built in", builtin="project_return")
        other, _warnings = self.store.add(dict(GARDEN, name="Scattered", when={"event": "back"}),
                                          "built in", "built in", builtin="scattered")
        self.assertIn(ident, [one["id"] for one in self.store.list()])
        self.monitor._write_builtins()
        self.assertNotIn(ident, [one["id"] for one in self.store.list()])
        self.assertNotIn(other, [one["id"] for one in self.store.list()])
        self.assertEqual(self.store.changes(ident)[0]["why"], GONE["project_return"])
        self.assertEqual(self.store.changes(other)[0]["why"], GONE["scattered"])
        self.monitor._write_builtins()
        self.assertEqual(len([one for one in self.store.changes(ident) if one["op"] == "delete"]), 1)

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
                        "url": "https://example.org/drainage", "why": "they plan raised beds"})
        self.app.monitor_decider.models = model
        tier = back["then"][0]
        said = self.app.monitor_decider.recommend(self.monitor, back, tier, context, "monfire_x")
        self.assertTrue(said["said"])
        self.assertEqual(said["url"], "https://example.org/drainage")
        asked = model.asked[0].messages[0]["content"]
        self.assertIn("not while I am planning the beds", asked, "the bad one like it")
        self.assertEqual(model.asked[0].task, "monitor_recommend")
        kept = self.app.notifications.get(said["notification_id"])["metadata"]["monitor"]
        self.assertEqual(kept["why"], "they plan raised beds")
        # "send": false - nothing said.
        self.app.monitor_decider.models = _Model({"send": False, "why": "nothing new"})
        self.assertEqual(self.app.monitor_decider.recommend(self.monitor, back, tier, context, "f2"),
                         {"sent": False, "why": "nothing new"})

    def test_an_activator_is_a_condition_and_a_job(self) -> None:
        job = dict(GARDEN, then=[{"do": "ask_model", "handler": "recommend"}])
        with self.assertRaisesRegex(ValueError, "brief"):
            self.store.add(job, "chat", "the user asked")
        job["then"] = [{"do": "ask_model", "handler": "recommend",
                        "brief": "Find this week's new papers on soil drainage"}]
        ident, _warnings = self.store.add(job, "chat", "the user asked")
        added = self.store.get(ident)
        model = _Model({"send": False, "why": "nothing new"})
        self.app.monitor_decider.models = model
        context = {"activator": {"id": ident, "name": added["name"], "group": added["group"]}}
        self.app.monitor_decider.recommend(self.monitor, added, added["then"][0], context, "f")
        self.assertIn("THE JOB: Find this week's new papers on soil drainage",
                      model.asked[0].messages[0]["content"])

    def test_every_built_in_that_asks_the_model_says_what_for(self) -> None:
        for one in self.store.list():
            for tier in one["then"]:
                if tier.get("handler") == "recommend":
                    self.assertTrue(tier.get("brief"), one["name"])

    # -- code says at once, a model after it (the user, 2026-11-04) ----------------------
    FIRST = {"name": "Seed fraud", "purpose": "Words of a seed fraud.", "group": "risk",
             "when": {"keywords": {"words": {"magic seeds": 9}, "where": ["any"]}},
             "then": [{"do": "notify", "min_score": 8, "title": "Careful", "message": "A fraud?",
                       "reason": "Seen: {matched}",
                       "and_then": {"do": "ask_model", "handler": "judge", "brief": "Look closer."}}],
             "cooldown_minutes": 30, "most_per_day": 5}

    def _first_then_model(self, answer):
        ident, _warnings = self.store.add(self.FIRST, "chat", "the user asked")
        model = _Model(answer)
        self.app.monitor_decider.models = model
        said = self.monitor.fire(self.store.get(ident), 9, ["“magic seeds”"], how="text",
                                 window={"title": "Seeds", "application": "chrome", "where": "page",
                                         "fresh": "Buy magic seeds now"})
        for _ in range(200):
            if model.asked and self.monitor._asking.acquire(blocking=False):
                self.monitor._asking.release()
                break
            time.sleep(0.01)
        time.sleep(0.05)
        return said, model, ident

    def test_code_says_at_once_with_what_set_it_off_then_a_model_looks_closer(self) -> None:
        said, model, _ident = self._first_then_model({"send": False, "why": "really a fraud"})
        first = self.app.notifications.get(said["notification_id"])
        self.assertEqual(first["title"], "Careful")
        self.assertEqual(first["metadata"]["reason"], "Seen: “magic seeds”")
        asked = model.asked[0].messages[0]["content"]
        self.assertIn("CODE HAS ALREADY TOLD THE USER", asked)
        self.assertIn("Buy magic seeds now", asked, "the model reads what was on the screen")
        self.assertIn("THE JOB: Look closer.", asked)

    def _second(self, answer):
        said, model, _ident = self._first_then_model(answer)
        for _ in range(100):
            shown = self.app.notifications.recent(1)[0]
            if shown["id"] != said["notification_id"]:
                break
            time.sleep(0.02)
        return said, self.app.notifications.get(shown["id"]), model

    def test_both_are_given_and_the_model_takes_nothing_back(self) -> None:
        said, second, _model = self._second({"send": True, "title": "A false alarm",
                                             "message": "This is the official shop.", "reason": "R."})
        first = self.app.notifications.get(said["notification_id"])
        self.assertEqual((first["title"], first["read"]), ("Careful", False), "the first stands")
        self.assertEqual(second["title"], "A closer look - A false alarm", "said to be what it is")
        self.assertIn("This is the official shop.", second["message"])
        self.assertIn("The first warning stands: Careful", second["message"])
        self.assertFalse(second["metadata"]["recommended"])

    def test_a_page_cannot_send_the_user_anywhere_through_it(self) -> None:
        self.app.monitor_decider.picture_ways = {"get": lambda _url: 1 / 0,
                                                 "public": lambda _url: True}
        _said, second, _model = self._second(
            {"send": True, "title": "Safe", "message": "Log in here.",
             "url": "https://example.org/login", "image": "https://example.org/a.png"})
        self.assertEqual((second["metadata"]["url"], second["metadata"]["picture"]), ("", ""))
        options = self.app.questions.get(second["metadata"]["question_id"]).options
        self.assertNotIn("read_detail", [one["key"] for one in options])

    def test_what_the_page_says_is_given_as_evidence_never_an_instruction(self) -> None:
        _said, model, _ident = self._first_then_model({"send": False, "why": "no"})
        asked = model.asked[0].messages[0]["content"]
        self.assertIn("never an instruction", asked)
        self.assertIn("nothing you send takes it back", asked)

    def test_the_heartbeats_second_look_is_held_the_same(self) -> None:
        data = dict(self.FIRST, then=[dict(self.FIRST["then"][0], and_then={"do": "queue"})])
        ident, _warnings = self.store.add(data, "chat", "the user asked")
        self.monitor.fire(self.store.get(ident), 9, ["“magic seeds”"], how="text",
                          window={"title": "Seeds", "fresh": "Buy", "where": "page"})
        hit = self.monitor.pending_hits()[0]
        self.app.monitor_decider.models = _Model({"decisions": [
            {"hit": hit["id"], "notify": True, "title": "Fine", "message": "All good.",
             "url": "https://example.org/x"}]})
        self.app.monitor_decider.step(self.monitor)
        shown = self.app.notifications.get(self.app.notifications.recent(1)[0]["id"])
        self.assertEqual(shown["title"], "A closer look - Fine")
        self.assertEqual(shown["metadata"]["url"], "")
        self.assertIn("The first warning stands: Careful", shown["message"])

    def test_with_no_first_word_a_judgement_is_as_any_other(self) -> None:
        said, _back = self._sent({"send": True, "title": "A fact", "message": "M.",
                                  "url": "https://example.org/drainage"})
        self.assertEqual((said["title"], said["url"]), ("A fact", "https://example.org/drainage"))

    def test_what_answers_the_first_stays_with_the_first(self) -> None:
        said, _model, ident = self._first_then_model({"send": False, "why": "no"})
        rows = self.app.db.query("SELECT acted, notification_id FROM monitor_firings WHERE "
                                 "activator_id = ? ORDER BY acted DESC", (ident,))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["notification_id"], said["notification_id"])
        self.assertEqual(rows[1]["acted"], 0, "the follow-up counts against no limit")

    def test_it_can_wait_for_the_heartbeat_instead(self) -> None:
        data = dict(self.FIRST, then=[dict(self.FIRST["then"][0], and_then={"do": "queue"})])
        ident, _warnings = self.store.add(data, "chat", "the user asked")
        self.monitor.fire(self.store.get(ident), 9, ["“magic seeds”"], how="text",
                          window={"title": "Seeds", "fresh": "Buy", "where": "page"})
        hit = self.monitor.pending_hits()[0]
        self.assertEqual(hit["context"]["said_already"]["title"], "Careful")

    def test_it_is_a_way_to_write_one_not_a_rule_for_all(self) -> None:
        plain = dict(self.FIRST, then=[{"do": "notify", "min_score": 8, "title": "Careful"}])
        ident, _warnings = self.store.add(plain, "chat", "the user asked")
        self.assertNotIn("and_then", self.store.get(ident)["then"][0])
        self.assertNotIn("and_then", self._builtin("break")["then"][0])

    def test_what_may_follow_is_held_by_code(self) -> None:
        for follow in ({"do": "notify"}, {"do": "ask_model", "handler": "judge"},
                       {"do": "ask_model", "handler": "calendar", "brief": "x"}, "queue"):
            with self.subTest(follow), self.assertRaisesRegex(ValueError, "and_then"):
                self.store.add(dict(self.FIRST, then=[dict(self.FIRST["then"][0], and_then=follow)]),
                               "chat", "x")

    def test_spam_speaks_first_with_its_words_kept_on_a_machine_that_had_the_old_one(self) -> None:
        from personalized_agent.heartbeat.monitor.builtins import OLD_SPAM_THENS
        spam = self._builtin("spam_risk")
        self.assertIn("and_then", spam["then"][0])
        self.assertIn("{matched}", spam["then"][0]["reason"])
        self.app.db.execute("UPDATE monitor_activators SET data = ? WHERE id = ?",
                            (json.dumps(dict({k: v for k, v in spam.items() if k in (
                                "name", "purpose", "group", "when", "cooldown_minutes",
                                "most_per_day")}, then=OLD_SPAM_THENS[0])), spam["id"]))
        new = act.check(__import__("personalized_agent.heartbeat.monitor.builtins",
                                   fromlist=["SPAM"]).SPAM, builtin=True)[0]["then"]
        self.assertEqual(self.store.retier("spam_risk", OLD_SPAM_THENS[0], new), 1)
        self.assertEqual(self.store.retier("spam_risk", OLD_SPAM_THENS[0], new), 0, "once")
        self.assertIn("that warning stands", self.store.get(spam["id"])["then"][0]["and_then"]["brief"])

    def test_quiet_while_presenting_or_in_a_meeting(self) -> None:
        names = [one["name"] for one in self.store.quiet_rules()]
        self.assertEqual(names, ["While presenting", "In a meeting"])
        back = self._builtin("back")
        for signal, rule in (("presenting", "While presenting"), ("in_meeting", "In a meeting")):
            with self.subTest(signal):
                self.monitor.latest = {"present": True, "events": [], signal: True}
                self.assertEqual(self.monitor.fire(back, 0, [])["held"],
                                 f"a rule of quiet: {rule}")

    def test_a_blocker_is_told_at_each_look_for_every_reminder(self) -> None:
        said = {"present": True, "events": [], "presenting": True}
        self.monitor.signals.record_if_due = lambda _when: None
        self.monitor.signals.snapshot = lambda _when: said
        self.monitor.tick()
        self.assertEqual(self.monitor.blocker, "While presenting")
        said["presenting"] = False
        self.monitor.tick()
        self.assertEqual(self.monitor.blocker, "")

    def _bubbles(self, showing=None):
        from collections import deque

        from personalized_agent.ui.desktop import DesktopAgent
        shown = []
        one = SimpleNamespace(
            app=self.app, _bubble=object() if showing else None, _showing=showing,
            _bubble_queue=deque(), BLOCKERS_EVERY_MS=1,
            root=SimpleNamespace(after=lambda _ms, _then: None))
        one._bubbles_opened = False
        one.collapsed = []
        one._hide_waiting = lambda: one.collapsed.append(0)
        one._show_waiting = lambda: one.collapsed.append(len(one._bubble_queue))
        one._blocker = lambda: DesktopAgent._blocker(one)
        one._blocked = lambda: DesktopAgent._blocked(one)
        one._watch_the_blockers = lambda: DesktopAgent._watch_the_blockers(one)
        one._show_next_bubble = lambda: shown.append(list(one._bubble_queue))
        one._next_bubble = lambda: DesktopAgent._next_bubble(one)
        one._look_for_work_waiting = lambda: None

        def close():
            one._bubble = one._showing = None
        one._close_bubble = close
        return one, shown

    def test_a_reminder_on_the_screen_goes_back_in_line_when_a_blocker_begins(self) -> None:
        bubbles, _shown = self._bubbles(showing={"text": "Dentist at three"})
        self.monitor.blocker = "While presenting"
        bubbles._watch_the_blockers()
        self.assertIsNone(bubbles._bubble)
        self.assertEqual(list(bubbles._bubble_queue), [{"text": "Dentist at three"}])

    def test_what_waited_comes_when_the_blocker_is_over(self) -> None:
        bubbles, shown = self._bubbles()
        bubbles._bubble_queue.append({"text": "Dentist at three"})
        self.monitor.blocker = "In a meeting"
        bubbles._watch_the_blockers()
        self.assertEqual(shown, [])
        self.monitor.blocker = ""
        bubbles._watch_the_blockers()
        self.assertEqual(shown, [[{"text": "Dentist at three"}]])

    def test_what_waits_is_collapsed_and_opened_by_hand(self) -> None:
        from personalized_agent.ui.desktop import DesktopAgent
        bubbles, shown = self._bubbles()
        bubbles._bubble_queue.extend([{"text": "One"}, {"text": "Two"}])
        self.monitor.blocker = "In a meeting"
        bubbles._watch_the_blockers()
        self.assertEqual(bubbles.collapsed[-1], 2, "says how many")
        self.assertEqual(self.monitor.reminders_waiting, 2, "and so does the panel")
        self.assertEqual(shown, [])
        DesktopAgent._open_waiting(bubbles)
        self.assertEqual(len(shown), 1, "pressed, they come")
        self.assertFalse(bubbles._blocked(), "and the blocker lets them through")
        bubbles._bubble_queue.clear()
        bubbles._watch_the_blockers()
        self.assertTrue(bubbles._blocked(), "all read: the next ones wait again")

    def test_nothing_is_drawn_while_a_blocker_holds(self) -> None:
        from personalized_agent.ui.desktop import DesktopAgent
        bubbles, _shown = self._bubbles()
        bubbles._bubble_queue.append({"text": "Dentist at three"})
        self.monitor.blocker = "In a meeting"
        DesktopAgent._show_next_bubble(bubbles)
        self.assertEqual(len(bubbles._bubble_queue), 1, "still in line, and nothing was drawn")

    def test_a_rule_of_quiet_taken_away_stays_away(self) -> None:
        from personalized_agent.heartbeat.monitor.builtins import QUIET
        one = self.store.quiet_rules()[0]
        self.store.remove_quiet(one["id"], "user")
        self.assertEqual(self.store.seed_quiet(QUIET), 0)
        self.assertEqual(len(self.store.quiet_rules()), 1)

    def test_what_a_firing_keeps_leaves_the_programs_out(self) -> None:
        self.monitor.latest = {"present": True, "events": [], "running": "chrome, zoom"}
        firing = self.monitor.fire(self._builtin("back"), 0, [])["firing"]
        kept = self.app.db.query_one("SELECT context FROM monitor_firings WHERE id = ?", (firing,))
        self.assertNotIn("running", json.loads(kept["context"])["signals"])

    # -- what the model sends (the user, 2026-11-04) -----------------------------------
    def _sent(self, answer, pictures=None):
        back = self._builtin("back")
        firing = f"monfire_{len(self.monitor.firings())}"
        self.app.db.execute("INSERT INTO monitor_firings (id, activator_id, at, how) "
                            "VALUES (?, ?, ?, 'tick')",
                            (firing, back["id"], datetime.now(timezone.utc).isoformat()))
        context = {"activator": {"id": back["id"], "name": back["name"], "group": "recommend"}}
        decider = self.app.monitor_decider
        decider.models = _Model(answer)
        decider.picture_ways = {"get": (pictures or {}).__getitem__, "public": lambda _url: True}
        return decider.recommend(self.monitor, back, back["then"][0], context, firing), back

    def _options(self, said):
        return [one["key"] for one in self.app.questions.get(said["question_id"]).options]

    @staticmethod
    def _png(size=200):
        from io import BytesIO

        from PIL import Image
        out = BytesIO()
        Image.new("RGB", (size, size), "#3f9142").save(out, "PNG")
        return out.getvalue()

    def test_content_and_a_page_are_read_or_good_to_know(self) -> None:
        opened = []
        self.monitor.opener = opened.append
        said, _back = self._sent({"send": True, "title": "A guide", "message": "Gravel under beds.",
                                  "url": "https://example.org/beds", "why": "they plan beds"})
        self.assertEqual(self._options(said), ["read_detail", "good_to_know", "add_to_collection"])
        shown = self.app.notifications.get(said["notification_id"])
        self.assertTrue(shown["metadata"]["content"])
        self.assertEqual(shown["metadata"]["url"], "https://example.org/beds")
        self.app.feedback.answer(said["question_id"], "read_detail")
        self.assertEqual(opened, ["https://example.org/beds"])

    def test_it_says_why_to_the_user_and_keeps_its_own_note(self) -> None:
        said, _back = self._sent({"send": True, "title": "A fact", "message": "Beds drain.",
                                  "reason": "You turned to a video on beds.", "why": "A note."})
        meta = self.app.notifications.get(said["notification_id"])["metadata"]
        self.assertEqual(meta["reason"], "You turned to a video on beds.")
        self.assertEqual(meta["monitor"]["why"], "A note.")

    def test_with_no_reason_written_its_own_note_is_shown(self) -> None:
        said, _back = self._sent({"send": True, "title": "A fact", "message": "M.", "why": "A note."})
        self.assertEqual(said["reason"], "A note.")

    def test_the_model_is_asked_for_the_line(self) -> None:
        from personalized_agent.heartbeat.monitor import decide
        self.assertIn('"reason"', decide.RECOMMEND_SYSTEM)
        # A terminal's title stayed for two months and was taken for the work (2026-11-05).
        self.assertIn("A WINDOW'S TITLE IS A LABEL", decide.RECOMMEND_SYSTEM)
        # A talk on the open list was reported as today's (2026-11-05).
        self.assertIn("ONLY WHAT TODAY'S LINES SHOW", decide.EVENING_SYSTEM)
        self.assertIn('"reason"', decide.STEP_SYSTEM)

    def test_good_to_know_opens_nothing_and_holds_nothing_back(self) -> None:
        opened = []
        self.monitor.opener = opened.append
        said, back = self._sent({"send": True, "title": "A fact", "message": "Beds drain.",
                                 "url": "https://example.org/beds"})
        self.app.feedback.answer(said["question_id"], "good_to_know")
        self.assertEqual(opened, [])
        self.assertEqual(self.monitor._kv(f"monitor.snoozed.{back['id']}"), "")

    def test_with_no_page_there_is_nothing_to_read(self) -> None:
        said, _back = self._sent({"send": True, "title": "A fact", "message": "Beds drain."})
        self.assertEqual(self._options(said), ["good_to_know", "add_to_collection"])

    def test_an_address_that_may_not_be_opened_is_left_out_and_the_rest_said(self) -> None:
        said, _back = self._sent({"send": True, "title": "A fact", "message": "Beds drain.",
                                  "url": "javascript:alert(1)"})
        self.assertTrue(said["said"])
        self.assertEqual(said["url"], "")
        self.assertEqual(self._options(said), ["good_to_know", "add_to_collection"])

    def test_an_address_with_the_users_own_words_in_it_is_left_out(self) -> None:
        back = self._builtin("back")
        context = {"activator": {"id": back["id"], "name": back["name"], "group": "recommend"},
                   "signals": {"project": "Zanzibarcove grant"}}
        self.app.monitor_decider.models = _Model({"send": True, "title": "A fact", "message": "M.",
                                                  "url": "https://example.org/zanzibarcove"})
        said = self.app.monitor_decider.recommend(self.monitor, back, back["then"][0], context, "f")
        self.assertTrue(said["said"])
        self.assertEqual(said["url"], "", "nothing of theirs goes out in an address")

    def test_a_picture_is_kept_here_and_served(self) -> None:
        said, _back = self._sent(
            {"send": True, "title": "A bed", "message": "This one.",
             "image": "https://example.org/bed.png"},
            {"https://example.org/bed.png": (self._png(), "image/png")})
        self.assertTrue(said["picture"].endswith(".png"))
        self.assertTrue(self.monitor.picture_path(said["picture"]).is_file())
        self.assertIsNone(self.monitor.picture_path("../" + said["picture"] + ".nothing"))
        shown = self.app.notifications.get(said["notification_id"])
        self.assertEqual(shown["metadata"]["picture"], said["picture"])

    def test_the_picture_a_page_shows_of_itself(self) -> None:
        page = b'<html><head><meta property="og:image" content="/bed.png"></head></html>'
        said, _back = self._sent(
            {"send": True, "title": "A bed", "message": "This one.", "image": "page",
             "url": "https://example.org/beds"},
            {"https://example.org/beds": (page, "text/html"),
             "https://example.org/bed.png": (self._png(), "image/png")})
        self.assertTrue(said["picture"])

    def test_a_picture_that_cannot_be_had_costs_the_notification_nothing(self) -> None:
        for name, found in (("not a picture", (b"<html>", "text/html")),
                            ("a tracking pixel", (self._png(1), "image/png"))):
            with self.subTest(name):
                said, _back = self._sent(
                    {"send": True, "title": "A bed", "message": "This one.",
                     "image": "https://example.org/bed.png"},
                    {"https://example.org/bed.png": found})
                self.assertTrue(said["said"])
                self.assertEqual(said["picture"], "")

    def test_a_picture_never_comes_from_here(self) -> None:
        from personalized_agent.heartbeat.monitor import pictures
        for address in ("http://127.0.0.1/a.png", "http://localhost/a.png",
                        "http://192.168.1.9/a.png", "http://169.254.169.254/a.png"):
            with self.subTest(address):
                self.assertFalse(pictures.is_public(address))

    def test_what_the_step_sends_is_read_as_well(self) -> None:
        opened = []
        self.monitor.opener = opened.append
        spam = self._builtin("spam_risk")
        self.monitor.fire(spam, 5, ["“gift card”"], how="text",
                          window={"title": "A prize", "application": "msedge",
                                  "fresh": "Buy a gift card", "where": "email"})
        hit = self.monitor.pending_hits()[0]
        self.app.monitor_decider.models = _Model({"decisions": [
            {"hit": hit["id"], "notify": True, "title": "Probably a scam", "message": "Do not buy.",
             "url": "https://example.org/scams", "why": "gift cards"}]})
        self.app.monitor_decider.step(self.monitor)
        shown = self.app.notifications.recent(1)[0]
        question = json.loads(shown["metadata"])["question_id"]
        self.app.feedback.answer(question, "read_detail")
        self.assertEqual(opened, ["https://example.org/scams"])

    def test_every_answer_is_learned_from_in_words(self) -> None:
        said, _back = self._sent({"send": True, "title": "A fact", "message": "Beds drain.",
                                  "url": "https://example.org/beds"})
        self.app.feedback.answer(said["question_id"], "good_to_know")
        from personalized_agent.agent.feedback.templates import meaning_of
        self.assertIn("glad", meaning_of("suggestion", "good_to_know"))
        self.assertIn("opened the page", meaning_of("suggestion", "read_detail"))

    def test_a_bad_notification_leaves_nothing_standing(self) -> None:
        said, back = self._sent({"send": True, "title": "A fact", "message": "Beds drain.",
                                 "url": "https://example.org/beds"})
        self.app.bad_notification(said["notification_id"], "not interesting")
        self.assertEqual(self.monitor.bad_ones(1)[0]["activator_id"], back["id"])
        self.assertNotEqual(self.app.questions.get(said["question_id"]).status, "pending")

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

    def test_a_key_pressed_at_the_lock_screen_is_nobody_back(self) -> None:
        now = datetime.now(timezone.utc)
        self.signals.snapshot(now)
        self.idle[0] = 2400.0
        self.signals.snapshot(now + timedelta(seconds=10))
        self.idle[0] = 2.0
        self.signals._front = lambda _local: {"application": "LockApp.exe", "title": ""}
        locked = self.signals.snapshot(now + timedelta(seconds=20))
        self.assertFalse(locked["present"])
        self.assertNotIn("back", locked["events"])
        self.signals._front = lambda _local: {"application": "chrome.exe", "title": ""}
        self.assertIn("back", self.signals.snapshot(now + timedelta(seconds=30))["events"])

    def _say(self, held="", microphone=(), programs=()):
        self.signals.platform = SimpleNamespace(what_holds_the_screen=lambda: held,
                                                microphone_users=lambda: list(microphone))
        self.signals.programs = lambda: list(programs)
        self.signals._running_due = 0.0
        return self.signals.snapshot(datetime.now(timezone.utc))

    def _in_front(self, application, title):
        self.signals._front = lambda _local: {"application": application, "title": title}

    def test_what_is_running_is_a_signal(self) -> None:
        said = self._say(programs=["chrome", "zoom"])
        self.assertEqual(said["running"], "chrome, zoom")
        self.assertFalse(said["in_meeting"], "a meeting program open is no meeting")

    def test_a_lock_screen_is_a_signal(self) -> None:
        self._in_front("LockApp.exe", "")
        self.assertTrue(self._say()["screen_locked"])
        self._in_front("chrome.exe", "Seeds")
        self.assertFalse(self._say()["screen_locked"])

    def test_slides_shown_are_told_from_reading_about_them(self) -> None:
        self._in_front("POWERPNT.EXE", "PowerPoint Slide Show - Beds.pptx")
        self.assertTrue(self._say()["presenting"])
        self._in_front("POWERPNT.EXE", "Beds.pptx - PowerPoint")
        self.assertFalse(self._say()["presenting"], "editing them")
        self._in_front("chrome.exe", "Beds - Google Slides")
        self.assertFalse(self._say()["presenting"], "editing them in a page")
        self.assertTrue(self._say(held="fullscreen")["presenting"], "over the whole screen")
        self._in_front("chrome.exe", "A film")
        said = self._say(held="fullscreen")
        self.assertTrue(said["full_screen"])
        self.assertFalse(said["presenting"], "a film is no presentation")
        self.assertTrue(self._say(held="presentation")["presenting"], "the system says so")

    def test_a_meeting_is_told_by_its_window_process_or_microphone(self) -> None:
        self._in_front("Zoom.exe", "Zoom Meeting")
        self.assertTrue(self._say()["in_meeting"])
        self._in_front("Zoom.exe", "Zoom Workplace")
        self.assertFalse(self._say()["in_meeting"])
        self._in_front("Code.exe", "beds.py")
        self.assertTrue(self._say(programs=["CptHost"])["in_meeting"], "behind other windows")
        self.assertTrue(self._say(microphone=["msteams"])["in_meeting"])
        self.assertFalse(self._say(microphone=["chrome"])["in_meeting"], "voice typing in a page")
        self.signals._pages_lately = lambda _local: ["https://docs.example.org/notes"]
        self.assertFalse(self._say(microphone=["chrome"])["in_meeting"])
        self.signals._pages_lately = lambda _local: ["https://meet.google.com/abc-defg-hij"]
        self.assertTrue(self._say(microphone=["chrome"])["in_meeting"], "a meeting's own address")
        self.assertFalse(self._say()["in_meeting"], "the address alone: it may have been left")
        self.assertFalse(self._say(microphone=["audacity"])["in_meeting"], "recording is no call")
        self._in_front("chrome.exe", "Meet - abc-defg-hij")
        self.assertTrue(self._say()["in_meeting"])
        self._in_front("chrome.exe", "How to meet deadlines")
        self.assertFalse(self._say()["in_meeting"])

    def test_the_system_can_be_asked_here(self) -> None:
        from personalized_agent.platform_layer import get_platform
        here = get_platform().activity
        self.assertIn(here.what_holds_the_screen(), ("", "fullscreen", "presentation"))
        self.assertIsInstance(here.microphone_users(), list)

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
        self.assertEqual(len(self.monitor.status()["quiet"]), 3, "with the two from the start")

    def test_a_bad_condition_is_refused(self) -> None:
        self.assertIn("error", self.tools.call("monitor_quiet_add", {
            "name": "x", "why": "y", "when": {"signal": "no_such", "op": "==", "value": 1}}))

    def test_taken_back_once_each(self) -> None:
        one = self.free[0]
        before = self.store.get(one["id"])["cooldown_minutes"]
        self._cases(one)
        self._edit(one, [], cooldown=before + 7)
        from_the_start = self.store.quiet_rules()
        quiet = self.store.add_quiet("In a meeting", {"signal": "activity", "op": "==", "value": "meeting"},
                                     "chat", "asked")
        ids = [row["id"] for row in self.store.changes(limit=2)]
        self.assertEqual(self.store.undo(ids), 2)
        self.assertEqual(self.store.get(one["id"])["cooldown_minutes"], before)
        self.assertEqual(self.store.quiet_rules(), from_the_start)
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

    def _with_events(self, events, ticks=(0,)):
        """What the signals make of a made-up history, as the running agent looks every so often."""
        from datetime import timedelta
        from personalized_agent.data_manage.storage.raw_store import RawStore
        from personalized_agent.heartbeat.monitor import signals as sig
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        made = agent.app.monitor_signals
        RawStore(made.db).add_events(events)
        made._set_kv(sig.ALIVE_KEY, self.now.isoformat())
        said = []
        for minutes in ticks:
            when = self.now + timedelta(minutes=minutes)
            made.record_if_due(when)
            live = made.live_window(when)
            said.append(live.since_break_min if live is not None else 0.0)
        return said, made.store.last(2000)

    def _focused(self, at):
        from personalized_agent.common.events.models import ListenerEvent
        return ListenerEvent(event_type="application.focused", source="application",
                             application="Notes", timestamp=at,
                             data={"window_title": "A note", "activation_time": at.isoformat()})

    def _ended(self, at, seconds):
        from personalized_agent.common.events.models import ListenerEvent
        return ListenerEvent(event_type="application.focus_ended", source="application",
                             application="Notes", timestamp=at,
                             data={"window_title": "A note", "duration_seconds": seconds,
                                   "deactivation_time": at.isoformat()})

    def _presence(self, kind, at):
        from personalized_agent.common.events.models import ListenerEvent
        return ListenerEvent(event_type=f"presence.{kind}", source="application", timestamp=at,
                             data={})

    def test_days_away_are_not_at_the_screen_when_hands_off_came_after(self) -> None:
        """hands_off is stamped later than the away it comes with: it hid the absence under way."""
        from datetime import datetime, timedelta, timezone
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        left = self.now - timedelta(days=3)
        said, _rows = self._with_events(
            [self._focused(left - timedelta(hours=1)), self._presence("back", left - timedelta(hours=1)),
             self._presence("away", left), self._presence("hands_off", left + timedelta(minutes=3))],
            ticks=(0, 15, 30))
        self.assertLess(max(said), 90, "an hour before leaving at most, not three days")

    def test_before_absences_were_recorded_no_stretch_runs(self) -> None:
        """The first build reaches eight days back: 11530 minutes "without a break"."""
        from datetime import datetime, timedelta, timezone
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        day = timedelta(days=1)
        events = []
        for back in range(10, 0, -1):
            start = self.now - back * day
            events += [self._focused(start),
                       self._ended(start + day - timedelta(minutes=1), 86340.0)]
        began = self.now - timedelta(minutes=40)
        said, rows = self._with_events(events + [self._presence("back", began)])
        # Forty minutes, and the window they began in counted whole.
        self.assertLessEqual(max(said), 55, "only since they have been")
        self.assertLessEqual(max(one.since_break_min for one in rows), 55)
        self.assertGreater(len(rows), 700, "what the windows show stays")

    def test_where_absences_were_never_recorded_no_stretch_is_told(self) -> None:
        from datetime import datetime, timedelta, timezone
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        start = self.now - timedelta(days=2)
        said, rows = self._with_events([self._focused(start),
                                        self._ended(self.now - timedelta(minutes=1), 172740.0)])
        self.assertEqual(max(said), 0)
        self.assertEqual(max(one.since_break_min for one in rows), 0)

    def _on_a_mac(self, windows, programs=((1, 0),), fails=False):
        """count_windows with the Mac's libraries made up: this runs on any system."""
        import contextlib
        import sys
        import types
        from personalized_agent.platform_layer.macos import MacActivityProvider
        objc = types.SimpleNamespace(autorelease_pool=contextlib.nullcontext)
        running = [mock.Mock(**{"processIdentifier.return_value": pid,
                                "activationPolicy.return_value": policy})
                   for pid, policy in programs]
        appkit = types.SimpleNamespace(NSWorkspace=mock.Mock(**{
            "sharedWorkspace.return_value.runningApplications.return_value": running}))
        quartz = types.SimpleNamespace(
            kCGWindowListOptionAll=0, kCGWindowListExcludeDesktopElements=16, kCGNullWindowID=0,
            CGWindowListCopyWindowInfo=mock.Mock(
                side_effect=OSError("no") if fails else None, return_value=windows))
        with mock.patch.dict(sys.modules, {"objc": objc, "AppKit": appkit, "Quartz": quartz}):
            said = MacActivityProvider.count_windows(object.__new__(MacActivityProvider))
        return said, quartz

    @staticmethod
    def _window(pid=1, layer=0, alpha=1.0, width=800, height=600):
        return {"kCGWindowOwnerPID": pid, "kCGWindowLayer": layer, "kCGWindowAlpha": alpha,
                "kCGWindowBounds": {"X": 0, "Y": 0, "Width": width, "Height": height}}

    def test_on_a_mac_the_windows_of_the_programs_in_the_dock(self) -> None:
        one = self._window
        said, _quartz = self._on_a_mac(
            [one(), one(), one(pid=2), one(layer=25), one(alpha=0), one(width=20), one(height=20)],
            programs=((1, 0), (2, 1)))
        self.assertEqual(said, 2, "not a helper's, a menu's, an unseen or a tiny one")

    def test_on_a_mac_another_space_is_not_closing_them(self) -> None:
        _said, quartz = self._on_a_mac([])
        quartz.CGWindowListCopyWindowInfo.assert_called_once_with(16, 0)   # all, not on screen only

    def test_on_a_mac_that_cannot_say_it_says_nothing(self) -> None:
        self.assertIsNone(self._on_a_mac([], fails=True)[0])

    def _counted(self, counts):
        from personalized_agent.heartbeat.monitor import signals as sig
        made = object.__new__(sig.Signals)
        made._window_counts = []
        made.platform = mock.Mock()
        said = []
        for at, count in counts:
            made.platform.count_windows.return_value = count
            with mock.patch.object(sig.time, "monotonic", return_value=float(at)):
                said.append(made._closing_up())
        return said

    def test_many_windows_closed_is_said_once(self) -> None:
        self.assertEqual(self._counted([(0, 10), (60, 9), (120, 5), (130, 5), (140, 5)]),
                         [False, False, True, False, False])

    def test_a_few_closed_or_closed_slowly_is_nothing(self) -> None:
        self.assertEqual(self._counted([(0, 20), (60, 15)]), [False, False], "a small share")
        self.assertEqual(self._counted([(0, 10), (120, 9), (240, 8), (360, 7), (480, 6)]),
                         [False] * 5, "over a long while")

    def test_where_it_cannot_be_said_it_is_nothing(self) -> None:
        self.assertEqual(self._counted([(0, None), (10, None)]), [False, False])

    def test_closing_up_of_an_evening_sends_the_report_unasked(self) -> None:
        from personalized_agent.heartbeat.monitor import builtins as made, decide
        one = next(one for one in made.builtins() if one["builtin"] == "evening_closing")
        self.assertEqual(one["then"][0]["handler"], "evening_now")
        kept = {}
        monitor = mock.Mock()
        monitor._kv.side_effect = lambda key: kept.get(key, "")
        monitor._set_kv.side_effect = kept.__setitem__
        decider = object.__new__(decide.Decider)
        decider.config = {}
        with mock.patch.object(decider, "write_evening", return_value={"sent": True}) as wrote:
            self.assertEqual(decider.evening(monitor, one, {}, "f1", at_once=True), {"sent": True})
            wrote.assert_called_once()
            self.assertEqual(decider.evening(monitor, one, {}, "f2", at_once=True),
                             {"skipped": "the evening report is being written"})
            for key in list(kept):
                kept[key] = "declined"
            self.assertIn("skipped", decider.evening(monitor, one, {}, "f3", at_once=True),
                          "Not today holds")
            wrote.assert_called_once()

    def _evening(self):
        from personalized_agent.heartbeat.monitor import decide
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        kept = {}
        monitor = SimpleNamespace(
            _kv=lambda key: kept.get(key, ""), _set_kv=kept.__setitem__,
            questions=agent.app.questions, notifications=agent.app.notifications,
            say=mock.Mock(return_value={"said": True}))
        decider = object.__new__(decide.Decider)
        decider.config = agent.app.config
        decider.tasks = None
        decider.signals = SimpleNamespace(store=SimpleNamespace(since=lambda _at: []),
                                          habits_text=lambda: "")
        decider._ask = mock.Mock(return_value="A made-up day.")
        one = {"id": "act_evening", "name": "The evening report", "group": "rest"}
        context = {"signals": {"worked_today_minutes": 360}}
        return monitor, decider, one, context

    def _answer(self, decider, monitor):
        return decider.on_evening_answer(monitor, {"id": "f1", "context": "{}"}, "accept")

    def test_the_evening_question_is_not_asked_again_while_it_waits(self) -> None:
        """The user, 2026-11-06: asked at 18:20 and at 21:00, Now on both, and two reports."""
        monitor, decider, one, context = self._evening()
        self.assertTrue(decider.evening(monitor, one, context, "f1")["asked"])
        self.assertIn("skipped", decider.evening(monitor, one, context, "f2"))
        self.assertEqual(len(monitor.notifications.waiting()), 1)

    def test_dismissed_unanswered_it_may_be_asked_again(self) -> None:
        monitor, decider, one, context = self._evening()
        decider.evening(monitor, one, context, "f1")
        monitor.notifications.mark_read(monitor.notifications.waiting()[0]["id"])
        self.assertTrue(decider.evening(monitor, one, context, "f2")["asked"])

    def test_now_pressed_twice_writes_one_report(self) -> None:
        monitor, decider, _one, _context = self._evening()
        self._answer(decider, monitor)
        self._answer(decider, monitor)
        decider._ask.assert_called_once()
        monitor.say.assert_called_once()

    def test_now_pressed_while_it_is_being_written_writes_no_other(self) -> None:
        monitor, decider, _one, _context = self._evening()
        writing, go = threading.Event(), threading.Event()

        def slowly(*_args, **_kwargs):
            writing.set()
            go.wait(5)
            return "A made-up day."
        decider._ask.side_effect = slowly
        first = threading.Thread(target=self._answer, args=(decider, monitor))
        first.start()
        self.assertTrue(writing.wait(5))
        self._answer(decider, monitor)
        go.set()
        first.join(5)
        decider._ask.assert_called_once()

    def test_written_nothing_is_left_to_answer(self) -> None:
        monitor, decider, one, context = self._evening()
        asked = decider.evening(monitor, one, context, "f1")
        decider.write_evening(monitor, {}, "f2")
        self.assertEqual(monitor.notifications.waiting(), [])
        self.assertEqual(monitor.questions.get(asked["question_id"]).status, "expired")

    def test_not_written_it_can_be_asked_for_again(self) -> None:
        monitor, decider, _one, _context = self._evening()
        decider._ask.side_effect = [RuntimeError("no model"), "A made-up day."]
        with self.assertRaises(RuntimeError):
            self._answer(decider, monitor)
        self._answer(decider, monitor)
        monitor.say.assert_called_once()

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


class ALotOfNewTextTests(unittest.TestCase):
    """The user, 2026-11-06: a lot of text unlike the words of late sets it off, and a model decides."""

    WHEN = {"new_text": {"least_chars": 800, "unlike": 0.9, "minutes": 30}}
    NEW = " ".join(f"harbourword{n}" for n in range(100))
    KNOWN = {f"knownword{n}" for n in range(300)}

    def _fires(self, text=NEW, where="page", known=None):
        known = self.KNOWN if known is None else known
        return act.evaluate(self.WHEN, {"text": text, "where": where,
                                        "recent_words": lambda _minutes: known})[0]

    def test_a_lot_of_it_unlike_the_words_of_late(self) -> None:
        self.assertTrue(self._fires())

    def test_a_little_is_nothing(self) -> None:
        self.assertFalse(self._fires(self.NEW[:400]))

    def test_much_like_the_words_of_late_is_nothing(self) -> None:
        words = {f"harbourword{n}" for n in range(20)}
        self.assertFalse(self._fires(known=self.KNOWN | words))

    def test_not_a_terminal(self) -> None:
        self.assertFalse(self._fires(where="terminal"))

    def test_mail_and_chats_too(self) -> None:
        for where in ("email", "chat", "other"):
            self.assertTrue(self._fires(where=where), where)

    def test_with_little_to_hold_it_against_it_is_nothing(self) -> None:
        self.assertFalse(self._fires(known={"tide"}))

    def test_what_shows_a_password_is_nothing(self) -> None:
        with mock.patch("personalized_agent.runtime.documents.shows_credentials", return_value=True):
            self.assertFalse(self._fires())

    def test_says_how_it_was_set_off(self) -> None:
        said = act.evaluate(self.WHEN, {"text": self.NEW, "where": "page",
                                        "recent_words": lambda _minutes: self.KNOWN})[2]
        self.assertIn("100% of the words unlike the last 30 minutes", said[0])

    def test_only_a_built_in_one_may_ask_this(self) -> None:
        with self.assertRaises(ValueError):
            act.check(dict(GARDEN, when=self.WHEN))


class ALotOfNewTextInTheMonitorTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.monitor = self.agent.app.monitor
        self.one = next(one for one in self.monitor.store.list() if one["builtin"] == "new_text")
        self.rows = 0

    def _read(self, seconds_ago, text) -> None:
        self.rows += 1
        self.monitor.db.execute(
            "INSERT INTO raw_events (id, event_type, source, timestamp, application, data, metadata, "
            "processed) VALUES (?, 'screen.text', 'test', ?, 'firefox.exe', ?, '{}', 0)",
            (f"e{self.rows}", (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat(),
             json.dumps({"text": text})))

    def test_it_asks_a_model_which_may_send_nothing(self) -> None:
        self.assertTrue(self.one["text_driven"])
        self.assertEqual(self.one["group"], "recommend")
        self.assertEqual((self.one["then"][0]["do"], self.one["then"][0]["handler"]),
                         ("ask_model", "recommend"))
        self.assertIn("send nothing", self.one["then"][0]["brief"])

    def test_the_words_of_late_leave_out_the_very_latest_and_the_long_ago(self) -> None:
        self._read(3, "justread")
        self._read(120, "tidetable")
        self._read(3600, "longago")
        self.assertEqual(self.monitor.recent_words(30), {"tidetable"})

    def test_read_again_only_now_and_then(self) -> None:
        self._read(120, "tidetable")
        self.monitor.recent_words(30)
        self._read(120, "harbour")
        self.assertEqual(self.monitor.recent_words(30), {"tidetable"})

    def test_once_an_hour_across_all_windows(self) -> None:
        now = datetime.now(timezone.utc)
        self.monitor.latest = {"present": True}
        self.assertEqual(self.monitor._held(self.one, "text", now), "")
        self.monitor.db.execute(
            "INSERT INTO monitor_firings (id, activator_id, at, how, score, why, action, acted, context) "
            "VALUES ('f1', ?, ?, 'text', 0, '', 'ask_model:recommend', 1, '{}')",
            (self.one["id"], (now - timedelta(minutes=20)).isoformat()))
        self.assertEqual(self.monitor._held(self.one, "text", now), "in its cooldown")


class WhatASearchMayCarryTests(unittest.TestCase):
    """The user, 2026-11-06: mail and chats may set it off, and their words stay out of a search."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app

    def _guard_for(self, where):
        decider, kept = self.app.monitor_decider, []

        def asked(*_args, guard=None, **_more):
            kept.append(guard)
            return '{"send": false}'

        one = next(a for a in self.app.monitor.store.list() if a["builtin"] == "new_text")
        context = {"activator": {"name": one["name"]}, "signals": {}, "why": [], "window": {
            "title": "Inbox", "where": where, "new_text": "Dear Ada Marlow, invoice 7741902 is due",
            "text": ""}}
        with mock.patch.object(decider, "_ask", side_effect=asked):
            decider.recommend(self.app.monitor, one, one["then"][0], context, "f1")
        return kept[0]

    def test_of_mail_a_name_and_a_number_are_refused(self) -> None:
        guard = self._guard_for("email")
        self.assertIn("private", guard.check_query("ada marlow invoice"))
        self.assertIn("private", guard.check_query("what is 7741902"))
        self.assertIsNone(guard.check_query("how invoices fall due"))

    def test_of_a_page_its_own_words_may_be_searched(self) -> None:
        self.assertIsNone(self._guard_for("page").check_query("Ada Marlow invoice 7741902"))

    def test_the_model_is_told(self) -> None:
        from personalized_agent.heartbeat.monitor.decide import RECOMMEND_SYSTEM
        self.assertIn("A SEARCH LEAVES THIS COMPUTER", RECOMMEND_SYSTEM)


class AVideoInFocusTests(unittest.TestCase):
    """The user, 2026-11-06: which window is on top says what they are at, not what an assistant
    is doing in a folder."""

    NOW = datetime(2026, 3, 11, 18, 10, tzinfo=timezone.utc)

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.made = self.agent.app.monitor_signals
        self.rows = 0

    def _focus(self, seconds_ago, title, application="firefox.exe") -> None:
        self.rows += 1
        self.made.db.execute(
            "INSERT INTO raw_events (id, event_type, source, timestamp, application, data, metadata, "
            "processed) VALUES (?, 'application.focused', 'test', ?, ?, ?, '{}', 0)",
            (f"e{self.rows}", (self.NOW - timedelta(seconds=seconds_ago)).isoformat(), application,
             json.dumps({"window_title": title})))

    def test_watching_for_a_while(self) -> None:
        self._focus(120, "notes.txt - Notepad", "notepad.exe")
        self._focus(40, "Tide tables explained - YouTube")
        self.assertTrue(self.made._watching(self.NOW))

    def test_a_glance_is_not_watching(self) -> None:
        self._focus(120, "notes.txt - Notepad", "notepad.exe")
        self._focus(10, "Tide tables explained - YouTube")
        self.assertFalse(self.made._watching(self.NOW))

    def test_one_video_after_another_is_one_stretch(self) -> None:
        self._focus(120, "notes.txt - Notepad", "notepad.exe")
        self._focus(50, "Tide tables explained - YouTube")
        self._focus(5, "Spring tides - YouTube")
        self.assertTrue(self.made._watching(self.NOW))

    def test_back_at_work_it_is_over(self) -> None:
        self._focus(300, "Tide tables explained - YouTube")
        self._focus(10, "notes.txt - Notepad", "notepad.exe")
        self.assertFalse(self.made._watching(self.NOW))

    def test_nothing_seen_is_not_watching(self) -> None:
        self.assertFalse(self.made._watching(self.NOW))

    def _placed(self, paths_say="harbour") -> SimpleNamespace:
        live = SimpleNamespace(start=self.NOW - timedelta(minutes=5), minutes=5, project_id="", project="")
        self.made._labelling = {"started_in": {}, "matcher": None, "index": None}
        with mock.patch.object(self.made, "_place_by_paths", return_value=(paths_say, "Harbour")) as paths, \
                mock.patch.object(self.made, "_place_by_words", return_value=("", "")) as words:
            self.made._place_live(live, [], self.NOW, self.NOW)
        live.asked = (paths.called, words.called)
        return live

    def test_the_paths_stand_aside(self) -> None:
        self._focus(40, "Tide tables explained - YouTube")
        live = self._placed()
        self.assertEqual((live.project_id, live.asked), ("", (False, False)))

    def test_elsewhere_the_paths_speak(self) -> None:
        self._focus(40, "notes.txt - Notepad", "notepad.exe")
        live = self._placed()
        self.assertEqual((live.project_id, live.asked), ("harbour", (True, False)))

    def test_the_words_on_the_screen_place_nothing_under_way(self) -> None:
        self._focus(40, "notes.txt - Notepad", "notepad.exe")
        self.assertEqual((self._placed(paths_say="").project_id), "")


class WhatAWindowTeachesTests(unittest.TestCase):
    """The user, 2026-11-06: of what is in a window, the model says what belongs to which."""

    START = datetime(2026, 3, 11, 17, 15, tzinfo=timezone.utc)

    def _index(self):
        from personalized_agent.heartbeat.monitor.projects import ProjectIndex
        a, b = self.START.isoformat(), (self.START + timedelta(minutes=15)).isoformat()
        rows = {"state_projects": [{"id": "p1", "name": "Harbour", "tasks": '["t1"]'}],
                "state_tasks": [{"id": "t1", "members": '["s1"]', "described": "{}", "archived_at": ""}],
                "FROM states": [
                    {"id": "s1", "title": "Survey", "detail": "", "starts_at": a, "ends_at": b,
                     "metadata": json.dumps({"sources": ["WindowsTerminal.exe window 'Tide work'"]})},
                    {"id": "s2", "title": "The week", "detail": "", "starts_at": a, "ends_at": b,
                     "metadata": json.dumps({"sources": [
                         "firefox.exe window 'Calendar - Week of March 9 \u2014 Mozilla Firefox'",
                         "https://example.org/watch/1"]})},
                    {"id": "s3", "title": "Old", "detail": "", "starts_at": a, "ends_at": b,
                     "metadata": "{}"}]}
        db = SimpleNamespace(query=lambda sql, _args=(): next(
            found for key, found in rows.items() if key in sql))
        return ProjectIndex(db, self.START, self.START + timedelta(minutes=15))

    def test_what_only_another_state_names_teaches_nothing_of_this_one(self) -> None:
        window = SimpleNamespace(start=self.START, minutes=15)
        items = {"t:tide work": 300.0, "t:calendar - week of march 9 — mozilla firefox": 500.0,
                 "t:unnamed": 30.0,
                 "h:example.org": 30.0}
        self.assertEqual(self._index().taught(window, "p1", items),
                         {"t:tide work": 300.0, "t:unnamed": 30.0, "h:example.org": 30.0})

    def test_a_window_elsewhere_is_as_it_was(self) -> None:
        window = SimpleNamespace(start=self.START + timedelta(hours=3), minutes=15)
        items = {"t:calendar - week of march 9": 500.0}
        self.assertEqual(self._index().taught(window, "p1", items), items)

    def test_what_a_source_names(self) -> None:
        from personalized_agent.heartbeat.monitor.operations import named_in
        self.assertEqual(named_in("firefox.exe window 'Tides \u2014 Mozilla Firefox'"), {"t:tides — mozilla firefox"})
        self.assertEqual(named_in("https://example.org/watch/1"), set())
        said = named_in("C:\\Survey\\scripts\\fetch.py")
        self.assertLessEqual({"f:c:/survey/scripts/fetch.py", "d:survey/scripts", "p:fetch.py"}, said)
        self.assertEqual(named_in(""), {"f:"})


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


class ScatteredIsTheirOwnTests(unittest.TestCase):
    def _signals(self, rates):
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        signals = agent.app.monitor_signals
        now = datetime.now(timezone.utc)
        windows = [Window(start=now - timedelta(hours=1), minutes=15, main="work", switch_rate=rate)
                   for rate in rates]
        return signals, now, mock.patch.object(signals.store, "since", return_value=windows)

    def test_the_top_quarter_of_their_own_weeks(self) -> None:
        signals, now, kept = self._signals(range(1, 101))
        with kept:
            self.assertEqual(signals.scattered_at(now), 75)

    def test_read_once_a_while_not_at_every_look(self) -> None:
        signals, now, kept = self._signals(range(1, 101))
        with kept as since:
            signals.scattered_at(now)
            signals.scattered_at(now)
        self.assertEqual(since.call_count, 1)

    def test_too_few_windows_say_nothing_of_them(self) -> None:
        signals, now, kept = self._signals([200] * 10)
        with kept:
            self.assertEqual(signals.scattered_at(now), 12)

    def test_the_look_goes_by_it(self) -> None:
        signals, now, kept = self._signals(range(1, 101))
        live = Window(start=now, minutes=15, main="work", switch_rate=60.0)
        with kept, mock.patch.object(signals, "live_window", return_value=live):
            self.assertNotEqual(signals.snapshot(now)["depth"], "scattered", "ordinary for them")
            live.switch_rate = 90.0
            self.assertEqual(signals.snapshot(now)["depth"], "scattered")


class AGlanceIsNotASwitchTests(unittest.TestCase):
    def test_only_a_stay_of_two_seconds_counts_as_a_focus_change(self) -> None:
        from personalized_agent.heartbeat.monitor import windows as ws
        start = datetime(2026, 11, 5, 9, tzinfo=timezone.utc)

        def stay(at, seconds):
            ended = start + timedelta(seconds=at + seconds)
            return {"event_type": "application.focus_ended", "timestamp": ended.isoformat(),
                    "application": "Tides", "data": {"deactivation_time": ended.isoformat(),
                                                     "duration_seconds": seconds,
                                                     "window_title": "Tide tables"}}
        events = [stay(0, 300), stay(300, 0.5), stay(301, 1.9), stay(303, 2.0), stay(305, 595)]
        window = ws.build_windows(start, start + timedelta(minutes=15), 15, events)[0]
        self.assertEqual(window.switch_rate, 3 * 4, "three stays in a quarter of an hour")


class WhatIsInFrontOnThePanelTests(unittest.TestCase):
    def test_read_as_it_is_asked_for_not_as_at_the_last_look(self) -> None:
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        monitor = agent.app.monitor
        self.assertEqual(monitor.status()["signals"], {}, "nothing looked at yet: not half of it")
        monitor.latest = {"mode": "working", "application": "Old", "title": "Old window"}
        agent.app.db.execute(
            "INSERT INTO raw_events (id, timestamp, source, event_type, application, data) "
            "VALUES ('e1', ?, 'application', 'application.focused', 'Tides', ?)",
            (datetime.now(timezone.utc).isoformat(), json.dumps({"window_title": "Tide tables"})))
        said = monitor.status()["signals"]
        self.assertEqual((said["application"], said["title"], said["mode"]),
                         ("Tides", "Tide tables", "working"))


if __name__ == "__main__":
    unittest.main()
