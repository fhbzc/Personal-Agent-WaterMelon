"""The chat debugger's tab, in a browser: the run flow and a reopened panel.

Ported from PR #4 (by daniela-miaut); the tab is `chatdebug` here, a tab of
its own beside the Experience and Heartbeat debuggers.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import ROOT

WEB = ROOT / "personalized_agent" / "api" / "web"


def _browser():
    """A headless-capable browser, Windows' own places first."""
    for candidate in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "google-chrome", "chromium", "chromium-browser", "msedge",
    ):
        found = candidate if os.path.exists(candidate) else shutil.which(candidate)
        if found:
            return found
    return None


def _run_browser_probe(engine: str, page: str, size: str = "1280,900",
                       budget: int = 3000):
    with tempfile.TemporaryDirectory() as work:
        probe = Path(work) / "probe.html"
        probe.write_text(page, encoding="utf-8")
        process = subprocess.Popen(
            [engine, "--headless=new", "--disable-gpu", "--no-first-run",
             f"--user-data-dir={work}/profile", f"--window-size={size}",
             f"--virtual-time-budget={budget}", "--dump-dom", probe.as_uri()],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace")
        try:
            return process.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            process.kill()
            return process.communicate()


ESCAPE = ("window.escapeHtml=s=>String(s).replace(/[&<>\\\"']/g,c=>"
          "({'&':'&amp;','<':'&lt;','>':'&gt;','\\\"':'&quot;',\"'\":'&#39;'}[c]));")


class ChatDebuggerUiTests(unittest.TestCase):
    def test_panel_is_registered_in_the_interface(self) -> None:
        page = (WEB / "index.html").read_text(encoding="utf-8")
        app = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn('data-tab="chatdebug"', page)
        self.assertIn('<section id="chatdebug"', page)
        self.assertIn('src="/chat-debugger.js"', page)
        self.assertIn("window.chatDebuggerPanel.activate()", app)
        self.assertIn("window.chatDebuggerPanel.deactivate()", app)
        # Its own tab, not a panel inside the Debugger's.
        debugger = page.split('<section id="debugger"', 1)[1].split("</section>", 1)[0]
        self.assertNotIn("chat-debugger", debugger)

    def test_a_build_without_the_debugger_has_no_chat_debugger(self) -> None:
        source = (WEB / "app.js").read_text(encoding="utf-8")
        hiding = source.split("async function applyDebuggerAvailability", 1)[1]
        hiding = hiding.split("\n}", 1)[0]
        self.assertIn('[data-tab="chatdebug"]', hiding)
        self.assertIn("#chatdebug", hiding)

    def test_the_page_opens_it(self) -> None:
        # A tab of the page (the avatar's menu keeps to Chat and Overview).
        page = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-tab="chatdebug"', page)
        self.assertIn('id="chatdebug"', page)
        # And the page opens the tab a "#name" asks for.
        self.assertIn("location.hash", (WEB / "app.js").read_text(encoding="utf-8"))

    def test_scripts_parse(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        for name in ("chat-debugger.js", "chat-navigation.js", "app.js"):
            with self.subTest(name):
                result = subprocess.run([node, "--check", str(WEB / name)],
                                        capture_output=True, text=True, timeout=120)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_run_action_loads_history_and_renders_execution_details(self) -> None:
        engine = _browser()
        if not engine:
            self.skipTest("a browser is needed to draw the panel")
        run = {
            "id": "run_1", "name": "A chat answers", "status": "passed",
            "duration_seconds": 0.12,
            "checks": [{"name": "has an answer", "passed": True}],
            "events": [
                {"sequence": 1, "kind": "instruction", "data": {"instruction": "hello"}},
                {"sequence": 2, "kind": "context", "data": {"history": ["note.md"]}},
                {"sequence": 3, "kind": "model_request",
                 "data": {"model": "stub", "system": "system + hello"}},
                {"sequence": 4, "kind": "completion", "data": {
                    "answer": "Hi there", "untrusted": "<img src=x onerror=alert(1)>"}},
            ],
            "result": {"answer": "Hi there", "conversation_id": "conv_ui"},
        }
        tests = [{"id": "chat.answer", "name": "A chat answers",
                  "description": "Send a message and check the reply."}]
        harness = (
            "<script>window.__calls=[];window.__runs=[];window.api={"
            "get:async p=>{window.__calls.push(['GET',p]);return "
            "p.startsWith('/api/debugger/chat/run?id=')?" + json.dumps(run)
            + ":{tests:" + json.dumps(tests) + ",runs:window.__runs}},"
            "post:async(p,b)=>{window.__calls.push(['POST',p,b]);"
            "window.__runs.unshift({id:'run_1',name:'A chat answers',status:'passed'});return "
            + json.dumps(run) + "}};" + ESCAPE
            + "</script><script>" + (WEB / "chat-debugger.js").read_text(encoding="utf-8")
            + "</script><script>window.addEventListener('load',async()=>{let error='';"
            "try{await window.chatDebuggerPanel.activate();"
            "document.querySelector('[data-chat-test=\"chat.answer\"]').click();"
            "const wait=()=>new Promise(r=>setTimeout(r,80));await wait();"
            "const prompt=document.querySelector('#chat-debugger-prompt');"
            "const cont=document.querySelector('#chat-debugger-continue');"
            "prompt.value='first custom';"
            "document.querySelector('#chat-debugger-run-custom').click();"
            "await wait();cont.checked=true;prompt.value='second custom';"
            "document.querySelector('#chat-debugger-run-custom').click();await wait();"
            "document.querySelector('#chat-debugger-run-suite').click();await wait();"
            "await wait();"
            "window.chatDebuggerPanel.deactivate();}catch(e){error=e.message;}"
            "const d=document.querySelector('#chat-debugger-detail');"
            "document.title=JSON.stringify({error:error,calls:window.__calls,"
            "testCount:document.querySelectorAll('[data-chat-test]').length,"
            "runCount:document.querySelectorAll('[data-chat-run]').length,"
            "status:document.querySelector('#chat-debugger-run-status').textContent,"
            "detail:d.textContent,injected:Boolean(d.querySelector('img'))});});</script>"
        )
        section = (WEB / "index.html").read_text(encoding="utf-8").split(
            '<section id="chatdebug"', 1)[1].split("</main>", 1)[0]
        shell = ('<!doctype html><meta charset="utf-8"><title></title>'
                 '<section id="chatdebug"' + section)
        stdout, stderr = _run_browser_probe(engine, shell + harness)
        match = re.search(r"<title>(.*?)</title>", stdout, re.S)
        self.assertIsNotNone(match, stderr[-1500:])
        report = json.loads(html.unescape(match.group(1)))
        self.assertEqual(report["error"], "")
        self.assertEqual(report["testCount"], 1)
        self.assertEqual(report["runCount"], 4)
        self.assertEqual(report["status"], "Ran 1: 1 passed, 0 failed")
        self.assertIn("system + hello", report["detail"])
        self.assertIn("note.md", report["detail"])
        self.assertIn("Hi there", report["detail"])
        self.assertIn("<img src=x onerror=alert(1)>", report["detail"])
        self.assertFalse(report["injected"], "event data was parsed as markup")
        self.assertTrue(any(call[0] == "GET" and call[1] == "/api/debugger/chat"
                            for call in report["calls"]))
        self.assertTrue(any(call[0] == "POST" and call[1] == "/api/debugger/chat/run"
                            and call[2] == {"test_id": "chat.answer"}
                            for call in report["calls"]))
        own = [call[2] for call in report["calls"]
               if call[0] == "POST" and call[1] == "/api/debugger/chat/run"
               and "message" in call[2]]
        self.assertEqual(own, [{"message": "first custom"},
                               {"message": "second custom", "conversation_id": "conv_ui"}])

    def test_reopened_panel_restores_saved_prompts_details_and_conversation(self) -> None:
        engine = _browser()
        if not engine:
            self.skipTest("a browser is needed to draw the panel")
        prompt = 'Remember café ☕\n<img src=x onerror="window.__injected=true">'
        own = {"id": "saved_custom", "test_id": "custom", "name": "Your own prompt",
               "status": "passed", "prompt": prompt, "conversation_id": "saved_conversation",
               "events": [], "result": {"answer": "Saved answer"}}
        older = {"id": "saved_older", "test_id": "chat.answer", "name": "Older test",
                 "status": "passed", "prompt": "An older prompt", "events": [],
                 "result": {"answer": "An older answer"}}
        section = (WEB / "index.html").read_text(encoding="utf-8").split(
            '<section id="chatdebug"', 1)[1].split("</main>", 1)[0]
        shell = ('<!doctype html><meta charset="utf-8"><title></title>'
                 '<section id="chatdebug"' + section)
        harness = (
            "<script>const saved=" + json.dumps([own, older]) + ";window.__posts=[];"
            "window.api={get:async p=>p.startsWith('/api/debugger/chat/run?id=')?"
            "saved.find(r=>r.id===decodeURIComponent(p.split('id=')[1])):"
            "{tests:[],runs:saved.map(({events,result,...summary})=>summary)},"
            "post:async(p,b)=>{window.__posts.push(b);return saved[0]}};" + ESCAPE
            + "</script><script>" + (WEB / "chat-debugger.js").read_text(encoding="utf-8")
            + "</script><script>"
            "window.addEventListener('load',async()=>{const report={};"
            "try{await window.chatDebuggerPanel.activate();"
            "const detail=document.querySelector('#chat-debugger-detail');"
            "const runs=document.querySelector('#chat-debugger-runs');"
            "const cont=document.querySelector('#chat-debugger-continue');"
            "report.prompts=Array.from(runs.querySelectorAll('.chat-debugger-run-prompt'))"
            ".map(e=>e.textContent);"
            "report.restoredDetail=detail.textContent;report.continueEnabled=!cont.disabled;"
            "report.injected=Boolean(document.querySelector('#chatdebug img'))"
            "||Boolean(window.__injected);"
            "const wait=()=>new Promise(r=>setTimeout(r,80));"
            "document.querySelector('[data-chat-run=saved_older]').click();await wait();"
            "window.chatDebuggerPanel.deactivate();await window.chatDebuggerPanel.activate();"
            "report.selectedAfterReopen=document.querySelector('[data-chat-run].selected')"
            ".dataset.chatRun;"
            "report.reopenedDetail=detail.textContent;"
            "cont.checked=true;document.querySelector('#chat-debugger-prompt').value='Follow up';"
            "document.querySelector('#chat-debugger-run-custom').click();await wait();"
            "report.posts=window.__posts;"
            "report.runCount=runs.querySelectorAll('[data-chat-run]').length;"
            "}catch(e){report.error=e.message;}finally{window.chatDebuggerPanel.deactivate();}"
            "document.title=JSON.stringify(report);});</script>"
        )
        stdout, stderr = _run_browser_probe(engine, shell + harness)
        match = re.search(r"<title>(.*?)</title>", stdout, re.S)
        self.assertIsNotNone(match, stderr[-1500:])
        report = json.loads(html.unescape(match.group(1)))
        self.assertNotIn("error", report)
        self.assertEqual(report["prompts"], [prompt, "An older prompt"])
        self.assertIn(prompt, report["restoredDetail"])
        self.assertIn("Saved answer", report["restoredDetail"])
        self.assertTrue(report["continueEnabled"])
        self.assertFalse(report["injected"])
        self.assertEqual(report["selectedAfterReopen"], "saved_older")
        self.assertIn("An older answer", report["reopenedDetail"])
        self.assertEqual(report["runCount"], 2)
        self.assertEqual(report["posts"], [{"message": "Follow up",
                                            "conversation_id": "saved_conversation"}])


if __name__ == "__main__":
    unittest.main()
