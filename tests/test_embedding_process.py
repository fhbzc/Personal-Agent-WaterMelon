"""The embedding model lives in a process of its own, held while in use."""

from __future__ import annotations

import contextlib
import time
import unittest

from personalized_agent.heartbeat.states.embedding import (
    Embedder, _pairwise, _pairwise_slowly)


class TheModelIsHeldWhileInUseTests(unittest.TestCase):

    def setUp(self) -> None:
        self.embedder = Embedder()
        # Kept a moment after its last use, not two minutes, for the test.
        self.embedder.LINGER_SECONDS = 0.5
        if not self.embedder.available():
            self.skipTest("no embedding model on this machine")

    def _gone_after_a_while(self) -> None:
        for _ in range(40):
            if not self.embedder.status()["loaded"]:
                return
            time.sleep(0.1)
        self.fail("still loaded long after its last use")

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
        self._gone_after_a_while()

    def test_a_reading_with_nothing_holding_it_lets_it_go(self) -> None:
        self.assertEqual(len(self.embedder.embed(["alone"])), 1)
        self._gone_after_a_while()

    def test_two_holders_keep_it_until_the_last_is_done(self) -> None:
        with self.embedder.loaded():
            with self.embedder.loaded():
                self.embedder.embed(["held twice"])
            self.assertTrue(self.embedder.status()["loaded"])
        self._gone_after_a_while()

    def test_the_next_piece_of_work_finds_it_still_loaded(self) -> None:
        """A round's pieces each hold it on their own. Let go the moment the
        first was done, the next loaded it again - 27 to 34 seconds each, three
        times in a minute and a half. Kept a while, they share one load."""
        with self.embedder.loaded():
            self.embedder.embed(["the first piece"])
            pid = self.embedder._child.pid
        self.assertTrue(self.embedder.status()["loaded"], "let go at once")
        with self.embedder.loaded():
            self.embedder.embed(["the next piece"])
            self.assertEqual(self.embedder._child.pid, pid, "loaded again")
        self._gone_after_a_while()


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


class ItStepsAsideTests(unittest.TestCase):
    """2026-11-15: "我刚刚chat突然很卡" - each load took every core for 10-37 s. The child runs below normal
    priority with half the threads; seen in a process of its own, as the child is."""

    def test_below_normal_and_half_the_threads(self) -> None:
        import json
        import subprocess
        import sys
        from pathlib import Path
        worker = Path(__file__).resolve().parents[1] / "personalized_agent" / "heartbeat" / "states"
        probe = ("import json, os, sys; sys.path.insert(0, sys.argv[1]); import embed_worker as w; w.step_aside()\n"
                 "said = {'omp': os.environ.get('OMP_NUM_THREADS'), 'want': str(w.threads())}\n"
                 "if sys.platform.startswith('win'):\n"
                 "    import ctypes; k = ctypes.windll.kernel32; k.GetCurrentProcess.restype = ctypes.c_void_p\n"
                 "    k.GetPriorityClass.argtypes = [ctypes.c_void_p]\n"
                 "    said['below'] = k.GetPriorityClass(k.GetCurrentProcess()) == 0x4000\n"
                 "else:\n"
                 "    said['below'] = os.nice(0) >= 10\n"
                 "print(json.dumps(said))")
        env = {key: value for key, value in __import__("os").environ.items()
               if key not in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")}
        out = subprocess.run([sys.executable, "-c", probe, str(worker)], capture_output=True, text=True,
                             env=env, timeout=60)
        said = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertTrue(said["below"], "below normal priority")
        self.assertEqual(said["omp"], said["want"], "half the threads")
        self.assertGreaterEqual(int(said["want"]), 1)


if __name__ == "__main__":
    unittest.main()
