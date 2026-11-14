"""THE DAY'S PLAN, PHASE 2 (the user, 2026-11-09: past the preview, "继续"): the user's word on a
thing of the plan - done, put off, the agent's or theirs, more time, let go - from the calendar and
the chat; and the plan's own reminders - at each block, for the approvals, the quiet of deep work,
a break.

Everyone and everything here is made up."""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.heartbeat.agenda import check as checking
from personalized_agent.heartbeat.agenda import items as gathered
from personalized_agent.heartbeat.agenda import triage as triaging
from personalized_agent.heartbeat.agenda.act import Feedback, Reminders
from personalized_agent.heartbeat.agenda.plan import Agenda


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.db = self.app.db

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _sorted(self, titles, who="human", minutes=30):
        for title in titles:
            self.app.todos.add(title)
        gathered.collect(self.db)
        for one in gathered.open_items(self.db):
            self.db.execute("UPDATE agenda_items SET triage = ?, triaged_for = ? WHERE id = ?",
                            (json.dumps({"who": who, "minutes": minutes, "kind": "a garden job",
                                         "rule": triaging.APPROVE_RULE}),
                             triaging.judged_for(one), one["id"]))
        return {one["title"]: one for one in gathered.open_items(self.db)}


class TheUsersWordTests(_Base):

    def _feedback(self):
        self.planned, self.learned, self.ticked, self.removed = [], [], [], []
        agenda = Agenda(self.db, lambda: None, self.app.config)
        agenda.plan_again = self.planned.append
        return agenda, Feedback(agenda, lessons=lambda action, said: self.learned.append((action, said)),
                                todo_done=self.ticked.append, todo_removed=self.removed.append)

    def test_put_off_kept_on_the_thing_and_planned_around(self) -> None:
        rows = self._sorted(["Prune the quince hedge", "Order bean poles"])
        agenda, feedback = self._feedback()
        hedge = rows["Prune the quince hedge"]
        now = datetime(2030, 3, 4, 10, 0)
        said = feedback.apply(hedge["id"], "tomorrow", now=now)
        self.assertEqual(said["detail"], "2030-03-05")
        self.assertEqual(len(self.planned), 1, "the day planned again around it")
        self.assertEqual(self.learned[0][0], "agenda_tomorrow")
        things = {one["title"]: one for one in agenda.things()}
        self.assertEqual(things["Prune the quince hedge"]["not_before"], "2030-03-05")
        feedback.apply(rows["Order bean poles"]["id"], "later_today", now=now)
        poles = {one["title"]: one for one in agenda.things()}["Order bean poles"]
        self.assertEqual((poles["not_before"], poles["not_before_time"]), ("2030-03-04", "11:00"))
        self.assertEqual(len(feedback.recent()), 2, "kept: how the user changes a plan")

    def test_done_and_let_go_of_as_on_their_list_and_not_opened_again(self) -> None:
        rows = self._sorted(["Prune the quince hedge", "Order bean poles"])
        agenda, feedback = self._feedback()
        feedback.apply(rows["Prune the quince hedge"]["id"], "done")
        feedback.apply(rows["Order bean poles"]["id"], "drop")
        self.assertEqual(self.ticked, [rows["Prune the quince hedge"]["ref_id"]])
        self.assertEqual(self.removed, [rows["Order bean poles"]["ref_id"]])
        self.assertEqual(gathered.open_items(self.db), [])
        gathered.collect(self.db)                 # the to-dos still there (the fakes kept them)
        self.assertEqual(gathered.open_items(self.db), [], "said done by the user: not opened again")

    def test_the_agents_or_theirs_and_more_time_outlast_sorting_again(self) -> None:
        rows = self._sorted(["Book the orchard hall"], who="approve", minutes=10)
        agenda, feedback = self._feedback()
        ident = rows["Book the orchard hall"]["id"]
        feedback.apply(ident, "to_agent")
        feedback.apply(ident, "more_time")
        row = gathered.open_items(self.db)[0]
        self.assertEqual((row["triage"]["who"], row["triage"]["minutes"]), ("delegable", 40))
        # Sorted again: the user's word holds.
        self.db.execute("UPDATE agenda_items SET triaged_for = '' WHERE id = ?", (ident,))

        class Models:
            def invoke(self, request, role="primary"):
                said = json.loads(request.messages[0]["content"])
                text = {"things": [{"id": one["id"], "who": "approve", "minutes": 5}
                                   for one in said.get("things", [])]}
                return SimpleNamespace(text=json.dumps(text), tool_calls=[])
        triaging.run(self.db, Models(), gathered.open_items(self.db))
        row = gathered.open_items(self.db)[0]
        self.assertEqual((row["triage"]["who"], row["triage"]["minutes"]), ("delegable", 40))

    def test_put_off_to_later_today_is_not_suggested_before_then(self) -> None:
        """The user's word "later today": the thing is not suggested in a free stretch before it -
        the user's day holds no time for it, only suggestions (the user, 2026-11-10)."""
        from personalized_agent.heartbeat.agenda.plan import day_blocks
        day = date(2030, 3, 4)
        things = [{"id": "a", "title": "Order bean poles", "triage": {"who": "human", "minutes": 30},
                   "not_before": "2030-03-04", "not_before_time": "11:00"},
                  {"id": "m", "title": "Seed swap", "triage": {"who": "human", "minutes": 30},
                   "fixed_local": "2030-03-04 10:30"}]
        blocks = day_blocks({"day": day, "plan_from": "09:00", "ends": "18:00"}, things,
                            [{"item": "a", "why": "the poles run out"}])
        self.assertEqual([(one["start"], one["kind"], one["items"]) for one in blocks],
                         [("09:00", "free", []), ("10:30", "meeting", ["m"]), ("11:00", "free", ["a"])])
        self.assertTrue(checking.check({"blocks": blocks, "might_do": [{"item": "a"}]}, things, day,
                                       "09:00", "18:00", 90, 360)["ok"])

    def test_from_the_chat_and_the_page(self) -> None:
        from personalized_agent.api.routes import build_routes
        rows = self._sorted(["Prune the quince hedge"])
        self.app.agenda.plan_again = lambda why: None
        said = self.app.agent.tools.call("agenda_change", {"id": rows["Prune the quince hedge"]["id"],
                                                          "action": "on_day", "day": "2030-05-01"})
        self.assertEqual(said["detail"], "2030-05-01")
        said = build_routes(self.app)[("POST", "/api/agenda/feedback")](SimpleNamespace(
            require=lambda name: {"id": rows["Prune the quince hedge"]["id"], "action": "mine"}[name],
            get=lambda name, default=None: default))
        self.assertEqual(said["detail"], "human")
        self.assertIn("blocks", self.app.agent.tools.call("agenda_list", {}))


