"""Each conversation in the chat's list has its own menu - pin, rename,
delete - as in other chat applications. Deleted, it is asked once in the
row, and everything said in it goes with it; pinned, it sits above the rest;
renamed, it keeps the name it was given."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import TemporaryAgent
from test_panels import _javascript_engine

WEB = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"


class DeletedWithEverythingInItTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_gone_with_its_messages_summary_and_follow_ups(self) -> None:
        from personalized_agent.api.errors import ApiError
        from personalized_agent.api.routes import build_routes

        talk = self.app.conversations
        kept = talk.create(title="Planning the garden")
        gone = talk.create(title="A recipe for scones")
        talk.add_message(kept, "user", "Which seeds first?")
        talk.add_message(gone, "user", "How long do scones bake?")
        answer = talk.add_message(gone, "assistant", "About twelve minutes.")
        talk.add_followup(talk.message(answer["id"]), "", "And at what heat?", "Hot: 220 C.")
        talk.save_summary(gone, talk.messages(gone)[-1]["id"], 2, "Scones, briefly.")

        class _Req:
            body = {"id": gone}

            def require(self, key):
                return self.body[key]

        routes = build_routes(self.app)
        self.assertEqual(routes[("POST", "/api/conversation/delete")](_Req()), {"deleted": gone})
        self.assertIsNone(talk.conversation(gone))
        self.assertEqual(talk.messages(gone), [])
        self.assertIsNone(talk.summary(gone))
        db = self.app.db
        self.assertEqual(db.query("SELECT id FROM chat_followups WHERE conversation_id = ?",
                                  (gone,)), [])
        self.assertIsNotNone(talk.conversation(kept), "only that one")
        self.assertEqual(len(talk.messages(kept)), 1)
        with self.assertRaises(ApiError) as missing:
            routes[("POST", "/api/conversation/delete")](_Req())
        self.assertEqual(missing.exception.status, 404)


PAGE = """<!doctype html><html><head><meta charset='utf-8'></head><body>
<div class="chat-layout"><div class="chat-workspace">
<button data-chat-sidebar-open></button><span data-chat-title></span>
<input id="chat-input"></div></div>
<script>window.__posts=[];
const api={get:async(p)=>p==='/api/conversations'?{conversations:[{id:'c1',title:'Planning the garden'},{id:'c2',title:'A recipe for scones'}]}:{conversation_id:p.split('=')[1],messages:[]},
 post:async(p,b)=>{window.__posts.push({p,b});return {deleted:b.id};}};</script>
<script>%s</script>
<script>window.addEventListener('load',async()=>{let err='';const out={};
const wait=(ms)=>new Promise(r=>setTimeout(r,ms||150));
try{const nav=createChatNavigation({api,onOpen:()=>{}});await nav.activate();await wait();
const menus=[...document.querySelectorAll('.chat-nav-more')];out.crosses=menus.length;
menus[1].click();await wait();
out.items=[...document.querySelectorAll('.chat-nav-menu button')].map(b=>b.textContent);
[...document.querySelectorAll('.chat-nav-menu button')].find(b=>b.textContent==='Delete').click();await wait();
out.asked=(document.querySelector('.chat-nav-ask')||{}).textContent||'';
out.postedBefore=window.__posts.length;
[...document.querySelectorAll('.chat-nav-line.asking button')].find(b=>b.textContent==='Delete').click();await wait();
out.posted=window.__posts.map(x=>x.p+':'+x.b.id);
out.rows=[...document.querySelectorAll('button.chat-nav-row')].map(b=>b.textContent);
}catch(e){err=String(e&&e.stack?e.stack:e);}
document.title=JSON.stringify({err,out});});</script></body></html>"""


class PinnedAndRenamedTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_pinned_first_and_never_left_out(self) -> None:
        talk = self.app.conversations
        old = talk.create(title="An old one about seeds")
        for n in range(4):
            talk.add_message(talk.create(title=f"Newer {n}"), "user", "hello")
        self.assertTrue(talk.pin(old, True))
        listed = talk.conversations(limit=3)
        self.assertEqual(listed[0]["id"], old, "pinned at the top")
        self.assertTrue(listed[0]["pinned_at"])
        self.assertTrue(talk.pin(old, False))
        self.assertNotIn(old, [one["id"] for one in talk.conversations(limit=3)])
        self.assertFalse(talk.pin("no-such", True))

    def test_renamed_it_keeps_the_name(self) -> None:
        from personalized_agent.api.errors import ApiError
        from personalized_agent.api.routes import build_routes
        talk = self.app.conversations
        one = talk.create()
        talk.add_message(one, "user", "hello")
        self.assertEqual(talk.conversations()[0]["title"], "hello")

        class _Req:
            def __init__(self, body):
                self.body = body

            def require(self, key):
                return self.body[key]

            def get(self, key, default=None):
                return self.body.get(key, default)
        routes = build_routes(self.app)
        said = routes[("POST", "/api/conversation/rename")](_Req({"id": one, "title": "  Seeds  "}))
        self.assertEqual(said["title"], "Seeds")
        talk.add_message(one, "user", "and more")
        self.assertEqual(talk.conversations()[0]["title"], "Seeds")
        with self.assertRaises(ApiError):
            routes[("POST", "/api/conversation/rename")](_Req({"id": one, "title": "   "}))
        with self.assertRaises(ApiError) as missing:
            routes[("POST", "/api/conversation/pin")](_Req({"id": "nope"}))
        self.assertEqual(missing.exception.status, 404)
        routes[("POST", "/api/conversation/pin")](_Req({"id": one, "pinned": True}))
        self.assertTrue(talk.conversations()[0]["pinned_at"])


class TheMenuOnEachRowTests(unittest.TestCase):

    def test_asked_once_then_deleted(self) -> None:
        kind, engine = _javascript_engine()
        if kind != "chrome":
            self.skipTest("no browser available to render the page")
        script = (WEB / "chat-navigation.js").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as work:
            page = Path(work) / "chat.html"
            page.write_text(PAGE % script, encoding="utf-8")
            done = subprocess.run(
                [engine, "--headless=new", "--disable-extensions", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=10000",
                 "--dump-dom", page.as_uri()],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        said = json.loads(re.search(r"<title>(.*?)</title>", done.stdout, re.S).group(1)
                          .replace("&quot;", '"').replace("&amp;", "&"))
        self.assertEqual(said["err"], "")
        out = said["out"]
        self.assertEqual(out["crosses"], 2)
        # And it can go under a topic (the user, 2026-11-03, from PR #8).
        self.assertEqual(out["items"], ["Pin", "Rename", "Move to a new topic…", "Delete"])
        self.assertIn("cannot be brought back", out["asked"])
        self.assertEqual(out["postedBefore"], 0, "deleted before it was asked")
        self.assertEqual(out["posted"], ["/api/conversation/delete:c2"])
        self.assertEqual(out["rows"], ["Planning the garden"])


LIST_PAGE = """<!doctype html><html><head><meta charset='utf-8'>
<style>[hidden]{display:none!important}</style></head><body>
<div class="chat-layout"><div class="chat-workspace">
<button data-chat-sidebar-open></button><span data-chat-title></span>
<input id="chat-input"></div></div>
<script>
const said={conversations:[{id:'c1',title:'Loose'},{id:'c2',title:'Loose and pinned',pinned_at:'x'},
 {id:'c3',title:'In a topic',project_id:'t1'},{id:'c4',title:'In a topic and pinned',project_id:'t1',pinned_at:'x'}]};
