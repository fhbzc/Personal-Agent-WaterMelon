"""Routine is not a task. The review that writes a state says when it is the
part of a day that is no piece of work - meals, rest, a video, a chat with a
friend - and the grouping leaves those out: grouped with the rest, they made
tasks of their own and took work into them."""

from __future__ import annotations

import unittest
from datetime import timedelta

from support import TemporaryAgent

from personalized_agent.common.events.models import utcnow

WORK = [("They are rebuilding the replication package for the soil paper.",
         "The census fetch script and the figure notebook."),
        ("They are fixing the replication code for the soil paper.",
         "The weighting step in the fetch script."),
        ("They are checking the replication figures against the soil paper.",
         "The figure notebook against the published figures."),
        ("They are still rebuilding the soil paper's replication package.",
         "More of the census fetching."),
        ("They are working through the soil paper's replication scripts.",
         "fetch.py and the notebook again."),
        ("They are rerunning the replication package for the soil paper.",
         "The whole package end to end.")]
ROUTINE = [("They are watching cooking videos.", "One video after another."),
           ("They are having lunch and reading the news.", "A news site."),
           ("They are chatting with a friend about the weekend.", "A chat app."),
           ("They are watching another cooking video.", "More videos."),
           ("They are resting with music on.", "A music player."),
           ("They are reading the evening news.", "The same news site.")]


LEISURE = [("They are watching a cooking routine on a food channel.", "One video after another."),
           ("They are watching another cooking routine.", "More videos."),
           ("They are watching a baking routine this evening.", "A long video."),
           ("They are watching cooking routines after dinner.", "Videos, one by one."),
           ("They are watching a pasta routine.", "A short video."),
           ("They are watching a cooking routine again.", "The same channel.")]


class TheReviewSaysWhichIsRoutineTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_asked_for_and_kept(self) -> None:
        from personalized_agent.heartbeat.core.heartbeat import STEP_PROMPTS
        # In the answer's own shape - a field only described above is one a
        # model leaves out - and defined in full in the instruction.
        self.assertIn('"kind": "work, leisure or routine"', STEP_PROMPTS["step_state"]["returns"])
        asked = STEP_PROMPTS["step_state"]["instruction"]
        self.assertIn('Give every state a "kind"', asked)
        self.assertIn('"kind": "routine"', asked)
        self.assertIn('"leisure"', asked)
        written = self.app.heartbeat._write_states("task_1", [
            {"title": "They are having lunch.", "detail": "A meal.",
             "kind": "Routine", "minutes": 20},
            {"title": "They are writing the soil paper.", "detail": "Section 3.",
             "kind": "work", "minutes": 40},
            {"title": "They are watching cooking videos.", "detail": "Videos.",
             "kind": "leisure", "minutes": 30},
            {"title": "They are reading.", "detail": "Something.", "minutes": 5}])
        kinds = [self.app.states.get(one["id"]).metadata.get("kind") for one in written]
        # As said - and none where nothing was said, told apart from work.
        self.assertEqual(kinds, ["routine", "work", "leisure", None])


class LeftOutOfTheTasksTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _write(self, rows, kind=""):
        from personalized_agent.agent.states.store import State
        ids = []
        for index, (title, detail) in enumerate(rows):
            at = utcnow() - timedelta(hours=1 + index)
            ids.append(self.app.states.add(State(
                title=title, detail=detail, starts_at=at,
                ends_at=at + timedelta(minutes=15),
                metadata={"kind": kind} if kind else {})).id)
        return ids

    def test_no_task_holds_a_routine_state(self) -> None:
        work = self._write(WORK)
        routine = self._write(ROUTINE, kind="routine")
        report = self.app.state_heartbeat.run_once(trigger="manual_debug")
        self.assertEqual(report["routine"]["states"], len(ROUTINE))
        grouped = {one for layer in report.get("layers") or []
                   for one in layer.get("states") or []}
        self.assertFalse(grouped & set(routine), "a routine state was grouped")
        self.assertTrue(grouped & set(work), "the work was grouped")
        self.assertFalse(set(routine) & set(report.get("loose") or []),
                         "routine is not left waiting to become a task")

    def test_leisure_is_grouped_but_never_with_work(self) -> None:
        work = set(self._write(WORK, kind="work"))
        # Cooking videos, as close in words to the replication work as the
        # embedding allows a pastime to be - and watched as a pastime.
        enjoyed = set(self._write([(title.replace("routine", "video"), detail)
                                   for title, detail in LEISURE], kind="leisure"))
        report = self.app.state_heartbeat.run_once(trigger="manual_debug")
        tasks = [set(layer.get("states") or []) for layer in report.get("layers") or []]
        grouped = set().union(*tasks) if tasks else set()
        self.assertTrue(grouped & work, "the work was grouped")
        self.assertTrue(grouped & enjoyed, "leisure is grouped too - it is not routine")
        for members in tasks:
            self.assertFalse(members & work and members & enjoyed,
                             "a task held both leisure and work")


class APagesKindTests(unittest.TestCase):
    """A task of leisure reaches the recommendations as one - by the rule a
    pastime is judged by - however its summary judged it."""

    def test_a_lasting_leisure_task_is_a_subject_of_leisure(self) -> None:
        import json
        from datetime import datetime, timezone
        from personalized_agent.heartbeat.connections import recommend
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        db = agent.app.db
        from personalized_agent.agent.states.store import State
        now = datetime.now(timezone.utc)

        def task(ident, title, kind, days, concrete):
            members = []
            for back in days:
                at = now - timedelta(days=back, hours=1)
                members.append(agent.app.states.add(State(
                    title=f"{title} {back}", detail="d", starts_at=at,
                    ends_at=at + timedelta(minutes=20), metadata={"kind": kind})).id)
            db.execute("INSERT INTO state_tasks (id, number, members, total, scope, created_at, "
                       "updated_at, described) VALUES (?, 1, ?, ?, 'live', ?, ?, ?)",
                       (ident, json.dumps(members), len(members), now.isoformat(),
                        now.isoformat(), json.dumps({"title": title, "concrete": concrete,
                                                     "summary": "s"})))
        task("t_films", "Watching thrillers", "leisure", [0, 4, 9], False)
        task("t_once", "One film night", "leisure", [0, 0, 1], True)
        task("t_soil", "Soil paper", "work", [0], True)
        found = {one["title"]: one for one in recommend.subjects(db, now=now)
                 if one["key"].startswith("task:")}
        self.assertIn("Watching thrillers", found, "kept, though judged not a task")
        self.assertTrue(found["Watching thrillers"]["leisure"])
        self.assertNotIn("One film night", found, "a pastime counts once it lasts")
        self.assertFalse(found["Soil paper"]["leisure"])


if __name__ == "__main__":
    unittest.main()
