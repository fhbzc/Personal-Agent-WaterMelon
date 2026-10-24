"""THE REVIEWER CHOOSES THE REFERENCES (the user, 2026-10-24): a call of its
own proposes, for each deliverable, past work of the user's - what each is,
why it fits, what might be taken; the reviewer, in its own context, keeps
those the work agent is to look at, with what to take and what not, and
only one proposed for that deliverable. It judges the user's past work as
evidence, not a standard. And a contract is shown it whole: one cut at
12,000 characters hid its last deliverable (try 4)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401

from personalized_agent.agent.work import runner
from personalized_agent.learning import references
from personalized_agent.learning.leave_out import LeaveOut


class TheReferencesTests(unittest.TestCase):

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.readme = self.root / "README.md"
        self.readme.write_text("# Hub\n\n## Quick start\n", encoding="utf-8")
        self.store = self.root / "store.py"
        self.store.write_text("ACCOUNTS = {}\n", encoding="utf-8")
        self.other = self.root / "other.md"
        self.other.write_text("other", encoding="utf-8")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_proposed_then_chosen_of_those_proposed(self) -> None:
        cards = [{"id": "card_1", "title": "README"}, {"id": "card_2", "title": "Accounts"}]
        proposed = references.checked({"cards": {
            "card_1": [{"path": str(self.readme), "summary": "The hub's README.",
                        "why": "the same kind of tool", "take": "Its quick start."}],
            "card_2": [{"path": str(self.store), "summary": "Accounts in JSON files.",
                        "why": "the same accounts", "take": "Its password hashing."}]}},
            cards, lambda path: True, LeaveOut(), most=references.PROPOSED)
        text = references.proposed_text(cards, proposed)
        self.assertIn("what it is: The hub's README.", text)
        self.assertIn("why it fits: the same accounts", text)
        chosen = references.checked({"cards": {
            "card_1": [{"path": str(self.other), "take": "Not proposed."}],
            "card_2": [{"path": str(self.store), "take": "Its password hashing.",
                        "not": "Its JSON files: keep accounts in SQLite."}]}},
            cards, lambda path: True, LeaveOut(), among=proposed)
        self.assertEqual(chosen["card_1"], [], "only one proposed for that deliverable")
        self.assertEqual(references.shown(chosen["card_2"]),
                         [f"    look at: {self.store} - Its password hashing. "
                          "Not: Its JSON files: keep accounts in SQLite."])

    def test_the_reviewer_judges_past_work_as_evidence(self) -> None:
        self.assertIn("THE USER'S PAST WORK IS EVIDENCE, NOT A STANDARD", runner.REVIEWER_SYSTEM)
        self.assertIn("what not to take", runner.REVIEW_REFERENCES)

    def test_a_contract_shown_whole(self) -> None:
        draft = {"goal": "g", "deliverables": [{"ref": f"d{n}", "statement": "s" * 1500}
                                               for n in range(1, 10)]}
        shown = runner._shown_whole(draft)
        self.assertGreater(len(shown), 12_000)
        self.assertEqual(json.loads(shown), draft, "whole")
        huge = "x" * (runner.CONTRACT_SHOWN + 5)
        self.assertIn("(cut here: 5 characters more)", runner._shown_whole(huge), "never cut unsaid")


if __name__ == "__main__":
    unittest.main()
