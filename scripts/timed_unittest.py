"""Run the tests named, as unittest does, and write down how long each took.

    python scripts/timed_unittest.py TIMES.json name [name ...] [-k pattern]

Used by scripts/run_tests.py: TIMES.json gets {"ran": n, "times": {"module.
Class.test": seconds}}, which it keeps for dividing the next run's work."""

from __future__ import annotations

import json
import sys
import time
import unittest


class _Timed(unittest.TextTestResult):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.times = {}
        self._started = {}

    def startTest(self, test) -> None:
        self._started[test.id()] = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test) -> None:
        began = self._started.pop(test.id(), None)
        if began is not None:
            self.times[test.id()] = time.perf_counter() - began
        super().stopTest(test)


def main(argv) -> int:
    times_file, rest = argv[0], argv[1:]
    pattern = None
    if "-k" in rest:
        at = rest.index("-k")
        pattern = rest[at + 1]
        rest = rest[:at] + rest[at + 2:]
    loader = unittest.TestLoader()
    if pattern:
        loader.testNamePatterns = [f"*{pattern}*"]
    suite = loader.loadTestsFromNames(rest)
    runner = unittest.TextTestRunner(resultclass=_Timed, verbosity=1)
    result = runner.run(suite)
    with open(times_file, "w", encoding="utf-8") as out:
        json.dump({"ran": result.testsRun, "times": result.times}, out)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
