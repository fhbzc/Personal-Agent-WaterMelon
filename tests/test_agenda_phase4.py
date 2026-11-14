"""THE DAY'S PLAN, PHASE 4 (the user, 2026-11-09: the morning and evening reports go into the plan,
"可以的"): the day ahead and the evening report written from the day's plan. How long the user's
things took is no longer learned (the user, 2026-11-10: "现在不需要给我做的东西估计时间了").

Everyone and everything here is made up."""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta, timezone

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.heartbeat.agenda import items as gathered
from personalized_agent.heartbeat.agenda import triage as triaging


def _at(hhmm: str) -> datetime:
    return datetime.combine(date.today(), datetime.min.time()) + timedelta(
        hours=int(hhmm[:2]), minutes=int(hhmm[3:]))


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.db = self.app.db
        self.agenda = self.app.agenda

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _sorted(self, titles, kind="pruning", minutes=20):
        for title in titles:
            self.app.todos.add(title)
        gathered.collect(self.db)
        for one in gathered.open_items(self.db):
            self.db.execute("UPDATE agenda_items SET triage = ?, triaged_for = ? WHERE id = ?",
                            (json.dumps({"who": "human", "minutes": minutes, "kind": kind,
                                         "rule": triaging.APPROVE_RULE}),
                             triaging.judged_for(one), one["id"]))
        return {one["title"]: one["id"] for one in gathered.open_items(self.db)}

    def _plan(self, plan):
        self.db.execute("INSERT INTO agenda_plans (id, day, made_at, plan) VALUES ('p1', ?, ?, ?)",
                        (date.today().isoformat(), datetime.now(timezone.utc).isoformat(), json.dumps(plan)))

    def _close(self, ident, hhmm, how="done_at"):
        """Ticked (or deleted) on the to-do list at `hhmm` - found by the agenda only later, as it is."""
        ref = self.db.query_one("SELECT ref_id FROM agenda_items WHERE id = ?", (ident,))["ref_id"]
        self.db.execute(f"UPDATE todo_items SET {how} = ? WHERE id = ?",
                        (_at(hhmm).astimezone().astimezone(timezone.utc).isoformat(), ref))
        gathered.collect(self.db)


class NothingTimedTests(_Base):
    """The user, 2026-11-10: "现在不需要给我做的东西估计时间了，你就负责把你要做的事情（和协助我做的事情做好）
    就行" - how long the user's things took is no longer learned, nor told to the sorting or the plan."""

    def test_done_in_a_block_nothing_is_learned_of_how_long_it_took(self) -> None:
        ids = self._sorted(["Prune the pears", "Prune the plums", "Prune the quinces"])
        self._plan({"blocks": [{"start": "09:00", "end": "11:00", "kind": "deep", "title": "Pruning",
                                "items": list(ids.values())}]})
        self._close(ids["Prune the plums"], "10:15")
        self._close(ids["Prune the pears"], "09:30")
        self._close(ids["Prune the quinces"], "10:50", how="deleted_at")
        self.assertEqual(self.agenda.learn_times(), 0)
        self.assertEqual(self.db.query_one("SELECT COUNT(*) AS n FROM agenda_estimates")["n"], 0)
        self.assertEqual(self.agenda.times_taken(), {})
        closed = self.db.query_one("SELECT closed_at FROM agenda_items WHERE id = ?", (ids["Prune the pears"],))
        self.assertEqual(closed["closed_at"], _at("09:30").astimezone().astimezone(timezone.utc).isoformat(),
                         "when it was ticked, not when the agenda found it (a side agent's note)")

    def test_the_sorting_and_the_plan_are_not_told_how_long_things_took(self) -> None:
        from types import SimpleNamespace
        self.db.execute("INSERT INTO agenda_estimates (item_id, at, kind, planned, actual) "
                        "VALUES ('agenda_x', ?, 'pruning', 20, 50)", (datetime.now(timezone.utc).isoformat(),))
        self._sorted(["Prune the pears"])
        asked = []

        class Models:
            def invoke(self, request, role="primary"):
                asked.append(request)
                said = json.loads(request.messages[0]["content"])
                return SimpleNamespace(text=json.dumps({"things": [
                    {"id": one["id"], "words": []} for one in said.get("things", [])]}), tool_calls=[])
        triaging.run_once(self.db, Models(), gathered.open_items(self.db), learned="")
        sorting = next(one for one in asked if one.task == "agenda_triage")
        self.assertNotIn("time_taken_by_kind", json.loads(sorting.messages[0]["content"]))
        self.assertIn("The user's own things are not timed", triaging.SYSTEM)
        self.assertIn("give 30 for them", triaging.SYSTEM)
        context = self.agenda._context(self.agenda.day_of(), self.agenda.things())
        self.assertNotIn("time_taken_by_kind", context)


