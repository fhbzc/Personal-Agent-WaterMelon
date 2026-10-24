"""THE CASES ARE CHOSEN AS THE REASONS ARE (the user, 2026-10-24, from try 4,
where the past project most like the job came sixth by its terms): those
with most of the job's terms go to the call that judges the reasons, which
says how alike each is, which of the reviewer's questions it helps answer
and which are the same project; the most alike are given in full, a project
in one place; a second call picks the judgments of a long one. Why a reason
does not hold is asked for in a debugger run only; how one that holds bears
here, always. No model is called."""

from __future__ import annotations

import json
import unittest

from test_learning import _Base

from personalized_agent.learning import find as find_module
from personalized_agent.learning.find import Finder

JOB = {"deliverable": ["deliverable:web app", "deliverable:script", "deliverable:tests"]}


class _Ask:
    """The model: the first call's answer, then the second's - and what each was asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = []

    def __call__(self, system, content, most):
        self.asked.append((system, content))
        return self.answers.pop(0) if self.answers else {}


class TheCasesTests(_Base):

    def _reason(self, statement, supports, **more):
        row = {"statement": statement, "source": "stated", "aspect": "output", "state": "active",
               "condition": {"dims": {}, "text": "any work"},
               "objects": [], "predictions": [], "history": [], "support": float(len(supports))}
        row.update(more)
        rid = self.store.save_reason(row)
        for jid in supports:
            self.store.link(rid, jid, "supports")
        return rid

    def test_by_the_share_of_the_jobs_terms_not_by_one_pair(self) -> None:
        one = self.case("A site with many judgments", deliverable=["deliverable:web app"])
        for _ in range(5):
            self.judgment(one)
        alike = self.case("A hub, its worker and its tests",
                          deliverable=["deliverable:web app", "deliverable:script",
                                       "deliverable:tests"])
        ranked = [case["id"] for case in Finder(self.db).similar_cases(JOB)]
        self.assertEqual(ranked[:2], [alike, one],
                         "all three deliverables first - the judgments only between equals")

    def test_judged_with_the_reasons_one_project_in_one_place(self) -> None:
        site = self.store.save_case({"unit_id": "u1", "title": "Preprint site",
                                     "facets": {"deliverable": ["deliverable:web app"]},
                                     "looks_like": "A white, compact site.",
                                     "how_made": "Next.js and Firestore."})
        site_again = self.store.save_case({"unit_id": "u2", "title": "Preprint site, accounts",
                                           "facets": {"deliverable": ["deliverable:web app"]}})
        hub = self.store.save_case({"unit_id": "u3", "title": "Distributed hub",
                                    "facets": JOB, "how_made": "Python, FastAPI, leases."})
        far = self.store.save_case({"unit_id": "u4", "title": "Robot patrol",
                                    "facets": {"deliverable": ["deliverable:script"]}})
        jid = self.judgment(site_again, direction="settings merged into the dashboard")
        rid = self._reason("Each page has one job.", [self.judgment(site), jid])
        questions = [{"q": "How should the pages look?", "kind": "output"},
                     {"q": "Which stack?", "kind": "habit"}]
        ask = _Ask({"reasons": {rid: {"holds": "yes", "here": "the task page holds it all",
                                      "answers": {"1": 3}}},
                    "cases": {hub: {"alike": 3, "answers": {"2": 3}, "same_as": ""},
                              site: {"alike": 2, "answers": {"1": 2}, "same_as": ""},
                              site_again: {"alike": 1, "answers": {}, "same_as": site},
                              far: {"alike": 0, "answers": {}, "same_as": ""}}})
        said = Finder(self.db).answer("a site where agents work together", JOB, questions, ask=ask)
        first = ask.asked[0][1]
        self.assertIn("THE CASES", first)
        self.assertIn("Python, FastAPI, leases.", first, "how each was made, to judge it by")
        self.assertEqual(said["found"]["cases"], [hub, site], "the most alike first; none not alike")
        self.assertEqual(said["found"]["same_project"], {site: [site_again]})
        self.assertNotIn(far, said["found"]["index"], "not alike: not given")
        self.assertIn(f"THE SAME PROJECT, ALSO {site_again}", said["text"])
        self.assertIn("settings merged into the dashboard", said["text"], "its judgments with it")
        self.assertIn("here: the task page holds it all", said["text"])
        # A way of working no reason answers: the cases said to answer it.
        self.assertIn(f"how it was made ({hub})", said["text"])
        self.assertEqual(len(ask.asked), 1, "short: no second call")

    def test_a_long_project_its_judgments_picked_together_in_a_second_call(self) -> None:
        site = self.case("Preprint site", deliverable=["deliverable:web app"])
        site_again = self.case("Preprint site, accounts", deliverable=["deliverable:web app"])
        mine = [self.judgment(site, direction=f"a long change {n} " + "x" * 600) for n in range(50)]
        theirs = [self.judgment(site_again, direction=f"an account change {n} " + "y" * 600)
                  for n in range(20)]
        rid = self._reason("Each page has one job.", mine[:2])
        ask = _Ask({"reasons": {rid: {"holds": "yes", "answers": {"1": 3}}},
                    "cases": {site: {"alike": 3, "answers": {}, "same_as": ""},
                              site_again: {"alike": 3, "answers": {}, "same_as": site}}},
                   {"cases": {site: [theirs[4], mine[9], "J-not-of-it"]}})
        said = Finder(self.db).answer("a site", JOB, [{"q": "How should it look?", "kind": "output"}],
                                      ask=ask)
        self.assertEqual(len(ask.asked), 2)
        self.assertIn("CASES' JUDGMENTS", ask.asked[1][1])
        self.assertIn(theirs[0], ask.asked[1][1], "the project's other case's judgments too")
        self.assertEqual(said["found"]["judgments_picked"], {site: [theirs[4], mine[9]]})
        self.assertIn(theirs[4], said["text"])
        self.assertNotIn(theirs[5], said["text"])
        self.assertLessEqual(len(said["text"]), find_module.PACKAGE_CHARS)

    def test_no_model_by_their_terms(self) -> None:
        site = self.case("Preprint site", deliverable=["deliverable:web app"])
        said = Finder(self.db).answer("a site", JOB, [], ask=None)
        self.assertEqual(said["found"]["cases"], [site])

    def test_why_not_asked_in_a_debugger_run_only(self) -> None:
        site = self.case("Preprint site", deliverable=["deliverable:web app"])
        rid = self._reason("Each page has one job.", [self.judgment(site), self.judgment(site)])
        for explain in (True, False):
            ask = _Ask({"reasons": {rid: {"holds": "no", "why": "not a site"}}})
            Finder(self.db).answer("a site", JOB, [], ask=ask, explain=explain)
            system = ask.asked[0][0]
            self.assertEqual("say why in a few words" in system, explain)
            self.assertEqual('"why": ""' in system, explain)
            self.assertIn('"here": ""', system, "how one that holds bears here: always")

    def test_five_reasons_a_question(self) -> None:
        self.assertEqual(find_module.PER_QUESTION, 5)


if __name__ == "__main__":
    unittest.main()
