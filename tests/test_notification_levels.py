"""TWO LEVELS OF NOTIFICATION (the user, 2026-11-06): one pops, one waits in a list opened by hand.
A risk comes through a blocker. Made-up notifications."""

from __future__ import annotations

import json
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import unittest
from collections import deque
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.agent.feedback.store import TaskQuestion
from personalized_agent.agent.feedback.templates import SUGGESTION
from personalized_agent.common.notifications import COLLAPSE, POP, told_from
from personalized_agent.heartbeat.core.triggers import Rule, notify_now
from personalized_agent.heartbeat.monitor import reserve as rs
from personalized_agent.heartbeat.monitor.reserve import chain_line
from personalized_agent.scheduler.execution import executors as ex
from personalized_agent.ui.avatar_buttons import QuickPanel
from personalized_agent.ui.desktop import DesktopAgent

WORDS = {"proper": ["Thistle Harbour"], "zh": ["涨潮"], "en": ["tide table", "spring tide"]}


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.notes = self.app.notifications


class TheLevelsTests(_Base):
    def test_it_pops_unless_its_sender_says_otherwise(self) -> None:
        self.assertEqual(self.notes.notify("A", "a").metadata["level"], POP)
        self.assertEqual(self.notes.notify("B", "b", level=COLLAPSE).metadata["level"], COLLAPSE)

    def test_what_goes_wrong_and_what_is_stuck_pop_whatever_is_said(self) -> None:
        for kind in ("error", "warning", "approval"):
            said = self.notes.notify(kind, kind, kind=kind, level=COLLAPSE)
            self.assertEqual(said.metadata["level"], POP, kind)

    def test_the_list_holds_what_waits_until_it_is_dealt_with(self) -> None:
        self.notes.notify("Pops", "now")
        kept = self.notes.notify("Waits", "later", level=COLLAPSE)
        self.assertEqual([one["title"] for one in self.notes.waiting()], ["Waits"])
        self.assertEqual(self.notes.waiting()[0]["metadata"]["level"], COLLAPSE)
        self.assertEqual(self.notes.waiting_count(), 1)
        self.notes.mark_read(kept.id)
        self.assertEqual(self.notes.waiting_count(), 0)

    def test_a_row_that_is_not_json_is_passed_over(self) -> None:
        self.app.db.execute("INSERT INTO notifications (id, created_at, kind, title, message, "
                            "metadata, read) VALUES ('old', '2026-01-01', 'info', 't', 'm', 'x', 0)")
        self.assertEqual(self.notes.waiting_count(), 0)

    def test_the_lesson_says_how_it_was_shown(self) -> None:
        self.assertIn("interrupted", chain_line({"level": "pop"}))
        self.assertIn("waited in the list", chain_line({"level": "collapse"}))


