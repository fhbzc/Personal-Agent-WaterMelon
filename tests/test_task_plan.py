"""WHAT IS LEFT TO DO, AND WHAT THE USER MERGED (the user, 2026-11-04). Every step moves on proof, the
user's own stay, and what was merged is not pulled apart. Everything here is made up."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.heartbeat.states import task_plan as plan
from personalized_agent.heartbeat.states import task_projects as tp
from personalized_agent.heartbeat.states import todo as _todo
from personalized_agent.heartbeat.states.project_round import ProjectRound, project_view
from personalized_agent.heartbeat.states.tasks import Kept, Task


class ProofTests(unittest.TestCase):
    READ = {"state_1": "send the tide tables to the harbour by friday"}

    def test_words_in_a_record_read_prove_it(self) -> None:
        kept, wrong = plan.proved(self.READ, [{"id": "state_1", "quote": "Send  the tide\ntables"}])
        self.assertEqual(wrong, "")
        self.assertEqual(kept[0]["id"], "state_1")

    def test_what_is_not_proof(self) -> None:
        for evidence in (None, [], "state_1",
                         [{"id": "state_2", "quote": "send the tide tables"}],
                         [{"id": "state_1", "quote": "the"}],
                         [{"id": "state_1", "quote": "send the tide charts"}]):
            kept, wrong = plan.proved(self.READ, evidence)
            self.assertEqual(kept, [], evidence)
            self.assertTrue(wrong, evidence)


class _Call(SimpleNamespace):
    pass


class _Turns:
    """A model that says what it was told to, turn by turn."""

    def __init__(self, turns):
        self.turns, self.asked = list(turns), []

    def invoke(self, request, role=None):
        self.asked.append(request)
        calls = self.turns.pop(0) if self.turns else []
        return SimpleNamespace(tool_calls=[_Call(id=f"c{n}", name=name, arguments=args)
                                           for n, (name, args) in enumerate(calls)],
                               text="", raw_content=[], usage=None)


class _WithWork(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.hb = self.app.state_heartbeat
        self.kept = Kept(self.app.db, "live")
        self.tasks = tp.RunTasks(self.app.db, "live")
        self.hb.open_records = lambda ids: {one: {"text": self.records[one]} if one in self.records
                                            else {"error": "no such"} for one in ids}
        self.records = {"state_1": "The harbour master asked for the tide tables by Friday.",
                        "state_2": "Sent the tide tables to the harbour master this morning."}
        self.cancelled = []
        self.hb.cancel_reminder = lambda ident, why: self.cancelled.append(ident)
        self._project("proj_a", "Tide tables", 60.0, ("t1", "t2"))

    def _project(self, ident, title, minutes, tasks, **said):
        row = Task(id=ident, number=abs(hash(ident)) % 1000, seat=1)
        row.profile = {"scheme": tp.SCHEME, "last_seen": "2026-11-04T10:00:00+00:00"}
        row.described = {"title": title, "line": "A study", "summary": "Plotting", "minutes": minutes,
                         "kind": "work", **said}
        self.kept.save([row])
        self.tasks.add([{"id": one, "title": f"Task {one}", "title_display": f"Task {one}",
                         "summary": f"Did {one}", "minutes": minutes / 2, "states": ["state_1"],
                         "ends_at": "2026-11-03T10:00:00+00:00", "project_id": ident} for one in tasks])

    def row(self, ident="proj_a"):
        return self.kept.by_ids([ident])[0]

    def steps(self, ident="proj_a"):
        return _todo.items(self.row(ident).described)

    def _plan(self, turns, ident="proj_a"):
        self.hb.models = _Turns(turns)
        with mock.patch.object(self.hb, "_going", return_value=True):
            round_ = ProjectRound(self.hb, self.hb.where("live"), {})
            self.assertTrue(plan.Planner(self.hb, round_).plan(self.row(ident)))
        return round_.report

    def _lessons(self, action):
        return self.app.db.query("SELECT summary FROM lesson_evidence WHERE action = ?", (action,))


PROOF = [{"id": "state_1", "quote": "asked for the tide tables by Friday"}]
DONE = [{"id": "state_2", "quote": "Sent the tide tables to the harbour master"}]


class TheListTests(_WithWork):

    def test_a_step_added_unread_is_refused_and_read_it_stands(self) -> None:
        add = ("add_step", {"text": "Send the tide tables", "why": "It was asked for", "evidence": PROOF})
        report = self._plan([[add], [("open_by_id", {"ids": ["state_1"]})], [add]])
        steps = self.steps()
        self.assertEqual([one["text"] for one in steps], ["Send the tide tables"])
        self.assertEqual(steps[0]["evidence"], PROOF)
        self.assertEqual(steps[0]["by"], "agent")
        self.assertEqual(len(report["plans"][0]["did"]), 1)
        self.assertEqual(report["plans"][0]["turns"], 4, "as many looks as it took")
        said = self.row().described
        self.assertTrue(said["todo_at"])
        self.assertEqual(said["todo_log"][0]["evidence"], PROOF)

    def test_each_tool_refused_without_proof_changes_nothing(self) -> None:
        self._plan([[("add_step", {"text": "Guess at something", "why": "A good idea", "evidence": []})]])
        self.assertEqual(self.steps(), [])

    def _with_a_step(self, **more):
        self._plan([[("open_by_id", {"ids": ["state_1", "state_2"]})],
                    [("add_step", {"text": "Send the tide tables", "why": "Asked for", "evidence": PROOF})]])
        step = self.steps()[0]
        if more:
            def how(said):
                steps = _todo.items(said)
                steps[0].update(more)
                return {"todo": steps}
            _todo.change(self.app.db, "state_tasks", "live", "proj_a", how)
        return step["id"]

    def test_done_on_proof_it_is_ticked_and_its_reminder_goes(self) -> None:
        step = self._with_a_step(reminder="task_9", reminder_at="2026-11-06 09:00")
        self._plan([[("open_by_id", {"ids": ["state_2"]})],
                    [("finish_step", {"step": step, "why": "It was sent", "evidence": DONE})]])
        one = self.steps()[0]
        self.assertTrue(one["done"])
        self.assertEqual(one["done_by"], "agent")
        self.assertEqual(self.cancelled, ["task_9"])

    def test_the_users_own_stay(self) -> None:
        step = self._with_a_step(by="user")
        self._plan([[("open_by_id", {"ids": ["state_2"]})],
                    [("remove_step", {"step": step, "why": "Done with", "evidence": DONE})]])
        self.assertEqual(len(self.steps()), 1)

    def test_a_tick_the_user_took_back_is_not_put_back(self) -> None:
        step = self._with_a_step()
        self.hb.tick_step("proj_a", step, True)
        self.hb.tick_step("proj_a", step, False)
        self._plan([[("open_by_id", {"ids": ["state_2"]})],
                    [("finish_step", {"step": step, "why": "It was sent", "evidence": DONE})]])
        self.assertFalse(self.steps()[0].get("done"))

    def test_a_reminder_goes_on_the_calendar_on_proof(self) -> None:
        step = self._with_a_step()
        self.hb.schedule_reminder = lambda args: {"task_id": "task_7", "seen": args}
        self._plan([[("open_by_id", {"ids": ["state_1"]})],
                    [("set_reminder", {"step": step, "at_local": "2026-11-06 09:00",
                                       "zone": "Europe/Lisbon", "why": "It is due", "evidence": PROOF})]])
        self.assertEqual(self.steps()[0]["reminder"], "task_7")

    def test_a_line_on_the_page_for_each_step(self) -> None:
        self._with_a_step()
        said = project_view(self.row())
        self.assertEqual(said["steps"][0]["text"], "Send the tide tables")

    def test_the_users_tick_is_a_lesson_and_takes_the_reminder(self) -> None:
        step = self._with_a_step(reminder="task_9")
        self.hb.tick_step("proj_a", step, True)
        self.assertTrue(self.steps()[0]["done"])
        self.assertEqual(self.steps()[0]["done_by"], "user")
        self.assertEqual(self.cancelled, ["task_9"])
        self.assertIn("Send the tide tables", self._lessons("ticked_step")[0]["summary"])


class WhenTests(_WithWork):

    def test_the_last_update_is_told_beside_the_next(self) -> None:
        said = self.app.state_heartbeat.when_next()
        self.assertEqual(said["last_at"], "", "none yet")
        self.app.config.set("state_round_completed_at", "2026-11-04T10:00:00+00:00", internal=True)
        self.assertEqual(self.hb.when_next()["last_at"], "2026-11-04T10:00:00+00:00")


class ThePanelsSizeTests(_WithWork):

    def _routes(self):
        from personalized_agent.api.routes import build_routes

        class Ask(dict):
            def require(self, key):
                return self[key]
        return build_routes(self.app), Ask

    def test_the_page_zooms_as_a_page_with_nothing_of_ours_between(self) -> None:
        routes, _Ask = self._routes()
        self.assertNotIn(("POST", "/api/panel_zooms"), routes)
        from support import ROOT
        source = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("panelZooms", source)
        self.assertNotIn("style.zoom", source)

    def test_no_page_of_settings_shows_a_size_of_words(self) -> None:
        from personalized_agent.common.config import schema
        for key in ("font_size", "popup_font_size"):
            self.assertTrue(schema.SCHEMA_BY_KEY[key].hidden, key)


class WhichTests(_WithWork):

    def test_active_work_and_leisure_that_changed_and_no_other(self) -> None:
        made = []
        for ident, said, more in (
                ("new", {}, {}), ("fun", {"kind": "leisure"}, {}),
                ("seen", {"todo_at": "2026-11-05T00:00:00+00:00"}, {}),
                ("grown", {"todo_at": "2026-11-01T00:00:00+00:00"}, {}),
                ("asleep", {}, {"archived_at": "x"}), ("gone", {"deleted_by_user": True}, {})):
            row = Task(id=ident, number=1, seat=1)
            row.described = said
            row.profile = {"last_seen": "2026-11-04T10:00:00+00:00"}
            for key, value in more.items():
                setattr(row, key, value)
            made.append(row)
        self.assertEqual({one.id for one in plan.Planner.due(made)}, {"new", "fun", "grown"})

    def test_what_the_user_merged_goes_first_and_only_so_many(self) -> None:
        made = []
        for n in range(plan.MOST_PLANNED + 3):
            row = Task(id=f"p{n}", number=n, seat=1)
            row.described = {"plan_due": True} if n == 8 else {}
            row.profile = {"last_seen": f"2026-11-0{n % 9 + 1}T10:00:00+00:00"}
            made.append(row)
        due = plan.Planner.due(made)
        self.assertEqual(len(due), plan.MOST_PLANNED)
        self.assertEqual(due[0].id, "p8")


class MergedTests(_WithWork):

    def setUp(self) -> None:
        super().setUp()
        self._project("proj_b", "Harbour report", 20.0, ("t3",))
        self._project("proj_c", "Tide gauge", 10.0, ("t4",))

    def test_they_become_the_one_worked_on_longest(self) -> None:
        said = self.hb.merge_projects(["proj_c", "proj_a", "proj_b"])
        self.assertEqual(said["id"], "proj_a")
        self.assertEqual({one["id"] for one in self.tasks.of_project("proj_a", limit=20)},
                         {"t1", "t2", "t3", "t4"})
        self.assertEqual(self.tasks.of_project("proj_b", limit=20), [])
        target, gone = self.row(), self.row("proj_b")
        self.assertTrue(target.described["merged_by_user"])
        self.assertTrue(target.described["plan_due"])
        self.assertIn("Harbour report", target.described["names_before"])
        self.assertTrue(gone.retired_at)
        self.assertEqual(gone.born_of["joined_into"], "proj_a")

    def test_the_one_dropped_on_is_the_one_that_stays(self) -> None:
        said = self.hb.merge_projects(["proj_a"], into="proj_c")
        self.assertEqual(said["id"], "proj_c", "though the other was worked on longer")
        self.assertEqual({one["id"] for one in self.tasks.of_project("proj_c", limit=20)},
                         {"t1", "t2", "t4"})
        self.assertTrue(self.row("proj_a").retired_at)

    def test_their_steps_and_what_was_linked_go_with_them(self) -> None:
        for ident, text in (("proj_a", "Send it"), ("proj_b", "Send it"), ("proj_b", "Check the gauge")):
            def how(said, text=text):
                return {"todo": _todo.items(said) + [{"id": _todo._an_id(), "text": text}]}
            _todo.change(self.app.db, "state_tasks", "live", ident, how)
        self.app.item_links.put("todo", "todo_1", [], ["proj_b"])
        self.hb.merge_projects(["proj_a", "proj_b"])
        self.assertEqual([one["text"] for one in self.steps()], ["Send it", "Check the gauge"])
        self.assertEqual({one["project_id"] for one in self.app.item_links.of("todo", "todo_1")},
                         {"proj_a"})

    def test_it_is_a_lesson_for_the_runs_after(self) -> None:
        self.hb.merge_projects(["proj_a", "proj_b"])
        said = self._lessons("merged_projects")[0]["summary"]
        self.assertIn("Tide tables", said)
        self.assertIn("Harbour report", said)

    def test_one_alone_or_one_unknown_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.hb.merge_projects(["proj_a", "proj_a"])
        with self.assertRaises(KeyError):
            self.hb.merge_projects(["proj_a", "nothing"])
        self.assertFalse(self.row().described.get("merged_by_user"))

    def test_the_grouping_cannot_pull_it_apart(self) -> None:
        kept = tp.keep_together([["t1", "t9"], ["t2", "t3"], ["t4"]],
                                {"t1": "p", "t2": "p", "t3": "p", "t4": "q", "t9": "r"}, ["p"])
        home = next(one for one in kept if "t1" in one)
        self.assertEqual({"t1", "t2", "t3"} & set(home), {"t1", "t2", "t3"})
        self.assertIn(["t4"], kept)
        self.assertEqual(sum(one.count("t2") for one in kept), 1)

    def test_new_work_may_still_join_it(self) -> None:
        kept = tp.keep_together([["t1", "t2", "new"]], {"t1": "p", "t2": "p", "new": ""}, ["p"])
        self.assertEqual(sorted(kept[0]), ["new", "t1", "t2"])

    def test_saved_it_is_gone_through_though_there_is_little_to_read(self) -> None:
        """A round stops at fewer than two states: the merge went nowhere, found with a real run."""
        self.hb.merge_projects(["proj_a", "proj_b"])
        self.hb.models = _Turns([])
        with mock.patch.object(ProjectRound, "_name_what_is_new"),                 mock.patch.object(ProjectRound, "_link_what_is_open"),                 mock.patch.object(self.hb, "_going", return_value=True):
            said = self.hb.settle_merge()
        self.assertTrue(said["round_complete"])
        self.assertFalse(self.row().described.get("plan_due"))

    def test_saved_with_nothing_running_it_is_merged_and_updated_at_once(self) -> None:
        with mock.patch.object(self.hb, "after_merge") as updated:
            said = self.hb.merge_and_update([{"into": "proj_a", "ids": ["proj_b"]}])
        self.assertEqual(said["merged"][0]["id"], "proj_a")
        updated.assert_called_once()

    def test_saved_with_a_round_running_it_stops_and_then_merges_and_updates(self) -> None:
        import threading
        running = self.hb._running_in("live")
        running.acquire()
        done = threading.Event()
        with mock.patch.object(self.hb, "after_merge", side_effect=lambda: done.set()):
            said = self.hb.merge_and_update([{"into": "proj_a", "ids": ["proj_b"]}])
            self.assertEqual(said, {"stopping": True})
            where = self.hb.where("live")
            self.assertFalse(self.hb._going(where), "the round is told to stop")
            self.assertIn("merged", self.hb._given_up(where))
            self.assertFalse(self.row("proj_b").retired_at, "not while it holds the work")
            running.release()
            self.assertTrue(done.wait(5))
        self.assertTrue(self.row("proj_b").retired_at)
        self.assertTrue(self.hb._going(where), "the next round goes on")

    def test_with_nothing_new_to_read_it_is_still_gone_through(self) -> None:
        self.hb.merge_projects(["proj_a", "proj_b"])
        self.hb.models = _Turns([])
        with mock.patch.object(self.hb, "_going", return_value=True), \
                mock.patch.object(ProjectRound, "_name_what_is_new") as named, \
                mock.patch.object(ProjectRound, "_link_what_is_open") as linked:
            round_ = ProjectRound(self.hb, self.hb.where("live"), {})
            round_._after_the_users_merge()
        named.assert_called_once()
        linked.assert_called_once()
        self.assertFalse(self.row().described.get("plan_due"), "gone through")
        self.assertEqual(json.loads(json.dumps(round_.report["plans"][0]["project"])), "proj_a")


if __name__ == "__main__":
    unittest.main()
