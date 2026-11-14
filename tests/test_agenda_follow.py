"""THE DAY'S PLAN, FOLLOWED (the user, 2026-11-10):
- a "delegable" thing is the agent's alone - not on the user's day, told the moment it is done
  ("他们做好就直接提示我做好了让我审批 ... 本质上我是让他们模拟完全自动工作的时候，会在什么时候干什么");
  an "approve" one is told ready for approval when its work is done ("有事情要审批是主动的");
- the plan's own work interrupted: carried on by itself in the background, said only when it
  could not be;
- a free stretch is shown on the calendar, never told; a question is asked as it comes up, once;
- a block's reminder missed while the agent was away: told late while it lasts; what was to be
  approved in an old plan's block gone by untold, told on its own.

Everyone and everything here is made up."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from datetime import date, datetime, timedelta, timezone

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.heartbeat.agenda import check as checking
from personalized_agent.heartbeat.agenda import items as gathered
from personalized_agent.heartbeat.agenda import triage as triaging
from personalized_agent.heartbeat.agenda.act import Reminders
from personalized_agent.heartbeat.agenda.execute import MOST_RESUMES, WORK_KEY, Executor
from personalized_agent.heartbeat.agenda.plan import Agenda


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.db = self.app.db
        self.sent = []

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _sorted(self, kinds):
        for title in kinds:
            self.app.todos.add(title)
        gathered.collect(self.db)
        for one in gathered.open_items(self.db):
            self.db.execute("UPDATE agenda_items SET triage = ?, triaged_for = ? WHERE id = ?",
                            (json.dumps({"who": kinds[one["title"]], "minutes": 10,
                                         "kind": "a garden job", "rule": triaging.APPROVE_RULE}),
                             triaging.judged_for(one), one["id"]))
        return {one["title"]: one["id"] for one in gathered.open_items(self.db)}

    def _plan(self, plan, ident="p1"):
        self.db.execute("INSERT INTO agenda_plans (id, day, made_at, plan) VALUES (?, ?, ?, ?)",
                        (ident, date.today().isoformat(), datetime.now(timezone.utc).isoformat(),
                         json.dumps(plan)))
        return Agenda(self.db, lambda: None, self.app.config)

    def _handed(self, ident, draft):
        self.db.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (WORK_KEY + ident, draft))

    def _notify(self, title, message, meta):
        self.sent.append((title, message, meta))

    @staticmethod
    def _at(hhmm):
        hours, minutes = map(int, hhmm.split(":"))
        return datetime.combine(date.today(), datetime.min.time()) + timedelta(hours=hours,
                                                                              minutes=minutes)


class StepThreeDecidedByThePlanTests(_Base):
    """The user, 2026-11-11: "所有calender notification/event都不在heartbeat直接加，而是要分析是否需要
    agent做，是否需要notify human(类似planning的分析方法)。比如现在这个notification, 理论上应该是agent做
    好我evaluate" - a reply the agent can write was told at its time instead, and its row closed."""

    def _step_three(self, who=None):
        from personalized_agent.common.events.models import utcnow
        done = self.app.heartbeat._add_notifications("heartbeat_test", [{
            "type": "reminder", "title": "Reply to the orchard club about a visit",
            "message": "The club wrote that its co-organiser can set up a visit for you.",
            "why": "Their reply has waited a day.",
            "at": (utcnow() + timedelta(hours=1)).isoformat()}])
        task = self.app.scheduler_store.get(done[0]["task_id"])
        gathered.collect(self.db)
        if who is not None:
            self.db.execute("UPDATE agenda_items SET triage = ? WHERE source = 'calendar' AND ref_id = ?",
                            (json.dumps({"who": who, "kind": "a message"}), task.id))
        return task

    def _told(self, task):
        before = len(self.app.notifications.recent(limit=200))
        outcome = self.app.executor.execute(task)
        return outcome, len(self.app.notifications.recent(limit=200)) - before

    def test_marked_for_the_plan_while_the_plan_is_on(self) -> None:
        self.assertTrue(self._step_three().task_payload.get("agenda_decides"))
        self.app.config.set("module_agenda_enabled", False)
        self.assertNotIn("agenda_decides", self._step_three().task_payload)

    def test_the_agents_is_not_told_and_stays_on_the_plan_after_its_time(self) -> None:
        task = self._step_three("delegable")
        outcome, told = self._told(task)
        self.assertEqual(told, 0, outcome.result)
        self.assertIn("the agent does it", outcome.result)
        self.app.scheduler_store.complete(task.id, outcome.result)
        gathered.collect(self.db, now=datetime.now(timezone.utc) + timedelta(hours=3))
        row = self.db.query_one("SELECT status FROM agenda_items WHERE ref_id = ?", (task.id,))
        self.assertEqual(row["status"], "open", "held for the agent's part, past its time")
        gathered.collect(self.db, now=datetime.now(timezone.utc) + timedelta(days=gathered.HELD_DAYS + 1))
        row = self.db.query_one("SELECT status FROM agenda_items WHERE ref_id = ?", (task.id,))
        self.assertEqual(row["status"], "closed", "not held for ever")

    def test_one_to_approve_is_the_agents_too_and_gone_is_not_told(self) -> None:
        self.assertEqual(self._told(self._step_three("approve"))[1], 0)
        outcome, told = self._told(self._step_three("gone"))
        self.assertEqual(told, 0)
        self.assertIn("no longer needed", outcome.result)

    def test_the_users_unsorted_or_the_agents_work_off_is_told(self) -> None:
        self.assertEqual(self._told(self._step_three("human"))[1], 1)
        self.assertEqual(self._told(self._step_three())[1], 1, "not sorted yet: told")
        self.app.config.set("module_agenda_work_enabled", False)
        self.assertEqual(self._told(self._step_three("delegable"))[1], 1,
                         "the agent's work off: no one would do it - told")


