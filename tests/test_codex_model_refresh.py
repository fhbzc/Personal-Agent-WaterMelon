"""Asking Codex for its models is a button, never a side effect of Settings.

Ported from PR #4 (by daniela-miaut). The app-server is started through the
children wrapper here, which on Windows also puts it in the agent's job; the
fake server below stands in for it through the same subprocess.Popen.
"""

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from support import ROOT, TemporaryAgent
from personalized_agent.api.errors import ApiError
from personalized_agent.api.routes import build_routes
from personalized_agent.api.server import Request
from personalized_agent.models.providers.codex_provider import CodexProvider


class CodexModelRefreshTests(unittest.TestCase):
    def setUp(self):
        self.agent = TemporaryAgent()
        self.provider = CodexProvider(workspace=self.agent.workspace)
        self.env = patch.dict(os.environ, {"CODEX_HOME": str(self.agent.dir)})
        self.env.start()
        self.processes = []
        self.messages = self.agent.dir / "requests.jsonl"

    def tearDown(self):
        for process in self.processes:
            self.assertIsNotNone(process.poll(), "model discovery left a process running")
        self.env.stop()
        self.agent.cleanup()

    def _server(self, mode="success"):
        """Exercise real pipes, notifications, paging, early exit and timeout."""
        program = r'''
import json, sys, time
path, mode = sys.argv[1:]
initialized = False
for line in sys.stdin:
    msg = json.loads(line)
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(line)
    method = msg["method"]
    if method == "initialize":
        print(json.dumps({"id": msg["id"], "result": {}}), flush=True)
    elif method == "initialized":
        initialized = True
    elif method == "model/list":
        assert initialized
        if mode == "timeout":
            time.sleep(60)
        if mode == "exit":
            sys.exit(1)
        if mode == "error":
            print(json.dumps({"id": msg["id"], "error": {"message": "sign in"}}), flush=True)
            continue
        print('non-json diagnostic', flush=True)
        print(json.dumps({"method": "notification"}), flush=True)
        rows = ([{"model": "future-one", "displayName": "Future One",
                  "defaultReasoningEffort": "medium", "supportedReasoningEfforts": [
                      {"reasoningEffort": "low"}, {"reasoningEffort": "medium"}]},
                 {"model": "hidden", "hidden": True}]
                if "cursor" not in msg["params"] else
                [{"model": "future-one"}, {"id": "future-two"}, None])
        result = {"data": rows, "nextCursor": "next" if "cursor" not in msg["params"] else None}
        if mode == "empty":
            result = {"data": [], "nextCursor": None}
        if mode == "bad-cursor":
            result["nextCursor"] = "next"
        print(json.dumps({"id": msg["id"], "result": result}), flush=True)
    else:
        raise AssertionError("Discovery must not start inference: " + method)
'''
        actual_popen = subprocess.Popen

        def spawn(*args, **kwargs):
            kwargs.pop("cwd", None)
            process = actual_popen([sys.executable, "-u", "-c", program,
                                    str(self.messages), mode], **kwargs)
            self.processes.append(process)
            return process

        return patch("personalized_agent.models.providers.codex_provider.subprocess.Popen",
                     side_effect=spawn)

    def test_settings_reads_never_launch_discovery(self):
        with patch("subprocess.Popen", side_effect=AssertionError("started discovery")):
            self.assertTrue(self.provider.list_models())

    def test_refresh_discovers_all_pages_and_keeps_new_list(self):
        with patch.object(self.provider, "executable", return_value="codex"), self._server():
            models = self.provider.refresh_models()
        self.assertEqual([m.id for m in models], ["default", "future-one", "future-two"])
        self.assertEqual(models[1].display_name, "Future One")
        self.assertEqual(models[1].reasoning_efforts, ["low", "medium"])
        self.assertEqual(models[1].default_reasoning_effort, "medium")
        self.assertEqual(self.provider.list_models(), models)
        requests = [json.loads(line) for line in
                    self.messages.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["method"] for r in requests],
                         ["initialize", "initialized", "model/list", "model/list"])
        self.assertEqual(requests[-1]["params"]["cursor"], "next")

    def test_subsequent_codex_cache_updates_are_still_picked_up(self):
        with patch.object(self.provider, "executable", return_value="codex"), self._server():
            self.provider.refresh_models()
        (self.agent.dir / "models_cache.json").write_text(json.dumps({"models": [
            {"slug": "newer-model", "visibility": "list"}]}), encoding="utf-8")
        self.assertEqual([m.id for m in self.provider.list_models()], ["default", "newer-model"])

    def test_failure_timeout_and_invalid_results_preserve_the_previous_list(self):
        with patch.object(self.provider, "executable", return_value="codex"), self._server():
            previous = self.provider.refresh_models()
        for mode in ("error", "exit", "empty", "bad-cursor", "timeout"):
            with self.subTest(mode), patch.object(self.provider, "executable", return_value="codex"), \
                    self._server(mode), patch(
                        "personalized_agent.models.providers.codex_provider.MODEL_REFRESH_TIMEOUT_SECONDS", 0.3):
                with self.assertRaises(RuntimeError):
                    self.provider.refresh_models()
            self.assertEqual(self.provider.list_models(), previous)

    def test_the_server_is_asked_read_only_and_never_for_approval(self):
        seen = []
        real = subprocess.Popen

        def spawn(*args, **kwargs):
            seen.append(list(args[0]))
            raise OSError("not started in this test")

        with patch.object(self.provider, "executable", return_value="codex"), \
                patch("personalized_agent.models.providers.codex_provider.subprocess.Popen",
                      side_effect=spawn):
            with self.assertRaises(OSError):
                self.provider.refresh_models()
        self.assertIs(subprocess.Popen, real)
        command = seen[0]
        self.assertEqual(command[:4], ["codex", "app-server", "--listen", "stdio://"])
        self.assertIn('sandbox_mode="read-only"', command)
        self.assertIn('approval_policy="never"', command)

    def test_it_goes_through_the_children_wrapper(self):
        """So a refresh in flight when the agent quits ends with it."""
        source = (ROOT / "personalized_agent" / "models" / "providers"
                  / "codex_provider.py").read_text(encoding="utf-8")
        refresh = source.split("def refresh_models", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("children.popen(", refresh)
        self.assertNotIn("subprocess.Popen(", refresh)

    def test_missing_codex_has_an_actionable_error(self):
        with patch.object(self.provider, "executable", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "not installed"):
                self.provider.refresh_models()

    def test_refresh_route_updates_settings_choices_without_changing_selection(self):
        app = self.agent.app
        app.models.register(self.provider)
        app.config.set("primary_provider", "codex")
        app.config.set("primary_model", "gpt-6-astra")
        before = app.config.all()
        routes = build_routes(app)
        req = Request("POST", "/api/models/codex/refresh", {}, {}, {})
        with patch.object(self.provider, "executable", return_value="codex"), self._server():
            result = routes[("POST", req.path)](req)
        self.assertEqual(result["provider"], "codex")
        settings = routes[("GET", "/api/settings")](req)
        self.assertIn("codex/future-two", settings["choices"]["models"])
        self.assertEqual(app.config.all(), before)
        with patch.object(self.provider, "executable", return_value=None):
            with self.assertRaises(ApiError) as caught:
                routes[("POST", req.path)](req)
        self.assertEqual(caught.exception.status, 502)


class ModelRefreshControlTests(unittest.TestCase):
    def test_click_refreshes_choices_preserves_selection_and_recovers_from_errors(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is required to exercise the Settings control")
        # Run the actual renderer with a small DOM adapter and deferred API
        # replies, so the loading state is checked while the request is pending.
        program = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
class Element {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.dataset = {}; this.events = {};
    this.classList = {add() {}, toggle() {}, remove() {}};
  }
  prepend(child) { this.children.unshift(child); }
  querySelectorAll() { return []; }
  set innerHTML(value) {
    this.children = [];
    if (this.tagName === 'select') {
      for (const match of value.matchAll(/<option value="([^"]*)" ([^>]*)>(.*?)<\/option>/g)) {
        this.appendChild(Object.assign(new Element('option'), {
          value: match[1], selected: match[2].includes('selected'), textContent: match[3]}));
      }
    }
  }
  appendChild(child) {
    this.children.push(child);
    if (this.tagName === 'select' && (child.selected || !this.value)) {
      this.value = child.value;
    }
  }
  append(...children) { children.forEach(child => this.appendChild(child)); }
  addEventListener(name, handler) { this.events[name] = handler; }
}
const root = new Element('div');
const document = {createElement: tag => new Element(tag), addEventListener() {}};
const find = (node, predicate) => predicate(node) ? node :
  node.children.map(child => find(child, predicate)).find(Boolean);
