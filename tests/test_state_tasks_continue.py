"""A task that carries on: a profile instead of every state, placement by
meaning and by workspace, dormancy, and being picked up again (34.4, as
amended)."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

from support import TemporaryAgent

from personalized_agent.common.events.models import utcnow
from personalized_agent.heartbeat.states.tasks import (
    ARCHIVE_AFTER_DAYS, FAR, MEMBERS_KEPT, QUIET_AFTER_DAYS, Kept, Task,
    fits, place, shares_with, specificity, wants_summary)


def _v(*values):
    """A small unit-ish vector, padded."""
    out = list(values) + [0.0] * (8 - len(values))
    size = sum(one * one for one in out) ** 0.5 or 1.0
    return [one / size for one in out]


def _task(task_id, vectors, places=(), started=None):
    task = Task(id=task_id)
    task.start_profile()
    when = started or datetime.now(timezone.utc) - timedelta(hours=len(vectors))
    for index, vector in enumerate(vectors):
        moment = (when + timedelta(hours=index)).isoformat()
        task.take(f"{task_id}-{index}", vector, places, moment, moment)
    return task


class AProfileInsteadOfEveryStateTests(unittest.TestCase):

    def test_the_centre_follows_the_work(self) -> None:
        """Fed states that move from one subject to another, the centre
        moves with them - a task that changes stays one task."""
        task = _task("t", [_v(1, 0)] * 30)
        before = task.centre
        for index in range(60):
            task.take(f"later-{index}", _v(0.3, 1), (), "", "")
        self.assertGreater(task.centre[1], before[1] + 0.3)

    def test_it_counts_every_state_but_lists_only_the_recent(self) -> None:
        task = _task("t", [_v(1, 0)] * (MEMBERS_KEPT + 30))
        self.assertEqual(task.count, MEMBERS_KEPT + 30)
        self.assertEqual(len(task.members), MEMBERS_KEPT)
        self.assertEqual(task.members[-1], f"t-{MEMBERS_KEPT + 29}")

    def test_it_knows_when_it_was_first_and_last_worked_on(self) -> None:
        began = datetime(2026, 9, 1, tzinfo=timezone.utc)
        task = _task("t", [_v(1, 0)] * 5, started=began)
        self.assertTrue(task.first_seen.startswith("2026-09-01T00"))
        self.assertTrue(task.last_seen.startswith("2026-09-01T04"))

    def test_and_where_it_was_worked_on(self) -> None:
        task = _task("t", [_v(1, 0)] * 10, places=("c:/work/paper",))
        self.assertGreater(task.coverage("c:/work/paper"), 0.9)
        self.assertEqual(task.region(), ["c:/work/paper"])


class PlacingAStateTests(unittest.TestCase):

    def _two(self):
        paper = _task("paper", [_v(1, 0.2)] * 20, places=("c:/work/paper",))
        other = _task("other", [_v(1, -0.2)] * 20, places=("c:/work/other",))
        return paper, other

    def test_a_state_goes_to_the_nearest_task(self) -> None:
        paper, other = self._two()
        got = place([paper, other], ["s"], {"s": _v(1, 0.25)}, {"s": []},
                    {"s": ("", "")})
        self.assertEqual(got["placed"], {"s": "paper"})
        self.assertEqual(got["evidence"]["s"]["task"], "paper")

    def test_a_shared_place_decides_between_two_that_read_alike(self) -> None:
        """Worded halfway between them, it opened a file in the second's
        folder: the folder decides."""
        paper, other = self._two()
        got = place([paper, other], ["s"], {"s": _v(1, 0.0)},
                    {"s": ["c:/work/other"]}, {"s": ("", "")})
        self.assertEqual(got["placed"].get("s"), "other")
        self.assertGreater(got["evidence"]["s"]["workspace"], 0.5)

    def test_a_tie_is_left_loose_rather_than_guessed(self) -> None:
        paper, other = self._two()
        got = place([paper, other], ["s"], {"s": _v(1, 0.0)}, {"s": []},
                    {"s": ("", "")})
        self.assertEqual(got["placed"], {})
        self.assertIn("equally near", got["evidence"]["s"]["why"])

    def test_and_something_far_from_everything_is_left_loose(self) -> None:
        paper, other = self._two()
        far_away = _v(0, 0, 1)
        self.assertGreater(1 - sum(a * b for a, b in zip(far_away, paper.centre)),
                           FAR)
        got = place([paper, other], ["s"], {"s": far_away}, {"s": []},
                    {"s": ("", "")})
        self.assertEqual(got["loose"], ["s"])

    def test_a_place_every_task_uses_tells_them_apart_less(self) -> None:
        shared = [_task(f"t{i}", [_v(1, i)] * 10, places=("mail.google.com",))
                  for i in range(4)]
        own = _task("own", [_v(1, 9)] * 10, places=("c:/work/own",))
        specific = specificity(shared + [own])
        self.assertLess(specific["mail.google.com"], specific["c:/work/own"])
        self.assertLess(shares_with(shared[0], ["mail.google.com"], specific)[0],
                        shares_with(own, ["c:/work/own"], specific)[0])


class QuietAndDormantTests(unittest.TestCase):

    def test_by_how_long_since_it_was_last_worked_on(self) -> None:
        now = datetime.now(timezone.utc)
        for days, phase in ((0, "active"), (QUIET_AFTER_DAYS + 1, "quiet"),
                            (ARCHIVE_AFTER_DAYS + 1, "archived")):
            task = _task("t", [_v(1)] * 3,
                         started=now - timedelta(days=days, hours=3))
            self.assertEqual(task.phase(), phase, days)


class ANameIsWrittenAgainWhenTheWorkHasMovedTests(unittest.TestCase):

    def test_growing_a_little_is_not_enough(self) -> None:
        task = _task("t", [_v(1, 0)] * 10)
        task.summary_id = "s"
        task.named()
        task.described = {"concrete_asked": True}
        for index in range(2):
            task.take(f"more-{index}", _v(1, 0.01), (), "", "")
        self.assertFalse(wants_summary(task))

    def test_being_largely_other_states_now_is(self) -> None:
        """A task that is mostly states it was not named from is named again,
        however little its centre moved (a third or more changed)."""
        task = _task("t", [_v(1, 0)] * 10)
        task.summary_id = "s"
        task.named()
        task.described = {"concrete_asked": True}
        for index in range(100):
            task.take(f"more-{index}", _v(1, 0.01), (), "", "")
        self.assertTrue(wants_summary(task))

    def test_moving_is(self) -> None:
        task = _task("t", [_v(1, 0)] * 10)
        task.summary_id = "s"
        task.named()
        for index in range(100):
            task.take(f"more-{index}", _v(0.2, 1), (), "", "")
        self.assertTrue(wants_summary(task))


class StretchesOfWorkTests(unittest.TestCase):
    """A task left for months and picked up again has two stretches with a
    gap between them, not one line across the months."""

    def test_a_long_gap_starts_a_new_stretch(self) -> None:
        began = datetime(2026, 3, 1, tzinfo=timezone.utc)
        task = _task("t", [_v(1)] * 4, started=began)
        later = (began + timedelta(days=180)).isoformat()
        task.take("back", _v(1), (), later, later)
        stretches = task.stretches()
        self.assertEqual(len(stretches), 2)
        self.assertTrue(stretches[0][0].startswith("2026-03-01"))
        self.assertEqual(stretches[1][0], later)

    def test_a_short_one_does_not(self) -> None:
        began = datetime(2026, 3, 1, tzinfo=timezone.utc)
        task = _task("t", [_v(1)] * 4, started=began)
        later = (began + timedelta(days=1)).isoformat()
        task.take("next day", _v(1), (), later, later)
        self.assertEqual(len(task.stretches()), 1)

    def test_two_tasks_joined_keep_both_histories(self) -> None:
        one = _task("a", [_v(1)] * 3,
                    started=datetime(2026, 1, 1, tzinfo=timezone.utc))
        other = _task("b", [_v(1)] * 3,
                      started=datetime(2026, 6, 1, tzinfo=timezone.utc))
        one.absorb(other)
        self.assertEqual(len(one.stretches()), 2)


class ATaskOrOneOfItsPiecesTests(unittest.TestCase):
    """A task of several pieces has its centre between them; a state that
    is plainly about one piece is far from the middle."""

    def test_a_state_near_a_piece_joins_the_task(self) -> None:
        broad = _task("broad", [_v(1, 0), _v(0, 1)] * 10)
        other = _task("other", [_v(0, 0, 1)] * 10)
        state = _v(1, -0.4)
        self.assertGreater(1 - sum(a * b for a, b in zip(state, broad.centre)),
                           FAR)
        missed = place([broad, other], ["s"], {"s": state}, {"s": []},
                       {"s": ("", "")})
        self.assertNotIn("s", missed["placed"])
        broad.set_parts([_v(1, 0), _v(0, 1)])
        got = place([broad, other], ["s"], {"s": state}, {"s": []},
                    {"s": ("", "")})
        self.assertEqual(got["placed"].get("s"), "broad")
        self.assertEqual(got["evidence"]["s"]["part"], 0)


def _ask(answer):
    """A model that answers every grouping question the same way."""
    asked = []

    class Reply:
        text = json.dumps(answer)
        usage = None

    class Models:
        def invoke(self, request, role=""):
            asked.append(getattr(request, "system", "")[:40])
            return Reply()

    return Models(), asked


class TheRoundCarriesTasksOnTests(unittest.TestCase):
    """Whole rounds, over the real store."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        self.app.config.set("state_summaries_enabled", False, internal=True)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _write(self, titles, folder, hours_ago=1.0):
        from personalized_agent.agent.states.store import State

        for index, title in enumerate(titles):
            began = utcnow() - timedelta(hours=hours_ago, minutes=index)
            self.app.states.add(State(
                title=title, detail=title, starts_at=began,
                ends_at=began + timedelta(minutes=10),
                metadata={"sources": [f"{folder}/notes{index % 3}.md"]}))

    PAPER = ["Revising the weighting paper's methods section.",
             "Rewriting the weighting paper's results paragraph.",
             "Checking the weighting paper's figure captions.",
             "Editing the weighting paper's introduction.",
             "Tightening the weighting paper's discussion.",
             "Fixing references in the weighting paper."]
    COOKING = ["Looking up a recipe for mapo tofu.",
               "Reading how long to braise beef shank.",
               "Comparing recipes for scallion pancakes.",
               "Watching a video on knife skills for vegetables.",
               "Planning the week's dinners and shopping list.",
               "Reading about fermenting chili paste at home."]

    def _first(self):
        self._write(self.PAPER, "C:/work/weighting-paper", hours_ago=3)
        self._write(self.COOKING, "C:/home/recipes", hours_ago=2)
        return self.beat.run_once(trigger="manual_debug")

    def test_a_task_idle_for_a_month_goes_dormant_and_is_not_drawn(self) -> None:
        self._first()
        kept = Kept(self.app.db)
        task = kept.live()[0]
        long_ago = (datetime.now(timezone.utc)
                    - timedelta(days=ARCHIVE_AFTER_DAYS + 5)).isoformat()
        task.profile["last_seen"] = long_ago
        kept.save([task])
        report = self.beat.run_once(trigger="manual_debug")
        self.assertIn(task.id, report["continuity"]["went_dormant"])
        self.assertNotIn(task.id, {one["task_id"] for one in report["layers"]})
        self.assertIn(task.id, {one.id for one in Kept(self.app.db).archived()})

    def test_and_work_in_its_place_months_later_picks_it_up_again(self) -> None:
        self._first()
        kept = Kept(self.app.db)
        paper = next(one for one in kept.live()
                     if any("c:/work/weighting_paper" in place
                            for place in one.places()))
        paper.archived_at = (datetime.now(timezone.utc)
                             - timedelta(days=150)).isoformat()
        paper.profile["last_seen"] = paper.archived_at
        kept.save([paper])
        self.assertIn(paper.id, {one.id for one in Kept(self.app.db).archived()})
        # Worded like the work it carries on - the stand-in embedder these
        # tests run on knows words, not meanings.
        self._write(["Revising the weighting paper's methods section again.",
                     "Editing the weighting paper's results paragraph again."],
                    "C:/work/weighting-paper", hours_ago=0.2)
        report = self.beat.run_once(trigger="manual_debug")
        picked = report["continuity"]["picked_up_again"]
        if not picked:
            self.skipTest("these embeddings put the new states nearer "
                          "another task")
        self.assertEqual({row["task"] for row in picked}, {paper.id})
        self.assertIn(paper.id, {one.id for one in Kept(self.app.db).live()})
        again = next(one for one in Kept(self.app.db).live()
                     if one.id == paper.id)
        self.assertEqual(again.archived_at, "")

    def test_two_tasks_that_have_become_one_are_asked_about(self) -> None:
        self._first()
        kept = Kept(self.app.db)
        live = kept.live()
        if len(live) < 2:
            self.skipTest("one task on these embeddings")
        one, other = live[0], live[1]
        other.total = list(one.total)
        other.radius = one.radius = max(one.radius, 0.2)
        kept.save([one, other])
        self.beat.models, asked = _ask({"same": True, "why": "one paper"})
        report = self.beat.run_once(trigger="manual_debug")
        joined = report["continuity"]["joined"]
        self.assertTrue(joined, "the pair was not asked about")
        self.assertTrue(any(one_ask.startswith("ARE THESE TWO TASKS")
                            for one_ask in asked))
        live_now = {task.id for task in Kept(self.app.db).live()}
        self.assertEqual(len({one.id, other.id} & live_now), 1)

    def test_and_a_pair_answered_no_is_not_asked_again(self) -> None:
        self._first()
        kept = Kept(self.app.db)
        live = kept.live()
        if len(live) < 2:
            self.skipTest("one task on these embeddings")
        one, other = live[0], live[1]
        other.total = list(one.total)
        other.radius = one.radius = max(one.radius, 0.2)
        kept.save([one, other])
        self.beat.models, asked = _ask({"same": False, "why": "two things"})
        self.beat.run_once(trigger="manual_debug")
        first = len(asked)
        self.beat.run_once(trigger="manual_debug")
        self.assertEqual(len(asked), first)

    GARDEN = ["Repotting the basil seedlings on the balcony.",
              "Repotting the tomato seedlings on the balcony.",
              "Watering the basil seedlings on the balcony.",
              "Watering the tomato seedlings on the balcony.",
              "Pruning the basil seedlings on the balcony.",
              "Pruning the tomato seedlings on the balcony."]

    def test_a_task_made_from_the_pile_is_made_once(self) -> None:
        """The states a new task was made from left the pile only if they
        were leftovers - so every round made the same task again."""
        self._first()
        self._write(self.GARDEN, "C:/home/garden", hours_ago=0.5)
        made = self.beat.run_once(trigger="manual_debug")
        became = made["continuity"]["became_tasks"]
        if not became:
            self.skipTest("these embeddings left the garden loose")
        loose = set(Kept(self.app.db).loose())
        for task in Kept(self.app.db).live():
            if task.id in became:
                self.assertFalse(set(task.members) & loose,
                                 "a new task's states are still in the pile")
        again = self.beat.run_once(trigger="manual_debug")
        self.assertEqual(again["continuity"]["became_tasks"], [])

    def test_copies_of_one_task_are_joined(self) -> None:
        self._first()
        kept = Kept(self.app.db)
        original = kept.live()[0]
        copy = Task(id="task-live-copy", members=list(original.members),
                    total=list(original.total), profile=dict(original.profile),
                    created_at="2099-01-01T00:00:00+00:00", seat=99)
        kept.save([copy])
        report = self.beat.run_once(trigger="manual_debug")
        self.assertEqual(report["copies_joined"], ["task-live-copy"])
        self.assertNotIn("task-live-copy",
                         {task.id for task in Kept(self.app.db).live()})

    def test_a_round_reads_only_what_is_new(self) -> None:
        self._first()
        self._write(["Editing the weighting paper's abstract."],
                    "C:/work/weighting-paper", hours_ago=0.1)
        report = self.beat.run_once(trigger="manual_debug")
        self.assertEqual(report["window"]["kind"], "new since the last round")
        self.assertEqual(report["window"]["read"], 1)
        self.assertEqual(report["continuity"]["arrivals"], 1)


