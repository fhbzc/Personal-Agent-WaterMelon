"""The Connections tab as the user uses it: the people grouped by
relationship and by organisation, searched, one opened, edited with a reason
and deleted with one - asked in the page first; and on the Overview, what the
user needs to know, worked out again on request."""

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


def _person(ident, name, relationship, org, **more):
    person = {"id": ident, "name": name, "aliases": [], "relationship": relationship,
              "closeness": 0, "org": org, "role": "", "location": "", "summary": "",
              "tags": [], "user_set": [], "added_by": "agent",
              "created_at": "2026-10-01T09:00:00", "updated_at": "2026-10-01T09:00:00",
              "checked_at": "", "deleted": False, "last_activity_at": "",
              "accounts_count": 0}
    person.update(more)
    return person


PEOPLE = [
    _person("p1", "Ada Lovelace", "colleague", "Acme", role="Engineer", closeness=4,
            aliases=["Ada L."], tags=["math"], user_set=["relationship"],
            last_activity_at="2026-10-09T10:00:00"),
    _person("p2", "Bob Stone", "colleague", "Globex", role="Manager"),
    _person("p3", "Cara <b>Lee</b>", "friend", "acme", closeness=2),
    _person("p4", "Dan Ng", "friend", "", location="Lisbon"),
]

LIST = {"people": PEOPLE,
        "relationships": ["family", "partner", "friend", "colleague", "manager", "report",
                          "mentor", "student", "classmate", "collaborator", "client",
                          "recruiter", "acquaintance", "other"],
        "closeness": {"1": "barely", "2": "loosely", "3": "regularly", "4": "closely",
                      "5": "closest"},
        "sources": [],
        "me": dict(_person("me1", "Robin H", "", "Northfield University", role="Analyst", is_self=True,
                           checked_at="2026-10-10T08:00:00",
                           field_times={"name": {"found_at": "2026-09-01T09:00:00",
                                                 "updated_at": "2026-09-01T09:00:00",
                                                 "by": "user"}}),
                   accounts=[{"id": "m1", "platform": "GitHub", "handle": "",
                              "url": "https://github.com/robin-hale-example",
                              "found_at": "2026-10-10T08:00:00",
                              "created_at": "2026-09-01T09:00:00",
                              "updated_at": "2026-09-01T09:00:00"}],
                   activity=[{"id": "me-e1", "at": "2026-10-08T10:00:00",
                              "what": "Posted <b>a</b> release note", "source": "your public page",
                              "url": "https://github.com/robin-hale-example/x/releases",
                              "found_at": "2026-10-10T08:00:00",
                              "created_at": "2026-10-10T08:00:00",
                              "updated_at": "2026-10-10T08:00:00"}]),
        "inbox": [{"id": "in1", "kind": "my_profile", "person_id": "me1",
                   "title": "Your role: Analyst \u2192 Lead Analyst",
                   "detail": "Your homepage says so since September.",
                   "change": {"field": "role", "value": "Lead Analyst"},
                   "sources": ["https://robin.example.org"], "found_at": "2026-10-10T08:00:00",
                   "created_at": "2026-10-10T08:01:00", "status": "open", "decided_at": "",
                   "reason": ""}],
        "labels": ["GitHub", "LinkedIn"], "orgs": ["Acme", "Globex"],
        "inbox_decided": [{"id": "in0", "title": "A profile of yours to add: X https://x.com/h",
                           "status": "dismissed", "decided_at": "2026-10-09T08:00:00",
                           "reason": "not mine", "sources": [], "change": {}}],
        "briefing": {"id": "b1", "at": "2026-10-10T08:00:00+00:00", "issue": 12,
                     "needs": [{"need": "a reviewer", "for": "the paper"}],
                     "items": [{"title": "Ada changed teams", "detail": "She now leads ML.",
                                "people": ["p1"], "names": ["Ada Lovelace"], "kind": "person",
                                "for": "the paper", "importance": 4, "why": "She reviews it.",
                                "sources": ["https://example.org/ada"],
                                "image": {"kept": "abc.jpg"}},
                               {"title": "Two of them know each other",
                                "detail": "Bob and Cara met.", "people": ["p2", "p3"],
                                "names": ["Bob Stone", "Cara Lee"], "kind": "across",
                                "for": "", "importance": 2, "why": "", "sources": []},
                               {"title": "Kaiyukan reopens", "detail": "From 1 Nov.",
                                "people": [], "names": [], "kind": "web",
                                "for": "the aquarium videos", "importance": 1, "why": "",
                                "sources": ["https://kaiyukan.example/news"]}],
                     "stats": {"calls": 2, "web": 1, "tokens_in": 10, "tokens_out": 5,
                               "stale_offered": 0}},
        "progress": {"running": False, "stage": "", "done": 4, "total": 4,
                     "started_at": "2026-10-10T07:59:00", "finished_at": "2026-10-10T08:00:00",
                     "items": 2},
        "deleted": 1, "lessons": {"kept": 3, "most": 100, "summarised": 7}}

