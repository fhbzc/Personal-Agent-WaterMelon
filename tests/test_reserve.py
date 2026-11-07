"""THE RESERVE: what may be recommended, made ready ahead (heartbeat/monitor/reserve.py). The pages
and the work here are made up."""

from __future__ import annotations

import html
import json
import re
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.heartbeat.monitor import match, reserve as rs
from personalized_agent.heartbeat.monitor.reserve import Reserve, chain_line

PAGE = "https://tides.example/harbour-tables"


class _Model:
    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []

    def invoke(self, request, role="primary"):
        self.asked.append(request)
        answer = self.answers.pop(0) if self.answers else {}
        return SimpleNamespace(text=answer if isinstance(answer, str) else json.dumps(answer),
                               tool_calls=[], usage=None)


class TheReserveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.reserve = self.app.reserve
        self.app.config.set("monitor_recommend_search", False)

    def _read(self, url: str = "", title: str = "") -> None:
        from personalized_agent.heartbeat.reading.log import title_key, url_key
        self.app.db.execute(
            "INSERT INTO reading_pieces (id, first_at, last_at, url, url_key, title, title_key, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"r{url}{title}", "2026-11-05T08:00:00+00:00", "2026-11-05T08:00:00+00:00", url,
             url_key(url), title, title_key(title), "2026-11-05T08:00:00+00:00",
             "2026-11-05T08:00:00+00:00"))

    def _model(self, *answers) -> _Model:
        model = _Model(*answers)
        self.app.monitor_decider.models = model
        return model

    # -- what comes in --
    def test_what_an_issue_left_over_comes_in_and_what_it_printed_does_not(self) -> None:
        printed = {"id": "s1", "title": "Spring tides", "sources": ["https://tides.example/spring"]}
        left = {"id": "s2", "title": "Harbour tables", "detail": "New tables", "sources": [PAGE]}
        self.assertEqual(self.reserve.from_the_issue([printed, left], [printed], set()), 1)
        self.assertEqual([one["title"] for one in self.reserve.items(rs.RAW)], ["Harbour tables"])

    def test_a_story_printed_later_is_not_told(self) -> None:
        left = {"id": "s2", "title": "Harbour tables", "sources": [PAGE]}
        self.reserve.from_the_issue([left], [], set())
        self.reserve.from_the_issue([], [dict(left, id="s9")], set())
        self.assertEqual(self.reserve.items(rs.RAW), [])
        self.assertEqual(self.reserve.items(rs.SEEN)[0]["seen_how"], "newspaper")

    def test_each_thing_comes_in_once_whatever_became_of_it(self) -> None:
        ident = self.reserve.add("Harbour tables", PAGE)
        self.assertTrue(ident)
        self.assertEqual(self.reserve.add("Harbour tables", PAGE), "")
        self.reserve.told(ident)
        self.assertEqual(self.reserve.add("Harbour tables again", PAGE), "")

    def test_what_was_read_does_not_come_in(self) -> None:
        self._read(PAGE)
        self.assertEqual(self.reserve.add("Harbour tables", PAGE), "")

    # -- already seen --
    def test_read_since_it_is_marked_at_the_heartbeat_by_address_or_by_title(self) -> None:
        by_address = self.reserve.add("Harbour tables", PAGE)
        by_title = self.reserve.add("A guide to neap tides", "https://other.example/neap")
        self._read(PAGE)
        self._read("https://mirror.example/copy", "A guide to neap tides")
        self.assertEqual(self.reserve.mark_seen(), 2)
        self.assertEqual(self.reserve.get(by_address)["seen_how"], "address")
        self.assertEqual(self.reserve.get(by_title)["seen_how"], "title")

    def test_looked_at_once_more_just_before_it_is_sent(self) -> None:
        ident = self.reserve.add("Harbour tables", PAGE, status=rs.READY)
        self.assertTrue(self.reserve.still_fresh(ident))
        self._read(PAGE)
        self.assertFalse(self.reserve.still_fresh(ident))
        self.assertEqual(self.reserve.get(ident)["status"], rs.SEEN)

    # -- made ready --
    def test_the_model_makes_it_ready_with_its_angle_and_its_words(self) -> None:
        kept = self.reserve.add("Harbour tables", PAGE, detail="New tables")
        dropped = self.reserve.add("Ten tips for sailors", "https://tips.example/ten")
        model = self._model({"items": [{
            "from": kept, "title": "Harbour tables", "url": PAGE, "detail": "Tables for 2027",
            "angle": "Their tide study needs these", "why": "They have been fitting tide data",
            "proper": ["Thistle Harbour"], "zh": ["潮汐表"], "en": ["tide table"]}]})
        self.assertEqual(self.reserve.prepare(self.app.monitor_decider), 1)
        ready = self.reserve.get(kept)
        self.assertEqual(ready["status"], rs.READY)
        self.assertEqual(ready["keywords"], {"proper": ["Thistle Harbour"], "zh": ["潮汐表"],
                                             "en": ["tide table"]})
        self.assertEqual(ready["angle"], "Their tide study needs these")
        self.assertEqual(self.reserve.get(dropped)["status"], rs.DROPPED)
        asked = model.asked[0]
        self.assertEqual(asked.task, rs.PREPARE_TASK)
        self.assertIn("THE LESSONS", asked.messages[0]["content"])

    def test_no_word_to_be_found_by_and_it_is_not_ready(self) -> None:
        ident = self.reserve.add("Harbour tables", PAGE)
        self._model({"items": [{"from": ident, "title": "Harbour tables", "url": PAGE}]})
        self.assertEqual(self.reserve.prepare(self.app.monitor_decider), 0)
        self.assertEqual(self.reserve.get(ident)["status"], rs.DROPPED)

    def test_an_invented_address_is_left_out(self) -> None:
        self._model({"items": [{"title": "Made up", "url": "https://nowhere.example/invented",
                                "en": ["tide table"]}]})
        self.assertEqual(self.reserve.prepare(self.app.monitor_decider), 0)
        self.assertEqual(self.reserve.items(rs.READY), [])

    def _looked_at(self, now, work=(), **answer) -> None:
        self._model({"items": [], **answer})
        self.app.monitor_decider.tasks = lambda: list(work)
        self.reserve.step(self.app.monitor_decider, now)

    def test_the_model_says_when_to_look_again(self) -> None:
        now = datetime(2026, 11, 5, 9, tzinfo=timezone.utc)
        self._looked_at(now, next_look_hours=10)
        self.assertFalse(self.reserve.due(now + timedelta(hours=9)))
        self.assertTrue(self.reserve.due(now + timedelta(hours=10)))

    def test_it_cannot_put_it_off_for_ever_or_come_back_at_once(self) -> None:
        now = datetime(2026, 11, 5, 9, tzinfo=timezone.utc)
        self._looked_at(now, next_look_hours=500)
        self.assertTrue(self.reserve.due(now + timedelta(hours=rs.LATEST_HOURS)))
        now += timedelta(days=2)
        self._looked_at(now, next_look_hours=0)
        self.assertFalse(self.reserve.due(now + timedelta(minutes=45)))
        self.assertTrue(self.reserve.due(now + timedelta(hours=rs.SOONEST_HOURS)))

    def test_new_work_wakes_it_even_with_a_full_reserve(self) -> None:
        now = datetime(2026, 11, 5, 9, tzinfo=timezone.utc)
        for one in range(rs.MOST_READY):
            self.reserve.add(f"Tide note {one}", f"https://tides.example/{one}", status=rs.READY)
        self._looked_at(now, ["Harbour survey - drafting"], next_look_hours=24)
        later = now + timedelta(hours=2)
        self.assertFalse(self.reserve.due(later, ["Harbour survey - now in review"]),
                         "the same work, where it stands changed")
        self.assertTrue(self.reserve.due(later, ["Harbour survey", "Lighthouse repairs - begun"]))
        self.assertFalse(self.reserve.due(now + timedelta(minutes=30), ["Lighthouse repairs"]),
                         "never twice within the hour")
        self.assertEqual(self.reserve.signals(["Lighthouse repairs - begun"], later),
                         ["New work: Lighthouse repairs - begun"])

    def _for_the_survey(self, count: int) -> list:
        return [self.reserve.add(f"Survey note {one}", f"https://survey.example/{one}", status=rs.READY,
                                 for_work="harbour survey") for one in range(count)]

    def test_work_running_short_wakes_it_once(self) -> None:
        now = datetime(2026, 11, 5, 9, tzinfo=timezone.utc)
        work = ["Harbour survey - drafting"]
        made = self._for_the_survey(rs.FEW_FOR_A_WORK)
        self._looked_at(now, work, next_look_hours=24)
        later = now + timedelta(hours=2)
        self.assertEqual(self.reserve.signals(work, later), [])
        self.reserve.told(made[0])
        self.assertEqual(self.reserve.signals(work, later),
                         ["Running short of things for: Harbour survey - drafting"])
        self._looked_at(later, work, next_look_hours=24)
        self.assertEqual(self.reserve.signals(work, later + timedelta(hours=2)), [],
                         "it looked and found no more: not asked again every hour")

    def test_an_answer_to_a_recommendation_wakes_it(self) -> None:
        now = datetime.now(timezone.utc) - timedelta(hours=2)
        self._looked_at(now, next_look_hours=24)
        self.assertEqual(self.reserve.signals((), now), [])
        self.app.lessons.record("recommendations", "bad:n1", "A recommendation called a bad one",
                                action="bad_notification")
        self.assertIn("Answers to recommendations since the last look: 1.",
                      self.reserve.signals((), now))
        self.assertTrue(self.reserve.due(now + timedelta(hours=2)))

    def test_the_model_is_told_why_and_what_it_made_is_kept_by_its_work(self) -> None:
        now = datetime(2026, 11, 5, 9, tzinfo=timezone.utc)
        self._looked_at(now, ["Harbour survey - drafting"], next_look_hours=24)
        later = now + timedelta(hours=2)
        model = self._model({"items": [
            {"title": "Harbour tables", "url": "", "angle": "a", "why": "w", "en": ["tide table"],
             "for": "Harbour survey - drafting"},
            {"title": "Ten tips", "url": "", "angle": "a", "why": "w", "en": ["sailing tips"],
             "for": "Something it made up"}]})
        self.app.monitor_decider.tasks = lambda: ["Harbour survey - drafting", "Lighthouse repairs"]
        self.reserve.step(self.app.monitor_decider, later)
        self.assertIn("New work: Lighthouse repairs", model.asked[0].messages[0]["content"])
        self.assertEqual({one["title"]: one["for_work"] for one in self.reserve.items(rs.READY)},
                         {"Harbour tables": "harbour survey", "Ten tips": ""})

    def test_a_reserve_from_before_gets_the_new_column(self) -> None:
        self.app.db.execute("ALTER TABLE reserve_items DROP COLUMN for_work")
        Reserve(self.app.db, self.app.config)
        self.assertEqual(self.reserve.add("Harbour tables", PAGE, status=rs.READY, for_work="x") != "",
                         True)

    def test_a_link_is_the_same_link_without_what_only_says_how_they_came(self) -> None:
        self.assertEqual(rs.link_key("https://www.tides.example/a/?utm_source=x&fbclid=1#top"),
                         "tides.example/a")
        self.assertEqual(rs.link_key("HTTP://tides.example/a?id=7&utm_medium=y"), "tides.example/a?id=7")
        self.assertNotEqual(rs.link_key("https://tides.example/a?id=7"),
                            rs.link_key("https://tides.example/a?id=8"), "another page")

    def test_it_comes_in_once_whatever_the_link_carries(self) -> None:
        self.assertTrue(self.reserve.add("Harbour tables", PAGE))
        self.assertEqual(self.reserve.add("Tables of the harbour", PAGE + "/?utm_source=mail#top"), "")

    def test_a_link_told_at_a_moment_is_never_told_again(self) -> None:
        self.assertFalse(self.reserve.told_before(PAGE))
        self.reserve.told_link("Harbour tables", PAGE, "n1")
        self.assertTrue(self.reserve.told_before(PAGE + "?utm_campaign=z"))
        self.assertEqual(self.reserve.add("Another title", PAGE), "")

    def test_told_at_a_moment_what_was_kept_ready_is_used_up(self) -> None:
        ident = self.reserve.add("Harbour tables", PAGE, status=rs.READY)
        self.assertFalse(self.reserve.told_before(PAGE), "ready, and never told")
        self.reserve.told_link("Harbour tables", PAGE, "n1")
        self.assertEqual(self.reserve.get(ident)["status"], rs.TOLD)
        self.assertFalse(self.reserve.still_fresh(ident))

    def test_a_link_with_a_title_already_here_is_still_kept(self) -> None:
        self.reserve.add("Harbour tables", PAGE)
        other = "https://other.example/tables"
        self.reserve.told_link("Harbour tables", other, "n1")
        self.assertTrue(self.reserve.told_before(other))

    def test_what_an_issue_printed_is_not_told_later(self) -> None:
        printed = {"id": "s1", "title": "Harbour tables", "url": PAGE}
        self.reserve.from_the_issue([printed], [printed], set())
        self.assertTrue(self.reserve.told_before(PAGE))
        self.assertEqual(self.reserve.add("Harbour tables again", PAGE), "")

    def test_what_was_read_is_told_before(self) -> None:
        self._read(PAGE, "Harbour tables")
        self.assertTrue(self.reserve.told_before(PAGE))

    def test_at_the_moment_the_same_link_is_not_sent_and_a_new_one_is_kept(self) -> None:
        decider = self.app.monitor_decider
        self.assertIs(decider.reserve, self.reserve)
        said = []
        monitor = SimpleNamespace(
            say=lambda *a, **k: said.append(k) or {"said": True, "notification_id": "n1"},
            recommended_today=lambda: 0)
        activator = {"id": "a", "name": "A moment", "group": "recommend"}
        answer = {"send": True, "title": "Harbour tables", "message": "New", "url": PAGE}
        self._model(answer, answer)
        with mock.patch.object(decider, "_sent_with", return_value=(PAGE, "")), \
                mock.patch.object(decider, "_recent_said", return_value=[]):
            first = decider.recommend(monitor, activator, {}, {}, "f1")
            second = decider.recommend(monitor, activator, {}, {}, "f2")
        self.assertTrue(first["said"])
        self.assertEqual(second, {"sent": False, "why": "that very link was told or read before"})
        self.assertEqual(len(said), 1)

    def test_what_was_given_longer_before_comes_down_to_the_most(self) -> None:
        ident = self.reserve.add("Harbour tables", PAGE, status=rs.READY)
        self.app.db.execute("UPDATE reserve_items SET expires_at = ? WHERE id = ?",
                            ("2099-01-01T00:00:00+00:00", ident))
        Reserve(self.app.db, self.app.config)
        latest = datetime.now(timezone.utc) + timedelta(days=rs.MOST_VALID_DAYS, minutes=1)
        self.assertLess(self.reserve.get(ident)["expires_at"], latest.isoformat())

    def test_the_debugger_shows_what_is_kept_its_words_and_what_matched(self) -> None:
        from personalized_agent.debugger.recommendation import report
        self._looked_at(datetime.now(timezone.utc) - timedelta(hours=2), next_look_hours=3)
        words = {"proper": ["Thistle Harbour"], "zh": ["潮汐表"], "en": ["tide table"]}
        ready = self.reserve.add("Harbour tables", PAGE, status=rs.READY, keywords=words,
                                 angle="An angle", why="A why", for_work="harbour survey")
        past = self.reserve.add("Old tables", "https://tides.example/old", status=rs.READY)
        self.app.db.execute("UPDATE reserve_items SET expires_at = ? WHERE id = ?",
                            ("2020-01-01T00:00:00+00:00", past))
        self.reserve.add("Not made ready", "https://tides.example/raw")
        self.app.db.execute(
            "INSERT INTO reserve_hits (id, at, reserve_id, title, score, hit, seen_by, tier, done) "
            "VALUES ('h1', '2026-11-06T09:00:00+00:00', ?, 'Harbour tables', 5, ?, 'ocr', 'middle', "
            "'sent')", (ready, json.dumps(["tide table"])))
        before = {one["id"]: dict(one) for one in self.reserve.newest()}
        said = report(self.app)
        by_id = {one["id"]: one for one in said["items"]}
        self.assertEqual(by_id[ready]["keywords"], words)
        self.assertEqual((by_id[ready]["state"], by_id[ready]["matches"]), ("live", 1))
        self.assertEqual(by_id[past]["state"], "past")
        self.assertEqual(said["states"]["raw"], 1)
        self.assertEqual(said["hits"][0]["hit"], ["tide table"])
        self.assertEqual(said["hits"][0]["done"], "sent")
        self.assertEqual(said["scores"]["high"], match.HIGH)
        self.assertTrue(said["looks"]["last"] and said["looks"]["next"])
        self.assertEqual({one["id"]: dict(one) for one in self.reserve.newest()}, before,
                         "reading it changes nothing")

    def test_what_said_to_look_is_kept_for_the_debugger(self) -> None:
        self._model({"items": []})
        self.reserve.prepare(self.app.monitor_decider, reasons=["New work: Harbour survey"])
        self.assertEqual(self.reserve.looks()["reasons"], ["New work: Harbour survey"])

    def test_the_debugger_page_draws_the_words_of_a_made_up_candidate(self) -> None:
        from test_panels import _javascript_engine
        kind, engine = _javascript_engine()
        if kind != "chrome":
            self.skipTest("no browser available to render the page")
        web = Path(rs.__file__).resolve().parents[2] / "api" / "web"
        section = re.search(r'<section id="recommenddebug".*?</section>\s*</div>\s*</section>',
                            (web / "index.html").read_text(encoding="utf-8"), re.S).group(0)
        script = (web / "recommendation-debugger.js").read_text(encoding="utf-8")
        said = {"switches": {"module": True, "monitor": True, "search": True},
                "looks": {"last": "", "next": "", "reasons": ["New work: <b>x</b>"], "due": True,
                          "signals": []},
                "scores": {"proper": 3, "concept": 2, "middle": 4, "high": 8},
                "states": {"live": 1, "told": 1},
                "items": [{"id": "r1", "state": "live", "title": "Harbour tables", "url": PAGE,
                           "source": "web", "angle": "An angle", "why": "A why", "for_work": "",
                           "expires_at": "", "matches": 2,
                           "keywords": {"proper": ["Thistle"], "zh": ["潮汐表"], "en": ["tide table"]}},
                          {"id": "r2", "state": "told", "title": "Told one", "url": "javascript:x",
                           "source": "web", "angle": "", "why": "", "for_work": "", "expires_at": "",
                           "matches": 0, "keywords": {}}],
                "hits": [{"at": "", "title": "Harbour tables", "hit": ["tide table"], "score": 5,
                          "tier": "middle", "seen_by": "ocr", "done": "sent"}]}
        page = ("<!doctype html><html><head><meta charset='utf-8'><title>t</title></head><body>"
                + section + "<script>const api={get:async()=>(" + json.dumps(said) + ")};</script>"
                "<script>" + script + "</script><script>window.addEventListener('load',async()=>{"
                "let err='';const out={};try{await window.recommendationDebugger.load();"
                "const q=(s)=>[...document.querySelectorAll(s)];"
                "out.words=q('#rec-items .rec-word').map(w=>w.textContent);"
                "out.rows=q('#rec-items tbody tr').length;"
                "out.options=q('#rec-state option').map(o=>o.textContent);"
                "out.reason=document.querySelector('#rec-now li').innerHTML;"
                "out.hit=document.querySelector('#rec-hits').textContent.includes('sent');"
                "const pick=document.querySelector('#rec-state');pick.value='told';"
                "pick.dispatchEvent(new Event('change'));"
                "out.told=q('#rec-items tbody tr').map(r=>r.dataset.item);"
                "out.link=q('#rec-items a').length;"
                "}catch(e){err=String(e&&e.stack?e.stack:e);}"
                "document.title=JSON.stringify({err,out});});</script></body></html>")
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "page.html"
            path.write_text(page, encoding="utf-8")
            done = subprocess.run(
                [engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=8000", "--dump-dom",
                 path.as_uri()], capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120)
        title = re.search(r"<title>(.*?)</title>", done.stdout, re.S).group(1)
        result = json.loads(html.unescape(title))
        self.assertEqual(result["err"], "")
        out = result["out"]
        self.assertEqual(out["words"], ["Thistle", "潮汐表", "tide table"])
        self.assertEqual(out["rows"], 1, "ready ones first")
        self.assertIn("Ready (1)", out["options"])
        self.assertEqual(out["reason"], "New work: &lt;b&gt;x&lt;/b&gt;", "what a model wrote is words")
        self.assertTrue(out["hit"])
        self.assertEqual(out["told"], ["r2"])
        self.assertEqual(out["link"], 0, "an address that is not a page is not a link")

    def test_the_debugger_opens_from_its_tab_on_the_real_page(self) -> None:
        """As it is used: the page and its scripts, the tab pressed. Drawn on a page of its own with
        an `api` made for it, it passed while the real page drew nothing."""
        from test_panels import _javascript_engine
        kind, engine = _javascript_engine()
        if kind != "chrome":
            self.skipTest("no browser available to render the page")
        web = Path(rs.__file__).resolve().parents[2] / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        stub = {"/api/debugger/recommendation": {
            "switches": {"module": True, "monitor": True, "search": True},
            "looks": {"last": "", "next": "", "reasons": [], "due": False, "signals": []},
            "scores": {"proper": 3, "concept": 2, "middle": 4, "high": 8}, "states": {"live": 1},
            "items": [{"id": "r1", "state": "live", "title": "Harbour tables", "url": PAGE,
                       "source": "web", "angle": "An angle", "why": "A why", "for_work": "",
                       "expires_at": "", "matches": 0, "keywords": {"en": ["tide table"]}}],
            "hits": []},
            "/api/status": {"pending_raw_data": {"events": 0, "edits": 0}, "debugger_built_in": True,
                            "library": {"records": 0, "folders": {}}}}
        harness = (
            "<script>const STUB=" + json.dumps(stub) + ";window.fetch=(u)=>{"
            "const p=String(u).split('?')[0];return Promise.resolve({ok:true,status:200,"
            "statusText:'OK',headers:{get:()=>null},json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>" + (web / "recommendation-debugger.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';const out={};"
            "const wait=()=>new Promise(r=>setTimeout(r,200));try{await wait();"
            "document.querySelector('[data-tab=\"recommenddebug\"]').click();await wait();await wait();"
            "out.now=document.querySelector('#rec-now').textContent;"
            "out.words=[...document.querySelectorAll('#rec-items .rec-word')].map(w=>w.textContent);"
            "out.shown=document.querySelector('#recommenddebug').offsetHeight;"
            "}catch(e){err=e.message;}document.title=JSON.stringify({err,out});});</script>")
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "page.html"
            path.write_text(page + harness, encoding="utf-8")
            done = subprocess.run(
                [engine, "--headless=new", "--disable-gpu", "--no-first-run", "--window-size=1400,900",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000", "--dump-dom",
                 path.as_uri()], capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=300)
        result = json.loads(html.unescape(re.search(r"<title>(.*?)</title>", done.stdout, re.S).group(1)))
        self.assertEqual(result["err"], "")
        out = result["out"]
        self.assertEqual(out["words"], ["tide table"])
        self.assertIn("Recommendations", out["now"])
        self.assertGreater(out["shown"], 100, "drawn, and to be seen")

    def test_not_every_heartbeat(self) -> None:
        now = datetime(2026, 11, 5, 9, tzinfo=timezone.utc)
        self._model({"items": []}, {"items": []})
        self.assertTrue(self.reserve.due(now), "never run, and few ready")
        self.reserve.step(self.app.monitor_decider, now)
        self.assertFalse(self.reserve.due(now + timedelta(minutes=15)))
        self.assertTrue(self.reserve.due(now + timedelta(hours=6)))
        self.reserve.add("Harbour tables", PAGE)
        self.assertFalse(self.reserve.due(now + timedelta(minutes=30)))
        self.assertTrue(self.reserve.due(now + timedelta(hours=1)), "something waits: sooner")

    def test_a_model_that_is_away_is_not_asked_but_what_was_read_is_still_marked(self) -> None:
        ident = self.reserve.add("Harbour tables", PAGE)
        self._read(PAGE)
        model = self._model()
        self.assertEqual(self.reserve.step(self.app.monitor_decider, asks=False), {"seen": 1})
        self.assertEqual(model.asked, [])
        self.assertEqual(self.reserve.get(ident)["status"], rs.SEEN)

    def test_only_a_deep_reserve_stops_it_looking(self) -> None:
        self.assertEqual(rs.MOST_READY, 100)
        now = datetime.now(timezone.utc)
        self.app.db.execute("INSERT INTO kv (key, value) VALUES (?, ?)",
                            (rs.LAST_RUN, (now - timedelta(hours=7)).isoformat()))
        for one in range(rs.MOST_READY - 1):
            self.reserve.add(f"Tide note {one}", f"https://tides.example/{one}", status=rs.READY)
        self.assertTrue(self.reserve.due(now), "99 ready: most of them will never be set off")
        self.reserve.add("Tide note last", "https://tides.example/last", status=rs.READY)
        self.assertFalse(self.reserve.due(now))

    def test_past_its_time_it_sets_nothing_off_and_the_model_is_asked(self) -> None:
        now = datetime.now(timezone.utc)
        kept = self.reserve.add("Harbour tables", PAGE, status=rs.READY, valid_days=3)
        gone = self.reserve.add("A regatta next week", "https://tides.example/regatta",
                                status=rs.READY, valid_days=3)
        self.assertEqual(len(self.reserve.live(now)), 2)
        later = now + timedelta(days=4)
        self.assertEqual(self.reserve.live(later), [])
        self.assertEqual(len(self.reserve.past_its_time(later)), 2)
        self.assertTrue(self.reserve.due(later))
        model = self._model({"items": [], "extend": [{"id": kept, "valid_days": 60}]})
        self.reserve.prepare(self.app.monitor_decider, later)
        self.assertIn("PAST ITS TIME", model.asked[0].messages[0]["content"])
        self.assertEqual([one["id"] for one in self.reserve.live(later)], [kept])
        self.assertEqual(self.reserve.get(gone)["status"], rs.DROPPED)

    def test_past_its_time_it_is_not_sent(self) -> None:
        ident = self.reserve.add("Harbour tables", PAGE, status=rs.READY, valid_days=1)
        self.app.db.execute("UPDATE reserve_items SET expires_at = ? WHERE id = ?",
                            ("2020-01-01T00:00:00+00:00", ident))
        self.assertFalse(self.reserve.still_fresh(ident))

    def test_how_long_is_the_models_within_reason(self) -> None:
        now = datetime(2026, 11, 5, tzinfo=timezone.utc)
        self.assertEqual(rs._until(now, 7), (now + timedelta(days=7)).isoformat())
        self.assertEqual(rs._until(now, "soon"), (now + timedelta(days=rs.VALID_DAYS)).isoformat())
        self.assertEqual(rs._until(now, 9999), (now + timedelta(days=30)).isoformat())

    def test_no_answer_to_go_by_and_nothing_is_dropped(self) -> None:
        ident = self.reserve.add("Harbour tables", PAGE)
        self._model("not json at all")
        self.assertEqual(self.reserve.prepare(self.app.monitor_decider), 0)
        self.assertEqual(self.reserve.get(ident)["status"], rs.RAW)

    def test_what_was_kept_before_it_had_a_time_gets_one(self) -> None:
        ident = self.reserve.add("Harbour tables", PAGE, status=rs.READY)
        self.app.db.execute("UPDATE reserve_items SET expires_at = '' WHERE id = ?", (ident,))
        Reserve(self.app.db, self.app.config)
        self.assertTrue(self.reserve.get(ident)["expires_at"])

    # -- the module --
    def test_switched_off_nothing_runs_and_nothing_is_recommended(self) -> None:
        self.app.config.set("module_recommendation_enabled", False)
        model = self._model()
        self.assertEqual(self.reserve.step(self.app.monitor_decider), {})
        self.assertEqual(model.asked, [])
        self.assertEqual(self.reserve.from_the_issue([{"id": "s", "title": "Tide"}], [], set()), 0)
        self.assertFalse(self.app.monitor.allowed({"group": "recommend", "enabled": True}))
        self.assertTrue(self.app.monitor.allowed({"group": "rest", "enabled": True}), "the rest goes on")

    def test_its_calls_are_counted_as_its_own(self) -> None:
        from personalized_agent.common import modules
        self.assertEqual(modules.module_of(rs.PREPARE_TASK), "recommendation")
        self.assertEqual(modules.module_of("monitor_recommend"), "recommendation")

    # -- no limit, and the chain --
    def test_the_days_number_goes_up_with_each(self) -> None:
        monitor = self.app.monitor
        context = {"activator": {"id": "a", "name": "A moment", "group": "recommend"}}
        for number in (1, 2, 3):
            monitor.say("Tide tables", "New tables", context, f"f{number}", content=True,
                        recommended=True)
            self.assertEqual(monitor.recommended_today(), number)
        last = self.app.db.query_one("SELECT metadata FROM notifications ORDER BY created_at DESC, "
                                     "rowid DESC LIMIT 1")
        self.assertEqual(json.loads(last["metadata"])["nth_today"], 3)

    def test_the_lesson_holds_the_whole_chain_but_not_the_screen(self) -> None:
        item = {"id": "rsv1", "source": "what an issue of the newspaper left over",
                "angle": "Their tide study needs these", "why": "They fit tide data",
                "keywords": {"proper": ["Thistle Harbour"], "zh": ["潮汐表"], "en": ["tide table"]}}
        chain = Reserve.chain(item, hit=["潮汐表"], score=7, judged=True)
        line = chain_line({"chain": chain, "reason": "You opened a tide chart", "nth_today": 4})
        for said in ("newspaper left over", "Their tide study", "They fit tide data",
                     "Thistle Harbour", "The words that did: 潮汐表", "Score 7", "judged by the model",
                     "You opened a tide chart", "number 4"):
            self.assertIn(said, line)
        self.assertNotIn("screen", json.dumps(chain))

    def test_a_moments_recommendation_says_what_set_it_off(self) -> None:
        line = chain_line({"monitor": {"activator": "Back from a break"}, "nth_today": 1})
        self.assertIn("Back from a break", line)

    def test_every_answer_to_one_keeps_the_chain_in_its_lesson(self) -> None:
        shown = {"id": "n1", "title": "Tide tables", "message": "New tables", "kind": "proactive",
                 "metadata": {"recommended": True, "nth_today": 2,
                              "chain": {"angle": "Their tide study needs these"}}}
        ident = self.app.learn_answer(shown, "good_to_know")
        row = self.app.db.query_one("SELECT kind, summary FROM lesson_evidence WHERE id = ?", (ident,))
        self.assertEqual(row["kind"], "recommendations")
        self.assertIn("Their tide study needs these", row["summary"])
        self.assertIn("number 2", row["summary"])


class NoLimitADayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.monitor = self.agent.app.monitor
        self.agent.app.config.set("monitor_most_a_day", 1)
        self.agent.app.config.set("monitor_least_gap_minutes", 1)
        self.monitor.latest = {"present": True}
        self.monitor.notifications = SimpleNamespace(_in_quiet_hours=lambda: False)

    def _fired(self, activator) -> None:
        when = datetime.now(timezone.utc) - timedelta(hours=1)
        self.monitor.db.execute(
            "INSERT INTO monitor_activators (id, data, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (activator["id"], json.dumps(activator), when.isoformat(), when.isoformat()))
        self.monitor.db.execute(
            "INSERT INTO monitor_firings (id, at, how, activator_id, acted) VALUES (?, ?, 'tick', ?, 1)",
            (f"f{activator['id']}", when.isoformat(), activator["id"]))

    def test_a_recommendation_is_not_held_by_the_number_a_break_is(self) -> None:
        rest = {"id": "b1", "group": "rest", "most_per_day": 9, "cooldown_minutes": 0}
        self._fired(rest)
        recommend = {"id": "r1", "group": "recommend", "most_per_day": 9, "cooldown_minutes": 0}
        now = datetime.now(timezone.utc)
        self.assertEqual(self.monitor._held(recommend, "tick", now), "")
        self.assertEqual(self.monitor._held(dict(rest, id="b2"), "tick", now),
                         "the most for a day already said")


if __name__ == "__main__":
    unittest.main()
