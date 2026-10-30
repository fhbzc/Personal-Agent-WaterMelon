"""THE FULL REVIEW, ASKED FOR BY THE REVIEWER - AND WHAT THE USER DID NOT SAY
(the user's decisions, 2026-10-18).

A full review covers everything since the last checkpoint and, passed, is the
next one; when many cards were touched it is an answer a card and one that
concludes. What the user would expect and did not say is added - by the
reviewer with the contract, by the work agent as it finds it, the reviewer
deciding - each shown as added, not theirs, and theirs to take out; never a
core promise of its own. A card queued on a deliverable is a part of it,
never a deliverable of its own. No model is called; the work is made up."""

from __future__ import annotations

import json
import time
import unittest
from unittest import mock

from support import TemporaryAgent
from test_work_runs import _TwoAgents


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.unit = {"id": "task-chk001", "name": "Write notes", "kind": "task", "why": ""}

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _run(self, models):
        self.app.work.models = models
        started = self.app.work.start(self.unit, "write the notes")
        self.assertNotIn("error", started, started)
        for _ in range(600):
            runs = self.app.work.runs(self.unit["id"])
            if runs and not runs[0]["alive"] and runs[0]["status"] != "running":
                break
            time.sleep(0.1)
        return self.app.work.run(started["run"])


