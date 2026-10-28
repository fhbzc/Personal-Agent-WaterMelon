"""THE WEB GUARD (runtime/web_guard.py): what may be searched and which pages
may be opened - the five ways a page may be (the user widened it,
2026-10-18), sites made to collect what is sent to them, Claude Code's own
WebSearch and WebFetch held to the same checks through a hook that asks
the call's own server and refuses when it cannot, and the contacts' round
under the same guard. No model is called; every address is made up."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
import urllib.request
from pathlib import Path

from personalized_agent.models.base import ToolSpec
from personalized_agent.models.providers.mcp_bridge import ToolBridge
from personalized_agent.runtime.web_guard import WebGuard

HOOK = Path(__file__).resolve().parents[1] / "personalized_agent" / "runtime" / "web_hook.py"
SPEC = ToolSpec("run_python", "Run Python code", {"type": "object", "properties": {}})


def _guard() -> WebGuard:
    guard = WebGuard()
    # The user's own: their instruction names their site; their notes hold
    # a word and a number nobody else has.
    guard.know("Build the charts for https://lab.example/projects/zephyrine - see the "
               "budget 48213 in notes", "user")
    # The web: a search's results, and a page read.
    guard.know("https://news.example/2026/10/charts-today Charts today", "web", "search")
    guard.know("https://blog.example/posts/plotting?ref=feed Plotting in the browser - "
               "zephyrine 48213 altair", "web", "blog.example")
    return guard


class TheFiveWaysTests(unittest.TestCase):

    def test_a_page_is_opened_only_where_nothing_of_the_users_can_travel(self) -> None:
        guard = _guard()
        way = lambda url: guard.why_not(url)[0]
        # 1. An address that exists - or its site's front page.
        self.assertEqual(way("https://news.example/2026/10/charts-today"), "it exists")
        self.assertEqual(way("https://blog.example/"), "the front page of a known site")
        # 2. Any page of a site the user named.
        self.assertEqual(way("https://lab.example/anything/at/all?q=1"), "a site the user named")
        # 3. Any page of a reference site, its subdomains too.
        self.assertEqual(way("https://docs.python.org/3.12/library/json.html"),
                         "a reference site")
        self.assertEqual(way("https://fr.wikipedia.org/wiki/Graphique"), "a reference site")
        # 4. A known address made shorter: a level up, its query dropped,
        # http for https.
        self.assertEqual(way("https://news.example/2026/10"), "a known address, shorter")
        self.assertEqual(way("http://blog.example/posts/plotting"), "a known address, shorter")
        # 5. On a known site, a short address with nothing of the user's in it.
        self.assertEqual(way("https://news.example/2026/11/charts-tomorrow"),
                         "on a known site, nothing of the user's in it")

    def test_what_could_carry_the_users_words_out_is_refused(self) -> None:
        guard = _guard()
        refused = lambda url: guard.check_address(url) is not None
        # A word only the user's files have - though the page it goes to
        # listed it, on its own site.
        self.assertTrue(refused("https://blog.example/zephyrine"))
        # Public on another site, it is no longer theirs alone.
        guard.know("Zephyrine, a name in the news", "web", "search")
        self.assertFalse(refused("https://blog.example/zephyrine"))
        # A number of theirs, never - wherever else it was seen.
        guard.know("48213", "web", "search")
        self.assertTrue(refused("https://news.example/48213"))
        # A query, a long address, something key-like: no.
        self.assertTrue(refused("https://news.example/charts?x=1"))
        self.assertTrue(refused("https://news.example/" + "/".join(["a"] * 12)))
        self.assertTrue(refused("https://news.example/" + "Q" * 44))
        # A SITE NEVER SEEN (widened 2026-10-27): a short address with
        # nothing of theirs - but not its name made of their words, nor a
        # number, nor a query; and a few, on a few sites.
        self.assertFalse(refused("https://unknown.example/charts"))
        guard2 = _guard()
        self.assertTrue(guard2.check_address("https://zephyrine.blog.example/") is not None)
        self.assertTrue(refused("https://n48213.example/"))
        self.assertTrue(refused("https://unknown.example/charts?x=1"))
        self.assertTrue(refused("file:///C:/Users/notes.txt"))

    def test_a_collector_only_at_an_address_that_exists(self) -> None:
        guard = WebGuard()
        guard.know("send it to https://webhook.site/abc-123 as we agreed", "user")
        self.assertIsNone(guard.check_address("https://webhook.site/abc-123"))
        self.assertIn("collects", guard.check_address("https://webhook.site/other"))
        self.assertIn("collects", guard.check_address("https://x.ngrok-free.app/a"))

    def test_a_search_carries_words_not_data(self) -> None:
        guard = WebGuard()
        self.assertIsNone(guard.check_query("altair theme for every figure"))
        self.assertIn("key", guard.check_query("sk-" + "a1b2c3d4e5f6g7h8"))
        self.assertIn("at most", guard.check_query("word " * 80))


class ItsOwnWebToolsHookedTests(unittest.TestCase):
    """Claude Code's WebSearch and WebFetch: asked of the guard before they
    run, and what they found known after - through the call's own server."""

    def _post(self, url, body):
        request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                         method="POST")
        with urllib.request.urlopen(request, timeout=10) as answer:
            return json.loads(answer.read() or b"{}")

    def test_refused_before_and_known_after(self) -> None:
        guard = WebGuard()
        with ToolBridge([SPEC], lambda *_: {}, guard=guard) as bridge:
            ask = lambda payload: self._post(bridge.hook_url, payload)
            made_up = {"hook_event_name": "PreToolUse", "tool_name": "WebFetch",
                       "tool_input": {"url": "https://found.example/page?id=2", "prompt": "x"}}
            said = ask(made_up)["hookSpecificOutput"]
            self.assertEqual((said["hookEventName"], said["permissionDecision"]),
                             ("PreToolUse", "deny"))
            self.assertIn("query", said["permissionDecisionReason"])
            self.assertIn("hookSpecificOutput", ask({
                "hook_event_name": "PreToolUse", "tool_name": "WebSearch",
                "tool_input": {"query": "ghp_" + "x" * 30}}))
            # What its search found exists after it.
            self.assertEqual(ask({"hook_event_name": "PostToolUse", "tool_name": "WebSearch",
                                  "tool_input": {"query": "pages"},
                                  "tool_response": {"results": [
                                      {"url": "https://found.example/page?id=2"}]}}), {})
            # Let through by this answer alone - the client allows neither
            # tool by itself.
            said = ask(made_up)["hookSpecificOutput"]
            self.assertEqual(said["permissionDecision"], "allow")
            # Its other tools are not asked about.
            self.assertEqual(ask({"hook_event_name": "PreToolUse", "tool_name": "Read",
                                  "tool_input": {}}), {})
        # A bridge with no guard has no hook address.
        with ToolBridge([SPEC], lambda *_: {}) as bare:
            with self.assertRaises(urllib.error.HTTPError):
                self._post(bare.url.replace("/mcp", "/hook"), {})

    def test_the_hook_process_asks_and_fails_closed(self) -> None:
        guard = WebGuard()
        asked = {"hook_event_name": "PreToolUse", "tool_name": "WebFetch",
                 "tool_input": {"url": "https://found.example/secret-page?k=1"}}

        def hook(url, payload):
            return subprocess.run([sys.executable, str(HOOK), url],
                                  input=json.dumps(payload), capture_output=True,
                                  text=True, timeout=60)

        with ToolBridge([SPEC], lambda *_: {}, guard=guard) as bridge:
            said = hook(bridge.hook_url, asked)
            self.assertEqual(said.returncode, 0, said.stderr)
            self.assertEqual(json.loads(said.stdout)["hookSpecificOutput"]
                             ["permissionDecision"], "deny")
            port = bridge.hook_url
        # The server gone: refused, never let through.
        gone = hook(port, asked)
        self.assertEqual(gone.returncode, 2)
        self.assertIn("could not be asked", gone.stderr)
        self.assertEqual(hook(port, dict(asked, hook_event_name="PostToolUse")).returncode, 0)
        # Only this computer's own server is ever asked.
        self.assertEqual(hook("https://elsewhere.example/hook", asked).returncode, 2)


