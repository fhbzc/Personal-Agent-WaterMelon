"""The agents' tools offered to Claude Code natively, over MCP - and its
calls ended when they fall silent, not when a clock runs out.

Offered the tools only as words, the model called them natively anyway and
Claude Code answered "No such tool available" - a round wasted, a file being
written written twice; and a fixed 300 s ended calls that were thinking at
length (measured: eleven minutes of thinking, never silent for 2.4 s). Now
each call has a server of the application's own (models/providers/
mcp_bridge.py) whose every tool call the run runs, as its own loop does -
paused, stopped, limited and recorded the same - and the client's output is
read as a stream, the call ended only after a stretch of silence.

A real call is made only with PA_TEST_CLAUDE_CODE=1 (it uses the user's
Claude Code and its sign-in).
"""

from __future__ import annotations

import json
import os
import time
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace
from unittest import mock

from personalized_agent.models.base import ModelRequest, ModelUsage, ToolSpec
from personalized_agent.models.providers import claude_code_provider as cc
from personalized_agent.models.providers.mcp_bridge import ToolBridge, tool_name

SPEC = ToolSpec("run_python", "Run Python code", {"type": "object",
                                                  "properties": {"code": {"type": "string"}}})


def _rpc(url, method, params=None, ident=1):
    body = json.dumps({"jsonrpc": "2.0", "id": ident, "method": method,
                       "params": params or {}}).encode()
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as answer:
        return json.loads(answer.read() or b"{}")


class TheBridgeTests(unittest.TestCase):

    def test_it_serves_the_requests_tools_and_runs_them_through_the_agent(self) -> None:
        ran = []

        def runner(name, arguments):
            ran.append((name, arguments))
            return {"stdout": "42\n"}

        with ToolBridge([SPEC], runner) as bridge:
            said = _rpc(bridge.url, "initialize", {"protocolVersion": "2025-06-18"})
            self.assertEqual(said["result"]["serverInfo"]["name"], "pa")
            listed = _rpc(bridge.url, "tools/list")["result"]["tools"]
            self.assertEqual([one["name"] for one in listed], ["run_python"])
            self.assertIn("properties", listed[0]["inputSchema"])
            called = _rpc(bridge.url, "tools/call",
                          {"name": "run_python", "arguments": {"code": "print(42)"}})
            self.assertFalse(called["result"]["isError"])
            self.assertEqual(json.loads(called["result"]["content"][0]["text"]),
                             {"stdout": "42\n"})
            # A tool it was not given is not run.
            other = _rpc(bridge.url, "tools/call", {"name": "delete_file", "arguments": {}})
            self.assertTrue(other["result"]["isError"])
            self.assertEqual(bridge.allowed(), [tool_name("run_python")])
            config = json.loads(bridge.config())["mcpServers"]["pa"]
            self.assertEqual((config["type"], config["url"]), ("http", bridge.url))
        self.assertEqual(ran, [("run_python", {"code": "print(42)"})])
        self.assertEqual([one["name"] for one in bridge.calls], ["run_python", "delete_file"])

    def test_only_its_secret_address_is_answered(self) -> None:
        with ToolBridge([SPEC], lambda *_: {}) as bridge:
            wrong = bridge.url.replace(bridge.secret, "guessed")
            with self.assertRaises(urllib.error.HTTPError) as said:
                _rpc(wrong, "tools/list")
            self.assertEqual(said.exception.code, 404)
            self.assertTrue(bridge.url.startswith("http://127.0.0.1:"))

    def test_busy_while_a_tool_runs_and_its_failure_is_the_models_to_read(self) -> None:
        seen = {}

        def runner(name, arguments):
            seen["busy"] = bridge.busy
            raise RuntimeError("the script broke")

        with ToolBridge([SPEC], runner) as bridge:
            called = _rpc(bridge.url, "tools/call", {"name": "run_python", "arguments": {}})
        self.assertTrue(seen["busy"])
        self.assertFalse(bridge.busy)
        self.assertTrue(called["result"]["isError"])
        self.assertIn("the script broke", called["result"]["content"][0]["text"])


