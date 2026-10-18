"""A run's token chart across the clock or across the run's own time - what
it had run when each call ended, its pauses and its waits for the user left
out, carried over from one session to the next."""

from __future__ import annotations

import json
import time
import unittest
from types import SimpleNamespace

from test_connections_panel import _render
from test_panels import _javascript_engine
from test_work_panel import CALLS


class TheRunCountsItsOwnTimeTests(unittest.TestCase):

    def _run(self, plan, minutes):
        from personalized_agent.agent.work.runner import Run
        run = SimpleNamespace(plan=plan, variant="full", id="run_x",
                              _minutes=lambda: minutes)
        run._active_before = lambda: Run._active_before(run)
        return run

    def test_each_call_says_how_long_the_run_had_run(self) -> None:
        from personalized_agent.agent.work.runner import Run
        plan = {}
        run = self._run(plan, 12.0)
        Run._record_call(run, "worker", "round 1", time.time() - 20,
                         {"tokens_in": 1000}, 200, "")
        self.assertEqual(plan["calls"][0]["active"], 720.0)

    def test_carried_from_one_session_to_the_next(self) -> None:
        from personalized_agent.agent.work.runner import Run
        plan = {}
        first = self._run(plan, 30.0)
        # As `_end_version` carries it when the first session ends.
        plan["active_seconds_ended"] = round(first._active_before() + 30.0 * 60, 1)
        second = self._run(plan, 5.0)          # a new session: its own five minutes
        Run._record_call(second, "reviewer", "round 2", time.time() - 10,
                         {"tokens_in": 500}, 100, "")
        self.assertEqual(plan["calls"][-1]["active"], 1800.0 + 300.0)


class TheChartTests(unittest.TestCase):

    def setUp(self) -> None:
        kind, self.engine = _javascript_engine()
        if kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _draw(self, calls, steps):
        return _render(self.engine, {}, (
            "const wrap=document.createElement('div');wrap.style.width='900px';"
            "wrap.innerHTML='<div data-xp-calls-legend=\"run_x\"></div>"
            "<div data-xp-calls-chart=\"run_x\" style=\"height:250px\"></div>';"
            "document.body.appendChild(wrap);"
            "xpCallsTake('run_x'," + json.dumps(calls) + ");" + steps))

    def test_time_spent_lays_the_calls_out_by_the_runs_own_time(self) -> None:
        calls = [dict(one, active=60.0 * (index + 1)) for index, one in enumerate(CALLS)]
        said = self._draw(calls, (
            "xpCallsDraw('run_x');await wait();"
            "const box=document.querySelector('[data-xp-calls-chart]');"
            "out.clock=[...box.querySelectorAll('.xp-calls-tick')].map(t=>t.textContent);"
            "click(document.querySelector('[data-xp-calls-axis=\"spent\"]'));await wait();"
            "out.spent=[...box.querySelectorAll('.xp-calls-tick')].map(t=>t.textContent);"
            "out.pressed=document.querySelector('[data-xp-calls-axis=\"spent\"]')"
            ".getAttribute('aria-pressed');"
            "out.estimated=document.querySelector('[data-xp-calls-legend]').textContent"
            ".includes('earlier calls estimated');"))
        self.assertEqual(said["err"], "", said["err"])
        out = said["out"]
        self.assertTrue(any("min" in one for one in out["spent"]), out["spent"])
        self.assertNotEqual(out["clock"], out["spent"])
        self.assertEqual(out["pressed"], "true")
        self.assertFalse(out["estimated"])

    def test_calls_from_before_it_counted_are_estimated(self) -> None:
        said = self._draw(CALLS, (
            "xpCallsAxisChosen='spent';xpCallsDraw('run_x');await wait();"
            "out.estimated=document.querySelector('[data-xp-calls-legend]').textContent"
            ".includes('earlier calls estimated');"
            "const s=xpCallsSpent(" + json.dumps(CALLS) + ");"
            "out.last=Math.max(...s.at.values())/1000;"))
        self.assertEqual(said["err"], "", said["err"])
        self.assertTrue(said["out"]["estimated"])
        # The night between the sessions is not counted as the run's time.
        self.assertLess(said["out"]["last"], 6 * 3600)


if __name__ == "__main__":
    unittest.main()
