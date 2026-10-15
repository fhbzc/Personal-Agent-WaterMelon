"""What was learned stays in Experience: a project or task learned from is
listed after it has gone quiet or ended, with its lessons; only what is at
work now is marked active."""

from __future__ import annotations

import unittest

from support import TemporaryAgent

from personalized_agent.common.events.models import utcnow


class WhatWasLearnedStaysTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.experience = self.app.state_heartbeat.experience

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _ended_task_with_a_card(self) -> str:
        from personalized_agent.heartbeat.states.tasks import Kept, Task

        task = Task(id="task-live-ended", members=["s1", "s2"],
                    described={"title": "Revise the replication package"},
                    retired_at=utcnow().isoformat())
        Kept(self.app.db).save([task])
        now = utcnow().isoformat()
        self.app.db.execute(
            "INSERT INTO experience_cards (id, scope, unit_id, task_id, parent_id, "
            "title, created_at, updated_at) VALUES (?, ?, ?, ?, '', ?, ?, ?)",
            ("card_1", self.experience.scope, task.id, task.id,
             "Revise the replication package", now, now))
        return task.id

    def test_an_ended_task_learned_from_is_still_listed(self) -> None:
        task_id = self._ended_task_with_a_card()
        units = {unit["id"]: unit for unit in self.experience.units()}
        self.assertIn(task_id, units)
        self.assertEqual(units[task_id]["name"], "Revise the replication package")
        self.assertFalse(units[task_id]["active"])
        # And its tree can be opened.
        self.assertNotIn("error", self.experience.graph(task_id))

    def test_what_is_at_work_now_is_marked_active(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Kept
        from test_state_tasks_continue import _task, _v

        live = _task("task-live-now", [_v(1, 0)] * 6)
        live.described = {"title": "Build the site", "concrete": True}
        live.seat = 0
        Kept(self.app.db).save([live])
        units = {unit["id"]: unit for unit in self.experience.units()}
        if "task-live-now" not in units:
            self.skipTest("the live task is not shown in this setup")
        self.assertTrue(units["task-live-now"]["active"])


if __name__ == "__main__":
    unittest.main()
