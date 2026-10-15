"""The test runner (scripts/run_tests.py): every test class found, and the
work divided by how long it takes, so no process waits on another."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def _runner():
    spec = importlib.util.spec_from_file_location("run_tests_under_test",
                                                  ROOT / "scripts" / "run_tests.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EveryClassIsFoundTests(unittest.TestCase):

    def test_one_made_from_a_base_of_its_own_file_too(self) -> None:
        runner = _runner()
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "test_sample.py"
            path.write_text(
                "import unittest\n\n\n"
                "class _Base(unittest.TestCase):\n"
                "    def setUp(self):\n        pass\n\n\n"
                "class DirectTests(unittest.TestCase):\n"
                "    def test_one(self):\n        pass\n\n\n"
                "class ThroughTheBaseTests(_Base):\n"
                "    def test_two(self):\n        pass\n\n"
                "    def test_three(self):\n        pass\n\n\n"
                "class NotATest:\n"
                "    def test_nothing(self):\n        pass\n", encoding="utf-8")
            found = runner.test_classes([path])
        self.assertEqual(found, {"test_sample.DirectTests": ["test_one"],
                                 "test_sample.ThroughTheBaseTests": ["test_two", "test_three"]})


class TheWorkIsDividedByTimeTests(unittest.TestCase):

    def test_a_long_class_is_run_test_by_test_and_the_longest_go_first(self) -> None:
        runner = _runner()
        classes = {"test_a.SlowTests": [f"test_{n}" for n in range(6)],
                   "test_a.QuickTests": ["test_x"], "test_b.QuickTests": ["test_y"]}
        with tempfile.TemporaryDirectory() as work:
            times = Path(work) / "durations.json"
            times.write_text(json.dumps({"test_a.SlowTests": 600.0, "test_a.QuickTests": 1.0,
                                         "test_b.QuickTests": 2.0}), encoding="utf-8")
            with mock.patch.object(runner, "DURATIONS", times):
                work_items = runner.items(classes)
        names = [name for _seconds, names in work_items for name in names]
        # The slow class is six items of its own; the quick ones share a batch.
        self.assertEqual(sum(1 for n in names if n.startswith("test_a.SlowTests.")), 6)
        self.assertIn(["test_b.QuickTests", "test_a.QuickTests"], [n for _s, n in work_items])
        seconds = [s for s, _n in work_items]
        self.assertEqual(seconds, sorted(seconds, reverse=True), "longest first")

    def test_what_was_measured_is_kept_for_next_time(self) -> None:
        runner = _runner()
        with tempfile.TemporaryDirectory() as work:
            times = Path(work) / "durations.json"
            with mock.patch.object(runner, "DURATIONS", times):
                runner.keep({"test_a.SlowTests.test_1": 3.0, "test_a.SlowTests.test_2": 4.5})
                kept = json.loads(times.read_text(encoding="utf-8"))
        self.assertEqual(kept["test_a.SlowTests"], 7.5)


if __name__ == "__main__":
    unittest.main()