class StuckIsNotGoingTests(_Base):
    """2026-11-11: two pieces of the plan's work stuck before they began (one "ready", one
    "collecting", since the day before) were counted as going - at MOST_RUNNING, nothing of the plan
    started again, and a reply the agent could have written was told to the user instead."""

    def test_only_what_moved_lately_or_runs_counts(self) -> None:
        now = datetime.now(timezone.utc)
        moved = {"d_ready": now - timedelta(hours=30), "d_collect": now - timedelta(hours=24),
                 "d_fresh": now - timedelta(minutes=5), "d_live": now - timedelta(days=2)}
        listed = [{"id": "d_ready", "work": {"versions": [{"state": "ongoing"}]}},
                  {"id": "d_collect", "work": {"versions": [{"state": "drafting"}]}},
                  {"id": "d_fresh", "work": {"versions": [{"state": "ongoing"}]}},
                  {"id": "d_live", "work": {"versions": [{"state": "ongoing", "alive": True}]}},
                  {"id": "d_other", "work": {"versions": [{"state": "ongoing"}]}}]
        self.app.drafts.assigned = lambda most=200: listed
        self.app.drafts.get = lambda ident: {"updated_at": moved[ident].isoformat()}
        self.assertEqual(self.app._agenda_going(["d_ready", "d_collect"]), 0, "stuck: not going")
        self.assertEqual(self.app._agenda_going(["d_ready", "d_collect", "d_fresh", "d_live"]), 2,
                         "moved lately, or a run alive however long")


class SeenThroughTests(_Base):
    """The user, 2026-11-11: "agent如果没正常做要自动resume, retrigger, 到自己设的ddl还没做完提醒我" - and a
    side agent's note: held for the agent, nothing told the user if its work never began."""

    def _draft(self, ident, status, minutes_ago, run_status=None):
        moved = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
        run = f"run_{ident}" if run_status else ""
        self.db.execute("INSERT INTO work_drafts (id, created_at, updated_at, source, text, status, run_id) "
                        "VALUES (?, ?, ?, 'agenda', 'x', ?, ?)", (f"d_{ident}", moved, moved, status, run))
        if run_status:
            self.db.execute("INSERT INTO work_runs (id, unit_id, unit_name, folder, status, instruction, "
                            "plan, log, final, started_at, ended_at, updated_at) VALUES "
                            "(?, 'u', 'u', '.', ?, '', '{}', '[]', '{}', ?, ?, ?)",
                            (run, run_status, moved, moved, moved))
        return f"d_{ident}"

    def _executor(self):
        return Executor(self._plan({"blocks": []}), lambda *a: {})

    def test_stuck_or_unfinished_handed_over_again_then_told_as_theirs(self) -> None:
        ids = self._sorted({"Answer the seed club": "delegable", "Book the hall": "approve",
                            "Price the fencing": "delegable", "Order bulbs": "delegable"})
        stuck, failed, fresh = ids["Answer the seed club"], ids["Book the hall"], ids["Price the fencing"]
        self._handed(stuck, self._draft("stuck", "collecting", 120))
        self._handed(failed, self._draft("failed", "started", 10, run_status="failed"))
        self._handed(fresh, self._draft("fresh", "collecting", 5))
        executor = self._executor()
        said = executor.see_through(self._notify, now=self._at("08:00"))
        self.assertCountEqual(said["again"], [stuck, failed], "a fresh one is left to go on")
        self.assertEqual(executor.started_for(stuck), "", "handed over again when its time comes")
        self.assertEqual(self.sent, [])
        for n in range(2):                      # stuck again and again
            self._handed(stuck, self._draft(f"stuck{n}", "ready", 120))
            executor.see_through(self._notify, now=self._at("08:00"))
        self.assertEqual(len(self.sent), 1)
        title, message, meta = self.sent[0]
        self.assertEqual(title, "The agent could not do it: Answer the seed club")
        self.assertIn("It is yours to do", message)
        self.assertTrue(meta["agenda"]["gave_up"])
        self._handed(stuck, self._draft("stuck9", "ready", 120))
        executor.see_through(self._notify, now=self._at("08:00"))
        self.assertEqual(len(self.sent), 1, "told once")

    def test_the_agents_help_with_the_users_own_is_seen_through_too(self) -> None:
        """2026-11-11: the agent's part of the user's call (watch for a message, have the material
        ready) was interrupted, said "could not carry on", and never handed over again."""
        ids = self._sorted({"Call the seed club at nine": "human"})
        call = ids["Call the seed club at nine"]
        self._handed(call, self._draft("help", "started", 10, run_status="failed"))
        today = date.today().isoformat()
        self._due(call, f"{today} 08:00")
        executor = self._executor()
        said = executor.see_through(self._notify, now=self._at("10:00"))
        self.assertEqual(said["again"], [call], "its help handed over again")
        self.assertEqual(said["late"], [], "the user's own call is not the agent's to be late on")
        self.assertEqual(self.sent, [])

    def _due(self, ident, when):
        row = self.db.query_one("SELECT triage FROM agenda_items WHERE id = ?", (ident,))
        said = dict(json.loads(row["triage"]), deadline=when)
        self.db.execute("UPDATE agenda_items SET triage = ?, detail = ? WHERE id = ?",
                        (json.dumps(said), "[a reminder] The seed club asked for a date.", ident))

    def test_its_deadline_gone_by_not_done_told_once(self) -> None:
        ids = self._sorted({"Answer the seed club": "delegable", "Water the beds": "human"})
        reply = ids["Answer the seed club"]
        today = date.today().isoformat()
        self._due(reply, f"{today} 10:00")
        self._due(ids["Water the beds"], f"{today} 10:00")
        executor = self._executor()
        executor.see_through(self._notify, now=self._at("09:30"))
        self.assertEqual(self.sent, [], "not yet")
        self._handed(reply, self._draft("going", "started", 1, run_status="interrupted"))
        executor.see_through(self._notify, now=self._at("10:05"))
        [(title, message, meta)] = self.sent
        self.assertEqual(title, "Not done in time: Answer the seed club")
        self.assertIn("by 10:00", message)
        self.assertIn("still working on it", message)
        self.assertIn("The seed club asked for a date.", message, "what it was about, as it came")
        self.assertTrue(meta["agenda"]["late"])
        executor.see_through(self._notify, now=self._at("11:00"))
        self.assertEqual(len(self.sent), 1, "once; the user's own thing is not the agent's to be late on")

    def test_several_late_at_once_told_in_one(self) -> None:
        ids = self._sorted({"Answer the seed club": "delegable", "Book the hall": "approve"})
        for ident in ids.values():
            self._due(ident, f"{(date.today() - timedelta(days=1)).isoformat()} 16:00")
        self._executor().see_through(self._notify, now=self._at("09:00"))
        [(title, message, meta)] = self.sent
        self.assertEqual(title, "Not done in time: 2")
        self.assertIn("- Book the hall", message)
        self.assertCountEqual(meta["agenda"]["approve"], list(ids.values()))

    def _set(self, ident, **said):
        row = self.db.query_one("SELECT triage FROM agenda_items WHERE id = ?", (ident,))
        self.db.execute("UPDATE agenda_items SET triage = ? WHERE id = ?",
                        (json.dumps(dict(json.loads(row["triage"]), **said)), ident))

    def test_told_before_a_hard_deadline_with_room_left_as_the_thing_needs(self) -> None:
        """2026-11-13: "提醒ddl是要根据事情内容留出余量的" - not only once it has gone by."""
        ids = self._sorted({"Prepare the seed talk": "delegable", "Answer the seed club": "delegable",
                            "Price the fencing": "delegable"})
        today = date.today().isoformat()
        talk, reply, soft = ids["Prepare the seed talk"], ids["Answer the seed club"], ids["Price the fencing"]
        self._due(talk, f"{today} 17:00")
        self._set(talk, hard=True, lead_minutes=240)            # a talk: hours before
        self._due(reply, f"{today} 17:00")
        self._set(reply, hard=True)                             # judged before lead_minutes: 30 at least
        self._due(soft, f"{today} 17:00")                       # soft: in the report only
        executor = self._executor()
        executor.see_through(self._notify, now=self._at("12:30"))
        self.assertEqual(self.sent, [], "not yet")
        executor.see_through(self._notify, now=self._at("13:05"))
        [(title, message, meta)] = self.sent
        self.assertEqual(title, "Due soon, not done yet: Prepare the seed talk")
        self.assertIn("still time to do it yourself", message)
        self.assertTrue(meta["agenda"]["at_risk"])
        executor.see_through(self._notify, now=self._at("16:35"))
        self.assertEqual([one[0] for one in self.sent][-1], "Due soon, not done yet: Answer the seed club")
        executor.see_through(self._notify, now=self._at("16:50"))
        self.assertEqual(len(self.sent), 2, "each once; the soft one not told before")
        self.assertIn("17:00", executor.late_said(talk), "said with it in the report")

    def test_its_part_done_already_it_is_not_late(self) -> None:
        """2026-11-13: "Not done in time ... It has not begun it" of a description the agent had finished
        minutes before - the thing made from its finished work, and the same thing again."""
        ids = self._sorted({"Write the seed note": "delegable", "Look at the seed note": "delegable"})
        first, review = ids["Write the seed note"], ids["Look at the seed note"]
        draft = self._draft("note", "started", 5, run_status="done")
        self.db.execute("UPDATE agenda_items SET source = 'work', ref_id = ? WHERE id = ?", (draft, review))
        yesterday = f"{(date.today() - timedelta(days=1)).isoformat()} 09:00"
        self._due(review, yesterday)
        self._due(first, yesterday)
        self._set(first, same_as=review)
        self.db.execute("INSERT INTO kv (key, value) VALUES (?, 'x')", ("agenda.told_done." + review,))
        said = self._executor().see_through(self._notify, now=self._at("10:00"))
        self.assertEqual((said["late"], self.sent), ([], []))