const data = {values: {primary_provider: 'codex', primary_model: 'saved-model', primary_reasoning_effort: 'high'},
  schema: [{key: 'primary_model', label: 'Primary model', type: 'dynamic_choice',
    choices_source: 'models', section: 'Models', description: 'Choose a model'},
    {key: 'primary_reasoning_effort', label: 'Primary reasoning effort', type: 'choice',
     choices: ['default', 'low', 'high'], section: 'Models', description: 'Choose an effort'}],
  choices: {models: ['codex/default', 'codex/saved-model']}};
let resolve, reject, loads = 0;
const posts = [], notices = [];
const api = {post: (path, body) => {
  if (path === '/api/settings') Object.assign(data.values, body.values);
  posts.push({path, body}); return new Promise((yes, no) => {resolve = yes; reject = no;});
}};
const source = fs.readFileSync(process.argv[1], 'utf8');
// From the settings' own definitions on: the heartbeats in minutes, the
// times of day, the cards shown until Edit - renderSettings uses them.
const start = source.indexOf("/* THE HEARTBEATS' INTERVALS");
const end = source.indexOf('/* -------------------------------------------------------------- debugger */', start);
const render = new Function('document', '$', '$$', 'api', 'toast', 'loadSettings', 'escapeHtml',
  'refreshBrowserCard',
  'let codexModelsRefreshing = false;' + source.slice(start, end) + '; return Object.assign(renderSettings, {edit: settingsEdit});')(
    document, selector => selector === '#settings-form' ? root : null,
    () => [find(root, e => e.className?.includes('refresh-models'))].filter(Boolean), api,
    (message, kind) => notices.push({message, kind}),
    async () => { loads++; data.choices.models = ['codex/default', 'codex/new-model']; render(data); },
    text => String(text), () => {});
