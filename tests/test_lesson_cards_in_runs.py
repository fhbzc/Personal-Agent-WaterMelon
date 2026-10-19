"""LESSON CARDS IN A RUN (the user's decision, 2026-10-18): the reviewer
answers what it had to know as it writes the criteria; the user's own work
keeps each answer as a card - never one resting on the web - and the next
run's reviewer is shown it, cites it, and may replace it with a new
version. A debugger way reads the user's cards and writes none; the way
with no context is shown none. No model is called; the work is made up."""

from __future__ import annotations

import json
import re
import unittest

from support import TemporaryAgent
from test_experience_debugger import _DebuggerBase, _Models
from test_work_runs import _TwoAgents

from personalized_agent.heartbeat.experience.lesson_cards import LessonCards

LC = re.compile(r"lc_[0-9a-f]{16}")
QUESTION = "How does the user want the notes written?"


def _reviewer(request):
    return "You review a work agent" in (request.system or "")


MADE_UP = "Which language does the user write notes in?"
OPEN = "Which greeting do notes most often open with these days?"


def _contract_review(request, replace):
    """STEP 1, no web: what the reviewer must know - one answer resting on
    the user's own words, one on words they never wrote, one said to rest
    on the web, one left open. Then the contract approved, citing the card
    it was shown, if any. None for anything else."""
    last = request.messages[-1]["content"]
    said = last if isinstance(last, str) else json.dumps(last)
    if not _reviewer(request):
        return None
    # The card it was shown for this very question, if any.
    words = "\n".join(one["content"] for one in request.messages
                      if isinstance(one.get("content"), str))
    shown = [found for found, question in re.findall(
        r'\{"id": "(lc_[0-9a-f]{16})", "question": "([^"]*)"', words) if question == QUESTION]
    if "BEFORE YOU WRITE THE CRITERIA OF THIS CONTRACT" in said:
        asked = {"question": QUESTION, "answer": "Plainly: one greeting a line.",
                 "evidence": [{"type": "user_said", "ref": "the request",
                               "said": "write the notes"}],
                 "topics": ["notes", "style"]}
        if shown and replace:
            asked = dict(asked, answer="Plainly, and dated.", replaces=shown[0])
        return json.dumps({"questions": [
            asked,
            {"question": MADE_UP, "answer": "French.",
             "evidence": [{"type": "user_said", "said": "always write in French"}]},
            {"question": "Which greeting is best?", "answer": "hello",
             "evidence": [{"type": "web", "ref": "https://example.org/greetings"}]},
            {"question": OPEN, "answer": ""}]})
    if "REVIEW THIS CONTRACT" not in said:
        return None
    return json.dumps({
        "verdict": "approve", "comments": [],
        "deliverables": [{"ref": "d1", "criteria": [
            {"text": "notes.md says hello", "because": shown[:1] or ["own judgement"],
             "backed_by": "check"}], "approach": "write it plainly"}]})


def _as_asked(request):
    """What a call was asked, as it was asked: its last message and its
    tools - the messages go on growing after it."""
    last = request.messages[-1]["content"]
    return (last if isinstance(last, str) else json.dumps(last),
            {one.name for one in request.tools or []})


class _Asking(_TwoAgents):

    def __init__(self, replace=False, **kwargs):
        super().__init__(**kwargs)
        self.replace = replace
        self.seen = []

    def invoke(self, request, role=""):
        if _reviewer(request):
            self.seen.append(_as_asked(request))
        answer = _contract_review(request, self.replace)
        if answer is not None:
            self.asked.append(request)
            return self._reply(answer)
        return super().invoke(request, role)


class _DebugAsking(_Models):

    def invoke(self, request, role=""):
        answer = _contract_review(request, False)
        if answer is not None:
            self.asked.append(request)
            return self._reply(answer)
        return super().invoke(request, role)


