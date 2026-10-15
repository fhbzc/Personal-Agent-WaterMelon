"""The services that speak the OpenAI chat API - OpenAI, Gemini, DeepSeek,
OpenRouter and the rest - one provider class, one instance each, and one
for any address the user gives.

Against a server of the test's own, on this computer: the conversation goes
out in the chat API's shape - tools as functions, an answer's tool calls,
each result a tool message - and comes back in the shape the rest of the
application keeps (text and tool_use blocks); what was read and written is
counted, the cached part apart; a failure says whether the user must mend
it (the key, credit, a model) or it passes (a rate limit, the service
down); a key that is turned away is not kept."""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from personalized_agent.models.base import (AvailabilityState, CredentialState,
                                            FailureClass, ModelInvocationError,
                                            ModelRequest, ToolSpec)
from personalized_agent.models.providers.openai_compatible import (
    PRESETS, OpenAICompatibleProvider, Preset, to_chat)


class _Credentials:
    def __init__(self):
        self.kept = {}

    def get(self, key):
        return self.kept.get(key)

    def set(self, key, value):
        self.kept[key] = value

    def has(self, key):
        return key in self.kept

    def delete(self, key):
        self.kept.pop(key, None)


class _Service(BaseHTTPRequestHandler):
    """A chat API: answers as `reply` says, and keeps what it was sent."""

    seen: list = []
    reply: dict = {}

    def log_message(self, *args):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        if code == 429:
            self.send_header("Retry-After", "7")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        type(self).seen.append(("GET", self.path, self.headers.get("Authorization"), None))
        if self.headers.get("Authorization") != "Bearer good-key":
            self._send(401, {"error": {"message": "Incorrect API key provided"}})
            return
        self._send(200, {"data": [{"id": "model-b"}, {"id": "model-a", "context_length": 8000}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append(("POST", self.path, self.headers.get("Authorization"), body))
        code, said = type(self).reply.get("next") or (200, {})
        if callable(said):
            code, said = said(body)
        self._send(code, said)


class OpenAICompatibleTests(unittest.TestCase):

    def setUp(self) -> None:
        _Service.seen = []
        _Service.reply = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Service)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        self.credentials = _Credentials()
        self.provider = OpenAICompatibleProvider(
            self.credentials, Preset("testapi", "Test API", self.url, ("TEST_API_KEY_NOT_SET",)))

    def _keyed(self):
        self.credentials.set("testapi.api_key", "good-key")
        return self.provider

    def test_every_service_is_a_provider(self) -> None:
        from support import TemporaryAgent
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        ids = {one["provider"]: one for one in agent.app.models.describe()["providers"]}
        for wanted in ("openrouter", "openai", "google", "deepseek", "moonshot", "qwen",
                       "zhipu", "xai", "mistral", "groq", "siliconflow", "together",
                       "custom_openai"):
            self.assertEqual(ids[wanted]["kind"], "api", wanted)
        self.assertEqual(ids["claude_code"]["kind"], "client")
        self.assertEqual(ids["codex"]["kind"], "client")
        self.assertEqual(ids["anthropic"]["kind"], "api")
        self.assertTrue(ids["custom_openai"]["needs_base_url"])
        self.assertEqual(ids["openrouter"]["default_base_url"], "https://openrouter.ai/api/v1")
        self.assertTrue(all(one.base_url.startswith("https://") for one in PRESETS
                            if one.provider_id != "custom_openai"))

    def test_a_key_is_tried_and_kept_only_when_it_works(self) -> None:
        said = self.provider.authenticate({"api_key": "bad-key"})
        self.assertEqual(said.state, CredentialState.INVALID)
        self.assertFalse(self.credentials.has("testapi.api_key"), "a key turned away is not kept")
        said = self.provider.authenticate({"api_key": "good-key"})
        self.assertEqual(said.state, CredentialState.AUTHENTICATED)
        self.assertEqual(self.provider.get_auth_status().state, CredentialState.AUTHENTICATED)
        self.assertEqual([one.id for one in self.provider.list_models()], ["model-a", "model-b"])
        self.assertTrue(self.provider.accepts_model_id("not-listed-yet"))
        self.provider.logout()
        self.assertEqual(self.provider.get_auth_status().state, CredentialState.NOT_CONFIGURED)

    def test_a_service_at_an_address_of_yours(self) -> None:
        custom = OpenAICompatibleProvider(self.credentials, next(
            one for one in PRESETS if one.provider_id == "custom_openai"))
        self.assertEqual(custom.get_auth_status().state, CredentialState.NOT_CONFIGURED)
        self.assertEqual(custom.authenticate({"api_key": "good-key"}).state,
                         CredentialState.INVALID, "no address yet")
        self.assertEqual(custom.authenticate({"api_key": "good-key", "base_url": "ftp://x"}).state,
                         CredentialState.INVALID)
        said = custom.authenticate({"api_key": "good-key", "base_url": self.url + "/"})
        self.assertEqual(said.state, CredentialState.AUTHENTICATED)
        self.assertEqual(custom.base_url(), self.url)

    def test_the_conversation_out_and_the_answer_back(self) -> None:
        _Service.reply["next"] = (200, {
            "model": "model-a",
            "choices": [{"finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": "Reading it.",
                "tool_calls": [{"id": "call_9", "type": "function", "function": {
                    "name": "read_file", "arguments": "{\"path\": \"notes.md\"}"}}]}}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 30,
                      "prompt_tokens_details": {"cached_tokens": 1000}}})
        request = ModelRequest(system="Be brief.", max_tokens=500, tools=[
            ToolSpec("read_file", "Read a file", {"type": "object", "properties": {
                "path": {"type": "string"}}})], messages=[
            {"role": "user", "content": "Read the notes"},
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "mine"},
                {"type": "text", "text": "First the list."},
                {"type": "tool_use", "id": "call_1", "name": "list_dir", "input": {"path": "."}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_1",
                 "content": [{"type": "text", "text": "notes.md"},
                             {"type": "image", "source": {"type": "base64",
                                                          "media_type": "image/png",
                                                          "data": "AAAA"}}]},
                {"type": "text", "text": "Go on"}]}])
        said = self._keyed().invoke(request, "model-a")
        method, path, auth, body = _Service.seen[-1]
        self.assertEqual((method, path, auth), ("POST", "/v1/chat/completions", "Bearer good-key"))
        self.assertEqual(body["max_tokens"], 500)
        self.assertEqual(body["tools"][0]["function"]["name"], "read_file")
        roles = [one["role"] for one in body["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "tool", "user"])
        self.assertEqual(body["messages"][2]["tool_calls"][0]["function"]["arguments"],
                         "{\"path\": \".\"}")
        self.assertEqual(body["messages"][2]["content"], "First the list.")
        self.assertNotIn("mine", json.dumps(body), "the model's thinking is not sent back")
        self.assertEqual(body["messages"][3], {"role": "tool", "tool_call_id": "call_1",
                                               "content": "notes.md"})
        # The image a tool returned, after the tool's message, from the user.
        self.assertEqual(body["messages"][4]["content"][1]["image_url"]["url"],
                         "data:image/png;base64,AAAA")
        # Back in the application's own shape.
        self.assertEqual(said.text, "Reading it.")
        self.assertEqual([(c.id, c.name, c.arguments) for c in said.tool_calls],
                         [("call_9", "read_file", {"path": "notes.md"})])
        self.assertEqual(said.raw_content[1], {"type": "tool_use", "id": "call_9",
                                               "name": "read_file",
                                               "input": {"path": "notes.md"}})
        self.assertEqual(said.stop_reason, "tool_use")
        self.assertEqual((said.usage.input_tokens, said.usage.cached_input_tokens,
                          said.usage.output_tokens), (200, 1000, 30))
        # And the answer, sent back as the conversation goes on, is a call again.
        again = to_chat(ModelRequest(messages=[{"role": "assistant", "content": said.raw_content}]))
        self.assertEqual(again[0]["tool_calls"][0]["id"], "call_9")

    def test_a_newer_model_takes_the_other_name_for_the_limit(self) -> None:
        def answer(body):
            if "max_tokens" in body:
                return 400, {"error": {"message": "Unsupported parameter: 'max_tokens'. Use "
                                                  "'max_completion_tokens' instead."}}
            return 200, {"choices": [{"message": {"content": "ok"}}], "usage": {}}
        _Service.reply["next"] = (200, answer)
        said = self._keyed().invoke(ModelRequest(messages=[{"role": "user", "content": "hi"}]),
                                    "model-a")
        self.assertEqual(said.text, "ok")
        self.assertIn("max_completion_tokens", _Service.seen[-1][3])
        self.provider.invoke(ModelRequest(messages=[{"role": "user", "content": "hi"}]), "model-a")
        self.assertIn("max_completion_tokens", _Service.seen[-1][3], "remembered")

    def test_how_hard_it_thinks_is_sent_where_the_model_takes_it(self) -> None:
        from personalized_agent.models.providers.openai_compatible import efforts_for
        self.assertEqual(efforts_for("openai", "gpt-5.1"), ["minimal", "low", "medium", "high"])
        self.assertEqual(efforts_for("openai", "o3-mini"), ["low", "medium", "high"])
        self.assertEqual(efforts_for("openai", "gpt-4o"), [])
        self.assertEqual(efforts_for("google", "gemini-2.5-pro"), ["low", "medium", "high"])
        self.assertEqual(efforts_for("deepseek", "deepseek-reasoner"), [])
        self.assertEqual(efforts_for("openrouter", "openai/o3",
                                     {"supported_parameters": ["reasoning"]}),
                         ["low", "medium", "high"])
        self.assertEqual(efforts_for("openrouter", "meta/llama", {}), [])
        ok = (200, {"choices": [{"message": {"content": "ok"}}], "usage": {}})
        openai = OpenAICompatibleProvider(self.credentials, Preset("openai", "OpenAI", self.url))
        self.credentials.set("openai.api_key", "good-key")
        _Service.reply["next"] = ok
        openai.invoke(ModelRequest(messages=[{"role": "user", "content": "hi"}],
                                   reasoning_effort="high"), "o3")
        self.assertEqual(_Service.seen[-1][3]["reasoning_effort"], "high")
        # A model that takes none is not sent one.
        openai.invoke(ModelRequest(messages=[{"role": "user", "content": "hi"}],
                                   reasoning_effort="high"), "gpt-4o")
        self.assertNotIn("reasoning_effort", _Service.seen[-1][3])
        relay = OpenAICompatibleProvider(self.credentials, Preset(
            "openrouter", "OpenRouter", self.url, (), caching="marked"))
        self.credentials.set("openrouter.api_key", "good-key")
        relay._models = [__import__("personalized_agent.models.base", fromlist=["x"])
                         .ModelDescriptor(id="openai/o3", display_name="o3",
                                          reasoning_efforts=["low", "medium", "high"])]
        relay.invoke(ModelRequest(messages=[{"role": "user", "content": "hi"}],
                                  reasoning_effort="low"), "openai/o3")
        self.assertEqual(_Service.seen[-1][3]["reasoning"], {"effort": "low"})

    def test_a_repeated_prompt_is_cached_where_the_service_offers_it(self) -> None:
        _Service.reply["next"] = (200, {"choices": [{"message": {"content": "ok"}}],
                                        "usage": {"prompt_tokens": 5000, "completion_tokens": 9,
                                                  "prompt_tokens_details": {
                                                      "cached_tokens": 4000,
                                                      "cache_write_tokens": 600}}})
        relay = OpenAICompatibleProvider(self.credentials, Preset(
            "openrouter", "OpenRouter", self.url, (), caching="marked"))
        self.credentials.set("openrouter.api_key", "good-key")
        request = ModelRequest(system="The rules.", messages=[
            {"role": "user", "content": "Do it"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": "x",
                                               "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1",
                                          "content": "done"}]}])
        said = relay.invoke(request, "anthropic/claude-sonnet-4.5")
        body = _Service.seen[-1][3]
        self.assertEqual(body["messages"][0]["content"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(body["messages"][-1]["role"], "tool")
        self.assertEqual(body["messages"][-1]["content"][0]["cache_control"],
                         {"type": "ephemeral"})
        self.assertEqual(body["usage"], {"include": True})
        # Counted apart: read from the cache, written to it, and new.
        self.assertEqual((said.usage.input_tokens, said.usage.cached_input_tokens,
                          said.usage.cache_written_tokens), (400, 4000, 600))
        # A model the service caches on its own: nothing marked.
        relay.invoke(request, "openai/gpt-5")
        self.assertNotIn("cache_control", json.dumps(_Service.seen[-1][3]))
        self.assertEqual(self._keyed().capability_model()["caching"], "auto")
        # Marks refused: sent without them, and not asked for again.

        def refuse(body):
            if "cache_control" in json.dumps(body):
                return 400, {"error": {"message": "Unknown field: cache_control"}}
            return 200, {"choices": [{"message": {"content": "plain"}}], "usage": {}}
        _Service.reply["next"] = (200, refuse)
        self.assertEqual(relay.invoke(request, "anthropic/claude-sonnet-4.5").text, "plain")
        relay.invoke(request, "anthropic/claude-sonnet-4.5")
        self.assertNotIn("cache_control", json.dumps(_Service.seen[-1][3]))

    def test_what_a_failure_asks_for(self) -> None:
        self._keyed()
        cases = [
            (401, {"error": {"message": "bad key"}}, AvailabilityState.CREDENTIAL_INVALID,
             FailureClass.USER_ACTION_REQUIRED),
            (402, {"error": {"message": "Insufficient credits"}},
             AvailabilityState.QUOTA_EXHAUSTED, FailureClass.USER_ACTION_REQUIRED),
            (429, {"error": {"type": "insufficient_quota", "message": "You exceeded your quota"}},
             AvailabilityState.QUOTA_EXHAUSTED, FailureClass.USER_ACTION_REQUIRED),
            (429, {"error": {"message": "Rate limit reached"}}, AvailabilityState.RATE_LIMITED,
             FailureClass.TEMPORARY),
            (404, {"error": {"message": "model not found"}}, AvailabilityState.MODEL_UNAVAILABLE,
             FailureClass.USER_ACTION_REQUIRED),
            (503, {"error": {"message": "overloaded"}}, AvailabilityState.PROVIDER_UNAVAILABLE,
             FailureClass.TEMPORARY),
            # A relay that answers 200 with the error inside.
            (200, {"error": {"message": "upstream overloaded", "code": 502}},
             AvailabilityState.PROVIDER_UNAVAILABLE, FailureClass.TEMPORARY),
        ]
        from personalized_agent.agent.work.runner import model_trouble
        for code, body, state, failure in cases:
            _Service.reply["next"] = (code, body)
            with self.assertRaises(ModelInvocationError) as caught:
                self.provider.invoke(ModelRequest(messages=[{"role": "user", "content": "hi"}]),
                                     "model-a")
            self.assertEqual((caught.exception.availability.state,
                              caught.exception.availability.failure_class), (state, failure),
                             (code, body))
        # Out of credit only paying mends; a rate limit passes.
        _Service.reply["next"] = (402, {"error": {"message": "Insufficient credits"}})
        with self.assertRaises(ModelInvocationError) as caught:
            self.provider.invoke(ModelRequest(messages=[{"role": "user", "content": "hi"}]),
                                 "model-a")
        self.assertEqual(model_trouble(caught.exception), "yours")
        self.server.shutdown()
        self.server.server_close()
        with self.assertRaises(ModelInvocationError) as caught:
            self.provider.invoke(ModelRequest(messages=[{"role": "user", "content": "hi"}]),
                                 "model-a")
        self.assertEqual(caught.exception.availability.failure_class, FailureClass.TEMPORARY)
        self.assertEqual(model_trouble(caught.exception), "temporary")


if __name__ == "__main__":
    unittest.main()
