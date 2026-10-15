"""The chat debugger: its scenarios, its routes, and the transcripts it keeps.

Ported from PR #4 (by daniela-miaut), adapted to the chat as it is now and to
the rule that a scenario's throwaway application lives under the running
application's own data root - never in the system's temporary folder.
"""

from __future__ import annotations

import json
import logging
import socket
import tempfile
import threading
import unittest
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from support import TemporaryAgent, keyed

from personalized_agent.api.errors import ApiError
from personalized_agent.api.routes import build_routes
from personalized_agent.api.server import ApiServer, Request
from personalized_agent.common.logging.setup import SECRETS
from personalized_agent.data_manage.storage.database import Database
from personalized_agent.debugger import chat_tests
from personalized_agent.debugger.chat import ChatDebugger, ChatDebuggerBusy
from personalized_agent.debugger.chat_tests import TESTS


def request(body=None, query=None):
    return Request("POST", "/api/debugger/chat/run", query or {}, body or {}, {})


class ChatDebuggerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = TemporaryAgent()
        self.app = self.fixture.app

    def tearDown(self):
        self.fixture.cleanup()

    def test_every_builtin_scenario_passes_through_real_chat(self):
        debugger = ChatDebugger(self.app)
        original_models = self.app.agent.models
        original_conversations = self.app.conversations.conversations()
        original_settings = self.app.config.all()
        original_handlers = tuple(logging.getLogger().handlers)
        for test in TESTS:
            with self.subTest(test=test["id"]):
                run = debugger.run(test_id=test["id"])
                self.assertEqual(run["status"], "passed", json.dumps(run["checks"]))
                self.assertTrue(run["checks"])
                kinds = [event["kind"] for event in run["events"]]
                for kind in ("fixture", "instruction", "context", "model_request",
                             "completion", "verification"):
                    self.assertIn(kind, kinds)
                self.assertEqual([e["sequence"] for e in run["events"]],
                                 list(range(1, len(run["events"]) + 1)))
                self.assertTrue(run["result"]["task_id"])
                prompts = [event["data"]["instruction"] for event in run["events"]
                           if event["kind"] == "instruction"]
                self.assertEqual(run["prompt"], "\n\n".join(prompts))
        # The running application is untouched by any of it.
        self.assertIs(self.app.agent.models, original_models)
        self.assertEqual(self.app.conversations.conversations(), original_conversations)
        self.assertEqual(self.app.config.all(), original_settings)
        self.assertEqual(tuple(logging.getLogger().handlers), original_handlers)
        self.assertEqual(len(debugger.snapshot()["runs"]), len(TESTS))

    def test_a_scenario_runs_under_the_data_root_and_leaves_nothing(self):
        """Never the system's temporary folder: inside the application's own
        data root, and gone afterwards."""
        built = []
        real = chat_tests._throwaway

        def watched(where):
            app = real(where)
            built.append((Path(where), app.root))
            return app

        debugger = ChatDebugger(self.app)
        with patch.object(chat_tests, "_throwaway", side_effect=watched), \
                patch("tempfile.mkdtemp", side_effect=AssertionError("a temporary folder")), \
                patch("tempfile.TemporaryDirectory",
                      side_effect=AssertionError("a temporary folder")):
            run = debugger.run(test_id="chat.workspace_write_read")
        self.assertEqual(run["status"], "passed", json.dumps(run["checks"]))
        where, root = built[0]
        scratch = (self.app.paths.data_dir / chat_tests.SCRATCH_DIR).resolve()
        self.assertEqual(where.resolve().parent, scratch)
        self.assertTrue(root.resolve().is_relative_to(scratch))
        self.assertFalse(where.exists(), "the throwaway application was left behind")
        fixture = next(e["data"] for e in run["events"] if e["kind"] == "fixture")
        self.assertTrue(Path(fixture["agent_root"]).resolve().is_relative_to(scratch))

    def test_a_scenario_without_a_data_root_is_refused(self):
        with self.assertRaises(RuntimeError):
            chat_tests.run_test(TESTS[0]["id"], lambda *_: None, where=None)

    def test_api_custom_chat_can_continue_a_conversation(self):
        routes = build_routes(self.app)
        run = routes[("POST", "/api/debugger/chat/run")](
            request({"message": "My project is Aurora."}))
        self.assertEqual(run["status"], "passed")
        continuation = routes[("POST", "/api/debugger/chat/run")](request({
            "message": "What is my project?",
            "conversation_id": run["result"]["conversation_id"],
        }))
        model_request = next(event["data"] for event in continuation["events"]
                             if event["kind"] == "model_request")
        self.assertEqual(model_request["messages"][0]["content"], "My project is Aurora.")
        self.assertEqual(model_request["messages"][-1]["content"], "What is my project?")
        detail = routes[("GET", "/api/debugger/chat/run")](request(query={"id": [run["id"]]}))
        self.assertEqual(detail, run)
        listing = routes[("GET", "/api/debugger/chat")](request())
        self.assertEqual(len(listing["runs"]), 2)
        self.assertNotIn("events", listing["runs"][0])
        self.assertEqual(listing["runs"][0]["prompt"], "What is my project?")
        self.assertEqual(listing["runs"][0]["conversation_id"],
                         run["result"]["conversation_id"])

    def test_reopening_application_restores_runs_and_conversation_context(self):
        from personalized_agent.app import AgentApplication

        routes = build_routes(self.app)
        first = routes[("POST", "/api/debugger/chat/run")](request({
            "message": "Remember the café ☕ project."}))
        self.app.db.close()
        reopened = AgentApplication(root=self.fixture.root, configure_logs=False,
                                    use_keyring=False)
        try:
            routes = build_routes(reopened)
            history = routes[("GET", "/api/debugger/chat")](request())
            self.assertEqual(history["runs"][0]["id"], first["id"])
            self.assertEqual(history["runs"][0]["prompt"], "Remember the café ☕ project.")
            self.assertEqual(routes[("GET", "/api/debugger/chat/run")](
                request(query={"id": [first["id"]]})), first)
            second = routes[("POST", "/api/debugger/chat/run")](request({
                "message": "What project?",
                "conversation_id": history["runs"][0]["conversation_id"],
            }))
            model_request = next(event["data"] for event in second["events"]
                                 if event["kind"] == "model_request")
            self.assertEqual(model_request["messages"][0]["content"],
                             "Remember the café ☕ project.")
            self.assertEqual(len(routes[("GET", "/api/debugger/chat")](request())["runs"]), 2)
        finally:
            reopened.db.close()

    def test_api_rejects_missing_invalid_and_unknown_inputs(self):
        routes = build_routes(self.app)
        post = routes[("POST", "/api/debugger/chat/run")]
        for body in ({}, {"message": " "}, {"message": []},
                     {"test_id": 1}, {"message": "hello", "test_id": TESTS[0]["id"]},
                     {"message": "hello", "conversation_id": []}):
            with self.subTest(body=body), self.assertRaises(ApiError):
                post(request(body))
        with self.assertRaises(ApiError) as error:
            post(request({"test_id": "missing-test"}))
        self.assertEqual(error.exception.status, 404)
        with self.assertRaises(ApiError) as error:
            routes[("GET", "/api/debugger/chat/run")](request(query={"id": ["missing-run"]}))
        self.assertEqual(error.exception.status, 404)

    def test_no_debugger_build_registers_no_chat_debugger_routes(self):
        with patch("personalized_agent.api.routes.debugger_available", return_value=False):
            routes = build_routes(self.app)
        self.assertFalse(any(path.startswith("/api/debugger/chat") for _method, path in routes))