class TheStreamIsWatchedTests(unittest.TestCase):

    def test_ended_when_silent_not_while_talking_or_a_tool_runs(self) -> None:
        stream = cc._Stream(ModelRequest(messages=[]), None, silent_seconds=0.2)
        self.assertIsNone(stream.watch())
        time.sleep(0.3)
        self.assertIn("silent", stream.watch() or "")
        stream.line('{"type":"stream_event"}\n')
        self.assertIsNone(stream.watch())
        bridge = SimpleNamespace(busy=True, last_activity=0.0)
        busy = cc._Stream(ModelRequest(messages=[]), bridge, silent_seconds=0.2)
        time.sleep(0.3)
        self.assertIsNone(busy.watch(), "a tool of the agent's is working")

    def test_ended_when_the_run_is_stopped(self) -> None:
        stopped = {"now": False}
        stream = cc._Stream(ModelRequest(messages=[], cancelled=lambda: stopped["now"]),
                            None, silent_seconds=60)
        self.assertIsNone(stream.watch())
        stopped["now"] = True
        self.assertEqual(stream.watch(), "cancelled")

    def test_what_it_has_used_so_far_is_told(self) -> None:
        told = []
        stream = cc._Stream(ModelRequest(messages=[], on_usage=told.append), None, 60)
        for n, out in ((1, 10), (1, 30), (2, 5)):
            stream.line(json.dumps({"type": "assistant", "message": {
                "id": f"m{n}", "usage": {"input_tokens": 3, "output_tokens": out,
                                         "cache_read_input_tokens": 100}}}) + "\n")
        last = told[-1]
        self.assertIsInstance(last, ModelUsage)
        # Each message's own latest count, added up across messages.
        self.assertEqual((last.output_tokens, last.input_tokens, last.cached_input_tokens),
                         (35, 6, 200))

    def test_the_envelope_is_the_streams_last_result_or_the_whole_output(self) -> None:
        stream = "\n".join([json.dumps({"type": "system", "subtype": "init"}),
                            json.dumps({"type": "result", "result": "ok", "usage": {}})])
        self.assertEqual(cc._result_of(stream)["result"], "ok")
        self.assertEqual(cc._result_of('{"result": "old"}')["result"], "old")
        with self.assertRaises(ValueError):
            cc._result_of("not json")


