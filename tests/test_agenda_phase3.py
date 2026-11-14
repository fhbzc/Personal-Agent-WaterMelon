"""THE DAY'S PLAN, PHASE 3 (the user, 2026-11-09: "那你继续做（注意，现在还是所有send/write都要approve,
之后才会放开)"; "这类task你要让agent写清楚如果他自己做他会怎么做（不approve的情况下)"): the agent's part
set going when its time comes, saying what it would do on its own; the user's approving counted by
kind of work; what the plan found gone cleared away, each undone for a week.

Everyone and everything here is made up."""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.heartbeat.agenda import items as gathered
from personalized_agent.heartbeat.agenda import triage as triaging
from personalized_agent.heartbeat.agenda.execute import APPROVE_FIRST, Cleaner, Executor
from personalized_agent.heartbeat.agenda.plan import Agenda


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.db = self.app.db

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _sorted(self, kinds):
        for title in kinds:
            self.app.todos.add(title)
        gathered.collect(self.db)
        for one in gathered.open_items(self.db):
            who = kinds[one["title"]]
            self.db.execute("UPDATE agenda_items SET triage = ?, triaged_for = ? WHERE id = ?",
                            (json.dumps({"who": who, "minutes": 10, "kind": "reply to a club",
                                         "agent_part": "Draft the reply", "rule": triaging.APPROVE_RULE}),
                             triaging.judged_for(one), one["id"]))
        return {one["title"]: one for one in gathered.open_items(self.db)}

    def _plan(self, plan):
        self.db.execute("INSERT INTO agenda_plans (id, day, made_at, plan) VALUES ('p1', ?, ?, ?)",
                        (date.today().isoformat(), datetime.now(timezone.utc).isoformat(), json.dumps(plan)))


class TheSameThingOnceTests(_Base):
    """2026-11-13: "你就合成为一条，这样一次性关掉" - the same thing on the plate twice is one thing."""

    def test_twins_shown_once_and_closed_together(self) -> None:
        from personalized_agent.heartbeat.agenda.act import twins
        rows = self._sorted({"Read the bee survey": "human", "Read the bee survey ": "human",
                             "Write the orchard report": "human", "Report on the orchard": "human"})
        survey = [row["id"] for row in self.db.query(
            "SELECT id FROM agenda_items WHERE status = 'open' AND trim(title) = 'Read the bee survey'")]
        self.assertEqual(len(survey), 2)
        report, alike = rows["Write the orchard report"]["id"], rows["Report on the orchard"]["id"]
        said = json.loads(self.db.query_one("SELECT triage FROM agenda_items WHERE id = ?", (alike,))["triage"])
        self.db.execute("UPDATE agenda_items SET triage = ? WHERE id = ?",
                        (json.dumps(dict(said, same_as=report)), alike))
        grouped = twins(self.db)
        self.assertEqual(grouped[survey[0]], set(survey), "word for word alike")
        self.assertEqual(grouped[report], {report, alike}, "sorted as the same")
        # In the report, once.
        listed = [one["title"].strip() for one in self.app.reports.to_do()]
        self.assertEqual(listed.count("Read the bee survey"), 1)
        self.assertEqual(len([one for one in listed if "orchard" in one]), 1)
        # Closed together - their to-dos too.
        self.app.agenda_feedback.apply(alike, "done", by="user")
        left = {row["id"] for row in self.db.query("SELECT id FROM agenda_items WHERE status = 'open'")}
        self.assertNotIn(report, left, "its twin closed with it")
        self.assertTrue(set(survey) <= left, "others untouched")
        self.assertEqual(len([one for one in self.app.todos.open() if "orchard" in one["text"].lower()]), 0)