class TheMonitorsTests(_Base):
    def _say(self, group="recommend", **more):
        context = {"activator": {"id": "a", "name": "A moment", "group": group}}
        self.fired = getattr(self, "fired", 0) + 1
        said = self.app.monitor.say("Title", "Words", context, f"f{self.fired}", **more)
        return self.notes.get(said["notification_id"])["metadata"]

    def test_a_recommendation_waits(self) -> None:
        self.assertEqual(self._say(recommended=True, content=True)["level"], COLLAPSE)

    def test_by_the_triggers_own_setting_not_the_models_word(self) -> None:
        """The user, 2026-11-07: popped or waiting by each trigger's delivery, done by code."""
        context = {"activator": {"id": "a", "name": "A moment", "group": "recommend",
                                 "delivery": "pop"}}
        said = self.app.monitor.say("Title", "Words", context, "fx", recommended=True, content=True)
        self.assertEqual(self.notes.get(said["notification_id"])["metadata"]["level"], POP)

    def test_a_break_waits_and_a_risk_pops(self) -> None:
        """The user, 2026-11-07: what a trigger sends by itself waits; a risk comes up at once."""
        self.assertEqual(self._say("rest")["level"], COLLAPSE)
        self.assertEqual(self._say("risk")["level"], POP)

    def test_only_a_risk_comes_through_a_blocker(self) -> None:
        self.assertTrue(self._say("risk")["through"])
        self.assertFalse(self._say("rest")["through"])

    def _matched(self, answer):
        reserve = self.app.reserve
        ident = reserve.add("Harbour tables", "https://tides.example/t", detail="New", status=rs.READY,
                            angle="An angle", why="A why", keywords=WORDS)
        rule = self.app.reserve_rule
        rule.activity = SimpleNamespace(
            get_active_window=lambda: SimpleNamespace(title="Tides", application="b", pid=1),
            front_window_image=lambda: SimpleNamespace(width=2000, height=1000))
        rule._live_at = 0.0
        self.app.monitor.latest = {"present": True}
        self.app.monitor_decider.models = SimpleNamespace(invoke=lambda *_a, **_k: SimpleNamespace(
            text=json.dumps(answer), tool_calls=[], usage=None))
        with mock.patch("personalized_agent.runtime.documents.ocr_picture",
                        return_value="tide table and spring tide"):
            said = rule.act("Tides", "b", "", "")
        self.assertEqual(reserve.get(ident)["status"], rs.TOLD)
        return self.notes.get(said["notification_id"])["metadata"]

    def test_a_match_waits_when_the_model_only_says_yes(self) -> None:
        self.assertEqual(self._matched({"yes": True})["level"], COLLAPSE)

    def test_a_match_waits_whatever_the_model_says_of_popping(self) -> None:
        self.assertEqual(self._matched({"yes": True, "pop": True})["level"], COLLAPSE)

    def test_a_match_not_known_to_be_on_the_screen_is_still_sent(self) -> None:
        """The user, 2026-11-07, "B": 13 of 14 matches stopped at not knowing what was in front - in
        the same program (2026-11-08: another program in front is moved on, test_reserve_match)."""
        reserve = self.app.reserve
        ident = reserve.add("Harbour tables", "https://tides.example/t", detail="New",
                            status=rs.READY, angle="An angle", why="A why", keywords=WORDS)
        rule = self.app.reserve_rule
        rule.activity = SimpleNamespace(              # another window of the same program now
            get_active_window=lambda: SimpleNamespace(title="Mail", application="b", pid=2),
            front_window_image=lambda: None)
        rule._live_at = 0.0
        self.app.monitor.latest = {"present": True}
        self.app.monitor_decider.models = SimpleNamespace(invoke=lambda *_a, **_k: SimpleNamespace(
            text=json.dumps({"yes": True}), tool_calls=[], usage=None))
        said = rule.act("Tides", "b", "", "tide table and spring tide")
        self.assertEqual(reserve.get(ident)["status"], rs.TOLD)
        self.assertEqual(self.notes.get(said["notification_id"])["metadata"]["level"], COLLAPSE)


class TheListTests(_Base):
    def _asked(self):
        asked = self.app.questions.ask(TaskQuestion(
            task_id="", kind=SUGGESTION, question="Harbour tables", task_detail="New tables",
            metadata={"options": [{"key": "good_to_know", "label": "Good to know",
                                   "effect": "complete"}]}))
        return self.notes.notify("Harbour tables", "New tables", kind="proactive", level=COLLAPSE,
                                 template=SUGGESTION,
                                 metadata={"question_id": asked.id, "recommended": True,
                                           "reason": "For your survey"}), asked

    def test_by_kind_each_by_its_title_opening_on_its_words_and_answers(self) -> None:
        self._asked()
        self.notes.notify("Updated", "A new version", kind="info", level=COLLAPSE)
        sections = self.app.quick_sections("waiting")
        self.assertEqual([len(one["rows"]) for one in sections], [1, 1])
        row = sections[0]["rows"][0]
        self.assertTrue(row["folds"])
        self.assertEqual((row["title"], row["detail"]), ("Harbour tables", "New tables"))
        self.assertIn("For your survey", row["why"])
        self.assertIn("Good to know", [one["label"] for one in row["actions"]])
        self.assertIn("/api/notifications/bad", [one["path"] for one in row["actions"]])
        other = sections[1]["rows"][0]
        self.assertEqual(other["actions"][0]["path"], "/api/notifications/read")

    def test_one_alone_is_open_and_the_last_of_two_opens(self) -> None:
        self._asked()
        self.assertFalse(self.app.quick_sections("waiting")[0]["rows"][0]["folds"])
        other = self.notes.notify("Updated", "A new version", kind="info", level=COLLAPSE)
        rows = [row for one in self.app.quick_sections("waiting") for row in one["rows"]]
        self.assertEqual([row["folds"] for row in rows], [True, True])
        self.notes.mark_read(other.id)
        self.assertFalse(self.app.quick_sections("waiting")[0]["rows"][0]["folds"])

    def test_answered_in_the_list_it_is_answered_and_gone(self) -> None:
        said, asked = self._asked()
        row = self.app.quick_sections("waiting")[0]["rows"][0]
        action = next(one for one in row["actions"] if one["path"] == "/api/notifications/answer")
        from personalized_agent.api.routes import build_routes
        request = SimpleNamespace(require=action["body"].__getitem__, get=action["body"].get)
        self.assertEqual(build_routes(self.app)[("POST", action["path"])](request), {"ok": True})
        self.assertEqual(self.notes.waiting_count(), 0)
        self.assertEqual(self.app.questions.get(asked.id).status, "answered")
        self.assertTrue(self.notes.get(said.id)["read"])


