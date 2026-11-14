"""THE DAY'S AGENDA (the user, 2026-11-09: "对所有我的activity, agent要进行安排 ... agent给我安排线性的日程";
the two principles: "make sure everything catch deadline" and "减少context switch，也就是尽量保证我有大块的
时间"; "能不能用之前work的reviewer-builder对每天日程做计划").

Gathered by code from where each thing is kept; judged by a model; planned by a builder, held to the
hard rules by code, reviewed as the user would judge. Phase 1: shown, never acted on.

Everyone and everything here is made up."""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.common.events.models import new_id
from personalized_agent.heartbeat.agenda import check as checking
from personalized_agent.heartbeat.agenda import items as gathered
from personalized_agent.heartbeat.agenda import triage as triaging
from personalized_agent.heartbeat.agenda.plan import Agenda

TOMORROW = date.today() + timedelta(days=1)


def _utc(local_naive: datetime) -> str:
    return local_naive.astimezone().astimezone(timezone.utc).isoformat()


def _on_calendar(db, title, at_local, details=None, event="going"):
    ident = new_id("task")
    db.execute("INSERT INTO scheduled_tasks (id, title, description, created_at, scheduled_at, "
               "status, execution_type, task_payload, metadata) VALUES (?, ?, ?, ?, ?, 'pending', "
               "'notification', ?, ?)",
               (ident, title, "", datetime.now(timezone.utc).isoformat(), _utc(at_local),
                json.dumps({"notify_user": event != "explore"}),
                json.dumps({"event": event, "details": details or {}})))
    return ident


class _Models:
    """Answers by the call's task: search words, the sorting, the builder's plans in turn, the
    reviewer's words in turn."""

    def __init__(self, sort=None, builds=(), reviews=()):
        self.sort, self.builds, self.reviews = sort or {}, list(builds), list(reviews)
        self.asked = []

    def invoke(self, request, role="primary"):
        self.asked.append(request)
        content = request.messages[0]["content"]
        said = json.loads(content) if isinstance(content, str) and content.startswith("{") else {}
        if request.task.endswith("_words"):
            text = {"things": [{"id": one["id"], "words": []} for one in said.get("things", [])]}
        elif request.task == "agenda_triage":
            text = {"things": [dict(self.sort.get(one["title"], {"who": "human"}), id=one["id"])
                               for one in said["things"]]}
        elif request.task == "agenda_build":
            ids = {one["title"]: one["id"] for one in said["things"]}
            text = self.builds.pop(0)(ids)
        elif request.task == "agenda_review":
            text = self.reviews.pop(0)
        else:
            text = {}
        return SimpleNamespace(text=json.dumps(text), stop_reason="end_turn", tool_calls=[])

    def tasks(self):
        return [one.task for one in self.asked if not one.task.endswith("_words")]


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.db = self.app.db

    def tearDown(self) -> None:
        self.agent.cleanup()


class GatheredTests(_Base):

    def test_every_source_one_row_each_changed_and_closed(self) -> None:
        todo = self.app.todos.add("Prune the quince hedge")
        at = datetime.combine(TOMORROW, datetime.min.time()) + timedelta(hours=11)
        _on_calendar(self.db, "Seed swap with Ilse Marrow", at, {"Location": "Allotment hall, plot 7"})
        _on_calendar(self.db, "A talk on soil fungi", at, event="explore")
        self.db.execute("INSERT INTO pending_confirmations (id, title, created_at, updated_at) "
                        "VALUES ('pc_1', 'Tea with Wren Halloway', ?, ?)",
                        (datetime.now(timezone.utc).isoformat(),) * 2)
        said = gathered.collect(self.db)
        self.assertEqual((said["added"], said["open"]), (4, 4))
        rows = {one["title"]: one for one in gathered.open_items(self.db)}
        self.assertEqual(rows["Seed swap with Ilse Marrow"]["place"], "Allotment hall, plot 7")
        self.assertTrue(rows["Seed swap with Ilse Marrow"]["fixed_at"])
        self.assertTrue(rows["A talk on soil fungi"]["triage"]["_from"]["quiet"], "asks nothing")
        self.assertEqual(gathered.collect(self.db)["added"], 0, "once each")
        self.app.todos.edit(todo["id"], text="Prune the quince hedge before the frost")
        self.assertEqual(gathered.collect(self.db)["changed"], 1, "its words changed")
        self.app.todos.delete(todo["id"])
        self.assertEqual(gathered.collect(self.db)["closed"], 1, "gone from the list: closed")

    def test_the_place_kept_from_the_heartbeat_and_the_chat(self) -> None:
        """The user, 2026-11-09: "heartbeat 里如果有地点可以留下"."""
        when = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d %H:%M")
        added = self.app.heartbeat._add_notifications("hb_test", [{
            "type": "reminder", "title": "Seed swap with Ilse Marrow", "message": "At the hall.",
            "at_local": when, "zone": "user", "place": "Allotment hall, plot 7",
            "why": "Agreed by mail."}])
        self.assertTrue(added[0]["ok"], added)
        said = self.app.agent.tools.call("schedule_task", {
            "title": "Pear grafting class", "description": "Bring a knife.", "rationale": "Booked.",
            "notify": "notify", "at_local": (datetime.now() + timedelta(days=3)).strftime(
                "%Y-%m-%d %H:%M"), "zone": "user", "event": "going",
            "place": "Orchard school, room 2"})
        self.assertTrue(said.get("task_id"), said)
        gathered.collect(self.db)
        places = {one["title"]: one["place"] for one in gathered.open_items(self.db)}
        self.assertEqual(places["Seed swap with Ilse Marrow"], "Allotment hall, plot 7")
        self.assertEqual(places["Pear grafting class"], "Orchard school, room 2")
        prompt = __import__("personalized_agent.heartbeat.core.heartbeat",
                            fromlist=["STEP_PROMPTS"]).STEP_PROMPTS["step_notify"]["returns"]
        self.assertIn('"place": "OPTIONAL', prompt)

    def test_the_whole_calendar_and_a_reminder_is_a_thing_to_do_from_its_day(self) -> None:
        """The user, 2026-11-09: two reminders three weeks off ("Write to <someone> about presenting in
        his group") were never gathered - the calendar was read a week ahead - and showed yellow."""
        far = datetime.combine(date.today() + timedelta(days=22), datetime.min.time()) + timedelta(hours=15)
        _on_calendar(self.db, "Write to Fern about presenting in her group", far, event="")
        _on_calendar(self.db, "Orchard board meeting", far + timedelta(days=1))
        gathered.collect(self.db)
        rows = {one["title"]: one for one in gathered.open_items(self.db)}
        self.assertIn("Write to Fern about presenting in her group", rows, "three weeks off: gathered")
        self.assertEqual(len(triaging.wanting(list(rows.values()))), 2)
        sort = {"Write to Fern about presenting in her group": {"who": "delegable", "at_time": False},
                "Orchard board meeting": {"who": "human", "at_time": True, "focus": "meeting"}}
        triaging.run(self.db, _Models(sort=sort), gathered.open_items(self.db))
        agenda = Agenda(self.db, lambda: None, self.app.config)
        things = {one["title"]: one for one in agenda.things()}
        reminder = things["Write to Fern about presenting in her group"]
        self.assertEqual((reminder["fixed_local"], reminder["not_before"]), ("", far.date().isoformat()))
        self.assertTrue(things["Orchard board meeting"]["fixed_local"], "a meeting stays at its time")
        plan = {"blocks": [], "later": [{"item": reminder["id"], "day": (far.date() - timedelta(days=3)).isoformat()}],
                "agent_does": []}
        problems = checking.check(plan, list(things.values()), date.today(), "09:00", "18:00", 90, 360)["problems"]
        self.assertTrue(any("is to be done from" in one for one in problems), problems)

    def test_work_handed_over_finished_is_for_the_user_to_look_over(self) -> None:
        assigned = lambda: [{"id": "draft_1", "text": "Compare three seed catalogues", "at": "",
                             "work": {"versions": [{"status": "done"}]}},
                            {"id": "draft_2", "text": "Sort the garden photos",
                             "work": {"versions": [{"state": "ongoing"}]}}]
        gathered.collect(self.db, assigned)
        rows = {one["title"]: one["detail"] for one in gathered.open_items(self.db)}
        self.assertIn("for the user to look at", rows["Compare three seed catalogues"])
        self.assertIn("the agent is doing it", rows["Sort the garden photos"])


