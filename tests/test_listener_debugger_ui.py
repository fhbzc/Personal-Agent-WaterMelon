"""Exercise filtering, inspection, escaping and refresh state in a browser."""
import html
import json
import re
import shutil
import subprocess
import unittest

from test_chat_debugger_ui import WEB, _browser, _run_browser_probe


class ListenerDebuggerUiTests(unittest.TestCase):
    def test_tab_wiring_and_syntax(self):
        page = (WEB / "index.html").read_text(encoding="utf-8")
        app = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn('data-tab="listenerdebug"', page)
        self.assertIn('src="/listener-debugger.js"', page)
        self.assertIn("window.listenerDebuggerPanel.activate()", app)
        self.assertIn("window.listenerDebuggerPanel.deactivate()", app)
        hiding = app.split("async function applyDebuggerAvailability", 1)[1].split("\n}", 1)[0]
        self.assertIn('[data-tab="listenerdebug"]', hiding)
        node = shutil.which("node")
        if node:
            done = subprocess.run([node, "--check", str(WEB / "listener-debugger.js")], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)

    def test_filters_keep_open_record_and_escape_untrusted_content(self):
        engine = _browser()
        if not engine: self.skipTest("browser unavailable")
        data = {"monitoring":True, "running":True, "generated_at":"2026-10-20T00:00:00Z",
                "window":{"hours":24,"sample_events":2,"sample_limited":False},
                "settings":{"enabled":True,"screen_enabled":True,"screen_interval_seconds":15,"screen_max_chars":4000,"screenshots_enabled":True,"screenshots_per_day":80,"wechat_immediate_write":True,"focus_check_seconds":1,
                            "fast":{"fastest_seconds":3,"slower":[[30,5]],"cpu_budget_percent":25,"battery_floor_seconds":5,"on_battery":False,"over_budget":False,"in_front":True,"looks":4,"looks_unchanged":3,"held_by_budget":0}},
                "issues":[{"kind":"screenshot_not_persisted","source":"screenshot","taken":7},
                          {"kind":"screen_read_failed","application":"WeChat","at":"2026-10-20T00:00:00Z","message":"e5rtError <img src=x onerror=alert(1)>"}],
                "providers":[{"id":"screen","state":"degraded","status":{"readings":5},"errors":["fixture error"],"session_recorded":1,"stored":{"events":1,"last_event":"2026-10-20T00:00:00Z"},"routing":{"received":2,"filtered":1,"failed":0,"filter_reasons":{"directory_excluded":1},"last_received_at":None}}],
                "applications":[{"application":"WeChat","focus_events":2,"text_events":1,"characters":5,"last_text":"2026-10-20T00:00:00Z","methods":{"macos_vision_ocr":1},"long_focus_without_new_text":1,"at_character_limit":0,
                                 "reading":{"status":"failed","last_failure_at":"2026-10-20T00:00:00Z","last_error":"e5rtError <img src=x onerror=alert(1)>"}}],
                "events":[{"id":"event_fixture","application":"WeChat","event_type":"screen.text","source":"screen","timestamp":"2026-10-20T00:00:00Z","linked_states":1,"data_preview":"<img src=x onerror=alert(1)> actual visible text","preview_truncated":False}]}
        data['wechat']={'conversations':[{'id':'conv_fixture','peer_name':'<img src=x onerror=alert(1)>','messages':2,'captures':1,'identity_status':'provisional','last_seen_at':None}]}
        data['wechat']['diagnostics']=[{'observation_id':'obs1','conversation_id':None,'last_seen_at':None,'layout_status':'unrecognized','layout_reason':'no_readable_chat_bubbles','diagnostics':{'assigned_messages':0,'unknown_senders':0}}]
        data['applications'].append({**data['applications'][0],'application':'Weixin','reading':{'status':'ok','wechat_layout':{'status':'unrecognized','reason':'no_readable_chat_bubbles'}}})
        detail={'conversation':{'conversation_id':'conv_fixture'},'messages':[{'text':'<img src=x onerror=alert(1)> message'}],'captures':[{'capture_id':'cap1'}],'pagination':{'has_more':True,'next_before':'msg1'}}
        older={**detail,'messages':[{'text':'older message'}],'pagination':{'has_more':False,'next_before':None}}
        shell = (WEB / "index.html").read_text(encoding="utf-8").split('<section id="listenerdebug"', 1)[1].split('<section id="debugger"', 1)[0]
        harness = '<!doctype html><meta charset="utf-8"><title></title><section id="listenerdebug"' + shell
        harness += "<script>window.calls=[];window.fail=false;window.api={get:async p=>{calls.push(p);if(window.fail)throw new Error('network down');if(p.includes('wechat/conversation?')) return p.includes('before=')?" + json.dumps(older) + ":" + json.dumps(detail) + ";return " + json.dumps(data) + ";}};</script>"
        harness += "<script>" + (WEB / "listener-debugger.js").read_text(encoding="utf-8") + "</script>"
        harness += """<script>window.addEventListener('load',async()=>{
          let error='';const wait=()=>new Promise(r=>setTimeout(r,60));let out={};
          try{await listenerDebuggerPanel.activate();
            out.structureReading=document.querySelector('#ld-apps').textContent;
            document.querySelector('#ld-auto').checked=false;
            document.querySelector('#ld-events details').open=true;
            document.querySelector('#ld-application').value='WeChat';
            document.querySelector('#ld-application').dispatchEvent(new Event('change'));await wait();
            out.open=document.querySelector('#ld-events details').open;
            out.injected=Boolean(document.querySelector('#ld-events img'));
            out.body=document.querySelector('#ld-events').textContent;
            out.issues=document.querySelector('#ld-issues').textContent;
            out.wechat=document.querySelector('#ld-wechat').textContent;
            out.summary=document.querySelector('#ld-summary').textContent;
            out.reading=document.querySelector('#ld-apps').textContent;
            out.errorInjected=Boolean(document.querySelector('#ld-issues img, #ld-apps img'));
            document.querySelector('[data-ld-conversation]').click();await wait();
            out.json=JSON.parse(document.querySelector('[data-ld-json]').textContent).messages.length;
            out.chatInjected=Boolean(document.querySelector('#ld-wechat img,#ld-wechat-detail img'));
            document.querySelector('[data-ld-older]').click();await wait();
            out.older=JSON.parse(document.querySelector('[data-ld-json]').textContent).messages.length;
            out.noMore=document.querySelector('[data-ld-older]').hidden;
            out.download=Boolean(document.querySelector('[data-ld-download]'));
            window.fail=true;document.querySelector('#ld-refresh').click();await wait();
            out.errorShown=document.querySelector('#ld-status').textContent.includes('network down');
            out.enabled=!document.querySelector('#ld-refresh').disabled;
            listenerDebuggerPanel.deactivate();const n=calls.length;await listenerDebuggerPanel.activate();await wait();
            out.reopened=calls.length>n;listenerDebuggerPanel.deactivate();
          }catch(e){error=e.stack;}document.title=JSON.stringify({...out,error,calls});});</script>"""
        stdout, stderr = _run_browser_probe(engine, harness)
        match = re.search(r"<title>(.*?)</title>", stdout, re.S)
        self.assertIsNotNone(match, stderr[-1500:])
        report = json.loads(html.unescape(match.group(1)))
        self.assertEqual(report["error"], "")
        self.assertEqual(report['json'],1)
        self.assertEqual(report['older'],2)
        self.assertTrue(report['noMore'])
        self.assertTrue(report['download'])
        self.assertFalse(report['chatInjected'])
        self.assertTrue(report["open"])
        self.assertFalse(report["injected"])
        self.assertIn("actual visible text", report["body"])
        self.assertIn("7", report["issues"])
        self.assertIn('WeChat: its text could not be read', report['issues'])
        self.assertIn('WeChat: its text could not be read', report['reading'])
        self.assertIn('WeChat: its messages could not be told apart', report['structureReading'])
        self.assertIn('in none', report['wechat'])
        self.assertIn('no message bubble could be read', report['wechat'])
        self.assertIn('Conversation _fixture', report['wechat'])
        # Fast sampling, as the reader paces it.
        self.assertIn('Chats, mail, social media: every 3 s', report['summary'])
        self.assertIn('read only when the window changed; every 5 s after 30 s the same;', report['summary'])
        self.assertIn('within 25% of one core', report['summary'])
        self.assertIn('In front now.', report['summary'])
        self.assertIn("WeChat's new messages are stored as soon as they are read", report['summary'])
        self.assertIn('The window in front is checked every 1 s', report['summary'])
        self.assertFalse(report['errorInjected'])
        self.assertTrue(report["enabled"])
        self.assertTrue(report["errorShown"])
        self.assertTrue(report["reopened"])
        self.assertTrue(any("application=WeChat" in p for p in report["calls"]))
