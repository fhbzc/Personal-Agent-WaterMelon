"""Tasks and projects, made per run (section 37, as amended 2026-10-18).

Each run writes the new states down as tasks, given the projects of the
last week, and groups the week's tasks into projects - a project keeping
its id while its main body goes on, measured in worked minutes. Everything
here is made up.
"""

import json
import re
import unittest
from datetime import datetime, timedelta, timezone

from personalized_agent.common.events.models import utcnow
from personalized_agent.heartbeat.states import task_projects as tp
from support import TemporaryAgent


class _State:
    def __init__(self, sid, title, minutes=10, kind="work", start=None, span=15,
                 sources=()):
        self.id = sid
        self.title = title
        self.detail = ""
        self.about = "user"
        self.starts_at = start or datetime(2026, 1, 5, 9, tzinfo=timezone.utc)
        self.ends_at = self.starts_at + timedelta(minutes=span)
        self.metadata = {"minutes": minutes, "kind": kind, "sources": list(sources)}


class InheritingByWorkedMinutesTests(unittest.TestCase):
    def setUp(self) -> None:
        names = iter(f"N{n}" for n in range(9))
        self.make = lambda: next(names)

    def test_the_main_body_goes_on_and_a_small_project_wholly_taken_ends_into_it(self) -> None:
        minutes = {"a1": 60, "a2": 60, "a3": 30, "b1": 10}
        out, happened, heirs = tp.inherit([["a1", "a2", "a3", "b1"]],
                                          {"A": ["a1", "a2"], "B": ["b1"]}, minutes, self.make)
        self.assertEqual(list(out), ["A"])
        self.assertEqual(happened["A"]["kind"], "continued")
        self.assertEqual(heirs, {"B": "A"})

    def test_two_substantial_parts_apart_is_a_split(self) -> None:
        minutes = {"c1": 40, "c2": 40}
        out, happened, heirs = tp.inherit([["c1"], ["c2"]], {"C": ["c1", "c2"]},
                                          minutes, self.make)
        self.assertNotIn("C", out)
        self.assertEqual({one["kind"] for one in happened.values()}, {"split"})
        self.assertIn("C", heirs)

    def test_two_substantial_parts_together_is_a_merge(self) -> None:
        minutes = {"a1": 50, "b1": 50}
        out, happened, heirs = tp.inherit([["a1", "b1"]], {"A": ["a1"], "B": ["b1"]},
                                          minutes, self.make)
        (pid, what), = happened.items()
        self.assertEqual(what["kind"], "merged")
        self.assertEqual(sorted(what["from"]), ["A", "B"])
        self.assertEqual(set(heirs), {"A", "B"})

    def test_a_small_piece_leaving_changes_nothing(self) -> None:
        minutes = {"a1": 90, "a2": 5}
        out, happened, _heirs = tp.inherit([["a1"], ["a2"]], {"A": ["a1", "a2"]},
                                           minutes, self.make)
        self.assertEqual(out["A"], ["a1"])
        self.assertEqual(happened["A"]["kind"], "continued")


class WritingTasksTests(unittest.TestCase):
    def test_every_state_is_in_exactly_one_task(self) -> None:
        states = [_State("x1", "Edit the grant budget"), _State("x2", "Edit the grant text"),
                  _State("x3", "Watch a cooking video", kind="leisure")]
        short = tp.short_labels([one.id for one in states], "s")
        answer = {"tasks": [
            {"title": "Revise the coastal-erosion grant", "summary": "Budget and text.",
             "kind": "work", "continues": "P1", "states": ["s1", "s2", "s1"]},
            {"title": "Nothing", "states": ["s9"]}]}
        tasks = tp.read_tasks(answer, states, short, {"P1": "p-grant"})
        held = [sid for task in tasks for sid in task["states"]]
        self.assertEqual(sorted(held), ["x1", "x2", "x3"])
        self.assertEqual(tasks[0]["continues"], "p-grant")
        self.assertTrue(tasks[-1].get("unnamed"))
        self.assertEqual(tasks[-1]["kind"], "leisure")

    def test_a_continues_naming_no_listed_project_is_dropped(self) -> None:
        states = [_State("x1", "Restore the motorcycle carburettor")]
        answer = {"tasks": [{"title": "t", "kind": "work", "continues": "P7",
                             "states": ["s1"]}]}
        tasks = tp.read_tasks(answer, states, {"x1": "s1"}, {"P1": "p-one"})
        self.assertEqual(tasks[0]["continues"], "")

    def test_minutes_are_held_to_their_span_and_the_stretch(self) -> None:
        start = datetime(2026, 1, 5, 9, tzinfo=timezone.utc)
        states = [_State("a", "x", minutes=50, start=start, span=20),
                  _State("b", "y", minutes=40, start=start, span=60)]
        said = tp.worked_minutes(states, (start, start + timedelta(minutes=30)))
        self.assertAlmostEqual(said["a"] + said["b"], 30.0)
        self.assertLessEqual(said["a"], 20.0)