class JudgedTests(_Base):

    def test_a_hard_deadline_only_on_a_line_it_was_shown(self) -> None:
        one = {"id": "agenda_1", "place": ""}
        said = triaging.judged({"who": "human", "deadline": "2030-03-01 17:00", "hard": True,
                                "shown_by": ["state_made_up"], "minutes": 40, "focus": "deep"},
                               one, {"state_real"})
        self.assertEqual(said["deadline"], "2030-03-01 17:00")
        self.assertFalse(said["hard"], "no line shown says so")
        said = triaging.judged({"who": "human", "deadline": "2030-03-01 17:00", "hard": True,
                                "shown_by": ["state_real"]}, one, {"state_real"})
        self.assertTrue(said["hard"])
        self.assertIsNone(triaging.judged({"who": "someone"}, one, set()))
        self.assertEqual(triaging.judged({"who": "human", "deadline": "soon"}, one, set())["deadline"], "")

    def test_three_kinds_the_users_word_for_a_kind_holds_and_moves_the_rest(self) -> None:
        """The user, 2026-11-09: "agent做一些，人类现在审批，之后可以完全delegate agent的 ... 人类现在和之后
        都要审批的 ... 人的deep work, agent作为辅助的 ... 这三个分类也可以interative 调整的"."""
        from personalized_agent.api.routes import build_routes
        for title in ("Reply to Wren about Thursday", "Reply to Ilse about the swap time",
                      "Write the orchard report"):
            self.app.todos.add(title)
        gathered.collect(self.db)
        sort = {"Reply to Wren about Thursday": {"who": "approve", "kind": "reply to a scheduling email"},
                "Reply to Ilse about the swap time": {"who": "approve",
                                                      "kind": "reply to a scheduling email"},
                "Write the orchard report": {"who": "human", "kind": "write a report", "focus": "deep"}}
        models = _Models(sort=sort)
        triaging.run(self.db, models, gathered.open_items(self.db))
        rows = {one["title"]: one for one in gathered.open_items(self.db)}
        self.assertEqual(rows["Write the orchard report"]["triage"]["who"], "human")
        agenda = Agenda(self.db, lambda: models, self.app.config)
        self.app.agenda = agenda
        said = build_routes(self.app)[("POST", "/api/agenda/who")](SimpleNamespace(
            require=lambda name: {"id": rows["Reply to Wren about Thursday"]["id"],
                                  "who": "delegable"}[name],
            get=lambda name, default=None: {"for_kind": True}.get(name, default)))
        self.assertEqual(len(said["moved"]), 2, "every thing of its kind")
        rows = {one["title"]: one["triage"] for one in gathered.open_items(self.db)}
        self.assertEqual(rows["Reply to Ilse about the swap time"]["who"], "delegable")
        self.assertEqual(triaging.modes(self.db)["reply to a scheduling email"]["set_by"], "user")
        evidence = self.db.query_one("SELECT COUNT(*) AS n FROM lesson_evidence")
        self.assertGreaterEqual(evidence["n"], 1, "what the user did is a lesson")
        # A new thing of that kind: the model says "approve"; the user's word holds.
        self.app.todos.add("Reply to Fern about Monday's slot")
        sort["Reply to Fern about Monday's slot"] = {"who": "approve",
                                                     "kind": "Reply to a scheduling email"}
        gathered.collect(self.db)
        triaging.run(self.db, models, gathered.open_items(self.db))
        rows = {one["title"]: one["triage"] for one in gathered.open_items(self.db)}
        self.assertEqual(rows["Reply to Fern about Monday's slot"]["who"], "delegable")
        sent = [json.loads(one.messages[0]["content"]) for one in models.asked
                if one.task == "agenda_triage"][-1]
        self.assertEqual((sent["kinds"][0]["id"], sent["kinds"][0]["who"], sent["kinds"][0]["set_by"]),
                         ("K1", "delegable", "user"), "listed to choose from, the user's first")

    def test_a_kind_chosen_by_its_number_or_a_new_one_made(self) -> None:
        """The user, 2026-11-09: "不再让模型自由起名 ... 选中了编号，代码就强制用你的分类"; "他应该允许
        创建新编号"."""
        triaging.set_mode(self.db, "reply to a scheduling email", "delegable", "user")
        for title in ("Answer Ilse about Friday", "Order bean poles", "Order seed trays"):
            self.app.todos.add(title)
        gathered.collect(self.db)
        seen = []

        class Chooses(_Models):
            def invoke(self, request, role="primary"):
                if request.task == "agenda_triage":
                    said = json.loads(request.messages[0]["content"])
                    seen.append(said["kinds"])
                    answer = {"Answer Ilse about Friday": {"who": "approve", "kind_id": "K1"},
                              "Order bean poles": {"who": "delegable", "kind_id": "new",
                                                   "new_kind": "order garden supplies"},
                              "Order seed trays": {"who": "delegable", "kind_id": "new",
                                                   "new_kind": "order garden supplies"}}
                    return SimpleNamespace(tool_calls=[], text=json.dumps({"things": [
                        dict(answer[one["title"]], id=one["id"]) for one in said["things"]]}))
                return super().invoke(request, role)
        triaging.run(self.db, Chooses(), gathered.open_items(self.db))
        rows = {one["title"]: one["triage"] for one in gathered.open_items(self.db)}
        self.assertEqual((rows["Answer Ilse about Friday"]["kind"], rows["Answer Ilse about Friday"]["who"]),
                         ("reply to a scheduling email", "delegable"), "K1 chosen: the user's word holds")
        self.assertEqual(rows["Order bean poles"]["kind"], rows["Order seed trays"]["kind"])
        made = triaging.modes(self.db)["order garden supplies"]
        self.assertEqual((made["mode"], made["set_by"]), ("delegable", "agent"), "a new kind, kept")
        self.assertEqual(seen[0][0]["kind"], "reply to a scheduling email")
        triaging.set_mode(self.db, "reply to a scheduling email", "approve", "agent")
        self.assertEqual(triaging.modes(self.db)["reply to a scheduling email"]["set_by"], "user",
                         "the model never writes over a kind the user set")
        listed, ids = triaging.kinds_listed(triaging.modes(self.db))
        self.assertEqual([one["id"] for one in listed], ["K1", "K2"])
        self.assertEqual(triaging.kind_of({"kind_id": "k2"}, ids), ("order garden supplies", False))
        self.assertEqual(triaging.kind_of({"kind_id": "K9"}, ids), ("", False), "no such number")

    def test_after_the_first_call_three_at_once_and_the_progress_by_the_way_sorted_now(self) -> None:
        """The user, 2026-11-09: "为什么79/80卡了这么久" - sorted again the new way, the old way's kinds
        counted as done; and one call after another, a minute and more each."""
        import threading
        import time
        for n in range(40):
            self.app.todos.add(f"Sow row {n}")
        gathered.collect(self.db)
        going, most, firsts = [0], [0], []
        lock = threading.Lock()

        class Slow(_Models):
            def invoke(self, request, role="primary"):
                if request.task == "agenda_triage":
                    said = json.loads(request.messages[0]["content"])
                    with lock:
                        going[0] += 1
                        most[0] = max(most[0], going[0])
                        firsts.append(len(said["contexts_in_use"]))
                    time.sleep(0.3)
                    with lock:
                        going[0] -= 1
                    return SimpleNamespace(tool_calls=[], text=json.dumps({"things": [
                        {"id": one["id"], "who": "human", "context": "rows"} for one in said["things"]]}))
                return super().invoke(request, role)
        said = triaging.run(self.db, Slow(), gathered.open_items(self.db))
        self.assertEqual((said["calls"], said["left"]), (4, 0))
        self.assertEqual(most[0], 3, "three at once after the first")
        self.assertEqual(firsts[0], 0)
        self.assertTrue(all(n == 1 for n in firsts[1:]), "the rest shown the first's names")
        agenda = Agenda(self.db, lambda: None, self.app.config)
        self.assertTrue(all(one["judged"] for one in agenda.things()))
        self.db.execute("UPDATE agenda_items SET triaged_for = fingerprint")   # sorted an old way
        self.assertFalse(any(one["judged"] for one in agenda.things()), "not counted as done")

    def test_what_waits_for_a_time_is_looked_at_as_over_or_its_time_to_be_set(self) -> None:
        """The user, 2026-11-09: "很多没有确认时间的event也要加进去的吧（确认一下有没有做完然后加进去)"."""
        from personalized_agent.heartbeat.agenda.plan import BUILDER_SYSTEM
        long_ago = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        self.db.execute("INSERT INTO pending_confirmations (id, title, created_at, updated_at) "
                        "VALUES ('pc_old', 'Tea with Wren Halloway', ?, ?)", (long_ago, long_ago))
        gathered.collect(self.db)
        one = gathered.open_items(self.db)[0]
        handed = triaging.shown(self.db, [one], {})
        self.assertEqual(handed[0]["open_for_days"], 20)
        self.assertIn("AGREED, ITS TIME NOT SET", triaging.SYSTEM)
        self.assertIn("SETTLING ITS TIME, not the event", triaging.SYSTEM)
        self.assertIn('A thing from "waiting" was agreed and has no time yet', BUILDER_SYSTEM)

    def test_a_message_already_decided_on_is_the_agents_and_only_approve_is_sorted_again(self) -> None:
        """The user, 2026-11-09: writing to someone about presenting, pinging someone about a talk -
        "这种应该是agent完全自己做"."""
        self.assertIn("WRITING TO SOMEONE THE USER HAS\n    ALREADY DECIDED TO WRITE TO", triaging.SYSTEM)
        self.assertIn("Not a\n    message they have already decided to send.", triaging.SYSTEM)
        for title in ("Write to Fern about presenting in her group", "Book the orchard hall"):
            self.app.todos.add(title)
        gathered.collect(self.db)
        rows = gathered.open_items(self.db)
        kinds = {"Write to Fern about presenting in her group": "approve", "Book the orchard hall": "delegable"}
        for one in rows:
            who = kinds[one["title"]]
            self.db.execute("UPDATE agenda_items SET triage = ?, triaged_for = ? WHERE id = ?",
                            (json.dumps({"who": who}), triaging.judged_for(one), one["id"]))
        wanting = [one["title"] for one in triaging.wanting(gathered.open_items(self.db))]
        self.assertEqual(wanting, ["Write to Fern about presenting in her group"],
                         "only what was sorted to approve before the rule")
        row = next(one for one in gathered.open_items(self.db) if one["title"].startswith("Write to"))
        self.db.execute("UPDATE agenda_items SET triage = ? WHERE id = ?",
                        (json.dumps({"who": "approve", "rule": triaging.APPROVE_RULE}), row["id"]))
        self.assertEqual(triaging.wanting(gathered.open_items(self.db)), [], "sorted under it: kept")

    def test_judged_the_old_way_is_judged_again(self) -> None:
        self.app.todos.add("Order bean poles")
        gathered.collect(self.db)
        row = gathered.open_items(self.db)[0]
        self.db.execute("UPDATE agenda_items SET triaged_for = fingerprint, triage = ? WHERE id = ?",
                        (json.dumps({"who": "agent_first"}), row["id"]))
        self.assertEqual(len(triaging.wanting(gathered.open_items(self.db))), 1)

    def test_only_the_new_or_changed_are_judged_again(self) -> None:
        self.app.todos.add("Order bean poles")
        gathered.collect(self.db)
        models = _Models(sort={"Order bean poles": {"who": "delegable", "minutes": 10,
                                                    "focus": "shallow", "context": "garden"}})
        self.assertEqual(triaging.run(self.db, models, gathered.open_items(self.db))["judged"], 1)
        row = gathered.open_items(self.db)[0]
        self.assertEqual((row["triage"]["who"], row["triage"]["context"]), ("delegable", "garden"))
        self.assertIn("_from", row["triage"], "what it was gathered with is kept")
        triaging.run(self.db, models, gathered.open_items(self.db))
        self.assertEqual(models.tasks().count("agenda_triage"), 1, "nothing new: not asked again")


