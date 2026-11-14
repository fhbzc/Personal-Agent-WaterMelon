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

    def test_nothing_comes_through_a_silence_a_risk_neither(self) -> None:
        """2026-11-13: "While presenting, In a meeting 这两个silence对全部monitor都有效"."""
        self.assertFalse(self._say("risk")["through"])
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

    def test_the_line_is_zoomed_as_every_other_window(self) -> None:
        """The user, 2026-11-09: "1 recommendation的字体也太大了，应该这个也是鼠标放上面可以滚轮调整"."""
        try:
            import tkinter as tk
            root = tk.Tk()
        except Exception:
            self.skipTest("no display for Tk here")
        root.withdraw()
        from personalized_agent.ui.desktop import zoom_popups
        self.notes.notify("Waits", "later", level=COLLAPSE)
        desk = self._desk()
        kept = {}
        desk.root, desk._words = root, (lambda text: text)
        desk.config = SimpleNamespace(get=lambda key, default=None: kept.get(key, default),
                                      set=lambda key, value, internal=False: kept.update({key: value}))
        for name in ("_show_waiting", "_draw_waiting", "_redraw_waiting", "_hide_waiting",
                     "_popup_font", "_bubble_zoom", "_zoom_bubble", "_zoom_keys", "_place_waiting",
                     "_avatar_moved"):
            setattr(desk, name, getattr(DesktopAgent, name).__get__(desk))
        avatar = {"at": (600, 400, 120, 120)}
        desk._beside_box = lambda: avatar["at"]
        desk._open_waiting = lambda: None
        desk._show_waiting()
        line = desk._waiting_line
        try:
            self._zoomed_and_moved(desk, line, root, avatar, zoom_popups)
        finally:
            # Tk's objects let go of on its own thread: collected on a worker's, they end the process.
            desk._hide_waiting()
            for name in ("agent_window", "_waiting_box", "root"):
                one = getattr(desk, name, None)
                if hasattr(one, "destroy") and name != "root":
                    one.destroy()
                setattr(desk, name, None)
            root.destroy()
            del desk, line, root
            import gc
            gc.collect()

    def _zoomed_and_moved(self, desk, line, root, avatar, zoom_popups) -> None:
        import sys
        import tkinter as tk
        # The system's own key with the wheel: Command on a Mac (Tk's Mod1), Control elsewhere.
        self.assertIn("<Mod1-MouseWheel>" if sys.platform == "darwin" else "<Control-MouseWheel>",
                      line.bind())

        def size():
            label = desk._waiting_box.winfo_children()[0]
            return abs(int(label.cget("font").split()[-1] if isinstance(label.cget("font"), str)
                           else label.cget("font")[1]))
        before = size()
        zoom_popups(desk, desk._bubble_zoom() + 2)
        self.assertIs(desk._waiting_line, line, "the same line, drawn again")
        self.assertGreater(size(), before, "larger at the larger zoom")

        # "移动西瓜logo, 1 recommendation 这个框很慢才会跟着西瓜一起走": moved with it, at once.
        root.update()
        was = (line.winfo_x(), line.winfo_y())
        desk.agent_window = tk.Toplevel(root)
        avatar["at"] = (300, 250, 120, 120)
        desk._avatar_moved(SimpleNamespace(widget=desk.agent_window))
        root.update()
        self.assertNotEqual((line.winfo_x(), line.winfo_y()), was)
        self.assertEqual(desk._waiting_at[1] - 250, was[1] - 400, "as far beside it as before")
        desk._avatar_moved(SimpleNamespace(widget=object()))   # a child's: nothing

    def test_pressed_the_list_opens(self) -> None:
        self.notes.notify("Waits", "later", level=COLLAPSE)
        desk = self._desk()
        opened = []
        # The side panel down the screen now (2026-11-13), not a list beside the avatar.
        desk.open_notifications = lambda: opened.append("side panel")
        DesktopAgent._open_waiting(desk)
        self.assertEqual(opened, ["side panel"])


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