DETAIL = dict(PEOPLE[0], accounts=[{"id": "a1", "platform": "GitHub", "handle": "ada",
                                    "url": "https://github.com/ada", "source": "web",
                                    "updated_at": "2026-10-02T09:00:00"}],
              activity=[{"id": "e1", "at": "2026-10-09T10:00:00", "what": "Gave a talk",
                         "source": "the public web", "url": "https://example.org/talk",
                         "evidence": [], "updated_at": "2026-10-09T12:00:00"},
                        {"id": "e2", "at": "", "what": "Moved to <i>ML</i>",
                         "source": "the user's activity", "url": "javascript:alert(1)",
                         "evidence": [], "updated_at": "2026-10-08T12:00:00"}],
              actions=[{"id": "x1", "at": "2026-10-03T09:00:00", "by": "user",
                        "action": "edit", "person_name": "Ada Lovelace",
                        "before": {"relationship": ""}, "after": {"relationship": "colleague"},
                        "reason": "we work together"}])

LESSONS = {"actions": DETAIL["actions"], "summary": {"text": "Mostly work people.",
                                                     "covers": 7, "until": ""},
           "deleted": [_person("p9", "Old Contact", "", "", deleted=True,
                               deleted_at="2026-10-05T09:00:00", deleted_by="user",
                               deleted_reason="spam")]}


