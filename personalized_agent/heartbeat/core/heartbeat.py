"""Agent Heartbeat (sections 34-37, 68).

Trigger -> checklist snapshot -> library retrieval -> execute checklist items ->
determine actions -> immediate action or scheduled task -> record Raw Edits ->
exit.  The model is not kept alive between heartbeats.

Section 37: the heartbeat is driven by its own timer.  A user message never
invokes it; direct interaction uses the normal agent workflow instead.
"""

from __future__ import annotations

import copy
import collections
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from ...scheduler.core.recurrence import RecurrenceError, parse_recurrence
from ...scheduler.core.recurrence import describe as describe_recurrence
from ...scheduler.core.zones import PLACE_RULE as _PLACE_RULE
from ...scheduler.core.given import HOW_TO_GIVE as _HOW_TO_GIVE
from ...scheduler.execution.executors import (DEFAULT_CHECK_AFTER_SECONDS,
                                              DEFAULT_RECHECK_AFTER_SECONDS,
                                              VERIFICATION, _interval)
from ...common.events.models import ScheduledTask, new_id, parse_iso, utcnow
from ...common.observability.tracing import NullTracer, new_trace_id
from ...common.reason import BY_HEARTBEAT
from ...data_manage.text import estimate_tokens
from ...common.languages import in_display, in_english
from .pending import INSTRUCTION as _PENDING_INSTRUCTION
from ...agent.overview.keywords import Moments, page_key, pairs_of
from ...agent.feedback.templates import PASSIVE, REMINDER
from ...models.base import SIGN_IN_STATES, ModelRequest, ToolSpec
from ...models.manager import ModelUnavailable

log = logging.getLogger(__name__)

#: How many states one run may write. A reply asking to rewrite the whole
#: calendar is a malfunction, not an instruction.
MAX_STATES_PER_RUN = 8

#: Section 52.4: how long the state accompanying a scheduled task lasts.
#: Short on purpose - it says "something is queued", not "the agent is busy
#: with this all week", and it should stop claiming the calendar soon after
#: the task has run.
#: What every step is told, whichever step it is.
#:
#: Section 37, as amended: the steps used to be sections of one
#: prompt answered in one call. They ask different questions - what is
#: happening, who this person is, what to do about it, what to close - and a
#: single answer to all of them is one act of attention divided three ways. Asked
#: separately, each gets a prompt containing its own question and its own
#: gathered context, and each of the later ones is told what the earlier ones
#: concluded, which the single call could not do at all.
#:
#: The cost is real and worth stating: the preamble is repeated per step,
#: and the measured input roughly doubles.
PREAMBLE = """You are the background reasoning pass of a personal desktop agent.

You are given one job below. Answer that job and nothing else.

Anything you infer rather than were told carries its evidence: what you
actually saw, and how you got from that to this. The user has to be able to
check your reasoning, because you are describing their situation to them and
you can be wrong about it.

Return ONLY a JSON object, in the shape you are given."""

#: What each step is asked, and what it must return. The text is the text
#: that was in the single prompt, unchanged - only the delivery is different.
from ..connections import people as _people  # noqa: E402 - step 2's words

#: Where the user's direct chat with the agent shows among a window's events.
CHAT_APPLICATION = "Direct chat with the agent"

STEP_PROMPTS = {
    "step_state": {
        "instruction": """ANALYSE THE USER'S STATE, and write it to the state calendar.

A state is something that is TRUE OVER A PERIOD and still true now - what
they are working on, what they are trying to get done. Give its real period:
when it began and when it ends or is expected to.

Something that has already happened is not a state. "The decision was made",
"they read the paper", "the meeting finished" are facts about the past: they
are observations, and they do not belong on the calendar. A finished
thing written as a state sits in the user's calendar claiming to be their
current situation, days after it stopped being anything of the kind.

READ WHAT WAS ON THE SCREEN, NOT WHAT THE WINDOW IS CALLED. A window's title
is often a name given long before: a terminal is named after the session it
was opened for, a chat after the person in it, a document after its file.
What the user is doing is in the text read from the window and in its
keywords. Where the two disagree, the text is right and the title is old -
and an entry that says its title has been the same for hours is telling
you exactly that.

THE PERIOD YOU CAN SEE IS THIS WINDOW. You are looking at a few minutes,
not at a week. A state you write begins and ends inside the window in front
of you, because that is the only stretch you have evidence about - and a
state dated across three days on the strength of fifteen minutes is a
guess wearing a date.

Something longer-lived is one of two other things, and neither is a state
invented here:

  - the same situation seen again. Write it for this window, as this
    window saw it. A long state is made of those, one window at a time.
  - a habit rather than a situation. That is the next step's, not yours.

There is no separate label to set for the length. A state that runs an hour
is a short one and a state that has been extended across a week is a long
one, and a label beside those dates would be a second place for them to
disagree.

This is about the USER. What they are doing, what they are working towards,
what they are in the middle of. Not about you: what you intend to do is not
the user's situation, and a calendar of your own intentions described back
to them as their state is a calendar about the wrong person.

WRITE SEVERAL STATES, ONE PER THING THEY WERE ACTUALLY DOING. A window of
activity is usually two or three of them, not one and not twenty.

AT LEAST ONE, UNLESS NOBODY WAS THERE. If there is activity in front of you
then something was going on, and returning nothing says the opposite: it
puts an empty stretch on the calendar for a stretch that was not empty, and
whatever is read back later reads it as nothing having happened. The one
exception is a window that really is idle - the machine locked, or a handful
of background events with nobody at the keyboard. Then write no state and
say so in `state_analysis`.

BEING AT THE DESK IS NOT A STATE. Coming back, stepping away, the screen
locking, going idle, how long a window sat in front before the lock - these
say whether the user was there, not what they were doing. So none of these
is a state:

  - "Back at the desk at 6:37 PM, after a thirteen-minute lock"
  - "Away from the desk since about 8:57 PM"
  - "The morning block ended mid-session - desk idle since 11:42, locked
    at 11:51"

If they came back and did something, the state is the something - "They
are checking the restarted build" - and the return is not mentioned in it.
If they were away for the whole window, write no state. Either way, whether
they were there goes in `state_analysis` and nowhere else.

EACH ONE IS A HIGH-LEVEL SUMMARY, NOT AN EVENT. Opening a file is not a
state; what the file was being opened FOR is. Two examples of the right
altitude:

  - "They are working on the survey-weights replication" - covering an
    editor on survey_weights_rebuild, a browser on the
    weighting paper and a terminal running the scripts. One state,
    three applications.
  - "They are watching videos on YouTube" - covering nine different video
    pages. One state, nine URLs, and the individual videos are not nine
    states.

And two of the wrong altitude, so you can see the line:

  - "They opened search.py" - that is an event. It belongs inside a state
    about whatever search.py is being changed for.
  - "They are using the computer" - that is every state at once and says
    nothing.

WHAT THE USER COMMITTED TO, IN THEIR OWN WORDS. When the user writes or
sends something saying they will do a thing later - "Happy to find a time
next month", "I'll send the revised draft by Friday", "let's talk next
week" - that promise goes in the state's `detail`, quoted, with the time
they gave. Read it from what they typed: the compose window, the sent
message, the chat box. And keep whose is whose: an offer the OTHER person
made is not the user's commitment. Replying to Prof. Okafor's offer to
meet with "Happy to find a time next month" is the
user promising to write next month - and a state that records only
the offer has lost the one thing they said they would do.

EACH STATE CARRIES ITS OWN NUMBERS.

  - `minutes`: THE DURATION OF THIS ACTIVITY, IN MINUTES. REQUIRED in every
    state. Definition: how long the activity actually went on in the window
    you were given - the time the user was really at it, not the span from
    its first sign to its last. Ten minutes on a draft with a five-
    minute detour in the middle is ten, not fifteen. The events carry their
    times; add up the stretches that were this. It is how much work a task
    is found to hold, so never leave it out or write 0 for something that
    happened. The minutes across all your states should not exceed the
    window.
  - `sources`: every file, page or window this state rests on, written out
    in full - the whole URL, the whole path, the whole window title. This is
    what makes the state checkable: a reader who disagrees with it needs to
    see what it was built from, not a description of what it was built from.
  - `materials`: the ids of the events that are THE MATERIAL OF THE WORK in
    this state - what someone doing this work again would want to open: a
    picture taken of a slide or figure (screen.shot), a document, email or
    page that was read, a file opened or saved, the user's own words to an
    agent - and what the work PRODUCED: a file saved, an agent's answer that
    was a result, a message sent. Copies of each are kept with the state,
    and anything left out is
    gone: the pictures no state names are deleted when this review ends,
    and the raw activity about a week later. LINK GENEROUSLY - anything
    plausibly part of the work. A material nobody needs costs a little
    disk; one left out is lost. Not leisure the work had nothing to do
    with.

THREE WORKED EXAMPLES, so the shape is not left to be guessed at.

All three are from one fifteen-minute window, which is the length of
window you are usually given. Notice that the minutes add up to it and the
periods sit inside it.

One project, across three applications, taking most of the window:

  {"title": "They are rebuilding the SWR replication package, checking "
            "the scripts against the published figures.",
   "detail": "Working through the replication scripts and checking them "
             "against the published figures. The census fetch is the "
             "part being changed.",
   "minutes": 9,
   "starts_at": "2026-03-04T14:02:00Z", "ends_at": "2026-03-04T14:11:00Z",
   "sources": ["C:/Users/sam/Research/survey_weights_rebuild/scripts/census_api/fetch.py",
               "C:/Users/sam/Research/survey_weights_rebuild/figures/fig2.ipynb",
               "https://example.org/papers/survey-weights-2023"],
   "evidence": ["the editor on fetch.py for most of the window",
                "the paper open in a second window alongside it"],
   "reasoning": "the file being edited and the page being read are two "
                "halves of the same task"}

Something in the same window that is not work at all:

  {"title": "They are watching videos on YouTube, unrelated to "
            "anything they have been working on.",
   "detail": "Four video pages in a row, none of them related to "
             "anything they have been working on.",
   "minutes": 4,
   "starts_at": "2026-03-04T14:11:00Z", "ends_at": "2026-03-04T14:15:00Z",
   "sources": ["https://www.youtube.com/watch?v=Ex4m1a0001A",
               "https://www.youtube.com/watch?v=Ex4m1a0002A",
               "https://www.youtube.com/watch?v=Ex4m1a0003A"],
   "evidence": ["four video pages, one after another"],
   "reasoning": "a run of unrelated video pages is watching, not research"}

And a third thing in the same window, brief but distinct - two minutes is
still a thing they did, and leaving it out says the window held only the
first two:

  {"title": "They are getting the release out.",
   "detail": "Back in the release directory briefly, on the changelog.",
   "minutes": 2,
   "starts_at": "2026-03-04T14:15:00Z", "ends_at": "2026-03-04T14:17:00Z",
   "sources": ["C:/Users/sam/Desktop/personalized_agent_release/CHANGELOG.md"],
   "evidence": ["the changelog edited again, two minutes before the end"],
   "reasoning": "a different directory and a different file from the "
                "replication work, so it is a different thing being done"}

THE TITLE IS ONE COMPLETE SENTENCE, AND ITS SUBJECT IS THE USER. Every
state is a sentence about them - "the user is...", "they are..." - because
every state on this calendar is a claim about their situation and nothing
else. Not the file ("search.py is being edited"), not the application
("Firefox is open"), not you ("I will check the release"): those are a
thing, a window and your own intention, and none of them is the person the
calendar is about.

Not a phrase, not a fragment, not a label either: a sentence with a subject
and a verb that reads on its own, because it is the line the user sees on
their calendar with nothing around it. "Replication work" and "YouTube"
are labels - they name a subject and say nothing about it. "They are
rebuilding the SWR replication package" is a sentence about the user, and a
reader who sees only that line knows what it means.

THE DETAIL SAYS WHAT THE STATE IS - what they are doing and what it is in
aid of. The detail does NOT say how long it lasted or when it happened:
those are `minutes`, `starts_at` and `ends_at`, and repeating them in prose
means two places to be wrong.

A state is shown to the user, never executed - the scheduler is what runs
things.

COVER EVERYTHING IN THE WINDOW. List every distinct thing they were doing,
including the ones they were already at before it started. A window where
they carried on with three things is a window with three states in it, and
returning one for it describes a different evening from the one that
happened.

You are not shown what is already on the calendar, and you are not meant to
be. This window is the evidence; the calendar is what earlier runs made of
earlier windows, and reading it first is how a review ends up confirming
last hour's conclusions instead of looking at this hour's activity. Write
what this window shows, in the words this window supports.

YOU DECIDE WHAT YOU NEED TO SEE. What you are given to start with is
everything that has happened since this review last ran - what happened,
not what it means. If that is enough, answer. If it is not, ask, and keep
asking until it is:

  - `open_by_id` reads anything whose id is in front of you, whole: an
    activity line's events (event_...), a state (state_...), a task or a
    notification (task_..., notif_...). The keywords on a line are a
    summary; what matters is often in the full reading.

There is nothing to search beyond this window: what you are shown is what
there is to go on. Answer when you have what you need and not before.
Guessing is the failure this is built to prevent.

WHAT THE AGENT DID ITSELF. Events marked "by: THE AGENT'S OWN WORK RUN" are
the agent working on a project for the user - files it wrote, code it ran,
pages it read - not the user's own activity. Write ONE state for each run
in the window, with "whose": "agent": its title a sentence whose subject is
the agent ("The agent worked on ..."), its detail what it did, its materials
those events. Nothing else is drawn from them: they are not the user's
work, habits or nature. Every other state is "whose": "user".

WORK, LEISURE OR ROUTINE. Give every state a "kind" beside its other fields.
Anything done towards something - a project, a paper, a job, a course, an
application, a trip being planned - is "kind": "work", however personal.
What is done for enjoyment - films, music, shows, games, recaps, sport,
hobbies, the news, browsing for fun, chatting with friends - is "kind":
"leisure". The rest of a day that is neither - meals, rest, errands,
chores - is "kind": "routine"; it stays on the calendar and is never grouped
into a task.""",
        "returns": """{
  "state_analysis": "what the user appears to be doing and what matters now",
  "states": [
    {"title": "ONE COMPLETE SENTENCE whose subject is THE USER. "
              "Not a phrase and not a fragment, and not a sentence about a "
              "file, an application or about you: a sentence about them, "
              "which reads on its own.",
     "detail": "what this state IS. Not how long it lasted and not when - "
               "those are the fields below. What they are doing and what "
               "it is in aid of.",
     "minutes": "REQUIRED, a number: the DURATION in minutes - how long this activity actually went on in the window, not its span",
     "starts_at": "ISO-8601 with the user's UTC offset, when this actually began",
     "ends_at": "ISO-8601 with the user's UTC offset, when it actually ends or is expected to",
     "sources": ["REQUIRED, never empty: every file, page or window this "
                 "rests on, written out in full - the whole path, the whole "
                 "URL, the window title as the activity shows it. Where the "
                 "work lives is read from these, so a state without them "
                 "cannot be told apart from others worded like it"],
     "evidence": ["what you saw that made you believe this - the actual "
                  "activity, file, page or message, not a restatement"],
     "materials": ["the event ids, from the ids lines, of what this "
                   "state's work was done with or on"],
     "reasoning": "how that evidence gets you to this state",
     "kind": "work, leisure or routine",
     "whose": "user, or agent for the agent's own work run"}
  ]
}""",
        "wants_activity": True,
        # Section 34.1, as amended: the states in English whatever the
        # display language is - they are compared with one another, and a
        # calendar half in one language and half in another does not match
        # itself. The rest of what it writes the user reads.
        "language": in_display() + in_english(
            "the states - their title, detail, evidence and reasoning"),
    },
    "step_people": {
        "instruction": _people.INSTRUCTION,
        "returns": _people.RETURNS,
        "wants_activity": True,
        # A person's tags are found by: one language.
        "language": in_display() + in_english("the tags you give a person"),
    },
    "step_nature": {
        "instruction": """WORK OUT WHAT THE USER IS LIKE - their PERSONAL NATURE, which goes to
user_nature/, where it keeps: so write only what would still be true next
month.

PERSONAL NATURE is who they are outside the results of their work: what a
companion who knew them well would know. Habits and rhythms - when they
work, rest and sleep, how they move through a day, how they switch between
things. Hobbies and interests - what they watch, read, play, follow. What
they care about, what wears them down, how they unwind, the people and
places in their life.

THIS STEP WRITES PERSONAL NATURE ONLY. Their work nature - their taste and
standards, how they decide, what they send back, how they work with their
agents - is learnt from the stages of their work, which record all of
that in detail; it is not written here. A habit at work is still personal
nature: "the user checks running jobs in brief glances" is how they move
through a day. What a piece of work should look like is not.

EVERY RECORD RESTS ON STATES. You are given a window of the user's activity
and the states written from it, numbered. Give, in `states`, the numbers of
the states each record rests on - at least one. A record that names no
state of this window is not written.

YOU DECIDE WHAT YOU NEED TO SEE, WITHIN THIS WINDOW: `open_by_id` reads a
state or an event of it whole. There is nothing to search beyond it. One
evening is an evening, and a habit is a habit only when this window shows
it more than once - say how sure you are accordingly.

EVERY RECORD SAYS HOW SURE YOU ARE. `confidence` runs 1 to 7, and every
step of it means something:

  7 - the user said it themselves, or you have watched it hold for weeks
      with nothing against it.
  6 - many occasions across a long stretch, and no counter-examples.
  5 - several occasions, no counter-examples, but not yet a long stretch.
  4 - a couple of occasions, and it is the obvious reading of them.
  3 - a couple of occasions, but another reading fits them just as well.
  2 - one occasion that looked like a pattern.
  1 - a guess you are recording so that it can be checked later.

Be honest about the low end. A 2 that is written down as a 2 is useful - it
can be confirmed or dropped next month - and a 2 written as a 6 is a thing
the agent will act on and keep acting on. Nothing here is urgent enough to
be worth overstating.

AN EMPTY LIST IS A GOOD ANSWER whenever this window holds nothing worth
recording. Judge each window on what it shows, not on how often you have
written before. Write something only when it is genuinely new about this
person and likely to still be true next month. A restatement of what they did this
window, or of a record already written, is not worth recording.

Write nothing rather than write a habit you have seen once.

EXAMPLES. Worth recording:

  {"kind": "personal",
   "title": "The user unwinds with cooking videos between stretches
             of work, a few minutes at a time.",
   "content": "Short videos fill the gaps while something runs; they come
               back to the work when it is done, not on a schedule.",
   "confidence": 3,
   "evidence": ["Tonight: video between three agent runs",
                "Earlier in this window the same"],
   "reasoning": "Twice - and another reading, that those were nights with
                 nothing urgent, fits as well. So a 3.",
   "states": [4, 6]}

  {"kind": "personal",
   "title": "The user keeps up with research news in their field through
             newsletter articles, read in the evening.",
   "content": "Newsletter articles about conference deadlines - publishing, peer review,
               careers - most evenings, between other things.",
   "confidence": 4,
   "evidence": ["Tonight: an article about conference deadlines",
                "Two more such articles earlier in this window"],
   "reasoning": "The same shape three times; it is the obvious reading.",
   "states": [2]}

Not worth recording - and why:

  - "The user wants a design talked through before any code is changed."
    Work nature - it comes from the stages of their work, not from here.
  - "The user is fixing a bug in the agent's heartbeat." What they are
    doing, not who they are: that is a state, and step one wrote it.
  - "The user is interested in Japanese food." One video is one evening.
  - "The user uses Firefox." True, and it changes nothing.
  - A record already in memory, reworded. Searching first is how you know.""",
        "returns": """{
  "user_nature": [
    {"kind": "personal",
     "title": "ONE COMPLETE SENTENCE whose subject is the user, saying the "
              "thing you now believe about them. Not a phrase and not a "
              "label: a sentence that reads on its own.",
     "content": "a short paragraph. What it is, how it shows, and where its "
                "edges are.",
     "confidence": 4,
     "evidence": ["what you saw that made you believe this - the actual "
                  "activity, record or message, not a restatement"],
     "reasoning": "how that evidence gets you to this trait, and what "
                  "would change your mind",
     "states": ["the NUMBERS of the states above that this rests on, as "
                "they are listed - [state 2] is 2. At least one."]}
  ]
}

When nothing is worth recording, return {"user_nature": []}.""",
        "wants_activity": True,
        # Natures are kept, and compared, in English.
        "language": in_english("the natures"),
    },
    "step_notify": {
        "instruction": """DECIDE WHICH NOTIFICATIONS NEED TO BE ADDED, AND WHICH NEED TO BE
DISABLED.

You are given the same window of the user's activity that the states were
read from, the states that have just been written from it, and EVERY ACTIVE
NOTIFICATION FROM NOW ON - each with its id, when it will pop up, and why it
was made. You answer two things from those three:

  - what to ADD. Each one goes on the user's calendar and, at the time you
    give it, pops up in front of whatever they are doing.
  - what to DISABLE: a listed notification whose thing has already been
    done. It stays on the calendar, marked closed with your reason, and
    never pops up.

DO NOT ADD WHAT IS ALREADY THERE. Read the list before adding anything. A
notification about the same thing as one listed is a duplicate however it
is worded - and adding it interrupts the user twice for one thing.

YOU CAN READ MORE OF THIS WINDOW, for both halves, whenever its lines alone
do not settle it - there is nothing to search beyond it:

  - `open_by_id` reads anything whose id is in front of you, whole: an
    activity line's events (event_...), a state (state_...), a listed
    notification (task_...). The keywords on a line are a summary; the
    sentence that matters - "Message sent", "I'll send it Friday" - is in
    the full reading.

THERE ARE TWO KINDS, and you choose one for each:

  - "reminder" - something for the user to DO: send the reply they said
    they would, take the break before the meeting, come back to the draft
    they left. The user answers "Got it", which dismisses it (no more
    notifications), "Add to to-do", which dismisses it onto their to-do list
    on the Overview - you see it among the open to-dos - "Remind me
    later", which brings it back in 30 minutes, or calls it a bad
    notification - which is a lesson you are given.
  - "notice" - something for the user to KNOW, with nothing asked of them:
    a deadline is tomorrow, the build they started has had time to finish.
    It is answered with "OK".

A PROMISE THE USER MADE IS THE FIRST THING TO LOOK FOR. When the user
writes, sends or says that they will do something later - "Happy to find
a time next month", "I'll send it Friday", "let's talk after the
deadline" - add a reminder at that time. This is what reminders are for:
by then the email it was written in is buried, and the promise is kept
only if something brings it back. Look for it in what the user TYPED - the
compose window, the sent message, the chat box - and in the states'
details, which quote it.

  - TURN A LOOSE TIME INTO A MOMENT, and say how in "why". "Next month"
    is a weekday morning in its first full week; "next week" is Monday at 9:00;
    "in a couple of months" is a morning about eight weeks on. However far
    ahead it is, it is still worth a reminder.
  - THE SAME PERSON IS NOT THE SAME REMINDER. "Reply to Prof. Okafor", due
    today, and "Find a time with Prof. Okafor", next month, are two
    things at two times. A reminder already listed about the same person
    or thread is a duplicate only if it is about the same thing at about
    the same time.

A NOTIFICATION IS AN INTERRUPTION, and the user pays for every one. Add one
only when the activity shows a concrete reason the user would want it at
that moment, and would be annoyed to have missed it. An empty list is the
right answer most of the time.

NEVER ADD WHAT IS ALREADY SCHEDULED. The ones already on the calendar are
listed; one about the same thing, at a nearby time, is a duplicate however
it is worded.

WHEN is part of the answer. Give the moment it is useful - before the
deadline, after the build should have finished, when the meeting is about
to start - and not a moment that has already passed.

""" + _HOW_TO_GIVE + """

A NOTIFICATION CAN REPEAT - when the activity shows something that itself
comes back on a schedule: a meeting every Monday at 10, a stand-up each
weekday morning, a form due on the first of every month, a dose to take
every eight hours. Then give "repeat": {"every": 1, "unit": "weeks"} (unit
one of minutes, hours, days, weeks), and end it where the activity says it
ends - "until" (YYYY-MM-DD HH:MM on the same clock) or "count" (how many
times); with neither it repeats until the user stops it, so leave both out
only when nothing says it ever ends. "at_local" is the first time; a repeat
of days or weeks keeps that time on the clock of its "zone" - a weekly
meeting in Paris stays at 10:00 Paris time when either side's clocks change. Never repeat a
reminder about one thing that happens once - the reply still to send, the
deadline still to meet: that is one notification, not a series. Leave
"repeat" out for every notification that happens once.

Write it for the user. The title is what they see first and the message is
one or two plain sentences. Your evidence and reasoning go in "why", which
is shown only if they ask.

EVERY NOTIFICATION YOU ADD SAYS WHY - what in the activity or the states
called for it, and why it is worth interrupting for at that moment. One with
no "why" is not added. When the user later deletes it, disables it or calls
it a bad notification, your reason is what shows what was misread.

EXAMPLES. Worth adding:

  - The user replied to Prof. Okafor's email offering to meet
    with "Thanks for reaching out - happy to find a time
    next month!" - and sent it. Already listed is "Reply to Prof.
    Okafor", for this afternoon; that is about replying, which is now done,
    not about next month:
      {"type": "reminder", "title": "Find a time with Prof. Okafor",
       "message": "You told him you would write again next month to
                   set up a chat about the research.",
       "at_local": "Monday 2 November 09:00 as YYYY-MM-DD HH:MM", "zone": "user",
       "why": "Their sent reply promises to find a time next month; the first
               full week of November starts on the 2nd, and nothing on the
               calendar is about it."}
  - The user wrote in a mail "I'll send the revised draft by Friday", and
    nothing on the calendar is about it:
      {"type": "reminder", "title": "Send the revised draft",
       "message": "You told the editor you would send it by Friday.",
       "at_local": "Friday 09:00 as YYYY-MM-DD HH:MM", "zone": "user",
       "why": "The promise in this morning's reply; no reminder exists."}
  - The user started a training run whose log says it needs about forty
    minutes:
      {"type": "notice", "title": "The training run should be done",
       "message": "It started at 14:05 and estimated forty minutes.",
       "at_local": "today about 14:50", "zone": "user",
       "why": "The estimate in the run's own output."}
  - A page the user read gives a submission deadline of tomorrow at 23:59,
    and the calendar has nothing about it:
      {"type": "notice", "title": "Submission deadline tomorrow",
       "message": "The workshop deadline is tomorrow at 23:59 AoE.",
       "at_local": "this evening", "zone": "user",
       "why": "The call for papers read at 16:10."}
  - An invitation from a colleague in Berlin, read by a user in New York:
    "Can we talk Thursday at 3pm?" - signed with the Berlin office, no zone
    named:
      {"type": "notice", "title": "Call with Jonas in ten minutes",
       "message": "The call he proposed for Thursday 15:00 his time.",
       "at_local": "Thursday 14:50 as YYYY-MM-DD HH:MM", "zone": "Europe/Berlin",
       "as_written": "Thursday at 3pm",
       "why": "His email proposes Thursday 3pm; he writes from the Berlin
               office, so the time is Berlin's."}

Not worth adding - and why:

  - The user is watching videos or reading the news. Nothing in it asks
    for anything.
  - The user is writing the draft right now. A reminder to do what they
    are doing is an interruption with nothing in it.
  - The reply is due Friday and a reminder for Friday morning is already
    listed, worded differently. That is a duplicate.
  - Something that will be on screen in front of them anyway before it
    matters - the next look, in a few minutes, can still catch it.

AND DISABLE WHAT IS ALREADY DONE.

A notification was right when it was made: the user had a reply to send, a
form to finish, a call to make. Since then they may have done it. If they
have, it will still pop up in front of them, telling them to do what is
already done - which is worse than no notification at all.

So, for each one, ask: HAS THE THING IT IS ABOUT ALREADY HAPPENED? Close it
only when the activity shows that it has:

  - Close: "Finish your reply to Prof. Moreau", and the activity shows the
    reply being sent - the Gmail window read "Message sent", or the sent
    folder shows it.
  - Close: "Check whether you answered the conference email", and the user
    answered it in this window.
  - Close: "Submit the travel form by Friday", and the form's confirmation
    page was on screen.

And do NOT close:

  - The user opened the email but there is no sign they sent anything.
    Reading is not replying.
  - It is about something that has not come up in this window at all.
    Not seeing it done is not seeing it done - most notifications should be
    left exactly as they are.
  - A notice - something to know at a time, like "the call starts in ten
    minutes" - whose time has not come. It is about the time, not a task.

YOU CAN LOOK THINGS UP. Every activity line carries the ids of the events it
stands for, and every notification its own id. `open_by_id` returns the
whole of any of them, exactly - a screen reading in full, a notification
with everything recorded about it. Use it before closing on a guess: the
sentence that says the reply went is usually in the full reading, not the
keywords.

Closing is not deleting. A closed notification stays on the calendar,
marked closed, with your reason under it - so the reason has to say what in
the activity showed it was done.

AND COMPLETE WHAT HAS NEW DETAILS.

A notification is made from what was known then. Since, more may have
come: the meeting's link was sent, its room or its agenda; it was moved to
another time; there is a document to bring, a number to call. Then the
notification the user will see should carry it - so UPDATE it, do not add
another about the same thing:

  - "Meeting with Prof. Tan at 15:00", and an email now gives its Zoom link:
    update it - the message with the link in it, the link in "details".
  - "Dentist on Thursday at 10:00", and a text moves it to 11:30: update its
    time.

Keep everything it already says that still holds, and add what is new;
change what was corrected. Only with something new from the activity -
nothing invented, nothing it already said. Each update says why, and what
in the activity shows it.

""" + _PENDING_INSTRUCTION,
        "returns": """{
  "notifications": [
    {"type": "reminder|notice",
     "title": "short, what the user sees first",
     "message": "one or two plain sentences for the user",
     "at_local": "YYYY-MM-DD HH:MM on the clock of zone - when it pops up (the first time, if it repeats)",
     "zone": "user, or the Region/City name of the place whose clock at_local is on (Europe/Paris), or UTC",
     "as_written": "OPTIONAL: the time exactly as the source wrote it, when it came from one",
     "repeat": "OPTIONAL, only for something that itself comes back on a schedule: {\"every\": 1, \"unit\": \"minutes|hours|days|weeks\", \"until\": \"YYYY-MM-DD HH:MM on the same clock\" or \"count\": N}",
     "why": "REQUIRED: the activity or state that calls for it, and why it is worth interrupting for then; not shown unless asked",
     "lessons": ["the ids of the lessons about the user's notifications this one follows, if any (les_...)"]}
  ],
  "closed": [
    {"id": "a listed notification's id, exactly (task_...)",
     "why": "one or two sentences for the user: what showed it was done",
     "evidence": ["the ids of the events or states that show it"]}
  ],
  "updated": [
    {"id": "a listed notification's id, exactly (task_...)",
     "message": "OPTIONAL: the whole message as it should now read - what it said that still holds, and the new detail",
     "title": "OPTIONAL: a corrected title",
     "at_local": "OPTIONAL: its new time, YYYY-MM-DD HH:MM on the clock of zone, when it moved",
     "zone": "with at_local: user, or the Region/City name of the place whose clock it is on, or UTC",
     "as_written": "OPTIONAL: the new time exactly as the source wrote it",
     "details": {"OPTIONAL - each new detail by name, e.g. link, place, agenda, phone, bring": "its value, exactly as the activity gave it"},
     "why": "one or two sentences for the user: what is new, and where it came from",
     "evidence": ["the ids of the events or states that show it"]}
  ],
  "pending": [
    {"title": "short: what is waiting for a time, and with whom",
     "detail": "one or two sentences: what was agreed, and what is still missing",
     "people": ["the names of the people it is with"],
     "evidence": ["the ids of the events or states that show it"]}
  ],
  "pending_resolved": [
    {"id": "a waiting one's id, exactly (wait_...)",
     "why": "where its time came from - the notification added for it"}
  ],
  "pending_dropped": [
    {"id": "a waiting one's id, exactly (wait_...)",
     "why": "what showed it is off"}
  ]
}""",
        "wants_activity": True,
        "language": in_display(),
    },
}