class TheLineOfWhatWaitsTests(unittest.TestCase):
    """The user, 2026-11-09: a notification closed, "1 recommendation" stayed a second or more -
    drawn again only at the next look, three seconds apart."""

    def test_drawn_again_as_soon_as_the_answer_is_kept(self) -> None:
        from personalized_agent.ui.desktop import DesktopAgent
        desktop = DesktopAgent.__new__(DesktopAgent)
        said = []
        desktop.on_main_thread = lambda work: said.append(work)
        desktop._show_waiting = lambda: None
        kept = []
        desktop._then_waiting(lambda one: kept.append(one))("notif_1")
        self.assertEqual(kept, ["notif_1"])
        self.assertEqual(said, [desktop._show_waiting], "the line, at once")

        def fails(_one):
            raise RuntimeError("the database went")
        with self.assertRaises(RuntimeError):
            desktop._then_waiting(fails)("notif_2")
        self.assertEqual(len(said), 2, "drawn again even so")

    def test_an_answer_read_and_the_line_drawn_before_it_is_kept(self) -> None:
        """The user, 2026-11-10: "Add to collection", and "N recommendation" still stayed two
        seconds - the line waited for the page to be kept in the collection."""
        from types import SimpleNamespace
        from personalized_agent.ui.desktop import DesktopAgent
        desktop = DesktopAgent.__new__(DesktopAgent)
        order = []
        desktop.on_main_thread = lambda work: order.append("line drawn")
        desktop._show_waiting = lambda: None
        desktop.app = SimpleNamespace(
            notifications=SimpleNamespace(mark_read=lambda one: order.append("read " + one)),
            feedback=SimpleNamespace(answer=lambda *a, **k: order.append("answered")))
        desktop._answer_question("ask_1", "add_to_collection", "notif_1")
        self.assertEqual(order, ["read notif_1", "line drawn", "answered"])

    def test_got_it_later_todo_and_bad_draw_the_line_before_what_is_slow(self) -> None:
        """The user, 2026-11-11: "X summary/notification 还是要等几秒才关闭" - "Got it" learned from
        the answer before the line was drawn again."""
        from types import SimpleNamespace
        from personalized_agent.ui.desktop import DesktopAgent
        desktop = DesktopAgent.__new__(DesktopAgent)
        order = []
        desktop.on_main_thread = lambda work: order.append("line drawn")
        desktop._show_waiting = lambda: None
        desktop.app = SimpleNamespace(
            notifications=SimpleNamespace(mark_read=lambda one: order.append("read " + one)))
        desktop._then_waiting(lambda one: order.append("learned from " + one), read=True)("notif_7")
        self.assertEqual(order, ["read notif_7", "line drawn", "learned from notif_7", "line drawn"])

    def test_off_the_line_on_the_press_before_anything_is_kept(self) -> None:
        """The user, 2026-11-12: after Good to know "1 recommendation" still stayed two seconds -
        the line waited for the database. Now it goes on the press, and comes back only if the
        answer was not kept."""
        from types import SimpleNamespace
        from personalized_agent.ui.desktop import DesktopAgent
        desktop = DesktopAgent.__new__(DesktopAgent)
        desktop._words = lambda text: text
        rows = [{"id": "notif_1", "recommended": True}, {"id": "notif_2"}]
        desktop.app = SimpleNamespace(notifications=SimpleNamespace(
            waiting=lambda most: list(rows),
            waiting_by_category=lambda: {"Recommended content": 1, "Reminders": 1}))
        desktop._waiting_counts = {"Recommended content": 1, "Reminders": 1}
        desktop._waiting_line = object()
        drawn, hidden = [], []
        desktop._draw_waiting = lambda lines: drawn.append(lines)
        desktop._hide_waiting = lambda: hidden.append(True)
        desktop._showing = {"text": "A page", "notification_id": "notif_1", "recommended": True}
        desktop._answered_now()
        self.assertEqual(drawn, [[("notifications", "1 notification")]], "on the press")
        self.assertEqual(desktop._answering, {"notif_1": "Recommended content"})
        # The watch looks again before the answer is kept: still not counted.
        desktop._blocked = lambda: False
        with mock.patch("personalized_agent.common.notifications.category_of",
                        side_effect=lambda row: ("Recommended content" if row.get("recommended")
                                                 else "Reminders")):
            DesktopAgent._show_waiting(desktop)
        self.assertEqual(desktop._waiting_counts, {"Reminders": 1})
        # The last one answered: the line goes at once.
        desktop._showing = {"text": "Dentist", "notification_id": "notif_2"}
        desktop._answered_now()
        self.assertEqual(hidden, [True])
        # Kept: counted again as the database says.
        desktop.on_main_thread = lambda work: None
        desktop._then_waiting(lambda one: None)("notif_1")
        self.assertNotIn("notif_1", desktop._answering)