class WhereAndHowOftenTests(unittest.TestCase):
    """The user, 2026-11-09: "calender plan now(在overview上要移到calender里)"; "The agent does it -
    you always approve，太多了，其实绝大多数都是不需要approve的"."""

    def test_the_days_plan_is_on_the_calendar_not_the_overview(self) -> None:
        from pathlib import Path
        page = (Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
                / "index.html").read_text(encoding="utf-8")
        calendar = page[page.index('<section id="calendar"'):]
        calendar = calendar[:calendar.index("</section>")]
        overview = page[page.index('<section id="overview"'):]
        overview = overview[:overview.index("</section>")]
        self.assertIn('id="ov-agenda"', calendar)
        self.assertNotIn('id="ov-agenda"', overview)
        # "就把planning 按钮拿过来就行"; "day 和month view是在planned/current view 之下的".
        toolbar = calendar[calendar.index('class="cal-toolbar"'):calendar.index('id="calendar-surface"')]
        for one in ('id="cal-plan"', 'data-cal-mode="current"', 'data-cal-mode="planned"',
                    'data-cal-view="month"', 'data-cal-view="day"'):
            self.assertIn(one, toolbar)
        self.assertNotIn('data-cal-view="planned"', page, "Planned is a calendar, not a third view")
        self.assertLess(calendar.index('id="calendar-surface"'), calendar.index('id="ov-agenda"'),
                        "the details under the calendar")

    def test_approve_is_what_the_agent_cannot_decide_alone(self) -> None:
        """The user, 2026-11-09: "agent单独基于当前信息无法做决定的(或者大概率无法做决定的列为一定要给人
        审批）"; "agent在执行的时候不确定是否要执行的（比如信息不足）也变成给人审批"."""
        self.assertIn("CAN THE AGENT DECIDE IT ALONE, FROM WHAT IS KNOWN NOW?", triaging.SYSTEM)
        self.assertIn("EVEN WHEN IT GOES OUT TO OTHERS", triaging.SYSTEM)
        self.assertIn("WHEN IT COMES TO DOING IT", triaging.__doc__)
        said = triaging.judged({"who": "approve", "missing": "Which Friday suits them."},
                               {"id": "x", "place": ""}, set())
        self.assertEqual(said["missing"], "Which Friday suits them.")
        said = triaging.judged({"who": "delegable", "missing": "nothing"}, {"id": "x", "place": ""}, set())
        self.assertEqual(said["missing"], "", "only for what the user must decide")