#: Asked when a notification step 3 proposes looks like one already waiting
#: near the same time. The rule is the user's: a repeat is not created - but
#: the model, having looked at both, may insist, and has to say why.
SAME_NOTIFICATION_PROMPT = """IS THIS NOTIFICATION ALREADY SCHEDULED?

You proposed the notification marked NEW. Already on the user's calendar,
within half an hour of it, are the ones listed below it, which look alike.

Say whether NEW is the same as one of them: the same thing to tell the user
at about the same time, however differently it is worded. If it is, it will
not be created - the user would be interrupted twice for one thing.

If it is genuinely different - a different thing, or something the user
needs to hear a second time - you may insist on creating it. Say why; the
reason is kept with it.

Return ONLY this JSON object:
{"same_as": "the id of the one it repeats, or null",
 "create_anyway": false,
 "why": "one sentence"}"""


ZONE_AGAIN_PROMPT = """WHOSE CLOCK IS THIS TIME ON?

You gave a notification's time on a clock whose "zone" was refused - why is
said below - so the moment it is for cannot be worked out. Name the place
whose clock it is, from what the notification and the activity it came from
say. An abbreviation (PST, EST, CET) is the place it is used for - EST in a
New York email is America/New_York, daylight saving and all; one used in
more than one place (CST, IST) is the one the sender or the event is in.

WHAT IS ACCEPTED: """ + _PLACE_RULE + """

Your answer is held to the same rule. When nothing says which place it is,
give an empty zone - the time is then taken as the user's own, and the user
is told so.

Return ONLY this JSON object:
{"zone": "the Region/City name, or empty", "why": "one sentence"}"""


#: Every instruction the heartbeat gives, as one text.
#:
#: Nothing sends this. Each step is sent its own part of it - see the
#: loop in run_once - and it is assembled here so that "what is the
#: heartbeat told" has one answer, for a reader and for the tests that
#: check a particular instruction is still given at all.
SYSTEM_PROMPT = PREAMBLE + "\n\n" + "\n\n".join(
    STEP_PROMPTS[name]["instruction"] + "\n\n" + STEP_PROMPTS[name]["returns"]
    for name in ("step_state", "step_people", "step_nature", "step_notify"))


#: The steps that decide for themselves what they need to see.
#:
#: Both are reading the same window of activity for different things - what
#: the user is doing now, and what doing it repeatedly says about them - and
#: neither can have its query written in advance, because the words worth
#: searching for are in the answer.
GATHERS_FOR_ITSELF = ("step_state", "step_people", "step_nature", "step_notify")

#: Steps kept - their prompts and code - and not run. Step 2, who the user
#: is, is paused: what the user is like is learned from their lessons now
#: (heartbeat/experience), not guessed from a few minutes of activity.
PAUSED_STEPS = ("step_nature",)

#: How far the summaries of activity got: the end of the stretch the last
#: review that answered had read. Kept in the settings, so it outlives the
#: process - what happened between that review and the agent being closed
#: is read by the next one, not lost.
SUMMARISED_UNTIL = "heartbeat_summarised_until"
#: Closed for longer than this, the stretch from before is read ON ITS OWN -
#: a late look, soon after the agent starts - rather than run together with
#: what happens after, across a gap the reader could not see.
LOOKED_AT_ALONE_MINUTES = 30
#: However long the gap since the last review, it is read whole - nothing is
#: skipped, and time alone never splits it: closed for months, nothing was
#: recorded, and the gap is one quick read. Only what one review cannot
#: carry (more than STATE_FLOOR_EVENTS recorded since - the model away while
#: the listener went on) is read in turns, oldest first, one straight after
#: another, until the reviews have caught up (`_late_stretch`).
#: How long after the agent starts the late look waits: not in the middle
#: of everything else starting.
CATCH_UP_AFTER_SECONDS = 60
#: Catching up, the pause between one turn and the next.
CATCH_UP_TURN_SECONDS = 5
#: A review under way: when it began - cleared when it ends. Still set when
#: the agent starts, the last one never finished (the agent was closed in
#: the middle of it), and it is done again straight away.
RUN_STARTED_AT = "heartbeat_run_started_at"
#: When the last review ended, whatever came of it - so a model that would
#: not answer is asked again an interval later, not in a loop.
RUN_TRIED_AT = "heartbeat_run_tried_at"

#: What a step may ask for when its floor is not enough.
#:
#: Within its own window: the step chooses what of it to read whole. A fixed share per folder decided in advance is a guess made before
#: the question is known - and it was being made by code that could not see
#: the activity it was allocating room for.
STATE_TOOLS = [
    ToolSpec(
        "open_by_id",
        "Read anything by its id, exactly and in full. The id says what it "
        "is, so there is no folder to choose: event_ is something the user "
        "did, as it was recorded - a screen reading with all of its text, a "
        "window, a page, a file; edit_ is a change the agent made; state_ is "
        "a state on the calendar; task_ is a scheduled task or notification, "
        "with why it was made; notif_ is a "
        "notification that was shown. The ids are printed beside what they "
        "stand for in your context. Ask for several at once.",
        {
            "type": "object",
            "properties": {"ids": {"type": "array",
                                   "items": {"type": "string"},
                                   "description": "the ids, exactly"}},
            "required": ["ids"],
        },
    ),
]


#: How many of a group's own times are listed before the rest are counted.
#:
#: A range is an answer to a question nobody asked. "x3 over 02:20-02:21"
#: hides whether that was three times in four seconds or three times in a
#: minute, and those are a double-click and a person going back and forth.
#: Twelve is enough to see the rhythm of a group; past that the rhythm is
#: the count and the span.
TIMES_PER_GROUP = 12

#: How many readings of one window keep their text before the rest are
#: dropped as repetitions.
#:
#: A window read every fifteen seconds for a quarter of an hour is sixty
#: readings of one page. The newest is what is on the screen now; a couple
#: behind it say whether the page changed; the other fifty-seven are the
#: same paragraph, and they are most of what the step was being charged for.
TEXTS_PER_GROUP = 3


#: Decoration a window title carries that changes while the window does not.
#:
#: Seen in the live feed: one terminal, one session, two entries fifty
#: seconds apart, identical but for the character at the front -
#: `◐ Upload instruction` and `✳ Upload instruction`. A spinner is a
#: window saying it is still working, which is the one thing about it that
#: is not worth a second row.
_SPINNER = re.compile(r"^[^\w\u4e00-\u9fff]+")

#: And the counters that go with them: an elapsed time, a percentage, a
#: step out of a total. Only these shapes, because a title is mostly names
#: and stripping digits generally would merge "Chapter 3" into "Chapter 4".
_TICKING = re.compile(r"\(\s*\d+\s*(s|m|sec|secs|/\s*\d+)\s*\)|\b\d+\s*%")


def _stable_title(title: str) -> str:
    """What is left of a title when the parts that tick are taken out."""
    text = _TICKING.sub("", str(title or ""))
    text = _SPINNER.sub("", text)
    return " ".join(text.split())


def _window_of(event: Dict[str, Any]) -> tuple:
    """Which window an event is about, whatever kind of event it is."""
    data = event.get("data") or {}
    return (str(event.get("application") or ""),
            _stable_title(data.get("window_title") or data.get("title")))


#: How far back the pages an event's keywords are weighed against go. A
#: keyword is a word frequent on this page and rare on the others, and the
#: few minutes one review reads are too few pages for "rare" to mean
#: anything - the same day the Overview weighs against.
KEYWORDS_WEIGHED_HOURS = 24

#: How long a window can keep one title before the title is said to be its
#: name rather than a description of what is in it.
TITLE_IS_A_NAME_AFTER = 2 * 3600


def _keyword_key(event: Dict[str, Any]) -> str:
    """Which page or file an event's keywords are filed under.

    A file by its path, since that is what its text was kept under; any
    other event by its window or page title reduced the way the Overview
    reduces it, so a focus event, a navigation and a reading of the same
    page find the same keywords.
    """
    data = event.get("data") or {}
    path = (data.get("path") or data.get("destination_path")
            or data.get("source_path"))
    if path and data.get("content"):
        return f"file:{path}"
    return page_key(str(data.get("window_title") or data.get("title") or ""))


def lend_readings(events: List[Dict[str, Any]]) -> None:
    """Give an event with no text of its own the window's own reading.

    Only the screen provider reads text; the application provider says a
    window came to the front and no more. So a focus event arrived as a
    title and nothing else - `firefox.exe application.focused, Personalized
    Agent` - which names the window and not one thing in it, and reads as
    an event that had nothing to say rather than one whose text is kept
    somewhere else.

    The reading of the same window is that text, so it is lent, marked as
    borrowed and stamped with when it was taken. Marked because it is
    evidence of a different kind: what the window said at some other
    moment, which is usually the same thing and is not the same claim.
    """
    readings: Dict[tuple, Dict[str, Any]] = {}
    for event in events:                     # newest first
        texts = [text for text in (event.get("texts") or []) if text.strip()]
        if texts:
            readings.setdefault(_window_of(event), {
                "text": texts[0], "at": event.get("timestamp", "")})
    for event in events:
        if [text for text in (event.get("texts") or []) if text.strip()]:
            continue
        lent = readings.get(_window_of(event))
        if lent:
            event["borrowed"] = dict(lent)


def _equal_shares(wanted: List[int], budget: int) -> List[int]:
    """How much of each text to show: everything if it fits, and if not,
    the same share each, with what a short text does not use passed on to
    the longer ones."""
    if sum(wanted) <= budget:
        return list(wanted)
    given = [0] * len(wanted)
    waiting = sorted(range(len(wanted)), key=lambda index: wanted[index])
    left = budget
    while waiting:
        share = left // len(waiting)
        index = waiting[0]
        if wanted[index] <= share:
            given[index] = wanted[index]
            left -= wanted[index]
            waiting.pop(0)
            continue
        for index in waiting:
            given[index] = share
        break
    return given


#: The events that are all about one window: switched to, switched away
#: from, and read. Folded into one entry per window.
WINDOW_KINDS = ("application.focused", "application.focus_ended", "screen.text")


def _note_window(group: Dict[str, Any], event: Dict[str, Any]) -> None:
    """What one window event adds to its window's entry: a stretch in front.

    `focus_ended` carries how long the window had been in front, so it is a
    whole stretch. A `focused` with no end after it - the newest of the
    window's focus events - is the window still in front now.
    """
    kind = str(event.get("event_type") or "")
    if kind == "screen.text":
        return
    first_focus = not group.get("_focus_seen")
    group["_focus_seen"] = True
    when = parse_iso(event.get("timestamp"))
    if when is None:
        return
    if kind == "application.focus_ended":
        try:
            held = float((event.get("data") or {}).get("duration_seconds") or 0)
        except (TypeError, ValueError):
            held = 0.0
        group.setdefault("spans", []).append(
            [(when - timedelta(seconds=held)).isoformat(), when.isoformat(),
             held])
    elif kind == "application.focused" and first_focus:
        group["in_front_since"] = when.isoformat()


def _signature(event: Dict[str, Any]) -> tuple:
    """What makes two events the same thing happening twice.

    The thing, not the moment: which application, what kind of event, and
    which file or page it was about. Two saves of one file are one fact
    about the afternoon and were two entries in the prompt.
    """
    data = event.get("data") or {}
    # ONE WINDOW IS ONE THING, whichever of the three said so. Switching to
    # it, switching away from it and reading it were three entries, and
    # only the reading had any of what was in it - so a terminal held for
    # an hour read as two entries of bare titles and one of text, and the
    # model had to work out they were the same sitting.
    if str(event.get("event_type") or "") in WINDOW_KINDS:
        return ("window", str(event.get("application") or ""),
                _stable_title(data.get("window_title") or data.get("title")))
    path = (data.get("path") or data.get("destination_path")
            or data.get("source_path") or "")
    about = (str(path) or str(data.get("url") or "")
             or _stable_title(data.get("window_title") or data.get("title")))
    return (str(event.get("event_type") or ""),
            str(event.get("application") or ""), about)


#: An id, as the agent's records write them: a kind, an underscore, and
#: hex - event_, edit_, state_, task_, rec_, notif_.
_AN_ID = re.compile(r"(?:event|edit|state|task|rec|notif)_[0-9a-f]{8,}")

#: How many of the events an activity line stands for are named on it.
#: The newest few - each opens with `open_by_id`, and a line read sixty
#: times does not need sixty ids to be read whole.
IDS_PER_LINE = 3