class LateIsForTheReportTests(_Base):
    """2026-11-13: "除非确实赶ddl，可以跳出直接汇报（或者放到waiting notifications里) 其它的什么agent没有启动，都放在
    report里一起"; "在我开会的时候，所有的回报都必须放在waiting notifictaion, 不能直接弹出"."""

    def _told(self):
        return [(one["title"], one["metadata"] if isinstance(one["metadata"], dict)
                 else json.loads(one["metadata"] or "{}")) for one in self.app.notifications.recent(50)]

    def test_late_alone_is_the_reports_racing_a_hard_one_told_and_held_like_any_other(self) -> None:
        ids = self._sorted({"Answer the seed club": "delegable", "Book the hall": "delegable"})
        soft, hard = ids["Answer the seed club"], ids["Book the hall"]
        row = self.db.query_one("SELECT triage FROM agenda_items WHERE id = ?", (hard,))
        self.db.execute("UPDATE agenda_items SET triage = ? WHERE id = ?", (json.dumps(dict(
            json.loads(row["triage"]), hard=True, deadline="2026-11-13 09:00")), hard))
        before = len(self._told())
        self.app._agenda_notify("Not done in time: Answer the seed club", "x",
                                {"agenda": {"approve": [soft], "until": "", "late": True}})
        self.assertEqual(len(self._told()), before, "not told: in the report")
        # RACING A HARD ONE: through the Monitor's "Due soon, not done yet" (2026-11-14), held by a
        # meeting or a silence like any other.
        self.app._agenda_notify("Not done in time: Book the hall", "x",
                                {"agenda": {"approve": [hard], "until": "", "late": True}})
        title, meta = self._told()[0]
        self.assertIn("Book the hall", title)
        self.assertFalse(meta.get("through"))
        self.assertEqual(meta["monitor"]["group"], "deadline")
        # The plan's own word on the time: told no more (2026-11-13: "其它都取消").
        told = len(self._told())
        self.app._agenda_notify("Deep work begins", "Until 11:00", {"agenda": {"block": "b1"}})
        self.assertEqual(len(self._told()), told)


