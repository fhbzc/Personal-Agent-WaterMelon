"""The Experience debugger's panel, in the Debugger tab, as the user uses it:
its projects by name alone, a new one made in a folder with its three
subfolders shown, the three ways side by side - each started on its own,
with its own limits (empty: none) - and each way's runs drawn and continued
as any run is."""

from __future__ import annotations

import html
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"


def _render(engine, stub, steps, budget=30000, width=1500):
    page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                  (WEB / "index.html").read_text(encoding="utf-8"))
    harness = (
        "<style>" + (WEB / "styles.css").read_text(encoding="utf-8") + "</style>"
        "<script>window.__posts=[];window.__gets=[];const STUB=" + json.dumps(stub) + ";"
        "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
        "if(o&&o.method==='POST')window.__posts.push({p,body:JSON.parse(o.body||'{}')});"
        "else window.__gets.push(String(u));"
        "const said=STUB[String(u)]||STUB[p]||{};"
        "return Promise.resolve({ok:true,status:200,statusText:'OK',"
        "headers:{get:()=>null},json:()=>Promise.resolve(said)});};</script>"
        "<script>" + (WEB / "app.js").read_text(encoding="utf-8") + "</script>"
        "<script>window.addEventListener('load',async()=>{let err='';const out={};"
        "const wait=(ms)=>new Promise(r=>setTimeout(r,ms||120));"
        "const click=(el)=>el.dispatchEvent(new MouseEvent('click',{bubbles:true}));"
        "const text=(el)=>el?el.textContent.replace(/\\s+/g,' ').trim():'';"
        "const type=(el,v)=>{el.value=v;el.dispatchEvent(new Event('input',{bubbles:true}));};"
        "activeTab='xdebug';"
        "document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.id==='xdebug'));"
        "document.body.style.minHeight='5000px';"
        "try{" + steps + "}catch(e){err=String(e&&e.stack?e.stack:e);}"
        "document.title=JSON.stringify({err,out});});</script>")
    with tempfile.TemporaryDirectory() as work:
        probe = Path(work) / "probe.html"
        probe.write_text(page + harness, encoding="utf-8")
        completed = subprocess.run(
            [engine, "--headless=new", "--disable-gpu", "--no-first-run",
             f"--user-data-dir={work}/profile", f"--virtual-time-budget={budget}",
             f"--window-size={width},900", "--dump-dom", probe.as_uri()],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    match = re.search(r"<title>(.*?)</title>", completed.stdout or "", re.S)
    assert match, "the page never finished rendering"
    return json.loads(html.unescape(match.group(1)))


def _variants(alive=()):
    return [{"variant": v, "label": label, "unit": f"dbg_1:{v}",
             "folder": f"C:\\bench\\{folder}", "runs": 0, "alive": v in alive}
            for v, label, folder in (("single", "Single model", "A"),
                                     ("noctx", "Builder + reviewer, no context", "B"),
                                     ("full", "Full", "C"))]


PROJECT = {"id": "dbg_1", "name": "Project: Notes", "description": "a short note",
           "instruction": "write the notes", "folder": "C:\\bench",
           "variants": _variants(("full",))}

RUN = {"id": "run_full", "folder": "C:\\bench\\C", "alive": True, "status": "running",
       "continuable": False, "paused": False, "started_at": "2026-10-08T12:00:00",
       "instruction": "write the notes", "final": {}, "log": [], "versions": {"count": 0},
       "plan": {"packages": {"mode": "ask", "requests": []}},
       "calls": [{"n": 1, "started": "2026-10-08T12:00:00+00:00",
                  "finished": "2026-10-08T12:00:30+00:00", "seconds": 30, "who": "builder",
                  "part": "contract", "tokens_in": 40000, "tokens_out": 900},
                 {"n": 2, "started": "2026-10-08T12:00:40+00:00",
                  "finished": "2026-10-08T12:01:00+00:00", "seconds": 20, "who": "reviewer",
                  "part": "contract", "tokens_in": 52000, "tokens_out": 300}]}

DONE = {"id": "run_single", "folder": "C:\\bench\\A", "alive": False,
        "status": "not finished", "continuable": True, "extendable": False, "paused": False,
        "started_at": "2026-10-08T11:00:00", "instruction": "write the notes",
        "final": {"answer": "I wrote notes.md - THE REPORT"}, "log": [],
        "versions": {"count": 1}, "plan": {"packages": {"mode": "ask", "requests": []},
                                           "phase": "alone"}}

LIVE = {"run": "run_full", "events": [], "last": 0, "state": "going", "status": "running",
        "resumes_from": None, "requests": {}, "pending": [], "contract": None,
        "progress": {"stage": "round 1", "phase": "rounds", "round": 1, "rounds_done": 0,
                     "used": {"minutes": 1, "tokens": 10, "tokens_in": 6, "tokens_out": 4,
                              "calls": 1},
                     "limits": {"minutes": 0, "tokens": 0, "calls": 0}, "activity": "working",
                     "pause_asked": False, "held": False, "stopping": False}}


class TheExperienceDebuggerPanelTests(unittest.TestCase):

    def setUp(self) -> None:
        from test_panels import _javascript_engine
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _stub(self):
        runs = "/api/debugger/experience/runs?project=dbg_1&variant="
        return {"/api/debugger/experience/projects": {"projects": [PROJECT]},
                runs + "single": {"runs": [DONE]}, runs + "noctx": {"runs": []},
                runs + "full": {"runs": [RUN]},
                "/api/debugger/experience/start": {"run": "run_new"},
                "/api/debugger/experience/continue": {"run": "run_single"},
                "/api/settings/choose-directory": {"selected": "C:\\Users\\me\\bench_b"},
                "/api/work/live": LIVE, "/api/work/reading": {"roots": []}}

    def test_three_ways_side_by_side_started_and_continued(self) -> None:
        steps = (
            "await loadDebuggerExperience(true);await wait();"
            "out.names=[...document.querySelectorAll('[data-xd-open]')].map(text);"
            "click(document.querySelector('[data-xd-open=\"dbg_1\"]'));await wait(400);"
            "const ways=[...document.querySelectorAll('#dbg-xd [data-xd-variant]')];"
            "out.ways=ways.map(w=>w.dataset.xdVariant);"
            "out.labels=ways.map(w=>text(w.querySelector('.xd-way-head h4')));"
            "out.shown=ways.filter(w=>!w.hidden&&w.offsetParent).map(w=>w.dataset.xdVariant);"
            "out.tabs=[...document.querySelectorAll('[data-xd-tab] .xd-tab-name')].map(text);"
            "out.words=text(document.querySelector('.xd-project-view'));"
            "out.fullRun=!!document.querySelector('[data-xd-variant=\"full\"] .xp-run');"
            "out.fullStop=!!document.querySelector('[data-xd-variant=\"full\"] .xd-way-head [data-xp-stop=\"run_full\"]');"
            "out.singleReport=text(document.querySelector('[data-xd-variant=\"single\"] .xp-answer'));"
            "out.startDisabled=ways.map(w=>{const b=w.querySelector('[data-xd-start]');return b?b.disabled:null;});"
            "out.placeholders=[...ways[1].querySelectorAll('[data-xd-limit]')].map(i=>[i.value,i.placeholder]);"
            # Start the second way, with one limit set.
            "type(ways[1].querySelector('[data-xd-limit=\"calls\"]'),'40');"
            "click(ways[1].querySelector('[data-xd-start]'));await wait(400);"
            "out.starts=window.__posts.filter(x=>x.p==='/api/debugger/experience/start').map(x=>x.body);"
            # Continue the single model's run, with words, and no limit.
            "const box=document.querySelector('[data-xp-continue-instruction=\"run_single\"]');"
            "type(box,'add a title');"
            "click(document.querySelector('[data-xd-variant=\"single\"] [data-xp-continue=\"run_single\"]'));"
            "await wait(400);"
            "out.continues=window.__posts.filter(x=>x.p==='/api/debugger/experience/continue').map(x=>x.body);"
            "out.workContinues=window.__posts.filter(x=>x.p==='/api/work/continue').length;"
            # The live session of the going way is followed here.
            "await wait(1500);"
            "out.liveGets=window.__gets.filter(g=>g.includes('/api/work/live?run=run_full')).length;")
        result = _render(self.engine, self._stub(), steps)
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        # Its name alone - no "Project:" before it.
        self.assertEqual(out["names"], ["Notes"])
        self.assertEqual(out["ways"], ["single", "noctx", "full"])
        # Which letter is which way is said here only: the folders say nothing.
        self.assertEqual(out["labels"], ["Single model · folder A",
                                         "Builder + reviewer, no context · folder B",
                                         "Full · folder C"])
        # One way at a time, chosen by its tab: the one that goes first.
        self.assertEqual(out["shown"], ["full"])
        self.assertEqual(out["tabs"], ["Single model · A", "Builder + reviewer, no context · B",
                                       "Full · C"])
        self.assertIn("write the notes", out["words"])
        self.assertIn("a short note", out["words"])
        self.assertTrue(out["fullRun"])
        self.assertTrue(out["fullStop"])
        self.assertIn("THE REPORT", out["singleReport"])
        self.assertEqual(out["startDisabled"], [True, False, None])
        # Minutes, input tokens, output tokens, model calls: none unless set.
        self.assertEqual(out["placeholders"], [["", "no limit"]] * 4)
        # Started with the words typed as it starts - offered the last ones.
        self.assertEqual(out["starts"], [{"project": "dbg_1", "variant": "noctx",
                                          "instruction": "write the notes",
                                          "limits": {"calls": 40}, "packages": "auto",
                                          # A sound when it ends, unless unticked.
                                          "sound": True}])
        self.assertEqual(out["continues"], [{"run": "run_single", "project": "dbg_1",
                                             "variant": "single", "limits": {},
                                             "sound": False, "packages": "ask",
                                             "instruction": "add a title"}])
        self.assertEqual(out["workContinues"], 0)
        self.assertGreater(out["liveGets"], 0)

    def test_an_interrupted_way_resumes_and_says_it_has_begun(self) -> None:
        stub = self._stub()
        interrupted = dict(DONE, id="run_cut", status="interrupted", continuable=True,
                           milestone={"version": "v18", "label": "Round 7"})
        stub["/api/debugger/experience/runs?project=dbg_1&variant=noctx"] = {
            "runs": [interrupted]}
        steps = (
            "await loadDebuggerExperience(true);await wait();"
            "click(document.querySelector('[data-xd-open=\"dbg_1\"]'));await wait(400);"
            "const way=document.querySelector('[data-xd-variant=\"noctx\"]');"
            "const row=way.querySelector('.xd-actions');"
            "out.row=text(row);"
            "const resume=row.querySelector('[data-xp-continue=\"run_cut\"]');"
            "out.label=text(resume);"
            "out.noStart=!row.querySelector('[data-xd-start]');"
            "out.inRun=text(way.querySelector('.xp-continue-row [data-xp-continue]'));"
            "click(resume);"
            "out.said=text(resume);out.disabled=resume.disabled;"
            "await wait(400);"
            "out.continues=window.__posts.filter(x=>x.p==='/api/debugger/experience/continue')"
            "  .map(x=>[x.body.run,x.body.variant]);")
        result = _render(self.engine, stub, steps)
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        self.assertEqual(out["label"], "Resume")
        self.assertTrue(out["noStart"], "Resume where Start was")
        self.assertIn("its last saved version, v18", out["row"])
        self.assertEqual(out["inRun"], "Resume")
        self.assertEqual(out["said"], "Resuming\u2026")
        self.assertTrue(out["disabled"])
        self.assertEqual(out["continues"], [["run_cut", "noctx"]])

    def test_the_way_shown_fills_a_wide_window(self) -> None:
        # The page's usual width cap is lifted on this tab: on a wide window
        # the way shown takes nearly all of it - and so does each, shown.
        steps = ("await loadDebuggerExperience(true);await wait(400);"
                 "out.widths=[];for(const tab of document.querySelectorAll('[data-xd-tab]')){"
                 "  click(tab);await wait();"
                 "  out.widths.push([...document.querySelectorAll('#dbg-xd [data-xd-variant]')]"
                 "    .filter(w=>!w.hidden).map(w=>Math.round(w.getBoundingClientRect().width)));}"
                 "out.window=window.innerWidth;")
        result = _render(self.engine, self._stub(), steps, width=2200)
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        self.assertEqual([len(one) for one in out["widths"]], [1, 1, 1], "one way at a time")
        for (width,) in out["widths"]:
            self.assertGreater(width, out["window"] * 0.9, out)

    def test_a_project_is_open_again_after_a_reload(self) -> None:
        # Loaded afresh, the page opens a project - the one open last, else
        # the newest - so a project just made never seems to be gone.
        steps = ("await loadDebuggerExperience(true);await wait(400);"
                 "out.open=!!document.querySelector('.xd-project-view');"
                 "out.kept=localStorage.getItem('pa.xdebug.open');")
        result = _render(self.engine, self._stub(), steps)
        self.assertEqual(result["err"], "", result["err"])
        self.assertTrue(result["out"]["open"])
        self.assertEqual(result["out"]["kept"], "dbg_1")

    def test_limits_apart_a_sound_a_chart_and_clearing_a_way(self) -> None:
        stub = self._stub()
        # The full way goes; the other two do not.
        stub["/api/debugger/experience/projects"] = {
            "projects": [PROJECT], "cleared": "dbg_1:single", "variant": "single",
            "files": 12, "folders": 3, "links": 1, "runs": 2, "not_removed": [],
            "left_alone": []}
        steps = (
            "await loadDebuggerExperience(true);await wait();"
            "click(document.querySelector('[data-xd-open=\"dbg_1\"]'));await wait(400);"
            "const way=()=>document.querySelector('#dbg-xd [data-xd-variant=\"noctx\"]');"
            "out.limits=text(way().querySelector('.xd-limits'));"
            "out.fields=[...way().querySelectorAll('[data-xd-limit]')].map(i=>i.dataset.xdLimit);"
            "out.soundLabel=text(way().querySelector('[data-xd-sound]').closest('label'));"
            "type(way().querySelector('[data-xd-limit=\"tokens_in\"]'),'900000');"
            "type(way().querySelector('[data-xd-limit=\"tokens_out\"]'),'30000');"
            # Ticked from the start: a sound when it ends is the default.
            "out.soundDefault=way().querySelector('[data-xd-sound]').checked;"
            "const tick=way().querySelector('[data-xd-sound]');tick.checked=true;"
            "tick.dispatchEvent(new Event('change',{bubbles:true}));"
            # Drawn again: the box stays ticked.
            "await loadDebuggerExperience(true);await wait();"
            "out.kept=way().querySelector('[data-xd-sound]').checked;"
            "click(way().querySelector('[data-xd-start]'));await wait(400);"
            "out.starts=window.__posts.filter(x=>x.p==='/api/debugger/experience/start').map(x=>x.body);"
            # The going way's run has its chart of tokens added up: builder,
            # reviewer and the total, in and out.
            "out.chart=[...document.querySelectorAll('[data-xd-variant=\"full\"] [data-xp-calls-chart=\"run_full\"] .xp-calls-line')].map(p=>p.dataset.xpLine+':'+p.dataset.xpPanel+':'+p.dataset.xpSum);"
            "out.legend=text(document.querySelector('[data-xp-calls-legend=\"run_full\"]'));"
            # A line hidden by clicking its name, and shown again.
            "const lines=()=>[...document.querySelectorAll('[data-xd-variant=\"full\"] [data-xp-calls-chart=\"run_full\"] .xp-calls-line')].map(p=>p.dataset.xpLine+':'+p.dataset.xpPanel);"
            "const key=(k)=>document.querySelector('[data-xp-calls-legend=\"run_full\"] [data-xp-calls-key=\"'+k+'\"]');"
            "click(key('reviewer'));await wait(200);"
            "out.hiddenLines=lines();out.pressed=key('reviewer').getAttribute('aria-pressed');"
            "click(key('total'));await wait(200);"
            "out.builderOnly=lines();"
            "click(key('reviewer'));click(key('total'));await wait(200);"
            "out.shownAgain=lines().length;"
            "out.continueSound=!!document.querySelector('[data-xd-variant=\"single\"] [data-xp-continue-sound=\"run_single\"]');"
            # A Clear for each way - the going one's not to be pressed.
            "out.clears=[...document.querySelectorAll('[data-xd-clear]')].map(b=>[b.dataset.xdClear,b.disabled,!!b.closest('[data-xd-variant=\"'+b.dataset.xdClear+'\"] .xd-way-head')]);"
            "out.projectClear=!!document.querySelector('.xd-project-view > p [data-xd-clear]');"
            # The single model's way cleared, while the full way goes: asked
            # in the page first, naming its folder - then done.
            "click(document.querySelector('[data-xd-clear=\"single\"]'));await wait(300);"
            "out.ask=text(document.querySelector('[data-xd-variant=\"single\"] .xd-clear-ask'));"
            "out.askUnderHead=document.querySelector('[data-xd-variant=\"single\"] .xd-clear-ask').previousElementSibling.className;"
            "out.asks=document.querySelectorAll('.xd-clear-ask').length;"
            "out.postsBefore=window.__posts.filter(x=>x.body.action==='clear').length;"
            "click(document.querySelector('[data-xd-clear-yes=\"single\"]'));await wait(400);"
            "out.posted=window.__posts.filter(x=>x.body.action==='clear').map(x=>x.body);"
            "out.toast=document.body.textContent.includes('Cleared: 12 files, 3 folders, 1 link and 2 runs.');"
            "out.askAfter=!!document.querySelector('.xd-clear-ask');"
            # Asked, and kept: nothing is cleared.
            "click(document.querySelector('[data-xd-clear=\"noctx\"]'));await wait(300);"
            "click(document.querySelector('[data-xd-clear-no=\"noctx\"]'));await wait(300);"
            "out.postedAfterNo=window.__posts.filter(x=>x.body.action==='clear').length;")
        result = _render(self.engine, stub, steps)
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        self.assertEqual(out["fields"], ["minutes", "tokens_in", "tokens_out", "calls"])
        self.assertIn("input tokens", out["limits"])
        self.assertIn("output tokens", out["limits"])
        self.assertEqual(out["soundLabel"], "Play a sound when it ends")
        self.assertTrue(out["soundDefault"])
        self.assertEqual(out["hiddenLines"], ["builder:in", "all:in", "builder:out", "all:out"])
        self.assertEqual(out["pressed"], "false")
        self.assertEqual(out["builderOnly"], ["builder:in", "builder:out"])
        self.assertEqual(out["shownAgain"], 6)
        self.assertTrue(out["kept"])
        self.assertEqual(out["starts"], [{"project": "dbg_1", "variant": "noctx",
                                          "instruction": "write the notes",
                                          "limits": {"tokens_in": 900000, "tokens_out": 30000},
                                          "packages": "auto", "sound": True}])
        self.assertEqual(out["chart"], ["builder:in:40000", "reviewer:in:52000", "all:in:92000",
                                        "builder:out:900", "reviewer:out:300", "all:out:1200"])
        self.assertIn("builder", out["legend"])
        self.assertIn("reviewer", out["legend"])
        self.assertTrue(out["continueSound"])
        # Each way its own Clear, in its own section; the going one's not
        # to be pressed - and none for the project as a whole.
        self.assertEqual(out["clears"], [["single", False, True], ["noctx", False, True],
                                         ["full", True, True]])
        self.assertFalse(out["projectClear"])
        self.assertIn("Clear everything this way wrote in C:\\bench\\A?",
                      out["ask"])
        self.assertIn("the other two ways and the project stay", out["ask"])
        self.assertEqual(out["asks"], 1)
        self.assertEqual(out["askUnderHead"], "xd-way-head", "asked just under the heading")
        self.assertEqual(out["postsBefore"], 0, "nothing is cleared before it is confirmed")
        self.assertEqual(out["posted"], [{"action": "clear", "id": "dbg_1",
                                          "variant": "single"}])
        self.assertTrue(out["toast"])
        self.assertFalse(out["askAfter"])
        self.assertEqual(out["postedAfterNo"], 1)

    def test_a_new_project_shows_its_three_subfolders(self) -> None:
        stub = self._stub()
        stub["/api/debugger/experience/projects"] = {"projects": [], "project": PROJECT}
        steps = (
            "await loadDebuggerExperience(true);await wait();"
            "click(document.querySelector('[data-xd-new]'));await wait();"
            "type(document.querySelector('[data-xd-field=\"new-name\"]'),'Notes');"
            "type(document.querySelector('[data-xd-field=\"new-description\"]'),'short');"
            "out.askedWords=!!document.querySelector('[data-xd-field=\"new-instruction\"]');"
            "click(document.querySelector('[data-xd-choose]'));await wait(300);"
            "out.form=text(document.querySelector('#xd-new'));"
            "out.kept=document.querySelector('[data-xd-field=\"new-name\"]').value;"
            "click(document.querySelector('[data-xd-create]'));await wait(300);"
            "out.creates=window.__posts.filter(x=>x.p==='/api/debugger/experience/projects').map(x=>x.body);")
        result = _render(self.engine, stub, steps)
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        for name in ("A", "B", "C"):
            self.assertIn("C:\\Users\\me\\bench_b\\" + name, out["form"])
        self.assertEqual(out["kept"], "Notes")
        # No instruction to make a project: it is given as a way starts.
        self.assertFalse(out["askedWords"])
        self.assertEqual(out["creates"], [{"action": "create", "name": "Notes",
                                           "description": "short",
                                           "folder": "C:\\Users\\me\\bench_b"}])


class AWaysHeadingAndItsLivePartsTests(unittest.TestCase):
    """Each way's heading says where its run stands - going with a spinner,
    paused, ended and how - with Stop and Clear beside the name. While a
    way goes, the panel is asked again and again: the heading and the run's
    live parts change in place, nothing else of the panel is drawn again, and
    nothing the user looks at moves. The run's end draws it whole - the view
    put back."""

    def setUp(self) -> None:
        from test_panels import _javascript_engine
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def test_heading_says_it_and_only_the_live_parts_change(self) -> None:
        stub = TheExperienceDebuggerPanelTests._stub(self)
        stub["/api/debugger/experience/runs?project=dbg_1&variant=full"] = {
            "runs": [json.loads(json.dumps(RUN))]}
        live = json.loads(json.dumps(LIVE))
        live["events"] = [{"seq": n, "at": "2026-10-08T12:00:00+00:00", "who": "worker",
                           "kind": "turn", "text": f"line {n}"} for n in range(1, 61)]
        live["last"] = 60
        stub["/api/work/live"] = live
        steps = (
            "await loadDebuggerExperience(true);await wait(1500);"
            "const way=(v)=>document.querySelector(`#dbg-xd [data-xd-variant=\"${v}\"]`);"
            "const head=(v)=>way(v).querySelector('.xd-way-head');"
            "out.heads=['single','noctx','full'].map(v=>text(head(v)));"
            "out.spins=['single','noctx','full'].map(v=>!!head(v).querySelector('.xp-live-spin'));"
            "out.stops=['single','noctx','full'].map(v=>!!head(v).querySelector('[data-xp-stop]'));"
            "out.clears=['single','noctx','full'].map(v=>{const b=head(v).querySelector('[data-xd-clear]');return b?b.disabled:null;});"
            "out.stopBelow=!!way('full').querySelector('.xd-actions [data-xp-stop]');"
            "out.chipInRun=getComputedStyle(way('full').querySelector('.xp-live-line .xp-live-state')).display;"
            "out.spinInRun=getComputedStyle(way('full').querySelector('.xp-live-doing .xp-live-spin')).display;"
            # Marked: what is not live.
            "const marks={limits:way('full').querySelector('.xd-limits'),"
            "  name:head('full').querySelector('h4'),run:way('full').querySelector('[data-xp-run-box=\"run_full\"]'),"
            "  report:way('single').querySelector('.xp-answer-text'),"
            "  start:way('noctx').querySelector('[data-xd-start]'),"
            "  words:document.querySelector('.xd-project-view .xd-words')};"
            "Object.values(marks).forEach(el=>{el.__mine=1;});"
            "const scroller=way('full').querySelector('.xp-live-scroll');"
            "scroller.scrollTop=200;window.scrollTo(0,700);await wait();"
            "const seen=[scroller.scrollTop,window.scrollY];"
            "const where=()=>Math.round(way('full').querySelector('.xp-live-scroll').getBoundingClientRect().top);"
            "out.seenTop=where();"
            # Ticks: the session paused, new lines, a new call.
            "for(let n=0;n<3;n++){"
            "  STUB['/api/work/live']=Object.assign({},STUB['/api/work/live'],{state:'paused',"
            "    events:Array.from({length:5},(_,i)=>({seq:61+n*5+i,at:'2026-10-08T12:02:00+00:00',"
            "      who:'reviewer',kind:'turn',text:'more'})),last:65+n*5,"
            "    calls:[{n:3+n,started:'2026-10-08T12:02:00+00:00',finished:'2026-10-08T12:02:3'+n+'+00:00',"
            "      seconds:30,who:'builder',part:'round 1',tokens_in:1000,tokens_out:10}]});"
            "  await loadDebuggerExperience();await wait(1300);}"
            "out.same=Object.entries(marks).filter(([k,el])=>!(document.contains(el)&&el.__mine===1)).map(([k])=>k);"
            "out.headPaused=text(head('full'));"
            "out.spinPaused=!!head('full').querySelector('.xp-live-spin');"
            "out.lines=scroller.querySelectorAll('.xp-live-ev').length;"
            "out.total=Number(way('full').querySelector('.xp-calls-line[data-xp-line=\"all\"][data-xp-panel=\"in\"]').dataset.xpSum);"
            "out.hint=text(way('full').querySelector('.xp-live-new'));"
            "out.now=[scroller.scrollTop,window.scrollY];out.seen=seen;"
            # It ends: drawn whole, the view put back.
            "const run=STUB['/api/debugger/experience/runs?project=dbg_1&variant=full'].runs[0];"
            "run.alive=false;run.status='done';"
            "STUB['/api/work/live']=Object.assign({},STUB['/api/work/live'],{state:'ended',events:[]});"
            "STUB['/api/debugger/experience/projects'].projects[0].variants.forEach(v=>{v.alive=false;});"
            "await loadDebuggerExperience();await wait(1300);"
            "out.redrawn=!(way('full').querySelector('[data-xp-run-box=\"run_full\"]').__mine===1);"
            "out.headEnded=text(head('full'));"
            "out.endedStop=!!head('full').querySelector('[data-xp-stop]');"
            "out.endedClear=head('full').querySelector('[data-xd-clear]').disabled;"
            "out.after=way('full').querySelector('.xp-live-scroll').scrollTop;"
            "out.afterTop=where();"
            "out.startBack=!!way('full').querySelector('.xd-actions');")
        result = _render(self.engine, stub, steps, budget=60000)
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        # Beside each name: where its run stands, Stop while it goes, Clear.
        self.assertIn("Single model · folder A", out["heads"][0])
        self.assertIn("ended", out["heads"][0])
        self.assertIn("not finished", out["heads"][0])
        self.assertIn("not started", out["heads"][1])
        self.assertIn("Full · folder C", out["heads"][2])
        self.assertIn("going", out["heads"][2])
        self.assertEqual(out["spins"], [False, False, True])
        self.assertEqual(out["stops"], [False, False, True])
        self.assertEqual(out["clears"], [False, False, True])
        self.assertFalse(out["stopBelow"], "Stop is beside the name, not below")
        self.assertEqual(out["chipInRun"], "none", "said once, in the heading")
        self.assertEqual(out["spinInRun"], "none")
        # Ticks: nothing but the live parts drawn again.
        self.assertEqual(out["same"], [])
        self.assertIn("paused", out["headPaused"])
        self.assertFalse(out["spinPaused"])
        self.assertEqual(out["lines"], 75)
        self.assertEqual(out["total"], 92_000 + 3_000)
        self.assertEqual(out["hint"], "15 new ↓")
        self.assertEqual(out["now"], out["seen"])
        # Ended: drawn whole, said in the heading, the view where it was.
        self.assertTrue(out["redrawn"])
        self.assertIn("ended", out["headEnded"])
        self.assertIn("done", out["headEnded"])
        self.assertFalse(out["endedStop"])
        self.assertFalse(out["endedClear"])
        # Start is offered again above the run - the page grew above what
        # was being read, and the window followed it: the session is where
        # it was on the screen, scrolled where it was.
        self.assertTrue(out["startBack"])
        self.assertEqual(out["after"], out["seen"][0])
        self.assertEqual(out["afterTop"], out["seenTop"])


def _tree(stages):
    return {"unit": {"id": "dbg_1:full", "name": "Notes", "kind": "debug project"},
            "cards": [{"id": "card_root", "title": "Notes", "depth": 0, "status": "working",
                       "level": "project", "parent_id": ""},
                      {"id": "card_1", "title": "Write the notes", "depth": 1,
                       "status": "working", "level": "deliverable", "parent_id": "card_root"}],
            "stages": [{"id": f"st_{n}", "card_id": "card_1", "kind": "execute",
                        "summary": f"Round {n}: STAGE {n} SAID", "began": "2026-10-08T12:00:00",
                        "states": [], "evaluation": {"verdict": "accepted"}}
                       for n in range(1, stages + 1)],
            "edges": [{"source": f"st_{n + 1}", "target": f"st_{n}", "kind": "follows"}
                      for n in range(1, stages)]}


class TheWaysAsTabsAndTheirTreesTests(unittest.TestCase):
    """The three ways are views to switch between - one shown at a time,
    each tab saying how its way stands - remembered for the project;
    switching draws nothing again and loses nothing typed or scrolled. Each
    builder-and-reviewer way shows how its stages evolved, as the Experience
    tab shows real work - its own tree, from its own route - drawn on in
    place as stages come."""

    def setUp(self) -> None:
        from test_panels import _javascript_engine
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def test_tabs_badges_switching_and_each_ways_tree(self) -> None:
        stub = TheExperienceDebuggerPanelTests._stub(self)
        tree = "/api/debugger/experience/tree?project=dbg_1&variant="
        stub[tree + "full"] = _tree(2)
        stub[tree + "noctx"] = _tree(0)
        live = json.loads(json.dumps(LIVE))
        live["events"] = [{"seq": n, "at": "2026-10-08T12:00:00+00:00", "who": "worker",
                           "kind": "turn", "text": f"line {n}"} for n in range(1, 61)]
        live["last"] = 60
        stub["/api/work/live"] = live
        steps = (
            "await loadDebuggerExperience(true);await wait(1500);"
            "const tabs=()=>[...document.querySelectorAll('[data-xd-tab]')];"
            "const shown=()=>[...document.querySelectorAll('#dbg-xd [data-xd-variant]')].filter(w=>!w.hidden&&w.offsetParent).map(w=>w.dataset.xdVariant);"
            "const way=(v)=>document.querySelector(`#dbg-xd [data-xd-variant=\"${v}\"]`);"
            "out.badges=tabs().map(t=>text(t.querySelector('.xd-tab-badge')));"
            "out.spins=tabs().map(t=>!!t.querySelector('.xp-live-spin'));"
            "out.selected=tabs().map(t=>t.getAttribute('aria-selected'));"
            "out.shown=shown();"
            # Each way's own tree, from its own route; the single model none.
            "out.treeGets=window.__gets.filter(g=>g.includes('/api/debugger/experience/tree')).map(g=>g.split('variant=')[1]);"
            "out.dots=document.querySelectorAll('#xd-tree-full [data-xp-stage]').length;"
            "out.where=[...document.querySelectorAll('#xd-tree-full [data-xp-stage]')].map(g=>g.dataset.xpWhere);"
            "out.single=text(way('single').querySelector('.xd-no-tree'));"
            "out.noctx=text(way('noctx').querySelector('#xd-tree-noctx'));"
            "out.workTree=!!document.querySelector('#xp-work-tree');"
            # A stage opened under the tree, as in the Experience tab.
            "click(document.querySelector('#xd-tree-full [data-xp-stage=\"st_2\"]'));await wait();"
            "out.stage=text(document.querySelector('#xd-stage-full'));"
            # Scrolled in the full way, and a draft in another: switched away
            # and back, nothing lost; nothing drawn again.
            "const scroller=way('full').querySelector('.xp-live-scroll');scroller.scrollTop=150;"
            "const fullWay=way('full');fullWay.__mine=1;"
            "click(tabs()[1]);await wait();"
            "out.shown2=shown();out.kept=localStorage.getItem('pa.xdebug.way.dbg_1');"
            "const box=way('noctx').querySelector('[data-xd-field=\"instruction-noctx\"]');box.__mine=1;"
            "type(box,'MY OWN DRAFT');"
            # A tick while another way is shown: its badge still follows.
            "STUB['/api/work/live']=Object.assign({},STUB['/api/work/live'],{state:'paused'});"
            "await loadDebuggerExperience();await wait(1300);"
            "out.badgeWhileHidden=text(tabs()[2].querySelector('.xd-tab-badge'));"
            "click(tabs()[2]);await wait();"
            "out.shown3=shown();out.scrollBack=way('full').querySelector('.xp-live-scroll').scrollTop;"
            "out.sameWay=way('full').__mine===1;"
            "click(tabs()[1]);await wait();"
            "const again=way('noctx').querySelector('[data-xd-field=\"instruction-noctx\"]');"
            "out.draft=again.value;out.sameBox=again.__mine===1;"
            # The keyboard: -> to the next tab.
            "tabs()[1].focus();tabs()[1].dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));await wait();"
            "out.shown4=shown();out.focusTab=document.activeElement.dataset.xdTab;"
            # A new stage on the next tick: drawn on in place, the stage open
            # still open.
            "STUB['" + tree + "full']=" + json.dumps(_tree(3)) + ";"
            "await loadDebuggerExperience();await wait(400);"
            "out.dotsAfter=document.querySelectorAll('#xd-tree-full [data-xp-stage]').length;"
            "out.stageAfter=text(document.querySelector('#xd-stage-full'));"
            "out.sameWayAfter=way('full').__mine===1;"
            # Drawn whole again (the project reopened): the way chosen is
            # still the one shown.
            "click(tabs()[1]);await wait();"
            "await loadDebuggerExperience(true);await wait(400);"
            "out.shown5=shown();out.draftAfter=way('noctx').querySelector('[data-xd-field=\"instruction-noctx\"]').value;")
        result = _render(self.engine, stub, steps, budget=60000)
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        # Each tab says how its way stands, a spinner on the one going.
        self.assertEqual(out["badges"], ["ended", "not started", "going"])
        self.assertEqual(out["spins"], [False, False, True])
        self.assertEqual(out["selected"], ["false", "false", "true"])
        self.assertEqual(out["shown"], ["full"])
        # Trees from the debugger's own route - none asked for the single
        # model - drawn in the way's own boxes, not the Experience tab's.
        self.assertEqual(sorted(set(out["treeGets"])), ["full", "noctx"])
        self.assertEqual(out["dots"], 2)
        self.assertEqual(set(out["where"]), {"xd-full"})
        self.assertIn("no tree of its work", out["single"])
        self.assertIn("No stages yet", out["noctx"])
        self.assertFalse(out["workTree"])
        self.assertIn("STAGE 2 SAID", out["stage"])
        # Switching: one way shown, remembered; nothing lost or drawn again.
        self.assertEqual(out["shown2"], ["noctx"])
        self.assertEqual(out["kept"], "noctx")
        self.assertEqual(out["badgeWhileHidden"], "paused")
        self.assertEqual(out["shown3"], ["full"])
        self.assertEqual(out["scrollBack"], 150)
        self.assertTrue(out["sameWay"])
        self.assertEqual(out["draft"], "MY OWN DRAFT")
        self.assertTrue(out["sameBox"])
        self.assertEqual(out["shown4"], ["full"])
        self.assertEqual(out["focusTab"], "full")
        # A new stage drawn on in place; the stage open still open.
        self.assertEqual(out["dotsAfter"], 3)
        self.assertIn("STAGE 2 SAID", out["stageAfter"])
        self.assertTrue(out["sameWayAfter"])
        self.assertEqual(out["shown5"], ["noctx"])
        self.assertEqual(out["draftAfter"], "MY OWN DRAFT")


class ItsOwnTabTests(unittest.TestCase):
    """The Experience debugger is a tab of its own, next to Experience - not
    a panel at the foot of the Debugger tab, where it was not found."""

    def test_the_panel_is_in_its_own_tab(self) -> None:
        page = (WEB / "index.html").read_text(encoding="utf-8")
        tabs = re.findall(r'<button data-tab="([^"]+)"', page)
        # With every debugger, after the Debugger (the user's own tabs first).
        self.assertEqual(tabs[tabs.index("debugger") + 1], "xdebug")
        own = page[page.index('<section id="xdebug"'):]
        own = own[:own.index("</section>")]
        self.assertIn('id="dbg-xd"', own)
        debugger = page[page.index('<section id="debugger"'):]
        self.assertNotIn('id="dbg-xd"', debugger[:debugger.index("</section>")])


if __name__ == "__main__":
    unittest.main()
