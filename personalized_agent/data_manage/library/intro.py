"""What each folder is, written into the folder itself.

Every folder carries an ``intro.txt`` saying where its contents come from and
what belongs in it. It is written for two readers at once.

The user is one. A folder of files the agent keeps about them is only an
improvement on an opaque database if opening it explains itself, and "here are
four hundred files" explains nothing.

The agent is the other: ``intro.txt`` is where a folder's meaning is stated
once. A folder whose description has drifted from what is actually in it is a
bug that is now visible, because the description is sitting in the folder.

An intro is written when it is missing and never overwritten. A user who
rewrites one has told the agent something about how they want that folder
used, and silently replacing it every startup would throw that away.
"""

from __future__ import annotations

from .record import STATE_TASKS, STATES, USER_FEEDBACK, USER_NATURE

INTROS = {

USER_NATURE: """user_nature
===========

How the user works: how they judge a result, and what output they want. The
durable half of what the agent knows.

Where it comes from
-------------------
- Only the lessons of the user's work - the stages learned in Experience -
  each read for how the user evaluates a result and what output they want,
  with the case it was seen in. (The agent heartbeat's nature step, which
  guessed at the person from a few minutes of activity, is paused.)

What belongs here
-----------------
Something that will still be true next month, with the lesson that shows it.
"Judges a figure by whether its point reads without the caption" belongs
here; a guess from one sitting does not.

This is the folder to read if you want to know why the agent behaves the way
it does towards this particular person. It is also the folder to edit if it
has got you wrong.
""",

USER_FEEDBACK: """user_feedback
=============

The user's verdict on what the agent did. Not what the agent did - what the
user thought of it.

Where it comes from
-------------------
- Every answer to a question the agent asked (section 52.6): the two question
  templates, and which option was chosen
- Corrections given in conversation
- A notification the user acted on, ignored, or told the agent to stop sending

What belongs here
-----------------
A judgement, with enough of the situation attached to know what was being
judged. "Told the agent to stop reminding about the gym" is useless without
what the reminder said and when it fired.

This folder is the only record of whether the agent's own judgement is any
good. It is kept separate from user_nature for that reason: what the user is
like and what the user thinks of the agent are different questions, and
letting the second colour the first is how an agent talks itself into
believing whatever is convenient.
""",

STATE_TASKS: """state_tasks
===========

What a run of states adds up to. A hundred states about one project, each
written a quarter of an hour at a time, are one thing the user has been
doing; this folder holds the one thing, and the states themselves stay next
door.

Where it comes from
-------------------
- The state heartbeat, which groups the states by what they mean and writes
  one record for each group of five or more (section 34.3)

What belongs here
-----------------
"They have spent the week on the SWR replication package." One record, naming
the states it was made from, so a reader who doubts it can go and read them.

It is kept apart from states for the reason any summary is kept apart from
what it summarises: they answer different questions. A state is a claim about
a stretch of time and it is either true or not; a task is a claim about a
number of states being one thing, and it can be wrong in a way no individual
state is - the states can each be right while the grouping that joined them
is not. Mixed into one folder, a reader could not tell which they were
holding.

A task here keeps its identity between runs. It is not rewritten every hour:
the same task gains states, and its record is written again only when enough
of what it holds has turned over that the old description is a description of
something else.
""",

STATES: """states
======

What was true over a stretch of time. A state has a beginning and an end, and
between them it is something the agent can assume.

Where it comes from
-------------------
- Every scheduled task writes one when it is created (section 11.1)
- The agent heartbeat's first step, which decides what is currently going on
- The heartbeat's fourth step, which ends, extends or retires them

What belongs here
-----------------
"Working on the thesis, 09:00 to 18:00." "Away until Monday." "Has asked not
to be interrupted this afternoon."

States are internal (section 11.1). They are not shown in the ordinary
interface and the user cannot create one. They are here, in a folder the user
can read, because internal should mean "not the user's to manage" rather than
"hidden from them".

A state that has ended is kept. What was true last Tuesday is context for
understanding last Tuesday, and deleting it would leave the agent unable to
explain its own past behaviour.
""",

}


def intro_for(folder: str) -> str:
    return INTROS.get(folder, f"{folder}\n\n(no description written yet)\n")


__all__ = ["INTROS", "intro_for"]
