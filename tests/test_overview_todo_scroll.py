"""To do on the Overview, scrolled, stays where it was scrolled to.

The task picture is redrawn whenever a round moves - every fifteen seconds
while work runs - and each redraw wrote the To do box anew, its list back at
the top: the to-dos below the first few could not be scrolled to. The box is
now written only when what it shows changed, and keeps its scroll when it is.
"""

from __future__ import annotations

import json
import re
import unittest

from support import ROOT
from test_chat_debugger_ui import ESCAPE, _browser, _run_browser_probe

WEB = ROOT / "personalized_agent" / "api" / "web"


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    return source[start:source.index("\n}\n", start) + 3]


class OverviewTodoScrollTests(unittest.TestCase):

    def test_at_its_ends_the_wheel_goes_on_to_the_page(self) -> None:
        # On a short screen the sticky header covers the list's top once the
        # page is scrolled to it; a list keeping the wheel to itself left its
        # first to-do under the header, out of reach.
        css = (WEB / "styles.css").read_text(encoding="utf-8")
        rule = re.search(r"\.ov-todo-list \{([^}]*)\}", css).group(1)
        self.assertIn("overflow-y: auto", rule)
        self.assertNotIn("overscroll-behavior", rule)

    def test_a_redraw_keeps_the_list_where_it_was_scrolled(self) -> None:
        engine = _browser()
        if not engine:
            self.skipTest("a browser is needed to draw the list")
        source = (WEB / "app.js").read_text(encoding="utf-8")
        todos = [{"id": f"t{n}", "text": f"Fictional errand {n}: repot the fern",
                  "detail": "Check the soil, the pot size and the window it stands by "
                            "before the weekend, with what was noted last time. " * 2}
                 for n in range(1, 7)]
        script = (
            "window.$=s=>document.querySelector(s);" + ESCAPE
            + "const delegateState={removing:'',tracking:new Set()};"
            + "const delegateLabel=()=>'Delegate work';"
            + "let ovTodos=" + json.dumps(todos) + ";let ovTodoDrawn='';"
            + _function(source, "ovTodoVersions") + _function(source, "ovTodoTracked")
            + _function(source, "ovTodoWork") + _function(source, "ovTodoBox")
            + _function(source, "ovPlaceTodos")
            + "window.addEventListener('load',()=>{const out={};"
            "ovPlaceTodos();const list=()=>document.querySelector('.ov-todo-list');"
            "out.scrolls=list().scrollHeight>list().clientHeight;"
            "list().scrollTop=list().scrollHeight;out.bottom=list().scrollTop;"
            "const first=list();ovPlaceTodos();ovPlaceTodos();"
            "out.same=list()===first;out.unchanged=list().scrollTop;"
            "ovTodos=ovTodos.slice(1);ovPlaceTodos();"
            "out.rewritten=list()!==first;out.changed=list().scrollTop;"
            "out.items=list().children.length;"
            "const pre=document.createElement('pre');pre.id='out';"
            "pre.textContent=JSON.stringify(out);document.body.prepend(pre);});")
        page = ("<!doctype html><html><head><meta charset='utf-8'><style>"
                + (WEB / "styles.css").read_text(encoding="utf-8")
                + "</style></head><body><div class='card' id='ov-tasks-card'>"
                "<div class='ov-tasks-top'><div class='ov-tasks-intro'></div>"
                "<aside id='ov-todo' class='ov-todo'></aside></div></div>"
                "<script>" + script + "</script></body></html>")
        stdout, stderr = _run_browser_probe(engine, page)
        found = re.search(r'<pre id="out">(.*?)</pre>', stdout, re.S)
        self.assertIsNotNone(found, stderr[-2000:])
        out = json.loads(found.group(1).replace("&quot;", '"'))
        self.assertTrue(out["scrolls"], out)
        self.assertGreater(out["bottom"], 0, out)
        # The same to-dos: the list is left as it is, scrolled to its end.
        self.assertTrue(out["same"], out)
        self.assertEqual(out["unchanged"], out["bottom"], out)
        # One fewer: written anew, and scrolled as far as it still goes.
        self.assertTrue(out["rewritten"], out)
        self.assertEqual(out["items"], 5, out)
        self.assertGreater(out["changed"], 0, out)