(async () => {
  render(data);
  let picker = find(root, e => e.className === 'model-picker');
  let [select, button] = picker.children;
  assert.equal(select.value, 'codex/saved-model');
  assert.equal(button.textContent, 'Refresh models');
  const success = button.events.click();
  assert.equal(button.disabled, true);
  assert.equal(button.textContent, 'Refreshing…');
  render(data); // A periodic Settings reload during discovery.
  const pending = find(root, e => e.className?.includes('refresh-models'));
  assert.equal(pending.disabled, true);
  assert.equal(pending.textContent, 'Refreshing…');
  await pending.events.click();
  assert.equal(posts.length, 1);
  resolve({}); await success;
  picker = find(root, e => e.className === 'model-picker');
  [select, button] = picker.children;
  assert.equal(select.value, 'codex/saved-model');
  assert(select.children.some(e => e.value === 'codex/new-model'));
  assert.equal(data.values.primary_model, 'saved-model');
  const failure = button.events.click();
  render(data);
  reject(new Error('Codex unavailable')); await failure;
  button = find(root, e => e.className?.includes('refresh-models'));
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, 'Refresh models');
  assert.equal(loads, 1);
  assert.equal(notices.at(-1).kind, 'error');
  assert.equal(notices.at(-1).message, 'Codex unavailable');
  assert.deepEqual(posts.map(p => p.path), ['/api/models/codex/refresh', '/api/models/codex/refresh']);
  const effort = find(root, e => e.tagName === 'select' && e.children.some(o => o.value === 'high'));
  assert.equal(effort.value, 'high');
  assert.equal(effort.children[0].textContent, 'Use Codex default');
  effort.value = 'low';
  const before = posts.length;
  await effort.events.change();
  // HELD UNTIL SAVE, as every settings card is: nothing sent on the change,
  // the choice kept for Save to send.
  assert.equal(posts.length, before);
  assert.equal(render.edit.pending.primary_reasoning_effort, 'low');
  console.log('Settings model refresh interactions passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
        result = subprocess.run(
            [node, "-e", program, str(ROOT / "personalized_agent/api/web/app.js")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("interactions passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