class TheWaitingListHoldsTheBubblesTests(unittest.TestCase):
    """The user, 2026-11-11: "我打开waiting notifications时你还是有那个通知。应该变成我打开waiting
    notifications那些通知先隐藏，关闭之后如果还有再显示"."""

    def _desktop(self, read=()):
        from collections import deque
        from types import SimpleNamespace
        from personalized_agent.ui.desktop import DesktopAgent
        desktop = DesktopAgent.__new__(DesktopAgent)
        desktop._bubble_queue = deque()
        desktop._showing = {"text": "Work summary: the morning", "notification_id": "notif_1"}
        desktop.destroyed = []
        desktop._bubble = SimpleNamespace(destroy=lambda: desktop.destroyed.append(True))
        desktop.drawn = []
        desktop._next_bubble = lambda: desktop._bubble_queue.popleft() if desktop._bubble_queue else None
        desktop.root = SimpleNamespace(after=lambda _ms, work: work())
        desktop._show_waiting = lambda: None
        desktop.lines_gone = []
        desktop._waiting_line = SimpleNamespace(destroy=lambda: desktop.lines_gone.append(True))
        desktop._waiting_at = None
        desktop.app = SimpleNamespace(notifications=SimpleNamespace(
            get=lambda ident: {"read": ident in read},
            waiting_by_category=lambda: {"Recommended content": 1}))
        return desktop

    def test_the_line_of_what_waits_is_not_beside_the_open_list(self) -> None:
        """2026-11-11, the user's screenshot: the list open, "1 recommendation" still beside the
        avatar - the blockers' watch drew it again every few seconds."""
        from personalized_agent.ui.desktop import DesktopAgent
        desktop = self._desktop()
        desktop._blocked = lambda: False
        desktop._waiting_list_open(True)
        self.assertEqual(desktop.lines_gone, [True], "taken away as the list opens")
        self.assertIsNone(desktop._waiting_line)
        DesktopAgent._show_waiting(desktop)              # the watch, a few seconds later
        self.assertIsNone(desktop._waiting_line, "not drawn again while the list is open")

    def test_the_one_shown_goes_back_and_none_is_drawn_while_it_is_open(self) -> None:
        desktop = self._desktop()
        desktop._waiting_list_open(True)
        self.assertEqual(desktop.destroyed, [True])
        self.assertIsNone(desktop._bubble)
        self.assertEqual(desktop._bubble_queue[0]["notification_id"], "notif_1", "back at the head")
        desktop._show_next_bubble()                      # a new one arriving: held
        self.assertEqual(len(desktop._bubble_queue), 1)

    def test_closed_the_unanswered_come_again_and_the_answered_do_not(self) -> None:
        desktop = self._desktop(read={"notif_2"})
        desktop._waiting_list_open(True)
        desktop._bubble_queue.append({"text": "Recommended: a paper", "notification_id": "notif_2"})
        desktop._bubble_queue.append({"text": "Recommended: another", "notification_id": "notif_3"})
        shown = []
        desktop._show_next_bubble = lambda: shown.append(
            desktop._bubble_queue[0]["notification_id"] if not desktop._list_holds else "held")
        desktop._waiting_list_open(False)
        self.assertEqual([one["notification_id"] for one in desktop._bubble_queue], ["notif_1", "notif_3"],
                         "the one answered in the list is not shown again")
        self.assertEqual(shown, ["notif_1"], "they come again once it is closed")



