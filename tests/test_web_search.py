"""The web search the agents and Connections use: Brave's Search API with
the user's key - results as title, address and snippet; a key it does not
take, or a limit reached, said as what it is - and, with no key, DuckDuckGo's
page, whose check for a human is said to be what it is, not taken for no
results (an agent took it so, and put addresses together from memory).

Against a server of the test's own, on this computer."""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from personalized_agent.agent.work import tools as tools_module


class _Engines(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *args):
        pass

    def _send(self, code, body, kind="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        type(self).seen.append((self.path, self.headers.get("X-Subscription-Token")))
        if self.path.startswith("/brave"):
            key = self.headers.get("X-Subscription-Token")
            if key == "busy-key":
                self._send(429, {"error": "rate limited"})
            elif key != "good-key":
                self._send(401, {"error": "unauthorized"})
            else:
                self._send(200, {"web": {"results": [
                    {"title": "argparse — <strong>Parser</strong>", "url": "https://docs.python.org/3/library/argparse.html",
                     "description": "The <strong>argparse</strong> module makes it easy"}]}})
        else:
            self._send(202, "<html>Unfortunately, bots use DuckDuckGo too.</html>", "text/html")


class TheWebSearchTests(unittest.TestCase):

    def setUp(self) -> None:
        _Engines.seen = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Engines)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        for name, value in (("BRAVE_ENDPOINT", base + "/brave"),
                            ("DUCKDUCKGO_ENDPOINT", base + "/ddg"),
                            ("BRAVE_SPACING", 0.0), ("_search_keys", None)):
            patcher = mock.patch.object(tools_module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        environment = mock.patch.dict("os.environ", {tools_module.BRAVE_KEY_ENV: ""})
        environment.start()
        self.addCleanup(environment.stop)

    def test_brave_with_the_users_key(self) -> None:
        said = tools_module.search("python argparse", key="good-key")
        self.assertEqual(said["engine"], "Brave Search")
        self.assertEqual(said["results"], [{
            "title": "argparse — Parser", "url": "https://docs.python.org/3/library/argparse.html",
            "snippet": "The argparse module makes it easy"}])
        path, key = _Engines.seen[-1]
        self.assertIn("q=python+argparse", path)
        self.assertEqual(key, "good-key")

    def test_a_key_not_taken_and_a_limit_said(self) -> None:
        said = tools_module.search("x", key="bad-key")
        self.assertTrue(said["unauthorized"])
        self.assertIn("not accepted", said["error"])
        said = tools_module.search("x", key="busy-key")
        self.assertIn("limit for this key is reached", said["error"])

    def test_the_stored_key_and_none(self) -> None:
        class Keys:
            kept = {}

            def get(self, key):
                return self.kept.get(key)
        keys = Keys()
        tools_module.configure_search(keys)
        self.assertEqual(tools_module.search_status()["engine"], "duckduckgo")
        # No key: DuckDuckGo's check for a human, said as such.
        said = tools_module.search("python argparse")
        self.assertIn("asked for a human check", said["error"])
        self.assertIn("No Brave Search key is set", said["error"])
        keys.kept[tools_module.BRAVE_KEY] = "good-key"
        self.assertEqual(tools_module.search_status(),
                         {"engine": "brave", "key_set": True, "from_environment": False,
                          "key_page": tools_module.BRAVE_KEY_PAGE})
        self.assertEqual(len(tools_module.search("python argparse")["results"]), 1)

    def test_the_settings_route_keeps_only_a_key_that_works(self) -> None:
        from support import TemporaryAgent
        from personalized_agent.api.errors import ApiError
        from personalized_agent.api.routes import build_routes
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        routes = build_routes(agent.app)

        class Request:
            def __init__(self, body):
                self.body = body

            def get(self, key, default=None):
                return self.body.get(key, default)

            def require(self, key):
                return self.body[key]
        with self.assertRaises(ApiError):
            routes[("POST", "/api/search/key")](Request({"api_key": "bad-key"}))
        self.assertFalse(agent.app.credentials.get(tools_module.BRAVE_KEY))
        said = routes[("POST", "/api/search/key")](Request({"api_key": "good-key"}))
        self.assertEqual(said["engine"], "brave")
        self.assertNotIn("good-key", json.dumps(said), "never shown again")
        self.assertEqual(routes[("GET", "/api/search")](Request({}))["engine"], "brave")
        said = routes[("POST", "/api/search/key/remove")](Request({}))
        self.assertEqual(said["engine"], "duckduckgo")


if __name__ == "__main__":
    unittest.main()
