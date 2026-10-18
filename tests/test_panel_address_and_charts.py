"""The panel in the address, so a refresh opens the one being looked at; and
a run's token chart drawn as soon as it can be seen, not at the run's next
call."""

from __future__ import annotations

import json
import unittest

from test_connections_panel import _render
from test_panels import _javascript_engine
from test_work_panel import CALLS


class ThePageTests(unittest.TestCase):

    def setUp(self) -> None:
        kind, self.engine = _javascript_engine()
        if kind != "chrome":
            self.skipTest("no browser available to render the page")

    def test_the_panel_is_in_the_address(self) -> None:
        said = _render(self.engine, {}, (
            "click(document.querySelector('#tabs button[data-tab=\"experience\"]'));await wait();"
            "out.work=location.hash;"
            "click(document.querySelector('#tabs button[data-tab=\"recordlog\"]'));await wait();"
            "out.log=location.hash;"
            "out.length=history.length;"))
        self.assertEqual(said["err"], "", said["err"])
        self.assertEqual(said["out"]["work"], "#experience")
        self.assertEqual(said["out"]["log"], "#recordlog")
        # Replaced, not a trail of entries.
        self.assertLessEqual(said["out"]["length"], 2)

    def test_a_chart_out_of_sight_is_drawn_when_it_comes_into_sight(self) -> None:
        said = _render(self.engine, {}, (
            "const wrap=document.createElement('div');wrap.style.display='none';"
            "wrap.innerHTML='<div data-xp-calls-legend=\"run_x\"></div>"
            "<div data-xp-calls-chart=\"run_x\" style=\"height:250px\"></div>';"
            "document.body.appendChild(wrap);"
            "xpCallsTake('run_x'," + json.dumps(CALLS) + ");xpCallsDraw('run_x');"
            "const box=wrap.querySelector('[data-xp-calls-chart]');"
            "out.hidden=box.querySelectorAll('svg').length;"
            "wrap.style.display='block';await wait(300);"
            "out.shown=box.querySelectorAll('svg').length;"))
        self.assertEqual(said["err"], "", said["err"])
        self.assertEqual(said["out"]["hidden"], 0)
        self.assertGreaterEqual(said["out"]["shown"], 1, "drawn once it had a width")


if __name__ == "__main__":
    unittest.main()