class TheUsersOwnWorkKeepsWhatItAnsweredTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.unit = {"id": "task-notes1", "name": "Write notes", "kind": "task", "why": ""}
        self.cards = LessonCards(self.app.db)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _run(self, models):
        import time
        self.app.work.models = models
        started = self.app.work.start(self.unit, "write the notes")
        self.assertNotIn("error", started, started)
        for _ in range(600):
            runs = self.app.work.runs(self.unit["id"])
            if runs and not runs[0]["alive"] and runs[0]["status"] != "running":
                break
            time.sleep(0.1)
        run = self.app.work.runs(self.unit["id"])[0]
        self.assertEqual(run["status"], "done", run["log"][-6:])
        return run

    def test_kept_shown_cited_and_replaced_never_from_the_web(self) -> None:
        models = _Asking()
        first = self._run(models)
        kept = {one["question"]: one for one in self.cards.cards()}
        self.assertEqual(set(kept), {QUESTION, MADE_UP})
        card = kept[QUESTION]
        self.assertEqual((card["tier"], card["status"]), ("user", "candidate"))
        self.assertEqual((card["context"]["project"], card["context"]["run"]),
                         (self.unit["id"], first["id"]))
        self.assertIn("the contract", card["context"]["why"])
        # Words the user never wrote are not theirs: the model's own, an
        # assumption.
        self.assertEqual(kept[MADE_UP]["tier"], "assumption")
        self.assertEqual(kept[MADE_UP]["evidence"][0]["type"], "model")
        # Asked with no web at all; the answer said to rest on the web is
        # not kept.
        step_one = [tools for said, tools in models.seen
                    if "BEFORE YOU WRITE THE CRITERIA OF THIS CONTRACT" in said]
        self.assertTrue(step_one)
        self.assertFalse({"web_search", "fetch_url"} & step_one[0])
        self.assertIn("read_file", step_one[0], "its own reading tools, the user's files")
        noted = [one for one in first["log"] if one.get("what") == "lesson cards"]
        self.assertTrue(noted, first["log"][-8:])
        self.assertIn("web", json.dumps(noted[0], default=str))
        # The question left open is asked with the contract's review, the
        # web there too - and never kept.
        review = [(said, tools) for said, tools in models.seen if "REVIEW THIS CONTRACT" in said]
        self.assertIn("THE QUESTIONS YOU LEFT OPEN", review[0][0])
        self.assertIn(OPEN, review[0][0])
        self.assertIn("web_search", review[0][1])
        self.assertNotIn(OPEN, kept)

        # The next run's reviewer is shown it, cites it - a use - and gives a
        # new answer: a new version, the old one kept and marked.
        models = _Asking(replace=True)
        second = self._run(models)
        shown = [r for r in models.asked if _reviewer(r)
                 and card["id"] in json.dumps(r.messages, default=str)]
        self.assertTrue(shown)
        self.assertIn("LESSON CARDS", json.dumps(shown[0].messages, default=str))
        uses = self.cards.uses(card["id"])
        self.assertEqual([(one["run_id"], one["decision"]) for one in uses],
                         [(second["id"], "notes.md says hello")])
        self.assertEqual(self.cards.get(card["id"])["status"], "superseded")
        now = [one for one in self.cards.cards() if one["lineage"] == card["id"]]
        self.assertEqual([(one["version"], one["answer"]) for one in now],
                         [(2, "Plainly, and dated.")])

    def test_the_same_question_again_is_not_a_second_card(self) -> None:
        self._run(_Asking())
        self._run(_Asking())
        self.assertEqual(sorted(one["question"] for one in self.cards.cards()),
                         sorted([QUESTION, MADE_UP]))


class ADebuggerWayReadsAndWritesNoneTests(_DebuggerBase):

    def test_the_full_way_reads_the_users_cards_and_b_is_shown_none(self) -> None:
        cards = LessonCards(self.app.db)
        mine = cards.write("Which notes does the user keep?", "Short plain notes.",
                           [{"type": "user_code", "ref": "notes/today.md"}],
                           context={"project": "elsewhere"})["card"]
        project = self._project()
        for variant in ("full", "noctx"):
            models = _DebugAsking()
            self.app.work.models = models
            self._start(project, variant)
            run = self._finished(project, variant)
            self.assertEqual(run["status"], "done", run["log"][-6:])
            reviewer = [r for r in models.asked if _reviewer(r)]
            seen = any(mine["id"] in json.dumps(r.messages, default=str) for r in reviewer)
            self.assertEqual(seen, variant == "full", variant)
            if variant == "noctx":
                for request in reviewer:
                    said = request.system + json.dumps(request.messages, default=str)
                    self.assertNotIn("LESSON CARD", said)
                    self.assertNotIn("replaces", said)
            # Asked the same questions - and nothing kept, no use recorded.
            self.assertEqual([one["id"] for one in cards.cards()], [mine["id"]])
            self.assertEqual(cards.uses(mine["id"]), [])


if __name__ == "__main__":
    unittest.main()