class GroupingTests(unittest.TestCase):
    def test_meaning_and_what_the_writer_said_make_one_partition(self) -> None:
        tasks = [{"id": "t1", "kind": "work", "continues": ""},
                 {"id": "t2", "kind": "work", "continues": "p-boat"},
                 {"id": "t3", "kind": "work", "continues": ""},
                 {"id": "t4", "kind": "routine", "continues": ""},
                 {"id": "t5", "kind": "leisure", "continues": ""}]
        vectors = {"t1": [1.0, 0.0], "t2": [0.0, 1.0], "t3": [0.98, 0.2],
                   "t4": [1.0, 0.0], "t5": [1.0, 0.0]}
        groups = tp.group(tasks, vectors, {"t1": "p-boat"})
        together = next(one for one in groups if "t1" in one)
        self.assertIn("t2", together, "said to continue t1's project")
        self.assertIn("t3", together, "means what t1 means")
        self.assertFalse(any("t4" in one for one in groups), "routine is in no project")
        self.assertFalse(any("t5" in one and "t1" in one for one in groups),
                         "leisure apart from work")


def _writer(prompt_seen):
    """A model that writes every stretch as two tasks by their words."""

    class Reply:
        def __init__(self, text):
            self.text = text
            self.usage = None

    class Models:
        def invoke(self, request, role=None):
            prompt = request.messages[0]["content"]
            prompt_seen.append(prompt)
            if '"lines"' in request.system and "STEP 2" not in request.system:
                labels = re.findall(r"^== (P\d+)", prompt, re.M)
                return Reply(json.dumps({"lines": {one: {"line": "A made-up project"}
                                                   for one in labels}}))
            rows = re.findall(r"^(s\d+)\|[^|]*\|[^|]*\|[^|]*\|([^|]*)\|", prompt, re.M)
            grant = [sid for sid, title in rows if "grant" in title.lower()]
            other = [sid for sid, title in rows if "grant" not in title.lower()]
            tasks = []
            if grant:
                tasks.append({"title": "Revise the coastal-erosion grant",
                              "summary": "Worked on the grant.", "kind": "work",
                              "continues": "", "states": grant})
            if other:
                tasks.append({"title": "Watch cooking videos", "summary": "Videos.",
                              "kind": "leisure", "continues": "", "states": other})
            return Reply(json.dumps({"lines": {}, "tasks": tasks}))

    return Models()


class _Embed:
    def embed(self, texts, while_ok=None):
        return [[1.0, 0.0] if "grant" in text.lower() else [0.0, 1.0] for text in texts]

    def loaded(self):
        import contextlib
        return contextlib.nullcontext()


class ARunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.app.config.set("state_projects_enabled", True)
        self.beat = self.app.state_heartbeat
        self.seen = []
        self.beat.models = _writer(self.seen)
        self.beat.embedder = _Embed()

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _state(self, title, hours_ago, kind="work"):
        from personalized_agent.agent.states.store import State

        return self.app.states.add(State(
            title=title, detail="",
            starts_at=utcnow() - timedelta(hours=hours_ago),
            ends_at=utcnow() - timedelta(hours=hours_ago) + timedelta(minutes=15),
            metadata={"minutes": 12, "kind": kind}))

    def test_one_state_is_enough_to_begin(self) -> None:
        self._state("They edit the grant budget.", 2)
        said = self.beat.due_now()
        self.assertTrue(said["run"], said)

    def test_the_first_run_writes_the_week_and_makes_projects(self) -> None:
        for hours in (100, 99, 30, 3):
            self._state("They edit the grant budget.", hours)
        self._state("They watch a cooking video.", 2, kind="leisure")
        report = self.beat.run_once(trigger="manual_debug")
        self.assertTrue(report.get("round_complete"), report)
        self.assertGreaterEqual(len(report["stretches"]), 2, "written in 12-hour stretches")
        from personalized_agent.heartbeat.states.tasks import Kept
        projects = [one for one in Kept(self.app.db).live()
                    if one.profile.get("scheme") == "projects"]
        titles = sorted(one.described["title"] for one in projects)
        self.assertEqual(titles, ["Revise the coastal-erosion grant", "Watch cooking videos"])
        grant = next(one for one in projects if "grant" in one.described["title"])
        self.assertAlmostEqual(grant.described["minutes"], 48.0)
        self.assertEqual(grant.described["line"], "A made-up project")

    def test_the_earlier_tasks_are_kept_as_history_untouched(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Kept, Task
        Kept(self.app.db).save([Task(id="old-1", number=1, members=["m1", "m2"],
                                     described={"title": "An earlier grouping"})])
        self._state("They edit the grant budget.", 2)
        self.beat.run_once(trigger="manual_debug")
        old, = Kept(self.app.db).by_ids(["old-1"])
        self.assertTrue(old.archived_at)
        self.assertEqual(old.members, ["m1", "m2"])
        self.assertEqual(old.described["title"], "An earlier grouping")

    def test_a_later_run_reads_only_what_is_new_and_shows_the_projects(self) -> None:
        self._state("They edit the grant budget.", 5)
        self.beat.run_once(trigger="manual_debug")
        first = len(self.seen)
        self._state("They edit the grant text.", 0)
        report = self.beat.run_once(trigger="manual_debug")
        self.assertEqual(report["window"]["kind"], "new since the last run")
        self.assertIn("THE USER'S PROJECTS", self.seen[first])
        picture = self.beat.the_picture()
        self.assertEqual(picture["scheme"], "projects")
        grant = next(one for one in picture["projects"] if "grant" in one["title"])
        self.assertEqual(grant["tasks_count"], 2)


class WhatReadsTheProjectsTests(unittest.TestCase):
    """With the scheme ON, as everyone runs it: learning, Work, the
    recommendations and the English copies read the projects it made."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.app.config.set("state_projects_enabled", True)
        self.beat = self.app.state_heartbeat
        self.beat.models = _writer([])
        self.beat.embedder = _Embed()
        from personalized_agent.agent.states.store import State
        for hours in (30, 20, 12, 8, 5, 3):
            self.app.states.add(State(
                title="They edit the grant budget.", detail="",
                starts_at=utcnow() - timedelta(hours=hours),
                ends_at=utcnow() - timedelta(hours=hours) + timedelta(minutes=15),
                metadata={"minutes": 12, "kind": "work"}))
        self.report = self.beat.run_once(trigger="manual_debug")
        from personalized_agent.heartbeat.states.tasks import Kept
        self.project = next(one for one in Kept(self.app.db).live()
                            if one.profile.get("scheme") == "projects")

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_learning_lists_the_project_and_work_can_start_on_it(self) -> None:
        experience = self.beat.experience
        names = {unit["id"]: unit["name"] for unit in experience.units()}
        self.assertEqual(names.get(self.project.id), "Revise the coastal-erosion grant")
        self.assertIsNotNone(experience.unit(self.project.id))
        self.assertEqual(len((experience.full_unit(self.project.id) or {}).get("tasks") or []), 1)

    def test_the_recommendations_read_its_latest_states_under_it(self) -> None:
        from personalized_agent.heartbeat.connections import recommend
        found = {one["key"]: one for one in recommend.subjects(self.app.db)}
        mine = found.get(f"project:{self.project.id}")
        self.assertIsNotNone(mine, list(found))
        self.assertTrue(mine["detail"][0].startswith("PROJECT: Revise the coastal-erosion grant"))
        events = [line for line in mine["detail"] if line.startswith("STATE [")]
        # Six events of fifteen minutes: far from a day, so all of them,
        # each with its time.
        self.assertEqual(len(events), 6, mine["detail"])
        self.assertTrue(all(" UTC, 15 min]: They edit the grant budget." in line
                            for line in events), events)
        self.assertAlmostEqual(mine["weight"], 1.5, places=2)

    def test_a_state_no_run_has_read_comes_alone(self) -> None:
        from personalized_agent.agent.states.store import State
        from personalized_agent.heartbeat.connections import recommend
        self.app.states.add(State(
            title="They sketch a tide-gauge chart.", detail="",
            starts_at=utcnow() - timedelta(minutes=20), ends_at=utcnow() - timedelta(minutes=10),
            metadata={"minutes": 10, "kind": "work"}))
        found = {one["key"]: one for one in recommend.subjects(self.app.db)}
        alone = found.get("recent:states")
        self.assertIsNotNone(alone, list(found))
        self.assertEqual(len(alone["detail"]), 1)
        self.assertIn("10 min]: They sketch a tide-gauge chart.", alone["detail"][0])

    def test_a_project_of_less_than_the_hours_is_no_page_of_its_own(self) -> None:
        # An hour and a half of the grant, against a rule of two: its states
        # are put with the ones no run has read, not made a page.
        from unittest import mock
        from personalized_agent.heartbeat.connections import recommend
        with mock.patch.object(recommend, "PAGE_PROJECT_HOURS", 2.0):
            found = {one["key"]: one for one in recommend.subjects(self.app.db)}
        self.assertNotIn(f"project:{self.project.id}", found)
        loose = found["recent:states"]
        self.assertEqual(len(loose["detail"]), 6)
        self.assertEqual(len(loose["measured"]), 6)
        self.assertTrue(all(line.startswith("STATE [") for line in loose["detail"]))

    def test_a_day_of_events_and_no_more(self) -> None:
        # The newest state alone lasts a day: it is all that is read, and
        # the grant's states before it are not.
        from personalized_agent.agent.states.store import State
        from personalized_agent.heartbeat.connections import recommend
        self.app.states.add(State(
            title="They run the long tide-model batch.", detail="",
            starts_at=utcnow() - timedelta(hours=2), ends_at=utcnow() + timedelta(hours=22),
            metadata={"kind": "work"}))
        found = {one["key"]: one for one in recommend.subjects(self.app.db)}
        self.assertNotIn(f"project:{self.project.id}", found)
        self.assertEqual(len(found["recent:states"]["detail"]), 1)
        self.assertAlmostEqual(found["recent:states"]["weight"], 24.0, places=2)

    def test_its_english_copy_is_not_translated_again(self) -> None:
        from personalized_agent.heartbeat.states.english_copies import _rows
        self.assertNotIn(self.project.id, [ident for _t, ident, _need in _rows(self.app.db, "live")])

    def test_the_overview_has_its_worked_time_and_folders(self) -> None:
        picture = self.beat.the_picture()
        mine = next(one for one in picture["projects"] if one["id"] == self.project.id)
        self.assertAlmostEqual(mine["minutes"], 72.0)
        self.assertFalse(mine["ended"])
        self.assertTrue(mine["tasks"])


class TheOverviewAndTheNextStepsTests(unittest.TestCase):
    def test_the_page_filters_projects_by_worked_time_and_has_no_next_steps(self) -> None:
        from pathlib import Path
        script = (Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
                  / "app.js").read_text(encoding="utf-8")
        self.assertIn("const PJ_LEAST_MINUTES = [15, 60, 360, 1440];", script)
        self.assertIn('whole.scheme === "projects"', script)
        self.assertNotIn("/api/state_heartbeat/todo", script)
        self.assertNotIn("sbShowStepsEverywhere", script)


if __name__ == "__main__":
    unittest.main()