class TheCommandTests(unittest.TestCase):

    def setUp(self) -> None:
        self.provider = cc.ClaudeCodeProvider()
        self.provider._resolve = lambda: ("cli", "claude", None)

    def test_streamed_and_with_the_calls_server_and_its_tools_alone(self) -> None:
        request = ModelRequest(messages=[{"role": "user", "content": "hi"}], tools=[SPEC],
                               system="be brief")
        with ToolBridge([SPEC], lambda *_: {}) as bridge:
            command = self.provider._command(request, "opus", bridge=bridge)
            self.assertEqual(command[command.index("--output-format") + 1], "stream-json")
            self.assertIn("--include-partial-messages", command)
            self.assertIn("--strict-mcp-config", command)
            self.assertEqual(command[command.index("--mcp-config") + 1], bridge.config())
            self.assertEqual(command[command.index("--allowedTools") + 1],
                             "mcp__pa__run_python")
            system = command[command.index("--append-system-prompt") + 1]
            self.assertIn("mcp__pa__run_python", system)
            self.assertNotIn("reply with a single JSON object", system)
        # Without a runner: the words, as before, and no server.
        plain = self.provider._command(request, "opus")
        self.assertNotIn("--mcp-config", plain)
        self.assertIn("reply with a single JSON object", plain[-1])

    def test_its_own_web_search_in_place_of_the_agents(self) -> None:
        """With the model's own search, Claude Code searches and reads pages
        with its WebSearch and WebFetch - the agent's web tools not offered;
        without it, those stay shut and the agent's are offered."""
        from personalized_agent.common import children
        web = ToolSpec("web_search", "Search the web",
                       {"type": "object", "properties": {"query": {"type": "string"}}})
        page = ToolSpec("fetch_url", "Read a page",
                        {"type": "object", "properties": {"url": {"type": "string"}}})
        seen = []

        def capture(command, **_kw):
            # The system prompt is read from its file while the call runs.
            at = command.index("--append-system-prompt-file")
            seen.append([*command[:at], "--append-system-prompt",
                         open(command[at + 1], encoding="utf-8").read(), *command[at + 2:]])
            raise children.Ended("stopped by the test")

        for builtin in (True, False):
            request = ModelRequest(messages=[{"role": "user", "content": "hi"}],
                                   tools=[web, page, SPEC], builtin_web=builtin)
            with mock.patch.object(self.provider, "executable", return_value="claude"), \
                    mock.patch.object(children, "run", side_effect=capture):
                with self.assertRaises(Exception):
                    self.provider.invoke(request, "opus")
        own, theirs = seen
        denied = own[own.index("--disallowed-tools") + 1:own.index("--strict-mcp-config")]
        self.assertNotIn("WebSearch", denied)
        self.assertNotIn("WebFetch", denied)
        allowed = own[own.index("--allowedTools") + 1:]
        self.assertIn("WebSearch", allowed)
        self.assertIn("WebFetch", allowed)
        system = own[own.index("--append-system-prompt") + 1]
        self.assertIn(cc.BUILTIN_WEB_NOTE, system)
        self.assertNotIn('"web_search"', system)
        self.assertIn("run_python", system)
        # Without it: shut, and the agent's own web tools offered.
        denied = theirs[theirs.index("--disallowed-tools") + 1:theirs.index("--strict-mcp-config")]
        self.assertIn("WebSearch", denied)
        self.assertNotIn("--allowedTools", theirs)
        self.assertIn("web_search", theirs[theirs.index("--append-system-prompt") + 1])

    def test_its_own_reading_tools_denied_where_the_agents_may_not_read(self) -> None:
        """Read, Glob and Grep offered - and denied, as Claude Code writes a
        place, everything the app's own reading refuses."""
        import tempfile
        from pathlib import Path
        from personalized_agent.runtime.filesystem import unreadable_for
        from personalized_agent.runtime.permissions import WorkspacePolicy
        # Not in the temp directory: beside its many thousands nothing is
        # offered (below).
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as top:
            top = Path(top)
            (top / "home" / "work").mkdir(parents=True)
            (top / "home" / "secret").mkdir()
            (top / "home" / "notes.txt").write_text("n")
            (top / "elsewhere").mkdir()
            policy = WorkspacePolicy(workspace_root=top / "home",
                                     writable_root=top / "home" / "work",
                                     protected_paths=[top / "home" / "secret"])
            denied = [Path(one).resolve() for one in unreadable_for(policy)]
            home = (top / "home").resolve()
            self.assertIn((top / "elsewhere").resolve(), denied)
            self.assertIn((top / "home" / "secret").resolve(), denied)
            self.assertFalse(any(one == home or home.is_relative_to(one) or
                                 one == (top / "home" / "notes.txt").resolve()
                                 for one in denied), "the readable root stays open")
        # Beside too many to list - a work folder in the temp directory: not
        # offered at all, never offered unbounded.
        from personalized_agent.runtime import filesystem
        with tempfile.TemporaryDirectory() as top:
            top = Path(top)
            (top / "work").mkdir()
            for n in range(filesystem.DENY_PER_FOLDER + 1):
                (top / f"other{n}").write_text("x")
            self.assertIsNone(unreadable_for(WorkspacePolicy(
                workspace_root=top / "work", writable_root=top / "work")))
        self.assertEqual(cc.read_rule("C:/Users/me/.ssh"), "Read(//c/Users/me/.ssh/**)")
        request = ModelRequest(messages=[{"role": "user", "content": "hi"}], tools=[SPEC],
                               native_read=["C:/Users/me/.ssh"])
        command = self.provider._command(request, "opus")
        denied = command[command.index("--disallowed-tools") + 1:
                         command.index("--strict-mcp-config")]
        self.assertIn("Read(//c/Users/me/.ssh/**)", denied)
        for name in cc.NATIVE_READ:
            self.assertNotIn(name, denied)
        self.assertEqual(command[command.index("--allowedTools") + 1:
                                 command.index("--allowedTools") + 4], list(cc.NATIVE_READ))
        # Writing and running stay shut.
        for name in ("Write", "Edit", "Bash"):
            self.assertIn(name, denied)
        # None: not offered.
        plain = self.provider._command(ModelRequest(messages=[], tools=[SPEC]), "opus")
        self.assertIn("Read", plain)
        self.assertNotIn("--allowedTools", plain)

    def test_a_long_system_prompt_goes_in_a_file(self) -> None:
        """Windows ends a command line at 32,767 characters: experience
        learning's prompt never reached the client ("The filename or
        extension is too long"). It is read from a file, removed after."""
        from pathlib import Path
        from personalized_agent.common import children
        long = "x" * 40_000
        seen = {}

        def capture(command, **_kw):
            at = command.index("--append-system-prompt-file")
            seen["file"] = Path(command[at + 1])
            seen["text"] = seen["file"].read_text(encoding="utf-8")
            seen["length"] = sum(len(one) + 1 for one in command)
            raise children.Ended("stopped by the test")

        request = ModelRequest(messages=[{"role": "user", "content": "hi"}], system=long)
        with mock.patch.object(self.provider, "executable", return_value="claude"),                 mock.patch.object(children, "run", side_effect=capture):
            with self.assertRaises(Exception):
                self.provider.invoke(request, "opus")
        self.assertIn(long, seen["text"])
        self.assertLess(seen["length"], 32_767)
        self.assertFalse(seen["file"].exists(), "removed once the call is over")
        # Places to deny past what fits: its own reading is not offered.
        many = [f"C:/Users/someone/folder number {n}" for n in range(1000)]
        request = ModelRequest(messages=[{"role": "user", "content": "hi"}], system=long,
                               tools=[SPEC], native_read=many)
        with mock.patch.object(self.provider, "executable", return_value="claude"),                 mock.patch.object(children, "run", side_effect=capture):
            with self.assertRaises(Exception):
                self.provider.invoke(request, "opus")
        self.assertLess(seen["length"], 32_767)
        self.assertNotIn(cc.NATIVE_READ_NOTE, seen["text"])

    def test_the_setting_decides_which_search(self) -> None:
        from personalized_agent.models.manager import ModelManager
        sent = []

        class _Config(dict):
            def get(self, key, default=None):
                return super().get(key, default)

        for chosen, expected in (("model", True), ("app", False)):
            manager = ModelManager.__new__(ModelManager)
            manager.config = _Config(web_search_with=chosen)
            request = ModelRequest(messages=[{"role": "user", "content": "hi"}],
                                   tools=[SPEC])
            sent.append((manager._as_configured(request).builtin_web, expected))
        self.assertEqual([one for one, _ in sent], [expected for _, expected in sent])

    def test_a_call_ended_by_its_watch_says_why(self) -> None:
        from personalized_agent.common import children
        from personalized_agent.models.base import ModelInvocationError
        request = ModelRequest(messages=[{"role": "user", "content": "hi"}])
        with mock.patch.object(self.provider, "executable", return_value="claude"), \
                mock.patch.object(children, "run",
                                  side_effect=children.Ended("was silent for 150s")):
            with self.assertRaises(ModelInvocationError) as said:
                self.provider.invoke(request, "opus")
        self.assertIn("was silent for 150s", str(said.exception))
        self.assertEqual(said.exception.availability.failure_class.value, "temporary")