class ATodosWorkIsTrackedTests(unittest.TestCase):

    def test_every_version_with_where_it_stands_now(self) -> None:
        engine = _browser()
        if not engine:
            self.skipTest("a browser is needed to draw the list")
        source = (WEB / "app.js").read_text(encoding="utf-8")
        todos = [{"id": "t1", "text": "Fictional errand: reply to the club", "work": {
            "versions": [
                {"version": 1, "state": "finished", "run": "r1", "unit": "u1", "draft": "d1",
                 "handed": "Reply to the club", "ended": "2026-10-26T10:00:00",
                 "items": [{"n": 0, "kind": "email", "title": "Reply to the club"},
                           {"n": 1, "kind": "file", "title": "Notes", "name": "notes.docx"}]},
                {"version": 2, "state": "ongoing", "run": "r2", "unit": "u2"},
                {"version": 3, "state": "drafting", "draft": "d3"}]}},
                 {"id": "t3", "text": "Fictional errand: a page", "work": {"versions": [
                {"version": 1, "state": "stopped", "run": "r5", "unit": "u5", "draft": "d5",
                 "status": "not finished", "continuable": True, "goes_back": False},
                {"version": 2, "state": "stopped", "run": "r6", "unit": "u6", "draft": "d6",
                 "status": "failed", "continuable": True, "goes_back": True,
                 "back_to": "after round 1: the page"}]}},
                 {"id": "t2", "text": "Fictional errand: buy tea"}]
        going = {"u2": {"run": "r2", "state": "going", "activity": "writing the reply"}}
        script = (
            "window.$=s=>document.querySelector(s);" + ESCAPE
            + "const localStamp=s=>'10:00';const workGoing={byUnit:" + json.dumps(going) + "};"
            + "const delegateState={removing:'',tracking:new Set()};"
            + "const delegateLabel=()=>'Delegate work';"
            + "let ovTodos=" + json.dumps(todos) + ";let ovTodoDrawn='';"
            + "const OV_TODO_STATE={going:'going',paused:'paused',waiting_for_you:'waiting for you'};"
            + "const XP_GOES_BACK={interrupted:'the application closed while it was going',"
              "'stopped at once':'you stopped it at once, before it wrapped up',"
              "failed:'it failed before it could stop'};"
            + "const XP_HANDED_KIND={email:'Email',event:'Event',text:'Text to paste',"
              "file:'File',folder:'Folder',answer:'Answer'};"
            + "".join(_function(source, name) for name in (
                "ovTodoVersions", "ovTodoControls", "ovTodoWhere", "ovTodoWork", "ovTodoMade",
                "ovTodoTracked", "ovTodoBox",
                "ovPlaceTodos"))
            + "window.addEventListener('load',()=>{const out={};ovPlaceTodos();"
            "const item=document.querySelector('[data-todo=\"t1\"]');"
            "out.delegate=item.querySelector('.ov-todo-delegate').textContent.replace(/\\s+/g,' ').trim();"
            "out.brief=item.querySelector('.ov-todo-track').textContent.replace(/\\s+/g,' ').trim();"
            "out.closed=item.querySelectorAll('.ov-todo-version').length;"
            "delegateState.tracking.add('t1');ovPlaceTodos();"
            "const rows=()=>Array.from(document.querySelectorAll('[data-todo=\"t1\"] .ov-todo-version'))"
            ".map(r=>Array.from(r.children).map(c=>c.textContent.replace(/\\s+/g,' ').trim()).join(' | '));"
            "out.lined=new Set(Array.from(document.querySelectorAll('.ov-todo-open'))"
            ".map(c=>Math.round(c.getBoundingClientRect().left))).size;"
            "out.rows=rows();"
            "workGoing.byUnit.u2={run:'r2',state:'waiting_for_you',activity:''};ovPlaceTodos();"
            "out.live=rows()[1];"
            "workGoing.byUnit.u2={run:'r2',state:'paused',activity:'',pause_asked:true};ovPlaceTodos();"
            "out.paused=rows()[1];"
            "workGoing.byUnit.u2={run:'r2',state:'going',activity:'',stopping:true};ovPlaceTodos();"
            "out.stopping=rows()[1];"
            "out.wired=document.querySelector('[data-todo-run-stop-now]').dataset.todoRunStopNow;"
            "delegateState.tracking.add('t3');delegateState.removingVersion='';ovPlaceTodos();"
            "out.ended=Array.from(document.querySelectorAll('[data-todo=\"t3\"] .ov-todo-version'))"
            ".map(r=>Array.from(r.children).map(c=>c.textContent.replace(/\s+/g,' ').trim()).join(' | '));"
            "out.back=document.querySelector('[data-todo-run-continue=\"r6\"]').title;"
            "delegateState.removingVersion='d1';ovPlaceTodos();"
            "out.asking=document.querySelector('.ov-todo-confirm').textContent.replace(/\\s+/g,' ').trim();"
            "out.plain=document.querySelector('[data-todo=\"t2\"]').textContent.includes('V');"
            "const pre=document.createElement('pre');pre.id='out';"
            "pre.textContent=JSON.stringify(out);document.body.prepend(pre);});")
        page = ("<!doctype html><html><head><meta charset='utf-8'><style>"
                + (WEB / "styles.css").read_text(encoding="utf-8")
                + "</style></head><body><div class='card' id='ov-tasks-card'>"
                "<aside id='ov-todo' class='ov-todo'></aside></div>"
                "<script>" + script + "</script></body></html>")
        stdout, stderr = _run_browser_probe(engine, page)
        found = re.search(r'<pre id="out">(.*?)</pre>', stdout, re.S)
        self.assertIsNotNone(found, stderr[-2000:])
        out = json.loads(found.group(1).replace("&quot;", '"'))
        # Handed over again and again: Delegate work stays, the next version named.
        self.assertEqual(out["delegate"], "Delegate work V4")
        # The button says it is the work's status; each version is in it.
        self.assertEqual(out["brief"], "▸ Work status (3)")
        self.assertEqual(out["closed"], 0)
        # Opened out: the newest first, each where it stands and what opens it.
        # What each made is said before it is opened; the buttons in one column.
        # Remove on each - not on one going.
        self.assertEqual(out["rows"], [
            "V3 | its plan, for you to check | Open the plan | Remove",
            "V2 | going - writing the reply | PauseStopFollow it in Work | ",
            "V1 | finished 10:00 - Email · Reply to the club | Open the email | Remove",
            " | File · notes.docx | Open the file | "])
        self.assertEqual(out["lined"], 1)
        self.assertEqual(out["live"], "V2 | waiting for you | PauseStopFollow it in Work | ")
        # RUN FROM ITS ROW, as in Work (the user, 2026-10-26): Resume when it
        # is paused; while it wraps up, only Stop now.
        self.assertEqual(out["paused"], "V2 | paused | ResumeStopFollow it in Work | ")
        self.assertEqual(out["stopping"], "V2 | going | Stop nowFollow it in Work | ")
        self.assertEqual(out["wired"], "r2", "the run it stops")
        # ENDED, AND ABLE TO GO ON - from the to-do too: Resume, back to its
        # last save point, for one that did not stop cleanly; Continue for
        # one that did.
        self.assertEqual(out["ended"], [
            "V2 | did not stop cleanly - it failed before it could stop | ResumeSee it in Work | Remove",
            "V1 | stopped: not finished | ContinueSee it in Work | Remove"])
        self.assertIn("after round 1: the page", out["back"])
        # Removing asks first.
        self.assertEqual(out["asking"], "Remove V1? Yes No")
        self.assertFalse(out["plain"], "a to-do never handed over has no versions")


if __name__ == "__main__":
    unittest.main()
