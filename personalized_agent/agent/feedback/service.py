"""Turning an answer into something the agent will meet again (section 52.4).

A judgement the user makes is only worth asking for if the agent can reach it
later. So an answered question becomes a record in user_feedback/ -
``user_feedback`` - which retrieval walks like the other two: what the agent
proposed, and what the user said about it, kept together.

The node carries all three parts, because none of them means much alone:

* what the task was going to do (the user judged this, not a summary of it)
* how the agent arranged it, in the agent's own words
* what the user answered, and anything they added
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ...common.events.models import KnowledgeNode
from ...data_manage.library import USER_FEEDBACK, Record
from .store import ANSWERED, TaskQuestion
from .templates import (COMPLETE, DELAY, RUN, TO_CHECK, TO_TODO,
                        effect_of, meaning_of)

log = logging.getLogger(__name__)

class FeedbackService:
    """Answers a question, records the judgement, and acts on it."""

    def __init__(self, *, questions: Any, library: Any = None, scheduler: Any = None,
                 recorder: Any = None, bus: Any = None, todos: Any = None) -> None:
        self.questions = questions
        #: The user's to-do list, where "Add to to-do" puts the thing.
        self.todos = todos
        self.library = library
        self.scheduler = scheduler
        self.recorder = recorder
        self.bus = bus

    def answer(self, question_id: str, answer: str,
               comment: str = "", notification_id: str = "") -> Optional[TaskQuestion]:
        question = self.questions.answer(question_id, answer, comment)
        if question is None:
            return None
        if answer == TO_TODO and self.todos is not None:
            # No more notifications about it (the effect below settles the
            # task), and it is on the list until it is done.
            try:
                self.todos.add(question.question, question.task_detail,
                               notification_id=notification_id, task_id=question.task_id)
            except Exception:
                log.exception("could not put %s on the to-do list", question.id)
        node_id = self._remember(question)
        if node_id:
            self.questions.attach_node(question.id, node_id)
            question.node_id = node_id
        self._act(question)
        if self.bus is not None:
            self.bus.publish("feedback.answered", question.to_dict())
        return question

    # -- the judgement, kept ------------------------------------------------
    def _remember(self, question: TaskQuestion) -> str:
        """Write the three parts into one node of the feedback kind."""
        if self.library is None:
            return ""
        meaning = meaning_of(question.kind, question.answer)
        content = (
            f"The agent scheduled: {question.task_detail or '(no detail given)'}\n"
            f"Why the agent scheduled it: "
            f"{question.arrangement or '(no reasoning recorded)'}\n"
            f"Asked: {question.question}\n"
            f"The user answered '{question.answer}' - {meaning}."
        )
        if question.comment:
            content += f"\nThey added: {question.comment}"
        try:
            record = self.library.write(Record(
                folder=USER_FEEDBACK,
                title=f"Feedback: {question.question}"[:200],
                summary=meaning[:500],
                body=content,
                keywords=[w for w in (question.kind, question.answer) if w],
                references=[r for r in (
                    f"question: {question.id}",
                    f"task: {question.task_id}" if question.task_id else "",
                ) if r],
                source="user_feedback",
                metadata={"task_id": question.task_id,
                          "question_id": question.id,
                          "answer": question.answer,
                          "kind": question.kind,
                          "effect": effect_of(question.kind, question.answer)},
            ))
            return record.id
        except Exception:
            log.exception("could not record feedback for question %s", question.id)
            return ""

    # -- and acted on -------------------------------------------------------
    def _act(self, question: TaskQuestion) -> None:
        """Do to the task what the answered option says to do.

        The mapping is not decided here. Each option in ``templates.py``
        declares its own effect, so a button's label and its consequence are
        written in one place and cannot drift apart - which they did when this
        was a chain of ``if answer ==`` and the templates were only strings.

        Answering is not an ending. A held task is paused in the same sense as
        one waiting to be checked - the difference is only who settles it - and
        every effect below hands it back to the scheduler to end the way any
        run ends: completed, delayed, or waiting to be checked.
        """
        if self.scheduler is None:
            return
        effect = effect_of(question.kind, question.answer)
        if effect is None:
            log.info("no effect declared for %s/%s", question.kind, question.answer)
            return
        task_id = question.task_id
        if not task_id:
            # An interruption the agent raised on its own, not on behalf of
            # anything on the calendar. The answer is still recorded - that is
            # what it was asked for - but there is no task to release, defer
            # or close.
            log.debug("answer to %s changes no task: it belongs to none",
                      question.id)
            return

        if effect == RUN:
            # Released, not finished: it runs, and what it returns decides.
            self.scheduler.approve(task_id, run_now=True)
        elif effect == DELAY:
            # The option carries its own interval, chosen for this task when
            # the question was raised. "Remind me in ten minutes" and "remind
            # me next week" are the same button with different numbers.
            self.scheduler.defer(task_id, seconds=self._delay_seconds(question),
                                 note=meaning_of(question.kind, question.answer))
        elif effect == TO_CHECK:
            # The user said they would do it. Nothing is checked afterwards
            # (only notifications run), so it is settled as said.
            self.scheduler.check_later(task_id,
                                       meaning_of(question.kind, question.answer))
        elif effect == COMPLETE:
            # Over, and settled rather than cancelled. The work will not
            # happen and that is an outcome, not a failure: a calendar of
            # cancellations reads as a list of things that went wrong, when
            # what happened is that the user decided.
            self.scheduler.store.complete(
                task_id, meaning_of(question.kind, question.answer))

    @staticmethod
    def _delay_seconds(question: TaskQuestion) -> Optional[int]:
        """How long the delay option offered, as it was offered.

        Taken from the question rather than recomputed, because the user chose
        a button that said a number and that number is what they agreed to.
        """
        offered = (question.metadata or {}).get("options") or []
        for option in offered:
            if isinstance(option, dict) and option.get("key") == question.answer:
                seconds = option.get("delay_seconds")
                if seconds:
                    return int(seconds)
        return None


__all__ = ["FeedbackService", "ANSWERED"]
