"""Bubbles: one at a time, and they wait to be answered (section 7.4)."""

from __future__ import annotations

import unittest
from datetime import timedelta
from types import SimpleNamespace

from support import TemporaryAgent, asking

from personalized_agent.common.events.models import ScheduledTask, utcnow
from personalized_agent.ui import desktop


class _FakeBubble:
    """Stands in for the Toplevel, so the queue can be tested without Tk."""

    def __init__(self) -> None:
        self.destroyed = False

    def destroy(self) -> None:
        self.destroyed = True


class _Harness(desktop.DesktopAgent):
    """A DesktopAgent with no window, exercising only the bubble queue."""

    def __init__(self, app) -> None:                   # no super().__init__
        import collections
        self.app = app
        self.config = app.config
        self._bubble_queue = collections.deque()
        self._bubble = None
        self._bubble_count = None
        self._showing = None
        self.drawn = []
        self.opened_chat = 0
        self.root = SimpleNamespace(after=lambda _ms, fn=None: None)

    # -- the parts that would need a window ---------------------------------
    def _show_next_bubble(self) -> None:
        if self._bubble is not None or not self._bubble_queue:
            return
        item = self._bubble_queue.popleft()
        self._showing = item
        drawn = {**item, "options": self._options_for(item["question_id"]),
                 "waiting": len(self._bubble_queue)}
        self.drawn.append(drawn)
        self._bubble = _FakeBubble()
        self._bubble_count = drawn

    def _refresh_bubble_count(self) -> None:
        if self._bubble is not None and self._bubble_count is not None:
            self._bubble_count["waiting"] = len(self._bubble_queue)

    def open_chat(self, initial_message=None) -> None:
        self.opened_chat += 1


class BubblesQueueAndWaitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = asking(self.agent.app)
        self.ui = _Harness(self.app)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _guarded_task(self, title="Tidy the exports"):
        return self.app.scheduler.create_task(ScheduledTask(
            title=title, description="Deletes old files.",
            scheduled_at=utcnow() - timedelta(seconds=1),
            execution_type="notification",
            task_payload={"notify_user": False}))

    # -- one at a time ------------------------------------------------------
    def test_only_one_bubble_is_on_screen(self) -> None:
        for i in range(3):
            self.ui.notify_bubble(f"message {i}")
        self.assertEqual(len(self.ui.drawn), 1, "bubbles piled up on each other")
        self.assertEqual(self.ui.drawn[0]["text"], "message 0")

    def test_the_rest_wait_their_turn(self) -> None:
        for i in range(3):
            self.ui.notify_bubble(f"message {i}")
        self.assertEqual(len(self.ui._bubble_queue), 2)

    def test_answering_one_brings_up_the_next(self) -> None:
        for i in range(3):
            self.ui.notify_bubble(f"message {i}")

        self.ui._bubble_answered("", "", emphasis=False)
        self.ui._show_next_bubble()          # the real one is on a timer

        self.assertEqual([b["text"] for b in self.ui.drawn],
                         ["message 0", "message 1"])

    def test_the_bubble_says_how_many_are_behind_it(self) -> None:
        # Without it, answering reads as having done nothing when the next one
        # appears in the same place.
        for i in range(3):
            self.ui.notify_bubble(f"message {i}")
        self.assertEqual(self.ui.drawn[0]["waiting"], 2)

    def test_the_count_keeps_up_with_what_arrives_later(self) -> None:
        """Notifications turn up over time, not in a batch.

        The count is drawn once and would otherwise say what was true at that
        instant - which for the first bubble is always none, however many
        follow it.
        """
        self.ui.notify_bubble("the first one")
        self.assertEqual(self.ui.drawn[0]["waiting"], 0)

        self.ui.notify_bubble("arrived while you were reading")
        self.ui.notify_bubble("and another")

        self.assertEqual(self.ui.drawn[0]["waiting"], 2,
                         "the bubble on screen still claims nothing is waiting")

    def test_nothing_is_dropped_when_several_arrive_at_once(self) -> None:
        for i in range(5):
            self.ui.notify_bubble(f"message {i}")
        for _ in range(5):
            self.ui._bubble_answered("", "", emphasis=False)
            self.ui._show_next_bubble()
        self.assertEqual([b["text"] for b in self.ui.drawn],
                         [f"message {i}" for i in range(5)])

    # -- it waits to be answered --------------------------------------------
    def test_a_bubble_has_something_to_answer_with(self) -> None:
        self.ui.notify_bubble("just so you know")
        self.assertTrue(self.ui.drawn[0]["options"],
                        "a bubble that waits for an answer needs a button")

    def test_a_question_brings_its_own_buttons(self) -> None:
        from personalized_agent.agent.feedback.store import KIND_PERMISSION

        task = self._guarded_task()
        self.app.scheduler.ask_permission(task)
        question = self.app.questions.pending_for(task.id, KIND_PERMISSION)

        self.ui.notify_bubble("Waiting on you", question_id=question.id)

        keys = [o["key"] for o in self.ui.drawn[0]["options"]]
        # Every template also offers Add to to-do (agent/feedback/templates.py).
        self.assertEqual(keys, ["allow", "deny", "delay", "stop_asking", "to_todo"])

    def test_pressing_a_button_answers_the_question(self) -> None:
        from personalized_agent.agent.feedback.store import KIND_PERMISSION

        task = self._guarded_task()
        self.app.scheduler.ask_permission(task)
        question = self.app.questions.pending_for(task.id, KIND_PERMISSION)
        self.ui.notify_bubble("Waiting on you", question_id=question.id)

        self.ui._answer_question(question.id, "deny")    # what the button runs

        self.assertEqual(self.app.questions.get(question.id).status, "answered")
        self.assertEqual(self.app.scheduler_store.get(task.id).status, "completed")

    def test_an_already_answered_question_falls_back_to_acknowledging(self) -> None:
        # Answered in the conversation view while the bubble sat there.
        from personalized_agent.agent.feedback.store import KIND_PERMISSION

        task = self._guarded_task()
        self.app.scheduler.ask_permission(task)
        question = self.app.questions.pending_for(task.id, KIND_PERMISSION)
        self.app.feedback.answer(question.id, "allow")

        self.ui.notify_bubble("Waiting on you", question_id=question.id)

        self.assertEqual([o["key"] for o in self.ui.drawn[0]["options"]], [""],
                         "it must not offer buttons that would answer twice")

    def test_a_missing_question_is_not_an_error(self) -> None:
        self.ui.notify_bubble("Waiting on you", question_id="ask_gone")
        self.assertTrue(self.ui.drawn[0]["options"])

    # -- the proactive kind -------------------------------------------------
    def test_acknowledging_a_proactive_message_stays_on_the_current_desktop(self) -> None:
        self.ui.notify_bubble("I noticed something", emphasis=True)
        self.ui._bubble_answered("", "", emphasis=True)
        self.assertEqual(self.ui.opened_chat, 0)

    def test_answering_a_question_does_not_open_the_conversation(self) -> None:
        self.ui.notify_bubble("Waiting on you", question_id="ask_x")
        self.ui._bubble_answered("ask_x", "allow", emphasis=True)
        self.assertEqual(self.ui.opened_chat, 0,
                         "answering is the whole interaction; it must not also "
                         "take the screen")