class TranscriptStorageTests(unittest.TestCase):
    @staticmethod
    def app(handler, db=None):
        return SimpleNamespace(agent=SimpleNamespace(handle_message=handler), db=db)

    def test_secrets_redacted_at_capture_and_snapshots_are_detached(self):
        secret = "my-private-test-value"
        SECRETS.register(secret)
        received = []

        def handle(message, conversation_id, *, origin, debug):
            received.append(message)
            debug("model_request", {"system": message, "nested": {"api_key": "another-value"}})
            return {"answer": secret, "error": None, "conversation_id": "test"}

        try:
            with tempfile.TemporaryDirectory() as directory:
                db = Database(Path(directory) / "agent.sqlite")
                try:
                    debugger = ChatDebugger(self.app(handle, db))
                    run = debugger.run(message=f"hello {secret}")
                    encoded = json.dumps(run)
                    self.assertNotIn(secret, encoded)
                    self.assertNotIn("another-value", encoded)
                    self.assertIn("REDACTED", encoded)
                    self.assertEqual(received, [f"hello {secret}"])
                    saved = db.query_one("SELECT summary, transcript FROM chat_debugger_runs")
                    self.assertNotIn(secret, json.dumps(saved))
                    self.assertNotIn("another-value", json.dumps(saved))
                    run["events"].clear()
                    self.assertTrue(debugger.detail(run["id"])["events"])
                    snapshot = debugger.snapshot()
                    snapshot["runs"][0]["name"] = "changed"
                    self.assertNotEqual(debugger.snapshot()["runs"][0]["name"], "changed")
                finally:
                    db.close()
        finally:
            SECRETS.forget(secret)

    def test_all_runs_survive_cache_eviction_and_database_reopen(self):
        def handle(message, conversation_id, *, origin, debug):
            debug("model_request", {"system": "x" * 1000})
            debug("model_response", {"text": "later"})
            return {"answer": "okay", "error": None}

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.sqlite"
            db = Database(path)
            try:
                debugger = ChatDebugger(self.app(handle, db), capacity=2, event_bytes=100)
                runs = [debugger.run(message=f"hello {index}") for index in range(25)]
                self.assertTrue(runs[-1]["capture_truncated"])
                self.assertEqual([e["kind"] for e in runs[-1]["events"]], ["capture_limit"])
                self.assertEqual(len(debugger.snapshot()["runs"]), 25)
                self.assertEqual(debugger.detail(runs[0]["id"]), runs[0])
            finally:
                db.close()
            reopened = Database(path)
            try:
                debugger = ChatDebugger(self.app(handle, reopened), capacity=2)
                self.assertEqual([run["id"] for run in debugger.snapshot()["runs"]],
                                 [run["id"] for run in reversed(runs)])
                for run in runs:
                    self.assertEqual(debugger.detail(run["id"]), run)
            finally:
                reopened.close()

    def test_builtin_prompts_survive_event_capture_limit(self):
        def run_test(test_id, capture, **_where):
            capture("instruction", {"instruction": "First prompt café ☕"})
            capture("instruction", {"instruction": "Second prompt"})
            return {"result": {"answer": "okay"}, "checks": [{"passed": True}]}

        debugger = ChatDebugger(self.app(None), event_bytes=1)
        with patch("personalized_agent.debugger.chat_tests.run_test", side_effect=run_test):
            run = debugger.run(test_id=TESTS[0]["id"])
        self.assertTrue(run["capture_truncated"])
        self.assertEqual(run["prompt"], "First prompt café ☕\n\nSecond prompt")

    def test_unfinished_run_is_kept_and_marked_interrupted_on_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "agent.sqlite")
            try:
                debugger = ChatDebugger(self.app(None, db))
                unfinished = {"id": "interrupted", "started_at": "2026-10-01T00:00:00Z",
                              "status": "running", "prompt": "Keep this prompt",
                              "events": [], "result": None}
                debugger._persist(unfinished)
                reopened = ChatDebugger(self.app(None, db))
                run = reopened.detail("interrupted")
                self.assertEqual(run["status"], "error")
                self.assertIn("closed before", run["error"])
                self.assertEqual(reopened.snapshot()["runs"][0]["prompt"], "Keep this prompt")
            finally:
                db.close()

    def test_failed_execution_records_terminal_error_and_releases_runner(self):
        def handle(message, conversation_id, *, origin, debug):
            raise RuntimeError("test execution failed")

        debugger = ChatDebugger(self.app(handle))
        for _ in range(2):
            run = debugger.run(message="hello")
            self.assertEqual(run["status"], "error")
            self.assertIn("test execution failed", run["error"])
            self.assertEqual(run["events"][-1]["kind"], "error")

    def test_running_transcript_readable_and_concurrent_runs_rejected(self):
        entered, release = threading.Event(), threading.Event()

        def handle(message, conversation_id, *, origin, debug):
            debug("model_request", {"system": "prompt"})
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test release timeout")
            return {"answer": "okay", "error": None}

        debugger = ChatDebugger(self.app(handle))
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(debugger.run, message="hello")
            try:
                self.assertTrue(entered.wait(3))
                run = debugger.snapshot()["runs"][0]
                self.assertEqual(run["status"], "running")
                self.assertEqual(debugger.detail(run["id"])["events"][0]["kind"],
                                 "model_request")
                with self.assertRaises(ChatDebuggerBusy):
                    debugger.run(message="another")
            finally:
                release.set()
            self.assertEqual(future.result()["status"], "passed")


