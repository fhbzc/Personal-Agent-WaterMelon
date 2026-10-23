"""The projects the user adds in the Experience tab: called by their names
alone; their description - the user's own words, kept where reading the
folders never rewrites it, shown under the name and on top of the project,
changed in place, and given to both agents verbatim; and their folders,
several, each added in the folder dialog and taken out after asking."""

from __future__ import annotations

import html
import json
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from support import TemporaryAgent
from test_work_runs import _TwoAgents


class _Request:
    def __init__(self, body):
        self.body = body

    def get(self, key, default=None):
        return self.body.get(key, default)

    def require(self, key):
        return self.body[key]

    def int_param(self, key, default):
        return int(self.body.get(key, default))


class _ProjectBase(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.experience = self.app.state_heartbeat.experience
        self.one = self.agent.dir / "slides"
        self.two = self.agent.dir / "data"
        for folder in (self.one, self.two):
            folder.mkdir()
        from personalized_agent.api.routes import build_routes
        self.routes = build_routes(self.app)

    def tearDown(self) -> None:
        for run in list(self.app.work._running.values()):
            run.stop_now = True
            run.release(True)
        end = time.time() + 30
        while time.time() < end and any(r.alive() for r in self.app.work._running.values()):
            time.sleep(0.05)
        self.agent.cleanup()

    def _post(self, body):
        return self.routes[("POST", "/api/experience/projects")](_Request(body))


class TheDescriptionIsKeptTests(_ProjectBase):

    WORDS = "Change the slides of the October talk: fewer words, bigger figures."

    def test_kept_apart_from_what_reading_says(self) -> None:
        made = self._post({"action": "create", "name": "Modify slides",
                           "description": self.WORDS,
                           "paths": [str(self.one), str(self.two)]})["project"]
        unit = made["unit_id"]
        from personalized_agent.heartbeat.experience.store import description_of
        self.assertEqual(description_of(self.experience.store.workspace(unit)), self.WORDS)
        # Reading the folders writes its own summary: the description stays.
        self.experience.store.set_processed(unit, "2026-10-08T12:00:00",
                                            {"title": "Talk", "goal": "a talk deck"})
        held = self.experience.store.workspace(unit)
        self.assertEqual(held["summary"]["goal"], "a talk deck")
        self.assertEqual(description_of(held), self.WORDS)
        # Listed with the project, and with its unit: its folders too.
        listed = self.routes[("GET", "/api/experience/projects")](_Request({}))["projects"]
        self.assertEqual(listed[0]["description"], self.WORDS)
        row = next(one for one in self.experience.units() if one["id"] == unit)
        self.assertEqual(row["description"], self.WORDS)
        self.assertEqual(row["paths"], [str(self.one.resolve()), str(self.two.resolve())])
        # Changed in place.
        said = self._post({"action": "update", "unit": unit, "description": "  New words. "})
        self.assertEqual(said["project"]["description"], "New words.")
        self.assertEqual(held["name"], self.experience.store.workspace(unit)["name"])
        self.assertIn("error", self._post({"action": "update", "unit": "proj_none",
                                           "description": "x"}))

    def test_a_project_made_before_has_its_goal_kept_as_its_description(self) -> None:
        made = self._post({"action": "create", "name": "Old", "description": "Its old words",
                           "paths": [str(self.one)]})["project"]
        store = self.experience.store
        # As a project made before descriptions had a place of their own.
        store.db.execute("UPDATE experience_workspaces SET summary = ? WHERE unit_id = ?",
                         (json.dumps({"title": "Old", "goal": "Its old words"}),
                          made["unit_id"]))
        # A folder added as a project of its own - not one the user made
        # with words - is left as it is.
        self.experience.add_directory(str(self.two))
        self.assertEqual(store.keep_descriptions(), 1)
        held = store.workspace(made["unit_id"])
        self.assertEqual(held["summary"]["description"], "Its old words")
        other = [one for one in store.workspaces(origin="chosen")
                 if not one["unit_id"].startswith("proj_")]
        self.assertTrue(other)
        self.assertNotIn("description", other[0]["summary"])
        # Once is enough.
        self.assertEqual(store.keep_descriptions(), 0)


class SeveralFoldersTests(_ProjectBase):

    def test_made_with_several_added_to_and_taken_out(self) -> None:
        made = self._post({"action": "create", "name": "Two folders",
                           "paths": [str(self.one), str(self.two)]})["project"]
        unit = made["unit_id"]
        self.assertEqual(len(self.experience.store.workspace(unit)["paths"]), 2)
        three = self.agent.dir / "more"
        three.mkdir()
        self._post({"action": "set", "unit": unit,
                    "paths": [str(self.one), str(self.two), str(three)]})
        self.assertEqual(len(self.experience.store.workspace(unit)["paths"]), 3)
        self._post({"action": "set", "unit": unit, "paths": [str(three)]})
        self.assertEqual(self.experience.store.workspace(unit)["paths"],
                         [str(three.resolve())])
        # The last one stays.
        self.assertIn("error", self._post({"action": "set", "unit": unit, "paths": []}))

    def test_the_work_may_be_granted_several_folders_each_taken_back(self) -> None:
        unit = "proj_x"
        self.assertNotIn("error", self.app.work.grant(unit, str(self.one)))
        self.assertNotIn("error", self.app.work.grant(unit, str(self.two)))
        self.assertEqual(len(self.app.work.folders(unit)), 2)
        self.app.work.revoke(unit, str(self.one.resolve()))
        self.assertEqual(self.app.work.folders(unit), [str(self.two.resolve())])


class TheAgentsAreGivenTheDescriptionTests(_ProjectBase):

    WORDS = "Keep the tone dry.\nNever touch the appendix."

    def test_verbatim_to_the_worker_and_the_reviewer(self) -> None:
        made = self._post({"action": "create", "name": "Notes", "description": self.WORDS,
                           "paths": [str(self.one)]})["project"]
        unit = self.experience.unit(made["unit_id"])
        models = _TwoAgents()
        self.app.work.models = models
        said = self.app.work.start(unit, "write the notes")
        self.assertNotIn("error", said, said)
        end = time.time() + 60
        while time.time() < end and self.app.work.runs(unit["id"])[0]["alive"]:
            time.sleep(0.05)
        line = f"The user's description of this project: {self.WORDS}"
        drafting = [r for r in models.asked if "DRAFT THE CONTRACT"
                    in str(r.messages[0]["content"])]
        self.assertTrue(drafting)
        self.assertIn(line, drafting[0].messages[0]["content"])
        rounds = [r for r in models.asked if "WORK ONE ROUND" in str(r.messages[0]["content"])]
        self.assertTrue(rounds)
        self.assertIn(line, rounds[0].messages[0]["content"])
        reviews = [r for r in models.asked if "You review a work agent" in r.system]
        self.assertTrue(reviews)
        self.assertTrue(all(line in str(r.messages[0]["content"]) for r in reviews))


class ThePanelTests(unittest.TestCase):
    """Names alone; the description under the name and on top, changed in
    place; folders listed with ×, asked before one is taken out, added in
    the dialog - on a project, on a new one, and for the work."""

    def setUp(self) -> None:
        from test_panels import _javascript_engine
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def test_names_descriptions_and_folders(self) -> None:
        web = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        mine = {"id": "proj_1", "kind": "directory", "name": "Project: Modify slides",
                "active": False, "learning": {}, "workspace": "C:/a",
                "workspace_origin": "chosen", "processed_until": "", "stages": 0,
                "tasks": [], "pending": 0, "paths": ["C:/a", "C:/b"],
                "description": "Fewer words, bigger figures."}
        theirs = {"id": "task-1", "kind": "task", "name": "Task: Read the mail",
                  "active": True, "learning": {}, "workspace": "", "stages": 3,
                  "tasks": [], "pending": 0, "paths": [], "description": ""}
        stub = {"/api/experience": {"stages": 3, "patterns": 0, "principles": 0,
                                    "units": [mine, theirs]},
                "/api/experience/projects": {"projects": []},
                "/api/experience/graph": {"unit": mine, "tasks": [], "cards": [],
                                          "stages": [], "edges": []},
                "/api/work/runs": {"runs": []},
                "/api/work/folders": {"folders": ["C:/g1", "C:/g2"], "warning": "w"},
                "/api/work/limits": {"defaults": {}},
                "/api/work/tree": {"cards": [], "stages": [], "edges": []},
                "/api/experience/projects#post": {}}
        click = "(el)=>el.dispatchEvent(new MouseEvent('click',{bubbles:true}))"
        steps = (
            f"const click={click};const text=(el)=>el?el.textContent.replace(/\\s+/g,' ').trim():'';"
            "activeTab='experience';"
            "document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.id==='experience'));"
            "await loadExperience(true);await wait();"
            "out.names=[...document.querySelectorAll('#xp-units [data-xp-unit]')].map(el=>el.firstChild.textContent.trim());"
            "out.title=text(document.querySelector('#xp-graph-title'));"
            "out.rowAbout=text(document.querySelector('#xp-units .xp-about'));"
            # A task the user did not add: headed by its name too.
            "experienceState.unit='task-1';STUB['/api/experience/graph']={unit:STUB['/api/experience'].units[1],"
            "  tasks:[],cards:[],stages:[],edges:[]};await xpOpenUnit(true);await wait();"
            "out.taskTitle=text(document.querySelector('#xp-graph-title'));"
            # A click on the description under a project's name chooses it.
            "STUB['/api/experience/graph']={unit:STUB['/api/experience'].units[0],"
            "  tasks:[],cards:[],stages:[],edges:[]};"
            "click(document.querySelector('#xp-units [data-xp-row=\"proj_1\"] .xp-about-words'));await wait();"
            "out.chosenByWords=experienceState.unit;"
            "out.taskTitleAgain=(experienceState.unit='task-1',"
            "  STUB['/api/experience/graph']={unit:STUB['/api/experience'].units[1],"
            "  tasks:[],cards:[],stages:[],edges:[]},'');await xpOpenUnit(true);await wait();"
            "out.tabText=document.querySelector('#experience').innerText;"
            "experienceState.unit='proj_1';STUB['/api/experience/graph']={unit:STUB['/api/experience'].units[0],"
            "  tasks:[],cards:[],stages:[],edges:[]};await xpOpenUnit(true);await wait();"
            "out.topAbout=text(document.querySelector('#xp-unit-about .xp-about'));"
            # The description, changed in place.
            "click(document.querySelector('#xp-unit-about [data-xp-about-edit]'));await wait();"
            "let box=document.querySelector('#xp-unit-about textarea');"
            "out.editing=!!box&&document.activeElement===box;"
            "box.value='New words.';box.dispatchEvent(new Event('input',{bubbles:true}));"
            "click(document.querySelector('#xp-unit-about [data-xp-about-save]'));await wait();"
            # The folders: each with ×, asked first.
            "const row=()=>document.querySelector('#xp-units [data-xp-folders-of=\"proj_1\"]');"
            "out.folders=[...row().querySelectorAll('.xp-folder')].map(li=>text(li.querySelector('code'))+':'+!!li.querySelector('.xp-folder-x'));"
            "click(row().querySelectorAll('[data-xp-folder-remove]')[1]);await wait();"
            "out.asked=text(row().querySelector('.xp-folder-ask'));"
            "out.postsAfterAsk=window.__posts.filter(p=>p.body.action==='set').length;"
            "click(row().querySelector('[data-xp-folder-no]'));await wait();"
            "out.askGone=!row().querySelector('.xp-folder-ask');"
            "click(row().querySelectorAll('[data-xp-folder-remove]')[1]);await wait();"
            "click(row().querySelector('[data-xp-folder-yes]'));await wait();"
            "STUB['/api/settings/choose-directory']={selected:'C:/c'};"
            "click(row().querySelector('[data-xp-folder-add]'));await wait();"
            # A new project with two folders, one of three taken out first.
            "click(document.querySelector('#xp-plus'));"
            "for(const f of ['C:/one','C:/two','C:/three']){"
            "  STUB['/api/settings/choose-directory']={selected:f};"
            "  click(document.querySelector('#xp-new-choose'));await wait();}"
            "out.newList=[...document.querySelectorAll('#xp-new-paths code')].map(text);"
            "click(document.querySelector('[data-xp-new-drop=\"1\"]'));await wait();"
            "document.querySelector('#xp-new-title').value='Talk';"
            "document.querySelector('#xp-new-description').value='For the talk';"
            "click(document.querySelector('#xp-new-create'));await wait();"
            # The work's folders: several, each with ×, asked first.
            "out.grants=[...document.querySelectorAll('.xp-folders .xp-folder')].map(li=>text(li.querySelector('code')));"
            "click(document.querySelectorAll('.xp-folders [data-xp-revoke]')[1]);await wait();"
            "out.grantAsk=text(document.querySelector('.xp-folders .xp-folder-ask'));"
            "click(document.querySelector('.xp-folders [data-xp-revoke-yes]'));await wait();"
            "STUB['/api/settings/choose-directory']={selected:'C:/g3'};"
            "click(document.querySelector('#xp-grant'));await wait();"
            "out.posts=window.__posts;")
        harness = (
            "<script>window.__posts=[];window.__gets=[];const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
            "if(o&&o.method==='POST')window.__posts.push({p,body:JSON.parse(o.body||'{}')});"
            "else window.__gets.push(String(u));"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "headers:{get:()=>null},json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';const out={};"
            "const wait=(ms)=>new Promise(r=>setTimeout(r,ms||80));"
            "try{" + steps + "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err,out});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=15000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
        match = re.search(r"<title>(.*?)</title>", completed.stdout or "", re.S)
        self.assertIsNotNone(match, "the page never finished rendering")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        # Names alone - no kind before them; the project the user added is
        # headed by its name, without "cards" or "attempts".
        self.assertEqual(out["names"], ["Modify slides", "Read the mail"])
        self.assertEqual(out["title"], "Modify slides")
        self.assertEqual(out["taskTitle"], "Read the mail")
        self.assertEqual(out["chosenByWords"], "proj_1",
                         "a click on the description chooses the project")
        # No label before any of them, anywhere in the tab.
        for label in ("Cards and attempts", "Project:", "Task:"):
            self.assertNotIn(label, out["tabText"])
        # Their description, under the name and on top.
        for said in (out["rowAbout"], out["topAbout"]):
            self.assertIn("Your description", said)
            self.assertIn("Fewer words, bigger figures.", said)
        self.assertTrue(out["editing"])
        posts = out["posts"]
        self.assertIn({"p": "/api/experience/projects", "body": {
            "action": "update", "unit": "proj_1", "description": "New words."}}, posts)
        self.assertEqual(out["folders"], ["C:/a:true", "C:/b:true"])
        self.assertIn("Take this folder out?", out["asked"])
        self.assertEqual(out["postsAfterAsk"], 0, "asked first, nothing changed")
        self.assertTrue(out["askGone"])
        sets = [one["body"] for one in posts if one["body"].get("action") == "set"]
        self.assertEqual(sets, [{"action": "set", "unit": "proj_1", "paths": ["C:/a"]},
                                {"action": "set", "unit": "proj_1",
                                 "paths": ["C:/a", "C:/b", "C:/c"]}])
        self.assertEqual(out["newList"], ["C:/one", "C:/two", "C:/three"])
        made = [one["body"] for one in posts if one["body"].get("action") == "create"]
        self.assertEqual(made, [{"action": "create", "name": "Talk",
                                 "description": "For the talk",
                                 "paths": ["C:/one", "C:/three"]}])
        self.assertEqual(out["grants"], ["C:/g1", "C:/g2"])
        self.assertIn("Take it out", out["grantAsk"])
        self.assertIn({"p": "/api/work/folders/remove",
                       "body": {"unit": "proj_1", "path": "C:/g2"}}, posts)
        self.assertIn({"p": "/api/work/folders", "body": {"unit": "proj_1", "path": "C:/g3"}},
                      posts)


if __name__ == "__main__":
    unittest.main()


class ARemovedProjectTests(_ProjectBase):
    """Remove on a project: it is not listed or learned again, and nothing
    is deleted - its stages and cards are the old learning's backup."""

    def _learned(self, unit, summary):
        store = self.experience.store
        card = store.add_card({"unit_id": unit, "task_id": unit, "title": f"{summary} card",
                               "type": "code", "statement": summary})
        stage = store.add_stage({"task_id": unit, "kind": "execution", "medium": "code",
                                 "summary": summary, "card_id": card["id"], "states": [],
                                 "began": "2026-09-01", "ended": "2026-09-01"})
        return card, stage

    def test_it_leaves_the_list_and_nothing_is_deleted(self) -> None:
        gone = self._post({"action": "create", "name": "Garden planner",
                           "paths": [str(self.one)]})["project"]["unit_id"]
        kept = self._post({"action": "create", "name": "Pond survey",
                           "paths": [str(self.two)]})["project"]["unit_id"]
        store = self.experience.store
        card, stage = self._learned(gone, "watering schedule for the tomato beds")
        later = store.add_stage({"task_id": gone, "kind": "evaluation", "medium": "code",
                                 "summary": "checked the tomato watering schedule",
                                 "card_id": card["id"], "states": [],
                                 "began": "2026-09-02", "ended": "2026-09-02"})
        store.add_edge(later["id"], stage["id"], "evaluates")
        _card, other = self._learned(kept, "watering schedule for the pond plants")
        store.link_stage_nature(stage["id"], "rec_shared_nature")
        store.link_stage_nature(other["id"], "rec_shared_nature")

        said = self.routes[("POST", "/api/experience/remove")](_Request({"unit": gone}))
        self.assertEqual(said.get("removed"), gone)
        # Nothing deleted: its stages, card, edges and natures are all kept.
        self.assertEqual({one["id"] for one in store.stages([gone])},
                         {stage["id"], later["id"]})
        self.assertIn(card["id"], [c["id"] for c in store.cards() if c["unit_id"] == gone])
        self.assertTrue(store.edges([stage["id"], later["id"]]))
        self.assertEqual(store.natures_of_stages([other["id"], stage["id"]]),
                         {other["id"]: ["rec_shared_nature"],
                          stage["id"]: ["rec_shared_nature"]})
        # Not listed, and not learned again.
        self.assertNotIn(gone, [one["id"] for one in self.experience.units()])
        self.assertIn(kept, [one["id"] for one in self.experience.units()])
        self.assertEqual(self.experience.learn([gone]), [])

    def test_not_while_it_is_being_learned(self) -> None:
        unit = self._post({"action": "create", "name": "Garden planner",
                           "paths": [str(self.one)]})["project"]["unit_id"]
        self.experience.learning_progress[unit] = {"running": True}
        try:
            said = self.experience.remove_unit(unit)
        finally:
            self.experience.learning_progress.pop(unit, None)
        self.assertIn("error", said)
        self.assertIn(unit, [one["id"] for one in self.experience.units()])

    def test_the_page_asks_first(self) -> None:
        script = (Path(__file__).resolve().parents[1] / "personalized_agent" / "api"
                  / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("data-xp-remove-yes", script)
        self.assertIn("Remove this project?", script)
        # The click on Remove only asks; the request is sent from the answer.
        at = script.index("removeAt.dataset.xpRemove)")
        self.assertLess(at, script.index('"/api/experience/remove"'))