class TheRunRunsTheToolsTests(unittest.TestCase):
    """A run whose model calls its tools natively, during the call: each is
    run by the run - the file written, the transcript and the live session
    saying so, the exchange kept in the conversation."""

    def test_a_whole_run(self) -> None:
        from test_work_transcript import _Base, _Counted

        class _Native(_Counted):
            native = 0

            def invoke(self, request, role=""):
                first = request.messages[0]["content"]
                first = first if isinstance(first, str) else json.dumps(first)
                if ("You review a work agent" not in (request.system or "")
                        and "WORK ONE ROUND" in first and request.tool_runner
                        and request.tools):
                    self.asked.append(request)
                    self.native += 1
                    said = request.tool_runner("write_file",
                                               {"path": "notes.md", "content": "hello"})
                    refused = request.tool_runner("not_offered", {})
                    if request.on_usage:
                        request.on_usage(ModelUsage(input_tokens=50, output_tokens=3))
                    reply = self._reply(json.dumps(self._summary(first)))
                    reply.usage = SimpleNamespace(input_tokens=100, output_tokens=7)
                    reply.tool_exchanges = [
                        {"name": "write_file", "arguments": {"path": "notes.md"},
                         "result": json.dumps(said), "error": False},
                        {"name": "not_offered", "arguments": {},
                         "result": json.dumps(refused), "error": True}]
                    return reply
                return super().invoke(request, role)

        class _Case(_Base):
            def runTest(self):
                pass

        case = _Case()
        case.setUp()
        try:
            models = _Native()
            said, row = case._done(models)
            self.assertEqual(row["status"], "done", row["log"][-6:])
            self.assertGreaterEqual(models.native, 1)
            from pathlib import Path
            folder = Path(said["folder"])
            self.assertEqual((folder / "notes.md").read_text(encoding="utf-8"), "hello")
            session = next((folder / "run_logs").glob(f"*_{said['run']}"))
            text = (session / "transcript-001.txt").read_text(encoding="utf-8")
            self.assertIn("ran during the call #1", text)
            self.assertIn("returned an error: ", text)
            self.assertIn("no tool not_offered here", text)
            # The live session's line says which tools it ran.
            events = (session / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn("Ran 2 tools (write_file, not_offered), then answers", events)
        finally:
            case.tearDown()

    def test_the_runner_keeps_the_runs_rules(self) -> None:
        from personalized_agent.agent.work.runner import Run
        held, saved, ran = [], [], []
        run = SimpleNamespace(
            stop_now=False, stopping=False, activity="",
            _hold=held.append, _going=lambda **_: True,
            owner=SimpleNamespace(save=lambda r: saved.append(1)),
            NATIVE_CALLS_PER_ROUND=Run.NATIVE_CALLS_PER_ROUND)
        tools = SimpleNamespace(call=lambda name, args: ran.append(name) or {"ok": True})
        specs = [SimpleNamespace(name="write_file")]
        go = Run._tool_runner(run, tools, specs, "worker", True, False, rounds_left=1)
        self.assertEqual(go("write_file", {}), {"ok": True})
        self.assertEqual(held, ["before write_file"])
        self.assertIn("no tool", go("run_python", {})["error"])
        for _ in range(Run.NATIVE_CALLS_PER_ROUND - 1):
            go("write_file", {})
        self.assertIn("all the room for tools", go("write_file", {})["error"])
        run.stop_now = True
        again = Run._tool_runner(run, tools, specs, "worker", True, False, rounds_left=3)
        self.assertIn("stopped", again("write_file", {})["error"])
        run.stop_now = False
        run._going = lambda **_: False
        self.assertIn("no room left", again("write_file", {})["error"])
        self.assertEqual(len(ran), Run.NATIVE_CALLS_PER_ROUND)


@unittest.skipUnless(os.environ.get("PA_TEST_CLAUDE_CODE") == "1",
                     "set PA_TEST_CLAUDE_CODE=1 to call the real Claude Code")
class ARealCallTests(unittest.TestCase):

    def test_it_calls_the_agents_tool_natively(self) -> None:
        provider = cc.ClaudeCodeProvider()
        ran = []
        request = ModelRequest(
            messages=[{"role": "user", "content": "Use run_python to compute 42*101, "
                                                  "then reply with only the number it printed."}],
            tools=[SPEC], tool_runner=lambda name, args: ran.append(name) or
            {"stdout": "4242\n"})
        response = provider.invoke(request, "haiku")
        self.assertEqual(ran, ["run_python"])
        self.assertEqual(response.tool_calls, [])
        self.assertIn("4242", response.text)
        self.assertEqual(response.tool_exchanges[0]["name"], "run_python")


if __name__ == "__main__":
    unittest.main()
