"""The web search the agents and Connections use when the model has none of
its own: DuckDuckGo's page - results as title, address and snippet; its
check for a human said to be what it is, not taken for no results (an agent
took it so, and put addresses together from memory). Nothing to set: no key,
no route (the user, 2026-10-30).

Against a server of the test's own, on this computer."""

from __future__ import annotations

import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from personalized_agent.agent.work import tools as tools_module

_RESULTS = (
    '<div class="result"><a rel="nofollow" class="result__a" '
    'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2Flibrary%2Fargparse.html'
    '&amp;rut=x">argparse &mdash; <b>Parser</b></a>'
    '<a class="result__snippet" href="#">The <b>argparse</b> module makes it easy</a></div>')


class _Engine(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *args):
        pass

    def _send(self, code, body):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        type(self).seen.append(self.path)
        if "robot" in self.path:
            self._send(202, "<html>Unfortunately, bots use DuckDuckGo too.</html>")
        else:
            self._send(200, "<html>" + _RESULTS + "</html>")


class TheWebSearchTests(unittest.TestCase):

    def setUp(self) -> None:
        _Engine.seen = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Engine)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        patcher = mock.patch.object(tools_module, "DUCKDUCKGO_ENDPOINT", base + "/ddg")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_results_from_duckduckgos_page(self) -> None:
        said = tools_module.search("python argparse")
        self.assertEqual(said["results"], [{
            "title": "argparse \u2014 Parser",
            "url": "https://docs.python.org/3/library/argparse.html",
            "snippet": "The argparse module makes it easy"}])
        self.assertIn("q=python+argparse", _Engine.seen[-1])

    def test_a_check_for_a_human_said_as_such(self) -> None:
        said = tools_module.search("robot")
        self.assertIn("asked for a human check", said["error"])
        self.assertNotIn("results", said)

    def test_nothing_to_set(self) -> None:
        """No key is read or kept, and Settings has no search route."""
        for gone in ("BRAVE_KEY", "configure_search", "search_key", "search_status"):
            self.assertFalse(hasattr(tools_module, gone), gone)
        from support import TemporaryAgent
        from personalized_agent.api.routes import build_routes
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        routes = build_routes(agent.app)
        self.assertFalse([one for one in routes if one[1].startswith("/api/search")])


if __name__ == "__main__":
    unittest.main()