class RoutineTests(_Base):
    """The user, 2026-11-09: "plan要根据用户的作息做adjust"."""

    def _windows(self):
        from personalized_agent.heartbeat.monitor.types import AWAY, Window
        zone = datetime.now().astimezone().tzinfo
        out = []
        for back in range(1, 29):
            day = date.today() - timedelta(days=back)
            for quarter in range(24 * 4):
                start = datetime.combine(day, datetime.min.time()).replace(tzinfo=zone) \
                    + timedelta(minutes=15 * quarter)
                hour = start.hour
                if day.weekday() >= 5:
                    main = "entertainment" if hour == 20 else AWAY        # a film, not work
                else:
                    main = "writing" if 9 <= hour < 17 and hour != 12 else AWAY
                out.append(Window(start=start, minutes=15, main=main,
                                  switch_rate=4.0 if hour < 12 else 20.0))
        return lambda since, until: [w for w in out if since <= w.start < until]

    def test_the_day_by_its_kind_from_the_users_windows(self) -> None:
        agenda = Agenda(self.db, lambda: None, self.app.config, windows=self._windows())
        monday = date.today() + timedelta(days=(0 - date.today().weekday()) % 7)
        said = agenda.routine(monday)
        self.assertEqual((said["kind"], said["begins"], said["ends"]), ("workday", 9, 17))
        self.assertEqual(said["work_minutes"], 7 * 60)
        lunch = next(one for one in said["hours"] if one["hour"] == 12)
        self.assertEqual(lunch["share"], 0.0, "away at noon")
        words = agenda.routine_said(monday)
        self.assertIn("09:00 at work on 100% of such days, mostly writing, focused for them", words)
        self.assertIn("14:00 at work on 100% of such days, mostly writing\n", words,
                      "as they usually are: said neither way")
        # Their night, from their habits: a computer left on at 03:00 is not a day begun.
        agenda.habits = lambda now=None: {"night_middle_hour": 3.5, "night_within_hours": 7}
        agenda._routines.clear()
        self.assertTrue(next(one for one in agenda.routine(monday)["hours"] if one["hour"] == 3)["night"])
        self.assertFalse(next(one for one in agenda.routine(monday)["hours"] if one["hour"] == 9)["night"])
        sunday = monday + timedelta(days=6)
        self.assertIn("they usually do not work on such a day", agenda.routine_said(sunday))
        self.assertEqual(agenda.capacities()["weekend"], 0)
        frame = agenda.day_of(datetime.combine(monday, datetime.min.time()) + timedelta(hours=7))
        self.assertEqual((frame["begins"], frame["ends"], frame["capacity"]), ("09:00", "17:00", 420))

    def test_counted_from_when_the_day_began_not_fixed_hours(self) -> None:
        """The user, 2026-11-09: "要结合用户习惯，什么时候focus, 什么时候开始工作，什么时候休息。不是固定的"."""
        agenda = Agenda(self.db, lambda: None, self.app.config, windows=self._windows(),
                        habits=lambda now=None: {"usual_work_stretch_minutes": 70,
                                                 "usual_absence_minutes": 25})
        monday = date.today() + timedelta(days=(0 - date.today().weekday()) % 7)
        said = agenda.routine(monday)
        self.assertEqual(said["span_hours"], 8.0, "09:00 to 17:00")
        first = said["after_start"][0]
        self.assertEqual((first["hour_after_start"], first["focus"]), (1, "focused"))
        words = agenda.routine_said(monday)
        self.assertIn("such a day usually runs 8.0 h", words)
        # From their windows: 09-12 at work, 12-13 away, 13-17 at work.
        self.assertEqual((said["stretch_minutes"], said["break_minutes"]), (240, 60),
                         "180 and 240 a day: the middle of them taken as it falls")
        self.assertIn("they usually keep at it 240 min before stepping away", words)
        self.assertIn("a time away usually lasts 60 min", words)
        self.assertIn("- hour 1: at work on 100% of such days, focused for them", words)
        # Begun late today - at 11:00 - the day runs as long as such a day runs, from then.
        zone = datetime.now().astimezone().tzinfo
        began = datetime.combine(monday, datetime.min.time()).replace(tzinfo=zone) + timedelta(hours=11)
        agenda.today = lambda: {"began": "11:00", "began_at": began, "worked_minutes": 30,
                                "on_since_last_break_minutes": 30}
        frame = agenda.day_of(datetime.combine(monday, datetime.min.time()) + timedelta(hours=11, minutes=30))
        self.assertEqual((frame["begins"], frame["ends"]), ("11:00", "19:00"))
        self.assertEqual(frame["stretch"], 240, "a deep block as long as they keep at one thing - their own")
        self.assertEqual(agenda.routine(monday)["focus_run_minutes"], 240)
        self.assertIn("they usually keep at one thing 240 min", agenda.routine_said(monday))
        # The day's begin and end are theirs, from their routine; what they do in it is theirs too
        # (the user, 2026-11-10: "你不要告诉user在几点做她的事情 ... 但是不要限制user做什么").
        from personalized_agent.heartbeat.agenda.plan import BUILDER_SYSTEM, PRINCIPLES
        self.assertIn("THE USER'S TIME IS THEIRS", PRINCIPLES)
        self.assertIn('"might_do"', BUILDER_SYSTEM)
        self.assertNotIn("NOT FIXED HOURS", BUILDER_SYSTEM, "no hours of theirs planned at all")

    def test_work_is_what_it_is_to_this_user(self) -> None:
        """The user, 2026-11-09: "agent要判断娱乐的不算（另外娱乐的判断要user-specific, 比如对我篮球是娱乐，
        对篮球up主就是工作)"."""
        from personalized_agent.heartbeat.agenda.plan import is_work
        from personalized_agent.heartbeat.monitor.types import Window
        at = datetime.now().astimezone()
        kinds = {"p-games": "work", "p-court": "leisure"}
        self.assertTrue(is_work(Window(start=at, main="entertainment", project_id="p-games"), kinds),
                        "watching games, for one whose work it is")
        self.assertFalse(is_work(Window(start=at, main="writing", project_id="p-court"), kinds),
                         "a leisure of theirs, whatever the window looked like")
        self.assertTrue(is_work(Window(start=at, main="coding"), kinds))
        self.assertFalse(is_work(Window(start=at, main="other"), kinds), "unknown is not counted")
        self.assertFalse(is_work(Window(start=at, main="browsing"), kinds))

    def test_a_deep_block_is_where_their_work_time_was_spent_not_the_most_runs(self) -> None:
        from personalized_agent.heartbeat.agenda.plan import by_time
        self.assertEqual(by_time([15] * 6 + [60, 120]), 60, "most runs short; half the time in an hour and more")
        self.assertIsNone(by_time([]))

    def test_a_day_ahead_holds_the_agents_work_whatever_kind_of_day(self) -> None:
        """The user, 2026-11-10: "只对agent要做的事情进行planing" - a day ahead is the agent's work, not
        the user's hours: it is counted, never held to what such a day of theirs holds."""
        saturday = TOMORROW + timedelta(days=(5 - TOMORROW.weekday()) % 7 or 7)
        things = [_thing("a", "Sort the seed orders", who="delegable", minutes=60)]
        plan = {"blocks": [], "later": [{"item": "a", "day": saturday.isoformat(), "order": 1}]}
        said = checking.check(plan, things, TOMORROW, "09:00", "17:00", 90, 420,
                              capacities={"workday": 420, "weekend": 0})
        self.assertTrue(said["ok"], said["problems"])
        self.assertEqual(said["days_ahead_load"], {saturday.isoformat(): "60 min of the agent's work"})


class OneNameForOneThingTests(_Base):
    """The user, 2026-11-09: sorted twelve at a time, each call shown the names the rest were given
    and every other thing's title - one name for one thing; the same thing said so."""

    def test_a_later_call_sees_the_names_given_and_the_rest(self) -> None:
        for n in range(14):
            self.app.todos.add(f"Hedge budget, part {n}")
        meeting = _on_calendar(self.db, "Hedge budget review with Ilse Marrow",
                               datetime.now() + timedelta(days=1))
        gathered.collect(self.db)
        ids = {one["title"]: one["id"] for one in gathered.open_items(self.db)}
        review = ids["Hedge budget review with Ilse Marrow"]
        seen = []

        class Named(_Models):
            def invoke(self, request, role="primary"):
                if request.task == "agenda_triage":
                    said = json.loads(request.messages[0]["content"])
                    seen.append(said)
                    return SimpleNamespace(tool_calls=[], text=json.dumps({"things": [
                        {"id": one["id"], "who": "human", "context": "hedge budget",
                         "same_as": review if one["title"] == "Hedge budget, part 0" else
                         "agenda_made_up"} for one in said["things"]]}))
                return super().invoke(request, role)
        triaging.run(self.db, Named(), gathered.open_items(self.db))
        self.assertGreaterEqual(len(seen), 2)
        self.assertEqual(seen[0]["contexts_in_use"], [])
        self.assertEqual(seen[1]["contexts_in_use"], ["hedge budget"], "the name the first gave")
        others = {one["id"] for one in seen[1]["the_other_things"]}
        self.assertTrue(others and not others & {one["id"] for one in seen[1]["things"]})
        rows = {one["title"]: one["triage"] for one in gathered.open_items(self.db)}
        self.assertEqual(rows["Hedge budget, part 0"]["same_as"], review)
        self.assertEqual(rows["Hedge budget, part 1"]["same_as"], "", "no such thing: not kept")
        self.assertTrue(meeting)

    def test_the_same_thing_planned_once_is_planned(self) -> None:
        d = TOMORROW.isoformat()
        things = [_thing("todo", "Go to the seed swap", minutes=60, same_as="cal"),
                  dict(_thing("cal", "Seed swap", minutes=60, focus="meeting"),
                       fixed_local=f"{d} 11:00")]
        plan = {"blocks": [{"start": "11:00", "end": "12:00", "kind": "meeting",
                            "title": "Seed swap", "items": ["cal"]}]}
        said = checking.check(plan, things, TOMORROW, "09:00", "18:00", 90, 360)
        self.assertTrue(said["ok"], said["problems"])


def _thing(ident, title, **triage):
    return {"id": ident, "title": title, "triage": dict({"who": "human", "minutes": 30}, **triage),
            "fixed_local": triage.pop("fixed_local", "") if "fixed_local" in triage else ""}


