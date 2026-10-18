"""No upgrade regroups the user's tasks.

One build recorded how places are read among the settings of the tree, so
starting it rebuilt every task from the tree: on a real calendar twelve
pieces of work were merged into one and most states left in none. These
hold the three answers: a setting the record does not have is not a change;
how places are read is no setting of the tree; and that one rebuild is
undone, once, where it happened."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

from support import TemporaryAgent


class NotAChangeTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.beat = self.agent.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_places_are_no_setting_of_the_tree(self) -> None:
        self.assertNotIn("places_read_as", self.beat._shape())

    def test_a_setting_the_record_lacks_or_has_extra_is_no_change(self) -> None:
        shape = self.beat._shape()
        older = {k: v for k, v in shape.items() if k != "enough_children"}
        self.assertEqual(self.beat._reshaped(older), [])
        self.assertEqual(self.beat._reshaped(dict(shape, places_read_as=2)), [])
        self.assertEqual(self.beat._reshaped({}), [])
        changed = dict(shape, min_nodes=int(shape["min_nodes"]) + 3)
        self.assertEqual(self.beat._reshaped(changed), ["min_nodes"])


class UndoneOnceTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.db = self.app.db
        self.beat = self.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _task(self, ident, title, members, retired_at=None):
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "INSERT INTO state_tasks (id, number, members, total, scope, created_at, updated_at, "
            "retired_at, described) VALUES (?, 1, ?, ?, 'live', ?, ?, ?, ?)",
            (ident, json.dumps(members), len(members), now, now, retired_at,
             json.dumps({"title": title})))

    def _run(self, ident, at, shape, lineage):
        self.db.execute(
            "INSERT INTO state_task_runs (id, ran_at, states, rebuilt, scope, shape, lineage) "
            "VALUES (?, ?, 10, 1, 'live', ?, ?)",
            (ident, at.isoformat(), json.dumps(shape), json.dumps(lineage)))

    def test_the_forced_regroup_is_undone(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Kept
        at = datetime.now(timezone.utc) - timedelta(hours=1)
        older = at - timedelta(days=9)
        shape = {k: v for k, v in self.beat._shape().items()}
        self._run("run_old", older, shape, [])
        self._task("t_soil", "Revise the soil paper", ["s1", "s2"],
                   retired_at=(at - timedelta(seconds=60)).isoformat())
        self._task("t_bees", "Count the beehives", ["s3", "s4"],
                   retired_at=at.isoformat())
        self._task("t_kept_retired", "An old task", ["s9"],
                   retired_at=(at - timedelta(days=3)).isoformat())
        self._task("t_blob", "Everything at once", ["s1", "s2", "s3", "s4"])
        self._run("run_forced", at, dict(shape, places_read_as=2),
                  [{"task": "t_blob", "kind": "merged from", "from": ["t_soil", "t_bees"]}])
        kept = Kept(self.db, "live")
        self.beat._undo_the_places_regroup(kept)
        live = {task.id for task in kept.live()}
        self.assertEqual(live, {"t_soil", "t_bees"})
        self.assertEqual(kept.shape_at_last_rebuild(), shape, "its record no longer counts")
        self.assertEqual(self.beat._reshaped(kept.shape_at_last_rebuild()), [])
        # Once: a later rebuild of the tasks is left alone.
        self._task("t_new", "A new task", ["s5"])
        self.beat._undo_the_places_regroup(kept)
        self.assertIn("t_new", {task.id for task in kept.live()})

    def test_nothing_to_undo_where_it_never_ran(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Kept
        at = datetime.now(timezone.utc) - timedelta(hours=1)
        self._run("run_plain", at, self.beat._shape(), [])
        self._task("t_live", "Water the seedlings", ["s1"])
        kept = Kept(self.db, "live")
        self.beat._undo_the_places_regroup(kept)
        self.assertEqual({task.id for task in kept.live()}, {"t_live"})
        self.assertTrue(self.app.config.get("state_places_regroup_undone"))


class LeisureByAllItsStatesTests(unittest.TestCase):

    def test_a_few_said_states_do_not_decide_a_task(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Task, leaning
        members = [f"s{n}" for n in range(200)]
        task = Task(id="t", members=members)
        said = {f"s{n}": True for n in range(8)}
        said.update({f"s{n}": False for n in range(8, 14)})
        self.assertIsNone(leaning(task, said), "8 against 6 of 200 is no kind")
        said.update({f"s{n}": True for n in range(14, 120)})
        self.assertTrue(leaning(task, said))
        work = {f"s{n}": False for n in range(150)}
        self.assertFalse(leaning(task, work))


if __name__ == "__main__":
    unittest.main()