class TheAgentsPartTests(_Base):

    def test_set_going_when_its_time_comes_once_saying_what_it_would_do(self) -> None:
        rows = self._sorted({"Reply to the seed club": "delegable", "Write the orchard report": "human"})
        reply, report = rows["Reply to the seed club"]["id"], rows["Write the orchard report"]["id"]
        self._plan({"blocks": [], "agent_does": [
            {"item": reply, "what": "Draft the reply to the club", "start": "09:00", "end": "09:20"},
            {"item": report, "what": "Collect last year's figures", "start": "10:00", "end": "10:30"}]})
        handed = []
        agenda = Agenda(self.db, lambda: None, self.app.config)
        executor = Executor(agenda, lambda words, title, folders: handed.append((words, title))
                            or {"assigned": f"draft_{len(handed)}"})
        at = lambda hhmm: datetime.combine(date.today(), datetime.min.time()) + timedelta(
            hours=int(hhmm[:2]), minutes=int(hhmm[3:]))
        self.assertEqual(executor.start_due(at("08:59")), [], "not before its time")
        self.assertEqual(executor.start_due(at("09:05")), [reply])
        self.assertEqual(executor.start_due(at("09:10")), [], "once")
        self.assertIn("WHAT I WOULD DO ON MY OWN", handed[0][0])
        # On its own, all of it (2026-11-12): only other channels to people wait for the user.
        self.assertIn("EVERY OTHER CHANNEL TO PEOPLE", handed[0][0])
        self.assertIn("NEEDS THE USER'S APPROVAL", handed[0][0])
        self.assertIn("Your part: Draft the reply to the club", handed[0][0])
        executor.start_due(at("10:01"))
        self.assertIn("THIS IS THE USER'S OWN WORK", handed[1][0], "a help with theirs, nothing more")
        self.assertEqual(executor.started_for(reply), "draft_1")

    def test_its_part_now_as_the_user_asks_whatever_time_it_was_given(self) -> None:
        """2026-11-13: "人可以告诉他马上做，做好会归到需要的东西整理好了的部分" - the report's "Prepare it now"."""
        rows = self._sorted({"Write the orchard report": "human", "Read the bee survey": "human"})
        report, survey = rows["Write the orchard report"]["id"], rows["Read the bee survey"]["id"]
        self._plan({"blocks": [], "agent_does": [
            {"item": report, "what": "Collect last year's figures", "start": "16:00", "end": "16:30"}]})
        handed = []
        agenda = Agenda(self.db, lambda: None, self.app.config)
        executor = Executor(agenda, lambda words, title, folders: handed.append(title)
                            or {"assigned": f"draft_{len(handed)}"})
        at = datetime.combine(date.today(), datetime.min.time()) + timedelta(hours=9)
        self.assertEqual(executor.start_due(at), [], "not before its time")
        self.db.execute("INSERT INTO kv (key, value) VALUES (?, 'x'), (?, 'x')",
                        ("agenda.now." + report, "agenda.now." + survey))
        started = executor.start_due(at)
        self.assertEqual(sorted(started), sorted([report, survey]),
                         "asked for now: started now - one not in today's plan too")
        self.assertEqual(executor.start_due(at), [], "once")

    def test_a_little_at_a_time_the_soonest_needed_first(self) -> None:
        """The user, 2026-11-09: "这个是一个可以慢慢做的过程，根据日程慢慢做，在用户需要审核的时候之前做好就行"
        - a restart set every start gone by going at once."""
        titles = {f"Reply to club {n}": "delegable" for n in range(5)}
        rows = self._sorted(titles)
        ids = {title: rows[title]["id"] for title in titles}
        # Approved at 15:00, 14:00, 16:00 and 12:00 (gone by at 13:00); the fifth needed at 14:20.
        blocks = [{"start": "15:00", "end": "15:30", "kind": "batch", "items": [ids["Reply to club 0"]]},
                  {"start": "14:00", "end": "14:30", "kind": "batch", "items": [ids["Reply to club 1"]]},
                  {"start": "16:00", "end": "16:30", "kind": "batch", "items": [ids["Reply to club 2"]]},
                  {"start": "12:00", "end": "12:30", "kind": "batch", "items": [ids["Reply to club 3"]]},
                  {"start": "14:20", "end": "14:40", "kind": "batch", "items": [ids["Reply to club 4"]]}]
        agent_does = [{"item": ids[f"Reply to club {n}"], "start": "08:00", "end": "08:30"} for n in range(4)]
        agent_does.append({"item": ids["Reply to club 4"], "start": "14:00", "end": "14:15"})
        self._plan({"blocks": blocks, "agent_does": agent_does})
        handed, still_going = [], []
        agenda = Agenda(self.db, lambda: None, self.app.config)
        executor = Executor(agenda, lambda words, title, folders: handed.append(title) or {"assigned": title},
                            going=lambda drafts: len([one for one in drafts if one in still_going]))
        now = datetime.combine(date.today(), datetime.min.time()) + timedelta(hours=13)
        executor.start_due(now)
        self.assertEqual(handed, ["Reply to club 1", "Reply to club 4"],
                         "two at a time; the soonest needed first - the fifth early, needed within 90 min")
        still_going += handed
        self.assertEqual(executor.start_due(now), [], "two going: none more")
        still_going.remove("Reply to club 1")
        executor.start_due(now)
        self.assertEqual(handed[-1], "Reply to club 0")
        self.assertNotIn("Reply to club 3", handed, "its time of need gone by: placed again first")

    def test_the_users_approving_counted_by_kind_no_offer_to_hand_over_yet(self) -> None:
        from personalized_agent.heartbeat.agenda import execute
        rows = self._sorted({"Reply to the seed club": "delegable"})
        executor = Executor(Agenda(self.db, lambda: None, self.app.config), lambda *a: {})
        ident = rows["Reply to the seed club"]["id"]
        executor.record_approval(ident, "as_it_was")
        executor.record_approval(ident, "as_it_was")
        said = executor.record_approval(ident, "changed")
        mode = triaging.modes(self.db)["reply to a club"]
        self.assertEqual((mode["approved"], mode["changed"]), (2, 1))
        self.assertIsNone(said["offer"])
        self.assertFalse(execute.HAND_OVER_OFFERS, "every send and write is approved, for now")
        self.assertIn("THIS NEEDS THE USER'S APPROVAL BEFORE ANY ACTION OUTSIDE YOUR OWN WORK FOLDER",
                      APPROVE_FIRST)
        # ONLY IN ITS OWN FOLDER (the user, 2026-11-10, after a plan's "deploy" ran by itself): a push
        # or a deploy is an action outside it like a message is.
        for words in ("push, deploy, publish", "outside your work folder", "every command as you "
                      "would run it"):
            self.assertIn(words, " ".join(APPROVE_FIRST.split()))
        from personalized_agent.heartbeat.agenda.execute import HELP_ONLY
        self.assertIn("anywhere else, read only", " ".join(HELP_ONLY.split()))

    def test_approved_from_the_page_is_done(self) -> None:
        from personalized_agent.api.routes import build_routes
        rows = self._sorted({"Reply to the seed club": "delegable"})
        ident = rows["Reply to the seed club"]["id"]
        self.app.agenda.plan_again = lambda why: None
        build_routes(self.app)[("POST", "/api/agenda/approval")](SimpleNamespace(
            require=lambda name: {"id": ident, "how": "as_it_was"}[name], get=lambda n, d=None: d))
        self.assertEqual(self.app.todos.open(), [], "the to-do done, as on the list")