class CheckTests(unittest.TestCase):
    """The hard rules, by code - of the agent's own work only (the user, 2026-11-10: "只对agent要做的
    事情进行planing, 你不要告诉user在几点做她的事情")."""

    def setUp(self) -> None:
        self.day = TOMORROW
        d = self.day.isoformat()
        self.things = [
            _thing("a", "Draft the hedge budget", who="delegable", minutes=120, focus="deep",
                   context="hedge", deadline=f"{d} 17:00"),
            dict(_thing("b", "Seed swap", minutes=60, focus="meeting"), fixed_local=f"{d} 11:00"),
            _thing("c", "Reply to Ilse Marrow", minutes=15, focus="shallow"),
            _thing("g", "Book the orchard hall", who="approve", minutes=5)]

    def _check(self, plan, whole=True):
        return checking.check(plan, self.things, self.day, "09:00", "18:00", 90, 360, whole=whole)

    def _good(self):
        from personalized_agent.heartbeat.agenda.plan import day_blocks
        might = [{"item": "c", "why": "Ilse Marrow is waiting for it"}]
        return {"agent_does": [{"item": "a", "what": "Draft it", "start": "09:00", "end": "11:00"},
                               {"item": "g", "what": "Draft the booking", "start": "11:00", "end": "11:10"}],
                "might_do": might,
                "blocks": day_blocks({"day": self.day, "plan_from": "09:00", "ends": "18:00"},
                                     self.things, might)}

    def test_a_plan_that_holds_passes_and_is_counted(self) -> None:
        said = self._check(self._good())
        self.assertTrue(said["ok"], said["problems"])
        self.assertEqual((said["switches"], said["fragments"], said["batches"]), (0, 0, 0),
                         "nothing of the user's put at a time: nothing of theirs to count")
        self.assertEqual(said["approval_days"], [])
        self.assertEqual((said["free_stretches"], said["suggested"]), (2, 2),
                         "09:00-11:00 and 12:00-18:00, the reply suggested in each")

    def test_only_the_users_things_in_their_order_passes(self) -> None:
        """The user, 2026-11-10: "你可以在每个user slot告诉user你接下来XX时间内都是没安排，并且这些是你可能
        想要做的task。但是不要限制user做什么"."""
        self.things = [one for one in self.things if one["triage"]["who"] == "human"]
        said = self._check({"might_do": [{"item": "c", "why": "Ilse Marrow is waiting for it"}]})
        self.assertTrue(said["ok"], said["problems"])
        self.assertTrue(self._check({})["ok"], "the user's things need no place in the plan")

    def test_an_agent_thing_nowhere_is_said_and_nothing_made_up(self) -> None:
        plan = self._good()
        plan["agent_does"] = plan["agent_does"][1:]
        plan["might_do"].append({"item": "zz", "why": ""})
        problems = self._check(plan)["problems"]
        self.assertTrue(any('"zz" is not one of' in one for one in problems), problems)
        self.assertTrue(any("Draft the hedge budget" in one and "is the agent's and is nowhere in the plan"
                            in one for one in problems), problems)
        self.assertFalse(any("Reply to Ilse Marrow" in one for one in problems), "the user's: not placed")
        for where in ("at_risk", "clean_up"):
            plan = self._good()
            plan["agent_does"] = plan["agent_does"][1:]
            plan[where] = [{"item": "a", "why": "too much today"}]
            self.assertTrue(self._check(plan)["ok"], (where, self._check(plan)["problems"]))
        plan = self._good()
        plan["agent_does"] = plan["agent_does"][1:]
        plan["later"] = [{"item": "a", "day": self.day.isoformat()}]
        self.assertTrue(any("a day after" in one for one in self._check(plan)["problems"]),
                        "a day ahead is a day after today")

    def test_the_agents_work_done_by_its_deadline(self) -> None:
        plan = self._good()
        plan["agent_does"][0].update(start="16:00", end="18:00")
        said = self._check(plan)
        self.assertFalse(said["ok"])
        self.assertTrue(any("after its deadline" in one for one in said["problems"]), said["problems"])
        plan["at_risk"] = [{"item": "a", "why": "too much today"}]
        self.assertTrue(self._check(plan)["ok"], "said at risk: let be")
        # Put off to a day after its deadline - or to its day, when it is due before the day begins.
        plan = self._good()
        plan["agent_does"] = plan["agent_does"][1:]
        plan["later"] = [{"item": "a", "day": (self.day + timedelta(days=2)).isoformat()}]
        self.assertTrue(any("is put off to" in one for one in self._check(plan)["problems"]))
        ahead = self.day + timedelta(days=1)
        self.things.append(_thing("d", "Write the plot report", who="delegable",
                                  deadline=f"{ahead.isoformat()} 08:00"))
        plan = self._good()
        plan["later"] = [{"item": "d", "day": ahead.isoformat()}]
        self.assertTrue(any("Write the plot report" in one and "is put off to" in one
                            for one in self._check(plan)["problems"]), "due before its day begins")
        self.things[-1]["triage"]["deadline"] = f"{ahead.isoformat()} 17:00"
        self.assertTrue(self._check(plan)["ok"], self._check(plan)["problems"])

    def test_the_agents_work_not_before_its_day(self) -> None:
        ahead = self.day + timedelta(days=1)
        self.things.append(dict(_thing("e", "Order the bean poles", who="delegable"),
                                not_before=ahead.isoformat()))
        plan = self._good()
        plan["agent_does"].append({"item": "e", "start": "12:00", "end": "12:20"})
        self.assertTrue(any("Order the bean poles" in one and "not today" in one
                            for one in self._check(plan)["problems"]))
        plan = self._good()
        plan["later"] = [{"item": "e", "day": ahead.isoformat()}]
        self.assertTrue(self._check(plan)["ok"], self._check(plan)["problems"])
        self.things[-1]["not_before"] = (ahead + timedelta(days=2)).isoformat()
        self.assertTrue(any("not on" in one for one in self._check(plan)["problems"]))

    def test_the_users_thing_gets_no_day(self) -> None:
        """The user, 2026-11-10: "只对agent要做的事情进行planing" - the days ahead are the agent's work."""
        plan = self._good()
        plan["later"] = [{"item": "c", "day": (self.day + timedelta(days=1)).isoformat()}]
        for whole in (True, False):
            self.assertTrue(any("Reply to Ilse Marrow" in one and "is the user's: give it no day - put it "
                                "in \"might_do\" in its order" in one
                                for one in self._check(plan, whole)["problems"]), whole)
        # The agent's own may go weeks or months ahead.
        self.things.append(_thing("f", "Sort last year's seed list", who="delegable", minutes=300))
        plan = self._good()
        plan["later"] = [{"item": "f", "day": (self.day + timedelta(days=90)).isoformat()}]
        self.assertTrue(self._check(plan)["ok"], "months ahead is fine")

    def test_an_approve_thing_with_agent_work_needs_no_approval_time(self) -> None:
        """The user, 2026-11-10: "approve" waits for the user's approval, which they give when they
        choose - no time of theirs planned for it ("你不要告诉user在几点做她的事情")."""
        said = self._check(self._good())
        self.assertTrue(said["ok"], said["problems"])
        self.assertEqual((said["approval_days"], said["approval_blocks_today"]), ([], 0))
        self.assertFalse(any("g" in block.get("items", []) for block in self._good()["blocks"]),
                         "on none of the user's blocks")

    def test_the_day_alone_is_held_to_the_days_rules_only(self) -> None:
        """The user, 2026-11-09: the day first, the rest after - what belongs to the days ahead is
        held when they are planned."""
        plan = self._good()
        plan["agent_does"] = plan["agent_does"][1:]          # the budget not placed yet
        self.assertFalse(self._check(plan)["ok"])
        said = self._check(plan, whole=False)
        self.assertFalse(any("nowhere" in one for one in said["problems"]), said["problems"])
        plan["agent_does"].append({"item": "a", "start": "16:00", "end": "18:00"})
        self.assertTrue(any("after its deadline" in one for one in self._check(plan, whole=False)["problems"]),
                        "the day's own rules still held")

    def test_the_agents_times_read(self) -> None:
        plan = self._good()
        plan["agent_does"][0].update(start="9", end="")
        self.assertTrue(any("its times do not read" in one for one in self._check(plan)["problems"]))


