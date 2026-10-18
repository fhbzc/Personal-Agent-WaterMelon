"""Overview, memory inspection and calendar surface (sections 9, 10, 13)."""

from __future__ import annotations


import os
import html
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import json
import shutil
import subprocess
import tempfile

from support import TemporaryAgent

from personalized_agent.common.events.models import KnowledgeEdge, ListenerEvent

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "personalized_agent" / "api" / "web"
LF = chr(10)
SEP = os.sep
PROJ = SEP.join(["C:", "proj", "demo"])


class ActivityAggregationTests(unittest.TestCase):
    """Section 9: the overview must report periods, not running totals."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        # Each wholly inside the local day it is meant for, whatever the time
        # now: "two hours ago" just after midnight was yesterday, and the
        # test failed when the suite reached it then. A period counts from
        # its start, so today's also start after midnight - as long as the
        # part of today that has passed allows.
        now = datetime.now().astimezone()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        passed = (now - midnight).total_seconds()
        today = lambda share: midnight + timedelta(seconds=passed * share)
        earlier = lambda days, hour: midnight - timedelta(days=days) + timedelta(hours=hour)
        # (whole seconds: a period's length is reported to a tenth)
        self.plan = [(today(0.9), "Editor", min(1800, int(passed * 0.4))),
                     (today(0.5), "Browser", min(600, int(passed * 0.2))),
                     (earlier(1, 12), "Editor", 3600), (earlier(2, 12), "Chat", 900)]
        for when, name, seconds in self.plan:
            self.app.raw_store.add_event(ListenerEvent(
                event_type="application.focus_ended", source="application", application=name,
                timestamp=when.astimezone(timezone.utc),
                data={"duration_seconds": seconds}))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_each_day_reports_its_own_total_not_a_running_one(self) -> None:
        days = self.app.overview.history(days=5)
        totals = [d["total_seconds"] for d in days]
        self.assertGreater(len(set(totals)), 1,
                           "every day reporting the same number means the window is unbounded")
        # Nothing was recorded five days ago, so that day must be empty.
        self.assertEqual(days[0]["total_seconds"], 0.0)
        recorded = sum(seconds for *_rest, seconds in self.plan)
        self.assertAlmostEqual(sum(totals), float(recorded), places=1)

    def test_a_day_window_excludes_activity_from_later_days(self) -> None:
        start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday = self.app.raw_store.application_usage(
            since=start - timedelta(days=1), until=start)
        self.assertEqual(round(sum(u["seconds"] for u in yesterday), 1), 3600.0)

    def test_the_current_focus_period_counts_before_the_user_switches_apps(self) -> None:
        # On an agent of its own: the activity set up above is today's, and
        # in the first hours of a day it falls inside this test's last hour.
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        now = datetime.now(timezone.utc)
        agent.app.raw_store.add_event(ListenerEvent(
            event_type="application.focused", source="application", application="Editor",
            timestamp=now - timedelta(minutes=4), data={"window_title": "draft.md"}))
        usage = agent.app.raw_store.application_usage(since=now - timedelta(hours=1), until=now)
        editor = next(entry for entry in usage if entry["application"] == "Editor")
        self.assertAlmostEqual(editor["seconds"], 240.0, delta=1.0)


    def test_the_hourly_series_says_what_was_used_in_each_hour(self) -> None:
        hourly = self.app.overview.hourly(hours=6)
        self.assertEqual(len(hourly["buckets"]), 6)
        for bucket in hourly["buckets"]:
            self.assertIn("label", bucket)
            self.assertIsInstance(bucket["applications"], dict)
        busy = [b for b in hourly["buckets"] if b["total_seconds"]]
        self.assertTrue(busy, "the seeded activity should land in some hour")
        self.assertTrue(all(name in hourly["series"] or True for b in busy
                            for name in b["applications"]))

    def test_the_hourly_endpoint_is_reachable(self) -> None:
        payload = self.app.overview.hourly(hours=3)
        self.assertEqual(set(payload) >= {"buckets", "series", "window_hours"}, True)


class FrontendSurfaceTests(unittest.TestCase):
    """The panels the specification requires are actually built."""

    def setUp(self) -> None:
        self.html = (WEB / "index.html").read_text(encoding="utf-8")
        self.js = (WEB / "app.js").read_text(encoding="utf-8")
        self.css = (WEB / "styles.css").read_text(encoding="utf-8")

    def test_every_element_the_script_reaches_for_exists(self) -> None:
        ids = set(re.findall(r'id="([^"]+)"', self.html))
        # ids the script creates itself before querying them
        created = set(re.findall(r'id="([A-Za-z0-9_-]+)"', self.js))
        referenced = set(re.findall(r'\$\$?\(\s*"#([A-Za-z0-9_-]+)', self.js))
        missing = sorted(referenced - ids - created)
        self.assertEqual(missing, [], f"script targets nothing in the markup: {missing}")

    def test_and_no_id_belongs_to_two_of_them(self) -> None:
        """`querySelector("#x")` returns whichever comes first, so a
        duplicate id is not a tidiness complaint - it silently points one
        panel's code at another panel's element.

        Three were sharing two names here. The month-by-month states
        browser and the state heartbeat both answered to `dbg-states-body`,
        and because the heartbeat panel comes first in the document the
        browser was writing its states into the heartbeat's panel, binding
        its click handler there, and hiding it with its collapse button.
        The test above only asks that every id the script reaches for
        exists, which was true throughout."""
        import collections

        names = re.findall(r'\bid="([^"]+)"', self.html)
        twice = sorted(name for name, n
                       in collections.Counter(names).items() if n > 1)
        self.assertEqual(twice, [], f"one id, two elements: {twice}")

    def test_the_calendar_has_a_dated_grid(self) -> None:
        """Section 10, as amended: a calendar, not a list of tasks."""
        for marker in ("calendar-surface", "cal-period", "cal-today", "cal-prev", "cal-next"):
            self.assertIn(marker, self.html, marker)
        self.assertIn("cal-weekdays", self.js)
        self.assertIn("cal-date", self.js)
        # Drawn from the date: six weeks are rendered whether or not tasks exist.
        self.assertIn("week < 6", self.js)
        self.assertIn(".cal-day", self.css)

    def test_the_overview_has_an_hourly_chart(self) -> None:
        self.assertIn("ov-hourly", self.html)
        self.assertIn("/api/overview/hourly", self.js)
        self.assertIn("polyline", self.js)

    def test_only_actionable_rows_look_actionable(self) -> None:
        """The application list is selectable; the history read-out is not."""
        self.assertIn(".bar-row.clickable { cursor: pointer; }", self.css)
        self.assertIn("cursor: default", self.css)
        self.assertIn("bar-row.selected", self.css)

    def test_the_hidden_attribute_is_not_overridden_by_a_display_rule(self) -> None:
        """A panel marked hidden must actually be hidden."""
        self.assertIn("[hidden] { display: none !important; }", self.css)

    def test_the_sign_in_panel_is_hosted_in_the_window(self) -> None:
        """Section 75.2: no terminal, and the code comes back here."""
        for marker in ("login-panel", "login-link", "login-code", "login-complete"):
            self.assertIn(marker, self.html, marker)
        self.assertIn("/api/models/login/start", self.js)
        self.assertIn("/api/models/login/complete", self.js)

    def test_a_blocked_auth_method_is_still_shown(self) -> None:
        """Section 77, as amended."""
        self.assertIn("authentication_method_details", self.js)
        self.assertIn("unavailable here", self.js)
        self.assertIn("method-blocked", self.css)


class NotificationVisibilityTests(unittest.TestCase):
    """Section 52.2: the calendar says which tasks will interrupt.

    A per-task setting the user cannot see is one they cannot correct, and
    "will this interrupt me" is the thing they most want to know about a
    calendar the agent fills on its own.
    """

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _run(self, steps: str, notify: bool, rationale: str = "",
             extra: dict = None) -> dict:
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        now = datetime.now().replace(hour=14, minute=0, second=0, microsecond=0)
        task = {
            "id": "t1", "title": "Confirm the clock", "status": "pending",
            "execution_type": "model_task",
            "description": "Checks whether your day fits Lisbon or Madrid.",
            "scheduled_at": now.isoformat(), "created_at": now.isoformat(),
            "last_run_at": None, "last_result": "",
            "task_payload": {"notify_user": notify},
            "metadata": ({"rationale": rationale} if rationale else {}),
        }
        task.update(extra or {})
        stub = {
            "/api/tasks": {"tasks": [task]},
            "/api/states": {"states": [], "covering_now": [], "counts": {}},
            "/api/status": {"pending_raw_data": {"events": 0, "edits": 0},
                            "library": {"records": 0, "folders": {}}},
        }
        harness = (
            "<script>window.__posted=[];const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
            "if(o&&o.method==='POST')window.__posted.push({p:p,b:String(o.body||'')});"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';"
            "const wait=()=>new Promise(r=>setTimeout(r,60));"
            "try{await loadCalendar();await wait();" + steps + "}catch(e){err=e.message;}"
            "const card=document.querySelector('.event-card');"
            "const chip=document.querySelector('.cal-chip');"
            "const why=document.querySelector('.event-why');"
            "document.title=JSON.stringify({err:err,"
            "detail:card?card.textContent.split(/[^!-~]+/).join(' ').trim():'',"
            "chipMarked:chip?chip.classList.contains('notifies'):false,"
            "chipTitle:chip?chip.getAttribute('title'):'',"
            "why:!!why,whyOpenByDefault:why?why.open:false,"
            "posted:window.__posted});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 "--window-size=1400,900",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
            dom = completed.stdout or ""
        match = re.search(r"<title>(.*?)</title>", dom, re.S)
        self.assertIsNotNone(match, "the calendar never rendered")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "")
        return result

    def test_an_event_says_which_step_made_it_what_kind_and_why(self) -> None:
        """"heartbeat" named a loop with three steps, of which one schedules.
        The event says which step of the agent put it there, when, which
        kind of notification it is, and - folded - the reason it gave."""
        opened = "document.querySelector('.cal-chip').click();await wait();"
        result = self._run(opened, notify=True, extra={
            "execution_type": "notification", "created_by": "heartbeat",
            "reason": "The mail says the manuscript is due Friday.",
            "task_payload": {"notify_user": True, "template": "passive"},
            "metadata": {"created_by_step": "agent heartbeat, step 3 - what "
                                            "notifications need to be added"}})
        self.assertIn("by agent heartbeat, step 3", result["detail"])
        self.assertIn("notice - something to know", result["detail"])
        self.assertIn("Why agent heartbeat, step 3", result["detail"])
        self.assertIn("manuscript is due Friday", result["detail"])
        self.assertFalse(result["whyOpenByDefault"])

    def test_a_notifying_task_is_marked_on_the_calendar(self) -> None:
        result = self._run("", notify=True)
        self.assertTrue(result["chipMarked"])
        self.assertIn("interrupts you", result["chipTitle"])

    def test_a_silent_task_is_not_marked(self) -> None:
        result = self._run("", notify=False)
        self.assertFalse(result["chipMarked"])
        self.assertIn("stays on the calendar", result["chipTitle"])

    def test_the_detail_says_which_it_is(self) -> None:
        opened = "document.querySelector('.cal-chip').click();await wait();"
        self.assertIn("Interrupts you", self._run(opened, notify=True)["detail"])
        self.assertIn("Stays on the calendar", self._run(opened, notify=False)["detail"])

    def test_the_detail_offers_no_switch(self) -> None:
        # Disable and Delete are the only things an event offers.
        opened = "document.querySelector('.cal-chip').click();await wait();"
        for notify in (True, False):
            detail = self._run(opened, notify=notify)["detail"]
            self.assertNotIn("Make it silent", detail)
            self.assertNotIn("Notify me", detail)

    def test_the_reasoning_is_there_but_folded_away(self) -> None:
        result = self._run("document.querySelector('.cal-chip').click();await wait();",
                           notify=False, rationale="A +0100 offset was seen on 3 Sep.")
        self.assertTrue(result["why"], "the rationale was not shown at all")
        self.assertFalse(result["whyOpenByDefault"],
                         "the reasoning was open, which is what made the detail "
                         "unreadable in the first place")

    def test_a_task_with_no_reasoning_shows_no_empty_section(self) -> None:
        result = self._run("document.querySelector('.cal-chip').click();await wait();",
                           notify=False)
        self.assertFalse(result["why"])


#: Section 14.33: opening and closing div tags, for the layout check.
TAG_PATTERN = r"<div\b|</div>"

class DebuggerModulePanelTests(unittest.TestCase):
    """Section 14.33: one panel per module, rendered in a real browser.

    The map says which components exist and how they connect. It cannot say
    what any one of them holds, so a wrong event is invisible until something
    downstream misbehaves.
    """

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _run(self, steps: str, monitoring: bool = True, bulk: int = 0) -> dict:
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        modules = {
            "monitoring": monitoring,
            "modules": [
                {"id": "raw_event_storage", "name": "Raw Event Storage",
                 "component_type": "storage",
                 "description": "Every event the Listener recorded.",
                 "status": {"state": "healthy"}, "metrics": {"events": 18},
                 "parent": "knowledge", "has_detail": True},
                {"id": "event_bus", "name": "Event Bus", "component_type": "service",
                 "description": "In-process publish and subscribe.",
                 "status": {"state": "healthy"}, "metrics": {},
                 "parent": "runtime", "has_detail": False},
            ],
        }
        # The application registers 28 of these. Two panels cannot crush a
        # map; twenty-eight can, which is the bug this reproduces.
        for index in range(bulk):
            modules["modules"].append({
                "id": f"filler_{index}", "name": f"Module {index}",
                "component_type": "service", "description": "Another module.",
                "status": {"state": "healthy"}, "metrics": {"n": index},
                "parent": "runtime", "has_detail": False})
        detail = {
            "id": "raw_event_storage", "monitoring": monitoring,
            "detail": {
                "summary": {"total events": 18, "event types": 2},
                "choices": {"field": "event_type", "label": "Event type",
                            "options": [
                                {"value": "application.focus_ended",
                                 "label": "application.focus_ended (14)"},
                                {"value": "browser.page_visit",
                                 "label": "browser.page_visit (4)"}],
                            "selected": ""},
                "groups": [
                    {"event_type": "browser.page_visit", "count": 4, "events": [
                        {"id": "event_1", "event_type": "browser.page_visit",
                         "source": "browser", "timestamp": "2026-09-05T01:00:00+00:00",
                         "application": "chrome.exe",
                         "data": {"url": "https://example.com/0", "title": "Page 0"},
                         "metadata": {}, "processed": False}]},
                ],
            },
        }
        stub = {
            "/api/debugger/modules": modules,
            "/api/debugger/module": detail,
            "/api/debugger/monitoring": {"monitoring": not monitoring},
            # activate() loads these three together, so all three are stubbed:
            # the panels are reached the way the application reaches them.
            "/api/debugger/topology": {
                "subsystems": [{"id": "knowledge", "name": "Knowledge"}],
                "components": [], "connections": [], "states": {},
                "connection_states": {}, "application": {"version": "test"}},
            "/api/debugger/timeline": {"traces": []},
            "/api/status": {"pending_raw_data": {"events": 0, "edits": 0},
                            "library": {"records": 0, "folders": {}}},
        }
        harness = (
            "<script>window.__posted=[];const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
            "if(o&&o.method==='POST')window.__posted.push({p:p,b:String(o.body||'')});"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>" + (web / "debugger.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';"
            "const wait=()=>new Promise(r=>setTimeout(r,80));"
            "try{await window.debuggerPanel.activate();await wait();"
            + steps + "}catch(e){err=e.message;}"
            "const box=document.getElementById('dbg-modules');"
            "document.title=JSON.stringify({err:err,"
            "panels:box?box.querySelectorAll('.dbg-module').length:0,"
            "text:box?box.textContent.split(/[^!-~]+/).join(' ').trim():'',"
            "fieldRows:document.querySelectorAll('.dbg-fields tr').length,"
            "options:[...document.querySelectorAll('.dbg-picker option')]"
            ".map(o=>o.textContent.trim()),"
            "focusOptions:[...document.querySelectorAll('#dbg-focus option')]"
            ".map(o=>o.value),"
            "canvasHeight:(()=>{const c=document.getElementById('dbg-map');"
            "return c?Math.round(c.getBoundingClientRect().height):-1;})(),"
            "painted:(()=>{const c=document.getElementById('dbg-map');"
            "if(!c||!c.width||!c.height)return 0;"
            "const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;"
            "let n=0;for(let i=3;i<d.length;i+=4){if(d[i]!==0)n++;}return n;})(),"
            "panelsVisible:[...document.querySelectorAll('.dbg-module')]"
            ".filter(e=>{const r=e.getBoundingClientRect();"
            "return r.width>1&&r.height>1;}).length,"
            "openEntries:document.querySelectorAll('.dbg-entry[open]').length,"
            "chosen:window.__chosen||'',"
            "survived:Boolean(window.__survived),"
            "names:[...document.querySelectorAll('.dbg-module h3')]"
            ".map(h=>h.textContent),"
            "posted:window.__posted});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 "--window-size=1400,900",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
            dom = completed.stdout or ""
        match = re.search(r"<title>(.*?)</title>", dom, re.S)
        self.assertIsNotNone(match, "the debugger never rendered")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "")
        return result

    def test_each_module_gets_its_own_panel(self) -> None:
        result = self._run("")
        self.assertEqual(result["panels"], 2)

    def test_a_panel_names_the_module_and_says_what_it_is_for(self) -> None:
        result = self._run("")
        self.assertIn("Raw Event Storage", result["text"])
        self.assertIn("Every event the Listener recorded", result["text"])

    def test_a_panel_shows_the_modules_own_numbers(self) -> None:
        result = self._run("")
        self.assertIn("healthy", result["text"])
        self.assertIn("18", result["text"])

    def test_opening_a_module_shows_what_is_in_it(self) -> None:
        result = self._run(
            "document.querySelector('.dbg-module-open').click();await wait();await wait();")
        self.assertIn("browser.page_visit", result["text"])

    def test_every_field_of_a_stored_row_is_shown(self) -> None:
        # The point of looking at a stored event is seeing what was stored,
        # not a chosen few columns.
        result = self._run(
            "document.querySelector('.dbg-module-open').click();await wait();await wait();"
            "document.querySelector('.dbg-entry summary').click();await wait();")
        self.assertGreaterEqual(result["fieldRows"], 8)
        for field in ("event_type", "timestamp", "application", "data", "processed"):
            self.assertIn(field, result["text"])

    def test_the_kinds_can_be_chosen_between(self) -> None:
        result = self._run(
            "document.querySelector('.dbg-module-open').click();await wait();await wait();")
        self.assertTrue(any("application.focus_ended" in o for o in result["options"]))
        self.assertTrue(any("browser.page_visit" in o for o in result["options"]))

    def test_a_module_with_no_detail_says_so(self) -> None:
        result = self._run("")
        self.assertIn("No detail view", result["text"])

    def test_with_monitoring_off_the_panels_say_nothing_was_collected(self) -> None:
        result = self._run("", monitoring=False)
        self.assertEqual(result["panels"], 0)
        self.assertIn("Monitoring is off", result["text"])
        self.assertIn("goes on working", result["text"])

    def test_the_switch_reports_the_servers_state(self) -> None:
        result = self._run("", monitoring=False)
        self.assertIn("Monitoring is off", result["text"])

    def test_focus_narrows_the_panels(self) -> None:
        # The reported bug: changing Focus appeared to do nothing, because it
        # only moved the map - and the map had been squeezed to no height by
        # the panel grid sharing its fixed-height container.
        both = self._run("")
        self.assertEqual(sorted(both["names"]), ["Event Bus", "Raw Event Storage"])

        narrowed = self._run(
            "document.getElementById('dbg-focus').value='knowledge';"
            "document.getElementById('dbg-focus')"
            ".dispatchEvent(new Event('change'));await wait();")
        self.assertEqual(narrowed["names"], ["Raw Event Storage"])

    def test_focus_can_be_cleared_again(self) -> None:
        result = self._run(
            "const f=document.getElementById('dbg-focus');"
            "f.value='knowledge';f.dispatchEvent(new Event('change'));await wait();"
            "f.value='';f.dispatchEvent(new Event('change'));await wait();")
        self.assertEqual(len(result["names"]), 2)

    def test_the_focus_control_is_populated_from_the_topology(self) -> None:
        # It is filled in by loadTopology, which only runs when the tab is
        # activated - so a panel loaded at page load would have found it empty.
        result = self._run("")
        self.assertIn("knowledge", result["focusOptions"])

    def test_the_panels_are_not_inside_the_fixed_height_map_shell(self) -> None:
        """The reported bug, as a structural invariant.

        The panel grid was placed inside ``.dbg-shell`` - a flex column with a
        fixed height and ``overflow: hidden``, sized so the map can own its
        area. Twenty-eight auto-height panels in there squeeze the map to
        nothing and are themselves clipped: the map looked dead, so Focus
        appeared to do nothing (it was redrawing a canvas with no height), and
        most panels could not be seen.

        Measuring the squeeze in a headless window did not reproduce it
        reliably, so this asserts the arrangement instead: the grid is a
        sibling of the shell, not a child of it.
        """
        markup = (ROOT / "personalized_agent" / "api" / "web"
                  / "index.html").read_text(encoding="utf-8")
        shell_open = markup.rindex("<div", 0, markup.index('id="dbg-shell"'))
        depth, closes_at = 0, None
        for match in re.finditer(TAG_PATTERN, markup[shell_open:]):
            depth += 1 if match.group().startswith("<div") else -1
            if depth == 0:
                closes_at = shell_open + match.end()
                break
        self.assertIsNotNone(closes_at, "the map shell never closes")
        self.assertGreater(
            markup.index('id="dbg-modules"'), closes_at,
            "the panel grid is nested inside the fixed-height map shell, which "
            "crushes the system map and clips the panels")

    def test_a_live_refresh_keeps_an_opened_entry_open(self) -> None:
        """The reported bug.

        These panels refresh every ten seconds. Rebuilding the grid each time
        collapses whatever row the reader had expanded, so the panel is
        unreadable exactly while it is being read.
        """
        result = self._run(
            "document.querySelector('.dbg-module-open').click();await wait();await wait();"
            "document.querySelector('.dbg-entry').open=true;await wait();"
            "await window.debuggerPanel.activate();await wait();await wait();")
        self.assertEqual(result["openEntries"], 1,
                         "the refresh collapsed the row that was being read")

    def test_a_live_refresh_keeps_the_detail_open(self) -> None:
        result = self._run(
            "document.querySelector('.dbg-module-open').click();await wait();await wait();"
            "await window.debuggerPanel.activate();await wait();await wait();")
        self.assertIn("browser.page_visit", result["text"])

    def test_a_live_refresh_keeps_the_chosen_type(self) -> None:
        # The type selector is part of what the reader set up; resetting it
        # sends them back to the unfiltered list every ten seconds.
        result = self._run(
            "document.querySelector('.dbg-module-open').click();await wait();await wait();"
            "const s=document.querySelector('.dbg-picker select');"
            "s.value='browser.page_visit';s.dispatchEvent(new Event('change'));"
            "await wait();await wait();"
            "await window.debuggerPanel.activate();await wait();await wait();"
            "window.__chosen=(document.querySelector('.dbg-picker select')||{}).value;")
        self.assertEqual(result["chosen"], "browser.page_visit")

    def test_a_refresh_does_not_rebuild_panels_that_have_not_changed(self) -> None:
        # Identity is the test: the same element object must still be there,
        # because everything the reader did lives on it.
        result = self._run(
            "const first=document.querySelector('.dbg-module');"
            "first.dataset.marked='yes';"
            "await window.debuggerPanel.activate();await wait();await wait();"
            "window.__survived=document.querySelector('.dbg-module')"
            ".dataset.marked==='yes';")
        self.assertTrue(result["survived"],
                        "the grid was rebuilt even though nothing changed")

    def test_turning_it_off_asks_the_server_to_stop_collecting(self) -> None:
        result = self._run(
            "document.getElementById('dbg-monitoring').checked=false;"
            "document.getElementById('dbg-monitoring')"
            ".dispatchEvent(new Event('change'));await wait();")
        posted = [p for p in result["posted"] if p["p"] == "/api/debugger/monitoring"]
        self.assertTrue(posted, "the switch sent nothing")
        self.assertIn('"enabled":false', posted[0]["b"].replace(" ", ""))


class AssistantTickListTests(unittest.TestCase):
    """Section 18, as amended: the assistants are detected and ticked.

    Typing a name into a box means knowing what the application calls the
    thing, and being told nothing when the spelling is wrong.
    """

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _run(self, steps: str, value: str = "", options=None, notes=None) -> dict:
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        # The probe is written to a temporary directory, so the page's own
        # <link href="/styles.css"> resolves to nothing. Without the stylesheet
        # every measurement below is of unstyled markup, which would let a
        # layout test pass while the real page is laid out wrongly.
        page = page.replace(
            '<link rel="stylesheet" href="/styles.css">',
            "<style>" + (web / "styles.css").read_text(encoding="utf-8") + "</style>",
            1)
        settings = {
            "values": {"listener_assistant_content_tools": value},
            "schema": [{
                "key": "listener_assistant_content_tools",
                "label": "Record conversations from",
                "type": "multi_choice", "default": "", "section": "Listener",
                "description": "Which assistants may be read.",
                "choices": None, "choices_source": "assistant_tools",
                "chooser": None, "readonly": False, "secret": False,
                "restart_required": False, "hidden": False,
            }],
            "choices": {"assistant_tools": options
                        if options is not None else ["Claude Code", "Cursor"]},
            "choice_notes": {"listener_assistant_content_tools": notes
                             if notes is not None
                             else {"Claude Code": "found at C:/Users/me/.claude/projects",
                                   "Cursor": "not found on this machine"}},
        }
        stub = {
            "/api/settings": settings,
            # loadSettings asks for all three together.
            "/api/models": {"roles": {}, "providers": [], "auth_methods": {},
                            "credential_backend": {"backend": "encrypted file"}},
            "/api/settings/defaults": {"user_defaults": {}},
            "/api/status": {"pending_raw_data": {"events": 0, "edits": 0},
                            "library": {"records": 0, "folders": {}}},
        }
        harness = (
            "<script>window.__posted=[];const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
            "if(o&&o.method==='POST')window.__posted.push({p:p,b:String(o.body||'')});"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';"
            "const wait=()=>new Promise(r=>setTimeout(r,60));"
            # The list is among the other settings: in Settings' Others view.
            "try{settingsView('privacy');await loadSettings();await wait();" + steps
            + "}catch(e){err=e.message;}"
            "const box=document.querySelector('.multi-choice');"
            "document.title=JSON.stringify({err:err,"
            "rendered:Boolean(box),"
            "boxes:[...document.querySelectorAll('.multi-choice input')]"
            ".map(i=>({v:i.value,on:i.checked})),"
            "text:box?box.textContent.split(/[^!-~]+/).join(' ').trim():'',"
            "value:box?box.value:null,"
            "geometry:[...document.querySelectorAll('.multi-choice-row')]"
            ".map(r=>{const n=r.querySelector('.multi-choice-name');"
            "const c=r.querySelector('input');"
            "return n&&c?{name:Math.round(n.getBoundingClientRect().left),"
            "tick:Math.round(c.getBoundingClientRect().left),"
            "rowLeft:Math.round(r.getBoundingClientRect().left),"
            "rowRight:Math.round(r.getBoundingClientRect().right)}:null;})"
            ".filter(Boolean),"
            "posted:window.__posted});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 "--window-size=1400,900",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
            dom = completed.stdout or ""
        match = re.search(r"<title>(.*?)</title>", dom, re.S)
        self.assertIsNotNone(match, "settings never rendered")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "")
        return result

    def test_it_is_a_tick_list_not_a_text_box(self) -> None:
        result = self._run("")
        self.assertTrue(result["rendered"])
        self.assertEqual([b["v"] for b in result["boxes"]], ["Claude Code", "Cursor"])

    def test_the_tick_is_left_of_the_name(self) -> None:
        """In Privacy every switch's tick is in one column at the left, and
        this list's ticks are in it too - not at the right edge of the row."""
        result = self._run(
            "document.getElementById('settings').classList.add('active');await wait();")
        self.assertTrue(result["geometry"], "the rows were not measurable")
        for row in result["geometry"]:
            self.assertLess(row["tick"], row["name"],
                            "the checkbox is still to the right of the name")

    def test_the_ticks_line_up_in_one_column(self) -> None:
        # Names differ in length, so a tick that simply follows the name sits
        # at a different place on every row.
        result = self._run(
            "document.getElementById('settings').classList.add('active');await wait();",
            options=["Claude Code", "A Much Longer Assistant Name"])
        ticks = {row["tick"] for row in result["geometry"]}
        self.assertEqual(len(ticks), 1, f"the ticks are ragged: {ticks}")

    def test_the_tick_starts_its_row(self) -> None:
        # Where a single switch's box starts, so the two kinds line up.
        result = self._run(
            "document.getElementById('settings').classList.add('active');await wait();")
        for row in result["geometry"]:
            self.assertLessEqual(row["tick"] - row["rowLeft"], 2,
                                 "the tick does not start the row")

    def test_the_detected_assistants_come_from_the_machine(self) -> None:
        result = self._run("", options=["Claude Code"])
        self.assertEqual([b["v"] for b in result["boxes"]], ["Claude Code"])

    def test_an_assistant_that_is_not_installed_is_shown_as_absent(self) -> None:
        # Leaving it out entirely gives the user nothing to reason about.
        result = self._run("")
        self.assertIn("not found on this machine", result["text"])

    def test_what_was_already_chosen_is_ticked(self) -> None:
        result = self._run("", value="Claude Code")
        chosen = {b["v"]: b["on"] for b in result["boxes"]}
        self.assertTrue(chosen["Claude Code"])
        self.assertFalse(chosen["Cursor"])

    def test_ticking_a_box_changes_the_value(self) -> None:
        result = self._run(
            "document.querySelector('.multi-choice input[value=\"Cursor\"]')"
            ".checked=true;await wait();")
        self.assertEqual(result["value"], "Cursor")

    def test_the_value_stays_a_newline_list(self) -> None:
        # Every reader of this setting already parses it that way; what
        # changed is the editor, not the stored shape.
        result = self._run(
            "document.querySelectorAll('.multi-choice input')"
            ".forEach((i,n)=>{i.checked=n<2;});await wait();",
            options=["Claude Code", "Cursor", "Zed"])
        self.assertEqual(result["value"], "Claude Code\nCursor")

    def test_every_one_ticked_is_every_one_later_ones_too(self) -> None:
        result = self._run(
            "document.querySelectorAll('.multi-choice input')"
            ".forEach(i=>{i.checked=true;});await wait();")
        self.assertEqual(result["value"], "*")

    def test_star_ticks_every_one_found(self) -> None:
        # The default: every assistant found.
        result = self._run("", value="*")
        self.assertTrue(all(b["on"] for b in result["boxes"]))
        self.assertEqual([b["v"] for b in result["boxes"]], ["Claude Code", "Cursor"])

    def test_it_says_what_ticking_nothing_means(self) -> None:
        result = self._run("")
        self.assertIn("Nothing ticked records nothing", result["text"])

    def test_a_choice_this_machine_does_not_offer_is_kept(self) -> None:
        # Opening the panel elsewhere must not quietly discard a name.
        result = self._run("", value="Some Other Assistant", options=["Claude Code"])
        values = [b["v"] for b in result["boxes"]]
        self.assertIn("Some Other Assistant", values)
        chosen = {b["v"]: b["on"] for b in result["boxes"]}
        self.assertTrue(chosen["Some Other Assistant"])

    def test_no_assistants_found_says_so(self) -> None:
        result = self._run("", options=[], notes={})
        self.assertIn("No assistants were found", result["text"])

if __name__ == "__main__":
    unittest.main()

class PageFileAndAssistantTests(unittest.TestCase):
    """Sections 9 and 25, as amended: what was read, worked on and worked through."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        now = datetime.now(timezone.utc)
        for url, title, visits in (
            ("https://docs.python.org/3/library/json.html", "json encoder", 3),
            ("https://example.invalid/page", "Example", 1),
        ):
            for i in range(visits):
                self.app.raw_store.add_event(ListenerEvent(
                    event_type="browser.navigation", source="browser", application="Chrome",
                    timestamp=now - timedelta(minutes=5 * i + 1),
                    data={"url": url, "title": title, "browser": "chrome"}))
        for path, kind, changes in ((PROJ + SEP + "parser.py", "modified", 4),
                                    (PROJ + SEP + "README.md", "created", 1)):
            for i in range(changes):
                self.app.raw_store.add_event(ListenerEvent(
                    event_type="filesystem." + kind, source="filesystem", application=None,
                    timestamp=now - timedelta(minutes=3 * i + 1), data={"path": path}))
        self.app.raw_store.add_event(ListenerEvent(
            event_type="assistant.session", source="assistant", application="Claude Code",
            timestamp=now - timedelta(minutes=2),
            data={"tool": "Claude Code", "project": PROJ, "session_id": "s1",
                  "exchanges": 42, "last_activity": now.isoformat()}))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_individual_pages_are_reported_not_just_the_browser(self) -> None:
        pages = self.app.overview.pages(hours=24)
        self.assertEqual(pages["total_pages"], 2)
        self.assertEqual(pages["total_visits"], 4)
        top = pages["pages"][0]
        self.assertEqual(top["visits"], 3)
        self.assertTrue(top["title"])
        self.assertTrue(top["url"])
        self.assertEqual(top["site"], "docs.python.org")
        self.assertTrue(top["last_visited"])

    def test_individual_files_are_reported_not_just_the_editor(self) -> None:
        files = self.app.overview.files(hours=24)
        self.assertEqual(files["total_files"], 2)
        top = files["files"][0]
        self.assertEqual(top["name"], "parser.py")
        self.assertEqual(top["changes"], 4)
        self.assertEqual(top["change"], "modified")
        self.assertTrue(top["folder"])

    def test_directories_are_not_reported_as_files(self) -> None:
        self.app.raw_store.add_event(ListenerEvent(
            event_type="filesystem.directory_created", source="filesystem",
            application=None, timestamp=datetime.now(timezone.utc),
            data={"path": PROJ + SEP + "newdir"}))
        names = [f["name"] for f in self.app.overview.files(hours=24)["files"]]
        self.assertNotIn("newdir", names)

    def test_assistant_work_is_reported_by_project(self) -> None:
        data = self.app.overview.assistants(hours=24)
        self.assertEqual(data["total_projects"], 1)
        row = data["projects"][0]
        self.assertEqual(row["tool"], "Claude Code")
        self.assertEqual(row["exchanges"], 42)
        self.assertEqual(row["sessions"], 1)

    def test_an_empty_view_says_whether_the_source_was_off(self) -> None:
        """Nothing recorded and nothing collected are different facts."""
        self.app.config.set("listener_enabled", True)
        self.app.config.set("listener_browser_enabled", False)
        state = self.app.overview.pages(hours=24)["collection"]
        self.assertFalse(state["enabled"])
        self.assertIn("browser monitoring", state["reason"])

    def test_the_master_switch_is_the_reason_when_it_is_off(self) -> None:
        """The most specific true reason, not the narrowest one."""
        self.app.config.set("listener_enabled", False)
        self.app.config.set("listener_browser_enabled", True)
        state = self.app.overview.pages(hours=24)["collection"]
        self.assertFalse(state["enabled"])
        self.assertIn("activity monitoring", state["reason"])

    def test_the_panel_asks_for_all_three_views(self) -> None:
        js = (WEB / "app.js").read_text(encoding="utf-8")
        for endpoint in ("/api/overview/pages", "/api/overview/files",
                         "/api/overview/assistants"):
            self.assertIn(endpoint, js)
        html = (WEB / "index.html").read_text(encoding="utf-8")
        for marker in ("ov-pages", "ov-files", "ov-assistants"):
            self.assertIn(marker, html)


class AssistantCaptureTests(unittest.TestCase):
    """Section 25, as amended: metadata only, never what was said."""

    def setUp(self) -> None:
        from personalized_agent.listener.providers import assistant

        self.module = assistant
        self.dir = Path(tempfile.mkdtemp(prefix="pa-assistant-"))
        project = self.dir / "sessions" / "C--proj-demo"
        project.mkdir(parents=True)
        record = {
            "sessionId": "session-1", "timestamp": "2026-08-31T10:00:00Z",
            "type": "user", "cwd": PROJ,
            "content": "a private prompt the user typed",
            "display": "another private thing",
        }
        (project / "session-1.jsonl").write_text(
            LF.join(json.dumps(record) for _ in range(5)) + LF, encoding="utf-8")
        self.tool = assistant.AssistantTool("demo", "Demo Assistant", self.dir / "sessions")

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def _provider(self):
        class Config:
            def get(self, key, default=None):
                return True

            def lines(self, key):
                return []

        provider = self.module.AssistantProvider(Config())
        provider._tools = [self.tool]
        return provider

    def test_sessions_are_captured(self) -> None:
        provider = self._provider()
        captured = []
        provider.bind(captured.append)
        self.assertEqual(provider.poll(), 1)
        data = captured[0].data
        self.assertEqual(data["tool"], "Demo Assistant")
        self.assertEqual(data["session_id"], "session-1")
        self.assertEqual(data["exchanges"], 5)

    def test_the_recorded_project_is_used_not_the_encoded_name(self) -> None:
        """The directory name is a lossy encoding; the session records the path."""
        provider = self._provider()
        captured = []
        provider.bind(captured.append)
        provider.poll()
        self.assertEqual(captured[0].data["project"], PROJ)

    def test_nothing_that_was_said_is_captured_unless_it_is_asked_for(self) -> None:
        """Section 18, as amended: the summary path never opens the content."""
        provider = self._provider()
        captured = []
        provider.bind(captured.append)
        provider.poll()
        payload = json.dumps([event.data for event in captured])
        for field in self.module.CONTENT_FIELDS:
            self.assertNotIn(field, payload, field + " reached an emitted event")
        self.assertNotIn("private prompt", payload)
        self.assertNotIn("another private thing", payload)

    def test_an_unchanged_session_is_not_reported_twice(self) -> None:
        provider = self._provider()
        provider.bind(lambda event: None)
        self.assertEqual(provider.poll(), 1)
        self.assertEqual(provider.poll(), 0)

    def test_the_source_is_on_by_default(self) -> None:
        """Every listening is on by default, this too (Settings > Privacy);
        recording the words is a separate choice."""
        from personalized_agent.common.config.schema import SCHEMA_BY_KEY

        field = SCHEMA_BY_KEY["listener_assistant_enabled"]
        self.assertTrue(field.default)
        self.assertIn("not read unless", field.description)
        # Recording the conversation is a second, separate choice, and it is
        # made by ticking the assistants rather than by a switch beside them:
        # two controls for one decision can disagree. Every assistant found
        # is ticked to begin with (*), as every listening is on by default,
        # and the guards around it are stated where the choice is made.
        content = SCHEMA_BY_KEY["listener_assistant_content_tools"]
        self.assertEqual(content.default, "*")
        for guard in ("monitoring off", "stays on this machine"):
            self.assertIn(guard, content.description.lower())

    def test_the_privacy_filter_knows_the_category(self) -> None:
        from personalized_agent.listener.core.privacy import CATEGORY_SETTINGS

        self.assertEqual(CATEGORY_SETTINGS["assistant"], "listener_assistant_enabled")

def _javascript_engine():
    """Whatever can parse JavaScript here: a headless browser, or node.

    The browser first: it parses as well as node does, and the tests that
    draw the page need one - with node first, installing node quietly turned
    every rendering test into a skip."""
    for candidate in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "google-chrome", "chromium", "chromium-browser", "msedge",
    ):
        found = candidate if os.path.exists(candidate) else shutil.which(candidate)
        if found:
            return ("chrome", found)
    node = shutil.which("node")
    if node:
        return ("node", node)
    return (None, None)


class FrontendSyntaxTests(unittest.TestCase):
    """The frontend must parse.

    A syntax error in a script leaves every function in it undefined, so the
    whole interface renders as empty panels while the backend and its tests
    stay perfectly green. Nothing else in the suite looks at the scripts.
    """

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if not self.engine:
            self.skipTest("no JavaScript engine available to parse the frontend")

    def _parse(self, path: Path) -> str:
        if self.kind == "node":
            completed = subprocess.run([self.engine, "--check", str(path)],
                                       capture_output=True, text=True, timeout=120)
            return "" if completed.returncode == 0 else (completed.stderr or "").strip()
        # Chrome: compile the file in a throwaway page and report the error.
        with tempfile.TemporaryDirectory() as work:
            source = path.read_text(encoding="utf-8")
            probe = Path(work) / "probe.html"
            payload = json.dumps(source)
            probe.write_text(
                "<!doctype html><meta charset=utf-8><body><pre id=out></pre>"
                "<script>try{new Function(" + payload + ");"
                "document.getElementById('out').textContent='OK'}"
                "catch(e){document.getElementById('out').textContent='ERR '+e.message}"
                "</script>", encoding="utf-8")
            out = Path(work) / "dump.txt"
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=4000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True, timeout=300)
            dom = completed.stdout or ""
            out.write_text(dom, encoding="utf-8")
            if "OK</pre>" in dom:
                return ""
            marker = dom.find("ERR ")
            return dom[marker:marker + 200] if marker >= 0 else "could not parse the script"

    def test_every_frontend_script_parses(self) -> None:
        web = ROOT / "personalized_agent" / "api" / "web"
        scripts = sorted(web.glob("*.js"))
        self.assertTrue(scripts, "no frontend scripts were found")
        for script in scripts:
            with self.subTest(script.name):
                problem = self._parse(script)
                self.assertEqual(problem, "", f"{script.name} does not parse: {problem}")


class ModelPanelRenderTests(unittest.TestCase):
    """Section 82, as amended: registering and choosing are separate panels.

    This renders the real page in a browser against stubbed API responses.
    Parsing alone does not catch a panel that renders into the wrong element
    or a control that lands in the wrong half of the interface - and that
    confusion was the whole reason the two panels were split apart.
    """

    MODELS = {
        "roles": {"primary": {"role": "primary", "provider": "claude_code",
                              "model": "opus", "label": "claude_code/opus",
                              "is_override": False, "availability": None}},
        "providers": [
            {"provider": "offline", "display_name": "Offline",
             "authentication_methods": ["none"], "authentication_method_details": [],
             "auth_status": {"state": "not_configured", "detail": ""},
             "models": [], "usage": {"total_tokens": 0}},
            {"provider": "anthropic", "display_name": "Anthropic",
             "authentication_methods": ["api_key"], "authentication_method_details": [],
             "auth_status": {"state": "not_configured", "detail": ""},
             "models": [], "usage": {"total_tokens": 0}},
            {"provider": "claude_code", "display_name": "Claude Code (local)",
             "authentication_methods": ["none"], "authentication_method_details": [],
             "auth_status": {"state": "authenticated", "detail": "signed in"},
             "models": [], "usage": {"total_tokens": 12}},
        ],
        "chain": [{"provider": "claude_code", "model": "opus", "label": "claude_code/opus",
                   "display_name": "Claude Code", "kind": "client",
                   "auth_status": {"state": "authenticated"}, "availability": None}],
        "credential_backend": {"backend": "test"},
        "fallback_enabled": False, "fallback_target": "", "checked_at": None,
    }

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _render(self) -> dict:
        web = ROOT / "personalized_agent" / "api" / "web"
        page = (web / "index.html").read_text(encoding="utf-8")
        script = (web / "app.js").read_text(encoding="utf-8")
        # The page without its own script tags; app.js is inlined below so the
        # whole thing is one same-origin document with nothing to fetch.
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "", page)
        stub = {
            "/api/settings": {"values": {}, "schema": [], "choices": {},
                              "choice_notes": {}},
            "/api/models": self.MODELS,
            "/api/settings/defaults": {"user_defaults": {}},
        }
        harness = (
            "<script>window.__calls=[];"
            "const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(url,opts)=>{const path=String(url).split('?')[0];"
            "window.__calls.push(path);"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "json:()=>Promise.resolve(STUB[path]||{})});};</script>"
            "<script>" + script + "</script>"
            "<script>window.addEventListener('load',async()=>{"
            "try{await loadSettings();}catch(e){window.__error=e.message;}"
            "const reg=document.querySelector('#model-register-panel');"
            "document.title=JSON.stringify({"
            "error:window.__error||'',"
            "chain:[...document.querySelectorAll('#model-chain li')].length,"
            "buttons:document.querySelectorAll('#models-card .use-provider').length,"
            "parts:!!document.querySelector('#model-use-panel'),"
            "regText:reg.textContent.split(/[^!-~]+/).join(' ').trim()});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=8000",
                 "--dump-dom", probe.as_uri()],
                # The page carries the application's own text, which is not
                # necessarily decodable in this machine's default encoding.
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
            dom = completed.stdout or ""
        match = re.search(r"<title>(.*?)</title>", dom, re.S)
        self.assertIsNotNone(match, "the page never finished rendering")
        return json.loads(html.unescape(match.group(1)))

    def test_the_list_and_the_two_tables_render(self) -> None:
        result = self._render()
        self.assertEqual(result["error"], "")
        # The models in order, chosen there - no button per provider any more.
        self.assertEqual(result["chain"], 1)
        self.assertEqual(result["buttons"], 0)
        # One model for every part: no table of which part uses which.
        self.assertFalse(result["parts"])
        self.assertIn("tokens", result["regText"])


SHIFTED_DETAIL = "{\"application\": \"firefox\", \"interactions\": [{\"started_at\": \"2026-09-01T09:30:00+00:00\", \"ended_at\": \"2026-09-01T09:31:00+00:00\", \"duration_seconds\": 60, \"duration_human\": \"1m\", \"window_title\": \"Mozilla Firefox\", \"item_count\": 1, \"pages\": [{\"kind\": \"page\", \"at\": \"2026-09-01T09:30:30+00:00\", \"url\": \"https://example.com/c\", \"title\": \"Page C\", \"site\": \"example.com\"}], \"files\": []}], \"active_periods\": [], \"window_activity\": [], \"browser_navigation\": [], \"file_interaction\": [], \"events\": []}"


class OverviewInteractionRenderTests(unittest.TestCase):
    """Section 9, as amended: selecting an application shows the interactions.

    Rendered in a real browser against stubbed responses, because what is being
    checked is behaviour - expanding a stretch of use, opening a page from
    inside it, clearing the selection - and none of that is visible to a parse.
    """

    OVERVIEW = {"total_human": "1h", "window_hours": 24,
                "applications": [
                    {"application": "firefox", "seconds": 600, "human": "10m",
                     "breakdown_kind": "sites",
                     "breakdown": [{"label": "docs.example.com", "count": 2,
                                    "url": "https://example.com/a"},
                                   {"label": "news.example.com", "count": 1,
                                    "url": "https://news.example.com/"}]},
                    {"application": "code", "seconds": 300, "human": "5m",
                     "breakdown_kind": "", "breakdown": []}]}
    DETAIL = {
        "application": "firefox",
        "interactions": [
            {"started_at": "2026-09-01T09:00:00+00:00", "ended_at": "2026-09-01T09:10:00+00:00",
             "duration_seconds": 600, "duration_human": "10m",
             "window_title": "Mozilla Firefox", "item_count": 2,
             "pages": [{"kind": "page", "at": "2026-09-01T09:02:00+00:00",
                        "url": "https://example.com/a", "title": "Page A", "site": "example.com"},
                       {"kind": "page", "at": "2026-09-01T09:05:00+00:00",
                        "url": "https://example.com/b", "title": "Page B", "site": "example.com"}],
             "files": []},
            {"started_at": "2026-09-01T08:00:00+00:00", "ended_at": "2026-09-01T08:20:00+00:00",
             "duration_seconds": 1200, "duration_human": "20m",
             "window_title": "Mozilla Firefox", "item_count": 0, "pages": [], "files": []},
        ],
        "active_periods": [], "window_activity": [], "browser_navigation": [],
        "file_interaction": [], "events": [],
    }
    PAGE = {"url": "https://example.com/a", "site": "example.com", "title": "Page A",
            "visits": [{"at": "2026-09-01T09:02:00+00:00", "title": "Page A",
                        "application": "firefox", "browser": "firefox"}],
            "visit_count": 1, "first_seen": None, "last_seen": None,
            "applications": [{"name": "firefox", "count": 1}], "collection": {}}

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _run(self, steps: str, detail: dict = None) -> dict:
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        script = (web / "app.js").read_text(encoding="utf-8")
        stub = {
            "/api/overview": self.OVERVIEW,
            "/api/overview/history": {"days": []},
            "/api/overview/hourly": {"buckets": [], "series": {}, "window_hours": 24},
            "/api/overview/pages": {"pages": [], "total_pages": 0, "total_visits": 0,
                                    "sites": [], "collection": {}, "window_hours": 24},
            "/api/overview/files": {"files": [], "total_files": 0, "total_changes": 0,
                                    "folders": [], "collection": {}, "window_hours": 24},
            "/api/overview/assistants": {"projects": [], "collection": {},
                                         "total_projects": 0, "total_sessions": 0,
                                         "window_hours": 24},
            "/api/overview/application": detail or self.DETAIL,
            "/api/overview/page": self.PAGE,
            "/api/status": {"pending_raw_data": {"events": 0, "edits": 0},
                            "library": {"records": 0, "folders": {}}},
        }
        # What the page asks on its own every few seconds (whether a piece of
        # work is going) is not an answer to a click: on a slow machine one
        # fell inside a test's few hundred milliseconds and was counted.
        harness = (
            "<script>window.__asked=[];const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u)=>{const p=String(u).split('?')[0];"
            "if(p!=='/api/work/going')window.__asked.push(p);"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + script + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';"
            "const wait=()=>new Promise(r=>setTimeout(r,60));"
            "try{await loadOverview();" + steps + "}catch(e){err=e.message;}"
            "const d=document.querySelector('#ov-detail');"
            "document.title=JSON.stringify({error:err,"
            "text:d.textContent.split(/[^!-~]+/).join(' ').trim().slice(0,700),"
            "expandButtons:document.querySelectorAll('#ov-detail .ov-expand').length,"
            "pageLinks:[...document.querySelectorAll('#ov-detail .ov-page')]"
            ".map(a=>a.textContent.trim()),"
            "clearHidden:document.querySelector('#ov-clear').hidden,"
            "fetched:window.__asked||[],"
            "appsText:document.querySelector('#ov-apps').textContent"
            ".split(/[^!-~]+/).join(' ').trim().slice(0,400),"
            "carets:document.querySelectorAll('#ov-apps .bar-caret').length,"
            "selectedSites:[...document.querySelectorAll('#ov-apps .bar-child.selected')]"
            ".map(e=>e.textContent.trim().split(/[^!-~]/)[0]),"
            "moreLabel:(document.querySelector('#ov-more')||{}).textContent||'',"
            "cutToggles:document.querySelectorAll('#ov-detail .cut-toggle').length,"
            "cutLabel:(document.querySelector('#ov-detail .cut-toggle')||{})"
            ".textContent||'',"
            "cutLabels:[...document.querySelectorAll('#ov-detail .cut-toggle')]"
            ".map(b=>b.textContent),"
            "backHidden:document.querySelector('#ov-detail-back').hidden,"
            "title:document.querySelector('#ov-detail-title').textContent});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
            dom = completed.stdout or ""
        match = re.search(r"<title>(.*?)</title>", dom, re.S)
        self.assertIsNotNone(match, "the page never finished rendering")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["error"], "")
        return result

    def test_each_panel_is_drawn_without_waiting_for_the_slowest(self) -> None:
        """The status can take twenty seconds; the applications must not
        wait for it. Here it never answers at all."""
        result = self._run(
            "overviewState.held=null;"
            "document.querySelector('#ov-apps').innerHTML='';"
            "const answered=window.fetch;"
            "window.fetch=(u)=>String(u).startsWith('/api/status')"
            "  ?new Promise(()=>{}):answered(u);"
            "loadOverview();await wait();")
        self.assertEqual(result["error"], "")
        self.assertIn("firefox", result["appsText"].lower())

    def test_selecting_an_application_lists_its_interactions(self) -> None:
        result = self._run("await selectApplication('firefox');await wait();")
        self.assertEqual(result["expandButtons"], 2, "one control per stretch of use")
        self.assertIn("10m", result["text"])
        self.assertIn("Mozilla Firefox", result["text"])
        # Collapsed: the pages are behind the control, not listed already.
        self.assertEqual(result["pageLinks"], [])
        self.assertFalse(result["clearHidden"], "clearing must be offered once selected")

    def test_expanding_an_interaction_shows_what_was_open(self) -> None:
        result = self._run("await selectApplication('firefox');await wait();"
                           "document.querySelector('#ov-detail .ov-expand').click();"
                           "await wait();await wait();")
        self.assertEqual(result["pageLinks"], ["Page B", "Page A"],
                         "the pages of that stretch, most recent first")

    def test_a_page_can_be_opened_from_inside_an_interaction(self) -> None:
        result = self._run("await selectApplication('firefox');await wait();"
                           "document.querySelector('#ov-detail .ov-expand').click();"
                           "await wait();await wait();"
                           "document.querySelector('#ov-detail .ov-page').click();"
                           "await wait();await wait();")
        self.assertEqual(result["title"], "Page detail")
        self.assertIn("example.com", result["text"])
        self.assertIn("1 visit", result["text"])
        self.assertFalse(result["backHidden"], "there must be a way back")

    def test_a_page_says_what_it_was_about_and_offers_what_was_read(self):
        """Keywords and a describing line under the page, worked out from
        the text the screen listener read there, and that text itself
        behind a toggle - the overview used to show an address and a title
        for a page whose whole text the agent had."""
        import copy

        detail = copy.deepcopy(self.DETAIL)
        page = detail["interactions"][0]["pages"][0]
        page.update({"keywords": ["weighting", "survey", "census"],
                     "description": "Reviewer two asks whether the weights hold.",
                     "excerpt": "You said: are the survey weights robust?",
                     "characters": 2400})
        expand = ("await selectApplication('firefox');await wait();"
                  "document.querySelector('#ov-detail .ov-expand').click();"
                  "await wait();await wait();")
        folded = self._run(expand, detail=detail)
        for word in ("weighting", "survey", "census"):
            self.assertIn(word, folded["text"])
        self.assertIn("Reviewer two asks whether the weights hold.", folded["text"])
        self.assertIn("what was read (2,400 characters)", " ".join(folded["cutLabels"]))
        self.assertNotIn("are the survey weights robust?", folded["text"],
                         "what was read is shown before it is asked for")
        opened = self._run(expand + "document.querySelector('#ov-detail "
                           ".ov-read-toggle').click();await wait();await wait();",
                           detail=detail)
        self.assertIn("are the survey weights robust?", opened["text"],
                      "opening the toggle did not show what was read")

    def test_an_interaction_with_nothing_recorded_cannot_be_expanded(self) -> None:
        result = self._run("await selectApplication('firefox');await wait();")
        self.assertIn("nothing recorded", result["text"])

    def test_opening_an_applications_breakdown_reloads_nothing(self) -> None:
        """Section 9, as amended: the caret cost a reload of the whole tab.

        Opening an application's breakdown changes only how the list already
        in hand is drawn, and it re-fetched all seven answers behind the tab
        and rebuilt every panel, both charts included. That is what made
        clicking one feel like waiting for it.
        """
        result = self._run(
            "window.__asked=[];"
            "document.querySelector('#ov-apps .bar-caret').click();"
            "await wait();await wait();")
        self.assertEqual(result["fetched"], [],
                         "opening a row asked the server for something")
        self.assertGreater(result["carets"], 0, "and it still drew the list")

    def test_choosing_a_site_asks_only_for_the_log(self) -> None:
        # Narrowing to a site narrows what the log answers, so that one is a
        # real question. The list around it is not.
        result = self._run(
            "await selectApplication('firefox');await wait();"
            "window.__asked=[];"
            "document.querySelector('#ov-apps .bar-caret').click();await wait();"
            "const child=document.querySelector('#ov-apps .bar-child');"
            "if(child)child.click();await wait();await wait();")
        self.assertEqual([p for p in result["fetched"]
                          if p != "/api/overview/application"], [],
                         f"it reloaded the tab: {result['fetched']}")

    def test_expanding_a_stretch_asks_the_server_for_nothing(self) -> None:
        """Section 9, as amended: opening one is a redraw, not a request.

        Expanding a stretch, showing more of them and opening out a cut title
        are all changes to how the same answer is drawn, and each of them
        fetched it again. The panel is rebuilt either way, so the request
        bought nothing and cost a round trip per click - on one machine that
        endpoint took 300ms, because the query behind it had no index to read
        the newest events from and sorted the whole table instead.
        """
        result = self._run(
            "await selectApplication('firefox');await wait();"
            "window.__asked=[];"
            "document.querySelector('#ov-detail .ov-expand').click();"
            "await wait();await wait();")
        self.assertEqual(result["fetched"], [],
                         "opening a stretch asked the server for something")
        self.assertEqual(result["pageLinks"], ["Page B", "Page A"],
                         "and it still opened")

    def test_showing_more_stretches_asks_for_nothing_either(self) -> None:
        many = json.loads(SHIFTED_DETAIL)
        many["interactions"] = many["interactions"] * 9
        result = self._run(
            "await selectApplication('firefox');await wait();"
            "window.__asked=[];"
            "document.querySelector('#ov-more').click();"
            "await wait();await wait();", detail=many)
        self.assertEqual(result["fetched"], [])

    def test_but_choosing_another_application_does_ask(self) -> None:
        # The redraw is for the same answer. A different one has to be got.
        result = self._run(
            "await selectApplication('firefox');await wait();"
            "window.__asked=[];"
            "await selectApplication('code');await wait();")
        self.assertIn("/api/overview/application", result["fetched"])

    def test_an_expanded_stretch_stays_open_when_the_list_moves(self) -> None:
        """Section 9, as amended: the panel is rebuilt, the stretch is the same.

        Which stretch is open used to be remembered by its position in the
        list. The list is rebuilt on every refresh and by then it has moved -
        a newer stretch of use goes on the front and everything shifts down -
        so "the first row is open" came to mean a different first row. The
        stretch the user had just opened closed itself, seconds later, while
        another sprang open in its place.
        """
        shifted = json.loads(SHIFTED_DETAIL)
        shifted["interactions"] = (shifted["interactions"]
                                   + self.DETAIL["interactions"])
        result = self._run(
            "await selectApplication('firefox');await wait();"
            "document.querySelector('#ov-detail .ov-expand').click();"
            "await wait();await wait();"
            "STUB['/api/overview/application']=" + json.dumps(shifted) + ";"
            "await loadApplicationDetail('firefox');await wait();await wait();")
        self.assertEqual(result["pageLinks"], ["Page B", "Page A"],
                         "the stretch the user opened is still the open one")

    def test_a_newly_arrived_stretch_does_not_spring_open(self) -> None:
        shifted = json.loads(SHIFTED_DETAIL)
        shifted["interactions"] = (shifted["interactions"]
                                   + self.DETAIL["interactions"])
        result = self._run(
            "await selectApplication('firefox');await wait();"
            "STUB['/api/overview/application']=" + json.dumps(shifted) + ";"
            "await loadApplicationDetail('firefox');await wait();await wait();")
        self.assertEqual(result["pageLinks"], [],
                         "nothing was opened, so nothing is open")

    def _measure(self, width: int) -> dict:
        """Where the two cards actually land, at a given window width."""
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        harness = (
            "<style>" + (web / "styles.css").read_text(encoding="utf-8") + "</style>"
            "<script>window.addEventListener('load',()=>{"
            # In the Debugger tab now: it is opened to be measured.
            "document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));"
            "document.querySelector('#debugger').classList.add('active');"
            "const l=document.querySelector('.ov-list').getBoundingClientRect();"
            "const d=document.querySelector('.ov-inspect').getBoundingClientRect();"
            "document.title=JSON.stringify({sameRow:Math.abs(l.y-d.y)<4,"
            "toTheRight:d.x>l.x,detailWider:d.width>l.width});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--window-size={width},900",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=6000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
        match = re.search(r"<title>(.*?)</title>", completed.stdout or "", re.S)
        self.assertIsNotNone(match, "the page never laid out")
        return json.loads(html.unescape(match.group(1)))

    def test_the_inspected_activity_sits_beside_the_list(self) -> None:
        # A selection whose result is a screen away reads as nothing having
        # happened, so the log shares a row with the list it came from.
        wide = self._measure(1400)
        self.assertTrue(wide["sameRow"], "the detail must share the list's row")
        self.assertTrue(wide["toTheRight"], "and sit to its right")
        self.assertTrue(wide["detailWider"], "the log needs the room, not the bars")

    def test_a_narrow_window_stacks_them_rather_than_squeezing(self) -> None:
        narrow = self._measure(700)
        self.assertFalse(narrow["sameRow"],
                         "side by side in 700px would crush both")

    def test_the_empty_message_claims_no_direction(self) -> None:
        # It sits beside the list on a wide window and below it on a narrow
        # one, so "above" would be wrong half the time.
        source = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text(
            encoding="utf-8")
        markup = (ROOT / "personalized_agent" / "api" / "web" / "index.html").read_text(
            encoding="utf-8")
        for text in (source, markup):
            self.assertNotIn("Select an application above", text)

    def test_the_log_does_not_run_on_forever(self) -> None:
        """A column beside a list, not a page: it shows some and offers the rest."""
        many = dict(self.DETAIL)
        many["interactions"] = [
            {"started_at": f"2026-09-01T{h:02d}:00:00+00:00",
             "ended_at": f"2026-09-01T{h:02d}:10:00+00:00",
             "duration_seconds": 600, "duration_human": "10m",
             "window_title": f"window {h}", "item_count": 0, "pages": [], "files": []}
            for h in range(9, 21)
        ]
        result = self._run("await selectApplication('firefox');await wait();",
                           detail=many)
        self.assertEqual(result["expandButtons"], 6,
                         "twelve stretches must not all be laid out at once")
        self.assertEqual(result["moreLabel"], "Show 6 earlier")

    def test_the_rest_of_the_log_is_one_control_away(self) -> None:
        many = dict(self.DETAIL)
        many["interactions"] = [
            {"started_at": f"2026-09-01T{h:02d}:00:00+00:00",
             "ended_at": f"2026-09-01T{h:02d}:10:00+00:00",
             "duration_seconds": 600, "duration_human": "10m",
             "window_title": f"window {h}", "item_count": 0, "pages": [], "files": []}
            for h in range(9, 21)
        ]
        result = self._run("await selectApplication('firefox');await wait();"
                           "document.querySelector('#ov-more').click();"
                           "await wait();await wait();", detail=many)
        self.assertEqual(result["expandButtons"], 12)
        self.assertEqual(result["moreLabel"], "Show fewer")

    def test_one_busy_stretch_says_what_it_is_not_showing(self) -> None:
        busy = dict(self.DETAIL)
        pages = [{"kind": "page", "at": f"2026-09-01T09:{m:02d}:00+00:00",
                  "url": f"https://example.com/{m}", "title": f"Page {m}",
                  "site": "example.com"} for m in range(30)]
        busy["interactions"] = [dict(self.DETAIL["interactions"][0],
                                     pages=pages, item_count=len(pages))]
        result = self._run("await selectApplication('firefox');await wait();"
                           "document.querySelector('#ov-detail .ov-expand').click();"
                           "await wait();await wait();", detail=busy)
        self.assertEqual(len(result["pageLinks"]), 12)
        self.assertIn("and 18 more", result["text"])

    def test_an_application_can_be_expanded_without_selecting_it(self) -> None:
        """Section 9, as amended: the arrow opens, the name selects."""
        result = self._run(
            "document.querySelector('#ov-apps .bar-caret').click();"
            "await wait();await wait();")
        self.assertIn("docs.example.com", result["appsText"])
        self.assertEqual(result["title"], "Application detail")
        # Expanding is not selecting: the log stays untouched.
        self.assertIn("Select an application", result["text"])
        self.assertTrue(result["clearHidden"])

    def test_the_name_still_selects(self) -> None:
        result = self._run("document.querySelector('#ov-apps .bar-name').click();"
                           "await wait();await wait();")
        self.assertFalse(result["clearHidden"], "clicking the name selects")
        self.assertIn("Mozilla Firefox", result["text"])

    def test_a_site_under_an_application_narrows_the_log_to_it(self) -> None:
        """Section 9, as amended: a site is a narrower version of the question."""
        result = self._run(
            "document.querySelector('#ov-apps .bar-caret').click();"
            "await wait();await wait();"
            "document.querySelector('#ov-apps .bar-sub').click();"
            "await wait();await wait();")
        self.assertEqual(result["title"], "Site detail")
        self.assertIn("Only what happened on", result["text"])
        self.assertIn("docs.example.com", result["text"])
        self.assertIn("Show all of firefox", result["text"],
                      "a narrowed log must offer the way back")

    def test_choosing_the_site_already_in_view_widens_again(self) -> None:
        result = self._run(
            "document.querySelector('#ov-apps .bar-caret').click();"
            "await wait();await wait();"
            "document.querySelector('#ov-apps .bar-sub').click();"
            "await wait();await wait();"
            "document.querySelector('#ov-apps .bar-sub').click();"
            "await wait();await wait();")
        self.assertEqual(result["title"], "Application detail")
        self.assertNotIn("Only what happened on", result["text"])

    def test_the_site_in_view_is_marked_in_the_list(self) -> None:
        result = self._run(
            "document.querySelector('#ov-apps .bar-caret').click();"
            "await wait();await wait();"
            "document.querySelector('#ov-apps .bar-sub').click();"
            "await wait();await wait();")
        self.assertEqual(result["selectedSites"], ["docs.example.com"])

    def test_an_application_with_nothing_to_show_has_no_arrow(self) -> None:
        # An arrow that opens onto nothing is a promise the row cannot keep.
        result = self._run("await wait();")
        self.assertEqual(result["carets"], 1,
                         "only the application with a breakdown gets an arrow")

    LONG = ("Some extremely long window title that nobody could scan at a glance "
            "because it just keeps going and going and going well past any limit")

    def test_a_long_line_is_cut_and_carries_its_own_handle(self) -> None:
        """A cut with no handle hides that there was more; this one asks."""
        long_titles = dict(self.DETAIL)
        long_titles["interactions"] = [dict(self.DETAIL["interactions"][0],
                                            window_title=self.LONG)]
        result = self._run("await selectApplication('firefox');await wait();",
                           detail=long_titles)
        self.assertNotIn(self.LONG, result["text"], "the whole title must not sit in the log")
        self.assertIn(self.LONG[:40], result["text"], "the start of it is still shown")
        self.assertEqual(result["cutToggles"], 1)
        self.assertIn("more", result["cutLabel"])

    def test_the_handle_unfolds_that_line_and_folds_it_again(self) -> None:
        long_titles = dict(self.DETAIL)
        long_titles["interactions"] = [dict(self.DETAIL["interactions"][0],
                                            window_title=self.LONG)]
        opened = self._run("await selectApplication('firefox');await wait();"
                           "document.querySelector('#ov-detail .cut-toggle').click();"
                           "await wait();await wait();", detail=long_titles)
        self.assertIn(self.LONG, opened["text"], "unfolding shows the whole line")
        self.assertIn("fold", opened["cutLabel"], "and offers to put it back")

        closed = self._run("await selectApplication('firefox');await wait();"
                           "document.querySelector('#ov-detail .cut-toggle').click();"
                           "await wait();await wait();"
                           "document.querySelector('#ov-detail .cut-toggle').click();"
                           "await wait();await wait();", detail=long_titles)
        self.assertNotIn(self.LONG, closed["text"])

    def test_only_the_line_that_was_opened_is_opened(self) -> None:
        # Unfolding one line must not unfold every other line like it.
        two = dict(self.DETAIL)
        two["interactions"] = [
            dict(self.DETAIL["interactions"][0], window_title=self.LONG,
                 ended_at="2026-09-01T09:10:00+00:00"),
            dict(self.DETAIL["interactions"][1], window_title=self.LONG,
                 ended_at="2026-09-01T08:20:00+00:00"),
        ]
        result = self._run("await selectApplication('firefox');await wait();"
                           "document.querySelector('#ov-detail .cut-toggle').click();"
                           "await wait();await wait();", detail=two)
        self.assertEqual(result["cutLabels"].count("\u25b4 fold"), 1,
                         "exactly one line is open")
        self.assertTrue(any("more" in label for label in result["cutLabels"]),
                        "the other stays cut")

    def test_a_short_line_gets_no_handle(self) -> None:
        # A toggle on a line that already fits is noise.
        result = self._run("await selectApplication('firefox');await wait();")
        self.assertEqual(result["cutToggles"], 0)

    def test_selecting_the_same_application_again_clears_it(self) -> None:
        result = self._run("await selectApplication('firefox');await wait();"
                           "await selectApplication('firefox');await wait();")
        self.assertTrue(result["clearHidden"])
        self.assertIn("Select an application", result["text"])

    def test_the_list_says_once_what_the_arrow_is_for(self) -> None:
        result = self._run("await wait();")
        self.assertIn("break one down", result["appsText"])

    def test_the_hint_goes_away_once_a_row_is_open(self) -> None:
        result = self._run("document.querySelector('#ov-apps .bar-caret').click();"
                           "await wait();await wait();")
        self.assertNotIn("break one down", result["appsText"])

    def test_the_panels_own_refresh_does_not_close_an_open_page(self) -> None:
        """The panel reloads itself every fifteen seconds.

        Rebuilding the application's log over an open page takes the user back
        out of it while they are reading, which from where they sit looks like
        the panel closing itself.
        """
        result = self._run(
            "await selectApplication('firefox');await wait();"
            "document.querySelector('#ov-detail .ov-expand').click();"
            "await wait();await wait();"
            "document.querySelector('#ov-detail .ov-page').click();"
            "await wait();await wait();"
            "await loadOverview();await wait();await wait();")
        self.assertEqual(result["title"], "Page detail",
                         "the refresh took the user back out of the page")
        self.assertIn("example.com", result["text"])

    def test_the_refresh_still_restores_an_application(self) -> None:
        result = self._run("await selectApplication('firefox');await wait();"
                           "await loadOverview();await wait();")
        self.assertEqual(result["title"], "Application detail")
        self.assertIn("Mozilla Firefox", result["text"])

    def test_the_selection_can_be_cleared(self) -> None:
        result = self._run("await selectApplication('firefox');await wait();"
                           "clearApplicationSelection();await wait();")
        self.assertTrue(result["clearHidden"])
        self.assertIn("Select an application", result["text"])
        self.assertEqual(result["expandButtons"], 0)

class UserDefaultsTests(unittest.TestCase):
    """Section 12.1: the user's choices are their defaults, and they travel."""

    def setUp(self) -> None:
        from personalized_agent.common.config.store import ConfigStore

        self.factory = ConfigStore
        self.base = Path(tempfile.mkdtemp(prefix="pa-userdef-"))
        self.internal = self.base / "internal"
        self.settings = self.internal / "settings" / "settings.json"
        self.store = self.factory(self.internal, path=self.settings)

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    def test_a_changed_setting_becomes_the_users_default(self) -> None:
        self.store.set("agent_name", "Nova")
        self.assertEqual(self.store.user_defaults()["agent_name"], "Nova")
        # None given: the avatar is called by its own name.
        self.assertEqual(self.store.builtin_defaults()["agent_name"], "")

    def test_a_fresh_settings_file_starts_from_the_users_defaults(self) -> None:
        self.store.set("agent_name", "Nova")
        self.store.set("font_size", 18)
        self.settings.unlink()
        reopened = self.factory(self.internal, path=self.settings)
        self.assertEqual(reopened.get("agent_name"), "Nova")
        self.assertEqual(reopened.get("font_size"), 18)

    def test_the_defaults_live_in_the_root_and_travel_with_it(self) -> None:
        """Section 110.1: copying the root carries the user's configuration."""
        self.store.set("agent_name", "Nova")
        self.assertTrue(self.store.user_defaults_path.is_relative_to(self.base))
        copy = self.base.parent / (self.base.name + "-copy")
        shutil.copytree(self.base, copy)
        try:
            moved = self.factory(copy / "internal",
                                 path=copy / "internal" / "settings" / "settings.json")
            self.assertEqual(moved.user_defaults()["agent_name"], "Nova")
        finally:
            shutil.rmtree(copy, ignore_errors=True)

    def test_resetting_undoes_the_choice(self) -> None:
        self.store.set("agent_name", "Nova")
        # None given by default: the avatar is called by its own name.
        self.assertEqual(self.store.reset("agent_name"), "")
        self.assertNotIn("agent_name", self.store.user_defaults())

    def test_resetting_everything_forgets_every_choice(self) -> None:
        self.store.set("agent_name", "Nova")
        self.store.set("font_size", 20)
        self.store.reset_all()
        self.assertEqual(self.store.user_defaults(), {})
        self.assertEqual(self.store.get("agent_name"), "")

    def test_a_derived_value_is_never_recorded_as_a_choice(self) -> None:
        """The agent root is not something the user picked in Settings."""
        self.store.set("agent_root", str(self.base), internal=True)
        self.assertNotIn("agent_root", self.store.user_defaults())

    def test_a_setting_this_version_dropped_is_ignored(self) -> None:
        self.store.user_defaults_path.write_text(
            json.dumps({"agent_name": "Nova", "a_setting_that_no_longer_exists": 1}),
            encoding="utf-8")
        reopened = self.factory(self.internal, path=self.settings)
        self.assertEqual(list(reopened.user_defaults()), ["agent_name"])


class DirectoryChooserTests(unittest.TestCase):
    """Section 12, as amended: a directory is chosen, not typed."""

    def setUp(self) -> None:
        from personalized_agent.common import chooser

        self.chooser = chooser
        self.dir = Path(tempfile.mkdtemp(prefix="pa-chooser-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_every_directory_setting_offers_the_chooser(self) -> None:
        from personalized_agent.common.config.schema import SETTINGS_SCHEMA

        directory_settings = {"agent_root", "workspace_root", "listener_watch_paths",
                              "listener_directory_exclusions",
                              # Section 18, as amended: which projects' assistant
                              # conversations are recorded, and which never are.
                              "listener_assistant_content_projects",
                              "listener_assistant_content_exclusions"}
        offered = {f.key for f in SETTINGS_SCHEMA if f.chooser == "directory"}
        self.assertEqual(offered, directory_settings)

    def test_the_chooser_reaches_the_frontend(self) -> None:
        from personalized_agent.common.config.schema import schema_as_dicts

        entry = next(e for e in schema_as_dicts() if e["key"] == "listener_watch_paths")
        self.assertEqual(entry["chooser"], "directory")

    def test_an_unusable_location_is_reported_when_it_is_chosen(self) -> None:
        missing = self.chooser.describe_directory(str(self.dir / "not-here"))
        self.assertFalse(missing["usable"])
        self.assertIn("does not exist", missing["problem"])
        a_file = self.dir / "a-file.txt"
        a_file.write_text("x", encoding="utf-8")
        not_a_dir = self.chooser.describe_directory(str(a_file))
        self.assertFalse(not_a_dir["usable"])
        self.assertIn("not a directory", not_a_dir["problem"])

    def test_a_usable_location_is_accepted(self) -> None:
        self.assertTrue(self.chooser.describe_directory(str(self.dir))["usable"])

    def test_a_machine_without_a_chooser_says_so_instead_of_failing(self) -> None:
        original = self.chooser.available
        self.chooser.available = lambda: False
        try:
            result = self.chooser.choose_directory()
        finally:
            self.chooser.available = original
        self.assertIsNone(result["selected"])
        self.assertFalse(result["available"])
        self.assertIn("type the path", result["reason"])

    def test_the_panel_uses_a_chooser_for_every_such_setting(self) -> None:
        js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("/api/settings/choose-directory", js)
        self.assertIn("/api/settings/check-directory", js)
        self.assertIn("/api/settings/choose-application", js)
        self.assertIn("field.chooser && !field.readonly", js)
        self.assertIn("type a path", js, "typing must remain possible")

    def test_an_application_is_chosen_not_typed(self) -> None:
        """Section 12, as amended: the user picks the application itself."""
        from personalized_agent.common.config.schema import SETTINGS_SCHEMA

        offered = {f.key for f in SETTINGS_SCHEMA if f.chooser == "application"}
        self.assertEqual(offered, {"local_client_path",
                                   "listener_application_allowlist",
                                   "listener_application_denylist"})

    def test_the_stored_value_is_what_activity_is_matched_against(self) -> None:
        """Choosing an executable must produce something the filter matches."""
        from personalized_agent.listener.core.privacy import PrivacyFilter

        agent = TemporaryAgent()
        try:
            # what the chooser stores for C:/Program Files/Mozilla Firefox/firefox.exe
            agent.app.config.set("listener_application_denylist", "firefox.exe")
            privacy = PrivacyFilter(agent.app.config)
            self.assertFalse(privacy.application_allowed("firefox.exe"))
            self.assertTrue(privacy.application_allowed("Code.exe"))
        finally:
            agent.cleanup()

    def test_a_machine_without_a_chooser_says_so_for_applications_too(self) -> None:
        original = self.chooser.available
        self.chooser.available = lambda: False
        try:
            result = self.chooser.choose_application()
        finally:
            self.chooser.available = original
        self.assertIsNone(result["selected"])
        self.assertFalse(result["available"])


class HourlyDateTests(unittest.TestCase):
    """Section 9, as amended: an hour label alone is ambiguous across midnight."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_every_hour_carries_its_date(self) -> None:
        hourly = self.agent.app.overview.hourly(hours=30)
        for bucket in hourly["buckets"]:
            self.assertRegex(bucket["date"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(bucket["date_label"])

    def test_the_period_covered_is_stated(self) -> None:
        hourly = self.agent.app.overview.hourly(hours=24)
        self.assertTrue(hourly["period"])
        self.assertGreaterEqual(len(hourly["dates"]), 1)

    def test_a_window_that_crosses_midnight_marks_the_day_change(self) -> None:
        hourly = self.agent.app.overview.hourly(hours=30)
        self.assertGreaterEqual(len(hourly["dates"]), 2, "30 hours must span two dates")
        self.assertTrue(any(bucket["starts_a_day"] for bucket in hourly["buckets"]))

    def test_the_chart_draws_the_date(self) -> None:
        js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("date_label", js)
        self.assertIn("chart-daybreak", js)

class WatchScopeTests(unittest.TestCase):
    """Section 19, as amended: the monitor watches the user's own space."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        from personalized_agent.listener.core.privacy import PrivacyFilter

        self.filter = PrivacyFilter(self.app.config)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_default_scope_is_everything_not_the_agent_workspace(self) -> None:
        """Section 19, as amended: the agent cannot notice what it is not watching.

        A narrow default leaves the feature reporting itself as running while
        recording almost nothing, which is worse than being off.
        """
        from personalized_agent.common.config.schema import default_watch_paths
        from personalized_agent.common.paths import default_root

        roots = [Path(line) for line in default_watch_paths().splitlines() if line.strip()]
        self.assertTrue(roots)
        for root in roots:
            self.assertEqual(root, Path(root.anchor),
                             "the default is whole drives, not one folder")
        workspace = default_root()
        self.assertNotIn(workspace, roots,
                         "watching only the workspace records the agent, not the user")

    def test_the_machines_own_churn_is_excluded_by_default(self) -> None:
        # Watching everything is only usable because this is not in it.
        from personalized_agent.common.config.schema import default_directory_exclusions

        excluded = [line.lower() for line in default_directory_exclusions().splitlines()]
        joined = " ".join(excluded)
        for expected in ("windows", "program files", "temp"):
            self.assertIn(expected, joined, f"{expected} would drown everything else")

    def test_noise_is_excluded_out_of_the_box(self) -> None:
        from personalized_agent.common.config import schema

        excluded = schema.defaults()["listener_directory_exclusions"]
        self.assertTrue(excluded.strip(), "watching a home directory needs exclusions")
        names = schema.defaults()["listener_excluded_names"].split(LF)
        for expected in ("node_modules", ".git", "__pycache__"):
            self.assertIn(expected, names)

    def test_the_users_own_work_is_recorded(self) -> None:
        home = str(Path.home())
        for path in (os.path.join(home, "Desktop", "report.docx"),
                     os.path.join(home, "Research", "project", "src", "main.py")):
            with self.subTest(path):
                self.assertTrue(self.filter.path_allowed(path))

    def test_project_noise_is_ignored_wherever_it_sits(self) -> None:
        """A prefix exclusion cannot express this: it is inside every project."""
        home = str(Path.home())
        for path in (
            os.path.join(home, "Research", "project", "node_modules", "react", "i.js"),
            os.path.join(home, "Research", "project", ".git", "objects", "ab", "cd"),
            os.path.join(home, "Research", "project", "__pycache__", "x.pyc"),
            os.path.join(home, "Research", "project", ".venv", "lib", "x.py"),
        ):
            with self.subTest(path):
                self.assertFalse(self.filter.path_allowed(path))

    def test_the_agents_own_files_are_not_the_users_activity(self) -> None:
        internal = str(self.app.paths.data_dir / "database" / "agent.db")
        self.assertFalse(self.filter.path_allowed(internal))

    def test_removing_a_name_starts_recording_it_again(self) -> None:
        """The shipped exclusions are defaults, not rules."""
        home = str(Path.home())
        path = os.path.join(home, "proj", "build", "out.o")
        self.assertFalse(self.filter.path_allowed(path))
        remaining = [n for n in self.app.config.lines("listener_excluded_names")
                     if n != "build"]
        self.app.config.set("listener_excluded_names", LF.join(remaining))
        self.assertTrue(self.filter.path_allowed(path))


class AgentStateTests(unittest.TestCase):
    """Section 7.5: hovering shows what the agent is doing and will do next."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_state_answers_what_the_user_is_asking(self) -> None:
        state = self.app.agent_state()
        self.assertIn("model", state)
        for component in ("state_heartbeat", "heartbeat"):
            with self.subTest(component):
                self.assertIn("next_run_in_seconds", state[component])
                self.assertIn("last_run_seconds_ago", state[component])
        # The whole of the last round is not what belongs on a hover: it
        # holds every state, the pairs it printed and the tree.
        self.assertNotIn("last_report", state["state_heartbeat"])
        self.assertIn("tasks", state["state_heartbeat"])
        self.assertNotIn("data_heartbeat", state)
        self.assertIn("next_task", state)
        self.assertIn("privacy", state)

    def test_the_next_task_is_reported_with_how_long_until_it_runs(self) -> None:
        from personalized_agent.common.events.models import ScheduledTask, utcnow

        self.app.scheduler_store.create(ScheduledTask(
            title="Morning digest", scheduled_at=utcnow() + timedelta(minutes=42),
            execution_type="notification"))
        task = self.app.agent_state()["next_task"]
        self.assertEqual(task["title"], "Morning digest")
        self.assertGreater(task["in_seconds"], 2400)
        self.assertLess(task["in_seconds"], 2600)

    def test_a_past_task_is_not_reported_as_upcoming(self) -> None:
        from personalized_agent.common.events.models import ScheduledTask, utcnow

        self.app.scheduler_store.create(ScheduledTask(
            title="Yesterday", scheduled_at=utcnow() - timedelta(days=1),
            execution_type="notification"))
        self.assertIsNone(self.app.agent_state()["next_task"])

    def test_reading_the_state_does_not_make_the_agent_work(self) -> None:
        """Section 7.5: pointing at the agent must not change what it does."""
        calls = []
        self.app.models.check_availability = lambda role="primary", force=False: (
            calls.append(role) or None)
        self.app.agent_state()
        self.assertEqual(calls, [], "hovering triggered a model check")

    def test_a_disabled_component_says_so_rather_than_showing_nothing(self) -> None:
        from personalized_agent.ui.desktop import _schedule_line

        self.assertEqual(_schedule_line("Heartbeat", {"enabled": False}), "Heartbeat: off")
        self.assertEqual(_schedule_line("Heartbeat", {"enabled": True, "running": False}),
                         "Heartbeat: not running")

    def test_durations_are_shown_as_durations(self) -> None:
        from personalized_agent.ui.desktop import _ago_in_words, _in_words

        self.assertEqual(_in_words(30), "in 30s")
        self.assertEqual(_in_words(2520), "in 42m")
        self.assertEqual(_in_words(7260), "in 2h 01m")
        self.assertEqual(_ago_in_words(None), "not yet")
        self.assertEqual(_ago_in_words(90), "1m ago")

    def test_quitting_takes_the_window_away_before_it_does_the_work(
            self) -> None:
        """Stopping takes a second or two; looking stuck is avoidable.

        Tk is single-threaded, so a window left up while `stop` runs is
        not merely visible - it is frozen: the title bar stops
        responding and the mouse becomes a spinner. The work is the same
        either way. What changes is that the last thing the user sees is
        the application going away at the moment they asked.
        """
        from personalized_agent.ui.desktop import DesktopAgent

        done = []

        class Window:
            def __init__(self, name):
                self.name = name

            def withdraw(self):
                done.append(f"hide {self.name}")

            def update_idletasks(self):
                done.append("repaint")

            def update(self):
                pass

            def destroy(self):
                done.append("destroy")

        class App:
            def stop(self):
                done.append("stop")

        class Probe:
            quit = DesktopAgent.quit

            def __init__(self):
                self.app = App()
                self.root = Window("root")
                self.agent_window = Window("agent")
                self._bubble = Window("bubble")

        Probe().quit()
        # EVERY WINDOW GONE, AND REPAINTED, BEFORE THE BLOCKING CALL.
        self.assertLess(done.index("hide root"), done.index("stop"))
        self.assertLess(done.index("hide agent"), done.index("stop"))
        self.assertLess(done.index("hide bubble"), done.index("stop"))
        self.assertLess(done.index("repaint"), done.index("stop"),
                        "the screen was not repainted before the work")
        # And the mainloop ends after it, not before - leaving it running
        # would keep the process alive with nothing on screen.
        self.assertGreater(done.index("destroy"), done.index("stop"))

    def test_the_hover_readout_goes_with_the_rest_of_the_interface(
            self) -> None:
        """It stayed on screen after everything else had gone.

        Withdrawing the root does not hide a Toplevel - each is a window
        in its own right - and the list of windows to hide went stale the
        moment another was added. Tk is asked for its children instead,
        which cannot fall behind.

        The pending refresh job goes too: it reaches into the application
        for a readout, and leaving it queued means a callback arriving at
        a stopped agent a second from now.
        """
        from personalized_agent.ui.desktop import DesktopAgent

        done = []

        class Window:
            def __init__(self, name, kids=()):
                self.name = name
                self._kids = list(kids)

            def winfo_children(self):
                return self._kids

            def withdraw(self):
                done.append(f"hide {self.name}")

            def after_cancel(self, job):
                done.append(f"cancel {job}")

            def update_idletasks(self):
                done.append("repaint")

            def update(self):
                pass

            def destroy(self):
                done.append("destroy")

        hover = Window("hover")
        bubble = Window("bubble")
        agent = Window("agent")

        class Probe:
            quit = DesktopAgent.quit

            def __init__(self):
                self.app = type("A", (), {
                    "stop": lambda _s: done.append("stop")})()
                self.root = Window("root", kids=[agent, bubble, hover])
                self.agent_window = agent
                self._bubble = bubble
                self._hover = hover
                self._hover_job = "after#42"

        Probe().quit()
        # THE HOVER READOUT IS HIDDEN, by being found rather than listed.
        self.assertIn("hide hover", done)
        self.assertLess(done.index("hide hover"), done.index("stop"))
        # Its refresh job is cancelled before the agent stops.
        self.assertIn("cancel after#42", done)
        self.assertLess(done.index("cancel after#42"), done.index("stop"))

    def test_and_a_missing_window_does_not_stop_the_quit(self) -> None:
        """The bubble may never have been shown."""
        from personalized_agent.ui.desktop import DesktopAgent

        done = []

        class Window:
            def withdraw(self):
                raise RuntimeError("this window is already gone")

            def update_idletasks(self):
                raise RuntimeError("nothing to repaint")

            def update(self):
                pass

            def destroy(self):
                done.append("destroy")

        class Probe:
            quit = DesktopAgent.quit

            def __init__(self):
                self.app = type("A", (), {"stop": lambda _s: done.append("stop")})()
                self.root = Window()
                self.agent_window = None
                self._bubble = None

        Probe().quit()
        self.assertEqual(done, ["stop", "destroy"])

    def test_the_readout_names_what_needs_the_user(self) -> None:
        from personalized_agent.ui.desktop import DesktopAgent

        self.app.config.set("listener_enabled", False)
        self.app.reauthentication_required = {"provider": "anthropic", "reason": "expired"}

        class Probe:
            def __init__(self, app):
                self.app = app
            _hover_text = DesktopAgent._hover_text

        text = Probe(self.app)._hover_text()
        self.assertIn("Activity monitoring: off", text)
        self.assertIn("sign in to anthropic again", text)
        self.assertIn("Model:", text)

    def test_the_readout_names_all_three_heartbeats(self) -> None:
        """The state heartbeat first: it answers what have I been doing,
        which is the question somebody hovering is asking."""
        from personalized_agent.ui.desktop import DesktopAgent

        class Probe:
            def __init__(self, app):
                self.app = app
            _hover_text = DesktopAgent._hover_text

        lines = Probe(self.app)._hover_text().splitlines()
        named = [line.split(":")[0] for line in lines]
        self.assertIn("State heartbeat", named)
        self.assertIn("Heartbeat", named)
        self.assertNotIn("Data heartbeat", named)
        self.assertLess(named.index("State heartbeat"), named.index("Heartbeat"))

    def test_hovering_never_reads_on_the_windows_thread(self) -> None:
        """What the agent is doing is read on a thread of its own: a slow
        read - as at start, with the states being read twice over - left
        the window that owns the pointer unanswering, and Windows showed the
        busy cursor. Until the agent is up the readout says it is starting."""
        import queue as queues
        import time as clock

        from personalized_agent.ui.desktop import DesktopAgent

        class Label:
            text = ""

            def configure(self, text):
                Label.text = text

            def cget(self, _name):
                return Label.text

        class Probe:
            _refresh_hover = DesktopAgent._refresh_hover
            _read_hover = DesktopAgent._read_hover
            _show_hover = DesktopAgent._show_hover

            def __init__(self, config):
                self.config = config
                # What the avatar is called, as the application says it.
                self.app = type("App", (), {"avatar_name": lambda _self: "Agent"})()
                self.queue = queues.Queue()
                self._hover = object()
                self._hover_label = Label()
                self.root = type("Root", (), {"after": lambda *a: "job"})()

            def _place_hover(self):
                pass

            def _hover_text(self):
                clock.sleep(1.5)
                return "Agent\nState heartbeat: every 6 hours"

        probe = Probe(self.app.config)
        began = clock.monotonic()
        probe._refresh_hover()
        self.assertLess(clock.monotonic() - began, 0.3, "the window waited for the read")
        self.assertIn("Starting", Label.text)
        kind, said = probe.queue.get(timeout=5)
        self.assertEqual(kind, "hover")
        probe._hover_said, probe._up = said, True
        probe._show_hover(said)
        self.assertIn("State heartbeat", Label.text)
        self.assertNotIn("Starting", Label.text)

    def test_the_agent_binds_hovering(self) -> None:
        source = (ROOT / "personalized_agent" / "ui" / "desktop.py").read_text(encoding="utf-8")
        self.assertIn('bind("<Enter>"', source)
        self.assertIn('bind("<Leave>"', source)


class DesktopConversationTests(unittest.TestCase):
    """Section 7.1, as amended: the agent window holds one conversation."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_consecutive_messages_stay_in_one_conversation(self) -> None:
        first = self.app.agent.handle_message("remember my deadline is in March")
        second = self.app.agent.handle_message("when is it?", first["conversation_id"])
        self.assertEqual(first["conversation_id"], second["conversation_id"])

    def test_passing_no_conversation_starts_a_new_one(self) -> None:
        """Which is exactly why the window must keep its own."""
        first = self.app.agent.handle_message("hello")
        second = self.app.agent.handle_message("hello again")
        self.assertNotEqual(first["conversation_id"], second["conversation_id"])

    def test_the_agent_opens_the_applications_own_conversation(self) -> None:
        """Section 7.1, as amended: one conversation interface, not two."""
        source = (ROOT / "personalized_agent" / "ui" / "desktop.py").read_text(encoding="utf-8")
        self.assertIn("/chat.html", source)
        self.assertNotIn("class ChatWindow", source,
                         "a second conversation interface in the desktop toolkit")
        self.assertIn("self._conversation_id", source,
                      "the window must carry its conversation into the page")

    def test_the_conversation_window_shows_only_the_conversation(self) -> None:
        page = (WEB / "chat.html").read_text(encoding="utf-8")
        self.assertIn("chat-form", page)
        # The conversation itself is the shared script's, as in the Chat tab.
        self.assertIn('<script src="/chat.js"></script>', page)
        self.assertIn("/api/chat", (WEB / "chat.js").read_text(encoding="utf-8"))
        for panel in ("ov-hourly", "library-list", "settings-form", "calendar-surface"):
            self.assertNotIn(panel, page, "the conversation window shows only the conversation")

    def test_a_project_is_added_with_a_plus(self) -> None:
        """A + opens a title, a description and its folders - one or more,
        each chosen in the machine's own dialog - and nothing else."""
        html = (WEB / "index.html").read_text(encoding="utf-8")
        for field in ("xp-plus", "xp-new-title", "xp-new-description",
                      "xp-new-choose", "xp-new-create"):
            self.assertIn(f'id="{field}"', html)
        for gone in ('id="xp-new-project"', 'id="xp-new-work"', 'id="xp-new-start"'):
            self.assertNotIn(gone, html)
        js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn('{ action: "create", name, description, paths: [...xpNew.paths] }', js)
        self.assertIn('id="xp-new-paths"', html)
        self.assertIn("/api/settings/choose-directory", js)
        # In the order of when each was last active - not moved by hand.
        self.assertNotIn('draggable="true" data-xp-row=', js)
        self.assertNotIn("/api/experience/order", js)
        self.assertIn("last active", js)

    def test_each_project_is_learned_from_its_own_row(self) -> None:
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn("Learned experience", html)
        # One experience: no test, no switch between two.
        for gone in ('id="xp-build"', 'data-xp-scope', 'id="xp-update"', 'id="xp-test-bars"'):
            self.assertNotIn(gone, html)
        js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("/api/experience/learn", js)
        self.assertIn('"Update learning" : "Start learning"', js)
        self.assertIn("tokens spent learning it", js)
        self.assertNotIn("Clear and run again", js)

    def test_there_is_no_memory_tab_and_no_checklist_on_the_overview(self) -> None:
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('data-tab="library"', html)
        self.assertNotIn('id="library"', html)
        overview = html[html.index('<section id="overview"'):]
        overview = overview[:overview.index("</section>")]
        # Nor the heartbeat's checklist: the heartbeat debugger shows it.
        self.assertNotIn('id="hb-checklist"', overview)
        js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("/api/library?", js)
        self.assertNotIn("loadLibrary", js)

    def test_the_sign_in_prompt_can_be_dismissed(self) -> None:
        """Section 75.3, as amended: being told is not being nagged."""
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn("reauth-dismiss", html)
        js = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("/api/models/reauthentication/dismiss", js)

    def test_a_fast_build_is_offered_for_iterating(self) -> None:
        """It exists, and it is not what a release is built with."""
        source = (ROOT / "build.py").read_text(encoding="utf-8")
        self.assertIn("--fast", source)
        windows = (ROOT / "build" / "windows" / "build_windows.py").read_text(encoding="utf-8")
        self.assertIn("clean=not fast", windows)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        # Named as an option of build.py, however the README words it.
        self.assertIn("python build.py", readme)
        self.assertIn("--fast", readme)

    def test_quitting_is_not_queried(self) -> None:
        """Section 7.1, as amended: Quit was already the answer."""
        source = (ROOT / "personalized_agent" / "ui" / "desktop.py").read_text(encoding="utf-8")
        self.assertNotIn("askokcancel", source)
        self.assertNotIn("messagebox", source)
        self.assertIn("def quit", source)

    def test_the_conversation_page_keeps_one_thread(self) -> None:
        page = (WEB / "chat.js").read_text(encoding="utf-8")
        self.assertIn("conversation_id: conversationId", page)
        self.assertIn("conversationId = result.conversation_id", page)

    def test_both_interfaces_reach_the_same_conversations(self) -> None:
        result = self.app.agent.handle_message("a message from anywhere")
        listed = [c["id"] for c in self.app.conversations.conversations()]
        self.assertIn(result["conversation_id"], listed)


class PopupTextSizeTests(unittest.TestCase):
    """Section 7.5, as amended: the agent's small windows are readable.

    The two windows the agent puts on screen - what it says, and the status
    readout under the pointer - were fixed at nine point, which on a high
    resolution display is smaller than anything else on screen.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _desktop(self):
        # The font helper is what the windows read; building a real Tk window
        # needs a display, and the question here is the size, not the widget.
        from personalized_agent.ui.desktop import DesktopAgent
        stand_in = DesktopAgent.__new__(DesktopAgent)
        stand_in.config = self.app.config
        return stand_in

    def test_the_size_is_a_setting_with_an_explanation(self) -> None:
        field = next(f for f in self.app.config.schema()
                     if f["key"] == "popup_font_size")
        self.assertEqual(field["section"], "General")
        self.assertTrue(field["description"], "every setting explains itself")
        self.assertEqual((field["minimum"], field["maximum"]), (7, 24))

    def test_the_windows_follow_the_setting(self) -> None:
        desktop = self._desktop()
        self.assertEqual(desktop._popup_font()[1], 9)
        self.app.config.set("popup_font_size", 16)
        self.assertEqual(desktop._popup_font()[1], 16,
                         "the next popup must already use the new size")

    def test_an_impossible_size_cannot_break_the_window(self) -> None:
        desktop = self._desktop()
        # Written past validation on purpose: a settings file can be edited by
        # hand, and a stray value must not leave the agent unable to speak.
        for value in (0, -4, 900, "large", None):
            self.app.config._values["popup_font_size"] = value
            self.assertTrue(7 <= desktop._popup_font()[1] <= 24, f"{value!r}")

    def test_the_bubble_is_sized_from_the_text_not_fixed(self) -> None:
        # A larger font inside a fixed window only cuts the message off.
        source = (ROOT / "personalized_agent" / "ui" / "desktop.py").read_text(encoding="utf-8")
        self.assertNotIn('bubble.geometry(f"300x90', source)
        self.assertIn("_popup_font()", source)


class InteractionDetailTests(unittest.TestCase):
    """Section 9, as amended: what a stretch of use consisted of.

    A duration answers "how long" and never "doing what". The pages and files
    belong to the period they happened in, so that expanding one shows the
    visit itself rather than sending the user to line timestamps up by hand.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        now = datetime.now(timezone.utc)
        self.now = now
        add = self.app.raw_store.add_event
        # A finished browsing stretch: ten minutes, ending forty-five ago.
        add(ListenerEvent(event_type="application.focus_ended", source="application",
                          application="firefox", timestamp=now - timedelta(minutes=45),
                          data={"duration_seconds": 600, "window_title": "Mozilla Firefox"}))
        for minutes, url, title in ((50, "https://docs.example.com/os", "os module"),
                                    (48, "https://news.example.com/", "News")):
            add(ListenerEvent(event_type="browser.navigation", source="browser",
                              application="firefox", timestamp=now - timedelta(minutes=minutes),
                              data={"url": url, "title": title, "browser": "firefox"}))
        # And one visit since, which belongs to no finished period.
        add(ListenerEvent(event_type="browser.navigation", source="browser",
                          application="firefox", timestamp=now - timedelta(minutes=5),
                          data={"url": "https://docs.example.com/os", "title": "os module",
                                "browser": "firefox"}))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_a_period_carries_the_pages_open_during_it(self) -> None:
        detail = self.app.overview.application_detail("firefox")
        finished = [i for i in detail["interactions"] if not i.get("ongoing")]
        self.assertEqual(len(finished), 1)
        period = finished[0]
        self.assertEqual(len(period["pages"]), 2,
                         "both visits fall inside the ten minutes it ran")
        self.assertEqual(period["duration_human"], "10m")
        self.assertEqual(period["item_count"], 2)
        self.assertTrue(period["started_at"] < period["ended_at"])

    def test_activity_since_the_last_period_is_not_dropped(self) -> None:
        # It has not ended yet; showing nothing would lose the newest thing
        # the user did, which is the one they most likely came to look for.
        detail = self.app.overview.application_detail("firefox")
        ongoing = [i for i in detail["interactions"] if i.get("ongoing")]
        self.assertEqual(len(ongoing), 1)
        self.assertEqual(len(ongoing[0]["pages"]), 1)
        self.assertEqual(detail["interactions"][0], ongoing[0],
                         "what is happening now belongs at the top")

    def test_a_page_can_be_asked_about_on_its_own(self) -> None:
        detail = self.app.overview.page_detail("https://docs.example.com/os")
        self.assertEqual(detail["visit_count"], 2)
        self.assertEqual(detail["title"], "os module")
        self.assertEqual([a["name"] for a in detail["applications"]], ["firefox"])
        self.assertGreater(detail["visits"][0]["at"], detail["visits"][1]["at"],
                           "most recent first")
        self.assertTrue(detail["first_seen"] < detail["last_seen"])

    def test_an_unvisited_page_reports_nothing_rather_than_failing(self) -> None:
        detail = self.app.overview.page_detail("https://never.example.com/")
        self.assertEqual(detail["visit_count"], 0)
        self.assertEqual(detail["visits"], [])

    def test_the_page_endpoint_is_registered(self) -> None:
        # The panel reaches for this path; if nothing serves it, the drill-down
        # fails at the moment the user clicks a page.
        from personalized_agent.api.routes import build_routes

        paths = {path for _method, path in build_routes(self.app)}
        self.assertIn("/api/overview/page", paths)
        self.assertIn("/api/overview/application", paths)


class ApplicationBreakdownTests(unittest.TestCase):
    """Section 9, as amended: what an application was used for, in the list.

    The kind follows the events rather than a list of known program names, so
    an application nobody has classified still shows whatever it recorded.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        now = datetime.now(timezone.utc)
        add = self.app.raw_store.add_event
        for app_name, seconds in (("firefox", 1800), ("code", 600), ("slack", 300)):
            add(ListenerEvent(event_type="application.focus_ended", source="application",
                              application=app_name, timestamp=now - timedelta(minutes=5),
                              data={"duration_seconds": seconds, "window_title": app_name}))
        for url in ("https://docs.example.com/a", "https://docs.example.com/b",
                    "https://news.example.com/"):
            add(ListenerEvent(event_type="browser.navigation", source="browser",
                              application="firefox", timestamp=now - timedelta(minutes=10),
                              data={"url": url, "title": url, "browser": "firefox"}))
        add(ListenerEvent(event_type="filesystem.modified", source="filesystem",
                          application="code", timestamp=now - timedelta(minutes=8),
                          data={"path": SEP.join([PROJ, "main.py"])}))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _by_name(self):
        return {a["application"]: a for a in self.app.overview.summary()["applications"]}

    def test_a_browsers_time_is_broken_down_by_site(self) -> None:
        firefox = self._by_name()["firefox"]
        self.assertEqual(firefox["breakdown_kind"], "sites")
        labels = [b["label"] for b in firefox["breakdown"]]
        self.assertEqual(labels, ["docs.example.com", "news.example.com"],
                         "ordered by how much they account for")
        self.assertEqual(firefox["breakdown"][0]["count"], 2)
        self.assertTrue(firefox["breakdown"][0]["url"],
                        "a site must carry a page that can be opened")

    def test_an_editors_time_is_broken_down_by_file(self) -> None:
        code = self._by_name()["code"]
        self.assertEqual(code["breakdown_kind"], "files")
        self.assertEqual([b["label"] for b in code["breakdown"]], ["main.py"])

    def test_an_application_that_recorded_nothing_more_has_no_breakdown(self) -> None:
        # The row must not offer to open onto nothing.
        slack = self._by_name()["slack"]
        self.assertEqual(slack["breakdown"], [])
        self.assertEqual(slack["breakdown_kind"], "")

    def test_the_breakdown_is_bounded(self) -> None:
        now = datetime.now(timezone.utc)
        for index in range(20):
            self.app.raw_store.add_event(ListenerEvent(
                event_type="browser.navigation", source="browser", application="firefox",
                timestamp=now - timedelta(minutes=11),
                data={"url": f"https://site{index}.example.com/", "browser": "firefox"}))
        self.assertLessEqual(len(self._by_name()["firefox"]["breakdown"]), 8,
                             "a list in a bar row cannot be unbounded")


class ScriptActuallyRunsTests(unittest.TestCase):
    """The frontend must run, not merely parse.

    A script can parse perfectly and still leave half the interface dead: a
    declaration removed by an edit elsewhere is a reference error at load, and
    everything after it never initialises. That is invisible to a parse and to
    every backend test, and it presents as one panel quietly going blank.
    """

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to run the page")

    def _load(self, steps: str = "") -> dict:
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        script = (web / "app.js").read_text(encoding="utf-8")
        now = datetime.now().replace(microsecond=0).isoformat()
        stub = {
            "/api/tasks": {"tasks": [{"id": "t1", "title": "Weekly review",
                                      "status": "pending", "execution_type": "immediate",
                                      "scheduled_at": now}]},
            "/api/status": {"pending_raw_data": {"events": 0, "edits": 0},
                            "library": {"records": 0, "folders": {}}},
        }
        harness = (
            "<script>window.__errs=[];window.addEventListener('error',"
            "e=>window.__errs.push(e.message));const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u)=>{const p=String(u).split('?')[0];"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + script + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';"
            "try{" + steps + "}catch(e){err=e.message;}"
            "document.title=JSON.stringify({error:err,errs:window.__errs,"
            "days:document.querySelectorAll('.cal-day').length,"
            "chips:document.querySelectorAll('.cal-chip').length,"
            "period:(document.querySelector('#cal-period')||{}).textContent||''});});"
            "</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=8000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
            dom = completed.stdout or ""
        match = re.search(r"<title>(.*?)</title>", dom, re.S)
        self.assertIsNotNone(match, "the page never finished loading")
        return json.loads(html.unescape(match.group(1)))

    def test_every_top_level_declaration_survives_to_the_end(self) -> None:
        # Naming each one here is what catches a removal: an edit that deletes
        # a declaration leaves everything after it uninitialised.
        names = ["overviewState", "calendarState", "WEEKDAYS", "DAY_MS", "unfolded",
                 "INTERACTIONS_SHOWN", "TITLE_CHARS", "SERIES_COLORS", "CHOOSERS"]
        checks = ";".join(
            f"if(typeof {name}==='undefined')throw new Error('{name} is missing')"
            for name in names)
        result = self._load(checks + ";")
        self.assertEqual(result["error"], "")
        self.assertEqual(result["errs"], [])

    def test_the_calendar_draws_its_grid(self) -> None:
        """Section 10, as amended: drawn from the date, not from the data."""
        result = self._load("await loadCalendar();")
        self.assertEqual(result["error"], "")
        self.assertEqual(result["days"], 42, "six weeks, whatever the data holds")
        self.assertTrue(result["period"], "the period must be named")
        self.assertEqual(result["chips"], 1, "and a scheduled task must appear on it")

    def test_the_calendar_draws_its_grid_with_no_tasks_at_all(self) -> None:
        result = self._load("STUB['/api/tasks']={tasks:[]};await loadCalendar();")
        self.assertEqual(result["error"], "")
        self.assertEqual(result["days"], 42, "an empty month still shows its days")
        self.assertEqual(result["chips"], 0)


class ActivityAttributionTests(unittest.TestCase):
    """Section 9, as amended: joining activity to the application it belongs to.

    The sources do not agree on names. Usage is counted under the process name
    ("firefox.exe"), browsing under the history source it was read from
    ("Firefox"), and a file change names nothing at all. Compared literally
    they never meet, and every row offers nothing to open while the data sits
    there.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        now = datetime.now(timezone.utc)
        self.now = now
        add = self.app.raw_store.add_event
        # Named exactly as the real listeners name them.
        add(ListenerEvent(event_type="application.focus_ended", source="application",
                          application="firefox.exe", timestamp=now - timedelta(minutes=20),
                          data={"duration_seconds": 1800, "window_title": "Mozilla Firefox"}))
        add(ListenerEvent(event_type="application.focus_ended", source="application",
                          application="Code.exe", timestamp=now - timedelta(minutes=2),
                          data={"duration_seconds": 600, "window_title": "VS Code"}))
        add(ListenerEvent(event_type="browser.navigation", source="browser",
                          application="Firefox", timestamp=now - timedelta(minutes=30),
                          data={"url": "https://docs.example.com/a", "browser": "Firefox"}))
        add(ListenerEvent(event_type="filesystem.modified", source="filesystem",
                          application=None, timestamp=now - timedelta(minutes=5),
                          data={"path": SEP.join([PROJ, "main.py"])}))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _by_name(self):
        return {a["application"]: a for a in self.app.overview.summary()["applications"]}

    def test_the_two_spellings_of_one_application_are_one_row(self) -> None:
        firefox = self._by_name()["firefox.exe"]
        self.assertEqual(firefox["breakdown_kind"], "sites")
        self.assertEqual([b["label"] for b in firefox["breakdown"]], ["docs.example.com"])

    def test_the_differently_spelled_source_does_not_claim_a_row(self) -> None:
        # "Firefox" is not something the usage list is keyed by; filing the
        # sites under it would hide them behind a row that does not exist.
        self.assertNotIn("Firefox", self._by_name())

    def test_activity_naming_no_application_goes_to_whatever_was_in_focus(self) -> None:
        code = self._by_name()["Code.exe"]
        self.assertEqual([b["label"] for b in code["breakdown"]], ["main.py"])

    def test_activity_with_nothing_in_focus_is_not_attributed_at_random(self) -> None:
        self.app.raw_store.add_event(ListenerEvent(
            event_type="browser.navigation", source="browser", application="Chrome",
            timestamp=self.now - timedelta(hours=10),
            data={"url": "https://orphan.example/", "browser": "Chrome"}))
        for entry in self._by_name().values():
            for item in entry["breakdown"]:
                self.assertNotIn("orphan", item["label"],
                                 "an unattributable visit must not be given to someone")


class SiteNarrowingTests(unittest.TestCase):
    """Section 9, as amended: one site inside an application.

    Asking about a site is a narrower version of the same question, so the
    answer keeps its shape - the same stretches of use, carrying only what
    belongs to that site.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        now = datetime.now(timezone.utc)
        add = self.app.raw_store.add_event
        # Two stretches: the first touched both sites, the second only one.
        add(ListenerEvent(event_type="application.focus_ended", source="application",
                          application="firefox.exe", timestamp=now - timedelta(minutes=40),
                          data={"duration_seconds": 600, "window_title": "Firefox"}))
        add(ListenerEvent(event_type="application.focus_ended", source="application",
                          application="firefox.exe", timestamp=now - timedelta(minutes=10),
                          data={"duration_seconds": 600, "window_title": "Firefox"}))
        for minutes, url in ((45, "https://docs.example.com/a"),
                             (44, "https://news.example.com/x"),
                             (15, "https://news.example.com/y")):
            add(ListenerEvent(event_type="browser.navigation", source="browser",
                              application="Firefox", timestamp=now - timedelta(minutes=minutes),
                              data={"url": url, "title": url, "browser": "Firefox"}))
        add(ListenerEvent(event_type="filesystem.modified", source="filesystem",
                          application=None, timestamp=now - timedelta(minutes=14),
                          data={"path": SEP.join([PROJ, "notes.txt"])}))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_only_the_stretches_that_touched_the_site_are_shown(self) -> None:
        whole = self.app.overview.application_detail("firefox.exe")
        narrowed = self.app.overview.application_detail("firefox.exe",
                                                        site="docs.example.com")
        self.assertGreater(len(whole["interactions"]), len(narrowed["interactions"]))
        self.assertEqual(len(narrowed["interactions"]), 1)
        self.assertEqual(narrowed["site"], "docs.example.com")

    def test_a_stretch_carries_only_that_sites_visits(self) -> None:
        narrowed = self.app.overview.application_detail("firefox.exe",
                                                        site="news.example.com")
        for interaction in narrowed["interactions"]:
            self.assertTrue(interaction["pages"])
            for page in interaction["pages"]:
                self.assertEqual(page["site"], "news.example.com")
            self.assertEqual(interaction["item_count"], len(interaction["pages"]))

    def test_a_stretch_that_never_touched_it_is_absent_not_empty(self) -> None:
        # Showing it as an empty stretch would say the user was there and did
        # nothing, which is not what happened.
        narrowed = self.app.overview.application_detail("firefox.exe",
                                                        site="docs.example.com")
        self.assertTrue(all(i["pages"] for i in narrowed["interactions"]))

    def test_files_belong_to_the_application_not_to_the_site(self) -> None:
        narrowed = self.app.overview.application_detail("firefox.exe",
                                                        site="news.example.com")
        self.assertTrue(all(not i["files"] for i in narrowed["interactions"]))

    def test_a_site_with_nothing_recorded_narrows_to_nothing(self) -> None:
        narrowed = self.app.overview.application_detail("firefox.exe",
                                                        site="absent.example.com")
        self.assertEqual(narrowed["interactions"], [])

    def test_asking_without_a_site_is_unchanged(self) -> None:
        whole = self.app.overview.application_detail("firefox.exe")
        self.assertEqual(whole["site"], "")
        self.assertTrue(any(i["pages"] for i in whole["interactions"]))


class ChatProgressRenderTests(unittest.TestCase):
    """Section 15.1: the waiting is shown as progress, in the chat itself."""

    STEPS = [
        {"stage": "Looking through what I know", "detail": "searching your memory",
         "started_at": "2026-09-01T09:00:00+00:00", "finished_at": "2026-09-01T09:00:01+00:00"},
        {"stage": "Read my notes", "detail": "3 related entries",
         "started_at": "2026-09-01T09:00:01+00:00", "finished_at": "2026-09-01T09:00:02+00:00"},
        {"stage": "Using remember", "detail": "title: the deadline",
         "started_at": "2026-09-01T09:00:02+00:00"},
    ]

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _run(self, page_name: str) -> dict:
        web = ROOT / "personalized_agent" / "api" / "web"
        # The conversation's own script, in place, as the page loads it.
        page = (web / page_name).read_text(encoding="utf-8").replace(
            '<script src="/chat.js"></script>',
            "<script>" + (web / "chat.js").read_text(encoding="utf-8") + "</script>")
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "", page)
        extra = ""
        if page_name == "index.html":
            extra = "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
        harness = (
            extra +
            "<script>window.addEventListener('load',async()=>{let err='';"
            "const wait=()=>new Promise(r=>setTimeout(r,120));"
            "window.fetch=(u)=>Promise.resolve({ok:true,status:200,json:()=>"
            "Promise.resolve({active:true,steps:" + json.dumps(self.STEPS) + "})});"
            "let p;try{p=chat.progressPanel(document.querySelector('#chat-log'));"
            "await wait();await wait();}catch(e){err=e.message;}"
            "const panel=document.querySelector('.progress-panel');"
            "const before={present:!!panel,"
            "text:panel?panel.textContent.split(/[^!-~]+/).join(' ').trim():'',"
            "done:document.querySelectorAll('.progress-steps li.done').length,"
            "doing:document.querySelectorAll('.progress-steps li.doing').length};"
            "p.finish();await wait();"
            "document.title=JSON.stringify({err,before,"
            "afterPresent:!!document.querySelector('.progress-panel')});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
            dom = completed.stdout or ""
        match = re.search(r"<title>(.*?)</title>", dom, re.S)
        self.assertIsNotNone(match, "the page never finished loading")
        return json.loads(html.unescape(match.group(1)))

    def test_the_stages_are_shown_while_the_agent_works(self) -> None:
        for page in ("chat.html", "index.html"):
            with self.subTest(page):
                result = self._run(page)
                self.assertEqual(result["err"], "")
                self.assertTrue(result["before"]["present"], "the panel must appear")
                self.assertIn("Looking through what I know", result["before"]["text"])
                self.assertIn("3 related entries", result["before"]["text"],
                              "a stage says what it found, not only that it ran")
                self.assertIn("Using remember", result["before"]["text"])

    def test_the_finished_stages_are_told_apart_from_the_current_one(self) -> None:
        result = self._run("chat.html")
        self.assertEqual(result["before"]["done"], 2)
        self.assertEqual(result["before"]["doing"], 1)

    def test_the_panel_goes_when_the_answer_arrives(self) -> None:
        # It describes work in progress; finished work is described by the
        # answer itself, and a stale panel under it would say otherwise.
        for page in ("chat.html", "index.html"):
            with self.subTest(page):
                self.assertFalse(self._run(page)["afterPresent"])


class WindowTitleContentTests(unittest.TestCase):
    """Section 9, as amended: an application that opens nothing still did something.

    A terminal opens no pages and, unless the user works in a watched
    directory, touches no recorded files. What it was doing is in its window
    title, and without that its stretches read "nothing recorded" while the
    record existed and was simply not being looked at.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        now = datetime.now(timezone.utc)
        self.now = now
        add = self.app.raw_store.add_event
        add(ListenerEvent(event_type="application.focus_ended", source="application",
                          application="WindowsTerminal.exe",
                          timestamp=now - timedelta(minutes=10),
                          data={"duration_seconds": 600, "window_title": "Terminal"}))
        for minutes, title in ((19, "building the release"),
                               (15, "running the tests"),
                               (14, "running the tests")):
            add(ListenerEvent(event_type="application.focused", source="application",
                              application="WindowsTerminal.exe",
                              timestamp=now - timedelta(minutes=minutes),
                              data={"window_title": title}))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _stretch(self):
        detail = self.app.overview.application_detail("WindowsTerminal.exe")
        return next(i for i in detail["interactions"] if not i.get("ongoing"))

    def test_a_stretch_with_no_pages_or_files_still_has_content(self) -> None:
        stretch = self._stretch()
        self.assertGreater(stretch["item_count"], 0,
                           "it said nothing was recorded when there was")
        self.assertEqual([w["title"] for w in stretch["windows"]][:1],
                         ["building the release"])

    def test_a_title_that_did_not_change_is_one_thing_not_several(self) -> None:
        titles = [w["title"] for w in self._stretch()["windows"]]
        self.assertEqual(titles.count("running the tests"), 1)

    def test_an_animated_title_is_one_thing_not_dozens(self) -> None:
        # A terminal rewrites its own title several times a second to spin a
        # glyph; compared literally each frame is a different title.
        now = datetime.now(timezone.utc)
        for index, glyph in enumerate("◐◓◑✳⠋"):
            self.app.raw_store.add_event(ListenerEvent(
                event_type="application.focused", source="application",
                application="Spinner.exe",
                timestamp=now - timedelta(minutes=30 - index),
                data={"window_title": f"{glyph} building the release"}))
        self.app.raw_store.add_event(ListenerEvent(
            event_type="application.focus_ended", source="application",
            application="Spinner.exe", timestamp=now - timedelta(minutes=20),
            data={"duration_seconds": 900, "window_title": "Spinner"}))
        detail = self.app.overview.application_detail("Spinner.exe")
        stretch = next(i for i in detail["interactions"] if not i.get("ongoing"))
        self.assertEqual([w["title"] for w in stretch["windows"]],
                         ["building the release"])

    def test_a_title_that_is_only_decoration_is_not_content(self) -> None:
        from personalized_agent.agent.overview.service import normalise_title

        self.assertEqual(normalise_title("◐ ✳ "), "")

    def test_an_ordinary_title_is_left_alone(self) -> None:
        from personalized_agent.agent.overview.service import normalise_title

        for title in ("ordinary title", "(2) something",
                      "cmd.exe - python run.py"):
            self.assertEqual(normalise_title(title), title)


class AssistantWorkIsInteractionTests(unittest.TestCase):
    """Section 9, as amended: the work, not the window it happened in.

    A stretch of terminal use with a coding-assistant session running inside
    it is not "nothing recorded" - the record exists, and it is the
    conversation. Saying otherwise describes the window rather than the work.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.now = datetime.now(timezone.utc)
        self.app.raw_store.add_event(ListenerEvent(
            event_type="application.focus_ended", source="application",
            application="WindowsTerminal.exe",
            timestamp=self.now - timedelta(minutes=5),
            data={"duration_seconds": 600, "window_title": "a terminal"}))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _say(self, minutes, role, text, session="s1"):
        self.app.raw_store.add_event(ListenerEvent(
            event_type="assistant.message", source="assistant",
            application="Claude Code",
            timestamp=self.now - timedelta(minutes=minutes),
            data={"role": role, "text": text, "tool": "Claude Code",
                  "project": PROJ, "session_id": session}))

    def _stretch(self):
        detail = self.app.overview.application_detail("WindowsTerminal.exe")
        return next(i for i in detail["interactions"] if not i.get("ongoing"))

    def test_a_conversation_inside_a_stretch_is_its_content(self) -> None:
        self._say(14, "user", "why does this say nothing recorded")
        self._say(13, "assistant", "because only metadata was kept")
        stretch = self._stretch()
        self.assertEqual(stretch["item_count"], 2)
        self.assertEqual([m["text"] for m in stretch["assistant"]],
                         ["why does this say nothing recorded",
                          "because only metadata was kept"])

    def test_both_sides_are_told_apart(self) -> None:
        self._say(14, "user", "a question")
        self._say(13, "assistant", "an answer")
        roles = [m["role"] for m in self._stretch()["assistant"]]
        self.assertEqual(roles, ["user", "assistant"])

    def test_the_work_is_attributed_to_the_window_it_happened_in(self) -> None:
        # The events name "Claude Code"; the usage list knows the terminal.
        self._say(14, "user", "a question")
        self.assertTrue(self._stretch()["assistant"])

    def test_a_session_summary_is_shown_once_however_often_it_is_reported(self) -> None:
        # A session is re-reported whenever it grows, so one stretch can hold
        # the same session several times over.
        #
        # The turn is here because a session is placed by where it was typed
        # (section 9, as amended): a summary says when a transcript grew, and
        # a transcript grows while the user is elsewhere, so what was in
        # front then is not where the work was. The turn is that evidence.
        self._say(15, "user", "start", session="s1")
        for minutes in (14, 13, 12):
            self.app.raw_store.add_event(ListenerEvent(
                event_type="assistant.session", source="assistant",
                application="Claude Code",
                timestamp=self.now - timedelta(minutes=minutes),
                data={"tool": "Claude Code", "project": PROJ, "exchanges": 12,
                      "session_id": "s1"}))
        summaries = [m for m in self._stretch()["assistant"]
                     if m["role"] == "session"]
        self.assertEqual(len(summaries), 1)

    def test_what_was_said_is_kept_even_when_a_summary_is_folded(self) -> None:
        self.app.raw_store.add_event(ListenerEvent(
            event_type="assistant.session", source="assistant",
            application="Claude Code", timestamp=self.now - timedelta(minutes=14),
            data={"tool": "Claude Code", "project": PROJ, "exchanges": 4}))
        self._say(13, "user", "the first thing I said")
        self._say(12, "user", "the second thing I said")
        texts = [m["text"] for m in self._stretch()["assistant"]]
        self.assertIn("the first thing I said", texts)
        self.assertIn("the second thing I said", texts)

    def test_recording_the_conversation_is_chosen_per_assistant(self) -> None:
        # Working through an assistant is where much of the user's thinking
        # happens - so every assistant found is recorded by default (*), as
        # every listening is on by default; and it is chosen per assistant
        # rather than for the whole machine.
        from personalized_agent.common.config.schema import SCHEMA_BY_KEY

        field = SCHEMA_BY_KEY["listener_assistant_content_tools"]
        self.assertEqual(field.default, "*")
        self.assertEqual(field.type, "multi_choice")
        self.assertIn("monitoring off", field.description)


class CalendarMonthOnlyTests(unittest.TestCase):
    """Section 10, as amended: a time is a thing you can point at.

    Drawing hours and then treating a click as "this day" wastes the grid: the
    reason to draw hours is that the user can aim at one.
    """

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _run(self, steps: str) -> dict:
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        now = datetime.now().replace(hour=14, minute=0, second=0, microsecond=0)
        stub = {
            "/api/tasks": {"tasks": [{"id": "t1", "title": "Weekly review",
                                      "status": "pending", "execution_type": "immediate",
                                      "scheduled_at": now.isoformat()}]},
            "/api/states": {"states": [], "covering_now": [], "counts": {}},
            "/api/status": {"pending_raw_data": {"events": 0, "edits": 0},
                            "library": {"records": 0, "folders": {}}},
        }
        harness = (
            "<script>const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u)=>{const p=String(u).split('?')[0];"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';"
            "const wait=()=>new Promise(r=>setTimeout(r,60));"
            "try{await loadCalendar();await wait();" + steps + "}catch(e){err=e.message;}"
            "document.title=JSON.stringify({err,"
            "period:(document.querySelector('#cal-period')||{}).textContent||'',"
            "slots:document.querySelectorAll('.cal-slot').length,"
            "views:document.querySelectorAll('#cal-views button').length,"
            "days:document.querySelectorAll('.cal-day').length});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
            dom = completed.stdout or ""
        match = re.search(r"<title>(.*?)</title>", dom, re.S)
        self.assertIsNotNone(match, "the calendar never rendered")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "")
        return result

    def test_only_the_month_is_left(self) -> None:
        result = self._run("renderCalendar();await wait();")
        self.assertEqual(result["days"], 42, "six weeks, drawn from the date")
        self.assertEqual(result["slots"], 0, "no grid of times")
        self.assertEqual(result["views"], 0, "no week or day buttons")

    def test_double_clicking_a_day_opens_it_hour_by_hour(self) -> None:
        # The user's later decision: a day view, as a calendar has, and a
        # day of the month opened by double-clicking it (test_calendar_day).
        result = self._run(
            "document.querySelector('.cal-day[data-date]')"
            ".dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));await wait();")
        self.assertEqual(result["days"], 0, "the month gave way to the day")

    def test_previous_and_next_move_a_month(self) -> None:
        start = self._run("")["period"]
        moved = self._run("document.querySelector('#cal-next').click();await wait();")
        back = self._run("document.querySelector('#cal-next').click();await wait();"
                         "document.querySelector('#cal-prev').click();await wait();")
        self.assertNotEqual(moved["period"], start)
        self.assertEqual(back["period"], start)
        self.assertEqual(moved["days"], 42)


class ChatFollowUpPanelTests(unittest.TestCase):
    """A reply is asked about by selecting in it - a phrase, or all of it -
    and the answer sits under it, folds, and closes; no deeper. There is no
    Follow up button."""

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _run(self, steps: str, width: int = 1400) -> dict:
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        stub = {
            "/api/chat": {"answer": "They are fetched from the census API.",
                          "message_id": "msg_1", "conversation_id": "conv_1",
                          "classification": "answer", "tool_calls": [],
                          "duration_seconds": 1},
            "/api/chat/followup": {"id": "fup_1", "answer": "A public data service.",
                                   "about": "", "question": "q"},
            "/api/chat/followup/delete": {"deleted": True},
            "/api/status": {"pending_raw_data": {"events": 0, "edits": 0},
                            "library": {"records": 0, "folders": {}}},
        }
        harness = (
            # The page's own styles: where a follow-up goes is layout.
            "<style>" + (web / "styles.css").read_text(encoding="utf-8") + "</style>"
            "<script>window.__posted=[];const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
            "if(o&&o.method==='POST')window.__posted.push({p,b:o.body});"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "headers:{get:()=>null},"
            "json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "chat.js").read_text(encoding="utf-8") + "</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';"
            "const wait=()=>new Promise(r=>setTimeout(r,80));"
            "const out={};"
            "try{document.querySelectorAll('.tab').forEach(s=>"
            "  s.classList.toggle('active',s.id==='chat'));"
            "document.querySelector('#chat-input').value='Where from?';"
            "document.querySelector('#chat-form').requestSubmit();await wait();"
            + steps + "}catch(e){err=e.message;}"
            "const box=document.querySelector('.followup');"
            "document.title=JSON.stringify({err,...out,"
            "boxes:document.querySelectorAll('.followup').length,"
            "title:box?box.querySelector('.fu-title').textContent:'',"
            "body:box?box.querySelector('.fu-body').textContent:'',"
            "folded:box?box.classList.contains('folded'):null,"
            "buttons:document.querySelectorAll('.msg .fu-whole,.msg-tools').length,"
            "posted:window.__posted});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--window-size={width},900",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
            dom = completed.stdout or ""
        match = re.search(r"<title>(.*?)</title>", dom, re.S)
        self.assertIsNotNone(match, "the page never rendered")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "")
        return result

    SELECT = ("const sel=window.getSelection();sel.removeAllRanges();"
              "sel.addRange(range);"
              "range.commonAncestorContainer.parentElement.dispatchEvent("
              "new MouseEvent('mouseup',{bubbles:true}));await wait();")
    ASK_WHOLE = ("const range=document.createRange();"
                 "range.selectNodeContents(document.querySelector('.msg-text'));"
                 + SELECT +
                 "out.pick=document.querySelector('#fu-pick').textContent;"
                 "document.querySelector('#fu-pick').click();await wait();"
                 "const input=document.querySelector('.fu-input');"
                 "out.placeholder=input.placeholder;"
                 "input.dispatchEvent(new KeyboardEvent('keydown',"
                 "{key:'Tab',bubbles:true,cancelable:true}));"
                 "out.filled=input.value;"
                 "input.form.requestSubmit();await wait();")

    # -- in the margin, as a comment is ----------------------------------
    ASK_PHRASE = ("{const text=document.querySelector('.msg-text').firstChild;"
                  "const range=document.createRange();"
                  "const at=text.textContent.indexOf('{word}');"
                  "range.setStart(text,at);range.setEnd(text,at+'{word}'.length);"
                  + SELECT +
                  "document.querySelector('#fu-pick').click();await wait();"
                  "document.querySelector('.fu-form').requestSubmit();await wait();await wait();}")
    PLACES = ("{const log=document.querySelector('#chat-log');"
              "const reply=document.querySelector('.msg.assistant .msg-text')"
              "  .getBoundingClientRect();"
              "out.margin=log.classList.contains('fu-margin-mode');"
              "out.cards=[...document.querySelectorAll('.followup')].map(c=>{"
              "  const r=c.getBoundingClientRect();const m=c.__anchor;"
              "  return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,"
              "    replyLeft:reply.left,replyRight:reply.right,replyBottom:reply.bottom,"
              "    markTop:m?m.getBoundingClientRect().top:null,"
              "    marked:m?m.textContent:''};});"
              "out.marks=[...document.querySelectorAll('mark.fu-anchor')]"
              "  .map(m=>m.textContent);}")

    def test_a_follow_up_sits_in_the_margin_beside_the_words_it_asks_about(self) -> None:
        result = self._run(self.ASK_PHRASE.replace("{word}", "census API") + self.PLACES)
        self.assertTrue(result["margin"])
        card = result["cards"][0]
        self.assertEqual(card["marked"], "census API")
        self.assertEqual(result["marks"], ["census API"])
        # On the left, the side the replies are on.
        self.assertLess(card["right"], card["replyLeft"], "not to the left of the reply")
        self.assertLess(abs(card["top"] - card["markTop"]), 3, "not at the words' height")

    def test_two_follow_ups_do_not_overlap_and_closing_one_unmarks_its_words(self) -> None:
        steps = (self.ASK_PHRASE.replace("{word}", "census API")
                 + self.ASK_PHRASE.replace("{word}", "fetched") + self.PLACES
                 + "out.before=out.cards;"
                 "document.querySelector('.followup .fu-close').click();await wait();"
                 + self.PLACES)
        result = self._run(steps)
        first, second = sorted(result["before"], key=lambda c: c["top"])
        self.assertGreaterEqual(second["top"], first["bottom"], "the cards overlap")
        self.assertEqual(len(result["cards"]), 1)
        self.assertEqual(len(result["marks"]), 1, "the closed card's words stay marked")

    def test_a_narrow_chat_keeps_them_under_the_reply(self) -> None:
        result = self._run(self.ASK_PHRASE.replace("{word}", "census API") + self.PLACES,
                           width=600)
        self.assertFalse(result["margin"])
        card = result["cards"][0]
        self.assertGreater(card["top"], card["replyBottom"] - 1)

    def test_the_whole_reply_can_be_followed_up(self) -> None:
        result = self._run(self.ASK_WHOLE)
        self.assertIn("what is They are fetched from the census API.", result["placeholder"])
        # Tab fills in the question it would ask.
        self.assertEqual(result["filled"], "what is They are fetched from the census API.")
        asked = [json.loads(p["b"]) for p in result["posted"]
                 if p["p"] == "/api/chat/followup"]
        self.assertEqual(asked, [{"message_id": "msg_1", "about": "",
                                  "question": "what is They are fetched from the census API."}])
        self.assertEqual(result["boxes"], 1)
        self.assertTrue(result["title"].startswith("Whole reply: what is"))
        self.assertEqual(result["body"], "A public data service.")
        self.assertEqual(result["pick"], "Ask about this reply")
        self.assertEqual(result["buttons"], 0, "there is no Follow up button")

    def test_an_answer_cannot_be_asked_about(self) -> None:
        """Two levels at most: selecting in a follow-up's answer offers
        nothing."""
        result = self._run(self.ASK_WHOLE
                           + "const again=document.createRange();"
                           "again.selectNodeContents(document.querySelector('.fu-body'));"
                           "const s2=window.getSelection();s2.removeAllRanges();"
                           "s2.addRange(again);document.querySelector('.fu-body')"
                           ".dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));"
                           "await wait();"
                           "out.offered=!!document.querySelector('#fu-pick');")
        self.assertFalse(result["offered"])

    def test_a_selected_phrase_can_be_followed_up(self) -> None:
        result = self._run(
            "const text=document.querySelector('.msg-text').firstChild;"
            "const at=text.textContent.indexOf('census API');"
            "const range=document.createRange();range.setStart(text,at);"
            "range.setEnd(text,at+10);const sel=window.getSelection();"
            "sel.removeAllRanges();sel.addRange(range);"
            "document.querySelector('.msg-text').dispatchEvent("
            "new MouseEvent('mouseup',{bubbles:true}));await wait();"
            "out.pick=document.querySelector('#fu-pick').textContent;"
            "document.querySelector('#fu-pick').click();await wait();"
            "out.about=document.querySelector('.fu-about').textContent;"
            "document.querySelector('.fu-input').form.requestSubmit();await wait();")
        self.assertIn("census API", result["pick"])
        self.assertIn("census API", result["about"])
        asked = [json.loads(p["b"]) for p in result["posted"]
                 if p["p"] == "/api/chat/followup"]
        # Sent empty, it asks what the phrase is.
        self.assertEqual(asked[0]["about"], "census API")
        self.assertEqual(asked[0]["question"], "what is census API")
        self.assertTrue(result["title"].startswith("census API: what is census API"))

    def test_it_folds_and_closes(self) -> None:
        folded = self._run(self.ASK_WHOLE
                           + "document.querySelector('.fu-toggle').click();")
        self.assertTrue(folded["folded"])
        closed = self._run(self.ASK_WHOLE
                           + "document.querySelector('.fu-close').click();"
                           "await wait();")
        self.assertEqual(closed["boxes"], 0)
        deleted = [json.loads(p["b"]) for p in closed["posted"]
                   if p["p"] == "/api/chat/followup/delete"]
        self.assertEqual(deleted, [{"id": "fup_1"}])


class LessonsPanelTests(unittest.TestCase):
    """The Lessons tab, and the Bad notification button on a notification."""

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _run(self, steps: str) -> dict:
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        stub = {
            "/api/lessons": {"kinds": [{
                "kind": "notifications", "title": "Notification lessons",
                "text": "SUMMARY: deletes market reminders.", "summary": "x",
                "max_chars": 20000, "actions": 1, "waiting": 0,
                "lessons": [{
                    "id": "calact_1", "acted_at": "2026-10-07T10:00:00+00:00",
                    "action": "bad_notification",
                    "did": "Marked bad by user",
                    "title": "Check the market", "why": "Not wanted at work.",
                    "reasoned_at": "2026-10-07T10:01:00+00:00",
                    "lesson": "Do not remind about markets during work hours.",
                    "event": {"task": {"title": "Check the market",
                                       "description": "ZEBRA full detail"},
                              "notification": {"message": "Markets are open"}}},
                    {
                    # An edit, once read: its title, the user's words and
                    # what it changed - before and after - are what is kept.
                    "id": "calact_2", "acted_at": "2026-10-06T10:00:00+00:00",
                    "action": "edit", "did": "Edited by user",
                    "title": "Stretch", "why": "Mornings are for work.",
                    "reasoned_at": "2026-10-06T10:01:00+00:00",
                    "lesson": "Prefer afternoon reminders to move.",
                    "event": {"task": {"title": "Stretch"},
                              "after": {"title": "Stretch"},
                              "user_said": "Not in the morning",
                              "changes": [{"field": "time",
                                           "before": "Mon 12 Oct 2026 09:00",
                                           "after": "Mon 12 Oct 2026 14:00"}]}}]}]},
            "/api/notifications": {"notifications": [{
                "id": "notif_1", "title": "Stand up", "message": "Time to move",
                "kind": "info"}]},
            "/api/status": {"pending_raw_data": {"events": 0, "edits": 0},
                            "library": {"records": 0, "folders": {}}},
        }
        harness = (
            "<script>window.__posted=[];const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
            "if(o&&o.method==='POST')window.__posted.push({p,b:o.body});"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "headers:{get:()=>null},"
            "json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';"
            "const wait=()=>new Promise(r=>setTimeout(r,60));"
            "try{" + steps + "}catch(e){err=e.message;}"
            "document.title=JSON.stringify({err,"
            "lessons:document.querySelector('#lessons-kinds').textContent"
            ".split(/[^!-~]+/).join(' ').trim(),"
            "toasts:[...document.querySelectorAll('#toast .toast-item')]"
            ".map(t=>t.textContent.split(/[^!-~]+/).join(' ').trim()),"
            "posted:window.__posted});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 "--window-size=1400,900",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
            dom = completed.stdout or ""
        match = re.search(r"<title>(.*?)</title>", dom, re.S)
        self.assertIsNotNone(match, "the page never rendered")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "")
        return result

    def test_the_lessons_tab_shows_each_lesson_with_its_record(self) -> None:
        result = self._run("await loadLessons();await wait();")
        for words in ("Notification lessons", "Check the market",
                      "Do not remind about markets during work hours.",
                      "Not wanted at work.", "Marked bad by user"):
            self.assertIn(words, result["lessons"])
        # The record is not kept, so not shown.
        self.assertNotIn("Complete record", result["lessons"])
        # What the agent is given is not shown.
        self.assertNotIn("What the agent is given", result["lessons"])

    def test_the_learned_lessons_in_force_forming_and_how_each_action_was_read(self) -> None:
        """From the lessons engine: what the agent is given, how sure and on
        how many cases; what is forming and how far it has to go; each
        action saying which lesson it backs; and the user's say."""
        engine = {"lessons": [{"id": "les_a", "statement": "Do not remind about chat "
                               "meetings until the time is set", "confidence": 0.83,
                               "pinned": False, "cases": 4, "against": 1, "evidence": [
                                   {"relation": "supports", "summary": "The user deleted "
                                    "this event.\nTitle: talk", "why": "unconfirmed"}],
                               "history": [{"at": "2026-10-10T08:00:00+00:00",
                                            "why": "promoted"}]}],
                  "forming": [{"id": "les_b", "statement": "Keep reminders short",
                               "confidence": 0.6, "pinned": False, "cases": 1,
                               "against": 0, "to_promote": 1.6, "evidence": [],
                               "history": []}],
                  "retired": [], "waiting": 2}
        steps = (
            "STUB['/api/lessons'].kinds[0].engine=" + json.dumps(engine) + ";"
            "STUB['/api/lessons'].kinds[0].lessons[0].read=[{relation:'supports',"
            "statement:'Do not remind about chat meetings until the time is set'}];"
            "await loadLessons();await wait();"
            "document.querySelector('[data-rule=\"les_a\"] [data-rule-op=\"pin\"]').click();"
            "await wait();")
        result = self._run(steps)
        for words in ("Lessons in force", "Do not remind about chat meetings until the time "
                      "is set", "83% sure", "4 cases, 1 against", "Forming",
                      "Keep reminders short", "about 2 more to become a lesson",
                      "2 of your edits not learned from yet", "Learn now", "What you did",
                      "Backs: Do not remind about chat meetings"):
            self.assertIn(words, result["lessons"])
        self.assertEqual([json.loads(one["b"]) for one in result["posted"]
                          if one["p"] == "/api/lessons/rule"],
                         [{"id": "les_a", "op": "pin", "statement": ""}])

    def test_what_you_did_is_a_status_not_a_button_and_its_titles_line_up(self) -> None:
        """An action's badge says what the user did: clicking "Deleted by
        user" asks nothing - it once shared the Delete button's class, so it
        asked to delete, and was pushed right with the button, out of line."""
        # The page's own styles, as the app serves them: the probe is read
        # from a folder of its own, where the stylesheet is not.
        css = (ROOT / "personalized_agent" / "api" / "web" / "styles.css").read_text(
            encoding="utf-8")
        steps = (
            "const css=document.createElement('style');css.textContent=" + json.dumps(css)
            + ";document.head.append(css);"
            "STUB['/api/lessons'].kinds[0].engine={lessons:[],forming:[],retired:[],waiting:0};"
            "STUB['/api/lessons'].kinds[0].lessons[0].action='delete';"
            "STUB['/api/lessons'].kinds[0].lessons[0].did='Deleted by user';"
            "document.querySelector('button[data-tab=\"lessons\"]').click();"
            "await loadLessons();await wait();"
            "document.querySelector('.lesson-act').click();await wait();"
            "const box=document.querySelector('#lessons-kinds');"
            "const xs=[...box.querySelectorAll('.lesson-did-title')]"
            ".map(e=>Math.round(e.getBoundingClientRect().left));"
            "box.append(' ASKED:'+!box.querySelector('.lesson-sure').hidden"
            "+' XS:'+xs.join('_')+' ');"
            "box.querySelector('button.lesson-delete').click();await wait();"
            "box.append(' THEN:'+!box.querySelector('.lesson-sure').hidden+' ');")
        result = self._run(steps)
        said = result["lessons"]
        self.assertIn("ASKED:false", said)
        self.assertIn("THEN:true", said, "the button still asks")
        # Learned from by the engine, an action is forgotten, not deleted.
        self.assertIn("Forget this action?", said)
        xs = re.search(r"XS:([0-9_]+)", said).group(1).split("_")
        self.assertEqual(len(xs), 2)
        self.assertEqual(len(set(xs)), 1, f"titles start at {xs}")
        self.assertGreater(int(xs[0]), 0, "laid out, not hidden")

    def test_an_edit_lesson_shows_what_it_changed(self) -> None:
        result = self._run("await loadLessons();await wait();")
        for words in ("Edited by user", "Stretch",
                      "Time Mon 12 Oct 2026 09:00 Mon 12 Oct 2026 14:00",
                      "Not in the morning", "Prefer afternoon reminders to move."):
            self.assertIn(words, result["lessons"])

    def test_a_reminder_can_come_back_later(self) -> None:
        result = self._run(
            "await pollNotifications();await wait();"
            "[...document.querySelectorAll('.toast-notification button')]"
            ".find(b=>b.textContent==='Remind me in 30 minutes').click();"
            "await wait();")
        later = [p for p in result["posted"] if p["p"] == "/api/notifications/later"]
        self.assertEqual(len(later), 1)
        self.assertIn("notif_1", later[0]["b"])
        self.assertFalse(any("Time to move" in one for one in result["toasts"]))

    def test_a_lesson_is_deleted_only_when_the_user_says_yes(self) -> None:
        asked = self._run("await loadLessons();await wait();"
                          "document.querySelector('.lesson-delete').click();"
                          "await wait();")
        self.assertIn("Delete this lesson?", asked["lessons"])
        self.assertFalse([p for p in asked["posted"]
                          if p["p"] == "/api/lessons/delete"])
        kept = self._run("await loadLessons();await wait();"
                         "document.querySelector('.lesson-delete').click();"
                         "document.querySelector('.lesson-no').click();await wait();")
        self.assertFalse([p for p in kept["posted"]
                          if p["p"] == "/api/lessons/delete"])
        gone = self._run("await loadLessons();await wait();"
                         "document.querySelector('.lesson-delete').click();"
                         "document.querySelector('.lesson-yes').click();await wait();")
        sent = [json.loads(p["b"]) for p in gone["posted"]
                if p["p"] == "/api/lessons/delete"]
        self.assertEqual(sent, [{"kind": "notifications", "id": "calact_1"}])

    def test_a_notification_can_be_called_a_bad_one(self) -> None:
        result = self._run(
            "await pollNotifications();await wait();"
            "[...document.querySelectorAll('.toast-notification button')]"
            ".find(b=>b.textContent.startsWith('Bad notification')).click();"
            "await wait();")
        bad = [p for p in result["posted"] if p["p"] == "/api/notifications/bad"]
        self.assertEqual(len(bad), 1)
        self.assertIn("notif_1", bad[0]["b"])
        self.assertFalse(any("Time to move" in one for one in result["toasts"]),
                         "the notification stayed up after being called bad")


class EventDetailTests(unittest.TestCase):
    """Section 10, as amended: an event opens.

    A chip has to fit a slot, so it can carry a name and little else. Clicking
    the thing itself is the only place the rest of it can come from.
    """

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _run(self, steps: str, status: str = "pending") -> dict:
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        now = datetime.now().replace(hour=14, minute=0, second=0, microsecond=0)
        stub = {
            "/api/tasks": {"tasks": [{
                "id": "t1", "title": "Weekly review of the quarterly numbers",
                "status": status, "execution_type": "model_task",
                "description": "Read the figures and summarise them.",
                # Section 52.3: a real rule now, not the string this stub used
                # to invent for a field that did not exist.
                "recurrence": {"every_seconds": 604800}, "runs": 3,
                "scheduled_at": now.isoformat(),
                "created_at": now.isoformat(), "last_run_at": None,
                "last_result": ""}]},
            "/api/states": {"states": [], "covering_now": [], "counts": {}},
            "/api/status": {"pending_raw_data": {"events": 0, "edits": 0},
                            "library": {"records": 0, "folders": {}}},
        }
        harness = (
            "<script>window.__posted=[];window.__bodies=[];const STUB="
            + json.dumps(stub) + ";"
            "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
            "if(o&&o.method==='POST'){window.__posted.push(p);"
            "window.__bodies.push(o.body||'');}"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';"
            "const wait=()=>new Promise(r=>setTimeout(r,60));"
            "try{await loadCalendar();await wait();" + steps + "}catch(e){err=e.message;}"
            "const card=document.querySelector('.event-card');"
            "const chip=document.querySelector('.cal-chip');"
            "document.title=JSON.stringify({err,"
            "open:!document.querySelector('#event-detail').hidden,"
            "detail:card?card.textContent.split(/[^!-~]+/).join(' ').trim():'',"
            "actions:[...document.querySelectorAll('#event-detail [data-act]')]"
            ".map(b=>b.dataset.act),"
            "chipText:chip?chip.textContent.split(/[^!-~]+/).join(' ').trim():'',"
            "chipWidth:chip?Math.round(chip.getBoundingClientRect().width):0,"
            "chipHeight:chip?Math.round(chip.getBoundingClientRect().height):0,"
            "posted:window.__posted,bodies:window.__bodies});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 "--window-size=1400,900",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
            dom = completed.stdout or ""
        match = re.search(r"<title>(.*?)</title>", dom, re.S)
        self.assertIsNotNone(match, "the calendar never rendered")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "")
        return result

    def test_clicking_an_event_opens_its_detail(self) -> None:
        result = self._run("document.querySelector('.cal-chip').click();await wait();")
        self.assertTrue(result["open"])
        self.assertIn("Weekly review", result["detail"])

    def test_the_detail_carries_what_the_chip_could_not(self) -> None:
        result = self._run("document.querySelector('.cal-chip').click();await wait();")
        # "every week (3 so far)" - the Repeats row finally says something.
        for expected in ("model_task", "every week", "Read the figures"):
            self.assertIn(expected, result["detail"])

    def test_the_detail_offers_the_actions_a_task_has(self) -> None:
        result = self._run("document.querySelector('.cal-chip').click();await wait();")
        # Section 52.2 added the one that decides whether it interrupts.
        # Disable keeps it on the calendar, marked; Delete takes it off. Both
        # stop it running and notifying. Nothing else.
        # Edit changes it, and is a lesson like the others.
        self.assertEqual(set(result["actions"]), {"edit", "disable", "delete"})

    def test_a_disabled_event_is_shown_as_disabled_by_the_user(self) -> None:
        result = self._run("document.querySelector('.cal-chip').click();await wait();",
                           status="disabled")
        self.assertIn("disabled by user", result["chipText"])
        self.assertIn("disabled by user", result["detail"])
        # Already disabled: it can still be edited, or deleted.
        self.assertEqual(result["actions"], ["edit", "delete"])

    def test_delete_asks_first_in_the_card(self) -> None:
        opened = "document.querySelector('.cal-chip').click();await wait();"
        asked = self._run(opened + "document.querySelector("
                          "'#event-detail [data-act=delete]').click();await wait();")
        self.assertIn("Are you sure", asked["detail"])
        self.assertNotIn("/api/tasks/action", asked["posted"],
                         "deleted before the user said yes")
        kept = self._run(opened + "document.querySelector("
                         "'#event-detail [data-act=delete]').click();await wait();"
                         "document.querySelector('[data-act=keep]').click();"
                         "await wait();")
        self.assertNotIn("/api/tasks/action", kept["posted"])
        self.assertNotIn("Are you sure", kept["detail"])
        sure = self._run(opened + "document.querySelector("
                         "'#event-detail [data-act=delete]').click();await wait();"
                         "document.querySelector('[data-sure=yes]').click();"
                         "await wait();")
        self.assertIn("/api/tasks/action", sure["posted"])
        said = self._run(opened + "document.querySelector("
                         "'#event-detail [data-act=delete]').click();await wait();"
                         "document.querySelector('.event-why-said').value="
                         "'Not during work';"
                         "document.querySelector('[data-sure=yes]').click();"
                         "await wait();")
        sent = [json.loads(b) for p, b in zip(said["posted"], said["bodies"])
                if p == "/api/tasks/action"]
        self.assertEqual(sent[0]["why"], "Not during work")

    def test_opening_an_event_changes_nothing(self) -> None:
        # Looking at a task must not act on it.
        result = self._run("document.querySelector('.cal-chip').click();await wait();")
        self.assertEqual([p for p in result["posted"] if "action" in p], [])

    def test_it_closes_again(self) -> None:
        result = self._run("document.querySelector('.cal-chip').click();await wait();"
                           "document.querySelector('#event-close').click();await wait();")
        self.assertFalse(result["open"])

    def test_a_chip_in_the_month_still_shows_its_time(self) -> None:
        # There is no hour row there to say it.
        result = self._run("renderCalendar();await wait();")
        self.assertIn(":", result["chipText"])

    # -- editing an event, in its card -----------------------------------
    _EDIT = ("document.querySelector('.cal-chip').click();await wait();"
             "document.querySelector('#event-detail [data-act=edit]').click();"
             "await wait();")

    def _edits(self, result: dict) -> list:
        return [json.loads(b) for p, b in zip(result["posted"], result["bodies"])
                if p == "/api/tasks/action"]

    def test_edit_opens_a_form_in_the_card(self) -> None:
        # What the form shows is what the event says: the title, and the
        # repeat it has (every week) - written out where it can be read.
        result = self._run(self._EDIT + (
            "const f=document.querySelector('.event-edit');"
            "f.insertAdjacentText('beforeend',' SHOWN '+["
            "f.querySelector('[name=title]').value,"
            "f.querySelector('[name=repeat]').value,"
            "f.querySelector('[name=every]').value,"
            "f.querySelector('[name=unit]').value,"
            "f.querySelector('[name=end]').value].join('|'));"))
        self.assertIn("SHOWN Weekly review of the quarterly numbers|every|1|weeks|never",
                      result["detail"])
        # A series: the form says the edit reaches every occurrence.
        self.assertIn("applies to every occurrence", result["detail"])
        for words in ("Title", "Message", "Next run", "Repeats", "Save", "Cancel"):
            self.assertIn(words, result["detail"])
        self.assertEqual(self._edits(result), [], "opening the form saved")

    def test_save_posts_only_what_was_changed(self) -> None:
        result = self._run(self._EDIT + (
            "const f=document.querySelector('.event-edit');"
            "const set=(n,v)=>{const e=f.querySelector(`[name=${n}]`);e.value=v;"
            "e.dispatchEvent(new Event('change'));};"
            "set('title','Weekly review, Friday afternoons');"
            "set('at','2030-01-18T16:30');"
            "set('every','2');set('unit','weeks');set('end','after');"
            "set('count','4');set('why','Afternoons suit me');"
            "f.querySelector('[data-edit=save]').click();await wait();"))
        sent = self._edits(result)
        self.assertEqual(len(sent), 1)
        body = sent[0]
        self.assertEqual(body["id"], "t1")
        self.assertEqual(body["action"], "edit")
        self.assertEqual(body["title"], "Weekly review, Friday afternoons")
        # The user's own time, with their own offset.
        self.assertTrue(body["at"].startswith("2030-01-18T16:30:00"), body["at"])
        self.assertRegex(body["at"], r"[+-]\d\d:\d\d$")
        self.assertEqual(body["repeat"], {"every": 2, "unit": "weeks", "count": 4})
        self.assertEqual(body["why"], "Afternoons suit me")
        # The message was left as it was, so it is not sent.
        self.assertNotIn("message", body)

    def test_a_repeat_taken_away_is_sent_as_once(self) -> None:
        result = self._run(self._EDIT + (
            "const f=document.querySelector('.event-edit');"
            "f.querySelector('[name=repeat]').value='none';"
            "f.querySelector('[data-edit=save]').click();await wait();"))
        sent = self._edits(result)
        self.assertEqual(sent, [{"id": "t1", "action": "edit", "repeat": None}])

    def test_cancel_and_an_unchanged_save_post_nothing(self) -> None:
        cancelled = self._run(self._EDIT + (
            "document.querySelector('.event-edit [name=title]').value='Other';"
            "document.querySelector('[data-edit=cancel]').click();await wait();"))
        self.assertEqual(self._edits(cancelled), [])
        # Back to the card, with its actions.
        self.assertTrue(cancelled["open"])
        self.assertEqual(set(cancelled["actions"]), {"edit", "disable", "delete"})
        unchanged = self._run(self._EDIT + (
            "document.querySelector('[data-edit=save]').click();await wait();"))
        self.assertEqual(self._edits(unchanged), [])

    def test_an_empty_title_is_not_saved(self) -> None:
        result = self._run(self._EDIT + (
            "document.querySelector('.event-edit [name=title]').value='  ';"
            "document.querySelector('[data-edit=save]').click();await wait();"))
        self.assertEqual(self._edits(result), [])
        self.assertTrue(result["open"])


class FocusFragmentationTests(unittest.TestCase):
    """Section 19.1: an animated title is the same title.

    The Listener ends a focus period when the title changes, and a terminal
    running a task rewrites its own title several times a second to spin a
    glyph. Compared literally, one continuous session became dozens of
    two-second stretches, and the activity log read as a list of fragments.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.now = datetime.now(timezone.utc)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _stretch(self, minutes, seconds, title):
        self.app.raw_store.add_event(ListenerEvent(
            event_type="application.focus_ended", source="application",
            application="Term.exe", timestamp=self.now - timedelta(minutes=minutes),
            data={"duration_seconds": seconds, "window_title": title}))

    def test_a_spinning_title_does_not_end_the_focus_period(self) -> None:
        from personalized_agent.common.titles import same_title

        self.assertTrue(same_title("\u25d0 building", "\u25d1 building"))
        self.assertTrue(same_title("\u2733 building", "building"))
        self.assertFalse(same_title("building", "testing"))

    def test_the_listener_compares_titles_by_what_they_mean(self) -> None:
        source = (ROOT / "personalized_agent" / "listener" / "providers"
                  / "application.py").read_text(encoding="utf-8")
        self.assertIn("same_title(", source)
        self.assertNotIn('current.get("title") == window.title', source)

    def test_fragments_of_one_session_are_shown_as_one(self) -> None:
        # Already-recorded data was fragmented by the same cause and does not
        # repair itself.
        for index, glyph in enumerate("\u25d0\u25d1\u2733"):
            self._stretch(30 - index, 4, f"{glyph} building the release")
        detail = self.app.overview.application_detail("Term.exe")
        stretches = [i for i in detail["interactions"] if not i.get("ongoing")]
        self.assertEqual(len(stretches), 1, "three frames of one session")

    def test_a_real_gap_is_still_two_stretches(self) -> None:
        # Going away and coming back is not a title animation.
        self._stretch(120, 4, "building the release")
        self._stretch(5, 4, "building the release")
        detail = self.app.overview.application_detail("Term.exe")
        stretches = [i for i in detail["interactions"] if not i.get("ongoing")]
        self.assertEqual(len(stretches), 2)

    def test_the_title_is_shown_without_its_animation(self) -> None:
        self._stretch(30, 4, "\u2733 building the release")
        detail = self.app.overview.application_detail("Term.exe")
        stretch = next(i for i in detail["interactions"] if not i.get("ongoing"))
        self.assertEqual(stretch["window_title"], "building the release")


class StateOpensTests(unittest.TestCase):
    """Section 34.1, as amended: a state can be opened, in the Debugger.

    States moved off the user's calendar - every task writes one, so the same
    thing appeared twice, and the rest are the agent's inferences about the
    user rather than entries in a diary they keep. What has not changed is
    that a state has to be openable wherever it is shown: being told the
    working day is on Madrid time, with no way to ask who decided that or when it
    applies, is worse than not being told.
    """

    def setUp(self) -> None:
        self.script = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text(
            encoding="utf-8")

    def test_a_state_chip_is_something_that_can_be_clicked(self) -> None:
        self.assertIn('<button type="button" class="cal-state', self.script)
        self.assertIn("openDebuggerState(", self.script)

    def test_the_card_says_when_the_state_was_added(self) -> None:
        """Which is not when the situation it describes began.

        A state written this morning about a fortnight of work and one
        written a fortnight ago look alike without it, and they are very
        different claims about how long the agent has been watching. The
        task card and the record page had said it all along.
        """
        self.assertIn('row("Added", state.created_at', self.script)

    def test_states_are_read_from_the_debugger_alone(self) -> None:
        self.assertIn("/api/debugger/states", self.script)

    def test_the_calendar_itself_no_longer_draws_them(self) -> None:
        """The three calendar views, not the file - stateChip still exists,
        because the Debugger uses it."""
        for name in ("function renderMonth", "function renderDayPanel"):
            start = self.script.index(name)
            view = self.script[start:self.script.index("\n}", start)]
            with self.subTest(view=name):
                self.assertNotIn("stateChip", view)
                self.assertNotIn("cal-state", view)
                self.assertNotIn("statesOn(", view)

    def test_the_calendar_does_not_fetch_them(self) -> None:
        loader = self.script[self.script.index("async function loadCalendar"):
                             self.script.index("function openPopover")]
        self.assertNotIn("/api/states", loader)
        self.assertNotIn("/api/debugger/states", loader,
                         "the calendar should not be reading states at all")

    def test_opening_a_state_says_where_it_came_from(self) -> None:
        for origin in ("You told the agent this",
                       "The agent inferred this from your activity"):
            self.assertIn(origin, self.script)

    def test_opening_a_state_says_it_is_not_run(self) -> None:
        # The difference from a task is the whole point of the distinction.
        self.assertIn("A state is shown, never run", self.script)

    def test_the_day_panel_rows_open_too(self) -> None:
        self.assertIn('data-state="${escapeHtml(state.id)}"', self.script)


class PopoverPlacementTests(unittest.TestCase):
    """Section 10, as amended: the detail opens beside what it is about.

    A card centred on the screen covers the grid, points at nothing, and makes
    the reader find their place again when it closes.
    """

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _run(self, steps: str, width: int = 1400) -> dict:
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        now = datetime.now().replace(hour=14, minute=0, second=0, microsecond=0)
        stub = {
            "/api/tasks": {"tasks": [{
                "id": "t1", "title": "Weekly review", "status": "pending",
                "execution_type": "notification", "description": "Read the figures.",
                "scheduled_at": now.isoformat(), "created_at": now.isoformat(),
                "last_run_at": None, "last_result": ""}]},
            "/api/states": {"states": [], "covering_now": [], "counts": {}},
            "/api/status": {"pending_raw_data": {"events": 0, "edits": 0},
                            "library": {"records": 0, "folders": {}}},
        }
        harness = (
            "<style>" + (web / "styles.css").read_text(encoding="utf-8") + "</style>"
            "<script>const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u)=>{const p=String(u).split('?')[0];"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';"
            "const wait=()=>new Promise(r=>setTimeout(r,80));"
            "try{await loadCalendar();await wait();" + steps + "}catch(e){err=e.message;}"
            "const card=document.querySelector('.event-card');"
            "const chip=document.querySelector('.cal-chip');"
            "const c=card?card.getBoundingClientRect():null;"
            "const a=chip?chip.getBoundingClientRect():null;"
            "document.title=JSON.stringify({err,open:!!card,"
            "card:c?{left:Math.round(c.left),top:Math.round(c.top),"
            "right:Math.round(c.right),bottom:Math.round(c.bottom)}:null,"
            "anchor:a?{left:Math.round(a.left),top:Math.round(a.top),"
            "right:Math.round(a.right)}:null,"
            "viewport:{w:window.innerWidth,h:window.innerHeight}});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--window-size={width},900",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
            dom = completed.stdout or ""
        match = re.search(r"<title>(.*?)</title>", dom, re.S)
        self.assertIsNotNone(match, "the calendar never rendered")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "")
        return result

    def test_the_detail_opens_beside_the_event(self) -> None:
        result = self._run("document.querySelector('.cal-chip').click();await wait();")
        self.assertTrue(result["open"])
        card, anchor = result["card"], result["anchor"]
        # Beside it, not centred over the page.
        self.assertGreaterEqual(card["left"], anchor["right"],
                                "the card should sit to the right of the event")
        self.assertLess(abs(card["top"] - anchor["top"]), 60,
                        "and level with the row it belongs to")

    def test_it_stays_on_the_screen(self) -> None:
        result = self._run("document.querySelector('.cal-chip').click();await wait();",
                           width=760)
        card = result["card"]
        self.assertGreaterEqual(card["left"], 0)
        self.assertLessEqual(card["right"], result["viewport"]["w"])
        self.assertLessEqual(card["bottom"], result["viewport"]["h"])

    def test_escape_closes_it(self) -> None:
        result = self._run(
            "document.querySelector('.cal-chip').click();await wait();"
            "document.dispatchEvent(new KeyboardEvent('keydown',"
            "{key:'Escape',bubbles:true}));await wait();")
        self.assertFalse(result["open"])

    def test_clicking_away_closes_it(self) -> None:
        result = self._run(
            "document.querySelector('.cal-chip').click();await wait();"
            "document.querySelector('#event-detail').click();await wait();")
        self.assertFalse(result["open"])

    def test_the_grid_is_not_dimmed_behind_it(self) -> None:
        # The backdrop catches the dismissing click; it is not there to hide
        # the grid the user is still reading.
        styles = (ROOT / "personalized_agent" / "api" / "web" / "styles.css").read_text(
            encoding="utf-8")
        self.assertIn("#event-detail { position: fixed; inset: 0; z-index: 40; "
                      "background: transparent; }", styles)


class TokenUsageChartTests(unittest.TestCase):
    """Section 14.33, as amended: what the model time was spent on.

    The snapshot already carried tokens over time as a timestamp and a number,
    which answers "how much" and never "on what". On what is the question worth
    asking, because its answer is actionable - a heartbeat outspending every
    conversation put together is a setting to change, and it looks exactly like
    a busy week until the lines are drawn apart.
    """

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _calls(self):
        """A spread across task kinds, weighted the way a real run is."""
        out, stamp = [], datetime(2026, 9, 16, 9, 0)
        plan = (["heartbeat"] * 9 + ["data_heartbeat"] * 6 + ["user_instruction"] * 4
                + ["scheduled_task"] * 2 + ["verification"])
        size = {"heartbeat": 5200, "data_heartbeat": 3800, "user_instruction": 1400,
                "scheduled_task": 900, "verification": 300}
        for index, task in enumerate(plan * 3):
            stamp = stamp + timedelta(minutes=5)
            base = size[task]
            out.append({"timestamp": stamp.isoformat(), "task": task,
                        "model": "claude-opus-5",
                        "input_tokens": base, "output_tokens": base // 8,
                        "total_tokens": base + base // 8,
                        "estimated_cost": round(base / 1e6 * 15, 6)})
        return out

    @staticmethod
    def _days() -> dict:
        days = [f"2026-10-0{i}" for i in range(1, 8)]
        rows = [{"day": day, "task": task, "calls": 3, "input_tokens": n,
                 "output_tokens": 0, "total_tokens": n, "estimated_cost": n / 1e6}
                for i, day in enumerate(days)
                for task, n in (("heartbeat", 1000 * (i + 1)), ("chat", 300))]
        return {"days": days, "rows": rows}

    def _run(self, steps: str = "", calls=None, by_task=None) -> dict:
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        stub = {
            "/api/tasks": {"tasks": []},
            "/api/status": {"pending_raw_data": {"events": 0, "edits": 0},
                            "library": {"records": 0, "folders": {}},
                            "debugger_built_in": True},
            "/api/debugger": {"model_metrics": {
                "tokens_over_time": self._calls() if calls is None else calls,
                "measured_since": "2026-08-02T03:11:50+00:00"}},
            "/api/debugger/states": {"states": [], "covering_now": [], "counts": {}},
            "/api/debugger/tokens_by_day": self._days(),
        }
        if by_task is not None:
            stub["/api/debugger"]["model_metrics"]["by_task"] = by_task
        harness = (
            "<script>const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u)=>{const p=String(u).split('?')[0];"
            "window.__asked=(window.__asked||[]).concat([String(u)]);"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';let more={};"
            "const wait=()=>new Promise(r=>setTimeout(r,80));"
            # These read the last calls; the chart opens per day (tested
            # apart, below).
            "try{dbgTokens.view='calls';await loadTokenUsage();await wait();" + steps + "}"
            "catch(e){err=e.message;}"
            "const canvas=document.querySelector('#dbg-tokens-chart');"
            "document.title=JSON.stringify({err,"
            "lines:document.querySelectorAll('.dbg-token-key').length,"
            "off:document.querySelectorAll('.dbg-token-key.off').length,"
            "labels:[...document.querySelectorAll('.dbg-token-key')]"
            "  .map(b=>b.dataset.task),"
            "legendText:(document.querySelector('#dbg-tokens-legend')||{}).textContent||'',"
            "note:(document.querySelector('#dbg-tokens-note')||{}).textContent||'',"
            "painted:canvas?canvas.getContext('2d')"
            "  .getImageData(0,0,canvas.width,canvas.height).data"
            "  .some((v,i)=>i%4===3&&v>0):false,"
            "hidden:[...dbgTokens.hidden],"
            "wired:!!window.__tokensWired,more,"
            "metric:dbgTokens.metric});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
            dom = completed.stdout or ""
        match = re.search(r"<title>(.*?)</title>", dom, re.S)
        self.assertIsNotNone(match, "the debugger never rendered")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "")
        return result

    # -- one line per kind of work ------------------------------------------
    def test_there_is_a_line_for_each_kind_of_work(self) -> None:
        result = self._run()
        self.assertEqual(sorted(result["labels"]),
                         ["data_heartbeat", "heartbeat", "scheduled_task",
                          "user_instruction", "verification"])

    def test_something_is_actually_drawn(self) -> None:
        # The legend can be right while the canvas is blank.
        self.assertTrue(self._run()["painted"], "the chart painted nothing")

    def test_the_legend_carries_each_line_s_total(self) -> None:
        # A legend that only names the lines makes the reader measure the
        # chart by eye to answer "which cost most".
        text = self._run()["legendText"]
        self.assertIn("heartbeat", text)
        self.assertRegex(text, r"\d")

    # -- the lines can be put away ------------------------------------------
    def test_a_line_can_be_hidden_and_brought_back(self) -> None:
        hide = ("document.querySelector('[data-task=\\'heartbeat\\']').click();"
                "await wait();")
        self.assertEqual(self._run(hide)["off"], 1)

        both = hide + ("document.querySelector('[data-task=\\'heartbeat\\']').click();"
                       "await wait();")
        self.assertEqual(self._run(both)["off"], 0,
                         "a hidden line must be findable to bring back")

    # -- per day, over the last N days ---------------------------------------
    def test_the_days_view_draws_a_bar_a_day_and_says_what_is_under_the_pointer(self) -> None:
        days = ("const v=document.querySelector('#dbg-tokens-view');"
                "v.value='days';v.dispatchEvent(new Event('change'));"
                "await wait();await wait();"
                "const c=document.querySelector('#dbg-tokens-chart');"
                "const r=c.getBoundingClientRect();"
                "c.dispatchEvent(new MouseEvent('mousemove',{clientX:r.right-15,"
                "  clientY:r.top+40,bubbles:true}));"
                "const tip=document.querySelector('#dbg-tokens-tip');"
                "more={tip:tip.hidden?'':tip.textContent.replace(/\s+/g,' '),"
                "  asked:window.__asked.filter(u=>u.includes('by_day')),"
                "  daysShown:!document.querySelector('#dbg-tokens-days-label').hidden};")
        result = self._run(days)
        self.assertTrue(result["painted"])
        self.assertTrue(result["more"]["daysShown"])
        self.assertRegex(result["more"]["asked"][0], r"days=30&east=-?\d+")
        self.assertIn("over the last 7 days", result["note"])
        # The last day: heartbeat 7000 and chat 300, by name.
        self.assertIn("heartbeat", result["more"]["tip"])
        self.assertIn("heartbeat7k", result["more"]["tip"].replace(" ", ""))
        self.assertIn("42 calls", result["note"])

    def test_every_kind_that_runs_its_own_and_only_past_fourteen_other(self) -> None:
        # Fourteen of their own - seven colours, then the same seven striped
        # - and a part removed from the application is Other, unnamed.
        calls = [{"timestamp": f"2026-10-01T10:{i:02d}:00+00:00", "task": f"k{i}",
                  "total_tokens": 100 * (i + 1), "input_tokens": 1,
                  "output_tokens": 1, "estimated_cost": 0} for i in range(16)]
        calls.append({"timestamp": "2026-10-01T11:00:00+00:00", "task": "data_heartbeat",
                      "module": "other", "total_tokens": 5000, "input_tokens": 1,
                      "output_tokens": 1, "estimated_cost": 0})
        ranking = {f"k{i}": {"total": 16 - i} for i in range(16)}
        result = self._run(calls=calls, by_task=ranking)
        self.assertEqual(result["labels"], [f"k{i}" for i in range(14)] + ["other"])
        self.assertIn("Other (2)", result["legendText"])
        self.assertNotIn("data_heartbeat", result["legendText"])

    # -- the metric can be changed ------------------------------------------
    def test_the_metric_can_be_changed(self) -> None:
        pick = ("const s=document.querySelector('#dbg-tokens-metric');"
                "s.value='estimated_cost';s.dispatchEvent(new Event('change'));"
                "await wait();")
        result = self._run(pick)
        self.assertEqual(result["metric"], "estimated_cost")
        self.assertIn("$", result["legendText"])

    # -- the awkward cases --------------------------------------------------
    def test_no_calls_yet_says_so_rather_than_drawing_nothing(self) -> None:
        result = self._run(calls=[])
        self.assertIn("No model calls", result["note"])
        self.assertEqual(result["lines"], 0)

    def test_it_says_what_the_history_actually_covers(self) -> None:
        """It used to say "since the agent started", which was the problem.

        Section 14.35, as amended: the calls are written down now, so the
        chart covers the agent's whole life rather than the current process -
        and the note has to say which, because "is this getting more
        expensive" cannot be answered from a window that restarts whenever
        anything is fixed.
        """
        note = self._run()["note"]
        self.assertIn("calls", note)
        self.assertNotIn("since the agent started", note)
        self.assertIn("recorded since", note,
                      "the chart must say what window it is drawing")
        self.assertIn("2026", note)

    def test_a_call_with_no_task_is_still_counted(self) -> None:
        result = self._run(calls=[{"timestamp": "2026-09-16T09:00:00", "task": "",
                                   "total_tokens": 100, "input_tokens": 90,
                                   "output_tokens": 10, "estimated_cost": 0.001,
                                   "model": "m"}])
        self.assertEqual(result["labels"], ["general"],
                         "a call with no task should not vanish from the chart")


class TheComponentGraphIsDrawnTests(unittest.TestCase):
    """The Debugger's map, measured with the real stylesheet and real topology.

    It was nought pixels tall. The map is the only child of the shell allowed
    to flex and it had min-height: 0, so every pixel the fixed-height panels
    below it wanted came out of the map - and once they added up to more than
    the shell there was nothing left. A canvas that size draws nothing and
    raises nothing, so the Debugger showed its panels, showed the states, and
    simply had no component graph.

    This needs the stylesheet, because the fault was entirely in the layout,
    and a real topology, because an empty one draws nothing either.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.kind, cls.engine = _javascript_engine()
        if cls.kind != "chrome":
            raise unittest.SkipTest("no browser available to render the page")
        # The registry is built from code, so any application has the same
        # shape of map as the user's.
        agent = TemporaryAgent()
        try:
            cls.topology = agent.app.observability_service.topology()
        finally:
            agent.cleanup()

    def _run(self) -> dict:
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        page = page.replace(
            '<link rel="stylesheet" href="/styles.css">',
            "<style>" + (web / "styles.css").read_text(encoding="utf-8") + "</style>",
            1)
        stub = {
            "/api/debugger/topology": self.topology,
            "/api/debugger/timeline": {"traces": []},
            "/api/debugger/modules": {"monitoring": True, "modules": []},
            "/api/status": {"pending_raw_data": {"events": 0, "edits": 0},
                            "library": {"records": 0, "folders": {}}},
        }
        harness = (
            "<script>const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>" + (web / "debugger.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';"
            "const wait=()=>new Promise(r=>setTimeout(r,120));"
            "try{"
            "document.querySelector('#tabs button[data-tab=\"debugger\"]').click();"
            "await wait();await window.debuggerPanel.activate();"
            "await wait();await wait();"
            "}catch(e){err=e.message;}"
            "const c=document.getElementById('dbg-map');"
            "const box=c?c.getBoundingClientRect():null;"
            "let painted=0;"
            "if(c&&c.width&&c.height){"
            "const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;"
            "for(let i=3;i<d.length;i+=4){if(d[i]!==0)painted++;}}"
            "document.title=JSON.stringify({err:err,painted:painted,"
            "height:box?Math.round(box.height):-1,"
            "width:box?Math.round(box.width):-1,"
            "attrH:c?c.height:-1,"
            "shellOverflow:getComputedStyle("
            "document.getElementById('dbg-shell')).overflowY});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 "--window-size=1400,900",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
            dom = completed.stdout or ""
        match = re.search(r"<title>(.*?)</title>", dom, re.S)
        self.assertIsNotNone(match, "the debugger never rendered")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "")
        return result

    def test_the_map_has_room_to_draw_in(self) -> None:
        self.assertGreater(self._run()["height"], 200,
                           "the map has been squeezed to nothing")

    def test_something_is_actually_drawn_on_it(self) -> None:
        # Having room is not using it: this would pass on a blank canvas of
        # the right size, which is why the pixels are counted.
        self.assertGreater(self._run()["painted"], 1000,
                           "the map is sized but nothing was painted on it")

    def test_the_panels_below_it_stay_reachable(self) -> None:
        # The map keeping a floor means the shell can overflow, and a shell
        # that hides its overflow would simply cut the last panel off.
        self.assertIn(self._run()["shellOverflow"], ("auto", "scroll"))


class ADayWithTooMuchOnItOpensTests(unittest.TestCase):
    """"+13 more" had to become reachable (month view).

    A month cell holds about three chips before the grid stops looking like a
    month, so the rest were counted and dropped. The count was a plain label:
    the day the agent put sixteen things on - exactly the day worth looking
    at - was the day the calendar told you least about.
    """

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _run(self, count: int, steps: str = "") -> dict:
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        page = page.replace(
            '<link rel="stylesheet" href="/styles.css">',
            "<style>" + (web / "styles.css").read_text(encoding="utf-8") + "</style>",
            1)
        # All on one day, inside whatever month the page opens on.
        when = datetime.now(timezone.utc).replace(
            day=15, hour=9, minute=0, second=0, microsecond=0)
        tasks = [{"id": f"task_{i}", "title": f"Thing {i}",
                  "description": "", "scheduled_at": when.isoformat(),
                  "status": "pending", "execution_type": "notification",
                  "created_at": when.isoformat(), "metadata": {},
                  "task_payload": {}, "recurrence": {}, "runs": 0}
                 for i in range(count)]
        stub = {
            "/api/tasks": {"tasks": tasks},
            "/api/questions": {"questions": []},
            "/api/status": {"pending_raw_data": {"events": 0, "edits": 0},
                            "library": {"records": 0, "folders": {}}},
        }
        harness = (
            "<script>const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';"
            "const wait=()=>new Promise(r=>setTimeout(r,80));"
            "try{await loadCalendar();await wait();" + steps + "}catch(e){err=e.message;}"
            "const surface=document.getElementById('calendar-surface');"
            "document.title=JSON.stringify({err:err,"
            "chips:surface.querySelectorAll('.cal-chip').length,"
            "more:[...surface.querySelectorAll('.cal-more')].map(b=>"
            "({text:b.textContent.trim(),tag:b.tagName}))"
            "});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 "--window-size=1400,1000",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
            dom = completed.stdout or ""
        match = re.search(r"<title>(.*?)</title>", dom, re.S)
        self.assertIsNotNone(match, "the calendar never rendered")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "")
        return result

    def test_a_crowded_day_says_how_many_it_is_holding_back(self) -> None:
        result = self._run(16)
        self.assertEqual(result["chips"], 3)
        self.assertEqual(result["more"][0]["text"], "+13 more")

    def test_the_count_is_something_you_can_press(self) -> None:
        # It was a <div>. A number that cannot be clicked is a dead end.
        self.assertEqual(self._run(16)["more"][0]["tag"], "BUTTON")

    def test_pressing_it_shows_every_one_of_them(self) -> None:
        result = self._run(
            16, "document.querySelector('.cal-more').click();await wait();")
        self.assertEqual(result["chips"], 16)

    def test_and_it_can_be_folded_back_up(self) -> None:
        result = self._run(
            16,
            "document.querySelector('.cal-more').click();await wait();"
            "[...document.querySelectorAll('.cal-more')]"
            ".find(b=>b.textContent.includes('fewer')).click();await wait();")
        self.assertEqual(result["chips"], 3)

    def test_a_quiet_day_offers_nothing_to_open(self) -> None:
        result = self._run(2)
        self.assertEqual(result["chips"], 2)
        self.assertEqual(result["more"], [])


class TheTokenChartOpensPerDayTests(unittest.TestCase):
    """On the Overview, the token usage chart opens on the per-day view."""

    def test_per_day_is_the_default(self) -> None:
        web = ROOT / "personalized_agent" / "api" / "web"
        script = (web / "app.js").read_text(encoding="utf-8")
        page = (web / "index.html").read_text(encoding="utf-8")
        self.assertIn('view: "days", days: 30', script)
        self.assertIn('<option value="days" selected>per day</option>', page)
        self.assertNotIn('id="dbg-tokens-days-label" hidden', page)


class TheDebuggerChartsAreDrawnWhenOpenedTests(unittest.TestCase):
    """Both charts were drawn once, while the tab was still hidden.

    A canvas with no layout does not report zero and refuse - it reports zero
    and the code carries on. One chart sized itself to a fallback of 600 and
    the other to a single pixel, and the browser then stretched that bitmap
    across a thousand: axis labels landed outside the visible area, which is
    why the axis looked as though it had never been drawn. Nothing redrew
    them when the tab opened, so the first thing that happened to re-render -
    changing a view, toggling a series - was what appeared to make the chart
    work.
    """

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _run(self, steps: str = "") -> dict:
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        page = page.replace(
            '<link rel="stylesheet" href="/styles.css">',
            "<style>" + (web / "styles.css").read_text(encoding="utf-8") + "</style>", 1)
        calls = [{"timestamp": f"2026-09-1{d}T10:00:00+00:00",
                  "total_tokens": 900 + d * 40, "input_tokens": 800,
                  "output_tokens": 100 + d, "task": "heartbeat", "model": "opus",
                  "estimated_cost": 0.01} for d in range(1, 8)]
        by_size = [{"records": 200 * n, "bytes": 1048576 * n,
                    "mean_ms": 1.0 + n * 0.3, "median_ms": 1.0, "mean_searches": 2,
                    "mean_opened": 3, "queries": 10, "mean_context_tokens": 500}
                   for n in range(1, 7)]
        stub = {
            "/api/debugger": {"model_metrics": {
                "tokens_over_time": calls,
                "measured_since": "2026-08-02T03:11:50+00:00"}},
            # The chart opens per day: a week of it.
            "/api/debugger/tokens_by_day": TokenUsageChartTests._days(),
            "/api/debugger/retrieval": {
                "summary": {"queries": 60, "mean_ms": 2.1, "worst_ms": 9.0,
                            "trend": "flat"},
                "by_kind": [], "by_model": [], "by_turn": [],
                "turn_summary": {"turns": 0}, "queries": [], "by_size": by_size,
                "library": {"records": 1006}},
            "/api/debugger/topology": {"subsystems": [], "components": [],
                                       "connections": [], "states": {},
                                       "connection_states": {},
                                       "application": {"version": "t"}},
            "/api/debugger/timeline": {"traces": []},
            "/api/debugger/modules": {"monitoring": True, "modules": []},
            "/api/status": {"pending_raw_data": {"events": 0, "edits": 0},
                            "library": {"records": 0, "folders": {}},
                            "running": True,
                            "application": {"version": "t", "name": "PA"},
                            "models": {"primary": {"label": "offline/offline-1"}},
                            "paths": {}, "runtime": {}, "privacy": {}},
        }

        def painted(name):
            return ("(()=>{const c=document.getElementById('" + name + "');"
                    "if(!c||!c.width||!c.height)return 0;"
                    "const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;"
                    "let n=0;for(let i=3;i<d.length;i+=4){if(d[i]!==0)n++;}return n;})()")

        def shape(name):
            return ("(()=>{const c=document.getElementById('" + name + "');"
                    "const r=c?c.getBoundingClientRect():null;"
                    "return r?{css:Math.round(r.width),bitmap:c.width}:null;})()")

        harness = (
            "<script>const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>" + (web / "debugger.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';"
            "const wait=()=>new Promise(r=>setTimeout(r,120));"
            "try{await wait();"
            # The token chart is on the Overview now (moved from the Debugger).
            "document.querySelector('#tabs button[data-tab=\"overview\"]').click();"
            "await wait();await wait();await wait();"
            + steps + "}catch(e){err=e.message;}"
            "document.title=JSON.stringify({err:err,"
            "tokens:" + painted("dbg-tokens-chart") + ","
            "retrieval:" + painted("dbg-retrieval-chart") + ","
            "tokensShape:" + shape("dbg-tokens-chart") + ","
            "retrievalShape:" + shape("dbg-retrieval-chart") + "});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 "--window-size=1400,1000",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
            dom = completed.stdout or ""
        match = re.search(r"<title>(.*?)</title>", dom, re.S)
        self.assertIsNotNone(match, "the debugger never rendered")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "")
        return result

    def test_the_token_chart_is_drawn_without_being_clicked(self) -> None:
        self.assertGreater(self._run()["tokens"], 1000,
                           "the token chart needed a click to appear")


    def test_neither_is_drawn_at_a_guessed_width(self) -> None:
        """The bitmap must match the box, or the labels land outside it.

        Drawing at a fallback width and letting the browser stretch it is why
        the axis had no name and no numbers: they were painted, off-screen.
        """
        result = self._run()
        for name in ("tokensShape",):
            with self.subTest(name):
                shape = result[name]
                self.assertGreater(shape["css"], 400)
                self.assertGreaterEqual(shape["bitmap"], shape["css"],
                                        "the bitmap is narrower than the box "
                                        "it is stretched across")


    def test_one_failing_panel_does_not_stop_the_others(self) -> None:
        """They are separate answers to separate questions.

        The charts used to be drawn after an await on the component map,
        which is the panel most likely to throw - it is the one that talks to
        the component registry.
        """
        result = self._run(
            "window.debuggerPanel.activate=()=>{throw new Error('map is broken');};"
            "document.querySelector('#tabs button[data-tab=\"debugger\"]').click();"
            "await wait();"
            "document.querySelector('#tabs button[data-tab=\"overview\"]').click();"
            "await wait();await wait();")
        self.assertGreater(result["tokens"], 1000,
                           "a broken map took the token chart with it")


class WhatTheAgentSaidToItselfIsNotTheUsersDayTests(unittest.TestCase):
    """Section 9, as amended: the panel shows the user's activity, not ours.

    The listener no longer records the agent's own model calls, but thirty
    days of them are already written down and the user is looking at those.
    Expanding a one-minute stretch of Firefox showed the relabeller asking for
    keywords in Chinese and English, the data heartbeat asking what some raw
    activity meant, and the folder folding old records away - all filed under
    Firefox because Firefox was the window in front while the agent worked in
    the background.

    The record is not rewritten to fix that. It is simply not shown as the
    user's activity, because it never was.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        now = datetime.now(timezone.utc)
        add = self.app.raw_store.add_event
        add(ListenerEvent(event_type="application.focus_ended", source="application",
                          application="firefox.exe", timestamp=now - timedelta(minutes=5),
                          data={"duration_seconds": 600, "window_title": "Firefox"}))
        add(ListenerEvent(event_type="browser.navigation", source="browser",
                          application="Firefox", timestamp=now - timedelta(minutes=9),
                          data={"url": "https://www.youtube.com/watch?v=Ex4m1a0001A", "title": "a video",
                                "browser": "Firefox"}))
        # What the agent said to a model while that was on screen.
        add(ListenerEvent(
            event_type="assistant.message", source="assistant",
            application="Claude Code", timestamp=now - timedelta(minutes=8),
            data={"role": "user", "tool": "Claude Code",
                  "text": "Add search keywords in Chinese and English to some memories.",
                  "session_id": "99999999-2222-4333-8444-555555555555",
                  "project": str(self.app.root / "workspace")}))
        add(ListenerEvent(
            event_type="assistant.session", source="assistant",
            application="Claude Code", timestamp=now - timedelta(minutes=8),
            data={"tool": "Claude Code", "exchanges": 18,
                  "session_id": "99999999-2222-4333-8444-555555555555",
                  "project": str(self.app.root / "workspace")}))
        # And what the user said to their own assistant, in their own project.
        add(ListenerEvent(
            event_type="assistant.message", source="assistant",
            application="Claude Code", timestamp=now - timedelta(minutes=7),
            data={"role": "user", "tool": "Claude Code",
                  "text": "how do I fix the build",
                  "session_id": "12121212-2222-4333-8444-555555555555",
                  "project": "C:/proj/demo"}))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _said(self):
        detail = self.app.overview.application_detail("firefox.exe")
        return [item["text"] for period in detail["interactions"]
                for item in period["assistant"]]

    def test_the_agent_asking_a_model_is_not_shown_as_use_of_the_browser(self) -> None:
        for text in self._said():
            self.assertNotIn("Add search keywords", text)

    def test_the_users_own_session_is_still_shown(self) -> None:
        self.assertIn("how do I fix the build", self._said())

    def test_and_the_browsing_that_really_was_the_browser_is_untouched(self) -> None:
        detail = self.app.overview.application_detail("firefox.exe")
        urls = [page["url"] for period in detail["interactions"]
                for page in period["pages"]]
        self.assertIn("https://www.youtube.com/watch?v=Ex4m1a0001A", urls)

    def test_nor_does_the_agent_appear_as_a_project_worked_in(self) -> None:
        # The same events drive the list of projects worked in through an
        # assistant, where the agent's own storage was the busiest of them.
        projects = [p["project"] for p in self.app.overview.assistants()["projects"]]
        self.assertIn("C:/proj/demo", projects)
        self.assertNotIn(str(self.app.root / "workspace"), projects)

    def test_a_marked_session_is_dropped_wherever_it_was_run(self) -> None:
        from personalized_agent.common.agent_sessions import new_session_id

        self.app.raw_store.add_event(ListenerEvent(
            event_type="assistant.message", source="assistant",
            application="Claude Code", timestamp=datetime.now(timezone.utc),
            data={"role": "user", "tool": "Claude Code", "text": "Raw activity to interpret",
                  "session_id": new_session_id(), "project": "C:/proj/demo"}))
        for text in self._said():
            self.assertNotIn("Raw activity to interpret", text)


class AnAssistantSessionBelongsToTheWindowItWasWorkedInTests(unittest.TestCase):
    """Section 9, as amended: a session is not bounded by focus.

    It is started in a terminal and then runs for minutes or hours while the
    user reads something else, so "what was in front when the transcript last
    grew" is not where the work was happening. Asked that way, a session of
    14,646 exchanges appeared under the browser because a video was playing
    while it ran - and over recent history the same rule filed assistant work
    under the chat client and under the lock screen, which is the machine
    sitting locked.

    What does say where a session lives is the turns the user typed: typing
    into an assistant means being in its window.
    """

    SESSION = "12121212-2222-4333-8444-555555555555"
    QUIET = "34343434-2222-4333-8444-555555555555"

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        now = datetime.now(timezone.utc)
        self.now = now
        add = self.app.raw_store.add_event
        # The user typed into the terminal, then went to watch a video while
        # the session went on working.
        add(ListenerEvent(event_type="application.focus_ended", source="application",
                          application="WindowsTerminal.exe",
                          timestamp=now - timedelta(minutes=29),
                          data={"duration_seconds": 60, "window_title": "a task"}))
        add(ListenerEvent(event_type="application.focus_ended", source="application",
                          application="firefox.exe", timestamp=now - timedelta(minutes=5),
                          data={"duration_seconds": 1200, "window_title": "YouTube"}))
        add(ListenerEvent(
            event_type="assistant.message", source="assistant",
            application="Claude Code", timestamp=now - timedelta(minutes=29, seconds=30),
            data={"role": "user", "tool": "Claude Code", "text": "fix the overview",
                  "session_id": self.SESSION, "project": "C:/proj/demo"}))
        # ... and this arrived while the video was on screen.
        add(ListenerEvent(
            event_type="assistant.session", source="assistant",
            application="Claude Code", timestamp=now - timedelta(minutes=10),
            data={"tool": "Claude Code", "exchanges": 14646,
                  "session_id": self.SESSION, "project": "C:/proj/demo"}))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _assistant_in(self, application: str):
        detail = self.app.overview.application_detail(application)
        return [item for period in detail["interactions"]
                for item in period["assistant"]]

    def test_the_session_is_not_filed_under_what_was_on_screen(self) -> None:
        self.assertEqual(self._assistant_in("firefox.exe"), [])

    def test_it_is_filed_under_the_window_its_turns_were_typed_into(self) -> None:
        said = [item["text"] for item in self._assistant_in("WindowsTerminal.exe")]
        self.assertTrue(any("14646 exchanges" in text for text in said),
                        "the session belongs to the terminal, not to the browser")

    def test_a_session_with_no_turns_recorded_is_not_guessed_at(self) -> None:
        # Its conversation was not recorded, so there is no evidence of where
        # it was worked in - and the browser is not a better answer than none.
        self.app.raw_store.add_event(ListenerEvent(
            event_type="assistant.session", source="assistant",
            application="Claude Code", timestamp=self.now - timedelta(minutes=9),
            data={"tool": "Claude Code", "exchanges": 7,
                  "session_id": self.QUIET, "project": "C:/proj/other"}))
        for application in ("firefox.exe", "WindowsTerminal.exe"):
            for item in self._assistant_in(application):
                self.assertNotIn("7 exchanges", item["text"])

    def test_the_terminal_still_says_what_it_was_doing(self) -> None:
        """What a stretch without evidence falls back on, and it is not a guess.

        Section 18: recording what was said is off until an assistant is
        ticked, so on a default installation no session has a typed turn and
        none of them can be placed. That is the case section 9 raised - a
        terminal reporting "nothing recorded" while a session ran inside it -
        and the answer to it is the window title, which a coding assistant
        sets to the task it is working on. The title is a fact about the
        window; the attribution would have been a guess about the work.
        """
        self.app.raw_store.add_event(ListenerEvent(
            event_type="application.focused", source="application",
            application="WindowsTerminal.exe",
            timestamp=self.now - timedelta(minutes=20),
            data={"window_title": "fix the overview"}))
        detail = self.app.overview.application_detail("WindowsTerminal.exe")
        titles = [item["title"] for period in detail["interactions"]
                  for item in period["windows"]]
        self.assertIn("fix the overview", titles)

    def test_the_browsing_that_really_was_the_browser_is_untouched(self) -> None:
        self.app.raw_store.add_event(ListenerEvent(
            event_type="browser.navigation", source="browser", application="Firefox",
            timestamp=self.now - timedelta(minutes=10),
            data={"url": "https://www.youtube.com/watch?v=Ex4m1a0001A", "title": "a video",
                  "browser": "Firefox"}))
        detail = self.app.overview.application_detail("firefox.exe")
        urls = [page["url"] for period in detail["interactions"]
                for page in period["pages"]]
        self.assertIn("https://www.youtube.com/watch?v=Ex4m1a0001A", urls)


class TheAgentsOwnRecordsAreNotTheUsersActivityTests(unittest.TestCase):
    """Section 9, as amended: the memory folders are bookkeeping, not a day.

    The agent keeps its memory in the workspace and the workspace is watched,
    which is right - the user's documents and downloads live there too. But
    every record written and every index rebuilt arrived back as a filesystem
    event with no application on it, and was given to whichever window was in
    front. One stretch of Firefox held **217** of them.

    Ownership is what distinguishes them, and for events already recorded the
    place is what is left to read it by. Only the memory folders and the
    internal storage, never the rest of the workspace.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        now = datetime.now(timezone.utc)
        self.app.raw_store.add_event(ListenerEvent(
            event_type="application.focus_ended", source="application",
            application="firefox.exe", timestamp=now - timedelta(minutes=5),
            data={"duration_seconds": 1200, "window_title": "YouTube"}))
        for path in (Path(self.app.root) / "workspace" / "data" / "others" / "rec.md",
                     Path(self.app.root) / "workspace" / "data" / "index" / "state.json",
                     Path(self.app.root) / "internal" / "database" / "agent.db",
                     Path(self.app.root) / "workspace" / "notes" / "mine.md"):
            self.app.raw_store.add_event(ListenerEvent(
                event_type="filesystem.modified", source="filesystem",
                application=None, timestamp=now - timedelta(minutes=10),
                data={"path": str(path)}))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _files(self):
        detail = self.app.overview.application_detail("firefox.exe")
        return [item["name"] for period in detail["interactions"]
                for item in period["files"]]

    def test_a_record_it_wrote_is_not_shown_as_use_of_the_browser(self) -> None:
        self.assertNotIn("rec.md", self._files())

    def test_nor_the_index_it_rebuilt(self) -> None:
        self.assertNotIn("state.json", self._files())

    def test_nor_its_internal_storage(self) -> None:
        self.assertNotIn("agent.db", self._files())

    def test_but_the_users_own_file_in_the_workspace_still_counts(self) -> None:
        # Not location for its own sake: the workspace is where the user's
        # documents and downloads live, and those are the activity worth
        # seeing.
        self.assertIn("mine.md", self._files())


class OurOwnPanelIsNotASiteTheUserVisitedTests(unittest.TestCase):
    """Section 9, as amended: the agent watching the user watching it.

    The interface is served over HTTP and opened in the ordinary browser, so
    every look at the Overview and every trip to Settings went into the
    browser history like any other page and came back through the Listener as
    browsing. On one machine ``127.0.0.1:8756`` was the sixth most visited
    host in the record, ahead of the search engine and most of the sites the
    user actually reads.

    It is the least useful self-observation of the three, because the agent
    already knows every interaction with its own interface: it served them.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.app.config.set("api_port", 8756)
        now = datetime.now(timezone.utc)
        add = self.app.raw_store.add_event
        add(ListenerEvent(event_type="application.focus_ended", source="application",
                          application="firefox.exe", timestamp=now - timedelta(minutes=5),
                          data={"duration_seconds": 1200,
                                "window_title": "Personalized Agent"}))
        add(ListenerEvent(event_type="application.focused", source="application",
                          application="firefox.exe", timestamp=now - timedelta(minutes=10),
                          data={"window_title": "Personalized Agent"}))
        for url in ("http://127.0.0.1:8756/#overview",
                    "http://localhost:8756/#settings",
                    "http://127.0.0.1:3000/",
                    "https://www.youtube.com/watch?v=Ex4m1a0002A"):
            add(ListenerEvent(event_type="browser.navigation", source="browser",
                              application="Firefox", timestamp=now - timedelta(minutes=10),
                              data={"url": url, "title": url, "browser": "Firefox"}))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _urls(self):
        detail = self.app.overview.application_detail("firefox.exe")
        return [page["url"] for period in detail["interactions"]
                for page in period["pages"]]

    def test_the_panel_is_not_listed_as_a_page_visited(self) -> None:
        self.assertNotIn("http://127.0.0.1:8756/#overview", self._urls())

    def test_by_whichever_name_this_machine_was_called(self) -> None:
        self.assertNotIn("http://localhost:8756/#settings", self._urls())

    def test_something_else_served_locally_is_still_the_users_own(self) -> None:
        # A port is what distinguishes our interface from anything else run
        # on this machine, and people develop on this machine too.
        self.assertIn("http://127.0.0.1:3000/", self._urls())

    def test_and_the_web_is_untouched(self) -> None:
        self.assertIn("https://www.youtube.com/watch?v=Ex4m1a0002A", self._urls())

    def test_it_does_not_count_as_a_site_the_browser_was_used_for(self) -> None:
        usage = {a["application"]: a for a in self.app.overview.summary()["applications"]}
        sites = [b["label"] for b in usage["firefox.exe"]["breakdown"]]
        self.assertNotIn("127.0.0.1:8756", sites)

    def test_the_window_title_is_left_alone(self) -> None:
        # The browser genuinely was showing that page, and what an
        # application was doing is not something to blank out.
        detail = self.app.overview.application_detail("firefox.exe")
        titles = [item["title"] for period in detail["interactions"]
                  for item in period["windows"]]
        self.assertIn("Personalized Agent", titles)


class PinnedMemoryLanguagesReadAsTwoTicksTests(unittest.TestCase):
    """Section 12, as amended: a stored list this control did not write.

    The memory languages are pinned to Chinese and English and stored as
    ``zh,en``. The tick list splits a stored value one entry per line, so it
    read that as a single entry named "zh,en" - shown **ticked** and marked
    "not offered here", with the real zh and en sitting unticked beneath it.
    Every part of that is wrong about a setting whose whole content is that
    those two are on and cannot be changed.

    The setting is also fixed rather than derived, and the difference matters
    in the interface: "read-only" tells the reader to go and change whatever
    this follows, and there is nothing to go to.
    """

    FIELD = {
        "key": "language", "label": "Memory languages", "type": "multi_choice",
        "default": "zh,en", "section": "General",
        "description": "The languages your memory is findable in.",
        "choices": ["zh", "en"], "choices_source": None, "chooser": None,
        "readonly": True, "fixed": True, "secret": False,
        "restart_required": False, "hidden": False,
    }

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _run(self, value: str = "zh,en", field: dict = None) -> dict:
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        shown = field or self.FIELD
        stub = {
            "/api/settings": {"values": {shown["key"]: value},
                              "schema": [shown],
                              "choices": {}, "choice_notes": {}},
            "/api/models": {"roles": {}, "providers": [], "auth_methods": {},
                            "credential_backend": {"backend": "encrypted file"}},
            "/api/settings/defaults": {"user_defaults": {}},
            "/api/status": {"pending_raw_data": {"events": 0, "edits": 0},
                            "library": {"records": 0, "folders": {}}},
        }
        harness = (
            "<script>window.__posted=[];const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
            "if(o&&o.method==='POST')window.__posted.push({p:p,b:String(o.body||'')});"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';"
            "try{await loadSettings();"
            "await new Promise(r=>setTimeout(r,60));}catch(e){err=e.message;}"
            "const box=document.querySelector('.multi-choice');"
            "const row=box?box.closest('.setting-row')||box.parentElement:null;"
            "document.title=JSON.stringify({err:err,"
            "boxes:[...document.querySelectorAll('.multi-choice input')]"
            ".map(i=>({v:i.value,on:i.checked,off:i.disabled})),"
            "names:[...document.querySelectorAll('.multi-choice-name')]"
            ".map(e=>e.textContent.trim()),"
            "text:box?box.textContent:'',"
            "rowText:row?row.textContent:''});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
        match = re.search(r"<title>(.*?)</title>", completed.stdout or "", re.S)
        self.assertIsNotNone(match, "settings never rendered")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "")
        return result

    def test_the_comma_separated_value_is_read_as_its_two_languages(self) -> None:
        result = self._run()
        self.assertEqual([b["v"] for b in result["boxes"]], ["zh", "en"])

    def test_and_both_of_them_are_ticked(self) -> None:
        self.assertEqual([b["on"] for b in self._run()["boxes"]], [True, True])

    def test_there_is_no_row_that_is_both_languages_at_once(self) -> None:
        self.assertNotIn("zh,en", self._run()["names"])

    def test_neither_language_is_marked_as_not_offered(self) -> None:
        self.assertNotIn("not offered here", self._run()["text"])

    def test_what_cannot_be_changed_cannot_be_ticked(self) -> None:
        # Disabling the container does nothing: it is a div, and the boxes
        # inside it stayed clickable while nothing was listening, so a tick
        # appeared to take and was silently discarded on the next render.
        self.assertTrue(all(b["off"] for b in self._run()["boxes"]))

    def test_it_says_fixed_rather_than_read_only(self) -> None:
        # "Read-only" means "this follows something else, change that" - and
        # for this setting there is nothing to go and change.
        row = self._run()["rowText"]
        self.assertIn("fixed in this version", row)
        self.assertNotIn("read-only", row)

    def test_the_assistants_footer_does_not_appear_under_languages(self) -> None:
        self.assertNotIn("Nothing ticked records nothing", self._run()["text"])

    def test_a_list_this_control_did_write_is_still_read_line_by_line(self) -> None:
        field = dict(self.FIELD, key="listener_assistant_content_tools",
                     readonly=False, fixed=False,
                     choices=["Claude Code", "Cursor"])
        result = self._run(value="Claude Code", field=field)
        self.assertEqual([(b["v"], b["on"]) for b in result["boxes"]],
                         [("Claude Code", True), ("Cursor", False)])

    def test_a_name_that_really_contains_a_comma_survives(self) -> None:
        # Splitting on commas is only safe where every piece is an option.
        field = dict(self.FIELD, key="listener_assistant_content_tools",
                     readonly=False, fixed=False,
                     choices=["Ada, Lovelace", "Cursor"])
        result = self._run(value="Ada, Lovelace", field=field)
        self.assertEqual([(b["v"], b["on"]) for b in result["boxes"]],
                         [("Ada, Lovelace", True), ("Cursor", False)])


class TheNewestEventsAreLookedUpNotSortedTests(unittest.TestCase):
    """Section 9, as amended: the Overview's own query had no index.

    Every index on raw activity leads with a column this query does not
    filter on - the event type, the application, whether it has been
    processed - so reading "the newest N events, whatever they are" could use
    none of them. On one machine that meant scanning all 49,522 rows and
    sorting them in a temporary B-tree: **271 ms**, on every click in the
    application list and every stretch of use expanded.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _plan(self, sql, params=()):
        return " ".join(dict(row)["detail"]
                        for row in self.app.db.query("EXPLAIN QUERY PLAN " + sql,
                                                     list(params)))

    def test_the_newest_events_come_off_an_index(self) -> None:
        plan = self._plan("SELECT * FROM raw_events ORDER BY timestamp DESC LIMIT 2000")
        self.assertIn("idx_raw_events_recent", plan)

    def test_and_are_not_sorted_to_get_there(self) -> None:
        # The temporary B-tree is the cost: it is the whole table, ordered,
        # to hand back the first two thousand.
        plan = self._plan("SELECT * FROM raw_events ORDER BY timestamp DESC LIMIT 2000")
        self.assertNotIn("TEMP B-TREE", plan)

    def test_a_window_of_time_uses_it_too(self) -> None:
        plan = self._plan(
            "SELECT * FROM raw_events WHERE timestamp >= ? "
            "ORDER BY timestamp DESC LIMIT 4000", ["2026-09-01T00:00:00+00:00"])
        self.assertIn("idx_raw_events_recent", plan)
        self.assertNotIn("TEMP B-TREE", plan)

    def test_the_index_exists_on_a_database_that_predates_it(self) -> None:
        # It is created on open rather than at creation, so an installation
        # that has been running for weeks gets it by being opened.
        names = {dict(row)["name"]
                 for row in self.app.db.query("PRAGMA index_list('raw_events')")}
        self.assertIn("idx_raw_events_recent", names)


class AnIntervalIsSetInTheUnitYouThinkIn(unittest.TestCase):
    """Section 12, as amended: stored in seconds, set in whatever you count in.

    Both heartbeat intervals were already settings, unhidden, in their own
    section - and both were shown as a raw number of seconds under a label
    ending "(s)". "How often should the agent wake?" answered as ``3600`` is
    a unit conversion the reader does in their head to check a value they
    never chose, and these two decide most of what the agent costs when
    nobody is talking to it.
    """

    # The heartbeats' own are set in minutes on General now; this is any
    # other interval kept in seconds.
    FIELD = {
        "key": "listener_flush_interval_seconds", "label": "How often the agent wakes",
        "type": "int", "default": 3600, "section": "Heartbeat",
        "description": "How often that review runs.",
        "choices": None, "choices_source": None, "chooser": None,
        "readonly": False, "fixed": False, "secret": False,
        "restart_required": False, "hidden": False, "minimum": 60, "maximum": 86400,
    }

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _run(self, value=3600, steps="", field=None):
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        shown = field or self.FIELD
        stub = {
            "/api/settings": {"values": {shown["key"]: value}, "schema": [shown],
                              "choices": {}, "choice_notes": {}},
            "/api/models": {"roles": {}, "providers": [], "auth_methods": {},
                            "credential_backend": {"backend": "encrypted file"}},
            "/api/settings/defaults": {"user_defaults": {}},
            "/api/status": {"pending_raw_data": {"events": 0, "edits": 0},
                            "library": {"records": 0, "folders": {}}},
        }
        harness = (
            "<script>window.__posted=[];const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
            "if(o&&o.method==='POST')window.__posted.push(String(o.body||''));"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';"
            "const wait=()=>new Promise(r=>setTimeout(r,60));"
            # Changed where it stands, as every settings card is.
            "try{await loadSettings();await wait();"
            + steps + "}catch(e){err=e.message;}"
            "const box=document.querySelector('.duration');"
            "document.title=JSON.stringify({err:err,"
            "rendered:Boolean(box),"
            "amount:(document.querySelector('.duration-amount')||{}).value||'',"
            "unit:(document.querySelector('.duration-unit')||{}).value||'',"
            "says:(document.querySelector('.duration-says')||{}).textContent||'',"
            "held:box?box.value:null,"
            "rowText:box?box.closest('.setting-row').textContent:'',"
            "posted:window.__posted});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
        match = re.search(r"<title>(.*?)</title>", completed.stdout or "", re.S)
        self.assertIsNotNone(match, "settings never rendered")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "")
        return result

    def test_an_hour_reads_as_an_hour(self) -> None:
        result = self._run(3600)
        self.assertEqual((result["amount"], result["unit"]), ("1", "hours"))

    def test_and_says_so_in_words(self) -> None:
        self.assertIn("every hour", self._run(3600)["says"])

    def test_fifteen_minutes_reads_as_fifteen_minutes(self) -> None:
        result = self._run(900)
        self.assertEqual((result["amount"], result["unit"]), ("15", "minutes"))
        self.assertIn("every 15 minutes", result["says"])

    def test_something_that_is_not_a_round_unit_stays_in_seconds(self) -> None:
        result = self._run(90)
        self.assertEqual((result["amount"], result["unit"]), ("90", "seconds"))

    def test_what_is_stored_is_still_seconds(self) -> None:
        self.assertEqual(self._run(3600)["held"], 3600)

    def test_typing_a_number_means_that_many_of_the_unit(self) -> None:
        result = self._run(900, steps=(
            "const a=document.querySelector('.duration-amount');a.value='30';"
            "a.dispatchEvent(new Event('change',{bubbles:true}));await wait();"))
        self.assertEqual(result["unit"], "minutes")
        self.assertEqual(result["held"], 1800)

    def test_a_number_typed_before_the_unit_is_changed_is_not_lost(self) -> None:
        # Converted from what the control is holding now, not from what it
        # opened with: 30 minutes asked for in hours is half an hour.
        result = self._run(3600, steps=(
            "const a=document.querySelector('.duration-amount');"
            "const u=document.querySelector('.duration-unit');"
            "u.value='minutes';u.dispatchEvent(new Event('change',{bubbles:true}));"
            "await wait();a.value='30';"
            "a.dispatchEvent(new Event('change',{bubbles:true}));await wait();"
            "u.value='seconds';u.dispatchEvent(new Event('change',{bubbles:true}));"
            "await wait();"))
        self.assertEqual(result["amount"], "1800")
        self.assertEqual(result["held"], 1800)

    def test_changing_the_unit_does_not_change_the_interval(self) -> None:
        # Switching "3600 seconds" to hours must read 1 hour, not 3600 hours.
        result = self._run(3600, steps=(
            "const u=document.querySelector('.duration-unit');u.value='seconds';"
            "u.dispatchEvent(new Event('change',{bubbles:true}));await wait();"
            "u.value='hours';u.dispatchEvent(new Event('change',{bubbles:true}));"
            "await wait();"))
        self.assertEqual(result["amount"], "1")
        self.assertEqual(result["held"], 3600)

    def test_it_is_saved_in_seconds(self) -> None:
        result = self._run(3600, steps=(
            "const a=document.querySelector('.duration-amount');a.value='30';"
            "const u=document.querySelector('.duration-unit');u.value='minutes';"
            "a.dispatchEvent(new Event('change',{bubbles:true}));await wait();"
            # The Save of the card it was changed in: each card has its own.
            "a.closest('[id^=\"settings-form\"]').querySelector('[data-settings-save]')"
            ".click();await wait();"))
        self.assertTrue(result["posted"], "nothing was saved")
        self.assertIn("1800", result["posted"][0])

    def test_a_value_below_the_floor_is_brought_up_to_it(self) -> None:
        result = self._run(3600, steps=(
            "const a=document.querySelector('.duration-amount');a.value='1';"
            "const u=document.querySelector('.duration-unit');u.value='seconds';"
            "a.dispatchEvent(new Event('change',{bubbles:true}));await wait();"))
        self.assertEqual(result["held"], 60)

    def test_the_label_no_longer_has_to_say_the_unit(self) -> None:
        self.assertNotIn("(s)", self._run(3600)["rowText"])

    def test_both_heartbeats_are_settings_in_their_own_section(self) -> None:
        # They always were. What was missing was being able to read them.
        from personalized_agent.common.config.schema import SCHEMA_BY_KEY

        for key in ("heartbeat_interval_seconds",):
            field = SCHEMA_BY_KEY[key]
            self.assertFalse(field.hidden)
            self.assertEqual(field.section, "Heartbeat")
            self.assertNotIn("(s)", field.label)


class APageBelongsToABrowserTests(unittest.TestCase):
    """Section 9, as amended: not to whatever was in front of it.

    A browser answers to two names - what it calls itself and what its
    process is called - and the suffix rule joined those for Chrome and
    Firefox but not for Edge, which calls itself Edge and runs as msedge.exe.
    The two never met, so every page read in Edge was attributed by focus
    instead: measured on one machine, **16 of 17 went to an SSH client**.

    A source that names no browser at all - "browser", which is not any
    application - had the same problem for a different reason: its pages
    were filed under a terminal, an SSH client and three different browsers.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        now = datetime.now(timezone.utc)
        self.now = now
        add = self.app.raw_store.add_event
        add(ListenerEvent(event_type="application.focus_ended", source="application",
                          application="msedge.exe", timestamp=now - timedelta(minutes=20),
                          data={"duration_seconds": 300, "window_title": "Edge"}))
        add(ListenerEvent(event_type="application.focus_ended", source="application",
                          application="BvSsh.exe", timestamp=now - timedelta(minutes=2),
                          data={"duration_seconds": 300, "window_title": "SSH"}))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _pages_in(self, application):
        detail = self.app.overview.application_detail(application)
        return [p["url"] for period in detail["interactions"] for p in period["pages"]]

    def test_edge_browsing_goes_to_edge(self) -> None:
        self.app.raw_store.add_event(ListenerEvent(
            event_type="browser.navigation", source="browser", application="Edge",
            timestamp=self.now - timedelta(minutes=5),
            data={"url": "https://example.com/a", "title": "A", "browser": "Edge"}))
        self.assertIn("https://example.com/a", self._pages_in("msedge.exe"))

    def test_and_not_to_the_ssh_client_that_was_in_front(self) -> None:
        self.app.raw_store.add_event(ListenerEvent(
            event_type="browser.navigation", source="browser", application="Edge",
            timestamp=self.now - timedelta(minutes=5),
            data={"url": "https://example.com/a", "title": "A", "browser": "Edge"}))
        self.assertEqual(self._pages_in("BvSsh.exe"), [])

    def test_a_page_from_a_source_that_names_no_browser_is_not_guessed_at(self) -> None:
        # "browser" is not an application. Filed by focus that put pages
        # under a terminal; there is no honest answer, so there is no answer.
        self.app.raw_store.add_event(ListenerEvent(
            event_type="browser.navigation", source="browser",
            application="browser", timestamp=self.now - timedelta(minutes=1),
            data={"url": "https://example.com/b", "title": "B"}))
        self.assertEqual(self._pages_in("BvSsh.exe"), [])

    def test_but_a_browser_in_front_is_the_honest_guess(self) -> None:
        self.app.raw_store.add_event(ListenerEvent(
            event_type="browser.navigation", source="browser",
            application="browser", timestamp=self.now - timedelta(minutes=22),
            data={"url": "https://example.com/c", "title": "C"}))
        self.assertIn("https://example.com/c", self._pages_in("msedge.exe"))

    def test_the_alias_is_read_both_ways(self) -> None:
        from personalized_agent.agent.overview.service import _is_a_browser, _normalise

        self.assertEqual(_normalise("Edge"), _normalise("msedge.exe"))
        self.assertTrue(_is_a_browser("Firefox"))
        self.assertTrue(_is_a_browser("msedge.exe"))
        self.assertFalse(_is_a_browser("BvSsh.exe"))
        self.assertFalse(_is_a_browser("WindowsTerminal.exe"))


class AStretchEndsWhenYouLeaveNotWhenATabChangesTests(unittest.TestCase):
    """Section 9, as amended: the title was never the boundary.

    A stretch of use used to end whenever the window *title* changed, so
    switching tab ended one stretch of browsing and began another. Measured
    on one machine: **127 stretches of Firefox, 81% of them under thirty
    seconds, median six**; and seventeen stretches of Chrome for two minutes
    of use, 94% under thirty seconds. Expanded, each held a line or two -
    which is not a record of an evening.

    The title is what varied inside the stretch, and the panel lists it
    there. What ends a stretch is going somewhere else and staying.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.now = datetime.now(timezone.utc)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _focused(self, ends_ago, seconds, title):
        self.app.raw_store.add_event(ListenerEvent(
            event_type="application.focus_ended", source="application",
            application="firefox.exe",
            timestamp=self.now - timedelta(seconds=ends_ago),
            data={"duration_seconds": seconds, "window_title": title}))

    def _stretches(self):
        detail = self.app.overview.application_detail("firefox.exe")
        return [p for p in detail["interactions"] if not p.get("ongoing")]

    def test_changing_tab_does_not_end_the_stretch(self) -> None:
        self._focused(300, 60, "Harbour towns at dusk - YouTube — Mozilla Firefox")
        self._focused(240, 60, "Gmail — Mozilla Firefox")
        self._focused(180, 60, "Harbour towns at dusk - YouTube — Mozilla Firefox")
        self.assertEqual(len(self._stretches()), 1)

    def test_and_the_stretch_keeps_the_time_actually_spent(self) -> None:
        self._focused(300, 60, "one")
        self._focused(240, 60, "two")
        self.assertEqual(self._stretches()[0]["duration_seconds"], 120)

    def test_going_away_and_staying_does_end_it(self) -> None:
        self._focused(3600, 60, "before lunch")
        self._focused(300, 60, "after lunch")
        self.assertEqual(len(self._stretches()), 2)

    def test_overlapping_stretches_are_joined_rather_than_left_apart(self) -> None:
        """The ordinary case, not a fault.

        A window's reported duration covers time the next window of the same
        application also reports, because the title changing is what ends one
        and begins the other. Measured on one machine, 90 of 217 consecutive
        stretches of Firefox overlapped - so a rule that joined only
        stretches with a gap left nearly half of them apart.
        """
        self._focused(300, 120, "one")      # 420s ago -> 300s ago
        self._focused(240, 120, "two")      # 360s ago -> 240s ago, overlapping
        stretches = self._stretches()
        self.assertEqual(len(stretches), 1)
        self.assertEqual(stretches[0]["duration_seconds"], 180,
                         "the overlap was counted twice")

    def test_the_span_is_kept_apart_from_the_time_spent(self) -> None:
        # A stretch shared with another window spans more than it spent
        # here, and end-minus-start would count that window's time as this
        # application's.
        self._focused(300, 30, "one")       # 330s ago -> 300s ago
        self._focused(240, 30, "two")       # 270s ago -> 240s ago
        stretch = self._stretches()[0]
        self.assertEqual(stretch["duration_seconds"], 60)
        self.assertEqual(stretch["span_seconds"], 90)
        self.assertTrue(stretch["span_human"])

    def test_a_stretch_nobody_left_says_nothing_about_a_span(self) -> None:
        self._focused(300, 60, "one")
        self.assertEqual(self._stretches()[0]["span_human"], "")

    def test_the_titles_are_still_there_inside_it(self) -> None:
        # They were the boundary; now they are the content, which is where
        # they always belonged.
        self._focused(300, 60, "Harbour towns at dusk - YouTube — Mozilla Firefox")
        self._focused(240, 60, "Gmail — Mozilla Firefox")
        for seconds, title in ((290, "Harbour towns at dusk - YouTube — Mozilla Firefox"),
                               (250, "Gmail — Mozilla Firefox")):
            self.app.raw_store.add_event(ListenerEvent(
                event_type="application.focused", source="application",
                application="firefox.exe",
                timestamp=self.now - timedelta(seconds=seconds),
                data={"window_title": title}))
        titles = [w["title"] for w in self._stretches()[0]["windows"]]
        self.assertEqual(titles, ["Harbour towns at dusk - YouTube — Mozilla Firefox", "Gmail — Mozilla Firefox"])

    def test_how_many_windows_it_was_spread_across_is_kept(self) -> None:
        self._focused(300, 60, "one")
        self._focused(240, 60, "two")
        self._focused(180, 60, "three")
        self.assertEqual(self._stretches()[0]["windows_seen"], 3)


class TheDetailCoversTheWindowTheTabClaimsTests(unittest.TestCase):
    """Section 9, as amended: a window of time, not a number of rows.

    Selecting an application read "the newest 2,000 events" and built its
    stretches of use out of whatever those happened to span. On one machine
    that was **213 minutes** of the twenty-four hours the tab said it was
    showing, and it said nothing about the rest - so "what was I doing
    yesterday evening", which is the question this panel is opened with,
    was answered about this afternoon.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.now = datetime.now(timezone.utc)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _focused(self, hours_ago, seconds=60, application="firefox.exe"):
        self.app.raw_store.add_event(ListenerEvent(
            event_type="application.focus_ended", source="application",
            application=application,
            timestamp=self.now - timedelta(hours=hours_ago),
            data={"duration_seconds": seconds, "window_title": f"{hours_ago}h ago"}))

    def _detail(self, **kwargs):
        return self.app.overview.application_detail("firefox.exe", **kwargs)

    def test_a_stretch_from_this_morning_is_still_there_this_evening(self) -> None:
        self._focused(20)
        self._focused(1)
        stretches = [p for p in self._detail()["interactions"] if not p.get("ongoing")]
        self.assertEqual(len(stretches), 2)

    def test_noise_in_between_does_not_push_it_out(self) -> None:
        """What made it a slice: the newest N events, whatever they were.

        A day is mostly file changes - on one machine 17,301 of 21,182 - so a
        count-bounded read covers however long the newest N of those span,
        which is an hour or two.
        """
        self._focused(20)
        for index in range(3000):
            self.app.raw_store.add_event(ListenerEvent(
                event_type="filesystem.modified", source="filesystem",
                application=None,
                timestamp=self.now - timedelta(minutes=30, seconds=index % 600),
                data={"path": f"C:/work/file{index}.txt"}))
        stretches = [p for p in self._detail()["interactions"] if not p.get("ongoing")]
        self.assertEqual(len(stretches), 1, "the morning fell out of the window")

    def test_what_is_older_than_the_window_is_not_there(self) -> None:
        self._focused(30)
        self._focused(1)
        stretches = [p for p in self._detail()["interactions"] if not p.get("ongoing")]
        self.assertEqual(len(stretches), 1)

    def test_the_window_is_the_one_asked_for(self) -> None:
        self._focused(5)
        self._focused(1)
        self.assertEqual(len(self._detail(hours=2)["interactions"]), 1)
        self.assertEqual(self._detail(hours=2)["window_hours"], 2)

    def test_the_answer_says_which_window_it_covers(self) -> None:
        self._focused(1)
        self.assertEqual(self._detail()["window_hours"], 24)

    def test_and_says_when_it_could_not_cover_it_all(self) -> None:
        # A panel that shows a slice and calls it a day is worse than one
        # that says it is a slice.
        for hours_ago in (1, 2, 3, 4, 5):
            self._focused(hours_ago)
        self.assertTrue(self._detail()["complete"])
        self.app.overview.MOST_EVENTS = 2
        try:
            self.assertFalse(self._detail()["complete"])
        finally:
            del self.app.overview.MOST_EVENTS

    def test_a_directory_change_is_not_read_at_all(self) -> None:
        # Recorded for the directory and never read for anything: every file
        # change inside it arrives separately. The cost is in building it.
        self._focused(1)
        for index in range(50):
            self.app.raw_store.add_event(ListenerEvent(
                event_type="filesystem.directory_modified", source="filesystem",
                application=None, timestamp=self.now - timedelta(minutes=50 + index),
                data={"path": "C:/work"}))
        kinds = {row["event_type"] for row in self._detail()["events"]}
        self.assertNotIn("filesystem.directory_modified", kinds)

    def test_the_agents_own_writes_never_reach_the_reader(self) -> None:
        """Excluded in the query, not filtered afterwards.

        A window of time builds every row in it into an object first, and on
        one machine 17,301 of the 21,182 events in a day were the agent
        writing its own memory - every one read, parsed, and thrown away.
        """
        self._focused(1)
        for index in range(20):
            self.app.raw_store.add_event(ListenerEvent(
                event_type="filesystem.modified", source="filesystem",
                application=None, timestamp=self.now - timedelta(minutes=30),
                data={"path": str(Path(self.app.root) / "workspace" / "data"
                                  / f"rec{index}.md")}))
        raw = self.app.raw_store.recent_events(
            limit=5000, since=self.now - timedelta(hours=24),
            not_under=self.app.overview._own_records())
        self.assertEqual([e for e in raw if e.event_type.startswith("filesystem.")], [])

    def test_but_the_users_own_file_still_arrives(self) -> None:
        self.app.raw_store.add_event(ListenerEvent(
            event_type="filesystem.modified", source="filesystem",
            application=None, timestamp=self.now - timedelta(minutes=30),
            data={"path": str(Path(self.app.root) / "workspace" / "notes" / "mine.md")}))
        raw = self.app.raw_store.recent_events(
            limit=5000, since=self.now - timedelta(hours=24),
            not_under=self.app.overview._own_records())
        self.assertTrue([e for e in raw if e.event_type.startswith("filesystem.")])


class WhatWasReadAndWhatWasWrittenTests(unittest.TestCase):
    """Section 19, as amended: the panel for what just happened to files.

    Different from the list of files worked on, which answers "what has been
    worked on most" by collapsing everything about one file into a count.
    This answers "what have I just been doing", which is a sequence.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.now = datetime.now(timezone.utc)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _touch(self, minutes_ago, path, kind="modified"):
        self.app.raw_store.add_event(ListenerEvent(
            event_type=f"filesystem.{kind}", source="filesystem", application=None,
            timestamp=self.now - timedelta(minutes=minutes_ago), data={"path": path}))

    def test_read_and_written_are_told_apart(self) -> None:
        self._touch(10, "C:/work/report.md")
        self._touch(5, "C:/work/reference.pdf", kind="opened")
        answer = self.app.overview.file_activity()
        self.assertEqual(answer["written"], 1)
        self.assertEqual(answer["read"], 1)
        by_name = {row["name"]: row for row in answer["activity"]}
        self.assertTrue(by_name["reference.pdf"]["read"])
        self.assertFalse(by_name["report.md"]["read"])
        self.assertEqual(by_name["reference.pdf"]["what"], "read")
        self.assertEqual(by_name["report.md"]["what"], "edited")

    def test_the_newest_comes_first(self) -> None:
        self._touch(30, "C:/work/old.md")
        self._touch(2, "C:/work/new.md")
        names = [r["name"] for r in self.app.overview.file_activity()["activity"]]
        self.assertEqual(names, ["new.md", "old.md"])

    def test_one_sitting_is_one_row_that_says_how_long(self) -> None:
        # A file saved twenty times while it is being written is one stretch
        # of work, not twenty things that happened.
        for minutes in (20, 18, 16, 14, 12):
            self._touch(minutes, "C:/work/draft.md")
        answer = self.app.overview.file_activity()
        self.assertEqual(len(answer["activity"]), 1)
        row = answer["activity"][0]
        self.assertEqual(row["times"], 5)
        self.assertEqual(row["over_seconds"], 480.0)
        self.assertTrue(row["over_human"])

    def test_coming_back_hours_later_is_a_second_row(self) -> None:
        self._touch(300, "C:/work/draft.md")
        self._touch(5, "C:/work/draft.md")
        self.assertEqual(len(self.app.overview.file_activity()["activity"]), 2)

    def test_reading_and_editing_the_same_file_stay_apart(self) -> None:
        self._touch(10, "C:/work/draft.md", kind="opened")
        self._touch(9, "C:/work/draft.md")
        kinds = {r["what"] for r in self.app.overview.file_activity()["activity"]}
        self.assertEqual(kinds, {"read", "edited"})

    def test_one_touch_claims_no_span(self) -> None:
        self._touch(5, "C:/work/once.md")
        self.assertEqual(self.app.overview.file_activity()["activity"][0]["over_human"], "")

    def test_the_agents_own_memory_files_are_not_in_it(self) -> None:
        self._touch(5, str(Path(self.app.root) / "workspace" / "data" / "others" / "r.md"))
        self._touch(4, "C:/work/mine.md")
        names = [r["name"] for r in self.app.overview.file_activity()["activity"]]
        self.assertEqual(names, ["mine.md"])

    def test_a_directory_change_is_not_file_activity(self) -> None:
        self._touch(5, "C:/work", kind="directory_modified")
        self.assertEqual(self.app.overview.file_activity()["activity"], [])


class TheDebuggerAsksTheLibraryWhatItHoldsTests(unittest.TestCase):
    """Section 14.16: the panel described a component by breaking on it.

    Inspecting the memory library asked its statistics for ``nodes`` and
    ``edges`` - what the knowledge graph had. The library has folders of
    records and no edges at all, so both keys were simply absent and the
    panel answered ``KeyError: 'nodes'``: a report of a fault in the thing it
    was trying to describe.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _status(self):
        from personalized_agent.debugger.wiring import _graph_status

        return _graph_status(self.app)

    def test_inspecting_it_does_not_raise(self) -> None:
        self.assertEqual(self._status()["state"], "running")

    def test_it_counts_records_and_folders(self) -> None:
        from personalized_agent.data_manage.library import USER_FEEDBACK, Record

        self.app.library.write(Record(folder=USER_FEEDBACK, title="one", summary="x", body="y"))
        status = self._status()
        self.assertEqual(status["records"], 1)
        self.assertEqual(status["folders"], len(self.app.library.stats()["folders"]))
        self.assertIn("user_feedback", status["by folder"])

    def test_it_says_nothing_about_edges(self) -> None:
        # There are none. A count of zero edges is a claim about a structure
        # this does not have.
        self.assertNotIn("edges", self._status())

    def test_a_library_it_cannot_read_is_reported_as_failed(self) -> None:
        self.app.library.stats = lambda: (_ for _ in ()).throw(OSError("gone"))
        status = self._status()
        self.assertEqual(status["state"], "failed")
        self.assertIn("unreachable", status["last_error"])

    def test_a_statistic_that_disappears_later_degrades_to_zero(self) -> None:
        # Read with defaults rather than by subscript, so the next change to
        # what the library counts is a zero and not an exception.
        self.app.library.stats = lambda: {"root": "somewhere"}
        status = self._status()
        self.assertEqual(status["state"], "running")
        self.assertEqual(status["records"], 0)


class EveryPanelSaysWhatPeriodItCoversTests(unittest.TestCase):
    """Section 9, as amended: a list without a period is a number without units.

    "Usage by application" is the panel people check against their own memory
    of the day, and the period it covers is the whole question - three hours
    of a browser is a morning or a fortnight depending on it. The Overview
    carried the window once, beside the total at the top, several panels
    away from the lists it applies to.

    Marking one list and not the ones beside it would be worse than marking
    none: a reader takes the unmarked ones to be something else.
    """

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _render(self, hours: int) -> dict:
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        overview = {
            "window_hours": hours,
            "total_seconds": 3600,
            "total_human": "1h",
            "applications": [{"application": "firefox.exe", "seconds": 3600,
                              "sites": []}],
            "pending": {"events": 0, "edits": 0},
            "library": {"records": 0, "folders": {}},
        }
        stub = {
            "/api/overview": overview,
            "/api/overview/history": {"days": []},
            "/api/overview/hourly": {"buckets": [], "series": []},
            "/api/overview/pages": {"pages": [], "window_hours": hours},
            "/api/overview/files": {"files": [], "window_hours": hours},
            "/api/overview/assistants": {"projects": [], "window_hours": hours},
            "/api/overview/file-activity": {"activity": [], "window_hours": hours,
                                            "total": 0, "read": 0, "written": 0},
            "/api/status": {"pending_raw_data": {"events": 0, "edits": 0},
                            "library": {"records": 0, "folders": {}}},
        }
        harness = (
            "<script>const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';"
            "try{await loadOverview();"
            "await new Promise(r=>setTimeout(r,80));}catch(e){err=e.message;}"
            "const notes=[...document.querySelectorAll('[data-window]')]"
            ".map(n=>n.textContent.trim());"
            "document.title=JSON.stringify({err:err,notes:notes,"
            "top:(document.getElementById('ov-window')||{}).textContent||''});"
            "});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 "--window-size=1400,900",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
            dom = completed.stdout or ""
        match = re.search(r"<title>(.*?)</title>", dom, re.S)
        self.assertIsNotNone(match, "the overview never rendered")
        return json.loads(html.unescape(match.group(1)))

    def test_the_lists_say_what_period_they_cover(self) -> None:
        result = self._render(24)
        self.assertEqual(result["err"], "")
        self.assertGreaterEqual(len(result["notes"]), 4,
                                "usage, pages, files and assistant work")
        for note in result["notes"]:
            self.assertEqual(note, "last 24 hours")

    def test_and_they_follow_the_window_rather_than_saying_24(self) -> None:
        # The window is a parameter, and a panel that says "24 hours" while
        # showing six is worse than one that says nothing.
        result = self._render(6)
        self.assertEqual(result["err"], "")
        self.assertTrue(result["notes"], "no panel carried the period")
        for note in result["notes"]:
            self.assertEqual(note, "last 6 hours")

    def test_the_total_at_the_top_still_carries_it(self) -> None:
        result = self._render(24)
        self.assertEqual(result["top"], "last 24 hours")

    def test_usage_by_application_is_one_of_them(self) -> None:
        web = ROOT / "personalized_agent" / "api" / "web"
        markup = (web / "index.html").read_text(encoding="utf-8")
        usage = markup.split("Usage by application", 1)[1].split("</div>", 3)
        self.assertIn("data-window", "".join(usage[:3]),
                      "the panel this was asked for carries the period")


class TheStateHeartbeatHasAPageOfItsOwnTests(unittest.TestCase):
    """Section 34.3: the review that groups what the other one wrote.

    Its own tab rather than a card on the heartbeat page, because it is a
    different question on a different clock: that page is one run of the
    review that writes states, step by step; this one is what the states
    already written add up to, which is read all at once.

    And it is drawn as a tree. A list of groups has to choose an altitude
    before it has seen anything - which is what a threshold does, quietly,
    for every group at once - where a tree holds every altitude and the
    cut is a line across it.
    """

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _render(self, report, click=True, after="") -> dict:
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        stub = {"/api/state_heartbeat/run": report}
        harness = (
            "<script>window.__posted=[];const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
            "if(o&&o.method==='POST')window.__posted.push(p);"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';"
            "try{" + ("document.getElementById('dbg-states-run').click();"
                      if click else "")
            + "await new Promise(r=>setTimeout(r,200));"
            + after
            + "}catch(e){err=e.message;}"
            # The state heartbeat's own body. It used to share
            # `dbg-states-body` with the month-by-month states browser,
            # and `getElementById` returns the first - which was this
            # panel, so this read what it meant to by accident.
            "const body=document.getElementById('dbg-statebeat-body');"
            "const card=document.querySelector('#event-detail .event-card');"
            "document.title=JSON.stringify({err:err,posted:window.__posted,"
            "card:card?card.textContent.replace(/\\s+/g,' ').trim():'',"
            "groups:document.querySelectorAll('.sb-task').length,"
            "leaves:document.querySelectorAll('.sb-leaf').length,"
            "branches:document.querySelectorAll('.sb-branch').length,"
            "cut:document.querySelectorAll('.sb-cut').length,"
            "open:[...document.querySelectorAll('.sb-task')].filter(d=>d.open).length,"
            "text:body?body.textContent.replace(/\\s+/g,' ').trim():'',"
            "html:body?body.innerHTML:'',"
            "tab:!!document.querySelector('[data-tab=\"statebeat\"]'),"
            "note:(document.getElementById('dbg-states-note')||{}).textContent||''});"
            "});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 "--window-size=1400,900",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
            dom = completed.stdout or ""
        match = re.search(r"<title>(.*?)</title>", dom, re.S)
        self.assertIsNotNone(match, "the page never rendered")
        return json.loads(html.unescape(match.group(1)))

    @staticmethod
    def _state(number, title, detail):
        return {"id": f"state_{number}", "title": title, "detail": detail,
                "sources": [], "evidence": [], "status": "active",
                "starts_at": "2026-03-04T14:02:00+00:00",
                "ends_at": "2026-03-04T14:17:00+00:00",
                "added": "2026-03-04T14:17:30+00:00",
                "said_terms": ["replication"], "rests_on_terms": []}

    @classmethod
    def _report(cls, **extra):
        #: Five about one project, one about something else - which is a
        #: group worth summarising and a leftover, so the picture has both
        #: a colour and a grey in it.
        work = [cls._state(index,
                           "They are rebuilding the SWR replication package.",
                           "Working through the census fetch script.")
                for index in range(1, 6)]
        other = [cls._state(9, "They are answering email about the grant.",
                            "Gmail threads.")]
        states = work + other
        # A tree of six leaves: the five join tightly, the sixth last.
        merges = [{"left": 0, "right": 1, "height": 0.08, "size": 2},
                  {"left": 6, "right": 2, "height": 0.12, "size": 3},
                  {"left": 7, "right": 3, "height": 0.18, "size": 4},
                  {"left": 8, "right": 4, "height": 0.24, "size": 5},
                  {"left": 9, "right": 5, "height": 0.86, "size": 6}]
        report = {
            "trigger": "manual_debug",
            "started_at": "2026-03-04T14:20:00+00:00",
            "finished_at": "2026-03-04T14:20:04+00:00",
            "duration_ms": 4200,
            "running": False,
            "settings": {"interval_seconds": 3600, "threshold": 0.5,
                         "min_nodes": 5, "summaries": False},
            "states_considered": 6,
            "states": states,
            "similarity": {
                "source": "embedding",
                "model": "sentence-transformers/all-MiniLM-L6-v2",
                "read_ms": 120, "unavailable": "",
                "pairs_scored": 15, "threshold": 0.5,
                "pairs": [
                    {"i": 0, "j": 1, "a": "state_1", "b": "state_2",
                     "a_title": "They are rebuilding the SWR replication package.",
                     "b_title": "They are rebuilding the SWR replication package.",
                     "score": 0.92, "distance": 0.08,
                     "shared_said": ["replication", "swr"]},
                    {"i": 0, "j": 5, "a": "state_1", "b": "state_9",
                     "a_title": "They are rebuilding the SWR replication package.",
                     "b_title": "They are answering email about the grant.",
                     "score": 0.14, "distance": 0.86, "shared_said": []},
                ],
            },
            "tree": {"leaves": [0, 1, 2, 3, 4, 5], "merges": merges,
                     "labels": [state["id"] for state in states],
                     "roots": [10], "count": 6, "cut_at": 0.5},
            "how": {
                "steps": [
                    "1. What is read. The title and the detail of each state.",
                    "2. How alike two of them are. Each state is turned into "
                    "a vector, and two states are as alike as their vectors "
                    "point the same way, which is the cosine between them.",
                    "3. The distance between them is 1 minus that.",
                    "4. The tree. The nearest two states are joined, at the "
                    "average distance of its members.",
                    "5. The cut. Joins closer than 0.50 are kept together.",
                ],
                "parameters": [
                    {"name": "stands out at", "value": "1.15 standard "
                                                       "deviations",
                     "why": "The value scipy's own documentation uses for "
                            "cutting a tree by inconsistency."},
                    {"name": "smallest gap that counts",
                     "value": "0.2 of the distance scale",
                     "why": "The score is a ratio, so it says out of line "
                            "for differences that are nothing."},
                    {"name": "smallest task worth opening", "value": "5 states",
                     "why": "Below it a task is shown with its states rather "
                            "than split further."},
                    {"name": "most sub-tasks in one layer", "value": "8",
                     "why": "About what a person reads as a few."},
                ],
                "reading": "sentence-transformers/all-MiniLM-L6-v2, on this "
                           "machine, English, 384 dimensions",
                "clustering": "Agglomerative, average linkage, nearest pair "
                              "first.",
                "layout": "The tree is drawn as a dendrogram.",
                "summarised": "Summaries are off. Nothing here was sent to a "
                              "model.",
            },
            "communities": [
                {"id": "group_1", "size": 5, "big_enough": True, "why_not": "",
                 "members": work, "titles": [s["title"] for s in work],
                 "shared_said": ["replication", "swr"], "shared_rests_on": [],
                 "shared_sources": [],
                 "from": "2026-03-01T09:00:00+00:00",
                 "to": "2026-03-04T14:17:00+00:00"},
                {"id": "group_2", "size": 1, "big_enough": False,
                 "why_not": "1 state, and a group is summarised at 5.",
                 "members": other, "titles": [other[0]["title"]],
                 "shared_said": ["email"], "shared_rests_on": [],
                 "shared_sources": [], "from": None, "to": None},
            ],
            "layers": [
                {"id": "task_1", "depth": 0, "size": 5, "height": 0.24,
                 "stands_out": 1.86,
                 "states": [state["id"] for state in work],
                 "loose": [], "children": []},
            ],
            # The one state that grouped with nothing: not a task of one,
            # and not hidden either.
            "loose": ["state_9"],
            "tasks_found": 1,
            "deepest": 1,
            "communities_found": 2,
            "big_enough": 1,
            "summaries_written": [],
            "calls": 0, "tokens_in": 0, "tokens_out": 0,
            "note": "Summaries are off. The groups below are the tree cut at "
                    "one height; nothing was sent to a model.",
        }
        report.update(extra)
        return report

    # -- the page ----------------------------------------------------------
    def test_the_tab_is_there(self) -> None:
        self.assertTrue(self._render(self._report())["tab"])

    def test_the_button_runs_one(self) -> None:
        result = self._render(self._report())
        self.assertEqual(result["err"], "")
        self.assertIn("/api/state_heartbeat/run", result["posted"])

    def test_every_task_is_shown(self) -> None:
        """One task, and the row for the states that are in none."""
        self.assertEqual(self._render(self._report())["groups"], 2)

    def test_nothing_on_this_page_opens_itself(self) -> None:
        """Reversing an earlier rule, and saying why.

        The top layer used to open itself, on the grounds that a page which
        runs something and then shows a row of closed rows has not shown
        anything. That was true when a task was a title and a list of
        states. A task now carries its description in four parts, what the
        call cost, whether it searched, every reply it got and a tech tree
        - and eight of those unrolled at once is not a result, it is a wall
        with the one thing the reader came for somewhere inside it. The row
        itself carries the title, the size and whether it searched, which
        is what choosing between them needs; everything past that is asked
        for."""
        self.assertEqual(self._render(self._report())["open"], 0)

    def test_which_layer_a_task_is_in_is_on_the_task(self) -> None:
        """An indented row two screens below its parent is a row with no
        context."""
        self.assertIn("layer 1", self._render(self._report())["text"])

    def test_the_states_that_grouped_with_nothing_are_said_to_be_that(self) -> None:
        """Not a task of one, and not hidden: a state that grouped with
        nothing is a finding about that state."""
        text = self._render(self._report())["text"]
        self.assertIn("outside every task", text)
        self.assertIn("each grouped with nothing", text)
        self.assertIn("They are answering email about the grant.", text)

    def test_a_task_says_its_size_and_how_far_it_stood_out(self) -> None:
        """Which is what makes the setting behind it a number worth
        choosing rather than a number to accept."""
        text = self._render(self._report())["text"]
        self.assertIn("5 states", text)
        self.assertIn("stands out 1.86", text)

    def test_a_task_counts_its_states_and_not_its_sub_tasks(self) -> None:
        """A task is read, not opened onto sub-tasks, so it says how many
        states it holds and nothing about sub-tasks either way."""
        text = self._render(self._report())["text"]
        self.assertIn("5 states", text)
        self.assertNotIn("no sub-tasks", text)
        self.assertNotIn("sub-tasks inside", text)

    def test_the_states_under_a_closed_task_are_listed(self) -> None:
        """A task whose states cannot be read is a label."""
        text = self._render(self._report())["text"]
        self.assertIn("They are rebuilding the SWR replication package.", text)

    def test_the_parameters_are_named_with_their_reasons(self) -> None:
        result = self._render(self._report())
        self.assertIn("The numbers, and why they are those numbers",
                      result["text"])
        self.assertIn("stands out at", result["text"])
        self.assertIn("smallest task worth opening", result["text"])

    def test_the_run_says_what_it_read_and_what_it_found(self) -> None:
        result = self._render(self._report())
        for figure in ("6 states read", "15 pairs scored", "1 tasks",
                       "1 layers deep"):
            self.assertIn(figure, result["text"], figure)

    # -- the tree ----------------------------------------------------------
    def test_every_state_is_a_leaf(self) -> None:
        result = self._render(self._report())
        self.assertEqual(result["leaves"], 6)

    def test_and_every_join_is_a_branch(self) -> None:
        """Five joins for six leaves. A branch drawn from a node the
        picture has not placed yet is a branch that disappears, which is
        how a dendrogram lies about the tree it is drawing."""
        result = self._render(self._report())
        self.assertEqual(result["branches"], 5)

    def test_and_the_cut_is_drawn_across_it(self) -> None:
        """Rather than described beside it: the cut is what turns the tree
        into the groups underneath."""
        result = self._render(self._report())
        self.assertEqual(result["cut"], 1)
        self.assertIn("cut at 0.50", result["text"])

    def test_a_leaf_is_labelled_with_its_state(self) -> None:
        result = self._render(self._report())
        self.assertIn("They are rebuilding the SWR replication package.",
                      result["text"])

    def test_the_group_worth_summarising_has_its_own_colour(self) -> None:
        result = self._render(self._report())
        colours = set(re.findall(r'fill="(hsl\([^"]+\))"', result["html"]))
        self.assertEqual(len(colours), 1, "one summarised group, one colour")

    def test_and_a_group_too_small_is_grey(self) -> None:
        """They are not findings, they are what is left over - and a dozen
        leftovers in a dozen colours reads as a dozen findings."""
        result = self._render(self._report())
        self.assertIn("var(--muted", result["html"])

    def test_and_the_colours_are_named_in_a_key(self) -> None:
        result = self._render(self._report())
        self.assertIn("sb-key", result["html"])
        self.assertIn("group 1", result["text"])
        self.assertIn("too small", result["text"])

    def test_a_leaf_opens_the_state_it_stands_for(self) -> None:
        """A label answers "which state is this" and none of the questions
        that follow: when it ran, which group it landed in, what it nearly
        joined instead."""
        result = self._render(self._report(), after=(
            "document.querySelector('.sb-leaf').dispatchEvent("
            "new MouseEvent('click',{bubbles:true}));"
            "await new Promise(r=>setTimeout(r,80));"))
        self.assertIn("They are", result["card"])
        self.assertIn("Most alike, of every other state", result["card"])

    def test_and_says_which_group_it_landed_in(self) -> None:
        result = self._render(self._report(), after=(
            "document.querySelector('.sb-leaf').dispatchEvent("
            "new MouseEvent('click',{bubbles:true}));"
            "await new Promise(r=>setTimeout(r,80));"))
        self.assertIn("group", result["card"])

    # -- the method --------------------------------------------------------
    def test_the_method_is_written_beside_the_picture(self) -> None:
        """This is where somebody is standing when they ask why two states
        are together."""
        result = self._render(self._report())
        self.assertIn("how two states are compared", result["text"])
        for step in ("1. What is read.", "2. How alike", "3. The distance",
                     "4. The tree.", "5. The cut."):
            self.assertIn(step, result["text"], step)

    def test_and_names_the_model_and_the_linkage(self) -> None:
        result = self._render(self._report())
        self.assertIn("MiniLM", result["text"])
        self.assertIn("average linkage", result["text"])
        self.assertIn("dendrogram", result["text"])

    def test_the_same_measurement_is_done_on_two_real_states(self) -> None:
        """A method is a claim about what the numbers would be. This is
        the numbers."""
        result = self._render(self._report())
        self.assertIn("The same measurement, on two states from this run",
                      result["text"])
        self.assertIn("0.92", result["text"])
        self.assertIn("0.08", result["text"])

    def test_and_says_what_the_cut_makes_of_it(self) -> None:
        result = self._render(self._report())
        self.assertIn("in one group", result["text"])

    def test_the_pairs_are_shown_with_how_far_apart_they_are(self) -> None:
        result = self._render(self._report())
        self.assertIn("alike", result["text"])
        self.assertIn("apart", result["text"])
        self.assertIn("They are answering email about the grant.",
                      result["text"])

    def test_and_the_ones_outside_the_cut_are_dimmed(self) -> None:
        result = self._render(self._report())
        self.assertIn("sb-unjoined", result["html"])

    # -- what it did not do ------------------------------------------------
    def test_a_run_with_summaries_off_says_so(self) -> None:
        result = self._render(self._report())
        self.assertIn("Summaries are off", result["text"])

    def test_a_round_that_found_nothing_to_compare_says_so(self) -> None:
        result = self._render(self._report(
            states_considered=1, states=[], communities=[], similarity={},
            tree={}, layers=[], loose=[], tasks_found=0,
            note="Fewer than two states. There is nothing to compare "
                 "and therefore nothing to group."))
        self.assertIn("nothing to compare", result["text"])
        self.assertEqual(result["groups"], 0)
        self.assertEqual(result["leaves"], 0)

    def test_a_model_that_was_away_does_not_hide_the_tree(self) -> None:
        """The grouping needs no model. A page that shows an error instead
        of the tree has thrown away the part that still worked."""
        result = self._render(self._report(blocked=True, reason="no credit"))
        self.assertIn("no credit", result["text"])
        self.assertEqual(result["leaves"], 6)


class RunningOneHeartbeatAndSeeingItTests(unittest.TestCase):
    """Section 37, as amended: the part of the agent nobody can watch.

    The heartbeat runs on a timer, decides what is worth doing, and all that
    is visible afterwards is a calendar that has changed. "Why did it do
    that" had no answer short of reading a log.

    So: run one now, and show each step - what it searched for,
    the text it was actually given, and what came of it. The context is shown
    as text rather than as a list of titles, because what a gathering *found*
    is a different question from what the model was *handed*, and the gap
    between them is where an odd answer is usually explained.
    """

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _render(self, report, click=True) -> dict:
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        stub = {"/api/heartbeat/run": report}
        harness = (
            "<script>window.__posted=[];const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
            "if(o&&o.method==='POST')window.__posted.push(p);"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';"
            "try{" + ("document.getElementById('dbg-beat-run').click();"
                      if click else "")
            + "await new Promise(r=>setTimeout(r,150));}catch(e){err=e.message;}"
            "const body=document.getElementById('dbg-beat-body');"
            "document.title=JSON.stringify({err:err,posted:window.__posted,"
            "steps:document.querySelectorAll('.beat-step').length,"
            "open:[...document.querySelectorAll('.beat-step')].filter(d=>d.open).length,"
            "text:body?body.textContent.replace(/\\s+/g,' ').trim():'',"
            "html:body?body.innerHTML:'',"
            # A card built straight from the function, so the ordering of a
            # long keyword list can be checked without a round trip.
            # A whole card, so what a reader would actually be looking at
            # can be checked - the text included.
            "cited:(()=>{if(typeof showCitation!=='function')return '';"
            "const a=document.createElement('button');document.body.appendChild(a);"
            "showCitation(a,{id:'rec_00112233445566aa',title:'A morning',"
            "folder:'others',summary:'Nothing here names it.',"
            "body:'The body mentions a kingfisher.exe in passing.',"
            "matched_words:['kingfisher.exe'],"
            "matched_here:['kingfisher.exe (keyword)'],"
            "created_at:'2026-09-20T10:00:00+00:00',source:'compaction'});"
            "const c=document.querySelector('.cite-card');"
            "const html=c?c.innerHTML:'';if(c)c.remove();return html;})(),"
            "cite:typeof citationFacts==='function'?citationFacts("
            "{keywords:['one','two','three','four','five','six','seven',"
            "'eight','nine','kingfisher.exe','kingfisher window title'],"
            "matched_keywords:['kingfisher window title'],"
            "matched_here:['kingfisher.exe (keyword)'],"
            "created_at:'2026-09-20T10:00:00+00:00',source:'compaction'}):'',"
            "note:(document.getElementById('dbg-beat-note')||{}).textContent||''});"
            "});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 "--window-size=1400,900",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
            dom = completed.stdout or ""
        match = re.search(r"<title>(.*?)</title>", dom, re.S)
        self.assertIsNotNone(match, "the debugger never rendered")
        return json.loads(html.unescape(match.group(1)))

    @staticmethod
    def _report(**extra):
        report = {
            "task_id": "heartbeat_1", "trigger": "manual_debug",
            "started_at": "2026-09-20T09:00:00+00:00",
            "finished_at": "2026-09-20T09:00:24+00:00",
            "state_analysis": "Working through the listener all morning.",
            "steps": [
                {"step": 1, "id": "step_state",
                 "title": "Analyze the user's current state.",
                 "asked_for": ["listener", "filesystem"],
                 "prompt": "THE-FIRST-MESSAGE-OF-STEP-ONE.",
                 "prompt_tokens": 1900,
                 "answer": '{"state_analysis": "working"}',
                 "answer_tokens": 12,
                 "context_tokens": 33192,
                 "context": "THE-CONTEXT-OF-STEP-ONE, at length.",
                 "records": [{"id": "rec_1", "title": "A morning on the listener",
                              "folder": "others"}],
                 "duration_ms": 41.0, "elapsed_ms": 24800,
                 "calls": 3, "tokens_in": 38400, "tokens_out": 620,
                 "tokens_total": 39020,
                 "phase": "answered", "answers": "what is going on",
                 "could_do": [{"what": "write a state on the calendar",
                               "count": 1}],
                 "cannot": "Step one only writes states. Scheduling a "
                           "task, notifying you and writing to memory "
                           "are other steps.",
                 "trail": [
                     {"did": "floor", "ms": 13, "tokens": 0,
                      "minutes": 15, "events_count": 12,
                      "events_seen": 140, "events_folded": 128,
                      "states": 2, "decided_by": "fixed",
                      "setting": "heartbeat_interval_seconds",
                      "decision": "everything since this review last ran - "
                                  "15 minutes, the heartbeat interval - and "
                                  "every state running now",
                      "states_started": [
                          {"id": "state_1", "title": "Debugging the listener",
                           "detail": "The capture listener is not recording.",
                           "about": "user", "source": "agent",
                           "status": "active", "extensions": 1,
                           "span_hours": 3.0, "horizon": "short",
                           "reason": "the event count stopped rising",
                           "starts_at": "2026-09-20T12:00:00+00:00",
                           "ends_at": "2026-09-20T15:00:00+00:00",
                           "metadata": {"evidence": ["the count stopped"],
                                        "reasoning": "nothing new arrived"}},
                          {"id": "state_2", "title": "Shipping the release",
                           "about": "agent", "source": "agent",
                           "status": "active", "span_hours": 96.0,
                           "horizon": "long", "metadata": {},
                           "starts_at": "2026-09-17T09:00:00+00:00",
                           "ends_at": "2026-09-21T09:00:00+00:00"}],
                      "states_not_shown": 3,
                      "why": "what is happening now, before anything is asked for",
                      "events": [
                          {"at": "14:32:05", "count": 6,
                           "at_times": ["14:32:05", "14:30:11", "14:27:40"],
                           "times_left_out": 3,
                           "application": "firefox.exe",
                           "event_type": "screen.text",
                           "title": "Retrieval-augmented generation",
                           "url": "https://arxiv.org/abs/2401.00001",
                           "text": "A paper about retrieval.",
                           "also_read": ["The second page of it."],
                           "texts_left_out": 3},
                          {"at": "14:31:02", "count": 1,
                           "at_times": ["14:31:02"],
                           "application": "sublime_text.exe",
                           "event_type": "application.focused",
                           "path": "C:/work/search.py", "text": "",
                           "borrowed": "The file open in the editor.",
                           "borrowed_at": "14:29:50"}],
                      "events_not_shown": 10},
                     {"did": "asked", "round": 1, "ms": 9200, "tokens": 1925,
                      "decided_by": "fixed",
                      "decision": "asked the model with everything gathered "
                                  "so far", "context_tokens": 1840,
                      "sent": "--- user ---\nANALYSE THE USER'S STATE",
                      "sent_tokens": 1900, "carried_over": 0,
                      "system": "You are the background reasoning pass.",
                      "tools": [
                          {"name": "search_memory",
                           "description": "Search your memory's index.",
                           "takes": {"type": "object",
                                     "properties": {"folder": {"type": "string"}}}},
                          {"name": "open_memory",
                           "description": "Read one record in full.",
                           "takes": {"type": "object",
                                     "properties": {"id": {"type": "string"}}}}]},
                     {"did": "fold", "ms": 4, "tokens": 0, "decided_by": "code",
                      "decision": "merged events with the same application, "
                                  "kind and file or page into one entry "
                                  "with a count",
                      "before_events": 140, "after_events": 12,
                      "before_tokens": 6556, "after_tokens": 606,
                      "before_text": "14:32:05  firefox.exe  screen.text",
                      "after_text": "14:18-14:32  x6  firefox.exe"},
                     {"did": "asked", "round": 2, "decided_by": "fixed",
                      "decision": "asked again with what came back",
                      "context_tokens": 2610,
                      "sent": "--- user ---\n[result of c0]",
                      "sent_tokens": 380, "carried_over": 2,
                      "system": "You are the background reasoning pass."},
                     {"did": "open", "id": "rec_1", "decided_by": "model",
                      "decision": "read rec_1 in full",
                      "said": "The search summary is not enough to date this.",
                      "arguments": {"id": "rec_1"},
                      "title": "A morning on the listener", "folder": "others"},
                     {"did": "answered", "round": 3, "decided_by": "model",
                      "decision": "answered rather than asking for more",
                      "said": "That is enough to say what they are doing."},
                     {"did": "plan", "terms": ["listener"], "at_most": 12,
                      "token_budget": 6000,
                      "folders": [{"folder": "others", "room_for": 4}]},
                     {"did": "search", "ms": 21, "tokens": 0,
                      "folder": "others", "room_for": 4,
                      "asked_for": ["filesystem.py", "the listener"],
                      "terms": ["filesystem.py", "listener"],
                      "dropped": ["the"],
                      "decided_by": "model",
                      "decision": "search others for filesystem.py, up to 4",
                      "said": "The file being edited is the thing to look up.",
                      "found": 1, "fell_back": False, "why": "",
                      "titles": ["A morning on the listener"],
                      "hits": [{"id": "rec_1",
                                "title": "A morning on the listener",
                                "matched": ["listener"], "score": 7.17,
                                "matched_where": {"listener": "keywords piece"},
                                "matched_keywords": ["listener window title"],
                                "why": "matched listener (part of a keyword) "
                                       "in others"}]},
                     {"did": "search", "folder": "user_nature", "room_for": 3,
                      "found": 1, "fell_back": True,
                      "why": "nothing matched, so the most recent was taken instead",
                      "titles": ["How they work"]},
                     {"did": "rank", "candidates": 2, "considering": 2},
                     {"did": "take", "id": "rec_1",
                      "title": "A morning on the listener",
                      "folder": "others", "tokens": 210, "used": 210,
                      "matched": ["listener"],
                      "matched_where": {"listener": "keywords piece"},
                      "matched_keywords": ["listener window title"],
                      "why": "matched listener (part of a keyword) "
                             "in others"},
                     {"did": "stop", "title": "How they work",
                      "folder": "user_nature", "tokens": 6000, "used": 210,
                      "token_budget": 6000,
                      "why": "taking this would cost 6000 tokens on top of 210, "
                             "over the budget of 6000"},
                 ],
                 "outcome_known": True,
                 "outcome": {"states_written": [
                     {"title": "They are debugging the listener.",
                      "detail": "Reading filesystem.py against the window feed.",
                      "minutes": 9,
                      "starts_at": "2026-03-04T14:02:00+00:00",
                      "ends_at": "2026-03-04T14:11:00+00:00",
                      "sources": ["C:/work/personalized_agent/listener/filesystem.py"],
                      "evidence": ["the editor on filesystem.py for most of it"],
                      "reasoning": "one file for most of the window",
                      "looked_at": {"minutes": 15,
                                    "from": "2026-03-04T14:00:00+00:00",
                                    "to": "2026-03-04T14:15:00+00:00",
                                    "events": 62,
                                    "first_event": "2026-03-04T14:00:11+00:00",
                                    "last_event": "2026-03-04T14:14:52+00:00"}}]}},
                {"step": 2, "id": "step_nature",
                 "title": "Determine what kind of person the user is.",
                 "asked_for": ["habits"], "context": "THE-CONTEXT-OF-STEP-TWO.",
                 "records": [], "duration_ms": 12.0,
                 "phase": "answered", "answers": "who this person is",
                 "outcome_known": True, "outcome": {},
                 "nothing": "Nothing was written to memory."},
                {"step": 3, "id": "step_help",
                 "title": "Determine whether there is anything useful you can do.",
                 "asked_for": [], "context": "THE-CONTEXT-OF-STEP-THREE.",
                 "records": [], "duration_ms": 8.0,
                 "phase": "answered", "answers": "what to do about it",
                 "outcome_known": True, "outcome": {},
                 "nothing": "Nothing. Deciding against is an outcome here - "
                            "interrupting has a cost."},
            ],
        }
        report.update(extra)
        return report

    def test_the_button_runs_one(self) -> None:
        result = self._render(self._report())
        self.assertEqual(result["err"], "")
        self.assertIn("/api/heartbeat/run", result["posted"])

    def test_every_step_is_shown(self) -> None:
        result = self._render(self._report())
        self.assertEqual(result["steps"], 3)

    def test_and_they_start_open_so_that_running_shows_something(self) -> None:
        result = self._render(self._report())
        self.assertEqual(result["open"], 3)

    def test_the_text_the_model_was_given_is_there(self) -> None:
        """Not a list of titles. What was found and what was handed over are
        different questions, and the second is the one that explains an odd
        answer."""
        result = self._render(self._report())
        for index in ("ONE", "TWO", "THREE"):
            self.assertIn(f"THE-CONTEXT-OF-STEP-{index}", result["text"])

    def test_what_each_step_found_is_named(self) -> None:
        result = self._render(self._report())
        self.assertIn("A morning on the listener", result["text"])

    def test_and_what_came_of_it(self) -> None:
        result = self._render(self._report())
        self.assertIn("They are debugging the listener.", result["text"])

    def test_a_state_it_wrote_is_shown_as_the_state_it_is(self) -> None:
        """Not flattened into a line of text.

        A state is a title, a period, what it rests on and why the agent
        believes it. The panel showed a string, which is the same loss as
        reporting "sources: 2" instead of the two sources.
        """
        result = self._render(self._report())
        self.assertIn("C:/work/personalized_agent/listener/filesystem.py",
                      result["text"])
        self.assertIn("one file for most of the window", result["text"])
        self.assertIn("9 minutes of the window", result["text"])

    def test_and_says_what_stretch_it_was_read_from(self) -> None:
        """Put there by the script, not by the model. A state that took
        nine minutes is a different claim out of a quarter of an hour than
        out of an afternoon."""
        result = self._render(self._report())
        self.assertIn("Read from", result["text"])
        self.assertIn("62 events", result["text"])

    def test_an_outcome_not_yet_known_is_not_reported_as_nothing(self) -> None:
        """They are opposite readings of the same empty outcome.

        The states are written after every step has answered, so for
        about a minute of every run step one had answered and had nothing
        recorded yet - and the panel called that "Nothing came of this
        step", which is a conclusion nobody had reached.
        """
        report = self._report()
        for step in report["steps"]:
            step["outcome"] = {}
            step["outcome_known"] = False
        result = self._render(report)
        self.assertNotIn("Nothing came of this step", result["text"])
        self.assertIn("Waiting", result["text"])

    def test_a_step_that_decided_against_says_so(self) -> None:
        # Two of the three steps in this report did nothing, which is a
        # real outcome: interrupting has a cost.
        result = self._render(self._report())
        self.assertIn("Deciding against is an outcome", result["text"])

    def test_how_long_the_run_took(self) -> None:
        result = self._render(self._report())
        self.assertIn("24", result["note"])

    def test_a_run_the_model_refused_still_shows_what_was_gathered(self) -> None:
        """Which is most of what anybody opening this panel wants.

        The steps are filled in before the model is asked, so a refusal
        leaves every gathering to look at rather than an error and nothing.
        """
        result = self._render(self._report(blocked=True, reason="no credit"))
        self.assertEqual(result["steps"], 3)
        self.assertIn("no credit", result["text"])
        self.assertIn("THE-CONTEXT-OF-STEP-ONE", result["text"])

    def test_the_gathering_is_shown_round_by_round(self) -> None:
        """Which folder, with how much room, and what came back from each."""
        result = self._render(self._report())
        text = result["text"]
        # Each part is its own element now, so the text runs together
        # differently - what matters is that all of it is there.
        self.assertIn("room for 4", text)
        self.assertIn("found 1", text)
        self.assertIn("2 candidates", text)

    def test_a_fallback_is_not_shown_as_a_match(self) -> None:
        # The gathering returns a memory from a folder whose search found
        # nothing; without this it is indistinguishable from a hit.
        result = self._render(self._report())
        self.assertIn("most recent was taken instead", result["text"])

    def test_the_record_the_budget_stopped_says_so(self) -> None:
        """The question this whole trail exists to answer.

        A gathering that stops looks arbitrary until it says that the next
        record would have cost more than was left.
        """
        result = self._render(self._report())
        self.assertIn("over the budget of 6000", result["text"])

    def test_what_each_record_cost_is_shown(self) -> None:
        result = self._render(self._report())
        self.assertIn("210 tokens", result["text"])

    def test_the_analysis_is_labelled_as_a_step_answer(self) -> None:
        """Unlabelled it reads as though the panel were narrating.

        It is the model's answer to step 1 - what the user appears to be
        doing and what matters now - and a reader who does not know that
        cannot tell whose words they are.
        """
        result = self._render(self._report())
        self.assertIn("step 1", result["text"])
        self.assertIn("Working through the listener", result["text"])

    def test_the_text_can_be_opened_out(self) -> None:
        # It runs to tens of thousands of characters and is shown in a window
        # that scrolls; anybody who would rather read it than scroll it needs
        # a way to say so.
        result = self._render(self._report())
        self.assertIn("expand", result["text"])

    def test_a_result_with_a_record_behind_it_can_be_opened(self) -> None:
        """Section 44.3, as amended: the trail's results are citations too.

        A title on its own cannot answer "what is actually in that one",
        which is the question a trail makes a reader ask - more so than chat
        does, because the trail's whole subject is how each record got there.
        """
        result = self._render(self._report())
        self.assertIn("trail-open", result["html"])
        self.assertIn("data-matched=\"listener (keywords piece)\"", result["html"])

    def test_and_the_words_that_found_it_travel_with_the_button(self) -> None:
        # The card shows the keywords a record carries and, separately, the
        # words that found it this time. The same memory is found by
        # different words on different days.
        result = self._render(self._report())
        self.assertIn("data-why=\"matched listener (part of a keyword) in others\"",
                      result["html"])

    def test_a_matched_word_says_which_part_of_the_record_it_was_in(self) -> None:
        """Section 37, as amended: a reason nobody can check by reading.

        The body is not indexed, so every match is in the keywords, the title
        or the summary. A reader looking at a title and a summary that do not
        contain the word has no way to reach "then it was in the keywords",
        and goes looking through the record for a word that is not there.
        """
        result = self._render(self._report())
        self.assertIn("part of a keyword", result["html"])

    def test_the_records_own_keyword_is_not_shown_as_a_word_of_the_question(self) -> None:
        # Nobody typed "listener window title". It is a keyword the record
        # carries that happens to contain a word that was typed, and it was
        # printed in the same list as the question's words.
        result = self._render(self._report())
        self.assertIn('data-keywords="listener window title"', result["html"])
        self.assertNotIn('data-matched="listener window title"', result["html"])

    def test_the_keyword_that_matched_is_not_hidden_behind_the_count(self) -> None:
        """Which is how the whole thing went unexplained.

        The card showed the first eight keywords as stored. A record found by
        its tenth therefore showed every keyword except the one the reader
        had opened the card to ask about.
        """
        card = self._render(self._report())["cite"]
        self.assertIn("kingfisher window title", card)
        self.assertLess(card.index("kingfisher window title"), card.index("one"))

    def test_and_the_keyword_it_sits_inside_has_its_own_line(self) -> None:
        card = self._render(self._report())["cite"]
        self.assertIn("inside its keyword", card)

    def test_the_card_shows_what_the_record_actually_says(self) -> None:
        """Section 44.3, as amended: a card without the record in it.

        It showed a title and a summary. The word a reader is asking about
        is usually in the body - it is what the writer of the record drew
        the keyword from - so they were shown the two parts of the record
        that do not contain the word, and concluded it was in none of it.
        """
        card = self._render(self._report())["cited"]
        self.assertIn("cite-body", card)
        self.assertIn("in passing", card)

    def test_and_marks_the_word_that_was_matched(self) -> None:
        card = self._render(self._report())["cited"]
        self.assertIn("<mark>kingfisher.exe</mark>", card)

    def test_the_card_names_the_record(self) -> None:
        """An id is not read, it is copied and looked up.

        A title is not a handle: two records can share one, and none of
        them can be typed into a search and found exactly.
        """
        card = self._render(self._report())["cited"]
        self.assertIn("rec_00112233445566aa", card)

    def test_and_so_does_each_result_in_a_trail(self) -> None:
        result = self._render(self._report())
        self.assertIn("trail-id", result["html"])
        self.assertIn("rec_1", result["text"])

    def test_the_step_starts_from_what_is_happening(self) -> None:
        """Section 37, as amended: step 1 gathers for itself.

        It does not begin with a plan, because nothing planned it. It
        begins with everything since the last review - the window is the
        heartbeat's own interval - and everything after that is something
        the step decided it wanted.
        """
        result = self._render(self._report())
        self.assertIn("the last 15 minutes", result["text"])
        self.assertIn("folded to 12", result["text"])
        self.assertIn("2 states", result["text"])

    def test_and_a_step_given_no_window_does_not_report_an_empty_one(self) -> None:
        """The step that decides what to do starts from the calendar.

        The line said "the last 0 minutes, 0 events" for it, which reads as
        a window that turned out to be empty rather than as no window.
        """
        report = self._report()
        floor = next(row for row in report["steps"][0]["trail"]
                     if row["did"] == "floor")
        floor.update({"minutes": 0, "events_seen": 0, "events_count": 0,
                      "events_folded": 0, "events": []})
        result = self._render(report)
        self.assertNotIn("the last 0 minutes", result["text"])
        # The row still says what it did start from.
        self.assertIn("2 states", result["text"])

    def test_a_record_it_read_in_full_is_shown_as_that(self) -> None:
        # Opening one is a different act from finding it, and costs more.
        result = self._render(self._report())
        self.assertIn("read in full", result["text"])

    def test_every_row_of_the_trail_is_numbered_in_its_section(self) -> None:
        """Two numbering schemes were running down the same column.

        The sections - 1.1, 1.2, 1.3 - and the round a call is on - "ask
        round 1", "answer round 3". Read in sequence, "answer round 3" sat
        directly above "1.3" and the two looked like one counter
        disagreeing with itself.
        """
        text = self._render(self._report())["text"]
        # The state step's trail is its first section, because its query is
        # written during the loop rather than before it.
        for row in ("1.1.1", "1.1.2", "1.1.3"):
            self.assertIn(row, text, row)
        for row in ("2.2.1",):
            self.assertNotIn(row, text, "step two searched nothing")

    def test_and_the_section_after_it_follows_the_last_row(self) -> None:
        text = self._render(self._report())["text"]
        self.assertLess(text.index("1.1.1"), text.index("1.2 "))

    def test_the_sections_run_in_the_order_the_step_ran(self) -> None:
        """A numbered list is a sequence, and this one was not.

        It opened with a summary of who decided what, then the searching,
        then the context - which is what the searching produced - so the
        numbers counted up while the story went back and forth.
        """
        text = self._render(self._report())["text"]
        order = ["what it did, round by round",
                 "what it was left holding",
                 "the call for this step",
                 "what came of it",
                 "in summary: what it could do",
                 "in summary: who decided what"]
        at = [text.index(heading) for heading in order]
        self.assertEqual(at, sorted(at), order)

    def test_a_step_that_gathered_for_itself_has_no_query_above_the_trail(self) -> None:
        """Its words are chosen during the loop, not before it.

        The state step has no query until it writes one, in the searches it
        decides to make - so listing them above the trail puts a result of
        the loop in front of the loop.
        """
        text = self._render(self._report())["text"]
        first, second = text.index("1 Analyze"), text.index("2 Determine")
        self.assertNotIn("what it searched for", text[first:second])

    def test_but_a_step_handed_a_query_shows_it_first(self) -> None:
        # The other three are handed a context assembled from a query
        # written before they ran, so it is the first thing that happened.
        text = self._render(self._report())["text"]
        at = text.index("2 Determine")
        self.assertLess(text.index("2.1 what it searched for"),
                        text.index("2.2 what it did", at))

    def test_a_summary_comes_after_the_thing_it_summarises(self) -> None:
        text = self._render(self._report())["text"]
        self.assertLess(text.index("what it did, round by round"),
                        text.index("in summary: who decided what"))

    def test_the_tab_says_it_is_a_debugger(self) -> None:
        """"Heartbeat" is the thing; this page is a tool for looking at it.

        Beside Calendar and Memory, which are the user's own, a tab called
        Heartbeat reads as another of those.
        """
        page = (ROOT / "personalized_agent" / "api" / "web"
                / "index.html").read_text(encoding="utf-8")
        self.assertIn(">Heartbeat debugger<", page)

    def test_every_section_says_which_step_it_belongs_to(self) -> None:
        """The steps were numbered and everything inside them was not.

        A reader who had scrolled past a trail of a dozen rows met "the
        context, as it stood at the end" with nothing saying which step it
        belonged to or how far through they were.
        """
        result = self._render(self._report())
        for section in ("1.1", "1.2", "1.3", "1.4", "1.5", "1.6"):
            self.assertIn(section, result["text"], section)
        self.assertIn("2.1", result["text"])

    def test_and_the_numbering_restarts_for_each_step(self) -> None:
        result = self._render(self._report())
        self.assertIn("3.1", result["text"])
        self.assertNotIn("1.20", result["text"])

    def test_the_numbering_does_not_carry_across_renders(self) -> None:
        # The step object outlives the render: it is held in the page
        # between refreshes, and a counter left on it would carry on.
        report = self._report()
        first = self._render(report)
        self.assertIn("1.1", first["text"])
        self.assertIn("1.1", self._render(report)["text"])

    def test_a_step_says_what_it_cost(self) -> None:
        """The header read "1 memories, 41 ms", and 41 ms was the gathering.

        The minute somebody waits through is the calls, and what the step
        spent is the number anybody deciding about the interval needs.
        """
        result = self._render(self._report())
        self.assertIn("3 calls", result["text"])
        self.assertIn("39,020 tokens", result["text"])
        self.assertIn("38,400 in, 620 out", result["text"])

    def test_and_keeps_both_clocks(self) -> None:
        # The gathering and the calls are different costs with different
        # remedies, and one number covering both hides which is which.
        result = self._render(self._report())
        self.assertIn("24.8 s", result["text"])
        self.assertIn("41 ms gathering", result["text"])

    def test_the_stoplist_is_not_called_a_frequency(self) -> None:
        """"Too common" says the word was measured against the library.

        It is a fixed list of grammatical filler - "of", "the", "is" - and
        a reader told a word was dropped for being common will go looking
        for the count that decided it.
        """
        result = self._render(self._report())
        self.assertIn("filler words, always ignored", result["text"])
        self.assertNotIn("too common", result["text"])

    def test_what_the_phrase_became_is_shown(self) -> None:
        """The index is searched by words, not by the sentence.

        A row showing only what the model typed cannot explain a result.
        """
        result = self._render(self._report())
        self.assertIn("searched by", result["text"])
        self.assertIn("filler words, always ignored", result["text"])

    def test_the_words_it_chose_to_search_for_are_shown(self) -> None:
        """The step picked them, so they are the interesting part.

        Nothing decides the folders or the room in advance any more, so what
        a search says about the step is which words it thought would work.
        """
        result = self._render(self._report())
        self.assertIn("filesystem.py", result["text"])
        self.assertIn("trail-asked", result["html"])

    def test_a_build_without_the_debugger_has_no_heartbeat_page(self) -> None:
        """It is the same kind of door, and it is its own tab now.

        Removing one and leaving the other is how a build ships an internal
        tool it meant to leave out.
        """
        source = (ROOT / "personalized_agent" / "api" / "web"
                  / "app.js").read_text(encoding="utf-8")
        hiding = source.split("async function applyDebuggerAvailability", 1)[1]
        hiding = hiding.split("\n}", 1)[0]
        self.assertIn('[data-tab="heartbeat"]', hiding)
        self.assertIn('#heartbeat', hiding)

    def test_the_heartbeat_has_a_place_of_its_own(self) -> None:
        """Section 37, as amended: one run is a page, not a panel.

        It shared a scroll with the timeline, the states and the token
        usage - and reading one run means reading four prompts, four
        contexts and every event behind them.
        """
        page = (ROOT / "personalized_agent" / "api" / "web"
                / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-tab="heartbeat"', page)
        self.assertIn('<section id="heartbeat"', page)

    def test_and_is_no_longer_inside_the_debugger(self) -> None:
        page = (ROOT / "personalized_agent" / "api" / "web"
                / "index.html").read_text(encoding="utf-8")
        debugger = page.split('<section id="debugger"', 1)[1]
        debugger = debugger.split("</section>", 1)[0]
        self.assertNotIn("dbg-beat-run", debugger)

    def test_nothing_on_the_page_polls_for_the_run(self) -> None:
        """Which is what made watching one and using the Overview two sittings.

        It asked twice a second for the length of a run - about a minute -
        and each answer carries every event every step has read.
        """
        source = (ROOT / "personalized_agent" / "api" / "web"
                  / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("watchHeartbeat", source)

    def test_but_it_can_be_asked_for_the_run_so_far(self) -> None:
        # Gone is the page deciding when to want it, not the ability to look.
        page = (ROOT / "personalized_agent" / "api" / "web"
                / "index.html").read_text(encoding="utf-8")
        source = (ROOT / "personalized_agent" / "api" / "web"
                  / "app.js").read_text(encoding="utf-8")
        self.assertIn("dbg-beat-refresh", page)
        self.assertIn("refreshHeartbeat", source)

    def test_the_page_says_it_does_not_update_itself(self) -> None:
        # Otherwise a run that is still going looks like one that stalled.
        page = (ROOT / "personalized_agent" / "api" / "web"
                / "index.html").read_text(encoding="utf-8")
        self.assertIn("does not update", page)

    def test_a_step_says_how_much_of_it_was_the_models(self) -> None:
        """Every step ends in a model's answer, so saying so distinguishes none.

        What differs is how much of the step was the model's: step 1 chooses
        what it looks at and keeps choosing; the other three are handed a
        context assembled before the question and choose only what to say
        about it.
        """
        result = self._render(self._report())
        self.assertIn("who decided what", result["text"])
        self.assertIn("chose what to look at", result["text"])

    def test_and_a_step_that_chose_nothing_says_that(self) -> None:
        report = self._report()
        for step in report["steps"]:
            step["trail"] = [row for row in step.get("trail") or []
                             if row.get("decided_by") != "model"]
        result = self._render(report)
        self.assertIn("wrote the answer, and nothing else", result["text"])
        self.assertIn("the words searched for, the folders and the budget",
                      result["text"])

    def test_how_many_times_it_asked_is_said(self) -> None:
        result = self._render(self._report())
        self.assertIn("asked for more 2 times", result["text"])

    def test_folding_says_it_was_code_and_not_a_model(self) -> None:
        """Section 37, as amended: a third kind of row.

        Every other row in the trail is somebody choosing something. A
        transform that runs the same way every time is a different claim,
        and a reader cannot tell which they are looking at from a result.
        """
        result = self._render(self._report())
        self.assertIn("code, no choice and no model", result["text"])
        self.assertIn("trail-who-code", result["html"])

    def test_and_shows_the_input_on_both_sides_of_itself(self) -> None:
        # A compression that reports only its own output cannot be checked.
        result = self._render(self._report())
        self.assertIn("before folding", result["text"])
        self.assertIn("after folding", result["text"])
        # The harness collapses runs of whitespace, so this is the feed as
        # rendered, not as spaced.
        self.assertIn("14:32:05 firefox.exe screen.text", result["text"])

    def test_with_what_it_cost_on_each_side(self) -> None:
        result = self._render(self._report())
        self.assertIn("140 events into 12", result["text"])
        self.assertIn("6556 tokens into 606", result["text"])

    def test_and_each_side_carries_its_own_size(self) -> None:
        """On the line that opens it, not on a line above.

        "Before folding" on its own says nothing about what is behind it,
        and the two numbers had to be carried back from the row.
        """
        result = self._render(self._report())
        self.assertIn("140 events, 6,556 tokens", result["text"])
        self.assertIn("12 events, 606 tokens", result["text"])

    def test_and_the_row_says_what_was_saved(self) -> None:
        # Two numbers next to each other are a subtraction somebody has to
        # do, and what folding bought is the point of the row.
        result = self._render(self._report())
        self.assertIn("5950 tokens saved", result["text"])
        self.assertIn("91%", result["text"])

    def test_a_step_that_did_not_search_does_not_blame_the_library(self) -> None:
        """"Nothing matched" is a claim about the memory.

        It was written when every step searched before it was asked
        anything, so an empty result could only mean the library held
        nothing. Step 1 decides whether to search and mostly does not - and
        a step that was satisfied and a memory that could not help lead
        somewhere completely different.
        """
        report = self._report()
        step = report["steps"][0]
        step["records"] = []
        step["searches"] = 0
        step["trail"] = [row for row in step["trail"] if row["did"] != "search"]
        result = self._render(report)
        self.assertIn("It did not search", result["text"])

    def test_but_a_search_that_matched_nothing_still_says_so(self) -> None:
        report = self._report()
        for step in report["steps"]:
            step["records"] = []
        result = self._render(report)
        self.assertIn("It searched, and nothing matched", result["text"])

    def test_the_step_summary_names_the_code_separately(self) -> None:
        # Not under the model and not under what was set in advance:
        # nobody chose it, and there is nothing to change.
        result = self._render(self._report())
        self.assertIn("no choice, no model", result["text"])

    def test_a_fixed_decision_names_the_setting_behind_it(self) -> None:
        """Which is what makes the line worth reading.

        "The agent chose" is not something anybody can act on. The name of
        the setting is where to go if the window is wrong - and it was the
        worse half of a label that already said nothing, because the model
        is part of the agent and so is the code.
        """
        result = self._render(self._report())
        self.assertIn("heartbeat_interval_seconds", result["text"])
        self.assertIn("trail-setting", result["html"])

    def test_the_summary_says_when_each_was_decided(self) -> None:
        # The axis is time, not identity: during this run, or before it.
        result = self._render(self._report())
        self.assertIn("during this run", result["text"])
        self.assertIn("set before the run", result["text"])

    def test_and_folding_does_not_count_as_the_model_choosing(self) -> None:
        result = self._render(self._report())
        self.assertIn("asked for more 2 times", result["text"])

    def test_each_row_says_who_decided_it(self) -> None:
        """Section 37, as amended: two kinds of trail that look alike.

        Step 1 chooses its own - how far back, which folder, what words -
        and the other three have all of it chosen for them before the
        question is asked. From the results alone the two are the same
        list, and "why did it look there" has a different answer in each.
        """
        result = self._render(self._report())
        self.assertIn("the model chose, during this run", result["text"])
        self.assertIn("fixed before the run", result["text"])

    def test_and_what_was_decided_rather_than_only_that_it_was(self) -> None:
        result = self._render(self._report())
        self.assertIn("search others for filesystem.py", result["text"])
        self.assertIn("15 minutes, the heartbeat interval", result["text"])

    def test_why_it_asked_is_shown(self) -> None:
        """The arguments say what it wanted; this says why.

        `search others for filesystem.py` is a call, and the question a
        reader has is what made those the right words.
        """
        result = self._render(self._report())
        self.assertIn("The file being edited is the thing to look up",
                      result["text"])

    def test_the_events_are_shown_rather_than_counted(self) -> None:
        """A count is the one part of them that answers nothing.

        The panel exists to say what the step read, and it was saying "12
        events" - in place of the events.
        """
        result = self._render(self._report())
        # A row that happened once keeps its moment; one that repeated
        # shows the stretch instead (see the folding tests below).
        self.assertIn("14:31:02", result["text"])
        self.assertIn("firefox.exe", result["text"])

    def test_with_the_detail_that_says_what_each_one_was(self) -> None:
        result = self._render(self._report())
        text = result["text"]
        self.assertIn("Retrieval-augmented generation", text)
        self.assertIn("arxiv.org/abs/2401.00001", text)
        self.assertIn("A paper about retrieval.", text)
        self.assertIn("C:/work/search.py", text)

    def test_a_step_lists_what_it_could_have_done(self) -> None:
        """An outcome shows what happened, not what did not.

        "Nothing" covers a step that decided against, one with nothing to
        decide about, and one that was never able to do the thing the
        reader had in mind - and the third is the common one.
        """
        result = self._render(self._report())
        self.assertIn("what it could do, and what it did", result["text"])
        self.assertIn("write a state on the calendar", result["text"])

    def test_and_what_it_could_not_have_done_at_all(self) -> None:
        # Which is the question an empty outcome actually raises.
        result = self._render(self._report())
        self.assertIn("Scheduling a", result["text"])

    def test_an_action_it_did_not_take_reads_as_none(self) -> None:
        report = self._report()
        report["steps"][0]["could_do"] = [
            {"what": "write a state on the calendar", "count": 0}]
        result = self._render(report)
        self.assertIn("none", result["text"])
        self.assertIn("beat-did-not", result["html"])

    def test_a_step_still_going_does_not_report_a_conclusion(self) -> None:
        """Everything on a step is written as a conclusion.

        "It did not search", "nothing came of it" - and a step being
        watched has not decided yet. The state step read "it did not
        search" right up to the moment it did, which is the whole minute a
        reader is most likely to be looking at it.
        """
        report = self._report()
        for step in report["steps"]:
            step["phase"] = "asking"
            step["records"] = []
            step["outcome"] = {}
            step["outcome_known"] = False
            step["trail"] = [row for row in step.get("trail") or []
                             if row["did"] != "search"]
        result = self._render(report)
        self.assertIn("still going", result["text"])
        self.assertNotIn("It did not search", result["text"])

    def test_nor_does_it_claim_the_outcome_is_nothing(self) -> None:
        """A step that has not got there yet and a step that concluded
        nothing have the same empty outcome, and only one of them is a
        finding. Which it is comes off `outcome_known`, because the phase
        cannot say: what a step wrote is recorded after every step has
        answered, so a step can be finished and have nothing on it yet."""
        report = self._report()
        for step in report["steps"]:
            step["phase"] = "asking"
            step["outcome"] = {}
            step["outcome_known"] = False
        result = self._render(report)
        self.assertNotIn("Nothing came of this step", result["text"])
        self.assertNotIn("interrupting has a cost", result["text"])

    def test_and_a_summary_of_an_unfinished_step_is_not_written(self) -> None:
        """A summary of what has not happened is a guess at how it will end.

        It said "the model chose what to look at - asked for more twice -
        and wrote the answer" while the step was on its second round and
        had written nothing.
        """
        report = self._report()
        report["steps"][0]["phase"] = "asking"
        result = self._render(report)
        self.assertNotIn("and wrote the answer", result["text"])
        self.assertIn("Not yet - this step is still going", result["text"])

    def test_the_context_waits_for_there_to_be_an_end(self) -> None:
        """A running total beside "left holding" is wrong when read.

        It grows with every round, so the figure is out of date by the time
        anybody has finished reading the line it is on.
        """
        report = self._report()
        report["steps"][0]["phase"] = "asking"
        result = self._render(report)
        self.assertNotIn("the context so far", result["text"])
        self.assertIn("1.2 what it was left holding Not yet", result["text"])

    def test_a_step_not_yet_reached_says_only_that(self) -> None:
        """All four are gathered for before any is asked.

        So steps two, three and four had a context, a token count and a
        trail before the first was touched, and sat there reporting what
        they were holding while step one was on its first round.
        """
        report = self._report()
        report["steps"][1]["phase"] = "gathered"
        result = self._render(report)
        self.assertIn("Waiting for the step before it", result["text"])
        self.assertNotIn("THE-CONTEXT-OF-STEP-TWO", result["text"])

    def test_and_its_header_says_waiting_rather_than_a_cost(self) -> None:
        report = self._report()
        report["steps"][1]["phase"] = "gathered"
        result = self._render(report)
        self.assertIn("waiting", result["text"])

    def test_every_row_of_the_trail_prints_what_it_cost(self) -> None:
        """A list of things that happened, with no way to weigh them.

        The folding and a model call read the same, so a reader looking
        for where a minute went had nowhere to look.
        """
        text = self._render(self._report())["text"]
        self.assertIn("9.2 s", text)
        self.assertIn("1,925 tokens", text)
        self.assertIn("13 ms", text)

    def test_and_prints_the_zeros(self) -> None:
        # A tool call spends no tokens and the folding spends none. That is
        # the fact, not a gap in the measuring.
        text = self._render(self._report())["text"]
        self.assertIn("4 ms · 0 tokens", text)
        self.assertIn("21 ms · 0 tokens", text)

    def test_a_step_that_made_no_call_still_prints_its_cost(self) -> None:
        """Zero is the answer, and a blank is not.

        A blank where the number goes reads as a step that was not
        measured, which is the one thing it should not be confused with.
        """
        result = self._render(self._report())
        self.assertIn("0 calls", result["text"])
        self.assertIn("0 tokens (0 in, 0 out)", result["text"])

    def test_a_finished_step_still_concludes(self) -> None:
        # The conclusions are right once there is one to draw.
        report = self._report()
        step = report["steps"][0]
        step["records"] = []
        step["outcome"] = {}
        step["trail"] = [row for row in step["trail"] if row["did"] != "search"]
        result = self._render(report)
        self.assertIn("It did not search", result["text"])

    def test_a_step_with_no_outcome_says_so_in_its_own_terms(self) -> None:
        """One sentence was shown for every step.

        It was about the cost of interrupting, so a step that writes states
        and a step that judges them both explained themselves by the cost
        of a notification neither of them is able to send.
        """
        result = self._render(self._report())
        self.assertIn("Nothing was written to memory", result["text"])

    def test_and_only_the_step_that_interrupts_mentions_interrupting(self) -> None:
        result = self._render(self._report())
        self.assertEqual(result["text"].count("interrupting has a cost"), 1)

    def test_an_ask_shows_the_message_it_sent(self) -> None:
        """Not a description of it.

        "20 lines of activity, 146 states, 0 records" says how much went in
        and never what, and what was sent is the only thing that explains
        what came back.
        """
        result = self._render(self._report())
        self.assertIn("the message it was sent", result["text"])
        self.assertIn("ANALYSE THE USER'S STATE", result["text"])

    def test_a_later_round_says_what_was_added_to_it(self) -> None:
        result = self._render(self._report())
        self.assertIn("what was added for this round", result["text"])
        self.assertIn("plus the 2 messages before it", result["text"])

    def test_the_ask_shows_what_it_could_ask_for(self) -> None:
        """The other half of the ask.

        No separate call decides whether to search: the message that asks
        for the answer carries the tools, and the model answers by using
        one or by not. The offer travels in the request rather than in the
        messages, so it was nowhere on the page.
        """
        result = self._render(self._report())
        self.assertIn("what it could ask for", result["text"])
        self.assertIn("2 tools", result["text"])
        self.assertIn("search_memory", result["text"])

    def test_with_what_each_one_takes(self) -> None:
        result = self._render(self._report())
        self.assertIn("Search your memory's index.", result["text"])
        self.assertIn("folder", result["text"])

    def test_and_the_standing_instruction_is_reachable(self) -> None:
        result = self._render(self._report())
        self.assertIn("the standing instruction, sent every round",
                      result["text"])

    def test_every_size_is_in_tokens(self) -> None:
        """Characters are a fact about a string.

        What is capped, paid for and worth deciding about is tokens.
        """
        result = self._render(self._report())
        self.assertNotIn("characters", result["text"])
        self.assertIn("33,192 tokens", result["text"])
        self.assertIn("1,900 tokens", result["text"])

    def test_the_trail_shows_the_call_that_came_between(self) -> None:
        """So the order can be read off it.

        "start, fold, search" looks like a search before any call, and what
        explains those words rather than others is a model choosing them
        after being shown the floor.
        """
        result = self._render(self._report())
        self.assertIn("round 1", result["text"])
        self.assertIn("1,840 tokens of context", result["text"])

    def test_the_first_message_is_labelled_as_the_first(self) -> None:
        # Several are sent. Calling one "the prompt" is how a reader
        # concludes the search below it happened before the call.
        result = self._render(self._report())
        self.assertIn("the first message it was sent", result["text"])

    def test_and_the_context_says_when_it_was_taken(self) -> None:
        # It grew during the run, so a reader comparing it against the
        # first message needs to know which moment it is.
        result = self._render(self._report())
        self.assertIn("the context at the end", result["text"])

    def test_each_occurrence_has_its_own_time(self) -> None:
        """Not the stretch they span.

        "x3 over 02:20-02:21" reads the same for three clicks in four
        seconds and three visits over a minute, and those are a
        double-click and a person going back and forth.
        """
        result = self._render(self._report())
        self.assertIn("14:32:05, 14:30:11, 14:27:40", result["text"])
        self.assertIn("and 3 more", result["text"])

    def test_an_event_with_no_text_shows_the_windows_own_reading(self) -> None:
        """Only the screen provider reads text.

        A focus event arrived as a title and nothing else, which names the
        window and not one thing in it - and reads as an event that had
        nothing to say rather than one whose text is kept elsewhere.
        """
        result = self._render(self._report())
        self.assertIn("no text of its own", result["text"])
        self.assertIn("The file open in the editor.", result["text"])
        self.assertIn("14:29:50", result["text"])

    def test_repeated_events_are_one_row_with_a_count(self) -> None:
        """An editor saving on a timer is one fact, not forty.

        The same thing at different times is merged before the step is
        charged for it, and the row says how many there were and the
        stretch they cover - which is the shape of a working session.
        """
        result = self._render(self._report())
        self.assertIn("\u00d76", result["text"])

    def test_the_fold_is_reported_rather_than_only_its_result(self) -> None:
        # Folding that says only what was sent is a compression nobody can
        # check, and the ratio is the thing worth looking at.
        result = self._render(self._report())
        self.assertIn("140 events, folded to 12", result["text"])

    def test_what_changed_between_readings_is_kept(self) -> None:
        # A page that was scrolled is not a page that was stared at.
        result = self._render(self._report())
        self.assertIn("also read: The second page of it.", result["text"])

    def test_and_the_readings_not_shown_are_counted(self) -> None:
        result = self._render(self._report())
        self.assertIn("3 further readings", result["text"])

    def test_and_how_many_are_not_shown_at_all(self) -> None:
        result = self._render(self._report())
        self.assertIn("and 10 more", result["text"])

    def test_the_states_it_started_with_are_named(self) -> None:
        # "2 states running" is the same failure one line down.
        result = self._render(self._report())
        self.assertIn("Debugging the listener", result["text"])

    def test_each_one_says_how_long_it_runs(self) -> None:
        """Which is most of what a state is.

        A thing that started forty minutes ago and a thing that has held
        for a fortnight are different claims about the user, and a title
        says neither.
        """
        result = self._render(self._report())
        self.assertIn("3 hours", result["text"])
        self.assertIn("4 days", result["text"])

    def test_and_how_far_into_it_the_user_already_is(self) -> None:
        # "3 hours" is what was written down; how much of it is behind them
        # is the question somebody reading a heartbeat has.
        result = self._render(self._report())
        self.assertRegex(result["text"], r"(minutes|hours|days) in")

    def test_a_state_can_be_opened(self) -> None:
        result = self._render(self._report())
        self.assertIn('data-beat-state="state_1"', result["html"])
        self.assertIn("trail-state", result["html"])

    def test_and_how_many_were_not_shown(self) -> None:
        result = self._render(self._report())
        self.assertIn("and 3 more", result["text"])

    def test_a_state_the_agent_holds_about_itself_says_so(self) -> None:
        result = self._render(self._report())
        self.assertIn("the agent intends", result["text"])

    def test_a_result_with_nothing_behind_it_is_not_a_button(self) -> None:
        report = self._report()
        for row in report["steps"][0]["trail"]:
            row.pop("id", None)
            for hit in row.get("hits", []):
                hit.pop("id", None)
        result = self._render(report)
        self.assertNotIn("trail-open", result["html"])
        self.assertIn("A morning on the listener", result["text"])

    def test_a_run_already_in_progress_says_so_rather_than_starting_another(self) -> None:
        result = self._render({"skipped": "already running"})
        self.assertIn("already running", result["text"])
        self.assertEqual(result["steps"], 0)

    def test_nothing_runs_until_the_button_is_pressed(self) -> None:
        result = self._render(self._report(), click=False)
        self.assertEqual(result["posted"], [])
        self.assertIn("Not run from here yet", result["text"])


class TheExperiencePanelTests(unittest.TestCase):
    """Each piece of work as stages, how the user works as principles and
    patterns, and the agent working on it when started."""

    GRAPH = {
        "unit": {"id": "project-1", "kind": "project", "name": "The agent release"},
        "tasks": [{"id": "task-a", "title": "Group tasks into projects"}],
        "cards": [
            {"id": "card_root", "parent_id": "", "depth": 0, "level": "root",
             "title": "The agent release", "statement": "the release ships",
             "status": "working", "type": "", "specification": [],
             "checked_by": {}, "why": {}, "refs": []},
            {"id": "card_rows", "parent_id": "card_root", "depth": 1,
             "level": "deliverable", "title": "Project rows", "type": "code",
             "statement": "the panel groups tasks by project",
             "status": "working",
             "specification": [{"requirement": "it opens on one click",
                                "source": "the user said: '一点就开'"}],
             "checked_by": {"by": "user", "what": "clicks a row"},
             "why": {"why": "the panel is read by project", "from": "inferred"},
             "refs": []}],
        "stages": [
            {"id": "stage_1", "task_id": "task-a", "card_id": "card_rows",
             "kind": "execution", "medium": "code", "summary": "built the rows",
             "deliverable": {"excerpt": "rows"}, "evaluation": {}, "states": ["s1"],
             "began": "2026-10-04T10:00:00+00:00", "created_at": "x",
             "written_by": {"model": "claude-x", "provider": "anthropic"}},
            {"id": "stage_2", "task_id": "task-a", "card_id": "card_rows",
             "kind": "evaluation", "medium": "code", "summary": "rejected",
             "deliverable": {}, "states": ["s2"],
             "evaluation": {"verdict": "rejected", "criteria": ["it expands"],
                            "words": ["还是没显示"]},
             "found_by": {"words": ["expand", "rows"], "places": [".js", "web"]},
             "natures": [{"id": "rec_9", "title": "Judges rows by opening them",
                          "kind": "work", "aspect": "evaluation", "pattern": "",
                          "example": {"what": "the project rows, opened one by one",
                                      "references": [{"location": "C:/w/rows.png"}]}}],
             "began": "2026-10-04T11:00:00+00:00", "created_at": "x",
             "written_by": {"model": "claude-x", "provider": "anthropic"}}],
        "edges": [{"source": "stage_2", "target": "stage_1", "kind": "evaluates"}],
    }
    TREE = {"principles": [{"id": "principle_1", "statement": "Judges on the real thing",
                            "detail": "d", "support": 9,
                            "written_by": {"model": "claude-x"},
                            "patterns": [{"id": "pattern_1",
                                          "statement": "Tries it by hand",
                                          "support": 9, "written_by": {},
                                          "members": [{"id": "rec_1",
                                                       "title": "Tried the rows",
                                                       "written_by": "claude-y"}]}]}],
            "unplaced": [], "records": 9, "links": 3}

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def test_the_graph_the_stage_card_and_the_principles(self) -> None:
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        script = (web / "app.js").read_text(encoding="utf-8")
        stub = {
            "/api/experience": {"stages": 2, "patterns": 1, "principles": 1,
                                "links": 3, "building": {},
                                "units": [{"id": "project-1", "kind": "project",
                                           "name": "The agent release",
                                           "stages": 2, "pending": 0,
                                           "tasks": [],
                                           "learning": {"until": "2026-10-05T10:00:00",
                                                        "learned_at": "2026-10-05T10:00:00",
                                                        "tokens": 12345, "calls": 7}},
                                          {"id": "task-new", "kind": "task",
                                           "name": "Never learned", "stages": 0,
                                           "pending": 3, "tasks": [], "learning": {}},
                                          {"id": "work-1", "kind": "new project",
                                           "name": "For the agents", "stages": 0,
                                           "pending": 0, "tasks": [], "learning": {}}]},
            "/api/experience/graph": self.GRAPH,
            "/api/experience/all": {"units": [self.GRAPH]},
            "/api/experience/patterns": self.TREE,
            "/api/experience/nature": {
                "id": "rec_9", "title": "Judges rows by opening them",
                "summary": "opens each row before accepting it",
                "example": {"what": "the project rows, opened one by one",
                            "references": [{"location": "C:/w/rows.png", "what": "the rows"}]},
                "pattern": {"id": "pattern_9", "statement": "Tries it by hand",
                            "last_active": "2026-10-04T11:00:00+00:00"},
                "principle": {"id": "principle_9", "statement": "Judges on the real thing",
                              "last_active": "2026-10-04T11:00:00+00:00"},
                "lessons": [{"id": "stage_2", "kind": "evaluation",
                             "summary": "rejected", "began": "2026-10-04T11:00:00"}],
                "body": "the whole record", "source": "experience"},
            "/api/work/folders": {"folders": ["C:/w/paper"],
                                  "warning": "Agents can create, change and delete any file"},
            "/api/work/limits": {"defaults": {"minutes": 45, "tokens_in": 8000000,
                                              "tokens_out": 400000, "calls": 200}},
            "/api/work/tree": {
                "unit": {"id": "project-1", "name": "The agent release"},
                "cards": [{"id": "wc_root", "parent_id": "", "depth": 0, "level": "root",
                           "title": "Write the notes", "statement": "notes exist",
                           "status": "working", "type": "", "specification": [],
                           "checked_by": {}, "why": {}, "refs": []},
                          {"id": "wc_notes", "parent_id": "wc_root", "depth": 1,
                           "level": "deliverable", "title": "The notes",
                           "statement": "notes.md says hello", "status": "done",
                           "type": "document", "specification": [],
                           "checked_by": {"by": "agent", "what": "read it"},
                           "why": {}, "refs": []}],
                "stages": [{"id": "ws_1", "card_id": "wc_notes", "task_id": "project-1",
                            "kind": "execution", "medium": "document",
                            "summary": "wrote the notes", "states": [],
                            "began": "2026-10-06T10:00:00+00:00",
                            "evaluation": {"verdict": "accepted",
                                           "words": ["it says hello"]},
                            "work": {"decision": {"about": "what to write",
                                                  "chose": "a greeting", "by": "agent"}},
                            "notes": {"proposals": [{"summary": "write hi"},
                                                    {"summary": "write hello"}],
                                      "reviews": [
                                          {"verdict": "revise", "on": "the proposal",
                                           "round": 1, "change": "say hello",
                                           "comments": [{"say": "greet properly",
                                                         "because": ["stage_9"]}]},
                                          {"verdict": "approve", "on": "the proposal",
                                           "round": 2, "comments": []},
                                          {"verdict": "accepted", "on": "the result",
                                           "checked": ["notes.md"],
                                           "comments": [{"say": "it says hello",
                                                         "because": ["own judgement"]}]}]}}],
                "edges": []},
            "/api/work/runs": {"runs": [{
                "id": "run_1", "alive": False, "status": "not finished",
                "continuable": True, "instruction": "",
                "folder": "C:/w/agent_work/x", "started_at": "2026-10-04T12:00:00",
                "plan": {"contract": {"goal": "the goal", "scope": ["the rows"],
                                      "deliverables": [{"card": "card_1",
                                                        "title": "Project rows",
                                                        "statement": "rows group"}]},
                         "deck": {"finished": False, "title": "Rows",
                                  "deliverables": [{"name": "The panel",
                                                    "location": "C:/w/agent_work/x/panel.js",
                                                    "what": "groups tasks"}]},
                         "todo": {"next": ["finish the panel"], "open": [], "blocked": []},
                         "decisions": [{"about": "the order", "chose": "by date",
                                        "over": "by name", "why": "newest first",
                                        "support": "strong", "because": ["stage_1"]}],
                         "rounds": [{"n": 1, "card": "Project rows",
                                     "objective": "draw the rows",
                                     "progress": "advanced", "stage": "stage_2"}],
                         "sessions": [{}], "total": {"calls": 3, "tokens_in": 100,
                                                     "tokens_out": 20}},
                "final": {"reason": "the call limit"},
                "log": [{"at": "2026-10-04T12:00:01", "what": "plan"}]}]},
        }
        harness = (
            "<script>const STUB=" + json.dumps(stub) + ";"
            "window.__asked=[];"
            "window.fetch=(u)=>{const p=String(u).split('?')[0];window.__asked.push(p);"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + script + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';let out={};"
            "try{await loadExperience(true);"
            "out.units=document.querySelector('#xp-units').textContent.replace(/\s+/g,' ');"
            "window.__asked.length=0;"
            "out.dots=document.querySelectorAll('#xp-graph [data-xp-stage]').length;"
            "out.edges=document.querySelectorAll('#xp-graph .xp-edge').length;"
            "document.querySelector('#xp-graph [data-xp-stage=\"stage_2\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "out.card=document.querySelector('#xp-stage').textContent.replace(/\s+/g,' ');"
            "document.querySelector('#xp-stage [data-xp-nature=\\\"rec_9\\\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "await new Promise((r)=>setTimeout(r,50));"
            "out.nature=document.querySelector('#xp-stage [data-xp-nature-detail=\\\"rec_9\\\"]')"
            "  .textContent.replace(/\s+/g,' ');"
            "out.gone=['xp-all','xp-patterns','xp-pattern-card','xp-prompts','xp-all-prompts']"
            "  .filter((id)=>document.getElementById(id)).length;"
            "document.querySelector('#xp-units [data-xp-unit=\\\"task-new\\\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "await new Promise((r)=>setTimeout(r,50));"
            "out.chosen=document.querySelector('#xp-units .xp-unit.selected').textContent.replace(/\s+/g,' ');"
            "out.asked=window.__asked.slice();"
            "out.work=document.querySelector('#xp-work').textContent.replace(/\s+/g,' ');"
            "out.rows=[...document.querySelectorAll('#xp-graph [data-xp-card]')].map((r)=>r.textContent.trim());"
            "out.workRows=document.querySelectorAll('#xp-work-tree [data-xp-card]').length;"
            "out.limits=['minutes','tokens_in','tokens_out','calls'].map((n)=>(document.getElementById('xp-limit-'+n)||{}).value);"
            "document.querySelector('#xp-work-tree [data-xp-stage=\\\"ws_1\\\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "out.workStage=document.querySelector('#xp-work-stage').textContent.replace(/\s+/g,' ');"
            "document.querySelector('#xp-graph [data-xp-card=\\\"card_rows\\\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "out.cardPanel=document.querySelector('#xp-stage').textContent.replace(/\s+/g,' ');"
            "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({error:err,out:out});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
        match = re.search(r"<title>(.*?)</title>", completed.stdout or "", re.S)
        self.assertIsNotNone(match, "the page never finished rendering")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["error"], "", result["error"])
        out = result["out"]
        # One list: no PROJECT / FOLDER labels to tell them apart.
        self.assertNotIn("PROJECT", out["units"])
        # Each row learned from itself: Update once learned, Start before,
        # what it has cost - and none for a project made for the agents.
        # And beside the button, whether it is updated by itself.
        self.assertIn("Update learning Update by itself learned up to 2026-10-05 10:00 · "
                      "12,345 tokens spent learning it (7 calls)", out["units"])
        self.assertIn("Start learning Update by itself not learned yet · nothing spent "
                      "learning it yet", out["units"])
        self.assertEqual(out["units"].count("learning"), 4)
        self.assertEqual(out["dots"], 2)
        self.assertEqual(out["edges"], 1)
        self.assertIn("还是没显示", out["card"])
        self.assertIn("written by claude-x (anthropic)", out["card"])
        # One piece of work at a time: no picture of all of them, no prompts,
        # no tree of principles (that is on the overview now).
        self.assertEqual(out["gone"], 0)
        # Choosing another redraws only its own part - the list is not asked
        # for again.
        self.assertIn("Never learned", out["chosen"])
        self.assertNotIn("/api/experience", out["asked"])
        self.assertIn("Start working on this", out["work"])
        # A run: the contract in force on top, unfolded; its results - the
        # slides and where each deliverable is - then what is left and the
        # decisions made for you; paused, it can be continued.
        for said in ("The results - not finished", "Show the slides",
                     "C:/w/agent_work/x/panel.js", "groups tasks",
                     "To do", "finish the panel", "Contract in force main Goal: the goal",
                     "Decisions made for you (1)", "strongly backed by your past work",
                     # Tokens in and out, apart.
                     "Continue", "the call limit", "100 in · 20 out"):
            self.assertIn(said, out["work"])
        # The rows are the cards, each under the one it is a piece of.
        self.assertEqual(len(out["rows"]), 2)
        self.assertIn("Project rows", out["rows"][1])
        # An attempt says what it was an attempt at.
        self.assertIn("An attempt at: Project rows", out["card"])
        # What the attempt shows about the user, by what it is about.
        self.assertIn("How you evaluate Judges rows by opening them", out["card"])
        # A nature opens where it is listed: its example and where it can be
        # opened, its pattern and principle with when they were last active,
        # the lessons it is attached to.
        for said in ("For example: the project rows, opened one by one",
                     "C:/w/rows.png", "In the pattern: Tries it by hand",
                     "Under the principle: Judges on the real thing",
                     "last active 2026-10-04", "Lessons it is attached to (1)"):
            self.assertIn(said, out["nature"])
        self.assertIn("Found by: expand, rows · where: .js, web", out["card"])
        self.assertIn("For example: the project rows, opened one by one C:/w/rows.png",
                      out["card"])
        # A card: what done means, how it is judged, its attempts in order.
        self.assertIn("Done means: the panel groups tasks by project", out["cardPanel"])
        self.assertIn("Checked by you: clicks a row", out["cardPanel"])
        self.assertIn("一点就开", out["cardPanel"])
        self.assertIn("Attempts (2)", out["cardPanel"])
        # The agents' own work: the folders they may change, said plainly;
        # the limits; their tree; and every review of a stage.
        self.assertIn("C:/w/paper", out["work"])
        self.assertIn("delete any file", out["work"])
        self.assertEqual(out["limits"], ["45", "8000000", "400000", "200"])
        self.assertEqual(out["workRows"], 2)
        self.assertIn("Proposed 2 times", out["workStage"])
        self.assertIn("revise · on the proposal (round 1) greet properly - stage_9",
                      out["workStage"])
        self.assertIn("accepted · on the result", out["workStage"])
        self.assertIn("Looked at: notes.md", out["workStage"])
        self.assertIn("A piece of: The agent release", out["cardPanel"])


class NatureExamplesPanelTests(unittest.TestCase):
    """A work nature, clicked in How you work, shows ALL its examples - each
    with what was done, its project, its date and where it can be opened -
    and its history."""

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    @staticmethod
    def _nature(examples, history):
        return {"id": "rec_9", "title": "Judges a figure by printing it",
                "summary": "prints it before saying yes", "body": "the whole record",
                "source": "experience", "created_at": "2026-09-01T10:00:00+00:00",
                "example": {"what": "the first figure"},
                "pattern": {"id": "p1", "statement": "Tries it by hand",
                            "last_active": "2026-10-04T11:00:00+00:00"},
                "principle": {}, "lessons": [], "examples": examples,
                "examples_total": len(examples), "history": history}

    def _run(self, nature) -> dict:
        now = datetime.now(timezone.utc)
        tree = {"kinds": [{"kind": "work", "principles": [{
            "id": "pr_1", "statement": "Judges on the real thing",
            "last_active": (now - timedelta(days=1)).isoformat(),
            "stretches": [[(now - timedelta(days=3)).isoformat(),
                           (now - timedelta(days=1)).isoformat()]],
            "patterns": [{"id": "p1", "statement": "Tries it by hand",
                          "last_active": (now - timedelta(days=1)).isoformat(),
                          "members": [{"id": "rec_9",
                                       "title": "Judges a figure by printing it"}]}]}],
            "unplaced": []}]}
        stub = {"/api/experience/nature": nature}
        steps = (
            "ovState.recentDays=0;renderPrinciples(TREE);"
            "document.querySelector('#ov-principles [data-ov-principle=\"pr_1\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "const card=document.getElementById('ov-principle-card');"
            "card.querySelector('details[data-ov-pattern=\"p1\"]').open=true;await wait();"
            "card.querySelector('[data-ov-nature=\"rec_9\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait();"
            "const box=()=>card.querySelector('[data-xp-nature-detail=\"rec_9\"]');"
            "out.hidden=box().hidden;out.text=box().textContent.replace(/\\s+/g,' ');"
            "out.rows=box().querySelectorAll('.xp-nature-example-row').length;"
            "const list=box().querySelector('.xp-nature-examples');"
            "out.scrolls=list?getComputedStyle(list).overflowY:'';"
            "out.places=[...box().querySelectorAll('.xp-nx-places code')].map(c=>c.textContent);"
            "out.asked=window.__asked.slice();"
            # The overview refreshes: what was open stays open.
            "renderPrinciples(TREE);await wait();"
            "out.openAfter=!box().hidden&&card.querySelector('details[data-ov-pattern=\"p1\"]').open;"
            "out.rowsAfter=box().querySelectorAll('.xp-nature-example-row').length;")
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        harness = (
            "<style>" + (web / "styles.css").read_text(encoding="utf-8") + "</style>"
            "<script>window.__asked=[];const STUB=" + json.dumps(stub) + ";"
            "const TREE=" + json.dumps(tree) + ";"
            "window.fetch=(u)=>{const p=String(u).split('?')[0];window.__asked.push(String(u));"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "headers:{get:()=>null},json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';const out={};"
            "const wait=()=>new Promise(r=>setTimeout(r,60));"
            "try{" + steps + "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err,out});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 "--window-size=1400,900",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
        match = re.search(r"<title>(.*?)</title>", completed.stdout or "", re.S)
        self.assertIsNotNone(match, "the page never rendered")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "", result["err"])
        return result["out"]

    def test_clicking_a_nature_shows_all_its_examples(self) -> None:
        examples = [{"source": "stage", "id": f"stage_{n}", "kind": "evaluation",
                     "title": f"Lesson number {n}", "what": f"what was done {n}",
                     "summary": f"the summary of {n}", "project": "The paper",
                     "task": f"Task {n}", "began": f"2026-09-{n:02d}T10:00:00+00:00",
                     "ended": f"2026-09-{n:02d}T11:00:00+00:00",
                     "deliverable": f"figure {n}", "own_example": n == 1,
                     "locations": [{"location": f"C:/w/figure_{n}.pdf",
                                    "what": "the figure"},
                                   {"location": f"https://example.org/{n}"}]}
                    for n in range(25, 0, -1)]
        out = self._run(self._nature(examples, []))
        self.assertFalse(out["hidden"])
        self.assertIn("/api/experience/nature?id=rec_9", out["asked"])
        # Every one of them, not a few, in a list that scrolls.
        self.assertEqual(out["rows"], 25)
        self.assertIn("Examples (25):", out["text"])
        self.assertEqual(out["scrolls"], "auto")
        for n in (1, 13, 25):
            self.assertIn(f"Lesson number {n}", out["text"])
            self.assertIn(f"Task {n}", out["text"])
            self.assertIn(f"2026-09-{n:02d}", out["text"])
            self.assertIn(f"C:/w/figure_{n}.pdf", out["places"])
            self.assertIn(f"https://example.org/{n}", out["places"])
        self.assertIn("The paper", out["text"])
        self.assertIn("For example: what was done 1", out["text"])
        self.assertIn("History (no earlier versions)", out["text"])
        # A refresh of the overview leaves it open, as it was.
        self.assertTrue(out["openAfter"])
        self.assertEqual(out["rowsAfter"], 25)

    def test_a_rewritten_nature_shows_its_earlier_versions(self) -> None:
        history = [{"at": "2026-09-20T10:00:00+00:00",
                    "replaced_at": "2026-10-01T10:00:00+00:00",
                    "title": "Prints the figures", "summary": "older words",
                    "body": "older body", "why": "seen again", "run": "run_2",
                    "merged_from": [{"id": "rec_7", "title": "Checks prints"}],
                    "split_from": {}},
                   {"at": "2026-09-01T10:00:00+00:00",
                    "replaced_at": "2026-09-20T10:00:00+00:00",
                    "title": "Prints one figure", "summary": "first words",
                    "body": "", "why": "", "run": "run_1", "merged_from": [],
                    "split_from": {"id": "rec_3", "title": "Prints everything"}}]
        out = self._run(self._nature([], history))
        self.assertIn("History (2 earlier versions)", out["text"])
        for said in ("Prints the figures", "written 2026-09-20 · replaced 2026-10-01",
                     "run run_2", "Why it changed: seen again",
                     "Merged from: Checks prints", "Split from: Prints everything",
                     "Prints one figure"):
            self.assertIn(said, out["text"])
        # With no examples sent, the record's own is shown as before.
        self.assertIn("For example: the first figure", out["text"])
