"""Each story the agent recommends begins with how it ties to what the user
is doing - asked of every prompt that writes stories, and kept with them."""

from __future__ import annotations

import unittest

import support  # noqa: F401

from personalized_agent.heartbeat.connections import recommend, round as connections


class AskedAndKeptTests(unittest.TestCase):

    def test_every_prompt_that_writes_stories_asks_for_it(self) -> None:
        for name, prompt in (("people", connections.PLAN), ("web", connections.WEB_PLAN),
                             ("the list", connections.UPDATE),
                             ("a page", recommend.PAGE_PLAN)):
            self.assertIn('"opening"', prompt, name)
            self.assertIn("never a label", prompt, name)
            # Still a template that fills in.
            self.assertIn("{", prompt)

    def test_kept_with_the_story_and_empty_is_none(self) -> None:
        fake = type("R", (), {"store": type("S", (), {"get": lambda self, p: None})()})()
        kept = connections.ConnectionsRound._items(fake, [
            {"title": "Ten-slide talks", "opening": "  While you trim the soil talk,  ",
             "detail": "two groups cut theirs.", "kind": "web"},
            {"title": "Kiln prices", "opening": "  ", "detail": "Plain.", "kind": "web"}])
        by_title = {one["title"]: one for one in kept}
        self.assertEqual(by_title["Ten-slide talks"]["opening"], "While you trim the soil talk,")
        self.assertNotIn("opening", by_title["Kiln prices"])


if __name__ == "__main__":
    unittest.main()