def fold_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The same event at different times, merged into one with a count.

    Structure the model would have to infer anyway, worked out in code
    where it is free. An editor saving on a timer writes the same event
    forty times in a quarter of an hour; a window read every fifteen
    seconds writes sixty. None of that is forty or sixty facts, and the
    step paid for every copy.

    What is kept: the newest of each group, because that is what is true
    now, with how many there were and the stretch they cover. Nothing is
    summarised away - a group of one is unchanged, and a group whose
    readings differed says so rather than presenting the last as the only.
    """
    groups: "collections.OrderedDict[tuple, Dict[str, Any]]" = collections.OrderedDict()
    for event in events:                     # newest first
        key = _signature(event)
        text = str((event.get("data") or {}).get("content") or "")
        group = groups.get(key)
        if group is None:
            folded = dict(event)
            folded["data"] = dict(event.get("data") or {})
            if key[0] == "window":
                folded["event_type"] = "window"
                folded["spans"] = []
                _note_window(folded, event)
            folded["count"] = 1
            # Every event it stands for, so any of them can be read whole.
            folded["ids"] = [event["id"]] if event.get("id") else []
            folded["first_at"] = folded["last_at"] = event.get("timestamp", "")
            folded["at"] = [event.get("timestamp", "")]
            folded["texts"] = [text] if text else []
            folded["text_at"] = [event.get("timestamp", "")] if text else []
            folded["text_keywords"] = ([list((event.get("data") or {})
                                             .get("keywords") or [])]
                                       if text else [])
            groups[key] = folded
            continue
        group["count"] += 1
        if event.get("id"):
            group.setdefault("ids", []).append(event["id"])
        if key[0] == "window":
            _note_window(group, event)
            # The title the window had last, and any keywords the newest
            # of its events found, whichever kind that was.
            for name, value in (event.get("data") or {}).items():
                group["data"].setdefault(name, value)
        # The list runs newest first, so each further one is older.
        group["first_at"] = event.get("timestamp", "") or group["first_at"]
        if len(group["at"]) < TIMES_PER_GROUP:
            group["at"].append(event.get("timestamp", ""))
        else:
            group["times_left_out"] = group.get("times_left_out", 0) + 1
        if text and text not in group["texts"]:
            # EVERY READING OF A WINDOW, not the newest three. The cap was
            # for a thing repeated - an editor saving on a timer - whose
            # texts were near copies. The screen reader keeps only what is
            # new on a window, so each of its readings is different text,
            # and the three newest of seven were the end of fifteen minutes
            # presented as all of it. The budget, not a count, bounds them.
            if (group.get("event_type") in ("screen.text", "window")
                    or len(group["texts"]) < TEXTS_PER_GROUP):
                group["texts"].append(text)
                group["text_at"].append(event.get("timestamp", ""))
                group["text_keywords"].append(
                    list((event.get("data") or {}).get("keywords") or []))
            else:
                group["texts_left_out"] = group.get("texts_left_out", 0) + 1
    out = list(groups.values())
    for group in out:
        group.pop("_focus_seen", None)
        if group.get("spans"):
            group["spans"].reverse()            # oldest first, as read
    return out


def _spans_said(event: Dict[str, Any]) -> List[str]:
    """When a window was in front, as each stretch with how long it lasted."""
    out = []
    for began, ended, held in event.get("spans") or []:
        out.append(f"{_clock(began)}-{_clock(ended)} "
                   f"({_held_for(float(held))})")
    if event.get("in_front_since"):
        out.append(f"{_clock(event['in_front_since'])}-"
                   + (f"{_clock(event['in_front_until'])} (when the agent was closed)"
                      if event.get("in_front_until") else "now"))
    return out


def _held_for(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes}m {rest:02d}s" if minutes < 60 else \
        f"{minutes // 60}h {minutes % 60:02d}m"


#: How many events a trail row shows before it says how many more.
#:
#: The trail said "12 events" - a count of the thing the step was reading,
#: in a panel whose whole job is to say what it read. The events are the
#: answer to almost every question somebody opens this with, and the count
#: was the one part of them that could not answer any of it.
EVENTS_IN_TRAIL = 40

#: And how much of each event's text, which is what it was actually doing.
EVENT_TEXT_IN_TRAIL = 240

#: How many states a trail row shows whole before it says how many more.
STATES_IN_TRAIL = 20


def _unfolded(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The raw feed in the shape the renderer expects, with no merging."""
    out = []
    for event in events:
        data = dict(event.get("data") or {})
        text = str(data.pop("content", "") or "").strip()
        if text:
            data["content"] = text[:CONTENT_PER_EVENT_SHOWN]
        out.append({**event, "data": data, "count": 1,
                    "at": [event.get("timestamp", "")],
                    "first_at": event.get("timestamp", ""),
                    "last_at": event.get("timestamp", "")})
    return out


#: How much of one event's text the unfolded comparison shows. The same cap
#: the folded one uses, so the two are the same rendering of the same feed
#: and the difference between them is the folding and nothing else.
CONTENT_PER_EVENT_SHOWN = 700


def _event_for_trail(event: Dict[str, Any]) -> Dict[str, Any]:
    """One event, with the detail that says what it was."""
    data = event.get("data") or {}
    path = (data.get("path") or data.get("destination_path")
            or data.get("source_path") or "")
    reads = list(data.get("reads") or [])
    text = str(data.get("content") or (reads[-1]["text"] if reads else ""))
    return {
        "at": _clock(event.get("timestamp", "")),
        # How many of this there were, and when each of them was. Not the
        # stretch: three in four seconds and three in a minute are a
        # double-click and a person going back and forth.
        "count": int(event.get("count") or 1),
        # A window's are its stretches in front; anything else's, when.
        "at_times": (_spans_said(event) if event.get("event_type") == "window"
                     else [_clock(at) for at in (event.get("at") or [])]),
        "times_left_out": int(event.get("times_left_out") or 0),
        "application": event.get("application") or "",
        "event_type": event.get("event_type") or "",
        "title": str(data.get("window_title") or data.get("title") or "")[:200],
        "keywords": [str(word) for word in (data.get("keywords") or [])],
        "url": str(data.get("url") or "")[:300],
        "path": str(path)[:300],
        "text": text[:EVENT_TEXT_IN_TRAIL]
                + ("…" if len(text) > EVENT_TEXT_IN_TRAIL else ""),
        "also_read": [str(other)[:EVENT_TEXT_IN_TRAIL]
                      for other in (data.get("also_read") or [])]
                     + [f"{one.get('at', '')}: {str(one.get('text', ''))[:EVENT_TEXT_IN_TRAIL]}"
                        for one in reads[:-1]],
        "borrowed": str(data.get("borrowed") or "")[:EVENT_TEXT_IN_TRAIL],
        "borrowed_at": str(data.get("borrowed_at") or ""),
        "texts_left_out": int(event.get("texts_left_out") or 0),
    }


def _events_for_trail(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"events": [_event_for_trail(e) for e in events[:EVENTS_IN_TRAIL]],
            "events_not_shown": max(0, len(events) - EVENTS_IN_TRAIL)}


class StateFloor:
    """What step 1 has in front of it, and what it went and got.

    Shaped like a gathering (section 44.1) because everything downstream -
    the step row, the Debugger panel, the token attribution - reads one: a
    trail of what happened in order, the text the model was given, and the
    records it ended up holding. The difference is when it is filled in. A
    gathering is finished before the model is asked; this one starts as the
    last few minutes and the running states, and the step adds to it.
    """

    def __init__(self, minutes: float, events: List[Dict[str, Any]],
                 states: List[Any], states_are: str = "") -> None:
        # What the states in it are, in the reader's terms. For the state
        # step they are its own earlier conclusions, shown so it does not
        # write the same thing twice; for the step after it they are what
        # this review has just concluded, which is evidence of a different
        # kind and has to say so.
        self.states_are = states_are
        self.states_about = ""
        self.minutes = minutes
        #: The stretch this floor was read from: the bounds asked for, as
        #: opposed to the events that turned up inside them.
        self.since = ""
        self.until = ""
        #: Whether the activity belongs in this context at all. The step
        #: that decides what to do reads states, not the raw feed: the feed
        #: is two thousand tokens of evidence for a question it is not
        #: being asked, and "nothing was recorded in this window" on a step
        #: that was never given a window reads as a finding.
        self.shows_activity = True
        #: Which of the states this same review wrote, by title. Not by id:
        #: they have none yet, because nothing reaches the calendar until
        #: every step has answered.
        self.states_new: set = set()
        #: The notifications already on the calendar, for the step that adds
        #: them - None for every other step, which is not shown the section.
        self.scheduled: Optional[List[str]] = None
        #: The calendar nature - what the user's own adds, disables and
        #: deletes say about the notifications they want - for the step that
        #: adds them. None for every other step.
        self.calendar_nature: Optional[str] = None
        #: What is waiting for a time (pending.py), for the step that adds
        #: notifications. None for every other step.
        self.pending: Optional[List[str]] = None
        #: The people already known and the user's changes to their
        #: contacts, for step 2 (connections/people.py). None for the rest.
        self.people: Optional[str] = None
        #: How often the heartbeat runs, said at the top of every step's
        #: context. 0 when not known, and then not said.
        self.every_minutes: float = 0.0
        #: Said instead of how often this is asked, on a late look.
        self.late: str = ""
        self.events = list(events)
        self.states = list(states)
        self.trail: List[Dict[str, Any]] = []
        self.nodes: List[Any] = []
        self.searched_for: List[str] = []
        self.searches = 0
        self.duration_ms = 0
        self.stopped_because = ""
        self.timing_id = ""
        #: Records opened in full, by id, so the same one is not fetched
        #: twice and so the context can carry what was read rather than a
        #: note that something was.
        self.opened: Dict[str, Any] = {}

    # -- what the model is shown -------------------------------------------
    def context_text(self) -> str:
        """Everything the step has, with each part saying what it is.

        The headings used to be labels - `WHAT IS HAPPENING`, `STATES
        RUNNING NOW` - which name a section without saying what is in it or
        where it came from. A reader of the prompt, model or person, then
        has to infer that one block is an observation of the user and
        another is the agent's own earlier conclusions, and those are read
        very differently: one is evidence, the other is a previous opinion.
        """
        seen = sum(int(e.get("count") or 1) for e in self.events)
        # It says it was folded. A step told "12 events" that is really
        # looking at a fold of three hundred has been given a wrong idea of
        # how busy the window was, and how busy it was is part of the
        # answer it is being asked for.
        lines = len(self.events)
        how = (f"shown as {lines} line{'' if lines == 1 else 's'} below, "
               f"because the same thing happening repeatedly is merged into "
               f"one line with a count"
               if seen > lines else "one line each")
        sections = []
        if self.shows_activity:
            sections.append([
                f"WHAT THE USER DID, in the last {self.minutes:g} minutes"
                + (f" ({_clock(self.since)} to {_clock(self.until)})"
                   if getattr(self, "since", "") and getattr(self, "until", "")
                   else ""),
                "Recorded by the listener as it happened: the files they opened "
                "or edited, the pages they read, the windows they had in front "
                "of them, and what those windows said. This is observation, not "
                "interpretation - it is the evidence you are reasoning from.",
                f"{seen} events, {how}. Newest first.",
                "What each entry is:",
                "  window - one application window: each stretch it was in "
                "front (when it came to the front, when it left, how long), "
                "and what was read on the screen from it, oldest first. A "
                "window with nothing read was in front, but its text was not "
                "captured.",
                "  browser.navigation - a page the browser loaded: its address "
                "and title, not its text (that is in its window's readings).",
                "  assistant.message, assistant.session - the user's own "
                "conversations with an AI assistant, and when such a session "
                "was active.",
                f"  chat.message ({CHAT_APPLICATION}) - the user's DIRECT CHAT "
                "WITH YOU, this agent: what the user said to you, in their own "
                "words, and what you replied - each message saying which. What "
                "they told you here is said to you on purpose; your replies are "
                "yours, not something the user did.",
                "  filesystem.opened, .modified, .created, .renamed, .deleted "
                "- a file the user opened or changed, with part of its text "
                "where it could be read.",
                "",
                self._events_text() or "Nothing was recorded in this window.",
            ])
        # Only where states belong, which is not the step that reads the
        # window. A reading of the evidence that starts from last hour's
        # conclusions is a reading that mostly confirms them - and a
        # section headed "what you already believe" holding nothing invites
        # the model to account for the emptiness instead of reading what is
        # in front of it.
        if self.states_are:
            sections.append([
                f"{self.states_are}, {len(self.states)} "
                f"state{'' if len(self.states) == 1 else 's'}",
                self.states_about,
                "",
                self._states_text() or "None. Nothing was written from this "
                                       "activity.",
            ])
        if self.scheduled is not None:
            sections.append([
                f"NOTIFICATIONS ALREADY SCHEDULED, {len(self.scheduled)}",
                "On the user's calendar now, each to pop up at its time. One "
                "about the same thing at a nearby time is a duplicate.",
                "",
                "\n".join(self.scheduled) or "None.",
            ])
        if self.pending is not None:
            sections.append([
                f"WAITING FOR A TIME, {len(self.pending)}",
                "Agreed, its time not known yet - each with its id. Resolve one "
                "when its time is seen, adding its notification; drop one that "
                "is off.",
                "",
                "\n".join(self.pending) or "None.",
            ])
        if self.people is not None:
            sections.append([
                "THE USER'S CONTACTS",
                "The people already known, each with their id - match the "
                "ones in this window to them - and the user's own changes to "
                "their contacts, which are their decisions: follow them.",
                "",
                self.people or "None yet.",
            ])
        if self.calendar_nature is not None:
            sections.append([
                "LESSONS ABOUT THE USER'S NOTIFICATIONS",
                "Learned from what they themselves did, over many of their "
                "edits - each with its id, how sure it is and how many cases "
                "back it. Follow the sure ones; lean on the rest. Name, in "
                "\"lessons\", the ids of those a notification you add follows.",
                "",
                self.calendar_nature or "None learned yet.",
            ])
        # Numbered by what is there. A context that skips from 1 to 3 asks
        # the reader - model or person - what happened to 2.
        out = [_now_said(), ""]
        # HOW OFTEN THIS IS ASKED, first, in every step's context. What an
        # answer should be depends on it: a review every fifteen minutes
        # writes what fifteen minutes showed and leaves the rest to the next
        # look, and a notification due before the next look is one that
        # cannot wait for it.
        if self.late:
            out.append(self.late)
            out.append("")
        if self.every_minutes:
            out.append(
                f"YOU ARE ASKED THIS EVERY {self.every_minutes:g} MINUTES. "
                f"This review covers the last {self.minutes:g} minutes, and "
                f"the next one will run in about {self.every_minutes:g} "
                f"minutes with whatever happens in between - so answer for "
                f"this stretch, and leave to the next look what can wait for "
                f"it. Anything that has to happen before then has to be "
                f"decided now.")
            out.append("")
        for number, (heading, *body) in enumerate(sections, start=1):
            out.append(f"=== {number}. {heading} ===")
            out.extend(body)
            out.append("")
        return "\n".join(out).rstrip()

    def events_text(self) -> str:
        """The events as the model is shown them. Public, because the
        folding step reports the same rendering before and after itself."""
        return self._events_text()

    def _events_text(self) -> str:
        """The events as lines rather than as JSON.

        A blind slice of `json.dumps` cut the feed off mid-event, so the
        oldest half of the window was a truncated string - and what it cut
        first was the text, which is the part worth reading. Each event is
        its own lines, and what does not fit is dropped as whole events.
        """
        lines = []
        for event in self.events:
            data = event.get("data") or {}
            count = int(event.get("count") or 1)
            # Each of them, not the stretch they span. Three times in four
            # seconds and three times in a minute are a double-click and a
            # person going back and forth, and a range is the same string
            # for both.
            times = [_clock(at) for at in (event.get("at") or [])]
            when = ", ".join(times) or _clock(event.get("timestamp", ""))
            if event.get("times_left_out"):
                when += f" and {event['times_left_out']} more"
            if event.get("event_type") == "window":
                reads = len(data.get("reads") or [])
                spans = _spans_said(event)
                lines.append("  ".join(filter(None, [
                    str(event.get("application", "")), "window",
                    f"in front {len(spans)} time{'' if len(spans) == 1 else 's'}"
                    if spans else "not switched to in this window",
                    f"read {reads} time{'' if reads == 1 else 's'}"
                    if reads else "nothing read from it"])))
                if spans:
                    lines.append("    in front: " + ", ".join(spans[:20])
                                 + (f" and {len(spans) - 20} more"
                                    if len(spans) > 20 else ""))
            else:
                head = "  ".join(filter(None, [
                    f"x{count}" if count > 1 else "",
                    str(event.get("application", "")),
                    str(event.get("event_type", "")),
                    f"at {when}" if count > 1 else when]))
                lines.append(head)
            if data.get("by") == "agent":
                lines.append(f"    by: THE AGENT'S OWN WORK RUN on "
                             f"{data.get('project') or 'a project'} - not the user")
            ids = list(event.get("ids") or [])
            if ids:
                lines.append("    ids: " + ", ".join(ids[:IDS_PER_LINE])
                             + (f" and {len(ids) - IDS_PER_LINE} more"
                                if len(ids) > IDS_PER_LINE else ""))
            for label in ("window_title", "title", "url", "path",
                          "source_path", "destination_path"):
                if data.get(label):
                    lines.append(f"    {label}: {str(data[label])[:300]}")
            if data.get("title_since"):
                lines.append(
                    f"    (this title has been the same since "
                    f"{_day(data['title_since'])} - it "
                    f"is the window's name, and need not say what is in it now)")
            if data.get("keywords"):
                lines.append("    keywords: " + ", ".join(
                    str(word) for word in data["keywords"]))
            shown_words = list(data.get("keywords") or [])
            for read in data.get("reads") or []:
                words = list(read.get("keywords") or [])
                if words and words != shown_words:
                    lines.append("    keywords from here: " + ", ".join(words))
                    shown_words = words
                lines.append(f"    read at {read.get('at', '')}: {read.get('text', '')}")
            if data.get("content"):
                lines.append(f"    text: {data['content']}")
            for other in data.get("also_read") or []:
                lines.append(f"    also read: {other}")
            if data.get("borrowed"):
                # Said to be borrowed. It is what this window held when it
                # was last read, which is usually what it held here and is
                # not the same claim.
                lines.append(f"    (no text of its own; this window read at "
                             f"{data.get('borrowed_at', '')}): "
                             f"{data['borrowed']}")
            elif data.get("borrowed_at"):
                lines.append(f"    (no text of its own; what this window "
                             f"showed is under its screen.text entry, read at "
                             f"{data['borrowed_at']})")
            if event.get("texts_left_out"):
                lines.append(f"    ({event['texts_left_out']} further readings "
                             f"of this, not shown)")
        return "\n".join(lines)

    def _states_text(self) -> str:
        """The states as lines, each saying where it came from.

        A state written from the last few minutes and one that has been on
        the calendar for two days are both true now and are not the same
        thing to act on: the first is a reading of what is in front of the
        user this minute, the second has already survived a few reviews
        without being closed. A step deciding what to do about them cannot
        tell the two apart from a title and a period.
        """
        lines = []
        for state in self.states:
            when = f"{_day(getattr(state, 'starts_at', None))} .. " \
                   f"{_day(getattr(state, 'ends_at', None)) or 'open'}"
            mark = ""
            if self.states_new:
                mark = (" (written from this window, just now)"
                        if getattr(state, "title", "") in self.states_new
                        else " (already running before this review)")
            # NUMBERED when not yet written, so a later step can name the
            # ones it rests on and the number can be turned into the state.
            said_id = (getattr(state, "id", "")
                       or f"state {len(lines) + 1}, not written yet")
            lines.append(f"[{said_id}] "
                         f"{when}{mark}  {getattr(state, 'title', '')}"
                         + (f"\n    {getattr(state, 'detail', '')}"
                            if getattr(state, "detail", "") else ""))
        return "\n".join(lines)

    def window(self) -> Dict[str, Any]:
        """The stretch of activity this floor was read from.

        Not the model's to report. It is a property of how the review was
        set up rather than a reading of anything, so asking for it would
        be asking the model to copy back a number it was handed - and a
        copied number is one that can come back wrong.

        Both the bounds asked for and the events that turned up in them. A
        quiet quarter of an hour holding two events and a busy one holding
        three hundred are not the same evidence, and the bounds alone
        cannot tell them apart.
        """
        first = sorted(str(event.get("first_at") or event.get("timestamp") or "")
                       for event in self.events if event.get("first_at")
                       or event.get("timestamp"))
        last = sorted(str(event.get("last_at") or event.get("timestamp") or "")
                      for event in self.events if event.get("last_at")
                      or event.get("timestamp"))
        return {"minutes": self.minutes,
                "from": self.since,
                "to": self.until,
                "events": sum(int(event.get("count") or 1)
                              for event in self.events),
                "groups": len(self.events),
                "first_event": first[0] if first else "",
                "last_event": last[-1] if last else ""}

    def restate_floor(self) -> None:
        """Rewrite the opening row after the states are known.

        The row is written when the floor is built. One step's states are
        not known then - they are what the step before it is about to
        answer - so the row said what was running instead, which is a
        different set and not what that step was given.
        """
        for row in self.trail:
            if row.get("did") != "floor":
                continue
            # And what the row says it was given. The step that reads the
            # window is handed no states, so the line is written without
            # them; a step that is handed some and does not say so is a
            # row that contradicts the context two lines below it.
            if self.states_are and self.shows_activity:
                row["decision"] = (f"{row.get('decision', '')}, and "
                                   f"{self.states_are.lower()}")
            row["states"] = len(self.states)
            row["states_started"] = [
                {"id": getattr(s, "id", ""), "title": getattr(s, "title", ""),
                 "detail": getattr(s, "detail", ""),
                 "starts_at": _moment(getattr(s, "starts_at", None)),
                 "ends_at": _moment(getattr(s, "ends_at", None)),
                 "about": getattr(s, "about", "user"),
                 "metadata": getattr(s, "metadata", {}) or {}}
                if not hasattr(s, "to_dict") else s.to_dict()
                for s in self.states[:STATES_IN_TRAIL]]
            row["states_not_shown"] = max(
                0, len(self.states) - STATES_IN_TRAIL)
            return

    # -- what it went and got ----------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {"query": " ".join(self.searched_for),
                "searched_for": list(self.searched_for),
                "nodes": [{"id": getattr(n, "id", ""),
                           "title": getattr(n, "title", ""),
                           "folder": getattr(n, "folder", "")}
                          for n in self.nodes],
                "trail": list(self.trail),
                "searches": self.searches,
                "duration_ms": self.duration_ms,
                "stopped_because": self.stopped_because}


class _AsState:
    """One of step one's answers, in the shape the floor renders.

    It has answered but nothing has been written yet - the calendar is
    updated after the whole loop - so what the next step reads is the
    answer itself, and this is the little of a state that rendering needs.
    """

    def __init__(self, entry: Dict[str, Any]) -> None:
        self.id = ""
        self.title = str(entry.get("title") or "")
        self.detail = str(entry.get("detail") or "")
        self.starts_at = entry.get("starts_at")
        self.ends_at = entry.get("ends_at")


