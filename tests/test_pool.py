"""ONE POOL for everything the agent runs side by side: how many places is
decided from everything going together; a place that comes free goes to
the kind with the fewest going, and between kinds with as many, by
priority - with room for one, priority alone."""

from __future__ import annotations

import threading
import time
import unittest

from personalized_agent.common.pool import Pool
from personalized_agent.common.room import at_once


class _Room:
    """As many places as it is told."""

    def __init__(self, places):
        self.places = places

    def allowed(self, running):
        return self.places, f"room for {self.places}"


class BalancedThenByPriorityTests(unittest.TestCase):

    def test_one_of_each_before_a_second_of_any(self) -> None:
        pool = Pool(_Room(2))
        first = pool.try_take("work", "run 1")
        second = pool.try_take("work", "run 2")
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        # Full: the grouping waits, and so does a third run.
        self.assertIsNone(pool.try_take("grouping", "summaries"))
        self.assertIsNone(pool.try_take("work", "run 3"))
        pool.give_back(first)
        # The place that came free is the grouping's - none of it is going -
        # though the runs matter more.
        self.assertIsNone(pool.try_take("work", "run 3"))
        self.assertIsNotNone(pool.try_take("grouping", "summaries"))

    def test_room_for_one_goes_by_priority(self) -> None:
        pool = Pool(_Room(1))
        held = pool.try_take("newspaper", "an issue")
        self.assertIsNone(pool.try_take("auto learning", "a project"))
        self.assertIsNone(pool.try_take("work", "a run"))
        pool.give_back(held)
        # The learning that updates itself asked first; the run comes first.
        self.assertIsNone(pool.try_take("auto learning", "a project"))
        self.assertIsNotNone(pool.try_take("work", "a run"))

    def test_a_waiting_kind_gets_a_place_before_a_third_run(self) -> None:
        pool = Pool(_Room(3))
        counted = ["run 1", "run 2"]
        pool.counts("work", lambda: list(counted))
        # Two runs going, counted by asking, and a project being learned:
        # full. The grouping waits.
        learning = pool.try_take("learning", "a project")
        self.assertIsNotNone(learning)
        self.assertIsNone(pool.try_take("grouping", "summaries"))
        pool.give_back(learning)
        # The place that came free: not a third run, the grouping's.
        self.assertFalse(pool.may_start("work", key="third")[0])
        self.assertIsNotNone(pool.try_take("grouping", "summaries"))
        self.assertEqual(pool.status()["by_kind"], {"grouping": 1, "work": 2})

    def test_never_fewer_than_one(self) -> None:
        pool = Pool(_Room(0))
        self.assertIsNotNone(pool.try_take("auto learning", "a project"))

    def test_a_waiter_that_stops_asking_is_forgotten(self) -> None:
        from personalized_agent.common import pool as pool_module
        pool = Pool(_Room(1))
        held = pool.try_take("grouping")
        self.assertIsNone(pool.try_take("work", "gone"))
        pool.give_back(held)
        real = pool_module.WAITER_FORGOTTEN
        pool_module.WAITER_FORGOTTEN = 0.0
        try:
            time.sleep(0.01)
            self.assertIsNotNone(pool.try_take("auto learning"))
        finally:
            pool_module.WAITER_FORGOTTEN = real


class AtOnceThroughThePoolTests(unittest.TestCase):

    def test_every_item_done_within_the_places(self) -> None:
        pool = Pool(_Room(2))
        other = pool.try_take("work", "a run")
        most, now, lock = [0], [0], threading.Lock()

        def work(item):
            with lock:
                now[0] += 1
                most[0] = max(most[0], now[0])
            time.sleep(0.05)
            with lock:
                now[0] -= 1
            return item

        done = []
        at_once(range(5), work, None, landed=lambda item, result, error: done.append(result),
                pool=pool, kind="grouping")
        self.assertEqual(sorted(done), [0, 1, 2, 3, 4])
        # One place was the run's: one at a time beside it.
        self.assertEqual(most[0], 1)
        self.assertEqual(pool.status()["by_kind"], {"work": 1})
        pool.give_back(other)

    def test_it_waits_for_a_place_rather_than_giving_up(self) -> None:
        pool = Pool(_Room(1))
        held = pool.try_take("work", "a run")
        threading.Timer(0.3, lambda: pool.give_back(held)).start()
        done = []
        at_once(range(2), lambda item: item, None,
                landed=lambda item, result, error: done.append(result),
                pool=pool, kind="grouping")
        self.assertEqual(sorted(done), [0, 1])


if __name__ == "__main__":
    unittest.main()
