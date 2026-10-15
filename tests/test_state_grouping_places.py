"""Where the work is, in grouping: a state working somewhere a task has never
been is not taken in on the strength of the mail being open beside it; a
group inside a task that works somewhere the rest never does is given back
as its own task; and a task that is largely other states now is named again.

The case: a day on a new project (its own repository and site) went into an
email task because Gmail was open beside it, and once the first few were in,
the task held the project's folder and the rest followed. The project never
appeared, under a name about emails."""

from __future__ import annotations

import math
import unittest

from personalized_agent.heartbeat.states.tasks import (
    Task, apart_by_place, fits, missing_from, wants_summary)

MAIL = "gmail.com"
PROJECT = "c:/users/me/desktop/preprint_reader_dana"
SITE = "preprints.example.org"


def _unit(vector):
    size = math.sqrt(sum(v * v for v in vector))
    return [v / size for v in vector]


def _near(axis, wobble, dims=8):
    """A unit vector mostly along one axis, a little along another."""
    vector = [0.0] * dims
    vector[axis] = 1.0
    vector[(axis + 1 + wobble) % dims] = 0.15
    return _unit(vector)


def _task(members, vectors, places_of):
    task = Task(id="t", members=[])
    task.start_profile()
    for index, one in enumerate(members):
        task.take(one, vectors[one], places_of.get(one, ()),
                  f"2026-10-0{1 + index // 10}T{10 + index % 10}:00:00+00:00",
                  f"2026-10-0{1 + index // 10}T{10 + index % 10}:10:00+00:00")
    return task


class APlaceTheTaskHasNeverBeenTests(unittest.TestCase):

    def setUp(self) -> None:
        self.vectors = {f"m{i}": _near(0, i % 3) for i in range(10)}
        self.places = {one: [MAIL] for one in self.vectors}
        self.task = _task(list(self.vectors), self.vectors, self.places)
        self.specific = {MAIL: 0.75}

    def test_it_is_the_most_telling_place_the_task_lacks(self) -> None:
        lacking, where = missing_from(self.task, [MAIL, PROJECT], self.specific)
        self.assertEqual((lacking, where), (1.0, PROJECT))
        self.assertEqual(missing_from(self.task, [MAIL], self.specific)[0], 0.0)

    def test_a_new_file_in_a_folder_the_task_has_is_not_somewhere_new(self) -> None:
        places = {one: [MAIL, PROJECT] for one in self.vectors}
        task = _task(list(self.vectors), self.vectors, places)
        lacking, _where = missing_from(
            task, [PROJECT + "/app/page.tsx", PROJECT], {MAIL: 0.75})
        self.assertEqual(lacking, 0.0)

    def test_sharing_only_the_mail_counts_against_it(self) -> None:
        state = _near(0, 1)
        with_mail = fits(self.task, state, [MAIL], self.specific)
        elsewhere = fits(self.task, state, [MAIL, PROJECT], self.specific)
        self.assertGreater(elsewhere["score"], with_mail["score"])
        self.assertEqual(elsewhere["elsewhere"], PROJECT)
        self.assertEqual(with_mail["elsewhere"], "")

    def test_somewhere_new_and_other_work_does_not_fit(self) -> None:
        # Close enough in meaning to be taken on the mail's credit before.
        state = _unit([0.31, 0.0, 0.0, math.sqrt(1 - 0.31 ** 2), 0, 0, 0, 0])
        before = fits(self.task, state, [MAIL], self.specific)
        self.assertTrue(before["fits"])
        after = fits(self.task, state, [MAIL, PROJECT], self.specific)
        self.assertFalse(after["fits"])


class AGroupThatWorksSomewhereElseTests(unittest.TestCase):

    def setUp(self) -> None:
        self.vectors, self.places = {}, {}
        for i in range(12):                       # the email task
            self.vectors[f"mail{i}"] = _near(0, i % 3)
            self.places[f"mail{i}"] = [MAIL]
        for i in range(8):                        # the project, with mail open
            self.vectors[f"proj{i}"] = _near(4, i % 3)
            self.places[f"proj{i}"] = [PROJECT, SITE] + ([MAIL] if i % 2 else [])
        # One of the project's states names only its site.
        self.vectors["proj_site"] = _near(4, 0)
        self.places["proj_site"] = [SITE, MAIL]
        self.task = _task(list(self.vectors), self.vectors, self.places)
        self.specific = {MAIL: 0.75, PROJECT: 1.0, SITE: 1.0}

    def test_the_project_is_found_and_all_of_it_goes(self) -> None:
        found = apart_by_place(self.task, self.places, self.vectors,
                               self.specific, min_nodes=5)
        self.assertIsNotNone(found)
        # Its folder or its site: either sets it apart.
        self.assertIn(found["place"], (PROJECT, SITE))
        self.assertEqual(set(found["states"]),
                         {f"proj{i}" for i in range(8)} | {"proj_site"})
        self.assertGreater(found["apart"], found["wide"])

    def test_not_when_the_rest_works_there_too(self) -> None:
        places = {one: list(where) + [PROJECT, SITE]
                  for one, where in self.places.items()}
        self.assertIsNone(apart_by_place(self.task, places, self.vectors,
                                         self.specific, min_nodes=5))

    def test_not_when_it_is_the_same_work_in_a_second_folder(self) -> None:
        vectors = {one: _near(0, i % 3) for i, one in enumerate(self.vectors)}
        self.assertIsNone(apart_by_place(self.task, self.places, vectors,
                                         self.specific, min_nodes=5))

    def test_not_by_a_window_or_page_title(self) -> None:
        places = {one: ([MAIL] if one.startswith("mail") else
                        [MAIL, "o vs. you - a video page"])
                  for one in self.places}
        self.assertIsNone(apart_by_place(
            self.task, places, self.vectors,
            {MAIL: 0.75, "o vs. you - a video page": 1.0}, min_nodes=5))

    def test_not_too_few_to_be_a_task(self) -> None:
        self.assertIsNone(apart_by_place(self.task, self.places, self.vectors,
                                         self.specific, min_nodes=10))


