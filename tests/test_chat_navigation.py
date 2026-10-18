"""The chat's sidebar: projects, every past conversation, reopening one.

Ported from PR #4 (by daniela-miaut). Added here: a reopened conversation is
drawn with each reply's id, so the follow-ups asked about it come back - in
the margin beside the words they were about.
"""

from __future__ import annotations

import html
import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from support import ROOT, TemporaryAgent
from personalized_agent.agent.dialog.store import ConversationStore
from personalized_agent.api.errors import ApiError
from personalized_agent.api.routes import build_routes
from personalized_agent.api.server import Request
from personalized_agent.data_manage.storage.database import Database
from personalized_agent.models.base import ModelResponse
from test_chat_debugger_ui import _browser, _run_browser_probe

WEB = ROOT / "personalized_agent" / "api" / "web"


class ChatProjectTests(unittest.TestCase):
    def setUp(self):
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.db = self.app.db
        self.store = self.app.conversations
        self.routes = build_routes(self.app)

    def tearDown(self):
        self.agent.cleanup()

    def call(self, method, path, **body):
        return self.routes[(method, path)](Request(method, path, {}, body, {}))

    def test_projects_and_membership_survive_reopening(self):
        project = self.call("POST", "/api/chat/projects", name=" Research ")
        conversation = self.call("POST", "/api/conversations", project_id=project["id"])
        self.store.add_message(conversation["id"], "user", "Plan the experiment")
        path = self.app.paths.database_file
        self.db.close()
        reopened = Database(path)
        try:
            store = ConversationStore(reopened)
            self.assertEqual(store.projects()[0]["name"], "Research")
            listed = store.conversations()[0]
            self.assertEqual(listed["project_id"], project["id"])
            self.assertEqual(listed["title"], "Plan the experiment")
            self.assertEqual(store.messages(listed["id"])[0]["content"], "Plan the experiment")
        finally:
            reopened.close()

    def test_history_endpoints_include_all_chats_and_all_messages(self):
        for index in range(35):
            self.store.create(str(index))
        conversation = self.store.create()
        for index in range(65):
            self.store.add_message(conversation, "user", f"Message {index}")
        self.assertEqual(len(self.call("GET", "/api/conversations")["conversations"]), 36)
        messages = self.call("GET", "/api/conversation", id=conversation)["messages"]
        self.assertEqual(len(messages), 65)
        self.assertEqual(messages[-1]["content"], "Message 64")

    def test_titles_and_recency_follow_messages(self):
        older = self.store.create()
        newer = self.store.create("Explicit title")
        self.store.add_message(newer, "user", "Ignored as title")
        self.store.add_message(older, "user", "First question")
        self.assertEqual(self.store.conversations()[0]["id"], older)
        self.assertEqual(self.store.conversations()[0]["title"], "First question")
        self.assertEqual(self.store.conversations()[1]["title"], "Explicit title")

    def test_invalid_projects_and_missing_conversations_are_rejected(self):
        for name in (" ", "x" * 121, []):
            with self.subTest(name=name), self.assertRaises(ApiError):
                self.call("POST", "/api/chat/projects", name=name)
        for project_id in ("missing", []):
            with self.subTest(project=project_id), self.assertRaises(ApiError):
                self.call("POST", "/api/conversations", project_id=project_id)
        with self.assertRaises(ApiError) as error:
            self.call("GET", "/api/conversation", id="missing")
        self.assertEqual(error.exception.status, 404)
        self.assertEqual(self.store.conversations(), [])

    def test_a_reopened_conversation_carries_its_ids_and_follow_ups(self):
        """Everything the page needs to draw it again as it was left."""
        self.app.models.invoke = lambda request, role="primary": ModelResponse(
            text="It is a public data service.")
        said = self.app.agent.handle_message("Where do citations come from?")
        followup = self.app.agent.follow_up(said["message_id"], "index", "what is an index")
        shown = self.call("GET", "/api/conversation", id=said["conversation_id"])
        reply = shown["messages"][-1]
        self.assertEqual(reply["id"], said["message_id"])
        self.assertEqual(reply["role"], "assistant")
        self.assertEqual([one["id"] for one in shown["followups"]], [followup["id"]])
        self.assertEqual(shown["followups"][0]["message_id"], reply["id"])
        self.assertEqual(shown["followups"][0]["about"], "index")

    def test_continuing_a_reopened_conversation_gives_the_model_its_history(self):
        sent = []

        def invoke(request, role="primary"):
            sent.append(request)
            return ModelResponse(text=f"reply {len(sent)}")

        self.app.models.invoke = invoke
        first = self.app.agent.handle_message("Plan the experiment")
        # Reopened later from the sidebar: the same id is sent with the message.
        self.app.agent.handle_message("And the budget?", first["conversation_id"])
        self.assertEqual([m["content"] for m in sent[-1].messages],
                         ["Plan the experiment", "reply 1", "And the budget?"])