class DayBlocksTests(unittest.TestCase):
    """THE USER'S DAY BY CODE (the user, 2026-11-10: "你可以在每个user slot告诉user你接下来XX时间内都是
    没安排，并且这些是你可能想要做的task。但是不要限制user做什么"): what is fixed at its time, and each free
    stretch with what of theirs might fit it - suggested, in "might_do"'s order."""

    def setUp(self) -> None:
        from personalized_agent.heartbeat.agenda.plan import day_blocks
        self.day_blocks = day_blocks
        d = TOMORROW.isoformat()
        after = (TOMORROW + timedelta(days=1)).isoformat()
        self.things = [
            dict(_thing("m1", "Seed swap", minutes=60, focus="meeting"), fixed_local=f"{d} 11:00",
                 place="Allotment hall"),
            dict(_thing("m2", "Orchard board", minutes=80, focus="meeting"), fixed_local=f"{d} 12:10"),
            dict(_thing("m3", "A talk on soil fungi", minutes=60), fixed_local=f"{d} 15:00", quiet=True),
            _thing("x", "Sort the seed orders", who="delegable"),
            dict(_thing("n", "Plant the garlic"), not_before=after),
            dict(_thing("t", "Order bean poles"), not_before=d, not_before_time="13:30"),
            _thing("h1", "Write the orchard report", minutes=300, focus="deep"),
            _thing("h2", "Reply to Ilse Marrow", minutes=15),
            _thing("h3", "Prune the quince hedge"),
            _thing("h4", "Label the seed drawer")]
        self.might = [{"item": ident, "why": f"why {ident}"}
                      for ident in ("x", "m1", "n", "t", "h2", "h1", "h3", "h4")]

    def _blocks(self, plan_from="09:00"):
        return self.day_blocks({"day": TOMORROW, "plan_from": plan_from, "ends": "18:00"},
                               self.things, self.might)

    def test_meetings_at_their_time_and_free_stretches_between_them(self) -> None:
        blocks = self._blocks()
        self.assertEqual([(one["start"], one["end"], one["kind"]) for one in blocks],
                         [("09:00", "11:00", "free"), ("11:00", "12:00", "meeting"),
                          ("12:10", "13:30", "meeting"), ("13:30", "18:00", "free")],
                         "12:00-12:10 too short to be a stretch; the talk only of interest")
        meeting = blocks[1]
        self.assertEqual((meeting["title"], meeting["items"], meeting["fixed"], meeting["place"]),
                         ("Seed swap", ["m1"], True, "Allotment hall"))
        free = blocks[0]
        self.assertEqual((free["title"], free["suggested"]), ("Nothing planned", True))

    def test_suggested_in_their_order_only_the_users_and_only_when_they_may(self) -> None:
        first, _m1, _m2, last = self._blocks()
        self.assertEqual(first["items"], ["h2", "h1", "h3"],
                         "the agent's, the fixed, the one for tomorrow and the one put off to 13:30 "
                         "left out - three at most, in might_do's order")
        self.assertEqual(first["might_do"][0], {"item": "h2", "why": "why h2"})
        self.assertEqual(last["items"], ["t", "h2", "h1"], "from 13:30 the one put off to then")

    def test_a_long_thing_is_suggested_whatever_it_would_take(self) -> None:
        """The user, 2026-11-10: "现在不需要给我做的东西估计时间了" - nothing of theirs is timed."""
        self.might = [{"item": "h1", "why": ""}]
        first = self._blocks()[0]
        self.assertEqual(first["items"], ["h1"], "300 minutes, in a stretch of two hours")

    def test_from_when_the_plan_is_made(self) -> None:
        blocks = self._blocks(plan_from="12:00")
        self.assertEqual([(one["start"], one["kind"]) for one in blocks],
                         [("12:10", "meeting"), ("13:30", "free")])
        self.assertEqual(self.day_blocks({"day": TOMORROW, "plan_from": "18:00", "ends": "18:00"},
                                         self.things, self.might), [])

    def test_put_off_to_a_time_suggested_in_a_stretch_that_runs_past_it(self) -> None:
        """One long stretch from before the time it was put off to: suggested there - the stretch
        runs past it (only one that ends by then leaves it out)."""
        self.things = [one for one in self.things if one["id"] not in ("m1", "m2")]
        self.might = [{"item": "t", "why": ""}]
        blocks = self._blocks()
        self.assertEqual([(one["start"], one["end"], one["items"]) for one in blocks],
                         [("09:00", "18:00", ["t"])])
        self.assertEqual(self.day_blocks({"day": TOMORROW, "plan_from": "09:00", "ends": "13:00"},
                                         self.things, self.might)[0]["items"], [],
                         "a stretch over by 13:00 does not hold what is put off to 13:30")


class AgentOnlyReplanTests(unittest.TestCase):
    """A REPLAN OF THE AGENT-ONLY PLAN (2026-11-10): the agent's work still to come and the user's
    might-do are known, not new; the agent's work is kept unless the answer places it again."""

    def setUp(self) -> None:
        from personalized_agent.heartbeat.agenda import adjust
        self.adjust = adjust
        self.day = TOMORROW
        self.row = {"day": self.day.isoformat(), "made_at": "2030-01-01T08:00:00+00:00", "plan": {
            "blocks": [], "might_do": [{"item": "h", "why": ""}],
            "agent_does": [{"item": "early", "what": "", "start": "08:00", "end": "08:30"},
                           {"item": "x", "what": "draft", "start": "13:00", "end": "13:30"}]}}
        self.things = [_thing("x", "Sort the seed orders", who="delegable"),
                       _thing("early", "File the receipts", who="delegable"),
                       _thing("h", "Prune the quince hedge"),
                       _thing("new", "Answer the orchard club", who="delegable")]
        for one in self.things:
            one["triage"]["at"] = "2030-01-01T07:00:00+00:00"
        self.base = adjust.base_of(self.row, self.day, "10:00")

    def test_known_not_new_and_the_agents_work_that_went_by_comes_back(self) -> None:
        placing = {one["id"]: one["why"] for one in self.adjust.to_place(self.base, self.things, self.day)}
        self.assertEqual(placing, {"new": "new since the plan",
                                   "early": "its day has come or gone, and it is not done"})
        shown = self.adjust.shown(self.base, self.things, self.day)
        self.assertEqual([one["item"] for one in shown["agent_does_so_far"]], ["x"])
        self.assertEqual(shown["might_do_so_far"], ["h"])

    def test_the_agents_work_kept_unless_placed_again(self) -> None:
        merged = self.adjust.merge(self.base, {"agent_does": [
            {"item": "new", "what": "", "start": "11:00", "end": "11:20"}]}, self.things, self.day)
        self.assertEqual([one["item"] for one in merged["agent_does"]], ["x", "new"])
        merged = self.adjust.merge(self.base, {"agent_does": [], "at_risk": [{"item": "x", "why": "late"}]},
                                   self.things, self.day)
        self.assertEqual(merged["agent_does"], [], "placed again - at risk - not carried")