class TheReportsTests(_Base):

    def _asked(self):
        asked = []

        def ask(system, prompt, task, max_tokens=900):
            asked.append(prompt)
            return "Your day."
        self.app.monitor_decider._ask = ask
        self.app.monitor.say = lambda *args, **kwargs: {"said": True}   # what is asked, not how sent
        return asked

    def test_the_day_ahead_goes_by_the_plan(self) -> None:
        ids = self._sorted(["Prune the pears"])
        self._plan({"summary": "A long pruning morning.", "blocks": [
            {"start": "09:00", "end": "11:00", "kind": "deep", "title": "Pruning",
             "items": list(ids.values())}],
            "at_risk": [{"item": ids["Prune the pears"], "why": "frost on Friday"}]})
        text = self.agenda.morning_text()
        for said in ("A long pruning morning.", "09:00-11:00  Pruning", "At risk: Prune the pears"):
            self.assertIn(said, text)
        asked = self._asked()
        self.app.monitor_decider.morning(self.app.monitor, {}, {"signals": {}}, "f1")
        self.assertIn("THE DAY'S PLAN:\n" + text, asked[0])

    def test_the_day_ahead_says_when_they_are_free_and_what_they_might_do(self) -> None:
        """The user, 2026-11-10: "你可以在每个user slot告诉user你接下来XX时间内都是没安排，并且这些是你可能
        想要做的task。但是不要限制user做什么"."""
        ids = self._sorted(["Prune the pears", "Graft the apple", "Reply to the seed club"])
        pears, apple, reply = ids["Prune the pears"], ids["Graft the apple"], ids["Reply to the seed club"]
        self.db.execute("UPDATE agenda_items SET triage = json_set(triage, '$.who', 'approve') WHERE id = ?",
                        (reply,))
        self._plan({"summary": "The agent writes to the seed club; the morning is yours.", "blocks": [
            {"start": "09:00", "end": "11:00", "kind": "free", "title": "Nothing planned", "suggested": True,
             "items": [pears, apple], "might_do": [{"item": pears, "why": "frost on Friday"},
                                                   {"item": apple, "why": ""}]},
            {"start": "11:00", "end": "12:00", "kind": "meeting", "title": "Seed swap", "fixed": True},
            {"start": "12:00", "end": "13:00", "kind": "free", "title": "Nothing planned", "suggested": True,
             "items": [], "might_do": []}],
            "agent_does": [{"item": reply, "what": "Draft the reply", "start": "09:00", "end": "09:20"}],
            "might_do": [{"item": pears, "why": "frost on Friday"}, {"item": apple, "why": ""}],
            "at_risk": [{"item": apple, "why": "the scions dry out"}]})
        text = self.agenda.morning_text()
        for said in ("The agent writes to the seed club; the morning is yours.",
                     "09:00-11:00  free - they might do: Prune the pears; Graft the apple",
                     "11:00-12:00  Seed swap", "12:00-13:00  free\n",
                     "At risk: Graft the apple", "The agent works on 1 things for you today."):
            self.assertIn(said, text)
        self.assertNotIn("To approve today", text, "approved when they choose: no time of theirs for it")
        self.assertNotIn("Nothing planned", text)

    def test_no_plan_no_part(self) -> None:
        self.assertEqual(self.agenda.morning_text(), "")
        self.app.monitor_decider.agenda = None
        self.assertEqual(self.app.monitor_decider._agenda_part("morning_text"), "")

    def test_the_evening_says_how_the_day_went_by_its_plan(self) -> None:
        ids = self._sorted(["Prune the pears", "Graft the apple"])
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        self._plan({"blocks": [{"start": "09:00", "end": "11:00", "kind": "deep", "title": "Pruning",
                                "items": [ids["Prune the pears"]]}],
                    "later": {tomorrow: [ids["Graft the apple"]]}})
        self._close(ids["Prune the pears"], "10:00")
        asked = self._asked()
        self.app.monitor_decider.write_evening(self.app.monitor, {"signals": {}}, "f2")
        prompt = asked[0]
        self.assertIn("THE DAY AS ITS PLAN WENT:", prompt)
        self.assertIn("09:00-11:00 planned deep: Pruning", prompt)
        self.assertIn("done: Prune the pears", prompt)
        self.assertIn("TOMORROW, AS IT STANDS NOW (a first draft):\n- Graft the apple", prompt)
        self.assertIsNone(self.db.query_one("SELECT actual FROM agenda_estimates"),
                          "nothing of the user's timed (the user, 2026-11-10)")