class NamedAgainWhenItIsOtherStatesNowTests(unittest.TestCase):

    def _named(self, size):
        vectors = {f"s{i}": _near(0, i % 3) for i in range(size)}
        task = _task(list(vectors), vectors, {})
        task.summary_id = "rec_1"
        task.described = {"concrete_asked": True, "title": "Emails"}
        task.named()
        task.summary_members = task.size
        return task, vectors

    def test_a_few_new_states_keep_the_name(self) -> None:
        task, _vectors = self._named(20)
        for i in range(3):
            task.take(f"new{i}", _near(0, i % 3), (), "", "")
        self.assertFalse(wants_summary(task))

    def test_a_third_other_states_is_named_again(self) -> None:
        task, _vectors = self._named(20)
        for i in range(7):
            task.take(f"new{i}", _near(0, i % 3), (), "", "")
        self.assertTrue(wants_summary(task))

    def test_losing_a_third_to_a_split_is_named_again(self) -> None:
        task, _vectors = self._named(21)
        task.members = task.members[:14]
        self.assertTrue(wants_summary(task))


class ProjectsFollowTheSameRulesTests(unittest.TestCase):
    """Tasks into projects: not linked through the mail when their homes
    differ; tasks whose home the rest of a project never has, and that are
    other work, leave it; and a project whose tasks are largely others now is
    named again."""

    def setUp(self) -> None:
        from support import TemporaryAgent

        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        self.app.config.set("state_summaries_enabled", False, internal=True)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _in(self, task_id, folder, axis, with_mail=True):
        from test_state_tasks_continue import _task, _v

        places = [f"c:/users/me/desktop/{folder}"] + ([MAIL] if with_mail else [])
        task = _task(task_id, [_v(*([0] * axis + [1, 0.1]))] * 6, places)
        task.described = {"title": f"Work in {folder}",
                          "where": f"In C:\\Users\\me\\Desktop\\{folder}."}
        return task, {one: places for one in task.members}

    def _known(self, *tasks):
        return {one: object() for task in tasks for one in task.members}

    def test_the_mail_does_not_link_two_homes(self) -> None:
        from test_state_tasks_continue import _ask

        alpha, places_a = self._in("task-a", "project_alpha", 0)
        beta, places_b = self._in("task-b", "project_beta", 4)
        self.beat.models, asked = _ask({"one_project": True, "tasks": [1, 2],
                                        "name": "x", "why": "y"})
        projects = self.beat._find_projects(
            [alpha, beta], [], self._known(alpha, beta),
            {**places_a, **places_b}, {}, self.beat.where("live"))
        self.assertEqual(projects, [])
        self.assertEqual(asked, [], "nothing to ask: they share only the mail")

    def test_other_work_leaves_the_project(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Project, Projects
        from test_state_tasks_continue import _ask

        made, places = [], {}
        for task_id, folder, axis in (("t-a1", "project_alpha", 0),
                                      ("t-a2", "project_alpha", 0),
                                      ("t-b1", "project_beta", 4),
                                      ("t-b2", "project_beta", 4)):
            task, held = self._in(task_id, folder, axis, with_mail=False)
            made.append(task)
            places.update(held)
        Projects(self.app.db).save(Project(
            id="project-live-one", name="Alpha", why="one program",
            home="c:/users/me/desktop/project_alpha",
            tasks=[task.id for task in made], described={"named_size": 4}))
        self.beat.models, _asked = _ask({"one_project": False, "tasks": [],
                                         "why": "no"})
        report = {}
        self.beat._find_projects(made, [], self._known(*made), places, report,
                                 self.beat.where("live"))
        project = Projects(self.app.db).all()[0]
        self.assertEqual(sorted(project.tasks), ["t-a1", "t-a2"])
        self.assertEqual(sorted(project.declined), ["t-b1", "t-b2"])
        self.assertEqual(report["taken_out_of_projects"][0]["tasks"],
                         ["t-b1", "t-b2"])

    def test_a_project_that_swapped_its_tasks_is_named_again(self) -> None:
        from personalized_agent.heartbeat.states.tasks import (
            Kept, Project, Projects)
        from test_state_tasks_continue import _ask

        made = [self._in(f"t-{n}", "project_alpha", 0)[0] for n in range(3)]
        for seat, task in enumerate(made):
            task.seat = seat
        Kept(self.app.db).save(made)
        # Named from three tasks, two of which have gone; the same count.
        Projects(self.app.db).save(Project(
            id="project-live-one", name="Old name", why="w",
            tasks=[task.id for task in made],
            described={"named_size": 3, "status": "old",
                       "summarised": ["t-0", "gone-1", "gone-2"]}))
        self.beat.models, _asked = _ask({"status": "new", "name": "New name",
                                         "why": "it changed"})
        self.beat._bring_projects_up_to_date({"moved": {}},
                                             self.beat.where("live"))
        project = Projects(self.app.db).all()[0]
        self.assertEqual(project.name, "New name")
        self.assertEqual(sorted(project.described["summarised"]),
                         ["t-0", "t-1", "t-2"])


class TheSameDrawingAfterARestartTests(unittest.TestCase):
    """The round's drawing - its arrows and pieces - is kept, so closing the
    app and opening it again shows the same picture, not one rebuilt from
    the task rows alone."""

    def setUp(self) -> None:
        from support import TemporaryAgent

        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_kept_drawing_is_drawn_after_a_restart(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Kept
        from test_state_tasks_continue import _task, _v

        made = [_task(f"task-{n}", [_v(n, 1)] * 6) for n in range(2)]
        for seat, task in enumerate(made):
            task.seat = seat
            task.described = {"title": f"Task {seat}", "concrete": True}
        Kept(self.app.db).save(made)
        report = {
            "plan": {"nodes": [{"id": "1", "task_id": "task-0", "title": "Task 0"},
                               {"id": "2", "task_id": "task-1", "title": "Task 1"}],
                     "edges": [{"before": "1", "after": "2", "why": "first"}]},
            "layers": [{"id": "1", "task_id": "task-0", "seat": 0, "size": 6,
                        "states": [], "summary": {"title": "Task 0"},
                        "children": [{"id": "1.1", "size": 3, "states": [],
                                      "summary": {"title": "A piece"}}]},
                       {"id": "2", "task_id": "task-1", "seat": 1, "size": 6,
                        "states": [], "summary": {"title": "Task 1"}}],
            "states": [], "finished_at": "2026-10-07T20:00:00+00:00"}
        self.beat._keep_the_drawing(report)
        # The app closes: the report in memory is gone.
        self.beat._last_report = {}
        self.beat._progress = {}
        self.beat._kept_drawing = None
        drawn = self.beat.the_picture()
        self.assertEqual(drawn["plan"]["edges"],
                         [{"before": "1", "after": "2", "why": "first"}])
        first = next(one for one in drawn["layers"] if one["id"] == "1")
        self.assertEqual([kid["id"] for kid in first["children"]], ["1.1"])


class ATaskKeepsItsHistoryTests(unittest.TestCase):
    """A task described again keeps what it was called before, and the page
    can ask where a task came from - split out of, merged from - and where
    those came from in turn."""

    def setUp(self) -> None:
        from support import TemporaryAgent

        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_version_it_replaces_is_kept(self) -> None:
        from personalized_agent.heartbeat.states.heartbeat import _version_of
        from test_state_tasks_continue import _task, _v

        task = _task("task-a", [_v(1, 0)] * 10)
        task.summary_id = "rec_old"
        task.summary_at = "2026-10-07T18:45:00+00:00"
        task.summary_members = 10
        task.described = {"title": "Follow up on the emails", "summary": "Mail."}
        task.named()
        for n in range(6):
            task.take(f"new-{n}", _v(0, 1), (), "", "")
        earlier = _version_of(task, "rec_new")
        self.assertEqual(earlier["title"], "Follow up on the emails")
        self.assertEqual(earlier["record_id"], "rec_old")
        self.assertIn("60% of its states changed", earlier["why"])
        # The same summary again is not a version.
        self.assertIsNone(_version_of(task, "rec_old"))

    def test_where_it_came_from_is_told(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Kept
        from test_state_tasks_continue import _task, _v

        parent = _task("task-parent", [_v(1, 0)] * 6)
        parent.described = {"title": "Emails and messages"}
        child = _task("task-child", [_v(0, 1)] * 6)
        child.described = {"title": "Build the PreprintReader site", "history": [
            {"at": "2026-10-08T02:01:00+00:00", "title": "PreprintReader set-up",
             "why": "40% of its states changed since it was named"}]}
        child.born_of = {"kind": "split from", "from": ["task-parent"],
                         "why": "its work is in c:/x/preprint_reader"}
        Kept(self.app.db).save([parent, child])
        said = self.beat.task_history("task-child")
        self.assertEqual(said["task"]["title"], "Build the PreprintReader site")
        self.assertEqual([one["title"] for one in said["versions"]],
                         ["PreprintReader set-up"])
        self.assertEqual(said["came_from"][0]["title"], "Emails and messages")
        self.assertEqual(said["came_from"][0]["how"], "split from")
        self.assertIsNone(self.beat.task_history("task-none"))


if __name__ == "__main__":
    unittest.main()