class ChatDebuggerHttpTests(unittest.TestCase):
    def test_routes_and_assets_work_over_local_http(self):
        fixture = TemporaryAgent()
        server = None
        try:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            fixture.app.config.set("api_port", port)
            server = ApiServer(fixture.app)
            server.start()
            base = f"http://127.0.0.1:{port}"

            def get(path):
                with urllib.request.urlopen(keyed(fixture.app, base + path), timeout=30) as response:
                    return response.read().decode("utf-8")

            def post(path, body):
                req = urllib.request.Request(
                    base + path, data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(keyed(fixture.app, req), timeout=60) as response:
                    return json.loads(response.read())

            self.assertIn('data-tab="chatdebug"', get("/"))
            self.assertIn("window.chatDebuggerPanel", get("/chat-debugger.js"))
            self.assertIn("window.createChatNavigation", get("/chat-navigation.js"))
            self.assertEqual(len(json.loads(get("/api/debugger/chat"))["tests"]), len(TESTS))
            run = post("/api/debugger/chat/run", {"test_id": "chat.history_unicode"})
            self.assertEqual(run["status"], "passed", json.dumps(run["checks"]))
            self.assertEqual(json.loads(get("/api/debugger/chat/run?id=" + run["id"])), run)
            result = post("/api/chat", {"message": "A normal chat still works."})
            self.assertTrue(result["answer"])
            self.assertEqual(len(json.loads(get("/api/debugger/chat"))["runs"]), 1)
        finally:
            if server:
                server.stop()
            fixture.cleanup()


if __name__ == "__main__":
    unittest.main()