class TheApprovalsPanelTests(_Base):
    """The user, 2026-11-09: the reminder to approve, pressed, opens the approvals panel."""

    def test_the_reminder_says_which_are_to_be_approved(self) -> None:
        from personalized_agent.heartbeat.agenda.act import Reminders
        ids = self._sorted(["Reply to the seed club", "Prune the pears"])
        reply = ids["Reply to the seed club"]
        # To approve (the user, 2026-11-10): a delegable one is told the moment it is done instead.
        self.db.execute("UPDATE agenda_items SET triage = json_set(triage, '$.who', 'approve') WHERE id = ?",
                        (reply,))
        self._plan({"blocks": [{"start": "10:00", "end": "10:30", "kind": "batch", "title": "Replies",
                                "items": [reply]},
                               {"start": "11:00", "end": "12:00", "kind": "deep", "title": "Pruning",
                                "items": [ids["Prune the pears"]]}]})
        told = []
        reminders = Reminders(self.agenda, notify=lambda title, message, meta: told.append(meta))
        reminders.tick(_at("10:01"))
        reminders.tick(_at("11:01"))
        self.assertEqual(told[0]["agenda"]["approve"], [reply])
        self.assertEqual(told[0]["agenda"]["until"], "10:30")
        self.assertNotIn("approve", told[1]["agenda"], "deep work asks for no approval")

    def test_each_row_says_where_it_stands(self) -> None:
        ids = self._sorted(["Reply to the seed club", "Answer Wren Halloway"])
        reply, answer = ids["Reply to the seed club"], ids["Answer Wren Halloway"]
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute("INSERT INTO work_drafts (id, created_at, updated_at, status) "
                        "VALUES ('draft_r', ?, ?, 'drafting')", (now, now))
        self.db.execute("INSERT INTO kv (key, value) VALUES (?, 'draft_r')", ("agenda.work." + reply,))
        told = self.app.agenda_approvals([reply, answer, "agenda_gone"], "10:30")
        self.assertEqual(told["scope"], "approval")
        self.assertEqual(told["work"], "To approve, until 10:30")
        self.assertEqual([(row["id"], row["state"]) for row in told["approval"]["rows"]],
                         [(reply, "working"), (answer, "not_started"), ("agenda_gone", "closed")])
        self.assertIn("still working", told["how"], "nothing made yet: what the agent is doing")
        published = []
        self.app.bus.subscribe("work.handed_opened", lambda topic, payload: published.append(payload))
        self.app.open_agenda_approvals([reply], "10:30")
        self.assertEqual(published[-1]["approval"]["rows"][0]["id"], reply)

    def test_the_bubble_offers_it_only_on_such_a_reminder(self) -> None:
        from types import SimpleNamespace
        from personalized_agent.ui.desktop import DesktopAgent
        said = {"n1": {"metadata": {"agenda": {"approve": ["agenda_a"], "until": "10:30"}}},
                "n2": {"metadata": {"agenda": {"block": "11:00"}}}}
        fake = SimpleNamespace(app=SimpleNamespace(notifications=SimpleNamespace(get=said.get)))
        self.assertEqual(DesktopAgent._bubble_approvals(fake, "n1"), (["agenda_a"], "10:30"))
        self.assertIsNone(DesktopAgent._bubble_approvals(fake, "n2"))
        self.assertIsNone(DesktopAgent._bubble_approvals(fake, ""))


