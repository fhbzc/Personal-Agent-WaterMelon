"""Settings in three views - General: the model in use and its connections
(the APIs and the command-line and desktop clients); Display: the language,
the avatar and the size of the text; Others: every other setting - and the
interface in the user's language.

Rendered in a real browser with the page's own script and fetch stubbed:
the views switch; the providers are grouped by kind; an API's address, the
page where its key is made and the warning that it is billed by the token
show with it; the address is sent with the key; the language, the avatar
and the text sizes are in Display, the rest in Others. With the interface in
Chinese, the application's own words are Chinese - and what the user or the
agent wrote is not touched."""

from __future__ import annotations

import html
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_panels import _javascript_engine

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "personalized_agent" / "api" / "web"

MODELS = {
    "roles": {"primary": {"role": "primary", "provider": "claude_code", "model": "opus",
                          "label": "claude_code/opus", "is_override": False,
                          "availability": None}},
    "providers": [
        {"provider": "offline", "display_name": "Offline", "kind": "offline",
         "authentication_methods": ["none"], "authentication_method_details": [],
         "auth_status": {"state": "authenticated", "detail": ""}, "models": [],
         "usage": {"total_tokens": 0}},
        {"provider": "claude_code", "display_name": "Claude Code (local)", "kind": "client",
         "authentication_methods": ["none"], "authentication_method_details": [],
         "auth_status": {"state": "authenticated", "detail": "signed in"},
         "models": [{"id": "claude-opus-5-5", "display_name": "Claude Opus 5.5",
                     "reasoning_efforts": ["low", "medium", "high", "xhigh", "max"]}],
         "usage": {"total_tokens": 12}},
        {"provider": "anthropic", "display_name": "Anthropic", "kind": "api",
         "authentication_methods": ["api_key"], "authentication_method_details": [],
         "auth_status": {"state": "not_configured", "detail": ""}, "models": [],
         "usage": {"total_tokens": 0}},
        {"provider": "openrouter", "display_name": "OpenRouter", "kind": "api",
         "authentication_methods": ["api_key"],
         "authentication_method_details": [{"method": "api_key", "available": True}],
         "auth_status": {"state": "not_configured", "detail": ""},
         "models": [{"id": "anthropic/claude-sonnet-4.5", "display_name": "Claude Sonnet 4.5"}],
         "usage": {"total_tokens": 0}, "base_url": "https://openrouter.ai/api/v1",
         "default_base_url": "https://openrouter.ai/api/v1", "needs_base_url": False,
         "key_page": "https://openrouter.ai/keys", "note": "One key for many.",
         "caching": "marked", "billed_per_token": True},
        {"provider": "custom_openai", "display_name": "Other OpenAI-compatible API",
         "kind": "api", "authentication_methods": ["api_key"],
         "authentication_method_details": [],
         "auth_status": {"state": "not_configured", "detail": ""}, "models": [],
         "usage": {"total_tokens": 0}, "base_url": "", "default_base_url": "",
         "needs_base_url": True, "key_page": "", "note": "", "caching": "unknown"},
    ],
    "chain": [{"provider": "claude_code", "model": "opus", "label": "claude_code/opus",
               "display_name": "Claude Code (local)", "kind": "client", "role": "primary",
               "is_override": False, "auth_status": {"state": "authenticated"},
               "availability": None, "effort": "high",
               "efforts": ["low", "medium", "high", "xhigh", "max"]}],
    "answered": "claude_code/opus",
    "credential_backend": {"backend": "test"},
    "fallback_enabled": False, "fallback_target": "", "checked_at": None,
}