class OneSummaryGoesAtOnceTests(unittest.TestCase):
    """The user, 2026-11-13: "这个1 summary还是要很久才关掉。你已经修这个好多次了，好好修一下". The waiting
    list closed on the press of its last answer, BEFORE the answer was written: the line was drawn
    from the database then - "1 summary" again - and nothing drew it again until the next look."""

    def _desktop(self, rows):
        from personalized_agent.ui.desktop import DesktopAgent
        desktop = DesktopAgent.__new__(DesktopAgent)
        desktop._words = lambda text: text
        desktop._bubble_queue = deque()
        desktop._bubble, desktop._showing = None, None
        desktop.root = SimpleNamespace(after=lambda _ms, work: None)
        desktop._blocked = lambda: False
        desktop._waiting_line = None
        desktop.hidden, desktop.drawn = [], []
        desktop._hide_waiting = lambda: desktop.hidden.append(True)
        desktop._draw_waiting = lambda lines: desktop.drawn.append(lines)
        desktop.app = SimpleNamespace(notifications=SimpleNamespace(
            waiting=lambda most: [dict(one) for one in rows],
            waiting_by_category=lambda: {"Summaries": len(rows)} if rows else {},
            get=lambda ident: {"read": False}))
        return desktop

    def test_answered_in_the_list_it_is_not_drawn_again_as_the_list_closes(self) -> None:
        from personalized_agent.ui.desktop import DesktopAgent
        rows = [{"id": "notif_1", "metadata": {"summary": True}}]
        desktop = self._desktop(rows)
        desktop._quick_panels = {"waiting": SimpleNamespace(gone={"notif_1"}, is_open=False)}
        DesktopAgent._waiting_list_open(desktop, False)      # closed on the press, not yet kept
        self.assertEqual(desktop.drawn, [], "no 1 summary drawn again")
        self.assertIn("notif_1", desktop._answering)

    def test_kept_the_line_is_drawn_again_at_once(self) -> None:
        from personalized_agent.ui.desktop import DesktopAgent
        desktop = self._desktop([])
        drawn_again = []
        desktop.on_main_thread = lambda work: drawn_again.append(work)
        desktop.app.local_post = lambda path, body: None
        finished = threading.Event()
        DesktopAgent._panel_act(desktop, {"key": "got_it", "path": "/api/notifications/read",
                                          "body": {"id": "notif_1"}}, finished.set)
        self.assertTrue(finished.wait(5))
        self.assertEqual(len(drawn_again), 1, "drawn again the moment it is kept")
        self.assertNotIn("notif_1", desktop._answering)

    def test_read_anywhere_is_said_and_the_report_opened_reads_its_summaries(self) -> None:
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        app = agent.app
        said = []
        app.bus.subscribe("notification.read", lambda _topic, payload: said.append(payload))
        report = app.notifications.notify("For your approval: 2", "- a", kind="approval",
                                          metadata={"approvals": True, "level": COLLAPSE})
        done = app.notifications.notify("What your agent did: 1", "- b", kind="info",
                                        metadata={"summary": True, "agent_did": True, "level": COLLAPSE})
        other = app.notifications.notify("The day ahead", "- c", kind="info",
                                         metadata={"summary": True, "level": COLLAPSE})
        self.assertEqual(app.notifications.read_report_summaries(), 2)
        self.assertTrue(app.notifications.get(report.id)["read"])
        self.assertTrue(app.notifications.get(done.id)["read"])
        self.assertFalse(app.notifications.get(other.id)["read"], "not the report's: left")
        self.assertEqual(sorted(said[-1]["ids"]), sorted([report.id, done.id]))
        app.notifications.mark_read(other.id)
        self.assertEqual(said[-1]["ids"], [other.id], "any read, said at once")