class TheConnectionsPanelTests(unittest.TestCase):

    def setUp(self) -> None:
        from test_panels import _javascript_engine
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _stub(self):
        return {"/api/connections": LIST, "/api/connections/lessons": LESSONS,
                # The Overview card's own, lighter read.
                "/api/connections/briefing": {"briefing": LIST["briefing"],
                                              "progress": LIST["progress"],
                                              "issues": [
                    {"id": "b1", "at": "2026-10-10T08:00:00+00:00", "issue": 12, "stories": 3},
                    {"id": "b0", "at": "2026-10-09T08:00:00+00:00", "issue": 11,
                     "stories": 1}]},
                "/api/connections/person?id=p1": DETAIL,
                "/api/connections/person?id=me1": dict(LIST["me"], actions=[]),
                "/api/connections/merge_preview?id=p1&other=p3": {
                    "keep": {"id": "p1", "name": "Ada Lovelace"},
                    "other": {"id": "p3", "name": "Cara Lee"},
                    "accounts": [{"platform": "X", "handle": "cara", "url": ""}],
                    "activity_count": 7, "activity": [{"at": "", "what": "Posted <b>a</b>"}],
                    "names": ["Cara Lee"], "tags": ["chess"], "filled": {"location": "Paris"}},
                "/api/connections/person": {"id": "p1", "name": "Ada Lovelace"},
                "/api/connections/run": {"started": True, "progress": {"running": True}},
                "/api/state_heartbeat": {"connections": {"running": True,
                                                         "stage": "reading pages",
                                                         "done": 2, "total": 4}}}

    def test_what_is_typed_survives_the_tab_read_again(self) -> None:
        """Your profile being edited, the tab read again in the background - a
        picture found, the agent's round over: what was typed stays, and Save
        sends it."""
        steps = (
            "click(document.querySelector('#tabs button[data-tab=\"connections\"]'));"
            "await wait(500);"
            "click(document.querySelector('#cx-me-open'));await wait(400);"
            "click(document.querySelector('#cx-person [data-cx-edit-open]'));await wait();"
            "document.querySelector('#cx-edit').elements.name.value='A New Name';"
            "await loadConnections();await wait(300);"
            "const form=document.querySelector('#cx-edit');"
            "out.kept=form?form.elements.name.value:null;"
            "form.requestSubmit();await wait(400);"
            "out.edits=window.__posts.filter(x=>x.p==='/api/connections/person'"
            "  &&x.body.action==='edit').map(x=>x.body);")
        result = _render(self.engine, self._stub(), steps)
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        self.assertEqual(out["kept"], "A New Name")
        self.assertEqual(len(out["edits"]), 1, out["edits"])
        self.assertEqual(out["edits"][0]["name"], "A New Name")
        self.assertEqual(out["edits"][0]["id"], "me1")

    def test_people_grouped_searched_opened_edited_and_deleted(self) -> None:
        steps = (
            "click(document.querySelector('#tabs button[data-tab=\"connections\"]'));"
            "await wait(500);"
            "out.active=document.querySelector('#connections').classList.contains('active');"
            "const groups=()=>[...document.querySelectorAll('#cx-list details')].map(d=>"
            "[text(d.querySelector('.cx-group-name')),text(d.querySelector('.cx-group-count'))]);"
            "out.byRelationship=groups();"
            "out.rowNames=[...document.querySelectorAll('#cx-list .cx-row-name strong')].map(text);"
            "out.escaped=!document.querySelector('#cx-list b');"
            "out.news=text(document.querySelector('[data-cx-person=\"p1\"] .cx-row-news'));"
            "out.noNews=text(document.querySelector('[data-cx-person=\"p2\"] .cx-row-news'));"
            "out.dots=document.querySelectorAll('[data-cx-person=\"p1\"] .cx-level i.on').length;"
            "type(document.querySelector('#cx-search'),'lisbon');"
            "out.searched=[...document.querySelectorAll('#cx-list .cx-row')].map(r=>r.dataset.cxPerson);"
            "type(document.querySelector('#cx-search'),'acme');"
            "out.searchedOrg=[...document.querySelectorAll('#cx-list .cx-row')].map(r=>r.dataset.cxPerson);"
            "type(document.querySelector('#cx-search'),'');"
            "const by=document.querySelector('#cx-group');by.value='org';"
            "by.dispatchEvent(new Event('change',{bubbles:true}));"
            "out.byOrg=groups();"
            "out.top=document.querySelector('#connections .cx-top').compareDocumentPosition("
            "document.querySelector('#connections .cx-grid'))&Node.DOCUMENT_POSITION_FOLLOWING;"
            "out.noYourProfilesCard=!document.querySelector('#cx-sources');"
            "out.labels=[...document.querySelectorAll('#cx-label-list option')].map(o=>o.value);"
            "out.orgs=[...document.querySelectorAll('#cx-org-list option')].map(o=>o.value);"
            "out.addLabelList=document.querySelector('#cx-add-links .cx-add-label').getAttribute('list');"
            "out.addOrgList=document.querySelector('#cx-add-org').getAttribute('list');"
            # YOU: your picture beside People opens your profile as anyone's.
            "out.meButton=!!document.querySelector('#cx-me-open .cx-avatar, #cx-me-open img, #cx-me-open span');"
            "click(document.querySelector('#cx-me-open'));await wait(400);"
            "out.meView=text(document.querySelector('#cx-person'));"
            "out.meSelected=document.querySelector('#cx-me-open').classList.contains('selected');"
            "out.meEscaped=!document.querySelector('#cx-person b');"
            "out.meNoDelete=!document.querySelector('#cx-person [data-cx-ask=\"delete\"]');"
            "out.meNoMerge=!document.querySelector('#cx-person [data-cx-ask=\"merge\"]');"
            # Shown, not a form - until Edit; Cancel shows it again.
            "out.meShown=!!document.querySelector('#cx-person .cx-facts')"
            "  &&!document.querySelector('#cx-person #cx-edit');"
            "click(document.querySelector('#cx-person [data-cx-edit-open]'));await wait();"
            "out.meForm=!!document.querySelector('#cx-person #cx-edit .cx-aff-org[list=\"cx-org-list\"]');"
            "click(document.querySelector('#cx-person [data-cx-edit-cancel]'));await wait();"
            "out.meShownAgain=!document.querySelector('#cx-person #cx-edit');"
            "const addMine=document.querySelector('#cx-person [data-cx-profile-add]');"
            "addMine.querySelector('input[type=\"url\"]').value='https://robin.example.org';"
            "addMine.elements.label.value='Homepage';addMine.requestSubmit();await wait(400);"
            "out.profileAdds=window.__posts.filter(x=>x.body.action==='add_profile').map(x=>x.body);"
            "out.inbox=text(document.querySelector('#cx-inbox'));"
            "out.inboxCount=text(document.querySelector('#cx-inbox-count'));"
            "out.noOldCard=!document.querySelector('#cx-lessons');"
            # An inbox item answered: the reason goes with it.
            "document.querySelector('[data-cx-inbox=\"in1\"] input').value='right';"
            "click(document.querySelector('[data-cx-inbox=\"in1\"] [data-cx-inbox-accept]'));"
            "await wait(400);"
            "out.answers=window.__posts.filter(x=>x.p==='/api/connections/inbox').map(x=>x.body);"
            "click(document.querySelector('#cx-person [data-cx-me-run]'));await wait(300);"
            "out.meRuns=window.__posts.filter(x=>x.p==='/api/connections/run').map(x=>x.body);"
            # Open a person.
            "click(document.querySelector('[data-cx-person=\"p1\"]'));await wait(400);"
            "out.meUnselected=!document.querySelector('#cx-me-open').classList.contains('selected');"
            "out.gets=window.__gets.filter(g=>g.startsWith('/api/connections/person'));"
            "out.detail=text(document.querySelector('#cx-person'));"
            "out.mine=[...document.querySelectorAll('#cx-person .cx-mine')].map(m=>"
            "m.parentElement.firstChild.textContent.trim());"
            "out.links=[...document.querySelectorAll('#cx-person a')].map(a=>"
            "[a.getAttribute('href'),a.rel,a.target]);"
            "out.noScriptLink=!document.querySelector('#cx-person a[href^=\"javascript\"]');"
            "out.noItalic=!document.querySelector('.cx-activity i');"
            "out.facts=text(document.querySelector('#cx-person .cx-facts'));"
            "out.formBeforeEdit=!!document.querySelector('#cx-edit');"
            # Edit one field, with a reason: the role at the main organisation;
            # then another relationship, made the main one.
            "click(document.querySelector('#cx-person [data-cx-edit-open]'));await wait();"
            "const form=document.querySelector('#cx-edit');"
            "form.querySelector('[data-cx-aff-row] .cx-aff-role').value='Lead';"
            "form.elements.reason.value='promoted';"
            "form.requestSubmit();await wait(400);"
            "out.shownAfterSave=!document.querySelector('#cx-edit');"
            "click(document.querySelector('#cx-person [data-cx-edit-open]'));await wait();"
            "const again=document.querySelector('#cx-edit');"
            "click(again.querySelector('[data-cx-row-add=\"rel\"]'));await wait();"
            "const rows=again.querySelectorAll('[data-cx-rel-row]');"
            "rows[1].querySelector('.cx-rel').value='Friend';"
            "rows[1].querySelector('input[type=\"radio\"]').checked=true;"
            "click(again.querySelector('[data-cx-row-add=\"aff\"]'));await wait();"
            "const affs=again.querySelectorAll('[data-cx-aff-row]');"
            "affs[1].querySelector('.cx-aff-org').value='MIT';"
            "affs[1].querySelector('.cx-aff-role').value='lecturer';"
            # A job given, and a second place made the main one.
            "again.querySelector('[data-cx-text-row=\"job\"] .cx-text').value='engineer';"
            "click(again.querySelector('[data-cx-row-add=\"place\"]'));await wait();"
            "const places=again.querySelectorAll('[data-cx-text-row=\"place\"]');"
            "places[1].querySelector('.cx-text').value='Boston';"
            "places[1].querySelector('input[type=\"radio\"]').checked=true;"
            "again.requestSubmit();await wait(400);"
            "out.edits=window.__posts.filter(x=>x.p==='/api/connections/person'&&x.body.action==='edit').map(x=>x.body);"
            # Delete: asked in the page first, nothing sent until it is answered.
            "click(document.querySelector('#cx-person [data-cx-ask=\"delete\"]'));await wait();"
            "out.askShown=!document.querySelector('#cx-delete-ask').hidden;"
            "out.deletesBefore=window.__posts.filter(x=>x.body.action==='delete').length;"
            "document.querySelector('#cx-delete-reason').value='not a real contact';"
            "click(document.querySelector('[data-cx-delete-yes]'));await wait(400);"
            "out.deletes=window.__posts.filter(x=>x.body.action==='delete').map(x=>x.body);"
            "out.after=text(document.querySelector('#cx-person'));")
        result = _render(self.engine, self._stub(), steps)
        self.assertEqual(result["err"], "")
        out = result["out"]
        self.assertTrue(out["active"])
        # Relationships in the API's order: friend before colleague.
        self.assertEqual(out["byRelationship"], [["Friend", "2"], ["Colleague", "2"]])
        self.assertIn("Cara <b>Lee</b>", out["rowNames"])
        self.assertTrue(out["escaped"])
        self.assertTrue(out["news"].startswith("last news"))
        self.assertEqual(out["noNews"], "no news yet")
        self.assertEqual(out["dots"], 4)
        self.assertEqual(out["searched"], ["p4"])
        self.assertEqual(sorted(out["searchedOrg"]), ["p1", "p3"])
        # Organisations alphabetical, case folded together, none last.
        self.assertEqual(out["byOrg"], [["Acme", "2"], ["Globex", "1"],
                                        ["No organisation", "1"]])
        # The inbox above the people; no card of "your profiles" any more -
        # you are a profile as the others are, opened from beside People.
        self.assertTrue(out["top"])
        self.assertTrue(out["noYourProfilesCard"])
        self.assertTrue(out["meButton"])
        self.assertTrue(out["meSelected"])
        self.assertTrue(out["meUnselected"], "another opened: you are not marked")
        for said in ("Robin H", "https://github.com/robin-hale-example", "Posted <b>a</b> release note",
                     "found ", "last looked up", "Look me up now", "waits in the Inbox"):
            self.assertIn(said, out["meView"])
        self.assertTrue(out["meEscaped"])
        self.assertTrue(out["meNoDelete"])
        self.assertTrue(out["meNoMerge"])
        self.assertTrue(out["meShown"])
        self.assertTrue(out["meForm"])
        self.assertTrue(out["meShownAgain"])
        # A profile added with its label; the boxes suggest what is in use.
        self.assertEqual(out["profileAdds"], [{"action": "add_profile", "id": "me1",
                                               "url": "https://robin.example.org",
                                               "label": "Homepage"}])
        self.assertEqual(out["labels"], ["GitHub", "LinkedIn"])
        self.assertEqual(out["orgs"], ["Acme", "Globex"])
        self.assertEqual((out["addLabelList"], out["addOrgList"]),
                         ("cx-label-list", "cx-org-list"))
        # The inbox: your own details, waiting for you; what you decided below.
        self.assertIn("Your role: Analyst \u2192 Lead Analyst", out["inbox"])
        self.assertIn("https://robin.example.org", out["inbox"])
        self.assertIn("Decided (1)", out["inbox"])
        self.assertEqual(out["inboxCount"], "1")
        self.assertTrue(out["noOldCard"], "your changes are in the Lessons tab now")
        self.assertEqual(out["answers"], [{"id": "in1", "accept": True, "reason": "right"}])
        self.assertEqual(out["meRuns"], [{"me": True}])
        self.assertEqual(out["gets"][-1], "/api/connections/person?id=p1")
        self.assertIn("Gave a talk", out["detail"])
        self.assertIn("date unknown", out["detail"])
        self.assertIn("Moved to <i>ML</i>", out["detail"])
        self.assertIn("we work together", out["detail"])
        self.assertEqual(out["mine"], ["Relationship"])
        self.assertIn(["https://github.com/ada", "noopener noreferrer", "_blank"], out["links"])
        self.assertTrue(out["noScriptLink"])
        self.assertTrue(out["noItalic"])
        # Shown, not a form, until Edit: the role at the organisation, the
        # relationship.
        self.assertFalse(out["formBeforeEdit"])
        self.assertIn("Engineer at Acme", out["facts"])
        self.assertIn("Colleague", out["facts"])
        self.assertTrue(out["shownAfterSave"])
        # Only what changed is sent, with the reason - the lists the main one
        # first.
        self.assertEqual(out["edits"][0], {"action": "edit", "id": "p1",
                                           "affiliations": [{"org": "Acme", "role": "Lead"}],
                                           "reason": "promoted"})
        self.assertEqual(out["edits"][1], {
            "action": "edit", "id": "p1", "professions": ["engineer"],
            "locations": ["Boston"],
            "relationships": ["friend", "colleague"],
            "affiliations": [{"org": "Acme", "role": "Engineer"},
                             {"org": "MIT", "role": "lecturer"}]})
        self.assertTrue(out["askShown"])
        self.assertEqual(out["deletesBefore"], 0)
        self.assertEqual(out["deletes"], [{"action": "delete", "id": "p1",
                                           "reason": "not a real contact"}])
        self.assertIn("was deleted", out["after"])

    def test_a_merge_shows_what_comes_over_before_it_can_be_confirmed(self) -> None:
        steps = (
            "click(document.querySelector('#tabs button[data-tab=\"connections\"]'));"
            "await wait(500);"
            "click(document.querySelector('[data-cx-person=\"p1\"]'));await wait(400);"
            "click(document.querySelector('[data-cx-ask=\"merge\"]'));"
            "const yes=document.querySelector('[data-cx-merge-yes]');"
            "out.offFirst=yes.disabled;"
            "const sel=document.querySelector('#cx-merge-other');sel.value='p3';"
            "sel.dispatchEvent(new Event('change',{bubbles:true}));await wait(400);"
            "out.preview=text(document.querySelector('#cx-merge-preview'));"
            "out.escaped=!document.querySelector('#cx-merge-preview b');"
            "out.onAfter=!yes.disabled;")
        result = _render(self.engine, self._stub(), steps)
        self.assertEqual(result["err"], "")
        out = result["out"]
        self.assertTrue(out["offFirst"])
        self.assertTrue(out["onAfter"])
        self.assertTrue(out["escaped"])
        for said in ("Cara Lee would be merged into Ada Lovelace", "Accounts (1)", "cara",
                     "Activity (7)", "and 6 more", "chess", "location: Paris"):
            self.assertIn(said, out["preview"])

    def test_an_account_is_moved_to_another_person(self) -> None:
        steps = (
            "click(document.querySelector('#tabs button[data-tab=\"connections\"]'));"
            "await wait(500);"
            "click(document.querySelector('[data-cx-person=\"p1\"]'));await wait(400);"
            "const box=document.querySelector('[data-cx-move-box=\"a1\"]');"
            "out.hiddenFirst=box.hidden;"
            "click(document.querySelector('[data-cx-move-ask=\"a1\"]'));"
            "out.shown=!box.hidden;"
            "out.choices=[...box.querySelectorAll('[data-cx-move-to] option')].map(o=>o.value);"
            "const to=box.querySelector('[data-cx-move-to]');to.value='p2';"
            "type(box.querySelector('[data-cx-move-reason]'),'it is Bob\\'s');"
            "click(box.querySelector('[data-cx-move-yes]'));await wait(400);"
            "out.moves=window.__posts.filter(x=>x.p==='/api/connections/person'"
            "&&x.body.action==='move_account').map(x=>x.body);")
        result = _render(self.engine, self._stub(), steps)
        self.assertEqual(result["err"], "")
        out = result["out"]
        self.assertTrue(out["hiddenFirst"])
        self.assertTrue(out["shown"])
        self.assertEqual(out["choices"], ["", "p2", "p3", "p4"], "every other person")
        self.assertEqual(out["moves"], [{"action": "move_account", "id": "p2",
                                         "account": "a1", "reason": "it is Bob's"}])

    def test_what_you_need_to_know_and_working_it_out_again(self) -> None:
        """A newspaper's front page: the issue's number and when it was
        worked out, the most important story leading with its picture and
        why it matters, the small ones in brief; every issue kept, and an
        earlier one read by choosing it."""
        steps = (
            "click(document.querySelector('#tabs button[data-tab=\"recommend\"]'));"
            "await followKnowCard();await wait(300);"
            "const card=document.querySelector('#recommend');"
            "out.inItsTab=!!document.querySelector('#recommend #ov-know');"
            "out.onOverview=!!document.querySelector('#overview #ov-know');"
            "out.mast=text(card.querySelector('.paper-mast'));"
            "out.lead=text(card.querySelector('.paper-lead .paper-headline'));"
            "out.standfirst=text(card.querySelector('.paper-lead .paper-standfirst'));"
            "out.picture=card.querySelector('.paper-lead img').getAttribute('src');"
            "out.brief=[...card.querySelectorAll('.paper-brief .paper-headline')].map(text);"
            "out.text=text(card);"
            "out.issues=[...card.querySelectorAll('[data-know-issue] option')].map(o=>o.value);"
            "const pick=card.querySelector('[data-know-issue]');pick.value='b0';"
            "pick.dispatchEvent(new Event('change',{bubbles:true}));await wait(300);"
            "out.asked=(window.__gets||[]).filter(u=>u.includes('/api/connections/briefing?id=b0'))"
            ".length;"
            "click(document.querySelector('#ov-know-run'));await wait(400);"
            "out.runs=window.__posts.filter(x=>x.p==='/api/connections/run').length;"
            # The State heartbeat's line reads the same small endpoint.
            "STUB['/api/connections/briefing']={briefing:null,progress:{running:true,"
            "stage:'reading pages',done:2,total:4}};"
            "await loadStateBeatConnections();"
            "out.beat=text(document.querySelector('#dbg-states-connections'));"
            "out.beatBar=!!document.querySelector('#dbg-states-connections .sb-progress-bar');")
        result = _render(self.engine, self._stub(), steps)
        self.assertEqual(result["err"], "")
        out = result["out"]
        self.assertTrue(out["inItsTab"])
        self.assertFalse(out["onOverview"], "a tab of its own, not on the Overview")
        self.assertIn("What you need to know", out["mast"])
        self.assertIn("No. 12, worked out", out["mast"])
        self.assertIn("2026", out["mast"])
        self.assertEqual(out["lead"], "Ada changed teams")
        self.assertEqual(out["standfirst"], "She reviews it.")
        self.assertEqual(out["picture"], "/api/connections/briefing/picture?name=abc.jpg")
        self.assertEqual(out["brief"], ["Two of them know each other", "Kaiyukan reopens"])
        self.assertIn("For the paper", out["text"])
        self.assertIn("kaiyukan.example", out["text"])
        self.assertEqual(out["issues"], ["b1", "b0"])
        self.assertEqual(out["asked"], 1)
        self.assertEqual(out["runs"], 1)
        self.assertIn("Connections: reading pages", out["beat"])
        self.assertIn("(2 of 4)", out["beat"])
        self.assertTrue(out["beatBar"])

    def test_customized_recommendation_is_a_paper_of_pages(self) -> None:
        """An issue of Customized Recommendation: a page per topic - the web's
        and the contacts' together - turned at the top; each story saying
        what in it is new to the reader, and taking their say."""
        stub = self._stub()
        stories = [
            {"id": "s1", "title": "A new qubit record", "detail": "Coherence doubled.",
             "new_to_you": "the record itself", "topic_id": "t1", "kind": "web",
             "sources": ["https://news.example.org/q"], "score": 0.9},
            {"id": "s2", "title": "Ada joined a quantum lab", "detail": "She moved.",
             "topic_id": "t1", "kind": "person", "names": ["Ada Lovelace"], "people": ["p1"],
             "sources": [], "score": 0.7},
            {"id": "s3", "title": "Soup science", "detail": "Onions.", "topic_id": "t2",
             "explore": True, "sources": ["https://food.example/s"], "score": 0.5}]
        stub["/api/connections/briefing"] = {
            "briefing": {"id": "b9", "at": "2026-10-14T08:00:00+00:00", "issue": 13,
                         "items": stories, "stats": {"paper": {
                             "front": ["s1", "s2", "s3"], "brief": [],
                             "pages": [{"key": "t1", "title": "Quantum computing",
                                        "stories": ["s1", "s2"]}]}}},
            "issues": [{"id": "b9", "at": "2026-10-14T08:00:00+00:00", "issue": 13,
                        "stories": 3}],
            "says": {"s2": "less"},
            "progress": {"running": False},
            "schedule": {"next": "2026-10-15T08:00:00+01:00", "times": ["08:00"]}}
        stub["/api/recommend/feedback"] = {"ok": True}
        steps = (
            "const order=[...document.querySelectorAll('#tabs button')].map(b=>b.dataset.tab);"
            "out.second=order[1];"
            "click(document.querySelector('#tabs button[data-tab=\"recommend\"]'));"
            "await followKnowCard();await wait(300);"
            "const card=document.querySelector('#recommend');"
            "out.mast=text(card.querySelector('.paper-mast'));"
            "out.when=text(document.querySelector('#ov-know-when'));"
            "out.tabs=[...document.querySelectorAll('#rec-pages [data-know-page]')].map(text);"
            "out.issuePicker=!!document.querySelector('#rec-pages [data-know-issue]');"
            "const side=document.querySelector('#rec-pages').getBoundingClientRect();"
            "const main=document.querySelector('#ov-know').getBoundingClientRect();"
            "out.leftOfMain=side.right<=main.left+1&&side.width>0;"
            "out.frontLead=text(card.querySelector('.paper-lead .paper-headline'));"
            "out.front=text(card);"
            "out.saidBefore=[...card.querySelectorAll('.paper-said')].map(text);"
            "click(document.querySelector('#rec-pages [data-know-page=\"t1\"]'));await wait(100);"
            "out.page=text(document.querySelector('#ov-know .paper-section'));"
            "out.onPage=[...document.querySelectorAll('#ov-know .paper-headline')].map(text);"
            "click(document.querySelector('#ov-know [data-know-say=\"knew\"][data-story=\"s1\"]'));"
            "await wait(200);"
            "out.said=window.__posts.filter(x=>x.p==='/api/recommend/feedback').map(x=>x.body);"
            "out.saidAfter=[...document.querySelectorAll('#ov-know .paper-said')].map(text);"
            "click(document.querySelector('#ov-know [data-know-say=\"knew\"][data-story=\"s1\"]'));"
            "await wait(200);"
            "out.sentAgain=window.__posts.filter(x=>x.p==='/api/recommend/feedback').length;")
        result = _render(self.engine, stub, steps)
        self.assertEqual(result["err"], "")
        out = result["out"]
        self.assertEqual(out["second"], "recommend", "right after the Overview")
        self.assertIn("Customized Recommendation", out["mast"])
        self.assertIn("No. 13", out["mast"])
        self.assertIn("Next issue", out["when"])
        self.assertTrue(out["leftOfMain"], "the pages down the left, the paper on the right")
        self.assertTrue(out["issuePicker"])
        self.assertTrue(out["tabs"][0].startswith("A1 Front page"), out["tabs"])
        self.assertTrue(out["tabs"][1].startswith("B1 Quantum computing"), out["tabs"])
        # What the user said before, kept and shown.
        self.assertEqual(out["saidBefore"], ["Not interested"])
        self.assertEqual(out["frontLead"], "A new qubit record")
        self.assertIn("New to you: the record itself", out["front"])
        self.assertIn("Something different", out["front"])
        self.assertEqual(out["page"], "Quantum computing")
        self.assertEqual(out["onPage"], ["A new qubit record", "Ada joined a quantum lab"])
        self.assertEqual(out["said"], [{"issue": "b9", "story": "s1", "action": "knew"}])
        # Kept on the story at once - and the same again is not sent twice.
        self.assertIn("Already knew", out["saidAfter"])
        self.assertEqual(out["sentAgain"], 1)

    def test_the_times_of_day_are_set_on_its_own_tab(self) -> None:
        """When an issue is made: on Customized Recommendation's tab, not in
        Settings - changed where it stands, no Edit: a time added, set and
        kept at once; one taken off with its × - the last one too: then no
        issue is made by itself, only with the button, and it says so."""
        stub = self._stub()
        stub["/api/connections/briefing"] = {
            "briefing": None, "issues": [], "progress": {"running": False},
            "schedule": {"times": ["08:00"], "time_zone": "WEST (UTC+0100)",
                         "next": "2026-10-15T08:00:00+01:00"}}
        steps = (
            "click(document.querySelector('#tabs button[data-tab=\"recommend\"]'));"
            "await followKnowCard();await wait(200);"
            "const box=document.querySelector('#rec-times');"
            "out.shown=text(box);"
            "out.inSettings=!!document.querySelector('#settings .times-control');"
            "out.edit=!!box.querySelector('[data-rec-times-edit]');"
            "out.lastCanGo=!box.querySelector('.times-one button').disabled;"
            "out.noneHidden=box.querySelector('.times-none').hidden;"
            "click([...box.querySelectorAll('button')].find(b=>b.textContent==='Add a time'));"
            "const times=[...box.querySelectorAll('input[type=time]')];"
            "times[1].value='18:30';times[1].dispatchEvent(new Event('change',{bubbles:true}));"
            "await wait(900);"
            "out.sent=window.__posts.filter(x=>x.p==='/api/settings').map(x=>x.body);"
            "click(box.querySelectorAll('.times-one button')[0]);await wait(900);"
            "out.removed=window.__posts.filter(x=>x.p==='/api/settings').map(x=>x.body).slice(-1);"
            "out.oneLeft=box.querySelectorAll('.times-one').length;"
            "click(box.querySelector('.times-one button'));await wait(900);"
            "out.none=window.__posts.filter(x=>x.p==='/api/settings').map(x=>x.body).slice(-1);"
            "out.noneLeft=box.querySelectorAll('.times-one').length;"
            "out.noneSaid=box.querySelector('.times-none').hidden?'':text(box.querySelector('.times-none'));")
        result = _render(self.engine, stub, steps)
        self.assertEqual(result["err"], "")
        out = result["out"]
        self.assertIn("Each day at", out["shown"])
        self.assertIn("WEST", out["shown"])
        self.assertFalse(out["inSettings"])
        self.assertFalse(out["edit"], "no Edit")
        self.assertTrue(out["lastCanGo"], "the only time can be taken off too")
        self.assertTrue(out["noneHidden"])
        self.assertEqual(out["sent"], [{"values": {"recommend_times": "08:00, 18:30"}}])
        self.assertEqual(out["removed"], [{"values": {"recommend_times": "18:30"}}])
        self.assertEqual(out["oneLeft"], 1)
        # Every time off: none sent, and said - an issue only with the button.
        self.assertEqual(out["none"], [{"values": {"recommend_times": ""}}])
        self.assertEqual(out["noneLeft"], 0)
        self.assertIn("only when you press Make an issue now", out["noneSaid"])

    def test_your_changes_are_a_section_of_the_lessons_tab(self) -> None:
        stub = self._stub()
        stub["/api/lessons"] = {"kinds": [
            {"kind": "notifications", "title": "Notification lessons", "actions": 0,
             "waiting": 0, "lessons": []},
            {"kind": "connections", "title": "Connection lessons", "actions": 9, "waiting": 0,
             "summary": {"text": "Mostly work people.", "covers": 7},
             "lessons": [
                 {"id": "x2", "acted_at": "2026-10-05T09:00:00", "action": "delete",
                  "did": "deleted", "title": "Old Contact", "person_id": "p9",
                  "event": {"user_said": "spam", "changes": []}, "deleted_now": True},
                 {"id": "x1", "acted_at": "2026-10-03T09:00:00", "action": "edit",
                  "did": "edited", "title": "Ada Lovelace", "person_id": "p1",
                  "event": {"user_said": "we work together", "changes": [
                      {"field": "relationship", "before": "", "after": "colleague"}]},
                  "deleted_now": False}]}]}
        steps = (
            "click(document.querySelector('#tabs button[data-tab=\"lessons\"]'));"
            "await loadLessons();await wait(300);"
            "const cards=[...document.querySelectorAll('#lessons-kinds .lesson-kind')];"
            "out.titles=cards.map(c=>text(c.querySelector('h3')));"
            "out.text=text(cards[1]);"
            "out.notThought=!cards[1].textContent.includes('Not yet thought about');"
            "click(cards[1].querySelector('.lesson-restore-ask'));await wait();"
            "cards[1].querySelector('.lesson-restore-sure input').value='back at work';"
            "click(cards[1].querySelector('.lesson-restore-yes'));await wait(400);"
            "out.restores=window.__posts.filter(x=>x.p==='/api/connections/person').map(x=>x.body);"
            "const again=[...document.querySelectorAll('#lessons-kinds .lesson-kind')][1];"
            "const one=again.querySelector('[data-lesson=\"x1\"]');"
            "click(one.querySelector('.lesson-delete'));await wait();"
            "click(one.querySelector('.lesson-yes'));await wait(400);"
            "out.deletes=window.__posts.filter(x=>x.p==='/api/lessons/delete').map(x=>x.body);")
        result = _render(self.engine, stub, steps)
        self.assertEqual(result["err"], "")
        out = result["out"]
        self.assertTrue(out["titles"][1].startswith("Connection lessons"))
        self.assertIn("7 older changes, summarised", out["text"])
        self.assertIn("Mostly work people.", out["text"])
        self.assertIn("You said: we work together", out["text"])
        self.assertIn("colleague", out["text"])
        self.assertTrue(out["notThought"], "kept as made, not read into lessons")
        self.assertEqual(out["restores"], [{"action": "restore", "id": "p9",
                                            "reason": "back at work"}])
        self.assertEqual(out["deletes"], [{"kind": "connections", "id": "x1"}])

    def test_nothing_worked_out_yet(self) -> None:
        stub = self._stub()
        stub["/api/connections"] = dict(LIST, briefing=None)
        stub["/api/connections/briefing"] = {"briefing": None, "progress": LIST["progress"]}
        result = _render(self.engine, stub,
                         "await followKnowCard();await wait(300);"
                         "out.text=text(document.querySelector('#ov-know .paper-empty'));"
                         "out.title=text(document.querySelector('#ov-know .paper-title'));")
        self.assertEqual(result["err"], "")
        # Said, with when it will be.
        self.assertTrue(result["out"]["text"].startswith("No issue yet:"))
        self.assertIn("at the times set in Settings", result["out"]["text"])
        self.assertEqual(result["out"]["title"], "Customized Recommendation")


if __name__ == "__main__":
    unittest.main()