class InTheirOwnWordsTests(_Base):
    """The user, 2026-11-08: "recommendation 也允许我用语言回复"."""

    def test_a_recommendation_answered_in_words_is_a_lesson_and_gone(self) -> None:
        said, _asked = TheListTests._asked(self)
        row = self.app.quick_sections("waiting")[0]["rows"][0]
        reply = next(one for one in row["actions"] if one["key"] == "reply")
        self.assertTrue(reply["writes"])
        self.assertEqual(reply["keys"], ["text"])
        body = dict(reply["body"], text="I read this one already, more on tide gauges")
        from personalized_agent.api.routes import build_routes
        request = SimpleNamespace(require=body.__getitem__, get=body.get)
        with mock.patch.object(self.app.lessons, "record", wraps=self.app.lessons.record) as kept:
            build_routes(self.app)[("POST", reply["path"])](request)
        self.assertEqual(self.notes.waiting_count(), 0)
        self.assertTrue(self.notes.get(said.id)["read"])
        told = [call for call in kept.call_args_list if call.args and call.args[0] == "recommendations"]
        self.assertTrue(told, "a lesson of what is recommended")
        self.assertIn("more on tide gauges", repr(told[0]))

    def test_every_one_waiting_can_be_answered_so(self) -> None:
        self.notes.notify("Updated", "A new version", kind="info", level=COLLAPSE)
        row = self.app.quick_sections("waiting")[0]["rows"][0]
        self.assertIn("reply", [one["key"] for one in row["actions"]])