class TheContactsRoundIsGuardedTests(unittest.TestCase):

    def setUp(self) -> None:
        from support import TemporaryAgent
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.app.connections_round.wait(10)
        self.agent.cleanup()

    def test_a_page_and_a_picture_only_where_nothing_can_travel(self) -> None:
        from personalized_agent.heartbeat.connections.round import ConnectionsRound
        fetched = []
        round_ = ConnectionsRound(self.app.connections, None, self.app.db,
                                  search=lambda q: {"results": [
                                      {"url": "https://found.example/a", "title": "A"}]},
                                  fetch=lambda url: fetched.append(url) or {"text": "ok"})
        used = {"calls": 0, "web": 0, "tokens_in": 0, "tokens_out": 0}
        round_.guard.know("Ana Ruiz, who writes at https://ana.example/blog", "user")
        said = round_._tool("fetch_page", {"url": "https://found.example/ana-ruiz"}, used)
        self.assertIn("error", said)
        round_._tool("web_search", {"query": "Ana Ruiz"}, used)
        self.assertNotIn("error", round_._tool("fetch_page",
                                               {"url": "https://found.example/a"}, used))
        self.assertNotIn("error", round_._tool("fetch_page",
                                               {"url": "https://ana.example/talks"}, used))
        self.assertEqual(fetched, ["https://found.example/a", "https://ana.example/talks"])
        self.assertIn("key", round_._tool("web_search", {"query": "AKIA" + "Z" * 16},
                                          used)["error"])
        # A picture the model names is a page asked for: held to the same.
        tried = []
        round_.download = lambda url: tried.append(url) or (b"", "text/plain")
        round_._pictured([{"title": "x", "image_url": "https://pix.example/ana-ruiz.jpg",
                           "sources": ["https://found.example/a"]}], lambda: True)
        self.assertIn("https://found.example/a", tried, "the pictures were looked for")
        self.assertNotIn("https://pix.example/ana-ruiz.jpg", tried)