class ClearedAwayTests(_Base):

    def test_what_was_sorted_gone_cleared_and_undone(self) -> None:
        rows = self._sorted({"Return the borrowed shears": "gone", "Sow the beans": "gone"})
        shears, beans = rows["Return the borrowed shears"], rows["Sow the beans"]
        # The user's word on one: never the agent's to clear.
        said = json.loads(self.db.query_one("SELECT triage FROM agenda_items WHERE id = ?", (beans["id"],))["triage"])
        said["set_by_user"] = "gone"
        self.db.execute("UPDATE agenda_items SET triage = ? WHERE id = ?", (json.dumps(said), beans["id"]))
        cleaner = Cleaner(Agenda(self.db, lambda: None, self.app.config), todos=self.app.todos)
        cleared = cleaner.clear({"clean_up": [{"item": shears["id"], "why": "Returned on Tuesday."},
                                              {"item": beans["id"], "why": "?"}]})
        self.assertEqual(cleared, [shears["id"]])
        self.assertEqual([one["text"] for one in self.app.todos.open()], ["Sow the beans"])
        evidence = self.db.query_one("SELECT COUNT(*) AS n FROM lesson_evidence")["n"]
        self.assertEqual(evidence, 0, "the agent's doing is no lesson")
        recent = cleaner.recent()
        self.assertEqual((recent[0]["title"], recent[0]["why"]), ("Return the borrowed shears", "Returned on Tuesday."))
        self.assertEqual(cleaner.undo(recent[0]["id"])["title"], "Return the borrowed shears")
        self.assertEqual(len(self.app.todos.open()), 2, "back on the list")
        self.assertEqual(cleaner.recent(), [])
        back = next(one for one in gathered.open_items(self.db) if one["id"] == shears["id"])
        self.assertEqual(back["triage"]["set_by_user"], "human", "kept: the user said it is not gone")

    def test_a_delegated_works_entry_hidden_not_its_project_and_back(self) -> None:
        drafts = self.app.drafts
        draft = drafts.create(text="Sort the garden photos", source="delegate", collect=False)
        cleaner = Cleaner(Agenda(self.db, lambda: None, self.app.config), drafts=drafts)
        self.db.execute("INSERT INTO agenda_items (id, source, ref_id, title, status, triage, created_at, updated_at) "
                        "VALUES ('agenda_w', 'work', ?, 'Sort the garden photos', 'open', ?, ?, ?)",
                        (draft["id"], json.dumps({"who": "gone"}), *(datetime.now(timezone.utc).isoformat(),) * 2))
        self.assertEqual(cleaner.clear({"clean_up": [{"item": "agenda_w", "why": "Done last week."}]}), ["agenda_w"])
        self.assertEqual(drafts.get(draft["id"])["options"].get("removed_by"), "agent")
        cleaner.undo(cleaner.recent()[0]["id"])
        self.assertNotIn("removed", drafts.get(draft["id"])["options"])

    def test_cleared_when_a_plan_is_kept(self) -> None:
        self.assertIs(self.app.agenda.cleaner, self.app.agenda_cleaner)
        self.assertIs(self.app.agenda.executor, self.app.agenda_executor)


if __name__ == "__main__":
    unittest.main()
