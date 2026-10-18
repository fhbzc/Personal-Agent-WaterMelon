"""Every task's states judged again, once, by the rules now: a state that
would not be placed in its task today goes to the pile. Nothing is retired,
merged or regrouped; a task that would lose more than half of itself is left
alone; what moved is kept, to be read and undone."""

from __future__ import annotations

import unittest
from datetime import timedelta
from unittest import mock

from support import TemporaryAgent

from personalized_agent.common.events.models import utcnow


def _v(*values):
    out = list(values) + [0.0] * (8 - len(values))
    size = sum(one * one for one in out) ** 0.5 or 1.0
    return [one / size for one in out]


class RecheckTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _states(self, titles):
        from personalized_agent.agent.states.store import State
        ids = []
        for index, title in enumerate(titles):
            at = utcnow() - timedelta(hours=len(titles) - index)
            ids.append(self.app.states.add(State(
                title=title, detail="d", starts_at=at, ends_at=at + timedelta(minutes=20))).id)
        return ids

    def _task(self, task_id, members, vectors):
        from personalized_agent.heartbeat.states.tasks import Kept, Task
        task = Task(id=task_id, created_at=utcnow().isoformat())
        task.start_profile()
        for one in members:
            task.take(one, vectors[one], (), "", "")
        task.described = {"title": task_id}
        kept = Kept(self.app.db, "live")
        kept.save([task])
        return kept

    def _run(self, kept, vectors):
        report = {}
        with mock.patch.object(self.beat, "_vectors", return_value=(vectors, "test")):
            self.beat._recheck_members_once(kept.live(), kept, self.beat.where("live"), report)
        return report

    def test_the_misfits_go_to_the_pile_and_the_task_stays(self) -> None:
        hives = self._states([f"They are inspecting beehive {n}." for n in range(8)])
        reading = self._states(["They are reading a news feed.", "They are reading a chat."])
        vectors = {one: _v(1, 0.05 * n) for n, one in enumerate(hives)}
        vectors.update({one: _v(0, 1) for one in reading})
        kept = self._task("t_hives", hives + reading, vectors)
        report = self._run(kept, vectors)
        live = {task.id: task for task in kept.live()}
        self.assertEqual(set(live), {"t_hives"}, "no task retired, made or merged")
        self.assertEqual(set(live["t_hives"].members), set(hives))
        self.assertEqual(set(kept.loose()), set(reading))
        self.assertEqual(set(report["rechecked"]["moved"]["t_hives"]), set(reading))
        self.assertEqual(live["t_hives"].described.get("title"), "t_hives", "its name kept")
        # Once.
        moved_back = {one: _v(0, 1) for one in hives}
        self._run(kept, moved_back)
        self.assertEqual(set(kept.live()[0].members), set(hives))

    def test_a_task_that_would_lose_most_of_itself_is_left_alone(self) -> None:
        mixed = self._states([f"They are doing thing {n}." for n in range(8)])
        vectors = {one: _v(1 if n % 2 else 0, 0 if n % 2 else 1, n * 0.1)
                   for n, one in enumerate(mixed)}
        kept = self._task("t_mixed", mixed, vectors)
        before = list(kept.live()[0].members)
        with mock.patch("personalized_agent.heartbeat.states.heartbeat._fits",
                        return_value={"fits": False}):
            report = self._run(kept, vectors)
        self.assertEqual(kept.live()[0].members, before)
        self.assertEqual(report["rechecked"]["left_alone"], ["t_mixed"])
        self.assertEqual(kept.loose(), [])

    def test_a_small_task_is_not_judged(self) -> None:
        few = self._states([f"They are watering pot {n}." for n in range(4)])
        odd = self._states(["They are reading a chat."])
        vectors = {one: _v(1, 0) for one in few}
        vectors.update({one: _v(0, 1) for one in odd})
        kept = self._task("t_small", few + odd, vectors)
        self._run(kept, vectors)
        self.assertEqual(set(kept.live()[0].members), set(few + odd))


if __name__ == "__main__":
    unittest.main()