class TheAgentsAloneTests(_Base):

    def test_done_a_delegable_thing_is_told_at_once_and_once(self) -> None:
        ids = self._sorted({"Reply to the seed club": "delegable", "Choose the hall's caterer": "approve"})
        reply, caterer = ids["Reply to the seed club"], ids["Choose the hall's caterer"]
        executor = Executor(self._plan({"blocks": []}), lambda *a: {})
        self._handed(reply, "draft_reply")
        self._handed(caterer, "draft_caterer")
        states = {"draft_reply": {"status": "done", "run": "run_r"},
                  "draft_caterer": {"status": "done", "run": "run_c"}}
        said = executor.follow(states.get, self._notify, lambda draft: True)
        self.assertCountEqual(said["done"], [reply, caterer])
        told = {meta["agenda"]["approve"][0]: (title, message) for title, message, meta in self.sent}
        title, message = told[reply]
        self.assertEqual(title, "Done - to look at: Reply to the seed club")
        self.assertIn("as it would on its own - nothing went out", message)
        executor.follow(states.get, self._notify, lambda draft: True)
        self.assertEqual(len(self.sent), 2, "each once")

    def test_done_an_approve_thing_is_told_ready_for_approval_at_once_and_once(self) -> None:
        """The user, 2026-11-10: "空闲不是主动提醒（有事情要审批是主动的）" - what is to be approved is
        told when it is ready, not held for a block."""
        ids = self._sorted({"Choose the hall's caterer": "approve"})
        caterer = ids["Choose the hall's caterer"]
        executor = Executor(self._plan({"blocks": []}), lambda *a: {})
        self._handed(caterer, "draft_caterer")
        state = {"status": "working", "run": "run_c"}
        executor.follow(lambda draft: state, self._notify, lambda draft: True)
        self.assertEqual(self.sent, [], "still working: nothing yet")
        state["status"] = "done"
        said = executor.follow(lambda draft: state, self._notify, lambda draft: True)
        self.assertEqual(said["done"], [caterer])
        [(title, message, meta)] = self.sent
        self.assertEqual(title, "Ready for your approval: Choose the hall's caterer")
        self.assertEqual(message, "The agent did its part - nothing went out.\n"
                                  "Look at it, then approve it, or say what is not right.")
        self.assertEqual(meta["agenda"]["approve"], [caterer], "it opens the approvals panel")
        executor.follow(lambda draft: state, self._notify, lambda draft: True)
        self.assertEqual(len(self.sent), 1, "once")

    def test_interrupted_it_is_carried_on_unsaid_a_few_times_at_most(self) -> None:
        """The user, 2026-11-10: "这个只是汇报一个状态吗，是的话你后台做就行了"."""
        ids = self._sorted({"Sort the seed packets": "delegable"})
        seeds = ids["Sort the seed packets"]
        executor = Executor(self._plan({"blocks": []}), lambda *a: {})
        self._handed(seeds, "draft_seeds")
        carried = []
        state = {"status": "interrupted", "run": "run_s", "stopped_at": "08:33"}
        said = executor.follow(lambda draft: state, self._notify,
                               lambda draft: carried.append(draft) or True)
        self.assertEqual(carried, ["draft_seeds"])
        self.assertEqual(said["resumed"], [seeds])
        self.assertEqual(self.sent, [], "carried on in the background, not told")
        for _ in range(MOST_RESUMES + 2):
            executor.follow(lambda draft: state, self._notify, lambda draft: carried.append(draft) or True)
        self.assertEqual(len(carried), MOST_RESUMES, "not without end")

    def test_not_carried_on_it_is_tried_again_then_handed_over_anew_not_told(self) -> None:
        """2026-11-11 ("agent如果没正常做要自动resume, retrigger"): "could not carry on: open it under
        Work assigned" was told at the first failure. Now tried again, then handed over anew."""
        ids = self._sorted({"Sort the seed packets": "delegable"})
        seeds = ids["Sort the seed packets"]
        executor = Executor(self._plan({"blocks": []}), lambda *a: {})
        self._handed(seeds, "draft_seeds")
        for _ in range(MOST_RESUMES + 1):
            executor.follow(lambda draft: {"status": "interrupted", "run": "run_s", "stopped_at": "08:33"},
                            self._notify, lambda draft: False)
        self.assertEqual(self.sent, [], "not told")
        moved = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        self.db.execute("INSERT INTO work_drafts (id, created_at, updated_at, source, text, status, run_id) "
                        "VALUES ('draft_seeds', ?, ?, 'agenda', 'x', 'started', 'run_s')", (moved, moved))
        self.db.execute("INSERT INTO work_runs (id, unit_id, unit_name, folder, status, instruction, plan, "
                        "log, final, started_at, ended_at, updated_at) VALUES ('run_s', 'u', 'u', '.', "
                        "'interrupted', '', '{}', '[]', '{}', ?, ?, ?)", (moved, moved, moved))
        said = executor.see_through(self._notify, now=self._at("08:40"))
        self.assertEqual(said["again"], [seeds], "handed over anew")
        self.assertEqual(self.sent, [])

    def test_switched_off_nothing_is_followed(self) -> None:
        ids = self._sorted({"Sort the seed packets": "delegable"})
        executor = Executor(self._plan({"blocks": []}), lambda *a: {}, allowed=lambda: False)
        self._handed(ids["Sort the seed packets"], "draft_seeds")
        executor.follow(lambda draft: {"status": "done", "run": "r"}, self._notify, lambda d: True)
        self.assertEqual(self.sent, [])

    def test_the_check_wants_the_agents_work_and_no_time_of_the_users_to_approve(self) -> None:
        """The user, 2026-11-10: "只对agent要做的事情进行planing" - the one to approve is the agent's
        work too; the user approves it when they choose."""
        ids = self._sorted({"Reply to the seed club": "delegable", "Choose the hall's caterer": "approve"})
        reply, caterer = ids["Reply to the seed club"], ids["Choose the hall's caterer"]
        things = [{"id": one, "title": title, "triage": {"who": "delegable" if one == reply else "approve",
                                                        "minutes": 10}}
                  for title, one in ids.items()]
        day = date.today()
        agent = [{"item": reply, "start": "09:00", "end": "09:20"},
                 {"item": caterer, "start": "09:20", "end": "09:40"}]
        alone = checking.check({"blocks": [], "agent_does": agent, "later": []}, things, day, "08:00", "20:00", 90, 600)
        self.assertTrue(alone["ok"], alone["problems"])
        self.assertEqual(alone["approval_days"], [])
        missing = checking.check({"blocks": [], "agent_does": agent[:1], "later": []}, things, day,
                                 "08:00", "20:00", 90, 600)
        self.assertTrue(any("Choose the hall's caterer" in one and "is the agent's and is nowhere" in one
                            for one in missing["problems"]), missing["problems"])