SETTINGS = {
    "values": {"interface_language": "zh", "display_language": "zh",
               "heartbeat_interval_seconds": 900, "state_heartbeat_every_hours": 6,
               "listener_screen_enabled": True, "start_at_login": True,
               "primary_model": "opus", "agent_name": "Agent"},
    "schema": [
        {"key": "interface_language", "label": "Interface language", "type": "choice",
         "choices": ["en", "zh"], "section": "General", "description": "Its screens.",
         "hidden": True},
        {"key": "display_language", "label": "Display language", "type": "choice",
         "choices": ["en", "zh"], "section": "General", "description": "What you see."},
        {"key": "agent_name", "label": "Agent name", "type": "string", "section": "General",
         "description": "Its name."},
        {"key": "font_size", "label": "Font size", "type": "int", "section": "General",
         "description": "Its text."},
        {"key": "character", "label": "Character / avatar", "type": "string",
         "section": "General", "description": "Its look."},
        {"key": "heartbeat_interval_seconds", "label": "How often the agent wakes",
         "type": "int", "section": "Heartbeat", "description": "Its review."},
        {"key": "start_at_login", "label": "Start when I sign in", "type": "bool",
         "section": "General", "description": "Started when you sign in."},
        {"key": "listener_screen_enabled", "label": "Read what is on the screen",
         "type": "bool", "section": "Listener", "description": "The text of the window in front."},
        {"key": "state_heartbeat_every_hours", "label": "How often states are grouped",
         "type": "float", "section": "Heartbeat", "description": "Its grouping."},
        {"key": "primary_model", "label": "Primary model", "type": "string",
         "section": "Models", "description": "The model."},
        {"key": "log_level", "label": "Log level", "type": "string", "section": "System",
         "description": "How much."}],
    "choices": {}, "choice_notes": {},
    "choice_labels": {"display_language": {"zh": "Chinese (中文)", "en": "English"}},
}


OCR = {"engine": "Windows OCR", "available": True, "downloading": {}, "languages": [
    {"code": "en", "name": "English", "installed": True, "can_add": False},
    {"code": "zh", "name": "Chinese", "installed": False, "can_add": True},
    {"code": "es", "name": "Spanish", "installed": False, "can_add": True},
    {"code": "ja", "name": "Japanese", "installed": False, "can_add": True},
    {"code": "ko", "name": "Korean", "installed": False, "can_add": True}]}


def _modules():
    from personalized_agent.common import modules
    spent = {"calls": 3, "total_tokens": 12000, "estimated_cost": 0.05, "by": "cost",
             "of_automatic": 40.0, "of_all": 30.0}
    before = {"calls": 1, "total_tokens": 900, "estimated_cost": 0.07, "by": "cost",
              "of_automatic": 58.3, "of_all": 41.2}
    return dict(modules.described(None, {"calendar": spent,
                                          "heartbeat (before modules)": before}), hours=72)


MODULES = _modules()


