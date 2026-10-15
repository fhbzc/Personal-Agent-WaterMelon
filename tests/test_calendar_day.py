"""THE CALENDAR, A DAY HOUR BY HOUR: as a calendar's day view is - a row
each half hour, each event a block at its time as long as it lasts - and an
event added by clicking the time it is at; the month as before, a day of it
opened by double-clicking."""

from __future__ import annotations

import html
import json
import re
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from test_panels import _javascript_engine

WEB = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"


def _render(engine, tasks, steps):
    page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                  (WEB / "index.html").read_text(encoding="utf-8"))
    stub = {"/api/tasks": {"tasks": tasks}, "/api/settings": {"values": {}, "schema": []}}
    harness = (
        "<style>" + (WEB / "styles.css").read_text(encoding="utf-8") + "</style>"
        "<script>try{localStorage.clear();}catch(e){}"
        "window.__posts=[];const STUB=" + json.dumps(stub) + ";"
        "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
        "if(o&&o.method==='POST')window.__posts.push({p,body:JSON.parse(o.body||'{}')});"
        "return Promise.resolve({ok:true,status:200,statusText:'OK',"
        "headers:{get:()=>null},json:()=>Promise.resolve(STUB[p]||{})});};</script>"
        "<script>" + (WEB / "app.js").read_text(encoding="utf-8") + "</script>"
        "<script>window.addEventListener('load',async()=>{let err='';const out={};"
        "const wait=(ms)=>new Promise(r=>setTimeout(r,ms||150));"
        "const click=(el)=>el.dispatchEvent(new MouseEvent('click',{bubbles:true}));"
        "try{" + steps + "}catch(e){err=String(e&&e.stack?e.stack:e);}"
        "document.title=JSON.stringify({err,out});});</script>")
    with tempfile.TemporaryDirectory() as work:
        probe = Path(work) / "probe.html"
        probe.write_text(page + harness, encoding="utf-8")
        done = subprocess.run(
            [engine, "--headless=new", "--disable-gpu", "--no-first-run",
             f"--user-data-dir={work}/profile", "--virtual-time-budget=10000",
             "--window-size=1300,900", "--dump-dom", probe.as_uri()],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    found = re.findall(r"<title>(.*?)</title>", done.stdout or "", re.S)
    found = [one for one in found if one.startswith("{")]
    assert found, (done.stdout or "")[:300]
    return json.loads(html.unescape(found[-1]))


class TheDayViewTests(unittest.TestCase):

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def test_a_day_hour_by_hour_and_an_event_added_at_a_time(self) -> None:
        # Today at 10:00 local time, for an hour and a half.
        local = datetime.now().astimezone()
        ten = local.replace(hour=10, minute=0, second=0, microsecond=0)
        task = {"id": "task_1", "title": "Talk", "status": "pending",
                "scheduled_at": ten.astimezone(timezone.utc).isoformat(),
                "execution_type": "notification", "runs": 0,
                "task_payload": {"notify_user": True, "duration_minutes": 90},
                "metadata": {}}
        steps = (
            "click(document.querySelector('#tabs button[data-tab=\"calendar\"]'));await wait();"
            "click(document.querySelector('[data-cal-view=\"day\"]'));await wait(300);"
            "const block=document.querySelector('.cal-event');"
            "out.block=block?[block.style.top,block.style.height]:null;"
            "out.slots=document.querySelectorAll('[data-cal-slot]').length;"
            "out.dayActive=document.querySelector('[data-cal-view=\"day\"]').classList.contains('active');"
            # 15:00: slot 30 of the day.
            "const slot=document.querySelectorAll('[data-cal-slot]')[30];"
            "click(slot);await wait();"
            "const form=document.querySelector('.cal-add-form');"
            "out.formTime=form?form.elements.time.value:null;"
            "form.elements.title.value='Dentist';form.elements.minutes.value='45';"
            "form.requestSubmit();await wait(300);"
            "out.posted=window.__posts.filter(x=>x.p==='/api/tasks').map(x=>x.body);"
            # Back to the month; a day double-clicked opens it.
            "click(document.querySelector('[data-cal-view=\"month\"]'));await wait(300);"
            "const cell=document.querySelectorAll('.cal-day')[10];"
            "cell.dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));await wait(300);"
            "out.backToDay=!!document.querySelector('.cal-dayview');")
        result = _render(self.engine, [task], steps)
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        self.assertTrue(out["dayActive"])
        self.assertEqual(out["slots"], 48)
        # 10:00 is the 20th half hour; 90 minutes is three rows.
        self.assertEqual(out["block"], ["440px", "64px"])
        self.assertEqual(out["formTime"], "15:00")
        self.assertEqual(len(out["posted"]), 1)
        posted = out["posted"][0]
        self.assertEqual(posted["title"], "Dentist")
        self.assertEqual(posted["payload"], {"duration_minutes": 45})
        self.assertTrue(posted["notify"])
        starts = datetime.fromisoformat(posted["scheduled_at"].replace("Z", "+00:00")).astimezone()
        self.assertEqual((starts.hour, starts.minute), (15, 0))
        self.assertTrue(out["backToDay"])


if __name__ == "__main__":
    unittest.main()