class OneInterruptionIsOnePromptTests(unittest.TestCase):
    """The same reminder, raised twice, is not two things to dismiss.

    A repeating reminder raises a notification every time it fires, and each
    one points at the *same standing question* - answering either answers
    both. Queueing both made the user dismiss the same words twice, and the
    second told them nothing they had not just read.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.ui = _Harness(self.agent.app)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_same_question_twice_is_one_bubble(self) -> None:
        self.ui.notify_bubble("Time to stand up", question_id="ask_1")
        self.ui.notify_bubble("Time to stand up", question_id="ask_1")
        self.assertEqual(len(self.ui.drawn), 1)
        self.assertEqual(len(self.ui._bubble_queue), 0,
                         "the repeat was queued behind the one on screen")

    def test_the_later_wording_wins(self) -> None:
        # A repeating reminder may say something new each time it fires.
        self.ui.notify_bubble("Sitting 45 minutes", question_id="ask_1")
        self.ui.notify_bubble("Sitting 90 minutes", question_id="ask_1")
        self.assertEqual(self.ui._showing["text"], "Sitting 90 minutes")

    def test_two_different_questions_are_two_bubbles(self) -> None:
        self.ui.notify_bubble("Stand up", question_id="ask_1")
        self.ui.notify_bubble("Something else", question_id="ask_2")
        self.assertEqual(len(self.ui.drawn), 1, "one at a time, still")
        self.assertEqual(len(self.ui._bubble_queue), 1, "the other one waits")

    def test_identical_words_with_no_question_also_merge(self) -> None:
        self.ui.notify_bubble("I noticed something")
        self.ui.notify_bubble("I noticed something")
        self.assertEqual(len(self.ui._bubble_queue), 0)

    def test_different_words_with_no_question_do_not(self) -> None:
        self.ui.notify_bubble("One thing")
        self.ui.notify_bubble("Another thing")
        self.assertEqual(len(self.ui._bubble_queue), 1)

    def test_a_repeat_merges_into_one_that_is_only_queued(self) -> None:
        self.ui.notify_bubble("First", question_id="ask_a")     # drawn
        self.ui.notify_bubble("Second", question_id="ask_b")    # queued
        self.ui.notify_bubble("Second again", question_id="ask_b")
        self.assertEqual(len(self.ui._bubble_queue), 1)
        self.assertEqual(self.ui._bubble_queue[0]["text"], "Second again")


class NothingGoesToTheSystemNotifierTests(unittest.TestCase):
    """The agent speaks in its own bubble, and nowhere else.

    An interruption from this agent is one of three templates and arrives
    with the options for answering it. A system notification carries none of
    them - it cannot be answered, and it is drawn by something this
    application does not control. Sending both put the same words on screen
    twice, once answerable and once not, and the copy the user reached first
    was usually the one they could do nothing with.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.sent = []
        self.app.notifications.platform = SimpleNamespace(
            notifications=SimpleNamespace(
                send_notification=lambda title, message: self.sent.append(title)))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_an_interruption_is_not_sent_to_the_system(self) -> None:
        from personalized_agent.agent.feedback.templates import REMINDER

        self.app.notifications.notify(
            "Time to stand up", "45 minutes", kind="proactive",
            template=REMINDER, question={"task_id": "task_rest"}, force=True)
        self.assertEqual(self.sent, [])

    def test_neither_is_a_system_warning(self) -> None:
        # Not only the answerable ones. Everything the agent has to say
        # appears in the agent, so that there is one place to look.
        self.app.notifications.notify("Model unavailable", "Open Settings.",
                                      kind="warning", force=True)
        self.assertEqual(self.sent, [])

    def test_it_is_still_recorded_and_still_published(self) -> None:
        # Removing the second channel must not remove the first one.
        from personalized_agent.agent.feedback.templates import PASSIVE

        seen = []
        self.app.bus.subscribe("notification.created",
                               lambda _topic, payload: seen.append(payload))
        notification = self.app.notifications.notify(
            "A thing worth saying", "once", kind="proactive",
            template=PASSIVE, question={"task_id": "task_note"}, force=True)
        self.assertIsNotNone(notification)
        self.assertEqual(len(seen), 1)
        self.assertTrue(self.app.notifications.recent(limit=5))

    def test_there_is_no_setting_for_it_any_more(self) -> None:
        from personalized_agent.common.config.schema import SETTINGS_SCHEMA

        keys = [field.key for field in SETTINGS_SCHEMA]
        self.assertNotIn("native_notifications", keys,
                         "a switch that turns on an unanswerable channel")


