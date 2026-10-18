"""NOT THIS WORK: the call already made to sum a task up, or to say where it
stands, also names the shown states that are plainly about something else;
they go to the pile - only once it is switched on. And the pile is offered to
the tasks again each round (switched on likewise), a state the model took
out of a task offered back to it only in a round the task gains others."""

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


class ThePromptsAskTests(unittest.TestCase):

    def test_both_calls_ask_for_it_by_number(self) -> None:
        from personalized_agent.heartbeat.states import heartbeat as hb
        self.assertIn('"not_this_work"', hb.SUMMARY_PROMPT)
        self.assertIn('"not_this_work"', hb.STATUS_PROMPT)
        self.assertIn("ARRIVED", hb.STATUS_PROMPT)

    def test_numbers_read_back_as_the_shown_states(self) -> None:
        from personalized_agent.heartbeat.states.heartbeat import _numbered_in
        shown = ["s1", "s2", "s3", "s4", "s5", "s6"]
        self.assertEqual(_numbered_in([2, "5", "#6"], shown), ["s2", "s5", "s6"])
        self.assertEqual(_numbered_in(["arrived 1"], shown), ["s1"])
        self.assertEqual(_numbered_in([9, 0, "x"], shown), [])
        self.assertEqual(_numbered_in([1, 2, 3, 4], shown), [], "most of them is no answer")
        self.assertEqual(_numbered_in("2", shown), [])


class TakenOutTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _task(self, size=8):
        from personalized_agent.agent.states.store import State
        from personalized_agent.heartbeat.states.tasks import Kept, Task
        ids = []
        for n in range(size):
            at = utcnow() - timedelta(hours=size - n)
            ids.append(self.app.states.add(State(
                title=f"They are inspecting beehive {n}.", detail="d", starts_at=at,
                ends_at=at + timedelta(minutes=20))).id)
        vectors = {one: _v(1, 0.05 * n) for n, one in enumerate(ids)}
        task = Task(id="t_hives", created_at=utcnow().isoformat())
        task.start_profile()
        for one in ids:
            task.take(one, vectors[one], (), "", "")
        task.described = {"title": "Inspect the beehives"}
        kept = Kept(self.app.db, "live")
        kept.save([task])
        return kept, kept.live()[0], ids, vectors

    def _take_out(self, task, ids, vectors):
        report = {}
        with mock.patch.object(self.beat, "_vectors", return_value=(vectors, "test")):
            out = self.beat._take_out_other_work(task, ids, self.beat.where("live"),
                                                 report, "status")
        return out, report

    def test_off_it_is_only_noted(self) -> None:
        kept, task, ids, vectors = self._task()
        out, report = self._take_out(task, ids[:2], vectors)
        self.assertEqual(out, [])
        self.assertEqual(report["not_this_work_said"][0]["states"], ids[:2])
        self.assertEqual(set(kept.live()[0].members), set(ids))
        self.assertEqual(kept.loose(), [])

    def test_off_it_is_kept_for_the_page_to_show(self) -> None:
        from personalized_agent.api.routes import build_routes

        class _Req:
            def get(self, key, default=None):
                return default
        kept, task, ids, vectors = self._task()
        self._take_out(task, ids[:2], vectors)
        said = build_routes(self.app)[("GET", "/api/state_heartbeat/not_this_work")](_Req())
        self.assertFalse(said["on"])
        entry = said["said"][0]
        self.assertEqual(entry["task"]["id"], "t_hives")
        self.assertEqual([one["id"] for one in entry["states"]], ids[:2])
        self.assertEqual(entry["states"][0]["title"], "They are inspecting beehive 0.")
        self.assertEqual(set(kept.live()[0].members), set(ids), "nothing moved")

    def test_on_they_go_to_the_pile_and_are_kept_as_taken_out(self) -> None:
        self.app.config.set("state_take_out_other_work", True, internal=True)
        kept, task, ids, vectors = self._task()
        out, report = self._take_out(task, ids[:2], vectors)
        self.assertEqual(out, ids[:2])
        after = kept.live()[0]
        self.assertEqual(set(after.members), set(ids[2:]))
        self.assertEqual(after.described.get("title"), "Inspect the beehives")
        self.assertEqual(set(kept.loose()), set(ids[:2]))
        self.assertEqual(after.profile.get("kicked_out"), ids[:2])
        self.assertEqual(self.app.config.get("state_not_this_work")[-1]["states"], ids[:2])

    def test_safeguards(self) -> None:
        self.app.config.set("state_take_out_other_work", True, internal=True)
        kept, task, ids, vectors = self._task(size=8)
        out, _ = self._take_out(task, ids[:5], vectors)
        self.assertEqual(out, [], "more than half of a task is not taken out")
        agent_small = self._task_small()
        self.assertEqual(agent_small, [])

    def _task_small(self):
        from personalized_agent.heartbeat.states.tasks import Task
        task = Task(id="t_small", created_at=utcnow().isoformat())
        task.members = ["a", "b", "c", "d", "e"]
        report = {}
        return self.beat._take_out_other_work(task, ["a"], self.beat.where("live"),
                                              report, "status")


class PlacingPassesOverTests(unittest.TestCase):

    def test_a_task_passes_over_a_state_it_was_told_is_not_its_own(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Task, place
        task = Task(id="t")
        task.start_profile()
        for n in range(10):
            task.take(f"t{n}", _v(1, 0), (), "", "")
        vectors = {"s": _v(1, 0.01)}
        free = place([task], ["s"], vectors, {"s": []}, {"s": ("", "")})
        self.assertEqual(free["placed"], {"s": "t"})
        task2 = Task(id="t")
        task2.start_profile()
        for n in range(10):
            task2.take(f"t{n}", _v(1, 0), (), "", "")
        held = place([task2], ["s"], vectors, {"s": []}, {"s": ("", "")},
                     barred={"s": {"t"}})
        self.assertEqual(held["placed"], {})
        self.assertIn("s", held["loose"])

    def test_kicked_out_kept_through_a_new_profile(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Task
        task = Task(id="t")
        task.start_profile()
        task.profile["kicked_out"] = ["s"]
        task.start_profile()
        self.assertEqual(task.profile["kicked_out"], ["s"])


class ThePileIsOfferedTests(unittest.TestCase):

    def test_off_by_default(self) -> None:
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        self.assertFalse(agent.app.config.get("state_task_reoffer_pile", False))
        self.assertFalse(agent.app.config.get("state_take_out_other_work", False))


if __name__ == "__main__":
    unittest.main()