class PlannedAgainTests(_Base):

    def test_a_blocks_time_gone_by_with_its_things_open(self) -> None:
        """Phase 2: they ran over, or it slipped - the day planned again from where it is."""
        rows = self._sorted(["Draft the hedge budget"])
        ident = rows["Draft the hedge budget"]["id"]
        now = datetime.now().astimezone().replace(tzinfo=None)
        if now.hour < 2:
            self.skipTest("too early in the day for a block gone by")
        ended = (now - timedelta(minutes=30)).strftime("%H:%M")
        began = (now - timedelta(minutes=90)).strftime("%H:%M")
        made = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        plan = {"blocks": [{"start": began, "end": ended, "kind": "deep", "items": [ident], "title": "Hedge"}],
                "_frame": {"triage_version": triaging.VERSION}}
        self.db.execute("INSERT INTO agenda_plans (id, day, made_at, plan) VALUES ('p', ?, ?, ?)",
                        (date.today().isoformat(), made, json.dumps(plan)))
        agenda = Agenda(self.db, lambda: None, self.app.config)
        self.assertIn("went by with its things still open", agenda.stale(agenda.latest(date.today())))
        self.db.execute("UPDATE agenda_plans SET made_at = ?", (datetime.now(timezone.utc).isoformat(),))
        self.assertEqual(agenda.stale(agenda.latest(date.today())), "", "planned again since it ended")


