"""Every drawing has its own arrowhead. One id shared by every drawing on the
page meant each arrow used the FIRST drawing's head - and where that one was
hidden (another tab, a way not in front) no arrow had a head at all."""

from __future__ import annotations

import unittest

from test_connections_panel import _render
from test_panels import _javascript_engine


class OwnHeadsTests(unittest.TestCase):

    def setUp(self) -> None:
        kind, self.engine = _javascript_engine()
        if kind != "chrome":
            self.skipTest("no browser available to render the page")

    def test_two_stage_maps_each_point_at_their_own_head(self) -> None:
        said = _render(self.engine, {}, (
            "const graph={stages:[{id:'a',kind:'goal',summary:'Plan the soil survey',card:'c1'},"
            "{id:'b',kind:'plan',summary:'Pick the plots',card:'c1'}],"
            "edges:[{source:'b',target:'a',kind:'after'}],"
            "cards:[{id:'c1',title:'Soil survey'}]};"
            "const first=document.createElement('div');first.style.display='none';"
            "first.innerHTML=xpGraphSvg(graph);document.body.appendChild(first);"
            "const second=document.createElement('div');"
            "second.innerHTML=xpGraphSvg(graph);document.body.appendChild(second);"
            "const own=(host)=>[...host.querySelectorAll('[marker-end]')].every(line=>{"
            "  const id=line.getAttribute('marker-end').slice(5,-1);"
            "  return host.querySelector('marker#'+CSS.escape(id))!==null;});"
            "out.lines=second.querySelectorAll('[marker-end]').length;"
            "out.own=own(first)&&own(second);"
            "out.ids=[...document.querySelectorAll('marker')].map(m=>m.id);"))
        self.assertEqual(said["err"], "", said["err"])
        self.assertGreaterEqual(said["out"]["lines"], 1)
        self.assertTrue(said["out"]["own"], said["out"])
        self.assertEqual(len(set(said["out"]["ids"])), len(said["out"]["ids"]), "no id twice")

    def test_and_so_do_the_task_pictures(self) -> None:
        import pathlib
        js = (pathlib.Path(__file__).resolve().parents[1] / "personalized_agent" / "api"
              / "web" / "app.js").read_text(encoding="utf-8")
        for fixed in ('url(#sb-arrow)', 'url(#sb-tt-arrow)', 'url(#xp-head)',
                      'id="sb-arrow"', 'id="sb-tt-arrow"', 'id="xp-head"'):
            self.assertNotIn(fixed, js)


if __name__ == "__main__":
    unittest.main()
