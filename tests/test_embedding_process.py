"""The embedding model lives in a process of its own, held while in use."""

from __future__ import annotations

import contextlib
import unittest

from personalized_agent.heartbeat.states.embedding import (
    Embedder, _pairwise, _pairwise_slowly)


class TheModelIsHeldWhileInUseTests(unittest.TestCase):

    def setUp(self) -> None:
        self.embedder = Embedder()
        if not self.embedder.available():
            self.skipTest("no embedding model on this machine")

    def tearDown(self) -> None:
        self.embedder.release()

    def test_loaded_for_the_work_and_gone_after_it(self) -> None:
        with self.embedder.loaded():
            first = self.embedder.embed(["one sentence", "another"])
            self.assertTrue(self.embedder.status()["loaded"])
            # Held: the second reading uses the same process.
            pid = self.embedder._child.pid
            self.embedder.embed(["a third"])
            self.assertEqual(self.embedder._child.pid, pid)
        self.assertEqual((len(first), len(first[0])), (2, 384))
        self.assertFalse(self.embedder.status()["loaded"])

    def test_a_reading_with_nothing_holding_it_lets_it_go(self) -> None:
        self.assertEqual(len(self.embedder.embed(["alone"])), 1)
        self.assertFalse(self.embedder.status()["loaded"])

    def test_two_holders_keep_it_until_the_last_is_done(self) -> None:
        with self.embedder.loaded():
            with self.embedder.loaded():
                self.embedder.embed(["held twice"])
            self.assertTrue(self.embedder.status()["loaded"])
        self.assertFalse(self.embedder.status()["loaded"])


class PairsAreScoredWithoutTheModelTests(unittest.TestCase):

    def test_the_same_pairs_as_the_slow_way(self) -> None:
        vectors = {"a": [1.0, 0.0, 0.0], "b": [0.8, 0.6, 0.0],
                   "c": [0.0, 0.0, 1.0], "d": [0.6, 0.8, 0.0]}
        order = ["a", "b", "missing", "c", "d"]
        fast = _pairwise(order, vectors)
        rows = [vectors.get(one) for one in order]
        slow = _pairwise_slowly(rows, [i for i, row in enumerate(rows) if row], 0.05)
        self.assertEqual([(i, j) for i, j, _ in fast], [(i, j) for i, j, _ in slow])
        for (_, _, x), (_, _, y) in zip(fast, slow):
            self.assertAlmostEqual(x, y, places=5)


class ARoundHoldsTheModelTests(unittest.TestCase):
    """Held from a round's first visit to its end - not let go between visits,
    which come two seconds apart."""

    def test_held_across_visits_and_let_go_when_the_round_ends(self) -> None:
        from personalized_agent.heartbeat.states.heartbeat import StateHeartbeat
        events = []

        class Model:
            @contextlib.contextmanager
            def loaded(self):
                events.append("held")
                yield self
                events.append("let go")

        beat = StateHeartbeat.__new__(StateHeartbeat)
        beat.embedder = Model()
        beat._model_holds, beat._visits_in_flight = {}, {}
        import threading
        beat._model_holds_lock = threading.Lock()
        open_round = [True, True, False]
        beat._round_in_progress = lambda: open_round.pop(0)
        beat._run_visit = lambda trigger, scope: {}
        for _ in range(3):
            beat.run_once()
        self.assertEqual(events, ["held", "let go"])


if __name__ == "__main__":
    unittest.main()