const api={get:async(p)=>p==='/api/conversations'?said:p==='/api/chat/projects'?{projects:[{id:'t1',name:'Seeds'}]}
 :{conversation_id:p.split('=')[1],messages:[]},post:async()=>({})};</script>
<script>%s</script>
<script>window.addEventListener('load',async()=>{let err='';const out={};
const wait=()=>new Promise(r=>setTimeout(r,150));
const rows=(s)=>[...document.querySelectorAll(s+' button.chat-nav-row[data-conversation]')].map(b=>b.textContent);
const seen=(s)=>{const one=document.querySelector(s);return !!one&&one.offsetParent!==null;};
try{const nav=createChatNavigation({api,onOpen:()=>{}});await nav.activate();await wait();
out.pinnedList=!!document.querySelector('.chat-pinned');
out.foldedTopic=rows('.chat-topics');out.chats=rows('.chat-history');
document.querySelector('.chat-topic-name').click();await wait();
out.openTopic=rows('.chat-topics');
const label=(p)=>document.querySelector('[data-fold="'+p+'"]');
label('chats').click();await wait();
out.chatsFolded=[seen('.chat-history'),label('chats').getAttribute('aria-expanded'),seen('.chat-topics')];
out.kept=localStorage.getItem('chat-list-folded');
label('chats').click();label('topics').click();await wait();
out.topicsFolded=[seen('.chat-topics'),seen('.chat-history')];
document.querySelector('[data-new-topic]').click();await wait();
out.formShows=seen('.chat-topic-form');
}catch(e){err=String(e&&e.stack?e.stack:e);}
document.title=JSON.stringify({err,out});});</script></body></html>"""


class TheListsPartsTests(unittest.TestCase):

    def test_pins_stay_in_their_part_and_each_part_folds(self) -> None:
        kind, engine = _javascript_engine()
        if kind != "chrome":
            self.skipTest("no browser available to render the page")
        script = (WEB / "chat-navigation.js").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as work:
            page = Path(work) / "chat.html"
            page.write_text(LIST_PAGE % script, encoding="utf-8")
            done = subprocess.run(
                [engine, "--headless=new", "--disable-extensions", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=10000",
                 "--dump-dom", page.as_uri()],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        said = json.loads(re.search(r"<title>(.*?)</title>", done.stdout, re.S).group(1)
                          .replace("&quot;", '"').replace("&amp;", "&"))
        self.assertEqual(said["err"], "")
        out = said["out"]
        self.assertFalse(out["pinnedList"], "no list of pins of its own")
        self.assertEqual(out["chats"], ["Loose and pinned", "Loose"], "on top of the chats")
        self.assertEqual(out["foldedTopic"], ["In a topic and pinned"], "in sight, its topic folded")
        self.assertEqual(out["openTopic"], ["In a topic and pinned", "In a topic"])
        self.assertEqual(out["chatsFolded"], [False, "false", True])
        self.assertEqual(out["kept"], '["chats"]')
        self.assertEqual(out["topicsFolded"], [False, True])
        self.assertTrue(out["formShows"], "a new topic opens Topics")


if __name__ == "__main__":
    unittest.main()