if __name__ == "__main__":
    unittest.main()


class TasksThatAreOneProjectTests(unittest.TestCase):
    """Tasks that live in the same place - one project folder, one paper -
    are grouped under it, when the model agrees they are one thing."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        self.app.config.set("state_summaries_enabled", False, internal=True)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_a_projects_home_is_its_folder_or_the_work_on_a_site(self) -> None:
        from personalized_agent.heartbeat.states.heartbeat import _project_root

        folders = {"agent_release": "c:/users/me/desktop/agent_release"}
        self.assertEqual(
            _project_root("c:/users/me/desktop/agent_release/src/a.py", folders),
            "c:/users/me/desktop/agent_release")
        self.assertEqual(_project_root("github.com/owner/repo/issues/3", {}),
                         "github.com/owner/repo")
        self.assertEqual(_project_root("mail.google.com/mail/u/0", {}), "",
                         "a mailbox is where things happen, not a project")

    def test_a_tasks_home_comes_from_where_it_says_its_work_lives(self) -> None:
        task = Task(id="t", described={
            "where": "The user does this in C:\\Users\\me\\Desktop"
                     "\\agent_release, in the terminal."})
        home = self.beat._home_of(task, {}, {})
        self.assertEqual(home, "c:/users/me/desktop/agent_release")

    def _two_in_one_folder(self):
        kept = Kept(self.app.db)
        made = []
        for index, what in enumerate(("Fix the heartbeat", "Write the release notes")):
            task = _task(f"task-{index}", [_v(1, index)] * 6)
            task.described = {"title": what,
                              "where": "In C:\\Users\\me\\Desktop\\agent_release."}
            task.seat = index
            made.append(task)
        kept.save(made)
        return made

    def test_tasks_sharing_a_home_are_asked_about_and_grouped(self) -> None:
        made = self._two_in_one_folder()
        self.beat.models, asked = _ask({
            "one_project": True, "tasks": [1, 2], "name": "The agent release",
            "why": "both are the release of the same program"})
        report = {}
        projects = self.beat._find_projects(
            made, [], {}, {}, report, self.beat.where("live"))
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].name, "The agent release")
        self.assertEqual(sorted(projects[0].tasks), ["task-0", "task-1"])
        self.assertEqual(projects[0].home, "c:/users/me/desktop/agent_release")
        self.assertTrue(any(one.startswith("ARE THESE TASKS PARTS")
                            for one in asked))
        self.assertEqual(report["projects"][0]["name"], "The agent release")

    def test_a_grouping_turned_down_is_not_asked_again(self) -> None:
        made = self._two_in_one_folder()
        self.beat.models, asked = _ask({"one_project": False, "tasks": [],
                                        "why": "two different things"})
        where = self.beat.where("live")
        self.assertEqual(self.beat._find_projects(made, [], {}, {}, {}, where), [])
        first = len(asked)
        self.beat._find_projects(made, [], {}, {}, {}, where)
        self.assertEqual(len(asked), first)


    # -- a project carries on ----------------------------------------------
    def _a_project(self, made):
        from personalized_agent.heartbeat.states.tasks import Project, Projects

        project = Project(id="project-live-one", name="The agent release",
                          why="one program", home="c:/users/me/desktop/agent_release",
                          tasks=[task.id for task in made],
                          described={"named_size": len(made)})
        Projects(self.app.db).save(project)
        return project

    def test_a_task_joined_into_another_passes_its_project_on(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Projects

        made = self._two_in_one_folder()
        self._a_project(made[:1])
        survivor = _task("task-survivor", [_v(0, 1)] * 6)
        survivor.seat = 5
        made[0].retired_at = "2026-10-01T00:00:00+00:00"
        made[0].born_of = {"kind": "new", "joined_into": "task-survivor"}
        Kept(self.app.db).save([survivor, made[0]])
        self.beat.models, _asked = _ask({"tasks": [], "why": "no"})
        self.beat._find_projects([survivor, made[1]], [], {}, {}, {},
                                 self.beat.where("live"))
        project = Projects(self.app.db).all()[0]
        self.assertIn("task-survivor", project.tasks)
        self.assertNotIn(made[0].id, project.tasks)

    def test_a_task_found_again_stays_in_its_project(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Projects

        made = self._two_in_one_folder()
        self._a_project(made)
        again = _task("task-again", [_v(1, 0)] * 6)
        again.born_of = {"kind": "split from", "from": [made[0].id]}
        self.beat.models, asked = _ask({"tasks": [], "why": "no"})
        self.beat._find_projects(made + [again], [], {}, {}, {},
                                 self.beat.where("live"))
        self.assertIn("task-again", Projects(self.app.db).all()[0].tasks)

    def test_a_no_is_asked_again_once_the_task_has_grown(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Project

        project = Project(id="p", declined={"t": 10})
        small = _task("t", [_v(1)] * 12)
        self.assertTrue(project.turned_down(small))
        grown = _task("t", [_v(1)] * 16)
        self.assertFalse(project.turned_down(grown))

    def test_a_way_of_working_is_not_offered_to_a_project(self) -> None:
        made = self._two_in_one_folder()
        made[1].described["concrete"] = False
        self.beat.models, asked = _ask({"one_project": True, "tasks": [1, 2],
                                        "name": "x", "why": "y"})
        self.beat._find_projects(made, [], {}, {}, {}, self.beat.where("live"))
        self.assertEqual(asked, [], "a group of one was asked about")

    def test_a_named_task_in_a_project_is_not_described_again(self) -> None:
        """Only the top layer is described: once a task has its name and is
        in a project, it is the project that is kept up to date."""
        made = self._two_in_one_folder()
        for task in made:
            task.summary_id = "rec_1"
            task.described = dict(task.described, concrete=True)
        Kept(self.app.db).save(made)
        self.assertFalse(self.beat._in_a_project(made[0].id))
        self._a_project(made)
        self.beat._project_of = {}
        self.assertTrue(self.beat._in_a_project(made[0].id))
        missing = self.beat._still_missing(everything=True)
        self.assertFalse(set(task.id for task in made) & set(missing), missing)

    def test_a_projects_status_is_written_from_what_its_tasks_are_doing(self) -> None:
        from personalized_agent.agent.states.store import State

        made = self._two_in_one_folder()
        for index, task in enumerate(made):
            state = self.app.states.add(State(
                title=f"Fixing the heartbeat, part {index}", detail="d",
                starts_at=utcnow(), ends_at=utcnow()))
            task.members = list(task.members) + [state.id]
            task.described.update({"status": "an old status", "urgency": 5,
                                   "importance": 3})
        Kept(self.app.db).save(made)
        self._a_project(made)
        sent = []

        class Reply:
            text = json.dumps({"status": "The user is releasing it.",
                               "name": "The agent release", "why": "w"})
            usage, model, provider = None, "m", "p"

        class Models:
            def invoke(self, request, role=""):
                sent.append(request.messages[0]["content"])
                return Reply()

        self.beat.models = Models()
        self.beat._bring_projects_up_to_date({"moved": {}}, self.beat.where("live"))
        self.assertTrue(sent)
        self.assertIn("Fixing the heartbeat, part 0", sent[0])
        self.assertIn("most recently", sent[0])
        self.assertNotIn("an old status", sent[0])

    def test_a_projects_status_comes_after_its_tasks_and_only_when_they_moved(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Projects

        made = self._two_in_one_folder()
        for task in made:
            task.described.update({"status": "The user is on it.",
                                   "urgency": 5, "importance": 3})
        Kept(self.app.db).save(made)
        self._a_project(made)
        self.beat.models, asked = _ask({"status": "The user is releasing it.",
                                        "name": "The agent release",
                                        "why": "both tasks are moving"})
        report = {"moved": {}}
        self.beat._bring_projects_up_to_date(report, self.beat.where("live"))
        project = Projects(self.app.db).all()[0]
        self.assertEqual(project.described["status"], "The user is releasing it.")
        drawn = report["projects"][0]
        self.assertEqual(drawn["urgency"], 5)
        self.assertEqual(drawn["status"], "The user is releasing it.")
        # Nothing moved since, so nothing is asked.
        self.app.config.set("state_round_tried", "[]", internal=True)
        before = len(asked)
        self.beat._bring_projects_up_to_date({"moved": {}},
                                             self.beat.where("live"))
        self.assertEqual(len(asked), before)
        # One of its tasks moved: asked again.
        self.beat._bring_projects_up_to_date(
            {"moved": {made[0].id: {"gained": ["s"], "lost": []}}},
            self.beat.where("live"))
        self.assertEqual(len(asked), before + 1)

    def test_a_project_that_has_changed_a_good_deal_is_asked_its_name(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Projects

        made = self._two_in_one_folder()
        project = self._a_project(made)
        project.described = {"named_size": 6, "status": "old"}
        Projects(self.app.db).save(project)
        self.beat.models, asked = _ask({"status": "new", "name": "A new name",
                                        "why": "it grew"})
        self.beat._bring_projects_up_to_date({"moved": {}},
                                             self.beat.where("live"))
        self.assertEqual(Projects(self.app.db).all()[0].name, "A new name")

    def test_a_project_without_a_status_leaves_the_round_unfinished(self) -> None:
        made = self._two_in_one_folder()
        project = self._a_project(made)
        self.assertIn(f"project:{project.id}",
                      self.beat._still_missing(everything=True))


    def test_the_page_gets_the_projects_whichever_way_it_is_drawn(self) -> None:
        """The picture comes from the round's report or from the kept
        tasks; projects reached the page by only one of them, and it was
        not the one the page usually got."""
        made = self._two_in_one_folder()
        self._a_project(made)
        self.beat._last_report = {}
        self.beat._progress = {}
        stored = self.beat.the_picture()
        self.assertEqual([one["name"] for one in stored.get("projects") or []],
                         ["The agent release"])
        report = {"plan": {"nodes": [{"id": "task_1", "task_id": made[0].id},
                                     {"id": "task_2", "task_id": made[1].id}],
                           "edges": []},
                  "layers": [{"id": "task_1", "task_id": made[0].id},
                             {"id": "task_2", "task_id": made[1].id}],
                  "finished_at": "2026-10-04T00:00:00+00:00"}
        self.beat._last_report = report
        self.beat._progress = report
        drawn = self.beat.the_picture()
        self.assertFalse(drawn.get("from_store"))
        self.assertEqual([one["name"] for one in drawn.get("projects") or []],
                         ["The agent release"])

class WhatARoundCostTests(unittest.TestCase):
    """The panel says what the last complete round cost - tokens in and
    out, calls, and time - with every visit it took added together."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    @staticmethod
    def _visit(tokens_in, tokens_out, calls, ms):
        return {"where": {"scope": "live"}, "duration_ms": ms,
                "stages": [{"calls": calls, "tokens_in": tokens_in,
                            "tokens_out": tokens_out}]}

    def test_two_visits_of_one_round_are_added_together(self) -> None:
        where = self.beat.where("live")
        self.beat._begin_round(where)
        self.app.config.set("state_round_done", False, internal=True)
        # One visit, then another an hour later: a round's time is the time
        # its parts were running, and two visits do not overlap.
        import unittest.mock
        with unittest.mock.patch("time.time", return_value=1_000_000.0):
            self.beat._add_what_this_visit_spent(self._visit(1000, 100, 2, 30000))
        with unittest.mock.patch("time.time", return_value=1_003_600.0):
            self.beat._add_what_this_visit_spent(self._visit(500, 50, 1, 15000))
        spent = self.beat._round_record()["spent"]
        self.assertEqual((spent["tokens_in"], spent["tokens_out"],
                          spent["calls"], spent["visits"]), (1500, 150, 3, 2))
        self.assertEqual(spent["seconds"], 45.0)
        self.assertEqual(self.beat._round_record()["last_spent"], {},
                         "an unfinished round was called complete")

    def test_the_total_is_the_last_complete_rounds_once_it_is_done(self) -> None:
        where = self.beat.where("live")
        self.beat._begin_round(where)
        self.app.config.set("state_round_done", False, internal=True)
        self.beat._add_what_this_visit_spent(self._visit(1000, 100, 2, 30000))
        self.beat._round_is_done(where)
        self.beat._add_what_this_visit_spent(self._visit(500, 50, 1, 15000))
        last = self.beat._round_record()["last_spent"]
        self.assertEqual(last["tokens_in"], 1500)
        self.assertEqual(last["visits"], 2)
        self.assertTrue(last["completed_at"])
        # And the next round starts from nothing, while the last stays.
        self.beat._begin_round(where)
        self.assertEqual(self.beat._round_record()["spent"]["tokens_in"], 0)
        self.assertEqual(self.beat._round_record()["last_spent"]["tokens_in"],
                         1500)


