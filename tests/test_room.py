"""Work done at once, never taking the machine from the user."""

from __future__ import annotations

import threading
import time
import unittest

from personalized_agent.common.room import Room, at_once


class _Settings(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _room(most=3, **seen) -> Room:
    room = Room(config=_Settings(state_heartbeat_at_once=most))
    machine = {"idle": 600.0, "refused": "", "battery": None,
               "cpu_others": 0.10, "memory_free": 16 * 1024 ** 3,
               "memory_total": 32 * 1024 ** 3}
    machine.update(seen)
    room.look = lambda running=0: machine
    return room


class HowManyAtOnceTests(unittest.TestCase):

    def test_away_and_idle_machine_gets_the_most(self) -> None:
        self.assertEqual(_room().allowed(0)[0], 3)

    def test_at_the_computer_at_most_two(self) -> None:
        allowed, why = _room(idle=5.0).allowed(0)
        self.assertEqual(allowed, 2)
        self.assertIn("at the computer", why)

    def test_not_knowing_counts_as_at_the_computer(self) -> None:
        self.assertEqual(_room(idle=None).allowed(0)[0], 2)

    def test_a_busy_processor_starts_no_more(self) -> None:
        # The user's own work has the processor: what is going finishes,
        # nothing more starts.
        room = _room(cpu_others=0.80)
        self.assertEqual(room.allowed(0)[0], 1)
        self.assertEqual(room.allowed(2)[0], 2)

    def test_half_the_processor_is_left_for_a_user_at_the_computer(self) -> None:
        self.assertEqual(_room(idle=5.0, cpu_others=0.40).allowed(0)[0], 2)
        self.assertEqual(_room(idle=5.0, cpu_others=0.48).allowed(0)[0], 1)

    def test_memory_is_left_free(self) -> None:
        # 2.5 GB free of 8: a fifth (1.6 GB) is not enough - two gigabytes is
        # kept - so half a gigabyte is left, room for one more call.
        room = _room(memory_free=2.5 * 1024 ** 3, memory_total=8 * 1024 ** 3)
        self.assertEqual(room.allowed(0)[0], 1)
        self.assertEqual(_room(memory_free=1.0 * 1024 ** 3).allowed(0)[0], 1)

    def test_on_battery(self) -> None:
        self.assertEqual(_room(battery={"plugged": False, "percent": 80}).allowed(0)[0], 2)
        self.assertEqual(_room(battery={"plugged": False, "percent": 20}).allowed(0)[0], 1)

    def test_refused_lately_is_one_at_a_time(self) -> None:
        allowed, why = _room(refused="rate_limited").allowed(0)
        self.assertEqual(allowed, 1)
        self.assertIn("rate_limited", why)

    def test_set_to_one_is_one(self) -> None:
        self.assertEqual(_room(most=1).allowed(0)[0], 1)


class AtOnceTests(unittest.TestCase):

    def _run(self, room, count=6):
        going, most, order = [0], [0], []
        lock = threading.Lock()

        def work(item):
            with lock:
                going[0] += 1
                most[0] = max(most[0], going[0])
            time.sleep(0.05 * (count - item))
            with lock:
                going[0] -= 1
            return item * 10

        landed = []
        started = at_once(iter(range(count)), work, room,
                          landed=lambda item, result, error:
                          landed.append((item, result, threading.current_thread())))
        return started, most[0], landed

    def test_never_more_than_allowed_and_each_lands_on_the_caller(self) -> None:
        started, most, landed = self._run(_room(most=3))
        self.assertEqual(started, 6)
        self.assertEqual(most, 3)
        self.assertEqual(sorted(result for _, result, _ in landed),
                         [0, 10, 20, 30, 40, 50])
        self.assertTrue(all(thread is threading.current_thread()
                            for _, _, thread in landed))

    def test_no_room_is_one_at_a_time(self) -> None:
        _started, most, _landed = self._run(None, count=3)
        self.assertEqual(most, 1)

    def test_saying_stop_starts_no_more(self) -> None:
        seen = []
        started = at_once(iter(range(10)), lambda item: item, _room(most=1),
                          landed=lambda item, result, error:
                          seen.append(item) or item < 2)
        self.assertEqual(started, 3)
        self.assertEqual(seen, [0, 1, 2])

    def test_an_error_is_handed_to_the_caller(self) -> None:
        caught = []

        def work(item):
            raise ValueError("no")

        at_once(iter([1]), work, None,
                landed=lambda item, result, error: caught.append(error))
        self.assertIsInstance(caught[0], ValueError)


if __name__ == "__main__":
    unittest.main()