class WhatSetItOffTests(_Base):
    """The user, 2026-11-06: each one says which trigger of the Monitor it came from."""

    def test_and_what_exactly_set_it_off(self) -> None:
        """The user, 2026-11-08: "这个为什么trigger, 要说出关键词是什么 ... 或者关键condition是什么"."""
        from personalized_agent.common.notifications import what_set_it_off
        matched = {"monitor": {"activator": "Something kept ready", "context": {
            "window": {"title": "harbour - Slack"},
            "chain": {"hit": ["tide tables", "ferry survey"]}}}}
        self.assertEqual(what_set_it_off(matched),
                         "the words on the screen: tide tables, ferry survey (in harbour - Slack)")
        held = {"monitor": {"activator": "Back after an absence", "context": {
            "why": ["event back", "back_after_minutes 183.0", "present True", "hour 20.5"]}}}
        self.assertEqual(what_set_it_off(held),
                         "back; back after 183 min; at the computer; at 20:30")
        self.assertEqual(what_set_it_off({"made_by": "heartbeat"}), "", "nothing kept of it")

    def test_a_trigger_by_its_name_or_what_sent_it_instead(self) -> None:
        self.assertEqual(told_from({"monitor": {"activator": "A rest"}}), "A rest")
        self.assertEqual(told_from('{"monitor": {"activator": "A rest"}}'), "A rest")
        self.assertEqual(told_from({"trigger": "A meeting invitation", "made_by": "heartbeat"}),
                         "A meeting invitation")
        self.assertEqual(told_from({"made_by": "heartbeat", "source": "scheduler"}),
                         "None: the heartbeat's round")
        self.assertEqual(told_from({"source": "scheduler"}), "None: scheduled on the calendar")
        self.assertEqual(told_from({}), "None: sent by the application")
        self.assertEqual(told_from("not written well"), "None: sent by the application")

    def _scheduled(self, trigger=""):
        said = self.app.heartbeat._add_notifications("heartbeat_test", [{
            "type": "notice", "title": "Team meeting", "message": "It starts soon.",
            "why": "The invitation says so.",
            "at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()}], trigger=trigger)
        self.assertTrue(said[0]["ok"], said)
        return self.app.scheduler.store.get(said[0]["task_id"])

    def test_it_goes_from_the_calendar_to_what_is_said(self) -> None:
        task = self._scheduled("A meeting invitation")
        self.assertEqual(ex._made_by(task), {"made_by": "heartbeat",
                                             "trigger": "A meeting invitation"})
        self.assertEqual(ex._made_by(self._scheduled()), {"made_by": "heartbeat"})

    def test_one_from_before_says_a_trigger_made_it(self) -> None:
        task = SimpleNamespace(metadata={"heartbeat_task": "trigger_1"}, created_by="heartbeat")
        self.assertEqual(ex._made_by(task)["trigger"], "A calendar trigger")

    def test_the_monitors_calendar_trigger_gives_its_name(self) -> None:
        heartbeat = mock.Mock()
        heartbeat._notifications_ahead.return_value = []
        heartbeat.models.invoke.return_value = SimpleNamespace(text='{"notifications": []}')
        heartbeat.pending = None
        heartbeat.calendar_nature = None
        heartbeat._add_notifications.return_value = []
        heartbeat._close_notifications.return_value = []
        heartbeat._update_notifications.return_value = []
        rule = Rule("invitation", "Why", None, None, shown_as="A meeting invitation")
        notify_now(heartbeat, rule, "Mail", "An invitation")
        self.assertEqual(heartbeat._add_notifications.call_args.kwargs["trigger"],
                         "A meeting invitation")

    def test_in_the_waiting_list(self) -> None:
        self.notes.notify("Harbour tables", "New", kind="proactive", level=COLLAPSE,
                          metadata={"monitor": {"activator": "Something kept ready"},
                                    "reason": "For your survey"})
        row = self.app.quick_sections("waiting")[0]["rows"][0]
        self.assertEqual((row["why"], row["note"]),
                         ("Why: For your survey", "Trigger: Something kept ready"))

    def test_on_the_page(self) -> None:
        from personalized_agent.api.routes import build_routes
        self.notes.notify("Harbour tables", "New", kind="proactive",
                          metadata={"monitor": {"activator": "A rest"}})
        rows = build_routes(self.app)[("GET", "/api/notifications")](
            SimpleNamespace(int_param=lambda _name, default: default,
                            get=lambda _name, default=None: default))["notifications"]
        self.assertEqual(rows[0]["trigger"], "A rest")


class TheBubblesTests(_Base):
    def _desk(self, blocker=""):
        self.app.monitor.blocker = blocker
        desk = SimpleNamespace(app=self.app, _bubble=None, _showing=None, _bubble_queue=deque(),
                               _bubbles_opened=False, queue=queue.Queue(), _waiting_line=None,
                               BLOCKERS_EVERY_MS=1, _quick_panels={},
                               root=SimpleNamespace(after=lambda _ms, _then: None))
        desk._blocker = lambda: DesktopAgent._blocker(desk)
        desk._blocked = lambda: DesktopAgent._blocked(desk)
        desk._hide_waiting = lambda: None
        desk._show_next_bubble = lambda: None
        desk._close_bubble = lambda: setattr(desk, "_bubble", None)
        return desk

    def test_what_waits_makes_no_bubble(self) -> None:
        desk = self._desk()
        DesktopAgent._on_notification(desk, "", {"id": "n", "title": "T", "message": "M",
                                                 "metadata": {"level": COLLAPSE}})
        self.assertEqual(desk.queue.get_nowait(), ("waiting", None))

    def test_what_pops_says_what_set_it_off(self) -> None:
        desk = self._desk()
        DesktopAgent._on_notification(desk, "", {"id": "n", "title": "T", "message": "M", "metadata": {
            "level": POP, "monitor": {"activator": "A rest"}}})
        self.assertEqual(desk.queue.get_nowait()[1]["trigger"], "A rest")

    def test_only_what_comes_through_is_taken_while_a_blocker_holds(self) -> None:
        desk = self._desk("In a meeting")
        desk._bubble_queue.extend([{"text": "Dentist"}, {"text": "Scam", "through": True}])
        self.assertEqual(DesktopAgent._next_bubble(desk)["text"], "Scam")
        self.assertIsNone(DesktopAgent._next_bubble(desk))
        self.assertEqual(list(desk._bubble_queue), [{"text": "Dentist"}])

    def test_a_blocker_beginning_leaves_a_risk_on_the_screen(self) -> None:
        desk = self._desk("In a meeting")
        desk._bubble, desk._showing = object(), {"text": "Scam", "through": True}
        desk._watch_the_blockers = lambda: DesktopAgent._watch_the_blockers(desk)
        desk._show_waiting = lambda: None
        desk._look_for_work_waiting = lambda: None
        desk._watch_the_blockers()
        self.assertIsNotNone(desk._bubble)
        self.assertEqual(list(desk._bubble_queue), [])

    def test_the_line_counts_the_list_and_what_a_blocker_holds(self) -> None:
        self.notes.notify("Waits", "later", level=COLLAPSE)
        desk = self._desk("In a meeting")
        desk._bubble_queue.append({"text": "Dentist"})
        desk._words = lambda text: text
        hidden = []
        desk._hide_waiting = lambda: hidden.append(1)
        desk._waiting_line = object()
        desk._draw_waiting = lambda rows: setattr(desk, "said", rows)
        DesktopAgent._show_waiting(desk)
        self.assertEqual(desk.said, [("notifications", "2 notifications")],
                         "a reminder and another: both notifications")
        self.notes.mark_all_read()
        desk._bubble_queue.clear()
        DesktopAgent._show_waiting(desk)
        self.assertEqual(hidden, [1], "nothing waits: no line")

    def test_pressed_the_list_opens(self) -> None:
        self.notes.notify("Waits", "later", level=COLLAPSE)
        desk = self._desk()
        opened = []
        desk._quick_panel = opened.append
        DesktopAgent._open_waiting(desk)
        self.assertEqual(opened, ["waiting"])


class TheRowThatOpensTests(unittest.TestCase):
    def setUp(self) -> None:
        # Last of all: Tk's own objects let go of here, on the thread that made them - left to
        # the collector, they went on another test's thread and ended the run.
        self.addCleanup(self._let_go)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.posted = queue.Queue()

    def _let_go(self) -> None:
        import gc
        for name in [one for one in vars(self) if not one.startswith("_")]:
            delattr(self, name)
        gc.collect()

    def _settle(self) -> None:
        for _ in range(40):
            try:
                while True:
                    self.posted.get_nowait()()
            except queue.Empty:
                pass
            self.root.update()

    def _until(self, holds) -> None:
        moment = time.monotonic() + 5
        while not holds() and time.monotonic() < moment:
            self._settle()
        self.assertTrue(holds())

    def _read_out(self) -> None:
        """Every reading done and taken: one left going would end Tk on its own thread."""
        for one in threading.enumerate():
            if one.name == "quick-panel":
                one.join(5)
        self._settle()

    def _panel(self, sections, closes_emptied=True) -> QuickPanel:
        panel = QuickPanel(self.root, "Waiting", lambda: sections, self.posted.put, lambda text: text,
                           ("Segoe UI", 10), "Nothing is waiting.", searches=True,
                           closes_emptied=closes_emptied)
        self.addCleanup(panel.close)
        self.addCleanup(self._read_out)
        return panel

    def _one(self):
        return [{"name": "Reminders", "rows": [{"id": "n1", "title": "Harbour tables", "actions": []}]}]

    def test_the_last_one_gone_it_closes(self) -> None:
        sections = self._one()
        panel = self._panel(sections)
        panel.show((800, 500, 128, 128))
        self._until(lambda: panel.opened_holding is True)
        self.assertTrue(panel.is_open)
        sections.clear()
        panel._read_it()
        self._until(lambda: not panel.is_open)

    def test_the_empty_list_is_never_drawn_on_the_screen(self) -> None:
        """The last one answered, the list closed with a flash: drawn empty, then closed."""
        sections = self._one()
        panel = self._panel(sections)
        panel.show((800, 500, 128, 128))
        self._until(lambda: panel.opened_holding is True)
        drawn_open = []
        draw = panel._draw
        panel._draw = lambda: (drawn_open.append(panel.opened), draw())[1]
        sections.clear()
        panel._read_it()
        self._until(lambda: not panel.is_open)
        self.assertTrue(drawn_open, "the empty list is drawn, out of sight, for the next opening")
        self.assertNotIn(True, drawn_open)
        self.assertIn("Nothing is waiting.", panel.list.get("1.0", "end"))

    def test_while_one_is_left_it_stays(self) -> None:
        sections = self._one()
        sections[0]["rows"].append({"id": "n2", "title": "Tide notes", "actions": []})
        panel = self._panel(sections)
        panel.show((800, 500, 128, 128))
        self._until(lambda: panel.opened_holding is True)
        sections[0]["rows"].pop()
        panel._read_it()
        self._until(lambda: "Tide notes" not in panel.list.get("1.0", "end"))
        self.assertTrue(panel.is_open)

    def test_opened_with_nothing_it_stays(self) -> None:
        sections = self._one()
        panel = self._panel(sections)
        panel.prepare()
        self._until(lambda: bool(panel.sections))
        sections.clear()                         # gone while it was closed: what it held is old
        panel.show((800, 500, 128, 128))
        self._until(lambda: panel.opened_holding is False)
        panel._read_it()
        self._read_out()
        self.assertTrue(panel.is_open)
        self.assertIn("Nothing is waiting.", panel.list.get("1.0", "end"))

    def test_a_search_finding_nothing_closes_nothing(self) -> None:
        panel = self._panel(self._one())
        panel.show((800, 500, 128, 128))
        self._until(lambda: panel.opened_holding is True)
        panel.box.insert(0, "nowhere")
        panel._draw()
        panel._read_it()
        self._read_out()
        self.assertTrue(panel.is_open)

    def test_other_lists_stay_open_emptied(self) -> None:
        sections = self._one()
        panel = self._panel(sections, closes_emptied=False)
        panel.show((800, 500, 128, 128))
        self._until(lambda: panel.opened_holding is True)
        sections.clear()
        panel._read_it()
        self._until(lambda: "Nothing is waiting." in panel.list.get("1.0", "end"))
        self.assertTrue(panel.is_open)

    def test_why_is_small_and_slanted_and_found_by_a_search(self) -> None:
        sections = [{"name": "", "rows": [{"id": "n", "title": "Harbour tables", "detail": "New",
                                           "why": "Why: for your survey", "note": "Trigger: A rest",
                                           "actions": []}]}]
        panel = self._panel(sections)
        panel.show((800, 500, 128, 128))
        self._until(lambda: panel.opened_holding is True)
        words = panel.list
        at = words.search("Why: for", "1.0")
        self.assertIn("why", words.tag_names(at))
        self.assertNotIn("why", words.tag_names(words.search("Trigger", "1.0")))
        font = tkfont.Font(root=self.root, font=words.tag_cget("why", "font"))
        said = tkfont.Font(root=self.root, font=words.tag_cget("muted", "font"))
        self.assertEqual(font.actual("slant"), "italic")
        self.assertLess(abs(font.actual("size")), abs(said.actual("size")))
        panel.box.insert(0, "survey")
        panel._draw()
        self.assertIn("Harbour tables", words.get("1.0", "end"))

    def test_its_title_alone_until_it_is_pressed(self) -> None:
        sections = [{"name": "Reminders", "rows": [
            {"id": "n1", "title": "Harbour tables", "detail": "The new tables", "note": "Why: a survey",
             "folds": True, "actions": [{"key": "bad", "label": "Bad", "changes": True}]}]}]
        panel = QuickPanel(self.root, "Waiting", lambda: sections, self.posted.put, lambda text: text,
                           ("Segoe UI", 10), "Nothing is waiting.")
        self.addCleanup(panel.close)
        panel.show((800, 500, 128, 128))
        self._settle()
        said = panel.list.get("1.0", "end")
        self.assertIn("▸ Harbour tables", said)
        self.assertNotIn("The new tables", said)
        panel._expand("n1")
        said = panel.list.get("1.0", "end")
        self.assertIn("▾ Harbour tables", said)
        self.assertIn("The new tables", said)
        self.assertIn("Why: a survey", said)
        self.assertEqual(len(panel.faces), 1, "its answers")
        panel._expand("n1")
        self.assertNotIn("The new tables", panel.list.get("1.0", "end"))


if __name__ == "__main__":
    unittest.main()