class _Seen(_TwoAgents):
    """What each reviewer call was asked, as it was asked."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.reviews = []

    def invoke(self, request, role=""):
        if "You review a work agent" in (request.system or ""):
            last = request.messages[-1]["content"]
            self.reviews.append(last if isinstance(last, str) else json.dumps(last))
        return super().invoke(request, role)


class TheFullReviewTests(_Base):

    def test_hints_and_everything_since_the_last_checkpoint(self) -> None:
        # A full review only at a card done: the card done each round.
        models = _Seen(rounds_to_complete=3, full_review="always", card_done_each=True)
        run = self._run(models)
        self.assertEqual(run["status"], "done", run["log"][-6:])
        rounds = [one for one in models.reviews if "REVIEW THIS ROUND" in one]
        # What the application sees, for the reviewer to decide by.
        self.assertIn("WHERE THE WORK STANDS, AS THE APPLICATION SEES IT", rounds[0])
        self.assertIn("the last checkpoint: the contract approved", rounds[0])
        self.assertIn("the last checkpoint: the full review after round 1", rounds[1])
        full = [one for one in models.reviews if "DO THE FULL REVIEW NOW" in one]
        # One at each card done, as asked - and the final one, at the end,
        # on the tester's run.
        self.assertEqual(len(full), 4)
        self.assertIn("the final full review", full[3])
        # Each covers the rounds since the one before it - not all of them.
        self.assertIn("CHECKPOINT (the contract approved)", full[0])
        self.assertIn("  1. The notes", full[0])
        self.assertIn("  2. The notes", full[1])
        self.assertNotIn("  1. The notes", full[1])
        self.assertIn("notes.md", full[0], "the files changed since it")
        self.assertEqual(run["plan"]["checkpoint"]["label"], "the full review after round 3")
        self.assertEqual([one["passed"] for one in run["plan"]["full_reviews"]],
                         [True, True, True, True])

    def test_many_cards_an_answer_each_and_one_that_concludes(self) -> None:
        from personalized_agent.agent.work import runner
        models = _Seen(full_review="milestone")
        with mock.patch.object(runner, "FULL_REVIEW_CARDS_AT_ONCE", 0):
            run = self._run(models)
        self.assertEqual(run["status"], "done", run["log"][-6:])
        asked = [one for one in models.reviews if "Check this card, one of those" in one]
        self.assertEqual(len(asked), 1)
        self.assertIn("DO THE FULL REVIEW NOW", asked[0], "the first card, with the overview")
        self.assertTrue(any("Every card the rounds touched is checked" in one
                            for one in models.reviews))
        kinds = [one["kind"] for one in self.app.work.versions(run["id"])["versions"]]
        self.assertEqual(kinds, ["contract", "full review", "finished"])


class _Needs(_TwoAgents):
    """A contract with a core promise; its review expects three things the
    user did not say; the work agent finds a fourth, and the reviewer adds
    it; and a finding queued on the deliverable."""

    def invoke(self, request, role=""):
        first = request.messages[0]["content"]
        first = first if isinstance(first, str) else json.dumps(first)
        last = request.messages[-1]["content"]
        said = last if isinstance(last, str) else json.dumps(last)
        reviewer = "You review a work agent" in (request.system or "")
        if not reviewer and "DRAFT THE CONTRACT" in first:
            self.asked.append(request)
            return self._reply(json.dumps({
                "goal": "notes.md says hello", "scope": ["the notes"], "out_of_scope": [],
                "core_promises": [{"id": "P1", "promise": "the notes say hello",
                                   "from_words": "write the notes",
                                   "real_condition": "open notes.md", "fails_if": []}],
                "deliverables": [{"ref": "d1", "title": "The notes", "type": "document",
                                  "statement": "notes.md says hello", "why": "asked",
                                  "tier": "core", "promises": ["P1"]}]}))
        if reviewer and "REVIEW THIS CONTRACT" in said:
            self.asked.append(request)
            return self._reply(json.dumps({
                "verdict": "approve", "comments": [],
                "deliverables": [{"ref": "d1", "criteria": [
                    {"text": "notes.md says hello", "because": ["own judgement"],
                     "backed_by": "check"}], "approach": "plainly"}],
                "expected": [
                    {"need": "notes.md opens in any text editor", "kind": "implied",
                     "promise": "P1", "because": ["write the notes"]},
                    {"need": "a dated heading", "kind": "needed",
                     "because": ["stage_x: dates every note"],
                     "deliverable": {"title": "A dated heading", "type": "document",
                                     "statement": "notes.md starts with today's date"}},
                    {"need": "a line about the weather", "kind": "nice", "because": []}]}))
        if not reviewer and "WORK ONE ROUND" in first and (
                "FAILED THE CHECKS" in said or isinstance(last, list)):
            self.asked.append(request)
            summary = self._summary(first)
            summary["needs"] = [{"need": "a copy for the printer", "kind": "needed",
                                 "saw": ["notes/print.md, which the user keeps"]},
                                {"need": "an emoji", "kind": "nice", "saw": []}]
            return self._reply(json.dumps(summary))
        if reviewer and "REVIEW THIS ROUND" in said:
            self.asked.append(request)
            self.reviewed_rounds += 1
            done = self.reviewed_rounds >= 2
            return self._reply(json.dumps({
                "promises": [{"id": "P1", "holds": "yes", "tried": "opened notes.md"}],
                "findings": [] if done else [
                    {"say": "a summary line would help", "type": "queued",
                     "card": {"title": "A summary line", "statement": "notes.md ends "
                              "with a one-line summary"}}],
                "progress": "advanced", "card_done": done, "complete": done,
                "needs": [{"n": 0, "verdict": "add", "kind": "needed", "why": "they print"},
                          {"n": 1, "verdict": "reject"}] if not done else [],
                "full_review": {"now": done, "why": "the notes are done"}}))
        return super().invoke(request, role)


class WhatTheUserDidNotSayTests(_Base):

    def test_added_shown_and_taken_out(self) -> None:
        run = self._run(_Needs())
        self.assertEqual(run["status"], "done", run["log"][-6:])
        contract = run["plan"]["contract"]
        added = {one["need"]: one for one in contract["added"]}
        self.assertEqual(set(added), {"notes.md opens in any text editor", "a dated heading",
                                      "a line about the weather", "a copy for the printer"})
        # Implied: a failure its core promise must survive - never a promise.
        self.assertEqual([one["id"] for one in contract["promises"]], ["P1"])
        self.assertIn("notes.md opens in any text editor (added by the reviewer, not your "
                      "words)", contract["promises"][0]["fails_if"])
        # Needed and nice: a supporting and a polish deliverable.
        tiers = run["plan"]["tiers"]
        self.assertEqual(tiers[added["a dated heading"]["card"]]["tier"], "supporting")
        self.assertEqual(tiers[added["a line about the weather"]["card"]]["tier"], "polish")
        # The work agent's, as the reviewer decided: one added, one not.
        self.assertEqual(added["a copy for the printer"]["by"], "the work agent")
        self.assertEqual(added["a copy for the printer"]["because"],
                         ["notes/print.md, which the user keeps"])
        self.assertNotIn("an emoji", added)
        # A finding queued on the deliverable: a part of it, not one of its own.
        tree = self.app.work.tree(self.unit)
        cards = {one["title"]: one for one in tree["cards"]}
        self.assertEqual(cards["A summary line"]["parent_id"], cards["The notes"]["id"])
        # Shown as added, not theirs - and theirs to take out.
        from personalized_agent.api.routes import build_routes
        routes = build_routes(self.app)
        shown = self.app.work.runs(self.unit["id"])[0]["contract_in_force"]
        self.assertEqual(len(shown["added"]), 4)
        self.assertEqual(shown["run"], run["id"])

        class _Request:
            def __init__(self, body):
                self.body = body

            def get(self, key, default=None):
                return self.body.get(key, default)

            def require(self, key):
                return self.body[key]

        heading = added["a dated heading"]
        # (The test's project is no project of the record's: taken out by
        # the work's own call, as the route makes it.)
        said = self.app.work.take_out(run["id"], self.unit, heading["id"])
        self.assertEqual(said["taken_out"], heading["id"])
        again = self.app.work.run(run["id"])
        self.assertTrue([one for one in again["plan"]["contract"]["added"]
                         if one["id"] == heading["id"]][0]["taken_out"])
        tree = self.app.work.tree(self.unit)
        card = [one for one in tree["cards"] if one["id"] == heading["card"]][0]
        self.assertEqual(card["status"], "set_aside")
        implied = added["notes.md opens in any text editor"]
        self.app.work.take_out(run["id"], self.unit, implied["id"])
        self.assertEqual(self.app.work.run(run["id"])["plan"]["contract"]["promises"][0]
                         ["fails_if"], [])
        shown = self.app.work.runs(self.unit["id"])[0]["contract_in_force"]
        self.assertEqual(len(shown["added"]), 2)
        self.assertIn("error", self.app.work.take_out(run["id"], self.unit, heading["id"]))
        from personalized_agent.api.errors import ApiError
        with self.assertRaises(ApiError):
            routes[("POST", "/api/work/contract/take_out")](
                _Request({"run": "run_none", "added": heading["id"]}))

    def test_the_page_shows_them_and_leaves_their_words_untranslated(self) -> None:
        from pathlib import Path
        web = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
        script = (web / "app.js").read_text(encoding="utf-8")
        self.assertIn("xpAddedBlock(c)", script)
        self.assertIn('api.post("/api/work/contract/take_out"', script)
        self.assertIn(".xp-added-words", (web / "i18n.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