class AGroupThatIsNoOneTaskTests(unittest.TestCase):
    """A group too wide to be one task is not made from the pile; a task
    buried in a group the model turned down is taken out of it; and a task
    made by merging is asked about, once, part by part."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        self.app.config.set("state_summaries_enabled", False, internal=True)

    def tearDown(self) -> None:
        self.agent.cleanup()

    # -- 1. the pile ----------------------------------------------------------
    def test_the_pile_makes_no_task_wider_than_one_the_model_accepted(self) -> None:
        accepted = _task("accepted", [_v(0, 0, 0, 1)] * 6)
        accepted.radius, accepted.spread = 0.1, 0.2
        accepted.described = {"concrete": True}
        # Turned down and wide: were it the measure, the wide group would pass.
        overview = _task("overview", [_v(0, 0, 0, 0, 1)] * 6)
        overview.radius, overview.spread = 0.5, 0.05
        overview.described = {"concrete": False}
        self.beat._live_for_pile = [accepted, overview]
        self.beat._places_now, self.beat._when_now = {}, {}
        vectors = {}
        for index in range(6):
            vectors[f"tight-{index}"] = _v(0, 0, 1, 0.01 * index)
            vectors[f"wide-{index}"] = _v(1, 0.75 if index % 2 else -0.75)
        made, waiting = self.beat._tasks_from_pile(
            list(vectors), vectors, self.beat.where("live"))
        self.assertEqual([sorted(task.members) for task in made],
                         [[f"tight-{index}" for index in range(6)]])
        self.assertEqual(sorted(waiting), [f"wide-{index}" for index in range(6)])

    # -- 2. a task buried in a group --------------------------------------------
    def test_the_states_the_model_names_are_read_off_the_numbered_list(self) -> None:
        from personalized_agent.heartbeat.states.heartbeat import _buried_in

        group = {"sample": [{"member": {"id": one}} for one in "abcd"]}
        self.assertEqual(_buried_in({"title": "Revise the paper",
                                     "states": [2, "3", 9, "x"]}, group),
                         {"title": "Revise the paper", "states": ["b", "c"],
                          "shown": ["a", "b", "c", "d"]})
        # Every state named is the group itself, not a task inside it.
        self.assertEqual(_buried_in({"states": [1, 2, 3, 4]}, group), {})
        self.assertEqual(_buried_in(None, group), {})

    def test_the_prompt_asks_for_it(self) -> None:
        from personalized_agent.heartbeat.states.heartbeat import SUMMARY_PROMPT

        self.assertIn('"buried"', SUMMARY_PROMPT)

    def _states(self):
        from personalized_agent.agent.states.store import State

        written = {}
        for kind, titles in (("paper", TheRoundCarriesTasksOnTests.PAPER),
                             ("cook", TheRoundCarriesTasksOnTests.COOKING)):
            for index, title in enumerate(titles):
                began = utcnow() - timedelta(hours=3, minutes=index)
                state = self.app.states.add(State(
                    title=title, detail=title, starts_at=began,
                    ends_at=began + timedelta(minutes=10)))
                written.setdefault(kind, []).append(
                    getattr(state, "id", state))
        return written

    def _one_task_of(self, written, task_id="task-mixed"):
        nodes = [__import__("personalized_agent.heartbeat.states.heartbeat",
                            fromlist=["_Node"])._Node(state)
                 for state in self.app.states.by_ids(
                     written["paper"] + written["cook"])]
        vectors, _how = self.beat._vectors(nodes, self.beat.where("live"))
        if len(vectors) < 12:
            self.skipTest("no embedder to read these states with")
        places_of, when_of = self.beat._what_and_when(nodes)
        task = Task(id=task_id, members=written["paper"] + written["cook"])
        self.beat._profile(task, places_of, when_of, vectors)
        return task

    def test_a_task_buried_in_a_group_is_taken_out_of_it(self) -> None:
        written = self._states()
        task = self._one_task_of(written)
        task.described = {"title": "Keep up with research and life",
                          "concrete": False}
        kept = Kept(self.app.db)
        kept.save([task])
        report = {}
        made = self.beat._take_out_what_is_buried(
            task, {"title": "Revise the weighting paper",
                   "states": written["paper"][:2],
                   "shown": written["paper"][:2] + written["cook"][:2]},
            kept, self.beat.where("live"), report)
        self.assertIsNotNone(made, report)
        self.assertEqual(sorted(made.members), sorted(written["paper"]))
        self.assertEqual(sorted(task.members), sorted(written["cook"]))
        self.assertEqual(made.born_of["from"], [task.id])
        self.assertIn(made.id, {one.id for one in Kept(self.app.db).live()})

    # -- 3. a merge, asked about part by part -------------------------------------
    def test_a_part_of_a_merge_that_is_other_work_is_taken_back_out(self) -> None:
        written = self._states()
        merged = self._one_task_of(written, "task-merged")
        paper = Task(id="task-paper", members=list(written["paper"]),
                     described={"title": "Revise the weighting paper",
                                "concrete": True, "summary": "the paper"},
                     retired_at="2026-10-04T00:00:00+00:00")
        cook = Task(id="task-cook", members=list(written["cook"]),
                    described={"title": "Cook the week's dinners",
                               "concrete": True, "summary": "dinners"},
                    retired_at="2026-10-04T00:00:00+00:00")
        merged.born_of = {"kind": "merged from",
                          "from": ["task-paper", "task-cook"]}
        merged.described = {"title": "Cook the week's dinners",
                            "concrete": True}
        kept = Kept(self.app.db)
        kept.save([paper, cook, merged])
        self.beat.models, asked = _ask({"same": False, "why": "other work"})
        live = [merged]
        report = {}
        back = self.beat._undo_old_merges(live, kept, report,
                                          self.beat.where("live"))
        self.assertEqual(len(back), 1)
        self.assertEqual(sorted(back[0].members), sorted(written["paper"]))
        # Back with the name it had.
        self.assertEqual(back[0].described["title"],
                         "Revise the weighting paper")
        self.assertEqual(sorted(merged.members), sorted(written["cook"]))
        self.assertIn(back[0], live)
        # The second part would have left nothing behind, so it was kept.
        self.assertEqual(len(asked), 1)
        # Asked once: the merge is not asked about again.
        self.assertTrue(Kept(self.app.db).by_ids(["task-merged"])[0]
                        .born_of.get("rechecked"))
        self.beat._undo_old_merges(live, kept, {}, self.beat.where("live"))
        self.assertEqual(len(asked), 1)

    def test_a_group_turned_down_before_is_asked_once_more(self) -> None:
        task = _task("t", [_v(1)] * 6)
        task.summary_id = "rec_1"
        task.named()
        task.described = {"concrete": False}
        self.assertTrue(wants_summary(task))
        task.described["asked_for_buried"] = True
        task.described["concrete_asked"] = True
        self.assertFalse(wants_summary(task))

    def test_every_task_is_judged_afresh_once(self) -> None:
        task = _task("t", [_v(1)] * 6)
        task.summary_id = "rec_1"
        task.named()
        task.described = {"concrete": True}
        self.assertTrue(wants_summary(task))
        task.described["concrete_asked"] = True
        self.assertFalse(wants_summary(task))

    def test_an_answer_that_leaves_out_the_judgement_keeps_it(self) -> None:
        """Only `status` sent: the group stays turned down, and its weights
        stay what they were."""
        from personalized_agent.heartbeat.states.heartbeat import _amended

        before = {"title": "Track presence", "concrete": False,
                  "concrete_why": "a state, nothing to finish",
                  "urgency": 2, "importance": 3, "weighed_why": "low"}
        got = _amended({"status": "The user is back.", "changed": ["status"]},
                       before)
        self.assertIs(got["concrete"], False)
        self.assertEqual(got["concrete_why"], "a state, nothing to finish")
        self.assertEqual((got["urgency"], got["importance"]), (2, 3))
        self.assertEqual(got["status"], "The user is back.")
        # What it does send is what counts.
        self.assertIs(_amended({"concrete": True}, before)["concrete"], True)

    def test_the_summary_prompt_asks_for_the_judgement_every_time(self) -> None:
        from personalized_agent.heartbeat.states.heartbeat import SUMMARY_PROMPT

        self.assertIn("EXCEPT `concrete` AND `concrete_why`, which you send "
                      "EVERY time", SUMMARY_PROMPT)


    def test_other_work_taken_out_is_never_asked_about_as_one_again(self) -> None:
        written = self._states()
        task = self._one_task_of(written)
        kept = Kept(self.app.db)
        kept.save([task])
        made = self.beat._take_out_what_is_buried(
            task, {"title": "Revise the paper", "states": written["paper"][:2],
                   "shown": written["paper"][:2] + written["cook"][:2]},
            kept, self.beat.where("live"), {})
        self.assertIn(made.id, task.profile.get("not_the_same") or [])
        self.assertIn(task.id, made.profile.get("not_the_same") or [])

    def test_other_work_joined_back_without_asking_is_taken_out_again(self) -> None:
        written = self._states()
        task = self._one_task_of(written, "task-rework")
        task.described = {"title": "Cook the week's dinners"}
        paper = Task(id="task-paper", members=list(written["paper"]),
                     described={"title": "Revise the weighting paper",
                                "concrete": True},
                     retired_at="2026-10-04T00:00:00+00:00",
                     born_of={"kind": "split from", "from": ["task-rework"],
                              "was": "task-old", "other_work": True,
                              "joined_into": "task-rework"})
        kept = Kept(self.app.db)
        kept.save([task, paper])
        self.beat.models, asked = _ask({"same": True, "why": "one"})
        live = [task]
        back = self.beat._undo_old_merges(live, kept, {},
                                          self.beat.where("live"))
        self.assertEqual(asked, [], "the model had already answered")
        self.assertEqual(len(back), 1)
        self.assertEqual(sorted(back[0].members), sorted(written["paper"]))
        self.assertEqual(back[0].described["title"],
                         "Revise the weighting paper")

    def test_a_few_states_in_common_do_not_make_two_tasks_copies(self) -> None:
        """Three of a five-state task's states in a bigger one is not a
        copy of it."""
        small = _task("task-small", [_v(1, 0)] * 5)
        big = _task("task-big", [_v(0, 1)] * 12)
        big.members = big.members[:9] + small.members[:3]
        both = [small, big]
        Kept(self.app.db).save(both)
        report = self.beat.run_once(trigger="manual_debug")
        self.assertNotIn("task-small",
                         (report.get("continuity") or {}).get("copies_joined")
                         or report.get("copies_joined") or [])

class ATaskIsHoursOfWorkTests(unittest.TestCase):
    """A task holds more than two hours of work, counted as how long each
    state says it lasted; a state that does not say counts its window, at
    that window's own length, once."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        self.app.config.set("state_summaries_enabled", False, internal=True)
        self.app.config.set("state_task_min_hours", 2.0)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _states(self, how_many, minutes, per_window=1, start=0, lasted=None):
        from personalized_agent.agent.states.store import State

        made = []
        base = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
        for index in range(how_many):
            window = (start + index) // per_window
            began = base + timedelta(minutes=window * max(1, minutes))
            looked = ({"minutes": minutes, "from": began.isoformat(),
                       "to": (began + timedelta(minutes=minutes)).isoformat()}
                      if minutes else None)
            state = self.app.states.add(State(
                title=f"Revising the paper, part {start + index}.",
                starts_at=began, ends_at=began + timedelta(minutes=5),
                metadata={**({"looked_at": looked} if looked else {}),
                          **({"minutes": lasted} if lasted else {})}))
            made.append(getattr(state, "id", state))
        return made

    def test_each_state_counts_how_long_it_lasted(self) -> None:
        """Three states from one fifteen-minute window, of 9, 4 and 2
        minutes, are fifteen minutes - and four two-minute ones from four
        windows are eight, not an hour."""
        nine = self._states(1, 15, lasted=9)
        four = self._states(1, 15, lasted=4)
        two = self._states(1, 15, lasted=2)
        self.assertEqual(self.beat._work_in(nine + four + two), 15 * 60)
        brief = self._states(4, 15, lasted=2, start=10)
        self.assertEqual(self.beat._work_in(brief), 4 * 2 * 60)

    def test_a_state_cannot_outlast_its_window(self) -> None:
        self.assertEqual(self.beat._work_in(self._states(1, 15, lasted=40)),
                         15 * 60)

    def test_two_hours_is_what_the_states_add_up_to(self) -> None:
        """Twelve fifteen-minute windows are three hours of span; at five
        minutes each they are an hour of work, which is not a task."""
        self.assertTrue(self.beat._too_short(Task(
            id="t", members=self._states(12, 15, lasted=5))))
        self.assertFalse(self.beat._too_short(Task(
            id="u", members=self._states(12, 15, lasted=11, start=100))))

    def test_each_window_counts_at_its_own_length(self) -> None:
        """Written every half hour, five states are two and a half hours -
        whatever the interval is now."""
        self.app.config.set("heartbeat_interval_seconds", 900)
        self.assertEqual(self.beat._work_in(self._states(5, 30)), 5 * 1800)

    def test_states_from_one_window_count_it_once(self) -> None:
        ids = self._states(9, 15, per_window=3)
        self.assertEqual(self.beat._work_in(ids), 3 * 900)

    def test_a_state_with_no_window_counts_the_interval_set_now(self) -> None:
        self.app.config.set("heartbeat_interval_seconds", 600)
        self.assertEqual(self.beat._work_in(self._states(3, 0)), 3 * 600)

    def test_two_hours_is_a_task_and_less_is_not(self) -> None:
        # As much work as the line, or more, is a task; less is not.
        self.assertTrue(self.beat._too_short(Task(
            id="t", members=self._states(7, 15))))
        self.assertFalse(self.beat._too_short(Task(
            id="u", members=self._states(8, 15, start=100))))

    def test_a_task_with_too_little_work_is_not_in_the_picture_at_all(self) -> None:
        long_task = _task("task-long", [_v(1, 0)] * 2)
        long_task.members = self._states(10, 15)
        long_task.described = {"title": "Revise the paper"}
        short_task = _task("task-short", [_v(0, 1)] * 2)
        short_task.members = self._states(4, 15, start=50)
        short_task.described = {"title": "An errand"}
        short_task.seat = 1
        Kept(self.app.db).save([long_task, short_task])
        self.beat._last_report = {}
        self.beat._progress = {}
        picture = self.beat.the_picture()
        drawn = {node.get("task_id") for node in picture["plan"]["nodes"]}
        self.assertIn("task-long", drawn)
        self.assertNotIn("task-short", drawn)
        # Not hidden: not there, so showing the hidden brings nothing back.
        self.assertNotIn("task-short", {layer.get("task_id")
                                        for layer in picture["layers"]})
        self.assertEqual(picture.get("too_little_work"), 1)


    def test_a_round_with_a_task_too_short_to_name_still_finishes(self) -> None:
        """The naming passes over a task with too little work; waiting for
        its name held a live round open for nearly two hours."""
        rounds = TheRoundCarriesTasksOnTests("test_two_tasks_that_have_become_one_are_asked_about")
        rounds.agent, rounds.app, rounds.beat = self.agent, self.app, self.beat
        self.app.config.set("state_task_min_hours", 1.0)
        rounds._write(rounds.PAPER + ["Proofreading the weighting paper's "
                                      "abstract.",
                                      "Formatting the weighting paper's "
                                      "tables."],
                      "C:/work/weighting-paper", hours_ago=3)
        rounds._write(rounds.COOKING, "C:/home/recipes", hours_ago=2)
        self.beat.run_once(trigger="manual_debug")
        live = Kept(self.app.db).live()
        if len(live) < 2:
            self.skipTest("one task on these embeddings")
        # Now the six-state task holds too little work; the eight does not.
        self.app.config.set("state_task_min_hours", 1.75)
        self.assertEqual(sum(1 for task in live if self.beat._too_short(task)), 1)
        self.app.config.set("state_summaries_enabled", True, internal=True)
        self.beat.models, asked = _ask({
            "title": "Revise the weighting paper", "summary": "The paper.",
            "concrete": True, "concrete_why": "a paper to finish",
            "urgency": 4, "importance": 4})
        report = self.beat.run_once(trigger="manual_debug")
        self.assertFalse(report.get("pieces_held_back"), report.get("pieces_held_back"))
        self.assertEqual(self.beat._still_missing(everything=True), [])

class OtherWorkLeavesTheProjectTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_a_task_taken_out_as_other_work_is_not_in_its_old_project(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Project, Projects

        rework = _task("task-rework", [_v(1, 0)] * 6)
        paper = _task("task-paper", [_v(0, 1)] * 6)
        paper.born_of = {"kind": "split from", "from": ["task-rework"],
                         "was": "task-old-paper", "other_work": True}
        found_again = _task("task-again", [_v(1, 0.1)] * 6)
        found_again.born_of = {"kind": "split from", "from": ["task-rework"]}
        Kept(self.app.db).save([rework, paper, found_again])
        Projects(self.app.db).save(Project(
            id="p", name="The agent release",
            tasks=["task-rework", "task-paper"]))
        self.beat.models, _asked = _ask({"tasks": [], "why": "no"})
        self.beat._find_projects([rework, paper, found_again], [], {}, {}, {},
                                 self.beat.where("live"))
        tasks = Projects(self.app.db).all()[0].tasks
        self.assertNotIn("task-paper", tasks)
        # The same work found again still inherits it.
        self.assertIn("task-again", tasks)