class TheSidePanelTests(unittest.TestCase):
    """The user, 2026-11-13: "你现在这个notifications/recommendation的channel太不现代了，能不能直接占满整个侧边框
    的那种（类似reports每个事项点击之后弹出来的内容)"."""

    def test_its_rows_with_their_answers_and_drawn_down_the_side(self) -> None:
        import shutil
        import subprocess
        from support import ROOT
        from personalized_agent.api.routes import build_routes
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        app = agent.app
        app.notifications.notify("High water at noon", "The tide tables say 12:04.\nhttps://example.org/tides",
                                 kind="proactive", level=COLLAPSE)
        said = build_routes(app)[("GET", "/api/notifications/waiting_list")](
            SimpleNamespace(get=lambda *_a: None))
        [row] = [one for part in said["sections"] for one in part["rows"]]
        self.assertTrue(row["at"], "when it came")
        self.assertIn("got_it", [one["key"] for one in row["actions"]])
        if not shutil.which("node"):
            self.skipTest("no node here")
        from test_chat_debugger_ui import ESCAPE
        from test_overview_todo_scroll import _function
        source = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text(encoding="utf-8")
        start = source.index("const notesState = ")
        script = ("const window=globalThis;" + ESCAPE + """
const page = { innerHTML: "", querySelector: () => null };
const $ = (sel) => sel === "#notes-page" ? page : null;
const localStamp = (x) => x;
const document = { activeElement: null };
const NOTES_SOLO = true;
""" + source[start:source.index("\n", start)] + _function(source, "notesAt") + _function(source, "linkify")
                  + _function(source, "notesCard") + _function(source, "renderNotes")
                  # Folded, as each is when more than one waits (one alone is open).
                  + "notesState.sections = " + json.dumps(said["sections"]).replace(
                      '"folds": false', '"folds": true') + ";"
                  + "renderNotes(true); const closed = page.innerHTML;"
                  + "notesState.open.add(" + json.dumps(row["id"]) + "); renderNotes(true);"
                  + "process.stdout.write(JSON.stringify({closed, open: page.innerHTML}));")
        ran = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                             encoding="utf-8", timeout=60)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        drawn = json.loads(ran.stdout)
        self.assertIn("High water at noon", drawn["closed"])
        self.assertNotIn("data-notes-act", drawn["closed"], "its answers once it is opened")
        self.assertIn(f'data-notes-act="{row["id"]}"', drawn["open"])
        self.assertIn("data-notes-reply", drawn["open"], "a reply in words")
        self.assertIn('href="https://example.org/tides"', drawn["open"], "its link, to open")

    def test_the_desktop_opens_it_once_down_the_side(self) -> None:
        from pathlib import Path
        from personalized_agent.platform_layer import app_window as aw
        from personalized_agent.ui.desktop import DesktopAgent
        desk = SimpleNamespace(app=SimpleNamespace(sign_in_url=lambda path: "http://here" + path,
                                                   paths=SimpleNamespace(data_dir=Path("data")),
                                                   config=None),
                               NOTES_TITLE=DesktopAgent.NOTES_TITLE, NOTES_PATH=DesktopAgent.NOTES_PATH,
                               _off_this_thread=lambda what, name="": what(),
                               _notes_on_a_mac=lambda: False)
        desk._notes_titles = lambda: DesktopAgent._notes_titles(desk)
        with mock.patch.object(aw, "slide_in_titled", return_value=False), \
                mock.patch.object(aw, "open_side_window", return_value=True) as opened:
            DesktopAgent.open_notifications(desk)
        # PLACED BY THE DESKTOP before it is seen, the page not moving it (2026-11-13: "要先在左边跳一下").
        self.assertEqual(opened.call_args[0][0], "http://here/?solo=notifications&placed=1")
        self.assertEqual(opened.call_args[0][2], aw.NOTES_SIZE)
        # PUT AWAY, SLID BACK IN (2026-11-13: "现在打开好像太慢了"): no browser started again.
        with mock.patch.object(aw, "slide_in_titled", return_value=True) as slid, \
                mock.patch.object(aw, "open_side_window") as opened:
            DesktopAgent.open_notifications(desk)
        opened.assert_not_called()
        self.assertEqual(slid.call_args[0][0], {DesktopAgent.NOTES_TITLE})

    def test_the_line_held_while_the_panel_is_shown_drawn_again_once_it_is_put_away(self) -> None:
        """2026-11-13: "打开1 recommendation 之后, 1 recommendation的通知还存在"."""
        from personalized_agent.platform_layer import app_window as aw
        from personalized_agent.ui.desktop import DesktopAgent
        held, again = [], []
        desk = SimpleNamespace(app=SimpleNamespace(config=None), NOTES_TITLE=DesktopAgent.NOTES_TITLE,
                               NOTES_WATCH_MS=DesktopAgent.NOTES_WATCH_MS,
                               NOTES_COMING_SECONDS=DesktopAgent.NOTES_COMING_SECONDS,
                               _notes_since=0.0, _notes_seen=False, _notes_watching=True,
                               root=SimpleNamespace(after=lambda ms, fn: again.append(fn)),
                               _waiting_list_open=held.append, _notes_on_a_mac=lambda: False)
        desk._notes_titles = lambda: DesktopAgent._notes_titles(desk)
        desk._watch_notes = lambda: DesktopAgent._watch_notes(desk)
        desk._notes_open_now = lambda: DesktopAgent._notes_open_now(desk)
        with mock.patch.object(aw, "shown_titled", return_value=True):
            DesktopAgent._watch_notes(desk)
        self.assertEqual((held, len(again)), ([], 1), "shown: held, looked at again")
        with mock.patch.object(aw, "shown_titled", return_value=False):
            DesktopAgent._watch_notes(desk)
        self.assertEqual(held, [False], "put away: drawn again")
        self.assertFalse(desk._notes_watching)

    def test_the_line_is_not_drawn_beside_a_panel_open_from_before(self) -> None:
        """2026-11-13: the panel open, "1 recommendation" still beside the avatar - it was open from before
        the agent started again."""
        from personalized_agent.ui.desktop import DesktopAgent
        held, after = [], []
        desk = SimpleNamespace(
            app=SimpleNamespace(notifications=SimpleNamespace(
                waiting_by_category=lambda: {"Recommended content": 1})),
            _blocked=lambda: False, _bubble_queue=deque(), _quick_panels={}, _list_holds=False,
            _notes_open_now=lambda: True, _waiting_list_open=held.append, NOTES_WATCH_MS=1,
            root=SimpleNamespace(after=lambda ms, fn: after.append(fn)), _watch_notes=lambda: None,
            _waiting_line=None)
        DesktopAgent._show_waiting(desk)
        self.assertEqual(held, [True], "held while it is open")
        self.assertEqual(len(after), 1, "and watched, to draw it again once it is put away")
        self.assertIsNone(desk._waiting_line, "no line drawn")

    def test_on_a_mac_the_same_a_panel_of_its_own_down_the_side(self) -> None:
        """2026-11-13: "Mac上的面板为什么不改呢" - Apple's WebKit, on the main thread, slid in; put away
        on the main thread too."""
        from personalized_agent.ui import mac_panel
        from personalized_agent.ui.desktop import DesktopAgent
        calls, held = [], []
        desk = SimpleNamespace(app=SimpleNamespace(sign_in_url=lambda path: "http://here" + path, config=None),
                               NOTES_TITLE=DesktopAgent.NOTES_TITLE, NOTES_WATCH_MS=1,
                               NOTES_PATH=DesktopAgent.NOTES_PATH,
                               queue=SimpleNamespace(put=lambda one: calls.append(one)),
                               _notes_on_a_mac=lambda: True, _waiting_list_open=held.append,
                               root=SimpleNamespace(after=lambda ms, fn: None))
        desk._open_notes_on_a_mac = lambda: DesktopAgent._open_notes_on_a_mac(desk)
        desk._watch_notes = lambda: None
        desk._notes_titles = lambda: DesktopAgent._notes_titles(desk)
        with mock.patch.dict("os.environ", {"PA_NO_DIALOGS": ""}),                 mock.patch.object(mac_panel, "side_panel", return_value=True) as slid:
            DesktopAgent.open_notifications(desk)
            self.assertEqual(held, [True], "the line held while it is shown")
            [(kind, made)] = calls
            self.assertEqual(kind, "call", "made on the main thread")
            made()
        self.assertEqual(slid.call_args[0], ("http://here/?solo=notifications&placed=1", 460,
                                             DesktopAgent.NOTES_TITLE))
        calls.clear()
        with mock.patch.object(mac_panel, "has_side_panel", return_value=True),                 mock.patch.object(mac_panel, "hide_side_panel") as hid:
            self.assertTrue(DesktopAgent.hide_notes_panel(desk))
            [(kind, put_away)] = calls
            put_away()
        hid.assert_called_once_with(DesktopAgent.NOTES_TITLE)
        with mock.patch.object(mac_panel, "has_side_panel", return_value=False):
            self.assertFalse(DesktopAgent.hide_notes_panel(desk), "none made: the page closes itself")

    def test_the_last_one_answered_the_panel_is_put_away(self) -> None:
        """2026-11-13: "waiting notification没有内容了之后不会自动关闭了"; its × puts it away, kept."""
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("no node here")
        from support import ROOT
        from test_overview_todo_scroll import _function
        source = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text(encoding="utf-8")
        script = ("const posted = []; let closed = 0; let rows = [];"
                  "const window = {close: () => { closed += 1; }};"
                  "const api = {get: async () => ({sections: [{name: 'x', rows}]}),"
                  "  post: async (path) => { posted.push(path); return {hidden: true}; }};"
                  "const notesState = {sections: [], open: new Set(), leaving: new Set()};"
                  "function renderNotes() {}"
                  + "".join("async " + _function(source, name) for name in ("loadNotes", "closeNotes"))
                  + "(async () => {"
                  "  await loadNotes(); await loadNotes(); const opened = posted.length;"   # opened empty
                  "  rows = [{id: 'n2'}]; await loadNotes(); const emptyUnanswered = posted.length;"
                  "  rows = [];"                     # answered elsewhere - beside the avatar, the report
                  "  await loadNotes(); await new Promise((r) => setTimeout(r, 10));"
                  "  process.stdout.write(JSON.stringify({opened, emptyUnanswered, posted, closed}));"
                  "})();")
        ran = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                             encoding="utf-8", timeout=60)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        said = json.loads(ran.stdout)
        self.assertEqual((said["opened"], said["emptyUnanswered"]), (0, 0), "opened empty, or with some: it stays")
        # 2026-11-13: "这个好像消息结束了没有自动关闭窗口" - its last one gone, however it went.
        self.assertEqual(said["posted"], ["/api/notifications/panel_hide"], "the last gone: put away")
        self.assertEqual(said["closed"], 0, "put away by the desktop, not closed")


if __name__ == "__main__":
    unittest.main()
