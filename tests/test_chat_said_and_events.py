"""WHAT THE AGENT DID, IN SMALL GREY WORDS; AN ANSWER THAT ASKS, SAID ON ITS FOLD; TWO KINDS OF EVENT;
NOT ON THE CALENDAR TWICE; THE DAY AHEAD WHOLE (the user, 2026-11-09). Everything here is made up."""

from __future__ import annotations

import json
import time
import unittest
from datetime import timedelta
from types import SimpleNamespace

import support
from support import TemporaryAgent

from personalized_agent.agent.decisions import QUESTIONS, said_actions
from personalized_agent.common.events.models import utcnow

ROOT = support.ROOT


class WhatItDidTests(unittest.TestCase):

    def test_a_line_for_each_change_none_for_a_look_or_a_refusal(self) -> None:
        lines = said_actions([
            {"tool": "schedule_task", "arguments": {"title": "Pear-tree count", "at_local":
                                                    "2026-11-20 10:00", "notify": "notify"}},
            {"tool": "schedule_task", "arguments": {"title": "Orchard talk", "at_local":
                                                    "2026-11-21 16:00", "event": "explore"}},
            {"tool": "todo_add", "arguments": {"text": "Send Wren Halloway the yields"}},
            {"tool": "todo_list", "arguments": {}},
            {"tool": "lesson_add", "arguments": {"rule": "Never remind me at lunch"}, "ok": False},
        ])
        self.assertEqual(lines, [
            "Put on your calendar: Pear-tree count, 2026-11-20 10:00 (with a notification)",
            "Put on your calendar: Orchard talk, 2026-11-21 16:00 (no notification)",
            "Added to your to-do list: Send Wren Halloway the yields"])


class TwoSpeedsSayItTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.two = self.app.two_speed
        test = self

        class _Models:
            def __getattr__(self, name):
                return getattr(test.app.agent.models, name)

            def invoke(self, request, role="primary"):
                text = test.quick if role == "fast_reply" else test.full
                return SimpleNamespace(text=text, tool_calls=[], usage=None, stop_reason="end_turn",
                                       content=[{"type": "text", "text": text}])
        self.app.agent.models = self.two.models = _Models()

    def _done(self, turn):
        for _ in range(300):
            state = self.two.state(turn)
            if state["state"] != "running":
                return state
            time.sleep(0.02)
        self.fail("never finished")

    def test_an_answer_that_asks_is_said_so_on_its_fold(self) -> None:
        self.quick = json.dumps({"reply": "Looking.", "full_agent": "run"})
        self.full = f"The orchard talk is Friday at 16:00. Shall I put it on your calendar?\n{QUESTIONS}"
        said = self.two.send("What is on at the orchard?")
        state = self._done(said["turn"])
        self.assertTrue(state["asks"])
        self.assertNotIn(QUESTIONS, state["answer"])
        kept = self.app.conversations.messages(said["conversation_id"], limit=None)[-1]
        self.assertTrue(kept["metadata"]["asks"])

    def test_what_it_did_comes_with_the_answer_and_is_kept(self) -> None:
        self.quick = json.dumps({"reply": "On it.", "full_agent": "run"})
        self.full = "Added."
        real = self.app.agent._reason

        def acting(*args, **kwargs):
            said = real(*args, **kwargs)
            said["tool_calls"] = [{"tool": "todo_add", "arguments": {"text": "Prune the pears"},
                                   "ok": True}]
            from personalized_agent.agent.decisions import said_actions as again
            said["actions"] = again(said["tool_calls"])
            return said
        self.app.agent._reason = acting
        said = self.two.send("Remind me to prune the pears")
        state = self._done(said["turn"])
        self.assertEqual(state["actions"], ["Added to your to-do list: Prune the pears"])
        kept = self.app.conversations.messages(said["conversation_id"], limit=None)[-1]
        self.assertEqual(kept["metadata"]["actions"], state["actions"])

    def test_the_page_and_the_bubble_show_them_unfolded(self) -> None:
        page = (ROOT / "personalized_agent" / "api" / "web" / "chat.js").read_text("utf-8")
        self.assertIn("Questions to answer", page)
        self.assertIn("chatSaid(fold, fold, actions, did)", page, "after the fold, not inside it")
        self.assertNotIn("chatDecision", page, "no buttons to answer with")
        bubble = (ROOT / "personalized_agent" / "ui" / "chat_bubble.py").read_text("utf-8")
        self.assertIn('self.words("Questions to answer")', bubble)
        self.assertIn('self._write_said("fold_said", actions)', bubble)


class WhoAnswersTests(unittest.TestCase):
    """The user, 2026-11-09: the chat's head said "offline/offline-1" while the model on their list
    answered - it was read once, when the page opened, before the model was added."""

    def test_asked_again_not_once(self) -> None:
        from pathlib import Path
        chat = (Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
                / "chat.js").read_text(encoding="utf-8")
        self.assertIn("async function chatWhoAnswers()", chat)
        self.assertIn("setInterval(chatWhoAnswers, 30000)", chat)
        self.assertIn('if (typeof chatWhoAnswers === "function") chatWhoAnswers();', chat)


class TheCalendarTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.tools = self.app.agent.tools

    def _add(self, **more):
        return self.tools.call("schedule_task", {
            "title": "Orchard talk by Wren Halloway", "description": "The pear-yield talk.",
            "rationale": "Seen on a page.", "notify": "notify", "at_local": "2030-05-02 16:00",
            "zone": "user", **more})

    def test_an_event_that_may_interest_them_goes_on_quietly(self) -> None:
        said = self._add(event="explore")
        task = self.app.scheduler.store.get(said["task_id"])
        self.assertFalse(task.task_payload["notify_user"], "never pops up")
        self.assertEqual(task.metadata["event"], "explore")

    def test_held_back_only_when_a_model_says_it_is_the_same_thing(self) -> None:
        """The user, 2026-11-09: "要模型确认是同一件事拦截"."""
        first = self._add()
        verdicts, asked = [], []

        def check(proposed, when, alike):
            asked.append((proposed["title"], [task.id for task in alike]))
            return verdicts.pop(0)
        self.tools.same_check = check
        verdicts.append({"same_as": first["task_id"], "create_anyway": False, "why": "The same talk."})
        again = self._add(title="Wren Halloway's orchard talk", at_local="2030-05-02 15:30")
        self.assertIn("error", again)
        self.assertEqual(again["same_as"], first["task_id"])
        self.assertEqual(asked[0], ("Wren Halloway's orchard talk", [first["task_id"]]))
        verdicts.append({"same_as": "", "create_anyway": False, "why": "Replying, not meeting."})
        other = self._add(title="Reply to Wren Halloway about the orchard talk",
                          at_local="2030-05-02 10:00")
        self.assertTrue(other.get("task_id"), "a model said it is another thing")
        self.assertEqual(self.app.scheduler.store.get(other["task_id"]).metadata["checked_not_the_same"],
                         "Replying, not meeting.")
        verdicts.append({"create": False, "why": "the check could not be made: offline"})
        unsure = self._add(title="Wren Halloway's orchard talk, the notes", at_local="2030-05-02 17:00")
        self.assertTrue(unsure.get("task_id"), "no model's word that it is the same: it goes on")
        far = self._add(at_local="2030-05-09 16:00")
        self.assertTrue(far.get("task_id"), "a week apart is not even asked about")
        self.assertEqual(len(asked), 3)

    def test_with_no_check_to_ask_the_asking_model_is_told(self) -> None:
        self.tools.same_check = None
        self._add()
        again = self._add(title="Wren Halloway's orchard talk", at_local="2030-05-02 15:30")
        self.assertEqual(again["already"][0]["title"], "Orchard talk by Wren Halloway")
        other = self._add(title="Wren Halloway's orchard talk", at_local="2030-05-02 15:30",
                          even_so=True, rationale="The second session, an hour later.")
        self.assertTrue(other.get("task_id"))

    def test_the_notifications_step_puts_one_on_quietly(self) -> None:
        when = (utcnow() + timedelta(days=3)).strftime("%Y-%m-%d %H:%M")
        added = self.app.heartbeat._add_notifications("hb_test", [{
            "type": "explore", "title": "A talk on pear grafting", "message": "Friday, 16:00.",
            "at_local": when, "zone": "user", "why": "On a page they read; nothing says they go."}])
        self.assertTrue(added[0]["ok"], added)
        self.assertEqual(added[0]["type"], "explore")
        task = self.app.scheduler.store.get(added[0]["task_id"])
        self.assertFalse(task.task_payload["notify_user"])
        self.assertEqual(task.metadata["event"], "explore")
        prompt = __import__("personalized_agent.heartbeat.core.heartbeat",
                            fromlist=["STEP_PROMPTS"]).STEP_PROMPTS["step_notify"]["instruction"]
        self.assertIn('"type": "explore"', prompt)


class TheDayAheadTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app

    def test_two_kinds_of_event_apart_and_what_looks_done_apart(self) -> None:
        from personalized_agent.common.events.models import ScheduledTask
        later = utcnow() + timedelta(hours=2)
        for title, kind in (("Orchard meeting with Wren Halloway", ""),
                            ("A talk on pear grafting", "explore")):
            self.app.scheduler.create_task(ScheduledTask(
                title=title, description="", scheduled_at=later, execution_type="notification",
                task_payload={"notify_user": not kind}, metadata={"event": kind} if kind else {}))
        done = self.app.todos.add("Send Wren Halloway the yields")
        self.app.todos.suggest(done["id"], "Sent this morning.")
        self.app.todos.add("Prune the pears")
        self.app.todo_check = None                   # no model here
        held = self.app._monitor_today(utcnow() + timedelta(hours=6))
        self.assertEqual([line.split(": ")[0][8:] for line in held["scheduled"]],
                         ["Orchard meeting with Wren Halloway"])
        self.assertEqual(len(held["explore"]), 1)
        self.assertEqual(len(held["looks_done"]), 1)
        self.assertEqual(held["todos"], ["- Prune the pears"])

    def test_never_cut_and_told_how(self) -> None:
        source = (ROOT / "personalized_agent" / "heartbeat" / "monitor" / "decide.py").read_text("utf-8")
        self.assertNotIn("text[:1400]", source)
        for said in ("MAY INTEREST THEM", "PEOPLE WHO MAY BE MEANT", "five at most"):
            self.assertIn(said, source)


if __name__ == "__main__":
    unittest.main()
