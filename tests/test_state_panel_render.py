"""Section 34.3: the state heartbeat panel has to RUN, not only parse.

The frontend syntax test proves the script parses, and nothing else in the
suite looks at what its functions do. A renderer that throws on its second
line therefore leaves the panel blank while the whole suite stays green -
which is how a task title that was written, stored, sent to the page and
read from a field nothing had ever put it in went unnoticed through a
dozen runs.

So this draws a whole rehearsal in a real browser, with the shapes a real
round produces, and fails if anything throws or if the parts are missing.
"""

import json
import pathlib
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_panels import ROOT, _javascript_engine

WEB = ROOT / "personalized_agent" / "api" / "web"


def _a_round(title: str, concrete: bool) -> dict:
    """One round's report, with every field the panel reaches for."""
    summary = {"title": title, "summary": "What it is, and where its edges are.",
               "concrete": concrete, "concrete_why": "because of the substance",
               "kept": False, "evidence": [], "record_id": "rec_1",
               "reasoning": "the same work throughout"}
    return {
        "where": {"name": "sandbox", "scope": "sandbox", "library": "S",
                  "states_from": "S/states", "tasks_to": "S/state_tasks"},
        "states_considered": 3, "duration_ms": 1200, "trigger": "test",
        "instruction": "THE INSTRUCTION, SENT WITH EVERY SUMMARY",
        "instruction_tokens": 900,
        "states": [{"id": "s1", "title": "One", "detail": "d"},
                   {"id": "s2", "title": "Two", "detail": "d"},
                   {"id": "s3", "title": "Three", "detail": "d"}],
        "stages": [{"stage": "reading the states", "detail": "the window",
                    "seconds": 0.1, "calls": 0, "tokens_in": 0,
                    "tokens_out": 0, "tokens_cached": 0, "tokens_new": 0},
                   {"stage": "summarising", "detail": "task 1 of 1",
                    "seconds": 0.9, "calls": 1, "tokens_in": 2,
                    "tokens_out": 400, "tokens_cached": 800,
                    "tokens_new": 198}],
        "window": {"kind": "first", "read": 3, "states_in_all": 3, "cap": 60,
                   "before_the_beginning": 0, "at_the_cap": False,
                   "held_outside": 0, "from": "", "days": 30,
                   "why": "the first grouping reads all of it"},
        "overview": {
            "from": "2026-09-01T00:00:00+00:00",
            "to": "2026-09-10T00:00:00+00:00", "columns": 2,
            "column_hours": 108, "states_in_all": 3, "read": 3,
            "in_tasks": 3, "this_round": 3, "this_round_from": "",
            "buckets": [
                {"from": "2026-09-01T00:00:00+00:00",
                 "to": "2026-09-05T00:00:00+00:00", "states": 2, "read": 2,
                 "in_tasks": 2, "this_round": 2},
                {"from": "2026-09-05T00:00:00+00:00",
                 "to": "2026-09-10T00:00:00+00:00", "states": 1, "read": 1,
                 "in_tasks": 1, "this_round": 1}],
            "spans": [{"id": "task_1", "task_id": "t1", "size": 3,
                       "from": "2026-09-01T00:00:00+00:00",
                       "to": "2026-09-10T00:00:00+00:00"}],
            "limits": {"max_states": 60, "max_days": 30, "by_duration": "",
                       "by_count": "", "first_round": True, "not_read": 0,
                       "bound_by": "neither: every state was read",
                       "worked_on": 3, "worked_on_all_of_it": True},
            "why": "every state there is, by when it happened"},
        "continuity": {
            "kept_tasks": 1, "arrivals": 3, "placed": 3, "loose": 0,
            "waiting": 0, "became_tasks": [], "ambiguous": 0, "forgotten": 0,
            "ended": [], "since_rebuild": 3, "rebuilt": True,
            "why": "nothing was kept from a previous round",
            "signals": [{"name": "a task has drifted", "value": 0.0,
                         "limit": 0.05, "fired": False, "why": "its centre"}],
            "lineage": [], "limits": {"accept_at": 1.0},
            "window": {"kind": "first"}, "processed": ["s1", "s2", "s3"]},
        "layers": [{
            "id": "task_1", "task_id": "t1", "depth": 0, "size": 3,
            "height": 0.3, "stands_out": 1.4, "states": ["s1", "s2", "s3"],
            "loose": [], "children": [],
            "plan": {
                "nodes": [
                    {"number": 1, "id": "task_1.1",
                     "title": "Finish the census fetch step.", "size": 9,
                     "began": "2026-09-01T00:00:00+00:00",
                     "ended": "2026-09-04T00:00:00+00:00",
                     "closed": 9, "of": 9, "finished": True},
                    {"number": 2, "id": "task_1.2",
                     "title": "Check the figures against the published ones.",
                     "size": 6, "began": "2026-09-05T00:00:00+00:00",
                     "ended": "2026-09-09T00:00:00+00:00",
                     "closed": 0, "of": 6, "finished": False}],
                "edges": [{"after": "task_1.2", "before": "task_1.1"}],
                "why": "the figures need the script to have run",
                "prompt": "THE PLAN PROMPT", "answer": "{}"},
            "children": [
                {"id": "task_1.1", "task_id": "c1", "depth": 1, "size": 9,
                 "height": 0.2, "stands_out": 1.2, "states": ["s1", "s2"],
                 "loose": [], "children": [], "last_layer": True,
                 "kept": {"took": [], "version": 1, "born_of": {},
                          "since": "2026-09-01T00:00:00+00:00", "drift": 0.0,
                          "widened": 1.0, "radius": 0.1, "spread": 0.01,
                          "happened": {}},
                 "summary": {"title": "Finish the census fetch step.",
                             "summary": "What to do.", "concrete": True,
                             "concrete_why": "a named step", "kept": False,
                             "evidence": [], "record_id": "r2"}},
                {"id": "task_1.2", "task_id": "c2", "depth": 1, "size": 6,
                 "height": 0.2, "stands_out": 1.2, "states": ["s3"],
                 "loose": [], "children": [], "last_layer": True,
                 "kept": {"took": [], "version": 1, "born_of": {},
                          "since": "2026-09-05T00:00:00+00:00", "drift": 0.0,
                          "widened": 1.0, "radius": 0.1, "spread": 0.01,
                          "happened": {}},
                 "summary": {"title": "Check the figures.", "summary": "Do it.",
                             "concrete": True, "concrete_why": "named",
                             "kept": False, "evidence": [], "record_id": "r3"}}],
            "kept": {"since": "2026-09-01T00:00:00+00:00", "version": 1,
                     "born_of": {"kind": "new", "from": []}, "took": [],
                     "drift": 0.0, "widened": 1.0, "radius": 0.1,
                     "spread": 0.02, "happened": {}},
            "summary": summary}],
        "communities": [{
            "id": "group_1", "size": 3, "big_enough": True,
            "titles": ["One", "Two", "Three"], "shared_said": ["one"],
            "summary": {**summary, "prompt": "THE WHOLE PROMPT, ENTIRE",
                        "answer": "{\"title\": \"...\"}", "calls": 1,
                        "prompt_tokens": 100, "sent_tokens": 1000,
                        "tokens_in": 2, "tokens_out": 400,
                        "tokens_cached": 800, "tokens_new": 198, "ms": 900}}],
        "spent": {"per_task": [{
            "task": "task_1", "group": "group_1", "states": 3, "kept": False,
            "calls": 1, "prompt_tokens": 100, "sent_tokens": 1000,
            "tokens_in": 2, "tokens_out": 400, "tokens_cached": 800,
            "tokens_new": 198, "concrete": concrete, "ms": 900}],
            "instruction_tokens": 900, "why": "the instruction goes again"},
        "not_shown": ([] if concrete else
                      [{"task": "task_1", "title": title, "why": "a pattern"}]),
        "summaries_written": [], "summaries_kept": [], "loose": [],
        "tree": {"leaves": [], "merges": [], "labels": []},
        "similarity": {"source": "embedding", "pairs_scored": 3,
                       "scored_now": True, "pairs": []},
        "settings": {"min_nodes": 3, "interval_seconds": 3600},
    }