class AdjustedTests(unittest.TestCase):
    """The user, 2026-11-09: "你每次重排的时候是不是可以复用之前拍的内容"; "builder会拿之前排好的，根据状态
    进行adjust" - by code: what is to be placed, and the answer laid on the plan so far."""

    def setUp(self) -> None:
        from personalized_agent.heartbeat.agenda import adjust
        self.adjust = adjust
        self.day = TOMORROW
        self.d = lambda n: (self.day + timedelta(days=n)).isoformat()
        self.row = {"day": self.day.isoformat(), "made_at": "2030-01-01T08:00:00+00:00", "plan": {
            "blocks": [{"start": "09:00", "end": "10:00", "kind": "deep", "title": "Hedge", "items": ["a"]},
                       {"start": "14:00", "end": "15:00", "kind": "deep", "title": "Report", "items": ["b"]}],
            "later": [{"item": "c", "day": self.d(1), "order": 1}, {"item": "d", "day": self.d(1), "order": 2},
                      {"item": "e", "day": self.d(5), "order": 1}]}}

    def _things(self, *more):
        said = {"a": {"who": "human", "at": "2030-01-01T07:00"}, "b": {"who": "human", "at": "2030-01-01T07:00"},
                "c": {"who": "human", "at": "2030-01-01T07:00"}, "d": {"who": "gone", "at": "2030-01-01T07:00"},
                "e": {"who": "human", "at": "2030-01-01T09:00"}}
        said.update(dict(more))
        return [{"id": ident, "title": ident, "triage": t} for ident, t in said.items()]

    def test_what_is_to_be_placed_new_sorted_again_slipped(self) -> None:
        base = self.adjust.base_of(self.row, self.day, "12:00")
        self.assertEqual([one["title"] for one in base["past_blocks"]], ["Hedge"], "before now: kept")
        things = self._things(("f", {"who": "approve"}))
        why = {one["id"]: one["why"] for one in self.adjust.to_place(base, things, self.day)}
        self.assertEqual(why["f"], "new since the plan")
        self.assertEqual(why["e"], "sorted again since the plan")
        self.assertNotIn("c", why, "where it was, unchanged: left be")
        self.assertNotIn("d", why, "done: not placed again")
        later = self.adjust.base_of(self.row, self.day + timedelta(days=2), "09:00")
        why = {one["id"]: one["why"] for one in self.adjust.to_place(later, things, self.day + timedelta(days=2))}
        self.assertIn("its day has come or gone", why["c"])
        self.assertIn("its day has come or gone", why["b"], "in a block of a day gone by")

    def test_the_answer_laid_on_the_plan_so_far(self) -> None:
        base = self.adjust.base_of(self.row, self.day, "12:00")
        things = self._things(("f", {"who": "approve"}))
        answer = {"blocks": [{"start": "13:00", "end": "14:00", "kind": "deep", "title": "Report",
                              "items": ["b"]}],
                  "later": {self.d(5): ["f", "e"]}}
        plan = self.adjust.merge(base, answer, things, self.day)
        self.assertEqual(plan["later_by_day"], {self.d(1): ["c"], self.d(5): ["f", "e"]},
                         "day 1 kept as it was, less what is done; day 5 as the builder wrote it")
        self.assertEqual([one["title"] for one in plan["blocks"]], ["Hedge", "Report"])
        self.assertTrue(plan["blocks"][0]["past"])
        self.assertEqual([one["title"] for one in self.adjust.for_check(plan)["blocks"]], ["Report"])
        self.assertEqual(plan["later"][0], {"item": "c", "day": self.d(1), "order": 1})

    def test_the_agent_working_today_leaves_the_approving_on_its_day(self) -> None:
        base = self.adjust.base_of(self.row, self.day, "08:00")
        plan = self.adjust.merge(base, {"blocks": [], "later": {},
                                        "agent_does": [{"item": "c", "start": "09:00", "end": "09:30"}]},
                                 self._things(), self.day)
        self.assertEqual(plan["later_by_day"][self.d(1)], ["c"], "approved on day 1, as it was")

    def test_the_next_hour_stands_the_rest_of_the_day_as_reviewed(self) -> None:
        """The user, 2026-11-09: "builder先给一个当天时间块，然后在现在之后1小时内的时间不变，剩余的reviewer
        会正常审核"."""
        kept = {"blocks": [{"start": "14:00", "end": "14:45", "kind": "deep", "title": "Hedge"},
                           {"start": "15:30", "end": "16:30", "kind": "deep", "title": "Report"}]}
        answer = {"blocks": [{"start": "14:00", "end": "15:00", "kind": "batch", "title": "Moved in"},
                             {"start": "16:00", "end": "17:00", "kind": "deep", "title": "Report, later"}]}
        plan = self.adjust.kept_with(kept, dict(answer), answer, "15:00")
        self.assertEqual([one["title"] for one in plan["blocks"]], ["Hedge", "Report, later"],
                         "before 15:00 as it was; after it, as the reviewer had it")
        plan = self.adjust.kept_with(kept, {"later": []}, {"later": {}}, "15:00")
        self.assertEqual([one["title"] for one in plan["blocks"]], ["Hedge", "Report"], "not given: kept")
        plan = self.adjust.kept_with(kept, dict(answer), answer, None)
        self.assertEqual([one["title"] for one in plan["blocks"]], ["Moved in", "Report, later"],
                         "another day than today: the whole of it reviewed")

    def test_a_thing_put_today_leaves_its_day_ahead(self) -> None:
        base = self.adjust.base_of(self.row, self.day, "08:00")
        plan = self.adjust.merge(base, {"blocks": [{"start": "09:00", "end": "10:00", "kind": "deep",
                                                    "items": ["c"]}], "later": {}},
                                 self._things(), self.day)
        self.assertEqual(plan["later_by_day"], {self.d(5): ["e"]})