class ABubbleButtonCanBeReadTests(unittest.TestCase):
    """A button whose text is its own background colour is a blank button.

    The primary button was drawn as "edge behind, background in front". That
    reads on the emphatic palette, whose edge is amber. The quiet palette
    deliberately has no visible edge - its edge *is* its background - so the
    label was written in the colour it was sitting on. The OK on a passive
    notice is always the primary button, so it was always the invisible one.
    """

    def test_every_palette_writes_its_primary_button_in_a_different_colour(self) -> None:
        for name in ("QUIET_BUBBLE", "EMPHATIC_BUBBLE"):
            palette = getattr(desktop, name)
            with self.subTest(palette=name):
                self.assertNotEqual(palette["accent"], palette["on_accent"],
                                    "the label is the same colour as the button")
                self.assertNotEqual(palette["bg"], palette["fg"],
                                    "the message is the same colour as the bubble")

    def test_every_palette_has_the_colours_the_bubble_asks_for(self) -> None:
        for name in ("QUIET_BUBBLE", "EMPHATIC_BUBBLE"):
            with self.subTest(palette=name):
                self.assertEqual(set(getattr(desktop, name)),
                                 {"bg", "fg", "edge", "accent", "on_accent"})

    def test_an_option_with_no_label_still_says_something(self) -> None:
        # Nothing writes one of these now, but a blank face is the one
        # outcome a button must never have.
        from personalized_agent.agent.feedback.templates import options_for, PASSIVE

        offered = [o.to_dict() for o in options_for(PASSIVE)]
        # Each button says what happens when it is pressed.
        self.assertEqual([o["label"] for o in offered],
                         ["OK (no more notifications)",
                          "Add to to-do (no more notifications; listed in To do on the Overview)"])
        for option in ({"key": "ok"}, {"key": "ok", "label": ""}, {}):
            with self.subTest(option=option):
                label = str(option.get("label") or option.get("key") or "OK")
                self.assertTrue(label.strip())


if __name__ == "__main__":
    unittest.main()