class LegacyDatabaseTests(unittest.TestCase):
    def test_legacy_database_preserves_chats_and_adds_project_column(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "chat.db"
            Database(path).close()
            with sqlite3.connect(path) as conn:
                conn.execute("DROP INDEX idx_conversations_project")
                conn.execute("ALTER TABLE conversations DROP COLUMN project_id")
                # The chat's own folder came later still (agent/dialog/files.py).
                conn.execute("ALTER TABLE conversations DROP COLUMN folder")
                # And pinning, later again (the chat's list, 2026-10-16).
                conn.execute("ALTER TABLE conversations DROP COLUMN pinned_at")
                conn.execute("INSERT INTO conversations VALUES ('legacy', '2020-01-01', 'Old chat')")
            conn.close()
            db = Database(path)
            try:
                store = ConversationStore(db)
                self.assertIsNone(store.conversation("legacy")["project_id"])
                self.assertEqual(store.folder_of("legacy"), "")
                self.assertEqual(store.conversations()[0]["title"], "Old chat")
                self.assertIsNone(store.conversations()[0]["pinned_at"])
                self.assertTrue(store.pin("legacy", True))
                project = store.create_project("New project")
                made = store.create(project_id=project["id"])
                self.assertEqual(store.conversation(made)["project_id"], project["id"])
            finally:
                db.close()


STUB = r"""
  window.__errors = [];
  window.addEventListener('error', e => __errors.push(e.message));
  window.addEventListener('unhandledrejection', e => __errors.push(String(e.reason)));
  history.replaceState({}, '', '?tab=chat&conversation=c1');
  window.__posts = [];
  const chats = Array.from({length:45}, (_, i) => ({id:'c'+(i+1), project_id:'p1', title:'Conversation '+(i+1)}));
  chats.push({id:'general', project_id:null, title:'General chat'});
  const CHAT = {
    '/api/chat/projects': (o) => o.method === 'POST'
      ? {id:'p2', name:'Second project'}
      : {projects:[{id:'p1', name:'Research'},{id:'p2', name:'Second project'}]},
    '/api/conversations': (o) => o.method === 'POST'
      ? {id:'new-project-chat', project_id:'p1'} : {conversations:chats},
  };
  window.__chatFetch = async (url, options={}) => {
    // Not against the page's own address: on Windows that is a file URL with
    // a drive letter, and "/api/x" resolves under it to "/C:/api/x".
    const parsed = new URL(url, 'http://agent.invalid');
    if (options.method === 'POST') __posts.push([parsed.pathname, JSON.parse(options.body || '{}')]);
    let data = null;
    if (CHAT[parsed.pathname]) data = CHAT[parsed.pathname](options);
    if (parsed.pathname === '/api/conversation') {
      const id = parsed.searchParams.get('id');
      if (id === 'c2') await new Promise(r => setTimeout(r, 80));
      data = {conversation_id:id, project_id:id==='general'?null:'p1', messages:[
        {id:'u_'+id, role:'user', content:'Message '+id, metadata:{}},
        {id:'a_'+id, role:'assistant', content:'Reply '+id+' cites the census API here', metadata:{}}],
        followups: id === 'c1' ? [{id:'f1', message_id:'a_c1', about:'census API',
          question:'what is census API', answer:'A public data service.'}] : []};
    }
    if (parsed.pathname === '/api/chat') {
      const payload = JSON.parse(options.body);
      chats.unshift({id:payload.conversation_id||'new-general',
        project_id:payload.conversation_id?'p1':null, title:payload.message});
      data = {conversation_id:payload.conversation_id||'new-general', message_id:'a_new',
        answer:'New reply', classification:'immediate', tool_calls:[], duration_seconds:0};
    }
    return data;
  };
  const realFetch = window.fetch;
  window.fetch = async (url, options={}) => {
    const data = await window.__chatFetch(url, options);
    return {ok:true, status:200, statusText:'OK', headers:{get:()=>null},
            json:async()=>data === null ? {} : data};
  };
"""

CHECKS = r"""
  window.addEventListener('load', async () => {
    const wait = (ms=40) => new Promise(r => setTimeout(r, ms));
    const assert = (condition, text) => {if(!condition) throw Error(text);};
    const find = s => document.querySelector(s);
    const mobile = matchMedia('(max-width:700px)').matches;
    const fullPage = Boolean(find('#tabs'));
    const open = () => {if(find('#chat-sidebar').hidden) find('[data-chat-sidebar-open]').click();};
    const report = {};
    try {
      await wait(160);
      assert(Boolean(find('[data-conversation="c1"][aria-current="page"]')), 'initial selection');
      assert(find('#chat-log').textContent.includes('Reply c1'), 'initial history');
      // Each reply drawn with its id.
      assert(Boolean(find('.msg.assistant[data-message="a_c1"]')), 'the reply keeps its id');
      {
        // The follow-up asked about it is back, beside the words it was about -
        // on both pages: one conversation, drawn by one script (chat.js).
        const card = find('.followup');
        assert(Boolean(card), 'the follow-up came back');
        assert(card.querySelector('.fu-body').textContent === 'A public data service.', 'its answer');
        const mark = find('mark.fu-anchor');
        assert(mark && mark.textContent === 'census API', 'its words are marked again');
        const log = find('#chat-log');
        report.margin = log.classList.contains('fu-margin-mode');
        const reply = find('.msg.assistant .msg-text').getBoundingClientRect();
        const box = card.getBoundingClientRect();
        report.beside = box.right < reply.left;
        report.atWords = Math.abs(box.top - mark.getBoundingClientRect().top) < 3;
        report.under = box.top >= reply.bottom - 1;
      }
      open();
      const scroll = find('.chat-sidebar-scroll');
      assert(scroll.scrollHeight > scroll.clientHeight, 'history must scroll');
      if (mobile) {
        assert(find('#chat-sidebar').getAttribute('aria-modal')==='true', 'mobile modal');
        assert(find('.chat-workspace').inert, 'background disabled');
        document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}));
        assert(find('#chat-sidebar').hidden, 'escape closes drawer');
        open();
        find('.chat-sidebar-backdrop').click();
        assert(find('#chat-sidebar').hidden, 'backdrop closes drawer');
        open();
      } else {
        find('[data-close]').click();
        assert(find('#chat-sidebar').hidden, 'desktop collapse');
        assert(localStorage.getItem('chat-sidebar-collapsed')==='true', 'collapse persistence');
        open();
      }
      find('[data-conversation="c2"]').click();
      find('[data-conversation="c1"]').click();
      await wait(160);
      assert(find('#chat-log').textContent.includes('Reply c1'), 'latest selection wins');
      open();
      find('[data-conversation="c2"]').click();
      await wait(160);
      assert(find('#chat-log').textContent.includes('Reply c2'), 'conversation switch');
      assert(!find('.followup'), 'another conversation shows none of its follow-ups');
      assert(new URLSearchParams(location.search).get('conversation')==='c2', 'conversation URL');
      // Chats only: no projects to file them under.
      assert(!find('.chat-projects') && !find('[data-add]') && !find('[data-project]'),
             'no projects');
      if (mobile) assert(find('#chat-sidebar').hidden, 'selection closes drawer');
      open();
      find('[data-new]').click();
      assert(!find('#chat-log').textContent, 'new chat clears log');
      assert(!new URLSearchParams(location.search).has('project'), 'no project in the address');
      find('#chat-input').value = 'A new question';
      find('#chat-form').dispatchEvent(new Event('submit',{cancelable:true}));
      report.lockedWhileSending = find('#chat-form button').disabled;
      await wait(120);
      report.unlockedAfter = !find('#chat-form button').disabled;
      assert(!__posts.some(p=>p[0]==='/api/conversations'), 'no conversation made in a project');
      assert(__posts.some(p=>p[0]==='/api/chat' && !p[1].conversation_id), 'a new chat');
      assert(Boolean(find('.msg.assistant[data-message="a_new"]')), 'a new reply keeps its id');
      report.errors = __errors;
      report.ok = true;
    } catch (error) { report.error = error.message; report.errors = window.__errors; }
    document.title = JSON.stringify(report);
  });
"""


class ChatNavigationUiTests(unittest.TestCase):
    def _page(self, filename: str) -> str:
        page = (WEB / filename).read_text(encoding="utf-8")
        page = page.replace('<link rel="stylesheet" href="/styles.css">',
                            "<style>" + (WEB / "styles.css").read_text(encoding="utf-8")
                            + "</style>")
        page = page.replace('<script src="/chat-navigation.js"></script>',
                            "<script>" + STUB + (WEB / "chat-navigation.js").read_text(
                                encoding="utf-8") + "</script>")
        # The conversation itself, the same script on both pages.
        page = page.replace('<script src="/chat.js"></script>',
                            "<script>" + (WEB / "chat.js").read_text(encoding="utf-8")
                            + "</script>")
        if filename == "index.html":
            page = page.replace('<script src="/app.js"></script>',
                                "<script>" + (WEB / "app.js").read_text(encoding="utf-8")
                                + "</script>")
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "", page)
        return page.replace("</body>", "<script>" + CHECKS + "</script></body>")

    def _run(self, filename: str, mobile: bool) -> dict:
        engine = _browser()
        if not engine:
            self.skipTest("a browser is needed to draw the page")
        stdout, stderr = _run_browser_probe(
            engine, self._page(filename), size="390,844" if mobile else "1400,900",
            budget=6000)
        title = re.search(r"<title>(.*?)</title>", stdout, re.S)
        self.assertIsNotNone(title, stderr[-1500:])
        return json.loads(html.unescape(title.group(1)))

    def test_both_pages_support_history_and_responsive_navigation(self):
        for filename in ("index.html", "chat.html"):
            for mobile in (False, True):
                with self.subTest(page=filename, mobile=mobile):
                    report = self._run(filename, mobile)
                    self.assertNotIn("error", report, report)
                    self.assertTrue(report["ok"])
                    self.assertTrue(report["lockedWhileSending"], "Send was not locked")
                    self.assertTrue(report["unlockedAfter"], "Send stayed locked")
                    # The whole interface also draws its other tabs from the
                    # same stub, and the calendar's has no dates to draw;
                    # nothing else may fail.
                    self.assertEqual([one for one in report["errors"]
                                      if "getFullYear" not in one], [])

    def test_a_reopened_conversation_brings_its_follow_ups_back_to_the_margin(self):
        # In the Chat tab and in the conversation window alike.
        for filename in ("index.html", "chat.html"):
            with self.subTest(page=filename):
                report = self._run(filename, mobile=False)
                self.assertNotIn("error", report, report)
                self.assertTrue(report["margin"], "the chat was not wide enough for a margin")
                self.assertTrue(report["beside"], "the card is not to the right of the reply")
                self.assertTrue(report["atWords"], "the card is not at the height of its words")

    def test_and_under_the_reply_where_there_is_no_room(self):
        for filename in ("index.html", "chat.html"):
            with self.subTest(page=filename):
                report = self._run(filename, mobile=True)
                self.assertNotIn("error", report, report)
                self.assertFalse(report["margin"])
                self.assertTrue(report["under"])


if __name__ == "__main__":
    unittest.main()