class _Node:
    """The little of a record the step row and the panel need."""

    def __init__(self, record_id: str, title: str, folder: str,
                 created_at: str, summary: str = "") -> None:
        self.id = record_id
        self.title = title
        self.folder = folder
        self.created_at = created_at
        self.summary = summary


def _as_node(hit: Any) -> _Node:
    return _Node(record_id=getattr(hit, "record_id", "") or getattr(hit, "id", ""),
                 title=getattr(hit, "title", ""),
                 folder=getattr(hit, "folder", ""),
                 created_at=getattr(hit, "created_at", ""),
                 summary=getattr(hit, "summary", ""))


def _here(value: Any) -> Optional[datetime]:
    """A moment, in the user's own timezone - this machine's.

    Every time the heartbeat showed was UTC cut to its clock, so the model
    read "01:30" for half past eight in the evening and wrote states and
    reminders against a day that was not the user's. Stored times stay UTC;
    shown times are the user's.
    """
    if not value:
        return None
    when = parse_iso(value) if not isinstance(value, datetime) else value
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone()


def _as_utc(value: Any) -> Optional[datetime]:
    """A stored moment, read back - with a timezone - or None."""
    said = parse_iso(str(value or ""))
    if said is not None and said.tzinfo is None:
        said = said.replace(tzinfo=timezone.utc)
    return said


def _clock(value: Any) -> str:
    """The time of day, in the user's timezone - with the day in front when
    it is not today, so a window that crosses midnight is not ambiguous."""
    here = _here(value)
    if here is None:
        return str(value or "")[11:19]
    if here.date() == datetime.now().astimezone().date():
        return here.strftime("%H:%M:%S")
    return here.strftime("%a %d %b %H:%M:%S")


def _day(value: Any) -> str:
    """A date and time, in the user's timezone."""
    here = _here(value)
    return here.strftime("%a %d %b %Y %H:%M") if here is not None else ""


def _zone_said(when: Optional[datetime] = None) -> str:
    """The user's timezone by name and by its offset from UTC."""
    here = (when or datetime.now()).astimezone()
    shift = here.utcoffset() or timedelta(0)
    total = int(shift.total_seconds())
    sign = "-" if total < 0 else "+"
    hours, rest = divmod(abs(total), 3600)
    return f"{here.tzname() or 'local time'}, UTC{sign}{hours:02d}:{rest // 60:02d}"


def _now_said() -> str:
    """The line every prompt opens with: now, and the zone every time is in."""
    here = datetime.now().astimezone()
    return (f"NOW: {here.strftime('%A %d %B %Y, %H:%M:%S')} "
            f"({_zone_said(here)}). Every time in this prompt is in this "
            f"timezone, and every time you write must carry its offset - "
            f"for example {here.replace(microsecond=0).isoformat()}.")


def _when_said(entry: Dict[str, Any],
               again: Any = None) -> Tuple[Optional[datetime], Dict[str, Any], str]:
    """When a notification step 3 proposes is for: the moment, what it was
    taken from, or why it could not be read - read as the chat's are, by
    one reader with one rule for a zone (scheduler/core/given.py)."""
    from ...scheduler.core.given import time_given
    return time_given(entry, again)



def _how_many(produced: Any, kind: Any) -> int:
    """How many of one kind of thing a step ended up doing.

    ``kind`` narrows a list that holds several: step three's actions are
    scheduling, notifying and noting in one list, and "3 actions" answers
    none of the three questions anybody has about it. It may be several
    words, because the same verdict is written down in more than one tense.
    """
    if produced is None:
        return 0
    if not isinstance(produced, list):
        return 1
    if kind is None:
        return len(produced)
    wanted = {kind} if isinstance(kind, str) else set(kind)
    return sum(1 for row in produced
               if isinstance(row, dict)
               and wanted & {str(row.get("type") or ""),
                             str(row.get("verdict") or "")})


def _writer(response: Any) -> Dict[str, str]:
    """Which model wrote an answer: its name and provider, and when."""
    return {"model": str(getattr(response, "model", "") or ""),
            "provider": str(getattr(response, "provider", "") or ""),
            "at": utcnow().isoformat()}


def _writer_said(writer: Optional[Dict[str, str]]) -> str:
    """The same, as a line for a file: "claude-opus-5-5 (anthropic)"."""
    writer = writer or {}
    model = str(writer.get("model") or "").strip()
    provider = str(writer.get("provider") or "").strip()
    if not model:
        return ""
    return f"{model} ({provider})" if provider else model


def _state_written(state: Any, minutes: int, sources: List[str],
                   evidence: List[str], reasoning: str,
                   looked_at: Optional[Dict[str, Any]] = None,
                   continued: bool = False) -> Dict[str, Any]:
    """A state the run wrote, as the report carries it.

    The thing itself, not a tally of it. It said `"sources": 2` and
    `"evidence": 3`, which is a number about a list nobody could then
    read: somebody looking at what a review concluded wants the file, the
    page and the argument, and a count of them is the one part of a state
    that cannot be checked.

    The stretch the review read is on it too, put there by the script that
    wrote the state (section 34.1, as amended).
    """
    card = {"id": getattr(state, "id", ""),
            "title": getattr(state, "title", ""),
            "detail": getattr(state, "detail", ""),
            "about": getattr(state, "about", "user"),
            "starts_at": _moment(getattr(state, "starts_at", None)),
            "ends_at": _moment(getattr(state, "ends_at", None)),
            "minutes": minutes,
            "sources": list(sources),
            "evidence": list(evidence),
            "reasoning": reasoning,
            "looked_at": looked_at or {}}
    if continued:
        card["continued"] = True
    return card


def _moment(value: Any) -> Optional[str]:
    """A time as text, whether it arrived as one or as a datetime."""
    if value is None or value == "":
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


#: How sure the agent is, one to seven.
#:
#: A range rather than a flag, because "I watched this hold for a month"
#: and "this looked like a pattern once" are both worth writing down and
#: are not the same claim - and the second one written as the first is
#: something the agent will act on and keep acting on.
CONFIDENCE_LOW = 1
CONFIDENCE_HIGH = 7


def _confidence(value: Any) -> int:
    """How sure, clamped to the scale. Unsaid is the middle."""
    try:
        return max(CONFIDENCE_LOW, min(CONFIDENCE_HIGH, int(round(float(value)))))
    except (TypeError, ValueError):
        return 4


def _nature_body(content: str, confidence: int, evidence: List[str],
                 reasoning: str) -> str:
    """The record as it is written to the folder.

    Everything on one page, because this is read back months later by
    something deciding whether to believe it - and a trait whose evidence
    lives somewhere else is a trait that gets believed on its title.
    """
    parts = [content.strip(), "", f"Confidence: {confidence} of 7."]
    if reasoning:
        parts += ["", "How this was reached:", reasoning]
    if evidence:
        parts += ["", "What it rests on:"]
        parts += [f"- {item}" for item in evidence]
    return "\n".join(parts).strip() + "\n"


def _minutes(value: Any) -> int:
    """How much of the window a state covers, as a whole number.

    Nothing turns on it being exact - it is the model's reading of its own
    evidence - but it has to be a number, because a state that took forty
    minutes of an hour and one that took two are different claims and the
    calendar shows them the same way.
    """
    try:
        return max(0, int(round(float(value))))
    except (TypeError, ValueError):
        return 0


def _minutes_of_span(starts: Any, ends: Any,
                     looked_at: Optional[Dict[str, Any]]) -> int:
    """The minutes a state's own start and end cover inside the window it
    was read from, for a state written without them. 0 when it says no
    stretch at all."""
    began, ended = parse_iso(str(starts or "")), parse_iso(str(ends or ""))
    if began is None or ended is None:
        return 0
    window = looked_at if isinstance(looked_at, dict) else {}
    low = parse_iso(str(window.get("from") or ""))
    high = parse_iso(str(window.get("to") or ""))
    if low is not None:
        began = max(began, low)
    if high is not None:
        ended = min(ended, high)
    seconds = (ended - began).total_seconds()
    return max(1, int(round(seconds / 60))) if seconds > 0 else 0


def _messages_text(messages: List[Dict[str, Any]]) -> str:
    """A stretch of the conversation, as text a person can read.

    A message's content is a string on the way out and a list of blocks on
    the way back - tool results, and the text that travels with them - so
    printing it raw gives a reader JSON where the interesting part is one
    field inside it.
    """
    out = []
    for message in messages:
        role = str(message.get("role", ""))
        content = message.get("content")
        out.append(f"--- {role} ---")
        if isinstance(content, str):
            out.append(content)
            continue
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                out.append(str(block))
                continue
            kind = str(block.get("type", ""))
            if kind == "tool_result":
                out.append(f"[result of {block.get('tool_use_id', '')}]")
                out.append(str(block.get("content", "")))
            elif kind == "text":
                out.append(str(block.get("text", "")))
            else:
                out.append(json.dumps(block, ensure_ascii=False,
                                      default=str))
    return "\n".join(out)


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        candidate = text[start : end + 1] if start != -1 and end > start else None
    if not candidate:
        return None
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except ValueError:
        return None


#: Each step's module (common/modules.py): one turned off, its step is not run.
STEP_MODULES = {"step_people": "connections", "step_notify": "calendar"}


def _step_on(config: Any, step: str) -> bool:
    from ...common.modules import enabled
    return enabled(config, STEP_MODULES.get(step, "core"))