class LeftHalfDoneTests(_Base):
    """The user, 2026-11-09: "你那些planning之类的，点开之后就是自动运行的（比如没做完的planning, 点开之后是
    自动resume)"."""

    def test_the_days_ahead_left_unplanned_are_planned_when_next_asked(self) -> None:
        ids = self._sorted(["Prune the pears"])
        frame = {"part": "today", "triage_version": triaging.VERSION}
        self._plan({"blocks": [{"start": "09:00", "end": "10:00", "kind": "deep", "title": "Pruning",
                                "items": list(ids.values())}], "_frame": frame})
        latest = self.agenda.latest(date.today())
        self.assertTrue(self.agenda.left_half(latest))
        kept = []
        self.agenda._ahead = lambda context, things, day, why, keep: kept.append((why, keep)) or {}
        real = self.agenda.day_of
        self.agenda.day_of = lambda now=None: dict(real(now), day=date.today())
        self.agenda.plan_ahead("left")
        self.assertEqual(kept[0][0], "left")
        self.assertNotIn("_frame", kept[0][1], "the day's plan kept as it was planned")
        self.assertEqual(kept[0][1]["blocks"][0]["title"], "Pruning")
        frame["part"] = "ahead"
        self.db.execute("UPDATE agenda_plans SET plan = ?", (json.dumps({"blocks": [], "_frame": frame}),))
        self.assertFalse(self.agenda.left_half(self.agenda.latest(date.today())), "both parts made: nothing left")

    def test_a_draft_left_half_drafted_is_drafted_again_when_opened(self) -> None:
        drafts = self.app.drafts
        now = datetime.now(timezone.utc).isoformat()
        for ident, status in (("draft_left", "drafting"), ("draft_ready", "ready")):
            self.db.execute("INSERT INTO work_drafts (id, created_at, updated_at, status, context) "
                            "VALUES (?, ?, ?, ?, '{}')", (ident, now, now, status))
        set_going = []
        drafts._collect_apart = lambda ident: set_going.append(ident)
        self.assertTrue(drafts.carry_on("draft_left"))
        self.assertEqual(drafts.get("draft_left")["status"], "collecting", "collected again, from the start")
        self.assertEqual(set_going, ["draft_left"])
        drafts._going.add("draft_left")
        self.assertFalse(drafts.carry_on("draft_left"), "being worked on here: left alone")
        self.assertFalse(drafts.carry_on("draft_ready"), "not left half done")
        self.assertFalse(drafts.carry_on("draft_none"))

    def test_waiting_to_start_carried_on_at_the_agents_start(self) -> None:
        drafts = self.app.drafts
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute("INSERT INTO work_drafts (id, created_at, updated_at, status, options) VALUES "
                        "('draft_wait', ?, ?, 'collecting', ?)",
                        (now, now, json.dumps({"start_when_ready": {"way": "auto"}})))
        self.db.execute("INSERT INTO work_drafts (id, created_at, updated_at, status) VALUES "
                        "('draft_mine', ?, ?, 'collecting')", (now, now))
        set_going = []
        drafts._collect_apart = lambda ident: set_going.append(ident)
        self.assertEqual(drafts.carry_on_waiting(), 1)
        self.assertEqual(set_going, ["draft_wait"], "the user's own waits until they open it")


if __name__ == "__main__":
    unittest.main()