class PlannedTests(_Base):
    """Built, checked, reviewed - and nothing done from it."""

    def test_built_until_it_holds_reviewed_as_the_user_would_and_kept(self) -> None:
        """The user, 2026-11-10: "只对agent要做的事情进行planing, 你不要告诉user在几点做她的事情" - the
        builder plans the agent's work and orders the user's things; the user's day is the code's."""
        from personalized_agent.heartbeat.agenda.plan import BUILDER_SYSTEM
        d = TOMORROW.isoformat()
        self.app.todos.add("Draft the hedge budget")
        self.app.todos.add("Reply to Ilse Marrow")
        _on_calendar(self.db, "Seed swap", datetime.combine(TOMORROW, datetime.min.time())
                     + timedelta(hours=11))
        sort = {"Draft the hedge budget": {"who": "delegable", "minutes": 120, "focus": "deep",
                                           "context": "hedge", "deadline": f"{d} 17:00"},
                "Seed swap": {"who": "human", "minutes": 60, "focus": "meeting", "at_time": True},
                "Reply to Ilse Marrow": {"who": "human", "minutes": 15, "focus": "shallow"}}

        def plan(budget_at, why="a quick one"):
            return lambda ids: {"agent_does": [{"item": ids["Draft the hedge budget"], "what": "Draft it",
                                                "start": budget_at[0], "end": budget_at[1]}],
                                "might_do": [{"item": ids["Reply to Ilse Marrow"], "why": why}],
                                "summary": "A day."}
        # IN TWO PARTS, THE NEAREST FIRST (the user, 2026-11-09: "当天的日程先排，然后剩下的再在后台慢慢
        # 排"): the day - built until it holds, no reviewer - kept at once; then the days ahead.
        models = _Models(sort=sort, builds=[plan(("16:00", "18:00")),
                                            plan(("09:00", "11:00")),
                                            lambda ids: {"later": {}},
                                            lambda ids: {"later": {}, "might_do": [
                                                {"item": ids["Reply to Ilse Marrow"],
                                                 "why": "Ilse Marrow is waiting for it"}]}],
                         reviews=[{"ok": True, "changes": []},          # the day's, once
                                  {"ok": False, "changes": [{"what": "Say that Ilse Marrow waits on the reply",
                                                             "because": ["own judgement"]}]},
                                  {"ok": True, "changes": []}])
        agenda = Agenda(self.db, lambda: models, self.app.config)
        day = {"day": TOMORROW, "begins": "09:00", "plan_from": "09:00", "ends": "18:00",
               "stretch": 90, "capacity": 360, "habit_days": 0, "usual_absence_minutes": -1}
        row = agenda.plan(day=day)
        ids = {one["title"]: one["id"] for one in agenda.things()}
        reply = ids["Reply to Ilse Marrow"]
        parts = [json.loads(one["rounds"]) for one in self.db.query(
            "SELECT rounds FROM agenda_plans ORDER BY made_at")]
        # The day's plan reviewed once, after the code's check (2026-11-12: everything from outside
        # reviewed before it acts).
        self.assertEqual([one["by"] for one in parts[0]], ["builder", "builder", "reviewer"],
                         "the day: reviewed once")
        self.assertFalse(parts[0][0]["check"]["ok"], "the agent's work past its deadline went back")
        self.assertTrue(any("after its deadline" in one for one in parts[0][0]["problems"]),
                        parts[0][0]["problems"])
        self.assertEqual([one["by"] for one in row["rounds"]], ["builder", "reviewer", "builder", "reviewer"])
        asked_parts = [json.loads(one.messages[0]["content"])["asked_part"] for one in models.asked
                       if one.task == "agenda_build"]
        self.assertEqual(asked_parts, ["today", "today", "ahead", "ahead"])
        self.assertTrue(row["checked"]["ok"])
        kept = agenda.latest(TOMORROW)
        # THE USER'S DAY BY CODE: the swap at its time, and the stretches either side free - the
        # reply suggested in each, never put at a time.
        blocks = kept["plan"]["blocks"]
        self.assertEqual([(one["start"], one["end"], one["kind"]) for one in blocks],
                         [("09:00", "11:00", "free"), ("11:00", "12:00", "meeting"),
                          ("12:00", "18:00", "free")])
        self.assertEqual(blocks[2]["might_do"], [{"item": reply, "why": "Ilse Marrow is waiting for it"}],
                         "the reviewer's change made in the second part")
        self.assertEqual(kept["plan"]["might_do"][0]["why"], "Ilse Marrow is waiting for it")
        self.assertEqual([(one["start"], one["end"]) for one in kept["plan"]["agent_does"]],
                         [("09:00", "11:00")], "the agent's work kept from the day's part")
        self.assertIn("THE USER'S THINGS ARE NOT PLANNED AT A TIME", BUILDER_SYSTEM)
        # PLANNED AGAIN: adjusted from this one - told the plan so far and what is to be placed;
        # the user's new thing goes in their order, given no day.
        self.app.todos.add("Prune the pear tree")
        models.builds += [lambda ids: dict(plan(("09:00", "11:00"))(ids), might_do=[
                              {"item": ids["Reply to Ilse Marrow"], "why": "Ilse Marrow is waiting for it"},
                              {"item": ids["Prune the pear tree"], "why": "before the frost"}]),
                          lambda ids: {"later": {}}]
        models.reviews += [{"ok": True}, {"ok": True}]          # the day's part, and the days ahead
        again = agenda.plan(day=day)
        built = json.loads([one for one in models.asked if one.task == "agenda_build"][4]
                           .messages[0]["content"])
        self.assertIn("the_plan_so_far", built)
        pear = {one["title"]: one["id"] for one in agenda.things()}["Prune the pear tree"]
        why = {one["id"]: one["why"] for one in built["to_place"]}
        self.assertEqual(why.get(pear), "new since the plan")
        self.assertNotIn(reply, why, "suggested in the plan so far: placed")
        self.assertTrue(again["checked"]["ok"], again["checked"]["problems"])
        self.assertEqual(again["plan"]["later"], [], "nothing of the user's on a day ahead")
        free = [one["items"] for one in again["plan"]["blocks"] if one["kind"] == "free"]
        self.assertEqual(free, [[reply, pear], [reply, pear]], "suggested in each free stretch")
        # The builder is given the projects, and no time the user's things took.
        built = json.loads(next(one for one in models.asked if one.task == "agenda_build")
                           .messages[0]["content"])
        self.assertIn("projects", built)
        self.assertNotIn("time_taken_by_kind", built)
        self.assertTrue(kept["review"]["ok"])
        # PHASE 1: nothing done from it - the two and the one added since, all still open.
        self.assertEqual(len(self.app.todos.open()), 3)
        self.assertEqual(self.db.query_one("SELECT COUNT(*) AS n FROM work_drafts")["n"], 0)

    def test_sorted_in_calls_of_twelve_until_none_is_left_and_off_the_heartbeat(self) -> None:
        """The user, 2026-11-09: "你现在还有这么多not sorted" - and a side note: ten calls in a row
        held the heartbeat's reminders a quarter of an hour."""
        import threading
        for n in range(30):
            self.app.todos.add(f"Label seed packet {n}")
        gathered.collect(self.db)
        dropped = {"Label seed packet 3"}            # left out of its call's answer once

        class Slow(_Models):
            def __init__(self):
                super().__init__()
                self.gate = threading.Event()

            def invoke(self, request, role="primary"):
                if request.task == "agenda_triage":
                    self.gate.wait(10)
                    said = json.loads(request.messages[0]["content"])
                    things = [one for one in said["things"] if one["title"] not in dropped]
                    if len(said["things"]) > 1 and len(things) < len(said["things"]):
                        dropped.clear()
                    self.asked.append(request)
                    return SimpleNamespace(text=json.dumps({"things": [
                        {"id": one["id"], "who": "human", "minutes": 5} for one in things]}),
                        tool_calls=[])
                return super().invoke(request, role)
        models = Slow()
        agenda = Agenda(self.db, lambda: models, self.app.config)
        self.db.execute("INSERT INTO agenda_plans (id, day, made_at) VALUES ('p', ?, ?)",
                        (agenda.day_of()["day"].isoformat(), datetime.now(timezone.utc).isoformat()))
        report = agenda.step()                       # back at once, the model still thinking
        self.assertTrue(report["judging"])
        self.assertTrue(agenda.planning)
        models.gate.set()
        agenda._planning.join(30)
        calls = [len(json.loads(one.messages[0]["content"])["things"]) for one in models.asked
                 if one.task == "agenda_triage"]
        self.assertTrue(all(n <= triaging.MOST_AT_ONCE for n in calls), calls)
        self.assertEqual(triaging.wanting(gathered.open_items(self.db)), [], "none left")

    def test_the_page_asking_sets_it_going_and_says_what_it_does(self) -> None:
        """The user, 2026-11-09: after a restart nothing went until a heartbeat came; "如果是正在
        分拣，按钮应该变成planning并且无法再点击"."""
        import threading
        from personalized_agent.api.routes import build_routes
        self.app.todos.add("Label the seed drawer")
        gate = threading.Event()

        class Held(_Models):
            def invoke(self, request, role="primary"):
                if request.task == "agenda_triage":
                    gate.wait(10)
                return super().invoke(request, role)
        models = Held(builds=[lambda ids: {"blocks": [
            {"start": "13:00", "end": "13:30", "kind": "batch", "title": "Small things",
             "items": [ids["Label the seed drawer"]]}], "summary": ""}], reviews=[{"ok": True}])
        agenda = Agenda(self.db, lambda: models, self.app.config)
        self.app.agenda = agenda
        late = datetime.combine(TOMORROW, datetime.min.time()) + timedelta(hours=12)  # not today's clock
        agenda.day_of = lambda now=None, real=agenda.day_of: real(late)
        said = build_routes(self.app)[("GET", "/api/agenda")](SimpleNamespace())
        self.assertTrue(said["planning"], "set going by the page's asking")
        self.assertEqual(said["doing"], "sorting")
        self.assertFalse(agenda.plan_soon(), "the button can start nothing more meanwhile")
        gate.set()
        agenda._planning.join(30)
        self.assertIsNotNone(agenda.latest(late.date()))
        self.assertEqual(agenda.kick(), {}, "planned: nothing more to do")

    def test_planned_again_when_its_things_were_sorted_again(self) -> None:
        """The user, 2026-11-09: "现在没看到3分类呀" - the plan shown was made before the three kinds."""
        agenda = Agenda(self.db, lambda: None, self.app.config)
        day = agenda.day_of()["day"].isoformat()
        stamp = datetime.now(timezone.utc).isoformat()
        self.db.execute("INSERT INTO agenda_plans (id, day, made_at, plan) VALUES ('old', ?, ?, ?)",
                        (day, stamp, json.dumps({"blocks": [], "_frame": {}})))
        self.assertEqual(agenda.stale(agenda.latest()), "the things were sorted another way")
        self.db.execute("UPDATE agenda_plans SET plan = ? WHERE id = 'old'",
                        (json.dumps({"blocks": [], "_frame": {"triage_version": triaging.VERSION}}),))
        self.assertEqual(agenda.stale(agenda.latest()), "")
        for n in range(5):
            self.app.todos.add(f"Water bed {n}")
        gathered.collect(self.db)
        for one in gathered.open_items(self.db):
            self.db.execute("UPDATE agenda_items SET triage = ?, triaged_for = ? WHERE id = ?",
                            (json.dumps({"who": "human", "at": "9999"}), triaging.judged_for(one),
                             one["id"]))
        self.assertIn("5 things sorted again", agenda.stale(agenda.latest()))

    def test_a_failed_plan_is_not_tried_again_every_minute(self) -> None:
        agenda = Agenda(self.db, lambda: None, self.app.config)
        late = datetime.combine(date.today(), datetime.min.time()) + timedelta(hours=12)
        agenda.plan = lambda **_kw: (_ for _ in ()).throw(RuntimeError("no model"))
        self.assertTrue(agenda.kick(late)["planning"])
        agenda._planning.join(30)
        self.assertEqual(agenda.kick(late), {}, "not again within half an hour")

    def test_planned_once_a_day_on_a_thread_of_its_own(self) -> None:
        models = _Models(builds=[lambda ids: {"blocks": [], "summary": ""}], reviews=[{"ok": True}])
        agenda = Agenda(self.db, lambda: models, self.app.config)
        late = datetime.combine(date.today(), datetime.min.time()) + timedelta(hours=12)
        report = agenda.step(now=late)
        self.assertTrue(report["planning"])
        agenda._planning.join(30)
        self.assertIsNotNone(agenda.latest(agenda.day_of(late)["day"]))
        self.assertNotIn("planning", agenda.step(now=late), "planned already: not again")
        import threading
        held = threading.Event()
        agenda.plan = lambda **_kw: held.wait(10)
        agenda.judge = lambda **_kw: {}
        self.assertTrue(agenda.plan_soon())
        self.assertFalse(agenda.plan_soon(), "one at a time")
        held.set()
        agenda._planning.join(30)


if __name__ == "__main__":
    unittest.main()
