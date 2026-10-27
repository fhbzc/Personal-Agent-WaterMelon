"""The work panel in the Experience tab, as the user uses it: never drawn
again under their typing; every line of the live session reachable whole;
the instruction in force shown whole; tokens in and out, apart; and how far
making their files readable to the agents' code has got."""

from __future__ import annotations

import html
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"

CODE = ("import pandas as pd\n"
        "frame = pd.read_csv(r\"C:\\Users\\someone\\census_api\\weights_analysis\\works.csv\")\n"
        + "".join(f"step_{n} = frame.groupby('year').size() * {n}\n" for n in range(30))
        + "print('THE END OF THE CODE')")

INSTRUCTION = "Read every paper in the folder and " + "say what each one adds; " * 40 + "THE END."
LOGS = "C:/w/agent_work/x/run_logs/2026-10-08_12-00-00_run_1"

AMENDED = "From here, also " + "keep the tables as they are; " * 20 + "AMENDED END."


def _render(engine, stub, steps, budget=30000, flags=()):
    page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                  (WEB / "index.html").read_text(encoding="utf-8"))
    harness = (
        "<style>" + (WEB / "styles.css").read_text(encoding="utf-8") + "</style>"
        "<script>window.__posts=[];window.__gets=[];const STUB=" + json.dumps(stub) + ";"
        "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
        "if(o&&o.method==='POST')window.__posts.push({p,body:JSON.parse(o.body||'{}')});"
        "else window.__gets.push(String(u));"
        "return Promise.resolve({ok:true,status:200,statusText:'OK',"
        "headers:{get:()=>null},json:()=>Promise.resolve(STUB[p]||{})});};</script>"
        "<script>" + (WEB / "app.js").read_text(encoding="utf-8") + "</script>"
        "<script>window.addEventListener('load',async()=>{let err='';const out={};"
        "const wait=(ms)=>new Promise(r=>setTimeout(r,ms||80));"
        "const click=(el)=>el.dispatchEvent(new MouseEvent('click',{bubbles:true}));"
        "const text=(el)=>el?el.textContent.replace(/\\s+/g,' ').trim():'';"
        "activeTab='experience';"
        "document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.id==='experience'));"
        "document.body.style.minHeight='5000px';"
        "try{" + steps + "}catch(e){err=String(e&&e.stack?e.stack:e);}"
        "document.title=JSON.stringify({err,out});});</script>")
    with tempfile.TemporaryDirectory() as work:
        probe = Path(work) / "probe.html"
        probe.write_text(page + harness, encoding="utf-8")
        completed = subprocess.run(
            [engine, "--headless=new", "--disable-gpu", "--no-first-run",
             f"--user-data-dir={work}/profile", f"--virtual-time-budget={budget}",
             "--window-size=1200,800", *flags, "--dump-dom", probe.as_uri()],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    match = re.search(r"<title>(.*?)</title>", completed.stdout or "", re.S)
    assert match, "the page never finished rendering"
    return json.loads(html.unescape(match.group(1)))


def _event(seq, who, kind, text, **detail):
    return {"seq": seq, "at": "2026-10-08T12:00:%02d+00:00" % (seq % 60), "who": who,
            "kind": kind, "text": text, **({"detail": detail} if detail else {})}


def _live(state="going", events=None, last=3):
    return {"run": "run_1", "events": events or [], "last": last, "state": state,
            "status": "running", "resumes_from": None, "requests": {}, "pending": [],
            "contract": {"goal": "Each paper summarised, " + "with its key result, " * 10
                         + "GOAL END", "branch": "main", "since": "v1", "done": 0,
                         "total": 2, "amended": True},
            "progress": {"stage": "round 1", "phase": "rounds", "round": 1, "rounds_done": 0,
                         "cards": 2, "cards_done": 0,
                         "used": {"minutes": 3, "tokens": 1285000, "tokens_in": 1200000,
                                  "tokens_out": 85000, "calls": 9},
                         "limits": {"minutes": 45, "tokens": 2000000, "calls": 200},
                         "paused_minutes": 0, "activity": "worker: thinking",
                         "pause_asked": False, "held": False, "stopping": False}}


class _PanelBase(unittest.TestCase):

    def setUp(self) -> None:
        from test_panels import _javascript_engine
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")


class EveryLineWholeTests(_PanelBase):

    def test_lines_instruction_tokens_and_reading(self) -> None:
        preview = 'run_python(frame = pd.read_csv(r"C:\\Users\\someone\\census_api\\weights_an…) → exit 0'
        events = [_event(1, "you", "start", "You asked: " + INSTRUCTION[:300] + "…",
                         full="You asked: " + INSTRUCTION),
                  _event(2, "worker", "turn", "Calls 1 tool: run_python", tokens=1500,
                         tokens_in=1200, tokens_out=300, full="[tool call run_python]"),
                  _event(3, "worker", "tool", preview, tool="run_python", ok=True,
                         full="run_python\ncode:\n" + CODE + "\n→\nreturncode: 0")]
        run = {"id": "run_1", "folder": "C:/w/agent_work/x", "alive": True,
               "status": "running", "continuable": False, "paused": False,
               "started_at": "2026-10-08T12:00:00", "instruction": INSTRUCTION,
               "final": {}, "log": [], "versions": {"count": 0},
               "transcript": [LOGS + "/transcript-001.txt", LOGS + "/transcript-002.txt"],
               "log_dir": "C:/w/agent_work/x/run_logs", "run_log_dir": LOGS,
               "contract_in_force": {"goal": "g", "instructions": [AMENDED],
                                     "deliverables": []},
               "plan": {"packages": {"mode": "ask", "requests": []},
                        "total": {"calls": 9, "tokens_in": 1200000, "tokens_out": 85000}}}
        stub = {"/api/work/runs": {"runs": [run]},
                "/api/work/folders": {"folders": [], "warning": ""},
                "/api/work/limits": {"defaults": {"minutes": 45, "tokens": 1000000,
                                                  "calls": 200}},
                "/api/work/tree": {"cards": [], "stages": [], "edges": []},
                "/api/work/live": _live(events=events),
                "/api/work/reading": {"roots": [
                    {"root": "C:/Users/someone/Dropbox", "state": "running", "done": 12,
                     "total": 48}, {"root": "C:/Users/someone/Documents", "state": "done",
                                    "done": 30, "total": 30}]}}
        steps = (
            "experienceState.unit='task-live';await xpLoadRuns();await wait(1300);"
            "const box=document.querySelector('[data-xp-live-body=\"run_1\"]');"
            "out.instruction=text(box.querySelector('.xp-live-instruction'));"
            "out.goal=text(box.querySelector('.xp-live-contract'));"
            "const rows=[...box.querySelectorAll('.xp-live-ev')];"
            "out.fulls=rows.map(li=>{const pre=li.querySelector('.xp-live-more pre');return pre?pre.textContent:'';});"
            "out.whiteSpace=getComputedStyle(rows[2].querySelector('.xp-live-more pre')).whiteSpace;"
            "click(rows[2].querySelector('.xp-live-more summary'));"
            "rows[2].querySelector('.xp-live-more').open=true;await wait();"
            # A box of its own size, scrolled to read it all - the session's
            # box does not grow with it.
            "const pre=rows[2].querySelector('.xp-live-more pre');"
            "out.openHeight=getComputedStyle(pre).overflowY==='auto'&&pre.clientHeight>0;"
            "pre.scrollTop=pre.scrollHeight;"
            "out.reachable=pre.scrollTop+pre.clientHeight>=pre.scrollHeight-2;"
            "out.cost=text(rows[1].querySelector('.xp-live-cost'));"
            "out.bars=[...box.querySelectorAll('.xp-live-bar')].map(text);"
            "out.summary=text(document.querySelector('.xp-run > p.muted'));"
            "out.transcript=text(document.querySelector('.xp-transcript'));"
            "out.logs=text(document.querySelector('.xp-logs-row'));"
            "click(document.querySelector('[data-xp-open-logs]'));await wait();"
            "out.logPosts=window.__posts.filter(x=>x.p==='/api/work/open_logs');"
            "out.reading=text(document.querySelector('#xp-reading'));"
            "out.readingHidden=document.querySelector('#xp-reading').hidden;"
            "STUB['/api/work/reading']={roots:[{root:'C:/Users/someone/Dropbox',state:'done',done:48,total:48}]};"
            "await wait(2500);"
            "out.readingAfter=document.querySelector('#xp-reading').hidden;"
            "out.readingGets=window.__gets.filter(g=>g.includes('/api/work/reading')).length;"
            "await wait(5000);"
            "out.readingGetsLater=window.__gets.filter(g=>g.includes('/api/work/reading')).length;")
        result = _render(self.engine, stub, steps)
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        # The instruction in force, whole: the run's and the amendment's.
        self.assertIn(" ".join(INSTRUCTION.split()), out["instruction"])
        self.assertIn(" ".join(AMENDED.split()), out["instruction"])
        self.assertIn("GOAL END", out["goal"])
        # Every line whole, a click away - the code as it was written.
        self.assertEqual(out["fulls"][0], "You asked: " + INSTRUCTION)
        self.assertIn(CODE, out["fulls"][2])
        self.assertEqual(out["whiteSpace"], "pre-wrap")
        self.assertTrue(out["openHeight"], "a box of its own, with its own scrollbar")
        self.assertTrue(out["reachable"], "nothing of it is out of reach")
        # Tokens: in and out, apart.
        self.assertEqual(out["cost"], "1.2k in · 300 out")
        self.assertTrue(out["bars"][1].startswith("tokens 1,285,000 / 2,000,000"), out["bars"])
        self.assertIn("1.2M in · 85k out", out["bars"][1])
        self.assertIn("1.2M in · 85k out", out["summary"])
        self.assertIn("Full transcript: 2 files", out["transcript"])
        self.assertIn("transcript-002.txt", out["transcript"])
        self.assertIn("In " + LOGS, out["transcript"])
        # OPEN LOG FOLDER: the project's run logs, opened by the server.
        self.assertIn("Logs: C:/w/agent_work/x/run_logs", out["logs"])
        self.assertIn("Open log folder", out["logs"])
        self.assertEqual(out["logPosts"], [{"p": "/api/work/open_logs",
                                            "body": {"run": "run_1"}}])
        # Making the files readable: how far, while it goes - then nothing,
        # and no more asking.
        self.assertFalse(out["readingHidden"])
        self.assertIn("Making your files readable to the agents' code: 12/48", out["reading"])
        self.assertIn("now: C:/Users/someone/Dropbox", out["reading"])
        self.assertTrue(out["readingAfter"])
        self.assertEqual(out["readingGets"], out["readingGetsLater"])


class NotUnderTheUsersTypingTests(_PanelBase):

    def _run(self, alive):
        return {"id": "run_1", "folder": "C:/w/agent_work/x", "alive": alive,
                "status": "running" if alive else "not finished",
                "continuable": not alive, "paused": False,
                "started_at": "2026-10-08T12:00:00", "instruction": "first",
                "final": {}, "log": [], "versions": {"count": 0},
                "plan": {"packages": {"mode": "ask", "requests": []}}}

    def test_a_going_run_does_not_take_the_instruction_away(self) -> None:
        stub = {"/api/work/runs": {"runs": [self._run(True)]},
                "/api/work/folders": {"folders": [], "warning": ""},
                "/api/work/limits": {"defaults": {}},
                "/api/work/tree": {"cards": [], "stages": [], "edges": []},
                "/api/experience": {"units": [{"id": "task-live", "name": "x", "kind": "task",
                                               "stages": 0, "learning": {"running": True}}],
                                    "learning": 1},
                "/api/experience/graph": {"unit": {"id": "task-live", "name": "x",
                                                   "kind": "task"}, "stages": []},
                "/api/work/live": _live(events=[_event(1, "worker", "turn", "line 1")], last=1)}
        steps = (
            "experienceState.unit='task-live';await loadExperience(false);await wait(1300);"
            # The field in view, as it is when the user types in it - focused
            # off the screen, the browser would bring it into view itself.
            "const box=document.querySelector('#xp-instruction');box.__mine=1;"
            "box.scrollIntoView({block:'center'});await wait(100);"
            "const y=window.scrollY;const onScreen=box.getBoundingClientRect().top;"
            "box.focus();box.value='hello world, typed';box.dispatchEvent(new Event('input',{bubbles:true}));"
            "box.setSelectionRange(3,8);"
            # Everything that redraws the panel, again and again, with answers
            # that change: the session's state, new lines, the learning timer.
            "for(let n=0;n<4;n++){"
            "  STUB['/api/work/live']=Object.assign({},STUB['/api/work/live'],{state:n%2?'paused':'going',"
            "    events:[{seq:n+2,at:'2026-10-08T12:00:0'+n+'+00:00',who:'worker',kind:'version',text:'v'+n}],last:n+2});"
            "  STUB['/api/work/runs'].runs[0].instruction='changed '+n;"
            "  await xpLoadRuns();xpLiveRedrawPanel();await wait(1300);}"
            "await loadExperience(false);await wait(3500);"
            "const now=document.activeElement;"
            "out.same=now===box&&now.__mine===1;out.value=now.value;"
            "out.sel=[now.selectionStart,now.selectionEnd];out.y=[y,window.scrollY];"
            "out.onScreen=[onScreen,now.getBoundingClientRect().top];"
            "out.lines=document.querySelectorAll('.xp-live-ev').length;"
            "out.summaryBefore=text(document.querySelector('.xp-run summary'));"
            # Left: drawn, with what was typed kept.
            "box.blur();await wait(600);"
            "out.summaryAfter=text(document.querySelector('.xp-run summary'));"
            "out.kept=document.querySelector('#xp-instruction').value;")
        result = _render(self.engine, stub, steps)
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        self.assertTrue(out["same"], "the field is the same element, and still focused")
        self.assertEqual(out["value"], "hello world, typed")
        self.assertEqual(out["sel"], [3, 8])
        # The field typed in stays where it is on the screen.
        self.assertAlmostEqual(out["onScreen"][0], out["onScreen"][1], delta=1)
        self.assertEqual(out["y"][0], out["y"][1])
        # The session itself still followed the run.
        self.assertGreaterEqual(out["lines"], 4)
        self.assertIn("first", out["summaryBefore"])
        self.assertIn("changed 3", out["summaryAfter"])
        self.assertEqual(out["kept"], "hello world, typed")

    def test_the_words_for_continue_keep_their_caret(self) -> None:
        stub = {"/api/work/runs": {"runs": [self._run(False)]},
                "/api/work/folders": {"folders": [], "warning": ""},
                "/api/work/limits": {"defaults": {}},
                "/api/work/tree": {"cards": [], "stages": [], "edges": []},
                "/api/work/live": _live(state="ended", events=[], last=0)}
        steps = (
            "experienceState.unit='task-live';await xpLoadRuns(true);await wait();"
            "const box=document.querySelector('[data-xp-continue-instruction=\"run_1\"]');box.__mine=1;"
            "box.focus();box.value='Make it shorter, please';"
            "box.dispatchEvent(new Event('input',{bubbles:true}));box.setSelectionRange(5,10);"
            "box.scrollTop=0;"
            "for(let n=0;n<5;n++){STUB['/api/work/runs'].runs[0].instruction='again '+n;"
            "  await xpLoadRuns();xpLiveRedrawPanel();await wait(400);}"
            "const now=document.activeElement;"
            "out.same=now===box&&now.__mine===1;out.value=now.value;"
            "out.sel=[now.selectionStart,now.selectionEnd];"
            # A press of a button beside it: the button is still there to be
            # clicked, and the click is had.
            "const stop=document.querySelector('[data-xp-continue=\"run_1\"]');"
            "stop.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));"
            "box.blur();await wait(50);out.buttonStill=document.contains(stop);"
            "stop.dispatchEvent(new PointerEvent('pointerup',{bubbles:true}));"
            "click(stop);await wait(300);"
            "out.posts=window.__posts.filter(p=>p.p==='/api/work/continue').map(p=>p.body.instruction);")
        result = _render(self.engine, stub, steps)
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        self.assertTrue(out["same"])
        self.assertEqual(out["value"], "Make it shorter, please")
        self.assertEqual(out["sel"], [5, 10])
        self.assertTrue(out["buttonStill"])
        self.assertEqual(out["posts"], ["Make it shorter, please"])


def _call(n, at, who, tokens_in, tokens_out, seconds=20, **more):
    from datetime import datetime, timedelta, timezone
    end = datetime.fromisoformat(at).replace(tzinfo=timezone.utc)
    return {"n": n, "started": (end - timedelta(seconds=seconds)).isoformat(),
            "finished": end.isoformat(), "seconds": seconds, "who": who,
            "part": more.pop("part", "round 1"), "tokens_in": tokens_in,
            "tokens_out": tokens_out, **more}


#: A run's calls: the builder and the reviewer, one that failed, and - the
#: next morning, after it was continued - one more.
CALLS = [
    _call(1, "2026-10-08T12:00:20", "builder", 41_003, 500, part="contract",
          tokens_in_cached=40_000, tokens_in_cache_written=1_000),
    _call(2, "2026-10-08T12:01:00", "reviewer", 52_000, 1_200, part="contract"),
    _call(3, "2026-10-08T12:02:10", "builder", 0, 0, failed=True, error="the provider is down"),
    _call(4, "2026-10-08T12:06:00", "builder", 63_500, 2_400),
    _call(5, "2026-10-09T09:00:00", "reviewer", 30_000, 800, part="final review"),
]


class TheChartOfTheModelCallsTests(_PanelBase):
    """Each run's tokens ADDED UP over time: what went in above, what came
    out below - two panels, each its own scale, one time axis - a line for
    each who made calls and one for the total, said whole on hover and with
    the keyboard, in a table too, and drawn on as the run makes more."""

    def _stub(self, live_calls=()):
        run = {"id": "run_1", "folder": "C:/w/agent_work/x", "alive": True,
               "status": "running", "continuable": False, "paused": False,
               "started_at": "2026-10-08T12:00:00", "instruction": "write it",
               "final": {}, "log": [], "versions": {"count": 0}, "calls": CALLS,
               "plan": {"packages": {"mode": "ask", "requests": []},
                        "total": {"calls": 5, "tokens_in": 186503, "tokens_out": 4900}}}
        live = _live(events=[_event(1, "worker", "turn", "line")], last=1)
        live["calls"] = list(live_calls)
        live["progress"]["limits"] = {"minutes": 45, "tokens_in": 8000000,
                                      "tokens_out": 400000, "calls": 200}
        live["progress"]["sound"] = True
        return {"/api/work/runs": {"runs": [run]},
                "/api/work/folders": {"folders": [], "warning": ""},
                "/api/work/limits": {"defaults": {"minutes": 45, "tokens_in": 8000000,
                                                  "tokens_out": 400000, "calls": 200}},
                "/api/work/tree": {"cards": [], "stages": [], "edges": []},
                "/api/work/live": live}

    def test_drawn_hovered_tabled_and_drawn_on(self) -> None:
        steps = (
            "experienceState.unit='task-live';await xpLoadRuns();await wait(400);"
            "const box=document.querySelector('[data-xp-calls-chart=\"run_1\"]');"
            "const svg=box.querySelector('svg');"
            "out.panels=[...svg.querySelectorAll('.xp-calls-panel')].map(text);"
            "out.lines=[...svg.querySelectorAll('.xp-calls-line')].map(p=>"
            "  [p.tagName,p.dataset.xpLine,p.dataset.xpPanel,Number(p.dataset.xpSum)]);"
            "out.failed=[...svg.querySelectorAll('.xp-call-failed')].map(g=>Number(g.dataset.xpCall));"
            "const line=(who,panel)=>svg.querySelector(`.xp-calls-line[data-xp-line=\"${who}\"][data-xp-panel=\"${panel}\"]`);"
            "out.colours=['builder','reviewer','all'].map(w=>getComputedStyle(line(w,'in')).stroke);"
            "out.widths=['builder','all'].map(w=>getComputedStyle(line(w,'in')).strokeWidth);"
            "out.fill=getComputedStyle(line('all','in')).fill;"
            "out.dark=matchMedia('(prefers-color-scheme: dark)').matches;"
            # Only ever climbing: each step of the total is higher on the page.
            "out.ys=(line('all','in').getAttribute('d').match(/V[-0-9.]+/g)||[]).map(v=>Number(v.slice(1)));"
            "out.ticks=[...svg.querySelectorAll('.xp-calls-tick')].map(text);"
            "out.breaks=svg.querySelectorAll('.xp-calls-break').length;"
            "out.ends=[...svg.querySelectorAll('.xp-calls-endlabel')].map(text);"
            "out.width=[svg.getAttribute('width'),box.clientWidth];"
            "out.boxHeight=[box.getBoundingClientRect().height,Number(svg.getAttribute('height'))];"
            "out.legend=text(document.querySelector('[data-xp-calls-legend=\"run_1\"]'));"
            # Hover: the pointer near call 2 says the totals then, and the call.
            "const hit=box._xpCalls.find(h=>h.n===2);"
            "const r=svg.getBoundingClientRect();"
            "box.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,clientX:r.left+hit.x+5,clientY:r.top+40}));"
            "const tip=box.querySelector('.xp-calls-tip');"
            "out.tipHidden=tip.hidden;out.tip=[...tip.children].map(text);"
            "out.focusDots=svg.querySelectorAll('.xp-calls-focus circle').length;"
            "out.cross=svg.querySelector('.xp-calls-cross').getAttribute('visibility');"
            "box.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,clientX:r.left+2,clientY:r.top+40}));"
            "out.farHidden=tip.hidden;"
            # The keyboard: focused, the latest; <- the one before.
            "box.focus();await wait(30);out.keyFirst=text(tip.children[3]);"
            "box.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true}));"
            "out.keyLeft=text(tip.children[0]);"
            "box.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));"
            "out.escHidden=tip.hidden;box.blur();"
            # The table: every call, the latest first, the totals beside it.
            "const table=document.querySelector('[data-xp-calls-table=\"run_1\"]');"
            "table.open=true;await wait(80);"
            "out.head=[...table.querySelectorAll('thead th')].map(text);"
            "out.rows=[...table.querySelectorAll('tbody tr')].map(tr=>[...tr.children].map(text));"
            "out.liveBars=[...document.querySelectorAll('.xp-live-bar')].map(text);"
            "out.liveSound=text(document.querySelector('.xp-live-sound'));"
            # The live session brings a sixth call: drawn on.
            "STUB['/api/work/live'].calls=[" + json.dumps(_call(
                6, "2026-10-09T09:01:00", "builder", 70_000, 3_000)) + "];"
            "STUB['/api/work/live'].last=2;"
            "await wait(2500);"
            "out.after=Number(document.querySelector('[data-xp-calls-chart=\"run_1\"] .xp-calls-line[data-xp-line=\"all\"][data-xp-panel=\"in\"]').dataset.xpSum);"
            "out.afterRows=document.querySelectorAll('[data-xp-calls-rows=\"run_1\"] tbody tr').length;"
            "out.asked=window.__gets.filter(g=>g.includes('/api/work/live')).map(g=>g.split('calls=')[1]);")
        result = _render(self.engine, self._stub(), steps)
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        # Two panels, one time axis - never two scales on one plot.
        self.assertEqual(out["panels"], ["Input tokens, added up", "Output tokens, added up"])
        # A line for each who, and the total - its last point the sum of every
        # call: 41,003 + 52,000 + 63,500 + 30,000 in; the failed one took
        # nothing.
        self.assertEqual(out["lines"], [
            ["path", "builder", "in", 104_503], ["path", "reviewer", "in", 82_000],
            ["path", "all", "in", 186_503], ["path", "builder", "out", 2_900],
            ["path", "reviewer", "out", 2_000], ["path", "all", "out", 4_900]])
        self.assertEqual(sum(one["tokens_in"] for one in CALLS), 186_503)
        self.assertEqual(out["failed"], [3, 3], "a ring on its line, in each panel")
        builder, reviewer, total = out["colours"]
        self.assertNotEqual(builder, reviewer)
        # The token chart's first colour, in the step of the theme in effect;
        # the total in the page's ink, a little heavier.
        self.assertEqual(builder, "rgb(57, 135, 229)" if out["dark"] else "rgb(42, 120, 214)")
        self.assertEqual(total, "rgb(230, 233, 240)" if out["dark"] else "rgb(28, 34, 48)")
        self.assertEqual(out["widths"], ["2px", "2.5px"])
        self.assertEqual(out["fill"], "none", "a line, not an area")
        self.assertEqual(out["ys"], sorted(out["ys"], reverse=True), "it only climbs")
        self.assertEqual(len(out["ys"]), 4, "a step at each call that took something")
        # The input scale in clean steps, the next morning after a break.
        self.assertIn("200k", out["ticks"])
        self.assertEqual(out["breaks"], 2, "a break in each panel")
        self.assertTrue(any("Oct" in one for one in out["ticks"]), out["ticks"])
        self.assertTrue(any(one.startswith("total ") for one in out["ends"]), out["ends"])
        self.assertEqual(int(out["width"][0]), out["width"][1])
        self.assertEqual(round(out["boxHeight"][0]), out["boxHeight"][1],
                         "its box as tall as the chart, axis and all")
        for said in ("builder", "reviewer", "total", "failed", "5 calls", "187k in · 4.9k out"):
            self.assertIn(said, out["legend"])
        # Hover: the totals then first, each one's, then the call.
        self.assertFalse(out["tipHidden"])
        self.assertEqual(out["tip"][0], "total 93,003 in · 1,700 out so far")
        self.assertEqual(out["tip"][1], "builder: 41,003 in · 500 out")
        self.assertEqual(out["tip"][2], "reviewer: 52,000 in · 1,200 out")
        self.assertEqual(out["tip"][3], "call 2 · reviewer · contract: 52,000 in · 1,200 out")
        self.assertIn("20 s", out["tip"][4])
        self.assertEqual(out["focusDots"], 6, "a dot on each line, in each panel")
        self.assertEqual(out["cross"], "visible")
        self.assertTrue(out["farHidden"], "nothing near: nothing said")
        self.assertIn("call 5 · reviewer · final review", out["keyFirst"])
        self.assertEqual(out["keyLeft"], "total 156,503 in · 4,100 out so far")
        self.assertTrue(out["escHidden"])
        self.assertIn("Total in so far", out["head"])
        self.assertEqual([row[0] for row in out["rows"]], ["5", "4", "3", "2", "1"])
        self.assertEqual(out["rows"][4][5:], ["41,003", "500", "41,003", "500"])
        self.assertEqual(out["rows"][0][7:], ["186,503", "4,900"])
        self.assertEqual(out["rows"][2][5], "failed")
        # Input and output, each against its own limit.
        self.assertTrue(out["liveBars"][1].startswith("input tokens"), out["liveBars"])
        self.assertIn("/ 8,000,000", out["liveBars"][1])
        self.assertTrue(out["liveBars"][2].startswith("output tokens"), out["liveBars"])
        self.assertIn("/ 400,000", out["liveBars"][2])
        self.assertFalse(any(one.startswith("tokens ") for one in out["liveBars"]))
        self.assertIn("sound", out["liveSound"])
        # Drawn on as the run makes more - asking only for those after the
        # last seen - in place, the table too.
        self.assertEqual(out["after"], 186_503 + 70_000)
        self.assertEqual(out["afterRows"], 6)
        self.assertIn("5", out["asked"])

    def _theme(self, flags):
        steps = (
            "experienceState.unit='task-live';await xpLoadRuns();await wait(300);"
            "out.dark=matchMedia('(prefers-color-scheme: dark)').matches;"
            "const line=(who)=>document.querySelector(`.xp-calls-line[data-xp-line=\"${who}\"][data-xp-panel=\"in\"]`);"
            "out.builder=getComputedStyle(line('builder')).stroke;"
            "out.total=getComputedStyle(line('all')).stroke;"
            "out.ring=getComputedStyle(document.querySelector('.xp-calls-end')).stroke;"
            "out.panel=getComputedStyle(document.documentElement).getPropertyValue('--panel').trim();")
        result = _render(self.engine, self._stub(), steps, flags=flags)
        self.assertEqual(result["err"], "", result["err"])
        return result["out"]

    def test_dark_mode_has_its_own_steps(self) -> None:
        out = self._theme(("--force-dark-mode",))
        if not out["dark"]:
            self.skipTest("this browser cannot be put in dark mode from the command line")
        self.assertEqual(out["builder"], "rgb(57, 135, 229)")        # the dark step
        self.assertEqual(out["total"], "rgb(230, 233, 240)")         # the dark ink
        self.assertEqual(out["panel"], "#1c2028")
        self.assertEqual(out["ring"], "rgb(28, 32, 40)", "ringed in the panel's colour")

    def test_light_mode_has_its_own_steps(self) -> None:
        out = self._theme(("--blink-settings=preferredColorScheme=1",))
        if out["dark"]:
            self.skipTest("this browser cannot be put in light mode from the command line")
        self.assertEqual(out["builder"], "rgb(42, 120, 214)")        # the light step
        self.assertEqual(out["total"], "rgb(28, 34, 48)")            # the light ink
        self.assertEqual(out["panel"], "#ffffff")
        self.assertEqual(out["ring"], "rgb(255, 255, 255)", "ringed in the panel's colour")


class LimitsApartAndASoundTests(_PanelBase):
    """Input and output tokens are limited apart where a run is started and
    continued; a box beside Start and each Continue asks for a sound when
    the run ends."""

    def test_start_and_continue_send_both_limits_and_the_sound(self) -> None:
        run = {"id": "run_1", "folder": "C:/w/agent_work/x", "alive": False,
               "status": "not finished", "continuable": True, "paused": False,
               "started_at": "2026-10-08T12:00:00", "instruction": "first", "calls": [],
               "final": {}, "log": [], "versions": {"count": 0},
               "plan": {"packages": {"mode": "ask", "requests": []}}}
        stub = {"/api/work/runs": {"runs": [run]},
                "/api/work/folders": {"folders": [], "warning": ""},
                "/api/work/limits": {"defaults": {"minutes": 45, "tokens_in": 8000000,
                                                  "tokens_out": 400000, "calls": 200}},
                "/api/work/tree": {"cards": [], "stages": [], "edges": []},
                "/api/work/live": _live(state="ended", events=[], last=0),
                # Start opens the plan to check first (delegate.py): ready here.
                "/api/work/drafts": {"draft": {
                    "id": "draft_1", "status": "ready", "unit_id": "task-live",
                    "source": "add", "text": "first", "folders": [], "window": {},
                    "context": {}, "plan": {"title": "The figures", "steps": ["Redo them"],
                                            "deliverables": ["README.md"]}}},
                "/api/work/drafts/start": {"run": "run_2", "unit": "task-live"}}
        steps = (
            "experienceState.unit='task-live';await xpLoadRuns(true);await wait();"
            "out.labels=text(document.querySelector('.xp-limits'));"
            "out.values=['minutes','tokens_in','tokens_out','calls'].map(n=>document.querySelector('#xp-limit-'+n).value);"
            "out.oldField=Boolean(document.querySelector('#xp-limit-tokens'));"
            "out.startSound=text(document.querySelector('#xp-sound').closest('label'));"
            "document.querySelector('#xp-limit-tokens_in').value='2000000';"
            "document.querySelector('#xp-limit-tokens_out').value='50000';"
            "const box=document.querySelector('#xp-sound');box.checked=true;"
            "box.dispatchEvent(new Event('change',{bubbles:true}));"
            # Drawn again: the box stays ticked.
            "await xpLoadRuns(true);await wait();"
            "out.kept=document.querySelector('#xp-sound').checked;"
            "document.querySelector('#xp-limit-tokens_in').value='2000000';"
            "document.querySelector('#xp-limit-tokens_out').value='50000';"
            "click(document.querySelector('#xp-start'));await wait(600);"
            # The plan, to check: nothing started before its Start.
            "out.planShown=document.querySelector('#delegate').open;"
            "out.beforeStart=window.__posts.filter(p=>p.p==='/api/work/drafts/start').length;"
            "click(document.querySelector('#delegate-start'));await wait(300);"
            "out.closed=!document.querySelector('#delegate').open;"
            "experienceState.unit='task-live';await xpLoadRuns(true);await wait();"
            "document.querySelector('#xp-limit-tokens_in').value='2000000';"
            "document.querySelector('#xp-limit-tokens_out').value='50000';"
            # Continue: its own box, unticked until ticked.
            "const again=document.querySelector('[data-xp-continue-sound=\"run_1\"]');"
            "out.continueBox=Boolean(again)&&!again.checked;"
            "again.checked=true;again.dispatchEvent(new Event('change',{bubbles:true}));"
            "click(document.querySelector('[data-xp-continue=\"run_1\"]'));await wait(300);"
            "out.starts=window.__posts.filter(p=>p.p==='/api/work/drafts').map(p=>p.body);"
            "out.started=window.__posts.filter(p=>p.p==='/api/work/drafts/start').map(p=>p.body);"
            "out.continues=window.__posts.filter(p=>p.p==='/api/work/continue').map(p=>p.body);")
        result = _render(self.engine, stub, steps)
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        self.assertIn("input tokens", out["labels"])
        self.assertIn("output tokens", out["labels"])
        self.assertNotIn("in + out", out["labels"])
        self.assertEqual(out["values"], ["45", "8000000", "400000", "200"])
        self.assertFalse(out["oldField"])
        self.assertEqual(out["startSound"], "Play a sound when it ends")
        self.assertTrue(out["kept"])
        self.assertEqual(len(out["starts"]), 1)
        self.assertTrue(out["planShown"])
        self.assertEqual(out["beforeStart"], 0, "nothing runs before the plan's Start")
        self.assertTrue(out["closed"])
        self.assertEqual(out["started"][0]["id"], "draft_1")
        self.assertEqual(out["started"][0]["plan"]["title"], "The figures")
        start = out["starts"][0]
        # The instruction first (the Delegate work box): what was typed is
        # sent as the instruction, written into the plan there.
        self.assertEqual((start["unit"], start["source"], start["instruction"]),
                         ("task-live", "add", ""))
        self.assertTrue(start["write_first"])
        self.assertEqual(start["limits"], {"minutes": 45, "tokens_in": 2000000,
                                           "tokens_out": 50000, "calls": 200})
        self.assertIs(start["sound"], True)
        self.assertTrue(out["continueBox"])
        self.assertEqual(len(out["continues"]), 1)
        self.assertIs(out["continues"][0]["sound"], True)
        self.assertEqual(out["continues"][0]["limits"]["tokens_out"], 50000)


REPORT = "".join(f"Paragraph {n}: what was found, and where it is.\n" for n in range(80))


class OnlyTheLivePartsChangeTests(_PanelBase):
    """While a run goes, the panel is asked again and again: only its live
    parts change - the session's lines, the chart, the counters - and in
    place; the rest is the same elements it was. Nothing the user is looking
    at moves: the window, a scrolled report, a line's "all of it", the live
    session scrolled up - which says how many new lines came below. Drawn
    whole when the run's state changes, the view is put back as it was. And
    each part of the run is a box of its own size: the page does not grow as
    the session does."""

    def _stub(self):
        run = {"id": "run_1", "folder": "C:/w/agent_work/x", "alive": True,
               "status": "running", "continuable": False, "paused": False,
               "started_at": "2026-10-08T12:00:00", "instruction": "write it",
               "final": {}, "log": [{"at": "2026-10-08T12:00:01", "what": "started"}],
               "versions": {"count": 0},
               "transcript": [LOGS + "/transcript-001.txt"], "run_log_dir": LOGS,
               "calls": CALLS[:2],
               "contract_in_force": {"goal": "Each paper summarised",
                                     "scope": ["the papers"], "deliverables": []},
               "plan": {"packages": {"mode": "ask", "requests": []}, "phase": "rounds",
                        "last_report": REPORT, "next": "write the summary",
                        "total": {"calls": 2, "tokens_in": 93003, "tokens_out": 1700}}}
        events = [_event(n, "worker", "tool", f"line {n}", full=CODE) for n in range(1, 41)]
        return {"/api/work/runs": {"runs": [run]},
                "/api/work/folders": {"folders": [], "warning": ""},
                "/api/work/limits": {"defaults": {}},
                "/api/work/tree": {"cards": [], "stages": [], "edges": []},
                "/api/work/live": _live(events=events, last=40)}

    def test_ticks_change_only_what_is_live_and_never_the_view(self) -> None:
        more = [_call(3 + n, "2026-10-08T12:%02d:00" % (10 + n), "builder" if n % 2 else "reviewer",
                      10_000 * (n + 1), 100 * (n + 1)) for n in range(3)]
        steps = (
            "experienceState.unit='task-live';await xpLoadRuns();await wait(1300);"
            "const q=(s)=>document.querySelector(s);"
            # Marked: the parts that are not live.
            "const marks={contract:q('[data-xp-keep=\"contract:run_1\"]'),"
            "  run:q('[data-xp-run-box=\"run_1\"]'),summary:q('.xp-run > summary'),"
            "  answer:q('.xp-answer-text'),limits:q('.xp-limits'),"
            "  instruction:q('#xp-instruction'),live:q('[data-xp-live-body=\"run_1\"]')};"
            "Object.values(marks).forEach(el=>{el.__mine=1;});"
            # Where the user looks: the report scrolled, a line's all of it
            # opened and scrolled, the session scrolled up, the window down.
            "const answer=marks.answer;answer.scrollTop=300;"
            "const scroller=q('[data-xp-live-body=\"run_1\"] .xp-live-scroll');"
            "const more=scroller.querySelectorAll('.xp-live-more')[30];more.open=true;await wait();"
            "const full=more.querySelector('pre');full.scrollTop=90;"
            "scroller.scrollTop=more.offsetTop-40;"
            "window.scrollTo(0,500);await wait();"
            "const seen={answer:answer.scrollTop,full:full.scrollTop,live:scroller.scrollTop,y:window.scrollY};"
            "const tall=document.documentElement.scrollHeight;"
            "const liveTall=scroller.getBoundingClientRect().height;"
            "out.lines0=scroller.querySelectorAll('.xp-live-ev').length;"
            # Three ticks: new lines and calls in the session, the runs'
            # counters, log, report and transcript grown.
            "const MORE=" + json.dumps(more) + ";"
            "for(let n=0;n<3;n++){"
            "  const seq=41+n*10;"
            "  STUB['/api/work/live']=Object.assign({},STUB['/api/work/live'],{"
            "    events:Array.from({length:10},(_,i)=>({seq:seq+i,at:'2026-10-08T12:01:00+00:00',"
            "      who:'reviewer',kind:'turn',text:'new '+(seq+i)})),last:seq+9,calls:[MORE[n]]});"
            "  const run=STUB['/api/work/runs'].runs[0];"
            "  run.calls=run.calls.concat([MORE[n]]);"
            "  run.plan.total={calls:3+n,tokens_in:93003+10000*(n+1),tokens_out:1700+100*(n+1)};"
            "  run.log=run.log.concat([{at:'2026-10-08T12:0'+n+':00',what:'round '+n}]);"
            "  run.plan.last_report=run.plan.last_report+'More '+n+'\\n';"
            "  run.transcript=run.transcript.concat(['" + LOGS + "/transcript-00'+(n+2)+'.txt']);"
            "  await xpLoadRuns();await wait(1300);}"
            "out.same=Object.entries(marks).filter(([k,el])=>!(document.contains(el)&&el.__mine===1)).map(([k])=>k);"
            "out.lines=scroller.querySelectorAll('.xp-live-ev').length;"
            "out.newHint=q('[data-xp-live-body=\"run_1\"] .xp-live-new');"
            "out.newHint=out.newHint&&!out.newHint.hidden?text(out.newHint):'';"
            "out.now={answer:answer.scrollTop,full:full.scrollTop,live:scroller.scrollTop,y:window.scrollY};"
            "out.seen=seen;"
            "out.tall=[tall,document.documentElement.scrollHeight];"
            "out.liveTall=[liveTall,scroller.getBoundingClientRect().height];"
            "out.totals=text(q('[data-xp-run-totals=\"run_1\"]'));"
            "out.report=answer.textContent.trim().split('\\n').pop();"
            "out.transcript=text(q('[data-xp-transcript-count=\"run_1\"]'));"
            "out.steps=q('[data-xp-run-log=\"run_1\"]').children.length;"
            "const total=q('.xp-calls-line[data-xp-line=\"all\"][data-xp-panel=\"in\"]');"
            "out.total=Number(total.dataset.xpSum);"
            # The hint pressed: down to the newest, and gone.
            "q('[data-xp-live-body=\"run_1\"] .xp-live-new').click();await wait();"
            "out.atBottom=scroller.scrollHeight-scroller.scrollTop-scroller.clientHeight<24;"
            "out.hintGone=q('[data-xp-live-body=\"run_1\"] .xp-live-new').hidden;"
            "scroller.scrollTop=more.offsetTop-40;await wait();const liveBack=scroller.scrollTop;"
            # The run's state changes: drawn whole - the view put back.
            "const run=STUB['/api/work/runs'].runs[0];run.status='paused';run.paused=true;"
            "await xpLoadRuns();await wait(300);"
            "out.redrawn=!(q('[data-xp-run-box=\"run_1\"]').__mine===1);"
            "out.summaryAfter=text(q('.xp-run > summary'));"
            "out.after={answer:q('.xp-answer-text').scrollTop,live:q('[data-xp-live-body=\"run_1\"] .xp-live-scroll').scrollTop,"
            "  full:q('[data-xp-live-body=\"run_1\"] .xp-live-more[open] pre')?.scrollTop,y:window.scrollY};"
            "out.liveBack=liveBack;"
            "out.heights=[...document.querySelectorAll('.xp-live-scroll, .xp-answer-text, [data-xp-keep=\"contract:run_1\"], .xp-calls-chart, .xp-transcript-list')]"
            "  .map(el=>[el.className.split(' ')[0]||el.dataset.xpKeep,getComputedStyle(el).overflowY,"
            "    getComputedStyle(el).height!=='auto'||getComputedStyle(el).maxHeight!=='none']);")
        result = _render(self.engine, self._stub(), steps, budget=60000)
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        # Not one of them drawn again.
        self.assertEqual(out["same"], [])
        # The live parts gained what came: lines, calls, counters, words.
        self.assertEqual(out["lines0"], 40)
        self.assertEqual(out["lines"], 70)
        self.assertEqual(out["total"], 93_003 + 10_000 + 20_000 + 30_000,
                         "the total's last point is the sum of every call")
        self.assertIn("5 calls in all", out["totals"])
        self.assertEqual(out["report"], "More 2")
        self.assertEqual(out["transcript"], "4 files")
        self.assertEqual(out["steps"], 4)
        # Nothing the user looks at moved; the session says what came below.
        self.assertEqual(out["now"], out["seen"])
        self.assertGreater(out["seen"]["answer"], 0)
        self.assertGreater(out["seen"]["full"], 0)
        self.assertGreater(out["seen"]["y"], 0)
        self.assertEqual(out["newHint"], "30 new ↓")
        self.assertTrue(out["atBottom"])
        self.assertTrue(out["hintGone"])
        # The page did not grow as the session did; the session's box neither.
        self.assertEqual(out["tall"][0], out["tall"][1])
        self.assertEqual(out["liveTall"][0], out["liveTall"][1])
        # Drawn whole on a change of state - and the view put back exactly.
        self.assertTrue(out["redrawn"])
        self.assertIn("paused", out["summaryAfter"])
        self.assertEqual(out["after"], {"answer": out["seen"]["answer"], "live": out["liveBack"],
                                        "full": out["seen"]["full"], "y": out["seen"]["y"]})
        # Each part a box of its own size, with its own scrollbar.
        for name, overflow, sized in out["heights"]:
            self.assertEqual(overflow if name != "xp-calls-chart" else "auto", "auto", name)
            self.assertTrue(sized, name)


#: A branched history - v1, v2, v3 on main; v4 a branch from v1 - two with a
#: commit-like message, two saved before versions had one.
def _version(vid, parent, branch, kind, label, message=None):
    one = {"id": vid, "n": int(vid[1:]), "parent": parent, "branch": branch, "kind": kind,
           "label": label, "at": "2026-10-08T12:0%s:00" % vid[1:], "files": 3, "granted": 0,
           "summary": {"rounds": 1, "cards": 2, "cards_done": 1, "todo": 2, "phase": "rounds"}}
    if message:
        one["message"] = message
    return one


TREE = {"run": "run_2", "head": "v4", "branch": "branch 2", "branches": ["main", "branch 2"],
        "alive": False, "status": "not finished",
        "versions": [
            _version("v1", "", "main", "contract", "The contract approved"),
            _version("v2", "v1", "main", "round", "Round 1: The notes", {
                "title": "Write the notes' first draft and the outline it follows",
                "body": ["notes.md: a first draft of every section", "outline.md: the order"],
                "files": {"added": ["notes.md", "outline.md"], "modified": ["README.md"],
                          "deleted": []}}),
            _version("v3", "v2", "main", "finished", "done"),
            _version("v4", "v1", "branch 2", "round", "Round 1 again", {
                "title": "Start again from the contract, shorter",
                "body": ["notes.md: shorter"],
                "files": {"added": [], "modified": ["notes.md"], "deleted": ["outline.md"]}})]}

V2 = {"id": "v2", "label": "Round 1: The notes", "kind": "round", "branch": "main",
      "parent": "v1", "at": "2026-10-08T12:02:00", "head": False, "goal": "the notes",
      "phase": "rounds", "summary": {"rounds": 1, "cards": 2, "cards_done": 1},
      "files": [{"path": "notes.md", "size": 5}, {"path": "outline.md", "size": 9}],
      "granted": [], "todo": {"next": ["write the summary"]}, "deck": False,
      "contract": {"goal": "THE CONTRACT AT V2", "scope": ["the notes"], "out_of_scope": [],
                   "instructions": [], "deliverables": []},
      "slides": {"status": "made", "count": 1,
                 "page": "<!doctype html><title>s</title><section class='slide'>V2 SLIDE</section>"}}


class TheVersionTreeTests(_PanelBase):
    """A run's versions as a commit graph: a row per version, the newest on
    top, what each changed and its files; a click on one shows the whole run
    as it stood then - its contract, its slides, its files - under a line
    with the way back to the latest; Continue is offered on it; the choice
    is kept as the panel is asked again, and a version saved meanwhile is
    added in place."""

    def _stub(self):
        run = {"id": "run_2", "folder": "C:/w/agent_work/x", "alive": False,
               "status": "not finished", "continuable": True, "extendable": False,
               "paused": False, "started_at": "2026-10-08T12:00:00",
               "instruction": "write the notes", "final": {}, "log": [], "calls": [],
               "contract_in_force": {"goal": "THE CONTRACT IN FORCE", "branch": "branch 2",
                                     "deliverables": []},
               "plan": {"packages": {"mode": "ask", "requests": []}},
               "versions": {"count": 4, "head": "v4", "branch": "branch 2"}}
        return {"/api/work/runs": {"runs": [run]},
                "/api/work/folders": {"folders": [], "warning": ""},
                "/api/work/limits": {"defaults": {}},
                "/api/work/tree": {"cards": [], "stages": [], "edges": []},
                "/api/work/versions": json.loads(json.dumps(TREE)), "/api/work/version": V2,
                "/api/work/live": _live(state="ended", events=[], last=0)}

    def test_walked_shown_continued_and_kept(self) -> None:
        steps = (
            "experienceState.unit='task-ver';await xpLoadRuns();await wait();"
            "const q=(s)=>document.querySelector(s);"
            "const graph=q('[data-xp-version-graph=\"run_2\"]');"
            "out.rows=[...graph.querySelectorAll('.xp-vrow')].map(li=>li.dataset.xpVersionNode);"
            "out.edges=graph.querySelectorAll('.xp-vedge').length;"
            "out.dots=graph.querySelectorAll('.xp-vnode-dot').length;"
            "out.branchBends=graph.querySelectorAll('path.xp-vedge').length;"
            "out.head=[...graph.querySelectorAll('.xp-vrow-head')].map(li=>li.dataset.xpVersionNode);"
            "const row=(v)=>graph.querySelector(`[data-xp-version-node=\"${v}\"]`);"
            "out.v2=text(row('v2').querySelector('.xp-vrow-title'));"
            "out.v2Full=row('v2').querySelector('.xp-vrow-title').title;"
            "out.v2Files=text(row('v2').querySelector('.xp-vfiles'));"
            "out.v1=text(row('v1').querySelector('.xp-vrow-title'));"
            "out.v1Files=!!row('v1').querySelector('.xp-vfiles');"
            "out.boxFixed=getComputedStyle(graph).overflowY+':'+getComputedStyle(graph).height;"
            "out.contractBefore=text(q('[data-xp-keep=\"contract:run_2\"]'));"
            # The second version clicked: the whole run as it stood then.
            "row('v2').dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait(400);"
            "out.showing=text(q('.xp-version-showing'));"
            "out.contractAfter=text(q('[data-xp-keep=\"contract:run_2\"]'));"
            "out.message=text(q('.xp-version-picked .xp-vmsg'));"
            "out.body=[...document.querySelectorAll('.xp-version-picked .xp-vmsg-body li')].map(text);"
            "out.fileLists=text(q('.xp-version-picked .xp-vmsg-files-box'));"
            "out.view=text(q('.xp-version-picked .xp-version-view'));"
            "out.frame=!!q('.xp-version-picked iframe[data-xp-version-slides]');"
            "out.shownRow=[...document.querySelectorAll('.xp-vrow-shown')].map(li=>li.dataset.xpVersionNode);"
            "out.kept=localStorage.getItem('pa.xp.version.run_2');"
            "out.gets=window.__gets.filter(g=>g.includes('/api/work/version?')).length;"
            # Continue, on the version shown.
            "out.fromHere=!!q('.xp-version-picked [data-xp-version-from=\"v2\"]');"
            "q('.xp-version-picked [data-xp-version-from=\"v2\"]').dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait(300);"
            "const words=q('.xp-version-picked .xp-version-continue textarea');"
            "out.words=!!words;out.focused=document.activeElement===words;"
            "out.continueRow=text(q('.xp-continue-row'));"
            # Kept across the panel asked again; a version saved meanwhile is
            # added to the tree in place.
            "const runBox=q('[data-xp-run-box=\"run_2\"]');runBox.__mine=1;words.blur();"
            "for(let n=0;n<3;n++){await xpLoadRuns();await wait(200);}"
            "out.stillShown=[...document.querySelectorAll('.xp-vrow-shown')].map(li=>li.dataset.xpVersionNode);"
            "out.sameRun=q('[data-xp-run-box=\"run_2\"]').__mine===1;"
            "const t=STUB['/api/work/versions'];t.versions.push({id:'v5',n:5,parent:'v4',branch:'branch 2',"
            "  kind:'round',label:'Round 2',at:'2026-10-08T12:05:00',summary:{},"
            "  message:{title:'Round 2: the summary',body:[],files:{added:['summary.md'],modified:[],deleted:[]}}});"
            "t.head='v5';STUB['/api/work/runs'].runs[0].versions={count:5,head:'v5',branch:'branch 2'};"
            "await xpLoadRuns();await wait(300);"
            "out.rowsAfter=[...q('[data-xp-version-graph=\"run_2\"]').querySelectorAll('.xp-vrow')].map(li=>li.dataset.xpVersionNode);"
            "out.headAfter=[...document.querySelectorAll('.xp-vrow-head')].map(li=>li.dataset.xpVersionNode);"
            "out.sameRunAfter=q('[data-xp-run-box=\"run_2\"]').__mine===1;"
            "out.shownAfter=[...document.querySelectorAll('.xp-vrow-shown')].map(li=>li.dataset.xpVersionNode);"
            # Back to the latest.
            "q('.xp-version-showing [data-xp-version-latest]').dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait(300);"
            "out.showingGone=!q('.xp-version-showing');"
            "out.contractBack=text(q('[data-xp-keep=\"contract:run_2\"]'));"
            "out.keptAfter=localStorage.getItem('pa.xp.version.run_2');")
        result = _render(self.engine, self._stub(), steps)
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        # A row per version, the newest on top; an edge to each from the one
        # before - the branch's a bend; the head marked.
        self.assertEqual(out["rows"], ["v4", "v3", "v2", "v1"])
        self.assertEqual(out["edges"], 3)
        self.assertEqual(out["dots"], 4)
        self.assertEqual(out["branchBends"], 1)
        self.assertEqual(out["head"], ["v4"])
        # Its message's title - whole on hover - and its files counted; one
        # saved before messages: its label, and no counts.
        self.assertEqual(out["v2"], "Write the notes' first draft and the outline it follows")
        self.assertEqual(out["v2Full"], out["v2"])
        self.assertEqual(out["v2Files"], "+2 ~1 −0")
        self.assertEqual(out["v1"], "The contract approved")
        self.assertFalse(out["v1Files"])
        self.assertTrue(out["boxFixed"].startswith("auto:"), out["boxFixed"])
        self.assertIn("THE CONTRACT IN FORCE", out["contractBefore"])
        # Shown as it stood at v2: the line on top, its contract, its message,
        # files, and slides.
        self.assertIn("Showing v2", out["showing"])
        self.assertIn("Write the notes' first draft", out["showing"])
        self.assertIn("back to latest (head v4)", out["showing"])
        self.assertIn("THE CONTRACT AT V2", out["contractAfter"])
        self.assertIn("not in force", out["contractAfter"])
        self.assertIn("Write the notes' first draft and the outline it follows", out["message"])
        self.assertEqual(out["body"], ["notes.md: a first draft of every section",
                                       "outline.md: the order"])
        for said in ("Added (2)", "notes.md", "outline.md", "Changed (1)", "README.md"):
            self.assertIn(said, out["fileLists"])
        self.assertIn("Files it holds (2)", out["view"])
        self.assertTrue(out["frame"])
        self.assertEqual(out["shownRow"], ["v2"])
        self.assertEqual(out["kept"], "v2")
        self.assertEqual(out["gets"], 1)
        # Continue from it: offered there, the words asked there.
        self.assertTrue(out["fromHere"])
        self.assertTrue(out["words"])
        self.assertTrue(out["focused"])
        self.assertIn("from version v2", out["continueRow"])
        # Kept as the panel is asked again - not drawn again; a new version
        # added in place, the one shown still shown.
        self.assertEqual(out["stillShown"], ["v2"])
        self.assertTrue(out["sameRun"])
        self.assertEqual(out["rowsAfter"], ["v5", "v4", "v3", "v2", "v1"])
        self.assertEqual(out["headAfter"], ["v5"])
        self.assertTrue(out["sameRunAfter"])
        self.assertEqual(out["shownAfter"], ["v2"])
        # Back to the latest: the contract in force again, the choice let go.
        self.assertTrue(out["showingGone"])
        self.assertIn("THE CONTRACT IN FORCE", out["contractBack"])
        self.assertIsNone(out["keptAfter"])


class StartedBeforeItsPlanTests(_PanelBase):

    def test_the_settings_first_and_start_while_the_context_is_collected(self) -> None:
        # The user's settings are there from the start, the context below
        # them as it comes; Start may be pressed before the plan is drafted.
        stub = {"/api/work/limits": {"defaults": {"minutes": 45, "tokens_in": 8000000,
                                                  "tokens_out": 400000, "calls": 200}},
                "/api/work/drafts": {"draft": {
                    "id": "draft_9", "status": "collecting", "unit_id": "", "source": "todo",
                    "text": "Reply to the club", "folders": [], "window": {}, "context": {},
                    "plan": {}, "options": {"todo": "t1"}}},
                "/api/work/drafts/start": {"starting": True, "draft": "draft_9",
                                           "when_ready": True},
                "/api/settings/choose-place": {"selected": "D:/work/club/notes.md",
                                               "kind": "file"}}
        steps = (
            "openDelegateDraft('draft_9');await wait(1500);"
            "const order=Array.from(document.querySelector('#delegate-form').children).map(e=>e.id||e.className);"
            "out.order=order;"
            "out.settings=Boolean(document.querySelector('#delegate-settings input[name=\"delegate-way\"][value=\"auto\"]:checked'));"
            "out.suggested=text(document.querySelector('#delegate-way-suggested'));"
            "out.waiting=text(document.querySelector('#delegate-body'));"
            "out.folders=text(document.querySelector('#delegate-settings #delegate-folders'));"
            # one button: whatever was selected last - a file here - is added, said so
            "click(document.querySelector('#delegate-add-folder'));await wait(300);"
            "out.added=text(document.querySelector('#delegate-folders ul'));"
            # the header stays in sight as the box is scrolled
            "const form=document.querySelector('#delegate-form');form.scrollTop=400;await wait(100);"
            "out.headerTop=Math.round(document.querySelector('#delegate-title').getBoundingClientRect().top"
            "-form.getBoundingClientRect().top);out.scrolled=form.scrollTop;"
            "out.startOn=!document.querySelector('#delegate-start').disabled"
            "&&!document.querySelector('#delegate-start').hidden;"
            "const m=document.querySelector('[data-delegate-limit=\"minutes\"]');m.value='12';"
            "m.dispatchEvent(new Event('change',{bubbles:true}));"
            # a poll redraws the wait - the settings stay as changed
            "await wait(1200);out.kept=document.querySelector('[data-delegate-limit=\"minutes\"]').value;"
            "click(document.querySelector('#delegate-start'));await wait(400);"
            "out.closed=!document.querySelector('#delegate').open;"
            "out.started=window.__posts.filter(p=>p.p==='/api/work/drafts/start').map(p=>p.body);")
        result = _render(self.engine, stub, steps)
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        self.assertEqual(out["order"][:4], ["delegate-title", "delegate-head",
                                            "delegate-settings", "delegate-body"])
        self.assertTrue(out["settings"])
        self.assertEqual(out["suggested"], "not drafted yet")
        self.assertIn("Collecting the context", out["waiting"])
        self.assertTrue(out["startOn"])
        self.assertEqual(out["kept"], "12")
        self.assertTrue(out["closed"])
        start = out["started"][0]
        # Where it works, chosen before any plan, with the settings.
        self.assertIn("Add work directory", out["folders"])
        self.assertNotIn("Add a file", out["folders"], "one button for a folder or a file")
        self.assertIn("file D:/work/club/notes.md", out["added"])
        self.assertGreater(out["scrolled"], 0)
        self.assertLessEqual(abs(out["headerTop"]), 2, "Delegate work stays at the top")
        self.assertIn("Nothing given", out["folders"])
        self.assertEqual((start["id"], start["plan"]), ("draft_9", {"way": "auto"}))
        self.assertEqual(start["options"]["limits"]["minutes"], 12)
        self.assertEqual(start["folders"], ["D:/work/club/notes.md"])

    def test_no_create_a_draft_saved_and_a_box_that_floats(self) -> None:
        stub = {"/api/work/limits": {"defaults": {"minutes": 45}},
                "/api/work/drafts": {"draft": {
                    "id": "draft_7", "status": "collecting", "unit_id": "", "source": "menu",
                    "text": "Reply to the club", "folders": [], "window": {}, "context": {},
                    "plan": {}, "options": {"way": "quick", "limits": {"minutes": 5}}}},
                "/api/work/drafts/save": {"draft": {"id": "draft_7"}},
                "/api/work/drafts/saved": {"drafts": [{
                    "id": "draft_6", "status": "ready", "plan": {"title": "Club reply"},
                    "options": {"saved": True}, "updated_at": "2026-10-26T10:00:00"}]}}
        steps = (
            "openDelegateDraft('draft_7');await wait(1500);"
            "const dialog=document.querySelector('#delegate');"
            "out.create=Boolean(document.querySelector('#delegate-create'));"
            "out.save=!document.querySelector('#delegate-save').hidden;"
            "out.modal=dialog.matches(':modal');"
            # a saved draft opens with what it was saved with
            "out.way=(document.querySelector('input[name=\"delegate-way\"]:checked')||{}).value;"
            "out.minutes=document.querySelector('[data-delegate-limit=\"minutes\"]').value;"
            "const title=document.querySelector('#delegate-title');"
            "const r=title.getBoundingClientRect();"
            "title.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0,clientX:r.left+5,clientY:r.top+5}));"
            "document.dispatchEvent(new PointerEvent('pointermove',{bubbles:true,clientX:r.left+5-200,clientY:r.top+5+100}));"
            "document.dispatchEvent(new PointerEvent('pointerup',{bubbles:true}));"
            "const after=dialog.getBoundingClientRect();"
            "out.moved=[Math.round(after.left-(r.left-(title.offsetLeft||0))),Math.round(after.top)];"
            "out.movedLeft=Math.round(r.left-after.left)>150;"
            "click(document.querySelector('#delegate-save'));await wait(400);"
            "out.closed=!dialog.open;"
            # opened again - a saved draft - and cancelled: closed, kept
            "STUB['/api/work/drafts'].draft.options.saved=true;"
            "openDelegateDraft('draft_7');await wait(1500);"
            "click(document.querySelector('#delegate-cancel'));await wait(300);"
            "out.cancelClosed=!dialog.open;"
            "out.cancelled=window.__posts.filter(p=>p.p==='/api/work/drafts/cancel').length;"
            "out.saved=window.__posts.filter(p=>p.p==='/api/work/drafts/save').map(p=>p.body);"
            "await xpLoadDrafts();out.drafts=text(document.querySelector('#xp-drafts'));")
        result = _render(self.engine, stub, steps)
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        self.assertFalse(out["create"])
        self.assertTrue(out["save"])
        self.assertFalse(out["modal"], "the page behind stays to be read")
        self.assertEqual((out["way"], out["minutes"]), ("quick", "5"))
        self.assertTrue(out["movedLeft"], out["moved"])
        self.assertTrue(out["closed"])
        self.assertTrue(out["cancelClosed"])
        self.assertEqual(out["cancelled"], 0, "Cancel leaves a saved draft as it was saved")
        saved = out["saved"][0]
        self.assertEqual((saved["id"], saved["options"]["way"], saved["options"]["limits"]),
                         ("draft_7", "quick", {"minutes": 5}))
        self.assertIn("Drafts", out["drafts"])
        self.assertIn("Club reply", out["drafts"])
        self.assertIn("Remove", out["drafts"])

    def test_start_is_not_offered_while_its_folder_is_in_use(self) -> None:
        said = ("the folder D:/work/club is being worked on by 'Reply to the club' - one "
                "piece of work at a time in a folder: wait until it ends, or stop it")
        stub = {"/api/work/limits": {"defaults": {"minutes": 45}},
                "/api/work/drafts": {"draft": {
                    "id": "draft_8", "status": "collecting", "unit_id": "", "source": "todo",
                    "text": "Reply again", "folders": ["D:/work/club"], "window": {},
                    "context": {}, "plan": {}, "options": {}}},
                "/api/work/folders/held": {"held": {"folder": "D:/work/club",
                                                    "by": "Reply to the club", "run": "r1"},
                                           "said": said}}
        steps = (
            "openDelegateDraft('draft_8');await wait(1500);"
            "out.disabled=document.querySelector('#delegate-start').disabled;"
            "const note=document.querySelector('#delegate-folder-held');"
            "out.note=note&&!note.hidden?text(note):'';")
        result = _render(self.engine, stub, steps)
        self.assertEqual(result["err"], "", result["err"])
        self.assertTrue(result["out"]["disabled"])
        self.assertIn("Start waits:", result["out"]["note"])
        self.assertIn("Reply to the club", result["out"]["note"])


if __name__ == "__main__":
    unittest.main()
