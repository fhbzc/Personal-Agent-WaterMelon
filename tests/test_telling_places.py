"""Only the places that say which work it was group states: a folder, a
document, a page deep in a site that is not visited for everything. A
window's title, an application, a site's front page do not - they turn up
beside every kind of work."""

from __future__ import annotations

import unittest

import support  # noqa: F401

from personalized_agent.heartbeat.states import heartbeat as hb


class WhichPlacesTellTests(unittest.TestCase):

    def test_folders_documents_and_deep_pages_only(self) -> None:
        places = ["c:/users/ana/projects/greenhouse_sensors",
                  "/users/ana/notes/orchard",
                  "budget for the orchard.xlsx",
                  "allotment.example.org/plots/14/rota",
                  "allotment.example.org",
                  "allotment.example.org/plots",
                  "Seed Catalogue - Web Browser",
                  "web browser",
                  "Chat with Bea"]
        kept = hb._telling(places, {"allotment.example.org": {"allotment.example.org/plots/14/rota"}})
        self.assertEqual(kept, ["c:/users/ana/projects/greenhouse_sensors",
                                "/users/ana/notes/orchard",
                                "budget for the orchard.xlsx",
                                "allotment.example.org/plots/14/rota"])

    def test_a_site_for_everything_is_no_place(self) -> None:
        pages = {f"search.example.com/results/q{n}" for n in range(hb.PAGES_OF_A_SITE_FOR_EVERYTHING)}
        self.assertEqual(hb._telling(["search.example.com/results/q1"],
                                     {"search.example.com": pages}), [])
        fewer = set(list(pages)[:3])
        self.assertEqual(hb._telling(["search.example.com/results/q1"],
                                     {"search.example.com": fewer}),
                         ["search.example.com/results/q1"])

    def test_changing_it_never_regroups(self) -> None:
        """How places are read is no setting of the tree: changing it once
        rebuilt every task, and merged twelve into one
        (test_no_regroup_on_upgrade.py)."""
        from support import TemporaryAgent
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        self.assertNotIn("places_read_as", agent.app.state_heartbeat._shape())


if __name__ == "__main__":
    unittest.main()
