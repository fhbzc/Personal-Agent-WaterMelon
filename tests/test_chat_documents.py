"""FROM THE CHAT (the user, 2026-11-03, from PR #8): a SUMMARY DOCUMENT of
answers ticked, from them alone; a newspaper SPECIAL ISSUE handed over from
the chat - beside the daily issues, which go on as before. Everything here
is made up."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.agent import chat_summary


class _Model:
    def __init__(self, text):
        self.text, self.asked = text, []

    def invoke(self, request, role="primary"):
        self.asked.append(request)
        return SimpleNamespace(text=self.text if isinstance(self.text, str) else json.dumps(self.text),
                               usage=None)


class SummaryTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.store = self.agent.app.conversations
        chat = self.store.create()
        self.store.add_message(chat, "user", "How do I water tomatoes?")
        self.answer = self.store.add_message(chat, "assistant", "Water at the roots, in the morning.")
        self.question = self.store.messages(chat)[0]["id"]

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _id(self, added):
        return added["id"] if isinstance(added, dict) else added

    def test_from_the_answers_ticked_alone_nothing_looked_up(self) -> None:
        model = _Model("Watering\n\nAt the roots, in the morning.")
        answers = chat_summary.answers_of(self.store, [self._id(self.answer)])
        said = chat_summary.summarise(model, answers, "Watering", "plain")
        self.assertEqual((said["answer_count"], said["style"]), (1, "plain"))
        request = model.asked[0]
        self.assertFalse(request.builtin_web, "nothing looked up")
        self.assertEqual(request.task, chat_summary.TASK)
        self.assertIn("ONLY the chat answers", request.system)
        self.assertIn("Water at the roots", request.messages[0]["content"])

    def test_only_the_agents_answers(self) -> None:
        with self.assertRaises(ValueError):
            chat_summary.answers_of(self.store, [self.question])

    def test_enriched_held_to_its_form(self) -> None:
        good = {"title": "Watering", "blocks": [
            {"type": "highlight", "text": "Mornings are best."},
            {"type": "diagram", "text": "Each day", "items": ["Check the soil", "Water the roots"]}]}
        said = chat_summary.summarise(_Model(good), ["x"], "", "enriched")
        self.assertEqual(said["document"]["title"], "Watering")
        self.assertIn("Check the soil → Water the roots", said["summary"])
        bad = {"title": "Watering", "blocks": [{"type": "diagram", "text": "One", "items": ["only one"]}]}
        with self.assertRaises(ValueError):
            chat_summary.summarise(_Model(bad), ["x"], "", "enriched")

    def test_bounds(self) -> None:
        with self.assertRaises(ValueError):
            chat_summary.summarise(_Model("x"), [], "", "plain")
        with self.assertRaises(ValueError):
            chat_summary.summarise(_Model("x"), ["a"], "", "fancy")
        with self.assertRaises(ValueError):
            chat_summary.summarise(_Model("x"), ["a" * (chat_summary.MOST_CHARACTERS + 1)], "", "plain")


class SpecialIssueTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.round = self.app.connections_round
        # No model of meanings loaded here: the stories are given (below).
        self.round.embedder = None
        self.store = self.app.connections

    def tearDown(self) -> None:
        self.round.wait(10)
        self.agent.cleanup()

    def _stories(self, *_args, **_kwargs):
        return [{"title": "A new tomato variety for cold springs", "summary": "Bred to set fruit early.",
                 "url": "https://example.org/tomato", "kind": "web", "page": "special", "gain": 3.0,
                 "recommended": True, "importance": 3}]

    def test_made_beside_the_daily_issues_which_go_on_as_before(self) -> None:
        daily = self.store.save_briefing([], [], {"paper": {"front": [], "brief": [], "pages": []}})
        self.round._page_branch = self._stories
        self.round._pictured = lambda items, going: items
        said = self.round.special_issue("Tomatoes in cold springs", "New tomato varieties")
        special = self.store.briefing(said["issue_id"])
        self.assertTrue(special["stats"]["special"])
        self.assertEqual((special["issue"], special["stats"]["title"]), (0, "Tomatoes in cold springs"))
        self.assertEqual(self.store.latest_daily()["id"], daily, "the day's issue is still the daily one")
        listed = {one["id"]: one for one in self.store.briefings()}
        self.assertEqual((listed[said["issue_id"]]["special"], listed[said["issue_id"]]["title"]),
                         (True, "Tomatoes in cold springs"))
        again = self.store.save_briefing([], [], {})
        self.assertEqual(self.store.briefing(again)["issue"], 2, "numbered after the daily issues")

    def test_handed_over_from_the_chat_not_while_another_is_made(self) -> None:
        self.assertTrue(self.app.agent.tools.has("make_special_issue"))
        self.assertIn("error", self.round.start_special(""))
        self.round._page_branch = self._stories
        self.round._pictured = lambda items, going: items
        self.round._thread = SimpleNamespace(is_alive=lambda: True, join=lambda timeout=None: None)
        self.assertIn("making an issue now", self.round.start_special("Tomatoes")["error"])
        self.round._thread = None
        started = self.app.agent.tools.call("make_special_issue", {"requirements": "Tomatoes",
                                                                   "title": "Tomatoes"})
        self.assertTrue(started.get("started"), started)
        self.round.wait(20)
        self.assertEqual((self.round.progress["stage"], self.round.progress["special"]), ("done", True))

    def test_newspaper_off_none_made(self) -> None:
        self.app.config.set("module_recommend_enabled", False)
        said = self.app.agent.tools.call("make_special_issue", {"requirements": "Tomatoes"})
        self.assertIn("switched off", said["error"])


if __name__ == "__main__":
    unittest.main()