class TheRemindersTests(_Base):
    """The user's choice of four, 2026-11-09."""

    def _plan(self, blocks, held="end of block"):
        day = date.today()
        self.db.execute("INSERT INTO agenda_plans (id, day, made_at, plan) VALUES (?, ?, ?, ?)",
                        (f"plan_{len(blocks)}", day.isoformat(), datetime.now(timezone.utc).isoformat(),
                         json.dumps({"blocks": blocks, "held_reminders": held})))
        self.sent = []
        agenda = Agenda(self.db, lambda: None, self.app.config)
        return agenda, Reminders(agenda, notify=lambda title, message, meta: self.sent.append((title, message)))

    def _at(self, hhmm):
        hours, minutes = map(int, hhmm.split(":"))
        return datetime.combine(date.today(), datetime.min.time()) + timedelta(hours=hours, minutes=minutes)

    def test_at_each_block_once_with_its_work_note_and_a_meeting_early(self) -> None:
        rows = self._sorted(["Draft the hedge budget"])
        ident = rows["Draft the hedge budget"]["id"]
        agenda, reminders = self._plan([
            {"start": "09:00", "end": "10:30", "kind": "deep", "title": "Hedge budget", "items": [ident],
             "note": {"left_off": "The cost table half filled.", "folder": "D:/garden/hedge"}},
            {"start": "11:00", "end": "12:00", "kind": "meeting", "title": "Seed swap", "place": "The hall"}])
        reminders.tick(self._at("09:01"))
        reminders.tick(self._at("09:02"))
        self.assertEqual(len(self.sent), 1, "once")
        title, message = self.sent[0]
        self.assertEqual(title, "Now, until 10:30: Hedge budget")
        self.assertIn("Last time: The cost table half filled.", message)
        self.assertIn("Folder: D:/garden/hedge", message)
        reminders.tick(self._at("10:50"))
        self.assertEqual(self.sent[-1][0], "Now, until 12:00: Seed swap", "a meeting ten minutes early")
        self.assertIn("Where: The hall", self.sent[-1][1])

    def test_the_approvals_together_when_their_block_begins(self) -> None:
        rows = self._sorted(["Reply to the seed club", "Book the orchard hall"], who="approve", minutes=5)
        agenda, reminders = self._plan([{"start": "13:00", "end": "13:20", "kind": "batch", "title": "Approvals",
                                         "items": [one["id"] for one in rows.values()]}])
        reminders.tick(self._at("13:00"))
        title, message = self.sent[0]
        self.assertEqual(title, "To approve, until 13:20: 2")
        self.assertIn("- Reply to the seed club", message)

    def test_deep_work_holds_the_rest_until_its_end_or_the_next_batch(self) -> None:
        blocks = [{"start": "09:00", "end": "10:30", "kind": "deep", "title": "Hedge"},
                  {"start": "11:00", "end": "11:30", "kind": "batch", "title": "Small things"}]
        agenda, reminders = self._plan(blocks)
        self.assertEqual(reminders.quiet_now(self._at("09:30")), "Deep work until 10:30")
        self.assertEqual(reminders.quiet_now(self._at("10:45")), "")
        agenda, reminders = self._plan(blocks + [{"start": "14:00", "end": "15:00", "kind": "break"}],
                                       held="batch")
        self.assertEqual(reminders.quiet_now(self._at("09:30")), "Deep work until 11:00")
        engine = self.app.monitor
        self.assertIs(engine.extra_quiet.__self__.__class__, Reminders, "the Monitor asks the plan")

    def test_no_break_reminder_of_the_plans_own(self) -> None:
        """2026-11-13: "把这个删掉" - a break reminder is the user's own to add, in the Monitor (2026-11-07)."""
        kind = "workday" if date.today().weekday() < 5 else "weekend"
        agenda, reminders = self._plan([{"start": "16:00", "end": "17:00", "kind": "meeting", "title": "Board"}])
        agenda._routines[(kind, date.today())] = {"days": 9, "stretch_minutes": 75, "break_minutes": 30}
        agenda.today = lambda: {"on_since_last_break_minutes": 140, "doing_now": "coding"}
        self.assertNotIn("Time for a break", reminders.tick(self._at("15:00")))
        self.assertFalse(any("break" in str(one[0]).lower() for one in self.sent))


if __name__ == "__main__":
    unittest.main()