class MissedWhileAwayTests(_Base):

    def test_a_block_begun_while_away_is_told_late_while_it_lasts(self) -> None:
        reminders = Reminders(self._plan({"blocks": [
            {"start": "09:00", "end": "09:30", "kind": "batch", "title": "Small things", "items": []}]}),
            notify=self._notify)
        reminders.tick(self._at("09:20"))
        self.assertEqual(self.sent[0][0], "Late - Now, until 09:30: Small things")
        reminders.tick(self._at("09:25"))
        self.assertEqual(len(self.sent), 1, "once")
        # And a plan made again for the day keeps it told.
        Reminders(self._plan({"blocks": [
            {"start": "09:00", "end": "09:30", "kind": "batch", "title": "Small things", "items": []}]},
            ident="p2"), notify=self._notify).tick(self._at("09:26"))
        self.assertEqual(len(self.sent), 1, "not again for a plan made again")

    def test_what_was_to_be_approved_in_a_block_gone_by_is_told_on_its_own(self) -> None:
        ids = self._sorted({"Choose the hall's caterer": "approve", "Reply to the seed club": "delegable",
                            "Book the orchard hall": "approve"})
        caterer, reply, hall = (ids["Choose the hall's caterer"], ids["Reply to the seed club"],
                                ids["Book the orchard hall"])
        self._handed(caterer, "draft_c")
        self._handed(reply, "draft_r")          # the agent's own: told when done, not here
        reminders = Reminders(self._plan({"blocks": [
            {"start": "09:00", "end": "09:30", "kind": "batch", "title": "Approvals",
             "items": [caterer, reply, hall]}]}), notify=self._notify)
        reminders.tick(self._at("09:50"))
        [(title, message, meta)] = self.sent
        self.assertEqual(title, "To approve, missed earlier: 1")
        self.assertIn("- Choose the hall's caterer", message)
        self.assertEqual(meta["agenda"]["approve"], [caterer],
                         "only what the agent made something for, and is to be approved")
        reminders.tick(self._at("10:30"))
        self.assertEqual(len(self.sent), 1, "never twice")

    def test_told_in_its_block_it_is_not_told_again(self) -> None:
        ids = self._sorted({"Choose the hall's caterer": "approve"})
        caterer = ids["Choose the hall's caterer"]
        self._handed(caterer, "draft_c")
        reminders = Reminders(self._plan({"blocks": [
            {"start": "09:00", "end": "09:30", "kind": "batch", "title": "Approvals",
             "items": [caterer]}]}), notify=self._notify)
        reminders.tick(self._at("09:01"))
        self.assertEqual(self.sent[0][0], "To approve, until 09:30: 1")
        reminders.tick(self._at("09:45"))
        self.assertEqual(len(self.sent), 1)



PLAN = """1. Run `git push origin main` in the orchard repository.
2. Run `gcloud run deploy orchard --region us-central1`.
3. Write to the seed club that it is live."""