def _render(engine, steps, with_i18n=False):
    page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                  (WEB / "index.html").read_text(encoding="utf-8"))
    stub = {"/api/settings": SETTINGS, "/api/models": MODELS,
            "/api/settings/defaults": {"user_defaults": {}},
            "/api/models/authenticate": {"provider": "openrouter",
                                         "auth_status": {"state": "authenticated"}},
            "/api/ocr": OCR,
            "/api/modules": MODULES,
            "/api/ocr/add": dict(OCR, started=True)}
    if with_i18n:
        # The interface's table for the display language (i18n.js asks for it).
        stub["/lang/zh.json"] = json.loads((WEB / "lang" / "zh.json").read_text(encoding="utf-8"))
    harness = (
        "<style>" + (WEB / "styles.css").read_text(encoding="utf-8") + "</style>"
        "<script>window.__posts=[];const STUB=" + json.dumps(stub) + ";"
        "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
        "if(o&&o.method==='POST')window.__posts.push({p,body:JSON.parse(o.body||'{}')});"
        "return Promise.resolve({ok:true,status:200,statusText:'OK',"
        "headers:{get:()=>null},json:()=>Promise.resolve(STUB[p]||{})});};</script>"
        + ("<script>" + (WEB / "i18n.js").read_text(encoding="utf-8") + "</script>"
           if with_i18n else "")
        + "<script>" + (WEB / "app.js").read_text(encoding="utf-8") + "</script>"
        "<script>window.addEventListener('load',async()=>{let err='';const out={};"
        "const wait=(ms)=>new Promise(r=>setTimeout(r,ms||150));"
        "const click=(el)=>el.dispatchEvent(new MouseEvent('click',{bubbles:true}));"
        "const text=(el)=>el?el.textContent.replace(/\\s+/g,' ').trim():'';"
        "try{" + steps + "}catch(e){err=String(e&&e.stack?e.stack:e);}"
        "document.title=JSON.stringify({err,out});});</script>")
    with tempfile.TemporaryDirectory() as work:
        probe = Path(work) / "probe.html"
        probe.write_text(page + harness, encoding="utf-8")
        completed = subprocess.run(
            [engine, "--headless=new", "--disable-gpu", "--no-first-run",
             f"--user-data-dir={work}/profile", "--virtual-time-budget=10000",
             "--window-size=1300,900", "--dump-dom", probe.as_uri()],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    match = re.search(r"<title>(.*?)</title>", completed.stdout or "", re.S)
    assert match, "the page never finished rendering"
    return json.loads(html.unescape(match.group(1)))


class SettingsViewsTests(unittest.TestCase):

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def test_general_and_others(self) -> None:
        steps = (
            "click(document.querySelector('#tabs button[data-tab=\"settings\"]'));"
            "await loadSettings();await wait();"
            "const shown=()=>[...document.querySelectorAll('[data-settings-pane]')]"
            "  .filter(p=>!p.hidden).map(p=>p.dataset.settingsPane);"
            "out.first=shown();"
            "out.general=text(document.querySelector('[data-settings-pane=\"general\"]'));"
            "out.display=text(document.querySelector('#settings-form-display'));"
            "out.avatar=text(document.querySelector('#settings-form-avatar'));"
            "out.languages=[...document.querySelectorAll("
            "'#settings-form-display select option')].map(o=>o.textContent);"
            "out.avatarEditor=!document.querySelector('#avatar-editor')"
            "  ||document.querySelector('#avatar-editor').hidden;"
            "out.others=text(document.querySelector('#settings-form'));"
            "out.kinds=[...document.querySelectorAll('#model-register-panel tr.provider-kind')].map(text);"
            # The list, in order: each model, how it stands.
            "out.chain=[...document.querySelectorAll('#model-chain li')].map(text);"
            # Add: hidden until asked for; what it is, then which one.
            "out.addHidden=document.querySelector('#auth-form').hidden;"
            "click(document.querySelector('#model-add-open'));await wait();"
            "out.addShown=!document.querySelector('#auth-form').hidden;"
            "const select=document.querySelector('#auth-form select[name=\"provider\"]');"
            "const options=()=>[...select.options].map(o=>o.value);"
            "out.apis=options();"
            "const client=document.querySelector('#auth-form input[name=\"kind\"][value=\"client\"]');"
            "client.checked=true;client.dispatchEvent(new Event('change'));await wait();"
            "out.services=options();"
            "select.value='claude_code';select.dispatchEvent(new Event('change'));await wait();"
            "out.clientNoAddress=document.querySelector('.base-url-field').hidden;"
            "out.clientNoBilling=document.querySelector('#api-billing').hidden;"
            "out.clientState=text(document.querySelector('#model-add-state'));"
            # Its model's levels of thinking, to choose from as it is added.
            "out.effortShown=!document.querySelector('.model-effort-field').hidden;"
            "out.effortLevels=[...document.querySelectorAll('#auth-form select[name=\"effort\"] option')]"
            "  .map(o=>o.value);"
            "out.rowEffort=document.querySelector('[data-chain-effort=\"0\"]').value;"
            "const api=document.querySelector('#auth-form input[name=\"kind\"][value=\"api\"]');"
            "api.checked=true;api.dispatchEvent(new Event('change'));await wait();"
            "select.value='anthropic';select.dispatchEvent(new Event('change'));await wait();"
            "out.noKeyModels=text(document.querySelector('#auth-form select[name=\"model\"]'));"
            "out.noKeyButton=text(document.querySelector('#auth-submit'));"
            "select.value='custom_openai';select.dispatchEvent(new Event('change'));await wait();"
            "out.customNeedsAddress=document.querySelector('#auth-form input[name=\"base_url\"]').required;"
            "select.value='openrouter';select.dispatchEvent(new Event('change'));await wait();"
            "out.addressShown=!document.querySelector('.base-url-field').hidden;"
            "out.addressHolder=document.querySelector('#auth-form input[name=\"base_url\"]').placeholder;"
            "out.keyNote=text(document.querySelector('#provider-key-note'));"
            "out.keyLink=document.querySelector('#provider-key-note a').href;"
            "out.billing=text(document.querySelector('#api-billing'));"
            "out.model=document.querySelector('#auth-form select[name=\"model\"]').value;"
            "out.modelTyped=!!document.querySelector('#auth-form input[name=\"model\"]');"
            "out.button=text(document.querySelector('#auth-submit'));"
            "document.querySelector('#auth-form input[name=\"api_key\"]').value='sk-test';"
            "document.querySelector('#auth-form').requestSubmit();await wait(400);"
            "out.sent=window.__posts.filter(x=>x.p==='/api/models/authenticate').map(x=>x.body);"
            "out.added=window.__posts.filter(x=>x.p==='/api/models/chain').map(x=>x.body);"
            # An API below a service on a subscription: the order's cost, said.
            "const two=[STUB['/api/models'].chain[0],{provider:'openrouter',model:'x',"
            "  label:'openrouter/x',display_name:'OpenRouter',kind:'api',"
            "  auth_status:{state:'authenticated'},availability:null}];"
            "modelsNow=Object.assign({},STUB['/api/models'],{chain:two});renderModelChain(modelsNow);"
            "out.cost=text(document.querySelector('#model-chain-cost'));"
            "out.costShown=!document.querySelector('#model-chain-cost').hidden;"
            "click(document.querySelector('[data-chain-down=\"0\"]'));await wait(300);"
            "out.moved=window.__posts.filter(x=>x.p==='/api/models/chain').slice(-1)[0].body;"
            "click(document.querySelector('[data-settings-view=\"others\"]'));await wait();"
            "out.second=shown();"
            "out.remembered=localStorage.getItem('settings-view');"
            # From a run waiting on its model: General, at the provider.
            "await openModelSettings('anthropic');await wait();"
            "out.third=shown();"
            "out.chosen=document.querySelector('#auth-form select[name=\"provider\"]').value;"
            "out.formOpen=!document.querySelector('#auth-form').hidden;")
        result = _render(self.engine, steps)
        self.assertEqual(result["err"], "")
        out = result["out"]
        self.assertEqual(out["first"], ["general"])
        # Display: its language and the size of its text.
        for said in ("Display language", "Language", "Font size"):
            self.assertIn(said, out["display"])
        for gone in ("Interface language", "Agent name", "Primary model",
                     "Character / avatar"):
            self.assertNotIn(gone, out["display"])
        # Avatar: which one, and what it is called - a view of its own.
        for said in ("Character / avatar", "Agent name"):
            self.assertIn(said, out["avatar"])
        # General: the models - no settings rows of its own.
        self.assertNotIn("Display language", out["general"])
        # Every other setting - the models' others too - in Others.
        for said in ("Log level", "Primary model"):
            self.assertIn(said, out["others"])
        for gone in ("Display language", "Font size", "Character / avatar", "Agent name"):
            self.assertNotIn(gone, out["others"])
        # Each language by its full name.
        self.assertEqual(out["languages"][:2], ["English", "Chinese (中文)"])
        # "Customize avatar" is off the page for now.
        self.assertTrue(out["avatarEditor"])
        self.assertEqual(out["kinds"], ["Command-line and desktop clients", "APIs", "Offline"])
        self.assertEqual(len(out["chain"]), 1)
        self.assertIn("Claude Code (local)", out["chain"][0])
        self.assertIn("answering", out["chain"][0])
        self.assertTrue(out["addHidden"])
        self.assertTrue(out["addShown"])
        self.assertEqual(out["apis"], ["anthropic", "openrouter", "custom_openai"])
        self.assertEqual(out["services"], ["offline", "claude_code"])
        self.assertTrue(out["clientNoAddress"])
        self.assertTrue(out["clientNoBilling"])
        self.assertIn("Ready", out["clientState"])
        self.assertTrue(out["effortShown"])
        self.assertEqual(out["effortLevels"], ["", "low", "medium", "high", "xhigh", "max"])
        self.assertEqual(out["rowEffort"], "high")
        self.assertTrue(out["customNeedsAddress"])
        self.assertTrue(out["addressShown"])
        self.assertEqual(out["addressHolder"], "https://openrouter.ai/api/v1")
        self.assertIn("Get a OpenRouter key", out["keyNote"])
        self.assertEqual(out["keyLink"], "https://openrouter.ai/keys")
        self.assertIn("Billed by the token", out["billing"])
        self.assertIn("Where you put it on the list matters", out["billing"])
        # The model is chosen from the provider's list, not typed.
        self.assertEqual(out["model"], "anthropic/claude-sonnet-4.5")
        self.assertFalse(out["modelTyped"])
        self.assertEqual(out["button"], "Connect and add")
        # An API that lists its models only once it has a key: connect first.
        self.assertIn("connect it first", out["noKeyModels"])
        self.assertEqual(out["noKeyButton"], "Connect")
        self.assertEqual(out["sent"], [{"provider": "openrouter", "method": "api_key",
                                        "api_key": "sk-test", "base_url": ""}])
        # Connected, then put at the end of the list.
        self.assertEqual(out["added"], [{"chain": [
            {"provider": "claude_code", "model": "opus", "effort": "high"},
            {"provider": "openrouter", "model": "anthropic/claude-sonnet-4.5"}]}])
        self.assertTrue(out["costShown"])
        self.assertIn("Mind the order", out["cost"])
        self.assertIn("is billed by the token", out["cost"])
        self.assertEqual(out["moved"], {"chain": [{"provider": "openrouter", "model": "x"},
                                                  {"provider": "claude_code", "model": "opus",
                                                   "effort": "high"}]})
        self.assertEqual(out["second"], ["others"])
        self.assertEqual(out["remembered"], "others")
        self.assertEqual(out["third"], ["general"])
        self.assertEqual(out["chosen"], "anthropic")
        self.assertTrue(out["formOpen"])

    def test_changed_in_place_saved_with_save_and_the_heartbeats_in_minutes(self) -> None:
        """No Edit: each value changed where it stands. Save appears once one
        is changed - with the first letter typed - and nothing is sent until
        it is pressed; then it goes again. The heartbeats in minutes."""
        steps = (
            "click(document.querySelector('#tabs button[data-tab=\"settings\"]'));"
            "await loadSettings();await wait();"
            "const card=document.querySelector('#settings-form-heartbeats');"
            "const bar=()=>card.querySelector('[data-save-bar]');"
            "out.beats=text(card);"
            "const boxes=[...card.querySelectorAll('input')];"
            "out.values=boxes.map(b=>b.value);"
            "out.open=boxes.every(b=>!b.disabled);"
            "out.edit=!!document.querySelector('[data-settings-edit]');"
            "out.saveBefore=!bar().hidden;"
            "boxes[0].value='10';boxes[0].dispatchEvent(new Event('input',{bubbles:true}));"
            "out.saveAfter=!bar().hidden;"
            "out.unsent=window.__posts.filter(x=>x.p==='/api/settings').length;"
            "click(document.querySelector('[data-settings-save=\"settings-form-heartbeats\"]'));"
            "await wait();"
            "out.sent=window.__posts.filter(x=>x.p==='/api/settings').map(x=>x.body);"
            "out.saveGone=document.querySelector('#settings-form-heartbeats [data-save-bar]').hidden;")
        result = _render(self.engine, steps)
        self.assertEqual(result["err"], "")
        out = result["out"]
        self.assertIn("Agent heartbeat, every (minutes)", out["beats"])
        self.assertIn("State heartbeat, every (minutes)", out["beats"])
        self.assertEqual(out["values"], ["15", "360"])
        self.assertTrue(out["open"], "changed where it stands")
        self.assertFalse(out["edit"], "no Edit")
        self.assertFalse(out["saveBefore"], "no Save until something is changed")
        self.assertTrue(out["saveAfter"], "Save once something is changed")
        self.assertEqual(out["unsent"], 0, "held until Save")
        self.assertEqual(out["sent"], [{"values": {"heartbeat_interval_seconds": 600}}])
        self.assertTrue(out["saveGone"])

    def test_starting_at_sign_in_is_on_general(self) -> None:
        """On General, its own card: changed where it stands, Save to keep it."""
        steps = (
            "click(document.querySelector('#tabs button[data-tab=\"settings\"]'));"
            "click(document.querySelector('[data-settings-view=\"general\"]'));"
            "await loadSettings();await wait();"
            "const card=document.querySelector('#startup-settings-card');"
            "out.inGeneral=!!card.closest('[data-settings-pane=\"general\"]');"
            "out.shown=card.getBoundingClientRect().height>0;"
            "out.inOthers=!!document.querySelector('#settings-form [data-key=\"start_at_login\"]');"
            "const box=card.querySelector('input[type=checkbox]');out.on=box.checked;"
            "box.checked=false;box.dispatchEvent(new Event('change',{bubbles:true}));"
            "click(card.querySelector('[data-settings-save]'));await wait();"
            "out.sent=window.__posts.filter(x=>x.p==='/api/settings').map(x=>x.body);")
        result = _render(self.engine, steps)
        self.assertEqual(result["err"], "")
        out = result["out"]
        self.assertTrue(out["inGeneral"] and out["shown"])
        self.assertTrue(out["on"], "on by default")
        self.assertEqual(out["sent"], [{"values": {"start_at_login": False}}])

    def test_privacy_each_listening_its_switch(self) -> None:
        """Privacy: what is listened to, each its own switch - what is left
        without it, and what it costs the computer; held until Save."""
        steps = (
            "click(document.querySelector('#tabs button[data-tab=\"settings\"]'));"
            "click(document.querySelector('[data-settings-view=\"privacy\"]'));"
            "await loadSettings();await wait();"
            "const card=document.querySelector('#privacy-card');"
            "out.shown=!card.closest('.settings-pane').hidden&&card.getBoundingClientRect().height>0;"
            "out.text=text(card);"
            "out.inOthers=!!document.querySelector('#settings-form [data-key=\"listener_screen_enabled\"]');"
            "const open=[...card.querySelectorAll('input[type=checkbox]')][0];"
            "out.open=!open.disabled;"
            "const r=open.getBoundingClientRect();out.box=[Math.round(r.width),Math.round(r.height)];"
            "open.checked=false;open.dispatchEvent(new Event('change',{bubbles:true}));"
            "out.unsent=window.__posts.filter(x=>x.p==='/api/settings').length;"
            "click(document.querySelector('[data-settings-save=\"settings-form-privacy\"]'));"
            "await wait();"
            "out.sent=window.__posts.filter(x=>x.p==='/api/settings').map(x=>x.body);")
        result = _render(self.engine, steps)
        self.assertEqual(result["err"], "")
        out = result["out"]
        for words in ("Read what is on the screen", "If turned off:",
                      "Only window titles are left", "Performance influence:", "The heaviest", "Changing these changes performance"):
            self.assertIn(words, out["text"])
        self.assertTrue(out["shown"], "the Privacy view opens")
        self.assertTrue(out["open"], "a switch to turn, where it stands")
        self.assertEqual(out["box"], [18, 18], "a box, not a field's width")
        self.assertEqual(out["unsent"], 0, "held until Save")
        self.assertEqual(out["sent"], [{"values": {"listener_screen_enabled": False}}])

    def test_modules_each_with_what_turning_it_off_means(self) -> None:
        """Modules: what runs by itself, each said - what it does, what
        turning it off means, what it spent - ticked where it stands; Save
        appears once one is changed and sends what was."""
        steps = (
            "click(document.querySelector('#tabs button[data-tab=\"settings\"]'));"
            "click(document.querySelector('[data-settings-view=\"modules\"]'));"
            "await loadModules();await wait();"
            "const card=document.querySelector('#modules-card');"
            "out.shown=!card.closest('.settings-pane').hidden&&card.getBoundingClientRect().height>0;"
            "out.text=text(card);"
            "out.heat=[...card.querySelectorAll('.module-row')].map(r=>[r.querySelector('strong').textContent,"
            "  r.style.getPropertyValue('--heat'),getComputedStyle(r).borderLeftColor]);"
            "out.open=[...card.querySelectorAll('[data-module]')].every(b=>!b.disabled);"
            "const bar=()=>card.querySelector('[data-modules-bar]');"
            "out.saveBefore=!bar().hidden;"
            "const box=card.querySelector('[data-module=\"module_calendar_enabled\"]');"
            "box.checked=false;box.dispatchEvent(new Event('change',{bubbles:true}));"
            "out.saveAfter=!bar().hidden;"
            "box.checked=true;box.dispatchEvent(new Event('change',{bubbles:true}));"
            "out.saveBack=!bar().hidden;"
            "box.checked=false;box.dispatchEvent(new Event('change',{bubbles:true}));"
            "click(card.querySelector('[data-modules-save]'));await wait(300);"
            "out.sent=window.__posts.filter(x=>x.p==='/api/settings').map(x=>x.body);")
        result = _render(self.engine, steps)
        self.assertEqual(result["err"], "")
        out = result["out"]
        self.assertNotIn("Activity and states", out["text"], "always on: not shown")
        self.assertNotIn("always on", out["text"])
        # The bar's colour, beside the others: the most spent, deep red; one
        # that spent nothing, the plain line.
        heat = {name: (share, colour) for name, share, colour in out["heat"]}
        self.assertEqual(heat["Calendar management"][0], "100%")
        self.assertEqual(heat["Task organisation"][0], "0%")
        self.assertNotEqual(heat["Calendar management"][1], heat["Task organisation"][1])
        self.assertIn("deep red for the most", out["text"])
        for words in ("Calendar management",
                      "If turned off:", "your contacts and you yourself looked up on the web",
                      "no one is looked up: an issue carries the news alone",
                      "while Connections is on, what your contacts have been doing",
                      "last 72 hours: about $0.05 · 12,000 tokens · 40% of the "
                      "functions · 30% of everything", "What you started yourself",
                      "The agent heartbeat, before its steps were counted apart",
                      "58.3% of the functions"):
            self.assertIn(words, out["text"])
        self.assertTrue(out["shown"], "the Functions view opens")
        self.assertTrue(out["open"], "ticked where it stands")
        self.assertFalse(out["saveBefore"])
        self.assertTrue(out["saveAfter"], "Save once one is changed")
        self.assertFalse(out["saveBack"], "changed back, nothing to save")
        self.assertEqual(out["sent"], [{"values": {"module_calendar_enabled": False}}])

    def test_display_says_which_languages_pictures_are_read_in(self) -> None:
        """Display: the five languages, each supported or not yet - each not
        yet with a tick of its own, and only the ticked ones downloaded, when
        the button is pressed."""
        steps = (
            "click(document.querySelector('#tabs button[data-tab=\"settings\"]'));"
            "await loadSettings();await loadOcr();await wait();"
            "const card=document.querySelector('#ocr-card');"
            "out.inDisplay=!!card.closest('[data-settings-pane=\"display\"]');"
            "out.text=text(card);"
            "out.unsent=window.__posts.filter(x=>x.p==='/api/ocr/add').length;"
            "const btn=card.querySelector('[data-ocr-add]');out.idle=btn.disabled;"
            "out.ticks=[...card.querySelectorAll('[data-ocr-code]')].map(b=>b.dataset.ocrCode);"
            "for(const c of ['zh','ja']){const b=card.querySelector(`[data-ocr-code=\"${c}\"]`);"
            "b.checked=true;b.dispatchEvent(new Event('change',{bubbles:true}));}"
            "out.ready=!btn.disabled;out.says=btn.textContent;"
            "click(btn);await wait();"
            "out.sent=window.__posts.filter(x=>x.p==='/api/ocr/add').map(x=>x.body);")
        result = _render(self.engine, steps)
        self.assertEqual(result["err"], "")
        out = result["out"]
        self.assertTrue(out["inDisplay"])
        self.assertIn("supported English", out["text"])
        self.assertIn("not yet Chinese", out["text"])
        self.assertEqual(out["ticks"], ["zh", "es", "ja", "ko"], "English is here already")
        self.assertTrue(out["idle"], "nothing ticked, nothing to download")
        self.assertTrue(out["ready"])
        self.assertEqual(out["says"], "Download 2 languages")
        self.assertEqual(out["unsent"], 0, "nothing added until asked")
        self.assertEqual(out["sent"], [{"codes": ["zh", "ja"]}])

    def test_times_in_the_users_time_zone(self) -> None:
        """A moment the agent wrote down in UTC is shown in this computer's
        time zone - the browser's, as the agent's own - not cut from the text."""
        from datetime import datetime
        moment = "2026-10-12T14:18:29.952328+00:00"
        local = datetime.fromisoformat(moment).astimezone()
        steps = ("await wait();"
                 f"out.minute=localStamp('{moment}');"
                 f"out.short=localStamp('{moment}','short');"
                 f"out.time=localStamp('{moment}','time');"
                 f"out.date=localStamp('{moment}','date');"
                 "out.bare=localStamp('2026-10-12');"
                 "out.unzoned=localStamp('2026-10-12T08:20:00');")
        result = _render(self.engine, steps)
        self.assertEqual(result["err"], "")
        out = result["out"]
        self.assertEqual(out["minute"], local.strftime("%Y-%m-%d %H:%M"))
        self.assertEqual(out["short"], local.strftime("%m-%d %H:%M"))
        self.assertEqual(out["time"], local.strftime("%H:%M:%S"))
        self.assertEqual(out["date"], local.strftime("%Y-%m-%d"))
        # A date alone, or a time with no zone, as written.
        self.assertEqual(out["bare"], "2026-10-12")
        self.assertEqual(out["unzoned"], "2026-10-12 08:20")

    def test_the_two_kinds_line_up(self) -> None:
        """"What is it?": each radio button its own size, the two in line."""
        steps = ("click(document.querySelector('#tabs button[data-tab=\"settings\"]'));"
                 "await loadSettings();await wait();"
                 "click(document.querySelector('#model-add-open'));await wait();"
                 "const radios=[...document.querySelectorAll('#auth-form input[name=\"kind\"]')];"
                 "out.boxes=radios.map(r=>{const b=r.getBoundingClientRect();"
                 "  return [Math.round(b.left),Math.round(b.width)];});")
        result = _render(self.engine, steps)
        self.assertEqual(result["err"], "")
        boxes = result["out"]["boxes"]
        self.assertEqual(len(boxes), 2)
        self.assertEqual(boxes[0][0], boxes[1][0], boxes)
        self.assertLess(max(width for _, width in boxes), 30, boxes)

    def test_the_interface_in_chinese(self) -> None:
        steps = (
            "await wait(300);"
            "const box=document.createElement('div');"
            "box.innerHTML='<div class=\"msg-text\">Settings</div><button>Save</button>';"
            "document.body.appendChild(box);await wait(200);"
            "out.tab=text(document.querySelector('#tabs button[data-tab=\"settings\"]'));"
            "out.overview=text(document.querySelector('#tabs button[data-tab=\"overview\"]'));"
            "out.button=text(box.querySelector('button'));"
            "out.message=text(box.querySelector('.msg-text'));"
            "out.placeholder=document.querySelector('#cx-search').placeholder;"
            "click(document.querySelector('#tabs button[data-tab=\"settings\"]'));"
            "await loadSettings();await wait(300);"
            "out.views=[...document.querySelectorAll('[data-settings-view]')].map(text);"
            "out.labels=text(document.querySelector('#settings-form-display'));"
            "out.lang=document.documentElement.lang;")
        result = _render(self.engine, steps, with_i18n=True)
        self.assertEqual(result["err"], "")
        out = result["out"]
        self.assertEqual(out["tab"], "设置")
        self.assertEqual(out["overview"], "概览")
        self.assertEqual(out["button"], "保存")
        # The user's or the agent's words are not the application's.
        self.assertEqual(out["message"], "Settings")
        self.assertEqual(out["placeholder"], "搜索姓名、机构、标签…")
        self.assertEqual(out["views"], ["通用", "显示", "头像", "功能", "隐私", "快捷键", "其他"])
        self.assertIn("显示语言", out["labels"])
        self.assertEqual(out["lang"], "zh-CN")


if __name__ == "__main__":
    unittest.main()