class AgentHeartbeat:
    def __init__(
        self,
        *,
        config: Any,
        checklist: Any,
        models: Any,
        scheduler: Any,
        notifications: Any,
        recorder: Any,
        raw_store: Any = None,
        bus: Any = None,
        observability: Any = None,
        tracer: Any = None,
        states: Any = None,
        library: Any = None,
    ) -> None:
        self.config = config
        self.tracer = tracer or NullTracer()
        self.checklist = checklist
        #: Section 34.1: the calendar this writes to and judges.
        self.states = states
        #: Where what kind of person the user is gets written.
        self.library = library
        self.models = models
        self.scheduler = scheduler
        self.notifications = notifications
        self.recorder = recorder
        self.raw_store = raw_store
        self.bus = bus
        self.observability = observability
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        #: Set when a setting this loop's timing depends on is edited.
        self._interval_changed = threading.Event()
        #: The run in progress, or nothing. The same dictionary the run is
        #: filling in, so watching it costs nothing and shows whatever has
        #: been decided so far (section 15.1: a wait with nothing shown is
        #: indistinguishable from a hang).
        self._progress: Optional[Dict[str, Any]] = None
        self._running = threading.Lock()
        self._last_report: Dict[str, Any] = {}
        #: When the loop last woke and when it will wake again, so the agent can
        #: say what it is about to do (section 7.5).
        #: The unfolded feed of the last floor built, kept only long enough
        #: for the trail to report what folding did to it.
        self._last_raw: List[Dict[str, Any]] = []
        #: The start of the stretch the last floor was read from. Set when
        #: the activity is read, so a run with no event store still has a
        #: bound to report rather than an attribute error.
        self._since = ""
        self._last_run_at: Optional[float] = None
        self._next_run_at: Optional[float] = None
        #: When this process's heartbeat started: the line a review never
        #: reaches back across when the agent had been closed a long while.
        self._began_at: Optional[datetime] = None
        #: The stretch a late look reads, fixed - or None for an ordinary
        #: review, which reads up to now.
        self._window_fixed: Optional[tuple] = None
        #: Whether the last review moved the summaries on (`_due_at`).
        self._caught_some = False

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._began_at = utcnow()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="agent-heartbeat", daemon=True)
        self._thread.start()
        log.info("agent heartbeat started")

    def stop(self) -> None:
        self._stop.set()
        # Woken, not waited out. The loop sleeps on `_interval_changed`
        # with the whole interval as its timeout, and `_stop` is a
        # different event - so setting it alone left the thread asleep for
        # up to an hour and the join below spent its full five seconds
        # failing. Setting both means the sleep returns at once, sees the
        # stop and ends: shutdown in milliseconds rather than in the join
        # timeout, three times over.
        self._interval_changed.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("agent heartbeat stopped")

    #: Settings that change when this loop should next run.
    WAKES_ON = ("heartbeat_interval_seconds", "heartbeat_enabled")

    def _interval(self) -> int:
        return max(60, int(self.config.get("heartbeat_interval_seconds", 900)))

    # -- how long to wait, and noticing that it changed --------------------
    def _watch_the_interval(self) -> None:
        """Wake this loop when its interval setting is edited.

        Both loops read the interval when they decide how long to sleep, so a
        change was picked up "on the next wake" - and the next wake is exactly
        what the change was about. Setting the agent's hour down to five
        minutes did nothing for up to an hour, which reads as the setting not
        working, and the panel went on reporting the next run an hour out
        because that is when it had been scheduled.

        So the sleep is interruptible. A settings change does not trigger a
        run: it makes the loop work out again when the run is due, counting
        from the same moment it was already counting from. If the new
        interval has already elapsed, that is now.
        """
        if self.bus is None:
            return
        def changed(_topic: Any, payload: Any) -> None:
            key = (payload or {}).get("key") if isinstance(payload, dict) else None
            if key in self.WAKES_ON:
                self._interval_changed.set()
        try:
            self.bus.subscribe("settings.changed", changed)
        except Exception:                # pragma: no cover - a loop without a bus
            log.debug("could not watch the interval setting", exc_info=True)

    def _sleep_until_due(self, seconds_from_now) -> bool:
        """Wait until the next run is due. False if we were asked to stop.

        ``seconds_from_now`` is read again whenever the setting changes, and
        the deadline is recomputed from when this wait began - so shortening
        the interval brings the next run forward instead of applying only
        after the wait that was already running.
        """
        began = time.time()
        while not self._stop.is_set():
            self._next_run_at = began + max(0.0, float(seconds_from_now()))
            remaining = self._next_run_at - time.time()
            if remaining <= 0:
                return True
            self._interval_changed.clear()
            self._interval_changed.wait(remaining)
            if self._stop.is_set():
                return False
            if not self._interval_changed.is_set():
                return True              # the wait simply ran out
        return False

    def _loop(self) -> None:
        self._watch_the_interval()
        self._loop_began = time.time()
        # Only when there is a late look to take does the loop wait for it.
        if (self._late_stretch() is not None
                and not self._stop.wait(CATCH_UP_AFTER_SECONDS)):
            self._catch_up()
        while not self._stop.is_set():
            entered = time.time()
            if not self._sleep_until_due(lambda: self._due_at() - entered):
                break
            # A run by hand while this slept moves the next one on.
            if self._due_at() > time.time() + 5:
                continue
            if not bool(self.config.get("heartbeat_enabled", True)):
                # Off: nothing is due until it is turned on again.
                self._interval_changed.clear()
                self._interval_changed.wait(60)
                continue
            self._last_run_at = time.time()
            try:
                self._run_marked()
            except Exception:
                log.exception("heartbeat iteration failed")
                if self.observability is not None:
                    self.observability.record_error("heartbeat", "iteration failed")

    # -- execution ----------------------------------------------------------
    @staticmethod
    def _charge(step_row: Dict[str, Any], response: Any) -> None:
        """What one call cost, added to the step that made it.

        Per step, because that is the unit somebody reading this decides
        about: the whole run's total says a heartbeat is expensive and not
        which part of it is. The state step makes several calls and each
        carries the whole conversation again, so its cost is not a few
        times a message but the sum of three growing ones.
        """
        usage = getattr(response, "usage", None)
        step_row["calls"] = int(step_row.get("calls") or 0) + 1
        step_row["tokens_in"] = (int(step_row.get("tokens_in") or 0)
                                 + int(getattr(usage, "input_tokens", 0) or 0))
        step_row["tokens_out"] = (int(step_row.get("tokens_out") or 0)
                                  + int(getattr(usage, "output_tokens", 0) or 0))
        step_row["tokens_total"] = (step_row["tokens_in"]
                                    + step_row["tokens_out"])

    def _add_keywords(self, events: List[Dict[str, Any]]) -> None:
        """Put each page's and file's keywords beside its title.

        A title says which page and not what was on it - "Inbox", "New
        chat", "Untitled - Notepad" - and the review read titles. The same
        keywords the Overview shows, worked out the same way, with no
        model: the words frequent on this page and rare on the others read
        that day. A page nothing was read on has none, and gets none.

        A WINDOW'S KEYWORDS ARE THOSE OF THE MOMENT. Filed by title alone,
        a terminal or a chat whose title never changes had one set for the
        whole day, and a window switch at 21:56 carried whatever the window
        said at ten at night. Each event now gets what its window showed
        around its own time - see `Moments`.
        """
        if self.raw_store is None or not events:
            return
        about = self._what_pages_were_about()
        files = [(f"file:{key}", text) for key, text in (
            ((e.get("data") or {}).get("path"),
             str((e.get("data") or {}).get("content") or ""))
            for e in events) if key and text]
        by_file: Dict[str, Dict[str, Any]] = {}
        if files:
            # A file's text is the part of it kept when it was touched,
            # so it is already of its moment; weighed against the files
            # and the day's pages, as before.
            corpus = pairs_of(files)
            for key, _when, text in getattr(self, "_pages_read", []):
                corpus.add(key, text)
            by_file = {key: found for key, found in corpus.about().items()
                       if key.startswith("file:")}
        first_seen: Dict[str, Any] = {}
        for key, when, _text in getattr(self, "_pages_read", []):
            if key and (key not in first_seen or when < first_seen[key]):
                first_seen[key] = when
        for event in events:
            key = _keyword_key(event)
            # A TITLE THAT HAS NOT CHANGED FOR HOURS IS A NAME. A terminal
            # is called after the session, a chat after whoever it is with,
            # and what is in them moves on underneath; said beside the
            # title, so the step does not read the name as the activity.
            since = first_seen.get(key)
            if since is not None and not key.startswith("file:"):
                try:
                    held = (parse_iso(event.get("timestamp"))
                            - since).total_seconds()
                except Exception:
                    held = 0
                if held >= TITLE_IS_A_NAME_AFTER:
                    event.setdefault("data", {})["title_since"] = since.isoformat()
            found = (by_file.get(key) if key.startswith("file:")
                     else about.near(key, event.get("timestamp")))
            if found and found.get("keywords"):
                event.setdefault("data", {})["keywords"] = list(found["keywords"])

    def _what_pages_were_about(self) -> Moments:
        """The keywords of every page read today, kept until a new read.

        What was read does not change after it was read, so the weighing is
        done again only when something new has been read.
        """
        since = utcnow() - timedelta(hours=KEYWORDS_WEIGHED_HOURS)
        try:
            reads = self.raw_store.recent_events(limit=3000,
                                                 event_type="screen.text",
                                                 since=since)
        except Exception:                # the review goes on without them
            log.debug("no pages to weigh keywords against", exc_info=True)
            return Moments([])
        mark = (len(reads), max((str(e.timestamp) for e in reads), default=""))
        held = getattr(self, "_about_kept", None)
        if held and held[0] == mark:
            return held[1]
        self._pages_read = [
            (page_key(str((e.data or {}).get("window_title") or "")),
             e.timestamp, str((e.data or {}).get("text") or "")) for e in reads]
        about = Moments(self._pages_read)
        self._about_kept = (mark, about)
        return about

    def run_once(self, trigger: str = "timer") -> Dict[str, Any]:
        # Section 37 - only the timer or an explicit debug trigger may start this.
        if trigger not in ("timer", "manual_debug"):
            raise PermissionError(
                "the heartbeat may not be invoked from a conversation; "
                "direct interaction uses the normal agent workflow"
            )
        if not self._running.acquire(blocking=False):
            return {"skipped": "already running"}
        # BEHIND: the next turn of what was never read, as a late look - not
        # the last few minutes. Nothing is skipped: the reviews come back
        # to now only when everything before has been read.
        stretch = None
        if self._window_fixed is None:
            try:
                stretch = self._late_stretch()
            except Exception:    # pragma: no cover - a read, not the work
                log.warning("could not tell how far behind the reviews are",
                            exc_info=True)
            if stretch is not None and not self._anything_in(*stretch):
                self.config.set(SUMMARISED_UNTIL, stretch[1].isoformat(), internal=True)
                self._running.release()
                return {"trigger": trigger, "read_to": stretch[1].isoformat(),
                        "skipped": "nothing was recorded in the stretch behind"}
            if stretch is not None:
                log.info("reading what was never summarised: %s to %s",
                         stretch[0].isoformat(), stretch[1].isoformat())
                self._window_fixed = stretch
        try:
            return self._review(trigger)
        finally:
            if stretch is not None:
                self._window_fixed = None

    def _review(self, trigger: str) -> Dict[str, Any]:
        """One review, its run lock held (released here)."""
        task_id = new_id("heartbeat")
        trace_id = new_trace_id()
        report: Dict[str, Any] = {
            "task_id": task_id,
            "trace_id": trace_id,
            "trigger": trigger,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            heartbeat_span = self.tracer.span(
                "heartbeat.execution", trace_id=trace_id, task_id=task_id,
                event_type="heartbeat.execution",
                metadata={"kind": "heartbeat", "trigger": trigger},
            )
            heartbeat_span.__enter__()
            self._span = heartbeat_span
            self._progress = report
            snapshot = [item for item in self.checklist.snapshot()  # section 35
                        if item.id not in PAUSED_STEPS
                        # A step of a module turned off (Settings > Modules)
                        # is not run - its tokens not spent.
                        and _step_on(self.config, item.id)
                        # A late look writes states and nothing else.
                        and (self._window_fixed is None or item.id == "step_state")]
            report["checklist"] = [item.text for item in snapshot]
            report["paused_steps"] = list(PAUSED_STEPS)
            if not snapshot:
                report["note"] = "checklist is empty"
                return self._finish(report)

            # Section 44.1, as amended: each step gathers its own context.
            #
            # The steps ask different questions - what is happening,
            # who this person is, what to do about it, what to close - and
            # each path begins near what its own question mentions. One
            # gathering for all of them begins near an average of them,
            # which is near none of them. The walks are cheap, and each one
            # now feeds the call for its own step.
            gathered = []
            # THE WINDOW READ AND FOLDED ONCE, for every step: the same
            # stretch, the same events. Read again for each step it was the
            # same work three times, and - ending at "now" - each a moment
            # later than the last, so the steps could see different events.
            window = None
            for item in snapshot:
                # EVERY STEP HAS ITS OWN COPY OF THE WINDOW, and nothing
                # beyond it: the last few minutes and the states running
                # now, with anything in it read whole by id. Nothing is
                # searched for - there is no index of the whole library to
                # search.
                if window is None:
                    window = self._state_floor(task_id)
                found = copy.deepcopy(window)
                if item.id in GATHERS_FOR_ITSELF:
                    if item.id in ("step_people", "step_nature", "step_notify"):
                        # Its states are not the ones already running: they
                        # are what this review is about to conclude, and
                        # they do not exist yet. Filled in below, once step
                        # one has answered.
                        found.states = []
                        found.states_are = ("STATES ALREADY WRITTEN FROM "
                                            "THIS ACTIVITY")
                        found.states_about = (
                            "A reading of the same activity: what the user "
                            "is doing. These are conclusions drawn from it, "
                            "not observations of it, and they can be wrong. "
                            "You are looking at that activity for something "
                            "longer-lived - not what they are doing, but "
                            "what doing it repeatedly says about them.")
                self.recorder.context_retrieved(task_id, found.to_dict())
                gathered.append((item, found))
            report["retrieval"] = [found.to_dict() for _item, found in gathered]
            # Section 37, as amended: the run, step by step. Set here rather
            # than at the end, so that a run the model refused still shows
            # what each step had gathered - which is most of what anybody
            # opening this panel wants to see.
            report["steps"] = self._steps_given(gathered)
            # Everything above this line is the gathering, and it is done
            # before any model is asked - so the whole of what each step
            # found is watchable within a second or two of the button, while
            # the calls that take the minute are still to come.
            for row in report["steps"]:
                row["phase"] = "gathered"
            # Section 37, as amended: one call per step.
            #
            # They used to be sections of one prompt answered in one call,
            # which is one act of attention divided three ways - and which
            # could not let a later step see what an earlier one had just
            # written. Asked separately, each gets its own question and its
            # own context, and each later step is given what the earlier
            # ones worked out. The preamble is repeated once per step and
            # the input roughly doubles; that is the price of the rest.
            payload: Dict[str, Any] = {}
            # WHICH MODEL WROTE IT, per step: everything a step writes says
            # so, because a summary is a claim and who made it is part of
            # how much to trust it.
            writers: Dict[str, Dict[str, str]] = {}
            concluded: List[str] = []
            for index, (item, found) in enumerate(gathered):
                shape = STEP_PROMPTS.get(item.id)
                if shape is None:
                    continue
                step_row = report["steps"][index]
                # From here to the end of the step, not the gathering alone.
                # `duration_ms` is how long it took to assemble a context,
                # which is milliseconds; the minute a reader is waiting
                # through is the calls, and that was reported nowhere.
                step_began = time.perf_counter()
                try:
                    if item.id in GATHERS_FOR_ITSELF:
                        if item.id == "step_notify":
                            # Every active one from now on, the agent's own
                            # checks among them, each with its id and why -
                            # to add beside, and to disable from.
                            found.scheduled = self._notifications_ahead(
                                with_checks=True)
                            # And what is waiting for a time (pending.py).
                            waiting = getattr(self, "pending", None)
                            if waiting is not None:
                                found.pending = waiting.lines()
                            # All of it, every time: it is kept short enough
                            # to give whole (calendar_nature.py).
                            keeper = getattr(self, "calendar_nature", None)
                            if keeper is not None:
                                try:
                                    found.calendar_nature = keeper.text()
                                except Exception:
                                    log.warning("the calendar nature could "
                                                "not be read", exc_info=True)
                        if item.id == "step_people":
                            # The people already known - those named in
                            # this window first - and the user's own
                            # changes to their contacts, dated.
                            keeper = getattr(self, "connections", None)
                            if keeper is not None:
                                try:
                                    found.people = _people.context(
                                        keeper, json.dumps(found.events, ensure_ascii=False,
                                                           default=str))
                                except Exception:
                                    log.warning("the known people could not be read",
                                                exc_info=True)
                        if item.id in ("step_people", "step_nature", "step_notify"):
                            # What step one answered, not what was written
                            # from it: the writing happens after the whole
                            # loop, so at this point `states_written` is
                            # empty and reading it hands this step nothing
                            # while appearing to hand it everything.
                            found.states = [
                                _AsState(entry)
                                for entry in (payload.get("states") or [])
                                if isinstance(entry, dict)]
                            # And the row that says what it started with.
                            # It was written when the floor was built, when
                            # these were not known, so it carried the
                            # running states - which this step is not given
                            # and which contradicted its own context two
                            # lines further down the panel.
                            found.restate_floor()
                            step_row["context"] = found.context_text()
                            step_row["trail"] = list(found.trail)
                        # Asked over and over until it says it has enough,
                        # and what it asked for in between is its own doing.
                        response = self._ask_step_one(
                            item, shape, found, step_row, task_id, concluded)
                    else:
                        prompt = self._step_prompt(item, shape, found,
                                                   activity, concluded)
                        step_row["prompt"] = prompt
                        step_row["prompt_tokens"] = estimate_tokens(prompt)
                        step_row["phase"] = "asking"
                        self.recorder.heartbeat_action(
                            task_id, "asking the model",
                            f"step {index + 1}: {item.text}"[:200])
                        response = self.models.invoke(
                            ModelRequest(
                                messages=[{"role": "user", "content": prompt}],
                                # Everything this pass composes - the notes it
                                # raises, the titles it puts on the calendar -
                                # is read by the user, so it carries the same
                                # instruction a reply does.
                                system=PREAMBLE,
                                language=shape.get("language") or in_display(),
                                max_tokens=int(self.config.get(
                                    "max_output_tokens", 4096)),
                                # Which step: each a module's, counted apart.
                                task=f"heartbeat:{item.id}",
                                metadata={"context_summary":
                                          f"{item.text[:60]}: "
                                          f"{len(found.nodes)} memories"[:2000]},
                            ),
                            role="heartbeat",
                        )
                except ModelUnavailable as exc:
                    report["blocked"] = True
                    report["reason"] = str(exc)
                    step_row["answer"] = ""
                    step_row["blocked"] = str(exc)
                    step_row["phase"] = "blocked"
                    # Timed too. A step that waited thirty seconds for a
                    # refusal took thirty seconds, and a blank there reads
                    # as a step that cost nothing.
                    step_row["elapsed_ms"] = int(
                        (time.perf_counter() - step_began) * 1000)
                    if (exc.availability.requires_user_action()
                            and exc.availability.state not in SIGN_IN_STATES):
                        self.notifications.notify(
                            "Heartbeat paused",
                            f"The heartbeat model is unavailable: "
                            f"{exc.availability.reason}",
                            kind="warning",
                            dedupe_key="heartbeat.model_unavailable",
                            signature=exc.availability.state.value,
                            action_hint="Open Settings > Models.",
                        )
                    # A model that is away is away for the steps after this
                    # one too, and asking three more times is three more
                    # failures and the same answer.
                    break
                if item.id != "step_state":
                    # Step one charges itself, once per round, inside its
                    # own loop - it makes more than one call.
                    self._charge(step_row, response)
                step_row["elapsed_ms"] = int(
                    (time.perf_counter() - step_began) * 1000)
                step_row["answer"] = response.text
                step_row["answer_tokens"] = estimate_tokens(response.text or "")
                writers[item.id] = _writer(response)
                step_row["written_by"] = writers[item.id]
                answered = _extract_json(response.text) or {}
                step_row["parsed"] = sorted(answered)
                step_row["phase"] = "answered"
                payload.update(answered)
                summary = self._what_it_concluded(item.id, answered)
                if summary:
                    # No number. A call does one job; which number that job
                    # has in a list it cannot see is not something it can
                    # use, and naming it invites answering the list.
                    concluded.append(summary)
            report["state_analysis"] = payload.get("state_analysis")

            # Step 1: what is going on, onto the calendar - each state
            # stamped with the stretch it was read from. Taken from the
            # floor rather than recomputed here, so the range on the state
            # is the range the model actually saw.
            looked_at = next((found.window() for step, found in gathered
                              if step.id == "step_state"
                              and hasattr(found, "window")), None)
            written = self._write_states(task_id, payload.get("states"),
                                         looked_at,
                                         written_by=writers.get("step_state"))
            if written:
                report["states_written"] = written
            # HOW FAR THE SUMMARIES GOT - only when step one answered. A
            # review the model never answered moves nothing on, so the next
            # one reads its stretch as well.
            read_to = next((found.until for step, found in gathered
                            if step.id == "step_state"), "")
            if "step_state" in writers and read_to:
                self.config.set(SUMMARISED_UNTIL, read_to, internal=True)

            # Step 2: who the user is, into user_nature/ - paused, so it
            # writes nothing even if an answer carries some.
            if "step_nature" not in PAUSED_STEPS:
                nature = self._write_nature(task_id, payload.get("user_nature"),
                                            written_by=writers.get("step_nature"),
                                            states_written=written)
                if nature:
                    report["nature_recorded"] = nature

            # Step 2: the people, into the user's contacts.
            keeper = getattr(self, "connections", None)
            if keeper is not None and "people" in payload:
                # WHAT THE MODEL WAS ASKED AND ANSWERED, kept with the changes
                # it makes (the Connection debugger) - when it makes any.
                asked = next((row for row in report.get("steps") or []
                              if row.get("id") == "step_people"), {})
                call = ""
                if payload.get("people"):
                    try:
                        call = keeper.begin_call(
                            "agent heartbeat: the people step",
                            str(asked.get("prompt") or ""), str(asked.get("answer") or ""),
                            window=f"the activity up to {read_to}" if read_to else "")
                    except Exception:      # pragma: no cover - a debugger's record
                        log.warning("could not keep the people step's call", exc_info=True)
                try:
                    # Found when the window they were seen in ends.
                    written_people = _people.apply(keeper, payload.get("people"),
                                                   found_at=read_to or "")
                except Exception:          # pragma: no cover - the step's own
                    log.warning("could not write the people", exc_info=True)
                    written_people = []
                finally:
                    if call:
                        try:
                            keeper.end_call(call, written_people)
                        except Exception:  # pragma: no cover - a debugger's record
                            log.warning("could not finish the people step's call",
                                        exc_info=True)
                if written_people:
                    report["people_written"] = written_people

            # Step 3: the notifications, onto the calendar.
            self._notify_writer = writers.get("step_notify") or {}
            added = self._add_notifications(task_id,
                                            payload.get("notifications"),
                                            report)
            if added:
                report["notifications_scheduled"] = added

            # What is waiting for a time: written, resolved, dropped.
            waiting = getattr(self, "pending", None)
            if waiting is not None:
                try:
                    report.update(waiting.apply(payload))
                except Exception:          # pragma: no cover - the step stands
                    log.warning("what is waiting for a time was not kept", exc_info=True)

            # And what step 3 found already done, disabled.
            closed = self._close_notifications(task_id, payload.get("closed"))
            if closed:
                report["notifications_closed"] = closed

            # And what step 3 found new details for, filled in.
            updated = self._update_notifications(task_id, payload.get("updated"),
                                                 report)
            if updated:
                report["notifications_updated"] = updated

            # LAST: the pictures taken in the stretch step 1 read that no
            # state linked. Nothing can refer to them now - a stage takes
            # its references from states, and this stretch's are written.
            # Only when step 1 answered: a run that failed read nothing.
            if looked_at and "states" in payload:
                materials = self._materials()
                if materials is not None:
                    try:
                        report["pictures_dropped"] = \
                            materials.drop_unlinked_pictures(looked_at)
                    except Exception:    # pragma: no cover - logged
                        log.warning("could not clear unlinked pictures",
                                    exc_info=True)

            self._steps_produced(report)
            # WHAT WAS READ, put in the reading log - in a thread of its own,
            # never holding this round (heartbeat/reading).
            reading = getattr(self, "reading", None)
            from ...common.modules import enabled as _module_on
            if reading is not None and self._window_fixed is None \
                    and not self._stop.is_set() and _module_on(self.config, "recommend"):
                try:
                    report["reading"] = {"started": reading.start(
                        going=lambda: not self._stop.is_set())}
                except Exception:        # pragma: no cover - never this round's
                    log.warning("the reading log could not start", exc_info=True)
            # AND THEN, IF IT IS DUE, THE STATE ROUND. Asked here rather
            # than kept on a timer of its own, because the two are not
            # the same kind of work: this round is a few calls on a fixed
            # schedule, and a state round is one call per task - minutes
            # on a busy folder. An hourly timer either runs it when there
            # is nothing to find or collides with the round that writes
            # the states it would read. Here it runs when there is new
            # work to group and not before, and the waiting is counted
            # from the last round that FINISHED, so an interrupted one is
            # resumed instead of starting again six hours later.
            #
            # Its own failure is not this round's failure: the states are
            # written by now and the grouping of them can wait.
            report["state_round"] = self._state_round_if_due()
            return self._finish(report)
        finally:
            span = getattr(self, "_span", None)
            if span is not None:
                self._span = None
                try:
                    span.__exit__(None, None, None)
                except Exception:
                    log.debug("heartbeat span close failed", exc_info=True)
            self._running.release()

    def _state_round_if_due(self) -> Dict[str, Any]:
        """Ask the state heartbeat whether it is due, and let it run.

        The decision comes back either way and goes in this round's
        report, because "the grouping did not run" and "it ran and found
        nothing" are different and a reader cannot tell them apart from
        an absence.
        """
        # NOT WHILE THE APPLICATION IS CLOSING. A state round is minutes
        # of work on a busy folder and it runs on THIS thread, so starting
        # one here as the user quits would hold the join on the way out
        # for as long as it took - which is the thing that made quitting
        # feel stuck in the first place, from a different cause.
        if self._stop.is_set():
            return {"run": False, "ran": False,
                    "why": "The application is closing."}
        beat = getattr(self, "state_heartbeat", None)
        if beat is None:
            beat = getattr(getattr(self, "app", None), "state_heartbeat", None)
        if beat is None or not hasattr(beat, "run_if_due"):
            return {"run": False, "why": "There is no state heartbeat here."}
        try:
            return beat.run_if_due(trigger="agent_heartbeat")
        except Exception as exc:     # pragma: no cover - logged, not raised
            log.exception("state heartbeat could not be asked")
            return {"run": False, "why": f"It could not be asked: {exc}"}

    # -- step 1: the state calendar (section 34.1, as amended) --------------
    def _write_states(self, task_id: str, requested: Any,
                      looked_at: Optional[Dict[str, Any]] = None,
                      written_by: Optional[Dict[str, str]] = None,
                      ) -> List[Dict[str, Any]]:
        """Put what is going on onto the calendar.

        A state is shown and used as context; it is never executed. That is
        the whole difference from a scheduled task, and it is why writing one
        cannot cause anything to happen.

        `looked_at` is the stretch this run read, from the floor step 1 was
        given. It goes on every state written here and is not asked of the
        model: it is a fact about the run, and a state that says it covers
        two minutes reads very differently once you know whether the review
        saw fifteen minutes or four hours.
        """
        if self.states is None or not isinstance(requested, list):
            return []
        from ...agent.states.store import (ABOUT_AGENT, ABOUT_USER, KINDS,
                                           SOURCE_AGENT, SOURCES, State,
                                           window_for)

        written = []
        # Numbered as they were asked for, so step 2 can say which of them
        # a trait rests on, and the number can be turned into the state.
        for asked_as, entry in enumerate(requested[:MAX_STATES_PER_RUN], start=1):
            # Section 52.1: everything shown on the calendar says what it is.
            # A state with a title and nothing else is a label the user cannot
            # act on, exactly as a task with a title and nothing else is.
            if not isinstance(entry, dict):
                continue
            if not str(entry.get("title") or "").strip():
                continue
            if not str(entry.get("detail") or "").strip():
                log.info("refusing a state with no detail: %s",
                         str(entry.get("title"))[:60])
                continue
            # The period is the agent's to state: only it knows what the
            # state is about, and a fixed window makes every long state
            # exactly a week and still running days from now, whatever it
            # describes.
            #
            # There is no horizon, here or anywhere. It was "short" or
            # "long" read off the span, which is the span again in a word,
            # and a word beside the dates is a second place for the two to
            # disagree. A state that gives no period at all falls back to
            # now, because that is the only thing it can honestly mean.
            starts, ends = window_for(starts_at=entry.get("starts_at"),
                                      ends_at=entry.get("ends_at"))
            source = str(entry.get("source") or SOURCE_AGENT)
            # Section 34.1, as amended: a state the agent inferred carries what
            # it inferred that from. The user is being told about their own
            # situation by something that can be wrong about it, and a claim
            # they cannot check is one they cannot argue with.
            evidence = [str(item)[:300] for item in (entry.get("evidence") or [])
                        if str(item).strip()][:8]
            reasoning = str(entry.get("reasoning") or "").strip()[:600]
            # What the state rests on and how much of the window went to
            # it. Kept apart from `evidence`, which is the argument for
            # believing the state; these are the things themselves - the
            # file, the page, the window - which is what somebody who
            # disagrees with the state needs in order to check it.
            sources = [str(item)[:400] for item in (entry.get("sources") or [])
                       if str(item).strip()][:20]
            minutes = _minutes(entry.get("minutes"))
            # REQUIRED, so never missing: an answer without it is given the
            # stretch it names, cut to the window - an upper bound on how
            # long it went on, and better than counting the whole window.
            if not minutes:
                minutes = _minutes_of_span(entry.get("starts_at"),
                                           entry.get("ends_at"), looked_at)
            # A state named as carrying on an existing one moves that
            # one's end rather than becoming a second entry, and the
            # sighting is recorded. Counted as a sighting rather than an
            # extension: an extension is a judgement that something is not
            # finished, and the count of those is how a state nobody ever
            # closes gets noticed.
            #
            # Step 1 is no longer shown the running states, so it has no
            # id to name and this is not reached from there. Kept because
            # it is what any caller that does know an id should get, and
            # because dropping it would silently turn such a call into a
            # duplicate on the calendar.
            carries_on = str(entry.get("continues") or "").strip()
            if carries_on and self.states.get(carries_on) is not None:
                moved = self.states.still_going(
                    carries_on, window_for(starts_at=entry.get("starts_at"),
                                           ends_at=entry.get("ends_at"))[1],
                    note=str(entry.get("title") or "")[:200])
                if moved is not None:
                    written.append(dict(_state_written(
                        moved, minutes=minutes, sources=sources,
                        evidence=evidence, reasoning=reasoning,
                        looked_at=looked_at, continued=True),
                        asked_as=asked_as))
                    self.recorder.heartbeat_action(
                        task_id, "a state is still going",
                        f"{moved.title}"[:200])
                    continue
            state = State(
                title=str(entry["title"])[:200],
                detail=str(entry.get("detail") or "")[:1000],
                metadata={key: value for key, value in (
                    ("evidence", evidence), ("reasoning", reasoning),
                    ("sources", sources), ("minutes", minutes),
                    # Which review this came out of, and how much it had
                    # in front of it. Script, not model.
                    ("looked_at", looked_at),
                    ("written_by", written_by),
                    # ROUTINE, said by the review: the part of a day that
                    # is no piece of work. It is kept out of the tasks.
                    # Work, leisure or routine, as the review said - kept
                    # as said, so a state from before it was asked (none)
                    # is told apart from one said to be work.
                    ("kind", str(entry.get("kind") or "").strip().lower()
                     if str(entry.get("kind") or "").strip().lower() in KINDS else ""),
                    # And which run: what else that run wrote - the nature
                    # records - is found from it.
                    ("heartbeat_run", task_id)) if value},
                source=source if source in SOURCES else SOURCE_AGENT,
                # The user's - except the agent's own work run, said so
                # by the step or shown by its events: every one it rests on
                # the agent's. Those are recorded and drawn on for nothing
                # else (no task, no stage, no nature).
                about=ABOUT_AGENT if self._the_agents(entry) else ABOUT_USER,
                starts_at=starts, ends_at=ends,
                # Section 52.7: the reasoning the model gave for this state is
                # its reason. It was written to metadata and not to the field
                # every reader looks at, so each state the heartbeat wrote
                # logged a fault and stored "no reason recorded" - which reads
                # afterwards as though the agent had no reason at all.
                reason=reasoning,
            )
            self.states.add(state)
            linked = self._link_materials(state, entry.get("materials"),
                                          looked_at)
            written.append(dict(_state_written(
                state, minutes=minutes, sources=sources, evidence=evidence,
                reasoning=reasoning, looked_at=looked_at), asked_as=asked_as,
                materials=len(linked)))
            self.recorder.heartbeat_action(task_id, "wrote a state",
                                           state.title)
        return written

    def _materials(self):
        from ..experience.materials import Materials
        root = getattr(self.library, "root", None)
        db = getattr(self.states, "db", None)
        if db is None:
            return None
        from pathlib import Path
        return Materials(db, (Path(root) / "references") if root else None)

    def _the_agents(self, entry: Dict[str, Any]) -> bool:
        """Whether a state is the agent's own work run: said so, or every
        event it rests on is marked as the agent's."""
        if str(entry.get("whose") or "").strip().lower() == "agent":
            return True
        ids = [str(one) for one in entry.get("materials") or []
               if str(one).startswith("event_")]
        if not ids or self.raw_store is None:
            return False
        try:
            events = self.raw_store.events_by_ids(ids)
        except Exception:          # pragma: no cover - a read
            return False
        return bool(events) and all(
            (getattr(one, "data", None) or {}).get("by") == "agent"
            for one in events)

    def _link_materials(self, state: Any, chosen: Any,
                        looked_at: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The state's materials, linked and their copies kept - now, while
        the raw activity and the files are still as they were."""
        materials = self._materials()
        if materials is None:
            return []
        try:
            return materials.link(state, chosen if isinstance(chosen, list)
                                  else [], looked_at or {})
        except Exception:        # pragma: no cover - the state stands
            log.warning("could not keep a state's materials", exc_info=True)
            return []

    # -- step 2: who the user is (section 34.2) -----------------------------
    #: The most notifications one run may add. A run that wants more is
    #: reading the window as a to-do list, and every one is an interruption.
    MOST_NOTIFICATIONS = 5

    # NO LIMIT ON HOW FAR AHEAD. "I'll follow up in January" is a promise
    # made in fifteen minutes of activity and kept months later, and a
    # reminder for it is the reason to have reminders at all.

    def _close_notifications(self, task_id: str, entries: Any
                             ) -> List[Dict[str, Any]]:
        """Step 4: the notifications already done, closed - not deleted.

        Only ones the step was shown: an id it made up, or one that has
        fired since, is refused rather than closing something it never
        looked at. A closed one keeps its place on the calendar with the
        reason, and the scheduler never runs anything that is not pending.
        """
        if not isinstance(entries, list) or self.scheduler is None:
            return []
        shown = set(getattr(self, "_open_notifications", set()) or ())
        store = getattr(self.scheduler, "store", None)
        out = []
        for entry in entries[:20]:
            if not isinstance(entry, dict):
                continue
            which = str(entry.get("id") or "").strip()
            why = str(entry.get("why") or "").strip()
            row = {"id": which, "why": why, "ok": False}
            task = store.get(which) if store is not None and which else None
            if which not in shown or task is None:
                row["error"] = "not one of the notifications it was shown"
            elif task.status != "pending":
                row["error"] = f"it is {task.status} now, not waiting"
            elif not why:
                row["error"] = "no reason given, and a closed one shows it"
            else:
                evidence = [str(one) for one in entry.get("evidence") or []
                            if str(one).strip()][:20]
                metadata = dict(task.metadata or {})
                metadata.update({
                    "closed_by": "agent heartbeat, step 3 - which "
                                 "notifications need to be added and "
                                 "which disabled",
                    "closed_reason": why[:1000],
                    "closed_at": utcnow().isoformat(),
                    "closed_evidence": evidence,
                    "closed_in": task_id})
                try:
                    self.scheduler.update_task(which, status="closed",
                                               lease_token=None,
                                               metadata=metadata)
                    row.update(ok=True, title=task.title,
                               at=task.scheduled_at.isoformat()
                               if task.scheduled_at else "")
                    self.recorder.heartbeat_action(
                        task_id, "closed a notification already done",
                        f"{task.title}: {why}"[:300])
                except Exception as exc:   # pragma: no cover - a write
                    row["error"] = f"could not close it: {exc}"[:300]
            out.append(row)
        return out

    #: The most details one notification keeps, and the most of each.
    MOST_DETAILS = 12
    MOST_DETAIL = 500

    def _update_notifications(self, task_id: str, entries: Any,
                              report: Optional[Dict[str, Any]] = None
                              ) -> List[Dict[str, Any]]:
        """Step 3, its third part: a notification filled in with what has
        come since it was made - the meeting's link, its new time - rather
        than another added about the same thing.

        As for closing: only one the step was shown and still waiting, and
        with a reason. What it said before and after is kept on it
        (metadata "updates"), the details by name ("details"), and the
        message the user sees carries them."""
        if not isinstance(entries, list) or self.scheduler is None:
            return []
        step_row = next((step for step in (report or {}).get("steps") or []
                         if step.get("id") == "step_notify"), None)
        shown = set(getattr(self, "_open_notifications", set()) or ())
        store = getattr(self.scheduler, "store", None)
        now = utcnow()
        out = []
        for entry in entries[:20]:
            if not isinstance(entry, dict):
                continue
            which = str(entry.get("id") or "").strip()
            why = str(entry.get("why") or "").strip()
            row: Dict[str, Any] = {"id": which, "why": why, "ok": False}
            task = store.get(which) if store is not None and which else None
            if which not in shown or task is None:
                row["error"] = "not one of the notifications it was shown"
                out.append(row)
                continue
            if task.status != "pending":
                row["error"] = f"it is {task.status} now, not waiting"
                out.append(row)
                continue
            if not why:
                row["error"] = "no reason given, and an update shows it"
                out.append(row)
                continue
            payload = dict(task.task_payload or {})
            metadata = dict(task.metadata or {})
            before = {"title": task.title, "message": str(task.description or ""),
                      "at": task.scheduled_at.isoformat() if task.scheduled_at else ""}
            fields: Dict[str, Any] = {}
            title = str(entry.get("title") or "").strip()[:200]
            if title and title != task.title:
                fields["title"] = title
            raw = entry.get("details")
            details = {str(k).strip()[:60]: str(v).strip()[:self.MOST_DETAIL]
                       for k, v in (raw.items() if isinstance(raw, dict) else [])
                       if str(k).strip() and str(v).strip()}
            known = dict(metadata.get("details") or {})
            new_details = {k: v for k, v in details.items() if known.get(k) != v}
            message = str(entry.get("message") or "").strip()[:1200]
            if not message and new_details:
                # Said in the message the user sees, not only kept by name.
                lines = [before["message"], *(f"{k}: {v}" for k, v in new_details.items())]
                message = chr(10).join(lines).strip()[:1200]
            if message and message != before["message"]:
                fields["description"] = message
                payload["message"] = message
            when, said_time = None, {}
            if entry.get("at_local") or entry.get("at"):
                when, said_time, refused = _when_said(
                    entry, self._zone_again(entry, step_row, report))
                if when is None:
                    row["error"] = refused
                    out.append(row)
                    continue
            if when is not None:
                when = max(when, now + timedelta(minutes=1))
                if task.scheduled_at is None or abs(when - task.scheduled_at) >= timedelta(minutes=1):
                    fields["scheduled_at"] = when
                    # What the new time was taken from, as for a new one.
                    metadata["time"] = said_time
                    fields["timezone_name"] = said_time["zone"]
            if not fields and not new_details:
                row["error"] = "nothing new in it"
                out.append(row)
                continue
            if new_details:
                known.update(new_details)
                metadata["details"] = dict(list(known.items())[:self.MOST_DETAILS])
            after = {"title": fields.get("title", before["title"]),
                     "message": fields.get("description", before["message"]),
                     "at": fields["scheduled_at"].isoformat()
                     if "scheduled_at" in fields else before["at"]}
            evidence = [str(one) for one in entry.get("evidence") or []
                        if str(one).strip()][:20]
            metadata["updates"] = [*list(metadata.get("updates") or [])[-19:], {
                "by": "agent heartbeat, step 3 - new details",
                "at": now.isoformat(), "in": task_id, "why": why[:1000],
                "evidence": evidence, "before": before, "after": after,
                "details": new_details}]
            try:
                self.scheduler.update_task(which, task_payload=payload,
                                           metadata=metadata, **fields)
                row.update(ok=True, title=after["title"], at=after["at"],
                           changed=sorted([*(key for key in fields if key != "timezone_name"),
                                           *(["details"] if new_details else [])]))
                self.recorder.heartbeat_action(
                    task_id, "filled in a notification with new details",
                    f"{after['title']}: {why}"[:300])
            except Exception as exc:   # pragma: no cover - a write
                row["error"] = f"could not update it: {exc}"[:300]
            out.append(row)
        return out

    def _notifications_ahead(self, with_checks: bool = False) -> List[str]:
        """The notifications on the calendar that have not popped up yet."""
        store = getattr(self.scheduler, "store", None)
        if store is None:
            return []
        try:
            waiting = store.list(status="pending", start=utcnow())
        except Exception:                 # the step goes on without them
            log.debug("could not list the scheduled notifications",
                      exc_info=True)
            return []
        out = []
        self._open_notifications = set()
        for task in waiting:
            checks = (task.execution_type == "model_task"
                      and (task.created_by == "heartbeat"
                           or (task.metadata or {}).get("created_by")
                           == "heartbeat"))
            if task.execution_type != "notification" and not (
                    with_checks and checks):
                continue
            kind = ("a check the agent will make" if checks else
                    "notice" if (task.task_payload or {}).get("template")
                    == PASSIVE else "reminder")
            line = (f"- [{task.id}] {_day(task.scheduled_at)}  [{kind}]  "
                    f"{task.title}: {str(task.description or '')[:400]}")
            details = (task.metadata or {}).get("details") or {}
            if details:
                line += "\n    details: " + "; ".join(
                    f"{k}: {v}" for k, v in list(details.items())[:12])[:600]
            if with_checks:
                why = str(task.reason or (task.metadata or {}).get(
                    "rationale") or "")
                if why:
                    line += f"\n    made because: {why[:300]}"
            out.append(line)
            self._open_notifications.add(task.id)
        return out[:40]

    #: How far either side of a new notification's time one already waiting
    #: is looked at as a possible repeat.
    SAME_TIME_WITHIN = timedelta(minutes=30)

    #: How alike two notifications' words must be to be checked: the share
    #: of the shorter one's terms that the other also has.
    ALIKE_AT = 0.5

    @staticmethod
    def _alike(one: str, other: str) -> float:
        """How much of the shorter text's terms the longer one shares.

        By the shorter, because a reminder reworded at length still says
        everything the short one did; Jaccard would score the pair low for
        the extra words alone. Chinese is compared by pairs of characters,
        as the keywords are.
        """
        from ...agent.overview.keywords import terms

        mine, theirs = set(terms(one)), set(terms(other))
        if not mine or not theirs:
            return 0.0
        return len(mine & theirs) / min(len(mine), len(theirs))

    def _waiting_notifications(self) -> List[Any]:
        store = getattr(self.scheduler, "store", None)
        if store is None:
            return []
        try:
            return [task for task in store.list(
                status="pending", start=utcnow() - self.SAME_TIME_WITHIN)
                if task.execution_type == "notification"]
        except Exception:
            log.debug("could not list the scheduled notifications",
                      exc_info=True)
            return []

    #: How much of what step 3 was shown goes with the zone asked again.
    ZONE_CONTEXT = 12000

    def _zone_again(self, entry: Dict[str, Any], step_row: Optional[Dict[str, Any]],
                    report: Optional[Dict[str, Any]] = None) -> Any:
        """How step 3 is asked ONCE MORE for a zone refused (given.py: one
        rule for a zone, the chat's and this step's): a question to the
        model, said why it was refused and what is accepted, with what step
        3 was shown, to name the place. What it names is held to the same
        rule by the reader; still refused, or no answer, and the time is the
        user's own - and the card says so. Each asking is kept on the step's
        row and in the report ("zone_checks")."""
        from ...scheduler.core import zones
        from ...scheduler.core.given import refusal

        def ask(said: str, reason: str) -> str:
            asked = "\n".join([
                _now_said(), "",
                f"THE ZONE YOU GAVE: \"{said}\"",
                f"WHY IT WAS REFUSED: {refusal(said, reason)}", "",
                "THE NOTIFICATION:",
                json.dumps({key: entry.get(key) for key in (
                    "title", "message", "at_local", "as_written", "why")
                    if entry.get(key)}, ensure_ascii=False, default=str), "",
                "WHAT IT CAME FROM:",
                str((step_row or {}).get("context") or "")[:self.ZONE_CONTEXT]])
            check: Dict[str, Any] = {"title": str(entry.get("title") or "")[:200],
                                     "zone": said, "prompt": asked}
            if step_row is not None:
                step_row.setdefault("zone_checks", []).append(check)
            if report is not None:
                report.setdefault("zone_checks", []).append(check)
            check["taken"] = "user"
            try:
                response = self.models.invoke(
                    ModelRequest(
                        messages=[{"role": "user", "content": asked}],
                        system=ZONE_AGAIN_PROMPT,
                        max_tokens=300,
                        task="heartbeat:zone_again",
                        language=in_english("the zone and why"),
                        metadata={"context_summary":
                                  f"the IANA zone for \"{said}\": "
                                  f"{check['title']}"[:2000]},
                    ),
                    role="heartbeat",
                )
            except Exception as exc:          # no answer: the user's own
                check["error"] = str(exc)[:300]
                return ""
            if step_row is not None:
                self._charge(step_row, response)
            check["answer"] = response.text or ""
            answered = _extract_json(response.text or "")
            zone = ""
            if isinstance(answered, dict):
                zone = str(answered.get("zone") or "").strip()
                check["why"] = str(answered.get("why") or "").strip()[:500]
            # Said here as the reader will hold it, so the record is right.
            try:
                if zone and not zones.is_own(zone):
                    check["taken"] = zones.place_zone(zone)
            except ValueError as exc:
                check["error"] = str(exc)[:300]
            log.info("a notification's zone %r was refused; taken as %s",
                     said, check["taken"])
            return zone

        return ask

    def _is_it_the_same(self, proposed: Dict[str, Any], when: datetime,
                        alike: List[Any], step_row: Optional[Dict[str, Any]]
                        ) -> Dict[str, Any]:
        """Ask whether a proposed notification repeats one already waiting.

        Returns the verdict: `create` and why. A check that cannot be made
        or read does not create - the doubt was that the user would be
        interrupted twice, and nothing has removed it.
        """
        def line(kind: str, title: str, message: str, at: datetime,
                 name: str = "") -> str:
            return (f"- {name + '  ' if name else ''}{_day(at)}"
                    f"  [{kind}]  {title}: {message[:300]}")

        def kind_of(task: Any) -> str:
            return ("notice" if (task.task_payload or {}).get("template")
                    == PASSIVE else "reminder")

        asked = "\n".join(
            [_now_said(), "",
             "NEW:",
             line(proposed["type"], proposed["title"], proposed["message"],
                  when),
             "",
             "ALREADY SCHEDULED NEARBY:"]
            + [line(kind_of(task), task.title, str(task.description or ""),
                    task.scheduled_at, task.id) for task in alike])
        check: Dict[str, Any] = {"title": proposed["title"], "prompt": asked,
                                 "alike": [task.id for task in alike]}
        try:
            response = self.models.invoke(
                ModelRequest(
                    messages=[{"role": "user", "content": asked}],
                    system=SAME_NOTIFICATION_PROMPT,
                    max_tokens=400,
                    task="heartbeat:same_notification",
                    language=in_english("the verdict and why"),
                    metadata={"context_summary":
                              f"is this notification already scheduled: "
                              f"{proposed['title']}"[:2000]},
                ),
                role="heartbeat",
            )
        except Exception as exc:
            check.update(create=False,
                         why=f"the check could not be made: {exc}"[:300])
            return check
        if step_row is not None:
            self._charge(step_row, response)
        check["answer"] = response.text or ""
        answered = _extract_json(response.text or "")
        if not isinstance(answered, dict):
            check.update(create=False,
                         why="the check's answer could not be read")
            return check
        same = str(answered.get("same_as") or "").strip()
        same = same if same.lower() not in ("null", "none") else ""
        anyway = bool(answered.get("create_anyway"))
        check.update(same_as=same, create_anyway=anyway,
                     create=(not same) or anyway,
                     why=str(answered.get("why") or "").strip()[:500])
        return check

    def _add_notifications(self, task_id: str, entries: Any,
                           report: Optional[Dict[str, Any]] = None
                           ) -> List[Dict[str, Any]]:
        """Each notification step 3 asked for, as a task on the calendar.

        A notification task, set to interrupt, carrying which of the two
        kinds it is: when its time comes the scheduler pops it up with that
        kind's answers. Refused, and said so, when it is missing what the
        user would read or has no time.

        AND CHECKED AGAINST WHAT IS ALREADY THERE. One whose words are close
        to a notification waiting within half an hour of it is shown to the
        model beside those, and is not created if the model says it repeats
        one - unless it insists, which it says why. Close in words and far
        in time is not a repeat: a reminder three days ahead and another two
        days ahead are both meant.
        """
        if not isinstance(entries, list) or self.scheduler is None:
            return []
        report = report if report is not None else {}
        step_row = next((step for step in report.get("steps") or []
                         if step.get("id") == "step_notify"), None)
        now = utcnow()
        waiting = self._waiting_notifications()
        added: List[Dict[str, Any]] = []
        for entry in entries[:self.MOST_NOTIFICATIONS]:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title") or "").strip()[:200]
            message = str(entry.get("message") or "").strip()[:600]
            kind = str(entry.get("type") or "reminder").strip().lower()
            template = PASSIVE if kind in ("notice", PASSIVE) else REMINDER
            row: Dict[str, Any] = {"title": title, "type": "notice"
                                   if template == PASSIVE else "reminder"}
            if not title or not message:
                added.append({**row, "ok": False,
                              "error": "a notification needs a title and a "
                                       "message"})
                continue
            when, said_time, refused = _when_said(
                entry, self._zone_again(entry, step_row, report))
            if when is None:
                added.append({**row, "ok": False, "error": refused})
                continue
            # A moment that has just passed is read as "now": the call takes
            # a while, and the point of it has not.
            when = max(when, now + timedelta(minutes=1))
            # IT MAY REPEAT, the user's decision - as a task the chat
            # schedules may: the same rule, read the same way (every and a
            # unit, or every_seconds; until or count to end it). One that
            # cannot be read is refused, saying why, not made to run once.
            repeat = entry.get("repeat") or entry.get("recurrence")
            recurrence: Dict[str, Any] = {}
            if repeat:
                try:
                    # On the clock of the zone its time is on.
                    recurrence = parse_recurrence(repeat, zone=said_time["zone"])
                except RecurrenceError as exc:
                    added.append({**row, "ok": False,
                                  "error": f"its repeat could not be read: {exc}"})
                    continue
                row["repeats"] = describe_recurrence(recurrence)
            words = f"{title}\n{message}"
            alike = [task for task in waiting
                     if abs(task.scheduled_at - when) <= self.SAME_TIME_WITHIN
                     and self._alike(words, f"{task.title}\n"
                                            f"{task.description or ''}")
                     >= self.ALIKE_AT]
            reason = str(entry.get("why") or "").strip()[:1000]
            # NO REASON, NO NOTIFICATION. What it was made for is the one
            # thing that shows, when the user deletes it or calls it a bad
            # one, what the agent misread (calendar_nature.py).
            if not reason:
                added.append({**row, "ok": False,
                              "error": "a notification needs a reason (\"why\")"})
                continue
            if alike:
                check = self._is_it_the_same(
                    {**row, "message": message}, when, alike, step_row)
                report.setdefault("notification_checks", []).append(check)
                if step_row is not None:
                    step_row.setdefault("checks", []).append(check)
                if not check["create"]:
                    repeated = next((task.title for task in alike
                                     if task.id == check.get("same_as")),
                                    alike[0].title)
                    added.append({**row, "ok": False,
                                  "error": (f"already scheduled as "
                                            f"\u201c{repeated}\u201d"
                                            if check.get("same_as")
                                            else check.get("why")
                                            or "not checked"),
                                  "checked": True})
                    continue
                if check.get("create_anyway"):
                    reason = (reason + " - created although it resembles "
                              "one already scheduled: "
                              + check.get("why", "")).strip(" -")
            try:
                created = self.scheduler.create_task(ScheduledTask(
                    title=title,
                    description=message,
                    scheduled_at=when,
                    execution_type="notification",
                    task_payload={"message": message, "notify_user": True,
                                  "template": template},
                    # WHICH PART OF THE AGENT, not only "the heartbeat":
                    # the calendar says which step of which run put it
                    # there, beside the reason it gave.
                    metadata={"created_by": "heartbeat",
                              "heartbeat_task": task_id,
                              # The lessons it follows: the user's next move
                              # on it says whether they were right.
                              "lessons_used": [str(one) for one in
                                               entry.get("lessons") or []][:10],
                              "written_by": dict(getattr(
                                  self, "_notify_writer", {}) or {}),
                              "created_by_step": "agent heartbeat, step 3 - "
                                                 "what notifications need to "
                                                 "be added",
                              "notification_type": row["type"],
                              # The time as a clock showed it, whose clock,
                              # and what the source wrote: shown on the card.
                              "time": said_time},
                    timezone_name=said_time["zone"],
                    created_by=BY_HEARTBEAT,
                    reason=reason,
                    recurrence=recurrence,
                ))
            except Exception as exc:
                log.exception("could not schedule a notification")
                added.append({**row, "ok": False, "error": str(exc)[:200]})
                continue
            # What this run adds is checked against too: two proposals for
            # one thing in one answer are a repeat as much as across runs.
            waiting.append(created)
            engine = getattr(self, "lessons", None)
            if engine is not None and entry.get("lessons"):
                try:
                    engine.used("notifications", [str(one) for one in entry["lessons"]],
                                created.id, "added")
                except Exception:              # pragma: no cover - it is made
                    log.warning("the lessons it followed were not kept", exc_info=True)
            added.append({**row, "ok": True, "task_id": created.id,
                          "at": when.isoformat(),
                          **({"checked": True} if alike else {})})
        if added:
            self.recorder.heartbeat_action(
                task_id, "scheduled notifications", json.dumps(added)[:800])
        return added

    def _write_nature(self, task_id: str, entries: Any,
                      written_by: Optional[Dict[str, str]] = None,
                      states_written: Optional[List[Dict[str, Any]]] = None
                      ) -> int:
        """Step 2: what the user is like, into user_nature/ - in two kinds.

        WORK nature is their taste and standards in work and how they like
        to work together, what the agent plans their work by; PERSONAL
        nature is habits, rhythm and interests, for a companion. Each
        record is linked to the states of this run it rests on - at least
        one, or it is not written: the link from what the user did to what
        it says about them is how a piece of work finds it.
        """
        if not isinstance(entries, list):
            return 0
        from ...data_manage.library import USER_NATURE, Record

        written = 0
        for entry in entries[:MAX_STATES_PER_RUN]:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title") or "").strip()
            if not title:
                continue
            content = str(entry.get("content") or "")
            kind = str(entry.get("kind") or "").strip().lower()
            # PERSONAL ONLY: work nature is learnt from the stages of the
            # work. One said to be both keeps its personal half; one said to
            # be work is left to the stages.
            kind = ("personal" if kind.startswith(("personal", "both"))
                    or ("personal" in kind) else "")
            states = self._nature_states(entry.get("states"),
                                         states_written or [])
            if not kind or not states:
                self.recorder.heartbeat_action(
                    task_id, "did not record a nature without "
                    + ("its kind" if not kind else "a state it rests on"),
                    title[:200])
                continue
            # The same three things a state carries, for the same reason:
            # what it rests on, how it got there, and how sure the agent
            # is. A trait is a claim about a person that they never made,
            # and one they cannot see the basis of is one they cannot
            # argue with.
            evidence = [str(item)[:300] for item in (entry.get("evidence") or [])
                        if str(item).strip()][:8]
            reasoning = str(entry.get("reasoning") or "").strip()[:600]
            confidence = _confidence(entry.get("confidence"))
            record = self.library.write(Record(
                folder=USER_NATURE, title=title[:200],
                summary=content[:500],
                body=(f"Kind: {'work and personal' if kind == 'both' else kind}"
                      " nature.\n\n"
                      + _nature_body(content, confidence, evidence, reasoning)),
                references=[f"heartbeat run: {task_id}", "step 2: who the user is"],
                source="heartbeat",
                metadata={"nature_kind": kind,
                          **({"written_by": _writer_said(written_by)}
                             if _writer_said(written_by) else {})}))
            self._link_nature(task_id, getattr(record, "id", ""),
                              entry.get("states"), states_written or [])
            db = getattr(self.states, "db", None)
            if db is not None and getattr(record, "id", ""):
                from ..experience.store import ExperienceStore
                ExperienceStore(db).set_nature_kind(
                    record.id, kind, _writer_said(written_by) or {})
            written += 1
            self.recorder.heartbeat_action(task_id, "recorded what kind of person "
                                           "the user is", title)
        return written

    @staticmethod
    def _nature_states(numbers: Any, states_written: List[Dict[str, Any]]
                       ) -> List[str]:
        """The ids of the written states a nature record names by number."""
        if not isinstance(numbers, list):
            return []
        by_number = {int(card["asked_as"]): str(card.get("id") or "")
                     for card in states_written
                     if card.get("asked_as") and card.get("id")}
        out = []
        for value in numbers[:12]:
            try:
                number = int(str(value).strip().lstrip("#").split()[-1])
            except (TypeError, ValueError, IndexError):
                continue
            if by_number.get(number):
                out.append(by_number[number])
        return out

    def _link_nature(self, task_id: str, record_id: str, numbers: Any,
                     states_written: List[Dict[str, Any]]) -> int:
        """Save which written states a nature record rests on."""
        if not record_id or not isinstance(numbers, list):
            return 0
        by_number = {int(card["asked_as"]): str(card.get("id") or "")
                     for card in states_written
                     if card.get("asked_as") and card.get("id")}
        db = getattr(self.states, "db", None)
        if db is None:
            return 0
        made = 0
        for value in numbers[:12]:
            try:
                number = int(str(value).strip().lstrip("#").split()[-1])
            except (TypeError, ValueError, IndexError):
                continue
            state_id = by_number.get(number)
            if not state_id:
                continue
            try:
                db.execute("INSERT OR IGNORE INTO nature_links "
                           "(state_id, record_id, run_id, created_at) "
                           "VALUES (?, ?, ?, ?)",
                           (state_id, record_id, task_id, utcnow().isoformat()))
                made += 1
            except Exception:            # pragma: no cover - a link, not a step
                log.debug("could not link a nature record", exc_info=True)
        return made

    #: Which of the report's fields each step is answerable for.
    #:
    #: By the checklist item's id rather than by its position: the steps are
    #: fixed, but a reader who has to count rows to find out which outcome
    #: belongs to which question is reading a table, not a report.
    OUTCOME_OF = {
        "step_state": ("what is going on",
                       ("state_analysis", "states_written")),
        "step_people": ("who is around the user, and what they are doing",
                        ("people_written",)),
        "step_nature": ("who this person is",
                        ("nature_recorded",)),
        "step_notify": ("what to notify the user of, and what is done",
                        ("notifications_scheduled", "notifications_closed")),
    }

    #: Everything a step is able to do, whether or not it did any of it.
    #:
    #: A panel that shows only what happened cannot be read for what did
    #: not: "nothing" covers a step that decided against, a step that had
    #: nothing to decide about, and a step that was never able to do the
    #: thing the reader had in mind. The third is the common one - somebody
    #: looking at step 1 for a scheduled task is looking at the wrong step,
    #: and an empty outcome does not tell them so.
    #:
    #: Each entry: what it is called, where its result lands in the report,
    #: and how to count one. The last says what the step cannot do at all,
    #: because that is the question an empty list actually raises.
    CAN_DO = {
        "step_state": {
            "does": (("write a state on the calendar", "states_written", None),),
            "cannot": "Step one only writes states. Writing to memory is "
                      "step two, and notifications are step three.",
            "nothing": "No state was written. Either nothing new was going "
                       "on, or what is going on was already on the calendar.",
        },
        "step_people": {
            "does": (("add or update a contact, with what they are doing",
                      "people_written", None),),
            "cannot": "This step only writes to the user's contacts. It does "
                      "not write states or notifications, and it signs in to "
                      "nothing: only what the user had in front of them.",
            "nothing": "No one appeared in this window - or no one the user's "
                       "own changes to their contacts allow adding.",
        },
        "step_nature": {
            "does": (("write what kind of person you are, to memory",
                      "nature_recorded", None),),
            "cannot": "This step only writes to user_nature. It does not "
                      "schedule, notify, or touch the calendar.",
            "nothing": "Nothing was written to memory. What happened in this "
                       "window was an afternoon, not evidence of a habit - "
                       "and this folder is for what would still be true "
                       "next month.",
        },
        "step_notify": {
            "does": (("schedule a notification",
                      "notifications_scheduled", None),
                     ("disable a notification that is already done",
                      "notifications_closed", None)),
            "cannot": "This step only adds notifications and disables ones "
                      "already done. It does not write states or touch "
                      "memory, and it never deletes one.",
            "nothing": "No notification was added or disabled. Interrupting "
                       "has a cost, most windows give no reason to, and "
                       "most waiting ones are not yet done.",
        },
    }


    #: The most events the floor will carry however busy the window is.
    STATE_FLOOR_EVENTS = 300

    #: How many times step 1 may come back for more before it has to answer.
    #:
    #: A loop that can always ask once more never answers. Six is enough for
    #: a widening and a few searches; the cap is reported rather than hidden,
    #: because a step that ran out of rounds answered on less than it wanted.
    STATE_MAX_ROUNDS = 6

    def _state_floor(self, task_id: str) -> "StateFloor":
        """What step 1 is given before it asks for anything.

        Every event of the last few minutes, and nothing else. Not a
        search: the question is what is happening, and a search needs words
        that only the answer to that question would supply - which is the
        loop this returns the first round of.
        """
        started = time.perf_counter()
        since, until = self._window_bounds()
        late = self._window_fixed is not None
        minutes = max(1.0, round((until - since).total_seconds() / 60.0, 1))
        events = self._activity_since(minutes, until=until if late else None)
        if late:
            # Still in front when the agent was closed: until then, not "now".
            for event in events:
                if event.get("in_front_since"):
                    event["in_front_until"] = until.isoformat()
        seen = sum(int(e.get("count") or 1) for e in events)
        # No states. The question is what happened in this window, and the
        # answer is in the window - the calendar is the agent's own earlier
        # opinions, and a reading of the evidence that starts from last
        # hour's conclusions is a reading that mostly confirms them.
        #
        # The step after this one is given states, but not these: it gets
        # what this step has just concluded, which it sets on its own floor.
        floor = StateFloor(minutes=minutes, events=events, states=[])
        floor.every_minutes = 0.0 if late else self._window_minutes()
        if late:
            floor.late = (
                f"A LATE LOOK. This stretch ({_clock(since)} to {_clock(until)}) "
                f"was never read - from before the agent was last closed, or "
                f"from while the reviews could not run - and is read only now. "
                f"Say what it showed, as it was then. Nothing in it is "
                f"happening now.")
        # What was asked for, kept beside what came back. Every state
        # written from this floor carries it (section 34.1, as amended).
        floor.since = self._since
        floor.until = until.isoformat()
        # What the same feed looked like before folding, rendered the same
        # way. A compression that reports its own output is one nobody can
        # check; this is the other half of the comparison.
        raw = StateFloor(minutes=minutes, events=_unfolded(self._last_raw),
                         states=[])
        floor.duration_ms = int((time.perf_counter() - started) * 1000)
        # Timed like any other gathering. It is not a search, but it is what
        # step 1 was given - and the call it feeds is charged against this
        # row, so without it a step's tokens are attributed to nothing and
        # the heartbeat reports one gathering fewer than it made.
        floor.trail.append({
            "did": "floor",
            # Fixed before the question was asked, which is the useful
            # distinction: everything after this row is the step's own
            # doing, and a reader cannot tell the two apart from results.
            #
            # The setting is named because that is what makes the line
            # actionable. "The agent chose" is not something anybody can
            # act on; "heartbeat_interval_seconds" is a thing to go and
            # change if the window is wrong.
            "decided_by": "fixed",
            "decision": f"everything since this review last ran - "
                        f"{minutes:g} minutes, the heartbeat interval",
            "setting": "heartbeat_interval_seconds",
            "minutes": minutes,
            # Both numbers. What happened, and what the step was shown -
            # folding that says only the second is a compression nobody can
            # check, and the ratio is the thing worth looking at.
            "events_seen": seen,
            "events_count": len(events),
            "events_folded": max(0, seen - len(events)),
            "states": 0,
            # Filled in by `restate_floor` for the step that is given
            # states. This one is not: it reads the window.
            "states_started": [],
            "states_not_shown": 0,
            "why": "what is happening now, before anything is asked for",
            # What this row cost. Nothing on a trail said how long it took
            # or what it spent, so a reader looking for where a minute went
            # had a list of things that happened and no way to weigh them.
            "ms": getattr(self, "_read_ms", 0),
            "tokens": 0,
            **_events_for_trail(events),
        })
        before, after = raw.events_text(), floor.events_text()
        floor.trail.append({
            "did": "fold",
            # Not a decision at all: a transform, run in Python, the same
            # way every time, with nothing to configure. That is a third
            # kind of thing from "the model chose" and "somebody set this
            # in advance", and from a result all three look alike.
            "decided_by": "code",
            "decision": "merged events with the same application, kind and "
                        "file or page into one entry with a count",
            "before_events": len(self._last_raw),
            "after_events": len(events),
            "before_tokens": estimate_tokens(before),
            "after_tokens": estimate_tokens(after),
            "ms": getattr(self, "_fold_ms", 0),
            # Folding is code, so it spends none. Printed anyway: a blank
            # reads as unmeasured, and "none" is the point of the row.
            "tokens": 0,
            "before_text": before,
            "after_text": after,
        })
        return floor

    #: How much of one event's text the step is shown.
    #:
    #: `content` was dropped from every event on its way here. That is the
    #: reading of the window - what the page actually said - and without it
    #: the step was analysing a list of application names: `firefox.exe`
    #: rather than the question being read on it. A name says which program
    #: was in front; nobody works on a program.
    CONTENT_PER_EVENT = 700

    #: And how much of it in total, spent on the most recent first.
    #:
    #: One reading can run to four thousand characters and a busy window is
    #: hundreds of events, so this is the difference between a step that
    #: sees what was on the screen and one that is handed a transcript of
    #: the afternoon in place of a question.
    CONTENT_BUDGET = 40000

    #: The most of one reading of a window shown when there is room for it:
    #: the screen reader keeps four thousand characters of a sitting, so
    #: this is all of it.
    READING_CEILING = 4000

    # -- the stretch a review reads -------------------------------------------
    #
    # FROM WHERE THE LAST ONE GOT TO. "The last interval" was the window, and
    # it is the same thing only while the agent keeps running: closed eight
    # minutes into an interval, those eight minutes were older than the next
    # review's fifteen and were never read - recorded by the listener and
    # summarised by nobody. So the end of what each review read is kept,
    # and the next one starts there.

    def _summarised_until(self) -> Optional[datetime]:
        return _as_utc(self.config.get(SUMMARISED_UNTIL, ""))

    def _window_bounds(self) -> tuple:
        """(since, until) for the review about to run.

        A late look reads the stretch it was given. An ordinary review reads
        from where the summaries got to - the time before the agent was last
        closed included, when it was closed only briefly - up to now, and
        never back across a long closure, which is the late look's to read
        on its own. More recorded since than one review carries, it reads
        the next turn of what is behind instead (`run_once`,
        `_late_stretch`)."""
        if self._window_fixed is not None:
            return self._window_fixed
        now = utcnow()
        mark = self._summarised_until()
        if mark is None:
            return now - timedelta(minutes=self._window_minutes()), now
        since = mark
        began = self._began_at
        if began is not None and began - mark >= timedelta(minutes=LOOKED_AT_ALONE_MINUTES):
            since = max(since, began)
        return min(since, now - timedelta(minutes=1)), now

    # -- WHEN THE NEXT REVIEW IS DUE ------------------------------------------
    #
    # As the state heartbeat's rounds are: from what happened last, not from
    # when this process happened to start. A review left unfinished (the
    # agent closed in the middle of it) is done again at once; a finished
    # one is followed by the next an interval after where it got to - so an
    # agent restarted five minutes after a review reviews ten minutes later,
    # over the fifteen since the last one, rather than fifteen minutes later
    # over the last fifteen alone.

    def _due_at(self) -> float:
        """When the next review is due, as a time.time()."""
        began = float(getattr(self, "_loop_began", 0.0) or time.time())
        # Not in the middle of the application starting.
        soonest = began + CATCH_UP_AFTER_SECONDS
        if str(self.config.get(RUN_STARTED_AT, "") or ""):
            return soonest
        marks = [one for one in (self._summarised_until(),
                                 _as_utc(self.config.get(RUN_TRIED_AT, "")))
                 if one is not None]
        if not marks:
            return began + self._interval()
        # Catching up, and the last turn got through: the next turn at once.
        # A turn that did not (the model away) waits an interval, as any.
        tried = _as_utc(self.config.get(RUN_TRIED_AT, ""))
        if self._caught_some and tried is not None and self._late_stretch() is not None:
            return max(soonest, tried.timestamp() + CATCH_UP_TURN_SECONDS)
        return max(soonest, max(marks).timestamp() + self._interval())

    def _run_marked(self) -> Dict[str, Any]:
        """A review, marked as under way while it runs and as tried when it
        ends - the two facts the next start reads (`_due_at`)."""
        self.config.set(RUN_STARTED_AT, utcnow().isoformat(), internal=True)
        before = self._summarised_until()
        try:
            return self.run_once(trigger="timer")
        finally:
            self._caught_some = self._summarised_until() != before
            self.config.set(RUN_STARTED_AT, "", internal=True)
            self.config.set(RUN_TRIED_AT, utcnow().isoformat(), internal=True)

    def _late_stretch(self) -> Optional[tuple]:
        """(since, until) of the next turn of what was never read, read on
        its own as a late look - or None, when an ordinary review reads
        everything since the summaries got to.

        On its own: what came before the agent was last started, when it
        was closed a long while (what came after is another stretch); and
        what one review cannot carry - more than STATE_FLOOR_EVENTS
        recorded since - in turns, oldest first, each ended where it would
        hold more, so none of it is dropped either. Never by time alone."""
        mark, began = self._summarised_until(), self._began_at
        if mark is None:
            return None
        if began is not None and began - mark >= timedelta(minutes=LOOKED_AT_ALONE_MINUTES):
            return mark, self._turn_end(mark, began)
        now = utcnow()
        end = self._turn_end(mark, now)
        return (mark, end) if end < now else None

    def _turn_end(self, since: datetime, end: datetime) -> datetime:
        """`end`, or sooner: where the turn from `since` would hold more
        events than one review carries - the next turn starts there."""
        db = getattr(self.raw_store, "db", None)
        if db is None:
            return end
        try:
            row = db.query_one(
                "SELECT timestamp FROM raw_events WHERE timestamp >= ? AND timestamp < ? "
                "ORDER BY timestamp LIMIT 1 OFFSET ?",
                (since.isoformat(), end.isoformat(), self.STATE_FLOOR_EVENTS))
        except Exception:        # pragma: no cover - a read, not the work
            return end
        found = _as_utc(row["timestamp"]) if row else None
        if found is None or found <= since:
            return end
        return min(end, found)

    def _anything_in(self, since: datetime, until: datetime) -> bool:
        try:
            return bool(self.raw_store is not None
                        and self.raw_store.recent_events(limit=1, since=since,
                                                         until=until))
        except Exception:        # pragma: no cover - a read, not the work
            return True

    def _catch_up(self) -> None:
        """THE LATE LOOK: what happened before the agent was last closed, and
        was never summarised, read on its own - when it was closed long
        enough that running it together with what comes after would join
        two stretches across a gap. Step one only: the states. Nothing it
        saw is happening now, so nothing in it is to be notified about."""
        if self._late_stretch() is None or not bool(self.config.get("heartbeat_enabled", True)):
            return
        try:
            # Its first turn; the loop takes the rest, one after another.
            self._run_marked()
        except Exception:        # pragma: no cover - logged, and tried again
            log.warning("the late look failed", exc_info=True)

    def _window_minutes(self) -> float:
        """How far back step 1 looks: the gap between runs.

        Not a number of its own. The heartbeat wakes every so often and asks
        what is going on, and the honest window for that question is *since
        it last asked*. A fixed five minutes on a fifteen-minute interval
        throws away two thirds of what happened, every time, and nothing in
        the answer says that it did.

        A run started by hand is a one-off and still uses the interval. It
        stands in for the run that would have happened, and a different
        window would make it a different thing.
        """
        seconds = float(self.config.get("heartbeat_interval_seconds", 900) or 0)
        return max(1.0, seconds / 60.0)

    def _direct_chat(self, since: datetime,
                     until: Optional[datetime]) -> List[Dict[str, Any]]:
        """THE USER'S DIRECT CHAT WITH THE AGENT in a window, as events: each
        message - the user's and the agent's reply - said as which it is. Not
        what the Chat debugger sent: those are tests, not the user."""
        db = getattr(self.raw_store, "db", None)
        if db is None:
            return []
        try:
            rows = db.query(
                "SELECT m.id, m.created_at, m.role, m.content, m.metadata, c.title "
                "FROM messages m LEFT JOIN conversations c ON c.id = m.conversation_id "
                "WHERE m.created_at >= ? AND m.created_at <= ? "
                "AND m.role IN ('user', 'assistant') ORDER BY m.created_at, m.rowid",
                (since.isoformat(), (until or utcnow()).isoformat()))
        except Exception:          # pragma: no cover - no chat kept here
            log.debug("could not read the direct chat", exc_info=True)
            return []
        out = []
        for row in rows:
            try:
                meta = json.loads(row["metadata"] or "{}") or {}
            except ValueError:
                meta = {}
            if meta.get("origin") == "chat_debugger":
                continue
            who = ("THE USER, to you (the agent)" if row["role"] == "user"
                   else "YOU (the agent), replying to the user")
            out.append({"id": row["id"], "timestamp": row["created_at"],
                        "event_type": "chat.message", "application": CHAT_APPLICATION,
                        "data": {"content": f"{who}: {row['content']}",
                                 "title": row["title"] or "", "role": row["role"],
                                 "direct_chat": True}})
        return out

    def _activity_since(self, minutes: float,
                        until: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Every event in a window, rather than the most recent handful.

        A count is what the step is reading: four events in five minutes and
        four hundred are different situations, and a fixed `limit` reports
        the same number for both.

        The text of each is kept, newest first until the budget runs out. It
        is the only thing here that says what the user is actually doing -
        the rest is which program it was done in.
        """
        # Before the guard: a run with no event store still looked at a
        # stretch of time and found it empty, and reporting the previous
        # run's bounds for it would be worse than reporting none.
        since = (until or utcnow()) - timedelta(minutes=minutes)
        self._since = since.isoformat()
        if self.raw_store is None or minutes <= 0:
            return []
        read_began = time.perf_counter()
        rows = self.raw_store.recent_events(limit=self.STATE_FLOOR_EVENTS,
                                            since=since, until=until)
        self._read_ms = int((time.perf_counter() - read_began) * 1000)
        fold_began = time.perf_counter()
        # Folded before anything is spent on it. The budget below then buys
        # distinct things rather than sixty readings of one page, which is
        # the difference between a window the step can see the shape of and
        # one it reads the first tenth of.
        raw = [{"id": e.id,
                "timestamp": e.timestamp.isoformat(),
                "event_type": e.event_type,
                "application": e.application,
                "data": dict(e.data or {})}
               for e in rows]
        # THE USER'S DIRECT CHAT WITH THE AGENT in the same window: what
        # they said to it and what it replied, as events of their own.
        raw += self._direct_chat(since, until)
        # WHAT THE SCREEN SAID, UNDER THE NAME THIS READS. Everything here
        # takes an event's text from `content`, which is where a file's
        # is kept; the screen reader keeps a window's under `text`. So no
        # reading of any window ever reached the step - it was shown the
        # title and nothing of what was under it, and a terminal named
        # after the morning's problem was reported, all evening, as that
        # problem.
        for event in raw:
            data = event["data"]
            if (event["event_type"] == "screen.text" and data.get("text")
                    and not data.get("content")):
                data["content"] = data.pop("text")
        self._add_keywords(raw)
        folded = fold_events(raw)
        lend_readings(folded)
        # SHARED OUT, NOT SPENT NEWEST FIRST. Spent from the newest, a
        # busy window ran out partway and everything before that point was
        # left out whole - the step saw the end of the window and was told
        # nothing about the rest. Now every text gets an equal share, and a
        # text shorter than its share leaves the rest to the others: when it
        # all fits, all of it is shown; when it does not, every reading is
        # still there, each shortened alike.
        wanted = []
        for event in folded:
            screen = event.get("event_type") in ("screen.text", "window")
            ceiling = self.READING_CEILING if screen else self.CONTENT_PER_EVENT
            texts = [text.strip() for text in event.get("texts", [])]
            for text in texts:
                if text:
                    wanted.append(min(len(text), ceiling))
        share = _equal_shares(wanted, self.CONTENT_BUDGET)
        cut = iter(share)
        for event in folded:
            data = dict(event.get("data") or {})
            data.pop("content", None)
            screen = event.get("event_type") in ("screen.text", "window")
            texts = event.pop("texts", [])
            at = event.pop("text_at", [])
            words = event.pop("text_keywords", [])
            kept = []
            for index, text in enumerate(texts):
                text = text.strip()
                if not text:
                    continue
                room = next(cut, 0)
                if room <= 0:
                    event["texts_left_out"] = event.get("texts_left_out", 0) + 1
                    continue
                kept.append({"at": _clock(at[index] if index < len(at) else ""),
                             "text": text[:room] + ("…" if len(text) > room else ""),
                             "keywords": (words[index] if index < len(words)
                                          else [])})
            if screen and kept:
                # Oldest first, so the window reads in the order it happened.
                data["reads"] = list(reversed(kept))
            elif kept:
                data["content"] = kept[0]["text"]
                if len(kept) > 1:
                    data["also_read"] = [one["text"] for one in kept[1:]]
            elif event.get("borrowed"):
                # A POINTER, NOT A COPY. What is lent is a reading of the same
                # window from this same window of time - which is now shown
                # in full under that window's own entry. A copy beside every
                # switch to it spent a quarter of the budget on duplicates
                # and cut the readings themselves to make room.
                data["borrowed_at"] = _clock(event["borrowed"].get("at", ""))
            event.pop("borrowed", None)
            event["data"] = data
        self._last_raw = raw
        self._fold_ms = int((time.perf_counter() - fold_began) * 1000)
        return folded

    # -- step 1, which gathers for itself ----------------------------------
    #: What each id's beginning says it is - and so where it is read from.
    ID_KINDS = {"msg": "a message of the user's direct chat with the agent",
                "event": "something the user did, as recorded",
                "edit": "a change the agent made",
                "state": "a state on the calendar",
                "task": "a scheduled task or notification",
                "notif": "a notification that was shown"}

    def _open_by_ids(self, ids: Sequence[str]) -> Dict[str, Any]:
        """Each id, read whole from where its beginning says it lives.

        Exact, so nothing is searched: an id is the thing or it is nothing.
        """
        out: Dict[str, Any] = {}
        by_kind: Dict[str, List[str]] = {}
        for one in ids:
            kind = one.split("_", 1)[0] if "_" in one else ""
            if kind not in self.ID_KINDS:
                out[one] = {"error": "not an id this can read: ids begin "
                            + ", ".join(f"{k}_" for k in self.ID_KINDS)}
                continue
            by_kind.setdefault(kind, []).append(one)

        def put(one: str, kind: str, value: Any) -> None:
            out[one] = {"is": self.ID_KINDS[kind], **(
                value if isinstance(value, dict) else {"value": value})}

        try:
            if by_kind.get("msg") and self.raw_store is not None:
                holes = ", ".join("?" for _ in by_kind["msg"])
                for row in self.raw_store.db.query(
                        "SELECT m.id, m.created_at, m.role, m.content, c.title FROM messages m "
                        "LEFT JOIN conversations c ON c.id = m.conversation_id "
                        f"WHERE m.id IN ({holes})", tuple(by_kind["msg"])):
                    put(str(row["id"]), "msg", {
                        "at": row["created_at"], "conversation": row["title"] or "",
                        "who": "the user" if row["role"] == "user" else "you, the agent",
                        "content": row["content"]})
            if by_kind.get("event") and self.raw_store is not None:
                for event in self.raw_store.events_by_ids(by_kind["event"]):
                    put(event.id, "event", event.to_dict())
            if by_kind.get("edit") and self.raw_store is not None:
                for edit in self.raw_store.edits_by_ids(by_kind["edit"]):
                    put(edit.id, "edit", edit.to_dict())
            if by_kind.get("state") and self.states is not None:
                for state in self.states.by_ids(by_kind["state"]):
                    put(state.id, "state", state.to_dict())
            store = getattr(self.scheduler, "store", None)
            for one in by_kind.get("task", []):
                task = store.get(one) if store is not None else None
                if task is not None:
                    put(one, "task", task.to_dict())
            if by_kind.get("notif") and self.raw_store is not None:
                holes = ", ".join("?" for _ in by_kind["notif"])
                for row in self.raw_store.db.query(
                        f"SELECT * FROM notifications WHERE id IN ({holes})",
                        tuple(by_kind["notif"])):
                    put(str(row["id"]), "notif", dict(row))
        except Exception as exc:         # pragma: no cover - a read
            log.debug("could not read by id", exc_info=True)
            for one in ids:
                out.setdefault(one, {"error": f"could not be read: {exc}"})
        for one in ids:
            out.setdefault(one, {"error": "nothing has this id"})
        return out

    def _run_state_tool(self, floor: "StateFloor", name: str,
                        arguments: Dict[str, Any], task_id: str,
                        said: str = "") -> Any:
        """One thing step 1 asked for, done, and written into its trail.

        ``said`` is whatever the model wrote in the same turn as the call.
        That is the only account of *why* it asked - the arguments say what
        it wanted and not what it was thinking - and without it the trail
        reports a decision with its reasoning removed.
        """
        arguments = arguments or {}
        began = time.perf_counter()
        asked = {"decided_by": "model", "said": said[:1500],
                 "arguments": dict(arguments),
                 # A tool call spends time and no tokens: it is the index
                 # and the filesystem, not a model.
                 "tokens": 0}
        if name == "open_by_id":
            wanted = arguments.get("ids")
            if not isinstance(wanted, list):
                wanted = [arguments.get("id", "")]
            ids = [str(one).strip() for one in wanted if str(one).strip()][:20]
            if not ids:
                return {"error": "open_by_id needs `ids`: the ids, exactly "
                                 "as they are printed."}
            found = self._open_by_ids(ids)
            asked["ms"] = int((time.perf_counter() - began) * 1000)
            floor.trail.append({"did": "open", **asked,
                                "decision": f"read {', '.join(ids)} by id",
                                "id": ", ".join(ids),
                                "found": sum(1 for one in found.values()
                                             if "error" not in one),
                                "why": "read by id"})
            return {"found": found}

        return {"error": f"unknown tool {name}"}

    def _ask_step_one(self, item: Any, shape: Dict[str, Any],
                      floor: "StateFloor", step_row: Dict[str, Any],
                      task_id: str,
                      concluded: Optional[List[str]] = None) -> Any:
        """The call for a step that gathers for itself: a loop, not a call.

        Every step uses it - what is happening, who the user is, and what to
        notify them of - each starting from a floor of its own and deciding
        what else it wants.

        None of them can be asked once: what to search the library for is
        the answer to the question it is being asked, so it is given what is
        happening now and left to decide whether that is enough - and what
        else it wants, from where, and how much of it.

        Every round is written into the trail as it happens, so the panel
        shows the step working rather than a minute of nothing.
        """
        rounds = max(1, int(self.config.get("heartbeat_state_max_rounds",
                                            self.STATE_MAX_ROUNDS) or 1))
        messages: List[Dict[str, Any]] = [
            {"role": "user",
             # What the earlier steps worked out travels here too. It was
             # passed as empty, so the one step that follows another and
             # loops was the only step in the run told nothing about what
             # had already been decided.
             "content": self._step_prompt(item, shape, floor, [],
                                          concluded or [])}
        ]
        step_row["prompt"] = messages[0]["content"]
        step_row["prompt_tokens"] = estimate_tokens(messages[0]["content"])
        system_prompt = PREAMBLE
        #: How much of the conversation earlier rounds have already shown.
        sent_through = 0
        response = None
        for round_number in range(rounds):
            step_row["phase"] = "asking"
            step_row["round"] = round_number + 1
            self.recorder.heartbeat_action(
                task_id, "asking the model",
                f"the user's state, round {round_number + 1}")
            # A row for the call itself, so the order of the trail is the
            # order things happened. Without it the trail runs start, fold,
            # search - and a reader cannot see that the search came *after*
            # a model call, which is the only thing that explains why those
            # words and not others.
            floor.trail.append({
                "did": "asked",
                "round": round_number + 1,
                "decided_by": "fixed",
                "decision": f"asked the model with everything gathered so "
                            f"far: {len(floor.events)} lines of activity, "
                            f"{len(floor.states)} states, "
                            f"{len(floor.nodes)} records",
                "context_tokens": estimate_tokens(floor.context_text()),
                # The message itself, not a description of it. A row that
                # counts what went in - "20 lines of activity, 146 states" -
                # says how much and never what, and what was sent is the
                # only thing that explains the answer that came back.
                #
                # New in this round, because the call carries every round
                # before it as well and printing the whole conversation
                # four times is the same text four times.
                "sent": _messages_text(messages[sent_through:]),
                "sent_tokens": estimate_tokens(
                    _messages_text(messages[sent_through:])),
                "carried_over": sent_through,
                # Filled in once the call returns: a row is written before
                # the call it stands for, and what it cost is not known
                # until afterwards.
                "ms": 0,
                "tokens": 0,
                "system": system_prompt,
                # What it was offered, which is half of how it decides
                # whether to search - and which travels in the request
                # beside the messages rather than inside them. A reader
                # rebuilding the call from the message alone sees it being
                # told it may ask for things, and never what.
                "tools": [{"name": spec.name,
                           "description": spec.description,
                           "takes": spec.input_schema}
                          for spec in STATE_TOOLS],
            })
            sent_through = len(messages)
            asking = floor.trail[-1]
            step_row["trail"] = list(floor.trail)
            call_began = time.perf_counter()
            response = self.models.invoke(
                ModelRequest(
                    messages=messages,
                    system=system_prompt,
                    tools=STATE_TOOLS,
                    # Which language: the step's own (STEP_PROMPTS).
                    language=shape.get("language") or in_display(),
                    max_tokens=int(self.config.get("max_output_tokens", 4096)),
                    # EACH STEP ITS OWN TAG: the calls are counted under the
                    # function each step belongs to (Settings > Functions) -
                    # one tag for all four put every one under the core.
                    task=f"heartbeat:{item.id}",
                    # No step number. This is telemetry, but a provider
                    # without a model behind it answers with it - so a
                    # number here reappears inside the next call's prompt,
                    # which is the one place it must not be.
                    metadata={"context_summary":
                              f"the user's state, round {round_number + 1}: "
                              f"{len(floor.events)} events, "
                              f"{len(floor.nodes)} memories"[:2000]},
                ),
                role="heartbeat",
            )
            self._charge(step_row, response)
            usage = getattr(response, "usage", None)
            asking["ms"] = int((time.perf_counter() - call_began) * 1000)
            asking["tokens"] = (int(getattr(usage, "input_tokens", 0) or 0)
                                + int(getattr(usage, "output_tokens", 0) or 0))
            calls = getattr(response, "tool_calls", None)
            if not calls:
                floor.trail.append({
                    "did": "answered",
                    "round": round_number + 1,
                    "decided_by": "model",
                    "decision": "answered rather than asking for more",
                    # The answer arrived in the call above, and is charged
                    # there. This row is the moment it stopped, not a cost.
                    "ms": 0,
                    "tokens": 0,
                    # Whole. This is the answer, and it was cut at four
                    # hundred characters - so the row that is meant to show
                    # what the step concluded showed the first sentence of
                    # it and stopped mid-word.
                    "said": str(getattr(response, "text", "") or ""),
                })
                step_row["trail"] = list(floor.trail)
                # Also the answer when the provider has no tool protocol at
                # all: the step answers from its floor, which is a smaller
                # answer than it could have given and not a failure.
                floor.stopped_because = "the step said it had enough"
                break
            messages.append({"role": "assistant",
                             "content": getattr(response, "raw_content", "")
                             or response.text})
            results = []
            # Whatever it wrote in the same turn as the calls. A model
            # asking for something usually says why first, and that
            # sentence is the only account of the decision there is.
            said = str(getattr(response, "text", "") or "")
            for call in calls:
                outcome = self._run_state_tool(floor, call.name,
                                               call.arguments, task_id,
                                               said=said)
                step_row["trail"] = list(floor.trail)
                # Reading by id is asked for in full, and a screen reading
                # alone can be thousands of characters.
                results.append({"type": "tool_result",
                                "tool_use_id": call.id,
                                "content": json.dumps(outcome,
                                                      ensure_ascii=False,
                                                      default=str)[
                                    :60000 if call.name == "open_by_id"
                                    else 12000]})
            # What it has now, not what it started with: the context it is
            # answering from grew, and it has to be shown that it did.
            #
            # In the same message as the results rather than after it. Roles
            # alternate, and two user messages in a row are refused by the
            # API outright - which no test here would have found, because
            # they all stand in for the model rather than reaching one.
            results.append({"type": "text",
                            "text": "Your context now holds:\n"
                                    + floor.context_text()})
            messages.append({"role": "user", "content": results})
        else:
            # It kept asking. Reported rather than hidden: an answer given
            # because the rounds ran out is not the same as one given
            # because the step was satisfied.
            floor.stopped_because = (f"stopped after {rounds} rounds, still "
                                     f"asking for more")
        step_row["rounds"] = (response is not None) and step_row.get("round", 1)
        step_row["context"] = floor.context_text()
        step_row["context_tokens"] = estimate_tokens(floor.context_text())
        step_row["records"] = [{"id": n.id, "title": n.title,
                                "folder": n.folder,
                                "created_at": n.created_at}
                               for n in floor.nodes]
        step_row["asked_for"] = list(floor.searched_for)
        step_row["searches"] = floor.searches
        step_row["stopped_because"] = floor.stopped_because
        return response

    def _step_prompt(self, item: Any, shape: Dict[str, Any], found: Any,
                     activity: List[Dict[str, Any]],
                     concluded: List[str]) -> str:
        """Everything this one step is shown, and nothing else.

        What a step does not need is left out rather than included cheaply.
        The raw activity feed is two thousand tokens and is evidence for what
        the user is doing and who they are; it is not evidence for what to
        schedule, which follows from the first two, or for whether a state
        has run out, which follows from the state.
        """
        parts = [shape["instruction"],
                 f"\nThe question you are answering:\n{item.text}"]
        if getattr(item, "detail", ""):
            parts.append(item.detail)
        if isinstance(found, StateFloor):
            # Its context is the live feed and the calendar, not a search
            # result, and it carries its own activity - so neither the
            # heading nor the raw block below belongs to it.
            parts.append("\nWHAT YOU HAVE TO START WITH:\n"
                         + found.context_text())
        else:
            parts.append("\nWhat your memory holds about this:\n"
                         + (found.context_text() or "Nothing matched."))
            if shape.get("wants_activity"):
                parts.append("\nRecent raw activity:\n"
                             + json.dumps(activity, ensure_ascii=False)[:8000])
        if concluded:
            # The thing a single call could not do: reading what was worked
            # out earlier in the same review. Given as findings rather than
            # as the output of steps - what was established is usable, and
            # which step established it is not.
            #
            # And as material handed over, not as something remembered.
            # Every step is its own call with its own context; a step told
            # "you already concluded" is being told it remembers a
            # conversation it was not in.
            parts.append("\nWHAT HAS ALREADY BEEN WORKED OUT FROM THIS "
                         "ACTIVITY:\n" + "\n".join(concluded))
        parts.append("\n" + _now_said())
        if isinstance(found, StateFloor):
            # Two ways to end, because this step has two.
            #
            # It said "Return ONLY this JSON object" - a direct instruction
            # not to call a tool, in the last and strongest position,
            # contradicting the paragraph far above that offers the tools.
            # The step read the nearest instruction and answered from the
            # floor, so the tools it was given went unused on the round
            # where using them was the point.
            parts.append(
                "\nNOW DO ONE OF TWO THINGS.\n"
                "\n  - If what you have above is not enough, CALL A TOOL. "
                "Say in a sentence what you are looking for and why, then "
                "call `open_by_id` to "
                "read anything whose id you can see - an event_, edit_, "
                "state_, task_ or notif_ - exactly and in full. You are "
                "asked again with what comes back.\n"
                "\n  - If you have enough, ANSWER. Return ONLY this JSON "
                "object and nothing else:\n" + shape["returns"])
        else:
            parts.append("\nReturn ONLY this JSON object:\n"
                         + shape["returns"])
        return "\n".join(parts)

    @staticmethod
    def _what_it_concluded(step_id: str, answered: Dict[str, Any]) -> str:
        """One line for the steps that come after, not the whole answer.

        A later step given everything an earlier one returned is reading a
        second prompt's worth of JSON to find one fact. What it needs is what
        was decided.
        """
        if step_id == "step_state":
            titles = [str(s.get("title", "")) for s in (answered.get("states") or [])]
            analysis = str(answered.get("state_analysis") or "").strip()
            said = analysis[:400]
            if titles:
                said += (" States written: " + "; ".join(titles[:6]))
            return said.strip()
        if step_id == "step_people":
            return ("; ".join(str(n.get("name", ""))
                              for n in (answered.get("people") or [])
                              if isinstance(n, dict))
                    or "no one in this window")[:400]
        if step_id == "step_nature":
            return "; ".join(str(n.get("title", ""))
                             for n in (answered.get("user_nature") or []))[:400]
        if step_id == "step_notify":
            asked = [n for n in answered.get("notifications") or []
                     if isinstance(n, dict)]
            closed = [c for c in answered.get("closed") or []
                      if isinstance(c, dict)]
            said = "; ".join(f"{n.get('type', '?')} at {n.get('at', '?')}: "
                             f"{n.get('title', '')}" for n in asked[:6])
            gone = "; ".join(f"disabled {c.get('id', '?')}: {c.get('why', '')}"
                             for c in closed[:6])
            filled = "; ".join(f"updated {u.get('id', '?')}: {u.get('why', '')}"
                               for u in answered.get("updated") or []
                               if isinstance(u, dict))
            return ("; ".join(one for one in (said, gone, filled) if one)
                    or "no notification added, disabled or updated")[:400]
        return ""

    @staticmethod
    def _steps_given(gathered: List[Any]) -> List[Dict[str, Any]]:
        """What each step was given, before the model was asked anything.

        The context is the text itself and not a list of titles. What a
        gathering *found* is already in ``retrieval``; what the model was
        actually shown is this, and the difference between them is where an
        answer that reads oddly is usually explained.
        """
        steps = []
        for index, (item, found) in enumerate(gathered):
            steps.append({
                "step": index + 1,
                "id": item.id,
                "title": item.text,
                "detail": item.detail,
                "asked_for": list(found.searched_for),
                # Every round of the gathering, in the order it happened:
                # what each folder was asked for, what came back, how the
                # candidates were ranked, and why each was taken or left.
                "trail": list(getattr(found, "trail", []) or []),
                "context": found.context_text(),
                # In tokens. Characters are a number about the string; the
                # thing being spent, capped and paid for is tokens, and a
                # reader deciding whether a context is too big cannot do
                # the division in their head.
                "context_tokens": estimate_tokens(found.context_text()),
                "records": [{"id": record.id, "title": record.title,
                             "folder": record.folder,
                             "created_at": record.created_at}
                            for record in found.nodes],
                "duration_ms": found.duration_ms,
                "searches": found.searches,
                "stopped_because": found.stopped_because,
                "outcome": {},
                # Whether the outcome above is empty because nothing came
                # of the step, or because the step has not got there yet.
                # They are opposite readings of the same empty dict, and
                # the panel showed the first for both - so step one said
                # "nothing came of this" for the whole minute between its
                # answer and its states being written.
                "outcome_known": False,
            })
        return steps

    def _steps_produced(self, report: Dict[str, Any]) -> None:
        """What each step produced, once the model has answered.

        One model call used to answer all of them (section 14.36), so the outcomes
        arrive together and are sorted back to the questions that asked for
        them here.
        """
        for step in report.get("steps") or []:
            label, fields = self.OUTCOME_OF.get(step.get("id"), ("", ()))
            step["answers"] = label
            step["outcome"] = {name: report.get(name)
                               for name in fields if report.get(name) is not None}
            # Now it is. The writing happens after every step has
            # answered, so until this runs an empty outcome means "not yet"
            # and not "nothing".
            step["outcome_known"] = True
            able = self.CAN_DO.get(step.get("id"))
            if able:
                step["could_do"] = [
                    {"what": what,
                     "count": _how_many(report.get(where), kind)}
                    for what, where, kind in able["does"]]
                step["cannot"] = able["cannot"]
                # What "nothing" means for this step. The panel used to say
                # the same sentence for every step, and it was about the cost
                # of interrupting - which only one of them can do.
                step["nothing"] = able["nothing"]

    def progress(self) -> Dict[str, Any]:
        """The run in progress, as far as it has got.

        Watched rather than waited on: the calls take about a minute
        between them, and a panel that shows nothing until the last of them
        returns is a panel that looks broken for a minute (section 15.1).

        A copy, because the run is still writing to the original on another
        thread - and a shallow copy of the steps too, since those are what
        change.
        """
        report = self._progress
        if report is None:
            # Not running: the last one, whole. A panel that empties itself
            # when the page is reloaded has thrown away the thing the reader
            # opened it to look at, and this is the only copy with the
            # prompts and the gathered text still in it.
            last = dict(self._last_report or {})
            last["running"] = False
            return last
        snapshot = dict(report)
        snapshot["steps"] = [dict(step) for step in report.get("steps") or []]
        snapshot["running"] = True
        return snapshot

    def _finish(self, report: Dict[str, Any]) -> Dict[str, Any]:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        self._progress = None
        self._last_report = report
        if self.observability is not None:
            self.observability.increment("heartbeat.executions")
            self.observability.set_state("heartbeat.last", report)
        if self.bus is not None:
            self.bus.publish("heartbeat.completed", report)
        return report

    @property
    def last_report(self) -> Dict[str, Any]:
        """The last run, without the bulk of what it read.

        The status this appears in is polled every few seconds by the
        interface, and the context given to the steps is tens of kilobytes
        of record text. Whoever asked for the run has it in the answer to
        that request; everyone else wants to know that it happened and what
        came of it.
        """
        report = dict(self._last_report)
        steps = report.get("steps")
        if steps:
            bulk = ("context", "prompt", "answer")
            report["steps"] = [
                {**{key: value for key, value in step.items()
                    if key not in bulk},
                 # The trail carries the events themselves now rather than a
                 # count of them, and that is most of a run's size. Whoever
                 # asked for the run has them; a poll every few seconds does
                 # not, and the rows keep their shape either way.
                 "trail": [{key: value for key, value in row.items()
                            if key not in ("before_text", "after_text",
                                           "sent", "system", "tools")
                            and not (key == "said"
                                     and row.get("did") == "answered")
                            and not (key in ("events", "states_started")
                                     and isinstance(value, list))}
                           for row in step.get("trail") or []]}
                for step in steps]
        return report

    def what_it_is_doing(self) -> Dict[str, Any]:
        """The same shape the other heartbeats answer in."""
        held = self.progress() or {}
        busy = bool(held.get("running"))
        steps = held.get("steps") or []
        return {
            "busy": busy,
            "doing": "looking at the day" if busy else "",
            "stage": (str((steps[-1] or {}).get("name", "")) if steps else "")
                     if busy else "",
            "detail": "",
            "since": held.get("started_at", "") if busy else "",
        }

    def _last_review_at(self) -> Optional[float]:
        """When the last review ended - kept in the settings, so a restart
        still knows it (the state heartbeat's readout does the same) - or
        when one began in this process, whichever is later."""
        kept = _as_utc(self.config.get(RUN_TRIED_AT, ""))
        times = [one for one in (kept.timestamp() if kept else None,
                                 self._last_run_at) if one is not None]
        return max(times) if times else None

    def status(self) -> Dict[str, Any]:
        now = time.time()
        enabled = bool(self.config.get("heartbeat_enabled", True))
        running = bool(self._thread and self._thread.is_alive())
        # WHEN IT LAST RAN AND WHEN IT RUNS NEXT, from what the schedule is
        # worked out from - the review last ended, kept across restarts, and
        # `_due_at` - not from what this process happened to do. Read from
        # memory alone, a restarted agent said "not yet, next unknown" for
        # as long as its first wait lasted, beside a state heartbeat that
        # knew both.
        last = self._last_review_at()
        reviewing = bool(str(self.config.get(RUN_STARTED_AT, "") or "")) and running
        if not enabled or not running:
            upcoming = None
        elif reviewing:
            upcoming = 0.0
        elif self._next_run_at is not None and self._next_run_at > now:
            upcoming = self._next_run_at - now
        else:
            upcoming = max(0.0, self._due_at() - now)
        return {
            "running": running,
            "enabled": enabled,
            "interval_seconds": int(self.config.get("heartbeat_interval_seconds", 900)),
            "last_run_seconds_ago": None if last is None else max(0.0, now - last),
            "next_run_in_seconds": upcoming,
            "checklist": [item.to_dict() for item in self.checklist.items()],
            "last_report": self.last_report,
        }
