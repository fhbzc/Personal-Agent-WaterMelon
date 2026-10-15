"""The chat's conversation so far, and what a debugger sees of a turn.

Ported from PR #4 (by daniela-miaut) and adapted to the chat as it is now:
there is no memory retrieval to inspect, and a conversation is given to the
model whole while it fits and summarised past that.
"""

from __future__ import annotations

import threading
import unittest

from support import TemporaryAgent

from personalized_agent.agent.core import agent_core
from personalized_agent.models.base import ModelResponse


class ChatCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _recording(self):
        sent = []
        original = self.app.models.invoke

        def record(request, role="primary"):
            sent.append(request)
            return original(request, role=role)

        self.app.models.invoke = record
        return sent

    # -- what a debugger sees -------------------------------------------------
    def test_debug_callback_receives_ordered_model_and_context_events(self) -> None:
        events = []
        result = self.app.agent.handle_message(
            "What have I been working on?",
            debug=lambda kind, data: events.append((kind, data)))
        kinds = [kind for kind, _ in events]
        self.assertEqual(kinds[0:2], ["instruction", "context"])
        self.assertIn("model_request", kinds)
        self.assertIn("model_resolution", kinds)
        self.assertIn("model_response", kinds)
        self.assertEqual(kinds[-1], "completion")
        request = next(data for kind, data in events if kind == "model_request")
        self.assertTrue(request["system"])
        self.assertEqual(request["messages"][-1]["content"], "What have I been working on?")
        context = next(data for kind, data in events if kind == "context")
        self.assertEqual(context["history"], [])
        self.assertEqual(context["summary"], "")
        self.assertEqual(events[-1][1]["task_id"], result["task_id"])

    def test_debug_callback_failure_and_mutation_cannot_change_chat(self) -> None:
        seen = []

        def mutate_then_raise(kind, data):
            seen.append(kind)
            if kind == "model_request":
                data["messages"].clear()
            raise RuntimeError("debug sink failed")

        result = self.app.agent.handle_message("hello", debug=mutate_then_raise)
        self.assertTrue(result["answer"])
        self.assertIn("model_request", seen)
        self.assertEqual([m["role"] for m in self.app.conversations.messages(
            result["conversation_id"])], ["user", "assistant"])

    def test_a_failing_turn_reports_an_error_event_and_ends_its_progress(self) -> None:
        events = []

        def broken(*_args, **_kwargs):
            raise RuntimeError("the model layer broke")

        self.app.models.invoke = broken
        with self.assertRaisesRegex(RuntimeError, "the model layer broke"):
            self.app.agent.run_instruction(
                "hello", debug=lambda kind, data: events.append((kind, data)))
        self.assertEqual(events[-1][0], "error")
        self.assertIn("the model layer broke", events[-1][1]["error"])

    def test_whitespace_only_model_answer_uses_visible_fallback(self) -> None:
        self.app.models.invoke = lambda request, role="primary": ModelResponse(text=" \n ")
        result = self.app.agent.handle_message("hello")
        self.assertEqual(result["answer"], "I could not produce an answer for this request.")

    # -- the conversation so far ------------------------------------------------
    def test_prior_conversation_turns_are_passed_before_current_question(self) -> None:
        conversation_id = self.app.conversations.create()
        first = self.app.agent.handle_message("I prefer concise answers.", conversation_id)
        sent = self._recording()
        self.app.agent.handle_message("What style do I prefer?", conversation_id)
        self.assertEqual(sent[0].messages, [
            {"role": "user", "content": "I prefer concise answers."},
            {"role": "assistant", "content": first["answer"]},
            {"role": "user", "content": "What style do I prefer?"}])

    def test_a_new_conversation_is_given_nothing_of_another(self) -> None:
        self.app.agent.handle_message("My project is called Aurora.")
        sent = self._recording()
        self.app.agent.handle_message("What is my project called?")
        self.assertEqual(sent[0].messages,
                         [{"role": "user", "content": "What is my project called?"}])
        self.assertNotIn("Aurora", sent[0].system)

    def test_a_reopened_conversation_is_read_whole_while_it_fits(self) -> None:
        """Every earlier message, in order, the current one exactly once."""
        conversation_id = self.app.conversations.create()
        for index in range(35):
            self.app.conversations.add_message(conversation_id, "user", f"old user {index}")
            self.app.conversations.add_message(conversation_id, "assistant",
                                               f"old answer {index}")
        sent = self._recording()
        self.app.agent.handle_message("current question", conversation_id)
        contents = [message["content"] for message in sent[0].messages]
        self.assertEqual(len(contents), 71)
        self.assertEqual(contents[0], "old user 0")
        self.assertEqual(contents[-2], "old answer 34")
        self.assertEqual(contents[-1], "current question")
        self.assertNotIn("Earlier in this conversation", sent[0].system)
        self.assertIsNone(self.app.conversations.summary(conversation_id))

    def _long_conversation(self, turns=20):
        conversation_id = self.app.conversations.create()
        size = agent_core.HISTORY_BUDGET_CHARS // 12
        for index in range(turns):
            self.app.conversations.add_message(conversation_id, "user",
                                               f"Question {index}: " + "q" * size)
            self.app.conversations.add_message(conversation_id, "assistant",
                                               f"Answer {index}: " + "a" * size)
        return conversation_id

    def _summarising(self):
        """A model that summarises when asked to, and answers otherwise."""
        asked = []

        def invoke(request, role="primary"):
            asked.append(request)
            if request.task == "chat_history_summary":
                return ModelResponse(text=f"SUMMARY {len(asked)}")
            return ModelResponse(text="the answer")

        self.app.models.invoke = invoke
        return asked

    def test_a_long_conversation_is_summarised_and_its_end_kept_word_for_word(self) -> None:
        conversation_id = self._long_conversation()
        asked = self._summarising()
        self.app.agent.handle_message("And the next one?", conversation_id)
        summaries = [one for one in asked if one.task == "chat_history_summary"]
        turn = [one for one in asked if one.task == "agent_instruction"][0]
        self.assertEqual(len(summaries), 1)
        self.assertIn("Question 0:", summaries[0].messages[0]["content"])
        # The summary is in the prompt; the oldest turns are not in the messages.
        self.assertIn("SUMMARY 1", turn.system)
        contents = [one["content"] for one in turn.messages]
        self.assertFalse(any(text.startswith("Question 0:") for text in contents))
        self.assertTrue(contents[-2].startswith("Answer 19:"))
        self.assertEqual(contents[-1], "And the next one?")
        # The kept part starts with something the user said.
        self.assertEqual(turn.messages[0]["role"], "user")
        # And what is given stays inside the budget.
        given = sum(len(text) for text in contents[:-1]) + len("SUMMARY 1")
        self.assertLessEqual(given, agent_core.HISTORY_BUDGET_CHARS)
        kept = self.app.conversations.summary(conversation_id)
        self.assertEqual(kept["summary"], "SUMMARY 1")

    def test_the_summary_is_reused_until_it_goes_stale_then_extended(self) -> None:
        conversation_id = self._long_conversation()
        asked = self._summarising()
        self.app.agent.handle_message("Next?", conversation_id)
        first = self.app.conversations.summary(conversation_id)
        asked.clear()
        # A short turn more still fits beside the summary: no new summary.
        self.app.agent.handle_message("And?", conversation_id)
        self.assertEqual([one.task for one in asked], ["agent_instruction"])
        self.assertIn(first["summary"], asked[0].system)
        # Enough said since that the end no longer fits: extended, from the
        # old summary and only what came after it.
        size = agent_core.HISTORY_BUDGET_CHARS // 6
        for index in range(6):
            self.app.conversations.add_message(conversation_id, "user", f"More {index}: "
                                               + "m" * size)
            self.app.conversations.add_message(conversation_id, "assistant", "ok")
        asked.clear()
        self.app.agent.handle_message("Still there?", conversation_id)
        summaries = [one for one in asked if one.task == "chat_history_summary"]
        self.assertEqual(len(summaries), 1)
        content = summaries[0].messages[0]["content"]
        self.assertIn(first["summary"], content)
        self.assertNotIn("Question 0:", content, "the old part was read again")
        second = self.app.conversations.summary(conversation_id)
        self.assertGreater(second["covered"], first["covered"])

    def test_a_summary_that_fails_is_not_kept_and_the_turn_still_answers(self) -> None:
        conversation_id = self._long_conversation()

        def invoke(request, role="primary"):
            if request.task == "chat_history_summary":
                raise RuntimeError("no summary today")
            invoke.turn = request
            return ModelResponse(text="answered anyway")

        self.app.models.invoke = invoke
        result = self.app.agent.handle_message("Next?", conversation_id)
        self.assertEqual(result["answer"], "answered anyway")
        self.assertIn("could not be summarised", invoke.turn.system)
        self.assertIsNone(self.app.conversations.summary(conversation_id))

    # -- one turn of a conversation at a time ------------------------------------
    def test_turns_of_one_conversation_are_taken_one_at_a_time(self) -> None:
        conversation_id = self.app.conversations.create()
        entered, release = threading.Event(), threading.Event()
        lock = threading.Lock()
        active = peak = 0
        seen = []
        original = self.app.models.invoke

        def held(request, role="primary"):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                seen.append([m["content"] for m in request.messages])
                first = len(seen) == 1
            if first:
                entered.set()
                release.wait(timeout=5)
            try:
                return original(request, role=role)
            finally:
                with lock:
                    active -= 1

        self.app.models.invoke = held
        failures = []

        def turn(text):
            try:
                self.app.agent.handle_message(text, conversation_id)
            except BaseException as exc:          # pragma: no cover - reported below
                failures.append(exc)

        one = threading.Thread(target=turn, args=("alpha",))
        two = threading.Thread(target=turn, args=("beta",))
        one.start()
        self.assertTrue(entered.wait(timeout=5))
        two.start()
        two.join(timeout=0.3)
        with lock:
            self.assertEqual(len(seen), 1, "the second turn did not wait for the first")
        release.set()
        one.join(timeout=5)
        two.join(timeout=5)
        self.assertEqual(failures, [])
        self.assertEqual(peak, 1)
        # The second turn read the first one, answer and all.
        self.assertEqual(seen[1][0], "alpha")
        self.assertEqual(seen[1][-1], "beta")
        self.assertEqual([m["role"] for m in self.app.conversations.messages(conversation_id)],
                         ["user", "assistant", "user", "assistant"])

    def test_different_conversations_are_not_held_up_by_each_other(self) -> None:
        entered, release = threading.Event(), threading.Event()
        original = self.app.models.invoke
        answered = []

        def held(request, role="primary"):
            if request.messages[-1]["content"] == "slow":
                entered.set()
                release.wait(timeout=5)
            return original(request, role=role)

        self.app.models.invoke = held
        slow = threading.Thread(target=lambda: self.app.agent.handle_message("slow"))
        slow.start()
        try:
            self.assertTrue(entered.wait(timeout=5))
            fast = threading.Thread(
                target=lambda: answered.append(self.app.agent.handle_message("fast")))
            fast.start()
            fast.join(timeout=5)
            self.assertEqual(len(answered), 1, "another conversation waited")
        finally:
            release.set()
            slow.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