class TheStateHeartbeatPanelRendersTests(unittest.TestCase):

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _in_the_page(self, body: str, held: dict | None = None) -> dict:
        """Run some script against the real page and read what it says.

        The same assembly `_render` uses - the panel's own HTML and the
        whole of app.js, in a browser - with the body doing whatever the
        test is about and putting its answer in the title.
        """
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (WEB / "index.html").read_text(encoding="utf-8"))
        harness = (
            # THE STYLESHEET, INLINE. The page links it by a relative
            # path and the probe is written to a temp directory, so
            # without it the browser has no CSS at all and every
            # computed style is a browser default - which is why two
            # goes at a cascade bug in this file went unnoticed by a
            # suite that renders the page in a real browser.
            "<style>" + (WEB / "styles.css").read_text(encoding="utf-8")
            + "</style>"
            "<script>window.onerror=(m)=>{window.__err=String(m);};</script>"
            "<script>window.fetch=()=>Promise.resolve({ok:true,status:200,"
            "statusText:'OK',json:()=>Promise.resolve({})});</script>"
            "<script>" + (WEB / "app.js").read_text(encoding="utf-8")
            + "</script>"
            # Fixed dates, from March on: no "last seven days" window, unless
            # a test is about that window and sets one.
            "<script>stateBeat.recentDays=0;</script>"
            # Values the body names, bound before it runs. A report
            # embedded in the body itself has to survive being read as
            # JavaScript source twice over - once by Python's quoting and
            # once by the browser - and the newline inside a regex that
            # cost three debugging rounds came from exactly that.
            + "<script>" + ";".join(
                f"const {name} = {json.dumps(value)}"
                for name, value in (held or {}).items()) + ";</script>"
            "<script>window.addEventListener('load',()=>{" + body
            + "});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            done = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu",
                 "--no-first-run", "--window-size=1400,900",
                 f"--user-data-dir={work}/profile",
                 "--virtual-time-budget=9000", "--dump-dom", probe.as_uri()],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=300)
        found = re.search(r"<title>(.*?)</title>", done.stdout or "", re.S)
        self.assertTrue(found, "the page never finished rendering")
        return json.loads(found.group(1).replace("&quot;", '"')
                          .replace("&amp;", "&"))

    def _render(self, report, rehearsal=None) -> dict:
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (WEB / "index.html").read_text(encoding="utf-8"))
        newline = chr(92) + chr(92) + "n"
        harness = (
            # THE STYLESHEET, INLINE. The page links it by a relative
            # path and the probe is written to a temp directory, so
            # without it the browser has no CSS at all and every
            # computed style is a browser default - which is why two
            # goes at a cascade bug in this file went unnoticed by a
            # suite that renders the page in a real browser.
            "<style>" + (WEB / "styles.css").read_text(encoding="utf-8")
            + "</style>"
            "<script>window.onerror=(m)=>{window.__err=String(m);};</script>"
            "<script>window.fetch=()=>Promise.resolve({ok:true,status:200,"
            "statusText:'OK',json:()=>Promise.resolve({})});</script>"
            "<script>" + (WEB / "app.js").read_text(encoding="utf-8")
            + "</script>"
            "<script>window.addEventListener('load',()=>{"
            "let err=window.__err||'';try{"
            "stateBeat.rehearsal=" + json.dumps(rehearsal) + ";"
            "stateBeat.report=" + json.dumps(report) + ";"
            "renderStateBeat();}catch(e){"
            "err=(e&&e.stack?e.stack:String(e)).split('" + newline + "')[0];}"
            "const box=document.getElementById('dbg-statebeat-body');"
            "document.title=JSON.stringify({err:err,"
            "chars:box?box.textContent.length:-1,"
            "rounds:document.querySelectorAll('.sb-round').length,"
            "compare:document.querySelectorAll('.sb-compare').length,"
            "overviews:document.querySelectorAll('.sb-overview').length,"
            "titles:[...document.querySelectorAll('.sb-task-title')]"
            ".map(e=>e.textContent.trim()),"
            "notShown:document.querySelectorAll('.sb-not-shown').length,"
            "prompts:document.querySelectorAll('.sb-prompt').length,"
            "treeNodes:document.querySelectorAll('.sb-tt-node').length,"
            "treeEdges:document.querySelectorAll('.sb-tt-edge').length,"
            "treeDone:document.querySelectorAll('.sb-tt-done').length,"
            "bars:document.querySelectorAll('.sb-ov-now,.sb-ov-held,"
            ".sb-ov-loose,.sb-ov-unread').length});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            done = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu",
                 "--no-first-run", "--window-size=1400,900",
                 f"--user-data-dir={work}/profile",
                 "--virtual-time-budget=9000", "--dump-dom", probe.as_uri()],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=300)
        found = re.search(r"<title>(.*?)</title>", done.stdout or "", re.S)
        self.assertTrue(found, "the page never finished rendering")
        return json.loads(found.group(1).replace("&quot;", '"')
                          .replace("&amp;", "&"))

    def _two_rounds(self):
        """A rehearsal where one task grows and another arrives late."""
        first = _a_round("Rebuild the package.", True)
        first["layers"][0]["task_id"] = "t1"
        first["layers"][0]["states"] = ["s1", "s2"]
        first["layers"][0]["size"] = 2
        first["layers"][0]["summary"] = dict(
            first["layers"][0].get("summary") or {},
            title="Rebuild the package.",
            described={"where": "in survey-weights/",
                       "status": "The user has not started the figures."},
            described_now=["where", "status"])

        second = json.loads(json.dumps(first))
        second["layers"][0]["states"] = ["s1", "s2", "s9"]
        second["layers"][0]["size"] = 3
        second["layers"][0]["summary"]["described"] = {
            "where": "in survey-weights/",
            "status": "The user has figure 1 matching now."}
        second["layers"][0]["summary"]["described_now"] = ["status"]
        # And one that only exists in the second round.
        late = json.loads(json.dumps(first["layers"][0]))
        late["task_id"] = "t2"
        late["id"] = "task_2"
        late["states"] = ["s10"]
        late["size"] = 1
        late["summary"] = {"title": "Answer the reviewer.", "described": {},
                           "described_now": []}
        second["layers"].append(late)
        second["states"] = (second.get("states") or []) + [
            {"id": "s9", "title": "A third sitting on the fetch step.",
             "detail": "d"},
            {"id": "s10", "title": "Reading the reviewer's letter.",
             "detail": "d"}]
        return first, second

    def test_every_task_is_followed_through_every_round(self) -> None:
        """The size table answers "did it grow" and nothing else. What a
        rehearsal exists to show is what appending DID: which states
        arrived, and whether the words changed."""
        first, second = self._two_rounds()
        # The shape the rehearsal actually has: named rounds, not a list.
        rehearsal = {
            "cap": 60, "why": "two rounds", "trace": [],
            "round_one": first, "round_two": second,
            "identity": {"kept_their_id_throughout": 1,
                         "tasks_after_round_one": 1}}
        verdict = self._in_the_page(
            "let err=window.__err||'';let text='';let blocks=0;try{"
            "stateBeat.rehearsal=" + json.dumps(rehearsal) + ";"
            "stateBeat.report=" + json.dumps(second) + ";renderStateBeat();"
            "const box=document.getElementById('dbg-statebeat-body');"
            "blocks=box.querySelectorAll('.sb-across').length;"
            "text=box.textContent;"
            "}catch(e){err=String(e);}"
            "document.title=JSON.stringify({err:err,text:text,blocks:blocks});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        # One block per task that existed in ANY round - including the one
        # that only appears in the second, whose absence from the first is
        # itself the finding.
        self.assertEqual(verdict["blocks"], 2, verdict["blocks"])
        said = verdict["text"]
        self.assertIn("Every task, round by round", said)
        # The state that arrived, named rather than counted.
        self.assertIn("A third sitting on the fetch step.", said)
        # The round a task did not exist in says so rather than leaving a
        # blank, because a blank reads as "nothing changed".
        self.assertIn("this task did not exist in this round", said)

    def test_and_a_changed_part_of_the_description_is_marked(self) -> None:
        first, second = self._two_rounds()
        rehearsal = {
            "cap": 60, "why": "two rounds", "trace": [],
            "round_one": first, "round_two": second, "identity": {}}
        verdict = self._in_the_page(
            "let err=window.__err||'';let changed=[];let text='';try{"
            "stateBeat.rehearsal=" + json.dumps(rehearsal) + ";"
            "stateBeat.report=" + json.dumps(second) + ";renderStateBeat();"
            "const box=document.getElementById('dbg-statebeat-body');"
            "changed=[...box.querySelectorAll('.sb-across-changed')]"
            ".map((one)=>one.textContent.trim().slice(0,40));"
            "text=box.textContent;"
            "}catch(e){err=String(e);}"
            "document.title=JSON.stringify({err:err,changed:changed,"
            "text:text});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertTrue(verdict["changed"], "nothing was marked as changed")
        # `status` moved between the rounds and `where` did not, so one is
        # marked and the other says so.
        self.assertIn("changed this round", verdict["text"])
        self.assertIn("unchanged", verdict["text"])

    def _with_a_tree(self):
        """A round whose report carries a tree worth zooming into."""
        report = _a_round("Rebuild the package.", True)
        leaves = list(range(8))
        report["states"] = [{"id": f"s{n}", "title": f"State {n}",
                             "detail": "d"} for n in leaves]
        report["tree"] = {
            "leaves": leaves,
            "labels": [f"s{n}" for n in leaves],
            "merges": [{"left": a, "right": a + 1, "height": 0.1 * (a + 1),
                        "size": 2} for a in range(0, 7)],
            "cut_at": 0.35}
        return report

    def test_the_tree_can_be_zoomed(self) -> None:
        """A hundred states is a hundred rows, and at that size the names
        are four pixels tall and the part worth looking at - which joins
        happen left of the cut - is a smudge."""
        verdict = self._in_the_page(
            "let err=window.__err||'';let was='';let now='';let back='';try{"
            "stateBeat.report=" + json.dumps(self._with_a_tree()) + ";"
            "renderStateBeat();"
            "const box=document.getElementById('dbg-statebeat-body');"
            "const svg=box.querySelector('.sb-graph[data-sb-whole]');"
            "was=svg.getAttribute('viewBox');"
            "box.querySelector('[data-sb-zoom=\"in\"]').click();"
            "now=svg.getAttribute('viewBox');"
            "box.querySelector('[data-sb-zoom=\"reset\"]').click();"
            "back=svg.getAttribute('viewBox');"
            "}catch(e){err=(e&&e.stack?e.stack:String(e)).split('\\n')[0];}"
            "document.title=JSON.stringify({err:err,was:was,now:now,"
            "back:back});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertTrue(verdict["was"], "the tree was not drawn")
        self.assertNotEqual(verdict["now"], verdict["was"],
                            "zooming in changed nothing")
        # Narrower than it was, which is what zooming in means.
        self.assertLess(float(verdict["now"].split()[2]),
                        float(verdict["was"].split()[2]))
        # And `fit` puts the whole tree back.
        self.assertEqual(verdict["back"], verdict["was"])

    def test_and_it_cannot_be_zoomed_past_the_picture(self) -> None:
        """A box larger than the drawing is empty space; one smaller than
        a few states is a line with no context around it."""
        verdict = self._in_the_page(
            "let err=window.__err||'';let tight=0;let loose=0;try{"
            "stateBeat.report=" + json.dumps(self._with_a_tree()) + ";"
            "renderStateBeat();"
            "const box=document.getElementById('dbg-statebeat-body');"
            "const svg=box.querySelector('.sb-graph[data-sb-whole]');"
            "const whole=Number(svg.dataset.sbWhole.split(' ')[2]);"
            "for(let n=0;n<40;n++)"
            "  box.querySelector('[data-sb-zoom=\"in\"]').click();"
            "tight=Number(svg.getAttribute('viewBox').split(' ')[2])/whole;"
            "for(let n=0;n<80;n++)"
            "  box.querySelector('[data-sb-zoom=\"out\"]').click();"
            "loose=Number(svg.getAttribute('viewBox').split(' ')[2])/whole;"
            "}catch(e){err=(e&&e.stack?e.stack:String(e)).split('\\n')[0];}"
            "document.title=JSON.stringify({err:err,tight:tight,loose:loose});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertGreater(verdict["tight"], 0.01, verdict)
        self.assertLessEqual(round(verdict["loose"], 3), 1.0, verdict)

    def test_and_plain_scrolling_is_left_alone(self) -> None:
        """A figure that swallows the wheel inside a page this long is a
        trap: the reader scrolls past it, zooms it by accident, and has no
        way out."""
        script = pathlib.Path(
            "personalized_agent/api/web/app.js").read_text(encoding="utf-8")
        block = script[script.index('host.addEventListener("wheel"'):]
        block = block[:block.index("}, { passive: false });")]
        self.assertIn("event.ctrlKey || event.metaKey", block)
        # It returns before preventing anything when the modifier is absent.
        self.assertLess(block.index("ctrlKey"), block.index("preventDefault"))

    def _rounds_with_states(self):
        """Two rounds where a task keeps some states, gains and loses."""
        first = _a_round("Rebuild the package.", True)
        first["layers"][0]["task_id"] = "t1"
        first["layers"][0]["states"] = ["s1", "s2"]
        first["layers"][0]["size"] = 2
        first["layers"][0]["summary"] = dict(
            first["layers"][0].get("summary") or {},
            title="Rebuild the package.",
            # s1 was in the sample; s2 was never shown; s9 was asked for.
            seen={"sampled": ["s1"], "in_full": []})
        second = json.loads(json.dumps(first))
        second["layers"][0]["states"] = ["s1", "s9"]
        second["layers"][0]["size"] = 2
        second["layers"][0]["summary"]["seen"] = {
            "sampled": ["s1"], "in_full": ["s9"]}
        second["states"] = (second.get("states") or []) + [{
            "id": "s9", "title": "A sitting on the fetch step.",
            "detail": "d", "reasoning": "three windows on one file",
            "status": "active", "sources": ["fetch.py"],
            "evidence": ["the traceback"]}]
        return {"cap": 60, "why": "two", "trace": [],
                "round_one": first, "round_two": second, "identity": {}}

    def test_what_stayed_is_shown_as_well_as_what_moved(self) -> None:
        """A task of twenty-six that gained eighteen and lost seventeen
        has eight states running through it, and those eight are what
        make it the same task. A page that shows only the movement shows
        the churn and hides the subject."""
        verdict = self._in_the_page(
            "let err=window.__err||'';let text='';try{"
            "stateBeat.rehearsal=" + json.dumps(self._rounds_with_states()) + ";"
            "stateBeat.report=" + json.dumps(_a_round("x", True)) + ";"
            "renderStateBeat();"
            "text=document.getElementById('dbg-statebeat-body').textContent;"
            "}catch(e){err=(e&&e.stack?e.stack:String(e)).split('\\n')[0];}"
            "document.title=JSON.stringify({err:err,text:text});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        # Normalised, because the template that writes these is wrapped
        # and a phrase that spans a line break is not in the text as a
        # phrase.
        said = " ".join(verdict["text"].split())
        self.assertIn("already in it and stayed", said)
        self.assertIn("1 stayed", said)
        self.assertIn("that arrived", said)
        self.assertIn("that left it", said)
        # And the first round lists everything it began with, rather than
        # naming a count and showing nothing.
        self.assertIn("all 2 states it began with", said)

    def test_and_a_state_is_shown_to_the_depth_the_model_read_it(self) -> None:
        """Drawing all three depths the same way invites the reader to
        explain a summary by a field the model never saw."""
        verdict = self._in_the_page(
            "let err=window.__err||'';let text='';try{"
            "stateBeat.rehearsal=" + json.dumps(self._rounds_with_states()) + ";"
            "stateBeat.report=" + json.dumps(_a_round("x", True)) + ";"
            "renderStateBeat();"
            "text=document.getElementById('dbg-statebeat-body').textContent;"
            "}catch(e){err=(e&&e.stack?e.stack:String(e)).split('\\n')[0];}"
            "document.title=JSON.stringify({err:err,text:text});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        said = verdict["text"];
        # s9 was asked for by the model, so its reasoning and status are
        # shown; s1 was only in the sample, so they are not.
        self.assertIn("read in full - it asked for this one", said)
        self.assertIn("three windows on one file", said)
        self.assertIn("as it appeared in the sample", said)
        # s2 was never in front of the model at all, and says so.
        self.assertIn("The model never saw this state", said)

    def _anchored_round(self, split=False):
        """A round whose report carries an anchoring."""
        report = _a_round("Rebuild the package.", True)
        report["layers"][0]["states"] = ["s1", "s2"]
        report["layers"][0]["id"] = "task_1"
        if split:
            # An anchor whose states ended in two different tasks, which
            # is the thing that must not happen. Without `children` the
            # two tasks hold exactly what is named here - a copy that
            # kept them held every state through its sub-tasks, and the
            # walk found all three under one task.
            report["layers"][0]["children"] = []
            report["layers"].append(dict(report["layers"][0],
                                         id="task_2", states=["s3"],
                                         children=[]))
        else:
            report["layers"][0]["states"] = ["s1", "s2", "s3"]
        report["similarity"]["anchored"] = {
            "of": 5, "named": 3, "joined": 3, "pulled": 1,
            "why": "What a state was open on is read before anything is scored.",
            "anchors": [{"on": ["c:/research/survey-weights/fetch.py"],
                         "states": 3, "ids": ["s1", "s2", "s3"],
                         "titles": ["One", "Two", "Three"]}],
            "by_word": [{"on": ["fieldnotes", "coauthor", "openreview"],
                         "kind": "words", "states": 2,
                         "ids": ["s4", "s5"],
                         "titles": ["Four", "Five"]}],
            "words": {"rare_under": 6, "must_share": 3, "pairs": 4,
                      "refused": 1, "considered": 2},
            "pulls": [{"a": "s1", "b": "s4", "a_title": "One",
                       "b_title": "Four", "share": 0.75, "was": 0.62,
                       "now": 0.39,
                       "under": ["c:/research/survey-weights"]}],
        }
        return report

    def test_the_page_shows_what_was_anchored(self) -> None:
        """Two phases, and the page has to show both or the grouping
        looks like one step that produced an answer."""
        verdict = self._in_the_page(
            "let err=window.__err||'';let text='';try{"
            "stateBeat.report=" + json.dumps(self._anchored_round()) + ";"
            "renderStateBeat();"
            "text=document.getElementById('dbg-statebeat-body').textContent;"
            "}catch(e){err=(e&&e.stack?e.stack:String(e)).split('\\n')[0];}"
            "document.title=JSON.stringify({err:err,text:text});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        said = " ".join(verdict["text"].split())
        self.assertIn("What was anchored, before anything was scored", said)
        self.assertIn("c:/research/survey-weights/fetch.py", said)
        # The states in the anchor, by name.
        for title in ("One", "Two", "Three"):
            self.assertIn(title, said)
        # Where the tree then put them.
        self.assertIn("all of them in task 1", said)

    def test_and_shows_what_was_only_drawn_nearer(self) -> None:
        """A shared folder is evidence and not proof, so the page has to
        keep the two apart: one joined them, the other did not."""
        verdict = self._in_the_page(
            "let err=window.__err||'';let text='';try{"
            "stateBeat.report=" + json.dumps(self._anchored_round()) + ";"
            "renderStateBeat();"
            "text=document.getElementById('dbg-statebeat-body').textContent;"
            "}catch(e){err=(e&&e.stack?e.stack:String(e)).split('\\n')[0];}"
            "document.title=JSON.stringify({err:err,text:text});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        said = " ".join(verdict["text"].split())
        self.assertIn("Drawn nearer", said)
        self.assertIn("These were not joined", said)
        # The distance before and after, so the pull can be judged.
        self.assertIn("0.62", said)
        self.assertIn("0.39", said)
        # And the states that named nothing say so.
        self.assertIn("2 states named no file and no page", said)

    def test_and_the_two_kinds_of_anchor_are_shown_apart(self) -> None:
        """A shared file and a shared vocabulary are different claims
        about two states, and a reader should be able to see which one
        put them together."""
        # Asked IN the page and answered with a short string: the
        # panel's whole text does not survive a document title, and a
        # probe that returns it reads back as nothing at all.
        want = ['Joined by what was open',
                'Joined by rare words they share',
                'fieldnotes',
                '1 by what was open, 1 by rare words']
        verdict = self._in_the_page(
            "let err=window.__err||'';let missing='';let kinds=0;try{"
            "stateBeat.report=" + json.dumps(self._anchored_round()) + ";"
            "renderStateBeat();"
            "const box=document.getElementById('dbg-statebeat-body');"
            "kinds=box.querySelectorAll('.sb-anchor-words').length;"
            "const said=box.textContent.replace(/[ \\n\\t]+/g, ' ');"
            "missing=" + json.dumps(want) + ".filter("
            "  (one)=>said.indexOf(one)<0).join('; ');"
            "}catch(e){err=String(e&&e.message?e.message:e);}"
            "document.title=JSON.stringify({err:err,missing:missing,"
            "kinds:kinds});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertEqual(verdict["missing"], "",
                         f"not on the page: {verdict['missing']}")
        self.assertEqual(verdict["kinds"], 1,
                         "the word anchors are not marked apart")

    def test_and_marks_an_anchor_that_came_apart(self) -> None:
        """An anchor split across two tasks is the one thing this
        mechanism must not do, so it is checked rather than assumed."""
        verdict = self._in_the_page(
            "let err=window.__err||'';let text='';try{"
            "stateBeat.report=" + json.dumps(self._anchored_round(split=True))
            + ";renderStateBeat();"
            "text=document.getElementById('dbg-statebeat-body').textContent;"
            "}catch(e){err=(e&&e.stack?e.stack:String(e)).split('\\n')[0];}"
            "document.title=JSON.stringify({err:err,text:text});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        said = " ".join(verdict["text"].split())
        self.assertIn("an anchor should not come apart", said)

    def test_everything_running_now_is_drawn_as_one_tree(self) -> None:
        """The tech tree further down draws the pieces of ONE task. This
        draws the tasks, which is the view a person wants when they sit
        down: what am I in the middle of, what started when, and what is
        waiting on something else."""
        report = _a_round("Rebuild the package.", True)
        report["plan"] = {
            "nodes": [
                {"id": "task_1", "number": 1, "title": "Rebuild the package.",
                 "size": 12, "closed": 4, "began": "2026-09-01T09:00:00+00:00",
                 "ended": "2026-09-06T17:00:00+00:00", "finished": True},
                {"id": "task_2", "number": 2, "title": "Answer the reviewer.",
                 "size": 7, "closed": 0, "began": "2026-09-05T09:00:00+00:00",
                 "ended": "2026-09-09T17:00:00+00:00", "finished": False},
                {"id": "task_3", "number": 3, "title": "Book the Lisbon trip.",
                 "size": 4, "closed": 0, "began": "2026-09-08T09:00:00+00:00",
                 "ended": "2026-09-09T12:00:00+00:00", "finished": False}],
            "edges": [{"before": "task_1", "after": "task_2"}],
            "why": "the letter needs a number out of the rebuilt package"}
        verdict = self._in_the_page(
            "let err=window.__err||'';let circles=0;let arrows=0;"
            "let names=0;let missing='';try{"
            "stateBeat.report=" + json.dumps(report) + ";renderStateBeat();"
            "const box=document.getElementById('dbg-statebeat-body');"
            "const tree=box.querySelector('.sb-whole-graph');"
            "circles=tree?tree.querySelectorAll('[data-sb-task]').length:0;"
            "arrows=tree?tree.querySelectorAll('.sb-tt-edge').length:0;"
            "names=tree?tree.querySelectorAll('.sb-tt-name').length:0;"
            "const said=box.textContent.replace(/[ \\n\\t]+/g, ' ');"
            "missing=" + json.dumps(
                ["Everything running now", "3 at the top",
                 "Answer the reviewer.", "Click a task"])
            + ".filter((one)=>said.indexOf(one)<0).join('; ');"
            "}catch(e){err=String(e&&e.message?e.message:e);}"
            "document.title=JSON.stringify({err:err,circles:circles,"
            "arrows:arrows,names:names,missing:missing});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertEqual(verdict["missing"], "",
                         f"not on the page: {verdict['missing']}")
        # A circle per task, a label beside each, and one arrow.
        self.assertEqual(verdict["circles"], 3)
        self.assertEqual(verdict["names"], 3)
        self.assertEqual(verdict["arrows"], 1)

    def test_a_task_keeps_its_emblem(self) -> None:
        """A row of identical circles gives the eye nothing to hold on
        to. The emblem is seeded from the task's ID, which is the one
        thing about a task that lasts - so it survives appending,
        re-summarising, growing and being found again by a rebuild, and
        a reader who has learnt "the one with the spokes" keeps it."""
        verdict = self._in_the_page(
            "let err=window.__err||'';let same=false;let differ=false;try{"
            "const a=sbEmblem('task-live-4f2a91','#fff',12);"
            "const b=sbEmblem('task-live-4f2a91','#fff',12);"
            "const c=sbEmblem('task-live-99bbcc','#fff',12);"
            "same=(a===b);differ=(a!==c);"
            "}catch(e){err=String(e&&e.message?e.message:e);}"
            "document.title=JSON.stringify({err:err,same:same,"
            "differ:differ});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertTrue(verdict["same"],
                        "the same task drew two different emblems")
        self.assertTrue(verdict["differ"],
                        "two tasks drew the same emblem")

    def test_and_the_circles_are_filled(self) -> None:
        report = _a_round("Rebuild the package.", True)
        report["plan"] = {
            "nodes": [
                {"id": "task_1", "title": "One.", "size": 9,
                 "began": "2026-09-01T09:00:00+00:00",
                 "ended": "2026-09-04T09:00:00+00:00", "finished": False},
                {"id": "task_2", "title": "Two.", "size": 5,
                 "began": "2026-09-03T09:00:00+00:00",
                 "ended": "2026-09-07T09:00:00+00:00", "finished": True}],
            "edges": []}
        verdict = self._in_the_page(
            "let err=window.__err||'';let hollow=0;let marked=0;try{"
            "stateBeat.report=" + json.dumps(report) + ";renderStateBeat();"
            "const tree=document.querySelector('.sb-whole-graph');"
            "hollow=[...tree.querySelectorAll('.sb-tt-disc')]"
            ".filter((one)=>one.getAttribute('fill')==='none').length;"
            "marked=tree.querySelectorAll('.sb-tt-emblem').length;"
            "}catch(e){err=String(e&&e.message?e.message:e);}"
            "document.title=JSON.stringify({err:err,hollow:hollow,"
            "marked:marked});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertEqual(verdict["hollow"], 0, "a circle is still hollow")
        self.assertEqual(verdict["marked"], 2, "a circle has no emblem")

    def test_and_the_card_reads_out_the_task_that_was_clicked(self) -> None:
        """The tree answers "what am I in the middle of" and cannot
        answer anything else - there is room beside a circle for a name
        and a count. The panel below is where the rest goes."""
        report = _a_round("Rebuild the package.", True)
        report["layers"][0]["id"] = "task_1"
        report["layers"][0]["summary"] = dict(
            report["layers"][0].get("summary") or {},
            title="Rebuild the package.",
            described={"where": "The user works in survey-weights/.",
                       "status": "The user has figure 1 matching."})
        report["plan"] = {
            "nodes": [
                {"id": "task_1", "title": "One.", "size": 9,
                 "began": "2026-09-01T09:00:00+00:00",
                 "ended": "2026-09-04T09:00:00+00:00", "finished": False},
                {"id": "task_2", "title": "Two.", "size": 5,
                 "began": "2026-09-03T09:00:00+00:00",
                 "ended": "2026-09-07T09:00:00+00:00", "finished": False}],
            "edges": []}
        verdict = self._in_the_page(
            "let err=window.__err||'';let before='';let after='';try{"
            "stateBeat.report=" + json.dumps(report) + ";renderStateBeat();"
            "const box=document.getElementById('dbg-statebeat-body');"
            "const card=box.querySelector('[data-sb-card]');"
            "before=card.textContent.trim().slice(0,40);"
            "box.querySelector('[data-sb-task=\"task_1\"]').dispatchEvent("
            "  new MouseEvent('click',{bubbles:true}));"
            "after=card.textContent.replace(/[ \\n\\t]+/g, ' ');"
            "}catch(e){err=String(e&&e.message?e.message:e);}"
            "document.title=JSON.stringify({err:err,before:before,"
            "after:after});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertIn("Click a task above", verdict["before"])
        self.assertIn("Rebuild the package.", verdict["after"])
        # The four parts are what a person comes back for.
        self.assertIn("Where the work lives", verdict["after"])
        self.assertIn("survey-weights", verdict["after"])
        self.assertIn("Where it stands", verdict["after"])

    def test_the_tasks_are_ordered_by_how_soon_and_can_be_reordered(self) -> None:
        """Urgency by default, because the question a person opens this
        with is what needs doing - the weighty thing is often the one
        that can wait a week."""
        report = _a_round("Rebuild the package.", True)
        base = report["layers"][0]
        report["layers"] = [
            dict(base, id="task_1", seat=0, children=[], states=["s1"],
                 summary=dict(base.get("summary") or {}, title="Calm but big.",
                              urgency=2, importance=7)),
            dict(base, id="task_2", seat=1, children=[], states=["s2"],
                 summary=dict(base.get("summary") or {}, title="Due tomorrow.",
                              urgency=7, importance=3))]
        report["plan"] = {"nodes": [
            {"id": "task_1", "title": "Calm but big.", "size": 9,
             "began": "2026-09-01T09:00:00+00:00",
             "ended": "2026-09-04T09:00:00+00:00"},
            {"id": "task_2", "title": "Due tomorrow.", "size": 5,
             "began": "2026-09-03T09:00:00+00:00",
             "ended": "2026-09-07T09:00:00+00:00"}], "edges": []}
        verdict = self._in_the_page(
            "let err=window.__err||'';let first='';let after='';try{"
            "stateBeat.report=" + json.dumps(report) + ";renderStateBeat();"
            "const box=document.getElementById('dbg-statebeat-body');"
            "const read=()=>[...box.querySelectorAll('.sb-tt-row')]"
            "  .map((one)=>one.dataset.sbTask).join(',');"
            "first=read();"
            "box.querySelector('[data-sb-sort=\"importance\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "after=read();"
            "}catch(e){err=String(e&&e.message?e.message:e);}"
            "document.title=JSON.stringify({err:err,first:first,"
            "after:after});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        # Urgency first: the one due tomorrow leads.
        self.assertEqual(verdict["first"], "task_2,task_1")
        # And by importance the big calm one does.
        self.assertEqual(verdict["after"], "task_1,task_2")

    def test_and_a_tie_keeps_the_order_it_had(self) -> None:
        """Two tasks the model marked the same are not ranked against
        each other by some further invention - they fall back to the
        seat, which is what the seats are for."""
        report = _a_round("Rebuild the package.", True)
        base = report["layers"][0]
        report["layers"] = [
            dict(base, id="task_1", seat=0, children=[], states=["s1"],
                 summary=dict(base.get("summary") or {}, title="First.",
                              urgency=5, importance=5)),
            dict(base, id="task_2", seat=1, children=[], states=["s2"],
                 summary=dict(base.get("summary") or {}, title="Second.",
                              urgency=5, importance=5))]
        report["plan"] = {"nodes": [
            {"id": "task_1", "title": "First.", "size": 3,
             "began": "2026-09-05T09:00:00+00:00",
             "ended": "2026-09-06T09:00:00+00:00"},
            {"id": "task_2", "title": "Second.", "size": 9,
             "began": "2026-09-01T09:00:00+00:00",
             "ended": "2026-09-02T09:00:00+00:00"}], "edges": []}
        verdict = self._in_the_page(
            "let err=window.__err||'';let order='';try{"
            "stateBeat.report=" + json.dumps(report) + ";renderStateBeat();"
            "order=[...document.querySelectorAll('.sb-tt-row')]"
            "  .map((one)=>one.dataset.sbTask).join(',');"
            "}catch(e){err=String(e&&e.message?e.message:e);}"
            "document.title=JSON.stringify({err:err,order:order});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        # Not by size, and not by date: by the seat they already had.
        self.assertEqual(verdict["order"], "task_1,task_2")

    def test_the_tree_is_never_fitted_to_its_box(self) -> None:
        """An SVG told to fit a box scales its whole drawing to get
        there, which is what kept shrinking the tree when a task was
        opened.

        This is asserted in the BROWSER, on the computed style, because
        the fault twice over was not the declaration but where it sat:
        `.sb-graph` sets `width: 100%; height: auto; max-height: 60vh`
        for the dendrogram, and two single-class rules carry the same
        weight - so the one that wins is the one written later. An
        override that reads correctly and sits above the rule it
        overrides does nothing at all, and nothing but the cascade can
        tell you that."""
        report = _a_round("Rebuild the package.", True)
        report["plan"] = {"nodes": [
            {"id": "task_1", "title": "One.", "size": 9,
             "began": "2026-09-01T09:00:00+00:00",
             "ended": "2026-09-04T09:00:00+00:00"},
            {"id": "task_2", "title": "Two.", "size": 5,
             "began": "2026-09-03T09:00:00+00:00",
             "ended": "2026-09-07T09:00:00+00:00"}], "edges": []}
        verdict = self._in_the_page(
            "let err=window.__err||'';let wide='';let tall='';let cap='';"
            "let frame='';try{"
            "stateBeat.report=" + json.dumps(report) + ";renderStateBeat();"
            "const svg=document.querySelector('.sb-whole-graph');"
            "const said=getComputedStyle(svg);"
            "wide=said.width;tall=said.height;cap=said.maxHeight;"
            "frame=getComputedStyle("
            "  svg.closest('.sb-tree-frame')).overflowY;"
            "}catch(e){err=String(e&&e.message?e.message:e);}"
            "document.title=JSON.stringify({err:err,wide:wide,tall:tall,"
            "cap:cap,frame:frame});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        # Nothing caps it, so nothing squeezes it.
        self.assertEqual(verdict["cap"], "none",
                         "something still limits the tree's height")
        # And the frame is what scrolls instead.
        self.assertIn(verdict["frame"], ("auto", "scroll"),
                      "the frame does not scroll, so it must be scaling")

    def test_the_picture_is_the_first_card_on_the_overview(self) -> None:
        """Where the tab opens, and clickable there.

        It is the answer to the question the page is opened with - what am
        I in the middle of - and the numbers under it are about the
        agent's own machinery. Drawn by the SAME function as the
        debugger's copy: a second function fed a second shape is how the
        two would drift, and the one nobody tested would be the one on
        the page people actually open.
        """
        picture = {
            "plan": {"nodes": [
                {"id": "task_1", "title": "The census fetch step.",
                 "size": 9, "began": "2026-09-28T09:00:00+00:00",
                 "ended": "2026-09-30T09:00:00+00:00"},
                {"id": "task_2", "title": "The figures.", "size": 5,
                 "began": "2026-09-29T09:00:00+00:00",
                 "ended": "2026-09-30T18:00:00+00:00"}],
                "edges": []},
            "layers": [
                {"id": "task_1", "size": 9, "states": ["s1"],
                 "summary": {"title": "The census fetch step.",
                             "summary": "Waiting out the rate limit.",
                             "concrete": True,
                             "described": {"status": "The user is blocked.",
                                           "urgency": 6, "importance": 5,
                                           "weighed_how": "read"}}},
                {"id": "task_2", "size": 5, "states": ["s2"],
                 "summary": {"title": "The figures.", "concrete": True,
                             "described": {"urgency": 3, "importance": 4,
                                           "weighed_how": "read"}}}],
            "states": [{"id": "s1", "status": "open",
                        "starts_at": "2026-09-28T09:00:00+00:00",
                        "ends_at": "2026-09-28T11:00:00+00:00"},
                       {"id": "s2", "status": "done",
                        "starts_at": "2026-09-29T09:00:00+00:00",
                        "ends_at": "2026-09-29T10:00:00+00:00"}],
            "running": False, "due": {"why": "7.0 hours since the last."},
        }
        verdict = self._in_the_page(
            "let err=window.__err||'';let first='';let rows=0;let cards=0;"
            "let reads='';let empty='';"
            "try{"
            "drawWhatIsRunning(" + json.dumps(picture) + ");"
            "const host=document.getElementById('ov-tasks');"
            "first=(document.querySelector('#overview .card')||{}).id||'';"
            "rows=host.querySelectorAll('.sb-tt-row').length;"
            "cards=host.querySelectorAll('[data-sb-card]').length;"
            "host.querySelector('[data-sb-task]').dispatchEvent("
            "  new MouseEvent('click',{bubbles:true}));"
            "reads=host.querySelector('[data-sb-card]').textContent"
            "  .replace(/[ \\n\\t]+/g,' ').trim();"
            "drawWhatIsRunning(null);"
            "empty=document.getElementById('ov-tasks').textContent"
            "  .replace(/[ \\n\\t]+/g,' ').trim();"
            "}catch(e){err=String(e&&e.message?e.message:e);}"
            "document.title=JSON.stringify({err:err,first:first,rows:rows,"
            "cards:cards,reads:reads,empty:empty});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        # FIRST on the page, before the three stat tiles.
        self.assertEqual(verdict["first"], "ov-tasks-card",
                         "the picture is not the first card on the overview")
        self.assertEqual(verdict["rows"], 2, "both tasks are not drawn")
        # ONE panel, not two - the drawing brings its own.
        self.assertEqual(verdict["cards"], 1,
                         "the overview grew a second detail panel")
        # And a click there reads out THERE, not into the debugger's copy.
        self.assertIn("census fetch step", verdict["reads"])
        self.assertIn("urgency 6", verdict["reads"])
        # Nothing to draw says why, because a blank reads as a fault.
        self.assertIn("not been asked about yet", verdict["empty"])

    def test_opening_a_task_with_no_pieces_does_not_move_the_view(
            self) -> None:
        """Reported from the running agent: clicking a node threw the
        picture to its right edge.

        The bottom layer used to be the states themselves, drawn as to-do
        items with their words to the LEFT of their circle, and the
        drawing asked for a left margin to fit them. That layer is gone -
        every layer is summarised work now - but the measurement stayed,
        and it counted a NOTE as leftward although a note is drawn to the
        right like everything else. A task with no pieces draws exactly
        one note, so clicking one asked for hundreds of pixels of margin
        and the frame was scrolled right to compensate.
        """
        report = {
                    "layers": [
                                {
                                            "id": "task_1",
                                            "size": 6,
                                            "states": [
                                                        "s1"
                                            ],
                                            "children": [],
                                            "why_closed": "Its pieces have not been worked out yet - the next round names them.",
                                            "summary": {
                                                        "title": "The census fetch step.",
                                                        "concrete": True,
                                                        "described": {
                                                                    "urgency": 6,
                                                                    "importance": 5,
                                                                    "weighed_how": "read"
                                                        }
                                            }
                                },
                                {
                                            "id": "task_2",
                                            "size": 4,
                                            "states": [
                                                        "s2"
                                            ],
                                            "children": [],
                                            "summary": {
                                                        "title": "The figures.",
                                                        "concrete": True,
                                                        "described": {
                                                                    "urgency": 3,
                                                                    "importance": 4,
                                                                    "weighed_how": "read"
                                                        }
                                            }
                                }
                    ],
                    "states": [
                                {
                                            "id": "s1",
                                            "status": "open"
                                },
                                {
                                            "id": "s2",
                                            "status": "done"
                                }
                    ],
                    "plan": {
                                "nodes": [
                                            {
                                                        "id": "task_1",
                                                        "title": "The census fetch step.",
                                                        "size": 6,
                                                        "began": "2026-08-01T09:00:00+00:00",
                                                        "ended": "2026-09-30T09:00:00+00:00"
                                            },
                                            {
                                                        "id": "task_2",
                                                        "title": "The figures.",
                                                        "size": 4,
                                                        "began": "2026-09-20T09:00:00+00:00",
                                                        "ended": "2026-09-29T09:00:00+00:00"
                                            }
                                ],
                                "edges": []
                    },
                    "running": False,
                    "due": {}
        }
        verdict = self._in_the_page(
            "let err=window.__err||'';let box='';let moved='';let note='';"
            "try{"
            "stateBeat.report=report0;renderStateBeat();"
            "const page=document.getElementById('dbg-statebeat-body');"
            "const frame=page.querySelector('.sb-tree-frame');"
            "frame.style.width='260px';frame.style.overflow='auto';"
            "frame.scrollLeft=90;"
            "const before=frame.scrollLeft;"
            "page.querySelector('[data-sb-task]').dispatchEvent("
            "  new MouseEvent('click',{bubbles:true}));"
            "box=page.querySelector('.sb-whole-graph')"
            "  .getAttribute('viewBox').split(' ')[0];"
            "moved=(frame.scrollLeft===before)?'held':"
            "  ('moved '+before+'->'+frame.scrollLeft);"
            "note=page.querySelector('.sb-tt-nothing')?'yes':'no';"
            "}catch(e){err=String(e&&e.message?e.message:e);}"
            "document.title=JSON.stringify({err:err,box:box,moved:moved,"
            "note:note});",
            {"report0": report})
        self.assertEqual(verdict["err"], "", verdict["err"])
        # A task is read, not opened, now that only tasks are described:
        # nothing unfolds under it - not even a note saying it is empty.
        self.assertEqual(verdict["note"], "no",
                         "a task was opened onto a note about its pieces")
        # NOTHING IS LEFT OF THE AXIS, so the box still starts at zero.
        self.assertEqual(float(verdict["box"]), 0.0,
                         "the drawing asked for a left margin it does not use")
        # And the reader keeps their place.
        self.assertEqual(verdict["moved"], "held", verdict["moved"])

    def test_clicking_a_project_keeps_the_hidden_button_and_frames_it(self) -> None:
        """Opened, a project draws lines down to its tasks; and opening it
        redraws from the whole picture, so the button for the rejected
        groupings - hidden by default - is still there afterwards."""
        def node(i, task_id, hidden=False):
            return {"id": f"task_{i}", "task_id": task_id, "title": f"T{i}",
                    "size": 5, "count": 9, "hidden": hidden,
                    "began": "2026-09-20T09:00:00+00:00",
                    "ended": f"2026-10-0{i}T09:00:00+00:00",
                    "summary": {"title": f"T{i}", "urgency": 7 - i}}
        nodes = [node(1, "a"), node(2, "b"), node(3, "c"), node(4, "d", True)]
        picture = {"plan": {"nodes": nodes, "edges": []},
                   "layers": [dict(n, states=[]) for n in nodes],
                   "projects": [{"id": "p1", "name": "The release",
                                 "tasks": ["a", "b"], "count": 18,
                                 "began": "2026-09-20T09:00:00+00:00",
                                 "ended": "2026-10-02T09:00:00+00:00",
                                 "stretches": []}],
                   "running": False, "due": {}}
        verdict = self._in_the_page(
            "let err=window.__err||'';let out={};try{"
            "const host=document.getElementById('ov-tasks');"
            "out.shownAtFirst=sbShowingHidden();"
            "drawWhatIsRunning(pic);"
            "out.buttonBefore=!!host.querySelector('[data-sb-hidden]');"
            "out.linksShut=host.querySelectorAll('.sb-tt-project-link').length;"
            "const disc=host.querySelector('[data-sb-project] .sb-tt-project-disc');"
            "const at={bubbles:true,button:0,pointerId:1};"
            "disc.dispatchEvent(new PointerEvent('pointerdown',at));"
            "disc.dispatchEvent(new PointerEvent('pointerup',at));"
            "disc.dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "out.buttonAfter=!!host.querySelector('[data-sb-hidden]');"
            "out.framedProject=!!host.querySelector("
            "  '[data-sb-project] .sb-tt-selection');"
            "host.querySelector('[data-sb-task=\"task_3\"] circle')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "out.framed=[...host.querySelectorAll('.sb-tt-selection')]"
            "  .map(f=>f.parentNode.dataset.sbTask||'P');"
            "const link=host.querySelector('.sb-tt-project-link');"
            "out.link=link?link.getAttribute('d'):'';"
            "out.rows=[...host.querySelectorAll('.sb-tt-row')]"
            "  .map(r=>r.dataset.sbProject?'P':r.dataset.sbTask).join(',');"
            "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err:err,out:out});",
            {"pic": picture})
        self.assertEqual(verdict["err"], "", verdict["err"])
        out = verdict["out"]
        self.assertFalse(out["shownAtFirst"], "rejected groupings shown by default")
        self.assertTrue(out["buttonBefore"])
        self.assertEqual(out["linksShut"], 0)
        # The project's tasks are not drawn under it - only the top layer.
        self.assertEqual(out["rows"], "P,task_3")
        self.assertTrue(out["buttonAfter"], "clicking a project lost the button")
        # What is selected is framed, and only that.
        self.assertTrue(out["framedProject"], "the clicked project is not framed")
        self.assertEqual(out["framed"], ["task_3"])
        # No lines down to tasks that are not drawn.
        self.assertEqual(out["link"], "")


    def test_a_redraw_keeps_where_the_picture_was_scrolled_to(self) -> None:
        """A wide picture scrolled to its right-hand end stayed there when
        it was drawn again - it went back to the left every time."""
        def node(i):
            return {"id": f"task_{i}", "task_id": f"t{i}", "title": "T" * 120,
                    "size": 5, "count": 9,
                    "began": "2026-01-01T09:00:00+00:00",
                    "ended": f"2026-10-0{i}T09:00:00+00:00",
                    "summary": {"title": "A long task name " * 6, "urgency": 4}}
        nodes = [node(1), node(2), node(3)]
        picture = {"plan": {"nodes": nodes, "edges": []},
                   "layers": [dict(n, states=[]) for n in nodes],
                   "running": False, "due": {}}
        verdict = self._in_the_page(
            "let err=window.__err||'';let out={};try{"
            "const host=document.getElementById('ov-tasks');"
            "host.style.width='300px';"
            "drawWhatIsRunning(pic);"
            "const boxes=[host,...host.querySelectorAll('*')]"
            "  .filter(b=>b.scrollWidth>b.clientWidth+5"
            "    && getComputedStyle(b).overflowX!=='visible');"
            "out.scrollable=boxes.length;"
            "if(boxes.length){const box=boxes[0];box.scrollLeft=box.scrollWidth;"
            "  out.before=box.scrollLeft;"
            "  drawWhatIsRunning(JSON.parse(JSON.stringify(pic)));"
            "  const again=[host,...host.querySelectorAll('*')]"
            "    .filter(b=>b.scrollWidth>b.clientWidth+5"
            "      && getComputedStyle(b).overflowX!=='visible')[0];"
            "  out.after=again?again.scrollLeft:-1;}"
            "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err:err,out:out});",
            {"pic": picture})
        self.assertEqual(verdict["err"], "", verdict["err"])
        out = verdict["out"]
        if not out.get("scrollable"):
            self.skipTest("the picture fitted; nothing to scroll")
        self.assertGreater(out["before"], 0)
        self.assertEqual(out["after"], out["before"])

    def test_the_overview_shows_only_work_from_the_last_week_by_default(self) -> None:
        """1, 7, 15, 30 or 180 days, and a week until somebody picks."""
        def node(i, days_ago):
            return {"id": f"task_{i}", "task_id": f"t{i}", "title": f"T{i}",
                    "size": 9, "count": 9, "began": "", "summary": {}}
        picture = {"plan": {"nodes": [], "edges": []}, "layers": [],
                   "running": False, "due": {}}
        verdict = self._in_the_page(
            "let err=window.__err||'';let out={};try{"
            "const ago=(d)=>new Date(Date.now()-d*86400000).toISOString();"
            "const node=(i,d)=>({id:'task_'+i,task_id:'t'+i,title:'T'+i,size:9,"
            "  count:9,began:ago(d+1),ended:ago(d),summary:{title:'T'+i}});"
            "pic.plan.nodes=[node(1,0.5),node(2,3),node(3,10),node(4,60)];"
            "pic.layers=pic.plan.nodes.map(n=>Object.assign({states:[]},n));"
            "const host=document.getElementById('ov-tasks');"
            "const rows=()=>[...host.querySelectorAll('[data-sb-task]')]"
            "  .map(r=>r.dataset.sbTask).sort().join(',');"
            "stateBeat.recentDays=undefined;drawWhatIsRunning(pic);"
            "out.week=rows();"
            "out.chosen=host.querySelector('[data-sb-recent].sb-sort:not(.secondary)')"
            "  .dataset.sbRecent;"
            "host.querySelector('[data-sb-recent=\"30\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "out.month=rows();"
            "host.querySelector('[data-sb-recent=\"1\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "out.day=rows();"
            "out.options=[...host.querySelectorAll('[data-sb-recent]')]"
            "  .map(b=>b.dataset.sbRecent).join(',');"
            "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err:err,out:out});",
            {"pic": picture})
        self.assertEqual(verdict["err"], "", verdict["err"])
        out = verdict["out"]
        self.assertEqual(out["chosen"], "7")
        self.assertEqual(out["week"], "task_1,task_2")
        self.assertEqual(out["month"], "task_1,task_2,task_3")
        self.assertEqual(out["day"], "task_1")
        self.assertEqual(out["options"], "1,7,15,30,180")

    def test_a_quiet_task_is_drawn_quieter_and_says_where_its_work_is(self) -> None:
        """A task nothing has been added to for days is still drawn, dimmed,
        and its card says so; every card says where the work mostly is."""
        nodes = [{"id": "task_1", "task_id": "a", "title": "The paper",
                  "size": 6, "count": 140, "phase": "quiet",
                  "region": ["c:/users/me/dropbox/research/weighting-paper"],
                  "began": "2026-08-01T09:00:00+00:00",
                  "ended": "2026-09-25T09:00:00+00:00"},
                 {"id": "task_2", "task_id": "b", "title": "Cooking",
                  "size": 5, "count": 12, "phase": "active", "region": [],
                  "began": "2026-09-20T09:00:00+00:00",
                  "ended": "2026-10-01T09:00:00+00:00"}]
        picture = {"plan": {"nodes": nodes, "edges": []},
                   "layers": [dict(n, summary={"title": n["title"]},
                                   states=[]) for n in nodes],
                   "running": False, "due": {}}
        verdict = self._in_the_page(
            "let err=window.__err||'';let out={};try{"
            "drawWhatIsRunning(pic);"
            "const host=document.getElementById('ov-tasks');"
            "out.quiet=host.querySelectorAll('.sb-tt-quiet').length;"
            "stateBeat.openTask='task_1';stateBeat.cardShows={};"
            "out.card=sbTaskCard(pic,'task_1');"
            "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err:err,out:out});",
            {"pic": picture})
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertEqual(verdict["out"]["quiet"], 1)
        card = verdict["out"]["card"]
        self.assertIn("140", card)
        self.assertIn("Quiet", card)
        self.assertIn("Mostly in", card)
        self.assertIn("research/weighting-paper", card)

    def test_the_card_shows_where_the_work_is_and_what_each_place_is_worth(self) -> None:
        """Clicked, a task says which places its work is in with each one's
        coverage, specificity and weight - and which states joined it this
        round, and which came near and did not, with why."""
        node = {"id": "task_1", "task_id": "a", "title": "The paper",
                "size": 6, "count": 40, "phase": "active", "weight": 61.3,
                "places": [{"place": "c:/users/me/research/paper",
                            "coverage": 0.8, "specific": 1.0, "weight": 0.8},
                           {"place": "mail.google.com", "coverage": 0.3,
                            "specific": 0.1, "weight": 0.03}],
                "this_round": {
                    "joined": [{"state": "s1", "title": "Editing the methods",
                                "distance": 0.31, "workspace": 0.8,
                                "place": "c:/users/me/research/paper",
                                "part": 1, "why": ""}],
                    "nearly": [{"state": "s2", "title": "Reading the news",
                                "distance": 0.62, "workspace": 0.0,
                                "place": "", "part": -1,
                                "why": "no task near enough"}]},
                "began": "2026-09-01T09:00:00+00:00",
                "ended": "2026-10-01T09:00:00+00:00"}
        other = dict(node, id="task_2", task_id="b", title="Other",
                     places=[], this_round={})
        picture = {"plan": {"nodes": [node, other], "edges": []},
                   "layers": [dict(n, summary={"title": n["title"]},
                                   states=[]) for n in (node, other)],
                   "running": False, "due": {}}
        verdict = self._in_the_page(
            "let err=window.__err||'';let out='';try{"
            "const box=document.createElement('div');"
            "box.innerHTML=sbTaskCard(pic,'task_1');"
            r"out=box.textContent.replace(/\s+/g,' ');"
            "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err:err,out:out});",
            {"pic": picture})
        self.assertEqual(verdict["err"], "", verdict["err"])
        card = verdict["out"]
        self.assertIn("Where the work is", card)
        self.assertIn("weight 61.3", card)
        self.assertIn("research/paper", card)
        self.assertIn("80%", card)
        self.assertIn("0.80", card)
        self.assertIn("This round", card)
        self.assertIn("1 joined, 1 came near and did not", card)
        self.assertIn("Editing the methods", card)
        self.assertIn("through piece 2", card)
        self.assertIn("no task near enough", card)

    def test_a_project_is_one_row_and_its_card_lists_its_tasks(self) -> None:
        """Projects are the top layer: a row of their own, sorted among the
        tasks in no project, that opens onto its tasks."""
        def node(i, task_id, title, urgency):
            return {"id": f"task_{i}", "task_id": task_id, "title": title,
                    "size": 5, "count": 9, "phase": "active",
                    "began": "2026-09-20T09:00:00+00:00",
                    "ended": f"2026-10-0{i}T09:00:00+00:00",
                    "summary": {"title": title, "urgency": urgency}}
        nodes = [node(1, "a", "Fix the heartbeat", 6),
                 node(2, "b", "Cooking", 4),
                 node(3, "c", "Write the release notes", 3)]
        picture = {"plan": {"nodes": nodes, "edges": [
                       {"before": "task_2", "after": "task_1"},
                       {"before": "task_2", "after": "task_3"}]},
                   "layers": [dict(n, states=[]) for n in nodes],
                   "projects": [{"id": "p1", "name": "The agent release",
                                 "why": "one program's release",
                                 "status": "The user is getting it out.",
                                 "home": "c:/users/me/desktop/agent_release",
                                 "tasks": ["a", "c"], "urgency": 6,
                                 "importance": 5, "count": 18,
                                 "began": "2026-09-20T09:00:00+00:00",
                                 "ended": "2026-10-03T09:00:00+00:00",
                                 "stretches": []}],
                   "running": False, "due": {}}
        verdict = self._in_the_page(
            "let err=window.__err||'';let out={};try{"
            "const rows=()=>[...document.getElementById('ov-tasks')"
            "  .querySelectorAll('.sb-tt-row')].map(r=>r.dataset.sbProject"
            "  ?'P':r.dataset.sbTask);"
            "stateBeat.openProjects=new Set();drawWhatIsRunning(pic);"
            "out.shut=rows();"
            "out.label=document.getElementById('ov-tasks')"
            "  .querySelector('[data-sb-project]').textContent"
            "  .replace(/\\s+/g,' ');"
            "document.documentElement.dataset.theme='light';"
            "out.light=sbColour(3,true);"
            "document.documentElement.dataset.theme='dark';"
            "out.dark=sbColour(3,true);"
            "out.arrows=document.getElementById('ov-tasks')"
            "  .querySelectorAll('.sb-tt-edge').length;"
            # A REAL CLICK: pressed, released and clicked, on the square -
            # the press is what a drag starts from, and a drag that took
            # the pointer swallowed the click.
            "const disc=document.getElementById('ov-tasks')"
            "  .querySelector('[data-sb-project] .sb-tt-project-disc');"
            "const at={bubbles:true,button:0,pointerId:1};"
            "disc.dispatchEvent(new PointerEvent('pointerdown',at));"
            "disc.dispatchEvent(new PointerEvent('pointerup',at));"
            "disc.dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "out.open=rows();"
            "const box=document.createElement('div');"
            "box.innerHTML=sbProjectCard(pic,'p1');"
            "out.card=box.textContent.replace(/\\s+/g,' ');"
            "box.innerHTML=sbTaskCard(pic,'task_1');"
            "out.task=box.textContent.replace(/\\s+/g,' ');"
            "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err:err,out:out});",
            {"pic": picture})
        self.assertEqual(verdict["err"], "", verdict["err"])
        out = verdict["out"]
        # Shut: the project, sorted by its most urgent task (6) above
        # Cooking (4); its own tasks are not drawn.
        self.assertEqual(out["shut"], ["P", "task_2"])
        # The two arrows into its tasks are one arrow into the project.
        self.assertEqual(out["arrows"], 1)
        # Open: its tasks hang under it.
        # ONLY THE TOP LAYER: clicked, the project stays one row - its card
        # lists its tasks, and none is drawn under it.
        self.assertEqual(out["open"], ["P", "task_2"])
        self.assertIn("The user is getting it out.", out["card"])
        self.assertIn("urgency 6/7", out["card"])
        self.assertIn("Write the release notes", out["card"])
        self.assertIn("Part of", out["task"])
        # A fuller label: a tag, the name, what it holds, where it stands.
        self.assertIn("PROJECT", out["label"])
        self.assertIn("18 states", out["label"])
        self.assertIn("The user is getting it out.", out["label"])
        # Lighter on a dark page, where a mid-tone purple could not be read.
        self.assertIn("70%)", out["dark"])
        self.assertIn("42%)", out["light"])

    def test_a_task_picked_up_again_is_drawn_with_its_gap(self) -> None:
        nodes = [{"id": "task_1", "task_id": "a", "title": "The paper",
                  "size": 6, "count": 40, "phase": "active",
                  "stretches": [["2026-03-01T09:00:00+00:00",
                                 "2026-03-20T09:00:00+00:00"],
                                ["2026-09-25T09:00:00+00:00",
                                 "2026-10-01T09:00:00+00:00"]],
                  "began": "2026-03-01T09:00:00+00:00",
                  "ended": "2026-10-01T09:00:00+00:00"},
                 {"id": "task_2", "task_id": "b", "title": "Cooking",
                  "size": 5, "count": 12, "phase": "active",
                  "began": "2026-09-20T09:00:00+00:00",
                  "ended": "2026-10-01T09:00:00+00:00"}]
        picture = {"plan": {"nodes": nodes, "edges": []},
                   "layers": [dict(n, summary={"title": n["title"]},
                                   states=[]) for n in nodes],
                   "running": False, "due": {}}
        verdict = self._in_the_page(
            "let err=window.__err||'';let out={};try{"
            "drawWhatIsRunning(pic);"
            "const host=document.getElementById('ov-tasks');"
            "const row=host.querySelector('[data-sb-task=\"task_1\"]');"
            "out.segments=row.querySelectorAll('.sb-tt-span:not(.sb-tt-span-gap)').length;"
            "out.gaps=row.querySelectorAll('.sb-tt-span-gap').length;"
            "out.card=sbTaskCard(pic,'task_1');"
            "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err:err,out:out});",
            {"pic": picture})
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertEqual(verdict["out"]["segments"], 2)
        self.assertEqual(verdict["out"]["gaps"], 1)
        self.assertIn("2 stretches", verdict["out"]["card"])
        self.assertIn("6 months", verdict["out"]["card"])

    def test_hiding_the_rejected_keeps_the_axis_and_it_opens_on_the_recent(self) -> None:
        """Hiding the rejected groupings moved every date: the time axis
        was taken from the rows drawn, and a rejected heap that began in
        March set where it started. And the frame opened at the far right
        end, past the newest disc by the width of its name. The axis is
        now the whole's, drawn or not; the frame opens where most of the
        recent tasks are; and the reader's place survives the button."""
        nodes = [{"id": "old", "title": "A rejected heap", "size": 4,
                  "hidden": True, "began": "2026-03-01T09:00:00+00:00",
                  "ended": "2026-03-05T09:00:00+00:00"}]
        for i in range(8):
            nodes.append({"id": f"t{i}", "title": f"Task {i}", "size": 4,
                          "began": f"2026-09-{10 + i:02d}T09:00:00+00:00",
                          "ended": f"2026-09-{20 + i:02d}T09:00:00+00:00"})
        nodes.append({"id": "early", "title": "An early task", "size": 4,
                      "began": "2026-05-01T09:00:00+00:00",
                      "ended": "2026-05-03T09:00:00+00:00"})
        picture = {"plan": {"nodes": nodes, "edges": []},
                   "layers": [{"id": n["id"], "task_id": n["id"],
                               "hidden": n.get("hidden", False),
                               "summary": {"title": n["title"]}}
                              for n in nodes],
                   "running": False, "due": {}}
        verdict = self._in_the_page(
            "let err=window.__err||'';let out={};try{"
            "stateBeat.showHidden=false;"
            "const s=document.createElement('style');"
            "s.textContent='#ov-tasks .sb-tree-frame{width:420px!important;"
            "overflow:auto!important;display:block!important}"
            " #ov-tasks,.panel,.view,section{display:block!important}';"
            "document.head.appendChild(s);"
            "drawWhatIsRunning(pic);"
            "const host=document.getElementById('ov-tasks');"
            "const at=()=>Object.fromEntries([...host.querySelectorAll("
            "  '[data-sb-disc]')].map(d=>[d.closest('[data-sb-task]')"
            "  ?d.closest('[data-sb-task]').dataset.sbTask:'?',"
            "  d.getAttribute('cx')]));"
            "const frame=host.querySelector('.sb-tree-frame');"
            "const r=frame.getBoundingClientRect();"
            "out.inView=[...host.querySelectorAll('[data-sb-disc]')]"
            "  .filter(d=>{const b=d.getBoundingClientRect();"
            "  return b.left>=r.left&&b.right<=r.right;}).length;"
            "out.hidden=at();frame.scrollLeft=200;"
            "host.querySelector('[data-sb-hidden]').dispatchEvent("
            "  new MouseEvent('click',{bubbles:true}));"
            "out.shown=at();"
            "out.after=host.querySelector('.sb-tree-frame').scrollLeft;"
            "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err:err,out:out});",
            {"pic": picture})
        self.assertEqual(verdict["err"], "", verdict["err"])
        out = verdict["out"]
        self.assertEqual(out["inView"], 8,
                         "the frame did not open on the eight recent tasks")
        for task, x in out["hidden"].items():
            self.assertEqual(out["shown"].get(task), x,
                             f"{task} moved when the rejected were shown")
        self.assertEqual(len(out["shown"]), len(out["hidden"]) + 1)
        self.assertEqual(out["after"], 200, "the reader's place was lost")

    def test_a_row_names_both_weights_and_what_they_are_out_of(self) -> None:
        """It said "3/3", which reads as three out of three.

        They are two different judgements on two different 1-to-7 scales.
        Unnamed, a reader cannot tell which number is which; unscaled,
        they cannot tell whether 3 is high. The card beside the picture
        already says "/7", so the row says it the same way.

        The width is measured from this line too: it is usually longer
        than the name, was drawn at the same place, and ran off the
        right-hand edge where the furthest task's words could not be
        reached at all.
        """
        report = _a_round("Rebuild the package.", True)
        report["layers"] = [
            {"id": "task_1", "size": 75, "states": [], "children": [],
             "summary": {"title": "Answer reviewer 2 on the method section "
                                  "of the weighting paper.",
                         "concrete": True, "urgency": 3, "importance": 6,
                         "described": {"urgency": 3, "importance": 6}}},
            {"id": "task_2", "size": 4, "states": [], "children": [],
             "summary": {"title": "The figures.", "concrete": True,
                         "urgency": 5, "importance": 2,
                         "described": {"urgency": 5, "importance": 2}}}]
        report["plan"] = {"nodes": [
            {"id": "task_1", "title": "Answer reviewer 2 on the method "
                                      "section of the weighting paper.",
             "size": 75, "began": "2026-08-01T09:00:00+00:00",
             "ended": "2026-09-30T09:00:00+00:00"},
            {"id": "task_2", "title": "The figures.", "size": 4,
             "began": "2026-09-20T09:00:00+00:00",
             "ended": "2026-09-29T09:00:00+00:00"}], "edges": []}
        verdict = self._in_the_page(
            "let err=window.__err||'';let said='';let fits='';"
            "try{"
            "stateBeat.report=report0;renderStateBeat();"
            "const page=document.getElementById('dbg-statebeat-body');"
            "said=[...page.querySelectorAll('.sb-tt-aside')]"
            "  .map(n=>n.textContent).join(' // ');"
            "const svg=page.querySelector('.sb-whole-graph');"
            "const box=Number(svg.getAttribute('viewBox').split(' ')[2]);"
            "const ends=[...page.querySelectorAll('.sb-tt-aside')]"
            "  .map(n=>Number(n.getAttribute('x'))"
            "    +n.textContent.length*5.6);"
            "fits=(Math.max(...ends)<=box)?'fits':"
            "  ('cut off by '+Math.round(Math.max(...ends)-box));"
            "}catch(e){err=String(e&&e.message?e.message:e);}"
            "document.title=JSON.stringify({err:err,said:said,fits:fits});",
            {"report0": report})
        self.assertEqual(verdict["err"], "", verdict["err"])
        # Named, and out of seven.
        self.assertIn("urgency 3/7", verdict["said"])
        self.assertIn("importance 6/7", verdict["said"])
        self.assertNotIn("3/6", verdict["said"],
                         "the two numbers are still printed as a ratio")
        # AND THE DRAWING IS WIDE ENOUGH FOR IT, which is the half of this
        # that a label alone would not have fixed.
        self.assertEqual(verdict["fits"], "fits", verdict["fits"])

    def test_a_click_reads_the_report_its_own_picture_was_drawn_from(
            self) -> None:
        """The tree is drawn in two places and they can hold different
        rounds. A click used to look the task up in whichever report the
        debugger last saw, find nothing to put under it, and do nothing.
        """
        report = {"layers": [{"id": "task_1", "size": 9, "states": [], "children": [{"id": "task_1.1", "size": 5, "states": [], "summary": {"title": "The fetch step.", "concrete": True}, "children": [{"id": "task_1.1.1", "size": 2, "states": [], "children": [], "why_closed": "nothing divides again", "summary": {"title": "Third layer A.", "concrete": True}}, {"id": "task_1.1.2", "size": 3, "states": [], "children": [], "why_closed": "nothing divides again", "summary": {"title": "Third layer B.", "concrete": True}}]}, {"id": "task_1.2", "size": 4, "states": [], "children": [], "why_closed": "nothing divides again", "summary": {"title": "The figures.", "concrete": True}}], "summary": {"title": "Task one.", "concrete": True, "urgency": 5, "importance": 4, "described": {}}}, {"id": "task_2", "size": 7, "states": [], "children": [{"id": "task_2.1", "size": 4, "states": [], "children": [], "why_closed": "nothing divides again", "summary": {"title": "Another piece.", "concrete": True}}, {"id": "task_2.2", "size": 3, "states": [], "children": [], "why_closed": "nothing divides again", "summary": {"title": "And another.", "concrete": True}}], "summary": {"title": "Task two.", "concrete": True, "urgency": 3, "importance": 2, "described": {}}}], "states": [], "running": False, "plan": {"nodes": [{"id": "task_1", "title": "Task one.", "size": 9, "children": 2, "inside": 4, "began": "2026-09-01T09:00:00+00:00", "ended": "2026-09-20T09:00:00+00:00"}, {"id": "task_2", "title": "Task two.", "size": 7, "children": 2, "inside": 2, "began": "2026-09-05T09:00:00+00:00", "ended": "2026-09-22T09:00:00+00:00"}], "edges": []}}
        verdict = self._in_the_page(
            "let err=window.__err||'';let opened=0;"
            "try{"
            "stateBeat.report={layers:[{id:'somewhere_else',children:[],"
            "  summary:{}}],states:[],plan:{nodes:[],edges:[]}};"
            "drawWhatIsRunning(report0);"
            "const host=document.getElementById('ov-tasks');"
            "host.querySelector('[data-sb-task]').dispatchEvent("
            "  new MouseEvent('click',{bubbles:true}));"
            "const card=host.closest('section,body').querySelector('[data-sb-card]');"
            "opened=(card?card.textContent:'').includes('Task one.')?1:0;"
            "opened+=host.querySelectorAll('[data-sb-piece]').length;"
            "}catch(e){err=String(e&&e.message?e.message:e);}"
            "document.title=JSON.stringify({err:err,opened:opened});",
            {"report0": report})
        self.assertEqual(verdict["err"], "", verdict["err"])
        # Its card is from the picture it was clicked in - and nothing
        # unfolds under it: only the top layer is described.
        self.assertEqual(verdict["opened"], 1,
                         "the click used the wrong report, or unfolded pieces")

    def test_a_row_says_how_many_states_and_nothing_about_what_is_inside(self) -> None:
        """A task is read, not opened onto pieces, so its row counts the
        states it holds and says nothing of sub-tasks - not even when the
        report still carries some."""
        report = {"layers": [{"id": "task_1", "size": 9, "states": [], "children": [{"id": "task_1.1", "size": 5, "states": [], "summary": {"title": "The fetch step.", "concrete": True}, "children": [{"id": "task_1.1.1", "size": 2, "states": [], "children": [], "why_closed": "nothing divides again", "summary": {"title": "Third layer A.", "concrete": True}}, {"id": "task_1.1.2", "size": 3, "states": [], "children": [], "why_closed": "nothing divides again", "summary": {"title": "Third layer B.", "concrete": True}}]}, {"id": "task_1.2", "size": 4, "states": [], "children": [], "why_closed": "nothing divides again", "summary": {"title": "The figures.", "concrete": True}}], "summary": {"title": "Task one.", "concrete": True, "urgency": 5, "importance": 4, "described": {}}}, {"id": "task_2", "size": 7, "states": [], "children": [{"id": "task_2.1", "size": 4, "states": [], "children": [], "why_closed": "nothing divides again", "summary": {"title": "Another piece.", "concrete": True}}, {"id": "task_2.2", "size": 3, "states": [], "children": [], "why_closed": "nothing divides again", "summary": {"title": "And another.", "concrete": True}}], "summary": {"title": "Task two.", "concrete": True, "urgency": 3, "importance": 2, "described": {}}}], "states": [], "running": False, "plan": {"nodes": [{"id": "task_1", "title": "Task one.", "size": 9, "children": 2, "inside": 4, "began": "2026-09-01T09:00:00+00:00", "ended": "2026-09-20T09:00:00+00:00"}, {"id": "task_2", "title": "Task two.", "size": 7, "children": 2, "inside": 2, "began": "2026-09-05T09:00:00+00:00", "ended": "2026-09-22T09:00:00+00:00"}], "edges": []}}
        verdict = self._in_the_page(
            "let err=window.__err||'';let said='';"
            "try{"
            "stateBeat.report=report0;renderStateBeat();"
            "const page=document.getElementById('dbg-statebeat-body');"
            "said=[...page.querySelectorAll('.sb-tt-aside')]"
            "  .map(n=>n.textContent).join(' | ');"
            "}catch(e){err=String(e&&e.message?e.message:e);}"
            "document.title=JSON.stringify({err:err,said:said});",
            {"report0": report})
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertIn("9 states", verdict["said"])
        self.assertIn("7 states", verdict["said"])
        self.assertNotIn("sub-task", verdict["said"])
        self.assertNotIn("in all", verdict["said"])
        self.assertNotIn("pieces", verdict["said"])

    def test_every_control_redraws_the_picture_it_was_used_on(self) -> None:
        """Four controls worked in the debugger and did nothing on the
        overview: sorting, shutting, zooming and the detail card.

        The picture was a debugger panel for its whole life, so every
        control written for it called `renderStateBeat` - which rewrites
        that one panel. Moving the DRAWING to the overview without moving
        the controls that redraw it left each of them changing a number
        and leaving the screen alone, which is indistinguishable from
        being broken.
        """
        report = {
            "layers": [
                {"id": "task_1", "size": 9, "states": [], "children": [],
                 "summary": {"title": "Task one.", "concrete": True,
                             "urgency": 2, "importance": 7, "described": {}}},
                {"id": "task_2", "size": 4, "states": [], "children": [],
                 "summary": {"title": "Task two.", "concrete": True,
                             "urgency": 6, "importance": 1, "described": {}}}],
            "states": [], "running": False, "due": {},
            "plan": {"nodes": [
                {"id": "task_1", "title": "Task one.", "size": 9,
                 "began": "2026-09-01T09:00:00+00:00",
                 "ended": "2026-09-20T09:00:00+00:00"},
                {"id": "task_2", "title": "Task two.", "size": 4,
                 "began": "2026-09-05T09:00:00+00:00",
                 "ended": "2026-09-22T09:00:00+00:00"}], "edges": []}}
        verdict = self._in_the_page(
            "let err=window.__err||'';let urgent='';let weighty='';"
            "let before='';let after='';"
            "try{"
            "drawWhatIsRunning(report0);"
            "const host=document.getElementById('ov-tasks');"
            "const names=()=>[...host.querySelectorAll('.sb-tt-name')]"
            "  .map(n=>n.textContent).join(',');"
            "urgent=names();"
            "host.querySelector('[data-sb-sort=\"importance\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "weighty=names();"
            "const svg=()=>host.querySelector('.sb-whole-graph');"
            "before=svg().style.width;"
            "sbZoomTreeBy(1.25);"
            "after=host.querySelector('.sb-whole-graph').style.width;"
            "}catch(e){err=String(e&&e.message?e.message:e);}"
            "document.title=JSON.stringify({err:err,urgent:urgent,"
            "weighty:weighty,before:before,after:after});",
            {"report0": report})
        self.assertEqual(verdict["err"], "", verdict["err"])
        # SORTING REORDERS WHAT IS ON SCREEN, not just a flag: urgency
        # puts task two first, importance puts task one first.
        self.assertEqual(verdict["urgent"], "Task two.,Task one.")
        self.assertEqual(verdict["weighty"], "Task one.,Task two.",
                         "sorting changed nothing on the overview")
        # AND ZOOMING CHANGES THE DRAWN SIZE.
        self.assertTrue(verdict["before"], "no width was set")
        self.assertNotEqual(verdict["before"], verdict["after"],
                            "zooming changed nothing on the overview")
        self.assertGreater(float(verdict["after"].replace("px", "")),
                           float(verdict["before"].replace("px", "")))

    def test_the_order_really_is_by_urgency_then_by_importance(self) -> None:
        """"In order of urgency" was the order they already happened to be
        in.

        `sbWeighOf` read `task.summary.urgency`, and the ordering hands it
        PLAN NODES - an id, a span and a size, and no summary at all. Every
        task scored the default 4, so the sort found nothing to separate
        them. The weights live on the layers; they are looked up by id.
        """
        report = {
                    "summarising": "",
                    "states": [],
                    "running": False,
                    "due": {},
                    "layers": [
                                {
                                            "id": "task_1",
                                            "size": 5,
                                            "states": [],
                                            "children": [],
                                            "why_closed": "x",
                                            "summary": {
                                                        "title": "Task task_1 here.",
                                                        "concrete": True,
                                                        "urgency": 2,
                                                        "importance": 7,
                                                        "described": {
                                                                    "urgency": 2,
                                                                    "importance": 7
                                                        }
                                            }
                                },
                                {
                                            "id": "task_2",
                                            "size": 5,
                                            "states": [],
                                            "children": [],
                                            "why_closed": "x",
                                            "summary": {
                                                        "title": "Task task_2 here.",
                                                        "concrete": True,
                                                        "urgency": 6,
                                                        "importance": 1,
                                                        "described": {
                                                                    "urgency": 6,
                                                                    "importance": 1
                                                        }
                                            }
                                },
                                {
                                            "id": "task_3",
                                            "size": 5,
                                            "states": [],
                                            "children": [],
                                            "why_closed": "x",
                                            "summary": {
                                                        "title": "Task task_3 here.",
                                                        "concrete": True,
                                                        "urgency": 4,
                                                        "importance": 4,
                                                        "described": {
                                                                    "urgency": 4,
                                                                    "importance": 4
                                                        }
                                            }
                                },
                                {
                                            "id": "task_4",
                                            "size": 5,
                                            "states": [],
                                            "children": [],
                                            "why_closed": "x",
                                            "summary": {
                                                        "title": "Task task_4 here.",
                                                        "concrete": True,
                                                        "urgency": 7,
                                                        "importance": 3,
                                                        "described": {
                                                                    "urgency": 7,
                                                                    "importance": 3
                                                        }
                                            }
                                }
                    ],
                    "plan": {
                                "nodes": [
                                            {
                                                        "id": "task_1",
                                                        "title": "Task task_1 here.",
                                                        "size": 5,
                                                        "began": "2026-09-01T09:00:00+00:00",
                                                        "ended": "2026-09-20T09:00:00+00:00"
                                            },
                                            {
                                                        "id": "task_2",
                                                        "title": "Task task_2 here.",
                                                        "size": 5,
                                                        "began": "2026-09-01T09:00:00+00:00",
                                                        "ended": "2026-09-20T09:00:00+00:00"
                                            },
                                            {
                                                        "id": "task_3",
                                                        "title": "Task task_3 here.",
                                                        "size": 5,
                                                        "began": "2026-09-01T09:00:00+00:00",
                                                        "ended": "2026-09-20T09:00:00+00:00"
                                            },
                                            {
                                                        "id": "task_4",
                                                        "title": "Task task_4 here.",
                                                        "size": 5,
                                                        "began": "2026-09-01T09:00:00+00:00",
                                                        "ended": "2026-09-20T09:00:00+00:00"
                                            }
                                ],
                                "edges": []
                    }
        }
        verdict = self._in_the_page(
            "let err=window.__err||'';let urgent='';let weighty='';"
            "try{"
            "drawWhatIsRunning(report0);"
            "const host=document.getElementById('ov-tasks');"
            "const order=()=>[...host.querySelectorAll('[data-sb-task]')]"
            "  .map(r=>r.dataset.sbTask).join(',');"
            "urgent=order();"
            "host.querySelector('[data-sb-sort=\"importance\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "weighty=order();"
            "}catch(e){err=String(e&&e.message?e.message:e);}"
            "document.title=JSON.stringify({err:err,urgent:urgent,"
            "weighty:weighty});",
            {"report0": report})
        self.assertEqual(verdict["err"], "", verdict["err"])
        # urgency 7, 6, 4, 2
        self.assertEqual(verdict["urgent"], "task_4,task_2,task_3,task_1")
        # importance 7, 4, 3, 1
        self.assertEqual(verdict["weighty"], "task_1,task_3,task_4,task_2")

    def test_each_task_is_drawn_in_its_own_colour(self) -> None:
        """Found by looking at the picture, not by counting elements.

        `stateBeatTopIndex` matches on shared STATE ids - which works for
        the groups it was written for, and not at all for the tree, whose
        nodes carry an id, a span and a size and no states. Every node
        fell through to the default 0, so every task was drawn the same
        colour: six circles, six names, one hue. Nothing about the number
        or the shape of the elements was wrong, which is why six rounds of
        checking counts never found it.
        """
        report = {"summarising": "", "states": [], "running": False,
                  "due": {}, "layers": [], "plan": {"nodes": [], "edges": []}}
        for number in range(4):
            report["layers"].append({
                "id": f"task_{number + 1}", "task_id": f"stored-{number}",
                "size": 5, "states": [], "children": [], "why_closed": "x",
                "summary": {"title": f"Task {number + 1}.", "concrete": True,
                            "urgency": 4, "importance": 4, "described": {}}})
            report["plan"]["nodes"].append({
                "id": f"task_{number + 1}", "task_id": f"stored-{number}",
                "title": f"Task {number + 1}.", "size": 5,
                "began": "2026-09-01T09:00:00+00:00",
                "ended": "2026-09-20T09:00:00+00:00"})
        verdict = self._in_the_page(
            "let err=window.__err||'';let hues='';let seeds='';"
            "try{"
            "drawWhatIsRunning(report0);"
            "const host=document.getElementById('ov-tasks');"
            "hues=[...host.querySelectorAll('[data-sb-task] .sb-tt-disc')]"
            "  .map(c=>c.getAttribute('fill')).join(',');"
            "}catch(e){err=String(e&&e.message?e.message:e);}"
            "document.title=JSON.stringify({err:err,hues:hues});",
            {"report0": report})
        self.assertEqual(verdict["err"], "", verdict["err"])
        hues = [one for one in verdict["hues"].split(",") if one]
        self.assertEqual(len(hues), 4, "not every task was drawn")
        self.assertEqual(len(set(hues)), 4,
                         f"tasks share a colour: {verdict['hues']}")

    def test_and_one_task_alone_is_still_drawn(self) -> None:
        """The picture is when the work happened as well as what waits on
        what - and in a window of the last day, one task is ordinary."""
        report = _a_round("Rebuild the package.", True)
        report["plan"] = {"nodes": [{"id": "task_1", "title": "Only one.",
                                     "size": 3, "began": "2026-09-01T09:00:00+00:00",
                                     "ended": "2026-09-02T09:00:00+00:00"}],
                          "edges": []}
        verdict = self._in_the_page(
            "let err=window.__err||'';let trees=0;try{"
            "stateBeat.report=" + json.dumps(report) + ";renderStateBeat();"
            "trees=document.querySelectorAll('.sb-whole-graph').length;"
            "}catch(e){err=String(e&&e.message?e.message:e);}"
            "document.title=JSON.stringify({err:err,trees:trees});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertEqual(verdict["trees"], 1)

    def test_a_prompt_can_be_taken_off_the_page_whole(self) -> None:
        """Reading one on the page is not the same as having it: what a
        person does with a prompt that produced an odd summary is paste it
        somewhere and change a line. Selecting sixteen thousand characters
        out of a scrolling pre is a minute of dragging that the panel then
        interrupts."""
        report = _a_round("Rebuild the package.", True)
        verdict = self._in_the_page(
            "let err=window.__err||'';let copied='';let buttons=0;try{"
            "stateBeat.report=" + json.dumps(report) + ";renderStateBeat();"
            "const box=document.getElementById('dbg-statebeat-body');"
            "buttons=box.querySelectorAll('[data-sb-copy]').length;"
            # The clipboard is not available to a file:// page, so the
            # copy is read from what the page would have put on it.
            "const one=box.querySelector('[data-sb-copy]');"
            "copied=sbPromptText(one.closest('details'));"
            "}catch(e){err=String(e);}"
            "document.title=JSON.stringify({err:err,copied:copied,"
            "buttons:buttons});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertGreater(verdict["buttons"], 0, "no copy button was drawn")
        # Both halves: the instruction and the message, which is what
        # "the whole prompt" means.
        self.assertIn("THE INSTRUCTION, SENT WITH EVERY SUMMARY",
                      verdict["copied"])
        self.assertIn("THE WHOLE PROMPT, ENTIRE", verdict["copied"])

    def test_and_it_does_not_take_what_came_back(self) -> None:
        """A button labelled copy beside the words "the prompt it was
        sent" must not hand over the model's replies as though they were
        part of it."""
        report = _a_round("Rebuild the package.", True)
        report["communities"][0]["summary"]["answer"] = "A REPLY, NOT A PROMPT"
        report["communities"][0]["summary"]["exchange"] = [
            {"call": 1, "reply": "A REPLY, NOT A PROMPT", "sent": "x",
             "was_json": True, "asked_to_search": False}]
        verdict = self._in_the_page(
            "let err=window.__err||'';let copied='';try{"
            "stateBeat.report=" + json.dumps(report) + ";renderStateBeat();"
            "const box=document.getElementById('dbg-statebeat-body');"
            "const one=[...box.querySelectorAll('[data-sb-copy]')]"
            ".find((b)=>b.dataset.sbCopy.startsWith('task-'));"
            "copied=one?sbPromptText(one.closest('details')):'(no button)';"
            "}catch(e){err=String(e);}"
            "document.title=JSON.stringify({err:err,copied:copied});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertNotIn("(no button)", verdict["copied"])
        self.assertIn("THE WHOLE PROMPT, ENTIRE", verdict["copied"])
        self.assertNotIn("A REPLY, NOT A PROMPT", verdict["copied"])

    def test_and_copying_does_not_open_the_block(self) -> None:
        """The button sits in a summary, so a plain click would toggle the
        block as well as copy it."""
        report = _a_round("Rebuild the package.", True)
        verdict = self._in_the_page(
            "let err=window.__err||'';let opened=null;try{"
            "stateBeat.report=" + json.dumps(report) + ";renderStateBeat();"
            "const box=document.getElementById('dbg-statebeat-body');"
            "const one=box.querySelector('[data-sb-copy]');"
            "const block=one.closest('details');"
            "const before=block.open;"
            "one.click();"
            "opened=(block.open!==before);"
            "}catch(e){err=String(e);}"
            "document.title=JSON.stringify({err:err,opened:opened});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertFalse(verdict["opened"],
                         "copying opened or closed the block it sits in")

    def test_everything_on_the_page_starts_closed(self) -> None:
        """One rule, tested as one rule.

        Blocks opened themselves for good local reasons - the step's
        prompt, so it could be read while the answer was awaited; the top
        layer, so a finished run showed something; the comparison, because
        it is the point of a rehearsal - and the sum of those reasons was
        a page that unrolled everything it had the moment it finished.
        Testing them one at a time is how it got there, so this asks the
        whole question: after a round, how many things are open."""
        report = _a_round("Rebuild the package.", True)
        verdict = self._in_the_page(
            "let err=window.__err||'';let open=-1;let blocks=-1;try{"
            "stateBeat.report=" + json.dumps(report) + ";renderStateBeat();"
            "const box=document.getElementById('dbg-statebeat-body');"
            "blocks=box.querySelectorAll('details').length;"
            "open=box.querySelectorAll('details[open]').length;"
            "}catch(e){err=String(e);}"
            "document.title=JSON.stringify({err:err,open:open,blocks:blocks});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertGreater(verdict["blocks"], 2,
                           "nothing was drawn, so nothing was tested")
        self.assertEqual(verdict["open"], 0,
                         f"{verdict['open']} of {verdict['blocks']} blocks "
                         f"opened themselves")

    def test_except_the_picture_which_is_the_one_thing_asked_for(self) -> None:
        """ONE exception, written down as one.

        The rule above is that nothing opens itself, and it is the right
        rule: the blocks all had good local reasons and the sum of them
        was a page that unrolled everything it had. The picture of what
        is running now is the exception, because it is the thing a reader
        opens this panel to see - a drawing behind a closed summary reads
        exactly like no drawing at all, which is a complaint this panel
        has already produced.

        It is asserted as AT MOST ONE so that the next block with a good
        local reason still fails here.
        """
        report = _a_round("Rebuild the package.", True)
        report["plan"] = {"nodes": [
            {"id": "task_1", "title": "One.", "size": 9,
             "began": "2026-09-01T09:00:00+00:00",
             "ended": "2026-09-04T09:00:00+00:00"},
            {"id": "task_2", "title": "Two.", "size": 5,
             "began": "2026-09-03T09:00:00+00:00",
             "ended": "2026-09-07T09:00:00+00:00"}], "edges": []}
        verdict = self._in_the_page(
            "let err=window.__err||'';let open=-1;let which='';try{"
            "stateBeat.report=" + json.dumps(report) + ";renderStateBeat();"
            "const box=document.getElementById('dbg-statebeat-body');"
            "const up=[...box.querySelectorAll('details[open]')];"
            "open=up.length;"
            "which=up.map(one=>one.dataset.sbOpen||one.className).join(',');"
            "}catch(e){err=String(e);}"
            "document.title=JSON.stringify({err:err,open:open,which:which});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertLessEqual(verdict["open"], 1,
                             f"more than the picture opened: {verdict['which']}")
        if verdict["open"]:
            self.assertEqual(verdict["which"], "whole-tree",
                             f"the wrong block opened itself: "
                             f"{verdict['which']}")

    def test_and_every_block_remembers_being_opened(self) -> None:
        """A block with no key closes again on the next redraw, which
        while a round is running is about twice a second."""
        report = _a_round("Rebuild the package.", True)
        verdict = self._in_the_page(
            "let err=window.__err||'';let nameless=[];try{"
            "stateBeat.report=" + json.dumps(report) + ";renderStateBeat();"
            "const box=document.getElementById('dbg-statebeat-body');"
            "nameless=[...box.querySelectorAll('details')]"
            ".filter((one)=>!one.dataset.sbOpen)"
            ".map((one)=>one.className||'(no class)');"
            "}catch(e){err=String(e);}"
            "document.title=JSON.stringify({err:err,nameless:nameless});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertEqual(verdict["nameless"], [],
                         "these blocks forget being opened")

    def test_a_redraw_that_changes_nothing_does_not_touch_the_page(self) -> None:
        """A round is followed by asking every four hundred milliseconds,
        and the prompt is shown BEFORE the answer arrives - so the reader
        is looking at it during exactly the seconds the panel is redrawing
        itself. Replacing the HTML destroys the selection, and text could
        not be copied out of this page at all while a round ran."""
        report = json.dumps(_a_round("Rebuild the package.", True))
        verdict = self._in_the_page(
            "let err=window.__err||'';let same=false;try{"
            "stateBeat.report=" + report + ";renderStateBeat();"
            "const box=document.getElementById('dbg-statebeat-body');"
            # Marked with a property rather than an element: adding a
            # node would change the HTML this is about, so the probe
            # would destroy the thing it is measuring.
            "box.firstElementChild.__mine=true;"
            # The same report again, which is what most of those polls
            # carry: a summary takes ten or fifteen seconds and the step
            # list does not move between them.
            "renderStateBeat();"
            "same=Boolean(box.firstElementChild"
            "&&box.firstElementChild.__mine);"
            "}catch(e){err=String(e);}"
            "document.title=JSON.stringify({err:err,same:same});")
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertTrue(verdict["same"],
                        "an identical redraw rebuilt the panel anyway")

    def test_and_a_redraw_waits_while_the_reader_holds_the_text(self) -> None:
        """They are reading something and the page is not entitled to take
        it away."""
        first = json.dumps(_a_round("Rebuild the package.", True))
        second = json.dumps(_a_round("Check the figures.", True))
        verdict = self._in_the_page(
            "let err=window.__err||'';let held='';let after='';try{"
            "stateBeat.report=" + first + ";renderStateBeat();"
            "const box=document.getElementById('dbg-statebeat-body');"
            # Hold some of it, the way a drag does.
            "const range=document.createRange();range.selectNodeContents(box);"
            "const sel=window.getSelection();sel.removeAllRanges();"
            "sel.addRange(range);"
            "stateBeat.report=" + second + ";renderStateBeat();"
            "held=box.textContent.indexOf('Rebuild the package.')>=0"
            "?'kept':'taken away';"
            # And let go: the held draw goes in by itself.
            "sel.removeAllRanges();"
            "setTimeout(()=>{"
            "after=document.getElementById('dbg-statebeat-body')"
            ".textContent.indexOf('Check the figures.')>=0?'landed':'lost';"
            "document.title=JSON.stringify({err:err,held:held,after:after});"
            "},50);"
            "}catch(e){err=String(e);"
            "document.title=JSON.stringify({err:err,held:held,after:after});}")
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertEqual(verdict["held"], "kept",
                         "the panel overwrote text the reader was holding")
        self.assertEqual(verdict["after"], "landed",
                         "the held draw never went in after letting go")

    def test_the_last_layer_is_drawn_as_a_tech_tree(self) -> None:
        """Time across, a circle per piece, an arrow for a dependency, and
        a filled circle for one that is finished."""
        verdict = self._render(_a_round("Finish the package.", True))
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertGreaterEqual(verdict["treeNodes"], 2, verdict)
        self.assertGreaterEqual(verdict["treeEdges"], 1, verdict)
        self.assertGreaterEqual(verdict["treeDone"], 1,
                                "a finished piece is not marked finished")

    def test_one_round_renders_without_throwing(self) -> None:
        verdict = self._render(_a_round("They are rebuilding the package.", True))
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertGreater(verdict["chars"], 400, verdict)
        self.assertGreaterEqual(verdict["overviews"], 1, verdict)
        self.assertGreater(verdict["bars"], 0, "the overview drew no bars")

    def test_the_progress_bar_counts_the_experience_too(self) -> None:
        """The stages of each piece of work and the patterns named are part
        of a round, and the bar says how far each has got."""
        picture = {"plan": {"nodes": [{"id": "task_1", "title": "A",
                                       "summary": {"title": "A"}}], "edges": []},
                   "layers": [], "running": True, "at_work": True,
                   "round": {"started_at": "2026-10-04T10:00:00+00:00",
                             "done": False},
                   "work": {"summarise": 2, "summarised": 2, "status": 2,
                            "status_done": 2,
                            "experience": {"phase": "stages", "units": 4,
                                           "units_done": 1, "unit": "The release",
                                           "work": {"patterns": 10, "named": 0},
                                           "personal": {"patterns": 4,
                                                        "named": 4}}}}
        verdict = self._in_the_page(
            "let err=window.__err||'';let said='';let share='';try{"
            "const box=document.createElement('div');"
            "box.innerHTML=sbProgressStrip(pic);"
            "said=box.textContent.replace(/\s+/g,' ');"
            "const bare=document.createElement('div');"
            "bare.innerHTML=sbProgressStrip(Object.assign({},pic,{work:{}}));"
            "window.__bare=bare.querySelectorAll('.sb-progress-row').length;"
            "share=[...box.querySelectorAll('.sb-progress-row')].map(r=>"
            "  r.querySelector('.sb-progress-label').textContent+'='+"
            "  r.querySelector('[style*=width]').getAttribute('style')).join(' | ');"
            "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err:err,said:said,share:share,"
            "bare:window.__bare});",
            {"pic": picture})
        self.assertEqual(verdict["err"], "", verdict["err"])
        # The stages are not the heartbeat's: written in the Experience
        # panel, with their own bar there.
        self.assertNotIn("turned into stages", verdict["said"])
        self.assertIn("0 of 10 work patterns named", verdict["said"])
        # Nothing writes personal natures now: no bar, no count for them.
        self.assertNotIn("personal patterns named", verdict["said"])
        # TWO BARS, one per part of the round: the tasks all done, and the
        # work natures - none of their patterns named. No stages bar.
        self.assertIn("Tasks and projects=width:100%", verdict["share"])
        self.assertNotIn("Task stages", verdict["share"])
        self.assertIn("Work nature=width:0%", verdict["share"])
        self.assertNotIn("Personal nature", verdict["share"])
        # And both from the start, before anything has been written.
        self.assertEqual(verdict["bare"], 2)

    def test_the_steps_after_a_round_have_bars_of_their_own(self) -> None:
        """What the user needs to know, and each kind of lesson: a bar each,
        how far while it runs, and where it stands between rounds."""
        picture = {"plan": {"nodes": [{"id": "task_1", "title": "A",
                                       "summary": {"title": "A"}}], "edges": []},
                   "layers": [], "running": False, "at_work": False,
                   "round": {"started_at": "2026-10-04T10:00:00+00:00", "done": True},
                   "work": {"connections": {"running": True, "stage": "searching the web",
                                            "done": 2, "total": 4},
                            "lessons": {
                                "notifications": {"running": True, "share": 40,
                                                  "step": "matching them to the lessons",
                                                  "done": 10, "of": 30, "active": 3,
                                                  "forming": 2},
                                "connections": {"running": False, "due": True, "share": 0,
                                                "waiting": 5, "active": 1, "forming": 0},
                                "work": {"running": False, "due": False, "share": 100,
                                         "active": 12, "forming": 4}}}}
        verdict = self._in_the_page(
            "let err=window.__err||'';let said='';let share='';try{"
            "const box=document.createElement('div');"
            "box.innerHTML=sbProgressStrip(pic);"
            "said=box.textContent.replace(/\s+/g,' ');"
            "share=[...box.querySelectorAll('.sb-progress-row')].map(r=>"
            "  r.querySelector('.sb-progress-label').textContent+'='+"
            "  r.querySelector('[style*=width]').getAttribute('style')).join(' | ');"
            "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err:err,said:said,share:share});",
            {"pic": picture})
        self.assertEqual(verdict["err"], "", verdict["err"])
        # No bar for Customized Recommendation: it runs on its own clock,
        # not in the state round.
        self.assertNotIn("Customized Recommendation", verdict["share"])
        self.assertNotIn("searching the web", verdict["said"])
        self.assertIn("Notification lessons=width:40%", verdict["share"])
        self.assertIn("matching them to the lessons (10 of 30)", verdict["said"])
        self.assertIn("Connection lessons=width:0%", verdict["share"])
        self.assertIn("5 new to learn from · 1 in force", verdict["said"])
        self.assertIn("Work nature lessons=width:100%", verdict["share"])
        self.assertIn("up to date · 12 in force, 4 forming", verdict["said"])


    def test_the_overview_shows_how_you_work_and_when_each_was_last_seen(self) -> None:
        """Principles drawn as the tasks are: a row each, a segment per
        stretch it was active, most recent first - and only principles."""
        from datetime import datetime, timedelta, timezone
        def ago(days):
            return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        tree = {"kinds": [{"kind": "work", "principles": [
            {"id": "pr_old", "statement": "Writes long letters",
             "last_active": ago(90), "stretches": [[ago(120), ago(90)]],
             "patterns": []},
            {"id": "pr_new", "statement": "Judges a change by using it",
             "last_active": ago(2),
             "stretches": [[ago(40), ago(35)], [ago(5), ago(2)]],
             "patterns": [{"id": "p1", "statement": "Tries it in dark mode",
                           "last_active": ago(2), "support": 3}]}],
            "unplaced": []}]}
        verdict = self._in_the_page(
            "let err=window.__err||'';let said='';let order=[];let spans=0;let quiet=0;try{"
            "let box=document.getElementById('ov-principles');"
            "if(!box){box=document.createElement('div');box.id='ov-principles';"
            "document.body.appendChild(box);}ovState.recentDays=0;renderPrinciples(tree);"
            "said=box.textContent.replace(/\s+/g,' ');"
            "order=[...box.querySelectorAll('.ov-pr-name')].map((s)=>s.textContent);"
            "spans=box.querySelectorAll('[data-ov-principle=\\\"pr_new\\\"] .sb-tt-span:not(.sb-tt-span-gap)').length;"
            "quiet=box.querySelectorAll('.ov-pr-row.sb-tt-quiet').length;"
            "box.querySelector('[data-ov-principle=\\\"pr_new\\\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "window.__card=document.getElementById('ov-principle-card').textContent.replace(/\s+/g,' ');"
            "window.__fitted=box.querySelector('svg').classList.contains('sb-graph');"
            "window.__wide=parseFloat(box.querySelector('svg').style.width);"
            "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err:err,said:said,order:order,spans:spans,quiet:quiet,"
            "card:window.__card,fitted:window.__fitted,wide:window.__wide});",
            {"tree": tree})
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertEqual(verdict["order"], ["Judges a change by using it",
                                            "Writes long letters"])
        # Two stretches, the gap between them left empty.
        self.assertEqual(verdict["spans"], 2)
        self.assertIn("(2 days ago)", verdict["said"])
        self.assertEqual(verdict["quiet"], 1)
        self.assertIn("you may have changed", verdict["said"])
        # Principles only - drawn with the tasks' own picture.
        self.assertNotIn("Tries it in dark mode", verdict["said"])
        self.assertIn("A circle is a principle", verdict["said"])
        # At its own size, never fitted to the panel; a principle clicked
        # opens below: its stretches and its patterns.
        self.assertFalse(verdict["fitted"])
        self.assertGreaterEqual(verdict["wide"], 900)
        self.assertIn("Judges a change by using it", verdict["card"])
        self.assertIn("When it was seen:", verdict["card"])
        self.assertIn("Its patterns (1):", verdict["card"])
        self.assertIn("Tries it in dark mode", verdict["card"])

    def test_principles_can_be_shown_for_the_last_so_many_days(self) -> None:
        from datetime import datetime, timedelta, timezone
        def ago(days):
            return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        tree = {"kinds": [{"kind": "work", "principles": [
            {"id": "pr_old", "statement": "Writes long letters", "last_active": ago(90),
             "stretches": [[ago(120), ago(90)]], "patterns": []},
            {"id": "pr_new", "statement": "Judges a change by using it",
             "last_active": ago(2), "stretches": [[ago(40), ago(2)]], "patterns": []}],
            "unplaced": []}]}
        verdict = self._in_the_page(
            "let err=window.__err||'';let out={};try{"
            "let box=document.getElementById('ov-principles');"
            "if(!box){box=document.createElement('div');box.id='ov-principles';"
            "document.body.appendChild(box);}"
            "const names=()=>[...box.querySelectorAll('.ov-pr-name')].map((s)=>s.textContent);"
            "renderPrinciples(tree);out.start=ovRecentDays();out.first=names();"
            "box.querySelector('[data-ov-recent=\\\"0\\\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));out.all=names();"
            "box.querySelector('[data-ov-recent=\\\"1\\\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "out.day=box.textContent.replace(/\s+/g,' ');"
            "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err:err,out:out});", {"tree": tree})
        self.assertEqual(verdict["err"], "", verdict["err"])
        out = verdict["out"]
        # Thirty days to start: the principle not seen for three months is not drawn.
        self.assertEqual(out["start"], 30)
        self.assertEqual(out["first"], ["Judges a change by using it"])
        self.assertEqual(out["all"], ["Judges a change by using it", "Writes long letters"])
        self.assertIn("No principle was active in the last day", out["day"])

    def test_stages_are_placed_by_dependency_not_by_time(self) -> None:
        # Three attempts at one card a chain, the last a month after the
        # second; a fourth, at another card, depends on the first. Evenly
        # spaced columns.
        graph = {"unit": {"id": "u", "name": "Work"}, "tasks": [],
                 "cards": [{"id": "a", "title": "A", "status": "working",
                            "depth": 0, "level": "root"},
                           {"id": "b", "title": "B", "status": "working",
                            "depth": 1, "level": "deliverable", "parent_id": "a"}],
                 "stages": [
                     {"id": "s1", "card_id": "a", "kind": "plan", "summary": "",
                      "began": "2026-09-01T10:00:00"},
                     {"id": "s2", "card_id": "a", "kind": "execution", "summary": "",
                      "began": "2026-09-01T10:05:00"},
                     {"id": "s3", "card_id": "a", "kind": "evaluation", "summary": "",
                      "began": "2026-10-01T10:00:00"},
                     {"id": "s4", "card_id": "b", "kind": "execution", "summary": "",
                      "began": "2026-09-01T10:06:00"}],
                 "edges": [{"source": "s4", "target": "s1", "kind": "depends_on"}]}
        verdict = self._in_the_page(
            "let err=window.__err||'';let xs={};try{"
            "document.getElementById('xp-graph').innerHTML=xpGraphSvg(graph);"
            "document.querySelectorAll('#xp-graph [data-xp-stage]').forEach((g)=>{"
            "xs[g.dataset.xpStage]=Number(g.querySelector('circle').getAttribute('cx'));});"
            "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err:err,xs:xs});",
            {"graph": graph})
        self.assertEqual(verdict["err"], "", verdict["err"])
        xs = verdict["xs"]
        self.assertGreater(xs["s2"], xs["s1"])
        self.assertEqual(xs["s2"] - xs["s1"], xs["s3"] - xs["s2"])
        self.assertEqual(xs["s4"], xs["s2"])

    def test_a_stage_card_says_what_it_shows_about_the_user(self) -> None:
        graph = {"unit": {"id": "u", "name": "Work"}, "tasks": [], "threads": [],
                 "edges": [],
                 "stages": [{"id": "s1", "thread_id": "", "kind": "evaluation",
                             "summary": "checked the slides [r1]", "began": "2026-09-01",
                             "deliverable": {"type": "slides", "excerpt": "the deck"},
                             "work": {"references": [{"id": "r1", "kind": "file",
                                                      "location": "C:/talks/deck.pptx",
                                                      "what": "the deck"}],
                                      "input": [{"what": "the talk's audience",
                                                 "from": "user",
                                                 "location": "C:/talks/brief.md",
                                                 "copies": {"text": "C:/kept/brief.txt"}}],
                                      "decision": {"about": "how many ideas a slide",
                                                   "chose": "one", "by": "agent"},
                                      "output": {"what": "a twelve-slide deck",
                                                 "specification": [
                                                     {"requirement": "one idea a slide",
                                                      "source": "the user said: 'one idea'"}],
                                                 "design": "the deck feeds the talk",
                                                 "references_note": "",
                                                 "rationale": {"why": "so it reads from the back",
                                                               "from": "inferred"}}},
                             "natures": [{"id": "rec_1", "kind": "work",
                                          "title": "Judges a slide by one idea on it",
                                          "pattern": "Slides carry one idea"}]}]}
        verdict = self._in_the_page(
            "let err=window.__err||'';let said='';try{"
            "experienceState.graph=graph;xpShowStage('s1','unit');"
            "const cite=document.querySelector('#xp-stage [data-xp-cite=r1]');"
            "window.citeFound=Boolean(cite);"
            "if(cite)cite.click();"
            "window.flashed=Boolean(document.querySelector('#xp-stage [data-xp-ref-id=r1].xp-ref-flash'));"
            + "said=document.getElementById('xp-stage').textContent"
            ".split(String.fromCharCode(10)).map((x)=>x.trim()).filter(Boolean).join(' ');"
            "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err:err,said:said,"
            "cite:window.citeFound,flashed:window.flashed});",
            {"graph": graph})
        self.assertEqual(verdict["err"], "", verdict["err"])
        # [r1] in the summary is a link to r1, and following it marks it.
        self.assertTrue(verdict["cite"], "the summary's [r1] is not a link")
        self.assertTrue(verdict["flashed"], "following [r1] did not reach r1")
        for shown in ("What it shows about you (1)", "r1", "C:/talks/deck.pptx", "Judges a slide by one idea on it",
                      "In the pattern: Slides carry one idea",
                      "Input", "the talk's audience", "C:/talks/brief.md",
                      "C:/kept/brief.txt", "Decision made by the agent",
                      "Output: a twelve-slide deck", "Specification",
                      "the user said: 'one idea'", "Design:", "the deck feeds the talk",
                      "Rationale inferred", "so it reads from the back",
                      "Example (slides): the deck"):
            self.assertIn(shown, verdict["said"])


    def test_where_the_work_is_stays_open_through_a_redraw(self) -> None:
        """The panel redraws every second or two; a section the reader
        opened in a task's card has to still be open after it."""
        task = {"places": [{"place": "c:/work/repo", "coverage": 0.8,
                            "specific": 1, "weight": 0.8}], "weight": 12}
        verdict = self._in_the_page(
            "let err=window.__err||'';let before=null,after=null;(async()=>{try{"
            "let host=document.getElementById('dbg-statebeat-body');"
            "if(!host){host=document.createElement('div');host.id='dbg-statebeat-body';"
            "document.body.appendChild(host);}"
            "host.innerHTML=sbPlacesBlock(task,{});"
            "host.querySelector('details').open=true;"
            "before=host.querySelector('details').open;"
            "await new Promise((r)=>setTimeout(r,50));"
            "stateBeatRemember();"
            "host.innerHTML=sbPlacesBlock(task,{});"
            "stateBeatRestore();"
            "after=host.querySelector('details').open;"
            "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err:err,before:before,after:after});})();",
            {"task": task})
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertTrue(verdict["before"])
        self.assertTrue(verdict["after"], "the redraw shut it")

    def test_where_the_work_is_stays_open_on_the_summary_tab(self) -> None:
        """The Summary tab draws the same card and redraws as often; a
        section opened there stays open - even with the debugger's hidden,
        shut copy of the card still in the page."""
        task = {"places": [{"place": "c:/work/repo", "coverage": 0.8,
                            "specific": 1, "weight": 0.8}], "weight": 12}
        verdict = self._in_the_page(
            "let err=window.__err||'';let after=null;(async()=>{try{"
            "const make=(id)=>{let h=document.getElementById(id);"
            "if(!h){h=document.createElement('div');h.id=id;document.body.appendChild(h);}"
            "return h;};"
            "const dbg=make('dbg-statebeat-body');dbg.style.display='none';"
            "dbg.innerHTML=sbPlacesBlock(task,{});"
            "const ov=make('ov-tasks');ov.style.display='block';"
            "ov.innerHTML=sbPlacesBlock(task,{});"
            "ov.querySelector('details').open=true;"
            "await new Promise((r)=>setTimeout(r,50));"
            "stateBeatRemember();"
            "ov.innerHTML=sbPlacesBlock(task,{});"
            "stateBeatRestore();"
            "after=ov.querySelector('details').open;"
            "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err:err,after:after});})();",
            {"task": task})
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertTrue(verdict["after"], "the Summary tab's redraw shut it")

    def test_a_rounds_cost_is_its_wall_clock_and_each_part(self) -> None:
        """The parts run at once: the round took as long as the last of them,
        and each part's own time and tokens are under it."""
        # A round's parts: its tasks, and each kind of nature's patterns -
        # the stages are written in the Experience panel, not by a round.
        spent = {"visits": 1, "seconds": 190, "calls": 8, "tokens_in": 1410,
                 "tokens_out": 141, "completed_at": "2026-10-04T10:00:00+00:00",
                 "parts": {
                     "tasks": {"seconds": 80, "calls": 3, "tokens_in": 1000,
                               "tokens_out": 100},
                     "work": {"seconds": 150, "calls": 4, "tokens_in": 400,
                              "tokens_out": 40},
                     "personal": {"seconds": 30, "calls": 1, "tokens_in": 10,
                                  "tokens_out": 1}}}
        verdict = self._in_the_page(
            "let err=window.__err||'';let said='';let rows=[];try{"
            "const box=document.createElement('div');"
            "box.innerHTML=sbCostLine({done:true,last_spent:spent});"
            "said=box.textContent.replace(/\s+/g,' ');"
            "rows=[...box.querySelectorAll('.sb-parts li')].map(r=>"
            "  r.textContent.replace(/\s+/g,' ').trim());"
            "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err:err,said:said,rows:rows});",
            {"spent": spent})
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertIn("3m 10s until every part was done", verdict["said"])
        self.assertIn("1,410 input tokens", verdict["said"])
        self.assertEqual(len(verdict["rows"]), 3)
        self.assertTrue(verdict["rows"][0].startswith("Tasks and projects 1m 20s"))
        self.assertIn("Work nature 2m 30s 400 in · 40 out", verdict["rows"][1])

    def test_and_a_summarised_task_shows_its_title(self) -> None:
        """Written, stored, sent to the page, and read from a field nothing
        had ever put it in - so no task had a title for a dozen runs."""
        verdict = self._render(_a_round("They are rebuilding the package.", True))
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertIn("They are rebuilding the package.", verdict["titles"])

    def test_and_a_way_of_working_is_marked_as_not_shown(self) -> None:
        verdict = self._render(
            _a_round("They kept returning to it in short sittings.", False))
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertGreaterEqual(verdict["notShown"], 1, verdict)

    def test_and_the_prompt_is_on_the_page_whole(self) -> None:
        """Both halves: the instruction and the message. A prompt that is
        described rather than shown cannot be read for what was odd."""
        verdict = self._render(_a_round("They are rebuilding the package.", True))
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertGreaterEqual(verdict["prompts"], 2, verdict)

    def test_a_whole_rehearsal_renders_one_round_at_a_time(self) -> None:
        one = _a_round("They are rebuilding the package.", True)
        two = _a_round("They kept returning to it in short sittings.", False)
        verdict = self._render(two, {
            "cap": 60, "why": "three rounds over a window of sixty",
            "trace": [{"step": "emptied the sandbox", "detail": {}, "why": ""},
                      {"step": "round one: the first grouping",
                       "detail": {"states_read": 3}, "why": "the cap"},
                      {"step": "round two: appending", "detail": {"arrivals": 3},
                       "why": "a few dot products"}],
            "identity": {"tasks_after_round_one": 1, "tasks_at_the_end": 1,
                         "kept_their_id_throughout": 1, "grew": {},
                         "new": [], "gone": []},
            "signals": [], "round_one": one, "round_two": two})
        self.assertEqual(verdict["err"], "", verdict["err"])
        self.assertEqual(verdict["rounds"], 2, verdict)
        self.assertEqual(verdict["compare"], 1, verdict)
        # One overview inside each round, and one per round in the comparison.
        self.assertGreaterEqual(verdict["overviews"], 4, verdict)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