class OneClickApproveTests(_Base):
    """The user, 2026-11-10: "要给我一个一键approve按钮，我approve之后agent会去按照自己的计划做。做好之后把做好的
    evidence存好" - which actions: "只做计划里写的那几行"."""

    def test_a_command_runs_only_as_written_in_the_plan(self) -> None:
        from personalized_agent.agent.work.runner import in_plan
        self.assertTrue(in_plan("git push origin main", PLAN))
        self.assertTrue(in_plan("git  push   origin main", PLAN), "spacing aside")
        self.assertFalse(in_plan("git push --force origin main", PLAN), "word for word")
        self.assertFalse(in_plan("rm -rf build", PLAN))
        self.assertFalse(in_plan("", PLAN))

    def test_only_a_whole_command_of_a_step_and_never_one_it_says_it_would_not_run(self) -> None:
        """A side agent's note, 2026-11-10: a command named where the plan says it would NOT run it,
        and the front of a longer one - its --dry-run left off - passed as "in the plan"."""
        from personalized_agent.agent.work.runner import in_plan, plan_commands
        plan = """Done: the release is ready.
1. Run `git push origin main --dry-run` to see what would go.
2. I would not run `git push --force origin main`.
- Then `gcloud run deploy orchard --region us-central1`
The command `rm -rf build` is mentioned here, outside any step."""
        self.assertEqual(plan_commands(plan), ["git push origin main --dry-run",
                                               "gcloud run deploy orchard --region us-central1"])
        self.assertTrue(in_plan("git push origin main --dry-run", plan))
        self.assertFalse(in_plan("git push origin main", plan), "not the front of a longer one")
        self.assertFalse(in_plan("git push --force origin main", plan), "not one it would not run")
        self.assertFalse(in_plan("rm -rf build", plan), "not one outside the steps")
        self.assertFalse(in_plan("--region us-central1", plan), "not a part of one")

    def _run(self, folder):
        import types
        from personalized_agent.agent.work.runner import Run
        ran, denied = [], []
        run = types.SimpleNamespace(
            folder=folder, plan={"approved_plan": PLAN},
            _unconfined_places=lambda: [folder / "agent-data"],
            _denied=lambda *said: denied.append(said),
            _run_on_computer=lambda command, shell, cwd, timeout: ran.append(command) or {
                "ok": True, "returncode": 0, "stdout": "To orchard.git\n   1a2b..3c4d  main -> main"},
            _changed=lambda *said: None)
        run._evidence = lambda *said: Run._evidence(run, *said)
        return run, Run._run_approved, ran, denied

    def test_approved_its_commands_run_accounts_and_all_with_their_evidence_kept(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as work:
            folder = Path(work)
            run, approved, ran, denied = self._run(folder)
            said = approved(run, {"command": "git push origin main", "cwd": str(folder)}, PLAN)
            self.assertTrue(said.get("approved"), said)
            self.assertEqual(ran, ["git push origin main"], "a push, as the plan said")
            kept = Path(said["evidence"])
            self.assertEqual(kept.parent, folder / "evidence")
            text = kept.read_text(encoding="utf-8")
            for words in ("command: git push origin main", "ended: ok (exit 0)", "main -> main"):
                self.assertIn(words, text)
            refused = approved(run, {"command": "git push --force origin main", "cwd": str(folder)},
                               PLAN)
            self.assertTrue(refused.get("refused"))
            self.assertIn("not in the plan the user approved", refused["error"])
            self.assertEqual(len(ran), 1, "nothing else ran")
            # Never where nothing may reach - not even written in the plan.
            plan = PLAN + "\n4. Run `type " + str(folder / "agent-data" / "keys.json") + "`"
            run.plan["approved_plan"] = plan
            kept_out = approved(run, {"command": "type " + str(folder / "agent-data" / "keys.json"),
                                      "cwd": str(folder)}, plan)
            self.assertTrue(kept_out.get("refused"))
            self.assertEqual(len(ran), 1)

    def test_approved_the_run_gets_its_plans_commands_and_nothing_more(self) -> None:
        """The user, 2026-11-10: "能开什么权限我到时候会说，现在是什么都开不了" - "我说的是额外权限": a plan
        approved, run_outside for its commands; no asking for more, and the client's own tools not
        out of the sandbox (that is `unconfined`'s, the user's own choice)."""
        import types
        from personalized_agent.agent.work.runner import Run
        run = types.SimpleNamespace(plan={"approved_plan": PLAN}, unconfined=False,
                                    tools=types.SimpleNamespace(outside=None, approve=None),
                                    _run_outside="the plan's commands", _request_approval="asking")
        Run._offer_outside(run)
        self.assertEqual((run.tools.outside, run.tools.approve), ("the plan's commands", None))
        self.assertIsNone(Run._unconfined_for(run, run.tools), "its client's tools stay in the sandbox")
        run.plan = {}
        Run._offer_outside(run)
        self.assertEqual((run.tools.outside, run.tools.approve), (None, None), "no plan, nothing")
        run.unconfined = True
        Run._offer_outside(run)
        self.assertEqual((run.tools.outside, run.tools.approve),
                         ("the plan's commands", "asking"), "the user's own choice, as before")

    def test_carried_out_it_is_done_and_says_where_its_evidence_is(self) -> None:
        from personalized_agent.heartbeat.agenda.execute import GOING_AHEAD
        ids = self._sorted({"Deploy the orchard site": "delegable"})
        deploy = ids["Deploy the orchard site"]
        executor = Executor(self._plan({"blocks": []}), lambda *a: {})
        done = []
        executor.done = done.append
        self._handed(deploy, "draft_d")
        self.db.execute("INSERT INTO kv (key, value) VALUES (?, ?)", (GOING_AHEAD + deploy, "run_d"))
        executor._set("agenda.told_done." + deploy, "earlier")     # told when it was first done
        executor.follow(lambda draft: {"status": "done", "run": "run_d"}, self._notify, lambda d: True)
        self.assertEqual(done, [deploy])
        title, message, _meta = self.sent[0]
        self.assertEqual(title, "Done as approved: Deploy the orchard site")
        self.assertIn("evidence/", message)
        executor.follow(lambda draft: {"status": "done", "run": "run_d"}, self._notify, lambda d: True)
        self.assertEqual(len(self.sent), 1, "once")

    def test_its_plan_is_the_heading_not_a_sentence_naming_it(self) -> None:
        """2026-11-12: "Approved, your agent will: list. I wouldn't put holds..." - a sentence naming the
        heading had been taken for it."""
        from personalized_agent.heartbeat.agenda.execute import own_plan
        text = ("Three times to offer.\n\n## WHAT I WOULD DO ON MY OWN\n1. Send the three lines.\n"
                "2. Add the call once a slot is picked.\n\nWHAT I'M NOT SURE OF\n- her trip\n\n"
                "The guide on your panel has the WHAT I WOULD DO ON MY OWN list. I wouldn't put holds.")
        self.assertEqual(own_plan(text), "1. Send the three lines.\n2. Add the call once a slot is picked.")
        self.assertEqual(own_plan("See the WHAT I WOULD DO ON MY OWN list. Nothing else."), "")
        self.assertEqual(own_plan("**WHAT I WOULD DO ON MY OWN:**\n1. Run it"), "1. Run it")

    def test_carried_out_its_messages_left_for_the_user_it_waits_for_i_sent_it(self) -> None:
        """2026-11-12: "这个在wechat上的event是不是我approve了agent也是不能操作的？" - it cannot: the message is
        theirs to send; not done until they say so."""
        from personalized_agent.heartbeat.agenda.execute import CARRIED_OUT, GOING_AHEAD
        ids = self._sorted({"Three times to talk": "delegable"})
        talk = ids["Three times to talk"]
        executor = Executor(self._plan({"blocks": []}), lambda *a: {})
        done = []
        executor.done = done.append
        executor.left_to_send = lambda run: True
        self._handed(talk, "draft_t")
        self.db.execute("INSERT INTO kv (key, value) VALUES (?, ?)", (GOING_AHEAD + talk, "run_t"))
        executor.follow(lambda draft: {"status": "done", "run": "run_t"}, self._notify, lambda d: True)
        self.assertEqual(done, [], "not done while the message is unsent")
        self.assertEqual(executor._kv(CARRIED_OUT + talk), "run_t")
        title, _message, meta = self.sent[0]
        self.assertEqual(title, "Its part done - yours to send: Three times to talk")
        self.assertEqual(meta["agenda"]["approve"], [talk])

    def test_looked_again_and_changed_it_goes_back_for_approval_not_done(self) -> None:
        """2026-11-12: "哪怕用户approve了，你agent work的时候要重新检查一遍，因为用户的work directory可能他自己改
        过，你之前的plan已经不适用了"."""
        from personalized_agent.heartbeat.agenda.execute import GO_AHEAD, GOING_AHEAD
        self.assertIn("FIRST, LOOK AGAIN", GO_AHEAD)
        self.assertIn("DO NOT DO IT", GO_AHEAD)
        self.assertIn('"WHAT I WOULD DO ON MY OWN"', GO_AHEAD)
        ids = self._sorted({"Deploy the orchard site": "delegable"})
        deploy = ids["Deploy the orchard site"]
        executor = Executor(self._plan({"blocks": []}), lambda *a: {})
        done, asked = [], []
        executor.done = done.append
        executor.asks_again = lambda run: asked.append(run) or True
        self._handed(deploy, "draft_d")
        self.db.execute("INSERT INTO kv (key, value) VALUES (?, ?)", (GOING_AHEAD + deploy, "run_d"))
        executor.follow(lambda draft: {"status": "done", "run": "run_d"}, self._notify, lambda d: True)
        self.assertEqual((done, asked), ([], ["run_d"]), "never taken for done")
        title, _message, meta = self.sent[0]
        self.assertEqual(title, "Looked again - it changed: Deploy the orchard site")
        self.assertEqual(meta["agenda"]["approve"], [deploy], "back for the user's approval")

    def test_the_button_carries_its_plan_out_in_the_same_run(self) -> None:
        ids = self._sorted({"Deploy the orchard site": "delegable"})
        deploy = ids["Deploy the orchard site"]
        unit = self.app.state_heartbeat.experience.store.create_work_project("Deploy the orchard site")
        draft = self.app.drafts.create(text="Deploy the orchard site", source="agenda", collect=False)
        self.db.execute("UPDATE work_drafts SET run_id = 'run_d', unit_id = ? WHERE id = ?",
                        (unit["unit_id"], draft["id"]))
        self._handed(deploy, draft["id"])
        asked = []
        self.app.work.continue_run = lambda run_id, unit, **said: asked.append((run_id, said)) or {
            "run": run_id}
        self.app._agenda_own_plan = lambda run_id, n=0: PLAN
        said = self.app.agenda_approved(deploy, "go")
        self.assertEqual(said["going"], "run_d")
        run_id, given = asked[0]
        self.assertEqual(run_id, "run_d", "the run that made it carries it out")
        self.assertEqual(given["approved_plan"], PLAN)
        self.assertNotIn("unconfined", given, "nothing extra: the run stays in the sandbox")
        self.assertEqual(given["started_by"], "agenda_work", "counted as the plan's work")
        self.assertIn("git push origin main", given["instruction"])
        self.assertIn("KEEP THE EVIDENCE", given["instruction"])
        self.app._agenda_own_plan = lambda run_id, n=0: ""
        self.assertIsNone(self.app.agenda_approved(deploy, "go"), "no plan written, nothing to do")



class AQuestionForTheUserTests(_Base):
    """The user, 2026-11-10: two "to approve" were "not started yet" - the agent could do nothing
    before they said something; "你这个是问我问题我并不能很明显的得到这个信息，你要make the question
    clear"."""

    def _asking(self, more=None):
        self.ids = ids = self._sorted(dict({"Evening call on the garden chat, maybe moved": "approve"},
                                           **(more or {})))
        call = ids["Evening call on the garden chat, maybe moved"]
        row = self.db.query_one("SELECT triage FROM agenda_items WHERE id = ?", (call,))
        said = dict(json.loads(row["triage"]),
                    missing="Who the call is with, and whether it is Tuesday.",
                    question="Is the evening call on the garden chat with Wren Halloway? And is it "
                             "Tuesday at 22:00 or 23:00 your time?",
                    why="The chat said 8pm their time, one hour later tomorrow.",
                    agent_part="Put the call on your calendar at the hour you say.")
        self.db.execute("UPDATE agenda_items SET triage = ? WHERE id = ?", (json.dumps(said), call))
        return call

    def test_the_question_stands_on_its_own(self) -> None:
        from personalized_agent.heartbeat.agenda.execute import question_for, waits_for_answer
        call = self._asking()
        thing = next(one for one in self._plan({"blocks": []}).things() if one["id"] == call)
        self.assertTrue(waits_for_answer(thing))
        self.assertFalse(waits_for_answer(thing, "draft_x"), "work handed over: not waiting")
        said = question_for(thing)
        self.assertTrue(said.startswith("Is the evening call on the garden chat with Wren Halloway?"))
        for words in ("About: Evening call on the garden chat, maybe moved", "From: a to-do on your list",
                      "What the agent knows: The chat said 8pm",
                      "Once you answer, the agent will: Put the call on your calendar"):
            self.assertIn(words, said)

    def test_asked_once_not_as_something_to_approve(self) -> None:
        call = self._asking()
        blocks = [{"start": "09:00", "end": "09:30", "kind": "batch", "title": "Replies", "items": [call]},
                  {"start": "12:30", "end": "12:40", "kind": "batch", "title": "Answers", "items": [call]}]
        reminders = Reminders(self._plan({"blocks": blocks}), notify=self._notify)
        reminders.tick(self._at("09:01"))
        titles = [one[0] for one in self.sent]
        self.assertIn("A question for you", titles)
        self.assertNotIn("To approve, until 09:30: 1", titles, "nothing made, nothing to approve")
        asked = next(one for one in self.sent if one[0] == "A question for you")
        self.assertIn("Is the evening call on the garden chat with Wren Halloway?", asked[1])
        self.assertEqual(asked[2]["agenda"]["approve"], [call], "it opens the panel to answer")
        reminders.tick(self._at("12:31"))
        self.assertEqual([one[0] for one in self.sent].count("A question for you"), 1,
                         "asked once, whatever block holds it again")

    def test_answered_it_is_kept_and_the_agent_carries_on_told_it(self) -> None:
        call = self._asking()
        handed = []
        self.app._agenda_assign = lambda words, title, folders: handed.append(words) or {
            "assigned": "draft_call"}
        said = self.app.agenda_approved(call, "answer:With Wren, Tuesday 23:00.")
        self.assertEqual(said["assigned"], "draft_call")
        self.assertIn("THE USER'S ANSWER", handed[0])
        self.assertIn("With Wren, Tuesday 23:00.", handed[0])
        row = self.db.query_one("SELECT triage FROM agenda_items WHERE id = ?", (call,))
        self.assertEqual(json.loads(row["triage"])["user_answer"], "With Wren, Tuesday 23:00.")
        self.assertEqual(self.app.agenda_executor.started_for(call), "draft_call")
        self.assertIsNone(self.app.agenda_approved(call, "answer:   "), "nothing said, nothing kept")

    def test_the_panel_asks_it_and_the_agent_does_not_start_it_first(self) -> None:
        call = self._asking()
        panel = self.app.agenda_approvals([call])
        [row] = panel["approval"]["rows"]
        self.assertEqual(row["state"], "needs_answer")
        self.assertIn("Is the evening call", row["question"])
        self.assertIn("The agent needs", "The agent needs to know")
        told = self.app.approval_item(row)
        self.assertIn("About: Evening call", told["question"])
        executor = Executor(self._plan({"blocks": [], "agent_does": [
            {"item": call, "what": "Put it on the calendar", "start": "09:00", "end": "09:10"}]}, "p2"),
            lambda *a: {"assigned": "never"})
        self.assertEqual(executor.start_due(self._at("09:05")), [], "nothing to do before the answer")

    def test_asked_once_as_it_comes_up_not_held_for_a_block(self) -> None:
        """The user, 2026-11-10: what waits for them is told, not held for a block - each question
        asked once, standing on its own."""
        call = self._asking({"Answer the seed club": "human"})
        reminders = Reminders(self._plan({"blocks": []}), notify=self._notify)
        reminders.tick(self._at("08:03"))
        [(title, message, meta)] = self.sent
        self.assertEqual(title, "A question for you")
        self.assertTrue(message.startswith("Is the evening call on the garden chat with Wren Halloway?"))
        self.assertEqual(meta["agenda"]["approve"], [call])
        self.assertEqual((meta["agenda"]["until"], meta["agenda"]["ask"]), ("", True))
        reminders.tick(self._at("08:04"))
        reminders.tick(self._at("15:00"))
        self.assertEqual(len(self.sent), 1, "asked once")

    def test_several_questions_in_one(self) -> None:
        call = self._asking({"Book the orchard hall": "approve"})
        hall = self.ids["Book the orchard hall"]
        row = self.db.query_one("SELECT triage FROM agenda_items WHERE id = ?", (hall,))
        self.db.execute("UPDATE agenda_items SET triage = ? WHERE id = ?", (json.dumps(dict(
            json.loads(row["triage"]), missing="Which Saturday.",
            question="Which Saturday should the orchard hall be booked for?")), hall))
        Reminders(self._plan({"blocks": []}), notify=self._notify).tick(self._at("08:03"))
        [(title, message, meta)] = self.sent
        self.assertEqual(title, "Questions for you: 2")
        self.assertCountEqual(meta["agenda"]["approve"], [call, hall])
        parts = message.split("\n\n")
        for begins in ("Is the evening call on the garden chat", "Which Saturday should the orchard hall"):
            self.assertTrue(any(part.startswith(begins) for part in parts), (begins, message))

    def test_a_thing_of_the_users_suggested_in_several_free_stretches_is_fine(self) -> None:
        """The user, 2026-11-10: suggestions, never a time - one thing may be suggested wherever it fits."""
        ids = self._sorted({"Answer the seed club": "human"})
        club = ids["Answer the seed club"]
        things = [{"id": club, "title": "Answer the seed club", "triage": {"who": "human", "minutes": 10}}]
        plan = {"blocks": [{"start": "09:00", "end": "09:30", "kind": "free", "title": "Nothing planned",
                            "items": [club], "suggested": True, "might_do": [{"item": club, "why": ""}]},
                           {"start": "12:30", "end": "13:40", "kind": "free", "title": "Nothing planned",
                            "items": [club], "suggested": True, "might_do": [{"item": club, "why": ""}]}],
                "agent_does": [], "later": [], "might_do": [{"item": club, "why": ""}]}
        said = checking.check(plan, things, date.today(), "08:00", "20:00", 90, 600)
        self.assertTrue(said["ok"], said["problems"])
        self.assertEqual((said["free_stretches"], said["suggested"]), (2, 2))


class FreeStretchesTests(_Base):
    """The user, 2026-11-10: "空闲不是主动提醒（有事情要审批是主动的），而是做一个calender display让用户可以
    随时查看" - a free stretch is shown on the calendar, never told."""

    def test_a_free_stretch_sends_nothing_a_meeting_still_does(self) -> None:
        ids = self._sorted({"Answer the seed club": "human", "Choose the hall's caterer": "approve"})
        club, caterer = ids["Answer the seed club"], ids["Choose the hall's caterer"]
        self._handed(caterer, "draft_c")
        reminders = Reminders(self._plan({"blocks": [
            {"start": "09:00", "end": "10:30", "kind": "free", "title": "Nothing planned", "suggested": True,
             "items": [club], "might_do": [{"item": club, "why": "Wren Halloway is waiting for it"}]},
            {"start": "10:30", "end": "11:00", "kind": "meeting", "title": "Seed swap", "fixed": True,
             "items": []},
            {"start": "11:00", "end": "13:00", "kind": "free", "title": "Nothing planned", "suggested": True,
             "items": [], "might_do": []}]}), notify=self._notify)
        for hhmm in ("09:00", "09:30", "11:00", "11:40"):
            self.assertEqual(reminders.tick(self._at(hhmm)), [], hhmm)
        self.assertEqual(self.sent, [], "begun or missed, a free stretch is not told")
        reminders.tick(self._at("10:20"))
        self.assertEqual([one[0] for one in self.sent], ["Now, until 11:00: Seed swap"],
                         "a meeting, ten minutes early, as before")


if __name__ == "__main__":
    unittest.main()