class PublicCodeAndDataTests(unittest.TestCase):
    """Where public files, schemas and data live (the user, 2026-10-27: a work
    could not fetch the CFF 1.2.0 schema): reference sites - none shows a
    repository's owner who asked for what. Not the pages users host there."""

    def test_a_file_made_up_there_is_opened(self) -> None:
        guard = WebGuard()
        for url in ("https://raw.githubusercontent.com/citation-file-format/"
                    "citation-file-format/1.2.0/schema.json",
                    "https://github.com/citation-file-format/citation-file-format",
                    "https://files.pythonhosted.org/packages/source/r/requests/x.tar.gz",
                    "https://cdn.jsdelivr.net/npm/marked@12.0.0/marked.min.js",
                    "https://zenodo.org/records/1234567"):
            with self.subTest(url=url):
                self.assertIsNone(guard.check_address(url))

    def test_not_the_pages_their_users_host_nor_a_collector(self) -> None:
        guard = WebGuard()
        # Not reference sites: what they may open is what any site may.
        for url in ("https://someone.github.io/page/notes",
                    "https://example-unknown.org/data.json"):
            with self.subTest(url=url):
                self.assertNotEqual(guard.why_not(url)[0], "a reference site")
        self.assertIsNotNone(guard.check_address("https://someone.github.io/notes?k=1"))
        self.assertIsNotNone(guard.check_address("https://webhook.site/abc-def"))


class MadeUpOnAnySiteTests(unittest.TestCase):
    """An address made up on a site not seen (2026-10-27): a few on each,
    a few such sites, its name held to the address's checks."""

    def test_a_few_on_each_and_a_few_sites(self) -> None:
        from personalized_agent.runtime import web_guard as wg
        guard = WebGuard()
        for n in range(wg.MOST_MADE_UP_PER_SITE):
            self.assertIsNone(guard.check_address(f"https://weather.example/day{n}"))
        self.assertIsNone(guard.check_address("https://weather.example/day0"), "again: one")
        said = guard.check_address("https://weather.example/day-more")
        self.assertIn("made up on weather.example already", said)
        for n in range(wg.MOST_NEW_SITES - 1):
            self.assertIsNone(guard.check_address(f"https://site{chr(97 + n % 26)}{n // 26}x.example/"))
        self.assertIn("sites not seen", guard.check_address("https://onemore.example/"))

    def test_what_was_looked_up_is_kept(self) -> None:
        guard = WebGuard()
        guard.note("search", "chicago weather")
        guard.hook({"hook_event_name": "PreToolUse", "tool_name": "WebFetch",
                    "tool_input": {"url": "https://www.weather.gov/lot/"}})
        self.assertEqual(guard.looked, [{"how": "search", "what": "chicago weather"},
                                        {"how": "read", "what": "https://www.weather.gov/lot/"}])

    def test_sites_a_clients_own_search_found_are_seen(self) -> None:
        guard = WebGuard()
        guard.know("zephyrine is in my notes", "user")
        guard.found(["www.fox32chicago.com"], "chicago weather forecast rain")
        # Its front page, and a short page on it, as on any known site.
        self.assertEqual(guard.why_not("https://www.fox32chicago.com/")[0],
                         "the front page of a known site")
        self.assertEqual(guard.why_not("https://www.fox32chicago.com/weather")[0],
                         "on a known site, nothing of the user's in it")
        self.assertIsNotNone(guard.check_address("https://www.fox32chicago.com/zephyrine"))


if __name__ == "__main__":
    unittest.main()
